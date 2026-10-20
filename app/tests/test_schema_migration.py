"""迁移引擎与整条逐版链（issue #90 / #91）。

规格正本：``app/specs/issue-87-spec.md``（T2：**唯一测试接缝 = CLI 进程入口**）。

只经**真实子进程**取证：``python -m alibot --migrate --data-dir <目录>``，配一个磁盘上的真实数据
目录 fixture；断言只落在**外部可观察面**——退出码、stdout / stderr 报告、数据目录与库的终态、
文件 sha256。不 import 实现内部函数、不断言私有函数或 SQL 文本（T1）。

历史逐版 fixture 由 ``tests/_migration_history.py`` 提供（从 git 历史里各版 ``SCHEMA`` 抽取生成）：
本文件据此从 v3 基线搭出「该版形态」的库，再让 ``--migrate`` 沿链推进到终态——这正是验收要求的
「对 ``3``…``11`` 的每一个源版本各造一个库并迁移」。

**终态判据（D7）**用「新库对照 + 结构断言」取证：另起一个由**本代码**初始化的当前版本库，比较
两边的表 / 列 / 索引 / 触发器；三张旧表是规格明写的**唯一例外**（回退面），单独断言。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alibot.db import DATABASE_FILE_NAME, MIN_SUPPORTED_SOURCE_VERSION, SCHEMA_VERSION, Database

from tests._migration_history import OBJECTS, VERSION_OBJECTS
from tests.conftest import backend

APP_DIR = Path(__file__).resolve().parents[1]

# 三条入口的对外契约码（写**字面量**而不是 import 实现常量：断言的是对外契约里的码）。
MIGRATION_SUCCEEDED = 0
DATA_NOT_ACCEPTED = 3
MIGRATION_FAILED = 5

# 迁移后**原样保留**的三张旧表（规格 D12：回退面，唯一例外）。
LEGACY_TABLES = ("channel_member", "channel_read", "channel_budget")

# 迁移审计台账的文件名（规格 D8 的默认落点）。
LEDGER_FILE_NAME = "schema-migrations.jsonl"

# 历史各版新增、但 SQLite 无法用 ``CREATE`` 表达的列（用带约束的 ``ADD COLUMN`` 补）。
# 取值口径按规格 D6：``theme`` 缺省 ``'system'``、``auto_claim`` 缺省 ``0``、``claimed_at`` 空。
# ``auto_claim`` 的 0 是**历史形态**（v12 引入时的默认值）；v20 的默认 flip 由 ``19 → 20`` 迁移
# 重建表时落成 1，搭 v12…v19 形态的库仍然必须按当时的 DDL 补这一列。
_COLUMN_DDL: dict[str, str] = {
    "dm_peer_member_id": 'ALTER TABLE "target" ADD COLUMN dm_peer_member_id TEXT REFERENCES member(id)',
    "task_ref": 'ALTER TABLE "message" ADD COLUMN task_ref TEXT',
    "theme": "ALTER TABLE \"workspace_settings\" ADD COLUMN theme TEXT NOT NULL DEFAULT 'system' "
    "CHECK (theme IN ('light', 'dark', 'system'))",
    "claimed_at": 'ALTER TABLE "task" ADD COLUMN claimed_at TEXT',
    "auto_claim": 'ALTER TABLE "agent_config" ADD COLUMN auto_claim INTEGER NOT NULL DEFAULT 0 '
    "CHECK (auto_claim IN (0, 1))",
}

# 「该版新增了哪些列」：搭 vN 形态的库时把 ``<= N`` 的这些列依次补上（表由该版的对象集给出）。
_COLUMN_ADDITIONS: dict[int, tuple[str, ...]] = {
    5: ("task_ref",),
    6: ("dm_peer_member_id",),
    10: ("theme",),
    12: ("claimed_at", "auto_claim"),
}

_ROW_COUNT_SQL: dict[str, str] = {
    "target": 'SELECT COUNT(*) FROM "target"',
    "target_member": 'SELECT COUNT(*) FROM "target_member"',
    "target_read": 'SELECT COUNT(*) FROM "target_read"',
    "target_budget": 'SELECT COUNT(*) FROM "target_budget"',
    "channel": 'SELECT COUNT(*) FROM "channel"',
    "channel_member": 'SELECT COUNT(*) FROM "channel_member"',
    "channel_read": 'SELECT COUNT(*) FROM "channel_read"',
    "channel_budget": 'SELECT COUNT(*) FROM "channel_budget"',
    "message": 'SELECT COUNT(*) FROM "message"',
}


# ─── CLI 与文件级辅助 ────────────────────────────────────────────────────────
def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """真实子进程跑 ``python -m alibot <args>``（``PYTHONPATH`` 指向本 worktree 的 ``app/``）。"""
    return subprocess.run(
        [sys.executable, "-m", "alibot", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
        cwd=str(APP_DIR),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup_dirs(data_dir: Path) -> list[Path]:
    """数据目录**同级**里以 ``<数据目录名>-backup-`` 开头的副本（规格 S6 的命名）。"""
    if not data_dir.parent.exists():
        return []
    return sorted(
        path for path in data_dir.parent.iterdir() if path.name.startswith(f"{data_dir.name}-backup-")
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _shape(db_path: Path) -> dict:
    """库的形态快照：表 / 每表列 / 显式索引 / 隐式唯一约束 / 触发器 / 版本（只读）。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = sorted(
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        columns = {
            table: sorted(str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")'))
            for table in tables
        }
        indexes = sorted(
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")
        )
        # 隐式唯一约束（``UNIQUE(...)`` / ``PRIMARY KEY`` 由 SQLite 自动建的 autoindex）也是 D7
        # 明写要齐备的对象：它们落在 ``sql IS NULL`` 那一档，必须单独取。
        implicit_indexes = sorted(
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NULL")
        )
        triggers = sorted(
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        )
        version = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return {
            "tables": tables,
            "columns": columns,
            "indexes": indexes,
            "implicit_indexes": implicit_indexes,
            "triggers": triggers,
            "version": None if version is None else str(version[0]),
        }
    finally:
        conn.close()


def _row_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        present = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        return {
            table: int(conn.execute(_ROW_COUNT_SQL[table]).fetchone()[0])
            for table in present
            if table in _ROW_COUNT_SQL
        }
    finally:
        conn.close()


def _new_member(conn: sqlite3.Connection, member_id: str, kind: str, name: str) -> None:
    conn.execute(
        "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) VALUES (?, ?, ?, ?, ?)",
        (member_id, kind, name, 1 if kind == "agent" else 4, "2026-10-14T09:00:00Z"),
    )


def _seed_rows(conn: sqlite3.Connection) -> None:
    """搭库用的最小业务数据：一个频道、两个成员、两条消息、一份「已用 3 跳」的预算。"""
    tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    _new_member(conn, "owner", "owner", "Owner")
    _new_member(conn, "mem_x", "agent", "小明")
    conn.execute(
        "INSERT INTO channel(id, name, description, created_at) VALUES ('ch_dev', 'dev', '开发频道', ?)",
        ("2026-10-14T09:00:02Z",),
    )
    if "target" in tables:
        conn.execute(
            "INSERT INTO target(id, kind, created_at) VALUES ('ch_dev', 'channel', ?)",
            ("2026-10-14T09:00:02Z",),
        )
        conn.execute("INSERT INTO target_member(target_id, member_id) VALUES ('ch_dev', 'owner')")
        conn.execute("INSERT INTO target_member(target_id, member_id) VALUES ('ch_dev', 'mem_x')")
        conn.execute(
            "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES ('owner', 'ch_dev', 1)"
        )
        conn.execute(
            "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES ('mem_x', 'ch_dev', 2)"
        )
        conn.execute("INSERT INTO target_budget(target_id, epoch_seq, used) VALUES ('ch_dev', 1, 3)")
    if "channel_member" in tables:
        conn.execute("INSERT INTO channel_member(channel_id, member_id) VALUES ('ch_dev', 'owner')")
        conn.execute("INSERT INTO channel_member(channel_id, member_id) VALUES ('ch_dev', 'mem_x')")
        conn.execute(
            "INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES ('owner', 'ch_dev', 1)"
        )
        conn.execute(
            "INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES ('mem_x', 'ch_dev', 2)"
        )
        conn.execute("INSERT INTO channel_budget(channel_id, epoch_seq, used) VALUES ('ch_dev', 1, 3)")
    conn.execute(
        "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at) "
        "VALUES ('msg_1', 'ch_dev', 1, 'owner', 'human', '第一条', ?)",
        ("2026-10-14T09:00:03Z",),
    )
    conn.execute(
        "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at) "
        "VALUES ('msg_2', 'ch_dev', 2, 'mem_x', 'agent', '第二条', ?)",
        ("2026-10-14T09:00:04Z",),
    )
    if "workspace_settings" in tables:
        conn.execute(
            "INSERT INTO workspace_settings(id, locale, message_density, updated_at) "
            "VALUES (1, 'zh-CN', 'comfortable', ?)",
            ("2026-10-14T09:00:05Z",),
        )


def _add_historical_column(conn: sqlite3.Connection, ddl: str) -> None:
    """按 ``ALTER TABLE … ADD COLUMN`` 补一条历史新增列；列已在表里时跳过。

    ``_COLUMN_ADDITIONS`` 是「v3 基线 + 逐版补列」的留档路线；v17 起留档也能给出**整表 DDL**
    （``agent_config_v17`` 已带 ``auto_claim``）。这条守卫让两种留档形态共存，而不必把同一列写两遍。
    """
    match = re.search(r'ALTER TABLE "([^"]+)" ADD COLUMN "?([A-Za-z_][A-Za-z0-9_]*)"?', ddl)
    assert match is not None, f"认不出的列 DDL：{ddl}"
    table, column = match.groups()
    existing = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if column not in existing:
        conn.execute(ddl)


def _build_library(data_dir: Path, version: int) -> Path:
    """在 ``data_dir`` 里搭一个「``version`` 版形态」、已有业务数据的库；返回库文件路径。

    形态来源是 ``tests/_migration_history.py``（从 git 历史各版的 ``SCHEMA`` 抽取）：先建该版出现
    过的全部对象，再补 SQLite 无法用 ``CREATE`` 表达的列，最后灌入最小业务数据并钉上版本号。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "attachments").mkdir(exist_ok=True)
    (data_dir / "attachments" / "probe.bin").write_bytes(b"attachment-bytes")
    db_path = data_dir / DATABASE_FILE_NAME
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        objects = VERSION_OBJECTS[version]
        # 建对象分三轮：表 → 该版新增的列 → 索引 / 触发器。索引可能引用后加的列
        # （例如 v6 起的 ``idx_target_dm_peer_unique``），所以列必须先补齐。
        for name in objects:
            if OBJECTS[name].startswith("CREATE TABLE"):
                conn.execute(OBJECTS[name])
        for introduced in range(3, version + 1):
            for column in _COLUMN_ADDITIONS.get(introduced, ()):
                _add_historical_column(conn, _COLUMN_DDL[column])
        for name in objects:
            if "INDEX" in OBJECTS[name].splitlines()[0]:
                conn.execute(OBJECTS[name])
        for name in objects:
            if OBJECTS[name].startswith("CREATE TRIGGER"):
                conn.execute(OBJECTS[name])
        _seed_rows(conn)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
    finally:
        conn.close()
    return db_path


def _migrate_argv(data_dir: Path) -> list[str]:
    return ["--migrate", "--data-dir", str(data_dir)]


def _add_duplicate_dm_peers(db_path: Path) -> None:
    """造 ``5 → 6`` 的唯一性收紧冲突：两个 ``kind='dm'`` target 指向同一个 peer。"""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        present = {str(row[1]) for row in conn.execute('PRAGMA table_info("target")')}
        if "dm_peer_member_id" not in present:
            conn.execute(_COLUMN_DDL["dm_peer_member_id"])
        conn.execute(
            "INSERT INTO target(id, kind, dm_peer_member_id, created_at) "
            "VALUES ('dm_a', 'dm', 'mem_x', '2026-10-14T09:00:06Z')"
        )
        conn.execute(
            "INSERT INTO target(id, kind, dm_peer_member_id, created_at) "
            "VALUES ('dm_b', 'dm', 'mem_x', '2026-10-14T09:00:07Z')"
        )
    finally:
        conn.close()


def _fresh_current_library(tmp_path: Path, name: str) -> Path:
    """由**本代码**初始化出来的当前版本库（终态对照物）。"""
    data_dir = tmp_path / name
    with backend(data_dir) as client:
        assert client.get("/api/health").json()["status"] == "ok"
    return data_dir / DATABASE_FILE_NAME


def _assert_terminal_shape(migrated_path: Path, fresh_path: Path) -> None:
    """D7：迁移后的库在表 / 列 / 索引 / 触发器上齐备且与「本代码初始化的库」一致。

    三张旧表（``channel_member`` / ``channel_read`` / ``channel_budget``）是规格明写的**唯一例外**，
    因此这里只要求迁移库是「当前形态」的**超集**：当前 ``SCHEMA`` 声明的每个对象都在，且当前形态的
    表的列集与迁移库逐字相同。
    """
    migrated = _shape(migrated_path)
    fresh = _shape(fresh_path)
    assert migrated["version"] == str(SCHEMA_VERSION)
    assert migrated["version"] == fresh["version"]
    for table in fresh["tables"]:
        assert table in migrated["tables"], f"终态缺当前形态的表：{table}"
        assert migrated["columns"][table] == fresh["columns"][table], f"表 {table} 的列不一致"
    for index in fresh["indexes"]:
        assert index in migrated["indexes"], f"终态缺当前形态的索引：{index}"
    for index in fresh["implicit_indexes"]:
        assert index in migrated["implicit_indexes"], f"终态缺当前形态的隐式唯一约束：{index}"
    for trigger in fresh["triggers"]:
        assert trigger in migrated["triggers"], f"终态缺当前形态的触发器：{trigger}"
    assert "attachment_no_rebind" not in migrated["triggers"], "旧触发器不得与新触发器并存"


# ─── 端到端：v3 ⇒ 终态，且本代码的 app 能打开它（验收 1 / 3 / 4） ────────────
def test_v3_library_migrates_to_terminal_state_and_the_app_can_open_it(tmp_path: Path) -> None:
    """v3 库跑 ``--migrate`` ⇒ 退出 0、版本 = 本代码常量、此后本代码的 app 能正常打开它。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _shape(data_dir / DATABASE_FILE_NAME)["version"] == str(SCHEMA_VERSION)

    # 本票存在的理由：迁移后**本代码的 app 能正常打开它**（不是「读数是 12」而已）。
    database = Database(data_dir)
    try:
        assert [channel["id"] for channel in database.list_channels()] == ["ch_dev"]
    finally:
        database.close()
    with backend(data_dir) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/channels").json()["channels"][0]["id"] == "ch_dev"


def test_migration_preserves_scope_data_and_keeps_the_old_tables(tmp_path: Path) -> None:
    """数据保真：影子行与新表按 ``channel_id → target_id`` 一一对应，三张旧表行数不变。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    before = _row_counts(db_path)

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT id, kind FROM target").fetchall() == [("ch_dev", "channel")]
        assert {tuple(row) for row in conn.execute("SELECT * FROM target_member")} == {
            ("ch_dev", "owner"),
            ("ch_dev", "mem_x"),
        }
        assert {tuple(row) for row in conn.execute("SELECT * FROM target_read")} == {
            ("owner", "ch_dev", 1),
            ("mem_x", "ch_dev", 2),
        }
        # 预算 `used` 是已耗尽预算的正本：搬迁必须保真，不得归零。
        assert conn.execute("SELECT target_id, epoch_seq, used FROM target_budget").fetchall() == [
            ("ch_dev", 1, 3)
        ]
    finally:
        conn.close()

    after = _row_counts(db_path)
    for table in (*LEGACY_TABLES, "channel", "message"):
        assert after[table] == before[table], f"旧表 / 既有表 {table} 的行数不得变化"


def test_terminal_shape_matches_a_freshly_initialized_library(tmp_path: Path) -> None:
    """D7：迁移后的库与**本代码初始化**的当前版本库在表 / 列 / 索引 / 触发器上一致。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    _assert_terminal_shape(data_dir / DATABASE_FILE_NAME, _fresh_current_library(tmp_path, "fresh"))


def test_theme_and_auto_claim_checks_are_present_after_migration(tmp_path: Path) -> None:
    """列级 ``CHECK`` 一起落库（只补列会让库级约束与 ``SCHEMA`` 文本不一致）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    conn = _connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO workspace_settings(id, locale, message_density, theme, updated_at) "
                "VALUES (2, 'zh-CN', 'comfortable', 'system', 'x')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE workspace_settings SET theme = 'neon' WHERE id = 1")
        # 迁移后直接从 DDL 默认值取 auto_claim = 1（#195 / ADR-0027 的默认 flip），
        # 且列级 CHECK 在库级生效。
        conn.execute("INSERT INTO agent_config(member_id, created_at, updated_at) VALUES ('mem_x', 'x', 'x')")
        assert (
            conn.execute("SELECT auto_claim FROM agent_config WHERE member_id = 'mem_x'").fetchone()[0] == 1
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE agent_config SET auto_claim = 7 WHERE member_id = 'mem_x'")
    finally:
        conn.close()


def test_v19_upgrade_flips_the_default_and_backfills_existing_auto_claim_rows(tmp_path: Path) -> None:
    """``19 → 20``（#195 / ADR-0027）：存量 0 → 1、行与其它列不变、新插入拿到默认 1。

    读数取自 CLI 报告与库内实际行：报告里该步「改动 N 行」必须等于库内由 0 变 1 的行数。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 19)
    columns = (
        "member_id, role, model_id, system_prompt, enabled, auto_claim, workspace_backend, "
        "permission_mode, allow_local_shell, docker_image, created_at, updated_at"
    )
    conn = _connect(db_path)
    try:
        _new_member(conn, "mem_y", "agent", "小红")
        conn.execute(
            "INSERT INTO agent_config(member_id, role, model_id, system_prompt, enabled, auto_claim, "
            "created_at, updated_at) VALUES "
            "('mem_x', '前端', 'ollama:qwen2.5', '提示', 1, 0, "
            "'2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO agent_config(member_id, role, model_id, system_prompt, enabled, auto_claim, "
            "created_at, updated_at) VALUES "
            "('mem_y', '研究', 'ollama:qwen2.5', '另一条提示', 1, 1, "
            "'2026-10-03T00:00:00Z', '2026-10-04T00:00:00Z')"
        )
        before = [
            tuple(row) for row in conn.execute(f"SELECT {columns} FROM agent_config ORDER BY member_id")
        ]
        count_before = conn.execute("SELECT COUNT(*) FROM agent_config").fetchone()[0]
    finally:
        conn.close()
    assert [row[5] for row in before] == [0, 1]

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    reported = re.search(r"^ {2}19 → 20 {2}.*?改动 (\d+) 行", completed.stdout, re.MULTILINE)
    assert reported is not None, completed.stdout
    assert int(reported.group(1)) == 1, "报告里的改动行数必须等于库里实际由 0 变 1 的行数"

    conn = _connect(db_path)
    try:
        after = [tuple(row) for row in conn.execute(f"SELECT {columns} FROM agent_config ORDER BY member_id")]
        assert conn.execute("SELECT COUNT(*) FROM agent_config").fetchone()[0] == count_before
        assert [row[5] for row in after] == [1, 1]
        assert [row[:5] + row[6:] for row in after] == [row[:5] + row[6:] for row in before], (
            "除 auto_claim 外的列必须逐行不变（含 updated_at）"
        )
        # 升级库的直接插入也拿到新默认值（重建后的 DDL 默认 1）。
        _new_member(conn, "mem_z", "agent", "新成员")
        conn.execute("INSERT INTO agent_config(member_id, created_at, updated_at) VALUES ('mem_z', 'x', 'x')")
        assert (
            conn.execute("SELECT auto_claim FROM agent_config WHERE member_id = 'mem_z'").fetchone()[0] == 1
        )
    finally:
        conn.close()


def test_foreign_key_constraints_match_the_current_schema_after_migration(tmp_path: Path) -> None:
    """D6 / D7：迁移库的 ``message`` / ``round_outcome`` 外键引用与当前 ``SCHEMA`` 一致。

    ``message.target_id REFERENCES target(id)`` 与 ``round_outcome.target_id REFERENCES target(id)``
    在 v3 表上不存在，只能靠重建补上（SQLite 没有 ``ALTER TABLE ADD CONSTRAINT``）。这条用例从
    **行为**取证：迁移后的库必须像本代码新建的库一样拒绝孤儿 ``target_id``。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    conn = _connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at) "
                "VALUES ('msg_orphan', 'tgt_missing', 3, 'owner', 'human', 'x', "
                "'2026-10-14T09:00:09Z')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, started_at, "
                "finished_at) VALUES ('ro_orphan', 'owner', 'tgt_missing', 0, 'replied', 'x', 'x')"
            )
    finally:
        conn.close()


def test_workspace_settings_default_row_is_filled_from_any_source_version(tmp_path: Path) -> None:
    """从 ``7`` 及以后起步的链不经过 ``6 → 7``，也要按目标形态补上 ``id = 1`` 缺省行（D7）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 10)
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM workspace_settings")
    finally:
        conn.close()

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    conn = _connect(db_path)
    try:
        assert conn.execute(
            "SELECT id, locale, message_density, theme FROM workspace_settings"
        ).fetchall() == [(1, "zh-CN", "comfortable", "system")]
    finally:
        conn.close()


