"""多 Agent 同频道接力：唤醒面、穿透与预算上界、并发合并、失败隔离、可追溯与恢复。

只经 HTTP 面与事件流取证（沿用 #② 的**唯一高层接缝**与脚本化替身，不另开第二套接缝）。
每一轮由测试自己用 ``harness["scheduler"].drain()`` 决定何时跑完，因此「一轮 in-flight」
与「被合并」这类窗口可以稳定观察，不需要 sleep。

本文件的核心是**有界响应策略 B** 的硬上界（票面写明「≤ 3 是验收断言，不是建议」）：
两个互相 `@` 的 Agent 在一个人世代内最多产生 3 次 agent→agent 唤醒，之后消息照常落库但
一个 Agent 都不再被唤醒，直到人类再发言重置。上界由构造保证（耗尽的世代里零投递），
而不是靠事后统计。
"""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from tests.conftest import (
    budget,
    channel_with_agent,
    create_agent,
    create_channel,
    drain,
    messages,
    outcomes,
    send,
)
from tests.scripted import DELAY, FAIL, IGNORE, REPLY


# ─── 普通消息唤醒全部成员且排除作者 ────────────────────────────────────────
def test_plain_message_wakes_every_channel_agent_member_and_excludes_the_author(
    client: TestClient, harness: dict
) -> None:
    """未点名任何人的普通消息落库后，该频道**全部** agent 成员被唤醒（作者自己排除）。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    third = create_agent(client, name="丙").json()["agent"]
    # 都自判无关：它们沉默，于是这一波唤醒可被完整观察而不引入后续往返。
    harness["seam"].behaviors = {member["id"]: IGNORE for member in (first, second, third)}
    channel = client.post(
        "/api/channels",
        json={"name": "全员", "description": "", "member_ids": [first["id"], second["id"]]},
    ).json()["channel"]
    # 丙不在频道里且没被点名：它不该被唤醒。
    create_channel(client, name="别的频道", member_ids=[third["id"]])

    send(client, channel["id"], "大家好，这是一条没有点名的消息")
    drain(harness)

    woken = {call["member_id"] for call in harness["seam"].calls}
    assert woken == {first["id"], second["id"]}
    assert third["id"] not in woken
    # 每个被唤醒者各留一条结论（可追溯「它为什么没说话」）。
    assert {record["member_id"] for record in outcomes(client, channel["id"])} == {
        first["id"],
        second["id"],
    }
    assert all(record["status"] == "ignored" for record in outcomes(client, channel["id"]))


def test_agent_author_is_excluded_from_the_wake_set_not_only_humans(
    client: TestClient, harness: dict
) -> None:
    """作者排除对 **Agent 作者**同样成立：甲发言后只有乙被唤醒，甲不会被自己唤醒。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].behaviors = {second["id"]: IGNORE}
    channel = client.post(
        "/api/channels",
        json={"name": "排除作者", "description": "", "member_ids": [first["id"], second["id"]]},
    ).json()["channel"]

    send(client, channel["id"], "普通一句")
    drain(harness)

    # 甲只被人类消息唤醒一次（它的回复没有把它自己再拉起来）。
    assert len(harness["seam"].calls_for(first["id"])) == 1
    # 乙看得到甲的回复 ⇒ 是**甲的那条 Agent 消息**把乙唤醒的（不是甲唤醒自己）。
    first_reply_seq = next(
        message["seq"] for message in messages(client, channel["id"]) if message["kind"] == "agent"
    )
    assert any(first_reply_seq in call["seqs"] for call in harness["seam"].calls_for(second["id"]))
    # 结论里也没有「甲被自己唤醒」这种轮次。
    assert all(
        not (record["member_id"] == first["id"] and record["base_seq"] == first_reply_seq)
        for record in outcomes(client, channel["id"])
    )


