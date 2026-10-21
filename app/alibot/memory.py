"""AgenticMemory 的 Markdown 分区与受限文件工具；不创建工作区或开放项目根。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.permission import AdditionalWorkingDirectory
from agentscope.tool import Edit, LocalBackend, Read, Write

from .runtime import SessionOffloader
from .runtime_files import PartitionBackend, PartitionPermissionMiddleware
from .workspace import ensure_state_partition, safe_partition_id


def assemble_memory(
    root: str | Path,
    member_id: str,
    permission_context: Any,
    *,
    mount: Any = None,
    offloader: SessionOffloader | None = None,
) -> tuple[list[Any], list[Any]]:
    """仅 memory_mode=agentic 时调用；返回中间件及缺少 workspace 时的三个工具。"""
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
    backend = PartitionBackend(
        mount.workspace.get_backend() if mount is not None and docker else LocalBackend(),
        host_memory,
        str(visible_workdir / "Memory"),
        read_only_dirs=((offloader.directory, str(offloader.visible_directory)),) if offloader else (),
    )
    directory = str(backend.visible_dir)
    permission_context.working_directories[directory] = AdditionalWorkingDirectory(
        path=directory, source="alibot-memory"
    )
    middlewares = [
        PartitionPermissionMiddleware(backend),
        AgenticMemoryMiddleware(workdir=str(visible_workdir), backend=backend),
    ]
    tools = (
        [] if mount is not None else [Read(backend=backend), Write(backend=backend), Edit(backend=backend)]
    )
    return middlewares, tools
