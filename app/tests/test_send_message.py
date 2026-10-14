"""发送、持久化与不可变性：频道内单调 seq、append-only、重启后顺序一致。

本文件只经 HTTP 面取证：并发请求拿到的 seq、被拒请求后的续号、重启后的历史与顺序、
人类面没有编辑/删除端点。测试不连数据库、不读表结构、不注入任何消息。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import backend, channels, create_channel, messages, peer_backends, send


def test_seq_is_monotonic_within_a_channel(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    seqs = [send(client, channel["id"], f"第 {index} 条").json()["message"]["seq"] for index in range(1, 5)]

    assert seqs == [1, 2, 3, 4]

    listed = messages(client, channel["id"])
    assert [message["seq"] for message in listed] == seqs
    assert [message["body"] for message in listed] == [f"第 {index} 条" for index in range(1, 5)]


def test_seq_is_per_channel_not_global(client: TestClient) -> None:
    first = create_channel(client, name="甲").json()["channel"]
    second = create_channel(client, name="乙").json()["channel"]

    send(client, first["id"], "甲一")
    send(client, first["id"], "甲二")
    second_seq = send(client, second["id"], "乙一").json()["message"]["seq"]

    assert second_seq == 1


def test_concurrent_sends_never_share_or_skip_a_seq(data_dir: Path) -> None:
    """并发写入下频道内 seq 唯一且无缺口 —— 只从并发请求的返回值与列表观察。

    同一数据目录上同时开 8 个后端，8 条消息并发提交。seq 的唯一性与连续性由
    ``UNIQUE(target_id, seq)`` 保证（实现事实，见 ``alibot/db.py`` 模块文档），
    测试只断言外部可见的结果：8 条各自拿到不同的 seq，合起来恰好是 1…8。
    """
    with peer_backends(data_dir, count=8) as peers:
        channel = create_channel(peers[0]).json()["channel"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(
                pool.map(lambda index: send(peers[index], channel["id"], f"并发第 {index} 条"), range(8))
            )

        assert [response.status_code for response in responses] == [201] * 8
        returned = sorted(response.json()["message"]["seq"] for response in responses)
        assert returned == list(range(1, 9))

        listed = messages(peers[0], channel["id"])
        assert [message["seq"] for message in listed] == list(range(1, 9))
        assert len({message["id"] for message in listed}) == 8
        assert sorted(message["body"] for message in listed) == [f"并发第 {index} 条" for index in range(8)]


def test_client_cannot_forge_seq_or_author(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    forged_seq = client.post(f"/api/channels/{channel['id']}/messages", json={"body": "伪造", "seq": 99})
    forged_author = client.post(
        f"/api/channels/{channel['id']}/messages", json={"body": "伪造", "author_member_id": "someone"}
    )

    assert forged_seq.status_code == 400
    assert forged_author.status_code == 400
    assert messages(client, channel["id"]) == []


def test_rejected_message_does_not_consume_a_seq(client: TestClient) -> None:
    """原子性：被拒的发送不落库、也不占号 —— 后续消息的 seq 无缺口。"""
    channel = create_channel(client).json()["channel"]
    assert send(client, channel["id"], "第一条").json()["message"]["seq"] == 1

    rejected = send(client, channel["id"], "长" * 8001)

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "validation_failed"
    assert send(client, channel["id"], "第三条").json()["message"]["seq"] == 2
    assert [message["body"] for message in messages(client, channel["id"])] == ["第一条", "第三条"]


def test_messages_are_append_only(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "永久记录").json()["message"]
    path = f"/api/channels/{channel['id']}/messages/{message['seq']}"

    # 人类 HTTP 面没有读取单条、编辑或删除消息的端点（消息 append-only）。
    assert client.get(path).status_code == 404
    assert client.put(path, json={"body": "改写"}).status_code == 404
    assert client.patch(path, json={"body": "改写"}).status_code == 404
    assert client.delete(path).status_code == 404

    # 走完这一圈之后，频道里那条消息逐字未变。
    assert [item["body"] for item in messages(client, channel["id"])] == ["永久记录"]


def test_empty_body_is_rejected_and_not_stored(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    response = send(client, channel["id"], "   \n  ")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert messages(client, channel["id"]) == []


def test_multiline_body_is_stored_verbatim(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    body = "第一行\n第二行\n\n第四行"

    send(client, channel["id"], body)

    listed = messages(client, channel["id"])
    assert listed[0]["body"] == body


def test_history_survives_backend_restart(launched: dict) -> None:
    client = launched["client"]
    channel = create_channel(client).json()["channel"]
    for index in range(1, 4):
        send(client, channel["id"], f"重启前的第 {index} 条")
    before = client.get(f"/api/channels/{channel['id']}/messages").json()

    after_restart = launched["restart"]()

    after = after_restart.get(f"/api/channels/{channel['id']}/messages").json()
    assert after["messages"] == before["messages"]
    assert [message["seq"] for message in after["messages"]] == [1, 2, 3]
    assert channels(after_restart)[channel["id"]]["last_seq"] == 3

    # 重启后继续发送，seq 继续向前。
    assert send(after_restart, channel["id"], "重启后的第一条").json()["message"]["seq"] == 4


def test_history_survives_a_second_backend_on_the_same_data_dir(data_dir: Path) -> None:
    """历史只在数据目录里：换一个后端进程读同一目录，看到的是同一条流。"""
    with backend(data_dir) as first:
        channel = create_channel(first).json()["channel"]
        for index in range(1, 4):
            send(first, channel["id"], f"第 {index} 条")

    with backend(data_dir) as second:
        listed = messages(second, channel["id"])
        assert [message["seq"] for message in listed] == [1, 2, 3]
        assert [message["body"] for message in listed] == [f"第 {index} 条" for index in range(1, 4)]


def test_after_seq_cursor_returns_only_newer_messages(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    for index in range(1, 4):
        send(client, channel["id"], f"第 {index} 条")

    payload = client.get(f"/api/channels/{channel['id']}/messages", params={"after_seq": 1}).json()

    assert [message["seq"] for message in payload["messages"]] == [2, 3]


def test_workspace_snapshot_reports_max_seq(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    send(client, channel["id"], "只有一条")

    snapshot = client.get("/api/workspace").json()

    assert snapshot["last_seq"] == 1
