"""#145 team 任务线程：只唤醒队长、TeamAssign 可用、五态仍由既有工具维护。

测试面沿用仓库既有接缝：

- HTTP 黑盒（任务线程消息、看板 ``GET /api/tasks``、改派/状态端点、预算只读面）；
- ``ManualScheduler`` + ``ScriptedAgentSeam``（唤醒集合与结构化任务动作）；
- #143 的进程内脚本模型 + 真实 ``AgentScopeAgentSeam``（HTTP 一轮之后
  ``team_assignment`` / 预算 / 任务行三者的对账）。不另起第二套仿真器。

立论（ADR-0022 / 规格 §3.3 / §3.5 / §4.2）：

- team 覆盖该频道的 channel 型 target 与**全部 task target**；任务线程只唤醒队长、不穿透；
- owner 改派 / Agent 转派只唤醒队长，不改 ``claimed_at``；
- ``claim_new_task()`` 在 team 频道返回 0；**建任务**改走 ``notify_task_created``：只唤醒队长一轮
  （``mandatory_reply=True``），仍不认领、不改任务行、不写分派、不扣跳（#179 A）；
- ``TeamAssign`` 在 task target 记 ``task_id``，不碰任务行、不写任务变更 ``system``；
- 任务五态仍由 owner HTTP 与队长轮次的结构化工具维护；队长不是受派者时可批准
  ``in_review → done``，队长是受派者时只能由 owner 批准。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from alibot.agent_seam import SetTaskStatusAction, TransferTaskAction
from alibot.db import open_database
from alibot.team import member_internal_name
from fastapi.testclient import TestClient

from tests.conftest import (
    LOCAL_MODEL_ID,
    ManualScheduler,
    backend,
    create_agent,
    create_channel,
    drain,
)
from tests.scripted import ACTION_ONLY, EMPTY_REPLY, IGNORE, SILENT
from tests.test_team_budget import _BudgetSeam, _ScriptedModel, _text, _tool_calls


def _db(client: TestClient) -> Any:
    return cast(Any, client.app).state.db


def _orchestrator(client: TestClient) -> Any:
    return cast(Any, client.app).state.orchestrator


def _make_team(client: TestClient, leader: str, *members: str, name: str = "团队频道") -> dict[str, Any]:
    channel = create_channel(client, name=name, member_ids=[leader, *members]).json()["channel"]
    response = client.patch(
        f"/api/channels/{channel['id']}/config",
        json={"mode": "team", "team_leader_member_id": leader},
    )
    assert response.status_code == 200, response.json()
    return channel


def _create_task(client: TestClient, channel_id: str, title: str = "团队任务") -> dict[str, Any]:
    created = client.post(f"/api/channels/{channel_id}/tasks", json={"title": title})
    assert created.status_code == 201, created.json()
    return created.json()["task"]


def _task(client: TestClient, task_id: str) -> dict[str, Any]:
    return client.get(f"/api/tasks/{task_id}").json()["task"]


def _thread(client: TestClient, task_id: str) -> list[dict[str, Any]]:
    return client.get(f"/api/tasks/{task_id}/messages").json()["messages"]


def _task_member_ids(client: TestClient, task_id: str) -> list[str]:
    members = client.get(f"/api/tasks/{task_id}/members").json()["members"]
    return [member["id"] for member in members]


# ─── 只唤醒队长、不穿透、非队长受派者无轮次 ─────────────────────────────
def test_non_leader_assignee_gets_no_task_round_and_no_piercing(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="受派者").json()["agent"]
    outsider = create_agent(client, name="外援").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = _create_task(client, channel["id"])
    target_id = task["target_id"]
    assert task["status"] == "todo"
    assert task["claimed_at"] is None
    # 建任务只唤醒队长一轮（#179 A）：不认领、不唤醒成员。
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(leader["id"])) == 1
    assert harness["seam"].calls_for(member["id"]) == []
    assert _task(client, task["id"])["claimed_at"] is None

    # owner 改派：只唤醒队长；任务行按既有规则改指派人，但 claimed_at 不被改写。
    assigned = client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": member["id"]})
    assert assigned.status_code == 200
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    after_assignment = _task(client, task["id"])
    assert after_assignment["assignee_member_id"] == member["id"]
    assert after_assignment["claimed_at"] is None, "改派不是认领"
    assert after_assignment["status"] == "todo"
    assert len(harness["seam"].calls_for(leader["id"])) == 2
    assert harness["seam"].calls_for(member["id"]) == []

    # 任务线程的人类消息：只唤醒队长；被 @ 的受派者与外援都不穿透。
    sent = client.post(
        f"/api/tasks/{task['id']}/messages",
        json={
            "body": f"@{member['display_name']} @{outsider['display_name']} 请同步进度",
            "task_ref": task["id"],
        },
    )
    assert sent.status_code == 201
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    assert harness["seam"].calls_for(member["id"]) == []
    assert harness["seam"].calls_for(outsider["id"]) == []
    leader_calls = harness["seam"].calls_for(leader["id"])
    assert len(leader_calls) == 3  # 建任务 + owner 改派 + 人类消息
    assert all(call["target_id"] == target_id for call in leader_calls)
    assert all(call["team"] is not None for call in leader_calls)

    task_outcomes = _db(client).list_round_outcomes(target_id)
    assert [row["member_id"] for row in task_outcomes] == [leader["id"]] * 3
    assert _task(client, task["id"])["claimed_at"] is None


def test_agent_transfer_only_wakes_the_leader_and_never_claims(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    source = create_agent(client, name="原受派者").json()["agent"]
    recipient = create_agent(client, name="新受派者").json()["agent"]
    channel = _make_team(client, leader["id"], source["id"], recipient["id"])
    task = _create_task(client, channel["id"], title="转派")
    drain(harness)  # 建任务唤醒队长那一轮（#179 A）
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": source["id"]}).status_code == 200
    )
    drain(harness)
    assert _task(client, task["id"])["claimed_at"] is None

    _, transfer_message = _db(client).update_task_assignee(
        task["id"], recipient["id"], actor_member_id=source["id"]
    )
    assert transfer_message is not None
    assert (
        _orchestrator(client).notify_task_reassignment(
            task["target_id"], transfer_message["seq"], recipient["id"], actor_member_id=source["id"]
        )
        == 1
    )
    drain(harness)

    after = _task(client, task["id"])
    assert after["assignee_member_id"] == recipient["id"]
    assert after["claimed_at"] is None, "Agent 转派不是认领"
    assert after["status"] == "todo"
    assert len(harness["seam"].calls_for(leader["id"])) == 3  # 建任务 + owner 改派 + Agent 转派
    assert harness["seam"].calls_for(recipient["id"]) == []


def test_team_channel_creation_never_claims_even_with_auto_claim(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="候选").json()["agent"]
    assert client.patch(f"/api/agents/{member['id']}", json={"auto_claim": True}).status_code == 200
    channel = _make_team(client, leader["id"], member["id"])

    task = _create_task(client, channel["id"], title="不认领")

    assert task["status"] == "todo"
    assert task["claimed_at"] is None
    # 待跑的是**队长**那一轮（建任务唤醒），不是候选成员的认领轮。
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(leader["id"])) == 1
    assert _orchestrator(client).claim_new_task(task["id"], channel["id"]) == 0
    assert _task(client, task["id"])["claimed_at"] is None
    assert harness["seam"].calls_for(member["id"]) == []


# ─── #179 A：建任务唤醒队长（仍不认领） ──────────────────────────────────


def test_creating_a_team_task_wakes_only_the_leader_with_a_mandatory_round(
    client: TestClient, harness: dict
) -> None:
    """建任务 ⇒ 队长**一轮**必答；任务行四字段 / 分派记录 / 预算三样都不变。

    人类动作不是 agent→agent 跳（ADR-0005 / ADR-0025）：投递本身不扣 `target_budget.used`，
    也不产生 `team_assignment`——分派只在队长真的调 `TeamAssign` 时才发生。
    """
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = _create_task(client, channel["id"], title="建任务唤醒")
    target_id = task["target_id"]
    db = _db(client)
    budget_before = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]

    # 待跑轮次 = 1，且**只属队长**：drain 之后只有队长接到了这一轮。
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert harness["scheduler"].pending_count() == 0
    leader_calls = harness["seam"].calls_for(leader["id"])
    assert len(leader_calls) == 1
    assert harness["seam"].calls_for(member["id"]) == []
    call = leader_calls[0]
    assert call["target_id"] == target_id
    assert call["mandatory_reply"] is True, "建任务那一轮是确定信号（#179 B 起 team 任务变化都必答）"
    assert call["team"] is not None, "这一轮是队长轮次"

    # 轮次里队长能看到「任务已创建：<标题>」——from_seq 取那条 system 消息的 seq。
    assert ["任务已创建：建任务唤醒" in body for body in call["bodies"]] == [True]

    # 任务行四字段原样：仍待认领、仍无指派人。
    after = _task(client, task["id"])
    for key in ("status", "assignee_member_id", "claimed_at", "updated_at"):
        assert after[key] == task[key], key
    assert after["status"] == "todo"
    assert after["assignee_member_id"] is None

    # 不写分派记录、不扣跳：分派只在队长真调 TeamAssign 时才发生。
    assert db.list_team_assignments(target_id) == []
    assert client.get(f"/api/tasks/{task['id']}/budget").json()["budget"] == budget_before
    assert client.get(f"/api/channels/{channel['id']}/budget").json()["budget"]["used"] == 0


def test_creating_a_team_task_with_an_unavailable_leader_wakes_nobody(
    client: TestClient, harness: dict
) -> None:
    """队长被停用 ⇒ 建任务零唤醒，并按 D18 同口径落一条 ``invalid-leader``。"""
    leader = create_agent(client, name="队长").json()["agent"]
    channel = _make_team(client, leader["id"])
    assert client.patch(f"/api/agents/{leader['id']}", json={"enabled": False}).status_code == 200

    task = _create_task(client, channel["id"], title="队长停用")

    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].call_count == 0
    runs = _db(client).list_team_runs(task["target_id"])
    assert [run["status"] for run in runs] == ["invalid-leader"]
    assert runs[0]["leader_member_id"] == leader["id"]
    # 仍然是「建任务不认领」：任务行不动。
    assert _task(client, task["id"])["claimed_at"] is None


def test_owner_reassignment_in_a_team_task_is_a_mandatory_round_for_the_leader(
    client: TestClient, harness: dict
) -> None:
    """#179 B：改派只唤醒队长，且那一轮是**确定信号**；受派者仍无轮次。"""
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = _create_task(client, channel["id"], title="改派必答")
    drain(harness)  # 建任务那一轮（#179 A）

    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": member["id"]}).status_code == 200
    )
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    call = harness["seam"].calls_for(leader["id"])[-1]
    assert call["mandatory_reply"] is True
    assert harness["seam"].calls_for(member["id"]) == []


