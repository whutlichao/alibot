"""Alibot 应用后端（Python，HTTP 人类面）。

首版只做「本机单用户 + 浏览器工作台」：后端默认只监听 loopback，
浏览器只经 HTTP 读写，业务状态的唯一正本是后端单文件库。
"""

from __future__ import annotations

from .db import (
    DEFAULT_DATA_DIR_NAME,
    DEFAULT_PORT,
    OWNER_AVATAR_SLOT,
    OWNER_DISPLAY_NAME,
    OWNER_ID,
    Database,
    DomainError,
    NotFound,
    SchemaVersionMismatch,
    default_data_dir,
    open_database,
)

__all__ = [
    "DEFAULT_DATA_DIR_NAME",
    "DEFAULT_PORT",
    "OWNER_AVATAR_SLOT",
    "OWNER_DISPLAY_NAME",
    "OWNER_ID",
    "Database",
    "DomainError",
    "NotFound",
    "SchemaVersionMismatch",
    "default_data_dir",
    "open_database",
]
