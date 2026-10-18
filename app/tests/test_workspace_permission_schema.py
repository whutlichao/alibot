"""#139 的库形态步骤：``SCHEMA_VERSION`` 16 → 17（workspace / permission / MCP / Skill / approval）。

判据来源：规格 §4.1 / §4.3、ADR-0015（迁移显式、只前进、链连续）、ADR-0021（workspace 是频道级项目
边界、权限三档、MCP/Skill 是配置、审批是 HITL 记录）。

接缝 = 规格 T2：真实子进程 ``python -m alibot --migrate [--dry-run] --data-dir <目录>`` + 磁盘上的
真实库。断言只落在外部可观察面：退出码、报告里的步骤行 / 改动行数 / sha256 读数、迁移后库的读数
（版本号 / 旧行默认值 / 表与索引的存在 / CHECK 与 UNIQUE 的行为 / 旧表的保留与复制）。

历史 v16 形态的库由 ``tests/_migration_history.py`` 的留档搭出来（与既有迁移测试同一个 builder）；
v17 的 DDL 留档也落在那里，供后续票直接复用。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest
from alibot.db import SCHEMA_VERSION

# v16 形态库的搭建与 CLI 调用复用既有迁移测试的文件级外部面（不 import 迁移实现内部）。
from tests.test_schema_migration import (
    _backup_dirs,
    _build_library,
    _fresh_current_library,
    _migrate_argv,
    _run,
    _shape,
)

MIGRATION_SUCCEEDED = 0

# 本票的起点：v16 是 #138 落地后的读数，`16 → 17` 由本票引入。
SOURCE_VERSION = 16

NEW_TABLES = (
    "permission_rule",
    "approval_request",
    "mcp_server",
    "skill",
    "remote_consent_scope",
)

# 报告里的版本步骤行（缩进两格；报告头部那行没有缩进，不会命中）与 sha256 读数。
STEP_LINE_RE = re.compile(r"^ {2}(\d+) → (\d+) {2}", re.MULTILINE)
STEP_CHANGED_RE = re.compile(r"^ {2}16 → 17 {2}.*?改动 (\d+) 行", re.MULTILINE)
READING_RE = re.compile(r"原库文件 sha256（预演[前后]）：([0-9a-f]{64})")

NOW = "2026-10-18T09:00:00Z"
CONSENT_ROWS = (
    ("ollama", "2026-10-17T01:00:00Z"),
    ("openai", "2026-10-17T02:00:00Z"),
)
# 迁移后应有的新表读数：每条旧行变成一条 ``('model', provider, acknowledged_at)``。
SCOPE_ROWS = [("model", provider, acknowledged_at) for provider, acknowledged_at in CONSENT_ROWS]


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


def _seed_legacy_rows(db_path: Path) -> None:
    """在 v16 库里放一份旧配置行与两条旧 ``remote_consent`` 行（升级前的存量面）。"""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_config(member_id, role, model_id, created_at, updated_at) "
            "VALUES ('mem_x', 'dev', 'qwen3', ?, ?)",
            ("2026-10-14T09:00:05Z", "2026-10-14T09:00:05Z"),
        )
        for provider, acknowledged_at in CONSENT_ROWS:
            conn.execute(
                "INSERT INTO remote_consent(provider, acknowledged_at) VALUES (?, ?)",
                (provider, acknowledged_at),
            )
    finally:
        conn.close()


def _migrated_library(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_rows(db_path)
    completed = _migrate(data_dir)
    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    return db_path


# ─── 验收 1 / 4：版本号、链的这一步、旧行默认值 ─────────────────────────────
def test_a_v16_library_reaches_v17_and_old_rows_keep_the_documented_defaults(tmp_path: Path) -> None:
    """v16 库被接受：报告里有 16 → 17、终点等于本代码版本；旧行按规格默认值解释。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_rows(db_path)

    completed = _migrate(data_dir)
    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    steps = [(int(frm), int(to)) for frm, to in STEP_LINE_RE.findall(completed.stdout)]
    assert (SOURCE_VERSION, SOURCE_VERSION + 1) in steps, completed.stdout
    assert steps[-1][1] == SCHEMA_VERSION, completed.stdout
    assert SCHEMA_VERSION >= 17

    conn = _connect(db_path)
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        assert version[0] == str(SCHEMA_VERSION)

        # 旧频道：根未配置、后端 local、权限档 ask-writes（规格 §4.1 / §4.3）。
        row = conn.execute(
            "SELECT workspace_root, workspace_backend, permission_mode FROM channel WHERE id = 'ch_dev'"
        ).fetchone()
        assert row == (None, "local", "ask-writes"), f"旧频道的 workspace 默认值不对：{row}"

        # 旧 Agent：后端与权限档跟随频道（NULL）、本地 shell 关闭、镜像未配置。
        row = conn.execute(
            "SELECT workspace_backend, permission_mode, allow_local_shell, docker_image "
            "FROM agent_config WHERE member_id = 'mem_x'"
        ).fetchone()
        assert row == (None, None, 0, None), f"旧 Agent 的 workspace 默认值不对：{row}"
    finally:
        conn.close()

    # 既有列的类型 / 默认值不回填：v16 的种子行逐字还在（频道名与描述没被改写）。
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT name, description, mode FROM channel WHERE id = 'ch_dev'").fetchone()
        assert row == ("dev", "开发频道", "autonomous")
    finally:
        conn.close()