def test_a_silent_leader_on_a_task_change_round_is_recorded_as_failed(
    client: TestClient, harness: dict
) -> None:
    """队长在被任务变化唤醒的轮次里沉默 ⇒ ``failed``（确定信号），不落 ``silent``。"""
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    harness["seam"].behaviors[leader["id"]] = SILENT

    task = _create_task(client, channel["id"], title="队长沉默")
    drain(harness)

    outcomes = _db(client).list_round_outcomes(task["target_id"])
    assert [outcome["status"] for outcome in outcomes] == ["failed"]
    assert outcomes[0]["member_id"] == leader["id"]
    # 沉默在确定信号下的收口不是「它不想说」：诊断不能把它写成认领（team 频道不认领）。
    assert "沉默" in outcomes[0]["reason"]
    # 新词条（ADR-0025 §六）：写「任务变化」，不得写「认领」——后者是一件在 team 频道没发生过的事。
    assert "任务变化" in outcomes[0]["reason"]
    assert "认领" not in outcomes[0]["reason"]


def test_an_ignored_leader_on_a_reassignment_round_uses_the_task_change_wording(
    client: TestClient, harness: dict
) -> None:
    """改派轮也是「队长被任务变化唤醒」：自判忽略按 `failed` 收口，且文案不牵连认领。"""
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = _create_task(client, channel["id"], title="改派沉默")
    drain(harness)  # 建任务那一轮（#179 A）正常回应

    harness["seam"].behaviors[leader["id"]] = IGNORE
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": member["id"]}).status_code == 200
    )
    drain(harness)

    outcome = _db(client).list_round_outcomes(task["target_id"])[-1]
    assert outcome["status"] == "failed"
    assert "任务变化" in outcome["reason"]
    assert "认领" not in outcome["reason"]


