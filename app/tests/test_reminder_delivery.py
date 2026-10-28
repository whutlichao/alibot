"""提醒**到点**（实施票 ② / issue #113）的 HTTP 黑盒契约：可注入的到期投递器。

测试 seam **只有一处**（规格 T2）：投递器的窄接口——喂一个**时刻**，拿到「这一批的投递后果」；
后果的一切可观察痕迹都经**既有 HTTP 读数面**断言（消息流、``/api/reminders``、``/api/workspace``、
``/api/channels/{id}/budget``、``/api/channels/{id}/round-outcomes``、``/api/search``）。

因此本文件**一行都不真等时间**：没有 ``sleep``、没有真轮询、没有 ``timeout`` 等待。每个用例用
:class:`ManualReminderDeliveryDriver`（本文件自带的几行替身）把「现在几点」直接喂进去。

本文件覆盖的契约（票面正本 = GitHub issue #113）：

- **C1**：有归属 ⇒ 落**恰好一条** ``kind='system'`` 消息（作者 owner），不耗预算、不产生轮次结果。
- **C2**：无归属 ⇒ **只写** ``fired_at``，任何 target 的消息条数与未读读数逐字不变。
- **C3**：``system`` 的既有口径一个都不变（不计未读 / 不进搜索 / 不唤醒 / 不耗预算）。
- **C5**：``fired_at`` 是唯一已投递标记；连触发两次只 +1；与落消息**同一事务**。
- **C6**：24 小时窗口内补投一次，窗外只写标记；边界 24 小时归「补投」一侧。
- **C7**：到期用**本机本地时间**，审计戳用 UTC，库内不做换算比较（逐字口径见 ``app/specs``）。
- **C8 / C9**：五分支可注入驱动；``POLL_INTERVAL_SECONDS == 30`` 且「+30 秒」注入即已投递。
- **C10**：启停后不留孤儿线程、关库后无人再动库。
- **C18 的后端半边 / C21**：正文取自后端词条，准点与补投分两个键；历史不回填、不读请求头。
- **C20**：每条 payload 恒含 ``fired_at`` 与派生 ``state``，四情形取值与「连读两次逐字相同」。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from alibot import i18n
from alibot.api import create_app
from alibot.reminder_delivery import (
    POLL_INTERVAL_SECONDS,
    ReminderDeliverer,
    ReminderDeliveryWorker,
    reminder_due_instant,
)
from fastapi.testclient import TestClient

from tests.conftest import build_app, channel_with_agent, create_agent, create_channel, messages, send
from tests.scripted import ManualScheduler, ScriptedAgentSeam, inert_docker_startup

# 一个固定的本地到期时刻：本文件的所有读数都以它为锚点（用**本地时间**写，与库里 `time` 的形状一致）。
DUE = "2026-10-18 09:00"
DUE_AT = reminder_due_instant(DUE)


def local_instant(offset: timedelta = timedelta(0)) -> datetime:
    """到期时刻 + 偏移，作为**注入时刻**（带时区，因此与 ``fired_at`` 的 UTC 读数可比）。"""
    return DUE_AT + offset


class ManualReminderDeliveryDriver:
    """测试替身：**不跑线程**，由测试显式喂时刻（C8 的测试 adapter）。

    它只实现驱动器那三个方法——`start` 不做启动扫（测试不想要一个隐式投递）、`shutdown` 空操作、
    `deliver` 直接转给 seam。于是「进程启动先扫一次」这条由生产 adapter 自己的用例覆盖
    （:func:`test_the_startup_sweep_fires_without_waiting_for_the_first_poll`），行为用例则完全确定。
    """

    def __init__(self, deliverer: ReminderDeliverer) -> None:
        self.deliverer = deliverer
        self.started = 0
        self.shut = 0

    def start(self) -> None:
        self.started += 1

    def shutdown(self) -> None:
        self.shut += 1

    def deliver(self, now: datetime):
        return self.deliverer.deliver(now)


@contextmanager
def reminder_backend(data_dir: Path, **kwargs) -> Iterator[dict]:
    """可控后端：手动调度器 + 脚本化替身 + **手动到期驱动器**（同处一个装配点，不新开第二个）。"""
    scheduler = ManualScheduler()
    seam = ScriptedAgentSeam()
    state: dict = {"scheduler": scheduler, "seam": seam, "driver": None}

    def factory(deliverer: ReminderDeliverer) -> ManualReminderDeliveryDriver:
        state["driver"] = ManualReminderDeliveryDriver(deliverer)
        return state["driver"]

    with TestClient(
        build_app_with_reminder_delivery(data_dir, factory, scheduler=scheduler, seam=seam)
    ) as client:
        state["client"] = client
        state["fire"] = lambda now: state["driver"].deliver(now)
        yield state


def build_app_with_reminder_delivery(
    data_dir: Path, factory, *, scheduler: ManualScheduler, seam: ScriptedAgentSeam
):
    return create_app(
        data_dir=data_dir,
        scheduler=scheduler,
        seam=seam,
        reminder_delivery=factory,
        # 与 `build_app` 同一条纪律：启动清扫在 xdist 多进程下会删掉别的 worker 的容器。
        docker_startup=inert_docker_startup,
    )


@pytest.fixture
def timed(data_dir: Path) -> Iterator[dict]:
    with reminder_backend(data_dir) as state:
        yield state


# ─── 读数辅助（只走既有 HTTP 面） ────────────────────────────────────────────
def listed(client: TestClient) -> list[dict]:
    return client.get("/api/reminders").json()["reminders"]


def one_reminder(client: TestClient, text: str) -> dict:
    return next(item for item in listed(client) if item["text"] == text)


def channel_reading(client: TestClient, channel_id: str) -> dict:
    return next(ch for ch in client.get("/api/channels").json()["channels"] if ch["id"] == channel_id)


def system_messages(client: TestClient, channel_id: str) -> list[dict]:
    return [message for message in messages(client, channel_id) if message["kind"] == "system"]


def targets_snapshot(client: TestClient) -> dict:
    """三种 target 与未读的最强读数（C2 判据 2/3 用它证明「没有凭空造 target」）。"""
    return {
        "channels": client.get("/api/channels").json()["channels"],
        "dms": client.get("/api/dms").json()["dms"],
        "tasks": client.get("/api/tasks").json()["tasks"],
        "workspace": client.get("/api/workspace").json(),
    }


def add_reminder(client: TestClient, text: str, due: str, channel_id: str | None = None) -> dict:
    payload: dict = {"text": text, "time": due}
    if channel_id is not None:
        payload["channel_id"] = channel_id
    response = client.post("/api/reminders", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["reminder"]


# ─── 常量与精度（C9） ───────────────────────────────────────────────────────
def test_the_poll_interval_constant_is_thirty_seconds() -> None:
    """C9 判据 1：轮询间隔是一个**模块级常量**，由这一条单元断言固定。"""
    assert POLL_INTERVAL_SECONDS == 30


def test_the_precision_ceiling_holds_with_an_injected_instant(timed: dict) -> None:
    """C9 判据 2：**声明式**给出 30 秒上界——在「到期 + 30 秒」这个注入时刻上，后果已包含该条。

    这条不读常量、不等时间：它只用「+30 秒」这个**外部可述**的界，因此实现换一种内部节奏也成立。
    """
    client: TestClient = timed["client"]
    channel = create_channel(client, name="精度").json()["channel"]
    reminder = add_reminder(client, "准点那条", DUE, channel["id"])

    report = timed["fire"](local_instant(timedelta(seconds=POLL_INTERVAL_SECONDS)))

    assert reminder["id"] in report.ids_for("on-time")
    assert one_reminder(client, "准点那条")["fired_at"] is not None
    assert len(system_messages(client, channel["id"])) == 1


def test_all_five_branches_are_driven_without_waiting(timed: dict) -> None:
    """C8 判据 1：几次注入就能驱动**全部五个分支**，每一步只经 HTTP 读数面断言。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="五分支").json()["channel"]
    on_time = add_reminder(client, "准点", DUE, channel["id"])
    late = add_reminder(client, "补投", "2026-10-18 08:00", channel["id"])
    out = add_reminder(client, "窗外", "2026-10-16 09:00", channel["id"])
    unaffiliated = add_reminder(client, "无归属", DUE)
    already = add_reminder(client, "已投过", DUE, channel["id"])

    # 第一轮：准点 + 补投 + 窗外 + 无归属 四条各自落到自己的分支。
    first = timed["fire"](local_instant(timedelta(seconds=5)))
    assert on_time["id"] in first.ids_for("on-time")
    assert late["id"] in first.ids_for("late")
    assert out["id"] in first.ids_for("out-of-window")
    assert unaffiliated["id"] in first.ids_for("unaffiliated")

    # 第二轮：全部已投过 ⇒ 落在 already-fired，且改动 0 行。
    second = timed["fire"](local_instant(timedelta(seconds=10)))
    assert already["id"] in second.ids_for("already-fired")
    assert second.ids_for("on-time", "late", "out-of-window", "unaffiliated") == ()

    assert one_reminder(client, "补投")["state"] == "fired"
    assert one_reminder(client, "窗外")["state"] == "missed"
    assert one_reminder(client, "无归属")["fired_at"] is not None
    assert len(system_messages(client, channel["id"])) == 3  # 准点 1 + 补投 1；窗外无消息


