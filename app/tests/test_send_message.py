"""发送、持久化与不可变性：频道内单调 seq、append-only、重启后顺序一致。"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.conftest import channels, create_channel, raw_connection, send


def test_seq_is_monotonic_within_a_channel(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    seqs = [send(client, channel["id"], f"第 {index} 条").json()["message"]["seq"] for index in range(1, 5)]

    assert seqs == [1, 2, 3, 4]

    listed = client.get(f"/api/channels/{channel['id']}/messages").json()["messages"]
    assert [message["seq"] for message in listed] == seqs
    assert [message["body"] for message in listed] == [f"第 {index} 条" for index in range(1, 5)]


def test_seq_is_per_channel_not_global(client: TestClient) -> None:
    first = create_channel(client, name="甲").json()["channel"]
    second = create_channel(client, name="乙").json()["channel"]

    send(client, first["id"], "甲一")
    send(client, first["id"], "甲二")
    second_seq = send(client, second["id"], "乙一").json()["message"]["seq"]

    assert second_seq == 1


def test_client_cannot_forge_seq_or_author(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    forged_seq = client.post(f"/api/channels/{channel['id']}/messages", json={"body": "伪造", "seq": 99})
    forged_author = client.post(
        f"/api/channels/{channel['id']}/messages", json={"body": "伪造", "author_member_id": "someone"}
    )

    assert forged_seq.status_code == 400
    assert forged_author.status_code == 400
    assert client.get(f"/api/channels/{channel['id']}/messages").json()["messages"] == []


def test_target_seq_is_unique_at_the_storage_layer(client: TestClient, data_dir) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "唯一").json()["message"]

    with raw_connection(data_dir) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at) "
            "VALUES ('msg_dup', ?, ?, 'owner', 'human', '重复', '2026-01-01T00:00:00.000Z')",
            (channel["id"], message["seq"]),
        )


def test_messages_are_append_only(client: TestClient, data_dir) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "永久记录").json()["message"]
    path = f"/api/channels/{channel['id']}/messages/{message['seq']}"

    # HTTP 面没有编辑或删除消息的端点。
    assert client.get(path).status_code == 404
    assert client.put(path, json={"body": "改写"}).status_code == 404
    assert client.patch(path, json={"body": "改写"}).status_code == 404
    assert client.delete(path).status_code == 404

    # 数据层兜底：任何更新/删除消息的路径都被拒绝。
    with raw_connection(data_dir) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE message SET body = '改写' WHERE id = ?", (message["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM message WHERE id = ?", (message["id"],))

    listed = client.get(f"/api/channels/{channel['id']}/messages").json()["messages"]
    assert [item["body"] for item in listed] == ["永久记录"]


def test_empty_body_is_rejected_and_not_stored(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    response = send(client, channel["id"], "   \n  ")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert client.get(f"/api/channels/{channel['id']}/messages").json()["messages"] == []


def test_multiline_body_is_stored_verbatim(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    body = "第一行\n第二行\n\n第四行"

    send(client, channel["id"], body)

    listed = client.get(f"/api/channels/{channel['id']}/messages").json()["messages"]
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
