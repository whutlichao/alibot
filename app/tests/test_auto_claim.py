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

import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from tests.conftest import LOCAL_MODEL_ID, create_agent, create_channel, drain
from tests.scripted import ACTION_ONLY, DELAY, EMPTY_REPLY, IGNORE, REPLY


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
    # ② 状态：认领**即开工**（ADR-0016）——同一个事务里把任务从 `todo` 推进到 `in_progress`。
    assert task["status"] == "in_progress"
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
        ("system", "任务已创建：整理验收清单"),
        ("system", f"已认领：{agent['display_name']} 接手了这张任务"),
        ("system", "认领后自动开工：状态推进至 进行中"),
    ]
    # 记录的作者与既有的任务事件消息同族（任务是 owner 的动作面产物），且它不带 task_ref/mentions。
    assert thread(client, task["id"])[2]["mention_snapshot"] == []


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
    assert [message["body"] for message in thread(client, task["id"])] == ["任务已创建：整理验收清单"]
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
    """「不回应」也是被唤醒跑了一轮：认领照常发生，但轮次结果收口成 **`failed`**。

    认领是**确定信号**（与「被 @点名」同级）：自判忽略在确定信号下按失败处理，
    不能把一个安静的 `ignored` 当成「它不想说」（ADR-0016 五 / S8）。
    """
    channel, agent = channel_with_agent_and_auto_claim(client)
    harness["seam"].behaviors[agent["id"]] = IGNORE

    task = new_task(client, channel["id"])
    drain(harness)

    assert task["assignee_member_id"] == agent["id"]
    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["failed"]
    # `reason` 里读得出「这一轮由认领唤醒」（S7 的可读后缀）。
    assert "认领" in outcomes[0]["reason"]


