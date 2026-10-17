"""提醒的到点形态（实施票 ① / issue #112）：`SCHEMA_VERSION` 13 + `MigrationStep(12, 13)`。

接缝沿用规格 T2 的**唯一测试接缝**：真实子进程 ``python -m alibot --migrate --data-dir <目录>``，
配磁盘上的真实数据目录 fixture；断言只落在**外部可观察面**——退出码、stdout 报告、库的终态与
列清单、``GET /api/reminders`` 的读数（规格 T1：不断言私有函数、SQL 文本、注册表内部表示）。

本文件覆盖的契约（票面正本 = GitHub issue #112）：

- **C4**：``reminder.time`` 的形状是 ``YYYY-MM-DD HH:MM``（一次性）；旧形状与重复 / 日期键被拒。
- **C5**（本票那一半）：``fired_at`` 列存在、可空、迁移后为 ``NULL``；**不写**任何投递逻辑。
- **C11**：号 = 13；链上登记了 ``12 → 13``；链连续性守卫覆盖它；迁移后只多一列。
- **C12**：存量 ``HH:MM`` 统一解释成「**迁移执行当日**」的该钟点，且**不依赖执行时刻**。
- **C13**：时间语义的**一次性收窄**必须能从 stdout 报告里读到。
- **C14**：复用既有 ``changed_rows`` 形状给出被改写行数（**不新增报告字段**）。

C12 判据 2（「口径不依赖执行时刻」）的取证方式：两次迁移在**不同时区的同一时刻**跑，且其中一次
用 ``libfaketime`` 把进程时钟挪到另一个钟点。没有 ``libfaketime`` 的机器上那一条**跳过**而不是
伪造——「拿不到证据就承认拿不到」比一条空转的绿更诚实，而判据 1（已知执行日）在任何机器上都跑。
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alibot.db import DATABASE_FILE_NAME, SCHEMA_VERSION

# v12 形态库的搭建逻辑很重（历史对象 + 补列 + 灌数据），复用既有迁移测试的 fixture builder——
# 与 ``test_migration_chain_guard.py`` / ``test_migrate_dry_run.py`` 同一体例：只借它的**文件级
# 外部面**（``_build_library`` 造磁盘上的真实库、``_migrate_argv`` 拼命令行、``_sha256`` 取读数），
# 不改那个文件。真跑子进程的 runner 由本文件自带（它要钉 ``TZ``，见 :data:`FIXED_TZ`）。
from tests.test_schema_migration import _build_library, _migrate_argv, _sha256

APP_DIR = Path(__file__).resolve().parents[1]

# 迁移入口的对外契约码（与既有迁移测试同体例：写**字面量**而不是 import 实现常量）。
MIGRATION_SUCCEEDED = 0

# 本票的号段：票面与 ADR-0017 §五都把它钉成 13（判据是「必须高于当前 main 的最小号」）。
REMINDER_SCHEMA_VERSION = 13
REMINDER_STEP_FROM = 12
REMINDER_STEP_TO = 13

# 报告里的版本步骤行（缩进两格；报告头部那行 ``源版本：… → 目标版本：…`` 没有缩进，不会命中）。
STEP_LINE_RE = re.compile(r"^ {2}(\d+) → (\d+) {2}(.*)$", re.MULTILINE)
# 那一行末尾的「改动 N 行」。
STEP_ROWS_RE = re.compile(r"改动 (\d+) 行$")

# 迁移前的 `reminder` 五列（票面 C11 判据 4 的对照基线：迁移后 = 这五列 + `fired_at`）。
LEGACY_REMINDER_COLUMNS = ("id", "text", "time", "channel_id", "created_at")
NEW_REMINDER_COLUMNS = (*LEGACY_REMINDER_COLUMNS, "fired_at")

# 「时间语义一次性收窄」的报告读数：判据是**能读到那句话**，不是逐字比一份渲染细节之外的东西。
# 三个词分开断言（同一个正则会把中文标点的写法锁死，而那句话的措辞允许微调）。
NARROWING_MARKERS = ("一次性收窄", "每天", "迁移执行当日")


# ─── 库的读取辅助（只读；不写、不初始化） ────────────────────────────────────
def _read_only(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _version(db_path: Path) -> str:
    conn = _read_only(db_path)
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return str(row[0])
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> tuple[str, ...]:
    """表的列清单（按 DDL 顺序；判据是「只多一列」这个**集合 + 顺序**事实）。"""
    conn = _read_only(db_path)
    try:
        return tuple(str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")'))
    finally:
        conn.close()


def _reminder_rows(db_path: Path) -> list[tuple]:
    conn = _read_only(db_path)
    try:
        return [
            tuple(row) for row in conn.execute("SELECT id, text, time, fired_at FROM reminder ORDER BY rowid")
        ]
    finally:
        conn.close()


def _build_v12_library(data_dir: Path) -> Path:
    """造一个**真 v12 main 形态**的库：``reminder`` 五列（无 ``fired_at``）、版本读数钉成 12。

    为什么不用历史抽取留档里的 ``VERSION_OBJECTS[12]``：那份表是**机器抽取**的「该版出现过哪些对象」，
    而 v12 的抽取树里没有 ``reminder``（``test_schema_migration.py`` 已把这类「读数 ↔ 结构」歧义登记
    为「由终态判据吸收」）。本票要验的恰是**带 ``reminder`` 的那棵树**上的 ``12 → 13`` 改写，因此借
    既有 builder 造 **v11 形态**（v11 已有 ``reminder``、列形态与本票迁移前一致），再把版本读数钉成
    12——这正是 v12 那一代真实的 `reminder` 形态。

    灌存量行发生在**钉版本之前**，于是它们走的是「迁移改写」而不是「写路径校验」。
    """
    db_path = _build_library(data_dir, REMINDER_STEP_FROM - 1)
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(REMINDER_STEP_FROM),)
        )
    finally:
        conn.close()
    return db_path


def _seed_legacy_reminders(db_path: Path, times: tuple[str, ...]) -> None:
    """往**v12 库**的 ``reminder`` 表里灌存量行（旧形状 ``HH:MM``，``fired_at`` 列还不存在）。"""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        for index, legacy_time in enumerate(times):
            conn.execute(
                "INSERT INTO reminder(id, text, time, channel_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    f"rm_legacy_{index}",
                    f"存量提醒 {index}",
                    legacy_time,
                    None,
                    "2026-10-14T09:00:00Z",
                ),
            )
    finally:
        conn.close()


def _step_line(report: str) -> tuple[int, int, str]:
    """报告里 ``12 → 13`` 那一整行（``from``、``to``、正文）；没有就断言失败。"""
    for match in STEP_LINE_RE.finditer(report):
        if (int(match.group(1)), int(match.group(2))) == (REMINDER_STEP_FROM, REMINDER_STEP_TO):
            return int(match.group(1)), int(match.group(2)), match.group(3)
    raise AssertionError(f"报告的步骤表里没有 {REMINDER_STEP_FROM} → {REMINDER_STEP_TO} 这一行：\n{report}")


def _changed_rows(report: str) -> int:
    """``12 → 13`` 那行报出的「改动 N 行」（C14：复用既有 ``changed_rows`` 形状）。"""
    _, _, body = _step_line(report)
    match = STEP_ROWS_RE.search(body)
    assert match is not None, f"`12 → 13` 那行没有打出行数：{body!r}"
    return int(match.group(1))


def _fixed_tz_today() -> str:
    """**固定 TZ** 下的当日（``YYYY-MM-DD ``）——「迁移执行当日」这个口径的正本读数。"""
    return _fixed_tz_now().strftime("%Y-%m-%d ")


# 本文件所有**真跑**迁移的子进程都钉住同一个时区。理由：「到期判定用**本机本地时间**；``created_at``
# 仍是 **UTC 审计时间戳**」这条口径（规格 C7）与「迁移执行当日」（C12）都只在本机 TZ 下可判定，
# 而一个**用 UTC 当日**的错误实现只有在「本地日 ≠ UTC 日」的窗口里才会红。钉一个与 UTC 恒有 8 小时
# 偏移的 TZ，于是这条判据在**每一次**运行里都成立，不靠当天恰好在窗口内。
FIXED_TZ = "UTC-8"


def _run_at_fixed_tz(*args: str) -> subprocess.CompletedProcess[str]:
    """在**固定时区**下真跑 ``python -m alibot``（其余环境与 ``_run`` 逐字一致）。"""
    return subprocess.run(
        [sys.executable, "-m", "alibot", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin", "TZ": FIXED_TZ},
        cwd=str(APP_DIR),
    )


def _fixed_tz_now() -> datetime:
    """**固定 TZ** 下的当前时刻（``TZ=UTC-8`` 的 POSIX 语义 = UTC+8）。"""
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _utc_today() -> str:
    """**UTC** 当日（``YYYY-MM-DD ``）——用来证明迁移取的不是这个读数。"""
    return datetime.now(UTC).strftime("%Y-%m-%d ")


# ─── C11 · 号 = 13，且链上登记了 12 → 13 ────────────────────────────────────
def test_schema_version_is_thirteen_and_the_step_is_registered() -> None:
    """C11：``SCHEMA_VERSION`` = 13（字面量钉死，防「两侧同步漂移」）。"""
    assert SCHEMA_VERSION == REMINDER_SCHEMA_VERSION, (
        f"SCHEMA_VERSION 应是 {REMINDER_SCHEMA_VERSION}（ADR-0017 §五 的最小号判据），实际 {SCHEMA_VERSION}"
    )


def test_a_v12_library_migrates_to_thirteen_and_reports_the_new_step(tmp_path: Path) -> None:
    """C11 判据 1 + 2：v12 库 ``--migrate`` ⇒ 退出 0、``schema_version`` = 13、报告含 ``12 → 13``。

    报告的步骤表由**真实子进程**给出，因此这条同时是「链连续性守卫覆盖了这一步」的读数：删掉
    ``MigrationStep(12, 13)`` 会让报告的步骤表止于 12，``_step_line`` 当场断言失败。
    """
    data_dir = tmp_path / "data"
    _build_v12_library(data_dir)

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _version(data_dir / DATABASE_FILE_NAME) == str(REMINDER_SCHEMA_VERSION)
    assert _step_line(completed.stdout)[:2] == (REMINDER_STEP_FROM, REMINDER_STEP_TO)


def test_the_report_ends_at_the_schema_version(tmp_path: Path) -> None:
    """C11 判据 2/3：报告的步骤表**终点覆盖** ``SCHEMA_VERSION``（链没有断在 12）。"""
    data_dir = tmp_path / "data"
    _build_v12_library(data_dir)

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    steps = [(int(a), int(b)) for a, b, _ in STEP_LINE_RE.findall(completed.stdout)]
    assert steps[-1][1] == SCHEMA_VERSION, f"步骤表止于 {steps[-1][1]}，没覆盖到 {SCHEMA_VERSION}"
    # 每一步严格 +1 且彼此衔接（与 test_migration_chain_guard.py 同一份判据，此处从 v12 起）。
    for (_, previous_to), (current_from, current_to) in pairwise(steps):
        assert current_from == previous_to, f"步骤表不连续：{previous_to} 之后接 {current_from}"
        assert current_to == current_from + 1


def test_the_migrated_reminder_table_has_exactly_one_new_column(tmp_path: Path) -> None:
    """C11 判据 4 + C5 判据 1：列清单 = 迁移前五列 + ``fired_at``，**没有**第三个新列。

    尤其没有 ``state`` / ``due_date`` / 时区列：D4 否掉的是「第三个**状态列**」，派生值不是列。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    assert _columns(db_path, "reminder") == LEGACY_REMINDER_COLUMNS, "v12 库本就不该有 fired_at"

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    assert _columns(data_dir / DATABASE_FILE_NAME, "reminder") == NEW_REMINDER_COLUMNS


