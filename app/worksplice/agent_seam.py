"""通向 AgentScope 的窄接缝：协作域只认识这一层的形状，不认识框架。

协作域的唤醒编排需要的全部框架能力被收敛成两个东西：

1. ``AgentSeam``——「一个成员的一轮」这个动作：输入**成员**与该成员**待读的新消息**，
   输出**零或多条待落库回复** + 一个**轮次结论**。这就是票面要求的「薄适配接口」。
2. ``AgentScopeAgentSeam``——真实实现，进程内直接调用 AgentScope 2.x（``Agent`` /
   ``agentscope.model`` / ``agentscope.credential``），不使用框架的 Agent Service。

为什么必须薄：测试要能在**不联网、零成本**的前提下把「回应 / 忽略 / 失败 / 延迟」四种行为
固定下来，所以替身（``tests/scripted.py`` 的 ``ScriptedAgentSeam``）注入的是同一个接口，
而不是去 mock 框架内部。于是协作域的行为测试与「模型是哪一家」完全解耦。

``agentscope`` 只在**真实适配器被实例化时**才 import：没有装它、或只想跑脚本化替身的测试，
不会因为缺依赖而失败。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# 轮次结论的状态：与规格 §5 的 round outcome status 同一套词。
SEAM_STATUSES = ("replied", "ignored", "failed")

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

# provider → AgentScope 2.0.9 credential 类型的 discriminator 与它的类型白名单。
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
    """该 provider 需要的凭证在进程环境里不存在（因此这个成员「未启用」）。"""

    def __init__(self, provider: str, env_names: tuple[str, ...]):
        self.provider = provider
        self.env_names = env_names
        joined = " / ".join(env_names) if env_names else "（该提供商无需凭证）"
        super().__init__(f"模型提供商 {provider} 缺少凭证：请先设置环境变量 {joined}")


@dataclass
class RoundMessage:
    """待落库的一条回复。``body`` 是完整正文——本票不做 token 级流式。"""

    body: str


@dataclass
class RoundOutcome:
    """适配接口输出的轮次结论（协作域据此落 ``round_outcome`` 行）。"""

    status: str
    reason: str = ""
    messages: list[RoundMessage] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    consumed_budget: bool = False

    def __post_init__(self) -> None:
        if self.status not in SEAM_STATUSES:
            raise ValueError(f"轮次结论状态非法：{self.status}")
        # 「一轮 ↔ 至多一条可落库消息」是契约（spec #1 §5）；越界直接在这里失败，
        # 而不是让落库路径多出一条谁都没预料到的消息。
        if len(self.messages) > 1:
            raise ValueError("一轮最多产出 1 条频道消息")


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
    ) -> RoundOutcome:
        """跑一轮，返回结论。

        Args:
            agent: 成员行 + ``agent_config``（``id`` / ``display_name`` / ``role`` /
                ``model_id`` / ``system_prompt`` / ``enabled``）。
            messages: 该成员**待读的新消息**（经同一个读接口取得，不含正文的第二通道）。
            mentioned: 这批新消息里是否个人提及了该成员（rubric：被点名必须回应）。
            target_id: 频道 id。
        """
        ...


def read_api_key(provider: str, environ: dict[str, str] | None = None) -> str | None:
    """凭证窄 seam：只从**进程环境变量**读，应用库不存任何密钥。

    Returns:
        命中的第一个环境变量值；该 provider 无需凭证时返回 ``None``（表示可以用）；
        需要凭证而环境里没有时抛 ``CredentialUnavailable``。
    """
    env = os.environ if environ is None else environ
    names = PROVIDER_KEY_ENV.get(provider)
    if names is None:
        raise CredentialUnavailable(provider, ())
    if not names:
        return None
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    raise CredentialUnavailable(provider, names)


class AgentScopeAgentSeam:
    """真实实现：进程内调用 AgentScope 2.x。

    只用 2.x API（``from agentscope.agent import Agent``、``Agent(name=…, system_prompt=…,
    model=…, toolkit=…)``、``agentscope.model`` + ``agentscope.credential``）。**不**使用
    1.x 的 ``ReActAgent(name, sys_prompt, …)`` 签名，**不**使用框架的 Agent Service。

    一轮 ↔ 一条 ``Msg``：一次 ``reply`` 调用累积成恰好一条 ``Msg``；它的 ``finished_reason`` /
    ``usage`` / ``error`` 只影响本轮的结论，不构成任何消息正本。
    """

    def __init__(self, *, timeout_seconds: float = 120.0, environ: dict[str, str] | None = None):
        self.timeout_seconds = timeout_seconds
        self._environ = environ

    # ─── 模型构造 ─────────────────────────────────────────────────────────
    def _build_model(self, provider: str, model_name: str) -> Any:
        from agentscope import credential as cred

        if provider == "ollama":
            ollama = cred.OllamaCredential(host=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
            from agentscope.model import OllamaChatModel

            return OllamaChatModel(credential=ollama, model=model_name, stream=False)

        api_key = read_api_key(provider, self._environ)
        credential_type = PROVIDER_CREDENTIAL_TYPE[provider]
        credential_class = cred.CredentialFactory.get_credential_class(credential_type)
        if credential_class is None:
            raise RuntimeError(f"AgentScope 不认识这个凭证类型：{credential_type}")
        # 用 model_validate 而不是派生专属构造参数：providers 的凭证形状不一致
        # （``api_key`` / ``host`` / ``api_host``），model_validate 是框架自己的反序列化入口。
        credential = credential_class.model_validate({"api_key": api_key} if api_key else {})
        chat_model_class = credential_class.get_chat_model_class()
        return chat_model_class(
            credential=credential,
            model=model_name,
            parameters=chat_model_class.Parameters(),
            stream=False,
        )

    # ─── 一轮 ─────────────────────────────────────────────────────────────
    def run_round(
        self,
        agent: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
    ) -> RoundOutcome:
        import asyncio

        provider = provider_of(str(agent.get("model_id") or ""))
        model_name = model_name_of(str(agent.get("model_id") or ""))
        system_prompt = _system_prompt(agent, mentioned=mentioned)

        async def _reply() -> Any:
            from agentscope.agent import Agent
            from agentscope.message import UserMsg

            instance = Agent(
                name=str(agent.get("display_name") or agent.get("id") or "agent"),
                system_prompt=system_prompt,
                model=self._build_model(provider, model_name),
            )
            # 一轮的一条 Msg：把待读的新消息合成一次输入，回复累积成恰好一条 Msg。
            # UserMsg 是 2.x 建用户消息的入口（它自己把文本包成 TextBlock）。
            inputs = [UserMsg(name=_author_name(item), content=_body(item)) for item in messages]
            return await instance.reply(inputs if inputs else None)

        try:
            reply = asyncio.run(asyncio.wait_for(_reply(), timeout=self.timeout_seconds))
        except TimeoutError:
            return RoundOutcome(status="failed", reason=f"模型调用超时（{self.timeout_seconds:.0f} 秒）")
        except Exception as error:  # 框架 / 网络 / 凭证的任何失败都收口成一轮 failed
            return RoundOutcome(status="failed", reason=f"模型调用失败：{type(error).__name__}: {error}")

        reply_error = getattr(reply, "error", None)
        if reply_error:
            return RoundOutcome(status="failed", reason=f"模型返回错误：{reply_error}")

        text = _msg_text(reply)
        if not text.strip():
            return RoundOutcome(status="ignored", reason="模型没有产出可落库的正文（本轮自判不回应）")
        return RoundOutcome(
            status="replied", reason="模型自主判断需要回应", messages=[RoundMessage(body=text)]
        )


def _system_prompt(agent: dict[str, Any], *, mentioned: bool) -> str:
    """把成员配置里的系统提示 + 本轮的回应 rubric 合成一次调用的 system prompt。"""
    base = str(agent.get("system_prompt") or "").strip()
    role = str(agent.get("role") or "").strip()
    parts = []
    if base:
        parts.append(base)
    if role:
        parts.append(f"你的角色：{role}")
    parts.append(
        "你在一个多成员频道里协作者的身份是「成员」，不是人类。"
        "回应纪律：被个人提及（@你的名字）时必须回应；话题明确与你无关、或你没有被点名时，"
        "应当保持沉默（只回一个空字符串之外的简短说明不算回应）。"
    )
    if mentioned:
        parts.append("注意：本轮有人点名了你，因此你必须回应。")
    return "\n\n".join(parts)


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
