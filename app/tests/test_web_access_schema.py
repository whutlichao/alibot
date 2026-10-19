"""schema 18：`remote_consent_scope` 的 `web` 档 + 两个 `web_access` 配置位的**库形态**（#184）。

判据来源：ADR-0026 §二 / §四（联网是配置事实、默认关闭、确认按 provider 落
``remote_consent_scope``）、ADR-0015（迁移显式、只前进、链连续、不回填旧数据）。

接缝分两层，都是既有接缝、不新开：

- **库级**：由**本代码**初始化出来的当前版本库（``test_schema_migration._fresh_current_library``），
  直接读它对外可见的结果——CHECK 放行 / 拒绝、列的形态；
- **迁移**：规格 T2 的唯一接缝 ``python -m alibot --migrate --data-dir <目录>``，v17 形态库由
  ``tests/_migration_history.py`` 的留档搭出来（与既有迁移测试同一个 builder）。

断言只落在外部可观察面：写入是否被 CHECK 拒绝、迁移报告的步骤表与改动行数、迁移后从库里读回的
取值、迁移后的列集与「本代码初始化的库」是否一致。**不**断言 ``SCHEMA`` 文本、不断言私有函数。

本轮**不**为网络请求加任何用量表或计数列（D1 / ADR-0026 §六）：预算口径不变，schema 18 只有
「`remote_consent_scope` 多一个档 + 两个 `web_access` 列」这一件事。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from alibot.db import SCHEMA_VERSION

# v17 形态库的搭建与 CLI 调用复用既有迁移测试的文件级外部面（不 import 迁移实现内部）。
from tests.test_schema_migration import (
    _build_library,
    _connect,
    _fresh_current_library,
    _migrate_argv,
    _run,
    _shape,
)

MIGRATION_SUCCEEDED = 0

# 报告里的版本步骤行（缩进两格；报告头部那行没有缩进，不会命中）。
STEP_LINE_RE = re.compile(r"^ {2}(\d+) → (\d+) {2}", re.MULTILINE)

# 本票的起点：v17 是 #139 落地后的读数，`17 → 18` 由本票引入。
SOURCE_VERSION = 17

NOW = "2026-10-19T09:00:00Z"

# 升级前的存量确认行：三个已有档各一条。它们在 ``17 → 18`` 重建 ``remote_consent_scope``
# 之后必须逐字还在（重建只改 DDL，不改任何一行）。
CONSENT_ROWS = (
    ("model", "ollama", "2026-10-18T01:00:00Z"),
    ("mcp", "github", "2026-10-18T02:00:00Z"),
    ("docker", "ghcr.io/example/agent:1", "2026-10-18T03:00:00Z"),
)

# `web_access` 的合法取值（ADR-0026 §二）：`NULL`（关闭 / 跟随频道）与这两档。
LEGAL_WEB_ACCESS = ("search", "search+fetch")
# 一个形似而不合法的取值：把「只抓取」当成一档是最容易出现的误写（ADR-0026 只有两档）。
ILLEGAL_WEB_ACCESS = "fetch-only"


def _insert_agent_config(conn: sqlite3.Connection, member_id: str) -> None:
    """给一个已存在的 Agent 成员补一条配置行（两列 `web_access` 的另一处宿主）。"""
    conn.execute(
        "INSERT INTO agent_config(member_id, created_at, updated_at) VALUES (?, ?, ?)",
        (member_id, NOW, NOW),
    )


def _seed_config_rows(conn: sqlite3.Connection) -> None:
    """在一个空库里放一条频道行与一条 Agent 配置行（两列 `web_access` 的宿主）。"""
    conn.execute(
        "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
        "VALUES ('mem_x', 'agent', '小明', 1, ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO channel(id, name, description, created_at) VALUES ('ch_dev', 'dev', '', ?)",
        (NOW,),
    )
    _insert_agent_config(conn, "mem_x")


# ─── 验收 1：`scope_kind = 'web'` 可写、`'webx'` 被 CHECK 拒 ─────────────────
def test_scope_kind_web_is_accepted_and_unknown_kinds_are_rejected(tmp_path: Path) -> None:
    """`remote_consent_scope` 的 kind 白名单纳入 `web`；别的取值仍被库级 CHECK 拒。"""
    db_path = _fresh_current_library(tmp_path, "fresh")
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) VALUES ('web', ?, ?)",
            ("duckduckgo", NOW),
        )
        rows = conn.execute(
            "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope"
        ).fetchall()
        assert rows == [("web", "duckduckgo", NOW)], rows

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
                "VALUES ('webx', 'duckduckgo', ?)",
                (NOW,),
            )
    finally:
        conn.close()


# ─── 验收 2：`web_access` 的两个合法档可写、`NULL` 可写、`'fetch-only'` 被拒 ─────
def test_channel_and_agent_web_access_take_only_the_two_documented_levels(tmp_path: Path) -> None:
    """两个 `web_access` 列的白名单：`NULL` / `'search'` / `'search+fetch'` 可写，其余被 CHECK 拒。"""
    db_path = _fresh_current_library(tmp_path, "fresh")
    conn = _connect(db_path)
    try:
        _seed_config_rows(conn)
        for level in LEGAL_WEB_ACCESS:
            conn.execute("UPDATE channel SET web_access = ? WHERE id = 'ch_dev'", (level,))
            saved = conn.execute("SELECT web_access FROM channel WHERE id = 'ch_dev'").fetchone()
            assert saved == (level,), f"频道的 web_access 没能写进 {level}：{saved}"

            conn.execute("UPDATE agent_config SET web_access = ? WHERE member_id = 'mem_x'", (level,))
            saved = conn.execute("SELECT web_access FROM agent_config WHERE member_id = 'mem_x'").fetchone()
            assert saved == (level,), f"Agent 的 web_access 没能写进 {level}：{saved}"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE channel SET web_access = ? WHERE id = 'ch_dev'", (ILLEGAL_WEB_ACCESS,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE agent_config SET web_access = ? WHERE member_id = 'mem_x'", (ILLEGAL_WEB_ACCESS,)
            )

        # `NULL` 是两条列的合法取值：频道的 `NULL` = 关闭，Agent 的 `NULL` = 跟随频道。
        conn.execute("UPDATE channel SET web_access = NULL WHERE id = 'ch_dev'")
        conn.execute("UPDATE agent_config SET web_access = NULL WHERE member_id = 'mem_x'")
        assert conn.execute("SELECT web_access FROM channel WHERE id = 'ch_dev'").fetchone() == (None,)
        assert conn.execute("SELECT web_access FROM agent_config WHERE member_id = 'mem_x'").fetchone() == (
            None,
        )
    finally:
        conn.close()


# ─── 验收 3：`17 → 18` 一步、既有行不变、`web_access` 全为 `NULL`（不回填） ──────
def _seed_legacy_rows(db_path: Path) -> None:
    """在 v17 库里放三条既有确认行与一条 Agent 配置行（升级前就存在的存量面）。"""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_config(member_id, role, model_id, created_at, updated_at) "
            "VALUES ('mem_x', 'dev', 'qwen3', ?, ?)",
            (NOW, NOW),
        )
        for scope_kind, scope_id, acknowledged_at in CONSENT_ROWS:
            conn.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) VALUES (?, ?, ?)",
                (scope_kind, scope_id, acknowledged_at),
            )
    finally:
        conn.close()


def _migrated_v17_library(tmp_path: Path) -> Path:
    """搭一个 v17 形态的库、灌入既有确认行，再用真实子进程把它推进到本代码版本。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_rows(db_path)
    completed = _run(*_migrate_argv(data_dir))
    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    return db_path