# ─── C5（本票那一半）· fired_at 存在、可空、迁移后为 NULL ────────────────────
def test_every_migrated_reminder_has_a_null_fired_at(tmp_path: Path) -> None:
    """C5 / C12 判据 1：迁移后存量行的 ``fired_at`` 全是 ``NULL``（本票**不写**投递逻辑）。"""
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00", "14:30"))

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    for row in _reminder_rows(data_dir / DATABASE_FILE_NAME):
        assert row[3] is None, f"迁移不得预写 fired_at：{row!r}"


# ─── C12 · 存量 HH:MM 解释成「迁移执行当日」的该钟点 ────────────────────────
def test_legacy_times_get_the_migration_day_as_their_date(tmp_path: Path) -> None:
    """C12 判据 1：``09:00`` 的存量行 ⇒ ``<迁移执行当日> 09:00``，``fired_at`` 仍 ``null``。

    逐字断言「当日」读数的来源是**本测试进程自己的本机本地日期**：真正的取值由迁移那个子进程算，
    两侧都是本机本地时区，因此这条同时证了「口径是本机本地、不是 UTC」。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00",))

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    rows = _reminder_rows(data_dir / DATABASE_FILE_NAME)
    assert [row[2] for row in rows] == [f"{_fixed_tz_today()}09:00"]
    assert rows[0][3] is None


def test_every_legacy_row_shares_the_same_date_and_keeps_its_own_time(tmp_path: Path) -> None:
    """C12 判据 2：N 条不同钟点 ⇒ 全部**同一日期** + 各自原钟点（没有任何一行的日期不同）。"""
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    legacy_times = ("00:05", "07:30", "12:00", "18:45", "23:59")
    _seed_legacy_reminders(db_path, legacy_times)

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    rewritten = [row[2] for row in _reminder_rows(data_dir / DATABASE_FILE_NAME)]
    dates = {value[:10] for value in rewritten}
    assert len(dates) == 1, f"存量行被改写成多个日期：{sorted(dates)}"
    assert [value[11:] for value in rewritten] == list(legacy_times), "各自的原钟点必须原样保留"


def _libfaketime_library() -> str | None:
    """``libfaketime`` 的动态库路径；本机没装 ⇒ ``None``（那条用例会跳过，不伪造证据）。"""
    for candidate in (
        "/usr/local/lib/faketime/libfaketime.dylib",
        "/opt/homebrew/lib/faketime/libfaketime.dylib",
        "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1",
        "/usr/lib/aarch64-linux-gnu/faketime/libfaketime.so.1",
    ):
        if Path(candidate).exists():
            return candidate
    return None


@pytest.mark.skipif(
    _libfaketime_library() is None,
    reason="本机没有 libfaketime：无法把子进程时钟挪到另一个钟点，因此取不到「口径不依赖执行时刻」的证据",
)
def test_the_date_does_not_depend_on_when_the_migration_runs(tmp_path: Path) -> None:
    """C12 判据 2 的强形式：**同一天的不同钟点**迁两次 ⇒ 改写出来的日期逐字相同。

    「迁移执行当日」与「下一个该钟点」的分野恰好在这里：后者的结果会随执行时刻变（同一份备份
    08:00 迁与 16:00 迁得到两个不同的到期日），前者不变。这条用 ``libfaketime`` 把子进程时钟挪到
    同一天的另一个钟点，因此它验的是**口径**而不是「跑得够快」。
    """
    faketime = _libfaketime_library()
    assert faketime is not None  # skipif 已保证

    dates: list[str] = []
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    for index, fake_time in enumerate((f"{today} 00:30:00", f"{today} 22:45:00")):
        data_dir = tmp_path / f"data-{index}"
        db_path = _build_v12_library(data_dir)
        _seed_legacy_reminders(db_path, ("09:00",))
        env = {
            "PYTHONPATH": str(APP_DIR),
            "PATH": "/usr/bin:/bin",
            "DYLD_FORCE_FLAT_NAMESPACE": "1",
            "DYLD_INSERT_LIBRARIES": faketime,
            "LD_PRELOAD": faketime,
            "FAKETIME": fake_time,
        }
        completed = subprocess.run(
            [sys.executable, "-m", "alibot", *_migrate_argv(data_dir)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(APP_DIR),
        )
        assert completed.returncode == MIGRATION_SUCCEEDED, (
            f"FAKETIME={fake_time} 下迁移失败：{completed.stderr!r}"
        )
        dates.append(_reminder_rows(data_dir / DATABASE_FILE_NAME)[0][2][:10])

    assert dates[0] == dates[1] == today, (
        f"改写出来的日期跟着执行时刻变了：{dates}——那正是「下一个该钟点」口径的症状"
    )


def test_rows_already_in_the_new_shape_are_not_rewritten(tmp_path: Path) -> None:
    """C12 判据 3：已是新形状的行**不被改写**（幂等）。

    取证方式：直接给 v12 库灌一条**已是** ``YYYY-MM-DD HH:MM`` 的行（形状对了、只是当时还没这一列），
    迁移后它的值必须逐字不变，而同一批里的旧形状行被补上日期。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00",))
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute(
            "INSERT INTO reminder(id, text, time, channel_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("rm_new_shape", "已新形状", "2026-12-31 23:59", None, "2026-10-14T09:00:00Z"),
        )
    finally:
        conn.close()

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    times = {row[0]: row[2] for row in _reminder_rows(data_dir / DATABASE_FILE_NAME)}
    assert times["rm_new_shape"] == "2026-12-31 23:59", "已是新形状的行不得被二次改写"
    assert times["rm_legacy_0"] == f"{_fixed_tz_today()}09:00"


