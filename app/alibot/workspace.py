"""LocalWorkspace 的装配层：频道项目根、文件工具根限定与 ``.alibot`` 状态分区。

AgentScope 2.0.10 的内置文件工具**没有**“必须落在工作区根内”的准入闸门（#135 实测：
Read/Glob/Grep 完全不检查；Write/Edit 的 realpath 只影响 ``ACCEPT_EDITS`` 下是否自动放行）。
规格 §3.6 / ADR-0021 因此把根检查放在**装配层**：

- 五个文件工具（Read / Write / Edit / Glob / Grep）各自挂一层 :class:`WorkspaceRootGuard`；
- 每个输入路径先锚到根（相对路径不再落到进程 cwd），再 ``realpath``，必须落在根内；
- ``..``、根外绝对路径、指向根外的 symlink 一律返回 ``ToolResultState.ERROR``；
- 默认工具集**不含 Bash**（D15）：本地 Bash 是主机进程、cwd 不是沙箱。``allow_local_shell``
  只作为标记暴露给权限/审批层（W2），本模块不在那里绕过审批。

AgentScope 自己的 ``data/`` / ``skills/`` / ``sessions/`` / ``.mcp`` 被收进 ``<root>/.alibot``，
于是“同一个项目根”与“每个 Agent 的技能 / MCP / 会话状态隔离”同时成立，项目根不被框架文件污染。

已知残余（#135 §四）：根内 hardlink 指向根外 inode、检查与执行之间的 TOCTOU，都不是基于路径
的检查能识别的；本模块只承诺“路径 realpath 落在根内”。
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import Edit, Glob, Grep, Read, ToolChunk, ToolMiddlewareBase, Write
from agentscope.workspace import LocalWorkspace, WorkspaceBase

#: 本仓共享的框架状态命名空间（规格 §3.6）：与用户项目文件分开。
ALIBOT_DIR = ".alibot"
AGENTS_DIR = "agents"
SESSIONS_DIR = "sessions"

#: 文件工具 → 它接收路径的输入键。Bash 不在这张表里：本模块默认不挂 Bash。
PATH_ARGUMENTS = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}


class WorkspaceRootViolation(Exception):
    """一个工具输入在 ``realpath`` 后落在项目根之外。"""

    def __init__(self, raw: Any, root: str) -> None:
        self.raw = raw
        self.root = root
        super().__init__(f"path escapes workspace root: {raw!r}")


class WorkspaceConfigError(Exception):
    """频道配置的项目根不可用（不是绝对路径 / 不存在 / 不是目录）。"""


@dataclass(frozen=True)
class WorkspaceSettings:
    """一个频道 + 成员解析出来的 workspace 配置。

    ``root`` 是**频道**的项目边界；后端 / 权限档 / 本地 shell 开关是 Agent 级覆盖（缺省跟随
    频道）。本模块只装配 ``local`` 后端；Docker 后端由 W3 在同一 seam 上装配。
    """

    root: str
    backend: str = "local"
    permission_mode: str = "ask-writes"
    allow_local_shell: bool = False
    docker_image: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WorkspaceSettings:
        """从 DB 读路径返回的原始行构造（未知键忽略，空值走默认）。"""
        return cls(
            root=str(raw.get("root") or ""),
            backend=str(raw.get("backend") or "local"),
            permission_mode=str(raw.get("permission_mode") or "ask-writes"),
            allow_local_shell=bool(raw.get("allow_local_shell") or False),
            docker_image=(str(raw["docker_image"]) if raw.get("docker_image") else None),
        )

    @property
    def is_local(self) -> bool:
        return self.backend == "local"


@dataclass
class LocalWorkspaceMount:
    """一轮可用的本地工作区：根、分区、AgentScope 实例与受限工具集。"""

    settings: WorkspaceSettings
    root: str
    member_id: str
    session_id: str
    workspace: WorkspaceBase
    tools: list[Any]
    agent_dir: str
    session_dir: str

    @property
    def allow_local_shell(self) -> bool:
        """``allow_local_shell=1`` 的标记本身（审批仍由权限层负责，不在这里绕过）。"""
        return self.settings.allow_local_shell

    @property
    def tool_names(self) -> set[str]:
        return {str(getattr(tool, "name", "")) for tool in self.tools}


def _safe_partition_id(value: str) -> str:
    """把 member / session id 收敛成一个安全的单级目录名（不允许穿越）。"""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(value or "")).strip()
    if cleaned in ("", ".", ".."):
        return "default"
    return cleaned


def session_partition_id(member_id: str, target_id: str) -> str:
    """``.alibot/sessions/<session_id>`` 的 session id（每成员每 target 一个稳定分区）。"""
    return _safe_partition_id(f"{target_id}--{member_id}")


def _within(real: str, root: str) -> bool:
    if real == root:
        return True
    try:
        return os.path.commonpath([real, root]) == root
    except ValueError:  # Windows 跨盘
        return False


def validate_workspace_root(raw: str) -> str:
    """校验并返回 **realpath** 后的项目根。

    ``LocalWorkspace.workdir`` 用的是 ``abspath``；根本身是 symlink 时两者会不同，导致根内路径
    被误拒。因此本模块只保留一个规范形态：``realpath``。
    """
    if not raw or not os.path.isabs(raw):
        raise WorkspaceConfigError(f"workspace_root must be an absolute path: {raw!r}")
    root = os.path.realpath(raw)
    if not os.path.isdir(root):
        raise WorkspaceConfigError(f"workspace_root is not a directory: {raw!r}")
    return root


def resolve_in_root(raw: Any, root: str) -> str:
    """把工具输入规范化为根内绝对 ``realpath``；根外抛 :class:`WorkspaceRootViolation`。

    相对路径**锚到根**（不是进程 cwd）；``None`` / 空串表示根（只对可选的 Glob/Grep ``path``
    有意义，Read/Write/Edit 传空会被工具自己当作目录/非法路径拒绝）。
    """
    if raw is None or raw == "":
        return root
    if not isinstance(raw, str):
        raise WorkspaceRootViolation(raw, root)
    candidate = raw if os.path.isabs(raw) else os.path.join(root, raw)
    real = os.path.realpath(candidate)
    if not _within(real, root):
        raise WorkspaceRootViolation(raw, root)
    return real


def _reject_dotdot_segments(pattern: Any, root: str) -> None:
    """Glob 的 pattern 是名字匹配，不是路径；仍拒绝 ``..`` 段做纵深防御。"""
    if not isinstance(pattern, str):
        return
    if any(segment == ".." for segment in re.split(r"[\\/]", pattern)):
        raise WorkspaceRootViolation(pattern, root)


class WorkspaceRootGuard(ToolMiddlewareBase):
    """调用前把路径锚到根并做 ``realpath`` 根检查；根外直接产出 ERROR chunk。"""

    def __init__(self, root: str) -> None:
        self.root = root

    async def on_tool_call(  # type: ignore[override]  # 框架基类把它标成协程，实际按异步生成器消费
        self,
        tool: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> AsyncGenerator[ToolChunk, None]:
        key = PATH_ARGUMENTS.get(str(getattr(tool, "name", "")))
        if key is None:  # 不在文件工具清单里：不改变行为（本模块也不挂 Bash）
            async for chunk in next_handler(**input_kwargs):
                yield chunk
            return

        try:
            real = resolve_in_root(input_kwargs.get(key), self.root)
            if tool.name == "Glob":
                _reject_dotdot_segments(input_kwargs.get("pattern"), self.root)
        except WorkspaceRootViolation as error:
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: path escapes workspace root: {error.raw!r}",
                    )
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        rewritten = dict(input_kwargs)
        rewritten[key] = real
        async for chunk in next_handler(**rewritten):
            yield chunk


class ProjectLocalWorkspace(LocalWorkspace):
    """把 AgentScope 的根级状态目录收进 ``<root>/.alibot``。

    ``LocalWorkspace`` 默认把 ``data/`` / ``skills/`` / ``sessions/`` / ``.mcp`` 直接放在
    ``workdir`` 根；那会污染用户项目。这里只改派生路径，不复制框架逻辑：

    - ``skills/`` → ``.alibot/agents/``（技能分区 = ``.alibot/agents/<member_id>/``）
    - ``sessions/`` → ``.alibot/sessions/``
    - ``data/`` → ``.alibot/data/``
    - ``.mcp`` → ``.alibot/mcp.json``
    """

    @property
    def _alibot_dir(self) -> str:
        return os.path.join(self.workdir, ALIBOT_DIR)

    @property
    def _data_dir(self) -> str:
        return os.path.join(self._alibot_dir, "data")

    @property
    def _skills_dir(self) -> str:
        return os.path.join(self._alibot_dir, AGENTS_DIR)

    @property
    def _sessions_dir(self) -> str:
        return os.path.join(self._alibot_dir, SESSIONS_DIR)

    @property
    def _mcp_file(self) -> str:
        return os.path.join(self._alibot_dir, "mcp.json")


def _guarded_tools(workspace: WorkspaceBase, root: str) -> list[Any]:
    """五个文件工具各挂根检查；**刻意不调用** ``workspace.list_tools()``，因为后者无条件含 Bash。"""
    backend = workspace.get_backend()
    guard = WorkspaceRootGuard(root)
    middlewares: list[ToolMiddlewareBase] = [guard]
    return [
        Edit(backend=backend, middlewares=middlewares),
        Glob(backend=backend, middlewares=middlewares),
        Grep(backend=backend, middlewares=middlewares),
        Read(backend=backend, middlewares=middlewares),
        Write(backend=backend, middlewares=middlewares),
    ]


def _ensure_partition(root: str, path: str) -> None:
    """创建 ``.alibot`` 分区，并在**创建前**拒绝根外 symlink 祖先（不产生根外状态写入）。"""
    current = root
    relative = os.path.relpath(path, root)
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        if os.path.lexists(current) and not _within(os.path.realpath(current), root):
            raise WorkspaceConfigError(f"workspace state partition escapes root: {current!r}")
    os.makedirs(path, exist_ok=True)
    if not _within(os.path.realpath(path), root):
        raise WorkspaceConfigError(f"workspace state partition escapes root: {path!r}")


async def assemble_local_workspace(
    settings: WorkspaceSettings,
    *,
    member_id: str,
    session_id: str,
) -> LocalWorkspaceMount:
    """按频道项目根装配一轮可用的 LocalWorkspace（文件工具受限、默认无 Bash）。"""
    root = validate_workspace_root(settings.root)
    member = _safe_partition_id(member_id)
    session = _safe_partition_id(session_id)
    agent_dir = os.path.join(root, ALIBOT_DIR, AGENTS_DIR, member)
    session_dir = os.path.join(root, ALIBOT_DIR, SESSIONS_DIR, session)
    _ensure_partition(root, agent_dir)
    _ensure_partition(root, session_dir)

    workspace = ProjectLocalWorkspace(workdir=root)
    await workspace.initialize()

    return LocalWorkspaceMount(
        settings=settings,
        root=root,
        member_id=member,
        session_id=session,
        workspace=workspace,
        tools=_guarded_tools(workspace, root),
        agent_dir=agent_dir,
        session_dir=session_dir,
    )
