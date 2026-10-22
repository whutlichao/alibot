"""全局记忆只读：两处读、写项目侧，缺失 / 越界只少读一处（ADR-0031 D5/D6/D9）。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.model import ChatResponse
from agentscope.tool import LocalBackend
from alibot.memory import GlobalMemoryReadMiddleware, assemble_memory
from alibot.permissions import build_permission_context
from alibot.workspace import WorkspaceSettings

from tests.test_approval_seam import _ScriptedModel, _StubModelSeam, _tool_call

MEMBER = "member"
GLOBAL_TEXT = "全局记忆：用户偏好简体中文，跨项目复用。"
PROJECT_OLD = "项目既有记忆：本频道的历史背景。"
PROJECT_NEW = "项目记忆：本项目使用 pytest。"


class _MemoryModel(_ScriptedModel):
    """记录每次模型输入，便于断言全局内容真的进入模型可见输入。"""

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


def _global_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 HOME + 临时全局根；测试绝不读写真实的 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home / ".alibot"


def _global_memory(global_root: Path, content: str = GLOBAL_TEXT) -> Path:
    directory = global_root / "agents" / MEMBER / "memory" / "Memory"
    directory.mkdir(parents=True)
    (directory / "MEMORY.md").write_text(content, encoding="utf-8")
    return directory


def _snapshot(directory: Path) -> tuple[int, list[str]]:
    return (directory / "MEMORY.md").stat().st_mtime_ns, sorted(item.name for item in directory.iterdir())


def _capture_middlewares(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    middlewares: list[Any] = []
    original = Agent.__init__

    def capture(self: Any, *args: Any, **kwargs: Any) -> None:
        middlewares.extend(kwargs.get("middlewares", []))
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    return middlewares


def _dm_round(
    tmp_path: Path,
    global_root: Path,
    model: _MemoryModel,
    *,
    memory_mode: str = "agentic",
) -> Any:
    data_root = tmp_path / "data"
    seam = _StubModelSeam(model, runtime_root=data_root, global_home=str(global_root))
    return seam.run_round(
        {"id": MEMBER, "model_id": "openai:stub", "memory_mode": memory_mode},
        [{"body": "开始", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
        direct_message=True,
    )


def test_project_round_reads_both_memories_and_writes_only_the_project_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    project_memory = project / ".alibot" / "memory" / MEMBER / "Memory" / "MEMORY.md"
    project_memory.parent.mkdir(parents=True)
    project_memory.write_text(PROJECT_OLD, encoding="utf-8")
    before = _snapshot(global_memory)
    middlewares = _capture_middlewares(monkeypatch)
    model = _MemoryModel(
        [
            _tool_call("read-memory", "Read", {"file_path": str(project_memory)}),
            _tool_call(
                "write-memory",
                "Write",
                {"file_path": str(project_memory), "content": PROJECT_NEW},
            ),
            _tool_call(
                "write-global",
                "Write",
                {"file_path": str(global_memory / "MEMORY.md"), "content": "篡改全局"},
            ),
            [ChatResponse(content=[TextBlock(text="记住了")], is_last=True)],
        ]
    )
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project), permission_mode="trusted-writes"),
        global_home=str(global_root),
    )
    outcome = seam.run_round(
        {"id": MEMBER, "model_id": "openai:stub", "memory_mode": "agentic"},
        [{"body": "记住项目偏好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )

    assert outcome.status == "replied", outcome.reason
    assert any(PROJECT_OLD in item and GLOBAL_TEXT in item for item in model.inputs)
    assert "全局记忆（只读）" in model.inputs[-1]
    assert project_memory.read_text(encoding="utf-8") == PROJECT_NEW
    assert "path is read-only" in model.inputs[-1]
    assert (global_memory / "MEMORY.md").read_text(encoding="utf-8") == GLOBAL_TEXT
    assert _snapshot(global_memory) == before
    assert any(isinstance(item, GlobalMemoryReadMiddleware) for item in middlewares)
    assert any(isinstance(item, AgenticMemoryMiddleware) for item in middlewares)
    assert (project / ".alibot" / "sessions").is_dir()
    assert not (global_root / "sessions").exists()
    assert not (global_root / "agents" / MEMBER / "sessions").exists()


def test_dm_round_reads_global_memory_and_writes_the_data_directory_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    before = _snapshot(global_memory)
    dm_memory = tmp_path / "data" / ".alibot" / "memory" / MEMBER / "Memory" / "MEMORY.md"
    dm_memory.parent.mkdir(parents=True)
    dm_memory.write_text(PROJECT_OLD, encoding="utf-8")
    model = _MemoryModel(
        [
            _tool_call("read-memory", "Read", {"file_path": str(dm_memory)}),
            _tool_call("write-memory", "Write", {"file_path": str(dm_memory), "content": PROJECT_NEW}),
            [ChatResponse(content=[TextBlock(text="记住了")], is_last=True)],
        ]
    )
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert any(PROJECT_OLD in item and GLOBAL_TEXT in item for item in model.inputs)
    assert dm_memory.read_text(encoding="utf-8") == PROJECT_NEW
    assert _snapshot(global_memory) == before


def test_dm_memory_tools_can_read_but_cannot_write_the_global_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    topic = global_memory / "topic.md"
    topic.write_text("全局主题：跨项目偏好。", encoding="utf-8")
    before = _snapshot(global_memory)
    model = _MemoryModel(
        [
            _tool_call("read-topic", "Read", {"file_path": str(topic)}),
            _tool_call("write-topic", "Write", {"file_path": str(topic), "content": "篡改全局"}),
            [ChatResponse(content=[TextBlock(text="只读")], is_last=True)],
        ]
    )
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    # 主题文件不在 MEMORY.md 注入里，只能经记忆侧的只读读取通道读到。
    assert "全局主题：跨项目偏好。" in model.inputs[-1]
    assert "path is read-only" in model.inputs[-1]
    assert topic.read_text(encoding="utf-8") == "全局主题：跨项目偏好。"
    assert _snapshot(global_memory) == before


def test_dm_working_directories_stay_the_project_side_memory_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    captured: list[dict[str, Any]] = []
    original = Agent.__init__

    def capture(self: Any, *args: Any, **kwargs: Any) -> None:
        captured.append(kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    directories = captured[0]["state"].permission_context.working_directories
    dm_memory = tmp_path / "data" / ".alibot" / "memory" / MEMBER / "Memory"
    assert set(directories) == {str(dm_memory)}
    assert str(global_memory) not in directories


def test_global_memory_write_through_a_path_alias_is_still_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    alias = tmp_path / "alias"
    os.symlink(global_root, alias, target_is_directory=True)
    target = alias / "agents" / MEMBER / "memory" / "Memory" / "MEMORY.md"
    before = _snapshot(global_memory)
    model = _MemoryModel(
        [
            _tool_call("write-alias", "Write", {"file_path": str(target), "content": "篡改全局"}),
            [ChatResponse(content=[TextBlock(text="只读")], is_last=True)],
        ]
    )
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert "path is read-only" in model.inputs[-1]
    assert (global_memory / "MEMORY.md").read_text(encoding="utf-8") == GLOBAL_TEXT
    assert _snapshot(global_memory) == before


def test_missing_global_memory_degrades_with_a_log_without_creating_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert not global_root.exists()
    records = [record for record in caplog.records if "global memory" in record.getMessage()]
    assert len(records) == 1


def test_non_utf8_global_memory_is_skipped_with_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = global_root / "agents" / MEMBER / "memory" / "Memory"
    global_memory.mkdir(parents=True)
    (global_memory / "MEMORY.md").write_bytes(b"\xff\xfe\x00bad")
    before = _snapshot(global_memory)
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert _snapshot(global_memory) == before
    assert any("global memory" in record.getMessage() for record in caplog.records)


def test_unreadable_global_memory_file_is_skipped_with_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = global_root / "agents" / MEMBER / "memory" / "Memory"
    global_memory.mkdir(parents=True)
    # MEMORY.md 是目录：read_bytes 抛 IsADirectoryError（OSError 分支），只少读一处。
    (global_memory / "MEMORY.md").mkdir()
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert (global_memory / "MEMORY.md").is_dir()
    assert sorted(item.name for item in global_memory.iterdir()) == ["MEMORY.md"]
    assert any("global memory" in record.getMessage() for record in caplog.records)


def test_global_member_home_symlink_outside_the_global_root_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    outside = tmp_path / "outside" / MEMBER
    outside_memory = outside / "memory" / "Memory"
    outside_memory.mkdir(parents=True)
    (outside_memory / "MEMORY.md").write_text("越界全局记忆", encoding="utf-8")
    (global_root / "agents").mkdir(parents=True)
    os.symlink(outside, global_root / "agents" / MEMBER, target_is_directory=True)
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert "越界全局记忆" not in "\n".join(model.inputs)
    assert (outside_memory / "MEMORY.md").read_text(encoding="utf-8") == "越界全局记忆"
    assert any("global memory" in record.getMessage() for record in caplog.records)


def test_memory_off_does_not_touch_either_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    before = _snapshot(global_memory)
    middlewares = _capture_middlewares(monkeypatch)
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="记忆关闭")], is_last=True)]])
    outcome = _dm_round(tmp_path, global_root, model, memory_mode="off")

    assert outcome.status == "replied", outcome.reason
    assert GLOBAL_TEXT not in "\n".join(model.inputs)
    assert not any(isinstance(item, GlobalMemoryReadMiddleware) for item in middlewares)
    assert not any(isinstance(item, AgenticMemoryMiddleware) for item in middlewares)
    assert not (tmp_path / "data" / ".alibot" / "memory").exists()
    assert _snapshot(global_memory) == before


def test_docker_global_memory_visible_path_maps_to_the_container_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    before = _snapshot(global_memory)
    mount = SimpleNamespace(
        settings=SimpleNamespace(is_docker=True),
        workspace=SimpleNamespace(get_backend=lambda: LocalBackend()),
        tools=[],
    )
    context = build_permission_context("ask-writes", [], root=str(project))
    middlewares, tools = assemble_memory(
        str(project), MEMBER, context, mount=mount, global_home=str(global_root)
    )

    assert tools == []
    middleware = next(item for item in middlewares if isinstance(item, GlobalMemoryReadMiddleware))
    prompt = asyncio.run(middleware.on_system_prompt(None, "base"))
    assert GLOBAL_TEXT in prompt
    assert "/alibot-home/agents/member/memory/Memory" in prompt
    assert str(global_memory) not in prompt
    assert _snapshot(global_memory) == before
