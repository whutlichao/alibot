"""新建频道：名称 + 描述 + 初始成员，校验失败整体不创建。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from worksplice.db import OWNER_ID

from tests.conftest import channels, create_channel, raw_connection, send


def test_create_channel_and_send_first_message_without_restart(client: TestClient) -> None:
    created = create_channel(client, name="移动端体验", description="首版闭环")
    assert created.status_code == 201

    channel = created.json()["channel"]
    assert channel["name"] == "移动端体验"
    assert channel["description"] == "首版闭环"
    assert channel["last_seq"] == 0
    assert channel["unread_count"] == 0

    # 创建完成后立刻能发第一条消息：不重启、不刷新。
    posted = send(client, channel["id"], "第一条消息")
    assert posted.status_code == 201
    assert posted.json()["message"]["seq"] == 1

    listed = client.get(f"/api/channels/{channel['id']}/messages").json()["messages"]
    assert [message["body"] for message in listed] == ["第一条消息"]


def test_created_channel_joins_the_creator_and_starts_empty(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]

    members = client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    assert [member["id"] for member in members] == [OWNER_ID]
    assert members[0]["kind"] == "owner"

    # 空频道没有任何消息，但 channel_read 行已随创建存在（已读位置从 0 起）。
    assert client.get(f"/api/channels/{channel['id']}/messages").json()["messages"] == []
    assert channels(client)[channel["id"]]["last_read_seq"] == 0


def test_empty_name_is_rejected_and_leaves_no_half_written_row(client: TestClient, data_dir) -> None:
    response = create_channel(client, name="   ", description="不该留下")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert "name" in body["error"]["fields"]

    # 整体不创建：库里的频道、频道成员、已读位置都没有半成品行。
    assert client.get("/api/channels").json() == {"channels": []}
    with raw_connection(data_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM channel_member").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM channel_read").fetchone()[0] == 0


def test_too_long_name_is_rejected_without_creating(client: TestClient) -> None:
    response = create_channel(client, name="频" * 200)

    assert response.status_code == 400
    assert "name" in response.json()["error"]["fields"]
    assert client.get("/api/channels").json() == {"channels": []}


def test_unknown_initial_member_is_rejected_without_creating(client: TestClient) -> None:
    response = create_channel(client, name="带陌生人", member_ids=["agent_not_exist"])

    assert response.status_code == 400
    assert "member_ids" in response.json()["error"]["fields"]
    assert client.get("/api/channels").json() == {"channels": []}


def test_owner_cannot_be_listed_as_initial_member(client: TestClient) -> None:
    response = create_channel(client, name="把 owner 当成员", member_ids=[OWNER_ID])

    assert response.status_code == 400
    assert client.get("/api/channels").json() == {"channels": []}


def test_missing_channel_is_404_for_reads_and_writes(client: TestClient) -> None:
    assert client.get("/api/channels/ch_missing/messages").status_code == 404
    assert send(client, "ch_missing", "无主消息").status_code == 404
    assert client.post("/api/channels/ch_missing/read", json={"seq": 1}).status_code == 404


def test_channels_are_listed_in_creation_order_with_unread(client: TestClient) -> None:
    first = create_channel(client, name="甲").json()["channel"]
    second = create_channel(client, name="乙").json()["channel"]

    listing = client.get("/api/channels").json()["channels"]
    assert [channel["id"] for channel in listing] == [first["id"], second["id"]]
    assert [channel["unread_count"] for channel in listing] == [0, 0]
