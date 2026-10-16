"""只读预演 ``--migrate --dry-run``（issue #93）。

规格正本：``app/specs/issue-87-spec.md``（**D10** 预演 / **S5** 的观察点；T2：**唯一测试接缝 =
CLI 进程入口**）。ADR：``docs/adr/0015-schema-migration-is-explicit-and-forward-only.md`` 第一节的
「预演（原库零字节改动）」。

只经**真实子进程**取证：``python -m alibot --migrate --dry-run --data-dir <目录>``，配一个磁盘上的
真实数据目录 fixture；断言只落在**外部可观察面**——退出码、stdout / stderr 报告文本、数据目录与库的
终态、文件 sha256。不 import 实现内部函数、不断言私有函数或 SQL 文本（T1）。

本文件守的四件事（对应票面 Observable acceptance）：

1. 预演在**数据目录内**的临时副本（``alibot.db`` 连同 ``-wal`` / ``-shm``）上跑完整路径，随后删掉
   副本：原库 sha256 前后一致、数据目录里不留副本 / 不留边文件；
2. 预演**零写副作用**：不建备份、不写审计台账、不改库里的 Schema 版本、不搬任何数据；
3. 报告与真跑**同形**：将执行的步骤清单（含形态步骤）+ 每步将改动的行数 + 原库 sha256 的前后两个
   读数；对一个 v3 库与一个 ``stored=4`` 的半改名库各给一份读数，形态步骤的行数非零；
4. 预演**不污染原库**：预演之后同一库上真跑 ``--migrate`` 仍退 ``0`` 且到终态。

历史逐版 fixture 由 ``tests/_migration_history.py`` 提供（其余模块的共享 builder 见下）。
"""

from __future__ import annotations

import hashlib
import itertools
import re
import sqlite3
from pathlib import Path

import pytest
from alibot.db import DATABASE_FILE_NAME, SCHEMA_VERSION

# v3 形态库的搭建逻辑很重（历史对象 + 补列 + 灌数据），复用既有迁移测试的 fixture builder。
# 与 ``test_migration_chain_guard.py`` 同一体例：只借它的**文件级外部面**（``_run`` 起真实子进程、
# ``_build_library`` 造磁盘上的真实数据目录），不改那个文件、也不在它上面加断言。
from tests.test_schema_migration import (
    _add_duplicate_dm_peers,
    _backup_dirs,
    _build_library,
    _migrate_argv,
    _run,
)
from tests.test_shape_conditioned_step import _build_half_renamed_library

# 迁移入口的对外契约码（与既有迁移测试同体例：写**字面量**而不是 import 实现常量）。
MIGRATION_SUCCEEDED = 0
# 用法错误（``--dry-run`` 单独给）：argparse 自己的码，不进迁移入口的 ``{0, 3, 4, 5}`` 裁决集合。
USAGE_ERROR = 2
DATA_NOT_ACCEPTED = 3
MIGRATION_FAILED = 5

# 预演副本在数据目录内的前缀（票面 D10 的落点；本文件按它的**可观察后果**断言：预演后一个都不剩）。
DRY_RUN_COPY_PREFIX = ".alibot-dry-run-"

# 预演报告里版本步骤行的形状，与真跑报告**逐字同形**（缩进两格；报告头部那行没有缩进，不会命中）。
STEP_LINE_RE = re.compile(r"^ {2}(\d+) → (\d+) {2}", re.MULTILINE)

# 形态步骤行：不推进版本号，因此不带 ``from → to`` 前缀，只打标签 + 改动行数。
SHAPE_STEP_LINE_RE = re.compile(r"^ {2}形态步骤.*改动 (\d+) 行$", re.MULTILINE)

# 原库 sha256 的前后两个读数（报告必须两个都打，且必须一致）。
READING_RE = re.compile(r"原库文件 sha256（预演[前后]）：([0-9a-f]{64})")

# 造「版本号在、但本代码不接受」的库（未来版本）用的两句话。
STAMP_VERSION_SQL = "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'"

# 造带 ``-wal`` / ``-shm`` 边文件的库：另开一条连接、关掉自动 checkpoint，让它保持未 checkpoint 状态。
SET_WAL_SQL = "PRAGMA journal_mode = WAL"
DISABLE_AUTOCHECKPOINT_SQL = "PRAGMA wal_autocheckpoint = 0"
INSERT_MEMBER_SQL = (
    "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
    "VALUES ('mem_y', 'agent', '小红', 1, '2026-10-14T09:00:01Z')"
)
INSERT_MEMBER_ROW_SQL = "INSERT INTO channel_member(channel_id, member_id) VALUES ('ch_dev', 'mem_y')"

