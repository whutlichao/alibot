"""建任务在所属频道流里留下一条可进入任务线程的 system 指针消息（#198 / ADR-0028）。

判据全部落在 HTTP 面与事件流上（票面 `Observable acceptance` 第 2 条）：

1. ``POST /api/channels/{id}/tasks`` 之后，频道消息列表**最后一条**是
   ``kind='system'``、``task_pointer=<task id>``、``task_ref is None``、正文
   「任务已创建：<title>」、作者 owner；任务线程仍只有它自己的那条 system；
2. 任务载荷带 ``message_count``（线程消息总条数），随线程消息增加；
3. 频道 / 任务两侧的 ``unread_count`` 都保持 0，频道 Agent 不被唤醒；
4. 频道事件流能收到这条 ``message`` 事件（沿用既有真 socket 读帧手法）；
5. 频道消息插入失败时整个建任务回滚：任务行与两条消息都不存在；
6. ``task_pointer`` **不接受**客户端写入（``MessageCreate`` 的 ``extra="forbid"``）。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest
from alibot.db import Database
from fastapi.testclient import TestClient

from tests.conftest import channel_with_agent, create_agent, create_channel, messages

OWNER_ID = "owner"


def _create_task(client: TestClient, channel_id: str, title: str) -> Any:
    return client.post(f"/api/channels/{channel_id}/tasks", json={"title": title})


def test_creating_a_task_appends_exactly_one_channel_pointer_message(client: TestClient) -> None:
    channel = create_channel(client, name="产品迭代").json()["channel"]

    created = _create_task(client, channel["id"], "整理验收清单")

    assert created.status_code == 201
    payload = created.json()
    task = payload["task"]
    pointer = payload["message"]
    assert payload["target"]["id"] == task["target_id"]

    # 返回载荷里的消息与读面一致（Studio 的 `submitTask` 直接吸收它，不额外取一次列表）。
    assert pointer is not None
    assert messages(client, channel["id"]) == [pointer]
    assert pointer["kind"] == "system"
    assert pointer["target_id"] == channel["id"]
    assert pointer["task_pointer"] == task["id"]
    assert pointer["task_ref"] is None
    assert pointer["body"] == "任务已创建：整理验收清单"
    assert pointer["author"]["id"] == OWNER_ID

    # 任务线程自己的首条 system 逐字不变：两处正文一致，但指针字段互不越界。
    thread = client.get(f"/api/tasks/{task['id']}/messages").json()
    assert [
        (message["kind"], message["body"], message["task_ref"], message["task_pointer"])
        for message in thread["messages"]
    ] == [("system", "任务已创建：整理验收清单", None, None)]

    # `message_count` 是任务线程的消息总条数（不是未读），随线程消息增长。
    assert task["message_count"] == 1
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["message_count"] == 1
    assert client.get("/api/tasks").json()["tasks"][0]["message_count"] == 1

    client.post(f"/api/tasks/{task['id']}/messages", json={"body": "补充一句"})
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["message_count"] == 2


def test_channel_pointer_does_not_wake_anyone_or_count_as_unread(
    client: TestClient, harness: dict
) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    task = _create_task(client, channel["id"], "检查未读边界").json()["task"]

    # 系统事件消息不唤醒：调度器里没有任何一轮待跑，适配层一次都没被调到。
    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].call_count == 0
    # 频道「消息 N」包含它（last_seq 前进），但未读徽标不计 system（频道 / 任务 / 工作台三处都是 0）。
    channel_after = client.get("/api/channels").json()["channels"][0]
    assert channel_after["last_seq"] == 1
    assert channel_after["unread_count"] == 0
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["unread_count"] == 0
    assert client.get("/api/workspace").json()["unread_total"] == 0


def test_channel_event_stream_carries_the_pointer_message(live: dict) -> None:
    http = live["http"]
    channel = http.post(
        "/api/channels", json={"name": "事件流", "description": ""}
    ).json()["channel"]

    events: list[dict] = []
    stop = threading.Event()
    connected = threading.Event()

    def reader() -> None:
        with http.stream(
            "GET", f"/api/channels/{channel['id']}/events", params={"after_seq": 0}
        ) as response:
            for line in response.iter_lines():
                connected.set()
                if stop.is_set():
                    return
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("data: "):
                    events.append(json.loads(text[len("data: ") :]))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        # 先确认订阅已建立（事件流发过第一帧，至少是一条 keep-alive 注释），再触发写路径：
        # 否则「帧没来」可能只是订阅迟到，而不是发布缺失。
        assert connected.wait(timeout=8.0), "频道事件流没有建立订阅"
        created = http.post(
            f"/api/channels/{channel['id']}/tasks", json={"title": "推送入口"}
        ).json()
        task_id = created["task"]["id"]
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if any(
                event.get("message", {}).get("task_pointer") == task_id for event in events
            ):
                break
            time.sleep(0.01)
        delivered = [
            event
            for event in events
            if event.get("type") == "message" and event["message"].get("task_pointer") == task_id
        ]
        assert delivered, f"频道事件流没有送达指针消息；已收到的帧：{events}"
        message = delivered[0]["message"]
        assert message["kind"] == "system"
        assert message["body"] == "任务已创建：推送入口"
        assert message["author"]["id"] == OWNER_ID
        # 它仍是可补拉的 `message` 事件：帧的游标就是消息自己的 seq。
        assert delivered[0]["seq"] == message["seq"]
    finally:
        stop.set()
        thread.join(timeout=5)


def test_task_creation_rolls_back_when_the_channel_pointer_insert_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = create_channel(client, name="产品迭代").json()["channel"]

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("注入：频道指针插入失败")

    # 注入点在**建任务事务内部**：此时 task 行、target、成员面与任务线程首条记录都已写过。
    monkeypatch.setattr(Database, "_append_task_pointer_locked", explode)

    with pytest.raises(RuntimeError, match="注入：频道指针插入失败"):
        _create_task(client, channel["id"], "原子性")

    # 任务行与两条消息都不存在：不存在「任务建好了、频道入口没落」的半成品。
    assert client.get("/api/tasks").json()["tasks"] == []
    assert messages(client, channel["id"]) == []


def test_clients_cannot_write_task_pointer(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    task = _create_task(client, channel["id"], "指针只由后端写").json()["task"]

    forged = client.post(
        f"/api/channels/{channel['id']}/messages",
        json={"body": "伪造一条指针", "task_pointer": task["id"]},
    )

    assert forged.status_code == 400
    assert forged.json()["error"]["code"] == "validation_failed"
    assert [message["task_pointer"] for message in messages(client, channel["id"])] == [task["id"]]
