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


# ─── #186：fetch 的 host 归一化（ADR-0026 §三） ───────────────────────────────
#
# ``fetch`` 的输入是**完整 URL**，而人类配置的 pattern 是 **host**（「允许抓取这个 host」）。
# 框架的 ``match_rule`` 默认只认工具名级规则（``rule_content is None``），因此两个落点必须成对：
# ``permissions._matched_pattern`` 把规则侧收成 host，``web`` 的 fetch 工具把输入侧收成 host。
# host 提取只有一份实现（``web.url_host``），#187 的容器出网白名单复用同一份。下面这组用例是
# 本票点名「最容易漏」的那条判据的取证面。


def _web_decide(
    mode: str,
    tool: Any,
    tool_input: dict[str, Any],
    rules: list[dict[str, str]] | None = None,
) -> PermissionBehavior:
    """用**真实的**权限引擎判一次联网工具调用（联网工具不看项目根，因此不挂 workspace）。"""
    engine = build_permission_engine(mode, rules or [])
    return asyncio.run(engine.check_permission(tool, tool_input)).behavior


def _fetch_rule(pattern: str, behavior: str = "allow") -> list[dict[str, str]]:
    return [{"tool_name": "fetch", "pattern": pattern, "behavior": behavior}]


def test_a_fetch_host_rule_matches_the_full_url_input() -> None:
    """``pattern='api.example.com'`` 必须匹配该 host 的完整 URL：大小写 / 端口 / 路径 / query 全归一。"""
    from alibot.web import fetch_tool

    tool = fetch_tool()
    rules = _fetch_rule("api.example.com")

    for url in (
        "https://api.example.com/x",
        "https://api.example.com",
        "HTTPS://API.Example.COM:8443/a/b?token=1#frag",
        "http://api.example.com:80/query?a=1",
        "api.example.com/relative-form",
    ):
        assert _web_decide("ask-writes", tool, {"url": url}, rules) is PermissionBehavior.ALLOW, url

    # 其他 host 仍 ASK：规则是 host 级的，不是「fetch 的全部调用」。
    for url in ("https://other.example.net/", "https://example.com/", "https://api.example.com.evil.test/"):
        assert _web_decide("ask-writes", tool, {"url": url}, rules) is PermissionBehavior.ASK, url


def test_a_fetch_host_rule_written_as_a_url_normalizes_the_same_way() -> None:
    """规则侧也归一：写成完整 URL 的 pattern 与写成裸 host 的 pattern 等价。"""
    from alibot.web import fetch_tool

    tool = fetch_tool()
    rules = _fetch_rule("HTTPS://API.Example.com:8443/docs?x=1")

    assert (
        _web_decide("ask-writes", tool, {"url": "https://api.example.com/a"}, rules)
        is PermissionBehavior.ALLOW
    )
    assert (
        _web_decide("ask-writes", tool, {"url": "https://api.example.com.evil.test/a"}, rules)
        is PermissionBehavior.ASK
    )


def test_fetch_asks_in_both_write_modes_and_is_denied_in_read_only() -> None:
    """``ask-writes`` / ``trusted-writes`` 都逐次 ASK；``read-only`` 没有「询问」这一档 ⇒ DENY。"""
    from alibot.web import fetch_tool

    tool = fetch_tool()
    url = {"url": "https://api.example.com/x"}

    assert _web_decide("ask-writes", tool, url) is PermissionBehavior.ASK
    assert _web_decide("trusted-writes", tool, url) is PermissionBehavior.ASK
    assert _web_decide("read-only", tool, url) is PermissionBehavior.DENY


def test_fetch_has_no_bypass_immune_so_the_host_rule_can_allow_it() -> None:
    """ADR-0026 §三：``fetch`` **不设** ``bypass_immune``（与本地 shell 刻意相反）。

    判据形状是「allow 规则能不能把它变成自动放行」：``fetch`` 能（人类可以一次配好常用域名），
    ``AskEveryTimeBash`` 不能。
    """
    from alibot.web import fetch_tool
    from alibot.workspace import local_shell_tool

    url = {"url": "https://api.example.com/x"}

    assert (
        _web_decide("ask-writes", fetch_tool(), url, _fetch_rule("api.example.com"))
        is PermissionBehavior.ALLOW
    )
    assert (
        _web_decide("ask-writes", local_shell_tool("/tmp"), {"command": "ls"}, _fetch_rule(""))
        is PermissionBehavior.ASK
    )


def test_deny_and_ask_rules_also_match_a_fetch_host() -> None:
    """三种行为都在 host 上判定：deny 优先于 allow，ask 把该 host 强制回到审批。"""
    from alibot.web import fetch_tool

    tool = fetch_tool()
    url = {"url": "https://api.example.com/x"}

    assert _web_decide("trusted-writes", tool, url, _fetch_rule("api.example.com", "deny")) is (
        PermissionBehavior.DENY
    )
    assert (
        _web_decide(
            "ask-writes",
            tool,
            url,
            _fetch_rule("api.example.com", "allow") + _fetch_rule("api.example.com", "ask"),
        )
        is PermissionBehavior.ASK
    )


def test_web_search_is_a_read_only_tool_in_every_mode() -> None:
    """``web_search`` 按只读处理（ADR-0026 §一）：三档都放行，不需要任何规则。"""
    from alibot.web import search_tool

    tool = search_tool(provider="brave", environ={"BRAVE_API_KEY": "key"})
    query = {"query": "agent framework", "count": 3}

    for mode in PERMISSION_MODES:
        assert _web_decide(mode, tool, query) is PermissionBehavior.ALLOW, mode


def test_a_pattern_that_yields_no_host_does_not_match_everything() -> None:
    """取不出 host 的 pattern（通配也不是本票的能力）只匹配不到，绝不退化成「全部调用」。"""
    from alibot.web import fetch_tool

    assert (
        _web_decide(
            "ask-writes", fetch_tool(), {"url": "https://api.example.com/x"}, _fetch_rule("*.example.com")
        )
        is PermissionBehavior.ASK
    )
