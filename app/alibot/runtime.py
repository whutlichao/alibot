"""Agent 运行时默认值；Agent 的显式配置优先，空值跟随环境与内置默认。"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CONTEXT_WINDOW_MESSAGES = 40


def context_window_messages(agent: Mapping[str, Any]) -> int:
    raw = agent.get("context_window_messages")
    if raw is None:
        raw = os.environ.get("ALIBOT_CONTEXT_WINDOW_MESSAGES", CONTEXT_WINDOW_MESSAGES)
    try:
        return max(1, int(str(raw)))
    except (ValueError, TypeError):
        return CONTEXT_WINDOW_MESSAGES


def runtime_configs(agent: Mapping[str, Any], environ: Mapping[str, str] | None) -> dict[str, Any]:
    """仅在真实适配器装配时加载框架；非法环境值降级为默认。"""
    from agentscope.agent import ContextConfig, InjectionConfig, ModelConfig, ReActConfig

    source = os.environ if environ is None else environ

    def number(key: str, default: float, low: float, high: float) -> float:
        raw = agent.get(key)
        if raw is None:
            raw = source.get(f"ALIBOT_{key.upper()}", default)
        try:
            value = float(str(raw))
            return value if low <= value <= high else default
        except (ValueError, TypeError):
            return default

    trigger = number("context_trigger_ratio", 0.7, 0.01, 0.9)
    reserve = number("context_reserve_ratio", 0.2, 0.001, 0.89)
    return {
        "context_config": ContextConfig(
            trigger_ratio=trigger,
            reserve_ratio=min(reserve, trigger / 2),
            context_buffer_ratio=min(0.2, trigger / 2),
            tool_result_limit=int(number("context_tool_result_limit", 4000, 1, 1000000)),
            max_image_num=int(number("context_max_image_num", 5, 0, 10000)),
        ),
        "react_config": ReActConfig(max_iters=int(number("react_max_iters", 30, 1, 10000))),
        "model_config": ModelConfig(max_retries=int(number("model_max_retries", 2, 0, 100))),
        "injection_config": InjectionConfig(timezone="Asia/Shanghai", time_format="%Y-%m-%dT%H:%M:%S%z"),
    }


def runtime_prompt(target_name: str, message_count: int) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    return f"\n\n当前时间：{now}；target：{target_name}；本轮上下文：{message_count} 条消息。"


class SessionOffloader:
    """框架 Offloader 协议；Docker bind mount 与主机使用同一个 .alibot 分区。"""

    def __init__(self, root: str | Path, session_id: str, *, docker: bool = False) -> None:
        from .workspace import safe_partition_id

        self.root = Path(root).resolve()
        self.relative = Path(".alibot/sessions") / safe_partition_id(session_id) / "offload"
        self.docker = docker

    def _write(self, kind: str, data: bytes, suffix: str = ".json") -> str:
        from .workspace import ensure_state_partition

        directory = self.root / self.relative
        ensure_state_partition(str(self.root), str(directory))
        path = directory / f"{kind}-{uuid.uuid4().hex}{suffix}"
        # 随机文件名 + 独占创建，不追随外部放进来的同名 symlink。
        with path.open("xb") as stream:
            stream.write(data)
        visible_root = Path("/workspace") if self.docker else self.root
        return str(visible_root / path.relative_to(self.root))

    async def offload_context(self, session_id: str, msgs: list[Any]) -> str:
        payload = [msg.model_dump(mode="json") for msg in msgs]
        return self._write("context", json.dumps(payload, ensure_ascii=False, indent=2).encode())

    async def offload_tool_result(self, session_id: str, tool_result: Any) -> str:
        return self._write("tool", tool_result.model_dump_json(indent=2).encode())

    async def offload_data_block(self, block: Any) -> Any:
        from agentscope.message import URLSource

        if block.source.type != "base64":
            return block
        suffix = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = self._write("data", base64.b64decode(block.source.data), suffix)
        source = URLSource.model_validate(
            {"url": f"workspace://{path}", "media_type": block.source.media_type}
        )
        return block.model_copy(update={"source": source})
