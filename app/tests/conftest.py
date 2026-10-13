"""HTTP 黑盒契约测试的公共夹具。

约定（issue #2「边界、安全与测试」）：

- 每条测试使用**独立临时数据目录**（``tmp_path``），绝不触碰用户真实数据目录
  （``~/Worksplice``，可用 ``WORKSPLICE_DATA_DIR`` 覆盖）。
- 只经 HTTP 面断言**外部可观察行为**：状态码、JSON 载荷、跨重启 / 跨实例的一致性。
  测试不 import 实现内部模块、不连数据库、不读表结构、不注入任何消息（含 ``system``）。
- 本票测试的输入只有测试自己经 HTTP 造出的**真实 human 消息**；``system`` / ``agent``
  消息在本票不存在，其未读口径由 #② / #③ 用真实消息覆盖。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from worksplice.api import create_app  # noqa: E402

# 规格固定的唯一成员行 id（issue #2 正文写明的对外身份，不是实现内部结构）。
SPEC_OWNER_ID = "owner"


@contextmanager
def backend(data_dir: Path) -> Iterator[TestClient]:
    """在 ``data_dir`` 上启动一个后端；退出时正常收尾（等价于进程退出）。

    在同一目录上重复进入 = 重启：重新进入后能看到的状态只可能来自持久化，
    不可能来自上一个进程的内存。
    """
    with TestClient(create_app(data_dir=data_dir)) as client:
        yield client


@contextmanager
def peer_backends(data_dir: Path, count: int) -> Iterator[list[TestClient]]:
    """同一数据目录上同时开 ``count`` 个后端：模拟多个客户端并发打同一个库。

    并发下「seq 唯一且无缺口」在 HTTP 面唯一可观察的窗口就在这里 —— 把并发交给真实
    写路径去产生，而不是由测试自己连数据库或注入数据。
    """
    app = create_app(data_dir=data_dir)
    with ExitStack() as stack:
        yield [stack.enter_context(TestClient(app)) for _ in range(count)]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """本用例独占的临时数据目录，用完即弃。"""
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


@pytest.fixture
def launched(data_dir: Path) -> Iterator[dict]:
    """启动一次后端；``restart()`` 先真正关掉它，再在同一数据目录上重开。"""
    stack = ExitStack()

    def launch() -> TestClient:
        return stack.enter_context(backend(data_dir))

    def restart() -> TestClient:
        stack.close()  # 当前后端正常收尾后，新后端只可能从磁盘读回状态
        return launch()

    state: dict = {"data_dir": data_dir, "client": launch(), "launch": launch, "restart": restart}
    try:
        yield state
    finally:
        stack.close()


@pytest.fixture
def client(launched: dict) -> TestClient:
    return launched["client"]


def create_channel(client: TestClient, name: str = "设计系统重构", description: str = "只读参照", **kwargs):
    return client.post("/api/channels", json={"name": name, "description": description, **kwargs})


def send(client: TestClient, channel_id: str, body: str):
    return client.post(f"/api/channels/{channel_id}/messages", json={"body": body})


def mark_read(client: TestClient, channel_id: str, seq: int):
    return client.post(f"/api/channels/{channel_id}/read", json={"seq": seq})


def messages(client: TestClient, channel_id: str, **params) -> list[dict]:
    return client.get(f"/api/channels/{channel_id}/messages", params=params).json()["messages"]


def channels(client: TestClient) -> dict[str, dict]:
    payload = client.get("/api/channels").json()
    return {channel["id"]: channel for channel in payload["channels"]}