def test_a_body_less_leader_reply_on_a_task_change_round_uses_the_task_change_wording(
    client: TestClient, harness: dict
) -> None:
    """`replied` 但无正文、无任务动作 ⇒ `failed`，且用 `reason.team_change_no_body` 而不是认领那一条。"""
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    harness["seam"].behaviors[leader["id"]] = EMPTY_REPLY

    task = _create_task(client, channel["id"], title="队长空回")
    drain(harness)

    outcomes = _db(client).list_round_outcomes(task["target_id"])
    assert [outcome["status"] for outcome in outcomes] == ["failed"]
    reason = outcomes[0]["reason"]
    assert "任务变化" in reason
    assert "没有产出可落库的正文" in reason, "落的是 reason.team_change_no_body"
    assert "认领" not in reason


def test_the_team_leader_prompt_does_not_list_workspace_agents_as_team_assign_options(
    client: TestClient, harness: dict
) -> None:
    """队长不是受派者时，prompt 不能把**全工作区**的 Agent 列成「可 TeamAssign 分派的成员」。

    `TeamAssign` 的可选成员由该工具自身的描述给出（本轮成员快照），而任务上下文里的
    `transfer_options_for()` 来源是 `Database.agent_members()`（全工作区）——两者不是同一份名单，
    在 prompt 里再列一份就会把频道外的 Agent 写成可分派对象（ADR-0025 §四）。
    """
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="频道成员").json()["agent"]
    outsider = create_agent(client, name="频道外成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])

    task = _create_task(client, channel["id"], title="成员清单口径")
    drain(harness)

    assert _task(client, task["id"])["assignee_member_id"] is None, "队长这一轮不是受派者"
    prompt = harness["seam"].calls_for(leader["id"])[0]["prompt"]
    assert outsider["display_name"] not in prompt, "频道外的 Agent 不是 TeamAssign 的可选项"
    assert "可转派 Agent" not in prompt, "队长不是受派者时没有 transfer_task 的可选项清单"
    assert "TeamAssign" in prompt, "分派指引仍在（可选成员见工具描述）"


# ─── 五态：owner HTTP + 队长轮次结构化工具 ──────────────────────────────
def test_owner_http_keeps_the_five_task_states_in_a_team_channel(client: TestClient) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    channel = _make_team(client, leader["id"])
    task = _create_task(client, channel["id"], title="看板任务")
    status_path = f"/api/tasks/{task['id']}/status"

    board = client.get("/api/tasks", params={"channel_id": channel["id"]}).json()["tasks"]
    assert [(row["id"], row["status"]) for row in board] == [(task["id"], "todo")]

    for status in ("in_progress", "in_review", "done", "closed"):
        response = client.patch(status_path, json={"status": status})
        assert response.status_code == 200, response.json()
        board = client.get("/api/tasks", params={"channel_id": channel["id"]}).json()["tasks"]
        assert [(row["id"], row["status"]) for row in board] == [(task["id"], status)]

    # 终态不可再推进；看板读数就是任务行，没有 team 专属的第六态。
    assert client.patch(status_path, json={"status": "todo"}).status_code == 400
    assert _task(client, task["id"])["status"] == "closed"


def test_leader_who_is_not_the_assignee_can_approve_in_review_to_done(
    client: TestClient, harness: dict
) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="实现者").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = _create_task(client, channel["id"], title="待复核")
    status_path = f"/api/tasks/{task['id']}/status"
    assert client.patch(status_path, json={"status": "in_progress"}).status_code == 200
    assert client.patch(status_path, json={"status": "in_review"}).status_code == 200
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": member["id"]}).status_code == 200
    )
    drain(harness)
    assert leader["id"] not in _task_member_ids(client, task["id"]), "队长不是受派者，也不是任务成员"

    harness["seam"].behaviors[leader["id"]] = ACTION_ONLY
    harness["seam"].task_actions[leader["id"]] = SetTaskStatusAction(task["id"], "done")
    client.post(f"/api/tasks/{task['id']}/messages", json={"body": "请收口", "task_ref": task["id"]})
    drain(harness)

    assert _task(client, task["id"])["status"] == "done"
    assert sum(message["body"] == "状态推进至 完成" for message in _thread(client, task["id"])) == 1
    leader_outcome = _db(client).list_round_outcomes(task["target_id"])[-1]
    assert leader_outcome["member_id"] == leader["id"]
    assert "拒绝" not in leader_outcome["reason"]


