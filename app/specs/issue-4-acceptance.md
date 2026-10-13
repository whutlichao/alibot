# 实施票 ③ 验收取证（issue #4）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。
issue 上的 Answer 摘要指向本文件。

> 阅读说明：§3 的表格指到具体用例（跑 `pytest -k <名字>` 即可复现）；§4 是**多次**真实浏览器 /
> 真实进程实测的输出片段，每次都跑在独立临时数据目录上，因此频道 id 与 seq 起点在各片段间不同。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-4-bounded-relay`（Orca 独立顶层 worktree）
- 基线：以 #3 已合并的 main（merge commit `4126dd4`）为基线
- 采集时间：2026-10-13
- 环境：macOS，Python 3.11（`uv venv`），真实 Chromium（ego-browser）驱动
- 有效用例数：**142 passed, 1 skipped**（`app/tests/`；跳过的就是那条默认跳过的真模型冒烟测试）
- 票标题（开工时用 `gh issue view 4` 核对）：

> 实施票 ③：多 Agent 同频道接力、普通消息唤醒与自判、未加入被点名穿透、agent→agent 预算 ≤3 与并发/失败隔离、刷新后可追溯

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：读票
（`gh issue view 4` + `gh issue view 1` §4/§5）→ 声明票标题 → 按 `tdd` 把每条契约写成
HTTP/SSE 黑盒测试 → 定期跑类型检查与单文件测试 → 全量 → `code-review` 双轴 → 提交 → PR。

票面还有一条**硬性流程要求**：任何影响交付行为/方案分叉/scope 边界的决策必须走 `ask`，不自行拍板。
开工前与实现中一共提了 4 组问题，人类/coordinator 的裁定见 §5。

## 1. 门禁结果（全量）

```text
$ .venv/bin/python -m pytest
142 passed, 1 skipped in 92.95s        # skipped = 默认跳过的真模型冒烟测试

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 28 files already formatted

$ .venv/bin/python -m mypy             # 配置在仓库根 mypy.ini
Success: no issues found in 23 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
errors 0 files 23
```

## 2. 用例分布（相对 #3 的 115 条）

| 文件 | 用例数 | 本票变化 |
| --- | --- | --- |
| `test_empty_workspace.py` | 5 | 未动 |
| `test_create_channel.py` | 9 | 未动 |
| `test_send_message.py` | 12 | 未动 |
| `test_channel_read.py` | 12 | 未读口径修正（见 §6），计数类断言改用非 owner 作者取证；新增 2 条 |
| `test_create_agent.py` | 8 | 未动 |
| `test_remote_data_flow.py` | 10 | 未动 |
| `test_mentions.py` | 14 | 未动 |
| `test_agent_round.py` | 32 | 合并语义用例改为「收口时落一行、max seq」；未读期望按新口径调整 |
| `test_bounded_relay.py` | **24（新）** | 本票核心：唤醒面、穿透、上界、合并、失败隔离、恢复、未读、不可变性 |
| `test_event_stream.py` | **16（+2）** | 新增 system 加入事件推送/补拉、预算耗尽在事件流可见 |
| `test_agent_smoke.py` | 1 | 未动（默认跳过） |
| 合计 | **143**（142 跑 + 1 skip） | |

## 3. 逐条验收（#4 全部验收项）

### 3.1 多 Agent 成员与普通消息唤醒

| 验收项 | 用例 |
| --- | --- |
| 频道可加入多个 Agent 成员（显式 `channel_member`，无静默自动加入） | `test_plain_message_wakes_every_channel_agent_member_and_excludes_the_author`（两个成员）+ #3 既有 `test_create_agent.py`；`#all` 自动加入语义不保留（`db.py` 无该路径） |
| 未点名任何人的消息唤醒该频道**全部** agent 成员、**排除作者自己** | 同上（丙在别的频道不被唤醒；唤醒集恰为 {甲,乙}）；`test_agent_author_is_excluded_from_the_wake_set_not_only_humans`（作者是 Agent 时同样排除：甲只被人类消息唤醒一次，乙是被**甲的那条回复**唤醒的，且不存在「甲以自己回复为 base_seq」的轮次） |
| 唤醒是注意力信号、不是命令；`ignored` 是正常协议选择 | `test_ignored_and_failed_are_distinguishable_in_the_same_channel`（ignored 与 failed 同频道并存）、`test_ignore_leaves_no_fake_acknowledgement`（忽略不产生伪造确认） |
| 被个人提及 → **必须回应**；不回应按 `failed`、不静默，前端可区分 | #3 既有 `test_mention_is_a_certain_signal_so_ignoring_it_is_failed`（本票未改：`failed` + `--error`）与 `test_clearly_irrelevant_message_is_ignored_not_failed`（`ignored` + `online`），两者并置即「不想说 vs 坏了」的区分面 |

### 3.2 穿透

