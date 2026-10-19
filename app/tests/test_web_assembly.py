"""#186：联网工具的装配与权限闸门（ADR-0026 §二 / §三 / §四）。

三条闸门 + 一条能力闸门要在**装配层**取证，而「工具到底挂没挂」唯一可观察的事实是 **provider 请求
里的 `tools` 清单**——那只能在真的 `Agent` 里读。因此这组用例沿用
``test_silence_tool_channel.py`` / ``test_local_workspace.py`` 的进程内 provider 端点
（``127.0.0.1:0``，零成本、不发一个包），**不新开 seam**、不新开前端测试框架。

**零请求的取证方式**：把 ``web.UrlopenTransport`` 换成记录型替身（进程内类），再让 stub 端点回一个
**主动调用** `fetch` 的工具调用。工具没挂时框架只会回一条「没有这个工具」，替身记录的请求数为 0；
同一组用例里另有一条「确认齐全 ⇒ 记到 1 条请求」的对照，证明这个探针是活的（不是永远读到 0）。

库侧配置（``web_access`` / 权限档 / ``remote_consent_scope('web', provider)``）用直写库取证，与
``test_approval_seam.py`` 同一体例：本票不做人类面的确认写入口（见验收文档的遗留项）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.api import create_app
from alibot.approval import ApprovalBroker
from alibot.db import open_database
from alibot.events import EventBus
from alibot.web import HttpRequest, HttpResponse
from fastapi.testclient import TestClient

AGENT_NAME = "联网成员"
STUB_REPLY = "已处理。"
FETCH_URL = "https://api.example.com/docs"


# ─── 进程内 provider 端点：可回固定正文，也可回一次工具调用 ────────────────────
class _StubProviderHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：记录每个请求体；按用例决定首轮是不是一次工具调用。"""

    protocol_version = "HTTP/1.1"

    request_bodies: ClassVar[list[dict[str, Any]]] = []
    #: 有值时首轮返回这个工具调用，之后的轮次回固定正文（Agent 的工具循环会再来一次）。
    tool_call: ClassVar[dict[str, Any] | None] = None
    tool_call_sent: ClassVar[bool] = False

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        if type(self).tool_call is not None and not type(self).tool_call_sent:
            type(self).tool_call_sent = True
            message: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [type(self).tool_call],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": STUB_REPLY}
            finish_reason = "stop"
        self._send_json(
            {
                "id": "chatcmpl-issue-186",
                "object": "chat.completion",
                "created": 0,
                "model": "stub-model",
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return


def _fetch_tool_call(url: str = FETCH_URL) -> dict[str, Any]:
    return {
        "id": "tc-fetch",
        "type": "function",
        "function": {"name": "fetch", "arguments": json.dumps({"url": url})},
    }


class _RecordingTransport:
    """出网传输替身：记录请求。装配层真的挂了联网工具、且审批放行时才会有记录。"""

    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(status=200, body=b"<html><body>page</body></html>", url=request.url)


@pytest.fixture
def stub_provider() -> Iterator[str]:
    _StubProviderHandler.request_bodies = []
    _StubProviderHandler.tool_call = None
    _StubProviderHandler.tool_call_sent = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubProviderHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "sk-issue-186-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.fixture
def no_outbound_requests(monkeypatch: pytest.MonkeyPatch) -> _RecordingTransport:
    """把默认传输换成记录型替身：联网工具真的执行了，才会在这里留下请求。"""
    transport = _RecordingTransport()
    monkeypatch.setattr("alibot.web.UrlopenTransport", lambda *args, **kwargs: transport)
    return transport


# ─── 库侧配置 ────────────────────────────────────────────────────────────────
@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    # 搜索 provider 由**进程环境**解析（D4）：只有一把 key ⇒ brave。每个用例都需要它，
    # 与模型端点无关（能力闸门那条用例用的是脚本化模型）。
    monkeypatch.setenv("BRAVE_API_KEY", "brave-issue-186")
    data_dir = tmp_path / "data"
    database = open_database(data_dir)
    agent = database.create_agent(AGENT_NAME, "工程师", "openai:stub-model", "简短回复。")
    channel = database.create_channel("联网频道", member_ids=[agent["id"]])
    database.close()
    # 重新打开：用例在库打开期间直写配置（与 ``test_approval_seam.py`` 同一体例）；
    # ``isolation_level=None`` 让外部连接的提交对这条连接可见。
    database = open_database(data_dir)
    try:
        yield database, agent, channel
    finally:
        database.close()


def _configure(
    database: Any,
    channel: dict[str, Any],
    *,
    web_access: str | None = None,
    mode: str = "ask-writes",
    consent: Sequence[str] = (),
    consent_rows: Sequence[str] = (),
    agent_id: str | None = None,
    agent_web_access: str | None = None,
) -> None:
    """直写库配置：``web_access`` / 权限档 / ``('web', provider)`` 确认行。"""
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE channel SET web_access = ?, permission_mode = ? WHERE id = ?",
            (web_access, mode, channel["id"]),
        )
        if agent_id is not None:
            connection.execute(
                "UPDATE agent_config SET web_access = ? WHERE member_id = ?",
                (agent_web_access, agent_id),
            )
        for provider in list(consent) + list(consent_rows):
            connection.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
                "VALUES ('web', ?, '2026-10-19T00:00:00+00:00') ON CONFLICT DO NOTHING",
                (provider,),
            )
        connection.commit()


