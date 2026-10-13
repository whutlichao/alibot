"""从空数据启动：空工作台 = 零频道 / 零 Agent / 零消息，仅 owner 一条成员行。

测试只经 HTTP 面断言工作台状态；数据目录本身只按「用户能直接找到并复制」这条对外契约
取证（地址由 ``/api/health`` 给出，备份 = 复制目录后在新地址上重开后端）。测试不连数据库、
不读表结构、不注入任何消息。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import SPEC_OWNER_ID, backend, channels, create_channel, send


def test_fresh_data_dir_is_empty_workspace(client: TestClient) -> None:
    payload = client.get("/api/workspace").json()

    assert payload["channels"] == []
    assert payload["unread_total"] == 0
    assert payload["last_seq"] == 0

    # owner 是规格固定的唯一成员行，不是预置业务数据。
    assert [member["id"] for member in payload["members"]] == [SPEC_OWNER_ID]
    assert payload["member"]["kind"] == "owner"


def test_no_business_seed_rows_anywhere(client: TestClient) -> None:
    assert channels(client) == {}
    assert client.get("/api/health").json()["status"] == "ok"

    # 空工作台里除了 owner 成员行，没有任何 Agent、频道或消息。
    assert client.get("/api/channels").json() == {"channels": []}


def test_data_dir_is_created_in_the_given_location(client: TestClient, data_dir: Path) -> None:
    assert data_dir.is_dir()
    assert client.get("/api/health").json()["data_dir"] == str(data_dir)


def test_backup_is_a_directory_copy(data_dir: Path, tmp_path: Path) -> None:
    """备份 = 复制数据目录：复制出的目录在新地址上重开后端，内容逐字一致。"""
    with backend(data_dir) as original:
        channel = create_channel(original, name="备份验证").json()["channel"]
        for index in range(1, 3):
            send(original, channel["id"], f"第 {index} 条")

    backup = tmp_path / "backup"
    shutil.copytree(data_dir, backup)

    with backend(backup) as restored:
        assert restored.get("/api/health").json()["data_dir"] == str(backup)
        listing = channels(restored)
        assert listing[channel["id"]]["name"] == "备份验证"
        assert listing[channel["id"]]["last_seq"] == 2
        listed = restored.get(f"/api/channels/{channel['id']}/messages").json()["messages"]
        assert [message["body"] for message in listed] == ["第 1 条", "第 2 条"]


def test_workspace_bootstrap_owner_display_fields(client: TestClient) -> None:
    member = client.get("/api/workspace").json()["member"]

    assert member["id"] == SPEC_OWNER_ID
    assert member["display_name"]
    assert 0 <= member["avatar_slot"] <= 4
    assert member["created_at"].endswith("Z")
