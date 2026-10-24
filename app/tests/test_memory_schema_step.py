"""记忆档位列的两次迁移：``23 → 24``（换到 ReMe）与 ``24 → 25``（删掉 ``memory_mode``）。

判据来源：ADR-0034（长期记忆换到 ReMe）+ ADR-0035（长期记忆始终开启，删列）+ ADR-0015
（迁移显式、只前进、链连续）。``24 → 25`` 用**当前** ``SCHEMA`` 重建 ``agent_config``：新 DDL 已不再
声明 ``memory_mode``，逐列复制即删列；重建前返回「``NULL`` 或非 ``reme``」的行数（这些成员由
「关」变为始终开启），重建后重放为 0。

``23 → 24`` 的历史映射（``agentic → reme``）在 v25 起没有落点——这一列马上就被删除，因此 v23 源库
只按链一路迁到终态即可，断言的是「源被接受、链连续、行保真、该列不再存在」。

接缝 = 规格 T2：真实子进程 ``python -m alibot --migrate --data-dir <目录>`` + 磁盘上的真实库。
断言只落在外部可观察面：退出码、报告里的步骤行、迁移后库的读数。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from alibot.db import SCHEMA_VERSION

from tests.test_schema_migration import _backup_dirs, _build_library, _migrate_argv, _run

MIGRATION_SUCCEEDED = 0
SOURCE_VERSION = 23
V24_SOURCE_VERSION = 24
LEGACY_MODE = "agentic"

_INSERT_AGENT_SQL = (
    "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
    "VALUES (?, 'agent', ?, 1, '2026-10-24T09:00:00Z')"
)
_INSERT_CONFIG_SQL = (
    "INSERT INTO agent_config(member_id, role, memory_mode, created_at, updated_at) "
    "VALUES (?, '升级前的角色', ?, '2026-10-24T09:00:00Z', '2026-10-24T09:00:00Z')"
)

#: v24 形态的完整配置行：迁移后每一列都必须逐字保留，只有 ``memory_mode`` 消失。
_V24_COLUMNS = (
    "member_id",
    "role",
    "model_id",
    "system_prompt",
    "enabled",
    "auto_claim",
    "workspace_backend",
    "permission_mode",
    "allow_local_shell",
    "docker_image",
    "web_access",
    "context_window_messages",
    "context_trigger_ratio",
    "react_max_iters",
    "memory_mode",
    "created_at",
    "updated_at",
)
_V24_ROWS: tuple[tuple[Any, ...], ...] = (
    (
        "mem_off",
        "关着的角色",
        "openai:keep",
        "保留的提示",
        1,
        0,
        "local",
        "trusted-writes",
        1,
        "alibot/base:latest",
        "off",
        12,
        0.55,
        7,
        "off",
        "2026-10-24T09:00:01Z",
        "2026-10-24T09:00:02Z",
    ),
    (
        "mem_reme",
        "记着的角色",
        "ollama:qwen2.5",
        "",
        0,
        1,
        None,
        None,
        0,
        None,
        None,
        None,
        None,
        None,
        "reme",
        "2026-10-24T09:00:03Z",
        "2026-10-24T09:00:04Z",
    ),
    (
        "mem_null",
        "空档角色",
        "dashscope:qwen-max",
        "另一段提示",
        1,
        1,
        "docker",
        "ask-writes",
        0,
        None,
        "search+fetch",
        40,
        0.7,
        30,
        None,
        "2026-10-24T09:00:05Z",
        "2026-10-24T09:00:06Z",
    ),
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


def _seed_v24_agents(db_path: Path) -> None:
    """写三条 v24 形态的完整配置行：``off`` / ``reme`` / 显式 ``NULL``。"""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        placeholders = ", ".join("?" for _ in _V24_COLUMNS)
        for row in _V24_ROWS:
            conn.execute(_INSERT_AGENT_SQL, (row[0], row[0]))
            conn.execute(
                f"INSERT INTO agent_config({', '.join(_V24_COLUMNS)}) VALUES ({placeholders})",
                row,
            )
    finally:
        conn.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path, isolation_level=None)


def _migrate(data_dir: Path):
    return _run(*_migrate_argv(data_dir))


def _columns(db_path: Path) -> set[str]:
    conn = _connect(db_path)
    try:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(agent_config)")}
    finally:
        conn.close()


def _assert_v24_rows_survived(db_path: Path) -> None:
    """除 ``memory_mode`` 外的每一列逐字保留（行数与每列取值）。"""
    conn = _connect(db_path)
    try:
        kept = tuple(column for column in _V24_COLUMNS if column != "memory_mode")
        rows = tuple(
            conn.execute(f"SELECT {', '.join(kept)} FROM agent_config ORDER BY member_id").fetchall()
        )
    finally:
        conn.close()
    expected = tuple(
        tuple(value for column, value in zip(_V24_COLUMNS, row, strict=True) if column != "memory_mode")
        for row in sorted(_V24_ROWS, key=lambda row: row[0])
    )
    assert rows == expected


def test_a_v23_library_is_accepted_and_reaches_v25_without_the_memory_mode_column(
    tmp_path: Path,
) -> None:
    """v23 库被接受 ⇒ 链里有 23 → 24 与 24 → 25，终态无 ``memory_mode``，行数与角色逐字保留。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_agents(db_path)

    completed = _migrate(data_dir)

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"v{SOURCE_VERSION} 必须被接受，实际 {completed.returncode}"
        f"（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert f"{SOURCE_VERSION} → {SOURCE_VERSION + 1}" in completed.stdout, "报告必须点明 23 → 24"
    assert "24 → 25" in completed.stdout, "报告必须点明删列这一步"

    conn = _connect(db_path)
    try:
        stored = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]
        assert stored == str(SCHEMA_VERSION)
        assert conn.execute("SELECT COUNT(*) FROM agent_config").fetchone()[0] == 3
        assert (
            conn.execute("SELECT role FROM agent_config WHERE member_id = 'mem_legacy'").fetchone()[0]
            == "升级前的角色"
        )
    finally:
        conn.close()
    assert "memory_mode" not in _columns(db_path), "v25 的 agent_config 不得再有这一列"