# ─── 备份（验收 5） ──────────────────────────────────────────────────────────
def test_backup_lands_beside_the_data_dir_with_database_and_attachments(tmp_path: Path) -> None:
    """成功后同级存在 ``<数据目录>-backup-3-to-12-<UTC 时间戳>``，其中同时有库与 ``attachments/``。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    backups = _backup_dirs(data_dir)
    assert len(backups) == 1, backups
    assert backups[0].name.startswith(f"{data_dir.name}-backup-3-to-{SCHEMA_VERSION}-")
    assert (backups[0] / DATABASE_FILE_NAME).is_file()
    assert (backups[0] / "attachments" / "probe.bin").read_bytes() == b"attachment-bytes"


def test_backup_failure_refuses_with_five_and_leaves_the_database_untouched(tmp_path: Path) -> None:
    """让备份目标不可写 ⇒ 退出 5、库 sha256 不变、无半迁移痕迹（S7）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    digest_before = _sha256(db_path)

    tmp_path.chmod(0o500)  # 同级不可写 ⇒ copytree 的目标建不出来
    try:
        completed = _run(*_migrate_argv(data_dir))
    finally:
        tmp_path.chmod(0o700)

    assert completed.returncode == MIGRATION_FAILED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _sha256(db_path) == digest_before, "备份失败不得改动库文件一个字节"
    assert _backup_dirs(data_dir) == [], "不得留下半份备份"
    assert _shape(db_path)["version"] == "3", "不得出现半迁移痕迹"


