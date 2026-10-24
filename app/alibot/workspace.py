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

import asyncio
import atexit
import logging
import os
import posixpath
import re
import threading
import time
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import Bash, Edit, Glob, Grep, Read, ToolChunk, ToolMiddlewareBase, Write
from agentscope.workspace import DockerBackend, DockerWorkspace, LocalWorkspace, WorkspaceBase

from .i18n import SOURCE_LOCALE, Locale, render

_log = logging.getLogger(__name__)

#: 本仓共享的框架状态命名空间（规格 §3.6）：与用户项目文件分开。
ALIBOT_DIR = ".alibot"
AGENTS_DIR = "agents"
SESSIONS_DIR = "sessions"

#: 全局层（Agent 家目录）根的目录名：固定挂在用户 HOME 下（ADR-0031），
#: **不随** ``--data-dir`` / ``ALIBOT_DATA_DIR`` 移动。
GLOBAL_HOME_DIRNAME = ".alibot"

#: Docker 容器内的全局层挂载点前缀（ADR-0031 §三）。本票只提供常量与路径解析，
#: bind 落地由后续票负责；它**不是**文件工具的根。
DOCKER_GLOBAL_HOME = "/alibot-home"

#: 家目录里的规则文件名：全局基座 ``agents/<member_id>/AGENTS.md``；项目补充
#: ``.alibot/agents/<member_id>/AGENTS.md``（ADR-0032：两层同构、扁平）。
RULES_FILE = "AGENTS.md"
#: 家目录里的技能目录（成员私有技能；全局技能库在 ``<global_home>/skills``）。
SKILLS_DIR = "skills"

#: Docker 工作区在容器内的固定根（AgentScope ``DockerWorkspace.workdir``）。
DOCKER_WORKDIR = "/workspace"

#: 本仓创建的 Docker 工作区 id 前缀；启动清扫只处理这一前缀，不碰其它 AgentScope 工作区。
DOCKER_WORKSPACE_PREFIX = "alibot-"

#: 容器空闲多久后回收（规格 §3.6“空闲回收”）。容器保留时下一轮 ~0 冷启动；镜像永不随容器删除。
DOCKER_IDLE_TTL_SECONDS = 600.0

#: 启动/初始化失败的短 TTL 缓存：避免每轮重试 30 s gateway 等待把轮次堆起来。
DOCKER_FAILURE_TTL_SECONDS = 30.0

#: 初始化超时：镜像已存在时一条容器启动路径；需要构建/拉取时给足网络与 apt/PyPI 时间。
DOCKER_INIT_TIMEOUT_SECONDS = 30.0
DOCKER_BUILD_TIMEOUT_SECONDS = 180.0

#: 容器 env 白名单（ADR-0021 §五：库不存密钥，也不把整个进程环境倒进容器）。
DOCKER_ENV_ALLOWLIST = ("LANG", "LC_ALL", "TZ")

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