def test_leader_who_is_the_assignee_can_only_be_approved_by_the_owner(
    client: TestClient, harness: dict
) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    channel = _make_team(client, leader["id"])
    task = _create_task(client, channel["id"], title="自审任务")
    status_path = f"/api/tasks/{task['id']}/status"
    assert client.patch(status_path, json={"status": "in_progress"}).status_code == 200
    assert client.patch(status_path, json={"status": "in_review"}).status_code == 200
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": leader["id"]}).status_code == 200
    )
    drain(harness)

    harness["seam"].behaviors[leader["id"]] = ACTION_ONLY
    harness["seam"].task_actions[leader["id"]] = SetTaskStatusAction(task["id"], "done")
    client.post(f"/api/tasks/{task['id']}/messages", json={"body": "我完成了", "task_ref": task["id"]})
    drain(harness)

    assert _task(client, task["id"])["status"] == "in_review"
    task_messages = _thread(client, task["id"])
    assert not any(message["body"] == "状态推进至 完成" for message in task_messages)
    leader_outcome = _db(client).list_round_outcomes(task["target_id"])[-1]
    assert "不能批准" in leader_outcome["reason"]

    owner_approval = client.patch(status_path, json={"status": "done"})
    assert owner_approval.status_code == 200
    assert owner_approval.json()["task"]["status"] == "done"


