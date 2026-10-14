"""启动时的 schema 版本闸门（issue #37）：版本不匹配 ⇒ 拒绝启动，且旧库一个字节都不改。

**为什么需要这个文件**：#24 把消息作用域升为 ``target`` 并把 ``SCHEMA_VERSION`` 3 → 4、**不写迁移
脚本**。规格决策 8 / ADR-0004 只规定「不做迁移」，没规定「旧库怎么办」——于是 v4 代码打开 v3 库时
会**静默半改名**：``schema_version`` 被改写成 4、新的 ``target*`` 表被建出来但是空的、旧表数据还在、
``GET /api/channels`` 返 200 看起来正常，而成员 / 预算 / 发消息 / 标读**全部 404**。
本票补的是「不做迁移」的另一半：**不接受旧库**。

与 ``conftest.py`` 的体例一致：经 HTTP 面或 ``Database`` 的公开入口取证，``tmp_path`` 独占数据目录，
绝不触碰 ``~/Alibot``。**唯一**直接读库的场合是「库未变」这类结构事实——它没有 HTTP 路径可观察，
也不该为测试新增一个端点；那种场合的断言只在打开前后各读一次文件，且**不写**该库。
"""

from __future__ import annotations

import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from alibot.db import DATABASE_FILE_NAME, SCHEMA_VERSION, Database, SchemaVersionMismatch
from fastapi.testclient import TestClient

from tests.conftest import backend

APP_DIR = Path(__file__).resolve().parents[1]

# v3 形态的库（#24 之前那一代）：没有 ``target*`` 表，成员/已读/预算三张表还是 ``channel_*`` 旧名。
# 这里用**显式 DDL** 构造，而不是去 import 一份 v3 源码——v3 的 ``db.py`` 在这个版本里已经不存在了，
# 而票面明确授权这种构造方式（「用 ``sqlite3`` 在 ``tmp_path`` 下建一个 v3 形态的库……这足以复现」）。
V3_SCHEMA = """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE member (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('owner', 'agent')),
  display_name TEXT NOT NULL, avatar_slot INTEGER NOT NULL, created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE channel (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE channel_member (
  channel_id TEXT NOT NULL REFERENCES channel(id), member_id TEXT NOT NULL REFERENCES member(id),
  PRIMARY KEY (channel_id, member_id)
);
CREATE TABLE message (
  id TEXT PRIMARY KEY, target_id TEXT NOT NULL, seq INTEGER NOT NULL,
  author_member_id TEXT NOT NULL REFERENCES member(id),
  kind TEXT NOT NULL CHECK (kind IN ('human', 'agent', 'system')), body TEXT NOT NULL,
  created_at TEXT NOT NULL, mention_snapshot TEXT NOT NULL DEFAULT '[]', UNIQUE (target_id, seq)
);
CREATE TABLE channel_read (
  member_id TEXT NOT NULL REFERENCES member(id), channel_id TEXT NOT NULL REFERENCES channel(id),
  last_read_seq INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (member_id, channel_id)
);
CREATE TABLE channel_budget (
  channel_id TEXT PRIMARY KEY REFERENCES channel(id), epoch_seq INTEGER NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)
);
"""

V3_ROWS = """
INSERT INTO schema_meta(key, value) VALUES ('schema_version', '3');
INSERT INTO member(id, kind, display_name, avatar_slot, created_at)
  VALUES ('owner', 'owner', 'Owner', 4, '2026-10-14T09:00:00Z');
INSERT INTO member(id, kind, display_name, avatar_slot, created_at)
  VALUES ('mem_xiaoming', 'agent', '小明', 1, '2026-10-14T09:00:01Z');
INSERT INTO channel(id, name, description, created_at)
  VALUES ('ch_dev', 'dev', '开发频道', '2026-10-14T09:00:02Z');
INSERT INTO channel_member(channel_id, member_id) VALUES ('ch_dev', 'owner');
INSERT INTO channel_member(channel_id, member_id) VALUES ('ch_dev', 'mem_xiaoming');
INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at)
  VALUES ('msg_1', 'ch_dev', 1, 'owner', 'human', '第一条', '2026-10-14T09:00:03Z');
INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at)
  VALUES ('msg_2', 'ch_dev', 2, 'mem_xiaoming', 'agent', '第二条', '2026-10-14T09:00:04Z');
INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES ('owner', 'ch_dev', 1);
INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES ('mem_xiaoming', 'ch_dev', 2);
INSERT INTO channel_budget(channel_id, epoch_seq, used) VALUES ('ch_dev', 1, 2);
"""