| 验收项 | 用例 / 证据 |
| --- | --- |
| `@` 未加入的 Agent 也被唤醒送达（不需先手动拉进频道） | `test_mentioning_an_outsider_wakes_it_without_joining`（`calls_for(outsider)` 有 1 条，且成员关系**未**新增） |
| 它若要回复：回复落库时顺带加入 + 追加一条 `system` 事件消息 | `test_pierced_agent_that_replies_joins_and_leaves_a_system_event`：`kinds == ["human","system","agent"]`、`system.seq == reply.seq - 1`（**人类裁定：system 先、回复后**）、`system.author.id == outsider`、正文含「加入」、`channel_member` 新增该行、轮次 reason 里含「穿透」与 system seq |
| 它若不回复则不加入 | `test_mentioning_an_outsider_wakes_it_without_joining`（成员关系只随真实回复发生） |
| 穿透同样受有界响应约束（不豁免预算） | `test_piercing_wake_consumes_a_jump_like_any_other`（甲的 Agent 消息穿透唤醒局外人 ⇒ `used == 1`）；`test_piercing_is_suppressed_once_the_budget_is_exhausted`（耗尽世代里穿透目标也拿到 `budget-exhausted` 结论） |
| 已经加入后不再重复写 system 事件 | `test_a_second_piercing_join_is_skipped_and_the_agent_stays_joined`（`kinds.count("system") == 1`，而 agent 回复有 2 条） |

### 3.3 有界响应（策略 B，本票核心）

| 验收项 | 用例 / 证据 |
| --- | --- |
| 人类世代 = 最近一条人类消息的 `seq`；人类消息落库即重置预算为 3 | `db.append_message` 在 `kind == "human"` 时**同事务**写 `channel_budget(epoch_seq=seq, used=0)`（落库那一刻的事实，不是惰性重置）；`test_a_new_human_message_resets_the_budget_and_the_relay_works_again` 断言 `used==0 && remaining==3 && epoch_seq > limit` |
| 一次 agent→agent 跳 = 一条 Agent 消息导致**至少一个其它** Agent 被唤醒 | `test_a_zero_delivery_agent_message_does_not_consume_budget`（唤醒集为空 ⇒ 不是跳）；`test_a_message_whose_targets_are_all_in_flight_...` 的反面由 `_spend_jump` 的「全部 in-flight ⇒ 不扣款」覆盖（见 §3.4） |
| 一条消息即使唤醒多个 Agent 也只消耗 1 次预算 | `test_multiple_targets_from_one_message_cost_only_one_jump`：甲的回复唤醒乙与丙（两名投递）⇒ `used == 1` |
| 预算耗尽：Agent 消息照常落库，但**零投递**，轮次记 `budget-exhausted` | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch`：`used == 3`、`remaining == 0`、agent 消息 ≥ 4 条（**照常落库**）、存在 `budget-exhausted` 结论；`_record_budget_exhausted` 为每个被压制成员各记一条 |
| 人类消息随后落库即重置，往返重新可用 | 同 §3.3 第一行 |
| **硬上界 ≤ 3**（可验收断言） | 由「耗尽的世代里 `_spend_jump` 直接 return 0（一个都不投递）」**构造性**保证；`test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch` 直接断言 `used <= 3` 且恰好 `== 3`；真实浏览器实测见 §4.1 |
| 能构造「两个互相 `@` 的 Agent」并得到有界结果 | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch`、`test_piercing_is_suppressed_once_the_budget_is_exhausted`、`test_budget_state_is_persisted_and_survives_restart`、`test_no_message_edit_or_delete_path_even_after_budget_exhaustion` 都是双 Agent 互 @；§4.1 用真实浏览器跑同构场景 |
| 穿透、并发合并不破坏上界：跳数以**投递的唤醒**为准 | `_spend_jump` 先判「是否有非 in-flight 目标」（全 in-flight ⇒ 零投递 ⇒ 不扣款），再在同一把锁内扣款；合并行的 `consumed_budget` 恒为 False，`test_repeated_wakes_during_a_round_are_merged_into_one` 断言 `budget.used == 0` |
| 预算 used/remaining 是**后端持久事实** | `channel_budget` 表 + `budget_state()` 读库；`test_budget_state_is_persisted_and_survives_restart`（重启后 `budget == before`，新人类消息在新进程里照常重置） |

### 3.4 并发、预算与失败隔离

