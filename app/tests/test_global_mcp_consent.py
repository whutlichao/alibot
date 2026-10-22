"""#236 全局远端 MCP 的确认：人类面按「成员 + 名字」定位。

判据只经公开面取证：

- HTTP 人类面：只读列表 / 确认写入口 / 幂等 / 重启保持 / 三类错误不写行 / 文件删除后不报错；
- 装配层：确认写入既有 ``remote_consent_scope('mcp', 'global:<member_id>:<name>')`` 后，
  下一轮 ``load_global_mcp_declarations`` 读到 ``consented=True``，``assemble_mcp_tools``
  不再走 ``consent_required`` 分支（远端传输用进程内替身计数，不联网）。

全局根与 HOME 一律指向临时目录：绝不读写真实的 ``~/.alibot``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import backend, create_agent


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _write_global_mcp(tmp_path: Path, member_id: str, servers: list[dict[str, Any]]) -> Path:
    path = tmp_path / "home" / ".alibot" / "agents" / member_id / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return path


def _stdio(name: str = "notes") -> dict[str, Any]:
    return {"name": name, "transport": "stdio", "command": "python3"}


def _remote(name: str = "remote", transport: str = "http") -> dict[str, Any]:
    url = "http://127.0.0.1:9/sse" if transport == "sse" else "http://127.0.0.1:9/mcp"
    return {"name": name, "transport": transport, "url": url}


def test_list_lists_only_real_global_entries_with_runtime_readings(data_dir: Path, tmp_path: Path) -> None:
    with backend(data_dir) as client:
        agent = create_agent(client).json()["agent"]
        _write_global_mcp(tmp_path, agent["id"], [_stdio(), _remote(), _remote("remote-sse", "sse")])

        response = client.get(f"/api/agents/{agent['id']}/global-mcp-servers")

    assert response.status_code == 200
    rows = response.json()["global_mcp_servers"]
    assert [row["name"] for row in rows] == ["notes", "remote", "remote-sse"]
    assert all(set(row) == {"name", "transport", "is_remote", "consented", "runtime"} for row in rows)
    local, http_row, sse_row = rows
    assert local["transport"] == "stdio"
    assert local["is_remote"] is False
    assert local["consented"] is False
    assert local["runtime"] is None
    assert http_row["transport"] == "http"
    assert http_row["is_remote"] is True
    assert http_row["consented"] is False
    assert http_row["runtime"] is None
    assert sse_row["transport"] == "sse"
    assert sse_row["is_remote"] is True
    assert sse_row["consented"] is False


def test_confirm_writes_the_existing_scope_row_is_idempotent_and_survives_restart(
    data_dir: Path, tmp_path: Path
) -> None:
    with backend(data_dir) as client:
        agent = create_agent(client).json()["agent"]
        _write_global_mcp(tmp_path, agent["id"], [_remote()])

        first = client.post(f"/api/agents/{agent['id']}/global-mcp-servers/remote/consent")
        assert first.status_code == 200
        consent = first.json()["consent"]
        assert consent["scope_kind"] == "mcp"
        assert consent["scope_id"] == f"global:{agent['id']}:remote"
        assert consent["acknowledged_at"].endswith("Z")
        # 只存时间：没有凭证、没有连接参数。
        assert set(consent) == {"scope_kind", "scope_id", "acknowledged_at"}
        assert first.json()["global_mcp_server"]["consented"] is True

        again = client.post(f"/api/agents/{agent['id']}/global-mcp-servers/remote/consent")
        assert again.status_code == 200
        assert again.json()["consent"]["acknowledged_at"] == consent["acknowledged_at"]

        listed = client.get(f"/api/agents/{agent['id']}/global-mcp-servers").json()
        assert listed["global_mcp_servers"][0]["consented"] is True

    # 重启（换一个进程读同一数据目录）：确认行仍在，列表仍读得到。
    with backend(data_dir) as restarted:
        rows = restarted.get(f"/api/agents/{agent['id']}/global-mcp-servers").json()["global_mcp_servers"]
        assert rows[0]["consented"] is True


def test_write_path_refuses_missing_member_name_and_stdio_without_writing_a_row(
    data_dir: Path, tmp_path: Path
) -> None:
    from alibot.db import open_database

    with backend(data_dir) as client:
        agent = create_agent(client).json()["agent"]
        _write_global_mcp(tmp_path, agent["id"], [_stdio()])

        missing_member = client.post("/api/agents/not-a-member/global-mcp-servers/ghost/consent")
        assert missing_member.status_code == 404
        assert missing_member.json()["error"]["message"]
        assert "not-a-member" in missing_member.json()["error"]["message"]

        missing_name = client.post(f"/api/agents/{agent['id']}/global-mcp-servers/ghost/consent")
        assert missing_name.status_code == 400
        assert missing_name.json()["error"]["message"]
        assert "ghost" in missing_name.json()["error"]["message"]

        stdio = client.post(f"/api/agents/{agent['id']}/global-mcp-servers/notes/consent")
        assert stdio.status_code == 400
        assert stdio.json()["error"]["message"]
        # 读回仍是只读行：stdio 永远不在「可确认」的那一半里。
        listed = client.get(f"/api/agents/{agent['id']}/global-mcp-servers").json()["global_mcp_servers"]
        assert listed == [
            {
                "name": "notes",
                "transport": "stdio",
                "is_remote": False,
                "consented": False,
                "runtime": None,
            }
        ]

        # 黑盒反证：把同名条目改成远端后若「已确认」，说明刚才的 stdio 拒绝偷偷写了行。
        _write_global_mcp(tmp_path, agent["id"], [_remote("notes")])
        flipped = client.get(f"/api/agents/{agent['id']}/global-mcp-servers").json()["global_mcp_servers"]
        assert flipped[0]["consented"] is False

    # 三个失败入口都没有写确认行（读的是公开的持久化读路径）。
    database = open_database(data_dir)
    try:
        assert not database.has_remote_consent_scope("mcp", "global:not-a-member:ghost")
        assert not database.has_remote_consent_scope("mcp", f"global:{agent['id']}:ghost")
        assert not database.has_remote_consent_scope("mcp", f"global:{agent['id']}:notes")
    finally:
        database.close()


def test_file_renamed_or_deleted_after_consent_leaves_the_row_alone_and_never_errors(
    data_dir: Path, tmp_path: Path
) -> None:
    from alibot.db import open_database

    with backend(data_dir) as client:
        agent = create_agent(client).json()["agent"]
        path = _write_global_mcp(tmp_path, agent["id"], [_remote()])
        assert client.post(f"/api/agents/{agent['id']}/global-mcp-servers/remote/consent").status_code == 200

        # 人工改名：列表变成零条，但读取本身不报错，旧确认行仍留在库里。
        renamed = path.with_name("mcp.json.bak")
        path.rename(renamed)
        listed = client.get(f"/api/agents/{agent['id']}/global-mcp-servers")
        assert listed.status_code == 200
        assert listed.json()["global_mcp_servers"] == []

        # 人工删除：同上；写入口给出可读错误（名字已不在文件里），不写新行。
        renamed.unlink()
        deleted = client.get(f"/api/agents/{agent['id']}/global-mcp-servers")
        assert deleted.status_code == 200
        assert deleted.json()["global_mcp_servers"] == []
        refused = client.post(f"/api/agents/{agent['id']}/global-mcp-servers/remote/consent")
        assert refused.status_code == 400
        assert refused.json()["error"]["message"]
        # 应用不自动清理、不写回全局层：文件仍然不存在。
        assert not path.exists()

    database = open_database(data_dir)
    try:
        assert database.has_remote_consent_scope("mcp", f"global:{agent['id']}:remote") is True
    finally:
        database.close()


def test_http_consent_unlocks_the_next_assembly(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """确认前不建连、给 ``consent_required``；确认后下一轮装配真的尝试连接。"""
    import asyncio

    from agentscope.mcp import MCPClient
    from alibot.db import open_database
    from alibot.mcp import assemble_mcp_tools, load_global_mcp_declarations, runtime_status
    from alibot.workspace import WorkspaceSettings, assemble_local_workspace

    attempts: list[str] = []

    async def fake_connect(self: Any) -> None:
        attempts.append(self.name)
        raise RuntimeError("stub remote refused")

    # 远端传输替身：只计数不联网（与 test_mcp_assembly.py 同一先例）。
    monkeypatch.setattr(MCPClient, "connect", fake_connect)
    project = tmp_path / "project"
    project.mkdir()
    root = str(tmp_path / "home" / ".alibot")

    def load() -> list[dict[str, Any]]:
        database = open_database(data_dir)
        try:
            return load_global_mcp_declarations(
                root,
                agent["id"],
                consent_of=lambda scope_id: database.has_remote_consent_scope("mcp", scope_id),
            )
        finally:
            database.close()

    async def attempt(declarations: list[dict[str, Any]]) -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(project)), member_id=agent["id"], session_id="session-236"
        )
        try:
            assembly = await assemble_mcp_tools(
                mount.workspace,
                declarations,
                member_id=agent["id"],
                session_id="session-236",
            )
            await assembly.close()
        finally:
            await mount.workspace.close()

    with backend(data_dir) as client:
        agent = create_agent(client).json()["agent"]
        _write_global_mcp(tmp_path, agent["id"], [_remote()])

        before = load()
        assert before[0]["consent_required"] is True
        assert before[0]["consented"] is False
        asyncio.run(attempt(before))
        assert attempts == []
        blocked = runtime_status(str(before[0]["id"]))
        assert blocked is not None and blocked["connected"] is False
        assert "确认" in (blocked["error"] or "") or "acknowledg" in (blocked["error"] or "")

        assert client.post(f"/api/agents/{agent['id']}/global-mcp-servers/remote/consent").status_code == 200

    after = load()
    assert after[0]["consented"] is True
    asyncio.run(attempt(after))
    assert attempts == ["remote"]
    confirmed = runtime_status(str(after[0]["id"]))
    assert confirmed is not None and confirmed["connected"] is False
    assert "stub remote refused" in (confirmed["error"] or "")
