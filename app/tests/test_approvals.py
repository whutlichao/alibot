"""#149 审批代理与权限读路径：进程内 broker 的状态机、等待、超时与重启一致性。

测试落在两个公开面上：

- ``ApprovalBroker`` 的 ``register / wait / submit``（进程内生命周期）；
- ``Database.permission_settings_for_target`` 的档位与规则读路径（频道级 + Agent 级）。

HTTP 面与真 seam 的取证在 ``test_approvals_http.py``；这里不需要联网或模型。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from alibot.approval import ApprovalBroker
from alibot.db import DomainError, open_database
from alibot.events import EventBus

TARGET = "ch_approval"
MEMBER = "mem_approval"
CALLS = [
    {"id": "tc-a", "name": "Write", "input": '{"file_path":"a.txt","content":"x"}'},
    {"id": "tc-b", "name": "Write", "input": '{"file_path":"b.txt","content":"y"}'},
]


@pytest.fixture
def library(tmp_path: Path) -> Any:
    database = open_database(tmp_path / "data")
    agent = database.create_agent("小明", "工程师", "ollama:qwen2.5", "简短回复。")
    channel = database.create_channel("审批频道", member_ids=[agent["id"]])
    try:
        yield database, agent, channel
    finally:
        database.close()


def _broker(database: Any, timeout: float = 5.0) -> tuple[ApprovalBroker, EventBus]:
    events = EventBus()
    return ApprovalBroker(database, events, timeout_seconds=timeout), events


def _wait_for_record(database: Any, reply_id: str, predicate: Any, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = database.get_approval_request(reply_id)
        if record is not None and predicate(record):
            return record
        time.sleep(0.005)
    raise AssertionError(f"approval {reply_id} did not reach the expected state")


def _register(broker: ApprovalBroker, channel: dict, agent: dict, reply_id: str = "reply-1") -> dict:
    return broker.register(
        reply_id=reply_id,
        target_id=channel["id"],
        member_id=agent["id"],
        tool_calls=CALLS,
    )


def test_register_persists_pending_and_publishes_the_live_event(library: Any) -> None:
    database, agent, channel = library
    broker, events = _broker(database)

    with events.subscribe(channel["id"]) as queue:
        record = _register(broker, channel, agent)
        frame = queue.get(timeout=1.0)

    assert record["state"] == "pending"
    assert [call["id"] for call in record["tool_calls"]] == ["tc-a", "tc-b"]
    assert frame["type"] == "approval_required"
    assert frame["reply_id"] == "reply-1"
    assert [call["id"] for call in frame["tool_calls"]] == ["tc-a", "tc-b"]
    # 记录只含工具调用与决策，没有额外字段可放凭证。
    assert set(record) == {
        "reply_id",
        "target_id",
        "member_id",
        "team_run_id",
        "tool_calls",
        "state",
        "decisions",
        "requested_at",
        "resolved_at",
    }


def test_approval_wakes_the_waiter_and_marks_approved(library: Any) -> None:
    database, agent, channel = library
    broker, _events = _broker(database)
    _register(broker, channel, agent)

    async def scenario() -> list[dict[str, Any]] | None:
        task = asyncio.create_task(broker.wait("reply-1"))
        await asyncio.sleep(0)
        _wait_for_record(database, "reply-1", lambda record: record["state"] == "pending")
        thread = threading.Thread(
            target=lambda: broker.submit("reply-1", [{"tool_call_id": "tc-a", "confirmed": True}])
        )
        thread.start()
        result = await task
        thread.join(timeout=2)
        return result

    decisions = asyncio.run(scenario())

    assert decisions == [{"tool_call_id": "tc-a", "confirmed": True, "rules": []}]
    record = database.get_approval_request("reply-1")
    assert record is not None
    assert record["state"] == "pending"  # tc-b 还没有决定


def test_a_denial_and_a_full_approval_reach_the_documented_terminal_states(library: Any) -> None:
    database, agent, channel = library
    broker, events = _broker(database)
    _register(broker, channel, agent, "reply-deny")
    with events.subscribe(channel["id"]):
        denied = broker.submit(
            "reply-deny",
            [
                {"tool_call_id": "tc-a", "confirmed": False},
                {"tool_call_id": "tc-b", "confirmed": True},
            ],
        )
        assert denied["state"] == "denied"
        assert denied["decisions"][0]["confirmed"] is False

    _register(broker, channel, agent, "reply-allow")
    approved = broker.submit(
        "reply-allow",
        [
            {"tool_call_id": "tc-a", "confirmed": True},
            {"tool_call_id": "tc-b", "confirmed": True},
        ],
    )
    assert approved["state"] == "approved"
    assert approved["resolved_at"] is not None


def test_partial_decisions_only_wake_with_the_new_batch(library: Any) -> None:
    database, agent, channel = library
    broker, _events = _broker(database)
    _register(broker, channel, agent)

    async def scenario() -> list[list[dict[str, Any]] | None]:
        first = asyncio.create_task(broker.wait("reply-1"))
        await asyncio.sleep(0)
        threading.Thread(
            target=lambda: broker.submit("reply-1", [{"tool_call_id": "tc-a", "confirmed": True}])
        ).start()
        batch_one = await first

        second = asyncio.create_task(broker.wait("reply-1"))
        await asyncio.sleep(0)
        threading.Thread(
            target=lambda: broker.submit("reply-1", [{"tool_call_id": "tc-b", "confirmed": False}])
        ).start()
        batch_two = await second
        return [batch_one, batch_two]

    first, second = asyncio.run(scenario())

    assert [item["tool_call_id"] for item in first or []] == ["tc-a"]
    assert [item["tool_call_id"] for item in second or []] == ["tc-b"]


def test_timeout_marks_expired_and_late_decisions_cannot_reopen(library: Any) -> None:
    database, agent, channel = library
    broker, _events = _broker(database, timeout=0.05)
    _register(broker, channel, agent)

    assert asyncio.run(broker.wait("reply-1")) is None
    record = database.get_approval_request("reply-1")
    assert record is not None
    assert record["state"] == "expired"
    assert record["resolved_at"] is not None

    with pytest.raises(DomainError) as caught:
        broker.submit("reply-1", [{"tool_call_id": "tc-a", "confirmed": True}])
    assert caught.value.status == 409


def test_retrying_the_same_decisions_is_idempotent_and_conflicts_are_409(library: Any) -> None:
    database, agent, channel = library
    broker, _events = _broker(database)
    _register(broker, channel, agent)
    body = [
        {"tool_call_id": "tc-a", "confirmed": True},
        {"tool_call_id": "tc-b", "confirmed": True},
    ]

    first = broker.submit("reply-1", body)
    second = broker.submit("reply-1", body)

    assert first["state"] == second["state"] == "approved"
    assert second["decisions"] == first["decisions"]

    _register(broker, channel, agent, "reply-2")
    broker.submit("reply-2", [{"tool_call_id": "tc-a", "confirmed": True}])
    with pytest.raises(DomainError) as caught:
        broker.submit("reply-2", [{"tool_call_id": "tc-a", "confirmed": False}])
    assert caught.value.status == 409


def test_unknown_tool_call_and_duplicate_tool_call_are_rejected(library: Any) -> None:
    database, agent, channel = library
    broker, _events = _broker(database)
    _register(broker, channel, agent)

    with pytest.raises(DomainError) as unknown:
        broker.submit("reply-1", [{"tool_call_id": "tc-nope", "confirmed": True}])
    assert unknown.value.status == 400

    with pytest.raises(DomainError) as duplicate:
        broker.submit(
            "reply-1",
            [
                {"tool_call_id": "tc-a", "confirmed": True},
                {"tool_call_id": "tc-a", "confirmed": True},
            ],
        )
    assert duplicate.value.status == 400


def test_startup_expires_pending_rows_from_the_previous_process(library: Any) -> None:
    database, agent, channel = library
    first, _events = _broker(database)
    _register(first, channel, agent)

    restarted, _events = _broker(database)

    assert restarted.expire_pending_at_startup() == ["reply-1"]
    record = database.get_approval_request("reply-1")
    assert record is not None
    assert record["state"] == "expired"


def test_permission_settings_resolve_mode_root_and_both_rule_scopes(library: Any) -> None:
    database: Any
    database, agent, channel = library
    database.create_permission_rule(
        scope_kind="channel",
        scope_id=channel["id"],
        tool_name="Write",
        pattern="src/**",
        behavior="allow",
    )
    database.create_permission_rule(
        scope_kind="agent",
        scope_id=agent["id"],
        tool_name="Write",
        pattern="src/**",
        behavior="deny",
    )

    settings = database.permission_settings_for_target(channel["id"], agent["id"])

    assert settings is not None
    assert settings["mode"] == "ask-writes"
    assert settings["channel_rules"][0]["behavior"] == "allow"
    assert settings["agent_rules"][0]["behavior"] == "deny"
    rules = [rule for rule in database.list_permission_rules() if rule["scope_kind"] == "agent"]
    assert len(rules) == 1


def test_registering_the_same_calls_again_does_not_reopen_a_resolved_record(library: Any) -> None:
    """#215 幂等边界：终态记录重复 register 同一批调用 → 不重开、不发布、状态不变。"""
    database, agent, channel = library
    broker, events = _broker(database)
    _register(broker, channel, agent)
    resolved = broker.submit(
        "reply-1",
        [
            {"tool_call_id": "tc-a", "confirmed": True},
            {"tool_call_id": "tc-b", "confirmed": True},
        ],
    )
    assert resolved["state"] == "approved"

    with events.subscribe(channel["id"]) as queue:
        again = _register(broker, channel, agent)

    assert again["state"] == "approved"
    assert again["resolved_at"] == resolved["resolved_at"]
    assert [call["id"] for call in again["tool_calls"]] == ["tc-a", "tc-b"]
    assert [item["tool_call_id"] for item in again["decisions"]] == ["tc-a", "tc-b"]
    assert queue.empty(), "重复 register 同一批调用不得再发布 approval_required"


