"""通向 AgentScope 的窄接缝：协作域只认识这一层的形状，不认识框架。

协作域的唤醒编排需要的全部框架能力被收敛成两个东西：

1. ``AgentSeam``——「一个成员的一轮」这个动作：输入**成员**与该成员**待读的新消息**，
   输出**零或多条待落库回复** + 一个**轮次结果**；轮次结果可带受限的结构化任务动作。
2. ``AgentScopeAgentSeam``——真实实现，进程内直接调用 AgentScope 2.x（``Agent`` /
   ``agentscope.model`` / ``agentscope.credential``），不使用框架的 Agent Service。

为什么必须薄：测试要能在**不联网、零成本**的前提下把「回应 / 忽略 / 失败 / 延迟 / 声明沉默」五种行为
固定下来，所以替身（``tests/scripted.py`` 的 ``ScriptedAgentSeam``）注入的是同一个接口，
而不是去 mock 框架内部。于是协作域的行为测试与「模型是哪一家」完全解耦。

``agentscope`` 只在**真实适配器被实例化时**才 import：没有装它、或只想跑脚本化替身的测试，
不会因为缺依赖而失败。
"""

from __future__ import annotations

import inspect
import logging
import os
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, runtime_checkable

from .i18n import SOURCE_LOCALE, Locale, render
from .runtime import SessionOffloader, runtime_configs, runtime_prompt

if TYPE_CHECKING:
    from .plan import PlanSession
    from .team import TeamRoundContext

_log = logging.getLogger(__name__)

# provider 客户端上「可关闭入口」的候选方法名（协程）。顺序即探测顺序。
_CLOSE_METHOD_NAMES = ("aclose", "close")

# 轮次结果的状态：与规格 §5 的 round outcome status 同一套词。
#
# ``silent``（#58 / ADR-0008）与 ``ignored`` **不是**同一件事，也不是 ``failed`` 的变体：
# - ``ignored``：既有 seam 的正常自判忽略，「这一轮我看了、没什么可说的」（没有结构化声明）；
# - ``silent``：模型经**结构化通道**（:data:`SILENCE_TOOL_NAME` 这个工具）明确声明本轮沉默。
# 把声明塞进 ``reason`` 就只能靠自由文本取证，机器无法稳定区分两种事实——所以它是独立的状态词。
SEAM_STATUSES = ("replied", "ignored", "failed", "silent")

# 「声明沉默」的结构化通道：一个**只记录意图**的工具，落在既有 toolkit 那一层。
#
# 为什么是工具调用而不是别的出口：AgentScope 2.0.10 的另一条结构化出口是
# ``Agent.reply(..., structured_schema=…)``，它经**强制** ``tool_choice``
# （``ToolChoice(mode="generate_structured_output")``，见 ``agentscope/model/_base.py``）实现；
# 而强制工具选择正是各家差异最大的那一档（思考模式下会被拒绝，``OllamaChatModel._format_tools``
# 干脆明写忽略 ``tool_choice.mode``）。普通工具调用只要求「把 schema 送过去」，跨 provider 的形状
# 最一致，且**不做任何正文解析**——声明与正文是两条互不推演的通道。
SILENCE_TOOL_NAME = "declare_silence"


class ReplySignal(StrEnum):
    """本轮的**回应纪律来源**：一个信号回答两件事——是否必答、可读文案该按哪个来源说。

    ``NONE`` 表示没有确定信号：模型按 #57 的 rubric 自判（可以合法沉默）。其余五种都是**确定信号**，
    都必须回应，但**来源不同**，因此提示词与轮次结果的可读文案不能互相挪用——把人类消息写成
    「任务变化」「认领」「自动续跑」或「被个人提及」，都是在写一条没发生过的事实（#189）。

    为什么是枚举，而不是从 ``reason`` 文本反推：同一 Agent 的重复唤醒会被合并、跨 target 的
    deferred 轮次会继承参数，合并后的 ``reason`` 取决于先到者（ADR-0016 五）。纪律与来源必须是
    **进程内**的显式标记。
    """

    NONE = "none"
    MENTION = "mention"
    CLAIM = "claim"
    # 自动续跑（ADR-0027）：受派者上一轮调用 `continue_task` 申请继续推进这张任务，编排落一条
    # `system` 记录后把同一个人再叫醒一轮。它与认领同级（都是确定信号），但来源不同——把续跑轮
    # 写成「认领」会指向一次不存在的认领。声明顺序也是合并优先级：认领高于续跑，人类消息最高。
    CONTINUE = "continue"
    TEAM_TASK_CHANGE = "team-task-change"
    # 人类直接对话（#189）。它**不改**唤醒面、不扣跳、不改未读口径与消息 kind——只把这一轮从
    # 「自判」升成「必答」。
    HUMAN_MESSAGE = "human-message"

    @property
    def mandatory(self) -> bool:
        """这一轮是否必须回应（确定信号）。"""
        return self is not ReplySignal.NONE

    @property
    def copy_prefix(self) -> str | None:
        """该来源那组可读文案的前缀：``reason.<prefix>_silent | _ignored | _no_body | _round``。

        ``NONE`` 与 ``MENTION`` 没有自己那组：没有确定信号时用「被个人提及」那一对；被点名时沿用 #57 以来
        既有的一句（不另加后缀）。元数据只挂在这里一处——加新一类信号时不用再去另一个模块改映射表。
        """
        return _COPY_PREFIX.get(self)

    def merged_with(self, other: ReplySignal) -> ReplySignal:
        """同一成员被重复唤醒时的合并：任一方强制 ⇒ 强制，来源取**声明顺序**较后的那个。

        纪律的推理与既有那几条「取或」合并相同（合并**不削弱**判据）。来源也必须是确定的：
        否则同一对唤醒换个到达顺序就会写出两种诊断。优先级就是枚举的声明顺序
        （``NONE < MENTION < CLAIM < CONTINUE < TEAM_TASK_CHANGE < HUMAN_MESSAGE``），因此不需要第二张表。
        """
        order = list(ReplySignal)
        return other if order.index(other) > order.index(self) else self


# 确定信号 → 那组文案的前缀。人类消息最高（最直接的对话），也是声明顺序的末位。
_COPY_PREFIX: dict[ReplySignal, str] = {
    ReplySignal.CLAIM: "claim",
    ReplySignal.CONTINUE: "continue",
    ReplySignal.TEAM_TASK_CHANGE: "team_change",
    ReplySignal.HUMAN_MESSAGE: "human_message",
}


