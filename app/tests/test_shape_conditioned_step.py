"""链前**形态步骤**（issue #92）：不依赖版本号地补齐半改名库的四张表。

规格正本：``app/specs/issue-87-spec.md``（S12 / D5；T2：**唯一测试接缝 = CLI 进程入口**）。

本文件只经**真实子进程**取证：``python -m alibot --migrate --data-dir <目录>``，配一个磁盘上的真实
数据目录 fixture；断言只落在**外部可观察面**——退出码、stdout / stderr 报告文本、数据目录与库的
终态、文件 sha256。不 import 实现内部函数、不断言私有函数或 SQL 文本（T1）。

处置的形态（#37 事故）：``schema_version`` 已是新一代、``channel`` 行都在，而 channel 型的**影子行 /
成员 / 已读 / 预算**还留在旧表名（``channel_member`` / ``channel_read`` / ``channel_budget``）里。
按版本号走链会**跳过** ``3 → 4``，所以只有「链前形态步骤」能把它搬回来。

历史逐版 fixture 由 ``tests/_migration_history.py`` 提供（从 git 历史里各版 ``SCHEMA`` 抽取生成）：
本文件据此搭出「该版形态」的库，再手工钉上版本号，造出「号是新一代、数据还留在旧一代」的库。
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

from alibot.db import BUDGET_LIMIT, DATABASE_FILE_NAME, SCHEMA_VERSION, Database

from tests._migration_history import OBJECTS, VERSION_OBJECTS
from tests.conftest import backend

APP_DIR = Path(__file__).resolve().parents[1]

MIGRATION_SUCCEEDED = 0
SHAPE_NOT_HANDLED = 4

# 迁移后**原样保留**的三张旧表（规格 D12：回退面，唯一例外）。
LEGACY_TABLES = ("channel_member", "channel_read", "channel_budget")

# 三张旧表加进「当前版本形态」的库时用的对象名（当前 ``SCHEMA`` 不再建它们）。
LEGACY_OBJECTS = ("channel_member", "channel_read", "channel_budget")

# 历史各版新增、但 SQLite 无法用 ``CREATE`` 表达的列（用带约束的 ``ADD COLUMN`` 补）。
_COLUMN_DDL: dict[str, str] = {
    "task_ref": 'ALTER TABLE "message" ADD COLUMN task_ref TEXT',
    "dm_peer_member_id": 'ALTER TABLE "target" ADD COLUMN dm_peer_member_id TEXT REFERENCES member(id)',
    "theme": "ALTER TABLE \"workspace_settings\" ADD COLUMN theme TEXT NOT NULL DEFAULT 'system' "
    "CHECK (theme IN ('light', 'dark', 'system'))",
    "claimed_at": 'ALTER TABLE "task" ADD COLUMN claimed_at TEXT',
    "auto_claim": 'ALTER TABLE "agent_config" ADD COLUMN auto_claim INTEGER NOT NULL DEFAULT 0 '
    "CHECK (auto_claim IN (0, 1))",
}
_COLUMN_ADDITIONS: dict[int, tuple[str, ...]] = {
    5: ("task_ref",),
    6: ("dm_peer_member_id",),
    10: ("theme",),
    12: ("claimed_at", "auto_claim"),
}

STAMP_VERSION_SQL = "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'"


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


def _migrate_argv(data_dir: Path) -> list[str]:
    return ["--migrate", "--data-dir", str(data_dir)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup_siblings(data_dir: Path) -> list[str]:
    """数据目录**同级**里以 ``<数据目录名>-backup-`` 开头的副本（规格 D8 的命名）。"""
    if not data_dir.parent.exists():
        return []
    return sorted(
        path.name for path in data_dir.parent.iterdir() if path.name.startswith(f"{data_dir.name}-backup-")
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> tuple:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return tuple(row)


# ─── 造库：历史逐版 DDL 留档 + 手工钉版本号 ──────────────────────────────────
def _create_shape(conn: sqlite3.Connection, version: int, extra: tuple[str, ...] = ()) -> None:
    """按 ``version`` 版的对象集建库（表 → 该版新增的列 → 索引 / 触发器）。"""
    objects = (*VERSION_OBJECTS[version], *extra)
    for name in objects:
        if OBJECTS[name].startswith("CREATE TABLE"):
            conn.execute(OBJECTS[name])
    for introduced in range(3, version + 1):
        for column in _COLUMN_ADDITIONS.get(introduced, ()):
            conn.execute(_COLUMN_DDL[column])
    for name in objects:
        if "INDEX" in OBJECTS[name].splitlines()[0]:
            conn.execute(OBJECTS[name])
    for name in objects:
        if OBJECTS[name].startswith("CREATE TRIGGER"):
            conn.execute(OBJECTS[name])


def _seed_legacy_scope_rows(conn: sqlite3.Connection) -> None:
    """灌入「只活在旧表名里」的 channel 型作用域数据：一个频道、两个成员、一份**已耗尽**预算。"""
    conn.execute(
        "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
        "VALUES ('owner', 'owner', 'Owner', 4, '2026-10-14T09:00:00Z')"
    )
    conn.execute(
        "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
        "VALUES ('mem_x', 'agent', '小明', 1, '2026-10-14T09:00:01Z')"
    )
    conn.execute(
        "INSERT INTO channel(id, name, description, created_at) VALUES ('ch_dev', 'dev', '开发频道', ?)",
        ("2026-10-14T09:00:02Z",),
    )
    conn.execute(
        "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at) "
        "VALUES ('msg_1', 'ch_dev', 1, 'owner', 'human', '第一条', ?)",
        ("2026-10-14T09:00:03Z",),
    )
    conn.execute("INSERT INTO channel_member(channel_id, member_id) VALUES ('ch_dev', 'owner')")
    conn.execute("INSERT INTO channel_member(channel_id, member_id) VALUES ('ch_dev', 'mem_x')")
    conn.execute(
        "INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES ('owner', 'ch_dev', 1)"
    )
    conn.execute(
        "INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES ('mem_x', 'ch_dev', 2)"
    )
    # 预算 `used` == 上限：这是「已经耗尽」的正本，搬迁后**不得归零**（D5 / User Story 19）。
    conn.execute(
        "INSERT INTO channel_budget(channel_id, epoch_seq, used) VALUES ('ch_dev', 1, ?)",
        (BUDGET_LIMIT,),
    )


def _build_half_renamed_library(data_dir: Path, *, stored_version: int) -> Path:
    """造「旧表名里有数据、``schema_version`` 已是 ``stored_version``」的半改名库。

    ``stored_version == 4`` 时目标表**整片不在**（v3 形态 + 钉 4）；``stored_version == SCHEMA_VERSION``
    时目标表在但**全空**（当前形态 + 旧表数据），用来取证「判定只看形态、不看版本号」。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "attachments").mkdir(exist_ok=True)
    db_path = data_dir / DATABASE_FILE_NAME
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if stored_version == SCHEMA_VERSION:
            # 当前版本形态 + 三张旧表（旧表是回退面，当前 ``SCHEMA`` 不建它们）。
            _create_shape(conn, SCHEMA_VERSION, extra=LEGACY_OBJECTS)
        else:
            _create_shape(conn, stored_version, extra=LEGACY_OBJECTS)
        _seed_legacy_scope_rows(conn)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(stored_version),),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _build_healthy_current_library(data_dir: Path) -> Path:
    """用本代码真实建一个当前版本的**健康**库，并把旧表镜像成与 ``target_*`` 一致的回退面。

    镜像这件事很要紧：它证明形态步骤**不是**「看到旧表名就当有缺」，而是逐行比对旧表 → 新表；
    一个已经搬过、只是回退面还在的健康库必须被判成**无事可做**。
    """
    with backend(data_dir) as client:
        assert client.post("/api/channels", json={"name": "dev"}).status_code == 201
    db_path = data_dir / DATABASE_FILE_NAME
    conn = _connect(db_path)
    try:
        for name in LEGACY_OBJECTS:
            conn.execute(OBJECTS[name])
        conn.execute(
            "INSERT INTO channel_member(channel_id, member_id) SELECT target_id, member_id FROM target_member"
        )
        conn.execute(
            "INSERT INTO channel_read(member_id, channel_id, last_read_seq) "
            "SELECT member_id, target_id, last_read_seq FROM target_read"
        )
        conn.execute(
            "INSERT INTO channel_budget(channel_id, epoch_seq, used) "
            "SELECT target_id, epoch_seq, used FROM target_budget"
        )
    finally:
        conn.close()
    return db_path


