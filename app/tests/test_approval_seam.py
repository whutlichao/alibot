"""#149 真 seam 的审批闭环：ASK → pending/SSE → 人类决策 → 唤醒同一轮。

这些用例用**真实** AgentScope ``Agent`` 与真实文件工具，只把 provider 换成进程内脚本模型；
审批记录、broker 等待与规则判定都走生产实现。覆盖：

- 批准后工具真的执行；拒绝后工具**没有**执行（AgentScope 记为 ``denied``，本轮仍可收口）；
- 审批等待不受 120 秒模型超时影响（用很短模型超时 + 更长的真实等待取证）；
- 超时把记录标 ``expired``、本轮 ``failed``，并中断挂起的 Agent；
- ``trusted-writes`` 根内自动写、根外仍 ASK；``read-only`` 直接 DENY；
- 规则（频道 + Agent 覆盖）确实进入权限引擎。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam
from alibot.approval import ApprovalBroker
from alibot.db import open_database
from alibot.events import EventBus

AGENT_NAME = "审批成员"


class _ScriptedModel(ChatModelBase):
    """按脚本吐块的确定性模型（与 #140 的测试夹具同一族）。"""

    def __init__(self, script: Sequence[Sequence[ChatResponse]]) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self.script = list(script)
        self.calls = 0

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        chunks = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        async def stream() -> AsyncGenerator[ChatResponse, None]:
            for chunk in chunks:
                yield chunk

        return stream()


class _StubModelSeam(AgentScopeAgentSeam):
    def __init__(self, model: ChatModelBase, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model = model

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._model


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> list[ChatResponse]:
    raw = json.dumps(arguments)
    middle = len(raw) // 2
    return [
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw[:middle])], is_last=False),
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw[middle:])], is_last=False),
        ChatResponse(content=[ToolCallBlock(id=call_id, name=name, input=raw)], is_last=True),
    ]


def _write_then_reply(path: str = "notes.txt", content: str = "approved\n") -> _ScriptedModel:
    return _ScriptedModel(
        [
            _tool_call("tc-write", "Write", {"file_path": path, "content": content}),
            [ChatResponse(content=[TextBlock(text="已处理。")], is_last=True)],
        ]
    )


@pytest.fixture
def library(tmp_path: Path) -> Any:
    root = tmp_path / "project"
    root.mkdir()
    data_dir = tmp_path / "data"
    database = open_database(data_dir)
    agent = database.create_agent(AGENT_NAME, "工程师", "openai:stub-model", "简短回复。")
    channel = database.create_channel("审批频道", member_ids=[agent["id"]])
    database.close()
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE channel SET workspace_root = ?, permission_mode = 'ask-writes' WHERE id = ?",
            (str(root), channel["id"]),
        )
        connection.commit()
    database = open_database(data_dir)
    try:
        yield database, agent, channel, root
    finally:
        database.close()


def _run_round(
    seam: AgentScopeAgentSeam,
    database: Any,
    agent: dict,
    channel: dict,
    *,
    on_pending: Callable[[dict[str, Any]], list[dict[str, Any]] | None],
    delay: float = 0.0,
    timeout: float = 10.0,
) -> tuple[Any, list[str]]:
    """跑一轮，并按 ``on_pending`` 的指示逐批提交决策。

    ``on_pending(record)`` 拿到当时那条 pending 记录，返回要提交的决策批；返回 ``None`` 表示
    「到此为止」（例如超时用例根本不提交）。同一轮里第二次 ASK 会让同一个 ``reply_id`` 重新
    挂起，所以调用方只能一次批一批地提交——这正是人类面在 Studio 里的操作节奏。
    """
    result: dict[str, Any] = {}
    decided: list[str] = []

    def run() -> None:
        result["outcome"] = seam.run_round(
            database.get_agent(agent["id"]),
            [{"seq": 1, "kind": "human", "body": "请写入", "author": {"id": "owner"}}],
            mentioned=False,
            target_id=channel["id"],
        )

    thread = threading.Thread(target=run)
    thread.start()
    broker = seam._approvals
    assert broker is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = database.list_approval_requests(channel["id"], "pending")
        record = pending[0] if pending else None
        if record is None:
            if not thread.is_alive():
                break
            time.sleep(0.01)
            continue
        batch = on_pending(record)
        if batch is None:
            break
        time.sleep(delay)
        broker.submit(record["reply_id"], batch)
        decided.extend(str(item["tool_call_id"]) for item in batch)
        delay = 0.0
    thread.join(timeout=15)
    assert not thread.is_alive(), "seam 轮次没有收口"
    return result["outcome"], decided