def _seam(
    database: Any, *, broker: ApprovalBroker | None = None, environ: dict[str, str] | None = None
) -> Any:
    return AgentScopeAgentSeam(
        timeout_seconds=10.0,
        environ=environ,
        permission_of=database.permission_settings_for_target,
        web_of=database.web_settings_for_target,
        approvals=broker,
    )


def _run_round(seam: Any, database: Any, agent: dict, channel: dict, *, emit: Any = None) -> Any:
    return seam.run_round_stream(
        database.get_agent(agent["id"]),
        [{"seq": 1, "kind": "human", "body": "帮我查一下", "author": {"id": "owner"}}],
        mentioned=False,
        target_id=channel["id"],
        emit=emit,
    )


def _tool_names(index: int = 0) -> set[str]:
    body = _StubProviderHandler.request_bodies[index]
    return {tool["function"]["name"] for tool in body.get("tools") or []}


def _system_prompt(index: int = 0) -> str:
    messages = _StubProviderHandler.request_bodies[index].get("messages") or []
    return "".join(str(item.get("content") or "") for item in messages if item.get("role") == "system")


# ─── 1. 配置闸门：未配置 web_access ⇒ 工具集逐字不变 ─────────────────────────
def test_without_web_access_the_tool_set_stays_unchanged(library: Any, stub_provider: str) -> None:
    """``web_access=NULL`` 时即使**已有**确认行，工具集也与 #186 之前逐字相同。"""
    database, agent, channel = library
    _configure(database, channel, consent=["brave"])

    outcome = _run_round(_seam(database), database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert _tool_names() == {"declare_silence"}
    assert "fetch" not in _system_prompt(), "没挂联网工具时提示里不该提到它"


# ─── 2. 权限档闸门：read-only 只装 web_search ────────────────────────────────
def test_read_only_mounts_web_search_but_never_fetch(library: Any, stub_provider: str) -> None:
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="read-only", consent=["brave"])

    outcome = _run_round(_seam(database), database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert "web_search" in _tool_names()
    assert "fetch" not in _tool_names(), "read-only 下 fetch 不装配（EXPLORE 没有“询问”这一档）"


def test_ask_writes_mounts_both_tools_and_the_search_level_mounts_only_search(
    library: Any, stub_provider: str
) -> None:
    database, agent, channel = library
    _configure(database, channel, web_access="search", mode="ask-writes", consent=["brave"])
    _run_round(_seam(database), database, agent, channel)
    assert {"web_search"} <= _tool_names()
    assert "fetch" not in _tool_names(), "web_access='search' 不打开 fetch"

    _StubProviderHandler.request_bodies.clear()
    _configure(database, channel, web_access="search+fetch", mode="ask-writes")
    outcome = _run_round(_seam(database), database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert {"web_search", "fetch"} <= _tool_names()
    # 系统提示自陈 fetch 的风险（与 AskEveryTimeBash 在 description 里自陈同一做法）。
    assert "暴露给第三方" in _system_prompt()


def test_an_agent_level_web_access_overrides_the_channel(library: Any, stub_provider: str) -> None:
    """频道关了、Agent 自己开了 ⇒ 这一轮挂联网工具（Agent 级覆盖，ADR-0026 §二）。"""
    database, agent, channel = library
    _configure(
        database,
        channel,
        web_access=None,
        mode="ask-writes",
        consent=["brave"],
        agent_id=agent["id"],
        agent_web_access="search",
    )

    _run_round(_seam(database), database, agent, channel)

    assert "web_search" in _tool_names()


# ─── 3. 数据面确认闸门：缺 ('web', provider) ⇒ 不装配且零请求 ─────────────────
def test_missing_web_consent_mounts_nothing_and_sends_zero_requests(
    library: Any, stub_provider: str, no_outbound_requests: _RecordingTransport
) -> None:
    """没有 ``('web', 'brave')`` 行时，模型**主动调用** `fetch` 也发不出任何请求。"""
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="ask-writes")
    _StubProviderHandler.tool_call = _fetch_tool_call()

    outcome = _run_round(_seam(database), database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert not ({"web_search", "fetch"} & _tool_names())
    assert no_outbound_requests.requests == [], "未确认的数据面一个请求都不许发出"
    assert database.list_approval_requests(channel["id"]) == [], "没挂工具就不会有审批"


def test_the_consent_endpoint_unlocks_the_tools_for_the_same_scope(
    library: Any, stub_provider: str, no_outbound_requests: _RecordingTransport
) -> None:
    """D6：确认行只能由人类面写——调端点前工具不装配，调完同一 scope **立刻**装配。

    这是「ADR-0026 §四 的闸门不是死代码」的可观察判据：同一个库、同一个 provider、同一份
    ``web_access``，唯一的差别是一次 ``POST /api/web-providers/brave/consent``。
    """
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="ask-writes")

    _run_round(_seam(database), database, agent, channel)
    assert not ({"web_search", "fetch"} & _tool_names()), "未确认时不该挂任何联网工具"
    assert no_outbound_requests.requests == []

    with TestClient(create_app(data_dir=Path(database.path).parent)) as client:
        confirmed = client.post("/api/web-providers/brave/consent")
        assert confirmed.status_code == 200, confirmed.text

    _StubProviderHandler.request_bodies.clear()
    outcome = _run_round(_seam(database), database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert {"web_search", "fetch"} <= _tool_names()
    assert "暴露给第三方" in _system_prompt()
    assert no_outbound_requests.requests == [], "只装配、没有调用就不该出网"


def test_a_consented_provider_mounts_the_tools_and_the_probe_is_live(
    library: Any, stub_provider: str, no_outbound_requests: _RecordingTransport
) -> None:
    """同一探针的对照组：确认齐全 + host 放行 ⇒ `fetch` 真的发出 1 条请求。"""
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="ask-writes", consent=["brave"])
    database.create_permission_rule(
        scope_kind="agent",
        scope_id=agent["id"],
        tool_name="fetch",
        pattern="api.example.com",
        behavior="allow",
    )
    _StubProviderHandler.tool_call = _fetch_tool_call()

    outcome = _run_round(_seam(database), database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert [request.url for request in no_outbound_requests.requests] == [FETCH_URL]


# ─── 4. fetch 走既有审批：ASK → 拒绝后 DENIED、本轮继续 ──────────────────────
def _run_with_decision(
    seam: Any,
    database: Any,
    agent: dict,
    channel: dict,
    *,
    confirmed: bool,
    emit: Any = None,
) -> Any:
    """在另一个线程里跑一轮，主线程等审批记录出现后提交决策（同 ``test_approval_seam``）。"""
    result: dict[str, Any] = {}

    def run() -> None:
        result["outcome"] = _run_round(seam, database, agent, channel, emit=emit)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 10
    reply_id = None
    while time.monotonic() < deadline:
        pending = database.list_approval_requests(channel["id"], "pending")
        if pending:
            reply_id = pending[0]["reply_id"]
            break
        time.sleep(0.01)
    assert reply_id is not None, "fetch 没有产生审批记录"
    broker = seam._approvals
    assert broker is not None
    broker.submit(reply_id, [{"tool_call_id": "tc-fetch", "confirmed": confirmed}])
    thread.join(timeout=15)
    assert not thread.is_alive(), "seam 轮次没有收口"
    return result["outcome"]


def test_fetch_asks_and_a_denial_is_a_denied_tool_result_with_the_round_continuing(
    library: Any, no_outbound_requests: _RecordingTransport
) -> None:
    """``ask-writes`` 下调用 `fetch` 产生 ``approval_request``；拒绝后工具没执行、本轮仍收口。"""
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="ask-writes", consent=["brave"])
    events: list[Any] = []
    model = _ScriptedModel([_fetch_call(), _text_reply()])
    seam = _ModelSubstitutingSeam(
        model,
        timeout_seconds=10.0,
        permission_of=database.permission_settings_for_target,
        web_of=database.web_settings_for_target,
        approvals=ApprovalBroker(database, EventBus(), timeout_seconds=5.0),
    )

    outcome = _run_with_decision(seam, database, agent, channel, confirmed=False, emit=events.append)

    assert outcome.status == "replied", outcome.reason
    assert no_outbound_requests.requests == [], "被拒绝的工具不能执行"
    assert [row["state"] for row in database.list_approval_requests(channel["id"])] == ["denied"]
    denied = [
        record.type
        for record in events
        if record.type == "TOOL_RESULT_END" and record.payload.get("state") == "denied"
    ]
    assert denied, [record.type for record in events]


def test_an_allowed_host_skips_the_approval_and_other_hosts_still_ask(
    library: Any, stub_provider: str, no_outbound_requests: _RecordingTransport
) -> None:
    """``permission_rule(fetch, pattern=<host>, allow)`` 放行该 host；别的 host 仍 ASK。"""
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="ask-writes", consent=["brave"])
    database.create_permission_rule(
        scope_kind="agent",
        scope_id=agent["id"],
        tool_name="fetch",
        pattern="api.example.com",
        behavior="allow",
    )
    _StubProviderHandler.tool_call = _fetch_tool_call("https://api.example.com/x")
    # 先跑一轮被放行的：不该出现审批，且真的发出请求。
    outcome = _run_round(_seam(database), database, agent, channel)
    assert outcome.status == "replied", outcome.reason
    assert database.list_approval_requests(channel["id"]) == []
    assert len(no_outbound_requests.requests) == 1

    # 换一个 host：规则不匹配，仍要 ASK（这里直接拒绝掉，不产生第二个请求）。
    other = _ScriptedModel([_fetch_call("https://internal.example.net/x"), _text_reply()])
    seam = _ModelSubstitutingSeam(
        other,
        timeout_seconds=10.0,
        permission_of=database.permission_settings_for_target,
        web_of=database.web_settings_for_target,
        approvals=ApprovalBroker(database, EventBus(), timeout_seconds=5.0),
    )
    outcome = _run_with_decision(seam, database, agent, channel, confirmed=False)
    assert outcome.status == "replied", outcome.reason
    assert [row["state"] for row in database.list_approval_requests(channel["id"])] == ["denied"]
    assert len(no_outbound_requests.requests) == 1, "被拒绝的 host 没有发请求"


