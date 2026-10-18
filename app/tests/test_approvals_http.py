"""#149 人类面黑盒：``GET/POST /api/approvals``、SSE 审批事件与重启一致性。

真实 HTTP 服务 + 真实 ``ApprovalBroker``；成员面用 ``ParkingSeam`` 固定「挂起 → 等待 →
批准/拒绝/超时」的形状（真 AgentScope 的审批闭环由 ``test_approval_seam.py`` 取证）。
本文件的判据全部来自 HTTP 响应与 SSE 帧，不 import 服务内部实现。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from alibot.agent_seam import RoundMessage, RoundOutcome
from alibot.db import open_database
from fastapi.testclient import TestClient

from tests.conftest import build_app, live_backend


class ParkingSeam:
    """固定形状的成员面：登记一条审批，等决策，再收口。"""

    def __init__(self, broker: Any) -> None:
        self.broker = broker
        self.calls: list[dict[str, Any]] = []

    def run_round(self, agent: dict, messages: list[dict], **kwargs: Any) -> RoundOutcome:
        return self.run_round_stream(agent, messages, emit=None, **kwargs)

    def run_round_stream(
        self,
        agent: dict,
        messages: list[dict],
        *,
        mentioned: bool,
        target_id: str,
        task_context: Any = None,
        mandatory_reply: bool = False,
        team: Any = None,
        emit: Any = None,
    ) -> RoundOutcome:
        import asyncio
        import uuid

        reply_id = f"reply-{uuid.uuid4().hex}"
        self.calls.append({"reply_id": reply_id, "target_id": target_id})
        self.broker.register(
            reply_id=reply_id,
            target_id=target_id,
            member_id=agent["id"],
            tool_calls=[{"id": "tc-park", "name": "Write", "input": "{}"}],
        )
        decisions = asyncio.run(self.broker.wait(reply_id))
        if decisions is None:
            return RoundOutcome(status="failed", reason="审批等待超时")
        denied = any(not decision["confirmed"] for decision in decisions)
        return RoundOutcome(
            status="replied",
            reason="审批完成",
            messages=[RoundMessage(body="已拒绝" if denied else "已批准")],
        )


@contextmanager
def _live_approval_backend(data_dir: Path, *, timeout: float = 5.0) -> Iterator[dict[str, Any]]:
    scheduler = _ManualScheduler()
    state: dict[str, Any] = {"scheduler": scheduler}
    with live_backend(
        data_dir,
        scheduler=scheduler,
        seam_factory=ParkingSeam,
        approval_timeout_seconds=timeout,
        state=state,
    ) as base_url:
        state["base_url"] = base_url
        state["http"] = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(10.0, read=None), trust_env=False
        )
        try:
            yield state
        finally:
            state["http"].close()


class _ManualScheduler:
    def __init__(self) -> None:
        self._pending: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self._lock = threading.RLock()

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self._pending.append((fn, args, kwargs))

    def drain(self, limit: int = 100) -> list[Any]:
        results: list[Any] = []
        for _ in range(limit):
            with self._lock:
                if not self._pending:
                    return results
                fn, args, kwargs = self._pending.pop(0)
            results.append(fn(*args, **kwargs))
        return results


def _setup(http: httpx.Client) -> tuple[dict, dict]:
    agent = http.post(
        "/api/agents",
        json={
            "name": "审批成员",
            "role": "工程师",
            "model_id": "ollama:qwen2.5",
            "system_prompt": "简短回复。",
        },
    ).json()["agent"]
    channel = http.post(
        "/api/channels",
        json={"name": "审批频道", "description": "", "member_ids": [agent["id"]]},
    ).json()["channel"]
    return agent, channel


def _collect_sse(base_url: str, target_id: str, seen: dict[str, Any], stop: threading.Event) -> None:
    events = seen.setdefault("events", [])
    types = seen.setdefault("types", set())
    try:
        with (
            httpx.Client(
                base_url=base_url, timeout=httpx.Timeout(10.0, read=None), trust_env=False
            ) as client,
            client.stream("GET", f"/api/channels/{target_id}/events") as response,
        ):
            seen["ready"] = True
            for line in response.iter_lines():
                if stop.is_set():
                    break
                text = line.decode() if isinstance(line, bytes) else line
                if not text.startswith("data: "):
                    continue
                event = json.loads(text[len("data: ") :])
                if event.get("type") in ("approval_required", "approval_resolved"):
                    events.append(event)
                    types.add(event["type"])
    except BaseException as error:  # 测试线程里的异常要带回主线程，否则 pytest 只给一句 warning
        seen["error"] = f"{type(error).__name__}: {error}"
        raise


def _run_round(scheduler: _ManualScheduler) -> threading.Thread:
    thread = threading.Thread(target=scheduler.drain)
    thread.start()
    return thread


def _wait_until(predicate: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_http_approval_wakes_the_round_and_streams_both_events(
    data_dir: Path,
) -> None:
    with _live_approval_backend(data_dir) as live:
        http = live["http"]
        agent, channel = _setup(http)
        seen: dict[str, Any] = {}
        stop = threading.Event()
        stream = threading.Thread(target=_collect_sse, args=(live["base_url"], channel["id"], seen, stop))
        stream.start()
        try:
            _wait_until(lambda: seen.get("ready") is True or "error" in seen)
            assert seen.get("ready") is True, seen
            http.post(f"/api/channels/{channel['id']}/messages", json={"body": "请开始"})
            round_thread = _run_round(live["scheduler"])

            _wait_until(
                lambda: http.get(
                    "/api/approvals",
                    params={"target_id": channel["id"], "state": "pending"},
                ).json()["approvals"]
            )
            pending = http.get(
                "/api/approvals", params={"target_id": channel["id"], "state": "pending"}
            ).json()["approvals"]
            assert len(pending) == 1
            reply_id = pending[0]["reply_id"]

            first = http.post(
                f"/api/approvals/{reply_id}",
                json={"decisions": [{"tool_call_id": "tc-park", "confirmed": True}]},
            )
            assert first.status_code == 200
            assert first.json()["approval"]["state"] == "approved"

            # 幂等：同一批决策重试返回同一终态，不会再次唤醒。
            again = http.post(
                f"/api/approvals/{reply_id}",
                json={"decisions": [{"tool_call_id": "tc-park", "confirmed": True}]},
            )
            assert again.status_code == 200
            assert again.json()["approval"]["state"] == "approved"

            # 相反结论：已完成，不能再改。
            conflict = http.post(
                f"/api/approvals/{reply_id}",
                json={"decisions": [{"tool_call_id": "tc-park", "confirmed": False}]},
            )
            assert conflict.status_code == 409

            round_thread.join(timeout=10)
            assert not round_thread.is_alive()
            _wait_until(lambda: seen.get("types") == {"approval_required", "approval_resolved"})
        finally:
            stop.set()
            stream.join(timeout=3)

        types = [event["type"] for event in seen["events"]]
        assert types == ["approval_required", "approval_resolved"]
        assert seen["events"][0]["tool_calls"][0]["id"] == "tc-park"
        assert seen["events"][1]["state"] == "approved"

        outcomes = http.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        assert outcomes[0]["status"] == "replied"
        assert outcomes[0]["member_id"] == agent["id"]


def test_denied_approval_is_persisted_and_the_round_still_replies(data_dir: Path) -> None:
    with _live_approval_backend(data_dir) as live:
        http = live["http"]
        _agent, channel = _setup(http)
        http.post(f"/api/channels/{channel['id']}/messages", json={"body": "请开始"})
        round_thread = _run_round(live["scheduler"])
        _wait_until(
            lambda: http.get(
                "/api/approvals", params={"target_id": channel["id"], "state": "pending"}
            ).json()["approvals"]
        )
        reply_id = http.get("/api/approvals", params={"target_id": channel["id"], "state": "pending"}).json()[
            "approvals"
        ][0]["reply_id"]

        response = http.post(
            f"/api/approvals/{reply_id}",
            json={"decisions": [{"tool_call_id": "tc-park", "confirmed": False}]},
        )
        assert response.status_code == 200
        assert response.json()["approval"]["state"] == "denied"
        assert response.json()["approval"]["decisions"][0]["confirmed"] is False

        round_thread.join(timeout=10)
        assert not round_thread.is_alive()
        outcomes = http.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        assert outcomes[0]["status"] == "replied"
        assert (
            http.get("/api/approvals", params={"target_id": channel["id"]}).json()["approvals"][0]["state"]
            == "denied"
        )


def test_timeout_expires_the_approval_and_fails_the_round(data_dir: Path) -> None:
    with _live_approval_backend(data_dir, timeout=0.15) as live:
        http = live["http"]
        _agent, channel = _setup(http)
        http.post(f"/api/channels/{channel['id']}/messages", json={"body": "请开始"})
        round_thread = _run_round(live["scheduler"])
        round_thread.join(timeout=10)
        assert not round_thread.is_alive()

        expired = http.get("/api/approvals", params={"target_id": channel["id"], "state": "expired"}).json()[
            "approvals"
        ]
        assert len(expired) == 1
        outcomes = http.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        assert outcomes[0]["status"] == "failed"
        assert "审批等待超时" in outcomes[0]["reason"]


def test_a_pending_row_from_a_crashed_process_is_expired_on_startup(data_dir: Path) -> None:
    database = open_database(data_dir)
    agent = database.create_agent("审批成员", "工程师", "ollama:qwen2.5", "简短回复。")
    channel = database.create_channel("审批频道", member_ids=[agent["id"]])
    database.create_approval_request(
        reply_id="reply-crashed",
        target_id=channel["id"],
        member_id=agent["id"],
        tool_calls=[{"id": "tc-1", "name": "Write", "input": "{}"}],
    )
    database.close()

    with TestClient(build_app(data_dir, seam_factory=ParkingSeam)) as client:
        approvals = client.get("/api/approvals", params={"target_id": channel["id"]}).json()["approvals"]
        assert [approval["state"] for approval in approvals] == ["expired"]
        assert approvals[0]["resolved_at"] is not None


def test_approval_endpoints_validate_state_target_and_unknown_ids(data_dir: Path) -> None:
    with TestClient(build_app(data_dir)) as client:
        assert client.get("/api/approvals", params={"state": "nope"}).status_code == 400
        assert client.get("/api/approvals", params={"target_id": "ch_missing"}).status_code == 404
        missing = client.post(
            "/api/approvals/reply-missing",
            json={"decisions": [{"tool_call_id": "tc-1", "confirmed": True}]},
        )
        assert missing.status_code == 404


def test_permission_rules_are_human_config_with_read_write_delete(data_dir: Path) -> None:
    with TestClient(build_app(data_dir)) as client:
        agent = client.post(
            "/api/agents",
            json={
                "name": "审批成员",
                "role": "工程师",
                "model_id": "ollama:qwen2.5",
                "system_prompt": "简短回复。",
            },
        ).json()["agent"]
        channel = client.post(
            "/api/channels",
            json={"name": "审批频道", "description": "", "member_ids": [agent["id"]]},
        ).json()["channel"]

        created = client.post(
            "/api/permission-rules",
            json={
                "scope_kind": "agent",
                "scope_id": agent["id"],
                "tool_name": "Write",
                "pattern": "src/**",
                "behavior": "allow",
            },
        )
        assert created.status_code == 201
        rule = created.json()["permission_rule"]
        assert rule["behavior"] == "allow"

        listed = client.get(
            "/api/permission-rules", params={"scope_kind": "agent", "scope_id": agent["id"]}
        ).json()["permission_rules"]
        assert [item["id"] for item in listed] == [rule["id"]]

        # 不暴露 BYPASS / DONT_ASK：非法行为整体拒绝，不落半条规则。
        rejected = client.post(
            "/api/permission-rules",
            json={
                "scope_kind": "channel",
                "scope_id": channel["id"],
                "tool_name": "Bash",
                "pattern": "",
                "behavior": "bypass",
            },
        )
        assert rejected.status_code == 400

        deleted = client.delete(f"/api/permission-rules/{rule['id']}")
        assert deleted.status_code == 200
        assert client.get("/api/permission-rules").json()["permission_rules"] == []