# ─── 穿透：点名一个未加入该频道的 Agent ────────────────────────────────────
def test_mentioning_an_outsider_wakes_it_without_joining(client: TestClient, harness: dict) -> None:
    """`@` 未加入频道的 Agent ⇒ 它照样收到唤醒（穿透），但**不因被点名而加入**。"""
    insider = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    harness["seam"].behaviors = {insider["id"]: IGNORE, outsider["id"]: IGNORE}
    channel = channel_with_agent(client, insider["id"], name="含一个成员")

    send(client, channel["id"], f"@{outsider['display_name']} 你来一句")
    drain(harness)

    assert len(harness["seam"].calls_for(outsider["id"])) == 1
    # 它选择不回复 ⇒ 不加入（成员关系只随真实回复发生）。
    member_ids = {
        member["id"] for member in client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    }
    assert outsider["id"] not in member_ids
    assert [message["kind"] for message in messages(client, channel["id"])] == ["human"]


def test_pierced_agent_that_replies_joins_and_leaves_a_system_event(
    client: TestClient, harness: dict
) -> None:
    """穿透唤醒的 Agent 若要回复：回复落库时**顺带加入**，并在流里追加一条 `system` 事件消息。"""
    insider = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    harness["seam"].behaviors = {insider["id"]: IGNORE}
    harness["seam"].replies[outsider["id"]] = "我来了，这条我接。"
    channel = channel_with_agent(client, insider["id"], name="含一个成员")

    send(client, channel["id"], f"@{outsider['display_name']} 你来一句")
    drain(harness)

    listed = messages(client, channel["id"])
    kinds = [message["kind"] for message in listed]
    # 人类消息 + system 事件消息 + Agent 回复；system 在回复**之前**（人类裁定）。
    assert kinds == ["human", "system", "agent"]
    system_message, reply = listed[1], listed[2]
    assert system_message["seq"] == reply["seq"] - 1
    assert system_message["author"]["id"] == outsider["id"]
    assert outsider["display_name"] in system_message["body"]
    assert "加入" in system_message["body"]
    assert reply["body"] == "我来了，这条我接。"
    # 成员关系新增该行。
    member_ids = {
        member["id"] for member in client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    }
    assert outsider["id"] in member_ids
    # 轮次结论里能看到这是穿透加入。
    record = next(item for item in outcomes(client, channel["id"]) if item["member_id"] == outsider["id"])
    assert record["status"] == "replied"
    assert "穿透" in record["reason"] and str(system_message["seq"]) in record["reason"]


def test_piercing_wake_consumes_a_jump_like_any_other(client: TestClient, harness: dict) -> None:
    """穿透不豁免预算：一条提及未加入者的 Agent 消息同样消耗 1 次跳的预算。"""
    agent = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    harness["seam"].replies[agent["id"]] = f"@{outsider['display_name']} 你来看下"
    harness["seam"].behaviors = {outsider["id"]: IGNORE}
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "请甲安排")
    drain(harness)

    # 甲的那条 Agent 消息穿透唤醒了未加入的局外人 ⇒ 它是一次跳，扣 1 次预算。
    assert len(harness["seam"].calls_for(outsider["id"])) == 1
    assert budget(client, channel["id"])["used"] == 1
    # 但穿透不会因为它被唤醒就加入（它自判忽略）。
    member_ids = {
        member["id"] for member in client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    }
    assert outsider["id"] not in member_ids