def _seed_dm_scope_rows(conn: sqlite3.Connection) -> None:
    """给当前版本库补一个**私信型** target 及它的成员 / 已读 / 预算行（形态步骤不得误伤它）。"""
    conn.execute(
        "INSERT INTO target(id, kind, dm_peer_member_id, created_at) "
        "VALUES ('dm_x', 'dm', 'mem_x', '2026-10-14T09:10:00Z')"
    )
    conn.execute("INSERT INTO target_member(target_id, member_id) VALUES ('dm_x', 'owner')")
    conn.execute("INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES ('owner', 'dm_x', 7)")
    conn.execute("INSERT INTO target_budget(target_id, epoch_seq, used) VALUES ('dm_x', 3, 2)")


def _channel_scope_rows(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        "target": [tuple(r) for r in conn.execute("SELECT id, kind FROM target WHERE kind = 'channel'")],
        "target_member": [
            tuple(r)
            for r in conn.execute("SELECT target_id, member_id FROM target_member ORDER BY member_id")
        ],
        "target_read": [
            tuple(r)
            for r in conn.execute(
                "SELECT member_id, target_id, last_read_seq FROM target_read ORDER BY member_id"
            )
        ],
        "target_budget": [
            tuple(r) for r in conn.execute("SELECT target_id, epoch_seq, used FROM target_budget")
        ],
    }