def test_a_reply_agent_claims_once_and_its_reply_is_a_plain_agent_message(
    client: TestClient, harness: dict
) -> None:
    """认领唤醒的那一轮照常走既有落库路径（回复是一条 `agent` 消息），不改 seam 的输出形状。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    harness["seam"].behaviors[agent["id"]] = REPLY

    task = new_task(client, channel["id"])
    drain(harness)

    # 三条 `system`（创建 / 已认领 / 认领后自动开工）+ 一条 `agent` 回复：认领轮的回复照常走既有落库路径。
    assert [message["kind"] for message in thread(client, task["id"])] == [
        "system",
        "system",
        "system",
        "agent",
    ]


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


# ─── 6. 认领即开工：[S1] 单事务 / [S2] 迁移表 / [S3] 记录键（ADR-0016） ─────────


def test_the_claim_start_record_body_is_rendered_from_its_own_key(client: TestClient, harness: dict) -> None:
    """[S3] 「认领后自动开工」用**自己的新键**渲染，不复用 `system.task.status`。

    判据是逐字相等：线程第 3 条正文 == `system.task.claim_started` 在偏好语言下的渲染结果。
    这样「编排替它开工」与「有人推了状态」在两份词条表里始终是两个可分辨的事实。
    """
    from alibot import i18n

    channel, _ = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])

    records = thread(client, task["id"])
    assert records[2]["body"] == i18n.render(
        "system.task.claim_started",
        i18n.SOURCE_LOCALE,
        status=i18n.render("status.task.in_progress", i18n.SOURCE_LOCALE),
    )
    # 与既有键**不是**同一句话（否则「谁推的」在线程里就查不出来了）。
    assert records[2]["body"] != i18n.render(
        "system.task.status",
        i18n.SOURCE_LOCALE,
        status=i18n.render("status.task.in_progress", i18n.SOURCE_LOCALE),
    )
    # 两条记录的作者同族（系统事件消息的既有口径，author 恒为 owner、不表示人推的）。
    assert records[1]["author"]["id"] == records[2]["author"]["id"] == "owner"


def _only_task(client: TestClient, title: str) -> dict:
    """按标题取刚建的那张任务（认领失败时**拿不到** HTTP 响应里的 task，只能从既有读面取回）。"""
    tasks = client.get("/api/tasks").json()["tasks"]
    matched = [task for task in tasks if task["title"] == title]
    assert len(matched) == 1, tasks
    return matched[0]


def test_claiming_is_all_or_nothing_when_the_status_step_fails(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """[S1] 单事务：开工那一步失败 ⇒ 认领的全部副作用**零改动**。

    注入点在**认领事务内部**（`_claim_start_status`）：此时「已认领」那条记录、成员面、已读游标与
    开世代都已在同一个事务里写过，紧接着的开工那一步失败。若不是单事务，这四处会各自留在库里。

    故障经 `POST /tasks` 冒出（认领由 `service.create_task` 内联触发，这是既有结构）——于是这里
    断言的是**认领零痕迹**，而不是「建任务本身被回滚」：任务行是它自己那个更早的事务的产物，
    按 D7 「开关关闭」格的口径，它应当留下且保持 `todo` / 未指派 / 无线程新增记录。
    """
    from alibot.db import Database

    channel, _ = channel_with_agent_and_auto_claim(client)

    def explode(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("注入：开工那一步失败")

    monkeypatch.setattr(Database, "_claim_start_status", explode)

    with pytest.raises(RuntimeError, match="注入：开工那一步失败"):
        client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "原子性"})

    task = _only_task(client, "原子性")
    # 认领的四处副作用全部未落地：指派 / claimed_at / 状态 / 成员面 / 预算 / 消息流。
    assert task["status"] == "todo"
    assert task["assignee_member_id"] is None
    assert task["claimed_at"] is None
    assert [member["id"] for member in task_members(client, task["id"])] == ["owner"]
    assert [(m["kind"], m["body"]) for m in thread(client, task["id"])] == [("system", "任务已创建：原子性")]
    assert task_budget(client, task["id"])["epoch_seq"] == 0
    assert task_budget(client, task["id"])["used"] == 0
    assert harness["scheduler"].pending_count() == 0


def test_claim_progress_is_legal_only_because_the_transition_table_allows_it(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """[S2] 合法性判定**复用**迁移表：把 `todo` 的合法目标收紧后，认领失败且零改动。

    这是「不许硬编码 in_progress」的可判定读法：实现若绕过 `TASK_STATUS_TRANSITIONS` 自己写
    `in_progress`，认领会照常成功、这条用例当场红。
    """
    from alibot import db as db_module

    channel, _ = channel_with_agent_and_auto_claim(client)
    monkeypatch.setitem(db_module.TASK_STATUS_TRANSITIONS, "todo", {"closed"})

    rejected = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "收紧迁移表"})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "validation_failed"

    task = _only_task(client, "收紧迁移表")
    assert task["status"] == "todo"
    assert task["assignee_member_id"] is None
    assert task["claimed_at"] is None
    assert [m["body"] for m in thread(client, task["id"])] == ["任务已创建：收紧迁移表"]
    assert task_budget(client, task["id"])["epoch_seq"] == 0
    assert harness["scheduler"].pending_count() == 0


def test_both_claim_records_reach_the_live_stream_and_match_the_replay(
    client: TestClient, harness: dict
) -> None:
    """[S1] 一次认领事务的两条记录都要经事件流出去：实时视图与补拉视图不分叉。"""
    channel, agent = channel_with_agent_and_auto_claim(client)

    task = new_task(client, channel["id"])

    with client.stream("GET", f"/api/tasks/{task['id']}/events", params={"after_seq": 0}) as response:
        assert response.status_code == 200
        replayed: list[dict] = []
        for line in response.iter_lines():
            text = line.decode() if isinstance(line, bytes) else line
            if text.startswith("data: "):
                event = json.loads(text[len("data: ") :])
                if event.get("type") == "message":
                    replayed.append(event["message"])
                if len(replayed) >= 3:
                    break

    live_seqs = [message["seq"] for message in replayed]
    assert live_seqs == sorted(live_seqs), "实时流的 seq 必须递增"
    assert [message["body"] for message in replayed] == [
        "任务已创建：整理验收清单",
        f"已认领：{agent['display_name']} 接手了这张任务",
        "认领后自动开工：状态推进至 进行中",
    ]
    # 补拉与实时是同一个集合（同一批 seq、同样的正文）。
    assert [(m["seq"], m["body"]) for m in thread(client, task["id"])] == [
        (message["seq"], message["body"]) for message in replayed
    ]


# ─── 7. 任务本体进 prompt [S5/S6] + 认领轮必须推进 [S7/S8]（ADR-0016 四 / 五） ──


def _claim_call(harness: dict, agent_id: str) -> dict:
    """取认领者在认领轮里被喂给接缝的那一次调用（替身记录的**输入侧**原件）。"""
    calls = harness["seam"].calls_for(agent_id)
    assert len(calls) == 1, calls
    return calls[0]


def test_the_claim_round_prompt_carries_the_task_body_and_the_assignee_identity(
    client: TestClient, harness: dict
) -> None:
    """[S5/S6] 认领轮的 prompt 带**标题 / 当前状态 / 受派者身份 / 必须回应**四样。

    prompt 注入没有 HTTP 观测面（本仓 README 登记的接缝级例外），因此按既有做法用脚本化替身
    记录收到的输入来断言；``prompt`` 由**真适配器的同一个合成函数**产出，断言落在**具体片段**上。
    """
    channel, agent = channel_with_agent_and_auto_claim(client)

    task = new_task(client, channel["id"], title="分析深圳明天的天气")
    drain(harness)
    call = _claim_call(harness, agent["id"])
    assert task["title"] == "分析深圳明天的天气"  # prompt 里的标题与任务行是同一份事实

    # 第一层：结构断言（替身收到的数据字段，不经文本）。
    assert call["mandatory_reply"] is True, "认领轮必须是「必须回应」的一轮"
    assert call["task_context"] is not None
    assert call["task_context"].title == "分析深圳明天的天气"
    assert call["task_context"].status == "in_progress"
    assert call["task_context"].assignee_member_id == agent["id"]

    # 第二层：文案断言（落具体字面量，不写「等于函数返回值」这种同义反复）。
    prompt = call["prompt"]
    assert "任务：分析深圳明天的天气" in prompt
    assert "当前状态：进行中" in prompt
    assert "你是这张任务的受派者。" in prompt
    assert "本轮由认领唤醒，你必须回应。" in prompt


def test_owner_reassignment_wakes_but_does_not_make_the_round_mandatory(
    client: TestClient, harness: dict
) -> None:
    """[S8 适用范围] owner 手动改派照旧唤醒（任务本体照给），但**不进**必答纪律。

    「只圈认领轮」这条边界靠这里守：改派走同一条唤醒路径，reason 仍是普通消息唤醒。
    """
    channel, _ = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"], title="改派不强制发言")
    drain(harness)

    other = create_agent(client, name="接手的人").json()["agent"]
    client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": other["id"]})
    drain(harness)

    other_calls = harness["seam"].calls_for(other["id"])
    assert other_calls, "owner 改派仍会唤醒新受派者"
    call = other_calls[-1]
    assert call["mandatory_reply"] is False, "改派轮不是必答轮"
    assert "本轮由认领唤醒，你必须回应。" not in call["prompt"]
    # 任务本体照旧给到它（改派后它就是受派者）。
    assert call["task_context"] is not None
    assert call["task_context"].title == "改派不强制发言"
    assert "你是这张任务的受派者。" in call["prompt"]


def test_a_non_assignee_task_round_gets_the_task_body_but_no_identity_sentence(
    client: TestClient, harness: dict
) -> None:
    """[S5 可判定读法] **非受派者**的任务轮次：有任务本体，但**没有**身份句。

    这是 S5 明确要求的那条读法（「非受派者的任务轮次**不含**身份句」）：它防的是模型凭空得到
    「我是受派者」这个假事实——现场那次错误回复正是模型自己推出「我不是受派者」之后又沉默。
    """
    channel, agent = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"], title="非受派者只看不说话")
    drain(harness)
    assert agent["id"] in {member["id"] for member in task_members(client, task["id"])}

    # 一位**不是**受派者的 Agent 被 @ 进任务线程（穿透唤醒）：它被唤醒时知道自己在这张任务里，
    # 但受派者是别人 —— 于是它拿到任务本体、**拿不到**身份句。
    other = create_agent(client, name="旁观者").json()["agent"]
    client.post(f"/api/tasks/{task['id']}/messages", json={"body": f"@{other['display_name']} 你看一下"})
    drain(harness)

    calls = harness["seam"].calls_for(other["id"])
    assert calls, "被拉进任务线程的 Agent 会被唤醒"
    call = calls[-1]
    assert call["task_context"] is not None, "它确实在任务线程里"
    assert call["task_context"].title == "非受派者只看不说话"
    assert call["task_context"].assignee_member_id == agent["id"], "受派者是别人"
    # 任务本体给了，但身份句**没有**——这正是 S5 要守的那一句。
    assert "任务：非受派者只看不说话" in call["prompt"]
    assert "你是这张任务的受派者。" not in call["prompt"], "非受派者绝不能拿到身份句"


def test_channel_and_dm_rounds_carry_no_task_context(client: TestClient, harness: dict) -> None:
    """[S5] 频道 / DM 轮次**不含**任何任务上下文（任务本体只属于任务线程）。"""
    agent = create_agent(client, name="频道成员").json()["agent"]
    channel = create_channel(client, name="聊天室", member_ids=[agent["id"]]).json()["channel"]

    client.post(f"/api/channels/{channel['id']}/messages", json={"body": "随便聊聊"})
    drain(harness)

    call = harness["seam"].calls_for(agent["id"])[-1]
    assert call["task_context"] is None
    assert call["mandatory_reply"] is False
    for fragment in ("任务上下文：", "你是这张任务的受派者。", "本轮由认领唤醒，你必须回应。"):
        assert fragment not in call["prompt"], f"频道轮次不该出现：{fragment}"


def test_an_action_only_claim_round_counts_as_a_reply(client: TestClient, harness: dict) -> None:
    """[S8] 「只调一次 set_task_status 而不说话」**算回应**：动作-only 不判失败。

    动作必须指向**本轮 target 里的**那张任务（编排会校验动作不得跨 target），因此这张任务的 id
    得等它被创建之后才填得进去——于是这里先建任务、再补上替身要提交的动作，然后才 drain。
    """
    from alibot.agent_seam import SetTaskStatusAction

    channel, agent = channel_with_agent_and_auto_claim(client)
    harness["seam"].behaviors[agent["id"]] = ACTION_ONLY

    task = new_task(client, channel["id"], title="动作-only 也算回应")
    harness["seam"].task_actions = {agent["id"]: SetTaskStatusAction(task["id"], "in_review")}
    drain(harness)

    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["replied"]
    # 动作真的被应用了（不是空转）：状态推进到 in_review，且线程里留下那条状态记录。
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "in_review"


def test_the_claim_round_reason_carries_the_readable_suffix(client: TestClient, harness: dict) -> None:
    """[S7] `round_outcome.reason` 追加可读后缀；`round_outcome` 表列集不变、typing 的 reason 是 claim。"""
    from alibot import i18n

    channel, agent = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    drain(harness)

    outcome = task_outcomes(client, task["id"])[0]
    assert outcome["member_id"] == agent["id"], "后缀属于认领者那一轮"
    # 前缀是本轮自己的 reason（脚本化替身给的那句），后缀是认领轮的可读标记。
    expected = i18n.render("reason.claim_round", i18n.SOURCE_LOCALE, reason="脚本化替身：决定回应")
    assert outcome["reason"] == expected
    assert outcome["reason"].endswith(
        i18n.render("reason.claim_round", i18n.SOURCE_LOCALE, reason="").split("；", 1)[-1]
    )
    # 表列集不变（`reason` 是既有 TEXT 列，零 DDL）。
    assert set(outcome) == {
        "id",
        "member_id",
        "target_id",
        "base_seq",
        "status",
        "reason",
        "started_at",
        "finished_at",
        "consumed_budget",
        "merged",
        "reply_seq",
    }


# ─── 8. 三路径五格读数表 [S10] + 预算与世代不变 [S9]（ADR-0016 二 / 七） ──────


def _path_snapshot(client: TestClient, task: dict) -> dict:
    """三路径对照的统一读数（D7 表里每一格都按同一组字段判）。"""
    return {
        "task": client.get(f"/api/tasks/{task['id']}").json()["task"],
        "records": [(m["kind"], m["body"]) for m in thread(client, task["id"])],
        "members": [m["id"] for m in task_members(client, task["id"])],
        "budget": task_budget(client, task["id"]),
        "outcomes": task_outcomes(client, task["id"]),
    }


def test_path_one_switch_off_leaves_no_trace_and_no_wake(client: TestClient, harness: dict) -> None:
    """[S10/D7 第 1 格] 开关关闭：任务留 `todo`、线程仅 `created`、`epoch_seq=0`、无唤醒、无轮次结果。"""
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]

    task = new_task(client, channel["id"], title="开关关着")

    snapshot = _path_snapshot(client, task)
    assert snapshot["task"]["status"] == "todo"
    assert snapshot["task"]["assignee_member_id"] is None
    assert snapshot["task"]["claimed_at"] is None
    assert snapshot["records"] == [("system", "任务已创建：开关关着")], "只有 created 一条"
    assert snapshot["members"] == ["owner"]
    assert snapshot["budget"]["epoch_seq"] == 0 and snapshot["budget"]["used"] == 0
    assert snapshot["outcomes"] == []
    assert harness["scheduler"].pending_count() == 0


def test_path_two_unavailable_agent_leaves_no_trace_and_no_wake(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """[S10/D7 第 2 格] 有候选但**不可用**：与第 1 格逐字同形（不可用的 Agent 不参与裁决）。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    agent = create_agent(client, model_id="dashscope:qwen-max").json()["agent"]
    client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]
    set_auto_claim(client, agent["id"], True)

    task = new_task(client, channel["id"], title="候选不可用")

    snapshot = _path_snapshot(client, task)
    assert snapshot["task"]["status"] == "todo"
    assert snapshot["task"]["assignee_member_id"] is None
    assert snapshot["records"] == [("system", "任务已创建：候选不可用")]
    assert snapshot["members"] == ["owner"]
    assert snapshot["budget"]["epoch_seq"] == 0
    assert snapshot["outcomes"] == []
    assert harness["scheduler"].pending_count() == 0


