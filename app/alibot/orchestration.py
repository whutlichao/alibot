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

- **人类世代** = 该 target 最近一次**开世代动作**所在消息的 `seq`；人类消息落库即开启新世代并把预算
  重置为满额，并把该世代**生效上界**拍成快照（``target_budget.applied_limit``，#53）。
- **一次 agent→agent 跳** = 一条 Agent 消息**投递了至少一个**唤醒给其它 Agent。一条消息即使唤醒
  多个成员也只消耗 1 次——它就是一次跳。被**合并**掉的重复唤醒不额外计数、也不额外消耗预算
  （「跳数以投递的唤醒为准」）。
- **预算耗尽**：Agent 消息照常落库（append-only，不可撤回），但**一个唤醒都不投递**，为每个
  「本应被唤醒却被压制」的成员各记一条 `budget-exhausted` 轮次结果。人类消息随后落库即重置。
- **硬上界（可验收断言）**：任一人类世代内同一 target 的 agent→agent 跳数 ≤ 该世代**生效上界**
  （频道覆盖或内置默认 3，见 :meth:`Database.channel_budget_view`）—— 由「耗尽的世代里一个都不
  投递」构造性保证，而不是靠事后统计。
- **穿透不豁免预算**：未加入却被点名的 Agent 同样走这条跳的裁决。

