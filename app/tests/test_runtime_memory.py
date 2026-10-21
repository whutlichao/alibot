"""AgenticMemory 的跨会话写回与召回，DM 只有记忆目录的文件权限。"""

from pathlib import Path
from typing import Any

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.model import ChatResponse

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
    assert "path escapes memory directory" in model.inputs[-1]
    assert "private-project-content" not in "\n".join(model.inputs)