def test_a_v17_library_reaches_the_current_version_in_one_step_and_keeps_every_row(
    tmp_path: Path,
) -> None:
    """v17 库被接受：报告里有 17 → 18、终点等于本代码版本；重建后的表保真、两列全为 NULL。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_rows(db_path)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    steps = [(int(frm), int(to)) for frm, to in STEP_LINE_RE.findall(completed.stdout)]
    assert (SOURCE_VERSION, SOURCE_VERSION + 1) in steps, completed.stdout
    assert steps[-1][1] == SCHEMA_VERSION, completed.stdout
    assert SCHEMA_VERSION >= 18

    conn = _connect(db_path)
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        assert version[0] == str(SCHEMA_VERSION)

        # 重建保真：三条既有确认行逐字还在（不丢数据、不改写确认时间）。
        rows = conn.execute(
            "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope ORDER BY scope_kind"
        ).fetchall()
        assert rows == sorted(CONSENT_ROWS), f"重建丢了或改写了既有确认行：{rows}"

        # 新档在**迁移后**的树上可写；词表外的词仍被拒（重建只放宽到四档，不是去掉 CHECK）。
        conn.execute(
            "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) VALUES ('web', ?, ?)",
            ("duckduckgo", NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
                "VALUES ('webx', 'duckduckgo', ?)",
                (NOW,),
            )

        # 不回填（ADR-0015）：既有频道与 Agent 的两列都停在 NULL——
        # 频道 = 关闭（行为逐字不变），Agent = 跟随频道。
        assert conn.execute("SELECT web_access FROM channel").fetchall() == [(None,)]
        assert conn.execute("SELECT web_access FROM agent_config").fetchall() == [(None,)]
    finally:
        conn.close()


def test_the_migrated_web_access_columns_match_a_fresh_library(tmp_path: Path) -> None:
    """两列与重建后的表在「迁移库」与「本代码初始化的库」上同形（D7 的终态判据）。"""
    db_path = _migrated_v17_library(tmp_path)
    fresh_path = _fresh_current_library(tmp_path, "fresh")
    migrated = _shape(db_path)
    fresh = _shape(fresh_path)

    for table in ("channel", "agent_config", "remote_consent_scope"):
        assert migrated["columns"][table] == fresh["columns"][table], f"{table} 的列与全量建库不一致"
    assert migrated["version"] == fresh["version"] == str(SCHEMA_VERSION)


def test_the_v18_archive_encodes_the_web_scope_and_the_two_web_access_columns(tmp_path: Path) -> None:
    """留档自身可复核：用 v18 留档搭出来的库就表现本版的三处库级行为（抄漏一处就红）。"""
    conn = _connect(_build_library(tmp_path / "archive", SCHEMA_VERSION))
    try:
        conn.execute(
            "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) VALUES ('web', ?, ?)",
            ("duckduckgo", NOW),
        )
        _insert_agent_config(conn, "mem_x")

        for level in LEGAL_WEB_ACCESS:
            conn.execute("UPDATE channel SET web_access = ? WHERE id = 'ch_dev'", (level,))
            conn.execute("UPDATE agent_config SET web_access = ? WHERE member_id = 'mem_x'", (level,))
        for target in (
            "UPDATE channel SET web_access = ? WHERE id = 'ch_dev'",
            "UPDATE agent_config SET web_access = ? WHERE member_id = 'mem_x'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(target, (ILLEGAL_WEB_ACCESS,))
    finally:
        conn.close()
