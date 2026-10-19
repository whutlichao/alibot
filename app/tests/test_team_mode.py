"""#142 team 模式：频道配置、只唤醒队长、pipeline 每轮重建。

测试只断言既有对外面：HTTP 载荷、消息流、``round_outcome``、``team_run``（库内运行记录）、
以及脚本化替身记录下来的唤醒输入。``app.state.db`` 与 ``app.state.orchestrator`` 是仓库既有的
测试接缝（与 ``test_tasks.py`` / ``test_auto_claim.py`` 同源），不是本票新开的读路径。
"""

from __future__ import annotations

from typing import Any, cast

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock
from agentscope.model import ChatModelBase, ChatResponse
from alibot.agent_seam import AgentScopeAgentSeam, TransferTaskAction
from alibot.team import TeamRoundContext, build_team_pipeline, member_internal_name
from fastapi.testclient import TestClient

from tests.conftest import (
    LOCAL_MODEL_ID,
    create_agent,
    create_channel,
    drain,
    messages,
    outcomes,
    send,
)
from tests.scripted import ACTION_ONLY


def _db(client: TestClient) -> Any:
    return cast(Any, client.app).state.db


def _orchestrator(client: TestClient) -> Any:
    return cast(Any, client.app).state.orchestrator


def _config(client: TestClient, channel_id: str) -> dict[str, Any]:
    response = client.get(f"/api/channels/{channel_id}/config")
    assert response.status_code == 200
    return response.json()["config"]


def _patch_config(client: TestClient, channel_id: str, **fields: Any):
    return client.patch(f"/api/channels/{channel_id}/config", json=fields)


def _make_team(client: TestClient, leader: str, *members: str, name: str = "团队频道") -> dict[str, Any]:
    channel = create_channel(client, name=name, member_ids=[leader, *members]).json()["channel"]
    response = _patch_config(client, channel["id"], mode="team", team_leader_member_id=leader)
    assert response.status_code == 200, response.json()
    return channel


def _agent_messages(client: TestClient, channel_id: str) -> list[dict[str, Any]]:
    return [message for message in messages(client, channel_id) if message["kind"] == "agent"]


# ─── 频道配置（GET / PATCH） ──────────────────────────────────────────────
def test_channel_config_defaults_and_team_scope(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    config = _config(client, channel["id"])

    assert config["mode"] == "autonomous"
    assert config["team_scope"] == "channel+task"
    assert config["team_leader_member_id"] is None
    assert config["leader_available"] is False
    assert config["workspace_root"] is None
    assert config["workspace_backend"] == "local"
    assert config["permission_mode"] == "ask-writes"


def test_channel_config_patch_round_trips_all_fields(client: TestClient, tmp_path) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    channel = create_channel(client, member_ids=[leader["id"]]).json()["channel"]

    saved = _patch_config(
        client,
        channel["id"],
        mode="team",
        team_leader_member_id=leader["id"],
        workspace_root=str(tmp_path),
        workspace_backend="docker",
        permission_mode="trusted-writes",
    )
    assert saved.status_code == 200

    config = _config(client, channel["id"])
    assert config["mode"] == "team"
    assert config["team_scope"] == "channel+task"
    assert config["team_leader_member_id"] == leader["id"]
    assert config["leader_available"] is True
    assert config["leader_reason"] == ""
    assert config["workspace_root"] == str(tmp_path)
    assert config["workspace_backend"] == "docker"
    assert config["permission_mode"] == "trusted-writes"


def test_channel_config_rejects_every_invalid_field_without_partial_write(
    client: TestClient,
) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    outsider = create_agent(client, name="外人").json()["agent"]
    channel = create_channel(client, member_ids=[leader["id"]]).json()["channel"]

    before = _config(client, channel["id"])
    cases = [
        ({"mode": "team"}, "team_leader_member_id"),
        ({"mode": "anarchy"}, "mode"),
        ({"workspace_backend": "quantum"}, "workspace_backend"),
        ({"permission_mode": "bypass"}, "permission_mode"),
        (
            {"mode": "team", "team_leader_member_id": outsider["id"]},
            "team_leader_member_id",
        ),
        ({"workspace_root": "relative/path"}, "workspace_root"),
        ({"team_leader_member_id": ""}, "team_leader_member_id"),
        # 显式 null 与未知键在请求体校验层就被拒绝（fields 只给请求体那一条）。
        ({"mode": None}, "body"),
        ({"team_scope": "channel+task"}, "body"),
    ]
    for payload, field in cases:
        response = _patch_config(client, channel["id"], **payload)
        assert response.status_code == 400, payload
        body = response.json()["error"]
        assert body["code"] == "validation_failed"
        assert field in body["fields"], payload

    assert _config(client, channel["id"]) == before


def test_channel_config_can_clear_the_workspace_root(client: TestClient, tmp_path) -> None:
    channel = create_channel(client).json()["channel"]
    assert _patch_config(client, channel["id"], workspace_root=str(tmp_path)).status_code == 200
    assert _config(client, channel["id"])["workspace_root"] == str(tmp_path)
    assert _patch_config(client, channel["id"], workspace_root=None).status_code == 200
    assert _config(client, channel["id"])["workspace_root"] is None


# ─── channel 型 target：只唤醒队长 ───────────────────────────────────────
def test_team_channel_human_message_wakes_only_the_leader(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])

    assert send(client, channel["id"], "开工").status_code == 201
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    # 只有队长有轮次结果与消息；成员没有 round_outcome、也没有 message。
    rows = outcomes(client, channel["id"])
    assert [row["member_id"] for row in rows] == [leader["id"]]
    assert rows[0]["status"] == "replied"
    agent_messages = _agent_messages(client, channel["id"])
    assert [message["author"]["id"] for message in agent_messages] == [leader["id"]]
    assert harness["seam"].calls_for(member["id"]) == []

    # pipeline 上下文来自当前成员（每轮重建）。
    call = harness["seam"].calls_for(leader["id"])[-1]
    assert call["team"] is not None
    assert {row["id"] for row in call["team"].members} == {member["id"]}

    # 队长最终回复不再唤醒任何人（D2）：编排的 notify 读数就是 0。
    assert _orchestrator(client).notify(channel["id"], agent_messages[0]["seq"]) == 0
    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].call_count == 1


