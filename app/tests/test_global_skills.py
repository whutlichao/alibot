"""#232 / #271 技能两层合并：成员家目录 > 全局公共技能库。

判据落在**真的会被送进模型的 system prompt** 与**运行期可读提示**上：

1. 同名技能每名只出现一次，且来自胜出层（成员家目录 > 公共库）；
2. 每个落选条目产生一条含「名字 + 胜出层」的提示，只进 ``RoundOutcome.reason``，
   **不进** provider 的 system prompt；
3. 技能是纯文件：坏条目跳过 + 一条可读日志，其余照常装配；目录缺失零条且不创建；
4. Docker 后端的可见目录映射到 ``/alibot-home/...``；
5. 技能装配不依赖 workspace（私信 / 未配置项目根同样生效）；频道级旧行与项目层旧字节不参与。

所有用例的全局根与 HOME 都指向临时目录，绝不读写真实的 ``~/.alibot``。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.db import open_database
from alibot.skills import (
    SKILL_LAYER_GLOBAL_MEMBER,
    SKILL_LAYER_GLOBAL_PUBLIC,
    install_directory,
    load_global_skills,
)
from alibot.team import TeamRoundContext
from alibot.workspace import docker_global_member_home, docker_global_skills_home


class _PlainReplyHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：记录请求体并回一条固定正文。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-issue-232",
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
    os.environ["OPENAI_API_KEY"] = "sk-issue-232-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME 指向临时目录：任何默认全局根都不可能落到真实 ``~/.alibot``。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _global_root(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".alibot"


def _without_memory(entries: list[str], member_id: str) -> list[str]:
    """去掉该成员的记忆工作区：它是轮次唯一允许写全局层的地方（ADR-0035）。"""
    prefix = f"agents/{member_id}/reme"
    return [entry for entry in entries if entry != prefix and not entry.startswith(f"{prefix}/")]


def _write_global_skill(root: Path, name: str, description: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return directory


def _install_member_skill(
    tmp_path: Path, global_root: Path, member_id: str, name: str, description: str
) -> dict[str, Any]:
    from alibot.i18n import SOURCE_LOCALE

    source = tmp_path / "sources" / f"{member_id}-{name}"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return install_directory(source, global_root=str(global_root), member_id=member_id, locale=SOURCE_LOCALE)


def _seed(
    tmp_path: Path,
) -> tuple[Any, dict[str, Any], dict[str, Any], Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    database = open_database(tmp_path / "data")
    agent = database.create_agent("小明", "工程师", "openai:stub-model", "简短回复。")
    channel = database.create_channel("项目频道", member_ids=[agent["id"]])
    database.update_channel_config(channel["id"], workspace_root=str(project))
    return database, agent, channel, project, _global_root(tmp_path)


def _create_project_skill(
    database: Any,
    *,
    scope_kind: str,
    scope_id: str,
    prepared: dict[str, Any],
) -> dict[str, Any]:
    return database.create_skill(
        scope_kind=scope_kind,
        scope_id=scope_id,
        **{key: prepared[key] for key in ("name", "description", "source_path", "content_hash")},
    )


def _seam(database: Any, global_root: Path) -> AgentScopeAgentSeam:
    return AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        skills_of=database.skills_for_target,
        global_home=str(global_root),
    )


def _round(seam: AgentScopeAgentSeam, agent: dict[str, Any], target_id: str) -> Any:
    return seam.run_round(
        agent,
        [{"seq": 1, "kind": "human", "body": "开始吧", "author": {"id": "owner"}}],
        mentioned=False,
        target_id=target_id,
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "".join(parts)


def _system_text(body: dict[str, Any]) -> str:
    return "\n".join(
        _content_text(message.get("content"))
        for message in body.get("messages") or []
        if message.get("role") == "system"
    )


def _tool_names(body: dict[str, Any]) -> set[str]:
    return {str(tool["function"]["name"]) for tool in body.get("tools") or []}


def _tree(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def test_two_layer_priority_assembles_member_home_before_public_library(
    tmp_path: Path, stub_provider: str
) -> None:
    database, agent, channel, _project, global_root = _seed(tmp_path)
    try:
        # 成员家目录的库行（安装器写入的字节 + 元数据）。
        member_row = _create_project_skill(
            database,
            scope_kind="agent",
            scope_id=agent["id"],
            prepared=_install_member_skill(
                tmp_path, global_root, agent["id"], "member-db", "库行：member-db"
            ),
        )
        # 成员家目录的人手工目录（没有库行）。
        _write_global_skill(
            global_root / "agents" / agent["id"] / "skills", "manual-member", "手工：manual-member"
        )
        # 两层同名：成员家目录更具体。
        _write_global_skill(global_root / "agents" / agent["id"] / "skills", "shared", "成员版：shared")
        _write_global_skill(global_root / "skills", "shared", "公共版：shared")
        _write_global_skill(global_root / "skills", "public-only", "公共：public-only")

        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        body = _PlainReplyHandler.request_bodies[0]
        assert "Skill" in _tool_names(body)
        system = _system_text(body)

        for description in ("库行：member-db", "手工：manual-member", "成员版：shared", "公共：public-only"):
            assert description in system
        # 公共库的同名落选者不装配。
        assert "公共版：shared" not in system
        # 唯一一条库行仍是成员级；频道级不产生行。
        assert [row["id"] for row in database.skills_for_target(channel["id"], agent["id"])] == [
            member_row["id"]
        ]
    finally:
        database.close()


def test_shadowed_skills_get_readable_notes_outside_the_system_prompt(
    tmp_path: Path, stub_provider: str
) -> None:
    database, agent, channel, _project, global_root = _seed(tmp_path)
    try:
        _write_global_skill(global_root / "agents" / agent["id"] / "skills", "shared", "成员版：shadowed")
        _write_global_skill(global_root / "skills", "shared", "公共版：shadowed")

        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason

        reasons = [line for line in outcome.reason.splitlines() if line.strip()]
        shadow_notes = [line for line in reasons if "忽略" in line]
        assert len(shadow_notes) == 1
        assert "shared" in shadow_notes[0]
        assert "全局成员家目录" in shadow_notes[0]
        assert "全局公共技能库" in shadow_notes[0]

        # 提示不进入 provider 的 system prompt（也不得复用 workspace_warnings）。
        system = _system_text(_PlainReplyHandler.request_bodies[0])
        assert "忽略" not in system
        assert "全局公共技能库" not in system
        # 胜出者仍然装配，落选者的描述不在系统提示里。
        assert "成员版：shadowed" in system
        assert "公共版：shadowed" not in system
    finally:
        database.close()


def test_a_disabled_member_row_is_not_revived_by_its_manual_directory(
    tmp_path: Path, stub_provider: str
) -> None:
    """停用行仍占有名字：它的字节不能退化成「人手工放置」的同名技能复活。"""
    database, agent, channel, _project, global_root = _seed(tmp_path)
    try:
        row = _create_project_skill(
            database,
            scope_kind="agent",
            scope_id=agent["id"],
            prepared=_install_member_skill(tmp_path, global_root, agent["id"], "retired", "停用：retired"),
        )
        database.update_skill(row["id"], {"enabled": False})

        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        body = _PlainReplyHandler.request_bodies[0]
        assert "Skill" not in _tool_names(body)
        assert "停用：retired" not in _system_text(body)
    finally:
        database.close()


def test_channel_scope_rows_and_project_layer_bytes_are_never_assembled(
    tmp_path: Path, stub_provider: str
) -> None:
    """① 频道级旧行 + 项目层旧字节都不进入装配（行与字节都保留）。"""
    import shutil

    database, agent, channel, project, global_root = _seed(tmp_path)
    try:
        prepared = _install_member_skill(
            tmp_path, global_root, agent["id"], "channel-only", "频道级：channel-only"
        )
        # 把字节挪到项目层旧路径（ADR-0031 说旧字节保留、不装配、不自动迁移）。
        member_dir = global_root / "agents" / agent["id"] / "skills" / "channel-only"
        legacy = project / ".alibot" / "skills" / channel["id"] / "channel-only"
        legacy.parent.mkdir(parents=True)
        shutil.move(str(member_dir), str(legacy))
        _create_project_skill(
            database,
            scope_kind="channel",
            scope_id=channel["id"],
            prepared=prepared,
        )

        # 库读路径只给成员行。
        assert database.skills_for_target(channel["id"], agent["id"]) == []

        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        body = _PlainReplyHandler.request_bodies[0]
        assert "Skill" not in _tool_names(body)
        assert "频道级：channel-only" not in _system_text(body)
        # 行与字节都还在（本票不迁移、不删除）。
        assert (legacy / "SKILL.md").is_file()
        assert len(database.list_skills(scope_kind="channel", scope_id=channel["id"])) == 1
    finally:
        database.close()


def test_a_manual_member_directory_without_a_database_row_is_assembled(
    tmp_path: Path, stub_provider: str
) -> None:
    """③ 家目录里有人手工放置、且没有库行的技能目录同样装配。"""
    database, agent, channel, _project, global_root = _seed(tmp_path)
    try:
        manual = _write_global_skill(
            global_root / "agents" / agent["id"] / "skills", "hand-written", "手工放置的技能"
        )
        assert database.skills_for_target(channel["id"], agent["id"]) == []

        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        body = _PlainReplyHandler.request_bodies[0]
        assert "Skill" in _tool_names(body)
        assert "手工放置的技能" in _system_text(body)
        assert (manual / "SKILL.md").is_file()
    finally:
        database.close()


def test_bad_global_entries_are_skipped_and_logged(
    tmp_path: Path, stub_provider: str, caplog: pytest.LogCaptureFixture
) -> None:
    database, agent, channel, _project, global_root = _seed(tmp_path)
    member_skills = global_root / "agents" / agent["id"] / "skills"
    try:
        _write_global_skill(member_skills, "good", "可用技能")
        # SKILL.md 缺失。
        (member_skills / "missing-manifest").mkdir(parents=True)
        # front matter 的 name 与目录名不一致。
        _write_global_skill(member_skills, "mismatch-dir", "名字不一致").joinpath("SKILL.md").write_text(
            "---\nname: other-name\ndescription: 名字不一致\n---\n\n# x\n",
            encoding="utf-8",
        )
        # 不可读：SKILL.md 是目录。
        (member_skills / "unreadable").mkdir(parents=True)
        (member_skills / "unreadable" / "SKILL.md").mkdir()
        # 根外 symlink（祖先越界）。
        outside = tmp_path / "outside"
        _write_global_skill(outside, "escaped", "根外技能")
        (member_skills / "escaped").symlink_to(outside / "escaped", target_is_directory=True)

        with caplog.at_level(logging.WARNING, logger="alibot.skills"):
            outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason

        system = _system_text(_PlainReplyHandler.request_bodies[0])
        assert "可用技能" in system
        for bad in ("missing-manifest", "mismatch-dir", "unreadable", "escaped"):
            assert bad not in system
        logged = "\n".join(record.getMessage() for record in caplog.records)
        for bad in ("missing-manifest", "mismatch-dir", "unreadable", "escaped"):
            assert bad in logged
    finally:
        database.close()


def test_missing_skill_layers_yield_zero_and_are_never_created(
    tmp_path: Path, stub_provider: str, caplog: pytest.LogCaptureFixture
) -> None:
    database, agent, channel, _project, global_root = _seed(tmp_path)
    try:
        # 全局根根本不存在：技能零条、不报错、不创建技能目录，但每层留一条可读记录（D9）。
        assert not global_root.exists()
        with caplog.at_level(logging.INFO, logger="alibot.skills"):
            outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        # 唯一允许落地的全局写入是记忆工作区（ADR-0035）；技能两层仍不创建。
        assert (global_root / "agents" / agent["id"] / "reme").is_dir()
        assert not (global_root / "skills").exists()
        assert not (global_root / "agents" / agent["id"] / "skills").exists()
        assert "Skill" not in _tool_names(_PlainReplyHandler.request_bodies[0])
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "global skill layer is absent" in logged
        assert str(global_root / "agents" / agent["id"] / "skills") in logged

        # 只有公共库、没有成员家目录：成员层零条，公共库照常。
        _write_global_skill(global_root / "skills", "public-only", "公共技能")
        layers = load_global_skills(str(global_root), agent["id"])
        assert layers[SKILL_LAYER_GLOBAL_MEMBER] == []
        assert [skill.name for skill in layers[SKILL_LAYER_GLOBAL_PUBLIC]] == ["public-only"]

        # 运行一轮不新建 / 不删除技能层的任何条目（记忆工作区除外）。
        before = _tree(global_root)
        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        assert _without_memory(_tree(global_root), agent["id"]) == _without_memory(before, agent["id"])
    finally:
        database.close()


def test_direct_message_assembles_member_skills_without_a_project_root(
    tmp_path: Path, stub_provider: str
) -> None:
    """#271：技能装配不再依赖 workspace——私信轮次（``settings is None``）同样装配两层。"""
    database = open_database(tmp_path / "data")
    try:
        agent = database.create_agent("小明", "工程师", "openai:stub-model", "简短回复。")
        global_root = _global_root(tmp_path)
        _write_global_skill(global_root / "agents" / agent["id"] / "skills", "dm-global", "私信成员技能")
        _write_global_skill(global_root / "skills", "dm-public", "私信公共技能")

        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), agent["id"])
        assert outcome.status == "replied", outcome.reason
        body = _PlainReplyHandler.request_bodies[0]
        assert "Skill" in _tool_names(body)
        system = _system_text(body)
        assert "私信成员技能" in system
        assert "私信公共技能" in system

        before = _tree(global_root)
        outcome = _round(_seam(database, global_root), database.get_agent(agent["id"]), agent["id"])
        assert outcome.status == "replied", outcome.reason
        assert _without_memory(_tree(global_root), agent["id"]) == _without_memory(before, agent["id"])
    finally:
        database.close()


