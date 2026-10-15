"""单文件 SQLite 持久化：target、成员、消息与每 target 已读位置的正本。

本模块是应用后端的持久化正本（spec #1 §2、第二版规格 #23 §2）。约束：

- 消息 **append-only**：不存在更新或删除消息的代码路径（库级触发器兜底）。
- **target 内**单调 ``seq``：``UNIQUE(target_id, seq)``，由本模块在同一事务内分配。
- 已读位置 ``target_read.last_read_seq`` 只在同一 target 内取 max 单调推进。
- 未读计数 = ``last_read_seq`` 之后 ``kind in ('human','agent')``、**作者不是本人**的顶层
  消息条数（#4 的人类裁定排除本人作者：#2 原本把 owner 自己的消息也计入）。

**消息作用域是 target，不是频道**（ADR-0004）：``target(id, kind ∈ {channel, dm, task})`` 是消息、
已读位置、回应预算与轮次结果的共同键。**channel 型 target 复用 ``channel.id``**——同一个字符串既是
频道主键也是 target 主键，``target`` 行对 channel 型是一张**影子行**（id 同值、只承载 ``kind``）；
频道的 ``name`` / ``description`` 仍留在 ``channel`` 表，**没有**映射表。三处旧名随作用域改名：
``channel_member → target_member``、``channel_read → target_read``、``channel_budget → target_budget``。

空库只含一条固定成员行 ``owner``（规格固定的唯一成员行），没有任何业务种子数据。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping, Sequence
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

SCHEMA_VERSION = 6

DEFAULT_PORT = 8788
DEFAULT_DATA_DIR_NAME = "Alibot"
DATA_DIR_ENV = "ALIBOT_DATA_DIR"
DATABASE_FILE_NAME = "alibot.db"

MESSAGE_KINDS = ("human", "agent", "system")

# target 的三种 kind（ADR-0004 / 规格 #23 §1）：channel 型复用 channel.id（影子行），
# dm / task 由各自的服务路径创建与管理；本表统一承载三种 target 作用域。
TARGET_KINDS = ("channel", "dm", "task")

# 计入工作台快照 ``unread_total`` 的 kind（规格 §2 / 决策 G5）：channel 型 + dm 型之和。
# **task 型不进** ``unread_total``（看板卡片自带徽标，各自读它）。
UNREAD_TOTAL_KINDS = ("channel", "dm")

TASK_STATUSES = ("todo", "in_progress", "in_review", "done", "closed")
TASK_STATUS_TRANSITIONS = {
    "todo": {"in_progress", "closed"},
    "in_progress": {"in_review", "todo", "closed"},
    "in_review": {"done", "in_progress", "todo"},
    "done": {"in_progress", "closed"},
    "closed": set(),
}
TASK_STATUS_LABELS = {
    "todo": "待认领",
    "in_progress": "进行中",
    "in_review": "待审",
    "done": "完成",
    "closed": "已关闭",
}

# 轮次结果的五种状态（spec #1 §5）。status 是「自判忽略」与「处理失败」的区分来源。
ROUND_STATUSES = ("replied", "ignored", "failed", "merged", "budget-exhausted")

# 有界响应预算（策略 B，spec #1 §5）：一个**人类世代内同一 target** 允许的 agent→agent 跳数上界。
# 它是**常量**：本票不做配置化（那是 #31 的范围），只把「每频道一份」泛化为「每 target 一份」。
BUDGET_LIMIT = 3

# 判断「这个库是不是空库」时要看的业务表名（见 :meth:`Database._assert_schema_compatible`）。
# 版本号缺失时**无法**从版本号判断库来自哪一代，所以新旧两代的名字都在列表里：
# 只要其中**任意一张表有任何一行**，这个库就有数据，不是空库。
BUSINESS_TABLES = (
    "member",
    "agent_config",
    "channel",
    "channel_member",
    "target",
    "target_member",
    "task",
    "message",
    "channel_read",
    "target_read",
    "channel_budget",
    "target_budget",
    "round_outcome",
    "remote_consent",
)

# 逐表计数语句：由上面的常量在模块级拼好（与 ``AGENT_SELECT_SQL`` / ``UNREAD_COUNT_SQL`` 同一体例：
# **不把拼接写进 ``execute`` 的实参**）。表名只能来自上面的字面量元组，不是外部输入，
# 因此这里不存在标识符注入面。
BUSINESS_COUNT_SQL: dict[str, str] = {table: f'SELECT COUNT(*) FROM "{table}"' for table in BUSINESS_TABLES}

MAX_CHANNEL_NAME_LENGTH = 80
MAX_CHANNEL_DESCRIPTION_LENGTH = 500
MAX_MESSAGE_BODY_LENGTH = 8000
MAX_MEMBER_NAME_LENGTH = 40
MAX_AGENT_ROLE_LENGTH = 80
MAX_SYSTEM_PROMPT_LENGTH = 8000
MAX_MODEL_ID_LENGTH = 120

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

-- target 是一切消息面的**唯一作用域键**（ADR-0004）：消息、已读位置、回应预算、轮次结果四件事
-- 全部挂 ``target_id``，``UNIQUE(target_id, seq)`` 因此说的是「target 内单调」。
-- **channel 型是一张影子行**：``id`` 与 ``channel.id`` 同值（复用，同一个字符串既是频道主键也是
-- target 主键），本表只承载 ``kind``；频道自己的 ``name`` / ``description`` 仍留在 ``channel`` 表，
-- 因此**没有**映射表。影子行的生命周期由频道决定：本仓没有删除频道的入口，也就没有删除它的路径。
-- 列的 CHECK 是 ``kind`` 受约束的正本（三种取值）；dm / task 型由各自的业务路径创建。
CREATE TABLE IF NOT EXISTS target (
  id                 TEXT PRIMARY KEY,
  kind               TEXT NOT NULL CHECK (kind IN ('channel', 'dm', 'task')),
  dm_peer_member_id  TEXT REFERENCES member(id),
  created_at         TEXT NOT NULL
);

-- 每个 Agent 最多一条 DM；频道与任务行的该列为空，不参与索引。
CREATE UNIQUE INDEX IF NOT EXISTS idx_target_dm_peer_unique
ON target(dm_peer_member_id) WHERE kind = 'dm' AND dm_peer_member_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS target_member (
  target_id TEXT NOT NULL REFERENCES target(id),
  member_id TEXT NOT NULL REFERENCES member(id),
  PRIMARY KEY (target_id, member_id)
);

-- 任务当前态在 task 行上；变更历史只进入该任务 target 的 append-only 消息流（ADR-0006）。
CREATE TABLE IF NOT EXISTS task (
  id                 TEXT PRIMARY KEY,
  target_id          TEXT NOT NULL UNIQUE REFERENCES target(id),
  channel_id         TEXT NOT NULL REFERENCES channel(id),
  title              TEXT NOT NULL CHECK (length(trim(title)) > 0),
  status             TEXT NOT NULL CHECK (status IN ('todo', 'in_progress', 'in_review', 'done', 'closed')),
  assignee_member_id TEXT REFERENCES member(id),
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (target_id, id)
);

CREATE TRIGGER IF NOT EXISTS task_target_must_be_task
BEFORE INSERT ON task
WHEN (SELECT kind FROM target WHERE id = NEW.target_id) != 'task'
BEGIN
  SELECT RAISE(ABORT, 'task must own a task target');
END;

CREATE TRIGGER IF NOT EXISTS task_channel_must_be_channel
BEFORE INSERT ON task
WHEN (SELECT kind FROM target WHERE id = NEW.channel_id) != 'channel'
BEGIN
  SELECT RAISE(ABORT, 'task must belong to a channel target');
END;

CREATE TABLE IF NOT EXISTS message (
  id               TEXT PRIMARY KEY,
  target_id        TEXT NOT NULL REFERENCES target(id),
  seq              INTEGER NOT NULL,
  author_member_id TEXT NOT NULL REFERENCES member(id),
  kind             TEXT NOT NULL CHECK (kind IN ('human', 'agent', 'system')),
  body             TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  mention_snapshot TEXT NOT NULL DEFAULT '[]',
  task_ref         TEXT,
  FOREIGN KEY (target_id, task_ref) REFERENCES task(target_id, id),
  UNIQUE (target_id, seq)
);

CREATE TABLE IF NOT EXISTS target_read (
  member_id     TEXT NOT NULL REFERENCES member(id),
  target_id     TEXT NOT NULL REFERENCES target(id),
  last_read_seq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (member_id, target_id)
);

-- 有界响应预算（策略 B，spec #1 §5）的正本：每 **target** 一行（ADR-0004 的泛化，机制逐字不变）。
-- ``epoch_seq`` = 人类世代（该 target 最近一次**开世代动作**所在消息的 seq；0 = 还没有开世代动作）；
-- ``used`` = 该世代内**已投递**的 agent→agent 跳数。
-- 落库而不是放进程内存：「已用/剩余」必须在后端重启后仍是同一条事实（验收项明写）。
-- **每 target 一份**：「一个频道烧完把另一个频道锁死」因此不可能发生（跨 target 不锁死）。
CREATE TABLE IF NOT EXISTS target_budget (
  target_id TEXT PRIMARY KEY REFERENCES target(id),
  epoch_seq INTEGER NOT NULL DEFAULT 0,
  used      INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)
);

-- Agent 的模型配置：模型是可配置数据而不是代码常量（spec #1 §2）。
-- role 是 Agent 的配置数据（用户输入原样落库），不是代码常量、也不折进系统提示。
CREATE TABLE IF NOT EXISTS agent_config (
  member_id     TEXT PRIMARY KEY REFERENCES member(id),
  role          TEXT NOT NULL DEFAULT '',
  model_id      TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL DEFAULT '',
  enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- 远端数据流告知的一次性确认（spec #1 §8）：按模型提供商记一次，与成员无关。
-- 这里**只**存「何时确认过」，不存任何凭证。
CREATE TABLE IF NOT EXISTS remote_consent (
  provider        TEXT PRIMARY KEY,
  acknowledged_at TEXT NOT NULL
);

-- 轮次结果（spec #1 §5）：一轮的处理结果。append-only，与消息同级的取证面。
-- ``consumed_budget`` 是**行级**标记，不是跳的计数：它说的是「这一轮是由**一次已投递并被计一次的
-- agent→agent 跳**送来的」。**一次跳投递 N 个成员就有 N 条为 1 的行**，而跳数只 +1，因此
-- **它不可按行求和**。该人类世代的**跳数唯一正本**是 ``target_budget.used``（见上面的表注释）。
CREATE TABLE IF NOT EXISTS round_outcome (
  id              TEXT PRIMARY KEY,
  member_id       TEXT NOT NULL REFERENCES member(id),
  target_id       TEXT NOT NULL REFERENCES target(id),
  base_seq        INTEGER NOT NULL,
  status          TEXT NOT NULL CHECK (status IN
                    ('replied', 'ignored', 'failed', 'merged', 'budget-exhausted')),
  reason          TEXT NOT NULL DEFAULT '',
  started_at      TEXT NOT NULL,
  finished_at     TEXT NOT NULL,
  consumed_budget INTEGER NOT NULL DEFAULT 0 CHECK (consumed_budget IN (0, 1)),
  merged          INTEGER NOT NULL DEFAULT 0 CHECK (merged IN (0, 1)),
  reply_seq       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_round_outcome_target ON round_outcome(target_id, finished_at);
CREATE INDEX IF NOT EXISTS idx_round_outcome_member ON round_outcome(member_id, finished_at);

CREATE INDEX IF NOT EXISTS idx_target_member_member ON target_member(member_id);

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

-- 轮次结果同样 append-only：它是「当时为什么没说话」的事实记录，不接受事后改写。
CREATE TRIGGER IF NOT EXISTS round_outcome_no_update
BEFORE UPDATE ON round_outcome
BEGIN
  SELECT RAISE(ABORT, 'round_outcome is append-only');
END;

CREATE TRIGGER IF NOT EXISTS round_outcome_no_delete
BEFORE DELETE ON round_outcome
BEGIN
  SELECT RAISE(ABORT, 'round_outcome is append-only');
END;
"""

