"""权限三档与用户规则的唯一映射层（规格 §3.6 / ADR-0021 §四）。

产品只暴露三档：``read-only`` / ``ask-writes`` / ``trusted-writes``。它们映射到 AgentScope 的
``EXPLORE`` / ``DEFAULT`` / ``ACCEPT_EDITS``；``BYPASS``（完全跳过安全审批）与 ``DONT_ASK``
（把 ASK 变成 DENY 的无人值守档）**不是**本仓的产品能力，也不接受来自配置/API 的取值。

规则的正本在 ``permission_rule`` 表：``(tool_name, pattern)`` 相同的行为键由 Agent 级规则覆盖
频道级规则；不同的键并存，交给框架的判定顺序（deny → ask → 只读 → 工具自身 → allow → 模式默认）。
本模块只做映射与合并，不读库、不写库——调用点注入已经解析好的规则。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypeAlias

from agentscope.middleware import MiddlewareBase
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)

PermissionModeName: TypeAlias = Literal["read-only", "ask-writes", "trusted-writes"]

#: 产品面允许的三档（顺序即文档顺序）；默认档与 ``channel.permission_mode`` 的 DDL 默认值一致。
PERMISSION_MODES: tuple[str, ...] = ("read-only", "ask-writes", "trusted-writes")
DEFAULT_PERMISSION_MODE = "ask-writes"

#: 用户规则允许的三种行为；**不**接受 ``bypass`` / ``dont_ask``。
RULE_BEHAVIORS: tuple[str, ...] = ("allow", "deny", "ask")

_MODE_TO_ENGINE: dict[str, PermissionMode] = {
    "read-only": PermissionMode.EXPLORE,
    "ask-writes": PermissionMode.DEFAULT,
    "trusted-writes": PermissionMode.ACCEPT_EDITS,
}


def map_permission_mode(mode: str) -> PermissionMode:
    """产品档位 → 框架档位；未知 / ``bypass`` / ``dont_ask`` 一律拒绝。"""
    try:
        return _MODE_TO_ENGINE[str(mode)]
    except KeyError:
        raise ValueError(f"unsupported permission mode: {mode!r}") from None


def normalize_rule(raw: Mapping[str, Any]) -> dict[str, str]:
    """把一行（或请求体里的）规则收成 ``{tool_name, pattern, behavior}``。

    行为不是三档之一时抛 ``ValueError``；调用点据此给出 400，而不是把未知行为静默当成 allow。
    """
    tool_name = str(raw.get("tool_name") or "").strip()
    pattern = str(raw.get("pattern") or "")
    behavior = str(raw.get("behavior") or "").strip()
    if not tool_name:
        raise ValueError("permission rule tool_name is required")
    if behavior not in RULE_BEHAVIORS:
        raise ValueError(f"unsupported permission behavior: {behavior!r}")
    return {"tool_name": tool_name, "pattern": pattern, "behavior": behavior}


def effective_rules(
    channel_rules: Iterable[Mapping[str, Any]],
    agent_rules: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """频道级规则 + Agent 级规则：同 ``(tool_name, pattern)`` 由 Agent 级覆盖。

    返回顺序稳定（先频道键序，再 Agent 新增键序），便于读数与测试；判定顺序仍由框架的
    ``PermissionEngine`` 决定（deny → ask → read-only → tool → allow）。
    """
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for raw in channel_rules:
        rule = normalize_rule(raw)
        merged[(rule["tool_name"], rule["pattern"])] = rule
    for raw in agent_rules:
        rule = normalize_rule(raw)
        merged[(rule["tool_name"], rule["pattern"])] = rule
    return list(merged.values())


_PATH_TOOL_NAMES = frozenset({"Read", "Write", "Edit", "Glob", "Grep"})


def _matched_pattern(rule: dict[str, str], root: str | None) -> str | None:
    """规则 pattern → 框架的 ``rule_content``。

    文件工具的 pattern 是相对项目根的 glob；框架的 ``match_rule`` 拿的是工具输入（可能是绝对
    路径），因此相对 pattern 先锚到根再匹配。绝对 pattern、空 pattern 与执行类工具（Bash）原样保留。
    """
    pattern = rule["pattern"]
    if not pattern:
        return None
    if root and rule["tool_name"] in _PATH_TOOL_NAMES and not os.path.isabs(pattern):
        return os.path.join(root, pattern)
    return pattern


def build_permission_context(
    mode: str,
    rules: Iterable[Mapping[str, Any]],
    *,
    root: str | None = None,
) -> PermissionContext:
    """构造一轮的权限上下文。

    ``root`` 是频道的项目根：它作为 ``ACCEPT_EDITS`` 的 working directory，令
    ``trusted-writes`` 只自动放行根内写入；根外路径不在白名单里，仍走 ASK（随后装配层的
    realpath 根检查还会在执行前拒绝越界写）。
    """
    engine_mode = map_permission_mode(mode)
    working_directories: dict[str, AdditionalWorkingDirectory] = {}
    if root:
        working_directories[root] = AdditionalWorkingDirectory(path=root, source="alibot-workspace")
    context = PermissionContext(mode=engine_mode, working_directories=working_directories)
    engine = PermissionEngine(context)
    for raw in rules:
        rule = normalize_rule(raw)
        engine.add_rule(
            PermissionRule(
                tool_name=rule["tool_name"],
                rule_content=_matched_pattern(rule, root),
                behavior=PermissionBehavior(rule["behavior"]),
                source="alibot-permission-rule",
            )
        )
    return context


def build_permission_engine(
    mode: str,
    rules: Iterable[Mapping[str, Any]],
    *,
    root: str | None = None,
) -> PermissionEngine:
    """与 :func:`build_permission_context` 同源，返回可查询的引擎（测试与装配共用）。"""
    return PermissionEngine(build_permission_context(mode, rules, root=root))


#: 文件工具的路径参数（与 ``workspace.PATH_ARGUMENTS`` 同口径；这里只用于权限判定的归一化）。
_PATH_KEYS = ("file_path", "path")


class WorkspacePathPermissionMiddleware(MiddlewareBase):
    """把相对路径按项目根归一化后再做权限判定。

    ``Write`` / ``Edit`` 的 ``check_permissions`` 用 ``os.path.realpath`` 判断“是否落在
    working directory 内”；模型常给相对路径，而进程 cwd 不一定是项目根。若不归一化，
    ``trusted-writes`` 会对根内相对路径误判成 ASK。这里只改写**权限判定看到的副本**
    （框架保证不改真实调用输入）；真正的执行前根检查仍由 ``workspace.WorkspaceRootGuard``
    负责，越界写依旧 ERROR。
    """

    def __init__(self, root: str):
        self.root = root

    async def on_check_permission(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ) -> Any:
        tool_input = dict(input_kwargs.get("tool_input") or {})
        for key in _PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value and not os.path.isabs(value):
                tool_input[key] = os.path.normpath(os.path.join(self.root, value))
        return await next_handler(tool_input=tool_input)
