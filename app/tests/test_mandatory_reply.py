"""#189 人类消息是**第四类确定信号**：DM / autonomous channel / team channel / task 四个面都必答。

判据来源：issue #189 与 `CONTEXT.md` 的**回应判断**词条。本文件只断言**外部可观察行为**：

- HTTP 落库的 `round_outcome`（状态 + 可读 reason）与消息流；
- 脚本化替身按既有做法记下的 `calls_for(member_id)` 里的 `prompt` / `mandatory_reply` 事实——
  `prompt` 由**真适配器的同一个** `_system_prompt` 合成，因此断言落在真的会被送进模型的那段文本上，
  不是手抄的文案副本（与 `test_agent_round.py` 的 #57 用例同源）。

不新开接缝：四个面都走 HTTP 扳机 + `ManualScheduler` + `ScriptedAgentSeam`。
「可以忽略的一轮」由 `tests.conftest.send_agent_message` 构造（非人类作者触发），
因为人类消息自本票起一律必答。

纪律的可读文案**按信号来源**分家（本票要守的就是这条）：人类消息唤醒的那一轮不能被写成
「任务变化」「认领」或「被个人提及」——那三条都是与事实不符的诊断。
"""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from tests.conftest import (
    channel_with_agent,
    create_agent,
    create_channel,
    drain,
    messages,
    outcomes,
    send,
    send_agent_message,
)
from tests.scripted import DELAY, EMPTY_REPLY, IGNORE, REPLY, SILENT


def _db(client: TestClient) -> Any:
    return cast(Any, client.app).state.db


def _target_outcomes(client: TestClient, target_id: str) -> list[dict[str, Any]]:
    """task / dm 型 target 没有频道面只读端点，读既有库方法（与 `test_team_mode.py` 同源）。"""
    return _db(client).list_round_outcomes(target_id)


def _dm(client: TestClient, agent_id: str) -> dict[str, Any]:
    return client.post("/api/dms", json={"agent_id": agent_id}).json()["dm"]


def _make_team(client: TestClient, leader: str, *members: str, name: str = "团队频道") -> dict[str, Any]:
    channel = create_channel(client, name=name, member_ids=[leader, *members]).json()["channel"]
    response = client.patch(
        f"/api/channels/{channel['id']}/config",
        json={"mode": "team", "team_leader_member_id": leader},
    )
    assert response.status_code == 200, response.json()
    return channel


