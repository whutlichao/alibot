"""Criterion 2/3 probe: ``TeamPipeline`` HITL routing, interrupts and waiting.

Run from the repository root:

    .venv/bin/python app/specs/issue-136-research/probe_team_hitl.py

Scenarios:

1. One member parks: the stream ends while the leader and the member are
   parked; approving by the member's ``reply_id`` resumes only the member,
   returns its reply as the leader's ``TeamAssign`` result, and the leader
   finishes.
2. The same with ``confirmed=False``: the member records a ``DENIED`` tool
   result, still finishes, and the leader receives a ``SUCCESS`` assignment
   result (the denial is not visible to the leader as a failure).
3. Two members park in one batch: approving one resumes only that member;
   the leader stays parked until the second decision arrives.
4. Wrong ``reply_id`` routing: the leader's id is rejected by the leader as
   "not waiting for user confirmation"; an unknown id has no parked member.
5. ``UserInterruptEvent`` aborts every parked member and the leader, each
   closing its pending call as ``INTERRUPTED``; the pipeline ignores the
   event's ``reply_id`` for routing and interrupts everyone.
6. Waiting does nothing: there is no built-in timeout; a parked pipeline is
   already idle, so the broker has to own the clock.
"""

from __future__ import annotations

import asyncio
import json
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
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.pipeline import TeamMember

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "issue-132-research"))

from _harness import (
    AskTool,
    InterceptingTeamPipeline,
    ScriptedModel,
    member_toolkit,
    text_chunks,
    tool_call_chunks,
)


def assignment(call_id: str, member: str) -> ToolCallBlock:
    return ToolCallBlock(
        id=call_id,
        name="TeamAssign",
        input=json.dumps({"member": member, "prompt": f"work for {member}"}),
    )


def leader_script(*calls: ToolCallBlock) -> list[Any]:
    assign = [
        ChatResponse(content=list(calls), is_last=False),
        ChatResponse(content=list(calls), is_last=True),
    ]
    return [assign, text_chunks([], "leader final")]


def leader_agent(*calls: ToolCallBlock) -> Agent:
    return Agent(name="leader", system_prompt="lead", model=ScriptedModel(leader_script(*calls)))


def member_agent(name: str, final: str) -> Agent:
    return Agent(
        name=name,
        system_prompt=name,
        model=ScriptedModel(
            [
                tool_call_chunks(f"tc-{name}", "NeedsApproval", {"text": name}),
                text_chunks([], final),
            ],
        ),
        toolkit=member_toolkit(AskTool()),
    )


def build_pipeline(
    *calls: ToolCallBlock,
    members: list[tuple[str, str]],
) -> tuple[InterceptingTeamPipeline, Agent, list[tuple[Any, ...]]]:
    trace: list[tuple[Any, ...]] = []
    leader = leader_agent(*calls)
    built = [TeamMember(agent=member_agent(name, final), description=name) for name, final in members]
    return InterceptingTeamPipeline(leader=leader, members=built, trace=trace), leader, trace


def event_counts(events: list[Any]) -> str:
    kinds = Counter(type(event).__name__ for event in events)
    return ",".join(f"{name}={count}" for name, count in sorted(kinds.items()))


def confirms(events: list[Any]) -> list[RequireUserConfirmEvent]:
    return [event for event in events if isinstance(event, RequireUserConfirmEvent)]


def reply_ends(events: list[Any]) -> list[str]:
    return [str(event.finished_reason) for event in events if isinstance(event, ReplyEndEvent)]


def tool_states(events: list[Any]) -> list[str]:
    return [str(event.state) for event in events if isinstance(event, ToolResultEndEvent)]


def agent_text(agent: Agent) -> str:
    return agent.state.context[-1].get_text_content() or ""


def leader_results(leader: Agent) -> list[str]:
    states: list[str] = []
    for block in leader.state.context[-1].get_content_blocks("tool_result"):
        states.append(f"{block.name}:{block.state}")
    return states


def stream_parked(pipeline: InterceptingTeamPipeline, event: RequireUserConfirmEvent) -> Any:
    return pipeline._parked_member(event.reply_id).agent


def assignment_counts(trace: list[tuple[Any, ...]]) -> list[int]:
    return [entry[1] for entry in trace if entry[0] == "ASSIGNMENTS_DONE"]