def test_path_three_already_claimed_task_is_a_no_op(client: TestClient, harness: dict) -> None:
    """[S10/D7 第 3 格] 已认领：幂等无操作——状态、线程、世代、用地、唤醒全都不动。"""
    channel, _ = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    drain(harness)
    before = _path_snapshot(client, task)
    pending_before = harness["scheduler"].pending_count()

    assert _orchestrator(client).claim_new_task(task["id"], channel["id"]) == 0

    assert _path_snapshot(client, task) == before
    assert harness["scheduler"].pending_count() == pending_before


def test_a_claim_still_delivers_and_must_be_answered_in_an_exhausted_epoch(
    client: TestClient, harness: dict
) -> None:
    """[S9] 预算耗尽的世代里认领**仍投递、仍必须回应**：认领不是跳（ADR-0016 二）。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    # 先把该频道的预算烧到上界（认领开的是**任务** target 的世代，与频道那份互不影响，
    # 于是这里同时验证「预算是每 target 一份」这条既有纪律）。
    exhaust = create_channel(client, name="已耗尽", member_ids=[agent["id"]]).json()["channel"]
    _db(client).consume_budget(exhaust["id"], 0, amount=3)

    task = new_task(client, channel["id"])
    assert harness["scheduler"].pending_count() == 1, "耗尽的是别的 target，认领照常投递"

    drain(harness)
    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["replied"]
    # 认领不计跳：任务的 `used` 仍是 0。
    assert task_budget(client, task["id"])["used"] == 0


def test_the_claim_round_is_mandatory_even_when_the_epoch_was_already_used(
    client: TestClient, harness: dict
) -> None:
    """[S9] 任务 target 的预算已耗尽时，认领轮**仍投递**（它不开预算账）。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    harness["seam"].behaviors[agent["id"]] = IGNORE

    task = new_task(client, channel["id"])
    drain(harness)
    # 把这张任务 target 的用量推到上界，再触发一次认领路径（无操作，不投递也不重置）。
    _db(client).consume_budget(task["target_id"], task_budget(client, task["id"])["epoch_seq"], amount=3)
    before = task_budget(client, task["id"])
    assert _orchestrator(client).claim_new_task(task["id"], channel["id"]) == 0
    assert task_budget(client, task["id"]) == before

    fresh = new_task(client, channel["id"], title="耗尽后再建一张")
    assert harness["scheduler"].pending_count() == 1, "认领照样投递：它不是跳"
    drain(harness)
    assert [outcome["status"] for outcome in task_outcomes(client, fresh["id"])] == ["failed"]
    assert task_budget(client, fresh["id"])["used"] == 0


