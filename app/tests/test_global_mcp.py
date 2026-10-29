"""成员家目录 ``mcp.json`` 是 MCP 声明的唯一正本（ADR-0021 §三 / ADR-0031 修订）。

判据落在**这一轮真的发生了什么**上：真 seam + 进程内 stub provider 读本轮工具清单与
``RoundOutcome.reason``，真实最小 stdio 子进程取证「文件条目真的连上了」，远端用传输替身计数。
全局根与 HOME 一律指向临时目录，绝不读写真实的 ``~/.alibot``；不联网。

覆盖三块：

1. 读路径（``load_member_mcp_declarations``）：缺失 / 坏 JSON / 顶层形状 / 坏条目 / 路径越界
   各自的读数与**可读问题**（运行期与管理面共用同一份）；
2. 装配（真 seam）：文件条目物化为工具、cwd 是成员目录、``env_names`` 的值只从进程环境来；
3. 降级与安全：坏文件不改变轮次结论、未确认远端不建连、一轮不写库也不新建全局层。
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
from alibot.mcp import load_member_mcp_declarations, runtime_status

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
        "description": (
            "label=" + label
            + " cwd=" + cwd
            + " env=" + os.environ.get("ALIBOT_GLOBAL_MCP_TOKEN", "(unset)")
        ),
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

SECRET_NAME = "ALIBOT_GLOBAL_MCP_TOKEN"
SECRET_VALUE = "global-secret-value-233"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # 进程环境是 env_names 的唯一来源：测试值只出现在这里，不落文件、不落库。
    monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)


@pytest.fixture
def stub_script(tmp_path: Path) -> Path:
    script = tmp_path / "stub_mcp.py"
    script.write_text(STUB_SERVER, encoding="utf-8")
    return script


def _global_root(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".alibot"


def _without_memory(entries: list[str], member_id: str) -> list[str]:
    """去掉该成员的记忆工作区：它是轮次唯一允许写全局层的地方（ADR-0035）。"""
    prefix = f"agents/{member_id}/reme"
    return [entry for entry in entries if entry != prefix and not entry.startswith(f"{prefix}/")]


def _write_global_mcp(tmp_path: Path, member_id: str, servers: Any, *, raw: str | None = None) -> Path:
    path = _global_root(tmp_path) / "agents" / member_id / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps({"servers": servers}), encoding="utf-8")
    return path


def _global_stdio(name: str, script: Path, *, label: str) -> dict[str, Any]:
    return {
        "name": name,
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script), label],
        "env_names": [SECRET_NAME],
    }


def _load(tmp_path: Path, member_id: str, *, consent_of: Any | None = None):
    return load_member_mcp_declarations(str(_global_root(tmp_path)), member_id, consent_of=consent_of)


# ─── 1. 读路径：缺失零条、不创建；条目补齐合成标识 ──────────────────────────
def test_a_missing_member_file_means_zero_declarations_without_creating_anything(
    tmp_path: Path,
) -> None:
    global_root = _global_root(tmp_path)
    document = _load(tmp_path, "agent-1")
    assert document.declarations == ()
    assert document.exists is False
    assert document.path is None
    assert document.issues == ()
    assert not global_root.exists()


def test_entries_get_synthetic_identity_and_derive_remote_consent(tmp_path: Path) -> None:
    _write_global_mcp(
        tmp_path,
        "agent-1",
        [
            _global_stdio("notes", Path("/tmp/stub.py"), label="global"),
            {
                "name": "remote",
                "transport": "http",
                "url": "https://example.test/mcp",
                "env_names": [SECRET_NAME],
            },
        ],
    )
    queried: list[str] = []

    def consent(scope_id: str) -> bool:
        queried.append(scope_id)
        return scope_id == "global:agent-1:remote"

    document = _load(tmp_path, "agent-1", consent_of=consent)
    rows = list(document.declarations)

    assert [row["name"] for row in rows] == ["notes", "remote"]
    local, remote = rows
    assert local["id"] == local["scope_id"] == "global:agent-1:notes"
    assert local["enabled"] is True
    assert local["is_remote"] is False
    assert local["consent_required"] is False
    assert local["consented"] is False
    assert "scope_kind" not in local
    # 文件只存变量名；加载结果里也没有值。
    assert local["env_names"] == [SECRET_NAME]
    assert SECRET_VALUE not in json.dumps(rows)
    assert remote["consent_required"] is True
    assert remote["consented"] is True
    assert queried == ["global:agent-1:remote"]
    assert document.issues == ()


# ─── 2. 坏文件 / 坏条目：零条或更少 + 可读问题（不抛出） ─────────────────────
def test_invalid_json_yields_zero_declarations_and_a_readable_issue(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_global_mcp(tmp_path, "agent-1", [], raw="{ not json")
    with caplog.at_level("WARNING", logger="alibot.mcp"):
        document = _load(tmp_path, "agent-1")
    assert document.declarations == ()
    assert document.exists is True
    assert document.issues and "JSON" in document.issues[0]
    assert any("JSON" in record.getMessage() for record in caplog.records)


def test_a_bad_entry_is_skipped_with_a_readable_issue(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_global_mcp(
        tmp_path,
        "agent-1",
        [
            {"name": "bad transport", "transport": "carrier-pigeon"},
            {"name": "no command", "transport": "stdio"},
            {"name": "bad env", "transport": "stdio", "command": "python3", "env_names": ["A=B"]},
            {"name": "good", "transport": "stdio", "command": "python3"},
        ],
    )
    with caplog.at_level("WARNING", logger="alibot.mcp"):
        document = _load(tmp_path, "agent-1")
    assert [row["name"] for row in document.declarations] == ["good"]
    assert document.issues and "3" in document.issues[0]
    assert len([record for record in caplog.records if record.levelname == "WARNING"]) >= 3


def test_wrong_top_level_shape_is_a_readable_issue(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write_global_mcp(tmp_path, "agent-1", [], raw='[{"name": "x", "transport": "stdio"}]')
    with caplog.at_level("WARNING", logger="alibot.mcp"):
        document = _load(tmp_path, "agent-1")
    assert document.declarations == ()
    assert document.issues and "servers" in document.issues[0]
    assert any("servers" in record.getMessage() for record in caplog.records)


def test_a_member_file_symlinked_outside_the_global_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside-mcp.json"
    outside.write_text(
        json.dumps({"servers": [{"name": "escape", "transport": "stdio", "command": "python3"}]}),
        encoding="utf-8",
    )
    member_home = _global_root(tmp_path) / "agents" / "agent-1"
    member_home.mkdir(parents=True)
    os.symlink(outside, member_home / "mcp.json")

    document = _load(tmp_path, "agent-1")
    assert document.declarations == ()
    assert document.exists is False
    assert document.issues and "路径不安全" in document.issues[0]


# ─── 3. 装配（真 seam）：文件条目物化为工具 ────────────────────────────────
class _CaptureHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容端点：记录请求体并回一条固定正文。"""

    protocol_version = "HTTP/1.1"
    request_bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length))
        type(self).request_bodies.append(body)
        payload = json.dumps(
            {
                "id": "chatcmpl-issue-233",
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
    os.environ["OPENAI_API_KEY"] = "sk-issue-233-stub"
    os.environ["OPENAI_BASE_URL"] = base_url
    try:
        yield base_url
    finally:
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _human(body: str = "开始吧") -> dict[str, Any]:
    return {"seq": 1, "kind": "human", "body": body, "author": {"id": "owner"}}


def _tool_descriptions(body: dict[str, Any]) -> dict[str, str]:
    return {
        str(tool["function"]["name"]): str(tool["function"].get("description") or "")
        for tool in body.get("tools") or []
    }


def _system_text(body: dict[str, Any]) -> str:
    """请求体里全部 ``role=system`` 消息的正文（装配/加载提示**只进** ``RoundOutcome.reason``）。"""
    parts: list[str] = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


@pytest.fixture
def declarations(tmp_path: Path) -> Iterator[Any]:
    database = open_database(tmp_path / "data")
    agent = database.create_agent("正本文件", "工程师", "ollama:qwen2.5", "你是测试。")
    channel = database.create_channel("正本频道", member_ids=[agent["id"]])
    try:
        yield database, channel["id"], agent["id"]
    finally:
        database.close()


def _prepare_project(database: Any, channel_id: str, agent_id: str, project: Path) -> None:
    project.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(project))
    database.update_agent(agent_id, model_id="openai:stub-model")


def _seam(database: Any, tmp_path: Path) -> AgentScopeAgentSeam:
    return AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_consent_of=lambda scope_id: database.has_remote_consent_scope("mcp", scope_id),
        global_home=str(_global_root(tmp_path)),
        runtime_root=tmp_path / "data",
    )


