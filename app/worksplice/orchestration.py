"""唤醒编排：消息落库之后，把「待读的新消息」交给成员面的 Agent 跑一轮。

五条纪律（spec #1 §4 / §5）：

1. **触发点唯一**：只在消息**成功落库之后**调用 :meth:`WakeOrchestrator.notify`。落库失败
   根本不会走到这里，所以也就不会唤醒。
2. **唤醒信号只带标识**：``(member_id, target_id, from_seq, reason)``，不带正文。Agent 被唤醒后
   经**同一个读接口**（``Database.list_messages``）自行读取待读消息——「读消息」只有一条路径。
3. **唤醒集合 = 该频道的全部 agent 成员，排除作者自己**。穿透（未加入却被点名）在 #③。
4. **一轮 ↔ 至多一条频道消息**：结论里的回复条数由适配接口的 ``RoundOutcome`` 约束，落库路径
   只有 :meth:`WakeOrchestrator._commit_reply` 一条。
5. **消费游标只在有结论时推进**（``replied`` / ``ignored`` 推进；``failed`` 不推进），于是下一次
   唤醒会重新读到这条消息——但**不是自动重放**：in-flight 状态只在内存里，后端重启后没有
   任何可重放的记录，只等下一次唤醒。

预算策略（#③）在本模块**不实现**：唤醒永不因预算被压制。本票只落「是否消耗预算」与
「是否被合并」两个字段——前者在该轮确实构成一次 agent→agent 跳时为真，后者在 in-flight
期间收到重复唤醒时为真。
"""

from __future__ import annotations

import threading
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from .agent_seam import (
    AgentSeam,
    CredentialUnavailable,
    RoundOutcome,
    is_remote_provider,
    model_name_of,
    provider_of,
    read_api_key,
)
from .db import OWNER_ID, Database, utc_now
from .events import EventBus
from .mentions import mentioned_member_ids, parse_mentions

# 状态点四态（spec #1 §3）：只表达后端事实，不从任何会话存活推导。
STATUS_ONLINE = "online"
STATUS_WORKING = "working"
STATUS_ERROR = "error"
STATUS_OFFLINE = "offline"

STATUS_HEALTHY = "healthy"
STATUS_UNAVAILABLE = "unavailable"

WAKE_REASON_MENTION = "mention"
WAKE_REASON_CHANNEL_MESSAGE = "channel-message"


class Scheduler(Protocol):
    """跑一轮的地方。生产用线程池，测试注入手动调度器以获得确定性观察窗口。"""

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> None: ...


class ThreadScheduler:
    """默认调度器：每轮跑在一个工作线程上。"""

    def __init__(self, max_workers: int = 4) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="worksplice-round")

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


@dataclass
class Availability:
    """成员此刻能不能跑一轮，以及为什么不能（reason 必须可读）。"""

    ok: bool
    reason: str = ""


