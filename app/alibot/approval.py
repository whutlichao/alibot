"""进程内审批代理（HITL，ADR-0021 §四 / 规格 §3.6）。

一条 ``RequireUserConfirmEvent`` 的生命周期：

1. 轮次 runner 收到事件 → :meth:`ApprovalBroker.register`：``approval_request`` 落库为
   ``pending``，发 SSE ``approval_required``；
2. runner 在**同一个事件循环**里 :meth:`wait`，不占用模型调用；Agent/TeamPipeline 实例因此
   跨等待存活；
3. 人类面 ``POST /api/approvals/{reply_id}`` → :meth:`submit`：只接受 pending、幂等、部分决策
   只回送新决策；全部决定后转 ``approved`` / ``denied``，发 ``approval_resolved``；
4. 默认 600 秒内没有全部决定 → :meth:`wait` 把记录标 ``expired``，runner 负责用
   ``UserInterruptEvent`` 收口该轮（轮次 ``failed``）；跨重启恢复不在首版（D19）。

等待不放在全局队列里按 id 轮询：每轮自己的 ``asyncio.Future`` 是唯一唤醒点，``submit`` 从 HTTP
线程用 ``loop.call_soon_threadsafe`` 投递。所有状态变更都在一把 ``threading.RLock`` 下，
数据库状态是本模块的持久正本，内存只保存「尚未回送给 Agent 的决策」与截止时刻。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, cast

from .db import Database, DomainError, NotFound, utc_now
from .events import EventBus, approval_required, approval_resolved
from .i18n import SOURCE_LOCALE, Locale, render
from .permissions import normalize_rule

_log = logging.getLogger(__name__)

#: 默认审批等待（规格 §3.6 / D19：10 分钟）。
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 600.0

APPROVAL_STATES = ("pending", "approved", "denied", "expired")


class ApprovalWaitTimeout(RuntimeError):
    """审批等待超时：记录已由 broker 标 ``expired``，runner 负责中断该轮。"""

    def __init__(self, seconds: float):
        super().__init__(f"approval wait timed out after {seconds:.0f}s")
        self.seconds = seconds


@dataclass
class _Session:
    """一条 ``reply_id`` 的内存等待状态（进程重启即消失——这正是 D19 的边界）。"""

    deadline: float
    loop: asyncio.AbstractEventLoop | None = None
    future: asyncio.Future[list[dict[str, Any]]] | None = None
    undelivered: list[dict[str, Any]] = field(default_factory=list)

    def drain(self) -> list[dict[str, Any]]:
        decisions = list(self.undelivered)
        self.undelivered.clear()
        return decisions


class ApprovalBroker:
    """按 ``reply_id`` 等待人类决策的进程内 broker（线程安全）。"""

    def __init__(
        self,
        db: Database,
        events: EventBus,
        *,
        timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        locale_of: Callable[[], Locale] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if timeout_seconds <= 0:
            raise ValueError("approval timeout must be positive")
        self._db = db
        self._events = events
        self.timeout_seconds = float(timeout_seconds)
        self._locale_of = locale_of if locale_of is not None else (lambda: SOURCE_LOCALE)
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}

    # ─── 文案 ─────────────────────────────────────────────────────────────
    def _locale(self) -> Locale:
        return self._locale_of()

    def _render(self, key: str, **params: Any) -> str:
        return render(key, self._locale(), **params)

    # ─── 注册 / 读取 ─────────────────────────────────────────────────────
    def register(
        self,
        *,
        reply_id: str,
        target_id: str,
        member_id: str,
        tool_calls: Iterable[Mapping[str, Any]],
        team_run_id: str | None = None,
    ) -> dict[str, Any]:
        """登记（或合并）一条待审批记录；同一个 ``reply_id`` 的新调用只追加、不重置 deadline。"""
        calls = [_tool_call_payload(call) for call in tool_calls]
        with self._lock:
            record = self._db.get_approval_request(reply_id)
            if record is None:
                self._db.create_approval_request(
                    reply_id=reply_id,
                    target_id=target_id,
                    member_id=member_id,
                    team_run_id=team_run_id,
                    tool_calls=calls,
                )
                self._sessions[reply_id] = _Session(deadline=self._monotonic() + self.timeout_seconds)
                record = self._db.get_approval_request(reply_id)
            elif record["state"] == "pending":
                merged = _merge_tool_calls(record["tool_calls"], calls)
                if merged != record["tool_calls"]:
                    self._db.update_approval_request(reply_id, tool_calls=merged)
                    record = self._db.get_approval_request(reply_id)
                self._sessions.setdefault(
                    reply_id, _Session(deadline=self._monotonic() + self.timeout_seconds)
                )
            else:
                # 已经 resolved / expired：保留持久事实，不重新打开。
                return record

            if record is None:
                raise RuntimeError("approval record disappeared during register")
            self._events.publish(
                record["target_id"],
                approval_required(
                    reply_id=reply_id,
                    member_id=record["member_id"],
                    tool_calls=record["tool_calls"],
                ),
            )
            return record

    def record(self, reply_id: str) -> dict[str, Any] | None:
        return self._db.get_approval_request(reply_id)

    def tool_calls(self, reply_id: str) -> list[dict[str, Any]]:
        record = self._db.get_approval_request(reply_id)
        return list(record["tool_calls"]) if record is not None else []

    # ─── 等待 ─────────────────────────────────────────────────────────────
    async def wait(self, reply_id: str) -> list[dict[str, Any]] | None:
        """等下一批新决策；返回 ``None`` 表示超时（记录已标 ``expired``）。

        已结束（approved/denied）或决策先到时会立即返回尚未回送的那批；返回空列表表示
        「没有新的可回送决策」——调用方应检查 Agent 是否仍挂起，不能把空批当成批准。
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            record = self._db.get_approval_request(reply_id)
            if record is None:
                raise NotFound(self._render("error.approval_not_found", reply_id=reply_id))
            session = self._sessions.get(reply_id)
            if session is None:
                session = _Session(deadline=self._monotonic() + self.timeout_seconds)
                self._sessions[reply_id] = session
            if record["state"] != "pending":
                return session.drain()
            if session.undelivered:
                return session.drain()
            if session.future is not None and not session.future.done():
                future = session.future
            else:
                future = loop.create_future()
                session.future = future
                session.loop = loop
            remaining = session.deadline - self._monotonic()

        if remaining <= 0:
            with self._lock:
                return None if self._expire(reply_id) else session.drain()
        try:
            return await asyncio.wait_for(future, remaining)
        except TimeoutError:
            with self._lock:
                if future.done() and not future.cancelled():
                    return future.result()
                return None if self._expire(reply_id) else session.drain()
        finally:
            with self._lock:
                if session.future is future:
                    session.future = None

    # ─── 人类决策 ─────────────────────────────────────────────────────────
    def submit(self, reply_id: str, decisions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """提交一批决策（只接受 pending；幂等；部分决策只回送新决策）。"""
        incoming = [_decision_payload(item) for item in decisions]
        with self._lock:
            record = self._db.get_approval_request(reply_id)
            if record is None:
                raise NotFound(self._render("error.approval_not_found", reply_id=reply_id))

            stored_by_id = {item["tool_call_id"]: item for item in record["decisions"]}
            tool_ids = {str(call.get("id") or "") for call in record["tool_calls"]}

            if record["state"] != "pending":
                if _decisions_match(stored_by_id, incoming):
                    return record
                raise DomainError(
                    "approval_conflict",
                    self._render("error.approval_state_conflict", state=record["state"]),
                    status=409,
                )

            seen: set[str] = set()
            new: list[dict[str, Any]] = []
            for item in incoming:
                tool_call_id = item["tool_call_id"]
                if tool_call_id in seen:
                    raise DomainError(
                        "validation_failed",
                        self._render("error.approval_decision_duplicate", tool_call_id=tool_call_id),
                    )
                seen.add(tool_call_id)
                if tool_call_id in stored_by_id:
                    existing = stored_by_id[tool_call_id]
                    if bool(existing["confirmed"]) == bool(item["confirmed"]):
                        continue  # 幂等重试：这一条已经按同样结论决策过
                    raise DomainError(
                        "approval_conflict",
                        self._render("error.approval_tool_call_decided", tool_call_id=tool_call_id),
                        status=409,
                    )
                if tool_call_id not in tool_ids:
                    raise DomainError(
                        "validation_failed",
                        self._render("error.approval_tool_call_unknown", tool_call_id=tool_call_id),
                    )
                new.append(item)

            if not new:
                return record

            all_decisions: list[dict[str, Any]] = [*record["decisions"], *new]
            decided_ids = {item["tool_call_id"] for item in all_decisions}
            remaining = [
                call for call in record["tool_calls"] if str(call.get("id") or "") not in decided_ids
            ]
            state = "pending"
            resolved_at = None
            if not remaining:
                state = "approved" if all(bool(item["confirmed"]) for item in all_decisions) else "denied"
                resolved_at = utc_now()
            self._db.update_approval_request(
                reply_id, decisions=all_decisions, state=state, resolved_at=resolved_at
            )
            # 决策里附带的新规则是**配置**：落在该 Agent 的 scope，供后续调用沿用。
            self._persist_rules(record["member_id"], new)

            session = self._sessions.get(reply_id)
            if session is None:
                session = _Session(deadline=self._monotonic() + self.timeout_seconds)
                self._sessions[reply_id] = session
            session.undelivered.extend(new)
            self._deliver(session)

            record = self._db.get_approval_request(reply_id)
            if record is None:
                raise RuntimeError("approval record disappeared during submit")
            if state == "pending":
                self._events.publish(
                    record["target_id"],
                    approval_required(
                        reply_id=reply_id,
                        member_id=record["member_id"],
                        tool_calls=remaining,
                    ),
                )
            else:
                self._events.publish(
                    record["target_id"],
                    approval_resolved(reply_id=reply_id, state=state, decisions=record["decisions"]),
                )
            return record

    def _persist_rules(self, member_id: str, decisions: Iterable[Mapping[str, Any]]) -> None:
        for decision in decisions:
            for raw in decision.get("rules") or []:
                if isinstance(raw, Mapping) and raw.get("tool_name"):
                    self._db.create_permission_rule(
                        scope_kind="agent",
                        scope_id=member_id,
                        tool_name=str(raw.get("tool_name") or ""),
                        pattern=str(raw.get("pattern") or ""),
                        behavior=str(raw.get("behavior") or ""),
                    )

    # ─── 超时 / 重启 ──────────────────────────────────────────────────────
    def _expire(self, reply_id: str) -> bool:
        """把仍 pending 的记录标 expired；返回是否真的改了状态。调用方持锁。"""
        record = self._db.get_approval_request(reply_id)
        if record is None or record["state"] != "pending":
            return False
        self._db.update_approval_request(reply_id, state="expired", resolved_at=utc_now())
        record = self._db.get_approval_request(reply_id)
        if record is not None:
            self._events.publish(
                record["target_id"],
                approval_resolved(reply_id=reply_id, state="expired", decisions=record["decisions"]),
            )
        return True

    def expire_pending_at_startup(self) -> list[str]:
        """进程启动时把上个进程残留的 pending 全部标 ``expired``（D19）。"""
        expired = self._db.expire_pending_approvals()
        if expired:
            _log.info("expired %d pending approval request(s) on startup", len(expired))
        return expired

    # ─── 唤醒 ─────────────────────────────────────────────────────────────
    def _deliver(self, session: _Session) -> None:
        """把尚未回送的决策交给等待中的 loop；没有等待者时留在队列里。"""
        if not session.undelivered or session.future is None or session.future.done():
            return
        loop = session.loop
        decisions = session.drain()
        future = session.future
        if loop is None or loop.is_closed():
            session.undelivered = [*decisions, *session.undelivered]
            return
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_set_future_result, future, decisions)


