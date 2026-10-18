"""#58 的库形态步骤：``SCHEMA_VERSION`` 14 → 15（``round_outcome.status`` 的库级 ``CHECK`` 重建）。

判据来源：ADR-0008（结构化沉默是独立的轮次结果状态）+ ADR-0015（迁移显式、只前进、链连续）。
SQLite 没有 ``ALTER TABLE … ADD CONSTRAINT``，所以把 ``silent`` 纳入表级 ``CHECK`` **必须重建表**；
重建的纪律是「建新表 / 搬迁 / 回填」幂等形态，**旧行逐行保真**（不回填语义、不丢数据）。

接缝 = 规格 T2：真实子进程 ``python -m alibot --migrate --data-dir <目录>`` + 磁盘上的真实库。
断言只落在外部可观察面：退出码、报告里的步骤行、迁移后库的读数（版本号 / 保真的旧行 / CHECK 的行为）。
历史 v14 形态的库由 ``tests/_migration_history.py`` 的留档搭出来（与既有迁移测试同一个 builder）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alibot.db import SCHEMA_VERSION

from tests.test_schema_migration import (
    _backup_dirs,
    _build_library,
    _migrate_argv,
    _run,
)

MIGRATION_SUCCEEDED = 0
# 本条步骤的起点：v14 是 #53 落地后的读数，#58 把它推到 15。
SOURCE_VERSION = 14

_INSERT_OUTCOME_SQL = (
    "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, reason, started_at, "
    "finished_at, consumed_budget, merged, reply_seq) VALUES (?, 'mem_x', 'ch_dev', 2, ?, ?, "
    "'2026-10-14T09:00:10Z', '2026-10-14T09:00:11Z', 0, 0, NULL)"
)


def _migrate(data_dir: Path):
    return _run(*_migrate_argv(data_dir))


def _seed_legacy_outcome(db_path: Path) -> None:
    """在迁移**之前**往 v14 库里写一条既有结论：它是「重建表保真」的对照物。"""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute(
            _INSERT_OUTCOME_SQL,
            ("round_v14_replied", "replied", "升级前就存在的一行"),
        )
    finally:
        conn.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path, isolation_level=None)


def test_a_v14_library_migrates_to_a_check_that_accepts_silent(tmp_path: Path) -> None:
    """v14 库被接受 ⇒ 报告里有 14 → 15，迁移后 CHECK 接受 ``silent`` 且旧行逐字还在。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_outcome(db_path)

    completed = _migrate(data_dir)

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"v{SOURCE_VERSION} 必须被接受，实际 {completed.returncode}"
        f"（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert f"{SOURCE_VERSION} → {SOURCE_VERSION + 1}" in completed.stdout, "报告必须点明这一步"

    conn = _connect(db_path)
    try:
        stored = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]
        assert stored == str(SCHEMA_VERSION)

        # 旧行逐字保真：重建表只改 DDL，不改任何一行（也不把旧行回填成 silent）。
        preserved = conn.execute(
            "SELECT status, reason FROM round_outcome WHERE id = 'round_v14_replied'"
        ).fetchone()
        assert preserved == ("replied", "升级前就存在的一行")

        declared = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'round_outcome'"
        ).fetchone()[0]
        assert "'silent'" in declared, "表级 CHECK 必须纳入 silent"

        # 库级 CHECK 现在**接受** silent：绕过应用层直接写一行。
        conn.execute(_INSERT_OUTCOME_SQL, ("round_v15_silent", "silent", "迁移后声明沉默"))
        # 而词表外的词仍然被拒：这次重建只**放宽**到词表，不是把 CHECK 去掉。
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_INSERT_OUTCOME_SQL, ("round_v15_quiet", "quiet", "非法词"))
    finally:
        conn.close()


def test_the_step_is_idempotent_and_keeps_every_row(tmp_path: Path) -> None:
    """迁移完再跑一次 ⇒ 改动 0 行、退出 0、不新建备份、行数不变（ADR-0015 的幂等纪律）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_outcome(db_path)
    assert _migrate(data_dir).returncode == MIGRATION_SUCCEEDED
    backups_after_first = _backup_dirs(data_dir)

    conn = _connect(db_path)
    try:
        before = conn.execute("SELECT COUNT(*) FROM round_outcome").fetchone()[0]
    finally:
        conn.close()

    second = _migrate(data_dir)

    assert second.returncode == MIGRATION_SUCCEEDED, second.stderr
    assert _backup_dirs(data_dir) == backups_after_first, "无事可做时不得再建一份备份"
    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM round_outcome").fetchone()[0] == before
    finally:
        conn.close()


def test_a_v15_library_is_the_current_form_and_still_reaches_terminal_state(tmp_path: Path) -> None:
    """已经是 v15 的库：``--migrate`` 空链、退出 0（不因为「差一个号」而重跑重建步骤）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SCHEMA_VERSION)

    completed = _migrate(data_dir)

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[
            0
        ] == str(SCHEMA_VERSION)
    finally:
        conn.close()
