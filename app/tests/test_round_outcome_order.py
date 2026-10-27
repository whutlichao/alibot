"""轮次结果的**读取顺序**是确定的（#297）。

这条测试守的是一条不变式，不是一个场景：``GET /channels/{id}/round-outcomes`` 的顺序不能依赖
**随机**的东西。

为什么需要一个专门的票（#297）：读面原本写 ``ORDER BY finished_at, id``，而

- ``finished_at`` 只有**毫秒**精度（``db.py`` 的 ``utc_now()``）；
- ``id`` 是 ``uuid4`` 后 12 位（``db.py`` 的 ``new_id()``）——**随机且不单调**。

两轮结果落在同一毫秒时顺序就由随机 id 决定。实测连续两次 ``utc_now()`` 同毫秒
199764 / 200000，于是同一份数据每次读到的顺序可能不同。症状是一次 CI flaky
（``test_agent_round.py::test_ignored_round_advances_the_cursor_so_it_is_not_reread``
报 ``assert [2, 1] == [1, 2]``），但根因是**读面不可依赖**——运行面板的成员事件与分派也吃
同一份顺序。

判据刻意写成「同毫秒写入 + 反复读」：单次跑绿说明不了什么（修复前后都可能绿），
必须让「顺序会不会变」有落脚点。
"""

from __future__ import annotations

import sqlite3

import pytest
from alibot.db import Database, new_id, open_database

# 同一毫秒：三条记录共用这一个 ``finished_at``，于是时间戳不再是区分它们的依据。
SAME_MILLISECOND = "2026-10-26T13:39:10.109Z"


@pytest.fixture()
def db(tmp_path) -> Database:
    return open_database(tmp_path / "data")


def _seed_channel(db: Database, *, members: int = 1) -> tuple[str, list[str]]:
    channel = db.create_channel(name="design")
    agent_ids = [
        db.create_agent(name=f"成员{index}", role="", model_id="dashscope:qwen-max", system_prompt="")["id"]
        for index in range(members)
    ]
    return str(channel["id"]), agent_ids


def _insert_same_millisecond(
    db: Database, target_id: str, member_id: str, base_seqs: list[int], ids: list[str] | None = None
) -> list[str]:
    """直接写行：``append_round_outcome`` 会用真实时钟，拿不到「同一毫秒」这个前提。

    插入顺序就是 ``base_seqs`` 的顺序——这**是**「这一轮先收口」的事实，读取顺序必须尊重它。

    ``ids`` 可选：传了就按它的顺序逐条注入，用来把「插入序 ≠ 字典序」这类前提**确定性**地
    构造出来，而不是靠随机 uuid 碰运气（#314）。不传时仍用 ``new_id("round")`` 逐条生成。
    """
    if ids is not None and len(ids) != len(base_seqs):
        raise ValueError(f"ids 与 base_seqs 长度不一致：{len(ids)} != {len(base_seqs)}")
    inserted = []
    for index, base_seq in enumerate(base_seqs):
        outcome_id = ids[index] if ids is not None else new_id("round")
        with db._lock:
            db._conn.execute(
                "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, reason, "
                "started_at, finished_at, consumed_budget, merged, reply_seq, finished_reason) "
                "VALUES (?, ?, ?, ?, 'ignored', '', ?, ?, 0, 0, NULL, '')",
                (outcome_id, member_id, target_id, base_seq, SAME_MILLISECOND, SAME_MILLISECOND),
            )
            db._conn.commit()
        inserted.append(outcome_id)
    return inserted


def test_round_outcomes_keep_insertion_order_within_the_same_millisecond(db: Database) -> None:
    """同毫秒内，顺序 = 收口顺序（``base_seq`` 升序），与随机 id 无关。"""
    target_id, (member_id,) = _seed_channel(db)
    # base_seq 故意**倒着**插入也不会乱：读面要按业务事实（base_seq）排，不是按写入先后。
    _insert_same_millisecond(db, target_id, member_id, [3, 1, 2])

    orders = [[row["base_seq"] for row in db.list_round_outcomes(target_id)] for _ in range(12)]
    assert all(order == [1, 2, 3] for order in orders), f"同一份数据读出了不同顺序：{orders}"


def test_round_outcome_order_is_stable_across_many_reads(db: Database) -> None:
    """反复读同一份数据，顺序恒定（这是「不 flaky」的直接判据）。"""
    target_id, (member_id,) = _seed_channel(db)
    _insert_same_millisecond(db, target_id, member_id, [1, 2])

    first = [row["id"] for row in db.list_round_outcomes(target_id)]
    for _ in range(30):
        assert [row["id"] for row in db.list_round_outcomes(target_id)] == first


def test_round_outcome_order_differs_by_member_within_the_same_millisecond(db: Database) -> None:
    """同一毫秒、同一 ``base_seq``、不同成员：顺序 = 收口（插入）顺序，两次读一致。"""
    target_id, (aaa, bbb) = _seed_channel(db, members=2)
    # 故意让字典序更小的成员**后**插入：判据是「插入序」，不是成员 id 的字典序。
    _insert_same_millisecond(db, target_id, bbb, [1])
    _insert_same_millisecond(db, target_id, aaa, [1])

    orders = [[row["member_id"] for row in db.list_round_outcomes(target_id)] for _ in range(10)]
    assert all(order == [bbb, aaa] for order in orders), f"同一份数据读出了不同顺序：{orders}"