# ─── 验收 1 / 2：半改名库端到端 + 已耗尽预算不归零 ──────────────────────────
def test_half_renamed_library_at_stored_four_is_completed_and_the_app_can_open_it(tmp_path: Path) -> None:
    """``stored=4``、channel 型数据全在旧表名里 ⇒ ``--migrate`` 搬到 ``target_*`` 并推进到终态。

    四条断言对应票面验收 1 与 2：退出 ``0``；四张表数据齐备且逐行对应；库里版本 = ``SCHEMA_VERSION``；
    **已耗尽预算的 ``used`` 不被归零**；本代码的 app 能打开它并读出预算。
    """
    data_dir = tmp_path / "data"
    db_path = _build_half_renamed_library(data_dir, stored_version=4)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "形态步骤" in completed.stdout, "报告必须打出实际执行的形态步骤"

    conn = _connect(db_path)
    try:
        assert _row(conn, "SELECT value FROM schema_meta WHERE key = 'schema_version'") == (
            str(SCHEMA_VERSION),
        )
        assert _channel_scope_rows(conn) == {
            "target": [("ch_dev", "channel")],
            "target_member": [("ch_dev", "mem_x"), ("ch_dev", "owner")],
            "target_read": [("mem_x", "ch_dev", 2), ("owner", "ch_dev", 1)],
            "target_budget": [("ch_dev", 1, BUDGET_LIMIT)],
        }
        # 三张旧表是回退面：原样保留、行数与内容不变。
        for table in LEGACY_TABLES:
            assert _row(conn, f'SELECT COUNT(*) FROM "{table}"')[0] > 0
    finally:
        conn.close()

    # 本代码的 app 能打开它：频道成员面与预算面都恢复（不是「读数是 12」而已）。
    database = Database(data_dir)
    try:
        assert [channel["id"] for channel in database.list_channels()] == ["ch_dev"]
        budget = database.budget_state("ch_dev")
        assert budget["used"] == BUDGET_LIMIT, "已耗尽的预算不得被归零"
        assert budget["remaining"] == 0
    finally:
        database.close()

    with backend(data_dir) as client:
        response = client.get("/api/channels/ch_dev/budget")
        assert response.status_code == 200, response.text
        assert response.json()["budget"]["used"] == BUDGET_LIMIT


