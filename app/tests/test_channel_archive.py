"""频道归档与删除（#342 / ADR-0040）：两个软删除档位的 HTTP 黑盒契约。

判据（票面 Observable acceptance 2）全部经**人类面**取证——HTTP 端点、``GET /api/workspace``
的汇总读数、``GET /api/search`` 的命中。测试不读库表、不断言私有函数、不新增接缝：

- **归档**：``POST .../archive`` ⇒ 200，``GET /api/channels`` 里该频道带非空 ``archived_at``；
  再调一次幂等（``archived_at`` 逐字不变）。
- **只读**：归档后五个频道面写入口（消息 / 配置 / 预算 / 加成员 / 新建任务）各自 400
  ``validation_failed``，且**零写入**（再读一次读数与归档前一致）。
- **任务面不受影响**：归档频道的既有任务仍能改状态、仍能在任务线程发言（正面断言）。
- **取消归档**：``POST .../unarchive`` ⇒ ``archived_at`` 为 ``null``，写入口恢复可用；再调幂等。
- **删除**：``DELETE /api/channels/{id}`` ⇒ 列表里不再出现、``GET .../messages`` 404、
  二次 ``DELETE`` 404、``archive`` / ``unarchive`` 对已删除频道 404。
- **总未读**：归档频道里的未读**不计入** ``GET /api/workspace`` 的 ``unread_total``；
  取消归档后**重新计入**。
- **搜索**：已删除频道的消息不再出现在 ``GET /api/search``；归档频道的消息**仍然出现**。
- **迁移**：v25 形态的库跑 ``--migrate`` 后 ``channel`` 行逐字保留、两列为 ``NULL``、
  终态版本等于 ``SCHEMA_VERSION``。

未读的取证方式沿用 ``test_channel_read.py`` 的口径：**Agent 作者的回复**才算 owner 的未读
（``#4`` 的裁定），因此这里用「发一条人类消息 → ``drain`` → Agent 回复落库」制造未读。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alibot.db import SCHEMA_VERSION
from fastapi.testclient import TestClient

from tests.conftest import (
    backend,
    channel_with_agent,
    create_agent,
    create_channel,
    drain,
    messages,
    send,
)
from tests.test_schema_migration import _build_library, _migrate_argv, _run


# ─── 人类面动作的唯一写法（路由字面量与判据对齐） ────────────────────────────
def archive(client: TestClient, channel_id: str):
    return client.post(f"/api/channels/{channel_id}/archive")


def unarchive(client: TestClient, channel_id: str):
    return client.post(f"/api/channels/{channel_id}/unarchive")


def delete(client: TestClient, channel_id: str):
    return client.delete(f"/api/channels/{channel_id}")


def listed(client: TestClient) -> dict[str, dict]:
    payload = client.get("/api/channels").json()
    return {channel["id"]: channel for channel in payload["channels"]}


def unread_total(client: TestClient) -> int:
    return client.get("/api/workspace").json()["unread_total"]


def search_hits(client: TestClient, query: str) -> list[dict]:
    response = client.get("/api/search", params={"q": query})
    assert response.status_code == 200, response.text
    return response.json()["hits"]


def one_channel(client: TestClient, name: str = "设计频道") -> dict:
    return create_channel(client, name=name).json()["channel"]


# ─── 一、归档：读数、幂等、左栏载荷形状 ──────────────────────────────────────
def test_archive_moves_the_channel_out_of_the_live_list_but_keeps_it_listed(
    client: TestClient,
) -> None:
    """归档 ⇒ 列表里**仍有**这条频道，且带非空 ``archived_at`` / ``deleted_at = null``。

    「仍在列表里」是刻意的：归档不是删除，前端按 ``archived_at`` 把它放进「已归档」折叠段。
    """
    channel = one_channel(client)
    assert listed(client)[channel["id"]]["archived_at"] is None

    response = archive(client, channel["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["channel"]["id"] == channel["id"]
    assert body["channel"]["archived_at"], "归档后 archived_at 必须非空"
    assert body["channel"]["deleted_at"] is None
    # 响应自带整份频道名单（同 `POST /api/channels` 的纪律：一次调用就能刷新左栏）。
    assert [item["id"] for item in body["channels"]] == [channel["id"]]
    assert body["channels"][0]["archived_at"] == body["channel"]["archived_at"]

    fresh = listed(client)[channel["id"]]
    assert fresh["archived_at"] == body["channel"]["archived_at"]
    assert fresh["deleted_at"] is None


def test_archiving_twice_is_idempotent_and_keeps_the_first_timestamp(client: TestClient) -> None:
    """幂等：第二次归档 ⇒ 200，``archived_at`` **逐字不变**（时间戳只记第一次的那一刻）。"""
    channel = one_channel(client)
    first = archive(client, channel["id"]).json()["channel"]["archived_at"]

    again = archive(client, channel["id"])

    assert again.status_code == 200, again.text
    assert again.json()["channel"]["archived_at"] == first


# ─── 二、归档 = 频道面只读（五个入口 + 零写入） ──────────────────────────────
def _read_channel_facts(client: TestClient, channel_id: str) -> dict:
    """该频道的**所有可观察读数**：列表行、消息条数、配置、预算读数、成员表。"""
    payload = client.get(f"/api/channels/{channel_id}/messages").json()
    return {
        "row": listed(client)[channel_id],
        "messages": [message["seq"] for message in payload["messages"]],
        "config": client.get(f"/api/channels/{channel_id}/config").json()["config"],
        "budget": client.get(f"/api/channels/{channel_id}/budget").json()["budget"],
        "members": [
            member["id"] for member in client.get(f"/api/channels/{channel_id}/members").json()["members"]
        ],
    }


def test_every_channel_face_write_is_rejected_while_archived_and_writes_nothing(
    client: TestClient,
) -> None:
    """五个写入口各自 400 ``validation_failed``；**读数逐字不变**（零写入，不是「先写后拒」）。

    参与者用一个真实 Agent：否则「加成员」与「新建任务」会被别的校验拦下，测不到归档那道门。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    outsider = create_agent(client, name="小红").json()["agent"]
    channel = channel_with_agent(client, agent["id"], name="只读频道")
    archive(client, channel["id"])
    before = _read_channel_facts(client, channel["id"])

    attempts = {
        "message": client.post(f"/api/channels/{channel['id']}/messages", json={"body": "归档后还能发吗"}),
        "config": client.patch(
            f"/api/channels/{channel['id']}/config", json={"permission_mode": "read-only"}
        ),
        "budget": client.patch(f"/api/channels/{channel['id']}/budget", json={"budget_limit": 10}),
        "members": client.post(f"/api/channels/{channel['id']}/members", json={"member_id": outsider["id"]}),
        "tasks": client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "归档后的新任务"}),
    }
    for name, response in attempts.items():
        assert response.status_code == 400, f"{name}: {response.status_code} {response.text}"
        error = response.json()["error"]
        assert error["code"] == "validation_failed", f"{name}: {error}"
        # 拒绝文案是后端按语言偏好渲染的自然语言（非空、不是键名本身）。
        assert error["message"] and "error." not in error["message"], f"{name}: {error}"

    assert _read_channel_facts(client, channel["id"]) == before, "归档频道的写入口必须零写入"
    # 任务面也没有被建出东西（新建任务是被拒的那一个）。
    assert client.get("/api/tasks", params={"channel_id": channel["id"]}).json()["tasks"] == []


