# issue #54 验收取证：回应预算的人类面上界写入口 + 边界 ADR 核对

票面正本：[GitHub issue #54](https://github.com/whutlichao/alibot/issues/54)（`gh issue view 54`）。父规格：
`app/specs/issue-31-spec.md` §Implementation Decisions 2「已裁定的配置契约」、§Testing Decisions。
判据来源 ADR：`docs/adr/0007-human-face-writes-budget-limit-only.md`（本票**核对**它，见 §5）、
`docs/adr/0011-agent-config-write-path-is-a-patch-endpoint.md`（`PATCH` 写路径的先例）、
`docs/adr/0002-response-budget-caps-relay-at-three.md`（**原文不改**）、
`docs/adr/0015-schema-migration-is-explicit-and-forward-only.md`（本票**不动 schema**）。

- worktree：`/Users/apple/orca/workspaces/alibot/wt-54`
- 分支：`issue-54-impl`（从 `alibot-budget-silence/integration` 切出，base tip `7867d26` = 已合并 #53 与 #57）
- schema 号：**未动**，仍是 `SCHEMA_VERSION = 14`（把 14 推到 15 是 #58 的事）
- 上游复用：`channel_budget_limit` 表、`BUDGET_LIMIT_TIERS`、`Database.set_channel_budget_limit`
  （#53 已交付的**唯一写函数**）、`GET /api/channels/{id}/budget` 的 `source` / `pending_limit`

## 1. 范围与三处裁定（先写在这里，免得被误读成漏做）

**① 写入口是「薄的一层」：档位裁决与「只写上界」的边界仍只有一处落点。**
`PATCH` 的请求模型只做**形状**校验（`budget_limit` 必须是整数、用量字段一律拒绝），档位集合
`{3, 5, 10}` 与「不碰 `target_budget`」的边界由 #53 的 `Database.set_channel_budget_limit` 裁决。
服务层 `WorkspaceService.set_channel_budget_limit` 只是一句委托——两侧各判一次档位只会漂成两套事实。

**② Studio 落点：频道头的「回应预算」按钮 → 一张**频道设置**弹窗（不是工作区设置弹窗）。
理由三条：
1. 配置的正本是 `channel_budget_limit`（**库内逐频道项**），所以入口必须挂在**频道**面上；频道头
   已经是「频道范围的面」的既有先例（成员名册就在那里），这也是不加新导航层的最小改动。
2. 票面明令「**不要**改工作区设置弹窗的语义」：工作区设置的三档（语言 / 消息密度 / 主题）是
   **工作区级**取值，把频道级上界塞进去会让那个弹窗同时承载两种作用域。
3. 弹窗自带完整的读写面（读数 + 待生效 + 三档写控件 + 错误行），一次打开即可完成「看→改→读回」，
   不需要第二个面板或跳转。
   DM 下入口隐藏：预算读路径 `/api/channels/{id}/budget` 只认频道型 target（`renderHeader()` 里
   `$('#btnChannelBudget').hidden = isDm`）。

**③ 「新增文案同时进两张词条表」按 ADR-0009 的两层实际落法。**
两张表**各有自己的键集**（ADR-0009 明确不许有第三层，也不许同一句在两表各登一遍），因此本票的
「同时」是：**界面文案**（标题 / 读数标签 / 档位 / 提示 / 错误前缀）进 `studio/i18n.js` 的 zh-CN 与
en **两份取值**；**后端生成的自然语言**（非法档位、用量只读的拒绝原因）进 `app/alibot/i18n.py`
的 zh-CN 与 en 两份取值。两侧都由机器判据兜住：`i18n_ui.test.cjs`（键集比对 + 三件套裸中文扫描）与
`test_backend_copy.py`（后端两表键集比对）。

## 2. 逐条验收判据 → 落点 → 证据

| # | 票面判据 | 实现落点 | 证据（命令 + 关键读数） |
| --- | --- | --- | --- |
| 1 | 频道设置写控件 + `PATCH /api/channels/{id}/budget`（建在 ① 的库内配置与读路径上） | `api.py`：`ChannelBudgetUpdate`（`extra="forbid"`，只认 `budget_limit`）+ `update_channel_budget()`；`service.py`：`set_channel_budget_limit()`（委托 #53 的唯一写函数）；`studio/index.html`：`#btnChannelBudget`（频道头）+ `#budgetOverlay` 弹窗；`studio/app.js`：`openBudgetModal` / `loadChannelBudget` / `renderBudgetModal` / `selectBudgetTier` / `submitBudgetTier` / `closeBudgetModal` | 后端：`pytest tests/test_channel_budget_write.py -q` ⇒ **37 passed**；其中 `test_patch_saves_the_upper_bound_and_returns_the_refreshed_budget`（200 + 返回刷新后的载荷）。前端：`node --test app/tests/channel_budget_ui.test.cjs` ⇒ **11 passed**；其中「频道头的预算入口打开弹窗…」断言 `PATCH` 一次调用后读数与后端载荷逐项相同。实机读数见 §3 |
| 2 | 只接受固定档位 3 / 5 / 10，默认仍 3；非法值**不部分写入** | `db.py::set_channel_budget_limit`（`budget_limit not in BUDGET_LIMIT_TIERS` ⇒ `DomainError("validation_failed", error.budget_limit_invalid)`；配置表列级 `CHECK` 兜底）；`api.py::ChannelBudgetUpdate.budget_limit` 用 `Annotated[int, Field(strict=True)]`（`"5"` 不被静默当成 5） | `pytest tests/test_channel_budget_write.py -q` 的 `test_an_integer_outside_the_fixed_tiers_is_refused_without_a_partial_write`（`-10/-1/0/1/2/4/6/7/9/11/100` ⇒ 400，`message` 含 `3, 5, 10` 与「未保存」，读回等于写入前载荷，**重启后**也等于写入前）、`test_a_value_that_is_not_a_tier_integer_is_refused`（`"5"/"abc"/""/null/5.0/true/[5]/{...}/"10"` ⇒ 400 且读回不变）、`test_a_body_without_the_upper_bound_is_refused`（`{}` / 缺字段 ⇒ 400）、`test_every_fixed_tier_is_accepted`（3/5/10 全绿；保存 3 与内置默认相同 ⇒ `pending_limit` 为 `null`） |
| 3 | 逐频道持久化；**不**加环境变量 / 工作区 / 成员级覆盖 | 写路径只写 `channel_budget_limit(channel_id PK → channel(id))`；解析只有 `_budget_facts_locked` 一处 | `test_the_saved_upper_bound_stays_inside_its_own_channel`（A 保存 10 ⇒ A 的 `pending_limit=10`，B 仍 `limit=3` / `built-in-default` / `pending_limit=null`；同频道 task 型 target 仍 3）、`test_a_saved_upper_bound_survives_a_restart_and_still_activates_next_epoch`（重启后 `budget` 载荷逐字相等，仍是「当前 3 / 待生效 5」）。**没有别的配置面**：本票 0 行涉及 `os.environ` / 工作区设置表 / 成员行（`git diff` 清单见 §6） |
| 4 | 请求不能写 / 重置 `used`；`used` 只由后端开世代时重置、一次**实际投递**跳 +1 | `ChannelBudgetUpdate` 把 `used` / `remaining` / `epoch_seq` / `applied_limit` 声明为**不可写字段**，校验器一律抛 `_TierValueError("error.budget_used_read_only")`（比落成「未知键」多一句实质原因）；未知键由 `extra="forbid"` 整体拒绝；写函数本身不碰 `target_budget`（#53） | `test_a_request_that_writes_used_is_refused_and_usage_stays_backend_owned`：一次真实投递把 `used` 推到 1，随后 `PATCH {budget_limit:5, used:0}` ⇒ **400**，`used` 仍是 1、`pending_limit` 仍是 `null`（被拒的请求连上界也没写）。`test_every_usage_or_epoch_field_is_refused[used/remaining/epoch_seq/applied_limit]`：每个字段两种体形（单独带 / 与 `budget_limit` 同带）都 400 且读回不变。可观察读数见 §3 |
| 5 | 保存不改变当前世代的有效上界与用量；**下一人类世代**生效，在此之前作为待生效值读回 | 写路径只写配置表，当前世代的 `applied_limit` 快照与 `used` 一字不改（#53 的快照机制）；`pending_limit` 只在「已保存 ≠ 当前有效」时非 null | `test_patch_saves_the_upper_bound_and_returns_the_refreshed_budget`：保存 5 之后 `epoch_seq` / `limit=3` / `used` / `remaining` 全不变、`pending_limit=5`；下一条人类消息开新世代 ⇒ `limit=5` / `source="channel-override"` / `pending_limit=null`。`test_a_patch_while_a_round_is_in_flight_does_not_raise_the_current_bound`：**在途窗口**里写入 10 ⇒ 返回 `limit=3` / `pending_limit=10`，放行后接力仍停在 `used=3` |
| 6 | 展示必须区分**当前有效上界**与**下一世代设置**及两者**来源** | `renderBudgetModal()` 的 ①「当前世代」分区：`budget-used` / `budget-remaining` / `budget-limit` / `budget-source` 四行 + 耗尽行；②「下一个人类世代」分区：`budget-pending` 一行 + 说明（`budget.pendingEffective` / `budget.pendingSame`）；来源取值的说法由 `budgetSourceLabel()` 按 `source` 的**机器可读取值**取词条 | 前端：`channel_budget_ui.test.cjs` 的「四个读数都来自后端载荷」（`budget-used=1` / `budget-remaining=4` / `budget-limit=5` / `budget-source=频道覆盖`）、「待生效的下一世代值与当前有效上界分开呈现」（`budget-limit=3`、`budget-remaining=2`、`budget-pending=5`，正文含「下一个人类世代生效」）、「没有待生效差异时…写成与当前相同」（`budget-pending=3` + 「没有待生效改动」）。DOM 落点见 §4 |
| 7 | 零投递耗尽、per-target 用量正本、并发硬上界**不变** | 本票 0 行改动消费路径（`orchestration.py` / `db.consume_budget` / `_reset_epoch_budget_locked` 未动）；写入口只写配置表 | `test_the_current_epochs_hard_bound_ignores_a_saved_pending_tier`（当前世代保存 5 ⇒ 接力停在 `used=3`、`remaining=0`、留下 `budget-exhausted`）、`test_the_next_epochs_hard_bound_follows_the_saved_tier`（新世代按 5 裁决 ⇒ 接力跑到 `used=5` 后耗尽）、`test_a_zero_delivery_agent_message_still_does_not_consume_budget`（零投递 ⇒ `used=0`、`remaining=10`）、`test_a_patch_while_a_round_is_in_flight_does_not_raise_the_current_bound`。既有族仍全绿：`pytest tests/test_bounded_relay.py tests/test_channel_budget_limit.py -q` |
| 8 | ADR-0007 与实装边界一致；**不**修订 ADR-0002 | `docs/adr/0007-…md` **未改动一个字**；`docs/adr/0002-…md` 也**未改** | 见 §5 的逐句核对（结论：无需改动）。`git diff --stat docs/` 为空 |
| 9 | 覆盖成功写入 / 非法输入 / 拒绝用量写入 / 下一世代生效 / 频道隔离 / 重启持久 / 既有耗尽行为 | `app/tests/test_channel_budget_write.py`（新增，37 个用例）| 覆盖面逐条见上表第 1–7 行；命令与读数：`pytest tests/test_channel_budget_write.py -q` ⇒ **37 passed in 2.04s** |
| 10 | （派生）既有 `error.budget_read_only` 不能再说假话 | `i18n.py` 同键改写为「回应预算的**用量**只读：人类面只能改上界（PATCH），已用跳数与世代由后端独占维护」；`api.py` 的 `no_budget_mutation` 只留 `PUT` / `POST` / `DELETE`（`PATCH` 已是真写路径） | 实机读数见 §3 的 `PUT` / `POST` / `DELETE` 行（404 + 新文案）；`pytest tests/test_backend_copy.py -q` 仍绿（键集与参数位不变，只有取值改写） |
| 11 | （派生）新增文案两份词条齐备、三件套无裸中文 | `studio/i18n.js` 新增 27 个 `budget.*` 键（zh-CN / en 各一份）；`app/alibot/i18n.py` 新增 `error.budget_used_read_only`（zh / en 各一份）| `node --test app/tests/*.test.cjs` ⇒ **127 passed**（含 `i18n_ui.test.cjs` 的键集比对与裸中文扫描）；`channel_budget_ui.test.cjs` 的「新增的预算文案在两份词条里都存在…」把 `app.js` / `index.html` 里点名的预算键逐个对表 |
| 12 | （派生）不新开测试缝、不动 schema | 后端沿用 `conftest.py` 的 `client` / `launched` + `send` / `drain` / `outcomes`；前端沿用 `vm` + `fetch` 替身 + DOM 替身（与 `agent_detail_ui.test.cjs` 同款）；`SCHEMA_VERSION` 与 `MigrationStep` 未动 | `pytest tests/test_channel_budget_limit.py::test_the_schema_version_and_the_default_limit_are_what_the_ticket_says -q` ⇒ 通过（`SCHEMA_VERSION == 14`、`BUDGET_LIMIT == 3`）；`git diff app/alibot/migrate.py app/alibot/db.py` 为空 |

## 3. `PATCH` 的请求 / 响应形状与拒绝语义（实机读数）

采集方式：隔离数据目录 + `create_app` 的手动调度器与脚本化替身（`/tmp/issue54_readings.py`，只走 HTTP 黑盒）。

```console
GET              => 200 {"budget": {"channel_id": "ch_f37b0b1284ed", "epoch_seq": 1, "limit": 3,
                                    "used": 0, "remaining": 3, "source": "built-in-default",
                                    "pending_limit": null}}
PATCH {"budget_limit": 5}          => 200 {"budget": {"…", "epoch_seq": 1, "limit": 3, "used": 0,
                                        "remaining": 3, "source": "built-in-default",
                                        "pending_limit": 5}}
PATCH {"budget_limit": 4}          => 400 {"error": {"code": "validation_failed",
                                        "message": "回应预算上界只接受固定档位：3, 5, 10，未保存任何改动",
                                        "fields": {"budget_limit": "必须是这些之一：3, 5, 10"}}}
PATCH {"budget_limit": "5"}        => 400 {"error": {"code": "validation_failed",
                                        "message": "请求体校验未通过",
                                        "fields": {"body": "请求体不合法：body.budget_limit（int_type）"}}}
PATCH {"budget_limit": 5, "used": 0} => 400 {"error": {"code": "validation_failed",
                                        "message": "请求体校验未通过",
                                        "fields": {"body": "回应预算的用量与世代事实只读：人类面只能改上界（PATCH），used 由后端独占维护"}}}
PATCH {"used": 0}                  => 400 （同上：即使用量字段单独提交，也得到「用量只读」这句实质原因）
PUT    /api/channels/{id}/budget   => 404 {"error": {"code": "not_found",
                                        "message": "回应预算的用量只读：人类面只能改上界（PATCH），已用跳数与世代由后端独占维护"}}
POST   /api/channels/{id}/budget   => 404 （同上）
DELETE /api/channels/{id}/budget   => 404 （同上）
# 切到 English 后再提交用量字段：
PATCH {"used": 0}                  => 400 {"error": {"code": "validation_failed",
                                        "message": "Request body failed validation",
                                        "fields": {"body": "Response-budget usage and epoch facts are read-only: the human surface can only change the upper bound (PATCH); used stays backend-owned"}}}
# 下一条人类消息开新世代：
GET              => 200 {"budget": {"…", "epoch_seq": 2, "limit": 5, "used": 0, "remaining": 5,
                                    "source": "channel-override", "pending_limit": null}}
```

形状小结：

- **请求**：`PATCH /api/channels/{id}/budget`，body **只表达上界** `{"budget_limit": 3|5|10}`。
  `budget_limit` 必须存在且是**整数**（`strict=True`：`"5"` ⇒ 400）。任何用量 / 世代事实字段
  （`used` / `remaining` / `epoch_seq` / `applied_limit`）⇒ **400 + 「用量只读」可读原因**；
  其它未知键 ⇒ 400（`extra="forbid"`）。**所有拒绝都发生在服务层之前或档位裁决处**，
  配置表一个字都不写（用例已逐条断言读回与重启后无痕）。
- **响应**：成功 200，载荷 `{"budget": {…}}`，与 `GET` **同形**（`channel_id` / `epoch_seq` /
  `limit` / `used` / `remaining` / `source` / `pending_limit`），于是前端一次调用即可刷新
  「当前世代读数 + 待生效值」。`limit` / `used` / `remaining` / `source` 保持**当前世代**的事实，
  新值只出现在 `pending_limit`（与当前有效上界相同时为 `null`）。
- **其它动词**：`PUT` / `POST` / `DELETE` 仍是 404（既有「人类面不存在的写路径」形状），文案已按实机
  边界改写，不再宣称「不存在写端点」。

## 4. Studio 落点（DOM 断言读数即「截图式描述」）

**位置**：频道头（`#chanHead` 的 `.chan-tools`）第三个图标按钮 `#btnChannelBudget`
（`data-od-id="btn-channel-budget"`，title / aria-label = 词条 `budget.entryTitle` = 「回应预算」），
点击打开 `#budgetOverlay` → `.modal`（`aria-labelledby="budgetTitle"`）。

弹窗里的 DOM（全部由 `renderBudgetModal()` 用 `createElement`/`textContent` 构建，零 `innerHTML`）：

```text
#budgetTitle            回应预算                          （词条 budget.title）
#budgetSub              #设计系统重构 · 上界由你设置；已用跳数由后端维护   （budget.sub，频道名来自工作台快照）
#budgetBody
  ── 分区「当前世代」（data-od-id=budget-section-current）
     .kv  已用        → data-od-id=budget-used        「1」
     .kv  剩余        → data-od-id=budget-remaining   「2」
     .kv  有效上界    → data-od-id=budget-limit       「3」
     .kv  来源        → data-od-id=budget-source      「频道覆盖」/「内置默认」（source 取值的界面说法）
     （remaining == 0 时追加两行）
     .kv  状态        → data-od-id=budget-exhausted   「预算耗尽」
     .field .hint     → data-od-id=budget-exhausted-note
                         「本轮零投递：Agent 消息照常落库，只是不再唤醒任何人（不是出错）。」
                         ——它**不在** #budgetFormError（错误区）里，耗尽不显示成出错
  ── 分区「下一个人类世代」（data-od-id=budget-section-next）
     .kv  已保存上界  → data-od-id=budget-pending     「5」（无待生效差异时显示当前值）
     .field .hint     → data-od-id=budget-pending-note
                         「这是已保存的档位，从下一个人类世代生效；当前世代仍按上面的有效上界算。」
                         或「与当前上界相同，没有待生效改动。」
  ── 分区「上界档位」（data-od-id=budget-section-tiers）
     .radio-row 三个 .radio-card（data-budget-tier=3/5/10，aria-pressed=…）：
       3 跳（默认） | 5 跳 | 10 跳
     .field .hint     → data-od-id=budget-tier-hint（「只有这三档；其它取值后端会整体拒绝…」）
     .agent-actions > 保存上界 按钮（data-budget-save=1；与已保存档位相同或离线时禁用）
#budgetFormError        失败时才显示（`.form-error`），文案 = budget.saveFailed + 后端给的原因
#budgetDone             关闭（词条 common.close）
```

**行为读数**（`node --test app/tests/channel_budget_ui.test.cjs`，11 passed）：

| 用例 | 关键读数 |
| --- | --- |
| 入口打开、四个读数 + 来源 | `#budgetOverlay.hidden=false`；`budget-used=1` / `budget-remaining=4` / `budget-limit=5` / `budget-source=频道覆盖`；`#budgetTitle=回应预算` |
| 待生效与当前分离 | `budget-limit=3`、`budget-remaining=2`、`budget-pending=5`，正文含「下一个人类世代生效」 |
| 无待生效差异 | `budget-pending=3` + 「没有待生效改动」 |
| 写控件只有三档 | `data-budget-tier` 依次 `["3","5","10"]`；默认选中 3（`aria-pressed=true`）；选 5 后 `aria-pressed` 翻转 |
| 提交形状 | 一次保存只发一个 `PATCH /api/channels/ch_1/budget`，body 逐字等于 `{"budget_limit": 5}`（**不含任何用量字段**） |
| 保存后读回 | 新载荷 `pending_limit=10` ⇒ `budget-pending=10`，`budget-limit` 仍是 `3`；toast 含「下一个人类世代生效」；错误行隐藏 |
| 后端拒绝 | `#budgetFormError.hidden=false`，文案含「只接受固定档位」与 `3, 5, 10`；读数保持后端说的那一份（`budget-pending=3`） |
| 预算耗尽 | `budget-exhausted=预算耗尽`，正文含「本轮零投递」，`#budgetFormError.hidden=true` |
| 切语言 | `applySettings({locale:'en'})` 后 `#budgetTitle=Response budget`、`budget-source=Built-in default`、`budget-exhausted=Budget exhausted` |
| DM 无入口 | DM 激活时 `#btnChannelBudget.hidden=true`；切回频道 `false` |
| 关闭落点 | ✕ / 底部关闭 / 点遮罩三条都能关；关掉后 `state.channelBudget.budget=null`（不缓存过期读数） |

## 5. ADR-0007 核对结论：**无需改动**

逐句核对（左＝ADR 原文，右＝实装事实与取证）：

| ADR-0007 原句 | 实装事实 | 取证 |
| --- | --- | --- |
| 「允许在频道设置新增 `PATCH /api/channels/{id}/budget`」 | 路由路径与动词逐字相同；入口在频道头 → 频道设置弹窗 | §3 实机读数；§4 DOM 读数 |
| 「但只可写上界、不可写用量」 | body 只有一个可写字段 `budget_limit`；`used` / `remaining` / `epoch_seq` / `applied_limit` 一律 400；写函数不碰 `target_budget` | §3 `PATCH {…, used: 0}` ⇒ 400；`test_a_request_that_writes_used_is_refused_and_usage_stays_backend_owned` |
| 「配置存于库内逐频道项并缺省回落到内置默认值」 | `channel_budget_limit(channel_id PK → channel(id))`；无行 ⇒ `BUDGET_LIMIT = 3` | `test_the_saved_upper_bound_stays_inside_its_own_channel`；`test_the_pending_value_also_survives_a_restart`（#53） |
| 「变更从下一个人类世代生效」 | 当前世代 `applied_limit` 快照不变，新值只作 `pending_limit` | §3 的 `epoch_seq: 1 → 2` 两段读数 |
| 「后端仍只在开世代动作落库时重置 `used`，并在一次 Agent→Agent 跳实际投递时推进一次」 | 消费路径 0 行改动；写入口不落消息、不唤醒、不消耗预算、不开世代 | §2 第 7 行；`test_a_zero_delivery_agent_message_still_does_not_consume_budget` |
| 「不削弱跳数正本与零投递耗尽语义」 | 同 #53：`used` 仍读 `target_budget.used`；耗尽仍零投递并留 `budget-exhausted` | `test_the_current_epochs_hard_bound_ignores_a_saved_pending_tier` |
| 「默认值仍为 3，本 ADR 不修订或取代 ADR-0002」 | 默认仍 3（`BUDGET_LIMIT = 3`）；`docs/adr/0002-…md` 未改 | `git diff --stat docs/` 为空；`test_the_schema_version_and_the_default_limit_are_what_the_ticket_says` |

**结论：ADR-0007 与实装边界一致，不做任何改写**（只字未动）。唯一需要「跟随实机」的文案不在 ADR 里，
而是 `error.budget_read_only` 那句「人类面不存在写入它的端点」——它在 #54 之后是假话，已就地改写（§2 第 10 行）。

## 6. 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `app/alibot/api.py` | 新增 `ChannelBudgetUpdate`（`extra="forbid"`；`budget_limit` 严格整数；用量 / 世代字段一律拒绝并给「用量只读」词条）；新增 `PATCH /api/channels/{id}/budget` 路由 `update_channel_budget()`；`no_budget_mutation` 的动词收窄为 `PUT` / `POST` / `DELETE`；`_validation_detail` 优先采用校验器带回的词条（于是「缺必填 + 提交用量」的体形得到实质原因而不是旁支）；模块端点清单与 `GET` 文档串同步 |
| `app/alibot/service.py` | 新增 `set_channel_budget_limit()`（薄委托；写明「配置不是对话事实」） |
| `app/alibot/i18n.py` | 改写 `error.budget_read_only`（zh / en，去掉「不存在写端点」的假话）；新增 `error.budget_used_read_only`（zh / en） |
| `app/alibot/studio/index.html` | 频道头加 `#btnChannelBudget`；新增 `#budgetOverlay` 频道设置弹窗骨架（`#budgetBody` / `#budgetFormError` / `#budgetClose` / `#budgetDone`），静态中文全挂 `data-i18n` |
| `app/alibot/studio/app.js` | 新增 `budget.*` 常量与 `budgetSourceLabel` / `savedBudgetTier` / `openBudgetModal` / `loadChannelBudget` / `closeBudgetModal` / `selectBudgetTier` / `submitBudgetTier` / `budgetHint` / `renderBudgetModal`；`state.channelBudget` 镜像；`renderHeader` 隐藏 DM 入口；`applySettings` 重铺弹窗；`bindEvents` 三个入口 + 正文点击委派；ESC 关闭；文件头交付说明 |
| `app/alibot/studio/i18n.js` | 新增 27 个 `budget.*` 键（zh-CN / en 各一份） |
| `app/README.md` | API 清单加 `PATCH /api/channels/{id}/budget` 一行；「有界响应」一节加「人类面只能写上界」一条 |
| `app/tests/test_channel_budget_write.py` | **新增**（37 个用例）：§2 第 1–7、9 行的后端取证 |
| `app/tests/channel_budget_ui.test.cjs` | **新增**（11 个用例）：§4 的前端取证（已并入门禁命令 `node --test app/tests/*.test.cjs`） |
| `app/tests/test_bounded_relay.py` | 一处既有断言跟随实机边界：`PUT/PATCH/DELETE/POST` 全部 404 ⇒ 改为 `PUT/DELETE/POST` 仍 404，并注明「上界只有 `PATCH` 一条写路径（#54 / ADR-0007）」 |
| `app/specs/issue-54-acceptance.md` | 本文件 |

**未改动**：`docs/adr/**`（含 ADR-0002 / ADR-0007）、`app/alibot/db.py`、`app/alibot/migrate.py`、
`app/alibot/orchestration.py`、`app/alibot/studio/app.css`（弹窗复用既有 `.overlay` / `.modal` /
`.kv` / `.radio-card` / `.d-sec` / `.form-error`，**零新色位、零新组件类**）。

## 7. 门禁读数（在 wt-54 根目录执行）

| 门禁 | 命令 | 读数 |
| --- | --- | --- |
| pytest（本票） | `cd app && ../.venv/bin/python -m pytest tests/test_channel_budget_write.py -q` | **37 passed in 2.04s** |
| pytest（相邻两族） | `cd app && ../.venv/bin/python -m pytest tests/test_channel_budget_limit.py tests/test_bounded_relay.py tests/test_backend_copy.py tests/test_settings.py tests/test_shape_conditioned_step.py -q` | **86 passed in 14.36s**（#53 的读路径族 / 接力硬上界族 / 后端词条族 / 设置族 / 形态步骤，无 fail） |
| pytest（全量） | `cd app && env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY ../.venv/bin/python -m pytest -q` | **614 passed, 2 skipped in 302.88s**（2 skipped 都是既有跳过：`test_agent_smoke.py` 需 `ALIBOT_SMOKE_MODEL`；`test_reminder_schema_step.py` 需 `libfaketime`） |
| ruff lint | `.venv/bin/ruff check app` | `All checks passed!` |
| ruff format | `.venv/bin/ruff format --check app` | `128 files already formatted` |
| mypy | `.venv/bin/python -m mypy`（**仓库根**） | `Success: no issues found in 55 source files` |
| pyright | `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | `0 errors, 0 warnings, 0 informations` |
| 前端契约（全量） | `node --test app/tests/*.test.cjs` | `tests 127 / pass 127 / fail 0` |
| 前端契约（本票） | `node --test app/tests/channel_budget_ui.test.cjs` | `tests 11 / pass 11 / fail 0` |

**已知噪声**（协调者给的基线，与本次改动无关）：`cd app` 里跑 `mypy` 会报
`Duplicate module named "__main__"`（配置路径按 cwd 解析的老问题），权威跑法是仓库根 `mypy`；
涉及出网替身的用例需 `env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY`（上表已带）。

**合并 integration（交付前）**：`git merge alibot-budget-silence/integration` ⇒ `Already up to date.`
（该分支 tip 仍是本票的 base `7867d26`，没有新的前进/冲突）。合并后上表门禁**全部重跑**，读数与合并前一致
（`614 passed, 2 skipped in 302.88s` / `127 pass` / `0 errors`），故不需要解冲突后的额外回归。

## 8. 遗留问题 / 相邻缺陷 / 不确定点（**未**自行修）

1. **「还没有任何人类世代」的频道里保存上界，读路径直接按已保存值报**（#53 的既有边界，见
   `issue-53-acceptance.md` §7 第 1 条）：`target_budget` 里没有行时没有「当前世代的有效上界」可
   快照，于是 `limit` 立刻是新值、`pending_limit=null`。界面上表现为：toast 说「从下一个人类世代生效」，
   而「下一个人类世代」那一行说「与当前上界相同」。两句都真（第一次世代就会用这个值），但放在一起
   略绕；要不要为 `epoch_seq == 0` 单独换一句文案，需要一次产品裁定，本票不擅自加。
2. **非整数档位（`"5"` / `null` / `5.0`）得到的是通用字段文案**（`请求体不合法：body.budget_limit（int_type）`），
   而不是「只接受固定档位 3, 5, 10」那句。理由是它们属于**类型**错误、与**档位**错误不是一类；
   两句都可读，但如果希望所有拒绝都收敛到同一句，需要把类型校验也搬进库层（会削弱「请求模型只做形状
   校验」的分层），本票不做。
3. **`PUT` / `POST` / `DELETE` 的拒绝码仍是 `not_found`**，而新文案提到「只能改上界（PATCH）」：
   码与文案的措辞略不对称。保留码是因为它是既有契约（`test_bounded_relay.py` 等处按 404 断言），
   改码会动到本票写面之外的语义；改文案已足以不留假话。
4. **`app/README.md:168` / `:170` 仍用改名前的 `channel_budget` 表名**（ADR-0004 已改为
   `target_budget`），`app/alibot/repair.py:10` 的文档串仍写「本代码常量是 12」（实际 14），
   `app/tests/_migration_history.py` 的 `SOURCE_COMMITS[13]/[14]` 仍是 `'pending'`：三条都是 #53
   已记录的既有陈旧读数，与本票无关，未改。
5. **`_validation_detail` 改动是共享的**（`AgentUpdate` / `SettingsUpdate` 的显式 `null` 也走它）：
   行为从「取第一条错误的词条」变成「优先取任意一条校验器词条」。它只影响**同时**含「校验器词条错误」
   与别的错误的体形（这两个模型的字段都非必填，既有用例里不出现），全量 pytest 已确认无回归；
   但如果将来有人依赖「首条错误优先」，这里需要重新裁定。
6. **Studio 没有自动化浏览器端到端**（沿用既有 DOM 替身族，不起真浏览器）：`fetch` 的失败分支
   （网络断）在真机上由 `error.httpStatus` 兜底，本票只用替身覆盖了「后端返回 400」这条主路径。
