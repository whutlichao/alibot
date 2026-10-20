from __future__ import annotations

from itertools import pairwise
from typing import Any, cast

from alibot.agent_seam import SetTaskStatusAction, TransferTaskAction
from alibot.db import BUDGET_LIMIT, TASK_STATUS_TRANSITIONS, TASK_STATUSES
from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, drain, messages
from tests.scripted import ACTION_ONLY, REPLY


def _db(client: TestClient) -> Any:
    """测试用的库句柄（``app.state.db``）。

    ``TestClient.app`` 在 Starlette 的类型里是 ASGI 包装器，真正的 app 对象仍是我们传给
    ``create_app`` 的那一个——``app.state.db`` 是它的公开接缝。因此这里只把类型放宽，
    不另开一条读库路径（生产代码没有额外暴露面）。
    """
    return cast(Any, client.app).state.db


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

    # 任务本体（标题）随第一条 `system` 记录进消息流（S4）：人读线程能读懂，Agent 读消息也能读懂。
    thread = client.get(f"/api/tasks/{task['id']}/messages").json()
    assert [message["body"] for message in thread["messages"]] == ["任务已创建：整理验收清单"]
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
    first_task = client.post(f"/api/channels/{first_channel['id']}/tasks", json={"title": "甲的任务"}).json()[
        "task"
    ]
    client.post(f"/api/channels/{second_channel['id']}/tasks", json={"title": "乙的任务"})

    filtered = client.get("/api/tasks", params={"channel_id": first_channel["id"]})

    assert filtered.status_code == 200
    assert filtered.json()["tasks"] == [first_task]


def test_task_statuses_follow_the_five_state_machine_and_closed_is_terminal(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "验证状态迁移"}).json()["task"]
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


def _expected_allowed(status: str) -> list[str]:
    """``allowed_transitions`` 的期望值：从迁移表按 ``TASK_STATUSES`` 顺序派生。

    这里**不钉**五态灯格的任何字面量——表换了两边一起变；断言的是「载荷与写路径同源」这条
    事实，而不是某份副本的形状。
    """
    return [target for target in TASK_STATUSES if target in TASK_STATUS_TRANSITIONS[status]]


def _path_from_todo(status: str) -> list[str]:
    """沿迁移表从 ``todo`` 走到 ``status`` 的最短路径（写路径只接受表内的边）。"""
    if status == "todo":
        return []
    queue: list[list[str]] = [["todo"]]
    seen = {"todo"}
    while queue:
        path = queue.pop(0)
        for target in _expected_allowed(path[-1]):
            if target == status:
                return [*path[1:], target]
            if target in seen:
                continue
            seen.add(target)
            queue.append([*path, target])
    raise AssertionError(f"迁移表里从 todo 走不到 {status}")


def _task_in_status(client: TestClient, channel_id: str, status: str) -> dict[str, Any]:
    """建一张任务并只用写路径把它推到 ``status``（不直接改库，走的是同一条入口）。"""
    created = client.post(f"/api/channels/{channel_id}/tasks", json={"title": f"载荷契约 {status}"})
    task = created.json()["task"]
    for step in _path_from_todo(status):
        moved = client.patch(f"/api/tasks/{task['id']}/status", json={"status": step})
        assert moved.status_code == 200, moved.json()
        task = moved.json()["task"]
    assert task["status"] == status
    return task