class DockerWorkspaceUnavailable(Exception):
    """Docker 后端不可用；``str(exc)`` 就是给用户看的可读原因（不回落本地）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class WorkspaceSettings:
    """一个频道 + 成员解析出来的 workspace 配置。

    ``root`` 是**频道**的项目边界；后端 / 权限档 / 本地 shell 开关是 Agent 级覆盖（缺省跟随
    频道）。``docker_image_consented`` 是 DB 读路径按 ``remote_consent_scope('docker', image)``
    算出的**配置事实**：本模块只消费它，不在装配时写库。

    ``docker_egress`` 是**频道级**的容器出网开关（#187 / ADR-0026 §五）：``False``（缺省）⇒
    容器以 ``--network none`` 创建。它只管**容器接不接网络**；按 host 的白名单在工具层
    （``web.EgressPolicy``），因为两个联网工具跑在宿主进程里，容器无网挡不住它们。
    """

    root: str
    backend: str = "local"
    permission_mode: str = "ask-writes"
    allow_local_shell: bool = False
    docker_image: str | None = None
    docker_image_consented: bool = False
    docker_egress: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WorkspaceSettings:
        """从 DB 读路径返回的原始行构造（未知键忽略，空值走默认）。"""
        return cls(
            root=str(raw.get("root") or ""),
            backend=str(raw.get("backend") or "local"),
            permission_mode=str(raw.get("permission_mode") or "ask-writes"),
            allow_local_shell=bool(raw.get("allow_local_shell") or False),
            docker_image=(str(raw["docker_image"]) if raw.get("docker_image") else None),
            docker_image_consented=bool(raw.get("docker_image_consented") or False),
            docker_egress=bool(raw.get("docker_egress") or False),
        )

    @property
    def is_local(self) -> bool:
        return self.backend == "local"

    @property
    def is_docker(self) -> bool:
        return self.backend == "docker"


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


def safe_partition_id(value: str) -> str:
    """把 member / session id 收敛成一个安全的单级目录名（不允许穿越）。"""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(value or "")).strip()
    if cleaned in ("", ".", ".."):
        return "default"
    return cleaned


def session_partition_id(member_id: str, target_id: str) -> str:
    """会话状态的 session id（每成员每 target 一个稳定分区）。"""
    return safe_partition_id(f"{target_id}--{member_id}")


def project_session_dir(project_root: str, session_id: str) -> str:
    """有项目根的轮次：``<项目根>/.alibot/sessions/<session_id>``（ADR-0032 §四）。"""
    return os.path.join(project_root, ALIBOT_DIR, SESSIONS_DIR, safe_partition_id(session_id))


def data_session_dir(data_root: str, session_id: str) -> str:
    """无项目根的轮次：``<数据目录>/sessions/<session_id>``（不再套一层 ``.alibot``）。"""
    return os.path.join(data_root, SESSIONS_DIR, safe_partition_id(session_id))


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


def global_home_root(environ: Mapping[str, str] | None = None) -> str:
    """全局层根：固定为 ``<HOME>/.alibot``（ADR-0031）。

    它**只**看 HOME，**不读** ``--data-dir`` / ``ALIBOT_DATA_DIR``：数据目录覆盖只改变数据
    目录本身，不移动全局层。
    """
    source = os.environ if environ is None else environ
    home = str(source.get("HOME") or "").strip()
    if not home:
        home = os.path.expanduser("~")
    return os.path.join(home, GLOBAL_HOME_DIRNAME)


def global_member_home(member_id: str, *, root: str | None = None) -> str:
    """成员家目录 ``<全局根>/agents/<member_id>``（Agent 家目录解析的唯一入口）。"""
    base = root if root is not None else global_home_root()
    return os.path.join(base, AGENTS_DIR, safe_partition_id(member_id))


def docker_global_member_home(member_id: str) -> str:
    """容器内成员家目录 ``/alibot-home/agents/<member_id>``（后续票的只读挂载目标）。"""
    return posixpath.join(DOCKER_GLOBAL_HOME, AGENTS_DIR, safe_partition_id(member_id))


def docker_global_skills_home() -> str:
    """容器内公共技能库挂载点 ``/alibot-home/skills``（后续票的只读挂载目标）。"""
    return posixpath.join(DOCKER_GLOBAL_HOME, SKILLS_DIR)


def docker_global_binds(
    member_id: str, *, global_root: str | None = None
) -> tuple[tuple[str, str, str], ...]:
    """全局层的两条 bind 规格 ``(宿主源, 容器内目标, 模式)``，顺序固定。

    - 成员家目录 ``<全局根>/agents/<member_id>`` → ``/alibot-home/agents/<member_id>``，**``ro``**
      （ADR-0034 §四：记忆由宿主进程内的 ReMe 写，容器不需要写权限）；
    - 公共技能库 ``<全局根>/skills`` → ``/alibot-home/skills``，仍 **``ro``**。

    宿主目录**不存在就跳过**该条，且这里**只判断、不创建**（ADR-0031：全局层是人的维护面，
    不能依赖 Docker daemon 对不存在的 bind 源自动建目录）。数据目录（``<全局根>/data``）与
    全局根整根都**不在**返回结果里。
    """
    base = global_root if global_root is not None else global_home_root()
    candidates = (
        (global_member_home(member_id, root=base), docker_global_member_home(member_id), "ro"),
        (os.path.join(base, SKILLS_DIR), docker_global_skills_home(), "ro"),
    )
    return tuple((host, target, mode) for host, target, mode in candidates if os.path.isdir(host))


def ensure_member_home(global_root: str, member_id: str) -> str:
    """创建（必要时）成员家目录 ``<全局根>/agents/<member_id>`` 这一级。

    长期记忆（ReMe）经 :func:`alibot.memory.memory_workspace_dir` 调用它建出这一级，再建
    ``reme/`` 工作区；应用**不**创建 ``<全局根>/skills/``、不创建 ``AGENTS.md``、不创建
    ``mcp.json``（ADR-0034 §一）。根外 symlink 祖先直接拒绝（``WorkspaceConfigError``），
    调用方按「该层缺席」降级。
    """
    root = os.path.realpath(global_root)
    home = global_member_home(member_id, root=root)
    ensure_state_partition(root, home)
    return home


def _resolve_read_path(root: str, parts: tuple[str, ...]) -> tuple[str | None, str | None]:
    """只读路径守卫的内核：``(realpath, None)`` 或 ``(None, 原因)``。

    原因只有两种：``escape``（根外 symlink 祖先 / 根外目标）与 ``absent``（不存在）。
    """
    root_real = os.path.realpath(root)
    candidate = os.path.join(root_real, *parts)
    real = os.path.realpath(candidate)
    if not _within(real, root_real):
        return None, "escape"
    if not os.path.exists(real):
        return None, "absent"
    return real, None


def resolve_global_read_path(root: str, *parts: str) -> str | None:
    """全局层**公开只读守卫**（#232 技能 / #233 MCP / #234 记忆共用）。

    以 ``root`` 自己的 ``realpath`` 为根，拒绝根外 symlink 祖先与根外目标；路径不存在
    （含父目录缺失、断链）返回 ``None``，调用方按「该层缺席」处理。它**只读**：不创建、
    不写入任何目录或文件，也不把这一层变成文件工具的工作根。
    """
    if not root:
        return None
    real, _reason = _resolve_read_path(root, parts)
    return real


def _read_layer_text(root: str | None, parts: tuple[str, ...], *, layer: str) -> str | None:
    """只读读取一层里的一份文本；任何降级都返回 ``None`` 并记一条可读日志。

    守卫与 :func:`resolve_global_read_path` **共用同一个内核**：以**自己的根**做
    ``realpath``，根外 symlink 祖先与根外目标一律拒绝（ADR-0031 §三）。缺失 / 不可读 /
    非 UTF-8 都只是「这一段缺席」，不创建目录、不写占位、不阻断本轮。
    """
    if root is None:
        return None
    root_real = os.path.realpath(root)
    real, reason = _resolve_read_path(root_real, parts)
    if real is None:
        candidate = os.path.join(root_real, *parts)
        if reason == "escape":
            _log.warning("%s rule path escapes its root: %s", layer, candidate)
        else:
            _log.info("%s rule file is absent: %s", layer, candidate)
        return None
    try:
        data = Path(real).read_bytes()
    except FileNotFoundError:  # realpath 与读取之间的删除：等价于缺席
        _log.info("%s rule file is absent: %s", layer, real)
        return None
    except OSError as error:
        _log.warning("%s rule file is unreadable: %s (%s)", layer, real, error)
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        _log.warning("%s rule file is not UTF-8: %s (%s)", layer, real, error)
        return None
    if not text:
        _log.info("%s rule file is empty: %s", layer, real)
        return None
    return text


def read_global_rules(global_root: str, member_id: str) -> str | None:
    """读取全局基座 ``agents/<member_id>/AGENTS.md``；缺席时返回 ``None``。"""
    return _read_layer_text(
        global_root,
        (AGENTS_DIR, safe_partition_id(member_id), RULES_FILE),
        layer="global layer",
    )


def read_project_rules(project_root: str | None, member_id: str) -> str | None:
    """读取项目补充 ``.alibot/agents/<member_id>/AGENTS.md``；无项目根时直接缺席。"""
    if project_root is None:
        return None
    return _read_layer_text(
        project_root,
        (ALIBOT_DIR, AGENTS_DIR, safe_partition_id(member_id), RULES_FILE),
        layer="project layer",
    )


def _reject_dotdot_segments(pattern: Any, root: str) -> None:
    """Glob 的 pattern 是名字匹配，不是路径；仍拒绝 ``..`` 段做纵深防御。"""
    if not isinstance(pattern, str):
        return
    if any(segment == ".." for segment in re.split(r"[\\/]", pattern)):
        raise WorkspaceRootViolation(pattern, root)


class WorkspaceRootGuard(ToolMiddlewareBase):
    """调用前把路径锚到根并做 ``realpath`` 根检查；根外直接产出 ERROR chunk。

    文件工具只有一个工作根：**频道项目根**。相对路径从它起算，绝对路径 ``realpath`` 后必须仍在
    根内（ADR-0021 §一）。家目录不再是白名单例外（ADR-0034 §四：记忆不再由文件工具写入）。
    """

    def __init__(self, root: str) -> None:
        self.root = root

    def resolve(self, raw: Any) -> str:
        """把工具输入规范化为根内绝对路径；根外抛 :class:`WorkspaceRootViolation`。"""
        return resolve_in_root(raw, self.root)

    async def on_tool_call(
        self,
        tool: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> Any:
        """框架把基类标成协程、实际按异步生成器消费。

        返回类型写 ``Any``（与 ``PartitionPermissionMiddleware.on_check_permission`` 同一约定）：
        ``AsyncGenerator`` 会与上游声明的 ``Coroutine`` 冲突而需要 ``# type: ignore[override]``，
        那是一条**看起来像**未使用、实则必需的 ignore（pi-lens 自带的 mypy 看不到 agentscope 时会
        把它误报成 unused）。``Any`` 对两种形状都兼容，行为零变化。
        """
        key = PATH_ARGUMENTS.get(str(getattr(tool, "name", "")))
        if key is None:  # 不在文件工具清单里：不改变行为（本模块也不挂 Bash）
            async for chunk in next_handler(**input_kwargs):
                yield chunk
            return

        raw = input_kwargs.get(key)
        try:
            real = self.resolve(raw)
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


class _AlibotLayoutMixin:
    """把 AgentScope 的根级状态目录收进 ``<workdir>/.alibot``。

    ``LocalWorkspace`` / ``DockerWorkspace`` 默认把 ``data/`` / ``skills/`` / ``sessions/`` /
    ``.mcp`` 直接放在 ``workdir`` 根；那会污染用户项目。这里只改派生路径，不复制框架逻辑：

    - ``skills/`` → ``.alibot/agents/``（技能分区 = ``.alibot/agents/<member_id>/``）
    - ``sessions/`` → ``.alibot/sessions/``
    - ``data/`` → ``.alibot/data/``
    - ``.mcp`` → ``.alibot/mcp.json``

    依赖上游 2.0.10 的四个私有 property（研究 #134 §6.2 方案 1）；升级 agentscope 时重跑
    ``test_docker_workspace.py`` / ``test_local_workspace.py``。
    """

    #: 由 ``LocalWorkspace`` / ``DockerWorkspace.__init__`` 赋值（本地是项目根，Docker 是 /workspace）。
    workdir: str

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


class ProjectLocalWorkspace(_AlibotLayoutMixin, LocalWorkspace):
    """本地后端：项目根共享，框架状态在 ``<root>/.alibot``。"""


class ProjectDockerWorkspace(_AlibotLayoutMixin, DockerWorkspace):
    """Docker 后端：``/workspace`` 挂到频道根，框架状态同样收进 ``.alibot``。

    多出来的一件事是**容器网络策略**（#187 / ADR-0026 §五）：``docker_egress`` 由
    :func:`assemble_docker_workspace` 在 ``initialize()`` 之前赋值，缺省 ``False`` = 容器无网。
    """

    #: 频道级的容器出网开关；缺省 ``False``，于是「忘记赋值」也不会放开出网。
    docker_egress: bool = False

    #: 全局层 bind 规格 ``(宿主源, 容器内目标, 模式)``；由装配点在 ``initialize()`` 之前赋值，
    #: 缺省空元组 = 没有全局层（不创建目录、不挂载）。
    global_binds: tuple[tuple[str, str, str], ...] = ()

    async def _create_and_start_container(self) -> None:
        """覆写上游创建路径，把 ``HostConfig.NetworkMode`` 写进容器配置（#187）。

        为什么必须动这个**私有**方法：AgentScope 2.0.10 的 ``_create_and_start_container`` 自己
        组装 ``config`` / ``HostConfig``（只写 ``Binds``），``create_or_replace(config=…)`` 也不
        接收网络参数——上游没有网络钩子，也没有第二处可注入的接缝。本仓因此把那份 config 的
        **构造**收进纯函数 :func:`docker_container_config`，这里只保留上游那三步
        （``create_or_replace`` → ``start`` → 建 backend）。升级 agentscope 时重跑
        ``test_docker_network_policy.py``（不起 daemon 的配置断言）与
        ``test_docker_workspace.py``（有 daemon 的端到端断言）。
        """
        config = docker_container_config(
            image_tag=self._image_tag,
            workspace_id=self.workspace_id,
            env=self.env,
            host_workdir=self.host_workdir,
            network_mode=docker_network_mode(self.docker_egress),
            global_binds=self.global_binds,
        )
        self._container = await self._client.containers.create_or_replace(
            name=f"as_ws_{self.workspace_id}",
            config=config,
        )
        await self._container.start()
        self._backend = DockerBackend(self._container, DOCKER_WORKDIR)


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


class AskEveryTimeBash(Bash):
    """本地 shell：**每次调用**都要人类确认（D15 / ADR-0021 §二）。

    默认不挂载；只有 ``allow_local_shell=1`` 才装配。它不声称根限定——命令可以 ``cd /`` 或读任意
    绝对路径，工具说明必须把这一点写出来。权限判定上它永远返回 ASK，且 ``bypass_immune=True``
    使 allow 规则与 ``trusted-writes`` 都不能把它变成自动放行；``read-only`` 档不装配它
    （``EXPLORE`` 没有“询问”这一档）。
    """

    description = (
        Bash.description
        + "\n\n警告：这是本机进程、没有沙箱；命令可以越过项目根（例如 cd / 或读任意绝对路径）。"
        "每次调用都需要人类逐次审批。"
    )
    is_read_only = False

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        return False

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Permission required: local shell command",
            decision_reason="Local shell is unsandboxed and runs on the host",
            bypass_immune=True,
        )


def local_shell_tool(root: str) -> Any:
    """装配一个 ``cwd=root`` 的本地 Bash；审批由权限引擎逐次发起。"""
    return AskEveryTimeBash(cwd=root)


def ensure_state_partition(root: str, path: str) -> None:
    """创建 ``.alibot`` 分区，并在**创建前**拒绝根外 symlink 祖先（不产生根外状态写入）。

    创建失败（父路径是普通文件、无写权限、磁盘满等）一律转成可读的
    :class:`WorkspaceConfigError`：调用方（工作区装配、会话状态、记忆家目录）都按「该分区不可用」
    统一处理，不把底层 ``OSError`` 直接泄给模型或用户。
    """
    current = root
    relative = os.path.relpath(path, root)
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        if os.path.lexists(current) and not _within(os.path.realpath(current), root):
            raise WorkspaceConfigError(f"workspace state partition escapes root: {current!r}")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as error:
        raise WorkspaceConfigError(f"workspace state partition is unusable: {path!r} ({error})") from error
    if not _within(os.path.realpath(path), root):
        raise WorkspaceConfigError(f"workspace state partition escapes root: {path!r}")


async def assemble_local_workspace(
    settings: WorkspaceSettings,
    *,
    member_id: str,
    session_id: str,
) -> LocalWorkspaceMount:
    """按频道项目根装配一轮可用的 LocalWorkspace（文件工具受限、默认无 Bash）。

    项目层家目录（``.alibot/agents/<member_id>/``）只是规则文件的宿主，不进入文件工具白名单：
    文件工具唯一的根是频道项目根（ADR-0021 §一 / ADR-0034 §四）。
    """
    root = validate_workspace_root(settings.root)
    member = safe_partition_id(member_id)
    session = safe_partition_id(session_id)
    agent_dir = os.path.join(root, ALIBOT_DIR, AGENTS_DIR, member)
    session_dir = project_session_dir(root, session)
    ensure_state_partition(root, agent_dir)
    ensure_state_partition(root, session_dir)
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


# ─── Docker 后端（#150） ────────────────────────────────────────────────────


#: 出网开关打开时容器接的网络：Docker 的 ``HostConfig.NetworkMode`` 只有「网络名 / none」两态，
#: 没有「按 host 过滤」这一档。host 级白名单因此由**工具层**执行（``web.EgressPolicy``），
#: NetworkMode 只负责「容器到底接不接网络」这半件事（ADR-0026 §五：不做通用网络策略 DSL）。
DOCKER_EGRESS_NETWORK = "bridge"


def docker_network_mode(docker_egress: bool) -> str:
    """频道出网开关 → 容器网络模式：关（缺省）= ``none``，开 = :data:`DOCKER_EGRESS_NETWORK`。"""
    return DOCKER_EGRESS_NETWORK if docker_egress else "none"


def docker_container_config(
    *,
    image_tag: str | None,
    workspace_id: str,
    env: Mapping[str, str] | None,
    host_workdir: str | None,
    network_mode: str,
    global_binds: Sequence[tuple[str, str, str]] = (),
) -> dict[str, Any]:
    """容器配置 dict（含 ``HostConfig``）：上游 2.0.10 的构造 + 网络策略 + 全局层挂载。

    逐字对齐 ``DockerWorkspace._create_and_start_container`` 的 config（镜像 / ``sleep infinity`` /
    working dir / label / env / bind mount），唯一多出来的是 ``HostConfig["NetworkMode"]`` 与
    ``global_binds`` 追加的条目——上游**没有任何**网络或额外 bind 的参数位
    （``create_or_replace(config=…)`` 也不接收）。抽成纯函数是为了让「网络模式与 bind mount 都写进了
    容器配置」这条判据**不需要 daemon** 就能断言。

    ``global_binds`` 每项是 ``(宿主源, 容器内目标, 模式)``，按给定顺序追加在 ``/workspace:rw`` 之后；
    模式由调用方给出（ADR-0034 §四：``docker_global_binds`` 现在两条都给 ``ro``），
    调用方（:func:`docker_global_binds`）已剔除不存在的宿主目录。
    """
    config: dict[str, Any] = {
        "Image": image_tag,
        "Cmd": ["sleep", "infinity"],
        "WorkingDir": DOCKER_WORKDIR,
        "Labels": {
            "agentscope.workspace": "true",
            "agentscope.workspace.id": workspace_id,
        },
    }
    if env:
        config["Env"] = [f"{name}={value}" for name, value in env.items()]

    host_config: dict[str, Any] = {"NetworkMode": network_mode}
    binds: list[str] = []
    if host_workdir is not None:
        binds.append(f"{os.path.abspath(host_workdir)}:{DOCKER_WORKDIR}:rw")
    for host_source, container_target, mode in global_binds:
        binds.append(f"{os.path.abspath(host_source)}:{container_target}:{mode}")
    if binds:
        host_config["Binds"] = binds
    config["HostConfig"] = host_config
    return config


def docker_workspace_id(member_id: str, session_id: str) -> str:
    """稳定的容器工作区 id（容器名 = ``as_ws_<id>``）。

    不用随机 UUID：崩溃重启后同一个 id 让 ``create_or_replace`` 替换遗留容器，启动清扫也能
    按前缀识别本仓容器（研究 #134 §6.1）。
    """
    return DOCKER_WORKSPACE_PREFIX + safe_partition_id(f"{member_id}--{session_id}")


def docker_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """只挑白名单里的进程环境值；绝不透传整个 ``os.environ``（ADR-0021 §五）。"""
    source = os.environ if environ is None else environ
    return {name: source[name] for name in DOCKER_ENV_ALLOWLIST if source.get(name)}


def resolve_in_container_root(raw: Any, root: str) -> str:
    """把工具输入锚到容器 ``/workspace``，并用**主机侧** realpath 做根检查。

    容器内工具本身可以读写 ``/etc``、``/root``（研究 #134 §3.1），所以装配层必须：

    - 绝对路径只接受 ``/workspace`` 及其子路径；
    - 相对路径锚到 ``/workspace``（不是容器进程 cwd 之外的任何位置）；
    - 映射到 ``host_workdir`` 后 ``realpath``，根外 symlink 同样拒绝；
    - 返回容器内路径（工具在容器里执行）。
    """
    if raw is None or raw == "":
        return DOCKER_WORKDIR
    if not isinstance(raw, str):
        raise WorkspaceRootViolation(raw, DOCKER_WORKDIR)

    if raw.startswith("/"):
        if raw != DOCKER_WORKDIR and not raw.startswith(DOCKER_WORKDIR + "/"):
            raise WorkspaceRootViolation(raw, DOCKER_WORKDIR)
        relative = raw[len(DOCKER_WORKDIR) :].lstrip("/")
    else:
        relative = raw

    if any(segment == ".." for segment in re.split(r"[\\/]", relative)):
        raise WorkspaceRootViolation(raw, DOCKER_WORKDIR)

    host_candidate = os.path.join(root, relative) if relative else root
    real = os.path.realpath(host_candidate)
    if not _within(real, root):
        raise WorkspaceRootViolation(raw, DOCKER_WORKDIR)

    relative_real = os.path.relpath(real, root)
    if relative_real == ".":
        return DOCKER_WORKDIR
    return posixpath.join(DOCKER_WORKDIR, relative_real.replace(os.sep, "/"))


class ContainerRootGuard(WorkspaceRootGuard):
    """Docker 文件工具的根检查：容器路径先映射到 host_workdir 再 ``realpath``。

    容器内只有 ``/workspace`` 一个文件工具根；``/alibot-home`` 是只读挂载，不进入工具面。
    """

    def __init__(self, root: str) -> None:
        super().__init__(DOCKER_WORKDIR)
        self.host_root = root

    def resolve(self, raw: Any) -> str:
        return resolve_in_container_root(raw, self.host_root)


def _docker_tools(workspace: WorkspaceBase, root: str) -> list[Any]:
    """容器内六个工具：Bash 默认开放（ADR-0021 §二），五个文件工具仍做 ``/workspace`` 根检查。"""
    backend = workspace.get_backend()
    guard = ContainerRootGuard(root)
    middlewares: list[ToolMiddlewareBase] = [guard]
    return [
        Bash(backend=backend),
        Edit(backend=backend, middlewares=middlewares),
        Glob(backend=backend, middlewares=middlewares),
        Grep(backend=backend, middlewares=middlewares),
        Read(backend=backend, middlewares=middlewares),
        Write(backend=backend, middlewares=middlewares),
    ]


@dataclass
class DockerWorkspaceMount:
    """一轮可用的 Docker 工作区；``release`` 只归还池条目，不删除容器。"""

    settings: WorkspaceSettings
    root: str
    member_id: str
    session_id: str
    workspace: ProjectDockerWorkspace
    tools: list[Any]
    agent_dir: str
    session_dir: str
    workspace_id: str
    container_name: str
    reused: bool
    entry: _DockerEntry

    @property
    def allow_local_shell(self) -> bool:
        """Docker 后端不使用本地 shell 通道（Bash 已在容器里）。"""
        return False

    @property
    def tool_names(self) -> set[str]:
        return {str(getattr(tool, "name", "")) for tool in self.tools}


@dataclass
class _DockerEntry:
    """进程内池条目：一个 (member, session) 对一个容器。"""

    key: tuple[str, str]
    workspace: ProjectDockerWorkspace
    settings: WorkspaceSettings
    root: str
    member_id: str
    session_id: str
    workspace_id: str
    container_name: str
    last_used: float
    active: int = 0
    image: str | None = None
    egress: bool = False
    global_binds: tuple[tuple[str, str, str], ...] = ()


_pool: dict[tuple[str, str], _DockerEntry] = {}
_pool_lock = threading.Lock()
_failures: dict[str, tuple[str, float]] = {}
_failure_lock = threading.Lock()
_reaper_started = False
_reaper_lock = threading.Lock()


def record_docker_failure(member_id: str, reason: str) -> None:
    """记下可读原因（短 TTL）；``WakeOrchestrator.availability`` 经它把成员标 offline。"""
    with _failure_lock:
        _failures[str(member_id)] = (reason, time.monotonic())


def clear_docker_failure(member_id: str | None = None) -> None:
    with _failure_lock:
        if member_id is None:
            _failures.clear()
        else:
            _failures.pop(str(member_id), None)


def docker_unavailable_reason(member_id: str) -> str | None:
    """失败缓存里的可读原因；过期条目在读取时清掉（不是后台计时器）。"""
    now = time.monotonic()
    with _failure_lock:
        record = _failures.get(str(member_id))
        if record is None:
            return None
        reason, recorded_at = record
        if now - recorded_at >= DOCKER_FAILURE_TTL_SECONDS:
            _failures.pop(str(member_id), None)
            return None
        return reason


async def _image_present_locally(image: str) -> bool:
    """镜像是否已在本机（含已构建的 agentscope 镜像 tag）。探活失败按“不存在”处理。"""
    import aiodocker

    client = aiodocker.Docker()
    try:
        await client.images.inspect(image)
        return True
    except Exception:
        return False
    finally:
        with suppress(Exception):
            await client.close()


async def _delete_container(name: str) -> bool:
    """用**当前 loop** 的新 client 删除容器；不假设旧 client 还活着。"""
    import aiodocker

    client = aiodocker.Docker()
    try:
        container = await client.containers.get(name)
        await container.delete(force=True)
        return True
    except Exception:
        return False
    finally:
        with suppress(Exception):
            await client.close()


def _docker_reason(error: BaseException, locale: Locale, *, timeout: float, image: str | None) -> str:
    """把实测的五类失败形状（研究 #134 §五）映射成一句可读原因。"""
    label = image or "AgentScope 默认镜像"
    if isinstance(error, TimeoutError):
        return render("workspace.docker-timeout", locale, seconds=f"{timeout:.0f}", image=label)
    if isinstance(error, ModuleNotFoundError):
        return render("workspace.docker-dependency", locale, error=error)
    try:
        from aiodocker.exceptions import DockerError, DockerStreamError
    except Exception:  # 依赖缺失时不能掩盖原始错误
        return render("workspace.docker-failed", locale, type=type(error).__name__, error=error)
    if isinstance(error, DockerStreamError):
        return render("workspace.docker-image-failed", locale, image=label, error=str(error))
    if isinstance(error, DockerError):
        if error.status == 900:
            return render("workspace.docker-daemon", locale, error=str(error))
        return render("workspace.docker-failed", locale, type=type(error).__name__, error=error)
    if isinstance(error, (FileExistsError, PermissionError)):
        return render("workspace.docker-mount", locale, type=type(error).__name__, error=error)
    if isinstance(error, RuntimeError):
        # gateway 健康等待失败：消息已含 gateway.log 尾部。
        return render("workspace.docker-failed", locale, type=type(error).__name__, error=error)
    return render("workspace.docker-failed", locale, type=type(error).__name__, error=error)


