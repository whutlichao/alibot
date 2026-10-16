"""手动修复入口（issue #37 追加范围）：把当前 schema 库缺失的 channel 型 target 作用域数据补齐。

**历史缘起**：``~/Alibot`` 上真实发生过的事故——库曾被 v4 代码打开（``schema_version = 4``、
``channel`` 行都在），但某些 channel 型的**影子行缺失**、成员 / 已读 / 预算还留在 ``channel_*``
旧表名里。症状是 ``GET /api/channels`` 正常，点进那个频道则成员 / 消息 / 预算 / 标读全部 404。

**怎么构造**（票面明确授权，且不碰 ``~/Alibot``）：在 ``tmp_path`` 独占目录用当前代码建库，
再用 SQL 把那一个频道的 target 作用域数据**搬回旧表名并删掉新表行**，复现相同的缺行形态。
历史 v4 事故的版本号事实如上；当前代码仍由 schema 闸门拒绝旧版本，不会把这组测试当作迁移。

**为什么这条入口存在**：规格决策 8 / ADR-0004 只规定「不做自动迁移」，没规定「已被半改名的库怎么办」。
本入口**不是**自动迁移：它只在用户显式跑 ``python -m alibot --repair-targets`` 时执行，
正常启动路径一行都不会碰它（``test_normal_startup_never_repairs_anything`` 钉住这一点）。
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alibot.db import DATABASE_FILE_NAME, SCHEMA_VERSION, Database, SchemaVersionMismatch
from alibot.repair import RepairError, render_outcome, repair_target_rows

from tests.conftest import backend, channel_with_agent, create_agent, messages, send

APP_DIR = Path(__file__).resolve().parents[1]

# 事故现场涉及的四张旧表（v3 表名）与本版四张新表的对应关系。
_SCOPED_TABLES = ("target", "target_member", "target_read", "target_budget")

# v3 那一代的三张旧表。
#
# **为什么测试要自己建它们**：历史 v4 的 ``SCHEMA`` 只 **CREATE IF NOT EXISTS** 新表，**不删**旧表。
# 所以「v4 代码打开过 v3 库」留下的历史形态是：旧表**还在**（带着 v3 的数据）、新表被建出来但是
# 空的。一个**全新**的当前版本库则根本没有这三张表；隔离测试显式构造旧表以覆盖补齐路径。
# 因此测试先把旧表建出来、把数据搬回去，才能真的复现事故现场，而不是近似它
# （``~/Alibot`` 的真实副本就是这个形态：三张旧表带着 dev 的 3 行数据）。
_V3_LEFTOVER_SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_member (
  channel_id TEXT NOT NULL REFERENCES channel(id), member_id TEXT NOT NULL REFERENCES member(id),
  PRIMARY KEY (channel_id, member_id)
);
CREATE TABLE IF NOT EXISTS channel_read (
  member_id TEXT NOT NULL REFERENCES member(id), channel_id TEXT NOT NULL REFERENCES channel(id),
  last_read_seq INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (member_id, channel_id)
);
CREATE TABLE IF NOT EXISTS channel_budget (
  channel_id TEXT PRIMARY KEY REFERENCES channel(id), epoch_seq INTEGER NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)
);
"""

# 取行数的语句：**一句一个模块级常量**（与 ``repair.py`` 同一体例：不把表名插进 ``execute``
# 的实参）。测试只关心这八张表，清单固定，没有需要从 ``sqlite_master`` 动态拼名字的余地。
_ROW_COUNT_SQL: dict[str, str] = {
    "target": 'SELECT COUNT(*) FROM "target"',
    "target_member": 'SELECT COUNT(*) FROM "target_member"',
    "target_read": 'SELECT COUNT(*) FROM "target_read"',
    "target_budget": 'SELECT COUNT(*) FROM "target_budget"',
    "channel": 'SELECT COUNT(*) FROM "channel"',
    "channel_member": 'SELECT COUNT(*) FROM "channel_member"',
    "channel_read": 'SELECT COUNT(*) FROM "channel_read"',
    "channel_budget": 'SELECT COUNT(*) FROM "channel_budget"',
}
_COUNT_TARGET_READ_FOR_TARGET = 'SELECT COUNT(*) FROM "target_read" WHERE target_id = :target_id'


def _db_path(data_dir: Path) -> Path:
    return data_dir / DATABASE_FILE_NAME


