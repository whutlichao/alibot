"""#149 权限三档与规则匹配的行为取证。

测试落在**权限模块的公开面**（`build_permission_engine` / `effective_rules`）上，用 AgentScope
真实的 `PermissionEngine` 与真实的文件工具做判定。这样断言的是「这个档位下这次工具调用会
allow / ask / deny」这一条可观察行为，而不是重新实现一遍判定顺序。

不暴露 `BYPASS` / `DONT_ASK` 是产品边界：它们只能作为框架内部的枚举存在，不能出现在本仓的
档位表或映射里。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agentscope.permission import PermissionBehavior, PermissionMode
from alibot.permissions import (
    DEFAULT_PERMISSION_MODE,
    PERMISSION_MODES,
    build_permission_engine,
    effective_rules,
    map_permission_mode,
)
from alibot.workspace import WorkspaceSettings, assemble_local_workspace


async def _decide(
    mode: str,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    root: Path,
    rules: list[dict[str, str]] | None = None,
) -> PermissionBehavior:
    engine = build_permission_engine(mode, rules or [], root=str(root))
    mount = await assemble_local_workspace(
        WorkspaceSettings(root=str(root), permission_mode=mode),
        member_id="mem_permissions",
        session_id="ch_permissions--mem_permissions",
    )
    try:
        tool = next(item for item in mount.tools if item.name == tool_name)
        decision = await engine.check_permission(tool, tool_input)
        return decision.behavior
    finally:
        await mount.workspace.close()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "inside.txt").write_text("inside\n", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    return project


def test_the_three_public_modes_are_the_only_product_surface() -> None:
    assert set(PERMISSION_MODES) == {"read-only", "ask-writes", "trusted-writes"}
    assert DEFAULT_PERMISSION_MODE == "ask-writes"

    with pytest.raises(ValueError):
        map_permission_mode("bypass")
    with pytest.raises(ValueError):
        map_permission_mode("dont_ask")


def test_the_modes_map_to_the_documented_engine_modes() -> None:
    assert map_permission_mode("read-only") is PermissionMode.EXPLORE
    assert map_permission_mode("ask-writes") is PermissionMode.DEFAULT
    assert map_permission_mode("trusted-writes") is PermissionMode.ACCEPT_EDITS


def test_read_only_allows_reads_and_denies_writes(root: Path) -> None:
    assert (
        asyncio.run(_decide("read-only", "Read", {"file_path": str(root / "inside.txt")}, root=root))
        is PermissionBehavior.ALLOW
    )
    assert (
        asyncio.run(_decide("read-only", "Glob", {"pattern": "*.txt"}, root=root)) is PermissionBehavior.ALLOW
    )
    assert (
        asyncio.run(
            _decide("read-only", "Write", {"file_path": str(root / "new.txt"), "content": "x"}, root=root)
        )
        is PermissionBehavior.DENY
    )


def test_ask_writes_allows_reads_and_asks_for_writes(root: Path) -> None:
    assert (
        asyncio.run(_decide("ask-writes", "Read", {"file_path": str(root / "inside.txt")}, root=root))
        is PermissionBehavior.ALLOW
    )
    assert (
        asyncio.run(
            _decide("ask-writes", "Write", {"file_path": str(root / "new.txt"), "content": "x"}, root=root)
        )
        is PermissionBehavior.ASK
    )


def test_trusted_writes_auto_allows_inside_the_root_and_still_asks_outside(root: Path) -> None:
    assert (
        asyncio.run(
            _decide(
                "trusted-writes",
                "Write",
                {"file_path": str(root / "new.txt"), "content": "x"},
                root=root,
            )
        )
        is PermissionBehavior.ALLOW
    )
    outside = root.parent / "outside" / "escape.txt"
    assert (
        asyncio.run(
            _decide(
                "trusted-writes",
                "Write",
                {"file_path": str(outside), "content": "x"},
                root=root,
            )
        )
        is PermissionBehavior.ASK
    )


def test_user_rules_match_by_tool_and_pattern(root: Path) -> None:
    allow_rule = {"tool_name": "Write", "pattern": "src/**", "behavior": "allow"}
    assert (
        asyncio.run(
            _decide(
                "ask-writes",
                "Write",
                {"file_path": str(root / "src" / "main.py"), "content": "x"},
                root=root,
                rules=[allow_rule],
            )
        )
        is PermissionBehavior.ALLOW
    )
    assert (
        asyncio.run(
            _decide(
                "ask-writes",
                "Write",
                {"file_path": str(root / "other.py"), "content": "x"},
                root=root,
                rules=[allow_rule],
            )
        )
        is PermissionBehavior.ASK
    )


def test_path_tool_names_and_keys_derive_from_the_workspace_tool_map() -> None:
    """六个工具的路径口径只有一份正本：``workspace.PATH_ARGUMENTS``（Bash 不在其中）。"""
    from alibot.permissions import PATH_KEYS, PATH_TOOL_NAMES
    from alibot.workspace import PATH_ARGUMENTS

    assert frozenset(PATH_ARGUMENTS) == PATH_TOOL_NAMES
    assert set(PATH_KEYS) == set(PATH_ARGUMENTS.values())
    assert len(PATH_ARGUMENTS) == 5, "五个文件工具：Read / Write / Edit / Glob / Grep"
    assert "Bash" not in PATH_TOOL_NAMES, "Bash 不看路径参数（D15：本地 Bash 逐次审批）"


def test_deny_and_ask_rules_win_over_the_mode_default(root: Path) -> None:
    assert (
        asyncio.run(
            _decide(
                "trusted-writes",
                "Write",
                {"file_path": str(root / "new.txt"), "content": "x"},
                root=root,
                rules=[{"tool_name": "Write", "pattern": "", "behavior": "deny"}],
            )
        )
        is PermissionBehavior.DENY
    )
    assert (
        asyncio.run(
            _decide(
                "trusted-writes",
                "Read",
                {"file_path": str(root / "inside.txt")},
                root=root,
                rules=[{"tool_name": "Read", "pattern": "", "behavior": "ask"}],
            )
        )
        is PermissionBehavior.ASK
    )


def test_agent_rules_override_channel_rules_for_the_same_key() -> None:
    channel = [
        {"tool_name": "Write", "pattern": "src/**", "behavior": "allow"},
        {"tool_name": "Read", "pattern": "**/*.env", "behavior": "deny"},
    ]
    agent = [{"tool_name": "Write", "pattern": "src/**", "behavior": "deny"}]

    merged = effective_rules(channel, agent)

    assert [(rule["tool_name"], rule["pattern"], rule["behavior"]) for rule in merged] == [
        ("Write", "src/**", "deny"),
        ("Read", "**/*.env", "deny"),
    ]
