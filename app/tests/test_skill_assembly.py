"""#152 / #271 Skill 装配层：启用态进入 AgentScope toolkit（Skill 查看器 + 系统提示）。

装配不在 HTTP 面上，测试落在公开的安装函数与真实 seam 上：

1. 启用技能进入下一轮的 ``Toolkit``：provider 的工具清单里出现 ``Skill`` 查看器，系统提示里
   出现技能名与描述；
2. 停用后同一轮不再出现（列表与启用态是 DB 事实，装配只消费它）；
3. 被篡改 / 缺失的技能字节不装配（hash 校验在装配层再核一次），也不拖垮工作区工具；
4. Docker 后端把 ``dir`` 翻译成容器内路径，字节仍来自宿主成员家目录（容器只挂载）；
5. 字节正本是成员家目录 ``<全局根>/agents/<member>/skills/``：频道级旧行不进入读路径。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.db import open_database
from alibot.skills import install_directory
from alibot.workspace import WorkspaceSettings

MANIFEST = """---
name: review
description: 代码评审清单
---

# Review

按以下步骤评审。
"""


class _PlainReplyHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：只回一条固定正文。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-issue-152",
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
    os.environ["OPENAI_API_KEY"] = "sk-issue-152-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _skill_source(tmp_path: Path) -> Path:
    source = tmp_path / "source-review"
    source.mkdir()
    (source / "SKILL.md").write_text(MANIFEST, encoding="utf-8")
    (source / "checklist.txt").write_text("a\nb\n", encoding="utf-8")
    return source


def _round(seam: AgentScopeAgentSeam, agent: dict[str, Any], target_id: str) -> Any:
    return seam.run_round(
        agent,
        [{"seq": 1, "kind": "human", "body": "评审一下", "author": {"id": "owner"}}],
        mentioned=False,
        target_id=target_id,
    )


def test_enabled_skill_reaches_the_toolkit_and_the_system_prompt(tmp_path: Path, stub_provider: str) -> None:
    from alibot.i18n import SOURCE_LOCALE

    root = tmp_path / "project"
    root.mkdir()
    global_root = tmp_path / "home" / ".alibot"
    database = open_database(tmp_path / "data")
    try:
        agent = database.create_agent("小明", "工程师", "openai:stub-model", "简短回复。")
        channel = database.create_channel("项目频道", member_ids=[agent["id"]])
        database.update_channel_config(channel["id"], workspace_root=str(root))

        source = _skill_source(tmp_path)
        prepared = install_directory(
            source, global_root=str(global_root), member_id=agent["id"], locale=SOURCE_LOCALE
        )
        database.create_skill(
            scope_kind="agent",
            scope_id=agent["id"],
            **{key: prepared[key] for key in ("name", "description", "source_path", "content_hash")},
        )

        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            skills_of=database.skills_for_target,
            global_home=str(global_root),
        )
        outcome = _round(seam, database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason

        body = _PlainReplyHandler.request_bodies[0]
        names = {tool["function"]["name"] for tool in body.get("tools") or []}
        assert "Skill" in names
        system = "\n".join(
            str(message.get("content") or "")
            for message in body.get("messages") or []
            if message.get("role") == "system"
        )
        assert "review" in system
        assert "代码评审清单" in system

        # 停用后同一轮不再装配（启用态是 DB 事实，装配只消费）。
        rows = database.skills_for_target(channel["id"], agent["id"])
        assert len(rows) == 1
        database.update_skill(rows[0]["id"], {"enabled": False})
        outcome = _round(seam, database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        body = _PlainReplyHandler.request_bodies[-1]
        names = {tool["function"]["name"] for tool in body.get("tools") or []}
        assert "Skill" not in names
        system = "\n".join(
            str(message.get("content") or "")
            for message in body.get("messages") or []
            if message.get("role") == "system"
        )
        assert "代码评审清单" not in system
    finally:
        database.close()


def test_tampered_skill_bytes_are_not_assembled_and_do_not_block_other_tools(
    tmp_path: Path, stub_provider: str
) -> None:
    from alibot.i18n import SOURCE_LOCALE

    root = tmp_path / "project"
    root.mkdir()
    global_root = tmp_path / "home" / ".alibot"
    database = open_database(tmp_path / "data")
    try:
        agent = database.create_agent("小明", "工程师", "openai:stub-model", "简短回复。")
        channel = database.create_channel("项目频道", member_ids=[agent["id"]])
        database.update_channel_config(channel["id"], workspace_root=str(root))

        source = _skill_source(tmp_path)
        prepared = install_directory(
            source, global_root=str(global_root), member_id=agent["id"], locale=SOURCE_LOCALE
        )
        database.create_skill(
            scope_kind="agent",
            scope_id=agent["id"],
            **{key: prepared[key] for key in ("name", "description", "source_path", "content_hash")},
        )
        # 安装之后有人改了成员家目录副本：hash 不匹配，装配层拒绝加载。
        (global_root / "agents" / agent["id"] / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: 被篡改\n---\n\nchanged\n", encoding="utf-8"
        )

        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            skills_of=database.skills_for_target,
            global_home=str(global_root),
        )
        outcome = _round(seam, database.get_agent(agent["id"]), channel["id"])
        assert outcome.status == "replied", outcome.reason
        body = _PlainReplyHandler.request_bodies[0]
        names = {tool["function"]["name"] for tool in body.get("tools") or []}
        assert "Skill" not in names
        assert {"Read", "Write", "Edit", "Glob", "Grep"} <= names
    finally:
        database.close()


def test_docker_backend_points_the_skill_at_the_container_mount(tmp_path: Path) -> None:
    from alibot.i18n import SOURCE_LOCALE
    from alibot.skills import load_skills
    from alibot.workspace import docker_global_member_home

    global_root = tmp_path / "home" / ".alibot"
    source = _skill_source(tmp_path)
    prepared = install_directory(
        source, global_root=str(global_root), member_id="mem_docker", locale=SOURCE_LOCALE
    )
    rows = [
        {
            "id": "sk_1",
            "name": prepared["name"],
            "description": prepared["description"],
            "content_hash": prepared["content_hash"],
            "enabled": True,
            "scope_kind": "agent",
            "scope_id": "mem_docker",
        }
    ]
    member_home = global_root / "agents" / "mem_docker"

    local = load_skills(rows, member_home=str(member_home))
    assert [skill.dir for skill in local] == [str(member_home / "skills" / "review")]

    container = load_skills(
        rows,
        member_home=str(member_home),
        container_home=docker_global_member_home("mem_docker"),
    )
    assert [skill.dir for skill in container] == [f"{docker_global_member_home('mem_docker')}/skills/review"]
    assert container[0].markdown.strip().startswith("# Review")

    # 构造一个「workspace root 在 host 上，但后端是 docker」的最小 settings 形态做自证。
    project = tmp_path / "project"
    project.mkdir()
    assert WorkspaceSettings(root=str(project), backend="docker").is_docker is True


def test_channel_scope_rows_are_never_the_assembly_source(tmp_path: Path) -> None:
    from alibot.i18n import SOURCE_LOCALE

    global_root = tmp_path / "home" / ".alibot"
    database = open_database(tmp_path / "data")
    try:
        agent = database.create_agent("小明", "工程师", "ollama:qwen2.5", "简短回复。")
        channel = database.create_channel("项目频道", member_ids=[agent["id"]])
        prepared = install_directory(
            _skill_source(tmp_path),
            global_root=str(global_root),
            member_id=agent["id"],
            locale=SOURCE_LOCALE,
        )
        database.create_skill(
            scope_kind="channel",
            scope_id=channel["id"],
            **{key: prepared[key] for key in ("name", "description", "source_path", "content_hash")},
        )

        # 目标读路径只给成员行：频道级旧行仍在库里，但不参与装配。
        assert database.skills_for_target(channel["id"], agent["id"]) == []
        assert [
            row["name"] for row in database.list_skills(scope_kind="channel", scope_id=channel["id"])
        ] == ["review"]
    finally:
        database.close()