def test_intact_shadow_row_but_missing_scope_rows_is_completed_without_zeroing_the_budget(
    tmp_path: Path,
) -> None:
    """影子行已在、成员 / 已读 / 预算有缺 ⇒ 同样补齐；已耗尽预算的 ``used`` 不得归零。

    这是「预算面保真」最锋利的一刀：只补三张表（漏预算）的实现会让 ``GET /api/channels/{id}/budget``
    从 404 变成「200 且 ``used`` 归零」——那不是少一行，是沉默抹掉一个已经耗尽的预算。因此这里直接
    钉住 ``target_budget.used`` == 上限（issue #92 验收第 2 条 / 规格 User Story 19）。
    """
    data_dir = tmp_path / "data"
    db_path = _build_half_renamed_library(data_dir, stored_version=4)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO target(id, kind, created_at) VALUES ('ch_dev', 'channel', '2026-10-14T09:00:02Z')"
        )
    finally:
        conn.close()

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    conn = _connect(db_path)
    try:
        assert _channel_scope_rows(conn)["target_budget"] == [("ch_dev", 1, BUDGET_LIMIT)]
        assert _row(conn, "SELECT COUNT(*) FROM target_member")[0] == 2
        assert _row(conn, "SELECT COUNT(*) FROM target_read")[0] == 2
    finally:
        conn.close()


# ─── 验收 3：幂等 ───────────────────────────────────────────────────────────
def test_second_run_of_the_completed_library_changes_nothing_and_adds_no_backup(tmp_path: Path) -> None:
    """补齐后的库再跑一次 ⇒ 退出 ``0``、库 sha256 不变、数据不变、**不新建备份**（S12 的幂等）。"""
    data_dir = tmp_path / "data"
    db_path = _build_half_renamed_library(data_dir, stored_version=4)
    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED
    digest_before = _sha256(db_path)
    backups_before = _backup_siblings(data_dir)
    conn = _connect(db_path)
    try:
        rows_before = _channel_scope_rows(conn)
    finally:
        conn.close()

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert "无事可做" in completed.stdout
    assert _sha256(db_path) == digest_before
    assert _backup_siblings(data_dir) == backups_before
    conn = _connect(db_path)
    try:
        assert _channel_scope_rows(conn) == rows_before
    finally:
        conn.close()


# ─── 验收 4：健康库不受影响 ─────────────────────────────────────────────────
def test_healthy_current_library_with_an_intact_legacy_fallback_is_a_noop(tmp_path: Path) -> None:
    """当前版健康库（旧表只是回退面镜像）⇒ ``0``、改动 0 行、sha256 不变、**不建备份**。

    这条钉住形态判定的**边界**：有旧表名不等于有缺——逐行比对旧表 → 新表才是判据。否则一个正常跑过
    迁移的健康库会被反复误判成半改名库。
    """
    data_dir = tmp_path / "data"
    db_path = _build_healthy_current_library(data_dir)
    digest_before = _sha256(db_path)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    assert "无事可做" in completed.stdout
    assert _sha256(db_path) == digest_before
    assert _backup_siblings(data_dir) == []