def test_a_new_tool_call_reopens_a_resolved_record_and_keeps_the_old_decisions(library: Any) -> None:
    """#215：同一 ``reply_id`` 的新调用把终态记录重开成 pending，旧决策作为历史保留。"""
    database, agent, channel = library
    broker, events = _broker(database)
    _register(broker, channel, agent)
    broker.submit(
        "reply-1",
        [
            {"tool_call_id": "tc-a", "confirmed": True},
            {"tool_call_id": "tc-b", "confirmed": True},
        ],
    )
    third = {"id": "tc-c", "name": "Write", "input": '{"file_path":"c.txt","content":"z"}'}
    # 把第一批的请求时间钉到一个旧时刻，才能分辨「重开是否刷新了它」（utc_now 是毫秒级）。
    first_batch_at = "2026-01-01T00:00:00.000Z"
    database.update_approval_request("reply-1", requested_at=first_batch_at)

    with events.subscribe(channel["id"]) as queue:
        reopened = broker.register(
            reply_id="reply-1",
            target_id=channel["id"],
            member_id=agent["id"],
            tool_calls=[third],
        )
        frame = queue.get(timeout=1.0)

    assert reopened["state"] == "pending"
    assert reopened["resolved_at"] is None
    # 重开是**新的一批**：请求时间移到这一批的起点（前端倒计时按 requested_at + 600s 判定）。
    assert reopened["requested_at"] != first_batch_at
    assert reopened["requested_at"] > first_batch_at
    assert [call["id"] for call in reopened["tool_calls"]] == ["tc-a", "tc-b", "tc-c"]
    assert [item["tool_call_id"] for item in reopened["decisions"]] == ["tc-a", "tc-b"]
    # 新帧只含**新**调用：前端据此重开卡片，不会把已决策的两条再问一遍。
    assert frame["type"] == "approval_required"
    assert frame["reply_id"] == "reply-1"
    assert [call["id"] for call in frame["tool_calls"]] == ["tc-c"]

    # 旧决策仍是历史事实：重复提交同一结论幂等；相反结论 409。
    assert broker.submit("reply-1", [{"tool_call_id": "tc-a", "confirmed": True}])["state"] == "pending"
    with pytest.raises(DomainError) as conflict:
        broker.submit("reply-1", [{"tool_call_id": "tc-a", "confirmed": False}])
    assert conflict.value.status == 409

    # 只决策新调用即可收口。
    final = broker.submit("reply-1", [{"tool_call_id": "tc-c", "confirmed": True}])
    assert final["state"] == "approved"
    assert [item["tool_call_id"] for item in final["decisions"]] == ["tc-a", "tc-b", "tc-c"]


