"""AgenticMemory 的 Markdown 分区与受限文件工具；不创建工作区或开放项目根。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase
from agentscope.permission import AdditionalWorkingDirectory, PermissionEngine, PermissionMode
from agentscope.tool import BackendBase, Edit, LocalBackend, Read, Write

from .workspace import ensure_state_partition, safe_partition_id


class MemoryBackend(BackendBase):
    """中间件和 DM 文件工具共用的根检查；Docker 路径映射到 bind mount 后核对。"""

    def __init__(self, backend: Any, host_dir: Path, visible_dir: str) -> None:
        self.backend = backend
        self.host_dir = host_dir.resolve()
        self.visible_dir = Path(visible_dir)

    def resolve(self, path: str) -> str:
        requested = Path(path)
        if not requested.is_absolute():
            requested = self.visible_dir / requested
        normalized = Path(os.path.normpath(requested))
        try:
            relative = normalized.relative_to(self.visible_dir)
            (self.host_dir / relative).resolve().relative_to(self.host_dir)
        except ValueError as error:
            raise PermissionError(f"path escapes memory directory: {path!r}") from error
        return str(normalized)

    async def exec_shell(
        self, command: list[str], *, cwd: str | None = None, timeout: float | None = None
    ) -> Any:
        raise PermissionError("memory backend does not provide shell access")

    async def read_file(self, path: str) -> bytes:
        return await self.backend.read_file(self.resolve(path))

    async def write_file(self, path: str, data: bytes) -> None:
        await self.backend.write_file(self.resolve(path), data)

    async def file_exists(self, path: str) -> bool:
        return bool(await self.backend.file_exists(self.resolve(path)))

    async def is_dir(self, path: str) -> bool:
        return bool(await self.backend.is_dir(self.resolve(path)))

    async def stat(self, path: str) -> Any:
        return await self.backend.stat(self.resolve(path))

    async def stat_mtime(self, path: str) -> float | None:
        return await self.backend.stat_mtime(self.resolve(path))

    async def list_dir(self, path: str, *, recursive: bool = False) -> list[str]:
        directory = self.resolve(path)
        entries = await self.backend.list_dir(directory, recursive=recursive)
        result = []
        for entry in entries:
            try:
                result.append(self.resolve(entry if os.path.isabs(entry) else os.path.join(directory, entry)))
            except PermissionError:
                continue
        return result


class MemoryPermissionMiddleware(MiddlewareBase):
    """记忆开关只授予记忆目录编辑权；保留显式 deny/ask 规则与项目权限档。"""

    def __init__(self, backend: MemoryBackend) -> None:
        self.backend = backend

    async def on_check_permission(self, agent: Any, input_kwargs: dict, next_handler: Any) -> Any:
        tool = input_kwargs.get("tool")
        if tool is None or getattr(tool, "name", "") not in ("Read", "Write", "Edit"):
            return await next_handler()
        tool_input = dict(input_kwargs.get("tool_input") or {})
        path = tool_input.get("file_path")
        if not isinstance(path, str):
            return await next_handler()
        try:
            tool_input["file_path"] = self.backend.resolve(path)
        except PermissionError:
            return await next_handler()
        if str(getattr(input_kwargs.get("tool_call"), "state", "")) == "allowed":
            return await next_handler(tool_input=tool_input)
        context = agent.state.permission_context.model_copy(deep=True)
        context.mode = PermissionMode.ACCEPT_EDITS
        context.working_directories = {
            str(self.backend.visible_dir): AdditionalWorkingDirectory(
                path=str(self.backend.visible_dir), source="alibot-memory"
            )
        }
        return await PermissionEngine(context).check_permission(tool, tool_input)


def assemble_memory(
    root: str | Path,
    member_id: str,
    permission_context: Any,
    *,
    mount: Any = None,
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
    backend = MemoryBackend(
        mount.workspace.get_backend() if mount is not None and docker else LocalBackend(),
        host_memory,
        str(visible_workdir / "Memory"),
    )
    directory = str(backend.visible_dir)
    permission_context.working_directories[directory] = AdditionalWorkingDirectory(
        path=directory, source="alibot-memory"
    )
    middlewares = [
        MemoryPermissionMiddleware(backend),
        AgenticMemoryMiddleware(workdir=str(visible_workdir), backend=backend),
    ]
    tools = (
        [] if mount is not None else [Read(backend=backend), Write(backend=backend), Edit(backend=backend)]
    )
    return middlewares, tools
