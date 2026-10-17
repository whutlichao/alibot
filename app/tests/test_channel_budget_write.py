"""频道回应预算的**人类面上界写入口**（实施票 #54 / 规格 #31 §2「已裁定的配置契约」）。

判据（票面正本 = GitHub issue #54，逐条对应本文件的用例组）：

1. **写入口**：``PATCH /api/channels/{id}/budget`` 只表达**上界**（``budget_limit``），成功后返回
   刷新后的只读读数——于是前端一次调用就能刷新「当前世代读数 + 待生效值」。
2. **固定档位**：只接受 ``{3, 5, 10}``，默认仍是 3；其余取值（非法整数 / 字符串 / null / 浮点 /
   缺字段 / 未知键）明确拒绝，且**不留下半成品**（整体不写，读回与重启后都没有痕迹）。
3. **用量边界**：请求体带 ``used``（或任何用量 / 世代事实字段：``remaining`` / ``epoch_seq`` /
   ``applied_limit``）整体拒绝、可读原因；``used`` 仍只由后端在开世代时重置、在一次**实际投递**的
   Agent→Agent 跳时 +1（人类面既写不了、也清不了）。
4. **下一世代生效**：保存不回溯改写当前世代——``limit`` / ``used`` / ``remaining`` / ``source``
   一字不改，``pending_limit`` 才是新值；下一条人类消息开的新世代才用它。
5. **逐频道隔离**：只影响被写的频道；task 型 target 仍只有内置默认。**重启后持久**。
6. **既有语义不回归**：当前世代的硬上界（届时生效那份）、零投递不消耗、在途轮次期间写入不抬高
   当前世代额度、耗尽仍零投递并留 ``budget-exhausted``。

接缝：只走既有 HTTP 黑盒（``conftest`` 的 ``client`` / ``launched`` + 手动调度器 + 脚本化替身），
与 ``test_channel_budget_limit.py`` / ``test_bounded_relay.py`` 同一套；不新增测试接缝。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    budget,
    create_agent,
    create_channel,
    drain,
    messages,
    outcomes,
    send,
)
from tests.scripted import DELAY, IGNORE, REPLY

# 内置默认上界（ADR-0002 未修订）：它是「未配置回落」这条判据的独立正本读数。
DEFAULT_LIMIT = 3
# 与默认值不同的那个合法档位（接力用例跑到 5 就够，比 10 快）。
OVERRIDE_LIMIT = 5

# 人类面**不能**写的用量 / 世代事实字段。它们全都被明确拒绝（见用例组 3）。
USAGE_FIELDS = ("used", "remaining", "epoch_seq", "applied_limit")


def _patch(client: TestClient, channel_id: str, body: dict[str, Any]):
    """写入口（票面唯一的写路径）。"""
    return client.patch(f"/api/channels/{channel_id}/budget", json=body)


def _open_epoch(client: TestClient, channel_id: str, body: str = "开一个世代") -> dict:
    """发一条人类消息：它**落库即开世代**（ADR-0010 / spec #1 §5），于是「当前世代」存在了。

    没有成员的频道也能开世代：预算约束的是「一次人类发言能引发多少 agent 往返」，
    没有 Agent 就没有跳，但世代与有效上界快照照常落库——这正是「下一世代才生效」的观察面。
    """
    assert send(client, channel_id, body).status_code == 201
    return budget(client, channel_id)


def _relay_channel(client: TestClient, first: dict, second: dict, name: str = "互相点名") -> dict:
    """两个互相 `@` 的 Agent：每次回复都点名对方，构成一个原本会永动的循环。"""
    return client.post(
        "/api/channels",
        json={"name": name, "description": "", "member_ids": [first["id"], second["id"]]},
    ).json()["channel"]


def _run_relay(client: TestClient, harness: dict, channel_id: str) -> None:
    """把接力跑到**自己停下**（不是被测试的循环上限截断）。"""
    for _ in range(20):
        drain(harness)
        if harness["scheduler"].pending_count() == 0:
            break


# ─── 1. 成功写入：只写上界，返回刷新后的读数 ────────────────────────────────
def test_patch_saves_the_upper_bound_and_returns_the_refreshed_budget(client: TestClient) -> None:
    """保存 5 ⇒ 200 + 刷新后的读数：当前世代仍是 3，``pending_limit`` = 5（下个世代生效）。"""
    channel = create_channel(client, name="成功写入").json()["channel"]
    before = _open_epoch(client, channel["id"])
    assert before["limit"] == DEFAULT_LIMIT and before["used"] == 0
    assert before["source"] == "built-in-default" and before["pending_limit"] is None

    response = _patch(client, channel["id"], {"budget_limit": OVERRIDE_LIMIT})

    assert response.status_code == 200, response.text
    saved = response.json()["budget"]
    # 成功返回**刷新后的**读数：前端一次调用即可刷新，不必再 GET。
    assert saved == budget(client, channel["id"])
    assert saved["channel_id"] == channel["id"]
    assert saved["epoch_seq"] == before["epoch_seq"], "写配置不推进世代"
    assert saved["limit"] == DEFAULT_LIMIT, "当前世代的有效上界不变"
    assert saved["used"] == before["used"], "写配置不碰用量"
    assert saved["remaining"] == DEFAULT_LIMIT - before["used"], "剩余量仍按当前世代有效上界算"
    assert saved["source"] == "built-in-default"
    assert saved["pending_limit"] == OVERRIDE_LIMIT, "新值只作为待生效值报出"

    # 下一条人类消息开新世代 ⇒ 新值生效，待生效值消失。
    after = _open_epoch(client, channel["id"], "新世代")
    assert after["epoch_seq"] > before["epoch_seq"]
    assert after["limit"] == OVERRIDE_LIMIT
    assert after["used"] == 0
    assert after["remaining"] == OVERRIDE_LIMIT
    assert after["source"] == "channel-override"
    assert after["pending_limit"] is None


@pytest.mark.parametrize("tier", [3, 5, 10])
def test_every_fixed_tier_is_accepted(client: TestClient, tier: int) -> None:
    """3 / 5 / 10 三档全部合法（默认 3 也在档位集合里，保存它不改变行为）。"""
    channel = create_channel(client, name=f"档位 {tier}").json()["channel"]
    _open_epoch(client, channel["id"])

    response = _patch(client, channel["id"], {"budget_limit": tier})

    assert response.status_code == 200, response.text
    saved = response.json()["budget"]
    # 「已保存 ≠ 当前有效」才报待生效值：保存 3 与内置默认相同 ⇒ 没有待生效差异。
    assert saved["pending_limit"] == (None if tier == DEFAULT_LIMIT else tier)
    assert saved["limit"] == DEFAULT_LIMIT, "当前世代不被回溯改写"

    after = _open_epoch(client, channel["id"], f"下一个世代用 {tier}")
    assert after["limit"] == tier
    assert after["source"] == "channel-override"
    assert after["pending_limit"] is None


def test_a_patch_before_the_first_human_epoch_is_read_back_as_pending(client: TestClient) -> None:
    """**还没有任何世代**的频道保存 5 ⇒ 读回 `limit=3` / `pending_limit=5`（不是「立刻生效」）。

    #54 票面原句：「保存的改动不改变当前世代的有效上界与用量，从下一个人类世代生效，在此之前作为
    待生效值读回」。没有世代时当前生效上界就是内置默认，因此读回必须仍是 3。
    """
    channel = create_channel(client, name="无世代写入").json()["channel"]
    before = budget(client, channel["id"])
    assert before["epoch_seq"] == 0 and before["limit"] == DEFAULT_LIMIT

    response = _patch(client, channel["id"], {"budget_limit": OVERRIDE_LIMIT})

    assert response.status_code == 200, response.text
    saved = response.json()["budget"]
    assert saved == budget(client, channel["id"])
    assert saved["epoch_seq"] == 0, "写配置不开世代"
    assert saved["limit"] == DEFAULT_LIMIT
    assert saved["source"] == "built-in-default"
    assert saved["used"] == 0 and saved["remaining"] == DEFAULT_LIMIT
    assert saved["pending_limit"] == OVERRIDE_LIMIT

    after = _open_epoch(client, channel["id"], "第一条人类消息")
    assert after["limit"] == OVERRIDE_LIMIT and after["source"] == "channel-override"
    assert after["pending_limit"] is None


# ─── 2. 非法值：明确拒绝，无部分写入 ────────────────────────────────────────
@pytest.mark.parametrize("illegal", [-10, -1, 0, 1, 2, 4, 6, 7, 9, 11, 100])
def test_an_integer_outside_the_fixed_tiers_is_refused_without_a_partial_write(
    launched: dict, illegal: int
) -> None:
    """只接受 3 / 5 / 10：其余整数 400 + 可读原因，读回仍是内置默认，重启后也没留下痕迹。"""
    client: TestClient = launched["client"]
    channel = create_channel(client, name=f"非法档位 {illegal}").json()["channel"]
    before = _open_epoch(client, channel["id"])

    response = _patch(client, channel["id"], {"budget_limit": illegal})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    # 可读原因：说清「只接受固定档位」与那三档，且明说未保存任何改动。
    assert "3, 5, 10" in error["message"]
    assert "未保存" in error["message"]
    assert budget(client, channel["id"]) == before, "非法值不得留下半成品"

    assert budget(launched["restart"](), channel["id"]) == before


@pytest.mark.parametrize("illegal", ["5", "abc", "", None, 5.0, True, [5], {"v": 5}, "10"])
def test_a_value_that_is_not_a_tier_integer_is_refused(client: TestClient, illegal: Any) -> None:
    """字符串 / null / 浮点 / 布尔 / 数组 / 对象一律拒绝（不把 ``"5"`` 静默当成 5）。"""
    channel = create_channel(client, name="非整数档位").json()["channel"]
    before = _open_epoch(client, channel["id"])

    response = _patch(client, channel["id"], {"budget_limit": illegal})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    # 可读原因：字段级正文不是空串（前端会把 ``fields`` 拼进提示）。
    assert isinstance(error["message"], str) and error["message"]
    assert error["fields"]["body"]
    assert budget(client, channel["id"]) == before


def test_a_body_without_the_upper_bound_is_refused(client: TestClient) -> None:
    """空请求体 / 只带用量字段 ⇒ 拒绝：写入口只表达上界，不接受「空更新」。"""
    channel = create_channel(client, name="缺字段").json()["channel"]
    before = _open_epoch(client, channel["id"])

    for body in ({}, {"limit": OVERRIDE_LIMIT}, {"budget_limit": None}):
        response = _patch(client, channel["id"], body)
        assert response.status_code == 400, (body, response.text)
        assert response.json()["error"]["code"] == "validation_failed"

    assert budget(client, channel["id"]) == before


# ─── 3. 用量不可写：used 由后端独占 ────────────────────────────────────────
def test_a_request_that_writes_used_is_refused_and_usage_stays_backend_owned(
    client: TestClient, harness: dict
) -> None:
    """``used`` 不能被清零：一次真实投递把 ``used`` 推到 1 之后，带 ``used: 0`` 的请求整体被拒。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].behaviors = {first["id"]: REPLY, second["id"]: IGNORE}
    channel = create_channel(client, name="用量不可写", member_ids=[first["id"], second["id"]]).json()[
        "channel"
    ]
    send(client, channel["id"], "开始")
    drain(harness)
    used_before = budget(client, channel["id"])["used"]
    assert used_before == 1, "一次实际投递的 Agent→Agent 跳让 used +1（后端事实）"

    # 既想改用量、又想顺带写上界：**整体拒绝**，两件事都不生效。
    response = _patch(client, channel["id"], {"budget_limit": OVERRIDE_LIMIT, "used": 0})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "用量" in error["message"] + error["fields"]["body"], "原因要说清用量只读"
    state = budget(client, channel["id"])
    assert state["used"] == used_before, "用量没有被清掉"
    assert state["pending_limit"] is None, "被拒的请求连上界也没有写进去（无部分写入）"

    # 只带用量、不带上界：同样是拒绝。
    alone = _patch(client, channel["id"], {"used": 0})
    assert alone.status_code == 400, alone.text
    assert budget(client, channel["id"])["used"] == used_before


