"""每频道已读位置与未读计数：按频道单调 max、跨重启 / 跨实例保持。

未读口径（**#4 的人类裁定，已修改 #2 的原口径**）：已读位置之后、
``kind ∈ {human, agent}``、**且作者不是本人**的顶层消息，不计 ``system``。
改前（#2）不排除本人作者，因此 owner 自己发的消息也计入自己的未读；#4 按票面字面改判为排除。

本票（#2）只产生 owner 自己的 human 消息，按新口径它们**永远不算未读**，所以本文件的
「计数」类断言一律用 **Agent 回复**（非 owner 作者）取证 —— 这与生产里唯一的非本人作者路径同形：
经 `@一个未加入该频道的 Agent` 触发穿透，它回复并顺带加入（同时留下一条不计入的 system 事件）。

测试不连数据库、不读表结构、不注入消息：已读位置的持久化由「换一个后端读同一数据目录」
这条 HTTP 通路取证。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import (
    backend,
    channels,
    create_agent,
    create_channel,
    drain,
    mark_read,
    messages,
    send,
)
from tests.scripted import IGNORE, REPLY


def make_repliers(
    client: TestClient, harness: dict, names: list[str], channel_name: str = "频道"
) -> tuple[dict, dict]:
    """建一个频道，里面是以**显式成员**加入的若干 Agent（默认都自判无关）。

    返回 ``(channel, agents)``。这样后面的每一步只会产生**恰好一条** Agent 消息：
    被点名那个回一句，其余沉默；它的回复又不把沉默者变成发言者。
    """
    agents = {name: create_agent(client, name=name).json()["agent"] for name in names}
    harness["seam"].behaviors = {agent["id"]: IGNORE for agent in agents.values()}
    channel = client.post(
        "/api/channels",
        json={
            "name": channel_name,
            "description": "",
            "member_ids": [agent["id"] for agent in agents.values()],
        },
    ).json()["channel"]
    return channel, agents


def ask(client: TestClient, harness: dict, channel: dict, agents: dict, name: str, body: str) -> int:
    """让 ``agents[name]`` 回复一句，返回它落库后的 seq。

    每步都把其余成员重置回「自判无关」：人类消息唤醒**全部**成员，若不重置，上一轮的回复行为
    会让其它成员也跟着发言，一段里就不只产生一条 Agent 消息了（那也正是本票在验的接力形态，
    但这里要的是**可控的单条**）。
    """
    agent = agents[name]
    harness["seam"].behaviors = {
        member["id"]: (REPLY if member["id"] == agent["id"] else IGNORE) for member in agents.values()
    }
    harness["seam"].replies[agent["id"]] = body
    send(client, channel["id"], f"@{agent['display_name']} 回一句")
    drain(harness)
    return channels(client)[channel["id"]]["last_seq"]


def test_new_channel_has_no_unread(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    assert channel["last_read_seq"] == 0
    assert channel["unread_count"] == 0


def test_owner_authored_messages_do_not_count_toward_own_unread(client: TestClient, harness: dict) -> None:
    """#4 的裁定：owner 自己发的消息不算自己的未读（改前 #2 会算）。"""
    channel, agents = make_repliers(client, harness, ["甲"])
    for index in range(1, 4):
        send(client, channel["id"], f"第 {index} 条")

    assert channels(client)[channel["id"]]["unread_count"] == 0
    assert client.get("/api/workspace").json()["unread_total"] == 0
    # 消息本身照常落库（未读口径不影响消息流）。
    assert len(messages(client, channel["id"])) == 3
    # 对照：同一个频道里别人（Agent）的一条回复则**会**计入。
    ask(client, harness, channel, agents, "甲", "甲的回复")
    assert channels(client)[channel["id"]]["unread_count"] == 1


def test_other_channels_accumulate_unread(client: TestClient, harness: dict) -> None:
    first, first_agents = make_repliers(client, harness, ["甲"], channel_name="甲频道")
    second, second_agents = make_repliers(client, harness, ["乙"], channel_name="乙频道")

    ask(client, harness, first, first_agents, "甲", "甲的回复")
    ask(client, harness, second, second_agents, "乙", "乙的回复")

    listing = channels(client)
    # 每个频道各一条别人（Agent）的回复；owner 自己那条不计入。
    assert listing[first["id"]]["unread_count"] == 1
    assert listing[second["id"]]["unread_count"] == 1
    assert client.get("/api/workspace").json()["unread_total"] == 2


def test_mark_read_clears_only_that_channel(client: TestClient, harness: dict) -> None:
    first, first_agents = make_repliers(client, harness, ["甲"], channel_name="甲频道")
    second, second_agents = make_repliers(client, harness, ["乙", "丙"], channel_name="乙频道")
    first_seq = ask(client, harness, first, first_agents, "甲", "甲一")
    ask(client, harness, second, second_agents, "乙", "乙一")
    ask(client, harness, second, second_agents, "丙", "乙二")
    assert channels(client)[second["id"]]["unread_count"] == 2

    payload = mark_read(client, first["id"], first_seq).json()

    assert payload["channel"]["unread_count"] == 0
    listing = {channel["id"]: channel for channel in payload["channels"]}
    # 推进当前频道的已读位置不改变其它频道。
    assert listing[second["id"]]["last_read_seq"] == 0
    assert listing[second["id"]]["unread_count"] == 2


