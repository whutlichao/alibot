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
- ``GET  /api/targets/{id}/plan``             Plan 快照（按 member 分组；运行面板的只读兜底）
- ``GET  /api/agents``                         Agent 成员 + 模型配置 + 四态状态点
- ``POST /api/agents``                         新建 Agent（名称 + 角色 + 模型 + 系统提示）
- ``GET  /api/agents/{id}``                    单个 Agent（含「为什么未启用」的可读原因）
- ``GET  /api/remote-data-flow``               远端数据流告知文案与已确认记录
- ``POST /api/remote-data-flow/ack``           确认一次告知（按模型提供商）
- ``GET  /api/approvals``                      待审批 / 历史审批快照（供运行面板恢复）
- ``POST /api/approvals/{reply_id}``           提交决策（只接受 pending；幂等；部分决策）
- ``GET/POST/DELETE /api/permission-rules``    权限规则的读写（配置动作，不落消息、不唤醒）
- ``GET/POST/PATCH/DELETE /api/mcp-servers``   MCP 声明的读写（只存 env 变量名；配置动作）
- ``POST /api/mcp-servers/{id}/consent``       远端 MCP 的数据面确认（幂等；未确认不连接）
- ``POST /api/docker-images/consent``          远端 Docker 镜像的数据面确认（幂等；未确认不拉取）
- ``GET/POST/PATCH/DELETE /api/skills``       Skill 的读写：本机目录 / 上传 zip 安装，启停与删除（配置动作）

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
import io
import os
import queue
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask
from starlette.responses import Response
from starlette.types import Scope

from .agent_seam import AgentScopeAgentSeam
from .approval import APPROVAL_STATES, DEFAULT_APPROVAL_TIMEOUT_SECONDS, ApprovalBroker
from .db import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_NAME_LENGTH,
    MAX_DOCKER_IMAGE_LENGTH,
    MAX_MESSAGE_BODY_LENGTH,
    OWNER_ID,
    PLAN_RUN_RETENTION,
    SEARCH_RESULT_LIMIT,
    Database,
    DomainError,
    NotFound,
    SearchFacet,
    open_database,
)
from .events import EventBus, is_replayable, sse_frame
from .i18n import locale_of, render
from .mcp import clear_runtime as clear_mcp_runtime
from .mcp import runtime_status as mcp_runtime_status
from .orchestration import ThreadScheduler, WakeOrchestrator
from .reminder_delivery import (
    ReminderDeliverer,
    ReminderDeliveryDriverFactory,
    ReminderDeliveryWorker,
)
from .service import WorkspaceService
from .skills import (
    MAX_ARCHIVE_BYTES,
    SkillValidationError,
    install_directory,
    install_zip,
    resolve_delete_roots,
    resolve_install_root,
    uninstall,
)
from .sop import SOPRunner
from .workspace import (
    clear_docker_failure,
    docker_unavailable_reason,
    shutdown_docker_workspaces,
    start_docker_reaper,
    sweep_stale_docker_workspaces,
)

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


class NoCacheStaticFiles(StaticFiles):
    """静态资源每次回源校验（``Cache-Control: no-cache``）。

    没有这条头时浏览器可以按启发式缓存保留旧的 ``i18n.js``，同时拿到新的 ``index.html``：
    新 HTML 引用的词条键在旧表里不存在，界面就回退成键名本身。``no-cache`` 只禁止**不经校验**
    复用，ETag / Last-Modified 仍然生效——内容未变时回 304，不重复传正文。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
    # Agent 级 workspace 覆盖（D14/D15）；缺省 = 跟随频道。取值由库层整体校验。
    workspace_backend: str | None = None
    permission_mode: str | None = None
    allow_local_shell: bool = False
    docker_image: str | None = None


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
    # Agent 级 workspace 覆盖（D14/D15）：缺省 = 不提；取值由库层整体校验。``docker_image``
    # 的空串是**显式清空**（回到内置镜像），不是「没提到」。
    workspace_backend: str | None = None
    permission_mode: str | None = None
    allow_local_shell: bool | None = None
    docker_image: str | None = None

    @field_validator(
        "display_name",
        "role",
        "model_id",
        "system_prompt",
        "enabled",
        "auto_claim",
        "workspace_backend",
        "permission_mode",
        "allow_local_shell",
        "docker_image",
        mode="before",
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


class DockerConsent(BaseModel):
    """Docker 镜像的数据面确认请求体（``image`` 是完整的镜像引用）。"""

    model_config = ConfigDict(extra="forbid")

    image: str = ""


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


class PermissionRuleCreate(BaseModel):
    """写一条权限规则（配置动作；不落消息、不唤醒、不消耗预算）。"""

    model_config = ConfigDict(extra="forbid")

    scope_kind: str = ""
    scope_id: str = ""
    tool_name: str = ""
    pattern: str = ""
    behavior: str = ""


class MCPServerCreate(BaseModel):
    """新建一条 MCP 声明。

    ``extra="forbid"`` 是安全边界的一部分：正文里出现 ``env`` / ``env_values`` 之类的键会**整体
    拒绝**，于是「把值存进库里」的路径根本不存在——只接受 ``env_names``。
    """

    model_config = ConfigDict(extra="forbid")

    scope_kind: str = ""
    scope_id: str = ""
    name: str = ""
    transport: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env_names: list[str] = Field(default_factory=list)
    enabled: bool = True


class MCPServerUpdate(BaseModel):
    """部分更新；scope 是身份的一部分，不允许改（出现即整体拒绝）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    env_names: list[str] | None = None
    enabled: bool | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("null is not accepted; omit the field instead")
        return value


