"""#219 一轮落库正文 = 这一轮全部用户可见 `text` 块按序拼接（不是最后一次模型调用）。

为什么落在**真 seam** 上：``ScriptedAgentSeam`` 直接返回一个 ``RoundOutcome``，它证明得了编排与落库，
但证明不了「一轮里模型写了什么、我们怎么把它收成一条正文」——那件事只在
``AgentScopeAgentSeam.run_round`` 里。票面因此要求在真 seam 上复现，而不是假定拼接逻辑正确。

为什么不需要真模型、不联网：进程内 stub provider（``127.0.0.1:0``）按用例回固定的 OpenAI 兼容响应。
第一次模型调用回「正文 A + 一个工具调用」，第二次回「正文 B」——于是这一轮里模型写了**两段**正文，
而最终 ``Msg`` 只带第二次调用的块。修复前落库正文只有 B，A 静默消失（生产库里的「以上是首版进展简报」就是 A）。

判据（与票面的 Observable acceptance 一一对应）：

1. 两段正文都落库，顺序是「A 在前、B 在后」，中间用空行分隔（不是粘成一句）；
2. 单段正文的轮次逐字不变（拼接不得给普通回复加上首尾空白或多余空行）；
3. 只有工具块、没有任何正文的一轮仍然没有正文 ⇒ 既有的 ``ignored`` 不变；
4. 结构化声明沉默仍零条待落库消息，工具之后写的那段正文也**不因拼接而复活**；
5. ``exceed_max_iters`` 分支走同一个提取器：落库的是这一轮的真实正文，不是框架的合成占位句。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from alibot.agent_seam import SILENCE_TOOL_NAME, AgentScopeAgentSeam

# 两次模型调用的两段正文。用**可区分**的句子而不是同一个占位串：否则「把 B 拼了两遍」也会通过。
FIRST_BODY = "【首版进展简报】这是我这一轮的实质正文。"
LAST_BODY = "上面就是这轮的实质进展简报，已提交审阅。"


# 每段正文的首尾空白由用例自己决定：判据 3 要求「每段先 strip()、空段跳过、不产生连续空行」，
# 所以探针必须能把带空白的段送进去，否则这条判据没有读数面。
class _StubProviderHandler(BaseHTTPRequestHandler):
    """进程内 provider 端点：OpenAI 兼容、keep-alive；前 N 次模型调用回正文 + 工具调用，之后回收尾正文。"""

    protocol_version = "HTTP/1.1"

    request_bodies: ClassVar[list[dict[str, Any]]] = []
    # 工具调用响应里的工具名与实参（``None`` 表示这一轮不产生工具调用）。
    tool_call: ClassVar[dict[str, Any] | None] = None
    # 前几次模型调用返回工具调用（Agent 的工具循环会再请求一次）。
    tool_rounds: ClassVar[int] = 1
    # 第一次模型调用的工具调用是否也带正文（工具调用 + 正文同时出现，正是 #219 的现场形态）。
    first_has_body: ClassVar[bool] = True
    # 收尾正文；``None`` 表示收尾那次调用什么都不写（模拟空正文）。
    last_text: ClassVar[str | None] = LAST_BODY
    # 两次调用正文的**原样**前后缀（判据 3 的白位面：拼接必须先 strip 再连）。
    first_pad: ClassVar[tuple[str, str]] = ("", "")
    last_pad: ClassVar[tuple[str, str]] = ("", "")
    calls: ClassVar[int] = 0

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        type(self).calls += 1
        call = type(self).tool_call
        if call is not None and type(self).calls <= type(self).tool_rounds:
            has_body = type(self).first_has_body and type(self).calls == 1
            self._send(
                {
                    "role": "assistant",
                    "content": (
                        f"{type(self).first_pad[0]}{FIRST_BODY}{type(self).first_pad[1]}"
                        if has_body
                        else None
                    ),
                    "tool_calls": [
                        {
                            "id": f"call-issue-219-{type(self).calls}",
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                    ],
                },
                "tool_calls",
            )
            return
        if type(self).last_text is None:
            self._send({"role": "assistant", "content": ""}, "stop")
            return
        self._send(
            {
                "role": "assistant",
                "content": f"{type(self).last_pad[0]}{type(self).last_text}{type(self).last_pad[1]}",
            },
            "stop",
        )

    def _send(self, message: dict[str, Any], finish_reason: str) -> None:
        payload = {
            "id": "chatcmpl-issue-219",
            "object": "chat.completion",
            "created": 0,
            "model": "stub-model",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
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
    _StubProviderHandler.tool_rounds = 1
    _StubProviderHandler.first_has_body = True
    _StubProviderHandler.last_text = LAST_BODY
    _StubProviderHandler.first_pad = ("", "")
    _StubProviderHandler.last_pad = ("", "")
    _StubProviderHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubProviderHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    # ``trust_env`` 关在 provider 客户端内部，这里只把「它会去连哪里」指到本机 stub。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-issue-219-stub")
    monkeypatch.setenv("OPENAI_BASE_URL", base_url)
    monkeypatch.delenv("OPENAI_PROXY", raising=False)
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _agent() -> dict[str, Any]:
    return {
        "id": "mem_issue_219",
        "display_name": "小红",
        "role": "调研员",
        "model_id": "openai:stub-model",
        "system_prompt": "先查资料，再给结论。",
        "enabled": True,
    }


def _messages() -> list[dict[str, Any]]:
    return [
        {
            "seq": 1,
            "kind": "human",
            "body": "给我一份进展简报。",
            "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
        }
    ]


def _task_context() -> Any:
    from alibot.agent_seam import TaskActionContext

    return TaskActionContext(
        task_id="task_issue_219",
        title="进展简报",
        status="in_progress",
        assignee_member_id="mem_issue_219",
        agent_members=(),
    )


def _run(agent: dict[str, Any] | None = None) -> Any:
    return AgentScopeAgentSeam(timeout_seconds=10.0).run_round(
        agent or _agent(),
        _messages(),
        mentioned=True,
        target_id="task_issue_219",
        task_context=_task_context(),
    )


# ─── 1. 工具调用前后的两段正文都要落库，顺序正确、空行分隔 ─────────────────
def test_a_round_keeps_every_user_visible_paragraph_in_model_order(stub_provider: str) -> None:
    """两次调用：第一次正文 A + 工具调用，第二次正文 B ⇒ 落库正文里 A 在 B 之前。"""
    _StubProviderHandler.tool_call = {"name": "set_task_status", "arguments": {"status": "in_review"}}

    outcome = _run()

    assert outcome.status == "replied", outcome.reason
    assert len(outcome.messages) == 1, "一轮仍然只落一条消息（CONTEXT.md 的硬约定）"
    body = outcome.messages[0].body
    assert FIRST_BODY in body, f"工具调用前那段正文被丢掉了，实际落库正文={body!r}"
    assert LAST_BODY in body
    assert body.index(FIRST_BODY) < body.index(LAST_BODY), "拼接顺序必须是模型调用顺序"
    assert body == f"{FIRST_BODY}\n\n{LAST_BODY}", "段与段之间用空行分隔，且不产生首尾空白"
    # 协议约束没变：动作仍是这一轮唯一的结构化结果。
    assert getattr(outcome.task_action, "status", None) == "in_review"


# ─── 2. 单段正文逐字不变（拼接不得给普通回复加空行） ───────────────────────
def test_a_single_paragraph_round_is_byte_for_byte_unchanged(stub_provider: str) -> None:
    """没有工具调用的一轮只有一次模型调用：正文逐字落库，没有尾部空行或额外分隔。"""
    outcome = _run()

    assert outcome.status == "replied", outcome.reason
    assert outcome.messages[0].body == LAST_BODY


# ─── 2b. 判据 3：每段先 strip、空段跳过、不产生连续空行或首尾空白 ───────────
def test_paragraphs_are_stripped_and_blank_ones_dropped(stub_provider: str) -> None:
    """两段正文都带首尾空白 ⇒ 落库的是 trim 过的段，中间**恰好**一个空行。"""
    _StubProviderHandler.tool_call = {"name": "set_task_status", "arguments": {"status": "in_review"}}
    _StubProviderHandler.first_pad = ("\n\n  ", "  \n")
    _StubProviderHandler.last_pad = ("\n   ", "\n\n")

    outcome = _run()

    assert outcome.status == "replied", outcome.reason
    body = outcome.messages[0].body
    assert body == f"{FIRST_BODY}\n\n{LAST_BODY}", repr(body)
    assert not body.startswith((" ", "\n")) and not body.endswith((" ", "\n"))
    assert "\n\n\n" not in body


# ─── 3. 只有工具块、没有正文 ⇒ 仍然没有正文（既有 task_action_only / ignored） ─
def test_a_round_with_only_tool_blocks_has_no_body(stub_provider: str) -> None:
    """两次调用都写不出正文（工具调用 + 空收尾）⇒ 落库仍零条消息，动作-only 收口。"""
    _StubProviderHandler.tool_call = {"name": "set_task_status", "arguments": {"status": "in_review"}}
    _StubProviderHandler.first_has_body = False
    _StubProviderHandler.last_text = None

    outcome = _run()

    assert outcome.status == "replied", outcome.reason
    assert outcome.messages == [], "没有 text 块就不该凭空拼出正文"
    assert getattr(outcome.task_action, "status", None) == "in_review"


# ─── 4. 结构化沉默仍零条消息：拼接不复活工具之后写的正文 ───────────────────
def test_a_declared_silence_stays_silent_even_if_the_model_wrote_paragraphs(
    stub_provider: str,
) -> None:
    """声明沉默 + 两段正文 ⇒ 仍 ``silent``、零条待落库消息（声明的语义优先）。"""
    _StubProviderHandler.tool_call = {"name": SILENCE_TOOL_NAME, "arguments": {}}

    outcome = _run()

    assert outcome.status == "silent", outcome.reason
    assert outcome.messages == [], "声明沉默就是零条待落库消息，拼接不得让它复活"
    assert outcome.task_action is None


# ─── 5. exceed_max_iters 分支走同一个提取器 ────────────────────────────────
def test_the_exceed_max_iters_branch_also_keeps_the_earlier_paragraph(stub_provider: str) -> None:
    """迭代上限收口：工具调用前那段正文同样落库（旧实现只留框架收尾那次调用）。"""
    _StubProviderHandler.tool_call = {"name": "set_task_status", "arguments": {"status": "in_review"}}
    agent = _agent()
    agent["react_max_iters"] = 1

    outcome = _run(agent)

    assert outcome.finished_reason == "exceed_max_iters", outcome.finished_reason
    assert outcome.status == "replied", outcome.reason
    body = outcome.messages[0].body
    assert FIRST_BODY in body, f"迭代上限分支丢了前面的正文，实际落库正文={body!r}"
    assert LAST_BODY in body
    assert body.index(FIRST_BODY) < body.index(LAST_BODY)


# ─── 6. 队长轮次同一条通道：队长的累积消息才是本轮正本 ─────────────────────
def test_the_leader_round_also_keeps_every_paragraph(stub_provider: str) -> None:
    """队长轮次（``run_round_stream`` + ``team``）：队长的正文同样按序拼齐全。

    单 Agent 与队长共用 :meth:`AgentScopeAgentSeam._outcome_from_reply`，但两者的正本不是同一条
    消息：队长的是**队长自己**那条累积 assistant 消息，成员的消息只作为 ``ToolResult`` 回到队长、
    不落 target 消息（D2）。因此这里用没有成员的 team 快照，验证「通道同一条、正本取对人」。
    """
    from alibot.team import TeamRoundContext

    _StubProviderHandler.tool_call = {"name": "set_task_status", "arguments": {"status": "in_review"}}
    agent = _agent()
    team = TeamRoundContext(target_id="task_issue_219", leader_id=agent["id"], members=())

    outcome = AgentScopeAgentSeam(timeout_seconds=10.0).run_round_stream(
        agent,
        _messages(),
        mentioned=True,
        target_id="task_issue_219",
        task_context=_task_context(),
        team=team,
        emit=None,
    )

    assert outcome.status == "replied", outcome.reason
    assert len(outcome.messages) == 1, "队长轮次同样至多一条消息"
    assert outcome.messages[0].body == f"{FIRST_BODY}\n\n{LAST_BODY}"


# ─── 7. 空正文 + 无声明 ⇒ 仍是既有的 ``ignored``（拼接不得凭空造正文） ────────
def test_an_empty_round_without_a_declaration_is_still_ignored(stub_provider: str) -> None:
    """频道轮次（没有任务动作通道）里模型什么都没写 ⇒ ``ignored``、零条消息。"""
    _StubProviderHandler.last_text = None

    outcome = AgentScopeAgentSeam(timeout_seconds=10.0).run_round(
        _agent(), _messages(), mentioned=False, target_id="ch_issue_219"
    )

    assert outcome.status == "ignored", outcome.reason
    assert outcome.messages == []


# ─── 8. 纯 thinking 不进正文：只有 ``text`` 块算用户可见正文 ────────────────
def test_a_thinking_block_never_becomes_body() -> None:
    """同一轮里既有 ``ThinkingBlock`` 又有 ``TextBlock`` ⇒ 正文只有那段 text。

    这一条只能靠脚本化模型取证：OpenAI 兼容传输会把 ``ThinkingBlock`` 静默丢掉
    （``_openai_formatter`` 明写「OpenAI API does not accept reasoning content」），
    stub provider 送不进去。脚本化模型是 ``test_approval_seam.py`` 的既有装置，这里直接复用。
    """
    from agentscope.message import TextBlock, ThinkingBlock
    from agentscope.model import ChatResponse

    from tests.test_approval_seam import _ScriptedModel, _StubModelSeam

    model = _ScriptedModel(
        [
            [
                ChatResponse(
                    content=[ThinkingBlock(thinking="先想想怎么回。"), TextBlock(text=LAST_BODY)],
                    is_last=True,
                )
            ]
        ]
    )
    seam = _StubModelSeam(model, timeout_seconds=10.0)

    outcome = seam.run_round(_agent(), _messages(), mentioned=True, target_id="ch_issue_219")

    assert outcome.status == "replied", outcome.reason
    assert outcome.messages[0].body == LAST_BODY, "thinking 块不是用户可见正文"
