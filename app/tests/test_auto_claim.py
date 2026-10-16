"""Agent 主动认领的 HTTP 黑盒契约（实施票 #65 / ADR-0010）。

四条承诺各有一组读数（票面「可观察验收判据」）：

1. **开关开启且 Agent 可用**：频道内新建任务后被认领——指派者与状态可读、认领者进入该任务的
   **target 成员**、它被唤醒跑了**一轮**（四点各有读数）；
2. **开关关闭 / Agent 不可用**：不认领，任务保持「未认领」（对照读数）；
3. **每张任务至多一次**：认领之后再次触发认领路径，``target_budget.epoch_seq`` **不再前进**、
   ``used`` **不再被重置**（这是 ADR-0002 三跳上界仍可执行的关键）；
4. **认领不消耗跳数**：``target_budget.used`` 不因认领这条投递 +1。

只断言**外部可观察行为**：HTTP 读数、任务线程里的消息、``round_outcome`` 行与 ``target_budget``
这三个既有只读面；不测内部函数，也不改 ``agent_seam`` 的输出形状（它仍是「零或多条待落库回复 +
一个轮次结果」）。
"""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from tests.conftest import LOCAL_MODEL_ID, create_agent, create_channel, drain
from tests.scripted import IGNORE, REPLY


def _db(client: TestClient) -> Any:
    """测试用的库句柄（``app.state.db``），与 ``test_tasks.py`` 同一个既有接缝。"""
    return cast(Any, client.app).state.db


def _orchestrator(client: TestClient) -> Any:
    """编排句柄（``app.state.orchestrator``）：认领裁决的**唯一**入口，服务层的调用点也在这里。"""
    return cast(Any, client.app).state.orchestrator


def new_task(client: TestClient, channel_id: str, title: str = "整理验收清单") -> dict:
    response = client.post(f"/api/channels/{channel_id}/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["task"]


def task_budget(client: TestClient, task_id: str) -> dict:
    return client.get(f"/api/tasks/{task_id}/budget").json()["budget"]


def thread(client: TestClient, task_id: str) -> list[dict]:
    return client.get(f"/api/tasks/{task_id}/messages").json()["messages"]


def task_members(client: TestClient, task_id: str) -> list[dict]:
    return client.get(f"/api/tasks/{task_id}/members").json()["members"]


def set_auto_claim(client: TestClient, member_id: str, value: bool) -> dict:
    response = client.patch(f"/api/agents/{member_id}", json={"auto_claim": value})
    assert response.status_code == 200, response.text
    return response.json()["agent"]


def task_outcomes(client: TestClient, task_id: str) -> list[dict]:
    target_id = client.get(f"/api/tasks/{task_id}").json()["task"]["target_id"]
    return _db(client).list_round_outcomes(target_id)


def channel_with_agent_and_auto_claim(client: TestClient) -> tuple[dict, dict]:
    """一个频道 + 一个已加入、开关已打开的本地 Agent（认领的**正样本**）。"""
    agent = create_agent(client, name="小明", model_id=LOCAL_MODEL_ID).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]
    return channel, set_auto_claim(client, agent["id"], True)


# ─── 1. 开关与读回 ───────────────────────────────────────────────────────────


def test_auto_claim_defaults_off_and_round_trips_through_the_patch_endpoint(
    client: TestClient,
) -> None:
    """开关缺省**关闭**（认领是显式选择），并经 ④ 的同一条写路径读写。"""
    agent = create_agent(client).json()["agent"]
    assert agent["auto_claim"] is False

    listed = client.get("/api/agents").json()["agents"]
    assert next(item for item in listed if item["id"] == agent["id"])["auto_claim"] is False

    opened = client.patch(f"/api/agents/{agent['id']}", json={"auto_claim": True}).json()["agent"]
    assert opened["auto_claim"] is True
    assert client.get(f"/api/agents/{agent['id']}").json()["agent"]["auto_claim"] is True
    # 只写这一个字段：其余配置逐字不变。
    assert opened["display_name"] == agent["display_name"]
    assert opened["enabled"] is True

    assert set_auto_claim(client, agent["id"], False)["auto_claim"] is False
    assert client.get(f"/api/agents/{agent['id']}").json()["agent"]["auto_claim"] is False


def test_auto_claim_rejects_an_explicit_null_without_writing(client: TestClient, harness: dict) -> None:
    """显式 ``null`` 不是「没提到」：整体拒绝，且开关与消息/预算三处读数都不动。"""
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]
    before_messages = client.get(f"/api/channels/{channel['id']}/messages").json()["messages"]

    rejected = client.patch(f"/api/agents/{agent['id']}", json={"auto_claim": None})
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "validation_failed"
    assert client.get(f"/api/agents/{agent['id']}").json()["agent"]["auto_claim"] is False

    # 配置变更（含被拒的那次）不是发言：无人被唤醒，频道消息仍逐字相同。
    assert client.get(f"/api/channels/{channel['id']}/messages").json()["messages"] == before_messages
    assert harness["scheduler"].pending_count() == 0


