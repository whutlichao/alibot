"""#151 MCP 声明的 HTTP 契约：CRUD、env 不泄露、远端确认、零副作用。

只经人类面取证：

1. 声明可增删改查，scope 过滤生效；
2. ``env_names`` 只存**变量名**：响应与库文件里都不出现值；正文带 ``env`` 直接拒绝；
3. 远端（http/sse）声明未确认时 ``consented=false``，确认幂等、stdio 不接受确认；
4. 配置动作不落消息、不产生轮次结果、不改变预算读数。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import budget, create_agent, create_channel, messages, outcomes

SECRET_NAME = "ALIBOT_MCP_TEST_TOKEN"
SECRET_VALUE = "sk-mcp-super-secret-value-151"


def _stdio_payload(**overrides):
    payload = {
        "scope_kind": "channel",
        "name": "local-tools",
        "transport": "stdio",
        "command": "python3",
        "args": ["-m", "example_mcp"],
        "env_names": [SECRET_NAME],
    }
    payload.update(overrides)
    return payload


def test_declarations_round_trip_and_scope_filtering(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    agent = create_agent(client).json()["agent"]

    channel_server = client.post(
        "/api/mcp-servers", json=_stdio_payload(scope_kind="channel", scope_id=channel["id"])
    )
    assert channel_server.status_code == 201
    created = channel_server.json()["mcp_server"]
    assert created["name"] == "local-tools"
    assert created["args"] == ["-m", "example_mcp"]
    assert created["env_names"] == [SECRET_NAME]
    assert created["scope_kind"] == "channel"
    assert created["scope_id"] == channel["id"]
    assert created["enabled"] is True
    assert created["is_remote"] is False
    assert created["consent_required"] is False
    # 从未装配过：运行时读数为 null，而不是伪造一个 connected。
    assert created["runtime"] is None

    agent_server = client.post(
        "/api/mcp-servers",
        json=_stdio_payload(scope_kind="agent", scope_id=agent["id"], name="agent-tools"),
    )
    assert agent_server.status_code == 201

    scoped = client.get(
        "/api/mcp-servers", params={"scope_kind": "channel", "scope_id": channel["id"]}
    ).json()["mcp_servers"]
    assert [server["name"] for server in scoped] == ["local-tools"]

    everything = client.get("/api/mcp-servers").json()["mcp_servers"]
    assert {server["name"] for server in everything} == {"local-tools", "agent-tools"}

    patched = client.patch(
        f"/api/mcp-servers/{created['id']}",
        json={"command": "python3.11", "enabled": False, "env_names": []},
    )
    assert patched.status_code == 200
    updated = patched.json()["mcp_server"]
    assert updated["command"] == "python3.11"
    assert updated["enabled"] is False
    assert updated["env_names"] == []
    assert updated["updated_at"] >= updated["created_at"]

    deleted = client.delete(f"/api/mcp-servers/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["mcp_server"]["id"] == created["id"]
    after_delete = client.get(
        "/api/mcp-servers", params={"scope_kind": "channel", "scope_id": channel["id"]}
    ).json()["mcp_servers"]
    assert after_delete == []
    assert client.delete(f"/api/mcp-servers/{created['id']}").status_code == 404


def test_env_values_never_appear_in_responses_or_the_database(
    client: TestClient, data_dir: Path, monkeypatch
) -> None:
    monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
    channel = create_channel(client).json()["channel"]

    response = client.post(
        "/api/mcp-servers", json=_stdio_payload(scope_kind="channel", scope_id=channel["id"])
    )
    assert response.status_code == 201
    assert SECRET_VALUE not in response.text

    listed = client.get("/api/mcp-servers", params={"scope_kind": "channel", "scope_id": channel["id"]})
    assert listed.status_code == 200
    assert SECRET_VALUE not in listed.text
    assert listed.json()["mcp_servers"][0]["env_names"] == [SECRET_NAME]

    # 正文里带值（而不是变量名）整体拒绝：没有 `env` 这条写路径。
    refused = client.post(
        "/api/mcp-servers",
        json=_stdio_payload(
            scope_kind="channel",
            scope_id=channel["id"],
            name="leaky",
            env={SECRET_NAME: SECRET_VALUE},
        ),
    )
    assert refused.status_code == 400
    assert SECRET_VALUE not in refused.text
    assert all(server["name"] != "leaky" for server in client.get("/api/mcp-servers").json()["mcp_servers"])

    # 库文件（含 WAL / SHM）里没有值；只有变量名。
    haystack = b"".join(path.read_bytes() for path in data_dir.rglob("*") if path.is_file())
    assert SECRET_VALUE.encode() not in haystack
    assert SECRET_NAME.encode() in haystack


def test_validation_rejects_incomplete_or_ambiguous_declarations(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    scope = {"scope_kind": "channel", "scope_id": channel["id"]}

    missing_command = client.post(
        "/api/mcp-servers", json={**scope, "name": "no-command", "transport": "stdio"}
    )
    assert missing_command.status_code == 400
    assert "command" in missing_command.json()["error"]["fields"]

    missing_url = client.post("/api/mcp-servers", json={**scope, "name": "no-url", "transport": "http"})
    assert missing_url.status_code == 400
    assert "url" in missing_url.json()["error"]["fields"]

    bad_sse = client.post(
        "/api/mcp-servers",
        json={**scope, "name": "bad-sse", "transport": "sse", "url": "http://127.0.0.1:9000/mcp"},
    )
    assert bad_sse.status_code == 400
    assert "url" in bad_sse.json()["error"]["fields"]

    mislabeled = client.post(
        "/api/mcp-servers",
        json={**scope, "name": "mislabeled", "transport": "http", "url": "http://127.0.0.1:9000/sse"},
    )
    assert mislabeled.status_code == 400
    assert "url" in mislabeled.json()["error"]["fields"]

    bad_name = client.post(
        "/api/mcp-servers",
        json={**scope, "name": "有空格 的名字", "transport": "stdio", "command": "python3"},
    )
    assert bad_name.status_code == 400
    assert "name" in bad_name.json()["error"]["fields"]

    bad_env = client.post(
        "/api/mcp-servers",
        json={
            **scope,
            "name": "bad-env",
            "transport": "stdio",
            "command": "python3",
            "env_names": [f"{SECRET_NAME}={SECRET_VALUE}"],
        },
    )
    assert bad_env.status_code == 400
    assert "env_names" in bad_env.json()["error"]["fields"]

    unknown_scope = client.post(
        "/api/mcp-servers",
        json={**_stdio_payload(name="ghost"), "scope_id": "missing-scope"},
    )
    assert unknown_scope.status_code == 400

    first = client.post(
        "/api/mcp-servers", json={**scope, "name": "dup", "transport": "stdio", "command": "python3"}
    )
    assert first.status_code == 201
    duplicate = client.post(
        "/api/mcp-servers", json={**scope, "name": "dup", "transport": "stdio", "command": "python3"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "mcp_server_conflict"


def test_remote_consent_is_required_and_idempotent(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    remote = client.post(
        "/api/mcp-servers",
        json={
            "scope_kind": "channel",
            "scope_id": channel["id"],
            "name": "remote-tools",
            "transport": "sse",
            "url": "http://127.0.0.1:9000/sse",
        },
    ).json()["mcp_server"]
    assert remote["is_remote"] is True
    assert remote["consent_required"] is True
    assert remote["consented"] is False

    first = client.post(f"/api/mcp-servers/{remote['id']}/consent")
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["mcp_server"]["consented"] is True
    assert first_payload["consent"]["scope_kind"] == "mcp"
    assert first_payload["consent"]["scope_id"] == remote["id"]
    assert first_payload["consent"]["acknowledged_at"].endswith("Z")

    second = client.post(f"/api/mcp-servers/{remote['id']}/consent")
    assert second.status_code == 200
    assert second.json()["consent"]["acknowledged_at"] == first_payload["consent"]["acknowledged_at"]
    assert second.json()["mcp_server"]["consented"] is True

    local = client.post(
        "/api/mcp-servers",
        json={
            "scope_kind": "channel",
            "scope_id": channel["id"],
            "name": "local-only",
            "transport": "stdio",
            "command": "python3",
        },
    ).json()["mcp_server"]
    refused = client.post(f"/api/mcp-servers/{local['id']}/consent")
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "validation_failed"


def test_configuration_changes_do_not_message_wake_or_spend_budget(client: TestClient) -> None:
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
    before_messages = messages(client, channel["id"])
    before_budget = budget(client, channel["id"])
    before_outcomes = outcomes(client, channel["id"])

    remote = client.post(
        "/api/mcp-servers",
        json={
            "scope_kind": "agent",
            "scope_id": agent["id"],
            "name": "remote",
            "transport": "http",
            "url": "http://127.0.0.1:9000/mcp",
        },
    ).json()["mcp_server"]
    assert client.post(f"/api/mcp-servers/{remote['id']}/consent").status_code == 200
    assert client.patch(f"/api/mcp-servers/{remote['id']}", json={"enabled": False}).status_code == 200
    assert client.delete(f"/api/mcp-servers/{remote['id']}").status_code == 200

    assert messages(client, channel["id"]) == before_messages
    assert budget(client, channel["id"]) == before_budget
    assert outcomes(client, channel["id"]) == before_outcomes