def _half_rename_channel(data_dir: Path, channel_id: str) -> None:
    """把该频道的 target 作用域数据搬回 ``channel_*`` 旧表名，并删掉对应的新表行。

    这复现「v4 代码打开过 v3 库」的缺行形态：**旧表有数据、新表没有**。
    本辅助函数只做数据搬运，不动 schema、不动 ``schema_version``。
    """
    conn = sqlite3.connect(_db_path(data_dir))
    try:
        # 先把 v3 那一代残留的旧表建出来（真实的旧库里它们本来就在），再搬数据回旧表名。
        conn.executescript(_V3_LEFTOVER_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO channel_member(channel_id, member_id) "
            "SELECT target_id, member_id FROM target_member WHERE target_id = ?",
            (channel_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO channel_read(member_id, channel_id, last_read_seq) "
            "SELECT member_id, target_id, last_read_seq FROM target_read WHERE target_id = ?",
            (channel_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO channel_budget(channel_id, epoch_seq, used) "
            "SELECT target_id, epoch_seq, used FROM target_budget WHERE target_id = ?",
            (channel_id,),
        )
        conn.execute("DELETE FROM target_member WHERE target_id = ?", (channel_id,))
        conn.execute("DELETE FROM target_read WHERE target_id = ?", (channel_id,))
        conn.execute("DELETE FROM target_budget WHERE target_id = ?", (channel_id,))
        conn.execute("DELETE FROM target WHERE id = ?", (channel_id,))
        conn.commit()
    finally:
        conn.close()


def _row_counts(data_dir: Path) -> dict[str, int]:
    """相关表的行数快照（只读；用于「再跑一次不改动」与「默认不执行」的取证）。

    只计**库里存在**的表：一个全新的当前版本库根本没有 ``channel_*`` 三张旧表（它们是 v3 那一代的
    残留），把它们当成 0 行会让快照误报「表不存在」而不是「行数为 0」。缺表本身不进字典，
    于是「以前没有、现在多了」也会被 ``==`` 直接看出来。
    """
    conn = sqlite3.connect(f"file:{_db_path(data_dir)}?mode=ro", uri=True)
    try:
        present = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        return {
            table: int(conn.execute(_ROW_COUNT_SQL[table]).fetchone()[0])
            for table in present
            if table in _ROW_COUNT_SQL
        }
    finally:
        conn.close()


def _count_target_read_rows_for(data_dir: Path, target_id: str) -> int:
    """该 target 的 ``target_read`` 行数（只读）。

    SQL 直接引用模块常量 ``_COUNT_TARGET_READ_FOR_TARGET``（不经过参数传入）：与 ``db.py``
    引用 ``UNREAD_COUNT_SQL`` 等常量的体例一致 —— 查询文本对静态检查是可见常量。
    """
    conn = sqlite3.connect(f"file:{_db_path(data_dir)}?mode=ro", uri=True)
    try:
        row = conn.execute(_COUNT_TARGET_READ_FOR_TARGET, {"target_id": target_id}).fetchone()
        return int(row[0])
    finally:
        conn.close()


def _accident(data_dir: Path, name: str = "dev") -> tuple[str, str]:
    """造一个「事故现场」：返回 ``(channel_id, agent_id)``。

    频道里已有真实消息（所以「补完能读回」是可断言的），并且那个频道的 target 作用域数据
    被搬回了旧表名。
    """
    with backend(data_dir) as client:
        agent = create_agent(client, name="小明").json()["agent"]
        channel = channel_with_agent(client, agent["id"], name=name)
        for index in range(1, 4):
            send(client, channel["id"], f"第 {index} 条")
    _half_rename_channel(data_dir, channel["id"])
    return channel["id"], agent["id"]


# ─── 缺影子行 ⇒ 点进去全 404；修复后恢复可读 ────────────────────────────────
def test_missing_shadow_rows_break_the_channel_and_repair_restores_it(tmp_path: Path) -> None:
    """事前 404 → 修复 → 事后可读：成员 / 消息 / 预算 / 标读四件事一起回来。

    「事前 404」不只是摆设：它证明这条用例的库**确实**处在事故形态（否则修复前后都正常，
    用例就什么都没证明）。
    """
    data_dir = tmp_path / "data"
    channel_id, agent_id = _accident(data_dir)

    with backend(data_dir) as client:
        assert client.get(f"/api/channels/{channel_id}/members").status_code == 404
        assert send(client, channel_id, "进不去").status_code == 404
        assert client.get(f"/api/channels/{channel_id}/budget").status_code == 404
        # 但列表页是好的——这正是事故里「看着正常」的那一面。
        assert client.get("/api/channels").status_code == 200

    outcome = repair_target_rows(data_dir)
    assert not outcome.already_complete
    # 上报的行数与实际相符（影子行 1 + 成员 2 + 已读 2 + 预算 1）。
    assert outcome.inserted["target"] == 1
    assert outcome.inserted["target_member"] == 2
    assert outcome.inserted["target_read"] == 2
    assert outcome.inserted["target_budget"] == 1

    with backend(data_dir) as client:
        members = client.get(f"/api/channels/{channel_id}/members").json()["members"]
        assert {member["id"] for member in members} == {"owner", agent_id}
        assert [message["body"] for message in messages(client, channel_id)] == [
            "第 1 条",
            "第 2 条",
            "第 3 条",
        ]
        assert client.get(f"/api/channels/{channel_id}/budget").status_code == 200

    # 数据一条没丢：旧表原样保留（本入口只补新表，不删来源）。
    conn = sqlite3.connect(_db_path(data_dir))
    try:
        assert conn.execute("SELECT COUNT(*) FROM channel_member").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0] == 1
    finally:
        conn.close()


def test_repair_restores_the_budget_ledger_instead_of_silently_resetting_it(tmp_path: Path) -> None:
    """**预算账本必须一起补**：只补前三张表会让 ``used`` 从「已耗尽」静默归零。

    这条是本票在事故处置建议（三条语句）之外多补的一条。只补 ``target`` / ``target_member`` /
    ``target_read`` 的话，``GET /api/channels/{id}/budget`` 会从 404 变成 **200**——看起来更好了，
    但 ``used`` 读成 0：一个**已经烧满的世代**被伪装成「还没用过」。那比 404 更危险，因为它让
    「预算耗尽」这条事实消失，而 ADR-0002/0004 的整个有界响应机制都建立在这条事实之上。
    """
    data_dir = tmp_path / "data"
    channel_id, _ = _accident(data_dir)

    # 事故现场已经完成：``_accident`` 把该频道的账本从 ``target_budget`` 搬到了 ``channel_budget``。
    # 现在把旧表里的用量改成 1（模拟「这个世代已经烧掉一跳」），并确认新表里已经没有该行——
    # 于是「已用 1 跳」是一条**只存在于旧表**的待恢复事实。
    conn = sqlite3.connect(_db_path(data_dir))
    try:
        conn.execute("UPDATE channel_budget SET used = 1 WHERE channel_id = ?", (channel_id,))
        conn.commit()
        expected = conn.execute(
            "SELECT epoch_seq, used FROM channel_budget WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        remaining_new = conn.execute(
            "SELECT COUNT(*) FROM target_budget WHERE target_id = ?", (channel_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert expected is not None and expected[1] == 1, "旧表里必须留着「已用 1 跳」这条事实"
    assert remaining_new == 0, "新表里必须没有该行，否则「从旧表恢复」无从谈起"

    repair_target_rows(data_dir)

    with backend(data_dir) as client:
        restored = client.get(f"/api/channels/{channel_id}/budget").json()["budget"]
    assert restored["used"] == 1, "已用跳数必须从旧表恢复，而不是从 0 重新开始"
    assert restored["remaining"] == restored["limit"] - 1
    assert restored["epoch_seq"] == expected[0]


# ─── 幂等 ───────────────────────────────────────────────────────────────────
def test_running_repair_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    """对已修好的库再跑一次：改动 **0 行**、不报错、库逐项不变。"""
    data_dir = tmp_path / "data"
    _accident(data_dir)

    first = repair_target_rows(data_dir)
    assert not first.already_complete
    after_first = _row_counts(data_dir)

    second = repair_target_rows(data_dir)
    assert second.already_complete, "第二次必须报「改动 0 行」"
    assert second.changed_rows == 0
    assert all(count == 0 for count in second.inserted.values())
    assert _row_counts(data_dir) == after_first
    # 行数账本「前 = 后」也说明没有隐藏的改动。
    assert second.before == second.after


def test_running_repair_on_an_already_healthy_database_is_a_no_op(tmp_path: Path) -> None:
    """全新库（根本没坏过）上跑一次：改动 0 行，且库完全不变（本入口只做加法）。"""
    data_dir = tmp_path / "data"
    with backend(data_dir) as client:
        agent = create_agent(client, name="小明").json()["agent"]
        channel = channel_with_agent(client, agent["id"])
        send(client, channel["id"], "一条")
    before = _row_counts(data_dir)

    outcome = repair_target_rows(data_dir)

    assert outcome.already_complete
    assert _row_counts(data_dir) == before


# ─── 版本不符 ⇒ 拒绝（与主修同一个答复） ─────────────────────────────────────
def test_repair_refuses_a_database_whose_version_is_not_current(tmp_path: Path) -> None:
    """``schema_version != SCHEMA_VERSION`` ⇒ 拒绝执行并说明；库一行不改。

    修复入口**不是**迁就旧库的后门：它和启动路径读同一道闸门，因此对 v3 库给出同一个答复。
    """
    data_dir = tmp_path / "data"
    with backend(data_dir) as client:
        agent = create_agent(client, name="小明").json()["agent"]
        channel = channel_with_agent(client, agent["id"])
        send(client, channel["id"], "一条")
    conn = sqlite3.connect(_db_path(data_dir))
    try:
        conn.execute("UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()
    before = _row_counts(data_dir)

    with pytest.raises(SchemaVersionMismatch) as caught:
        repair_target_rows(data_dir)

    assert _row_counts(data_dir) == before
    assert str(SCHEMA_VERSION) in str(caught.value) and "3" in str(caught.value)


def test_repair_refuses_a_library_that_is_not_current_schema_shaped(tmp_path: Path) -> None:
    """版本号是当前版本、**表结构却不是当前 schema 形态**、**且库里有业务数据** ⇒ ``RepairError``。

    「有数据」是这条用例的要紧前提：**没有**数据的残缺库不算错（那是空库/未初始化库，本入口当作
    「没什么可补」处理，见下一条用例）。只有「有数据但不是当前 schema 形态」才是本入口不处置的库。

    构造：一个只有 ``schema_meta`` + ``member`` 表的库（缺 ``channel`` / ``target*``），并塞一行
    member——于是「有业务数据」成立，而 ``_CURRENT_REQUIRED_TABLES`` 大面积缺失。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(_db_path(data_dir))
    try:
        conn.executescript(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
            "CREATE TABLE member (\n"
            "  id TEXT PRIMARY KEY, kind TEXT NOT NULL, display_name TEXT NOT NULL,\n"
            "  avatar_slot INTEGER NOT NULL, created_at TEXT NOT NULL, deleted_at TEXT\n"
            ");\n"
            "INSERT INTO member(id, kind, display_name, avatar_slot, created_at)\n"
            "  VALUES ('owner', 'owner', 'Owner', 4, '2026-10-14T09:00:00Z');\n"
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RepairError) as caught:
        repair_target_rows(data_dir)

    assert "不是当前 schema 形态" in str(caught.value)


def test_repair_on_an_empty_directory_is_a_clean_no_op(tmp_path: Path) -> None:
    """空目录 / 没有库文件 ⇒ 改动 0 行、退出码语义正常，**且不凭空建一个库出来**。

    用户跑修复入口是想修**已有数据**；在一个没数据的目录上生成 ``alibot.db`` 不是他要的
    （连接会凭空造一个空库），因此这条路径必须早退。
    """
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    outcome = repair_target_rows(empty_dir)

    assert outcome.already_complete and outcome.changed_rows == 0
    assert list(empty_dir.iterdir()) == [], "空目录不得被建出 alibot.db 或其它文件"


def test_repair_on_a_schema_only_library_without_data_is_a_no_op(tmp_path: Path) -> None:
    """库存在、版本号是当前版本，但**没有任何业务数据**（初始化到一半）⇒ 空操作，不报错。

    「库形态不对」与「库没有数据」是两件事：只有**有数据却形态不对**时才该报 ``RepairError``。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(_db_path(data_dir))
    try:
        conn.executescript("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        conn.commit()
    finally:
        conn.close()

    outcome = repair_target_rows(data_dir)

    assert outcome.already_complete and outcome.changed_rows == 0
    # 它没有顺手把缺的表建出来（本入口只补作用域数据行，不改 schema）。
    conn = sqlite3.connect(f"file:{_db_path(data_dir)}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert tables == {"schema_meta"}


# ─── 默认不执行：正常启动绝不自动补 ─────────────────────────────────────────
def test_normal_startup_never_repairs_anything(tmp_path: Path) -> None:
    """**纪律钉子**：起服务这条路径不碰修复入口——半改名的库照旧坏着，不会被偷偷改好。

    这是「不做自动迁移」在那条纪律上的可断言形式：如果哪天有人把修复挂进启动流程，
    这条用例会红。
    """
    data_dir = tmp_path / "data"
    channel_id, _ = _accident(data_dir)
    before = _row_counts(data_dir)

    with backend(data_dir) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        # 启动**没有**顺手把它修好：仍然是 404，且库里一行没变。
        assert client.get(f"/api/channels/{channel_id}/members").status_code == 404
    assert _row_counts(data_dir) == before


def test_startup_path_does_not_import_the_repair_module() -> None:
    """结构证据：``create_app`` 的模块图里没有修复模块（不是靠「碰巧没调用」）。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import alibot.api; print('alibot.repair' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
        cwd=str(APP_DIR),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False", "启动路径不得引入修复模块"


# ─── CLI：显式入口的真实进程行为 ────────────────────────────────────────────
def test_cli_repair_reports_changes_then_reports_nothing_to_do(tmp_path: Path) -> None:
    """真实进程：``python -m alibot --repair-targets --data-dir <目录>``。

    第一次报出补了哪些行、退出码 0；第二次报「改动 0 行」。``PYTHONPATH`` 指向本 worktree 的
    ``app/``（本仓 ``.venv`` 是符号链接指向主仓，其 editable 的 ``.pth`` 指回主仓代码）。
    """
    data_dir = tmp_path / "data"
    channel_id, _ = _accident(data_dir)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alibot", "--repair-targets", "--data-dir", str(data_dir)],
            capture_output=True,
            text=True,
            timeout=60,
            env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
            cwd=str(APP_DIR),
        )

    first = run()
    assert first.returncode == 0, first.stderr
    assert "本次共补" in first.stdout
    assert str(_db_path(data_dir)) in first.stdout

    second = run()
    assert second.returncode == 0, second.stderr
    assert "本次改动 0 行" in second.stdout

    # 真的修好了：频道面四件事都回来了。
    with backend(data_dir) as client:
        assert client.get(f"/api/channels/{channel_id}/members").status_code == 200
        assert client.get(f"/api/channels/{channel_id}/budget").status_code == 200


def test_cli_repair_refuses_an_old_database_with_a_non_zero_exit_code(tmp_path: Path) -> None:
    """旧库上跑修复入口 ⇒ 非 0 退出码 + stderr 说明（与启动路径同一个答复）。"""
    data_dir = tmp_path / "data"
    with backend(data_dir) as client:
        create_agent(client, name="小明")
    conn = sqlite3.connect(_db_path(data_dir))
    try:
        conn.execute("UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()
    before = _row_counts(data_dir)

    completed = subprocess.run(
        [sys.executable, "-m", "alibot", "--repair-targets", "--data-dir", str(data_dir)],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
        cwd=str(APP_DIR),
    )

    assert completed.returncode != 0
    assert "拒绝启动" in completed.stderr
    assert _row_counts(data_dir) == before


# ─── 报告可读 ───────────────────────────────────────────────────────────────
def test_report_lists_every_relevant_table_and_the_self_checks(tmp_path: Path) -> None:
    """报告要能被人读：列出八张表的「前 → 后」并列出通过的自校验项。"""
    data_dir = tmp_path / "data"
    _accident(data_dir)

    text = render_outcome(repair_target_rows(data_dir))

    for table in (*_SCOPED_TABLES, "channel", "channel_member", "channel_read", "channel_budget"):
        assert table in text
    assert "自校验通过" in text
    assert "每个频道都有 channel 型影子行" in text
    # 明确告诉用户旧表被保留（可回退），不被静默删除。
    assert "保留" in text


def test_repair_never_touches_channels_it_did_not_need_to(tmp_path: Path) -> None:
    """只补缺失的那个频道：另一个健康频道的数据原样不动（增量、不是全量重写）。"""
    data_dir = tmp_path / "data"
    with backend(data_dir) as client:
        agent = create_agent(client, name="小明").json()["agent"]
        healthy = channel_with_agent(client, agent["id"], name="healthy")
        broken = channel_with_agent(client, agent["id"], name="broken")
        send(client, healthy["id"], "健康频道")
        send(client, broken["id"], "坏频道")
    _half_rename_channel(data_dir, broken["id"])
    healthy_reads_before = _count_target_read_rows_for(data_dir, healthy["id"])
    assert healthy_reads_before == 2

    repair_target_rows(data_dir)

    assert _count_target_read_rows_for(data_dir, healthy["id"]) == healthy_reads_before
    with backend(data_dir) as client:
        for channel_id in (healthy["id"], broken["id"]):
            assert client.get(f"/api/channels/{channel_id}/members").status_code == 200
            assert len(messages(client, channel_id)) == 1


def test_database_class_still_works_normally_after_repair(tmp_path: Path) -> None:
    """补完之后库仍是普通当前 schema 库：``Database`` 正常打开，版本号没被动过。"""
    data_dir = tmp_path / "data"
    _accident(data_dir)

    repair_target_rows(data_dir)

    db = Database(data_dir)
    try:
        assert db._stored_schema_version() == str(SCHEMA_VERSION)
        assert db.list_channels() != []
    finally:
        db.close()
