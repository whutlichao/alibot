"""命令行入口：``python -m worksplice``。

默认只绑定 **loopback**（``127.0.0.1:8788``）。首版无认证，因此不提供局域网/远程访问；
确实需要时设 ``WORKSPLICE_ALLOW_REMOTE=1`` 显式越界，并自行承担风险。
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

from .db import DATA_DIR_ENV, DEFAULT_PORT, default_data_dir

REMOTE_ENV = "WORKSPLICE_ALLOW_REMOTE"


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worksplice", description="Worksplice 本机工作台后端")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，只允许 loopback）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"数据目录（默认 {default_data_dir()}；也可用环境变量 {DATA_DIR_ENV} 指定）",
    )
    return parser


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

    import uvicorn

    from .api import create_app

    app = create_app(data_dir=data_dir)
    print(f"worksplice 工作台： http://{args.host}:{args.port}/studio/")
    print(f"数据目录（备份即复制该目录）： {data_dir}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
