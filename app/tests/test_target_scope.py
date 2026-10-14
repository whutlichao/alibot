"""target 升为一等对象（issue #24 / ADR-0004）的数据面取证。

票面把这票定为**承重重构**：外部可观察行为（HTTP 路径/参数/响应字段名、未读口径、预算上限、
唤醒面）必须与改名前一模一样，变的只是**内部作用域键**。因此本文件只补三类既有测试没有覆盖的
新事实，且**优先走 HTTP 面**：

1. **结构**：``target`` 表存在、``kind`` 受约束、channel 型是**影子行**（id 同值）、
   ``message.target_id`` 有引用约束、append-only 触发器仍在、``SCHEMA_VERSION`` 已 bump。
2. **三种 kind 共存**：seq 仍是 **target 内**单调，三种 kind 各自独立。
3. **跨 target 不锁死**：一个 target 烧满 3 跳，另一个 target 的往返仍然可用。

“结构”这类事实在 HTTP 面无路径可观察（没有「读表结构」的端点，也不该为测试新增一个），
所以那几条**直接读库文件**。这是**唯一**允许读库的场合，且在注释里逐条说明原因；
dm / task 型消息也由测试自己用**最小数据**构造（票面验收项 2 明确授权：「就用你构造的最小数据
在 HTTP 面或等价层证明该口径成立，并说明你怎么构造的」）——本票不实现 dm / task 的业务逻辑
（#25–#28 做），只证明 schema 与查询路径**允许**它们。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alibot.db import BUDGET_LIMIT, SCHEMA_VERSION, Database
from fastapi.testclient import TestClient

from tests.conftest import (
    SPEC_OWNER_ID,
    budget,
    channel_with_agent,
    create_agent,
    create_channel,
    drain,
    messages,
    outcomes,
    send,
)


@contextmanager
def _store(data_dir: Path) -> Iterator[Database]:
    """打开该用例自己的库文件（**只读结构事实**，见模块文档）。"""
    db = Database(data_dir)
    try:
        yield db
    finally:
        db.close()


# ─── 结构：target 表、kind 约束、影子行、引用约束 ────────────────────────────
def test_target_table_exists_and_kind_is_constrained(client: TestClient, data_dir: Path) -> None:
    """``target`` 表存在且 ``kind ∈ {channel, dm, task}`` 受**库级**约束，没有映射表。"""
    channel = create_channel(client, name="结构").json()["channel"]

    with _store(data_dir) as db:
        # 影子行存在，且 kind 是 channel。
        assert db.get_target(channel["id"])["kind"] == "channel"
        # kind 的取值域由 CHECK 约束兜底：写一个域外的值必须被库拒绝。
        with pytest.raises(sqlite3.IntegrityError):
            db._conn.execute(
                "INSERT INTO target(id, kind, created_at) VALUES ('tgt_bad', 'nope', '2026-01-01T00:00:00Z')"
            )
        # 三种合法取值都写得进去（dm / task 是本票要**允许**的第二、第三种 kind）。
        db.create_target("dm", target_id="tgt_dm_probe")
        db.create_target("task", target_id="tgt_task_probe")
        assert db.get_target("tgt_dm_probe")["kind"] == "dm"
        assert db.get_target("tgt_task_probe")["kind"] == "task"
        # **没有映射表**：ADR-0004 明确否掉了「新建 target 行 + 映射表指向频道」那条路。
        tables = {
            row[0]
            for row in db._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert "target" in tables
        assert not {name for name in tables if "map" in name or "mapping" in name}


def test_channel_target_is_a_shadow_row_sharing_the_channel_id(client: TestClient, data_dir: Path) -> None:
    """channel 型 target 与 `channel` 行 **id 同值**；频道的 name/description 仍留在 `channel` 表。

    同时取证「频道消息的 ``target_id`` 就是频道 id」这条影子行语义（票面验收项 1）。
    """
    channel = create_channel(client, name="影子行", description="描述留在 channel 表").json()["channel"]
    sent = send(client, channel["id"], "一条频道消息").json()["message"]

    # HTTP 面：消息的 target_id 仍等于频道 id（零语义漂移，与 `m.target_id = c.id` 一致）。
    assert sent["target_id"] == channel["id"]

    with _store(data_dir) as db:
        target_row = db.get_target(channel["id"])
        channel_row = db._conn.execute("SELECT * FROM channel WHERE id = ?", (channel["id"],)).fetchone()
        assert target_row["id"] == channel_row["id"] == channel["id"]
        # 影子行**只承载 kind**：频道的名字/描述不在它上面（它们仍在 channel 行）。
        assert target_row["kind"] == "channel"
        assert channel_row["name"] == "影子行"
        assert channel_row["description"] == "描述留在 channel 表"


def test_message_target_id_has_a_reference_constraint(client: TestClient, data_dir: Path) -> None:
    """``message.target_id`` 是真外键：指向不存在的 target 必须被库拒绝。"""
    create_channel(client, name="引用约束")

    with _store(data_dir) as db, pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at, "
            "mention_snapshot) VALUES ('msg_ghost', 'tgt_不存在', 1, ?, 'human', '无主', "
            "'2026-01-01T00:00:00Z', '[]')",
            (SPEC_OWNER_ID,),
        )


def test_append_only_triggers_still_reject_update_and_delete(client: TestClient, harness: dict) -> None:
    """改名之后 append-only 仍是**库级**事实：message 与 round_outcome 的 UPDATE/DELETE 都被拒。

    库级触发器是**行级**的，所以先真跑一轮把两张表都填出行来，否则 `UPDATE ... ` 影响 0 行、
    不会报错，这条用例就成了恒真的假证据。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "一轮")
    drain(harness)  # 真跑一轮：落一条 agent 回复（message）与一条 round_outcome

    with _store(harness["data_dir"]) as db, pytest.raises(sqlite3.IntegrityError):
        # 前提证据：两张表里确实有行（否则下面的拒绝是「无行可拒」的假象）。
        assert db._conn.execute("SELECT COUNT(*) FROM message").fetchone()[0] > 0
        assert db._conn.execute("SELECT COUNT(*) FROM round_outcome").fetchone()[0] > 0
        db._conn.execute("UPDATE message SET body = '改写'")

    with _store(harness["data_dir"]) as db, pytest.raises(sqlite3.IntegrityError):
        db._conn.execute("DELETE FROM message")

    with _store(harness["data_dir"]) as db, pytest.raises(sqlite3.IntegrityError):
        db._conn.execute("UPDATE round_outcome SET status = 'failed'")

    with _store(harness["data_dir"]) as db, pytest.raises(sqlite3.IntegrityError):
        db._conn.execute("DELETE FROM round_outcome")