# ─── C12 · 口径是**本机本地**日期，不是 UTC 日期 ─────────────────────────────
def test_the_rewritten_date_is_the_local_day_and_not_the_utc_day(tmp_path: Path) -> None:
    """C12 + C7：迁移补的日期取**本机本地**当日，**不是** UTC 当日。

    为什么这条判据在**任何时刻**都成立：偏移是**按当前 UTC 小时动态挑的**——UTC 小时 < 12 时取
    ``UTC+12``（本地 = UTC−12，落在**前一天**），否则取 ``UTC-12``（本地 = UTC+12，落在**下一天**）。
    12 小时的整体位移必然跨过一个日历日界线，于是「本地日 ≠ UTC 日」这条前提在每次运行里都为真（本
    用例先自查它），一个误用 ``datetime.now(UTC)`` 取日期的实现**每一次**都会红，不必等某一天恰好
    落在窗口里。

    本文件其它的真跑用例用固定的 ``TZ="UTC-8"``（见 :data:`FIXED_TZ`）：它们断言的是「改写结果等于
    本地当日」这件事本身，而这条用例负责把「本地 vs UTC」的分野**变成必然可判**。
    """
    utc_now = datetime.now(UTC)
    # POSIX 的 ``TZ`` 把**东正西负**反过来写：``UTC+12`` = 本地比 UTC **晚** 12 小时（落在前一天），
    # ``UTC-12`` = 本地比 UTC **早** 12 小时（落在下一天）。挑那一个必然跨过日界线的一侧。
    behind = utc_now.hour < 12
    tz = "UTC+12" if behind else "UTC-12"
    local_now = utc_now + timedelta(hours=-12 if behind else 12)
    local_day = local_now.strftime("%Y-%m-%d ")
    utc_day = utc_now.strftime("%Y-%m-%d ")
    assert local_day != utc_day, f"前提失效：本地日与 UTC 日相同（{local_day!r}）——这条用例失去区分力"

    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00",))

    completed = subprocess.run(
        [sys.executable, "-m", "alibot", *_migrate_argv(data_dir)],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin", "TZ": tz},
        cwd=str(APP_DIR),
    )

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    written = _reminder_rows(data_dir / DATABASE_FILE_NAME)[0][2]
    assert written == f"{local_day}09:00", (
        f"迁移补的日期不是本地当日（TZ={tz}）：写出 {written!r}，本地日={local_day!r}，UTC 日={utc_day!r}"
    )


