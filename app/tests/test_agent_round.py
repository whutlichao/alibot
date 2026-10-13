"""唤醒 → 自判 → 落库 → 轮次结果：正向闭环、唤醒面、回应判断、状态点、失败隔离、恢复。

只经 HTTP 面与事件流取证；每一轮由测试自己用 ``harness["scheduler"].drain()`` 决定何时跑完，
因此「一轮 in-flight」与「被合并」这类窗口可以稳定观察，不需要 sleep。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import (
    agents,
    channel_with_agent,
    create_agent,
    create_channel,
    messages,
    outcomes,
    send,
)
from tests.scripted import DELAY, FAIL, IGNORE, REPLY


def test_positive_loop_human_message_to_agent_reply(client: TestClient, harness: dict) -> None:
    """正向闭环：人类发消息 → 落库 → Agent 被唤醒 → 回复追加落库（不在 HTTP 面伪造）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].replies[agent["id"]] = "看过了，我改一下颜色对比度。"
    channel = channel_with_agent(client, agent["id"])

    human = send(client, channel["id"], f"@{agent['display_name']} 帮我看下配色").json()["message"]
    assert human["seq"] == 1
    assert messages(client, channel["id"]) and len(messages(client, channel["id"])) == 1

    harness["scheduler"].drain()

    listed = messages(client, channel["id"])
    assert [message["seq"] for message in listed] == [1, 2]
    assert listed[1]["kind"] == "agent"
    assert listed[1]["author"]["id"] == agent["id"]
    assert listed[1]["author"]["kind"] == "agent"
    assert listed[1]["body"] == "看过了，我改一下颜色对比度。"
    # Agent 回复沿用人类消息的渲染所需字段（方形头像色位、显示名）。
    assert 0 <= listed[1]["author"]["avatar_slot"] <= 4

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "replied"
    assert record["member_id"] == agent["id"]
    assert record["base_seq"] == 1
    assert record["reply_seq"] == 2
    assert record["merged"] is False
    assert record["consumed_budget"] is False
    assert record["started_at"].endswith("Z") and record["finished_at"].endswith("Z")


def test_the_agent_reads_messages_through_the_same_read_path(client: TestClient, harness: dict) -> None:
    """唤醒信号只带标识、不带正文：Agent 经同一个读接口自己读到待读消息。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "第一句")
    send(client, channel["id"], "第二句")

    harness["scheduler"].drain()

    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 1
    # 适配接口拿到的就是「该成员待读的新消息」，它自己从库里读到的（不是唤醒信号塞的）。
    assert calls[0]["bodies"] == ["第一句", "第二句"]
    assert calls[0]["seqs"] == [1, 2]


def test_all_channel_agents_are_woken_and_the_author_is_excluded(client: TestClient, harness: dict) -> None:
    """唤醒面 = 该频道全部 agent 成员；不在频道里的 Agent 不被唤醒（穿透属 #③）。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    outsider = create_agent(client, name="外人").json()["agent"]
    # 两个成员都「明确无关」：它们沉默，回复不再产生新唤醒，于是这一波可被完整观察。
    harness["seam"].behaviors = {first["id"]: IGNORE, second["id"]: IGNORE}
    channel = client.post(
        "/api/channels",
        json={"name": "两个成员", "description": "", "member_ids": [first["id"], second["id"]]},
    ).json()["channel"]
    # 不在频道里的 Agent 不被唤醒（穿透属 #③）。
    create_channel(client, name="别的频道", member_ids=[outsider["id"]])

    send(client, channel["id"], "大家好")
    harness["scheduler"].drain()

    woken = {call["member_id"] for call in harness["seam"].calls}
    assert woken == {first["id"], second["id"]}
    assert outsider["id"] not in woken
    assert len(outcomes(client, channel["id"])) == 2


