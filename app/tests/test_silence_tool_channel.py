"""#58 结构化沉默通道的**真实 seam** 取证：``declare_silence`` 工具 → ``RoundOutcome(status="silent")``。

为什么必须落在真 seam 上：``ScriptedAgentSeam`` 直接返回一个 ``RoundOutcome``，它证明得了编排与落库，
但**证明不了这条结构化通道真的存在**——工具 schema 有没有送到 provider、模型调了工具之后我们怎么
收口，都只在 ``AgentScopeAgentSeam.run_round`` 里。票面因此要求「核实 API、验证 provider 能力差异」，
而不是假定所有 provider 一致。

为什么不需要真模型、不联网：stub 端点跑在进程内的 ``http.server``（``127.0.0.1:0``），按用例回一份
固定的 OpenAI 兼容响应——要么回一个 ``declare_silence`` 的工具调用（模拟**支持**工具调用的
provider/模型），要么只回正文（模拟**不产出**工具调用的一侧）。这与
``test_agent_seam_client_lifecycle.py`` 的 stub 是同一族做法（本机、零成本、进默认 pytest 安全）。

三条判据：

1. 支持工具调用 ⇒ 请求里真的有 ``declare_silence`` 的 schema，模型调用它即落 ``silent``，且
   **零条**待落库消息（即便模型在工具之后又写了一段正文，那段也不落库：声明与正文互不推演）；
2. 不支持工具调用 ⇒ 这一轮**根本不挂**沉默工具（请求里没有 ``tools`` / prompt 里没有它），
   于是结论只可能来自正文，即既有的 ``replied`` / ``ignored``——退化是结构性的；
3. 模型没调用工具时，行为与 #58 之前逐字相同（正文照常落库）。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from alibot.agent_seam import SILENCE_TOOL_NAME, AgentScopeAgentSeam

STUB_TEXT = "这是一条普通回复。"


class _StubProviderHandler(BaseHTTPRequestHandler):
    """进程内 provider 端点：OpenAI 兼容、keep-alive；按用例决定第一轮是不是工具调用。"""

    protocol_version = "HTTP/1.1"

    request_bodies: ClassVar[list[dict[str, Any]]] = []
    # 有值时首轮返回该工具调用，之后的轮次回固定正文（Agent 的工具循环会再来一次）。
    tool_call: ClassVar[dict[str, Any] | None] = None
    tool_call_sent: ClassVar[bool] = False
    # 非 0 时端点只回空正文（模拟「模型什么都没写」）。
    empty_content: ClassVar[bool] = False

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        if type(self).tool_call is not None and not type(self).tool_call_sent:
            type(self).tool_call_sent = True
            call = type(self).tool_call
            assert call is not None
            self._send_json(
                {
                    "id": "chatcmpl-issue-58-tool",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "stub-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-declare-silence",
                                        "type": "function",
                                        "function": {
                                            "name": call["name"],
                                            "arguments": json.dumps(call["arguments"]),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
            return
        self._send_json(
            {
                "id": "chatcmpl-issue-58-text",
                "object": "chat.completion",
                "created": 0,
                "model": "stub-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "" if type(self).empty_content else STUB_TEXT,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    def _send_json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """本机 stub provider：把 ``OPENAI_BASE_URL`` 指过去，凭证用一个假 key。"""
    _StubProviderHandler.request_bodies = []
    _StubProviderHandler.tool_call = None
    _StubProviderHandler.tool_call_sent = False
    _StubProviderHandler.empty_content = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubProviderHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    # ``trust_env`` 关在 provider 客户端内部，这里只把「它会去连哪里」指到本机 stub。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-issue-58-stub")
    monkeypatch.setenv("OPENAI_BASE_URL", base_url)
    monkeypatch.delenv("OPENAI_PROXY", raising=False)
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def round_inputs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    agent = {
        "id": "mem_issue_58",
        "display_name": "小明",
        "role": "前端工程师",
        "model_id": "openai:stub-model",
        "system_prompt": "你只回一句极短的确认。",
        "enabled": True,
    }
    messages = [
        {
            "seq": 1,
            "kind": "human",
            "body": "随口聊两句，今天天气不错。",
            "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
        }
    ]
    return agent, messages


def _tool_names(request_body: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in request_body.get("tools") or []}


class _StubTransportSeam(AgentScopeAgentSeam):
    """把 provider 名留在闸门之外的 seam，模型客户端仍指向本机 stub（只为一侧退化可观察）。

    生产里「闸门」与「传输层」读同一张表（``PROVIDER_KEY_ENV``），因此今天没有任何一家真的落在
    闸门之外。要观察「闸门为假时请求里没有 ``tools``」就只能把这两层临时拆开：provider 名交给
    闸门判（不在表内 ⇒ 假），模型构造点接到 stub 的 OpenAI 兼容传输上，于是请求真的发得出去、
    也真的可以被抓下来读。断言仍然只落在**抓到的请求载荷**与**轮次结果**上。
    """

    def _build_model(self, provider: str, model_name: str) -> Any:
        return super()._build_model("openai", model_name)


# ─── 1. 支持工具调用 ⇒ 能声明 silent，且零条待落库消息 ────────────────────
def test_a_declared_silence_becomes_a_silent_outcome_without_any_message(stub_provider: str) -> None:
    """模型调用 ``declare_silence`` ⇒ ``silent`` + 零条 messages；工具之后写的正文也不落库。"""
    _StubProviderHandler.tool_call = {"name": SILENCE_TOOL_NAME, "arguments": {}}
    agent, messages = round_inputs()

    outcome = AgentScopeAgentSeam(timeout_seconds=10.0).run_round(
        agent, messages, mentioned=False, target_id="ch_issue_58"
    )

    assert outcome.status == "silent", outcome.reason
    assert outcome.messages == [], "声明沉默就是零条待落库消息（D0-d）"
    assert outcome.reason.strip(), "reason 仍要留下可读诊断"
    assert outcome.task_action is None

    first = _StubProviderHandler.request_bodies[0]
    assert SILENCE_TOOL_NAME in _tool_names(first), "结构化通道必须真的出现在 provider 请求里"
    assert _StubProviderHandler.tool_call_sent, "stub 确实回过一次工具调用（否则本用例没有取证到通道）"
    # 说明书进了 system prompt（模型得知道有这个出口），但不改变 #57 的回应纪律那一段。
    assert SILENCE_TOOL_NAME in json.dumps(first, ensure_ascii=False)
    assert "回应纪律" in json.dumps(first, ensure_ascii=False)


# ─── 2. 闸门为假的 provider ⇒ 结构性退化（请求里看不到工具） ──────────────
def test_a_provider_outside_the_gate_degrades_to_the_existing_outcomes(stub_provider: str) -> None:
    """闸门关掉 ⇒ 请求里既没有 ``tools`` 也没有工具说明书 ⇒ 只能得到既有的 ``replied``。"""
    agent, messages = round_inputs()
    agent["model_id"] = "no-such-provider:stub-model"

    outcome = _StubTransportSeam(timeout_seconds=10.0).run_round(
        agent, messages, mentioned=False, target_id="ch_issue_58"
    )

    assert outcome.status == "replied", outcome.reason
    assert len(outcome.messages) == 1 and STUB_TEXT in outcome.messages[0].body
    assert len(_StubProviderHandler.request_bodies) == 1, "退化路径不再有工具循环的第二跳"
    first = _StubProviderHandler.request_bodies[0]
    assert "tools" not in first, "不挂工具时请求里不能出现 tools（否则退化只是嘴上说说）"
    assert SILENCE_TOOL_NAME not in json.dumps(first, ensure_ascii=False)


def test_an_unavailable_provider_never_reaches_the_transport(stub_provider: str) -> None:
    """本仓不认识的 provider 连客户端都建不出来：这一轮直接失败，一个请求都发不出去。"""
    agent, messages = round_inputs()
    agent["model_id"] = "no-such-provider:stub-model"

    outcome = AgentScopeAgentSeam(timeout_seconds=10.0).run_round(
        agent, messages, mentioned=False, target_id="ch_issue_58"
    )

    assert outcome.status == "failed", outcome.reason
    assert _StubProviderHandler.request_bodies == [], "没有传输层 ⇒ 根本不会有请求，更不会有 tools 键"


# ─── 3. 模型没调用工具 ⇒ 行为与 #58 之前逐字相同 ───────────────────────────
def test_a_model_that_does_not_call_the_tool_keeps_the_plain_reply(stub_provider: str) -> None:
    """正文照常落库（silent 不是「看到工具就默认沉默」）。"""
    agent, messages = round_inputs()

    outcome = AgentScopeAgentSeam(timeout_seconds=10.0).run_round(
        agent, messages, mentioned=False, target_id="ch_issue_58"
    )

    assert outcome.status == "replied", outcome.reason
    assert outcome.messages and outcome.messages[0].body == STUB_TEXT
    assert SILENCE_TOOL_NAME in _tool_names(_StubProviderHandler.request_bodies[0])


def test_an_empty_body_without_a_declaration_is_still_ignored(stub_provider: str) -> None:
    """空正文 + 没有声明 ⇒ 仍是既有的 ``ignored``（``silent`` 只能由结构化声明产生）。"""
    _StubProviderHandler.empty_content = True
    agent, messages = round_inputs()

    outcome = AgentScopeAgentSeam(timeout_seconds=10.0).run_round(
        agent, messages, mentioned=False, target_id="ch_issue_58"
    )

    assert outcome.status == "ignored", outcome.reason
    assert outcome.messages == []
