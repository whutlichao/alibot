"""#261 的记忆档位迁移：``SCHEMA_VERSION`` 23 → 24（``agent_config.memory_mode`` 的 CHECK 重建）。

判据来源：ADR-0034（长期记忆换到 ReMe）+ ADR-0015（迁移显式、只前进、链连续）。
SQLite 改列级 CHECK 只能重建表；重建的纪律是「建新表 / 搬迁 / 回填」的幂等形态，**旧行逐行保真**，
唯一的取值改写是 ``memory_mode``：旧的 Markdown 记忆档 → ``reme``（同一个决策换了实现，不能静默
降级成 ``off``）。

接缝 = 规格 T2：真实子进程 ``python -m alibot --migrate --data-dir <目录>`` + 磁盘上的真实库。
断言只落在外部可观察面：退出码、报告里的步骤行、迁移后库的读数与 CHECK 的行为。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alibot.db import SCHEMA_VERSION

from tests.test_schema_migration import _backup_dirs, _build_library, _migrate_argv, _run

MIGRATION_SUCCEEDED = 0
SOURCE_VERSION = 23
LEGACY_MODE = "agentic"

_INSERT_AGENT_SQL = (
    "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
    "VALUES (?, 'agent', ?, 1, '2026-10-24T09:00:00Z')"
)
_INSERT_CONFIG_SQL = (
    "INSERT INTO agent_config(member_id, role, memory_mode, created_at, updated_at) "
    "VALUES (?, '升级前的角色', ?, '2026-10-24T09:00:00Z', '2026-10-24T09:00:00Z')"
)


def _seed_agents(db_path: Path) -> None:
    """在迁移**之前**写入三条 Agent 配置：旧档 / 关闭 / 显式 NULL。"""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        for member_id, mode in (
            ("mem_legacy", LEGACY_MODE),
            ("mem_off", "off"),
            ("mem_null", None),
        ):
            conn.execute(_INSERT_AGENT_SQL, (member_id, member_id))
            conn.execute(_INSERT_CONFIG_SQL, (member_id, mode))
    finally:
        conn.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path, isolation_level=None)


def _migrate(data_dir: Path):
    return _run(*_migrate_argv(data_dir))


def test_a_v23_library_maps_the_legacy_memory_mode_to_reme(tmp_path: Path) -> None:
    """v23 库被接受 ⇒ 报告里有 23 → 24，旧档变 ``reme``，其余取值逐字保留。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_agents(db_path)

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
        modes = dict(conn.execute("SELECT member_id, memory_mode FROM agent_config"))
        assert modes["mem_legacy"] == "reme", "旧记忆档必须显式映射为 reme"
        assert modes["mem_off"] == "off", "关闭档不动"
        assert modes["mem_null"] is None, "显式 NULL 不回填"
        assert (
            conn.execute("SELECT role FROM agent_config WHERE member_id = 'mem_legacy'").fetchone()[0]
            == "升级前的角色"
        )

        # 新 CHECK 接受 reme、拒绝旧词（绕过应用层直接写：行为可观察，不断言 DDL 文本）。
        conn.execute("UPDATE agent_config SET memory_mode = 'reme' WHERE member_id = 'mem_off'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE agent_config SET memory_mode = ? WHERE member_id = 'mem_null'", (LEGACY_MODE,)
            )
    finally:
        conn.close()


def test_the_step_is_idempotent_and_keeps_every_row(tmp_path: Path) -> None:
    """迁移完再跑一次 ⇒ 退出 0、不新建备份、行数不变、映射结果不变。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_agents(db_path)
    assert _migrate(data_dir).returncode == MIGRATION_SUCCEEDED
    backups_after_first = _backup_dirs(data_dir)

    second = _migrate(data_dir)

    assert second.returncode == MIGRATION_SUCCEEDED, second.stderr
    assert _backup_dirs(data_dir) == backups_after_first, "无事可做时不得再建一份备份"
    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM agent_config").fetchone()[0] == 3
        assert (
            conn.execute("SELECT memory_mode FROM agent_config WHERE member_id = 'mem_legacy'").fetchone()[0]
            == "reme"
        )
    finally:
        conn.close()