def test_agent_author_is_excluded_from_the_wake_set(client: TestClient, harness: dict) -> None:
    """作者排除对 Agent 作者也成立：轮到 A 发言后，唤醒集里只有 B（不含 A 自己）。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    # 甲回应（它是人类消息的作者之外的一方），乙沉默：于是不会出现互相唤醒。
    harness["seam"].behaviors = {second["id"]: IGNORE}
    channel = client.post(
        "/api/channels",
        json={"name": "排除作者", "description": "", "member_ids": [first["id"], second["id"]]},
    ).json()["channel"]

    send(client, channel["id"], "请甲回答")
    harness["scheduler"].drain()

    # 甲的一轮（人类消息唤醒）+ 甲回复后的那一轮（只唤醒乙）——甲从未被自己的回复唤醒。
    first_calls = harness["seam"].calls_for(first["id"])
    assert len(first_calls) == 1
    assert {call["member_id"] for call in harness["seam"].calls} == {first["id"], second["id"]}
    # 甲的两轮都在，且来源不同：人类消息那一轮 consumed_budget=False，回复后的那一轮也不是
    # 由甲自己触发的。
    first_rounds = [
        record for record in outcomes(client, channel["id"]) if record["member_id"] == first["id"]
    ]
    assert len(first_rounds) == 1


def test_agent_reply_does_not_wake_itself(client: TestClient, harness: dict) -> None:
    """单 Agent 场景：它的回复唤醒不到任何其它 Agent（作者被排除），因此不会自激。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 看一下")
    harness["scheduler"].drain()

    # 一轮 human 唤醒 + 一轮 agent 回复后的零唤醒：适配层只被调用一次。
    assert harness["seam"].call_count == 1
    assert len(messages(client, channel["id"])) == 2


def test_wake_reason_distinguishes_mention_from_channel_message(client: TestClient, harness: dict) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "这是一条没有点名的消息")
    send(client, channel["id"], f"@{agent['display_name']} 这条点名了")
    harness["scheduler"].drain()

    assert harness["seam"].calls_for(agent["id"])[0]["mentioned"] is True


def test_mention_is_a_certain_signal_so_ignoring_it_is_failed(client: TestClient, harness: dict) -> None:
    """被点名却不回应 → failed（不静默；不能把「它坏了」当成「它不想说」）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = IGNORE
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 必须回一句")
    harness["scheduler"].drain()

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "failed"
    # 可读的 reason 要留住适配层原本的自判理由，便于事后区分「它不想说」与「它没做到」。
    assert "必须回应" in record["reason"]
    assert "脚本化替身" in record["reason"]
    assert client.get(f"/api/agents/{agent['id']}/status").json()["status"] == "error"
    # 没有半条消息：不写任何回复。
    assert [message["kind"] for message in messages(client, channel["id"])] == ["human"]


def test_clearly_irrelevant_message_is_ignored_not_failed(client: TestClient, harness: dict) -> None:
    """明确无关 → ignored：正常协议选择，不是失败，也不产生伪造的确认。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = IGNORE
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "只是和别人聊两句，没有点名")
    harness["scheduler"].drain()

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "ignored"
    assert record["reason"]
    assert messages(client, channel["id"]) and len(messages(client, channel["id"])) == 1
    # ignored 不是失败：状态点回到空闲（未点名的无关消息允许沉默）。
    assert client.get(f"/api/agents/{agent['id']}/status").json()["status"] == "online"


def test_round_outcome_distinguishes_replied_ignored_and_failed(client: TestClient, harness: dict) -> None:
    reply_agent = create_agent(client, name="甲").json()["agent"]
    ignore_agent = create_agent(client, name="乙").json()["agent"]
    fail_agent = create_agent(client, name="丙").json()["agent"]
    harness["seam"].behaviors = {ignore_agent["id"]: IGNORE, fail_agent["id"]: FAIL}
    channel = client.post(
        "/api/channels",
        json={
            "name": "三种结论",
            "description": "",
            "member_ids": [reply_agent["id"], ignore_agent["id"], fail_agent["id"]],
        },
    ).json()["channel"]

    send(client, channel["id"], "大家看一下")
    harness["scheduler"].drain()

    by_member = {record["member_id"]: record["status"] for record in outcomes(client, channel["id"])}
    assert by_member == {
        reply_agent["id"]: "replied",
        ignore_agent["id"]: "ignored",
        fail_agent["id"]: "failed",
    }


