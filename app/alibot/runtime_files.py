"""运行时文件工具的分区边界：记忆可编辑，当前会话卸载只读。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
)
from agentscope.tool import BackendBase


class PartitionBackend(BackendBase):
    """工具执行前核对分区 realpath；禁止 shell，并区分可编辑与只读目录。"""

    def __init__(
        self,
        backend: Any,
        host_dir: Path,
        visible_dir: str,
        *,
        writable: bool = True,
        read_only_dirs: tuple[tuple[Path, str], ...] = (),
    ) -> None:
        self.backend = backend
        self.host_dir = host_dir.absolute()
        self.visible_dir = Path(visible_dir)
        self.writable = writable
        self.read_only_dirs = [(host.absolute(), Path(visible)) for host, visible in read_only_dirs]

    def resolve(self, path: str, *, write: bool = False) -> str:
        requested = Path(path)
        if not requested.is_absolute():
            requested = self.visible_dir / requested
        normalized = Path(os.path.normpath(requested))
        directories = [(self.host_dir, self.visible_dir)] if self.writable or not write else []
        if not write:
            directories += self.read_only_dirs
        for host, visible in directories:
            try:
                relative = normalized.relative_to(visible)
                (host / relative).resolve().relative_to(host)
            except ValueError:
                continue
            return str(normalized)
        raise PermissionError(f"path escapes runtime directories: {path!r}")

    def is_read_only(self, path: str) -> bool:
        """路径是否落在只读目录（local 用 realpath 比对，容忍 ``/var`` ↔ ``/private/var`` 这类别名）。"""
        requested = Path(path)
        if not requested.is_absolute():
            requested = self.visible_dir / requested
        normalized = Path(os.path.normpath(requested))
        real = Path(os.path.realpath(requested))
        for _, visible in self.read_only_dirs:
            for candidate in (normalized, real):
                for base in (visible, Path(os.path.realpath(visible))):
                    try:
                        candidate.relative_to(base)
                        return True
                    except ValueError:
                        continue
        return False

    async def exec_shell(
        self, command: list[str], *, cwd: str | None = None, timeout: float | None = None
    ) -> Any:
        raise PermissionError("runtime file backend does not provide shell access")

    async def read_file(self, path: str) -> bytes:
        return await self.backend.read_file(self.resolve(path))

    async def write_file(self, path: str, data: bytes) -> None:
        await self.backend.write_file(self.resolve(path, write=True), data)

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


class PartitionPermissionMiddleware(MiddlewareBase):
    """只授予分区内的最小权限；保留显式 deny/ask 规则与项目权限档。"""

    def __init__(self, backend: PartitionBackend) -> None:
        self.backend = backend

    async def on_check_permission(self, agent: Any, input_kwargs: dict, next_handler: Any) -> Any:
        tool = input_kwargs.get("tool")
        if tool is None or getattr(tool, "name", "") not in ("Read", "Write", "Edit"):
            return await next_handler()
        tool_input = dict(input_kwargs.get("tool_input") or {})
        path = tool_input.get("file_path")
        if not isinstance(path, str):
            return await next_handler()
        write = tool.name != "Read"
        try:
            tool_input["file_path"] = self.backend.resolve(path, write=write)
        except PermissionError:
            if write and self.backend.is_read_only(path):
                # 只读目录（会话卸载 / 全局记忆）不是合法写入目标：直接拒绝，不转成 ASK。
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Error: path is read-only: {path!r}",
                )
            return await next_handler()
        if str(getattr(input_kwargs.get("tool_call"), "state", "")) == "allowed":
            return await next_handler(tool_input=tool_input)
        context = agent.state.permission_context.model_copy(deep=True)
        context.mode = PermissionMode.ACCEPT_EDITS if write else PermissionMode.EXPLORE
        context.working_directories = {
            str(self.backend.visible_dir): AdditionalWorkingDirectory(
                path=str(self.backend.visible_dir), source="alibot-runtime"
            )
        }
        if not write:
            for _, visible in self.backend.read_only_dirs:
                context.working_directories[str(visible)] = AdditionalWorkingDirectory(
                    path=str(visible), source="alibot-offload"
                )
        return await PermissionEngine(context).check_permission(tool, tool_input)
