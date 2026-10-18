"""Issue #133 probe: does a real DeepSeek leader call ``TeamAssign``?

Run from the repository root (the shell must have ``DEEPSEEK_API_KEY``; the
value is never read, printed or stored by this probe):

    env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
        .venv/bin/python app/specs/issue-133-research/probe_team_assign_deepseek.py

Budget rules enforced in code (one human-authorized call). Re-running this
script makes **another** paid request; only run it with fresh authorization:

* at most **one** real DeepSeek request (``OneShotDeepSeekModel`` raises if a
  second real call is attempted);
* ``max_retries=0`` — no retry even for retryable transport errors;
* ``max_tokens=2048``;
* estimated input tokens (messages + tool schemas, bytes/4) must stay under
  8,000 before the request is sent;
* at most **one** ``TeamAssign`` reaches the member runner; extra calls the
  model may emit are recorded and dropped by ``CappedTeamPipeline``.

The leader is the only real model. The member is a scripted
``ChatModelBase`` (no network), and the leader's *second* model call (the one
that follows a successful assignment) is scripted too, so the probe measures
the real first decision without paying for a second request.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import sys
import time
from collections import Counter
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SPECS = Path(__file__).resolve().parent
sys.path.insert(0, str(SPECS.parent / "issue-132-research"))

import agentscope  # noqa: E402
from _harness import (  # noqa: E402
    InterceptingTeamPipeline,
    ScriptedModel,
    TracingAgent,
    text_chunks,
)
from agentscope.agent import Agent  # noqa: E402
from agentscope.credential import DeepSeekCredential  # noqa: E402
from agentscope.event import EventBase, ReplyEndEvent  # noqa: E402
from agentscope.message import TextBlock, ThinkingBlock, ToolCallBlock, UserMsg  # noqa: E402
from agentscope.model import ChatResponse, DeepSeekChatModel  # noqa: E402
from agentscope.pipeline import TeamMember  # noqa: E402

MODEL_ID = "deepseek-chat"
MAX_TOKENS = 2048
INPUT_TOKEN_BUDGET = 8_000
MEMBER_ID = "m-alpha"

SYSTEM_PROMPT = (
    "You lead a two-person team. The only member is m-alpha, who replies with "
    "one short line. Delegate work with the TeamAssign tool. Do not do the "
    "member's work yourself."
)
USER_PROMPT = (
    "Ask m-alpha to produce exactly the line 'hello from alpha'. "
    "Use exactly one TeamAssign call and then stop."
)
SCRIPTED_FINAL = "Delegation complete: m-alpha produced the requested line."


class OneShotDeepSeekModel(DeepSeekChatModel):
    """DeepSeek chat model with a hard one-real-request budget.

    The first model call is a real DeepSeek request. Every later call is
    answered by a scripted final text so the pipeline can finish without a
    second paid request.
    """

    def __init__(self, *, scripted_final: str) -> None:
        api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set in this shell")
        super().__init__(
            credential=DeepSeekCredential(api_key=api_key),
            model=MODEL_ID,
            parameters=self.Parameters(max_tokens=MAX_TOKENS, thinking_enable=False),
            stream=False,
            max_retries=0,
        )
        self.real_calls = 0
        self.scripted_calls = 0
        self.first_call: dict[str, Any] = {}
        self.tool_schema_names: list[str] = []
        self.tool_choice_seen: str | None = None
        self._scripted_final = scripted_final

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        self.tool_schema_names = [(tool.get("function") or {}).get("name") for tool in (tools or [])]
        self.tool_choice_seen = None if tool_choice is None else tool_choice.mode

        if self.real_calls >= 1:
            if self.scripted_calls >= 1:
                raise RuntimeError("probe budget: a third leader model call was attempted")
            self.scripted_calls += 1
            return ChatResponse(
                content=[TextBlock(text=self._scripted_final)],
                is_last=True,
            )

        estimated = await self.count_tokens(messages, tools)
        self.first_call["estimated_input_tokens"] = estimated
        if estimated > INPUT_TOKEN_BUDGET:
            raise RuntimeError(
                f"probe budget: estimated input {estimated} > {INPUT_TOKEN_BUDGET} tokens",
            )

        self.real_calls += 1
        started = time.monotonic()
        try:
            response = await super()._call_api(
                model_name=model_name,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs,
            )
        except Exception as exc:
            self.first_call["latency_s"] = round(time.monotonic() - started, 3)
            self.first_call["error_class"] = type(exc).__name__
            self.first_call["error_module"] = type(exc).__module__
            self.first_call["error_message"] = _scrub(str(exc))[:1000]
            raise

        self.first_call["latency_s"] = round(time.monotonic() - started, 3)
        self.first_call["content"] = [_describe_block(block) for block in response.content]
        if response.usage is not None:
            self.first_call["usage"] = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_input_tokens": response.usage.cache_input_tokens,
                "time_s": response.usage.time,
            }
        return response


def _scrub(text: str) -> str:
    """Remove anything that looks like an API key before printing."""

    return re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-REDACTED", text)


def _describe_block(block: Any) -> dict[str, Any]:
    if isinstance(block, ToolCallBlock):
        return {"type": "tool_call", "name": block.name, "input": block.input}
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "chars": len(block.thinking)}
    return {"type": type(block).__name__}


class CappedTeamPipeline(InterceptingTeamPipeline):
    """Records the assignment call and lets at most one reach the runner."""

    def __init__(self, *, leader: Agent, members: list[TeamMember], trace: list[Any]):
        super().__init__(leader=leader, members=members, trace=trace)
        self.executed: list[dict[str, Any]] = []
        self.dropped: list[str] = []

    async def _run_assignments(
        self,
        tool_calls: list[ToolCallBlock],
        results: list[Any],
        resumed: Any = None,
    ) -> AsyncGenerator[Any, None]:
        calls = tool_calls
        if resumed is None:
            calls = tool_calls[:1]
            self.dropped = [call.id for call in tool_calls[1:]]
        async for event in super()._run_assignments(calls, results, resumed):
            yield event
        if resumed is None:
            self.executed = [{"id": result.id, "state": str(result.state)} for result in results]


def build_pipeline(
    leader_model: OneShotDeepSeekModel,
    trace: list[Any],
) -> CappedTeamPipeline:
    leader = Agent(
        name="leader",
        system_prompt=SYSTEM_PROMPT,
        model=leader_model,
    )
    member = TeamMember(
        agent=TracingAgent(
            name=MEMBER_ID,
            system_prompt="Reply with one short greeting line.",
            model=ScriptedModel([text_chunks([], "hello from alpha")]),
            trace=trace,
        ),
        description="Replies with one short greeting line.",
    )
    return CappedTeamPipeline(leader=leader, members=[member], trace=trace)


def cost_estimate(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Peak/off-peak estimate against the public deepseek-flash rates.

    Source: https://api-docs.deepseek.com/quick_start/pricing/ read on
    2026-10-18. The current price sheet lists ``deepseek-flash`` and
    ``deepseek-v4-pro``; ``deepseek-chat`` is not listed (legacy id). The
    estimate therefore assumes the request is served at Flash rates.
    """
    rates = {
        "peak": {"cache_hit": 0.006, "cache_miss": 0.3, "output": 1.2},
        "off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.6},
    }
    if not usage:
        return {"usd": None, "reason": "no usage returned", "rates_per_1m": rates}
    cache_hit = usage.get("cache_input_tokens") or 0
    input_tokens = usage.get("input_tokens") or 0
    cache_miss = max(input_tokens - cache_hit, 0)
    output = usage.get("output_tokens") or 0

    def usd(band: dict[str, float]) -> float:
        return (
            cache_hit * band["cache_hit"] + cache_miss * band["cache_miss"] + output * band["output"]
        ) / 1_000_000

    return {
        "assumption": "served at deepseek-flash rates (deepseek-chat is not on the price sheet)",
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss,
        "output_tokens": output,
        "usd_peak": round(usd(rates["peak"]), 8),
        "usd_off_peak": round(usd(rates["off_peak"]), 8),
        "rates_per_1m": rates,
    }