class SkillCreateLocal(BaseModel):
    """从**本机目录**安装：``source_path`` 是绝对目录；其余键出现即整体拒绝。

    ``channel_id`` 是解析 ``workspace_root`` 的上下文（Agent 级技能按成员分区，但成员可能属于
    多个频道）；频道级安装可省略，此时用 ``scope_id`` 对应的频道。
    """

    model_config = ConfigDict(extra="forbid")

    scope_kind: str = ""
    scope_id: str = ""
    source_path: str = ""
    channel_id: str | None = None


class SkillUpdate(BaseModel):
    """启停；scope 与内容 hash 是身份，不允许改（出现即整体拒绝）。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None

    @field_validator("enabled", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("null is not accepted; omit the field instead")
        return value


class ApprovalDecision(BaseModel):
    """一条工具调用的决策；``rules`` 是可选的后续规则建议（落成 Agent 级规则）。"""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = ""
    confirmed: bool = False
    rules: list[PermissionRuleCreate] = Field(default_factory=list)


class ApprovalSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[ApprovalDecision] = Field(default_factory=list)


class SOPStepCreate(BaseModel):
    """SOP 的一个步骤：executor / 可空 verifier 用 **member id** 引用。"""

    model_config = ConfigDict(extra="forbid")

    subject: str
    description: str = ""
    executor_member_id: str
    verifier_member_id: str | None = None
    max_attempts: int = 3


class SOPCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    steps: list[SOPStepCreate] = Field(min_length=1)


class SOPUpdate(BaseModel):
    """部分更新：启停与编辑；编辑只影响后续 run。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    steps: list[SOPStepCreate] | None = None
    enabled: bool | None = None


class SOPRunCreate(BaseModel):
    """owner 启动一次运行：只指定运行归属 target；leader 恒为 owner，不接受指定。"""

    model_config = ConfigDict(extra="forbid")

    target_id: str


class SOPResume(BaseModel):
    """恢复 ``awaiting`` 的 SOP：送入与审批同形的结构化确认结果。

    ``reply_id`` 省略时使用本进程记录的停车标识；跨重启恢复必须显式给出
    （awaiting 详情是进程内事实，不落库）。
    """

    model_config = ConfigDict(extra="forbid")

    reply_id: str | None = None
    confirm_results: list[dict[str, Any]] = Field(default_factory=list)


