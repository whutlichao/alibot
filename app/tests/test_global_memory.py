"""长期记忆的新落点：家目录扁平、两处可写、白名单只让出记忆文件（ADR-0032）。

判据落在**磁盘事实与模型可见输入**上：写进去的文件真的在那个路径、被拒的路径字节不变、
``system prompt`` 里的段落来自哪一份文件。全局根与 HOME 一律指向临时目录，
绝不读写真实的 ``~/.alibot``。
"""

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
from agentscope.permission import PermissionDecision
from agentscope.tool import LocalBackend
from alibot.memory import GlobalMemoryReadMiddleware, assemble_memory
from alibot.permissions import build_permission_context
from alibot.runtime_files import PartitionBackend, PartitionPermissionMiddleware
from alibot.workspace import WorkspaceSettings, is_memory_file_path

from tests.test_approval_seam import _ScriptedModel, _StubModelSeam, _tool_call

MEMBER = "member"
GLOBAL_TEXT = "全局记忆：用户偏好简体中文，跨项目复用。"
PROJECT_OLD = "项目既有记忆：本频道的历史背景。"
PROJECT_NEW = "项目记忆：本项目使用 pytest。"
GLOBAL_NEW = "全局记忆：所有项目都用 pytest。"


class _MemoryModel(_ScriptedModel):
    """记录每次模型输入，便于断言内容真的进入模型可见输入。

    ``generate_structured_output`` 被重写为**直接返回空选择**：记忆中间件的异步召回会对
    工作目录里的 ``<topic>.md`` 发起一次结构化选择调用，而脚本模型的脚本按顺序消费，多一次
    调用就会把「读 / 写记忆」的脚本错位。召回不是本文件的判据（落点与白名单才是），
    因此与 ``test_runtime_context.py`` 的压缩模型同一处理方式。
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


def _global_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 HOME + 临时全局根；测试绝不读写真实的 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home / ".alibot"


def _global_home(global_root: Path) -> Path:
    """全局成员家目录：``<全局根>/agents/<member_id>/``（扁平，没有中间层）。"""
    return global_root / "agents" / MEMBER


def _global_memory(global_root: Path, content: str = GLOBAL_TEXT) -> Path:
    directory = _global_home(global_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "MEMORY.md"
    path.write_text(content, encoding="utf-8")
    return path


def _snapshot(path: Path) -> tuple[int, bytes]:
    return path.stat().st_mtime_ns, path.read_bytes()


async def _check_write(middleware: PartitionPermissionMiddleware, path: str) -> Any:
    """跑一次 ``Write`` 的权限判定；放行时返回下游标记，拒绝时返回 ``PermissionDecision``。"""
    tool = SimpleNamespace(name="Write")

    async def _next(**_kwargs: Any) -> str:
        return "delegated"

    return await middleware.on_check_permission(
        None, {"tool": tool, "tool_input": {"file_path": path}, "tool_call": None}, _next
    )


def _listing(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(item.relative_to(root)) for item in root.rglob("*"))


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


# ─── 1. 有项目根：项目家目录读写，全局家目录的记忆可写、其余只读 ───────────
def test_project_round_writes_both_memories_and_rejects_every_other_home_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    global_home = _global_home(global_root)
    project_home = project / ".alibot" / "agents" / MEMBER
    project_home.mkdir(parents=True)
    project_memory = project_home / "MEMORY.md"
    project_memory.write_text(PROJECT_OLD, encoding="utf-8")
    # 全局家目录里的三份只读文件：规则基座、MCP 声明、技能字节。
    global_rules = global_home / "AGENTS.md"
    global_rules.write_text("全局规则\n", encoding="utf-8")
    global_mcp = global_home / "mcp.json"
    global_mcp.write_text('{"servers": []}\n', encoding="utf-8")
    global_skill = global_home / "skills" / "x.md"
    global_skill.parent.mkdir(parents=True)
    global_skill.write_text("技能字节\n", encoding="utf-8")
    readonly_before = {path: _snapshot(path) for path in (global_rules, global_mcp, global_skill)}
    middlewares = _capture_middlewares(monkeypatch)
    model = _MemoryModel(
        [
            _tool_call("read-memory", "Read", {"file_path": str(project_memory)}),
            _tool_call(
                "write-project",
                "Write",
                {"file_path": str(project_memory), "content": PROJECT_NEW},
            ),
            # 全局 MEMORY.md 与三份只读文件都先读一遍：Write 工具要求「写前先读」，
            # 这样被拒的就只可能是白名单判据本身，而不是这条前置条件。
            _tool_call("read-global", "Read", {"file_path": str(global_memory)}),
            _tool_call(
                "write-global",
                "Write",
                {"file_path": str(global_memory), "content": GLOBAL_NEW},
            ),
            _tool_call(
                "write-topic",
                "Write",
                {"file_path": str(global_home / "topic.md"), "content": "全局主题：跨项目偏好。"},
            ),
            _tool_call("read-rules", "Read", {"file_path": str(global_rules)}),
            _tool_call("write-rules", "Write", {"file_path": str(global_rules), "content": "篡改规则"}),
            _tool_call("read-mcp", "Read", {"file_path": str(global_mcp)}),
            _tool_call("write-mcp", "Write", {"file_path": str(global_mcp), "content": "{}"}),
            _tool_call("read-skill", "Read", {"file_path": str(global_skill)}),
            _tool_call("write-skill", "Write", {"file_path": str(global_skill), "content": "篡改技能"}),
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
    # 两处内容都进入模型可见输入：项目段来自中间件，全局段来自宿主只读注入。
    assert any(PROJECT_OLD in item and GLOBAL_TEXT in item for item in model.inputs)
    assert "全局记忆" in model.inputs[-1]
    # 写：项目侧与全局侧的记忆文件都成功。
    assert project_memory.read_text(encoding="utf-8") == PROJECT_NEW
    assert global_memory.read_text(encoding="utf-8") == GLOBAL_NEW
    assert (global_home / "topic.md").read_text(encoding="utf-8") == "全局主题：跨项目偏好。"
    # 只读：规则 / MCP / 技能字节一个字节都没变，且拒绝是可读文案。
    assert "path is read-only" in model.inputs[-1]
    for path, before in readonly_before.items():
        assert _snapshot(path) == before, f"{path.name} 不得被改写"
    assert any(isinstance(item, GlobalMemoryReadMiddleware) for item in middlewares)
    assert any(isinstance(item, AgenticMemoryMiddleware) for item in middlewares)
    assert (project / ".alibot" / "sessions").is_dir()
    assert not (global_root / "sessions").exists()
    assert not (global_home / "sessions").exists()


# ─── 2. 无项目根：全局家目录是唯一落点，数据目录下不再出现 ``.alibot/memory`` ──
def test_dm_round_writes_memory_to_the_global_home_and_never_to_the_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    model = _MemoryModel(
        [
            _tool_call("read-memory", "Read", {"file_path": str(global_memory)}),
            _tool_call(
                "write-memory",
                "Write",
                {"file_path": str(global_memory), "content": GLOBAL_NEW},
            ),
            [ChatResponse(content=[TextBlock(text="记住了")], is_last=True)],
        ]
    )
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert any(GLOBAL_TEXT in item for item in model.inputs)
    assert global_memory.read_text(encoding="utf-8") == GLOBAL_NEW
    # 数据目录下不再出现镜像的 ``.alibot/memory``（ADR-0032 §四）。
    assert not (tmp_path / "data" / ".alibot" / "memory").exists()
    assert not (tmp_path / "data" / ".alibot").exists()
    # 会话状态落在数据目录的 ``sessions/``（不在 ``.alibot`` 之下）。
    assert (tmp_path / "data" / "sessions" / f"target--{MEMBER}" / "state.json").is_file()


# ─── 3. 无项目根：全局家目录里只有记忆文件可写 ────────────────────────────
def test_dm_can_write_the_global_topic_file_but_not_rules_mcp_or_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_home = _global_home(global_root)
    global_home.mkdir(parents=True)
    topic = global_home / "topic.md"
    topic.write_text("全局主题：跨项目偏好。", encoding="utf-8")
    rules = global_home / "AGENTS.md"
    rules.write_text("全局规则\n", encoding="utf-8")
    mcp = global_home / "mcp.json"
    mcp.write_text('{"servers": []}\n', encoding="utf-8")
    skill = global_home / "skills" / "x.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("技能字节\n", encoding="utf-8")
    readonly_before = {path: _snapshot(path) for path in (rules, mcp, skill)}
    model = _MemoryModel(
        [
            _tool_call("read-topic", "Read", {"file_path": str(topic)}),
            _tool_call("write-topic", "Write", {"file_path": str(topic), "content": "全局主题：已更新。"}),
            _tool_call("read-rules", "Read", {"file_path": str(rules)}),
            _tool_call("write-rules", "Write", {"file_path": str(rules), "content": "篡改规则"}),
            _tool_call("read-mcp", "Read", {"file_path": str(mcp)}),
            _tool_call("write-mcp", "Write", {"file_path": str(mcp), "content": "{}"}),
            _tool_call("read-skill", "Read", {"file_path": str(skill)}),
            _tool_call("write-skill", "Write", {"file_path": str(skill), "content": "篡改技能"}),
            [ChatResponse(content=[TextBlock(text="只写记忆")], is_last=True)],
        ]
    )
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    # 主题文件可读（不在 MEMORY.md 注入里，只能经记忆侧的读取通道读到）。
    assert "全局主题：跨项目偏好。" in model.inputs[-1]
    assert topic.read_text(encoding="utf-8") == "全局主题：已更新。"
    assert "path is read-only" in model.inputs[-1]
    for path, before in readonly_before.items():
        assert _snapshot(path) == before, f"{path.name} 不得被改写"


def test_global_home_subdirectory_md_is_rejected_even_though_it_is_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """白名单按「根下一级」判定：``sub/note.md`` 仍在白名单之外。"""
    global_root = _global_root(tmp_path, monkeypatch)
    global_home = _global_home(global_root)
    nested = global_home / "sub" / "note.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("子目录笔记\n", encoding="utf-8")
    before = _snapshot(nested)
    model = _MemoryModel(
        [
            _tool_call("read-nested", "Read", {"file_path": str(nested)}),
            _tool_call("write-nested", "Write", {"file_path": str(nested), "content": "篡改"}),
            [ChatResponse(content=[TextBlock(text="拒绝")], is_last=True)],
        ]
    )
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert "path is read-only" in model.inputs[-1]
    assert _snapshot(nested) == before


# ─── 4. 工作目录：有项目根挂两处，无项目根只挂全局家目录 ─────────────────
def test_working_directories_cover_both_homes_when_a_project_root_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    global_root = _global_root(tmp_path, monkeypatch)
    _global_memory(global_root)
    captured: list[dict[str, Any]] = []
    original = Agent.__init__

    def capture(self: Any, *args: Any, **kwargs: Any) -> None:
        captured.append(kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project), permission_mode="trusted-writes"),
        global_home=str(global_root),
    )
    outcome = seam.run_round(
        {"id": MEMBER, "model_id": "openai:stub", "memory_mode": "agentic"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )

    assert outcome.status == "replied", outcome.reason
    directories = set(captured[0]["state"].permission_context.working_directories)
    # 两处家目录都在（项目侧 + 全局侧）；项目根本身仍由既有权限上下文带上（ADR-0021 不变）。
    assert str(project / ".alibot" / "agents" / MEMBER) in directories
    assert str(_global_home(global_root)) in directories


def test_dm_working_directories_are_the_global_home_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    _global_memory(global_root)
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
    assert set(directories) == {str(_global_home(global_root))}


def test_a_relative_path_in_a_project_round_stays_under_the_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径的锚点只有 ``strict`` 一处：有项目根时它锚项目根，**不**锚到记忆家目录。

    端到端事实：相对路径的 ``MEMORY.md`` 落到 ``<项目根>/MEMORY.md``，全局家目录里的
    ``MEMORY.md`` 字节不变（家目录不是第二个工作根，ADR-0032 §五）。
    """
    project = tmp_path / "project"
    project.mkdir()
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    before = _snapshot(global_memory)
    monkeypatch.chdir(project)
    model = _MemoryModel(
        [
            _tool_call("write-relative", "Write", {"file_path": "MEMORY.md", "content": "项目相对记忆。"}),
            [ChatResponse(content=[TextBlock(text="写好了")], is_last=True)],
        ]
    )
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project), permission_mode="trusted-writes"),
        global_home=str(global_root),
    )
    outcome = seam.run_round(
        {"id": MEMBER, "model_id": "openai:stub", "memory_mode": "agentic"},
        [{"body": "写记忆", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )

    assert outcome.status == "replied", outcome.reason
    assert (project / "MEMORY.md").read_text(encoding="utf-8") == "项目相对记忆。"
    assert _snapshot(global_memory) == before, "相对路径不得被锚到记忆家目录"


def test_the_strict_flag_alone_decides_whether_a_relative_path_is_reanchored(tmp_path: Path) -> None:
    """``strict`` 是相对路径锚点的**唯一**判据（区分“删除 ``anchor_relative``”与“恒为 ``True``”）。

    严格档（无项目根：本层是唯一准入面）把相对路径锚到记忆分区，因此根下的 ``AGENTS.md``
    撞上白名单 → 可读拒绝；宽松档（有项目根）**不**锚家目录，相对路径原样交给项目根守卫，
    本层不自行判只读。旧实现（``anchor_relative`` 恒 ``True``）会让宽松档也锚到家目录，
    于是 ``strict=False`` 的那一行变成 ``path is read-only``，这条断言就会失败。
    """
    home = tmp_path / "home"
    home.mkdir()
    backend = PartitionBackend(LocalBackend(), home, str(home), write_filter=is_memory_file_path)
    decisions = {
        strict: asyncio.run(_check_write(PartitionPermissionMiddleware(backend, strict=strict), "AGENTS.md"))
        for strict in (True, False)
    }

    assert "path is read-only" in str(getattr(decisions[True], "message", "")), decisions[True]
    assert not isinstance(decisions[False], PermissionDecision), "宽松档不得把相对路径锚到记忆家目录"


# ─── 5. 守卫与降级：越界 symlink、缺失、非 UTF-8、不可读 ────────────────
def test_global_memory_write_through_a_path_alias_is_still_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    _global_memory(global_root)
    rules = _global_home(global_root) / "AGENTS.md"
    rules.write_text("全局规则\n", encoding="utf-8")
    alias = tmp_path / "alias"
    os.symlink(global_root, alias, target_is_directory=True)
    target = alias / "agents" / MEMBER / "AGENTS.md"
    before = _snapshot(rules)
    model = _MemoryModel(
        [
            _tool_call("read-rules", "Read", {"file_path": str(rules)}),
            _tool_call("write-alias", "Write", {"file_path": str(target), "content": "篡改全局"}),
            [ChatResponse(content=[TextBlock(text="只读")], is_last=True)],
        ]
    )
    outcome = _dm_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert "path is read-only" in model.inputs[-1]
    assert _snapshot(rules) == before


def _project_round(
    tmp_path: Path,
    global_root: Path,
    model: _MemoryModel,
    *,
    memory_mode: str = "agentic",
) -> Any:
    """有项目根的轮次：全局段由宿主只读注入，因此降级日志出现在这一支。"""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project), permission_mode="trusted-writes"),
        global_home=str(global_root),
    )
    return seam.run_round(
        {"id": MEMBER, "model_id": "openai:stub", "memory_mode": memory_mode},
        [{"body": "开始", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )


def test_missing_global_memory_degrades_with_a_log_and_creates_only_the_home_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """缺文件 ⇒ 该段缺席 + 一条日志；应用只建 ``agents/<member_id>/`` 这一级（含 MEMORY.md）。"""
    global_root = _global_root(tmp_path, monkeypatch)
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _project_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    home = _global_home(global_root)
    assert home.is_dir()
    # 但**不**创建 skills/、AGENTS.md、mcp.json——人的维护面只让出一格。
    assert not (global_root / "skills").exists()
    assert not (home / "AGENTS.md").exists()
    assert not (home / "mcp.json").exists()
    records = [record for record in caplog.records if "global memory" in record.getMessage()]
    assert len(records) == 1


def test_non_utf8_global_memory_is_skipped_with_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    home = _global_home(global_root)
    home.mkdir(parents=True)
    (home / "MEMORY.md").write_bytes(b"\xff\xfe\x00bad")
    before = _snapshot(home / "MEMORY.md")
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _project_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert _snapshot(home / "MEMORY.md") == before
    assert any("global memory" in record.getMessage() for record in caplog.records)


def test_unreadable_global_memory_file_is_skipped_with_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    home = _global_home(global_root)
    home.mkdir(parents=True)
    # MEMORY.md 是目录：read_bytes 抛 IsADirectoryError（OSError 分支），只少读一处。
    (home / "MEMORY.md").mkdir()
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _project_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert (home / "MEMORY.md").is_dir()
    assert sorted(item.name for item in home.iterdir()) == ["MEMORY.md"]
    assert any("global memory" in record.message for record in caplog.records)


def test_global_member_home_symlink_outside_the_global_root_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    (outside / "MEMORY.md").write_text("越界全局记忆", encoding="utf-8")
    (global_root / "agents").mkdir(parents=True)
    os.symlink(outside, _global_home(global_root), target_is_directory=True)
    caplog.set_level(logging.INFO, logger="alibot.memory")
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="照常收口")], is_last=True)]])
    outcome = _project_round(tmp_path, global_root, model)

    assert outcome.status == "replied", outcome.reason
    assert "越界全局记忆" not in "\n".join(model.inputs)
    assert (outside / "MEMORY.md").read_text(encoding="utf-8") == "越界全局记忆"
    assert any("global memory" in record.message for record in caplog.records)


