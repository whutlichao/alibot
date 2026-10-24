"""长期记忆 = ReMe 工作区：每个成员一个，跨项目 / 跨会话召回（ADR-0034）。

AgentScope 的 :class:`~agentscope.middleware.ReMeMiddleware` 把 ReMe **进程内**嵌进这一轮：
写入由 ``auto_memory`` 在轮次收尾时按 ``session_id`` 落成 ``daily`` 记忆卡片，检索默认走
BM25 关键词索引（本仓**不**注入 embedding，因此不新增任何凭据面）。

**作用域**（ADR-0034 §二）：ReMe 的检索覆盖整个 ``workspace_dir``、写回只按 ``session_id``
隔离，没有项目过滤。按 AgentScope workspace-manager 文档的 ``IsolationPolicy.PER_AGENT``
语义，一个成员一个工作区：同一 member 在所有 session / target / 项目之间共享记忆。
工作区路径是 ``<global_home>/agents/<member_id>/reme``——与 Agent 家目录同处全局层，
但 ``reme/`` 由应用自管，文件工具既不可见也不可写。

旧的 ``MEMORY.md`` / ``<topic>.md`` 不再是记忆落点：它们既不被注入、也不被写入，升级时
原样留在磁盘上（不自动导入，见 ADR-0034 §五）。

长期记忆**没有开关**（ADR-0035）：每一轮只经 :func:`assemble_memory` 装配，唯一会让它缺席的是
全局层未接线（``global_home`` 为空）或工作区不可建。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agentscope.middleware import ReMeMiddleware

from .workspace import WorkspaceConfigError, ensure_member_home, ensure_state_partition, safe_partition_id

__all__ = [
    "MemoryAssembly",
    "assemble_memory",
    "memory_workspace_dir",
    "memory_workspace_id",
]

_log = logging.getLogger(__name__)

#: 家目录下的 ReMe 工作区目录名：``<global_home>/agents/<member_id>/reme``。
REME_DIR = "reme"

#: 每次检索返回的记忆条数（也是 ``memory_search`` 工具的默认 ``limit``）。
REME_TOP_K = 5


def memory_workspace_id(member_id: str) -> str:
    """记忆工作区 id：**一个成员一个**工作区（PER_AGENT），与 session / target / 项目无关。

    纯函数——相同的 member id 永远得到相同的 id，因此同一成员的所有轮次落在同一个
    ``workspace_dir``，跨项目、跨会话召回（ADR-0034 §二）。
    """
    return safe_partition_id(member_id)


def memory_workspace_dir(global_home: str | Path, member_id: str) -> Path:
    """ReMe 工作区 ``<global_home>/agents/<member_id>/reme``，按需建目录。

    建目录走 :func:`alibot.workspace.ensure_state_partition`：根外的 symlink 祖先直接抛
    :class:`~alibot.workspace.WorkspaceConfigError`（不在全局根之外产生写入）。调用方把它
    当作「这一轮没有记忆」降级，不改变本轮结论。
    """
    root = os.path.realpath(str(global_home))
    home = Path(ensure_member_home(root, memory_workspace_id(member_id)))
    workspace = home / REME_DIR
    ensure_state_partition(root, str(workspace))
    return workspace


@dataclass
class MemoryAssembly:
    """一轮的记忆装配：中间件 + 它提供的工具 + 可读的装配读数。

    ``close`` 必须由**这一轮的 finally** 调用（在关闭 model client 之前）：ReMe 的嵌入应用
    带后台索引循环，异常只记日志、不改变本轮结论。
    """

    middleware: Any
    tools: list[Any]
    workspace_dir: Path
    mode: Literal["both", "static_control"]

    async def close(self) -> None:
        """关闭嵌入式 ReMe 应用；失败只记一条可读日志。"""
        try:
            await self.middleware.close()
        except Exception as error:  # 收尾失败不能改写本轮结论
            _log.warning("ReMe memory teardown failed: %s", error)


async def assemble_memory(
    member_id: str,
    *,
    model: Any,
    provider: str,
    global_home: str | Path | None,
    supports_tools: bool,
) -> MemoryAssembly | None:
    """每轮的长期记忆装配（ADR-0035：没有开关）；未接线全局层时返回 ``None``。

    - ``global_home`` 为空（测试 / 未接线）⇒ 不装中间件、不建目录，只记一条可读日志；
    - ``supports_tools`` 为假（provider 不能携带工具）⇒ ``mode="static_control"``：
      检索结果在回复前注入，``memory_search`` 不送出去；
    - 工作区不可建（根外 symlink 祖先 / 无权限）⇒ 这一轮记忆缺席，返回 ``None``。

    ``model`` 是**本轮**模型：ReMe 的抽取与检索固定用它（``chat_model``），不随 Agent 变化。
    """
    if not global_home:
        _log.info(
            "long-term memory is absent this round: no global home is wired (provider=%s, member=%s)",
            provider,
            member_id,
        )
        return None
    try:
        workspace = memory_workspace_dir(global_home, member_id)
    except (WorkspaceConfigError, OSError) as error:
        _log.warning("ReMe workspace is unusable; memory is absent this round: %s", error)
        return None
    mode: Literal["both", "static_control"] = "both" if supports_tools else "static_control"
    middleware = ReMeMiddleware(
        workspace_dir=str(workspace),
        parameters=ReMeMiddleware.Parameters(
            chat_model=model,
            mode=mode,
            top_k=REME_TOP_K,
        ),
    )
    tools = list(await middleware.list_tools())
    return MemoryAssembly(middleware=middleware, tools=tools, workspace_dir=workspace, mode=mode)
