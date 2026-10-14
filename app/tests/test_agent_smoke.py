"""真模型连通性冒烟测试：**默认跳过**、本地手动运行、可能产生费用。

为什么单独一个文件：票面要求「真模型的连通性另有一条默认跳过、本地手动运行的冒烟测试；
测试不需要联网、零成本」。所以这里：

- ``pytest`` 默认收集它但**全部 skip**：判定依据是环境变量，而不是「碰巧能连上」。
  于是「跑全量测试」这条命令永远不联网、永远零成本。
- 手动运行时显式给变量，并且**先**确认远端数据流告知（避免它在无人确认的情况下调远端）。

手动运行方式（以本机 Ollama 为例，零凭证、零费用）::

    ALIBOT_SMOKE_MODEL=ollama:qwen2.5 \
    ALIBOT_SMOKE_ALLOW_REMOTE=0 \
    .venv/bin/python -m pytest tests/test_agent_smoke.py -q -s

远端 provider（**会产生费用**，且需要该 provider 的 API key）::

    ALIBOT_SMOKE_MODEL=dashscope:qwen-max \
    ALIBOT_SMOKE_ALLOW_REMOTE=1 \
    DASHSCOPE_API_KEY=... \
    .venv/bin/python -m pytest tests/test_agent_smoke.py -q -s

本用例只断言「连通性」这一件事：一次真调用要么产出一条完整回复，要么给出可读的失败原因。
它不重复功能测试的任何断言，也不参与任何门禁。
"""

from __future__ import annotations

import os

import pytest
from alibot.agent_seam import (
    AgentScopeAgentSeam,
    is_remote_provider,
    provider_of,
)

SMOKE_MODEL_ENV = "ALIBOT_SMOKE_MODEL"
ALLOW_REMOTE_ENV = "ALIBOT_SMOKE_ALLOW_REMOTE"

SMOKE_MODEL = os.environ.get(SMOKE_MODEL_ENV, "")
ALLOW_REMOTE = os.environ.get(ALLOW_REMOTE_ENV) == "1"

pytestmark = pytest.mark.skipif(
    not SMOKE_MODEL,
    reason=(
        f"真模型冒烟测试默认跳过：设 {SMOKE_MODEL_ENV}=provider:model 才运行"
        f"（远端 provider 还需 {ALLOW_REMOTE_ENV}=1）"
    ),
)


def test_real_model_round_trip() -> None:
    """真调一次模型：确认 2.x 适配层能把「待读消息」变成一条完整回复。"""
    provider = provider_of(SMOKE_MODEL)
    if is_remote_provider(provider) and not ALLOW_REMOTE:
        pytest.skip(
            f"{SMOKE_MODEL} 是远端 provider，会离开本机并可能产生费用；"
            f"确认数据面告知后设 {ALLOW_REMOTE_ENV}=1 再运行"
        )

    seam = AgentScopeAgentSeam(timeout_seconds=60.0)
    agent = {
        "id": "mem_smoke",
        "display_name": "冒烟测试员",
        "role": "只回一句",
        "model_id": SMOKE_MODEL,
        "system_prompt": "你只回一句极短的确认。",
        "enabled": True,
    }
    messages = [
        {
            "seq": 1,
            "kind": "human",
            "body": "请只回一句：连上了。",
            "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
        }
    ]

    outcome = seam.run_round(agent, messages, mentioned=True, target_id="ch_smoke")

    print(f"\n[smoke] model={SMOKE_MODEL} status={outcome.status} reason={outcome.reason}")
    for message in outcome.messages:
        print(f"[smoke] body={message.body!r}")

    # 连通性判定：要么真的产出了一条完整正文，要么失败原因可读（例如缺凭证、网络不可达）。
    if outcome.status == "replied":
        assert len(outcome.messages) == 1, "一轮至多一条消息（框架侧的一轮↔一 Msg 契约）"
        assert outcome.messages[0].body.strip()
    else:
        assert outcome.status in {"failed", "ignored"}
        assert outcome.reason, "失败必须给出可读原因"