# ─── 2. 开关开启且可用：被认领（四点读数） ───────────────────────────────────


def test_new_task_is_claimed_when_the_switch_is_on_and_the_agent_is_available(
    client: TestClient, harness: dict
) -> None:
    channel, agent = channel_with_agent_and_auto_claim(client)

    task = new_task(client, channel["id"])

    # ① 指派者：认领者自己。
    assert task["assignee_member_id"] == agent["id"]
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["assignee_member_id"] == agent["id"]
    # ② 状态：认领**不改状态**（ADR-0010 的动作表把「认领」与「改状态」列为两格；认领的载荷效应
    #    只有 system 记录 / target 成员 / 唤醒三件），任务仍在 `todo`。
    assert task["status"] == "todo"
    assert task["claimed_at"] is not None
    # ③ 认领者进入该任务的 target 成员面（与 owner 指派同一张表、同一个键）。
    assert agent["id"] in {member["id"] for member in task_members(client, task["id"])}
    # ④ 它被唤醒跑了**一轮**：编排已投递一轮，跑完后该 task target 上留下它的轮次结果。
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 1
    assert calls[0]["target_id"] == task["target_id"]
    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["replied"]
    assert outcomes[0]["member_id"] == agent["id"]


def test_a_claimed_task_is_readable_from_the_card_thread_and_members(
    client: TestClient, harness: dict
) -> None:
    """认领结果在三处既有呈现面可读：看板卡片字段、任务线程的 `system` 记录、任务成员表。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])

    board = client.get("/api/tasks", params={"channel_id": channel["id"]}).json()["tasks"]
    assert board[0]["assignee_member_id"] == agent["id"]
    assert board[0]["id"] == task["id"]
    assert agent["id"] in {member["id"] for member in task_members(client, task["id"])}


def test_the_claim_record_is_a_readable_system_message_in_the_task_thread(
    client: TestClient, harness: dict
) -> None:
    """认领在任务线程里落一条**可读的** `system` 记录（它**不是**对话回合，不唤醒别人）。"""
    channel, agent = channel_with_agent_and_auto_claim(client)

    task = new_task(client, channel["id"])

    records = [(message["kind"], message["body"]) for message in thread(client, task["id"])]
    assert records == [
        ("system", "任务已创建"),
        ("system", f"已认领：{agent['display_name']} 接手了这张任务"),
    ]
    # 记录的作者与既有的任务事件消息同族（任务是 owner 的动作面产物），且它不带 task_ref/mentions。
    assert thread(client, task["id"])[1]["mention_snapshot"] == []


def test_claim_writes_the_record_in_the_current_locale(client: TestClient, harness: dict) -> None:
    """`system` 记录按**语言偏好**产出（ADR-0009 第二层：编排读库，不读请求头）。"""
    client.patch("/api/settings", json={"locale": "en"})
    channel, agent = channel_with_agent_and_auto_claim(client)

    task = new_task(client, channel["id"])

    body = thread(client, task["id"])[1]["body"]
    assert body == f"Claimed: {agent['display_name']} took this task"


# ─── 3. 开关关闭 / Agent 不可用：不认领 ──────────────────────────────────────


def test_new_task_stays_unclaimed_when_the_switch_is_off(client: TestClient, harness: dict) -> None:
    """对照读数：开关关着（缺省）时，任务保持**未认领**，且没有任何唤醒被投递。"""
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]

    task = new_task(client, channel["id"])

    assert task["assignee_member_id"] is None
    assert task["claimed_at"] is None
    assert task["status"] == "todo"
    assert [member["id"] for member in task_members(client, task["id"])] == ["owner"]
    assert [message["body"] for message in thread(client, task["id"])] == ["任务已创建"]
    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].calls == []
    assert task_budget(client, task["id"]) == {
        "channel_id": task["target_id"],
        "epoch_seq": 0,
        "limit": 3,
        "used": 0,
        "remaining": 3,
    }


def test_new_task_stays_unclaimed_when_the_claimant_is_unavailable(client: TestClient, harness: dict) -> None:
    """开关开着但 Agent **不可用**时不认领：认领走的是与唤醒同一套可用性判定。"""
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]
    set_auto_claim(client, agent["id"], True)
    # 「暂停」= 写 enabled = 0（ADR-0011）：可用性立刻变成未启用，于是它不是候选。
    client.patch(f"/api/agents/{agent['id']}", json={"enabled": False})

    task = new_task(client, channel["id"])

    assert task["assignee_member_id"] is None
    assert task["claimed_at"] is None
    assert harness["scheduler"].pending_count() == 0
    assert task_budget(client, task["id"])["epoch_seq"] == 0


def test_an_agent_outside_the_channel_never_claims(client: TestClient, harness: dict) -> None:
    """裁决面是**该频道**的成员：开关开着的局外 Agent 不参与认领。"""
    outsider = create_agent(client, name="局外").json()["agent"]
    set_auto_claim(client, outsider["id"], True)
    channel = create_channel(client, name="协作频道").json()["channel"]

    task = new_task(client, channel["id"])

    assert task["assignee_member_id"] is None
    assert harness["scheduler"].pending_count() == 0


def test_only_one_agent_claims_when_several_are_eligible(client: TestClient, harness: dict) -> None:
    """一次只认领一个（不抢、不重复指派）：候选按创建顺序取第一个，其余不被唤醒。"""
    first = create_agent(client, name="第一位").json()["agent"]
    second = create_agent(client, name="第二位").json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[first["id"], second["id"]]).json()[
        "channel"
    ]
    set_auto_claim(client, first["id"], True)
    set_auto_claim(client, second["id"], True)

    task = new_task(client, channel["id"])

    assert task["assignee_member_id"] == first["id"]
    only_member = {member["id"] for member in task_members(client, task["id"])}
    assert second["id"] not in only_member
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(first["id"])) == 1
    assert harness["seam"].calls_for(second["id"]) == []


def test_an_ignoring_agent_still_claims_and_records_its_round(client: TestClient, harness: dict) -> None:
    """「不回应」也是被唤醒跑了一轮：认领照常发生，轮次结果收口成 `ignored`（脚本化替身固定）。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    harness["seam"].behaviors[agent["id"]] = IGNORE

    task = new_task(client, channel["id"])
    drain(harness)

    assert task["assignee_member_id"] == agent["id"]
    assert [outcome["status"] for outcome in task_outcomes(client, task["id"])] == ["ignored"]