def _set_future_result(future: asyncio.Future[list[dict[str, Any]]], decisions: list[dict[str, Any]]) -> None:
    if not future.done():
        future.set_result(decisions)


def _tool_call_payload(call: Mapping[str, Any]) -> dict[str, Any]:
    """只保存工具调用与建议规则；调用方不传凭证，本函数也不添加任何字段。"""
    dump = getattr(call, "model_dump", None)
    raw = cast(dict[str, Any], dump(mode="json")) if callable(dump) else dict(call)
    payload: dict[str, Any] = {
        "id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or ""),
        "input": raw.get("input") or "",
    }
    suggestions = raw.get("suggested_rules") or []
    if suggestions:
        payload["suggested_rules"] = [_rule_payload(rule) for rule in suggestions]
    return payload


def _rule_payload(rule: Any) -> dict[str, str]:
    raw = rule.model_dump(mode="json") if hasattr(rule, "model_dump") else dict(rule)
    return {
        "tool_name": str(raw.get("tool_name") or ""),
        "pattern": str(raw.get("rule_content") or raw.get("pattern") or ""),
        "behavior": str(raw.get("behavior") or ""),
    }


def _merge_tool_calls(
    existing: Iterable[Mapping[str, Any]], incoming: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {str(call.get("id") or ""): dict(call) for call in existing}
    order = list(merged)
    for call in incoming:
        key = str(call.get("id") or "")
        if key not in merged:
            order.append(key)
        merged[key] = dict(call)
    return [merged[key] for key in order]


def _decision_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    rules: list[dict[str, str]] = []
    for raw in item.get("rules") or []:
        try:
            rules.append(normalize_rule(raw))
        except ValueError as error:
            raise DomainError("validation_failed", str(error)) from error
    return {
        "tool_call_id": str(item.get("tool_call_id") or ""),
        "confirmed": bool(item.get("confirmed", False)),
        "rules": rules,
    }


def _decisions_match(stored: Mapping[str, Mapping[str, Any]], incoming: Iterable[Mapping[str, Any]]) -> bool:
    for item in incoming:
        current = stored.get(item["tool_call_id"])
        if current is None or bool(current["confirmed"]) != bool(item["confirmed"]):
            return False
    return True