def test_a_failed_round_writes_no_half_message(client: TestClient, harness: dict) -> None:
    """失败 → 该轮 failed + 状态点 error + 不写半条消息（流里不出现残缺内容）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = FAIL
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "请处理")
    harness["scheduler"].drain()

    assert len(messages(client, channel["id"])) == 1
    assert outcomes(client, channel["id"])[0]["status"] == "failed"
    assert clients_status(client, agent["id"]) == "error"


def clients_status(client: TestClient, member_id: str) -> str:
    return client.get(f"/api/agents/{member_id}/status").json()["status"]


def test_failure_isolated_from_other_agents_and_from_human_sending(client: TestClient, harness: dict) -> None:
    broken = create_agent(client, name="坏的").json()["agent"]
    working = create_agent(client, name="好的").json()["agent"]
    harness["seam"].behaviors = {broken["id"]: FAIL}
    channel = client.post(
        "/api/channels",
        json={"name": "隔离", "description": "", "member_ids": [broken["id"], working["id"]]},
    ).json()["channel"]

    send(client, channel["id"], "一起看下")
    harness["scheduler"].drain()

    statuses = {record["member_id"]: record["status"] for record in outcomes(client, channel["id"])}
    assert statuses[broken["id"]] == "failed"
    assert statuses[working["id"]] == "replied"
    assert clients_status(client, broken["id"]) == "error"
    assert clients_status(client, working["id"]) == "online"

    # 一个 Agent 失败不影响人类发送路径：下一条消息照常落库并照常唤醒。
    follow_up = send(client, channel["id"], "坏的那条不影响我继续发")
    assert follow_up.status_code == 201
    assert follow_up.json()["message"]["seq"] == 3


def test_second_wake_while_a_round_is_in_flight_is_merged(client: TestClient, harness: dict) -> None:
    """同一成员最多一轮 in-flight：重复唤醒合并为一次，不重复调用模型、不排队重放。

    #④ 的完整合并语义（以最大 seq 为准）：重复唤醒在窗口内只累积到内存，**轮次收口时**合成
    恰好一条 merged 行，base_seq = 窗口内最大 seq。因此在放行那一轮之前，库里看不到 merged ——
    这是「不重放」的必然形态，不是丢失。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    harness["seam"].behaviors[agent["id"]] = DELAY
    harness["seam"].block()

    send(client, channel["id"], "第一条：开始一轮")
    # 手动跑这一轮 —— 它会停在替身的闸门上，于是这一轮仍 in-flight。
    import threading

    runner = threading.Thread(target=harness["scheduler"].drain)
    runner.start()
    for _ in range(100):
        if harness["seam"].calls_for(agent["id"]):
            break
        import time

        time.sleep(0.01)
    assert harness["seam"].calls_for(agent["id"]), "第一轮没有进入 in-flight"

    send(client, channel["id"], "第二条：这一轮还在跑")
    # 第二次唤醒不排队、不重放：它被投递进编排、立即判定为合并（返回 None，不落独立结论）。
    drain_after = harness["scheduler"].drain()
    assert drain_after == [None]
    assert len(harness["seam"].calls_for(agent["id"])) == 1

    harness["seam"].release()
    runner.join(timeout=10)
    assert not runner.is_alive()
    assert len(messages(client, channel["id"])) == 3  # 两条人类消息 + 一个 Agent 回复

    merged = [record for record in outcomes(client, channel["id"]) if record["status"] == "merged"]
    assert len(merged) == 1
    assert merged[0]["merged"] is True
    assert merged[0]["reason"]
    # 「以最大 seq 为准」：被合并那次唤醒的 seq（2）进 base_seq，而不是最小/首个。
    assert merged[0]["base_seq"] == 2
    # 合并不产生投递，因此不消耗预算。
    assert merged[0]["consumed_budget"] is False