def _ensure_layout(root: str, member_id: str, session_id: str) -> tuple[str, str]:
    member = safe_partition_id(member_id)
    agent_dir = os.path.join(root, ALIBOT_DIR, AGENTS_DIR, member)
    session_dir = project_session_dir(root, session_id)
    ensure_state_partition(root, agent_dir)
    ensure_state_partition(root, session_dir)
    return agent_dir, session_dir


async def _rebind_entry(entry: _DockerEntry) -> bool:
    """把池里的容器重新绑到当前 round 的 event loop。

    AgentScope 的 ``DockerWorkspace`` 把 aiodocker client 建在创建它的 loop 上；seam 每轮用
    ``asyncio.run``。跨 loop 复用必须换 client + 容器句柄 + backend（研究 #134 §6.1 的
    “每轮复用”在 AgentScope 的 loop 语义下需要这一层）。
    """
    import aiodocker

    client = aiodocker.Docker()
    try:
        container = await client.containers.get(entry.container_name)
        info = await container.show()
        if not bool((info.get("State") or {}).get("Running")):
            with suppress(Exception):
                await client.close()
            return False
        workspace = entry.workspace
        workspace._client = client
        workspace._container = container
        workspace._backend = DockerBackend(container, DOCKER_WORKDIR)
        workspace.is_alive = True
        return True
    except Exception:
        with suppress(Exception):
            await client.close()
        return False


