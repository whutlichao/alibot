"""#205：AgentScope 对齐三处——openai-response 模型类、MultiAgentFormatter、read-only 计划工具面。

**这是 seam 级用例，不服从行为黑盒的默认分层**：模型类与 formatter 只能从构造结果读到（构造
SDK 客户端不联网，真实请求只在 ``_call_api`` 时发生）。票面明确要求 ``_build_model`` 与
``model.formatter.format`` 的读数，故与 ``test_agent_seam_client_lifecycle.py`` 同类，直接驱动
适配器；team 的 ``_round_toolkit`` 参数断言同理，用来覆盖「成员在 read-only 下不会被 TeamAssign
驱动」时无法从请求体观察的那一格。

取证面分两层：

- **模型构造**（a/b/c）：直接对 ``_build_model`` 的返回值取证，不发任何网络请求。
- **计划工具面**（d）：沿用 ``test_web_assembly.py`` 的进程内 provider 端点，读**真实请求体**里的
  ``tools``；team 路径用进程内脚本模型（只有它能在同一轮里真的触发 ``TeamAssign``），与
  ``test_global_rules.py`` 同源。read-only 与 ask-writes 成对出现，防止「工具面为空」的空转用例。

``openai-response`` 与 ``openai`` 共用同一份 ``openai_credential``，但模型类不同（Responses API
vs Chat Completions）；四个 Plan 工具在 ``PermissionMode.EXPLORE`` 下必然被拒，read-only 轮次
因此连工具面与 Plan run 都不该出现。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import AsyncGenerator, Iterator, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import alibot.agent_seam as agent_seam_module
import pytest
from agentscope import formatter as formatters
from agentscope import model as models
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import PROVIDER_CREDENTIAL_TYPE, AgentScopeAgentSeam
from alibot.db import open_database
from alibot.plan import PLAN_TOOL_NAMES, PlanSession
from alibot.team import TeamRoundContext, member_internal_name

# 每个 provider 期望的模型类与 MultiAgentFormatter。这是**验收判据**的独立副本：
# 源实现按 provider 建表，本文件按同一份公开事实（agentscope.model / agentscope.formatter）
# 复核，而不是 import 实现里的私有映射（否则两处一起写错也能通过）。
EXPECTED_MODELS: dict[str, tuple[str, str]] = {
    "dashscope": ("DashScopeChatModel", "DashScopeMultiAgentFormatter"),
    "openai": ("OpenAIChatModel", "OpenAIMultiAgentFormatter"),
    "openai-response": ("OpenAIResponseModel", "OpenAIResponseMultiAgentFormatter"),
    "anthropic": ("AnthropicChatModel", "AnthropicMultiAgentFormatter"),
    "gemini": ("GeminiChatModel", "GeminiMultiAgentFormatter"),
    "deepseek": ("DeepSeekChatModel", "DeepSeekMultiAgentFormatter"),
    "moonshot": ("MoonshotChatModel", "MoonshotMultiAgentFormatter"),
    "minimax": ("MiniMaxChatModel", "MiniMaxMultiAgentFormatter"),
    "volcengine": ("VolcengineChatModel", "VolcengineMultiAgentFormatter"),
    "xai": ("XAIChatModel", "XAIMultiAgentFormatter"),
    "ollama": ("OllamaChatModel", "OllamaMultiAgentFormatter"),
}

# 只用于**构造**客户端的假 key：不发起任何请求；ollama 不需要 key。
FAKE_KEYS: dict[str, tuple[str, ...]] = {
    "dashscope": ("DASHSCOPE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openai-response": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "moonshot": ("MOONSHOT_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "volcengine": ("ARK_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "ollama": (),
}

STUB_REPLY = "已处理。"


def _seam(**kwargs: Any) -> AgentScopeAgentSeam:
    return AgentScopeAgentSeam(timeout_seconds=10.0, **kwargs)


# ─── a/b：模型类与 formatter 按 provider 对齐 ────────────────────────────────
def test_openai_response_builds_the_responses_model_with_its_formatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``openai-response`` 必须建 ``OpenAIResponseModel``，不是 credential 工厂默认的 chat 模型。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-issue-205-stub")

    model = _seam()._build_model("openai-response", "gpt-5")

    assert isinstance(model, models.OpenAIResponseModel)
    assert not isinstance(model, models.OpenAIChatModel)
    assert isinstance(model.formatter, formatters.OpenAIResponseMultiAgentFormatter)


@pytest.mark.parametrize("provider", sorted(PROVIDER_CREDENTIAL_TYPE))
def test_every_provider_builds_its_model_with_the_matching_multi_agent_formatter(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """11 家 provider：模型类正确 + formatter 是这一家的 MultiAgentFormatter。"""
    assert set(EXPECTED_MODELS) == set(PROVIDER_CREDENTIAL_TYPE)
    for name in FAKE_KEYS[provider]:
        monkeypatch.setenv(name, "sk-issue-205-stub")
    model_name, formatter_name = EXPECTED_MODELS[provider]

    model = _seam()._build_model(provider, "stub-model")

    assert isinstance(model, getattr(models, model_name))
    assert isinstance(model.formatter, getattr(formatters, formatter_name))


# ─── c：多作者行为——两条消息的名字都进模型载荷 ──────────────────────────────
def test_multi_agent_formatter_puts_both_author_names_into_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-issue-205-stub")
    model = _seam()._build_model("openai", "stub-model")

    payload = asyncio.run(
        model.formatter.format(
            [
                UserMsg(name="alice", content="第一条：准备验收"),
                Msg(name="bob", content=[TextBlock(text="第二条：复核证据")], role="assistant"),
            ]
        )
    )

    rendered = json.dumps(payload, ensure_ascii=False)
    assert "alice" in rendered, rendered
    assert "bob" in rendered, rendered
    # 不只是类名：名字与各自的正文在同一份载荷里，多作者轮次才真的可区分。
    assert "alice" in rendered and "第一条：准备验收" in rendered, rendered
    assert "bob" in rendered and "第二条：复核证据" in rendered, rendered


# ─── d-1：单 Agent——read-only 轮次没有 Plan 四工具，也不开 Plan run ────────
class _PlainReplyHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：记录请求体并回一条固定正文。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-issue-205",
                "object": "chat.completion",
                "created": 0,
                "model": "stub-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": STUB_REPLY},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    _PlainReplyHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PlainReplyHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-issue-205-stub")
    monkeypatch.setenv("OPENAI_BASE_URL", base_url)
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME 指向临时目录：任何默认全局根都不可能落到真实 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _seed(
    tmp_path: Path, *, team: bool = False
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    database = open_database(tmp_path / "data")
    leader = database.create_agent("队长", "协调", "openai:stub-model", "简短回复。")
    if not team:
        channel = database.create_channel("规划频道", member_ids=[leader["id"]])
        return database, leader, channel, None
    member = database.create_agent("成员", "执行", "openai:stub-model", "简短回复。")
    channel = database.create_channel("规划频道", member_ids=[leader["id"], member["id"]])
    database.update_channel_config(channel["id"], mode="team", team_leader_member_id=leader["id"])
    return database, leader, channel, member


def _human(body: str = "开始吧") -> dict[str, Any]:
    return {"seq": 1, "kind": "human", "body": body, "author": {"id": "owner"}}


def _tool_names(index: int = -1) -> set[str]:
    body = _PlainReplyHandler.request_bodies[index]
    return {tool["function"]["name"] for tool in body.get("tools") or []}


def _plan_run_count(database: Any) -> int:
    with sqlite3.connect(database.path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM plan_run").fetchone()[0])


def _single_agent_round(database: Any, agent: dict[str, Any], channel: dict[str, Any]) -> Any:
    plan = PlanSession(database, target_id=channel["id"], base_seq=1)
    seam = _seam(permission_of=database.permission_settings_for_target)
    return seam.run_round_stream(
        database.get_agent(agent["id"]),
        [_human()],
        mentioned=False,
        target_id=channel["id"],
        plan=plan,
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("read-only", False), ("ask-writes", True), ("trusted-writes", True)],
)
def test_read_only_rounds_do_not_mount_plan_tools_or_open_a_plan_run(
    tmp_path: Path, stub_provider: str, mode: str, expected: bool
) -> None:
    database, agent, channel, _member = _seed(tmp_path)
    try:
        database.update_channel_config(channel["id"], permission_mode=mode)

        outcome = _single_agent_round(database, agent, channel)

        assert outcome.status == "replied", outcome.reason
        names = _tool_names()
        if expected:
            # 对照：ask-writes 下四工具真的在请求体里，证明上面的「缺席」不是空转。
            assert set(PLAN_TOOL_NAMES) <= names, names
            assert _plan_run_count(database) == 1
        else:
            assert not (set(PLAN_TOOL_NAMES) & names), names
            assert _plan_run_count(database) == 0, "read-only 轮次不得开 Plan run"
    finally:
        database.close()


# ─── d-2：team——队长与成员的工具面同步收紧 ─────────────────────────────────
class _ScriptedModel(ChatModelBase):
    """进程内脚本模型：记录每次请求的 tools，并按脚本回包。"""

    def __init__(self, script: Sequence[Sequence[ChatResponse]]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="scripted",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = list(script)
        self.tool_names: set[str] = set()
        self.calls = 0

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        for tool in tools or []:
            function = tool.get("function", tool)
            name = function.get("name")
            if name:
                self.tool_names.add(str(name))
        chunks = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        async def stream() -> AsyncGenerator[ChatResponse, None]:
            for chunk in chunks:
                yield chunk

        return stream()


def _text(text: str) -> list[ChatResponse]:
    return [ChatResponse(content=[TextBlock(text=text)], is_last=True)]


def _team_assign(member_id: str, prompt: str) -> list[ChatResponse]:
    block = ToolCallBlock(
        id="call-205",
        name="TeamAssign",
        input=json.dumps({"member": member_internal_name(member_id), "prompt": prompt}),
    )
    return [
        ChatResponse(content=[block], is_last=False),
        ChatResponse(content=[block], is_last=True),
    ]


class _PipelineSeam(AgentScopeAgentSeam):
    """真实适配器 + 按构造顺序取用的脚本模型队列（队长在前、成员随后）。"""

    def __init__(self, scripted: list[ChatModelBase], **kwargs: Any) -> None:
        super().__init__(timeout_seconds=10.0, **kwargs)
        self._scripted = list(scripted)

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._scripted.pop(0)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("read-only", False), ("ask-writes", True)],
)
def test_team_leader_and_member_plan_tool_surfaces_follow_the_permission_mode(
    tmp_path: Path, mode: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, leader, channel, member = _seed(tmp_path, team=True)
    assert member is not None
    # 成员在 read-only 档下不会被 ``TeamAssign`` 驱动（EXPLORE 拒写），因此成员工具面改由
    # **装配参数**取证：成员 Agent 的 toolkit 与队长在同一轮里一起构造。
    real_round_toolkit = agent_seam_module._round_toolkit
    with_plan_calls: list[bool] = []

    def spy(provider: str, **kwargs: Any) -> Any:
        with_plan_calls.append(bool(kwargs.get("with_plan")))
        return real_round_toolkit(provider, **kwargs)

    monkeypatch.setattr(agent_seam_module, "_round_toolkit", spy)
    try:
        database.update_channel_config(channel["id"], permission_mode=mode)
        leader_model = _ScriptedModel([_team_assign(member["id"], "写结论"), _text("队长最终答复")])
        member_model = _ScriptedModel([_text("成员结论")])
        seam = _PipelineSeam(
            [leader_model, member_model],
            permission_of=database.permission_settings_for_target,
        )
        team_run = database.create_team_run(
            target_id=channel["id"], leader_member_id=leader["id"], base_seq=1
        )
        context = TeamRoundContext(
            target_id=channel["id"],
            leader_id=leader["id"],
            members=(member,),
            team_run_id=team_run["id"],
            budget=database,
        )

        outcome = seam.run_round_stream(
            database.get_agent(leader["id"]),
            [_human("开工")],
            mentioned=False,
            target_id=channel["id"],
            team=context,
            plan=PlanSession(database, target_id=channel["id"], base_seq=1),
        )

        assert outcome.status == "replied", outcome.reason
        # 队长必然装配一次；成员在 ask-writes 下有自己的工具面（Plan 四工具），read-only 下
        # 连 toolkit 都不建（没有文件/技能/联网需求），两处都不得出现 with_plan=True。
        assert len(with_plan_calls) >= (2 if expected else 1), with_plan_calls
        assert set(with_plan_calls) == {expected}, with_plan_calls
        if expected:
            assert set(PLAN_TOOL_NAMES) <= leader_model.tool_names, leader_model.tool_names
            assert set(PLAN_TOOL_NAMES) <= member_model.tool_names, member_model.tool_names
            assert _plan_run_count(database) > 0, "ask-writes 下每轮都有 Plan run"
        else:
            assert not (set(PLAN_TOOL_NAMES) & leader_model.tool_names), leader_model.tool_names
            assert _plan_run_count(database) == 0, "read-only 轮次不得开 Plan run"
    finally:
        database.close()