def test_task_payload_carries_allowed_transitions_on_every_read_path(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    for status in TASK_STATUSES:
        task = _task_in_status(client, channel["id"], status)
        expected = _expected_allowed(status)
        assert task["allowed_transitions"] == expected

        # 读路径一：全量看板；读路径二：单条任务。
        board = client.get("/api/tasks").json()["tasks"]
        assert [item["allowed_transitions"] for item in board if item["id"] == task["id"]] == [expected]
        single = client.get(f"/api/tasks/{task['id']}").json()["task"]
        assert single["allowed_transitions"] == expected
        # 频道筛选是同一读路径的另一条入口（`?channel_id=`），不能有两套形状。
        filtered = client.get("/api/tasks", params={"channel_id": channel["id"]}).json()["tasks"]
        assert [item["allowed_transitions"] for item in filtered if item["id"] == task["id"]] == [expected]

        # 另外两个 PATCH 的 `task` 也带这个字段（改名 / 改派都不改状态集合）。
        retitled = client.patch(f"/api/tasks/{task['id']}/title", json={"title": f"改名 {status}"})
        assert retitled.status_code == 200
        assert retitled.json()["task"]["allowed_transitions"] == expected
        unassigned = client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": None})
        assert unassigned.status_code == 200
        assert unassigned.json()["task"]["allowed_transitions"] == expected


def test_allowed_transitions_are_exactly_what_the_write_path_accepts(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    for status in TASK_STATUSES:
        allowed = _expected_allowed(status)
        for target in TASK_STATUSES:
            if target == status:
                continue
            task = _task_in_status(client, channel["id"], status)
            status_path = f"/api/tasks/{task['id']}/status"

            response = client.patch(status_path, json={"status": target})

            if target in allowed:
                assert response.status_code == 200, (status, target, response.json())
                assert response.json()["task"]["status"] == target
                assert response.json()["task"]["allowed_transitions"] == _expected_allowed(target)
            else:
                assert response.status_code == 400, (status, target, response.json())
                assert response.json()["error"]["code"] == "validation_failed"
                # 被拒绝的写不改变后端事实：报表里仍是原状态与原集合。
                unchanged = client.get(f"/api/tasks/{task['id']}").json()["task"]
                assert unchanged["status"] == status
                assert unchanged["allowed_transitions"] == allowed


def test_task_reference_is_renderable_in_its_own_target_and_rejected_across_targets(
    client: TestClient,
) -> None:
    channel = create_channel(client).json()["channel"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "指针消息"}).json()["task"]

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
    assert [
        message["task_ref"] for message in client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    ] == [
        None,
        task["id"],
    ]


def test_task_event_stream_replays_thread_messages_and_rejects_missing_tasks(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "事件流"}).json()["task"]

    with client.stream("GET", f"/api/tasks/{task['id']}/events", params={"after_seq": 0}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = iter(response.iter_lines())
        first = next(lines)
        while first and not first.startswith("data: "):
            first = next(lines)
        assert first is not None
        assert '"type":"message"' in first
        assert '"body":"任务已创建：事件流"' in first

    missing = client.get("/api/tasks/missing/events")
    assert missing.status_code == 404


def test_assignment_wakes_only_the_assignee_and_reassignment_moves_the_task_member(
    client: TestClient, harness: dict
) -> None:
    channel = create_channel(client).json()["channel"]
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "需要认领"}).json()["task"]
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
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "检查未读边界"}).json()["task"]
    assert harness["scheduler"].pending_count() == 0

    assignment = client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": agent["id"]}).json()
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

    status_change = client.patch(f"/api/tasks/{task['id']}/status", json={"status": "in_progress"})
    title_change = client.patch(f"/api/tasks/{task['id']}/title", json={"title": "核对完成"})

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

    thread_messages = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    assert [message["body"] for message in thread_messages[-2:]] == [
        "状态推进至 进行中",
        "标题已更改为：核对完成",
    ]
    assert [message["kind"] for message in thread_messages[-2:]] == ["system", "system"]

    event_path = f"/api/tasks/{task['id']}/messages/{thread_messages[-1]['seq']}"
    for method in ("put", "patch", "delete"):
        request = getattr(client, method)
        if method == "delete":
            response = request(event_path)
        else:
            response = request(event_path, json={"body": "改写事件"})
        assert response.status_code == 404
    assert client.get(f"/api/tasks/{task['id']}/messages").json()["messages"] == thread_messages


def test_agent_reassignment_spends_one_jump_and_obeys_the_budget_limit(
    client: TestClient, harness: dict
) -> None:
    channel = create_channel(client).json()["channel"]
    agents = [create_agent(client, name=name).json()["agent"] for name in ("甲", "乙", "丙", "丁", "戊")]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "逐次转派"}).json()["task"]
    assignee_path = f"/api/tasks/{task['id']}/assignee"
    harness["seam"].task_actions = {
        agent["id"]: TransferTaskAction(task["id"], agents[index + 1]["id"])
        for index, agent in enumerate(agents[:-1])
    }

    owner_assignment = client.patch(assignee_path, json={"member_id": agents[0]["id"]}).json()
    epoch_seq = owner_assignment["message"]["seq"]
    assert client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]["used"] == 0
    assert harness["scheduler"].pending_count() == 1

    spoofed_actor = client.patch(
        assignee_path,
        json={"member_id": agents[1]["id"], "actor_member_id": agents[0]["id"]},
    )
    assert spoofed_actor.status_code == 400
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["assignee_member_id"] == agents[0]["id"]

    for hop, (actor, recipient) in enumerate(pairwise(agents), start=1):
        assert harness["scheduler"].pending_count() == 1
        assert len(harness["scheduler"].drain(limit=1)) == 1
        assert client.get(f"/api/tasks/{task['id']}").json()["task"]["assignee_member_id"] == recipient["id"]
        budget = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]
        assert budget["epoch_seq"] == epoch_seq
        assert budget["used"] == min(hop, BUDGET_LIMIT)
        assert len(harness["seam"].calls_for(actor["id"])) == 1
        if hop <= BUDGET_LIMIT:
            assert harness["scheduler"].pending_count() == 1
            assert harness["seam"].calls_for(recipient["id"]) == []
        else:
            assert harness["scheduler"].pending_count() == 0
            assert harness["seam"].calls_for(recipient["id"]) == []

    owner_reassignment = client.patch(assignee_path, json={"member_id": agents[0]["id"]}).json()
    reset_budget = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]
    assert reset_budget["epoch_seq"] == owner_reassignment["message"]["seq"]
    assert reset_budget["epoch_seq"] > epoch_seq
    assert reset_budget["used"] == 0
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(agents[0]["id"])) == 2