async def scenario_decision(confirmed: bool) -> None:
    label = "approve" if confirmed else "deny"
    pipeline, leader, trace = build_pipeline(
        assignment("call-alpha", "m-alpha"),
        members=[("m-alpha", "alpha final")],
    )
    phase1 = [event async for event in pipeline.reply_stream(UserMsg(name="user", content="go"))]
    require = confirms(phase1)[0]
    member = stream_parked(pipeline, require)
    print(f"[{label}] phase1_events={len(phase1)} last={type(phase1[-1]).__name__}")
    print(f"[{label}] phase1_require_reply_id_is_member={require.reply_id == member.state.reply_id}")
    print(f"[{label}] phase1_require_reply_id_is_leader={require.reply_id == leader.state.reply_id}")
    print(
        f"[{label}] phase1_parked leader={leader.state.has_awaiting_tool_calls('leader')} "
        f"member={member.state.has_awaiting_tool_calls(member.name)}",
    )
    print(f"[{label}] phase1_reply_ends={reply_ends(phase1)} assignments={assignment_counts(trace)}")
    print(f"[{label}] phase1_has_reply_start={any(isinstance(_, ReplyStartEvent) for _ in phase1)}")

    phase2 = [
        event
        async for event in pipeline.reply_stream(
            UserConfirmResultEvent(
                reply_id=require.reply_id,
                confirm_results=[ConfirmResult(confirmed=confirmed, tool_call=require.tool_calls[0])],
            ),
        )
    ]
    print(f"[{label}] phase2_events={len(phase2)} counts={event_counts(phase2)}")
    print(f"[{label}] phase2_reply_ends={reply_ends(phase2)}")
    print(f"[{label}] phase2_reply_starts={sum(isinstance(_, ReplyStartEvent) for _ in phase2)}")
    print(f"[{label}] phase2_tool_states={tool_states(phase2)}")
    print(f"[{label}] assignments_after={assignment_counts(trace)}")
    print(f"[{label}] leader_tool_results={leader_results(leader)}")
    print(f"[{label}] leader_final_text={agent_text(leader)!r}")
    print(f"[{label}] leader_parked_after={leader.state.has_awaiting_tool_calls('leader')}")
    print(f"[{label}] member_parked_after={member.state.has_awaiting_tool_calls(member.name)}")


async def scenario_two_members() -> None:
    pipeline, leader, trace = build_pipeline(
        assignment("call-alpha", "m-alpha"),
        assignment("call-beta", "m-beta"),
        members=[("m-alpha", "alpha final"), ("m-beta", "beta final")],
    )
    phase1 = [event async for event in pipeline.reply_stream(UserMsg(name="user", content="go"))]
    required = confirms(phase1)
    ids = [event.reply_id for event in required]
    names = [stream_parked(pipeline, event).name for event in required]
    print(f"[two-members] phase1_events={len(phase1)} requires={len(required)} members={names}")
    print(f"[two-members] phase1_reply_ends={reply_ends(phase1)} assignments={assignment_counts(trace)}")
    print(
        "[two-members] both_parked="
        f"{all(pipeline._parked_member(_.reply_id).agent.state.has_awaiting_tool_calls(pipeline._parked_member(_.reply_id).agent.name) for _ in required)}",
    )

    first = required[0]
    phase2 = [
        event
        async for event in pipeline.reply_stream(
            UserConfirmResultEvent(
                reply_id=first.reply_id,
                confirm_results=[ConfirmResult(confirmed=True, tool_call=first.tool_calls[0])],
            ),
        )
    ]
    print(f"[two-members] after_first_events={len(phase2)} reply_ends={reply_ends(phase2)}")
    print(f"[two-members] after_first_assignments={assignment_counts(trace)}")
    print(f"[two-members] after_first_leader_parked={leader.state.has_awaiting_tool_calls('leader')}")
    print(f"[two-members] after_first_leader_results={leader_results(leader)}")
    remaining = [pipeline._parked_member(rid) for rid in ids if rid != first.reply_id]
    print(f"[two-members] remaining_parked={[_.agent.name for _ in remaining]}")

    second_id = ids[1]
    second = pipeline._parked_member(second_id)
    pending = [
        block
        for block in second.agent.state.context[-1].get_content_blocks("tool_call")
        if block.state == "asking"
    ]
    phase3 = [
        event
        async for event in pipeline.reply_stream(
            UserConfirmResultEvent(
                reply_id=second_id,
                confirm_results=[ConfirmResult(confirmed=True, tool_call=pending[0])],
            ),
        )
    ]
    print(f"[two-members] after_second_events={len(phase3)} reply_ends={reply_ends(phase3)}")
    print(f"[two-members] after_second_assignments={assignment_counts(trace)}")
    print(f"[two-members] leader_final_text={agent_text(leader)!r}")