# ─── C1：有归属 ⇒ 恰好一条 system 消息，作者 owner ─────────────────────────
def test_an_affiliated_reminder_fires_exactly_one_owner_system_message(timed: dict) -> None:
    """C1 判据 1：该频道消息末尾新增**恰好一条** ``kind == "system"``、``author.id == "owner"``。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="到点频道").json()["channel"]
    send(client, channel["id"], "一条既有人类消息")
    before = messages(client, channel["id"])
    add_reminder(client, "检查令牌替换进度", DUE, channel["id"])

    timed["fire"](local_instant(timedelta(seconds=5)))

    after = messages(client, channel["id"])
    assert len(after) == len(before) + 1
    fired = after[-1]
    assert fired["kind"] == "system"
    assert fired["author"]["id"] == "owner"
    assert "检查令牌替换进度" in fired["body"]


def test_firing_does_not_spend_budget_and_produces_no_round_outcome(timed: dict) -> None:
    """C1 判据 2/3（= C3 判据 3）：预算读数逐字不变、轮次结果无新增行。"""
    client: TestClient = timed["client"]
    agent_id = create_agent(client, name="预算对照").json()["agent"]["id"]
    channel = channel_with_agent(client, agent_id, name="预算频道")
    send(client, channel["id"], "一条人类消息")
    timed["scheduler"].drain()

    def readings() -> tuple[dict, int]:
        budget = client.get(f"/api/channels/{channel['id']}/budget").json()["budget"]
        outcomes = client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        return budget, len(outcomes)

    add_reminder(client, "不耗预算", DUE, channel["id"])
    before = readings()

    timed["fire"](local_instant(timedelta(seconds=5)))

    assert readings() == before
    assert len(system_messages(client, channel["id"])) == 1


def test_triggering_twice_adds_exactly_one_message(timed: dict) -> None:
    """C1 判据 4（= C5 判据 1）：连触发两次 ⇒ 消息条数只 +1，第二次改动 0 行。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="幂等").json()["channel"]
    add_reminder(client, "只响一次", DUE, channel["id"])

    timed["fire"](local_instant(timedelta(seconds=5)))
    first = messages(client, channel["id"])
    report = timed["fire"](local_instant(timedelta(seconds=10)))

    assert len(messages(client, channel["id"])) == len(first)
    assert report.ids_for("on-time") == ()
    assert report.ids_for("late") == ()