def test_the_step_is_idempotent_and_keeps_every_row(tmp_path: Path) -> None:
    """迁移完再跑一次 ⇒ 退出 0、不新建备份、行数不变、列集不变。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_agents(db_path)
    assert _migrate(data_dir).returncode == MIGRATION_SUCCEEDED
    backups_after_first = _backup_dirs(data_dir)
    columns_after_first = _columns(db_path)

    second = _migrate(data_dir)

    assert second.returncode == MIGRATION_SUCCEEDED, second.stderr
    assert _backup_dirs(data_dir) == backups_after_first, "无事可做时不得再建一份备份"
    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM agent_config").fetchone()[0] == 3
    finally:
        conn.close()
    assert _columns(db_path) == columns_after_first


def test_a_v24_library_drops_the_memory_mode_column_and_keeps_every_other_value(
    tmp_path: Path,
) -> None:
    """v24 库（``off`` / ``reme`` / ``NULL`` 都有）⇒ 25 删列，其它列逐字保留；重放是空操作。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, V24_SOURCE_VERSION)
    _seed_v24_agents(db_path)

    completed = _migrate(data_dir)

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"v{V24_SOURCE_VERSION} 必须被接受，实际 {completed.returncode}"
        f"（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "24 → 25" in completed.stdout, "报告必须点明删列这一步"
    # 返回值 = 重建前「NULL 或不等于 reme」的行数：off 与 NULL 两行由「关」变为始终开启。
    assert "改动 2 行" in completed.stdout, completed.stdout
    assert "memory_mode" not in _columns(db_path)
    _assert_v24_rows_survived(db_path)

    backups_after_first = _backup_dirs(data_dir)
    second = _migrate(data_dir)

    assert second.returncode == MIGRATION_SUCCEEDED, second.stderr
    assert _backup_dirs(data_dir) == backups_after_first, "终态库重放不得再建备份"
    assert "memory_mode" not in _columns(db_path)
