"""每频道已读位置与未读计数：按频道单调 max、跨重启 / 跨实例保持。

未读口径（issue #2）：已读位置之后 ``kind ∈ {human, agent}`` 的顶层消息，不计 ``system``。
**本票只能产生 human 消息**（本票不创建 Agent、不写入 system 事件消息），因此本文件的断言
覆盖的是 human 消息下的计数与已读推进；``system`` 与 ``agent`` 消息的实际口径由 #② / #③
用真实消息覆盖，这里不做声明。

测试不连数据库、不读表结构、不注入消息：已读位置的持久化由「换一个后端读同一数据目录」
这条 HTTP 通路取证。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import backend, channels, create_channel, mark_read, messages, send


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


def test_history_read_before_the_read_position_is_never_unread_again(client: TestClient) -> None:
    """计数口径的可观察面：已读位置之后的 human 消息才算未读，位置之前的永不再算。"""
    channel = create_channel(client).json()["channel"]
    for index in range(1, 4):
        send(client, channel["id"], f"第 {index} 条")

    mark_read(client, channel["id"], 2)

    assert channels(client)[channel["id"]]["unread_count"] == 1
    assert channels(client)[channel["id"]]["last_read_seq"] == 2
    # 整个频道的历史仍然完整可读（已读位置只影响未读计数，不影响消息流）。
    assert [message["seq"] for message in messages(client, channel["id"])] == [1, 2, 3]


def test_read_position_is_persisted_outside_the_process(data_dir: Path) -> None:
    """已读位置的正本在数据目录里，不在内存：换一个后端进程读同一目录，结果一致。"""
    with backend(data_dir) as first:
        channel = create_channel(first, name="甲").json()["channel"]
        send(first, channel["id"], "甲一")
        send(first, channel["id"], "甲二")
        assert mark_read(first, channel["id"], 2).json()["channel"]["unread_count"] == 0

    with backend(data_dir) as second:
        listing = channels(second)
        assert listing[channel["id"]]["last_read_seq"] == 2
        assert listing[channel["id"]]["unread_count"] == 0
        # 第二个后端继续推进到更新的位置，随后第三个后端读回新值。
        send(second, channel["id"], "甲三")
        assert channels(second)[channel["id"]]["unread_count"] == 1
        mark_read(second, channel["id"], 3)

    with backend(data_dir) as third:
        assert channels(third)[channel["id"]]["last_read_seq"] == 3


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
