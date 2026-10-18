"""#144 成员产出可见性：运行记录 + SSE，不占消息 / 未读 / 搜索。

取证面只有三个既有接缝：

- **HTTP 黑盒**：消息流、频道未读、搜索、`GET /api/targets/{id}/team-runs`；
- **SSE**：真实 HTTP 服务上的 ``agent_event`` / ``team_assignment`` 帧（live-only、无 ``id``）；
- **真 TeamPipeline + 进程内脚本模型**：成员确实运行过，才有「成员过程不落消息」可断言。

脚本化替身（``ScriptedAgentSeam``）只跑队长，因此「成员过程」的用例必须走真实
``BudgetedTeamPipeline``；配额与运行记录的装配点已在 #143 验证，这里只验证可见性契约。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import httpx
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.db import open_database
from alibot.team import member_internal_name
from fastapi.testclient import TestClient

from tests.conftest import (
    LOCAL_MODEL_ID,
    ManualScheduler,
    create_agent,
    create_channel,
    live_backend,
    messages,
    send,
)

MEMBER_RESULT = "成员过程结论-绝密-只该出现在运行记录"
LEADER_RESULT = "队长最终答复"


# ─── 真 pipeline 的进程内脚本模型（与 test_team_budget.py 同源） ─────────────
def _text(text: str) -> list[ChatResponse]:
    return [ChatResponse(content=[TextBlock(text=text)], is_last=True)]


def _tool_calls(calls: list[tuple[str, str, str]]) -> list[ChatResponse]:
    blocks = [
        ToolCallBlock(id=call_id, name="TeamAssign", input=json.dumps({"member": name, "prompt": prompt}))
        for call_id, name, prompt in calls
    ]
    return [
        ChatResponse(content=list(blocks), is_last=False),
        ChatResponse(content=list(blocks), is_last=True),
    ]


class _ScriptedModel(ChatModelBase):
    def __init__(self, script: list[list[ChatResponse]]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="scripted",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = list(script)
        self.calls = 0

    async def _call_api(self, *args: Any, **kwargs: Any):
        chunks = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        async def stream():
            for chunk in chunks:
                yield chunk

        return stream()


class _PipelineSeam(AgentScopeAgentSeam):
    """真实适配器 + 按构造顺序取用的脚本模型队列（队长在前、成员随后）。"""

    def __init__(self, models: list[ChatModelBase]) -> None:
        super().__init__(timeout_seconds=10.0)
        self._models = list(models)

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._models.pop(0)


def _seed_team(data_dir: Path, *, task: bool = False) -> dict[str, Any]:
    """直接建库播种（只用于给 live 服务准备身份；所有断言仍走 HTTP）。"""
    db = open_database(data_dir)
    try:
        leader = db.create_agent(
            name="队长", role="协调", model_id=LOCAL_MODEL_ID, system_prompt="你是队长。"
        )
        member = db.create_agent(
            name="成员", role="执行", model_id=LOCAL_MODEL_ID, system_prompt="你是成员。"
        )
        channel = db.create_channel(name="可见性", member_ids=[leader["id"], member["id"]])
        result: dict[str, Any] = {"leader": leader, "member": member, "channel": channel}
        if task:
            result["task"] = db.create_task(channel_id=channel["id"], title="整理结论")
        return result
    finally:
        db.close()


@contextmanager
def _live_client(
    data_dir: Path, *, seam: Any
) -> Iterator[tuple[httpx.Client, ManualScheduler, dict[str, Any]]]:
    scheduler = ManualScheduler()
    state: dict[str, Any] = {}
    with live_backend(data_dir, scheduler=scheduler, seam=seam, state=state) as base_url:
        client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, read=None), trust_env=False)
        try:
            yield client, scheduler, state
        finally:
            client.close()


def _make_team(client: httpx.Client, channel_id: str, leader_id: str) -> None:
    response = client.patch(
        f"/api/channels/{channel_id}/config",
        json={"mode": "team", "team_leader_member_id": leader_id},
    )
    assert response.status_code == 200, response.json()


def _start_reader(
    client: httpx.Client, path: str
) -> tuple[list[dict[str, Any]], threading.Event, threading.Event, threading.Thread]:
    frames: list[dict[str, Any]] = []
    ready = threading.Event()
    stop = threading.Event()

    def reader() -> None:
        current: dict[str, Any] = {"id": None, "event": None}
        with client.stream("GET", path) as response:
            assert response.status_code == 200, response.read()
            ready.set()
            for line in response.iter_lines():
                if stop.is_set():
                    return
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("id: "):
                    current["id"] = text[len("id: ") :]
                elif text.startswith("event: "):
                    current["event"] = text[len("event: ") :]
                elif text.startswith("data: "):
                    current["data"] = json.loads(text[len("data: ") :])
                    frames.append(current)
                    current = {"id": None, "event": None}

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return frames, ready, stop, thread


def _wait_frames(frames: list[dict[str, Any]], predicate, *, label: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(list(frames)):
            return
        time.sleep(0.01)
    raise AssertionError(f"等不到 {label}；已收到的帧：{frames}")


def _stop_reader(stop: threading.Event, thread: threading.Thread) -> None:
    stop.set()
    thread.join(timeout=5)


def _sse(frames: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [frame["data"] for frame in frames if frame["data"].get("type") == event_type]


# ─── AC1 + AC2：频道（成员过程不落消息 / 未读 / 搜索；SSE 归属） ──────────────
def test_channel_member_output_is_visible_only_as_events_and_run_records(data_dir: Path) -> None:
    seeded = _seed_team(data_dir)
    leader, member, channel = seeded["leader"], seeded["member"], seeded["channel"]
    seam = _PipelineSeam(
        [
            _ScriptedModel(
                [
                    _tool_calls([("call-1", member_internal_name(member["id"]), "写出你的结论")]),
                    _text(LEADER_RESULT),
                ]
            ),
            _ScriptedModel([_text(MEMBER_RESULT)]),
        ]
    )

    with _live_client(data_dir, seam=seam) as (client, scheduler, _state):
        _make_team(client, channel["id"], leader["id"])
        frames, ready, stop, reader = _start_reader(client, f"/api/channels/{channel['id']}/events")
        try:
            assert ready.wait(5), "事件流未建立"
            assert (
                client.post(f"/api/channels/{channel['id']}/messages", json={"body": "开工"}).status_code
                == 201
            )
            scheduler.drain()
            _wait_frames(
                frames,
                lambda items: (
                    any(
                        frame["data"].get("type") == "team_assignment"
                        and frame["data"].get("state") == "succeeded"
                        for frame in items
                    )
                    and any(
                        frame["data"].get("type") == "message" and frame["data"]["message"]["kind"] == "agent"
                        for frame in items
                    )
                    and any(frame["data"].get("type") == "round_outcome" for frame in items)
                ),
                label="分派收口、队长最终消息与轮次结果",
            )
        finally:
            _stop_reader(stop, reader)

        # AC2：频道消息只有队长最终 1 条；未读与搜索都没有成员过程。
        agent_messages = [
            message
            for message in client.get(f"/api/channels/{channel['id']}/messages").json()["messages"]
            if message["kind"] == "agent"
        ]
        assert [message["body"] for message in agent_messages] == [LEADER_RESULT]
        assert client.get("/api/channels").json()["channels"][0]["unread_count"] == 1
        hits = client.get("/api/search", params={"q": "成员过程结论", "facet": "message"}).json()["hits"]
        assert hits == []

        # AC1：成员事件带 member_id / reply_id / target_id；分派 SSE 字段齐全且无 id。
        events = _sse(frames, "agent_event")
        member_events = [event for event in events if event["member_id"] == member["id"]]
        assert member_events, events
        assert any(event["event_type"] == "REPLY_START" for event in member_events)
        assert any(MEMBER_RESULT in json.dumps(event["payload"], ensure_ascii=False) for event in events)
        for event in member_events:
            assert event["reply_id"]
            assert event["target_id"] == channel["id"]
        for frame in frames:
            if frame["data"].get("type") in {"agent_event", "team_assignment"}:
                assert frame["id"] is None, "live-only 事件不得推进重连游标"

        assignments = _sse(frames, "team_assignment")
        assert [event["state"] for event in assignments] == ["started", "succeeded"]
        for event in assignments:
            assert event["team_run_id"]
            assert event["assignment_id"]
            assert event["member_id"] == member["id"]
            assert event["target_id"] == channel["id"]
            assert event["task_id"] is None
        assert assignments[-1]["reply_id"], "收口帧应带上成员回复 id"

        # AC3：运行记录字段与快照端点。
        payload = client.get(f"/api/targets/{channel['id']}/team-runs").json()
        assert payload["has_more"] is False
        assert [run["status"] for run in payload["runs"]] == ["completed"]
        [assignment] = payload["runs"][0]["assignments"]
        assert assignment["prompt"] == "写出你的结论"
        assert assignment["state"] == "succeeded"
        assert assignment["reply_excerpt"] == MEMBER_RESULT
        assert assignment["started_at"] and assignment["finished_at"]
        assert assignment["task_id"] is None
        assert assignment["member_id"] == member["id"]

        # 轮次结果只记队长，不记成员。
        outcomes = client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        assert [outcome["member_id"] for outcome in outcomes] == [leader["id"]]


# ─── AC1 + AC2 + AC3：task 线程（task_id / target_id 归属、按 task 过滤） ────
def test_task_thread_member_output_is_visible_only_as_events_and_run_records(data_dir: Path) -> None:
    seeded = _seed_team(data_dir, task=True)
    leader, member, channel = seeded["leader"], seeded["member"], seeded["channel"]
    task = seeded["task"]
    seam = _PipelineSeam(
        [
            _ScriptedModel(
                [
                    _tool_calls([("call-task", member_internal_name(member["id"]), "任务结论")]),
                    _text("队长任务答复"),
                ]
            ),
            _ScriptedModel([_text(MEMBER_RESULT)]),
        ]
    )

    with _live_client(data_dir, seam=seam) as (client, scheduler, _state):
        _make_team(client, channel["id"], leader["id"])
        frames, ready, stop, reader = _start_reader(client, f"/api/tasks/{task['id']}/events")
        try:
            assert ready.wait(5), "事件流未建立"
            assert (
                client.post(f"/api/tasks/{task['id']}/messages", json={"body": "请整理"}).status_code == 201
            )
            scheduler.drain()
            _wait_frames(
                frames,
                lambda items: (
                    any(
                        frame["data"].get("type") == "team_assignment"
                        and frame["data"].get("state") == "succeeded"
                        for frame in items
                    )
                    and any(
                        frame["data"].get("type") == "message" and frame["data"]["message"]["kind"] == "agent"
                        for frame in items
                    )
                    and any(frame["data"].get("type") == "round_outcome" for frame in items)
                ),
                label="任务线程分派收口、队长最终消息与轮次结果",
            )
        finally:
            _stop_reader(stop, reader)

        task_messages = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
        assert [message["body"] for message in task_messages if message["kind"] == "agent"] == [
            "队长任务答复"
        ]
        task_view = client.get(f"/api/tasks/{task['id']}").json()["task"]
        assert task_view["unread_count"] == 1
        hits = client.get("/api/search", params={"q": "成员过程结论", "facet": "message"}).json()["hits"]
        assert hits == []

        events = _sse(frames, "agent_event")
        member_events = [event for event in events if event["member_id"] == member["id"]]
        assert member_events
        assert any(event["event_type"] == "REPLY_START" for event in member_events)
        assert any(MEMBER_RESULT in json.dumps(event["payload"], ensure_ascii=False) for event in events)
        for event in member_events:
            assert event["target_id"] == task["target_id"]
            assert event["reply_id"]

        assignments = _sse(frames, "team_assignment")
        assert [event["state"] for event in assignments] == ["started", "succeeded"]
        for event in assignments:
            assert event["target_id"] == task["target_id"]
            assert event["task_id"] == task["id"]

        outcomes = _sse(frames, "round_outcome")
        assert [frame["outcome"]["member_id"] for frame in outcomes] == [leader["id"]]
        assert [frame["outcome"]["status"] for frame in outcomes] == ["replied"]

        filtered = client.get(
            f"/api/targets/{task['target_id']}/team-runs", params={"task_id": task["id"]}
        ).json()
        assert [run["status"] for run in filtered["runs"]] == ["completed"]
        assert filtered["runs"][0]["assignments"][0]["task_id"] == task["id"]

        other = client.get(
            f"/api/targets/{task['target_id']}/team-runs", params={"task_id": "task_other"}
        ).json()
        assert other["runs"] == []


# ─── AC3：分页按时间倒序 ──────────────────────────────────────────────────
def test_team_runs_are_paginated_newest_first(client: TestClient) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = create_channel(client, member_ids=[leader["id"], member["id"]]).json()["channel"]
    assert (
        client.patch(
            f"/api/channels/{channel['id']}/config",
            json={"mode": "team", "team_leader_member_id": leader["id"]},
        ).status_code
        == 200
    )
    scheduler = cast(Any, client.app).state.orchestrator._scheduler
    for index in range(1, 4):
        assert send(client, channel["id"], f"第 {index} 轮").status_code == 201
        scheduler.drain()

    first = client.get(f"/api/targets/{channel['id']}/team-runs", params={"limit": 2, "offset": 0}).json()
    second = client.get(f"/api/targets/{channel['id']}/team-runs", params={"limit": 2, "offset": 2}).json()
    all_runs = client.get(f"/api/targets/{channel['id']}/team-runs", params={"limit": 10}).json()

    assert first["limit"] == 2 and first["offset"] == 0
    assert first["has_more"] is True
    assert len(first["runs"]) == 2
    assert second["has_more"] is False
    assert len(all_runs["runs"]) == 3
    assert [run["id"] for run in first["runs"]] == [run["id"] for run in all_runs["runs"][:2]]
    assert [run["id"] for run in second["runs"]] == [run["id"] for run in all_runs["runs"][2:]]
    assert first["runs"][0]["started_at"] >= first["runs"][1]["started_at"] >= second["runs"][0]["started_at"]


# ─── AC4：穿透仍落频道消息，不产生 team_assignment、不再唤醒；team task 不穿透 ─
def test_pierced_reply_lands_as_a_channel_message_without_an_assignment(client: TestClient) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    outsider = create_agent(client, name="外援").json()["agent"]
    channel = create_channel(client, member_ids=[leader["id"], member["id"]]).json()["channel"]
    assert (
        client.patch(
            f"/api/channels/{channel['id']}/config",
            json={"mode": "team", "team_leader_member_id": leader["id"]},
        ).status_code
        == 200
    )
    seam = cast(Any, client.app).state.orchestrator._seam
    seam.replies[leader["id"]] = LEADER_RESULT
    seam.replies[outsider["id"]] = "外援一次性答复"
    scheduler = cast(Any, client.app).state.orchestrator._scheduler
    db = cast(Any, client.app).state.db

    assert send(client, channel["id"], f"@{outsider['display_name']} 帮忙").status_code == 201
    scheduler.drain()

    agent_bodies = [
        message["body"] for message in messages(client, channel["id"]) if message["kind"] == "agent"
    ]
    assert sorted(agent_bodies) == sorted([LEADER_RESULT, "外援一次性答复"])
    assert seam.calls_for(member["id"]) == []
    assert db.list_team_assignments(channel["id"]) == []
    assert scheduler.pending_count() == 0, "穿透回复不得再唤醒任何人"
    # 穿透回复是真正的频道消息，因此可被搜索；成员过程不是消息，搜不到。
    assert client.get("/api/search", params={"q": "外援一次性答复"}).json()["hits"]


def test_team_task_does_not_pierce(client: TestClient) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    outsider = create_agent(client, name="外援").json()["agent"]
    channel = create_channel(client, member_ids=[leader["id"], member["id"]]).json()["channel"]
    assert (
        client.patch(
            f"/api/channels/{channel['id']}/config",
            json={"mode": "team", "team_leader_member_id": leader["id"]},
        ).status_code
        == 200
    )
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "整理"}).json()["task"]
    seam = cast(Any, client.app).state.orchestrator._seam
    seam.replies[leader["id"]] = LEADER_RESULT
    seam.replies[outsider["id"]] = "不应该出现的答复"
    scheduler = cast(Any, client.app).state.orchestrator._scheduler

    assert (
        client.post(
            f"/api/tasks/{task['id']}/messages",
            json={"body": f"@{outsider['display_name']} 帮忙"},
        ).status_code
        == 201
    )
    scheduler.drain(limit=1)

    assert seam.calls_for(outsider["id"]) == []
    task_messages = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    assert [message["body"] for message in task_messages if message["kind"] == "agent"] == [LEADER_RESULT]