# ─── C13 · 一次性收窄必须能从迁移报告里读到 ────────────────────────────────
def test_the_report_states_the_one_time_semantic_narrowing(tmp_path: Path) -> None:
    """C13 判据 1：stdout 报告里能读到「每天该钟点 → 迁移执行当日该钟点」这句收窄说明。

    判据**不是**「步数统计」：只统计行数的报告会让读者以为这是一次无害的形变，而它改变了行的
    含义。这句必须能直接读到，不需要去翻源码。
    """
    data_dir = tmp_path / "data"
    _build_v12_library(data_dir)

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    report = completed.stdout
    for marker in NARROWING_MARKERS:
        assert marker in report, f"报告里读不到收窄说明的「{marker}」：\n{report}"


# ─── C14 · 被改写行数复用 changed_rows（不新增报告字段） ─────────────────────
def test_the_step_reports_three_changed_rows_for_three_legacy_rows(tmp_path: Path) -> None:
    """C14 判据 1：N = 3 条存量 ``HH:MM`` 行 ⇒ ``12 → 13`` 那行「改动 3 行」逐字可读。"""
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00", "14:30", "20:00"))

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert _changed_rows(completed.stdout) == 3, completed.stdout


def test_the_step_reports_zero_changed_rows_for_an_empty_reminder_table(tmp_path: Path) -> None:
    """C14 判据 2：空提醒表 ⇒ 那行显示「改动 0 行」（不改写任何东西就不报数）。"""
    data_dir = tmp_path / "data"
    _build_v12_library(data_dir)

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert _changed_rows(completed.stdout) == 0, completed.stdout


