"""AgenticMemory 的 Markdown 分区与受限文件工具；不创建工作区或开放项目根。

全局记忆（``~/.alibot/agents/<member_id>/memory/Memory/``）由宿主经 #230 只读守卫读取，
以带标签段落追加到 system prompt；它**不**交给 ``AgenticMemoryMiddleware``（该中间件会为缺失的
``MEMORY.md`` 自动创建占位文件，而全局层禁止应用写入），也不进入文件工具的工作根。
"""

from __future__ import annotations

import logging
import os
import posixpath
from pathlib import Path
from typing import Any

from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase
from agentscope.permission import AdditionalWorkingDirectory
from agentscope.tool import Edit, LocalBackend, Read, Write

from .runtime import SessionOffloader
from .runtime_files import PartitionBackend, PartitionPermissionMiddleware
from .workspace import (
    docker_global_member_home,
    ensure_state_partition,
    global_member_home,
    resolve_global_read_path,
    safe_partition_id,
)

_log = logging.getLogger(__name__)

#: 全局记忆段落标签：与规则两段的标签一样，让模型从 system prompt 就能分辨来源。
_GLOBAL_MEMORY_LABEL = "全局记忆（只读）"
_GLOBAL_MEMORY_FILE = "MEMORY.md"


def _global_memory_parts(global_home: str, member_id: str) -> tuple[str, ...]:
    """成员家目录下的 ``memory/Memory`` 相对全局根的分段（经 #230 的成员家目录解析）。"""
    member_home = global_member_home(member_id, root=global_home)
    return (*Path(member_home).relative_to(Path(global_home)).parts, "memory", "Memory")


def _global_memory_dir(global_home: str, member_id: str) -> str | None:
    """全局记忆目录的 realpath；缺失 / 不可读 / 指向全局根外时返回 ``None``（不创建）。"""
    return resolve_global_read_path(global_home, *_global_memory_parts(global_home, member_id))


def _read_global_memory(global_home: str, member_id: str, *, log: bool = True) -> str | None:
    """只读读取全局 ``MEMORY.md``；任何降级返回 ``None`` 并记一条可读日志。

    ``log=False`` 供中间件在同一轮内只记一次：降级按「这一段缺席」处理，不改变本轮结论。
    """
    candidate = os.path.join(global_home, *_global_memory_parts(global_home, member_id), _GLOBAL_MEMORY_FILE)
    real = resolve_global_read_path(
        global_home, *_global_memory_parts(global_home, member_id), _GLOBAL_MEMORY_FILE
    )
    if real is None:
        if log:
            _log.info("global memory layer is absent or unreadable: %s", candidate)
        return None
    try:
        data = Path(real).read_bytes()
    except FileNotFoundError:  # realpath 与读取之间的删除：等价于缺席
        if log:
            _log.info("global memory file is absent: %s", real)
        return None
    except OSError as error:
        if log:
            _log.warning("global memory file is unreadable: %s (%s)", real, error)
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        if log:
            _log.warning("global memory file is not UTF-8: %s (%s)", real, error)
        return None
    if not text.strip():
        if log:
            _log.info("global memory file is empty: %s", real)
        return None
    return text


class GlobalMemoryReadMiddleware(MiddlewareBase):
    """把全局 ``MEMORY.md`` 以只读段落追加到 system prompt。

    内容由宿主每轮重新读取（经 :func:`workspace.resolve_global_read_path` 守卫），因此全局侧
    不会被应用写入；目录 / 文件缺失或越界只是「这一段缺席」，不影响本轮收口。
    """

    def __init__(self, global_home: str, member_id: str, visible_dir: str) -> None:
        self._global_home = global_home
        self._member_id = member_id
        self._visible_dir = visible_dir
        self._logged_degradation = False

    async def on_system_prompt(self, agent: Any, current_prompt: str) -> str:
        content = _read_global_memory(self._global_home, self._member_id, log=not self._logged_degradation)
        if content is None:
            self._logged_degradation = True
            return current_prompt
        return f"{current_prompt}\n\n{_GLOBAL_MEMORY_LABEL}\n来源：{self._visible_dir}\n{content}"


def assemble_memory(
    root: str | Path,
    member_id: str,
    permission_context: Any,
    *,
    mount: Any = None,
    offloader: SessionOffloader | None = None,
    global_home: str | Path | None = None,
) -> tuple[list[Any], list[Any]]:
    """仅 memory_mode=agentic 时调用；返回中间件及缺少 workspace 时的三个工具。

    ``global_home`` 是 #230 注入的全局根（生产 = ``workspace.global_home_root()``）。默认
    ``None`` 时只挂项目侧记忆，行为与本票之前逐字相同；全局目录缺失 / 越界时只少挂一处。
    """
    host_root = Path(root).resolve()
    relative = Path(".alibot/memory") / safe_partition_id(member_id)
    host_workdir = host_root / relative
    host_memory = host_workdir / "Memory"
    current = host_root
    for part in host_memory.relative_to(host_root).parts:
        current /= part
        if current.is_symlink():
            raise PermissionError(f"memory partition must not be a symlink: {current}")
    ensure_state_partition(str(host_root), str(host_memory))
    docker = mount is not None and mount.settings.is_docker
    visible_workdir = Path("/workspace") / relative if docker else host_workdir

    global_middlewares: list[Any] = []
    read_only_dirs: list[tuple[Path, str]] = []
    if offloader is not None:
        read_only_dirs.append((offloader.directory, str(offloader.visible_directory)))
    if global_home:
        global_root = os.path.abspath(str(global_home))
        global_dir = _global_memory_dir(global_root, member_id)
        visible_dir = (
            posixpath.join(docker_global_member_home(member_id), "memory", "Memory")
            if docker
            else (global_dir or os.path.join(global_root, *_global_memory_parts(global_root, member_id)))
        )
        global_middlewares.append(GlobalMemoryReadMiddleware(global_root, member_id, visible_dir))
        if global_dir is not None:
            # 只读读取通道：DM 的记忆 Read 工具可显式读取；写路径仍只看项目侧。
            read_only_dirs.append((Path(global_dir), visible_dir))

    backend = PartitionBackend(
        mount.workspace.get_backend() if mount is not None and docker else LocalBackend(),
        host_memory,
        str(visible_workdir / "Memory"),
        read_only_dirs=tuple(read_only_dirs),
    )
    directory = str(backend.visible_dir)
    permission_context.working_directories[directory] = AdditionalWorkingDirectory(
        path=directory, source="alibot-memory"
    )
    middlewares = [
        PartitionPermissionMiddleware(backend),
        *global_middlewares,
        AgenticMemoryMiddleware(workdir=str(visible_workdir), backend=backend),
    ]
    tools = (
        [] if mount is not None else [Read(backend=backend), Write(backend=backend), Edit(backend=backend)]
    )
    return middlewares, tools
