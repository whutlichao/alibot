"""MCP 声明的装配层（#151 / 规格 §3.6 / ADR-0021 §三）。

库里只存**声明**（transport / command / args / url / ``env_names``），本模块负责把它物化成
AgentScope 的 :class:`agentscope.mcp.MCPClient`：

- **值只从进程环境读**：``env_names`` 里的变量名按名字取当前进程的值，值不落库、不进响应。
  stdio 传输只把这些**被声明的**变量交给子进程；MCP SDK 会再合并一份安全默认环境（PATH 等），
  因此不会把无关的进程环境整体泄露给子进程。
- **远端数据面**：HTTP / SSE 声明在确认（``remote_consent_scope('mcp', id)``）之前**不建连**；
  这是装配层的过滤，不依赖 UI。
- **失败可读、不阻塞**：每个 server 的最近一次装配结果按 id 记在进程内；连接失败只影响该
  server 的工具，其他 server 与工作区工具照常装配。连接失败原因来自 AgentScope 自身的
  warning（框架的 ``list_mcps`` 会吞掉单 server 异常并继续），因此这里挂一个只读日志捕获器。
- **生命周期**：客户端由工作区持有（Local 是每轮新建；Docker 走容器内 MCP 网关），一轮结束
  由 :meth:`MCPAssembly.close` 关闭并丢弃实例。Docker 的容器跨轮复用，但 MCP 客户端的
  transport 绑定在创建它的 event loop 上——每轮 ``asyncio.run`` 换 loop，必须重建，不能复用。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from .i18n import SOURCE_LOCALE, Locale, render

#: 声明允许的传输；与 ``mcp_server.transport`` 的 CHECK 一致。
MCP_TRANSPORTS: tuple[str, ...] = ("stdio", "http", "sse")

#: 远端传输（需要一次数据面确认）。
REMOTE_MCP_TRANSPORTS: frozenset[str] = frozenset({"http", "sse"})

#: 进程内的装配结果（按 server id）。它**不是**持久事实：重启即清空，库里没有对应列。
_runtime: dict[str, dict[str, Any]] = {}
_runtime_lock = threading.Lock()

# AgentScope 的 ``list_mcps`` 对单个 server 的失败只写一条 warning，然后继续返回其余客户端。
# 这里捕获它，把失败原因变成可读的运行时状态；匹配前缀是框架稳定文案（见 2.0.10
# ``workspace/_base.py`` 与 ``_sandboxed_base.py``）。
_MCP_FAILURE_PREFIX = "Failed to start MCP "
_MCP_FAILURE_PATTERN = re.compile(
    r"^Failed to start MCP (?P<name>'[^']*') for agent=.*? session=.*?: (?P<reason>.*)$",
    re.DOTALL,
)


def record_runtime(
    server_id: str,
    *,
    connected: bool,
    error: str | None = None,
    tools: list[str] | None = None,
) -> dict[str, Any]:
    """记下一个 server 装配后的进程内状态（不落库、不落消息）。"""
    status = {
        "connected": connected,
        "error": error,
        "tools": list(tools or []),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _runtime_lock:
        _runtime[server_id] = status
    return dict(status)


def runtime_status(server_id: str) -> dict[str, Any] | None:
    """最近一次装配状态；从未装配过时返回 ``None``。"""
    with _runtime_lock:
        status = _runtime.get(server_id)
    return dict(status) if status is not None else None


def clear_runtime(server_id: str | None = None) -> None:
    """清掉一个（或全部）运行时状态；删除声明时调用。"""
    with _runtime_lock:
        if server_id is None:
            _runtime.clear()
        else:
            _runtime.pop(server_id, None)


@dataclass
class MCPAssembly:
    """一轮装配的产物：可挂进 toolkit 的工具 + 结束后关闭客户端的能力。"""

    workspace: Any
    member_id: str
    session_id: str
    tools: list[Any] = field(default_factory=list)

    async def close(self) -> None:
        """关闭本 agent/session 的客户端并从工作区缓存里丢弃（同一 loop 内调用）。"""
        await _drop_live_clients(self.workspace, self.member_id, self.session_id)


class _FailureCapture(logging.Handler):
    """只收集 AgentScope 的「MCP 启动失败」warning；不改变日志输出。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.reasons: dict[str, str] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # 日志格式化失败不该影响装配
            return
        if not message.startswith(_MCP_FAILURE_PREFIX):
            return
        match = _MCP_FAILURE_PATTERN.match(message)
        if match is None:
            return
        self.reasons[match.group("name").strip("'")] = match.group("reason").strip()


def _declared_env(env_names: list[str], environ: Mapping[str, str]) -> dict[str, str]:
    """按**变量名**从进程环境取值；未设置的变量名直接略过（不报错、不写占位）。"""
    return {name: environ[name] for name in env_names if name in environ}


