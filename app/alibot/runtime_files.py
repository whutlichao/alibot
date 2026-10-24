"""运行时文件工具的分区边界：当前会话的卸载目录**只读**。

无 workspace 挂载的轮次（DM / 未配项目根的频道 / 无频道根的任务）没有项目根可做文件工具的
工作根；这一轮唯一可读的文件面是当前会话的卸载目录（:class:`alibot.runtime.SessionOffloader`）。
长期记忆不再经过文件工具（ADR-0034 §四），因此这里没有任何可写分区：越界返回可读的拒绝，
不是静默跳过。
"""

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
    """工具执行前核对分区 realpath；只有只读分区，不提供写入。

    - ``host_dir`` / ``visible_dir``：唯一分区（当前会话的卸载目录）。

    相对路径锚到主分区的模型可见路径；绝对路径原样归一化，``realpath`` 后必须仍在分区内。
    """

    def __init__(self, backend: Any, host_dir: Path, visible_dir: str) -> None:
        self.backend = backend
        self.host_dir = host_dir.absolute()
        self.visible_dir = Path(visible_dir)

    # ── 路径解析 ────────────────────────────────────────────────────────────
    @staticmethod
    def _candidates(normalized: Path) -> tuple[Path, ...]:
        """同一路径的两种写法：字面归一化与 ``realpath``（容忍 ``/tmp`` ↔ ``/private/tmp`` 与路径别名）。"""
        real = Path(os.path.realpath(normalized))
        return (normalized,) if real == normalized else (normalized, real)

    @classmethod
    def _match(cls, normalized: Path, host: Path, visible: Path) -> str | None:
        """``normalized`` 落在 ``visible`` 内且 realpath 仍在 ``host`` 内 ⇒ 返回相对路径。

        两种写法各试一次（字面路径与 realpath）：经 symlink 别名进来的请求必须同样被拦住，
        而不是因为“字面路径对不上”就落到下一条分支。
        """
        for candidate in cls._candidates(normalized):
            for base in (visible, Path(os.path.realpath(visible))):
                try:
                    relative = candidate.relative_to(base)
                    (host / relative).resolve().relative_to(host)
                except ValueError:
                    continue
                return str(relative)
        return None

    def resolve(self, path: str) -> str:
        """相对路径锚到主分区；分区内返回归一化绝对路径，否则抛 ``PermissionError``。"""
        requested = Path(path)
        if not requested.is_absolute():
            requested = self.visible_dir / requested
        normalized = Path(os.path.normpath(requested))
        if self._match(normalized, self.host_dir, self.visible_dir) is not None:
            return str(normalized)
        raise PermissionError(f"path escapes runtime directories: {path!r}")

    async def exec_shell(
        self, command: list[str], *, cwd: str | None = None, timeout: float | None = None
    ) -> Any:
        raise PermissionError("runtime file backend does not provide shell access")

    async def read_file(self, path: str) -> bytes:
        return await self.backend.read_file(self.resolve(path))

    async def write_file(self, path: str, data: bytes) -> None:
        raise PermissionError("runtime file backend is read-only")

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
    """只授予分区内的最小权限；保留显式 deny/ask 规则与项目权限档。

    这一层只出现在**没有 workspace 挂载**的轮次：文件工具只有一个只读分区（卸载目录），
    相对路径锚到它。写入一律拒绝——DM 不再有可写的记忆目录（ADR-0034 §四）。
    """

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
            resolved = self.backend.resolve(path)
        except PermissionError:
            if write:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Error: path is read-only: {path!r}",
                )
            return await next_handler()
        if write:
            # 分区内也没有可写面：记忆由 ReMe 在宿主路径写，不经过文件工具。
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Error: path is read-only: {path!r}",
            )
        tool_input["file_path"] = resolved
        if str(getattr(input_kwargs.get("tool_call"), "state", "")) == "allowed":
            return await next_handler(tool_input=tool_input)
        context = agent.state.permission_context.model_copy(deep=True)
        context.mode = PermissionMode.EXPLORE
        context.working_directories = {
            str(self.backend.visible_dir): AdditionalWorkingDirectory(
                path=str(self.backend.visible_dir), source="alibot-runtime"
            )
        }
        return await PermissionEngine(context).check_permission(tool, tool_input)
