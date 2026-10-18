"""进程内事件流：把「后端发生了什么」广播给订阅了同一个 target 的浏览器。

为什么需要进程内广播：前端要「不刷新就看到 Agent 的完整回复」，而 Agent 的回复由**后端进程内的
唤醒编排**落库（不经 HTTP），所以它推不到浏览器。事件流是这条路径；HTTP 面仍只属于人类面。

事件形状（``type``）：

- ``message``        新消息落库（载荷与 ``GET /messages`` 的元素同形）
- ``member_status``  成员状态点（``member_id`` + ``status`` ∈ 四态）
- ``typing``         「正在回复」指示（``member_id``：该成员有一轮 in-flight）
- ``round_outcome``  轮次结果（载荷与只读端点同形）
- ``agent_event``    ``AgentEvent`` 白名单透传（ADR-0020；成员过程，不落库）
- ``team_assignment`` 分派开始/成功/失败/预算耗尽（运行记录快照 ``GET /team-runs``）
- ``plan_update``    Plan 任务创建/更新/删除（快照 ``GET /plan``）
- ``sop_event``      SOP 步骤开始/结束/verdict（快照 ``GET /sop-runs/{id}``）
- ``approval_required`` / ``approval_resolved`` 审批请求与结果回执（快照 ``GET /approvals``）

**两层，不是两套正本**（ADR-0020）：``message`` / ``round_outcome`` 是**可补拉**的，重连带
``after_seq`` 由端点读库补齐，SSE 帧带 ``id: <seq>``；其余事件是 **live-only**，断线丢失、
重启消失，**不发 ``id`` 行**——否则浏览器会把 ``Last-Event-ID`` 推进到库里不存在的游标。

**补拉的正本是数据库，不是这个总线**：总线的缓冲只覆盖「订阅那一刻之后」的事件，而
「重连带 ``last_seq`` 补拉错过的消息」由事件流端点直接读库补齐（见 ``api.py`` 的
频道与任务事件流）。所以这里不需要保留历史，只要把当下的广播送达。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import Any

# 队列容量：单个订阅者跟不过来时不阻塞发布方（丢的是事件，不是消息——消息已在库里）。
QUEUE_LIMIT = 256

# 可补拉（读库补齐、带 `id: <seq>`）的事件类型。其余一律 live-only：不发 id、不补拉。
REPLAYABLE_TYPES = frozenset({"message", "round_outcome"})

# live-only 运行事件（规格 §4.5）。前端据这份词表决定「消费 / 忽略」，后端据它决定不发 id。
LIVE_EVENT_TYPES = frozenset(
    {
        "agent_event",
        "team_assignment",
        "plan_update",
        "sop_event",
        "approval_required",
        "approval_resolved",
    }
)


class EventBus:
    """按 target 分组的进程内广播。线程安全（发布方是唤醒编排的工作线程）。"""

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


def is_live_only(event: Mapping[str, Any]) -> bool:
    """该事件是否属于 live-only 运行事件（规格 §4.5）：不发 ``id``、断线不补拉。"""
    return event.get("type") in LIVE_EVENT_TYPES


def is_replayable(event: Mapping[str, Any]) -> bool:
    """该事件是否可补拉：只有它能带 ``id: <seq>``（重连游标只由这类事件推进）。

    与 :func:`is_live_only` 互补：一个类型即使同时出现在两份词表里，也**不能**推进游标——
    live-only 的词表优先，重连补拉永远只覆盖消息与轮次结果。
    """
    return event.get("type") in REPLAYABLE_TYPES and not is_live_only(event)


def sse_frame(event: Mapping[str, Any]) -> str:
    """把事件编码成一帧 SSE。

    ``id`` 只写给可补拉事件（``message`` / ``round_outcome``）；live-only 运行事件**不发** ``id``
    行，浏览器因此保留最后一个可补拉事件的 id，重连不会把游标推到不可补拉的位置。
    """
    import json

    body = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    lines: list[str] = []
    if is_replayable(event):
        lines.append(f"id: {int(event.get('seq', 0))}")
    lines.append(f"event: {event.get('type', 'message')}")
    lines.append(f"data: {body}")
    return "\n".join(lines) + "\n\n"


# ─── 新运行事件的公共发布入口（后续票的领域代码调用）──────────────────────
#
# 这些构造函数只做「形状 + 字段名」的收口：发布仍是 ``bus.publish(target_id, event)``。
# 把它们集中在这里，是为了让「帧字段」只有一份正本（规格 §4.5），而不是散在各调用点。


def agent_event(record: Any) -> dict[str, Any]:
    """``AgentEventRecord`` → ``agent_event`` 帧载荷。

    事件是成员过程的直播（ADR-0020）：``payload`` 已在 seam 侧按白名单收窄（不含 secrets /
    base64），这里只补帧字段，不改写内容。
    """
    return {
        "type": "agent_event",
        "event_type": record.type,
        "member_id": record.member_id,
        "reply_id": record.reply_id,
        "target_id": record.target_id,
        "payload": record.payload,
    }


def team_assignment(
    *,
    team_run_id: str,
    assignment_id: str,
    member_id: str,
    reply_id: str,
    state: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """一次分派的运行记录帧；``task_id`` 在 task target 上非空。"""
    return {
        "type": "team_assignment",
        "team_run_id": team_run_id,
        "assignment_id": assignment_id,
        "task_id": task_id,
        "member_id": member_id,
        "reply_id": reply_id,
        "state": state,
    }


def plan_update(*, plan_run_id: str, member_id: str, task: Mapping[str, Any]) -> dict[str, Any]:
    """Plan 任务的创建/更新/删除帧（``task`` 是 Plan 任务行的快照）。"""
    return {
        "type": "plan_update",
        "plan_run_id": plan_run_id,
        "member_id": member_id,
        "task": dict(task),
    }


def sop_event(*, sop_run_id: str, step_index: int, phase: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """SOP 步骤开始/结束/verdict 帧。"""
    return {
        "type": "sop_event",
        "sop_run_id": sop_run_id,
        "step_index": step_index,
        "phase": phase,
        "payload": dict(payload),
    }


def approval_required(
    *, reply_id: str, member_id: str, tool_calls: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """需要人类确认的审批请求帧（``tool_calls`` 只含可展示的摘要字段）。"""
    return {
        "type": "approval_required",
        "reply_id": reply_id,
        "member_id": member_id,
        "tool_calls": [dict(call) for call in tool_calls],
    }


def approval_resolved(*, reply_id: str, state: str, decisions: list[Mapping[str, Any]]) -> dict[str, Any]:
    """审批结果回执帧（``state`` 含 resolved / expired 等后端事实）。"""
    return {
        "type": "approval_resolved",
        "reply_id": reply_id,
        "state": state,
        "decisions": [dict(decision) for decision in decisions],
    }
