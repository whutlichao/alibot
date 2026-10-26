"""逐版 schema 迁移引擎（issue #90 / #91）。

规格正本：``app/specs/issue-87-spec.md``（D5 步骤注册表、D6 逐版内容、D7 终态、D8 备份 / 事务 /
审计、D2 退出码）。ADR：``docs/adr/0015-schema-migration-is-explicit-and-forward-only.md``。

本模块只做**迁移这件事本身**，不决定进程退出码（那由 CLI 层 ``__main__`` 映射）、不监听任何端口。
它把两半**分开**：

- :func:`run_chain` —— 「对一个**已经打开的库**跑迁移链」：只吃一个 ``sqlite3.Connection`` 与源
  版本，逐步走注册表、做显式数据搬迁与形态对齐。它**不管**备份、审计、事务边界与退出码。
- :func:`migrate_data_dir` —— 「CLI 参数 / 数据目录 / 备份编排」：整目录备份 → 单事务 → 跑链 →
  落审计台账 → 给出报告。

这条缝是**故意留的挂点**：#93 的预演（:class:`StagedLibraryCopy` + :func:`dry_run_chain`）在
**库副本**上跑同一条链（复用 :func:`run_chain`）；#92 的**形态步骤**（:func:`run_shape_step`）已经是
:func:`run_chain` 的前置——它在走 :data:`STEPS` 之前先按**形态**（不看版本号）补齐 channel 型作用域
数据，因此预演与真实迁移共享同一个入口。

**只读预演（#93）**：`--migrate --dry-run` 把 ``alibot.db`` **连同旁边的** ``-wal`` / ``-shm`` 复制成
**数据目录内**的临时副本（只复制主文件会读到不完整状态——已提交但未 checkpoint 的行还在 WAL 里），
在副本上跑完整路径，报告步骤清单与每步将改动的行数，并打出**原库文件 sha256 的前后两个读数**，
随后无条件删除副本。它**从不**用 SQLite 打开原库，因此「原库零字节改动」是构造性的。

**形态步骤（#92）**：``channel`` 行在、而 channel 型的**影子行 / 成员 / 已读 / 预算**有缺时，按
:data:`repair.CHANNEL_SCOPE_MOVES` 的同一份列映射一次补齐**四张表**、影子行最先落。它是链的
**不可省前置**：#37 那类半改名库的 ``schema_version`` 已是新一代，按版本号走链会**跳过** ``3 → 4``，
只有形态步骤能把它搬回来。

**引擎的两条骨架**（规格 D5 / D6）：

1. **逐版步骤注册表**：每步声明 ``from → to``，严格 +1、唯一、连续覆盖
   ``MIN_SUPPORTED_SOURCE_VERSION .. SCHEMA_VERSION``；执行器从库里的版本起**逐步走、不跳步**。
2. **对齐兜底**：每一步之后都向**当前** ``SCHEMA`` 声明的对象集（表 / 列 / 索引 / 触发器 /
   外键约束 / 默认行）**幂等补齐、只增不减**。于是「读数 ↔ 结构」的歧义（例如读数 10 对应过两棵
   不同的树）在推进中被吸收，而终态判据始终是唯一的对齐目标。

**兼容面**：``channel_member`` / ``channel_read`` / ``channel_budget`` 三张旧表**原样保留**（回退面），
不 ``DROP``、不双写——对齐只碰当前 ``SCHEMA`` 声明的对象，旧表不在其中。

**审计落点**：数据目录内的 append-only 台账文件 :data:`MIGRATION_LEDGER_FILE_NAME`（JSON Lines）。
它不引入 schema 变更、不 bump ``SCHEMA_VERSION``，人类面（HTTP）没有任何写入口。

**技能字节迁移（#274）**：``migrate-skills`` 的扫描 / 计划 / 执行也住在本模块
（:func:`plan_skill_migration` / :func:`apply_skill_migration`）。它不推进 schema 版本，只把旧项目层
``<workspace_root>/.alibot/skills/<partition>/<name>/`` 的字节复制到成员家目录并重算 hash；
备份沿用 :func:`_backup_data_dir`（带 ``label="skills"``，与 schema 迁移的 ``<from>-to-<to>`` 命名区分），
清单落数据目录内的 ``skill-migration-<UTC>.json``。库行的 ``source_path`` 记录**来源**（旧项目层路径）。
频道级分区只进报告、不复制；源字节不删除。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import (
    DATABASE_FILE_NAME,
    DEFAULT_LOCALE,
    DEFAULT_MESSAGE_DENSITY,
    DEFAULT_THEME,
    INSERT_WORKSPACE_SETTINGS_SQL,
    MIN_SUPPORTED_SOURCE_VERSION,
    SCHEMA,
    SCHEMA_VERSION,
    STAMP_SCHEMA_VERSION_SQL,
    new_id,
    utc_now,
)
from .mcp import (
    GLOBAL_MCP_FILENAME,
    REMOTE_MCP_TRANSPORTS,
    InvalidMCPDocument,
    validate_mcp_document,
)
from .repair import CHANNEL_SCOPE_MOVES, channel_scope_gaps
from .skills import SKILLS_DIR, SkillValidationError, read_skill_tree, skills_partition
from .workspace import (
    AGENTS_DIR,
    ALIBOT_DIR,
    WorkspaceConfigError,
    WorkspaceRootViolation,
    ensure_state_partition,
    global_home_root,
    resolve_global_read_path,
    resolve_in_root,
    safe_partition_id,
    validate_workspace_root,
)

# 迁移前整目录备份的落点：数据目录**同级**、命名自带方向与 UTC 时间戳（规格 D8）。
BACKUP_INFIX = "-backup-"
BACKUP_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# 迁移审计台账：数据目录内的 append-only 文件（JSON Lines，一行一条记录）。**默认落点**，
# 不引入库内表、不 bump ``SCHEMA_VERSION``（规格 D8 与票面的落点口径）。
MIGRATION_LEDGER_FILE_NAME = "schema-migrations.jsonl"

# ``5 → 6`` 的唯一性收紧：存量里同一 peer 的 DM target 冲突行（`NULL` 不参与，与部分索引的
# ``WHERE`` 逐字对应）。检出即**拒绝升级**（规格 D6、User Story 33），不假设索引一定建得上。
DUPLICATE_DM_PEERS_SQL = (
    "SELECT dm_peer_member_id, COUNT(*) AS n FROM target "
    "WHERE kind = 'dm' AND dm_peer_member_id IS NOT NULL "
    "GROUP BY dm_peer_member_id HAVING COUNT(*) > 1"
)

# ``8 → 9`` 那一代（v9）的附件触发器：绑定的**任何**改写都拒绝。它在 ``10 → 11`` 被两条更细的
# 触发器（``attachment_metadata_immutable`` / ``attachment_binding_is_immutable``）**替换**。
# 这条常量只用于把链上的 `8 → 9` 走成它当时的样子；终态里不存在它。
ATTACHMENT_NO_REBIND_SQL = """
CREATE TRIGGER IF NOT EXISTS attachment_no_rebind
BEFORE UPDATE ON attachment
WHEN OLD.message_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'attachment binding is immutable');
END;
"""

# ``10 → 11`` 要删掉的旧触发器名。
LEGACY_ATTACHMENT_TRIGGER = "attachment_no_rebind"

# ``3 → 4`` 的四条数据搬迁从 :data:`repair.CHANNEL_SCOPE_MOVES` 取（「目标表 ← 旧表 ← 语句」的
# 唯一落点，``--repair-targets`` 与这一步共用）；**影子行必须最先**——另外三张表的引用约束都指向
# ``target``（规格 D6、User Story 22）。列名映射逐列显式写出（不赌列顺序、不用 ``SELECT *``）；
# 来源表存在才搬、不存在就跳过（v3 库一定有它们；分支局部的树可能没有，跳过而不是报错）。
CHANNEL_SCOPE_TABLES: tuple[str, ...] = tuple(table for table, _, _ in CHANNEL_SCOPE_MOVES)

# ─── 结构变更语句模板 ────────────────────────────────────────────────────────
# SQLite 的标识符（表名 / 触发器名）**不能**作为绑定参数，D 型语句的列定义也只能来自我们自己的
# ``SCHEMA`` 解析结果。因此这几条语句以**模块级模板**存在、在模块级拼好文本，再把常量交给
# ``execute``（与 ``db.py`` 的 ``BUSINESS_COUNT_SQL`` 同体例：拼接的从来不是外部输入）。
# 查列名反过来用**表值函数**参数化（``pragma_table_info`` 接受绑定参数）。
TABLE_COLUMN_NAMES_SQL = "SELECT name FROM pragma_table_info(:table)"
ADD_COLUMN_SQL = 'ALTER TABLE "{table}" ADD COLUMN {definition}'
DROP_TRIGGER_SQL = 'DROP TRIGGER IF EXISTS "{name}"'
DROP_TABLE_SQL = 'DROP TABLE "{table}"'
RENAME_TABLE_SQL = 'ALTER TABLE "{source}" RENAME TO "{target}"'
COPY_TABLE_SQL = 'INSERT INTO "{target}" ({target_columns}) SELECT {select_columns} FROM "{source}"'

# ``12 → 13`` 的存量改写：只动「只有钟点」的行——已经是「日期 + 钟点」的行由 ``GLOB`` 的模式排除
# 在第一项上（新形状的前四个字符是数字 + ``-``，不会是两位钟点 + ``:``），于是**幂等**不动它们。
# 前缀（= 迁移执行当日的 ``YYYY-MM-DD ``）由绑定参数给，不在 SQL 里拼。
# 模式与 :data:`alibot.db.REMINDER_TIME_PATTERN` 的旧半互为镜像：这里认的是「**旧**形状」这个窄集合。
REWRITE_LEGACY_REMINDER_TIME_SQL = (
    "UPDATE reminder SET time = ? || time "
    "WHERE time GLOB '[0-2][0-9]:[0-5][0-9]' AND time NOT GLOB '*[^0-9:]*'"
)

# 存量 ``HH:MM`` 要补的日期前缀格式（本机本地日期；口径见 :func:`_reminder_legacy_time_prefix`）。
REMINDER_LEGACY_DATE_FORMAT = "%Y-%m-%d "

# 「时间语义一次性收窄」那一票的号段（``12 → 13``）。报告里的收窄说明只在链真的含这一步时给出，
# 因此这里需要这两个数（写成常量而不是散在两处的字面量）。
REMINDER_TIME_STEP_FROM = 12
REMINDER_TIME_STEP_TO = 13

# 表重建时临时表的前缀（迁移事务内短暂存在，随后被 RENAME 成正式名）。
REBUILD_TABLE_PREFIX = "__migrate_new_"

# ``REFERENCES <表>(<列…>)``：外键引用（列清单里不会再有嵌套括号，正则安全）。
FOREIGN_KEY_REFERENCE_RE = re.compile(r"REFERENCES\s+[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)")

# CREATE TABLE 主体里以这些关键字开头的条目是**表级约束**，不是列（解析列定义时跳过）。
_TABLE_CONSTRAINT_KEYWORDS = frozenset({"UNIQUE", "FOREIGN", "PRIMARY", "CHECK", "CONSTRAINT"})


class MigrationError(Exception):
    """迁移没能完成。

    基类同时覆盖三种「没做成」：备份失败（:class:`BackupError`）、某一步失败
    （:class:`StepError`，含 ``5 → 6`` 的存量冲突）、以及整体失败并已回滚
    （:class:`MigrationFailed`）。CLI 层把这三种统一映射为退出码 ``5``（「动手了、失败并已回滚」），
    与「库不被本代码接受」（``3``）和「库不被本入口处置」（``4``）区分开。
    """


class BackupError(MigrationError):
    """迁移前的整目录备份失败：**拒绝迁移**，不退化成「放行 + 一句警告」（规格 D8 / S7）。"""


class StepError(MigrationError):
    """某一步自身拒绝继续（例如 ``5 → 6`` 的唯一性收紧遇到存量冲突行）。"""


class MigrationFailed(MigrationError):
    """链跑到一半失败，已 ``ROLLBACK``，库停在与动手前一致的版本。"""


class DryRunError(MigrationError):
    """预演**没能跑成**：副本建不出来，或者跑完删不干净（#93）。

    它与「库不被本代码接受」（``3``）、「库不被本入口处置」（``4``）都不同，与链上失败同族：CLI 层
    把它映射为 ``5``（「预演没能跑通」）。不论哪一种，原库都**一个字节都没动**——预演从不拿 SQLite
    打开原库，副本删不干净也只在数据目录内留下那一个临时子目录。
    """


@dataclass(frozen=True)
class StepReport:
    """一步的执行读数：标签 + ``from → to`` + 本步改动的**数据行数**（DDL 不计行）。"""

    label: str
    from_version: int
    to_version: int
    changed_rows: int


@dataclass(frozen=True)
class ChainResult:
    """对**一个库**跑完一条链的结果（不含备份 / 审计 / 退出码）。"""

    from_version: int
    to_version: int
    steps: tuple[StepReport, ...]

    @property
    def changed_rows(self) -> int:
        return sum(step.changed_rows for step in self.steps)


@dataclass(frozen=True)
class MigrationReport:
    """一次**真正执行**的迁移的全部可观察读数（stdout 报告的数据源）。"""

    db_path: Path
    backup_path: Path
    ledger_path: Path
    chain: ChainResult
    started_at: str
    finished_at: str


MigrationStepFn = Callable[[sqlite3.Connection], int]


@dataclass(frozen=True)
class MigrationStep:
    """注册表里的一步：声明 ``from → to``（严格 +1）与它自己的显式改动。

    ``apply`` 只做该步的**主改动**（数据搬迁 / 建表 / 加列 / 触发器替换），返回本步改动的数据行数。
    它不吃版本号、不管事务、不管对齐——对齐是执行器在每步之后统一施的兜底。
    """

    from_version: int
    to_version: int
    label: str
    apply: MigrationStepFn


# ─── 当前 SCHEMA 的「参考形态」 ──────────────────────────────────────────────
# 用 SQLite 自己解析 ``SCHEMA``：在内存库里跑一遍，再读它的 ``sqlite_master``。这样「当前形态」
# 永远只有 SCHEMA 这一个正本，不存在第二份手抄的列 / 索引 / 触发器清单。
@dataclass(frozen=True)
class _ReferenceShape:
    tables: tuple[str, ...]
    table_sql: dict[str, str]
    columns: dict[str, tuple[tuple[str, str], ...]]
    indexes: tuple[str, ...]
    index_sql: dict[str, str]
    triggers: tuple[str, ...]
    trigger_sql: dict[str, str]


def _split_top_level(text: str) -> list[str]:
    """按**顶层逗号**切分（括号与单引号内的逗号不算）。"""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = False
    for char in text:
        if quoted:
            current.append(char)
            quoted = char != "'"
            continue
        if char == "'":
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _without_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _column_definitions(create_sql: str) -> tuple[tuple[str, str], ...]:
    """从 ``CREATE TABLE`` 文本里抽 ``(列名, 列定义)``；表级约束与注释行被跳过。

    显式用**解析出来的定义**做 ``ALTER TABLE ADD COLUMN``，于是新增列连它的 ``NOT NULL`` /
    ``DEFAULT`` / 列级 ``CHECK`` 一起落到库里——只写列名会让库级约束与 ``SCHEMA`` 文本不一致
    （规格 D6 的 ``9 → 10``）。
    """
    body = create_sql[create_sql.index("(") + 1 : create_sql.rindex(")")]
    columns: list[tuple[str, str]] = []
    for entry in _split_top_level(_without_comments(body)):
        entry = " ".join(entry.split())
        if not entry:
            continue
        name = entry.split()[0]
        if name.upper() in _TABLE_CONSTRAINT_KEYWORDS:
            continue
        columns.append((name, entry))
    return tuple(columns)


def _foreign_key_references(create_sql: str) -> tuple[str, ...]:
    """``CREATE TABLE`` 里声明的全部外键引用片段（内联 ``REFERENCES`` 与表级 ``FOREIGN KEY``）。"""
    return tuple(_normalize_sql(match.group(0)) for match in FOREIGN_KEY_REFERENCE_RE.finditer(create_sql))


def _build_reference_shape() -> _ReferenceShape:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA)
        tables = tuple(
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        )
        table_sql = {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
        }
        indexes = tuple(
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL ORDER BY rowid"
            )
        )
        index_sql = {
            str(row[0]): str(row[1])
            for row in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            )
        }
        triggers = tuple(
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY rowid")
        )
        trigger_sql = {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
        }
    finally:
        conn.close()
    return _ReferenceShape(
        tables=tables,
        table_sql=table_sql,
        columns={name: _column_definitions(table_sql[name]) for name in tables},
        indexes=indexes,
        index_sql=index_sql,
        triggers=triggers,
        trigger_sql=trigger_sql,
    )


# 当前 ``SCHEMA`` 的解析形态：对齐兜底与终态判据的唯一目标。
CURRENT_SCHEMA_SHAPE = _build_reference_shape()


# ─── 连接与目录级读数 ────────────────────────────────────────────────────────
def connect(db_path: Path) -> sqlite3.Connection:
    """打开一个可写连接（连接级 PRAGMA 不改库文件一个字节，与 ``db.Database._connect`` 同口径）。

    **刻意不切 WAL**：迁移失败要回滚到「原版本 + 原文件形态」，``PRAGMA journal_mode = WAL``
    会改库文件头，属于闸门 / 回滚面之外的副作用。SQLite 的 DDL 在回滚日志模式下同样是事务性的。
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(TABLE_COLUMN_NAMES_SQL, {"table": table})}


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def _object_table(sql: str) -> str | None:
    """索引 / 触发器 DDL 里的基表名（``... ON <table>(`` 或 ``... ON <table> WHEN ...``）。

    对象可能比它的基表先被对齐到（例如从 v4 起步时 ``attachment`` 尚未建出）：那时创建索引 /
    触发器会撞上 ``no such table``。这里先取基表名，基表不在就跳过——它不是「静默忽略一个失败」，
    而是把这件事交给**同一次对齐**里更早的建表步骤（紧随其后的建表 / 下一轮对齐会补上）。
    """
    match = re.search(r"\bON\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)
    return None if match is None else match.group(1)


def _indexed_columns(sql: str) -> frozenset[str]:
    """索引 DDL 里 ``ON <table>(<列…>)`` 列出的列名。

    只取每一项的**首个标识符**；含 ``(`` 的项是表达式（例如 ``casefold(body)``），不对它做
    「列是否存在」的判定——表达式内部的名字会被误当成列名。没有任何可判定的列时返回空集，
    调用方的子集判断因此放行（创建失败仍会被下面翻成可读的拒绝）。
    """
    match = re.search(r"\bON\s+[A-Za-z_][A-Za-z0-9_]*\s*\(([^)]*)\)", sql, re.IGNORECASE)
    if match is None:
        return frozenset()
    columns: set[str] = set()
    for term in _split_top_level(match.group(1)):
        entry = term.strip()
        if not entry or "(" in entry:
            continue
        columns.add(entry.split()[0].strip('"'))
    return frozenset(columns)


def _create_missing_tables(conn: sqlite3.Connection, names: tuple[str, ...]) -> None:
    """按**当前 SCHEMA** 的 DDL 建出缺失的表（``IF NOT EXISTS``，幂等、只增不减）。"""
    existing = _table_names(conn)
    for name in names:
        if name not in existing:
            conn.execute(CURRENT_SCHEMA_SHAPE.table_sql[name])


def _add_missing_columns(conn: sqlite3.Connection, table: str) -> None:
    """给既有表补**当前 SCHEMA** 声明的缺失列（连列定义一起，含默认值与列级 CHECK）。"""
    if table not in _table_names(conn):
        return
    existing = _column_names(conn, table)
    for name, definition in CURRENT_SCHEMA_SHAPE.columns[table]:
        if name not in existing:
            conn.execute(ADD_COLUMN_SQL.format(table=table, definition=definition))


def _rebuild_table(
    conn: sqlite3.Connection,
    table: str,
) -> None:
    """把一张既有表**重建**成当前 ``SCHEMA`` 的 DDL 形态并保真复制既有行。

    逐列原样复制；**不**提供逐列取值改写（``23 → 24`` 的 ``agentic → reme`` 映射随
    ``24 → 25`` 删列一起退场，见 ADR-0035）。

    重建存在的唯一理由：SQLite 没有 ``ALTER TABLE ADD CONSTRAINT``，而 ``message`` /
    ``round_outcome`` 这类表在 v3 就已存在、外键引用只能靠重建补上（规格 D6 的 ``3 → 4``「加
    ``REFERENCES target(id)``」与 ``4 → 5`` 的复合外键）。列本身不动、数据逐行复制，因此这是
    「加约束」而不是「改写既有列」。

    ``PRAGMA defer_foreign_keys = ON`` 让 ``DROP TABLE`` 的隐式删除不立刻校验外键——新表随后被
    改名为正式名、行与原表一致，因此 ``COMMIT`` 时的检查仍然成立。

    迁移连接在 ``BEGIN`` 之前显式关掉 ``foreign_keys``（见 :func:`migrate_data_dir` / :func:`dry_run_chain`）：
    重建**被引用的父表**（例如 ``channel``）会先隐式删除父行，即使延迟到 ``COMMIT`` 仍会报
    ``FOREIGN KEY constraint failed``。链末的 ``PRAGMA foreign_key_check`` 负责证明数据仍然完整。
    """
    conn.execute("PRAGMA defer_foreign_keys = ON")
    temporary = f"{REBUILD_TABLE_PREFIX}{table}"
    columns = [name for name, _ in CURRENT_SCHEMA_SHAPE.columns[table] if name in _column_names(conn, table)]
    target_columns = ", ".join(f'"{name}"' for name in columns)
    select_columns = ", ".join(f'"{name}"' for name in columns)
    definition = CURRENT_SCHEMA_SHAPE.table_sql[table]
    # sqlite_master 存的建表语句可能带、也可能不带 ``IF NOT EXISTS``（SQLite 会把它规范化掉），
    # 因此替换表名时两态都要认。
    renamed = re.sub(
        rf"(CREATE TABLE\s+(?:IF NOT EXISTS\s+)?){re.escape(table)}(\s*\()",
        rf"\g<1>{temporary}\g<2>",
        definition,
        count=1,
    )
    conn.execute(renamed)
    conn.execute(
        COPY_TABLE_SQL.format(
            target=temporary,
            target_columns=target_columns,
            select_columns=select_columns,
            source=table,
        )
    )
    conn.execute(DROP_TABLE_SQL.format(table=table))
    conn.execute(RENAME_TABLE_SQL.format(source=temporary, target=table))


def _rebuild_tables_missing_foreign_keys(conn: sqlite3.Connection) -> None:
    """重建**缺少当前 ``SCHEMA`` 外键引用**的既有表（只碰外键，列由 ``ADD COLUMN`` 负责）。"""
    records = {
        str(row[0]): str(row[1])
        for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
    }
    for table in CURRENT_SCHEMA_SHAPE.tables:
        current = records.get(table)
        if current is None:
            continue
        wanted = _foreign_key_references(_without_comments(CURRENT_SCHEMA_SHAPE.table_sql[table]))
        if not wanted:
            continue
        present = _normalize_sql(_without_comments(current))
        if all(reference in present for reference in wanted):
            continue
        _rebuild_table(conn, table)


def _create_missing_indexes(conn: sqlite3.Connection) -> None:
    """补齐**当前 SCHEMA** 声明的缺失索引（含部分唯一索引）。

    唯一索引是**唯一性收紧**：存量里已有冲突行时它建不上——那是**显式拒绝升级**的信号，不是
    「建不上就算了」。这里把 ``IntegrityError`` 翻成 :class:`StepError`，于是无论它是被哪一步的
    对齐兜底撞上的，给出的都是同一句可读的拒绝，而不是一句裸的 ``UNIQUE constraint failed``。
    """
    existing = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    tables = _table_names(conn)
    for name in CURRENT_SCHEMA_SHAPE.indexes:
        if name in existing:
            continue
        table = _object_table(CURRENT_SCHEMA_SHAPE.index_sql[name])
        if table is not None and table not in tables:
            continue  # 基表还没建出来：交给同一次对齐里更早的建表步骤
        if table is not None and not _indexed_columns(CURRENT_SCHEMA_SHAPE.index_sql[name]) <= _column_names(
            conn, table
        ):
            # 索引引用的列还没到（例如从 v5 起步时 ``message.task_pointer`` 尚未补上）：同样交给
            # 「补列 → 建索引」这条更大的对齐。它不是静默放过唯一性收紧——执行器每步之后都会再跑
            # 一次对齐，那时列已在，索引照常建出；列补不上时下面依然会把冲突翻成可读的拒绝。
            continue
        try:
            conn.execute(CURRENT_SCHEMA_SHAPE.index_sql[name])
        except sqlite3.IntegrityError as exc:
            raise StepError(
                f"补齐唯一索引 {name} 时遇到存量冲突行：这条索引是一次唯一性收紧，"
                f"存量数据不满足它的约束，本次迁移已拒绝继续、库未改动。原因：{exc}"
            ) from exc


def _replace_changed_triggers(conn: sqlite3.Connection) -> None:
    """补齐**当前 SCHEMA** 声明的触发器；同名但文本不同的**先删后建**（替换语义）。

    这条正是 ``10 → 11`` 要的东西：``CREATE TRIGGER IF NOT EXISTS`` 使「建」幂等，却**不使
    「替换」幂等**——曾按 v9 建过旧触发器的库必须在终态里只剩新的一套（规格 D6、User Story 31）。
    """
    existing = {
        str(row[0]): str(row[1])
        for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
    }
    tables = _table_names(conn)
    for name in CURRENT_SCHEMA_SHAPE.triggers:
        wanted = CURRENT_SCHEMA_SHAPE.trigger_sql[name]
        table = _object_table(wanted)
        if table is not None and table not in tables:
            continue  # 基表还没建出来：交给同一次对齐里更早的建表步骤
        current = existing.get(name)
        if current is not None and _normalize_sql(current) == _normalize_sql(wanted):
            continue
        if current is not None:
            conn.execute(DROP_TRIGGER_SQL.format(name=name))
        conn.execute(wanted)


def _ensure_workspace_settings_row(conn: sqlite3.Connection) -> int:
    """按目标形态补上 ``workspace_settings`` 的 ``id = 1`` 缺省行（幂等）。

    它是 D7 明写的「按目标形态补齐的默认行」：从 ``7`` 及以后起步的链不会经过 ``6 → 7``，一个缺这张
    表（或表在而缺行）的树只有靠对齐兜底才能拿到这行可读的设置面。
    """
    if "workspace_settings" not in _table_names(conn):
        return 0
    cursor = conn.execute(
        INSERT_WORKSPACE_SETTINGS_SQL,
        (DEFAULT_LOCALE, DEFAULT_MESSAGE_DENSITY, DEFAULT_THEME, utc_now()),
    )
    return max(cursor.rowcount, 0)


def _align_to_current_schema(conn: sqlite3.Connection) -> None:
    """共享的对齐兜底：向当前 ``SCHEMA`` 声明的对象集**幂等补齐、只增不减**。

    顺序是有讲究的：先建表（索引 / 触发器 / 外键的目标表要在）→ 补列（列级 CHECK 随之落库）→
    重建缺外键的表 → 补索引 → 替换触发器 → 补默认行。只碰 ``SCHEMA`` 里有的对象，因此三张旧表
    （``channel_member`` / ``channel_read`` / ``channel_budget``）与任何分支局部的多余对象都原样
    留着——回退面不被这次迁移清掉。
    """
    _create_missing_tables(conn, CURRENT_SCHEMA_SHAPE.tables)
    for table in CURRENT_SCHEMA_SHAPE.tables:
        _add_missing_columns(conn, table)
    _rebuild_tables_missing_foreign_keys(conn)
    _create_missing_indexes(conn)
    _replace_changed_triggers(conn)
    _ensure_workspace_settings_row(conn)


# ─── 逐版步骤 ────────────────────────────────────────────────────────────────
def _step_3_to_4(conn: sqlite3.Connection) -> int:
    """channel 型作用域数据搬迁：影子行最先落，三张旧表按 ``channel_id → target_id`` 逐列搬。

    ``target`` 的四张表要**先**在（另外三张的引用约束指向 ``target``），所以本步自己先把它们
    建到当前形态，再跑四条 ``INSERT OR IGNORE``（幂等：重复跑改动 0 行）。三张旧表**原样保留**。
    """
    _create_missing_tables(conn, CHANNEL_SCOPE_TABLES)
    existing = _table_names(conn)
    changed = 0
    for _target, source, sql in CHANNEL_SCOPE_MOVES:
        if source not in existing:
            continue
        changed += max(conn.execute(sql).rowcount, 0)
    return changed


def _step_4_to_5(conn: sqlite3.Connection) -> int:
    """任务表与任务线程：``task``（含表级 ``UNIQUE(target_id, id)``）+ ``message.task_ref`` + 2 条触发器。

    表级唯一约束必须保留——复合外键 ``(target_id, task_ref) → task(target_id, id)`` 靠它才建得上；
    这里直接按当前 ``SCHEMA`` 建 ``task``，所以约束随 DDL 一起在。
    """
    _create_missing_tables(conn, ("task",))
    _add_missing_columns(conn, "message")
    _replace_changed_triggers(conn)
    return 0


def _step_5_to_6(conn: sqlite3.Connection) -> int:
    """私信一对一唯一：``target.dm_peer_member_id`` + 部分唯一索引。

    这是**唯一性收紧**：存量里同一 peer 若有多行 DM target，索引建不上——必须**显式检出并拒绝
    升级**，不得假设一定建得上、也不得静默跳过（规格 D6、User Story 33）。
    """
    _add_missing_columns(conn, "target")
    duplicates = conn.execute(DUPLICATE_DM_PEERS_SQL).fetchall()
    if duplicates:
        detail = "、".join(f"{row[0]}（{row[1]} 行）" for row in duplicates)
        raise StepError(
            "5 → 6 的一对一唯一约束遇到存量冲突：同一个私信对端在 target 里有多行 "
            f"（kind='dm'）：{detail}。请先人工合并这些重复的 DM target 再重跑迁移；"
            "本次迁移已拒绝继续，库未改动。"
        )
    _create_missing_indexes(conn)
    return 0


def _step_6_to_7(conn: sqlite3.Connection) -> int:
    """工作区设置单行表：``workspace_settings`` + 补上 ``id = 1`` 那一行（缺省档位）。"""
    _create_missing_tables(conn, ("workspace_settings",))
    _add_missing_columns(conn, "workspace_settings")
    return _ensure_workspace_settings_row(conn)


def _step_7_to_8(conn: sqlite3.Connection) -> int:
    """提醒表：``reminder``（``channel_id`` 可空、引用的是 ``channel`` 而不是 ``target``）。"""
    _create_missing_tables(conn, ("reminder",))
    return 0


def _step_8_to_9(conn: sqlite3.Connection) -> int:
    """附件表 + 索引 + 触发器：``attachment`` + ``idx_attachment_message`` + 两条 v9 触发器。

    对 main 用户这一步是**空步**（``8`` 从未作为 main 的读数存在），但它必须是链上连续的一环——
    分支局部号段的库只有靠它才能推进（规格 D6、User Story 24）。
    """
    _create_missing_tables(conn, ("attachment",))
    _create_missing_indexes(conn)
    conn.execute(ATTACHMENT_NO_REBIND_SQL)
    _replace_changed_triggers(conn)
    return 0


def _step_9_to_10(conn: sqlite3.Connection) -> int:
    """主题三档：``workspace_settings.theme`` + **列级 ``CHECK``**（只补列会让库级约束不一致）。"""
    _create_missing_tables(conn, ("workspace_settings",))
    _add_missing_columns(conn, "workspace_settings")
    return 0


def _step_10_to_11(conn: sqlite3.Connection) -> int:
    """附件触发器**替换**：删 ``attachment_no_rebind``，建两条更细的不可变触发器（先删后建）。"""
    _create_missing_tables(conn, ("attachment",))
    conn.execute(DROP_TRIGGER_SQL.format(name=LEGACY_ATTACHMENT_TRIGGER))
    _replace_changed_triggers(conn)
    return 0


def _step_11_to_12(conn: sqlite3.Connection) -> int:
    """认领与 ``claimed_at``：``agent_config.auto_claim``（缺省 ``0`` = 关闭）与 ``task.claimed_at``。

    存量行取目标形态 DDL 默认值：``auto_claim = 0``、``claimed_at = NULL``（= 从未被认领，升级后
    仍可被认领一次并开一次世代）。两个列定义都从 ``SCHEMA`` 解析出来，``CHECK`` 不会漏。
    """
    _add_missing_columns(conn, "task")
    _add_missing_columns(conn, "agent_config")
    return 0


def _reminder_legacy_time_prefix() -> str:
    """存量别名要补的日期前缀：**迁移执行当日**（本机本地时区，``YYYY-MM-DD ``）。

    口径出处：ADR-0017 §五 / 规格 C12。为什么是「迁移执行当日」而不是「下一个该钟点」——后者的
    结果**依赖迁移执行的时刻**（同一份备份 08:00 迁与 16:00 迁得到两个不同的到期日），不可复现、
    难以写测试；前者是**唯一不依赖运行时状态**的解释口径。

    单独提为一个函数只为一件事：让「当日」这个词的地点只有一个（测试可以把 ``TZ`` 固定住真跑
    子进程，断言读到的日期就是这个函数给出的那个）。
    """
    return datetime.now().astimezone().strftime(REMINDER_LEGACY_DATE_FORMAT)


def _step_12_to_13(conn: sqlite3.Connection) -> int:
    """提醒的到点形态：``reminder.time`` 形状扩为「日期 + 钟点」+ 新增可空列 ``fired_at``。

    **硬要求（ADR-0017 §五 / 规格 C12、C13）**：存量只有钟点的行统一解释成「**迁移执行当日**」的
    该钟点（本机本地时间口径），``fired_at`` 补 ``NULL``。这是一次**时间语义的一次性收窄**：用户
    原来的「**每天** 9 点」在迁移后变成「**迁移那天** 9 点」。它不丢数据行，但**改变了行的含义**
    ——因此报告必须能读到这句（见 :data:`REMINDER_TIME_SEMANTICS_NARROWING`），不能只有步数统计。

    本步只做**改写与加列**，不写任何投递逻辑（原子性「落消息 + 写 ``fired_at``」归票 ②）；
    也不加第二个状态列（``state`` 是读路径的派生值，D4 否掉的是状态**列**）。

    改写是**窄的**：只动 ``time`` 形如旧 ``HH:MM`` 的行。已是新形状的行原样留着，于是重跑本步
    （或幂等地重跑整条链）不会二次改写；返回的行数就是**实际被改写**的 ``reminder`` 行数。
    """
    _create_missing_tables(conn, ("reminder",))
    _add_missing_columns(conn, "reminder")
    cursor = conn.execute(REWRITE_LEGACY_REMINDER_TIME_SQL, (_reminder_legacy_time_prefix(),))
    return max(cursor.rowcount, 0)


def _step_13_to_14(conn: sqlite3.Connection) -> int:
    """回应预算的**配置落点**（#53）：频道上界覆盖表 + 该世代生效上界的快照列。

    本步只做两件 DDL 级的事，**不动任何既有行**（返回 0 行改动）：

    - 建 ``channel_budget_limit``：库内**逐频道**配置（没有行 = 回落内置默认 3）；
    - 给 ``target_budget`` 补 ``applied_limit``：该世代生效上界的快照列，可空。

    **不回填旧数据**（ADR-0015 的只前进纪律）：存量 ``target_budget`` 行的 ``applied_limit`` 留
    ``NULL``，读路径把它解释为「该世代用的是内置默认值」——这与本票之前的行为逐字等价（配置能力
    是这一版才出现的，旧世代不可能有频道覆盖），因此不需要、也不允许按今天的配置倒推历史快照。
    """
    _create_missing_tables(conn, ("channel_budget_limit",))
    _add_missing_columns(conn, "target_budget")
    return 0


def _step_14_to_15(conn: sqlite3.Connection) -> int:
    """轮次结果新增第六个状态 ``silent``（#58 / ADR-0008）：``round_outcome.status`` 的 CHECK 重建。

    为什么必须是**重建**而不是 ``ADD COLUMN``：status 的取值白名单是**表级** ``CHECK``，SQLite 没有
    ``ALTER TABLE … ADD CONSTRAINT``（列级 CHECK 才能随 ``ADD COLUMN`` 补，见 ``9 → 10``）。因此这一步
    走 ADR-0015 第三节明写的形态——「建新表 / 搬迁 / 回填」，落点就是既有的 :func:`_rebuild_table`：
    按**当前** ``SCHEMA`` 的 DDL 建临时表、逐列复制既有行、``DROP`` 原名表、把临时表改名回来。

    幂等与保真的两条纪律：

    - **不回填语义**：既有行原样复制。v14 的库里不可能有 ``silent``（那一版没有这个状态，也没有任何
      代码路径能产生它），所以这里既不造、也不推断——升级只放宽**允许**的取值；
    - **重复执行是空操作**：表级 DDL 里已经点名 ``silent`` 就跳过（``plan_steps`` 本身也不会重跑，
      这一条防的是手工重放与形态对齐把同一张表反复重建）。

    索引 / 触发器随 ``DROP TABLE`` 一起消失，由执行器在每步之后施的 :func:`_align_to_current_schema`
    按当前 ``SCHEMA`` 补回来——那条路径既有、已有测试覆盖，本步不重复实现。
    """
    _create_missing_tables(conn, ("round_outcome",))
    stored = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'round_outcome'"
    ).fetchone()
    if stored is not None and "'silent'" in _normalize_sql(_without_comments(str(stored[0]))):
        return 0
    _rebuild_table(conn, "round_outcome")
    return 0