def test_only_the_legacy_shaped_rows_count_towards_the_rewritten_total(tmp_path: Path) -> None:
    """C14 判据 1 的对照读数：三条旧形状 + 一条新形状 ⇒ 那行报 **3**，不是 4（幂等不改写）。"""
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("08:00", "12:00", "16:00"))
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute(
            "INSERT INTO reminder(id, text, time, channel_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("rm_new_shape", "已新形状", "2027-01-01 07:00", None, "2026-10-14T09:00:00Z"),
        )
    finally:
        conn.close()

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert _changed_rows(completed.stdout) == 3, completed.stdout


def test_the_dry_run_reports_the_same_rewrite_count_without_touching_the_library(
    tmp_path: Path,
) -> None:
    """C14 的预演读数：预演与真跑**共用同一份**步骤表渲染，因此行数相同且原库不动。

    这条同时钉住「报告形状不新增字段」：预演报告与真跑报告都是同一组 `from → to + 改动 N 行`。
    """
    data_dir = tmp_path / "dry-data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00", "13:00"))
    digest_before = _sha256(db_path)

    dry = _run_at_fixed_tz("--migrate", "--dry-run", "--data-dir", str(data_dir))

    assert dry.returncode == MIGRATION_SUCCEEDED, dry.stderr
    assert _changed_rows(dry.stdout) == 2, dry.stdout
    assert _sha256(db_path) == digest_before, "预演不得改动原库"