# ─── 接受范围 = [3, SCHEMA_VERSION]（验收 2 / 11） ──────────────────────────
@pytest.mark.parametrize("version", range(MIN_SUPPORTED_SOURCE_VERSION, SCHEMA_VERSION))
def test_every_source_version_in_the_supported_range_reaches_terminal_state(
    tmp_path: Path, version: int
) -> None:
    """对 ``3``…``11`` 的每一个源版本各造一个库并迁移 ⇒ 都 ``0`` 且到终态（D4 的终态口径）。

    历史 fixture 里读数为 8 / 9 的两个号段从未作为 ``main`` 的读数存在过，读数 10 在 main 上也对应
    过缺 ``reminder`` 的树——这些「读数 ↔ 结构」的歧义都由终态判据（对齐到当前 ``SCHEMA``）吸收。
    """
    data_dir = tmp_path / f"data-v{version}"
    _build_library(data_dir, version)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"v{version} 必须被接受，实际 {completed.returncode}"
        f"（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    _assert_terminal_shape(
        data_dir / DATABASE_FILE_NAME, _fresh_current_library(tmp_path, f"fresh-v{version}")
    )


def test_future_version_is_refused_by_all_three_entry_points_without_touching_the_file(
    tmp_path: Path,
) -> None:
    """未来版本 ⇒ 三条入口都 ``3``、库 sha256 不变（S14：方向没有放宽）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, SCHEMA_VERSION)
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
    finally:
        conn.close()
    digest_before = _sha256(db_path)

    results = {
        "migrate": _run(*_migrate_argv(data_dir)),
        "startup": _run("--data-dir", str(data_dir), "--port", "8799"),
        "repair-targets": _run("--repair-targets", "--data-dir", str(data_dir)),
    }

    assert {result.returncode for result in results.values()} == {DATA_NOT_ACCEPTED}
    assert len({result.stderr for result in results.values()}) == 1
    assert _sha256(db_path) == digest_before


# ─── 审计与报告（验收 7 / 8） ────────────────────────────────────────────────
def test_report_lists_steps_row_counts_and_backup_path(tmp_path: Path) -> None:
    """报告给出实际执行的步骤清单、每步改动的行数与本次备份落点（D8）。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED
    report = completed.stdout
    for step in range(3, SCHEMA_VERSION):
        assert f"{step} → {step + 1}" in report, f"报告缺 {step} → {step + 1} 这一步"
    assert "改动" in report and "行" in report
    assert str(_backup_dirs(data_dir)[0]) in report


