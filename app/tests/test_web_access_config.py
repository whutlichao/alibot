"""`web_access` 的**读写面**：`PATCH /api/channels/{id}/config` 与 `PATCH /api/agents/{id}`（#184）。

判据来源：ADR-0026 §二（默认关闭、显式开启；频道给默认值，Agent 可覆盖，`NULL` = 跟随频道）、
ADR-0011（Agent 配置的唯一写路径，任一字段不合法则**整体不写**）、ADR-0021 §六（未配置时行为逐字不变）。

接缝 = 既有 HTTP 黑盒（``create_app`` + 手动调度器 + 脚本化替身），与前后的 ``test_team_mode.py`` /
``test_agent_detail.py`` 同一套；不新开测试缝、不读表结构。断言只落在对外载荷与**读回的原值**：

- 「不部分写入」的取证方式就是票面给的那一条——请求失败后**读回原值**（不是查库）；
- `NULL` 的语义按协调者裁定（人定 D5，一个字不差）：频道侧显式 ``null`` = **关闭**（照
  ``workspace_root`` 先例），Agent 侧显式 ``null`` 一律 **400**（照该端点的既有纪律）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel

# 两档合法值与一个形似而不合法的取值（ADR-0026 只有两档：`'fetch-only'` 不是其中一档）。
LEGAL_WEB_ACCESS = ("search", "search+fetch")
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


# ─── 验收 4a：频道的 web_access 可读、两档可写、显式 null = 关闭 ───────────────
def test_a_fresh_channel_reports_web_access_off(client: TestClient) -> None:
    """未配置 = 关闭：新建频道的读数就是 `null`（不挂任何联网工具）。"""
    channel = create_channel(client).json()["channel"]

    assert _config(client, channel["id"])["web_access"] is None


def test_channel_web_access_round_trips_the_two_levels_and_can_be_turned_off(
    client: TestClient,
) -> None:
    """两档逐档写进去再读回；显式 ``null`` 把它重新关掉（`NULL` 是合法配置状态，不是「没提到」）。"""
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


def test_agent_web_access_round_trips_the_two_levels(client: TestClient) -> None:
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
