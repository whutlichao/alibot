"""Agent 详情与编辑：`PATCH /api/agents/{id}` 的 HTTP 黑盒契约（ADR-0011）。

只经 HTTP 面取证：逐字段读回、非法输入的**整体拒绝**（无部分写入）、「暂停」= 写 `enabled = 0`
及其可读原因、改到远端 provider 沿用**既有远端数据流告知**语义、以及**配置变更不是发言**
（消息 / 轮次结果 / 预算三处读数前后逐字不变）。概览页的四个数字也在这里按**既有事实**
（`round_outcome` 聚合 + 任务行 + 频道成员表）对照断言——测试不连数据库、不读表结构。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import (
    LOCAL_MODEL_ID,
    REMOTE_MODEL_ID,
    agents,
    backend,
    budget,
    channel_with_agent,
    create_agent,
    create_channel,
    messages,
    outcomes,
    send,
)


def patch_agent(client: TestClient, member_id: str, **fields):
    return client.patch(f"/api/agents/{member_id}", json=fields)


def detail(client: TestClient, member_id: str) -> dict:
    return client.get(f"/api/agents/{member_id}").json()["agent"]


def test_patch_writes_every_writable_field_and_reads_it_back(client: TestClient) -> None:
    agent = create_agent(client, name="原名", role="原角色", model_id=LOCAL_MODEL_ID).json()["agent"]

    response = patch_agent(
        client,
        agent["id"],
        display_name="新名字",
        role="新角色",
        model_id="ollama:qwen3",
        system_prompt="你只回答一句话。",
        enabled=False,
        auto_claim=False,
    )

    assert response.status_code == 200
    body = response.json()
    written = body["agent"]
    assert written["display_name"] == "新名字"
    assert written["role"] == "新角色"
    assert written["model_id"] == "ollama:qwen3"
    assert written["system_prompt"] == "你只回答一句话。"
    assert written["enabled"] is False
    assert written["auto_claim"] is False
    # 响应与后续两个读面说的是同一件事（读回一致，不是回显入参）。
    assert detail(client, agent["id"])["display_name"] == "新名字"
    assert detail(client, agent["id"])["auto_claim"] is False
    assert [item["display_name"] for item in agents(client)] == ["新名字"]
    assert [item["auto_claim"] for item in agents(client)] == [False]
    # 响应形状与 `POST /api/agents` 同形：一次调用就能刷新详情、左栏与成员面板。
    assert body["members"] and body["agents"]


def test_partial_patch_leaves_the_other_fields_alone(client: TestClient) -> None:
    agent = create_agent(client, name="原名", role="原角色", model_id=LOCAL_MODEL_ID).json()["agent"]

    patch_agent(client, agent["id"], role="只改角色")

    current = detail(client, agent["id"])
    assert current["role"] == "只改角色"
    assert current["display_name"] == "原名"
    assert current["model_id"] == LOCAL_MODEL_ID


def test_invalid_model_id_rejects_the_whole_request_without_partial_write(client: TestClient) -> None:
    agent = create_agent(client, name="原名", role="原角色", model_id=LOCAL_MODEL_ID).json()["agent"]

    # 同一个请求里既有**合法**字段也有**非法**字段：合法的那个也不许落下（不得部分写入）。
    response = patch_agent(client, agent["id"], display_name="不该生效", role="不该生效", model_id="nope")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "model_id" in error["fields"]
    current = detail(client, agent["id"])
    assert current["display_name"] == "原名"
    assert current["role"] == "原角色"
    assert current["model_id"] == LOCAL_MODEL_ID


def test_patch_writes_agent_workspace_fields_and_reads_them_back(client: TestClient) -> None:
    """D14/D15 的四个 Agent 级字段有产品写路径：PATCH 后 GET/列表读回同一份持久化事实。"""
    agent = create_agent(client, name="工作区", model_id=LOCAL_MODEL_ID).json()["agent"]

    response = patch_agent(
        client,
        agent["id"],
        workspace_backend="docker",
        permission_mode="read-only",
        allow_local_shell=True,
        docker_image="registry.example/agent-runtime:1.2.3",
    )

    assert response.status_code == 200, response.json()
    written = response.json()["agent"]
    assert written["workspace_backend"] == "docker"
    assert written["permission_mode"] == "read-only"
    assert written["allow_local_shell"] is True
    assert written["docker_image"] == "registry.example/agent-runtime:1.2.3"

    current = detail(client, agent["id"])
    assert current["workspace_backend"] == "docker"
    assert current["permission_mode"] == "read-only"
    assert current["allow_local_shell"] is True
    assert current["docker_image"] == "registry.example/agent-runtime:1.2.3"
    listed = agents(client)[0]
    assert listed["workspace_backend"] == "docker"
    assert listed["docker_image"] == "registry.example/agent-runtime:1.2.3"

    # 空串是「显式清空镜像」（回到内置镜像），不是保留旧值；其它三项不动。
    cleared = patch_agent(client, agent["id"], docker_image="").json()["agent"]
    assert cleared["docker_image"] is None
    assert cleared["workspace_backend"] == "docker"
    assert cleared["allow_local_shell"] is True


def test_create_agent_accepts_the_same_workspace_fields(client: TestClient) -> None:
    """创建链路也写这四列（Agent 详情读面因此从第一轮起就有真实来源）。"""
    response = client.post(
        "/api/agents",
        json={
            "name": "新工作区",
            "role": "执行",
            "model_id": LOCAL_MODEL_ID,
            "system_prompt": "只做测试。",
            "workspace_backend": "local",
            "permission_mode": "trusted-writes",
            "allow_local_shell": True,
            "docker_image": "registry.example/agent-runtime:2.0.0",
        },
    )

    assert response.status_code == 201, response.json()
    agent = response.json()["agent"]
    assert agent["workspace_backend"] == "local"
    assert agent["permission_mode"] == "trusted-writes"
    assert agent["allow_local_shell"] is True
    assert agent["docker_image"] == "registry.example/agent-runtime:2.0.0"
    assert detail(client, agent["id"])["docker_image"] == "registry.example/agent-runtime:2.0.0"


def test_invalid_agent_workspace_values_reject_the_whole_request(client: TestClient) -> None:
    """非法后端 / 权限档 / 超长镜像整体拒绝：同一请求里的合法字段也**不写**。"""
    agent = create_agent(client, name="原名", role="原角色").json()["agent"]

    for invalid in (
        {"workspace_backend": "virtualbox"},
        {"permission_mode": "bypass"},
        {"docker_image": "x" * 256},
    ):
        response = patch_agent(client, agent["id"], role="不该写入", **invalid)
        assert response.status_code == 400, (invalid, response.json())
        assert response.json()["error"]["code"] == "validation_failed"
        assert set(response.json()["error"]["fields"]) == set(invalid)

    current = detail(client, agent["id"])
    assert current["role"] == "原角色"
    assert current["workspace_backend"] is None
    assert current["permission_mode"] is None


def test_unknown_and_null_fields_are_rejected_whole(client: TestClient) -> None:
    agent = create_agent(client, name="原名").json()["agent"]

    # 未知键整体拒绝（连同同一请求里的合法字段一起）——`auto_claim` 从 #65 起是可写字段，
    # 因此这里举一个本形态不存在的键。
    unknown = patch_agent(client, agent["id"], dedupe=True, display_name="不该生效")
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "validation_failed"

    # 显式 `null` 不是「没提到」：它既不是字段取值，也不是合法取值（`auto_claim` 同样是布尔配置）。
    for field in ("enabled", "auto_claim", "allow_local_shell", "workspace_backend", "docker_image"):
        explicit_null = patch_agent(client, agent["id"], **{field: None})
        assert explicit_null.status_code == 400
        assert "body" in explicit_null.json()["error"]["fields"]

    current = detail(client, agent["id"])
    assert current["display_name"] == "原名"
    assert current["enabled"] is True
    assert current["auto_claim"] is True


def test_docker_image_consent_endpoint_is_idempotent_and_validates_the_image(
    client: TestClient,
) -> None:
    """Docker 镜像确认有产品写路径：幂等、只存确认时间；空值/未知键整体拒绝。"""
    image = "registry.example/agent-runtime:1.2.3"

    first = client.post("/api/docker-images/consent", json={"image": image})
    assert first.status_code == 200, first.json()
    consent = first.json()["consent"]
    assert consent["scope_kind"] == "docker"
    assert consent["scope_id"] == image
    assert consent["acknowledged_at"]

    second = client.post("/api/docker-images/consent", json={"image": f"  {image}  "})
    assert second.status_code == 200, second.json()
    assert second.json()["consent"] == consent, "重复确认不覆盖首次时间"

    empty = client.post("/api/docker-images/consent", json={"image": "   "})
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "validation_failed"
    assert "image" in empty.json()["error"]["fields"]

    unknown = client.post("/api/docker-images/consent", json={"image": image, "scope": "x"})
    assert unknown.status_code == 400


def test_pause_writes_enabled_zero_with_a_readable_reason_and_resume_restores(client: TestClient) -> None:
    agent = create_agent(client).json()["agent"]
    assert detail(client, agent["id"])["status"] == "online"

    paused = patch_agent(client, agent["id"], enabled=False).json()["agent"]

    # 「暂停」不是新状态机：状态点仍是既有的四态里的**未启用**，原因由既有可用性判定给出。
    assert paused["enabled"] is False
    assert paused["status"] == "offline"
    assert paused["enabled_reason"]
    assert client.get(f"/api/agents/{agent['id']}/status").json()["status"] == "offline"
    assert client.get(f"/api/agents/{agent['id']}/status").json()["reason"] == paused["enabled_reason"]
    assert client.get("/api/member-statuses").json()["statuses"][agent["id"]] == "offline"

    resumed = patch_agent(client, agent["id"], enabled=True).json()["agent"]

    assert resumed["enabled"] is True
    assert resumed["status"] == "online"
    assert resumed["enabled_reason"] == ""


def test_model_change_to_a_remote_provider_reuses_the_data_flow_notice(
    client: TestClient, monkeypatch
) -> None:
    """改到远端 provider 沿用**与创建同一套**校验与告知语义：未确认 ⇒ 未启用 + 可读原因。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    agent = create_agent(client, model_id=LOCAL_MODEL_ID).json()["agent"]
    assert agent["is_remote"] is False

    changed = patch_agent(client, agent["id"], model_id=REMOTE_MODEL_ID).json()["agent"]

    assert changed["provider"] == "dashscope"
    assert changed["is_remote"] is True
    assert changed["remote_consent"] is False
    assert changed["status"] == "offline"
    assert changed["enabled_reason"]
    # 远端数据流告知的数据面把新目的地认了出来（告知面读的是库内配置，不是请求参数）。
    assert "dashscope" in client.get("/api/remote-data-flow").json()["destinations"]

    # 确认之后（同一套告知机制）状态点立刻跟着后端事实走。
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-not-a-real-key")
    client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})

    assert detail(client, agent["id"])["status"] == "online"


