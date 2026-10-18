"""#140 流式 seam 的取证：``run_round_stream`` 把 ``AgentEvent`` 收窄成归属完整的记录。

为什么落在真 seam 上：``ScriptedAgentSeam`` 直接给 ``RoundOutcome``，证明不了框架事件真的被
消费、归属真的按 ``reply_id`` 建表、白名单真的挡住了 ``ModelCall*``。本文件因此：

- 用脚本化 ``ChatModelBase``（进程内、不联网、零成本，与 ``issue-132-research/_harness.py``
  同一族做法）驱动真适配器的 ``run_round_stream``，读真实事件序列；
- 用 monkeypatch 替换 ``Agent.reply_stream`` 造孤儿事件，验证「查不到就丢弃 + debug，不猜」；
- 真适配器与脚本化替身各一条，确认 ``run_round`` 的语义没有因为流式出口而改变。

判据对应票面 5 条：seam 存在且 ``run_round`` 委托、五类事件字段齐全（含 task target）、
派生名 + ``reply_id`` 归属、替身支持流式、事件不改变 ``RoundOutcome``。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Any

import pytest
from agentscope.agent import Agent
from agentscope.event import (
    CustomEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
)
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.types import ReplyFinishedReason
from alibot.agent_seam import (
    AgentEventRecord,
    AgentScopeAgentSeam,
    RoundOutcome,
    TaskActionContext,
)

from tests.scripted import ScriptedAgentSeam

AGENT: dict[str, Any] = {
    "id": "mem_140",
    "display_name": "流式成员",
    "role": "只做测试",
    "model_id": "dashscope:stub-model",
    "system_prompt": "只做测试。",
    "enabled": True,
}
MESSAGES: list[dict[str, Any]] = [
    {
        "seq": 1,
        "kind": "human",
        "body": "请回答",
        "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
    }
]


class _ScriptedModel(ChatModelBase):
    """可流式的确定性模型：每次调用按脚本吐一串 ``ChatResponse``。"""

    def __init__(self, script: Sequence[Sequence[ChatResponse]]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = list(script)
        self.calls = 0

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        chunks = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        async def stream() -> AsyncGenerator[ChatResponse, None]:
            for chunk in chunks:
                yield chunk

        return stream()


class _StubModelSeam(AgentScopeAgentSeam):
    """把模型构造换成进程内脚本模型：被测的仍是真适配器的事件消费循环。"""

    def __init__(self, model: ChatModelBase) -> None:
        super().__init__(timeout_seconds=10.0)
        self._model = model
        self.stream_flags: list[bool] = []

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        self.stream_flags.append(stream)
        return self._model


def _tool_call_chunks(call_id: str, name: str, raw: str) -> list[ChatResponse]:
    """把一次工具调用拆成非最终块，框架才会转换成 ``ToolCall*`` 事件。"""
    middle = len(raw) // 2
    return [
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw[:middle])], is_last=False),
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw[middle:])], is_last=False),
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw)], is_last=True),
    ]


def _round_script() -> list[list[ChatResponse]]:
    """一轮里有文本增量、一次工具调用（不存在 → 错误 ToolResult）、再一段最终文本。"""
    raw = json.dumps({"text": "hello"})
    return [
        [
            ChatResponse(content=[TextBlock(text="你好")], is_last=False),
            ChatResponse(content=[TextBlock(text="，世界")], is_last=False),
            *_tool_call_chunks("call-1", "NoSuchTool", raw),
        ],
        [
            ChatResponse(content=[TextBlock(text="已")], is_last=False),
            ChatResponse(content=[TextBlock(text="完成")], is_last=True),
        ],
    ]


def test_stream_emits_whitelisted_records_with_full_attribution() -> None:
    """单 Agent 轮次：五类事件都在，字段齐全，白名单外的 ``ModelCall*`` 不出现。"""
    records: list[AgentEventRecord] = []
    seam = _StubModelSeam(_ScriptedModel(_round_script()))

    outcome = seam.run_round_stream(AGENT, MESSAGES, mentioned=True, target_id="ch_140", emit=records.append)

    types = [record.type for record in records]
    for expected in (
        "REPLY_START",
        "TEXT_BLOCK_START",
        "TEXT_BLOCK_DELTA",
        "TEXT_BLOCK_END",
        "TOOL_CALL_START",
        "TOOL_CALL_DELTA",
        "TOOL_CALL_END",
        "TOOL_RESULT_START",
        "TOOL_RESULT_TEXT_DELTA",
        "TOOL_RESULT_END",
        "REPLY_END",
    ):
        assert expected in types, f"缺少 {expected} 记录：{types}"
    assert "MODEL_CALL_START" not in types
    assert "MODEL_CALL_END" not in types

    # 归属：派生名只用于建表（不放进 payload），每条记录都带同一个 reply_id、成员 id 与本轮 target。
    assert records[0].type == "REPLY_START"
    assert records[0].payload == {"role": "assistant"}
    reply_ids = {record.reply_id for record in records}
    assert len(reply_ids) == 1 and next(iter(reply_ids))
    assert all(record.member_id == "mem_140" for record in records)
    assert all(record.target_id == "ch_140" for record in records)

    # 字段齐全：工具调用/结果的 id 与名字、增量正文、结束原因。
    starts = [record for record in records if record.type == "TOOL_CALL_START"]
    assert starts[0].payload == {"tool_call_id": "call-1", "tool_call_name": "NoSuchTool"}
    assert any(
        record.payload.get("tool_call_id") == "call-1" and record.payload.get("delta")
        for record in records
        if record.type == "TOOL_CALL_DELTA"
    )
    result_end = next(record for record in records if record.type == "TOOL_RESULT_END")
    assert result_end.payload["tool_call_id"] == "call-1"
    assert result_end.payload["state"] == "error"
    reply_end = records[-1]
    assert reply_end.type == "REPLY_END"
    assert reply_end.payload["finished_reason"] == "completed"
    assert [record.payload["delta"] for record in records if record.type == "TEXT_BLOCK_DELTA"][:2] == [
        "你好",
        "，世界",
    ]

    # 流式观察面不改变轮次结论：仍是一轮一条正文。
    assert outcome.status == "replied"
    assert [message.body for message in outcome.messages] == ["完成"]
    assert seam.stream_flags == [True]


def test_records_carry_the_task_target_id() -> None:
    """task target：``target_id`` 是任务 id，不是所属频道；归属字段不变。"""
    records: list[AgentEventRecord] = []
    seam = _StubModelSeam(_ScriptedModel(_round_script()))
    context = TaskActionContext(
        task_id="task_140",
        title="验证流式归属",
        status="in_progress",
        assignee_member_id="mem_other",
        agent_members=(),
    )

    outcome = seam.run_round_stream(
        AGENT,
        MESSAGES,
        mentioned=True,
        target_id="task_140",
        task_context=context,
        emit=records.append,
    )

    assert records, "task 轮次也必须产生事件"
    assert all(record.target_id == "task_140" for record in records)
    assert all(record.member_id == "mem_140" for record in records)
    assert outcome.status == "replied"


def test_orphan_and_unknown_events_are_dropped_with_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """没有 ``ReplyStart`` 的孤儿、查不到的 ``reply_id``、无 ``reply_id`` 的事件都不透传。"""
    derived = "m-mem_140"  # D10：`m-<member id>`（ADR-0018）

    async def fake_reply_stream(
        self: Agent,
        inputs: Any = None,
        structured_schema: Any = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[Any, None]:
        yield ReplyStartEvent(session_id="s", reply_id="r1", name="not-a-member", role="assistant")
        yield TextBlockDeltaEvent(reply_id="r1", block_id="b1", delta="孤儿正文")
        yield CustomEvent(name="noise", value={"secret": "x"})
        yield ReplyStartEvent(session_id="s", reply_id="r2", name=derived, role="assistant")
        yield ModelCallStartEvent(reply_id="r2", model_name="stub-model")
        yield TextBlockDeltaEvent(reply_id="r2", block_id="b2", delta="保留正文")
        yield TextBlockDeltaEvent(reply_id="r3", block_id="b3", delta="未知归属")
        yield ReplyEndEvent(session_id="s", reply_id="r2", finished_reason=ReplyFinishedReason.COMPLETED)
        yield Msg(name=derived, content=[TextBlock(text="保留正文")], role="assistant")

    monkeypatch.setattr(Agent, "reply_stream", fake_reply_stream)
    records: list[AgentEventRecord] = []
    seam = _StubModelSeam(_ScriptedModel(_round_script()))

    with caplog.at_level(logging.DEBUG, logger="alibot.agent_seam"):
        outcome = seam.run_round_stream(
            AGENT, MESSAGES, mentioned=True, target_id="ch_140", emit=records.append
        )

    assert [record.type for record in records] == ["REPLY_START", "TEXT_BLOCK_DELTA", "REPLY_END"]
    assert [record.payload.get("delta") for record in records if record.type == "TEXT_BLOCK_DELTA"] == [
        "保留正文"
    ]
    assert all(record.reply_id == "r2" for record in records)
    assert outcome.status == "replied"
    assert outcome.messages[0].body == "保留正文"

    debug_lines = [line for line in caplog.messages if "agent event" in line]
    assert len(debug_lines) >= 4, caplog.messages


def test_run_round_delegates_to_run_round_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_round`` 是兼容壳：把同一份参数交给 ``run_round_stream``，且 ``emit=None``。"""
    seam = _StubModelSeam(_ScriptedModel(_round_script()))
    captured: list[dict[str, Any]] = []

    def fake_stream(*args: Any, **kwargs: Any) -> RoundOutcome:
        captured.append(kwargs)
        return RoundOutcome(status="ignored", reason="delegated")

    monkeypatch.setattr(seam, "run_round_stream", fake_stream)

    outcome = seam.run_round(AGENT, MESSAGES, mentioned=False, target_id="ch_140")

    assert outcome.reason == "delegated"
    assert captured and captured[0]["emit"] is None