def test_mentioned_agent_can_complete_a_task_action_without_chat_text(
    client: TestClient, harness: dict
) -> None:
    channel = create_channel(client).json()["channel"]
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "点名后转派"}).json()["task"]
    assignee_path = f"/api/tasks/{task['id']}/assignee"
    assert client.patch(assignee_path, json={"member_id": first["id"]}).status_code == 200
    drain(harness)

    harness["seam"].behaviors[first["id"]] = ACTION_ONLY
    harness["seam"].task_actions[first["id"]] = TransferTaskAction(task["id"], second["id"])
    mentioned = client.post(
        f"/api/tasks/{task['id']}/messages",
        json={"body": f"@{first['display_name']} 请转派", "task_ref": task["id"]},
    )
    assert mentioned.status_code == 201
    assert harness["scheduler"].pending_count() == 1

    assert len(harness["scheduler"].drain(limit=1)) == 1

    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["assignee_member_id"] == second["id"]
    assert harness["scheduler"].pending_count() == 1
    assert harness["seam"].calls_for(second["id"]) == []
    agent_outcome = next(
        outcome
        for outcome in reversed(_db(client).list_round_outcomes(task["target_id"]))
        if outcome["member_id"] == first["id"]
    )
    assert agent_outcome["status"] == "replied"
    assert agent_outcome["reply_seq"] is None


def test_assigned_agent_cannot_approve_own_task_but_owner_can(client: TestClient, harness: dict) -> None:
    channel = create_channel(client).json()["channel"]
    agent = create_agent(client, name="审阅者").json()["agent"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "待审任务"}).json()["task"]

    status_path = f"/api/tasks/{task['id']}/status"
    assert client.patch(status_path, json={"status": "in_progress"}).status_code == 200
    assert client.patch(status_path, json={"status": "in_review"}).status_code == 200
    harness["seam"].task_actions[agent["id"]] = SetTaskStatusAction(task["id"], "done")

    client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": agent["id"]})
    drain(harness)
    task_messages = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "in_review"
    assert not any(message["body"] == "状态推进至 完成" for message in task_messages)
    assert len(harness["seam"].calls_for(agent["id"])) == 1
    agent_outcome = next(
        outcome
        for outcome in _db(client).list_round_outcomes(task["target_id"])
        if outcome["member_id"] == agent["id"]
    )
    assert "不能批准" in agent_outcome["reason"]

    owner_approval = client.patch(status_path, json={"status": "done"})
    assert owner_approval.status_code == 200
    assert owner_approval.json()["task"]["status"] == "done"


def test_other_task_member_can_review_assignee_output(client: TestClient, harness: dict) -> None:
    channel = create_channel(client).json()["channel"]
    assignee = create_agent(client, name="实现者").json()["agent"]
    reviewer = create_agent(client, name="复核者").json()["agent"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "独立复核"}).json()["task"]
    status_path = f"/api/tasks/{task['id']}/status"
    assert client.patch(status_path, json={"status": "in_progress"}).status_code == 200
    assert client.patch(status_path, json={"status": "in_review"}).status_code == 200
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": assignee["id"]}).status_code
        == 200
    )
    drain(harness)

    harness["seam"].task_actions[reviewer["id"]] = SetTaskStatusAction(task["id"], "done")
    review_request = client.post(
        f"/api/tasks/{task['id']}/messages",
        json={"body": f"@{reviewer['display_name']} 请复核", "task_ref": task["id"]},
    )
    assert review_request.status_code == 201
    drain(harness)

    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "done"
    task_messages = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    assert sum(message["body"] == "状态推进至 完成" for message in task_messages) == 1
    assert reviewer["id"] in {
        member["id"] for member in client.get(f"/api/tasks/{task['id']}/members").json()["members"]
    }