# ─── C4 · 写路径：新形状受理、旧形状与不该做的键被拒 ────────────────────────
def test_a_new_shape_time_round_trips_verbatim(client) -> None:
    """C4 判据 1：``time = "2026-10-18 09:00"`` ⇒ 201，且回读**逐字等于**传入值。"""
    created = client.post("/api/reminders", json={"text": "检查令牌替换进度", "time": "2026-10-18 09:00"})

    assert created.status_code == 201
    assert created.json()["reminder"]["time"] == "2026-10-18 09:00"
    assert client.get("/api/reminders").json()["reminders"][0]["time"] == "2026-10-18 09:00"


@pytest.mark.parametrize(
    "legacy_or_malformed",
    [
        "09:00",  # 旧形状：只有钟点
        "2026-10-18",  # 只有日期
        "2026-10-18 9:00",  # 钟点未零填充
        "2026-13-01 09:00",  # 月份越界
        "2026-10-32 09:00",  # 日越界
        "2026-10-18 24:00",  # 24 小时制不允许 24 点
        "2026-10-18 09:60",  # 分钟越界
        "2026-10-18 09:00:00",  # 带秒
    ],
)
def test_any_shape_that_is_not_the_one_off_moment_is_refused(client, legacy_or_malformed: str) -> None:
    """C4 判据 2：任何不合 ``YYYY-MM-DD HH:MM`` 的取值 ⇒ 400 + ``fields["time"]`` + 可读正文。"""
    response = client.post("/api/reminders", json={"text": "x", "time": legacy_or_malformed})

    assert response.status_code == 400, f"{legacy_or_malformed!r} 应被拒绝"
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "time" in error["fields"]
    # 字段文案是**可读自然语言**，不是键名（也不是一句裸露的格式串）。
    assert error["fields"]["time"] != "time"
    assert error["fields"]["time"].strip()
    assert "YYYY-MM-DD HH:MM" not in error["fields"]["time"], "文案不该只是一个格式占位串"
    assert client.get("/api/reminders").json()["reminders"] == []


