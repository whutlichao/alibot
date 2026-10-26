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

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .i18n import SOURCE_LOCALE, Locale, render

_log = logging.getLogger(__name__)

#: 声明允许的传输；与 ``mcp_server.transport`` 的 CHECK 一致。
MCP_TRANSPORTS: tuple[str, ...] = ("stdio", "http", "sse")

#: 远端传输（需要一次数据面确认）。
REMOTE_MCP_TRANSPORTS: frozenset[str] = frozenset({"http", "sse"})

# MCP 声明名同时是 AgentScope 的 MCPClient 名（工具名 ``mcp__{name}__{tool}``），框架只接受
# ``[a-zA-Z0-9_-]+``。写路径与全局文件读路径共用这一份形状。
MCP_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# ``env_names`` 只存变量**名**：这个形状同时排除了 ``KEY=value`` 这类把值写进名字的输入。
MCP_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

#: 全局成员家目录里的文件型声明（ADR-0031 §一）。
GLOBAL_MCP_FILENAME = "mcp.json"


class InvalidMCPDeclaration(ValueError):
    """一条声明的形状不合法；``field`` 指出是哪个字段（供写路径渲染既有错误文案）。"""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"invalid MCP declaration: {field}")


class InvalidMCPDocument(ValueError):
    """一份 ``mcp.json`` 原文不合法：``location`` 是字段级错误的位置，``message`` 已经渲染好。"""

    def __init__(self, location: str, key: str, locale: Locale | None = None, **params: Any) -> None:
        self.location = location
        self.message = render(key, locale, **params)
        super().__init__(self.message)


def normalize_mcp_declaration(values: Mapping[str, Any]) -> dict[str, Any]:
    """校验并收敛一条声明的**完整**形态（库写路径与全局文件读路径共用的唯一实现）。

    语义与 ``mcp_server`` 表的既有约束逐字一致：名字 / transport / args / ``env_names`` /
    command 与 URL 的形状；``stdio`` 必须有 command，远端必须有 http(s) URL，且 ``sse`` 与
    ``http`` 的 URL path 不能互相冒充。非法时抛 :class:`InvalidMCPDeclaration`。
    """
    name = str(values.get("name") or "").strip()
    if not MCP_SERVER_NAME_PATTERN.fullmatch(name):
        raise InvalidMCPDeclaration("name")

    transport = str(values.get("transport") or "").strip()
    if transport not in MCP_TRANSPORTS:
        raise InvalidMCPDeclaration("transport")

    raw_args = values.get("args")
    if raw_args is None:
        raw_args = []
    if not isinstance(raw_args, (list, tuple)) or any(not isinstance(item, str) for item in raw_args):
        raise InvalidMCPDeclaration("args")
    args = list(raw_args)

    raw_env_names = values.get("env_names")
    if raw_env_names is None:
        raw_env_names = []
    if not isinstance(raw_env_names, (list, tuple)):
        raise InvalidMCPDeclaration("env_names")
    env_names: list[str] = []
    for item in raw_env_names:
        if not isinstance(item, str):
            raise InvalidMCPDeclaration("env_names")
        clean = item.strip()
        if not MCP_ENV_NAME_PATTERN.fullmatch(clean):
            raise InvalidMCPDeclaration("env_names")
        if clean not in env_names:
            env_names.append(clean)

    command = str(values.get("command") or "").strip()
    url = str(values.get("url") or "").strip()
    if transport == "stdio":
        if not command:
            raise InvalidMCPDeclaration("command")
        url = ""
    else:
        if not url:
            raise InvalidMCPDeclaration("url")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise InvalidMCPDeclaration("url_invalid")
        looks_sse = parts.path.endswith("/sse") or parts.path.endswith("/messages/")
        if transport == "sse" and not looks_sse:
            raise InvalidMCPDeclaration("url_sse")
        if transport == "http" and looks_sse:
            # AgentScope 按 URL path 选传输：http 声明指向 /sse 会被静默当成 sse。
            raise InvalidMCPDeclaration("url_http")
        command = ""

    return {
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "url": url,
        "env_names": env_names,
        "enabled": bool(values.get("enabled", True)),
    }


