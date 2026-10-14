from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, sse_frames
from tests.scripted import DELAY, IGNORE


def _db(client: TestClient):
    return cast(FastAPI, client.app).state.db


def test_create_dm_is_idempotent_and_contains_only_owner_and_peer(client: TestClient) -> None:
    agent = create_agent(client, name="小明").json()["agent"]

    first = client.post("/api/dms", json={"agent_id": agent["id"]})
    second = client.post("/api/dms", json={"agent_id": agent["id"]})

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["created"] is True
    assert second_payload["created"] is False
    assert first_payload["dm"]["id"] == second_payload["dm"]["id"]
    assert first_payload["dm"]["kind"] == "dm"

    # 精确成员集合是写路径的不变量，DM 没有公开成员列表接口；此处直接取证，不为测试添加新 API。
    members = _db(client).list_target_members(first_payload["dm"]["id"])
    assert [member["id"] for member in members] == ["owner", agent["id"]]
    assert len(client.get("/api/dms").json()["dms"]) == 1


def test_invalid_dm_peer_is_rejected_without_leaving_a_target(client: TestClient) -> None:
    # 被拒请求不能留下不可见的半成品 target；API 列表之外再核对表行数。
    before = _db(client)._conn.execute("SELECT COUNT(*) FROM target WHERE kind = 'dm'").fetchone()[0]

    for agent_id in ("agent_missing", "owner"):
        response = client.post("/api/dms", json={"agent_id": agent_id})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_failed"

    after = _db(client)._conn.execute("SELECT COUNT(*) FROM target WHERE kind = 'dm'").fetchone()[0]
    assert after == before == 0
    assert client.get("/api/dms").json()["dms"] == []


def test_dm_has_no_join_or_leave_endpoint(client: TestClient) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    dm = client.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]

    assert client.post(f"/api/dms/{dm['id']}/members", json={"member_id": agent["id"]}).status_code == 404
    assert client.delete(f"/api/dms/{dm['id']}/members").status_code == 404
    assert client.post(f"/api/dms/{dm['id']}/join").status_code == 404
    assert client.post(f"/api/dms/{dm['id']}/leave").status_code == 404


def test_dm_message_rejects_task_reference(client: TestClient) -> None:
    agent = create_agent(client, name="私信对象").json()["agent"]
    dm = client.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]

    response = client.post(
        f"/api/dms/{dm['id']}/messages", json={"body": "不是任务消息", "task_ref": "task_out_of_scope"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert client.get(f"/api/dms/{dm['id']}/messages").json()["messages"] == []


def test_owner_message_wakes_dm_peer_but_agent_reply_does_not_wake_anyone(
    client: TestClient, harness: dict
) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    outsider = create_agent(client, name="第三方").json()["agent"]
    harness["seam"].replies[agent["id"]] = f"已处理，@{outsider['display_name']}"
    dm = client.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]

    response = client.post(f"/api/dms/{dm['id']}/messages", json={"body": "请看一下"})

    assert response.status_code == 201, response.text
    assert response.json()["message"]["seq"] == 1
    assert harness["scheduler"].pending_count() == 1
    harness["scheduler"].drain()

    assert len(harness["seam"].calls_for(agent["id"])) == 1
    assert harness["seam"].calls_for(outsider["id"]) == []
    assert harness["scheduler"].pending_count() == 0
    messages = client.get(f"/api/dms/{dm['id']}/messages").json()["messages"]
    assert [message["kind"] for message in messages] == ["human", "agent"]


def test_dm_wake_is_independent_of_the_same_agent_round_on_another_target(
    client: TestClient, harness: dict
) -> None:
    import threading
    import time

    agent = create_agent(client, name="跨 target Agent").json()["agent"]
    channel = client.post(
        "/api/channels", json={"name": "频道", "description": "", "member_ids": [agent["id"]]}
    ).json()["channel"]
    dm = client.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]
    harness["seam"].behaviors[agent["id"]] = DELAY

    client.post(f"/api/channels/{channel['id']}/messages", json={"body": "频道任务"})
    harness["seam"].block()
    channel_runner = threading.Thread(target=harness["scheduler"].drain)
    channel_runner.start()
    dm_runner = None
    try:
        for _ in range(200):
            if harness["seam"].calls_for(agent["id"]):
                break
            time.sleep(0.01)
        assert harness["seam"].calls_for(agent["id"]), "频道轮次没有进入 in-flight"

        response = client.post(f"/api/dms/{dm['id']}/messages", json={"body": "私信任务"})
        assert response.status_code == 201, response.text
        dm_runner = threading.Thread(target=harness["scheduler"].drain)
        dm_runner.start()
        for _ in range(200):
            if len(harness["seam"].calls_for(agent["id"])) == 2 or not dm_runner.is_alive():
                break
            time.sleep(0.01)
        assert not dm_runner.is_alive(), "不同 target 的唤醒应等待当前轮次，不应并发执行"
        assert [call["target_id"] for call in harness["seam"].calls_for(agent["id"])] == [channel["id"]]
    finally:
        harness["seam"].release()
        channel_runner.join(timeout=10)
        if dm_runner is not None:
            dm_runner.join(timeout=10)

    assert not channel_runner.is_alive()
    assert dm_runner is not None and not dm_runner.is_alive()
    harness["scheduler"].drain()
    assert [call["target_id"] for call in harness["seam"].calls_for(agent["id"])] == [
        channel["id"],
        dm["id"],
    ]
    dm_messages = client.get(f"/api/dms/{dm['id']}/messages").json()["messages"]
    assert [message["kind"] for message in dm_messages] == ["human", "agent"]


