"""显式把 Agent 成员加入频道（issue #14）：幂等、校验、唤醒面与 ``channel_read`` 就绪。

本文件只经 HTTP 面与事件流断言**外部可观察行为**（与 `app/tests/conftest.py` 顶部的既有约定
一致）：不 import 实现内部模块、不连数据库、不读表结构。成员关系一律从 ``GET /channels/{id}/members``
与「它收到了哪些 seq」这两条外部事实读出来。

一处**已实现但不可达**的校验分支（不测，理由见验收文档）：`member_id` 已删除 → 校验失败。
首版没有删除成员的 HTTP 端点（``DELETE /api/agents/{id}`` 明确回 404），因此该分支在 HTTP 面
无法到达；按人类裁定不为此引入直连数据库的准备性夹具，以免破坏本仓「只走 HTTP 面」的硬约定。
代码里保留该判据（与 `create_channel` 的 `member_ids` 校验同口径），供将来加删除端点时直接生效。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import (
    SPEC_OWNER_ID,
    agents,
    backend,
    channels,
    create_agent,
    create_channel,
    drain,
    messages,
    send,
)
from tests.scripted import IGNORE, REPLY


def join(client: TestClient, channel_id: str, member_id: str):
    return client.post(f"/api/channels/{channel_id}/members", json={"member_id": member_id})


def member_ids(client: TestClient, channel_id: str) -> list[str]:
    payload = client.get(f"/api/channels/{channel_id}/members").json()
    return [member["id"] for member in payload["members"]]


def member_names(client: TestClient, channel_id: str) -> list[str]:
    payload = client.get(f"/api/channels/{channel_id}/members").json()
    return [member["display_name"] for member in payload["members"]]


def test_join_adds_the_agent_and_returns_the_fresh_member_table(client: TestClient) -> None:
    """新建的 Agent 不在任何频道里；经此端点加入后它出现在该频道的成员表里。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = create_channel(client, name="设计系统重构").json()["channel"]

    # 加入之前：它不在这个频道（这正是「新建 Agent 后 @ 不到它」的后端事实）。
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID]

    response = join(client, channel["id"], agent["id"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["joined"] is True
    assert payload["member"]["id"] == agent["id"]
    # 响应自带该频道最新成员表：前端一次调用即可刷新面板与 `@` 候选，不必「先加再读」。
    assert [member["id"] for member in payload["members"]] == [SPEC_OWNER_ID, agent["id"]]
    assert payload["channel"]["id"] == channel["id"]
    # 独立读一次，证明落库（而不是只回显请求里的东西）。
    assert member_names(client, channel["id"]) == ["Owner", "小明"]
    # 频道表本身没有多出一条频道，也没有别的频道被动过。
    assert list(channels(client)) == [channel["id"]]


def test_join_is_idempotent_and_the_payload_distinguishes_new_from_already_in(
    client: TestClient,
) -> None:
    """重复加入不报错、不产生重复行；`joined` 机械区分「新增」与「本来就在」。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = create_channel(client, name="频道").json()["channel"]

    first = join(client, channel["id"], agent["id"])
    second = join(client, channel["id"], agent["id"])

    assert first.status_code == second.status_code == 200
    assert first.json()["joined"] is True
    assert second.json()["joined"] is False
    # 不产生重复行：成员表里这个 agent 恰好出现一次（owner + 它 = 2 行）。
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID, agent["id"]]


def test_a_repeated_join_does_not_widen_the_wake_surface(client: TestClient, harness: dict) -> None:
    """已加入者重复调用：不重复计入唤醒面（一条提及只唤醒它一次）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors = {agent["id"]: IGNORE}
    channel = create_channel(client, name="频道").json()["channel"]
    first = join(client, channel["id"], agent["id"])
    # 先钉住加入确实发生了（否则下面「没多唤醒」也会在一个没加入的世界上恒真）。
    assert first.status_code == 200 and first.json()["joined"] is True
    join(client, channel["id"], agent["id"])
    join(client, channel["id"], agent["id"])
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID, agent["id"]]

    send(client, channel["id"], f"@{agent['display_name']} 看一下")
    drain(harness)

    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 1, "重复加入不得让它被唤醒多次"
    assert calls[0]["seqs"] == [1]


def test_missing_channel_is_404(client: TestClient) -> None:
    agent = create_agent(client, name="小明").json()["agent"]

    response = join(client, "ch_missing", agent["id"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_member_is_rejected_and_nothing_changes(client: TestClient) -> None:
    """成员不存在 → validation_failed，且整体不落库（成员表与消息流都没有半成品）。"""
    channel = create_channel(client, name="频道").json()["channel"]

    response = join(client, channel["id"], "mem_not_exist")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert "member_id" in body["error"]["fields"]
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID]
    assert messages(client, channel["id"]) == []


def test_owner_cannot_join_through_this_endpoint(client: TestClient) -> None:
    """`kind != 'agent'` → validation_failed，文案与 `create_channel` 的初始成员校验同口径。

    owner 已由建频道自动加入，因此它**本来就在**频道里；但这条校验的证据不是「它在不在」，
    而是响应仍然是校验失败（不是成功），且成员表没有被这次调用改写。
    """
    channel = create_channel(client, name="频道").json()["channel"]
    before = member_ids(client, channel["id"])

    response = join(client, channel["id"], SPEC_OWNER_ID)

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    # 与 `create_channel` 的「初始成员只能是 Agent」同一句话（同一口径，不另写一套措辞）。
    assert "只能是 Agent" in body["error"]["fields"]["member_id"]
    assert member_ids(client, channel["id"]) == before


def test_join_without_a_member_id_is_rejected(client: TestClient) -> None:
    channel = create_channel(client, name="频道").json()["channel"]

    response = join(client, channel["id"], "")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert "member_id" in response.json()["error"]["fields"]
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID]


def test_a_joined_agent_is_woken_through_the_channel_member_path(client: TestClient, harness: dict) -> None:
    """加入后向该频道发一条 `@它` 的消息：`mention_snapshot` 命中它，且它被唤醒并看到那条消息。

    「走频道成员路径而不是穿透」是可判别的：穿透回复会额外落一条 `system` 事件消息
    （`orchestration.py::_commit_reply`），加入后**没有**这条，成员表也不因此变化。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = create_channel(client, name="频道").json()["channel"]
    harness["seam"].behaviors = {agent["id"]: REPLY}
    harness["seam"].replies[agent["id"]] = "收到"
    join(client, channel["id"], agent["id"])

    posted = send(client, channel["id"], f"@{agent['display_name']} 看一下")
    snapshot = posted.json()["message"]["mention_snapshot"]
    assert [span["member_id"] for span in snapshot] == [agent["id"]]

    drain(harness)

    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 1
    assert calls[0]["seqs"] == [1]
    assert calls[0]["mentioned"] is True
    # 它确实回复了，而且回的是该频道（一次真实的回合）。
    stored = messages(client, channel["id"])
    assert [message["kind"] for message in stored] == ["human", "agent"]
    assert stored[-1]["body"] == "收到"
    # 穿透才有的 system 事件消息没有出现，成员表仍是 owner + 它。
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID, agent["id"]]


def test_joining_replaces_the_penetration_path_for_mentions(client: TestClient, harness: dict) -> None:
    """对照：未加入时点名靠穿透唤醒（且不加入）；加入后同一条提及走成员路径。

    这条同时是「本票没有改穿透语义」的证据：穿透那条路（`test_bounded_relay.py` 钉住的
    「被点名 ≠ 加入」）在加入之前逐字不变。
    """
    agent = create_agent(client, name="局外人").json()["agent"]
    insider = create_agent(client, name="甲").json()["agent"]
    channel = create_channel(client, name="频道", member_ids=[insider["id"]]).json()["channel"]
    harness["seam"].behaviors = {insider["id"]: IGNORE, agent["id"]: IGNORE}

    # 未加入：点名 → 穿透唤醒，但它**没有**因此加入。
    send(client, channel["id"], f"@{agent['display_name']} 看一下")
    drain(harness)
    assert len(harness["seam"].calls_for(agent["id"])) == 1
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID, insider["id"]]

    # 显式加入之后：同一条提及被频道成员表命中，它正常回复。
    join(client, channel["id"], agent["id"])
    harness["seam"].behaviors = {insider["id"]: IGNORE, agent["id"]: REPLY}
    harness["seam"].replies[agent["id"]] = "收到"
    send(client, channel["id"], f"@{agent['display_name']} 再看一下")
    drain(harness)

    assert len(harness["seam"].calls_for(agent["id"])) == 2
    assert harness["seam"].calls_for(agent["id"])[-1]["mentioned"] is True
    stored = messages(client, channel["id"])
    # 成员路径的回复不写 system 事件：两条人类消息 + 它的一条回复。
    assert [message["kind"] for message in stored] == ["human", "human", "agent"]
    assert stored[-1]["body"] == "收到"
    # 成员表按创建时间排序：owner 最先，然后两个 agent 按各自创建顺序。
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID, agent["id"], insider["id"]]


def test_the_channel_read_row_advances_while_history_remains_context(
    client: TestClient, harness: dict
) -> None:
    """加入时同步补 `channel_read`：它的消费游标是一条真实推进的位置，历史消息不**重复**投递。

    口径与 `db.add_channel_member` 的既有行为**逐字一致**（票面明写复用该写点、不改它的语义）：
    加入时游标从 0 起，因此加入后第一次唤醒会读到该频道既有的历史——与「穿透加入」同一个起点，
    不是本票引入的行为；关键在于它们**只被投递一次**：一轮收口后游标推进，
    下一次唤醒仅对新消息作回应判断，历史继续作为上下文。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    insider = create_agent(client, name="甲").json()["agent"]
    channel = create_channel(client, name="频道", member_ids=[insider["id"]]).json()["channel"]
    harness["seam"].behaviors = {insider["id"]: IGNORE}
    # 加入之前的两条历史消息。
    send(client, channel["id"], "加入前的第一条")
    send(client, channel["id"], "加入前的第二条")
    drain(harness)

    first_join = join(client, channel["id"], agent["id"])
    assert first_join.json()["joined"] is True
    assert agent["id"] in member_ids(client, channel["id"])
    harness["seam"].behaviors = {insider["id"]: IGNORE, agent["id"]: REPLY}
    harness["seam"].replies[agent["id"]] = "第一条回复"
    send(client, channel["id"], f"@{agent['display_name']} 看这条")
    drain(harness)

    first = harness["seam"].calls_for(agent["id"])
    assert len(first) == 1
    # 起点是 0（既有写点的语义）：历史与这条一起被读到，随后被标读推进。
    assert first[0]["seqs"] == [1, 2, 3]

    # 第二轮带历史与自己的回复，但消费基线仍只到新消息。
    harness["seam"].replies[agent["id"]] = "第二条回复"
    posted = send(client, channel["id"], f"@{agent['display_name']} 再看一条")
    newest = posted.json()["message"]["seq"]
    drain(harness)

    calls = harness["seam"].calls_for(agent["id"])
    assert len(calls) == 2
    assert calls[1]["seqs"] == list(range(1, newest + 1))
    records = client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
    assert [row for row in records if row["member_id"] == agent["id"]][-1]["base_seq"] == newest


def test_joining_leaves_no_message_behind(client: TestClient) -> None:
    """显式加入不写 system 事件消息（裁定 1）：消息流与 seq 完全不动，成员变化不进对话流。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = create_channel(client, name="频道").json()["channel"]
    send(client, channel["id"], "第一条")
    before = channels(client)[channel["id"]]

    join(client, channel["id"], agent["id"])
    join(client, channel["id"], agent["id"])
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID, agent["id"]]

    after = channels(client)[channel["id"]]
    assert after["last_seq"] == before["last_seq"] == 1
    assert [message["kind"] for message in messages(client, channel["id"])] == ["human"]


def test_membership_survives_a_restart(launched: dict) -> None:
    """成员关系的正本在数据目录里，不在内存：换一个进程读同一目录，成员表与频道表一致。"""
    client = launched["client"]
    agent = create_agent(client, name="小明").json()["agent"]
    channel = create_channel(client, name="频道").json()["channel"]
    join(client, channel["id"], agent["id"])
    before_channels = channels(client)
    before_members = member_ids(client, channel["id"])

    after_restart = launched["restart"]()

    assert member_ids(after_restart, channel["id"]) == before_members
    assert channels(after_restart)[channel["id"]] == before_channels[channel["id"]]
    # 重启后仍是「本来就在」：幂等的事实来源是持久化，不是上一个进程的记忆。
    assert join(after_restart, channel["id"], agent["id"]).json()["joined"] is False


def test_an_outsider_never_appears_in_the_members_table(client: TestClient) -> None:
    """**反向证据**：没加入过的 Agent 不出现在成员表里（证明断言真的在看成员关系）。"""
    inside = create_agent(client, name="在频道里").json()["agent"]
    outside = create_agent(client, name="不在频道里").json()["agent"]
    channel = create_channel(client, name="频道", member_ids=[inside["id"]]).json()["channel"]

    names = member_names(client, channel["id"])

    assert "在频道里" in names
    assert "不在频道里" not in names
    assert outside["id"] not in member_ids(client, channel["id"])
    # 它当然还在工作区的 Agent 名单里（左栏「本机有哪些 Agent」的口径不变）。
    assert outside["id"] in [agent["id"] for agent in agents(client)]
    # 反向证据成立的前提：同一个 Agent 经过此端点加入后**会**出现（否则「不在」是恒真的）。
    assert join(client, channel["id"], outside["id"]).json()["joined"] is True
    assert outside["id"] in member_ids(client, channel["id"])


def test_a_deleted_member_is_refused_by_a_later_backend(data_dir, tmp_path) -> None:
    """校验失败不落库的**跨进程**取证：被拒的加入在另一个后端读同一目录时也看不到。

    这条用「幻影成员 id」取证（HTTP 面唯一可达的校验失败形态）：拒绝发生在写入之前，
    因此数据目录里不可能留下 channel_member 行。
    """
    with backend(data_dir) as first:
        channel = first.post("/api/channels", json={"name": "频道", "member_ids": []}).json()["channel"]
        assert join(first, channel["id"], "mem_ghost").status_code == 400
        assert member_ids(first, channel["id"]) == [SPEC_OWNER_ID]

    with backend(data_dir) as second:
        assert member_ids(second, channel["id"]) == [SPEC_OWNER_ID]


def test_channel_members_cannot_be_removed_through_the_human_face(client: TestClient) -> None:
    """本票不做移出：人类面没有移出端点（明确 404，而不是沉默的 405）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = create_channel(client, name="频道").json()["channel"]
    join(client, channel["id"], agent["id"])

    removed = client.delete(f"/api/channels/{channel['id']}/members/{agent['id']}")

    assert removed.status_code == 404
    assert removed.json()["error"]["code"] == "not_found"
    assert member_ids(client, channel["id"]) == [SPEC_OWNER_ID, agent["id"]]
