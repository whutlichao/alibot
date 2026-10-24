"""中断端点、事件与状态落盘：真实 Agent + 零联网模型。"""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.model import ChatResponse
from alibot.api import create_app
from alibot.events import sse_frame
from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, live_backend, messages, outcomes, send, sse_frames
from tests.scripted import ManualScheduler
from tests.test_approval_seam import _ScriptedModel, _StubModelSeam, _tool_call


class BlockingModel(_ScriptedModel):
    def __init__(self) -> None:
        super().__init__([[ChatResponse(content=[TextBlock(text="完成")], is_last=True)]])
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.client = SimpleNamespace(aclose=self.close)

    async def close(self) -> None:
        self.closed.set()

    async def _call_api(self, *args: Any, **kwargs: Any) -> Any:
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return await super()._call_api(*args, **kwargs)


@pytest.mark.parametrize("collection", ["channels", "dms", "tasks"])
def test_reconnected_sse_restores_the_current_interrupt_handle(tmp_path: Path, collection: str) -> None:
    model = BlockingModel()
    scheduler = ManualScheduler()
    with (
        live_backend(
            tmp_path, scheduler=scheduler, seam=_StubModelSeam(model, runtime_root=tmp_path)
        ) as base_url,
        httpx.Client(base_url=base_url, trust_env=False, timeout=10) as http,
    ):
        agent = http.post(
            "/api/agents",
            json={
                "name": "成员",
                "role": "助手",
                "model_id": "ollama:stub",
                "system_prompt": "请回应",
            },
        ).json()["agent"]
        if collection == "dms":
            target = http.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]
        else:
            target = http.post(
                "/api/channels",
                json={
                    "name": "频道",
                    "member_ids": [agent["id"]],
                },
            ).json()["channel"]
            if collection == "tasks":
                http.patch(
                    f"/api/channels/{target['id']}/config",
                    json={
                        "mode": "team",
                        "team_leader_member_id": agent["id"],
                    },
                )
                target = http.post(f"/api/channels/{target['id']}/tasks", json={"title": "任务"}).json()[
                    "task"
                ]
        if collection != "tasks":
            http.post(f"/api/{collection}/{target['id']}/messages", json={"body": "请处理"})
        thread = threading.Thread(target=scheduler.drain, daemon=True)
        thread.start()
        try:
            assert model.started.wait(3)
            replayed = sse_frames(http, target["id"], after_seq=1, target_collection=collection)
            snapshot = next(event for event in replayed if event["type"] == "round.active")
            assert list(snapshot["rounds"].values()) == [agent["id"]]
            reply_id = next(iter(snapshot["rounds"]))
            target_id = target.get("target_id", target["id"])
            assert http.post(f"/api/targets/{target_id}/rounds/{reply_id}/interrupt").status_code == 200
            thread.join(5)
            assert not thread.is_alive()
            replayed = sse_frames(http, target["id"], after_seq=1, target_collection=collection)
            assert next(event for event in replayed if event["type"] == "round.active")["rounds"] == {}
            if collection == "tasks":
                assert any(
                    event["type"] == "round_outcome" and event["outcome"]["finished_reason"] == "interrupted"
                    for event in replayed
                )
        finally:
            model.release.set()
            thread.join(5)