def test_docker_backend_maps_global_skill_dirs_to_alibot_home(tmp_path: Path) -> None:
    agent_id = "mem_docker"
    global_root = _global_root(tmp_path)
    member_dir = _write_global_skill(global_root / "agents" / agent_id / "skills", "member-skill", "成员技能")
    public_dir = _write_global_skill(global_root / "skills", "public-skill", "公共技能")

    host = load_global_skills(str(global_root), agent_id)
    assert [skill.dir for skill in host[SKILL_LAYER_GLOBAL_MEMBER]] == [str(member_dir)]
    assert [skill.dir for skill in host[SKILL_LAYER_GLOBAL_PUBLIC]] == [str(public_dir)]

    container = load_global_skills(str(global_root), agent_id, docker=True)
    assert [skill.dir for skill in container[SKILL_LAYER_GLOBAL_MEMBER]] == [
        f"{docker_global_member_home(agent_id)}/skills/member-skill"
    ]
    assert [skill.dir for skill in container[SKILL_LAYER_GLOBAL_PUBLIC]] == [
        f"{docker_global_skills_home()}/public-skill"
    ]
    assert container[SKILL_LAYER_GLOBAL_MEMBER][0].dir.startswith("/alibot-home/agents/")
    assert container[SKILL_LAYER_GLOBAL_PUBLIC][0].dir == "/alibot-home/skills/public-skill"

    # 成员家目录缺失：该层缺席，公共库照常（容器内路径不存在也按层缺席降级）。
    import shutil

    shutil.rmtree(global_root / "agents" / agent_id)
    degraded = load_global_skills(str(global_root), agent_id, docker=True)
    assert degraded[SKILL_LAYER_GLOBAL_MEMBER] == []
    assert [skill.name for skill in degraded[SKILL_LAYER_GLOBAL_PUBLIC]] == ["public-skill"]