@pytest.mark.parametrize("field", USAGE_FIELDS)
def test_every_usage_or_epoch_field_is_refused(client: TestClient, field: str) -> None:
    """``used`` / ``remaining`` / ``epoch_seq`` / ``applied_limit`` 都是后端正本，人类面写不了。"""
    channel = create_channel(client, name=f"不写 {field}").json()["channel"]
    before = _open_epoch(client, channel["id"])

    for body in ({field: 0}, {"budget_limit": OVERRIDE_LIMIT, field: 0}):
        response = _patch(client, channel["id"], body)
        assert response.status_code == 400, (body, response.text)
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert "用量" in error["fields"]["body"]

    assert budget(client, channel["id"]) == before


def test_an_unknown_field_is_refused_and_nothing_is_written(client: TestClient) -> None:
    """未知键（例如把只读读数 ``limit`` 当可写字段）整体拒绝：写面只认 ``budget_limit``。"""
    channel = create_channel(client, name="未知键").json()["channel"]
    before = _open_epoch(client, channel["id"])

    response = _patch(client, channel["id"], {"budget_limit": OVERRIDE_LIMIT, "limit": 10})

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    assert budget(client, channel["id"]) == before


# ─── 4. 逐频道隔离 + task 型 target 不受影响 ────────────────────────────────
def test_the_saved_upper_bound_stays_inside_its_own_channel(client: TestClient) -> None:
    """频道 A 的写入不改变频道 B；task 型 target 仍只有内置默认（配置是**逐频道**的）。"""
    configured = create_channel(client, name="配过的频道").json()["channel"]
    untouched = create_channel(client, name="没配的频道").json()["channel"]
    _open_epoch(client, configured["id"])
    _open_epoch(client, untouched["id"])
    assert _patch(client, configured["id"], {"budget_limit": 10}).status_code == 200

    assert budget(client, configured["id"])["pending_limit"] == 10
    other = budget(client, untouched["id"])
    assert other["limit"] == DEFAULT_LIMIT
    assert other["source"] == "built-in-default"
    assert other["pending_limit"] is None

    task = client.post(f"/api/channels/{configured['id']}/tasks", json={"title": "一张任务"}).json()["task"]
    task_budget = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]
    assert task_budget["limit"] == DEFAULT_LIMIT