# ``15 → 16`` 的六张新表（规格 §4.2 / ADR-0018 / ADR-0022 / ADR-0023）：team 运行与分派、
# Plan 运行与事项、SOP 定义与运行。它们是**纯增量**：既有表只给 ``channel`` 补两列。
TEAM_PLAN_SOP_TABLES: tuple[str, ...] = (
    "team_run",
    "team_assignment",
    "plan_run",
    "plan_task",
    "sop_definition",
    "sop_run",
)


def _step_15_to_16(conn: sqlite3.Connection) -> int:
    """team 模式 / Plan / SOP 的纯增量形态（#138 / 规格 §4.1 / §4.2）。

    本步只做两件事：按**当前 ``SCHEMA``** 建出六张新表、给 ``channel`` 补 ``mode`` /
    ``team_leader_member_id`` 两列。索引与两条「team 必须有队长」触发器由执行器在本步之后施的
    :func:`_align_to_current_schema` 按同一份 ``SCHEMA`` 创建（那条路径既有、已有测试覆盖）。

    **不重建既有表、不回填业务数据**：旧频道的 ``mode`` 由列默认值 ``'autonomous'`` 解释，
    ``team_leader_member_id`` 为 ``NULL``（规格 §4.1 的旧行语义）。``ADD COLUMN`` 连列级 ``CHECK``
    与 ``REFERENCES`` 一起落库，因此不触发「缺外键就重建」的对齐路径。
    """
    _create_missing_tables(conn, TEAM_PLAN_SOP_TABLES)
    _add_missing_columns(conn, "channel")
    return 0