def test_audit_ledger_gets_one_append_only_record_and_has_no_http_write_entry(tmp_path: Path) -> None:
    """迁移成功后能查到本次那条记录；HTTP 面（应用源码）不存在能写它的入口（S9）。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    lines = (data_dir / LEDGER_FILE_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["from"] == 3 and record["to"] == SCHEMA_VERSION
    assert record["result"] == "success"
    assert record["started_at"] and record["finished_at"]
    assert str(_backup_dirs(data_dir)[0]) == record["backup_path"]

    # 追加是 append-only 的：再跑一次（幂等、无迁移）不新增记录。
    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED
    assert len((data_dir / LEDGER_FILE_NAME).read_text(encoding="utf-8").splitlines()) == 1

    # 人类面没有任何写入口：应用源码里不出现台账文件名。
    sources = [
        path for path in (APP_DIR / "alibot").rglob("*.py") if path.name not in {"migrate.py", "__main__.py"}
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert LEDGER_FILE_NAME not in text and "schema-migrations" not in text


# ─── 幂等 / 无事可做（S10） ─────────────────────────────────────────────────
def test_second_run_is_a_noop_without_a_new_backup(tmp_path: Path) -> None:
    """迁移完再跑一次 ⇒ 改动 0 行、退出 0、**不新建备份**。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)
    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED
    digest_before = _sha256(data_dir / DATABASE_FILE_NAME)
    backups_before = _backup_dirs(data_dir)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED
    assert _sha256(data_dir / DATABASE_FILE_NAME) == digest_before
    assert _backup_dirs(data_dir) == backups_before
    assert "无事可做" in completed.stdout


