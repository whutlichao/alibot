"""真实运行时接缝：框架压缩、卸载与配置须进入真实 Agent，脚本模型不联网。"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.model import ChatResponse

from tests.test_approval_seam import _ScriptedModel, _StubModelSeam, _tool_call


class CompressionModel(_ScriptedModel):
    def __init__(self) -> None:
        super().__init__([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]])
        self.context_size = 1000
        self.summaries = 0

    async def count_tokens(self, messages: list[Any], tools: Any = None) -> int:
        return sum(100 for msg in messages if "archive" in str(msg.content))

    async def generate_structured_output(
        self, messages: list[Any], structured_model: Any, **kwargs: Any
    ) -> Any:
        self.summaries += 1
        return SimpleNamespace(
            finished_reason="completed",
            usage=None,
            content={
                "task_overview": "记住历史",
                "current_state": "已读",
                "important_discoveries": "无",
                "next_steps": "回应",
                "context_to_preserve": "用户偏好",
            },
        )


def test_env_threshold_triggers_real_compression_and_offloads_history(tmp_path: Path) -> None:
    model = CompressionModel()
    seam = _StubModelSeam(
        model,
        runtime_root=tmp_path,
        environ={"ALIBOT_CONTEXT_TRIGGER_RATIO": "0.5"},
    )
    outcome = seam.run_round(
        {"id": "member", "model_id": "openai:stub", "system_prompt": "简短回应"},
        [{"seq": n, "body": f"archive {n}", "author": {"id": "owner"}} for n in range(1, 7)],
        mentioned=False,
        target_id="target",
    )
    assert outcome.status == "replied", outcome.reason
    assert model.summaries == 1
    files = list((tmp_path / ".alibot/sessions/target--member/offload").glob("*.json"))
    assert files
    assert "archive 1" in files[0].read_text()


def test_runtime_configs_and_current_time_are_passed_to_agent(tmp_path: Path, monkeypatch) -> None:
    created: list[Any] = []
    original = Agent.__init__

    def capture(self, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        created.append((self, kwargs))

    monkeypatch.setattr(Agent, "__init__", capture)
    model = CompressionModel()
    seam = _StubModelSeam(model, runtime_root=tmp_path, environ={"ALIBOT_REACT_MAX_ITERS": "12"})
    outcome = seam.run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"body": "你好", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )
    assert outcome.status == "replied", outcome.reason
    instance, options = created[0]
    assert instance.context_config.trigger_ratio == 0.7
    assert instance.context_config.reserve_ratio == 0.2
    assert instance.context_config.tool_result_limit == 4000
    assert instance.context_config.max_image_num == 5
    assert instance.react_config.max_iters == 12
    assert instance.model_config.max_retries == 2
    assert instance.injection_config.timezone == "Asia/Shanghai"
    prompt = str(options["system_prompt"])
    assert datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat() in prompt
    assert "+08:00" in prompt
    assert "target" in prompt
    assert "上下文" in prompt
    assert "archive" not in json.dumps(instance.state.model_dump(mode="json"), ensure_ascii=False)


def test_truncated_tool_output_is_kept_in_the_session_partition(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("content " * 1000)
    model = _ScriptedModel(
        [
            _tool_call("read-large", "Read", {"file_path": "large.txt"}),
            [ChatResponse(content=[TextBlock(text="已读")], is_last=True)],
        ]
    )
    seam = _StubModelSeam(
        model,
        workspace_of=lambda *_: {"root": str(tmp_path), "backend": "local"},
        environ={"ALIBOT_CONTEXT_TOOL_RESULT_LIMIT": "20"},
    )
    outcome = seam.run_round(
        {"id": "member", "model_id": "openai:stub"},
        [{"body": "读 large.txt", "author": {"id": "owner"}}],
        mentioned=False,
        target_id="target",
    )
    assert outcome.status == "replied", outcome.reason
    files = list((tmp_path / ".alibot/sessions/target--member/offload").glob("tool-*.json"))
    assert files
    assert "content " * 100 in files[0].read_text()