# ``16 → 17`` 的五张新表（规格 §4.3 / ADR-0021）：权限规则、HITL 审批、MCP 声明、Skill 元数据与
# 远端数据面确认。它们是**纯增量**：既有表只给 ``channel`` 补 workspace 三列、给 ``agent_config``
# 补四列。旧 ``remote_consent`` 保留作兼容面。
WORKSPACE_PERMISSION_TABLES: tuple[str, ...] = (
    "permission_rule",
    "approval_request",
    "mcp_server",
    "skill",
    "remote_consent_scope",
)

# 旧 ``remote_consent`` 行的**一次性**复制：每条 ``provider`` 变成 ``('model', provider,
# acknowledged_at)``。``INSERT OR IGNORE`` 让重放幂等（复合主键已存在时不动既有确认时间）；
# 旧表不删（ADR-0015 的兼容面纪律）。
COPY_REMOTE_CONSENT_SCOPE_SQL = (
    "INSERT OR IGNORE INTO remote_consent_scope (scope_kind, scope_id, acknowledged_at) "
    "SELECT 'model', provider, acknowledged_at FROM remote_consent"
)


def _step_16_to_17(conn: sqlite3.Connection) -> int:
    """workspace / permission / MCP / Skill / approval 的纯增量形态（#139 / 规格 §4.1 / §4.3）。

    本步做三件事：按**当前 ``SCHEMA``** 建出五张新表、给 ``channel`` 与 ``agent_config`` 补本版新增列、
    把旧 ``remote_consent`` 的每一行复制成 ``remote_consent_scope`` 的 ``('model', provider, ...)``。
    索引 / 触发器 / 外键由执行器在本步之后施的 :func:`_align_to_current_schema` 按同一份 ``SCHEMA``
    补齐。

    **不重建既有表、不回填其他业务数据**：旧频道的 ``workspace_root`` 为 ``NULL``、后端 ``local``、
    权限档 ``ask-writes``；旧 Agent 的后端 / 权限档为 ``NULL``（跟随频道默认）、本地 shell 关闭、
    镜像未配置（规格 §4.1 的旧行语义）。复制是幂等的：已经存在的 scope 行不被覆盖，重跑改动 0 行。
    返回的行数只统计**实际复制**的 ``remote_consent`` 行。
    """
    _create_missing_tables(conn, WORKSPACE_PERMISSION_TABLES)
    _add_missing_columns(conn, "channel")
    _add_missing_columns(conn, "agent_config")
    if "remote_consent" not in _table_names(conn):
        return 0
    return max(conn.execute(COPY_REMOTE_CONSENT_SCOPE_SQL).rowcount, 0)


# ``17 → 18`` 的两列（ADR-0026 §二）：频道级 = 默认档（``NULL`` = 关闭），Agent 级 = 覆盖
# （``NULL`` = 跟随频道）。``web_access`` 不是新表：它与 ``workspace_backend`` 同级，是**单值配置**。
WEB_ACCESS_TABLES: tuple[str, ...] = ("channel", "agent_config")


def _step_17_to_18(conn: sqlite3.Connection) -> int:
    """Agent 联网的库形态（#184 / ADR-0026 §二 / §四）：`web` 档 + 两个 ``web_access`` 列。

    本步只做两件 DDL 级的事，**不动任何既有行的取值**（返回 0 行改动）：

    - 给 ``channel`` 与 ``agent_config`` 补 ``web_access``（连列级 ``CHECK`` 一起，与 ``9 → 10`` /
      ``16 → 17`` 同一手法）；
    - **重建** ``remote_consent_scope``：它的取值白名单是**表级** ``CHECK``，而 SQLite 没有
      ``ALTER TABLE … ADD CONSTRAINT``，所以把 ``web`` 纳入白名单只能走 :func:`_rebuild_table`
      （建新表 / 逐列复制 / ``DROP`` 原名表 / 改名回来）。

    **不变填也不丢数据**（ADR-0015 的只前进纪律）：旧频道的 ``web_access`` 停在 ``NULL``（= 关闭，
    与升级前「没有联网工具」逐字等价）；旧 Agent 的也停在 ``NULL``（= 跟随频道）；
    ``remote_consent_scope`` 的既有行由 :func:`_rebuild_table` 逐列原样复制。这里不造第二个
    ``web`` 确认行、也不按今天的配置倒推历史。

    **重复执行是空操作**：表级 DDL 里已经点名 ``web`` 就跳过重建（与 ``14 → 15`` 同一条幂等纪律，
    防止手工重放把同一张表反复重建）。
    """
    _create_missing_tables(conn, ("remote_consent_scope",))
    for table in WEB_ACCESS_TABLES:
        _add_missing_columns(conn, table)
    stored = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'remote_consent_scope'"
    ).fetchone()
    if stored is not None and "'web'" in _normalize_sql(_without_comments(str(stored[0]))):
        return 0
    _rebuild_table(conn, "remote_consent_scope")
    return 0


# ``18 → 19`` 的一列（#187 / ADR-0026 §五）：频道级「容器是否可出网」开关。它不是新表，也不是
# 白名单——白名单复用 ``permission_rule`` 里 ``fetch`` 的 allow 规则的 host（D3）。
DOCKER_EGRESS_TABLES: tuple[str, ...] = ("channel",)


def _step_18_to_19(conn: sqlite3.Connection) -> int:
    """Docker 后端容器网络策略的库形态（#187 / ADR-0026 §五）：``channel.docker_egress``。

    只加一列（连列级 ``CHECK`` 一起，与 ``16 → 17`` / ``17 → 18`` 同一手法），**不动任何既有行的
    取值**（返回 0 行改动）：旧频道的 ``docker_egress`` 停在 0，读路径把它解释成「容器默认无网」。
    这**不是回填**，而是本版比上一版更严的默认值——ADR-0026 §五 的裁定就是「容器默认无网」，
    升级不应把旧频道静默留在有网状态。

    **重复执行是空操作**：:func:`_add_missing_columns` 只补缺的列。
    """
    _create_missing_tables(conn, DOCKER_EGRESS_TABLES)
    for table in DOCKER_EGRESS_TABLES:
        _add_missing_columns(conn, table)
    return 0


def _step_19_to_20(conn: sqlite3.Connection) -> int:
    """频道指针消息的库形态（#198 / ADR-0028）：``message.task_pointer`` + 部分唯一索引 + 触发器。

    三件都是**纯增量**，不动任何既有行（返回 0 行改动）：

    - 可空列 ``task_pointer``（带 ``REFERENCES task(id)``）：``NULL`` = 不是频道指针消息；
    - 部分唯一索引 ``idx_message_task_pointer``：一张任务至多一条指针消息；
    - ``BEFORE INSERT`` 触发器 ``message_task_pointer_is_scoped``：非空指针必须是「channel 型
      target 里的 system 消息，且指向该频道下的任务」。

    **不回填旧数据**（ADR-0015 的只前进纪律）：升级前的任务没有频道消息，读路径如实返回
    ``None``；本步不替历史任务造一条。

    **重复执行是空操作**：``_add_missing_columns`` / ``_create_missing_indexes`` 都只补缺；
    触发器由 ``_replace_changed_triggers`` 按文本比对。
    """
    _add_missing_columns(conn, "message")
    _create_missing_indexes(conn)
    _replace_changed_triggers(conn)
    return 0


# ``21 → 22``（本票 #199）：联网档纳入 ``off`` 需要重建两张表的列级 CHECK；凭据表是纯增量。
# 号段说明：本票原先自行取的是 21，但 #195（自动认领默认开启）随 main 先落地了 21，按 ADR-0015
# 「必须高于当前 main」的判据，本步顺延为 ``21 → 22``。
WEB_ACCESS_REBUILD_TABLES: tuple[str, ...] = ("channel", "agent_config")
WEB_CREDENTIAL_TABLES: tuple[str, ...] = ("web_provider_credential",)