# ─── 单事务与显式拒绝（验收 6 / 9） ─────────────────────────────────────────
def test_five_to_six_conflict_is_explicitly_refused(tmp_path: Path) -> None:
    """``5 → 6`` 的唯一性收紧遇到存量冲突行 ⇒ **明确拒绝升级**，不静默跳过（D6 / User Story 33）。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 5)
    _add_duplicate_dm_peers(db_path)
    before = _shape(db_path)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_FAILED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "冲突" in completed.stderr
    assert _shape(db_path) == before, "拒绝升级必须回滚到原版本、不留中间态"


def test_failed_migration_rolls_back_ddl_moved_in_earlier_steps(tmp_path: Path) -> None:
    """单事务：一步失败 ⇒ 前面步骤建出的新表 / 新列一起回滚，库停在原版本（S8）。

    构造：v4 形态库 + 存量 DM 重复行。链从 ``4 → 5`` 起步（会先建 ``task`` 表、给 ``message`` 加
    ``task_ref``），随后对齐到唯一的私信索引时撞上冲突 ⇒ 整条链回滚。于是「新表建了、数据没搬」这
    种中间态**不存在**——``task`` 表与 ``task_ref`` 列都必须消失。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 4)
    _add_duplicate_dm_peers(db_path)
    before = _shape(db_path)
    assert "task" not in before["tables"], "v4 形态本就没有 task 表"
    assert "task_ref" not in before["columns"]["message"]

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_FAILED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    after = _shape(db_path)
    assert after == before, "失败后必须逐项回到动手前的形态（含 DDL）"
    assert after["version"] == "4"
    # 原版本代码仍能打开它：库是完整的 v4 形态（不是半迁移）。
    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM target").fetchone()[0] == 3
    finally:
        conn.close()