def _run_with_decision(
    seam: AgentScopeAgentSeam,
    database: Any,
    agent: dict,
    channel: dict,
    *,
    decision: dict[str, Any] | None,
    delay: float = 0.0,
) -> Any:
    outcome, _decided = _run_round(
        seam,
        database,
        agent,
        channel,
        on_pending=(lambda _record: [decision]) if decision is not None else (lambda _record: None),
        delay=delay,
    )
    return outcome


def _run_approving_every_batch(
    seam: AgentScopeAgentSeam,
    database: Any,
    agent: dict,
    channel: dict,
    *,
    batches: int,
) -> tuple[Any, list[str]]:
    """逐批批准：每批只提交库里**尚未决策**的调用，凑满 ``batches`` 批即止。"""
    submitted: list[str] = []

    def on_pending(record: dict[str, Any]) -> list[dict[str, Any]] | None:
        if len(submitted) >= batches:
            return None
        known = {str(item["tool_call_id"]) for item in record["decisions"]}
        batch = [
            {"tool_call_id": str(call["id"]), "confirmed": True}
            for call in record["tool_calls"]
            if str(call["id"]) not in known
        ]
        if not batch:
            return None
        submitted.append(record["reply_id"])
        return batch

    return _run_round(seam, database, agent, channel, on_pending=on_pending)


def _broker(database: Any, timeout: float = 5.0) -> ApprovalBroker:
    return ApprovalBroker(database, EventBus(), timeout_seconds=timeout)


def _seam(database: Any, model: ChatModelBase, broker: ApprovalBroker, timeout: float = 5.0) -> Any:
    return _StubModelSeam(
        model,
        timeout_seconds=timeout,
        workspace_of=database.workspace_settings_for_target,
        permission_of=database.permission_settings_for_target,
        approvals=broker,
    )


def test_approval_executes_the_tool_and_completes_the_round(library: Any) -> None:
    database, agent, channel, root = library
    broker = _broker(database)
    # 模型超时故意压到 0.2 秒；真实等待 0.4 秒再批准——审批等待必须独立于模型超时。
    seam = _seam(database, _write_then_reply(), broker, timeout=0.2)

    outcome = _run_with_decision(
        seam,
        database,
        agent,
        channel,
        decision={"tool_call_id": "tc-write", "confirmed": True},
        delay=0.4,
    )

    assert outcome.status == "replied", outcome.reason
    assert (root / "notes.txt").read_text(encoding="utf-8") == "approved\n"
    record = database.list_approval_requests(channel["id"], "approved")
    assert len(record) == 1
    assert record[0]["decisions"] == [{"tool_call_id": "tc-write", "confirmed": True, "rules": []}]
    # 单 Agent 轮没有 team_run：审批行明确为 NULL（不是一条猜出来的关联）。
    assert record[0]["team_run_id"] is None