def test_team_channel_piercing_is_a_one_off_outside_the_pipeline(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    outsider = create_agent(client, name="外援").json()["agent"]
    channel = _make_team(client, leader["id"])

    send(client, channel["id"], f"@{outsider['display_name']} 帮忙看一眼")
    assert harness["scheduler"].pending_count() == 2
    drain(harness)

    # 穿透仍一次性送达（D17），但不进入 pipeline：队长是唯一拿到 team 上下文的轮次。
    leader_call = harness["seam"].calls_for(leader["id"])[-1]
    assert leader_call["team"] is not None
    assert harness["seam"].calls_for(outsider["id"])
    assert all(call["team"] is None for call in harness["seam"].calls_for(outsider["id"]))
    assert len(_agent_messages(client, channel["id"])) == 2
    # 穿透回复之后零唤醒。
    assert harness["scheduler"].pending_count() == 0


def test_team_member_is_added_to_the_next_round_pipeline(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    channel = _make_team(client, leader["id"], first["id"])

    send(client, channel["id"], "第一轮")
    drain(harness)
    first_round = harness["seam"].calls_for(leader["id"])[-1]["team"]
    assert {row["id"] for row in first_round.members} == {first["id"]}

    joined = client.post(f"/api/channels/{channel['id']}/members", json={"member_id": second["id"]})
    assert joined.status_code == 200

    send(client, channel["id"], "第二轮")
    drain(harness)
    second_round = harness["seam"].calls_for(leader["id"])[-1]["team"]
    assert {row["id"] for row in second_round.members} == {first["id"], second["id"]}


def test_a_member_that_cannot_run_leaves_the_next_round_pipeline(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])

    send(client, channel["id"], "第一轮")
    drain(harness)
    assert {row["id"] for row in harness["seam"].calls_for(leader["id"])[-1]["team"].members} == {
        member["id"]
    }

    # 频道成员只能加入、不能移出（既有契约），因此“移出 pipeline”的产品路径是成员不可运行：
    # 每轮重建读的是当前成员 + 可用性，停用后下一轮不再被装配。
    assert client.patch(f"/api/agents/{member['id']}", json={"enabled": False}).status_code == 200
    send(client, channel["id"], "第二轮")
    drain(harness)
    assert harness["seam"].calls_for(leader["id"])[-1]["team"].members == ()


def test_invalid_leader_is_silent_and_readable(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    channel = _make_team(client, leader["id"])

    assert client.patch(f"/api/agents/{leader['id']}", json={"enabled": False}).status_code == 200

    assert send(client, channel["id"], "还醒着吗").status_code == 201
    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].call_count == 0
    assert outcomes(client, channel["id"]) == []

    config = _config(client, channel["id"])
    assert config["leader_available"] is False
    assert "停用" in config["leader_reason"]

    runs = _db(client).list_team_runs(channel["id"])
    assert [run["status"] for run in runs] == ["invalid-leader"]
    assert runs[0]["leader_member_id"] == leader["id"]


# ─── task 型 target：只唤醒队长、不穿透 ─────────────────────────────────
def test_team_task_human_message_wakes_only_the_leader_and_does_not_pierce(
    client: TestClient, harness: dict
) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    outsider = create_agent(client, name="外援").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])

    created = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "团队任务"})
    assert created.status_code == 201
    task = created.json()["task"]
    assert task["status"] == "todo"
    assert task["claimed_at"] is None
    # 建任务只唤醒队长一轮（#179 A），没有成员轮次、也没有认领。
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(leader["id"])) == 1
    assert harness["seam"].calls_for(member["id"]) == []
    assert _db(client).get_task(task["id"])["claimed_at"] is None

    response = client.post(
        f"/api/tasks/{task['id']}/messages",
        json={"body": f"@{outsider['display_name']} 也看看", "task_ref": task["id"]},
    )
    assert response.status_code == 201
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    assert harness["seam"].calls_for(outsider["id"]) == []
    assert harness["seam"].calls_for(member["id"]) == []
    leader_call = harness["seam"].calls_for(leader["id"])[-1]
    assert leader_call["target_id"] == task["target_id"]
    assert leader_call["team"] is not None

    task_outcomes = _db(client).list_round_outcomes(task["target_id"])
    assert [row["member_id"] for row in task_outcomes] == [leader["id"], leader["id"]]
    assert [row["member_id"] for row in outcomes(client, channel["id"])] == []

    thread = client.get(f"/api/tasks/{task['id']}/messages").json()["messages"]
    # 线程里的 agent 正文只有队长自己的两条（建任务那一轮 + 人类消息那一轮）；成员与外援都不落消息。
    assert [row["author"]["id"] for row in thread if row["kind"] == "agent"] == [leader["id"]] * 2


