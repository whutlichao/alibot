"""Shared fixtures for the issue #132 research probes.

These probes run against the installed ``agentscope==2.0.10`` and do not
import anything from ``app/alibot``.  They are deliberately deterministic:
a scripted chat model replaces the provider so every reading in
``app/specs/issue-132-research.md`` is reproducible offline.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any, ClassVar

from agentscope.agent import Agent
from agentscope.event import AgentEvent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.pipeline import TeamPipeline
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class ScriptedModel(ChatModelBase):
    """A deterministic streaming model: one scripted response per call."""

    def __init__(self, script: Sequence[Sequence[ChatResponse]]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="scripted",
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


class EchoTool(ToolBase):
    """An always-allowed local tool used to make ToolCall/ToolResult events."""

    name = "Echo"
    description = "Echo the given text in upper case."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    is_concurrency_safe = True
    is_read_only = True

    async def __call__(
        self,
        text: str,
        **kwargs: Any,
    ) -> AsyncGenerator[ToolChunk, None]:
        yield ToolChunk(content=[TextBlock(text=text.upper())])

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Echo is always allowed in the probe.",
        )


class AskTool(ToolBase):
    """A local tool that always asks the user, to produce a HITL park."""

    name = "NeedsApproval"
    description = "Require a human confirmation before running."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    is_concurrency_safe = False
    is_read_only = False

    async def __call__(
        self,
        text: str,
        **kwargs: Any,
    ) -> AsyncGenerator[ToolChunk, None]:
        yield ToolChunk(content=[TextBlock(text=f"approved:{text}")])

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="The probe requires human confirmation.",
        )


class TracingAgent(Agent):
    """An ``Agent`` that appends a trace line when its stream starts."""

    def __init__(self, *args: Any, trace: list[tuple[Any, ...]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._trace = trace

    async def reply_stream(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        self._trace.append(("MEMBER_STREAM_START", self.name))
        async for event in super().reply_stream(*args, **kwargs):
            self._trace.append(("MEMBER_EVENT", self.name, type(event).__name__))
            yield event


class InterceptingTeamPipeline(TeamPipeline):
    """Logs every ``ToolCallBlock`` before delegating to the base runner."""

    def __init__(self, *args: Any, trace: list[tuple[Any, ...]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._trace = trace

    async def _run_assignments(
        self,
        tool_calls: list[ToolCallBlock],
        results: list[Any],
        resumed: Any = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        for call in tool_calls:
            self._trace.append(
                (
                    "INTERCEPT",
                    call.id,
                    call.name,
                    self._parse(call)["member"],
                    "resumed" if resumed is not None else "fresh",
                ),
            )
        async for event in super()._run_assignments(tool_calls, results, resumed):
            yield event
        self._trace.append(("ASSIGNMENTS_DONE", len(results)))


def text_chunks(parts: Sequence[str], final: str) -> list[ChatResponse]:
    """Build a streamed text response: deltas first, complete response last."""

    chunks = [ChatResponse(content=[TextBlock(text=part)], is_last=False) for part in parts]
    chunks.append(ChatResponse(content=[TextBlock(text=final)], is_last=True))
    return chunks


def tool_call_chunks(
    call_id: str,
    name: str,
    payload: dict[str, Any],
) -> list[ChatResponse]:
    """Stream a tool call in three chunks so ToolCall* events are emitted.

    A single ``is_last=True`` chunk is not converted into block events by
    ``Agent._reasoning_impl``; the deltas must arrive in non-final chunks.
    """

    raw = json.dumps(payload)
    middle = len(raw) // 2
    return [
        ChatResponse(
            content=[ToolCallBlock(id=call_id, name=name, input=raw[:middle])],
            is_last=False,
        ),
        ChatResponse(
            content=[ToolCallBlock(id=call_id, name=name, input=raw[middle:])],
            is_last=False,
        ),
        ChatResponse(
            content=[ToolCallBlock(id=call_id, name=name, input=raw)],
            is_last=True,
        ),
    ]


def member_toolkit(tool: ToolBase) -> Toolkit:
    """A toolkit with exactly one probe tool."""

    return Toolkit(tools=[tool])