def test_paused_agent_is_still_woken_but_produces_no_reply(client: TestClient, harness: dict) -> None:
    """暂停的**已知代价**（ADR-0011）：仍会被唤醒、仍落一条可读的 `failed` 轮次结果，只是不产出回复。"""
    agent = create_agent(client).json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    patch_agent(client, agent["id"], enabled=False)
    calls_before = harness["seam"].call_count

    send(client, channel["id"], f"@{agent['display_name']} 还在吗")
    harness["scheduler"].drain()

    records = outcomes(client, channel["id"])
    assert [record["status"] for record in records] == ["failed"]
    assert records[0]["reason"]
    # 暂停**不是**「从唤醒面排除」：它照样走完一轮（有轮次结果），只是适配层根本没被调到。
    assert harness["seam"].call_count == calls_before
    assert messages(client, channel["id"]) == [] or all(
        message["kind"] == "human" for message in messages(client, channel["id"])
    )


def test_config_change_leaves_messages_outcomes_and_budget_untouched(launched: dict) -> None:
    """配置变更**不是发言**：人类世代、预算用量、消息与轮次结果三处读数前后逐字不变。"""
    client: TestClient = launched["client"]
    agent = create_agent(client).json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], f"@{agent['display_name']} 先说一句")
    launched["scheduler"].drain()

    def snapshot() -> tuple[list, list, dict]:
        """消息 / 轮次结果 / 预算三处读数。

        消息取「seq + kind + 正文 + 指针」而**不取** `author.display_name`：作者的显示名是读的时候
        从成员表联出来的（改名是一次合法的改名，不是对历史消息的改写），把它算进「消息读数」会让
        这个用例在证明「不落消息」的同时反对「允许改名」。
        """
        listed = messages(client, channel["id"])
        records = outcomes(client, channel["id"])
        return (
            [(item["seq"], item["kind"], item["body"], item["task_ref"]) for item in listed],
            [(record["id"], record["status"], record["reason"], record["base_seq"]) for record in records],
            budget(client, channel["id"]),
        )

    before = snapshot()
    assert before[0] and before[1]  # 先造出真实活动，再证明改配置不改变它

    for fields in (
        {"display_name": "改名后", "role": "新角色"},
        {"model_id": "ollama:qwen3", "system_prompt": "改了提示"},
        {"enabled": False},
        {"enabled": True},
    ):
        assert patch_agent(client, agent["id"], **fields).status_code == 200

    assert snapshot() == before