def test_reopening_restarts_the_waiting_window_with_the_existing_duration(library: Any) -> None:
    """重开时按**现有时长**重开等待窗口：第一批的等待预算不会耗掉第二批的。"""
    database, agent, channel = library
    broker, _events = _broker(database, timeout=0.3)
    _register(broker, channel, agent)
    broker.submit(
        "reply-1",
        [
            {"tool_call_id": "tc-a", "confirmed": True},
            {"tool_call_id": "tc-b", "confirmed": True},
        ],
    )
    # 把第一批排队中的决策取走（没有等待者时它们留在队列里）。
    assert [item["tool_call_id"] for item in asyncio.run(broker.wait("reply-1")) or []] == [
        "tc-a",
        "tc-b",
    ]
    # 等到第一批的窗口早就过掉，再登记新调用。
    time.sleep(0.4)
    reopened = broker.register(
        reply_id="reply-1",
        target_id=channel["id"],
        member_id=agent["id"],
        tool_calls=[{"id": "tc-c", "name": "Write", "input": "{}"}],
    )
    assert reopened["state"] == "pending"

    async def scenario() -> list[dict[str, Any]] | None:
        task = asyncio.create_task(broker.wait("reply-1"))
        await asyncio.sleep(0)
        thread = threading.Thread(
            target=lambda: broker.submit("reply-1", [{"tool_call_id": "tc-c", "confirmed": True}])
        )
        thread.start()
        result = await task
        thread.join(timeout=2)
        return result

    decisions = asyncio.run(scenario())

    # 窗口确实重开过：否则这次 wait 会因为首批 deadline 已过而直接标 expired 返回 None。
    assert decisions == [{"tool_call_id": "tc-c", "confirmed": True, "rules": []}]
    final = database.get_approval_request("reply-1")
    assert final is not None
    assert final["state"] == "approved"


