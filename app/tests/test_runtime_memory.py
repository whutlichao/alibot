"""AgenticMemory 的跨会话写回与召回；DM 只有记忆家目录的文件权限（ADR-0032）。

无项目根的轮次（DM / 频道未配项目根）里，全局家目录 `~/.alibot/agents/<member_id>/` 是唯一
记忆落点，`MEMORY.md` 直接放在这一层（扁平，没有 `memory/Memory/` 中间层）。会话状态落在
`<数据目录>/sessions/<session_id>/`，不再写 `<数据目录>/.alibot/**`。
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.model import ChatResponse
from alibot.runtime import SessionOffloader

from tests.test_approval_seam import _ScriptedModel, _StubModelSeam, _tool_call


class MemoryModel(_ScriptedModel):
    """记录每次模型输入；召回调用固定返回空选择。

    ``generate_structured_output`` 被重写为**直接返回空选择**：记忆中间件的异步召回会对家目录里
    的 ``<topic>.md`` / ``AGENTS.md`` 发起一次结构化选择调用，而脚本模型按顺序消费脚本，多一次
    调用就会让「读 / 写记忆」的脚本错位。召回不是本文件的判据（落点与白名单才是）。
    """

    def __init__(self, script: Any) -> None:
        super().__init__(script)
        self.inputs: list[str] = []

    async def generate_structured_output(
        self, messages: list[Any], structured_model: Any, **kwargs: Any
    ) -> Any:
        return SimpleNamespace(finished_reason="completed", usage=None, content={"selected_files": []})

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


@pytest.fixture
def global_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 HOME + 临时全局根：测试绝不读写真实的 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home / ".alibot"


def _member_home(global_root: Path, member: str = "member") -> Path:
    """全局成员家目录：``<全局根>/agents/<member_id>/``（扁平）。"""
    return global_root / "agents" / member


def _dm_seam(model: Any, tmp_path: Path, global_root: Path) -> _StubModelSeam:
    """DM 轮次：数据目录做 runtime_root，全局根是唯一记忆落点。"""
    return _StubModelSeam(model, runtime_root=tmp_path / "data", global_home=str(global_root))


def test_agentic_memory_written_in_one_session_is_injected_in_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_root: Path
) -> None:
    options: list[dict[str, Any]] = []
    original = Agent.__init__

    def capture(self, *args: Any, **kwargs: Any) -> None:
        options.append(kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    memory = _member_home(global_root) / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("旧索引\n", encoding="utf-8")
    model = MemoryModel(
        [
            _tool_call("read-index", "Read", {"file_path": str(memory)}),
            _tool_call("save", "Write", {"file_path": str(memory), "content": "用户偏好：请用简体中文。"}),
            [ChatResponse(content=[TextBlock(text="记住了")], is_last=True)],
        ]
    )
    seam = _dm_seam(model, tmp_path, global_root)
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


def test_memory_off_creates_no_memory_directory_and_adds_no_middleware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_root: Path
) -> None:
    middlewares: list[Any] = []
    original = Agent.__init__

    def capture(self, *args: Any, **kwargs: Any) -> None:
        middlewares.extend(kwargs.get("middlewares", []))
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "openai:stub", "memory_mode": "off"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )
    assert outcome.status == "replied", outcome.reason
    # 数据目录侧：不再出现 ``.alibot`` 镜像层（ADR-0032 §四），连 ``sessions/`` 也只在有轮次时建。
    assert not (tmp_path / "data" / ".alibot").exists()
    assert not _member_home(global_root).exists(), "关闭长期记忆时两处都不建目录"
    assert not any(isinstance(item, AgenticMemoryMiddleware) for item in middlewares)


def test_dm_memory_tools_cannot_read_a_project_file(tmp_path: Path, global_root: Path) -> None:
    secret = tmp_path / "project.txt"
    secret.write_text("private-project-content")
    model = MemoryModel(
        [
            _tool_call("read-project", "Read", {"file_path": str(secret)}),
            [ChatResponse(content=[TextBlock(text="无法读取项目文件")], is_last=True)],
        ]
    )
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
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
    tmp_path: Path, global_root: Path, memory_mode: str
) -> None:
    """卸载文件可读、别的会话与项目文件不可读（落点在 ``<数据目录>/sessions/``）。"""
    data_root = tmp_path / "data"
    path = asyncio.run(
        SessionOffloader(data_root, "target--member", in_project=False).offload_context(
            "session", [Msg(name="owner", content=[TextBlock(text="older-context-content")], role="user")]
        )
    )
    other = asyncio.run(
        SessionOffloader(data_root, "other--member", in_project=False).offload_context(
            "other", [Msg(name="owner", content=[TextBlock(text="private-other-session")], role="user")]
        )
    )
    assert Path(path).is_relative_to(data_root / "sessions" / "target--member" / "offload")
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
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
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
        assert not (data_root / ".alibot" / "memory").exists()


def test_sessions_land_in_the_data_directory_without_an_extra_alibot_layer(
    tmp_path: Path, global_root: Path
) -> None:
    """无项目根：``<数据目录>/sessions/<target>--<member>/state.json``，且不出现 ``.alibot`` 镜像层。"""
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="完成")], is_last=True)]])
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"seq": 1, "body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )

    assert outcome.status == "replied", outcome.reason
    data_root = tmp_path / "data"
    assert (data_root / "sessions" / "target--member" / "state.json").is_file()
    assert not (data_root / ".alibot").exists(), "数据目录下不得再出现 .alibot 镜像层"


def test_project_round_sessions_land_under_the_project_alibot_partition(
    tmp_path: Path, global_root: Path
) -> None:
    """有项目根：``<项目根>/.alibot/sessions/<target>--<member>/state.json``（与 local/docker 现状一致）。"""
    from alibot.workspace import WorkspaceSettings

    project = tmp_path / "project"
    project.mkdir()
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="完成")], is_last=True)]])
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project)),
        global_home=str(global_root),
    )
    outcome = seam.run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"seq": 1, "body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )

    assert outcome.status == "replied", outcome.reason
    assert (project / ".alibot" / "sessions" / "channel--member" / "state.json").is_file()
    assert not (project / "sessions").exists(), "项目根下不出现裸 sessions/"


def test_dm_round_does_not_inject_a_second_global_memory_segment(tmp_path: Path, global_root: Path) -> None:
    """无项目根时全局 ``MEMORY.md`` 只经记忆中间件出现一次（ADR-0032 §三：同一文件不重复）。"""
    memory = _member_home(global_root) / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("全局记忆：只用简体中文。", encoding="utf-8")
    model = MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    outcome = _dm_seam(model, tmp_path, global_root).run_round(
        {"id": "member", "model_id": "openai:stub", "memory_mode": "agentic"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )

    assert outcome.status == "replied", outcome.reason
    system_prompt = model.inputs[-1]
    assert system_prompt.count("全局记忆：只用简体中文。") == 1, "同一个文件不得在提示里出现两次"
    assert "来源：" not in system_prompt, "无项目根时不该有第二段全局段落（那是宿主只读注入的标记）"


def test_project_round_rejects_writing_the_project_rules_file(tmp_path: Path, global_root: Path) -> None:
    """项目侧 ``AGENTS.md`` 与全局侧一样只读（ADR-0032 §三：规则文件只由人维护）。"""
    from alibot.workspace import WorkspaceSettings

    project = tmp_path / "project"
    project.mkdir()
    rules = project / ".alibot" / "agents" / "member" / "AGENTS.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("项目补充规则\n", encoding="utf-8")
    before = (rules.stat().st_mtime_ns, rules.read_bytes())
    model = MemoryModel(
        [
            _tool_call("read-rules", "Read", {"file_path": str(rules)}),
            _tool_call("write-rules", "Write", {"file_path": str(rules), "content": "篡改"}),
            [ChatResponse(content=[TextBlock(text="拒绝")], is_last=True)],
        ]
    )
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project), permission_mode="trusted-writes"),
        global_home=str(global_root),
    )
    outcome = seam.run_round(
        {"id": "member", "model_id": "openai:stub", "memory_mode": "agentic"},
        [{"body": "改规则", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )

    assert outcome.status == "replied", outcome.reason
    assert "path is read-only" in model.inputs[-1]
    assert (rules.stat().st_mtime_ns, rules.read_bytes()) == before
