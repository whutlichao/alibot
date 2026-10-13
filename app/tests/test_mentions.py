"""提及解析与快照：最左最长匹配、引号形式、不存在名字与邮箱式文本不误判。

只经 HTTP 面取证：发送返回的消息里带 `mention_snapshot`（解析结果随消息**落库**），
带 `after_seq` 重读拿到的还是同一份快照（不事后按当前成员名表重新解析）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import backend, channel_with_agent, create_agent, messages, send


def snapshot_of(client: TestClient, channel_id: str, body: str) -> list[dict]:
    response = send(client, channel_id, body)
    assert response.status_code == 201, response.text
    return response.json()["message"]["mention_snapshot"]


@pytest.fixture
def two_agents(client: TestClient) -> dict[str, str]:
    """一个频道里放两个 Agent：`小明` 与 `小明王`（用来验最左最长匹配）。"""
    short = create_agent(client, name="小明").json()["agent"]
    long = create_agent(client, name="小明王").json()["agent"]
    both = client.post(
        "/api/channels",
        json={"name": "两个 Agent", "description": "", "member_ids": [short["id"], long["id"]]},
    ).json()["channel"]
    return {"short": short["id"], "long": long["id"], "channel": both["id"]}


def test_plain_mention_matches_the_only_member(client: TestClient) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    spans = snapshot_of(client, channel["id"], "@小明 看一下")

    assert [span["member_id"] for span in spans] == [agent["id"]]
    assert spans[0]["display_name"] == "小明"
    assert spans[0]["start"] == 0
    assert spans[0]["end"] == 3


def test_leftmost_longest_wins(client: TestClient, two_agents: dict[str, str]) -> None:
    channel_id = two_agents["channel"]

    spans = snapshot_of(client, channel_id, "@小明王 请处理")

    assert [span["member_id"] for span in spans] == [two_agents["long"]]
    assert spans[0]["display_name"] == "小明王"
    # 短名不会被切出来当成另一次提及：扫描一次消费掉整个匹配。
    assert len(spans) == 1

    short_spans = snapshot_of(client, channel_id, "@小明 看一下")
    assert [span["member_id"] for span in short_spans] == [two_agents["short"]]


def test_quoted_form_supports_names_with_spaces(client: TestClient) -> None:
    spaced = create_agent(client, name="前端 小明").json()["agent"]
    plain = create_agent(client, name="小明").json()["agent"]
    channel = client.post(
        "/api/channels",
        json={"name": "带空格的名字", "description": "", "member_ids": [spaced["id"], plain["id"]]},
    ).json()["channel"]

    quoted = snapshot_of(client, channel["id"], '@"前端 小明" 看一下')
    assert [span["member_id"] for span in quoted] == [spaced["id"]]
    assert quoted[0]["display_name"] == "前端 小明"

    # 无引号形式无法表达带空格的名字：`@前端 小明` 里 `@` 后面只有 `前端`，不是成员名。
    assert snapshot_of(client, channel["id"], "@前端 小明") == []
    # 但该成员的名字片段本身仍是一个真实成员时，无引号形式命中那个成员。
    assert [span["member_id"] for span in snapshot_of(client, channel["id"], "@小明 看一下")] == [plain["id"]]

    # 两种形式并存时位置都正确。
    mixed = snapshot_of(client, channel["id"], '请 @"前端 小明" 与 @小明 一起看')
    assert [span["display_name"] for span in mixed] == ["前端 小明", "小明"]
    assert [span["start"] for span in mixed] == [2, 13]


def test_quoted_form_with_unknown_name_is_not_a_mention(client: TestClient) -> None:
    create_agent(client, name="小明")
    channel = channel_with_agent(client, create_agent(client, name="别人").json()["agent"]["id"])

    assert snapshot_of(client, channel["id"], '@"没有这个人" 看一下') == []


def test_unknown_name_is_not_a_mention(client: TestClient) -> None:
    create_agent(client, name="小明")
    channel = channel_with_agent(client, create_agent(client, name="别人").json()["agent"]["id"])

    assert snapshot_of(client, channel["id"], "@查无此人 看一下") == []


def test_at_sign_without_a_member_is_not_a_mention(client: TestClient) -> None:
    create_agent(client, name="小明")
    channel = channel_with_agent(client, create_agent(client, name="别人").json()["agent"]["id"])

    for body in ("单价 @ 10 元", "今天就到这里 @", "@小明丰 不是这个名字", "话题 #tag@小明"):
        spans = snapshot_of(client, channel["id"], body)
        assert spans == [], body


def test_email_like_text_is_not_a_mention(client: TestClient) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    create_agent(client, name="example")
    channel = channel_with_agent(client, agent["id"])

    for body in (
        "联系 user@example.com 获取权限",
        "抄送 user@小明",
        "路径 /var/lib@小明",
        "版本 v1.2@小明",
        "user_name@小明",
        "user-name@小明",
        "user+tag@小明",
    ):
        spans = snapshot_of(client, channel["id"], body)
        assert spans == [], body


def test_mention_right_after_chinese_text_is_not_a_mention(client: TestClient) -> None:
    """紧贴中文的 `你好@小明` 不算提及：`isalnum()` 对中日韩文字亦为真。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    assert snapshot_of(client, channel["id"], "你好@小明") == []
    assert snapshot_of(client, channel["id"], "我问一下@小明") == []


