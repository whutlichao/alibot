"""Issue #133 probe: known provider differences around ``TeamAssign``.

Runs entirely offline against the installed ``agentscope==2.0.10``:

    env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
        .venv/bin/python app/specs/issue-133-research/probe_known_differences.py

Scenarios:

1. thinking block before a ``TeamAssign`` call in the same response — does the
   pipeline still execute the assignment?
2. the provider rejects the tool schema (HTTP 400 ``BadRequestError``) — what
   surfaces, and is it retried?
3. ``tool_choice`` formatting per provider — DeepSeek forwards forced modes,
   Ollama drops them; forced mode + thinking is a documented HTTP 400.
4. retry defaults and the absence of ``tool_choice`` in ``TeamPipeline``.

Scenarios 1 and 2 use nothing but scripted models; scenario 3 inspects the
request kwargs with a fake OpenAI-compatible client, so no provider is called.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

SPECS = Path(__file__).resolve().parent
sys.path.insert(0, str(SPECS.parent / "issue-132-research"))

import agentscope  # noqa: E402
import httpx  # noqa: E402
import openai  # noqa: E402
from _harness import InterceptingTeamPipeline, ScriptedModel, TracingAgent, text_chunks  # noqa: E402
from agentscope.agent import Agent  # noqa: E402
from agentscope.formatter import DeepSeekChatFormatter  # noqa: E402
from agentscope.message import (  # noqa: E402
    ThinkingBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.model import ChatResponse, DeepSeekChatModel, OllamaChatModel  # noqa: E402
from agentscope.pipeline import TeamMember  # noqa: E402
from agentscope.tool import ToolChoice  # noqa: E402

TEAM_ASSIGN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "TeamAssign",
        "description": "Assign a task to a team member.",
        "parameters": {
            "type": "object",
            "properties": {
                "member": {"type": "string", "enum": ["m-alpha"]},
                "prompt": {"type": "string"},
            },
            "required": ["member", "prompt"],
        },
    },
}


def leader_script_with_thinking() -> list[list[ChatResponse]]:
    """One response: a thinking block, then a TeamAssign call in deltas."""

    raw = json.dumps({"member": "m-alpha", "prompt": "say hi"})
    middle = len(raw) // 2
    first_turn = [
        ChatResponse(content=[ThinkingBlock(thinking="I should delegate this.")], is_last=False),
        ChatResponse(
            content=[ToolCallBlock(id="tc-thinking", name="TeamAssign", input=raw[:middle])],
            is_last=False,
        ),
        ChatResponse(
            content=[ToolCallBlock(id="tc-thinking", name="TeamAssign", input=raw[middle:])],
            is_last=False,
        ),
        ChatResponse(
            content=[
                ThinkingBlock(thinking="I should delegate this."),
                ToolCallBlock(id="tc-thinking", name="TeamAssign", input=raw),
            ],
            is_last=True,
        ),
    ]
    return [first_turn, text_chunks([], "leader final after assignment")]


class RejectingModel(ScriptedModel):
    """A model whose provider rejects the request (e.g. an invalid schema)."""

    def __init__(self, error: Exception) -> None:
        super().__init__([text_chunks([], "unused")])
        self.error = error

    async def _call_api(self, *args: Any, **kwargs: Any) -> AsyncGenerator[ChatResponse, None]:
        self.calls += 1
        raise self.error


def build_pipeline(leader_model: Any, trace: list[Any]) -> InterceptingTeamPipeline:
    leader = Agent(name="leader", system_prompt="lead the team", model=leader_model)
    member = TeamMember(
        agent=TracingAgent(
            name="m-alpha",
            system_prompt="reply with one line",
            model=ScriptedModel([text_chunks([], "hello from alpha")]),
            trace=trace,
        ),
        description="replies with one line",
    )
    return InterceptingTeamPipeline(leader=leader, members=[member], trace=trace)


async def run_scenario(leader_model: Any) -> dict[str, Any]:
    trace: list[Any] = []
    pipeline = build_pipeline(leader_model, trace)
    events: list[Any] = []
    error: Exception | None = None
    try:
        await asyncio.wait_for(_collect(pipeline, events), timeout=30)
    except Exception as exc:
        error = exc
    assignment_results = [entry[1] for entry in trace if entry[0] == "ASSIGNMENTS_DONE"]
    context_blocks = [
        [type(block).__name__ for block in message.get_content_blocks()]
        for message in pipeline.leader.state.context
    ]
    reasoning_content: list[str] = []
    try:
        formatted = await DeepSeekChatFormatter().format(pipeline.leader.state.context)
        reasoning_content = [
            message.get("reasoning_content")
            for message in formatted
            if message.get("role") == "assistant" and message.get("reasoning_content")
        ]
    except Exception as exc:
        reasoning_content = [f"<format failed: {type(exc).__name__}>"]
    return {
        "events": len(events),
        "event_types": sorted({type(event).__name__ for event in events}),
        "event_count_types": _counts(events),
        "member_stream_starts": sum(1 for entry in trace if entry[0] == "MEMBER_STREAM_START"),
        "intercepts": [entry for entry in trace if entry[0] == "INTERCEPT"],
        "assignment_results": assignment_results,
        "leader_context_blocks": context_blocks,
        "formatted_assistant_reasoning_content": reasoning_content,
        "model_calls": getattr(leader_model, "calls", None),
        "error": None
        if error is None
        else {
            "class": type(error).__name__,
            "module": type(error).__module__,
            "message": str(error)[:500],
        },
    }


async def _collect(pipeline: InterceptingTeamPipeline, events: list[Any]) -> Any:
    async for event in pipeline.reply_stream(UserMsg(name="user", content="go")):
        events.append(event)
    return events


def _counts(events: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[type(event).__name__] = counts.get(type(event).__name__, 0) + 1
    return dict(sorted(counts.items()))


class _FakeCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(choices=[], usage=None, id="fake-completion", model="fake-model")


class _FakeClient:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()


async def request_kwargs(
    *,
    thinking: bool,
    choice: ToolChoice | None,
) -> dict[str, Any]:
    """Build one DeepSeek request against a fake client and return its kwargs."""

    model = object.__new__(DeepSeekChatModel)
    model.parameters = DeepSeekChatModel.Parameters(
        max_tokens=2048,
        thinking_enable=thinking,
    )
    model.stream = False
    model.formatter = DeepSeekChatFormatter()
    fake = _FakeClient()
    model.client = fake
    await model._call_api(
        "deepseek-chat",
        messages=[UserMsg(name="user", content="probe")],
        tools=[TEAM_ASSIGN_SCHEMA],
        tool_choice=choice,
    )
    return fake.completions.requests[0]


def ollama_tool_choice(choice: ToolChoice) -> dict[str, Any]:
    """Call Ollama's formatter without constructing a client."""

    model = object.__new__(OllamaChatModel)
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Capture()
    logger = logging.getLogger("as")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        tools, formatted = model._format_tools([TEAM_ASSIGN_SCHEMA], choice)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    return {
        "formatted_tool_choice": formatted,
        "tools_kept": [tool["function"]["name"] for tool in tools or []],
        "warnings": records,
    }