# ─── 验收 5：预演零字节改动 + 两个 sha256 读数 ───────────────────────────────
def test_dry_run_keeps_the_original_library_byte_identical(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_rows(db_path)
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


# ─── 验收 3：五张新表 / 新列 / 两处路径一致 ─────────────────────────────────
def test_the_new_tables_and_columns_match_a_fresh_library(tmp_path: Path) -> None:
    before_dir = tmp_path / "before"
    before_path = _build_library(before_dir, SOURCE_VERSION)
    before = _shape(before_path)
    assert "permission_rule" not in before["tables"], "v16 库不该已经有本票的新表"
    assert "workspace_root" not in before["columns"]["channel"], "v16 库不该已经有本票的新列"
    assert "allow_local_shell" not in before["columns"]["agent_config"], "v16 库不该已经有本票的新列"

    db_path = _migrated_library(tmp_path)
    fresh_path = _fresh_current_library(tmp_path, "fresh")
    migrated = _shape(db_path)
    fresh = _shape(fresh_path)

    for table in NEW_TABLES:
        assert table in migrated["tables"], f"迁移后缺表：{table}"
        assert table in fresh["tables"], f"全量建库缺表：{table}"
        assert migrated["columns"][table] == fresh["columns"][table], f"{table} 的列与全量建库不一致"
    for table in ("channel", "agent_config"):
        assert migrated["columns"][table] == fresh["columns"][table], f"{table} 的列与全量建库不一致"
    assert migrated["implicit_indexes"] == fresh["implicit_indexes"], "隐式唯一约束与全量建库不一致"
    assert migrated["version"] == fresh["version"] == str(SCHEMA_VERSION)

    # 旧表保留（ADR-0015 的兼容面纪律）：迁移后仍可读，两条旧行逐字还在。
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT provider, acknowledged_at FROM remote_consent ORDER BY provider"
        ).fetchall()
        assert rows == sorted(CONSENT_ROWS), f"旧 remote_consent 行被改写或删除了：{rows}"
    finally:
        conn.close()


