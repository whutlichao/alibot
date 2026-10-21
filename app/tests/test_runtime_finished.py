"""轮次结束原因经真实适配器与 HTTP 读路径保留，历史行仍为 NULL。"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock
from agentscope.model import ChatResponse
from alibot.api import create_app
from alibot.db import Database
from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, outcomes, send
from tests.scripted import ManualScheduler
from tests.test_approval_seam import _ScriptedModel, _StubModelSeam
from tests.test_schema_migration import _build_library, _migrate_argv, _run, _shape


@pytest.mark.parametrize(
    ("finished_reason", "copy"),
    [("exceed_max_iters", "达到迭代上限"), ("interrupted", "中断")],
)
def test_finished_reason_and_readable_diagnosis_survive_http(
    tmp_path: Path, monkeypatch, finished_reason: str, copy: str
) -> None:
    async def reply(self, *args: Any, **kwargs: Any) -> Any:
        yield Msg(
            name=self.name,
            content=[TextBlock(text="部分结论")],
            role="assistant",
            finished_reason=finished_reason,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(Agent, "reply_stream", reply)
    model = _ScriptedModel([[ChatResponse(content=[TextBlock(text="未使用")], is_last=True)]])
    scheduler = ManualScheduler()
    app = create_app(data_dir=tmp_path, seam=_StubModelSeam(model), scheduler=scheduler)
    with TestClient(app) as client:
        agent = create_agent(client).json()["agent"]
        channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
        send(client, channel["id"], "请处理")
        scheduler.drain()
        row = outcomes(client, channel["id"])[0]
        assert row["finished_reason"] == finished_reason
        assert copy in row["reason"]
        assert "模型自主判断需要回应" not in row["reason"]
        assert row["status"] == "replied"


def test_schema_22_upgrades_to_current_shape_and_keeps_old_finished_reason_null(tmp_path: Path) -> None:
    data = tmp_path / "old"
    path = _build_library(data, 22)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, started_at, finished_at) "
            "VALUES ('old-round', 'mem_x', 'ch_dev', 2, 'replied', '2026-10-20', '2026-10-20')"
        )
    result = _run(*_migrate_argv(data))
    assert result.returncode == 0, result.stderr
    assert "22 → 23" in result.stdout
    fresh = Database(tmp_path / "fresh.sqlite3")
    try:
        assert _shape(path) == _shape(fresh.path)
    finally:
        fresh.close()
    upgraded = Database(data)
    try:
        assert upgraded.list_round_outcomes("ch_dev")[0]["finished_reason"] is None
    finally:
        upgraded.close()
