"""事件流与断线补拉：四类事件、按 seq 追加、重连带 last_seq 补拉不重复不错序。

只经事件流（``GET /api/channels/{id}/events``）与 HTTP 面取证。事件流的补拉由后端读库完成，
因此「重连补齐」可以用「带上 last_seq 建一条新连接」这条可观通路稳定验证，不需要真的断网。

事件流的用例跑在**真实 HTTP 服务**上（``live`` 夹具）：``TestClient`` 会读完整个响应体，
而事件流是持续的，逐帧读取必须走真 socket——这也正是浏览器 ``EventSource`` 干的事。
"""

from __future__ import annotations

import json
import threading
import time

import httpx
from fastapi.testclient import TestClient

from tests.conftest import (
    REMOTE_MODEL_ID,
    channel_with_agent,
    create_agent,
    outcomes,
    send,
    sse_frames,
)
from tests.scripted import DELAY, FAIL, IGNORE

AGENT_PAYLOAD = {
    "name": "小明",
    "role": "前端工程师",
    "model_id": "ollama:qwen2.5",
    "system_prompt": "你是前端工程师。",
}


def _channel_with(http: httpx.Client, agent_id: str, name: str = "协作频道") -> dict:
    return http.post(
        "/api/channels", json={"name": name, "description": "", "member_ids": [agent_id]}
    ).json()["channel"]


