"""远端数据流告知：必须在首次远端调用之前确认，未确认不生效，且不得声称 E2EE。

只经 HTTP 面取证：告知文案的数据面清单、确认前后 Agent 状态点与轮次 reason 的差别、
以及「未确认时一个远端调用都不发生」（由脚本化替身的调用记录在行为测试里断言）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import LOCAL_MODEL_ID, REMOTE_MODEL_ID, agents, backend, channel_with_agent, create_agent


def test_data_flow_statement_lists_everything_that_leaves_the_machine(client: TestClient) -> None:
    payload = client.get("/api/remote-data-flow").json()

    for face in ("消息正文", "发送者显示名", "频道名称与描述", "被点名成员的显示名"):
        assert face in payload["data_faces"], face
    assert payload["statement"]
    # 不得声称端到端加密，且默认只绑 loopback。
    assert payload["end_to_end_encrypted"] is False
    assert payload["loopback_only"] is True
    assert "端到端加密" in payload["statement"]


def test_remote_agent_is_offline_until_the_notice_is_acknowledged(client: TestClient) -> None:
    agent = create_agent(client, model_id=REMOTE_MODEL_ID).json()["agent"]

    assert agent["status"] == "offline"
    assert agent["remote_consent"] is False
    assert agent["enabled_reason"]


def test_local_model_needs_no_remote_notice(client: TestClient) -> None:
    agent = create_agent(client, model_id=LOCAL_MODEL_ID).json()["agent"]

    assert agent["is_remote"] is False
    assert agent["status"] == "online"
    # 本机提供商根本不该被要求确认远程数据面。
    refused = client.post("/api/remote-data-flow/ack", json={"provider": "ollama"})
    assert refused.status_code == 400


def test_acknowledgement_turns_the_agent_on(client: TestClient, monkeypatch) -> None:
    # 凭证只从进程环境变量读：给一个**假** key 就足以让「已确认 + 有凭证」这一态成立。
    # 本用例不会真的调用远端：四行为都由脚本化替身固定，测试不联网、零成本。
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-not-a-real-key")
    create_agent(client, model_id=REMOTE_MODEL_ID)

    accepted = client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})

    assert accepted.status_code == 200
    assert accepted.json()["consent"]["provider"] == "dashscope"
    assert accepted.json()["consent"]["acknowledged_at"].endswith("Z")
    assert [agent["status"] for agent in accepted.json()["agents"]] == ["online"]
    assert agents(client)[0]["remote_consent"] is True


def test_acknowledgement_is_per_provider_and_idempotent(client: TestClient) -> None:
    first = client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"}).json()["consent"]
    again = client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"}).json()["consent"]

    # 一次确认就是一次：重复确认不覆盖首次时间。
    assert again["acknowledged_at"] == first["acknowledged_at"]
    # 别的 provider 不受影响。
    assert client.get("/api/remote-data-flow").json()["consents"] == [first]


def test_unknown_provider_is_refused(client: TestClient) -> None:
    response = client.post("/api/remote-data-flow/ack", json={"provider": "not-a-provider"})

    assert response.status_code == 400
    assert "provider" in response.json()["error"]["fields"]


def test_consent_and_registry_hold_no_secrets(client: TestClient) -> None:
    """凭证只从进程环境变量读：告知记录与 Agent 配置里都不出现任何密钥字段。"""
    create_agent(client, model_id=REMOTE_MODEL_ID)
    client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})

    flow = client.get("/api/remote-data-flow").json()
    agent = agents(client)[0]

    assert set(flow["consents"][0].keys()) == {"provider", "acknowledged_at"}
    for forbidden in ("api_key", "apiKey", "key", "secret", "token", "credential"):
        assert forbidden not in agent, forbidden
        assert forbidden not in flow, forbidden


def test_missing_credential_is_reported_readably(client: TestClient, monkeypatch) -> None:
    """缺凭证 = 未启用，且「为什么」必须可读——不能安静地什么都不做。"""
    # 明确拿掉凭证：本用例断言的就是「环境里没有 key」时的可读原因。
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    create_agent(client, model_id=REMOTE_MODEL_ID)
    client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})

    agent = client.get("/api/agents").json()["agents"][0]
    assert agent["status"] == "offline"
    assert "DASHSCOPE_API_KEY" in agent["enabled_reason"]


def test_consent_survives_backend_restart(data_dir: Path) -> None:
    with backend(data_dir) as first:
        create_agent(first, model_id=REMOTE_MODEL_ID)
        first.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})

    with backend(data_dir) as second:
        assert [record["provider"] for record in second.get("/api/remote-data-flow").json()["consents"]] == [
            "dashscope"
        ]
        assert agents(second)[0]["remote_consent"] is True


def test_agent_in_channel_is_woken_only_after_consent(client: TestClient, harness: dict, monkeypatch) -> None:
    """未确认时被 @ 必须给出可读的轮次 reason（failed），且绝不发起远端调用。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-not-a-real-key")
    agent = create_agent(client, model_id=REMOTE_MODEL_ID).json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    client.post(f"/api/channels/{channel['id']}/messages", json={"body": f"@{agent['display_name']} 看一下"})
    harness["scheduler"].drain()

    # 未确认 → 根本没启用，所以适配层一次都没被调到。
    assert harness["seam"].call_count == 0
    outcome = client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"][0]
    assert outcome["status"] == "failed"
    assert "未确认远端数据流告知" in outcome["reason"]