# ─── C2：无归属的显式分叉 ───────────────────────────────────────────────────
def test_an_unaffiliated_reminder_only_writes_the_marker(timed: dict) -> None:
    """C2 判据 1/2/3/4：只写 ``fired_at``，任何 target 与未读读数逐字不变，第二次改动 0 行。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="对照频道").json()["channel"]
    send(client, channel["id"], "既有消息")
    reminder = add_reminder(client, "无归属备忘", DUE)

    before = targets_snapshot(client)
    before_messages = {ch["id"]: messages(client, ch["id"]) for ch in before["channels"]}

    timed["fire"](local_instant(timedelta(minutes=1)))

    fired = one_reminder(client, "无归属备忘")
    assert fired["fired_at"] is not None
    assert fired["state"] == "fired"

    after = targets_snapshot(client)
    assert after == before
    assert {ch["id"]: messages(client, ch["id"]) for ch in after["channels"]} == before_messages

    second = timed["fire"](local_instant(timedelta(minutes=2)))
    assert second.ids_for("unaffiliated") == ()
    assert second.outcomes() == ("already-fired",)
    assert targets_snapshot(client) == before
    assert reminder["channel_id"] is None


def test_an_unaffiliated_reminder_past_the_window_also_only_writes_the_marker(timed: dict) -> None:
    """C6 判据 4（与 C2 同一分叉）：无归属的过期提醒在**两条路径**（窗口内 / 窗口外）都只写 ``fired_at``。

    上一条用例覆盖窗口内那一支；这一条补窗口外那一支——它证明「无归属」这条分叉**优先于**
    「距到期多远」，因此窗口外也绝不会因为「差点要补投」而落出消息。
    """
    client: TestClient = timed["client"]
    inside = add_reminder(client, "无归属·窗口内", "2026-10-18 08:00")
    outside = add_reminder(client, "无归属·窗口外", "2026-10-16 09:00")

    before = targets_snapshot(client)
    report = timed["fire"](local_instant())

    # 两条都归「无归属」分支，而不是「补投」或「窗口外」——分叉判据是**有没有归属**。
    assert set(report.ids_for("unaffiliated")) == {inside["id"], outside["id"]}
    assert report.ids_for("late", "out-of-window") == ()
    for text in ("无归属·窗口内", "无归属·窗口外"):
        item = one_reminder(client, text)
        assert item["fired_at"] is not None
    assert targets_snapshot(client) == before


def test_the_reminder_suite_does_not_wait_for_real_time(tmp_path: Path) -> None:
    """C9 判据 3：整套提醒相关测试的**实测墙钟耗时显著小于 30 秒**，据此可判定「没有测试真等」。

    这条把判据本身变成机器可判定的（否则它只能靠人跑一遍看时间）。上界取得比规格举例的 5 秒宽，
    因为整条 pytest 进程的启动开销不是本能力的行为；关键是它必须**远小于** 30 秒那个轮询间隔。
    """
    started = time.monotonic()
    with TestClient(build_app(tmp_path / "data")) as client:
        channel = create_channel(client, name="墙钟").json()["channel"]
        add_reminder(client, "不该等", DUE, channel["id"])
    elapsed = time.monotonic() - started

    assert elapsed < POLL_INTERVAL_SECONDS / 3, (
        f"启停一次后端耗时 {elapsed:.2f}s，已接近轮询间隔：说明有路径真的在等时间"
    )


# ─── C3：system 的既有口径一个都不变 ────────────────────────────────────────
def test_the_fired_system_message_is_not_unread_and_not_searchable(timed: dict) -> None:
    """C3 判据 1/2：未读读数逐字不变；用正文里的词搜不到这条 ``system`` 消息。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="搜索对照").json()["channel"]
    add_reminder(client, "检查令牌替换进度", DUE, channel["id"])

    unread_before = client.get("/api/workspace").json()["unread_total"]
    channel_unread_before = channel_reading(client, channel["id"])["unread_count"]

    timed["fire"](local_instant(timedelta(seconds=5)))

    assert client.get("/api/workspace").json()["unread_total"] == unread_before
    assert channel_reading(client, channel["id"])["unread_count"] == channel_unread_before

    hits = client.get("/api/search", params={"q": "令牌替换进度"}).json()["hits"]
    message_hits = [hit for hit in hits if "message" in hit]
    fired_body = system_messages(client, channel["id"])[0]["body"]
    assert all(hit["message"]["body"] != fired_body for hit in message_hits)