# provider → 该 provider 需要的凭证环境变量名（可能有多个候选，任一存在即视为有凭证）。
#
# 这张表是「未启用 = 缺凭证」判定的**唯一**落点（一个窄 seam）：换成本机 Ollama 时
# ``ollama`` 不需要任何变量（框架的 ``OllamaCredential`` 只带 host），于是本机模型天然可用。
# 未来要插入别的凭证来源（例如系统钥匙串）时，只需要替换 ``resolve_credential``。
PROVIDER_KEY_ENV: dict[str, tuple[str, ...]] = {
    "dashscope": ("DASHSCOPE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openai-response": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "moonshot": ("MOONSHOT_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "volcengine": ("ARK_API_KEY", "VOLCENGINE_API_KEY"),
    "xai": ("XAI_API_KEY",),
    # 本机模型：不需要凭证（host 有默认值）。
    "ollama": (),
}

# provider → AgentScope 2.0.10 credential 类型的 discriminator 与它的类型白名单。
# 取自 ``agentscope.credential`` 的子类 ``type`` 字段（见 Answer 的版本核对记录）。
PROVIDER_CREDENTIAL_TYPE: dict[str, str] = {
    "dashscope": "dashscope_credential",
    "openai": "openai_credential",
    "openai-response": "openai_credential",
    "anthropic": "anthropic_credential",
    "gemini": "gemini_credential",
    "deepseek": "deepseek_credential",
    "moonshot": "moonshot_credential",
    "minimax": "minimax_credential",
    "volcengine": "volcengine_credential",
    "xai": "xai_credential",
    "ollama": "ollama_credential",
}

PROVIDERS = tuple(PROVIDER_KEY_ENV)


# 能携带工具调用的 provider（结构化沉默通道的**能力闸门**，按 provider 逐个判，不做整体假设）。
#
# 核实方法（取证记录见 ``app/specs/issue-58-acceptance.md``）：读安装的 AgentScope 2.0.9 源码
# ``.venv/lib/python3.11/site-packages/agentscope/model/<provider>/_model.py``——十一家 ChatModel 的
# ``_call_api`` 都声明 ``tools: list[dict] | None``，并各自实现了 ``_format_tools``，即**传输层**都把
# 工具 schema 转发给服务端。因此闸门的取值表今天就是 ``PROVIDER_KEY_ENV`` 的键集（同一份单一事实
# 来源，不另立一张与它恒等的表）。它存在的理由不是「有人是 False」：
#
# 1. **模型层不可静态判定**：chat 的 ``ModelCard`` 只有 input/output 类型，``supports_tools`` 只存在于
#    realtime 的 card；某个具体模型会不会真的产出 tool call 只能由运行时回答。因此本仓**不假定**
#    「所有 provider / 所有模型一致」，判据落在运行时是否真的收到这个工具调用上。
# 2. **退化必须是结构性的**：闸门为 False 时这一轮**不挂**沉默工具（请求里没有 ``tools``），
#    于是模型无法声明 ⇒ 结论只可能来自正文，即既有的 ``replied`` / ``ignored`` / ``failed``。
#    今天没有任何 provider 落在 False（这是逐 provider 判定的结果，不是整体假设）；将来真出现
#    「传输层丢掉工具」的一家，再把能力表从凭证表分出去即可，不为一个还不存在的差异预置第二张表。
def supports_tool_calling(provider: str) -> bool:
    """该 provider 的这一轮能否携带工具调用（结构化沉默通道的前提；未知 provider 一律 False）。"""
    return provider in PROVIDER_KEY_ENV


# 落在本机、不把数据送出本机的 provider：不需要「远端数据流告知」。
LOCAL_PROVIDERS = ("ollama",)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"


def provider_of(model_id: str) -> str:
    """``"dashscope:qwen-max"`` → ``"dashscope"``；没有 provider 前缀时返回空串。"""
    head, separator, _rest = (model_id or "").strip().partition(":")
    return head if separator else ""


def model_name_of(model_id: str) -> str:
    """``"dashscope:qwen-max"`` → ``"qwen-max"``。"""
    _head, separator, rest = (model_id or "").strip().partition(":")
    return rest if separator else ""


def is_remote_provider(provider: str) -> bool:
    return bool(provider) and provider not in LOCAL_PROVIDERS


class CredentialUnavailable(Exception):
    """该 provider 需要的凭证在进程环境里不存在（因此这个成员「未启用」）。

    文案按 ``locale`` 取（ADR-0009 第二层）：这条消息会经 ``enabled_reason`` / ``/status`` 的
    ``reason`` 露给用户，因此它跟着**语言偏好**走，而不是钉在源语言上。
    """

    def __init__(self, provider: str, env_names: tuple[str, ...], *, locale: str | None = None):
        self.provider = provider
        self.env_names = env_names
        # 多个候选环境变量名之间用 " / " 连接：它们是**标识符**，不是自然语言列举，不随语言变。
        joined = " / ".join(env_names) if env_names else render("availability.credential_none", locale)
        super().__init__(render("availability.credential_missing", locale, provider=provider, env=joined))


@dataclass
class RoundMessage:
    """待落库的一条回复。``body`` 是完整正文——本票不做 token 级流式。"""

    body: str


@dataclass(frozen=True)
class TransferTaskAction:
    """Agent 在当前任务轮次中把任务转派给另一位 Agent 的结构化动作。"""

    task_id: str
    assignee_member_id: str


TaskStatus: TypeAlias = Literal["todo", "in_progress", "in_review", "done", "closed"]


@dataclass(frozen=True)
class SetTaskStatusAction:
    """Agent 在当前任务轮次中推进任务状态的结构化动作。"""

    task_id: str
    status: TaskStatus


@dataclass(frozen=True)
class ContinueTaskAction:
    """Agent 在当前任务轮次中**申请自动续跑**的结构化动作（ADR-0027）。

    它不改任务行：受派者用它在轮次收口时声明「这张任务还没干完，请编排再叫我一轮」。
    「再说一次我会继续」写在正文里不是这个动作——正文里的承诺没有执行面。
    """

    task_id: str


AgentTaskAction: TypeAlias = TransferTaskAction | SetTaskStatusAction | ContinueTaskAction


@dataclass(frozen=True)
class TaskAgentOption:
    """任务动作工具可选的 Agent（模型只看到序号与显示名，不接收内部成员 id）。"""

    member_id: str
    display_name: str


@dataclass(frozen=True)
class TaskActionContext:
    """当前任务专用工具的服务端上下文；task id 与成员 id 不由模型提供。

    ``title`` / ``status`` 是**输入侧**的任务本体（ADR-0016 四）：它们进 prompt，让被唤醒的那一轮
    不必从几条第三人称 ``system`` 记录里推断「我在干什么」。输出形状不动（ADR-0010 一）。
    """

    task_id: str
    assignee_member_id: str | None
    agent_members: tuple[TaskAgentOption, ...]
    title: str = ""
    status: str = ""
    # 自动续跑（ADR-0027）是否在这一轮可用：只在 **autonomous 任务线程**打开。team 任务线程的
    # 唤醒面只有队长（ADR-0022），续跑会绕过那条裁决，所以那边显式关掉（工具与提示都不出现）。
    allow_continue: bool = False

    def transfer_options_for(self, actor_id: str) -> tuple[TaskAgentOption, ...]:
        """模型不可见的转派目标正本，顺序由服务端固定，允许显示名重复。"""
        return tuple(option for option in self.agent_members if option.member_id != actor_id)


@dataclass
class RoundOutcome:
    """适配接口输出的轮次结果（协作域据此落 ``round_outcome`` 行）。

    ``task_action`` 是内部类型化值，不含 actor 身份；编排器必须把动作绑定到本轮输入的 Agent，
    不得从消息正文或动作载荷推断执行者。
    """

    status: str
    reason: str = ""
    messages: list[RoundMessage] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    consumed_budget: bool = False
    task_action: AgentTaskAction | None = None
    finished_reason: str | None = None

    def __post_init__(self) -> None:
        if self.finished_reason is None:
            self.finished_reason = "error" if self.status == "failed" else "completed"
        if self.finished_reason not in ("completed", "interrupted", "exceed_max_iters", "error"):
            raise ValueError(f"轮次结束原因非法：{self.finished_reason}")
        if self.status not in SEAM_STATUSES:
            raise ValueError(f"轮次结果状态非法：{self.status}")
        # 「一轮 ↔ 至多一条可落库消息」是契约（spec #1 §5）；越界直接在这里失败，
        # 而不是让落库路径多出一条谁都没预料到的消息。
        if len(self.messages) > 1:
            raise ValueError("一轮最多产出 1 条频道消息")
        # ``silent`` 与**非空** ``messages`` 互斥（#35 D0-d / ADR-0008）：声明沉默就是零条待落库消息。
        # 这条校验放在互斥的**唯一**落点（seam 的输出类型）上，而不是散在落库路径里——编排据此落
        # ``round_outcome(status="silent")``，``reply_seq`` 自然为 NULL。
        if self.status == "silent" and self.messages:
            raise ValueError("silent 轮次不得产出待落库的回复消息")
        if self.task_action is not None and not isinstance(
            self.task_action, (TransferTaskAction, SetTaskStatusAction, ContinueTaskAction)
        ):
            raise ValueError("Agent 任务动作必须使用受支持的结构化类型")


@dataclass(frozen=True)
class AgentEventRecord:
    """一条可直播的框架事件（ADR-0020）：归属完整的白名单副本，**不落库**。

    事件的唯一去处是 SSE 的 ``agent_event`` 帧；它不是业务正本，断线丢失、重启消失。
    ``member_id`` / ``reply_id`` 来自本轮的事件归属映射，``target_id`` 由本轮调用注入
    （channel 或 task，二者对本层没有区别）。
    """

    type: str
    target_id: str
    member_id: str
    reply_id: str
    payload: dict[str, Any]


def member_internal_name(member_id: str) -> str:
    """D10：pipeline 内部名由 **member id** 派生（``m-<id>``），显示名不参与。

    派生名只活在框架内部与事件归属映射里：不落库、不显示、不改成员身份。``display_name``
    在频道里可以重复，唯一稳定键只能是 id。
    """
    return f"m-{member_id}"


def _event_type(event: Any) -> str:
    return str(getattr(event, "type", "") or "")


def _enum_value(value: Any) -> Any:
    """pydantic/Enum 字段转成可 JSON 化的原始值（``str`` / ``bool`` / ``None`` 原样返回）。"""
    return getattr(value, "value", value)


def _data_length(value: str | None) -> int:
    """DataBlock 元数据只保留长度，不透传 base64 / URL 正文。"""
    return len(value or "")


def _hint_value(hint: Any) -> Any:
    """``HintBlock.hint`` 是 str 或内容块列表；数据块只保留元数据。"""
    if not isinstance(hint, list):
        return hint
    parts: list[Any] = []
    for block in hint:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        source = getattr(block, "source", None)
        payload = getattr(source, "data", None) or getattr(source, "url", None) or ""
        parts.append(
            {
                "type": getattr(block, "type", "data"),
                "name": getattr(block, "name", None),
                "length": _data_length(payload if isinstance(payload, str) else ""),
            }
        )
    return parts


def _agent_event_payload(event: Any) -> dict[str, Any] | None:
    """白名单：只返回可直播的结构字段；``None`` 表示该事件不进入事件流。

    ``ReplyStart`` 的 ``name`` 是 D10 派生名——它只用于归属，不放进 payload（不显示）。
    ``session_id`` 是框架内部 id，同样不出去。``DataBlock*`` 与 ``ToolResultDataDelta``
    只保留 media_type / 长度，绝不透传 base64 正文。
    """
    kind = _event_type(event)
    if kind == "REPLY_START":
        return {"role": str(getattr(event, "role", ""))}
    if kind == "REPLY_END":
        payload: dict[str, Any] = {"finished_reason": _enum_value(getattr(event, "finished_reason", ""))}
        error = getattr(event, "error", None)
        if error is not None:
            payload["error"] = {
                "type": _enum_value(getattr(error, "type", "")),
                "message": str(getattr(error, "message", "")),
            }
        return payload
    if kind in ("TEXT_BLOCK_START", "THINKING_BLOCK_START", "DATA_BLOCK_END"):
        return {"block_id": str(getattr(event, "block_id", ""))}
    if kind in ("TEXT_BLOCK_DELTA", "THINKING_BLOCK_DELTA"):
        return {
            "block_id": str(getattr(event, "block_id", "")),
            "delta": str(getattr(event, "delta", "")),
        }
    if kind == "TEXT_BLOCK_END":
        return {
            "block_id": str(getattr(event, "block_id", "")),
            "text": getattr(event, "text", None),
        }
    if kind == "THINKING_BLOCK_END":
        # ``ThinkingBlock*`` 属于规格 §3.2 的白名单；END 带 block_id 与可用的累计文本元数据
        # （AgentScope 2.0.10 的 ``ThinkingBlockEndEvent`` 只有 block_id，故 ``text`` 可能为 None）。
        return {
            "block_id": str(getattr(event, "block_id", "")),
            "text": getattr(event, "text", None),
        }
    if kind == "TOOL_CALL_START":
        return {
            "tool_call_id": str(getattr(event, "tool_call_id", "")),
            "tool_call_name": str(getattr(event, "tool_call_name", "")),
        }
    if kind == "TOOL_CALL_DELTA":
        return {
            "tool_call_id": str(getattr(event, "tool_call_id", "")),
            "delta": str(getattr(event, "delta", "")),
        }
    if kind == "TOOL_CALL_END":
        return {"tool_call_id": str(getattr(event, "tool_call_id", ""))}
    if kind == "TOOL_RESULT_START":
        return {
            "tool_call_id": str(getattr(event, "tool_call_id", "")),
            "tool_call_name": str(getattr(event, "tool_call_name", "")),
        }
    if kind == "TOOL_RESULT_TEXT_DELTA":
        return {
            "tool_call_id": str(getattr(event, "tool_call_id", "")),
            "delta": str(getattr(event, "delta", "")),
        }
    if kind == "TOOL_RESULT_DATA_DELTA":
        return {
            "tool_call_id": str(getattr(event, "tool_call_id", "")),
            "block_id": str(getattr(event, "block_id", "")),
            "media_type": str(getattr(event, "media_type", "")),
            "length": _data_length(getattr(event, "data", None) or getattr(event, "url", None)),
        }
    if kind == "TOOL_RESULT_END":
        return {
            "tool_call_id": str(getattr(event, "tool_call_id", "")),
            "state": _enum_value(getattr(event, "state", "")),
        }
    if kind == "HINT_BLOCK":
        return {
            "block_id": str(getattr(event, "block_id", "")),
            "source": getattr(event, "source", None),
            "hint": _hint_value(getattr(event, "hint", None)),
        }
    if kind == "REQUIRE_USER_CONFIRM":
        return {
            "tool_calls": [
                {
                    "id": str(getattr(call, "id", "")),
                    "name": str(getattr(call, "name", "")),
                    "input": str(getattr(call, "input", "")),
                }
                for call in getattr(event, "tool_calls", [])
            ]
        }
    if kind == "DATA_BLOCK_START":
        payload = {
            "block_id": str(getattr(event, "block_id", "")),
            "media_type": str(getattr(event, "media_type", "")),
        }
        name = getattr(event, "name", None)
        if name:
            payload["name"] = str(name)
        return payload
    if kind == "DATA_BLOCK_DELTA":
        return {
            "block_id": str(getattr(event, "block_id", "")),
            "media_type": str(getattr(event, "media_type", "")),
            "length": _data_length(getattr(event, "data", None) or getattr(event, "url", None)),
        }
    if kind == "CUSTOM":
        return {
            "name": str(getattr(event, "name", "")),
            "value": getattr(event, "value", {}),
        }
    return None


def _agent_event_record(
    event: Any,
    *,
    target_id: str,
    names: Mapping[str, str],
    reply_members: dict[str, str],
) -> AgentEventRecord | None:
    """把一条 ``AgentEvent`` 归属并收窄成 :class:`AgentEventRecord`；查不到就丢弃。

    归属算法（ADR-0020 三）：``ReplyStartEvent.name`` 用 **D10 派生名表**映射成 member id，
    并写入 ``reply_id → member_id``；其余事件只按 ``reply_id`` 查表。没有 ``ReplyStart``
    的孤儿、表里查不到的 ``reply_id``、没有 ``reply_id`` 的事件（``CustomEvent``）一律丢弃并
    记 debug——**不猜**（猜出来的归属会把成员的事件挂到别人名下，比丢事件更糟）。
    """
    kind = _event_type(event)
    reply_id = str(getattr(event, "reply_id", "") or "")
    if not reply_id:
        _log.debug("agent event dropped: %s has no reply_id", kind)
        return None
    if kind == "REPLY_START":
        name = str(getattr(event, "name", "") or "")
        member_id = names.get(name)
        if member_id is None:
            _log.debug("agent event dropped: ReplyStart name %r is not a known member", name)
            return None
        reply_members.setdefault(reply_id, member_id)
    member_id = reply_members.get(reply_id)
    if member_id is None:
        _log.debug("agent event dropped: reply_id %s has no ReplyStart", reply_id)
        return None
    payload = _agent_event_payload(event)
    if payload is None:
        _log.debug("agent event dropped: %s is outside the whitelist", kind)
        return None
    return AgentEventRecord(
        type=kind,
        target_id=target_id,
        member_id=member_id,
        reply_id=reply_id,
        payload=payload,
    )


@runtime_checkable
class AgentSeam(Protocol):
    """成员面：一轮的输入与输出。

    ``seam`` 的调用点是协作域（后端进程内），不是 HTTP——Agent 身份因此是**结构身份**：
    由这个调用点决定，没有可被伪造、转发或读取的自述凭证。
    """

    def run_round(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
        signal: ReplySignal = ReplySignal.NONE,
        direct_message: bool = False,
    ) -> RoundOutcome:
        """跑一轮，返回结论。

        Args:
            agent: 成员行 + ``agent_config``（``id`` / ``display_name`` / ``role`` /
                ``model_id`` / ``system_prompt`` / ``enabled``）。
            messages: 该成员**待读的新消息**（经同一个读接口取得，不含正文的第二通道）。
            mentioned: 这批新消息里是否个人提及了该成员（rubric：被点名必须回应）。
            target_id: 频道 id 或任务 id——这一轮的 target（事件随之带出去）。
            task_context: 仅任务线程提供的内部动作上下文；频道与 DM 不开放任务工具。
            signal: 本轮的**回应纪律来源**（不落库，:class:`ReplySignal`）：非 ``NONE`` 时本轮
                必须回应，且可读文案按这个来源选。它由编排在投递时算好，**不用** ``reason`` 推导——
                重复唤醒会被合并，而合并后的 ``reason`` 取决于先到者。
            direct_message: 这一轮发生在**私聊**（#189）：system prompt 用一对一口径，
                不再给「未被点名可以沉默」的频道边界。
        """
        ...

    def run_round_stream(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
        signal: ReplySignal = ReplySignal.NONE,
        direct_message: bool = False,
        team: TeamRoundContext | None = None,
        emit: Callable[[AgentEventRecord], None] | None = None,
        plan: PlanSession | None = None,
    ) -> RoundOutcome:
        """跑一轮并（可选地）把框架事件按白名单直播出去。

        ``team`` 非空时这是**队长的 team 轮次**：适配器在 ``TeamPipeline`` 里运行队长与成员，
        ``RoundOutcome`` 仍只描述队长（成员结论是分派结果，不是频道轮次）。``emit=None`` 时
        与单 Agent 轮次一样只返回结论。其它参数语义同 :meth:`run_round`。

        ``plan`` 非空时这一轮（team 里则是队长与每个实际被 ``TeamAssign`` 的成员）自动获得
        AgentScope 的 Plan 四工具；每个 Agent 的一次 ``reply_stream`` 对应一个独立 run
        （方向与回写由 :mod:`alibot.plan` 独占）。``plan=None`` 时行为逐字不变。
        """
        ...


def read_api_key(
    provider: str, environ: dict[str, str] | None = None, *, locale: str | None = None
) -> str | None:
    """凭证窄 seam：模型凭证只从**进程环境变量**读（联网搜索凭据是独立的
    ``web_provider_credential``，不走这里）。

    ``locale`` 只决定**异常消息**的语言（ADR-0009 第二层）：消息会经 ``enabled_reason`` 露给用户，
    因此调用点把当前**语言偏好**传下来；不传则用源语言。

    Returns:
        命中的第一个环境变量值；该 provider 无需凭证时返回 ``None``（表示可以用）；
        需要凭证而环境里没有时抛 ``CredentialUnavailable``。
    """
    env = os.environ if environ is None else environ
    names = PROVIDER_KEY_ENV.get(provider)
    if names is None:
        raise CredentialUnavailable(provider, (), locale=locale)
    if not names:
        return None
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    raise CredentialUnavailable(provider, names, locale=locale)


class AgentScopeAgentSeam:
    """真实实现：进程内调用 AgentScope 2.x。

    只用 2.x API（``from agentscope.agent import Agent``、``Agent(name=…, system_prompt=…,
    model=…, toolkit=…)``、``agentscope.model`` + ``agentscope.credential``）。**不**使用
    1.x 的 ``ReActAgent(name, sys_prompt, …)`` 签名，**不**使用框架的 Agent Service。

    一轮 ↔ 一条 ``Msg``：一次 ``reply`` 调用累积成恰好一条 ``Msg``；它的 ``finished_reason`` /
    ``usage`` / ``error`` 只影响本轮的结论，不构成任何消息正本。
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 120.0,
        environ: dict[str, str] | None = None,
        locale_of: Callable[[], Locale] | None = None,
        workspace_of: Callable[[str, str], Any] | None = None,
        permission_of: Callable[[str, str], Any] | None = None,
        mcp_of: Callable[[str, str], Any] | None = None,
        skills_of: Callable[[str, str], Any] | None = None,
        web_of: Callable[[str, str], Any] | None = None,
        approvals: Any | None = None,
        team_run_of: Callable[[str, str], str | None] | None = None,
        usage_sink: Callable[[dict[str, Any]], None] | None = None,
        runtime_root: Any | None = None,
        target_name_of: Callable[[str], str] | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self._environ = environ
        # 这一层产出的文案（轮次 reason、凭证原因）按**语言偏好**渲染，而偏好落在库里：
        # 取值方式由**装配点**注入（``api.create_app`` 传 ``i18n.locale_of(database)``），
        # 于是适配层自己不依赖库、也不读任何请求头（ADR-0009）。不注入时取源语言。
        self._locale_of = locale_of if locale_of is not None else (lambda: SOURCE_LOCALE)
        # workspace 装配钩子（#148）：由装配点注入「(target_id, member_id) → 频道项目根配置」的
        # **读**路径（生产是 ``Database.workspace_settings_for_target``）。适配层仍不依赖库；
        # 不注入时这一轮不挂文件工具——未配置 workspace 的频道行为逐字不变（ADR-0021 §六）。
        self._workspace_of = workspace_of
        # 权限读路径（#149）：档位 + 频道/Agent 规则 + 项目根；不注入时沿用 workspace 配置里的
        # 权限档、没有用户规则（未配置 workspace 的频道没有可授权的工具，行为不变）。
        self._permission_of = permission_of
        # MCP 声明读路径（#151）：生产是 ``Database.mcp_servers_for_target``。不注入时这一轮
        # 不挂 MCP——未配置声明的工作区行为逐字不变。
        self._mcp_of = mcp_of
        # Skill 读路径（#152）：生产是 ``Database.skills_for_target``。只返回**启用**的技能；
        # 字节在频道项目根的 ``.alibot/skills/`` 下，装配层再核一次 hash。不注入时这一轮不挂
        # Skill——未配置技能的工作区行为逐字不变。
        self._skills_of = skills_of
        # 联网配置读路径（#186）：生产是 ``Database.web_settings_for_target``（``web_access`` /
        # 权限档 / 已确认的 web provider）。**不挂在 workspace 上面**——``web_access`` 是独立配
        # 置位，频道没配项目根时它也生效（ADR-0026 §二）。不注入时这一轮不挂联网工具。
        self._web_of = web_of
        # 审批代理（#149）：不注入时遇到 ASK 只能收口为失败（单 Agent 没有人类确认通道）。
        self._approvals = approvals
        # 团队运行的关联 id（#149 的审批记录列）；#142 装配点可注入，未注入时为空。
        self._team_run_of = team_run_of
        # 用量观察口（#55 的测量工具）：一轮的最终 ``Msg.usage`` 原样交给它，**不落库、不进
        # ``RoundOutcome``**。默认 ``None`` 时生产行为逐字不变；它不是接缝的输出形状（ADR-0010 一），
        # 只是与 ``emit`` 同级的进程内观察面。测量工具据它区分「usage 缺失」与「usage=0」——
        # 后者是真实读数，前者不能当零成本。
        self._usage_sink = usage_sink
        self._runtime_root = runtime_root
        self._target_name_of = target_name_of

    def _runtime_options(self, agent: dict[str, Any], target_id: str) -> dict[str, Any]:
        from .workspace import session_partition_id

        options = runtime_configs(agent, self._environ)
        settings = self._workspace_settings(target_id, str(agent["id"]))
        root = settings.root if settings is not None else self._runtime_root
        if root is not None:
            options["offloader"] = SessionOffloader(
                root,
                session_partition_id(str(agent["id"]), target_id),
                docker=settings is not None and settings.is_docker,
            )
        return options

    def _runtime_prompt(self, target_id: str, messages: list[dict[str, Any]]) -> str:
        name = self._target_name_of(target_id) if self._target_name_of is not None else target_id
        return runtime_prompt(name, len(messages))

    async def _runtime_mount(self, settings: Any, member_id: str, target_id: str, warnings: list[str]) -> Any:
        from .workspace import DockerWorkspaceUnavailable, assemble_workspace, session_partition_id

        try:
            return await assemble_workspace(
                settings,
                member_id=member_id,
                session_id=session_partition_id(member_id, target_id),
                locale=self._locale(),
            )
        except DockerWorkspaceUnavailable as error:
            warning = render("seam.workspace_degraded", self._locale(), error=error)
            _log.warning("%s", warning)
            warnings.append(warning)
            return None

    def _permission_settings(self, target_id: str, member_id: str) -> dict[str, Any] | None:
        """一轮的权限配置；没有 workspace 也没有注入读路径时返回 ``None``。"""
        if self._permission_of is not None:
            raw = self._permission_of(target_id, member_id)
            if raw is None:
                return None
            return dict(raw)
        settings = self._workspace_settings(target_id, member_id)
        if settings is None:
            return None
        return {
            "mode": settings.permission_mode,
            "root": settings.root,
            "channel_rules": [],
            "agent_rules": [],
        }

    def _mcp_declarations(self, target_id: str, member_id: str) -> list[dict[str, Any]]:
        """本轮启用的 MCP 声明；未注入读路径时为空。读路径失败只记 debug，不拖垮一轮。"""
        if self._mcp_of is None:
            return []
        try:
            return [dict(item) for item in (self._mcp_of(target_id, member_id) or [])]
        except Exception:  # 配置读不出来等价于没有 MCP；不阻塞其他工具
            _log.debug("mcp declaration lookup failed", exc_info=True)
            return []

    async def _assemble_mcp(self, target_id: str, member_id: str, mount: Any, session_id: str) -> Any:
        """把声明挂到这一轮的工作区；任何失败都只影响 MCP 工具，不改变轮次结论。"""
        from .mcp import MCPAssembly, assemble_mcp_tools

        # Local 后端的 stdio MCP 以该成员的 ``.alibot/agents/<id>`` 为 cwd（声明与状态按成员
        # 分区）；Docker 的 stdio 在容器内由网关启动，cwd 用工作区根（``/workspace``）。
        settings = getattr(mount, "settings", None)
        cwd = str(mount.agent_dir) if settings is not None and settings.is_local else None
        try:
            return await assemble_mcp_tools(
                mount.workspace,
                self._mcp_declarations(target_id, member_id),
                member_id=member_id,
                session_id=session_id,
                environ=self._environ,
                locale=self._locale(),
                cwd=cwd,
            )
        except Exception:  # 装配层自身的意外：没有 MCP 工具，但工作区工具照常
            _log.warning("mcp assembly failed", exc_info=True)
            return MCPAssembly(
                workspace=mount.workspace,
                member_id=member_id,
                session_id=session_id,
            )

    def _skill_rows(self, target_id: str, member_id: str) -> list[dict[str, Any]]:
        """本轮启用的技能元数据；读路径失败只记 debug，不影响其他工具。"""
        if self._skills_of is None:
            return []
        try:
            return [dict(item) for item in (self._skills_of(target_id, member_id) or [])]
        except Exception:  # 配置读不出来等价于没有技能；不阻塞其他工具
            _log.debug("skill lookup failed", exc_info=True)
            return []

    def _skills_for(self, target_id: str, member_id: str, settings: Any) -> list[Any]:
        """把启用技能读成 AgentScope ``Skill``；Docker 后端的 ``dir`` 指向容器内挂载路径。"""
        rows = self._skill_rows(target_id, member_id)
        if not rows or settings is None:
            return []
        from .skills import load_skills
        from .workspace import DOCKER_WORKDIR

        container_root = DOCKER_WORKDIR if settings.is_docker else None
        return load_skills(rows, root=settings.root, container_root=container_root)

    def _team_run_for(self, target_id: str, member_id: str) -> str | None:
        if self._team_run_of is None:
            return None
        try:
            return self._team_run_of(target_id, member_id)
        except Exception:  # 关联信息是附加列，取不到不影响审批本身
            return None

    def _locale(self) -> Locale:
        """当前**语言偏好**（由装配点注入的取值方式给出；默认源语言）。"""
        return self._locale_of()

    def _workspace_settings(self, target_id: str, member_id: str) -> Any | None:
        """本轮的项目根配置；没有装配钩子 / 未配置根时返回 ``None``。

        ``local`` 与 ``docker`` 都原样返回：后端分派在 :func:`assemble_workspace` 里，本模块
        **绝不**把 docker 静默改写成 local（ADR-0021 §二）。
        """
        if self._workspace_of is None:
            return None
        from .workspace import WorkspaceSettings

        raw = self._workspace_of(target_id, member_id)
        if raw is None:
            return None
        return raw if isinstance(raw, WorkspaceSettings) else WorkspaceSettings.from_mapping(raw)

    def _web_settings(self, target_id: str, member_id: str) -> dict[str, Any] | None:
        """联网工具的配置读数（#186）；没有装配钩子 / 读不出来时返回 ``None``。"""
        if self._web_of is None:
            return None
        try:
            raw = self._web_of(target_id, member_id)
        except Exception:  # 配置读不出来等价于没开联网工具；不拖垮一轮
            _log.debug("web settings lookup failed", exc_info=True)
            return None
        return dict(raw) if raw is not None else None

    def _web_tools(self, target_id: str, member_id: str) -> list[Any]:
        """这一轮要挂的联网工具（ADR-0026 §二 / §三 / §四）。

        四条闸门，按顺序判；任何一条不过都返回**空列表**（不装配），且不抛异常——配置问题不该变成
        一轮的失败：

        1. **配置闸门**（§二）：读路径把 ``NULL`` / ``'search'`` / ``'search+fetch'`` 合并成有效档；
           只有 ``'off'`` 会给出空值，于是不挂任何联网工具。
        2. **provider 解析**（修订版解析顺序）：进程环境显式值 → 凭据表默认项 → 已配置项的固定顺序 →
           免凭据 ``exa-mcp``；取值非法 ⇒ 不挂，原因进 debug 日志。模型拿不到 provider 参数。
        3. **数据面确认闸门**（§四）：带凭据 provider 缺 ``remote_consent_scope('web', provider)`` 行 ⇒
           不挂；``exa-mcp`` 免确认。于是**一个请求都不会发出**。host 级粒度仍交给 ``permission_rule``。
        4. **权限档闸门**（§三）：``read-only`` 只挂 ``web_search``；``ask-writes`` / ``trusted-writes``
           才挂 ``fetch``，而 ``fetch`` 的逐次 ASK 由权限引擎（``FetchTool`` 继承的 ASK）给出。

        挂上之后还有一层**容器出网策略**（#187 / §五）：Docker 后端下按 ``docker_egress`` 与
        ``fetch`` 的 allow host 白名单构造 ``EgressPolicy`` 交给两个工具；开关关闭时工具**一个
        请求都不发**，返回说明容器网络策略的可读错误。

        能力闸门（``supports_tool_calling``）**不在**这里：它由 :func:`_round_toolkit` 统一守，于是
        “provider 不能携带工具”与文件工具/沉默工具是同一个落点。
        """
        from .web import (
            FALLBACK_PROVIDER,
            FETCH_ACCESS_LEVEL,
            EgressPolicy,
            ProviderResolutionError,
            fetch_tool,
            resolve_search_provider,
            search_tool,
        )

        settings = self._web_settings(target_id, member_id)
        if settings is None:
            return []
        access = str(settings.get("web_access") or "")
        if not access:
            return []
        credentials = settings.get("credentials") or {}
        try:
            provider = resolve_search_provider(
                self._environ,
                credentials=credentials,
                default_provider=settings.get("default_provider"),
            )
        except ProviderResolutionError as error:
            _log.debug("web tools skipped: %s", error.reason)
            return []
        # 免凭据的 ``exa-mcp`` 不需要确认行：它不发送任何凭据，也不存在「把项目内容交给某个
        # 已配置服务」的额外数据面。带凭据 provider 仍必须有 ``('web', provider)`` 确认行。
        if provider != FALLBACK_PROVIDER and provider not in settings.get("consented_providers", ()):
            _log.debug("web tools skipped: no ('web', %s) consent row", provider)
            return []
        # 容器出网策略（#187 / ADR-0026 §五）：**只有 Docker 后端**才有这一层。两个工具跑在宿主
        # 进程里，容器真的无网挡不住它们，因此「默认无网」必须在这里变成可读拒绝 + 零请求；
        # 本地后端传 ``None``，工具行为与 #186 逐字相同（票面：不改本地后端行为）。
        egress = None
        if str(settings.get("backend") or "local") == "docker":
            egress = EgressPolicy(
                enabled=bool(settings.get("docker_egress")),
                allowed_hosts=frozenset(str(host) for host in settings.get("egress_hosts") or ()),
                locale=self._locale(),
            )
        tools = [
            search_tool(
                provider=provider,
                environ=self._environ,
                credentials=credentials,
                egress=egress,
            )
        ]
        # 未知取值（写路径的 CHECK 拦住了）退化成「只挂只读的搜索」：fail-safe 的方向是多装只读工具，
        # 而不是把需要逐次审批的 fetch 多装上去。
        if access == FETCH_ACCESS_LEVEL and settings.get("mode") != "read-only":
            tools.append(fetch_tool(egress=egress))
        return tools

    # ─── 模型构造 ─────────────────────────────────────────────────────────
    def _build_model(self, provider: str, model_name: str, *, stream: bool = False) -> Any:
        """建这一轮用的模型。

        ``stream`` 只在有事件观察者（``emit``）时为真：没有观察者时保持既有的非流式调用，
        provider 请求形状与 #140 之前逐字相同；有观察者时打开流式，框架才会产出
        ``TextBlockDelta`` / ``ToolCall*`` 这类增量事件。
        """
        from agentscope import credential as cred

        if provider == "ollama":
            ollama = cred.OllamaCredential(host=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
            from agentscope.model import OllamaChatModel

            return OllamaChatModel(credential=ollama, model=model_name, stream=stream)

        api_key = read_api_key(provider, self._environ, locale=self._locale())
        credential_type = PROVIDER_CREDENTIAL_TYPE[provider]
        credential_class = cred.CredentialFactory.get_credential_class(credential_type)
        if credential_class is None:
            raise RuntimeError(
                render(
                    "seam.unknown_credential_type",
                    self._locale(),
                    credential_type=credential_type,
                )
            )
        # 用 model_validate 而不是派生专属构造参数：providers 的凭证形状不一致
        # （``api_key`` / ``host`` / ``api_host``），model_validate 是框架自己的反序列化入口。
        credential = credential_class.model_validate({"api_key": api_key} if api_key else {})
        chat_model_class = credential_class.get_chat_model_class()
        return chat_model_class(
            credential=credential,
            model=model_name,
            parameters=chat_model_class.Parameters(),
            stream=stream,
        )

    # ─── SOP 参与者（#147）：按 member id 装配一个可被 ``SOPStep`` 驱动的 Agent ──
    def sop_agent(self, member: Mapping[str, Any], *, role: str = "executor") -> Any:
        """SOP 的 executor / verifier：只带模型与身份提示，不挂工具（SOP 无工具面）。

        参与者 AgentState 的跨重启恢复不在本票（D19）：每次运行/恢复都按当前成员行重建。
        没有 workspace / 权限上下文是因为这里不执行工具；``SOPStep`` 交给它的是结构化
        handover / verdict，仍由框架的 ``reply_stream`` 收口。
        """
        from agentscope.agent import Agent

        from .sop import sop_agent_prompt

        provider = provider_of(str(member.get("model_id") or ""))
        model = self._build_model(provider, model_name_of(str(member.get("model_id") or "")))
        return Agent(
            name=member_internal_name(str(member["id"])),
            system_prompt=sop_agent_prompt(member, role=role),
            model=model,
        )

    # ─── 一轮 ─────────────────────────────────────────────────────────────
    def run_round(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
        signal: ReplySignal = ReplySignal.NONE,
        direct_message: bool = False,
    ) -> RoundOutcome:
        """兼容壳：``run_round_stream(..., emit=None)``（ADR-0020 二）。"""
        return self.run_round_stream(
            agent,
            messages,
            mentioned=mentioned,
            target_id=target_id,
            task_context=task_context,
            signal=signal,
            direct_message=direct_message,
            emit=None,
        )

    def run_round_stream(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
        signal: ReplySignal = ReplySignal.NONE,
        direct_message: bool = False,
        team: TeamRoundContext | None = None,
        emit: Callable[[AgentEventRecord], None] | None = None,
        plan: PlanSession | None = None,
    ) -> RoundOutcome:
        """流式一轮：消费 ``Agent.reply_stream`` / ``TeamPipeline.reply_stream``。

        有 ``emit`` 时才打开模型流式（否则 provider 请求形状与 ``run_round`` 的既有形态一致）。
        事件只是观察面：回调失败只记 debug，不改写本轮结论；``RoundOutcome`` 仍只由最终 ``Msg``
        与结构化工具记录决定，事件也不落库。

        ``team`` 非空时走队长轮次：同一轮里按 ``TeamContext`` 的成员快照重建 ``TeamPipeline``
        （D11），队长与成员的事件都按 D10 派生名归属后直播；队长最终回复才是本轮的结论。
        """
        if team is not None:
            return self._run_team_round_stream(
                agent,
                messages,
                mentioned=mentioned,
                target_id=target_id,
                task_context=task_context,
                signal=signal,
                direct_message=direct_message,
                team=team,
                emit=emit,
                plan=plan,
            )
        import asyncio

        provider = provider_of(str(agent.get("model_id") or ""))
        model_name = model_name_of(str(agent.get("model_id") or ""))
        # 归属（ADR-0020 三）：D10 派生名 → member id 只用于 ``ReplyStartEvent.name``；
        # 其余事件按 ``reply_id`` 查。表跨整轮存活（事件消费在同一个 loop 内）。
        member_id = str(agent["id"])
        # 联网工具只在这一轮解析**一次**：``_system_prompt`` 要不要自陈它们的风险，与工具集里
        # 真的有没有它们，必须是同一个事实。
        web_tools = self._web_tools(target_id, member_id)
        system_prompt = _system_prompt(
            agent,
            mentioned=mentioned,
            task_context=task_context,
            signal=signal,
            direct_message=direct_message,
            structured_silence=supports_tool_calling(provider),
            with_web=bool(web_tools) and supports_tool_calling(provider),
        ) + self._runtime_prompt(target_id, messages)
        task_actions: list[AgentTaskAction] = []
        # 这一轮模型是否**经结构化通道**声明了沉默（``declare_silence`` 工具被调用过）。
        # 它是一个进程内的事实列表，只在 ``_round`` 的 loop 里被工具线程追加，不落库。
        silence_declarations: list[None] = []
        member_names = {member_internal_name(member_id): member_id}
        reply_members: dict[str, str] = {}
        workspace_warnings: list[str] = []

        async def _round() -> Any:
            # 模型（连同它建出来的 provider 客户端）在**这一轮的 loop 里建、也在这一轮里关**：
            # 两件事都在同一个 ``asyncio.run`` 的协程内完成，中间不允许跨到下一个 loop。
            # 客户端若带着活连接活过这一轮，它的 ``__del__`` 会在之后的某个 loop 里去关一个属于
            # 已关闭 loop 的 socket transport —— 那正是 issue #30 的 ``Event loop is closed``。
            model = self._build_model(provider, model_name, stream=emit is not None)
            mount: Any | None = None
            mcp_assembly: Any | None = None
            try:
                from agentscope.event import (
                    ConfirmResult,
                    RequireUserConfirmEvent,
                    UserConfirmResultEvent,
                    UserInterruptEvent,
                )
                from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
                from agentscope.state import AgentState

                from .approval import ApprovalWaitTimeout
                from .permissions import (
                    WorkspacePathPermissionMiddleware,
                    build_permission_context,
                    effective_rules,
                )
                from .workspace import (
                    local_shell_tool,
                    release_workspace_mount,
                    session_partition_id,
                )

                settings = self._workspace_settings(target_id, member_id)
                permission_settings = self._permission_settings(target_id, member_id)
                permission_mode = str(
                    (permission_settings or {}).get("mode")
                    or (settings.permission_mode if settings is not None else "ask-writes")
                )
                root = (permission_settings or {}).get("root") or (
                    settings.root if settings is not None else None
                )
                context = build_permission_context(
                    permission_mode,
                    effective_rules(
                        (permission_settings or {}).get("channel_rules") or [],
                        (permission_settings or {}).get("agent_rules") or [],
                    ),
                    root=str(root) if root else None,
                )
                if settings is not None:
                    session_id = session_partition_id(member_id, target_id)
                    mount = await self._runtime_mount(settings, member_id, target_id, workspace_warnings)
                    # MCP 声明挂在同一个工作区上：Local 建本地客户端，Docker 走容器内网关；
                    # 单个 server 失败只影响自己的工具（状态可读），不改变本轮结论。
                    if mount is not None:
                        mcp_assembly = await self._assemble_mcp(target_id, member_id, mount, session_id)

                agent_options: dict[str, Any] = {
                    # 框架内部名用 D10 派生名：``display_name`` 可重复，且事件归属只认派生名。
                    "name": member_internal_name(member_id),
                    "system_prompt": "\n".join([system_prompt, *workspace_warnings]),
                    "model": model,
                    "state": AgentState(permission_context=context),
                    **self._runtime_options(agent, target_id),
                }
                workspace_tools = list(mount.tools) if mount is not None else []
                if mcp_assembly is not None:
                    workspace_tools.extend(mcp_assembly.tools)
                # 本地 shell 默认不挂（D15）。显式开启后仍是**每次调用**审批；read-only 下不挂，
                # 因为 EXPLORE 对非只读调用的结论是 DENY（没有“询问”这一档）。
                if (
                    mount is not None
                    and getattr(mount, "allow_local_shell", False)
                    and permission_mode != "read-only"
                ):
                    workspace_tools.append(local_shell_tool(mount.root))
                toolkit = _round_toolkit(
                    provider,
                    task_context=task_context,
                    actor_id=member_id,
                    task_actions=task_actions,
                    silence_declarations=silence_declarations,
                    workspace_tools=workspace_tools or None,
                    web_tools=web_tools,
                    skills=self._skills_for(target_id, member_id, settings),
                    with_plan=plan is not None,
                )
                if toolkit is not None:
                    agent_options["toolkit"] = toolkit
                if root:
                    agent_options["middlewares"] = [WorkspacePathPermissionMiddleware(str(root))]
                instance = _agent_instance(agent_options, plan=plan, member_id=member_id)
                # 一轮的一条 Msg：把待读的新消息合成一次输入，回复累积成恰好一条 Msg。
                # UserMsg 是 2.x 建用户消息的入口（它自己把文本包成 TextBlock）。
                # 自动续跑轮（ADR-0027）会把**整个任务线程**交进来：自己的历史回复必须按
                # assistant 角色回传，否则模型会把上一轮的产出读成「另一个人说的话」。
                inputs: Any = [
                    (
                        Msg(
                            name=_author_name(item),
                            content=[TextBlock(type="text", text=_body(item))],
                            role="assistant",
                        )
                        if str(item["author"]["id"]) == member_id
                        else UserMsg(name=_author_name(item), content=_body(item))
                    )
                    for item in messages
                ] or None
                pending_reply_id: str | None = None

                def _publish(item: Any) -> None:
                    if emit is None:
                        return
                    record = _agent_event_record(
                        item,
                        target_id=target_id,
                        names=member_names,
                        reply_members=reply_members,
                    )
                    if record is None:
                        return
                    try:
                        emit(record)
                    except Exception:  # 直播是观察面：回调失败不能改写这一轮的结论
                        _log.debug("agent event emit failed", exc_info=True)

                async def _consume(stream_inputs: Any, *, register: bool) -> Any:
                    """消费一段流；返回最终 Msg 或 ``None``（挂起时流自然结束）。"""
                    nonlocal pending_reply_id
                    reply: Any = None
                    async for item in instance.reply_stream(stream_inputs, yield_final_msg=True):
                        if isinstance(item, Msg):
                            reply = item
                            continue
                        if register and isinstance(item, RequireUserConfirmEvent):
                            if self._approvals is None:
                                raise RuntimeError(
                                    "AgentScope requires user confirmation but no ApprovalBroker is wired"
                                )
                            self._approvals.register(
                                reply_id=item.reply_id,
                                target_id=target_id,
                                member_id=member_id,
                                tool_calls=item.tool_calls,
                                team_run_id=self._team_run_for(target_id, member_id),
                            )
                            pending_reply_id = item.reply_id
                        _publish(item)
                    # 挂起时 ``yield_final_msg`` 会给出「等待权限确认」的占位 Msg；它不是回复，
                    # 不能当成本轮收口（否则审批还没发生就被记录成 replied）。
                    if reply is not None and instance.state.has_awaiting_tool_calls(instance.name):
                        return None
                    return reply

                while True:
                    # 模型调用 / 工具执行各有 120 秒上界；审批等待在**流结束之后**单独计时
                    # （broker 的 600 秒），因此挂起不会撞上模型超时（#136 研究硬约束 3）。
                    reply = await asyncio.wait_for(
                        _consume(inputs, register=True), timeout=self.timeout_seconds
                    )
                    if reply is not None:
                        return reply
                    if self._approvals is None or pending_reply_id is None:
                        raise RuntimeError("AgentScope ended the round without a final Msg")
                    decisions = await self._approvals.wait(pending_reply_id)
                    if decisions is None:
                        # 超时：broker 已标 expired。AgentScope 没有内建超时，只能用
                        # ``UserInterruptEvent`` 收口（单 Agent 的工具结果转 INTERRUPTED）。
                        with suppress(Exception):
                            await asyncio.wait_for(
                                _consume(
                                    UserInterruptEvent(reply_id=pending_reply_id),
                                    register=False,
                                ),
                                timeout=self.timeout_seconds,
                            )
                        raise ApprovalWaitTimeout(self._approvals.timeout_seconds)
                    if not decisions:
                        # 没有新的可回送决策（例如并发提交已把状态推到终态）：不能再送一次
                        # 已完成 id（框架会抛错），只能收口失败。
                        record = self._approvals.record(pending_reply_id)
                        if record is None or record["state"] == "pending":
                            continue
                        raise RuntimeError(f"approval {pending_reply_id} resolved without new decisions")
                    stored = {
                        str(call.get("id") or ""): call
                        for call in self._approvals.tool_calls(pending_reply_id)
                    }
                    confirm_results = []
                    for decision in decisions:
                        raw = stored.get(str(decision.get("tool_call_id") or ""))
                        if raw is None:
                            raise RuntimeError(
                                f"approval decision references an unknown tool call: {decision!r}"
                            )
                        confirm_results.append(
                            ConfirmResult(
                                confirmed=bool(decision.get("confirmed")),
                                # 建议规则只用于展示；决策回送只需要 id/name/input。库里保存的
                                # suggested_rules 是展示摘要，不按框架模型反序列化。
                                tool_call=ToolCallBlock.model_validate(
                                    {key: value for key, value in raw.items() if key != "suggested_rules"}
                                ),
                            )
                        )
                    inputs = UserConfirmResultEvent(
                        reply_id=pending_reply_id, confirm_results=confirm_results
                    )
            finally:
                # 关闭落在 ``finally``：超时与异常路径同样要收干净（且仍在同一个 loop 内）。
                # MCP 客户端先关：Local 的 workspace.close 也会兜底，Docker 的容器留在池里，
                # transport 绑定在当前 loop 上，不能带到下一轮。
                if mcp_assembly is not None:
                    await mcp_assembly.close()
                if mount is not None:
                    await release_workspace_mount(mount)
                await _aclose_model_client(model)

        try:
            reply = asyncio.run(_round())
        except TimeoutError:
            # 失败轮同样可能是**已计费**的一次调用：没有最终 Msg 就没有 usage，必须发一条
            # usage=None 的记录；测量工具据此停跑，而不是把账目当完整。
            self._record_usage(None, agent, target_id)
            return RoundOutcome(
                status="failed",
                reason=render("seam.timeout", self._locale(), seconds=f"{self.timeout_seconds:.0f}"),
            )
        except Exception as error:  # 框架 / 网络 / 凭证的任何失败都收口成一轮 failed
            from .approval import ApprovalWaitTimeout
            from .workspace import DockerWorkspaceUnavailable

            self._record_usage(None, agent, target_id)
            if isinstance(error, ApprovalWaitTimeout):
                return RoundOutcome(
                    status="failed",
                    reason=render("seam.approval_timeout", self._locale(), seconds=f"{error.seconds:.0f}"),
                )
            if isinstance(error, DockerWorkspaceUnavailable):
                return RoundOutcome(status="failed", reason=str(error))
            return RoundOutcome(
                status="failed",
                reason=render(
                    "seam.call_failed",
                    self._locale(),
                    type=type(error).__name__,
                    error=error,
                ),
            )

        self._record_usage(reply, agent, target_id)
        outcome = self._outcome_from_reply(reply, task_actions, silence_declarations)
        outcome.reason = "\n".join([outcome.reason, *workspace_warnings])
        return outcome

    def _run_team_round_stream(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None,
        signal: ReplySignal,
        direct_message: bool,
        team: TeamRoundContext,
        emit: Callable[[AgentEventRecord], None] | None,
        plan: PlanSession | None = None,
    ) -> RoundOutcome:
        """队长轮次：按这一轮的成员快照重建 ``TeamPipeline`` 并消费它的事件流。

        - **D11**：pipeline 在每次调用里重建，成员来自 ``team.members``（编排每轮从库里读）；
          进程不缓存成员表。
        - **D10**：队长与成员都用 ``m-<member id>`` 作为框架内部名；``display_name`` 只进工具
          描述，因此重名不会撞名。事件归属表同时含队长与成员。
        - **D2**：只有队长的最终回复进入本轮的 ``RoundOutcome``；成员结论是队长看到的
          ``ToolResult``，不落 target 消息。
        """
        import asyncio

        from .team import build_team_pipeline, member_internal_name

        provider = provider_of(str(agent.get("model_id") or ""))
        system_prompt = _system_prompt(
            agent,
            mentioned=mentioned,
            task_context=task_context,
            signal=signal,
            direct_message=direct_message,
            structured_silence=supports_tool_calling(provider),
            leader=True,
        )
        task_actions: list[AgentTaskAction] = []
        silence_declarations: list[None] = []
        member_id = str(agent["id"])
        names = dict(team.name_to_member)
        names.setdefault(member_internal_name(member_id), member_id)
        reply_members: dict[str, str] = {}
        workspace_warnings: list[str] = []

        async def _round() -> Any:
            from agentscope.event import (
                ConfirmResult,
                RequireUserConfirmEvent,
                UserConfirmResultEvent,
                UserInterruptEvent,
            )
            from agentscope.message import ToolCallBlock, UserMsg
            from agentscope.state import AgentState

            from .approval import ApprovalWaitTimeout
            from .permissions import (
                WorkspacePathPermissionMiddleware,
                build_permission_context,
                effective_rules,
            )
            from .workspace import (
                local_shell_tool,
                release_workspace_mount,
                session_partition_id,
            )

            models: list[Any] = []
            mounts: list[Any] = []
            mcp_assemblies: list[Any] = []
            agents_by_id: dict[str, Any] = {}

            def _permission_for(member_id_value: str) -> tuple[Any, str | None]:
                settings = self._workspace_settings(target_id, member_id_value)
                permission_settings = self._permission_settings(target_id, member_id_value)
                mode = str(
                    (permission_settings or {}).get("mode")
                    or (settings.permission_mode if settings is not None else "ask-writes")
                )
                root = (permission_settings or {}).get("root") or (
                    settings.root if settings is not None else None
                )
                context = build_permission_context(
                    mode,
                    effective_rules(
                        (permission_settings or {}).get("channel_rules") or [],
                        (permission_settings or {}).get("agent_rules") or [],
                    ),
                    root=str(root) if root else None,
                )
                return context, str(root) if root else None

            async def _workspace_tools_for(member_id_value: str) -> list[Any] | None:
                """这一轮该成员的项目根工具（未配置 / 非 local 后端时没有；ADR-0021 §六）。"""
                settings = self._workspace_settings(target_id, member_id_value)
                if settings is None:
                    return None
                session_id = session_partition_id(member_id_value, target_id)
                mount = await self._runtime_mount(settings, member_id_value, target_id, workspace_warnings)
                if mount is None:
                    return None
                mounts.append(mount)
                tools = list(mount.tools)
                assembly = await self._assemble_mcp(target_id, member_id_value, mount, session_id)
                mcp_assemblies.append(assembly)
                tools.extend(assembly.tools)
                permission_settings = self._permission_settings(target_id, member_id_value)
                mode = str(
                    (permission_settings or {}).get("mode")
                    or (settings.permission_mode if settings is not None else "ask-writes")
                )
                if getattr(mount, "allow_local_shell", False) and mode != "read-only":
                    tools.append(local_shell_tool(mount.root))
                return tools

            async def _skills_for_member(member_id_value: str) -> list[Any]:
                settings = self._workspace_settings(target_id, member_id_value)
                if settings is None:
                    return []
                return self._skills_for(target_id, member_id_value, settings)

            def _build_agent(
                member: dict[str, Any],
                prompt: str,
                *,
                toolkit: Any = None,
                stream: bool = False,
            ) -> Any:
                member_provider = provider_of(str(member.get("model_id") or ""))
                member_model = model_name_of(str(member.get("model_id") or ""))
                model = self._build_model(member_provider, member_model, stream=stream)
                models.append(model)
                context, root = _permission_for(str(member["id"]))
                options: dict[str, Any] = {
                    "name": member_internal_name(str(member["id"])),
                    "system_prompt": "\n".join(
                        [prompt + self._runtime_prompt(target_id, messages), *workspace_warnings]
                    ),
                    "model": model,
                    "state": AgentState(permission_context=context),
                    **self._runtime_options(member, target_id),
                }
                if root:
                    options["middlewares"] = [WorkspacePathPermissionMiddleware(root)]
                if toolkit is not None:
                    options["toolkit"] = toolkit
                instance = _agent_instance(options, plan=plan, member_id=str(member["id"]))
                agents_by_id[str(member["id"])] = instance
                return instance

            try:
                leader_web_tools = self._web_tools(target_id, member_id)
                leader_toolkit = _round_toolkit(
                    provider,
                    task_context=task_context,
                    actor_id=member_id,
                    task_actions=task_actions,
                    silence_declarations=silence_declarations,
                    workspace_tools=await _workspace_tools_for(member_id),
                    web_tools=leader_web_tools,
                    skills=await _skills_for_member(member_id),
                    with_plan=plan is not None,
                )
                leader_agent = _build_agent(
                    agent,
                    system_prompt,
                    toolkit=leader_toolkit,
                    stream=emit is not None,
                )
                member_agents: list[tuple[dict[str, Any], Any]] = []
                for member in team.members:
                    member_id_value = str(member["id"])
                    member_provider = provider_of(str(member.get("model_id") or ""))
                    workspace_tools = await _workspace_tools_for(member_id_value)
                    member_skills = await _skills_for_member(member_id_value)
                    # 联网配置按**该成员自己**的 member id 读（Agent 级覆盖），不拿队长的。
                    member_web_tools = self._web_tools(target_id, member_id_value)
                    member_agents.append(
                        (
                            member,
                            _build_agent(
                                member,
                                # 提示里提到联网工具就当且仅当它们真的在手上（能力闸门在同一处判）。
                                _team_member_prompt(
                                    member,
                                    with_web=bool(member_web_tools)
                                    and supports_tool_calling(member_provider),
                                ),
                                # 成员没有任务上下文（规格 §3.5）；队长负责把任务写清楚。
                                # 配置了项目根时挂受限文件工具；Plan 轮次则始终有自己的四工具。
                                toolkit=(
                                    _round_toolkit(
                                        member_provider,
                                        task_context=None,
                                        actor_id=member_id_value,
                                        task_actions=[],
                                        silence_declarations=[],
                                        workspace_tools=workspace_tools,
                                        web_tools=member_web_tools,
                                        skills=member_skills,
                                        with_plan=plan is not None,
                                    )
                                    if workspace_tools
                                    or member_skills
                                    or member_web_tools
                                    or plan is not None
                                    else None
                                ),
                            ),
                        )
                    )
                pipeline = build_team_pipeline(
                    leader_agent=leader_agent,
                    leader_id=member_id,
                    members=member_agents,
                    target_id=target_id,
                    task_id=team.task_id,
                    team_run_id=team.team_run_id,
                    budget=team.budget,
                    publish=team.publish,
                )
                inputs: Any = [
                    UserMsg(name=_author_name(item), content=_body(item)) for item in messages
                ] or None
                parked: dict[str, str] = {}
                current_reply_id: str | None = None

                def _member_of(reply_id: str) -> str | None:
                    for member_id_value, instance in agents_by_id.items():
                        if str(getattr(instance.state, "reply_id", "")) == reply_id:
                            return member_id_value
                    return reply_members.get(reply_id)

                def _any_awaiting() -> bool:
                    return any(
                        instance.state.has_awaiting_tool_calls(instance.name)
                        for instance in agents_by_id.values()
                    )

                async def _consume(stream_inputs: Any, *, register: bool) -> Any:
                    nonlocal current_reply_id
                    async for item in pipeline.reply_stream(stream_inputs):
                        if register and isinstance(item, RequireUserConfirmEvent):
                            if self._approvals is None:
                                raise RuntimeError(
                                    "AgentScope requires user confirmation but no ApprovalBroker is wired"
                                )
                            participant = _member_of(item.reply_id)
                            if participant is None:
                                raise RuntimeError(
                                    f"no participant matches approval reply_id {item.reply_id}"
                                )
                            self._approvals.register(
                                reply_id=item.reply_id,
                                target_id=target_id,
                                member_id=participant,
                                tool_calls=item.tool_calls,
                                # 这一轮 team_run 是 pipeline/context 的正本：审批行直接挂上它，
                                # 不依赖生产装配是否接到 ``team_run_of``（单 Agent 轮仍走那个口）。
                                team_run_id=team.team_run_id,
                            )
                            parked[item.reply_id] = participant
                            current_reply_id = item.reply_id
                        if emit is None:
                            continue
                        record = _agent_event_record(
                            item,
                            target_id=target_id,
                            names=names,
                            reply_members=reply_members,
                        )
                        if record is None:
                            continue
                        if record.type == "REPLY_START":
                            # 分派 SSE 的 reply_id 归属（#144）：成员按调用顺序串行运行，
                            # ReplyStart 的到达顺序即该成员的分派顺序。
                            pipeline.note_member_reply(record.member_id, record.reply_id)
                        try:
                            emit(record)
                        except Exception:  # 直播是观察面：回调失败不能改写这一轮的结论
                            _log.debug("agent event emit failed", exc_info=True)
                    reply = _final_reply_message(leader_agent)
                    if reply is not None and _any_awaiting():
                        return None  # 挂起占位 Msg 不是本轮结论
                    return reply

                while True:
                    reply = await asyncio.wait_for(
                        _consume(inputs, register=True), timeout=self.timeout_seconds
                    )
                    if reply is not None and not _any_awaiting():
                        return reply
                    if self._approvals is None or not parked:
                        raise RuntimeError("TeamPipeline ended the round without a final reply")
                    reply_id = current_reply_id
                    if reply_id is None or reply_id not in parked:
                        reply_id = next(iter(parked))
                        current_reply_id = reply_id
                    decisions = await self._approvals.wait(reply_id)
                    if decisions is None:
                        with suppress(Exception):
                            await asyncio.wait_for(
                                _consume(UserInterruptEvent(reply_id=reply_id), register=False),
                                timeout=self.timeout_seconds,
                            )
                        raise ApprovalWaitTimeout(self._approvals.timeout_seconds)
                    if not decisions:
                        # broker 只会在终态且没有未回送决策时返回空批：此时再送一次确认会
                        # 打穿 Agent（框架会抛“not waiting for confirmation”），只能收口失败。
                        raise RuntimeError(f"approval {reply_id} resolved without new decisions")
                    stored = {
                        str(call.get("id") or ""): call for call in self._approvals.tool_calls(reply_id)
                    }
                    confirm_results = []
                    for decision in decisions:
                        raw = stored.get(str(decision.get("tool_call_id") or ""))
                        if raw is None:
                            raise RuntimeError(
                                f"approval decision references an unknown tool call: {decision!r}"
                            )
                        confirm_results.append(
                            ConfirmResult(
                                confirmed=bool(decision.get("confirmed")),
                                tool_call=ToolCallBlock.model_validate(
                                    {key: value for key, value in raw.items() if key != "suggested_rules"}
                                ),
                            )
                        )
                    record = self._approvals.record(reply_id)
                    if record is not None and record["state"] != "pending":
                        parked.pop(reply_id, None)
                        current_reply_id = None
                    inputs = UserConfirmResultEvent(reply_id=reply_id, confirm_results=confirm_results)
            finally:
                # 后装配的先关（anyio cancel scope 按连接顺序嵌套）。
                for assembly in reversed(mcp_assemblies):
                    await assembly.close()
                for mount in mounts:
                    await release_workspace_mount(mount)
                for model in models:
                    await _aclose_model_client(model)

        try:
            reply = asyncio.run(_round())
        except TimeoutError:
            self._record_usage(None, agent, target_id)
            return RoundOutcome(
                status="failed",
                reason=render("seam.timeout", self._locale(), seconds=f"{self.timeout_seconds:.0f}"),
            )
        except Exception as error:  # 框架 / 网络 / 凭证的任何失败都收口成一轮 failed
            from .workspace import DockerWorkspaceUnavailable

            self._record_usage(None, agent, target_id)
            if isinstance(error, DockerWorkspaceUnavailable):
                return RoundOutcome(status="failed", reason=str(error))
            return RoundOutcome(
                status="failed",
                reason=render(
                    "seam.call_failed",
                    self._locale(),
                    type=type(error).__name__,
                    error=error,
                ),
            )
        if reply is None:
            self._record_usage(None, agent, target_id)
            return RoundOutcome(
                status="failed",
                reason=render("seam.team_no_leader_reply", self._locale()),
            )
        self._record_usage(reply, agent, target_id)
        outcome = self._outcome_from_reply(reply, task_actions, silence_declarations)
        outcome.reason = "\n".join([outcome.reason, *workspace_warnings])
        return outcome

    def _record_usage(self, reply: Any, agent: Mapping[str, Any], target_id: str) -> None:
        """把最终 ``Msg.usage`` 交给观察口；不落库、不改本轮结论。

        ``usage is None`` 时仍然发一条记录，但四个 token 字段是 ``None``——测量工具必须能把
        「provider 没给 usage」与「usage 真的是 0」分开，后者才可能是零成本证据。
        """
        sink = self._usage_sink
        if sink is None:
            return
        usage = getattr(reply, "usage", None)
        record = {
            "member_id": str(agent.get("id") or ""),
            "target_id": target_id,
            "model_id": str(agent.get("model_id") or ""),
            "input_tokens": None if usage is None else int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": None if usage is None else int(getattr(usage, "output_tokens", 0) or 0),
            "cache_input_tokens": (
                None if usage is None else int(getattr(usage, "cache_input_tokens", 0) or 0)
            ),
            "cache_creation_input_tokens": (
                None if usage is None else int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            ),
            "finished_reason": _enum_value(getattr(reply, "finished_reason", "")),
            "error": bool(getattr(reply, "error", None)),
        }
        try:
            sink(record)
        except Exception:  # 观察面失败不能改写一轮的结论（与 ``emit`` 同一条纪律）
            _log.debug("usage sink failed", exc_info=True)

    def _outcome_from_reply(
        self,
        reply: Any,
        task_actions: list[AgentTaskAction],
        silence_declarations: list[None],
    ) -> RoundOutcome:
        """把最终 ``Msg`` 与结构化工具记录收口成一轮的结论（单 Agent / 队长共用）。"""
        reply_error = getattr(reply, "error", None)
        if reply_error:
            return RoundOutcome(
                status="failed",
                reason=render("seam.reply_error", self._locale(), error=reply_error),
            )

        finished_reason = str(getattr(reply, "finished_reason", None) or "completed")
        if finished_reason == "interrupted":
            return RoundOutcome(
                status="replied",
                reason=render("seam.interrupted", self._locale()),
                finished_reason=finished_reason,
            )
        if finished_reason == "exceed_max_iters":
            text = _msg_text(reply)
            return RoundOutcome(
                status="replied",
                reason=render("seam.exceed_max_iters", self._locale()),
                messages=[RoundMessage(body=text)] if text.strip() else [],
                task_action=task_actions[0] if task_actions else None,
                finished_reason=finished_reason,
            )

        task_action = task_actions[0] if task_actions else None
        text = _msg_text(reply)
        # 结构化声明**优先于**正文（D0-d：silent ↔ 非空 messages 互斥）：模型既调了工具又写了一段
        # 「好的，本轮我不说话」，那段仍是正文，不能作为回复落库。声明与正文互不推演——这里只读工具
        # 记录，不从文本里认任何措辞。
        if silence_declarations:
            if task_action is not None:
                # 既声明沉默又提交任务动作：动作是本轮真正发生的事，按既有的「动作-only 算回应」收口
                # （否则会静默丢掉一个已经校验过的结构化动作）。reason 把两件事都说清楚。
                return RoundOutcome(
                    status="replied",
                    reason=render("seam.silence_with_action", self._locale()),
                    task_action=task_action,
                )
            return RoundOutcome(status="silent", reason=render("seam.silent", self._locale()))
        if not text.strip():
            if task_action is not None:
                return RoundOutcome(
                    status="replied",
                    reason=render("seam.task_action_only", self._locale()),
                    task_action=task_action,
                )
            return RoundOutcome(status="ignored", reason=render("seam.no_body", self._locale()))
        return RoundOutcome(
            status="replied",
            reason=render("seam.replied", self._locale()),
            messages=[RoundMessage(body=text)],
            task_action=task_action,
        )


def _web_prompt_note() -> str:
    """联网工具的边界句（#186 / ADR-0026 §一）：只在真正挂了联网工具时进 system prompt。

    与 ``AskEveryTimeBash`` 在工具 description 里自陈风险是同一做法：模型要能从提示里知道这两个工具
    的数据面差别（``web_search`` 只送查询词，``fetch`` 把 URL 送出本机）。
    """
    return (
        "你本轮配有联网工具：web_search 只送出查询词与条数；fetch 会把目标 URL 送出本机，"
        "可能把内网地址或带凭据的地址暴露给第三方，因此每次调用都要人类逐次审批"
        "（按 host 的权限规则可放行）。不要用它们访问本机或内网地址。"
    )


def _team_member_prompt(member: Mapping[str, Any], *, with_web: bool = False) -> str:
    """被队长分派的成员这一轮的 system prompt（没有频道 rubric，任务描述就是全部输入）。"""
    parts: list[str] = []
    base = str(member.get("system_prompt") or "").strip()
    if base:
        parts.append(base)
    role = str(member.get("role") or "").strip()
    if role:
        parts.append(f"你的角色：{role}")
    parts.append(
        "你是 team 模式频道里的成员。队长会把一项具体任务分派给你；你看不到频道上下文，"
        "任务描述就是全部输入。请直接完成并在正文里给出结果。"
    )
    if with_web:
        parts.append(_web_prompt_note())
    return "\n\n".join(parts)


def _final_reply_message(agent: Any) -> Any:
    """队长这一轮的最终回复：``TeamPipeline`` 不返回 ``Msg``，它留在 Agent 的上下文尾部。"""
    context = getattr(getattr(agent, "state", None), "context", None) or []
    for message in reversed(context):
        if getattr(message, "role", "") == "assistant" and getattr(message, "name", "") == agent.name:
            return message
    return None


def _client_closer(client: Any) -> Any:
    """从 provider 客户端上取「关闭入口」；取不到返回 ``None``（安静跳过）。

    provider 客户端的关闭入口不统一：``openai.AsyncClient`` / ``anthropic.AsyncAnthropic`` /
    ``ollama.AsyncClient`` 是协程 ``close()``；``google.genai.Client`` 的异步入口在 ``aio.aclose()``
    （它的同步 ``close()`` 管不到异步客户端）。所以这里用鸭子类型探测，而不是 instanceof 一串类型。

    只接受**协程函数**是本函数唯一的判定规则：正是这条规则让 ``google.genai.Client`` 跳过它那个
    同步的 ``close()``、落到 ``aio.aclose()`` 上。于是 ``_aclose_model_client`` 不再需要二次判定。
    """
    candidates = [getattr(client, name, None) for name in _CLOSE_METHOD_NAMES]
    aio = getattr(client, "aio", None)
    candidates.append(getattr(aio, "aclose", None))
    for candidate in candidates:
        if callable(candidate) and inspect.iscoroutinefunction(candidate):
            return candidate
    return None


async def _aclose_model_client(model: Any) -> None:
    """在**当前** event loop 内关掉 ``model`` 持有的 provider 客户端。

    为什么必须显式关：``run_round`` 每轮用 ``asyncio.run()`` 新建并随即关闭一个 loop，而 provider
    客户端若带着 keep-alive 连接活过这一轮，它的 ``__del__`` 会在**之后的**某个 loop 里
    ``create_task(self.aclose())``，去关一个属于已关闭 loop 的 socket transport —— 那正是
    issue #30 的 ``RuntimeError: Event loop is closed``。

    取不到可关闭入口（或关闭本身失败）时**安静跳过**：一轮的成败由模型调用决定，收尾不该把一个
    已经成功的一轮变成 failed。取值入口只由 ``_client_closer`` 一处判定（只接受协程函数），
    这里只管调用与忍错。
    """
    try:
        client = getattr(model, "client", None)
        closer = _client_closer(client) if client is not None else None
        if closer is None:
            return
        await closer()
    except Exception:  # 收尾是尽力而为：理由见 docstring
        return


# #57 / D5 那条 rubric 的**尾段**：沉默是什么，以及它的反面（括注 / 旁白 / 说明 / 道歉都仍是正文）。
# 频道与私聊共用它——「沉默的含义」不因会话形状而变，变的只是「什么时候允许沉默」。
_SILENCE_RUBRIC_TAIL = (
    "沉默指的是这一轮不产出正文，什么都不写，也不用括注、旁白、说明或道歉去交代"
    "「本轮消息与我无关」：那仍然是一条正文，属于说了等于没说；"
    "其余情况有话要说就直接写成正文，不要把要说的话整体包在括注里。"
)

# #57 / D5 的定稿 rubric（channel 轮）：**沉默 = 这一轮不产出正文**。这段文本**逐字不变**——
# `test_the_silence_rubric_*` 以它为判据，而它刻意恰好是**一段**（只写一半，模型就没有依据判断
# 这一轮该不该说话）。人类消息的必答句（#189）因此是**另起一段**，不并进这里。
_CHANNEL_RUBRIC = (
    "回应纪律：被个人提及（@你的名字）时必须回应，而且正文要有实质内容；"
    "话题明确与你无关、或你没有被点名时，正常的做法是保持沉默——" + _SILENCE_RUBRIC_TAIL
)

# 私聊的 rubric（#189）：一对一没有「频道里没点我」这回事，因此不再给「未被点名可以沉默」的边界——
# 把它搬进私聊就是给模型一个合法的沉默出口（issue #189 的机制定位）。尾段与频道共用（见上）。
_DIRECT_MESSAGE_RUBRIC = (
    "你是这个私聊里唯一的 Agent，正在和人类一对一对话：这里没有其他协作者，"
    "收到的每一句都来自人类本人。回应纪律：人类对你说话时必须回应，而且正文要有实质内容；"
    + _SILENCE_RUBRIC_TAIL
)


def _system_prompt(
    agent: dict[str, Any],
    *,
    mentioned: bool,
    task_context: TaskActionContext | None = None,
    signal: ReplySignal = ReplySignal.NONE,
    direct_message: bool = False,
    structured_silence: bool = False,
    leader: bool = False,
    with_web: bool = False,
) -> str:
    """把成员配置里的系统提示 + 本轮的回应 rubric + 任务上下文合成一次调用的 system prompt。

    ``leader`` 是**队长轮次**的标记（#179 C / ADR-0025 §四）：它只换**身份句**、另加一段
    ``TeamAssign`` 指引，并在必答时用队长口径的「必须回应」句。它不是一种新的回应纪律：
    ``signal`` 仍是唯一的纪律标记，只是队长的措辞不能写成「认领」——team 频道不认领。

    ``signal`` 是**纪律来源**的显式枚举（ADR-0016 五 / #189）：是否必答、以及该用哪个来源的措辞，
    都由它回答。它由编排在投递时算好，**不由** ``reason`` 或 ``mentioned`` 推导——同一 Agent 的重复
    唤醒会被合并，而合并后的 ``reason`` 取决于先到者，纪律不该随它漂。四种确定信号（被个人提及 /
    认领 / team 队长被任务变化唤醒 / **人类消息**）**同级不同源**：可读文案不能互相挪用。

    ``direct_message``（#189）标记这一轮发生在**私聊**：私聊的 system prompt 不再自称「多成员频道」、
    不再给「没有被点名时可以沉默」的边界（那是频道的事实，不是私聊的事实）。

    回应 rubric 的措辞是 #57 / D5 的定稿：**沉默 = 这一轮不产出正文**。旧措辞把它写成「只回一个
    空字符串之外的简短说明不算回应」——主句要求沉默、括注却示范「回一句说明」，模型于是把
    「（本轮消息与我无关，无需回应）」当成一次合规的沉默说了出来，而那仍是一条真实正文。
    因此这里不再用「回什么」描述沉默，并把它的反面（括注 / 旁白 / 说明 / 道歉）逐个点名。
    边界必须留在同一段里：被点名必须回应，未被点名且话题无关才轮到沉默——只写一半，模型就没有
    依据判断这一轮该不该说话。频道口径是 :data:`_CHANNEL_RUBRIC`（逐字不变），私聊口径是
    :data:`_DIRECT_MESSAGE_RUBRIC`。

    ``structured_silence``（#58）只**补一句**：这一轮模型手上有 ``declare_silence`` 这个工具时，
    沉默要用它声明，而不是靠「什么都不写」让后端去猜。它不改 #57 的措辞，也不改变纪律——声明之后
    仍然由后端按同一套 rubric 收口（被点名时声明沉默照样按 failed 处理）。

    ``with_web``（#186）只在**这一轮真的挂了联网工具**时为真，补一段边界句（:func:`_web_prompt_note`）；
    它与 ``structured_silence`` 同一形状：提示里提到某个工具，工具就真的在手上。默认 False，因此未
    配置 ``web_access`` 的频道与 #186 之前逐字相同。
    """
    base = str(agent.get("system_prompt") or "").strip()
    role = str(agent.get("role") or "").strip()
    parts = []
    if base:
        parts.append(base)
    if role:
        parts.append(f"你的角色：{role}")
    if leader:
        parts.append(
            "你是这个 team 频道的队长，不是成员：队长负责拆解任务、把具体工作分派给成员，"
            "再汇总他们的结论给出最终回复。" + _CHANNEL_RUBRIC
        )
    elif direct_message:
        parts.append(_DIRECT_MESSAGE_RUBRIC)
    else:
        parts.append("你在一个多成员频道里协作者的身份是「成员」，不是人类。" + _CHANNEL_RUBRIC)
    if leader:
        parts.append(
            "你是这个 team 频道的队长，负责拆解与分派。需要成员动手时使用 TeamAssign 工具"
            "（成员 + 任务描述）：成员在**同一轮**里运行，结果作为工具结果回到你这里，"
            "不需要、也不能指望他们另开一轮。成员看不到频道上下文，所以任务描述要写全。"
        )
    if signal is ReplySignal.TEAM_TASK_CHANGE:
        # 建任务 / 改派 / 转派叫醒队长的那一轮（#179 B / ADR-0025 §四）。
        parts.append("本轮由 team 任务变化唤醒，你必须回应。")
    if signal is ReplySignal.HUMAN_MESSAGE:
        # 人类直接对话（#189）：四个面同一句。来源写成「人类消息」——它不是任务变化、不是认领，
        # 也不依赖「被点名」才成立，写成那三种就是一条不存在的事实。
        parts.append("本轮由人类消息唤醒：人类直接对你说话，这是确定信号，你必须回应。")
    if signal is ReplySignal.CONTINUE:
        # 自动续跑（ADR-0027）：上一轮由**受派者自己**申请，来源写成「认领」或「人类消息」都是
        # 一条不存在的事实。
        parts.append("本轮由自动续跑唤醒：上一轮你申请继续推进这张任务，这是确定信号，你必须回应。")
    if mentioned:
        parts.append("注意：本轮有人点名了你，因此你必须回应。")
    if with_web:
        parts.append(_web_prompt_note())
    if structured_silence:
        # 结构化通道的**说明书**（#58）：只解释这个出口怎么用，不重述纪律（上面那段是唯一的 rubric）。
        # 措辞刻意不复述「被点名 / 话题无关」这两条边界：它们已经在上面的 rubric 里，写第二遍会让
        # 「回应纪律恰好是一段」这条既有判据（#57 的测试）变成两段自相解释的规矩。
        parts.append(
            "你本轮配有一个 declare_silence 工具。当上面的回应纪律允许你沉默时，"
            "调用它来声明本轮沉默——声明之后什么都不要再写；"
            "不要用括注、旁白、说明或道歉去交代「我本轮不说话」，那仍然是一条正文。"
            "当纪律要求你必须回应时，不要用它回避。"
        )
    if task_context is not None:
        # 任务本体（ADR-0016 四）：标题 + 当前状态 + 受派者身份。
        # 身份句只在**本轮 Agent 就是受派者**时给——否则会制造「我是受派者」的假事实。
        context_lines = ["任务上下文："]
        if task_context.title:
            context_lines.append(f"任务：{task_context.title}")
        if task_context.status:
            context_lines.append(f"当前状态：{render(f'status.task.{task_context.status}', SOURCE_LOCALE)}")
        if task_context.assignee_member_id == str(agent.get("id") or ""):
            context_lines.append("你是这张任务的受派者。")
        parts.append("\n".join(context_lines))
        if signal is ReplySignal.CLAIM and not leader:
            # 认领轮另有显式声明（S5）：纪律不靠模型猜 rubric。队长轮的对应句在上面的信号段里（措辞不同）。
            parts.append("本轮由认领唤醒，你必须回应。")
        transfer_options = task_context.transfer_options_for(str(agent.get("id") or ""))
        option_names = "\n".join(
            f"{index}. {option.display_name}" for index, option in enumerate(transfer_options, start=1)
        )
        # 转派句按本轮身份选：`transfer_task` 仍只属**受派者**（ADR-0022 §三）。措辞要同时盖住两种事实：
        # 队长**就是**受派者时工具真的在它手上（`_task_action_tools` 只在 `actor == assignee` 时挂它），
        # 队长不是受派者时没有这个工具——所以不能用一句无条件的「队长不能改指派人」。
        transfer_sentence = (
            "transfer_task 只属于这张任务的受派者：你只有在就是受派者时才能用它改指派人；"
            "不是受派者时需要成员动手请用 TeamAssign。"
            if leader
            else "仅当前受派者可使用 transfer_task 工具转派给另一位 Agent。"
        )
        # 队长**不是受派者**时：`transfer_task` 不在它手上，而 `TeamAssign` 的可选成员由**该工具自身的描述**
        # 列出（`team.py` 从本轮成员快照生成）。这里**不再列一份**：`transfer_options_for()` 的来源是
        # `Database.agent_members()`（**全工作区**），把它标成「可 TeamAssign 分派的成员」会把频道外的
        # Agent 写成可分派对象——那是与事实不符的口径（#179 follow-up / ADR-0025 §四）。
        # 任务动作的公共约束**只写一处**：两种身份（队长不受派 / 其它）只差末尾那段成员引导，
        # 否则以后改动作约束还得同步两个分支（review Standards 轴的 Duplicated Code）。
        action_rules = (
            "当前在任务线程中。需要改状态时使用 set_task_status 工具，不要只在正文中声称已改；"
            f"{transfer_sentence}每轮最多提交一个动作，"
            "动作最终是否成功以系统记录为准。"
        )
        if task_context.allow_continue and task_context.assignee_member_id == str(agent.get("id") or ""):
            # 自动续跑的**执行面**（ADR-0027）：正文里的「我会继续」没有执行面，工具调用才有。
            # 完成 / 待审的出口复用既有五态，不新增状态。
            action_rules += (
                "任务还没干完（还有后续步骤、或你正准备说「接下来我会…」）时，**必须调用 "
                "continue_task 工具**申请自动续跑：只在正文里承诺不会触发任何后续轮次。"
                "任务已完成或需要人工审阅时，用 set_task_status 把状态推进到 in_review；"
                "需要用户先确认才能继续时，不要调用 continue_task。"
            )
        if leader and task_context.assignee_member_id != str(agent.get("id") or ""):
            parts.append(f"{action_rules}需要成员动手时用 TeamAssign，可选成员见该工具的描述。")
        else:
            parts.append(
                f"{action_rules}可转派 Agent（工具参数 recipient_number 对应序号）：\n"
                f"{option_names if option_names else '暂无其他 Agent'}"
            )
    return "\n\n".join(parts)


def _agent_instance(options: dict[str, Any], *, plan: PlanSession | None, member_id: str) -> Any:
    """按是否有 Plan 选择 Agent 类；Plan 轮次在每次回复前后开/收一个 run。"""
    if plan is None:
        from agentscope.agent import Agent

        return Agent(**options)
    from .plan import plan_agent_class

    instance = plan_agent_class()(**options)
    instance._plan_session = plan
    instance._plan_member_id = member_id
    return instance


def _round_toolkit(
    provider: str,
    *,
    task_context: TaskActionContext | None,
    actor_id: str,
    task_actions: list[AgentTaskAction],
    silence_declarations: list[None],
    workspace_tools: list[Any] | None = None,
    web_tools: list[Any] | None = None,
    skills: list[Any] | None = None,
    with_plan: bool = False,
) -> Any:
    """装配这一轮的工具集：workspace 文件工具 + 联网工具 + 任务动作（仅任务线程）+ 沉默声明。

    **能力闸门在这里**：``supports_tool_calling(provider)`` 为假时**不挂**沉默工具，于是请求里没有
    ``tools``、模型无法声明沉默，这一轮只能靠正文收口成既有的 ``replied`` / ``ignored`` / ``failed``。
    退化是结构性的（不是「模型大概不会调」的侥幸），这与「不假定所有 provider 一致」是同一条纪律。

    workspace 工具（#148）不受这个闸门影响：它们是**有 workspace 配置**时的能力，不是 message 通道；
    工具 schema 走的是同一条 provider 工具通道——provider 不能携带工具时，文件工具也送不出去。

    ``web_tools``（#186）与沉默工具同属**能力**：票面要求「provider 不能携带工具时，联网工具与文件
    工具一样送不出去」，因此它也守 ``supports_tool_calling`` 闸门。哪两个工具在清单里（配置档 /
    权限档 / 数据面确认三条闸门）由装配层的 :meth:`AgentScopeAgentSeam._web_tools` 决定，与这里的
    能力闸门正交。

    Skill（#152）与 workspace 工具同属「本轮能力」：AgentScope 的 ``Toolkit`` 把技能的说明注入
    系统提示并提供一个内置查看器；只有技能、没有工具时仍然要建 toolkit。

    返回 ``None`` 表示这一轮不需要任何工具（未配置 workspace 的频道 + 非任务轮次 + 不支持工具的
    provider + 没有技能）：此时 ``Agent`` 不带 ``toolkit``，与 #58 之前的形态逐字相同。
    """
    from agentscope.tool import Toolkit

    tools: list[Any] = []
    if workspace_tools:
        tools.extend(workspace_tools)
    if web_tools and supports_tool_calling(provider):
        tools.extend(web_tools)
    if task_context is not None:
        tools.extend(_task_action_tools(task_context, actor_id, task_actions))
    if with_plan:
        # Plan 四工具与 workspace 同属「本轮能力」，不受沉默工具的能力闸门影响；provider
        # 不能携带工具时它们和文件工具一样送不出去，但装配层不据此改变清单。
        from .plan import plan_tools

        tools.extend(plan_tools())
    if supports_tool_calling(provider):
        tools.extend(_silence_tools(silence_declarations))
    if not tools and not skills:
        return None
    return Toolkit(tools=tools, skills_or_loaders=skills or [])


def _value_returning_tool(func: Callable[..., Any], permission: Any, *, is_read_only: bool = False) -> Any:
    """把一个**返回普通值**的工具函数包成可进 ``Toolkit`` 的 ``ToolBase``。

    AgentScope 2.0.10 **运行期**接受返回普通值（``str``）的工具；它 bundled 的 typing 仍只声明
    ``ToolChunk`` / ``Awaitable[ToolChunk]``。差距收口在**这一处**：形参写成 ``Callable[..., Any]``
    （``Any`` 返回类型对 ``ToolChunk`` 双向兼容），于是既不需要 ``# type: ignore``（它在只看得到
    ``Any`` 的检查器环境里会是「unused ignore」），也不需要 ``cast``（那会是「redundant cast」）。
    行为零变化：这里只多了一个局部变量注解。
    """
    from agentscope.tool import FunctionTool, ToolBase

    tool: ToolBase = FunctionTool(func, permission=permission, is_read_only=is_read_only)
    return tool


def _silence_tools(declarations: list[None]) -> list[Any]:
    """「声明沉默」这个结构化通道：**只记录意图**、不写任何东西（写入仍由编排决定）。

    工具是**自愿**调用的、不做强制 ``tool_choice``：强制工具选择在 AgentScope 2.0.10 里是各家差异
    最大的一档（思考模式会被拒绝、Ollama 传输层忽略 mode），而本通道只需要「模型想沉默时有一个
    正式的出口」——它自愿调用，我们据此落 ``silent``；不调用就完全退回既有判据。
    """
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import ToolBase

    declaration_lock = threading.Lock()

    def declare_silence() -> str:
        """声明本轮沉默：这一轮不产出正文（不要再用括注 / 旁白 / 道歉交代）。"""
        with declaration_lock:
            declarations.append(None)
        return "已记录本轮沉默；请不要再输出任何正文。"

    permission = PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message="沉默声明只记录为本轮的结构化结果，不改写任何消息或任务。",
    )
    # 工具函数的 bundled typing 与运行期不一致：收口在 `_value_returning_tool` 一处。
    tools: list[ToolBase] = [_value_returning_tool(declare_silence, permission, is_read_only=True)]
    return tools


def _task_action_tools(
    context: TaskActionContext, actor_id: str, actions: list[AgentTaskAction]
) -> list[Any]:
    """仅在任务轮次提供类型化动作工具；工具只收集意图，写入仍由编排器授权。"""
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import ToolBase

    action_lock = threading.Lock()

    def record(action: AgentTaskAction) -> str:
        with action_lock:
            if actions:
                return "本轮只接受一个任务动作；已记录的动作保持不变。"
            actions.append(action)
        return "已记录任务动作请求；系统会在本轮结束时校验并应用。"

    def set_task_status(status: TaskStatus) -> str:
        """请求推进当前任务状态。

        Args:
            status: 目标状态：todo、in_progress、in_review、done 或 closed。
        """
        return record(SetTaskStatusAction(context.task_id, status))

    # ALLOW 仅批准把动作请求放进本轮结果；数据库写入仍由编排器在回复后校验并执行。
    permission = PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message="任务动作只记录为本轮结构化结果，执行前仍会经过服务端授权校验。",
    )
    # 工具函数的 bundled typing 与运行期不一致：收口在 `_value_returning_tool` 一处。
    tools: list[ToolBase] = [_value_returning_tool(set_task_status, permission)]
    if actor_id == context.assignee_member_id:
        if context.allow_continue:

            def continue_task() -> str:
                """申请自动续跑：本轮结束后由编排再唤醒你一轮，继续推进这张任务。

                任务没干完（还有后续步骤、或你正准备说「接下来我会…」）时**必须调用它**；
                只在正文里承诺「我会继续」不会触发任何后续轮次。任务已完成或需要人工审阅时，
                改用 set_task_status；需要用户先确认才能继续时，不要调用它。
                """
                return record(ContinueTaskAction(context.task_id))

            tools.append(_value_returning_tool(continue_task, permission))

        transfer_options = context.transfer_options_for(actor_id)

        def transfer_task(recipient_number: int) -> str:
            """把当前任务转派给一位 Agent。

            Args:
                recipient_number: 系统提示中可转派 Agent 的 1-based 序号。
            """
            if recipient_number < 1 or recipient_number > len(transfer_options):
                return "转派目标序号无效；没有记录转派动作。"
            recipient = transfer_options[recipient_number - 1]
            return record(TransferTaskAction(context.task_id, recipient.member_id))

        tools.append(_value_returning_tool(transfer_task, permission))
    return tools


def _author_name(message: dict[str, Any]) -> str:
    author = message.get("author") or {}
    return str(author.get("display_name") or author.get("id") or "member")


def _body(message: dict[str, Any]) -> str:
    return str(message.get("body") or "")


def _msg_text(message: Any) -> str:
    """从 AgentScope 的 ``Msg`` 里取纯文本（``content`` 可能是 str 或内容块列表）。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)
    return ""