def _new_mount(entry: _DockerEntry, *, reused: bool) -> DockerWorkspaceMount:
    agent_dir, session_dir = _ensure_layout(entry.root, entry.member_id, entry.session_id)
    return DockerWorkspaceMount(
        settings=entry.settings,
        root=entry.root,
        member_id=entry.member_id,
        session_id=entry.session_id,
        workspace=entry.workspace,
        tools=_docker_tools(entry.workspace, entry.root),
        agent_dir=agent_dir,
        session_dir=session_dir,
        workspace_id=entry.workspace_id,
        container_name=entry.container_name,
        reused=reused,
        entry=entry,
    )


async def assemble_docker_workspace(
    settings: WorkspaceSettings,
    *,
    member_id: str,
    session_id: str,
    locale: Locale = SOURCE_LOCALE,
) -> DockerWorkspaceMount:
    """按 Agent/会话装配 DockerWorkspace（稳定 id、挂载频道根、env 白名单、失败即离线）。

    全局层的两条 bind（成员家目录 / 公共技能库）都由 :func:`docker_global_binds` 按**宿主目录
    是否存在**算出，两者都是 ``ro``：记忆由进程内的 ReMe 写宿主路径，不经过容器（ADR-0034 §四）。
    """
    if not settings.is_docker:
        raise ValueError(f"not a docker workspace: {settings.backend!r}")

    root = validate_workspace_root(settings.root)
    member = safe_partition_id(member_id)
    session = safe_partition_id(session_id)
    key = (member, session)
    workspace_id = docker_workspace_id(member, session)
    container_name = f"as_ws_{workspace_id}"
    # 全局层 bind 规格（宿主目录缺失则少一条）；在池复用判定与 initialize() 之前算好。
    global_binds = docker_global_binds(member)

    # CI（Linux runner）回归：容器以 root 创建 `/workspace/.alibot/...`，bind mount 上这些
    # 目录的属主是容器 root；host 在 initialize 之后再 `makedirs` 会拿 PermissionError。
    # 因此 **先由 host 建好完整分区**，容器 `mkdir -p` 看到已存在的用户目录。
    _ensure_layout(root, member, session)

    cached = docker_unavailable_reason(member)
    if cached is not None:
        raise DockerWorkspaceUnavailable(cached)

    # 复用：上一轮结束后 client 已关、容器仍在；换到当前 loop 的 client 后继续用。
    image = settings.docker_image
    with _pool_lock:
        pooled = _pool.get(key)
    if pooled is not None and (
        pooled.image != settings.docker_image
        or pooled.egress != settings.docker_egress
        or pooled.global_binds != global_binds
    ):
        # 配置写路径改了镜像 / 出网开关，或全局层目录后来才建（bind 规格变化）：池里的容器按
        # 旧配置建的，不能复用（否则「改了配置却没生效」）。删掉旧容器后按新配置重建；
        # 镜像本身不随容器删除。
        with _pool_lock:
            if _pool.get(key) is pooled:
                _pool.pop(key, None)
        with suppress(Exception):
            await _delete_container(container_name)
        pooled = None
    if pooled is not None:
        if await _rebind_entry(pooled):
            with _pool_lock:
                pooled.active += 1
                pooled.last_used = time.monotonic()
            clear_docker_failure(member)
            try:
                return _new_mount(pooled, reused=True)
            except Exception as error:
                with suppress(Exception):
                    await pooled.workspace.close()
                with suppress(Exception):
                    await _delete_container(container_name)
                with _pool_lock:
                    if _pool.get(key) is pooled:
                        _pool.pop(key, None)
                reason = _docker_reason(error, locale, timeout=DOCKER_INIT_TIMEOUT_SECONDS, image=image)
                record_docker_failure(member, reason)
                raise DockerWorkspaceUnavailable(reason) from error
        with _pool_lock:
            if _pool.get(key) is pooled:
                _pool.pop(key, None)
        with suppress(Exception):
            await _delete_container(container_name)

    # 远端镜像：本机已有则直接使用；否则必须已有 scope 确认，未确认不拉取。
    present = False
    if image:
        present = await _image_present_locally(image)
        if not present and not settings.docker_image_consented:
            reason = render("workspace.docker-consent", locale, image=image)
            record_docker_failure(member, reason)
            raise DockerWorkspaceUnavailable(reason)

    timeout = DOCKER_BUILD_TIMEOUT_SECONDS if image is None or not present else DOCKER_INIT_TIMEOUT_SECONDS
    options: dict[str, Any] = {
        "workspace_id": workspace_id,
        "host_workdir": root,
        "env": docker_env(),
    }
    if image:
        options["base_image"] = image

    workspace = ProjectDockerWorkspace(**options)
    # 出网开关与全局 bind 都必须在 initialize() 之前赋值：容器就是在那一步创建的（#187 / #235）。
    workspace.docker_egress = settings.docker_egress
    workspace.global_binds = global_binds
    try:
        await asyncio.wait_for(workspace.initialize(), timeout=timeout)
    except Exception as error:
        with suppress(Exception):
            await asyncio.wait_for(workspace.close(), timeout=10)
        with suppress(Exception):
            await _delete_container(container_name)
        reason = _docker_reason(error, locale, timeout=timeout, image=image)
        record_docker_failure(member, reason)
        raise DockerWorkspaceUnavailable(reason) from error

    entry = _DockerEntry(
        key=key,
        workspace=workspace,
        settings=settings,
        root=root,
        member_id=member,
        session_id=session,
        workspace_id=workspace_id,
        container_name=container_name,
        last_used=time.monotonic(),
        active=1,
        image=image,
        egress=settings.docker_egress,
        global_binds=global_binds,
    )
    with _pool_lock:
        _pool[key] = entry
    clear_docker_failure(member)
    try:
        return _new_mount(entry, reused=False)
    except Exception as error:
        with _pool_lock:
            if _pool.get(key) is entry:
                _pool.pop(key, None)
        with suppress(Exception):
            await workspace.close()
        with suppress(Exception):
            await _delete_container(container_name)
        reason = _docker_reason(error, locale, timeout=timeout, image=image)
        record_docker_failure(member, reason)
        raise DockerWorkspaceUnavailable(reason) from error


