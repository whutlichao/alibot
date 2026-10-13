"""HTTP 黑盒契约测试的公共夹具。

每条测试使用**独立临时数据目录**（``tmp_path``），绝不触碰用户真实数据目录
（``~/Worksplice``，可用 ``WORKSPLICE_DATA_DIR`` 覆盖）。

测试只经后端 HTTP 面断言外部可观察行为，不断言内部函数或私有结构。
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from worksplice.api import create_app  # noqa: E402
from worksplice.db import DATABASE_FILE_NAME  # noqa: E402


@contextmanager
def raw_connection(data_dir: Path) -> Iterator[sqlite3.Connection]:
    """直连用例自己的临时库（只用于验证库级约束，不做业务断言）。"""
    connection = sqlite3.connect(data_dir / DATABASE_FILE_NAME)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """本用例独占的临时数据目录，用完即弃。"""
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


@pytest.fixture
def launched(data_dir: Path) -> Iterator[dict]:
    """启动一次后端；``restart()`` 在同一数据目录上重开（模拟进程重启）。"""
    apps: list[TestClient] = []

    def launch() -> TestClient:
        client = TestClient(create_app(data_dir=data_dir))
        apps.append(client)
        return client

    state: dict = {"data_dir": data_dir, "client": launch(), "launch": launch, "apps": apps}

    def restart() -> TestClient:
        apps[-1].close()
        client = launch()
        state["client"] = client
        return client

    state["restart"] = restart
    yield state

    for client in apps:
        with suppress(RuntimeError):
            client.close()


@pytest.fixture
def client(launched: dict) -> TestClient:
    return launched["client"]


def create_channel(client: TestClient, name: str = "设计系统重构", description: str = "只读参照", **kwargs):
    return client.post("/api/channels", json={"name": name, "description": description, **kwargs})


def send(client: TestClient, channel_id: str, body: str):
    return client.post(f"/api/channels/{channel_id}/messages", json={"body": body})


def mark_read(client: TestClient, channel_id: str, seq: int):
    return client.post(f"/api/channels/{channel_id}/read", json={"seq": seq})


def channels(client: TestClient) -> dict[str, dict]:
    payload = client.get("/api/channels").json()
    return {channel["id"]: channel for channel in payload["channels"]}