def test_piercing_is_suppressed_once_the_budget_is_exhausted(client: TestClient, harness: dict) -> None:
    """预算耗尽时穿透也被压制：未加入者拿到的结论同样是 `budget-exhausted`。

    构造：甲乙互相接力（每条回复都额外提及未加入的局外人）把预算推到耗尽。
    耗尽是「零投递」的极端形态，它直接证明「跳数以投递的唤醒为准」——`used` 恰好停在 3，
    而此后的 Agent 消息仍在落库。
    """
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    mention = f"@{outsider['display_name']}"
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} {mention} 继续",
        second["id"]: f"@{first['display_name']} {mention} 继续",
    }
    # 局外人永远沉默 ⇒ 它只被穿透唤醒、从不加入（否则拓扑会变）。
    harness["seam"].behaviors = {outsider["id"]: IGNORE}
    channel = _relay_channel(client, first, second)

    send(client, channel["id"], "开始接力")
    for _ in range(12):
        drain(harness)
        if harness["scheduler"].pending_count() == 0:
            break

    # 上界恰好用满：耗尽的那些消息零投递，因此 used 不会超过 3。
    assert budget(client, channel["id"])["used"] == 3
    # 穿透目标在耗尽时同样被压制，且留下了可追溯的结论。
    exhausted = [
        record for record in outcomes(client, channel["id"]) if record["status"] == "budget-exhausted"
    ]
    assert exhausted
    assert any(record["member_id"] == outsider["id"] for record in exhausted), (
        "被压制的穿透成员也必须留下可追溯的结论（穿透不豁免预算）"
    )
    assert all("预算已用尽" in record["reason"] for record in exhausted)
    # 未加入者始终没有变成成员：穿透不因被唤醒就加入。
    member_ids = {
        member["id"] for member in client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    }
    assert (
        outsider["id"] not in member_ids
    )  # ─── ignored 与 failed 可区分 ─────────────────────────────────────────────


def test_ignored_and_failed_are_distinguishable_in_the_same_channel(
    client: TestClient, harness: dict
) -> None:
    """「它不想说」与「它坏了」是两个事实：同频道内 ignored 与 failed 并存且可区分。"""
    quiet = create_agent(client, name="沉默者").json()["agent"]
    broken = create_agent(client, name="故障者").json()["agent"]
    harness["seam"].behaviors = {quiet["id"]: IGNORE, broken["id"]: FAIL}
    channel = client.post(
        "/api/channels",
        json={"name": "两种事实", "description": "", "member_ids": [quiet["id"], broken["id"]]},
    ).json()["channel"]

    send(client, channel["id"], "大家看一下（没有点名）")
    drain(harness)

    by_member = {record["member_id"]: record for record in outcomes(client, channel["id"])}
    assert by_member[quiet["id"]]["status"] == "ignored"
    assert by_member[broken["id"]]["status"] == "failed"
    assert by_member[quiet["id"]]["reason"] and by_member[broken["id"]]["reason"]
    # 状态点区分：忽略不是失败。
    assert client.get(f"/api/agents/{quiet['id']}/status").json()["status"] == "online"
    assert client.get(f"/api/agents/{broken['id']}/status").json()["status"] == "error"


def test_ignore_leaves_no_fake_acknowledgement(client: TestClient, harness: dict) -> None:
    """忽略只意味着「没有新消息」：不产生伪造的确认消息。"""
    agent = create_agent(client, name="沉默者").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = IGNORE
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "无关的一句")
    drain(harness)

    assert [message["kind"] for message in messages(client, channel["id"])] == ["human"]


# ─── 有界响应策略 B：硬上界 ≤ 3 ───────────────────────────────────────────
def _relay_channel(client: TestClient, first: dict, second: dict, name: str = "互相点名") -> dict:
    """两个互相 `@` 的 Agent：每次回复都点名对方，构成一个原本会永动的循环。"""
    return client.post(
        "/api/channels",
        json={"name": name, "description": "", "member_ids": [first["id"], second["id"]]},
    ).json()["channel"]


