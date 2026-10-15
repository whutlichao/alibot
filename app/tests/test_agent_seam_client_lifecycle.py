"""issue #30 回归：``AgentScopeAgentSeam`` 每轮一个 event loop，provider 客户端必须在本轮内关闭。

为什么必须是**真实** seam：缺陷落在「一轮的 loop 关掉之后，本轮建出来的 provider 客户端才被回收」
这条生命周期上，而 ``ScriptedAgentSeam`` 根本不建客户端 —— 它在替身路径上永远不可见。

为什么不需要真模型、不联网：stub 端点跑在进程内的 ``http.server``（绑 ``127.0.0.1:0``），只回一份
固定的 OpenAI 兼容 chat completion。它像真端点一样**保持 keep-alive**（``HTTP/1.1`` +
``Content-Length``），于是一次请求之后连接池里真的挂着一根属于本轮 loop 的 socket —— 这是复现的前提。

两条判据（互为佐证）：

1. **症状判据**（与 issue #30 的原始输出逐字对应）：轮次返回后在一个**新的** loop 里强制 GC，
   断言收集到的 asyncio 出口里**不出现** ``Event loop is closed``。跨 loop 回收时 ``httpx2`` 的
   ``transport.close()`` → ``loop.call_soon()`` → ``_check_closed()`` 会把这条文本送到某个出口。
2. **直接判据**（第二条用例）：记录 ``httpx2.AsyncClient.aclose()`` 被调用时所在的 loop，
   断言它在 ``run_round`` **返回之前**就已经被调用过 —— 也就是关闭发生在 ``asyncio.run`` 仍在运行的
   那个 loop 里。等价性：缺陷的定义就是「客户端在 ``run_round`` 返回时仍未被关闭，被留给之后的某个
   loop 去收」；而本轮 loop 一旦关闭，任何后续回收都只可能发生在别的 loop 里。于是「返回前已关闭」
   与「跨 loop 关闭」互斥，前者成立即后者不可能发生。修复前这条判据为假（客户端仍活着）。

**关于仓库「测试只断言外部可观察行为」这条约定的例外**：`tests/conftest.py` 的天具是给
「HTTP 黑盒契约测试」用的，它断言的是经 HTTP 面可观察的东西。本文件是**接缝级生命周期测试**：
被测对象是 `AgentScopeAgentSeam.run_round` 这个**适配接缝本身**（票面指定的 target），不是协作域
行为；而这类缺陷在 HTTP 面上**不可见**（issue #30 正本：它不写协作域正本，消息与轮次结论
完全不受影响），所以无法经 HTTP 面取证。它与同样 import 实现模块的 `test_agent_smoke.py`
（README 已登记的「默认跳过的真模型冒烟测试」）是同类例外，区别在于本文件跑的是**本机 stub、
零成本**，因此进默认 `pytest` 也是安全的。
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from alibot.agent_seam import (
    AgentScopeAgentSeam,
    SetTaskStatusAction,
    TaskActionContext,
    TaskAgentOption,
    TransferTaskAction,
)

# 跨 loop 关闭时真正被打到 stderr 的那句话（issue #30 的原始症状）。
CLOSED_LOOP_MARKER = "Event loop is closed"

# stub 端点回的固定回复：一轮 ↔ 一条 Msg，正文即这里的 content。
STUB_REPLY = "收到，这是本机 stub 的回复。"


class _StubProviderHandler(BaseHTTPRequestHandler):
    """进程内 provider 端点：keep-alive + 固定的 OpenAI 兼容响应。"""

    # HTTP/1.1 + Content-Length = 连接被复用，连接池里真的留住活连接（复现的前提）。
    protocol_version = "HTTP/1.1"

    # 端点自证：测试用它断言真的发生过一次 HTTP 往返（否则「不报错」可能只是因为压根没连）。
    # ClassVar 是刻意的：这份共享状态属于端点类本身，不是某个 handler 实例的状态。
    requests: ClassVar[list[str]] = []
    request_bodies: ClassVar[list[dict[str, Any]]] = []
    # 非 0 时端点回这个状态码（失败路径用它把一轮逼成 failed）。
    fail_status: ClassVar[int] = 0
    # 有值时首轮返回该结构化 tool call，后续轮次回复固定文本。
    tool_call: ClassVar[dict[str, Any] | None] = None
    tool_call_sent: ClassVar[bool] = False

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        request_body = json.loads(self.rfile.read(length))
        type(self).requests.append(self.path)
        type(self).request_bodies.append(request_body)
        if type(self).fail_status:
            # 保持 keep-alive 地回错误（不发送 Connection: close）：连接仍然留在连接池里，
            # 于是「客户端有没有被关掉」这件事仍然可观察。
            self._send_json(
                {"error": {"message": "stub 端点按用例设定拒绝这一轮", "type": "invalid_request_error"}},
                status=type(self).fail_status,
            )
            return
        if type(self).tool_call is not None and not type(self).tool_call_sent:
            type(self).tool_call_sent = True
            tool_call = type(self).tool_call
            self._send_json(
                {
                    "id": "chatcmpl-issue-26-tool-stub",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "stub-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-task-action",
                                        "type": "function",
                                        "function": {
                                            "name": tool_call["name"],
                                            "arguments": json.dumps(tool_call["arguments"]),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
            return
        self._send_json(
            {
                "id": "chatcmpl-issue-30-stub",
                "object": "chat.completion",
                "created": 0,
                "model": "stub-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": STUB_REPLY},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: Any) -> None:
        """静音：stderr / 日志是本次断言要独占的通道。"""
        return


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """本机 stub provider 端点：返回 OpenAI 兼容的 ``base_url``，并把凭证指到它。"""
    _StubProviderHandler.requests = []
    _StubProviderHandler.request_bodies = []
    _StubProviderHandler.fail_status = 0
    _StubProviderHandler.tool_call = None
    _StubProviderHandler.tool_call_sent = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubProviderHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    # seam 的凭证窄入口只读进程环境变量；base_url 由 openai SDK 自己的 OPENAI_BASE_URL 兜底。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-issue-30-stub")
    monkeypatch.setenv("OPENAI_BASE_URL", base_url)
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@contextmanager
def cross_loop_failure_probe() -> Iterator[list[str]]:
    """收集「跨 loop 回收」类错误会流出的所有通道。

    一条 ``RuntimeError('Event loop is closed')`` 可能出现在三处：某个 loop 的异常处理器、
    ``asyncio`` 日志（``Task exception was never retrieved``）、或是它逃到线程钩子。
    三条一并接住，断言才不会因为「报错恰好走了另一条路」而假绿。
    """
    seen: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(self.format(record))

    collector = _Collector()
    collector.setFormatter(logging.Formatter("%(message)s"))
    asyncio_logger = logging.getLogger("asyncio")
    # 只放行本用例要看得见的那条：``asyncio`` 的 DEBUG 会把「Using selector」也收进来，
    # 那是噪声，不是症状。
    previous_level = asyncio_logger.level
    asyncio_logger.addHandler(collector)
    asyncio_logger.setLevel(logging.ERROR)

    previous_thread_hook = threading.excepthook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        seen.append(f"threading.excepthook: {args.exc_type.__name__}: {args.exc_value}")
        previous_thread_hook(args)

    threading.excepthook = thread_hook
    try:
        yield seen
    finally:
        threading.excepthook = previous_thread_hook
        asyncio_logger.removeHandler(collector)
        asyncio_logger.setLevel(previous_level)


def collect_in_fresh_loop(seen: list[str]) -> None:
    """在**新的** loop 里强制 GC，把「上一轮留下的客户端」逼到回收。

    这正是缺陷的触发条件：客户端若跨过轮次仍然活着（被引用环拖到 GC 才回收），它的 ``__del__``
    会在**这个** loop 里 ``create_task(self.aclose())``，去关一个属于**上一轮已关闭 loop** 的
    socket transport。修复后客户端在本轮 loop 内已被显式关闭，这里回收时无事发生。
    """

    def handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        seen.append(str(context.get("message")) + " " + str(context.get("exception")))

    async def collect() -> None:
        for _ in range(4):
            gc.collect()
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        for _ in range(4):
            gc.collect()
            await asyncio.sleep(0.05)

    loop = asyncio.new_event_loop()
    # 异常处理器装在这个 loop 上（而不是依赖默认的日志出口）：Task 可能在 loop 关闭之后才被回收，
    # 而 ``call_exception_handler`` 不检查 loop 是否已关闭，所以这个钩子仍然接得住。
    loop.set_exception_handler(handler)
    try:
        loop.run_until_complete(collect())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    gc.collect()


@contextmanager
def provider_client_close_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[asyncio.AbstractEventLoop]]:
    """记录 provider 连接池是在**哪个 loop** 里被关掉的（比字符串匹配更直接的判据）。

    所有触发者（openai / anthropic / ollama，以及各 provider 派生出来的 chat model）的连接池都落在
    ``httpx2.AsyncClient`` 上，所以打在这一层的 ``aclose`` 就能同时覆盖各家的关闭入口。
    """
    import httpx2

    calls: list[asyncio.AbstractEventLoop] = []
    original_aclose = httpx2.AsyncClient.aclose

    async def spy(self: httpx2.AsyncClient) -> None:
        calls.append(asyncio.get_running_loop())
        await original_aclose(self)

    # 用 ``monkeypatch.context()`` 而不是 ``monkeypatch.undo()``：后者会撤销**整个函数级** fixture 的
    # 补丁，包括 ``stub_provider`` 设的 ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` 环境变量。
    with monkeypatch.context() as patch:
        patch.setattr(httpx2.AsyncClient, "aclose", spy)
        yield calls


def board_inputs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    agent = {
        "id": "mem_issue_30",
        "display_name": "小明",
        "role": "前端工程师",
        "model_id": "openai:stub-model",
        "system_prompt": "你只回一句极短的确认。",
        "enabled": True,
    }
    messages = [
        {
            "seq": 1,
            "kind": "human",
            "body": "在吗？",
            "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
        }
    ]
    return agent, messages


def test_round_closes_provider_client_within_its_own_event_loop(stub_provider: str) -> None:
    """一轮结束后不残留跨 loop 的未回收任务：新 loop 里 GC 不再打 ``Event loop is closed``。"""
    agent, messages = board_inputs()
    seam = AgentScopeAgentSeam(timeout_seconds=10.0)

    with cross_loop_failure_probe() as seen:
        outcome = seam.run_round(agent, messages, mentioned=True, target_id="ch_issue_30")
        collect_in_fresh_loop(seen)

    # 先确认这一轮真的走完了整条链路（建 client → 真发 HTTP → 收回复），而不是因为失败而「安静」。
    assert outcome.status == "replied", outcome.reason
    assert STUB_REPLY in outcome.messages[0].body
    assert _StubProviderHandler.requests, "stub 端点没有收到请求：本用例必须在真实 HTTP 往返下取证"

    transcript = "\n".join(seen)
    assert CLOSED_LOOP_MARKER not in transcript, f"轮次结束后仍出现跨 loop 回收失败：\n{transcript}"


def test_failed_round_also_closes_provider_client(
    stub_provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败路径同样在**本轮 loop 内**关闭客户端：关闭落在 ``finally``，而不是只在成功路径上。

    端点回 400，Agent 的 reply 因此抛异常 → 本轮走 failed 分支；客户端在异常**之前**就建好了
    （请求真的发出去过），所以这条路径必须同样把连接池收干净。
    """
    _StubProviderHandler.fail_status = 400
    agent, messages = board_inputs()
    seam = AgentScopeAgentSeam(timeout_seconds=10.0)

    with provider_client_close_probe(monkeypatch) as calls, cross_loop_failure_probe() as seen:
        outcome = seam.run_round(agent, messages, mentioned=True, target_id="ch_issue_30")
        # 关键时点：``run_round`` 已返回，本轮那个 loop 也已经关闭。
        closed_before_return = list(calls)
        collect_in_fresh_loop(seen)

    assert outcome.status == "failed", outcome.reason
    assert _StubProviderHandler.requests, "stub 端点没有收到请求：失败必须发生在客户端已建好之后"

    # 直接判据：客户端在轮次返回之前就被关掉了（于是关闭必定发生在本轮那个 loop 内）。
    assert closed_before_return, (
        "run_round 返回时这个 provider 客户端仍未被关闭 —— 它被留给了之后的某个 loop 去回收，"
        "而这正是 issue #30 的缺陷"
    )
    # 反向核对：GC 之后不该再有新的关闭动作（连接池早已关干净）。
    assert len(calls) == len(closed_before_return), (
        f"轮次返回之后又出现了 {len(calls) - len(closed_before_return)} 次跨 loop 关闭"
    )

    transcript = "\n".join(seen)
    assert CLOSED_LOOP_MARKER not in transcript, f"失败路径仍出现跨 loop 回收失败：\n{transcript}"


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        (
            "set_task_status",
            {"status": "done"},
            SetTaskStatusAction(task_id="task_issue_26", status="done"),
        ),
        (
            "transfer_task",
            {"recipient_number": 2},
            TransferTaskAction(task_id="task_issue_26", assignee_member_id="mem_issue_32"),
        ),
    ],
)
def test_task_tools_return_only_structured_actions(
    stub_provider: str,
    tool_name: str,
    arguments: dict[str, str],
    expected: SetTaskStatusAction | TransferTaskAction,
) -> None:
    """AgentScope 工具调用被转换成类型化动作，不从最终回复正文解析命令。"""
    _StubProviderHandler.tool_call = {"name": tool_name, "arguments": arguments}
    agent, messages = board_inputs()
    task_context = TaskActionContext(
        task_id="task_issue_26",
        assignee_member_id=agent["id"],
        agent_members=(
            TaskAgentOption(member_id=agent["id"], display_name="小明"),
            TaskAgentOption(member_id="mem_issue_31", display_name="乙"),
            TaskAgentOption(member_id="mem_issue_32", display_name="乙"),
        ),
    )

    outcome = AgentScopeAgentSeam(timeout_seconds=10.0).run_round(
        agent,
        messages,
        mentioned=False,
        target_id="tgt_issue_26",
        task_context=task_context,
    )

    assert outcome.status == "replied", outcome.reason
    assert outcome.task_action == expected
    assert len(_StubProviderHandler.requests) == 2
    tools = _StubProviderHandler.request_bodies[0]["tools"]
    assert {tool["function"]["name"] for tool in tools} == {"set_task_status", "transfer_task"}
    assert "mem_issue_32" not in json.dumps(_StubProviderHandler.request_bodies[0])
