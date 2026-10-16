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
from typing import Any, Literal

from .i18n import SOURCE_LOCALE, locale_of, render

OWNER_ID = "owner"
OWNER_DISPLAY_NAME = "Owner"
OWNER_AVATAR_SLOT = 4

# 头像色位族大小：与设计系统的 --av-0…--av-4 一一对应（色位按 slot 取模）。
# 前端 studio/app.js 的同名常量必须与本值一致。
AVATAR_SLOT_COUNT = 5

# schema 版本（`schema_meta.schema_version`）。闸门见 `_assert_schema_compatible`：不等就**拒绝启动**、
# 不做迁移（ADR-0004）。号段由规格 #60 的协调裁定分配，实施票不得自行改号：
#   7 = #61（工作区设置面）已合并；8 = #68（reminder 表）；9 = #67（附件）；
#   10 = **本票 #71**（`workspace_settings.theme` 新增一列）；11 = #35 的 #58（结构化沉默）。
SCHEMA_VERSION = 10

# 工作区设置（`CONTEXT.md` **工作区设置**）：本机单用户工作台的**配置类**取值集合——
# **语言偏好** / **消息密度** / **主题**三档。列集就是「只有这些档位」这条约束的落点：
# 没有通用 key-value 面，因此这个设置面不可能被当成通用存储。
LOCALES = ("zh-CN", "en")
MESSAGE_DENSITIES = ("comfortable", "compact")
# 主题三档（#71）：显式浅色 / 显式深色 / 跟随系统外观。
# `system` 是**缺省**档（空库的取值就是它）：由浏览器的 `prefers-color-scheme` 裁决；
# 显式两档把标记写在 `<html data-theme>` 上。三档的**取值层**在 `alibot-design-system/`
# （同一 token 名集合的第二组取值，`decisions/ADR-0017-dark-value-layer.md`）。
THEMES = ("light", "dark", "system")

# 缺省语言 = 词条表的**源语言**（同一件事不写第二遍：改源语言只改 `i18n.SOURCE_LOCALE`）。
DEFAULT_LOCALE = SOURCE_LOCALE
DEFAULT_MESSAGE_DENSITY = "comfortable"
DEFAULT_THEME = "system"

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

# 轮次结果的五种状态（spec #1 §5）。status 是「自判忽略」与「处理失败」的区分来源。
ROUND_STATUSES = ("replied", "ignored", "failed", "merged", "budget-exhausted")

# 有界响应预算（策略 B，spec #1 §5）：一个**人类世代内同一 target** 允许的 agent→agent 跳数上界。
# 它是**常量**：本票不做配置化（那是 #31 的范围），只把「每频道一份」泛化为「每 target 一份」。
BUDGET_LIMIT = 3

# 工作台搜索的硬上限：前端可以请求更小的窗口，但不能把整张消息表一次搬到浏览器。
SEARCH_RESULT_LIMIT = 200

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
    "workspace_settings",
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

