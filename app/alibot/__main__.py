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

from .db import DATA_DIR_ENV, DEFAULT_PORT, SchemaVersionMismatch, default_data_dir

REMOTE_ENV = "ALIBOT_ALLOW_REMOTE"

# 数据目录里是一个本代码不接受的库（schema 版本不匹配）时的退出码。
# 与上面的「非 loopback 拒绝启动」区分开：运维看一眼退出码就知道是哪一类拒绝。
# 启动路径与 ``--repair-targets`` **共用**它：同一个库，两条路径给同一个答复。
DATA_NOT_ACCEPTED_EXIT_CODE = 3

# ``--repair-targets`` 自己发现「这个库不是本入口能处置的那种」时的退出码。
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
    return parser


def _run_repair(data_dir: Path) -> int:
    """执行手动补齐并打印报告；返回进程退出码。

    两类拒绝各占一个退出码，且都发生在**写入之前**：

    - ``SchemaVersionMismatch``（库的版本不是本代码需要的）⇒ :data:`DATA_NOT_ACCEPTED_EXIT_CODE`，
      与主修、与启动路径同义：本入口**不**充当迁就旧库的后门。
    - ``RepairError``（库版本匹配，但不是本入口能处置的形态）⇒ :data:`REPAIR_FAILED_EXIT_CODE`。
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

    if not _is_loopback(args.host) and os.environ.get(REMOTE_ENV) != "1":
        print(
            f"拒绝启动：{args.host} 不是 loopback 地址。首版无认证，默认不提供局域网/远程访问；"
            f"确实需要时设 {REMOTE_ENV}=1。",
            file=sys.stderr,
        )
        return 2

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()

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