def build_client(
    server: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> Any:
    """把一条声明物化成 ``MCPClient``（不连接）。

    传输映射由 AgentScope 决定：``http`` 与 ``sse`` 都走 ``HttpMCPConfig``，SDK 按 URL path
    是否以 ``/sse`` / ``/messages/`` 结尾选择 SSE；声明层的校验保证 ``transport='sse'`` 的
    URL 落在这条路径上。
    """
    from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig

    source = os.environ if environ is None else environ
    transport = str(server["transport"])
    if transport == "stdio":
        env = _declared_env([str(name) for name in server.get("env_names") or []], source)
        config: Any = StdioMCPConfig(
            command=str(server.get("command") or ""),
            args=[str(arg) for arg in server.get("args") or []],
            env=env or None,
            cwd=cwd,
        )
    else:
        config = HttpMCPConfig(url=str(server.get("url") or ""))
    return MCPClient(
        name=str(server["name"]),
        is_stateful=True,
        mcp_config=config,
    )


async def assemble_mcp_tools(
    workspace: Any,
    declarations: Sequence[Mapping[str, Any]],
    *,
    member_id: str,
    session_id: str,
    environ: Mapping[str, str] | None = None,
    locale: Locale = SOURCE_LOCALE,
    cwd: str | None = None,
) -> MCPAssembly:
    """把本轮启用且已确认的声明挂到工作区，返回可用的 MCP 工具。

    ``workspace`` 是 AgentScope 的 Local / Docker 工作区：Local 直接建本地客户端，Docker 由
    框架的容器内 MCP 网关代理。两种情况都按 ``(member_id, session_id)`` 隔离实例。

    单个 server 失败**不抛出**：状态写进运行时读数，其余工具照常返回。
    """
    assembly = MCPAssembly(workspace=workspace, member_id=member_id, session_id=session_id)
    specs: list[Any] = []
    eligible: list[Mapping[str, Any]] = []

    for declaration in declarations:
        server_id = str(declaration["id"])
        if declaration.get("consent_required") and not declaration.get("consented"):
            record_runtime(
                server_id,
                connected=False,
                error=render("mcp.runtime.consent_required", locale, name=str(declaration["name"])),
            )
            continue
        try:
            specs.append(
                build_client(
                    declaration,
                    environ=environ,
                    cwd=cwd
                    if cwd is not None
                    else (str(workspace.workdir) if hasattr(workspace, "workdir") else None),
                )
            )
        except Exception as error:  # 声明合法但框架拒绝（例如 name 形状）也不阻塞其他 server
            record_runtime(server_id, connected=False, error=str(error))
            continue
        eligible.append(declaration)

    if not specs:
        # 本轮没有可装配项（未声明 / 全部未确认远端）：清掉滞留的默认声明，避免池化工作区
        # 在之后的 list_mcps 里复活上一轮的 server。
        if hasattr(workspace, "default_mcps"):
            workspace.default_mcps = []
        return assembly

    # 工作区在 list_mcps 时按 agent/session 建实例；这里只给本轮声明，写入的内存层不落盘。
    workspace.default_mcps = specs
    capture = _FailureCapture()
    framework_logger = logging.getLogger("as")
    framework_logger.addHandler(capture)
    try:
        clients = await workspace.list_mcps(agent_id=member_id, session_id=session_id)
    except Exception as error:
        # 工作区/网关整体不可用：每个声明都记同一份可读原因，不抛出（工具面只是少一块）。
        for declaration in eligible:
            record_runtime(str(declaration["id"]), connected=False, error=str(error))
        return assembly
    finally:
        framework_logger.removeHandler(capture)

    by_name = {str(getattr(client, "name", "")): client for client in clients}
    for declaration in eligible:
        server_id = str(declaration["id"])
        name = str(declaration["name"])
        client = by_name.get(name)
        if client is None:
            reason = capture.reasons.get(name) or render("mcp.runtime.connect_failed", locale, name=name)
            record_runtime(server_id, connected=False, error=reason)
            continue
        try:
            tools = await client.list_tools()
        except Exception as error:
            record_runtime(server_id, connected=False, error=str(error))
            continue
        record_runtime(
            server_id,
            connected=True,
            tools=[str(getattr(tool, "name", "")) for tool in tools],
        )
        assembly.tools.extend(tools)
    return assembly


async def _drop_live_clients(workspace: Any, member_id: str, session_id: str) -> None:
    """关闭并丢弃工作区为这个 agent/session 缓存的 MCP 实例。

    AgentScope 没有公开「只关闭某个 session 的客户端、不删除 offload 目录」的入口；
    ``purge_session`` 会连会话目录一起删掉，``reset`` 会清整个工作区。这里只动框架的实例层：
    声明仍在 ``default_mcps``，下一次 ``list_mcps`` 会在**当前 loop** 上重建。
    """
    instances = getattr(workspace, "_mcp_instances", None)
    if not isinstance(instances, dict):
        return
    live = instances.pop((member_id, session_id), {})
    # 逆序关闭：MCP 的 stdio/http transport 是 anyio cancel scope，同一 task 内按连接顺序嵌套；
    # 先关内层（后连的）再关外层，否则框架会报 "cancel scope isn't the current task's" 并取消。
    for client in reversed(list(live.values())):
        if not getattr(client, "is_connected", False):
            continue
        with suppress(Exception):
            await client.close()