# ─── 5. 重启后持久 ─────────────────────────────────────────────────────────
def test_a_saved_upper_bound_survives_a_restart_and_still_activates_next_epoch(
    launched: dict,
) -> None:
    """重启后仍是「当前 3 / 待生效 5」；下一条人类消息开的新世代才用它。"""
    client: TestClient = launched["client"]
    channel = create_channel(client, name="重启保持写入").json()["channel"]
    _open_epoch(client, channel["id"])
    assert _patch(client, channel["id"], {"budget_limit": OVERRIDE_LIMIT}).status_code == 200
    before = budget(client, channel["id"])

    restarted = launched["restart"]()

    assert budget(restarted, channel["id"]) == before
    assert before["limit"] == DEFAULT_LIMIT and before["pending_limit"] == OVERRIDE_LIMIT
    after = _open_epoch(restarted, channel["id"], "重启后的新世代")
    assert after["limit"] == OVERRIDE_LIMIT
    assert after["pending_limit"] is None


# ─── 6. 既有语义不回归：硬上界 / 零投递 / 耗尽 / 在途写入 ───────────────────
def test_the_current_epochs_hard_bound_ignores_a_saved_pending_tier(
    client: TestClient, harness: dict
) -> None:
    """当前世代保存 5，但硬上界仍是 3：接力恰好停在 3 跳，耗尽照旧零投递。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = _relay_channel(client, first, second, name="待生效不抬额度")

    send(client, channel["id"], "开世代")
    assert _patch(client, channel["id"], {"budget_limit": OVERRIDE_LIMIT}).status_code == 200
    _run_relay(client, harness, channel["id"])

    state = budget(client, channel["id"])
    assert state["used"] == DEFAULT_LIMIT, "当前世代的有效上界仍是 3（待生效值不参与裁决）"
    assert state["limit"] == DEFAULT_LIMIT
    assert state["remaining"] == 0
    assert state["pending_limit"] == OVERRIDE_LIMIT
    exhausted = [row for row in outcomes(client, channel["id"]) if row["status"] == "budget-exhausted"]
    assert exhausted, "到上界之后一个唤醒都不投递（既有零投递语义不回归）"


def test_the_next_epochs_hard_bound_follows_the_saved_tier(client: TestClient, harness: dict) -> None:
    """新人类世代按保存的 5 裁决：接力能跑到 5 跳，然后是耗尽——硬上界与只读面同一条事实。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = _relay_channel(client, first, second, name="新世代用配置")

    send(client, channel["id"], "第一代")
    _run_relay(client, harness, channel["id"])
    assert budget(client, channel["id"])["used"] == DEFAULT_LIMIT
    assert _patch(client, channel["id"], {"budget_limit": OVERRIDE_LIMIT}).status_code == 200

    send(client, channel["id"], "第二代")
    _run_relay(client, harness, channel["id"])

    state = budget(client, channel["id"])
    assert state["limit"] == OVERRIDE_LIMIT
    assert state["source"] == "channel-override"
    assert state["used"] == OVERRIDE_LIMIT, "硬上界与只读面取的是同一份有效上界"
    assert state["remaining"] == 0
    assert [row for row in outcomes(client, channel["id"]) if row["status"] == "budget-exhausted"], (
        "到上界之后一个唤醒都不投递"
    )