def create_app(
    data_dir: Path | str | None = None,
    db: Database | None = None,
    *,
    scheduler: Any | None = None,
    seam: Any | None = None,
    reminder_delivery: ReminderDeliveryDriverFactory | None = None,
    approval_timeout_seconds: float | None = None,
    seam_factory: Callable[[Any], Any] | None = None,
) -> FastAPI:
    """组装后端。

    ``scheduler`` / ``seam`` / ``reminder_delivery`` 是测试注入点：传入手动调度器、脚本化替身与**手动
    触发的到期驱动器**，就能在不联网、零成本、**不等时间**的前提下把行为固定下来，并让测试自己
    决定什么时候跑完一轮、什么时候喂一个时刻给到期投递器。生产默认是线程池 + 真实 AgentScope 适配器
    + 后台轮询线程（启动先扫一次）。

    ``reminder_delivery`` 收的是一个**工厂**（``ReminderDeliverer -> 驱动器``）而不是实例：到期投递器
    需要本函数刚打开的库句柄，而驱动器需要投递器——工厂把这条依赖顺序收在一处。它与 ``scheduler``
    同处一个装配点，**不开第二个**（C8）。

    ``seam_factory`` 是审批测试的注入口：它接收本函数刚建好的 ``ApprovalBroker``，返回成员面实现；
    ``approval_timeout_seconds`` 只用于把默认 10 分钟的审批等待缩短到测试可观察的尺度。
    """
    database = db if db is not None else open_database(data_dir)
    events = EventBus()
    # 审批（#149）：进程内 broker 是等待/唤醒的唯一落点；启动时把上个进程残留的 pending 标
    # expired（D19：跨重启恢复不在首版）。默认等待 10 分钟；测试可注入更短的上界。
    approvals = ApprovalBroker(
        database,
        events,
        timeout_seconds=(
            approval_timeout_seconds
            if approval_timeout_seconds is not None
            else DEFAULT_APPROVAL_TIMEOUT_SECONDS
        ),
        locale_of=lambda: locale_of(database),
    )
    approvals.expire_pending_at_startup()
    # 优雅关停的信号：事件流是**永不自然结束**的响应，如果不主动收，进程会在 graceful shutdown
    # 里等那两个连接直到超时（表现为「进程还在、监听套接字已没、新请求全部被拒」）。
    shutting_down = threading.Event()
    if seam_factory is not None:
        member_seam: Any = seam_factory(approvals)
    elif seam is not None:
        member_seam = seam
    else:
        member_seam = AgentScopeAgentSeam(
            locale_of=lambda: locale_of(database),
            # workspace 装配钩子（#148）：频道显式配置 ``workspace_root`` 后这一轮才挂受限文件工具；
            # 未配置时读路径返回 None，行为逐字不变。
            workspace_of=database.workspace_settings_for_target,
            # 权限档 + 规则（#149）；审批等待由同一个 broker 唤醒（SSE / HTTP 人类面在同一进程）。
            permission_of=database.permission_settings_for_target,
            # MCP 声明（#151）：启用 + 已确认的远端声明由装配层物化为 MCPClient；未配置时为空。
            mcp_of=database.mcp_servers_for_target,
            # Skill 元数据（#152）：启用 + 已安装的技能由装配层读成 AgentScope Skill；未配置时为空。
            skills_of=database.skills_for_target,
            approvals=approvals,
        )
    orchestrator = WakeOrchestrator(
        database,
        member_seam,
        events,
        scheduler if scheduler is not None else ThreadScheduler(),
        # Docker 工作区失败原因（#150）：装配层记录，成员状态点据此变 offline（不回落本地）。
        workspace_unavailable=docker_unavailable_reason,
    )
    # **到期投递器**（ADR-0017 §四）：判定「到点」的唯一 seam。它的产物是 `system` 消息与行上的
    # `fired_at`，与唤醒编排无关——到点**不唤醒**任何人，因此这里只把 `EventBus` 交给它做广播。
    make_driver: ReminderDeliveryDriverFactory = (
        reminder_delivery if reminder_delivery is not None else ReminderDeliveryWorker
    )
    driver = make_driver(ReminderDeliverer(database, events))
    # SOP（#147）：owner 显式启动的多步运行复用同一个调度器；参与者按 member id 经成员面
    # 适配器装配（脚本化替身没有 ``sop_agent`` 时，启动会给出可读的 validation_failed）。
    sop_runner = SOPRunner(
        database,
        events,
        scheduler=orchestrator.scheduler,
        agent_factory=getattr(member_seam, "sop_agent", None),
    )
    service = WorkspaceService(database, orchestrator=orchestrator, events=events, sops=sop_runner)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Plan 保留策略的**启动**那一半（ADR-0023）：每个 (target, member) 只留最近 20 run
        # 或 30 天内的记录；`plan_task` 随 run 级联清理。另一半点在每轮收口后（plan.py）。
        database.prune_plan_runs()
        # SOP 保留策略的启动那一半（ADR-0023 §三）：最近 50 个 run 或 90 天；定义不删。
        database.prune_sop_runs()
        # Docker 工作区（#150）：启动清扫上一进程遗留的容器 + 空闲回收线程。Docker 是可选
        # 后端：daemon 不在时这些步骤安静失败，不影响主服务启动。
        try:
            start_docker_reaper()
            await sweep_stale_docker_workspaces()
        except Exception:
            pass
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
            # 容器清理是 best-effort：SIGKILL 靠下一次启动清扫兜底。
            with suppress(Exception):
                await shutdown_docker_workspaces()
            database.close()

    app = FastAPI(title="Alibot", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db = database
    app.state.service = service
    app.state.events = events
    app.state.orchestrator = orchestrator
    app.state.approvals = approvals
    app.state.sops = sop_runner
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

    # ─── 审批（HITL；人类面，Agent 不经 HTTP） ─────────────────────────────
    @app.get("/api/approvals")
    def list_approvals(
        target_id: str | None = Query(default=None),
        state: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """待审批 / 历史审批列表（供运行面板恢复；``state=pending`` 是最常用读数）。"""
        if state is not None and state not in APPROVAL_STATES:
            raise DomainError(
                "validation_failed",
                _render_copy("error.approval_state_invalid", states=", ".join(APPROVAL_STATES)),
            )
        if target_id is not None:
            database.get_target(target_id)  # 不存在的 target ⇒ 404
        return {"approvals": database.list_approval_requests(target_id=target_id, state=state)}

    @app.post("/api/approvals/{reply_id}")
    def submit_approval(reply_id: str, payload: ApprovalSubmit) -> dict[str, Any]:
        """提交一批决策：只接受 pending；同批重试幂等；相反结论 409。

        决策里的 ``rules`` 会落成该 Agent 的权限规则（配置动作，不落消息、不唤醒）。
        决策回送由 broker 按 ``reply_id`` 唤醒仍在等待的那一轮；已完成 / 已过期的轮次
        不会被重新打开。
        """
        record = approvals.submit(
            reply_id,
            [
                {
                    "tool_call_id": decision.tool_call_id,
                    "confirmed": decision.confirmed,
                    "rules": [
                        {
                            "tool_name": rule.tool_name,
                            "pattern": rule.pattern,
                            "behavior": rule.behavior,
                        }
                        for rule in decision.rules
                    ],
                }
                for decision in payload.decisions
            ],
        )
        return {"approval": record}

    # ─── 权限规则（配置；不落消息、不唤醒、不消耗预算） ────────────────────
    @app.get("/api/permission-rules")
    def list_permission_rules(
        scope_kind: str | None = Query(default=None),
        scope_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if scope_kind is not None and scope_kind not in ("channel", "agent"):
            raise DomainError("validation_failed", _render_copy("error.permission_rule_scope_invalid"))
        return {"permission_rules": database.list_permission_rules(scope_kind=scope_kind, scope_id=scope_id)}

    @app.post("/api/permission-rules", status_code=201)
    def create_permission_rule(payload: PermissionRuleCreate) -> dict[str, Any]:
        rule = database.create_permission_rule(
            scope_kind=payload.scope_kind,
            scope_id=payload.scope_id,
            tool_name=payload.tool_name,
            pattern=payload.pattern,
            behavior=payload.behavior,
        )
        return {"permission_rule": rule}

    @app.delete("/api/permission-rules/{rule_id}")
    def delete_permission_rule(rule_id: str) -> dict[str, Any]:
        rule = database.delete_permission_rule(rule_id)
        return {"permission_rule": rule}

    # ─── MCP 声明（配置；不落消息、不唤醒、不消耗预算） ────────────────────
    @app.get("/api/mcp-servers")
    def list_mcp_servers(
        scope_kind: str | None = Query(default=None),
        scope_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """MCP 声明列表；只回 ``env_names``（变量名），**永不**回 env 值。

        ``runtime`` 是进程内的最近一次装配读数（``connected`` / ``tools`` / ``error``）；从未
        装配过为 ``null``。它是观察面，不落库、不影响配置。
        """
        if scope_kind is not None and scope_kind not in ("channel", "agent"):
            raise DomainError("validation_failed", _render_copy("error.mcp_scope_invalid"))
        servers = database.list_mcp_servers(scope_kind=scope_kind, scope_id=scope_id)
        for server in servers:
            server["runtime"] = mcp_runtime_status(server["id"])
        return {"mcp_servers": servers}

    @app.post("/api/mcp-servers", status_code=201)
    def create_mcp_server(payload: MCPServerCreate) -> dict[str, Any]:
        """写一条 MCP 声明；同名（同 scope）409，不静默覆盖。"""
        server = database.create_mcp_server(
            scope_kind=payload.scope_kind,
            scope_id=payload.scope_id,
            name=payload.name,
            transport=payload.transport,
            command=payload.command,
            args=payload.args,
            url=payload.url,
            env_names=payload.env_names,
            enabled=payload.enabled,
        )
        server["runtime"] = mcp_runtime_status(server["id"])
        return {"mcp_server": server}

    @app.patch("/api/mcp-servers/{server_id}")
    def update_mcp_server(server_id: str, payload: MCPServerUpdate) -> dict[str, Any]:
        """部分更新声明；``scope_kind`` / ``scope_id`` 不可改。整条结果重新校验。"""
        server = database.update_mcp_server(server_id, payload.model_dump(exclude_unset=True))
        # 配置变了，上一轮的连接读数不再代表这条声明；下一轮装配会重新记录。
        clear_mcp_runtime(server["id"])
        server["runtime"] = None
        return {"mcp_server": server}

    @app.delete("/api/mcp-servers/{server_id}")
    def delete_mcp_server(server_id: str) -> dict[str, Any]:
        server = database.delete_mcp_server(server_id)
        clear_mcp_runtime(server_id)
        server["runtime"] = None
        return {"mcp_server": server}

    def _skill_error(exc: SkillValidationError) -> DomainError:
        """把安装器的词条异常翻成统一 HTTP 错误（正文按当前语言偏好渲染）。"""
        message = _render_copy(exc.key, **exc.params)
        fields = {exc.field: message} if exc.field else None
        code = "skill_conflict" if exc.status == 409 else "validation_failed"
        return DomainError(code, message, status=exc.status, fields=fields)

    @app.get("/api/skills")
    def list_skills(
        scope_kind: str | None = None,
        scope_id: str | None = None,
    ) -> dict[str, Any]:
        """Skill 列表（配置只读面；不含字节，只含 hash 与启用态）。"""
        return {"skills": database.list_skills(scope_kind=scope_kind, scope_id=scope_id)}

    @app.post("/api/skills", status_code=201)
    async def install_skill(
        request: Request,
        scope_kind: str = "",
        scope_id: str = "",
        channel_id: str = "",
        filename: str = "",
    ) -> dict[str, Any]:
        """安装一个技能：JSON 正文 = 本机目录；原始 zip 字节 = 上传包。

        两种来源都先校验 ``SKILL.md`` front matter、路径与内容 hash，任何一项不合法都**整体
        拒绝**：不落库、不留半棵树。不是 multipart——与附件上传一致，原始字节走正文，元数据
        走 query，因此后端不需要 multipart 解析依赖。
        """
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        locale = locale_of(database)
        target_scope_kind = scope_kind
        target_scope_id = scope_id
        target_channel_id = channel_id or None
        source_kind = "directory"
        if content_type in ("application/zip", "application/x-zip-compressed", "application/octet-stream"):
            source_kind = "zip"
        elif content_type not in ("", "application/json"):
            raise DomainError(
                "validation_failed",
                _render_copy("error.request_body_invalid"),
                fields={"body": _render_copy("error.request_body_invalid")},
            )

        if source_kind == "directory":
            try:
                payload = SkillCreateLocal.model_validate(await request.json())
            except Exception:
                raise DomainError(
                    "validation_failed",
                    _render_copy("error.request_body_invalid"),
                    fields={"body": _render_copy("error.request_body_invalid")},
                ) from None
            target_scope_kind = payload.scope_kind
            target_scope_id = payload.scope_id
            target_channel_id = payload.channel_id or None
            source_path = payload.source_path
        else:
            body = await request.body()
            if not body:
                raise DomainError(
                    "validation_failed",
                    _render_copy("error.request_body_invalid"),
                    fields={"body": _render_copy("error.request_body_invalid")},
                )
            if len(body) > MAX_ARCHIVE_BYTES:
                message = _render_copy("error.skill_too_large", limit=str(MAX_ARCHIVE_BYTES))
                raise DomainError("validation_failed", message, fields={"source": message})
            source_path = ""

        try:
            root = resolve_install_root(
                database,
                scope_kind=target_scope_kind,
                scope_id=target_scope_id,
                channel_id=target_channel_id,
                locale=locale,
            )
            if source_kind == "zip":
                prepared = install_zip(
                    io.BytesIO(body),
                    filename=filename or "upload.zip",
                    root=root,
                    scope_id=target_scope_id,
                    locale=locale,
                )
            else:
                prepared = install_directory(
                    source_path,
                    root=root,
                    scope_id=target_scope_id,
                    locale=locale,
                )
        except SkillValidationError as error:
            raise _skill_error(error) from None

        try:
            skill = database.create_skill(
                scope_kind=target_scope_kind,
                scope_id=target_scope_id,
                name=prepared["name"],
                description=prepared["description"],
                source_path=prepared["source_path"],
                content_hash=prepared["content_hash"],
            )
        except Exception:
            # 落库失败（冲突 / 校验）与安装一起收口：删掉刚复制的工作区副本，不留半成品。
            with suppress(OSError):
                uninstall(root, scope_id=target_scope_id, name=prepared["name"])
            raise
        return {"skill": skill}

    @app.patch("/api/skills/{skill_id}")
    def update_skill(skill_id: str, payload: SkillUpdate) -> dict[str, Any]:
        """启停一个技能（只改启用态；不落消息、不唤醒、不消耗预算）。"""
        return {"skill": database.update_skill(skill_id, payload.model_dump(exclude_unset=True))}

    @app.delete("/api/skills/{skill_id}")
    def delete_skill(skill_id: str) -> dict[str, Any]:
        """删除配置与工作区副本（不删用户源目录；只影响该 scope）。"""
        current = database.get_skill(skill_id)
        if current is None:
            raise NotFound(_render_copy("error.skill_not_found", skill_id=skill_id))
        roots = resolve_delete_roots(
            database,
            scope_kind=current["scope_kind"],
            scope_id=current["scope_id"],
            channel_id=None,
        )
        deleted = database.delete_skill(skill_id)
        for root in roots:
            with suppress(OSError):
                uninstall(root, scope_id=current["scope_id"], name=current["name"])
        return {"skill": deleted}

    @app.post("/api/mcp-servers/{server_id}/consent")
    def consent_mcp_server(server_id: str) -> dict[str, Any]:
        """远端 MCP 的数据面确认（幂等；重复确认不覆盖首次时间）。

        未确认的 HTTP / SSE 声明在装配层不会建连；这条确认是唯一开关。stdio 是**本机**进程，
        不需要也不接受这条确认（400），避免把「本机」误标成远端。
        """
        server, consent = database.record_mcp_consent(server_id)
        server["runtime"] = mcp_runtime_status(server["id"])
        return {"mcp_server": server, "consent": consent}

    @app.post("/api/docker-images/consent")
    def consent_docker_image(payload: DockerConsent) -> dict[str, Any]:
        """远端 Docker 镜像的数据面确认（幂等；重复确认不覆盖首次时间）。

        这条确认是 ``remote_consent_scope('docker', image)`` 的人类面唯一写路径。未确认的镜像在
        装配层**不拉取**、不启动容器，成员以可读原因 ``offline``；确认后清掉失败短缓存，下一轮
        重新尝试。确认只存时间，不存凭证与连接参数。
        """
        image = payload.image.strip()
        if not image:
            raise DomainError(
                "validation_failed",
                _render_copy("error.remote_scope_id_empty"),
                fields={"image": _render_copy("error.remote_scope_id_empty")},
            )
        if len(image) > MAX_DOCKER_IMAGE_LENGTH:
            raise DomainError(
                "validation_failed",
                _render_copy("error.agent_update_invalid"),
                fields={
                    "image": _render_copy(
                        "error.field.agent_docker_image_too_long", limit=MAX_DOCKER_IMAGE_LENGTH
                    )
                },
            )
        consent = database.record_remote_consent_scope("docker", image)
        clear_docker_failure()
        return {"consent": consent}

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

    # ─── Plan 快照（运行面板的只读兜底；不落消息、不计未读/搜索/预算） ─────
    @app.get("/api/targets/{target_id}/plan")
    def get_plan(
        target_id: str,
        limit: int = Query(default=PLAN_RUN_RETENTION, ge=1, le=100),
    ) -> dict[str, Any]:
        """该 target 的 Plan（按 member 分组，默认最近 20 个 run）。

        这是运行面板的兜底面：``plan_update`` 是 live-only 事件，断线不补；刷新/重启后前端
        重新读这里（ADR-0023）。Plan 不是消息、不是任务，也不属于任何未读/搜索/预算面。
        """
        return {"plan": service.plan(target_id, limit=limit)}

    # ─── SOP（owner 显式启动；人类面唯一入口，成员面没有 SOP 工具） ──────────
    @app.get("/api/sops")
    def list_sops() -> dict[str, Any]:
        """SOP 定义列表（配置；不是运行记录，也不进看板/未读/搜索）。"""
        return {"sops": service.sops()}

    @app.post("/api/sops", status_code=201)
    def create_sop(payload: SOPCreate) -> dict[str, Any]:
        """新建一条 SOP 定义；`steps` 里的 executor/verifier 必须是当前 Agent 成员。"""
        return {
            "sop": service.create_sop(
                name=payload.name,
                description=payload.description,
                steps=[step.model_dump() for step in payload.steps],
            )
        }

    @app.get("/api/sops/{definition_id}")
    def get_sop(definition_id: str) -> dict[str, Any]:
        return {"sop": service.sop(definition_id)}

    @app.patch("/api/sops/{definition_id}")
    def update_sop(definition_id: str, payload: SOPUpdate) -> dict[str, Any]:
        """启停与编辑；编辑只影响后续 run（既有 run 的 ``state`` 不重写）。"""
        changes = payload.model_dump(exclude_unset=True)
        steps = changes.pop("steps", None)
        return {
            "sop": service.update_sop(
                definition_id,
                name=changes.get("name"),
                description=changes.get("description"),
                steps=None if steps is None else [dict(step) for step in steps],
                enabled=changes.get("enabled"),
            )
        }

    @app.post("/api/sops/{definition_id}/runs", status_code=201)
    def start_sop_run(definition_id: str, payload: SOPRunCreate) -> dict[str, Any]:
        """owner 启动一次运行；`leader` 恒为 owner，请求体不接受指定。"""
        return {"run": service.start_sop_run(definition_id, target_id=payload.target_id)}

    @app.get("/api/sop-runs/{run_id}")
    def get_sop_run(run_id: str) -> dict[str, Any]:
        """运行快照：`phase`、逐步 verdict 与当前 `awaiting`（进程内详情，可空）。"""
        return {"run": service.sop_run(run_id)}

    @app.post("/api/sop-runs/{run_id}/resume")
    def resume_sop_run(run_id: str, payload: SOPResume) -> dict[str, Any]:
        """恢复 `awaiting` 的运行：送入人类确认/外部结果（与审批同形的结构化载荷）。"""
        return {
            "run": service.resume_sop_run(
                run_id,
                reply_id=payload.reply_id,
                confirm_results=payload.confirm_results,
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
            workspace_backend=payload.workspace_backend,
            permission_mode=payload.permission_mode,
            allow_local_shell=payload.allow_local_shell,
            docker_image=payload.docker_image,
        )
        return {"agent": agent, "agents": service.agents(), "members": database.list_members()}

    @app.get("/api/agents/{member_id}")
    def get_agent(member_id: str) -> dict[str, Any]:
        return {"agent": service.agent(member_id)}

    @app.patch("/api/agents/{member_id}")
    def update_agent(member_id: str, payload: AgentUpdate) -> dict[str, Any]:
        """编辑 Agent 配置（人类面唯一写路径，ADR-0011）。

        可写 ``display_name`` / ``role`` / ``model_id`` / ``system_prompt`` / ``enabled`` /
        ``auto_claim`` / ``workspace_backend`` / ``permission_mode`` / ``allow_local_shell`` /
        ``docker_image``；任一字段不合法则**整体不写**（不得部分写入）。``model_id`` 沿用与创建同一套
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
            workspace_backend=payload.workspace_backend,
            permission_mode=payload.permission_mode,
            allow_local_shell=payload.allow_local_shell,
            docker_image=payload.docker_image,
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
    app.mount("/studio", NoCacheStaticFiles(directory=str(studio_dir()), html=True), name="studio")

    design = design_system_dir()
    if design is not None:
        app.mount("/design", StaticFiles(directory=str(design)), name="design")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/studio/")

    return app