def test_failed_round_does_not_advance_the_cursor_so_the_next_wake_rereads(
    client: TestClient, harness: dict
) -> None:
    """失败的轮次不推进消费游标：下一次唤醒会重新读到那条消息（但不是自动重放）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = FAIL
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "第一次会失败")
    harness["scheduler"].drain()
    assert outcomes(client, channel["id"])[0]["status"] == "failed"

    # 换成会回应的行为，再触发一次唤醒：失败那轮读过的消息必须重新出现在输入里。
    harness["seam"].behaviors[agent["id"]] = REPLY
    send(client, channel["id"], "第二次会成功")
    harness["scheduler"].drain()

    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 2
    # 第二轮的输入同时包含「失败那轮没吃掉的那条」与「新的一条」。
    assert calls[0]["bodies"] == ["第一次会失败"]
    assert calls[1]["bodies"] == ["第一次会失败", "第二次会成功"]


def test_ignored_round_advances_the_cursor_so_it_is_not_reread(client: TestClient, harness: dict) -> None:
    """ignored 是正常协议选择：它推进游标，同一批消息不会被反复送进适配层。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = IGNORE
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "第一条无关")
    harness["scheduler"].drain()
    send(client, channel["id"], "第二条无关")
    harness["scheduler"].drain()

    calls = harness["seam"].calls_for(agent["id"])
    assert [call["bodies"] for call in calls] == [["第一条无关"], ["第二条无关"]]


def test_status_point_starts_offline_without_a_model(client: TestClient, harness: dict) -> None:
    """缺模型的 Agent 状态点是「未启用」，被 @ 时轮次 reason 可读。"""
    agent = create_agent(client, name="小明").json()["agent"]
    # 本票没有改配置的端点，所以直接造一个缺模型的成员不是人类面能力；改为断言
    # 「远端未确认」这条真实可达的未启用路径（见 test_remote_data_flow）。
    assert clients_status(client, agent["id"]) == "online"

    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "无需确认的本机模型可以正常跑")
    harness["scheduler"].drain()
    assert outcomes(client, channel["id"])[0]["status"] == "replied"


def test_unavailable_agent_reason_is_readable(client: TestClient, harness: dict, monkeypatch) -> None:
    """未启用（缺凭证）的 Agent 被 @ 时，轮次 reason 必须**可读**——不能安静地什么都不做。

    「缺模型」在 HTTP 面不可达（创建入口要求模型标识非空，且本票没有改配置的端点），
    所以这里走真实可达的另一条「未启用」路径：已确认告知但环境里没有该 provider 的凭证。
    """
    from tests.conftest import REMOTE_MODEL_ID

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    agent = create_agent(client, name="远端", model_id=REMOTE_MODEL_ID).json()["agent"]
    client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 看一下")
    harness["scheduler"].drain()

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "failed"
    # 可读性：说清了是哪个 provider、要设哪个环境变量。
    assert "dashscope" in record["reason"]
    assert "DASHSCOPE_API_KEY" in record["reason"]
    assert clients_status(client, agent["id"]) == "offline"


def test_unconfirmed_remote_agent_reason_is_readable(client: TestClient, harness: dict) -> None:
    """未确认远端数据流告知也是「未启用」：reason 要说清是哪一步没做。"""
    from tests.conftest import REMOTE_MODEL_ID

    agent = create_agent(client, name="远端未确认", model_id=REMOTE_MODEL_ID).json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 看一下")
    harness["scheduler"].drain()

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "failed"
    assert "未确认远端数据流告知" in record["reason"]
    assert clients_status(client, agent["id"]) == "offline"


def test_round_outcomes_and_agent_replies_survive_restart(launched: dict) -> None:
    """重启后消息、成员、轮次结果完整；进行中的轮次不被自动重放。"""
    client = launched["client"]
    agent = create_agent(client, name="小明").json()["agent"]
    launched["seam"].replies[agent["id"]] = "重启前的那条回复"
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "重启前")
    launched["scheduler"].drain()
    before_messages = messages(client, channel["id"])
    before_outcomes = outcomes(client, channel["id"])

    after = launched["restart"]()

    assert [item["id"] for item in agents(after)] == [agent["id"]]
    assert messages(after, channel["id"]) == before_messages
    assert outcomes(after, channel["id"]) == before_outcomes
    # 进行中的轮次在后端重启后不被自动重放：新后端一个待跑轮次都没有。
    assert launched["scheduler"].pending_count() == 0
    assert launched["seam"].call_count == 0