def _wait_for(events: list[dict], predicate, timeout: float = 5.0) -> bool:
    """轮询等待条件成立。只用于等真实 HTTP 推送到达，不用于绕过不确定的时序。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(list(events)):
            return True
        time.sleep(0.01)
    return False


def test_event_stream_replays_messages_after_the_cursor(live: dict) -> None:
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    live["seam"].replies[agent["id"]] = "这是完整回复"
    channel = _channel_with(http, agent["id"])
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "第一条"})
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "第二条"})
    live["scheduler"].drain()

    # 前端「断线前看到的最后一条」是 seq=1，重连时带上它。
    replayed = sse_frames(http, channel["id"], after_seq=1)
    message_events = [event for event in replayed if event["type"] == "message"]

    assert [event["seq"] for event in message_events] == [2, 3]
    assert [event["message"]["body"] for event in message_events] == ["第二条", "这是完整回复"]
    # 不重复：seq=1 那条没有被再发一次。
    assert 1 not in [event["seq"] for event in message_events]


def test_event_stream_replays_round_outcomes(live: dict) -> None:
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    channel = _channel_with(http, agent["id"])
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "一轮"})
    live["scheduler"].drain()

    replayed = sse_frames(http, channel["id"], after_seq=0)

    round_events = [event for event in replayed if event["type"] == "round_outcome"]
    assert len(round_events) == 1
    assert round_events[0]["outcome"]["status"] == "replied"
    over_http = http.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
    assert round_events[0]["outcome"] == over_http[0]


def test_event_stream_sends_a_status_snapshot_on_connect(live: dict) -> None:
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    channel = _channel_with(http, agent["id"])

    replayed = sse_frames(http, channel["id"], after_seq=0)

    status_events = [event for event in replayed if event["type"] == "member_status"]
    assert [event["member_id"] for event in status_events] == [agent["id"]]
    assert status_events[0]["status"] == "online"


def test_replay_does_not_duplicate_or_reorder(live: dict) -> None:
    """补拉 + 实时推送的边界不重叠：seq 严格递增、无重复。"""
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    channel = _channel_with(http, agent["id"])
    for index in range(1, 6):
        http.post(f"/api/channels/{channel['id']}/messages", json={"body": f"第 {index} 条"})
    live["scheduler"].drain()

    replayed = sse_frames(http, channel["id"], after_seq=0)
    seqs = [event["seq"] for event in replayed if event["type"] == "message"]

    assert seqs == sorted(set(seqs)), "补拉的事件出现重复或错序"
    total = len(http.get(f"/api/channels/{channel['id']}/messages").json()["messages"])
    assert seqs == list(range(1, total + 1))


def test_round_outcomes_are_also_readable_over_http(live: dict) -> None:
    """轮次结果同时经只读端点与事件流可见（两条取证通道，同一份事实）。"""
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    channel = _channel_with(http, agent["id"])
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "一轮"})
    live["scheduler"].drain()

    over_http = http.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
    over_sse = [
        event["outcome"]
        for event in sse_frames(http, channel["id"], after_seq=0)
        if event["type"] == "round_outcome"
    ]

    assert over_http == over_sse


def test_round_outcomes_can_be_filtered_by_base_seq(client: TestClient, harness: dict) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "第一条")
    harness["scheduler"].drain()
    send(client, channel["id"], "第二条")
    harness["scheduler"].drain()

    newer = outcomes(client, channel["id"], base_seq=3)

    assert [record["base_seq"] for record in newer] == [3]


def test_unknown_channel_event_stream_is_not_found(client: TestClient) -> None:
    response = client.get("/api/channels/ch_missing/events")

    assert response.status_code == 404


def test_typing_indicator_is_emitted_while_a_round_is_in_flight(live: dict) -> None:
    """「正在回复」由后端事实驱动：一轮 in-flight 时发 active=True，收口后发 active=False。"""
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    live["seam"].behaviors[agent["id"]] = DELAY
    live["seam"].block()
    channel = _channel_with(http, agent["id"])

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

    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "开始一轮"})
    runner = threading.Thread(target=live["scheduler"].drain)
    runner.start()
    typing_open = _wait_for(events, lambda items: any(i["type"] == "typing" and i["active"] for i in items))
    assert typing_open, events

    live["seam"].release()
    runner.join(timeout=10)
    assert not runner.is_alive()
    _wait_for(events, lambda items: any(i["type"] == "typing" and i["active"] is False for i in items))

    statuses = [event["status"] for event in events if event["type"] == "member_status"]
    assert "working" in statuses, events
    assert statuses[-1] == "online", events
    stop.set()
    reader_thread.join(timeout=5)


def test_status_event_reports_error_after_a_failed_round(live: dict) -> None:
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    live["seam"].behaviors[agent["id"]] = FAIL
    channel = _channel_with(http, agent["id"])

    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "一轮"})
    live["scheduler"].drain()

    events = sse_frames(http, channel["id"], after_seq=0)
    statuses = [event["status"] for event in events if event["type"] == "member_status"]
    assert statuses[-1] == "error"


def test_status_event_reports_offline_for_a_disabled_agent(live: dict) -> None:
    http = live["http"]
    payload = dict(AGENT_PAYLOAD, name="远端", model_id=REMOTE_MODEL_ID)
    agent = http.post("/api/agents", json=payload).json()["agent"]
    channel = _channel_with(http, agent["id"])

    events = sse_frames(http, channel["id"], after_seq=0)
    statuses = [event["status"] for event in events if event["type"] == "member_status"]
    assert statuses == ["offline"]


def test_ignored_round_returns_to_online(live: dict) -> None:
    """ignored 不是失败：状态点回到空闲（四态只表达后端事实）。"""
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    live["seam"].behaviors[agent["id"]] = IGNORE
    channel = _channel_with(http, agent["id"])

    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "无关消息"})
    live["scheduler"].drain()

    events = sse_frames(http, channel["id"], after_seq=0)
    statuses = [event["status"] for event in events if event["type"] == "member_status"]
    assert statuses[-1] == "online"


def test_events_of_other_channels_do_not_leak(live: dict) -> None:
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    first = _channel_with(http, agent["id"], name="甲")
    second = http.post("/api/channels", json={"name": "乙", "description": "", "member_ids": []}).json()[
        "channel"
    ]
    http.post(f"/api/channels/{first['id']}/messages", json={"body": "甲里的消息"})
    http.post(f"/api/channels/{second['id']}/messages", json={"body": "乙里的消息"})

    events = sse_frames(http, second["id"], after_seq=0)
    bodies = [event["message"]["body"] for event in events if event["type"] == "message"]

    assert bodies == ["乙里的消息"]


def test_stream_frames_use_seq_as_the_event_id(live: dict) -> None:
    """每一帧的 SSE ``id`` 就是频道内 seq：前端重连的游标与消息顺序是同一个数。"""
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    channel = _channel_with(http, agent["id"])
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "一条"})

    with http.stream("GET", f"/api/channels/{channel['id']}/events", params={"after_seq": 0}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        ids: list[str] = []
        data_seqs: list[int] = []
        for line in response.iter_lines():
            text = line.decode() if isinstance(line, bytes) else line
            if text.startswith("id: "):
                ids.append(text[len("id: ") :])
            elif text.startswith("data: "):
                data_seqs.append(json.loads(text[len("data: ") :])["seq"])
            elif text.startswith(":"):
                break

    assert ids == [str(seq) for seq in data_seqs]
    assert "1" in ids