def test_team_task_agent_message_from_a_member_wakes_the_leader(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "进展"}).json()["task"]
    target_id = task["target_id"]
    drain(harness)  # 建任务唤醒队长那一轮（#179 A）

    message = _db(client).append_message(
        target_id=target_id,
        body="我更新了进度",
        author_member_id=member["id"],
        kind="agent",
        mention_snapshot=[],
    )
    assert _orchestrator(client).notify(target_id, message["seq"]) == 1
    drain(harness)
    # 队长共两轮：建任务那一轮（#179 A）+ 成员消息这一轮。
    assert len(harness["seam"].calls_for(leader["id"])) == 2
    assert harness["seam"].calls_for(member["id"]) == []


def test_leader_agent_message_in_a_task_thread_wakes_nobody(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    channel = _make_team(client, leader["id"])
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "收口"}).json()["task"]
    drain(harness)  # 建任务唤醒队长那一轮（#179 A）

    message = _db(client).append_message(
        target_id=task["target_id"],
        body="队长自己的回复",
        author_member_id=leader["id"],
        kind="agent",
        mention_snapshot=[],
    )
    assert _orchestrator(client).notify(task["target_id"], message["seq"]) == 0
    assert harness["scheduler"].pending_count() == 0


def test_owner_reassignment_wakes_the_leader_not_the_new_assignee(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "改派"}).json()["task"]
    drain(harness)  # 建任务唤醒队长那一轮（#179 A）

    response = client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": member["id"]})
    assert response.status_code == 200
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["assignee_member_id"] == member["id"]
    assert len(harness["seam"].calls_for(leader["id"])) == 2
    assert harness["seam"].calls_for(member["id"]) == []


