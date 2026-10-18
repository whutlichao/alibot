"""Criterion 4 probe: RequireUserConfirmEvent routing and a parked stream.

Run from the repository root:

    .venv/bin/python app/specs/issue-132-research/probe_hitl.py

A member calls a tool that always needs human confirmation.  The probe
records how ``TeamPipeline`` ends the first stream while both the leader and
the member are parked, which ``reply_id`` routes the confirmation, and what
the resumed stream looks like.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import agentscope
from _harness import (
    AskTool,
    InterceptingTeamPipeline,
    ScriptedModel,
    member_toolkit,
    text_chunks,
    tool_call_chunks,
)
from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.pipeline import TeamMember


def leader_assignment() -> list[Any]:
    call = ToolCallBlock(
        id="call-alpha",
        name="TeamAssign",
        input=json.dumps({"member": "m-alpha", "prompt": "needs approval"}),
    )
    return [
        ChatResponse(content=[call], is_last=False),
        ChatResponse(content=[call], is_last=True),
    ]


async def main() -> None:
    print(f"agentscope_version={agentscope.__version__}")
    trace: list[tuple[Any, ...]] = []
    leader = Agent(
        name="leader",
        system_prompt="lead",
        model=ScriptedModel(
            [
                leader_assignment(),
                text_chunks([], "leader final after approval"),
            ],
        ),
    )
    member = Agent(
        name="m-alpha",
        system_prompt="member alpha",
        model=ScriptedModel(
            [
                tool_call_chunks("tc-ask", "NeedsApproval", {"text": "x"}),
                text_chunks([], "member done"),
            ],
        ),
        toolkit=member_toolkit(AskTool()),
    )
    pipeline = InterceptingTeamPipeline(
        leader=leader,
        members=[TeamMember(agent=member, description="alpha")],
        trace=trace,
    )

    phase1 = [event async for event in pipeline.reply_stream(UserMsg(name="user", content="go"))]
    confirm_events = [event for event in phase1 if isinstance(event, RequireUserConfirmEvent)]
    require = confirm_events[0]
    parked_member = pipeline._parked_member(require.reply_id)

    print(f"phase1_events={len(phase1)}")
    print(f"phase1_last_event={type(phase1[-1]).__name__}")
    print(f"phase1_require_count={len(confirm_events)}")
    print(f"phase1_require_reply_id={require.reply_id}")
    print(f"leader_reply_id={leader.state.reply_id}")
    print(f"member_reply_id={member.state.reply_id}")
    print(f"require_reply_id_is_member={require.reply_id == member.state.reply_id}")
    print(f"require_reply_id_is_leader={require.reply_id == leader.state.reply_id}")
    print(f"routed_member={parked_member.agent.name}")
    print(f"leader_parked={leader.state.has_awaiting_tool_calls(leader.name)}")
    print(f"member_parked={member.state.has_awaiting_tool_calls(member.name)}")
    print(f"phase1_assignment_results={[e[1] for e in trace if e[0] == 'ASSIGNMENTS_DONE']}")
    print("phase1_stream_exhausted_while_parked=True")

    pending_call = require.tool_calls[0]
    phase2 = [
        event
        async for event in pipeline.reply_stream(
            UserConfirmResultEvent(
                reply_id=require.reply_id,
                confirm_results=[ConfirmResult(confirmed=True, tool_call=pending_call)],
            ),
        )
    ]
    phase2_reply_starts = [e for e in phase2 if isinstance(e, ReplyStartEvent)]
    phase2_with_reply_id = [e for e in phase2 if getattr(e, "reply_id", None) is not None]
    print(f"phase2_events={len(phase2)}")
    print(f"phase2_reply_start_count={len(phase2_reply_starts)}")
    print(f"phase2_events_with_reply_id={len(phase2_with_reply_id)}")
    print(f"phase2_orphans_if_map_starts_fresh={len(phase2_with_reply_id) - len(phase2_reply_starts)}")
    print(f"phase2_last_event={type(phase2[-1]).__name__}")
    print(f"phase2_assignment_results={[e[1] for e in trace if e[0] == 'ASSIGNMENTS_DONE']}")
    print(f"leader_parked_after={leader.state.has_awaiting_tool_calls(leader.name)}")
    print(f"member_parked_after={member.state.has_awaiting_tool_calls(member.name)}")


if __name__ == "__main__":
    asyncio.run(main())