| 验收项 | 用例 |
| --- | --- |
| 同一 Agent 最多一轮 in-flight；进行中的重复唤醒**合并为一次**、以最大 `seq` 为准、不排队不重放，合并记入轮次结果 | `test_repeated_wakes_during_a_round_are_merged_into_one`：两条重复唤醒 ⇒ **恰好一条** merged 行、`base_seq == 3`（最大 seq）、`merged is True`、模型只被调用 **1** 次；`_record_merged` 在收口时落行 |
| 「不重放」≠「永久吞掉」 | `test_merged_wakes_are_not_replayed_but_not_swallowed`：第一轮只读到「第一条」（第二条被合并未重放），下一次**新的**唤醒把被合并那条一并读到 |
| 目标全部 in-flight ⇒ 零投递 ⇒ 不算一次跳 | `_spend_jump` 的 `any(... not in self._in_flight)` 判据；`test_a_message_whose_targets_are_all_in_flight_...` 用例名保留在 §3.3 的零投递形态里（`test_a_zero_delivery_agent_message_does_not_consume_budget` 是它在单成员频道里的可确定形态） |
| 一个 Agent 的失败不影响其它 Agent 与人类发送 | `test_one_agents_failure_does_not_stop_others_or_human_sending`（坏的 `failed` / 好的 `replied` / 后续人类消息 201） |
| 失败轮次不写半条消息、状态点 `--error`；缺模型/凭证被 `@` 时 reason 可读 | 同上（流里没有坏成员的消息、`status == "error"`）；`test_unavailable_agent_reason_is_readable_when_mentioned`（reason 含 provider 与 `DASHSCOPE_API_KEY`，状态点 `offline`） |
| 预算耗尽**不**等于出错（状态点仍空闲） | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch` 末尾断言两个 Agent 的 `status == "online"`；`status_of` 只把 `failed` 当 `--error` |
| 后端重启后进行中的轮次不被自动重放 | `test_in_flight_round_is_not_replayed_after_restart`（重启后 pending 0、seam 调用 0、无轮次结果、状态点 `online`） |

### 3.5 未读与 system 口径

| 验收项 | 用例 |
| --- | --- |
| Agent 回复计入未读；作者为本人时不计入自己的未读 | `test_agent_replies_count_toward_unread_and_the_system_event_does_not`（Agent 回复计 1，owner 自己那条 0）；`test_owner_authored_messages_do_not_count_toward_own_unread`（见 §6） |
| 正在查看该频道时到达的 Agent 回复立即已读，其它频道累计 | `test_agent_replies_count_toward_unread_...` 末段（标读后归零）；#3 既有 `test_agent_reply_is_read_immediately_when_viewing`；真实浏览器见 §4.2 |
| 穿透加入产生的 `system` 事件消息**不计入**未读（用**真实**穿透事件取证） | `test_agent_replies_count_toward_unread_and_the_system_event_does_not`：先断言 `kinds == ["human","system","agent"]`（确证发生了真实穿透加入），再断言 `unread_count == 1`（若 system 计入会是 2） |
| 刷新浏览器或重启后端后已读位置、未读计数与消息仍正确 | `test_system_event_unread_semantics_survive_restart`；#3 既有 `test_unread_survives_browser_and_backend_restart`、`test_unread_including_agent_replies_survives_restart` |
| 三处读数（列表 / 单条 / workspace 快照）同一口径 | `test_unread_readings_across_endpoints_share_one_rule`（三者相等且 == 1）；口径只在 `db.py` 的 `CHANNEL_SELECT_SQL` 写一次 |

### 3.6 可追溯与恢复

| 验收项 | 用例 |
| --- | --- |
| 每个有结论的轮次留下完整 `round_outcome`（member_id/target_id/base_seq/status/reason/起止时间/consumed_budget/merged） | `test_positive_loop...`（#3 既有，逐字段断言）+ 本票新增的 merged / budget-exhausted 行；端点 `GET /api/channels/{id}/round-outcomes` |
| 「为什么它当时没说话」可解释 | `budget-exhausted` 的 reason 写明人类世代 seq 与「≤3 已用尽」；`merged` 的 reason 写明被合并次数与最大 seq；`failed` 保留适配层原始理由 |
| 重启后成员关系、消息、轮次结果与**预算状态**全部从库恢复 | `test_members_messages_outcomes_and_budget_survive_restart`（四者逐一对比）；`test_budget_state_is_persisted_and_survives_restart` |
| 首版**不做**可观测页面 | 无 UI 变更涉及轮次/预算；只新增只读数据端点 `GET /api/channels/{id}/budget`（与 #3 的 round-outcomes 同性质，人类裁定 Q3=A） |
| 消息 append-only 硬约束不被打开 | `test_no_message_edit_or_delete_path_even_after_budget_exhaustion`：耗尽世代后所有消息的 `DELETE`/`PUT`/`PATCH` 均 404、流未变，预算端点四种写方法也 404；#3 既有 `test_round_outcome_is_append_only`、`test_agent_reply_body_is_immutable` |

### 3.7 视觉与交互契约

| 验收项 | 证据 |
| --- | --- |
| system 事件消息用既有消息行形态（方形头像色位 + 作者名 + 时间），与正文可区分但**不新造色位** | `app.js` 的 `messageNode()` 对 `kind === 'system'` 复用 `avatarNode(author.display_name, author.avatar_slot, 'sm')`（**该成员自己的 --av-\* 色位**）+ `.msg-author` + `.msg-time` + 一个 `<span class="msg-tag">`「事件」capsule；`app.css` 只用既有 `--muted` / `--border` / `--mono`。浏览器 DOM 取证见 §4.2（`avatar sm av-2`）与截图 `penetration-system-row.png` |
| 未读徽标仍只用 `--unread` 族 + mono + `tabular-nums` | `app.css` 的 `.badge` 未改（`var(--unread)` / `var(--mono)` / `tabular-nums`），本票未新增任何色位 |
| 四态状态点只表达后端事实；`working` 脉冲在 `prefers-reduced-motion` 下关闭 | `.presence.working` / `.typing-line::before` 的 `@media (prefers-reduced-motion: reduce) { animation: none }` 未改；本票改动只在 `status_of` 的语义（budget-exhausted 不再算 error） |
| 简体中文、68ch/14px/1.65、提及高亮、Agent capsule 沿用 | `app.css` 未改这三项；新增的 `.msg.sys .msg-text` 同样写 `max-width: 68ch; line-height: 1.65`；`mention` / `.msg-tag` 规则未改 |
| 视觉仍只**只读**参照 `worksplice-design-system/` | `git diff 4126dd4...HEAD --stat` 无 `worksplice-design-system/` 条目（见 §7） |

### 3.8 测试范式

| 验收项 | 证据 |
| --- | --- |
| 复用 #③ 的**唯一高层接缝**与脚本化替身，不另开第二套 | 新用例只用 `tests/conftest.py` 的 `client`/`harness`/`launched`/`live` 夹具 + `tests/scripted.py` 的 `ScriptedAgentSeam`（`REPLY`/`IGNORE`/`FAIL`/`DELAY`）/`ManualScheduler`；没有第二种注入方式 |
| 每条测试独立临时数据目录 | 全部经 `data_dir` / `harness` / `launched` / `live` 夹具（`tmp_path`）；人工实跑用 `/tmp/wsps-e2e-data-4`，**不是** `~/Worksplice` |
| 只断言 HTTP 面与事件流可观察行为 | 新用例只读 `GET /api/...`、`POST`、SSE 帧；不断言内部函数调用、私有结构、日志文案或 DOM 细节（§4 的浏览器取证在 pytest 之外，见 §8 遗留） |
| 事件流面（真实 HTTP）新增覆盖 | `test_event_stream.py::test_system_join_event_is_pushed_as_a_message_and_replayable`（system 走**同一类** `message` 事件 + 重连补拉不重复不错序）、`::test_budget_exhaustion_is_visible_in_the_round_outcome_event` |

## 4. 以独立临时数据目录实跑（后端 + 真实浏览器）

数据目录 `/tmp/wsps-e2e-data-4`（**不是** `~/Worksplice`），后端是真实 uvicorn 进程，
浏览器是真实 Chromium（ego-browser）。Agent 行为由**脚本化替身**固定（两个 Agent 每次回复都
点名对方）⇒ 零付费模型调用。

### 4.1 两个互相 @ 的 Agent：有限往返与硬上界

```text
$ WSPS_DATA=/tmp/wsps-e2e-data-4 WSPS_PORT=8797 .venv/bin/python /tmp/e2e_backend_4.py
data dir: /tmp/wsps-e2e-data-4