async def main() -> None:
    result: dict[str, Any] = {
        "agentscope_version": agentscope.__version__,
        "python": sys.version.split()[0],
    }

    thinking_model = ScriptedModel(leader_script_with_thinking())
    result["thinking_plus_assign"] = await run_scenario(thinking_model)

    fallback_model = ScriptedModel([text_chunks([], "leader answers directly")])
    result["no_assignment_fallback"] = await run_scenario(fallback_model)

    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    bad_request = openai.BadRequestError(
        "probe: provider rejected the tool schema",
        response=httpx.Response(400, request=request),
        body=None,
    )
    result["schema_rejected"] = await run_scenario(RejectingModel(bad_request))

    result["deepseek_tool_choice_required_thinking_on"] = {
        key: value
        for key, value in (await request_kwargs(thinking=True, choice=ToolChoice(mode="required"))).items()
        if key in {"tools", "tool_choice", "extra_body", "max_tokens", "stream"}
    }
    result["deepseek_tool_choice_named_thinking_off"] = {
        key: value
        for key, value in (await request_kwargs(thinking=False, choice=ToolChoice(mode="TeamAssign"))).items()
        if key in {"tools", "tool_choice", "extra_body"}
    }
    result["deepseek_tool_choice_absent"] = {
        key: value
        for key, value in (await request_kwargs(thinking=False, choice=None)).items()
        if key in {"tools", "tool_choice", "extra_body"}
    }

    result["ollama_tool_choice_required"] = ollama_tool_choice(ToolChoice(mode="required"))
    result["ollama_tool_choice_named"] = ollama_tool_choice(
        ToolChoice(mode="TeamAssign"),
    )

    unknown_choice = ToolChoice(mode="NotATool")
    try:
        base = object.__new__(DeepSeekChatModel)
        base._validate_tool_choice(unknown_choice, [TEAM_ASSIGN_SCHEMA])
        validation = "accepted"
    except ValueError as exc:
        validation = f"ValueError: {exc}"
    result["validate_unknown_tool_choice"] = validation

    agentscope_package = Path(inspect.getfile(DeepSeekChatModel)).resolve().parents[2]
    team_source = (agentscope_package / "pipeline" / "_team_pipeline.py").read_text()
    result["team_pipeline_tool_choice_mentions"] = team_source.count("tool_choice")
    result["deepseek_init_max_retries_default"] = (
        inspect.signature(
            DeepSeekChatModel.__init__,
        )
        .parameters["max_retries"]
        .default
    )
    result["deepseek_retryable_exceptions"] = [
        f"{cls.__module__}.{cls.__name__}" for cls in DeepSeekChatModel._get_retryable_exceptions()
    ]
    result["deepseek_bad_request_is_retryable"] = any(
        cls is openai.BadRequestError for cls in DeepSeekChatModel._get_retryable_exceptions()
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
