"""工作区设置面（语言偏好 / 消息密度）的 HTTP 黑盒契约。

只断言**外部可观察行为**：``GET`` / ``PATCH /api/settings`` 的读数、非法输入的拒绝形态、
重启后一致、以及「改设置不产生对话副作用」（消息 / 轮次结果 / 预算跳数三处前后不变）。
测试不连数据库、不读表结构、不假设内部字段名以外的东西。

本票的边界（ADR-0009 / ADR-0007）：

- 语言偏好**落库**（不放浏览器），因为后端产出文案的路径跑在 HTTP 请求之外；
  但**后端生成的自然语言按语言渲染是 #62**，本票只落偏好本身。
- 主题是第三档、由 #71 追加，因此本票的设置面**不得**出现主题字段或档位。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, messages, send


def settings(client: TestClient) -> dict:
    return client.get("/api/settings").json()["settings"]


def test_defaults_are_source_locale_and_comfortable_density(client: TestClient) -> None:
    current = settings(client)

    assert current["locale"] == "zh-CN"
    assert current["message_density"] == "comfortable"
    # 空库里设置面也必须可读（缺省值随 schema 初始化落库，不是「查不到」）。
    assert current["updated_at"]


def test_patch_writes_both_tiers_and_is_readable_back(client: TestClient) -> None:
    payload = client.patch("/api/settings", json={"locale": "en", "message_density": "compact"})

    assert payload.status_code == 200
    assert payload.json()["settings"]["locale"] == "en"
    assert payload.json()["settings"]["message_density"] == "compact"
    # 写路径的返回值与随后读回的读数一致（只有一个正本）。
    assert settings(client)["locale"] == "en"


def test_patch_is_partial_and_leaves_the_other_tier_alone(client: TestClient) -> None:
    client.patch("/api/settings", json={"locale": "en"})

    client.patch("/api/settings", json={"message_density": "compact"})

    current = settings(client)
    assert current["locale"] == "en"
    assert current["message_density"] == "compact"


def test_empty_patch_changes_nothing(client: TestClient) -> None:
    before = settings(client)

    response = client.patch("/api/settings", json={})

    assert response.status_code == 200
    assert response.json()["settings"]["locale"] == before["locale"]
    assert response.json()["settings"]["message_density"] == before["message_density"]


def test_settings_survive_backend_restart(launched: dict) -> None:
    """重启 = 换一个进程读同一个数据目录：取值仍在（后端是唯一正本）。"""
    launched["client"].patch("/api/settings", json={"locale": "en", "message_density": "compact"})

    restarted = launched["restart"]()

    current = restarted.get("/api/settings").json()["settings"]
    assert current["locale"] == "en"
    assert current["message_density"] == "compact"


def test_unknown_key_is_rejected_whole(client: TestClient) -> None:
    response = client.patch("/api/settings", json={"locale": "en", "theme": "dark"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    # 未知键整体拒绝：同一请求里合法的 `locale` 也不得被写入（没有一半新一半旧）。
    assert settings(client)["locale"] == "zh-CN"


def test_out_of_tier_values_are_rejected_with_fields(client: TestClient) -> None:
    for body, field in (
        ({"locale": "zh"}, "locale"),
        ({"message_density": "spacious"}, "message_density"),
    ):
        response = client.patch("/api/settings", json=body)

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert field in error["fields"]

    assert settings(client)["locale"] == "zh-CN"
    assert settings(client)["message_density"] == "comfortable"


def test_settings_change_leaves_production_facts_untouched(launched: dict) -> None:
    """改设置**不落消息、不唤醒、不消耗预算**：三处读数前后逐字不变。"""
    client: TestClient = launched["client"]
    agent_id = create_agent(client, name="设置面验证").json()["agent"]["id"]
    channel = create_channel(client, name="设置面", member_ids=[agent_id]).json()["channel"]
    send(client, channel["id"], "一条人类消息")
    launched["scheduler"].drain()

    def snapshot() -> tuple[int, int, int]:
        listed = messages(client, channel["id"])
        outcomes = client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        budget = client.get(f"/api/channels/{channel['id']}/budget").json()["budget"]
        return len(listed), len(outcomes), budget["used"]

    before = snapshot()
    assert before[0] > 0 and before[1] > 0  # 先造出真实的活动，再证明改设置不改变它

    response = client.patch("/api/settings", json={"locale": "en", "message_density": "compact"})

    assert response.status_code == 200
    assert snapshot() == before


def test_settings_do_not_expose_theme_tier(client: TestClient) -> None:
    """主题是**第三档**、由 #71 追加：本票的设置面只有语言与消息密度。"""
    assert sorted(settings(client).keys()) == ["locale", "message_density", "updated_at"]


def test_settings_patch_does_not_accept_theme_key(client: TestClient) -> None:
    response = client.patch("/api/settings", json={"theme": "dark"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