def mcp_document_entry_count(text: str) -> int:
    """一份 ``mcp.json`` 原文里的条目计数（``servers`` 数组长度；原文不可解析时为 0）。

    这是**原文事实**，不做合规过滤：一条形状不合法的条目也算一条（它在文件里，装配层才会
    跳过它并留日志）。写路径已用 :func:`validate_mcp_document` 验过形状，读路径面对的是人
    手工改过的文件，因此两者都按这同一份计数口径讲话。
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return 0
    if not isinstance(payload, Mapping):
        return 0
    servers = payload.get("servers")
    return len(servers) if isinstance(servers, list) else 0


def validate_mcp_document(text: str, locale: Locale | None = None) -> list[dict[str, Any]]:
    """校验一份 ``mcp.json`` **原文**并返回归一化后的条目（写盘路径的唯一内核）。

    三道校验必须全部通过，任何一条不成立都抛 :class:`InvalidMCPDocument`（它的 ``location`` 指出
    出错的条目 / 字段）：① 文本是可解析的 JSON；② 顶层是对象且 ``servers`` 是数组；
    ③ 每条经 :func:`normalize_mcp_declaration`（与库写路径、全局文件读路径**同一份实现**）。
    因此这里只做「形状是否可接受」的判定，一个字都不写。

    与读路径的差异只有一处，且是刻意的：读路径**跳过**坏条目继续装配，写路径**整体拒绝**——
    人类正在提交一份文件，静默丢掉他写的一条才是坏结果。重名同理（读路径首条生效；这里因为
    装配层无法表达「同名两条」，也归入拒绝）。
    """
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise InvalidMCPDocument(
            "text", "error.global_mcp_text_invalid_json", locale, reason=str(error)
        ) from None
    if not isinstance(payload, Mapping) or not isinstance(payload.get("servers"), list):
        raise InvalidMCPDocument("servers", "error.global_mcp_servers_missing", locale)

    declarations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(payload["servers"]):
        position = index + 1
        if not isinstance(entry, Mapping):
            raise InvalidMCPDocument(
                f"servers[{index}]", "error.global_mcp_entry_not_object", locale, position=position
            )
        try:
            normalized = normalize_mcp_declaration(entry)
        except InvalidMCPDeclaration as error:
            raise InvalidMCPDocument(
                f"servers[{index}].{error.field}",
                "error.global_mcp_entry_invalid",
                locale,
                position=position,
                field=error.field,
            ) from None
        name = str(normalized["name"])
        if name in seen:
            raise InvalidMCPDocument(
                f"servers[{index}].name",
                "error.global_mcp_entry_duplicate",
                locale,
                position=position,
                name=name,
            )
        seen.add(name)
        declarations.append(normalized)
    return declarations


def load_global_mcp_declarations(
    global_home: str | os.PathLike[str] | None,
    member_id: str,
    *,
    consent_of: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """读 ``<全局根>/agents/<member_id>/mcp.json``（纯文件，**只读、不创建**）。

    文件顶层形状固定为 ``{"servers": [ {name, transport, command, args, url, env_names,
    enabled?}, ... ]}``；条目形状与库里项目声明完全同一套校验
    （:func:`normalize_mcp_declaration`）。文件缺失 / 非法 JSON / 顶层形状不对 ⇒ 全局层零条
    + 一条可读日志；单条目非法 ⇒ 跳过该条 + 日志。项目层不受影响。

    每条全局声明补齐合成标识 ``global:<member_id>:<name>``（``id`` 与 ``scope_id`` 同一值），
    并推导 ``is_remote`` / ``consent_required``；远端条目的 ``consented`` 由注入的
    ``consent_of(合成标识)`` 从既有确认行读取，未注入时按未确认处理。
    """
    if not global_home:
        return []
    from .workspace import AGENTS_DIR, resolve_global_read_path, safe_partition_id

    # 守卫的根是**全局根**（不是成员家目录）：成员家目录本身是指向根外的 symlink 时也要拒绝。
    path = resolve_global_read_path(
        str(global_home), AGENTS_DIR, safe_partition_id(member_id), GLOBAL_MCP_FILENAME
    )
    if path is None:
        _log.debug("global MCP declaration file is absent: %s", member_id)
        return []
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:  # realpath 与读取之间的删除：等价于缺席
        return []
    except (OSError, UnicodeDecodeError) as error:
        _log.warning("global MCP declaration file is unreadable: %s (%s)", path, error)
        return []
    try:
        payload = json.loads(raw)
    except ValueError as error:
        _log.warning("global MCP declaration file is not valid JSON: %s (%s)", path, error)
        return []
    if not isinstance(payload, Mapping) or not isinstance(payload.get("servers"), list):
        _log.warning("global MCP declaration file has no `servers` array: %s", path)
        return []

    declarations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(payload["servers"]):
        if not isinstance(entry, Mapping):
            _log.warning("global MCP declaration #%d is not an object: %s", index, path)
            continue
        try:
            normalized = normalize_mcp_declaration(entry)
        except InvalidMCPDeclaration as error:
            _log.warning("global MCP declaration #%d is invalid (%s): %s", index, error.field, path)
            continue
        name = str(normalized["name"])
        if name in seen:
            # 同一层内重名没有既有优先级可依：首条生效，其余跳过并留日志。
            _log.warning("duplicate global MCP declaration is ignored: %s (%s)", name, path)
            continue
        seen.add(name)
        is_remote = normalized["transport"] in REMOTE_MCP_TRANSPORTS
        scope_id = f"global:{member_id}:{name}"
        consented = False
        if is_remote and consent_of is not None:
            try:
                consented = bool(consent_of(scope_id))
            except Exception:  # 确认行读不出来按未确认处理，不拖垮整轮
                _log.debug("global MCP consent lookup failed", exc_info=True)
                consented = False
        declarations.append(
            {
                **normalized,
                "id": scope_id,
                "scope_id": scope_id,
                "is_remote": is_remote,
                "consent_required": is_remote,
                "consented": consented if is_remote else False,
            }
        )
    return declarations


def read_global_mcp_source(global_home: str | os.PathLike[str] | None, member_id: str) -> Path | None:
    """该成员 ``mcp.json`` 的**真实路径**（全局只读守卫放行后的）；缺席 / 越界时 ``None``。

    守卫的根是**全局根**（不是成员家目录）：成员家目录本身是指向根外的 symlink 时也拒绝。
    它**只读**：不创建、不写入任何字节。
    """
    if not global_home:
        return None
    from .workspace import AGENTS_DIR, resolve_global_read_path, safe_partition_id

    resolved = resolve_global_read_path(
        str(global_home), AGENTS_DIR, safe_partition_id(member_id), GLOBAL_MCP_FILENAME
    )
    return None if resolved is None else Path(resolved)


def global_mcp_unimported_count(
    global_home: str | os.PathLike[str] | None,
    member_id: str,
    *,
    imported_names: Collection[str] = (),
) -> int:
    """成员家目录 ``mcp.json`` 里**尚未导入成库行**的条目数（#275 的提示面）。

    #275 起这份文件**不再进入运行时装配**：它是导入源（``migrate-mcp``）与 Studio 的写面。
    装配层因此只需要知道「文件里还有多少东西没进库」—— 有就发一条只进 ``RoundOutcome.reason``
    的可读提示，让人去跑导入；没有就什么都不说。

    ``imported_names`` 是该成员**库里已有的声明名**（装配层自己的读路径给）：只有名字不在这个
    集合里的条目才算「未导入」。这条参数是必需的——少了它，按 README 推荐的「先导入、再删文件」
    走完的人会每轮都看到「还有 N 条未导入」的**假提示**（那些条目恰恰已装配）。

    口径刻意宽容：文件缺失 / 非法 JSON / 顶层形状不对 / 条目形状不合法 ⇒ **0**（那一格没有
    可提示的东西）。它是**提示的触发条件**，不是导入判定——导入自己用
    :func:`validate_mcp_document` 做整体拒绝。
    """
    path = read_global_mcp_source(global_home, member_id)
    if path is None:
        return 0
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # realpath 与读取之间的删除：等价于缺席
        return 0
    except (OSError, UnicodeDecodeError):
        return 0
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(payload, Mapping):
        return 0
    servers = payload.get("servers")
    if not isinstance(servers, list):
        return 0
    already = set(imported_names)
    count = 0
    for entry in servers:
        if not isinstance(entry, Mapping):
            continue
        try:
            normalized = normalize_mcp_declaration(entry)
        except InvalidMCPDeclaration:
            continue
        if str(normalized["name"]) in already:
            continue
        count += 1
    return count


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