def test_team_round_collects_shadow_notes_from_every_member(tmp_path: Path, stub_provider: str) -> None:
    database, leader, _channel, _project, global_root = _seed(tmp_path)
    try:
        member = database.create_agent("成员", "执行", "openai:stub-model", "成员配置段。")
        channel = database.create_channel("团队频道", member_ids=[leader["id"], member["id"]])
        # 队长与成员各有一条「公共库被成员家目录覆盖」的技能（技能装配不需要项目根）。
        for member_row, name in ((leader, "leader-skill"), (member, "member-skill")):
            _write_global_skill(global_root / "agents" / member_row["id"] / "skills", name, f"成员版：{name}")
            _write_global_skill(global_root / "skills", name, f"公共版：{name}")

        seam = _seam(database, global_root)
        team = TeamRoundContext(
            target_id=channel["id"],
            leader_id=leader["id"],
            members=(database.get_agent(member["id"]),),
        )
        outcome = seam.run_round_stream(
            database.get_agent(leader["id"]),
            [{"seq": 1, "kind": "human", "body": "开工", "author": {"id": "owner"}}],
            mentioned=False,
            target_id=channel["id"],
            team=team,
        )
        assert outcome.status == "replied", outcome.reason
        assert "leader-skill" in outcome.reason and "全局成员家目录" in outcome.reason
        assert "member-skill" in outcome.reason and "全局成员家目录" in outcome.reason
        # 每条落选恰好一条提示（不因 team 汇总而重复）。
        notes = [line for line in outcome.reason.splitlines() if "忽略" in line]
        assert len(notes) == 2
        # 提示不进队长的 system prompt。
        leader_system = _system_text(_PlainReplyHandler.request_bodies[0])
        assert "忽略" not in leader_system
    finally:
        database.close()