def test_a_second_ask_in_the_same_reply_is_approved_like_the_first(library: Any) -> None:
    """#215：同一个 ``reply_id`` 里的第二次 ASK 必须和第一次一样可决策，批准后本轮继续收口。

    AgentScope 的 ``reply_id`` 是**一条回复**的标识（``docs/agentscope/2.0.10`` 的
    human-in-the-loop 页）：同一轮里第二个需要确认的工具调用会再发一次
    ``RequireUserConfirmEvent``，``reply_id`` 与第一个相同。修复前 ``register()`` 见到终态
    直接原样返回，seam 的 ``wait()`` 拿到空批 → 整轮 failed（生产里那条
    ``resolved without new decisions``）。
    """
    database, agent, channel, root = library
    broker = _broker(database)
    model = _ScriptedModel(
        [
            _tool_call("tc-one", "Write", {"file_path": "one.txt", "content": "1\n"}),
            _tool_call("tc-two", "Write", {"file_path": "two.txt", "content": "2\n"}),
            [ChatResponse(content=[TextBlock(text="两件都写好了。")], is_last=True)],
        ]
    )
    seam = _seam(database, model, broker)

    outcome, decided = _run_approving_every_batch(seam, database, agent, channel, batches=2)

    assert outcome.status == "replied", outcome.reason
    assert decided == ["tc-one", "tc-two"]
    assert (root / "one.txt").read_text(encoding="utf-8") == "1\n"
    assert (root / "two.txt").read_text(encoding="utf-8") == "2\n"
    # 同一条回复只落**一行**审批记录：两次 ASK 是同一行里的两个工具调用。
    records = database.list_approval_requests(channel["id"])
    assert len(records) == 1
    assert [call["id"] for call in records[0]["tool_calls"]] == ["tc-one", "tc-two"]
    assert [item["tool_call_id"] for item in records[0]["decisions"]] == ["tc-one", "tc-two"]
    assert records[0]["state"] == "approved"
    assert records[0]["resolved_at"] is not None


def test_denial_does_not_execute_the_tool_and_the_round_still_finishes(library: Any) -> None:
    database, agent, channel, root = library
    broker = _broker(database)
    seam = _seam(database, _write_then_reply(), broker)

    outcome = _run_with_decision(
        seam,
        database,
        agent,
        channel,
        decision={"tool_call_id": "tc-write", "confirmed": False},
    )

    assert outcome.status == "replied", outcome.reason
    assert not (root / "notes.txt").exists()
    record = database.list_approval_requests(channel["id"], "denied")
    assert len(record) == 1


def test_waiting_for_approval_is_not_killed_by_the_model_timeout(library: Any) -> None:
    database, agent, channel, root = library
    broker = _broker(database, timeout=10.0)
    seam = _seam(database, _write_then_reply(), broker, timeout=0.05)

    outcome = _run_with_decision(
        seam,
        database,
        agent,
        channel,
        decision={"tool_call_id": "tc-write", "confirmed": True},
        delay=0.3,
    )

    assert outcome.status == "replied", outcome.reason
    assert (root / "notes.txt").exists()


def test_a_terminal_record_without_new_decisions_still_fails_the_round(library: Any) -> None:
    """终态 + 没有新决策仍然是失败出口，绝不被吞成静默成功（#215 的硬边界）。

    重开只发生在「这批调用里有记录尚未包含的 id」时；记录已收口且没有可回送的决策，说明这一轮
    的挂起没有对应的新确认，seam 只能收口 failed，而不是假装 replied。
    """
    database, agent, channel, root = library
    broker = _broker(database)
    seam = _seam(database, _write_then_reply(), broker)

    def approve_out_of_band(record: dict[str, Any]) -> list[dict[str, Any]] | None:
        # 人类在 seam 之外（另一条路径）把记录推到终态，且不经过 broker 的决策队列——
        # wait() 因此拿到空批，正是「resolved without new decisions」的场景。
        with sqlite3.connect(database.path) as connection:
            connection.execute(
                "UPDATE approval_request SET state = 'approved', resolved_at = "
                "strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE reply_id = ?",
                (record["reply_id"],),
            )
            connection.commit()
        return None

    outcome, _decided = _run_round(seam, database, agent, channel, on_pending=approve_out_of_band)

    assert outcome.status == "failed"
    assert "resolved without new decisions" in outcome.reason
    assert not (root / "notes.txt").exists()


