"""从空数据启动：空工作台 = 零频道 / 零 Agent / 零消息，仅 owner 一条成员行。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from worksplice.db import DATABASE_FILE_NAME, OWNER_ID

from tests.conftest import channels


def test_fresh_data_dir_is_empty_workspace(client: TestClient) -> None:
    payload = client.get("/api/workspace").json()

    assert payload["channels"] == []
    assert payload["unread_total"] == 0
    assert payload["last_seq"] == 0

    # owner 是规格固定的唯一成员行，不是预置业务数据。
    assert [member["id"] for member in payload["members"]] == [OWNER_ID]
    assert payload["member"]["kind"] == "owner"


def test_no_business_seed_rows_anywhere(client: TestClient) -> None:
    assert channels(client) == {}
    assert client.get("/api/health").json()["status"] == "ok"

    # 空库里除了 owner 成员行，没有任何 Agent、频道或消息。
    assert client.get("/api/channels").json() == {"channels": []}


def test_data_dir_is_created_in_the_given_location(client: TestClient, data_dir) -> None:
    assert (data_dir / DATABASE_FILE_NAME).exists()
    assert client.get("/api/health").json()["data_dir"] == str(data_dir)


def test_workspace_bootstrap_owner_display_fields(client: TestClient) -> None:
    member = client.get("/api/workspace").json()["member"]

    assert member["id"] == OWNER_ID
    assert member["display_name"]
    assert 0 <= member["avatar_slot"] <= 4
    assert member["created_at"].endswith("Z")
