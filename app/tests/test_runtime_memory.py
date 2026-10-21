"""AgenticMemory 的跨会话写回与召回，DM 只有记忆目录的文件权限。"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.model import ChatResponse
from alibot.runtime import SessionOffloader

from tests.test_approval_seam import _ScriptedModel, _StubModelSeam, _tool_call


class MemoryModel(_ScriptedModel):
    def __init__(self, script: Any) -> None:
        super().__init__(script)
        self.inputs: list[str] = []

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.inputs.append(str(messages))
        return await super()._call_api(model_name, messages, tools=tools, tool_choice=tool_choice, **kwargs)


def test_agentic_memory_written_in_one_session_is_injected_in_another(tmp_path: Path, monkeypatch) -> None:
    options: list[dict[str, Any]] = []
    original = Agent.__init__

    def capture(self, *args: Any, **kwargs: Any) -> None:
        options.append(kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    memory = tmp_path / ".alibot/memory/member/Memory/MEMORY.md"
    model = MemoryModel(
        [
            _tool_call("read-index", "Read", {"file_path": str(memory)}),
            _tool_call("save", "Write", {"file_path": str(memory), "content": "用户偏好：请用简体中文。"}),
            [ChatResponse(content=[TextBlock(text="记住了")], is_last=True)],
        ]
    )
    seam = _StubModelSeam(model, runtime_root=tmp_path)
    agent = {"id": "member", "model_id": "openai:stub", "memory_mode": "agentic"}
    inputs = [{"body": "记住我的偏好", "author": {"id": "owner"}}]
    outcome = seam.run_round(agent, inputs, mentioned=False, target_id="first", direct_message=True)
    assert outcome.status == "replied", outcome.reason
    assert memory.read_text() == "用户偏好：请用简体中文。", model.inputs
    assert any(isinstance(item, AgenticMemoryMiddleware) for item in options[0]["middlewares"])
    directories = options[0]["state"].permission_context.working_directories
    assert set(directories) == {str(memory.parent)}
    outcome = seam.run_round(agent, inputs, mentioned=False, target_id="second", direct_message=True)
    assert outcome.status == "replied", outcome.reason
    assert "用户偏好：请用简体中文。" in model.inputs[-1]


def test_memory_off_creates_no_memory_directory_and_adds_no_middleware(tmp_path: Path, monkeypatch) -> None:
    middlewares: list[Any] = []
    original = Agent.__init__

    def capture(self, *args: Any, **kwargs: Any) -> None:
        middlewares.extend(kwargs.get("middlewares", []))
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    outcome = _StubModelSeam(model, runtime_root=tmp_path).run_round(
        {"id": "member", "model_id": "openai:stub", "memory_mode": "off"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )
    assert outcome.status == "replied", outcome.reason
    assert not (tmp_path / ".alibot/memory").exists()
    assert not any(isinstance(item, AgenticMemoryMiddleware) for item in middlewares)


def test_dm_memory_tools_cannot_read_a_project_file(tmp_path: Path) -> None:
    secret = tmp_path / "project.txt"
    secret.write_text("private-project-content")
    model = MemoryModel(
        [
            _tool_call("read-project", "Read", {"file_path": str(secret)}),
            [ChatResponse(content=[TextBlock(text="无法读取项目文件")], is_last=True)],
        ]
    )
    outcome = _StubModelSeam(model, runtime_root=tmp_path).run_round(
        {"id": "member", "model_id": "openai:stub", "memory_mode": "agentic"},
        [{"body": "试读项目文件", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
        direct_message=True,
    )
    assert outcome.status == "replied", outcome.reason
    assert "path escapes runtime directories" in model.inputs[-1]
    assert "private-project-content" not in "\n".join(model.inputs)


@pytest.mark.parametrize("memory_mode", ["off", "agentic"])
def test_dm_can_read_its_offloaded_context_without_reading_other_partitions(
    tmp_path: Path, memory_mode: str
) -> None:
    path = asyncio.run(
        SessionOffloader(tmp_path, "target--member").offload_context(
            "session", [Msg(name="owner", content=[TextBlock(text="older-context-content")], role="user")]
        )
    )
    other = asyncio.run(
        SessionOffloader(tmp_path, "other--member").offload_context(
            "other", [Msg(name="owner", content=[TextBlock(text="private-other-session")], role="user")]
        )
    )
    secret = tmp_path / "project.txt"
    secret.write_text("private-project-content")
    escaped = Path(path).parent / "escaped.txt"
    escaped.symlink_to(secret)
    model = MemoryModel(
        [
            _tool_call("read-offload", "Read", {"file_path": path}),
            _tool_call("read-other", "Read", {"file_path": other}),
            _tool_call("read-escaped", "Read", {"file_path": str(escaped)}),
            [ChatResponse(content=[TextBlock(text="已找回卸载上下文")], is_last=True)],
        ]
    )
    outcome = _StubModelSeam(model, runtime_root=tmp_path).run_round(
        {"id": "member", "model_id": "openai:stub", "memory_mode": memory_mode},
        [{"body": "请读取卸载文件", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
        direct_message=True,
    )
    assert outcome.status == "replied", outcome.reason
    assert "older-context-content" in model.inputs[-1]
    assert "private-other-session" not in "\n".join(model.inputs)
    assert "private-project-content" not in "\n".join(model.inputs)
    if memory_mode == "off":
        assert not (tmp_path / ".alibot/memory").exists()
