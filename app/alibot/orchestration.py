"""唤醒编排：消息落库之后，把「待读的新消息」交给成员面的 Agent 跑一轮。

五条纪律（spec #1 §4 / §5）：

1. **触发点唯一**：只在消息**成功落库之后**调用 :meth:`WakeOrchestrator.notify`。落库失败
   根本不会走到这里，所以也就不会唤醒。
2. **唤醒信号只带标识**：``(member_id, target_id, from_seq, reason)``，不带正文。Agent 被唤醒后
   经**同一个读接口**（``Database.list_messages``）自行读取待读消息——「读消息」只有一条路径。
3. **唤醒集合 = 该频道的全部 agent 成员，排除作者自己** + **穿透**（被点名却未加入的 Agent）。
4. **一轮 ↔ 至多一条频道消息**：轮次结果里的回复条数由适配接口的 ``RoundOutcome`` 约束，落库路径
   只有 :meth:`WakeOrchestrator._commit_reply` 一条。
5. **消费游标只在有结论时推进**（``replied`` / ``ignored`` 推进；``failed`` 不推进），于是下一次
   唤醒会重新读到这条消息——但**不是自动重放**：in-flight 状态只在内存里，后端重启后没有
   任何可重放的记录，只等下一次唤醒。

预算策略（策略 B，实施票 ③）在本模块实现，且落库（``target_budget``）：

- **人类世代** = 该 target 最近一次**开世代动作**所在消息的 `seq`；人类消息落库即开启新世代并把预算重置为 3。
- **一次 agent→agent 跳** = 一条 Agent 消息**投递了至少一个**唤醒给其它 Agent。一条消息即使唤醒
  多个成员也只消耗 1 次——它就是一次跳。被**合并**掉的重复唤醒不额外计数、也不额外消耗预算
  （「跳数以投递的唤醒为准」）。
- **预算耗尽**：Agent 消息照常落库（append-only，不可撤回），但**一个唤醒都不投递**，为每个
  「本应被唤醒却被压制」的成员各记一条 `budget-exhausted` 轮次结果。人类消息随后落库即重置。
- **硬上界（可验收断言）**：任一人类世代内同一频道的 agent→agent 跳数 ≤ 3 —— 由「耗尽的世代里
  一个都不投递」构造性保证，而不是靠事后统计。
- **穿透不豁免预算**：未加入却被点名的 Agent 同样走这条跳的裁决。

**跳数的单一正本**是 :meth:`Database.budget_state` 据以读出的 ``target_budget.used``：它每次
agent→agent 跳恰好 +1，不因成员数变化。``round_outcome.consumed_budget`` 只是**行级**标记
（「这一轮由那次已计一次的跳送来」），一次跳投递 N 个成员就有 N 条为真的行，因此**不可按行求和**；
想拿跳数的人只能读 ``target_budget.used``。
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
    SetTaskStatusAction,
    TaskActionContext,
    TaskAgentOption,
    TransferTaskAction,
    is_remote_provider,
    model_name_of,
    provider_of,
    read_api_key,
)
from .db import BUDGET_LIMIT, Database, DomainError, utc_now
from .events import EventBus
from .i18n import join_list, locale_of, render
from .mentions import mentioned_member_ids, parse_mentions
from .service import WorkspaceService, remote_data_faces

# 状态点四态（spec #1 §3）：只表达后端事实，不从任何会话存活推导。
STATUS_ONLINE = "online"
STATUS_WORKING = "working"
STATUS_ERROR = "error"
STATUS_OFFLINE = "offline"

WAKE_REASON_MENTION = "mention"
WAKE_REASON_CHANNEL_MESSAGE = "channel-message"


class Scheduler(Protocol):
    """跑一轮的地方。生产用线程池，测试注入手动调度器以获得确定性观察窗口。"""

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> None: ...


class ThreadScheduler:
    """默认调度器：每轮跑在一个工作线程上。"""

    def __init__(self, max_workers: int = 4) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="alibot-round")

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


@dataclass
class Availability:
    """成员此刻能不能跑一轮，以及为什么不能（reason 必须可读）。"""

    ok: bool
    reason: str = ""


@dataclass
class _WakeTarget:
    """一个唤醒目标：成员行 + 它是否**只能靠穿透**送达（被点名但未加入该 target）。

    ``pierced`` 是判断「回复时要不要改成员关系」的依据。它是**显式字段**而不是塞进成员 dict 的
    隐式标记：那份 dict 会被原样交给适配接口（``AgentSeam.run_round``），往里藏协作域的内部标记
    会同时污染 seam 的输入契约与「成员行就是成员行」这个约定。
    """

    member: dict[str, Any]
    pierced: bool = False


@dataclass
class _Committed:
    """一次回复落库的结果。

    ``joined``/``system_seq`` 在既有的非 DM 穿透路径上为真：它记录成员变化并追加一条 ``system``
    事件消息；DM 上的穿透只送达、不改成员关系。
    顺序由人类裁定：system 先（seq n），回复随后（seq n+1）——避免「一个还没加入的人先发言」。
    """

    reply_seq: int
    finished_at: str
    joined: bool = False
    system_seq: int | None = None


@dataclass
class _DeferredWake:
    """另一 target 上的唤醒：等待该成员当前轮次收口后再按原 target 执行。"""

    agent: dict[str, Any]
    target_id: str
    from_seq: int
    reason: str
    consumed_budget: bool
    pierced: bool


@dataclass
class _Turn:
    """一个成员**正在进行中**的那一轮在编排侧的簿记。

    ``repeat_bases`` 是 in-flight 期间到达的重复唤醒的 ``base_seq`` 集合。它们**不排队、不重放**
    （不重复跑模型、不为每条各跑一轮），但也不能悄悄消失：轮次收口时合成**恰好一条** merged
    记录，取值以**最大 seq 为准**（票面原文）。另一 target 上的唤醒不属于重复唤醒，放进
    ``deferred_rounds`` 等当前轮次结束后按其各自作用域运行。
    """

    target_id: str
    repeat_bases: list[int]
    deferred_rounds: dict[str, _DeferredWake]


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
        # in-flight 只是运行态（进程内事实）：重启后没有可重放的轮次。
        # 「最近一轮失败」不在这里 —— 它读库（见 ``status_of``），因为它是一个持久事实。
        self._in_flight: dict[str, _Turn] = {}
        self._lock = threading.RLock()

    # ─── 文案的语言偏好（ADR-0009 第二层：读库，不读请求头） ────────────────
    def _locale(self) -> str:
        """当前**语言偏好**（库内的工作区设置）。

        编排跑在 HTTP 请求之外（后台线程 / 测试的手动调度器），**没有请求头可读**，因此这里的
        取值只能来自库——这正是 ADR-0009 把语言偏好落库的决定性理由。
        """
        return locale_of(self._db)

    # ─── 唤醒面 ───────────────────────────────────────────────────────────
    def notify(self, target_id: str, from_seq: int, reason: str = WAKE_REASON_CHANNEL_MESSAGE) -> int:
        """消息落库之后计算唤醒集合并投递轮次。返回**投递的成员数**。

        目录（顺序就是裁决顺序）：

        1. 作者被排除（人类与 Agent 作者都一样）。
        2. 面 = 该 target 全部 Agent 成员 + **穿透**（被点名却未加入该 target 的 Agent）。
        3. ``system`` 事件消息不是对话回合：它既不唤醒任何人，也不消耗预算。
        4. DM 中 Agent 作者的消息不再唤醒任何人；其它 target 上 Agent 作者且目标非空 ⇒
           这是**一次 agent→agent 跳**：先看预算。预算耗尽则一个都不投递，
           为每个被压制的成员记一条 ``budget-exhausted``；否则先扣 1 次预算再投递。

        这次跳只让 ``target_budget.used`` +1（跳数的单一正本）；下面传给每个成员的
        ``jump_delivered`` 是**行级**标记，投递 N 个成员就有 N 条为真的轮次结果行。
        """
        # 目标作用域早退：调用入口已按 kind 校验 channel/task；唤醒机制本身对 target 不分支。
        target = self._db.get_target(target_id)
        message = self._db.get_message(target_id, from_seq)
        author_id = message["author"]["id"]
        author_kind = message["author"]["kind"]

        # system 事件消息（穿透加入）只是成员变化的事实：不构成对话回合。
        if message["kind"] == "system":
            return 0

        # DM 成员关系固定为 owner + 单一 Agent：Agent 回复与其正文提及都不产生唤醒。
        if target["kind"] == "dm" and author_kind == "agent":
            return 0

        targets = self._wake_targets(target_id, author_id, message)
        if not targets:
            return 0

        # 这一次跳是否真的发生了（Agent 作者 + 目标非空 + 有余额 ⇒ 投递）。它作为**行级**标记
        # 下调给每个成员：`target_budget.used` 已经把跳数记了 1 次，行标记只为「这一轮是怎么来的」。
        # 名字故意不叫 consumed_budget：`round_outcome.consumed_budget` 是历史列名，读起来像
        # 「这一行扣了一次」，正是 #12 要消掉的歧义。
        jump_delivered = author_kind == "agent" and target["kind"] != "dm"
        if jump_delivered and not self._spend_jump(
            target_id, from_seq, [target.member for target in targets]
        ):
            return 0

        mentions = set(mentioned_member_ids(message["mention_snapshot"]))
        for wake_target in targets:
            member = wake_target.member
            member_reason = WAKE_REASON_MENTION if member["id"] in mentions else reason
            self._scheduler.submit(
                self.run_round,
                member,
                target_id,
                from_seq,
                member_reason,
                jump_delivered,
                wake_target.pierced,
            )
        return len(targets)

    def notify_task_assignment(self, target_id: str, from_seq: int, member_id: str) -> int:
        """只有显式指派命令可唤醒新指派 Agent；任务 system 消息本身仍不触发通用唤醒。"""
        return self._notify_task_assignee(target_id, from_seq, member_id, agent_jump=False)

    def notify_task_reassignment(self, target_id: str, from_seq: int, member_id: str) -> int:
        """Agent 转派按一次有界 agent→agent 跳处理；预算耗尽时保留记录但不唤醒。"""
        return self._notify_task_assignee(target_id, from_seq, member_id, agent_jump=True)

    def _notify_task_assignee(
        self, target_id: str, from_seq: int, member_id: str, *, agent_jump: bool
    ) -> int:
        """验证任务指派事件、按动作预算裁决后唤醒新受派 Agent。"""
        target = self._db.get_target(target_id)
        if target["kind"] != "task":
            return 0
        message = self._db.get_message(target_id, from_seq)
        if message["kind"] != "system":
            return 0
        assignee = next(
            (agent for agent in self._db.list_target_agent_members(target_id) if agent["id"] == member_id),
            None,
        )
        if assignee is None:
            return 0
        if agent_jump and not self._spend_jump(target_id, from_seq, [assignee]):
            return 0
        self._scheduler.submit(
            self.run_round,
            assignee,
            target_id,
            from_seq,
            WAKE_REASON_CHANNEL_MESSAGE,
            agent_jump,
            False,
        )
        return 1

    def _wake_targets(self, target_id: str, author_id: str, message: dict[str, Any]) -> list[_WakeTarget]:
        """唤醒面 = **该 target** 的 agent 成员（除作者）+ 被点名但**未加入**该 target 的 Agent（穿透）。

        成员面读的是 ``target_member``（同一个 `target_id` 键），**不按 kind 分支**（ADR-0004 决策 8）；
        channel 型 target 的 id 就是频道 id，所以这里的传参与改名前的值逐字相同。
        """
        targets = [
            _WakeTarget(member=member)
            for member in self._db.list_target_agent_members(target_id)
            if member["id"] != author_id
        ]
        seen = {target.member["id"] for target in targets}
        for member_id in mentioned_member_ids(message["mention_snapshot"]):
            if member_id == author_id or member_id in seen:
                continue
            agent = self._pierce_target(member_id)
            if agent is None:
                continue
            # 标记这次唤醒是穿透（未加入却被点名）；是否改成员关系由 target kind 决定。
            targets.append(_WakeTarget(member=agent, pierced=True))
            seen.add(member_id)
        return targets

    def _pierce_target(self, member_id: str) -> dict[str, Any] | None:
        """被点名的穿透候选：必须是**活着的 Agent 成员**（人类与已删除身份不是穿透对象）。"""
        try:
            agent = self._db.get_agent(member_id)
        except Exception:
            return None  # 人类被提及、或提及快照指向不存在的成员：不是穿透对象
        if agent.get("deleted_at"):
            return None
        return agent

    def _spend_jump(self, target_id: str, from_seq: int, targets: list[dict[str, Any]]) -> bool:
        """扣一次 agent→agent 跳的预算。返回是否投递（False = 预算耗尽，一个都不投）。

        这里是**跳数唯一正本**的**唯一写点**：一次调用让 ``target_budget.used`` 恰好 +1，
        与该跳投递了几个成员无关。

        「以投递的唤醒为准」有两个落点，都在这一个方法里：

        - 目标全部处于 in-flight ⇒ 只产生合并、零投递 ⇒ **不算一次跳**（票面原句），不消耗预算。
        - 目标非空且还有余额 ⇒ 先扣再投；扣款与投递决策在同一把锁内，因此并发到达的两条 Agent
          消息不可能同时读到「还剩 1」而各投一次（硬上界 ≤ 3 由此成立）。
        """
        with self._lock:
            if not any(
                member["id"] not in self._in_flight or self._in_flight[member["id"]].target_id != target_id
                for member in targets
            ):
                return True
            state = self._db.budget_state(target_id)
            if state["used"] >= BUDGET_LIMIT:
                self._record_budget_exhausted(target_id, from_seq, targets)
                return False
            self._db.consume_budget(target_id, state["epoch_seq"])
            return True

    def _record_budget_exhausted(self, target_id: str, from_seq: int, targets: list[dict[str, Any]]) -> None:
        """耗尽的世代里不投递任何唤醒，但每个**本应被唤醒**的成员都要留下可追溯的轮次结果。

        状态点**不**因此变 ``--error``（``status_of`` 不把 ``budget-exhausted`` 当失败）：预算耗尽
        既不是「它坏了」也不是「它不想说」，它是「这个人类世代已经用过 3 次往返了」这条后端事实。
        """
        epoch_seq = self._db.budget_state(target_id)["epoch_seq"]
        reason = render("reason.budget_exhausted", self._locale(), epoch=epoch_seq, limit=BUDGET_LIMIT)
        for member in targets:
            record = self._db.append_round_outcome(
                member_id=member["id"],
                target_id=target_id,
                base_seq=from_seq,
                status="budget-exhausted",
                reason=reason,
                consumed_budget=False,
                merged=False,
            )
            self._publish_outcome(target_id, from_seq, record)

    def _publish_outcome(self, target_id: str, seq: int, record: dict[str, Any]) -> None:
        """把一条轮次结果推给订阅者（轮次结果事件的唯一发布点）。"""
        self._events.publish(target_id, {"type": "round_outcome", "seq": seq, "outcome": record})

    # ─── 一轮 ─────────────────────────────────────────────────────────────
    def run_round(
        self,
        agent: dict[str, Any],
        target_id: str,
        from_seq: int,
        reason: str,
        consumed_budget: bool = False,
        pierced: bool = False,
    ) -> dict[str, Any] | None:
        """跑一轮。返回落下的轮次结果；瞬态轮次（无新消息、只被合并）返回 None。

        ``consumed_budget`` 只写进这一行的 ``round_outcome.consumed_budget`` 列：它是**行级**
        标记（「这一轮由哪次跳送来」），**不是**跳的计数 —— 一次跳投递 N 个成员会写 N 条为真的行，
        跳数只 +1。跳数的唯一正本是 ``target_budget.used``。
        """
        member_id = agent["id"]

        with self._lock:
            active_turn = self._in_flight.get(member_id)
            if active_turn is not None:
                if active_turn.target_id == target_id:
                    # 同 target 重复唤醒**不排队、不重放**，收口时合成一条 merged 记录。
                    active_turn.repeat_bases.append(int(from_seq))
                else:
                    # 跨 target 不是重复唤醒：不能并入错误作用域，也不能并发运行同一 Agent。
                    deferred = active_turn.deferred_rounds.get(target_id)
                    if deferred is None:
                        active_turn.deferred_rounds[target_id] = _DeferredWake(
                            agent=agent,
                            target_id=target_id,
                            from_seq=int(from_seq),
                            reason=reason,
                            consumed_budget=consumed_budget,
                            pierced=pierced,
                        )
                    else:
                        deferred.from_seq = max(deferred.from_seq, int(from_seq))
                        deferred.consumed_budget = deferred.consumed_budget or consumed_budget
                        deferred.pierced = deferred.pierced or pierced
                        if reason == WAKE_REASON_MENTION:
                            deferred.reason = reason
                return None
            self._in_flight[member_id] = _Turn(target_id=target_id, repeat_bases=[], deferred_rounds={})

        started_at = utc_now()
        base_seq = from_seq
        try:
            cursor = self._db.agent_cursor(target_id, member_id)
            pending = [
                message
                for message in self._db.list_messages(target_id, after_seq=cursor)
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

            self._emit_status(target_id, member_id, base_seq)
            self._events.publish(
                target_id,
                {"type": "typing", "member_id": member_id, "seq": base_seq, "active": True, "reason": reason},
            )

            availability = self.availability(agent)
            if not availability.ok:
                outcome = RoundOutcome(status="failed", reason=availability.reason)
            else:
                task_context = self._task_action_context(target_id)
                outcome = self._run_seam(
                    agent,
                    pending,
                    mentioned=mentioned,
                    target_id=target_id,
                    task_context=task_context,
                )
                # rubric：被个人提及是**确定信号**——必须回应。自判忽略在确定信号下按失败处理，
                # 不静默（不能把「它坏了」当成「它不想说」）。
                if mentioned and outcome.status == "ignored":
                    outcome = RoundOutcome(
                        status="failed",
                        reason=render("reason.mention_ignored", self._locale(), reason=outcome.reason),
                    )
                # 被点名却不产出正文同理：不是「不想说」，是没做到。
                elif (
                    mentioned
                    and outcome.status == "replied"
                    and not outcome.messages
                    and outcome.task_action is None
                ):
                    outcome = RoundOutcome(
                        status="failed",
                        reason=render("reason.mention_no_body", self._locale()),
                    )

            finished_at = utc_now()
            # 本轮的重复唤醒在**收口时**才落账（Q4=A）：窗口内它们只累积在内存里，
            # 因此这里才读。「以最大 seq 为准」 = 合并行的 base_seq 取其中的最大值。
            with self._lock:
                turn = self._in_flight.get(member_id)
                merged_bases = list(turn.repeat_bases) if turn is not None else []
            reply_seq: int | None = None
            if outcome.status == "replied" and outcome.messages:
                committed = self._commit_reply(agent, target_id, outcome.messages[0].body, pierced=pierced)
                reply_seq = committed.reply_seq
                finished_at = committed.finished_at
                if committed.joined:
                    outcome = RoundOutcome(
                        status="replied",
                        reason=render(
                            "reason.pierced_suffix",
                            self._locale(),
                            reason=outcome.reason,
                            seq=committed.system_seq,
                        ),
                        task_action=outcome.task_action,
                    )
                self._db.mark_read(target_id, base_seq, member_id=member_id)
            elif outcome.status == "ignored" or (
                outcome.status == "replied" and outcome.task_action is not None
            ):
                self._db.mark_read(target_id, base_seq, member_id=member_id)

            if outcome.status == "replied" and outcome.task_action is not None:
                rejection = self._apply_task_action(agent, target_id, outcome.task_action)
                if rejection:
                    outcome.reason = "；".join(part for part in (outcome.reason, rejection) if part)

            self._record_merged(target_id, member_id, merged_bases, started_at)
            record = self._db.append_round_outcome(
                member_id=member_id,
                target_id=target_id,
                base_seq=base_seq,
                status=outcome.status,
                reason=outcome.reason,
                started_at=started_at,
                finished_at=finished_at,
                consumed_budget=consumed_budget,
                merged=False,
                reply_seq=reply_seq,
            )
            self._publish_outcome(target_id, base_seq, record)
            self._events.publish(
                target_id,
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
                    target_id=target_id,
                    base_seq=base_seq,
                    status="failed",
                    reason=render(
                        "reason.round_exception",
                        self._locale(),
                        type=type(error).__name__,
                        error=error,
                    ),
                    started_at=started_at,
                    consumed_budget=consumed_budget,
                )
            return None
        finally:
            with self._lock:
                turn = self._in_flight.pop(member_id, None)
                deferred_rounds = list(turn.deferred_rounds.values()) if turn is not None else []
            for deferred in deferred_rounds:
                self._scheduler.submit(
                    self.run_round,
                    deferred.agent,
                    deferred.target_id,
                    deferred.from_seq,
                    deferred.reason,
                    deferred.consumed_budget,
                    deferred.pierced,
                )
            # 收口后的状态点必须在**清掉 in-flight 之后**发：否则最后一帧会停在 working，
            # 前端的状态点就会永久显示「正在回复」。这是后端事实，不是前端补偿。
            self._emit_status(target_id, member_id, base_seq)

    def _apply_task_action(
        self, agent: dict[str, Any], target_id: str, action: TransferTaskAction | SetTaskStatusAction
    ) -> str | None:
        """以当前轮次 Agent 的可信身份执行一个结构化任务动作；动作不得跨 target。"""
        try:
            task = self._db.get_task(action.task_id)
            if task["target_id"] != target_id:
                raise DomainError(
                    "validation_failed", render("error.task_action_wrong_target", self._locale())
                )
            if isinstance(action, SetTaskStatusAction) and not self._db.is_target_member(
                target_id, str(agent["id"])
            ):
                raise DomainError("validation_failed", render("error.task_action_not_member", self._locale()))
            service = WorkspaceService(
                self._db,
                member_id=str(agent["id"]),
                orchestrator=self,
                events=self._events,
            )
            if isinstance(action, TransferTaskAction):
                service.update_task_assignee(action.task_id, action.assignee_member_id)
            else:
                service.update_task_status(action.task_id, action.status)
        except DomainError as error:
            return render("error.task_action_rejected", self._locale(), message=error.message)
        return None

    def _record_merged(
        self,
        target_id: str,
        member_id: str,
        merged_bases: list[int],
        started_at: str,
    ) -> None:
        """把「这一轮期间到达的重复唤醒」合成**恰好一条** merged 记录（以最大 seq 为准）。

        它们不重放、不额外消耗预算（跳数以**投递的唤醒**为准），但必须留痕：这是
        「它当时为什么没有被单独问一次」的会计事实。consumed_budget 写在这一轮自己的轮次结果行上，
        这里写 False —— 合并本身没有产生投递（也不增加 ``target_budget.used``，那才是跳数正本）。
        """
        if not merged_bases:
            return
        largest = max(merged_bases)
        locale = self._locale()
        reasons = join_list(sorted({f"#{seq}" for seq in merged_bases}), locale)
        record = self._db.append_round_outcome(
            member_id=member_id,
            target_id=target_id,
            base_seq=largest,
            status="merged",
            reason=render("reason.merged", locale, count=len(merged_bases), bases=reasons, largest=largest),
            started_at=started_at,
            consumed_budget=False,
            merged=True,
        )
        self._publish_outcome(target_id, largest, record)

    # ─── 适配接口调用 ─────────────────────────────────────────────────────
    def _task_action_context(self, target_id: str) -> TaskActionContext | None:
        task = self._db.task_for_target(target_id)
        if task is None:
            return None
        agents = tuple(
            TaskAgentOption(member_id=str(agent["id"]), display_name=str(agent["display_name"]))
            for agent in self._db.agent_members()
        )
        return TaskActionContext(
            task_id=str(task["id"]),
            assignee_member_id=task["assignee_member_id"],
            agent_members=agents,
        )

    def _run_seam(
        self,
        agent: dict[str, Any],
        pending: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
    ) -> RoundOutcome:
        try:
            return self._seam.run_round(
                agent,
                pending,
                mentioned=mentioned,
                target_id=target_id,
                task_context=task_context,
            )
        except Exception as error:  # 适配层的任何异常都收口成该轮 failed，不影响别的轮次
            return RoundOutcome(
                status="failed",
                reason=render(
                    "reason.seam_exception",
                    self._locale(),
                    type=type(error).__name__,
                    error=error,
                ),
            )

    def _commit_reply(
        self,
        agent: dict[str, Any],
        target_id: str,
        body: str,
        pierced: bool = False,
    ) -> _Committed:
        """把 Agent 回复 append 到 target 消息流；DM 穿透只送达，其它 target 沿用既有成员变化记录。

        非 DM target 上的穿透先写成员行与 ``system`` 事件消息，再写回复；DM 上绝不写成员行或加入事件。
        system 消息本身不唤醒任何人、不消耗预算。
        """
        members = self._db.list_members()
        target = self._db.get_target(target_id)
        system_seq: int | None = None
        if target["kind"] != "dm" and pierced and not self._db.is_target_member(target_id, agent["id"]):
            self._db.add_target_member(target_id, agent["id"])
            system_message = self._db.append_message(
                target_id=target_id,
                body=render("system.member.pierced_join", self._locale(), name=agent["display_name"]),
                author_member_id=agent["id"],
                kind="system",
                mention_snapshot=[],
            )
            system_seq = int(system_message["seq"])
            self._events.publish(
                target_id,
                {"type": "message", "seq": system_seq, "message": system_message},
            )
        message = self._db.append_message(
            target_id=target_id,
            body=body,
            author_member_id=agent["id"],
            kind="agent",
            mention_snapshot=parse_mentions(body, members),
        )
        self._events.publish(target_id, {"type": "message", "seq": message["seq"], "message": message})
        # 回复落库后照常唤醒（作者自己会被排除）。单 Agent 场景下唤醒集为空，即一次「零唤醒」。
        self.notify(target_id, message["seq"], reason=WAKE_REASON_CHANNEL_MESSAGE)
        return _Committed(
            reply_seq=int(message["seq"]),
            finished_at=str(message["created_at"]),
            joined=system_seq is not None,
            system_seq=system_seq,
        )

    # ─── 可用性（「未启用」判定的唯一落点） ────────────────────────────────
    def availability(self, agent: dict[str, Any]) -> Availability:
        locale = self._locale()
        model_id = str(agent.get("model_id") or "").strip()
        if not model_id:
            return Availability(False, render("availability.no_model", locale))
        if not agent.get("enabled", True):
            return Availability(False, render("availability.disabled", locale))
        provider = provider_of(model_id)
        if not provider:
            return Availability(
                False, render("availability.model_provider_prefix", locale, model_id=model_id)
            )
        if not model_name_of(model_id):
            return Availability(False, render("availability.model_name_missing", locale, model_id=model_id))
        if is_remote_provider(provider) and not self._db.has_remote_consent(provider):
            return Availability(
                False,
                render(
                    "availability.remote_consent",
                    locale,
                    provider=provider,
                    faces=join_list(remote_data_faces(locale), locale),
                ),
            )
        try:
            read_api_key(provider, locale=locale)
        except CredentialUnavailable as error:
            return Availability(False, str(error))
        except Exception as error:  # 未知 provider 等：同样给可读原因而不是静默
            return Availability(
                False,
                render(
                    "availability.credential_unreadable",
                    locale,
                    provider=provider,
                    type=type(error).__name__,
                    error=error,
                ),
            )
        return Availability(True)

    # ─── 状态点（只表达后端事实） ─────────────────────────────────────────
    def status_of(self, agent: dict[str, Any]) -> str:
        """四态优先级：未启用 → 正在轮次中 → 出错 → 在线。

        「出错」读的是**库里最近一条轮次结果**，不是内存里的一次失败：
        「最近一轮失败」是一个持久事实，后端重启后仍然是它。
        「正在轮次中」确实只是内存事实（in-flight 是进程内的运行态），但重启后就没有在跑的
        轮次了，所以这个取值不会撒谎。
        """
        if not self.availability(agent).ok:
            return STATUS_OFFLINE
        with self._lock:
            if agent["id"] in self._in_flight:
                return STATUS_WORKING
        if self._db.latest_round_status(agent["id"]) == "failed":
            return STATUS_ERROR
        return STATUS_ONLINE

    def member_statuses(self, agent_ids: list[str] | None = None) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for agent in self._db.agent_members():
            if agent_ids is not None and agent["id"] not in agent_ids:
                continue
            statuses[agent["id"]] = self.status_of(agent)
        return statuses

    def _emit_status(self, target_id: str, member_id: str, seq: int) -> None:
        try:
            agent = self._db.get_agent(member_id)
        except Exception:
            return
        self._events.publish(
            target_id,
            {"type": "member_status", "member_id": member_id, "seq": seq, "status": self.status_of(agent)},
        )