# ─── 5. 能力闸门：provider 不能携带工具 ⇒ 不装配联网工具 ────────────────────
class _ScriptedModel(ChatModelBase):
    """按脚本吐块的确定性模型（与 ``test_approval_seam.py`` 同族）：记录每轮收到的工具清单。"""

    def __init__(self, script: Sequence[Sequence[ChatResponse]] | None = None) -> None:
        super().__init__(
            credential=cast(Any, None),
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = (
            list(script)
            if script is not None
            else [[ChatResponse(content=[TextBlock(text=STUB_REPLY)], is_last=True)]]
        )
        self.calls = 0
        self.tool_names: list[set[str]] = []

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.tool_names.append({tool["function"]["name"] for tool in tools or []})
        chunks = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        async def stream() -> Any:
            for chunk in chunks:
                yield chunk

        return stream()


def _text_reply() -> list[ChatResponse]:
    return [ChatResponse(content=[TextBlock(text=STUB_REPLY)], is_last=True)]


def _fetch_call(url: str = FETCH_URL, call_id: str = "tc-fetch") -> list[ChatResponse]:
    raw = json.dumps({"url": url})
    return [ChatResponse(content=[ToolCallBlock(id=call_id, name="fetch", input=raw)], is_last=True)]


class _ModelSubstitutingSeam(AgentScopeAgentSeam):
    """把模型换成进程内替身：能力闸门只能在**模型层**取证（票面要求核实 provider 收到什么）。"""

    def __init__(self, model: ChatModelBase, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model = model

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._model


def test_a_provider_that_cannot_carry_tools_gets_no_web_tool(library: Any) -> None:
    """``supports_tool_calling`` 为假 ⇒ 联网工具与文件工具一样送不出去（票面判据）。"""
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="read-only", consent=["brave"])
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE agent_config SET model_id = 'custom:stub-model' WHERE member_id = ?",
            (agent["id"],),
        )
        connection.commit()
    model = _ScriptedModel()
    seam = _ModelSubstitutingSeam(
        model,
        timeout_seconds=10.0,
        permission_of=database.permission_settings_for_target,
        web_of=database.web_settings_for_target,
    )

    outcome = _run_round(seam, database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert model.tool_names, "模型没被调用：这条判据无从取证"
    assert not ({"web_search", "fetch"} & model.tool_names[0])


# ─── 6. 容器出网策略（#187 / ADR-0026 §五） ────────────────────────────────
# ``backend='docker'`` + ``docker_egress`` 由库读路径带进装配层；这一层没有容器参与（两个工具跑在
# 宿主进程里），因此「默认无网」的可读错误与零请求在这里取证，容器侧的 NetworkMode 由
# ``test_docker_network_policy.py`` 取证（本机无 daemon）。
def _configure_container_egress(database: Any, channel: dict[str, Any], *, egress: bool) -> None:
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE channel SET docker_egress = ?, workspace_backend = 'docker' WHERE id = ?",
            (1 if egress else 0, channel["id"]),
        )
        connection.commit()