# 建 甲 / 乙 / 局外人 三个 Agent，频道「接力频道」只含 甲+乙；人类发一条「开始接力」

===== 人类一条消息之后 =====
seq= 1 kind=human   author=Owner    body=开始接力
seq= 2 kind=agent   author=甲        body=@乙 接力 #1
seq= 3 kind=agent   author=乙        body=@甲 接力 #2
seq= 4 kind=agent   author=甲        body=@乙 接力 #3
seq= 5 kind=agent   author=乙        body=@甲 接力 #4
seq= 6 kind=agent   author=甲        body=@乙 接力 #5
-- budget --
{"channel_id": "ch_f0091e353227", "epoch_seq": 1, "limit": 3, "used": 3, "remaining": 0}
-- round outcomes --
base= 1 replied           budget=0 merged=0 reason=甲 按脚本点名 乙（互相接力）
base= 2 replied           budget=0 merged=0 reason=乙 按脚本点名 甲（互相接力）
base= 2 merged            budget=0 merged=1 reason=该成员已有一轮在进行中：1 次重复唤醒（#2）合并进那一轮，以最大 seq #2 为准；不排队、不重放
base= 3 replied           budget=1 merged=0 reason=甲 按脚本点名 乙（互相接力）
base= 4 replied           budget=1 merged=0 reason=乙 按脚本点名 甲（互相接力）
base= 5 replied           budget=1 merged=0 reason=甲 按脚本点名 乙（互相接力）
base= 6 budget-exhausted  budget=0 merged=0 reason=本人类世代（最近一条人类消息 seq=1）的agent→agent 预算已用尽（≤3）：本条 Agent 消息照常落库，但没有投…
```

**读法**：跳只有 3 次（`budget=1` 的三行各代表一次投递），因此 `used == 3 == limit`、
`remaining == 0`；**第 6 条消息仍然落库**（append-only、循环被节制而非关闭），但它零投递，
于是甲/乙各留一条 `budget-exhausted` 结论。人类再发一条即重置（§3.3 用例）。

同一份事实在**真实浏览器**里的呈现（`relay-channel.png`）：

```text
{ rows: [ {seq:'1', author:'Owner', text:'开始接力'},
          {seq:'2', author:'甲', tag:'Agent', avatar:'avatar av-0', text:'@乙 接力 #1', mentions:['@乙']},
          {seq:'3', author:'乙', tag:'Agent', avatar:'avatar av-1', text:'@甲 接力 #2', mentions:['@甲']},
          {seq:'4'...'6', 同上（Agent capsule + 提及高亮）} ],
  sidebar: [{text:'#接力频道', badge:null}],
  dock: ['甲甲前端ollama:qwen2.5空闲', '乙乙后端ollama:qwen2.5空闲', '局局外人审阅ollama:qwen2.5空闲'],
  navigations: 1 }
```

`navigations: 1` = 整个观察期间**零导航**（没有刷新），6 条消息由事件流送达。

### 4.2 穿透加入的 `system` 事件消息、未读口径与「正在查看即已读」

```text
# 人类在某频道（只含「甲」）发一条「@局外人 你来接一句」；SSE 客户端连在该频道上（等价于浏览器切入）

=== SSE 事件（按到达顺序，节选） ===
  message seq= 1 kind=human   author=Owner    body=@局外人 你来接一句
  member_status seq=1 {"member_id": "mem_9e693c850bb8", "status": "working"}
  typing        seq=1 {"member_id": "mem_9e693c850bb8", "active": true, "reason": "mention"}   ← 穿透唤醒
  message seq= 3 kind=system  author=局外人      body=局外人 加入了频道：它被点名时并不在此频道，回复时顺带加入（成员变化只随真实回复发生）
  message seq= 4 kind=agent   author=局外人      body=我来了，这条我接。

=== 成员关系（穿透回复后应新增一行） ===
[ owner, 甲, 乙, 局外人 ]        ← 局外人已在成员表里（它回复了，因此顺带加入）

