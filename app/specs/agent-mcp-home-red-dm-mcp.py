"""红证据：基线 ``b80412b`` 上 DM 轮次拿不到**库内** Agent 级 MCP 声明。

这不是本仓测试目录里的用例（``pytest`` 不收集 ``app/specs/``）：它取证的是**旧代码**的行为，
只用于复现「DM 没有 MCP」这条回归。复现步骤（不改动当前分支）：

.. code-block:: sh

    git worktree add /tmp/alibot-baseline-e96d b80412b
    cp app/specs/agent-mcp-home-red-dm-mcp.py \
        /tmp/alibot-baseline-e96d/app/tests/test_red_dm_mcp.py
    cd /tmp/alibot-baseline-e96d/app
    /Users/apple/orca/workspaces/alibot/agent-mcp-home/.venv/bin/python -m pytest \
        tests/test_red_dm_mcp.py -q

预期失败（2026-10-29 实跑）：

.. code-block:: text

    E  AssertionError: 基线 DM 工具面：['declare_silence', 'memory_search']
    FAILED tests/test_red_dm_mcp.py::test_baseline_dm_round_has_no_mcp_tools

同一场景在新代码里由 ``app/tests/test_dm_mcp.py`` 取证：文件条目照常物化，且不暴露项目文件工具。
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

STUB_SERVER = textwrap.dedent(
    """
    import json
    import sys

    def send(payload):
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()

    tools = [{
        "name": "echo",
        "description": "stub",
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
        elif request_id is not None:
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    """
)


class _CaptureHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：记录请求体以断言工具清单。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-red-dm-mcp",
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
def stub_provider() -> Iterator[str]:
    _CaptureHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "sk-red-dm-mcp"
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


def test_baseline_dm_round_has_no_mcp_tools(data_dir: Path, tmp_path: Path, stub_provider: str) -> None:
    script = tmp_path / "stub_mcp.py"
    script.write_text(STUB_SERVER, encoding="utf-8")
    database = open_database(data_dir)
    try:
        agent = database.create_agent("私信成员", "工程师", "openai:stub-model", "你是测试。")
        # 旧正本：库里 Agent 级的声明。
        database.create_mcp_server(
            scope_kind="agent",
            scope_id=agent["id"],
            name="stub",
            transport="stdio",
            command=sys.executable,
            args=[str(script)],
        )
        dm, _created = database.create_dm(agent["id"])
        seam = AgentScopeAgentSeam(
            timeout_seconds=10.0,
            workspace_of=database.workspace_settings_for_target,
            # 与基线 api.py 同一条接线：声明清单走库行；DM 没有频道上下文时它返回空。
            mcp_of=database.mcp_layers_for_target,
            global_home=str(Path(os.environ["HOME"]) / ".alibot"),
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
        assert "mcp__stub__echo" in tools, f"基线 DM 工具面：{sorted(tools)}"
    finally:
        database.close()
