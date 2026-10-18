"""Criterion 1/3 probe: approval waiting, partial decisions and timeout.

Run from the repository root:

    .venv/bin/python app/specs/issue-136-research/probe_broker.py

The probe implements a throwaway in-process ``ApprovalBroker`` prototype (no
product code) on top of the real ``Agent`` HITL protocol, and measures:

1. An approval resolved inside the window resumes the agent normally.
2. A rejection resumes the agent with a ``DENIED`` tool result.
3. Two concurrent ASK calls share one ``reply_id``; a partial decision
   resumes only the confirmed call, and the agent parks again **without**
   re-emitting the remaining ``RequireUserConfirmEvent``. The next resume must
   carry only the still-pending call: replaying a finished call id raises.
4. A timeout (shortened to 0.3s for the probe; the product default is 600s)
   marks the request ``expired`` and the only protocol-level way to close the
   round is ``UserInterruptEvent``.
5. A late decision after expiry is rejected; the state stays ``expired``.
6. A user cancel is the same closure path as a timeout.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import agentscope
from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    ReplyEndEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "issue-132-research"))

from _harness import (
    AskTool,
    ScriptedModel,
    member_toolkit,
    text_chunks,
    tool_call_chunks,
)


class ConcurrentAskTool(AskTool):
    """An ASK tool that may run in the same concurrent batch as a sibling."""

    is_concurrency_safe = True


class ConcurrentAskToolB(ConcurrentAskTool):
    """A second ASK tool name, so batch de-duplication does not merge it."""

    name = "NeedsApprovalB"


@dataclass
class PendingApproval:
    reply_id: str
    tool_calls: dict[str, ToolCallBlock] = field(default_factory=dict)
    decided: dict[str, bool] = field(default_factory=dict)
    state: str = "pending"
    queue: asyncio.Queue[UserConfirmResultEvent | None] = field(default_factory=asyncio.Queue)

    def outcome(self) -> None:
        if set(self.decided) == set(self.tool_calls):
            self.state = "approved" if all(self.decided.values()) else "denied"


class ApprovalBroker:
    """Prototype: one record per parked reply, decisions may arrive in parts."""

    def __init__(self, timeout: float = 600.0) -> None:
        self.timeout = timeout
        self.pending: dict[str, PendingApproval] = {}

    def register(self, event: RequireUserConfirmEvent) -> PendingApproval:
        record = self.pending.setdefault(event.reply_id, PendingApproval(reply_id=event.reply_id))
        for tool_call in event.tool_calls:
            record.tool_calls[tool_call.id] = tool_call
        return record

    async def wait(self, reply_id: str) -> UserConfirmResultEvent | None:
        record = self.pending[reply_id]
        try:
            return await asyncio.wait_for(record.queue.get(), self.timeout)
        except TimeoutError:
            record.state = "expired"
            return None

    def resolve(self, reply_id: str, decisions: dict[str, bool]) -> str:
        """Record a partial/full decision; return the record state."""

        record = self.pending[reply_id]
        if record.state != "pending":
            return record.state
        unknown = set(decisions) - set(record.tool_calls)
        already = set(decisions) & set(record.decided)
        if unknown or already:
            raise ValueError(f"unknown={sorted(unknown)} already_decided={sorted(already)}")
        newly = [
            ConfirmResult(confirmed=confirmed, tool_call=record.tool_calls[tool_call_id])
            for tool_call_id, confirmed in decisions.items()
        ]
        record.decided.update(decisions)
        record.outcome()
        record.queue.put_nowait(
            UserConfirmResultEvent(reply_id=reply_id, confirm_results=newly),
        )
        return record.state

    def cancel(self, reply_id: str) -> None:
        record = self.pending[reply_id]
        if record.state == "pending":
            record.state = "cancelled"
            record.queue.put_nowait(None)


def solo_agent() -> Agent:
    return Agent(
        name="solo",
        system_prompt="probe",
        model=ScriptedModel(
            [
                tool_call_chunks("tc-ask", "NeedsApproval", {"text": "x"}),
                text_chunks([], "solo final"),
            ],
        ),
        toolkit=member_toolkit(AskTool()),
    )


def two_ask_agent() -> Agent:
    first = ToolCallBlock(id="tc-a", name="NeedsApproval", input='{"text": "a"}')
    second = ToolCallBlock(id="tc-b", name="NeedsApprovalB", input='{"text": "b"}')
    return Agent(
        name="solo",
        system_prompt="probe",
        model=ScriptedModel(
            [
                [
                    ChatResponse(content=[first, second], is_last=False),
                    ChatResponse(content=[first, second], is_last=True),
                ],
                text_chunks([], "solo final"),
                text_chunks([], "solo final after partial"),
                text_chunks([], "solo final after second partial"),
            ],
        ),
        toolkit=Toolkit(tools=[ConcurrentAskTool(), ConcurrentAskToolB()]),
    )


async def park(agent: Agent) -> list[RequireUserConfirmEvent]:
    events = [event async for event in agent.reply_stream(UserMsg(name="user", content="go"))]
    return [event for event in events if isinstance(event, RequireUserConfirmEvent)]


def reply_end(events: list[Any]) -> str:
    ends = [event for event in events if isinstance(event, ReplyEndEvent)]
    return str(ends[-1].finished_reason) if ends else "none"


def context_states(agent: Agent) -> list[str]:
    states: list[str] = []
    for message in agent.state.context:
        for block in message.get_content_blocks("tool_result"):
            states.append(str(block.state))
    return states


async def scenario_decision(confirmed: bool) -> None:
    label = "approve" if confirmed else "deny"
    agent = solo_agent()
    requires = await park(agent)
    broker = ApprovalBroker(timeout=5.0)
    record = broker.register(requires[0])
    waiter = asyncio.create_task(broker.wait(record.reply_id))
    await asyncio.sleep(0.05)
    state = broker.resolve(record.reply_id, {requires[0].tool_calls[0].id: confirmed})
    decision_event = await waiter
    assert decision_event is not None
    events = [event async for event in agent.reply_stream(decision_event)]
    print(f"[{label}] state={state}")
    print(f"[{label}] reply_end={reply_end(events)} tool_results={context_states(agent)}")


async def scenario_partial_batch() -> None:
    agent = two_ask_agent()
    requires = await park(agent)
    print(f"[partial] require_events={len(requires)} reply_ids={len({_.reply_id for _ in requires})}")
    broker = ApprovalBroker(timeout=5.0)
    for event in requires:
        broker.register(event)
    record = broker.pending[requires[0].reply_id]
    print(f"[partial] pending_tool_calls={sorted(record.tool_calls)}")

    state = broker.resolve(record.reply_id, {"tc-a": True})
    partial_event = await broker.wait(record.reply_id)
    assert partial_event is not None
    first_resume = [event async for event in agent.reply_stream(partial_event)]
    print(
        f"[partial] state={state} first_resume_events={len(first_resume)} "
        f"require_again={sum(isinstance(_, RequireUserConfirmEvent) for _ in first_resume)}",
    )
    print(
        f"[partial] first_resume_reply_end={reply_end(first_resume)} "
        f"still_awaiting={agent.state.has_awaiting_tool_calls(agent.name)}",
    )

    replay = UserConfirmResultEvent(
        reply_id=record.reply_id,
        confirm_results=[
            ConfirmResult(confirmed=True, tool_call=record.tool_calls["tc-a"]),
            ConfirmResult(confirmed=True, tool_call=record.tool_calls["tc-b"]),
        ],
    )
    try:
        async for _ in agent.reply_stream(replay):
            pass
    except ValueError as exc:
        print(f"[partial] replay_finished_call_error={str(exc).split(', but received')[0]}")

    state = broker.resolve(record.reply_id, {"tc-b": True})
    final_event = await broker.wait(record.reply_id)
    assert final_event is not None
    second_resume = [event async for event in agent.reply_stream(final_event)]
    print(f"[partial] state={state} second_resume_reply_end={reply_end(second_resume)}")
    print(f"[partial] tool_results={context_states(agent)}")

    try:
        broker.resolve(record.reply_id, {"tc-b": True})
    except ValueError as exc:
        print(f"[partial] replay_error={exc}")


async def scenario_timeout() -> None:
    agent = solo_agent()
    requires = await park(agent)
    broker = ApprovalBroker(timeout=0.3)
    record = broker.register(requires[0])
    started = asyncio.get_running_loop().time()
    decision_event = await broker.wait(record.reply_id)
    elapsed = asyncio.get_running_loop().time() - started
    print(f"[timeout] elapsed_seconds={elapsed:.2f} decision_event={decision_event} state={record.state}")
    late = broker.resolve(record.reply_id, {requires[0].tool_calls[0].id: True})
    print(f"[timeout] late_resolve_state={late}")
    events = [
        event
        async for event in agent.reply_stream(
            UserInterruptEvent(reply_id=record.reply_id),
        )
    ]
    print(f"[timeout] interrupt_reply_end={reply_end(events)} tool_results={context_states(agent)}")
    print(f"[timeout] parked_after={agent.state.has_awaiting_tool_calls(agent.name)}")


async def scenario_cancel() -> None:
    agent = solo_agent()
    requires = await park(agent)
    broker = ApprovalBroker(timeout=5.0)
    record = broker.register(requires[0])
    waiter = asyncio.create_task(broker.wait(record.reply_id))
    await asyncio.sleep(0.05)
    broker.cancel(record.reply_id)
    decision_event = await waiter
    print(f"[cancel] decision_event={decision_event} state={record.state}")
    events = [
        event
        async for event in agent.reply_stream(
            UserInterruptEvent(reply_id=record.reply_id),
        )
    ]
    print(
        f"[cancel] interrupt_reply_end={reply_end(events)} "
        f"parked_after={agent.state.has_awaiting_tool_calls(agent.name)}",
    )


async def main() -> None:
    print(f"agentscope_version={agentscope.__version__}")
    await scenario_decision(confirmed=True)
    await scenario_decision(confirmed=False)
    await scenario_partial_batch()
    await scenario_timeout()
    await scenario_cancel()


if __name__ == "__main__":
    asyncio.run(main())