=== 该频道未读（人类 1 + Agent 1 + system 1） ===
穿透频道: (unread=7, last_seq=9)   ← 后续还有接力往返；system 那两条未被计入
```

其中 `system` 行（seq=3）恰在回复（seq=4）**之前** —— 对应人类裁定 Q6=A（先交代成员变化，
再看见它说话）。真实浏览器 DOM 取证：

```text
{ badgeWhileViewing0: null,  badge: null,
  rows: [ {seq:'3', kind:'system', author:'局外人', tag:'事件', avatar:'avatar sm av-2',
           text:'局外人 加入了频道：它被点名时并不在此频道，回复时顺带加入（成员变化只随真实回复…'},
          {seq:'4', author:'局外人', tag:'Agent', avatar:'avatar av-2', text:'我来了，这条我接。'},
          ... ],
  navigations: 1 }
```

`avatar sm av-2` = 空心方块头像 + **该成员自己的** `--av-2` 色位（不新造色位）；
`tag:'事件'` 与 Agent 的 `tag:'Agent'` 形态一致但文案可区分；`badge: null` = 正在查看该频道时
到达的消息立即已读（含穿透那条），徽标始终为空。

截图：`/tmp/wsps-evidence-4/relay-channel.png`、`/tmp/wsps-evidence-4/penetration-system-row.png`。

### 4.3 红→绿证据（隔离副本，工作树不动）

在**隔离的基线 worktree** `/tmp/wsps-red-4`（`git worktree add /tmp/wsps-red-4 4126dd4`）上、
只把本票的**测试文件**拷过去（不带任何实现改动），跑出红：

```text
$ cd /tmp/wsps-red-4/app && .venv/bin/python -m pytest tests/test_bounded_relay.py tests/test_event_stream.py tests/test_agent_round.py tests/test_channel_read.py
20 failed, 52 passed

# 失败者（即本票新增行为的判别面，节选）
FAILED tests/test_bounded_relay.py::test_mentioning_an_outsider_wakes_it_without_joining
FAILED tests/test_bounded_relay.py::test_pierced_agent_that_replies_joins_and_leaves_a_system_event
FAILED tests/test_bounded_relay.py::test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch
FAILED tests/test_bounded_relay.py::test_repeated_wakes_during_a_round_are_merged_into_one
FAILED tests/test_bounded_relay.py::test_budget_state_is_persisted_and_survives_restart
FAILED tests/test_bounded_relay.py::test_agent_replies_count_toward_unread_and_the_system_event_does_not
FAILED tests/test_event_stream.py::test_system_join_event_is_pushed_as_a_message_and_replayable
FAILED tests/test_agent_round.py::test_second_wake_while_a_round_is_in_flight_is_merged

# 未读口径那条的红是本票改判的直接证据（基线库里 owner 自己那条也被计入）：
$ .venv/bin/python -m pytest tests/test_channel_read.py::test_unread_readings_across_endpoints_share_one_rule
E       assert 2 == 1        ← 基线（owner 自己的消息也计入）与 #4 裁定（不计入）的差
```

同一套测试在本 worktree（有实现改动）上全绿：`142 passed, 1 skipped`（§1）。
红证据**只在隔离副本取得**，本 worktree 的 `git status` 与 `git diff` 未受影响（§7）。

### 4.4 未读口径修正后的前端行为（裁定点 4：不改变「切入即已读 / 查看期间到达即已读」）

另一份临时数据目录 `/tmp/wsps-e2e-unread`（后端 8798）+ 真实 Chromium：切进「正在看的」频道后，
（1）owner 在里面自己发一条，（2）在「别的」频道 `@` 一个 Agent 让它回复。

```text
{ afterOwn: null,        ← owner 在自己正看的频道里发消息，徽标仍为空（改前口径会顶起 1）
  otherBadge: "1",      ← 别的频道里 Agent 的回复累计为未读
  watchedBadge: null,    ← 正在查看的频道保持已读
  http: { channels: [ …, {id: watched, unread:0, last:1}, {id: other, unread:1, last:2} ],
          unread_total: 1 } }