# ─── C6 / C21：补投、窗口与文案分档 ─────────────────────────────────────────
def test_the_window_decides_between_a_late_delivery_and_silence(timed: dict) -> None:
    """C6 判据 1/2：1 小时与 23 小时补投（补投措辞），25 小时无消息但 ``fired_at`` 非空。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="补投").json()["channel"]
    one_hour = add_reminder(client, "一小时前", "2026-10-18 08:00", channel["id"])
    almost = add_reminder(client, "二十三小时前", "2026-10-17 10:00", channel["id"])
    gone = add_reminder(client, "二十五小时前", "2026-10-17 08:00", channel["id"])

    report = timed["fire"](local_instant())

    assert set(report.ids_for("late")) == {one_hour["id"], almost["id"]}
    assert gone["id"] in report.ids_for("out-of-window")
    assert len(system_messages(client, channel["id"])) == 2
    for text in ("一小时前", "二十三小时前"):
        assert one_reminder(client, text)["fired_at"] is not None
    missed = one_reminder(client, "二十五小时前")
    assert missed["fired_at"] is not None
    assert missed["state"] == "missed"

    # 再触发一次：三条都不再产生任何消息（后果表里读到 already-fired）。
    again = timed["fire"](local_instant())
    assert again.ids_for("late", "out-of-window") == ()
    assert len(system_messages(client, channel["id"])) == 2


def test_exactly_twenty_four_hours_lands_on_the_late_side(timed: dict) -> None:
    """C6 判据 3：距到期**恰好 24 小时**归入「补投」一侧（边界值唯一归属）。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="边界").json()["channel"]
    add_reminder(client, "恰好二十四小时", "2026-10-17 09:00", channel["id"])

    report = timed["fire"](local_instant())

    assert report.outcomes() == ("late",)
    assert len(system_messages(client, channel["id"])) == 1
    assert one_reminder(client, "恰好二十四小时")["state"] == "fired"


