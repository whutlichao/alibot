"""#146 Plan：四工具、三态、装载/回写、重启读回、保留策略与隔离面。

取证明面按仓库既有分层：

- **真实适配器 + 进程内脚本模型**：四工具确实被注册进本轮的 toolkit，并且真的操作
  ``AgentState.tasks``（不是测试自己写库）；
- **HTTP 黑盒**：``GET /api/targets/{id}/plan`` 是运行面板的兜底面，重启后仍能读回；
- **事件总线**：#141 的 ``plan_update`` 公共发布入口被实际调用（帧不带 id、按 target 归属）；
- **库边界**：保留策略与级联清理只经 ``app.state.db`` 这个既有测试接缝触发。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.plan import PLAN_TOOL_NAMES
from alibot.team import member_internal_name
from fastapi.testclient import TestClient

from tests.conftest import backend, build_app, create_agent, create_channel
from tests.scripted import ManualScheduler


class _ScriptedToolModel(ChatModelBase):
    """可流式的确定性模型：每次模型调用按脚本吐一串 ``ChatResponse``。"""

    def __init__(self, script: Sequence[Sequence[ChatResponse]]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        self.calls.append({"messages": messages, "tools": tools})
        chunks = self.script[min(len(self.calls) - 1, len(self.script) - 1)]

        async def stream() -> AsyncGenerator[ChatResponse, None]:
            for chunk in chunks:
                yield chunk

        return stream()

    @property
    def tool_names(self) -> set[str]:
        names: set[str] = set()
        for call in self.calls:
            for tool in call["tools"] or []:
                function = tool.get("function", tool)
                name = function.get("name")
                if name:
                    names.add(str(name))
        return names

    @property
    def system_text(self) -> str:
        for call in self.calls:
            for message in call["messages"]:
                role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
                if role == "system":
                    content = (
                        message.get("content")
                        if isinstance(message, dict)
                        else getattr(message, "content", "")
                    )
                    return str(content)
        return ""


class _ModelSequenceSeam(AgentScopeAgentSeam):
    """真实适配器；模型构造换成按顺序取用的脚本模型（每个 Agent 一份）。"""

    def __init__(self, models: Sequence[ChatModelBase]) -> None:
        super().__init__(timeout_seconds=10.0)
        self._models = list(models)
        self._used: list[ChatModelBase] = []

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        model = self._models[min(len(self._used), len(self._models) - 1)]
        self._used.append(model)
        return model


def _tool_call_chunks(call_id: str, name: str, raw: str) -> list[ChatResponse]:
    """把一次工具调用拆成非最终块，框架才会转换成 ``ToolCall*`` 事件。"""
    middle = len(raw) // 2
    return [
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw[:middle])], is_last=False),
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw[middle:])], is_last=False),
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw)], is_last=True),
    ]


def _call(call_id: str, name: str, payload: dict[str, Any]) -> list[ChatResponse]:
    return _tool_call_chunks(call_id, name, json.dumps(payload))


def _final(text: str) -> list[ChatResponse]:
    return [ChatResponse(content=[TextBlock(text=text)], is_last=True)]


def _scripted_rounds() -> list[_ScriptedToolModel]:
    """三轮：创建 → 进行中 + 读取 → 完成。每轮都以一条正文收口。"""
    return [
        _ScriptedToolModel(
            [
                _call("c1", "TaskCreate", {"subject": "整理验收清单", "description": "先列条目"}),
                _final("第一轮完成"),
            ]
        ),
        _ScriptedToolModel(
            [
                _call("c2", "TaskUpdate", {"task_id": "1", "status": "in_progress"}),
                _call("c3", "TaskGet", {"task_id": "1"}),
                _call("c4", "TaskList", {}),
                _final("第二轮完成"),
            ]
        ),
        _ScriptedToolModel(
            [
                _call("c5", "TaskUpdate", {"task_id": "1", "status": "completed"}),
                _final("第三轮完成"),
            ]
        ),
    ]


def _prepare_claimed_task(client: TestClient, agent_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """频道 + 任务，并把任务指派给该 Agent（这样任务线程消息会唤醒它）。"""
    channel = create_channel(client, member_ids=[agent_id]).json()["channel"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "规划验收"}).json()["task"]
    assigned = client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": agent_id})
    assert assigned.status_code == 200, assigned.json()
    return task, channel


def _plan_of(client: TestClient, target_id: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/targets/{target_id}/plan", params=params)
    assert response.status_code == 200, response.json()
    return response.json()["plan"]


def test_four_plan_tools_write_three_states_and_survive_restart(data_dir) -> None:
    """四工具都可调用；三态、装载/回写与重启读回都经 HTTP 面取证。"""
    models = _scripted_rounds()
    seam = _ModelSequenceSeam(cast(list[ChatModelBase], models))
    scheduler = ManualScheduler()
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        agent = create_agent(client).json()["agent"]
        task, _channel = _prepare_claimed_task(client, agent["id"])
        events = cast(Any, client.app).state.events
        frames: list[dict[str, Any]] = []
        with events.subscribe(task["target_id"]) as queue:
            for round_index in range(3):
                sent = client.post(
                    f"/api/tasks/{task['id']}/messages", json={"body": f"第 {round_index + 1} 轮"}
                )
                assert sent.status_code == 201
                scheduler.drain()
            while not queue.empty():
                frames.append(queue.get_nowait())

        # 四工具都进了这一轮 provider 的 tool schema。
        seen = set().union(*(model.tool_names for model in models))
        assert set(PLAN_TOOL_NAMES) <= seen

        plan = _plan_of(client, task["target_id"])
        member_entry = next(item for item in plan["members"] if item["member_id"] == agent["id"])
        runs = member_entry["runs"]
        assert len(runs) == 3
        states = {run["tasks"][0]["state"] for run in runs}
        assert states == {"pending", "in_progress", "completed"}
        assert all(run["status"] == "completed" for run in runs)
        assert all(run["tasks"][0]["subject"] == "整理验收清单" for run in runs)

        # live-only 的 plan_update：字段齐全、按 target 归属，且不推进重连游标（无 seq / id）。
        plan_frames = [frame for frame in frames if frame.get("type") == "plan_update"]
        assert plan_frames, [frame.get("type") for frame in frames]
        created = next(frame for frame in plan_frames if frame["task"]["state"] == "pending")
        assert created["target_id"] == task["target_id"]
        assert created["member_id"] == agent["id"]
        assert created["plan_run_id"] in {run["id"] for run in runs}
        assert "seq" not in created and "id" not in created
        assert any(frame["task"]["state"] == "completed" for frame in plan_frames)

        # Plan 不碰任务五态，也不进搜索/预算：任务仍是创建时的 todo，Plan 的 subject 搜不到。
        assert client.get(f"/api/tasks/{task['id']}").json()["task"]["status"] == "todo"
        budget = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]
        assert budget["used"] == 0
        assert client.get("/api/search", params={"q": "整理验收清单"}).json()["hits"] == []

        # 不新增 target：这个数据目录里只有频道影子行 + 任务 target 两个。
        database = cast(Any, client.app).state.db
        with sqlite3.connect(database.path) as raw:
            assert raw.execute("SELECT COUNT(*) FROM target").fetchone()[0] == 2

    # 重启：同一个数据目录、全新进程内存，Plan 仍可读回同一份清单。
    with backend(data_dir) as restarted:
        reloaded = _plan_of(restarted, task["target_id"])
        member_entry = next(item for item in reloaded["members"] if item["member_id"] == agent["id"])
        assert {run["tasks"][0]["state"] for run in member_entry["runs"]} == {
            "pending",
            "in_progress",
            "completed",
        }


def test_team_leader_and_assigned_member_each_have_their_own_plan(data_dir) -> None:
    """team 轮次：队长有 Plan；被 TeamAssign 的成员有自己的 Plan，但提示里没有任务上下文。"""
    leader_model = _ScriptedToolModel(
        [
            _call(
                "lead-1",
                "TeamAssign",
                {"member": member_internal_name("PLACEHOLDER"), "prompt": "把清单列出来"},
            ),
            _final("队长汇总"),
        ]
    )
    member_model = _ScriptedToolModel(
        [
            _call("mem-1", "TaskCreate", {"subject": "成员自己的清单", "description": "只属于成员"}),
            _final("成员完成"),
        ]
    )
    scheduler = ManualScheduler()
    seam = _ModelSequenceSeam(
        cast(list[ChatModelBase], [leader_model, member_model]),
    )
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=cast(Any, seam))) as client:
        leader = create_agent(client, name="队长").json()["agent"]
        member = create_agent(client, name="成员").json()["agent"]
        channel = create_channel(client, name="team 频道", member_ids=[leader["id"], member["id"]]).json()[
            "channel"
        ]
        config = client.patch(
            f"/api/channels/{channel['id']}/config",
            json={"mode": "team", "team_leader_member_id": leader["id"]},
        )
        assert config.status_code == 200, config.json()
        task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "team 规划"}).json()["task"]

        # 队长模型拿到的派生名在运行时才知道：把占位换掉（成员 id 是稳定的）。
        leader_model.script[0] = _call(
            "lead-1",
            "TeamAssign",
            {"member": member_internal_name(member["id"]), "prompt": "把清单列出来"},
        )
        sent = client.post(f"/api/tasks/{task['id']}/messages", json={"body": "开始"})
        assert sent.status_code == 201
        scheduler.drain()

        plan = _plan_of(client, task["target_id"])
        by_member = {item["member_id"]: item for item in plan["members"]}
        assert leader["id"] in by_member and by_member[leader["id"]]["runs"]
        assert member["id"] in by_member and by_member[member["id"]]["runs"]
        member_tasks = by_member[member["id"]]["runs"][0]["tasks"]
        assert [item["subject"] for item in member_tasks] == ["成员自己的清单"]
        assert by_member[leader["id"]]["runs"][0]["tasks"] == []

        # 成员提示**没有**任务上下文（任务标题/受派者身份都不进成员上下文）。
        assert "任务上下文" in leader_model.system_text
        assert "任务上下文" not in member_model.system_text
        assert "team 规划" not in member_model.system_text


def test_retention_keeps_latest_twenty_and_prunes_at_startup(data_dir) -> None:
    """每个 (target, member) 最近 20 run 或 30 天（先到者为准）；plan_task 随 run 级联。"""
    scheduler = ManualScheduler()
    with TestClient(build_app(data_dir, scheduler=scheduler, seam=seam_for_http_only())) as client:
        agent = create_agent(client).json()["agent"]
        task, _channel = _prepare_claimed_task(client, agent["id"])
        database = cast(Any, client.app).state.db
        for index in range(25):
            run = database.start_plan_run(target_id=task["target_id"], member_id=agent["id"], base_seq=index)
            database.finish_plan_run(
                run["id"],
                status="completed",
                tasks=[
                    {
                        "seq": 1,
                        "subject": f"清单 {index}",
                        "description": "",
                        "state": "pending",
                        "created_at": "2026-10-18T00:00:00.000Z",
                        "updated_at": "2026-10-18T00:00:00.000Z",
                    }
                ],
            )
        plan = _plan_of(client, task["target_id"])
        assert len(plan["members"][0]["runs"]) == 20

        # 30 天边界：把当前最早的一条回拨到 40 天前；重启时的启动清理必须删掉它。
        oldest = plan["members"][0]["runs"][-1]
        old = (
            (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        with sqlite3.connect(database.path) as raw:
            raw.execute("UPDATE plan_run SET started_at = ? WHERE id = ?", (old, oldest["id"]))
            raw.commit()
        assert database.get_plan_run(oldest["id"])["started_at"] == old

    with backend(data_dir) as restarted:
        plan = _plan_of(restarted, task["target_id"])
        assert len(plan["members"][0]["runs"]) == 19
        assert oldest["id"] not in {run["id"] for run in plan["members"][0]["runs"]}
        database = cast(Any, restarted.app).state.db
        with sqlite3.connect(database.path) as raw:
            orphans = raw.execute(
                "SELECT COUNT(*) FROM plan_task WHERE plan_run_id NOT IN (SELECT id FROM plan_run)"
            ).fetchone()[0]
        assert orphans == 0


def test_plan_endpoint_404s_for_unknown_target(client: TestClient) -> None:
    response = client.get("/api/targets/nope/plan")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def seam_for_http_only() -> Any:
    """保留策略用例不跑推理：一个不会被唤醒的脚本化替身就够了。"""
    from tests.scripted import IGNORE, ScriptedAgentSeam

    return ScriptedAgentSeam(default=IGNORE)
