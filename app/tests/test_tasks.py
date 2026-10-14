from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, drain, messages
from tests.scripted import REPLY


def test_creating_a_task_creates_its_own_thread_without_a_channel_message(client: TestClient) -> None:
    channel = create_channel(client, name="产品迭代").json()["channel"]

    created = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "整理验收清单"})

    assert created.status_code == 201
    task = created.json()["task"]
    assert task["channel_id"] == channel["id"]
    assert task["status"] == "todo"
    assert task["assignee_member_id"] is None
    assert created.json()["target"]["id"] == task["target_id"]
    assert created.json()["target"]["kind"] == "task"
    assert client.get("/api/tasks").json()["tasks"] == [task]
    assert messages(client, channel["id"]) == []

    thread = client.get(f"/api/tasks/{task['id']}/messages").json()
    assert [message["body"] for message in thread["messages"]] == ["任务已创建"]
    assert [message["kind"] for message in thread["messages"]] == ["system"]
    assert thread["messages"][0]["target_id"] == task["target_id"]


def test_invalid_task_creation_is_rejected_without_creating_a_task_or_channel_message(
    client: TestClient,
) -> None:
    channel = create_channel(client, name="产品迭代").json()["channel"]

    empty_title = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "   "})
    missing_channel = client.post("/api/channels/missing/tasks", json={"title": "不会落库"})

    assert empty_title.status_code == 400
    assert empty_title.json()["error"]["fields"]["title"] == "任务标题不能为空"
    assert missing_channel.status_code == 404
    assert client.get("/api/tasks").json()["tasks"] == []
    assert messages(client, channel["id"]) == []


def test_workspace_board_can_be_filtered_to_one_channel(client: TestClient) -> None:
    first_channel = create_channel(client, name="甲").json()["channel"]
    second_channel = create_channel(client, name="乙").json()["channel"]
    first_task = client.post(
        f"/api/channels/{first_channel['id']}/tasks", json={"title": "甲的任务"}
    ).json()["task"]
    client.post(f"/api/channels/{second_channel['id']}/tasks", json={"title": "乙的任务"})

    filtered = client.get("/api/tasks", params={"channel_id": first_channel["id"]})

    assert filtered.status_code == 200
    assert filtered.json()["tasks"] == [first_task]


def test_task_statuses_follow_the_five_state_machine_and_closed_is_terminal(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    task = client.post(
        f"/api/channels/{channel['id']}/tasks", json={"title": "验证状态迁移"}
    ).json()["task"]
    path = f"/api/tasks/{task['id']}/status"

    for status in ("in_progress", "in_review", "done", "in_progress", "closed"):
        response = client.patch(path, json={"status": status})
        assert response.status_code == 200
        assert response.json()["task"]["status"] == status

    rejected = client.patch(path, json={"status": "todo"})

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "validation_failed"
    assert "closed" in rejected.json()["error"]["message"]
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "closed"


def test_task_reference_is_renderable_in_its_own_target_and_rejected_across_targets(
    client: TestClient,
) -> None:
    channel = create_channel(client).json()["channel"]
    task = client.post(
        f"/api/channels/{channel['id']}/tasks", json={"title": "指针消息"}
    ).json()["task"]

    same_target = client.post(
        f"/api/tasks/{task['id']}/messages",
        json={"body": "讨论这项任务", "task_ref": task["id"]},
    )
    cross_target = client.post(
        f"/api/channels/{channel['id']}/messages",
        json={"body": "频道不能跨 target 指向任务", "task_ref": task["id"]},
    )

    assert same_target.status_code == 201
    assert same_target.json()["message"]["task_ref"] == task["id"]
    assert same_target.json()["message"]["target_id"] == task["target_id"]
    assert cross_target.status_code == 400
    assert cross_target.json()["error"]["code"] == "validation_failed"
    assert messages(client, channel["id"]) == []
    assert [message["task_ref"] for message in client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]] == [
        None,
        task["id"],
    ]


def test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    task = client.post(
        f"/api/channels/{channel['id']}/tasks", json={"title": "事件流"}
    ).json()["task"]

    with client.stream("GET", f"/api/tasks/{task['id']}/events", params={"after_seq": 0}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = iter(response.iter_lines())
        first = next(lines)
        while first and not first.startswith("data: "):
            first = next(lines)
        assert first is not None
        assert '"type":"message"' in first
        assert '"body":"任务已创建"' in first

    missing = client.get("/api/tasks/missing/events")
    assert missing.status_code == 404


def test_assignment_wakes_only_the_assignee_and_reassignment_moves_the_task_member(
    client: TestClient, harness: dict
) -> None:
    channel = create_channel(client).json()["channel"]
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    task = client.post(
        f"/api/channels/{channel['id']}/tasks", json={"title": "需要认领"}
    ).json()["task"]
    path = f"/api/tasks/{task['id']}/assignee"

    assigned = client.patch(path, json={"member_id": first["id"]})

    assert assigned.status_code == 200
    assert assigned.json()["task"]["assignee_member_id"] == first["id"]
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(first["id"])) == 1

    reassigned = client.patch(path, json={"member_id": second["id"]})

    assert reassigned.status_code == 200
    assert reassigned.json()["task"]["assignee_member_id"] == second["id"]
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(second["id"])) == 1
    assert [member["id"] for member in client.get(f"/api/tasks/{task['id']}/members").json()["members"]] == [
        "owner",
        second["id"],
    ]

    unassigned = client.patch(path, json={"member_id": None})

    assert unassigned.status_code == 200
    assert unassigned.json()["task"]["assignee_member_id"] is None
    assert harness["scheduler"].pending_count() == 0


def test_task_unread_is_separate_and_ordinary_system_events_do_not_wake_or_spend_budget(
    client: TestClient, harness: dict
) -> None:
    channel = create_channel(client).json()["channel"]
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors = {agent["id"]: REPLY}
    harness["seam"].replies = {agent["id"]: "我已接手"}
    task = client.post(
        f"/api/channels/{channel['id']}/tasks", json={"title": "检查未读边界"}
    ).json()["task"]
    assert harness["scheduler"].pending_count() == 0

    assignment = client.patch(
        f"/api/tasks/{task['id']}/assignee", json={"member_id": agent["id"]}
    ).json()
    assignment_seq = assignment["message"]["seq"]
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    task_after_reply = client.get(f"/api/tasks/{task['id']}").json()["task"]
    channel_after_reply = client.get("/api/channels").json()["channels"][0]
    workspace_after_reply = client.get("/api/workspace").json()
    assert len(harness["seam"].calls_for(agent["id"])) == 1
    assert task_after_reply["unread_count"] == 1
    assert channel_after_reply["unread_count"] == 0
    assert workspace_after_reply["unread_total"] == 0

    messages_in_thread = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    client.post(f"/api/tasks/{task['id']}/read", json={"seq": messages_in_thread[-1]["seq"]})
    before = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]
    assert before["epoch_seq"] == assignment_seq
    assert before["used"] == 0

    status_change = client.patch(
        f"/api/tasks/{task['id']}/status", json={"status": "in_progress"}
    )
    title_change = client.patch(
        f"/api/tasks/{task['id']}/title", json={"title": "核对完成"}
    )

    assert status_change.status_code == 200
    assert title_change.status_code == 200
    assert harness["scheduler"].pending_count() == 0
    assert len(harness["seam"].calls_for(agent["id"])) == 1
    after = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]
    task_after_events = client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert after == before
    assert task_after_events["unread_count"] == 0
    assert client.get("/api/channels").json()["channels"][0]["unread_count"] == 0
    assert client.get("/api/workspace").json()["unread_total"] == 0