def test_a_zero_delivery_agent_message_still_does_not_consume_budget(
    client: TestClient, harness: dict
) -> None:
    """零投递不消耗预算的纪律不因写入口而变：唯一成员回复时唤醒集为空，``used`` 仍是 0。"""
    agent = create_agent(client, name="独苗").json()["agent"]
    channel = create_channel(client, name="零投递", member_ids=[agent["id"]]).json()["channel"]
    assert _patch(client, channel["id"], {"budget_limit": 10}).status_code == 200

    send(client, channel["id"], "第一条")
    drain(harness)

    assert any(message["kind"] == "agent" for message in messages(client, channel["id"]))
    state = budget(client, channel["id"])
    assert state["used"] == 0, "没有实际投递 ⇒ 不算一次跳"
    assert state["remaining"] == 10, "新世代按保存的档位满额"


def test_a_patch_while_a_round_is_in_flight_does_not_raise_the_current_bound(
    client: TestClient, harness: dict
) -> None:
    """**在途窗口**里写入不改变当前世代额度：放行后接力仍停在 3，不是停在 10。

    这是「并发 / 在途轮次不能越过当前世代有效上界」这条既有硬上界在**写入面**上的判别点：
    写配置只写配置表，裁决读的是世代快照（``applied_limit``）。
    """
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].behaviors = {second["id"]: DELAY}
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    harness["seam"].block()
    channel = _relay_channel(client, first, second, name="在途写入")

    # 人类消息先开世代（快照 = 3），它的唤醒还在路上；先让乙停在闸门上，形成「一轮在途」。
    send(client, channel["id"], "开世代")
    # 在**另一条线程**里把排队的轮次跑起来：乙的第一轮会停在闸门上，形成一段稳定的在途窗口。
    runner = threading.Thread(target=harness["scheduler"].drain)
    runner.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not harness["seam"].calls_for(second["id"]):
        time.sleep(0.01)
    assert harness["seam"].calls_for(second["id"]), "乙没有进入在途"

    response = _patch(client, channel["id"], {"budget_limit": 10})
    assert response.status_code == 200, response.text
    assert response.json()["budget"]["limit"] == DEFAULT_LIMIT, "在途写入不改当前世代额度"
    assert response.json()["budget"]["pending_limit"] == 10

    harness["seam"].release()
    runner.join(timeout=10)
    assert not runner.is_alive()
    _run_relay(client, harness, channel["id"])

    state = budget(client, channel["id"])
    assert state["used"] == DEFAULT_LIMIT, "仍停在当前世代的有效上界 3，而不是待生效的 10"
    assert state["limit"] == DEFAULT_LIMIT
    assert state["remaining"] == 0
    assert state["used"] <= state["limit"] + 0, "硬上界不变量：已用不超过当前世代有效上界"
