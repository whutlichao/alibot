"""MCP 装配层的行为取证：真实 stdio 子进程 + 传输替身。

正本是**每个 Agent 家目录的 ``mcp.json``**（ADR-0021 §三 / ADR-0031 修订）：装配层读文件，
频道与 DM 读同一份。这一层不在 HTTP 面上，所以测试落在公开的装配函数 ``assemble_mcp_tools``、
文件读路径 ``load_member_mcp_declarations`` 与工作区公开类上，用**最小本地 stdio MCP 服务**取证：

1. 启用的 stdio 声明真的物化为 MCP 工具，``env_names`` 的值从进程环境传给子进程；
2. 一个 server 启动失败不阻塞其他 server/工作区工具，失败原因可通过 ``runtime_status`` 读到；
3. 远端声明在确认之前**不建连**；确认后才会尝试连接（用 monkeypatch 的传输替身计数）；
4. 同一工作区的不同 ``(agent, session)`` 得到彼此独立的客户端实例；
5. 库里的旧行（Agent 级与频道级）**不参与装配**，频道级旧声明不会默默启用。

stub 进程只实现 MCP 的最小 JSON-RPC 面（initialize / tools/list / tools/call），不联网。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.db import open_database
from alibot.mcp import MCPAssembly, assemble_mcp_tools, load_member_mcp_declarations, runtime_status
from alibot.workspace import WorkspaceSettings, assemble_local_workspace, global_home_root

SECRET_NAME = "ALIBOT_MCP_ASSEMBLY_TOKEN"
SECRET_VALUE = "assembly-secret-value-151"

STUB_SERVER = textwrap.dedent(
    """
    import json
    import os
    import sys

    def send(payload):
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()

    tools = [{
        "name": "echo",
        "description": "stub env=" + os.environ.get("ALIBOT_MCP_ASSEMBLY_TOKEN", "(unset)"),
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }]

    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "stub", "version": "1.0"},
                },
            })
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}})
        elif method == "tools/call":
            arguments = message["params"].get("arguments") or {}
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(arguments.get("text", ""))}],
                    "isError": False,
                },
            })
        elif request_id is not None:
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    """
)


@pytest.fixture
def stub_script(tmp_path: Path) -> Path:
    script = tmp_path / "stub_mcp.py"
    script.write_text(STUB_SERVER, encoding="utf-8")
    return script


@pytest.fixture
def declarations(data_dir: Path) -> Iterator[Any]:
    """一个真实库：频道 + Agent，测试按需往成员家目录写 ``mcp.json``。"""
    database = open_database(data_dir)
    agent = database.create_agent("MCP 测试", "工程师", "ollama:qwen2.5", "你是测试。")
    channel = database.create_channel("MCP 频道", member_ids=[agent["id"]])
    try:
        yield database, channel["id"], agent["id"]
    finally:
        database.close()


def _member_file(agent_id: str) -> Path:
    return Path(global_home_root()) / "agents" / agent_id / "mcp.json"


def _write_member_mcp(agent_id: str, servers: list[dict[str, Any]]) -> Path:
    path = _member_file(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"servers": servers}, ensure_ascii=False), encoding="utf-8")
    return path


def _stdio_entry(name: str, script: Path, *, environ_name: str = SECRET_NAME) -> dict[str, Any]:
    return {
        "name": name,
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script)],
        "env_names": [environ_name],
    }


def _declarations(agent_id: str, *, consent_of: Any | None = None) -> list[dict[str, Any]]:
    return list(
        load_member_mcp_declarations(global_home_root(), agent_id, consent_of=consent_of).declarations
    )


def _insert_legacy_row(
    data_dir: Path,
    *,
    row_id: str,
    name: str,
    scope_kind: str,
    scope_id: str,
    command: str = "legacy",
) -> None:
    """直接写一条旧 ``mcp_server`` 行：证明它不再进入装配（读路径已不经过库）。"""
    conn = sqlite3.connect(data_dir / "alibot.db")
    try:
        conn.execute(
            "INSERT INTO mcp_server(id, name, transport, command, args, url, env_names, enabled, "
            "scope_kind, scope_id, created_at, updated_at) "
            "VALUES (?, ?, 'stdio', ?, '[]', '', '[]', 1, ?, ?, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z')",
            (row_id, name, command, scope_kind, scope_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_stdio_declaration_becomes_tools_and_reads_env_from_process(
    declarations: Any, stub_script: Path, tmp_path: Path
) -> None:
    _database, channel_id, agent_id = declarations
    _write_member_mcp(agent_id, [_stdio_entry("stub", stub_script)])
    root = tmp_path / "project"
    root.mkdir()

    async def scenario() -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(root)), member_id=agent_id, session_id=f"{channel_id}--{agent_id}"
        )
        try:
            assembly = await assemble_mcp_tools(
                mount.workspace,
                _declarations(agent_id),
                member_id=agent_id,
                session_id=f"{channel_id}--{agent_id}",
                environ={SECRET_NAME: SECRET_VALUE},
            )
            try:
                names = [tool.name for tool in assembly.tools]
                assert names == ["mcp__stub__echo"]
                assert SECRET_VALUE in assembly.tools[0].description

                status = runtime_status(f"global:{agent_id}:stub")
                assert status is not None
                assert status["connected"] is True
                assert status["tools"] == ["mcp__stub__echo"]
                assert status["error"] is None
            finally:
                await assembly.close()
        finally:
            await mount.workspace.close()

    asyncio.run(scenario())


def test_a_broken_server_is_readable_and_does_not_block_other_tools(
    declarations: Any, stub_script: Path, tmp_path: Path
) -> None:
    _database, channel_id, agent_id = declarations
    _write_member_mcp(
        agent_id,
        [
            _stdio_entry("good", stub_script),
            {
                "name": "broken",
                "transport": "stdio",
                "command": "/nonexistent/alibot-mcp-binary",
            },
        ],
    )
    root = tmp_path / "project"
    root.mkdir()

    async def scenario() -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(root)), member_id=agent_id, session_id=f"{channel_id}--{agent_id}"
        )
        try:
            # 装配本身不抛：坏 server 只影响自己。
            assembly = await assemble_mcp_tools(
                mount.workspace,
                _declarations(agent_id),
                member_id=agent_id,
                session_id=f"{channel_id}--{agent_id}",
                environ={SECRET_NAME: SECRET_VALUE},
            )
            try:
                assert [tool.name for tool in assembly.tools] == ["mcp__good__echo"]
                good_status = runtime_status(f"global:{agent_id}:good")
                broken_status = runtime_status(f"global:{agent_id}:broken")
                assert good_status is not None and good_status["connected"] is True
                assert broken_status is not None
                assert broken_status["connected"] is False
                # 可读：失败原因不是空串，也不是一个泛化的布尔。
                assert broken_status["error"]
                assert "alibot-mcp-binary" in broken_status["error"]
            finally:
                await assembly.close()
        finally:
            await mount.workspace.close()

    asyncio.run(scenario())


def test_unconfirmed_remote_is_not_connected_and_consent_unlocks_the_attempt(
    declarations: Any, tmp_path: Path, monkeypatch
) -> None:
    from agentscope.mcp import MCPClient

    database, channel_id, agent_id = declarations
    _write_member_mcp(
        agent_id,
        [{"name": "remote", "transport": "http", "url": "http://127.0.0.1:9/mcp"}],
    )
    synthetic_id = f"global:{agent_id}:remote"
    attempts: list[str] = []

    async def fake_connect(self: Any) -> None:
        attempts.append(self.name)
        raise RuntimeError("stub remote refused")

    monkeypatch.setattr(MCPClient, "connect", fake_connect)
    root = tmp_path / "project"
    root.mkdir()

    def consent(scope_id: str) -> bool:
        return database.has_remote_consent_scope("mcp", scope_id)

    async def attempt() -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(root)), member_id=agent_id, session_id=f"{channel_id}--{agent_id}"
        )
        try:
            assembly = await assemble_mcp_tools(
                mount.workspace,
                _declarations(agent_id, consent_of=consent),
                member_id=agent_id,
                session_id=f"{channel_id}--{agent_id}",
            )
            await assembly.close()
        finally:
            await mount.workspace.close()

    # 未确认：一次连接尝试都没有，状态给出可读的「未确认」。
    asyncio.run(attempt())
    assert attempts == []
    unconfirmed = runtime_status(synthetic_id)
    assert unconfirmed is not None
    assert unconfirmed["connected"] is False
    assert "acknowledg" in unconfirmed["error"] or "确认" in unconfirmed["error"]

    # 确认后：装配层才会尝试连接；替身失败同样可读，且不抛出。
    database.record_remote_consent_scope("mcp", synthetic_id)
    assert _declarations(agent_id, consent_of=consent)[0]["consented"] is True

    asyncio.run(attempt())
    assert attempts == ["remote"]
    confirmed = runtime_status(synthetic_id)
    assert confirmed is not None
    assert confirmed["connected"] is False
    assert "stub remote refused" in (confirmed["error"] or "")


def test_clients_are_isolated_per_agent_and_session(
    declarations: Any, stub_script: Path, tmp_path: Path
) -> None:
    _database, _channel_id, agent_id = declarations
    _write_member_mcp(agent_id, [_stdio_entry("stub", stub_script)])
    root = tmp_path / "project"
    root.mkdir()

    async def scenario() -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(root)), member_id=agent_id, session_id="session-a"
        )
        try:
            first = await assemble_mcp_tools(
                mount.workspace,
                _declarations(agent_id),
                member_id=agent_id,
                session_id="session-a",
                environ={SECRET_NAME: SECRET_VALUE},
            )
            second = await assemble_mcp_tools(
                mount.workspace,
                _declarations(agent_id),
                member_id=agent_id,
                session_id="session-b",
                environ={SECRET_NAME: SECRET_VALUE},
            )
            try:
                assert first.tools and second.tools
                assert first.tools[0] is not second.tools[0]
            finally:
                # anyio cancel scope 按连接顺序嵌套：后连的先关。
                await second.close()
                await first.close()
        finally:
            await mount.workspace.close()

    asyncio.run(scenario())


def test_close_drops_the_clients_before_closing_the_runtime_host() -> None:
    """DM 的运行宿主只在轮末关一次，且**后于**客户端关闭（客户端的 transport 挂在宿主 loop 上）。"""
    order: list[str] = []

    class _Client:
        is_connected = True

        async def close(self) -> None:
            order.append("client")

    class _Host:
        def __init__(self) -> None:
            self._mcp_instances: dict[tuple[str, str], dict[str, Any]] = {
                ("agent-1", "session-1"): {"stub": _Client()}
            }

        async def close(self) -> None:
            order.append("host")

    host = _Host()
    assembly = MCPAssembly(
        workspace=host,
        member_id="agent-1",
        session_id="session-1",
        host_workspace=host,
    )

    asyncio.run(assembly.close())

    assert order == ["client", "host"]
    assert host._mcp_instances == {}, "实例层要被丢弃：下一次装配在当前 loop 上重建"


def test_legacy_database_rows_never_reach_assembly(declarations: Any, data_dir: Path) -> None:
    """库里的旧行（Agent 级与频道级）不再是装配来源；只有文件条目进入工具面。"""
    database, channel_id, agent_id = declarations
    _insert_legacy_row(
        data_dir,
        row_id="mcp_legacy_agent",
        name="legacy-agent",
        scope_kind="agent",
        scope_id=agent_id,
    )
    _insert_legacy_row(
        data_dir,
        row_id="mcp_legacy_channel",
        name="legacy-channel",
        scope_kind="channel",
        scope_id=channel_id,
    )
    _write_member_mcp(
        agent_id,
        [{"name": "from-file", "transport": "stdio", "command": "python3"}],
    )

    names = [item["name"] for item in _declarations(agent_id)]
    assert names == ["from-file"]
    assert database.get_agent(agent_id)["id"] == agent_id


class _CaptureHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：只回一条固定正文；记录请求体以断言工具清单。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-issue-151",
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
    _CaptureHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "sk-issue-151-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _human(body: str = "你好") -> dict[str, Any]:
    return {"seq": 1, "kind": "human", "body": body, "author": {"id": "owner"}}


def _seam(database: Any, data_dir: Path) -> AgentScopeAgentSeam:
    return AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_consent_of=lambda scope_id: database.has_remote_consent_scope("mcp", scope_id),
        global_home=global_home_root(),
        runtime_root=data_dir,
    )


def test_a_channel_only_legacy_declaration_never_reaches_the_provider(
    declarations: Any, stub_script: Path, tmp_path: Path, data_dir: Path, stub_provider: str
) -> None:
    database, channel_id, agent_id = declarations
    root = tmp_path / "project"
    root.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(root))
    database.update_agent(agent_id, model_id="openai:stub-model")
    _insert_legacy_row(
        data_dir,
        row_id="mcp_channel_only",
        name="stub",
        scope_kind="channel",
        scope_id=channel_id,
        command=sys.executable,
    )

    outcome = _seam(database, data_dir).run_round(
        database.get_agent(agent_id),
        [_human()],
        mentioned=False,
        target_id=channel_id,
    )

    assert outcome.status == "replied", outcome.reason
    names = {tool["function"]["name"] for tool in _CaptureHandler.request_bodies[0].get("tools") or []}
    assert not [name for name in names if name.startswith("mcp__")]
    assert runtime_status(f"global:{agent_id}:stub") is None


def test_a_channel_round_and_a_dm_round_read_the_same_member_file(
    declarations: Any, stub_script: Path, tmp_path: Path, data_dir: Path, stub_provider: str
) -> None:
    """频道与 DM 读同一份 ``mcp.json``：同一 Agent 的工具面在两处都出现。"""
    database, channel_id, agent_id = declarations
    root = tmp_path / "project"
    root.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(root))
    database.update_agent(agent_id, model_id="openai:stub-model")
    _write_member_mcp(agent_id, [_stdio_entry("stub", stub_script)])
    seam = _seam(database, data_dir)

    channel_outcome = seam.run_round(
        database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id
    )
    assert channel_outcome.status == "replied", channel_outcome.reason
    channel_tools = {
        tool["function"]["name"] for tool in _CaptureHandler.request_bodies[0].get("tools") or []
    }
    assert "mcp__stub__echo" in channel_tools

    _CaptureHandler.request_bodies.clear()
    dm, _created = database.create_dm(agent_id)
    dm_outcome = seam.run_round(
        database.get_agent(agent_id),
        [_human()],
        mentioned=False,
        target_id=dm["id"],
        direct_message=True,
    )
    assert dm_outcome.status == "replied", dm_outcome.reason
    dm_tools = {tool["function"]["name"] for tool in _CaptureHandler.request_bodies[0].get("tools") or []}
    assert "mcp__stub__echo" in dm_tools
    # 频道轮拿项目文件工具；DM 轮没有（运行宿主只托管 MCP）。
    assert "Write" in channel_tools
    assert "Write" not in dm_tools


def _docker_available() -> bool:
    import subprocess

    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


requires_docker = pytest.mark.skipif(not _docker_available(), reason="Docker daemon is not available")


@requires_docker
def test_docker_backend_materializes_mcp_through_the_workspace_gateway(
    declarations: Any, tmp_path: Path
) -> None:
    """Docker 后端：stdio MCP 由容器内网关启动，主机侧拿到同样的工具与可读状态。"""
    from alibot.workspace import (
        WorkspaceSettings,
        assemble_workspace,
        release_workspace_mount,
        session_partition_id,
        shutdown_docker_workspaces,
    )

    _database, channel_id, agent_id = declarations
    root = tmp_path / "project"
    root.mkdir()
    (root / "stub_mcp.py").write_text(STUB_SERVER, encoding="utf-8")
    _write_member_mcp(
        agent_id,
        [
            {
                "name": "dockerstub",
                "transport": "stdio",
                "command": "python3",
                "args": ["/workspace/stub_mcp.py"],
                "env_names": [SECRET_NAME],
            }
        ],
    )
    settings = WorkspaceSettings(root=str(root), backend="docker")
    session_id = session_partition_id(agent_id, channel_id)

    async def scenario() -> None:
        mount = await assemble_workspace(settings, member_id=agent_id, session_id=session_id)
        try:
            assembly = await assemble_mcp_tools(
                mount.workspace,
                _declarations(agent_id),
                member_id=agent_id,
                session_id=session_id,
                environ={SECRET_NAME: SECRET_VALUE},
            )
            try:
                assert [tool.name for tool in assembly.tools] == ["mcp__dockerstub__echo"]
                status = runtime_status(f"global:{agent_id}:dockerstub")
                assert status is not None and status["connected"] is True
            finally:
                await assembly.close()
        finally:
            await release_workspace_mount(mount)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(shutdown_docker_workspaces())
