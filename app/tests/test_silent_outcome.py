"""#58 结构化沉默信号 ``silent``：seam 契约、落库、库级 CHECK 与词表一致性。

判据来源：spec #35 的 D0-b / D0-d / D3（落点-b）与 ADR-0008。

接缝沿用既有那两套（票面硬要求：**不新开 seam**）：

1. **seam 级**（``AgentSeam`` / ``RoundOutcome``）：一轮结果的形状与互斥校验直接在这个类型上取证，
   因为「一轮 ↔ 至多一条可落库消息」与「``silent`` 必须零条」都是它的 ``__post_init__`` 契约，
   HTTP 面上不可见（违反者根本走不到落库）。
2. **HTTP 黑盒 + 脚本化替身**：``silent`` / ``ignored`` / ``replied`` / ``failed`` 四种结论经既有
   ``ScriptedAgentSeam`` 注入，断言只落在 HTTP 可见的 ``round_outcome`` 行与消息流上。

``silent`` 与 ``ignored`` 的边界（CONTEXT.md 的**沉默**/**轮次结果**两词条）：``ignored`` 是既有
seam 的正常自判忽略；``silent`` 专指模型经**结构化通道**明确声明沉默。``reason`` 仍只是可读诊断，
不承担状态判别——因此这里的断言一律读 ``status``，只在「reason 可读」这一条上读文本非空。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from alibot.agent_seam import SEAM_STATUSES, RoundMessage, RoundOutcome
from alibot.db import DATABASE_FILE_NAME, ROUND_STATUSES
from fastapi.testclient import TestClient

from tests.conftest import (
    channel_with_agent,
    create_agent,
    messages,
    outcomes,
    send,
)
from tests.scripted import FAIL, IGNORE, REPLY, SILENT

# 已落库的一条轮次结果在 DB 里除了 id / status / reason 之外还必须给的值（见 ``SCHEMA``）。
_INSERT_OUTCOME_SQL = (
    "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, reason, started_at, "
    "finished_at, consumed_budget, merged, reply_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL)"
)


# ─── 1. seam 契约：第四个状态词 + 与非空 messages 互斥 ─────────────────────
def test_silent_is_the_fourth_seam_status_on_par_with_the_other_three() -> None:
    """``silent`` 与 ``replied`` / ``ignored`` / ``failed`` **平级**（同一个词表，不是布尔字段）。"""
    assert SEAM_STATUSES == ("replied", "ignored", "failed", "silent")


def test_a_silent_outcome_carries_no_storable_message() -> None:
    """``RoundOutcome(status="silent")`` 与**非空** ``messages`` 互斥（D0-d）。"""
    silent = RoundOutcome(status="silent", reason="模型经结构化通道声明本轮沉默")
    assert silent.status == "silent"
    assert silent.messages == []

    with pytest.raises(ValueError):
        RoundOutcome(status="silent", messages=[RoundMessage(body="（本轮与我无关）")])


def test_each_round_still_carries_at_most_one_storable_message() -> None:
    """既有的「一轮 ↔ 至多一条 target 消息」强校验原样保留（不是被 silent 放宽）。"""
    assert len(RoundOutcome(status="replied", messages=[RoundMessage(body="一句")]).messages) == 1
    with pytest.raises(ValueError):
        RoundOutcome(
            status="replied",
            messages=[RoundMessage(body="第一句"), RoundMessage(body="第二句")],
        )


# ─── 2. 四种结论经既有脚本化替身落库 ─────────────────────────────────────
@pytest.mark.parametrize(
    ("behavior", "expected_status", "reply_messages"),
    [
        (REPLY, "replied", 1),
        (IGNORE, "ignored", 0),
        (FAIL, "failed", 0),
        (SILENT, "silent", 0),
    ],
)
def test_each_round_outcome_kind_is_persisted_with_its_message_count(
    client: TestClient,
    harness: dict,
    behavior: str,
    expected_status: str,
    reply_messages: int,
) -> None:
    """四种结论各落一条 ``round_outcome``：``silent`` **零条**待落库回复消息，``ignored`` 语义不变。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = behavior
    channel = channel_with_agent(client, agent["id"])

    # 不点名（不是确定信号）：这一轮允许自判忽略 / 声明沉默，不该被纪律改判成 failed。
    send(client, channel["id"], "随便聊两句，今天天气不错")
    harness["scheduler"].drain()

    listed = messages(client, channel["id"])
    assert [message["kind"] for message in listed] == ["human"] + ["agent"] * reply_messages

    records = outcomes(client, channel["id"])
    assert [record["status"] for record in records] == [expected_status]
    record = records[0]
    assert record["member_id"] == agent["id"]
    assert record["base_seq"] == listed[0]["seq"]
    assert record["reason"].strip(), "reason 是可读诊断：任何结论都必须留下可读的一句话"
    if expected_status == "silent":
        assert record["reply_seq"] is None, "silent 轮次没有可落库的回复，因此没有 reply_seq"

    # 状态点只看「最近一条非 merged 的轮次结果是否为 failed」：silent 不得把它推成 error。
    expected_member_status = "error" if expected_status == "failed" else "online"
    assert client.get(f"/api/agents/{agent['id']}/status").json()["status"] == expected_member_status, (
        f"{expected_status} 轮的成员状态点读数不对"
    )


