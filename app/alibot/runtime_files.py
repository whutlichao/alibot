"""运行时文件工具的分区边界：记忆可编辑，当前会话卸载只读。

ADR-0032 §三：家目录（项目层与全局层同构）里应用可写的只有**根下**的 ``MEMORY.md`` 与
``<topic>.md``；``AGENTS.md``、``mcp.json``、``skills/`` 与任意子目录一律只读。白名单在这里
落成机械判据（:func:`alibot.workspace.is_memory_file_path`），拒绝时给出可读文案而不是静默跳过。
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


class ReadOnlyPathError(PermissionError):
    """路径落在**可写根的记忆白名单之外**（``AGENTS.md`` / ``mcp.json`` / 子目录）。

    与「根外路径」（``path escapes runtime directories``）刻意分开：前者是一个合法目录里的
    只读文件，后者根本不是本工具的工作面。调用方据此给出两种不同的可读拒绝。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"path is read-only: {path!r}")


class PartitionBackend(BackendBase):
    """工具执行前核对分区 realpath；禁止 shell，并区分可编辑与只读目录。

    - ``host_dir`` / ``visible_dir``：主分区（记忆家目录）。``write_filter`` 给出它的写入白名单，
      ``None`` 表示整棵子树可写（既有行为）。
    - ``read_only_dirs``：只读分区（会话卸载目录）。
    - ``writable_dirs``：**额外**的可写分区（有项目根时的全局家目录），每项自带白名单。
    - ``anchor_relative``：相对路径是否锚到主分区。有项目根的轮次里相对路径仍从**频道项目根**
      起算，因此那时为 ``False``——相对路径直接判成「不在本分区」，交给项目根的守卫。
    """

    def __init__(
        self,
        backend: Any,
        host_dir: Path,
        visible_dir: str,
        *,
        writable: bool = True,
        read_only_dirs: tuple[tuple[Path, str], ...] = (),
        writable_dirs: tuple[tuple[Path, str, Any], ...] = (),
        write_filter: Any = None,
        anchor_relative: bool = True,
    ) -> None:
        self.backend = backend
        self.host_dir = host_dir.absolute()
        self.visible_dir = Path(visible_dir)
        self.writable = writable
        self.read_only_dirs = [(host.absolute(), Path(visible)) for host, visible in read_only_dirs]
        self.writable_dirs = [
            (host.absolute(), Path(visible), write_filter_of)
            for host, visible, write_filter_of in writable_dirs
        ]
        self.write_filter = write_filter
        self.anchor_relative = anchor_relative

    # ── 路径解析 ────────────────────────────────────────────────────────────
    @staticmethod
    def _candidates(normalized: Path) -> tuple[Path, ...]:
        """同一路径的两种写法：字面归一化与 ``realpath``（容忍 ``/tmp`` ↔ ``/private/tmp`` 与路径别名）。"""
        real = Path(os.path.realpath(normalized))
        return (normalized,) if real == normalized else (normalized, real)

    @classmethod
    def _match(cls, normalized: Path, host: Path, visible: Path) -> str | None:
        """``normalized`` 落在 ``visible`` 内且 realpath 仍在 ``host`` 内 ⇒ 返回相对路径。

        两种写法各试一次（字面路径与 realpath）：经 symlink 别名进来的写请求必须同样被白名单
        拦住，而不是因为“字面路径对不上”就落到下一条分支。
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

    def _writable_roots(self) -> list[tuple[Path, Path, Any]]:
        roots: list[tuple[Path, Path, Any]] = []
        if self.writable:
            roots.append((self.host_dir, self.visible_dir, self.write_filter))
        roots.extend(self.writable_dirs)
        return roots

    def resolve(self, path: str, *, write: bool = False) -> str:
        requested = Path(path)
        relative_input = not requested.is_absolute()
        if relative_input and self.anchor_relative:
            requested = self.visible_dir / requested
        normalized = Path(os.path.normpath(requested))
        if write:
            for host, visible, write_filter_of in self._writable_roots():
                relative = self._match(normalized, host, visible)
                if relative is None:
                    continue
                if write_filter_of is not None and not write_filter_of(relative):
                    if relative_input and not self.anchor_relative:
                        break  # 相对路径从项目根起算：交给项目根的守卫，不在这里判只读
                    raise ReadOnlyPathError(path)
                return str(normalized)
            raise PermissionError(f"path escapes runtime directories: {path!r}")
        for host, visible in [(self.host_dir, self.visible_dir), *self.read_only_dirs]:
            if self._match(normalized, host, visible) is not None:
                return str(normalized)
        for host, visible, _filter in self.writable_dirs:
            if self._match(normalized, host, visible) is not None:
                return str(normalized)
        raise PermissionError(f"path escapes runtime directories: {path!r}")

    def is_read_only(self, path: str) -> bool:
        """路径是否落在只读目录（local 用 realpath 比对，容忍 ``/var`` ↔ ``/private/var`` 这类别名）。"""
        requested = Path(path)
        if not requested.is_absolute():
            requested = self.visible_dir / requested
        normalized = Path(os.path.normpath(requested))
        for _, visible in self.read_only_dirs:
            for candidate in self._candidates(normalized):
                for base in (visible, Path(os.path.realpath(visible))):
                    try:
                        candidate.relative_to(base)
                        return True
                    except ValueError:
                        continue
        return False

    def writable_visible_dirs(self) -> list[Path]:
        """可写分区对模型可见的路径（写权限上下文要覆盖它们全部）。"""
        return [visible for _host, visible, _filter in self._writable_roots()]

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
    """只授予分区内的最小权限；保留显式 deny/ask 规则与项目权限档。

    ``strict`` 为真时本层是**唯一**的文件工具准入面（无项目根的轮次：工作区没装配起来，
    没有 ``WorkspaceRootGuard`` 把相对路径锚到项目根）。此时相对路径锚到记忆分区，且越界
    与只读一律给出可读拒绝；为假时（有项目根）相对路径仍从频道项目根起算，本层只拦家目录
    白名单与登记工作目录。
    """

    def __init__(self, backend: PartitionBackend, *, strict: bool = False) -> None:
        self.backend = backend
        self.strict = strict

    def _anchor(self, path: str) -> str:
        """相对路径的锚点：严格档锚到分区，宽松档锚到进程 cwd（交给项目根守卫）。"""
        if self.strict or os.path.isabs(path):
            return path
        return os.path.join(os.getcwd(), path)

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
            tool_input["file_path"] = self.backend.resolve(self._anchor(path), write=write)
        except ReadOnlyPathError as error:
            # 家目录里的非白名单文件（AGENTS.md / mcp.json / 子目录）：直接拒绝，不转成 ASK。
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Error: {error}",
            )
        except PermissionError:
            if write and self.backend.is_read_only(path):
                # 只读目录（会话卸载）不是合法写入目标：直接拒绝，不转成 ASK。
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Error: path is read-only: {path!r}",
                )
            return await next_handler()
        if str(getattr(input_kwargs.get("tool_call"), "state", "")) == "allowed":
            return await next_handler(tool_input=tool_input)
        context = agent.state.permission_context.model_copy(deep=True)
        context.mode = PermissionMode.ACCEPT_EDITS if write else PermissionMode.EXPLORE
        visible_dirs = self.backend.writable_visible_dirs() if write else [self.backend.visible_dir]
        context.working_directories = {
            str(visible): AdditionalWorkingDirectory(path=str(visible), source="alibot-runtime")
            for visible in visible_dirs
        }
        if not write:
            for _, visible in self.backend.read_only_dirs:
                context.working_directories[str(visible)] = AdditionalWorkingDirectory(
                    path=str(visible), source="alibot-offload"
                )
        return await PermissionEngine(context).check_permission(tool, tool_input)
