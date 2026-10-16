"""逐版 schema 迁移引擎（issue #90 / #91）。

规格正本：``app/specs/issue-87-spec.md``（D5 步骤注册表、D6 逐版内容、D7 终态、D8 备份 / 事务 /
审计、D2 退出码）。ADR：``docs/adr/0015-schema-migration-is-explicit-and-forward-only.md``。

本模块只做**迁移这件事本身**，不决定进程退出码（那由 CLI 层 ``__main__`` 映射）、不监听任何端口。
它把两半**分开**：

- :func:`run_chain` —— 「对一个**已经打开的库**跑迁移链」：只吃一个 ``sqlite3.Connection`` 与源
  版本，逐步走注册表、做显式数据搬迁与形态对齐。它**不管**备份、审计、事务边界与退出码。
- :func:`migrate_data_dir` —— 「CLI 参数 / 数据目录 / 备份编排」：整目录备份 → 单事务 → 跑链 →
  落审计台账 → 给出报告。

这条缝是**故意留的挂点**：#93 的预演在**库副本**上跑同一条链（复用 :func:`run_chain`）；#92 的
**形态步骤**（:func:`run_shape_step`）已经是 :func:`run_chain` 的前置——它在走 :data:`STEPS` 之前
先按**形态**（不看版本号）补齐 channel 型作用域数据，因此预演与真实迁移共享同一个入口。

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
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
    utc_now,
)
from .repair import CHANNEL_SCOPE_MOVES, channel_scope_gaps

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
COPY_TABLE_SQL = 'INSERT INTO "{target}" ({columns}) SELECT {columns} FROM "{source}"'

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


def _rebuild_table(conn: sqlite3.Connection, table: str) -> None:
    """把一张既有表**重建**成当前 ``SCHEMA`` 的 DDL 形态并保真复制既有行。

    重建存在的唯一理由：SQLite 没有 ``ALTER TABLE ADD CONSTRAINT``，而 ``message`` /
    ``round_outcome`` 这类表在 v3 就已存在、外键引用只能靠重建补上（规格 D6 的 ``3 → 4``「加
    ``REFERENCES target(id)``」与 ``4 → 5`` 的复合外键）。列本身不动、数据逐行复制，因此这是
    「加约束」而不是「改写既有列」。

    ``PRAGMA defer_foreign_keys = ON`` 让 ``DROP TABLE`` 的隐式删除不立刻校验外键——新表随后被
    改名为正式名、行与原表一致，因此 ``COMMIT`` 时的检查仍然成立。
    """
    conn.execute("PRAGMA defer_foreign_keys = ON")
    temporary = f"{REBUILD_TABLE_PREFIX}{table}"
    columns = [name for name, _ in CURRENT_SCHEMA_SHAPE.columns[table] if name in _column_names(conn, table)]
    quoted = ", ".join(f'"{name}"' for name in columns)
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
    conn.execute(COPY_TABLE_SQL.format(target=temporary, columns=quoted, source=table))
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
    """从 ``from_version`` 到 ``to_version`` 的**连续**步骤序列（不跳步）。

    纯函数、不碰库：它是 #93 预演报告里「将执行的步骤清单」与执行器的共同来源。
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
    return ChainResult(from_version=from_version, to_version=to_version, steps=tuple(reports))


# ─── 备份与审计 ──────────────────────────────────────────────────────────────
def backup_directory_name(data_dir: Path | str, from_version: int, to_version: int) -> str:
    """``<数据目录>-backup-<from>-to-<to>-<UTC 时间戳>``（规格 D8 / S6）。"""
    data_dir = Path(data_dir)
    stamp = datetime.now(UTC).strftime(BACKUP_TIMESTAMP_FORMAT)
    return f"{data_dir.name}{BACKUP_INFIX}{from_version}-to-{to_version}-{stamp}"


def _backup_data_dir(data_dir: Path, from_version: int, to_version: int) -> Path:
    """把**整个数据目录**（含 ``attachments/`` 字节）复制到同级；失败即拒绝迁移。"""
    destination = data_dir.parent / backup_directory_name(data_dir, from_version, to_version)
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


def render_report(report: MigrationReport) -> str:
    """把一次迁移写成人类 / 脚本都读得懂的报告（stdout 用；D8）。"""
    lines = [
        f"迁移：{report.db_path}",
        f"源版本：{report.chain.from_version} → 目标版本：{report.chain.to_version}",
        f"本次备份：{report.backup_path}",
        "",
        "实际执行的步骤（每步改动的数据行数）：",
    ]
    for step in report.chain.steps:
        if step.from_version == step.to_version:
            # 形态步骤（#92）：它**不推进版本号**，``from`` / ``to`` 只是源版本的回声。链上的逐版步骤
            # 一律 ``from + 1 == to``（:func:`_assert_chain_is_continuous` 守着），所以这个分支不会
            # 落到版本步上；打出一个「4 → 4」会把不推进说成推进。
            lines.append(f"  {step.label}（不推进版本号）  改动 {step.changed_rows} 行")
        else:
            lines.append(
                f"  {step.from_version} → {step.to_version}  {step.label}  改动 {step.changed_rows} 行"
            )
    lines.extend(
        [
            "",
            f"迁移完成：schema_version = {report.chain.to_version}；本次共改动 {report.chain.changed_rows} 行。",
            f"审计记录：{report.ledger_path}",
        ]
    )
    return "\n".join(lines)