def _step_21_to_22(conn: sqlite3.Connection) -> int:
    """多 provider 搜索 + 凭据入库 + 联网档显式 ``off``（#199）。

    两件 DDL 级的事，**不动任何既有行的取值**（返回 0 行改动）：

    - 建 ``web_provider_credential``（含「至多一个默认」的部分唯一索引）；
    - 重建 ``channel`` 与 ``agent_config``：它们的 ``web_access`` 列级 CHECK 要纳入 ``off``，
      而 SQLite 没有 ``ALTER TABLE … ADD CONSTRAINT``，只能走 :func:`_rebuild_table`
      （建新表 / 逐列复制 / ``DROP`` 原名表 / 改名回来）。既有行逐字保留：``NULL`` 仍是
      「未配置」（读路径回退到默认档 ``search``），不是回填。

    **重复执行是空操作**：表级 DDL 里已经点名 ``'off'`` 就跳过重建（与 ``17 → 18`` 同一条幂等纪律）。
    """
    _create_missing_tables(conn, WEB_CREDENTIAL_TABLES)
    for table in WEB_ACCESS_REBUILD_TABLES:
        stored = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if stored is None:
            continue
        if "'off'" in _normalize_sql(_without_comments(str(stored[0]))):
            continue
        _rebuild_table(conn, table)
    return 0


# ─── 链前形态步骤（#92） ─────────────────────────────────────────────────────
# 形态步骤的标签：在报告里与逐版步骤并列，但 ``from`` / ``to`` 都是源版本（它**不推进版本号**）。
SHAPE_STEP_LABEL = "形态步骤（channel 型作用域数据补齐：影子行 / 成员 / 已读 / 预算）"


def run_shape_step(conn: sqlite3.Connection, source_version: int) -> StepReport | None:
    """链前的**形态步骤**：有缺才搬，无缺不动（幂等：修好的库改动 0 行）。

    「四条搬迁共享既有列映射、影子行先落、四张表一起补齐」由 :func:`_step_3_to_4` 承担——它与逐版链
    的 ``3 → 4`` 是**同一段代码**、同一份 :data:`repair.CHANNEL_SCOPE_MOVES`（不写第二份）。
    ``run_shape_step`` 只加一层**形态判定**（:func:`repair.channel_scope_gaps`，只读）：健康库
    （数据本就在 ``target_*``）不触发、不产生任何改动。

    跑过形态步骤后随即施一次共享对齐：``--migrate`` 对「号已是本代码版本、只是 channel 型作用域数据
    还留在旧表名里」的库走的是**空链**（:func:`plan_steps` 为 ``()``），终态对齐只能由这里落。逐版链
    上的调用方多这一次对齐没有副作用（对齐幂等、只增不减）。

    ``source_version`` 只进报告（形态步骤不推进版本号），因此 :class:`StepReport` 的 ``from`` /
    ``to`` 都填它。
    """
    if not channel_scope_gaps(conn, _table_names(conn)):
        return None
    changed = _step_3_to_4(conn)
    _align_to_current_schema(conn)
    return StepReport(
        label=SHAPE_STEP_LABEL,
        from_version=source_version,
        to_version=source_version,
        changed_rows=changed,
    )


# ``20 → 21`` 的语义变更（#195 / ADR-0029）：``agent_config.auto_claim`` 缺省 0 → 1。
# 这不是纯增量：新插入的默认值变了，存量行也要按新语义一次性回填，因此本步同时做「重建 + 回填」。
# 号段说明：本票原先预留 20，但 #198（频道指针消息）在合并时已先落地 20，按 ADR-0015
# 「必须高于当前 main」的判据，本步顺延为 ``20 → 21``。
AUTO_CLAIM_TABLES: tuple[str, ...] = ("agent_config",)

# 存量行的一次性回填：只改 ``auto_claim`` 这一列（``WHERE auto_claim = 0`` 让重放是空操作）。
BACKFILL_AUTO_CLAIM_SQL = "UPDATE agent_config SET auto_claim = 1 WHERE auto_claim = 0"


def _step_20_to_21(conn: sqlite3.Connection) -> int:
    """自动认领默认开启（#195 / ADR-0029）：重建 ``agent_config`` + 存量行一次性回填。

    两件事，顺序固定：

    1. **重建** ``agent_config``（:func:`_rebuild_table`）：按**当前 ``SCHEMA``** 的 DDL 落表，
       于是升级库此后不写这一列的直接插入也拿到默认 1。重建逐列复制既有行，列集与数据不变；
    2. **回填**：``UPDATE … SET auto_claim = 1 WHERE auto_claim = 0``。只改这一列，不动
       ``updated_at``（这是语义迁移，不是一次配置写入），也不碰其它列。

    返回值是**实际由 0 变 1 的行数**（``UPDATE`` 的 ``rowcount``）：重放同一库时第二次是 0 行。
    这**不是**「把开关打开」的常规写路径——那仍然只有 ``PATCH /api/agents/{id}``；本步是一次显式的
    一次性升级回填（ADR-0029 的代价：升级会覆盖此前显式关闭的存量行）。

    索引 / 触发器随 ``DROP TABLE`` 一起消失，由执行器在本步之后施的
    :func:`_align_to_current_schema` 按当前 ``SCHEMA`` 补回来。
    """
    _create_missing_tables(conn, AUTO_CLAIM_TABLES)
    _rebuild_table(conn, "agent_config")
    return max(conn.execute(BACKFILL_AUTO_CLAIM_SQL).rowcount, 0)


def _step_22_to_23(conn: sqlite3.Connection) -> int:
    """运行时配置与可空的结束原因；既有轮次不回填。"""
    _add_missing_columns(conn, "round_outcome")
    _add_missing_columns(conn, "agent_config")
    return 0


# ``23 → 24`` 的历史动作是「旧 ``agentic`` 记忆档换成 ReMe」，但 ``24 → 25`` 已把这一列整个删除
# （ADR-0035）：对 v23 源库来说，该列的最终去向就是消失，映射不再有落点。保留本步只为链连续。
def _step_23_to_24(conn: sqlite3.Connection) -> int:
    """历史步骤：长期记忆换到 ReMe（ADR-0034）；由 ``24 → 25`` 取代，本步不改写任何行。

    ``memory_mode`` 不再出现在当前 ``SCHEMA`` 里，因此这里既不重建表、也不做 ``agentic → reme``
    映射（映射的目标列马上被删掉）。返回 0：本步不改变任何行，删列与其读数由
    :func:`_step_24_to_25` 承担。``_create_missing_tables`` 仍保留，保证缺表形态被建出。
    """
    _create_missing_tables(conn, ("agent_config",))
    return 0


def _step_24_to_25(conn: sqlite3.Connection) -> int:
    """长期记忆始终开启（ADR-0035）：重建 ``agent_config``，删掉 ``memory_mode`` 列。

    当前 ``SCHEMA`` 的 ``agent_config`` DDL 已不再声明该列，因此 :func:`_rebuild_table` 的
    「逐列复制」天然就是删列；其它列 / 行逐字保留。

    返回值 = 重建前 ``memory_mode IS NULL OR memory_mode <> 'reme'`` 的行数：这些成员的存量档位
    不是 ``reme``（v24 源里是 ``off`` / ``NULL``；v23 源里的旧档 ``agentic`` 也计入，因为它的语义
    随列删除一起退场）。列已不存在（终态库重放）⇒ 0。
    """
    _create_missing_tables(conn, ("agent_config",))
    if "memory_mode" not in _column_names(conn, "agent_config"):
        return 0
    disabled = int(
        conn.execute(
            "SELECT COUNT(*) FROM agent_config WHERE memory_mode IS NULL OR memory_mode <> 'reme'"
        ).fetchone()[0]
    )
    _rebuild_table(conn, "agent_config")
    return disabled


STEPS: tuple[MigrationStep, ...] = (
    MigrationStep(3, 4, "channel 型作用域数据搬迁（影子行 / 成员 / 已读 / 预算）", _step_3_to_4),
    MigrationStep(4, 5, "任务表与任务线程（task + message.task_ref + 触发器）", _step_4_to_5),
    MigrationStep(5, 6, "私信一对一唯一（dm_peer_member_id + 部分唯一索引）", _step_5_to_6),
    MigrationStep(6, 7, "工作区设置单行表（workspace_settings + id = 1 缺省行）", _step_6_to_7),
    MigrationStep(7, 8, "提醒表（reminder）", _step_7_to_8),
    MigrationStep(8, 9, "附件表 + 索引 + 触发器（attachment）", _step_8_to_9),
    MigrationStep(9, 10, "主题三档（workspace_settings.theme + 列级 CHECK）", _step_9_to_10),
    MigrationStep(10, 11, "附件触发器替换（先删 attachment_no_rebind，后建两条新触发器）", _step_10_to_11),
    MigrationStep(11, 12, "认领与 claimed_at（auto_claim / task.claimed_at）", _step_11_to_12),
    MigrationStep(12, 13, "提醒的到点形态（time 补日期 / fired_at 列）", _step_12_to_13),
    MigrationStep(
        13,
        14,
        "回应预算的配置落点（channel_budget_limit + target_budget.applied_limit）",
        _step_13_to_14,
    ),
    MigrationStep(
        14,
        15,
        "结构化沉默状态（round_outcome.status 的库级 CHECK 重建，纳入 silent）",
        _step_14_to_15,
    ),
    MigrationStep(15, 16, "agentscope-2 team/plan/sop expand", _step_15_to_16),
    MigrationStep(
        16,
        17,
        "agentscope-2 workspace/permission/mcp/skill expand",
        _step_16_to_17,
    ),
    MigrationStep(
        17,
        18,
        "agent web access config (remote_consent_scope 的 web 档 + 两个 web_access 列)",
        _step_17_to_18,
    ),
    MigrationStep(
        18,
        19,
        "docker container egress policy (channel.docker_egress)",
        _step_18_to_19,
    ),
    MigrationStep(
        19,
        20,
        "channel task pointer message (message.task_pointer + unique index + scope trigger)",
        _step_19_to_20,
    ),
    MigrationStep(
        20,
        21,
        "auto claim defaults on (agent_config rebuild + one-time backfill)",
        _step_20_to_21,
    ),
    MigrationStep(
        21,
        22,
        "web provider credentials + explicit off level (web_provider_credential, web_access CHECK)",
        _step_21_to_22,
    ),
    MigrationStep(22, 23, "agent runtime context/memory config + round finished_reason", _step_22_to_23),
    MigrationStep(
        23,
        24,
        "long-term memory to ReMe (superseded by 24 -> 25; chain continuity only)",
        _step_23_to_24,
    ),
    MigrationStep(
        24,
        25,
        "long-term memory is always on (drop agent_config.memory_mode)",
        _step_24_to_25,
    ),
)


def _assert_chain_is_continuous() -> None:
    """注册表自身的不变量：``from`` 严格 +1、唯一、连续覆盖支持区间。

    #95 会把「链连续性」做成一条守卫**测试**；这里是执行器启动时的内部不变量，防的是注册表被改坏
    之后跑出一条跳步 / 断链的迁移（那正是 ``9 → 11 → 12`` 那次争号要防的事）。
    """
    expected = MIN_SUPPORTED_SOURCE_VERSION
    for step in STEPS:
        if step.from_version != expected or step.to_version != expected + 1:
            raise RuntimeError(
                f"迁移链不连续：期望 {expected} → {expected + 1}，注册表给的是 "
                f"{step.from_version} → {step.to_version}"
            )
        expected = step.to_version
    if expected != SCHEMA_VERSION:
        raise RuntimeError(f"迁移链未覆盖到本代码的 Schema 版本 {SCHEMA_VERSION}：止于 {expected}")


_assert_chain_is_continuous()


def plan_steps(from_version: int, to_version: int = SCHEMA_VERSION) -> tuple[MigrationStep, ...]:
    """从 ``from_version`` 到 ``to_version`` 的**连续**步骤序列（不跳步、每步严格 +1）。

    纯函数、不碰库：它是 #93 预演报告里「将执行的步骤清单」与执行器的共同来源。两条不变量在这里
    也**再判一次**（注册表的不变量由 :func:`_assert_chain_is_continuous` 在导入时守）：

    - 每一步的 ``from`` 必须**接上**上一个 ``to``（防「某步 ``from`` 落后号」，历史的 ``9 → 11``）；
    - 每一步必须**严格 +1**（防一个从 ``9`` 直接跳到 ``11`` 的步骤把 10 那一代悄悄略过）。

    后者是导入期不变量与这里的分工差异：导入期检查只在模块加载时跑一遍，而预演 / 真跑是在**运行时**
    规划，因此「链断了」必须在这里也能被暴露成 ``5``，而不是被「大概齐」地执行过去。
    """
    if from_version == to_version:
        return ()
    steps = tuple(step for step in STEPS if from_version <= step.from_version < to_version)
    expected = from_version
    for step in steps:
        if step.from_version != expected:
            raise MigrationError(
                f"迁移链不连续：从 {from_version} 走到 {to_version} 时，期望 {expected} 的下一步，"
                f"注册表给的是 {step.from_version} → {step.to_version}"
            )
        if step.to_version != step.from_version + 1:
            raise MigrationError(
                f"迁移链不连续：注册表里的 {step.from_version} → {step.to_version} 不是严格 +1 的一步"
                f"（它跳过了 {step.from_version + 1}）。迁移链要求每步严格 +1（规格 D5）。"
            )
        expected = step.to_version
    if expected != to_version:
        raise MigrationError(f"迁移链没有覆盖到 {to_version}：止于 {expected}")
    return steps


# ─── 执行器 ──────────────────────────────────────────────────────────────────
def run_chain(conn: sqlite3.Connection, from_version: int, to_version: int = SCHEMA_VERSION) -> ChainResult:
    """对一个**已经打开的库**先跑形态步骤、再逐步走链（不跳步），每步之后施一次对齐兜底。

    形态步骤是**不可省的前置**（规格 D5 / D11）：它不依赖版本号，处置「号已是新一代、channel 型作用域
    数据还留在旧表名里」的半改名库——按版本号走链会**跳过** ``3 → 4``，只有它能搬回来。健康库上它是
    无操作（改动 0 行、不进报告）。

    **事务边界不在这里**：调用方负责 ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``（单事务收口，
    规格 D8）。它也不管备份与审计——那是 :func:`migrate_data_dir` 的编排。这条缝就是 #93 的挂点：
    在**库副本**上跑同一条链、拿同一份 :class:`ChainResult`（含形态步骤那一项）。
    """
    reports: list[StepReport] = []
    shape = run_shape_step(conn, from_version)
    if shape is not None:
        reports.append(shape)
    for step in plan_steps(from_version, to_version):
        changed = step.apply(conn)
        _align_to_current_schema(conn)
        reports.append(
            StepReport(
                label=step.label,
                from_version=step.from_version,
                to_version=step.to_version,
                changed_rows=changed,
            )
        )
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        table, rowid, parent = (str(violations[0][0]), str(violations[0][1]), str(violations[0][2]))
        raise StepError(
            f"迁移后外键校验失败：表 {table} 的 rowid={rowid} 指向不存在的 {parent}；本次迁移已拒绝继续"
        )
    return ChainResult(from_version=from_version, to_version=to_version, steps=tuple(reports))


# ─── 备份与审计 ──────────────────────────────────────────────────────────────
def backup_directory_name(
    data_dir: Path | str, from_version: int, to_version: int, label: str | None = None
) -> str:
    """``<数据目录>-backup-<from>-to-<to>-<UTC 时间戳>``（规格 D8 / S6）。

    ``label`` 非空时改用 ``<数据目录>-backup-<label>-<UTC 时间戳>``：技能迁移不推进 schema
    版本，把备份与 schema 迁移区分开（#274）。
    """
    data_dir = Path(data_dir)
    stamp = datetime.now(UTC).strftime(BACKUP_TIMESTAMP_FORMAT)
    if label:
        return f"{data_dir.name}{BACKUP_INFIX}{label}-{stamp}"
    return f"{data_dir.name}{BACKUP_INFIX}{from_version}-to-{to_version}-{stamp}"


