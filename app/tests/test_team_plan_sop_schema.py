"""#138 的库形态步骤：``SCHEMA_VERSION`` 15 → 16（team / Plan / SOP 的纯增量形态）。

判据来源：规格 §4.1 / §4.2、ADR-0018（team 是频道级拓扑）、ADR-0022（TeamAssign 是运行期记录）、
ADR-0023（Plan / SOP 各有独立状态机）、ADR-0015（迁移显式、只前进、链连续）。

接缝 = 规格 T2：真实子进程 ``python -m alibot --migrate [--dry-run] --data-dir <目录>`` + 磁盘上的
真实库。断言只落在外部可观察面：退出码、报告里的步骤行与 sha256 读数、迁移后库的读数（版本号 /
旧行默认值 / 表与索引的存在 / CHECK 与触发器的行为 / 外键级联）。

历史 v15 形态的库由 ``tests/_migration_history.py`` 的留档搭出来（与既有迁移测试同一个 builder）；
v16 的 DDL 留档也落在那里，供后续票直接复用。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest
from alibot.db import SCHEMA_VERSION

# v15 形态库的搭建与 CLI 调用复用既有迁移测试的文件级外部面（不 import 迁移实现内部）。
from tests.test_schema_migration import (
    _backup_dirs,
    _build_library,
    _fresh_current_library,
    _migrate_argv,
    _run,
    _shape,
)

MIGRATION_SUCCEEDED = 0

# 本票的起点：v15 是 #58 落地后的读数，`15 → 16` 由本票引入。
SOURCE_VERSION = 15

NEW_TABLES = (
    "team_run",
    "team_assignment",
    "plan_run",
    "plan_task",
    "sop_definition",
    "sop_run",
)
NEW_INDEXES = ("idx_team_assignment_target", "idx_team_assignment_team_run")
NEW_TRIGGERS = (
    "channel_team_requires_leader_insert",
    "channel_team_requires_leader_update",
)

# 报告里的版本步骤行（缩进两格；报告头部那行没有缩进，不会命中）与 sha256 读数。
STEP_LINE_RE = re.compile(r"^ {2}(\d+) → (\d+) {2}", re.MULTILINE)
READING_RE = re.compile(r"原库文件 sha256（预演[前后]）：([0-9a-f]{64})")

NOW = "2026-10-18T09:00:00Z"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migrate(data_dir: Path):
    return _run(*_migrate_argv(data_dir))


def _dry_run(data_dir: Path):
    return _run("--migrate", "--dry-run", "--data-dir", str(data_dir))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrated_library(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    completed = _migrate(data_dir)
    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    return db_path


# ─── 验收 1 / 4：版本号、链的这一步、旧行默认值 ─────────────────────────────
def test_a_v15_library_reaches_v16_and_old_channels_default_to_autonomous(tmp_path: Path) -> None:
    """v15 库被接受：报告里有 15 → 16、终点等于本代码版本；旧频道按默认值解释。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)

    completed = _migrate(data_dir)
    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    steps = [(int(frm), int(to)) for frm, to in STEP_LINE_RE.findall(completed.stdout)]
    assert (SOURCE_VERSION, SOURCE_VERSION + 1) in steps, completed.stdout
    assert steps[-1][1] == SCHEMA_VERSION, completed.stdout
    assert SCHEMA_VERSION >= 16

    conn = _connect(db_path)
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        assert version[0] == str(SCHEMA_VERSION)
        row = conn.execute("SELECT mode, team_leader_member_id FROM channel WHERE id = 'ch_dev'").fetchone()
        assert row == ("autonomous", None), f"旧频道的默认值不对：{row}"
    finally:
        conn.close()

    # 既有列的类型 / 默认值不回填：v15 的种子行逐字还在（频道名与描述没被改写）。
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT name, description FROM channel WHERE id = 'ch_dev'").fetchone()
        assert row == ("dev", "开发频道")
    finally:
        conn.close()