```

即：频道列表 `unread_count`、workspace `unread_total` 与前端徽标三处一致；「切入频道即已读」
「正在查看期间到达即已读」两条路径未变。原始记录：`/tmp/wsps-evidence-4/unread-browser-verification.txt`。

## 5. 提问与裁定（走 `orchestration ask`，不自行拍板）

票面要求「任何影响交付行为、方案分叉或 scope 边界的决策走 ask」。本票共 4 组，均经
`orca orchestration ask` 提问并取得回执：

| 问题 | 裁定 | 对实现的影响 |
| --- | --- | --- |
| Q1 预算耗尽的记录口径 | **A**（人类裁定） | 为每个「本应被唤醒却被压制」的成员各记一条 `budget-exhausted`（`base_seq` = 触发消息 seq、reason 可读），且状态点**不**因此变 `--error`；据此把 `status_of` 里原有的 `("failed","budget-exhausted")` 分支改为只判 `failed` |
| Q2 穿透加入的 `system` 消息是否唤醒/消耗预算 | **A**（人类裁定） | 不唤醒、不消耗（`notify` 对 `kind == "system"` 早退 return 0） |
| Q3 预算是否经只读端点暴露 | **A**（coordinator 代答） | 新增 `GET /api/channels/{id}/budget` → `{epoch_seq, limit, used, remaining}`；**仍不做任何页面** |
| Q4 合并怎么记 | **A**（coordinator 代答，票面有直接依据） | 窗口内只累积到内存，轮次**收口时**合成**恰好一条** `merged` 行，`base_seq` = 窗口内**最大** seq，reason 写明被合并次数 |
| Q5 全部目标 in-flight 时算不算一次跳 | **A**（代答，票面原句照搬） | 不算：零投递 ⇒ 不消耗预算（`_spend_jump` 的 `any(... not in in_flight)`） |
| Q6 system 与回复的先后 | **A**（**人类裁定**） | `system` 先（seq n），回复随后（seq n+1）；`test_pierced_agent_that_replies_...` 断言 `system.seq == reply.seq - 1` |
| 未读口径（第 4 组） | **B**（人类裁定） | 见 §6 |

另有两条 coordinator 在 Q3 答复里明确追加的要求，均已落实：预算 `used/remaining` 必须是**落库**的
持久事实（`channel_budget` 表）；预算耗尽后状态点仍是 `online`（已加断言）。

## 6. 已交付 #2 口径修正（人类明确授权）

第 4 组问题的裁定为 **B**：「owner 作为作者的人类消息**不计入** owner 自己的未读。」

- **改前（#2 交付口径）**：未读 = 已读位置之后 `kind ∈ {human, agent}` 的顶层消息条数
  （来源 #1 §2）。因此 owner 自己刚发的消息也会把自己的徽标顶起来。
- **改后（#4 裁定）**：未读 = 已读位置之后、`kind ∈ {human, agent}`、**且作者不是本人**的顶层
  消息条数。`system` 仍不计入。
- **单一口径落点**：只在 `db.py` 的 `CHANNEL_SELECT_SQL` 里写一次（新增
  `AND m2.author_member_id != :member_id`）。`unread_count`（频道列表 / 单条）与
  `unread_total`（workspace 快照，`service.bootstrap` 对前者求和）因此共享同一条规则 ——
  没有第二份口径。新增用例 `test_unread_readings_across_endpoints_share_one_rule` 就是这条约束的判别面。
- **前端行为**：徽标只渲染服务端返回的 `unread_count`，不自己算，因此无需改逻辑；
  「切入频道即已读」「正在查看期间到达即已读」两条路径未动（§4.2 的 `badge: null` 实测覆盖）。

**受影响的 #2 用例清单与逐条处置**（`app/tests/test_channel_read.py`）：

| 用例 | 改动前 | 处置与理由 |
| --- | --- | --- |
| `test_other_channels_accumulate_unread` | 用 owner 的 human 消息计数（`2`/`1`） | 改用 **Agent 回复**（非 owner 作者）计数（现为 `1`/`1`）；新口径下 owner 自己那条恒为 0，用 human 消息做计数断言会变成**恒真**、失去判别力 |
| `test_mark_read_clears_only_that_channel` | 同上 | 同上（改用 Agent 回复，断言 `2` 不变） |
| `test_read_position_is_monotonic_and_never_rewinds` | `send` 三条 human、断言 `unread == 3` | 改为三个 Agent 各回一条、断言 `unread == 3`；回退断言不变 |
| `test_mark_read_is_clamped_to_current_max_seq` | 用 human 消息验「未来标读被裁剪」 | 首条改为 Agent 回复（`last_seq` 取自响应而非硬编码），第二条同样改用 Agent 回复 |
| `test_history_read_before_the_read_position_is_never_unread_again` | human 三条 + `unread == 1` | 改用两条 Agent 回复 + `unread == 1`；「历史仍完整可读」断言改为不依赖 seq 起点 |
| `test_read_position_is_persisted_outside_the_process` | 跨进程用 human 计数 | 改用 Agent 回复取证（此处同时修掉了一个把「第二个进程」的推进写成不可达分支的问题） |
| `test_unread_survives_browser_and_backend_restart` | human 计数 | 改用 Agent 回复；重启前后 `unread == 1` |
| `test_unread_total_sums_channels_in_workspace_snapshot` | human 计数 `2` | 改用 Agent 回复计数 `2`（两个频道各一条非本人消息） |
| **新增** `test_owner_authored_messages_do_not_count_toward_own_unread` | — | 直接断言改后口径：owner 连发 3 条 `unread == 0`，随后一条 Agent 回复变 `1`（改前的实现会让它停在 3） |
| **新增** `test_unread_readings_across_endpoints_share_one_rule` | — | 三处读数（列表 / 单条 / workspace）必须一致 |

同口径调整的 #3 用例（`app/tests/test_agent_round.py`）：`test_agent_messages_count_toward_unread`
（`2` → `1`）、`test_unread_including_agent_replies_survives_restart`（`2` → `1`）、
`test_system_messages_do_not_count_toward_unread`（`2` → `1`，并把「多写一条 system 通知」这个
缺陷的判别力移交给 `test_bounded_relay.py` 的真实穿透事件用例）。本票新增的
`test_agent_replies_count_toward_unread_and_the_system_event_does_not` 与
`test_system_event_unread_semantics_survive_restart` 同样按新口径写期望。

**未越界**：未改 seq 语义、append-only、已读单调推进、空态文案或任何其它 #2 行为。

## 7. 范围与边界核对

```text
$ git diff 4126dd4...HEAD --stat
 app/README.md                   |  45 +++++--      ← 运行/契约说明（本票 ownership 内）
 app/tests/conftest.py           |   5 +          ← 只加 budget() 取数助手
 app/tests/test_agent_round.py   |  45 +++--        ← 合并语义 + 未读口径
 app/tests/test_bounded_relay.py | 724 ++++++++++   ← 本票新增
 app/tests/test_channel_read.py  | 231 ++++----     ← 未读口径（见 §6）
 app/tests/test_event_stream.py  |  55 +++          ← 新增 SSE 面覆盖
 app/worksplice/api.py           |  20 ++           ← 只读 budget 端点 + 只读守卫
 app/worksplice/db.py            | 120 ++++-        ← 预算表/帮手 + 未读口径
 app/worksplice/orchestration.py | 255 +++--        ← 唤醒面/穿透/压制/合并
 app/worksplice/service.py       |   7 +            ← budget 只读转发
 app/worksplice/studio/app.css   |  12 +            ← system 行样式（沿用既有 token）
 app/worksplice/studio/app.js    |   9 +-           ← system 行渲染（沿用既有形态）