# ─── 6. 关闭长期记忆：两处都不挂、不建目录、不装中间件 ───────────────────
def test_memory_off_does_not_touch_either_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_root = _global_root(tmp_path, monkeypatch)
    global_memory = _global_memory(global_root)
    before = _snapshot(global_memory)
    listing_before = _listing(global_root)
    middlewares = _capture_middlewares(monkeypatch)
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="记忆关闭")], is_last=True)]])
    outcome = _dm_round(tmp_path, global_root, model, memory_mode="off")

    assert outcome.status == "replied", outcome.reason
    assert GLOBAL_TEXT not in "\n".join(model.inputs)
    assert not any(isinstance(item, GlobalMemoryReadMiddleware) for item in middlewares)
    assert not any(isinstance(item, AgenticMemoryMiddleware) for item in middlewares)
    assert not (tmp_path / "data" / ".alibot" / "memory").exists()
    assert _snapshot(global_memory) == before
    assert _listing(global_root) == listing_before


def test_memory_off_in_a_project_round_creates_no_home_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    global_root = _global_root(tmp_path, monkeypatch)
    middlewares = _capture_middlewares(monkeypatch)
    model = _MemoryModel([[ChatResponse(content=[TextBlock(text="记忆关闭")], is_last=True)]])
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: WorkspaceSettings(root=str(project)),
        global_home=str(global_root),
    )
    outcome = seam.run_round(
        {"id": MEMBER, "model_id": "openai:stub", "memory_mode": "off"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="channel",
    )

    assert outcome.status == "replied", outcome.reason
    assert not any(isinstance(item, AgenticMemoryMiddleware) for item in middlewares)
    assert not (project / ".alibot" / "agents" / MEMBER / "MEMORY.md").exists()
    assert not (_global_home(global_root) / "MEMORY.md").exists()


# ─── 7. Docker：全局家目录可见路径映射到容器挂载点 ────────────────────────
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
    assert "/alibot-home/agents/member" in prompt
    assert str(global_memory) not in prompt
    assert _snapshot(global_memory) == before


# ─── 8. 无项目根又没有全局根：这一轮没有记忆落点，不建任何目录 ─────────────
def test_no_project_root_and_no_global_home_assembles_nothing(tmp_path: Path) -> None:
    context = build_permission_context("ask-writes", [], root=None)
    middlewares, tools = assemble_memory(None, MEMBER, context, global_home=None)

    assert middlewares == []
    assert tools == []
    assert not (tmp_path / "data").exists()