def test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch(
    client: TestClient, harness: dict
) -> None:
    """**硬上界（票面验收断言）**：两个互相 `@` 的 Agent，任一人类世代内跳数 ≤ 3。

    同时验证耗尽后的三件事：Agent 消息照常落库、零唤醒、轮次记 `budget-exhausted`。
    """
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = _relay_channel(client, first, second)

    send(client, channel["id"], "开始接力")
    epoch = client.get(f"/api/channels/{channel['id']}/messages").json()["channel"]["last_seq"]
    # 跑到不再有任何新唤醒（循环必须自己停下，不能被测试的循环上限截断）。
    for _ in range(12):
        drain(harness)
        if harness["scheduler"].pending_count() == 0:
            break

    assert budget(client, channel["id"]) == {
        "channel_id": channel["id"],
        "epoch_seq": epoch,
        "limit": 3,
        "used": 3,
        "remaining": 0,
    }
    # 跳数上界：世代内消耗预算的投递恰好 3 次（每次跳可能唤醒多个成员，但只算一次）。
    assert budget(client, channel["id"])["used"] <= 3
    # 耗尽之后仍照常落库的 Agent 消息存在（append-only，不可撤回），且它们零唤醒。
    listed = messages(client, channel["id"])
    agent_messages = [message for message in listed if message["kind"] == "agent"]
    assert len(agent_messages) >= 4, "耗尽后 Agent 消息照常落库，循环是被节制而不是被关掉"
    exhausted = [
        record for record in outcomes(client, channel["id"]) if record["status"] == "budget-exhausted"
    ]
    assert exhausted, "预算耗尽必须留下可追溯的轮次结论"
    assert all("预算已用尽" in record["reason"] for record in exhausted)
    # 耗尽**不是**出错：状态点仍是空闲（区分「不想说」/「它坏了」/「这个世代用完了」）。
    assert client.get(f"/api/agents/{first['id']}/status").json()["status"] == "online"
    assert client.get(f"/api/agents/{second['id']}/status").json()["status"] == "online"


def test_a_new_human_message_resets_the_budget_and_the_relay_works_again(
    client: TestClient, harness: dict
) -> None:
    """人类消息随后落库即重置预算（回合重新可用）——循环被节制而不是永久关闭。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = _relay_channel(client, first, second)

    send(client, channel["id"], "第一代开始")
    for _ in range(12):
        drain(harness)
        if harness["scheduler"].pending_count() == 0:
            break
    assert budget(client, channel["id"])["used"] == 3

    # 人类再发一条 ⇒ 新世代、预算重置为满额、往返重新可用。
    send(client, channel["id"], "第二代开始")
    state = budget(client, channel["id"])
    assert state["used"] == 0 and state["remaining"] == 3
    assert state["epoch_seq"] > state["limit"]  # epoch 就是那条新人类消息的 seq

    drain(harness)
    assert budget(client, channel["id"])["used"] >= 1, "重置后必须有真实的 agent→agent 投递"


def test_multiple_targets_from_one_message_cost_only_one_jump(client: TestClient, harness: dict) -> None:
    """一条消息即使唤醒多个 Agent，也只消耗 **1** 次预算（它就是一次跳）。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    third = create_agent(client, name="丙").json()["agent"]
    # 只有甲回复（点名两人）；乙丙沉默，于是这一段没有后续跳。
    harness["seam"].replies[first["id"]] = f"@{second['display_name']} @{third['display_name']} 你俩看下"
    harness["seam"].behaviors = {second["id"]: IGNORE, third["id"]: IGNORE}
    channel = client.post(
        "/api/channels",
        json={"name": "多个目标", "description": "", "member_ids": [first["id"], second["id"], third["id"]]},
    ).json()["channel"]

    send(client, channel["id"], "请甲协调")
    drain(harness)

    # 甲的回复唤醒了乙与丙（两个投递）→ 仍然只是 1 次跳。
    assert budget(client, channel["id"])["used"] == 1
    assert {call["member_id"] for call in harness["seam"].calls} >= {first["id"], second["id"], third["id"]}


