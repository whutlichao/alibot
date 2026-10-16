"""命令行入口：``python -m alibot``。

默认只绑定 **loopback**（``127.0.0.1:8788``）。首版无认证，因此不提供局域网/远程访问；
确实需要时设 ``ALIBOT_ALLOW_REMOTE=1`` 显式越界，并自行承担风险。
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

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
            "【手动修复入口】把当前 schema 库里缺失的 channel 型 target 作用域数据补齐"
            "（影子行 / 成员 / 已读 / 预算），幂等、可自校验；不会自动执行、不写迁移脚本"
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
    return parser


def _migratable_source_version(found_version: str | None) -> int | None:
    """闸门拒收的读数里，哪些是**可迁移的源版本**（规格 D4：``[MIN, SCHEMA_VERSION)``）。

    闸门（``db.Database._assert_schema_compatible``）只在版本**恰好等于**本代码常量时放行，因此
    版本在 ``3 .. SCHEMA_VERSION - 1`` 的旧库会在这里被拒。``--migrate`` 是唯一把这段区间接回来的
    入口：读数是可解析的整数且落在区间内 ⇒ 交给迁移引擎；其余（低于 3 / 认不出的号 / **未来版本**）
    一律给闸门的原答复（退出码 ``3``）。
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
    两条路径上都不变、也不出现新备份。「形态步骤有活」这条也先经**只读**探测（``migrate`` 的
    :func:`channel_scope_gaps`）判出来，然后才进入会写库的迁移编排。
    """
    # 复用既有判据（票面 Constraints）：形态清单与「有业务数据」都取自 ``repair`` / ``Database``
    # 已有的那一份，本入口不新增第二套判定，自己只做「事实 → 退出码」的映射。形态步骤的「有缺」探测
    # 与链共用 ``migrate.channel_scope_gaps``——同一份判据，不是第二套。
    from . import migrate
    from .repair import missing_current_required_tables

    db_path = data_dir / DATABASE_FILE_NAME
    if not db_path.exists():
        # 没有库文件：迁移无事可做，也**不去创建**它（连接会凭空生成一个空库）。
        print(f"迁移：{data_dir} 里没有 {DATABASE_FILE_NAME}，没有库文件、无事可做；不会凭空创建库。")
        return 0

    try:
        database = Database.open_without_initializing(data_dir)  # 闸门 + **不建表**
    except SchemaVersionMismatch as exc:
        source_version = _migratable_source_version(exc.found_version)
        if source_version is not None:
            return _perform_migration(data_dir, source_version)
        print(str(exc), file=sys.stderr)
        return DATA_NOT_ACCEPTED_EXIT_CODE

    try:
        existing = database.table_names()
        missing = missing_current_required_tables(existing)
        # 缺表时不去问形态步骤（那正是「4 不被形态步骤当成可补齐对象」这条边界的落点）；只有形态完好
        # 才探测 channel 型作用域行有没有缺——那是「号已是本代码版本、数据还留在旧表名里」的判据。
        gaps = () if missing else migrate.channel_scope_gaps(database._conn)
        has_business_data = database.has_business_data() if missing else False
    finally:
        database.close()

    if missing:
        if has_business_data:
            # 「4 不是迁移失败」：它说这个库不被本入口处置。不做任何写入、不建备份。
            print(
                f"不执行迁移：{db_path} 的版本已是 {SCHEMA_VERSION}，但缺当前形态必须存在的表"
                f"（{'、'.join(missing)}）——这不是本入口能处置的形态。未改动库文件、未建备份。",
                file=sys.stderr,
            )
            return REPAIR_FAILED_EXIT_CODE
        print(f"迁移：{db_path} 缺当前形态的表但没有业务数据，无事可做；不建备份、不凭空初始化库。")
        return 0

    if gaps:
        # 形态步骤有活：版本号已经是目标版本，但 channel 型作用域数据还留在旧表名里（#37 那类半改名
        # 库的「号已到位、数据没搬」形态）。它**不是**「无事可做」——走同一条迁移编排去补，链为空。
        return _perform_migration(data_dir, SCHEMA_VERSION)

    print(f"迁移：{db_path} 已是本代码的 schema 形态（版本 {SCHEMA_VERSION}），无事可做；不建备份。")
    return 0


def _run_repair(data_dir: Path) -> int:
    """执行手动补齐并打印报告；返回进程退出码。

    两类拒绝各占一个退出码，且都发生在**写入之前**：

    - ``SchemaVersionMismatch``（库的版本不是本代码需要的）⇒ :data:`DATA_NOT_ACCEPTED_EXIT_CODE`，
      与迁移入口、与启动路径**同一个答复与同一个退出码**：本入口**不**充当迁就旧库的后门。
    - ``RepairError``（库版本匹配，但不是本入口能处置的形态）⇒ :data:`REPAIR_FAILED_EXIT_CODE`。
      与 ``--migrate`` 同判：都是「库不被本入口处置」，都发生在任何写入之前。
    """
    from .repair import RepairError, render_outcome, repair_target_rows

    try:
        outcome = repair_target_rows(data_dir)
    except SchemaVersionMismatch as exc:
        print(str(exc), file=sys.stderr)
        return DATA_NOT_ACCEPTED_EXIT_CODE
    except RepairError as exc:
        print(f"未执行修复：{exc}", file=sys.stderr)
        return REPAIR_FAILED_EXIT_CODE

    print(render_outcome(outcome))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()

    if args.migrate:
        # 显式迁移入口：**唯一**的迁移入口，且只有显式触发才会跑到这里（任何启动路径都不会经过本
        # 分支）。它不监听任何端口，``--host`` 对它没有意义——因此它**先于** loopback 闸门收口，
        # 答复集合是 {0, 3, 4, 5}（规格 D2），不会落进「非 loopback ⇒ 2」那条启动路径的语义。
        # 它在 ``create_app`` / uvicorn 之前收口，所以迁移不涉及 uvicorn、也不起任何监听（S1）。
        return _run_migrate(data_dir)

    if not _is_loopback(args.host) and os.environ.get(REMOTE_ENV) != "1":
        print(
            f"拒绝启动：{args.host} 不是 loopback 地址。首版无认证，默认不提供局域网/远程访问；"
            f"确实需要时设 {REMOTE_ENV}=1。",
            file=sys.stderr,
        )
        return 2

    if args.repair_targets:
        # 手动修复入口：**显式**触发才能跑到这里（其它任何启动路径都不会经过本分支）。
        # 它在 ``create_app`` 之前就收口，因此修复不涉及 uvicorn、也不起任何监听。
        return _run_repair(data_dir)

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