def test_a_reply_agent_claims_once_and_its_reply_is_a_plain_agent_message(
    client: TestClient, harness: dict
) -> None:
    """认领唤醒的那一轮照常走既有落库路径（回复是一条 `agent` 消息），不改 seam 的输出形状。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    harness["seam"].behaviors[agent["id"]] = REPLY

    task = new_task(client, channel["id"])
    drain(harness)

    assert [message["kind"] for message in thread(client, task["id"])] == ["system", "system", "agent"]


# ─── 4. 预算语义（ADR-0010 三 / 四 + 票面「不消耗跳数」） ──────────────────────


def test_first_claim_opens_one_epoch_and_resets_the_budget_to_full(client: TestClient, harness: dict) -> None:
    """首次认领是该 task target 的**一次开世代动作**：`epoch_seq` = 认领那条 `system` 记录的 seq。"""
    channel, _ = channel_with_agent_and_auto_claim(client)

    task = new_task(client, channel["id"])

    claim_seq = thread(client, task["id"])[1]["seq"]
    assert task_budget(client, task["id"]) == {
        "channel_id": task["target_id"],
        "epoch_seq": claim_seq,
        "limit": 3,
        "used": 0,
        "remaining": 3,
    }


def test_a_claim_does_not_consume_a_jump(client: TestClient, harness: dict) -> None:
    """认领**不是跳**：它唤醒认领者，但 `target_budget.used` 不因这条投递 +1。"""
    channel, _ = channel_with_agent_and_auto_claim(client)

    task = new_task(client, channel["id"])
    # 投递已发生（认领者被唤醒），但 `used` 仍是 0。
    assert harness["scheduler"].pending_count() == 1
    assert task_budget(client, task["id"])["used"] == 0

    drain(harness)
    # 认领者的回复是 Agent 消息，但该任务 target 上除它自己外没有别的 Agent ⇒ 零投递 ⇒ 不是一次跳。
    assert [message["kind"] for message in thread(client, task["id"])][-1] == "agent"
    assert task_budget(client, task["id"])["used"] == 0


def test_a_task_is_claimed_at_most_once_so_the_epoch_never_advances_again(
    client: TestClient, harness: dict
) -> None:
    """**每张任务至多一次**（本票最关键的读数）：再触发认领路径，`epoch_seq` 不前进、`used` 不重置。

    读数一律来自 HTTP 面（`GET /api/tasks/{id}/budget`、`/messages`、`/api/tasks/{id}`）。两半各看一件事：

    1. **全程 HTTP**：取消指派之后在同一个频道再建一张新任务——认领路径确实又跑了一次，
       而那张**已被认领过**的任务纹丝不动（`claimed_at`、`epoch_seq`、`used`、线程记录都不变）。
    2. **同一张任务的第二次触发**：认领路径的扳机是「频道内新建任务」，对一张**已存在**的任务没有
       第二条 HTTP 扳机，因此这里调**服务层调用的同一个入口**（`WorkspaceService.create_task` 里
       那一次 `claim_new_task`）——与 `test_tasks.py` 用 `app.state.db` 取证同一体例：断言读的是
       外部事实，只有扳机走接缝。这一半证明的是**库内 `claimed_at` 这条构造性事实**（而不是
       「任务恰好还指派着某人」）：任务回到未指派后仍然不可能被认领第二次。
    """
    channel, _ = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    drain(harness)
    # 把用量推上去，才看得出「不被重置」（`_db` 是 `test_tasks.py` 登记过的取证接缝，这里只用来摆读数）。
    first_epoch = task_budget(client, task["id"])["epoch_seq"]
    _db(client).consume_budget(task["target_id"], first_epoch, amount=2)
    before = task_budget(client, task["id"])
    assert before == {
        "channel_id": task["target_id"],
        "epoch_seq": first_epoch,
        "limit": 3,
        "used": 2,
        "remaining": 1,
    }
    records_before = [(message["kind"], message["body"]) for message in thread(client, task["id"])]
    claimed_at_before = client.get(f"/api/tasks/{task['id']}").json()["task"]["claimed_at"]

    # ① 取消指派（HTTP）→ 同一频道再建一张新任务（HTTP）：认领路径又跑了一次，且认领的是新任务。
    client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": None})
    fresh = new_task(client, channel["id"], title="同一频道的新任务")
    assert fresh["assignee_member_id"] is not None, "认领路径确实又跑了一次"
    after_unassign = [*records_before, ("system", "指派已取消")]
    assert [(m["kind"], m["body"]) for m in thread(client, task["id"])] == after_unassign
    assert task_budget(client, task["id"])["epoch_seq"] == first_epoch
    assert task_budget(client, task["id"])["used"] == 2, "已被认领过的任务：用量不被重置"
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["claimed_at"] == claimed_at_before
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["assignee_member_id"] is None

    # ② 对同一张任务再触发一次认领路径：不推进世代、不重置用量、不写第二条记录、不新增唤醒。
    already_claimed = task_budget(client, task["id"])
    records = [(message["kind"], message["body"]) for message in thread(client, task["id"])]
    pending_before = harness["scheduler"].pending_count()  # ① 的那一轮（新任务的认领唤醒）还排着
    assert _orchestrator(client).claim_new_task(task["id"], channel["id"]) == 0
    assert task_budget(client, task["id"]) == already_claimed
    assert [(m["kind"], m["body"]) for m in thread(client, task["id"])] == records
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["claimed_at"] == claimed_at_before
    assert harness["scheduler"].pending_count() == pending_before


def test_the_claim_marker_survives_a_backend_restart(launched: dict) -> None:
    """`claimed_at` 是**落库的事实**：后端重启后同一张任务不可能再被认领一次。

    重启后没有 HTTP 扳机能指向这张已存在的任务（见上一条用例的说明），因此这里同样经那个入口取证。
    """
    client = launched["client"]
    channel, _ = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    drain(launched)
    before = task_budget(client, task["id"])

    after = launched["restart"]()  # 新进程读同一个数据目录：内存里的东西一律不留

    assert after.get(f"/api/tasks/{task['id']}").json()["task"]["claimed_at"] is not None
    assert task_budget(after, task["id"]) == before
    assert _orchestrator(after).claim_new_task(task["id"], channel["id"]) == 0
    assert task_budget(after, task["id"]) == before


# ─── 5. 认领不改既有语义：配置写路径的边界 ───────────────────────────────────


def test_the_auto_claim_switch_changes_nothing_until_the_next_task(client: TestClient, harness: dict) -> None:
    """开关是**配置类取值**：写它不落消息、不唤醒、不消耗预算，且不回填已有的任务。"""
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]
    existing = new_task(client, channel["id"], title="先有的任务")

    def snapshot() -> tuple:
        return (
            client.get(f"/api/tasks/{existing['id']}").json()["task"],
            thread(client, existing["id"]),
            task_budget(client, existing["id"]),
            harness["scheduler"].pending_count(),
        )

    before = snapshot()
    set_auto_claim(client, agent["id"], True)

    assert snapshot() == before, "开关只影响之后新建的任务"
