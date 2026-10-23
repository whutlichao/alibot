"""Agent 运行时默认值；Agent 的显式配置优先，空值跟随环境与内置默认。"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CONTEXT_WINDOW_MESSAGES = 40
_log = logging.getLogger(__name__)


@dataclass(eq=False)
class RuntimeTurn:
    """只在一轮的 loop 存活；HTTP 线程通过 call_soon_threadsafe 请求取消。"""

    loop: Any
    task: Any
    interrupted: bool = False


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
    """框架 Offloader 协议；会话状态落在**显式**的会话目录（ADR-0032 §四）。

    有项目根：``<项目根>/.alibot/sessions/<session_id>/offload/``；无项目根（DM / 未配项目根的
    频道）：``<数据目录>/sessions/<session_id>/offload/``。Docker 后端的容器内路径由
    ``visible_directory`` 映射（``/workspace/.alibot/sessions/...``），宿主同一分区。
    """

    def __init__(
        self,
        root: str | Path,
        session_id: str,
        *,
        docker: bool = False,
        in_project: bool = True,
    ) -> None:
        from .workspace import safe_partition_id

        self.root = Path(root).resolve()
        self.session_id = safe_partition_id(session_id)
        self.in_project = in_project
        self.relative = (
            Path(".alibot/sessions") / self.session_id / "offload"
            if in_project
            else Path("sessions") / self.session_id / "offload"
        )
        self.docker = docker

    @property
    def directory(self) -> Path:
        return self.root / self.relative

    @property
    def visible_directory(self) -> Path:
        return (Path("/workspace") if self.docker else self.root) / self.relative

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


class SessionState:
    """可恢复的框架运行状态；消息窗口始终从协作域正本重建。

    落点与 :class:`SessionOffloader` 同源（ADR-0032 §四）：有项目根
    ``<项目根>/.alibot/sessions/<session_id>/state.json``，无项目根
    ``<数据目录>/sessions/<session_id>/state.json``。
    """

    def __init__(self, root: Path, session_id: str, *, in_project: bool = True) -> None:
        from .workspace import safe_partition_id

        self.root = root
        self.in_project = in_project
        base = Path(".alibot/sessions") if in_project else Path("sessions")
        self.directory = root / base / safe_partition_id(session_id)

    def load(self, permission_context: Any) -> Any:
        from agentscope.state import AgentState, ReplyContext

        from .workspace import ensure_state_partition

        try:
            ensure_state_partition(str(self.root), str(self.directory))
            path = self.directory / "state.json"
            if path.is_symlink():
                raise ValueError("state.json must not be a symlink")
            state = AgentState.model_validate_json(path.read_text()) if path.exists() else AgentState()
            # 每轮权限按当前配置重建；不能从旧快照恢复曾被撤销的权限或挂起审批。
            state.permission_context = permission_context
            state.reply_context = ReplyContext()
            # 历史已由编排的窗口输入承载，避免把同一批消息在 state 与 inputs 中拼两遍。
            # 压缩摘要、工具缓存、Plan 与 middleware 状态仍从完整快照恢复。
            state.context = []
            return state
        except Exception:
            _log.warning("Agent state load failed; using an empty state: %s", self.directory, exc_info=True)
            return AgentState(permission_context=permission_context)

    def save(self, state: Any) -> None:
        from .workspace import ensure_state_partition

        temporary = self.directory / f"{uuid.uuid4().hex}.tmp"
        try:
            ensure_state_partition(str(self.root), str(self.directory))
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(state.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / "state.json")
        except Exception:
            _log.warning("Agent state save failed: %s", self.directory, exc_info=True)
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