def test_interrupt_endpoint_only_stops_the_current_target_and_emits_an_sse_event(tmp_path: Path) -> None:
    model = BlockingModel()
    scheduler = ManualScheduler()
    app = create_app(
        data_dir=tmp_path, seam=_StubModelSeam(model, runtime_root=tmp_path), scheduler=scheduler
    )
    with TestClient(app) as client:
        agent = create_agent(client).json()["agent"]
        channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
        other = create_channel(client, name="其他频道", member_ids=[agent["id"]]).json()["channel"]
        with app.state.events.subscribe(channel["id"]) as events:
            send(client, channel["id"], "需要很久")
            thread = threading.Thread(target=scheduler.drain, daemon=True)
            thread.start()
            try:
                assert model.started.wait(3)
                event = events.get(timeout=3)
                while event.get("event_type") != "REPLY_START":
                    event = events.get(timeout=3)
                reply_id = event["reply_id"]
                assert (
                    client.post(f"/api/targets/{other['id']}/rounds/{reply_id}/interrupt").status_code == 409
                )
                url = f"/api/targets/{channel['id']}/rounds/{reply_id}/interrupt"
                response = client.post(url)
                assert response.status_code == 200, response.text
                assert client.post(url).status_code == 409
                thread.join(5)
                assert not thread.is_alive()
                assert model.closed.is_set()
                row = outcomes(client, channel["id"])[0]
                assert row["finished_reason"] == "interrupted"
                assert row["reply_seq"] is None
                assert len(messages(client, channel["id"])) == 1
                interrupted = events.get(timeout=3)
                while interrupted["type"] != "round.interrupted":
                    interrupted = events.get(timeout=3)
                assert "event: round.interrupted" in sse_frame(interrupted)
                assert "id:" not in sse_frame(interrupted)
                state = tmp_path / f"sessions/{channel['id']}--{agent['id']}/state.json"
                assert state.is_file()
            finally:
                model.release.set()
                thread.join(5)


def test_state_is_saved_atomically_and_loaded_next_round_with_current_permissions(
    tmp_path: Path, monkeypatch
) -> None:
    seen: list[Any] = []
    original = Agent.__init__

    def capture(self, *args: Any, **kwargs: Any) -> None:
        seen.append(kwargs["state"])
        original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", capture)
    model = _ScriptedModel([[ChatResponse(content=[TextBlock(text="完成")], is_last=True)]])
    seam = _StubModelSeam(model, runtime_root=tmp_path)
    agent = {"id": "member", "model_id": "openai:stub"}
    inputs = [{"seq": 1, "body": "你好", "author": {"id": "owner"}}]
    assert seam.run_round(agent, inputs, mentioned=False, target_id="target").status == "replied"
    path = tmp_path / "sessions" / "target--member" / "state.json"
    assert path.is_file()
    data = json.loads(path.read_text())
    data["middle_context"]["persisted"] = "上一轮的运行状态"
    path.write_text(json.dumps(data))
    assert seam.run_round(agent, inputs, mentioned=False, target_id="target").status == "replied"
    assert seen[1].middle_context["persisted"] == "上一轮的运行状态"
    assert not list(path.parent.glob("*.tmp"))
    path.write_text("broken state")
    assert seam.run_round(agent, inputs, mentioned=False, target_id="target").status == "replied"


def test_interrupting_a_parked_round_closes_approval_without_executing_the_tool(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    scheduler = ManualScheduler()
    model = _ScriptedModel([_tool_call("write", "Write", {"file_path": "blocked.txt", "content": "no"})])
    app = create_app(
        data_dir=tmp_path / "data",
        scheduler=scheduler,
        seam_factory=lambda approvals: _StubModelSeam(
            model,
            approvals=approvals,
            runtime_root=tmp_path,
            workspace_of=lambda *_: {"root": str(root), "backend": "local", "permission_mode": "ask-writes"},
        ),
    )
    with TestClient(app) as client:
        agent = create_agent(client).json()["agent"]
        channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
        with app.state.events.subscribe(channel["id"]) as events:
            send(client, channel["id"], "请写入")
            thread = threading.Thread(target=scheduler.drain, daemon=True)
            thread.start()
            event = events.get(timeout=3)
            while event["type"] != "approval_required":
                event = events.get(timeout=3)
            reply_id = event["reply_id"]
            response = client.post(f"/api/targets/{channel['id']}/rounds/{reply_id}/interrupt")
            thread.join(5)
            assert response.status_code == 200, response.text
            assert not thread.is_alive()
            assert outcomes(client, channel["id"])[0]["finished_reason"] == "interrupted"
            assert not (root / "blocked.txt").exists()
            assert client.get("/api/approvals", params={"state": "pending"}).json()["approvals"] == []
