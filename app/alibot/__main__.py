"""命令行入口：``python -m alibot``。

默认只绑定 **loopback**（``127.0.0.1:8788``）。首版无认证，因此不提供局域网/远程访问；
确实需要时设 ``ALIBOT_ALLOW_REMOTE=1`` 显式越界，并自行承担风险。
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .db import (
    DATA_DIR_ENV,
    DATABASE_FILE_NAME,
    DEFAULT_PORT,
    MIN_SUPPORTED_SOURCE_VERSION,
    SCHEMA_VERSION,
    Database,
    SchemaVersionMismatch,
    default_data_dir,
)

REMOTE_ENV = "ALIBOT_ALLOW_REMOTE"

# 数据目录里是一个本代码不接受的库（schema 版本不匹配）时的退出码。
# 与「非 loopback 拒绝启动」的 2 区分开：运维看一眼退出码就知道是哪一类拒绝。
# 三条入口（无旗标启动 / ``--migrate`` / ``--repair-targets``）**共用**它：同一个库，三条路径给
# 同一个答复——因为三者都经 ``db.Database`` 的**同一道版本闸门**，那是唯一的裁决点（ADR-0015）。
DATA_NOT_ACCEPTED_EXIT_CODE = 3

# 「库形态不是本入口能处置的那种」时的退出码：版本已等于本代码版本，但缺当前形态必须存在的表
# （``repair.missing_current_required_tables`` 的那份清单）**且**库里有业务数据。``--migrate`` 与
# ``--repair-targets`` 共用它，且都发生在**任何写入之前**（库文件 sha256 不变、数据目录里不出现新备份）。
#
# 它与「迁移失败、已回滚」是两回事：前者是「还没动手」，后者是「动手了、失败并回滚」。
REPAIR_FAILED_EXIT_CODE = 4

# 「迁移失败，**已回滚**」时的退出码（规格 D2 / ADR-0015）：备份失败、或迁移链跑到一半失败并已
# ``ROLLBACK``。它一定发生在**动手之后**——库 sha256 可能已被回滚到原状，但绝不留下「新表建了、
# 数据没搬」的中间态。与上面那个 ``4`` 的区分是「动手之前 vs 动手之后」。
MIGRATION_FAILED_EXIT_CODE = 5

# ``--repair-targets`` 已降为 ``--migrate`` 的**弃用别名**（ADR-0015 §五 / 规格 D11 / S13）：同一个
# 入口、同一条「形态步骤 + 逐版链」、同一结果、同一退出码集合 ``{0, 3, 4, 5}``，只在最前面多打一句
# 改用指引。
#
# 这句提示打在 **stdout**（不是 stderr）：别名与 ``--migrate`` 的 **stderr 必须逐字相同**（S13 的
# 等价性判据），而提示正是「别名多出来的那一行」；规格 T3 的 S13 行也明写「stdout 含弃用提示」。
REPAIR_TARGETS_DEPRECATION_NOTICE = (
    "提示：--repair-targets 已弃用，请改用 --migrate（同一入口、同一条链、同一个退出码）。"
)


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alibot", description="Alibot 本机工作台后端")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，只允许 loopback）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"数据目录（默认 {default_data_dir()}；也可用环境变量 {DATA_DIR_ENV} 指定）",
    )
    parser.add_argument(
        "--repair-targets",
        action="store_true",
        help=(
            "【已弃用别名】等价于 --migrate：同一入口、同一条「形态步骤 + 逐版链」、同一个退出码；"
            "运行时会先打一句「请改用 --migrate」，随后行为与 --migrate 逐字相同"
        ),
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help=(
            "【显式迁移入口】把数据目录里的旧库逐版推进到本代码的 schema 版本"
            f"（接受源版本 {MIN_SUPPORTED_SOURCE_VERSION}..{SCHEMA_VERSION}）；迁移前自动整目录备份、"
            "单事务收口、幂等。跑完直接退出、不起监听。"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "【只读预演】与 --migrate 同一条路径，但在**数据目录内**的临时副本上跑"
            "（alibot.db 连同旁边的 -wal / -shm 一起复制）：打出将执行的步骤与每步将改动的行数、"
            "原库 sha256 的前后两个读数，随后删掉副本。原库零字节改动、不建备份、不写审计。"
            "必须与 --migrate（或它的别名 --repair-targets）同用。"
        ),
    )
    return parser


def _migratable_source_version(found_version: str | None) -> int | None:
    """闸门拒收的读数里，哪些是**可迁移的源版本**（规格 D4：``[MIN, SCHEMA_VERSION)``）。

    闸门（``db.Database._assert_schema_compatible``）只在版本**恰好等于**本代码常量时放行，因此
    版本在 ``3 .. SCHEMA_VERSION - 1`` 的旧库会在这里被拒。``--migrate`` 与它的**弃用别名**
    ``--repair-targets``（本票 #94 起同入口、同版本门）是唯一把这段区间接回来的入口：读数是可解析的
    整数且落在区间内 ⇒ 交给迁移引擎；其余（低于 3 / 认不出的号 / **未来版本**）一律给闸门的原答复
    （退出码 ``3``）。
    """
    if found_version is None:
        return None
    try:
        version = int(found_version)
    except ValueError:
        return None
    if MIN_SUPPORTED_SOURCE_VERSION <= version < SCHEMA_VERSION:
        return version
    return None


def _perform_migration(data_dir: Path, from_version: int) -> int:
    """跑一次真正执行的迁移：备份 + 单事务 + 审计 + 报告（规格 D8 / D2 的 ``0`` 与 ``5``）。"""
    from . import migrate

    try:
        report = migrate.migrate_data_dir(data_dir, from_version)
    except migrate.MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return MIGRATION_FAILED_EXIT_CODE
    print(migrate.render_report(report))
    return 0


# 只读裁决的五个档位（``_LibraryVerdict.kind``）。写成 ``Literal`` 而不是裸 ``str``：这五个词是域概念，
# 拼错一个应当被类型检查器当场拦下，而不是在运行时悄悄落进最后那个 ``else``。
_LibraryVerdictKind = Literal["accepted", "shape-only", "nothing", "not-accepted", "shape-missing"]


@dataclass(frozen=True)
class _LibraryVerdict:
    """对一个**已存在**的库的只读裁决（规格 D2）：真实迁移与只读预演**共用同一份判定**。

    ``kind`` 的取值与它对应要做的事：

    - ``accepted``：库可迁（版本落在 ``[3, SCHEMA_VERSION)``）⇒ 从 ``source_version`` 起走迁移编排。
    - ``shape-only``：版本已是目标版本、但形态步骤有活（#37 那类半改名库）⇒ 走同一条编排，链为空。
    - ``nothing``：无事可做（形态完好 / 缺表但没业务数据）⇒ ``0``，不建备份、不写库。
    - ``not-accepted``：版本闸门拒绝（低于 3 / 缺版本号但有业务数据 / 认不出的号 / 未来版本）⇒ ``3``。
    - ``shape-missing``：版本匹配、但现在缺当前形态必须存在的表且有业务数据 ⇒ ``4``（还没动手）。
    """

    kind: _LibraryVerdictKind
    source_version: int = SCHEMA_VERSION
    message: str = ""


def _report_verdict(verdict: _LibraryVerdict) -> int | None:
    """把「不进迁移编排」的三档裁决打出来并给出退出码；``None`` = 这一档要走迁移编排。

    三档的落点与真跑、预演**同一个来源**：预演不在别处再抄一遍这条级联，于是「同一个库、同一个
    答复」不会因为两条路径各写一份而漂移。
    """
    if verdict.kind == "not-accepted":
        print(verdict.message, file=sys.stderr)
        return DATA_NOT_ACCEPTED_EXIT_CODE
    if verdict.kind == "shape-missing":
        # 「4 不是迁移失败」：它说这个库不被本入口处置。不做任何写入、不建备份。
        print(verdict.message, file=sys.stderr)
        return REPAIR_FAILED_EXIT_CODE
    if verdict.kind == "nothing":
        print(verdict.message)
        return 0
    return None


def _inspect_library(data_dir: Path, reported_db_path: Path | None = None) -> _LibraryVerdict:
    """只读裁决：版本闸门 → 缺表 / 有业务数据 → 形态步骤有没有活。

    判定只取既有的三个事实源（``db.Database`` 的闸门与 :meth:`has_business_data`、``repair`` 的
    ``missing_current_required_tables`` 与 ``channel_scope_gaps_of``），这里只做「事实 → 裁决」的
    映射，不新增第二套判定。全部读操作都发生在**任何写入之前**，且只读地看待 ``data_dir``。

    预演把**同一份**裁决施在副本上（``data_dir`` 传副本目录）：于是同一个库上「预演给什么答复」与
    「真跑给什么答复」同源。``reported_db_path`` 是**正文里报出的库文件**——预演读的是副本，但正文
    必须指回**原库**（规格 D3：同一个库、同一个答复），于是两条路径对这个库给出**逐字相同**的拒绝
    正文（含其中那句 ``--migrate --data-dir <数据目录>`` 指引）。
    """
    from .repair import channel_scope_gaps_of, missing_current_required_tables

    db_path = reported_db_path if reported_db_path is not None else data_dir / DATABASE_FILE_NAME
    try:
        database = Database.open_without_initializing(data_dir)  # 闸门 + **不建表**
    except SchemaVersionMismatch as exc:
        source_version = _migratable_source_version(exc.found_version)
        if source_version is None:
            # 闸门读的是副本，正文要报**原库**：按原库路径重渲染同一条拒绝（同一个库、同一个答复）。
            return _LibraryVerdict(
                "not-accepted",
                message=str(SchemaVersionMismatch(db_path, exc.found_version, exc.expected_version)),
            )
        return _LibraryVerdict("accepted", source_version=source_version)

    try:
        missing = missing_current_required_tables(database.table_names())
        # 缺表时不去问形态步骤（那正是「4 不被形态步骤当成可补齐对象」这条边界的落点）；只有形态完好
        # 才探测 channel 型作用域行有没有缺——那是「号已是本代码版本、数据还留在旧表名里」的判据。
        # 探测与「有没有业务数据」都只读，因此都发生在决定备份之前。
        gaps = () if missing else channel_scope_gaps_of(database)
        has_business_data = bool(missing) and database.has_business_data()
    finally:
        database.close()

    if missing:
        if has_business_data:
            # 「4 不是迁移失败」：它说这个库不被本入口处置。不做任何写入、不建备份。
            return _LibraryVerdict(
                "shape-missing",
                message=(
                    f"不执行迁移：{db_path} 的版本已是 {SCHEMA_VERSION}，但缺当前形态必须存在的表"
                    f"（{'、'.join(missing)}）——这不是本入口能处置的形态。未改动库文件、未建备份。"
                ),
            )
        return _LibraryVerdict(
            "nothing",
            message=(f"迁移：{db_path} 缺当前形态的表但没有业务数据，无事可做；不建备份、不凭空初始化库。"),
        )
    if gaps:
        # 形态步骤有活：版本号已经是目标版本，但 channel 型作用域数据还留在旧表名里（#37 那类半改名
        # 库的「号已到位、数据没搬」形态）。它**不是**「无事可做」——走同一条迁移编排去补，链为空。
        return _LibraryVerdict("shape-only", source_version=SCHEMA_VERSION)
    return _LibraryVerdict(
        "nothing",
        message=f"迁移：{db_path} 已是本代码的 schema 形态（版本 {SCHEMA_VERSION}），无事可做；不建备份。",
    )


def _run_migrate(data_dir: Path) -> int:
    """显式迁移入口：跑完直接退出（不起监听、不跑 uvicorn），并给出答复矩阵里的那个答复。

    接受范围 = 规格 D4 的 ``[MIN_SUPPORTED_SOURCE_VERSION, SCHEMA_VERSION]``：从 3 到 11 的
    **每一个**源版本都会被接受并逐版推进到终态。矩阵如下（判据与可观察验收按规格 D2 / D9）：

    - 没有库文件 ⇒ ``0``，**不凭空创建库**（与 ``--repair-targets`` 同一口径）。
    - 版本可解析且落在 ``[3, SCHEMA_VERSION)`` ⇒ 真正执行迁移：整目录备份 → 单事务逐版推进 →
      审计台账 → 报告；成功 ``0``、备份失败或链上失败（已回滚）⇒ ``5``。
    - 版本闸门拒绝且不是可迁移源版本（低于 3 / 版本号缺失但有业务数据 / 认不出的号 / **未来版本**）
      ⇒ :data:`DATA_NOT_ACCEPTED_EXIT_CODE`，正文与启动路径、``--repair-targets`` **逐字相同**。
    - 版本已是本代码版本、当前形态完好、且 channel 型作用域数据没有缺 ⇒ ``0``，无事可做、
      **不建备份**。
    - 版本已是本代码版本、表都在、但 channel 型作用域行还留在旧表名里（形态步骤有活）⇒ 走同一条
      迁移编排（备份 + 单事务 + 审计 + 报告），链本身可能是空的；成功 ``0``。
    - 版本已是本代码版本、但缺当前形态必须存在的表、**且**库里有业务数据 ⇒
      :data:`REPAIR_FAILED_EXIT_CODE`（复用 ``repair`` 的判据），发生在**任何写入之前**。
    - 版本已是本代码版本、缺表但**没有**业务数据 ⇒ ``0``（空库口径不变）。

    ``3`` / ``4`` 两条分支都发生在任何写入之前：闸门只读，形态判定只读——因此库文件 sha256 在这
    两条路径上都不变、也不出现新备份。「形态步骤有活」这条也先经**只读**探测（``repair`` 的
    :func:`channel_scope_gaps_of`）判出来，然后才进入会写库的迁移编排。

    裁决本身由 :func:`_inspect_library` 给出——它只做「事实 → 裁决」的映射，判据（闸门 / 缺表 /
    有业务数据 / 形态步骤有活）全部复用既有那一份；只读预演把**同一份**裁决施在副本上。
    """
    db_path = data_dir / DATABASE_FILE_NAME
    if not db_path.exists():
        # 没有库文件：迁移无事可做，也**不去创建**它（连接会凭空生成一个空库）。
        print(f"迁移：{data_dir} 里没有 {DATABASE_FILE_NAME}，没有库文件、无事可做；不会凭空创建库。")
        return 0

    verdict = _inspect_library(data_dir)
    code = _report_verdict(verdict)
    if code is not None:
        return code
    return _perform_migration(data_dir, verdict.source_version)


def _run_dry_run(data_dir: Path) -> int:
    """只读预演（``--migrate --dry-run``）：把**同一份裁决**施在数据目录内的临时副本上。

    与真跑同一条代码路径（形态步骤 + 逐版链），但一切写操作只落在副本上：副本 = ``alibot.db`` 连同
    旁边的 ``-wal`` / ``-shm``（只复制主文件会读到不完整状态），跑完整条链后**无条件删除**。

    答复沿用真跑的矩阵 ``{0, 3, 4, 5}``：``3`` / ``4`` 是与真跑**逐字相同**的正文；``0`` = 可预演
    （报告给出步骤清单、每步将改动的行数、原库 sha256 的前后两个读数）；预演在副本上发现「这条链
    跑不通」（含计划断链）⇒ ``5``——**正文与报告都要写明原库一个字节都没动**，且措辞里不能说「已
    回滚」（预演没有可回滚之物：写操作只落在副本上）。这条措辞口径是 #93 的**人定裁定**。

    预演**从不**用 SQLite 打开原库（闸门、形态判定与整条链全在副本上），所以零字节改动是构造性的。
    原库 sha256 的前后两个读数不只在成功时打：``3`` / ``4`` / 无事可做 / 预演跑不通都打——「预演没碰
    原库」是这条路每一档都要举的证。
    """
    from . import migrate

    db_path = data_dir / DATABASE_FILE_NAME
    if not db_path.exists():
        print(f"预演：{data_dir} 里没有 {DATABASE_FILE_NAME}，没有库文件、无事可做；不会凭空创建库。")
        return 0

    staged = migrate.StagedLibraryCopy(data_dir)
    chain: migrate.ChainResult | None = None
    status = 0
    try:
        with staged as copy_dir:
            verdict = _inspect_library(copy_dir, db_path)
            code = _report_verdict(verdict)
            if code is None:
                chain = migrate.dry_run_chain(copy_dir, verdict.source_version)
            else:
                status = code
    except migrate.MigrationError as exc:
        # 预演跑不通（副本建不出来 / 计划断链 / 链在副本上失败）：给「预演没能跑通」的答复 5。
        # 人定裁定：正文（stderr）与报告（stdout，render_dry_run_untouched）**都**要写明原库未改动，
        # 且不得出现「已回滚」这种属于真跑（动手之后）的措辞。
        print(str(exc), file=sys.stderr)
        if staged.has_readings:
            print(staged.untouched_note(), file=sys.stderr)
        chain = None
        status = MIGRATION_FAILED_EXIT_CODE

    report = staged.report(chain)
    if report is None:
        # 连前后读数都没有（库文件都量不成）：原库同样一个字节没动，但没有这份证据可打。
        return status
    print(
        migrate.render_dry_run_report(report)
        if chain is not None
        else migrate.render_dry_run_untouched(report)
    )
    return status


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()

    # ``--repair-targets`` 是 ``--migrate`` 的**弃用别名**（ADR-0015 §五 / 规格 D11）：两条旗标走
    # **同一个入口**（本分支）、同一条「形态步骤 + 逐版链」、同一个退出码集合 ``{0, 3, 4, 5}``。
    # 别名只多打一句改用指引（stdout），因此它与 ``--migrate`` 的 stdout 只差那一行、stderr 逐字相同。
    is_migration = args.migrate or args.repair_targets

    if args.dry_run and not is_migration:
        # 只读预演是**迁移**这条路径的修饰旗标（规格 D10 的 ``--migrate --dry-run``），单独给它是
        # 用法错误：走 argparse 既有的「用法错误 ⇒ 2」那条路，而不是落进迁移入口的 ``{0, 3, 4, 5}``
        # 裁决集合——它不是对这个库的答复，是对这次调用的答复。（#93 人定裁定 (A)。）
        parser.error(
            "--dry-run 只能与 --migrate（或它的别名 --repair-targets）同用：预演的就是迁移这条路径。"
        )

    if is_migration:
        # 显式迁移入口（及其弃用别名）：**唯一**的迁移入口，且只有显式触发才会跑到这里（任何启动
        # 路径都不会经过本分支）。它不监听任何端口，``--host`` 对它没有意义——因此它**先于** loopback
        # 闸门收口，答复集合是 {0, 3, 4, 5}（规格 D2），不会落进「非 loopback ⇒ 2」那条启动路径的
        # 语义。它在 ``create_app`` / uvicorn 之前收口，所以迁移不涉及 uvicorn、也不起任何监听（S1）。
        # ``--dry-run`` 只把这同一条路径挪到**数据目录内的临时副本**上（D10），答复矩阵不变。
        if args.repair_targets:
            print(REPAIR_TARGETS_DEPRECATION_NOTICE)
        return _run_dry_run(data_dir) if args.dry_run else _run_migrate(data_dir)

    if not _is_loopback(args.host) and os.environ.get(REMOTE_ENV) != "1":
        print(
            f"拒绝启动：{args.host} 不是 loopback 地址。首版无认证，默认不提供局域网/远程访问；"
            f"确实需要时设 {REMOTE_ENV}=1。",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from .api import create_app

    try:
        app = create_app(data_dir=data_dir)
    except SchemaVersionMismatch as exc:
        # 库的 schema 版本与本代码不匹配：**拒绝启动**，并把「库路径 + 两个版本号 + 一句指引」
        # 打到 stderr、以非 0 退出码结束。这必须发生在**组装应用之前**——既不能让它变成某个
        # 请求上的 500，也不能让进程先监听再报错。
        #
        # 这里不捕获更宽的异常：启动路径上其它失败（数据目录不可写、uvicorn 起不来……）的堆栈
        # 比一句包装过的话有用，不该被吞掉。
        print(str(exc), file=sys.stderr)
        return DATA_NOT_ACCEPTED_EXIT_CODE

    print(f"alibot 工作台： http://{args.host}:{args.port}/studio/")
    print(f"数据目录（备份即复制该目录）： {data_dir}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