@pytest.mark.parametrize("extra", [{"repeat": "daily"}, {"date": "2026-10-18"}, {"every": "week"}])
def test_repetition_and_date_keys_are_refused_whole(client, extra: dict) -> None:
    """C4 判据 3：本能力不做的重复 / 单独日期键 ⇒ 400 ``validation_failed``，**不静默忽略**。"""
    response = client.post("/api/reminders", json={"text": "x", "time": "2026-10-18 09:00", **extra})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert client.get("/api/reminders").json()["reminders"] == []


def test_the_stored_time_keeps_the_legacy_row_out_of_the_write_path(client) -> None:
    """C4 的反面读数：旧形状写**不进来**，因此库里不会再有新的旧形状行。

    这条是 C12 的前提：迁移只改写存量，写路径从此只接受新形状——否则迁移的收窄会被下一次写入
    悄悄退回去。
    """
    assert client.post("/api/reminders", json={"text": "x", "time": "09:00"}).status_code == 400
    assert client.post("/api/reminders", json={"text": "x", "time": "2026-10-18 09:00"}).status_code == 201

    stored = [item["time"] for item in client.get("/api/reminders").json()["reminders"]]
    assert stored == ["2026-10-18 09:00"]


# ─── 报告形状：不新增报告字段 ────────────────────────────────────────────────
def test_the_report_has_no_new_field_besides_the_step_table(tmp_path: Path) -> None:
    """C14 判据 3：那一步的读数落在**既有**的 `from → to … 改动 N 行` 形状里。

    取证方式是把报告的步骤行按既有形状解析：``12 → 13`` 那行必须与其它版本步**同形**（同一个正则
    命中、同一句「改动 N 行」），没有「改写行数」这种独立字段。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00",))

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    report = completed.stdout
    assert "改写行数" not in report, "不得新增「改写行数」这个独立报告字段"
    rows = {
        int(from_): int(count)
        for from_, count in re.findall(r"^ {2}(\d+) → \d+ {2}.*改动 (\d+) 行$", report, re.MULTILINE)
    }
    assert rows[REMINDER_STEP_FROM] == 1


# ─── 迁移后本代码的 app 能打开它（终态不是「读数是 13」而已） ────────────────
def test_the_migrated_library_opens_and_serves_the_reminders_it_rewrote(tmp_path: Path) -> None:
    """迁移后的库能被本代码 app 正常打开，且读路径给出**改写后**的到期时刻。"""
    from tests.conftest import backend

    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00",))

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    with backend(data_dir) as opened:
        assert opened.get("/api/health").json()["status"] == "ok"
        listed = opened.get("/api/reminders").json()["reminders"]
        assert [item["time"] for item in listed] == [f"{_fixed_tz_today()}09:00"]
        # 票 ① 不写投递逻辑：「谁用谁负责」——`fired_at` / `state` 两个键由票 ②（#113）的读路径契约
        # 提供（C20），因此这里的期望随 #113 落地一并收紧为「键已就位且值为**未到点**」。
        assert listed[0]["fired_at"] is None
        assert listed[0]["state"] == "pending"


# ─── 迁移审计台账如实记录这一步（既有报告机制的继承） ────────────────────────
def test_the_audit_record_covers_the_new_step(tmp_path: Path) -> None:
    """台账的 ``from``/``to`` 覆盖到 13（既有 append-only 台账，不新增字段）。"""
    data_dir = tmp_path / "data"
    _build_v12_library(data_dir)

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    records = [
        json.loads(line)
        for line in (data_dir / "schema-migrations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["from"] == REMINDER_STEP_FROM
    assert records[-1]["to"] == REMINDER_SCHEMA_VERSION
    assert records[-1]["result"] == "success"


def test_the_backup_name_carries_the_new_target_version(tmp_path: Path) -> None:
    """备份目录名带 ``<from>-to-<to>``：这一步之后它是 ``12-to-13``（既有命名口径不变）。"""
    data_dir = tmp_path / "data"
    _build_v12_library(data_dir)

    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    backups = [path for path in data_dir.parent.iterdir() if path.name.startswith(f"{data_dir.name}-backup-")]
    assert len(backups) == 1, backups
    assert backups[0].name.startswith(f"{data_dir.name}-backup-12-to-13-")


def test_a_current_version_library_is_not_backed_up_or_written(tmp_path: Path) -> None:
    """无事的库仍不建备份、不写库（迁移这一步没有把「无事可做」变成「反正跑一遍」）。"""
    from tests.conftest import backend

    data_dir = tmp_path / "data"
    with backend(data_dir) as opened:
        assert opened.get("/api/health").json()["status"] == "ok"
    digest_before = _sha256(data_dir / DATABASE_FILE_NAME)

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert _sha256(data_dir / DATABASE_FILE_NAME) == digest_before
    assert not [
        path for path in data_dir.parent.iterdir() if path.name.startswith(f"{data_dir.name}-backup-")
    ]


# ─── 幂等：同一条链重跑不改写第二次 ──────────────────────────────────────────
def test_rerunning_the_step_on_an_already_migrated_library_changes_nothing(tmp_path: Path) -> None:
    """幂等：迁到 13 之后，再跑一次 ``12 → 13`` 的改写不动任何行（``fired_at`` 也仍是 NULL）。"""
    data_dir = tmp_path / "data"
    db_path = _build_v12_library(data_dir)
    _seed_legacy_reminders(db_path, ("09:00", "14:30"))
    assert _run_at_fixed_tz(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED
    after_first = _reminder_rows(data_dir / DATABASE_FILE_NAME)

    # 把版本号退回 12，让同一条链再走一遍（这是「重跑」在库里最直接的形态）。
    # 先把上次的备份目录挪走：备份目录名只到**秒**，同一秒内的第二次迁移会撞上同名目录而拒绝——
    # 那是既有备份口径（不是本票的东西），所以这里清掉它，让「重跑同一条链」这件事本身可观察。
    for leftover in data_dir.parent.iterdir():
        if leftover.name.startswith(f"{data_dir.name}-backup-"):
            shutil.rmtree(leftover)
    conn = sqlite3.connect(data_dir / DATABASE_FILE_NAME, isolation_level=None)
    try:
        conn.execute("UPDATE schema_meta SET value = '12' WHERE key = 'schema_version'")
    finally:
        conn.close()

    completed = _run_at_fixed_tz(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert _reminder_rows(data_dir / DATABASE_FILE_NAME) == after_first, "已是新形状的行不得被二次改写"
    assert _changed_rows(completed.stdout) == 0, completed.stdout