# ─── TeamAssign 在 task target：运行记录不改任务行 ──────────────────────
def test_team_assign_in_a_task_thread_is_task_scoped_and_leaves_the_task_row_alone(
    data_dir: Path,
) -> None:
    setup = open_database(data_dir)
    leader = setup.create_agent(
        name="任务队长", role="协调", model_id=LOCAL_MODEL_ID, system_prompt="你是队长。"
    )
    member = setup.create_agent(
        name="任务成员", role="执行", model_id=LOCAL_MODEL_ID, system_prompt="你是成员。"
    )
    channel = setup.create_channel(name="任务线程频道", member_ids=[leader["id"], member["id"]])
    setup.close()

    # `_BudgetSeam` 的模型是**按轮**消耗的：每轮 1 个队长 + 每个成员 1 个（成员实例在 pipeline 建轮时
    # 就装配好）。所以两轮 team 运行要四份：建任务那一轮（#179 A）+ 「开工」那一轮。
    creation_model = _ScriptedModel([_text("队长发现新任务")])
    idle_member_model = _ScriptedModel([_text("建任务那轮没有分派，不会被调用")])
    leader_model = _ScriptedModel(
        [
            _tool_calls([("task-thread-call", member_internal_name(member["id"]), "整理验收清单")]),
            _text("队长收口"),
        ]
    )
    member_model = _ScriptedModel([_text("成员产出")])
    seam = _BudgetSeam([creation_model, idle_member_model, leader_model, member_model])
    scheduler = ManualScheduler()

    with backend(data_dir, scheduler=scheduler, seam=seam) as client:
        assert (
            client.patch(
                f"/api/channels/{channel['id']}/config",
                json={"mode": "team", "team_leader_member_id": leader["id"]},
            ).status_code
            == 200
        )
        task = _create_task(client, channel["id"], title="运行期分派")
        target_id = task["target_id"]
        db = _db(client)
        # 先把**建任务唤醒队长**那一轮跑掉（#179 A），下面只观察「开工」那一轮的分派。
        scheduler.drain()
        before = db.get_task(task["id"])
        before_system = [message for message in _thread(client, task["id"]) if message["kind"] == "system"]

        sent = client.post(f"/api/tasks/{task['id']}/messages", json={"body": "开工", "task_ref": task["id"]})
        assert sent.status_code == 201
        scheduler.drain()

        rows = db.list_team_assignments(target_id)
        assert len(rows) == 1
        assignment = rows[0]
        assert assignment["task_id"] == task["id"]
        assert assignment["target_id"] == target_id
        assert assignment["member_id"] == member["id"]
        assert assignment["leader_member_id"] == leader["id"]
        assert assignment["state"] == "succeeded"
        assert assignment["jump_consumed"] == 1
        assert assignment["reply_excerpt"] == "成员产出"

        runs = db.list_team_runs(target_id)
        # 两轮 team 运行：建任务那一轮 + 「开工」那一轮（`started_at DESC`：最新在前）；分派挂在后者下。
        assert [run["status"] for run in runs] == ["completed", "completed"]
        assert assignment["team_run_id"] == runs[0]["id"]
        assert len({run["id"] for run in runs}) == 2

        # TeamAssign 不改任务行：状态 / 指派人 / claimed_at / updated_at 全部原样。
        after = db.get_task(task["id"])
        for key in ("status", "assignee_member_id", "claimed_at", "updated_at"):
            assert after[key] == before[key], key
        assert after["status"] == "todo"
        assert after["assignee_member_id"] is None

        # 不写任务变更 system 消息；成员产出也不落 message；线程里的 agent 正文只有队长自己那两条。
        after_system = [message for message in _thread(client, task["id"]) if message["kind"] == "system"]
        assert [message["body"] for message in after_system] == [message["body"] for message in before_system]
        agent_bodies = [
            message["body"] for message in _thread(client, task["id"]) if message["kind"] == "agent"
        ]
        assert agent_bodies == ["队长发现新任务", "队长收口"]
        assert "成员产出" not in agent_bodies

        # 队长轮次的 prompt 是**队长**身份：含队长身份与 TeamAssign 指引，不写「身份是成员」。
        leader_system = json.dumps(leader_model.seen[0], ensure_ascii=False, default=str)
        assert "队长的身份是「队长」" in leader_system or "team 频道的队长" in leader_system
        assert "TeamAssign" in leader_system
        assert "协作者的身份是「成员」" not in leader_system
        # `transfer_task` 的措辞与**事实**一致：工具只属受派者；队长同时是受派者时它真的在手上，
        # 不是受派者时则没有这个工具（`_task_action_tools` 只在 actor == assignee 时挂它）。
        assert "transfer_task 只属于这张任务的受派者" in leader_system
        assert "不是受派者时需要成员动手请用 TeamAssign" in leader_system
        # 被 TeamAssign 的成员：仍是既有成员提示，不声明队长身份，也不提 TeamAssign。
        member_system = json.dumps(member_model.seen[0], ensure_ascii=False, default=str)
        assert "你是 team 模式频道里的成员" in member_system
        assert "TeamAssign" not in member_system
        assert "team 频道的队长" not in member_system

        # 消耗该 task 的预算；频道预算不动。
        assert db.budget_state(target_id)["used"] == 1
        assert db.budget_state(channel["id"])["used"] == 0