class WakeOrchestrator:
    """成员面的唯一入口：唤醒 → 读消息 → 自判 → 落库 → 记轮次结果。"""

    def __init__(
        self,
        db: Database,
        seam: AgentSeam,
        events: EventBus,
        scheduler: Scheduler | None = None,
    ) -> None:
        self._db = db
        self._seam = seam
        self._events = events
        self._scheduler: Scheduler = scheduler if scheduler is not None else ThreadScheduler()
        # in-flight 与「最近一轮失败」都只在内存里：重启后不重放、也不残留错误的错误态。
        self._in_flight: set[str] = set()
        self._last_failed: set[str] = set()
        self._lock = threading.RLock()

    # ─── 唤醒面 ───────────────────────────────────────────────────────────
    def notify(self, channel_id: str, from_seq: int, reason: str = WAKE_REASON_CHANNEL_MESSAGE) -> int:
        """消息落库之后计算唤醒集合并投递轮次。返回投递的成员数。"""
        self._db.get_channel(channel_id)  # 404 早退（频道不存在就不唤醒）
        message = self._db.get_message(channel_id, from_seq)
        author_id = message["author"]["id"]
        author_kind = message["author"]["kind"]

        members = self._db.list_channel_agent_members(channel_id)
        targets = [member for member in members if member["id"] != author_id]
        # 「一次 agent→agent 跳」= 一条 **agent** 消息导致至少一个其它 agent 被唤醒。
        consumed_budget = author_kind == "agent" and bool(targets)

        mentions = set(mentioned_member_ids(message["mention_snapshot"]))
        for member in targets:
            member_reason = WAKE_REASON_MENTION if member["id"] in mentions else reason
            self._scheduler.submit(
                self.run_round,
                member,
                channel_id,
                from_seq,
                member_reason,
                consumed_budget,
            )
        return len(targets)

    # ─── 一轮 ─────────────────────────────────────────────────────────────
    def run_round(
        self,
        agent: dict[str, Any],
        channel_id: str,
        from_seq: int,
        reason: str,
        consumed_budget: bool = False,
    ) -> dict[str, Any] | None:
        """跑一轮。返回落下的轮次结果；瞬态轮次（无新消息）返回 None。"""
        member_id = agent["id"]

        with self._lock:
            if member_id in self._in_flight:
                # 同一成员最多一轮 in-flight：重复唤醒**不静默消失**，落一条 merged。
                # 完整合并语义（以最大 seq 为准等）留在 #③。
                return self._db.append_round_outcome(
                    member_id=member_id,
                    target_id=channel_id,
                    base_seq=from_seq,
                    status="merged",
                    reason="该成员已有一轮在进行中，这次唤醒合并进那一轮（不重复调用模型，也不排队重放）",
                    consumed_budget=consumed_budget,
                    merged=True,
                )
            self._in_flight.add(member_id)

        started_at = utc_now()
        base_seq = from_seq
        try:
            cursor = self._db.agent_cursor(channel_id, member_id)
            pending = [
                message
                for message in self._db.list_messages(channel_id, after_seq=cursor)
                if message["author"]["id"] != member_id
            ]
            if not pending:
                # 瞬态轮次（无新消息）：按规格不落盘。
                return None

            mentions = {
                mention
                for message in pending
                for mention in mentioned_member_ids(message["mention_snapshot"])
            }
            mentioned = member_id in mentions
            base_seq = max(message["seq"] for message in pending)

            self._emit_status(channel_id, member_id, base_seq)
            self._events.publish(
                channel_id,
                {"type": "typing", "member_id": member_id, "seq": base_seq, "active": True, "reason": reason},
            )

            availability = self.availability(agent)
            if not availability.ok:
                outcome = RoundOutcome(status="failed", reason=availability.reason)
            else:
                outcome = self._run_seam(agent, pending, mentioned=mentioned, channel_id=channel_id)
                # rubric：被个人提及是**确定信号**——必须回应。自判忽略在确定信号下按失败处理，
                # 不静默（不能把「它坏了」当成「它不想说」）。
                if mentioned and outcome.status == "ignored":
                    outcome = RoundOutcome(
                        status="failed",
                        reason=(
                            "被个人提及是确定信号，必须回应；本轮自判忽略，按失败处理"
                            f"（原因为：{outcome.reason}）"
                        ),
                    )
                # 被点名却不产出正文同理：不是「不想说」，是没做到。
                elif mentioned and outcome.status == "replied" and not outcome.messages:
                    outcome = RoundOutcome(
                        status="failed",
                        reason="被个人提及是确定信号，但本轮没有产出可落库的正文，按失败处理",
                    )

            finished_at = utc_now()
            reply_seq: int | None = None
            if outcome.status == "replied" and outcome.messages:
                reply_seq = self._commit_reply(agent, channel_id, outcome.messages[0].body, base_seq)
                self._db.mark_read(channel_id, base_seq, member_id=member_id)
            elif outcome.status == "ignored":
                self._db.mark_read(channel_id, base_seq, member_id=member_id)

            with self._lock:
                if outcome.status == "failed":
                    self._last_failed.add(member_id)
                else:
                    self._last_failed.discard(member_id)

            record = self._db.append_round_outcome(
                member_id=member_id,
                target_id=channel_id,
                base_seq=base_seq,
                status=outcome.status,
                reason=outcome.reason,
                started_at=started_at,
                finished_at=finished_at,
                consumed_budget=consumed_budget,
                merged=False,
                reply_seq=reply_seq,
            )
            self._events.publish(
                channel_id,
                {
                    "type": "round_outcome",
                    "seq": base_seq,
                    "outcome": record,
                },
            )
            self._events.publish(
                channel_id,
                {
                    "type": "typing",
                    "member_id": member_id,
                    "seq": base_seq,
                    "active": False,
                    "reason": reason,
                },
            )
            return record
        except Exception as error:
            # 轮次内部的任何异常都不能逃逸到工作线程：一个成员的偶发问题不影响别的轮次，
            # 也不该在进程收尾（库已关闭）时打穿线程边界。尽力记一条 failed，记不下就止于静默失败。
            with suppress(Exception):
                self._db.append_round_outcome(
                    member_id=member_id,
                    target_id=channel_id,
                    base_seq=base_seq,
                    status="failed",
                    reason=f"轮次内部异常：{type(error).__name__}: {error}",
                    started_at=started_at,
                    consumed_budget=consumed_budget,
                )
            return None
        finally:
            with self._lock:
                self._in_flight.discard(member_id)
            # 收口后的状态点必须在**清掉 in-flight 之后**发：否则最后一帧会停在 working，
            # 前端的状态点就会永久显示「正在回复」。这是后端事实，不是前端补偿。
            self._emit_status(channel_id, member_id, base_seq)

    # ─── 适配接口调用 ─────────────────────────────────────────────────────
    def _run_seam(
        self,
        agent: dict[str, Any],
        pending: list[dict[str, Any]],
        *,
        mentioned: bool,
        channel_id: str,
    ) -> RoundOutcome:
        try:
            return self._seam.run_round(agent, pending, mentioned=mentioned, target_id=channel_id)
        except Exception as error:  # 适配层的任何异常都收口成该轮 failed，不影响别的轮次
            return RoundOutcome(status="failed", reason=f"Agent 适配层异常：{type(error).__name__}: {error}")

    def _commit_reply(self, agent: dict[str, Any], channel_id: str, body: str, base_seq: int) -> int:
        """把回复落库——本条票唯一的一条「Agent 消息进频道」的路径。"""
        members = self._db.list_members()
        message = self._db.append_message(
            channel_id=channel_id,
            body=body,
            author_member_id=agent["id"],
            kind="agent",
            mention_snapshot=parse_mentions(body, members),
        )
        self._events.publish(channel_id, {"type": "message", "seq": message["seq"], "message": message})
        # 回复落库后照常唤醒（作者自己会被排除）。单 Agent 场景下唤醒集为空，即一次「零唤醒」。
        self.notify(channel_id, message["seq"], reason=WAKE_REASON_CHANNEL_MESSAGE)
        _ = base_seq  # 保留参数以标明落库与那一轮 base_seq 的对应关系（取证用）
        return int(message["seq"])

    # ─── 可用性（「未启用」判定的唯一落点） ────────────────────────────────
    def availability(self, agent: dict[str, Any]) -> Availability:
        model_id = str(agent.get("model_id") or "").strip()
        if not model_id:
            return Availability(False, "未配置模型：请先在 Agent 配置里填写模型标识（provider:model）")
        if not agent.get("enabled", True):
            return Availability(False, "该 Agent 的模型配置已停用")
        provider = provider_of(model_id)
        if not provider:
            return Availability(
                False, f"模型标识缺少提供商前缀：{model_id}（应为 provider:model，例如 dashscope:qwen-max）"
            )
        if not model_name_of(model_id):
            return Availability(False, f"模型标识缺少模型名：{model_id}（应为 provider:model）")
        if is_remote_provider(provider) and not self._db.has_remote_consent(provider):
            return Availability(
                False,
                f"尚未确认远端数据流告知：{provider} 会把消息正文、发送者显示名、频道名称与描述、"
                f"被点名成员显示名发送到本机之外。确认后该 Agent 才会启用",
            )
        try:
            read_api_key(provider)
        except CredentialUnavailable as error:
            return Availability(False, str(error))
        except Exception as error:  # 未知 provider 等：同样给可读原因而不是静默
            return Availability(False, f"无法解析 {provider} 的凭证：{type(error).__name__}: {error}")
        return Availability(True)

    # ─── 状态点（只表达后端事实） ─────────────────────────────────────────
    def status_of(self, agent: dict[str, Any]) -> str:
        """四态优先级：未启用 → 正在回复 → 出错 → 空闲。"""
        if not self.availability(agent).ok:
            return STATUS_OFFLINE
        with self._lock:
            if agent["id"] in self._in_flight:
                return STATUS_WORKING
            if agent["id"] in self._last_failed:
                return STATUS_ERROR
        return STATUS_ONLINE

    def member_statuses(self, agent_ids: list[str] | None = None) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for agent in self._db.agent_members():
            if agent_ids is not None and agent["id"] not in agent_ids:
                continue
            statuses[agent["id"]] = self.status_of(agent)
        return statuses

    def _emit_status(self, channel_id: str, member_id: str, seq: int) -> None:
        try:
            agent = self._db.get_agent(member_id)
        except Exception:
            return
        self._events.publish(
            channel_id,
            {"type": "member_status", "member_id": member_id, "seq": seq, "status": self.status_of(agent)},
        )

    # ─── 未读口径的辅助：agent 消费游标 ───────────────────────────────────
    def owner_visibility(self) -> dict[str, Any]:
        """给只读快照用的一小组事实（当前没有额外语义，保留给下游扩展）。"""
        return {"owner_id": OWNER_ID}


def owner_unread_guard() -> None:
    """占位：owner 的未读徽标与 Agent 的消费游标互不干扰（前者读 ``channel_read`` 的 owner 行）。"""
