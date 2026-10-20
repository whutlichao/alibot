"""15 → 16 → 17 的迁移演练（#157）。

接缝与既有迁移测试一致：真实子进程 ``python -m alibot --migrate --data-dir <目录>``；断言只落在
退出码、报告正文与磁盘上的库（不 import 私有函数、不断言 SQL 文本）。v15 形态由
``tests/_migration_history.py`` 的留档搭出来（复用 ``test_schema_migration._build_library``）。

「恢复备份后**旧代码**能打开」这一条由 ``app/specs/issue-157-migration/drill.py`` 用 ``2aebc3f`` 的
旧代码树取证；本文件验证的是恢复出来的库仍是 v15 形态、业务行可读、与迁移前逐字节一致——历史代码
树不进 CI。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from alibot.db import SCHEMA_VERSION

from tests.test_schema_migration import (
    LEDGER_FILE_NAME,
    _backup_dirs,
    _build_library,
    _connect,
    _migrate_argv,
    _run,
    _sha256,
)

MIGRATION_SUCCEEDED = 0

# 迁移链在 15 → 16 与 16 → 17 建出的表（规格 §4.2 / §4.3）。
NEW_TABLES = (
    "team_run",
    "team_assignment",
    "plan_run",
    "plan_task",
    "sop_definition",
    "sop_run",
    "permission_rule",
    "approval_request",
    "mcp_server",
    "skill",
    "remote_consent_scope",
)

CONSENT_ACKNOWLEDGED_AT = "2026-10-14T09:00:10Z"


def _seed_v15_extra_rows(db_path: Path) -> None:
    """v15 库里补上迁移要解释的行：两张被新默认值 / 复制规则覆盖的表，外加一条任务线程。

    ``_build_library`` 的最小业务数据不含 ``agent_config`` 与 ``remote_consent``；这两张表正是
    v17 的旧行默认值与一次性复制所针对的对象。任务行用来证明「恢复备份后业务面还在」不止频道与消息。
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_config(member_id, role, model_id, system_prompt, enabled, "
            "created_at, updated_at) VALUES ('mem_x', 'planner', 'deepseek-v4', '你是一个助手', 1, ?, ?)",
            ("2026-10-14T09:00:01Z", "2026-10-14T09:00:01Z"),
        )
        conn.execute(
            "INSERT INTO remote_consent(provider, acknowledged_at) VALUES ('anthropic', ?)",
            (CONSENT_ACKNOWLEDGED_AT,),
        )
        conn.execute(
            "INSERT INTO target(id, kind, created_at) VALUES ('task_t1', 'task', '2026-10-14T09:00:05Z')"
        )
        conn.execute(
            "INSERT INTO task(id, target_id, channel_id, title, status, assignee_member_id, "
            "created_at, updated_at) VALUES ('task_1', 'task_t1', 'ch_dev', '修复登录', 'todo', "
            "'mem_x', '2026-10-14T09:00:05Z', '2026-10-14T09:00:05Z')"
        )
    finally:
        conn.close()


def _version(db_path: Path) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        assert row is not None
        return str(row[0])
    finally:
        conn.close()


def test_v15_dry_run_keeps_the_library_byte_identical_and_reports_both_steps(tmp_path: Path) -> None:
    """预演列出 15 → 16 / 16 → 17 与将改动的行数；原库 sha256 前后一致、不建备份、不写台账。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 15)
    _seed_v15_extra_rows(db_path)
    digest_before = _sha256(db_path)

    completed = _run("--migrate", "--dry-run", "--data-dir", str(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    report = completed.stdout
    assert "15 → 16  agentscope-2 team/plan/sop expand  改动 0 行" in report
    assert "16 → 17  agentscope-2 workspace/permission/mcp/skill expand  改动 1 行" in report
    assert report.count(digest_before) == 2, "报告必须给出原库 sha256 的前后两个读数"
    assert _sha256(db_path) == digest_before, "预演不得改动原库一个字节"
    assert _backup_dirs(data_dir) == [], "预演不建备份"
    assert not (data_dir / LEDGER_FILE_NAME).exists(), "预演不写审计台账"
    assert _version(db_path) == "15", "预演不改库里的版本号"


def test_v15_to_v17_sets_old_row_defaults_copies_consent_and_the_backup_restores(tmp_path: Path) -> None:
    """真迁移：推进到本代码版本、旧行默认值、旧确认行复制、新表可查、备份可恢复到原 v15 业务面。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 15)
    _seed_v15_extra_rows(db_path)
    digest_before = _sha256(db_path)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    # 终点从 ``SCHEMA_VERSION`` 派生（ADR-0015 §三）：下一次合法 bump 不该让这条演练假红。
    assert _version(db_path) == str(SCHEMA_VERSION)
    assert f"schema_version = {SCHEMA_VERSION}" in completed.stdout

    conn = _connect(db_path)
    try:
        channel = conn.execute(
            "SELECT mode, team_leader_member_id, workspace_root, workspace_backend, permission_mode "
            "FROM channel WHERE id = 'ch_dev'"
        ).fetchone()
        assert channel is not None
        assert tuple(channel) == ("autonomous", None, None, "local", "ask-writes")

        agent = conn.execute(
            "SELECT role, auto_claim, workspace_backend, permission_mode, allow_local_shell, docker_image "
            "FROM agent_config WHERE member_id = 'mem_x'"
        ).fetchone()
        assert agent is not None
        assert tuple(agent) == ("planner", 1, None, None, 0, None), (
            "存量 auto_claim = 0 由 19 → 20 一次性回填为 1（#195 / ADR-0027）"
        )

        consent = conn.execute(
            "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope"
        ).fetchall()
        assert [tuple(row) for row in consent] == [("model", "anthropic", CONSENT_ACKNOWLEDGED_AT)]
        assert conn.execute("SELECT COUNT(*) FROM remote_consent").fetchone()[0] == 1, "旧表不删"

        for table in NEW_TABLES:
            expected = 1 if table == "remote_consent_scope" else 0
            assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == expected, table

        assert conn.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 2
        assert conn.execute("SELECT used FROM target_budget WHERE target_id = 'ch_dev'").fetchone()[0] == 3
    finally:
        conn.close()

    backups = _backup_dirs(data_dir)
    assert len(backups) == 1, backups
    backup_db = backups[0] / "alibot.db"
    assert _sha256(backup_db) == digest_before, "备份就是迁移前的整份库"

    ledger = json.loads((data_dir / LEDGER_FILE_NAME).read_text(encoding="utf-8").splitlines()[0])
    assert (ledger["from"], ledger["to"], ledger["result"]) == (15, SCHEMA_VERSION, "success")
    assert ledger["backup_path"] == str(backups[0])

    restored_dir = tmp_path / "restored"
    shutil.copytree(backups[0], restored_dir)
    restored_db = restored_dir / "alibot.db"
    assert _sha256(restored_db) == digest_before
    assert _version(restored_db) == "15"
    restored = _connect(restored_db)
    try:
        assert restored.execute("SELECT name FROM channel WHERE id = 'ch_dev'").fetchone()[0] == "dev"
        bodies = [row[0] for row in restored.execute("SELECT body FROM message ORDER BY seq")]
        assert bodies == ["第一条", "第二条"]
        assert restored.execute("SELECT title FROM task WHERE id = 'task_1'").fetchone()[0] == "修复登录"
    finally:
        restored.close()