def test_a_locked_database_fails_with_five_and_writes_a_failed_audit_record(tmp_path: Path) -> None:
    """库被别的进程占住 ⇒ ``BEGIN IMMEDIATE`` 失败也是「动手失败、已回滚」⇒ 5，并留失败审计。

    这条钉住的是「迁移失败」这个面的**边界**：失败可能发生在事务真正开始之前（拿不到写锁），
    但它与链上失败一样给退出码 ``5``、一样不改库、一样在台账里留一条 ``result=failed``——而不是
    一个未捕获的堆栈 / 别的退出码。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 3)
    digest_before = _sha256(db_path)
    blocker = sqlite3.connect(db_path, isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")  # 占住写锁
        completed = _run(*_migrate_argv(data_dir))
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    assert completed.returncode == MIGRATION_FAILED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _sha256(db_path) == digest_before
    assert _shape(db_path)["version"] == "3"
    records = [json.loads(line) for line in (data_dir / LEDGER_FILE_NAME).read_text().splitlines()]
    assert records[-1]["result"] == "failed" and records[-1]["from"] == 3


# ─── 触发器替换与缺表 v10 树（验收 10 / 11） ────────────────────────────────
@pytest.mark.parametrize("version", [9, 10])
def test_ten_to_eleven_replaces_the_legacy_attachment_trigger(tmp_path: Path, version: int) -> None:
    """``10 → 11`` 的触发器**先删后建**：终态里不存在 ``attachment_no_rebind`` 与新触发器并存。"""
    data_dir = tmp_path / f"data-v{version}"
    db_path = _build_library(data_dir, version)
    if version == 9:
        assert "attachment_no_rebind" in _shape(db_path)["triggers"], "v9 库里应有旧触发器"

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    triggers = set(_shape(db_path)["triggers"])
    assert "attachment_no_rebind" not in triggers
    assert {
        "attachment_metadata_immutable",
        "attachment_binding_is_immutable",
        "attachment_no_delete",
    } <= triggers


def test_missing_table_v10_tree_is_absorbed_by_alignment(tmp_path: Path) -> None:
    """缺 ``reminder`` 的 v10 树由终态判据吸收：对齐把当前 ``SCHEMA`` 的对象补齐。"""
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 10)
    assert "reminder" not in _shape(db_path)["tables"], "这个 v10 树本来就没有 reminder"

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    _assert_terminal_shape(db_path, _fresh_current_library(tmp_path, "fresh"))


def test_partial_unique_index_enforces_one_dm_per_peer_after_migration(tmp_path: Path) -> None:
    """迁移后部分唯一索引在库级生效：同一 peer 的第二个 DM target 被拒绝（不是只有名字在）。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 3)
    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    conn = _connect(data_dir / DATABASE_FILE_NAME)
    try:
        conn.execute(
            "INSERT INTO target(id, kind, dm_peer_member_id, created_at) "
            "VALUES ('dm_a', 'dm', 'mem_x', '2026-10-14T09:00:06Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO target(id, kind, dm_peer_member_id, created_at) "
                "VALUES ('dm_b', 'dm', 'mem_x', '2026-10-14T09:00:07Z')"
            )
    finally:
        conn.close()
