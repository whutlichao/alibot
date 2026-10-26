"""#233 / #271 MCP 声明两层合并：成员级库行 / 全局成员家目录（频道级旧行装配忽略）。

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


def test_only_member_scope_rows_are_read_while_channel_rows_stay_in_the_database(
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
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="disabled",
        transport="stdio",
        command="disabled-cmd",
        enabled=False,
    )

    # 未合并读路径只给成员行（含停用行），频道级旧行不进入装配。
    layers = database.mcp_layers_for_target(channel_id, agent_id)
    assert {(row["name"], row["scope_kind"], row["command"]) for row in layers} == {
        ("shared", "agent", "agent-cmd"),
        ("disabled", "agent", "disabled-cmd"),
    }
    merged = database.mcp_servers_for_target(channel_id, agent_id)
    assert [(row["name"], row["scope_kind"], row["command"]) for row in merged] == [
        ("shared", "agent", "agent-cmd")
    ]
    # 频道级行本身没有被删。
    assert {row["name"] for row in database.list_mcp_servers(scope_kind="channel", scope_id=channel_id)} == {
        "shared",
        "channel-only",
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


def test_the_file_is_not_assembled_and_an_unimported_entry_is_reported_readably(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    """#275：``mcp.json` 不再是装配层——文件里有条目而库里没有 ⇒ 运行期**不连接**，只给可读提示。

    这一条替换了本文件原先的「两层合并、同名落选者进 reason」用例（那正是被移除的层）。判据仍是
    「这一轮真的发生了什么」：真 seam + stub provider 读工具清单与 reason，真实 stdio 子进程
    取证「谁被连接了」。

    文件里的 ``global-*`` 是**给过但没导入**的条目：它们必须在两处都不出现——工具清单里没有它们，
    运行期读数里也没有它们（没连接 = 连尝试都没有）。
    """
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    project.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(project))
    database.update_agent(agent_id, model_id="openai:stub-model")

    # 频道级旧行：保留但不进入装配（若被装配，``channel`` 会出现在工具清单里）。
    database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="team",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "channel"],
    )
    database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="channel",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "channel"],
    )
    # 频道级旧行指向不存在的命令：若被装配，运行期读数会留下失败痕迹。
    channel_only = database.create_mcp_server(
        scope_kind="channel",
        scope_id=channel_id,
        name="broken",
        transport="stdio",
        command="/nonexistent/global-mcp-loser",
    )
    # 成员级库行：这是**唯一**的装配来源。
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="team",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "agent"],
    )
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="broken",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "agent"],
    )
    # 文件里有两条**未导入**的声明：它们不是装配来源。
    _write_global_mcp(
        tmp_path,
        agent_id,
        [
            _global_stdio("team", stub_script, label="global"),
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
    # 只有库行装配：文件里的同名条目**没有**参与合并，文件独有的条目也没进来。
    assert sorted(descriptions) == ["mcp__broken__echo", "mcp__team__echo"]
    assert "layer=agent" in descriptions["mcp__team__echo"]
    assert "layer=agent" in descriptions["mcp__broken__echo"]
    # 文件里那两条都没被连接（连尝试都没有）。
    assert runtime_status(f"global:{agent_id}:team") is None
    assert runtime_status(f"global:{agent_id}:global") is None
    # 频道级旧行没有被连接。
    assert runtime_status(channel_only["id"]) is None

    # 可读提示：说清「文件里还有未导入的条目」并给出去哪一条命令，且**不含**工具名。
    assert "未导入" in outcome.reason
    assert "migrate-mcp" in outcome.reason
    assert "「team」" not in outcome.reason
    assert outcome.reason.count("mcp__") == 0
    # 提示不进 provider system prompt。
    system_prompt = _system_text(body)
    assert "未导入" not in system_prompt
    assert "频道级" not in system_prompt


def test_an_unconfirmed_remote_is_not_connected_until_the_consent_row_exists(
    declarations: Any, stub_provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#275 后的口径：远端的「未确认不建连」说的是**库行**，文件里的条目永远不建连。

    原用例咬的是「文件里的远端条目在确认前不连接、确认后连接」。装配层移除后，文件条目没有
    「确认后连接」这条路可走了（要连接必须先 `migrate-mcp` 导入成库行），因此这里拆成两半：
    库行远端走既有的 `consent_required` 分支；文件里的同名/远端条目**一次尝试都没有**。
    """
    from agentscope.mcp import MCPClient

    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    project.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(project))
    database.update_agent(agent_id, model_id="openai:stub-model")
    row = database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="remote",
        transport="http",
        url="http://127.0.0.1:9/mcp",
    )
    # 文件里另有两条远端：一条同名、一条独有。都不是装配来源。
    _write_global_mcp(
        tmp_path,
        agent_id,
        [
            {"name": "remote", "transport": "http", "url": "http://127.0.0.1:9/mcp"},
            {"name": "file-only", "transport": "http", "url": "http://127.0.0.1:9/mcp"},
        ],
    )
    attempts: list[str] = []

    async def fake_connect(self: Any) -> None:
        attempts.append(self.name)
        raise RuntimeError("stub remote refused")

    monkeypatch.setattr(MCPClient, "connect", fake_connect)
    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_layers_for_target,
        global_home=str(_global_root(tmp_path)),
    )
    agent = database.get_agent(agent_id)

    # 库行远端未确认：一次连接尝试都没有；运行期读数给出可读的 `consent_required`。
    first = seam.run_round(agent, [_human()], mentioned=False, target_id=channel_id)
    assert first.status == "replied", first.reason
    assert attempts == []
    unconfirmed = runtime_status(row["id"])
    assert unconfirmed is not None
    assert unconfirmed["connected"] is False
    assert "确认" in (unconfirmed["error"] or "") or "acknowledg" in (unconfirmed["error"] or "")
    first_tools = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__remote__echo" not in first_tools
    # 文件里的条目从来没有被连接过（连运行期读数都没有）。
    assert runtime_status(f"global:{agent_id}:remote") is None
    assert runtime_status(f"global:{agent_id}:file-only") is None
    # 有一条可读提示说清「文件里还有未导入的条目」。
    assert "未导入" in first.reason
    assert "migrate-mcp" in first.reason

    # 人类面确认入口属下一票：这里只写既有的确认行，证明装配层读的是它。
    database.record_remote_consent_scope("mcp", row["id"])

    second = seam.run_round(agent, [_human()], mentioned=False, target_id=channel_id)
    assert second.status == "replied", second.reason
    assert attempts == ["remote"]
    confirmed = runtime_status(row["id"])
    assert confirmed is not None
    assert confirmed["connected"] is False
    assert "stub remote refused" in (confirmed["error"] or "")


