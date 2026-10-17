"""提醒（面 8 / #68）的 HTTP 黑盒契约：库内对象 + 增删读三个端点。

只断言**外部可观察行为**：``GET`` / ``POST`` / ``DELETE /api/reminders`` 的读数、非法输入的
拒绝形态、重启后一致，以及「提醒不是对话事实」这条边界（ADR-0012 / D18）：增删提醒**不落消息、
不唤醒、不消耗预算**，也不新增 target、不计未读。测试不连数据库、不读表结构。

两条与设置面（#61）**刻意不同**的取值语义，各自有用例钉住：

- 时间是**一次性**的「日期 + 钟点」：合法形状是 24 小时制、零填充的 ``YYYY-MM-DD HH:MM``（ADR-0017
  §二把时间语义从「只有钟点」升为「日期 + 钟点」）；**没有重复字段**（「每天 / 每周」与单独的
  ``date`` 键都不是本能力的东西，给了就整体拒绝，不静默忽略）。
- ``channel_id`` 的显式 ``null`` **是有含义的取值**——「不归属任何频道」；设置面那条「显式 null
  一律拒绝」的理由（档位没有「不归属」这个合法状态）在这里不成立。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_agent, create_channel, messages, send


def reminders(client: TestClient) -> list[dict]:
    return client.get("/api/reminders").json()["reminders"]


def workspace_snapshot(client: TestClient) -> dict:
    """工作台快照（成员 / 频道 / 私信 / Agent / 未读合计 / last_seq）。

    它是「提醒不新增 target、不计未读」这条边界**在 HTTP 面能拿到的最强读数**：三种 target
    （channel / dm / task）与未读都在这一份里，任何新增作用域或未读变化都会让这份快照不等。
    """
    return client.get("/api/workspace").json()


def channel_reading(client: TestClient, channel_id: str) -> dict:
    """频道列表里那一行（`last_seq` / `last_read_seq` / `unread_count` 的读数点）。"""
    return next(
        channel for channel in client.get("/api/channels").json()["channels"] if channel["id"] == channel_id
    )


def test_empty_workspace_has_no_reminders(client: TestClient) -> None:
    """空库 = 空提醒表；读端点在空态下也必须可读（不是一个查不到的错误）。"""
    assert reminders(client) == []


def test_create_list_delete_round_trip(client: TestClient) -> None:
    channel = create_channel(client, name="设计系统重构").json()["channel"]

    created = client.post(
        "/api/reminders",
        json={"text": "检查令牌替换进度", "time": "2026-10-18 14:30", "channel_id": channel["id"]},
    )

    assert created.status_code == 201
    reminder = created.json()["reminder"]
    # 列的形状：时间 + 文本 + 归属频道（可读名随行带出，前端不必自己去查频道表）。
    assert reminder["text"] == "检查令牌替换进度"
    assert reminder["time"] == "2026-10-18 14:30"
    assert reminder["channel_id"] == channel["id"]
    assert reminder["channel_name"] == "设计系统重构"
    assert reminder["created_at"]
    # 写路径的载荷带**最新**全表：前端一次调用就能重绘，不必「先写再读」。
    assert created.json()["reminders"] == [reminder]
    assert reminders(client) == [reminder]

    deleted = client.delete(f"/api/reminders/{reminder['id']}")

    assert deleted.status_code == 200
    assert deleted.json()["reminder"] == reminder
    assert deleted.json()["reminders"] == []
    assert reminders(client) == []


def test_reminder_without_channel_has_no_affiliation(client: TestClient) -> None:
    """「**可**归属一个频道」：省略即不归属——空工作台（还没有频道）里也设得成提醒。"""
    created = client.post("/api/reminders", json={"text": "看一眼构建", "time": "2026-10-18 09:00"})

    assert created.status_code == 201
    reminder = created.json()["reminder"]
    assert reminder["channel_id"] is None
    assert reminder["channel_name"] == ""


def test_explicit_null_channel_id_means_unaffiliated(client: TestClient) -> None:
    """显式 ``null`` 在这里是**合法取值**（不归属），与设置面「显式 null 一律拒绝」不同。"""
    created = client.post(
        "/api/reminders", json={"text": "不归属", "time": "2026-10-18 09:00", "channel_id": None}
    )

    assert created.status_code == 201
    assert created.json()["reminder"]["channel_id"] is None


def test_listing_keeps_creation_order_and_returns_every_reminder(client: TestClient) -> None:
    channel = create_channel(client, name="后端").json()["channel"]
    for text in ("第一条", "第二条", "第三条"):
        assert (
            client.post(
                "/api/reminders",
                json={"text": text, "time": "2026-10-18 09:00", "channel_id": channel["id"]},
            ).status_code
            == 201
        )

    assert [item["text"] for item in reminders(client)] == ["第一条", "第二条", "第三条"]


def test_reminders_survive_backend_restart(launched: dict) -> None:
    """重启 = 换一个进程读同一个数据目录：提醒仍在（后端是唯一正本，不在这台浏览器里）。"""
    client: TestClient = launched["client"]
    channel = create_channel(client, name="设计系统重构").json()["channel"]
    before = client.post(
        "/api/reminders",
        json={"text": "检查令牌替换进度", "time": "2026-10-18 14:30", "channel_id": channel["id"]},
    ).json()["reminder"]

    restarted = launched["restart"]()

    assert restarted.get("/api/reminders").json()["reminders"] == [before]


def test_invalid_input_is_rejected_with_a_code_and_creates_nothing(client: TestClient) -> None:
    cases = [
        ({"text": "   ", "time": "2026-10-18 09:00"}, "text"),
        ({"text": "x", "time": ""}, "time"),
        ({"text": "x", "time": "2026-10-18 9:00"}, "time"),
        ({"text": "x", "time": "24:00"}, "time"),
        ({"text": "x", "time": "09:60"}, "time"),
        ({"text": "x", "time": "09:00:00"}, "time"),
        ({"text": "x" * 201, "time": "2026-10-18 09:00"}, "text"),
        ({"text": "x", "time": "2026-10-18 09:00", "channel_id": "ch_not_here"}, "channel_id"),
    ]

    for body, field in cases:
        response = client.post("/api/reminders", json=body)

        assert response.status_code == 400, f"{body!r} 应被拒绝"
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert field in error["fields"], f"{body!r} 的字段级说明应指出 {field}"

    # 校验先于写入：一个非法字段也不留半成品行。
    assert reminders(client) == []


def test_unknown_and_out_of_scope_keys_are_rejected_whole(client: TestClient) -> None:
    """未知键、以及**本票不做**的日期 / 重复字段：整体拒绝，不静默忽略。"""
    channel = create_channel(client, name="设计系统重构").json()["channel"]

    for extra in ({"theme": "dark"}, {"date": "2026-10-17"}, {"repeat": "daily"}):
        response = client.post(
            "/api/reminders",
            json={"text": "x", "time": "2026-10-18 09:00", "channel_id": channel["id"], **extra},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_failed"

    assert reminders(client) == []


def test_deleting_an_unknown_reminder_is_404(client: TestClient) -> None:
    response = client.delete("/api/reminders/rm_not_here")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_deleting_leaves_the_other_reminders_alone(client: TestClient) -> None:
    first = client.post("/api/reminders", json={"text": "第一条", "time": "2026-10-18 09:00"}).json()[
        "reminder"
    ]
    second = client.post("/api/reminders", json={"text": "第二条", "time": "2026-10-18 10:00"}).json()[
        "reminder"
    ]

    deleted = client.delete(f"/api/reminders/{first['id']}")

    assert deleted.json()["reminders"] == [second]
    assert reminders(client) == [second]


def test_reminder_writes_leave_production_facts_untouched(launched: dict) -> None:
    """提醒**不是对话事实**（ADR-0012 / D18）：增删前后三处读数逐字不变。

    三处 = 消息条数 / 轮次结果条数 / 该频道人类世代的 ``target_budget.used``（ADR-0007 的边界：
    人类面可以写配置类取值，**不得**写用量类事实）。
    """
    client: TestClient = launched["client"]
    agent_id = create_agent(client, name="提醒面验证").json()["agent"]["id"]
    channel = create_channel(client, name="提醒面", member_ids=[agent_id]).json()["channel"]
    send(client, channel["id"], "一条人类消息")
    launched["scheduler"].drain()

    def facts() -> tuple[int, int, int]:
        listed = messages(client, channel["id"])
        outcomes = client.get(f"/api/channels/{channel['id']}/round-outcomes").json()["round_outcomes"]
        budget = client.get(f"/api/channels/{channel['id']}/budget").json()["budget"]
        return len(listed), len(outcomes), budget["used"]

    before = facts()
    assert before[0] > 0 and before[1] > 0  # 先造出真实活动，再证明提醒不改变它

    created = client.post(
        "/api/reminders",
        json={"text": "检查令牌替换进度", "time": "2026-10-18 14:30", "channel_id": channel["id"]},
    )
    assert created.status_code == 201
    assert facts() == before, "新增提醒不得落消息、不得唤醒、不得消耗预算"

    assert client.delete(f"/api/reminders/{created.json()['reminder']['id']}").status_code == 200
    assert facts() == before, "删除提醒同样不得落消息、不得唤醒、不得消耗预算"


def test_reminder_writes_add_no_target_and_no_unread(launched: dict) -> None:
    """对照读数：提醒**不新增 target、不计未读**。

    读数是工作台快照（三种 target 与未读合计都在它里面）+ 该频道的未读计数。任一新增作用域
    （channel / dm / task）或任一未读变化都会让这两份读数不等——提醒与频道的关系是**归属**，
    不是 target 作用域（ADR-0012）。
    """
    client: TestClient = launched["client"]
    agent_id = create_agent(client, name="未读对照").json()["agent"]["id"]
    channel = create_channel(client, name="提醒面", member_ids=[agent_id]).json()["channel"]
    send(client, channel["id"], "一条人类消息")
    launched["scheduler"].drain()

    snapshot_before = workspace_snapshot(client)
    unread_before = channel_reading(client, channel["id"])["unread_count"]
    tasks_before = client.get("/api/tasks").json()["tasks"]

    created = client.post(
        "/api/reminders",
        json={"text": "检查令牌替换进度", "time": "2026-10-18 14:30", "channel_id": channel["id"]},
    )
    assert created.status_code == 201

    assert workspace_snapshot(client) == snapshot_before
    assert channel_reading(client, channel["id"])["unread_count"] == unread_before
    assert client.get("/api/tasks").json()["tasks"] == tasks_before

    client.delete(f"/api/reminders/{created.json()['reminder']['id']}")

    assert workspace_snapshot(client) == snapshot_before
    assert channel_reading(client, channel["id"])["unread_count"] == unread_before


def test_reminder_copy_follows_the_stored_locale(client: TestClient) -> None:
    """新增的提醒文案按**库内语言偏好**渲染（#62 / ADR-0009 第二层的口径；本票不另开一条路径）。

    断言方式与 `test_backend_copy.py` 同款：比对的不是某一句具体译文，而是**两种语言的读数不同**
    且各自取自该语言的词条（`alibot.i18n.render`）；机器可读的 `code` 与 `fields` 的**键**在两种
    语言下逐字相同。这里直接读词条表的原因与那份文件一样——它是**数据契约**，不经 HTTP 面暴露。
    """
    from alibot import i18n

    def error(payload: dict) -> dict:
        return client.post("/api/reminders", json=payload).json()["error"]

    zh_invalid = error({"text": "   ", "time": "2026-10-18 9:00"})
    zh_missing = client.delete("/api/reminders/rm_nope").json()["error"]

    client.patch("/api/settings", json={"locale": "en"})

    en_invalid = error({"text": "   ", "time": "2026-10-18 9:00"})
    en_missing = client.delete("/api/reminders/rm_nope").json()["error"]

    # code 与 fields 的键是机器可读的：两种语言下逐字相同。
    assert en_invalid["code"] == zh_invalid["code"] == "validation_failed"
    assert sorted(en_invalid["fields"]) == sorted(zh_invalid["fields"]) == ["text", "time"]
    assert en_missing["code"] == zh_missing["code"] == "not_found"

    # 自然语言按各自的语言取值（措辞可变，契约是「取自那一份词条」）。
    assert zh_invalid["message"] == i18n.render("error.reminder_invalid", "zh-CN")
    assert en_invalid["message"] == i18n.render("error.reminder_invalid", "en")
    assert zh_invalid["message"] != en_invalid["message"]
    assert zh_invalid["fields"]["text"] == i18n.render("error.reminder_text_empty", "zh-CN")
    assert en_invalid["fields"]["text"] == i18n.render("error.reminder_text_empty", "en")
    assert zh_invalid["fields"]["time"] == i18n.render("error.reminder_time_shape", "zh-CN")
    assert en_invalid["fields"]["time"] == i18n.render("error.reminder_time_shape", "en")
    assert zh_missing["message"] == i18n.render("error.reminder_not_found", "zh-CN", reminder_id="rm_nope")
    assert en_missing["message"] == i18n.render("error.reminder_not_found", "en", reminder_id="rm_nope")

    # 归属频道的字段文案复用既有的 `error.channel_not_found`，不另立一条。
    en_channel = error({"text": "x", "time": "2026-10-18 09:00", "channel_id": "ch_nope"})
    assert en_channel["fields"]["channel_id"] == i18n.render(
        "error.channel_not_found", "en", channel_id="ch_nope"
    )


def test_reminder_text_too_long_message_carries_the_limit(client: TestClient) -> None:
    """上限那一句把**上限本身**作为参数带出来（两种语言同一参数位）。"""
    from alibot import i18n

    for locale in ("zh-CN", "en"):
        client.patch("/api/settings", json={"locale": locale})
        error = client.post("/api/reminders", json={"text": "x" * 201, "time": "2026-10-18 09:00"}).json()[
            "error"
        ]

        assert error["fields"]["text"] == i18n.render("error.reminder_text_too_long", locale, limit=200)