async def release_workspace_mount(mount: Any) -> None:
    """一轮结束：本地立即 close；Docker 只归还池（容器留到空闲 TTL 或进程退出）。

    关闭 aiodocker client 必须在**创建它的 loop**里做，所以 release 就在这一轮的 loop 内完成；
    容器本身不属于任何 loop，下一轮由 :func:`_rebind_entry` 换句柄。
    """
    if not isinstance(mount, DockerWorkspaceMount):
        await mount.workspace.close()
        return

    workspace = mount.workspace
    # gateway 是 aiohttp 客户端（UnixConnector 指向容器内网关）；它属于当前 loop，
    # 必须在 loop 关闭前显式 aclose，否则 pytest 会把未关闭的 socket/transport 报成
    # unraisable。容器本身仍留在池里，下一轮由 _rebind_entry 换句柄。
    gateway = getattr(workspace, "_gateway", None)
    if gateway is not None:
        with suppress(Exception):
            await gateway.aclose()
    workspace._gateway = None
    workspace._mcp_instances.clear()
    workspace._mcp_last_used.clear()
    with suppress(Exception):
        if workspace._client is not None:
            await workspace._client.close()
    workspace._client = None
    workspace._container = None
    workspace._backend = None
    workspace.is_alive = False

    with _pool_lock:
        entry = _pool.get(mount.entry.key)
        if entry is not None and entry is mount.entry:
            entry.active = max(0, entry.active - 1)
            entry.last_used = time.monotonic()


