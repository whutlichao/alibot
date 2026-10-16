"""工作区设置面（语言偏好 / 消息密度 / 主题）的 HTTP 黑盒契约。

只断言**外部可观察行为**：``GET`` / ``PATCH /api/settings`` 的读数、非法输入的拒绝形态、
重启后一致、以及「改设置不产生对话副作用」（消息 / 轮次结果 / 预算跳数三处前后不变）。
测试不连数据库、不读表结构、不假设内部字段名以外的东西。

本票的边界（ADR-0009 / ADR-0007）：

- 语言偏好**落库**（不放浏览器），因为后端产出文案的路径跑在 HTTP 请求之外；
  但**后端生成的自然语言按语言渲染是 #62**，本票只落偏好本身。
- **主题档位（#71）**：本文件里两条「设置面没有主题档位」的断言已被**有意改判**为
  「第三个档位存在、取值合法、非法值被拒」——那是 #61 立的临时契约（它当时明确写着
  「主题由 #71 追加」），#71 就是来兑现它的。这不是放宽测试：非法取值仍必须整体拒绝。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, messages, send


def settings(client: TestClient) -> dict:
    return client.get("/api/settings").json()["settings"]


def test_defaults_are_source_locale_comfortable_density_and_system_theme(
    client: TestClient,
) -> None:
    current = settings(client)

    assert current["locale"] == "zh-CN"
    assert current["message_density"] == "comfortable"
    # 「跟随系统」是主题的缺省档：空库里没有用户取值，但档位本身必须可读且合法。
    assert current["theme"] == "system"
    # 空库里设置面也必须可读（缺省值随 schema 初始化落库，不是「查不到」）。
    assert current["updated_at"]


def test_patch_writes_every_tier_and_is_readable_back(client: TestClient) -> None:
    payload = client.patch(
        "/api/settings", json={"locale": "en", "message_density": "compact", "theme": "dark"}
    )

    assert payload.status_code == 200
    assert payload.json()["settings"]["locale"] == "en"
    assert payload.json()["settings"]["message_density"] == "compact"
    assert payload.json()["settings"]["theme"] == "dark"
    # 写路径的返回值与随后读回的读数一致（只有一个正本）。
    assert settings(client)["locale"] == "en"
    assert settings(client)["theme"] == "dark"


def test_patch_is_partial_and_leaves_the_other_tiers_alone(client: TestClient) -> None:
    client.patch("/api/settings", json={"locale": "en"})

    client.patch("/api/settings", json={"message_density": "compact"})

    client.patch("/api/settings", json={"theme": "light"})

    current = settings(client)
    assert current["locale"] == "en"
    assert current["message_density"] == "compact"
    assert current["theme"] == "light"


def test_empty_patch_changes_nothing(client: TestClient) -> None:
    before = settings(client)

    response = client.patch("/api/settings", json={})

    assert response.status_code == 200
    assert response.json()["settings"]["locale"] == before["locale"]
    assert response.json()["settings"]["message_density"] == before["message_density"]
    assert response.json()["settings"]["theme"] == before["theme"]


def test_settings_survive_backend_restart(launched: dict) -> None:
    """重启 = 换一个进程读同一个数据目录：取值仍在（后端是唯一正本）。"""
    launched["client"].patch(
        "/api/settings", json={"locale": "en", "message_density": "compact", "theme": "dark"}
    )

    restarted = launched["restart"]()

    current = restarted.get("/api/settings").json()["settings"]
    assert current["locale"] == "en"
    assert current["message_density"] == "compact"
    assert current["theme"] == "dark"


def test_unknown_key_is_rejected_whole(client: TestClient) -> None:
    # `accent` 不是任何一档（三档是 locale / message_density / theme）：未知键整体拒绝。
    response = client.patch("/api/settings", json={"locale": "en", "accent": "pink"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    # 未知键整体拒绝：同一请求里合法的 `locale` 也不得被写入（没有一半新一半旧）。
    assert settings(client)["locale"] == "zh-CN"


def test_out_of_tier_values_are_rejected_with_fields(client: TestClient) -> None:
    for body, field in (
        ({"locale": "zh"}, "locale"),
        ({"message_density": "spacious"}, "message_density"),
        ({"theme": "sepia"}, "theme"),
    ):
        response = client.patch("/api/settings", json=body)

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert field in error["fields"]

    assert settings(client)["locale"] == "zh-CN"
    assert settings(client)["message_density"] == "comfortable"
    assert settings(client)["theme"] == "system"


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

    response = client.patch(
        "/api/settings", json={"locale": "en", "message_density": "compact", "theme": "dark"}
    )

    assert response.status_code == 200
    assert snapshot() == before


def test_explicit_null_is_rejected_not_ignored(client: TestClient) -> None:
    """`null` 不是「没提到」：它既不是档位取值，也不是合法档位，因此整体拒绝。"""
    client.patch("/api/settings", json={"locale": "en"})

    response = client.patch("/api/settings", json={"locale": None})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    # 拒绝后取值不动（既不回到缺省，也不被静默当成一次空更新）。
    assert settings(client)["locale"] == "en"


def test_settings_expose_the_theme_tier_with_legal_values(client: TestClient) -> None:
    """第三档（主题，`#71`）**存在**、取值就是三档、且每一档都写得进读得回。

    #61 在这里立的是「设置面没有主题档位」的临时契约；#71 有意改判为「第三档存在且合法」。
    """
    assert sorted(settings(client).keys()) == [
        "locale",
        "message_density",
        "theme",
        "updated_at",
    ]

    for tier in ("light", "dark", "system"):
        response = client.patch("/api/settings", json={"theme": tier})

        assert response.status_code == 200
        assert response.json()["settings"]["theme"] == tier
        assert settings(client)["theme"] == tier


def test_theme_tier_rejects_values_outside_the_three_tiers(client: TestClient) -> None:
    """非法档位整体拒绝：既不写入，也不把已写好的取值改回缺省。"""
    client.patch("/api/settings", json={"theme": "dark"})

    response = client.patch("/api/settings", json={"theme": "sepia"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "theme" in error["fields"]
    assert settings(client)["theme"] == "dark"
