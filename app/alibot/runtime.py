"""Agent 运行时默认值；Agent 的显式配置优先，空值跟随环境与内置默认。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

CONTEXT_WINDOW_MESSAGES = 40


def context_window_messages(agent: Mapping[str, Any]) -> int:
    raw = agent.get("context_window_messages")
    if raw is None:
        raw = os.environ.get("ALIBOT_CONTEXT_WINDOW_MESSAGES", CONTEXT_WINDOW_MESSAGES)
    try:
        return max(1, int(str(raw)))
    except (ValueError, TypeError):
        return CONTEXT_WINDOW_MESSAGES