def test_docker_container_egress_off_mounts_the_tools_but_sends_zero_requests(
    library: Any, no_outbound_requests: _RecordingTransport
) -> None:
    """默认无网：工具照旧挂（``web_access`` 已开），但每次调用是被拒绝的**可读错误**。"""
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="ask-writes", consent=["brave"])
    _configure_container_egress(database, channel, egress=False)
    # 空 pattern 的 allow = 「这个工具的调用都放行」：权限层不会 ASK，于是这个用例证明的只有
    # 出网策略这一层（不会混淆成「等审批」）。
    database.create_permission_rule(
        scope_kind="channel", scope_id=channel["id"], tool_name="fetch", pattern="", behavior="allow"
    )
    seam = _ModelSubstitutingSeam(
        _ScriptedModel([_fetch_call(), _text_reply()]),
        timeout_seconds=10.0,
        permission_of=database.permission_settings_for_target,
        web_of=database.web_settings_for_target,
    )

    outcome = _run_round(seam, database, agent, channel)

    assert outcome.status == "replied", outcome.reason
    assert no_outbound_requests.requests == [], "容器未允许出网时一个请求都不该发"
    assert database.list_approval_requests(channel["id"]) == []


def test_docker_egress_on_allows_only_the_hosts_in_the_fetch_allow_rules(
    library: Any, no_outbound_requests: _RecordingTransport
) -> None:
    """显式允许：白名单（``fetch`` 的 allow 规则 host）内成功，白名单外仍是零请求。"""
    database, agent, channel = library
    _configure(database, channel, web_access="search+fetch", mode="ask-writes", consent=["brave"])
    _configure_container_egress(database, channel, egress=True)
    database.create_permission_rule(
        scope_kind="channel", scope_id=channel["id"], tool_name="fetch", pattern="", behavior="allow"
    )
    database.create_permission_rule(
        scope_kind="channel",
        scope_id=channel["id"],
        tool_name="fetch",
        pattern="api.example.com",
        behavior="allow",
    )

    allowed = _ModelSubstitutingSeam(
        _ScriptedModel([_fetch_call(FETCH_URL), _text_reply()]),
        timeout_seconds=10.0,
        permission_of=database.permission_settings_for_target,
        web_of=database.web_settings_for_target,
    )
    outcome = _run_round(allowed, database, agent, channel)
    assert outcome.status == "replied", outcome.reason
    assert [request.url for request in no_outbound_requests.requests] == [FETCH_URL]

    outside = _ModelSubstitutingSeam(
        _ScriptedModel([_fetch_call("https://internal.example.net/x"), _text_reply()]),
        timeout_seconds=10.0,
        permission_of=database.permission_settings_for_target,
        web_of=database.web_settings_for_target,
    )
    outcome = _run_round(outside, database, agent, channel)
    assert outcome.status == "replied", outcome.reason
    assert len(no_outbound_requests.requests) == 1, "白名单外的 host 没有发请求"