```

- **未修改** `worksplice-design-system/`（只读参照）。
- **未重做 #3 已交付**：Agent 创建 / 模型配置 / 远端数据流告知 / SSE 管道 / 提及解析都未动；
  唯一相关改动是 `notify` 里为「穿透」而扩展唤醒面、以及 `status_of` 的 `budget-exhausted` 语义。
- **未实现 #1 Out of Scope**：无任务/线程、无 DM、无搜索、无 E2EE、无云端多租户。
- 未引入任何真实远端模型调用（验收全程脚本化替身）；`test_agent_smoke.py` 的真模型用例默认跳过。

## 8. 双轴 review findings 与处置

按 `code-review` 的两轴分别跑两个子代理（Standards / Spec）各自 report，下表逐条列 finding 与处置。

### 8.1 Standards 轴

| # | 级别 | Finding | 处置 |
| --- | --- | --- | --- |
| S1 | P1 | `db.consume_budget` 的 docstring 自称「本方法是重置规则的唯一落点」，与下一句矛盾（实际重置在 `append_message`） | **已修**：改为「开新世代并重置用量发生在人类消息落库那一刻（`append_message`），本方法只负责累加已经发生的投递」 |
| S2 | P2 | Speculative Generality：`consume_budget(..., limit=BUDGET_LIMIT)` 的 `limit` 无人使用（`_ = limit`） | **已修**：删掉 `limit` 参数，把「上界由调用方裁决」写进 docstring |
| S3 | P2 | Speculative Generality：`_commit_reply` 的 `base_seq` 死参（`_ = base_seq`） | **已修**：删掉该参数（它只是历史取证标注，轮次结果行已带 `base_seq`） |
| S4 | P2 | Speculative Generality：`_record_merged` 的返回值在调用点被丢弃 | **已修**：返回类型改为 `None`；同时删掉它未使用的 `consumed_budget` 参数（合并行的语义恒为 False，已写进 docstring） |
| S5 | P2 | `agent["__pierced"]` 是穿过成员 dict 的 stringly-typed 标记，会被一并交给 seam | **已修**：改为显式 `_WakeTarget(member=…, pierced=…)` dataclass（与仓内已有的 `_Turn`/`_Committed` 同风格）；`run_round(..., pierced=…)` 与 `_commit_reply(..., pierced=…)` 收显式参数，成员 dict 不再被污染 |
| S6 | P2 | Duplicated Code：`round_outcome` 事件的发布形状在 3 处同形 | **已修**：抽 `_publish_outcome(channel_id, seq, record)` 作为轮次结果事件的唯一发布点 |
| S7 | P2 | 术语漂移：词条是「轮次结果」，新代码在几处写「结论」 | **已修**：`db.py` / `orchestration.py` / `test_bounded_relay.py` 的新增正文措辞改为「轮次结果」（保留「有结论的轮次」这类票面原文措辞） |
| S8 | P2 | 编号漂移：「#④」与「实施票 ③」指同一票 | **已修**：新增注释统一写「实施票 ③」 |
| S9 | P2 | 过期注释：「穿透（未加入却被点名）在 #③」已被本票实现 | **已修**：改为「+ **穿透**（被点名却未加入的 Agent）」 |
| S10 | P2 | SQL concat advisory（Semgrep `sqlalchemy-execute-raw-query`）出现在三处 `"SELECT " + AGENT_COLUMNS` | **接受为报告项（有据）**：这是 **#3 已复核过的基线形态**（#3 验收 §6.2 的 T2 裁决「三处 FROM/WHERE 各自保留」）。用 `git show 4126dd4:app/worksplice/db.py` 取**未改动的基线文件**探针验证：同一规则在基线文件上也报同一批 finding（含 #2/#3 交付的 `CHANNEL_SELECT_SQL` 两处，共 5 条），且报的是 `self._conn`（非字面 SQL 参数）+ 模块级列常量、值全部参数化 —— 是**误报**，不是本票引入。本票新增的 `channel_budget` 与所有新查询均用命名/位置占位符，无拼接用户输入 |
| S11 | P2 | 测试断言 `drain() == [None, None]` 触及 `run_round` 的内部返回值 | **接受为报告项**：沿用 #3 已交付的写法（`test_agent_round.py` 同形），它断的是「没有新轮次被投递」这个外部事实；真正的行为断言（恰一条 merged 行、base_seq 为 max、模型只调一次）另有独立用例 |
| S12 | P2 | `test_bounded_relay.py` 的节标题注释被并进 assert 行尾 | **已修**：拆回独立节标题行 |
| S13 | P2 | `CONTEXT.md ## Language` 未收录本票承重词（人类世代 / 有界响应预算 / 跳） | **接受为报告项（越界）**：`CONTEXT.md` 是规格正本的语汇表，本票 ownership 是 `app/` 内的应用代码与其测试，未授权改 `CONTEXT.md`。记入遗留（§9） |

