"""#151 MCP 装配层的行为取证：真实 stdio 子进程 + 传输替身。

这一层不在 HTTP 面上（声明经 HTTP 写入后由缝在轮次里装配），所以测试落在公开的装配函数
``assemble_mcp_tools`` 与工作区公开类上，用**最小本地 stdio MCP 服务**取证：

1. 启用的 stdio 声明真的物化为 MCP 工具，``env_names`` 的值从进程环境传给子进程；
2. 一个 server 启动失败不阻塞其他 server/工作区工具，失败原因可通过 ``runtime_status`` 读到；
3. 远端声明在确认之前**不建连**；确认后才会尝试连接（用 monkeypatch 的传输替身计数）；
4. 同一工作区的不同 ``(agent, session)`` 得到彼此独立的客户端实例。

stub 进程只实现 MCP 的最小 JSON-RPC 面（initialize / tools/list / tools/call），不联网。
"""

from __future__ import annotations

import asyncio
import json
import os
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
from alibot.mcp import assemble_mcp_tools, runtime_status
from alibot.workspace import WorkspaceSettings, assemble_local_workspace

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
    """一个真实库：频道 + Agent，测试按需往里写声明。"""
    database = open_database(data_dir)
    agent = database.create_agent("MCP 测试", "工程师", "ollama:qwen2.5", "你是测试。")
    channel = database.create_channel("MCP 频道", member_ids=[agent["id"]])
    try:
        yield database, channel["id"], agent["id"]
    finally:
        database.close()


def _stdio(
    database: Any,
    *,
    scope_kind: str,
    scope_id: str,
    name: str,
    script: Path,
    environ_name: str = SECRET_NAME,
) -> dict[str, Any]:
    return database.create_mcp_server(
        scope_kind=scope_kind,
        scope_id=scope_id,
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(script)],
        env_names=[environ_name],
    )


def test_stdio_declaration_becomes_tools_and_reads_env_from_process(
    declarations: Any, stub_script: Path, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    server = _stdio(
        database,
        scope_kind="agent",
        scope_id=agent_id,
        name="stub",
        script=stub_script,
    )
    root = tmp_path / "project"
    root.mkdir()

    async def scenario() -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(root)), member_id=agent_id, session_id=f"{channel_id}--{agent_id}"
        )
        try:
            assembly = await assemble_mcp_tools(
                mount.workspace,
                database.mcp_servers_for_target(channel_id, agent_id),
                member_id=agent_id,
                session_id=f"{channel_id}--{agent_id}",
                environ={SECRET_NAME: SECRET_VALUE},
            )
            try:
                names = [tool.name for tool in assembly.tools]
                assert names == ["mcp__stub__echo"]
                assert SECRET_VALUE in assembly.tools[0].description

                status = runtime_status(server["id"])
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
    database, channel_id, agent_id = declarations
    good = _stdio(
        database,
        scope_kind="channel",
        scope_id=channel_id,
        name="good",
        script=stub_script,
    )
    broken = database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="broken",
        transport="stdio",
        command="/nonexistent/alibot-mcp-binary",
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
                database.mcp_servers_for_target(channel_id, agent_id),
                member_id=agent_id,
                session_id=f"{channel_id}--{agent_id}",
                environ={SECRET_NAME: SECRET_VALUE},
            )
            try:
                assert [tool.name for tool in assembly.tools] == ["mcp__good__echo"]
                good_status = runtime_status(good["id"])
                broken_status = runtime_status(broken["id"])
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
    server = database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="remote",
        transport="http",
        url="http://127.0.0.1:9/mcp",
    )
    attempts: list[str] = []

    async def fake_connect(self: Any) -> None:
        attempts.append(self.name)
        raise RuntimeError("stub remote refused")

    monkeypatch.setattr(MCPClient, "connect", fake_connect)
    root = tmp_path / "project"
    root.mkdir()

    async def attempt() -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(root)), member_id=agent_id, session_id=f"{channel_id}--{agent_id}"
        )
        try:
            assembly = await assemble_mcp_tools(
                mount.workspace,
                database.mcp_servers_for_target(channel_id, agent_id),
                member_id=agent_id,
                session_id=f"{channel_id}--{agent_id}",
            )
            await assembly.close()
        finally:
            await mount.workspace.close()

    # 未确认：一次连接尝试都没有，状态给出可读的「未确认」。
    asyncio.run(attempt())
    assert attempts == []
    unconfirmed = runtime_status(server["id"])
    assert unconfirmed is not None
    assert unconfirmed["connected"] is False
    assert "acknowledg" in unconfirmed["error"] or "确认" in unconfirmed["error"]

    # 确认后：装配层才会尝试连接；替身失败同样可读，且不抛出。
    database.record_mcp_consent(server["id"])
    assert database.mcp_servers_for_target(channel_id, agent_id)[0]["consented"] is True

    asyncio.run(attempt())
    assert attempts == ["remote"]
    confirmed = runtime_status(server["id"])
    assert confirmed is not None
    assert confirmed["connected"] is False
    assert "stub remote refused" in (confirmed["error"] or "")


