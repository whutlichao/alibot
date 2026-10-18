"""#143 TeamAssign 预算：每次分派原子扣 1 跳、耗尽给错误 ToolResult、运行记录正确。

测试面（与 ADR-0019 / R1 结论一致）：

- **DB 原语**：``Database.try_consume_jump`` 用一个 ``BEGIN IMMEDIATE`` + 条件 UPDATE 裁决；
  进程内 100 次并发与多连接并发都不越过当前世代上界。
- **pipeline**：``BudgetedTeamPipeline`` 在成员运行前逐条裁决；每次分派对**对应 target**
  ``target_budget.used`` 恰好 +1；耗尽的那一条不运行成员、给队长 ERROR ``ToolResult``；
  ``team_assignment`` 的 ``state`` / ``jump_consumed`` 与 ``used`` 一致；task target 用
  自己的预算、不推进频道预算；队长最终回复不扣跳；成员失败不退款。

模型是进程内脚本模型（与 #142 的真实适配器用例同源）：不联网、零成本，但走真实的
``TeamPipeline._run_assignments`` 与真实的 ``Database``。
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.db import open_database
from alibot.team import TeamRoundContext, member_internal_name
from fastapi.testclient import TestClient

from tests.conftest import (
    LOCAL_MODEL_ID,
    ManualScheduler,
    backend,
    create_agent,
    create_channel,
    messages,
    send,
)

DEFAULT_LIMIT = 3


def _db(client: TestClient) -> Any:
    return cast(Any, client.app).state.db


def _text(text: str) -> list[ChatResponse]:
    """One complete assistant response (a member's final reply)."""

    return [ChatResponse(content=[TextBlock(text=text)], is_last=True)]


def _tool_calls(calls: list[tuple[str, str, str]]) -> list[ChatResponse]:
    """One assistant response carrying ``(call_id, member_name, prompt)`` calls.

    与 R1 探针同形：工具调用必须在非 final 分片里出现，框架才会把它转成 block 事件。
    """

    blocks = [
        ToolCallBlock(
            id=call_id,
            name="TeamAssign",
            input=json.dumps({"member": member_name, "prompt": prompt}),
        )
        for call_id, member_name, prompt in calls
    ]
    return [
        ChatResponse(content=list(blocks), is_last=False),
        ChatResponse(content=list(blocks), is_last=True),
    ]


class _ScriptedModel(ChatModelBase):
    """Deterministic model: one scripted response per call, recording inputs."""

    def __init__(self, script: list[list[ChatResponse]]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="scripted",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = list(script)
        self.calls = 0
        self.seen: list[Any] = []

    async def _call_api(self, *args: Any, **kwargs: Any):
        messages = kwargs.get("messages")
        if messages is None and len(args) >= 2:
            messages = args[1]
        self.seen.append(messages)
        chunks = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        async def stream():
            for chunk in chunks:
                yield chunk

        return stream()


class _FailingModel(_ScriptedModel):
    """A member whose model call fails: the jump is already consumed and must not refund."""

    async def _call_api(self, *args: Any, **kwargs: Any):
        raise RuntimeError("scripted member failure")


class _InterruptingModel(_ScriptedModel):
    """A member whose stream is interrupted: ``INTERRUPTED`` result, jump still consumed."""

    async def _call_api(self, *args: Any, **kwargs: Any):
        raise asyncio.CancelledError


class _BudgetSeam(AgentScopeAgentSeam):
    """真实适配器 + 进程内脚本模型队列（按构造顺序：队长在前、成员随后）。"""

    def __init__(self, models: list[ChatModelBase]) -> None:
        super().__init__(timeout_seconds=10.0)
        self._models = list(models)

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._models.pop(0)


def _human(body: str = "开工") -> dict[str, Any]:
    return {
        "seq": 1,
        "kind": "human",
        "body": body,
        "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
    }


def _context(
    db: Any,
    *,
    target_id: str,
    leader_id: str,
    member_ids: tuple[str, ...],
    team_run_id: str,
    task_id: str | None = None,
) -> TeamRoundContext:
    return TeamRoundContext(
        target_id=target_id,
        leader_id=leader_id,
        members=tuple(db.get_agent(member_id) for member_id in member_ids),
        task_id=task_id,
        team_run_id=team_run_id,
        budget=db,
    )


def _round(seam: _BudgetSeam, leader: dict[str, Any], context: TeamRoundContext) -> Any:
    return seam.run_round_stream(
        leader,
        [_human()],
        mentioned=False,
        target_id=context.target_id,
        team=context,
    )


# ─── DB 原语：单事务检查 + 扣减 ──────────────────────────────────────────
def test_try_consume_jump_never_exceeds_the_epoch_limit(client: TestClient) -> None:
    """100 次进程内并发尝试：恰好 ``limit`` 次成功，``used`` 停在 ``limit``。"""

    db = _db(client)
    channel = create_channel(client, name="并发预算").json()["channel"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        decisions = list(pool.map(lambda _: db.try_consume_jump(channel["id"]), range(100)))

    assert sum(1 for decision in decisions if decision["ok"]) == DEFAULT_LIMIT
    assert all(decision["used"] <= DEFAULT_LIMIT for decision in decisions)
    state = db.budget_state(channel["id"])
    assert state["used"] == DEFAULT_LIMIT
    assert state["remaining"] == 0


def test_try_consume_jump_is_atomic_across_connections(data_dir: Path) -> None:
    """多连接并发（模拟多进程）下条件 UPDATE 仍不越过上界。"""

    databases = [open_database(data_dir) for _ in range(8)]
    channel = databases[0].create_channel(name="跨连接预算")

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(
            pool.map(
                lambda index: databases[index % len(databases)].try_consume_jump(channel["id"]),
                range(100),
            )
        )

    assert sum(1 for decision in decisions if decision["ok"]) == DEFAULT_LIMIT
    assert databases[0].budget_state(channel["id"])["used"] == DEFAULT_LIMIT


def test_a_saved_override_does_not_raise_the_pre_epoch_limit(client: TestClient) -> None:
    """还没开世代时，保存的频道覆盖只是 ``pending_limit``；当前上界仍是内置默认 3。"""

    db = _db(client)
    channel = create_channel(client, name="待生效上界").json()["channel"]
    response = client.patch(f"/api/channels/{channel['id']}/budget", json={"budget_limit": 5})
    assert response.status_code == 200, response.json()

    decisions = [db.try_consume_jump(channel["id"]) for _ in range(4)]

    assert [decision["ok"] for decision in decisions] == [True, True, True, False]
    state = db.budget_state(channel["id"])
    assert state["used"] == DEFAULT_LIMIT
    assert state["limit"] == DEFAULT_LIMIT


# ─── pipeline：每次分派 +1、耗尽 ERROR、记录一致 ─────────────────────────
def test_each_assignment_consumes_one_jump_and_the_next_is_an_error(client: TestClient) -> None:
    db = _db(client)
    leader = create_agent(client, name="预算队长").json()["agent"]
    member = create_agent(client, name="预算成员").json()["agent"]
    channel = create_channel(client, name="预算频道", member_ids=[leader["id"], member["id"]]).json()[
        "channel"
    ]
    team_run = db.create_team_run(target_id=channel["id"], leader_member_id=leader["id"], base_seq=1)
    member_name = member_internal_name(member["id"])
    calls = [(f"call-{index}", member_name, f"任务 {index}") for index in range(1, 5)]

    leader_model = _ScriptedModel([_tool_calls(calls), _text("队长最终答复")])
    member_model = _ScriptedModel([_text("成员结果")])
    seam = _BudgetSeam([leader_model, member_model])
    context = _context(
        db,
        target_id=channel["id"],
        leader_id=leader["id"],
        member_ids=(member["id"],),
        team_run_id=team_run["id"],
    )

    outcome = _round(seam, leader, context)

    assert outcome.status == "replied"
    assert [message.body for message in outcome.messages] == ["队长最终答复"]
    assert outcome.consumed_budget is False, "队长最终回复不计跳"
    assert member_model.calls == DEFAULT_LIMIT, "第 N+1 次分派不得运行成员"

    state = db.budget_state(channel["id"])
    assert state["used"] == DEFAULT_LIMIT

    rows = {row["tool_call_id"]: row for row in db.list_team_assignments(channel["id"])}
    assert set(rows) == {call_id for call_id, _, _ in calls}
    for index in range(1, DEFAULT_LIMIT + 1):
        row = rows[f"call-{index}"]
        assert row["state"] == "succeeded"
        assert row["jump_consumed"] == 1
        assert row["task_id"] is None
        assert row["leader_member_id"] == leader["id"]
        assert row["member_id"] == member["id"]

    rejected = rows[f"call-{DEFAULT_LIMIT + 1}"]
    assert rejected["state"] == "budget-exhausted"
    assert rejected["jump_consumed"] == 0
    assert sum(row["jump_consumed"] for row in rows.values()) == state["used"]

    # 队长看到的第 4 条结果是 ERROR ToolResult，文案写明未运行成员；结果顺序与调用顺序一致。
    dumped = json.dumps(leader_model.seen[1], ensure_ascii=False, default=str)
    assert "call-4" in dumped and "预算耗尽" in dumped
    positions = [dumped.index(f"call-{index}") for index in range(1, 5)]
    assert positions == sorted(positions)


def test_task_target_uses_its_own_budget_and_not_the_channel(client: TestClient) -> None:
    db = _db(client)
    leader = create_agent(client, name="任务队长").json()["agent"]
    member = create_agent(client, name="任务成员").json()["agent"]
    channel = create_channel(client, name="任务预算频道", member_ids=[leader["id"], member["id"]]).json()[
        "channel"
    ]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "预算隔离"}).json()["task"]
    member_name = member_internal_name(member["id"])

    channel_run = db.create_team_run(target_id=channel["id"], leader_member_id=leader["id"], base_seq=1)
    channel_seam = _BudgetSeam(
        [
            _ScriptedModel([_tool_calls([("channel-call", member_name, "频道任务")]), _text("频道答复")]),
            _ScriptedModel([_text("频道成员结果")]),
        ]
    )
    _round(
        channel_seam,
        leader,
        _context(
            db,
            target_id=channel["id"],
            leader_id=leader["id"],
            member_ids=(member["id"],),
            team_run_id=channel_run["id"],
        ),
    )

    task_run = db.create_team_run(target_id=task["target_id"], leader_member_id=leader["id"], base_seq=1)
    task_seam = _BudgetSeam(
        [
            _ScriptedModel([_tool_calls([("task-call", member_name, "任务线程任务")]), _text("任务答复")]),
            _ScriptedModel([_text("任务成员结果")]),
        ]
    )
    _round(
        task_seam,
        leader,
        _context(
            db,
            target_id=task["target_id"],
            leader_id=leader["id"],
            member_ids=(member["id"],),
            team_run_id=task_run["id"],
            task_id=task["id"],
        ),
    )

    assert db.budget_state(channel["id"])["used"] == 1
    assert db.budget_state(task["target_id"])["used"] == 1, "task target 用 task 自己的预算"

    task_rows = db.list_team_assignments(task["target_id"])
    assert len(task_rows) == 1
    assert task_rows[0]["task_id"] == task["id"]
    assert task_rows[0]["jump_consumed"] == 1
    assert task_rows[0]["state"] == "succeeded"


def test_concurrent_team_rounds_do_not_cross_the_bound(client: TestClient) -> None:
    """四条并发的队长轮各分派一次：恰好 3 次扣减成功，第 4 次 ERROR 且不运行成员。"""

    db = _db(client)
    leader = create_agent(client, name="并发队长").json()["agent"]
    member = create_agent(client, name="并发成员").json()["agent"]
    channel = create_channel(client, name="并发分派", member_ids=[leader["id"], member["id"]]).json()[
        "channel"
    ]
    member_name = member_internal_name(member["id"])

    def one_round(index: int) -> int:
        team_run = db.create_team_run(
            target_id=channel["id"], leader_member_id=leader["id"], base_seq=index + 1
        )
        member_model = _ScriptedModel([_text("并发成员结果")])
        seam = _BudgetSeam(
            [
                _ScriptedModel(
                    [_tool_calls([(f"concurrent-{index}", member_name, "并发任务")]), _text("答复")]
                ),
                member_model,
            ]
        )
        outcome = _round(
            seam,
            leader,
            _context(
                db,
                target_id=channel["id"],
                leader_id=leader["id"],
                member_ids=(member["id"],),
                team_run_id=team_run["id"],
            ),
        )
        assert outcome.status == "replied"
        return member_model.calls

    with ThreadPoolExecutor(max_workers=4) as pool:
        member_runs = list(pool.map(one_round, range(4)))

    assert sum(member_runs) == DEFAULT_LIMIT, "只有获授权的 3 次真正运行了成员"
    assert db.budget_state(channel["id"])["used"] == DEFAULT_LIMIT
    rows = db.list_team_assignments(channel["id"])
    assert len(rows) == 4
    assert sorted(row["state"] for row in rows) == [
        "budget-exhausted",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert sum(row["jump_consumed"] for row in rows) == DEFAULT_LIMIT


def test_a_failed_member_does_not_refund_the_jump(client: TestClient) -> None:
    db = _db(client)
    leader = create_agent(client, name="失败队长").json()["agent"]
    member = create_agent(client, name="失败成员").json()["agent"]
    channel = create_channel(client, name="失败不退款", member_ids=[leader["id"], member["id"]]).json()[
        "channel"
    ]
    team_run = db.create_team_run(target_id=channel["id"], leader_member_id=leader["id"], base_seq=1)
    seam = _BudgetSeam(
        [
            _ScriptedModel(
                [
                    _tool_calls([("failed-call", member_internal_name(member["id"]), "会失败的任务")]),
                    _text("队长看到失败后自行回答"),
                ]
            ),
            _FailingModel([_text("不会用到")]),
        ]
    )

    outcome = _round(
        seam,
        leader,
        _context(
            db,
            target_id=channel["id"],
            leader_id=leader["id"],
            member_ids=(member["id"],),
            team_run_id=team_run["id"],
        ),
    )

    assert db.budget_state(channel["id"])["used"] == 1, "分派已发生 ⇒ 跳已消耗，不退款"
    rows = db.list_team_assignments(channel["id"])
    assert len(rows) == 1
    assert rows[0]["jump_consumed"] == 1
    assert rows[0]["state"] == "failed"
    # 成员失败被收口为 ERROR ToolResult 还是整轮失败，取决于框架；两种情况下预算都不退回。
    assert outcome.status in ("replied", "failed")


def test_an_interrupted_member_does_not_refund_the_jump(client: TestClient) -> None:
    db = _db(client)
    leader = create_agent(client, name="中断队长").json()["agent"]
    member = create_agent(client, name="中断成员").json()["agent"]
    channel = create_channel(client, name="中断不退款", member_ids=[leader["id"], member["id"]]).json()[
        "channel"
    ]
    team_run = db.create_team_run(target_id=channel["id"], leader_member_id=leader["id"], base_seq=1)
    seam = _BudgetSeam(
        [
            _ScriptedModel(
                [
                    _tool_calls([("interrupted-call", member_internal_name(member["id"]), "会被中断的任务")]),
                    _text("队长看到中断后自行回答"),
                ]
            ),
            _InterruptingModel([_text("不会用到")]),
        ]
    )

    outcome = _round(
        seam,
        leader,
        _context(
            db,
            target_id=channel["id"],
            leader_id=leader["id"],
            member_ids=(member["id"],),
            team_run_id=team_run["id"],
        ),
    )

    assert outcome.status == "replied"
    assert db.budget_state(channel["id"])["used"] == 1, "中断也不退款"
    rows = db.list_team_assignments(channel["id"])
    assert len(rows) == 1
    assert rows[0]["jump_consumed"] == 1
    assert rows[0]["state"] == "interrupted"


def test_http_round_wires_the_budget_ledger_and_team_run(data_dir: Path) -> None:
    """编排把 ``budget`` 与当轮的 ``team_run_id`` 交给 pipeline：HTTP 一轮后记录与预算对得上。

    这条用例保护的是**装配点**（``_team_round_context`` / ``TeamRoundContext``），不是又一个
    pipeline 单元：进程内脚本模型仍走真实 ``TeamPipeline`` 与真实库。
    """

    setup = open_database(data_dir)
    leader = setup.create_agent(
        name="HTTP 队长", role="协调", model_id=LOCAL_MODEL_ID, system_prompt="你是队长。"
    )
    member = setup.create_agent(
        name="HTTP 成员", role="成员", model_id=LOCAL_MODEL_ID, system_prompt="你是成员。"
    )
    channel = setup.create_channel(name="HTTP 预算", member_ids=[leader["id"], member["id"]])
    setup.close()

    leader_model = _ScriptedModel(
        [
            _tool_calls([("http-call", member_internal_name(member["id"]), "HTTP 任务")]),
            _text("HTTP 队长答复"),
        ]
    )
    member_model = _ScriptedModel([_text("HTTP 成员结果")])
    seam = _BudgetSeam([leader_model, member_model])
    scheduler = ManualScheduler()

    with backend(data_dir, scheduler=scheduler, seam=seam) as client:
        response = client.patch(
            f"/api/channels/{channel['id']}/config",
            json={"mode": "team", "team_leader_member_id": leader["id"]},
        )
        assert response.status_code == 200, response.json()

        assert send(client, channel["id"], "开工").status_code == 201
        scheduler.drain()

        db = _db(client)
        state = db.budget_state(channel["id"])
        assert state["used"] == 1, "一次 TeamAssign 只扣一跳；队长最终回复不再扣"
        rows = db.list_team_assignments(channel["id"])
        assert len(rows) == 1
        assert rows[0]["jump_consumed"] == 1
        runs = db.list_team_runs(channel["id"])
        assert [run["status"] for run in runs] == ["completed"]
        assert rows[0]["team_run_id"] == runs[0]["id"]
        assert [
            message["body"] for message in messages(client, channel["id"]) if message["kind"] == "agent"
        ] == ["HTTP 队长答复"]