def _build_v3_database(data_dir: Path, version: str | None = "3") -> Path:
    """在 ``data_dir`` 里造一个 v3 形态、且**已有业务数据**的库；返回库文件路径。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / DATABASE_FILE_NAME
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V3_SCHEMA)
        conn.executescript(V3_ROWS)
        if version is None:  # 「版本号缺失但有数据」那条分支
            conn.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
        else:
            conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (version,))
        conn.commit()
    finally:
        conn.close()
    return db_path


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


# 取行数的语句：**一句一个模块级常量**（与 ``db.py`` / ``repair.py`` 同一体例：不把表名插进
# ``execute`` 的实参）。快照要覆盖新旧两代的作用域表，清单固定。
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


def _snapshot(db_path: Path) -> dict[str, Any]:
    """库的逐一比对快照：版本号 + 每张表的行数 + 表清单（只读；**不**改动该库）。

    行数只计**库里存在**的表（缺表不进字典），于是「以前没有、现在多了」会被 ``==`` 直接看出来；
    这正是拒绝路径要断言的事（旧库不许被 ``CREATE TABLE IF NOT EXISTS`` 拓出 ``target*``）。
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
        counts = {
            table: conn.execute(_ROW_COUNT_SQL[table]).fetchone()[0]
            for table in tables
            if table in _ROW_COUNT_SQL
        }
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return {"tables": tables, "row_counts": counts, "stored_version": None if row is None else row[0]}
    finally:
        conn.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ─── 拒绝对旧库：库一个字节都不改 ────────────────────────────────────────────
@pytest.mark.parametrize("stored_version", ["3", "5", "not-a-number"])
def test_older_or_unknown_version_is_refused_and_the_file_is_untouched(
    tmp_path: Path, stored_version: str
) -> None:
    """库里版本 ≠ 常量 ⇒ 抛错终止；库的版本号、表清单与各表行数**逐项一致**。

    ``"3"`` 是真实的旧库（本票的缺陷现场），``"5"``/垃圾值覆盖「未来库」与坏库——三种都不得被
    静默接受，也都不许被改写。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v3_database(data_dir, version=stored_version)
    before = _snapshot(db_path)

    with pytest.raises(SchemaVersionMismatch) as caught:
        Database(data_dir)

    after = _snapshot(db_path)
    # 逐项一致：版本号没被改写、**没有**多出 target* 表、每张表行数不变。
    assert after == before
    assert after["stored_version"] == stored_version
    assert "target" not in after["tables"]
    assert "target_member" not in after["tables"]

    # 报错可读且可操作：库文件路径、库里的版本、本代码需要的版本、一句指引。
    message = str(caught.value)
    assert str(db_path) in message
    assert str(SCHEMA_VERSION) in message and stored_version in message
    assert "不做数据迁移" in message
    assert "备份" in message and "数据目录" in message


def test_version_missing_but_business_rows_exist_is_refused(tmp_path: Path) -> None:
    """版本号**缺失**但库里有业务数据 ⇒ 同样拒绝（这不是空库，是来源不明的库）。

    「没有版本号」不等于「空库」：真读不到版本号时，唯一安全的判据是**库里有没有行**。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v3_database(data_dir, version=None)
    before = _snapshot(db_path)

    with pytest.raises(SchemaVersionMismatch) as caught:
        Database(data_dir)

    assert _snapshot(db_path) == before
    assert "缺失" in str(caught.value)


def test_the_refusal_happens_before_any_schema_change_not_just_a_500(tmp_path: Path) -> None:
    """拒绝发生在**建应用之前**：``create_app`` 直接抛错，不会先起一个后返 500 的应用。

    这是「不能只在 HTTP 层返 500」那条要求的可断言形式：抛出点在 ``create_app`` 内，因此
    ``python -m alibot`` 拿不到 app、也就不会开始监听。
    """
    from alibot.api import create_app

    data_dir = tmp_path / "data"
    _build_v3_database(data_dir)

    with pytest.raises(SchemaVersionMismatch):
        create_app(data_dir=data_dir)


def test_refused_database_is_still_refused_on_the_second_attempt(tmp_path: Path) -> None:
    """拒绝是**幂等**的：反复启动不会「第二次就接受了」，也不会把库越改越乱。"""
    data_dir = tmp_path / "data"
    db_path = _build_v3_database(data_dir)
    before = _snapshot(db_path)

    for _ in range(3):
        with pytest.raises(SchemaVersionMismatch):
            Database(data_dir)

    assert _snapshot(db_path) == before