**跳数的单一正本**是 :meth:`Database.budget_state` 据以读出的 ``target_budget.used``：它每次
agent→agent 跳恰好 +1，不因成员数变化。``round_outcome.consumed_budget`` 只是**行级**标记
（「这一轮由那次已计一次的跳送来」），一次跳投递 N 个成员就有 N 条为真的行，因此**不可按行求和**；
想拿跳数的人只能读 ``target_budget.used``。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from .agent_seam import (
    AgentSeam,
    ContinueTaskAction,
    CredentialUnavailable,
    ReplySignal,
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
from .db import OWNER_ID, TASK_WORK_ROUNDS_PER_EPOCH, Database, DomainError, utc_now
from .events import EventBus, agent_event
from .i18n import Locale, join_list, locale_of, render
from .mentions import mentioned_member_ids, parse_mentions
from .plan import PlanSession
from .runtime import context_window_messages
from .service import WorkspaceService, remote_data_faces
from .team import TeamRoundContext

# 状态点四态（spec #1 §3）：只表达后端事实，不从任何会话存活推导。
STATUS_ONLINE = "online"
STATUS_WORKING = "working"
STATUS_ERROR = "error"
STATUS_OFFLINE = "offline"

WAKE_REASON_MENTION = "mention"
WAKE_REASON_CHANNEL_MESSAGE = "channel-message"
# 认领那条投递的 reason（ADR-0016 五）：**不落库**，只进进程内轮次参数与 typing 事件；
# 「本轮由认领唤醒」以 ``reason.claim_round`` 后缀追加进 ``round_outcome.reason`` 文本。
# 它与 ``mention`` 同级（都是**确定信号**），因此 `notify` 里 ``mention`` 可以覆盖它——
# 覆盖**不削弱**判据：两者都要求必须回应（见 `CONTEXT.md` **回应判断**）。
WAKE_REASON_CLAIM = "claim"
# 自动续跑那条投递的 reason（ADR-0027）：与认领同一形状——**不落库**，只进进程内轮次参数与
# typing 事件；「本轮由自动续跑唤醒」以 ``reason.continue_round`` 后缀追加进 reason 文本。
WAKE_REASON_CONTINUE = "continue"
# team 成员没有自己的唤醒轮次：它由队长的 TeamAssign 分派运行。typing 的 reason 只是可读标识
# （规格 §4.5），不与预算/唤醒判定耦合。
TYPING_REASON_TEAM_ASSIGNMENT = "team-assignment"


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
    # 本轮的回应纪律来源（不落库）。跳 target 的 deferred 轮次必须**继承**它，合并时**取或**：
    # 任一方强制 ⇒ 合并后强制（同一 Agent 的重复唤醒会被合并，判据不能随谁先到而漂）；
    # 两方都强制时来源按 `ReplySignal.merged_with` 的固定优先级取，诊断也不随到达顺序变。
    signal: ReplySignal = ReplySignal.NONE
    # 本轮是否把**整个 target 历史**作为输入（自动续跑轮，ADR-0027）；合并时同样取或。
    history: bool = False


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
    reply_ids: set[str] = field(default_factory=set)
    interrupt: Callable[[str, str], bool] | None = None


class WakeOrchestrator:
    """成员面的唯一入口：唤醒 → 读消息 → 自判 → 落库 → 记轮次结果。"""

    def interrupt_round(self, target_id: str, reply_id: str) -> bool:
        self._db.get_target(target_id)
        with self._lock:
            for turn in self._in_flight.values():
                if turn.target_id == target_id and reply_id in turn.reply_ids and turn.interrupt is not None:
                    return turn.interrupt(target_id, reply_id)
        return False

    def __init__(
        self,
        db: Database,
        seam: AgentSeam,
        events: EventBus,
        scheduler: Scheduler | None = None,
        workspace_unavailable: Callable[[str], str | None] | None = None,
    ) -> None:
        self._db = db
        self._seam = seam
        self._events = events
        self._scheduler: Scheduler = scheduler if scheduler is not None else ThreadScheduler()
        # Docker 工作区失败的可读原因（#150）：装配层记录、这里只读。不注入时行为不变。
        self._workspace_unavailable = workspace_unavailable
        # in-flight 只是运行态（进程内事实）：重启后没有可重放的轮次。
        # 「最近一轮失败」不在这里 —— 它读库（见 ``status_of``），因为它是一个持久事实。
        self._in_flight: dict[str, _Turn] = {}
        self._lock = threading.RLock()

    # ─── 文案的语言偏好（ADR-0009 第二层：读库，不读请求头） ────────────────
    @property
    def scheduler(self) -> Scheduler:
        """本轮/本进程共用的调度器（SOP 运行复用它，不新开第二个线程池）。"""
        return self._scheduler

    def _locale(self) -> Locale:
        """当前**语言偏好**（库内的工作区设置）。

        编排跑在 HTTP 请求之外（后台线程 / 测试的手动调度器），**没有请求头可读**，因此这里的
        取值只能来自库——这正是 ADR-0009 把语言偏好落库的决定性理由。
        """
        return locale_of(self._db)

    def _dispatch(
        self,
        member: dict[str, Any],
        target_id: str,
        from_seq: int,
        reason: str,
        *,
        consumed_budget: bool,
        pierced: bool,
        signal: ReplySignal,
        history: bool = False,
    ) -> None:
        """投递一轮的**唯一落点**（七个唤醒入口共用）。

        这些参数原先在每处 `submit(self.run_round, ...)` 里各写一遍位置参数——#189 落地时就真的漏传过
        一处必答标记（队长轮仍拿到旧的布尔，纪律静默失效）。收成一处之后，「多一个纪律维度」只需要在
        这里改一次；标志位一律关键字传参，位置错位不再可能。

        ``history`` 只被自动续跑轮置真（ADR-0027）：把整个 target 历史交给模型，它才接得上自己上一轮
        的产出。其它入口逐字保持既有口径（只喂新消息）。
        """
        self._scheduler.submit(
            self.run_round,
            member,
            target_id,
            from_seq,
            reason,
            consumed_budget,
            pierced,
            signal,
            history,
        )

    # ─── 唤醒面 ───────────────────────────────────────────────────────────
    # 纪律（`signal`）在这里定下、随轮次一路带下去；**已启动**的那一轮不会因后到的人类消息改判
    # （同 target 的重复唤醒按 Q4=A 合并，见 run_round 与验收文档 §10）。
    def notify(self, target_id: str, from_seq: int, reason: str = WAKE_REASON_CHANNEL_MESSAGE) -> int:
        """消息落库之后计算唤醒集合并投递轮次。返回**投递的成员数**。

        目录（顺序就是裁决顺序）：

        1. 作者被排除（人类与 Agent 作者都一样）。
        2. 面 = 该 target 全部 Agent 成员 + **穿透**（被点名却未加入该 target 的 Agent）。
        3. ``system`` 事件消息不是对话回合：它既不唤醒任何人，也不消耗预算。
        4. DM 中 Agent 作者的消息不再唤醒任何人；其它 target 上 Agent 作者且目标非空 ⇒
           这是**一次 agent→agent 跳**：先看预算。预算耗尽则一个都不投递，
           为每个被压制的成员记一条 ``budget-exhausted``；否则先扣 1 次预算再投递。

        本轮投给每个成员的**纪律来源**由消息 kind 定（#189）：``message.kind == "human"`` ⇒
        ``HUMAN_MESSAGE``（DM / autonomous channel / task target 四个面同一口径），被点名时升为
        ``MENTION``；Agent 作者的消息 ⇒ ``NONE``（仍自判，纪律不变）。
        判据读**消息 kind** 而不是作者成员 kind：owner 的成员 kind 是 ``owner``（不是 ``human``），
        按作者 kind 判会把人类发言误认成非人类。它不改唤醒面、不扣跳、不改未读口径。

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

        # team 模式（D1 / D23=B）：该频道 target 与它全部 task target 只唤醒队长。
        team = self._team_config(target_id, target)
        if team is not None:
            return self._notify_team(target_id, from_seq, message, team, reason)

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
        # 人类作者的每一句话都是**确定信号**（#189）：DM / autonomous channel / task 同一个口径。
        # 读的是**消息 kind**（owner 的成员 kind 是 `owner`，不是 `human`）。
        base_signal = ReplySignal.HUMAN_MESSAGE if message["kind"] == "human" else ReplySignal.NONE
        for wake_target in targets:
            member = wake_target.member
            # `mention` 可以覆盖 `claim` / `human-message`（它们同级、都是**确定信号**），
            # 覆盖**不削弱**判据：「必须回应」由枚举承载，不读 reason（ADR-0016 五）。
            member_signal = ReplySignal.MENTION if member["id"] in mentions else base_signal
            member_reason = WAKE_REASON_MENTION if member["id"] in mentions else reason
            self._dispatch(
                member,
                target_id,
                from_seq,
                member_reason,
                consumed_budget=jump_delivered,
                pierced=wake_target.pierced,
                signal=member_signal,
            )
        return len(targets)

    def notify_task_assignment(
        self,
        target_id: str,
        from_seq: int,
        member_id: str,
        *,
        reason: str = WAKE_REASON_CHANNEL_MESSAGE,
        signal: ReplySignal = ReplySignal.NONE,
        actor_member_id: str | None = None,
    ) -> int:
        """只有显式指派命令可唤醒新指派 Agent；任务 system 消息本身仍不触发通用唤醒。

        ``reason`` 缺省是普通消息唤醒——owner 手动改派走这个缺省值，因此那一轮维持既有纪律；
        **认领**由编排传入 ``signal=ReplySignal.CLAIM``（与 ``reason`` 分开：reason 只是 typing /
        诊断的标识，纪律与文案来源由 signal 回答）。

        ``actor_member_id`` 是改派的发起者（owner 或 Agent）。team 模式只唤醒队长，并且
        **actor 就是队长时零唤醒**（D23=B）；system 记录的作者恒为 owner，因此不能从消息反推。
        """
        return self._notify_task_assignee(
            target_id,
            from_seq,
            member_id,
            agent_jump=False,
            reason=reason,
            signal=signal,
            actor_member_id=actor_member_id,
        )

    def notify_task_created(self, task_id: str, channel_id: str) -> int:
        """新建任务后的唤醒裁决（ADR-0025）；返回到**投递的轮次数**（0 / 1）。

        两条分支合在一个入口上，是因为扳机只有这一个（``WorkspaceService.create_task``）：

        - **team 频道**：只向该频道队长投递**一轮**且是确定信号（``TEAM_TASK_CHANGE``，#179 A）。仍**不认领**、
          不改任务行、不唤醒成员、不扣跳（人类动作不是 agent→agent 跳）；轮次里队长能看到
          「任务已创建：<标题>」——``from_seq`` 取那条 ``system`` 消息的 ``seq``（ADR-0025 §二）。
          队长不可用（D18）⇒ 零唤醒 + 一条 ``invalid-leader``。
        - **其它频道（autonomous / DM）**：逐字转调既有的 :meth:`claim_new_task`。不把认领逻辑复制成两份，
          也不让 ``claim_new_task`` 的名字承担「唤醒但不认领」这个新语义。
        """
        task = self._db.get_task(task_id)
        target_id = str(task["target_id"])
        # task target 没有自己的 mode 列：经 `task.channel_id -> channel.mode` 解析（ADR-0022 §一）。
        team = self._team_config(target_id)
        if team is None:
            return self.claim_new_task(task_id, channel_id)
        return self._notify_team_task_created(task_id, team)

    def claim_new_task(self, task_id: str, channel_id: str) -> int:
        """新建任务后由**编排**裁决认领（`CONTEXT.md` **认领** / ADR-0010）；返回是否认领（0 / 1）。

        认领是**配置层的确定性规则，不是模型的判断**：Agent 不会「申请」认领，`auto_claim` 开关决定
        编排要不要替它接手。候选 = 该**频道**里**可用**（``availability``：模型配置、启用态、凭证、
        远端告知）且 ``auto_claim`` 开启的 Agent 成员，按创建顺序取**第一个**——一次只认领一个，
        不抢、不重复指派。判别顺序与「谁会被唤醒」同族：不可用的 Agent 不参与裁决。

        「**每张任务至多一次**」由 ``Database.claim_task`` 的 ``claimed_at`` 标记构造性保证；这里只做
        「这次要不要动它」的早退，真正的把关在库层（并发下同一个任务不可能被开两次世代）。
        认领唤醒认领者走既有的 ``notify_task_assignment``：与 owner 指派同一条路径，**不消耗跳数**。

        建任务那个扳机**不是**直接调这里：它调 :meth:`notify_task_created`，由后者把 team 频道分走。
        """
        task = self._db.get_task(task_id)
        if task["assignee_member_id"] is not None or task["claimed_at"] is not None:
            return 0
        # team 模式下认领不再是一条唤醒路径（D16 / D23=B）：创建立即停在 todo，不替任何人接手。
        # 建任务的 team 分支已由 `notify_task_created` 分走；这里的早退仍然必要——
        # `claim_new_task` 是公开入口，别的调用方（含测试）不该在 team 频道里认领。
        if self._db.get_channel_config(channel_id)["mode"] == "team":
            return 0
        claimant = next(
            (
                agent
                for agent in self._db.list_target_agent_members(channel_id)
                if agent.get("auto_claim") and self.availability(agent).ok
            ),
            None,
        )
        if claimant is None:
            return 0
        claimed_task, messages = self._db.claim_task(task_id, str(claimant["id"]))
        if not messages:  # 与上面同一组前置条件；库层再判一次（幂等无操作）
            return 0
        target_id = str(claimed_task["target_id"])
        # 一个认领事务产出**两条**记录（已认领 + 认领后自动开工）：逐条 push 给订阅者，
        # 否则实时视图只看到第一条、而补拉看到两条——那两个面不能分叉。
        for message in messages:
            self._events.publish(target_id, {"type": "message", "seq": message["seq"], "message": message})
        # `from_seq` **仍取第一条**（「已认领」那条）：它既是世代标识（ADR-0010 三），
        # 也是本轮唤醒的 base_seq——不要改成最后一条。
        self.notify_task_assignment(
            target_id,
            int(messages[0]["seq"]),
            str(claimant["id"]),
            reason=WAKE_REASON_CLAIM,
            signal=ReplySignal.CLAIM,
        )
        return 1

    def notify_task_reassignment(
        self,
        target_id: str,
        from_seq: int,
        member_id: str,
        *,
        actor_member_id: str | None = None,
    ) -> int:
        """Agent 转派按一次有界 agent→agent 跳处理；预算耗尽时保留记录但不唤醒。

        team 模式下唤醒对象是队长；``actor_member_id`` 是队长时零唤醒（不自我唤醒）。
        """
        return self._notify_task_assignee(
            target_id,
            from_seq,
            member_id,
            agent_jump=True,
            actor_member_id=actor_member_id,
        )

    def _notify_task_assignee(
        self,
        target_id: str,
        from_seq: int,
        member_id: str,
        *,
        agent_jump: bool,
        reason: str = WAKE_REASON_CHANNEL_MESSAGE,
        signal: ReplySignal = ReplySignal.NONE,
        actor_member_id: str | None = None,
    ) -> int:
        """验证任务指派事件、按动作预算裁决后唤醒新受派 Agent。

        ``signal`` **只认领轮为真**（``CLAIM``，ADR-0016 五的适用范围）：owner 手动改派走同一条
        路径、signal 仍是 ``NONE``，于是那一轮维持既有纪律（不强制发言）。纪律与文案来源全由这个
        进程内枚举回答，**不**读 ``reason`` 文本。
        """
        target = self._db.get_target(target_id)
        if target["kind"] != "task":
            return 0
        message = self._db.get_message(target_id, from_seq)
        if message["kind"] != "system":
            return 0
        team = self._team_config(target_id, target)
        if team is not None:
            return self._notify_team_task_assignee(
                target_id,
                from_seq,
                team,
                agent_jump=agent_jump,
                actor_member_id=actor_member_id,
            )
        assignee = next(
            (agent for agent in self._db.list_target_agent_members(target_id) if agent["id"] == member_id),
            None,
        )
        if assignee is None:
            return 0
        if agent_jump and not self._spend_jump(target_id, from_seq, [assignee]):
            return 0
        self._dispatch(
            assignee,
            target_id,
            from_seq,
            reason,
            consumed_budget=agent_jump,
            pierced=False,
            signal=signal,
        )
        return 1

    # ─── team 模式（D1 / D18 / D23=B） ────────────────────────────────────
    def _team_config(self, target_id: str, target: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """解析该 target 的 team 配置；不是 team 频道（或 DM）时返回 ``None``。

        task target **没有**自己的 mode 列：它经 ``task.channel_id -> channel.mode`` 解析
        （ADR-0022 §一）。DM 恒为 autonomous（D1）。
        """
        target = target if target is not None else self._db.get_target(target_id)
        if target["kind"] == "dm":
            return None
        task: dict[str, Any] | None = None
        if target["kind"] == "task":
            task = self._db.task_for_target(target_id)
            if task is None:
                return None
            channel_id = str(task["channel_id"])
        elif target["kind"] == "channel":
            channel_id = target_id
        else:  # pragma: no cover - TARGET_KINDS 是封闭集合
            return None
        config = self._db.get_channel_config(channel_id)
        if config["mode"] != "team":
            return None
        return {
            **config,
            "target_id": target_id,
            "target_kind": target["kind"],
            "task_id": None if task is None else str(task["id"]),
        }

    def leader_status(self, config: dict[str, Any]) -> Availability:
        """队长的可读可用性（D18）：不存在 / 已删除 / 已停用 / 不是成员 / 不可运行。

        设置页的 ``leader_available`` / ``leader_reason`` 与唤醒裁决读的是这**同一个**方法：
        「配置无效」于是不可能在两个面上给出不同结论。
        """
        locale = self._locale()
        leader_id = config.get("team_leader_member_id")
        channel_id = str(config.get("channel_id") or "")
        if not leader_id:
            return Availability(False, render("team.leader_missing", locale))
        try:
            agent = self._db.get_agent(str(leader_id))
        except Exception:
            return Availability(False, render("team.leader_deleted", locale, member_id=str(leader_id)))
        if agent.get("deleted_at"):
            return Availability(False, render("team.leader_deleted", locale, member_id=str(leader_id)))
        if not agent.get("enabled", True):
            return Availability(False, render("team.leader_disabled", locale, member_id=str(leader_id)))
        try:
            is_member = self._db.is_target_member(channel_id, str(leader_id))
        except Exception:
            is_member = False
        if not is_member:
            return Availability(False, render("team.leader_not_member", locale, member_id=str(leader_id)))
        return self.availability(agent)

    def _notify_team(
        self,
        target_id: str,
        from_seq: int,
        message: dict[str, Any],
        team: dict[str, Any],
        reason: str = WAKE_REASON_CHANNEL_MESSAGE,
    ) -> int:
        """team 模式下的唤醒集合：队长（channel 另加穿透候选；task 不穿透）。

        运行矩阵（规格 §3.3）逐条落在这里：channel 的人类消息只唤醒队长、Agent 消息零唤醒；
        task 的人类/非队长 Agent 消息只唤醒队长；队长自己的消息零唤醒；system 不在对话回合内。
        """
        author_id = str(message["author"]["id"])
        author_kind = str(message["author"]["kind"])
        status = self.leader_status(team)
        if not status.ok:
            self._record_invalid_leader(team, from_seq)
            return 0
        leader_id = str(team["team_leader_member_id"])
        leader = self._db.get_agent(leader_id)
        mentions = set(mentioned_member_ids(message["mention_snapshot"]))
        targets: list[_WakeTarget] = []

        if team["target_kind"] == "channel":
            # D2：队长自己的最终回复不再唤醒任何成员（含穿透）。
            if message["kind"] != "human":
                return 0
            if leader_id != author_id:
                targets.append(_WakeTarget(member=leader))
            # D17：穿透仍是一次性送达，但不进入 pipeline（成员集合不再被唤醒）。
            seen = {leader_id, author_id} | {
                str(member["id"]) for member in self._db.list_target_agent_members(team["channel_id"])
            }
            for member_id in mentions:
                if member_id in seen:
                    continue
                agent = self._pierce_target(member_id)
                if agent is None:
                    continue
                targets.append(_WakeTarget(member=agent, pierced=True))
                seen.add(member_id)
        else:  # task：只唤醒队长，不做穿透
            if message["kind"] == "human":
                if leader_id != author_id:
                    targets.append(_WakeTarget(member=leader))
            elif author_kind == "agent":
                # 队长自己的最终回复零唤醒；非队长 Agent 的任务消息只唤醒队长（不穿透）。
                if author_id == leader_id:
                    return 0
                targets.append(_WakeTarget(member=leader))
            else:
                return 0

        if not targets:
            return 0
        jump_delivered = author_kind == "agent" and team["target_kind"] != "dm"
        if jump_delivered and not self._spend_jump(
            target_id, from_seq, [target.member for target in targets]
        ):
            return 0
        # 队长的纪律来源与其它面同一套判据（#189）：人类消息 ⇒ 必答；Agent 消息（非队长自己的）
        # 唤醒队长的那一轮维持既有纪律（不强制发言）。读消息 kind，不读作者成员 kind。
        base_signal = ReplySignal.HUMAN_MESSAGE if message["kind"] == "human" else ReplySignal.NONE
        for wake_target in targets:
            member = wake_target.member
            member_signal = ReplySignal.MENTION if member["id"] in mentions else base_signal
            member_reason = WAKE_REASON_MENTION if member["id"] in mentions else reason
            self._dispatch(
                member,
                target_id,
                from_seq,
                member_reason,
                consumed_budget=jump_delivered,
                pierced=wake_target.pierced,
                signal=member_signal,
            )
        return len(targets)

    def _notify_team_task_created(self, task_id: str, team: dict[str, Any]) -> int:
        """team 频道建任务：向队长投递**一轮**必答，仍不认领（#179 A / ADR-0025 §二）。

        `system` 事件消息本身不是对话回合（`notify` 在它上面早退），所以这一轮不经 `notify`，
        而是在这里显式投递。投递的三个不变项：

        - **不认领**：`task.status/assignee_member_id/claimed_at/updated_at` 都不动，任务停在 `todo`；
        - **不唤醒成员**、**不穿透**：只有队长一轮；
        - **不扣跳**：人类建任务是人的动作，不是 agent→agent 跳（ADR-0005）；`target_budget.used` 不动。

        `from_seq` 取建任务那条 `system` 消息的 `seq`：它是这一轮的 `base_seq`，也是队长
        在轮次里看到「任务已创建：<标题>」的那条待读消息的序号（还没被任何游标消费过）。
        """
        target_id = str(team["target_id"])
        from_seq = self._task_created_seq(task_id)
        status = self.leader_status(team)
        if not status.ok:
            # 与「人类消息 → 队长不可用」同口径（D18）：零唤醒 + 一条 invalid-leader。
            self._record_invalid_leader(team, from_seq)
            return 0
        leader = self._db.get_agent(str(team["team_leader_member_id"]))
        self._dispatch(
            leader,
            target_id,
            from_seq,
            WAKE_REASON_CHANNEL_MESSAGE,
            consumed_budget=False,
            pierced=False,
            # 建任务 / 改派 / 转派都是「任务变化」（#179 B）：不是人类消息（那是一条真的人类发言），
            # 也不是认领（team 频道不认领）。
            signal=ReplySignal.TEAM_TASK_CHANGE,
        )
        return 1

    def _task_created_seq(self, task_id: str) -> int:
        """建任务那条 `system` 消息的 seq（ADR-0016 四的同一条记录：「任务已创建：<title>」）。

        读法按**任务线程的消息流**取第一条 `system`——不硬编码 1，也不另建一张映射表：
        `Database.create_task` 在同一事务里逐字落下它，流是它唯一的正本。
        """
        for message in self._db.list_task_messages(task_id):
            if message["kind"] == "system":
                return int(message["seq"])
        return 0  # pragma: no cover - create_task 必然落一条 system；0 = 「从头读」，不编造事实

    def _notify_team_task_assignee(
        self,
        target_id: str,
        from_seq: int,
        team: dict[str, Any],
        *,
        agent_jump: bool,
        actor_member_id: str | None,
    ) -> int:
        """team 任务线程的改派/转派：唤醒**队长**，不唤醒新受派者（ADR-0005 收窄）。

        这一轮是**确定信号**（`ReplySignal.TEAM_TASK_CHANGE`，#179 B / ADR-0025 §三）：队长可以合法沉默的
        旧口径就是用户看到的「指派后只有一条消息就停住」。`actor` 是队长时仍零唤醒（不自我唤醒）。
        """
        status = self.leader_status(team)
        if not status.ok:
            self._record_invalid_leader(team, from_seq)
            return 0
        leader_id = str(team["team_leader_member_id"])
        if actor_member_id == leader_id:
            return 0
        leader = self._db.get_agent(leader_id)
        if agent_jump and not self._spend_jump(target_id, from_seq, [leader]):
            return 0
        self._dispatch(
            leader,
            target_id,
            from_seq,
            WAKE_REASON_CHANNEL_MESSAGE,
            consumed_budget=agent_jump,
            pierced=False,
            signal=ReplySignal.TEAM_TASK_CHANGE,
        )
        return 1

    def _record_invalid_leader(self, team: dict[str, Any], from_seq: int) -> None:
        """队长不可用 ⇒ 零唤醒，并留下一条 ``invalid-leader`` 运行记录（D18）。

        可读原因由 ``GET /api/channels/{id}/config`` 的 ``leader_reason`` 给出；运行记录本身
        没有 reason 列（规格 §4.2），它只回答「这一轮为什么没跑」。
        """
        leader_id = team.get("team_leader_member_id")
        if not leader_id:
            return
        with suppress(Exception):
            self._db.create_team_run(
                target_id=str(team["target_id"]),
                leader_member_id=str(leader_id),
                base_seq=int(from_seq),
                status="invalid-leader",
                finished_at=utc_now(),
            )

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
          消息不可能同时读到「还剩 1」而各投一次（硬上界 = 该世代生效上界，由此成立）。
        """
        with self._lock:
            if not any(
                member["id"] not in self._in_flight or self._in_flight[member["id"]].target_id != target_id
                for member in targets
            ):
                return True
            state = self._db.budget_state(target_id)
            # 上界取**当前世代的有效上界**（``target_budget.applied_limit`` 快照，频道覆盖或内置
            # 默认）：与只读面 ``GET /api/channels/{id}/budget`` 是同一份解析结果，否则「展示的
            # effective limit」会与这里真正执行的压制行为分成两条事实。
            if state["used"] >= state["limit"]:
                self._record_budget_exhausted(target_id, from_seq, targets)
                return False
            self._db.consume_budget(target_id, state["epoch_seq"])
            return True

    def _record_budget_exhausted(self, target_id: str, from_seq: int, targets: list[dict[str, Any]]) -> None:
        """耗尽的世代里不投递任何唤醒，但每个**本应被唤醒**的成员都要留下可追溯的轮次结果。

        状态点**不**因此变 ``--error``（``status_of`` 不把 ``budget-exhausted`` 当失败）：预算耗尽
        既不是「它坏了」也不是「它不想说」，它是「这个人类世代已经用满它的回应预算上界」这条后端事实。
        """
        state = self._db.budget_state(target_id)
        reason = render(
            "reason.budget_exhausted", self._locale(), epoch=state["epoch_seq"], limit=state["limit"]
        )
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
        signal: ReplySignal = ReplySignal.NONE,
        history: bool = False,
    ) -> dict[str, Any] | None:
        """跑一轮。返回落下的轮次结果；瞬态轮次（无新消息、只被合并）返回 None。

        ``consumed_budget`` 只写进这一行的 ``round_outcome.consumed_budget`` 列：它是**行级**
        标记（「这一轮由哪次跳送来」），**不是**跳的计数 —— 一次跳投递 N 个成员会写 N 条为真的行，
        跳数只 +1。跳数的唯一正本是 ``target_budget.used``。

        ``reason`` 只承担**可读诊断 + 正在回复指示**，**不**承担纪律判据：那是
        ``signal``（进程内枚举、不落库）的事。原因：同一 Agent 同 target 的重复唤醒不排队、
        只合成一条 merged，而跨 target 的 deferred 轮次会继承参数——若判据读 reason，一条同时到达的
        普通消息就可能让「必答」静默失效，取决于谁先到（ADR-0016 五）。
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
                            signal=signal,
                        )
                    else:
                        deferred.from_seq = max(deferred.from_seq, int(from_seq))
                        deferred.consumed_budget = deferred.consumed_budget or consumed_budget
                        deferred.pierced = deferred.pierced or pierced
                        # 合并**不削弱**纪律：任一方强制 ⇒ 合并后强制（与 consumed_budget / pierced
                        # 同款合并）；两方都强制时来源按固定优先级取，诊断不随到达顺序变。
                        deferred.signal = deferred.signal.merged_with(signal)
                        deferred.history = deferred.history or history
                        if reason == WAKE_REASON_MENTION:
                            deferred.reason = reason
                return None
            self._in_flight[member_id] = _Turn(target_id=target_id, repeat_bases=[], deferred_rounds={})

        started_at = utc_now()
        base_seq = from_seq
        continuation_requested = False
        try:
            cursor = self._db.agent_cursor(target_id, member_id)
            fresh = self._db.list_messages(target_id, after_seq=cursor)
            if not fresh:
                # 瞬态轮次（无新消息）：按规格不落盘。
                return None
            # 自动续跑轮（ADR-0027）把**整个 target 历史**交给模型：续跑的意义就是接上自己上一轮的
            # 产出，只喂一条「继续」记录会让它从零重做。其它轮次逐字保持既有口径（只喂新消息、
            # 且不含自己的消息）。
            pending = (
                self._db.list_messages(target_id)
                if history
                else [message for message in fresh if message["author"]["id"] != member_id]
            )
            if not pending:
                return None

            # mentions 只看**本轮新消息里别人说的话**（ADR-0027）：历史里旧的点名不该把续跑轮改判成
            # 「被个人提及」；自己消息里的自我提及同样不构成本轮的点名（与普通轮次的既有口径一致）。
            mentioned_messages = [message for message in fresh if message["author"]["id"] != member_id]
            mentions = {
                mention
                for message in mentioned_messages
                for mention in mentioned_member_ids(message["mention_snapshot"])
            }
            mentioned = member_id in mentions
            base_seq = max(message["seq"] for message in pending)
            # 历史只进模型输入：唤醒、回应纪律与读位仍使用上面的新消息基线。
            inputs = (
                pending
                if history
                else [message for message in self._db.list_messages(target_id) if message["seq"] <= base_seq][
                    -context_window_messages(agent) :
                ]
            )

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
                team_context = self._team_round_context(target_id, agent)
                # 确定信号的可读文案按**信号来源**选（#179 / #189 / ADR-0016 五 / ADR-0025 §六）：四种
                # 确定信号同级不同源，把人类消息写成「任务变化」「认领」或「被个人提及」都是在写一条
                # 没发生过的事实。来源是进程内枚举 `signal`，**不**读 `reason`、也不从「本轮是不是队长」
                # 反推（那个反推在 #189 之前可以，但它对人声与任务变化一视同仁，正是本票要拆掉的）。
                copy_prefix = signal.copy_prefix
                if copy_prefix is None:
                    silent_key, ignored_key = "reason.mention_silent", "reason.mention_ignored"
                    no_body_key = "reason.mention_no_body"
                else:
                    silent_key = f"reason.{copy_prefix}_silent"
                    ignored_key = f"reason.{copy_prefix}_ignored"
                    no_body_key = f"reason.{copy_prefix}_no_body"

                # Plan 只在**任务 / team 轮次**自动可用（规格 §3.5）：频道与 DM 的自主轮次
                # 既不装载也不回写。会话本身不写消息、不扣预算；事件仍走 #141 的公共发布入口。
                def publish_plan(event: dict[str, Any]) -> None:
                    self._events.publish(target_id, event)

                plan_session = (
                    PlanSession(
                        self._db,
                        target_id=target_id,
                        base_seq=base_seq,
                        publish=publish_plan,
                    )
                    if task_context is not None or team_context is not None
                    else None
                )
                team_run_id: str | None = None
                if team_context is not None:
                    team_run = self._db.create_team_run(
                        target_id=target_id,
                        leader_member_id=member_id,
                        base_seq=base_seq,
                    )
                    team_run_id = str(team_run["id"])
                    # 分派运行记录挂在这次 team_run 下（pipeline 由适配层按轮构建）。
                    team_context = replace(team_context, team_run_id=team_run_id)
                team_status = "failed"
                try:
                    outcome = self._run_seam(
                        agent,
                        inputs,
                        mentioned=mentioned,
                        target_id=target_id,
                        task_context=task_context,
                        signal=signal,
                        direct_message=self._db.get_target(target_id)["kind"] == "dm",
                        team=team_context,
                        plan=plan_session,
                    )
                    team_status = "completed" if outcome.status != "failed" else "failed"
                finally:
                    if team_run_id is not None:
                        with suppress(Exception):
                            self._db.finish_team_run(team_run_id, team_status)
                # rubric：**确定信号有四条**（被个人提及 / 被认领唤醒 / team 队长被任务变化唤醒 /
                # 被人类消息唤醒），四条**同级**、**同一套判据、同一处落点**。后三条用 ``signal.mandatory``
                # 判定（不读 reason：重复唤醒会被合并，原因见 run_round docstring），区别只在**可读文案**
                # （`ReplySignal.copy_prefix`）；提及那一侧仍用 ``mentioned``。自判忽略在不静默（不能把
                # 「它坏了」当成「它不想说」）。
                definite_signal = mentioned or signal.mandatory
                # ``silent``（#58）与 ``ignored`` 在确定信号下**同级**处理：两者都是「它决定不说话」，
                # 而不是「没被问」。CONTEXT.md 的**回应判断**词条明写这条纪律不因状态来源而放宽。
                if definite_signal and outcome.status in ("ignored", "silent"):
                    # 诊断文案按**哪个**确定信号 + **哪种**沉默选：认领轮不能写成「被个人提及」，
                    # 声明沉默也不能写成「自判忽略」（那是一个不存在的事实）。
                    if signal.mandatory:
                        key = silent_key if outcome.status == "silent" else ignored_key
                    elif outcome.status == "silent":
                        key = "reason.mention_silent"
                    else:
                        key = "reason.mention_ignored"
                    outcome = RoundOutcome(
                        status="failed",
                        reason=render(key, self._locale(), reason=outcome.reason),
                    )
                # 确定信号下不产出正文同理：不是「不想说」，是没做到。
                # **动作-only 算回应**：``task_action is not None`` 时不判失败。
                elif (
                    definite_signal
                    and outcome.status == "replied"
                    and outcome.finished_reason == "completed"
                    and not outcome.messages
                    and outcome.task_action is None
                ):
                    key = no_body_key if signal.mandatory else "reason.mention_no_body"
                    outcome = RoundOutcome(
                        status="failed",
                        reason=render(key, self._locale()),
                    )
                if copy_prefix is not None:
                    # 可读后缀（ADR-0016 五）：照 ``reason.pierced_suffix`` 的既有先例追加进 reason 文本。
                    # 它只是诊断——纪律已由上面的枚举落定。被点名（``MENTION``）走既有口径：它本来就
                    # 有自己的一句「本轮有人点名了你」，不另加后缀（#57 以来的行为逐字不变）。
                    round_key = f"reason.{copy_prefix}_round"
                    outcome = RoundOutcome(
                        status=outcome.status,
                        reason=render(round_key, self._locale(), reason=outcome.reason),
                        messages=outcome.messages,
                        started_at=outcome.started_at,
                        finished_at=outcome.finished_at,
                        consumed_budget=outcome.consumed_budget,
                        task_action=outcome.task_action,
                        finished_reason=outcome.finished_reason,
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
                        finished_reason=outcome.finished_reason,
                    )
                self._db.mark_read(target_id, base_seq, member_id=member_id)
            elif outcome.status in ("ignored", "silent") or (
                outcome.status == "replied" and outcome.task_action is not None
            ):
                # ``ignored`` / ``silent`` 都是「这一轮处理完了、没有正文要留」：待读推到本轮基线，
                # 否则这些消息会在下一轮被当成「新消息」再喂一次（同一轮被问两遍）。
                self._db.mark_read(target_id, base_seq, member_id=member_id)

            if outcome.status == "replied" and outcome.task_action is not None:
                if isinstance(outcome.task_action, ContinueTaskAction):
                    # 自动续跑是**请求**而不是库写入（ADR-0027）：先校验它属于这张任务、属于受派者，
                    # 真正的投递在收口后做（同一成员 in-flight 时投递会被当成重复唤醒合并掉）。
                    rejection = self._validate_continue_request(agent, target_id, outcome.task_action)
                    continuation_requested = rejection is None
                    if rejection:
                        outcome.reason = "；".join(part for part in (outcome.reason, rejection) if part)
                else:
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
                finished_reason=outcome.finished_reason,
            )
            self._publish_outcome(target_id, base_seq, record)
            if outcome.finished_reason == "interrupted":
                self._events.publish(
                    target_id,
                    {"type": "round.interrupted", "member_id": member_id, "outcome": record},
                )
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
                self._dispatch(
                    deferred.agent,
                    deferred.target_id,
                    deferred.from_seq,
                    deferred.reason,
                    consumed_budget=deferred.consumed_budget,
                    pierced=deferred.pierced,
                    # 纪律与来源必须沿 deferred 一起传下去，否则「跨 target 被挡在后面」的必答轮会以
                    # signal=NONE 重跑——纪律静默失效，reason 后缀也丢（ADR-0016 五）。
                    signal=deferred.signal,
                    history=deferred.history,
                )
            # 自动续跑必须在 in-flight **清掉之后**投递（ADR-0027）：同成员同 target 的新一轮若在收口前
            # 投递，会被当成重复唤醒合并进本轮，续跑当场消失。轮次结论已经落库，这里再失败也不能
            # 打穿工作线程（与 try 块里「任何异常都不逃逸」同一条纪律）。
            if continuation_requested:
                with suppress(Exception):
                    self._dispatch_continuation(agent, target_id)
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
            if isinstance(action, SetTaskStatusAction) and not (
                self._db.is_target_member(target_id, str(agent["id"]))
                or self._is_team_leader(target_id, str(agent["id"]))
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

    # ─── 自动续跑（ADR-0027） ─────────────────────────────────────────────
    def _validate_continue_request(
        self, agent: dict[str, Any], target_id: str, action: ContinueTaskAction
    ) -> str | None:
        """校验「申请续跑」属于这张任务、且是本轮 Agent 的受派者身份；返回拒绝文案或 ``None``。

        动作本身不改库，所以这里只做「这是不是一个真实、且由受派者发出的请求」这一层；投递条件
        （任务仍在进行中、世代的轮数上限）由 :meth:`_dispatch_continuation` 在收口时再裁决。
        """
        locale = self._locale()
        try:
            task = self._db.get_task(action.task_id)
        except DomainError as error:
            return render("error.task_action_rejected", locale, message=error.message)
        if str(task["target_id"]) != target_id:
            return render(
                "error.task_action_rejected",
                locale,
                message=render("error.task_action_wrong_target", locale),
            )
        if str(task.get("assignee_member_id") or "") != str(agent["id"]):
            return render(
                "error.task_action_rejected",
                locale,
                message=render("error.continue_not_assignee", locale),
            )
        return None

    def _dispatch_continuation(self, agent: dict[str, Any], target_id: str) -> int:
        """受派者申请自动续跑：落一条 ``system`` 记录，再把同一个人叫醒一轮；返回是否投递（0 / 1）。

        三件**必须**在这里一起成立的边界：

        - **只在进行中的任务上续跑**：任务已经离开 ``in_progress``（完成 / 待审 / 关闭）或换了受派者，
          这次申请就过期了——静默不投递，不写记录。
        - **不消耗跳数**：续跑不是 agent→agent 跳（跳的载体是 Agent 消息，它唤醒的是**别人**）；
          ``target_budget.used`` 一字不动。
        - **每世代有界**：轮数从 ``round_outcome`` 读（持久、跨重启一致），到达
          ``TASK_WORK_ROUNDS_PER_EPOCH`` 后不再投递，改落一条可读的「已暂停」记录——不停在
          一个没人解释的静默里。

        唤醒的载体仍必须是一条消息（ADR-0017 的构造性前提）：先落 ``system.task.continue``，
        再以它的 ``seq`` 投递；续跑轮把整个任务线程作为输入（``history=True``），模型才接得上自己
        上一轮的产出。
        """
        task = self._db.task_for_target(target_id)
        if task is None:
            return 0
        if str(task["status"]) != "in_progress":
            return 0
        if str(task.get("assignee_member_id") or "") != str(agent["id"]):
            return 0
        if self._team_config(target_id) is not None:
            return 0
        if not self.availability(agent).ok:
            return 0
        locale = self._locale()
        if self._rounds_since_epoch(target_id, str(agent["id"])) >= TASK_WORK_ROUNDS_PER_EPOCH:
            message = self._db.append_message(
                target_id=target_id,
                body=render("system.task.continue_paused", locale, rounds=TASK_WORK_ROUNDS_PER_EPOCH),
                author_member_id=OWNER_ID,
                kind="system",
                mention_snapshot=[],
            )
            self._events.publish(target_id, {"type": "message", "seq": message["seq"], "message": message})
            return 0
        message = self._db.append_message(
            target_id=target_id,
            body=render("system.task.continue", locale),
            author_member_id=OWNER_ID,
            kind="system",
            mention_snapshot=[],
        )
        self._events.publish(target_id, {"type": "message", "seq": message["seq"], "message": message})
        self._dispatch(
            agent,
            target_id,
            int(message["seq"]),
            WAKE_REASON_CONTINUE,
            consumed_budget=False,
            pierced=False,
            signal=ReplySignal.CONTINUE,
            history=True,
        )
        return 1

    def _rounds_since_epoch(self, target_id: str, member_id: str) -> int:
        """该成员在**当前人类世代**里已经跑完的轮数（``merged`` 行不算一轮）。

        上限的计数读 ``round_outcome``（运行事实的正本）：它跨重启一致、不依赖 locale，也不新增 DDL。
        世代的起算点是 ``target_budget.epoch_seq``——与本文件里跳数的世代语义同一个键。
        """
        epoch = int(self._db.budget_state(target_id)["epoch_seq"])
        outcomes = self._db.list_round_outcomes(target_id, after_seq=epoch)
        return sum(
            1 for outcome in outcomes if str(outcome["member_id"]) == member_id and not outcome["merged"]
        )

    def _is_team_leader(self, target_id: str, member_id: str) -> bool:
        """该成员是否是此 target 所属 team 的队长。

        team 任务线程里只有队长会跑任务轮次（ADR-0022 §五），因此结构化任务工具的把关从
        「任务成员」扩到「任务成员 **或** 该频道队长」：队长不是受派者时也能批准
        ``in_review → done``；自审禁令与迁移表仍由 ``Database.update_task_status`` 执行。
        """
        team = self._team_config(target_id)
        return team is not None and str(team["team_leader_member_id"]) == member_id

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
        """任务上下文（`TaskActionContext` 的**唯一装配点**）：除 id 与成员表外还带任务本体。

        ``title`` / ``status`` 是 ADR-0016 四要求进 prompt 的那两样（标题、当前状态）；受派者身份
        由接缝根据 ``assignee_member_id`` 与**本轮 Agent 的 id** 比对得出，不在这里预先替你判断。
        """
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
            title=str(task["title"]),
            status=str(task["status"]),
            # team 任务线程只唤醒队长（ADR-0022）：续跑会绕开那条裁决，因此那边不提供这个动作。
            allow_continue=self._team_config(target_id) is None,
        )

    def _team_round_context(self, target_id: str, agent: dict[str, Any]) -> TeamRoundContext | None:
        """队长轮次的 team 上下文；非 team target 或非队长时返回 ``None``。

        **每轮**从库里读当前频道成员（D11）：加入/移除从下一轮生效，不需要重启进程。
        不可运行的成员不进入 pipeline（它们无法被 ``TeamAssign`` 真正执行）。
        """
        team = self._team_config(target_id)
        if team is None or str(team["team_leader_member_id"]) != str(agent["id"]):
            return None
        members = tuple(
            member
            for member in self._db.list_target_agent_members(str(team["channel_id"]))
            if str(member["id"]) != str(agent["id"]) and self.availability(member).ok
        )

        def publish(event: dict[str, Any]) -> None:
            self._events.publish(target_id, event)

        return TeamRoundContext(
            target_id=target_id,
            leader_id=str(agent["id"]),
            members=members,
            task_id=team["task_id"],
            budget=self._db,
            # team_assignment 是 live-only（ADR-0020）：分派开始/收口都经公共入口发到该 target。
            publish=publish,
        )

    def _run_seam(
        self,
        agent: dict[str, Any],
        pending: list[dict[str, Any]],
        *,
        mentioned: bool,
        target_id: str,
        task_context: TaskActionContext | None = None,
        signal: ReplySignal = ReplySignal.NONE,
        direct_message: bool = False,
        team: TeamRoundContext | None = None,
        plan: PlanSession | None = None,
    ) -> RoundOutcome:
        leader_id = str(agent["id"])

        def emit(record: Any) -> None:
            if record.type in ("REPLY_START", "REPLY_END"):
                with self._lock:
                    turn = self._in_flight.get(leader_id)
                    if turn is not None and turn.target_id == record.target_id:
                        if record.type == "REPLY_START":
                            turn.reply_ids.add(record.reply_id)
                            turn.interrupt = getattr(self._seam, "interrupt_round", None)
                        else:
                            turn.reply_ids.discard(record.reply_id)
            # 事件只直播、不落库（ADR-0020）；emit 在编排的工作线程里同步调用，
            # ``EventBus.publish`` 对满队列丢事件、不阻塞模型调用。
            self._events.publish(record.target_id, agent_event(record))
            # 成员的「正在回复」只按它的 REPLY_START / REPLY_END 发（规格 §4.5）：队长的 typing
            # 仍由 ``_run_round`` 自己在轮次开始/结束发，成员帧不改变那条既有语义。
            if record.type in ("REPLY_START", "REPLY_END") and record.member_id != leader_id:
                self._events.publish(
                    record.target_id,
                    {
                        "type": "typing",
                        "member_id": record.member_id,
                        "active": record.type == "REPLY_START",
                        "reason": TYPING_REASON_TEAM_ASSIGNMENT,
                    },
                )

        try:
            return self._seam.run_round_stream(
                agent,
                pending,
                mentioned=mentioned,
                target_id=target_id,
                task_context=task_context,
                signal=signal,
                direct_message=direct_message,
                team=team,
                emit=emit,
                plan=plan,
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
        if self._workspace_unavailable is not None:
            workspace_reason = self._workspace_unavailable(str(agent.get("id") or ""))
            if workspace_reason:
                # Docker 不可用时适配器停用工作区工具，仍可完成对话（运行时升级）。
                return Availability(True, workspace_reason)
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