def test_read_position_is_monotonic_and_never_rewinds(client: TestClient, harness: dict) -> None:
    channel, agents = make_repliers(client, harness, ["甲", "乙", "丙"])
    latest = 0
    for name in ("甲", "乙", "丙"):
        latest = ask(client, harness, channel, agents, name, f"{name} 的回复")
    assert channels(client)[channel["id"]]["unread_count"] == 3

    mark_read(client, channel["id"], latest)
    rewound = mark_read(client, channel["id"], 1).json()["channel"]

    # 同一频道内取 max：回退请求被忽略。
    assert rewound["last_read_seq"] == latest
    assert rewound["unread_count"] == 0


def test_mark_read_is_clamped_to_current_max_seq(client: TestClient, harness: dict) -> None:
    """把未来消息提前标为已读不可能发生：已读位置被裁剪到当前最大 seq。"""
    channel, agents = make_repliers(client, harness, ["甲", "乙"])
    current = ask(client, harness, channel, agents, "甲", "第一条回复")

    oversized = mark_read(client, channel["id"], 99).json()["channel"]
    assert oversized["last_read_seq"] == current

    # 随后到达的消息仍然算未读。
    ask(client, harness, channel, agents, "乙", "第二条回复")
    assert channels(client)[channel["id"]]["unread_count"] == 1


def test_history_read_before_the_read_position_is_never_unread_again(
    client: TestClient, harness: dict
) -> None:
    """计数口径的可观察面：已读位置之后的（非本人）消息才算未读，位置之前的永不再算。"""
    channel, agents = make_repliers(client, harness, ["甲", "乙"])
    boundary = ask(client, harness, channel, agents, "甲", "第一条回复")
    ask(client, harness, channel, agents, "乙", "第二条回复")

    mark_read(client, channel["id"], boundary)

    assert channels(client)[channel["id"]]["unread_count"] == 1
    assert channels(client)[channel["id"]]["last_read_seq"] == boundary
    # 整个频道的历史仍然完整可读（已读位置只影响未读计数，不影响消息流）。
    last_seq = channels(client)[channel["id"]]["last_seq"]
    assert [message["seq"] for message in messages(client, channel["id"])] == list(range(1, last_seq + 1))


def test_read_position_is_persisted_outside_the_process(data_dir: Path, harness: dict) -> None:
    """已读位置的正本在数据目录里，不在内存：换一个后端进程读同一目录，结果一致。"""
    client = harness["client"]
    channel, agents = make_repliers(client, harness, ["甲", "乙"])
    ask(client, harness, channel, agents, "甲", "甲一")
    target = ask(client, harness, channel, agents, "乙", "甲二")
    assert channels(client)[channel["id"]]["unread_count"] == 2
    assert mark_read(client, channel["id"], target).json()["channel"]["unread_count"] == 0

    # 换一个进程读同一数据目录：位置、计数、消息流都一致（只可能来自磁盘）。
    with backend(data_dir) as second:
        listing = channels(second)
        assert listing[channel["id"]]["last_read_seq"] == target
        assert listing[channel["id"]]["unread_count"] == 0
        assert second.get("/api/workspace").json()["unread_total"] == 0
        assert len(messages(second, channel["id"])) == len(messages(client, channel["id"]))


def test_unread_survives_browser_and_backend_restart(launched: dict) -> None:
    client = launched["client"]
    first, first_agents = make_repliers(client, launched, ["甲"], channel_name="甲频道")
    second, second_agents = make_repliers(client, launched, ["乙"], channel_name="乙频道")
    first_target = ask(client, launched, first, first_agents, "甲", "甲一")
    ask(client, launched, second, second_agents, "乙", "乙一")
    mark_read(client, first["id"], first_target)

    after_restart = launched["restart"]()

    listing = channels(after_restart)
    assert listing[first["id"]]["last_read_seq"] == first_target
    assert listing[first["id"]]["unread_count"] == 0
    assert listing[second["id"]]["last_read_seq"] == 0
    assert listing[second["id"]]["unread_count"] == 1
    assert after_restart.get("/api/workspace").json()["unread_total"] == 1


def test_mark_read_requires_non_negative_integer(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    assert client.post(f"/api/channels/{channel['id']}/read", json={"seq": -1}).status_code == 400
    assert client.post(f"/api/channels/{channel['id']}/read", json={"seq": "3"}).status_code == 400


def test_unread_total_sums_channels_in_workspace_snapshot(client: TestClient, harness: dict) -> None:
    first, first_agents = make_repliers(client, harness, ["甲"], channel_name="甲频道")
    second, second_agents = make_repliers(client, harness, ["乙"], channel_name="乙频道")
    ask(client, harness, first, first_agents, "甲", "甲一")
    ask(client, harness, second, second_agents, "乙", "乙一")

    assert client.get("/api/workspace").json()["unread_total"] == 2


def test_unread_readings_across_endpoints_share_one_rule(client: TestClient, harness: dict) -> None:
    """列表、单条频道与 workspace 快照三处读数必须是同一条口径（单一落点）。"""
    channel, agents = make_repliers(client, harness, ["甲"])
    ask(client, harness, channel, agents, "甲", "甲一")

    listed = channels(client)[channel["id"]]["unread_count"]
    single = client.get(f"/api/channels/{channel['id']}/messages").json()["channel"]["unread_count"]
    total = client.get("/api/workspace").json()["unread_total"]
    assert listed == single == total == 1
