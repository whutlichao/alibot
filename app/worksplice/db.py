"""单文件 SQLite 持久化：频道、成员、消息与每频道已读位置的正本。

本模块是应用后端的持久化正本（spec #1 §2）。约束：

- 消息 **append-only**：不存在更新或删除消息的代码路径（库级触发器兜底）。
- 频道内单调 ``seq``：``UNIQUE(target_id, seq)``，由本模块在同一事务内分配。
- 已读位置 ``channel_read.last_read_seq`` 只在同一频道内取 max 单调推进。
- 未读计数 = ``last_read_seq`` 之后 ``kind in ('human','agent')`` 的顶层消息条数。

空库只含一条固定成员行 ``owner``（规格固定的唯一成员行），没有任何业务种子数据。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OWNER_ID = "owner"
OWNER_DISPLAY_NAME = "Owner"
OWNER_AVATAR_SLOT = 4

# 头像色位族大小：与设计系统的 --av-0…--av-4 一一对应（色位按 slot 取模）。
# 前端 studio/app.js 的同名常量必须与本值一致。
AVATAR_SLOT_COUNT = 5

SCHEMA_VERSION = 1

DEFAULT_PORT = 8788
DEFAULT_DATA_DIR_NAME = "Worksplice"
DATA_DIR_ENV = "WORKSPLICE_DATA_DIR"
DATABASE_FILE_NAME = "worksplice.db"

MESSAGE_KINDS = ("human", "agent", "system")

MAX_CHANNEL_NAME_LENGTH = 80
MAX_CHANNEL_DESCRIPTION_LENGTH = 500
MAX_MESSAGE_BODY_LENGTH = 8000

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS member (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('owner', 'agent')),
  display_name TEXT NOT NULL,
  avatar_slot  INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  deleted_at   TEXT
);

CREATE TABLE IF NOT EXISTS channel (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_member (
  channel_id TEXT NOT NULL REFERENCES channel(id),
  member_id  TEXT NOT NULL REFERENCES member(id),
  PRIMARY KEY (channel_id, member_id)
);

CREATE TABLE IF NOT EXISTS message (
  id               TEXT PRIMARY KEY,
  target_id        TEXT NOT NULL,
  seq              INTEGER NOT NULL,
  author_member_id TEXT NOT NULL REFERENCES member(id),
  kind             TEXT NOT NULL CHECK (kind IN ('human', 'agent', 'system')),
  body             TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  mention_snapshot TEXT NOT NULL DEFAULT '[]',
  UNIQUE (target_id, seq)
);

CREATE TABLE IF NOT EXISTS channel_read (
  member_id     TEXT NOT NULL REFERENCES member(id),
  channel_id    TEXT NOT NULL REFERENCES channel(id),
  last_read_seq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (member_id, channel_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_member_member ON channel_member(member_id);

-- append-only 兜底：任何更新/删除消息的路径（含未来的代码路径与人工 SQL）都被拒绝。
CREATE TRIGGER IF NOT EXISTS message_no_update
BEFORE UPDATE ON message
BEGIN
  SELECT RAISE(ABORT, 'message is append-only');
END;

CREATE TRIGGER IF NOT EXISTS message_no_delete
BEFORE DELETE ON message
BEGIN
  SELECT RAISE(ABORT, 'message is append-only');
END;
"""

# 频道读取的唯一一条 SQL（无拼接，值全部经命名占位符绑定）：
# - ``:channel_id IS NULL`` 时返回全部频道（列表）；给值时返回单条。
# - 未读计数口径**只在这里写一次**（规格 §2）：已读位置之后、kind ∈ {human, agent}
#   的顶层消息；不计 system。之前 list/get 各抄一份，口径漂移风险真实存在。
CHANNEL_SELECT_SQL = """
    SELECT c.*,
           COALESCE((SELECT MAX(seq) FROM message m WHERE m.target_id = c.id), 0) AS last_seq,
           COALESCE(r.last_read_seq, 0) AS last_read_seq,
           COALESCE((SELECT COUNT(*) FROM message m2
                      WHERE m2.target_id = c.id
                        AND m2.seq > COALESCE(r.last_read_seq, 0)
                        AND m2.kind IN ('human', 'agent')), 0) AS unread_count
      FROM channel c
      LEFT JOIN channel_read r ON r.channel_id = c.id AND r.member_id = :member_id
     WHERE (:channel_id IS NULL OR c.id = :channel_id)
     ORDER BY c.created_at, c.id
"""


