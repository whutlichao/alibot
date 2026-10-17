"""频道回应预算的配置落点与只读呈现（实施票 #53 / 规格 #31 §2「已裁定的配置契约」）。

判据（票面正本 = GitHub issue #53，逐条对应本文件的用例组）：

1. **配置落点**：上界是**库内逐频道**配置，未配置的频道回落内置默认 3；没有环境变量、
   工作区级、成员级配置。档位只接受 ``{3, 5, 10}``，非法值整体拒绝、不留半成品。
2. **只读呈现**：``GET /api/channels/{id}/budget`` 报出当前人类世代的 ``used`` / ``remaining`` /
   **有效上界**与**来源**（频道覆盖 / 内置默认）。既有字段名一个字不改（票面 Constraint）。
3. **下一世代待生效**：已保存的新值与当前有效上界**不同**时单独报出，当前 ``remaining``
   仍按当前世代的有效上界算。
4. **用量正本**：``used`` 读 ``target_budget.used``，不由 ``round_outcome`` 行求和。
5. **跨重启保持**：当前世代的有效上界与 ``used`` 在后端重启后仍是同一条事实。
6. **接缝**：只走既有 HTTP 黑盒（``create_app`` + 手动调度器 + 脚本化替身）；不新增测试接缝。

**关于「已保存的上界」怎么造**：人类面的写入口（``PATCH /api/channels/{id}/budget``）归 #54，
本票不带它。因此凡是要「库里已有一条保存值」的用例，都用**既有的**库句柄接缝
（``app.state.db``，与 ``test_tasks.py`` / ``test_auto_claim.py`` 同一个、不是本票新开的）调用
本票的配置落点 API 写入，再用 HTTP 只读面断言。这不违反「不新开测试缝」：注入点、调度器、
替身、HTTP 客户端全部是既有那一套，#54 接上 PATCH 之后这两条用例可以平移到 HTTP 写路径。

「上界从下一世代生效」的观察面因此是三元组：``limit``（当前世代有效上界）、``source``（它从哪一层
来）、``pending_limit``（已保存、下个世代才生效的新值）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from alibot.db import BUDGET_LIMIT, DATABASE_FILE_NAME, SCHEMA_VERSION, DomainError
from fastapi.testclient import TestClient

from tests.conftest import (
    create_agent,
    create_channel,
    drain,
    outcomes,
    send,
)
from tests.scripted import IGNORE, REPLY
from tests.test_schema_migration import _build_library, _migrate_argv, _run

# 内置默认上界（票面与 ADR-0002 都钉成 3）：它是「未配置回落」这条判据的独立正本读数。
DEFAULT_LIMIT = 3
# 频道覆盖的合法档位里、与默认值不同的那一档（取 5 而不是 10：接力用例跑到 5 就够，跑得更快）。
OVERRIDE_LIMIT = 5


def _db(client: TestClient) -> Any:
    """既有库句柄接缝（``app.state.db``）：用来造「库里已保存的上界」，与 ``test_tasks.py`` 同源。"""
    return cast(Any, client.app).state.db


def _budget(client: TestClient, channel_id: str) -> dict:
    """``GET /api/channels/{id}/budget`` 的 ``budget`` 载荷（票面唯一的只读呈现面）。"""
    response = client.get(f"/api/channels/{channel_id}/budget")
    assert response.status_code == 200, response.text
    return response.json()["budget"]


# ─── 1. 未配置回落内置默认，且来源可读 ───────────────────────────────────────
def test_an_unconfigured_channel_reports_the_built_in_default_and_its_source(
    client: TestClient,
) -> None:
    """没有任何频道覆盖 ⇒ 有效上界 = 内置默认 3，来源 = 内置默认，没有待生效值。"""
    channel = create_channel(client, name="回落默认").json()["channel"]

    state = _budget(client, channel["id"])

    # 既有字段名一个字不改（票面 Constraint）：这五个键必须原样在。
    assert {"channel_id", "epoch_seq", "limit", "used", "remaining"} <= set(state)
    assert state["channel_id"] == channel["id"]
    assert state["limit"] == DEFAULT_LIMIT
    assert state["used"] == 0
    assert state["remaining"] == DEFAULT_LIMIT
    # 本轮新增的两个读数：来源 + 待生效值。
    assert state["source"] == "built-in-default"
    assert state["pending_limit"] is None


# ─── 2. 已保存的上界：当前世代不变，下一世代生效 ─────────────────────────────
def test_a_saved_upper_bound_is_pending_until_the_next_human_epoch(client: TestClient) -> None:
    """保存上界不回溯改写当前世代：``limit`` 仍是 3、``pending_limit`` = 5，下个世代才变 5。"""
    channel = create_channel(client, name="下个世代生效").json()["channel"]
    send(client, channel["id"], "开个世代")
    before = _budget(client, channel["id"])
    assert before["limit"] == DEFAULT_LIMIT and before["source"] == "built-in-default"

    _db(client).set_channel_budget_limit(channel["id"], OVERRIDE_LIMIT)

    pending = _budget(client, channel["id"])
    assert pending["epoch_seq"] == before["epoch_seq"]  # 当前世代没被推进
    assert pending["limit"] == DEFAULT_LIMIT  # 当前有效上界不变
    assert pending["remaining"] == DEFAULT_LIMIT - pending["used"]
    assert pending["source"] == "built-in-default"
    assert pending["pending_limit"] == OVERRIDE_LIMIT  # 下一世代才生效的那条

    send(client, channel["id"], "下一条人类消息开新世代")

    after = _budget(client, channel["id"])
    assert after["epoch_seq"] > before["epoch_seq"]
    assert after["limit"] == OVERRIDE_LIMIT
    assert after["remaining"] == OVERRIDE_LIMIT - after["used"]
    assert after["source"] == "channel-override"
    assert after["pending_limit"] is None  # 已生效 ⇒ 不再有待生效值


# ─── 3. remaining 按当前世代有效上界算，不按待生效值算 ───────────────────────
def test_remaining_is_calculated_from_the_current_epoch_effective_limit(
    client: TestClient, harness: dict
) -> None:
    """当前世代已用 1 跳、有效上界 3 ⇒ ``remaining`` 仍是 2（待生效的 5 不抬高当前额度）。"""
    talker = create_agent(client, name="甲").json()["agent"]
    quiet = create_agent(client, name="乙").json()["agent"]
    # 甲回一句（丙…… 乙被这条 Agent 消息唤醒 ⇒ 一次跳），乙自判无关 ⇒ 恰好停在 1 跳。
    harness["seam"].behaviors = {talker["id"]: REPLY, quiet["id"]: IGNORE}
    channel = create_channel(client, name="剩余按当前算", member_ids=[talker["id"], quiet["id"]]).json()[
        "channel"
    ]

    send(client, channel["id"], "开始")
    drain(harness)
    before = _budget(client, channel["id"])
    assert before["used"] == 1
    assert before["limit"] == DEFAULT_LIMIT
    assert before["remaining"] == DEFAULT_LIMIT - 1

    _db(client).set_channel_budget_limit(channel["id"], OVERRIDE_LIMIT)

    pending = _budget(client, channel["id"])
    assert pending["used"] == 1, "保存配置不改用量"
    assert pending["limit"] == DEFAULT_LIMIT
    assert pending["remaining"] == DEFAULT_LIMIT - 1, "剩余量仍按当前世代的有效上界算"
    assert pending["pending_limit"] == OVERRIDE_LIMIT


# ─── 4. used 的正本是 target_budget.used，不是轮次结果行求和 ──────────────────
def test_used_is_the_backend_truth_not_a_sum_of_round_outcomes(client: TestClient, harness: dict) -> None:
    """一次跳唤醒**两个**成员 ⇒ 两条 ``consumed_budget=True`` 的轮次结果，而 ``used`` 只 +1。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    third = create_agent(client, name="丙").json()["agent"]
    harness["seam"].behaviors = {first["id"]: REPLY, second["id"]: IGNORE, third["id"]: IGNORE}
    channel = create_channel(client, name="用量正本", member_ids=[first["id"]]).json()["channel"]

    # 人类消息落库时只有甲在频道里（它这一轮**不是**跳）；人类消息的唤醒先排队，
    # 随后乙、丙加入，于是甲那条 Agent 消息是它们**第一次**被唤醒——恰好构成一次跳唤醒两人。
    send(client, channel["id"], "开始")
    for member in (second, third):
        assert (
            client.post(
                f"/api/channels/{channel['id']}/members", json={"member_id": member["id"]}
            ).status_code
            == 200
        )
    drain(harness)

    rows = outcomes(client, channel["id"])
    consumed_rows = [row for row in rows if row["consumed_budget"]]
    state = _budget(client, channel["id"])
    assert len(consumed_rows) == 2, "这条跳唤醒了两个成员 ⇒ 行级标记出现在两行上"
    assert state["used"] == 1, "跳数只 +1：读的正本是 target_budget.used，不是行求和"
    assert sum(1 for row in rows if row["consumed_budget"]) == 2 != state["used"]