def test_silent_and_ignored_are_two_distinguishable_rows_in_one_round(
    client: TestClient, harness: dict
) -> None:
    """同一条消息下两种「没说话」各自可查：``silent`` 有显式声明，``ignored`` 是既有的自判忽略。"""
    silent_agent = create_agent(client, name="沉默者").json()["agent"]
    ignored_agent = create_agent(client, name="忽略者").json()["agent"]
    harness["seam"].behaviors[silent_agent["id"]] = SILENT
    harness["seam"].behaviors[ignored_agent["id"]] = IGNORE
    channel = channel_with_agent(client, silent_agent["id"])
    joined = client.post(f"/api/channels/{channel['id']}/members", json={"member_id": ignored_agent["id"]})
    assert joined.status_code in (200, 201), joined.text

    send(client, channel["id"], "两句闲话")
    harness["scheduler"].drain()

    by_member = {record["member_id"]: record for record in outcomes(client, channel["id"])}
    assert by_member[silent_agent["id"]]["status"] == "silent"
    assert by_member[ignored_agent["id"]]["status"] == "ignored"
    # 两个都不是 failed：状态点不因它们变红。
    assert all(record["status"] != "failed" for record in by_member.values())


# ─── 3. 确定信号下 silence 按 failed 收口（CONTEXT.md 的回应判断） ────────
def test_silence_under_a_definite_signal_is_recorded_as_failed(client: TestClient, harness: dict) -> None:
    """被点名却声明沉默 ⇒ ``failed``（不静默：不能把「它不想说」当成「它没被问」）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = SILENT
    channel = channel_with_agent(client, agent["id"])

    send(client, channel["id"], f"@{agent['display_name']} 帮我看下配色")
    harness["scheduler"].drain()

    records = outcomes(client, channel["id"])
    assert [record["status"] for record in records] == ["failed"]
    listed = messages(client, channel["id"])
    assert [message["kind"] for message in listed] == ["human"], "声明沉默不产出回复消息"
    assert "确定信号" in records[0]["reason"], "改判的原因必须是可读的（而不是只剩一个 failed）"


# ─── 4. 库级 CHECK：接受 silent、拒绝非法词 ───────────────────────────────
def test_the_database_check_accepts_silent_and_refuses_an_unknown_word(
    client: TestClient, harness: dict
) -> None:
    """持久化契约：``ROUND_STATUSES`` 与表级 ``CHECK`` 都纳入 ``silent``（ADR-0008）。"""
    assert "silent" in ROUND_STATUSES

    agent = create_agent(client, name="小明").json()["agent"]
    harness["seam"].behaviors[agent["id"]] = SILENT
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "随便聊两句")
    harness["scheduler"].drain()
    silent_row = outcomes(client, channel["id"])[0]
    assert silent_row["status"] == "silent"

    db_path = harness["data_dir"] / DATABASE_FILE_NAME
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        declared = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'round_outcome'"
        ).fetchone()[0]
        assert "'silent'" in declared, "表级 CHECK 必须点名 silent，否则它只是词表里的一行字"

        # 库级 CHECK **接受** silent：绕过应用层直接写一行合法值。
        conn.execute(
            _INSERT_OUTCOME_SQL,
            (
                "round_direct_silent",
                agent["id"],
                channel["id"],
                1,
                "silent",
                "直接写入",
                "2026-10-20T09:00:00Z",
                "2026-10-20T09:00:01Z",
            ),
        )
        # 库级 CHECK **拒绝**词表外的词（应用层校验失效时库仍然拦得住）。
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                _INSERT_OUTCOME_SQL,
                (
                    "round_direct_quiet",
                    agent["id"],
                    channel["id"],
                    1,
                    "quiet",
                    "非法词",
                    "2026-10-20T09:00:02Z",
                    "2026-10-20T09:00:03Z",
                ),
            )
    finally:
        conn.close()


# ─── 5. 词表一致性：后端 status 词 ↔ Studio 摘要词表 ↔ 两种语言的说法 ──────
def test_studio_vocabulary_agrees_with_the_backend_status_words() -> None:
    """``silent`` 在前端是一等摘要状态，且**两种语言**都有准确说法（不是回退成 ``silent`` 原文）。"""
    studio = Path(__file__).resolve().parents[1] / "alibot" / "studio"
    app_js = (studio / "app.js").read_text(encoding="utf-8")
    summary_source = re.search(r"const SUMMARY_STATUSES = \[(.*?)\];", app_js, re.S)
    assert summary_source is not None, "Studio 的摘要词表仍在 app.js 里"
    summary_statuses = set(re.findall(r"'([a-z-]+)'", summary_source.group(1)))
    assert "silent" in summary_statuses, "silent 必须进摘要行（它不是失败，不能只挂 failed 那块）"
    assert summary_statuses <= set(ROUND_STATUSES), "前端不得发明后端词表里没有的状态词"
    # seam 的四个词都必须是持久化词表的子集：能落库的才能被查出来。
    assert set(SEAM_STATUSES) <= set(ROUND_STATUSES)

    i18n_js = (studio / "i18n.js").read_text(encoding="utf-8")
    for status in sorted(summary_statuses):
        for suffix in ("label", "note"):
            key = f"outcome.{status}.{suffix}"
            # 两种语言各一处：词条表是「同一键集、两份取值」，落单键会让一种语言回退成状态词原文。
            assert i18n_js.count(f"'{key}'") == 2, f"{key} 必须在两种语言里都有取值"

    # 两种语言的取值按**出现顺序**读（源语言在词条表前面）：同一键两处，缺一处就会变成状态词原文。
    labels = re.findall(r"'outcome\.silent\.label': '([^']*)'", i18n_js)
    assert len(labels) == 2, "silent 的说法必须在两种语言里各有一处"
    assert "沉默" in labels[0], "源语言必须说「沉默」（CONTEXT.md 的沉默词条）"
    assert "ilence" in labels[1], "第二语言必须说 silence（同一个事实）"