def _backup_data_dir(data_dir: Path, from_version: int, to_version: int, label: str | None = None) -> Path:
    """把**整个数据目录**（含 ``attachments/`` 字节）复制到同级；失败即拒绝迁移。"""
    destination = data_dir.parent / backup_directory_name(data_dir, from_version, to_version, label)
    try:
        shutil.copytree(data_dir, destination)
    except (OSError, shutil.Error) as exc:
        raise BackupError(f"迁移前的整目录备份失败，已拒绝迁移（库文件一个字节都没动）：{exc}") from exc
    return destination


def append_audit_record(
    data_dir: Path,
    from_version: int,
    to_version: int,
    started_at: str,
    finished_at: str,
    result: str,
    backup_path: Path,
    error: str | None = None,
) -> Path:
    """往数据目录内的台账追加一条记录（append-only，不回改历史行）。"""
    ledger_path = data_dir / MIGRATION_LEDGER_FILE_NAME
    record: dict[str, object] = {
        "from": from_version,
        "to": to_version,
        "started_at": started_at,
        "finished_at": finished_at,
        "result": result,
        "backup_path": str(backup_path),
    }
    if error is not None:
        record["error"] = error
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return ledger_path


# ─── 目录级编排（CLI 调它） ─────────────────────────────────────────────────
def migrate_data_dir(data_dir: Path | str, from_version: int) -> MigrationReport:
    """备份 → 单事务跑链 → 钉版本 → 落审计 → 报告。

    **备份失败即拒绝迁移**（:class:`BackupError`，库一个字节不改）；链上任何失败都 ``ROLLBACK``
    （:class:`MigrationFailed`），库停在 ``from_version``、仍能被原版本代码打开，不存在
    「新表建了但数据没搬」的中间态。``BEGIN IMMEDIATE`` 本身也在那个 ``try`` 里：库被别的进程占住
    时它与链上失败一样是「动手失败、已回滚」，给同一个退出码 ``5``，而不是一个未捕获的堆栈。
    """
    data_dir = Path(data_dir)
    db_path = data_dir / DATABASE_FILE_NAME
    started_at = utc_now()
    backup_path = _backup_data_dir(data_dir, from_version, SCHEMA_VERSION)

    conn = connect(db_path)
    try:
        try:
            # 重建被引用的父表时，删除父行的延迟违例在 COMMIT 仍会失败；迁移连接因此关闭外键执行，
            # 由链末的 ``PRAGMA foreign_key_check`` 证明数据完整（schema 里的 FK 定义不变）。
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            chain = run_chain(conn, from_version)
            conn.execute(STAMP_SCHEMA_VERSION_SQL, (str(SCHEMA_VERSION),))
            conn.execute("COMMIT")
        except Exception as exc:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            with suppress(OSError):
                append_audit_record(
                    data_dir,
                    from_version,
                    SCHEMA_VERSION,
                    started_at,
                    utc_now(),
                    "failed",
                    backup_path,
                    error=str(exc),
                )
            raise MigrationFailed(
                f"迁移失败并已回滚：库仍停在版本 {from_version}，未被改动。原因：{exc}"
            ) from exc
    finally:
        conn.close()

    finished_at = utc_now()
    ledger_path = append_audit_record(
        data_dir, from_version, SCHEMA_VERSION, started_at, finished_at, "success", backup_path
    )
    return MigrationReport(
        db_path=db_path,
        backup_path=backup_path,
        ledger_path=ledger_path,
        chain=chain,
        started_at=started_at,
        finished_at=finished_at,
    )


def _render_step_lines(chain: ChainResult) -> list[str]:
    """步骤表的渲染：真跑（:func:`render_report`）与预演**共用同一份**，于是两份报告同形。

    形态步骤（#92）**不推进版本号**，``from`` / ``to`` 只是源版本的回声；链上的逐版步骤一律
    ``from + 1 == to``（:func:`_assert_chain_is_continuous` 与 :func:`plan_steps` 守着），所以那个
    分支不会落到版本步上——打出一个「4 → 4」会把不推进说成推进。
    """
    lines: list[str] = []
    for step in chain.steps:
        if step.from_version == step.to_version:
            lines.append(f"  {step.label}（不推进版本号）  改动 {step.changed_rows} 行")
        else:
            lines.append(
                f"  {step.from_version} → {step.to_version}  {step.label}  改动 {step.changed_rows} 行"
            )
    return lines


def _time_narrowing_lines(chain: ChainResult) -> list[str]:
    """``12 → 13`` 那一步的**时间语义一次性收窄**说明（只在链真的含这一步时给出）。

    为什么必须在报告里（ADR-0017 §五 / 规格 C13 判据 2）：这一步不丢数据行，但把存量行「每天该
    钟点」的含义改成了「迁移执行当日该钟点」——**改变了行的含义**。只给步数统计会让读者以为这是
    一次无害的形变；因此这句要能直接读到（``render_report`` 与预演报告共用，两处同口径）。

    条件句的理由与 :func:`_render_step_lines` 同源：形态步骤那一档链可能是空的（版本已是 13、只是
    作用域数据还留在旧表名下），那时打这句会凭空声称一件没发生的事。
    """
    if not any(
        step.from_version == REMINDER_TIME_STEP_FROM and step.to_version == REMINDER_TIME_STEP_TO
        for step in chain.steps
    ):
        return []
    return [
        "",
        "【时间语义的一次性收窄】存量提醒的到期语义从「每天该钟点」收窄为「迁移执行当日该钟点」"
        "（本机本地时间口径）：原来的「每天 9 点」现在是「迁移那天 9 点」。",
        "它不丢数据行，但改变了行的含义——这是一次语义收窄，不是一次普通的形变（ADR-0017 §五）。",
    ]


def render_report(report: MigrationReport) -> str:
    """把一次迁移写成人类 / 脚本都读得懂的报告（stdout 用；D8）。"""
    lines = [
        f"迁移：{report.db_path}",
        f"源版本：{report.chain.from_version} → 目标版本：{report.chain.to_version}",
        f"本次备份：{report.backup_path}",
        "",
        "实际执行的步骤（每步改动的数据行数）：",
    ]
    lines.extend(_render_step_lines(report.chain))
    lines.extend(_time_narrowing_lines(report.chain))
    lines.extend(
        [
            "",
            f"迁移完成：schema_version = {report.chain.to_version}；本次共改动 {report.chain.changed_rows} 行。",
            f"审计记录：{report.ledger_path}",
        ]
    )
    return "\n".join(lines)


# ─── 只读预演（#93） ─────────────────────────────────────────────────────────
# 预演副本落在**数据目录内**的隐藏临时子目录里（规格 D10）：副本 = ``alibot.db`` **连同旁边的**
# ``-wal`` / ``-shm``。只复制主文件会读到不完整状态——已提交但还没 checkpoint 的行都在 WAL 里，
# 于是报告会把「完整状态」少算几行。
DRY_RUN_COPY_PREFIX = ".alibot-dry-run-"

# 库的边文件后缀（顺序固定：主文件 → 预写日志 → 共享内存索引）。
LIBRARY_SIDECAR_SUFFIXES: tuple[str, ...] = ("", "-wal", "-shm")


