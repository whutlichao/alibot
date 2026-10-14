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
    messages,
    outcomes,
    send,
)
from tests.scripted import FAIL, REPLY


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