async def scenario_bad_reply_ids() -> None:
    pipeline, leader, _ = build_pipeline(
        assignment("call-alpha", "m-alpha"),
        members=[("m-alpha", "alpha final")],
    )
    phase1 = [event async for event in pipeline.reply_stream(UserMsg(name="user", content="go"))]
    require = confirms(phase1)[0]
    for label, reply_id in (("leader-reply-id", leader.state.reply_id), ("unknown", "bogus")):
        try:
            async for _ in pipeline.reply_stream(
                UserConfirmResultEvent(
                    reply_id=reply_id,
                    confirm_results=[ConfirmResult(confirmed=True, tool_call=require.tool_calls[0])],
                ),
            ):
                pass
        except ValueError as exc:
            print(f"[bad-reply-id:{label}] ValueError={str(exc).split(chr(10))[0][:160]}")
    print(f"[bad-reply-id] member_still_parked={pipeline._parked_member(require.reply_id).agent.name}")


async def scenario_interrupt() -> None:
    pipeline, leader, trace = build_pipeline(
        assignment("call-alpha", "m-alpha"),
        members=[("m-alpha", "alpha final")],
    )
    phase1 = [event async for event in pipeline.reply_stream(UserMsg(name="user", content="go"))]
    require = confirms(phase1)[0]
    member = stream_parked(pipeline, require)
    phase2 = [
        event
        async for event in pipeline.reply_stream(
            UserInterruptEvent(reply_id="deliberately-not-any-reply-id"),
        )
    ]
    print(f"[interrupt] events={len(phase2)} counts={event_counts(phase2)}")
    print(f"[interrupt] reply_ends={reply_ends(phase2)}")
    print(f"[interrupt] tool_states={tool_states(phase2)}")
    print(f"[interrupt] leader_tool_results={leader_results(leader)}")
    print(f"[interrupt] leader_parked_after={leader.state.has_awaiting_tool_calls('leader')}")
    print(f"[interrupt] member_parked_after={member.state.has_awaiting_tool_calls(member.name)}")
    print(f"[interrupt] assignments_after={assignment_counts(trace)}")


async def scenario_waiting_does_nothing() -> None:
    pipeline, leader, _ = build_pipeline(
        assignment("call-alpha", "m-alpha"),
        members=[("m-alpha", "alpha final")],
    )
    phase1 = [event async for event in pipeline.reply_stream(UserMsg(name="user", content="go"))]
    require = confirms(phase1)[0]
    member = stream_parked(pipeline, require)
    started = asyncio.get_running_loop().time()
    await asyncio.sleep(0.5)
    elapsed = asyncio.get_running_loop().time() - started
    print(f"[timeout] waited_seconds={elapsed:.2f}")
    print(f"[timeout] phase1_stream_already_ended={type(phase1[-1]).__name__}")
    print(f"[timeout] leader_parked={leader.state.has_awaiting_tool_calls('leader')}")
    print(f"[timeout] member_parked={member.state.has_awaiting_tool_calls(member.name)}")
    print("[timeout] builtin_timeout_event=False (no timer runs while parked)")
    cleanup = [
        event
        async for event in pipeline.reply_stream(
            UserInterruptEvent(reply_id=leader.state.reply_id),
        )
    ]
    print(f"[timeout] cleanup_reply_ends={reply_ends(cleanup)} cleanup_tool_states={tool_states(cleanup)}")


async def main() -> None:
    print(f"agentscope_version={agentscope.__version__}")
    await scenario_decision(confirmed=True)
    await scenario_decision(confirmed=False)
    await scenario_two_members()
    await scenario_bad_reply_ids()
    await scenario_interrupt()
    await scenario_waiting_does_nothing()


if __name__ == "__main__":
    asyncio.run(main())
