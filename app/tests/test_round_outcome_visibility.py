"""轮次失败在消息流里可见（实施票 #8）的 HTTP / SSE 面契约。

修复本身在前端（`studio/app.js` / `studio/app.css`：把已经算好、已经落库、已经推流的那份事实
呈现出来）。因此这里断言的不是前端 DOM，而是**前端据以呈现的那份事实**在两条可观察通道上
（只读端点与事件流）都已经成立：

- 每条失败轮次结果都带**可读 reason**，且 `base_seq` 指向**触发它的那条消息**的 seq —— 这就是
  「绑定到具体是哪条消息触发的哪一轮」在数据面的形态；
- 失败**不写半条消息、不占 seq**：消息流里不会多出一条 Agent 消息 —— 这就是「不伪造它答应了」；
- 「未启用」（成员状态）与「这一轮失败」（轮次结果）是两个可分别取到的后端事实；
- 该成员随后成功回应时，失败痕迹仍可追溯，且消息流里只有**一条**它的回复。

真实 Chromium 里的呈现（失败行挂在哪条消息下面、色位与文案）按前两票的边界手跑取证，
见 `app/specs/issue-8-acceptance.md`，不进 pytest。
"""

from __future__ import annotations

import json
import threading
import time

import httpx
from fastapi.testclient import TestClient

from tests.conftest import (
    REMOTE_MODEL_ID,
    agents,
    channel_with_agent,
    channels,
    create_agent,
    create_channel,
    messages,
    outcomes,
    send,
)
from tests.scripted import FAIL, IGNORE, REPLY


def _wait_for(events: list[dict], predicate, timeout: float = 5.0) -> bool:
    """轮询等待条件成立（等真实 HTTP 推送到达，而不是用固定延时猜时序）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(list(events)):
            return True
        time.sleep(0.01)
    return False


def _disabled_remote_agent(client: TestClient, monkeypatch, name: str = "远端") -> dict:
    """配一个 `dashscope:…` 且进程内无 `DASHSCOPE_API_KEY` 的成员：复现「未启用被 @」。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    agent = create_agent(client, name=name, model_id=REMOTE_MODEL_ID).json()["agent"]
    # 远端数据流已确认 ⇒ 唯一让它「未启用」的原因就是缺凭证（reason 因此唯一且可读）。
    client.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})
    return agent


def test_disabled_round_failure_is_readable_and_bound_to_the_triggering_message(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """缺凭证被 `@`：失败轮次结果带可读 reason，且 `base_seq` = 那条 @ 它的消息的 seq。"""
    agent = _disabled_remote_agent(client, monkeypatch)
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], f"@{agent['display_name']} 看一下这条")
    harness["scheduler"].drain()

    records = outcomes(client, channel["id"])
    assert len(records) == 1
    record = records[0]
    triggering = messages(client, channel["id"])[0]
    assert record["status"] == "failed"
    assert record["base_seq"] == triggering["seq"] == 1, "失败必须能绑定到触发它的那条消息"
    assert record["reason"], "失败轮次必须留下可读原因"
    assert "dashscope" in record["reason"] and "DASHSCOPE_API_KEY" in record["reason"]


def test_disabled_round_writes_no_half_message_and_does_not_take_a_seq(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """失败不伪造「它答应了」：消息流里只有那条人类消息，`last_seq` 不因失败前进。"""
    agent = _disabled_remote_agent(client, monkeypatch)
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], f"@{agent['display_name']} 看一下这条")
    harness["scheduler"].drain()

    stream = messages(client, channel["id"])
    assert [(message["seq"], message["kind"]) for message in stream] == [(1, "human")]
    assert channels(client)[channel["id"]]["last_seq"] == 1


def test_unavailable_member_state_and_failed_round_outcome_are_distinguishable(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """两个后端事实分别可读：成员**状态**（未启用 + 为什么）与**这一轮**的结果（失败 + 原因）。"""
    agent = _disabled_remote_agent(client, monkeypatch)
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], f"@{agent['display_name']} 看一下这条")
    harness["scheduler"].drain()

    view = agents(client)[0]
    record = outcomes(client, channel["id"])[0]

    assert view["status"] == "offline" and view["enabled_reason"]
    assert record["status"] == "failed"
    # 两者说的是同一件事的两个面，但**不是同一个字段**：状态是成员此刻的可用性，
    # 结果是那一次轮次的历史结论。前端因此可以分别陈述。
    assert view["enabled_reason"] == record["reason"]


def test_model_failure_round_carries_its_own_readable_reason(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    """第二种情形：成员可用但**这一轮**模型调用失败 —— reason 同样可读、同样绑定到消息。"""
    agent = create_agent(client, name="本机").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = FAIL
    harness["seam"].replies[agent["id"]] = "模型调用失败：脚本化替身"
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], f"@{agent['display_name']} 来一句")
    harness["scheduler"].drain()

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "failed"
    assert record["base_seq"] == 1
    assert record["reason"] == "模型调用失败：脚本化替身"
    # 与「未启用」是两回事：成员状态点仍是它自己的事实（这里可用）。
    assert agents(client)[0]["status"] == "error"
    assert agents(client)[0]["enabled_reason"] == ""