def test_dm_mention_to_third_agent_is_delivery_only_and_counts_as_unread(
    client: TestClient, harness: dict
) -> None:
    peer = create_agent(client, name="私信对象").json()["agent"]
    outsider = create_agent(client, name="穿透对象").json()["agent"]
    harness["seam"].behaviors[peer["id"]] = IGNORE
    dm = client.post("/api/dms", json={"agent_id": peer["id"]}).json()["dm"]

    response = client.post(
        f"/api/dms/{dm['id']}/messages", json={"body": f"请 @{outsider['display_name']} 看一下"}
    )

    assert response.status_code == 201, response.text
    assert harness["scheduler"].pending_count() == 2
    harness["scheduler"].drain()

    outsider_calls = harness["seam"].calls_for(outsider["id"])
    assert len(outsider_calls) == 1
    assert outsider_calls[0]["mentioned"] is True
    assert harness["scheduler"].pending_count() == 0
    messages = client.get(f"/api/dms/{dm['id']}/messages").json()["messages"]
    assert [message["kind"] for message in messages] == ["human", "agent"]
    assert messages[-1]["author"]["id"] == outsider["id"]
    # DM 没有公开成员列表接口；直接核对结构不变量，避免为了测试暴露无需求的路由。
    members = _db(client).list_target_members(dm["id"])
    assert [member["id"] for member in members] == ["owner", peer["id"]]
    listed_dm = client.get("/api/dms").json()["dms"][0]
    assert listed_dm["unread_count"] == 1
    assert client.get("/api/workspace").json()["unread_total"] == 1


def test_dm_piercing_policy_does_not_change_existing_task_piercing(client: TestClient, harness: dict) -> None:
    agent = create_agent(client, name="任务穿透对象").json()["agent"]
    channel = create_channel(client, name="任务穿透回归").json()["channel"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "保留既有穿透行为"}).json()[
        "task"
    ]
    harness["seam"].replies[agent["id"]] = "任务穿透回复"

    response = client.post(
        f"/api/tasks/{task['id']}/messages",
        json={"body": f"请 @{agent['display_name']} 看一下", "task_ref": task["id"]},
    )

    assert response.status_code == 201, response.text
    assert harness["scheduler"].pending_count() == 1
    harness["scheduler"].drain()

    thread = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    assert [message["kind"] for message in thread] == ["system", "human", "system", "agent"]
    assert thread[2]["author"]["id"] == agent["id"]
    assert _db(client).is_target_member(task["target_id"], agent["id"])


def test_dm_event_stream_replays_messages_and_peer_status(live: dict) -> None:
    http = live["http"]
    peer = http.post(
        "/api/agents",
        json={"name": "私信对象", "role": "", "model_id": "ollama:qwen2.5", "system_prompt": ""},
    ).json()["agent"]
    live["seam"].replies[peer["id"]] = "私信回复"
    dm = http.post("/api/dms", json={"agent_id": peer["id"]}).json()["dm"]
    http.post(f"/api/dms/{dm['id']}/messages", json={"body": "请处理"})
    live["scheduler"].drain()

    events = sse_frames(http, dm["id"], after_seq=1, target_collection="dms")

    messages = [event for event in events if event["type"] == "message"]
    statuses = [event for event in events if event["type"] == "member_status"]
    assert [(event["seq"], event["message"]["body"]) for event in messages] == [(2, "私信回复")]
    assert [event["member_id"] for event in statuses] == [peer["id"]]
    assert all(event["type"] != "round_outcome" for event in events)


def test_dm_event_stream_subscribes_before_snapshot_and_deduplicates_boundary(
    live: dict, monkeypatch
) -> None:
    import json

    import httpx

    from alibot.service import WorkspaceService

    http = live["http"]
    peers = [
        http.post(
            "/api/agents",
            json={"name": name, "role": "", "model_id": "ollama:qwen2.5", "system_prompt": ""},
        ).json()["agent"]
        for name in ("快照前对象", "快照后对象")
    ]
    dms = [http.post("/api/dms", json={"agent_id": peer["id"]}).json()["dm"] for peer in peers]
    before_snapshot_dm, after_snapshot_dm = (dm["id"] for dm in dms)
    original_dm_messages = WorkspaceService.dm_messages
    injected: set[str] = set()

    def publish_at_snapshot_boundary(service, dm_id: str, after_seq: int = 0):
        if dm_id == before_snapshot_dm and dm_id not in injected:
            injected.add(dm_id)
            service.send_dm_message(dm_id, "快照已包含的边界消息")
        messages = original_dm_messages(service, dm_id, after_seq=after_seq)
        if dm_id == after_snapshot_dm and dm_id not in injected:
            injected.add(dm_id)
            service.send_dm_message(dm_id, "快照之后的边界消息")
        return messages

    monkeypatch.setattr(WorkspaceService, "dm_messages", publish_at_snapshot_boundary)

    def message_events(dm_id: str) -> list[dict]:
        events: list[dict] = []
        timeout = httpx.Timeout(2.0, read=2.0)
        with http.stream("GET", f"/api/dms/{dm_id}/events", timeout=timeout) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("data: "):
                    events.append(json.loads(text[len("data: ") :]))
                elif text.startswith(":"):
                    break
        return [event for event in events if event["type"] == "message"]

    included_in_snapshot = message_events(before_snapshot_dm)
    delivered_from_queue = message_events(after_snapshot_dm)

    assert [(event["seq"], event["message"]["body"]) for event in included_in_snapshot] == [
        (1, "快照已包含的边界消息")
    ]
    assert [(event["seq"], event["message"]["body"]) for event in delivered_from_queue] == [
        (1, "快照之后的边界消息")
    ]


def test_unknown_dm_event_stream_is_not_found(client: TestClient) -> None:
    response = client.get("/api/dms/dm_missing/events")

    assert response.status_code == 404
