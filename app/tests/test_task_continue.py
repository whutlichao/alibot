"""自动续跑（ADR-0027）的 HTTP 黑盒契约：受派者申请后，编排再唤醒它一轮。

用户可见的病（本票的来源）：认领轮回复一句「我会继续推进」，然后任务停在 ``in_progress``，
再也没有下一轮——正文里的承诺没有执行面。这里断言的是**有执行面**的那条路径：

1. 受派者在本轮提交 ``continue_task``（结构化动作）⇒ 编排落一条可读的 ``system`` 记录，
   并把**同一个人**再唤醒一轮；那一轮把整个任务线程（含它自己上一轮的回复）作为输入；
2. 不申请 ⇒ 不多跑一轮（对照读数，既有行为逐字不变）；
3. 每次人类世代内最多 ``TASK_WORK_ROUNDS_PER_EPOCH`` 轮；到达上限后不再投递，并落一条
   「已暂停」记录，不停在没人解释的静默里；
4. 续跑**不是跳**：``target_budget.used`` 一字不动。

只断言外部可观察行为：HTTP 读数、任务线程、``round_outcome`` 与 ``target_budget``；
``continue_task`` 的“模型会不会调”由脚本化替身固定（测试不联网、零成本）。
"""

from __future__ import annotations

from typing import Any, cast

from alibot.agent_seam import ContinueTaskAction, SetTaskStatusAction
from fastapi.testclient import TestClient

from tests.conftest import LOCAL_MODEL_ID, create_agent, create_channel, drain


def _db(client: TestClient) -> Any:
    """测试用的库句柄（``app.state.db``），与 ``test_tasks.py`` 同一个既有接缝。"""
    return cast(Any, client.app).state.db


def new_task(client: TestClient, channel_id: str, title: str = "整理验收清单") -> dict:
    response = client.post(f"/api/channels/{channel_id}/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["task"]


def thread(client: TestClient, task_id: str) -> list[dict]:
    return client.get(f"/api/tasks/{task_id}/messages").json()["messages"]


def task_budget(client: TestClient, task_id: str) -> dict:
    return client.get(f"/api/tasks/{task_id}/budget").json()["budget"]


def task_outcomes(client: TestClient, task_id: str) -> list[dict]:
    target_id = client.get(f"/api/tasks/{task_id}").json()["task"]["target_id"]
    return _db(client).list_round_outcomes(target_id)


def channel_with_agent_and_auto_claim(client: TestClient) -> tuple[dict, dict]:
    agent = create_agent(client, name="小明", model_id=LOCAL_MODEL_ID).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]
    claimed = client.patch(f"/api/agents/{agent['id']}", json={"auto_claim": True})
    assert claimed.status_code == 200, claimed.text
    return channel, claimed.json()["agent"]


def test_the_assignee_can_ask_for_auto_continue_and_is_woken_again(client: TestClient, harness: dict) -> None:
    """受派者申请续跑 ⇒ 第二轮真的发生，且它接得上自己的上一轮。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    # 认领轮：模型同时给出正文与「我还要继续」的结构化动作。
    harness["seam"].task_actions[agent["id"]] = ContinueTaskAction(task["id"])

    drain(harness)

    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 2, "认领轮申请续跑后必须再跑一轮"
    # 第一轮是认领；第二轮是自动续跑（来源不同、纪律同族：都是确定信号）。
    assert calls[1]["signal"] == "continue"
    assert calls[1]["mandatory_reply"] is True
    assert "自动续跑" in calls[1]["prompt"]

    records = thread(client, task["id"])
    # 续跑轮的输入是**整个任务线程**：创建 / 已认领 / 开工 / 自己上一轮的回复 / 续跑记录。
    assert calls[1]["seqs"] == [1, 2, 3, 4, 5]
    assert calls[1]["bodies"][3] == records[3]["body"]
    assert [message["kind"] for message in records] == [
        "system",
        "system",
        "system",
        "agent",
        "system",
        "agent",
    ]
    assert records[4]["body"] == "自动续跑：受派者申请继续推进这张任务"
    # 两轮都留在轮次结果里；第二轮的可读原因写明它由自动续跑唤醒。
    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["replied", "replied"]
    assert "自动续跑" in outcomes[1]["reason"]
    # 续跑不是 agent→agent 跳：预算用量一字不动。
    assert task_budget(client, task["id"])["used"] == 0


def test_a_task_stays_at_one_round_without_a_continue_request(client: TestClient, harness: dict) -> None:
    """对照读数：不申请就不多跑——既有行为逐字不变（正文里的承诺不触发任何东西）。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])

    drain(harness)

    assert len(harness["seam"].calls_for(agent["id"])) == 1
    assert [message["kind"] for message in thread(client, task["id"])] == [
        "system",
        "system",
        "system",
        "agent",
    ]
    assert [outcome["status"] for outcome in task_outcomes(client, task["id"])] == ["replied"]