def test_approval_timeout_marks_expired_and_fails_the_round(library: Any) -> None:
    database, agent, channel, root = library
    broker = _broker(database, timeout=0.05)
    seam = _seam(database, _write_then_reply(), broker)

    outcome = _run_with_decision(seam, database, agent, channel, decision=None)

    assert outcome.status == "failed"
    assert "审批等待超时" in outcome.reason
    record = database.list_approval_requests(channel["id"], "expired")
    assert len(record) == 1
    assert not (root / "notes.txt").exists()


def test_trusted_writes_auto_allows_inside_the_root_without_an_approval(library: Any) -> None:
    database, agent, channel, root = library
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE agent_config SET permission_mode = 'trusted-writes' WHERE member_id = ?",
            (agent["id"],),
        )
        connection.commit()
    broker = _broker(database)
    seam = _seam(database, _write_then_reply(), broker)

    outcome = _run_with_decision(seam, database, agent, channel, decision=None)

    assert outcome.status == "replied", outcome.reason
    assert (root / "notes.txt").read_text(encoding="utf-8") == "approved\n"
    assert database.list_approval_requests(channel["id"]) == []


def test_trusted_writes_still_asks_for_a_write_outside_the_root(library: Any) -> None:
    database, agent, channel, root = library
    outside = root.parent / "outside.txt"
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE agent_config SET permission_mode = 'trusted-writes' WHERE member_id = ?",
            (agent["id"],),
        )
        connection.commit()
    broker = _broker(database)
    seam = _seam(database, _write_then_reply(str(outside)), broker)

    outcome = _run_with_decision(seam, database, agent, channel, decision=None)

    assert outcome.status == "failed"
    assert not outside.exists()
    assert len(database.list_approval_requests(channel["id"], "expired")) == 1


def test_read_only_denies_the_write_without_creating_an_approval(library: Any) -> None:
    database, agent, channel, root = library
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE agent_config SET permission_mode = 'read-only' WHERE member_id = ?",
            (agent["id"],),
        )
        connection.commit()
    broker = _broker(database)
    seam = _seam(database, _write_then_reply(), broker)

    outcome = _run_with_decision(seam, database, agent, channel, decision=None)

    assert outcome.status == "replied", outcome.reason
    assert not (root / "notes.txt").exists()
    assert database.list_approval_requests(channel["id"]) == []


def test_agent_rule_overrides_the_channel_rule(library: Any) -> None:
    database, agent, channel, root = library
    database.create_permission_rule(
        scope_kind="channel",
        scope_id=channel["id"],
        tool_name="Write",
        pattern="notes.txt",
        behavior="deny",
    )
    database.create_permission_rule(
        scope_kind="agent",
        scope_id=agent["id"],
        tool_name="Write",
        pattern="notes.txt",
        behavior="allow",
    )
    broker = _broker(database)
    seam = _seam(database, _write_then_reply(), broker)

    outcome = _run_with_decision(seam, database, agent, channel, decision=None)

    assert outcome.status == "replied", outcome.reason
    assert (root / "notes.txt").exists()
    assert database.list_approval_requests(channel["id"]) == []


def test_approved_rules_are_persisted_as_agent_rules(library: Any) -> None:
    database, agent, channel, _root = library
    broker = _broker(database)
    seam = _seam(database, _write_then_reply(), broker)

    outcome = _run_with_decision(
        seam,
        database,
        agent,
        channel,
        decision={
            "tool_call_id": "tc-write",
            "confirmed": True,
            "rules": [{"tool_name": "Write", "pattern": "notes.txt", "behavior": "allow"}],
        },
    )

    assert outcome.status == "replied", outcome.reason
    rules = database.list_permission_rules("agent", agent["id"])
    assert [(rule["tool_name"], rule["pattern"], rule["behavior"]) for rule in rules] == [
        ("Write", "notes.txt", "allow")
    ]