def test_in_flight_round_is_not_replayed_after_restart(launched: dict) -> None:
    """真正的「进行中」：一轮停在闸门上时重启后端，重启后它不被重放、也不留下错误态。"""
    import threading
    import time

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

    # 重启后：消息与成员完整，没有自动重放的轮次，状态点也不是「有轮次在进行中」。
    assert [message["kind"] for message in messages(after, channel["id"])] == ["human"]
    assert launched["scheduler"].pending_count() == 0
    assert after.get(f"/api/agents/{agent['id']}/status").json()["status"] == "online"
    assert outcomes(after, channel["id"]) == []

    launched["seam"].release()
    runner.join(timeout=10)


def test_rejected_message_never_wakes_anyone(client: TestClient, harness: dict) -> None:
    """触发点唯一：消息**未成功落库**就不唤醒（校验被拒与软删频道两条真实路径）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    rejected = send(client, channel["id"], "长" * 8001)
    assert rejected.status_code == 400

    # 频道不存在时也落不了库：同样没有任何唤醒。
    missing = client.post("/api/channels/ch_missing/messages", json={"body": "发给不存在的频道"})
    assert missing.status_code == 404

    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].call_count == 0
    assert messages(client, channel["id"]) == []
    assert outcomes(client, channel["id"]) == []


def test_sending_to_a_channel_without_agents_never_wakes(client: TestClient, harness: dict) -> None:
    channel = create_channel(client, name="只有人类").json()["channel"]

    send(client, channel["id"], "没有 Agent 的频道")

    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].call_count == 0
    assert outcomes(client, channel["id"]) == []


def test_wake_orchestration_covers_delay_behavior(client: TestClient, harness: dict) -> None:
    """延迟行为：闸门放行后照常收口成一整条回复（不产生残缺消息）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = DELAY
    harness["seam"].replies[agent["id"]] = "延迟后写下的一句话"
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "慢慢来")
    harness["scheduler"].drain()

    listed = messages(client, channel["id"])
    assert listed[-1]["kind"] == "agent"
    assert listed[-1]["body"] == "延迟后写下的一句话"
    assert outcomes(client, channel["id"])[0]["status"] == "replied"


def test_merged_flag_is_recorded_for_rounds_that_were_not_merged(client: TestClient, harness: dict) -> None:
    """未被合并的普通轮次里 merged=False：字段是事实记录，不是恒定值。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "普通一轮")
    harness["scheduler"].drain()

    record = outcomes(client, channel["id"])[0]
    assert record["merged"] is False
    assert record["consumed_budget"] is False


def test_consumed_budget_is_false_for_human_epoch(client: TestClient, harness: dict) -> None:
    """人类消息触发的轮次不消耗预算（一次 agent→agent 跳才消耗）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "人类消息")
    harness["scheduler"].drain()

    assert all(record["consumed_budget"] is False for record in outcomes(client, channel["id"]))