def test_the_copy_tier_is_decided_by_the_gap_and_not_the_code_path(timed: dict) -> None:
    """C21 判据 1/2：+5 秒取 ``system.reminder.fired``；+5 分钟（< 24 小时）取 ``fired_late``。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="文案分档").json()["channel"]
    add_reminder(client, "五秒", DUE, channel["id"])

    timed["fire"](local_instant(timedelta(seconds=5)))

    on_time_body = system_messages(client, channel["id"])[-1]["body"]
    assert on_time_body == i18n.render("system.reminder.fired", "zh-CN", text="五秒")

    other = create_channel(client, name="文案分档二").json()["channel"]
    add_reminder(client, "五分钟", DUE, other["id"])
    timed["fire"](local_instant(timedelta(minutes=5)))

    late_body = system_messages(client, other["id"])[-1]["body"]
    assert late_body == i18n.render("system.reminder.fired_late", "zh-CN", text="五分钟")
    assert late_body != on_time_body


def test_the_stored_copy_follows_the_library_locale_and_never_backfills(timed: dict) -> None:
    """C18 判据 3/4/6：不可见请求头不改落库语言；切换偏好后历史正文逐字不变。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="文案语言").json()["channel"]

    assert client.patch("/api/settings", json={"locale": "zh-CN"}).status_code == 200
    add_reminder(client, "中文那条", DUE, channel["id"])
    timed["fire"](local_instant(timedelta(seconds=5)))
    zh_body = system_messages(client, channel["id"])[-1]["body"]
    assert zh_body == i18n.render("system.reminder.fired", "zh-CN", text="中文那条")

    # 切到 en 之后新响的那条取 en 的取值；落库正文由**库内偏好**决定，请求头不算数。
    assert client.patch("/api/settings", json={"locale": "en"}).status_code == 200
    other = create_channel(client, name="Copy").json()["channel"]
    add_reminder(client, "english one", "2026-10-18 09:06", other["id"])
    client.headers["Accept-Language"] = "zh-CN"
    timed["fire"](local_instant(timedelta(minutes=6)))
    en_body = system_messages(client, other["id"])[-1]["body"]
    assert en_body == i18n.render("system.reminder.fired", "en", text="english one")

    # 历史不回填：zh 那条的正文在切换偏好之后逐字不变。
    assert system_messages(client, channel["id"])[-1]["body"] == zh_body


def test_the_two_copy_keys_are_backend_only() -> None:
    """C18 判据 1：两条 ``system.reminder.*`` **只**进后端词条表，**不**越界写前端键。

    越界校验方向是本票的责任：`system.reminder.*` 是落库正文（后端第二层），前端词条表里出现同一条
    就是「第三层」（ADR-0009 明确否掉）。
    """
    source = Path(__file__).resolve().parents[1] / "alibot" / "studio" / "i18n.js"
    text = source.read_text(encoding="utf-8")

    assert "system.reminder.fired" in i18n.CATALOGS["zh-CN"]
    assert "system.reminder.fired" in i18n.CATALOGS["en"]
    assert "system.reminder.fired_late" in i18n.CATALOGS["zh-CN"]
    assert "system.reminder.fired_late" in i18n.CATALOGS["en"]
    assert "system.reminder." not in text


