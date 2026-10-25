"""#148：LocalWorkspace 的频道项目根与文件工具根限定。

这一层是**装配层**的行为取证：AgentScope 2.0.10 的内置文件工具本身没有“必须落在工作区根内”
的准入闸门（#135 研究实测），所以根检查只能由 ``app/alibot/workspace.py`` 在调用前完成。
测试用**真实临时目录**（根 / 根外 / symlink 都在磁盘上），断言五件事：

1. 根内 Read / Write / Edit / Glob / Grep 成功；
2. ``..``、根外绝对路径、指向根外的 symlink 一律 ``ToolResultState.ERROR``，且**没有根外写入**；
3. 默认工具集不含 Bash（D15 / ADR-0021 §二）；``allow_local_shell`` 只作为标记暴露，不在本层绕过审批；
4. ``.alibot/agents/<member_id>`` 与 ``.alibot/sessions/<session_id>`` 分区存在，项目根不被
   AgentScope 的 ``data/`` / ``skills/`` / ``sessions/`` / ``.mcp`` 污染；
5. 真 seam 的轮次工具集真的接上了这些工具（provider 请求里能看到五个文件工具、看不到 Bash）。

最后一条需要模型客户端真的发一次请求，因此用进程内 stub provider（不联网、零成本）；根限定的
判定仍全部落在工具返回的 ``ToolResultState`` 与磁盘事实，不依赖模型行为。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agentscope.message import ToolResultState
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.workspace import (
    WorkspaceConfigError,
    WorkspaceSettings,
    assemble_local_workspace,
    session_partition_id,
)


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


async def _latest_chunk(tool: Any, **kwargs: Any) -> Any:
    """调用一个带 middleware 的工具，返回最后一个 chunk。"""
    result = await tool(**kwargs)
    chunks = [chunk async for chunk in result] if hasattr(result, "__aiter__") else [result]
    assert chunks, f"{tool.name} 没有返回任何 chunk"
    return chunks[-1]


def _tool(mount: Any, name: str) -> Any:
    return next(tool for tool in mount.tools if tool.name == name)


@pytest.fixture
def layout(tmp_path: Path) -> Iterator[dict[str, Path]]:
    """根、根外目录、symlink 与一个进程外 cwd 的真实布局。"""
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    cwd = tmp_path / "process-cwd"
    for directory in (root, outside, cwd):
        directory.mkdir()

    (root / "inside.txt").write_text("inside-marker\n", encoding="utf-8")
    (outside / "secret.txt").write_text("outside-marker\n", encoding="utf-8")
    (outside / "edit-target.txt").write_text("alpha\n", encoding="utf-8")
    (cwd / "cwd-marker.txt").write_text("cwd-marker\n", encoding="utf-8")

    os.symlink(outside / "secret.txt", root / "read_link.txt")
    os.symlink(outside / "edit-target.txt", root / "edit_link.txt")
    os.symlink(outside / "write-victim.txt", root / "write_link.txt")
    os.symlink(outside, root / "link_dir")

    previous = os.getcwd()
    os.chdir(cwd)
    try:
        yield {"root": root, "outside": outside, "cwd": cwd}
    finally:
        os.chdir(previous)


def _mount(root: Path, *, member: str = "mem_workspace", target: str = "ch_workspace") -> Any:
    settings = WorkspaceSettings(root=str(root))
    return _run(
        assemble_local_workspace(
            settings,
            member_id=member,
            session_id=session_partition_id(member, target),
        )
    )


# ─── 1. 根内：五个工具都成功 ──────────────────────────────────────────────
def test_inside_paths_work_for_all_five_file_tools(layout: dict[str, Path]) -> None:
    mount = _mount(layout["root"])
    root = layout["root"]

    written = _run(
        _latest_chunk(
            _tool(mount, "Write"),
            file_path=str(root / "notes.txt"),
            content="hello workspace\n",
        )
    )
    assert written.state is not ToolResultState.ERROR
    assert (root / "notes.txt").read_text(encoding="utf-8") == "hello workspace\n"

    read = _run(_latest_chunk(_tool(mount, "Read"), file_path=str(root / "inside.txt")))
    assert read.state is not ToolResultState.ERROR
    assert "inside-marker" in read.content[0].text

    edited = _run(
        _latest_chunk(
            _tool(mount, "Edit"),
            file_path=str(root / "notes.txt"),
            old_string="hello",
            new_string="goodbye",
        )
    )
    assert edited.state is not ToolResultState.ERROR
    assert (root / "notes.txt").read_text(encoding="utf-8") == "goodbye workspace\n"

    globbed = _run(_latest_chunk(_tool(mount, "Glob"), pattern="*.txt", path=str(root)))
    assert globbed.state is not ToolResultState.ERROR
    assert "notes.txt" in globbed.content[0].text

    grepped = _run(_latest_chunk(_tool(mount, "Grep"), pattern="goodbye", path=str(root)))
    assert grepped.state is not ToolResultState.ERROR
    assert "notes.txt" in grepped.content[0].text

    # 相对路径锚到根，而不是进程 cwd（#135 的装配建议）。
    relative = _run(_latest_chunk(_tool(mount, "Read"), file_path="inside.txt"))
    assert relative.state is not ToolResultState.ERROR
    assert "inside-marker" in relative.content[0].text


# ─── 2. 三类逃逸：全部 ERROR，且没有根外写入 ─────────────────────────────
@pytest.mark.parametrize(
    ("tool_name", "make_kwargs"),
    [
        ("Read", lambda root, outside: {"file_path": str(outside / "secret.txt")}),
        ("Read", lambda root, outside: {"file_path": str(root / ".." / "outside" / "secret.txt")}),
        ("Read", lambda root, outside: {"file_path": str(root / "read_link.txt")}),
        ("Write", lambda root, outside: {"file_path": str(outside / "pwned.txt"), "content": "x"}),
        (
            "Write",
            lambda root, outside: {
                "file_path": str(root / ".." / "outside" / "pwned-dotdot.txt"),
                "content": "x",
            },
        ),
        (
            "Write",
            lambda root, outside: {"file_path": str(root / "write_link.txt"), "content": "x"},
        ),
        (
            "Edit",
            lambda root, outside: {
                "file_path": str(outside / "edit-target.txt"),
                "old_string": "alpha",
                "new_string": "beta",
            },
        ),
        (
            "Edit",
            lambda root, outside: {
                "file_path": str(root / ".." / "outside" / "edit-target.txt"),
                "old_string": "alpha",
                "new_string": "beta",
            },
        ),
        (
            "Edit",
            lambda root, outside: {
                "file_path": str(root / "edit_link.txt"),
                "old_string": "alpha",
                "new_string": "beta",
            },
        ),
        ("Glob", lambda root, outside: {"pattern": "*.txt", "path": str(outside)}),
        (
            "Glob",
            lambda root, outside: {"pattern": "*.txt", "path": str(root / ".." / "outside")},
        ),
        ("Glob", lambda root, outside: {"pattern": "*.txt", "path": str(root / "link_dir")}),
        ("Grep", lambda root, outside: {"pattern": "outside-marker", "path": str(outside)}),
        (
            "Grep",
            lambda root, outside: {
                "pattern": "outside-marker",
                "path": str(root / ".." / "outside"),
            },
        ),
        (
            "Grep",
            lambda root, outside: {"pattern": "outside-marker", "path": str(root / "read_link.txt")},
        ),
    ],
)
def test_escapes_are_errors_and_never_write_outside(
    layout: dict[str, Path],
    tool_name: str,
    make_kwargs: Any,
) -> None:
    mount = _mount(layout["root"])
    root = layout["root"]
    outside = layout["outside"]

    result = _run(_latest_chunk(_tool(mount, tool_name), **make_kwargs(root, outside)))

    assert result.state is ToolResultState.ERROR, f"{tool_name} 必须拒绝根外输入"
    assert "workspace root" in result.content[0].text
    assert not (outside / "pwned.txt").exists()
    assert not (outside / "pwned-dotdot.txt").exists()
    assert (outside / "edit-target.txt").read_text(encoding="utf-8") == "alpha\n"
    assert not (outside / "write-victim.txt").exists()


def test_relative_dotdot_escape_is_rejected_without_touching_outside(
    layout: dict[str, Path],
) -> None:
    mount = _mount(layout["root"])

    result = _run(
        _latest_chunk(_tool(mount, "Write"), file_path="../outside/pwned-relative.txt", content="x")
    )

    assert result.state is ToolResultState.ERROR
    assert not (layout["outside"] / "pwned-relative.txt").exists()


def test_glob_without_path_is_anchored_to_the_root_not_the_process_cwd(
    layout: dict[str, Path],
) -> None:
    mount = _mount(layout["root"])

    result = _run(_latest_chunk(_tool(mount, "Glob"), pattern="*.txt"))

    assert result.state is not ToolResultState.ERROR
    assert "inside.txt" in result.content[0].text
    assert "cwd-marker.txt" not in result.content[0].text

    grep = _run(_latest_chunk(_tool(mount, "Grep"), pattern="cwd-marker"))

    assert grep.state is not ToolResultState.ERROR
    assert "cwd-marker.txt" not in grep.content[0].text


# ─── 3. 默认无 Bash；allow_local_shell 只是暴露给审批层 ───────────────────
def test_default_tool_set_has_no_bash_and_exposes_the_shell_flag(layout: dict[str, Path]) -> None:
    mount = _mount(layout["root"])

    assert {tool.name for tool in mount.tools} == {"Read", "Write", "Edit", "Glob", "Grep"}
    assert mount.allow_local_shell is False

    opened = _run(
        assemble_local_workspace(
            WorkspaceSettings(root=str(layout["root"]), allow_local_shell=True),
            member_id="mem_workspace",
            session_id=session_partition_id("mem_workspace", "ch_workspace"),
        )
    )
    assert opened.allow_local_shell is True
    assert "Bash" not in {tool.name for tool in opened.tools}, "本层不在审批之外挂 Bash"


# ─── 4. .alibot 分区，不污染项目根 ────────────────────────────────────────
def test_alibot_partitions_exist_and_state_does_not_pollute_the_project_root(
    layout: dict[str, Path],
) -> None:
    root = layout["root"]
    member_id = "mem_workspace"
    session_id = session_partition_id(member_id, "ch_workspace")

    _mount(root, member=member_id, target="ch_workspace")

    assert (root / ".alibot" / "agents" / member_id).is_dir()
    assert (root / ".alibot" / "sessions" / session_id).is_dir()
    for polluted in ("data", "skills", "sessions", ".mcp"):
        assert not (root / polluted).exists(), f"项目根不应出现 AgentScope 的 {polluted}"
    # 根内业务文件仍在原处。
    assert (root / "inside.txt").read_text(encoding="utf-8") == "inside-marker\n"


def test_channel_workspace_root_resolves_for_channel_and_task_targets(
    tmp_path: Path, layout: dict[str, Path]
) -> None:
    """直接写 ``channel.workspace_root`` 后，读路径给 channel 与 task target 同一份配置。"""
    from alibot.db import open_database

    data_dir = tmp_path / "data"
    database = open_database(data_dir)
    agent = database.create_agent("小明", "工程师", "openai:stub-model", "简短回复。")
    channel = database.create_channel("项目频道", member_ids=[agent["id"]])
    task = database.create_task(channel["id"], "实现工作区")

    database.close()
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE channel SET workspace_root = ?, workspace_backend = 'local', "
            "permission_mode = 'ask-writes' WHERE id = ?",
            (str(layout["root"]), channel["id"]),
        )
        connection.execute(
            "UPDATE agent_config SET allow_local_shell = 1, permission_mode = 'trusted-writes' "
            "WHERE member_id = ?",
            (agent["id"],),
        )
        connection.commit()

    database = open_database(data_dir)
    try:
        for target_id in (channel["id"], task["target_id"]):
            settings = database.workspace_settings_for_target(target_id, agent["id"])
            assert settings is not None
            assert settings["root"] == str(layout["root"])
            assert settings["backend"] == "local"
            assert settings["permission_mode"] == "trusted-writes"
            assert settings["allow_local_shell"] is True

        # 未配置根的频道：读路径返回 None，行为保持首版形态（ADR-0021 §六）。
        other = database.create_channel("无工作区")
        assert database.workspace_settings_for_target(other["id"], agent["id"]) is None

        # DM 没有项目根。
        dm, _created = database.create_dm(agent["id"])
        assert database.workspace_settings_for_target(dm["id"], agent["id"]) is None
    finally:
        database.close()


def test_seam_uses_the_channel_workspace_root_from_the_database(
    tmp_path: Path, layout: dict[str, Path], stub_provider: str
) -> None:
    """频道写 ``workspace_root`` 后，真 seam 的下一轮就带上受限文件工具并建立分区。"""
    from alibot.db import open_database

    data_dir = tmp_path / "data"
    database = open_database(data_dir)
    agent = database.create_agent("小明", "工程师", "openai:stub-model", "简短回复。")
    channel = database.create_channel("项目频道", member_ids=[agent["id"]])
    database.close()
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE channel SET workspace_root = ? WHERE id = ?",
            (str(layout["root"]), channel["id"]),
        )
        connection.commit()

    database = open_database(data_dir)
    try:
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
        )
        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [{"seq": 1, "kind": "human", "body": "你好", "author": {"id": "owner"}}],
            mentioned=False,
            target_id=channel["id"],
        )
    finally:
        database.close()

    assert outcome.status == "replied", outcome.reason
    names = {tool["function"]["name"] for tool in _PlainReplyHandler.request_bodies[0].get("tools") or []}
    assert {"Read", "Write", "Edit", "Glob", "Grep"} <= names
    assert "Bash" not in names
    assert (layout["root"] / ".alibot" / "agents" / agent["id"]).is_dir()
    assert (
        layout["root"] / ".alibot" / "sessions" / session_partition_id(agent["id"], channel["id"])
    ).is_dir()


def test_alibot_state_symlink_cannot_redirect_writes_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside-state"
    root.mkdir()
    outside.mkdir()
    os.symlink(outside, root / ".alibot")

    with pytest.raises(WorkspaceConfigError):
        _mount(root)

    assert not (outside / "agents").exists()
    assert not (outside / "sessions").exists()


# ─── 5. 真 seam：轮次工具集接上五个文件工具、没有 Bash ────────────────────
class _PlainReplyHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：只回一条固定正文（不需要工具调用循环）。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-issue-148",
                "object": "chat.completion",
                "created": 0,
                "model": "stub-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "已处理。"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def stub_provider() -> Iterator[str]:
    _PlainReplyHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PlainReplyHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "sk-issue-148-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_seam_round_mounts_the_guarded_tools_and_creates_partitions(
    layout: dict[str, Path], stub_provider: str
) -> None:
    root = layout["root"]
    member_id = "mem_workspace"
    settings = WorkspaceSettings(root=str(root))

    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=lambda target_id, actor_id: settings,
    )
    agent = {
        "id": member_id,
        "display_name": "小明",
        "role": "工程师",
        "model_id": "openai:stub-model",
        "system_prompt": "简短回复。",
        "enabled": True,
    }

    outcome = seam.run_round(
        agent,
        [{"seq": 1, "kind": "human", "body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="ch_workspace",
    )

    assert outcome.status == "replied", outcome.reason
    first = _PlainReplyHandler.request_bodies[0]
    names = {tool["function"]["name"] for tool in first.get("tools") or []}
    assert {"Read", "Write", "Edit", "Glob", "Grep"} <= names
    assert "Bash" not in names
    assert (root / ".alibot" / "agents" / member_id).is_dir()
    assert (root / ".alibot" / "sessions" / session_partition_id(member_id, "ch_workspace")).is_dir()


# ─── 6. 成员技能目录：本地只读第二根（#272） ──────────────────────────────
_MEMBER = "mem_workspace"


def _point_home_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 HOME 指向临时目录，返回全局层根 ``<home>/.alibot``（ADR-0031：只看 HOME）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home / ".alibot"


def _member_skills_root(global_root: Path, member: str = _MEMBER) -> Path:
    return global_root / "agents" / member / "skills"


def _write_member_reference(global_root: Path, member: str = _MEMBER) -> Path:
    """铺出 ``<家目录>/agents/<member>/skills/<name>/references/x.md`` 并返回该文件。"""
    reference = _member_skills_root(global_root, member) / "code-review" / "references"
    reference.mkdir(parents=True)
    target = reference / "x.md"
    target.write_text("reference-marker\n", encoding="utf-8")
    return target


def test_read_glob_and_grep_reach_the_member_skills_read_only_root(
    tmp_path: Path, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _point_home_at(tmp_path, monkeypatch)
    target = _write_member_reference(global_root)
    mount = _mount(layout["root"])

    read = _run(_latest_chunk(_tool(mount, "Read"), file_path=str(target)))
    assert read.state is not ToolResultState.ERROR, read.content[0].text
    assert "reference-marker" in read.content[0].text

    globbed = _run(_latest_chunk(_tool(mount, "Glob"), pattern="*.md", path=str(target.parent)))
    assert globbed.state is not ToolResultState.ERROR, globbed.content[0].text
    assert "x.md" in globbed.content[0].text

    grepped = _run(_latest_chunk(_tool(mount, "Grep"), pattern="reference-marker", path=str(target.parent)))
    assert grepped.state is not ToolResultState.ERROR, grepped.content[0].text
    assert "x.md" in grepped.content[0].text


def test_write_and_edit_in_the_member_skills_root_are_rejected_like_an_escape(
    tmp_path: Path, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _point_home_at(tmp_path, monkeypatch)
    target = _write_member_reference(global_root)
    mount = _mount(layout["root"])
    new_file = target.parent / "new.md"

    cases = (
        ("Write", {"file_path": str(target), "content": "overwritten\n"}),
        ("Write", {"file_path": str(new_file), "content": "x\n"}),
        (
            "Edit",
            {
                "file_path": str(target),
                "old_string": "reference-marker",
                "new_string": "changed",
            },
        ),
    )
    for tool_name, kwargs in cases:
        result = _run(_latest_chunk(_tool(mount, tool_name), **kwargs))
        assert result.state is ToolResultState.ERROR, f"{tool_name} 必须拒绝只读根内的写入"
        assert result.content[0].text == (f"Error: path escapes workspace root: {kwargs['file_path']!r}")

    assert target.read_text(encoding="utf-8") == "reference-marker\n"
    assert not new_file.exists()


def test_paths_outside_both_roots_are_still_rejected(
    tmp_path: Path, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """只读根只覆盖技能目录：全局层的兄弟路径与项目根外都不放开。"""
    global_root = _point_home_at(tmp_path, monkeypatch)
    _write_member_reference(global_root)
    rules = global_root / "agents" / _MEMBER / "AGENTS.md"
    rules.write_text("# rules\n", encoding="utf-8")
    mcp = global_root / "mcp.json"
    mcp.write_text("{}", encoding="utf-8")
    memory = global_root / "agents" / _MEMBER / "memory" / "note.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("memory-marker\n", encoding="utf-8")
    mount = _mount(layout["root"])

    for path in (layout["outside"] / "secret.txt", rules, mcp, memory):
        result = _run(_latest_chunk(_tool(mount, "Read"), file_path=str(path)))
        assert result.state is ToolResultState.ERROR, f"{path} 必须仍在根外"
        assert result.content[0].text == f"Error: path escapes workspace root: {str(path)!r}"


def test_symlinks_inside_the_skills_root_cannot_escape_it(
    tmp_path: Path, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _point_home_at(tmp_path, monkeypatch)
    skills = _member_skills_root(global_root)
    skills.mkdir(parents=True)
    os.symlink(layout["outside"] / "secret.txt", skills / "secret_link.md")
    os.symlink(layout["outside"], skills / "linked_dir")
    mount = _mount(layout["root"])

    for path in (skills / "secret_link.md", skills / "linked_dir" / "secret.txt"):
        result = _run(_latest_chunk(_tool(mount, "Read"), file_path=str(path)))
        assert result.state is ToolResultState.ERROR, f"{path} 必须仍被 realpath 拒绝"


def test_absent_member_skills_dir_is_neither_created_nor_readable(
    tmp_path: Path, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = _point_home_at(tmp_path, monkeypatch)
    mount = _mount(layout["root"])
    absent = _member_skills_root(global_root) / "ghost" / "references" / "x.md"

    result = _run(_latest_chunk(_tool(mount, "Read"), file_path=str(absent)))

    assert result.state is ToolResultState.ERROR
    assert not _member_skills_root(global_root).exists(), "只读根不存在时不创建"