def test_system_event_message_neither_wakes_nor_consumes_budget(client: TestClient, harness: dict) -> None:
    """穿透加入产生的 system 事件消息**不唤醒、不消耗预算**（它不是对话回合）。

    构造使穿越只发生在人类世代内：人类消息先被一个穿透不涉及的 insider 读掉。
    """
    insider = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    harness["seam"].behaviors = {insider["id"]: IGNORE}
    harness["seam"].replies[outsider["id"]] = "我来了"
    channel = channel_with_agent(client, insider["id"], name="含一个成员")

    send(client, channel["id"], f"@{outsider['display_name']} 来一句")
    drain(harness)

    # 这一世代里的跳只有一次：局外人那条穿透回复。
    assert budget(client, channel["id"])["used"] == 1
    listed = messages(client, channel["id"])
    assert [message["kind"] for message in listed] == ["human", "system", "agent"]
    # system 消息**没有**追加任何轮次结论：结论只属于被唤醒的那两个成员。
    assert {record["member_id"] for record in outcomes(client, channel["id"])} == {
        insider["id"],
        outsider["id"],
    }
    # 也没有因为 system 消息再唤醒一轮：唤醒过的人恰好就是那两个。
    assert {call["member_id"] for call in harness["seam"].calls} == {insider["id"], outsider["id"]}


def test_a_second_piercing_join_is_skipped_and_the_agent_stays_joined(
    client: TestClient, harness: dict
) -> None:
    """已经加入之后不再重复写 system 事件（成员变化只在**真正新增**时发生）。"""
    insider = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    harness["seam"].behaviors = {insider["id"]: IGNORE}
    channel = channel_with_agent(client, insider["id"])

    # 第一次穿透回复 ⇒ 加入 + 一条 system 消息。
    harness["seam"].replies[outsider["id"]] = "第一次回复"
    send(client, channel["id"], f"@{outsider['display_name']} 来一句")
    drain(harness)
    assert [message["kind"] for message in messages(client, channel["id"])] == ["human", "system", "agent"]

    # 第二次：它已经是成员，不再写第二条 system 消息，但照常回复。
    harness["seam"].replies[outsider["id"]] = f"@{insider['display_name']} 第二次回复"
    send(client, channel["id"], f"@{outsider['display_name']} 再来一句")
    drain(harness)

    kinds = [message["kind"] for message in messages(client, channel["id"])]
    assert kinds.count("system") == 1, "成员变化只随真正的新增发生"
    assert kinds.count("agent") == 2


