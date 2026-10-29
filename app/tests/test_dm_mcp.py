"""DM 也使用该 Agent 家目录 ``mcp.json`` 的声明；DM 不获得项目文件权限。

本文件是「DM 没有 MCP」旧行为的回归钉：旧代码只给有频道上下文的轮次装配 MCP，
DM 的 ``mcp_of`` 读路径在 ``_channel_id_for_target_locked`` 处返回空，于是文件里的
声明一条也不会连接。新行为以文件为唯一正本、并给无项目根的轮次一个**只托管 MCP**
的运行宿主——文件条目照常物化成工具，但宿主不暴露任何项目文件工具。

判据落在这一轮真的发生了什么上：真 seam + 进程内 stub provider 读本轮工具清单，
真实最小 stdio 子进程取证「文件条目真的连上了」，并断言 DM 的工具面没有写 / 编辑 /
glob / grep / bash（卸载目录的只读 Read 不在项目文件面里，保留）。
"""

from __future__ import annotations

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
from alibot.mcp import runtime_status

STUB_SERVER = textwrap.dedent(
    """
    import json
    import os
    import sys

    label = sys.argv[1] if len(sys.argv) > 1 else "none"
    cwd = os.getcwd()

    def send(payload):
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()

    tools = [{
        "name": "echo",
        "description": "label=" + label + " cwd=" + cwd,
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

#: DM 的工具面里不该出现的**项目文件**工具（卸载目录的只读 Read 是数据目录内的另一回事）。
PROJECT_FILE_TOOLS = {"Write", "Edit", "Glob", "Grep", "Bash"}


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
                "id": "chatcmpl-dm-mcp",
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


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def stub_script(tmp_path: Path) -> Path:
    script = tmp_path / "stub_mcp.py"
    script.write_text(STUB_SERVER, encoding="utf-8")
    return script


@pytest.fixture
def stub_provider() -> Iterator[str]:
    _CaptureHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "sk-dm-mcp-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _global_root() -> Path:
    return Path(os.environ["HOME"]) / ".alibot"


def _write_member_mcp(member_id: str, servers: list[dict[str, Any]]) -> Path:
    path = _global_root() / "agents" / member_id / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return path


def _human(body: str = "你好") -> dict[str, Any]:
    return {"seq": 1, "kind": "human", "body": body, "author": {"id": "owner"}}


def test_a_dm_round_uses_the_agent_home_mcp_file_and_gets_no_project_file_tools(
    data_dir: Path, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    database = open_database(data_dir)
    try:
        agent = database.create_agent("私信成员", "工程师", "openai:stub-model", "你是测试。")
        _write_member_mcp(
            agent["id"],
            [
                {
                    "name": "stub",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(stub_script), "dm"],
                }
            ],
        )
        dm, _created = database.create_dm(agent["id"])
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root()),
            runtime_root=data_dir,
        )

        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=dm["id"],
            direct_message=True,
        )

        assert outcome.status == "replied", outcome.reason
        tools = {
            str(tool.get("function", {}).get("name") or ""): tool
            for tool in _CaptureHandler.request_bodies[0].get("tools") or []
        }
        assert "mcp__stub__echo" in tools, (
            "DM 也必须装配该 Agent 家目录 mcp.json 的声明；"
            f"本轮工具：{sorted(tools)}；reason：{outcome.reason}"
        )
        assert not (PROJECT_FILE_TOOLS & set(tools)), (
            f"DM 不该拿到项目文件工具：{sorted(PROJECT_FILE_TOOLS & set(tools))}"
        )
        description = str(tools["mcp__stub__echo"]["function"].get("description") or "")
        assert "label=dm" in description
        # stdio MCP 的 cwd 是运行宿主目录（数据目录内），不是任何项目根。
        cwd = description.split("cwd=", 1)[1].strip()
        assert Path(cwd).is_dir()
        assert str(Path(data_dir).resolve()) in cwd
        assert str(Path(tmp_path / "project").resolve()) not in cwd

        status = runtime_status(f"global:{agent['id']}:stub")
        assert status is not None and status["connected"] is True
        assert status["tools"] == ["mcp__stub__echo"]
    finally:
        database.close()


def test_a_dm_round_without_an_mcp_file_still_replies_and_creates_no_host(
    data_dir: Path, stub_provider: str
) -> None:
    database = open_database(data_dir)
    try:
        agent = database.create_agent("无声明成员", "工程师", "openai:stub-model", "你是测试。")
        dm, _created = database.create_dm(agent["id"])
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            global_home=str(_global_root()),
            runtime_root=data_dir,
        )

        outcome = seam.run_round(
            database.get_agent(agent["id"]),
            [_human()],
            mentioned=False,
            target_id=dm["id"],
            direct_message=True,
        )

        assert outcome.status == "replied", outcome.reason
        tools = {
            str(tool.get("function", {}).get("name") or "")
            for tool in _CaptureHandler.request_bodies[0].get("tools") or []
        }
        assert not [name for name in tools if name.startswith("mcp__")]
        # 没有声明就没有可托管的客户端：不建宿主目录（数据目录里不出现空的 mcp-hosts 分区）。
        host_root = Path(data_dir) / "mcp-hosts"
        assert not host_root.exists() or not any(host_root.iterdir())
    finally:
        database.close()