async def sweep_idle_docker_workspaces(*, now: float | None = None) -> list[str]:
    """回收空闲超过 TTL 的容器（镜像保留）；返回被删除的容器名。"""
    moment = time.monotonic() if now is None else now
    with _pool_lock:
        expired = [
            entry
            for entry in _pool.values()
            if entry.active == 0 and moment - entry.last_used >= DOCKER_IDLE_TTL_SECONDS
        ]
        for entry in expired:
            if _pool.get(entry.key) is entry:
                _pool.pop(entry.key, None)
    for entry in expired:
        await _delete_container(entry.container_name)
    return [entry.container_name for entry in expired]


async def sweep_stale_docker_workspaces() -> list[str]:
    """启动清扫：删除本仓前缀、当前池里不再登记的遗留容器（``os._exit`` / 宿主崩溃）。"""
    import aiodocker

    with _pool_lock:
        active_names = {entry.container_name for entry in _pool.values()}

    client = aiodocker.Docker()
    removed: list[str] = []
    try:
        containers = await client.containers.list(all=True, filters={"label": ["agentscope.workspace=true"]})
        for container in containers:
            # aiodocker 的 list() 返回 DockerContainer；原始 JSON 在 ``_container``。
            info = getattr(container, "_container", {}) or {}
            labels = info.get("Labels") or {}
            workspace_id = str(labels.get("agentscope.workspace.id") or "")
            if not workspace_id.startswith(DOCKER_WORKSPACE_PREFIX):
                continue
            names = info.get("Names") or []
            name = str(names[0]).lstrip("/") if names else f"as_ws_{workspace_id}"
            if name in active_names:
                continue
            try:
                await container.delete(force=True)
                removed.append(name)
            except Exception:
                continue
    except Exception:
        return []
    finally:
        with suppress(Exception):
            await client.close()
    return removed


