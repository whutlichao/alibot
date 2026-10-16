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
# 它与「迁移失败、已回滚」是两回事：前者是「还没动手」，后者是「动手了、失败并回滚」。本版尚无迁移
# 步骤，因此还不会产生后者（那是 #90 / #91 的范围）；到那时它会另占一个码，不复用本常量。
REPAIR_FAILED_EXIT_CODE = 4


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
            "【显式迁移入口】把数据目录里的库推进到本代码的 schema 版本；跑完直接退出、不起监听。"
            "本版尚未携带迁移步骤，旧版本库仍按退出码 3 拒绝并给出备份指引"
        ),
    )
    return parser


def _run_migrate(data_dir: Path) -> int:
    """显式迁移入口：跑完直接退出（不起监听、不跑 uvicorn），并给出答复矩阵里的那个答复。

    本版（#89）**尚无任何迁移步骤**，因此接受范围为空：迁移链覆盖不到任何旧版本。矩阵如下
    （判据与可观察验收按规格 D2 / D9）：

    - 没有库文件 ⇒ ``0``，**不凭空创建库**（与 ``--repair-targets`` 同一口径）。
    - 版本闸门拒绝（版本低于 3 / 版本号缺失但有业务数据 / 认不出的号 / 未来版本，以及 ① 阶段尚无
      迁移步骤时的 ``3``..``SCHEMA_VERSION - 1``）⇒ :data:`DATA_NOT_ACCEPTED_EXIT_CODE`，正文与
      启动路径、``--repair-targets`` **逐字相同**。
    - 版本已是本代码版本、当前形态完好 ⇒ ``0``，无事可做、**不建备份**。
    - 版本已是本代码版本、但缺当前形态必须存在的表、**且**库里有业务数据 ⇒
      :data:`REPAIR_FAILED_EXIT_CODE`（复用 ``repair`` 的判据），发生在**任何写入之前**。
    - 版本已是本代码版本、缺表但**没有**业务数据 ⇒ ``0``（空库口径不变）。

    所有分支都发生在任何写入之前：闸门只读，形态判定只读，本版也不执行任何迁移步骤——因此库文件
    sha256 在每条路径上都不变。等 #90 把 ``3 → 4`` 一步落地后，接受范围才变为链的实际覆盖范围。
    """
    # 复用既有判据（票面 Constraints）：形态清单与「有业务数据」都取自 ``repair`` / ``Database``
    # 已有的那一份，本入口不新增第二套判定，自己只做「事实 → 退出码」的映射。
    from .repair import missing_current_required_tables

    db_path = data_dir / DATABASE_FILE_NAME
    if not db_path.exists():
        # 没有库文件：迁移无事可做，也**不去创建**它（连接会凭空生成一个空库）。
        print(f"迁移：{data_dir} 里没有 {DATABASE_FILE_NAME}，没有库文件、无事可做；不会凭空创建库。")
        return 0

    try:
        database = Database.open_without_initializing(data_dir)  # 闸门 + **不建表**
    except SchemaVersionMismatch as exc:
        print(str(exc), file=sys.stderr)
        return DATA_NOT_ACCEPTED_EXIT_CODE

    try:
        missing = missing_current_required_tables(database.table_names())
        if not missing:
            print(f"迁移：{db_path} 已是本代码的 schema 形态（版本 {SCHEMA_VERSION}），无事可做；不建备份。")
            return 0
        if database.has_business_data():
            # 「4 不是迁移失败」：它说这个库不被本入口处置。不做任何写入、不建备份。
            print(
                f"不执行迁移：{db_path} 的版本已是 {SCHEMA_VERSION}，但缺当前形态必须存在的表"
                f"（{'、'.join(missing)}）——这不是本入口能处置的形态。未改动库文件、未建备份。",
                file=sys.stderr,
            )
            return REPAIR_FAILED_EXIT_CODE
        print(f"迁移：{db_path} 缺当前形态的表但没有业务数据，无事可做；不建备份、不凭空初始化库。")
        return 0
    finally:
        database.close()


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
