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
        "memory_mode": "reme",
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
        {"memory_mode": "mem0"},
        # 旧档位名不再被接受（ADR-0034 / schema 24）。
        {"memory_mode": "agentic"},
    ],
)
def test_invalid_runtime_config_does_not_save_other_fields(client: TestClient, invalid: dict) -> None:
    agent = create_agent(client).json()["agent"]
    url = f"/api/agents/{agent['id']}"
    result = client.patch(url, json={"role": "不应保存", **invalid})
    assert result.status_code == 400, result.text
    assert client.get(url).json()["agent"]["role"] == agent["role"]