def test_a_later_successful_round_does_not_duplicate_the_failure(client: TestClient, harness: dict) -> None:
    """失败后同一成员再成功回应：消息流里只有**一条**它的回复，失败痕迹仍可追溯。"""
    agent = create_agent(client, name="本机").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = FAIL
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], f"@{agent['display_name']} 第一次")
    harness["scheduler"].drain()

    harness["seam"].behaviors[agent["id"]] = REPLY
    harness["seam"].replies[agent["id"]] = "第二次我来了"
    send(client, channel["id"], f"@{agent['display_name']} 第二次")
    harness["scheduler"].drain()

    stream = messages(client, channel["id"])
    agent_messages = [message for message in stream if message["kind"] == "agent"]
    assert [message["body"] for message in agent_messages] == ["第二次我来了"], "不得看起来像说了两次"
    assert [record["status"] for record in outcomes(client, channel["id"])] == ["failed", "replied"]
    assert [record["base_seq"] for record in outcomes(client, channel["id"])] == [1, 2]


def test_failed_round_outcome_reaches_the_event_stream(live: dict, monkeypatch) -> None:
    """失败轮次结果是**推给订阅者**的：浏览器（前端）不需要轮询就能拿到 reason 与绑定 seq。"""
    http: httpx.Client = live["http"]
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    agent = http.post(
        "/api/agents",
        json={
            "name": "远端",
            "role": "审阅",
            "model_id": REMOTE_MODEL_ID,
            "system_prompt": "你是审阅者。",
        },
    ).json()["agent"]
    http.post("/api/remote-data-flow/ack", json={"provider": "dashscope"})
    channel = http.post(
        "/api/channels", json={"name": "复现频道", "description": "", "member_ids": [agent["id"]]}
    ).json()["channel"]

    events: list[dict] = []
    stop = threading.Event()

    def reader() -> None:
        with http.stream("GET", f"/api/channels/{channel['id']}/events", params={"after_seq": 0}) as response:
            for line in response.iter_lines():
                if stop.is_set():
                    return
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("data: "):
                    events.append(json.loads(text[len("data: ") :]))

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        http.post(
            f"/api/channels/{channel['id']}/messages", json={"body": f"@{agent['display_name']} 看这条"}
        )
        live["scheduler"].drain()

        pushed = _wait_for(
            events,
            lambda items: any(
                item["type"] == "round_outcome" and item["outcome"]["status"] == "failed" for item in items
            ),
            timeout=10.0,
        )
        assert pushed, events
        event = next(
            item
            for item in events
            if item["type"] == "round_outcome" and item["outcome"]["status"] == "failed"
        )
        assert event["outcome"]["base_seq"] == 1
        assert "DASHSCOPE_API_KEY" in event["outcome"]["reason"]
        # 推送的那条与只读端点读到的**是同一条事实**（前端两条路都不会拿到不同的说法）。
        assert (
            event["outcome"]
            == http.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"][0]
        )
    finally:
        stop.set()
        reader_thread.join(timeout=5)


# ─── #18：摘要行的呈现依据（同一条消息下非 failed 轮次结果的分组与逐成员事实） ───


def _mixed_channel(client: TestClient, harness: dict, ignore: int = 2, fail: int = 1) -> dict:
    """一个频道里放 `ignore` 个「自判忽略」成员 + `fail` 个「这一轮失败」成员。

    这是 #18 摘要行的核心场景：同一条消息（同一 `base_seq`）下**既有**该收进折叠摘要行的
    非 `failed` 结果、**也有**必须直接展开的 `failed` 结果。
    """
    agents_ = [create_agent(client, name=f"成员{index}").json()["agent"] for index in range(ignore + fail)]
    ignore_ids = {agent["id"] for agent in agents_[:ignore]}
    harness["seam"].behaviors = {
        agent["id"]: (IGNORE if agent["id"] in ignore_ids else FAIL) for agent in agents_
    }
    channel = create_channel(client, name="混合结论", member_ids=[agent["id"] for agent in agents_]).json()[
        "channel"
    ]
    send(client, channel["id"], "大家看一下")
    harness["scheduler"].drain()
    return channel


def test_non_failed_outcomes_of_one_message_are_grouped_under_its_base_seq(
    client: TestClient, harness: dict
) -> None:
    """摘要行的分组依据：同一 `base_seq` 下的多条非 `failed` 结果**同属一条消息**。

    前端「每消息至多一行」的折叠就是按 `base_seq` 分组的，因此这里断言那份分组**在数据面成立**：
    `ignored` 因当前唤醒面（频道内全部 agent 成员）按成员各有一条，但它们的 `base_seq` 相同，
    可以收成一行而**不丢任何记录**（append-only 取证面一条不少）。
    """
    channel = _mixed_channel(client, harness, ignore=3, fail=0)
    records = outcomes(client, channel["id"])

    assert [record["status"] for record in records] == ["ignored"] * 3, "三个成员各留一条记录"
    assert {record["base_seq"] for record in records} == {1}, "同一批唤醒共用同一个 base_seq"
    assert len({record["member_id"] for record in records}) == 3, "三条记录分属三个不同成员"
    # 同一 base_seq 下**每成员至多一条**：这就是「一行摘要 + 展开后逐成员一行」的成立前提。
    assert all(len(record["reason"]) > 0 for record in records), "每条都要有自己的 reason 原文"


