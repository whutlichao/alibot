"""后端可见文案按**语言偏好**渲染（实施票 #62 / ADR-0009 第二层）的 HTTP 黑盒契约。

断言的是**同一动作在两种语言下的读数**，而不是某一句具体译文：措辞可变，契约是「文案只有两个
落点、且第二层（后端生成的自然语言）按库内的语言偏好取值」。因此每条用例都比对两种语言的读数
**不同**、并核对它取自**该语言的词条取值**（``alibot.i18n.render``）；机器可读的 ``code`` 与
``fields`` 的**键**在两种语言下必须逐字相同（正文可译、代码不可译）。

四条验收判据（票面）：

1. 同一动作在两种语言下产出的 ``system`` 消息正文 / 错误 ``message`` / ``reason`` /
   ``enabled_reason`` / 远端告知文案**取值不同**，而 ``code`` 与 ``fields`` 的键**相同**；
2. **不带任何请求头**的编排路径（手动调度器）产出的文案用当前偏好——两种偏好各跑一次给读数，
   并额外断言「带着 ``Accept-Language: en`` 也一样」（语言偏好读库，绝不读请求头）；
3. **历史不回填**：先以语言 A 产出若干消息，切到语言 B，再断言此前落库的正文**逐字不变**；
4. 库内没有**用户选过**的偏好时回落到**源语言**（简体中文）。

唯一直接 import 实现模块的是键集比对（``alibot.i18n``）：词条表是**数据契约**，它的「两份取值
覆盖同一键集、无落单键」不经 HTTP 面暴露，因此与 #61 的前端词条表同款做法——直接读那份数据。
其余用例一律只经 HTTP 面读数。
"""

from __future__ import annotations

import string

from alibot import i18n
from fastapi.testclient import TestClient

from tests.conftest import (
    REMOTE_MODEL_ID,
    budget,
    channel_with_agent,
    create_agent,
    create_channel,
    drain,
    messages,
    outcomes,
    send,
)
from tests.scripted import IGNORE

ZH = "zh-CN"
EN = "en"


def use_locale(client: TestClient, locale: str) -> None:
    """写语言偏好（人类面动作；正本在库里，后端产出文案时读它）。"""
    response = client.patch("/api/settings", json={"locale": locale})
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["locale"] == locale