# 频道读取的唯一一条 SQL（无拼接，值全部经命名占位符绑定）：
# - ``:channel_id IS NULL`` 时返回全部频道（列表）；给值时返回单条。
# - 未读计数口径**只在这里写一次**（规格 §2 与 #4 的人类裁定）：已读位置之后、
#   kind ∈ {human, agent}、**且作者不是本人**的顶层消息；不计 system。
#   unread_count（列表/单条）与 unread_total（workspace 汇总）因此共享同一个形状。
# - 作用域键是 **target_id**，不是 channel_id：channel 型 target 复用 ``channel.id``（影子行），
#   所以 ``m.target_id = c.id`` 这条既有事实逐字不变，但查询本身是 **target 作用域**——
#   **不按 kind 分支**（ADR-0004 决策 8）。
# Agent 行的列清单（member 行 + agent_config 配置列）。三处查询共用：
# 加一个配置列时只改这里，不会只改到其中一两处。
# 三处 FROM/WHERE 各自保留（起点与过滤条件本就不同）—— #3 的双轴 review 已为这个形态裁决过。
AGENT_COLUMNS = "m.*, c.role, c.model_id, c.system_prompt, c.enabled, c.updated_at AS config_updated_at"

# 由常量 ``AGENT_COLUMNS`` 拼出的 Agent 选取语句（**在模块级拼好**，不把拼接写进
# ``execute`` 的实参：查询文本对静态检查是可见常量，也不是注入面）。
AGENT_SELECT_SQL = "SELECT " + AGENT_COLUMNS + " "