# ─── 验收 5：预演零字节改动 + 两个 sha256 读数 ───────────────────────────────
def test_dry_run_keeps_the_original_library_byte_identical(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    before = _sha256(db_path)
    backups_before = _backup_dirs(data_dir)

    completed = _dry_run(data_dir)
    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    readings = READING_RE.findall(completed.stdout)
    assert len(readings) == 2, completed.stdout
    assert readings[0] == readings[1] == before, completed.stdout
    assert _sha256(db_path) == before, "预演改动了原库"
    assert _backup_dirs(data_dir) == backups_before, "预演建了备份"
    print(f"dry-run sha256 before={before} after={_sha256(db_path)}")


# ─── 验收 3：六张新表 / 索引 / 两处路径一致 ─────────────────────────────────
def test_the_six_new_tables_and_indexes_exist_and_match_a_fresh_library(tmp_path: Path) -> None:
    db_path = _migrated_library(tmp_path)
    fresh_path = _fresh_current_library(tmp_path, "fresh")
    migrated = _shape(db_path)
    fresh = _shape(fresh_path)

    for table in NEW_TABLES:
        assert table in migrated["tables"], f"迁移后缺表：{table}"
        assert migrated["columns"][table] == fresh["columns"][table], f"{table} 的列与全量建库不一致"
    for index in NEW_INDEXES:
        assert index in migrated["indexes"], f"迁移后缺索引：{index}"
        assert index in fresh["indexes"], f"全量建库缺索引：{index}"
    for trigger in NEW_TRIGGERS:
        assert trigger in migrated["triggers"], f"迁移后缺触发器：{trigger}"
        assert trigger in fresh["triggers"], f"全量建库缺触发器：{trigger}"
    assert migrated["version"] == fresh["version"] == str(SCHEMA_VERSION)


# ─── 验收 2：team 频道必须有队长（插入与更新两条触发器） ────────────────────
def test_a_team_channel_without_a_leader_is_rejected_on_insert_and_update(tmp_path: Path) -> None:
    db_path = _migrated_library(tmp_path)
    conn = _connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="team channel requires a leader"):
            conn.execute(
                "INSERT INTO channel(id, name, description, created_at, mode) "
                "VALUES ('ch_team', 'team', '', ?, 'team')",
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="team channel requires a leader"):
            conn.execute("UPDATE channel SET mode = 'team' WHERE id = 'ch_dev'")

        # 同一条 UPDATE 里给上队长 ⇒ 不变量成立，允许。
        conn.execute("UPDATE channel SET mode = 'team', team_leader_member_id = 'mem_x' WHERE id = 'ch_dev'")
        row = conn.execute("SELECT mode, team_leader_member_id FROM channel WHERE id = 'ch_dev'").fetchone()
        assert row == ("team", "mem_x")

        # 插入时带队长同样允许。
        conn.execute(
            "INSERT INTO channel(id, name, description, created_at, mode, team_leader_member_id) "
            "VALUES ('ch_team', 'team', '', ?, 'team', 'mem_x')",
            (NOW,),
        )
        # 把队长摘掉（仍是 team）也被拒绝。
        with pytest.raises(sqlite3.IntegrityError, match="team channel requires a leader"):
            conn.execute("UPDATE channel SET team_leader_member_id = NULL WHERE id = 'ch_dev'")
    finally:
        conn.close()


# ─── 验收 3：CHECK 与 UNIQUE 与规格 §4.2 一致 ───────────────────────────────
def test_state_checks_and_plan_sequence_uniqueness_are_enforced(tmp_path: Path) -> None:
    db_path = _migrated_library(tmp_path)
    conn = _connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO team_run(id, target_id, leader_member_id, base_seq, status, started_at) "
                "VALUES ('tr_bad', 'ch_dev', 'mem_x', 0, 'bogus', ?)",
                (NOW,),
            )
        conn.execute(
            "INSERT INTO team_run(id, target_id, leader_member_id, base_seq, status, started_at) "
            "VALUES ('tr_ok', 'ch_dev', 'mem_x', 0, 'running', ?)",
            (NOW,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO team_assignment(id, team_run_id, target_id, leader_member_id, member_id, "
                "tool_call_id, prompt, state, jump_consumed, started_at) "
                "VALUES ('ta_bad', 'tr_ok', 'ch_dev', 'mem_x', 'mem_x', 'call_1', 'do', 'started', 2, ?)",
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO team_assignment(id, team_run_id, target_id, leader_member_id, member_id, "
                "tool_call_id, prompt, state, started_at) "
                "VALUES ('ta_bad', 'tr_ok', 'ch_dev', 'mem_x', 'mem_x', 'call_1', 'do', 'bogus', ?)",
                (NOW,),
            )

        conn.execute(
            "INSERT INTO plan_run(id, target_id, member_id, base_seq, status, started_at) "
            "VALUES ('pr_1', 'ch_dev', 'mem_x', 0, 'running', ?)",
            (NOW,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO plan_task(id, plan_run_id, seq, subject, state, created_at, updated_at) "
                "VALUES ('pt_bad', 'pr_1', 1, 's', 'bogus', ?, ?)",
                (NOW, NOW),
            )
        conn.execute(
            "INSERT INTO plan_task(id, plan_run_id, seq, subject, state, created_at, updated_at) "
            "VALUES ('pt_1', 'pr_1', 1, 's', 'pending', ?, ?)",
            (NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO plan_task(id, plan_run_id, seq, subject, state, created_at, updated_at) "
                "VALUES ('pt_2', 'pr_1', 1, 's', 'pending', ?, ?)",
                (NOW, NOW),
            )
        # ``ON DELETE CASCADE``：plan_task 随 run 删除（工作态没有独立保留语义）。
        conn.execute("DELETE FROM plan_run WHERE id = 'pr_1'")
        assert conn.execute("SELECT COUNT(*) FROM plan_task WHERE plan_run_id = 'pr_1'").fetchone()[0] == 0

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sop_definition(id, name, steps, enabled, created_at, updated_at) "
                "VALUES ('sd_bad', 'x', '[]', 2, ?, ?)",
                (NOW, NOW),
            )
        conn.execute(
            "INSERT INTO sop_definition(id, name, steps, enabled, created_at, updated_at) "
            "VALUES ('sd_1', 'x', '[]', 1, ?, ?)",
            (NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sop_run(id, sop_definition_id, target_id, leader_member_id, state, phase, "
                "started_at, updated_at) VALUES ('sr_bad', 'sd_1', 'ch_dev', 'mem_x', '{}', 'bogus', ?, ?)",
                (NOW, NOW),
            )
        conn.execute(
            "INSERT INTO sop_run(id, sop_definition_id, target_id, leader_member_id, state, phase, "
            "started_at, updated_at) VALUES ('sr_1', 'sd_1', 'ch_dev', 'mem_x', '{}', 'pending', ?, ?)",
            (NOW, NOW),
        )
        assert conn.execute("SELECT COUNT(*) FROM sop_run").fetchone()[0] == 1
    finally:
        conn.close()
