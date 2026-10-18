"""HTTP 人类面：本机单用户的频道消息契约 + 事件流订阅。

端点（浏览器只经这里读写，业务正本在后端单文件库）：

- ``GET  /api/workspace``                     工作台快照（空库 = 空工作台）
- ``GET  /api/settings``                      工作区设置（语言偏好 / 消息密度 / 主题）
- ``PATCH /api/settings``                     写工作区设置（配置类取值；不落消息、不唤醒、不消耗预算）
- ``POST /api/attachments``                   上传一个**未绑定**的附件（原始字节；返回标识与元数据）
- ``GET  /api/attachments/{id}``              已绑定附件的内容（下载 / 预览；草稿 404）
- ``GET  /api/reminders``                     全部提醒（时间 / 文本 / 归属频道）
- ``POST /api/reminders``                     新建一条提醒（不落消息、不唤醒、不消耗预算）
- ``DELETE /api/reminders/{id}``              删除一条提醒（同上；不存在的 id ⇒ 404）
- ``GET  /api/search``                        工作台搜索（消息 / 任务 / Agent 三类命中，``facet`` 过滤）
- ``GET  /api/channels``                      频道列表（含未读计数）
- ``POST /api/channels``                      新建频道（名称 + 描述 + 初始成员，原子校验）
- ``GET  /api/channels/{id}/messages``        按 seq 升序取消息（``after_seq`` 增量）
- ``POST /api/channels/{id}/messages``        发送人类消息（同事务分配频道内单调 seq）
- ``GET  /api/channels/{id}/members``         频道成员
- ``POST /api/channels/{id}/members``         把 Agent 成员加入频道（幂等，只接受 Agent）
- ``GET  /api/channels/{id}/config``          频道配置（mode / team_scope / 队长 / workspace / 权限）
- ``PATCH /api/channels/{id}/config``         写频道配置（team 需合法队长；任一字段不合法整体拒绝）
- ``GET  /api/channels/{id}/round-outcomes``  轮次结果（只读，无 UI）
- ``GET  /api/channels/{id}/budget``          有界响应预算的读数（当前世代 + 来源 + 待生效值）
- ``PATCH /api/channels/{id}/budget``         保存回应预算**上界**（固定档位 3 / 5 / 10；用量不可写）
- ``GET  /api/channels/{id}/events``          事件流（SSE，``after_seq`` 补拉）
- ``POST /api/channels/{id}/read``            标读（同一频道内按 seq 单调取 max）
- ``GET  /api/tasks``                         全工作区任务看板（可按频道筛选）
- ``POST /api/channels/{id}/tasks``           在频道下新建任务与独立线程
- ``GET/POST /api/tasks/{id}/messages``       读取 / 追加任务线程消息
- ``GET  /api/tasks/{id}/events``             任务线程事件流（SSE，``after_seq`` 补拉）
- ``GET  /api/targets/{id}/team-runs``        team 运行记录与分派快照（倒序分页、可按 task 过滤）
- ``GET  /api/agents``                         Agent 成员 + 模型配置 + 四态状态点
- ``POST /api/agents``                         新建 Agent（名称 + 角色 + 模型 + 系统提示）
- ``GET  /api/agents/{id}``                    单个 Agent（含「为什么未启用」的可读原因）
- ``GET  /api/remote-data-flow``               远端数据流告知文案与已确认记录
- ``POST /api/remote-data-flow/ack``           确认一次告知（按模型提供商）

**HTTP 面自始至终属于人类面**：Agent 的任何动作不经 HTTP，由后端调用点（唤醒编排）直接执行。
因此这里没有「以某成员名义调用」的路径，也**没有**创建或编辑系统消息的端点。

消息是 append-only：**没有**编辑或删除消息的端点。轮次结果同样 append-only。

附件（ADR-0014）是**两步上传**：先 ``POST /api/attachments`` 拿标识，再随消息提交时绑定
（``attachment_ids``），绑定与消息落库在同一个事务里。未绑定的上传是**未发送的草稿**：它不出现
在任何读取面里（内容端点对草稿也回 404）。附件字节落数据目录、库里只有元数据；与消息同口径，
**没有**删除附件的端点。
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from .agent_seam import AgentScopeAgentSeam
from .db import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_NAME_LENGTH,
    MAX_MESSAGE_BODY_LENGTH,
    OWNER_ID,
    SEARCH_RESULT_LIMIT,
    Database,
    DomainError,
    SearchFacet,
    open_database,
)
from .events import EventBus, is_replayable, sse_frame
from .i18n import locale_of, render
from .orchestration import ThreadScheduler, WakeOrchestrator
from .reminder_delivery import (
    ReminderDeliverer,
    ReminderDeliveryDriverFactory,
    ReminderDeliveryWorker,
)
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


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""


class TaskStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = ""


class TaskTitleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""


class TaskAssigneeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str | None


class DmCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = ""


class DmMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = ""
    # 先上传拿标识、随消息提交时绑定（ADR-0014）：绑定与消息落库在同一事务里。
    attachment_ids: list[str] = Field(default_factory=list)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = ""
    task_ref: str | None = None
    # 先上传拿标识、随消息提交时绑定（ADR-0014）：绑定与消息落库在同一事务里。
    attachment_ids: list[str] = Field(default_factory=list)


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    role: str = ""
    model_id: str = ""
    system_prompt: str = ""


class AgentUpdate(BaseModel):
    """Agent 配置的**部分**更新（ADR-0011 的唯一写路径）。

    字段缺省 = 这次请求没提到这一项（保持原值）；未知键由 ``extra="forbid"`` **整体拒绝**。
    **显式 ``null`` 不是「没提到」**：它既不是字段取值也不是合法取值，因此同样整体拒绝，
    而不是被静默当成一次成功的空更新（与 `SettingsUpdate` 同一条纪律）。

    ``auto_claim``（#65 / ADR-0010）是「自动认领任务」开关，与 ``enabled`` 一样是**布尔配置**：
    ``true`` 打开、``false`` 关闭，缺省 = 这次不提它。
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    role: str | None = None
    model_id: str | None = None
    system_prompt: str | None = None
    enabled: bool | None = None
    auto_claim: bool | None = None

    @field_validator(
        "display_name", "role", "model_id", "system_prompt", "enabled", "auto_claim", mode="before"
    )
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        """Pydantic v2 的字段校验器只对**出现在请求体里**的字段运行，因此这里只拦显式 ``null``。"""
        if value is None:
            raise _TierValueError("error.agent_field_null")
        return value


class RemoteConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = ""


class ChannelMemberAdd(BaseModel):
    """把一个 Agent 成员加入频道（人类面动作）。"""

    model_config = ConfigDict(extra="forbid")

    member_id: str = ""


class ChannelConfigUpdate(BaseModel):
    """频道配置的**部分**更新（规格 §4.4 / ADR-0018 / ADR-0021）。

    未知键由 ``extra="forbid"`` 整体拒绝；字段缺省 = 这次请求没提到它（保持原值）。
    ``workspace_root`` 是唯一允许显式 ``null`` 的字段（``null`` = 清空项目根，即回到「未配置」
    的既有默认行为）；其余字段的显式 ``null`` 既不是合法取值、也不是「没提到」，整体拒绝。

    ``team_scope`` 是只读事实（固定 ``channel+task``），不在可写字段里：提交它会被
    ``extra="forbid"`` 拒绝，而不是被静默忽略。
    """

    model_config = ConfigDict(extra="forbid")

    mode: str | None = None
    team_leader_member_id: str | None = None
    workspace_root: str | None = None
    workspace_backend: str | None = None
    permission_mode: str | None = None

    @field_validator("mode", "team_leader_member_id", "workspace_backend", "permission_mode", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise _TierValueError("error.field.channel_config_field_null")
        return value


class MarkRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # strict："3" 这类字符串不会被静默当成 3。
    seq: Annotated[int, Field(strict=True)]


class ChannelBudgetUpdate(BaseModel):
    """频道回应预算**上界**的写入（ADR-0007 / 规格 #31 §2；写路径形态照 ADR-0011）。

    人类面只能表达**上界**，因此可写字段只有一个 ``budget_limit``：

    - 取值只接受固定档位 ``{3, 5, 10}``；档位裁决落在库层（``Database.set_channel_budget_limit``），
      于是非法档位得到的是与读路径同一句词条文案（``error.budget_limit_invalid``），且**整体不写**。
      ``strict=True`` 与 ``MarkRead.seq`` 同一条纪律：``"5"`` 这类字符串不会被静默当成 5。
    - **用量与世代事实不可写**：``used`` / ``remaining`` / ``epoch_seq`` / ``applied_limit`` 被显式
      声明，但校验器对它们**一律拒绝**。写成显式字段（而不是留给 ``extra="forbid"`` 当未知键）是有意
      的：未知键只能得到一句泛泛的「请求体不合法」，而这里必须说清「用量由后端独占维护」
      （ADR-0007：``target_budget.used`` 是跳数正本，人类面既写不了、也清不了）。
    - **只读字段声明在 ``budget_limit`` 之前是有意的**：pydantic 按**字段定义序**校验，因此
      ``{"used": 0}``（缺必填 + 提交用量）与 ``{"budget_limit": 5, "used": 0}`` 的首条错误都是
      「用量只读」那条词条，而不是一句旁支的「字段缺了」。这只是**本端点**的字段顺序，不牵动
      ``_validation_detail`` 的共享选择规则——那条规则仍只认**首条**错误（见该函数的文档）。
    - 未知键由 ``extra="forbid"`` 整体拒绝，不静默忽略。于是任何一条被拒的请求都在服务层之前就
      结束，**不留下半成品**（配置表一个字都没写）。
    """

    model_config = ConfigDict(extra="forbid")

    # 顺序即校验顺序（见类文档串）：只读字段在前，可写的 budget_limit 在后。
    used: Any = None
    remaining: Any = None
    epoch_seq: Any = None
    applied_limit: Any = None
    budget_limit: Annotated[int, Field(strict=True)]

    @field_validator("used", "remaining", "epoch_seq", "applied_limit", mode="before")
    @classmethod
    def _reject_usage_write(cls, value: Any) -> Any:
        """Pydantic v2 的字段校验器只对**出现在请求体里**的字段运行，因此这里只拦真正提交的用量字段。"""
        raise _TierValueError("error.budget_used_read_only")


class _TierValueError(ValueError):
    """**档位取值**类校验错误：只带**词条键**，正文由边界按语言偏好渲染。

    pydantic 的字段校验器跑在请求进入人类面之前，那里**没有语言偏好可读**（ADR-0009 第二层的取值
    来自库内设置）；因此校验器只把「该说哪一句」的键抛出去，由 ``RequestValidationError`` 处理器
    在能读到偏好的边界处渲染。``ValueError`` 的 ``str()`` 只是这个键本身（pydantic 会把它包进
    ``msg``，但那条 ``msg`` 不再被序列化给用户，见 ``_validation_error_handler``）。
    """

    def __init__(self, key: str):
        self.key = key
        super().__init__(key)


class SettingsUpdate(BaseModel):
    """工作区设置的**部分**更新。

    未知键由 ``extra="forbid"`` **整体拒绝**（``validation_failed``），不静默忽略：
    把设置面当成通用 key-value 存储正是 `CONTEXT.md` **工作区设置** 的 `_Avoid_`；
    档位取值由服务层按 ``LOCALES`` / ``MESSAGE_DENSITIES`` / ``THEMES`` 校验，错误体仍是既有的
    ``code`` + ``fields`` 形态。字段缺省 = 这一次请求没提到这一档（保持原值）。

    **显式 ``null`` 不是「没提到」**：它既不是档位取值、也不是合法档位，因此同样整体拒绝，
    而不是被当成一次成功的空更新（``null`` 与「这一档保持原值」是两件事，后者由**省略字段**表达）。
    """

    model_config = ConfigDict(extra="forbid")

    locale: str | None = None
    message_density: str | None = None
    # 主题三档（#71）：显式浅色 / 显式深色 / 跟随系统。它是**配置类**取值，与另外两档走同一条
    # 读写路径；三档的**取值层**（深色 token）在设计系统包里，与本端点无关。
    theme: str | None = None

    @field_validator("locale", "message_density", "theme", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        """Pydantic v2 的字段校验器只对**出现在请求体里**的字段运行，因此这里只拦显式 ``null``。"""
        if value is None:
            raise _TierValueError("error.tier_value_null")
        return value


class ReminderCreate(BaseModel):
    """新建一条提醒（`CONTEXT.md` **提醒**：时间 + 文本 + **可归属**一个频道）。

    未知键由 ``extra="forbid"`` 整体拒绝（与设置面同一形态）。**没有日期与重复字段**：原型
    `#rmTime` 只有 `type="time"`，本票不扩张。

    ``channel_id`` 的显式 ``null`` 在这里**是有含义的取值**——「不归属任何频道」（空工作台或
    私信视图下也设得成提醒），因此它**不**沿用设置面那条「显式 null 一律拒绝」的规则：
    那条规则的理由是档位 `null` 既不是档位取值、也不是「没提到」，而这里的 `null` 恰好就是
    「不归属」这个合法状态。文本与时间的形状由库层按 `REMINDER_TIME_PATTERN` 与上限校验。
    """

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    time: str = ""
    channel_id: str | None = None


def create_app(
    data_dir: Path | str | None = None,
    db: Database | None = None,
    *,
    scheduler: Any | None = None,
    seam: Any | None = None,
    reminder_delivery: ReminderDeliveryDriverFactory | None = None,
) -> FastAPI:
    """组装后端。

    ``scheduler`` / ``seam`` / ``reminder_delivery`` 是测试注入点：传入手动调度器、脚本化替身与**手动
    触发的到期驱动器**，就能在不联网、零成本、**不等时间**的前提下把行为固定下来，并让测试自己
    决定什么时候跑完一轮、什么时候喂一个时刻给到期投递器。生产默认是线程池 + 真实 AgentScope 适配器
    + 后台轮询线程（启动先扫一次）。

    ``reminder_delivery`` 收的是一个**工厂**（``ReminderDeliverer -> 驱动器``）而不是实例：到期投递器
    需要本函数刚打开的库句柄，而驱动器需要投递器——工厂把这条依赖顺序收在一处。它与 ``scheduler``
    同处一个装配点，**不开第二个**（C8）。
    """
    database = db if db is not None else open_database(data_dir)
    events = EventBus()
    # 优雅关停的信号：事件流是**永不自然结束**的响应，如果不主动收，进程会在 graceful shutdown
    # 里等那两个连接直到超时（表现为「进程还在、监听套接字已没、新请求全部被拒」）。
    shutting_down = threading.Event()
    orchestrator = WakeOrchestrator(
        database,
        # 真实适配器按**语言偏好**渲染它自己产出的文案（轮次 reason / 凭证原因）：取值方式由
        # 装配点注入，适配层自己不依赖库（ADR-0009 第二层）。
        seam
        if seam is not None
        else AgentScopeAgentSeam(
            locale_of=lambda: locale_of(database),
            # workspace 装配钩子（#148）：频道显式配置 ``workspace_root`` 后这一轮才挂受限文件工具；
            # 未配置时读路径返回 None，行为逐字不变。
            workspace_of=database.workspace_settings_for_target,
        ),
        events,
        scheduler if scheduler is not None else ThreadScheduler(),
    )
    # **到期投递器**（ADR-0017 §四）：判定「到点」的唯一 seam。它的产物是 `system` 消息与行上的
    # `fired_at`，与唤醒编排无关——到点**不唤醒**任何人，因此这里只把 `EventBus` 交给它做广播。
    make_driver: ReminderDeliveryDriverFactory = (
        reminder_delivery if reminder_delivery is not None else ReminderDeliveryWorker
    )
    driver = make_driver(ReminderDeliverer(database, events))
    service = WorkspaceService(database, orchestrator=orchestrator, events=events)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 开窗前先扫一次（C8 判据 3）：一条启动前就已过点且在窗口内的提醒，在「第一次读到
        # ``GET /api/reminders``」时 `fired_at` 已非空，不需要等第一轮 30 秒轮询。
        start_driver = getattr(driver, "start", None)
        if callable(start_driver):
            start_driver()
        try:
            yield
        finally:
            # 顺序很重要：先让事件流退出（它们会在半个 keep-alive 周期内看到这个信号），
            # 再停到期投递器（它写库，必须收口在关库**之前**，否则关库之后它还能拿到库句柄），
            # 再停调度器，最后关库（C10）。
            shutting_down.set()
            close_driver = getattr(driver, "shutdown", None)
            if callable(close_driver):
                close_driver()
            close_scheduler = getattr(orchestrator._scheduler, "shutdown", None)
            if callable(close_scheduler):
                close_scheduler()
            database.close()

    app = FastAPI(title="Alibot", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db = database
    app.state.service = service
    app.state.events = events
    app.state.orchestrator = orchestrator
    app.state.reminder_delivery = driver
    app.state.data_dir = database.data_dir

    def _not_found(message: str) -> JSONResponse:
        """统一 404 形状。``message`` 由调用点按**当前语言偏好**渲染（`i18n.render`）。"""
        return JSONResponse({"error": {"code": "not_found", "message": message}}, status_code=404)

    def _render_copy(key: str, **params: Any) -> str:
        """本模块产出的用户可见文案一律经它取值（语言偏好读库，ADR-0009 第二层）。"""
        return render(key, locale_of(database), **params)

    def _validation_detail(exc: RequestValidationError) -> str:
        """``fields.body`` 的那一句话：正文按**语言偏好**渲染，机器可读的标识不译。

        之前这里放的是 pydantic 错误的 ``repr``——它是 Python 结构（``type`` / ``loc`` / ``msg`` /
        ``input``），前端 `readError` 会把 ``fields`` 的值拼进错误提示，于是用户在中文界面里读到一段
        英文 repr。现在：句子是我们的（词条表），**出错位置与 pydantic 的错误类型原样保留**（它们是
        标识符，与 ``code`` 同类）；自定义校验器（``_TierValueError``）带回词条键，在这里才渲染——
        校验器跑到时还没有语言偏好可读。

        取值规则只有一条：**首条错误**带校验器词条就用它，否则回退到首条错误的「位置 + 类型」。
        需要「实质拒绝优先于旁支错误」的模型把只读字段声明在必填字段之前即可（pydantic 按定义序
        校验），不在这里加「任意一条词条优先」的规则——那会改变所有端点（``AgentUpdate`` /
        ``SettingsUpdate`` 等）的 400 文案选择。
        """
        error = exc.errors()[0] if exc.errors() else {}
        context_error = (error.get("ctx") or {}).get("error")
        if isinstance(context_error, _TierValueError):
            return _render_copy(context_error.key)
        location = ".".join(str(part) for part in error.get("loc", ())) or "body"
        return _render_copy(
            "error.field.body_invalid", location=location, kind=str(error.get("type") or "invalid")
        )

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
                    "message": _render_copy("error.request_body_invalid"),
                    "fields": {"body": _validation_detail(exc)},
                }
            },
            status_code=400,
        )

    # ─── 附件（两步上传：先拿标识、随消息提交时绑定；ADR-0014） ────────────
    @app.post("/api/attachments", status_code=201)
    async def upload_attachment(
        request: Request,
        name: Annotated[str, Query(max_length=MAX_ATTACHMENT_NAME_LENGTH)] = "",
    ) -> dict[str, Any]:
        """人类面第一步：上传一个**未绑定**的附件，返回标识与可读元数据。

        请求体是文件的**原始字节**（不是 multipart）：``Content-Type`` 是声明的 MIME，文件名
        在 query 的 ``name`` 上。于是后端不需要 multipart 解析依赖，而「类型上限」判的本来就是
        声明类型（本机单用户、无鉴权，不做内容嗅探）。

        读取时按上限**截断累积**（最多多读一个 chunk）：超限的请求不会先把整个大文件吃进内存，
        真正的拒绝与可读原因在服务层一处给出，因此错误体仍是既有的 ``code`` + ``fields`` 形状。
        """
        data = bytearray()
        async for chunk in request.stream():
            if len(data) <= MAX_ATTACHMENT_BYTES:
                data.extend(chunk)
        return {
            "attachment": service.upload_attachment(
                name=name,
                mime_type=request.headers.get("content-type", ""),
                data=bytes(data),
            )
        }

    @app.get("/api/attachments/{attachment_id}")
    def download_attachment(
        attachment_id: str,
        disposition: Annotated[Literal["attachment", "inline"], Query()] = "attachment",
    ) -> FileResponse:
        """已绑定附件的内容：按原文件名下载（``disposition=inline`` 则浏览器内预览）。

        草稿（还没随消息发送的上传）回 404——读取面不暴露未发送的上传。响应带
        ``Content-Disposition``（原文件名）与库里记录的那个 MIME。
        """
        attachment, path = service.attachment_for_download(attachment_id)
        return FileResponse(
            path,
            media_type=attachment["mime_type"],
            filename=attachment["name"],
            content_disposition_type=disposition,
        )

    # ─── 工作台 ────────────────────────────────────────────────────────────
    @app.get("/api/workspace")
    def get_workspace() -> dict[str, Any]:
        return service.bootstrap()

    @app.get("/api/health")
    def get_health() -> dict[str, Any]:
        return {"status": "ok", "member_id": OWNER_ID, "data_dir": str(app.state.data_dir)}

    # ─── 工作区设置（配置类取值：语言偏好 / 消息密度 / 主题） ────────────────
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        """工作区设置（**语言偏好** / **消息密度** / **主题**）。正本是库内的那一行。"""
        return {"settings": service.settings()}

    @app.patch("/api/settings")
    def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
        """部分更新工作区设置。

        只写**配置类**取值：改设置**不落消息、不唤醒、不消耗预算**（ADR-0007 的边界：
        人类面可以写配置，不得写用量类事实）。未知键与非法档位整体拒绝，不留一半新一半旧。
        """
        return {
            "settings": service.update_settings(
                locale=payload.locale,
                message_density=payload.message_density,
                theme=payload.theme,
            )
        }

    # ─── 提醒（库内对象；ADR-0012 增删不落消息 / ADR-0017 到点落 system 消息） ──
    @app.get("/api/reminders")
    def list_reminders() -> dict[str, Any]:
        """全部提醒（时间 / 文本 / 归属频道 / 已到点标记与派生状态）。正本是库里的 ``reminder`` 表。

        每条恒含 ``fired_at``（``null`` = 还没到点）与 ``state``（``pending`` / ``fired`` / ``missed``，
        后端派生的只读值）。**无归属**提醒到点不落消息，因此这两个键是它唯一可观察的到点证据。
        """
        return {"reminders": service.reminders()}

    @app.post("/api/reminders", status_code=201)
    def create_reminder(payload: ReminderCreate) -> dict[str, Any]:
        """新建一条提醒。

        **增删**提醒**不落消息、不唤醒、不消耗预算**（ADR-0012 / D18）：提醒的增删不是对话事实，
        因此这里既不写 `system` 消息，也不新增 target、不计未读。
        **到点**是另一件事（ADR-0017 §一）：到期投递器会为**有归属**的提醒落一条 `kind='system'`
        消息进该频道的消息流（作者恒为 owner），但它**不唤醒**任何人、不消耗回应预算、不计未读、
        不进搜索；**无归属**的提醒到点只写 `fired_at`、不落消息。
        响应同时带**最新**的完整提醒表，于是前端一次调用就能重绘列表，不必「先写再读」两次往返。
        """
        reminder = service.create_reminder(payload.text, payload.time, payload.channel_id)
        return {"reminder": reminder, "reminders": service.reminders()}

    @app.delete("/api/reminders/{reminder_id}")
    def delete_reminder(reminder_id: str) -> dict[str, Any]:
        """删除一条提醒（人类面动作）；同样不落消息、不唤醒、不消耗预算。

        「不落消息」在这里指的是**删除动作本身**不写消息——它不回收已到点落下的那条 `system` 消息
        （消息是 append-only 的取证面，ADR-0001）。不存在的 id ⇒ 404 ``not_found``。
        """
        reminder = service.delete_reminder(reminder_id)
        return {"reminder": reminder, "reminders": service.reminders()}

    @app.get("/api/search")
    def search(
        q: Annotated[str, Query(min_length=1, max_length=MAX_MESSAGE_BODY_LENGTH)],
        facet: Annotated[SearchFacet, Query()] = "all",
        target_id: str | None = Query(default=None),
        limit: int = Query(default=SEARCH_RESULT_LIMIT, ge=1, le=SEARCH_RESULT_LIMIT),
    ) -> dict[str, Any]:
        """搜索三类命中：消息（正文）、任务（标题）、Agent（显示名与角色）。

        ``facet`` 是**后端的过滤参数**（``all`` / ``message`` / ``task`` / ``agent``），
        非法取值与非法 ``limit`` 一样整体拒绝（400）。命中判定与高亮区间都由后端产生，
        前端只按区间包标签；``target_id`` 沿用既有语义（只在该 target 作用域内搜消息）。
        """
        return {"hits": service.search(q, facet=facet, target_id=target_id, limit=limit)}

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

    @app.get("/api/tasks")
    def list_tasks(channel_id: str | None = Query(default=None)) -> dict[str, Any]:
        return {"tasks": service.tasks(channel_id=channel_id)}

    @app.post("/api/channels/{channel_id}/tasks", status_code=201)
    def create_task(channel_id: str, payload: TaskCreate) -> dict[str, Any]:
        return service.create_task(channel_id=channel_id, title=payload.title)

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        return service.task(task_id)

    @app.get("/api/targets/{target_id}/team-runs")
    def list_team_runs(
        target_id: str,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        task_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """运行面板的快照兜底：该 target 的 team 运行（倒序）与分派记录。

        ``target_id`` 可以是频道，也可以是任务线程的 target。``task_id`` 只做**命中过滤**
        （未知 task 与空页同形）；``has_more`` 由多取一条判定，调用方按 ``offset + limit``
        继续分页。``agent_event`` / ``team_assignment`` 是 live-only，断线后只能靠这份读面恢复。
        """
        return service.team_runs(target_id, limit=limit, offset=offset, task_id=task_id)

    @app.patch("/api/tasks/{task_id}/status")
    def update_task_status(task_id: str, payload: TaskStatusChange) -> dict[str, Any]:
        return service.update_task_status(task_id, payload.status)

    @app.patch("/api/tasks/{task_id}/title")
    def update_task_title(task_id: str, payload: TaskTitleChange) -> dict[str, Any]:
        return service.update_task_title(task_id, payload.title)

    @app.patch("/api/tasks/{task_id}/assignee")
    def update_task_assignee(task_id: str, payload: TaskAssigneeChange) -> dict[str, Any]:
        return service.update_task_assignee(task_id, payload.member_id)

    @app.get("/api/tasks/{task_id}/members")
    def list_task_members(task_id: str) -> dict[str, Any]:
        return {"members": service.task_members(task_id)}

    @app.get("/api/tasks/{task_id}/budget")
    def get_task_budget(task_id: str) -> dict[str, Any]:
        return {"budget": service.task_budget(task_id)}

    @app.post("/api/tasks/{task_id}/read")
    def mark_task_read(task_id: str, payload: MarkRead) -> dict[str, Any]:
        return {"task": service.mark_task_read(task_id, payload.seq)}

    @app.get("/api/tasks/{task_id}/messages")
    def list_task_messages(task_id: str, after_seq: int = Query(default=0, ge=0)) -> dict[str, Any]:
        return service.task_messages(task_id, after_seq=after_seq)

    @app.post("/api/tasks/{task_id}/messages", status_code=201)
    def create_task_message(task_id: str, payload: MessageCreate) -> dict[str, Any]:
        return {
            "message": service.send_task_message(
                task_id, payload.body, payload.task_ref, payload.attachment_ids
            )
        }

    # ─── 私信（显式创建；重复请求复用同一 target） ─────────────────────────
    @app.get("/api/dms")
    def list_dms() -> dict[str, Any]:
        return {"dms": service.list_dms()}

    @app.post("/api/dms")
    def create_dm(payload: DmCreate) -> JSONResponse:
        dm, created = service.create_dm(payload.agent_id)
        return JSONResponse(
            {"dm": dm, "created": created},
            status_code=201 if created else 200,
        )

    @app.get("/api/dms/{dm_id}/messages")
    def list_dm_messages(dm_id: str, after_seq: int = Query(default=0, ge=0)) -> dict[str, Any]:
        messages = service.dm_messages(dm_id, after_seq=after_seq)
        dm = next(dm for dm in service.list_dms() if dm["id"] == dm_id)
        return {"dm": dm, "messages": messages}

    @app.post("/api/dms/{dm_id}/messages", status_code=201)
    def create_dm_message(dm_id: str, payload: DmMessageCreate) -> dict[str, Any]:
        return {"message": service.send_dm_message(dm_id, payload.body, payload.attachment_ids)}

    @app.post("/api/dms/{dm_id}/read")
    def mark_dm_read(dm_id: str, payload: MarkRead) -> dict[str, Any]:
        dm = service.mark_dm_read(dm_id, payload.seq)
        return {"dm": dm, "dms": service.list_dms()}

    @app.get("/api/channels/{channel_id}/messages")
    def list_messages(channel_id: str, after_seq: int = Query(default=0, ge=0)) -> dict[str, Any]:
        messages = service.channel_messages(channel_id, after_seq=after_seq)
        channel = database.get_channel(channel_id)
        return {"channel": channel, "messages": messages}

    @app.post("/api/channels/{channel_id}/messages", status_code=201)
    def create_message(channel_id: str, payload: MessageCreate) -> dict[str, Any]:
        message = service.send_message(channel_id, payload.body, payload.task_ref, payload.attachment_ids)
        return {"message": message}

    @app.get("/api/channels/{channel_id}/members")
    def list_channel_members(channel_id: str) -> dict[str, Any]:
        return {"members": service.channel_members(channel_id)}

    @app.get("/api/channels/{channel_id}/config")
    def get_channel_config(channel_id: str) -> dict[str, Any]:
        """频道配置：mode / ``team_scope=channel+task`` / 队长 / workspace / 权限档。

        ``leader_available`` / ``leader_reason`` 与唤醒裁决读**同一个**后端事实（D18）：队长被停用
        或删除时设置页给出可读原因，频道消息照常落库但不唤醒任何人，不回落 autonomous。
        """
        return {"config": service.channel_config(channel_id)}

    @app.patch("/api/channels/{channel_id}/config")
    def update_channel_config(channel_id: str, payload: ChannelConfigUpdate) -> dict[str, Any]:
        """写频道配置（人类面动作）：任一字段不合法**整体拒绝**、不留下半成品。

        ``mode=team`` 要求队长是当前频道成员且非 deleted；它同时覆盖该频道 target 与全部 task
        target（读路径解析，不复制到 task 行）。本端点**不**改 ``auto_claim``（D16 是读路径规则）。
        """
        return {"config": service.update_channel_config(channel_id, payload.model_dump(exclude_unset=True))}

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

    # ─── 有界响应预算（只读；频道设置视图的读数面） ──────────────────────
    @app.get("/api/channels/{channel_id}/budget")
    def get_channel_budget(channel_id: str) -> dict[str, Any]:
        """该频道当前人类世代的有界响应预算（后端持久事实）。

        它让「任一人类世代内 agent→agent 跳数 ≤ 该世代生效上界」与「重启后预算状态不变」可以经
        HTTP 面直接断言，而不用从消息与轮次结果反推。

        ``used`` 是「该人类世代已用跳数」的**唯一正本**：要从只读面拿跳数就读它，
        不要对 ``round_outcome`` 的 ``consumed_budget`` 求和（见那个端点的说明）。

        #53 扩展（**既有字段名一个字不改**，只做附加）：

        - ``limit`` = 当前人类世代的**有效上界**，``remaining`` 按它算（不是按待生效值）；
        - ``source`` = ``channel-override``（库内逐频道配置）/ ``built-in-default``（内置默认 3）；
        - ``pending_limit`` = 已保存、**从下一个人类世代起生效**的上界；没有待生效差异时为 ``null``。
          它只是「下个世代会变成多少」，不会改变本世代返回的 ``used`` / ``remaining``。

        读行为只经这个既有端点取证；#53 只做附加（既有字段名一个字不改），**写入口是同一路径上的
        ``PATCH``**（见 ``update_channel_budget``；``used`` 仍由后端独占维护，ADR-0007）。
        """
        return {"budget": service.channel_budget(channel_id)}

    @app.patch("/api/channels/{channel_id}/budget")
    def update_channel_budget(channel_id: str, payload: ChannelBudgetUpdate) -> dict[str, Any]:
        """保存该频道的回应预算**上界**（人类面唯一的写路径，ADR-0007 / 规格 #31 §2）。

        只表达上界：请求体只有一个可写的 ``budget_limit``（固定档位 3 / 5 / 10，默认仍为 3）。
        带上 ``used``（或 ``remaining`` / ``epoch_seq`` / ``applied_limit``）一律**整体拒绝**：
        用量与世代事实是后端的正本，人类面既写不了、也清不了；``used`` 仍只在人类消息落库开世代时
        重置、并只在一次**实际投递**的 Agent→Agent 跳上 +1。

        成功后返回**刷新后的只读读数**，与 ``GET`` 同一份载荷——前端一次调用就能同时刷新当前世代
        读数与待生效值。三条语义与读路径是同一条事实：当前世代**不被回溯改写**（``limit`` / ``used``
        / ``remaining`` 一字不变）、新值以 ``pending_limit`` 报出、从**下一个人类世代**起生效。
        """
        return {"budget": service.set_channel_budget_limit(channel_id, payload.budget_limit)}

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

    @app.patch("/api/agents/{member_id}")
    def update_agent(member_id: str, payload: AgentUpdate) -> dict[str, Any]:
        """编辑 Agent 配置（人类面唯一写路径，ADR-0011）。

        可写 ``display_name`` / ``role`` / ``model_id`` / ``system_prompt`` / ``enabled`` /
        ``auto_claim``；任一字段不合法则**整体不写**（不得部分写入）。``model_id`` 沿用与创建同一套
        校验与**远端数据流告知**语义。**「暂停」就是这个端点上的 `enabled=false`**（ADR-0011：写
        `enabled = 0`，不改唤醒面）；**「自动认领任务」就是 `auto_claim`**（#65 / ADR-0010：编排在
        新建任务后读它，它不改唤醒面、不落消息、不消耗预算）。

        配置变更**不落消息、不唤醒、不消耗预算、不开世代**——它不是一次「发言」。响应形状与
        ``POST /api/agents`` 同形（``agent`` + ``agents`` + ``members``），前端一次调用就能刷新
        详情、左栏与成员面板。
        """
        agent = service.update_agent(
            member_id,
            display_name=payload.display_name,
            role=payload.role,
            model_id=payload.model_id,
            system_prompt=payload.system_prompt,
            enabled=payload.enabled,
            auto_claim=payload.auto_claim,
        )
        return {"agent": agent, "agents": service.agents(), "members": database.list_members()}

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

        **三类快照都在返回 ``StreamingResponse`` 之前一次读齐**：headers（状态码 200）一旦送出，
        后面任何 ``DomainError`` 都换不成状态码——Starlette 只会报
        ``RuntimeError: Caught handled exception, but response already started``，客户端拿到
        「200 + 0 字节」后断流。前置之后，作用域行缺失（``target`` 影子行不存在）走的是正常的
        404 封装路径，与 ``database.get_channel`` 的早退同一个形态。

        这条路径**不是理论风险**：v4 代码打开一个 v3 库时，``channel`` 行被原样留下、而承载作用域
        的 ``target`` 影子行不会被补上（旧库的成员关系在 ``channel_member`` 里，代码已不再读它）。
        于是 ``GET /api/channels`` 照旧 200、列表里能看到这个频道，点进去的每个端点（含事件流）
        都会在读写那一刻撞上这个不一致。事件流的症状最糟——它先发 200 再抛错。

        """
        database.get_channel(channel_id)  # 404 早退

        # 快照在响应开始之前读完（理由见 docstring）。三类读都要前置：只前置消息的话，
        # 「消息发了一半、轮次结果读失败」仍会留下一个半真半假的流。
        snapshot_messages = service.channel_messages(channel_id, after_seq=after_seq)
        snapshot_outcomes = service.round_outcomes(channel_id, after_seq=after_seq)
        snapshot_statuses = orchestrator.member_statuses(
            [member["id"] for member in database.list_target_agent_members(channel_id)]
        )

        async def stream() -> AsyncIterator[str]:
            # 1) 补拉：消息是正本，直接读库（Agent 回复与人类消息走同一条路径）。
            for message in snapshot_messages:
                yield sse_frame({"type": "message", "seq": message["seq"], "message": message})
            # 2) 补拉：轮次结果（也只读库）。
            for outcome in snapshot_outcomes:
                yield sse_frame({"type": "round_outcome", "seq": outcome["base_seq"], "outcome": outcome})
            # 3) 当前状态点快照：四态只表达后端事实，重连后立刻回到真相。
            for member_id, status in snapshot_statuses.items():
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
                    # live-only 事件没有 seq——它们不参与游标比较，否则重连带 after_seq>0 时会被整批吞掉。
                    if is_replayable(event) and int(event.get("seq", 0)) < after_seq:
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

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(
        task_id: str,
        request: Request,
        after_seq: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        """订阅任务线程事件流；重连时从任务 target 的消息正本与轮次结果补拉。

        任务线程是 team 模式下运行记录的另一半载体（规格 §4.4 / §4.5）：``agent_event`` /
        ``team_assignment`` / ``plan_update`` / ``sop_event`` / ``approval_*`` 与频道事件流同形透传；
        它们没有 seq、不发 ``id``，``after_seq`` 补拉仍只覆盖 ``message`` 与 ``round_outcome``。
        """
        task = service.task(task_id)["task"]  # 响应开始前做作用域校验，确保不存在的任务返回 404。
        subscription = events.subscribe(task["target_id"])
        subscriber = subscription.__enter__()
        try:
            snapshot = service.task_messages(task_id, after_seq=after_seq)["messages"]
            snapshot_outcomes = service.task_round_outcomes(task_id, after_seq=after_seq)
        except Exception:
            subscription.__exit__(None, None, None)
            raise

        async def stream() -> AsyncIterator[str]:
            try:
                for message in snapshot:
                    yield sse_frame({"type": "message", "seq": message["seq"], "message": message})
                for outcome in snapshot_outcomes:
                    yield sse_frame({"type": "round_outcome", "seq": outcome["base_seq"], "outcome": outcome})

                deadline = asyncio.get_running_loop().time() + SSE_STREAM_MAX_SECONDS
                while not shutting_down.is_set():
                    if await request.is_disconnected():
                        return
                    if asyncio.get_running_loop().time() >= deadline:
                        yield "event: rotate\ndata: {}\n\n"
                        return
                    try:
                        event = await asyncio.to_thread(subscriber.get, True, SSE_POLL_SECONDS)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    # live-only 事件没有 seq：只有可补拉事件参与游标比较（理由同频道事件流）。
                    if is_replayable(event) and int(event.get("seq", 0)) <= after_seq:
                        continue
                    yield sse_frame(event)
            finally:
                subscription.__exit__(None, None, None)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/dms/{dm_id}/events")
    async def dm_events(
        dm_id: str,
        request: Request,
        after_seq: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        """私信事件流：补拉消息与对方 Agent 的状态，不暴露频道轮次结果。"""
        dm = database.get_dm(dm_id, OWNER_ID)  # 在响应开始前完成 404 早退

        # 先登记订阅再读快照：否则在读完快照、注册订阅之间落库并广播的消息会永久漏掉。
        # 快照仍在返回 StreamingResponse 前读完，保留错误在 headers 发出前正常返回的保证。
        subscription = events.subscribe(dm_id)
        channel = subscription.__enter__()
        subscription_open = True

        def close_subscription() -> None:
            nonlocal subscription_open
            if subscription_open:
                subscription_open = False
                subscription.__exit__(None, None, None)

        try:
            snapshot_messages = service.dm_messages(dm_id, after_seq=after_seq)
            snapshot_statuses = orchestrator.member_statuses([dm["agent_id"]])
        except BaseException:
            close_subscription()
            raise

        # 若消息在订阅后、读库快照前发布，它同时存在于快照和队列中；快照是正本，
        # 记录其消息游标以便后续丢弃队列里的重叠副本。
        snapshot_message_seq = max((int(message["seq"]) for message in snapshot_messages), default=after_seq)

        async def stream() -> AsyncIterator[str]:
            try:
                for message in snapshot_messages:
                    yield sse_frame({"type": "message", "seq": message["seq"], "message": message})
                for member_id, status in snapshot_statuses.items():
                    yield sse_frame(
                        {"type": "member_status", "seq": after_seq, "member_id": member_id, "status": status}
                    )
                deadline = asyncio.get_running_loop().time() + SSE_STREAM_MAX_SECONDS
                while not shutting_down.is_set():
                    if await request.is_disconnected():
                        return
                    if asyncio.get_running_loop().time() >= deadline:
                        yield "event: rotate\ndata: {}\n\n"
                        return
                    try:
                        event = await asyncio.to_thread(channel.get, True, SSE_POLL_SECONDS)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    if event.get("type") == "message" and int(event.get("seq", 0)) <= snapshot_message_seq:
                        continue
                    # live-only 运行事件没有 seq，不参与游标比较（理由同频道事件流）。
                    if is_replayable(event) and int(event.get("seq", 0)) < after_seq:
                        continue
                    yield sse_frame(event)
            finally:
                close_subscription()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            background=BackgroundTask(close_subscription),
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
        return _not_found(_render_copy("error.round_outcomes_immutable"))

    @app.api_route(
        "/api/channels/{channel_id}/budget",
        methods=["PUT", "POST", "DELETE"],
        include_in_schema=False,
    )
    def no_budget_mutation(channel_id: str) -> JSONResponse:
        """上界只经 ``PATCH`` 写（ADR-0007）；用量只由后端在「人类消息开世代 / 一次跳投递」时推进。"""
        return _not_found(_render_copy("error.budget_read_only"))

    @app.api_route(
        "/api/channels/{channel_id}/members/{member_id}",
        methods=["PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_channel_member_mutation(channel_id: str, member_id: str) -> JSONResponse:
        """本票没有「移出频道」的端点：离开 / 踢出需要单独的语义裁定（是否写 system 事件消息、
        是否影响历史未读与唤醒面），因此人类面明确回 404 而不是留一个沉默的 405。"""
        return _not_found(_render_copy("error.channel_members_append_only"))

    @app.api_route(
        "/api/channels/{channel_id}/messages/{seq}",
        methods=["GET", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_message_mutation(channel_id: str, seq: int) -> JSONResponse:
        """消息 append-only：人类 HTTP 面没有编辑或删除消息的端点。"""
        return _not_found(_render_copy("error.messages_immutable"))

    @app.api_route(
        "/api/attachments/{attachment_id}",
        methods=["PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def no_attachment_mutation(attachment_id: str) -> JSONResponse:
        """附件与消息同口径：上传后不改内容、不改名、不覆盖，也没有删除路径（ADR-0014）。"""
        return _not_found(_render_copy("error.attachments_immutable"))

    @app.api_route(
        "/api/agents/{member_id}",
        methods=["PUT", "DELETE"],
        include_in_schema=False,
    )
    def no_agent_mutation(member_id: str) -> JSONResponse:
        """Agent 是成员行，只能创建与编辑（编辑走上面的 ``PATCH``）：本票没有删除端点。

        ``PUT`` 也不留：整体替换一个配置对象会绕开「部分更新」这条显式的语义（每个字段缺省
        即保持原值，而整体替换里缺省就是清空），两者混在一个路径上迟早会被读错。
        """
        return _not_found(_render_copy("error.agent_delete_unsupported"))

    # ─── 静态资源：工作台 + 只读设计系统 ─────────────────────────────────
    app.mount("/studio", StaticFiles(directory=str(studio_dir()), html=True), name="studio")

    design = design_system_dir()
    if design is not None:
        app.mount("/design", StaticFiles(directory=str(design)), name="design")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/studio/")

    return app
