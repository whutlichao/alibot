"""提醒的**到点**判定与投递（ADR-0017 §一 / §三 / §四）：本能力唯一的 core seam。

**seam 的形状**（宽度 = 1 输入 + 1 输出）：

```text
   注入时刻 ───▶ ReminderTrigger.deliver(now) ───▶ DeliveryReport（这一批的投递后果）
```

它**不暴露**轮询间隔、锁、线程生命周期——那些是 :class:`ReminderPoller` 这个 adapter 的内部关注点，
而 :class:`ReminderTrigger` 只回答一件事：「在**这个时刻**，这一批提醒各自的投递后果是什么」。
于是测试可以喂一个时刻就驱动全部分支（准点 / 补投 / 窗口外 / 已投过 / 无归属），**一行都不真等时间**；
生产装配下同一段代码由后台线程按 :data:`POLL_INTERVAL_SECONDS` 驱动，并在进程启动时**先扫一次**。

五条分支（对外只经既有 HTTP 读数面观察）：

| 分支 | 条件 | 后果 |
| --- | --- | --- |
| 准点 | 有归属、`\\|now − 到期\\| ≤ POLL_INTERVAL_SECONDS` | 落一条 `system` 消息（`system.reminder.fired`）+ 写 `fired_at` |
| 补投 | 有归属、`POLL_INTERVAL_SECONDS < \\|now − 到期\\| ≤ 24 小时` | 落一条 `system` 消息（`system.reminder.fired_late`）+ 写 `fired_at` |
| 窗口外 | 有归属、`\\|now − 到期\\| > 24 小时` | **只写** `fired_at`，不落消息（人类面呈现为**已错过**） |
| 已投过 | `fired_at` 已非空 | **改动 0 行**（「至多投递一次」的构造性保证） |
| 无归属 | `channel_id IS NULL` | **只写** `fired_at`，不落消息（不新增 target，ADR-0012 仍有效） |

**时区口径（唯一一处）**：本仓的 `reminder.time` 是按**本机本地时区**解释的到期时刻，而 `created_at` /
`fired_at` 是 **UTC 审计戳**。全实现只有 :func:`reminder_due_instant` 一处做「本地到期时刻 → UTC 瞬时」的
换算，它的三个消费点（24 小时窗口判定、30 秒精度判据、准点 / 补投文案分档）都调它——不许各写一份，
更不许把本地时刻当 UTC 直接比较（库里也不做这类比较）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - 只为类型面：本模块在运行期不 import 库层（db 反过来要 import 这里）
    from .db import Database
    from .events import EventBus

# 轮询间隔（C9）：**准点投递的精度上界**，同时是 C21 的准点 / 补投文案分档阈值。
# 「30 秒」这个界在实现里**只出现一次**——两个契约共用它，不会漂成两套阈值。
# 它刻意**不是**可配置档位：配错了的表现是「静默不响」（ADR-0017 §四）。
POLL_INTERVAL_SECONDS = 30

# 错过补投的窗口（C6）：距到期 `≤ 24 小时` ⇒ 补投；`> 24 小时` ⇒ 只写已投递标记。
# 边界值归「补投」一侧（唯一归属，不留含混）。同一个常量也用于 C20 的 `state` 推导。
MISSED_WINDOW = timedelta(hours=24)

# `reminder.time` 的形状（写路径的正则另在 ``db.REMINDER_TIME_PATTERN``：这里只负责**解释**它）。
REMINDER_TIME_FORMAT = "%Y-%m-%d %H:%M"

# ─── 读路径的派生状态（C20：``state`` 是只读派生值，不落库、不占列） ─────────────
REMINDER_STATE_PENDING = "pending"
REMINDER_STATE_FIRED = "fired"
REMINDER_STATE_MISSED = "missed"

# ─── 投递后果（seam 的输出词汇；测试与日志都用它，而不是「走了哪条代码路径」） ────
OUTCOME_ON_TIME = "on-time"
OUTCOME_LATE = "late"
OUTCOME_OUT_OF_WINDOW = "out-of-window"
OUTCOME_ALREADY_FIRED = "already-fired"
OUTCOME_UNAFFILIATED = "unaffiliated"

# 落库正文的两条**后端**词条（C18 的后端半边 / C21 的分档）：它们是 `system` 消息正文，
# 由投递器在 HTTP 请求之外写入、没有请求头可读 ⇒ 只能按**库内语言偏好**渲染。
COPY_ON_TIME = "system.reminder.fired"
COPY_LATE = "system.reminder.fired_late"


def reminder_due_instant(due: str) -> datetime:
    """**唯一**的时区换算口径：把提醒的本地到期时刻（``YYYY-MM-DD HH:MM``）换算成 UTC 瞬时。

    逐字口径（C7）：「到期判定用**本机本地时间**；``created_at`` 仍是 **UTC 审计时间戳**；两者在库里
    **不做换算比较**，只在需要比较时（S9）才换算到同一时区」。``astimezone()`` 对无时区的
    ``datetime`` 按**本机时区**解释（含夏令时规则），返回带时区的瞬时，因此与 UTC 审计戳可比。
    """
    return datetime.strptime(due, REMINDER_TIME_FORMAT).astimezone()


def utc_instant(stamp: str) -> datetime:
    """把库里的审计戳（ISO-8601、毫秒精度、``Z`` 后缀）解析成带时区的瞬时。"""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def utc_audit_stamp(moment: datetime) -> str:
    """把投递时刻格式化成**审计戳**形状（UTC、毫秒精度、``Z`` 后缀，与 ``utc_now()`` 同族）。

    它是 ``fired_at`` 的唯一写入口：投递器把**判定用的那个瞬时**写成戳，于是「何时判定到点」与
    「何时被标成已投递」在库里是同一个时间，不存在两次取时钟的漂移。
    """
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reminder_state(due: str, fired_at: str | None) -> str:
    """由 ``fired_at`` 与到期时刻**推导**出的人类面状态（C20 的唯一推导规则）。

    - `fired_at IS NULL` ⇒ ``pending``（到期未到期都是「还没投递」，仍是待处理）；
    - `|fired_at − 到期| ≤ 24 小时` ⇒ ``fired``；
    - `fired_at − 到期 > 24 小时` ⇒ ``missed``（人类面呈现为**已错过**，它不是第三个状态列）。

    纯函数：不读当前时刻、不写库，因此同一个库连读两次 payload 逐字相同。
    """
    if not fired_at:
        return REMINDER_STATE_PENDING
    offset = utc_instant(fired_at) - reminder_due_instant(due)
    return REMINDER_STATE_FIRED if abs(offset) <= MISSED_WINDOW else REMINDER_STATE_MISSED


@dataclass(frozen=True)
class Firing:
    """一条提醒的投递后果：标识 + 后果词汇（``OUTCOME_*``）。"""

    reminder_id: str
    outcome: str


@dataclass(frozen=True)
class DeliveryReport:
    """这一批的投递后果（seam 的输出）。没有候选时 ``firings`` 为空元组。"""

    firings: tuple[Firing, ...] = ()

    def outcomes(self) -> tuple[str, ...]:
        return tuple(firing.outcome for firing in self.firings)

    def ids_for(self, *outcomes: str) -> tuple[str, ...]:
        return tuple(firing.reminder_id for firing in self.firings if firing.outcome in outcomes)


class ReminderDriver(Protocol):
    """投递器的**生命周期**侧面（生产 = 后台线程；测试 = 手动触发）。

    seam 本身（:class:`ReminderTrigger`）只有「喂时刻 → 拿后果」，生命周期在它之外——
    因此注入替身时调用方不需要学轮询间隔、锁或线程。
    """

    def start(self) -> None: ...

    def shutdown(self) -> None: ...

    def deliver(self, now: datetime) -> DeliveryReport: ...


# 装配点注入的是**驱动器的构造器**而不是实例：seam 需要库句柄（它由 ``create_app`` 打开），
# 而驱动器需要 seam。构造器的形状把这两条依赖顺序在一个地方收口。
ReminderDriverFactory = Callable[["ReminderTrigger"], ReminderDriver]


class ReminderTrigger:
    """到期投递器：输入**当前时刻**，输出这一批的投递后果。

    ``now`` 必须是**带时区**的瞬时（生产传 ``datetime.now(UTC)``；测试传「本地某刻 → 瞬时」）。
    写路径全部收在库层的 :meth:`Database.fire_reminder` 里：落 `system` 消息与写 `fired_at` 在
    **同一个事务**里收口，于是「消息落了但标记没写」这条会重复投递的中间态在构造上不可达。
    """

    def __init__(self, db: Database, events: EventBus | None = None) -> None:
        self._db = db
        self._events = events

    def deliver(self, now: datetime) -> DeliveryReport:
        """扫一遍提醒，把已到点的那些按分支处置，返回后果。

        **扫全部提醒**，而不是只扫未投递的——这是为了让 ``already-fired`` 这条分支在测试里**可驱动**
        （C8 判据 1 要求五分支都能被一个注入时刻驱出来）：已投递的行仍进候选，但一开头就落到
        ``already-fired``、**改动 0 行**。它因此是「至多投递一次」的**正面证据**（第二次扫的后果表里
        能读到它），而不是一条只在并发完赛时才能碰到的死代码。代价是每轮多算一遍已投递行
        （单用户本机、每 30 秒一次，量级可忽）。

        到期与否在 Python 侧判定——库里不对本地时刻与 UTC 时刻做大小比较（C7 规矩 3）。
        """
        firings: list[Firing] = []
        for reminder in self._db.list_reminders():
            reminder_id = str(reminder["id"])
            if reminder["fired_at"] is not None:
                firings.append(Firing(reminder_id, OUTCOME_ALREADY_FIRED))
                continue
            due = reminder_due_instant(str(reminder["time"]))
            if due > now:
                continue  # 未到点：不是候选，也不进入后果列表
            firings.append(Firing(reminder_id, self._fire(reminder, now - due, now)))
        return DeliveryReport(tuple(firings))

    # ─── 内部：单条提醒的分支判定与落库 ───────────────────────────────────
    def _fire(self, reminder: dict[str, Any], offset: timedelta, now: datetime) -> str:
        """按「有没有归属 / 距到期多远」选分支，把后果写进库，返回后果词汇。

        ``offset`` 已经是「现在 − 到期」的绝对间隔（到期未到不会走到这里）；``now`` 是**投递时刻**，
        它会被写成 ``fired_at``。两者来自**同一个**注入时刻，因此库里的已投递戳与这里的判定口径不会
        各算一份时间（C9 的 30 秒精度判据、C20 的 `state` 推导都依赖这一点）。
        """
        reminder_id = str(reminder["id"])
        channel_id = reminder["channel_id"]
        stamp = utc_audit_stamp(now)
        if channel_id is None:
            # 无归属：只写已投递标记，**任何 target 的消息条数都不变**（不新增 target）。
            return (
                OUTCOME_UNAFFILIATED
                if self._db.fire_reminder(reminder_id, fired_at=stamp) is not None
                else OUTCOME_ALREADY_FIRED
            )
        if offset > MISSED_WINDOW:
            # 窗口外：只写标记、不落消息（人类面呈现为**已错过**）。
            return (
                OUTCOME_OUT_OF_WINDOW
                if self._db.fire_reminder(reminder_id, fired_at=stamp) is not None
                else OUTCOME_ALREADY_FIRED
            )
        on_time = offset <= poll_interval()
        fired = self._db.fire_reminder(
            reminder_id, copy_key=COPY_ON_TIME if on_time else COPY_LATE, fired_at=stamp
        )
        if fired is None:
            return OUTCOME_ALREADY_FIRED
        self._publish(str(channel_id), fired["message"])
        return OUTCOME_ON_TIME if on_time else OUTCOME_LATE

    def _publish(self, target_id: str, message: dict[str, Any] | None) -> None:
        """把落库的那条 `system` 消息广播给订阅了该 target 的浏览器（形状与既有消息事件相同）。

        只广播、**不唤醒**：`system` 消息不是对话回合（`orchestration.py` 的 ``notify`` 对它早退），
        因此这里刻意不调编排——到点不消耗任何人的回应预算。
        """
        if self._events is None or message is None:
            return
        self._events.publish(target_id, {"type": "message", "seq": message["seq"], "message": message})


class ReminderPoller:
    """生产 adapter：进程内后台线程按固定间隔轮询 + **启动先扫一次**。

    启动扫在 :meth:`start` 里**同步**做完（它由 ``lifespan`` 在监听开始之前调用），于是「一条启动前
    就已过点且在窗口内的提醒」在第一次读到 ``GET /api/reminders`` 时 ``fired_at`` 已非空——不需要
    等第一次轮询到点（C8 判据 3）。之后的轮询在后台线程上跑，与启动扫是**同一段代码**（
    :meth:`ReminderTrigger.deliver`），不新增第二条时间路径。

    关停顺序由装配点保证：本对象在 ``database.close()`` **之前**收口（C10），因此线程不会在库关掉之后
    还去拿库句柄。
    """

    THREAD_NAME = "alibot-reminder-poller"

    def __init__(self, trigger: ReminderTrigger, *, interval: float = POLL_INTERVAL_SECONDS) -> None:
        self._trigger = trigger
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def deliver(self, now: datetime) -> DeliveryReport:
        return self._trigger.deliver(now)

    def start(self) -> None:
        """同步跑一次启动扫，然后起后台线程按间隔轮询（可重复启停）。"""
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._sweep()
        self._thread = threading.Thread(target=self._run, name=self.THREAD_NAME, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        """置停止信号并等线程退出（库关之前必须走到这里）。"""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self._interval + POLL_INTERVAL_SECONDS)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._sweep()

    def _sweep(self) -> None:
        """一轮投递。单条写失败（库忙、外键被并发删掉）不得让轮询线程死掉：下一轮会重试。"""
        try:
            self._trigger.deliver(datetime.now(UTC))
        except Exception:
            return


def poll_interval() -> timedelta:
    """:data:`POLL_INTERVAL_SECONDS` 的间隔形式（精度判据与文案分档共用同一个常量）。"""
    return timedelta(seconds=POLL_INTERVAL_SECONDS)