def test_a_channel_round_assembles_the_member_file(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    _write_global_mcp(tmp_path, agent_id, [_global_stdio("global", stub_script, label="file")])

    outcome = _seam(database, tmp_path).run_round(
        database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id
    )

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__global__echo" in descriptions
    description = descriptions["mcp__global__echo"]
    assert "label=file" in description
    # env_names 的值来自进程环境。
    assert f"env={SECRET_VALUE}" in description
    # Local 后端的 stdio cwd 是成员项目目录（声明与状态按成员分区）。
    expected_cwd = project.resolve() / ".alibot" / "agents" / agent_id
    assert f"cwd={expected_cwd}" in description
    status = runtime_status(f"global:{agent_id}:global")
    assert status is not None and status["connected"] is True


def test_a_broken_file_still_replies_and_reports_the_issue(
    declarations: Any, stub_provider: str, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    _write_global_mcp(tmp_path, agent_id, [], raw="{ this is not json")

    outcome = _seam(database, tmp_path).run_round(
        database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id
    )

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert not [name for name in descriptions if name.startswith("mcp__")]
    # 可读提示：指出是哪个文件、为什么；不是静默跳过。
    assert "mcp.json" in outcome.reason
    assert "JSON" in outcome.reason
    # 提示**只进** reason：不污染 provider 的 system prompt（ADR-0031 §四的既有承诺）。
    system_text = _system_text(_CaptureHandler.request_bodies[0])
    assert "mcp.json" not in system_text
    assert "JSON" not in system_text


def test_a_bad_entry_does_not_block_good_entries_and_is_reported(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    _write_global_mcp(
        tmp_path,
        agent_id,
        [
            {"name": "bad", "transport": "stdio"},
            _global_stdio("good", stub_script, label="file"),
        ],
    )

    outcome = _seam(database, tmp_path).run_round(
        database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id
    )

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__good__echo" in descriptions
    assert "mcp__bad__echo" not in descriptions
    assert "1" in outcome.reason and "跳过" in outcome.reason
    assert "跳过" not in _system_text(_CaptureHandler.request_bodies[0])


def test_a_disabled_entry_is_not_assembled(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    _write_global_mcp(
        tmp_path,
        agent_id,
        [{**_global_stdio("off", stub_script, label="file"), "enabled": False}],
    )

    outcome = _seam(database, tmp_path).run_round(
        database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id
    )

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__off__echo" not in descriptions


def test_an_unconfirmed_remote_is_not_connected_until_the_consent_row_exists(
    declarations: Any, stub_provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentscope.mcp import MCPClient

    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    _write_global_mcp(
        tmp_path,
        agent_id,
        [{"name": "remote", "transport": "http", "url": "http://127.0.0.1:9/mcp"}],
    )
    synthetic_id = f"global:{agent_id}:remote"
    attempts: list[str] = []

    async def fake_connect(self: Any) -> None:
        attempts.append(self.name)
        raise RuntimeError("stub remote refused")

    monkeypatch.setattr(MCPClient, "connect", fake_connect)
    seam = _seam(database, tmp_path)
    agent = database.get_agent(agent_id)

    first = seam.run_round(agent, [_human()], mentioned=False, target_id=channel_id)
    assert first.status == "replied", first.reason
    assert attempts == []
    unconfirmed = runtime_status(synthetic_id)
    assert unconfirmed is not None
    assert unconfirmed["connected"] is False
    assert "确认" in (unconfirmed["error"] or "") or "acknowledg" in (unconfirmed["error"] or "")
    first_tools = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__remote__echo" not in first_tools

    # 人类面确认入口（Studio 的结构化/原文两个面）写的就是这条确认行。
    database.record_remote_consent_scope("mcp", synthetic_id)

    second = seam.run_round(agent, [_human()], mentioned=False, target_id=channel_id)
    assert second.status == "replied", second.reason
    assert attempts == ["remote"]
    confirmed = runtime_status(synthetic_id)
    assert confirmed is not None
    assert confirmed["connected"] is False
    assert "stub remote refused" in (confirmed["error"] or "")


def test_a_round_does_not_write_global_entries_to_the_database_or_create_layers(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    _write_global_mcp(tmp_path, agent_id, [_global_stdio("global", stub_script, label="file")])
    global_root = _global_root(tmp_path)
    before = sorted(str(path.relative_to(global_root)) for path in global_root.rglob("*"))
    outcome = _seam(database, tmp_path).run_round(
        database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id
    )

    assert outcome.status == "replied", outcome.reason
    after = sorted(str(path.relative_to(global_root)) for path in global_root.rglob("*"))
    # 唯一允许的写入是这一轮的记忆工作区（ADR-0035）；MCP 声明仍只读、不新建任何层。
    assert _without_memory(after, agent_id) == _without_memory(before, agent_id)
    # 文件条目不会被回写成库行：库里的 ``mcp_server`` 表保持原样（没有公开读面时用原始计数）。
    import sqlite3

    conn = sqlite3.connect(tmp_path / "data" / "alibot.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM mcp_server").fetchone()[0]
    finally:
        conn.close()
    assert count == 0
