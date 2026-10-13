"""每频道已读位置与未读计数：按频道单调 max、口径只含 human/agent、跨重启保持。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import channels, create_channel, mark_read, raw_connection, send


def test_new_channel_has_no_unread(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    assert channel["last_read_seq"] == 0
    assert channel["unread_count"] == 0


def test_other_channels_accumulate_unread(client: TestClient) -> None:
    first = create_channel(client, name="甲").json()["channel"]
    second = create_channel(client, name="乙").json()["channel"]

    send(client, first["id"], "甲的第一条")
    send(client, first["id"], "甲的第二条")
    send(client, second["id"], "乙的第一条")

    listing = channels(client)
    assert listing[first["id"]]["unread_count"] == 2
    assert listing[second["id"]]["unread_count"] == 1


def test_mark_read_clears_only_that_channel(client: TestClient) -> None:
    first = create_channel(client, name="甲").json()["channel"]
    second = create_channel(client, name="乙").json()["channel"]
    send(client, first["id"], "甲一")
    send(client, second["id"], "乙一")
    send(client, second["id"], "乙二")

    payload = mark_read(client, first["id"], 1).json()

    assert payload["channel"]["last_read_seq"] == 1
    assert payload["channel"]["unread_count"] == 0
    listing = {channel["id"]: channel for channel in payload["channels"]}
    # 推进当前频道的已读位置不改变其它频道。
    assert listing[second["id"]]["last_read_seq"] == 0
    assert listing[second["id"]]["unread_count"] == 2


def test_read_position_is_monotonic_and_never_rewinds(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    for index in range(1, 4):
        send(client, channel["id"], f"第 {index} 条")

    mark_read(client, channel["id"], 3)
    rewound = mark_read(client, channel["id"], 1).json()["channel"]

    # 同一频道内取 max：回退请求被忽略。
    assert rewound["last_read_seq"] == 3
    assert rewound["unread_count"] == 0


def test_mark_read_is_clamped_to_current_max_seq(client: TestClient) -> None:
    """把未来消息提前标为已读不可能发生：已读位置被裁剪到当前最大 seq。"""
    channel = create_channel(client).json()["channel"]
    send(client, channel["id"], "第一条")

    oversized = mark_read(client, channel["id"], 99).json()["channel"]
    assert oversized["last_read_seq"] == 1

    # 随后到达的消息仍然算未读。
    send(client, channel["id"], "第二条")
    assert channels(client)[channel["id"]]["unread_count"] == 1


def test_unread_counts_only_human_and_agent_top_level_messages(client: TestClient, data_dir) -> None:
    """未读口径：已读位置之后的 human/agent 顶层消息；不计 system 事件消息。"""
    channel = create_channel(client).json()["channel"]
    send(client, channel["id"], "人一")
    mark_read(client, channel["id"], 1)

    # system 事件消息（#② / #③ 才会产生）不计入未读。
    with raw_connection(data_dir) as conn:
        conn.execute(
            "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at) "
            "VALUES ('msg_sys', ?, 2, 'owner', 'system', '某 Agent 加入频道', '2026-01-01T00:00:00.000Z')",
            (channel["id"],),
        )

    assert channels(client)[channel["id"]]["unread_count"] == 0


def test_unread_survives_browser_and_backend_restart(launched: dict) -> None:
    client = launched["client"]
    first = create_channel(client, name="甲").json()["channel"]
    second = create_channel(client, name="乙").json()["channel"]
    send(client, first["id"], "甲一")
    send(client, second["id"], "乙一")
    mark_read(client, first["id"], 1)

    after_restart = launched["restart"]()

    listing = channels(after_restart)
    assert listing[first["id"]]["last_read_seq"] == 1
    assert listing[first["id"]]["unread_count"] == 0
    assert listing[second["id"]]["last_read_seq"] == 0
    assert listing[second["id"]]["unread_count"] == 1
    assert after_restart.get("/api/workspace").json()["unread_total"] == 1


def test_channel_read_row_exists_per_channel(client: TestClient, data_dir) -> None:
    channel = create_channel(client).json()["channel"]
    send(client, channel["id"], "一条")
    mark_read(client, channel["id"], 1)

    with raw_connection(data_dir) as conn:
        rows = conn.execute("SELECT member_id, channel_id, last_read_seq FROM channel_read").fetchall()

    assert rows == [("owner", channel["id"], 1)]


def test_mark_read_requires_non_negative_integer(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    assert client.post(f"/api/channels/{channel['id']}/read", json={"seq": -1}).status_code == 400
    assert client.post(f"/api/channels/{channel['id']}/read", json={"seq": "3"}).status_code == 400


def test_unread_total_sums_channels_in_workspace_snapshot(client: TestClient) -> None:
    first = create_channel(client, name="甲").json()["channel"]
    second = create_channel(client, name="乙").json()["channel"]
    send(client, first["id"], "甲一")
    send(client, second["id"], "乙一")

    assert client.get("/api/workspace").json()["unread_total"] == 2