def test_auto_continue_pauses_at_the_generation_limit(client: TestClient, harness: dict) -> None:
    """每次人类世代内最多 4 轮（认领轮 + 3 轮续跑）；到上限后落一条可读的暂停记录。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])

    # 每一轮都在收口时重新申请续跑：第 1 轮（认领）+ 3 轮续跑 = 4 轮，之后必须停。
    for _ in range(4):
        harness["seam"].task_actions[agent["id"]] = ContinueTaskAction(task["id"])
        drain(harness, limit=1)

    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 4
    assert [call["signal"] for call in calls[1:]] == ["continue", "continue", "continue"]
    assert harness["scheduler"].pending_count() == 0
    # 停在一条**可见**的事实上，而不是静默：线程最后一条是暂停记录，任务仍是进行中。
    records = thread(client, task["id"])
    assert records[-1]["kind"] == "system"
    assert "自动续跑已暂停" in records[-1]["body"]
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "in_progress"
    # 4 轮全部留了轮次结果；续跑仍未消耗任何跳数。
    assert len(task_outcomes(client, task["id"])) == 4
    assert task_budget(client, task["id"])["used"] == 0


def test_a_continuation_round_can_finish_the_work_instead_of_looping(
    client: TestClient, harness: dict
) -> None:
    """续跑轮改用 ``set_task_status`` 把任务推进到待审：不再有下一轮，任务离开进行中。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    harness["seam"].task_actions[agent["id"]] = ContinueTaskAction(task["id"])
    drain(harness, limit=1)  # 只跑认领轮：它会申请续跑，但下一轮还排着

    harness["seam"].task_actions[agent["id"]] = SetTaskStatusAction(task["id"], "in_review")
    drain(harness)

    assert len(harness["seam"].calls_for(agent["id"])) == 2
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "in_review"
    assert harness["scheduler"].pending_count() == 0
    assert "状态推进至 待审" in thread(client, task["id"])[-1]["body"]


def test_the_continue_action_is_not_offered_in_team_task_threads(client: TestClient, harness: dict) -> None:
    """team 任务线程只唤醒队长（ADR-0022）：自动续跑不给，提示里也不出现。"""
    leader = create_agent(client, name="队长", model_id=LOCAL_MODEL_ID).json()["agent"]
    member = create_agent(client, name="组员", model_id=LOCAL_MODEL_ID).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[leader["id"], member["id"]]).json()[
        "channel"
    ]
    configured = client.patch(
        f"/api/channels/{channel['id']}/config",
        json={"mode": "team", "team_leader_member_id": leader["id"]},
    )
    assert configured.status_code == 200, configured.text

    new_task(client, channel["id"])
    drain(harness)

    calls = harness["seam"].calls_for(leader["id"])
    assert calls, "建任务后队长应当被唤醒一轮"
    assert calls[-1]["task_context"].allow_continue is False
    assert "continue_task" not in calls[-1]["prompt"]