def fields_of(template: str) -> set[str]:
    """取一份取值里的具名参数（``{name}``）——两份取值必须用同一组参数，否则取值会缺参。"""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def create_task(client: TestClient, channel_id: str, title: str) -> str:
    response = client.post(f"/api/channels/{channel_id}/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["task"]["id"]


def task_bodies(client: TestClient, task_id: str) -> list[str]:
    payload = client.get(f"/api/tasks/{task_id}/messages").json()
    return [message["body"] for message in payload["messages"]]


def test_two_catalogs_cover_the_same_keyset_with_the_same_parameters() -> None:
    source = i18n.CATALOGS[i18n.SOURCE_LOCALE]
    second = i18n.CATALOGS[EN]

    assert i18n.SOURCE_LOCALE == ZH
    # 两份取值覆盖**同一个键集、无落单键**（缺一个键就没有第二语言的文案；多一个键是死词条）。
    assert set(source) == set(second)
    for key in source:
        assert source[key].strip(), f"源语言缺取值：{key}"
        assert second[key].strip(), f"第二语言缺取值：{key}"
        # 参数位也必须一致：只翻译句子、不改参数位，才可能两边都渲染得出来。
        assert fields_of(source[key]) == fields_of(second[key]), key


def test_http_error_message_and_field_values_follow_the_locale_while_keys_do_not(
    client: TestClient,
) -> None:
    """正文（``message`` 与 ``fields`` 的值）可译；``code`` 与 ``fields`` 的键不可译。"""
    channel = create_channel(client).json()["channel"]["id"]

    def readings(locale: str) -> tuple[dict, dict, dict]:
        use_locale(client, locale)
        bad_model = create_agent(client, model_id="qwen-max").json()["error"]
        bad_provider = client.post("/api/remote-data-flow/ack", json={"provider": "没有这个"}).json()["error"]
        no_endpoint = client.put(f"/api/channels/{channel}/round-outcomes", json={}).json()["error"]
        return bad_model, bad_provider, no_endpoint

    zh_model, zh_provider, zh_endpoint = readings(ZH)
    en_model, en_provider, en_endpoint = readings(EN)

    for zh_error, en_error in ((zh_model, en_model), (zh_provider, en_provider), (zh_endpoint, en_endpoint)):
        # 机器契约不动：code 与 fields 的**键**逐字相同（正文是另一层的事）。
        assert zh_error["code"] == en_error["code"]
        assert set(zh_error.get("fields", {})) == set(en_error.get("fields", {}))
        # 自然语言按语言取值。
        assert zh_error["message"] != en_error["message"]
        if "fields" in zh_error:  # 404 一族本体没有 fields（形状不变，不凭空造一个）
            assert zh_error["fields"] != en_error["fields"]

    assert zh_model["fields"]["model_id"] == i18n.render(
        "error.model_id_provider_prefix", ZH, model_id="qwen-max"
    )
    assert en_model["fields"]["model_id"] == i18n.render(
        "error.model_id_provider_prefix", EN, model_id="qwen-max"
    )


def test_request_body_validation_detail_follows_the_locale(client: TestClient) -> None:
    """请求体校验失败时 ``fields.body`` 也是按偏好渲染的正文，不是 pydantic 的原始 repr。

    前端 `readError` 会把 ``fields`` 的值拼进错误提示，因此这里放一段英文 repr 就等于在中文界面里
    露出英文；而机器可读的**标识**（出错位置、pydantic 的错误类型）保持原样不译。
    """
    channel = create_channel(client).json()["channel"]["id"]

    def readings(locale: str) -> tuple[dict, dict]:
        use_locale(client, locale)
        wrong_type = client.post(f"/api/channels/{channel}/messages", json={"body": 5}).json()["error"]
        null_tier = client.patch("/api/settings", json={"locale": None}).json()["error"]
        return wrong_type, null_tier

    zh_wrong, zh_null = readings(ZH)
    en_wrong, en_null = readings(EN)

    for zh_error, en_error in ((zh_wrong, en_wrong), (zh_null, en_null)):
        assert zh_error["code"] == en_error["code"] == "validation_failed"
        assert set(zh_error["fields"]) == set(en_error["fields"])  # 键不译
        assert zh_error["message"] != en_error["message"]
        assert zh_error["fields"] != en_error["fields"]
        # 正文是句子，不是 Python 结构：repr 里的那些键不再出现。
        assert "msg" not in zh_error["fields"]["body"]
        assert "loc" not in zh_error["fields"]["body"]
        assert "input" not in zh_error["fields"]["body"]

    # 出错位置与错误类型是**标识**：两种语言里逐字相同。
    for body in (zh_wrong["fields"]["body"], en_wrong["fields"]["body"]):
        assert "body.body" in body and "string_type" in body
    # 自定义校验器（显式 null）带回词条键，由边界按偏好渲染成整句。
    assert zh_null["fields"]["body"] == i18n.render("error.tier_value_null", ZH)
    assert en_null["fields"]["body"] == i18n.render("error.tier_value_null", EN)


def test_task_system_message_body_follows_the_locale_at_write_time(client: TestClient) -> None:
    """落库的 ``system`` 正文按**写入时**的语言偏好取值（两语言各产一条读回来比对）。"""
    channel = create_channel(client).json()["channel"]["id"]

    use_locale(client, ZH)
    zh_task = create_task(client, channel, "中文任务")

    use_locale(client, EN)
    en_task = create_task(client, channel, "English task")

    assert task_bodies(client, zh_task)[0] == i18n.render("system.task.created", ZH, title="中文任务")
    assert task_bodies(client, en_task)[0] == i18n.render("system.task.created", EN, title="English task")
    assert task_bodies(client, zh_task)[0] != task_bodies(client, en_task)[0]


def test_task_status_title_and_assignee_messages_follow_the_locale(client: TestClient) -> None:
    """改状态 / 改标题 / 改指派三条系统消息同样按偏好取值（含「穿透加入」之外的四个来源）。"""
    agent = create_agent(client, name="阿蓝").json()["agent"]
    channel = create_channel(client).json()["channel"]["id"]

    def write_a_thread(locale: str) -> list[str]:
        use_locale(client, locale)
        task_id = create_task(client, channel, "标题 A")
        client.patch(f"/api/tasks/{task_id}/status", json={"status": "in_progress"})
        client.patch(f"/api/tasks/{task_id}/title", json={"title": "标题 B"})
        client.patch(f"/api/tasks/{task_id}/assignee", json={"member_id": agent["id"]})
        return task_bodies(client, task_id)

    zh_bodies = write_a_thread(ZH)
    en_bodies = write_a_thread(EN)

    assert len(zh_bodies) == len(en_bodies) == 4
    for index, (zh_body, en_body) in enumerate(zip(zh_bodies, en_bodies, strict=True)):
        assert zh_body != en_body, f"第 {index} 条系统消息没有随语言变"
    assert zh_bodies[1] == i18n.render(
        "system.task.status", ZH, status=i18n.render("status.task.in_progress", ZH)
    )
    assert en_bodies[1] == i18n.render(
        "system.task.status", EN, status=i18n.render("status.task.in_progress", EN)
    )
    assert zh_bodies[2] == i18n.render("system.task.title", ZH, title="标题 B")
    assert en_bodies[3] == i18n.render("system.task.assignee", EN, name="阿蓝")


def test_remote_data_flow_notice_and_faces_follow_the_locale(client: TestClient) -> None:
    """远端数据流告知（当面文案 + 数据面清单）按偏好取值；事实字段不变。"""
    use_locale(client, ZH)
    zh_notice = client.get("/api/remote-data-flow").json()
    use_locale(client, EN)
    en_notice = client.get("/api/remote-data-flow").json()

    assert zh_notice["statement"] != en_notice["statement"]
    assert zh_notice["data_faces"] != en_notice["data_faces"]
    assert len(zh_notice["data_faces"]) == len(en_notice["data_faces"])
    # 事实（不译）不随语言变：目的地、本机提供商、两个布尔事实。
    assert zh_notice["destinations"] == en_notice["destinations"]
    assert zh_notice["local_providers"] == en_notice["local_providers"]
    assert zh_notice["end_to_end_encrypted"] == en_notice["end_to_end_encrypted"] is False
    assert zh_notice["loopback_only"] == en_notice["loopback_only"] is True


def test_enabled_reason_follows_the_locale(client: TestClient) -> None:
    """``enabled_reason``（后端事实的「为什么未启用」）按偏好取值。"""
    agent_id = create_agent(client, model_id=REMOTE_MODEL_ID).json()["agent"]["id"]

    def reason(locale: str) -> str:
        use_locale(client, locale)
        return client.get(f"/api/agents/{agent_id}").json()["agent"]["enabled_reason"]

    zh_reason = reason(ZH)
    en_reason = reason(EN)

    assert zh_reason and en_reason
    assert zh_reason != en_reason
    # 同一条事实的两种说法都点名同一个提供商（标识符不译）。
    assert "dashscope" in zh_reason and "dashscope" in en_reason


def test_round_reason_on_the_orchestration_path_uses_the_stored_locale(
    client: TestClient, harness: dict
) -> None:
    """**不带任何请求头**的编排路径（手动调度器）产出的 ``reason`` 用当前偏好。

    唤醒由 HTTP 请求触发，但轮次跑在请求之外（手动调度器里）——那条路径没有请求上下文，
    这正是 ADR-0009 把语言偏好落库的决定性理由。两种偏好各跑一次给读数。
    """
    agent = create_agent(client, name="被点名的人", model_id=REMOTE_MODEL_ID).json()["agent"]
    channel = channel_with_agent(client, agent["id"])

    def round_reason(locale: str) -> str:
        use_locale(client, locale)
        # 远端未确认 ⇒ 该成员不可用，于是这一轮必然留下一条可读原因的 failed 结果。
        send(client, channel["id"], "请 @被点名的人 看一下")
        drain(harness)
        record = outcomes(client, channel["id"])[-1]
        assert record["status"] == "failed"
        # 同一条后端事实在只读面上的说法必须逐字一致（一处措辞、两个出口，不漂移）。
        assert record["reason"] == client.get(f"/api/agents/{agent['id']}").json()["agent"]["enabled_reason"]
        # 并且**逐字等于**按当前偏好渲染出的那句：数据面清单读同一偏好下的告知面读数。
        faces = client.get("/api/remote-data-flow").json()["data_faces"]
        assert record["reason"] == i18n.render(
            "availability.remote_consent",
            locale,
            provider="dashscope",
            faces=i18n.join_list(faces, locale),
        )
        return record["reason"]

    zh_reason = round_reason(ZH)
    en_reason = round_reason(EN)

    assert zh_reason != en_reason
    # 标识符不译：两种说法都点名同一个提供商。
    assert "dashscope" in zh_reason and "dashscope" in en_reason


def test_request_headers_never_choose_the_language(client: TestClient, harness: dict) -> None:
    """语言偏好读库，**不读请求头**：偏好是 zh-CN 时，带 ``Accept-Language: en`` 也一样是中文。"""
    use_locale(client, ZH)
    agent = create_agent(client, name="被点名的人", model_id=REMOTE_MODEL_ID).json()["agent"]
    channel = channel_with_agent(client, agent["id"])
    client.post(
        f"/api/channels/{channel['id']}/messages",
        json={"body": "请 @被点名的人 看一下"},
        headers={"Accept-Language": "en", "Content-Language": "en"},
    )
    drain(harness)

    reason = outcomes(client, channel["id"])[-1]["reason"]
    # 带英文请求头也仍是**源语言**那条取值（请求头不参与选择）。
    faces = client.get("/api/remote-data-flow").json()["data_faces"]
    assert reason == i18n.render(
        "availability.remote_consent",
        ZH,
        provider="dashscope",
        faces=i18n.join_list(faces, ZH),
    )
    # 错误文案同理：带英文请求头的请求仍拿到源语言正文。
    error = client.post(
        "/api/remote-data-flow/ack",
        json={"provider": "没有这个"},
        headers={"Accept-Language": "en"},
    ).json()["error"]
    assert error["message"] == i18n.render("error.unknown_provider", ZH)


def test_mention_ignored_failure_reason_follows_the_locale(client: TestClient, harness: dict) -> None:
    """「被点名却自判忽略 ⇒ 按失败处理」那条既有分支的原因也按偏好取值。"""
    agent = create_agent(client, name="沉默的人").json()["agent"]
    harness["seam"].behaviors = {agent["id"]: IGNORE}
    channel = channel_with_agent(client, agent["id"])

    def failure_reason(locale: str) -> str:
        use_locale(client, locale)
        send(client, channel["id"], f"@{agent['display_name']} 请回应")
        drain(harness)
        record = outcomes(client, channel["id"])[-1]
        assert record["status"] == "failed"
        return record["reason"]

    zh_reason = failure_reason(ZH)
    en_reason = failure_reason(EN)

    assert zh_reason != en_reason
    # 内嵌的「原本为什么自判忽略」是替身给出的原因（测试自己造的文本），两种语言都原样带上。
    assert "脚本化替身：自判无关" in zh_reason and "脚本化替身：自判无关" in en_reason


def test_pierced_join_system_message_follows_the_locale(client: TestClient, harness: dict) -> None:
    """穿透加入那条 ``system`` 事件消息也按偏好取值（它是「成员变化」这条事实的正文）。"""
    host = create_agent(client, name="局内人").json()["agent"]

    def join_bodies(locale: str) -> tuple[list[str], str]:
        # 每种语言一份**全新**频道与全新局外人：穿透加入只在「还没加入」的那一次发生。
        outsider = create_agent(client, name=f"局外人-{locale}").json()["agent"]
        channel = channel_with_agent(client, host["id"])
        use_locale(client, locale)
        send(client, channel["id"], f"请 @{outsider['display_name']} 看一下")
        drain(harness)
        bodies = [
            message["body"] for message in messages(client, channel["id"]) if message["kind"] == "system"
        ]
        return bodies, outsider["display_name"]

    zh_bodies, zh_name = join_bodies(ZH)
    en_bodies, en_name = join_bodies(EN)

    assert zh_bodies and en_bodies
    assert zh_bodies[0] == i18n.render("system.member.pierced_join", ZH, name=zh_name)
    assert en_bodies[0] == i18n.render("system.member.pierced_join", EN, name=en_name)
    assert zh_bodies[0] != en_bodies[0]


def test_budget_exhausted_reason_follows_the_locale(client: TestClient, harness: dict) -> None:
    """预算耗尽那条既有 ``reason`` 分支同样按偏好取值（含「零投递」这条事实的说明）。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = create_channel(client, member_ids=[first["id"], second["id"]]).json()["channel"]

    use_locale(client, EN)
    send(client, channel["id"], "开始接力")
    for _ in range(12):
        drain(harness)
        if harness["scheduler"].pending_count() == 0:
            break

    state = budget(client, channel["id"])
    assert state["used"] == state["limit"]  # 上界恰好用满（既有纪律不回归）
    exhausted = [
        record for record in outcomes(client, channel["id"]) if record["status"] == "budget-exhausted"
    ]
    assert exhausted
    # 每条耗尽记录的正文都取自当前偏好的那条词条（含 epoch 与上界两个参数）。
    assert {record["reason"] for record in exhausted} == {
        i18n.render("reason.budget_exhausted", EN, epoch=state["epoch_seq"], limit=state["limit"])
    }
    assert i18n.render(
        "reason.budget_exhausted", EN, epoch=state["epoch_seq"], limit=state["limit"]
    ) != i18n.render("reason.budget_exhausted", ZH, epoch=state["epoch_seq"], limit=state["limit"])


def test_history_is_not_backfilled_when_the_locale_changes(client: TestClient) -> None:
    """历史不回填：已落库的正文保持写入时的语言（append-only，ADR-0001 / ADR-0009 三）。"""
    channel = create_channel(client).json()["channel"]["id"]

    use_locale(client, ZH)
    zh_task = create_task(client, channel, "先写下的任务")
    before = task_bodies(client, zh_task)
    assert before == [i18n.render("system.task.created", ZH, title="先写下的任务")]

    use_locale(client, EN)
    en_task = create_task(client, channel, "written later")

    # 切语言不重渲染：老正文逐字不变（含后续再读一次）。
    assert task_bodies(client, zh_task) == before
    assert task_bodies(client, en_task)[0] == i18n.render("system.task.created", EN, title="written later")


def test_fresh_workspace_falls_back_to_the_source_language(client: TestClient) -> None:
    """库内没有**用户选过**的偏好时取源语言：新工作台直接产出简体中文正文。"""
    assert client.get("/api/settings").json()["settings"]["locale"] == ZH

    channel = create_channel(client).json()["channel"]["id"]
    task_id = create_task(client, channel, "空工作台的任务")

    assert task_bodies(client, task_id)[0] == i18n.render("system.task.created", ZH, title="空工作台的任务")
    channel_messages = messages(client, channel)
    assert [message["task_pointer"] for message in channel_messages] == [task_id]
    assert channel_messages[0]["body"] == i18n.render("system.task.created", ZH, title="空工作台的任务")