def test_agent_transfer_to_another_member_wakes_the_leader(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    source = create_agent(client, name="原受派者").json()["agent"]
    target = create_agent(client, name="新受派者").json()["agent"]
    channel = _make_team(client, leader["id"], source["id"], target["id"])
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "转派"}).json()["task"]
    drain(harness)  # 建任务唤醒队长那一轮（#179 A）

    # owner 先把任务指派给 source（唤醒队长），再模拟 source 的一次 Agent 转派。
    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": source["id"]}).status_code == 200
    )
    drain(harness)
    _, transfer_message = _db(client).update_task_assignee(
        task["id"], target["id"], actor_member_id=source["id"]
    )
    assert transfer_message is not None
    assert (
        _orchestrator(client).notify_task_reassignment(
            task["target_id"],
            transfer_message["seq"],
            target["id"],
            actor_member_id=source["id"],
        )
        == 1
    )
    drain(harness)

    assert len(harness["seam"].calls_for(leader["id"])) == 3  # 建任务 + owner 改派 + Agent 转派
    assert harness["seam"].calls_for(target["id"]) == []


def test_agent_transfer_by_the_leader_wakes_nobody(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    channel = _make_team(client, leader["id"], member["id"])
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "队长转派"}).json()["task"]

    assert (
        client.patch(f"/api/tasks/{task['id']}/assignee", json={"member_id": leader["id"]}).status_code == 200
    )
    drain(harness)
    harness["seam"].behaviors[leader["id"]] = ACTION_ONLY
    harness["seam"].task_actions[leader["id"]] = TransferTaskAction(task["id"], member["id"])

    client.post(f"/api/tasks/{task['id']}/messages", json={"body": "交给成员", "task_ref": task["id"]})
    drain(harness)

    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["assignee_member_id"] == member["id"]
    # actor 就是队长 ⇒ 转派本身不再唤醒（不自我唤醒）。
    assert harness["scheduler"].pending_count() == 0
    assert harness["seam"].calls_for(member["id"]) == []


def test_claiming_a_task_never_happens_in_a_team_channel(client: TestClient, harness: dict) -> None:
    leader = create_agent(client, name="队长").json()["agent"]
    member = create_agent(client, name="成员").json()["agent"]
    # 即使成员开着 auto_claim，team 模式的读路径也不认领（D16）。
    assert client.patch(f"/api/agents/{member['id']}", json={"auto_claim": True}).status_code == 200
    channel = _make_team(client, leader["id"], member["id"])
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "不认领"}).json()["task"]

    assert task["claimed_at"] is None
    assert task["status"] == "todo"
    # 待跑的是**队长**那一轮（建任务唤醒），不是候选成员的认领轮。
    assert harness["scheduler"].pending_count() == 1
    drain(harness)
    assert len(harness["seam"].calls_for(leader["id"])) == 1
    assert _orchestrator(client).claim_new_task(task["id"], channel["id"]) == 0
    assert client.get(f"/api/tasks/{task['id']}").json()["task"]["claimed_at"] is None
    assert harness["seam"].calls_for(member["id"]) == []


# ─── autonomous / DM 不变 ────────────────────────────────────────────────
def test_autonomous_channels_still_wake_every_member(client: TestClient, harness: dict) -> None:
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    channel = create_channel(client, member_ids=[first["id"], second["id"]]).json()["channel"]
    assert _config(client, channel["id"])["mode"] == "autonomous"

    send(client, channel["id"], "大家好")
    assert harness["scheduler"].pending_count() == 2
    drain(harness)

    assert {row["member_id"] for row in outcomes(client, channel["id"])} == {
        first["id"],
        second["id"],
    }
    assert harness["seam"].calls_for(first["id"])
    assert harness["seam"].calls_for(second["id"])
    assert all(call["team"] is None for call in harness["seam"].calls)


def test_dms_are_always_autonomous(client: TestClient, harness: dict) -> None:
    agent = create_agent(client, name="私聊对象").json()["agent"]
    dm = client.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]

    assert client.post(f"/api/dms/{dm['id']}/messages", json={"body": "你好"}).status_code == 201
    assert harness["scheduler"].pending_count() == 1
    drain(harness)

    assert harness["seam"].calls_for(agent["id"])
    assert harness["seam"].calls_for(agent["id"])[0]["team"] is None