# ─── 验收 3：CHECK / UNIQUE / 外键与规格 §4.3 一致 ─────────────────────────
def test_workspace_permission_and_registry_constraints_are_enforced(tmp_path: Path) -> None:
    db_path = _migrated_library(tmp_path)
    conn = _connect(db_path)
    try:
        # channel 的 workspace 档位
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE channel SET workspace_backend = 'bogus' WHERE id = 'ch_dev'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE channel SET permission_mode = 'bogus' WHERE id = 'ch_dev'")
        conn.execute(
            "UPDATE channel SET workspace_root = '/tmp/project', workspace_backend = 'docker', "
            "permission_mode = 'trusted-writes' WHERE id = 'ch_dev'"
        )

        # agent_config 的覆盖档位与本地 shell 开关
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE agent_config SET workspace_backend = 'bogus' WHERE member_id = 'mem_x'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE agent_config SET permission_mode = 'bypass' WHERE member_id = 'mem_x'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE agent_config SET allow_local_shell = 2 WHERE member_id = 'mem_x'")
        conn.execute(
            "UPDATE agent_config SET workspace_backend = 'local', permission_mode = 'ask-writes', "
            "allow_local_shell = 1, docker_image = 'alibot-workspace:test' WHERE member_id = 'mem_x'"
        )

        # permission_rule：两处取值白名单
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO permission_rule(id, scope_kind, scope_id, tool_name, behavior, created_at) "
                "VALUES ('pr_bad', 'org', 'ch_dev', 'Write', 'allow', ?)",
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO permission_rule(id, scope_kind, scope_id, tool_name, behavior, created_at) "
                "VALUES ('pr_bad', 'channel', 'ch_dev', 'Write', 'maybe', ?)",
                (NOW,),
            )
        conn.execute(
            "INSERT INTO permission_rule(id, scope_kind, scope_id, tool_name, pattern, behavior, created_at) "
            "VALUES ('pr_ok', 'channel', 'ch_dev', 'Write', 'src/*', 'ask', ?)",
            (NOW,),
        )

        # approval_request：状态白名单 + target 外键
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO approval_request(reply_id, target_id, member_id, tool_calls, state, requested_at) "
                "VALUES ('ap_bad', 'ch_dev', 'mem_x', '[]', 'waiting', ?)",
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO approval_request(reply_id, target_id, member_id, tool_calls, state, requested_at) "
                "VALUES ('ap_fk', 'ch_missing', 'mem_x', '[]', 'pending', ?)",
                (NOW,),
            )
        conn.execute(
            "INSERT INTO approval_request(reply_id, target_id, member_id, tool_calls, state, requested_at) "
            "VALUES ('ap_ok', 'ch_dev', 'mem_x', '[]', 'pending', ?)",
            (NOW,),
        )

        # mcp_server：transport 白名单 / enabled 白名单 / (scope_kind, scope_id, name) 唯一
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO mcp_server(id, name, transport, scope_kind, scope_id, created_at, updated_at) "
                "VALUES ('mcp_bad', 'github', 'websocket', 'channel', 'ch_dev', ?, ?)",
                (NOW, NOW),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO mcp_server(id, name, transport, enabled, scope_kind, scope_id, created_at, "
                "updated_at) VALUES ('mcp_bad', 'github', 'stdio', 2, 'channel', 'ch_dev', ?, ?)",
                (NOW, NOW),
            )
        conn.execute(
            "INSERT INTO mcp_server(id, name, transport, command, env_names, scope_kind, scope_id, "
            "created_at, updated_at) VALUES ('mcp_1', 'github', 'stdio', 'npx', '[\"TOKEN\"]', "
            "'channel', 'ch_dev', ?, ?)",
            (NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO mcp_server(id, name, transport, scope_kind, scope_id, created_at, updated_at) "
                "VALUES ('mcp_dup', 'github', 'stdio', 'channel', 'ch_dev', ?, ?)",
                (NOW, NOW),
            )

        # skill：enabled 白名单 / (scope_kind, scope_id, name) 唯一
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO skill(id, name, source_path, content_hash, enabled, scope_kind, scope_id, "
                "created_at, updated_at) VALUES ('sk_bad', 'review', 'skills/review', 'sha', 2, "
                "'agent', 'mem_x', ?, ?)",
                (NOW, NOW),
            )
        conn.execute(
            "INSERT INTO skill(id, name, source_path, content_hash, scope_kind, scope_id, created_at, "
            "updated_at) VALUES ('sk_1', 'review', 'skills/review', 'sha', 'agent', 'mem_x', ?, ?)",
            (NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO skill(id, name, source_path, content_hash, scope_kind, scope_id, created_at, "
                "updated_at) VALUES ('sk_dup', 'review', 'skills/other', 'sha2', 'agent', 'mem_x', ?, ?)",
                (NOW, NOW),
            )

        # remote_consent_scope：kind 白名单 + 复合主键
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
                "VALUES ('registry', 'ghcr.io', ?)",
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
                "VALUES ('model', 'ollama', ?)",
                (NOW,),
            )
        conn.execute(
            "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
            "VALUES ('mcp', 'github', ?)",
            (NOW,),
        )
    finally:
        conn.close()


# ─── 验收 4：旧 remote_consent 复制成 model scope（计数与幂等） ─────────────
def test_legacy_remote_consent_rows_become_model_scopes_once_and_the_step_is_idempotent(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SOURCE_VERSION)
    _seed_legacy_rows(db_path)

    completed = _migrate(data_dir)
    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    match = STEP_CHANGED_RE.search(completed.stdout)
    assert match is not None, completed.stdout
    assert match.group(1) == str(len(CONSENT_ROWS)), f"复制计数不对：{completed.stdout}"

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope ORDER BY scope_id"
        ).fetchall()
        assert rows == SCOPE_ROWS, rows
        # 旧表保留，且旧行没有被删改。
        legacy = conn.execute(
            "SELECT provider, acknowledged_at FROM remote_consent ORDER BY provider"
        ).fetchall()
        assert legacy == sorted(CONSENT_ROWS), legacy

        # 把读数退回 16 再跑一次同一步：复制是幂等的（改动 0 行、不重复、不覆盖）。
        conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(SOURCE_VERSION),))
    finally:
        conn.close()

    # 把读数退回 16 再跑一次同一步：复制是幂等的（改动 0 行、不重复、不覆盖）。
    # 两次迁移可能落在同一个 UTC 秒里，备份目录名会撞上；先把上一次备份移开（它不再参与断言）。
    for backup in _backup_dirs(data_dir):
        backup.rename(tmp_path / f"first-{backup.name}")
    replay = _migrate(data_dir)
    assert replay.returncode == MIGRATION_SUCCEEDED, replay.stderr
    match = STEP_CHANGED_RE.search(replay.stdout)
    assert match is not None, replay.stdout
    assert match.group(1) == "0", f"重放不是幂等的：{replay.stdout}"

    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM remote_consent_scope").fetchone()[0] == len(CONSENT_ROWS)
        rows = conn.execute(
            "SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope ORDER BY scope_id"
        ).fetchall()
        assert rows == SCOPE_ROWS, rows
    finally:
        conn.close()