-- 工作区设置（`CONTEXT.md` **工作区设置**）：**单行表**（``id = 1``），列就是明确列出的档位
-- （语言偏好 / 消息密度 / 主题三档）。它是**配置类**取值（人类面经 ``GET`` / ``PATCH /api/settings``
-- 读写），与预算表的边界是**写什么**：人类面可以写配置，但**不得**写用量类事实
-- （``target_budget.used`` 仍只由后端推进，ADR-0007）。
-- 刻意不做成通用 key-value 表：那会让「只有这几档」这条约束失去落点（词条的 `_Avoid_` 明写）。
-- 改设置**不落消息、不唤醒、不消耗预算**——它不是对话事实。
CREATE TABLE IF NOT EXISTS workspace_settings (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  locale          TEXT NOT NULL DEFAULT 'zh-CN' CHECK (locale IN ('zh-CN', 'en')),
  message_density TEXT NOT NULL DEFAULT 'comfortable'
                  CHECK (message_density IN ('comfortable', 'compact')),
  -- 主题三档（#71）：显式浅色 / 显式深色 / 跟随系统外观。缺省是 ``system``
  -- （空库的取值就是「跟随系统」）；取值层在 alibot-design-system/。
  theme           TEXT NOT NULL DEFAULT 'system'
                  CHECK (theme IN ('light', 'dark', 'system')),
  updated_at      TEXT NOT NULL
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

# Agent 概览读数的三条聚合语句（同样**在模块级拼好**，同上一条体例）。
# 每条的成员过滤是**同一个形状** ``(:member_id IS NULL OR …)``：``agent_stats()`` 的「全部成员」
# 与「某一个成员」两条路径共用一条语句，因此不存在第二份只差一个 ``WHERE`` 的拷贝；
# 参数一律走命名占位符（传 ``{"member_id": …}``），没有标识符注入面。
AGENT_STATS_CHANNEL_SQL = (
    "SELECT tm.member_id AS member_id, COUNT(*) AS n FROM target_member tm "
    "JOIN target t ON t.id = tm.target_id "
    "WHERE t.kind = 'channel' AND (:member_id IS NULL OR tm.member_id = :member_id) "
    "GROUP BY tm.member_id"
)
AGENT_STATS_ROUND_SQL = (
    "SELECT member_id, COUNT(*) AS n, "
    "SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END) AS replied, "
    "SUM(CASE WHEN status IN ('replied', 'ignored', 'failed') THEN 1 ELSE 0 END) AS verdicts "
    "FROM round_outcome WHERE (:member_id IS NULL OR member_id = :member_id) "
    "GROUP BY member_id"
)
AGENT_STATS_TASK_SQL = (
    "SELECT assignee_member_id AS member_id, COUNT(*) AS n FROM task "
    "WHERE status = 'done' AND assignee_member_id IS NOT NULL "
    "AND (:member_id IS NULL OR assignee_member_id = :member_id) "
    "GROUP BY assignee_member_id"
)

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

# 工作台搜索的唯一判定：SQLite CASEFOLD + LIKE 子串匹配，前端只消费后端高亮区间。
#
# ``LIKE`` 的模式由 ``_search_like_pattern`` 绑定为 ``%关键词%``，并转义用户输入里的通配符，
# 所以它仍是严格的子串匹配。全工作区版本由 target 驱动，经 message 上既有的
# ``UNIQUE(target_id, seq)`` 索引读取各 target 消息；不新增 schema / FTS5。
#
# 命中来源有**三类**（ADR-0013）：消息（正文）、任务（标题）、Agent（显示名与角色）。
# ``facet`` 是**后端的过滤参数**（不是前端过滤）：选中哪一类就只查哪一条 SQL，
# 三类共用上面这一条判定纪律（大小写不敏感的子串匹配），因此不存在第二个判定落点。
SEARCH_HIT_SELECT_SQL = """
    SELECT m.*, a.kind AS author_kind, a.display_name AS author_name,
           a.avatar_slot AS author_avatar_slot, a.deleted_at AS author_deleted_at,
           t.kind AS target_kind,
           CASE t.kind
             WHEN 'channel' THEN c.name
             WHEN 'dm' THEN peer.display_name
             WHEN 'task' THEN task.title
           END AS target_display_name
"""
SEARCH_HIT_FROM_SQL = """
      FROM target t
      CROSS JOIN message m
      JOIN member a ON a.id = m.author_member_id
 LEFT JOIN channel c ON c.id = t.id AND t.kind = 'channel'
 LEFT JOIN member peer ON peer.id = t.dm_peer_member_id AND t.kind = 'dm'
 LEFT JOIN task ON task.target_id = t.id AND t.kind = 'task'
"""
SEARCH_ALL_HITS_SQL = (
    SEARCH_HIT_SELECT_SQL
    + SEARCH_HIT_FROM_SQL
    + """
     WHERE m.target_id = t.id
       AND m.kind != 'system'
       AND CASEFOLD(m.body) LIKE :pattern ESCAPE '\\'
     LIMIT :limit
"""
)
SEARCH_TARGET_HITS_SQL = (
    SEARCH_HIT_SELECT_SQL
    + SEARCH_HIT_FROM_SQL
    + """
     WHERE m.target_id = :target_id
       AND m.target_id = t.id
       AND m.kind != 'system'
       AND CASEFOLD(m.body) LIKE :pattern ESCAPE '\\'
     LIMIT :limit
"""
)

# 任务命中：命中本体是**任务标题**；``channel`` 是它的来源（``.r-top`` 里显示的那个「#频道」）。
SEARCH_TASK_HITS_SQL = """
    SELECT t.id AS id, t.target_id AS target_id, t.title AS title, t.status AS status,
           c.id AS channel_id, c.name AS channel_name
      FROM task t
      JOIN channel c ON c.id = t.channel_id
     WHERE CASEFOLD(t.title) LIKE :pattern ESCAPE '\\'
     ORDER BY t.created_at, t.id
     LIMIT :limit
"""

# Agent 命中：命中本体是**显示名与角色**（两个字段各自是子串匹配的候选）。
# soft-delete 的成员不是可呈现的 Agent，因此排除；没有 ``agent_config`` 行的成员仍可被显示名命中，
# 因此是 LEFT JOIN（与 ``agent_members()`` 同一口径，两处不能分叉）。
SEARCH_AGENT_HITS_SQL = """
    SELECT m.id AS id, m.display_name AS display_name, m.avatar_slot AS avatar_slot,
           COALESCE(c.role, '') AS role, COALESCE(c.model_id, '') AS model_id
      FROM member m
 LEFT JOIN agent_config c ON c.member_id = m.id
     WHERE m.kind = 'agent'
       AND m.deleted_at IS NULL
       AND (CASEFOLD(m.display_name) LIKE :pattern ESCAPE '\\'
            OR CASEFOLD(COALESCE(c.role, '')) LIKE :pattern ESCAPE '\\')
     ORDER BY m.display_name, m.id
     LIMIT :limit
"""

# 搜索的四档 facet（原型 `.facet-row` 的取值与顺序）。它同时是 HTTP 查询参数的类型，
# 因此「哪几档」只有这一处定义：FastAPI 的校验与库层的分支读的是同一个 Literal。
SearchFacet = Literal["all", "message", "task", "agent"]


def _sqlite_casefold(value: str | None) -> str | None:
    """为 SQLite ``LIKE`` 提供与 Python 正文区间判定一致的 Unicode 大小写折叠。"""
    return value.casefold() if value is not None else None


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
    """404 一族：``message`` 必须显式给出（调用点按**语言偏好**渲染，见 ``Database._copy``）。

    这里**故意没有缺省文案**：缺省值要么是钉死源语言的一句话（切到 English 后就露出中文），要么
    是一条不可达的死词条。删掉缺省，让「404 的正文来自词条表」成为签名级约束——漏传立即报错，
    而不是悄悄说错语言。
    """

    def __init__(self, message: str):
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
        self._conn.create_function("CASEFOLD", 1, _sqlite_casefold, deterministic=True)
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
            # 工作区设置的**单行**：缺省值就是源语言、默认密度与「跟随系统」主题，与「空库 = 空工作台」
            # 同一条纪律（空库里没有任何用户取值，但设置面必须可读）。
            self._conn.execute(
                "INSERT INTO workspace_settings(id, locale, message_density, theme, updated_at) "
                "VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (DEFAULT_LOCALE, DEFAULT_MESSAGE_DENSITY, DEFAULT_THEME, utc_now()),
            )

    # ─── 事务助手 ──────────────────────────────────────────────────────────
    def _begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._conn.execute("COMMIT")

    def _rollback(self) -> None:
        with suppress(sqlite3.OperationalError):  # 事务已经结束
            self._conn.execute("ROLLBACK")

    # ─── 可见文案（ADR-0009 第二层：按**库内**语言偏好渲染） ───────────────
    def _render_copy(self, key: str, /, **params: Any) -> str:
        """本层产出的**用户可见**自然语言一律经它取值：HTTP 错误的 ``message`` / ``fields`` 的
        自然语言，以及落库的 ``system`` 消息正文。

        取值方式选的是「本层自读偏好」而不是「由调用方注入」（票面授权里两种都算合规）：

        - 语言偏好的正本就是本层的 ``workspace_settings`` 那一行，自读与编排路径（无请求头）
          读的是**同一个来源**，因此两条路径不可能拿到不同语言；
        - 落库正文必须在**写入那一刻**定型（append-only，历史不回填），把语言判断留在写路径内部
          才不会被某个调用点漏掉；
        - 代价是一次单行主键读（``locale_of`` → ``workspace_settings``），且**不做缓存**：
          缓存会让「刚切换语言」的读数滞后，那正是这条机制要保证的。
        """
        return render(key, locale_of(self), **params)

    # ─── 成员 ─────────────────────────────────────────────────────────────
    def list_members(self, kind: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        if kind not in (None, "owner", "agent"):
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_member_kind", kind=kind),
                fields={"kind": self._render_copy("error.field.member_kind_invalid")},
            )
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
            raise NotFound(self._render_copy("error.member_not_found", member_id=member_id))
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
            raise NotFound(self._render_copy("error.agent_not_found", member_id=member_id))
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
            fields["name"] = self._render_copy("error.field.agent_name_empty")
        elif len(clean_name) > MAX_MEMBER_NAME_LENGTH:
            fields["name"] = self._render_copy(
                "error.field.agent_name_too_long", limit=MAX_MEMBER_NAME_LENGTH
            )
        if not clean_model:
            fields["model_id"] = self._render_copy("error.model_id_empty")
        elif len(clean_model) > MAX_MODEL_ID_LENGTH:
            fields["model_id"] = self._render_copy("error.field.model_id_too_long", limit=MAX_MODEL_ID_LENGTH)
        if len(clean_role) > MAX_AGENT_ROLE_LENGTH:
            fields["role"] = self._render_copy("error.field.role_too_long", limit=MAX_AGENT_ROLE_LENGTH)
        if len(clean_prompt) > MAX_SYSTEM_PROMPT_LENGTH:
            fields["system_prompt"] = self._render_copy(
                "error.field.system_prompt_too_long", limit=MAX_SYSTEM_PROMPT_LENGTH
            )
        if fields:
            raise DomainError("validation_failed", self._render_copy("error.agent_invalid"), fields=fields)

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

    def update_agent(
        self,
        member_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        model_id: str | None = None,
        system_prompt: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """部分更新 Agent 配置（``None`` = 这次请求没提到这一字段，保持原值）。

        校验先于写入：**任一字段不合法则整体不改**（与 ``create_agent`` /
        ``update_workspace_settings`` 同一偏序——没有「一半新一半旧」的中间态）。``model_id`` 的
        **形状**校验（provider 白名单）由服务层在进到本层之前完成；本层只判与持久化同构的判据
        （空值 / 长度）。

        它只写 ``member.display_name`` 与 ``agent_config`` 那一行（后者由 ``create_agent`` 在同一
        事务里创建，因此本写路径没有「缺行」的中间态）：**不落消息、不唤醒、不消耗任何预算、
        也不开世代**——配置变更不是一次对话动作（ADR-0011）。
        """
        self.get_agent(member_id)  # 存在性 + 「必须是 Agent」：不是就抛 NotFound
        clean = {
            "display_name": None if display_name is None else (display_name or "").strip(),
            "role": None if role is None else (role or "").strip(),
            "model_id": None if model_id is None else (model_id or "").strip(),
            "system_prompt": None if system_prompt is None else (system_prompt or "").strip(),
        }

        fields: dict[str, str] = {}
        if clean["display_name"] is not None:
            if not clean["display_name"]:
                fields["display_name"] = self._render_copy("error.field.agent_name_empty")
            elif len(clean["display_name"]) > MAX_MEMBER_NAME_LENGTH:
                fields["display_name"] = self._render_copy(
                    "error.field.agent_name_too_long", limit=MAX_MEMBER_NAME_LENGTH
                )
        if clean["model_id"] is not None:
            if not clean["model_id"]:
                fields["model_id"] = self._render_copy("error.model_id_empty")
            elif len(clean["model_id"]) > MAX_MODEL_ID_LENGTH:
                fields["model_id"] = self._render_copy(
                    "error.field.model_id_too_long", limit=MAX_MODEL_ID_LENGTH
                )
        if clean["role"] is not None and len(clean["role"]) > MAX_AGENT_ROLE_LENGTH:
            fields["role"] = self._render_copy("error.field.role_too_long", limit=MAX_AGENT_ROLE_LENGTH)
        if clean["system_prompt"] is not None and len(clean["system_prompt"]) > MAX_SYSTEM_PROMPT_LENGTH:
            fields["system_prompt"] = self._render_copy(
                "error.field.system_prompt_too_long", limit=MAX_SYSTEM_PROMPT_LENGTH
            )
        if fields:
            raise DomainError(
                "validation_failed", self._render_copy("error.agent_update_invalid"), fields=fields
            )

        with self._lock:
            self._begin()
            try:
                if clean["display_name"] is not None:
                    self._conn.execute(
                        "UPDATE member SET display_name = ? WHERE id = ?",
                        (clean["display_name"], member_id),
                    )
                # `COALESCE` 就是「没提到的字段保持原值」这条部分更新语义的落点：它让「只写该写的」
                # 与「绝不部分写入」在**同一条语句**里成立，不靠 Python 侧拼 SQL。
                self._conn.execute(
                    "UPDATE agent_config SET "
                    "role = COALESCE(?, role), "
                    "model_id = COALESCE(?, model_id), "
                    "system_prompt = COALESCE(?, system_prompt), "
                    "enabled = COALESCE(?, enabled), "
                    "updated_at = ? WHERE member_id = ?",
                    (
                        clean["role"],
                        clean["model_id"],
                        clean["system_prompt"],
                        None if enabled is None else int(bool(enabled)),
                        utc_now(),
                        member_id,
                    ),
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        # 读回落库后的那一份（不是把入参回显出去）：调用方看到的是**持久化事实**。
        return self.get_agent(member_id)

    def agent_stats(self, member_id: str | None = None) -> dict[str, dict[str, Any]]:
        """按成员聚合的**只读既有事实**（Agent 概览页每个数字的唯一来源）——不新增数据面。

        - ``channel_count``：它是成员的 **channel 型 target** 数（``target_member`` 与 ``target.kind``）；
        - ``round_count``：它的 ``round_outcome`` 行数（参与过的轮次），append-only 事实；
        - ``completed_task_count``：指派给它且 ``status='done'`` 的 ``task`` 行数；
        - ``replied_count`` / ``verdict_count``：成功率的两项（``replied`` ÷ 有结论的轮次
          ``replied|ignored|failed``）。``merged``（不是它的决定）与 ``budget-exhausted``
          （根本没跑起来）**不计入**分母。

        三处读的都是**行级事实**，不读内存、不做推断：后端重启后读数逐字不变。
        ``member_id`` 为 ``None`` 时一次聚合全部成员（列表面用它，避免 N+1 查询）——两条路径共用
        同一条语句（过滤是 ``AGENT_STATS_*_SQL`` 里的 ``:member_id IS NULL OR …``），因此不存在
        第二份只差一个 ``WHERE`` 的拷贝。
        """
        args = {"member_id": member_id}
        stats: dict[str, dict[str, Any]] = {}
        with self._lock:
            for row in self._conn.execute(AGENT_STATS_CHANNEL_SQL, args).fetchall():
                stats.setdefault(row["member_id"], {})["channel_count"] = int(row["n"])
            for row in self._conn.execute(AGENT_STATS_ROUND_SQL, args).fetchall():
                entry = stats.setdefault(row["member_id"], {})
                entry["round_count"] = int(row["n"])
                entry["replied_count"] = int(row["replied"] or 0)
                entry["verdict_count"] = int(row["verdicts"] or 0)
            for row in self._conn.execute(AGENT_STATS_TASK_SQL, args).fetchall():
                stats.setdefault(row["member_id"], {})["completed_task_count"] = int(row["n"])
        return stats

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
                "validation_failed",
                self._render_copy("error.field.provider_empty"),
                fields={"provider": self._render_copy("error.field.provider_empty")},
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

    # ─── 工作区设置（配置类取值：人类面可写，用量类事实不可写） ─────────────
    def workspace_settings(self) -> dict[str, Any]:
        """当前工作区设置（**只读**）。行的缺省值在空库初始化时就已写入。

        ``open_without_initializing`` 打开的库可能是「单行不存在」的形态（诊断路径不做 schema
        变更），因此这里按缺省值回退，而不是在只读路径上补一行——读一个库不该改它。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT locale, message_density, theme, updated_at FROM workspace_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            return {
                "locale": DEFAULT_LOCALE,
                "message_density": DEFAULT_MESSAGE_DENSITY,
                "theme": DEFAULT_THEME,
                "updated_at": "",
            }
        return {
            "locale": row["locale"],
            "message_density": row["message_density"],
            "theme": row["theme"],
            "updated_at": row["updated_at"],
        }

    def update_workspace_settings(
        self,
        locale: str | None = None,
        message_density: str | None = None,
        theme: str | None = None,
    ) -> dict[str, Any]:
        """部分更新工作区设置（``None`` = 这一档没被这次请求提到，保持原值）。

        校验先于写入：任一档位非法则**整体不改**（与 `add_channel_member` / `create_agent`
        的偏序一致——没有「一半新一半旧」的中间态）。它只写 ``workspace_settings`` 一行：
        **不落消息、不唤醒、不消耗预算**——设置不是对话事实。
        """
        fields: dict[str, str] = {}
        if locale is not None and locale not in LOCALES:
            fields["locale"] = self._render_copy("error.field_choices", values=", ".join(LOCALES))
        if message_density is not None and message_density not in MESSAGE_DENSITIES:
            fields["message_density"] = self._render_copy(
                "error.field_choices", values=", ".join(MESSAGE_DENSITIES)
            )
        if theme is not None and theme not in THEMES:
            fields["theme"] = self._render_copy("error.field_choices", values=", ".join(THEMES))
        if fields:
            raise DomainError("validation_failed", self._render_copy("error.settings_invalid"), fields=fields)
        with self._lock:
            current = self.workspace_settings()
            self._conn.execute(
                "INSERT INTO workspace_settings(id, locale, message_density, theme, updated_at) "
                "VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET locale = excluded.locale, "
                "message_density = excluded.message_density, theme = excluded.theme, "
                "updated_at = excluded.updated_at",
                (
                    current["locale"] if locale is None else locale,
                    current["message_density"] if message_density is None else message_density,
                    current["theme"] if theme is None else theme,
                    utc_now(),
                ),
            )
        return self.workspace_settings()

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
                "validation_failed",
                self._render_copy("error.unknown_round_status", status=status),
                fields={"status": self._render_copy("error.field.round_status_invalid")},
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
            raise NotFound(self._render_copy("error.target_not_found", target_id=target_id))
        return {"id": row["id"], "kind": row["kind"], "created_at": row["created_at"]}

    # ─── 任务 ─────────────────────────────────────────────────────────────
    def create_task(self, channel_id: str, title: str) -> dict[str, Any]:
        clean_title = (title or "").strip()
        if not clean_title:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.task_title_empty"),
                fields={"title": self._render_copy("error.task_title_empty")},
            )
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
                    "mention_snapshot) VALUES (?, ?, 1, ?, 'system', ?, ?, '[]')",
                    (new_id("msg"), target_id, OWNER_ID, self._render_copy("system.task.created"), now),
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
            raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
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

    def task_for_target(self, target_id: str) -> dict[str, Any] | None:
        """取 task 型 target 对应的任务；channel / DM 型 target 返回 None。"""
        target = self.get_target(target_id)
        if target["kind"] != "task":
            return None
        with self._lock:
            row = self._conn.execute("SELECT id FROM task WHERE target_id = ?", (target_id,)).fetchone()
        if row is None:
            raise NotFound(self._render_copy("error.task_missing_for_target", target_id=target_id))
        return self.get_task(str(row["id"]))

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

    def update_task_status(
        self, task_id: str, status: str, *, actor_member_id: str = OWNER_ID
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if status not in TASK_STATUSES:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_task_status", status=status),
                fields={"status": self._render_copy("error.field.task_status_invalid")},
            )
        actor = self.get_member(actor_member_id)
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, status, assignee_member_id FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
                current = str(row["status"])
                target_id = str(row["target_id"])
                if status == current:
                    self._commit()
                    return self.get_task(task_id), None
                if status not in TASK_STATUS_TRANSITIONS[current]:
                    raise DomainError(
                        "validation_failed",
                        self._render_copy("error.task_transition_invalid", current=current, target=status),
                        fields={
                            "status": self._render_copy(
                                "error.field.task_transition_invalid", current=current, target=status
                            )
                        },
                    )
                if (
                    current == "in_review"
                    and status == "done"
                    and actor["kind"] == "agent"
                    and row["assignee_member_id"] == actor_member_id
                ):
                    raise DomainError(
                        "validation_failed",
                        self._render_copy("error.task_self_approve"),
                        fields={"status": self._render_copy("error.field.task_self_approve")},
                    )
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id)
                )
                seq = self._append_task_system_message_locked(
                    target_id,
                    self._render_copy(
                        "system.task.status", status=self._render_copy(f"status.task.{status}")
                    ),
                    now,
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def update_task_title(self, task_id: str, title: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        clean_title = (title or "").strip()
        if not clean_title:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.task_title_empty"),
                fields={"title": self._render_copy("error.task_title_empty")},
            )
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, title FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
                target_id = str(row["target_id"])
                if clean_title == row["title"]:
                    self._commit()
                    return self.get_task(task_id), None
                now = utc_now()
                self._conn.execute(
                    "UPDATE task SET title = ?, updated_at = ? WHERE id = ?", (clean_title, now, task_id)
                )
                seq = self._append_task_system_message_locked(
                    target_id, self._render_copy("system.task.title", title=clean_title), now
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get_task(task_id), self.get_message(target_id, seq)

    def update_task_assignee(
        self, task_id: str, member_id: str | None, *, actor_member_id: str = OWNER_ID
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        actor = self.get_member(actor_member_id)
        assignee: dict[str, Any] | None = None
        if member_id is not None:
            try:
                assignee = self.get_member(member_id)
            except NotFound as exc:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.assignee_missing"),
                    fields={"member_id": self._render_copy("error.field.assignee_missing")},
                ) from exc
            if assignee["deleted_at"] is not None or assignee["kind"] != "agent":
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.assignee_not_agent"),
                    fields={"member_id": self._render_copy("error.field.assignee_not_agent")},
                )
        self.get_task(task_id)  # 404 早退
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    "SELECT target_id, assignee_member_id FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(self._render_copy("error.task_not_found", task_id=task_id))
                target_id = str(row["target_id"])
                previous_id = row["assignee_member_id"]
                if actor["kind"] == "agent":
                    if actor["deleted_at"] is not None or previous_id != actor_member_id:
                        raise DomainError(
                            "validation_failed", self._render_copy("error.transfer_not_assignee")
                        )
                    if member_id is None:
                        raise DomainError(
                            "validation_failed", self._render_copy("error.transfer_needs_target")
                        )
                elif actor["kind"] != "owner":
                    raise DomainError("validation_failed", self._render_copy("error.reassign_not_allowed"))
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
                label = (
                    self._render_copy("system.task.assignee", name=assignee["display_name"])
                    if assignee is not None
                    else self._render_copy("system.task.unassigned")
                )
                seq = self._append_task_system_message_locked(target_id, label, now)
                if member_id is not None and actor["kind"] == "owner":
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
                "validation_failed",
                self._render_copy("error.unknown_target_kind", kind=kind),
                fields={"kind": self._render_copy("error.field.target_kind_invalid")},
            )
        if kind == "channel":
            raise DomainError(
                "validation_failed",
                self._render_copy("error.channel_target_explicit"),
                fields={"kind": self._render_copy("error.field.channel_target_explicit")},
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
            raise NotFound(self._render_copy("error.channel_not_found", channel_id=channel_id))
        return self._channel_payload(row)

    def create_dm(self, agent_id: str, owner_id: str = OWNER_ID) -> tuple[dict[str, Any], bool]:
        """创建或复用 owner 与单一 Agent 的两方私信，返回 (DM, 是否新建)。"""
        clean_agent_id = (agent_id or "").strip()
        with self._lock:
            row = self._conn.execute("SELECT * FROM member WHERE id = ?", (clean_agent_id,)).fetchone()
        if row is None or row["deleted_at"] is not None or row["kind"] != "agent":
            raise DomainError(
                "validation_failed",
                self._render_copy("error.dm_peer_invalid"),
                fields={
                    "agent_id": self._render_copy("error.field.dm_peer_invalid", member_id=clean_agent_id)
                },
            )
        owner = self.get_member(owner_id)
        if owner["kind"] != "owner":
            raise DomainError("validation_failed", self._render_copy("error.dm_creator_not_owner"))

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
            raise NotFound(self._render_copy("error.dm_not_found", dm_id=dm_id))
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
            fields["name"] = self._render_copy("error.field.channel_name_empty")
        elif len(clean_name) > MAX_CHANNEL_NAME_LENGTH:
            fields["name"] = self._render_copy(
                "error.field.channel_name_too_long", limit=MAX_CHANNEL_NAME_LENGTH
            )
        if len(clean_description) > MAX_CHANNEL_DESCRIPTION_LENGTH:
            fields["description"] = self._render_copy(
                "error.field.channel_description_too_long", limit=MAX_CHANNEL_DESCRIPTION_LENGTH
            )
        if fields:
            raise DomainError("validation_failed", self._render_copy("error.channel_invalid"), fields=fields)

        self.get_member(creator_id)
        selected: list[dict[str, Any]] = []
        for member_id in requested:
            with self._lock:
                row = self._conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.channel_invalid"),
                    fields={"member_ids": self._render_copy("error.member_unavailable", member_id=member_id)},
                )
            if row["kind"] != "agent":
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.channel_invalid"),
                    fields={"member_ids": self._render_copy("error.member_not_agent", member_id=member_id)},
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

    def search(
        self,
        query: str,
        *,
        facet: SearchFacet = "all",
        target_id: str | None = None,
        limit: int = SEARCH_RESULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """搜索三类命中；``facet`` 决定查哪几类，``target_id`` 收窄到单个 target 的消息面。

        本仓是单用户工作台，target_member 表表达唤醒与未读的成员面，不是可见性 ACL；所以这里
        **不**附加 target_member 过滤。target 仅在调用方显式限定时校验存在，避免把错误 target
        伪装成零命中。

        给定 ``target_id`` 时**只有消息命中适用**（沿用既有语义：只在该作用域内搜消息）——
        任务命中挂它所属的频道、Agent 命中不挂任何 target，两者都不属于「这一个 target」。
        ``limit`` 是**每类**命中的条数上限（``facet='all'`` 时三类各自最多 ``limit`` 条）。

        每条命中的形状统一（ADR-0013）：``kind`` / ``text`` / ``highlight_ranges`` / ``target``
        （所属 target，Agent 命中为 ``null``），外加该类的对象标识（``message`` / ``task`` / ``agent``）。
        区间缺失时（空表）前端整段呈现，**不得**自行在文本里找位置。
        """
        if target_id is not None:
            self.get_target(target_id)
        params = {
            "pattern": self._search_like_pattern(query),
            "target_id": target_id,
            "limit": limit,
        }
        hits: list[dict[str, Any]] = []
        with self._lock:
            if facet in ("all", "message"):
                sql = SEARCH_TARGET_HITS_SQL if target_id is not None else SEARCH_ALL_HITS_SQL
                hits.extend(self._message_hit_payload(row, query) for row in self._conn.execute(sql, params))
            if target_id is not None:
                return hits
            if facet in ("all", "task"):
                hits.extend(
                    self._task_hit_payload(row, query)
                    for row in self._conn.execute(SEARCH_TASK_HITS_SQL, params)
                )
            if facet in ("all", "agent"):
                hits.extend(
                    self._agent_hit_payload(row, query)
                    for row in self._conn.execute(SEARCH_AGENT_HITS_SQL, params)
                )
        return hits

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
            raise DomainError(
                "validation_failed",
                self._render_copy("error.unknown_message_kind", kind=kind),
                fields={"kind": self._render_copy("error.field.message_kind_invalid")},
            )
        clean_body = (body or "").strip()
        if not clean_body:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.message_body_empty"),
                fields={"body": self._render_copy("error.message_body_empty")},
            )
        if len(clean_body) > MAX_MESSAGE_BODY_LENGTH:
            raise DomainError(
                "validation_failed",
                self._render_copy("error.message_body_too_long", limit=MAX_MESSAGE_BODY_LENGTH),
                fields={
                    "body": self._render_copy("error.message_body_too_long", limit=MAX_MESSAGE_BODY_LENGTH)
                },
            )
        self.get_target(target_id)  # 404 早退
        self.get_member(author_member_id)
        if task_ref is not None:
            try:
                referenced_task = self.get_task(task_ref)
            except NotFound as exc:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.task_ref_missing"),
                    fields={"task_ref": self._render_copy("error.field.task_ref_missing")},
                ) from exc
            if referenced_task["target_id"] != target_id:
                raise DomainError(
                    "validation_failed",
                    self._render_copy("error.task_ref_other_target"),
                    fields={"task_ref": self._render_copy("error.field.task_ref_other_target")},
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
            raise NotFound(self._render_copy("error.message_not_found", target_id=target_id, seq=seq))
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

    @staticmethod
    def _search_like_pattern(query: str) -> str:
        """把用户文本变为 SQLite ``LIKE '%kw%' ESCAPE '\\'`` 的字面子串模式。"""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "%" + escaped.casefold() + "%"

    @staticmethod
    def _search_highlight_ranges(body: str, query: str) -> list[dict[str, int]]:
        """给出全部不重叠命中的 UTF-16 区间，供浏览器直接 ``slice`` 呈现。"""
        folded_body: list[str] = []
        original_starts: list[int] = []
        original_ends: list[int] = []
        for index, character in enumerate(body):
            folded_character = character.casefold()
            folded_body.append(folded_character)
            original_starts.extend([index] * len(folded_character))
            original_ends.extend([index + 1] * len(folded_character))

        folded_text = "".join(folded_body)
        folded_query = query.casefold()
        if not folded_query:
            return []
        ranges: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        previous_original_end = 0
        cursor = 0
        while True:
            folded_start = folded_text.find(folded_query, cursor)
            if folded_start < 0:
                break
            folded_end = folded_start + len(folded_query)
            start = original_starts[folded_start]
            end = original_ends[folded_end - 1]
            original_range = (start, end)
            # JavaScript 的 String.slice 按 UTF-16 code unit 计数；转换留在唯一判定落点，前端无需重算。
            if original_range not in seen and start >= previous_original_end:
                ranges.append(
                    {
                        "start": len(body[:start].encode("utf-16-le")) // 2,
                        "end": len(body[:end].encode("utf-16-le")) // 2,
                    }
                )
                seen.add(original_range)
                previous_original_end = end
                cursor = folded_end
            else:
                # casefold 展开可能让 folded 匹配映回重叠原文；从下一个 folded 起点继续，
                # 不能直接跳过候选尾部，否则会漏掉紧随其后的非重叠原文命中。
                cursor = folded_start + 1
        return ranges

    @classmethod
    def _message_hit_payload(cls, row: sqlite3.Row, query: str) -> dict[str, Any]:
        """消息命中：可呈现文本是消息正文，区间由后端给出（``kind='system'`` 已被 SQL 排除）。"""
        message = cls._message_payload(row)
        message["author"]["deleted"] = row["author_deleted_at"] is not None
        return {
            "kind": "message",
            "text": message["body"],
            "highlight_ranges": cls._search_highlight_ranges(message["body"], query),
            "target": {
                "id": row["target_id"],
                "kind": row["target_kind"],
                "display_name": row["target_display_name"],
            },
            "message": message,
        }

    @classmethod
    def _task_hit_payload(cls, row: sqlite3.Row, query: str) -> dict[str, Any]:
        """任务命中：可呈现文本是**任务标题**，``target`` 是它所属的频道（点击落到任务线程）。"""
        title = row["title"]
        return {
            "kind": "task",
            "text": title,
            "highlight_ranges": cls._search_highlight_ranges(title, query),
            "target": {
                "id": row["channel_id"],
                "kind": "channel",
                "display_name": row["channel_name"],
            },
            "task": {
                "id": row["id"],
                "target_id": row["target_id"],
                "channel_id": row["channel_id"],
                "channel_name": row["channel_name"],
                "title": title,
                "status": row["status"],
            },
        }

    @classmethod
    def _agent_hit_payload(cls, row: sqlite3.Row, query: str) -> dict[str, Any]:
        """Agent 命中：命中本体是显示名与角色，因此可呈现文本是「显示名 · 角色」。

        两个候选字段（显示名 / 角色）里哪一个命中的位置由后端的区间给出——前端只按区间包标签。
        Agent 不挂任何 target，所以 ``target`` 是 ``null``（对象标识在 ``agent`` 里）。
        ``status`` 是**编排**给出的事实，由服务层补齐（与 ``list_dms`` 补 ``peer.status`` 同一体例）。
        """
        display_name = row["display_name"]
        role = row["role"]
        text = f"{display_name} · {role}" if role else display_name
        return {
            "kind": "agent",
            "text": text,
            "highlight_ranges": cls._search_highlight_ranges(text, query),
            "target": None,
            "agent": {
                "id": row["id"],
                "display_name": display_name,
                "avatar_slot": row["avatar_slot"],
                "role": role,
                "model_id": row["model_id"],
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
            raise DomainError(
                "validation_failed",
                self._render_copy("error.seq_invalid"),
                fields={"seq": self._render_copy("error.seq_invalid")},
            )
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
