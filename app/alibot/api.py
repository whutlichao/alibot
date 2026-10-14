"""HTTP 人类面：本机单用户的频道消息契约 + 事件流订阅。

端点（浏览器只经这里读写，业务正本在后端单文件库）：

- ``GET  /api/workspace``                     工作台快照（空库 = 空工作台）
- ``GET  /api/channels``                      频道列表（含未读计数）
- ``POST /api/channels``                      新建频道（名称 + 描述 + 初始成员，原子校验）
- ``GET  /api/channels/{id}/messages``        按 seq 升序取消息（``after_seq`` 增量）
- ``POST /api/channels/{id}/messages``        发送人类消息（同事务分配频道内单调 seq）
- ``GET  /api/channels/{id}/members``         频道成员
- ``POST /api/channels/{id}/members``         把 Agent 成员加入频道（幂等，只接受 Agent）
- ``GET  /api/channels/{id}/round-outcomes``  轮次结果（只读，无 UI）
- ``GET  /api/channels/{id}/budget``          有界响应预算（只读，无 UI）
- ``GET  /api/channels/{id}/events``          事件流（SSE，``after_seq`` 补拉）
- ``POST /api/channels/{id}/read``            标读（同一频道内按 seq 单调取 max）
- ``GET  /api/agents``                         Agent 成员 + 模型配置 + 四态状态点
- ``POST /api/agents``                         新建 Agent（名称 + 角色 + 模型 + 系统提示）
- ``GET  /api/agents/{id}``                    单个 Agent（含「为什么未启用」的可读原因）
- ``GET  /api/remote-data-flow``               远端数据流告知文案与已确认记录
- ``POST /api/remote-data-flow/ack``           确认一次告知（按模型提供商）

**HTTP 面自始至终属于人类面**：Agent 的任何动作不经 HTTP，由后端调用点（唤醒编排）直接执行。
因此这里没有「以某成员名义调用」的路径，也**没有**创建或编辑系统消息的端点。

消息是 append-only：**没有**编辑或删除消息的端点。轮次结果同样 append-only。
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .agent_seam import AgentScopeAgentSeam
from .db import (
    OWNER_ID,
    Database,
    DomainError,
    open_database,
)
from .events import EventBus, sse_frame
from .orchestration import ThreadScheduler, WakeOrchestrator
from .service import WorkspaceService

PACKAGE_DIR = Path(__file__).resolve().parent

# 事件流的单连接最长寿命。到点后服务端主动收尾，浏览器（EventSource）会带最新 `last_seq`
# 重连——这正是「重连带 last_seq 补拉」那条路径，因此轮换不丢事件。
#
# 为什么需要它：事件流是永不自然结束的响应，如果不主动轮换，进程在优雅关停时会**等这些连接
# 等到超时**（表现为「进程还在、监听套接字已没、新请求全拒」）；不少反代也会默默切掉长连接。
#
# 环境变量可调（反代空闲超时短的部署需要它；本地验收也用它来实跑重连路径）。


def _env_seconds(name: str, default: float) -> float:
    """读一个秒数环境变量；值不合法时退回默认值（配置错误不该让后端起不来）。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


SSE_STREAM_MAX_SECONDS = _env_seconds("ALIBOT_SSE_ROTATE_SECONDS", 55.0)

# 事件流的推送轮询间隔：队列在此间无事件时发一个 keep-alive 注释帧，
# 顺带让事件流能感知关停信号（见 `channel_events` 的循环）。
SSE_POLL_SECONDS = 0.5


def repo_root() -> Path:
    """仓库根（``app/alibot/api.py`` → 上溯三层）。"""
    return PACKAGE_DIR.parent.parent


def studio_dir() -> Path:
    return PACKAGE_DIR / "studio"


def design_system_dir() -> Path | None:
    """只读参照的设计系统目录（视觉契约的正本），不存在时返回 None。"""
    candidate = repo_root() / "alibot-design-system"
    return candidate if candidate.is_dir() else None


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    member_ids: list[str] = Field(default_factory=list)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = ""


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    role: str = ""
    model_id: str = ""
    system_prompt: str = ""


class RemoteConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = ""


class ChannelMemberAdd(BaseModel):
    """把一个 Agent 成员加入频道（人类面动作）。"""

    model_config = ConfigDict(extra="forbid")

    member_id: str = ""


class MarkRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # strict："3" 这类字符串不会被静默当成 3。
    seq: Annotated[int, Field(strict=True)]


def create_app(
    data_dir: Path | str | None = None,
    db: Database | None = None,
    *,
    scheduler: Any | None = None,
    seam: Any | None = None,
) -> FastAPI:
    """组装后端。

    ``scheduler`` / ``seam`` 是测试注入点：传入手动调度器与脚本化替身，就能在不联网、零成本
    的前提下把「回应 / 忽略 / 失败 / 延迟」四种行为固定下来，并让测试自己决定什么时候跑完一轮。
    生产默认是线程池 + 真实 AgentScope 适配器。
    """
    database = db if db is not None else open_database(data_dir)
    events = EventBus()
    # 优雅关停的信号：事件流是**永不自然结束**的响应，如果不主动收，进程会在 graceful shutdown
    # 里等那两个连接直到超时（表现为「进程还在、监听套接字已没、新请求全部被拒」）。
    shutting_down = threading.Event()
    orchestrator = WakeOrchestrator(
        database,
        seam if seam is not None else AgentScopeAgentSeam(),
        events,
        scheduler if scheduler is not None else ThreadScheduler(),
    )
    service = WorkspaceService(database, orchestrator=orchestrator, events=events)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # 顺序很重要：先让事件流退出（它们会在半个 keep-alive 周期内看到这个信号），
            # 再停调度器，最后关库。
            shutting_down.set()
            close_scheduler = getattr(orchestrator._scheduler, "shutdown", None)
            if callable(close_scheduler):
                close_scheduler()
            database.close()

    app = FastAPI(title="Alibot", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db = database
    app.state.service = service
    app.state.events = events
    app.state.orchestrator = orchestrator
    app.state.data_dir = database.data_dir

    def _not_found(message: str = "接口不存在") -> JSONResponse:
        return JSONResponse({"error": {"code": "not_found", "message": message}}, status_code=404)

    # ─── 错误封装：统一 {"error": {"code", "message", "fields?"}} ──────────
    @app.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(exc.payload(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "validation_failed",
                    "message": "请求体校验未通过",
                    "fields": {"body": str(exc.errors()[:1])},
                }
            },
            status_code=400,
        )

    # ─── 工作台 ────────────────────────────────────────────────────────────
    @app.get("/api/workspace")
    def get_workspace() -> dict[str, Any]:
        return service.bootstrap()

    @app.get("/api/health")
    def get_health() -> dict[str, Any]:
        return {"status": "ok", "member_id": OWNER_ID, "data_dir": str(app.state.data_dir)}

    # ─── 频道 ─────────────────────────────────────────────────────────────
    @app.get("/api/channels")
    def list_channels() -> dict[str, Any]:
        return {"channels": service.list_channels()}

    @app.post("/api/channels", status_code=201)
    def create_channel(payload: ChannelCreate) -> dict[str, Any]:
        channel = service.create_channel(
            name=payload.name, description=payload.description, member_ids=payload.member_ids
        )
        return {"channel": channel, "channels": service.list_channels()}

    @app.get("/api/channels/{channel_id}/messages")
    def list_messages(channel_id: str, after_seq: int = Query(default=0, ge=0)) -> dict[str, Any]:
        messages = service.channel_messages(channel_id, after_seq=after_seq)
        channel = database.get_channel(channel_id)
        return {"channel": channel, "messages": messages}

    @app.post("/api/channels/{channel_id}/messages", status_code=201)
    def create_message(channel_id: str, payload: MessageCreate) -> dict[str, Any]:
        message = service.send_message(channel_id, payload.body)
        return {"message": message}

    @app.get("/api/channels/{channel_id}/members")
    def list_channel_members(channel_id: str) -> dict[str, Any]:
        return {"members": service.channel_members(channel_id)}

    @app.post("/api/channels/{channel_id}/members")
    def add_channel_member(channel_id: str, payload: ChannelMemberAdd) -> dict[str, Any]:
        """把一个 Agent 成员加入频道（幂等）。

        状态码与「确认一次远端数据流告知」同形：这是一个**幂等动作**，所以成功一律 200，
        「新增」还是「本来就在」由载荷里的 ``joined`` 机械区分，而不是靠状态码二次编码。

        只接受 Agent：owner 已由建频道自动加入，不经此端点。响应同时带该频道**最新**成员表，
        于是前端一次调用就能刷新面板与 `@` 补全的候选，不必「先加再读」两次往返。
        """
        return service.add_channel_member(channel_id, payload.member_id)

    # ─── 轮次结果（只读；首版没有可观测页面） ─────────────────────────────
    @app.get("/api/channels/{channel_id}/round-outcomes")
    def list_round_outcomes(channel_id: str, base_seq: int = Query(default=0, ge=0)) -> dict[str, Any]:
        """该频道的轮次结果（append-only 事实记录）。

        每行上的 ``consumed_budget`` 是**行级**标记（「这一轮由一次已计一次的 agent→agent 跳送来」），
        **不是**跳的计数：一次跳投递 N 个成员会得到 N 条为真的行，而跳数只 +1。因此
        **不要对它们求和**；该人类世代的跳数正本是 ``GET /api/channels/{id}/budget`` 的 ``used``。
        """
        return {"round_outcomes": service.round_outcomes(channel_id, after_seq=base_seq)}

    # ─── 有界响应预算（只读；首版没有可观测页面） ────────────────────────
    @app.get("/api/channels/{channel_id}/budget")
    def get_channel_budget(channel_id: str) -> dict[str, Any]:
        """该频道当前人类世代的有界响应预算（后端持久事实）。

        它让「任一人类世代内 agent→agent 跳数 ≤ 3」与「重启后预算状态不变」可以经 HTTP 面
        直接断言，而不用从消息与轮次结果反推。

        ``used`` 是「该人类世代已用跳数」的**唯一正本**：要从只读面拿跳数就读它，
        不要对 ``round_outcome`` 的 ``consumed_budget`` 求和（见那个端点的说明）。
        """
        return {"budget": service.channel_budget(channel_id)}

    # ─── Agent（人类面的创建入口；Agent 本身不经 HTTP） ───────────────────
    @app.get("/api/agents")
    def list_agents() -> dict[str, Any]:
        return {"agents": service.agents()}

    @app.post("/api/agents", status_code=201)
    def create_agent(payload: AgentCreate) -> dict[str, Any]:
        agent = service.create_agent(
            name=payload.name,
            role=payload.role,
            model_id=payload.model_id,
            system_prompt=payload.system_prompt,
        )
        return {"agent": agent, "agents": service.agents(), "members": database.list_members()}

    @app.get("/api/agents/{member_id}")
    def get_agent(member_id: str) -> dict[str, Any]:
        return {"agent": service.agent(member_id)}

    @app.get("/api/agents/{member_id}/status")
    def get_agent_status(member_id: str) -> dict[str, Any]:
        return service.member_status(member_id)

    @app.get("/api/member-statuses")
    def get_member_statuses() -> dict[str, Any]:
        return service.member_statuses()

    # ─── 远端数据流告知（首次启用远端模型之前） ───────────────────────────
    @app.get("/api/remote-data-flow")
    def get_remote_data_flow() -> dict[str, Any]:
        return service.remote_data_flow()

    @app.post("/api/remote-data-flow/ack")
    def acknowledge_remote_data_flow(payload: RemoteConsent) -> dict[str, Any]:
        return service.acknowledge_remote_flow(payload.provider)

    # ─── 已读位置 ─────────────────────────────────────────────────────────
    @app.post("/api/channels/{channel_id}/read")
    def mark_channel_read(channel_id: str, payload: MarkRead) -> dict[str, Any]:
        channel = service.mark_read(channel_id, payload.seq)
        return {"channel": channel, "channels": service.list_channels()}

    # ─── 事件流（SSE） ────────────────────────────────────────────────────
    @app.get("/api/channels/{channel_id}/events")
    async def channel_events(
        channel_id: str,
        request: Request,
        after_seq: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        """订阅该频道的事件流。

        连接建立时先用**库里的正本**补齐 ``after_seq`` 之后的内容：消息（含 Agent 的完整回复）
        与轮次结果，再补一份当前的状态点快照。此后推送实时事件。于是「重连带 ``last_seq``
        补拉」既不会漏、也不会重复——补的是 ``seq > after_seq`` 的事件，实时事件的下界也是它。
        """
        database.get_channel(channel_id)  # 404 早退

        async def stream() -> AsyncIterator[str]:
            # 1) 补拉：消息是正本，直接读库（Agent 回复与人类消息走同一条路径）。
            snapshot = service.channel_messages(channel_id, after_seq=after_seq)
            for message in snapshot:
                yield sse_frame({"type": "message", "seq": message["seq"], "message": message})
            # 2) 补拉：轮次结果（也只读库）。
            for outcome in service.round_outcomes(channel_id, after_seq=after_seq):
                yield sse_frame({"type": "round_outcome", "seq": outcome["base_seq"], "outcome": outcome})
            # 3) 当前状态点快照：四态只表达后端事实，重连后立刻回到真相。
            for member_id, status in orchestrator.member_statuses(
                [member["id"] for member in database.list_channel_agent_members(channel_id)]
            ).items():
                yield sse_frame(
                    {"type": "member_status", "seq": after_seq, "member_id": member_id, "status": status}
                )

            with events.subscribe(channel_id) as channel:
                deadline = asyncio.get_running_loop().time() + SSE_STREAM_MAX_SECONDS
                while not shutting_down.is_set():
                    if await request.is_disconnected():
                        return
                    if asyncio.get_running_loop().time() >= deadline:
                        # 主动轮换：先**明确告知**，浏览器就能区分「服务端请我重连」与
                        # 「后端挂了」，不会把例行轮换误报成不可达；随后带最新 last_seq 重连。
                        yield "event: rotate\ndata: {}\n\n"
                        return
                    try:
                        event = await asyncio.to_thread(channel.get, True, SSE_POLL_SECONDS)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    # 补拉与实时推送的边界：只发比连接游标更新的事件，不重复。
                    if int(event.get("seq", 0)) < after_seq:
                        continue
                    yield sse_frame(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ─── 人类面不存在的写路径：明确回 404 而不是 500 ───────────────────────
    @app.api_route(
        "/api/channels/{channel_id}/round-outcomes",
        methods=["PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_round_outcome_mutation(channel_id: str) -> JSONResponse:
        """轮次结果 append-only：它是「当时为什么没说话」的事实记录，不接受事后改写。"""
        return _not_found("轮次结果不可编辑、不可删除：人类面不存在该端点")

    @app.api_route(
        "/api/channels/{channel_id}/budget",
        methods=["PUT", "PATCH", "DELETE", "POST"],
        include_in_schema=False,
    )
    def no_budget_mutation(channel_id: str) -> JSONResponse:
        """预算只由后端在「人类消息重置 / 一次跳投递」时推进：人类面不能直接改它。"""
        return _not_found("回应预算只读：人类面不存在写入它的端点")

    @app.api_route(
        "/api/channels/{channel_id}/members/{member_id}",
        methods=["PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_channel_member_mutation(channel_id: str, member_id: str) -> JSONResponse:
        """本票没有「移出频道」的端点：离开 / 踢出需要单独的语义裁定（是否写 system 事件消息、
        是否影响历史未读与唤醒面），因此人类面明确回 404 而不是留一个沉默的 405。"""
        return _not_found("频道成员只能加入、不能移出：人类面不存在该端点")

    @app.api_route(
        "/api/channels/{channel_id}/messages/{seq}",
        methods=["GET", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_message_mutation(channel_id: str, seq: int) -> JSONResponse:
        """消息 append-only：人类 HTTP 面没有编辑或删除消息的端点。"""
        return _not_found("消息不可编辑、不可删除：人类面不存在该端点")

    @app.api_route(
        "/api/agents/{member_id}",
        methods=["PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_agent_mutation(member_id: str) -> JSONResponse:
        """本票没有改配置 / 删 Agent 的端点：模型与系统提示是配置数据，改配置的方式是新建。"""
        return _not_found("Agent 配置不可编辑、不可删除：人类面不存在该端点")

    # ─── 静态资源：工作台 + 只读设计系统 ─────────────────────────────────
    app.mount("/studio", StaticFiles(directory=str(studio_dir()), html=True), name="studio")

    design = design_system_dir()
    if design is not None:
        app.mount("/design", StaticFiles(directory=str(design)), name="design")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/studio/")

    return app