def _create_task(client: TestClient, channel_id: str, title: str = "任务") -> dict[str, Any]:
    response = client.post(f"/api/channels/{channel_id}/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["task"]


# ─── 面一：DM —— owner 的每条消息都是确定信号 ─────────────────────────────
def test_a_dm_owner_message_is_a_mandatory_round(client: TestClient, harness: dict) -> None:
    """DM 里 owner 的消息让唯一 Agent 得到 `mandatory_reply=True`（不再靠「被点名」才必答）。"""
    agent = create_agent(client, name="小张").json()["agent"]
    harness["seam"].replies[agent["id"]] = "收到，我去查"
    dm = _dm(client, agent["id"])

    assert (
        client.post(f"/api/dms/{dm['id']}/messages", json={"body": "查询今天的google股票"}).status_code == 201
    )
    drain(harness)

    call = harness["seam"].calls_for(agent["id"])[-1]
    assert call["mandatory_reply"] is True, "DM 里人类的每条消息都是确定信号"
    assert [record["status"] for record in _target_outcomes(client, dm["id"])] == ["replied"]


def test_a_silent_dm_round_is_a_failure_that_names_the_human_message(
    client: TestClient, harness: dict
) -> None:
    """DM 里声明沉默 ⇒ `failed`，reason 写「人类消息」，并带出原始的 silent 理由。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = SILENT
    dm = _dm(client, agent["id"])

    assert client.post(f"/api/dms/{dm['id']}/messages", json={"body": "什么情况啊？"}).status_code == 201
    drain(harness)

    record = _target_outcomes(client, dm["id"])[0]
    assert record["status"] == "failed", "人类消息是确定信号，沉默不能以 silent 收口"
    assert "人类消息" in record["reason"]
    assert "确定信号" in record["reason"]
    # 原始 silent 理由必须被带出来，否则诊断只剩一句「不许沉默」。
    assert "声明本轮沉默" in record["reason"]
    # 信号来源可区分：不得把人类消息写成另外三种信号。
    for wrong in ("任务变化", "认领", "被个人提及"):
        assert wrong not in record["reason"], f"人类消息不能被写成「{wrong}」"
    # 沉默不产出回复消息（既有语义不变）。
    assert [
        message["kind"] for message in client.get(f"/api/dms/{dm['id']}/messages").json()["messages"]
    ] == ["human"]


# ─── 面二：autonomous channel —— 每个 Agent 成员都必答 ────────────────────
def test_a_human_message_makes_every_member_of_an_autonomous_channel_mandatory(
    client: TestClient, harness: dict
) -> None:
    """autonomous 频道里 owner 的一条消息 ⇒ 该 target 的**每个** Agent 成员都拿到必答标记。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    channel = create_channel(client, member_ids=[first["id"], second["id"]]).json()["channel"]

    send(client, channel["id"], "两件事都看下")
    drain(harness)

    for agent in (first, second):
        # 取**第一轮**：两位都回应后彼此还会唤醒一轮（Agent 作者，非必答），那不属于本判据。
        calls = harness["seam"].calls_for(agent["id"])
        assert calls[0]["mandatory_reply"] is True, f"{agent['display_name']} 的人类消息轮也应必答"


def test_an_empty_body_on_a_human_message_round_is_a_failure(client: TestClient, harness: dict) -> None:
    """必答轮「replied 但没有正文」同样按 failed 收口（人类消息不是「可以不说话」）。"""
    agent = create_agent(client, name="空回者").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = EMPTY_REPLY
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "说一下今天的结论")
    drain(harness)

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "failed"
    assert "人类消息" in record["reason"]
    assert "正文" in record["reason"]


# ─── 面三：team channel —— 队长必答，且文案是「人类消息」口径 ─────────────
def test_a_team_channel_human_message_is_a_mandatory_leader_round_with_human_wording(
    client: TestClient, harness: dict
) -> None:
    """team 频道里 owner 的消息让队长必答；文案写「人类消息」，不得复用「任务变化」。"""
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    harness["seam"].behaviors[leader["id"]] = SILENT
    channel = _make_team(client, leader["id"], member["id"])

    send(client, channel["id"], "开工")
    drain(harness)

    call = harness["seam"].calls_for(leader["id"])[-1]
    assert call["mandatory_reply"] is True
    assert "人类消息" in call["prompt"], "提示词要说明这一轮为什么必须回应"
    assert "任务变化" not in call["prompt"], "人类消息不是任务变化"
    assert harness["seam"].calls_for(member["id"]) == [], "team 频道仍只唤醒队长"

    record = outcomes(client, channel["id"])[0]
    assert record["member_id"] == leader["id"]
    assert record["status"] == "failed"
    assert "人类消息" in record["reason"]
    assert "任务变化" not in record["reason"], "建任务 / 改派的文案不能挪用到人类消息上"
    assert "认领" not in record["reason"]


# ─── 面四：task target —— 受派者必答 ─────────────────────────────────────
def test_an_owner_message_in_a_task_thread_makes_the_assignee_mandatory(
    client: TestClient, harness: dict
) -> None:
    """任务线程里 owner 发言 ⇒ 受派者必答（人类直接对话不是「频道里没点我」）。"""
    agent = create_agent(client, name="受派者").json()["agent"]
    # 本用例只关心 owner 手动改派那一轮；关掉自动认领，避免建任务时的认领轮抢先跑掉。
    assert client.patch(f"/api/agents/{agent['id']}", json={"auto_claim": False}).status_code == 200
    harness["seam"].replies[agent["id"]] = "已经在做"
    channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
    task = _create_task(client, channel["id"], title="任务线程必答")
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": agent["id"]}).status_code == 200
    )
    drain(harness)  # owner 手动改派那一轮（既有纪律：非必答）
    assert harness["seam"].calls_for(agent["id"])[-1]["mandatory_reply"] is False

    assert (
        client.post(
            f"/api/tasks/{task['id']}/messages",
            json={"body": "现在的进度如何？", "task_ref": task["id"]},
        ).status_code
        == 201
    )
    drain(harness)

    assert harness["seam"].calls_for(agent["id"])[-1]["mandatory_reply"] is True, "受派者必须回应人类"