def test_config_change_survives_backend_restart(data_dir: Path) -> None:
    with backend(data_dir) as first:
        agent = create_agent(first, name="重启前").json()["agent"]
        first.patch(
            f"/api/agents/{agent['id']}",
            json={"display_name": "重启后", "role": "常驻角色", "enabled": False},
        )

    with backend(data_dir) as second:
        agent = agents(second)[0]
        assert agent["display_name"] == "重启后"
        assert agent["role"] == "常驻角色"
        assert agent["enabled"] is False
        assert agent["status"] == "offline"


def test_overview_numbers_are_derived_from_existing_facts(client: TestClient, harness: dict) -> None:
    """概览的四个数字各自能在既有数据面里找到来源（不是为填满界面编出来的值）。"""
    agent = create_agent(client).json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    fresh = detail(client, agent["id"])["stats"]
    assert fresh == {
        "channel_count": 1,
        "round_count": 0,
        "completed_task_count": 0,
        "success_rate": None,  # 没有样本 ⇒ 不编一个 0%
    }

    # 消息处理数 = 它参与过的轮次结果条数；成功率 = replied ÷ (replied + ignored + failed)。
    send(client, channel["id"], f"@{agent['display_name']} 处理一下")
    harness["scheduler"].drain()
    assert len(outcomes(client, channel["id"])) == 1

    stats = detail(client, agent["id"])["stats"]
    assert stats["round_count"] == 1
    assert stats["success_rate"] == 1.0

    # 完成任务数 = 指派给它且 status 为 done 的任务行：走既有的任务写路径把它做完。
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "验收清单"}).json()["task"]
    client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": agent["id"]})
    harness["scheduler"].drain()
    assert detail(client, agent["id"])["stats"]["completed_task_count"] == 0
    for status in ("in_progress", "in_review", "done"):
        assert client.patch(f"/api/tasks/{task['id']}/status", json={"status": status}).status_code == 200

    assert detail(client, agent["id"])["stats"]["completed_task_count"] == 1
    # 订阅频道数 = 它是成员的频道数（同一个频道，再加一个就变 2）。
    second = create_channel(client, name="第二个频道", member_ids=[agent["id"]]).json()["channel"]
    assert second["id"] != channel["id"]
    assert detail(client, agent["id"])["stats"]["channel_count"] == 2


def test_patch_of_a_missing_member_is_a_readable_404(client: TestClient) -> None:
    response = patch_agent(client, "mem_missing", display_name="谁")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["message"]


def test_there_is_still_no_delete_or_replace_route(client: TestClient) -> None:
    agent = create_agent(client).json()["agent"]

    assert client.delete(f"/api/agents/{agent['id']}").status_code == 404
    assert client.put(f"/api/agents/{agent['id']}", json={"role": "整体替换"}).status_code == 404
    assert detail(client, agent["id"])["role"] == agent["role"]