# 未读的**唯一一个 WHERE 形状**（ADR-0004 决策 7）：已读位置之后、``kind ∈ {human, agent}``、
# 作者非本人、**只算顶层**。三种 kind 用同一个形状，**不按 kind 分支**。
# 它是**单一字面量落点**：下面三个消费点（频道列表、工作台汇总、单 target 计数）都把它原样拼进
# 自己的 FROM 里，于是不存在第二个形状。别名统一用 ``m``（三个消费点里它是各自子查询的局部名字，
# 互不冲突），因此这里只需一条常量，而不是按别名派生多份。
UNREAD_WHERE_SQL = """
                     AND m.seq > COALESCE(
                           (SELECT r.last_read_seq FROM target_read r
                             WHERE r.member_id = :member_id AND r.target_id = m.target_id), 0)
                     AND m.kind IN ('human', 'agent')
                     AND m.author_member_id != :member_id
"""

CHANNEL_SELECT_SQL = (
    """
    SELECT c.*,
           COALESCE((SELECT MAX(seq) FROM message m WHERE m.target_id = c.id), 0) AS last_seq,
           COALESCE(r.last_read_seq, 0) AS last_read_seq,
           COALESCE((SELECT COUNT(*) FROM message m
                      WHERE m.target_id = c.id
"""
    + UNREAD_WHERE_SQL
    + """
                    ), 0) AS unread_count
      FROM channel c
      LEFT JOIN target_read r ON r.target_id = c.id AND r.member_id = :member_id
     WHERE (:channel_id IS NULL OR c.id = :channel_id)
     ORDER BY c.created_at, c.id
"""
)

# 工作台快照的 ``unread_total`` = **channel 型 + dm 型**之和（决策 G5）：**task 型不进它**。
# 它用的是上面**同一条 WHERE 形状**（同一个 ``UNREAD_WHERE_SQL``），只是在 target 上按 kind 过滤后
# 汇总 ——「只有一个 SQL 形状」因此是结构上的事实，而不是两句彼此长得像的 SQL。
# 取值域由上面的 ``UNREAD_TOTAL_KINDS`` **拼出来**（不是另写一份字面量）：G5 的「channel + dm」
# 因此只有一个落点，改一处不会与另一处漂移。
_UNREAD_TOTAL_KIND_LIST = ", ".join(f"'{kind}'" for kind in UNREAD_TOTAL_KINDS)
UNREAD_TOTAL_SQL = (
    "SELECT COALESCE(SUM((SELECT COUNT(*) FROM message m\n"
    "                      WHERE m.target_id = t.id\n" + UNREAD_WHERE_SQL + "                    )), 0)\n"
    "  FROM target t\n"
    " WHERE t.kind IN (" + _UNREAD_TOTAL_KIND_LIST + ")\n"
)

# 单个 target 的未读条数：**同样的 WHERE 形状**（别名同为 m），区别只在于作用域键直接由参数给出。
UNREAD_COUNT_SQL = "SELECT COUNT(*) FROM message m\n WHERE m.target_id = :target_id\n" + UNREAD_WHERE_SQL

DM_SELECT_SQL = (
    "SELECT t.id, t.created_at, 'dm' AS kind, "
    "       p.id AS peer_id, p.kind AS peer_kind, p.display_name AS peer_display_name, "
    "       p.avatar_slot AS peer_avatar_slot, p.created_at AS peer_created_at, "
    "       p.deleted_at AS peer_deleted_at, COALESCE(MAX(m.seq), 0) AS last_seq, "
    "       COALESCE(r.last_read_seq, 0) AS last_read_seq, "
    "       COALESCE((SELECT COUNT(*) FROM message unread WHERE unread.target_id = t.id\n"
    + UNREAD_WHERE_SQL.replace("m.", "unread.")
    + "       ), 0) AS unread_count "
    "  FROM target t "
    "  JOIN target_member owner_members ON owner_members.target_id = t.id "
    "                              AND owner_members.member_id = :member_id "
    "  JOIN member p ON p.id = t.dm_peer_member_id "
    "  LEFT JOIN message m ON m.target_id = t.id "
    "  LEFT JOIN target_read r ON r.target_id = t.id AND r.member_id = :member_id "
    " WHERE t.kind = 'dm' AND (:dm_id IS NULL OR t.id = :dm_id) "
    " GROUP BY t.id, t.created_at, p.id, p.kind, p.display_name, p.avatar_slot, "
    "          p.created_at, p.deleted_at, r.last_read_seq "
    " ORDER BY t.created_at, t.id"
)