def test_round_outcomes_still_order_by_finished_at_first(db: Database) -> None:
    """跨毫秒时**仍以收口时间为第一排序键**：新的一轮排在旧的后面。"""
    target_id, (member_id,) = _seed_channel(db)
    with db._lock:
        db._conn.execute(
            "INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, reason, "
            "started_at, finished_at, consumed_budget, merged, reply_seq, finished_reason) "
            "VALUES (?, ?, ?, 9, 'ignored', '', ?, ?, 0, 0, NULL, '')",
            (new_id("round"), member_id, target_id, "2026-10-26T13:00:00.000Z", "2026-10-26T13:00:00.000Z"),
        )
        db._conn.commit()
    _insert_same_millisecond(db, target_id, member_id, [1])

    # 13:00 那条比 13:39 的那一批早，因此排在前面，尽管它的 base_seq 更大。
    assert [row["base_seq"] for row in db.list_round_outcomes(target_id)] == [9, 1]


def test_latest_round_outcome_status_is_also_deterministic(db: Database) -> None:
    """「最近一条轮次结果」与列表端点是**同一个顺序**（``latest_round_status``）。"""
    target_id, (member_id,) = _seed_channel(db)
    _insert_same_millisecond(db, target_id, member_id, [1, 2])

    # 同一毫秒内 base_seq 更大的那条是「更近的一轮」——两次读都要给出它。
    listed = db.list_round_outcomes(target_id)
    assert listed[-1]["base_seq"] == 2
    for _ in range(10):
        assert db.latest_round_status(member_id) == listed[-1]["status"]


def test_round_outcome_table_has_a_monotonic_tiebreaker(db: Database) -> None:
    """表本身要提供一个**单调**的 tiebreaker（``rowid``），随机 ``id`` 不能承担这个角色。

    这条是「为什么修法选 rowid 而不是换 ``utc_now()`` 精度」的落点：``rowid`` 随插入单调，
    而 ``uuid4`` 不单调。
    """
    target_id, (member_id,) = _seed_channel(db)
    # id 固定成「插入序 ≠ 字典序」的一批（格式同 ``new_id("round")``：``round_`` + 12 位 hex）。
    # 用随机 uuid 做这件事是 1/6 概率撞上有序、断言假红（#314），这里改成确定性地构造。
    unordered_ids = ["round_cccccccccccc", "round_aaaaaaaaaaaa", "round_bbbbbbbbbbbb"]
    inserted = _insert_same_millisecond(db, target_id, member_id, [1, 2, 3], ids=unordered_ids)

    with db._lock:
        rows = db._conn.execute(
            "SELECT id FROM round_outcome WHERE target_id = ? ORDER BY rowid", (target_id,)
        ).fetchall()
    assert [str(row["id"]) for row in rows] == inserted
    # id 的字典序**不**等于插入序：这正是原来那条 ORDER BY id 不可靠的原因。
    assert inserted != sorted(inserted), (
        f"这批 id 的字典序恰好等于插入序（{inserted}），用例失去区分力——换一组固定的 id"
    )


def test_round_outcome_is_not_a_without_rowid_table(db: Database) -> None:
    """``rowid`` 可用（WITHOUT ROWID 表没有它，那样 tiebreaker 就得另找）。"""
    with db._lock:
        sql = db._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='round_outcome'"
        ).fetchone()
    assert sql is not None
    assert "WITHOUT ROWID" not in str(sql["sql"]).upper()


def test_same_millisecond_condition_is_reachable() -> None:
    """守住前提：毫秒时间戳确实会重复（否则本票的判据退化成「跑一次绿」）。"""
    from alibot.db import utc_now

    seen_same = False
    previous = utc_now()
    for _ in range(20000):
        current = utc_now()
        if current == previous:
            seen_same = True
            break
        previous = current
    assert seen_same, "连续两次 utc_now() 从未落到同一毫秒——本票的前提变了，请重看判据"


def test_ordering_does_not_depend_on_the_random_id() -> None:
    """用一份**顺序已知**的内存库证明 ``ORDER BY ..., id`` 会被随机 id 打乱。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, base_seq INTEGER)")
    # 故意挑一组字典序与插入序相反的 id。
    for outcome_id, base_seq in (("round_zzz", 1), ("round_yyy", 2), ("round_xxx", 3)):
        conn.execute("INSERT INTO t(id, base_seq) VALUES (?, ?)", (outcome_id, base_seq))

    by_id = [row[0] for row in conn.execute("SELECT base_seq FROM t ORDER BY id")]
    by_rowid = [row[0] for row in conn.execute("SELECT base_seq FROM t ORDER BY rowid")]
    assert by_id == [3, 2, 1], "ORDER BY id 给出了与插入相反的次序——这就是原读面的缺陷"
    assert by_rowid == [1, 2, 3], "ORDER BY rowid 才是插入序"
