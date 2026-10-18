"""Criterion 1 probe: a single agent parks on ASK and resumes by decision.

Run from the repository root:

    .venv/bin/python app/specs/issue-136-research/probe_single_agent.py

Scenarios (all in-process, no network, deterministic scripted model):

1. ``confirmed=True``: the stream continues, the tool runs, the final message
   and a ``ReplyEndEvent(COMPLETED)`` are produced.
2. ``confirmed=False``: the tool result is ``ToolResultState.DENIED``; the
   model is called again and the round can close normally.
3. A confirmation carrying the wrong ``reply_id`` still resumes: ``Agent``
   validates tool-call ids, not ``reply_id``; only ``TeamPipeline`` routes by
   ``reply_id``.
4. A second decision after the round closed raises ``ValueError``.
5. ``UserInterruptEvent`` closes the parked call as ``INTERRUPTED`` and ends
   the reply as ``INTERRUPTED`` with a fallback message; the next message
   starts a fresh reply (no stuck state).
6. ``UserInterruptEvent`` on an idle agent is a silent no-op.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import agentscope
from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    ToolResultEndEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import Msg, UserMsg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "issue-132-research"))

from _harness import (
    AskTool,
    ScriptedModel,
    member_toolkit,
    text_chunks,
    tool_call_chunks,
)


def build_agent() -> Agent:
    """One agent whose first scripted response asks for a tool call."""

    return Agent(
        name="solo",
        system_prompt="probe",
        model=ScriptedModel(
            [
                tool_call_chunks("tc-ask", "NeedsApproval", {"text": "x"}),
                text_chunks([], "solo final"),
                text_chunks([], "solo final again"),
            ],
        ),
        toolkit=member_toolkit(AskTool()),
    )


def event_counts(events: list[Any]) -> str:
    kinds = Counter(type(event).__name__ for event in events)
    return ",".join(f"{name}={count}" for name, count in sorted(kinds.items()))


def final_text(events: list[Any]) -> str:
    texts = [
        event.get_text_content() for event in events if isinstance(event, Msg) and event.get_text_content()
    ]
    return texts[-1] if texts else ""


def tool_result_states(agent: Agent) -> list[str]:
    states: list[str] = []
    for message in agent.state.context:
        for block in message.get_content_blocks("tool_result"):
            states.append(str(block.state))
    return states


def parked(agent: Agent) -> bool:
    return agent.state.has_awaiting_tool_calls(agent.name)


def first_confirm(events: list[Any]) -> RequireUserConfirmEvent:
    confirms = [event for event in events if isinstance(event, RequireUserConfirmEvent)]
    assert confirms, "expected a RequireUserConfirmEvent"
    return confirms[0]


async def resume(
    agent: Agent,
    reply_id: str,
    confirmed: bool,
    tool_call: Any,
) -> list[Any]:
    return [
        event
        async for event in agent.reply_stream(
            UserConfirmResultEvent(
                reply_id=reply_id,
                confirm_results=[ConfirmResult(confirmed=confirmed, tool_call=tool_call)],
            ),
            yield_final_msg=True,
        )
    ]


async def scenario(confirmed: bool) -> None:
    label = "approve" if confirmed else "deny"
    agent = build_agent()
    phase1 = [event async for event in agent.reply_stream(UserMsg(name="user", content="go"))]
    require = first_confirm(phase1)
    print(f"[{label}] phase1_events={len(phase1)} last={type(phase1[-1]).__name__}")
    print(f"[{label}] phase1_has_reply_end={any(isinstance(_, ReplyEndEvent) for _ in phase1)}")
    print(f"[{label}] require_tool_calls={[_.id for _ in require.tool_calls]}")
    print(f"[{label}] require_reply_id_is_agent={require.reply_id == agent.state.reply_id}")
    print(f"[{label}] parked_after_phase1={parked(agent)}")

    phase2 = await resume(agent, require.reply_id, confirmed, require.tool_calls[0])
    ends = [event for event in phase2 if isinstance(event, ReplyEndEvent)]
    tool_ends = [event for event in phase2 if isinstance(event, ToolResultEndEvent)]
    print(f"[{label}] phase2_events={len(phase2)} counts={event_counts(phase2)}")
    print(f"[{label}] phase2_tool_result_states={[str(_.state) for _ in tool_ends]}")
    print(f"[{label}] phase2_reply_end={[str(_.finished_reason) for _ in ends]}")
    print(f"[{label}] phase2_final_text={final_text(phase2)!r}")
    print(f"[{label}] context_tool_result_states={tool_result_states(agent)}")
    print(f"[{label}] parked_after_phase2={parked(agent)}")

    try:
        await resume(agent, require.reply_id, confirmed, require.tool_calls[0])
    except ValueError as exc:
        print(f"[{label}] duplicate_decision_error={str(exc).split(', but received')[0]}")


async def scenario_wrong_reply_id() -> None:
    agent = build_agent()
    phase1 = [event async for event in agent.reply_stream(UserMsg(name="user", content="go"))]
    require = first_confirm(phase1)
    phase2 = await resume(agent, "not-the-reply-id", True, require.tool_calls[0])
    ends = [event for event in phase2 if isinstance(event, ReplyEndEvent)]
    print(f"[wrong-reply-id] phase2_events={len(phase2)}")
    print(f"[wrong-reply-id] reply_end={[str(_.finished_reason) for _ in ends]}")
    print(f"[wrong-reply-id] final_text={final_text(phase2)!r}")


async def scenario_interrupt() -> None:
    agent = build_agent()
    phase1 = [event async for event in agent.reply_stream(UserMsg(name="user", content="go"))]
    require = first_confirm(phase1)
    phase2 = [
        event
        async for event in agent.reply_stream(
            UserInterruptEvent(reply_id=require.reply_id),
            yield_final_msg=True,
        )
    ]
    ends = [event for event in phase2 if isinstance(event, ReplyEndEvent)]
    tool_ends = [event for event in phase2 if isinstance(event, ToolResultEndEvent)]
    print(f"[interrupt] phase2_events={len(phase2)} counts={event_counts(phase2)}")
    print(f"[interrupt] tool_result_states={[str(_.state) for _ in tool_ends]}")
    print(f"[interrupt] reply_end={[str(_.finished_reason) for _ in ends]}")
    print(f"[interrupt] fallback_text={final_text(phase2)!r}")
    print(f"[interrupt] parked_after={parked(agent)}")
    print(f"[interrupt] context_tool_result_states={tool_result_states(agent)}")

    phase3 = [event async for event in agent.reply_stream(UserMsg(name="user", content="again"))]
    starts = [event for event in phase3 if isinstance(event, ReplyStartEvent)]
    print(f"[interrupt] next_reply_starts={len(starts)} next_reply_events={len(phase3)}")


async def scenario_idle_interrupt() -> None:
    agent = build_agent()
    events = [event async for event in agent.reply_stream(UserInterruptEvent(reply_id=agent.state.reply_id))]
    print(f"[idle-interrupt] events={len(events)} parked={parked(agent)}")


async def main() -> None:
    print(f"agentscope_version={agentscope.__version__}")
    await scenario(confirmed=True)
    await scenario(confirmed=False)
    await scenario_wrong_reply_id()
    await scenario_interrupt()
    await scenario_idle_interrupt()


if __name__ == "__main__":
    asyncio.run(main())