class _SequenceModelSeam(_StubModelSeam):
    """按构造顺序发模型：队长先建，成员随后（与 ``_run_team_round_stream`` 的装配顺序一致）。"""

    def __init__(self, models: Sequence[ChatModelBase], **kwargs: Any) -> None:
        super().__init__(models[0], **kwargs)
        self._models = list(models)

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        return self._models.pop(0)


def _team_assignment(call_id: str, member: str) -> list[ChatResponse]:
    raw = json.dumps({"member": member, "prompt": "请写入"})
    middle = len(raw) // 2
    return [
        ChatResponse(
            content=[ToolCallBlock(id=call_id, name="TeamAssign", input=raw[:middle])],
            is_last=False,
        ),
        ChatResponse(
            content=[ToolCallBlock(id=call_id, name="TeamAssign", input=raw[middle:])],
            is_last=False,
        ),
        ChatResponse(content=[ToolCallBlock(id=call_id, name="TeamAssign", input=raw)], is_last=True),
    ]


def test_team_member_approval_routes_by_reply_id_and_completes_the_round(library: Any) -> None:
    """TeamPipeline：成员挂起 → 按成员 reply_id 批准 → 成员写文件 → 队长收口。"""
    from alibot.agent_seam import member_internal_name
    from alibot.team import TeamRoundContext

    database, leader, channel, root = library
    member = database.create_agent("成员", "工程师", "openai:stub-model", "简短回复。")
    database.add_target_member(channel["id"], member["id"])
    broker = _broker(database)
    leader_model = _ScriptedModel(
        [
            _team_assignment("call-assign", member_internal_name(member["id"])),
            [ChatResponse(content=[TextBlock(text="leader final")], is_last=True)],
        ]
    )
    member_model = _write_then_reply()
    seam = _SequenceModelSeam(
        [leader_model, member_model],
        timeout_seconds=5.0,
        workspace_of=database.workspace_settings_for_target,
        permission_of=database.permission_settings_for_target,
        approvals=broker,
    )
    # #143 之后 team 轮次必须带运行记录与预算正本：分派在运行成员前原子扣跳并落
    # ``team_assignment``。这里只补装配，不改本用例的审批断言。
    team_run = database.create_team_run(target_id=channel["id"], leader_member_id=leader["id"], base_seq=1)
    team = TeamRoundContext(
        target_id=channel["id"],
        leader_id=leader["id"],
        members=(member,),
        team_run_id=str(team_run["id"]),
        budget=database,
    )
    result: dict[str, Any] = {}

    def run() -> None:
        result["outcome"] = seam.run_round_stream(
            database.get_agent(leader["id"]),
            [{"seq": 1, "kind": "human", "body": "请安排", "author": {"id": "owner"}}],
            mentioned=False,
            target_id=channel["id"],
            team=team,
            emit=None,
        )

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 10
    pending = []
    while time.monotonic() < deadline:
        pending = database.list_approval_requests(channel["id"], "pending")
        if pending:
            break
        time.sleep(0.01)
    assert pending, "TeamPipeline 没有登记成员审批"
    assert pending[0]["member_id"] == member["id"]
    # team 轮的 run id 来自 pipeline/context（不是装配可选项）：审批行必须挂上这一轮。
    assert pending[0]["team_run_id"] == team_run["id"]
    broker.submit(pending[0]["reply_id"], [{"tool_call_id": "tc-write", "confirmed": True}])
    thread.join(timeout=15)
    assert not thread.is_alive()

    outcome = result["outcome"]
    assert outcome.status == "replied", outcome.reason
    assert outcome.messages[0].body == "leader final"
    assert (root / "notes.txt").read_text(encoding="utf-8") == "approved\n"
    assert database.list_approval_requests(channel["id"], "approved")[0]["member_id"] == member["id"]