def test_mention_after_punctuation_or_whitespace_is_a_mention(client: TestClient) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    for body in ("请 @小明 看", "@小明", "你好，@小明", "（@小明）", "、@小明", '"@小明"', ">@小明"):
        spans = snapshot_of(client, channel["id"], body)
        assert [span["member_id"] for span in spans] == [agent["id"]], body


def test_snapshot_is_persisted_and_not_re_resolved_later(client: TestClient) -> None:
    """改名不让历史提及关系漂移：快照随消息落库，之后按当前名表重新解析也读到旧快照。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    body = "@小明 看一下"
    stored = snapshot_of(client, channel["id"], body)

    # 再建一个同名 Agent（模拟成员名表变了），重读那条消息拿到的仍是原快照。
    create_agent(client, name="小明")
    reread = messages(client, channel["id"], after_seq=0)

    assert reread[0]["mention_snapshot"] == stored
    assert [span["member_id"] for span in reread[0]["mention_snapshot"]] == [agent["id"]]


def test_snapshot_survives_backend_restart(data_dir: Path) -> None:
    body = '请 @"小明" 与 @小明 一起看'
    with backend(data_dir) as first:
        agent = create_agent(first, name="小明").json()["agent"]
        channel = channel_with_agent(first, agent["id"])
        send(first, channel["id"], body)

    with backend(data_dir) as second:
        listed = messages(second, channel["id"])
        spans = listed[0]["mention_snapshot"]
        assert len(spans) == 2
        assert {span["member_id"] for span in spans} == {agent["id"]}
        # 位置快照对应的就是正文里的那两段 token（引号形式含闭引号）。
        assert [body[span["start"] : span["end"]] for span in spans] == ['@"小明"', "@小明"]


def test_mention_snapshot_is_not_forgeable_by_the_client(client: TestClient) -> None:
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    response = client.post(
        f"/api/channels/{channel['id']}/messages",
        json={"body": "没有点名", "mention_snapshot": [{"member_id": agent["id"], "start": 0, "end": 3}]},
    )

    assert response.status_code == 400
    assert messages(client, channel["id"]) == []


def test_mention_without_a_channel_agent_still_records_a_snapshot(client: TestClient) -> None:
    """提及快照是「这条正文里点了哪个成员」，与成员是否在该频道无关（穿透在 #③）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel_with_agent(client, create_agent(client, name="别人").json()["agent"]["id"])

    agent_id, channel_id = agent["id"], client.get("/api/channels").json()["channels"][0]["id"]
    spans = snapshot_of(client, channel_id, "@小明 看一下")

    assert [span["member_id"] for span in spans] == [agent_id]
