"""上下文窗口经既有脚本化适配接缝取证；回应纪律与读位仍只消费新消息。"""

from typing import Any, cast

from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, drain, outcomes, send


def test_second_round_remembers_the_exchange_without_reusing_old_mentions(
    client: TestClient, harness: dict
) -> None:
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
    send(client, channel["id"], "@小明 记住第一句")
    drain(harness)
    first_reply = harness["seam"].calls_for(agent["id"])
    assert first_reply[0]["mentioned"] is True

    send(client, channel["id"], "接着说")
    drain(harness)

    second = harness["seam"].calls_for(agent["id"])[1]
    assert second["seqs"] == [1, 2, 3]
    assert second["bodies"][0] == "@小明 记住第一句"
    assert second["mentioned"] is False
    assert second["signal"] == "human-message"
    assert outcomes(client, channel["id"])[-1]["base_seq"] == 3
    assert cast(Any, client.app).state.db.agent_cursor(channel["id"], agent["id"]) == 3


def test_window_can_be_overridden_without_truncating_outcome_reads(
    client: TestClient, harness: dict, monkeypatch
) -> None:
    monkeypatch.setenv("ALIBOT_CONTEXT_WINDOW_MESSAGES", "2")
    agent = create_agent(client).json()["agent"]
    channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
    send(client, channel["id"], "第一句")
    drain(harness)
    send(client, channel["id"], "第二句")
    drain(harness)
    assert harness["seam"].calls_for(agent["id"])[1]["seqs"] == [2, 3]
    assert len(outcomes(client, channel["id"])) == 2