def test_ignored_is_not_recorded_or_reported_as_failure(client: TestClient, harness: dict) -> None:
    """`ignored` 不是失败：记录里是 `ignored`、状态点仍空闲（摘要行不得用失败措辞的依据）。"""
    agent = create_agent(client, name="沉默者").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = IGNORE
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], "没有点名谁")
    harness["scheduler"].drain()

    record = outcomes(client, channel["id"])[0]
    assert record["status"] == "ignored", "不得把自判忽略写成 failed"
    assert record["reason"]
    assert agents(client)[0]["status"] == "online", "ignored 不把成员判成出错"


def test_budget_exhausted_rounds_of_one_message_share_the_triggering_seq(
    client: TestClient, harness: dict
) -> None:
    """`budget-exhausted` 也是「同一条消息下可收成一行」的一族，且**不是** failed。

    预算是既存后端事实（本票只消费）：耗尽时每个「本应被唤醒却被压制」的成员各记一条，
    `base_seq` = 那条触发它的 Agent 消息；状态点仍是空闲（预算耗尽不是出错）。
    """
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    third = create_agent(client, name="丙").json()["agent"]
    channel = client.post(
        "/api/channels",
        json={
            "name": "接力",
            "description": "",
            "member_ids": [first["id"], second["id"], third["id"]],
        },
    ).json()["channel"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} @{third['display_name']} 继续",
        second["id"]: f"@{first['display_name']} @{third['display_name']} 继续",
        third["id"]: f"@{first['display_name']} @{second['display_name']} 继续",
    }

    send(client, channel["id"], "开始接力")
    for _ in range(12):
        harness["scheduler"].drain()
        if harness["scheduler"].pending_count() == 0:
            break

    exhausted = [
        record for record in outcomes(client, channel["id"]) if record["status"] == "budget-exhausted"
    ]
    assert exhausted, "预算耗尽必须留下可追溯的轮次结论"
    # 同一跳压制多个成员时，它们的 base_seq 相同 ⇒ 前端收成一行（不逐成员铺满消息流）。
    by_base: dict[int, set[str]] = {}
    for record in exhausted:
        by_base.setdefault(record["base_seq"], set()).add(record["member_id"])
    assert any(len(members) > 1 for members in by_base.values()), by_base
    # 预算耗尽不是失败：不写半条消息、状态点不判 error（摘要行不得用失败视觉的依据）。
    assert all("预算已用尽" in record["reason"] for record in exhausted)
    assert client.get(f"/api/agents/{first['id']}/status").json()["status"] != "error"


def test_failed_and_non_failed_outcomes_of_one_message_coexist_without_swallowing(
    client: TestClient, harness: dict
) -> None:
    """同一条消息下 `failed` 与非 `failed` **混存**：两类都得出现，互不吞并。

    这是摘要行的关键边界 —— 折叠摘要不能把失败吞掉（失败必须免点击可读），失败也不能遮掉
    那三条正常选择。数据面必须同时给出两组事实，且都绑定到同一条消息的 `base_seq`。
    """
    channel = _mixed_channel(client, harness, ignore=2, fail=1)
    records = outcomes(client, channel["id"])
    triggering = messages(client, channel["id"])[0]

    grouped: dict[str, list[str]] = {}
    for record in records:
        grouped.setdefault(record["status"], []).append(record["member_id"])
    assert sorted(grouped) == sorted(["failed", "ignored"]), grouped
    assert len(grouped["ignored"]) == 2
    assert len(grouped["failed"]) == 1
    # 两类都在**同一条**消息下 ⇒ 前端在同一锚点下要同时给出「摘要行」与「failed 块」。
    assert triggering["seq"] == 1
    assert {record["base_seq"] for record in records} == {triggering["seq"]}
    failed = next(record for record in records if record["status"] == "failed")
    assert failed["reason"], "失败块的 reason 必须可读（它免点击就呈现）"


def test_every_outcome_reason_is_verbatim_readable_chinese(client: TestClient, harness: dict) -> None:
    """展开后逐成员呈现的 `reason` 是**可读中文原文**：不重新措辞、不截断成省略号。

    前端直接呈现这个字段，因此断言它自带可读性（既不是空串、也不是被省略号截断的半句话）。
    """
    channel = _mixed_channel(client, harness, ignore=2, fail=1)
    for record in outcomes(client, channel["id"]):
        assert record["reason"].strip(), record
        assert not record["reason"].rstrip().endswith("…"), "后端给的是完整可读原文，不该被截断"
        assert any("\u4e00" <= char <= "\u9fff" for char in record["reason"]), record["reason"]