def summarize_events(events: list[EventBase]) -> dict[str, Any]:
    counts = Counter(type(event).__name__ for event in events)
    leader_ends = [
        {
            "finished_reason": str(event.finished_reason),
            "has_error": event.error is not None,
        }
        for event in events
        if isinstance(event, ReplyEndEvent)
    ]
    return {"counts": dict(sorted(counts.items())), "reply_ends": leader_ends}


async def _collect(pipeline: CappedTeamPipeline, events: list[EventBase]) -> None:
    async for event in pipeline.reply_stream(UserMsg(name="user", content=USER_PROMPT)):
        events.append(event)


async def main() -> int:
    trace: list[Any] = []
    leader_model = OneShotDeepSeekModel(scripted_final=SCRIPTED_FINAL)
    pipeline = build_pipeline(leader_model, trace)
    started = time.monotonic()
    result: dict[str, Any] = {
        "run": {
            "date_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "agentscope_version": agentscope.__version__,
            "requested_model": MODEL_ID,
            "credential_env": "DEEPSEEK_API_KEY",
            "credential_present": True,
            "thinking_enable": False,
            "stream": False,
            "max_retries": 0,
            "max_tokens": MAX_TOKENS,
            "input_token_budget": INPUT_TOKEN_BUDGET,
        },
    }

    events: list[EventBase] = []
    error: Exception | None = None
    try:
        await asyncio.wait_for(_collect(pipeline, events), timeout=120)
    except Exception as exc:
        error = exc

    run = result["run"]
    run["wall_s"] = round(time.monotonic() - started, 3)
    run["real_api_calls"] = leader_model.real_calls
    run["scripted_leader_calls"] = leader_model.scripted_calls
    run["tool_schema_names"] = leader_model.tool_schema_names
    run["tool_choice_seen"] = leader_model.tool_choice_seen
    run["first_call"] = leader_model.first_call

    raw_calls = [
        block for block in leader_model.first_call.get("content", []) if block.get("type") == "tool_call"
    ]
    team_assign_calls = [block for block in raw_calls if block.get("name") == "TeamAssign"]
    run["raw_tool_calls"] = raw_calls
    run["team_assign_calls_emitted"] = len(team_assign_calls)
    run["assignments_executed"] = pipeline.executed
    run["assignments_dropped_by_cap"] = pipeline.dropped
    run["member_stream_starts"] = sum(1 for entry in trace if entry[0] == "MEMBER_STREAM_START")
    run["events"] = summarize_events(events)

    if error is not None:
        run["outcome"] = "error"
        run["error_class"] = type(error).__name__
        run["error_module"] = type(error).__module__
        run["error_message"] = _scrub(str(error))[:1000]
        run["fallback"] = "error"
    elif team_assign_calls and pipeline.executed:
        states = [item["state"] for item in pipeline.executed]
        run["outcome"] = "assignment"
        run["assignment_success"] = all(state.endswith("success") for state in states)
        run["fallback"] = "none"
    else:
        run["outcome"] = "leader_direct_answer"
        run["fallback"] = "leader answered without calling TeamAssign"
        run["final_text"] = "".join(
            block.get("text", "")
            for block in leader_model.first_call.get("content", [])
            if block.get("type") == "text"
        )

    result["cost_estimate_usd"] = cost_estimate(leader_model.first_call.get("usage"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
