"""AgenticMemory 的家目录分区与受限文件工具；记忆两处可写、其余只读（ADR-0032）。

一个 root 一份**同构扁平家目录**：项目层 ``<workspace_root>/.alibot/agents/<member_id>/``、
全局层 ``~/.alibot/agents/<member_id>/``，两者都直接承载 ``AGENTS.md`` 与 ``MEMORY.md``。

- **有项目根的轮次**：``AgenticMemoryMiddleware`` 的 workdir 是**项目家目录**（``memory_dir="."``），
  全局家目录作为**受白名单约束的额外可写目录**同时挂上，全局 ``MEMORY.md`` 仍以带标签段落注入
  system prompt（两处内容不同，不重复）。
- **无项目根**（DM / 频道未配项目根 / task 无频道根）：全局家目录是**唯一**记忆落点，
  workdir 就是它；此时不再额外注入第二段全局段落（同一个文件不重复）。
- **未开启长期记忆**：两处都不挂、不建目录、不装中间件（调用方在装配层早退）。

写入白名单由 :func:`alibot.workspace.is_memory_file_path` 给出：家目录根下的 ``MEMORY.md`` 与
``<topic>.md``；``AGENTS.md``、``mcp.json``、``skills/`` 与任意子目录一律只读。
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
    AGENTS_DIR,
    ALIBOT_DIR,
    DOCKER_WORKDIR,
    MEMORY_FILE,
    WorkspaceConfigError,
    docker_global_member_home,
    ensure_member_home,
    ensure_state_partition,
    global_member_home,
    is_memory_file_path,
    resolve_global_read_path,
    safe_partition_id,
)

_log = logging.getLogger(__name__)

#: 全局记忆段落标签：与规则两段的标签一样，让模型从 system prompt 就能分辨来源。
#: 家目录根下的 ``MEMORY.md`` 现在是**可写**的（ADR-0032），标签不再自称只读。
_GLOBAL_MEMORY_LABEL = "全局记忆"


def _global_memory_parts(global_home: str, member_id: str) -> tuple[str, ...]:
    """成员家目录相对全局根的分段（扁平：``agents/<member_id>``，没有中间层）。"""
    member_home = global_member_home(member_id, root=global_home)
    return tuple(Path(member_home).relative_to(Path(global_home)).parts)


def _read_global_memory(global_home: str, member_id: str, *, log: bool = True) -> str | None:
    """只读读取全局 ``MEMORY.md``；任何降级返回 ``None`` 并记一条可读日志。

    ``log=False`` 供中间件在同一轮内只记一次：降级按「这一段缺席」处理，不改变本轮结论。
    """
    parts = _global_memory_parts(global_home, member_id)
    candidate = os.path.join(global_home, *parts, MEMORY_FILE)
    real = resolve_global_read_path(global_home, *parts, MEMORY_FILE)
    if real is None:
        if log:
            _log.info("global memory file is absent or unreadable: %s", candidate)
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
    """把全局 ``MEMORY.md`` 以带标签段落追加到 system prompt（只在有项目根的轮次）。

    内容由宿主每轮重新读取（经 :func:`workspace.resolve_global_read_path` 守卫），因此宿主侧读取
    从不追随家目录之外的 symlink。目录 / 文件缺失或越界只是「这一段缺席」，不影响本轮收口。
    无项目根的轮次不挂它：那时 ``AgenticMemoryMiddleware`` 的 workdir 就是全局家目录，
    同一个文件不该在提示里出现两次。
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
        return (
            f"{current_prompt}\n\n{_GLOBAL_MEMORY_LABEL}\n来源：{self._visible_dir}\n{content}"
            f"\n（属于全局通用的记忆写这里：{self._visible_dir}/{MEMORY_FILE} 或同层的 <topic>.md。）"
        )


def _project_home(root: str | Path, member_id: str) -> Path:
    """项目层家目录 ``<项目根>/.alibot/agents/<member_id>``；根外 symlink 祖先直接拒绝。"""
    host_root = Path(root).resolve()
    home = host_root / ALIBOT_DIR / AGENTS_DIR / member_id
    current = host_root
    for part in home.relative_to(host_root).parts:
        current /= part
        if current.is_symlink():
            raise PermissionError(f"memory partition must not be a symlink: {current}")
    ensure_state_partition(str(host_root), str(home))
    return home