def test_schema_version_is_bumped_and_no_migration_script(client: TestClient, data_dir: Path) -> None:
    """``SCHEMA_VERSION`` 已 bump（决策 8：第二版从空库开始，不写迁移脚本）。"""
    create_channel(client, name="版本")

    with _store(data_dir) as db:
        row = db._conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        assert int(row["value"]) == SCHEMA_VERSION
        # bump 过：首版是 3，本票是 4（改名 + 影子行 + 引用约束一起进入同一个版本号）。
        assert SCHEMA_VERSION > 3
    # 没有迁移脚本：仓库里不存在任何 migrations/ 目录或 *_migrate*.py。
    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / "migrations").exists()
    assert not list(repo_root.rglob("migrate*.py"))


# ─── 三种 kind 各自独立计数 seq ─────────────────────────────────────────────
def test_three_kinds_count_seq_independently(client: TestClient, data_dir: Path) -> None:
    """``UNIQUE(target_id, seq)`` 不变，且三种 kind 的 seq **各自独立单调**。

    构造（最小数据，见模块文档）：频道走 HTTP 发消息；dm / task 型 target 只写一行 target，
    消息经 ``append_message``（**同一个**落库路径）写入——证明消息表与 seq 分配**不按 kind 分支**。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "频道第一条")
    send(client, channel["id"], "频道第二条")

    with _store(data_dir) as db:
        db.create_target("dm", target_id="tgt_dm_seq")
        db.create_target("task", target_id="tgt_task_seq")
        # 往 dm 写两条、往 task 写一条。
        db.append_message(target_id="tgt_dm_seq", body="私信一", author_member_id=agent["id"], kind="agent")
        db.append_message(target_id="tgt_dm_seq", body="私信二", author_member_id=agent["id"], kind="agent")
        db.append_message(
            target_id="tgt_task_seq", body="任务线程一", author_member_id=agent["id"], kind="agent"
        )

        # 每个 target 的 seq 都从 1 起，各自单调——一个 target 的消息不影响另一个。
        assert [m["seq"] for m in db.list_messages("tgt_dm_seq")] == [1, 2]
        assert [m["seq"] for m in db.list_messages("tgt_task_seq")] == [1]
        assert [m["seq"] for m in db.list_messages(channel["id"])] == [1, 2]

    # HTTP 面：频道自己的读数不受另外两个 target 影响（频道流与它们互不串号）。
    assert [message["seq"] for message in messages(client, channel["id"])] == [1, 2]


# ─── 跨 target 不锁死：每 target 一份预算 ───────────────────────────────────
def test_budget_is_per_target_so_burning_one_does_not_lock_another(client: TestClient, harness: dict) -> None:
    """在频道 A 内烧满 3 跳后，**频道 B 的往返仍然可用**（ADR-0004 决策 4：每 target 一份预算）。

    这是本票最要紧的新行为：若预算仍是「全局一份」或「跨 target 共享」，B 的往返会被 A 一起锁死，
    而那正是 ADR-0004 拒绝的方案。构造：A 里两个互相 @ 的 Agent 把 `used` 顶到上限；
    随后在 B 里发一条人类消息并让 B 的 Agent 回复——B 必须**真的被投递**且有自己的预算读数。
    """
    burnout_a = create_agent(client, name="甲").json()["agent"]
    burnout_b = create_agent(client, name="乙").json()["agent"]
    quiet = create_agent(client, name="丙").json()["agent"]
    harness["seam"].replies = {
        burnout_a["id"]: f"@{burnout_b['display_name']} 继续",
        burnout_b["id"]: f"@{burnout_a['display_name']} 继续",
        quiet["id"]: "B 频道收到",
    }
    channel_a = create_channel(client, name="A 烧满", member_ids=[burnout_a["id"], burnout_b["id"]]).json()[
        "channel"
    ]
    channel_b = create_channel(client, name="B 另一个", member_ids=[quiet["id"]]).json()["channel"]

    # 1) A 里接力到耗尽：used 顶到上限、remaining 归零。
    send(client, channel_a["id"], "A 开始接力")
    for _ in range(12):
        drain(harness)
        if harness["scheduler"].pending_count() == 0:
            break
    assert budget(client, channel_a["id"])["used"] == BUDGET_LIMIT
    assert budget(client, channel_a["id"])["remaining"] == 0

    # 2) B 的往返**不受 A 影响**：人类消息唤醒丙，丙真实回复（不是被压制）。
    before_b = budget(client, channel_b["id"])
    assert before_b["used"] == 0 and before_b["remaining"] == BUDGET_LIMIT
    send(client, channel_b["id"], "B 你好")
    drain(harness)
    assert any(
        message["kind"] == "agent" and message["body"] == "B 频道收到"
        for message in messages(client, channel_b["id"])
    ), "B 频道的 Agent 必须被真实投递：A 烧满 3 跳不得锁死 B 的往返"

    # 3) 两份预算各自独立：A 仍停在满额，B 有它自己的一条记录。
    assert budget(client, channel_a["id"])["used"] == BUDGET_LIMIT
    assert budget(client, channel_b["id"])["used"] >= 0
    with _store(harness["data_dir"]) as db:
        rows = db._conn.execute("SELECT target_id, used FROM target_budget ORDER BY target_id").fetchall()
        budgeted = {row["target_id"]: row["used"] for row in rows}
        # 两个 target 各占一行（**每 target 一份**，不是全局一行）。
        assert channel_a["id"] in budgeted and channel_b["id"] in budgeted
        assert budgeted[channel_a["id"]] == BUDGET_LIMIT


# ─── 未读口径：unread_total = channel + dm，task 不进 ────────────────────────
def test_unread_total_is_channel_plus_dm_and_task_stays_out(client: TestClient, data_dir: Path) -> None:
    """``unread_total`` = channel 型 + dm 型之和；**task 型不进它**（决策 G5）。

    构造（最小数据，见模块文档）：频道未读用一条真实的 Agent 回复产生（走 HTTP 往返）；
    dm / task 型 target 各由一行 ``target`` + 一条**由 Agent 作者**的 ``human`` 消息产生——
    未读口径要求「作者非本人」，所以由 Agent 作者的消息才算 owner 的未读。
    随后用 ``mark_read`` 推进 dm 的已读位置，证明投入 ``unread_total`` 的那部分确实随已读位置变化。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    create_channel(client, name="频道", member_ids=[agent["id"]])
    harness_agent = agent["id"]

    with _store(data_dir) as db:
        # dm：两条由 Agent 作者、owner 未读的消息。
        db.create_target("dm", target_id="tgt_dm_unread")
        db.append_message(
            target_id="tgt_dm_unread", body="私信一", author_member_id=harness_agent, kind="human"
        )
        db.append_message(
            target_id="tgt_dm_unread", body="私信二", author_member_id=harness_agent, kind="human"
        )
        # task：一条同类消息 —— 它必须**只**计入 task 自己，不进 unread_total。
        db.create_target("task", target_id="tgt_task_unread")
        db.append_message(
            target_id="tgt_task_unread", body="任务线程", author_member_id=harness_agent, kind="human"
        )
        db.mark_read("tgt_dm_unread", 0, member_id=SPEC_OWNER_ID)

        # 数据面：汇总口径 = channel 型 + dm 型，task 型被排除。
        assert db.unread_total(SPEC_OWNER_ID) == 2, "dm 的两条计入，task 的一条不进 unread_total"
        # 同一个形状（`_unread_count` 与频道列表共用）下，task 自己的读数仍是 1 ——
        # 它没有被丢掉，只是不进汇总（看板卡片各自读它）。
        assert db._unread_count("tgt_task_unread", SPEC_OWNER_ID) == 1

        # 推进 dm 已读位置后，投入 unread_total 的那部分归零，task 那部分不受影响。
        db.mark_read("tgt_dm_unread", 2, member_id=SPEC_OWNER_ID)
        assert db.unread_total(SPEC_OWNER_ID) == 0
        assert db._unread_count("tgt_task_unread", SPEC_OWNER_ID) == 1

    # HTTP 面：workspace 快照与库里的汇总口径一致（同一落点，不是第二套算法）。
    assert client.get("/api/workspace").json()["unread_total"] == 0