# ─── 运行记录快照：可按 task 过滤 ────────────────────────────────────────
def test_team_runs_snapshot_can_be_filtered_by_task(client: TestClient) -> None:
    """快照端点归 #144；本票只断言 task target 的 run/assignment 能按 task 读回。

    端点形状（#144）：``{target_id, runs:[{...team_run, assignments:[...]}], offset, limit, has_more}``。
    这里不新造 SSE 或运行记录，只经 HTTP 读既有库事实。
    """
    leader = create_agent(client, name="快照队长").json()["agent"]
    member = create_agent(client, name="快照成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = _create_task(client, channel["id"], title="快照任务")
    db = _db(client)
    run = db.create_team_run(target_id=task["target_id"], leader_member_id=leader["id"], base_seq=1)
    assignment = db.create_team_assignment(
        team_run_id=run["id"],
        target_id=task["target_id"],
        task_id=task["id"],
        leader_member_id=leader["id"],
        member_id=member["id"],
        tool_call_id="snapshot-call",
        prompt="写验收",
        state="started",
        jump_consumed=1,
    )

    response = client.get(f"/api/targets/{task['target_id']}/team-runs", params={"task_id": task["id"]})

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["target_id"] == task["target_id"]
    assert [row["id"] for row in payload["runs"]] == [run["id"]]
    assert [row["id"] for row in payload["runs"][0]["assignments"]] == [assignment["id"]]
    assert payload["runs"][0]["assignments"][0]["task_id"] == task["id"]


def test_agent_message_mention_in_a_task_thread_does_not_pierce(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    author = create_agent(client, name="成员作者").json()["agent"]
    outsider = create_agent(client, name="外援").json()["agent"]
    channel = _make_team(client, leader["id"], author["id"])
    task = _create_task(client, channel["id"], title="答复不穿透")

    message = _db(client).append_message(
        target_id=task["target_id"],
        body=f"@{outsider['display_name']} 也看看",
        author_member_id=author["id"],
        kind="agent",
        mention_snapshot=[{"member_id": outsider["id"]}],
    )
    assert _orchestrator(client).notify(task["target_id"], message["seq"]) == 1
    drain(harness)

    assert len(harness["seam"].calls_for(leader["id"])) == 1
    assert harness["seam"].calls_for(author["id"]) == []
    assert harness["seam"].calls_for(outsider["id"]) == []


def test_leader_transfer_wakes_nobody_and_keeps_claimed_at(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = _create_task(client, channel["id"], title="队长转派")
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": leader["id"]}).status_code == 200
    )
    drain(harness)
    harness["seam"].behaviors[leader["id"]] = ACTION_ONLY
    harness["seam"].task_actions[leader["id"]] = TransferTaskAction(task["id"], member["id"])

    client.post(f"/api/tasks/{task['id']}/messages", json={"body": "交给成员", "task_ref": task["id"]})
    drain(harness)

    after = _task(client, task["id"])
    assert after["assignee_member_id"] == member["id"]
    assert after["claimed_at"] is None
    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].calls_for(member["id"]) == []
