"""进程内事件流：把「后端发生了什么」广播给订阅了同一个频道的浏览器。

为什么需要进程内广播：前端要「不刷新就看到 Agent 的完整回复」，而 Agent 的回复由**后端进程内的
唤醒编排**落库（不经 HTTP），所以它推不到浏览器。事件流是这条路径；HTTP 面仍只属于人类面。

事件形状（``type``）：

- ``message``        新消息落库（载荷与 ``GET /messages`` 的元素同形）
- ``member_status``  成员状态点（``member_id`` + ``status`` ∈ 四态）
- ``typing``         「正在回复」指示（``member_id``：该成员有一轮 in-flight）
- ``round_outcome``  轮次结果（载荷与只读端点同形）

**补拉的正本是数据库，不是这个总线**：总线的缓冲只覆盖「订阅那一刻之后」的事件，而
「重连带 ``last_seq`` 补拉错过的消息」由事件流端点直接读库补齐（见 ``api.py`` 的
``channel_events``）。所以这里不需要保留历史，只要把当下的广播送达。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import Any

# 队列容量：单个订阅者跟不过来时不阻塞发布方（丢的是事件，不是消息——消息已在库里）。
QUEUE_LIMIT = 256


class EventBus:
    """按频道分组的进程内广播。线程安全（发布方是唤醒编排的工作线程）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, set[queue.Queue[dict[str, Any]]]] = {}

    def publish(self, target_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        """广播一条事件；返回实际送出的载荷（补上 ``target_id``）。"""
        payload = dict(event)
        payload.setdefault("target_id", target_id)
        with self._lock:
            subscribers = list(self._subscribers.get(target_id, ()))
        for subscriber in subscribers:
            # 跟不上的订阅者只会漏事件、不会阻塞写路径；它的下一次标读/刷新走 HTTP 全量。
            with suppress(queue.Full):
                subscriber.put_nowait(payload)
        return payload

    @contextmanager
    def subscribe(self, target_id: str) -> Iterator[queue.Queue[dict[str, Any]]]:
        channel: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=QUEUE_LIMIT)
        with self._lock:
            self._subscribers.setdefault(target_id, set()).add(channel)
        try:
            yield channel
        finally:
            with self._lock:
                group = self._subscribers.get(target_id)
                if group is not None:
                    group.discard(channel)
                    if not group:
                        self._subscribers.pop(target_id, None)


def sse_frame(event: Mapping[str, Any]) -> str:
    """把事件编码成一帧 SSE：``id`` 用频道内消息 ``seq``（前端重连的游标就是它）。"""
    import json

    body = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    seq = int(event.get("seq", 0))
    return f"id: {seq}\nevent: {event.get('type', 'message')}\ndata: {body}\n\n"