class SchemaVersionMismatch(Exception):
    """库里的 schema 版本与本代码需要的版本不一致：**拒绝启动**，且不改动旧库一个字节。

    它不是 :class:`DomainError`：这条事实发生在**启动路径**上（进程级的拒绝），不是某个 HTTP
    请求的领域错误。HTTP 层因此不会把它折成 500——进程根本不会开始监听。

    ``found_version`` 为 ``None`` 表示「版本号缺失」（没有 ``schema_meta`` 表，或没有
    ``schema_version`` 那一行）。缺失时只有库里**已有业务数据**才会抛这个错：真的空库照常初始化
    （否则空目录首次启动就起不来了）。
    """

    def __init__(self, db_path: Path, found_version: str | None, expected_version: int):
        self.db_path = Path(db_path)
        self.found_version = found_version
        self.expected_version = expected_version
        super().__init__(self._render())

    def _render(self) -> str:
        found = (
            str(self.found_version)
            if self.found_version is not None
            else "缺失（schema_meta 里没有 schema_version 这一行）"
        )
        return (
            "拒绝启动：数据库的 schema 版本与本代码不匹配。\n"
            f"  库文件：{self.db_path}\n"
            f"  库里的版本：{found}\n"
            f"  本代码需要的版本：{self.expected_version}\n"
            "  本版不做数据迁移：请先备份该数据目录，再换一个数据目录启动"
            "（例如 --data-dir <另一个目录>）。"
        )


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
    """默认数据目录：``~/Alibot``（可见、可直接复制），可用环境变量覆盖。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DATA_DIR_NAME


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Database:
    """单文件库的窄接口。写路径串行化（SQLite 单写者）。

    **启动时的版本闸门**（见 :meth:`_assert_schema_compatible`）：打开库之后、任何 schema 变更
    之前，先读 ``schema_meta.schema_version`` 与本代码的 :data:`SCHEMA_VERSION` 比对，不等就抛
    :class:`SchemaVersionMismatch` —— 于是旧库不会被「静默半改名」（版本号被改写、新表建出来但
    是空的、旧表数据还在）。
    """

    def __init__(self, data_dir: Path | str):
        self._connect(Path(data_dir))
        self._initialize()

    @classmethod
    def open_without_initializing(cls, data_dir: Path | str) -> Database:
        """打开库、过**同一道版本闸门**，但**不**做 schema 初始化（不建表、不写行）。

        存在的理由：显式修复入口需要先“看清这个库实际长什么样”——某张表到底在不在、里面有多少行
        ——才能决定补什么。用 ``Database(...)`` 做不到这点：它的 ``_initialize()`` 会立刻把缺的表
        ``CREATE IF NOT EXISTS`` 出来，于是“表缺失”这个待诊断的事实会在诊断之前就被抹掉
        （而且那是一次 schema 变更，不该发生在“先看一眼”的时候）。

        版本闸门照旧生效：版本不等时**连看都不让看**，抛 :class:`SchemaVersionMismatch`。
        """
        database = cls.__new__(cls)
        database._connect(Path(data_dir))
        database._assert_schema_compatible()
        return database

    def _connect(self, data_dir: Path) -> None:
        """建立连接（构造函数与 :meth:`open_without_initializing` 共用）。

        这里**只**做连接级设置：它们不写业务行、不改 schema，也**不改库文件一个字节**，因此可以
        安全地出现在版本闸门之前（闸门要在一个原样的库上做判断）。
        """
        self.data_dir = data_dir
        self.path = self.data_dir / DATABASE_FILE_NAME
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")

    def _apply_write_pragmas(self) -> None:
        """把这个库切到 WAL。**必须在版本闸门之后调用。**

        ``PRAGMA journal_mode = WAL`` 不是连接级开关：它要把库文件头里的 journal mode 从
        ``delete`` 改成 ``wal``（实测：对一个非 WAL 库执行它，文件 sha256 会变）。对**拒绝路径**
        来说那就等于「改动了旧库」，而本票的硬要求是旧库**一个字节都不许变**。所以它不能和上面
        那两条连接级 PRAGMA 放在一起，而必须等闸门放行之后才执行。

        这一条是实测发现的：初版把它放在闸门之前，只有用**已是 WAL 形态**的旧库取证时看不出问题
        （那时这条 PRAGMA 是 no-op）；换一个非 WAL 旧库，sha256 就变了。
        """
        self._conn.execute("PRAGMA journal_mode = WAL")

    # ─── 生命周期 ──────────────────────────────────────────────────────────
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _stored_schema_version(self) -> str | None:
        """读库里的 ``schema_meta.schema_version``；表或行不存在时返回 ``None``（**只读**）。"""
        meta = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if meta is None:
            return None
        row = self._conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return None if row is None else str(row[0])

    def _has_business_data(self) -> bool:
        """库里是否**已有业务数据**（任意业务表有任何一行）。只读；表不存在时按没有数据处理。"""
        existing = {
            str(row[0])
            for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        for table in BUSINESS_TABLES:
            if table not in existing:
                continue
            row = self._conn.execute(BUSINESS_COUNT_SQL[table]).fetchone()
            if row is not None and int(row[0]) > 0:
                return True
        return False

    def _assert_schema_compatible(self) -> None:
        """版本闸门：不等就**拒绝**，且**不做任何 schema 变更**。

        三条分支（与票面 Change 逐条对应）：

        - 库不存在 / 没有任何业务数据（且版本号缺失或就是本版）⇒ 放行，由 :meth:`_initialize` 建库。
        - 版本号**等于** :data:`SCHEMA_VERSION` ⇒ 放行（既有路径行为一字不变）。
        - 版本号**不等于**本版 ⇒ 抛 :class:`SchemaVersionMismatch`（旧库、未来库、垃圾版本号都算）。
          版本号**缺失但库里有业务数据**同样拒绝——那不是空库，是来源不明的库。

        **不修版本号、不跳过、不猜测**：本版不做迁移（规格决策 8 / ADR-0004），因此唯一正确的
        处置是让用户拿一个干净的数据目录启动，并把旧库原样留着（它一个字节都没被动过）。
        """
        stored = self._stored_schema_version()
        if stored == str(SCHEMA_VERSION):
            return
        if stored is None and not self._has_business_data():
            return
        raise SchemaVersionMismatch(self.path, found_version=stored, expected_version=SCHEMA_VERSION)

    def _initialize(self) -> None:
        """闸门先于**任何** schema 变更（也先于 WAL 切换）：不等就抛，于是旧库不会被静默半改名。"""
        with self._lock:
            self._assert_schema_compatible()
            self._apply_write_pragmas()
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
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

    # ─── Agent 配置（模型与系统提示是可配置数据，不是代码常量） ─────────
    def agent_members(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        """全部 Agent 成员（含其配置），按创建顺序。"""
        with self._lock:
            rows = self._conn.execute(
                AGENT_SELECT_SQL + "FROM member m LEFT JOIN agent_config c ON c.member_id = m.id "
                "WHERE m.kind = 'agent' AND m.deleted_at IS NULL "
                "ORDER BY m.created_at, m.id",
            ).fetchall()
        return [self._agent_payload(row) for row in rows]

    def get_agent(self, member_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                AGENT_SELECT_SQL + "FROM member m LEFT JOIN agent_config c ON c.member_id = m.id "
                "WHERE m.id = ? AND m.kind = 'agent'",
                (member_id,),
            ).fetchone()
        if row is None:
            raise NotFound(f"Agent 不存在：{member_id}")
        return self._agent_payload(row)

    @staticmethod
    def _agent_payload(row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "kind": row["kind"],
            "display_name": row["display_name"],
            "avatar_slot": row["avatar_slot"],
            "created_at": row["created_at"],
            "deleted_at": row["deleted_at"],
            "role": row["role"] or "",
            "model_id": row["model_id"] or "",
            "system_prompt": row["system_prompt"] or "",
            "enabled": bool(row["enabled"]) if row["enabled"] is not None else False,
            "config_updated_at": row["config_updated_at"] if "config_updated_at" in keys else None,
        }

    def create_agent(
        self,
        name: str,
        role: str,
        model_id: str,
        system_prompt: str,
        avatar_slot: int | None = None,
    ) -> dict[str, Any]:
        """校验整体通过后才写库：任一字段不合法则**不留任何半成品行**。

        名称与模型标识必填；模型标识的格式（provider 白名单）由服务层校验，
        本层只负责长度与空值这一类与持久化同构的判据。
        """
        clean_name = (name or "").strip()
        clean_role = (role or "").strip()
        clean_model = (model_id or "").strip()
        clean_prompt = (system_prompt or "").strip()

        fields: dict[str, str] = {}
        if not clean_name:
            fields["name"] = "Agent 名称不能为空"
        elif len(clean_name) > MAX_MEMBER_NAME_LENGTH:
            fields["name"] = f"Agent 名称最多 {MAX_MEMBER_NAME_LENGTH} 个字符"
        if not clean_model:
            fields["model_id"] = "模型标识不能为空"
        elif len(clean_model) > MAX_MODEL_ID_LENGTH:
            fields["model_id"] = f"模型标识最多 {MAX_MODEL_ID_LENGTH} 个字符"
        if len(clean_role) > MAX_AGENT_ROLE_LENGTH:
            fields["role"] = f"角色最多 {MAX_AGENT_ROLE_LENGTH} 个字符"
        if len(clean_prompt) > MAX_SYSTEM_PROMPT_LENGTH:
            fields["system_prompt"] = f"系统提示最多 {MAX_SYSTEM_PROMPT_LENGTH} 个字符"
        if fields:
            raise DomainError("validation_failed", "Agent 校验未通过，未创建任何数据", fields=fields)

        with self._lock:
            if avatar_slot is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM member WHERE kind = 'agent'").fetchone()
                avatar_slot = int(row["n"]) % AVATAR_SLOT_COUNT
            member_id = new_id("mem")
            now = utc_now()
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO member(id, kind, display_name, avatar_slot, created_at) "
                    "VALUES (?, 'agent', ?, ?, ?)",
                    (member_id, clean_name, avatar_slot, now),
                )
                self._conn.execute(
                    "INSERT INTO agent_config(member_id, role, model_id, system_prompt, enabled, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (member_id, clean_role, clean_model, clean_prompt, now, now),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_agent(member_id)

    # ─── 远端数据流告知（按模型提供商一次性确认） ────────────────────────
    def list_remote_consents(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, acknowledged_at FROM remote_consent ORDER BY acknowledged_at, provider"
            ).fetchall()
        return [{"provider": row["provider"], "acknowledged_at": row["acknowledged_at"]} for row in rows]

    def has_remote_consent(self, provider: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM remote_consent WHERE provider = ?", (provider,)
            ).fetchone()
        return row is not None

    def record_remote_consent(self, provider: str) -> dict[str, Any]:
        """记下一次告知确认。幂等：重复确认不覆盖首次时间。本表不存任何凭证。"""
        clean = (provider or "").strip()
        if not clean:
            raise DomainError(
                "validation_failed", "模型提供商不能为空", fields={"provider": "模型提供商不能为空"}
            )
        now = utc_now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO remote_consent(provider, acknowledged_at) VALUES (?, ?) "
                "ON CONFLICT(provider) DO NOTHING",
                (clean, now),
            )
            row = self._conn.execute(
                "SELECT provider, acknowledged_at FROM remote_consent WHERE provider = ?", (clean,)
            ).fetchone()
        return {"provider": row["provider"], "acknowledged_at": row["acknowledged_at"]}

    # ─── 轮次结果（append-only） ────────────────────────────────────────
    def append_round_outcome(
        self,
        member_id: str,
        target_id: str,
        base_seq: int,
        status: str,
        reason: str = "",
        started_at: str | None = None,
        finished_at: str | None = None,
        consumed_budget: bool = False,
        merged: bool = False,
        reply_seq: int | None = None,
    ) -> dict[str, Any]:
        """落一条轮次结果。

        ``consumed_budget`` 是**行级**标记（「这一轮所属的那次跳」），**不是**跳的计数：
        一次跳投递 N 个成员会写 N 条 ``consumed_budget=1`` 的行，而跳数只 +1。**不可按行求和** ——
        该人类世代的跳数唯一正本是 ``target_budget.used``（:meth:`budget_state`）。
        行数与跳数之间**没有固定关系**（投递的成员若无可落库内容就不落行），所以按行求和得到的数
        既可能偏大也可能偏小；它不是跳数。
        """
        if status not in ROUND_STATUSES:
            raise DomainError(
                "validation_failed", f"未知的轮次结果状态：{status}", fields={"status": "轮次结果状态非法"}
            )
        now = utc_now()
        outcome_id = new_id("round")
        with self._lock:
            self._conn.execute(
                "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, reason, started_at, "
                "finished_at, consumed_budget, merged, reply_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outcome_id,
                    member_id,
                    target_id,
                    int(base_seq),
                    status,
                    reason,
                    started_at or now,
                    finished_at or now,
                    1 if consumed_budget else 0,
                    1 if merged else 0,
                    reply_seq,
                ),
            )
        return {
            "id": outcome_id,
            "member_id": member_id,
            "target_id": target_id,
            "base_seq": int(base_seq),
            "status": status,
            "reason": reason,
            "started_at": started_at or now,
            "finished_at": finished_at or now,
            "consumed_budget": bool(consumed_budget),
            "merged": bool(merged),
            "reply_seq": reply_seq,
        }

    def list_round_outcomes(self, target_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        """按 ``finished_at`` 升序取该 target 的轮次结果（``after_seq`` 按 ``base_seq`` 过滤）。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM round_outcome WHERE target_id = ? AND base_seq >= ? ORDER BY finished_at, id",
                (target_id, after_seq),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "member_id": row["member_id"],
                "target_id": row["target_id"],
                "base_seq": row["base_seq"],
                "status": row["status"],
                "reason": row["reason"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "consumed_budget": bool(row["consumed_budget"]),
                "merged": bool(row["merged"]),
                "reply_seq": row["reply_seq"],
            }
            for row in rows
        ]

    # ─── target（消息作用域的正本） ───────────────────────────────────────
    def get_target(self, target_id: str) -> dict[str, Any]:
        """取一个 target 行；不存在则 404。**不按 kind 分支**（ADR-0004 决策 8）。

        它是所有**目标作用域**读写的 404 早退点：``message`` / ``target_read`` / ``target_budget`` /
        ``round_outcome`` 四张表的外键都指向这里，因此'键存在'就是'作用域可用'。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id, kind, created_at FROM target WHERE id = ?", (target_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"target 不存在：{target_id}")
        return {"id": row["id"], "kind": row["kind"], "created_at": row["created_at"]}

    # ─── 任务 ─────────────────────────────────────────────────────────────
    def create_task(self, channel_id: str, title: str) -> dict[str, Any]:
        clean_title = (title or "").strip()
        if not clean_title:
            raise DomainError("validation_failed", "任务标题不能为空", fields={"title": "任务标题不能为空"})
        self.get_channel(channel_id)  # 必须恰属于一个存在的 channel 型 target

        task_id = new_id("task")
        target_id = new_id("tgt")
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    "INSERT INTO target(id, kind, created_at) VALUES (?, 'task', ?)", (target_id, now)
                )
                self._conn.execute(
                    "INSERT INTO task(id, target_id, channel_id, title, status, assignee_member_id, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, 'todo', NULL, ?, ?)",
                    (task_id, target_id, channel_id, clean_title, now, now),
                )
                self._conn.execute(
                    "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)", (target_id, OWNER_ID)
                )
                self._conn.execute(
                    "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0)",
                    (OWNER_ID, target_id),
                )
                self._conn.execute(
                    "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
                    "mention_snapshot) VALUES (?, ?, 1, ?, 'system', '任务已创建', ?, '[]')",
                    (new_id("msg"), target_id, OWNER_ID, now),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id)

    def get_task(self, task_id: str, member_id: str = OWNER_ID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT t.*, c.name AS channel_name FROM task t "
                "JOIN channel c ON c.id = t.channel_id WHERE t.id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise NotFound(f"任务不存在：{task_id}")
        task = {
            "id": row["id"],
            "target_id": row["target_id"],
            "channel_id": row["channel_id"],
            "channel_name": row["channel_name"],
            "title": row["title"],
            "status": row["status"],
            "assignee_member_id": row["assignee_member_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        task["unread_count"] = self.unread_count(task["target_id"], member_id)
        return task

    def list_tasks(self, channel_id: str | None = None, member_id: str = OWNER_ID) -> list[dict[str, Any]]:
        if channel_id is not None:
            self.get_channel(channel_id)  # 非法筛选频道明确 404，不能伪装成空结果
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM task WHERE (:channel_id IS NULL OR channel_id = :channel_id) "
                "ORDER BY created_at, id",
                {"channel_id": channel_id},
            ).fetchall()
        return [self.get_task(row["id"], member_id) for row in rows]

    def list_task_messages(self, task_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        task = self.get_task(task_id)  # task 面只认任务，不把任意 target 当成任务线程
        return self.list_messages(task["target_id"], after_seq=after_seq)

    def _append_task_system_message_locked(self, target_id: str, body: str, now: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM message WHERE target_id = ?", (target_id,)
        ).fetchone()
        seq = int(row["next_seq"])
        self._conn.execute(
            "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
            "mention_snapshot) VALUES (?, ?, ?, ?, 'system', ?, ?, '[]')",
            (new_id("msg"), target_id, seq, OWNER_ID, body, now),
        )
        return seq

    def update_task_status(self, task_id: str, status: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if status not in TASK_STATUSES:
            raise DomainError("validation_failed", f"未知的任务状态：{status}", fields={"status": "任务状态非法"})
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, status FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(f"任务不存在：{task_id}")
                current = str(row["status"])
                target_id = str(row["target_id"])
                if status == current:
                    self._commit()
                    return self.get_task(task_id), None
                if status not in TASK_STATUS_TRANSITIONS[current]:
                    raise DomainError(
                        "validation_failed",
                        f"任务不能从 {current} 迁移到 {status}；closed 是终态",
                        fields={"status": f"不允许从 {current} 迁移到 {status}"},
                    )
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id)
                )
                seq = self._append_task_system_message_locked(
                    target_id, f"状态推进至 {TASK_STATUS_LABELS[status]}", now
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def update_task_title(self, task_id: str, title: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        clean_title = (title or "").strip()
        if not clean_title:
            raise DomainError("validation_failed", "任务标题不能为空", fields={"title": "任务标题不能为空"})
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, title FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(f"任务不存在：{task_id}")
                target_id = str(row["target_id"])
                if clean_title == row["title"]:
                    self._commit()
                    return self.get_task(task_id), None
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET title = ?, updated_at = ? WHERE id = ?", (clean_title, now, task_id)
                )
                seq = self._append_task_system_message_locked(target_id, f"标题已更改为：{clean_title}", now)
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def update_task_assignee(
        self, task_id: str, member_id: str | None
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        assignee: dict[str, Any] | None = None
        if member_id is not None:
            try:
                assignee = self.get_member(member_id)
            except NotFound as exc:
                raise DomainError(
                    "validation_failed", "指派对象不存在", fields={"member_id": "Agent 不存在"}
                ) from exc
            if assignee["deleted_at"] is not None or assignee["kind"] != "agent":
                raise DomainError(
                    "validation_failed", "任务只能指派给未删除的 Agent", fields={"member_id": "指派对象必须是 Agent"}
                )
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, assignee_member_id FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(f"任务不存在：{task_id}")
                target_id = str(row["target_id"])
                previous_id = row["assignee_member_id"]
                if member_id == previous_id:
                    self._commit()
                    return self.get_task(task_id), None
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET assignee_member_id = ?, updated_at = ? WHERE id = ?",
                    (member_id, now, task_id),
                )
                if previous_id is not None:
                    self._conn.execute(
                        "DELETE FROM target_member WHERE target_id = ? AND member_id = ?",
                        (target_id, previous_id),
                    )
                    self._conn.execute(
                        "DELETE FROM target_read WHERE target_id = ? AND member_id = ?",
                        (target_id, previous_id),
                    )
                if member_id is not None:
                    self._conn.execute(
                        "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)",
                        (target_id, member_id),
                    )
                    self._conn.execute(
                        "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0)",
                        (member_id, target_id),
                    )
                label = f"已指派给 {assignee['display_name']}" if assignee is not None else "指派已取消"
                seq = self._append_task_system_message_locked(target_id, label, now)
                if member_id is not None:
                    self._conn.execute(
                        "INSERT INTO target_budget(target_id, epoch_seq, used) VALUES (?, ?, 0) "
                        "ON CONFLICT(target_id) DO UPDATE SET epoch_seq = excluded.epoch_seq, used = 0",
                        (target_id, seq),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def create_target(self, kind: str, target_id: str | None = None) -> dict[str, Any]:
        """落一个新的 **dm / task 型** target 行。

        **本方法没有生产调用点**：它只插一行 target，不做 dm / task 业务校验；业务创建分别走
        :meth:`create_dm` / :meth:`create_task` 的完整写路径。这里只供
        `app/tests/test_target_scope.py` 构造作用域数据、取证共享 target 结构。

        **channel 型不经此处**：它复用 ``channel.id``、由 :meth:`create_channel` 落影子行，
        这里显式拒绝，以免出现'有 target 行却没有对应频道行'的破坏不变量的中间态。
        """
        if kind not in TARGET_KINDS:
            raise DomainError(
                "validation_failed", f"未知的 target 类型：{kind}", fields={"kind": "target 类型非法"}
            )
        if kind == "channel":
            raise DomainError(
                "validation_failed",
                "channel 型 target 由频道创建时落影子行，不经此处",
                fields={"kind": "channel 型 target 复用 channel.id"},
            )
        new_target_id = target_id or new_id("tgt")
        with self._lock:
            self._conn.execute(
                "INSERT INTO target(id, kind, created_at) VALUES (?, ?, ?)",
                (new_target_id, kind, utc_now()),
            )
        return self.get_target(new_target_id)

    def unread_total(self, member_id: str = OWNER_ID) -> int:
        """工作台快照的总未读 = **channel 型 + dm 型**之和（决策 G5）；**task 型不进它**。

        它用的是与频道列表**同一条未读 WHERE 形状**（``UNREAD_WHERE_SQL`` 这个单一文本落点），
        因此「未读计数只有一个 SQL 形状」是结构上的事实：汇总只多一层 ``kind IN ('channel','dm')``
        过滤，不是第二套计数口径。
        """
        with self._lock:
            row = self._conn.execute(UNREAD_TOTAL_SQL, {"member_id": member_id}).fetchone()
        return int(row[0]) if row is not None else 0

    # ─── 频道（channel 型 target 的呈现面：name / description 仍留在 channel 表） ──
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

    def create_dm(self, agent_id: str, owner_id: str = OWNER_ID) -> tuple[dict[str, Any], bool]:
        """创建或复用 owner 与单一 Agent 的两方私信，返回 (DM, 是否新建)。"""
        clean_agent_id = (agent_id or "").strip()
        with self._lock:
            row = self._conn.execute("SELECT * FROM member WHERE id = ?", (clean_agent_id,)).fetchone()
        if row is None or row["deleted_at"] is not None or row["kind"] != "agent":
            raise DomainError(
                "validation_failed",
                "私信对象必须是存在且未删除的 Agent",
                fields={"agent_id": f"Agent 不存在或不可用：{clean_agent_id}"},
            )
        owner = self.get_member(owner_id)
        if owner["kind"] != "owner":
            raise DomainError("validation_failed", "私信创建者必须是 owner")

        target_id: str
        created = False
        with self._lock:
            self._begin()
            try:
                existing = self._conn.execute(
                    "SELECT id FROM target WHERE kind = 'dm' AND dm_peer_member_id = ?",
                    (clean_agent_id,),
                ).fetchone()
                if existing is not None:
                    target_id = str(existing["id"])
                else:
                    target_id = new_id("dm")
                    now = utc_now()
                    self._conn.execute(
                        "INSERT INTO target(id, kind, dm_peer_member_id, created_at) VALUES (?, 'dm', ?, ?)",
                        (target_id, clean_agent_id, now),
                    )
                    for member_id in (owner_id, clean_agent_id):
                        self._conn.execute(
                            "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)",
                            (target_id, member_id),
                        )
                        self._conn.execute(
                            "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0)",
                            (member_id, target_id),
                        )
                    created = True
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_dm(target_id, owner_id), created

    def list_dms(self, member_id: str = OWNER_ID) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(DM_SELECT_SQL, {"member_id": member_id, "dm_id": None}).fetchall()
        return [self._dm_payload(row) for row in rows]

    def get_dm(self, dm_id: str, member_id: str = OWNER_ID) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(DM_SELECT_SQL, {"member_id": member_id, "dm_id": dm_id}).fetchone()
        if row is None:
            raise NotFound(f"私信不存在：{dm_id}")
        return self._dm_payload(row)

    @staticmethod
    def _dm_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "created_at": row["created_at"],
            "agent_id": row["peer_id"],
            "peer": {
                "id": row["peer_id"],
                "kind": row["peer_kind"],
                "display_name": row["peer_display_name"],
                "avatar_slot": row["peer_avatar_slot"],
                "created_at": row["peer_created_at"],
                "deleted_at": row["peer_deleted_at"],
            },
            "last_seq": row["last_seq"],
            "last_read_seq": row["last_read_seq"],
            "unread_count": row["unread_count"],
        }

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

    def list_target_members(self, target_id: str) -> list[dict[str, Any]]:
        """该 target 的成员行（成员面/唤醒面的正本）。**不按 kind 分支**（三种 kind 同一张表、一个键）。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.* FROM target_member tm JOIN member m ON m.id = tm.member_id "
                "WHERE tm.target_id = ? ORDER BY CASE m.kind WHEN 'owner' THEN 0 ELSE 1 END, m.created_at",
                (target_id,),
            ).fetchall()
        return [self._member_payload(row) for row in rows]

    def list_target_agent_members(self, target_id: str) -> list[dict[str, Any]]:
        """该 target 的全部 Agent 成员（唤醒面的正本；调用方自行排除作者）。**不按 kind 分支**。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                AGENT_SELECT_SQL + "FROM target_member tm JOIN member m ON m.id = tm.member_id "
                "LEFT JOIN agent_config c ON c.member_id = m.id "
                "WHERE tm.target_id = ? AND m.kind = 'agent' AND m.deleted_at IS NULL "
                "ORDER BY m.created_at, m.id",
                (target_id,),
            ).fetchall()
        return [self._agent_payload(row) for row in rows]

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
                # channel 型的**影子行**：id 与 channel.id 同值、只承载 kind（ADR-0004 决策 1）。
                # 写在这里而不是别处，是因为它必须与频道行**同一个事务**：否则会出现
                # 「频道在、影子行不在」的中间态，而 message.target_id 的外键就指不到它了。
                self._conn.execute(
                    "INSERT INTO target(id, kind, created_at) VALUES (?, 'channel', ?)",
                    (channel_id, now),
                )
                for member_id in participants:
                    self._conn.execute(
                        "INSERT INTO target_member(target_id, member_id) VALUES (?, ?)",
                        (channel_id, member_id),
                    )
                    self._conn.execute(
                        "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0) "
                        "ON CONFLICT(member_id, target_id) DO NOTHING",
                        (member_id, channel_id),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_channel(channel_id, creator_id)

    # ─── 消息（作用域键是 **target_id**） ────────────────────────────────
    def list_messages(self, target_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self.get_target(target_id)  # 404 早退
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
                       a.avatar_slot AS author_avatar_slot
                  FROM message m JOIN member a ON a.id = m.author_member_id
                 WHERE m.target_id = ? AND m.seq > ?
                 ORDER BY m.seq
                """,
                (target_id, after_seq),
            ).fetchall()
        return [self._message_payload(row) for row in rows]

    def append_message(
        self,
        target_id: str,
        body: str,
        author_member_id: str = OWNER_ID,
        kind: str = "human",
        mention_snapshot: Iterable[Mapping[str, Any]] | None = None,
        task_ref: str | None = None,
    ) -> dict[str, Any]:
        """在同一事务内分配 **target 内**单调 seq 并落库。失败则不落任何行。"""
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
        self.get_target(target_id)  # 404 早退
        self.get_member(author_member_id)
        if task_ref is not None:
            try:
                referenced_task = self.get_task(task_ref)
            except NotFound as exc:
                raise DomainError(
                    "validation_failed", "task_ref 指向的任务不存在", fields={"task_ref": "任务不存在"}
                ) from exc
            if referenced_task["target_id"] != target_id:
                raise DomainError(
                    "validation_failed",
                    "task_ref 必须与消息位于同一 target",
                    fields={"task_ref": "不能引用其他 target 的任务"},
                )

        message_id = new_id("msg")
        now = utc_now()
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM message WHERE target_id = ?",
                    (target_id,),
                ).fetchone()
                seq = int(row["next_seq"])
                self._conn.execute(
                    "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
                    "mention_snapshot, task_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        message_id,
                        target_id,
                        seq,
                        author_member_id,
                        kind,
                        clean_body,
                        now,
                        json.dumps(list(mention_snapshot or []), ensure_ascii=False),
                        task_ref,
                    ),
                )
                if kind == "human":
                    # 人类消息落库**即**开启新世代并把回应预算重置为满额（spec #1 §5）。
                    # 写在这里而不是等到下一次扣款时惰性重置：「人类消息随后落库即重置」
                    # 是落库那一刻的事实，预算状态端点与压制行为必须同时看到它。
                    # 预算按 **target** 一份（ADR-0004 决策 4）：同一个写点服务三种 kind。
                    self._conn.execute(
                        "INSERT INTO target_budget(target_id, epoch_seq, used) VALUES (?, ?, 0) "
                        "ON CONFLICT(target_id) DO UPDATE SET epoch_seq = excluded.epoch_seq, used = 0",
                        (target_id, seq),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_message(target_id, seq)

    def get_message(self, target_id: str, seq: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
                       a.avatar_slot AS author_avatar_slot
                  FROM message m JOIN member a ON a.id = m.author_member_id
                 WHERE m.target_id = ? AND m.seq = ?
                """,
                (target_id, seq),
            ).fetchone()
        if row is None:
            raise NotFound(f"消息不存在：{target_id}#{seq}")
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
            "task_ref": row["task_ref"],
            "author": {
                "id": row["author_member_id"],
                "kind": row["author_kind"],
                "display_name": row["author_name"],
                "avatar_slot": row["author_avatar_slot"],
            },
        }

    # ─── 有界响应预算（策略 B 的正本，落库） ──────────────────────────────
    def budget_state(self, target_id: str, limit: int = BUDGET_LIMIT) -> dict[str, Any]:
        """该 target 当前人类世代的预算状态（只读）。没有行时 used=0、epoch_seq=0。

        ``epoch_seq=0`` 表示还没有开世代动作 —— 此时预算按满额生效：预算约束的是
        「一次人类发言能引发多少 agent 往返」，没有开世代动作时没有可越界的世代。

        返回载荷里的 ``channel_id`` 是 **HTTP 面既有字段名**（票面 Constraint：响应字段名一律不变），
        它承载的是这个 target 的 id——channel 型下就等于频道 id。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT epoch_seq, used FROM target_budget WHERE target_id = ?", (target_id,)
            ).fetchone()
        epoch_seq = int(row["epoch_seq"]) if row is not None else 0
        used = int(row["used"]) if row is not None else 0
        return {
            "channel_id": target_id,
            "epoch_seq": epoch_seq,
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0),
        }

    def human_epoch_seq(self, target_id: str) -> int:
        """该 target 最近一条**人类**消息的 seq（人类世代的定义，spec #1 §5）。没有则为 0。"""
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS epoch_seq FROM message "
                "WHERE target_id = ? AND kind = 'human'",
                (target_id,),
            ).fetchone()
        return int(row["epoch_seq"])

    def consume_budget(self, target_id: str, epoch_seq: int, amount: int = 1) -> int:
        """把预算推进到世代 ``epoch_seq`` 并消耗 ``amount``；返回消耗后的 ``used``。

        开新世代并重置用量发生在**人类消息落库那一刻**（``append_message``），本方法只负责
        累加已经发生的投递。这里仍按 seq 兜底：若调用顺序意外让旧世代的行盖住新世代，
        用量就从 0 起算，而不是把旧世代的额度算到新世代头上。

        上界（≤ ``BUDGET_LIMIT``）由调用方（``WakeOrchestrator._spend_jump``）在同一把锁内
        裁决后才调用本方法 —— 这里只记事实，不重复做上界判定。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT epoch_seq, used FROM target_budget WHERE target_id = ?", (target_id,)
            ).fetchone()
            previous = None if row is None else (int(row["epoch_seq"]), int(row["used"]))
            used = previous[1] if previous is not None and previous[0] >= int(epoch_seq) else 0
            used += max(int(amount), 0)
            self._conn.execute(
                "INSERT INTO target_budget(target_id, epoch_seq, used) VALUES (?, ?, ?) "
                "ON CONFLICT(target_id) DO UPDATE SET epoch_seq = excluded.epoch_seq, used = excluded.used",
                (target_id, int(epoch_seq), used),
            )
        return used

    # ─── target 成员（唯一写点：人类面「加入」与穿透加入共用） ──────────────
    def is_target_member(self, target_id: str, member_id: str) -> bool:
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM target_member WHERE target_id = ? AND member_id = ?",
                (target_id, member_id),
            ).fetchone()
        return row is not None

    def add_target_member(self, target_id: str, member_id: str) -> bool:
        """把成员加入该 target；返回是否**新增**（已在该 target 里则返回 False，幂等）。"""
        self.get_target(target_id)  # 404 早退
        self.get_member(member_id)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO target_member(target_id, member_id) VALUES (?, ?) "
                "ON CONFLICT(target_id, member_id) DO NOTHING",
                (target_id, member_id),
            )
            inserted = cursor.rowcount > 0
            if inserted:
                self._conn.execute(
                    "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, 0) "
                    "ON CONFLICT(member_id, target_id) DO NOTHING",
                    (member_id, target_id),
                )
        return inserted

    # ─── 已读位置（未读的正本） ───────────────────────────────────────────
    def latest_round_status(self, member_id: str) -> str | None:
        """该成员最近一条**关于它能否说话**的轮次结果（库里的正本；没有轮次时为 None）。

        状态点的「出错」必须由**落库的事实**推导，而不是内存里的一张失败表：
        否则后端重启后状态点会回到「在线」，而库里最近一轮仍是 failed —— 同一条事实两个说法。
        按 ``finished_at`` 与 ``id`` 同序列定序，与列表端点的顺序一致。

        ``merged`` 行被跳过：被合并是**它正在跑的那一轮**的调度事实（那条事实写在同一成员自己的
        行为轮次收口时落库的行上），不是「它刚刚跑完一轮」—— 把它当最近一条轮次结果会让
        状态点在 in-flight 期间被读成在线。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM round_outcome WHERE member_id = ? AND status != 'merged' "
                "ORDER BY finished_at DESC, id DESC LIMIT 1",
                (member_id,),
            ).fetchone()
        return row["status"] if row is not None else None

    def agent_cursor(self, target_id: str, member_id: str) -> int:
        """该成员在该 target 已经读到哪一条（消费游标）。

        复用 ``target_read``——该表语义就是「每成员每 target 的已读位置」，owner 的行是未读徽标的
        正本，Agent 的行只是它自己的消费位置，两者互不干扰。没有行时为 0（从头读）。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                "SELECT last_read_seq FROM target_read WHERE member_id = ? AND target_id = ?",
                (member_id, target_id),
            ).fetchone()
        return int(row["last_read_seq"]) if row is not None else 0

    def mark_read(self, target_id: str, seq: int, member_id: str = OWNER_ID) -> None:
        """把未读位置单调推进到 ``min(seq, 该 target 当前最大 seq)``（无返回）。

        上界裁剪保证「把未来消息提前标为已读」不可能发生；已读位置永不回退。
        推进的是 **target** 的已读位置（ADR-0004）。

        **返回 None**（早先返回 dict）：channel 面的响应体是**外部契约**（票面 Constraint：响应字段名
        一律不变），因此它由 channel 面入口 ``WorkspaceService.mark_read`` 用 ``get_channel`` 组出；
        ``db`` 层不把「channel 型的响应长什么样」写进自己的分支里（于是查询路径上没有 kind 分支）。
        """
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise DomainError("validation_failed", "seq 必须是非负整数", fields={"seq": "seq 必须是非负整数"})
        self.get_target(target_id)  # 404 早退
        self.get_member(member_id)
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM message WHERE target_id = ?",
                    (target_id,),
                ).fetchone()
                effective = min(seq, int(row["last_seq"]))
                self._conn.execute(
                    "INSERT INTO target_read(member_id, target_id, last_read_seq) VALUES (?, ?, ?) "
                    "ON CONFLICT(member_id, target_id) DO UPDATE SET "
                    "last_read_seq = MAX(target_read.last_read_seq, excluded.last_read_seq)",
                    (member_id, target_id, effective),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def unread_count(self, target_id: str, member_id: str = OWNER_ID) -> int:
        """该 target 的未读条数（**与频道列表同一条 WHERE 形状**：``UNREAD_COUNT_SQL``）。

        形状与 ``CHANNEL_SELECT_SQL`` 里的 ``unread_count`` 子查询、``unread_total`` 汇总
        完全同源（同一个 ``UNREAD_WHERE_SQL``），**不按 kind 分支**。
        """
        self.get_target(target_id)  # 404 早退
        with self._lock:
            row = self._conn.execute(
                UNREAD_COUNT_SQL, {"member_id": member_id, "target_id": target_id}
            ).fetchone()
        return int(row[0]) if row is not None else 0


def open_database(data_dir: Path | str | None = None) -> Database:
    return Database(data_dir if data_dir is not None else default_data_dir())
