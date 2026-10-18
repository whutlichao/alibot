"""事件流与断线补拉：四类事件、按 seq 追加、重连带 last_seq 补拉不重复不错序。

只经事件流（``GET /api/channels/{id}/events``）与 HTTP 面取证。事件流的补拉由后端读库完成，
因此「重连补齐」可以用「带上 last_seq 建一条新连接」这条可观通路稳定验证，不需要真的断网。

事件流的用例跑在**真实 HTTP 服务**上（``live`` 夹具）：``TestClient`` 会读完整个响应体，
而事件流是持续的，逐帧读取必须走真 socket——这也正是浏览器 ``EventSource`` 干的事。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from alibot.agent_seam import AgentEventRecord
from alibot.events import (
    QUEUE_LIMIT,
    EventBus,
    agent_event,
    approval_required,
    approval_resolved,
    plan_update,
    sop_event,
    sse_frame,
    team_assignment,
)
from fastapi.testclient import TestClient

from tests.conftest import (
    REMOTE_MODEL_ID,
    channel_with_agent,
    create_agent,
    live_backend,
    outcomes,
    send,
    sse_frames,
)
from tests.scripted import DELAY, FAIL, IGNORE, ManualScheduler, ScriptedAgentSeam

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


@contextmanager
def _poisoned_target(data_dir: Path, channel_id: str) -> Iterator[None]:
    """把该频道变成「channel 行在、channel 型 target 影子行缺失」——本地旧库只读取证确认的形态。

    这是缺陷的唯一触发条件（见 `test_event_stream_of_a_channel_whose_target_row_is_missing`）：
    v4 代码打开一个 v3 库时，`channel` 表被原样留下，而承载作用域的 `target` 影子行不会被补
    （旧库的成员关系在 `channel_member` / `channel_read` 里，代码已不再读它们）。测试用**最小数据**
    构造这一形态，因此它不依赖任何迁移或旧库 fixture。

    只删三张表里的行，不动别的：

    - `target` 影子行（`message.target_id` 的外键因此指向空——旧库写入时该外键还不存在）；
    - `target_member` / `target_read` 里该频道的行（旧库里这两样本来在 `channel_member` /
      `channel_read` 表里）。

    删完立刻自校验「channel 行还在」，否则用例会退化成「频道不存在」那条既有路径。
    """
    conn = sqlite3.connect(data_dir / "alibot.db", isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        assert conn.execute("SELECT COUNT(*) FROM channel WHERE id = ?", (channel_id,)).fetchone()[0] == 1
        conn.execute("DELETE FROM target_member WHERE target_id = ?", (channel_id,))
        conn.execute("DELETE FROM target_read WHERE target_id = ?", (channel_id,))
        conn.execute("DELETE FROM target WHERE id = ?", (channel_id,))
        # 前提证据：channel 行**仍**在（列表页因此照旧 200），缺的只有作用域那一行。
        assert conn.execute("SELECT COUNT(*) FROM channel WHERE id = ?", (channel_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM target WHERE id = ?", (channel_id,)).fetchone()[0] == 0
    finally:
        conn.close()
    yield


def test_unknown_channel_event_stream_is_not_found(client: TestClient) -> None:
    response = client.get("/api/channels/ch_missing/events")

    assert response.status_code == 404


def test_event_stream_of_a_channel_whose_target_row_is_missing(data_dir: Path, launched: dict) -> None:
    """作用域行缺失时必须在**发出响应之前** 404，而不是先发 200 再中途抛错。

    这是本地旧库（只读取证）那次不一致的确切路径（v3 库被 v4 代码打开）：`GET /api/channels` 照旧 200
    （`channel` 行还在），点进频道却全部失效。对事件流而言，症状比别的端点更糟——
    `StreamingResponse` 的 headers（200）在 `stream()` 跑之前就已经送出，于是
    `service.channel_messages` → `db.get_target` 抛出的 `NotFound` 再也换不成 404：
    Starlette 只能报 `RuntimeError: Caught handled exception, but response already started`，
    客户端拿到 **200 + 0 字节** 后断流。

    断言写两件事：状态码是 **404**，且载荷是统一的错误封装（``error.code == "not_found"``）。
    旧代码在这条路径上红得比断言更早——``RuntimeError`` 会直接从 ``client.get`` 里抛出来（见下），
    用例因此是失败而不是「拿到了 200」；修复后它才变成一个正常的 404 响应。

    红证据（修复前，真 uvicorn + 真 socket，堆栈逐帧）：

    - `api.py:343 stream()` → `service.py:101 channel_messages` → `db.py:794 list_messages`
      → `db.py:622 get_target` → `NotFound: target 不存在：ch_…`；
    - 随后 Starlette `responses.py:253` → `RuntimeError: Caught handled exception, but response
      already started`，客户端 `bytes received: b''`。
    """
    http = launched["client"]  # 与 client 同一后端；这里只用它的 data_dir 构造形态
    http.post("/api/agents", json=AGENT_PAYLOAD)
    channel = _channel_with(http, http.get("/api/agents").json()["agents"][0]["id"])
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "影子行没了"})
    # 先证明这条频道在**正常**形态下是可订阅的：否则下面的 404 成了恒真的假证据。
    assert sse_frames(http, channel["id"], after_seq=0)

    with _poisoned_target(data_dir, channel["id"]):
        response = http.get(f"/api/channels/{channel['id']}/events")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


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

    # ``typing`` 的收口帧与收口后的状态点是**两次独立推送**：typing=False 在轮次的 try 尾部发，
    # 状态点则在 finally 里「清掉 in-flight 之后」才发（顺序见 orchestration.run_round）。所以
    # 「看到 typing=False」并不等于「online 那一帧已经到了」——最后一条状态要显式等，不能裸断言。
    _wait_for(
        events,
        lambda items: [e["status"] for e in items if e["type"] == "member_status"][-1:] == ["online"],
    )

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
    """只有可补拉事件的 SSE ``id`` 是频道内 seq；快照 / live-only 帧没有 ``id``。

    前端重连的游标由 ``message`` / ``round_outcome`` 推进（#141 / ADR-0020）；把 ``member_status``
    这类快照帧也写成 ``id`` 会让 ``Last-Event-ID`` 落在「库里没有对应行」的位置。
    """
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    channel = _channel_with(http, agent["id"])
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "一条"})

    with http.stream("GET", f"/api/channels/{channel['id']}/events", params={"after_seq": 0}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        frames: list[dict] = []
        current: dict = {"id": None, "type": None}
        for line in response.iter_lines():
            text = line.decode() if isinstance(line, bytes) else line
            if text.startswith("id: "):
                current["id"] = text[len("id: ") :]
            elif text.startswith("event: "):
                current["type"] = text[len("event: ") :]
            elif text.startswith("data: "):
                current["data"] = json.loads(text[len("data: ") :])
                frames.append(current)
                current = {"id": None, "type": None}
            elif text.startswith(":"):
                break

    replayable = [frame for frame in frames if frame["type"] in {"message", "round_outcome"}]
    live_only = [frame for frame in frames if frame["type"] not in {"message", "round_outcome"}]
    assert replayable, frames
    assert all(frame["id"] == str(frame["data"]["seq"]) for frame in replayable)
    assert all(frame["id"] is None for frame in live_only), live_only
    assert "1" in [frame["id"] for frame in replayable]


def test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage(data_dir: Path, monkeypatch) -> None:
    """例行轮换必须先发 ``event: rotate``：前端据此区分「服务端请我重连」与「后端挂了」。

    没有这一帧，555 秒后的主动收尾会被前端当成掉线，每分钟闪一次「后端不可达」——
    那既影响可用性，也是前端不诚实（把服务端的正常行为说成故障）。
    """
    monkeypatch.setenv("ALIBOT_SSE_ROTATE_SECONDS", "1")
    scheduler, seam = ManualScheduler(), ScriptedAgentSeam()
    with (
        live_backend(data_dir, scheduler=scheduler, seam=seam) as base_url,
        httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, read=None), trust_env=False) as http,
    ):
        agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
        channel = _channel_with(http, agent["id"])
        http.post(f"/api/channels/{channel['id']}/messages", json={"body": "一条"})

        names: list[str] = []
        with http.stream("GET", f"/api/channels/{channel['id']}/events", params={"after_seq": 0}) as response:
            current = ""
            for line in response.iter_lines():
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("event: "):
                    current = text[len("event: ") :]
                    names.append(current)
                elif text.startswith("data: ") and current == "rotate":
                    break

    assert "rotate" in names, names
    # 轮换帧必须是**命名事件**（`event: rotate`），不是普通的 keep-alive 注释：
    # 注释帧不会触发 EventSource 的任何回调，前端根本看不到它。
    assert names.count("rotate") == 1
    # 轮换前先把该发的事件（消息 + 状态点快照）发完，不丢事件。
    assert "message" in names
    assert "member_status" in names


def test_system_join_event_is_pushed_as_a_message_and_replayable(live: dict) -> None:
    """穿透加入的 `system` 事件消息走**同一类 message 事件**，且重连带 last_seq 能补齐。

    它是「成员变化是可见的事实」在事件流上的形态：前端不需要为它另开一套处理。
    """
    http = live["http"]
    insider = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    outsider = http.post("/api/agents", json=dict(AGENT_PAYLOAD, name="局外人")).json()["agent"]
    live["seam"].behaviors[insider["id"]] = IGNORE
    live["seam"].replies[outsider["id"]] = "穿透回复"
    channel = _channel_with(http, insider["id"])
    # 把局外人从频道里移出不可能（首版没有该端点），所以用「它从未加入」的频道：
    # 这里直接建一个不含它的频道。
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": f"@{outsider['display_name']} 来一句"})
    live["scheduler"].drain()

    events = sse_frames(http, channel["id"], after_seq=0)
    message_events = [event for event in events if event["type"] == "message"]
    kinds = [event["message"]["kind"] for event in message_events]
    assert kinds == ["human", "system", "agent"]
    system_event = message_events[1]
    assert system_event["message"]["author"]["id"] == outsider["id"]
    # 重连带 last_seq=1 只补 system 与回复两条（不重复、不错序）。
    replayed = sse_frames(http, channel["id"], after_seq=1)
    assert [event["seq"] for event in replayed if event["type"] == "message"] == [2, 3]


def test_budget_exhaustion_is_visible_in_the_round_outcome_event(live: dict) -> None:
    """`budget-exhausted` 作为轮次结果事件推给订阅者（循环上界的取证面在事件流上也可见）。"""
    http = live["http"]
    first = http.post("/api/agents", json=dict(AGENT_PAYLOAD, name="甲")).json()["agent"]
    second = http.post("/api/agents", json=dict(AGENT_PAYLOAD, name="乙")).json()["agent"]
    live["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = http.post(
        "/api/channels",
        json={"name": "互相点名", "description": "", "member_ids": [first["id"], second["id"]]},
    ).json()["channel"]
    http.post(f"/api/channels/{channel['id']}/messages", json={"body": "开始"})
    for _ in range(12):
        live["scheduler"].drain()
        if live["scheduler"].pending_count() == 0:
            break

    events = sse_frames(http, channel["id"], after_seq=0)
    statuses = [event["outcome"]["status"] for event in events if event["type"] == "round_outcome"]
    assert "budget-exhausted" in statuses
    assert statuses.count("budget-exhausted") >= 1
    # 预算端点与事件流说的是同一条事实。
    state = http.get(f"/api/channels/{channel['id']}/budget").json()["budget"]
    assert state["used"] == state["limit"] == 3 and state["remaining"] == 0


# ─── #141：live-only 运行事件（agent_event / team_assignment / plan_update / sop_event / approval_*）───


def _start_reader(
    http: httpx.Client, path: str, params: dict | None = None
) -> tuple[list[dict], threading.Event, threading.Thread]:
    """后台逐帧读一段事件流，同时保留 SSE ``id`` 行（``sse_frames`` 只解析 data，看不到 id）。

    事件流是持续的，逐帧读取必须走真 socket（``httpx.Client.stream``）；主线程负责触发事件，
    读线程只做「把帧按到达顺序记下来」。``id`` 与 ``event`` 按 SSE 语义在 ``data`` 之前到达。
    """
    frames: list[dict] = []
    stop = threading.Event()

    def reader() -> None:
        current: dict = {"id": None, "event": None}
        with http.stream("GET", path, params=params or {}) as response:
            for line in response.iter_lines():
                if stop.is_set():
                    return
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("id: "):
                    current["id"] = text[len("id: ") :]
                elif text.startswith("event: "):
                    current["event"] = text[len("event: ") :]
                elif text.startswith("data: "):
                    current["data"] = json.loads(text[len("data: ") :])
                    frames.append(current)
                    current = {"id": None, "event": None}

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return frames, stop, thread


def _wait_frames(frames: list[dict], predicate, *, label: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(list(frames)):
            return
        time.sleep(0.01)
    raise AssertionError(f"等不到 {label}；已收到的帧：{frames}")


def _stop_reader(stop: threading.Event, thread: threading.Thread) -> None:
    stop.set()
    thread.join(timeout=5)


def test_agent_events_are_live_only_frames_with_attribution(live: dict) -> None:
    """一轮运行产生 ``agent_event``：字段完整、归属到 target/成员/回复，且**不发** ``id``。

    事件是直播面（ADR-0020）：它只在订阅者在线时到达，因此必须在触发那一轮**之前**连上；
    这正是浏览器 ``EventSource`` 的时序。
    """
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    live["seam"].replies[agent["id"]] = "完整回复"
    channel = _channel_with(http, agent["id"])

    frames, stop, reader = _start_reader(http, f"/api/channels/{channel['id']}/events", {"after_seq": 0})
    try:
        http.post(f"/api/channels/{channel['id']}/messages", json={"body": "请回答"})
        live["scheduler"].drain()
        _wait_frames(
            frames,
            lambda items: (
                any(item["data"].get("type") == "agent_event" for item in items)
                and any(item["data"].get("type") == "round_outcome" for item in items)
            ),
            label="agent_event 与 round_outcome",
        )
    finally:
        _stop_reader(stop, reader)

    agent_frames = [frame for frame in frames if frame["data"].get("type") == "agent_event"]
    assert {frame["data"]["event_type"] for frame in agent_frames} == {
        "REPLY_START",
        "TEXT_BLOCK_DELTA",
        "REPLY_END",
    }
    for frame in agent_frames:
        data = frame["data"]
        assert frame["event"] == "agent_event"
        assert frame["id"] is None, "live-only 事件不得发 id 行（不能推进重连游标）"
        assert data["member_id"] == agent["id"]
        assert data["reply_id"]
        assert data["target_id"] == channel["id"]
        assert isinstance(data["payload"], dict)

    message_frame = next(frame for frame in frames if frame["data"].get("type") == "message")
    outcome_frame = next(frame for frame in frames if frame["data"].get("type") == "round_outcome")
    assert message_frame["id"] == str(message_frame["data"]["seq"])
    assert outcome_frame["id"] == str(outcome_frame["data"]["seq"])


def _publish_runtime_events(
    live: dict, target_id: str, task_id: str | None, frames: list[dict], expected: set[str]
) -> None:
    """经 EventBus 的公共发布入口反复推新事件，直到读线程看到全部类型。

    为什么要重复：订阅发生在本进程内，但测试只能从「帧有没有到」判断它是否已经建立；
    这里不 sleep 猜时序，而是持续发布直到可观察结果成立。重复发布本身也是幂等读法（无 id）。
    """
    events = live["app"].state.events
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        events.publish(
            target_id,
            agent_event(
                AgentEventRecord(
                    type="TEXT_BLOCK_DELTA",
                    target_id=target_id,
                    member_id="mem_live",
                    reply_id="reply_live",
                    payload={"block_id": "b1", "delta": "直播增量"},
                )
            ),
        )
        events.publish(
            target_id,
            team_assignment(
                team_run_id="run_live",
                assignment_id="assignment_live",
                task_id=task_id,
                member_id="mem_live",
                reply_id="reply_live",
                state="started",
            ),
        )
        events.publish(
            target_id,
            plan_update(plan_run_id="plan_live", member_id="mem_live", task={"id": "plan_task_live"}),
        )
        events.publish(
            target_id,
            sop_event(sop_run_id="sop_live", step_index=0, phase="started", payload={"step": "verify"}),
        )
        events.publish(
            target_id,
            approval_required(
                reply_id="reply_live",
                member_id="mem_live",
                tool_calls=[{"tool_call_id": "call_live", "name": "write_file"}],
            ),
        )
        events.publish(
            target_id,
            approval_resolved(reply_id="reply_live", state="resolved", decisions=[{"confirmed": False}]),
        )
        seen = {frame["data"].get("type") for frame in frames}
        if expected <= seen:
            return
        time.sleep(0.02)
    raise AssertionError(f"等不到 live-only 事件 {sorted(expected)}；已收到的帧：{frames}")


def test_task_stream_forwards_every_live_runtime_event_and_task_id(live: dict) -> None:
    """任务线程 SSE 也透传运行记录；``team_assignment.task_id`` 在 task target 上非空。"""
    http = live["http"]
    channel = http.post(
        "/api/channels", json={"name": "任务事件", "description": "", "member_ids": []}
    ).json()["channel"]
    task = http.post(f"/api/channels/{channel['id']}/tasks", json={"title": "整理"}).json()["task"]

    frames, stop, reader = _start_reader(http, f"/api/tasks/{task['id']}/events", {"after_seq": 0})
    try:
        _publish_runtime_events(
            live,
            task["target_id"],
            task["id"],
            frames,
            {
                "agent_event",
                "team_assignment",
                "plan_update",
                "sop_event",
                "approval_required",
                "approval_resolved",
            },
        )
        _wait_frames(
            frames,
            lambda items: any(item["data"].get("type") == "message" for item in items),
            label="任务线程快照消息",
        )
    finally:
        _stop_reader(stop, reader)

    live_frames = [
        frame
        for frame in frames
        if frame["data"].get("type") in {"agent_event", "team_assignment", "plan_update", "sop_event"}
        or frame["data"].get("type", "").startswith("approval_")
    ]
    assert live_frames
    for frame in live_frames:
        assert frame["id"] is None, "新事件不得发 id 行"
        assert frame["data"]["target_id"] == task["target_id"]
    assignment = next(frame["data"] for frame in live_frames if frame["data"]["type"] == "team_assignment")
    assert assignment["task_id"] == task["id"]
    assert assignment["state"] == "started"
    # 既有可补拉事件在任务线程上仍带 id（重连游标只由它们推进）。
    message_frame = next(frame for frame in frames if frame["data"].get("type") == "message")
    assert message_frame["id"] == str(message_frame["data"]["seq"])


@pytest.mark.parametrize("kind", ["channel", "dm"])
def test_channel_and_dm_streams_forward_live_runtime_events_after_a_cursor(live: dict, kind: str) -> None:
    """重连带 ``after_seq>=1`` 时，live-only 事件（没有 seq）仍必须透传。

    旧实现按 ``seq < after_seq`` 丢弃队列事件；新事件没有 seq（视作 0），会在重连后的流里
    被整批静默吞掉——这正是「运行面板只有刷新才更新」的根因。
    """
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    if kind == "channel":
        channel = _channel_with(http, agent["id"])
        target_id = channel["id"]
        path = f"/api/channels/{channel['id']}/events"
        http.post(f"/api/channels/{channel['id']}/messages", json={"body": "先有一条消息"})
    else:
        dm = http.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]
        target_id = dm["id"]
        path = f"/api/dms/{dm['id']}/events"
        http.post(f"/api/dms/{dm['id']}/messages", json={"body": "先有一条私信"})
    live["scheduler"].drain()

    frames, stop, reader = _start_reader(http, path, {"after_seq": 1})
    try:
        _publish_runtime_events(
            live,
            target_id,
            None,
            frames,
            {
                "agent_event",
                "team_assignment",
                "plan_update",
                "sop_event",
                "approval_required",
                "approval_resolved",
            },
        )
    finally:
        _stop_reader(stop, reader)

    live_frames = [frame for frame in frames if frame["id"] is None]
    assert {frame["data"]["type"] for frame in live_frames} >= {
        "agent_event",
        "team_assignment",
        "plan_update",
        "sop_event",
        "approval_required",
        "approval_resolved",
    }


def test_sse_frame_emits_id_only_for_replayable_events() -> None:
    """帧形状的数据契约：只有可补拉的 message / round_outcome 带 ``id``。"""
    assert sse_frame({"type": "message", "seq": 4, "message": {}}).startswith("id: 4\n")
    assert sse_frame({"type": "round_outcome", "seq": 5, "outcome": {}}).startswith("id: 5\n")
    for type_ in (
        "agent_event",
        "team_assignment",
        "plan_update",
        "sop_event",
        "approval_required",
        "approval_resolved",
        "member_status",
        "typing",
    ):
        frame = sse_frame({"type": type_, "payload": {}})
        assert not frame.startswith("id: "), f"{type_} 是 live-only / 快照事件，不得带 id"
        assert f"event: {type_}\n" in frame


def test_event_bus_queue_full_drops_events_without_blocking_the_writer() -> None:
    """跟不上的订阅者丢事件、不阻塞写路径；已送达的既有事件仍在队列里（先进先出）。"""
    bus = EventBus()
    with bus.subscribe("target-1") as subscriber:
        for index in range(QUEUE_LIMIT * 2):
            published = bus.publish(
                "target-1",
                {"type": "typing", "seq": index, "member_id": "mem", "active": True, "reason": "订阅者很慢"},
            )
            assert published["target_id"] == "target-1"

        assert subscriber.qsize() == QUEUE_LIMIT
        assert subscriber.get_nowait()["seq"] == 0
        # 没订阅者的 target 照旧只做构造，不产生队列。
        assert bus.publish("target-2", {"type": "typing"})["target_id"] == "target-2"


def test_task_round_emits_agent_events_on_the_task_target(live: dict) -> None:
    """真实的一轮任务线程也直播 ``agent_event``：归属是 **task target**，不是频道。

    这是任务线程运行面板的数据来源；后续票的 team 分派 / Plan / 审批事件走同一条总线与同一种帧。
    """
    http = live["http"]
    agent = http.post("/api/agents", json=AGENT_PAYLOAD).json()["agent"]
    live["seam"].replies[agent["id"]] = "任务线程回复"
    channel = _channel_with(http, agent["id"])
    task = http.post(f"/api/channels/{channel['id']}/tasks", json={"title": "直播任务"}).json()["task"]
    http.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": agent["id"]})
    live["scheduler"].drain()

    frames, stop, reader = _start_reader(http, f"/api/tasks/{task['id']}/events", {"after_seq": 0})
    try:
        http.post(f"/api/tasks/{task['id']}/messages", json={"body": "请处理"})
        live["scheduler"].drain()
        _wait_frames(
            frames,
            lambda items: (
                any(item["data"].get("type") == "agent_event" for item in items)
                and any(item["data"].get("type") == "round_outcome" for item in items)
            ),
            label="任务线程的 agent_event 与 round_outcome",
        )
    finally:
        _stop_reader(stop, reader)

    agent_frames = [frame for frame in frames if frame["data"].get("type") == "agent_event"]
    assert agent_frames
    for frame in agent_frames:
        assert frame["id"] is None
        assert frame["data"]["target_id"] == task["target_id"]
        assert frame["data"]["member_id"] == agent["id"]
        assert frame["data"]["reply_id"]
    outcome_frame = next(frame for frame in frames if frame["data"].get("type") == "round_outcome")
    assert outcome_frame["id"] == str(outcome_frame["data"]["seq"])
    assert outcome_frame["data"]["outcome"]["target_id"] == task["target_id"]
