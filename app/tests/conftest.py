"""HTTP 黑盒契约测试的公共夹具。

约定（issue #2 / #3「边界、安全与测试」）：

- 每条测试使用**独立临时数据目录**（``tmp_path``），绝不触碰用户真实数据目录
  （``~/Alibot``，可用 ``ALIBOT_DATA_DIR`` 覆盖）。
- 只经 HTTP 面与事件流断言**外部可观察行为**：状态码、JSON 载荷、SSE 帧、
  跨重启 / 跨实例的一致性。测试不 import 实现内部模块、不连数据库、不读表结构。
- 测试注入**脚本化替身**（``tests/scripted.py``）与**手动调度器**，于是四种行为
  （回应 / 忽略 / 失败 / 延迟）可固定，且不联网、零成本、零付费调用。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from alibot.api import create_app  # noqa: E402

from tests.scripted import ManualScheduler, ScriptedAgentSeam  # noqa: E402

# 规格固定的唯一成员行 id（issue #2 正文写明的对外身份，不是实现内部结构）。
SPEC_OWNER_ID = "owner"

# 本机 provider：不需要远端数据流告知也能启用。验收里用它避免任何确认步骤的干扰。
LOCAL_MODEL_ID = "ollama:qwen2.5"
# 远端 provider：启用前需要一次数据流告知确认。
REMOTE_MODEL_ID = "dashscope:qwen-max"


@pytest.fixture(autouse=True, scope="session")
def _sanitize_proxy_environment() -> Iterator[None]:
    """把宿主环境的 `no_proxy` / `NO_PROXY` 收干净：这些测试只跟 127.0.0.1 说话。

    触发场景（本机真实踩到）：环境里 `NO_PROXY=localhost,127.0.0.1,::1,[::1]` 时，httpx 解析
    代理绕过表会把 `[::1]` 当成 URL，于是连**回环**请求都报
    ``InvalidURL: Invalid port: ':1]'``。它发生在 seam 自己建的客户端上（那条路径不吃
    ``trust_env=False``），因此 `test_agent_seam_client_lifecycle` 与本机 stub provider 的用例
    会以「环境」而不是「代码」为原因失败——基线也跟着红。

    只摘掉**带方括号的 IPv6 条目**（不是 host / domain，注定解析失败），其余代理条目照旧：
    真模型冒烟若要走用户自己的代理，行为不变。
    """
    keys = ("no_proxy", "NO_PROXY")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            value = os.environ.get(key)
            if not value:
                continue
            kept = [entry for entry in value.split(",") if "[" not in entry and "]" not in entry]
            os.environ[key] = ",".join(kept)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_app(
    data_dir: Path, scheduler: ManualScheduler | None = None, seam: ScriptedAgentSeam | None = None
):
    return create_app(
        data_dir=data_dir,
        scheduler=scheduler if scheduler is not None else ManualScheduler(),
        seam=seam if seam is not None else ScriptedAgentSeam(),
        # 到期投递器同样默认注入一个**惰性替身**（同 ManualScheduler 的理由）：行为测试必须确定，
        # 不能因为「今天恰巧过了某条提醒的到期时刻」而多出系统事件消息。真实后台轮询由
        # ``test_reminder_delivery.py`` 自己在**单独的**用例里注入（那两条恰好要验证启动先扫与关停）。
        reminder_delivery=InertReminderDeliveryDriver,
    )


class InertReminderDeliveryDriver:
    """测试默认的到期驱动器：既不做启动扫、也不轮询（只有显式注入时刻才会投递）。

    它让「提醒存在」与「提醒到点」在既有用例里彻底解耦：一个用例只要不主动喂时刻，就绝不会因为
    跑在哪个钟点而多出消息。这与 ``ManualScheduler`` 把「什么时候跑完一轮」交给测试是同一条纪律。
    """

    THREAD_NAME = "alibot-reminder-poller"

    def __init__(self, trigger: Any) -> None:
        self.trigger = trigger

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def deliver(self, now: Any) -> Any:
        return self.trigger.deliver(now)


@contextmanager
def backend(data_dir: Path, **kwargs) -> Iterator[TestClient]:
    """在 ``data_dir`` 上启动一个后端；退出时正常收尾（等价于进程退出）。

    在同一目录上重复进入 = 重启：重新进入后能看到的状态只可能来自持久化，
    不可能来自上一个进程的内存。
    """
    with TestClient(build_app(data_dir, **kwargs)) as client:
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
def harness(data_dir: Path) -> Iterator[dict]:
    """可控后端：手动调度器 + 脚本化替身 + 可重启。

    ``launch()`` / ``restart()`` 与 #2 的语义一致（重启 = 换一个进程读同一数据目录）。
    """
    stack = ExitStack()
    state: dict = {"data_dir": data_dir, "scheduler": ManualScheduler(), "seam": ScriptedAgentSeam()}

    def launch() -> TestClient:
        state["scheduler"] = ManualScheduler()
        state["seam"] = ScriptedAgentSeam(
            behaviors=dict(state["seam"].behaviors), replies=dict(state["seam"].replies)
        )
        return stack.enter_context(backend(data_dir, scheduler=state["scheduler"], seam=state["seam"]))

    def restart() -> TestClient:
        stack.close()  # 当前后端正常收尾后，新后端只可能从磁盘读回状态
        return launch()

    state["client"] = launch()
    state["launch"] = launch
    state["restart"] = restart
    try:
        yield state
    finally:
        stack.close()


@pytest.fixture
def client(harness: dict) -> TestClient:
    return harness["client"]


@pytest.fixture
def launched(harness: dict) -> dict:
    """与 ``client`` 同一后端，但测试需要 ``restart()`` / ``launch()`` 时用它。"""
    return harness


@contextmanager
def live_backend(data_dir: Path, **kwargs) -> Iterator[str]:
    """在**真实 HTTP 服务**上启动后端，返回 ``base_url``。

    为什么事件流测试需要真 socket：``TestClient`` 会把整个响应体读完才返回，而事件流是持续的。
    所以「按 seq 追加 / 重连补拉 / 正在回复」这些必须经真实 HTTP 的**部分读取**才能取证——
    这也更接近浏览器真正干的事（``EventSource`` 就是逐帧读的）。
    """
    import threading
    import time

    import uvicorn

    config = uvicorn.Config(
        build_app(data_dir, **kwargs), host="127.0.0.1", port=0, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(1000):
        if server.started and server.servers:
            break
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def live(data_dir: Path) -> Iterator[dict]:
    """真实 HTTP 后端 + 可控调度器/替身；需要事件流的用例用它。"""
    import httpx

    scheduler = ManualScheduler()
    seam = ScriptedAgentSeam()
    state: dict = {"data_dir": data_dir, "scheduler": scheduler, "seam": seam}
    with live_backend(data_dir, scheduler=scheduler, seam=seam) as base_url:
        state["base_url"] = base_url
        # ``trust_env=False``：测试只跟 127.0.0.1 说话，绝不让进程环境里的 HTTP(S)_PROXY /
        # ALL_PROXY 把本地请求劫持到代理上（那会让「读本地事件流」变成一个环境相关的偶然）。
        # ``read=None``：事件流是持续的，帧间的 keep-alive 间隔不能被读超时误判成失败。
        state["http"] = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(30.0, read=None), trust_env=False
        )
        try:
            yield state
        finally:
            state["http"].close()


def sse_frames(
    http: Any, target_id: str, after_seq: int = 0, limit: int = 60, target_collection: str = "channels"
) -> list[dict]:
    """读一段事件流，返回解析后的事件列表。

    读到 replay 之后的第一个 keep-alive（``:`` 开头的注释帧）就主动断开：那就是「历史发完了」的
    信号，之后是实时推送，不属于「补拉」的断言范围。
    """
    import json

    events: list[dict] = []
    with http.stream(
        "GET", f"/api/{target_collection}/{target_id}/events", params={"after_seq": after_seq}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            text = line.decode() if isinstance(line, bytes) else line
            if text.startswith("data: "):
                events.append(json.loads(text[len("data: ") :]))
                if len(events) >= limit:
                    break
            elif text.startswith(":") and events:
                break
    return events


def create_channel(client: TestClient, name: str = "设计系统重构", description: str = "只读参照", **kwargs):
    return client.post("/api/channels", json={"name": name, "description": description, **kwargs})


def send(client: TestClient, channel_id: str, body: str, **payload):
    """发送一条人类消息；``payload`` 可带既有契约的其它字段（``task_ref`` / ``attachment_ids``）。"""
    return client.post(f"/api/channels/{channel_id}/messages", json={"body": body, **payload})


def mark_read(client: TestClient, channel_id: str, seq: int):
    return client.post(f"/api/channels/{channel_id}/read", json={"seq": seq})


def messages(client: TestClient, channel_id: str, **params) -> list[dict]:
    return client.get(f"/api/channels/{channel_id}/messages", params=params).json()["messages"]


def channels(client: TestClient) -> dict[str, dict]:
    payload = client.get("/api/channels").json()
    return {channel["id"]: channel for channel in payload["channels"]}


def create_agent(
    client: TestClient,
    name: str = "小明",
    role: str = "前端工程师",
    model_id: str = LOCAL_MODEL_ID,
    system_prompt: str = "你是前端工程师。",
):
    return client.post(
        "/api/agents",
        json={"name": name, "role": role, "model_id": model_id, "system_prompt": system_prompt},
    )


def agents(client: TestClient) -> list[dict]:
    return client.get("/api/agents").json()["agents"]


def outcomes(client: TestClient, channel_id: str, **params) -> list[dict]:
    return client.get(f"/api/channels/{channel_id}/round-outcomes", params=params).json()["round_outcomes"]


def budget(client: TestClient, channel_id: str) -> dict:
    """该频道当前人类世代的有界响应预算（后端持久事实，只读端点）。"""
    return client.get(f"/api/channels/{channel_id}/budget").json()["budget"]


def drain(harness: dict, limit: int = 100) -> list:
    """把待跑的轮次跑完（含它们自己唤醒出来的下一轮）。"""
    return harness["scheduler"].drain(limit=limit)


def channel_with_agent(
    client: TestClient,
    agent_id: str,
    name: str = "协作频道",
    description: str = "验收用",
) -> dict:
    return create_channel(client, name=name, description=description, member_ids=[agent_id]).json()["channel"]


def sse_events(client: TestClient, channel_id: str, after_seq: int = 0) -> list[dict]:
    """把事件流的响应读成事件列表（流会在测试结束时被关闭）。

    只用于「补拉」这一类有限长度的断言：``after_seq`` 之后的内容（消息 + 轮次结果 + 状态点快照）
    发完后流会进入 keep-alive 等待，因此这里只读前几帧就主动关闭。
    """
    import json

    events: list[dict] = []
    with client.stream(
        "GET", f"/api/channels/{channel_id}/events", params={"after_seq": after_seq}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line:
                continue
            text = line.decode() if isinstance(line, bytes) else line
            if text.startswith("data: "):
                events.append(json.loads(text[len("data: ") :]))
                # 补拉部分发完后会出现 keep-alive 注释；读到它就不再往下等。
            if len(events) >= 60:
                break
            if text.startswith(":"):
                break
    return events