# 迁移后**原样保留**的三张旧表（规格 D12：回退面，唯一例外）与它们的行为读数语句。
LEGACY_TABLES = ("channel_member", "channel_read", "channel_budget")
LEGACY_COUNT_SQL: dict[str, str] = {table: f'SELECT COUNT(*) FROM "{table}"' for table in LEGACY_TABLES}


# ─── 文件与库级辅助 ──────────────────────────────────────────────────────────
def _dry_run_argv(data_dir: Path) -> list[str]:
    return ["--migrate", "--dry-run", "--data-dir", str(data_dir)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entries(directory: Path) -> set[str]:
    """数据目录里的条目名集合（预演前后各取一次，用来证明「不留副本」）。"""
    return {path.name for path in directory.iterdir()}


def _read_only(db_path: Path) -> sqlite3.Connection:
    """只读连接（断言库的终态用；预演本身的零字节改动由 sha256 举证）。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _read_version(db_path: Path) -> str:
    conn = _read_only(db_path)
    try:
        return str(conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0])
    finally:
        conn.close()


def _read_scalar(db_path: Path, sql: str) -> int:
    conn = _read_only(db_path)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def _stamp_version(db_path: Path, version: int) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute(STAMP_VERSION_SQL, (str(version),))
    finally:
        conn.close()


# ─── 报告解析（把报告当**数据**读，不依赖渲染细节之外的东西） ────────────────
def _reported_steps(report: str) -> list[tuple[int, int]]:
    """报告里的版本步骤表：``(from, to)`` 序列。"""
    return [(int(frm), int(to)) for frm, to in STEP_LINE_RE.findall(report)]


def _shape_step_rows(report: str) -> int:
    """形态步骤那一行报出的「改动 N 行」（没有那一行 ⇒ 断言失败）。"""
    match = SHAPE_STEP_LINE_RE.search(report)
    assert match is not None, f"报告里没有形态步骤那一行：{report!r}"
    return int(match.group(1))


def _readings(report: str) -> list[str]:
    """报告里原库 sha256 的前后两个读数（顺序 = 报告里的出现顺序）。"""
    return READING_RE.findall(report)


def _assert_continuous_and_covering(steps: list[tuple[int, int]], first: int) -> None:
    """步骤表必须从 ``first`` 起、每步严格 +1、彼此衔接、最后一个 ``to`` == ``SCHEMA_VERSION``。"""
    assert steps, "报告里没有步骤表"
    assert steps[0][0] == first, f"步骤表没有从库版本 {first} 起步：{steps[0]}"
    assert all(to == frm + 1 for frm, to in steps), f"步骤表里有不是严格 +1 的一步：{steps}"
    for previous, current in itertools.pairwise(steps):
        assert current[0] == previous[1], f"步骤表不连续：{previous} 之后接 {current}"
    assert steps[-1][1] == SCHEMA_VERSION, (
        f"计划没有覆盖到 SCHEMA_VERSION = {SCHEMA_VERSION}：止于 {steps[-1][1]}"
    )


# ─── 验收 1 / 2：原库 sha256 前后一致 + 数据目录里不留副本 ────────────────────
def test_dry_run_leaves_the_original_library_byte_identical_and_reports_both_readings(
    tmp_path: Path,
) -> None:
    """v3 库预演 ⇒ 退 ``0``、原库 sha256 前后一致，且报告里也打出这两个读数（S5）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    digest_before = _sha256(db_path)

    completed = _run(*_dry_run_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _sha256(db_path) == digest_before, "预演不得改动原库一个字节"

    readings = _readings(completed.stdout)
    assert readings == [digest_before, digest_before], (
        f"报告必须打出原库 sha256 的前后两个读数且一致，实际 {readings}"
    )


def test_dry_run_leaves_no_temp_copy_and_no_wal_sidecars_in_the_data_dir(tmp_path: Path) -> None:
    """预演前后分别列数据目录内容 ⇒ 预演后**没有**临时副本、没有 ``-wal`` / ``-shm`` 残留（S5）。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)
    before = _entries(data_dir)

    completed = _run(*_dry_run_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    after = _entries(data_dir)
    # 先点名两条具体后果（失败时的信息比集合相等更好读），再让集合相等当兜底：它蕴含前两条。
    assert not [name for name in after if name.startswith(DRY_RUN_COPY_PREFIX)], (
        f"数据目录里留下了预演副本：{sorted(after)}"
    )
    assert not [name for name in after if name.endswith(("-wal", "-shm"))], (
        f"数据目录里留下了 WAL / SHM 边文件：{sorted(after)}"
    )
    assert after == before, f"预演改变了数据目录的内容：{sorted(before)} → {sorted(after)}"


# ─── 验收 3：不建备份、不写台账、不改版本、不搬数据 ──────────────────────────
def test_dry_run_creates_no_backup_no_ledger_and_keeps_the_stored_version(tmp_path: Path) -> None:
    """预演 ⇒ 同级无 ``-backup-`` 目录、数据目录内无审计台账、库里 ``schema_version`` 仍是原值。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)

    completed = _run(*_dry_run_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert _backup_dirs(data_dir) == [], "预演不得建备份"
    assert not (data_dir / "schema-migrations.jsonl").exists(), "预演不得写审计台账"
    assert _read_version(db_path) == "3", "预演不得改库里的 Schema 版本"
    # 预演也不得搬任何数据：v3 库里本来就没有 ``target`` 表。
    assert _read_scalar(db_path, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'target'") == 0, (
        "预演不得把形态 / 版本步骤写进原库"
    )


# ─── 验收 4：报告内容（步骤清单 + 每步将改动的行数 + 形态步骤非零） ───────────
def test_the_dry_run_report_lists_the_planned_steps_with_row_counts(tmp_path: Path) -> None:
    """v3 库的报告：连续且覆盖到 ``SCHEMA_VERSION`` 的步骤表 + 每步行数 + 含形态步骤（S11 / S5）。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)

    completed = _run(*_dry_run_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    report = completed.stdout
    _assert_continuous_and_covering(_reported_steps(report), 3)
    assert _shape_step_rows(report) > 0, "v3 库的形态步骤要搬既有行，行数必须非零"

    # 每一步都要打出「改动 N 行」——报告给的是预判的改动面，不是一句「大概齐」。
    for line in report.splitlines():
        if STEP_LINE_RE.match(line) or SHAPE_STEP_LINE_RE.match(line):
            assert "改动" in line and "行" in line, f"步骤行没有打出行数：{line!r}"


@pytest.mark.parametrize("stored_version", [3, 4])
def test_the_dry_run_reports_the_shape_step_with_rows_for_v3_and_half_renamed(
    tmp_path: Path, stored_version: int
) -> None:
    """v3 库与 ``stored=4`` 的半改名库各给一份读数：形态步骤行数非零，且都从库里版本起步。"""
    data_dir = tmp_path / f"data-v{stored_version}"
    db_path = (
        _build_library(data_dir, 3)
        if stored_version == 3
        else _build_half_renamed_library(data_dir, stored_version=4)
    )
    digest_before = _sha256(db_path)

    completed = _run(*_dry_run_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _sha256(db_path) == digest_before
    assert _shape_step_rows(completed.stdout) > 0, "形态步骤要搬既有行，行数必须非零"
    _assert_continuous_and_covering(_reported_steps(completed.stdout), stored_version)


def test_dry_run_on_a_half_renamed_library_moves_nothing_in_the_original(tmp_path: Path) -> None:
    """``stored=4`` 半改名库：预演报告形态步骤有多少行，原库就**一行都没搬**（数据仍只在旧表名里）。"""
    data_dir = tmp_path / "data"
    db_path = _build_half_renamed_library(data_dir, stored_version=4)
    digest_before = _sha256(db_path)

    completed = _run(*_dry_run_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert _sha256(db_path) == digest_before
    assert _shape_step_rows(completed.stdout) > 0
    # 原库仍是半改名形态：影子行没落、版本没动、三张旧表的数据原样在。
    assert _read_scalar(db_path, "SELECT COUNT(*) FROM target WHERE kind = 'channel'") == 0
    assert _read_version(db_path) == "4"
    for table in LEGACY_TABLES:
        assert _read_scalar(db_path, LEGACY_COUNT_SQL[table]) > 0


# ─── 验收 5：WAL / SHM 情形（副本必须含边文件，报告反映完整状态） ────────────
def test_dry_run_reads_the_complete_state_including_wal_frames(tmp_path: Path) -> None:
    """库旁边确有 ``-wal`` / ``-shm`` ⇒ 报告反映的是**完整**状态，且用完仍不留副本（D10）。

    构造：v3 库 + 一条另开的 WAL 连接（关掉自动 checkpoint），在**未 checkpoint** 的窗口里多写一行
    ``channel_member``。那一行只在 ``-wal`` 里，主文件看不到——只复制主文件的实现会把形态步骤算成
    ``6`` 行（1 影子行 + 2 成员 + 2 已读 + 1 预算），含边文件的实现必须算成 ``7`` 行。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    writer = sqlite3.connect(db_path, isolation_level=None)
    try:
        assert writer.execute(SET_WAL_SQL).fetchone()[0] == "wal"
        writer.execute(DISABLE_AUTOCHECKPOINT_SQL)
        writer.execute(INSERT_MEMBER_SQL)
        writer.execute(INSERT_MEMBER_ROW_SQL)
        assert (data_dir / f"{DATABASE_FILE_NAME}-wal").is_file(), "构造失败：没有 -wal 边文件"
        assert (data_dir / f"{DATABASE_FILE_NAME}-shm").is_file(), "构造失败：没有 -shm 边文件"
        digest_before = _sha256(db_path)
        entries_before = _entries(data_dir)

        completed = _run(*_dry_run_argv(data_dir))

        assert completed.returncode == MIGRATION_SUCCEEDED, (
            f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
        )
        assert _shape_step_rows(completed.stdout) == 7, (
            f"预演没有读到 WAL 里那一行（完整状态 = 7 行）：{completed.stdout!r}"
        )
        assert _readings(completed.stdout) == [digest_before, digest_before]
        assert _sha256(db_path) == digest_before, "预演不得改动原库（含它的边文件所在的库）"
        assert _entries(data_dir) == entries_before, "预演不得在数据目录里留下副本或多余的边文件"
        assert not [name for name in _entries(data_dir) if name.startswith(DRY_RUN_COPY_PREFIX)], (
            "预演副本必须删掉"
        )
    finally:
        writer.close()


# ─── 预演之后真跑仍正常（验收 7） ────────────────────────────────────────────
def test_a_real_migration_after_a_dry_run_still_succeeds(tmp_path: Path) -> None:
    """同一库上预演一次、再真跑 ``--migrate`` ⇒ 退 ``0``、版本到 ``SCHEMA_VERSION``、数据齐备。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)

    dry = _run(*_dry_run_argv(data_dir))
    assert dry.returncode == MIGRATION_SUCCEEDED, dry.stderr

    real = _run(*_migrate_argv(data_dir))

    assert real.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {real.returncode}（stdout={real.stdout!r} stderr={real.stderr!r}）"
    )
    assert _read_version(db_path) == str(SCHEMA_VERSION)
    assert _read_scalar(db_path, "SELECT COUNT(*) FROM target") == 1
    assert _read_scalar(db_path, "SELECT COUNT(*) FROM target_member") == 2
    assert _read_scalar(db_path, "SELECT COUNT(*) FROM target_read") == 2
    assert _read_scalar(db_path, "SELECT used FROM target_budget") == 3, "已耗尽的预算不得归零"
    assert _read_scalar(db_path, "SELECT COUNT(*) FROM message") == 2


# ─── 与真跑同一个答复：拒绝路径逐字相同 ─────────────────────────────────────
def test_dry_run_refuses_with_the_same_verdict_and_body_as_a_real_migration(tmp_path: Path) -> None:
    """未来版本 ⇒ 预演与真跑都退 ``3``、**逐字相同**的正文，且原库 sha256 不变（D3 / D2）。

    这条钉住的是预演实现的**路径泄漏**：预演读的是副本，但正文里的库路径与那句
    ``--migrate --data-dir <数据目录>`` 指引必须指回**原库**——否则「同一个库、同一个答复」被打破。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SCHEMA_VERSION)
    _stamp_version(db_path, SCHEMA_VERSION + 1)
    digest_before = _sha256(db_path)

    real = _run(*_migrate_argv(data_dir))
    dry = _run(*_dry_run_argv(data_dir))

    assert real.returncode == DATA_NOT_ACCEPTED, real.stdout
    assert dry.returncode == DATA_NOT_ACCEPTED, dry.stdout
    assert dry.stderr == real.stderr, "预演与真跑对同一个库必须给逐字相同的拒绝正文"
    assert str(db_path) in dry.stderr
    assert DRY_RUN_COPY_PREFIX not in dry.stderr, "正文不得泄漏预演副本的路径"
    assert _sha256(db_path) == digest_before
    assert _readings(dry.stdout) == [digest_before, digest_before]
    assert not [name for name in _entries(data_dir) if name.startswith(DRY_RUN_COPY_PREFIX)]


# ─── 链在副本上跑不通 ⇒ 5，且正文与报告都写明原库未改动 ─────────────────────
def test_dry_run_reports_five_when_the_chain_cannot_run_on_the_copy(tmp_path: Path) -> None:
    """``5 → 6`` 的唯一性收紧遇到存量冲突 ⇒ 预演退 ``5``、正文与报告都写明原库未改动。

    与真跑的 ``5``（「迁移失败、**已回滚**」）分开措辞：预演没有可回滚之物——它连原库都没打开，
    写操作只落在副本上。这条同时证明预演**真的**跑了链（不是只做只读判定）：唯一性冲突是在副本上
    被撞上的，原库的重复 DM 行原样还在、sha256 不变。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 5)
    _add_duplicate_dm_peers(db_path)
    digest_before = _sha256(db_path)
    entries_before = _entries(data_dir)

    completed = _run(*_dry_run_argv(data_dir))

    assert completed.returncode == MIGRATION_FAILED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "原库未改动" in completed.stderr, "5 的正文必须写明原库未改动"
    assert "已回滚" not in completed.stderr, "预演没有可回滚之物，不得沿用真跑的「已回滚」措辞"
    assert _sha256(db_path) == digest_before
    assert _read_version(db_path) == "5"
    assert _read_scalar(db_path, "SELECT COUNT(*) FROM target WHERE kind = 'dm'") == 2, (
        "原库的冲突行必须原样在（预演只碰副本）"
    )
    assert _entries(data_dir) == entries_before, "预演跑不通也必须删掉副本"
    # 报告（stdout）同样要写明原库未改动：前后两个读数一致。
    assert _readings(completed.stdout) == [digest_before, digest_before]
    assert "原库零字节改动：前后读数一致。" in completed.stdout


# ─── 单独给 --dry-run 是用法错误（#93 人定裁定 (A)） ─────────────────────────


def test_bare_dry_run_without_migrate_is_a_usage_error_and_does_not_start_a_server(
    tmp_path: Path,
) -> None:
    """``--dry-run`` 单独给 ⇒ 用法错误（退出 ``2``），不是一个库的裁决、也不起服务（人定裁定 A）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    digest_before = _sha256(db_path)

    completed = _run("--dry-run", "--data-dir", str(data_dir))

    assert completed.returncode == USAGE_ERROR, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "--dry-run" in completed.stderr and "--migrate" in completed.stderr, (
        f"报错文案要说清两个旗标的关系：{completed.stderr!r}"
    )
    assert "unrecognized arguments" not in completed.stderr, (
        f"--dry-run 是被认得的旗标：它单独给要得到一条说清关系的用法错误，而不是「认不出这个参数」。"
        f"实际 {completed.stderr!r}"
    )
    assert "http://" not in completed.stdout, "单独给 --dry-run 不得顺手把服务起起来"
    assert _sha256(db_path) == digest_before
    assert _read_version(db_path) == "3"


# ─── 连副本都建不起来 ⇒ 仍给 5，且照样举「原库未改动」的证 ───────────────────
def test_dry_run_without_a_place_for_the_copy_still_proves_the_untouched_library(
    tmp_path: Path,
) -> None:
    """数据目录不可写 ⇒ 副本建不出来 ⇒ 退 ``5``，报告与正文仍写明原库未改动（人定裁定）。

    这条钉住的是「5 的两份证据缺一不可」：即使预演连副本都没跑起来，用户也必须拿到「原库一个字节
    都没动」的读数——不是一句「失败了」了事。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    digest_before = _sha256(db_path)
    data_dir.chmod(0o500)  # 数据目录不可写 ⇒ 副本目录建不出来
    try:
        completed = _run(*_dry_run_argv(data_dir))
    finally:
        data_dir.chmod(0o700)

    assert completed.returncode == MIGRATION_FAILED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "原库" in completed.stderr, "5 的正文必须写明原库未改动"
    assert _readings(completed.stdout) == [digest_before, digest_before], (
        f"报告仍要打出原库 sha256 的前后两个读数：{completed.stdout!r}"
    )
    assert "临时副本：没能建起来" in completed.stdout, "副本没建起来时报告不得说它「已删除」"
    assert _sha256(db_path) == digest_before
    assert not [name for name in _entries(data_dir) if name.startswith(DRY_RUN_COPY_PREFIX)]
