"""当前 schema 库的**作用域数据补齐入口**（issue #37 追加范围）。

**这不是自动迁移。** 跨版本迁移是**显式命令** ``python -m alibot --migrate``（逐版链在
``alibot/migrate.py``），任何启动路径都不自动迁移（ADR-0015 取代了 ADR-0004 / 规格决策 8 的
「不做旧数据迁移」口径）。本模块只在用户**显式**执行 ``python -m alibot --repair-targets`` 时运行，
任何正常启动路径都**不会**调用它（``__main__`` 里它是与「起服务」并列的另一条分支，不是启动流程的
一步）。它同时是迁移链 ``3 → 4`` 那一步的**共用列映射**来源（同一个「channel 型作用域数据补齐」
语义只有一份 SQL）。**把本入口降为 ``--migrate`` 的弃用别名、并放宽它的版本门，见实施票 ④（#94）**：
本票（#90 / #91）不改本入口的版本门——它仍严格要求库版本等于当前 ``SCHEMA_VERSION``，旧 v4 库会由
版本闸门拒绝，不会被本入口升级。

它处置的是**一个具体的历史事故**：#24 把消息作用域升为 ``target`` 并把 ``SCHEMA_VERSION`` 3 → 4，
而 v4 的 ``_initialize`` 曾经会静默接受 v3 库（那是 #37 主修修掉的那半）。被那样打开过的库会停在
**半改名**状态——``schema_version`` 已是 4、``channel`` 行都在，但某些 channel 型 target 的
**影子行缺失**，``channel_member`` / ``channel_read`` / ``channel_budget`` 的数据还留在旧表名里。
症状是 ``GET /api/channels`` 看起来正常，点进缺影子行的那个频道则成员 / 消息 / 预算 / 标读
**全部 404**。**数据一条没丢，只是没搬。**

这段历史是本入口的缘起，不代表跨版本迁移：当前实现仍严格要求库版本等于当前 ``SCHEMA_VERSION``；
旧 v4 库会由版本闸门拒绝，不会被本入口升级。

补齐四张表（channel 型 target 的作用域数据，ADR-0004）：

- ``target`` ← ``channel``：影子行（``kind='channel'``，id **复用** ``channel.id``）。
- ``target_member`` ← ``channel_member``：频道成员关系。
- ``target_read`` ← ``channel_read``：每成员已读位置。它同时是**未读徽标的唯一正本**。
- ``target_budget`` ← ``channel_budget``：有界响应预算的账本（``epoch_seq`` / ``used``）。

**第四张表不在事故处置建议的三条语句里，但它同样承重**：只补前三张的话，
``GET /api/channels/{id}/budget`` 会从 404 变成 **200 而 ``used`` 归零**。那不是「少一行」，
而是「沉默地抹掉一个已经耗尽的预算」——比 404 更糟，因为它把「预算烧完了」伪装成「还没用过」。
因此本入口把两张预算表**一并**补齐并核验。

三条纪律（票面追加要求 2–4 条）：**默认不执行**、**幂等**、**不得掩盖版本问题**。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

# 本模块是**纯数据逻辑**：不 import ``__main__``（那会形成「CLI ↔ 修复」的循环依赖），也不决定
# 进程退出码——那由 CLI 层（``__main__``）自己映射。于是「同一个库，两条路径给同一个答复」
# 这件事落在**行为**上（两条路径都经 :class:`Database` 的同一道闸门），而不是靠共享一个常量。
from .db import DATABASE_FILE_NAME, Database

# ─── 补齐语句 ────────────────────────────────────────────────────────────
# 每张目标表一条 ``INSERT OR IGNORE``，SQL 本身**一句一个模块级常量**（表名不插进 ``execute``
# 的实参，与 ``db.py`` 的 ``UNREAD_COUNT_SQL`` / ``AGENT_SELECT_SQL`` 同一体例）；下面再用一个
# 元组把「目标表 ← 旧表」与对应语句配起来，供循环取用。
#
# 为什么每条都从 ``channel`` 出发：channel 型 target 的 id **复用** ``channel.id``（ADR-0004 的
# 影子行语义），所以「channel 型的作用域数据」只可能来自频道。这个起点同时保证了 dm / task 型
# target，以及挂在它们上面的成员 / 已读 / 预算行，**一个都不会被碰**——本入口没有它们的来源表，
# 也就没有误伤它们的路径。
#
# 为什么显式列出列名（而不是 ``SELECT *``）：新表与旧表的列名本就不同（``channel_id`` →
# ``target_id``），显式写一次「哪个列搬到哪个列」比赌列顺序更稳，且一眼可审。
#
# 幂等来自 ``OR IGNORE``：主键 / 唯一键冲突的行被跳过，已补好的行不会被第二遍改写。
REPAIR_TARGET_SQL = (
    "INSERT OR IGNORE INTO target(id, kind, created_at) SELECT id, 'channel', created_at FROM channel"
)
REPAIR_TARGET_MEMBER_SQL = (
    "INSERT OR IGNORE INTO target_member(target_id, member_id) "
    "SELECT channel_id, member_id FROM channel_member"
)
REPAIR_TARGET_READ_SQL = (
    "INSERT OR IGNORE INTO target_read(member_id, target_id, last_read_seq) "
    "SELECT member_id, channel_id, last_read_seq FROM channel_read"
)
REPAIR_TARGET_BUDGET_SQL = (
    "INSERT OR IGNORE INTO target_budget(target_id, epoch_seq, used) "
    "SELECT channel_id, epoch_seq, used FROM channel_budget"
)

# 「目标表 ← 可选来源表 ← 语句」的配对与执行顺序。**这是这段列映射的唯一落点**：
# ``--repair-targets`` 的补齐循环与迁移链 ``3 → 4`` 那一步都从这里取（ADR-0015 §三「两个地方都在
# 搬同一批行」的告诫因此落在一份常量上，而不是两份彼此长得像的 SQL）。
#
# 来源表是**可选**的：当前 ``SCHEMA`` 只 CREATE IF NOT EXISTS 新表、**不建** ``channel_*`` 旧表，
# 所以一个**全新**的当前版本库根本没有它们——那种库本来就不需要补，跳过对应语句即可（否则会对一个
# 健康的库报错）。历史事故现场（「v4 打开过 v3 库」）里旧表**在**、带着数据。
#
# ``target`` 必须最先：另外三张表的引用约束都指向它。
CHANNEL_SCOPE_MOVES: tuple[tuple[str, str, str], ...] = (
    ("target", "channel", REPAIR_TARGET_SQL),
    ("target_member", "channel_member", REPAIR_TARGET_MEMBER_SQL),
    ("target_read", "channel_read", REPAIR_TARGET_READ_SQL),
    ("target_budget", "channel_budget", REPAIR_TARGET_BUDGET_SQL),
)

# 补齐循环按目标表取语句（下标取常量，与 ``db.py`` 的 ``BUSINESS_COUNT_SQL[table]`` 同体例）。
_REPAIR_ORDER: tuple[tuple[str, str], ...] = tuple(
    (table, source) for table, source, _ in CHANNEL_SCOPE_MOVES
)
_REPAIR_SQL: dict[str, str] = {table: sql for table, _, sql in CHANNEL_SCOPE_MOVES}

# 当前 schema 形态必须存在的表：``channel`` 是来源，四张 ``target*`` 是修复目标；``task`` 是
# v5 新增的业务表。缺任何一张都不是「当前库里 channel 型 target 行缺失」，本入口不处置。
_CURRENT_REQUIRED_TABLES = ("channel", "target", "target_member", "target_read", "target_budget", "task")

# ─── 事后自校验 ─────────────────────────────────────────────────────────────
# 旧表的**每一行**都必须在新表里找得到对应行。逐对写 SQL（同样一句一个常量），因为两边的列名
# 不同（``channel_id`` ↔ ``target_id``）—— 这正是「补齐」这件事唯一需要精确说清的地方。
# 自校验与补齐用的是同一组列名，所以它检验的是「上面四条语句真的做到了它们声称的事」，
# 而不是「跑完不报错」。
CHECK_SHADOW_ROWS_SQL = (
    "SELECT COUNT(*) FROM channel c LEFT JOIN target t "
    "ON t.id = c.id AND t.kind = 'channel' WHERE t.id IS NULL"
)
CHECK_MEMBER_ROWS_SQL = (
    "SELECT COUNT(*) FROM channel_member cm LEFT JOIN target_member tm "
    "ON tm.target_id = cm.channel_id AND tm.member_id = cm.member_id WHERE tm.target_id IS NULL"
)
CHECK_READ_ROWS_SQL = (
    "SELECT COUNT(*) FROM channel_read cr LEFT JOIN target_read tr "
    "ON tr.target_id = cr.channel_id AND tr.member_id = cr.member_id WHERE tr.target_id IS NULL"
)
CHECK_BUDGET_ROWS_SQL = (
    "SELECT COUNT(*) FROM channel_budget cb LEFT JOIN target_budget tb "
    "ON tb.target_id = cb.channel_id WHERE tb.target_id IS NULL"
)

# ── 反向（1:1 的另一半）──
# 上面四条只证明「旧表的每一行都搬过来了」。只查单向是不够的：一个库完全可能多出一个
# ``kind='channel'`` 的 target，却没有对应的 ``channel`` 行、或没有任何成员行——那时单向检查全绿，
# 但库已经不满足 ADR-0004 的不变量（“channel 型 target 是频道行的影子行”）。
# 因此这四条从 ``target`` 反向查：每条 channel 型 target 都必须有对应频道行与**至少**一行成员。
# ``kind='channel'`` 是硬条件：dm / task 型 target 不参与本入口（它们没有频道行，也不该有），
# 这正是「不误伤 dm/task」在自校验里的体现。
CHECK_ORPHAN_CHANNEL_TARGETS_SQL = (
    "SELECT COUNT(*) FROM target t LEFT JOIN channel c ON c.id = t.id "
    "WHERE t.kind = 'channel' AND c.id IS NULL"
)
CHECK_CHANNEL_TARGETS_WITHOUT_MEMBERS_SQL = (
    "SELECT COUNT(*) FROM target t WHERE t.kind = 'channel' AND NOT EXISTS "
    "(SELECT 1 FROM target_member tm WHERE tm.target_id = t.id)"
)

# 每条检查的「标签 + 它用到的表」；语句本体在下面按标签索引的字典里（**下标取常量**的形态，
# 与 ``db.py`` 的 ``BUSINESS_COUNT_SQL[table]`` 一致：不把裸变量直接喂给 ``execute``）。
# 表名显式写出来，是因为 ``_REPAIR_ORDER`` 里的表清单已经穷举了本模块认得的八张表，
# 没有需要从 SQL 文本里猜的余地。
#
# 前四条是**补齐方向**（旧表 → 新表：每一行都搬到了吗）；后两条是**反向 1:1**（新表 → 旧表：
# 每个 channel 型 target 都有频道行与成员行吗）。两个方向合起来才是规格要求的
# 「target<->channel 1:1」。
_CONSISTENCY_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("每个频道都有 channel 型影子行", ("channel", "target")),
    ("channel_member 的每一行都在 target_member 里", ("channel_member", "target_member")),
    ("channel_read 的每一行都在 target_read 里", ("channel_read", "target_read")),
    ("channel_budget 的每一行都在 target_budget 里", ("channel_budget", "target_budget")),
    ("每个 channel 型 target 都有对应的频道行", ("target", "channel")),
    ("每个 channel 型 target 都至少有一行成员", ("target", "target_member")),
)

_CHECK_SQL: dict[str, str] = {
    "每个频道都有 channel 型影子行": CHECK_SHADOW_ROWS_SQL,
    "channel_member 的每一行都在 target_member 里": CHECK_MEMBER_ROWS_SQL,
    "channel_read 的每一行都在 target_read 里": CHECK_READ_ROWS_SQL,
    "channel_budget 的每一行都在 target_budget 里": CHECK_BUDGET_ROWS_SQL,
    "每个 channel 型 target 都有对应的频道行": CHECK_ORPHAN_CHANNEL_TARGETS_SQL,
    "每个 channel 型 target 都至少有一行成员": CHECK_CHANNEL_TARGETS_WITHOUT_MEMBERS_SQL,
}

# 报「改动前后」行数用的表清单：四张补齐目标表 + 四张来源表。
REPORTED_TABLES = (
    "target",
    "target_member",
    "target_read",
    "target_budget",
    "channel",
    "channel_member",
    "channel_read",
    "channel_budget",
)

# 计数语句：同样一句一个模块级常量（避免把表名插进 ``execute`` 的实参）。
COUNT_TARGET_SQL = 'SELECT COUNT(*) FROM "target"'
COUNT_TARGET_MEMBER_SQL = 'SELECT COUNT(*) FROM "target_member"'
COUNT_TARGET_READ_SQL = 'SELECT COUNT(*) FROM "target_read"'
COUNT_TARGET_BUDGET_SQL = 'SELECT COUNT(*) FROM "target_budget"'
COUNT_CHANNEL_SQL = 'SELECT COUNT(*) FROM "channel"'
COUNT_CHANNEL_MEMBER_SQL = 'SELECT COUNT(*) FROM "channel_member"'
COUNT_CHANNEL_READ_SQL = 'SELECT COUNT(*) FROM "channel_read"'
COUNT_CHANNEL_BUDGET_SQL = 'SELECT COUNT(*) FROM "channel_budget"'

_ROW_COUNT_SQL: dict[str, str] = {
    "target": COUNT_TARGET_SQL,
    "target_member": COUNT_TARGET_MEMBER_SQL,
    "target_read": COUNT_TARGET_READ_SQL,
    "target_budget": COUNT_TARGET_BUDGET_SQL,
    "channel": COUNT_CHANNEL_SQL,
    "channel_member": COUNT_CHANNEL_MEMBER_SQL,
    "channel_read": COUNT_CHANNEL_READ_SQL,
    "channel_budget": COUNT_CHANNEL_BUDGET_SQL,
}


class RepairError(Exception):
    """修复入口自己发现的问题：库不接受本入口处置（例如本来就不是当前 schema 形态）。

    与 :class:`alibot.db.SchemaVersionMismatch` 分工不同——那个说「这个库本代码不接受」，
    这个说「版本号匹配，但库形态不是本入口能补齐的情况」。
    """


@dataclass(frozen=True)
class RepairOutcome:
    """一次补齐的结果。

    ``inserted`` 的键是**目标表**名、值是 ``INSERT OR IGNORE`` 真正写入的行数。它天然就是
    「本次新增了几行」这个读数，因此「对修好的库再跑一次 ⇒ 改动 0 行」用的是**同一个**账，
    不需要第二套计数逻辑。

    ``before`` / ``after`` 与 ``inserted`` 口径一致（每表行数），于是「哪几行是这次补上的」
    可以直接相减看出来，读报告的人不必相信一句「已修复」。
    """

    db_path: Path
    inserted: dict[str, int]
    before: dict[str, int]
    after: dict[str, int]
    verified: tuple[str, ...]

    @property
    def changed_rows(self) -> int:
        return sum(self.inserted.values())

    @property
    def already_complete(self) -> bool:
        return self.changed_rows == 0


def missing_current_required_tables(existing: set[str]) -> tuple[str, ...]:
    """当前 schema 形态**必须存在、但库里缺失**的表（空元组 = 形态完好）。

    这是「库形态对不对」这条判据的**唯一落点**：``--migrate`` 与 ``--repair-targets`` 两条显式入口
    共用它（票面要求复用既有判据，不新增第二套）。缺表时算不算「本入口不处置」还取决于库里有不有
    业务数据（:meth:`Database.has_business_data`）——那由调用方按自己的答复语义决定：修复入口报错、
    迁移入口返回退出码 ``4``，而没有业务数据时两者都是空操作（``0``）。
    """
    return tuple(sorted(set(_CURRENT_REQUIRED_TABLES) - existing))


def _counts(conn: sqlite3.Connection, existing: set[str]) -> dict[str, int]:
    """每张报告表当前有多少行（不存在的表按 0 计，于是「缺表」与「空表」在报告里可分辨）。"""
    counts: dict[str, int] = {}
    for table in REPORTED_TABLES:
        if table not in existing:
            counts[table] = 0
            continue
        row = conn.execute(_ROW_COUNT_SQL[table]).fetchone()
        counts[table] = int(row[0]) if row is not None else 0
    return counts


def _remaining_gaps(conn: sqlite3.Connection, existing: set[str]) -> list[str]:
    """还差什么没补齐（空列表 = 已补齐）。

    检查用到的表不全在时**跳过该条**：那种库不是「当前 schema 形态的半改名库」，由调用方另行报错，
    不在这里误报成「补齐失败」（``_validate_shape`` 已经把这件事说清楚了）。
    """
    gaps: list[str] = []
    for label, tables in _CONSISTENCY_CHECKS:
        if not set(tables) <= existing:
            continue
        row = conn.execute(_CHECK_SQL[label]).fetchone()
        count = int(row[0]) if row is not None else 0
        if count:
            gaps.append(f"{label}：还差 {count} 行")
    return gaps


def _nothing_to_repair(db_path: Path) -> RepairOutcome:
    """「没什么可补」的结果：改动 0 行、自校验项为空（没跑过补齐就不声称验过什么）。"""
    empty: dict[str, int] = dict.fromkeys(REPORTED_TABLES, 0)
    return RepairOutcome(db_path=db_path, inserted={}, before=empty, after=dict(empty), verified=())


def repair_target_rows(data_dir: Path | str) -> RepairOutcome:
    """把 **channel 型 target** 缺失的作用域数据补齐（幂等、可自校验）。

    **版本闸门复用 :class:`Database` 的那一个判据**：库的 ``schema_version`` 不等于本代码的常量时，
    :meth:`Database.open_without_initializing` 会抛 :class:`~alibot.db.SchemaVersionMismatch`，
    于是本入口在**动任何数据之前**就拒绝执行。这不是「在代码里比对一下常量」的软检查，而是与主修
    **同一段代码**给出的同一个答复（票面追加要求 4）。

    用 ``open_without_initializing``（而不是 ``Database(...)``）很要紧：后者会先把缺的表
    ``CREATE IF NOT EXISTS`` 出来，于是「某张表缺失」这个**正要诊断的事实**会在诊断前被抹掉，
    而且那已经是一次 schema 变更。

    四条 ``INSERT OR IGNORE`` 与自校验在**同一个写事务**内完成：自校验不通过就 ``ROLLBACK`` 并抛
    :class:`RepairError`。于是「本入口要么把库补齐到它声称的状态，要么一行都不改」是结构性事实，
    而不是「希望如此」。这同时让「对已修好的库再跑一次 ⇒ 改动 0 行」不可能有半途状态。

    旧表（``channel_member`` / ``channel_read`` / ``channel_budget``）**存在才搬、不存在就跳过**：
    全新的当前版本库没有它们，也不需要有（本入口对一个健康的库就是个空操作，不报错）。

    空目录 / 压根没有库文件时也是**空操作**（改动 0 行、退出码 0），不报错、也**不"顺手"建一个
    空库出来**：用户跑修复入口是想修已有数据，在一个没数据的目录上凭空生成 ``alibot.db`` 不是他要的。

    只写四张 ``target*`` 表；**不碰** ``channel*`` 旧表（它们是来源，保留原样）、不碰 dm / task 型
    target、不改 ``schema_meta``、不改任何表结构。
    """
    db_path = Path(data_dir) / DATABASE_FILE_NAME
    if not db_path.exists():
        # 没有库文件：没什么可修的，也**不去创建**它（连接会凭空生成一个空库）。
        return _nothing_to_repair(db_path)

    database = Database.open_without_initializing(data_dir)  # 闸门 + **不建表**
    try:
        conn = database._conn
        existing = database.table_names()
        missing = missing_current_required_tables(existing)
        if missing:
            # 库形态不对：但「压根没有业务数据」（空库 / 刚建还没初始化的库）不是错误——
            # 没有数据就没什么可补的，报错反而把「空目录上跑一次」变成一个失败。
            # 只有「**有**业务数据但不是当前 schema 形态」才是本入口不处置的那种库。
            if database.has_business_data():
                raise RepairError(
                    f"这个库不是当前 schema 形态（缺表：{', '.join(missing)}），本入口只处置「当前库里 "
                    f"channel 型 target 缺失」这一种情形。"
                )
            return _nothing_to_repair(db_path)

        before = _counts(conn, existing)
        inserted: dict[str, int] = {}
        # 用容器已有的事务助手 + **持它的锁**：``Database`` 的类文档承诺「写路径串行化」，
        # 本入口也是它的写路径之一，不应绕过那条契约（CLI 单线程下无实害，但契约要一致）。
        with database._lock:
            database._begin()
            try:
                for table, source in _REPAIR_ORDER:
                    # 来源表不在：这个库没那类旧数据可搬（例如全新的当前版本库），跳过。
                    # 记 0 而不是不留键，于是报告里「这张表本次 0 行」仍是显式读数。
                    if source is not None and source not in existing:
                        inserted[table] = 0
                        continue
                    cursor = conn.execute(_REPAIR_SQL[table])
                    inserted[table] = max(cursor.rowcount, 0)
                gaps = _remaining_gaps(conn, existing)
                if gaps:
                    database._rollback()
                    raise RepairError("补齐后自校验仍未通过，已回滚、未改动任何行：" + "；".join(gaps))
                database._commit()
            except Exception:
                database._rollback()  # 已提交/已回滚时它是 no-op（助手内部吞掉「无事务」）
                raise

        after = _counts(conn, existing)
        verified = tuple(label for label, _ in _CONSISTENCY_CHECKS)
        return RepairOutcome(
            db_path=database.path,
            inserted=inserted,
            before=before,
            after=after,
            verified=verified,
        )
    finally:
        database.close()


def render_outcome(outcome: RepairOutcome) -> str:
    """把一次补齐的结果写成人类可读的报告（stdout 用）。"""
    lines = [f"目标库：{outcome.db_path}", ""]
    if outcome.already_complete:
        lines.append("已是最新：没有需要补齐的行（本次改动 0 行）。")
    else:
        lines.append("本次补齐的行：")
        for table, count in outcome.inserted.items():
            if count:
                lines.append(f"  + {table}: {count} 行")
        lines.append(f"本次共补 {outcome.changed_rows} 行。")
    lines.append("")
    lines.append("行数（前 → 后）：")
    for table in REPORTED_TABLES:
        before, after = outcome.before[table], outcome.after[table]
        marker = f"  (+{after - before})" if after > before else ""
        lines.append(f"  {table:16s} {before:5d} → {after:<5d}{marker}")
    # 自校验只在真的跑过补齐（或库本来就是当前 schema 形态）时才有意义。空目录 / 没有数据的库上它为空，
    # 那就不要打一个空标题出来。
    if outcome.verified:
        lines.append("")
        lines.append("自校验通过：")
        lines.extend(f"  ✓ {label}" for label in outcome.verified)
    lines.append("")
    lines.append("提示：旧表（channel_member / channel_read / channel_budget）已不再被读写，")
    lines.append("      本入口**保留**它们的原样以便随时回退；确认无误后可自行决定是否清理。")
    return "\n".join(lines)