# ─── 5. 降级：坏全局文件不拖累项目层；缺失文件不创建目录 ────────────────────
def _prepare_project(database: Any, channel_id: str, agent_id: str, project: Path) -> None:
    project.mkdir()
    database.update_channel_config(channel_id, workspace_root=str(project))
    database.update_agent(agent_id, model_id="openai:stub-model")


def test_a_broken_global_file_never_affects_the_database_rows(
    declarations: Any,
    stub_script: Path,
    stub_provider: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#275：文件坏掉只影响「有没有未导入提示」，不影响装配（装配只看库行）。

    原用例咬的是「坏全局文件不拖累项目层」。装配层移除后这条降级路径连同旧实现一起消失，
    留下更强的判据：文件是非法 JSON 时，**库行照常装配**，且不产生任何「有未导入条目」的提示
    （读不出可导入的条目 = 没有可提示的东西）。
    """
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
    outcome = seam.run_round(database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id)

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "layer=agent" in descriptions["mcp__project__echo"]
    # 文件不可解析 ⇒ 没有可导入的条目，也就不发「未导入」提示。
    assert "未导入" not in outcome.reason


def test_a_file_entry_is_not_assembled_even_when_other_entries_are_malformed(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    """#275：文件里的好坏条目都不进装配——「坏条目被跳过、好条目照装」那条旧路径已不存在。

    原用例咬的是「坏条目只损失自己那一条」。现在文件不是装配来源，因此判据变成更强的形态：
    文件里**一条都不装配**（无论好坏），运行期读数里也没有它们，只有一条「有未导入条目」提示。
    """
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
    assert "mcp__good__echo" not in descriptions
    assert "mcp__bad__echo" not in descriptions
    assert runtime_status(f"global:{agent_id}:good") is None
    # 文件里有一条可读的声明（good）⇒ 发一条「有未导入条目」的提示。
    assert "未导入" in outcome.reason
    assert "migrate-mcp" in outcome.reason


def test_an_imported_entry_stops_being_reported_as_not_imported(
    declarations: Any, stub_script: Path, stub_provider: str, tmp_path: Path
) -> None:
    """#275 review P1：按 README 走完「导入后保留文件」的人，不该再看到「未导入」的假提示。

    这条是那个假陈述的回归钉：文件里有一条**已导入成库行**的条目，本轮必须
    ① 装配它（从库行来）、② **不**再报「文件里还有未导入的条目」。只按文件条目计数的实现会在这里红。
    """
    database, channel_id, agent_id = declarations
    project = tmp_path / "project"
    _prepare_project(database, channel_id, agent_id, project)
    # 库里已有同名同形行（= `migrate-mcp --apply` 之后的状态），文件**保留**。
    database.create_mcp_server(
        scope_kind="agent",
        scope_id=agent_id,
        name="imported",
        transport="stdio",
        command=sys.executable,
        args=[str(stub_script), "imported"],
    )
    _write_global_mcp(tmp_path, agent_id, [_global_stdio("imported", stub_script, label="global")])
    seam = AgentScopeAgentSeam(
        timeout_seconds=10.0,
        workspace_of=database.workspace_settings_for_target,
        mcp_of=database.mcp_layers_for_target,
        global_home=str(_global_root(tmp_path)),
    )
    outcome = seam.run_round(database.get_agent(agent_id), [_human()], mentioned=False, target_id=channel_id)

    assert outcome.status == "replied", outcome.reason
    descriptions = _tool_descriptions(_CaptureHandler.request_bodies[0])
    assert "mcp__imported__echo" in descriptions
    # 装配的是库行那一份（label=imported），不是文件里那份（label=global）。
    assert "layer=imported" in descriptions["mcp__imported__echo"]
    assert "未导入" not in outcome.reason


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
