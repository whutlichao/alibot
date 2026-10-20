"""`web_access` 的**读写面**：`PATCH /api/channels/{id}/config` 与 `PATCH /api/agents/{id}`（#184）。

判据来源：ADR-0026 §二（2026-10-20 修订：未配置 = 默认 `search`，`off` = 显式关闭；频道给默认值，
Agent 可覆盖，`NULL` = 跟随频道）、
ADR-0011（Agent 配置的唯一写路径，任一字段不合法则**整体不写**）、ADR-0021 §六（未配置时行为逐字不变）。

接缝 = 既有 HTTP 黑盒（``create_app`` + 手动调度器 + 脚本化替身），与前后的 ``test_team_mode.py`` /
``test_agent_detail.py`` 同一套；不新开测试缝、不读表结构。断言只落在对外载荷与**读回的原值**：

- 「不部分写入」的取证方式就是票面给的那一条——请求失败后**读回原值**（不是查库）；
- `NULL` 的语义：频道侧显式 ``null`` = **未配置**（读路径回退默认档，照 ``workspace_root``
  先例），Agent 侧显式 ``null`` 一律 **400**（照该端点的既有纪律）。

DM 的联网档读数（#202）是装配层的**内部**配置读数（`web_settings_for_target`），没有 HTTP 面，
因此那一节用库的公开方法取证；DM / 通道 / 任务仍经 HTTP 创建，Agent 级档位仍经 PATCH 写入。
"""

from __future__ import annotations

from pathlib import Path

from alibot.db import open_database
from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel

# 三档合法值与一个形似而不合法的取值（`'fetch-only'` 不是其中一档）。
LEGAL_WEB_ACCESS = ("off", "search", "search+fetch")
ILLEGAL_WEB_ACCESS = "fetch-only"


def _config(client: TestClient, channel_id: str) -> dict:
    response = client.get(f"/api/channels/{channel_id}/config")
    assert response.status_code == 200, response.text
    return response.json()["config"]


def _patch_config(client: TestClient, channel_id: str, **fields: object):
    return client.patch(f"/api/channels/{channel_id}/config", json=fields)


def _agent(client: TestClient, member_id: str) -> dict:
    response = client.get(f"/api/agents/{member_id}")
    assert response.status_code == 200, response.text
    return response.json()["agent"]


def _patch_agent(client: TestClient, member_id: str, **fields: object):
    return client.patch(f"/api/agents/{member_id}", json=fields)


# ─── 验收 4a：频道的 web_access 可读、三档可写、显式 null = 未配置 ───────────────
def test_a_fresh_channel_reports_web_access_as_unset(client: TestClient) -> None:
    """未配置的读数是 `null`；读路径把它解释为默认档 `search`（不是关闭）。"""
    channel = create_channel(client).json()["channel"]

    assert _config(client, channel["id"])["web_access"] is None


def test_channel_web_access_round_trips_the_three_levels_and_can_be_unset(
    client: TestClient,
) -> None:
    """三档逐档写进去再读回；显式 ``null`` 恢复到「未配置 = 默认档」。"""
    channel = create_channel(client).json()["channel"]

    for level in LEGAL_WEB_ACCESS:
        saved = _patch_config(client, channel["id"], web_access=level)
        assert saved.status_code == 200, saved.text
        assert saved.json()["config"]["web_access"] == level
        assert _config(client, channel["id"])["web_access"] == level

    turned_off = _patch_config(client, channel["id"], web_access=None)
    assert turned_off.status_code == 200, turned_off.text
    assert _config(client, channel["id"])["web_access"] is None