# ─── D10 / D11：pipeline 装配本身 ────────────────────────────────────────
class _StubModel(ChatModelBase):
    """不联网的模型：只为让 ``Agent`` 能构造出来（本组用例不跑推理）。"""

    def __init__(self) -> None:
        super().__init__(
            credential=None,  # type: ignore[arg-type]
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()

    async def _call_api(self, *args: Any, **kwargs: Any):
        raise AssertionError("本用例不跑推理")
        yield  # pragma: no cover


class _StubModelSeam(AgentScopeAgentSeam):
    """真实适配器 + 进程内脚本模型：被测的是 team 轮次的装配与事件消费。"""

    def __init__(self) -> None:
        super().__init__(timeout_seconds=10.0)
        self.models: list[ChatModelBase] = []

    def _build_model(self, provider: str, model_name: str, stream: bool = False) -> Any:
        self.models.append(_StubModel())
        return self.models[-1]


def _stub_agent(name: str):
    from agentscope.agent import Agent

    return Agent(name=name, system_prompt="测试", model=_StubModel())


def test_pipeline_names_are_derived_from_member_ids_not_display_names() -> None:
    duplicate = {"display_name": "同名", "role": "成员"}
    members = [
        ({**duplicate, "id": "mem_a"}, _stub_agent(member_internal_name("mem_a"))),
        ({**duplicate, "id": "mem_b"}, _stub_agent(member_internal_name("mem_b"))),
    ]

    pipeline = build_team_pipeline(
        leader_agent=_stub_agent(member_internal_name("mem_leader")),
        leader_id="mem_leader",
        members=members,
        target_id="ch_142",
    )

    assert pipeline.name_to_member == {
        "m-mem_leader": "mem_leader",
        "m-mem_a": "mem_a",
        "m-mem_b": "mem_b",
    }
    # 构造没有因 display_name 重名而失败；派生名也与队长名不同。
    assert len(set(pipeline.members)) == 2
    assert member_internal_name("mem_leader") not in pipeline.members


class _ReplyModel(_StubModel):
    """每次调用只回一句正文。"""

    async def _call_api(self, *args: Any, **kwargs: Any):
        async def stream():
            yield ChatResponse(content=[TextBlock(text="队长直接回答")], is_last=True)

        return stream()


def test_real_seam_runs_the_leader_round_through_the_team_pipeline() -> None:
    """没有 TeamAssign 时 pipeline 仍返回队长的最终回复（成员不运行、不上报成员轮次）。"""

    seam = _StubModelSeam()
    seam._build_model = lambda provider, model_name, stream=False: _ReplyModel()  # type: ignore[method-assign]

    leader = {
        "id": "mem_leader",
        "display_name": "队长",
        "role": "协调",
        "model_id": LOCAL_MODEL_ID,
        "system_prompt": "你是队长。",
        "enabled": True,
    }
    members = tuple(
        {
            "id": member_id,
            "display_name": "同名",
            "role": "成员",
            "model_id": LOCAL_MODEL_ID,
            "system_prompt": "你是成员。",
            "enabled": True,
        }
        for member_id in ("mem_a", "mem_b")
    )
    context = TeamRoundContext(target_id="ch_142", leader_id="mem_leader", members=members)
    records: list[Any] = []

    outcome = seam.run_round_stream(
        leader,
        [
            {
                "seq": 1,
                "kind": "human",
                "body": "开工",
                "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
            }
        ],
        mentioned=False,
        target_id="ch_142",
        team=context,
        emit=records.append,
    )

    assert outcome.status == "replied"
    assert [message.body for message in outcome.messages] == ["队长直接回答"]
    assert records and {record.member_id for record in records} == {"mem_leader"}
    assert records[0].type == "REPLY_START"


def test_duplicate_display_names_do_not_break_a_team_round() -> None:
    """两个成员显示名相同也构造/运行 pipeline（内部名来自 member id）。"""
    context = TeamRoundContext(
        target_id="ch_142",
        leader_id="mem_leader",
        members=tuple(
            {
                "id": member_id,
                "display_name": "同名",
                "role": "成员",
                "model_id": LOCAL_MODEL_ID,
                "system_prompt": "",
                "enabled": True,
            }
            for member_id in ("mem_a", "mem_b")
        ),
    )
    leader = {
        "id": "mem_leader",
        "display_name": "同名",
        "role": "队长",
        "model_id": LOCAL_MODEL_ID,
        "system_prompt": "",
        "enabled": True,
    }
    seam = _StubModelSeam()
    seam._build_model = lambda provider, model_name, stream=False: _ReplyModel()  # type: ignore[method-assign]

    outcome = seam.run_round_stream(
        leader,
        [
            {
                "seq": 1,
                "kind": "human",
                "body": "开工",
                "author": {"id": "owner", "kind": "owner", "display_name": "Owner"},
            }
        ],
        mentioned=False,
        target_id="ch_142",
        team=context,
    )

    assert outcome.status == "replied"
    assert [message.body for message in outcome.messages] == ["队长直接回答"]
