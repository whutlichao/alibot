"""工作台搜索：只经人类 HTTP 面断言跨 target 的命中契约。"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, send


def _db(client: TestClient):
    """搜索需要构造没有人类面写入口的 soft-delete 历史夹具。"""
    return cast(FastAPI, client.app).state.db


def search(
    client: TestClient,
    query: str,
    *,
    target_id: str | None = None,
    limit: int | None = None,
) -> dict:
    """通过公开搜索面读取命中；测试不自行扫描正文或重算高亮。"""
    params: dict[str, str | int] = {"q": query}
    if target_id is not None:
        params["target_id"] = target_id
    if limit is not None:
        params["limit"] = limit
    response = client.get("/api/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_workspace_search_covers_channel_dm_and_task_and_can_be_limited_to_one_target(
    client: TestClient,
) -> None:
    agent = create_agent(client, name="私信对象").json()["agent"]
    channel = create_channel(client, name="协作频道").json()["channel"]
    dm = client.post("/api/dms", json={"agent_id": agent["id"]}).json()["dm"]
    task = client.post(f"/api/channels/{channel['id']}/tasks", json={"title": "搜索任务"}).json()["task"]

    channel_message = send(client, channel["id"], "Needle 在频道").json()["message"]
    dm_message = client.post(f"/api/dms/{dm['id']}/messages", json={"body": "needle 在私信"}).json()[
        "message"
    ]
    task_message = client.post(
        f"/api/tasks/{task['id']}/messages", json={"body": "NEEDLE 在任务线程"}
    ).json()["message"]

    hits = search(client, "nEeDlE")["hits"]
    by_target = {hit["target"]["id"]: hit for hit in hits}

    assert set(by_target) == {channel["id"], dm["id"], task["target_id"]}
    assert by_target[channel["id"]]["message"]["id"] == channel_message["id"]
    assert by_target[channel["id"]]["target"] == {
        "id": channel["id"],
        "kind": "channel",
        "display_name": "协作频道",
    }
    assert by_target[dm["id"]]["message"]["id"] == dm_message["id"]
    assert by_target[dm["id"]]["target"] == {
        "id": dm["id"],
        "kind": "dm",
        "display_name": "私信对象",
    }
    assert by_target[task["target_id"]]["message"]["id"] == task_message["id"]
    assert by_target[task["target_id"]]["target"] == {
        "id": task["target_id"],
        "kind": "task",
        "display_name": "搜索任务",
    }

    limited = search(client, "needle", target_id=dm["id"])["hits"]
    assert [hit["message"]["id"] for hit in limited] == [dm_message["id"]]


def test_search_returns_all_backend_highlight_ranges_and_uses_strict_substring_matching(
    client: TestClient,
) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "Alpha alpha ALPHA").json()["message"]
    send(client, channel["id"], "search——product 搜索")

    hits = search(client, "aLpHa")["hits"]
    assert hits == [
        {
            "message": {
                **message,
                "author": {**message["author"], "deleted": False},
            },
            "target": {"id": channel["id"], "kind": "channel", "display_name": "设计系统重构"},
            "highlight_ranges": [
                {"start": 0, "end": 5},
                {"start": 6, "end": 11},
                {"start": 12, "end": 17},
            ],
        }
    ]

    # 负向边界：不是拼音、不是模糊，也不把隔开的词当作分词命中。
    assert search(client, "sousuo")["hits"] == []
    assert search(client, "searhc")["hits"] == []
    assert search(client, "search product")["hits"] == []


def test_search_casefolds_unicode_without_drifting_highlight_ranges(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "😀 ÄLPHA İX").json()["message"]

    accented = search(client, "älpha")["hits"]
    assert [hit["message"]["id"] for hit in accented] == [message["id"]]
    assert accented[0]["highlight_ranges"] == [{"start": 3, "end": 8}]

    x_hit = search(client, "x")["hits"]
    assert [hit["message"]["id"] for hit in x_hit] == [message["id"]]
    # emoji 占两个 UTF-16 code unit，İ 的 casefold 会展开；后端区间仍须精确落到 X。
    assert x_hit[0]["highlight_ranges"] == [{"start": 10, "end": 11}]


def test_search_casefold_expansion_does_not_emit_overlapping_source_ranges(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "sßs").json()["message"]

    hits = search(client, "ss")["hits"]

    assert [hit["message"]["id"] for hit in hits] == [message["id"]]
    assert hits[0]["highlight_ranges"] == [{"start": 0, "end": 2}]


def test_search_reports_every_non_overlapping_match(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "banana").json()["message"]

    hits = search(client, "ana")["hits"]
    assert [hit["message"]["id"] for hit in hits] == [message["id"]]
    # 两次 ana 重叠；产品裁定为标准非重叠命中，所以只标出首个区间 [1, 4)。
    assert hits[0]["highlight_ranges"] == [{"start": 1, "end": 4}]


def test_search_treats_like_wildcards_as_literal_substrings(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], r"100%_safe\path").json()["message"]

    for query, expected_starts in (("%", [3]), ("_", [4]), ("\\", [9])):
        hits = search(client, query)["hits"]
        assert [hit["message"]["id"] for hit in hits] == [message["id"]]
        assert hits[0]["highlight_ranges"] == [
            {"start": start, "end": start + 1} for start in expected_starts
        ]


def test_search_preserves_whitespace_as_part_of_the_literal_query(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    message = send(client, channel["id"], "needle").json()["message"]

    assert [hit["message"]["id"] for hit in search(client, "needle")["hits"]] == [message["id"]]
    assert search(client, " needle")["hits"] == []
    assert search(client, "needle ")["hits"] == []


def test_search_excludes_system_messages_but_marks_a_soft_deleted_author(client: TestClient) -> None:
    agent = create_agent(client, name="已删除作者").json()["agent"]
    channel = create_channel(client, member_ids=[agent["id"]]).json()["channel"]
    database = _db(client)

    # 没有删除成员的人类面入口；为验证历史渲染语义，先用确定性库夹具留下消息，再标成 soft-delete。
    message = database.append_message(
        channel["id"], "needle 来自已删除作者", author_member_id=agent["id"], kind="agent"
    )
    database.append_message(channel["id"], "needle 系统事件", kind="system")
    with database._lock:
        database._conn.execute(
            "UPDATE member SET deleted_at = ? WHERE id = ?", ("2026-10-15T00:00:00Z", agent["id"])
        )

    hits = search(client, "needle")["hits"]
    assert [hit["message"]["id"] for hit in hits] == [message["id"]]
    assert hits[0]["message"]["author"] == {**message["author"], "deleted": True}


def test_search_caps_the_result_count(client: TestClient) -> None:
    channel = create_channel(client).json()["channel"]
    for index in range(3):
        send(client, channel["id"], f"bounded needle {index}")

    hits = search(client, "needle", limit=2)["hits"]
    assert len(hits) == 2
    assert all("needle" in hit["message"]["body"] for hit in hits)

    invalid_limit = client.get("/api/search", params={"q": "needle", "limit": 201})
    assert invalid_limit.status_code == 400
    assert invalid_limit.json()["error"]["code"] == "validation_failed"


def test_search_does_not_turn_an_invalid_target_into_zero_hits(client: TestClient) -> None:
    response = client.get("/api/search", params={"q": "needle", "target_id": "missing-target"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