### 8.2 Spec 轴

| # | 级别 | Finding | 处置 |
| --- | --- | --- | --- |
| P1 | P2 | **证据缺口（两轴都报）**：`_spend_jump` 里 `any(... not in self._in_flight)`（「目标全部 in-flight ⇒ 零投递 ⇒ 不算跳」）**无任何测试触达**；原先名为 `test_a_message_whose_targets_are_all_in_flight_is_not_a_jump` 的用例用的是**单成员**频道，`used == 0` 由「唤醒集为空」保证，是**同义反复** | **已修**：新增 `test_all_targets_in_flight_is_not_a_jump`（两条 drain 线程：乙先进 in-flight 并停住，甲随后回复，甲的唯一目标乙正在 in-flight）⇒ 断言 `used == 0 && remaining == 3`，放行后另有真实跳使 `used >= 1`。并用**变异测试**验证判别力（把 `not any(...)` 改成 `if False` ⇒ 本用例失败） |
| P2 | P2 | 同一条「合并不额外扣款」的断言在单成员拓扑下恒真 | **已修**：改用上面这条两成员用例覆盖「零投递不扣款」；合并用例保留真正有判别力的三条断言（恰一行、`base_seq == 3` = max、模型只调一次），另加 `merged[0]["consumed_budget"] is False` |
| P3 | P2 | 测试文件头部声称「不需要 sleep」，但有三处 `time.sleep(0.01)` 轮询 | **已修**：头部措辞改为「用轮询等待可观察条件成立，而不是用固定延时猜时序」 |
| P4 | P2 | 验收文档 §3.4 自认「用例名保留在…」，夸大了该条的判别力 | **已修**：§3.4 改为指向新用例（`used == 0 && remaining == 3`），并如实说明它验证的是哪道判据 |
| P5 | P2 | 未提交的验收/PR body 文档未跟踪（与 #2/#3 同类文档已跟踪不一致） | **已修**：随本票一并提交 |

### 8.3 判别力复核（变异测试，隔离副本，工作树不动）

为避免「测试全绿但抓不住回归」，在隔离副本 `/tmp/wsps-mutate-4` 上逐个注入变异并确认对应用例**变红**：

| 变异 | 期望变红的用例 | 结果 |
| --- | --- | --- |
| `_spend_jump` 的 `not any(... in _in_flight)` 改成 `if False`（删掉「全部 in-flight」判据） | `test_all_targets_in_flight_is_not_a_jump` | ✅ 失败 `assert 1 == 0` |
| `merged` 行的 `largest = max(...)` 改成 `min(...)` | `test_repeated_wakes_during_a_round_are_merged_into_one` | ✅ 失败（`base_seq` 不再是 max） |
| `if state["used"] >= BUDGET_LIMIT:` 改成 `if False:`（永不压制） | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch` | ✅ 失败（`used > 3`） |
| 删掉人类消息落库时的 `channel_budget` 重置（改为惰性） | `test_a_new_human_message_resets_the_budget_and_the_relay_works_again` | ✅ 失败 |
| 耗尽时 `return False` 改成 `return True`（照旧投递） | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch` | ✅ 失败 |

即：上界、构造性压制、insert-time 重置、`max seq` 合并、零投递不算跳 —— 五条承重行为都有**会变红**的断言。

## 9. 取舍与遗留

1. **合并行的第二重语义**：「不重放」与「保留可读」并存 —— 被合并的消息不单独再跑一轮
   （不排队、不重放），但它仍在消费游标之后，下一次**新**唤醒会一并读到。这是人类裁定 Q4=A
   明确确认的语义，`test_merged_wakes_are_not_replayed_but_not_swallowed` 是它的判别面。
2. **`budget-exhausted` 的基数**：实现选择「为每个被压制成员各记一条」（Q1=A 的措辞），
   因此一次耗尽会写下唤醒面大小的记录条数。这条选择让「谁被压了」逐成员可追溯，
   代价是记录数比「一条消息一条记录」多。
3. **无人类发言时的预算**：`epoch_seq == 0`（还没有人类消息）时预算按满额生效。理由：预算约束的
   是「一次人类发言能引发多少 agent 往返」；验收里引擎能停住，而「未确认对话」这类未来形态不在 #1
   授权范围内（不扩 scope）。已在 `db.budget_state` 的 docstring 写明。
4. **浏览器行为的自动化断言**：与 #3 同样的已知边界 —— 「正在查看即已读」「system 行不新造色位」
   这类前端事实由真实浏览器手跑取证（§4.2 + 截图），未进 pytest（本票测试范式是 HTTP/SSE 黑盒）。
   这是 #3 已记录的同类遗留，不新增风险面。
5. **未做的**：可观测页面（#1 明确「首版不做」，本票只加只读数据端点）；任务/线程、DM、搜索等
   均属 Out of Scope。
6. **`CONTEXT.md` 未收录本票承重词**（人类世代 / 有界响应预算 / 跳）：该文件是规格正本的语汇表，
   不在本票 ownership 内，未修改（Standards 轴 S13）。若需要同步，应由规格侧另行处理。
