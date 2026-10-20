"""#147 SOP：owner 定义/启动、两步 executor→verifier、max_attempts、awaiting 恢复、重启恢复与隔离面。

取证明面按仓库既有分层：

- **真实 ``SOPEngine`` / ``SOPStep`` + 进程内脚本 Agent**：两步流程、verifier 拒绝一次后重试、
  ``max_attempts`` 由真实引擎裁决（stub 只提供 ``reply_stream``，不碰状态机）；
- **HTTP 黑盒**：定义 CRUD、owner 启动、run 快照、``awaiting`` 恢复；
- **事件总线**：#141 的 ``sop_event`` 公共发布入口被实际调用（帧不带 id、按 target 归属）；
- **库边界**：保留策略只经 ``app.state.db`` 这个既有测试接缝触发。

SOP 不是任务：整个文件都不写消息、不改任务行，断言也落在这些隔离面上。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from agentscope.event import RequireUserConfirmEvent, UserConfirmResultEvent
from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.types import ReplyFinishedReason
from alibot.sop import SOP_RUN_RETENTION
from fastapi.testclient import TestClient

from tests.conftest import build_app, create_agent, create_channel
from tests.scripted import ManualScheduler, ScriptedAgentSeam

_OWNER_ID = "owner"


# ─── 进程内脚本 Agent（只实现 SOPStep 需要的 reply_stream 契约） ──────────────


def _completed(structured: dict[str, Any], text: str = "done") -> Msg:
    return Msg(
        name="sop-agent",
        role="assistant",
        content=[TextBlock(text=text)],
        structured_output=structured,
        finished_reason=ReplyFinishedReason.COMPLETED,
    )


def _schema_name(schema: Any) -> str:
    return str(getattr(schema, "__name__", ""))


class _HandoverExecutor:
    """每次尝试都交出一份 handover；把收到的 inputs 原文记下来供断言。"""

    def __init__(self, name: str = "executor") -> None:
        self.name = name
        self.calls: list[Any] = []

    async def reply_stream(
        self,
        inputs: Any = None,
        structured_schema: Any = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[Any, None]:
        self.calls.append(inputs)
        assert _schema_name(structured_schema) == "_Handover"
        yield _completed({"handover": f"draft v{len(self.calls)}"}, text="draft")


class _ScriptedVerifier:
    """按预设序列给出 verdict；序列耗尽后沿用最后一档。"""

    def __init__(self, verdicts: Sequence[bool], message: str = "缺少验收命令") -> None:
        self.name = "verifier"
        self.verdicts = list(verdicts)
        self.message = message
        self.calls: list[Any] = []

    async def reply_stream(
        self,
        inputs: Any = None,
        structured_schema: Any = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[Any, None]:
        self.calls.append(inputs)
        assert _schema_name(structured_schema) == "_Verdict"
        passed = self.verdicts[min(len(self.calls) - 1, len(self.verdicts) - 1)]
        yield _completed(
            {"passed": passed, "message": "" if passed else self.message},
            text="verdict",
        )


class _ParkingExecutor:
    """Executor 在第一次回复中要求人类确认；收到 ``UserConfirmResultEvent`` 后才交出 handover。"""

    def __init__(self) -> None:
        self.name = "parking-executor"
        self.calls: list[Any] = []

    async def reply_stream(
        self,
        inputs: Any = None,
        structured_schema: Any = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[Any, None]:
        self.calls.append(inputs)
        if not isinstance(inputs, UserConfirmResultEvent):
            yield RequireUserConfirmEvent(
                reply_id="sop-park",
                tool_calls=[ToolCallBlock(id="call-1", name="write_file", input='{"path":"a.md"}')],
            )
            return
        yield _completed({"handover": "confirmed draft"}, text="draft")


class _ParkingVerifier:
    """Verifier 在第一次判定前要求人类确认；恢复后才给出 pass。"""

    def __init__(self) -> None:
        self.name = "parking-verifier"
        self.calls: list[Any] = []

    async def reply_stream(
        self,
        inputs: Any = None,
        structured_schema: Any = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[Any, None]:
        self.calls.append(inputs)
        if not isinstance(inputs, UserConfirmResultEvent):
            yield RequireUserConfirmEvent(
                reply_id="sop-verify-park",
                tool_calls=[ToolCallBlock(id="call-2", name="run_tests", input="{}")],
            )
            return
        yield _completed({"passed": True, "message": ""}, text="verdict")


# 「成员 id + 角色」→ 进程内脚本 Agent；生产里由 AgentScopeAgentSeam.sop_agent 装配。
class _SopSeam(ScriptedAgentSeam):
    def __init__(self, agents: dict[tuple[str, str], Any]) -> None:
        super().__init__()
        self.sop_agents = dict(agents)
        self.sop_roles: list[tuple[str, str]] = []

    def sop_agent(self, member: dict[str, Any], *, role: str = "executor") -> Any:
        self.sop_roles.append((str(member["id"]), role))
        return self.sop_agents[(str(member["id"]), role)]


# ─── HTTP 夹具 ────────────────────────────────────────────────────────────────


def _define_sop(
    client: TestClient,
    *,
    executor_id: str,
    verifier_id: str,
    max_attempts: int = 3,
    name: str = "发布 SOP",
    subject: str = "起草发布说明",
    description: str = "写清楚范围与验收命令",
):
    return client.post(
        "/api/sops",
        json={
            "name": name,
            "description": "两步：起草 + 校验",
            "steps": [
                {
                    "subject": subject,
                    "description": description,
                    "executor_member_id": executor_id,
                    "verifier_member_id": verifier_id,
                    "max_attempts": max_attempts,
                }
            ],
        },
    )


def _start(client: TestClient, sop_id: str, target_id: str):
    return client.post(f"/api/sops/{sop_id}/runs", json={"target_id": target_id})


def _run(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/sop-runs/{run_id}")
    assert response.status_code == 200, response.json()
    return response.json()["run"]


def _two_agents(client: TestClient) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    executor = create_agent(client, name="执行者").json()["agent"]
    verifier = create_agent(client, name="校验者").json()["agent"]
    # SOP 用例要观察「任务行不被 SOP 触碰」：默认开启的自动认领会在建任务时先推进状态，
    # 因此这里显式关闭两名成员的开关（认领不是本文件的被测量）。
    for agent in (executor, verifier):
        assert client.patch(f"/api/agents/{agent['id']}", json={"auto_claim": False}).status_code == 200
    channel = create_channel(client, member_ids=[executor["id"], verifier["id"]]).json()["channel"]
    return executor, verifier, channel


# ─── 判据 1：owner 创建/启动；两步流程 + 拒绝一次后重试 + max_attempts ────────


def test_owner_runs_two_step_sop_verifier_retries_then_completes(data_dir) -> None:
    executor = _HandoverExecutor()
    verifier = _ScriptedVerifier([False, True])
    scheduler = ManualScheduler()
    seam = _SopSeam({})
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        ex, vr, channel = _two_agents(client)
        seam.sop_agents[(ex["id"], "executor")] = executor
        seam.sop_agents[(vr["id"], "verifier")] = verifier

        assert client.get("/api/sops").json() == {"sops": []}
        created = _define_sop(client, executor_id=ex["id"], verifier_id=vr["id"])
        assert created.status_code == 201, created.json()
        definition = created.json()["sop"]
        assert definition["enabled"] is True
        assert definition["steps"][0]["max_attempts"] == 3
        assert [item["id"] for item in client.get("/api/sops").json()["sops"]] == [definition["id"]]
        assert client.get(f"/api/sops/{definition['id']}").json()["sop"]["name"] == "发布 SOP"

        events = cast(Any, client.app).state.events
        frames: list[dict[str, Any]] = []
        with events.subscribe(channel["id"]) as queue:
            started = _start(client, definition["id"], channel["id"])
            assert started.status_code == 201, started.json()
            run = started.json()["run"]
            assert run["phase"] == "pending"
            # owner 是启动者：leader 不由请求指定，也不接受 Agent 身份。
            assert run["leader_member_id"] == _OWNER_ID
            assert run["target_id"] == channel["id"]
            scheduler.drain()
            while not queue.empty():
                frames.append(queue.get_nowait())

        final = _run(client, run["id"])
        assert final["phase"] == "completed"
        step = final["state"]["steps"][0]
        assert step["phase"] == "completed"
        assert [verdict["passed"] for verdict in step["verifications"]] == [False, True]
        assert step["verifications"][0]["message"] == "缺少验收命令"
        assert final["finished_at"] is not None

        # 真实引擎把上一次拒绝原因原样交给 executor 的第二次尝试。
        assert len(executor.calls) == 2
        assert len(verifier.calls) == 2
        assert "缺少验收命令" in json.dumps(executor.calls[1], default=str, ensure_ascii=False)
        assert seam.sop_roles == [(ex["id"], "executor"), (vr["id"], "verifier")]

        # sop_event：步骤开始/结束/verdict；live-only，不带 seq / id。
        sop_frames = [frame for frame in frames if frame.get("type") == "sop_event"]
        assert sop_frames, [frame.get("type") for frame in frames]
        assert all(frame["sop_run_id"] == run["id"] for frame in sop_frames)
        assert all(frame["step_index"] == 0 for frame in sop_frames)
        assert all("payload" in frame for frame in sop_frames)
        assert {"running", "completed"} <= {frame["phase"] for frame in sop_frames}
        verdicts = [
            frame["payload"].get("verdict") for frame in sop_frames if frame["payload"].get("verdict")
        ]
        assert [verdict["passed"] for verdict in verdicts] == [False, True]
        assert all("seq" not in frame and "id" not in frame for frame in sop_frames)


def test_max_attempts_fails_the_run(data_dir) -> None:
    executor = _HandoverExecutor()
    verifier = _ScriptedVerifier([False])  # 永远拒绝
    scheduler = ManualScheduler()
    seam = _SopSeam({})
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        ex, vr, channel = _two_agents(client)
        seam.sop_agents[(ex["id"], "executor")] = executor
        seam.sop_agents[(vr["id"], "verifier")] = verifier
        definition = _define_sop(client, executor_id=ex["id"], verifier_id=vr["id"], max_attempts=2).json()[
            "sop"
        ]
        run = _start(client, definition["id"], channel["id"]).json()["run"]
        scheduler.drain()

        final = _run(client, run["id"])
        assert final["phase"] == "failed"
        step = final["state"]["steps"][0]
        assert step["phase"] == "failed"
        assert len(step["verifications"]) == 2
        assert len(executor.calls) == 2


def test_disabled_definition_cannot_start_and_patch_applies_to_later_runs(data_dir) -> None:
    executor = _HandoverExecutor()
    verifier = _ScriptedVerifier([True])
    scheduler = ManualScheduler()
    seam = _SopSeam({})
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        ex, vr, channel = _two_agents(client)
        seam.sop_agents[(ex["id"], "executor")] = executor
        seam.sop_agents[(vr["id"], "verifier")] = verifier
        definition = _define_sop(client, executor_id=ex["id"], verifier_id=vr["id"]).json()["sop"]

        disabled = client.patch(f"/api/sops/{definition['id']}", json={"enabled": False})
        assert disabled.status_code == 200, disabled.json()
        assert disabled.json()["sop"]["enabled"] is False
        rejected = _start(client, definition["id"], channel["id"])
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "validation_failed"

        client.patch(f"/api/sops/{definition['id']}", json={"enabled": True})
        first = _start(client, definition["id"], channel["id"]).json()["run"]
        scheduler.drain()
        assert _run(client, first["id"])["phase"] == "completed"

        # 编辑只影响后续 run：老 run 的状态不变，新 run 的步骤描述进入下一次尝试的输入。
        client.patch(
            f"/api/sops/{definition['id']}",
            json={
                "steps": [
                    {
                        "subject": "起草第二版",
                        "description": "新的验收口径",
                        "executor_member_id": ex["id"],
                        "verifier_member_id": vr["id"],
                        "max_attempts": 3,
                    }
                ]
            },
        )
        assert _run(client, first["id"])["state"]["steps"][0]["phase"] == "completed"
        second = _start(client, definition["id"], channel["id"]).json()["run"]
        scheduler.drain()
        assert _run(client, second["id"])["phase"] == "completed"
        assert "新的验收口径" in json.dumps(executor.calls[-1], default=str, ensure_ascii=False)


# ─── 判据 2：awaiting 可恢复；重启后 SOPRunState 可恢复 ───────────────────────


def test_awaiting_run_resumes_via_owner_confirmation(data_dir) -> None:
    executor = _ParkingExecutor()
    verifier = _ScriptedVerifier([True])
    scheduler = ManualScheduler()
    seam = _SopSeam({})
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        ex, vr, channel = _two_agents(client)
        seam.sop_agents[(ex["id"], "executor")] = executor
        seam.sop_agents[(vr["id"], "verifier")] = verifier
        definition = _define_sop(client, executor_id=ex["id"], verifier_id=vr["id"]).json()["sop"]
        run = _start(client, definition["id"], channel["id"]).json()["run"]
        scheduler.drain()

        parked = _run(client, run["id"])
        assert parked["phase"] == "awaiting"
        assert parked["state"]["steps"][0]["phase"] == "awaiting"
        assert parked["awaiting"]["reply_id"] == "sop-park"
        assert parked["awaiting"]["tool_calls"][0]["name"] == "write_file"

        resumed = client.post(f"/api/sop-runs/{run['id']}/resume", json={})
        assert resumed.status_code == 200, resumed.json()
        scheduler.drain()

        final = _run(client, run["id"])
        assert final["phase"] == "completed"
        assert final["state"]["steps"][0]["phase"] == "completed"
        assert final["awaiting"] is None
        assert len(executor.calls) == 2


def test_state_survives_restart_and_completed_step_is_not_rerun(data_dir) -> None:
    # 第一步完成；第二步 verifier 停在 awaiting。重启后从持久化的 SOPRunState 继续。
    step1_executor = _HandoverExecutor("executor-1")
    step1_verifier = _ScriptedVerifier([True])
    step2_executor = _HandoverExecutor("executor-2")
    step2_verifier = _ParkingVerifier()

    scheduler = ManualScheduler()
    seam = _SopSeam({})
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        ex1 = create_agent(client, name="执行者一").json()["agent"]
        vr1 = create_agent(client, name="校验者一").json()["agent"]
        ex2 = create_agent(client, name="执行者二").json()["agent"]
        vr2 = create_agent(client, name="校验者二").json()["agent"]
        channel = create_channel(client, member_ids=[ex1["id"], vr1["id"], ex2["id"], vr2["id"]]).json()[
            "channel"
        ]
        seam.sop_agents[(ex1["id"], "executor")] = step1_executor
        seam.sop_agents[(vr1["id"], "verifier")] = step1_verifier
        seam.sop_agents[(ex2["id"], "executor")] = step2_executor
        seam.sop_agents[(vr2["id"], "verifier")] = step2_verifier
        definition = client.post(
            "/api/sops",
            json={
                "name": "两段 SOP",
                "description": "",
                "steps": [
                    {
                        "subject": "第一步",
                        "description": "先完成",
                        "executor_member_id": ex1["id"],
                        "verifier_member_id": vr1["id"],
                        "max_attempts": 3,
                    },
                    {
                        "subject": "第二步",
                        "description": "等待人类确认",
                        "executor_member_id": ex2["id"],
                        "verifier_member_id": vr2["id"],
                        "max_attempts": 3,
                    },
                ],
            },
        ).json()["sop"]
        run = _start(client, definition["id"], channel["id"]).json()["run"]
        scheduler.drain()

        parked = _run(client, run["id"])
        assert parked["phase"] == "awaiting"
        assert [step["phase"] for step in parked["state"]["steps"]] == ["completed", "awaiting"]
        saved_state = parked["state"]

    # 全新进程：状态只可能来自库。恢复时第一步已完成，不会被重新执行。
    scheduler2 = ManualScheduler()
    step2_verifier2 = _ParkingVerifier()
    seam2 = _SopSeam(
        {
            (ex1["id"], "executor"): step1_executor,
            (vr1["id"], "verifier"): step1_verifier,
            (ex2["id"], "executor"): step2_executor,
            (vr2["id"], "verifier"): step2_verifier2,
        }
    )
    with TestClient(build_app(data_dir, scheduler=scheduler2, seam=cast(Any, seam2))) as restarted:
        reloaded = _run(restarted, run["id"])
        assert reloaded["phase"] == "awaiting"
        assert reloaded["state"] == saved_state
        # awaiting 详情是进程内事实（不落库）：重启后必须由 owner 显式给出 reply_id。
        assert reloaded["awaiting"] is None
        resumed = restarted.post(f"/api/sop-runs/{run['id']}/resume", json={"reply_id": "sop-verify-park"})
        assert resumed.status_code == 200, resumed.json()
        scheduler2.drain()

        final = _run(restarted, run["id"])
        assert final["phase"] == "completed"
        # 第一步没有被重跑：两位第一步参与者在重启进程里零调用。
        assert len(step1_executor.calls) == 1
        assert len(step1_verifier.calls) == 1
        assert len(step2_executor.calls) == 1
        assert len(step2_verifier2.calls) == 1


# ─── 判据 3：保留策略；完成/失败不删定义 ────────────────────────────────────


def test_retention_keeps_latest_runs_and_definitions(data_dir) -> None:
    scheduler = ManualScheduler()
    seam = _SopSeam({})
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        ex, vr, channel = _two_agents(client)
        definition = _define_sop(client, executor_id=ex["id"], verifier_id=vr["id"]).json()["sop"]
        database = cast(Any, client.app).state.db
        base = datetime.now(UTC)
        for index in range(SOP_RUN_RETENTION + 3):
            database.create_sop_run(
                sop_definition_id=definition["id"],
                target_id=channel["id"],
                leader_member_id=_OWNER_ID,
                state=json.dumps({"id": f"stored-{index}", "inputs": [], "steps": []}),
                phase="completed",
                started_at=(base + timedelta(minutes=index)).isoformat(),
            )
        with sqlite3.connect(database.path) as raw:
            count = raw.execute("SELECT COUNT(*) FROM sop_run").fetchone()[0]
            oldest = raw.execute("SELECT id FROM sop_run ORDER BY started_at LIMIT 1").fetchone()[0]
        assert count == SOP_RUN_RETENTION

        # 90 天边界：把最早的一条回拨到窗口外，启动时清理，定义仍在。
        with sqlite3.connect(database.path) as raw:
            raw.execute(
                "UPDATE sop_run SET started_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(days=100)).isoformat(), oldest),
            )
            raw.commit()
        assert database.prune_sop_runs() == 1
        with sqlite3.connect(database.path) as raw:
            assert raw.execute("SELECT COUNT(*) FROM sop_run").fetchone()[0] == SOP_RUN_RETENTION - 1
        assert client.get(f"/api/sops/{definition['id']}").status_code == 200


# ─── 判据 4：SOP 不改任务五态、不进看板/未读/搜索/预算，不产生任务行 ─────────


def test_sop_does_not_touch_tasks_or_other_human_faces(data_dir) -> None:
    executor = _HandoverExecutor()
    verifier = _ScriptedVerifier([True])
    scheduler = ManualScheduler()
    seam = _SopSeam({})
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        ex, vr, channel = _two_agents(client)
        seam.sop_agents[(ex["id"], "executor")] = executor
        seam.sop_agents[(vr["id"], "verifier")] = verifier
        task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "人类任务"}).json()["task"]
        definition = _define_sop(client, executor_id=ex["id"], verifier_id=vr["id"]).json()["sop"]

        database = cast(Any, client.app).state.db
        with sqlite3.connect(database.path) as raw:
            messages_before = raw.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        run = _start(client, definition["id"], task["target_id"]).json()["run"]
        scheduler.drain()
        assert _run(client, run["id"])["phase"] == "completed"

        assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "todo"
        assert client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]["used"] == 0
        assert client.get(f"/api/channels/{channel['id']}/budget").json()["budget"]["used"] == 0
        assert client.get("/api/search", params={"q": "起草发布说明"}).json()["hits"] == []
        assert client.get("/api/channels").json()["channels"][0]["unread_count"] == 0

        database = cast(Any, client.app).state.db
        with sqlite3.connect(database.path) as raw:
            assert raw.execute("SELECT COUNT(*) FROM task").fetchone()[0] == 1
            assert raw.execute("SELECT status FROM task").fetchone()[0] == "todo"
            # SOP 不落任何消息：运行前后消息条数不变（任务创建时已有自己的 system 记录）。
            assert raw.execute("SELECT COUNT(*) FROM message").fetchone()[0] == messages_before
            # 频道 + 任务两个 target；SOP 不新增 target。
            assert raw.execute("SELECT COUNT(*) FROM target").fetchone()[0] == 2
            assert raw.execute("SELECT COUNT(*) FROM plan_run").fetchone()[0] == 0

        # Agent 面没有 SOP 动作：启动者只能是 owner，载荷里不接受 leader。
        assert (
            client.post(
                f"/api/sops/{definition['id']}/runs",
                json={"target_id": channel["id"], "leader_member_id": ex["id"]},
            ).status_code
            == 400
        )
        assert client.get(f"/api/sops/{definition['id']}/runs").status_code == 405


# ─── 边界：未知名/缺成员/非法步骤整体拒绝 ──────────────────────────────────


def test_definition_validation_and_unknown_ids(data_dir) -> None:
    with TestClient(build_app(data_dir)) as client:
        ex, vr, _channel = _two_agents(client)
        empty = client.post("/api/sops", json={"name": "空 SOP", "description": "", "steps": []})
        assert empty.status_code == 400
        bad_attempts = client.post(
            "/api/sops",
            json={
                "name": "坏 SOP",
                "description": "",
                "steps": [
                    {
                        "subject": "步骤",
                        "description": "",
                        "executor_member_id": ex["id"],
                        "verifier_member_id": vr["id"],
                        "max_attempts": 0,
                    }
                ],
            },
        )
        assert bad_attempts.status_code == 400
        unknown_member = client.post(
            "/api/sops",
            json={
                "name": "未知成员",
                "description": "",
                "steps": [
                    {
                        "subject": "步骤",
                        "description": "",
                        "executor_member_id": "agent_missing",
                        "verifier_member_id": None,
                        "max_attempts": 3,
                    }
                ],
            },
        )
        assert unknown_member.status_code == 404
        assert client.get("/api/sops/sop_missing").status_code == 404
        assert client.get("/api/sop-runs/soprun_missing").status_code == 404