# ─── 提示词：DM 是一对一口径，channel 保留 #57 rubric 并追加必答句 ────────
def test_the_dm_prompt_speaks_one_to_one_and_requires_a_reply(client: TestClient, harness: dict) -> None:
    """DM 轮不再自称「多成员频道」、不再给「没被点名可以沉默」的边界，且有显式必答句。"""
    agent = create_agent(client, name="私聊对象").json()["agent"]
    dm = _dm(client, agent["id"])

    assert client.post(f"/api/dms/{dm['id']}/messages", json={"body": "在吗"}).status_code == 201
    drain(harness)

    prompt = harness["seam"].calls_for(agent["id"])[-1]["prompt"]
    assert "多成员频道" not in prompt, "私聊不是频道，身份句必须是一对一口径"
    assert "没有被点名时" not in prompt, "私聊里没有「点不点名」这回事"
    assert "保持沉默" not in prompt, "私聊不该给「正常做法是保持沉默」这条边界"
    assert "人类消息" in prompt, "必答轮要说明这一轮为什么必须回应"
    assert "必须回应" in prompt, "必答轮必须有一句显式要求回应的句子"


def test_the_channel_prompt_keeps_the_silence_rubric_and_appends_the_human_message_rule(
    client: TestClient, harness: dict
) -> None:
    """channel 轮的 #57 rubric **原样一段**；人类消息必答句是**独立段**，不拆 rubric。"""
    agent = create_agent(client, name="成员").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "记一笔：今天的对齐会推迟到明天。")
    drain(harness)

    prompt = harness["seam"].calls_for(agent["id"])[-1]["prompt"]
    blocks = prompt.split("\n\n")
    rubric = [block for block in blocks if "点名" in block]
    assert len(rubric) == 1, f"回应纪律仍应恰好是一段：{rubric}"
    assert "不产出正文" in rubric[0]
    required = [block for block in blocks if "人类消息" in block]
    assert len(required) == 1, f"人类消息必答句应当是独立的一段：{required}"
    assert "必须回应" in required[0]
    assert required[0] != rubric[0], "必答句不得并入 rubric（那会把它拆成两段规矩）"


# ─── 非人类作者：纪律一律不变 ────────────────────────────────────────────
def test_an_agent_authored_message_still_leaves_the_round_to_self_judgement(
    client: TestClient, harness: dict
) -> None:
    """Agent 作者的消息不因本票变成必答：那一轮仍可自判忽略（收口成 `ignored`）。

    构造（不猜时序）：让**被观察者（乙）先跑**它的人类消息轮次，因此乙先消费掉那条人类消息；
    随后甲的回复作为一条**新的 Agent 消息**再唤醒乙——那一轮没有确定信号。
    """
    quiet = create_agent(client, name="乙").json()["agent"]
    talker = create_agent(client, name="甲").json()["agent"]
    harness["seam"].behaviors = {quiet["id"]: IGNORE, talker["id"]: REPLY}
    harness["seam"].replies[talker["id"]] = "我来回"
    # 成员顺序 = 唤醒顺序：乙在前，于是乙的必答轮先跑、先消费掉人类消息。
    channel = create_channel(client, member_ids=[quiet["id"], talker["id"]]).json()["channel"]

    send(client, channel["id"], "两位都在吗")
    drain(harness)

    calls = harness["seam"].calls_for(quiet["id"])
    assert [call["mandatory_reply"] for call in calls] == [True, False], (
        "乙先跑人类消息（必答），再被甲的 Agent 消息唤醒（非必答）"
    )
    records = [record for record in outcomes(client, channel["id"]) if record["member_id"] == quiet["id"]]
    assert [record["status"] for record in records] == ["failed", "ignored"], (
        "非必答轮的自判忽略语义不变（不被本票改判成 failed）"
    )
    assert "确定信号" not in records[1]["reason"]
    assert [message["kind"] for message in messages(client, channel["id"])].count("agent") == 1


