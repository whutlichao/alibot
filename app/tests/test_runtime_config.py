"""运行时配置的 HTTP 往返、显式 null 与整体拒绝。"""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_agent


def test_runtime_config_roundtrip_omission_and_explicit_null(client: TestClient) -> None:
    agent = create_agent(client).json()["agent"]
    url = f"/api/agents/{agent['id']}"
    values = {
        "context_window_messages": 60,
        "context_trigger_ratio": 0.6,
        "react_max_iters": 45,
    }
    result = client.patch(url, json=values)
    assert result.status_code == 200, result.text
    assert {key: result.json()["agent"][key] for key in values} == values
    assert client.patch(url, json={"role": "新角色"}).status_code == 200
    assert {key: client.get(url).json()["agent"][key] for key in values} == values
    cleared = client.patch(url, json=dict.fromkeys(values))
    assert cleared.status_code == 200, cleared.text
    assert {key: cleared.json()["agent"][key] for key in values} == dict.fromkeys(values)


@pytest.mark.parametrize(
    "invalid",
    [
        {"context_window_messages": 0},
        {"context_trigger_ratio": 1},
        {"react_max_iters": -1},
    ],
)
def test_invalid_runtime_config_does_not_save_other_fields(client: TestClient, invalid: dict) -> None:
    agent = create_agent(client).json()["agent"]
    url = f"/api/agents/{agent['id']}"
    result = client.patch(url, json={"role": "不应保存", **invalid})
    assert result.status_code == 400, result.text
    assert client.get(url).json()["agent"]["role"] == agent["role"]


def test_memory_mode_is_no_longer_accepted_by_the_agent_update_contract(client: TestClient) -> None:
    """长期记忆没有开关：``PATCH`` 带 ``memory_mode`` 整体 400，且不改任何字段。"""
    agent = create_agent(client).json()["agent"]
    url = f"/api/agents/{agent['id']}"

    result = client.patch(url, json={"role": "不应保存", "memory_mode": "off"})

    assert result.status_code == 400, result.text
    assert result.json()["error"]["code"] == "validation_failed"
    stored = client.get(url).json()["agent"]
    assert stored["role"] == agent["role"]
    assert "memory_mode" not in stored
