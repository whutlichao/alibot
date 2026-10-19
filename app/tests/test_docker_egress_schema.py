"""schema 19：`channel.docker_egress`（#187 / ADR-0026 §五）的**库形态**与迁移。

判据来源：ADR-0026 §五（容器默认无网、显式允许才放开白名单）、ADR-0015（迁移显式、只前进、链连续、
不回填旧数据）。

接缝沿用既有迁移测试的外部面（``tests.test_schema_migration``），断言只落在可观察结果上：库级
``CHECK`` 放行 / 拒绝、迁移报告的步骤表与改动行数、迁移后读回的取值与列集。**不**断言 ``SCHEMA``
文本、也不把版本号钉死（从 ``SCHEMA_VERSION`` 派生）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from alibot.db import SCHEMA_VERSION

from tests.test_schema_migration import (
    _build_library,
    _connect,
    _fresh_current_library,
    _migrate_argv,
    _run,
    _shape,
)

MIGRATION_SUCCEEDED = 0
NOW = "2026-10-19T09:00:00Z"
# 本票的起点：v18 是 #184 落地后的读数，`18 → 19` 由本票引入。
SOURCE_VERSION = 18
STEP_LINE_RE = re.compile(r"^ {2}(\d+) → (\d+) {2}", re.MULTILINE)


# ─── 验收 1：列的形态与 CHECK ───────────────────────────────────────────────
def test_docker_egress_is_a_two_state_switch_with_no_egress_by_default(tmp_path: Path) -> None:
    """新库的两态：缺省 0（容器无网）、可写 1；别的取值被库级 CHECK 拒。"""
    conn = _connect(_fresh_current_library(tmp_path, "fresh"))
    try:
        # 空库没有频道行：直接落一行（本用例只关心列的形态与 CHECK）。
        conn.execute(
            "INSERT INTO channel(id, name, description, created_at) VALUES ('ch_dev', 'dev', '', ?)",
            (NOW,),
        )
        default = conn.execute("SELECT docker_egress FROM channel").fetchone()
        assert default is not None and default[0] == 0

        conn.execute("UPDATE channel SET docker_egress = 1")
        assert conn.execute("SELECT docker_egress FROM channel").fetchone()[0] == 1

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE channel SET docker_egress = 2")
    finally:
        conn.close()


# ─── 验收 2：18 → 19 一步、旧行不回填、列集与当前代码一致 ────────────────────
def test_migration_from_18_adds_the_column_without_backfilling(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stdout + completed.stderr
    steps = [(int(frm), int(to)) for frm, to in STEP_LINE_RE.findall(completed.stdout)]
    assert (SOURCE_VERSION, SOURCE_VERSION + 1) in steps, completed.stdout
    # 严格 +1：这条链上没有跳步（每一步的 to 就是下一步的 from）。
    assert all(to == frm + 1 for frm, to in steps)
    assert steps[-1][1] == SCHEMA_VERSION

    conn = _connect(db_path)
    try:
        stamped = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]
        assert stamped == str(SCHEMA_VERSION)
        # 旧行不回填：迁移只加列，存量频道停在该列默认值（= 容器无网，比升级前更严的缺省）。
        assert conn.execute("SELECT COUNT(*) FROM channel WHERE docker_egress != 0").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0] > 0
    finally:
        conn.close()
    assert _shape(db_path) == _shape(_fresh_current_library(tmp_path, "current"))