def test_budget_state_is_persisted_and_survives_restart(launched: dict) -> None:
    """预算 used/remaining 是**后端持久事实**：重启后与压制行为一致。"""
    client = launched["client"]
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    launched["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = _relay_channel(client, first, second)

    send(client, channel["id"], "开始")
    for _ in range(12):
        drain(launched)
        if launched["scheduler"].pending_count() == 0:
            break
    before = budget(client, channel["id"])
    assert before["used"] == 3 and before["remaining"] == 0

    after = launched["restart"]()

    # 重启后读到的仍是同一条持久事实：世代未变、用量仍是满额。
    assert budget(after, channel["id"]) == before
    # 因此这个世代里的后续 Agent 消息仍会被压制（压制读的就是这个计数）。
    assert budget(after, channel["id"])["used"] >= 3
    # 重启后预算重置规则同样生效：新人类消息开启新世代、用量归零。
    send(after, channel["id"], "新世代")
    reset = budget(after, channel["id"])
    assert reset["used"] == 0 and reset["remaining"] == 3
    assert reset["epoch_seq"] > before["epoch_seq"]
    assert launched["seam"].calls == []  # 还没跑任何一轮


# ─── 并发合并 ─────────────────────────────────────────────────────────────
def test_repeated_wakes_during_a_round_are_merged_into_one(client: TestClient, harness: dict) -> None:
    """同一成员最多一轮 in-flight：进行中的重复唤醒合并成**一次**，不并行、不重放。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    harness["seam"].behaviors[agent["id"]] = DELAY
    harness["seam"].block()

    send(client, channel["id"], "第一条：开始一轮")
    runner = threading.Thread(target=harness["scheduler"].drain)
    runner.start()
    for _ in range(200):
        if harness["seam"].calls_for(agent["id"]):
            break
        time.sleep(0.01)
    assert harness["seam"].calls_for(agent["id"]), "第一轮没有进入 in-flight"

    # 一轮进行中到达两条重复唤醒：合并进那一轮。
    send(client, channel["id"], "第二条：还在跑")
    send(client, channel["id"], "第三条：还在跑")
    assert harness["scheduler"].drain() == [None, None]
    # 不并行：模型只被调用一次。
    assert len(harness["seam"].calls_for(agent["id"])) == 1

    harness["seam"].release()
    runner.join(timeout=10)
    assert not runner.is_alive()

    merged = [record for record in outcomes(client, channel["id"]) if record["status"] == "merged"]
    assert len(merged) == 1, "合并为一次 = 恰好一条 merged 记录"
    assert merged[0]["merged"] is True
    # 「以最大 seq 为准」：被合并唤醒的最大 seq（3）进 base_seq。
    assert merged[0]["base_seq"] == 3
    assert "合并" in merged[0]["reason"]
    # 被合并的重复唤醒不额外计数、也不额外消耗预算。
    assert merged[0]["consumed_budget"] is False
    assert budget(client, channel["id"])["used"] == 0


def test_merged_wakes_are_not_replayed_but_not_swallowed(client: TestClient, harness: dict) -> None:
    """「不重放」≠「永久吞掉」：被合并的消息仍在消费游标之后，下一次新唤醒一并读到。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    harness["seam"].behaviors[agent["id"]] = DELAY
    harness["seam"].replies[agent["id"]] = "第一轮回复"
    harness["seam"].block()

    send(client, channel["id"], "第一条")
    runner = threading.Thread(target=harness["scheduler"].drain)
    runner.start()
    for _ in range(200):
        if harness["seam"].calls_for(agent["id"]):
            break
        time.sleep(0.01)
    assert harness["seam"].calls_for(agent["id"])

    send(client, channel["id"], "第二条（被合并）")
    assert harness["scheduler"].drain() == [None]
    harness["seam"].release()
    runner.join(timeout=10)

    # 第一轮只读到了「第一条」——第二条被合并，没有被单独重放。
    assert harness["seam"].calls_for(agent["id"])[0]["bodies"] == ["第一条"]

    # 下一次**新的**唤醒（人类新消息）会把被合并那条一并读到。
    harness["seam"].behaviors[agent["id"]] = REPLY
    harness["seam"].replies[agent["id"]] = "第三轮回复"
    send(client, channel["id"], "第三条（新唤醒）")
    drain(harness)

    bodies = harness["seam"].calls_for(agent["id"])[1]["bodies"]
    assert "第二条（被合并）" in bodies
    assert "第三条（新唤醒）" in bodies


def test_a_zero_delivery_agent_message_does_not_consume_budget(client: TestClient, harness: dict) -> None:
    """**零投递的 Agent 消息不消耗预算**（「跳数以投递的唤醒为准」的可观察形态）。

    可确定构造的两个零投递形态：

    1. **唯一目标正在 in-flight**（`任何目标不在 in-flight` 不成立）：这里用人类消息连发两次
       把同一个成员反复唤醒，窗口内的 Agent 消息都不是「一次跳」。
    2. **作者是唯一成员**（唤醒集为空）：回复不构成 agent→agent 跳。

    单成员频道里两种形态同时具备，因此 `used` 在整个过程中必须恒为 0（人类消息不消耗预算）。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "第一条")
    drain(harness)
    assert any(message["kind"] == "agent" for message in messages(client, channel["id"]))
    assert budget(client, channel["id"])["used"] == 0

    # 再跑一轮：它的回复仍然唤醒不到任何其它 Agent（唤醒集为空 ⇒ 不是一次跳）。
    send(client, channel["id"], "第二条")
    drain(harness)
    assert budget(client, channel["id"])["used"] == 0
    # 两个世代的预算都未被消耗，但消息确实在增长（不是「什么都不干」）。
    assert len([message for message in messages(client, channel["id"]) if message["kind"] == "agent"]) >= 2


# ─── 失败隔离 ─────────────────────────────────────────────────────────────
def test_one_agents_failure_does_not_stop_others_or_human_sending(client: TestClient, harness: dict) -> None:
    """一个 Agent 的失败/超时不影响其它 Agent 轮次，也不影响人类继续发消息。"""
    broken = create_agent(client, name="坏的").json()["agent"]
    working = create_agent(client, name="好的").json()["agent"]
    harness["seam"].behaviors = {broken["id"]: FAIL}
    channel = client.post(
        "/api/channels",
        json={"name": "隔离", "description": "", "member_ids": [broken["id"], working["id"]]},
    ).json()["channel"]

    send(client, channel["id"], "一起看下")
    drain(harness)

    by_member = {record["member_id"]: record["status"] for record in outcomes(client, channel["id"])}
    assert by_member[broken["id"]] == "failed"
    assert by_member[working["id"]] == "replied"
    # 失败轮次不写半条消息：流里没有属于坏成员的内容。
    assert all(message["author"]["id"] != broken["id"] for message in messages(client, channel["id"]))
    assert client.get(f"/api/agents/{broken['id']}/status").json()["status"] == "error"
    assert client.get(f"/api/agents/{working['id']}/status").json()["status"] == "online"

    # 人类发送路径不受影响。
    follow_up = send(client, channel["id"], "坏的那个不影响我继续发")
    assert follow_up.status_code == 201


def test_unavailable_agent_reason_is_readable_when_mentioned(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """缺凭证/未确认的 Agent 被 `@` 时，轮次 reason 必须可读（不安静地什么都不做）。"""
    from tests.conftest import REMOTE_MODEL_ID

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    agent = create_agent(client, name="远端", model_id=REMOTE_MODEL_ID).json()["agent"]
    client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 看一下")
    drain(harness)

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "failed"
    assert "dashscope" in record["reason"] and "DASHSCOPE_API_KEY" in record["reason"]
    assert client.get(f"/api/agents/{agent['id']}/status").json()["status"] == "offline"


# ─── 重启恢复 ─────────────────────────────────────────────────────────────
def test_members_messages_outcomes_and_budget_survive_restart(launched: dict) -> None:
    """刷新/重启后：成员关系、消息、轮次结果与预算状态全部从库恢复。"""
    client = launched["client"]
    insider = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    launched["seam"].behaviors = {insider["id"]: IGNORE}
    launched["seam"].replies[outsider["id"]] = "穿透回复"
    channel = channel_with_agent(client, insider["id"])

    send(client, channel["id"], f"@{outsider['display_name']} 来一句")
    drain(launched)
    before_messages = messages(client, channel["id"])
    before_outcomes = outcomes(client, channel["id"])
    before_members = client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    before_budget = budget(client, channel["id"])

    after = launched["restart"]()

    assert messages(after, channel["id"]) == before_messages
    assert outcomes(after, channel["id"]) == before_outcomes
    assert after.get(f"/api/channels/{channel['id']}/members").json()["members"] == before_members
    assert budget(after, channel["id"]) == before_budget
    # 穿透加入产生的 system 消息也在重启后仍可追溯。
    assert [message["kind"] for message in messages(after, channel["id"])] == ["human", "system", "agent"]


def test_in_flight_round_is_not_replayed_after_restart(launched: dict) -> None:
    """后端重启后进行中的轮次**不被自动重放**（只等下一次唤醒）。"""
    client = launched["client"]
    agent = create_agent(client, name="小明").json()["agent"]
    launched["seam"].behaviors[agent["id"]] = DELAY
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "第一轮开始")
    launched["seam"].block()
    runner = threading.Thread(target=launched["scheduler"].drain)
    runner.start()
    for _ in range(200):
        if launched["seam"].calls_for(agent["id"]):
            break
        time.sleep(0.01)
    assert launched["seam"].calls_for(agent["id"]), "第一轮没有进入 in-flight"

    after = launched["restart"]()

    assert launched["scheduler"].pending_count() == 0
    assert launched["seam"].call_count == 0
    assert outcomes(after, channel["id"]) == []
    assert [message["kind"] for message in messages(after, channel["id"])] == ["human"]
    assert after.get(f"/api/agents/{agent['id']}/status").json()["status"] == "online"

    launched["seam"].release()
    runner.join(timeout=10)


# ─── 未读与 system 口径 ───────────────────────────────────────────────────
def test_agent_replies_count_toward_unread_and_the_system_event_does_not(
    client: TestClient, harness: dict
) -> None:
    """**用真实穿透加入事件取证**：Agent 回复计入未读，穿透加入的 `system` 事件消息不计入。"""
    insider = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    harness["seam"].behaviors = {insider["id"]: IGNORE}
    harness["seam"].replies[outsider["id"]] = "穿透回复"
    channel = channel_with_agent(client, insider["id"], name="看这个频道")

    send(client, channel["id"], f"@{outsider['display_name']} 来一句")
    drain(harness)

    listed = messages(client, channel["id"])
    kinds = [message["kind"] for message in listed]
    assert kinds == ["human", "system", "agent"], "需要真实的穿透加入事件消息"

    # owner 从未标读：人类消息 + Agent 回复 = 2；system 不计入（否则会虚增成 3）。
    state = {item["id"]: item for item in client.get("/api/channels").json()["channels"]}
    assert state[channel["id"]]["last_seq"] == 3
    assert state[channel["id"]]["unread_count"] == 2
    assert client.get("/api/workspace").json()["unread_total"] == 2

    # 正在查看该频道时到达的回复立即已读（用同一 HTTP 面模拟前端的动作）。
    client.post(f"/api/channels/{channel['id']}/read", json={"seq": 3})
    assert {item["id"]: item for item in client.get("/api/channels").json()["channels"]}[channel["id"]][
        "unread_count"
    ] == 0


def test_system_event_unread_semantics_survive_restart(launched: dict) -> None:
    """重启后 system 口径不变：未读仍是「人类 + Agent」两条。"""
    client = launched["client"]
    insider = create_agent(client, name="甲").json()["agent"]
    outsider = create_agent(client, name="局外人").json()["agent"]
    launched["seam"].behaviors = {insider["id"]: IGNORE}
    launched["seam"].replies[outsider["id"]] = "穿透回复"
    channel = channel_with_agent(client, insider["id"])
    send(client, channel["id"], f"@{outsider['display_name']} 来一句")
    drain(launched)

    after = launched["restart"]()
    state = {item["id"]: item for item in after.get("/api/channels").json()["channels"]}
    assert state[channel["id"]]["unread_count"] == 2


# ─── 不可变性（append-only 不被打开） ─────────────────────────────────────
def test_no_message_edit_or_delete_path_even_after_budget_exhaustion(
    client: TestClient, harness: dict
) -> None:
    """预算耗尽/忽略/失败/穿透加入都不产生编辑或删除路径：消息仍 append-only。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = _relay_channel(client, first, second)
    send(client, channel["id"], "开始")
    for _ in range(12):
        drain(harness)
        if harness["scheduler"].pending_count() == 0:
            break

    before = messages(client, channel["id"])
    assert any(record["status"] == "budget-exhausted" for record in outcomes(client, channel["id"]))
    # 耗尽世代之后，Agent 消息仍在继续落库（append-only 的证明）。
    assert len([message for message in before if message["kind"] == "agent"]) >= 4

    for message in before:
        path = f"/api/channels/{channel['id']}/messages/{message['seq']}"
        assert client.delete(path).status_code == 404
        assert client.put(path, json={"body": "改写"}).status_code == 404
        assert client.patch(path, json={"body": "改写"}).status_code == 404
    # 流没有变化。
    assert messages(client, channel["id"]) == before

    # 预算也只读：人类面没有写入它的端点。
    for method in ("put", "patch", "delete", "post"):
        assert getattr(client, method)(f"/api/channels/{channel['id']}/budget").status_code == 404