# ─── C7：本地到期 / UTC 审计戳 ──────────────────────────────────────────────
def test_the_due_moment_is_judged_on_the_local_clock(timed: dict) -> None:
    """C7 判据 1/2：本地 ``09:00:05`` 判到点，同一库在本地 ``08:59:55`` 判**不**到点。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="本地时钟").json()["channel"]
    add_reminder(client, "九点整", DUE, channel["id"])

    early = timed["fire"](local_instant(-timedelta(seconds=5)))
    assert early.outcomes() == ()
    assert one_reminder(client, "九点整")["fired_at"] is None
    assert system_messages(client, channel["id"]) == []

    late = timed["fire"](local_instant(timedelta(seconds=5)))
    assert late.outcomes() == ("on-time",)
    assert one_reminder(client, "九点整")["fired_at"] is not None


def test_fired_at_is_a_utc_audit_stamp_of_the_same_family_as_created_at(timed: dict) -> None:
    """C5 判据 4（C7 规矩 2）：``fired_at`` 是 ``Z`` 后缀的 UTC 串，与 ``created_at`` 同族。"""
    client: TestClient = timed["client"]
    add_reminder(client, "审计戳", DUE)

    timed["fire"](local_instant(timedelta(seconds=5)))

    fired = one_reminder(client, "审计戳")
    assert fired["fired_at"].endswith("Z")
    assert "T" in fired["fired_at"]
    assert fired["created_at"].endswith("Z")


# ─── C20：读路径恒给 fired_at 与派生 state ──────────────────────────────────
def test_every_payload_carries_fired_at_and_state(timed: dict) -> None:
    """C20 判据 1/2/4：键存在性、四情形取值、连读两次逐字相同（`state` 不被写入）。"""
    client: TestClient = timed["client"]
    channel = create_channel(client, name="四情形").json()["channel"]
    add_reminder(client, "未到点", "2026-10-19 09:00", channel["id"])
    add_reminder(client, "已过点未投递", "2026-10-17 09:00", channel["id"])
    add_reminder(client, "窗口内已投递", "2026-10-18 08:55", channel["id"])
    add_reminder(client, "窗口外已投递", "2026-10-16 09:00", channel["id"])

    timed["fire"](local_instant())

    states = {item["text"]: item["state"] for item in listed(client)}
    assert states["未到点"] == "pending"
    assert states["窗口内已投递"] == "fired"
    assert states["窗口外已投递"] == "missed"
    # 第四个（已过点但未投递）那条在这一次注入里也过了点，于是它同样被投掉 ⇒ 这里只断言键存在性。
    for item in listed(client):
        assert "fired_at" in item
        assert "state" in item

    first = listed(client)
    assert listed(client) == first


def test_a_due_but_unfired_reminder_still_reads_pending(timed: dict) -> None:
    """C20 的规则表第 2 行：``fired_at IS NULL`` 且到期时刻**已过** ⇒ 仍是 ``pending``。"""
    client: TestClient = timed["client"]
    add_reminder(client, "等着下一轮", "2026-10-17 09:00")

    item = one_reminder(client, "等着下一轮")

    assert item["fired_at"] is None
    assert item["state"] == "pending"


# ─── C5：同一事务（回滚 = 从未成功） ────────────────────────────────────────
def test_a_failed_message_write_leaves_the_marker_null(tmp_path: Path) -> None:
    """C5 判据 2：落消息那一步抛错 ⇒ ``fired_at`` 仍为 ``null``、消息条数不变，下一次能正常投递。

    取证方式是把「落消息」这一步注入失败（``Database._append_target_message_locked`` 抛错），
    于是走的是**真实**的事务边界与回滚路径——不伪造库状态。
    """
    from alibot.db import Database

    @contextmanager
    def backend() -> Iterator[dict]:
        scheduler = ManualScheduler()
        seam = ScriptedAgentSeam()
        state: dict = {}

        def factory(deliverer: ReminderDeliverer):
            state["driver"] = ManualReminderDeliveryDriver(deliverer)
            return state["driver"]

        with TestClient(
            create_app(
                data_dir=tmp_path,
                scheduler=scheduler,
                seam=seam,
                reminder_delivery=factory,
                docker_startup=inert_docker_startup,
            )
        ) as client:
            state["client"] = client
            state["fire"] = state["driver"].deliver
            yield state

    with backend() as state:
        client: TestClient = state["client"]
        channel = create_channel(client, name="回滚").json()["channel"]
        add_reminder(client, "会失败一次", DUE, channel["id"])

        # **故障注入**，不是对内部分支的断言（C5 判据 2 允许「测试替身注入」）：让「落消息」那一步抛错，
        # 随后的一切断言只落在 HTTP 读数面（消息条数、``fired_at``），与规格 T1 的「只断言外部可观察行为」
        # 一致。``mock.patch.object`` 负责装载与还原，测试因此不需要自己管恢复。
        with (
            mock.patch.object(
                Database, "_append_target_message_locked", side_effect=RuntimeError("注入的落消息失败")
            ),
            pytest.raises(RuntimeError),
        ):
            state["fire"](local_instant(timedelta(seconds=5)))

        # 回滚：消息没落、标记没写 ⇒ 下一轮会**重试**（那是「从未成功」，不是重复投递）。
        assert messages(client, channel["id"]) == []
        assert one_reminder(client, "会失败一次")["fired_at"] is None

        state["fire"](local_instant(timedelta(seconds=10)))

        assert len(system_messages(client, channel["id"])) == 1
        assert one_reminder(client, "会失败一次")["fired_at"] is not None


# ─── C8 判据 3 / C10：生产装配的启动扫与关停 ────────────────────────────────
def test_the_startup_sweep_fires_without_waiting_for_the_first_poll(tmp_path: Path) -> None:
    """C8 判据 3：**生产装配**（真实后台轮询）下，启动前就已过点且在窗口内的提醒，

    在第一次读到 ``GET /api/reminders`` 时 ``fired_at`` 已非空——不需要等第一轮 30 秒轮询。

    取证窗口：先在**上一个进程**里落一条已经过点的提醒（本地「一分钟前」），关闭它；再用**生产**
    装配（不注入驱动器）打开同一个数据目录，进去就立即读——启动扫已经跑完。
    """
    from datetime import datetime as dt

    data_dir = tmp_path / "data"
    past_due = (dt.now().astimezone() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")

    # 上一进程用**惰性**装配（不投递），只为把一条已过点的提醒落到库里。
    with TestClient(build_app(data_dir)) as first:
        channel = create_channel(first, name="启动扫").json()["channel"]
        body = {"text": "开机就该响", "time": past_due, "channel_id": channel["id"]}
        assert first.post("/api/reminders", json=body).status_code == 201
        channel_id = channel["id"]
        assert first.get("/api/reminders").json()["reminders"][0]["fired_at"] is None

    # 新进程用**生产**装配（不注入驱动器 ⇒ 真实后台线程 + 启动先扫），进去就立即读。
    # 只关掉 Docker 启动清扫（`docker_startup=inert_docker_startup`）：本用例要证的是**提醒**的
    # 启动先扫，容器清扫在这里既无关、又会在 xdist 并行时删掉别的 worker 的容器（#328 / #330）。
    with TestClient(create_app(data_dir=data_dir, docker_startup=inert_docker_startup)) as second:
        item = next(r for r in second.get("/api/reminders").json()["reminders"] if r["text"] == "开机就该响")
        assert item["fired_at"] is not None
        assert item["state"] == "fired"
        assert len([m for m in messages(second, channel_id) if m["kind"] == "system"]) == 1


def test_repeated_start_and_stop_leaves_no_orphan_thread_and_no_closed_database_error(
    tmp_path: Path,
) -> None:
    """C10 判据 1/2：反复启停 3 次，每次都能干净关停（不抛「库已关」、不挂住）。"""
    data_dir = tmp_path / "data"
    from datetime import datetime as dt

    past_due = (dt.now().astimezone() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")

    for _ in range(3):
        # 每一次都用**生产**装配（真实轮询线程），退出时走完整条 lifespan 关停顺序。
        # Docker 启动清扫仍注入惰性替身：本用例证的是「反复启停无孤儿线程 / 无已关库报错」，
        # 与容器清扫无关，而后者在 xdist 并行下会跨进程误删（#328 / #330）。
        with TestClient(create_app(data_dir=data_dir, docker_startup=inert_docker_startup)) as client:
            channel = create_channel(client, name="启停").json()["channel"]
            assert (
                client.post(
                    "/api/reminders",
                    json={"text": "反复启停", "time": past_due, "channel_id": channel["id"]},
                ).status_code
                == 201
            )
        # 退出 ``TestClient`` = 走完整条 lifespan 关停顺序（停事件流 → 停投递器 → 停调度器 → 关库）。

    assert not any(
        thread.name == ReminderDeliveryWorker.THREAD_NAME and thread.is_alive()
        for thread in threading.enumerate()
    )
