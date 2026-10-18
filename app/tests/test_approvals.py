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