def test_claim_keeps_the_schema_and_round_outcome_column_set_unchanged(
    client: TestClient, harness: dict
) -> None:
    """[S12/S9 不变量] schema 不 bump；`RoundOutcome` 字段集不变；认领轮不写新列。"""
    import dataclasses

    from alibot.agent_seam import RoundOutcome
    from alibot.db import SCHEMA_VERSION

    assert SCHEMA_VERSION == 12, "本票零 DDL、不 bump（ADR-0016 六）"
    assert [field.name for field in dataclasses.fields(RoundOutcome)] == [
        "status",
        "reason",
        "messages",
        "started_at",
        "finished_at",
        "consumed_budget",
        "task_action",
    ], "RoundOutcome 一字不动（ADR-0010 一）"

    channel, _ = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    drain(harness)
    # `round_outcome` 的列集与基线相同（含认领轮）。
    assert set(task_outcomes(client, task["id"])[0]) == {
        "id",
        "member_id",
        "target_id",
        "base_seq",
        "status",
        "reason",
        "started_at",
        "finished_at",
        "consumed_budget",
        "merged",
        "reply_seq",
    }


def test_path_five_claimed_then_unavailable_at_wake_time(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """[S10/D7 第 5 格] 认领裁决与「模型此刻能不能跑」是两件事：状态已推进、那一轮 `failed`。

    编排的裁决（谁该接手、该不该开工）发生在 `claim_task` **之前**；`availability` 在裁决阶段看过一次，
    但「认领已落库、随后才发现不可用」这个窗口是真实的（例如凭证在两步之间被撤掉）。预期可观察结果：
    任务 `in_progress` 且**不回滚**，那一轮 `failed`、`reason` 取既有 `availability.*` 文案。

    注入点选在凭证解析（`read_api_key`）：它由 `availability` 在**每一轮开头**重新判一次，正是那个
    「裁决时可用、跑的时候不可用」的真实来源；而「先 PATCH enabled=false」造不出这个窗口——
    成员快照在投递时就定了型。
    """
    import alibot.orchestration as orchestration_module

    channel, _ = channel_with_agent_and_auto_claim(client)
    task = new_task(client, channel["id"])
    # 认领已在事务里落库并推进了状态。
    assert task["status"] == "in_progress"

    def credential_vanished(*args: Any, **kwargs: Any) -> str:
        raise orchestration_module.CredentialUnavailable("ollama", ("OLLAMA_API_KEY",))

    monkeypatch.setattr(orchestration_module, "read_api_key", credential_vanished)
    drain(harness)

    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "in_progress", "不回滚"
    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["failed"]
    # reason 是既有 availability 文案（认领后缀只在提示到接缝的轮次上追加，失败轮不含它）。
    assert "OLLAMA_API_KEY" in outcomes[0]["reason"], outcomes[0]["reason"]


def test_a_body_less_reply_on_the_claim_round_is_recorded_as_failed(
    client: TestClient, harness: dict
) -> None:
    """[S8 第二行] `replied` 但**无正文且无任务动作** ⇒ `failed`（「假装回应」不成立）。"""
    channel, agent = channel_with_agent_and_auto_claim(client)
    harness["seam"].behaviors[agent["id"]] = EMPTY_REPLY

    task = new_task(client, channel["id"])
    drain(harness)

    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["failed"]
    # 失败不伪造「它答应了」：线程里没有多出 agent 消息。
    assert [message["kind"] for message in thread(client, task["id"])] == [
        "system",
        "system",
        "system",
    ]


def test_the_mandatory_flag_survives_a_deferred_wait_on_another_target(
    client: TestClient, harness: dict
) -> None:
    """[S8] 跨 target 被挡在后面（deferred）时，**必答标记必须一起传下去**。

    这条守护的是一个真实缺陷形状：`_DeferredWake` 收下了 `mandatory_reply`（含「取或」合并），
    但收口时重投 deferred 若漏传它，认领轮就会以 `mandatory_reply=False` 重跑——自判忽略不再收口成
    `failed`、`reason.claim_round` 后缀也丢，而**其余一切看起来都正常**。

    构造方式：先让认领者在一个**频道**轮次里挂着（delay 闸门），此时任务线程的认领唤醒只能进
    deferred；放行后按 deferred 路径重跑，断言那一轮仍是**必须回应**的一轮。
    """
    agent = create_agent(client, name="小明", model_id=LOCAL_MODEL_ID).json()["agent"]
    channel = create_channel(client, name="协作频道", member_ids=[agent["id"]]).json()["channel"]
    set_auto_claim(client, agent["id"], True)
    harness["seam"].behaviors[agent["id"]] = DELAY

    # ① 先用一条频道消息把该 Agent 占住（这一轮停在闸门上，它的 target 是频道）。
    harness["seam"].block()
    client.post(f"/api/channels/{channel['id']}/messages", json={"body": "先聊两句"})
    import threading

    runner = threading.Thread(target=drain, args=(harness,), daemon=True)
    runner.start()
    for _ in range(200):
        if harness["seam"].calls_for(agent["id"]):
            break
        threading.Event().wait(0.01)

    # ② 它正忙时新建任务 ⇒ 认领唤醒只能进 deferred（另一个 target）。
    task = new_task(client, channel["id"], title="延迟中的认领")

    # ③ 放行：闸门开 → 频道那轮收口 → deferred 的认领轮被重投。
    harness["seam"].behaviors[agent["id"]] = IGNORE
    harness["seam"].release()
    runner.join(timeout=10)
    drain(harness)  # 收掉 deferred 重投出来的那一轮

    calls = harness["seam"].calls_for(agent["id"])
    claim_calls = [call for call in calls if call["target_id"] == task["target_id"]]
    assert claim_calls, "任务线程那一轮必须真的跑过"
    assert claim_calls[-1]["mandatory_reply"] is True, "deferred 重投必须保住必答标记"
    outcomes = task_outcomes(client, task["id"])
    assert [outcome["status"] for outcome in outcomes] == ["failed"], "IGNORE 在必答轮 ⇒ failed"
    assert "认领" in outcomes[0]["reason"], "reason.claim_round 后缀也要在"