def test_channel_web_access_rejects_an_unknown_level_without_partial_write(client: TestClient) -> None:
    """非法档整体拒绝（400 + 该字段可读原因），同一请求里的其它字段也一个字都没写。"""
    channel = create_channel(client).json()["channel"]
    assert _patch_config(client, channel["id"], web_access="search").status_code == 200
    before = _config(client, channel["id"])

    response = _patch_config(
        client,
        channel["id"],
        web_access=ILLEGAL_WEB_ACCESS,
        permission_mode="trusted-writes",
        workspace_backend="docker",
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "web_access" in error["fields"], error
    assert _config(client, channel["id"]) == before


# ─── 验收 4b：Agent 级覆盖同样可读可写；显式 null 一律拒绝（该端点的既有纪律） ──
def test_a_fresh_agent_reports_web_access_as_following_the_channel(client: TestClient) -> None:
    """Agent 未配置 = 跟随频道：读数是 `null`。"""
    agent = create_agent(client).json()["agent"]

    assert _agent(client, agent["id"])["web_access"] is None


def test_agent_web_access_round_trips_the_three_levels(client: TestClient) -> None:
    agent = create_agent(client).json()["agent"]

    for level in LEGAL_WEB_ACCESS:
        saved = _patch_agent(client, agent["id"], web_access=level)
        assert saved.status_code == 200, saved.text
        assert saved.json()["agent"]["web_access"] == level
        assert _agent(client, agent["id"])["web_access"] == level


def test_agent_web_access_rejects_null_and_unknown_levels_without_partial_write(
    client: TestClient,
) -> None:
    """显式 ``null`` 与非法档都整体拒绝；同一请求里的 `role` 也没被写（不部分写入）。"""
    agent = create_agent(client).json()["agent"]
    before = _agent(client, agent["id"])

    rejected: list[tuple[dict[str, object], str]] = [
        ({"web_access": ILLEGAL_WEB_ACCESS, "role": "改了角色"}, "web_access"),
        ({"web_access": None, "role": "改了角色"}, "body"),
    ]
    for fields, field in rejected:
        response = _patch_agent(client, agent["id"], **fields)
        assert response.status_code == 400, fields
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert field in error["fields"], (fields, error)

    assert _agent(client, agent["id"]) == before


# ─── 验收 5：DM 的联网档读路径（#202）────────────────────────────────────────
# DM 没有频道，读路径只读 `agent_config`：未配置 = 默认档 `search`、显式 `off` 归一成 `None`；
# `mode` 固定 `read-only`、`backend=local`、`docker_egress=False`、`egress_hosts=[]`（没有频道
# workspace）。这条读数是装配层的**内部配置读数**（没有 HTTP 面），因此这里用库的公开方法取证；
# DM / 通道 / 任务本身仍经 HTTP 创建、Agent 级档位仍经 `PATCH /api/agents/{id}` 写入。


def _dm(client: TestClient, agent_id: str) -> dict:
    response = client.post("/api/dms", json={"agent_id": agent_id})
    assert response.status_code in (200, 201), response.text
    return response.json()["dm"]


def _task(client: TestClient, channel_id: str) -> dict:
    response = client.post(f"/api/channels/{channel_id}/tasks", json={"title": "查资料"})
    assert response.status_code == 201, response.text
    return response.json()["task"]


def test_a_dm_reads_the_default_search_level_from_agent_config_only(
    client: TestClient, data_dir: Path
) -> None:
    """DM 未配置 Agent 级档位 ⇒ 默认档 `search`；其余读数是 DM 的固定形状。"""
    agent = create_agent(client).json()["agent"]
    dm = _dm(client, agent["id"])

    database = open_database(data_dir)
    try:
        settings = database.web_settings_for_target(dm["id"], agent["id"])
    finally:
        database.close()

    assert settings is not None
    assert settings["web_access"] == "search"
    assert settings["mode"] == "read-only", "DM 没有权限档与审批面"
    assert settings["backend"] == "local"
    assert settings["docker_egress"] is False
    assert settings["egress_hosts"] == []
    assert settings["credentials"] == {}
    assert settings["default_provider"] is None
    assert settings["consented_providers"] == ()


def test_a_dm_normalizes_an_explicit_off_to_none(client: TestClient, data_dir: Path) -> None:
    """`off` 与频道分支同一条归一：装配层读到的空档位 = 不挂工具。"""
    agent = create_agent(client).json()["agent"]
    dm = _dm(client, agent["id"])
    assert _patch_agent(client, agent["id"], web_access="off").status_code == 200

    database = open_database(data_dir)
    try:
        settings = database.web_settings_for_target(dm["id"], agent["id"])
    finally:
        database.close()

    assert settings is not None
    assert settings["web_access"] is None


def test_an_unknown_target_still_reads_as_none(client: TestClient, data_dir: Path) -> None:
    """不是 channel / task / dm 的 target（这里是不存在的 id）仍是 `None`。"""
    agent = create_agent(client).json()["agent"]

    database = open_database(data_dir)
    try:
        settings = database.web_settings_for_target("tgt-does-not-exist", agent["id"])
    finally:
        database.close()

    assert settings is None


def test_channel_and_task_levels_keep_their_merge_order(client: TestClient, data_dir: Path) -> None:
    """channel / task 分支逐字不变：`agent → channel → 默认档`，`off` 归一；未知 target 仍 `None`。"""
    channel = create_channel(client).json()["channel"]
    agent = create_agent(client).json()["agent"]
    task = _task(client, channel["id"])
    saved = _patch_config(
        client,
        channel["id"],
        web_access="search+fetch",
        permission_mode="read-only",
        workspace_backend="docker",
        docker_egress=True,
    )
    assert saved.status_code == 200, saved.text

    database = open_database(data_dir)
    try:
        for target_id in (channel["id"], task["target_id"]):
            settings = database.web_settings_for_target(target_id, agent["id"])
            assert settings is not None
            assert settings["web_access"] == "search+fetch"
            assert settings["mode"] == "read-only"
            assert settings["backend"] == "docker"
            assert settings["docker_egress"] is True

        # Agent 级覆盖频道：`off` 在两条分支里都与频道分支的既有归一一致。
        assert _patch_agent(client, agent["id"], web_access="off").status_code == 200
        for target_id in (channel["id"], task["target_id"]):
            settings = database.web_settings_for_target(target_id, agent["id"])
            assert settings is not None
            assert settings["web_access"] is None
    finally:
        database.close()
