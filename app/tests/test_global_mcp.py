"""#233 MCP 声明三层合并：项目 Agent 级 / 项目频道级 / 全局成员家目录。

判据落在**这一轮真的发生了什么**上：真 seam + 进程内 stub provider 读本轮工具清单与
system prompt，真实最小 stdio 子进程取证「只连接胜出条目」，全局远端用传输替身计数。
全局根与 HOME 一律指向临时目录，绝不读写真实的 ``~/.alibot``；不联网。

全局 ``mcp.json`` 的顶层形状由本票定义（见
``alibot.mcp.load_global_mcp_declarations``）：``{"servers": [ ... ]}``。
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
from alibot.mcp import load_global_mcp_declarations, runtime_status

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
            "layer=" + label
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


# ─── 1. 全局声明是可选纯文件：缺失零条、不创建；条目补齐合成标识 ─────────────
def test_a_missing_global_file_means_zero_declarations_without_creating_anything(
    tmp_path: Path,
) -> None:
    global_root = _global_root(tmp_path)
    assert load_global_mcp_declarations(str(global_root), "agent-1") == []
    assert not global_root.exists()


def test_global_entries_get_synthetic_identity_and_derive_remote_consent(
    tmp_path: Path,
) -> None:
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

    rows = load_global_mcp_declarations(str(_global_root(tmp_path)), "agent-1", consent_of=consent)

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


# ─── 2. 坏文件 / 坏条目只损失全局层，项目层不受影响 ─────────────────────────
def test_invalid_json_means_zero_global_declarations_and_a_readable_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_global_mcp(tmp_path, "agent-1", [], raw="{ not json")
    with caplog.at_level("WARNING", logger="alibot.mcp"):
        rows = load_global_mcp_declarations(str(_global_root(tmp_path)), "agent-1")
    assert rows == []
    assert any("JSON" in record.getMessage() for record in caplog.records)


def test_a_bad_entry_is_skipped_while_good_entries_still_load(
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
        rows = load_global_mcp_declarations(str(_global_root(tmp_path)), "agent-1")
    assert [row["name"] for row in rows] == ["good"]
    assert len([record for record in caplog.records if record.levelname == "WARNING"]) >= 3


def test_wrong_top_level_shape_is_treated_as_an_invalid_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_global_mcp(tmp_path, "agent-1", [], raw='[{"name": "x", "transport": "stdio"}]')
    with caplog.at_level("WARNING", logger="alibot.mcp"):
        rows = load_global_mcp_declarations(str(_global_root(tmp_path)), "agent-1")
    assert rows == []
    assert any("servers" in record.getMessage() for record in caplog.records)


def test_a_global_file_symlinked_outside_the_global_root_is_ignored(tmp_path: Path) -> None:
    outside = tmp_path / "outside-mcp.json"
    outside.write_text(
        json.dumps({"servers": [{"name": "escape", "transport": "stdio", "command": "python3"}]}),
        encoding="utf-8",
    )
    member_home = _global_root(tmp_path) / "agents" / "agent-1"
    member_home.mkdir(parents=True)
    os.symlink(outside, member_home / "mcp.json")

    assert load_global_mcp_declarations(str(_global_root(tmp_path)), "agent-1") == []


# ─── 3. 项目层两层：未合并读路径保留落选者，原方法形状不变 ───────────────────
@pytest.fixture
def declarations(tmp_path: Path) -> Iterator[Any]:
    database = open_database(tmp_path / "data")
    agent = database.create_agent("三层合并", "工程师", "ollama:qwen2.5", "你是测试。")
    channel = database.create_channel("三层频道", member_ids=[agent["id"]])
    try:
        yield database, channel["id"], agent["id"]
    finally:
        database.close()


def test_project_layers_keep_both_scopes_while_the_merged_reader_is_unchanged(
    declarations: Any,
) -> None:
    database, channel_id, agent_id = declarations
    database.create_mcp_server(
        scope_kind="channel", scope_id=channel_id, name="shared", transport="stdio", command="channel-cmd"
    )
    database.create_mcp_server(
        scope_kind="agent", scope_id=agent_id, name="shared", transport="stdio", command="agent-cmd"
    )
    database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="channel-only",
        transport="stdio",
        command="channel-only-cmd",
    )

    layers = database.mcp_layers_for_target(channel_id, agent_id)

    # 优先级从高到低（Agent 级在前），同名两条都在：合并与落选提示由装配层负责。
    # 层内顺序没有契约：`created_at` 只有毫秒精度、`id` 是随机前缀，同一毫秒插入的两行
    # 先后不确定（`ORDER BY created_at, id`），因此只断言「Agent 级整体在频道级之前」与内容。
    assert len(layers) == 3
    seen_channel = False
    for row in layers:
        if row["scope_kind"] == "agent":
            assert not seen_channel, layers
        else:
            seen_channel = True
    assert {(row["scope_kind"], row["name"], row["command"]) for row in layers} == {
        ("agent", "shared", "agent-cmd"),
        ("channel", "shared", "channel-cmd"),
        ("channel", "channel-only", "channel-only-cmd"),
    }
    # 既有合并读路径保持原形状：同名只留 Agent 级那一条。
    merged = database.mcp_servers_for_target(channel_id, agent_id)
    assert len(merged) == 2
    assert {(row["scope_kind"], row["name"]) for row in merged} == {
        ("agent", "shared"),
        ("channel", "channel-only"),
    }


# ─── 4. 三层合并（真 seam）：每名一条、胜出层正确、落选提示只在 reason ──────
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


def _system_text(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for message in body.get("messages") or []:
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content or []:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts)


def _tool_descriptions(body: dict[str, Any]) -> dict[str, str]:
    return {
        str(tool["function"]["name"]): str(tool["function"].get("description") or "")
        for tool in body.get("tools") or []
    }


def test_three_layers_keep_one_entry_per_name_and_report_losers_only_in_the_reason(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    project.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(project))
    database.update_agent(agent_id, model_id="openai:stub-model")

    database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="team",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "channel"],
    )
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="team",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "agent"],
    )
    database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="channel",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "channel"],
    )
    # 落选的频道声明指向不存在的命令：若被装配，运行期读数会留下失败痕迹。
    loser = database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="broken",
        transport="stdio",
        command="/nonexistent/global-mcp-loser",
    )
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="broken",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "agent"],
    )
    _write_global_mcp(
        tmp_path,
        agent_id,
        [
            _global_stdio("team", stub_script, label="global"),
            _global_stdio("channel", stub_script, label="global"),
            _global_stdio("global", stub_script, label="global"),
        ],
    )

    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_layers_for_target,
        global_home=str(_global_root(tmp_path)),
    )
    outcome = seam.run_round(database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id)

    assert outcome.status == "replied", outcome.reason
    body = _CaptureHandler.request_bodies[0]
    descriptions = {
        name: description
        for name, description in _tool_descriptions(body).items()
        if name.startswith("mcp__")
    }
    # 同名恰好一条，且来自胜出层。
    assert sorted(descriptions) == [
        "mcp__broken__echo",
        "mcp__channel__echo",
        "mcp__global__echo",
        "mcp__team__echo",
    ]
    assert "layer=agent" in descriptions["mcp__team__echo"]
    assert "layer=channel" in descriptions["mcp__channel__echo"]
    assert "layer=global" in descriptions["mcp__global__echo"]
    # 全局 stdio 的 cwd 仍是项目侧成员分区（不指向全局层）。
    assert f"cwd={project / '.alibot' / 'agents' / agent_id}" in descriptions["mcp__global__echo"]
    assert "layer=agent" in descriptions["mcp__broken__echo"]
    # env 值只在装配时从进程环境读。
    assert SECRET_VALUE in descriptions["mcp__global__echo"]

    # 落选条目各一条可读提示（名字 + 胜出层）；胜出条目不产生提示。
    for name, layer in [
        ("team", "项目 Agent 级"),
        ("broken", "项目 Agent 级"),
        ("channel", "项目频道级"),
    ]:
        assert f"「{name}」" in outcome.reason
        assert layer in outcome.reason
    # team 有频道级与全局级两条落选者 ⇒ 两条提示。
    assert outcome.reason.count("「team」") == 2
    assert "「global」" not in outcome.reason
    assert outcome.reason.count("mcp__") == 0

    # 提示不进 provider system prompt。
    system_prompt = _system_text(body)
    assert "被更高优先级" not in system_prompt
    assert "项目频道级" not in system_prompt

    # 落选的频道声明没有被连接（运行期读数里没有它）。
    assert runtime_status(loser["id"]) is None
    assert runtime_status(f"global:{agent_id}:team") is None
    assert runtime_status(f"global:{agent_id}:channel") is None


def test_unconfirmed_global_remote_is_not_connected_until_the_consent_row_exists(
    declarations: Any, stub_provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentscope.mcp import MCPClient

    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    project.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(project))
    database.update_agent(agent_id, model_id="openai:stub-model")
    _write_global_mcp(
        tmp_path,
        agent_id,
        [{"name": "remote", "transport": "http", "url": "http://127.0.0.1:9/mcp"}],
    )
    attempts: list[str] = []

    async def fake_connect(self: Any) -> None:
        attempts.append(self.name)
        raise RuntimeError("stub remote refused")

    monkeypatch.setattr(MCPClient, "connect", fake_connect)
    scope_id = f"global:{agent_id}:remote"
    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        global_home=str(_global_root(tmp_path)),
        global_mcp_consent_of=lambda probe: database.has_remote_consent_scope("mcp", probe),
    )
    agent = database.get_agent(agent_id)

    # 未确认：一次连接尝试都没有；运行期读数给出可读的 `consent_required`。
    first = seam.run_round(agent, [_human()], mentioned=False, target_id=channel_id)
    assert first.status == "replied", first.reason
    assert attempts == []
    unconfirmed = runtime_status(scope_id)
    assert unconfirmed is not None
    assert unconfirmed["connected"] is False
    assert "确认" in (unconfirmed["error"] or "") or "acknowledg" in (unconfirmed["error"] or "")
    first_tools = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__remote__echo" not in first_tools

    # 人类面确认入口属下一票：这里只写既有的确认行，证明装配层读的是它。
    database.record_remote_consent_scope("mcp", scope_id)

    second = seam.run_round(agent, [_human()], mentioned=False, target_id=channel_id)
    assert second.status == "replied", second.reason
    assert attempts == ["remote"]
    confirmed = runtime_status(scope_id)
    assert confirmed is not None
    assert confirmed["connected"] is False
    assert "stub remote refused" in (confirmed["error"] or "")


# ─── 5. 降级：坏全局文件不拖累项目层；缺失文件不创建目录 ────────────────────
def _prepare_project(database: Any, channel_id: str, agent_id: str, project: Path) -> None:
    project.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(project))
    database.update_agent(agent_id, model_id="openai:stub-model")


def test_invalid_global_json_leaves_the_project_layer_untouched(
    declarations: Any,
    stub_script: Path,
    stub_provider: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="project",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "agent"],
    )
    _write_global_mcp(tmp_path, agent_id, [], raw="{ this is not json")
    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_layers_for_target,
        global_home=str(_global_root(tmp_path)),
    )
    with caplog.at_level("WARNING", logger="alibot.mcp"):
        outcome = seam.run_round(
            database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id
        )

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "layer=agent" in descriptions["mcp__project__echo"]
    assert any("JSON" in record.getMessage() for record in caplog.records)
    # 全局层零条：既没有工具，也没有落选提示（提示只针对同名落选）。
    assert "被更高优先级" not in outcome.reason


def test_a_bad_global_entry_is_skipped_without_blocking_the_good_entry(
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
            _global_stdio("good", stub_script, label="global"),
        ],
    )
    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_layers_for_target,
        global_home=str(_global_root(tmp_path)),
    )
    outcome = seam.run_round(database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id)

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__good__echo" in descriptions
    assert "mcp__bad__echo" not in descriptions


def test_a_round_does_not_create_global_mcp_layers_or_write_global_entries_to_the_database(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    _write_global_mcp(tmp_path, agent_id, [_global_stdio("global", stub_script, label="global")])
    global_root = _global_root(tmp_path)
    before = sorted(str(path.relative_to(global_root)) for path in global_root.rglob("*"))
    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_layers_for_target,
        global_home=str(global_root),
    )
    outcome = seam.run_round(database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id)

    assert outcome.status == "replied", outcome.reason
    after = sorted(str(path.relative_to(global_root)) for path in global_root.rglob("*"))
    # 唯一允许的写入是这一轮的记忆工作区（ADR-0035）；MCP 声明仍只读、不新建任何层。
    assert _without_memory(after, agent_id) == _without_memory(before, agent_id)
    # 全局声明是纯文件：不进 ``mcp_server`` 表，也没有任何库行带 env 值。
    assert database.list_mcp_servers(scope_kind="agent", scope_id=agent_id) == []
    assert database.list_mcp_servers(scope_kind="channel", scope_id=channel_id) == []


def test_a_round_without_a_project_mount_assembles_no_mcp_at_all(
    declarations: Any, stub_provider: str, tmp_path: Path
) -> None:
    database, _channel_id, agent_id = declarations
    database.update_agent(agent_id, model_id="openai:stub-model")
    _write_global_mcp(
        tmp_path,
        agent_id,
        [
            {
                "name": "global",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(tmp_path / "stub_mcp.py"), "global"],
            }
        ],
    )
    dm, _created = database.create_dm(agent_id)
    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_layers_for_target,
        global_home=str(_global_root(tmp_path)),
    )
    outcome = seam.run_round(
        database.get_agent(agent_id),
        [_human("你好")],
        mentioned=False,
        target_id=dm["id"],
        direct_message=True,
    )

    assert outcome.status == "replied", outcome.reason
    tools = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert not [name for name in tools if name.startswith("mcp__")]
    # 没有项目根 ⇒ 不读全局声明，也不产生落选提示。
    assert "被更高优先级" not in outcome.reason