def test_run_round_stream_without_emit_matches_run_round() -> None:
    """不传 ``emit`` 时两条入口的轮次结论完全相同（流式出口是增量，不是新语义）。"""
    plain = _StubModelSeam(_ScriptedModel(_round_script())).run_round(
        AGENT, MESSAGES, mentioned=True, target_id="ch_140"
    )
    streamed = _StubModelSeam(_ScriptedModel(_round_script())).run_round_stream(
        AGENT, MESSAGES, mentioned=True, target_id="ch_140", emit=None
    )

    assert plain == streamed
    assert plain.status == "replied"
    assert [message.body for message in plain.messages] == ["完成"]


def test_scripted_seam_streams_without_changing_the_outcome() -> None:
    """脚本化替身支持流式行为；``run_round`` 的返回值与流式出口一致。"""
    records: list[AgentEventRecord] = []
    seam = ScriptedAgentSeam(replies={"mem_140": "脚本回复"})

    outcome = seam.run_round_stream(
        AGENT, MESSAGES, mentioned=True, target_id="task_140", emit=records.append
    )

    assert [record.type for record in records] == ["REPLY_START", "TEXT_BLOCK_DELTA", "REPLY_END"]
    assert all(record.member_id == "mem_140" for record in records)
    assert all(record.target_id == "task_140" for record in records)
    assert len({record.reply_id for record in records}) == 1
    assert records[1].payload["delta"] == "脚本回复"
    assert outcome.status == "replied"
    assert [message.body for message in outcome.messages] == ["脚本回复"]

    plain = ScriptedAgentSeam(replies={"mem_140": "脚本回复"}).run_round(
        AGENT, MESSAGES, mentioned=True, target_id="task_140"
    )
    assert plain == outcome