def sha256_file(path: Path) -> str:
    """文件的 sha256（分块读：库可能是几百 MB，不整份进内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DryRunReport:
    """一次只读预演的全部可观察读数（stdout 报告的数据源）。

    副本有三种状态，报告必须**如实**区分（D10 的「数据目录里不留临时副本」是可观察事实，不能在
    报告里说成已经做到）：``copy_path is None`` = 连副本都没建起来；``copy_removed`` = 已经删掉；
    路径在而 ``copy_removed`` 为假 = 没能删掉（调用方另有 stderr 正文说明）。
    """

    db_path: Path
    copy_path: Path | None
    copy_removed: bool
    digest_before: str
    digest_after: str
    chain: ChainResult | None

    @property
    def unchanged(self) -> bool:
        """原库文件 sha256 的前后两个读数是否一致（预演的举证责任）。"""
        return self.digest_before == self.digest_after


class StagedLibraryCopy:
    """数据目录**内**的库临时副本（``alibot.db`` 连同 ``-wal`` / ``-shm``），用完即删。

    预演的隔离面（规格 D10 / S5）：

    - 副本落在数据目录内的隐藏临时子目录里，因此数据目录里不会出现「落在别处的副本」；
    - 只用 :func:`shutil.copy2` 复制**库文件与它的边文件**（附件字节在 ``attachments/`` 下、不进单
      文件库，预演也不碰它们）；
    - :meth:`__exit__` **无条件**删除它——预演成功、失败、抛异常都删；删不干净就明确报错，**并且
      不让报告说「已删除」**（:attr:`copy_removed` 保持为假）；
    - 进入时与退出时各取一次**原库文件**的 sha256，供报告打出前后两个读数。

    预演**从不用 SQLite 打开原库**：版本闸门、形态判定与整条链全部施在副本上（原库只被读字节），
    因此「原库零字节改动」是构造性的，而不是「打开之后希望它没写」。
    """

    def __init__(self, data_dir: Path | str) -> None:
        self.source_dir = Path(data_dir)
        self.source_db_path = self.source_dir / DATABASE_FILE_NAME
        self.copy_dir: Path | None = None
        self.copy_removed = False
        self.digest_before = ""
        self.digest_after = ""

    def __enter__(self) -> Path:
        """建副本；返回副本目录（里面就是 ``alibot.db`` 与它的边文件）。"""
        self.digest_before = sha256_file(self.source_db_path)
        try:
            self.copy_dir = Path(tempfile.mkdtemp(dir=self.source_dir, prefix=DRY_RUN_COPY_PREFIX))
        except OSError as exc:
            self._measure_after()
            raise DryRunError(f"预演没法在数据目录内建临时副本：{exc}。原库文件一个字节都没动。") from exc
        try:
            for suffix in LIBRARY_SIDECAR_SUFFIXES:
                source = self.source_db_path.with_name(self.source_db_path.name + suffix)
                if source.exists():
                    shutil.copy2(source, self.copy_dir / source.name)
        except OSError as exc:
            # ``__exit__`` 不会被调用（`__enter__` 就失败了），所以在这里把半份副本清掉。
            shutil.rmtree(self.copy_dir, ignore_errors=True)
            self.copy_dir = None
            self._measure_after()
            raise DryRunError(
                f"预演没法把库文件（含 ``-wal`` / ``-shm``）复制进数据目录内的临时副本：{exc}。"
                f"原库文件一个字节都没动，数据目录里也不留半个副本。"
            ) from exc
        return self.copy_dir

    def __exit__(self, *_exc_info: object) -> None:
        try:
            self.cleanup()
        finally:
            self._measure_after()

    def cleanup(self) -> None:
        """删除临时副本（成功与失败都删）；删不干净就报错，且**不**把「已删除」写进报告。

        ``copy_removed`` 只在真的删掉之后才置真——报告里那句「已删除」是给用户看的证据，不能是一条
        与 stderr 自相矛盾的空话。
        """
        if self.copy_dir is None:
            return
        shutil.rmtree(self.copy_dir, ignore_errors=True)
        if self.copy_dir.exists():
            self.copy_removed = False
            raise DryRunError(
                f"预演结束但数据目录里的临时副本没删干净：{self.copy_dir}（请手动删除该目录）。"
                f"原库文件一个字节都没动。"
            )
        self.copy_removed = True

    @property
    def has_readings(self) -> bool:
        """原库 sha256 的前后两个读数是否都在（``False`` = 连库文件都没量成）。"""
        return bool(self.digest_before and self.digest_after)

    def report(self, chain: ChainResult | None = None) -> DryRunReport | None:
        """预演的可观察读数；``None`` = 连前后读数都没有（库文件都量不成），没有证据可打。

        **必须在 with 块之外调用**（那时副本的去向与原库的前后读数都已落定）。
        """
        if not self.has_readings:
            return None
        return DryRunReport(
            db_path=self.source_db_path,
            copy_path=self.copy_dir,
            copy_removed=self.copy_removed,
            digest_before=self.digest_before,
            digest_after=self.digest_after,
            chain=chain,
        )

    def untouched_note(self) -> str:
        """一行「原库一个字节都没动」的正文（预演跑不通时打给 stderr 的那句）。"""
        if not self.has_readings:
            return f"预演未改动原库：{self.source_db_path}（预演没有对它做任何写操作）。"
        return (
            f"预演未改动原库：{self.source_db_path} 的 sha256 前后一致"
            f"（{self.digest_before} → {self.digest_after}）；原库一个字节都没动。"
        )

    def _measure_after(self) -> None:
        """取原库文件 sha256 的「后」读数（副本没建起来时也要取：那是真实测得的读数，不是推断）。"""
        self.digest_after = sha256_file(self.source_db_path)


def dry_run_chain(copy_dir: Path | str, from_version: int) -> ChainResult:
    """在**副本**上跑与真跑同一条链（形态步骤 + 逐版链），随后 ``ROLLBACK``。

    副本用完即删，因此这里既不钉版本号、也不落审计：预演的全部产出就是这份 :class:`ChainResult`
    （步骤清单 + 每步将改动的行数）。

    链跑不通（含计划断链）⇒ 抛 :class:`MigrationFailed`，正文按 **#93 的人定裁定**写明「预演发现这条
    链在副本上跑不通；原库未改动」——预演**没有可回滚之物**（它连原库都没打开，写操作只落在副本上），
    所以这里不沿用真跑那句「迁移失败并已回滚」，也不用「已回滚」这种会误导的措辞。调用方把它映射为
    ``5``。
    """
    conn: sqlite3.Connection | None = None
    try:
        try:
            conn = connect(Path(copy_dir) / DATABASE_FILE_NAME)
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                return run_chain(conn, from_version)
            finally:
                with suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
        except Exception as exc:
            # ``connect`` 也在里面：副本打不开同样是「这条链在副本上跑不通」，一样给 5，不逃逸成堆栈。
            raise MigrationFailed(
                f"预演发现这条链在副本上跑不通；原库未改动（预演没有可回滚之物，写操作只落在副本上）。"
                f"原因：{exc}"
            ) from exc
    finally:
        if conn is not None:
            conn.close()


def _dry_run_copy_line(report: DryRunReport) -> str:
    """副本那一行的正文：副本的三种去向各说各的，**不把「没做」说成「做到了」**。"""
    safety = "不建备份、不写审计台账、不改库里的 Schema 版本"
    if report.copy_path is None:
        return f"临时副本：没能建起来（预演没跑）；{safety}，原库未改动。"
    if report.copy_removed:
        return f"临时副本：{report.copy_path} 已删除；数据目录内不留副本、{safety}。"
    return f"临时副本：{report.copy_path} 没能删掉（数据目录里还留着它，见 stderr）；{safety}。"


def _dry_run_proof_lines(report: DryRunReport) -> list[str]:
    """预演报告的共同尾部：原库 sha256 的前后两个读数 + 副本去向 + 无任何写副作用。"""
    consistency = "前后读数一致" if report.unchanged else "前后读数不一致（这是缺陷，请上报）"
    return [
        "",
        f"原库文件 sha256（预演前）：{report.digest_before}",
        f"原库文件 sha256（预演后）：{report.digest_after}",
        f"原库零字节改动：{consistency}。",
        _dry_run_copy_line(report),
    ]


def render_dry_run_report(report: DryRunReport) -> str:
    """把一次跑通的预演写成与真跑**同形**的报告（步骤清单 + 每步将改动的行数 + sha256 前后读数）。"""
    chain = report.chain
    if chain is None:
        raise ValueError("预演报告要有一次跑通的链结果；没跑完时用 render_dry_run_untouched")
    lines = [
        f"预演（只读，在数据目录内的临时副本上跑完整迁移路径）：{report.db_path}",
        f"源版本：{chain.from_version} → 目标版本：{chain.to_version}",
        "",
        "将执行的步骤（每步将改动的数据行数）：",
    ]
    lines.extend(_render_step_lines(chain))
    lines.extend(_time_narrowing_lines(chain))
    lines.extend(_dry_run_proof_lines(report))
    lines.append(f"预演结论：这条链在副本上跑通了；本次共将改动 {chain.changed_rows} 行。")
    return "\n".join(lines)


def render_dry_run_untouched(report: DryRunReport) -> str:
    """预演**没**跑成（库不被接受 / 形态不对 / 链在副本上跑不通 / 无事可做）时的正文。

    全部内容就是「原库一个字节都没动」这份读数：预演什么都没做，这本身就是要给用户看的事实。
    """
    lines = [f"预演未改动原库：{report.db_path}"]
    lines.extend(_dry_run_proof_lines(report))
    return "\n".join(lines)


# ─── 技能字节迁移（#274 / ADR-0031 §二） ────────────────────────────────────
# 这是**显式**的一次性搬迁：项目层旧字节 ``<workspace_root>/.alibot/skills/<partition>/<name>/``
# → 成员家目录 ``<全局根>/agents/<member>/skills/<name>/``。它不在启动路径上、不是 HTTP 端点、
# 不改 schema、不删源字节：只复制、只重算 hash、只更新 ``skill`` 行。
#
# 三种处置（逐条计划里各占一行，报告里写明原因）：
# - ``create``：目标不存在 → 复制并（必要时）建/更新行；
# - ``skip``：目标已存在且**内容相同** → 不覆盖；行有差异时按需对齐；
# - ``refuse``：目标已存在但内容不同（或两个源指向同一目标且内容不同）→ **拒绝执行**，
#   要求人类先手动裁定。本命令不提供 ``--force``。
SKILL_MIGRATION_MANIFEST_PREFIX = "skill-migration-"
# 频道级字节不迁移：报告里只给数量与这一句建议（票面行为 1）。
SKILL_MIGRATION_CHANNEL_NOTICE = "频道级已弃用，请按成员安装"
SKILL_ACTION_CREATE = "create"
SKILL_ACTION_SKIP = "skip"
SKILL_ACTION_REFUSE = "refuse"
_SKILL_ACTION_LABELS = {
    SKILL_ACTION_CREATE: "新建",
    SKILL_ACTION_SKIP: "跳过",
    SKILL_ACTION_REFUSE: "拒绝",
}
# 迁移清单 / 报告里指向旧项目层技能目录的相对路径（与旧安装器的落点逐字一致）。
LEGACY_SKILLS_PARTS: tuple[str, ...] = (ALIBOT_DIR, SKILLS_DIR)


class SkillMigrationError(MigrationError):
    """技能迁移**没能开始或没能完成**：库形态不对、目标冲突、复制/写库失败。"""


class SkillMigrationFailed(SkillMigrationError):
    """跑到一半失败：带上**已完成**的条目，调用方照实打印，已复制的文件保留、不回滚删除。"""

    def __init__(self, message: str, result: SkillApplyResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class SkillMigrationEntry:
    """一条逐条计划：一个 ``<partition>/<name>/`` 源树与它的目标、动作、原因与规模读数。"""

    source: Path
    target: Path
    member_id: str
    channel_id: str
    name: str
    description: str
    content_hash: str
    file_count: int
    byte_count: int
    action: str
    reason: str
    row_id: str | None
    row_changes: tuple[str, ...]

    @property
    def needs_write(self) -> bool:
        """这一条是否需要动文件或库行（已就位的条目重复 apply 时是空操作）。"""
        return self.action == SKILL_ACTION_CREATE or bool(self.row_changes)


@dataclass(frozen=True)
class ChannelLevelSkills:
    """一个频道级分区：只进报告（数量 + 建议），不复制、不入库。"""

    channel_id: str
    source: Path
    names: tuple[str, ...]


@dataclass(frozen=True)
class SkillMigrationSkip:
    """没能判定 / 不能迁移的源路径与一句原因（不猜、不跳过说明）。"""

    source: Path
    reason: str


@dataclass(frozen=True)
class SkillMigrationPlan:
    """一次迁移的**只读**计划：逐条动作、频道级读数与跳过读数。"""

    data_dir: Path
    global_root: str
    entries: tuple[SkillMigrationEntry, ...]
    channel_level: tuple[ChannelLevelSkills, ...]
    skipped: tuple[SkillMigrationSkip, ...]

    @property
    def refused(self) -> tuple[SkillMigrationEntry, ...]:
        return tuple(entry for entry in self.entries if entry.action == SKILL_ACTION_REFUSE)

    @property
    def pending(self) -> tuple[SkillMigrationEntry, ...]:
        """本次 apply 真的要写的条目（已就位 → 空）。"""
        return tuple(entry for entry in self.entries if entry.needs_write)


@dataclass(frozen=True)
class SkillApplyEntry:
    """一条 apply 的完成读数：字节去向 + 库行动作 + 实际写入的 hash。"""

    entry: SkillMigrationEntry
    outcome: str
    row_action: str
    content_hash: str


@dataclass(frozen=True)
class SkillApplyResult:
    """一次 apply 的完成读数（失败时也带着已完成的部分）。"""

    backup_path: Path | None
    manifest_path: Path | None
    completed: tuple[SkillApplyEntry, ...]

    @property
    def removable_sources(self) -> tuple[Path, ...]:
        """已经确认复制成功的源路径（只打印，不删除）。"""
        return tuple(item.entry.source for item in self.completed if item.outcome == "copied")


def _existing_skill_tree(
    global_root: str, member_id: str, name: str
) -> tuple[str, Path | None, dict[str, object] | None]:
    """目标路径的现状：``absent`` / ``present``（含读到的树）/ ``conflict``（不可读或越界）。

    根检查与装配层**同一口径**：:func:`workspace.resolve_global_read_path` 以全局根自己的
    ``realpath`` 为根，拒绝根外 symlink 祖先与根外目标。
    """
    raw = Path(skills_partition(global_root, member_id)) / name
    resolved = resolve_global_read_path(
        global_root, AGENTS_DIR, safe_partition_id(member_id), SKILLS_DIR, name
    )
    if resolved is None:
        return ("conflict" if os.path.lexists(raw) else "absent"), raw, None
    root = Path(resolved)
    if not root.is_dir():
        return "conflict", root, None
    try:
        return "present", root, read_skill_tree(root)
    except (SkillValidationError, OSError):
        return "conflict", root, None


def _skill_row_changes(row: sqlite3.Row | None, source: Path, content_hash: str) -> tuple[str, ...]:
    """这条源在 ``skill`` 行上要改哪些列（空 = 已对齐，重复 apply 不再写）。

    ``source_path`` 记的是**来源**（旧项目层路径），不是目标家目录路径，所以重复 apply
    比较的也是来源。
    """
    if row is None:
        return ("insert",)
    changes: list[str] = []
    if str(row["source_path"]) != str(source):
        changes.append("source_path")
    if str(row["content_hash"]) != content_hash:
        changes.append("content_hash")
    if changes:
        changes.append("updated_at")
    return tuple(changes)


def _plan_entry(
    *,
    global_root: str,
    source: Path,
    target: Path,
    member_id: str,
    channel_id: str,
    tree: Mapping[str, object],
    row: sqlite3.Row | None,
) -> SkillMigrationEntry:
    """按「目标现状 + 库行现状」给出逐条动作；内容不同一律 ``refuse``（不覆盖）。"""
    name = str(tree["name"])
    content_hash = str(tree["content_hash"])
    state, resolved_target, existing = _existing_skill_tree(global_root, member_id, name)
    resolved = resolved_target if resolved_target is not None else target
    if state == "absent":
        action, reason = SKILL_ACTION_CREATE, "目标不存在，将复制字节"
    elif state == "present" and existing is not None and str(existing["content_hash"]) == content_hash:
        action = SKILL_ACTION_SKIP
        reason = "目标已存在且内容相同，不覆盖"
    elif state == "present":
        action = SKILL_ACTION_REFUSE
        reason = "目标已存在但内容不同，需人工裁定后再迁移（本命令不覆盖、不提供 --force）"
    else:
        action = SKILL_ACTION_REFUSE
        reason = "目标路径不可读或越出全局根，需人工裁定"
    changes = () if action == SKILL_ACTION_REFUSE else _skill_row_changes(row, source, content_hash)
    if changes == ("insert",):
        reason = f"{reason}；库行不存在，将按成员作用域新建"
    elif changes:
        reason = f"{reason}；将同步库行（{', '.join(changes)}）"
    return SkillMigrationEntry(
        source=source,
        target=resolved,
        member_id=member_id,
        channel_id=channel_id,
        name=name,
        description=str(tree["description"]),
        content_hash=content_hash,
        file_count=int(str(tree["file_count"])),
        byte_count=int(str(tree["bytes"])),
        action=action,
        reason=reason,
        row_id=None if row is None else str(row["id"]),
        row_changes=changes,
    )


def plan_skill_migration(data_dir: Path | str, global_root: str | None = None) -> SkillMigrationPlan:
    """扫描所有频道项目根的旧技能分区，产出**只读**的逐条计划（dry-run 的全部实现）。"""
    data_dir_path = Path(data_dir)
    db_path = data_dir_path / DATABASE_FILE_NAME
    if not db_path.is_file():
        raise SkillMigrationError(f"数据目录里没有 {DATABASE_FILE_NAME}：{data_dir_path}")
    root = os.path.realpath(global_root if global_root is not None else global_home_root())

    conn = connect(db_path)
    try:
        tables = _table_names(conn)
        missing = {"channel", "member", "skill"} - tables
        if missing:
            raise SkillMigrationError(
                "库里缺少技能迁移需要的表："
                + "、".join(sorted(missing))
                + "；请先用 `python -m alibot --migrate` 把库推进到本代码的 schema 版本。"
            )
        members = {
            safe_partition_id(str(row["id"])): row
            for row in conn.execute("SELECT id, kind, deleted_at FROM member")
        }
        all_channels = {
            safe_partition_id(str(row["id"])): row
            for row in conn.execute("SELECT id, workspace_root FROM channel ORDER BY id")
        }
        channels = {
            key: row
            for key, row in all_channels.items()
            if row["workspace_root"] is not None and str(row["workspace_root"]).strip() != ""
        }
        rows = {
            (str(row["scope_id"]), str(row["name"])): row
            for row in conn.execute("SELECT * FROM skill WHERE scope_kind = 'agent'")
        }

        entries: list[SkillMigrationEntry] = []
        channel_level: list[ChannelLevelSkills] = []
        skipped: list[SkillMigrationSkip] = []
        planned: dict[str, int] = {}
        seen_sources: set[str] = set()
        seen_roots: set[str] = set()

        for channel in channels.values():
            channel_id = str(channel["id"])
            try:
                workspace_root = validate_workspace_root(str(channel["workspace_root"]))
                legacy_root = Path(resolve_in_root(os.path.join(*LEGACY_SKILLS_PARTS), workspace_root))
            except (WorkspaceConfigError, WorkspaceRootViolation) as error:
                skipped.append(
                    SkillMigrationSkip(
                        Path(str(channel["workspace_root"])),
                        f"频道 {channel_id} 的项目根不可用：{error}",
                    )
                )
                continue
            if not legacy_root.is_dir() or str(legacy_root) in seen_roots:
                continue
            seen_roots.add(str(legacy_root))

            for partition in sorted(legacy_root.iterdir(), key=lambda item: item.name):
                if partition.is_symlink() or not partition.is_dir():
                    continue
                member = members.get(partition.name)
                channel_partition = all_channels.get(partition.name)
                if member is not None and channel_partition is not None:
                    skipped.append(
                        SkillMigrationSkip(partition, "分区同时命中成员与频道，无法判定归属，未迁移")
                    )
                    continue
                if channel_partition is not None:
                    names = tuple(
                        sorted(
                            child.name
                            for child in partition.iterdir()
                            if child.is_dir() and not child.is_symlink()
                        )
                    )
                    channel_level.append(
                        ChannelLevelSkills(
                            channel_id=str(channel_partition["id"]), source=partition, names=names
                        )
                    )
                    continue
                if member is None:
                    skipped.append(
                        SkillMigrationSkip(partition, "分区既不是成员也不是频道，无法判定归属，未迁移")
                    )
                    continue
                if str(member["kind"]) != "agent" or member["deleted_at"] is not None:
                    skipped.append(SkillMigrationSkip(partition, "分区对应的成员不是未删除的 Agent，未迁移"))
                    continue

                member_id = str(member["id"])
                for skill_dir in sorted(partition.iterdir(), key=lambda item: item.name):
                    if skill_dir.is_symlink() or not skill_dir.is_dir():
                        continue
                    source_key = os.path.realpath(skill_dir)
                    if source_key in seen_sources:
                        skipped.append(
                            SkillMigrationSkip(skill_dir, "同一源路径被多个频道根重复扫描，已去重")
                        )
                        continue
                    seen_sources.add(source_key)
                    try:
                        tree = read_skill_tree(skill_dir)
                    except (SkillValidationError, OSError) as error:
                        skipped.append(SkillMigrationSkip(skill_dir, f"技能树校验不通过：{error}"))
                        continue
                    if str(tree["name"]) != skill_dir.name:
                        skipped.append(
                            SkillMigrationSkip(
                                skill_dir,
                                f"清单名 {tree['name']!r} 与目录名 {skill_dir.name!r} 不一致，未迁移",
                            )
                        )
                        continue
                    target = Path(skills_partition(root, member_id)) / str(tree["name"])
                    entry = _plan_entry(
                        global_root=root,
                        source=skill_dir,
                        target=target,
                        member_id=member_id,
                        channel_id=channel_id,
                        tree=tree,
                        row=rows.get((member_id, str(tree["name"]))),
                    )
                    prior_index = planned.get(str(entry.target))
                    if prior_index is not None:
                        prior = entries[prior_index]
                        if prior.content_hash == entry.content_hash:
                            entry = replace(
                                entry,
                                action=SKILL_ACTION_SKIP,
                                reason="另一个源树已指向同一目标且内容相同，目标只写一次",
                                row_changes=(),
                            )
                        else:
                            entries[prior_index] = replace(
                                prior,
                                action=SKILL_ACTION_REFUSE,
                                reason="多个源树指向同一目标但内容不同，需人工裁定",
                                row_changes=(),
                            )
                            entry = replace(
                                entry,
                                action=SKILL_ACTION_REFUSE,
                                reason="多个源树指向同一目标但内容不同，需人工裁定",
                                row_changes=(),
                            )
                    # 目标 → 第一个**要写**的条目：后续同目标的源只跟它比（skip 不登记，
                    # 否则第三个内容不同的源就找不到真正的创建者，冲突会被漏判）。
                    if entry.action == SKILL_ACTION_CREATE and str(entry.target) not in planned:
                        planned[str(entry.target)] = len(entries)
                    entries.append(entry)
    finally:
        conn.close()

    return SkillMigrationPlan(
        data_dir=data_dir_path,
        global_root=root,
        entries=tuple(entries),
        channel_level=tuple(channel_level),
        skipped=tuple(skipped),
    )


def _manifest_payload(plan: SkillMigrationPlan) -> dict[str, object]:
    """迁移清单 JSON 的数据面：逐条计划 + 频道级读数 + 跳过读数（审计用，不回写库）。"""
    return {
        "kind": "skill-migration",
        "created_at": utc_now(),
        "data_dir": str(plan.data_dir),
        "global_root": plan.global_root,
        "entries": [
            {
                "source": str(entry.source),
                "target": str(entry.target),
                "member_id": entry.member_id,
                "channel_id": entry.channel_id,
                "name": entry.name,
                "action": entry.action,
                "reason": entry.reason,
                "content_hash": entry.content_hash,
                "file_count": entry.file_count,
                "bytes": entry.byte_count,
                "row_id": entry.row_id,
            }
            for entry in plan.entries
        ],
        "channel_level": [
            {
                "channel_id": item.channel_id,
                "source": str(item.source),
                "names": list(item.names),
                "notice": SKILL_MIGRATION_CHANNEL_NOTICE,
            }
            for item in plan.channel_level
        ],
        "skipped": [{"source": str(item.source), "reason": item.reason} for item in plan.skipped],
    }


def _write_skill_manifest(plan: SkillMigrationPlan) -> Path:
    """先落清单再复制：清单是本次迁移的审计正本（数据目录内，UTC 时间戳）。"""
    stamp = datetime.now(UTC).strftime(BACKUP_TIMESTAMP_FORMAT)
    path = plan.data_dir / f"{SKILL_MIGRATION_MANIFEST_PREFIX}{stamp}.json"
    suffix = 1
    while path.exists():
        suffix += 1
        path = plan.data_dir / f"{SKILL_MIGRATION_MANIFEST_PREFIX}{stamp}-{suffix}.json"
    try:
        path.write_text(
            json.dumps(_manifest_payload(plan), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise SkillMigrationError(f"迁移清单写入失败，已停止（未复制任何文件）：{error}") from error
    return path


def _upsert_skill_row(conn: sqlite3.Connection, entry: SkillMigrationEntry, content_hash: str) -> str:
    """按成员作用域更新 / 新建 ``skill`` 行；返回值是 ``created`` / ``updated`` / ``unchanged``。

    ``source_path`` 写**来源**（旧项目层源路径），不写目标家目录路径——票面要求新建行也标注
    来源；目标落点由字节位置自证，重复 apply 靠来源 + hash 判定幂等。
    """
    now = utc_now()
    row = conn.execute(
        "SELECT * FROM skill WHERE scope_kind = 'agent' AND scope_id = ? AND name = ?",
        (entry.member_id, entry.name),
    ).fetchone()
    source = str(entry.source)
    if row is None:
        conn.execute(
            "INSERT INTO skill(id, name, description, source_path, content_hash, enabled, "
            "scope_kind, scope_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, 'agent', ?, ?, ?)",
            (
                new_id("sk"),
                entry.name,
                entry.description,
                source,
                content_hash,
                entry.member_id,
                now,
                now,
            ),
        )
        return "created"
    changes = _skill_row_changes(row, entry.source, content_hash)
    if not changes:
        return "unchanged"
    conn.execute(
        "UPDATE skill SET source_path = ?, content_hash = ?, updated_at = ? WHERE id = ?",
        (source, content_hash, now, row["id"]),
    )
    return "updated"


def _apply_skill_entry(
    conn: sqlite3.Connection, plan: SkillMigrationPlan, entry: SkillMigrationEntry
) -> SkillApplyEntry:
    """复制一棵树（先 staging 后改名，不覆盖既有目标）并对齐库行。"""
    outcome = "skipped"
    if entry.action == SKILL_ACTION_CREATE:
        if os.path.lexists(entry.target):
            state, _resolved, existing = _existing_skill_tree(plan.global_root, entry.member_id, entry.name)
            if state != "present" or existing is None or str(existing["content_hash"]) != entry.content_hash:
                raise SkillMigrationError(f"目标在计划之后出现且内容不同，已停止：{entry.target}")
            outcome = "skipped"
        else:
            parent = entry.target.parent
            ensure_state_partition(plan.global_root, str(parent))
            staging = parent / f".migration-{uuid.uuid4().hex}"
            try:
                shutil.copytree(entry.source, staging, copy_function=shutil.copy2)
                staged = read_skill_tree(staging)
                if str(staged["content_hash"]) != entry.content_hash:
                    raise SkillMigrationError(f"复制结果与计划中的 hash 不一致，已停止：{entry.target}")
                if os.path.lexists(entry.target):
                    raise SkillMigrationError(f"目标在复制期间出现，已停止（未覆盖）：{entry.target}")
                os.rename(staging, entry.target)
                outcome = "copied"
            finally:
                if os.path.lexists(staging):
                    shutil.rmtree(staging, ignore_errors=True)
    row_action = _upsert_skill_row(conn, entry, entry.content_hash)
    return SkillApplyEntry(
        entry=entry, outcome=outcome, row_action=row_action, content_hash=entry.content_hash
    )


def apply_skill_migration(plan: SkillMigrationPlan) -> SkillApplyResult:
    """执行**已计划**的迁移：备份 → 清单 → 逐条复制 → 重算 hash → 更新/新建行。

    有拒绝条目时**在动手之前**整体停下（零写入）。任何异常：立即停、带上已完成条目、
    已复制的文件保留、不回滚删除（票面行为 4/5）。
    """
    refused = plan.refused
    if refused:
        listing = "；".join(f"{entry.source} → {entry.target}" for entry in refused)
        raise SkillMigrationError(f"存在需人工裁定的同名目标，已拒绝执行（未写入任何文件）：{listing}")

    pending = plan.pending
    if not pending:
        return SkillApplyResult(backup_path=None, manifest_path=None, completed=())

    try:
        backup_path = _backup_data_dir(plan.data_dir, SCHEMA_VERSION, SCHEMA_VERSION, label="skills")
    except BackupError as error:
        raise SkillMigrationError(f"技能迁移前的整目录备份失败，已停止（未写入任何文件）：{error}") from error
    try:
        manifest_path = _write_skill_manifest(plan)
    except SkillMigrationError as error:
        raise SkillMigrationError(f"{error}（备份已建：{backup_path}）") from error

    completed: list[SkillApplyEntry] = []
    conn = connect(plan.data_dir / DATABASE_FILE_NAME)
    try:
        for entry in pending:
            try:
                completed.append(_apply_skill_entry(conn, plan, entry))
            except (OSError, sqlite3.Error, SkillValidationError, WorkspaceConfigError) as error:
                raise SkillMigrationFailed(
                    f"迁移在中途失败：{error}",
                    SkillApplyResult(
                        backup_path=backup_path,
                        manifest_path=manifest_path,
                        completed=tuple(completed),
                    ),
                ) from error
            except SkillMigrationError as error:
                raise SkillMigrationFailed(
                    str(error),
                    SkillApplyResult(
                        backup_path=backup_path,
                        manifest_path=manifest_path,
                        completed=tuple(completed),
                    ),
                ) from error
    finally:
        conn.close()
    return SkillApplyResult(backup_path=backup_path, manifest_path=manifest_path, completed=tuple(completed))


def render_skill_migration_plan(plan: SkillMigrationPlan) -> str:
    """逐条计划的可读报告：源 → 目标、动作与原因、字节数 / 文件数；频道级只报数量与建议。"""
    counts = dict.fromkeys(_SKILL_ACTION_LABELS, 0)
    for entry in plan.entries:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    lines = [
        "技能迁移计划（migrate-skills，只读扫描）",
        f"数据目录： {plan.data_dir}",
        f"全局层根： {plan.global_root}",
    ]
    if plan.entries:
        lines.append(
            f"成员级技能： {len(plan.entries)} 条"
            f"（新建 {counts[SKILL_ACTION_CREATE]} / 跳过 {counts[SKILL_ACTION_SKIP]}"
            f" / 拒绝 {counts[SKILL_ACTION_REFUSE]}）"
        )
        for entry in plan.entries:
            label = _SKILL_ACTION_LABELS.get(entry.action, entry.action)
            lines.append(
                f"  {label} {entry.source} → {entry.target}"
                f"（{entry.file_count} 个文件 / {entry.byte_count} 字节；hash {entry.content_hash}）"
            )
            lines.append(f"    原因：{entry.reason}")
    else:
        lines.append("成员级技能： 0 条（没有可迁移的成员级字节）")
    if plan.channel_level:
        total = sum(len(item.names) for item in plan.channel_level)
        lines.append(
            f"频道级技能（不迁移）： {len(plan.channel_level)} 个分区 / {total} 个技能"
            f" —— {SKILL_MIGRATION_CHANNEL_NOTICE}"
        )
        for partition in plan.channel_level:
            names = "、".join(partition.names) if partition.names else "（空分区）"
            lines.append(
                f"  频道 {partition.channel_id}：{partition.source}（{len(partition.names)} 个技能：{names}）"
            )
            lines.append(f"    {SKILL_MIGRATION_CHANNEL_NOTICE}")
    if plan.skipped:
        lines.append(f"跳过（无法判定或不可用）： {len(plan.skipped)} 处")
        for skip in plan.skipped:
            lines.append(f"  {skip.source}：{skip.reason}")
    return "\n".join(lines)


def render_skill_migration_result(result: SkillApplyResult) -> str:
    """apply 的完成读数：备份 / 清单落点、逐条去向、可手工删除的源路径。"""
    lines: list[str] = []
    if result.backup_path is None and result.manifest_path is None:
        lines.append("技能迁移：没有需要写入的条目（全部已就位或没有成员级字节）；未写入任何文件。")
    else:
        lines.append("技能迁移完成")
        if result.backup_path is not None:
            lines.append(f"  数据目录备份： {result.backup_path}")
        if result.manifest_path is not None:
            lines.append(f"  迁移清单： {result.manifest_path}")
    copied = sum(1 for item in result.completed if item.outcome == "copied")
    lines.append(
        f"处理条目： {len(result.completed)} 条（复制 {copied} / 跳过 {len(result.completed) - copied}）"
    )
    for item in result.completed:
        entry = item.entry
        action = "复制" if item.outcome == "copied" else "跳过"
        lines.append(
            f"  {action} {entry.source} → {entry.target}"
            f"（{entry.file_count} 个文件 / {entry.byte_count} 字节；hash {item.content_hash}）"
        )
        if item.row_action == "created":
            lines.append(f"    库行：新建（scope_kind=agent，scope_id={entry.member_id}）")
        elif item.row_action == "updated":
            lines.append(f"    库行：更新 {entry.row_id}（{', '.join(entry.row_changes)}）")
        else:
            lines.append("    库行：无需改动")
    if result.removable_sources:
        lines.append("可手工删除的源路径（确认后；本命令不删除任何源文件）：")
        for source in result.removable_sources:
            lines.append(f"  {source}")
    return "\n".join(lines)


# ─── MCP 声明导入（#275 / ADR-0031 §二） ────────────────────────────────────
# 这是**显式**的一次性导入：成员家目录 ``<全局根>/agents/<member>/mcp.json`` 的条目
# → ``mcp_server`` 的 Agent 级库行。它不在启动路径上、不是 HTTP 端点、不改 schema、不删源文件：
# 只读文件、只写库行、只落备份与清单。
#
# 三种处置（与 #274 的 ``create`` / ``skip`` / ``refuse`` 同一套安全口径）：
# - ``create``：库里没有同名行 → 新建（``scope_kind='agent'`` / ``scope_id=<member>``）；
# - ``skip``：库里已有同名**同形**行 → 不覆盖、不写 ``updated_at``；
# - ``refuse``：库里已有同名行但**形状不同** → **拒绝整条命令**，要求人类先裁定。
#   本命令不提供 ``--force``。
#
# 另有一档与条目无关的拒绝（:class:`McpFileRefusal`）：**文件本身不可用**（非法 JSON / 顶层形状不对 /
# 某条形状不合法 / 文件里重名）。它与 Studio 写路径同一口径——静默丢掉人写的一条才是坏结果，所以
# 整份文件都不导入，而不是跳过坏的那一条。
#
# **远端条目的确认不代做**（票面要求）：``remote_consent_scope('mcp', …)`` 一行都不写，
# 只在报告里点出导入后仍需 ``POST /api/mcp-servers/{id}/consent``。理由与 ADR-0031 §三 同源：
# 数据面扩大必须由人类逐条确认，迁移工具不是那条确认通道。
MCP_MIGRATION_MANIFEST_PREFIX = "mcp-migration-"
MCP_ACTION_CREATE = "create"
MCP_ACTION_SKIP = "skip"
MCP_ACTION_REFUSE = "refuse"
_MCP_ACTION_LABELS = {
    MCP_ACTION_CREATE: "新建",
    MCP_ACTION_SKIP: "跳过",
    MCP_ACTION_REFUSE: "拒绝",
}
# 导入报告里指向远端条目确认入口的那句（逐条带真实库行 id）。
MCP_MIGRATION_CONSENT_NOTICE = (
    "远端条目的数据面确认**未代做**：导入后仍需由人逐条调用 "
    "`POST /api/mcp-servers/{id}/consent`（或在 Studio 里点「确认使用」）。"
)
# 「形状」里**不算**东西的键：库行的身份（``id`` / ``scope_*``）与时间戳不是声明形状的一部分。
# 其余键一律算形状，且**由归一化内核的输出派生**（不手抄）——加一列时形状比较自动跟上，
# 不会因为漏抄而把「不同形」误判成「同形」。
_MCP_NON_SHAPE_KEYS: frozenset[str] = frozenset(
    {"id", "scope_kind", "scope_id", "created_at", "updated_at", "is_remote", "consent_required", "consented"}
)


class McpMigrationError(MigrationError):
    """MCP 导入**没能开始或没能完成**：库形态不对 / 文件不可用 / 同名不同形 / 写库失败。"""


@dataclass(frozen=True)
class McpMigrationEntry:
    """一条逐条计划：一份 ``mcp.json`` 里的一条声明、它的动作、原因与（若有）既有行。"""

    source: Path
    member_id: str
    declaration: dict[str, Any]
    action: str
    reason: str
    row_id: str | None

    @property
    def name(self) -> str:
        return str(self.declaration["name"])

    @property
    def is_remote(self) -> bool:
        return str(self.declaration["transport"]) in REMOTE_MCP_TRANSPORTS

    @property
    def needs_write(self) -> bool:
        """这一条是否需要动库行：本命令只「新建」，已同形的条目是 ``skip``（重复 apply 零写入）。

        没有「更新既存行」这一档是**刻意的**：同形 → 跳过；不同形 → 拒绝并要求人裁定。两者都不写。
        """
        return self.action == MCP_ACTION_CREATE


@dataclass(frozen=True)
class McpFileRefusal:
    """一份 ``mcp.json`` **整体**不可用：一条都不导入，原因指名到条目 / 字段。

    它不是「一条声明的动作」，因为它没有可导入的声明可指；报告与拒绝理由都按「这份文件」讲。
    """

    source: Path
    member_id: str
    reason: str


@dataclass(frozen=True)
class McpMigrationSkip:
    """没能导入的成员分区（不是成员 / 家目录越界 / 不可读）与一句原因（不猜、不静默跳过）。"""

    source: Path
    reason: str


@dataclass(frozen=True)
class McpMigrationPlan:
    """一次导入的**只读**计划：逐条动作、整体拒绝与跳过读数（dry-run 的全部产出）。"""

    data_dir: Path
    global_root: str
    entries: tuple[McpMigrationEntry, ...]
    skipped: tuple[McpMigrationSkip, ...]
    refusals: tuple[McpFileRefusal, ...] = ()

    @property
    def refused(self) -> tuple[McpMigrationEntry, ...]:
        return tuple(entry for entry in self.entries if entry.action == MCP_ACTION_REFUSE)

    @property
    def has_refusals(self) -> bool:
        """是否有任何一档拒绝（同名不同形 / 整份文件不可用）——有则 apply 零写入。"""
        return bool(self.refused) or bool(self.refusals)

    @property
    def pending(self) -> tuple[McpMigrationEntry, ...]:
        """本次 apply 真的要写的条目（已就位 → 空）。"""
        return tuple(entry for entry in self.entries if entry.needs_write)

    @property
    def remote_imports(self) -> tuple[McpMigrationEntry, ...]:
        return tuple(entry for entry in self.pending if entry.is_remote)


@dataclass(frozen=True)
class McpApplyEntry:
    """一条 apply 的完成读数：条目与最终行 id（远端确认入口要用它）。"""

    entry: McpMigrationEntry
    row_id: str


@dataclass(frozen=True)
class McpApplyResult:
    """一次 apply 的完成读数（失败时也带着已完成的部分）。"""

    backup_path: Path | None
    manifest_path: Path | None
    completed: tuple[McpApplyEntry, ...]

    @property
    def remote_rows(self) -> tuple[McpApplyEntry, ...]:
        """已导入的**远端**行（报告据此逐条给出确认入口；本命令不代确认）。"""
        return tuple(item for item in self.completed if item.entry.is_remote)


def _mcp_declaration_shape(declaration: Mapping[str, Any]) -> dict[str, Any]:
    """一条声明的**形状**（参与「同形 / 不同形」判定的一组值，不含身份与时间戳）。

    键集从 :func:`mcp.normalize_mcp_declaration` 的输出派生（见 :data:`_MCP_NON_SHAPE_KEYS` 的
    注释）：两侧（文件声明 / 库行）都含同一组形状键，因此比较键集仍是「归一化后的一致」。
    """
    return {key: value for key, value in declaration.items() if key not in _MCP_NON_SHAPE_KEYS}


def _plan_member_entries(
    *,
    member_id: str,
    source: Path,
    declarations: list[dict[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
) -> list[McpMigrationEntry]:
    """一个成员分区的逐条动作：库里没有 → 新建；有且同形 → 跳过；有但不同形 → 拒绝。"""
    entries: list[McpMigrationEntry] = []
    for declaration in declarations:
        name = str(declaration["name"])
        row = rows.get(name)
        if row is None:
            entries.append(
                McpMigrationEntry(
                    source=source,
                    member_id=member_id,
                    declaration=declaration,
                    action=MCP_ACTION_CREATE,
                    reason="库里没有同名声明，将按成员作用域新建",
                    row_id=None,
                )
            )
            continue
        if _mcp_declaration_shape(row) == _mcp_declaration_shape(declaration):
            entries.append(
                McpMigrationEntry(
                    source=source,
                    member_id=member_id,
                    declaration=declaration,
                    action=MCP_ACTION_SKIP,
                    reason="库里已有同名同形声明，不覆盖",
                    row_id=str(row["id"]),
                )
            )
            continue
        entries.append(
            McpMigrationEntry(
                source=source,
                member_id=member_id,
                declaration=declaration,
                action=MCP_ACTION_REFUSE,
                reason=("库里已有同名但形状不同的声明，需人工裁定后再导入（本命令不覆盖、不提供 --force）"),
                row_id=str(row["id"]),
            )
        )
    return entries


def _member_mcp_source(root: str, member_id: str) -> tuple[Path | None, str | None]:
    """该成员 ``mcp.json`` 的路径与**不可读/越界**的原因（``None, None`` = 文件缺席）。

    路径判定复用全局只读守卫：根外 symlink 祖先与根外目标一律拒绝——与装配层、Studio 读路径
    **同一份**判据，不新写一套。
    """
    raw = Path(os.path.join(root, AGENTS_DIR, safe_partition_id(member_id), GLOBAL_MCP_FILENAME))
    resolved = resolve_global_read_path(root, AGENTS_DIR, safe_partition_id(member_id), GLOBAL_MCP_FILENAME)
    if resolved is None:
        if os.path.lexists(raw):
            return raw, "文件路径越出全局根（根外 symlink 祖先或根外目标），未导入"
        return None, None
    return Path(resolved), None


def plan_mcp_migration(data_dir: Path | str, global_root: str | None = None) -> McpMigrationPlan:
    """扫描全局层各成员家目录的 ``mcp.json``，产出**只读**的逐条计划（dry-run 的全部实现）。

    遍历 ``<全局根>/agents/<partition>/mcp.json``（不是遍历库里的成员）：这样「家目录里有一份文件、
    库里却没有对应成员」的情况会进 ``skipped`` 并给原因，而不是被静默漏掉。库里的成员行只用于
    判定分区归属与读取既有声明行。
    """
    data_dir_path = Path(data_dir)
    db_path = data_dir_path / DATABASE_FILE_NAME
    if not db_path.is_file():
        raise McpMigrationError(f"数据目录里没有 {DATABASE_FILE_NAME}：{data_dir_path}")
    root = os.path.realpath(global_root if global_root is not None else global_home_root())

    conn = connect(db_path)
    try:
        tables = _table_names(conn)
        missing = {"member", "mcp_server"} - tables
        if missing:
            raise McpMigrationError(
                "库里缺少 MCP 导入需要的表："
                + "、".join(sorted(missing))
                + "；请先用 `python -m alibot --migrate` 把库推进到本代码的 schema 版本。"
            )
        members = {
            safe_partition_id(str(row["id"])): row
            for row in conn.execute("SELECT id, kind, deleted_at FROM member")
        }
        rows_by_member: dict[str, dict[str, Mapping[str, Any]]] = {}
        for row in conn.execute(
            "SELECT * FROM mcp_server WHERE scope_kind = 'agent' ORDER BY created_at, id"
        ):
            payload = {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "transport": str(row["transport"]),
                "command": str(row["command"]),
                "args": json.loads(row["args"] or "[]"),
                "url": str(row["url"]),
                "env_names": json.loads(row["env_names"] or "[]"),
                "enabled": bool(row["enabled"]),
                "scope_kind": str(row["scope_kind"]),
                "scope_id": str(row["scope_id"]),
            }
            rows_by_member.setdefault(str(row["scope_id"]), {})[str(row["name"])] = payload
    finally:
        conn.close()

    entries: list[McpMigrationEntry] = []
    skipped: list[McpMigrationSkip] = []
    refusals: list[McpFileRefusal] = []
    agents_dir = Path(root) / AGENTS_DIR
    if not agents_dir.is_dir():
        return McpMigrationPlan(data_dir=data_dir_path, global_root=root, entries=(), skipped=())

    for partition in sorted(agents_dir.iterdir(), key=lambda item: item.name):
        if partition.is_symlink() or not partition.is_dir():
            continue
        source, guard_reason = _member_mcp_source(root, partition.name)
        if guard_reason is not None:
            skipped.append(McpMigrationSkip(source or partition, guard_reason))
            continue
        if source is None or not source.is_file():
            continue  # 该成员没有 mcp.json：这一层缺席，不是跳过项
        member = members.get(partition.name)
        if member is None:
            skipped.append(McpMigrationSkip(source, "分区不是库里的成员（或成员 id 被收敛过），未导入"))
            continue
        if str(member["kind"]) != "agent" or member["deleted_at"] is not None:
            skipped.append(McpMigrationSkip(source, "分区对应的成员不是未删除的 Agent，未导入"))
            continue
        member_id = str(member["id"])
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            skipped.append(McpMigrationSkip(source, f"文件不可读（{error}），未导入"))
            continue
        try:
            declarations = validate_mcp_document(text)
        except InvalidMCPDocument as error:
            # 文件整体不可用 ⇒ 整体拒绝（与 Studio 写路径同一口径：静默丢掉人写的一条才是坏结果）。
            refusals.append(
                McpFileRefusal(
                    source=source,
                    member_id=member_id,
                    reason=f"文件不可用，未导入（{error.message}；位置 {error.location}）",
                )
            )
            continue
        entries.extend(
            _plan_member_entries(
                member_id=member_id,
                source=source,
                declarations=declarations,
                rows=rows_by_member.get(member_id, {}),
            )
        )
    return McpMigrationPlan(
        data_dir=data_dir_path,
        global_root=root,
        entries=tuple(entries),
        skipped=tuple(skipped),
        refusals=tuple(refusals),
    )


def _write_mcp_manifest(plan: McpMigrationPlan) -> Path:
    """先落清单再写库：清单是本次导入的审计正本（数据目录内，UTC 时间戳）。"""
    stamp = datetime.now(UTC).strftime(BACKUP_TIMESTAMP_FORMAT)
    path = plan.data_dir / f"{MCP_MIGRATION_MANIFEST_PREFIX}{stamp}.json"
    suffix = 1
    while path.exists():
        suffix += 1
        path = plan.data_dir / f"{MCP_MIGRATION_MANIFEST_PREFIX}{stamp}-{suffix}.json"
    payload = {
        "kind": "mcp-migration",
        "created_at": utc_now(),
        "data_dir": str(plan.data_dir),
        "global_root": plan.global_root,
        "entries": [
            {
                "source": str(entry.source),
                "member_id": entry.member_id,
                "name": entry.name,
                "transport": entry.declaration["transport"],
                "action": entry.action,
                "reason": entry.reason,
                "row_id": entry.row_id,
            }
            for entry in plan.entries
        ],
        "skipped": [{"source": str(item.source), "reason": item.reason} for item in plan.skipped],
        "refusals": [
            {
                "source": str(item.source),
                "member_id": item.member_id,
                "reason": item.reason,
            }
            for item in plan.refusals
        ],
        "consent_notice": MCP_MIGRATION_CONSENT_NOTICE,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        raise McpMigrationError(f"导入清单写入失败，已停止（库行一条都没写）：{error}") from error
    return path


def _insert_mcp_row(conn: sqlite3.Connection, entry: McpMigrationEntry) -> str:
    """按成员作用域新建一条声明行；返回新行 id。

    ``args`` / ``env_names`` 落成 JSON 数组（与库写路径同一存储形态）；``enabled`` 落 0/1。
    ``env_names`` 只有变量**名**——文件里根本没有值的落点，因此导入不会带入任何密钥。
    """
    declaration = entry.declaration
    now = utc_now()
    server_id = new_id("mcp")
    conn.execute(
        "INSERT INTO mcp_server(id, name, transport, command, args, url, env_names, enabled, "
        "scope_kind, scope_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'agent', ?, ?, ?)",
        (
            server_id,
            str(declaration["name"]),
            str(declaration["transport"]),
            str(declaration.get("command") or ""),
            json.dumps(list(declaration.get("args") or []), ensure_ascii=False),
            str(declaration.get("url") or ""),
            json.dumps(list(declaration.get("env_names") or []), ensure_ascii=False),
            1 if declaration.get("enabled", True) else 0,
            entry.member_id,
            now,
            now,
        ),
    )
    return server_id


def apply_mcp_migration(plan: McpMigrationPlan) -> McpApplyResult:
    """执行**已计划**的导入：备份 → 清单 → 逐条新建库行（单事务收口）。

    有拒绝条目时**在动手之前**整体停下（零写入，退出码由调用方给 6）。远端条目的确认**不代做**：
    ``remote_consent_scope`` 一行都不写（票面约束「不代确认」），报告里逐条给出确认入口。
    """
    # 两档拒绝都在**写任何字节之前**收口：同名不同形（要人裁定）与整份文件不可用。
    # 后者若不拦住，同一批里别的好文件会被部分导入，于是「一次 apply」变成一份混合结果。
    if plan.has_refusals:
        parts: list[str] = [f"{entry.source} 的「{entry.name}」（{entry.reason}）" for entry in plan.refused]
        parts.extend(f"{item.source}（{item.reason}）" for item in plan.refusals)
        raise McpMigrationError("存在需人工裁定的条目，已拒绝执行（未写入任何库行）：" + "；".join(parts))

    pending = plan.pending
    if not pending:
        return McpApplyResult(backup_path=None, manifest_path=None, completed=())

    try:
        backup_path = _backup_data_dir(plan.data_dir, SCHEMA_VERSION, SCHEMA_VERSION, label="mcp")
    except BackupError as error:
        raise McpMigrationError(f"MCP 导入前的整目录备份失败，已停止（未写入任何库行）：{error}") from error
    try:
        manifest_path = _write_mcp_manifest(plan)
    except McpMigrationError as error:
        raise McpMigrationError(f"{error}（备份已建：{backup_path}）") from error

    completed: list[McpApplyEntry] = []
    conn = connect(plan.data_dir / DATABASE_FILE_NAME)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for entry in pending:
                completed.append(McpApplyEntry(entry=entry, row_id=_insert_mcp_row(conn, entry)))
            conn.execute("COMMIT")
        except Exception:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    except Exception as error:
        raise McpMigrationError(
            f"MCP 导入失败并已回滚（库停在原状）：{error}；备份在 {backup_path}"
        ) from error
    finally:
        conn.close()
    return McpApplyResult(backup_path=backup_path, manifest_path=manifest_path, completed=tuple(completed))


def render_mcp_migration_plan(plan: McpMigrationPlan) -> str:
    """逐条计划的可读报告：源文件 → 动作与原因、名字与传输；远端条目提前点明确认入口。"""
    counts = dict.fromkeys(_MCP_ACTION_LABELS, 0)
    for entry in plan.entries:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    lines = [
        "MCP 导入计划（migrate-mcp，只读扫描）",
        f"数据目录： {plan.data_dir}",
        f"全局层根： {plan.global_root}",
    ]
    if plan.entries:
        lines.append(
            f"家目录声明： {len(plan.entries)} 条"
            f"（新建 {counts[MCP_ACTION_CREATE]} / 跳过 {counts[MCP_ACTION_SKIP]}"
            f" / 拒绝 {counts[MCP_ACTION_REFUSE]}）"
        )
        for entry in plan.entries:
            label = _MCP_ACTION_LABELS.get(entry.action, entry.action)
            kind = "远端" if entry.is_remote else "本机"
            lines.append(
                f"  {label} {entry.source} 的「{entry.name}」"
                f"（{entry.declaration['transport']} / {kind}）→ 成员 {entry.member_id}"
            )
            lines.append(f"    原因：{entry.reason}")
    else:
        lines.append("家目录声明： 0 条（家目录里没有可导入的 mcp.json 条目）")
    if plan.refusals:
        # 整份文件不可用：它没有逐条声明可列，所以按「这份文件」讲，并指明出错位置。
        lines.append(f"拒绝（文件不可用，整份都不导入）： {len(plan.refusals)} 份")
        for item in plan.refusals:
            lines.append(f"  {item.source}（成员 {item.member_id}）：{item.reason}")
    if plan.skipped:
        lines.append(f"未导入（无法判定或不可用）： {len(plan.skipped)} 处")
        for skip in plan.skipped:
            lines.append(f"  {skip.source}：{skip.reason}")
    if plan.remote_imports:
        lines.append(f"远端条目 {len(plan.remote_imports)} 条：{MCP_MIGRATION_CONSENT_NOTICE}")
    return "\n".join(lines)


def render_mcp_migration_result(result: McpApplyResult) -> str:
    """apply 的完成读数：备份 / 清单落点、逐条新建的库行，以及**未代做**的远端确认。"""
    lines: list[str] = []
    if result.backup_path is None and result.manifest_path is None:
        lines.append("MCP 导入：没有需要导入的条目（全部已同形在库里或没有家目录条目）；未写入任何文件。")
    else:
        lines.append("MCP 导入完成")
        if result.backup_path is not None:
            lines.append(f"  数据目录备份： {result.backup_path}")
        if result.manifest_path is not None:
            lines.append(f"  导入清单： {result.manifest_path}")
    lines.append(f"处理条目： {len(result.completed)} 条（新建 {len(result.completed)} / 跳过 0）")
    for item in result.completed:
        entry = item.entry
        kind = "远端" if entry.is_remote else "本机"
        lines.append(f"  新建 {entry.source} 的「{entry.name}」（{entry.declaration['transport']} / {kind}）")
        lines.append(f"    库行：新建 {item.row_id}（scope_kind=agent，scope_id={entry.member_id}）")
    if result.remote_rows:
        lines.append(MCP_MIGRATION_CONSENT_NOTICE)
        for item in result.remote_rows:
            lines.append(f"  POST /api/mcp-servers/{item.row_id}/consent  （成员 {item.entry.member_id}）")
    return "\n".join(lines)