@pytest.mark.parametrize("journal_mode", ["delete", "truncate", "persist"])
@pytest.mark.parametrize("stored_version", ["3", "5"])
def test_refusal_leaves_the_file_byte_identical_even_when_it_is_not_already_wal(
    tmp_path: Path, journal_mode: str, stored_version: str
) -> None:
    """**逐字节未改**——且必须用一个**非 WAL** 的旧库来取证。

    这条用例的价值在于它瞪的是一个真实缺陷：初版把 ``PRAGMA journal_mode = WAL`` 放在版本闸门
    **之前**。那条 PRAGMA 不是连接级开关，它要把库文件头的 journal mode 从 ``delete`` 改成
    ``wal``——对一个非 WAL 旧库执行它，文件 sha256 **会变**，于是「旧库一个字节不改」在这条路径
    上并不成立。

    为什么之前没发现：取证用的 v3 fixture 本身已是 WAL 形态，那条 PRAGMA 于是成了 no-op。
    这条用例用 ``journal_mode IN ('delete','truncate','persist')`` 构造非 WAL 库，
    所以它能在修复前变红（sha256 不等），而不是只靠逻辑快照。

    同时断言**没有多出同伴文件**（``-wal`` / ``-shm``）：它们同样是「库被动过」的痕迹。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v3_database(data_dir, version=stored_version)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(f"PRAGMA journal_mode = {journal_mode}").fetchone()[0] == journal_mode
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == journal_mode
    finally:
        conn.close()
    files_before = sorted(path.name for path in data_dir.iterdir())
    digest_before = _sha256(db_path)
    assert _snapshot(db_path)["stored_version"] == stored_version

    with pytest.raises(SchemaVersionMismatch):
        Database(data_dir)

    assert _sha256(db_path) == digest_before, "拒绝路径不得改动库文件一个字节（连文件头也不行）"
    assert sorted(path.name for path in data_dir.iterdir()) == files_before, "不得多出 -wal/-shm"


# ─── 正常路径不回归 ─────────────────────────────────────────────────────────
def test_empty_data_dir_still_starts(tmp_path: Path) -> None:
    """空目录照常启动：库被建出来、版本号是本版、owner 行在（既有行为一字不变）。"""
    data_dir = tmp_path / "fresh"

    with backend(data_dir) as client:
        assert client.get("/api/health").json()["status"] == "ok"
    assert _snapshot(data_dir / DATABASE_FILE_NAME)["stored_version"] == str(SCHEMA_VERSION)


def test_v4_database_still_starts_and_keeps_its_rows(tmp_path: Path) -> None:
    """v4 库再次启动成功，且既有数据（频道 + 消息）读得回来——闸门**不误伤**正常库。"""
    data_dir = tmp_path / "data"

    with backend(data_dir) as first:
        channel = first.post("/api/channels", json={"name": "dev"}).json()["channel"]
        first.post(f"/api/channels/{channel['id']}/messages", json={"body": "第一条"})
    before = _snapshot(data_dir / DATABASE_FILE_NAME)

    with backend(data_dir) as second:
        assert second.get("/api/health").json()["status"] == "ok"
        listed = second.get(f"/api/channels/{channel['id']}/messages").json()["messages"]
        assert [message["body"] for message in listed] == ["第一条"]

    assert _snapshot(data_dir / DATABASE_FILE_NAME) == before


def test_empty_schema_meta_without_version_row_still_starts(tmp_path: Path) -> None:
    """库存在、``schema_meta`` 存在但**没有版本行**、且没有任何业务数据 ⇒ 照常启动。

    这是「库文件不存在 / 空库」之外第三种「照常启动」的形态：初始化到一半的目录不该把人挡在门外。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / DATABASE_FILE_NAME
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()

    with backend(data_dir) as client:
        assert client.get("/api/health").json()["status"] == "ok"
    assert _snapshot(db_path)["stored_version"] == str(SCHEMA_VERSION)


# ─── 启动路径：非 0 退出码 + stderr（真实进程，不是 TestClient） ───────────────
def test_cli_process_exits_non_zero_with_a_readable_stderr(tmp_path: Path) -> None:
    """真实进程：``python -m alibot --data-dir <v3 目录>`` ⇒ 非 0 退出 + 可读的 stderr。

    ``PYTHONPATH`` 指向本 worktree 的 ``app/``：本仓的 ``.venv`` 是符号链接指向主仓，
    其 editable 安装的 ``.pth`` 指回**主仓**的 ``app``，不显式指定就会测到主仓代码而不是本 worktree。
    """
    data_dir = tmp_path / "data"
    db_path = _build_v3_database(data_dir)
    before = _snapshot(db_path)

    env = {"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alibot",
            "--data-dir",
            str(data_dir),
            "--port",
            str(_free_port()),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(APP_DIR),
    )

    assert completed.returncode != 0, f"旧库必须被拒绝启动，实际退出码 {completed.returncode}"
    stderr = completed.stderr
    assert str(db_path) in stderr
    assert str(SCHEMA_VERSION) in stderr and "3" in stderr
    assert "备份" in stderr
    # 明确拒绝启动之后，库仍是打开前那一份。
    assert _snapshot(db_path) == before


def test_health_endpoint_shape_is_unchanged(client: TestClient) -> None:
    """HTTP 契约一字未改（本票不动路径/参数/字段名）——顺带固定 ``/api/health`` 的形状。"""
    assert set(client.get("/api/health").json()) == {"status", "member_id", "data_dir"}