class DomainError(Exception):
    """可映射为 HTTP 错误响应的领域错误。"""

    def __init__(self, code: str, message: str, status: int = 400, fields: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields or {}

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.fields:
            body["error"]["fields"] = self.fields
        return body


class NotFound(DomainError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__("not_found", message, status=404)


def utc_now() -> str:
    """ISO-8601 毫秒精度时间戳（UTC，带 Z 后缀）。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_data_dir() -> Path:
    """默认数据目录：``~/Worksplice``（可见、可直接复制），可用环境变量覆盖。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DATA_DIR_NAME


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Database:
    """单文件库的窄接口。写路径串行化（SQLite 单写者）。"""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / DATABASE_FILE_NAME
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    # ─── 生命周期 ──────────────────────────────────────────────────────────
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(SCHEMA_VERSION),),
            )
            self._conn.execute(
                "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
                "VALUES (?, 'owner', ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (OWNER_ID, OWNER_DISPLAY_NAME, OWNER_AVATAR_SLOT, utc_now()),
            )

    # ─── 事务助手 ──────────────────────────────────────────────────────────
    def _begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._conn.execute("COMMIT")

    def _rollback(self) -> None:
        with suppress(sqlite3.OperationalError):  # 事务已经结束
            self._conn.execute("ROLLBACK")

    # ─── 成员 ─────────────────────────────────────────────────────────────
    def list_members(self, kind: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        if kind not in (None, "owner", "agent"):
            raise DomainError("validation_failed", f"未知的成员类型：{kind}", fields={"kind": "成员类型非法"})
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM member "
                "WHERE (:include_deleted = 1 OR deleted_at IS NULL) "
                "AND (:kind IS NULL OR kind = :kind) "
                "ORDER BY CASE kind WHEN 'owner' THEN 0 ELSE 1 END, created_at, id",
                {"include_deleted": 1 if include_deleted else 0, "kind": kind},
            ).fetchall()
        return [self._member_payload(row) for row in rows]

    def get_member(self, member_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            raise NotFound(f"成员不存在：{member_id}")
        return self._member_payload(row)

    @staticmethod
    def _member_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "display_name": row["display_name"],
            "avatar_slot": row["avatar_slot"],
            "created_at": row["created_at"],
            "deleted_at": row["deleted_at"],
        }

    # ─── 频道 ─────────────────────────────────────────────────────────────
    def list_channels(self, member_id: str = OWNER_ID) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                CHANNEL_SELECT_SQL, {"member_id": member_id, "channel_id": None}
            ).fetchall()
        return [self._channel_payload(row) for row in rows]

    def get_channel(self, channel_id: str, member_id: str = OWNER_ID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                CHANNEL_SELECT_SQL, {"member_id": member_id, "channel_id": channel_id}
            ).fetchone()
        if row is None:
            raise NotFound(f"频道不存在：{channel_id}")
        return self._channel_payload(row)

    @staticmethod
    def _channel_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "created_at": row["created_at"],
            "last_seq": row["last_seq"],
            "last_read_seq": row["last_read_seq"],
            "unread_count": row["unread_count"],
        }

    def list_channel_members(self, channel_id: str) -> list[dict[str, Any]]:
        self.get_channel(channel_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.* FROM channel_member cm JOIN member m ON m.id = cm.member_id "
                "WHERE cm.channel_id = ? ORDER BY CASE m.kind WHEN 'owner' THEN 0 ELSE 1 END, m.created_at",
                (channel_id,),
            ).fetchall()
        return [self._member_payload(row) for row in rows]

    def create_channel(
        self,
        name: str,
        description: str = "",
        member_ids: Sequence[str] | None = None,
        creator_id: str = OWNER_ID,
    ) -> dict[str, Any]:
        """校验整体通过后才写库：任一字段不合法则**不留任何半成品行**。"""
        clean_name = (name or "").strip()
        clean_description = (description or "").strip()
        requested = list(dict.fromkeys(member_ids or []))

        fields: dict[str, str] = {}
        if not clean_name:
            fields["name"] = "频道名称不能为空"
        elif len(clean_name) > MAX_CHANNEL_NAME_LENGTH:
            fields["name"] = f"频道名称最多 {MAX_CHANNEL_NAME_LENGTH} 个字符"
        if len(clean_description) > MAX_CHANNEL_DESCRIPTION_LENGTH:
            fields["description"] = f"频道描述最多 {MAX_CHANNEL_DESCRIPTION_LENGTH} 个字符"
        if fields:
            raise DomainError("validation_failed", "频道校验未通过，未创建任何数据", fields=fields)

        self.get_member(creator_id)
        selected: list[dict[str, Any]] = []
        for member_id in requested:
            with self._lock:
                row = self._conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise DomainError(
                    "validation_failed",
                    "频道校验未通过，未创建任何数据",
                    fields={"member_ids": f"成员不存在或已删除：{member_id}"},
                )
            if row["kind"] != "agent":
                raise DomainError(
                    "validation_failed",
                    "频道校验未通过，未创建任何数据",
                    fields={"member_ids": f"初始成员只能是 Agent：{member_id}"},
                )
            selected.append(self._member_payload(row))

        channel_id = new_id("ch")
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO channel(id, name, description, created_at) VALUES (?, ?, ?, ?)",
                    (channel_id, clean_name, clean_description, now),
                )
                participants = [creator_id] + [member["id"] for member in selected]
                for member_id in participants:
                    self._conn.execute(
                        "INSERT INTO channel_member(channel_id, member_id) VALUES (?, ?)",
                        (channel_id, member_id),
                    )
                    self._conn.execute(
                        "INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES (?, ?, 0) "
                        "ON CONFLICT(member_id, channel_id) DO NOTHING",
                        (member_id, channel_id),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_channel(channel_id, creator_id)

    # ─── 消息 ─────────────────────────────────────────────────────────────
    def list_messages(self, channel_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self.get_channel(channel_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
                       a.avatar_slot AS author_avatar_slot
                  FROM message m JOIN member a ON a.id = m.author_member_id
                 WHERE m.target_id = ? AND m.seq > ?
                 ORDER BY m.seq
                """,
                (channel_id, after_seq),
            ).fetchall()
        return [self._message_payload(row) for row in rows]

    def append_message(
        self,
        channel_id: str,
        body: str,
        author_member_id: str = OWNER_ID,
        kind: str = "human",
        mention_snapshot: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """在同一事务内分配频道内单调 seq 并落库。失败则不落任何行。"""
        if kind not in MESSAGE_KINDS:
            raise DomainError("validation_failed", f"未知的消息类型：{kind}", fields={"kind": "消息类型非法"})
        clean_body = (body or "").strip()
        if not clean_body:
            raise DomainError("validation_failed", "消息正文不能为空", fields={"body": "消息正文不能为空"})
        if len(clean_body) > MAX_MESSAGE_BODY_LENGTH:
            raise DomainError(
                "validation_failed",
                f"消息正文最多 {MAX_MESSAGE_BODY_LENGTH} 个字符",
                fields={"body": f"消息正文最多 {MAX_MESSAGE_BODY_LENGTH} 个字符"},
            )
        self.get_channel(channel_id)  # 404 早退
        self.get_member(author_member_id)

        message_id = new_id("msg")
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM message WHERE target_id = ?",
                    (channel_id,),
                ).fetchone()
                seq = int(row["next_seq"])
                self._conn.execute(
                    "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
                    "mention_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        message_id,
                        channel_id,
                        seq,
                        author_member_id,
                        kind,
                        clean_body,
                        now,
                        json.dumps(list(mention_snapshot or []), ensure_ascii=False),
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_message(channel_id, seq)

    def get_message(self, channel_id: str, seq: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
                       a.avatar_slot AS author_avatar_slot
                  FROM message m JOIN member a ON a.id = m.author_member_id
                 WHERE m.target_id = ? AND m.seq = ?
                """,
                (channel_id, seq),
            ).fetchone()
        if row is None:
            raise NotFound(f"消息不存在：{channel_id}#{seq}")
        return self._message_payload(row)

    @staticmethod
    def _message_payload(row: sqlite3.Row) -> dict[str, Any]:
        try:
            mentions = json.loads(row["mention_snapshot"])
        except (ValueError, TypeError):
            mentions = []
        return {
            "id": row["id"],
            "target_id": row["target_id"],
            "seq": row["seq"],
            "kind": row["kind"],
            "body": row["body"],
            "created_at": row["created_at"],
            "mention_snapshot": mentions,
            "author": {
                "id": row["author_member_id"],
                "kind": row["author_kind"],
                "display_name": row["author_name"],
                "avatar_slot": row["author_avatar_slot"],
            },
        }

    # ─── 已读位置（未读的正本） ───────────────────────────────────────────
    def mark_read(self, channel_id: str, seq: int, member_id: str = OWNER_ID) -> dict[str, Any]:
        """把已读位置单调推进到 ``min(seq, 该频道当前最大 seq)``。

        上界裁剪保证「把未来消息提前标为已读」不可能发生；已读位置永不回退。
        """
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise DomainError("validation_failed", "seq 必须是非负整数", fields={"seq": "seq 必须是非负整数"})
        self.get_channel(channel_id)
        self.get_member(member_id)
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM message WHERE target_id = ?",
                    (channel_id,),
                ).fetchone()
                effective = min(seq, int(row["last_seq"]))
                self._conn.execute(
                    "INSERT INTO channel_read(member_id, channel_id, last_read_seq) VALUES (?, ?, ?) "
                    "ON CONFLICT(member_id, channel_id) DO UPDATE SET "
                    "last_read_seq = MAX(channel_read.last_read_seq, excluded.last_read_seq)",
                    (member_id, channel_id, effective),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_channel(channel_id, member_id)


def open_database(data_dir: Path | str | None = None) -> Database:
    return Database(data_dir if data_dir is not None else default_data_dir())