def test_task_plane_of_an_archived_channel_still_works(client: TestClient) -> None:
    """**刻意边界**（ADR-0040 决策 2）：归档的是「频道这个对话面」，任务自己的五态不受影响。

    正面断言两件事：既有任务仍能改状态、仍能在**任务线程**发言（后者的写入落在 task target，
    不是频道 target）。
    """
    channel = one_channel(client, name="带任务的频道")
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "先建后归档"}).json()["task"]
    archive(client, channel["id"])

    moved = client.patch(f"/api/tasks/{task['id']}/status", json={"status": "in_progress"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["task"]["status"] == "in_progress"

    replied = client.post(f"/api/tasks/{task['id']}/messages", json={"body": "归档后的任务线程发言"})
    assert replied.status_code == 201, replied.text
    assert replied.json()["message"]["body"] == "归档后的任务线程发言"


# ─── 三、取消归档：读数、幂等、写入口恢复 ────────────────────────────────────
def test_unarchive_restores_the_writable_face_and_is_idempotent(client: TestClient) -> None:
    channel = one_channel(client)
    archive(client, channel["id"])

    response = unarchive(client, channel["id"])

    assert response.status_code == 200, response.text
    assert response.json()["channel"]["archived_at"] is None
    assert response.json()["channels"][0]["archived_at"] is None

    again = unarchive(client, channel["id"])
    assert again.status_code == 200, again.text
    assert again.json()["channel"]["archived_at"] is None

    restored = send(client, channel["id"], "取消归档后又能发了")
    assert restored.status_code == 201, restored.text
    assert [message["body"] for message in messages(client, channel["id"])] == ["取消归档后又能发了"]


# ─── 四、删除：终态、不可见、不可恢复、不幂等 ────────────────────────────────
def test_delete_removes_the_channel_from_every_read_face_and_is_not_idempotent(
    client: TestClient,
) -> None:
    channel = one_channel(client, name="要删的频道")
    other = one_channel(client, name="留下的频道")

    response = delete(client, channel["id"])

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == channel["id"]
    assert [item["id"] for item in response.json()["channels"]] == [other["id"]]
    assert channel["id"] not in listed(client)
    # 每个频道面端点都「不存在」：读消息 404（与 get_channel 同一条早退）。
    assert client.get(f"/api/channels/{channel['id']}/messages").status_code == 404
    assert client.get(f"/api/channels/{channel['id']}/config").status_code == 404

    # 不幂等：第二次删除读取的是「已不存在」（与 `DELETE /api/agents/{id}` 同口径）。
    second = delete(client, channel["id"])
    assert second.status_code == 404, second.text
    assert second.json()["error"]["code"] == "not_found"
    # 归档 / 取消归档对已删除频道同样是 404：它已「不存在」，没有恢复这条路。
    assert archive(client, channel["id"]).status_code == 404
    assert unarchive(client, channel["id"]).status_code == 404


def test_delete_after_archive_clears_the_archive_tier(client: TestClient) -> None:
    """先归档后删除：终态不再需要 ``archived_at``，删除把它一并清掉（两档不是状态机）。"""
    channel = one_channel(client)
    assert archive(client, channel["id"]).status_code == 200

    assert delete(client, channel["id"]).status_code == 200

    assert channel["id"] not in listed(client)
    assert client.get(f"/api/channels/{channel['id']}/messages").status_code == 404


# ─── 五、总未读：归档不计入，取消归档重新计入 ────────────────────────────────
def test_archived_channel_unread_stops_counting_towards_the_sidebar_total(
    client: TestClient, harness: dict
) -> None:
    """归档频道的未读**不计入** ``unread_total``；取消归档后**重新计入**（同一份未读读数）。"""
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"], name="未读频道")
    harness["seam"].replies[agent["id"]] = "收到"

    send(client, channel["id"], "你好")
    drain(harness)
    row = listed(client)[channel["id"]]
    assert row["unread_count"] >= 1, "构造前提：频道里确实有 owner 未读"
    assert unread_total(client) == row["unread_count"]

    archive(client, channel["id"])
    assert unread_total(client) == 0, "归档频道的未读不该再催左栏徽标"
    # 频道自己的未读读数照旧（归档只影响汇总口径，不改事实）。
    assert listed(client)[channel["id"]]["unread_count"] == row["unread_count"]

    unarchive(client, channel["id"])
    assert unread_total(client) == row["unread_count"], "取消归档后重新计入"


# ─── 六、搜索：已删除不可搜，归档仍可搜 ──────────────────────────────────────
def test_deleted_channel_messages_leave_search_while_archived_ones_stay(client: TestClient) -> None:
    archived = one_channel(client, name="归档频道")
    deleted = one_channel(client, name="删除频道")
    send(client, archived["id"], "neon 在归档频道")
    send(client, deleted["id"], "neon 在删除频道")
    assert {hit["target"]["id"] for hit in search_hits(client, "neon")} == {
        archived["id"],
        deleted["id"],
    }

    archive(client, archived["id"])
    delete(client, deleted["id"])

    hits = search_hits(client, "neon")
    assert {hit["target"]["id"] for hit in hits} == {archived["id"]}, (
        "归档频道的历史仍可搜（只收走写入面），已删除频道的消息必须消失"
    )


# ─── 七、迁移 25 → 26：纯增量、不回填、行逐字保留 ────────────────────────────
def _channel_rows(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        columns = [str(row[1]) for row in conn.execute('PRAGMA table_info("channel")')]
        return [tuple(row) for row in conn.execute(f"SELECT {', '.join(columns)} FROM channel ORDER BY id")]
    finally:
        conn.close()


def _channel_columns(db_path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [str(row[1]) for row in conn.execute('PRAGMA table_info("channel")')]
    finally:
        conn.close()


def test_migrating_a_v25_library_adds_the_two_null_columns_and_keeps_every_row(
    tmp_path: Path,
) -> None:
    """v25 形态的库 ⇒ 迁移后 ``channel`` 行逐字保留、两列全 ``NULL``、版本到本代码的常量。

    这是「纯增量迁移」的直接判据：不重建表（行内容逐字相同）、不回填（两列都 ``NULL``）。
    """
    data_dir = tmp_path / "data"
    db_path = _build_library(data_dir, 25)
    assert "archived_at" not in _channel_columns(db_path), "构造前提：v25 形态没有这两列"
    before = _channel_rows(db_path)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == 0, f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    columns = _channel_columns(db_path)
    assert "archived_at" in columns and "deleted_at" in columns
    after = _channel_rows(db_path)
    assert [row[: len(before[0])] for row in after] == before, "既有列的值必须逐字保留"
    archived_at, deleted_at = (columns.index("archived_at"), columns.index("deleted_at"))
    assert all(row[archived_at] is None and row[deleted_at] is None for row in after), "不回填"

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        stored = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]
    finally:
        conn.close()
    assert stored == str(SCHEMA_VERSION)


def test_migrated_library_serves_the_new_endpoints(tmp_path: Path) -> None:
    """迁移后的库由本代码打开时，三个新端点可用（迁移不是只改形态，行为面也得接上）。"""
    data_dir = tmp_path / "data"
    _build_library(data_dir, 25)
    assert _run(*_migrate_argv(data_dir)).returncode == 0

    with backend(data_dir) as client:
        payload = client.get("/api/channels").json()["channels"]
        assert payload, "v25 库里有频道行"
        channel_id = payload[0]["id"]
        assert payload[0]["archived_at"] is None

        assert archive(client, channel_id).status_code == 200
        assert unarchive(client, channel_id).status_code == 200
        assert delete(client, channel_id).status_code == 200
        assert channel_id not in listed(client)


# ─── 八、三个新动作是配置类写路径（不落消息、不唤醒、不消耗预算） ────────────
def test_archive_and_delete_do_not_touch_the_message_stream_or_the_budget(
    client: TestClient, harness: dict
) -> None:
    """三个动作**不落消息、不唤醒、不消耗预算**：消息条数、轮次结果、预算读数逐字不变。

    这是它们与「发言」的分界线（同 ``DELETE /api/agents/{id}`` 的纪律）：它们是整理动作，
    不是对话事实。
    """
    agent = create_agent(client, name="小明").json()["agent"]
    channel = channel_with_agent(client, agent["id"], name="安静频道")
    harness["seam"].replies[agent["id"]] = "收到"
    send(client, channel["id"], "先制造一个人类世代")
    drain(harness)
    before_messages = [message["seq"] for message in messages(client, channel["id"])]
    before_budget = client.get(f"/api/channels/{channel['id']}/budget").json()["budget"]
    before_outcomes = client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]

    archive(client, channel["id"])
    unarchive(client, channel["id"])

    assert [message["seq"] for message in messages(client, channel["id"])] == before_messages
    assert client.get(f"/api/channels/{channel['id']}/budget").json()["budget"] == before_budget
    assert (
        client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        == before_outcomes
    )
    assert drain(harness) == [], "归档 / 取消归档不唤醒任何成员（没有待跑轮次）"


def test_create_channel_still_reports_the_two_new_fields(client: TestClient) -> None:
    """``POST /api/channels`` 与 ``GET /api/workspace`` 的载荷同形：新频道两档都是 ``null``。

    前端只有一套形状判断（不在「新建回来的行」和「列表行」之间切换），这条守的就是那个形状。
    """
    created = one_channel(client)
    assert created["archived_at"] is None and created["deleted_at"] is None

    snapshot = client.get("/api/workspace").json()["channels"][0]
    assert snapshot["archived_at"] is None and snapshot["deleted_at"] is None