async def shutdown_docker_workspaces() -> list[str]:
    """删掉本进程登记的全部容器（进程退出/优雅关停的 best-effort 清理）。"""
    with _pool_lock:
        entries = list(_pool.values())
        _pool.clear()
    for entry in entries:
        await _delete_container(entry.container_name)
    return [entry.container_name for entry in entries]


async def reset_docker_workspace(
    settings: WorkspaceSettings,
    *,
    member_id: str,
    session_id: str,
) -> bool:
    """``reset``：删容器可写层（容器本身），保留 ``host_workdir`` 与项目文件。

    AgentScope 2.0.10 的 ``reset()`` 只删 ``data/skills/sessions/.mcp`` 且容器继续运行；规格
    §3.6 要的是“删容器可写层”，因此这里删除容器并清掉池条目，下一轮重新创建。
    """
    member = safe_partition_id(member_id)
    session = safe_partition_id(session_id)
    key = (member, session)
    with _pool_lock:
        entry = _pool.pop(key, None)
    clear_docker_failure(member)
    if entry is None:
        return False
    await _delete_container(entry.container_name)
    return True


def _reaper_loop() -> None:
    while True:
        time.sleep(30.0)
        try:
            asyncio.run(sweep_idle_docker_workspaces())
        except Exception:
            continue


def start_docker_reaper() -> None:
    """启动空闲回收线程（幂等；Docker 不可用时线程只是空转）。"""
    global _reaper_started
    with _reaper_lock:
        if _reaper_started:
            return
        _reaper_started = True
    thread = threading.Thread(target=_reaper_loop, name="alibot-docker-reaper", daemon=True)
    thread.start()


def _shutdown_docker_at_exit() -> None:
    """atexit 兜底：SIGKILL 无法覆盖，但正常退出/未捕获异常会走到这里。"""
    try:
        asyncio.run(shutdown_docker_workspaces())
    except Exception:
        return


atexit.register(_shutdown_docker_at_exit)


async def assemble_workspace(
    settings: WorkspaceSettings,
    *,
    member_id: str,
    session_id: str,
    locale: Locale = SOURCE_LOCALE,
) -> Any:
    """按后端分派装配；**不静默回落**（local 与 docker 各自失败即失败）。"""
    if settings.is_docker:
        return await assemble_docker_workspace(
            settings,
            member_id=member_id,
            session_id=session_id,
            locale=locale,
        )
    return await assemble_local_workspace(
        settings,
        member_id=member_id,
        session_id=session_id,
    )