# ─── 已知边界：同 target 的「在飞轮次」合并不改判 ────────────────────────
def test_a_human_message_merged_into_an_in_flight_round_does_not_retroactively_force_it(
    client: TestClient, harness: dict
) -> None:
    """**已知边界**（本票不改合并语义）：纪律只在**投递时**定下。

    某成员此刻正在跑一轮（由 Agent 消息触发 ⇒ 非必答），owner 的人类消息在这之后到达——按既有的
    Q4=A 合并语义（同 target 重复唤醒不排队、不重放，收口时合成一条 `merged`），那条人类消息**不会**
    把已经在跑的那一轮改判成必答。可观察后果：

    - 在飞那一轮的记录仍是 `ignored`（非必答），reason 不含「确定信号」；
    - 人类消息**没被吞掉**：它仍在消费游标之后，下一次唤醒会把它一并读到（下一轮的 `bodies` 里能看到）。

    这条暴露面与认领轮相同（ADR-0016 五 的合并语义、Q4=A），验收文档 §10 有登记；本用例锁定现状，
    防止「以为合并会继承纪律」的误读。
    """
    import threading
    import time

    quiet = create_agent(client, name="乙").json()["agent"]
    speaker = create_agent(client, name="甲").json()["agent"]
    harness["seam"].behaviors = {quiet["id"]: DELAY}
    channel = create_channel(client, member_ids=[quiet["id"], speaker["id"]]).json()["channel"]

    # 第一轮：Agent 作者的消息（非必答），停在闸门上 ⇒ 该成员持续 in-flight。
    harness["seam"].block()
    send_agent_message(client, channel["id"], speaker["id"], "甲：先说一句")
    runner = threading.Thread(target=harness["scheduler"].drain)
    runner.start()
    try:
        for _ in range(200):
            if harness["seam"].calls_for(quiet["id"]):
                break
            time.sleep(0.01)
        assert harness["seam"].calls_for(quiet["id"]), "第一轮没有进入 in-flight"

        # owner 的人类消息在 in-flight 窗口里到达 ⇒ 被合并进那一轮（不重放、不排队）。
        assert send(client, channel["id"], "人类：这件也看下").status_code == 201
        harness["scheduler"].drain(limit=1)  # 这一轮直接命中 in-flight ⇒ 合成 merged、不进适配层
    finally:
        harness["seam"].release()
        runner.join(timeout=10)
    assert not runner.is_alive()

    # 在飞那一轮拿到的是**投递时**的纪律（非必答）：替身的记录与轮次结果都这么说。
    assert harness["seam"].calls_for(quiet["id"])[0]["mandatory_reply"] is False
    records = outcomes(client, channel["id"])
    first_round = next(
        record for record in records if record["member_id"] == quiet["id"] and record["status"] != "merged"
    )
    assert first_round["status"] != "failed", "非必答轮不该被纪律改判"
    assert "确定信号" not in first_round["reason"]
    assert any(record["status"] == "merged" for record in records), (
        "人类消息被合并进在飞轮次 ⇒ 留一条 merged 记录"
    )

    # 没被吞掉：下一次唤醒会把它一并读到。
    send_agent_message(client, channel["id"], speaker["id"], "甲：再说一句")
    drain(harness)
    bodies = harness["seam"].calls_for(quiet["id"])[-1]["bodies"]
    assert "人类：这件也看下" in bodies, "被合并的人类消息仍在游标之后，不会丢"
