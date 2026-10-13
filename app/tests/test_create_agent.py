"""新建 Agent 与模型配置：人面入口、校验原子性、配置落库、勾选加入频道。

只经 HTTP 面取证：新建返回的配置、工作台快照里的 Agent 清单、频道初始成员勾选清单、
重启后仍在。测试不连数据库、不读表结构。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import (
    LOCAL_MODEL_ID,
    REMOTE_MODEL_ID,
    agents,
    channel_with_agent,
    create_agent,
    create_channel,
    messages,
)


def test_create_agent_returns_name_role_model_and_prompt(client: TestClient) -> None:
    response = create_agent(
        client, name="小明", role="前端工程师", model_id=LOCAL_MODEL_ID, system_prompt="你是前端工程师。"
    )

    assert response.status_code == 201
    agent = response.json()["agent"]
    assert agent["kind"] == "agent"
    assert agent["display_name"] == "小明"
    assert agent["role"] == "前端工程师"
    # 模型与系统提示是**配置数据**，落库内容等于用户输入。
    assert agent["model_id"] == LOCAL_MODEL_ID
    assert agent["system_prompt"] == "你是前端工程师。"
    assert agent["enabled"] is True
    assert 0 <= agent["avatar_slot"] <= 4


def test_empty_name_leaves_no_half_written_agent(client: TestClient) -> None:
    response = create_agent(client, name="   ")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert "name" in body["error"]["fields"]

    # 整体不创建：HTTP 面看不到任何残留，owner 仍是唯一成员行。
    assert agents(client) == []
    assert [member["id"] for member in client.get("/api/workspace").json()["members"]] == ["owner"]


def test_empty_model_leaves_no_half_written_agent(client: TestClient) -> None:
    response = create_agent(client, model_id="")

    assert response.status_code == 400
    assert "model_id" in response.json()["error"]["fields"]
    assert agents(client) == []


def test_model_needs_a_known_provider_prefix(client: TestClient) -> None:
    """provider 前缀决定凭证来源与是否远端，所以它必须在白名单里。"""
    for bad in ("qwen-max", "unknown:qwen-max", "dashscope:", ":qwen-max"):
        response = create_agent(client, model_id=bad)
        assert response.status_code == 400, bad
        assert "model_id" in response.json()["error"]["fields"], bad

    assert agents(client) == []


def test_agent_appears_in_the_channel_member_picker_and_joins_when_checked(client: TestClient) -> None:
    agent = create_agent(client).json()["agent"]

    # 新建频道的初始成员勾选清单（工作台快照里的 Agent 清单就是那个控件的数据源）。
    listed = agents(client)
    assert [item["id"] for item in listed] == [agent["id"]]

    channel = channel_with_agent(client, agent["id"])
    members = client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    assert [member["id"] for member in members] == ["owner", agent["id"]]


def test_unchecked_agent_is_not_a_channel_member(client: TestClient) -> None:
    create_agent(client)

    channel = create_channel(client).json()["channel"]

    members = client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    assert [member["id"] for member in members] == ["owner"]


def test_agent_config_is_not_a_code_constant(launched: dict) -> None:
    """换模型不需要改协作域代码：配置落库，重启后读回来的还是用户填的那个模型。"""
    client = launched["client"]
    fake = create_agent(client, name="可换模型", model_id=REMOTE_MODEL_ID).json()["agent"]

    after = launched["restart"]()
    listed = {item["id"]: item for item in agents(after)}

    assert listed[fake["id"]]["model_id"] == REMOTE_MODEL_ID
    assert listed[fake["id"]]["system_prompt"] == "你是前端工程师。"
    assert listed[fake["id"]]["role"] == "前端工程师"


def test_agent_is_not_an_http_caller(client: TestClient) -> None:
    """HTTP 面属于人类面：没有「以某成员名义调用」的路径，也没有改配置 / 删 Agent 的端点。"""
    agent = create_agent(client).json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    # Agent 没有凭证可拿，也没有任何端点接受「我以某成员身份」这种自述。
    assert client.post("/api/agents", json={"name": "伪造", "model_id": LOCAL_MODEL_ID}).status_code == 201
    assert client.put(f"/api/agents/{agent['id']}", json={"model_id": REMOTE_MODEL_ID}).status_code == 404
    assert client.patch(f"/api/agents/{agent['id']}", json={"role": "改角色"}).status_code == 404
    assert client.delete(f"/api/agents/{agent['id']}").status_code == 404
    # 改配置的企图全部落空：配置仍是创建时的那份。
    assert client.get(f"/api/agents/{agent['id']}").json()["agent"]["model_id"] == LOCAL_MODEL_ID

    # 人类面也没有「往频道里塞一条 agent/system 消息」的端点。
    assert (
        client.post(
            f"/api/channels/{channel['id']}/messages", json={"body": "冒充", "kind": "agent"}
        ).status_code
        == 400
    )
    assert messages(client, channel["id"]) == []
