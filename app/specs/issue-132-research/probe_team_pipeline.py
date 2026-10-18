"""Criterion 1 + 2 probe: TeamPipeline interception point and event ownership.

Run from the repository root:

    .venv/bin/python app/specs/issue-132-research/probe_team_pipeline.py

The probe builds a leader and two members on ``agentscope==2.0.10`` with a
scripted model, overrides ``TeamPipeline._run_assignments`` to log every
``ToolCallBlock``, and records whether those logs precede every
``member.agent.reply_stream()`` call.  It then attributes every streamed
event to a member through ``ReplyStartEvent.name`` + ``reply_id``.

A second scenario denies one assignment inside the override, appends an
error ``ToolResultBlock`` for it, and shows that the denied member never
starts its stream — the "budget exhausted does not run the member" shape.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import Counter, defaultdict
from typing import Any

import agentscope
import agentscope.event._event as event_module
from _harness import (
    EchoTool,
    InterceptingTeamPipeline,
    ScriptedModel,
    TracingAgent,
    member_toolkit,
    text_chunks,
    tool_call_chunks,
)
from agentscope.agent import Agent
from agentscope.event import EventBase, ReplyStartEvent
from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock, ToolResultState, UserMsg
from agentscope.model import ChatResponse
from agentscope.pipeline import TeamMember

MEMBERS = {"alpha": "m-alpha", "beta": "m-beta"}


class FilteringTeamPipeline(InterceptingTeamPipeline):
    """Denies one assignment before the base runner sees it."""

    async def _run_assignments(
        self,
        tool_calls: list[ToolCallBlock],
        results: list[Any],
        resumed: Any = None,
    ) -> Any:
        allowed: list[ToolCallBlock] = []
        for call in tool_calls:
            member = self._parse(call)["member"]
            if member == "m-beta":
                results.append(
                    ToolResultBlock(
                        id=call.id,
                        name="TeamAssign",
                        output=[TextBlock(text="probe: denied before run")],
                        state=ToolResultState.ERROR,
                    ),
                )
                self._trace.append(("DENIED", call.id, member))
            else:
                allowed.append(call)
        async for event in super()._run_assignments(allowed, results, resumed):
            yield event


def leader_assignment_response() -> list[Any]:
    """One model response carrying two TeamAssign calls."""

    calls = [
        ToolCallBlock(
            id="call-alpha",
            name="TeamAssign",
            input=json.dumps({"member": "m-alpha", "prompt": "alpha task"}),
        ),
        ToolCallBlock(
            id="call-beta",
            name="TeamAssign",
            input=json.dumps({"member": "m-beta", "prompt": "beta task"}),
        ),
    ]
    return [
        ChatResponse(content=list(calls), is_last=False),
        ChatResponse(content=list(calls), is_last=True),
    ]


def build_team(
    trace: list[tuple[Any, ...]],
    pipeline_class: type[InterceptingTeamPipeline],
) -> InterceptingTeamPipeline:
    leader = Agent(
        name="leader",
        system_prompt="lead the team",
        model=ScriptedModel(
            [
                leader_assignment_response(),
                text_chunks([], "leader final after assignments"),
            ],
        ),
    )
    members = [
        TeamMember(
            agent=TracingAgent(
                name=pipeline_name,
                system_prompt=f"member {member_id}",
                trace=trace,
                model=ScriptedModel(
                    [
                        tool_call_chunks("tc-" + member_id, "Echo", {"text": member_id}),
                        text_chunks(
                            [f"{member_id} ", "reply"],
                            f"{member_id} reply",
                        ),
                    ],
                ),
                toolkit=member_toolkit(EchoTool()),
            ),
            description=f"member {member_id}",
        )
        for member_id, pipeline_name in MEMBERS.items()
    ]
    return pipeline_class(
        leader=leader,
        members=members,
        trace=trace,
    )


async def run_scenario(
    pipeline_class: type[InterceptingTeamPipeline],
) -> tuple[list[Any], list[tuple[Any, ...]]]:
    trace: list[tuple[Any, ...]] = []
    pipeline = build_team(trace, pipeline_class)
    events = [event async for event in pipeline.reply_stream(UserMsg(name="user", content="go"))]
    return events, trace


def check_interception(trace: list[tuple[Any, ...]]) -> dict[str, Any]:
    intercepts = [entry for entry in trace if entry[0] == "INTERCEPT"]
    stream_starts = [index for index, entry in enumerate(trace) if entry[0] == "MEMBER_STREAM_START"]
    intercept_indexes = [index for index, entry in enumerate(trace) if entry[0] == "INTERCEPT"]
    return {
        "member_stream_starts": len(stream_starts),
        "intercepts": intercepts,
        "intercept_count": len(intercepts),
        "all_intercepts_before_first_member_stream": bool(intercept_indexes)
        and bool(stream_starts)
        and max(intercept_indexes) < min(stream_starts),
        "assigned_members": [entry[3] for entry in intercepts],
        "assignment_results": [entry[1] for entry in trace if entry[0] == "ASSIGNMENTS_DONE"],
    }


def classes_without_reply_id() -> list[str]:
    names = []
    for name in sorted(dir(event_module)):
        obj = getattr(event_module, name)
        if (
            inspect.isclass(obj)
            and issubclass(obj, EventBase)
            and obj is not EventBase
            and "reply_id" not in obj.model_fields
        ):
            names.append(name)
    return names


def attribute(events: list[Any]) -> dict[str, Any]:
    name_to_member = {pipeline_name: member_id for member_id, pipeline_name in MEMBERS.items()}
    name_to_member["leader"] = "leader"

    reply_to_member: dict[str, str] = {}
    reply_starts = 0
    events_before_first_reply_start = 0
    seen_reply_start = False
    orphan_events: list[tuple[str, str | None]] = []
    per_member: dict[str, Counter[str]] = defaultdict(Counter)

    for event in events:
        if isinstance(event, ReplyStartEvent):
            reply_starts += 1
            member_id = name_to_member[event.name]
            reply_to_member[event.reply_id] = member_id
            seen_reply_start = True
            per_member[member_id]["ReplyStartEvent"] += 1
            continue

        reply_id = getattr(event, "reply_id", None)
        member_id = reply_to_member.get(reply_id) if reply_id is not None else None
        if not seen_reply_start:
            events_before_first_reply_start += 1
        if member_id is None:
            orphan_events.append((type(event).__name__, reply_id))
            continue
        per_member[member_id][type(event).__name__] += 1

    return {
        "reply_starts": reply_starts,
        "reply_ids": reply_to_member,
        "events_before_first_reply_start": events_before_first_reply_start,
        "orphan_events": orphan_events,
        "orphan_count": len(orphan_events),
        "per_member": {member: dict(counter) for member, counter in sorted(per_member.items())},
        "event_classes_without_reply_id": classes_without_reply_id(),
    }


async def main() -> None:
    print(f"agentscope_version={agentscope.__version__}")
    events, trace = await run_scenario(InterceptingTeamPipeline)
    interception = check_interception(trace)
    attribution = attribute(events)

    print(f"total_events={len(events)}")
    print(f"member_stream_starts={interception['member_stream_starts']}")
    print(f"intercept_count={interception['intercept_count']}")
    for entry in interception["intercepts"]:
        print(f"intercept={entry}")
    print(
        "all_intercepts_before_first_member_stream="
        f"{interception['all_intercepts_before_first_member_stream']}"
    )
    print(f"assigned_members={interception['assigned_members']}")
    print(f"assignment_results={interception['assignment_results']}")
    print(f"reply_start_count={attribution['reply_starts']}")
    print(f"reply_id_to_member={attribution['reply_ids']}")
    print(f"events_before_first_reply_start={attribution['events_before_first_reply_start']}")
    print(f"orphan_count={attribution['orphan_count']}")
    print(f"orphan_events={attribution['orphan_events']}")
    for member_id, counts in attribution["per_member"].items():
        print(
            f"member={member_id} "
            f"TextBlockDelta={counts.get('TextBlockDeltaEvent', 0)} "
            f"ToolCallStart={counts.get('ToolCallStartEvent', 0)} "
            f"ToolResultStart={counts.get('ToolResultStartEvent', 0)} "
            f"ReplyEnd={counts.get('ReplyEndEvent', 0)} "
            f"total={sum(counts.values())}"
        )
    print(f"event_classes_without_reply_id={attribution['event_classes_without_reply_id']}")

    filtered_events, filtered_trace = await run_scenario(FilteringTeamPipeline)
    filtered_starts = [entry for entry in filtered_trace if entry[0] == "MEMBER_STREAM_START"]
    denied = [entry for entry in filtered_trace if entry[0] == "DENIED"]
    print(f"filtered_total_events={len(filtered_events)}")
    print(f"filtered_member_stream_starts={[entry[1] for entry in filtered_starts]}")
    print(f"filtered_denied={denied}")
    print(f"filtered_denied_member_stream_started={any(entry[1] == 'm-beta' for entry in filtered_starts)}")


if __name__ == "__main__":
    asyncio.run(main())