# ─── 验收：判定只看形态、不看版本号 ─────────────────────────────────────────
def test_current_version_library_with_missing_shadow_rows_is_completed_too(tmp_path: Path) -> None:
    """版本读数已是本代码版本、表都在、但 channel 型作用域行还留在旧表名里 ⇒ 同样被补齐。

    这条正是「判定只看形态、不看版本号」：它**不能**被当成「无事可做」的 ``0``（issue #92 验收第 3 条）。
    链是空的（版本已是 12），补齐全部由链前的形态步骤完成。
    """
    data_dir = tmp_path / "data"
    db_path = _build_half_renamed_library(data_dir, stored_version=SCHEMA_VERSION)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "形态步骤" in completed.stdout
    conn = _connect(db_path)
    try:
        assert _channel_scope_rows(conn) == {
            "target": [("ch_dev", "channel")],
            "target_member": [("ch_dev", "mem_x"), ("ch_dev", "owner")],
            "target_read": [("mem_x", "ch_dev", 2), ("owner", "ch_dev", 1)],
            "target_budget": [("ch_dev", 1, BUDGET_LIMIT)],
        }
    finally:
        conn.close()

    # 幂等的第二遍：这次才是真正的「无事可做」。
    digest_before = _sha256(db_path)
    completed = _run(*_migrate_argv(data_dir))
    assert completed.returncode == MIGRATION_SUCCEEDED
    assert "无事可做" in completed.stdout
    assert _sha256(db_path) == digest_before
    assert _backup_siblings(data_dir) and len(_backup_siblings(data_dir)) == 1


def test_shape_step_does_not_touch_dm_scope_rows(tmp_path: Path) -> None:
    """形态步骤只搬 channel 型作用域数据，不误伤私信 / 任务型作用域及其成员 / 已读 / 预算行（D5）。"""
    data_dir = tmp_path / "data"
    db_path = _build_half_renamed_library(data_dir, stored_version=SCHEMA_VERSION)
    conn = _connect(db_path)
    try:
        _seed_dm_scope_rows(conn)
        dm_before = {
            "target": _row(conn, "SELECT id, kind, dm_peer_member_id FROM target WHERE id = 'dm_x'"),
            "member": _row(conn, "SELECT target_id, member_id FROM target_member WHERE target_id = 'dm_x'"),
            "read": _row(
                conn, "SELECT member_id, target_id, last_read_seq FROM target_read WHERE target_id = 'dm_x'"
            ),
            "budget": _row(
                conn, "SELECT target_id, epoch_seq, used FROM target_budget WHERE target_id = 'dm_x'"
            ),
        }
    finally:
        conn.close()

    assert _run(*_migrate_argv(data_dir)).returncode == MIGRATION_SUCCEEDED

    conn = _connect(db_path)
    try:
        assert (
            _row(conn, "SELECT id, kind, dm_peer_member_id FROM target WHERE id = 'dm_x'")
            == dm_before["target"]
        )
        assert (
            _row(conn, "SELECT target_id, member_id FROM target_member WHERE target_id = 'dm_x'")
            == dm_before["member"]
        )
        assert (
            _row(conn, "SELECT member_id, target_id, last_read_seq FROM target_read WHERE target_id = 'dm_x'")
            == dm_before["read"]
        )
        assert (
            _row(conn, "SELECT target_id, epoch_seq, used FROM target_budget WHERE target_id = 'dm_x'")
            == dm_before["budget"]
        )
    finally:
        conn.close()


# ─── 边界：缺表的库仍走 4，不被形态步骤当成可补齐的对象 ─────────────────────
def test_missing_required_table_still_exits_four_without_touching_the_library(tmp_path: Path) -> None:
    """当前版本但缺 ``task`` 且有业务数据 ⇒ 仍 ``4``、sha256 不变、不建备份（`4` 与形态步骤互补）。"""
    data_dir = tmp_path / "data"
    db_path = _build_healthy_current_library(data_dir)
    conn = _connect(db_path)
    try:
        conn.execute('DROP TABLE "task"')
    finally:
        conn.close()
    digest_before = _sha256(db_path)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == SHAPE_NOT_HANDLED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _sha256(db_path) == digest_before, "形态不对的答复必须发生在任何写入之前"
    assert _backup_siblings(data_dir) == [], "4 是「还没动手」：不得新建备份副本"