# ─── 5. 硬上界裁决与只读面取同一份有效上界 ──────────────────────────────────
def test_the_hard_bound_of_the_current_epoch_ignores_the_pending_value(
    client: TestClient, harness: dict
) -> None:
    """当前世代保存了 5，但硬上界仍是 3：接力恰好停在 3 跳，耗尽记录照旧零投递。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = create_channel(client, name="待生效不抬额度", member_ids=[first["id"], second["id"]]).json()[
        "channel"
    ]
    # 人类消息已经开了这个世代（快照 = 3），它的唤醒还没跑；此时保存 5 只是「下个世代生效」，
    # 于是这一世代的接力必须停在 3 —— 待生效值不参与硬上界裁决。
    send(client, channel["id"], "开世代")
    _db(client).set_channel_budget_limit(channel["id"], OVERRIDE_LIMIT)
    drain(harness, limit=200)
    drain(harness, limit=200)

    state = _budget(client, channel["id"])
    assert state["used"] == DEFAULT_LIMIT, "当前世代的有效上界仍是 3（待生效的 5 不参与裁决）"
    assert state["limit"] == DEFAULT_LIMIT
    assert state["pending_limit"] == OVERRIDE_LIMIT
    exhausted = [row for row in outcomes(client, channel["id"]) if row["status"] == "budget-exhausted"]
    assert exhausted, "到上界之后一个唤醒都不投递（既有零投递语义不回归）"


def test_the_next_epoch_hard_bound_is_the_saved_upper_bound(client: TestClient, harness: dict) -> None:
    """新人类世代按保存的 5 裁决：接力能跑到 5 跳，然后是耗尽——与只读面报的上界同一条事实。"""
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    harness["seam"].replies = {
        first["id"]: f"@{second['display_name']} 继续",
        second["id"]: f"@{first['display_name']} 继续",
    }
    channel = create_channel(client, name="新世代用配置", member_ids=[first["id"], second["id"]]).json()[
        "channel"
    ]
    send(client, channel["id"], "开世代")
    drain(harness)
    _db(client).set_channel_budget_limit(channel["id"], OVERRIDE_LIMIT)

    send(client, channel["id"], "新世代")
    drain(harness, limit=200)
    drain(harness, limit=200)

    state = _budget(client, channel["id"])
    assert state["limit"] == OVERRIDE_LIMIT
    assert state["source"] == "channel-override"
    assert state["used"] == OVERRIDE_LIMIT, "硬上界与只读面取的是同一份有效上界"
    assert state["remaining"] == 0
    exhausted = [row for row in outcomes(client, channel["id"]) if row["status"] == "budget-exhausted"]
    assert exhausted, "到上界之后一个唤醒都不投递"


# ─── 6. 逐频道：不影响别的频道，也不影响 task 型 target ─────────────────────
def test_the_override_stays_inside_its_own_channel(client: TestClient) -> None:
    """频道 A 的覆盖不改变频道 B；task 型 target 只有内置默认（配置是**逐频道**的）。"""
    configured = create_channel(client, name="配过的频道").json()["channel"]
    untouched = create_channel(client, name="没配的频道").json()["channel"]
    send(client, configured["id"], "开世代")
    _db(client).set_channel_budget_limit(configured["id"], OVERRIDE_LIMIT)
    send(client, configured["id"], "新世代")

    assert _budget(client, configured["id"])["limit"] == OVERRIDE_LIMIT
    other = _budget(client, untouched["id"])
    assert other["limit"] == DEFAULT_LIMIT
    assert other["source"] == "built-in-default"
    assert other["pending_limit"] is None

    task = client.post(f"/api/channels/{configured['id']}/tasks", json={"title": "一张任务"}).json()["task"]
    task_budget = client.get(f"/api/tasks/{task['id']}/budget").json()["budget"]
    assert task_budget["limit"] == DEFAULT_LIMIT, "频道配置不落到 task 型 target 上"


# ─── 7. 跨后端重启保持 ───────────────────────────────────────────────────────
def test_the_effective_limit_and_usage_survive_a_restart(launched: dict) -> None:
    """重启前读到的 ``epoch_seq`` / ``limit`` / ``source`` / ``used`` 与重启后逐字相同。"""
    client: TestClient = launched["client"]
    first = create_agent(client, name="甲").json()["agent"]
    second = create_agent(client, name="乙").json()["agent"]
    launched["seam"].behaviors = {first["id"]: REPLY, second["id"]: IGNORE}
    channel = create_channel(client, name="重启保持", member_ids=[first["id"], second["id"]]).json()[
        "channel"
    ]
    send(client, channel["id"], "开世代")
    drain(launched)
    _db(client).set_channel_budget_limit(channel["id"], OVERRIDE_LIMIT)
    send(client, channel["id"], "新世代")
    drain(launched)
    before = _budget(client, channel["id"])
    assert before["used"] >= 1 and before["limit"] == OVERRIDE_LIMIT

    restarted = launched["restart"]()

    assert _budget(restarted, channel["id"]) == before


def test_the_pending_value_also_survives_a_restart(launched: dict) -> None:
    """待生效值也落库：重启后仍是「当前 3 / 待生效 5」，不会因为换了进程就静默生效。"""
    client: TestClient = launched["client"]
    channel = create_channel(client, name="待生效重启").json()["channel"]
    send(client, channel["id"], "开世代")
    _db(client).set_channel_budget_limit(channel["id"], OVERRIDE_LIMIT)
    before = _budget(client, channel["id"])

    restarted = launched["restart"]()

    after = _budget(restarted, channel["id"])
    assert after == before
    assert after["limit"] == DEFAULT_LIMIT
    assert after["pending_limit"] == OVERRIDE_LIMIT


# ─── 8. 固定档位：非法值整体拒绝，不留半成品 ─────────────────────────────────
@pytest.mark.parametrize("illegal", [0, 1, 2, 4, 6, 7, 10 + 1, 100])
def test_an_illegal_upper_bound_is_refused_without_a_partial_write(launched: dict, illegal: int) -> None:
    """只接受 3 / 5 / 10：其余取值明确拒绝，读回仍是内置默认、重启后也没有留下痕迹。"""
    client: TestClient = launched["client"]
    channel = create_channel(client, name="非法档位").json()["channel"]
    send(client, channel["id"], "开世代")

    with pytest.raises(DomainError) as refused:
        _db(client).set_channel_budget_limit(channel["id"], illegal)
    assert refused.value.code == "validation_failed"

    state = _budget(client, channel["id"])
    assert state["limit"] == DEFAULT_LIMIT
    assert state["source"] == "built-in-default"
    assert state["pending_limit"] is None, "非法值不得留下半成品"

    restarted = launched["restart"]()
    assert _budget(restarted, channel["id"]) == state


# ─── 9. schema 14：迁移只前进、不回填，旧行按内置默认读 ─────────────────────
def test_a_v13_library_migrates_to_fourteen_and_old_rows_read_as_the_default(
    tmp_path: Path,
) -> None:
    """v13 库（已有 ``target_budget.used = 3``）迁到 14 后：旧世代**不回填**，按内置默认读。"""
    from tests.conftest import backend

    data_dir = tmp_path / "data"
    _build_library(data_dir, 13)
    channel_id = "ch_dev"  # 迁移 fixture 里那个已用 3 跳的频道

    completed = _run(*_migrate_argv(data_dir))
    assert completed.returncode == 0, completed.stderr
    assert "13 → 14" in completed.stdout, "报告里必须能读到本票那一步"

    with backend(data_dir) as client:
        state = _budget(client, channel_id)
        assert state["used"] == 3, "存量用量原样保留（迁移不改写用量）"
        assert state["limit"] == DEFAULT_LIMIT, "旧行没有被回填：仍按内置默认读"
        assert state["source"] == "built-in-default"
        assert state["pending_limit"] is None
        # 迁移确实建出了配置落点：保存 5 之后读得回「待生效 5」。
        _db(client).set_channel_budget_limit(channel_id, OVERRIDE_LIMIT)
        assert _budget(client, channel_id)["pending_limit"] == OVERRIDE_LIMIT


# ─── 边界：schema 号与内置默认值的独立读数 ──────────────────────────────────
def test_the_schema_version_and_the_default_limit_are_what_the_ticket_says() -> None:
    """本票（#53）拿号 14；内置默认仍是 3（ADR-0002 不修订）。

    ``SCHEMA_VERSION`` 不钉**等号**：ADR-0015 §三 明写判据是「必须高于当前 main」而不是「等于被分配
    的预留号」——#58 把它推到 15（``round_outcome.status`` 的 CHECK 纳入 ``silent``），本票的号段事实
    仍然成立（``>= 14``），但「此刻恰好等于 14」不是本票要守的契约。
    """
    assert SCHEMA_VERSION >= 14
    assert DEFAULT_LIMIT == BUDGET_LIMIT == 3
    assert DATABASE_FILE_NAME.endswith(".db")