def test_an_expired_record_is_not_reopened_by_a_late_registration(library: Any) -> None:
    """``expired`` 仍是终态：迟到登记不复活（超时 / 重启语义不变）。"""
    database, agent, channel = library
    broker, events = _broker(database, timeout=0.05)
    _register(broker, channel, agent)
    assert asyncio.run(broker.wait("reply-1")) is None

    with events.subscribe(channel["id"]) as queue:
        late = broker.register(
            reply_id="reply-1",
            target_id=channel["id"],
            member_id=agent["id"],
            tool_calls=[{"id": "tc-c", "name": "Write", "input": "{}"}],
        )

    assert late["state"] == "expired"
    assert [call["id"] for call in late["tool_calls"]] == ["tc-a", "tc-b"]
    assert queue.empty()


def test_updating_an_approval_keeps_resolved_at_unless_it_is_written_explicitly(library: Any) -> None:
    """``update_approval_request``：不传 = 不修改；显式 ``None`` = 写回 ``NULL``。"""
    database, agent, channel = library
    broker, _events = _broker(database)
    _register(broker, channel, agent)
    resolved = broker.submit(
        "reply-1",
        [
            {"tool_call_id": "tc-a", "confirmed": True},
            {"tool_call_id": "tc-b", "confirmed": True},
        ],
    )
    assert resolved["resolved_at"] is not None

    untouched = database.update_approval_request("reply-1", state="approved")
    assert untouched["resolved_at"] == resolved["resolved_at"]

    cleared = database.update_approval_request("reply-1", state="pending", resolved_at=None)
    assert cleared["resolved_at"] is None
    assert cleared["state"] == "pending"


def test_approval_records_keep_only_tool_calls_and_never_extra_secret_fields(library: Any) -> None:
    """审批记录只存工具调用与决策；调用方夹带的密钥类字段不落库、不回读。"""
    database, agent, channel = library
    broker, _events = _broker(database)

    record = broker.register(
        reply_id="reply-secret",
        target_id=channel["id"],
        member_id=agent["id"],
        tool_calls=[
            {
                "id": "tc-secret",
                "name": "Bash",
                "input": "ls",
                "api_key": "sk-should-not-be-stored",
                "env": {"TOKEN": "secret"},
            }
        ],
    )

    assert record["tool_calls"] == [{"id": "tc-secret", "name": "Bash", "input": "ls"}]
    assert "sk-should-not-be-stored" not in json.dumps(record, ensure_ascii=False)