def test_clients_are_isolated_per_agent_and_session(
    declarations: Any, stub_script: Path, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    _stdio(
        database,
        scope_kind="channel",
        scope_id=channel_id,
        name="stub",
        script=stub_script,
    )
    root = tmp_path / "project"
    root.mkdir()

    async def scenario() -> None:
        mount = await assemble_local_workspace(
            WorkspaceSettings(root=str(root)), member_id=agent_id, session_id="session-a"
        )
        try:
            first = await assemble_mcp_tools(
                mount.workspace,
                database.mcp_servers_for_target(channel_id, agent_id),
                member_id=agent_id,
                session_id="session-a",
                environ={SECRET_NAME: SECRET_VALUE},
            )
            second = await assemble_mcp_tools(
                mount.workspace,
                database.mcp_servers_for_target(channel_id, agent_id),
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


def test_agent_scope_overrides_channel_scope_by_name(declarations: Any) -> None:
    """频道声明对成员生效；Agent 声明同名时覆盖它，且只返回启用项。"""
    database, channel_id, agent_id = declarations
    database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="shared",
        transport="stdio",
        command="python3",
    )
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="shared",
        transport="stdio",
        command="python3.11",
    )
    database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="disabled",
        transport="stdio",
        command="python3",
        enabled=False,
    )

    resolved = database.mcp_servers_for_target(channel_id, agent_id)

    assert [server["name"] for server in resolved] == ["shared"]
    assert resolved[0]["scope_kind"] == "agent"
    assert resolved[0]["command"] == "python3.11"


def test_a_real_round_exposes_the_mcp_tool_to_the_provider(
    declarations: Any, stub_script: Path, tmp_path: Path, stub_provider: str
) -> None:
    """缝的下一轮真的把声明装配进 toolkit：provider 请求里能看到 ``mcp__stub__echo``。"""
    database, channel_id, agent_id = declarations
    root = tmp_path / "project"
    root.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(root))
    database.update_agent(agent_id, model_id="openai:stub-model")
    _stdio(
        database,
        scope_kind="channel",
        scope_id=channel_id,
        name="stub",
        script=stub_script,
    )

    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_servers_for_target,
    )
    outcome = seam.run_round(
        database.get_agent(agent_id),
        [{"seq": 1, "kind": "human", "body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id=channel_id,
    )

    assert outcome.status == "replied", outcome.reason
    names = {tool["function"]["name"] for tool in _CaptureHandler.request_bodies[0].get("tools") or []}
    assert "mcp__stub__echo" in names


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
    declarations: Any, stub_script: Path, tmp_path: Path
) -> None:
    """Docker 后端：stdio MCP 由容器内网关启动，主机侧拿到同样的工具与可读状态。"""
    from alibot.workspace import (
        WorkspaceSettings,
        assemble_workspace,
        release_workspace_mount,
        session_partition_id,
        shutdown_docker_workspaces,
    )

    database, channel_id, agent_id = declarations
    root = tmp_path / "project"
    root.mkdir()
    (root / "stub_mcp.py").write_text(STUB_SERVER, encoding="utf-8")
    server = database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="dockerstub",
        transport="stdio",
        command="python3",
        args=["/workspace/stub_mcp.py"],
        env_names=[SECRET_NAME],
    )
    settings = WorkspaceSettings(root=str(root), backend="docker")
    session_id = session_partition_id(agent_id, channel_id)

    async def scenario() -> None:
        mount = await assemble_workspace(settings, member_id=agent_id, session_id=session_id)
        try:
            assembly = await assemble_mcp_tools(
                mount.workspace,
                database.mcp_servers_for_target(channel_id, agent_id),
                member_id=agent_id,
                session_id=session_id,
                environ={SECRET_NAME: SECRET_VALUE},
            )
            try:
                assert [tool.name for tool in assembly.tools] == ["mcp__dockerstub__echo"]
                status = runtime_status(server["id"])
                assert status is not None and status["connected"] is True
            finally:
                await assembly.close()
        finally:
            await release_workspace_mount(mount)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(shutdown_docker_workspaces())