def _ensure_global_home(global_root: str, member_id: str) -> Path | None:
    """创建全局成员家目录；越界 symlink / 不可建时降级为 ``None``（该层缺席）。

    ADR-0032 §三：应用可以建 ``agents/<member_id>/`` 这一级，但全局根外的 symlink 祖先不被追随
    （``ensure_state_partition`` 拒绝）。拒绝时本层记忆缺席，本轮照常收口。
    """
    try:
        return Path(ensure_member_home(global_root, member_id))
    except (WorkspaceConfigError, OSError) as error:
        _log.warning("global memory home is unusable; the layer is absent this round: %s", error)
        return None


def assemble_memory(
    root: str | Path | None,
    member_id: str,
    permission_context: Any,
    *,
    mount: Any = None,
    offloader: SessionOffloader | None = None,
    global_home: str | Path | None = None,
) -> tuple[list[Any], list[Any]]:
    """仅 memory_mode=agentic 时调用；返回中间件及缺少 workspace 时的三个工具。

    ``root`` 是这一轮的**项目根**（无项目根时 ``None``：DM / 未配项目根的频道 / 无频道根的任务）。
    ``global_home`` 是 #230 注入的全局根（生产 = ``workspace.global_home_root()``）；未接线时
    有项目根的轮次只挂项目侧记忆，无项目根的轮次则没有任何记忆落点（返回空装配，不建目录）。
    """
    member = safe_partition_id(member_id)
    docker = mount is not None and getattr(mount.settings, "is_docker", False)
    global_root = os.path.abspath(str(global_home)) if global_home else None
    try:
        project_home = _project_home(root, member) if root is not None else None
    except (PermissionError, WorkspaceConfigError) as error:
        # 项目家目录的 symlink 祖先指向根外：该层缺席（不写、不读），本轮照常收口。
        _log.warning("project memory home is unusable; memory is absent this round: %s", error)
        return [], []
    if project_home is None and global_root is None:
        # 无项目根又没有全局根：这一轮没有记忆落点。不装中间件、不建目录（ADR-0032 §三）。
        _log.info("agentic memory has no landing directory this round; skipping assembly")
        return [], []

    read_only_dirs: list[tuple[Path, str]] = []
    if offloader is not None:
        read_only_dirs.append((offloader.directory, str(offloader.visible_directory)))

    extra_writable: list[tuple[Path, str, Any]] = []
    global_middlewares: list[Any] = []
    if project_home is not None:
        workdir = project_home
        visible_workdir = (
            posixpath.join(DOCKER_WORKDIR, ALIBOT_DIR, AGENTS_DIR, member) if docker else str(project_home)
        )
        if global_root is not None:
            global_dir = _ensure_global_home(global_root, member)
            if global_dir is not None:
                global_visible = docker_global_member_home(member) if docker else str(global_dir)
                extra_writable.append((global_dir, global_visible, is_memory_file_path))
                global_middlewares.append(GlobalMemoryReadMiddleware(global_root, member, global_visible))
    else:
        # 无项目根：全局家目录是唯一记忆落点，workdir 就是它（不再注入第二段全局段落）。
        assert global_root is not None
        global_dir = _ensure_global_home(global_root, member)
        if global_dir is None:
            return [], []
        workdir = global_dir
        visible_workdir = str(workdir)

    backend = PartitionBackend(
        mount.workspace.get_backend() if docker and mount is not None else LocalBackend(),
        workdir,
        visible_workdir,
        read_only_dirs=tuple(read_only_dirs),
        writable_dirs=tuple(extra_writable),
        write_filter=is_memory_file_path,
    )
    for visible in backend.writable_visible_dirs():
        permission_context.working_directories[str(visible)] = AdditionalWorkingDirectory(
            path=str(visible), source="alibot-memory"
        )
    middlewares = [
        # 相对路径的锚点只有一处：本层 ``strict``。无项目根（或工作区没装配起来）时它是唯一准入面，
        # 相对路径锚到记忆家目录；有项目根时为 ``False``，相对路径仍从项目根起算、交给
        # ``WorkspacePathPermissionMiddleware`` + ``WorkspaceRootGuard``，本层只负责家目录白名单
        # 与工作目录登记（全局家目录只是绝对路径的显式例外，不是第二个工作根）。
        PartitionPermissionMiddleware(backend, strict=mount is None),
        *global_middlewares,
        AgenticMemoryMiddleware(workdir=str(visible_workdir), memory_dir=".", backend=backend),
    ]
    tools = (
        [] if mount is not None else [Read(backend=backend), Write(backend=backend), Edit(backend=backend)]
    )
    return middlewares, tools
