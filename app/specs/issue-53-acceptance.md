# issue #53 验收取证：回应预算配置落点 + 只读呈现

票面正本：[GitHub issue #53](https://github.com/whutlichao/alibot/issues/53)（`gh issue view 53`）。父规格：
`app/specs/issue-31-spec.md` §Implementation Decisions 2「已裁定的配置契约」、§Testing Decisions。
判据来源 ADR：`docs/adr/0002-response-budget-caps-relay-at-three.md`（**原文不改**）、
`docs/adr/0007-human-face-writes-budget-limit-only.md`（边界由本票的落点支撑，本票**不写**它）、
`docs/adr/0015-schema-migration-is-explicit-and-forward-only.md`（迁移纪律）。

- worktree：`/Users/apple/orca/workspaces/alibot/wt-53`
- 分支：`issue-53-impl`（从 `alibot-budget-silence/integration` 切出，tip `7ab75a1`）
- schema 号：`SCHEMA_VERSION` 13 → **14**（本票拿 14，后续票从 15 起）
- 本票**不**改 ADR-0002、**不**写 ADR-0007、**不**碰 `app/alibot/studio/*`（写入口与 UI 归 #54）

## 1. 范围与接缝裁定（两条边界，写在这里免得被误读成漏做）

**① 「频道设置视图展示」落在只读载荷上，UI 渲染归 #54。** 票面第 2 条判据的原句是「**Extend the
existing read path** so the channel settings view displays…」，主句是**扩展既有读路径**；#54 的验收第 1 条
才是「Add the channel-settings **write control** and `PATCH` route」，协调者的指针也写明「#54 只加写入口 +
UI」。因此本票交付 `GET /api/channels/{id}/budget` 的载荷扩展（`source` / `pending_limit`），
**不新增**频道设置面板。#54 只需把这两个字段渲染出来，并在同一面板上加写控件。
（`app/alibot/studio/` 本票 0 行改动 ⇒ 没有新增前端文案，`i18n.js` 与裸中文扫描不受影响。）

**② 测试缝沿用既有两层，不新开。** 读行为全部经 HTTP 黑盒（`create_app` + `ManualScheduler` + 脚本化替身）。
唯一需要「库里已有一条已保存上界」的用例，用**既有**库句柄接缝 `app.state.db`（`test_tasks.py` /
`test_auto_claim.py:29` 同一个、不是本票新开的）调用本票的配置落点 API 写入，再经 HTTP 断言读回。
#54 接上 `PATCH` 之后这批用例可平移到 HTTP 写路径。

## 2. 逐条验收判据 → 落点 → 证据

| # | 票面判据 | 实现落点 | 证据（命令 + 关键读数） |
| --- | --- | --- | --- |
| 1 | 库内逐频道上界覆盖，缺省回落内置默认 3；不引入环境变量 / 工作区级 / 成员级配置 | `db.py`：`channel_budget_limit(channel_id, budget_limit, updated_at)`（`budget_limit CHECK IN (3,5,10)`，外键 → `channel(id)`）+ `BUDGET_LIMIT_TIERS = (3, 5, 10)`；解析只有一处 `_budget_facts_locked` | `pytest tests/test_channel_budget_limit.py -q` ⇒ `19 passed`；其中 `test_an_unconfigured_channel_reports_the_built_in_default_and_its_source`（未配置 ⇒ `limit=3` / `source="built-in-default"` / `pending_limit=null`）、`test_the_override_stays_inside_its_own_channel`（配过的频道 5、没配的频道仍是 3、task 型 target 仍是 3）。**没有别的配置面**：`grep -rn "BUDGET_LIMIT\b" app/alibot/` 的命中只有 `db.py` 的定义与解析点，`db.py` 内 `budget` 与 `environ/getenv` 零交集 ⇒ 读路径不读环境变量 |
| 2 | 扩展既有读路径：展示当前世代 `used` / `remaining` / 有效上界 / 来源（频道覆盖 / 内置默认） | `db.channel_budget_view()` = `budget_state()` 五项 + `source` + `pending_limit`；`service.channel_budget()` 改调它；`api.py` 端点文档同步扩展 | 实测载荷（`/tmp/issue53_readings.py`，隔离数据目录）：未配置 ⇒ `{"channel_id":"ch_…","epoch_seq":1,"limit":3,"used":0,"remaining":3,"source":"built-in-default","pending_limit":null}`；下一个世代用上覆盖后 ⇒ `{"…","epoch_seq":2,"limit":5,"used":0,"remaining":5,"source":"channel-override","pending_limit":null}`。**既有字段名一个字不改**：`{"channel_id","epoch_seq","limit","used","remaining"} <= set(payload)`（`test_an_unconfigured_channel_reports_the_built_in_default_and_its_source`），task 型读路径载荷不变（`test_auto_claim.py::task_budget` 的逐字断言仍绿） |
| 3 | 已保存的下一世代上界与当前有效上界不同时单独展示；当前 `remaining` 按当前世代有效上界算 | `target_budget.applied_limit` 快照（开世代时写），`pending_limit` 只在「已保存 ≠ 当前有效」时非 null；`budget_limit` 的保存**不碰** `applied_limit` / `used` | `pytest tests/test_channel_budget_limit.py -q` 的 `test_a_saved_upper_bound_is_pending_until_the_next_human_epoch`（保存 5 后：`epoch_seq` 不变、`limit=3`、`remaining=3-used`、`pending_limit=5`；下一条人类消息后：`limit=5`、`source="channel-override"`、`pending_limit=null`）与 `test_remaining_is_calculated_from_the_current_epoch_effective_limit`（`used=1` + 保存 5 ⇒ `remaining=3-1=2`，不是 `5-1`）。硬上界同源：`test_the_hard_bound_of_the_current_epoch_ignores_the_pending_value`（待生效 5 不参与裁决 ⇒ 接力停在 `used=3` 并留 `budget-exhausted`）、`test_the_next_epoch_hard_bound_is_the_saved_upper_bound`（新世代 ⇒ 接力跑到 `used=5`） |
| 4 | `used` 读 `target_budget.used` 正本，不由 per-member round outcome 求和 | `_budget_facts_locked` 直接 `SELECT used FROM target_budget`；没有任何对 `round_outcome` 的聚合 | `test_used_is_the_backend_truth_not_a_sum_of_round_outcomes`：一次跳唤醒两个成员 ⇒ `consumed_budget=True` 的行恰好 2 条，而 `budget["used"] == 1`（行求和 2 ≠ 1）；既有的 `test_bounded_relay.py::test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch`（`used=3` 而标记行只有 2 条）仍绿 |
| 5 | 当前世代有效上界与 used 跨后端重启不变 | 快照写在 `target_budget` 行上（不是进程内存），并与 `used` / `epoch_seq` 同一行、同一事务落库 | `test_the_effective_limit_and_usage_survive_a_restart`（重启后 `budget` 载荷逐字相等：`epoch_seq`/`limit`/`source`/`used`/`remaining` 全同）、`test_the_pending_value_also_survives_a_restart`（重启后仍是「当前 3 / 待生效 5」，不会因为换进程就静默生效） |
| 6 | 只经既有 HTTP 黑盒 / 脚本化 seam 覆盖读行为，不新开测试缝 | 新用例全部走 `tests/conftest.py` 的 `client` / `launched` 夹具 + `send` / `drain` / `outcomes` + `app.state.db` 既有句柄 | 见 §1②；`pytest tests/test_channel_budget_limit.py -q` ⇒ `19 passed`，无新夹具、无新替身、无 `sqlite3` 直连 |

## 3. schema 14 的具体形态（ADR-0015 的「携带 from→to 步骤」声明）

- `SCHEMA_VERSION` **13 → 14**（`app/alibot/db.py`，号段记账写在常量上方的注释块里）。
- 注册 **`MigrationStep(13, 14, "回应预算的配置落点（channel_budget_limit + target_budget.applied_limit）", _step_13_to_14)`**。
  该步只做两件 DDL 级的事、**返回 0 行改动**：`_create_missing_tables(conn, ("channel_budget_limit",))`
  + `_add_missing_columns(conn, "target_budget")`（列定义连 `CHECK` 一起从 `SCHEMA` 解析）。
- 新增表 `channel_budget_limit(channel_id PRIMARY KEY → channel(id), budget_limit CHECK IN (3,5,10), updated_at)`；
  新增列 `target_budget.applied_limit INTEGER CHECK (applied_limit IN (3,5,10))`（**可空**）。
- **只前进、不回填**：存量 `target_budget` 行的 `applied_limit` 留 `NULL`，读路径把它解释为「该世代用的是
  内置默认值」——与本票之前的行为逐字等价（配置能力是这一版才出现的，旧世代不可能有频道覆盖）。
- 该步的验收读数（真实子进程）：

取证方式＝迁移测试既有的 T2 接缝（真实子进程）：用 `tests.test_schema_migration` 的
`_build_library(data_dir, 13)` 造一个**有业务数据**的 v13 库（频道 `ch_dev` 的 `target_budget`
已有 `used = 3`），再跑 `python -m alibot --migrate --data-dir <目录>`。

```console
exit: 0
源版本：13 → 目标版本：14
本次备份：/tmp/v13lib/data-backup-13-to-14-20261017T121833Z
  13 → 14  回应预算的配置落点（channel_budget_limit + target_budget.applied_limit）  改动 0 行
```

```console
$ pytest tests/test_channel_budget_limit.py -q
19 passed
# 其中 test_a_v13_library_migrates_to_fourteen_and_old_rows_read_as_the_default：
#   v13 库（target_budget 里已有 used = 3）迁到 14 ⇒ used 原样 3、limit 仍是内置默认 3、
#   source="built-in-default"、pending_limit=null；随后保存 5 ⇒ pending_limit=5（证明配置表已建出）。
$ pytest tests/test_migration_chain_guard.py tests/test_schema_migration.py tests/test_migrate_dry_run.py \
        tests/test_shape_conditioned_step.py -q
全部通过（链连续性 / 每个源版本迁到终态 / 预演 / 形态步骤）
```

## 4. 全量门禁读数（在 wt-53 根目录执行）

| 门禁 | 命令 | 读数 |
| --- | --- | --- |
| pytest（全量） | `cd app && env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY ../.venv/bin/python -m pytest` | **574 passed, 2 skipped**；2 skipped 都是既有跳过，与本票无关：
`test_agent_smoke.py`（真模型冒烟需 `ALIBOT_SMOKE_MODEL`）、`test_reminder_schema_step.py:314`
（本机没有 `libfaketime`，取不到「口径不依赖执行时刻」的证据） |
| pytest（本票） | `cd app && ../.venv/bin/python -m pytest tests/test_channel_budget_limit.py -q` | **19 passed** |
| ruff lint | `.venv/bin/ruff check app` | `All checks passed!` |
| ruff format | `.venv/bin/ruff format --check app` | `124 files already formatted` |
| mypy | `.venv/bin/python -m mypy`（**仓库根**；`mypy.ini` 的 `files = app/alibot, app/tests` 相对仓库根解析） | `Success: no issues found in 54 source files` |
| pyright | `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | `0 errors, 0 warnings, 0 informations` |
| 前端契约 | `node --test app/tests/{search_ui,agent_detail_ui,i18n_ui,rail_identity,mobile_shell}.test.cjs` | `tests 105 / pass 105 / fail 0` |

**已知噪声**（与本次改动无关，按协调者给的基线处置）：`test_agent_seam_client_lifecycle.py` 的
`InvalidURL: Invalid port: ':1]'` 需 `env -u http_proxy …`（上表已带）；`cd app && ../.venv/bin/python -m mypy`
会报 `Duplicate module named "__main__"`——已在**干净基线**（`git stash -u` 后同样命令）复现同一报错，
是配置路径按 cwd 解析的老问题，不是本票回归；权威跑法是仓库根 `mypy`（`mypy.ini` 注释明写）。

## 5. 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `app/alibot/db.py` | `SCHEMA_VERSION` 13→14（含号段记账）；`BUDGET_LIMIT` 注释改为「默认 3、可按档位覆盖」；新增 `BUDGET_LIMIT_TIERS` / `BUDGET_LIMIT_SOURCE_*` / `BUDGET_STATE_KEYS`；`SCHEMA` 加 `target_budget.applied_limit` 与 `channel_budget_limit` 表；新增 `_saved_budget_limit_locked` / `_budget_facts_locked`（**唯一**解析点）/ `_reset_epoch_budget_locked`（三种开世代动作共用）；`budget_state` 改读快照（载荷键集不变）；新增 `channel_budget_view` / `channel_budget_limit` / `set_channel_budget_limit`；`consume_budget` 的惰性建行连快照一起写 |
| `app/alibot/service.py` | `channel_budget()` 改调 `channel_budget_view()`（只做载荷扩展） |
| `app/alibot/api.py` | `GET /api/channels/{id}/budget` 的文档串补齐新增字段与边界（路由与字段名不变，无新端点） |
| `app/alibot/orchestration.py` | `_spend_jump` 与 `_record_budget_exhausted` 改用 `budget_state()["limit"]`（与只读面同一份解析结果），删掉 `BUDGET_LIMIT` 直读；相关文档串改成「该世代生效上界」 |
| `app/alibot/migrate.py` | 新增 `_step_13_to_14` 并登记 `MigrationStep(13, 14, …)` |
| `app/alibot/i18n.py` | 新增一对 `error.budget_limit_invalid`（zh / en；非法档位的可读拒绝文案，供 #54 的 `PATCH` 直接用）。**只进后端词条表**：它是 HTTP 错误的 `message`（ADR-0009 第二层），与既有 `error.budget_read_only` 同一体例，不越界写前端表 |
| `app/README.md` | 「有界响应（策略 B）」三条与本票相关的口径（重置改为满额 + 快照、硬上界改为「该世代生效上界」）+ `GET /api/channels/{id}/budget` 的载荷行 |
| `app/tests/test_channel_budget_limit.py` | **新增**（19 个用例）：本文件 §2 的全部判据 |
| `app/tests/_migration_history.py` | 补 v14 留档（`SOURCE_COMMITS[14]='pending'`，与 v13 同一体例待合并后回填；新增 `target_budget_v14` / `channel_budget_limit` 两个对象与 `VERSION_OBJECTS[14]`） |
| `app/tests/test_bounded_relay.py` | 频道预算载荷的**逐字**断言补上本票扩展的两个字段（字段名未改，只做扩展） |
| `app/tests/test_reminder_schema_step.py` | 4 处把「终态 = 13」的字面量改为 `SCHEMA_VERSION`（详见 §6） |

## 6. 与既有测试的三处冲突（都是「共享常量的字面量钉死」，不是行为回归）

1. **`test_reminder_schema_step.py::test_schema_version_is_thirteen_and_the_step_is_registered`**：
   `assert SCHEMA_VERSION == 13` 会在任何合法 bump 上假红。改成 `SCHEMA_VERSION >= 13` 并重命名
   （`…_is_at_least_thirteen`），理由与 #112 处置 `test_auto_claim.py` 里那个字面量 12 完全一致
   （`app/specs/issue-112-answer.md` 已记录同一体例）：`SCHEMA_VERSION` 是跨票递增的共享常量，钉死它
   把后续正常 bump 变成假红。提醒那一步本身仍由 `_step_line`（报告里必须有 `12 → 13`）与链连续性守卫取证。
2. 同文件另外 3 处「迁移终态 / 台账 `to` / 备份目录名 = 13」改为 `SCHEMA_VERSION`
   （分别是 `_version(...) == str(SCHEMA_VERSION)`、`records[-1]["to"] == SCHEMA_VERSION`、
   `-backup-{REMINDER_STEP_FROM}-to-{SCHEMA_VERSION}-`）。
3. **`test_bounded_relay.py`** 的频道预算载荷逐字断言补上 `source` / `pending_limit`：票面明写这个
   端点的既有字段名不变、只做扩展，因此「逐字等于五项」的断言必须跟着扩展到七项。

## 7. 遗留问题 / 相邻缺陷 / 不确定点（**未**自行修）

1. **「还没有任何世代」的频道里保存上界 —— 已按 #54 票面口径处置（review 收口）**：票面原句是
   「保存的改动不改变当前世代的有效上界与用量，从下一个人类世代生效，在此之前作为待生效值读回」，
   因此 `_budget_facts_locked` 在 `target_budget` **无行**时不再取「已保存值」：报
   `limit = 内置默认 3` / `source = "built-in-default"` / `pending_limit = 已保存值`（当且仅当 ≠ 3）。
   实机读数：无世代频道保存 5 ⇒ `{"epoch_seq": 0, "limit": 3, "used": 0, "remaining": 3,
   "source": "built-in-default", "pending_limit": 5}`；第一条人类消息开世代后 ⇒
   `{"epoch_seq": 1, "limit": 5, "remaining": 5, "source": "channel-override", "pending_limit": null}`。
   有预算行的频道逐字不变（读 `applied_limit` 快照）。新用例
   `test_a_saved_upper_bound_on_a_fresh_channel_is_pending_until_the_first_epoch` 加在
   `tests/test_channel_budget_limit.py`（本族由 19 条增至 **20 条**）；与之同口径的 HTTP 写面用例与
   实机读数见 `issue-54-acceptance.md` §8 第 1 条。
2. **保存与当前有效上界相同的档位**时 `pending_limit` 为 null（没有「会变」这回事），而 `source`
   要到下一世代开世代时才翻成 `channel-override`（因为快照那一刻才记下来源）。数值不变、来源变，
   这是诚实读数，不是抖动。
3. **task 型 target 不受频道覆盖影响**（本票的判据选择）：配置表主键是 `channel_id`，解析也只对
   channel 型 target 命中，因此 `GET /api/tasks/{id}/budget` 永远报内置默认。这与「逐频道配置」的
   票面口径一致（`test_the_override_stays_inside_its_own_channel` 已钉住），但如果 #55 实测后想让
   任务线程也吃频道上界，需要一次新的裁定。
4. **`app/alibot/repair.py:10` 的文档串仍写「本代码常量是 12」**（实际已是 14）。历史遗留的陈旧读数，
   与本票无关，未改。
5. **`app/README.md:165` / `:172` 仍用改名前的 `channel_budget` 表名**（ADR-0004 已改为 `target_budget`；
   第 172 行「预算落库（`channel_budget` 表）」）。旧表名在 `.py` 侧有守卫测试，但 README 无守卫；未改。
6. **`app/tests/_migration_history.py` 的 `SOURCE_COMMITS[13]` 仍是 `'pending'`**（#112 合并时未回填），
   本票照同一体例给 14 也留 `'pending'`；两份都应在合并后回填合并提交号。
7. **`Database.consume_budget` 的兜底分支存在一处口径不一致**（观察到、未改）：`previous[0] >= epoch_seq`
   时保留 `used`，但随后的 `ON CONFLICT` 会把 `epoch_seq` 改写成调用方传入的（可能更旧的）值，
   于是「保留新世代用量 + 回退世代号」可能同时发生。当前唯一调用方
   （`WakeOrchestrator._spend_jump`）总是传入刚读到的 `epoch_seq`，因此不可达；不属于本票写面。
8. **`app/alibot/api.py:21` 的模块级端点清单仍写「有界响应预算（只读，无 UI）」**：本票之后它是
   「频道设置视图的读数面」，但 UI 由 #54 交付，这一行等 #54 落地时再改更准确。