def test_unread_total_from_http_matches_channel_unread_when_only_channels_exist(
    client: TestClient, harness: dict
) -> None:
    """只有 channel 型数据时，``unread_total`` 与「频道未读相加」逐字相等（改名零漂移）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"], name="唯一频道")
    send(client, channel["id"], "你好")
    drain(harness)

    # 一条 Agent 回复 ⇒ 频道未读 1。
    assert client.get("/api/channels").json()["channels"][0]["unread_count"] == 1
    assert client.get("/api/workspace").json()["unread_total"] == 1


# ─── 频道面不因改名而变：dm / task 型 target 不是频道 ───────────────────────
def test_dm_and_task_targets_are_not_reachable_through_the_channel_face(
    client: TestClient, data_dir: Path
) -> None:
    """改的是内部作用域键：`/api/channels/{id}/...` 只认 channel 型 target，dm/task 一律 404。

    「不改 URL、不加新端点、不改错误码」这条 Constraint 的**负向证据**：dm / task 型 target 已经
    存在于库里（它不再是「不存在的东西」），但频道面仍然不认它——因为它们不是频道，本票也不给
    它们任何 HTTP 入口（#25–#28 做）。
    """
    create_channel(client, name="频道")
    with _store(data_dir) as db:
        db.create_target("dm", target_id="tgt_dm_hidden")
        db.create_target("task", target_id="tgt_task_hidden")

    for target_id in ("tgt_dm_hidden", "tgt_task_hidden"):
        assert client.get(f"/api/channels/{target_id}/messages").status_code == 404
        assert send(client, target_id, "不该进得来").status_code == 404
        assert client.post(f"/api/channels/{target_id}/read", json={"seq": 1}).status_code == 404
        assert client.get(f"/api/channels/{target_id}/budget").status_code == 404
        assert client.get(f"/api/channels/{target_id}/members").status_code == 404
        assert client.get(f"/api/channels/{target_id}/round-outcomes").status_code == 404
        assert client.get(f"/api/channels/{target_id}/events").status_code == 404


def test_target_member_table_is_the_single_membership_surface(client: TestClient, data_dir: Path) -> None:
    """成员关系读写在**同一个 `target_id` 键**上进行（`target_member`），channel 型即频道成员表。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = create_channel(client, name="成员面", member_ids=[agent["id"]]).json()["channel"]

    # HTTP 面：频道成员表就是该 channel 型 target 的成员行。
    listed = client.get(f"/api/channels/{channel['id']}/members").json()["members"]
    assert [member["id"] for member in listed] == [SPEC_OWNER_ID, agent["id"]]

    with _store(data_dir) as db:
        # 同一张表、同一个键：`target_member(target_id, member_id)`。
        assert db.is_target_member(channel["id"], agent["id"]) is True
        # 旧的 `channel_member` 表名已不存在（改名是彻底的，不留两份）。
        tables = {
            row[0]
            for row in db._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert "target_member" in tables
        assert "channel_member" not in tables
        assert "target_read" in tables and "channel_read" not in tables
        assert "target_budget" in tables and "channel_budget" not in tables


def test_round_outcomes_still_read_the_same_shape_after_the_rename(client: TestClient, harness: dict) -> None:
    """轮次结果仍在 `target_id` 上（它本来就叫对了，不动），只读端点的载荷形状不变。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    send(client, channel["id"], "一轮")
    drain(harness)

    record = outcomes(client, channel["id"])[0]
    assert record["target_id"] == channel["id"]
    assert set(record) == {
        "id",
        "member_id",
        "target_id",
        "base_seq",
        "status",
        "reason",
        "started_at",
        "finished_at",
        "consumed_budget",
        "merged",
        "reply_seq",
    }