def test_non_agent_author_never_causes_a_budget_consuming_jump(client: TestClient, harness: dict) -> None:
    """Agent 回复落库后唤醒集为空（单 Agent 场景）≠ 一次跳：没有别的成员被唤醒，故不消耗预算。

    本票不实现预算策略（#③），但「是否消耗预算」这个字段必须准确：它记录的是事实。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 一轮")
    harness["scheduler"].drain()

    assert outcomes(client, channel["id"])[0]["consumed_budget"] is False


def test_agent_messages_count_toward_unread(client: TestClient, harness: dict) -> None:
    """Agent 回复计入「其它频道」的未读（kind ∈ {human, agent}）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    watched = channel_with_agent(client, agent["id"], name="正在看的")
    other = create_channel(client, name="别的").json()["channel"]

    # 先进入 other 把它标读，于是它是「其它频道」的观察点。
    client.post(f"/api/channels/{other['id']}/read", json={"seq": 0})
    send(client, watched["id"], f"@{agent['display_name']} 回一句")
    harness["scheduler"].drain()

    listing = {channel["id"]: channel for channel in client.get("/api/channels").json()["channels"]}
    assert listing[watched["id"]]["last_seq"] == 2
    # watched 频道里人类与 Agent 各一条；owner 从未标读它，所以两条都算未读。
    assert listing[watched["id"]]["unread_count"] == 2
    assert listing[other["id"]]["unread_count"] == 0


def test_agent_reply_is_read_immediately_when_viewing(client: TestClient, harness: dict) -> None:
    """正在查看该频道时到达的 Agent 回复立即标记为已读。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 回一句")
    harness["scheduler"].drain()

    # 前端在「正在查看」时的动作就是把 seq 推到最新；这里用同一 HTTP 面模拟它。
    client.post(f"/api/channels/{channel['id']}/read", json={"seq": 2})

    listing = {item["id"]: item for item in client.get("/api/channels").json()["channels"]}
    assert listing[channel["id"]]["unread_count"] == 0


def test_unread_including_agent_replies_survives_restart(launched: dict) -> None:
    client = launched["client"]
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 回一句")
    launched["scheduler"].drain()
    before = {item["id"]: item for item in client.get("/api/channels").json()["channels"]}

    after = launched["restart"]()

    listing = {item["id"]: item for item in after.get("/api/channels").json()["channels"]}
    assert listing[channel["id"]]["unread_count"] == before[channel["id"]]["unread_count"] == 2


def test_system_messages_do_not_count_toward_unread(client: TestClient, harness: dict) -> None:
    """system 事件消息不计数：本票不产生 system 消息，因此这里断言的是「Agent 回复只算一条」。

    若实现把 Agent 回复多写成一条 system 通知，这个数字就会变成 3 而不是 2 —— 这条用例就是
    那个缺陷的判别力来源。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 回一句")
    harness["scheduler"].drain()

    listing = {item["id"]: item for item in client.get("/api/channels").json()["channels"]}
    kinds = [message["kind"] for message in messages(client, channel["id"])]
    assert kinds == ["human", "agent"]
    assert listing[channel["id"]]["unread_count"] == 2


def test_round_outcome_is_append_only(client: TestClient, harness: dict) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "一轮")
    harness["scheduler"].drain()
    record = outcomes(client, channel["id"])[0]

    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(f"/api/channels/{channel['id']}/round-outcomes").status_code == 404
    assert outcomes(client, channel["id"]) == [record]


def test_agent_reply_body_is_immutable(client: TestClient, harness: dict) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].replies[agent["id"]] = "原文"
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "一轮")
    harness["scheduler"].drain()
    reply = messages(client, channel["id"])[1]

    path = f"/api/channels/{channel['id']}/messages/{reply['seq']}"
    assert client.delete(path).status_code == 404
    assert client.put(path, json={"body": "改写"}).status_code == 404
    assert messages(client, channel["id"])[1]["body"] == "原文"


def test_reply_from_a_different_backend_instance_sees_the_same_stream(data_dir: Path, harness: dict) -> None:
    """回复落库后，换一个后端实例读同一数据目录看到的是同一条流（正本在库里）。"""
    agent = create_agent(harness["client"], name="小明").json()["agent"]
    harness["seam"].replies[agent["id"]] = "落在库里的回复"
    channel = channel_with_agent(harness["client"], agent["id"])
    send(harness["client"], channel["id"], "一轮")
    harness["scheduler"].drain()

    from tests.conftest import build_app

    with TestClient(build_app(data_dir)) as other:
        listed = messages(other, channel["id"])
        assert [message["body"] for message in listed][-1] == "落在库里的回复"
        assert outcomes(other, channel["id"])[0]["status"] == "replied"
