# Issue #189 验收 —— 人类消息一律必答（DM / channel / team / task 四个面）

> 票面：GitHub [#189](https://github.com/whutlichao/alibot/issues/189)（impl，G-impl 门禁）。
> 裁定：产品负责人在 issue 正文里定夺「人类消息一律必答」；本票把它落成**第四类确定信号**。
> 词条正本：`CONTEXT.md` 的**回应判断** / **唤醒**；ADR-0025 §三 只补一句**事实修正**（不新增 ADR）。

| 项 | 值 |
| --- | --- |
| schema | **不动**（`SCHEMA_VERSION` 未变，零 DDL、零迁移步骤） |
| HTTP 端点 | **不增不删**（DM / 频道 / 任务线程的消息端点逐字不变） |
| `round_outcome` 状态词表 | **不动**（`replied` / `ignored` / `failed` / `merged` / `budget-exhausted` / `silent`） |
| 消息 kind | **不动**（`human` / `agent` / `system`） |
| 唤醒面 / 穿透 / 预算 / 未读口径 | **不动** |
| 后端落点 | `app/alibot/orchestration.py`（投递纪律 `ReplySignal` + 轮次收口文案）、`app/alibot/agent_seam.py`（`ReplySignal` / `_system_prompt` 一对一口径）、`app/alibot/i18n.py`（四条新词条 ×2 语言） |
| 测试落点 | `app/tests/test_mandatory_reply.py`（新增 9 条）、`app/tests/conftest.py`（`send_agent_message` 辅助）、`app/tests/scripted.py`（替身记 `signal`）、`app/tests/test_approvals_http.py`（假 seam 跟随契约）、以及按新纪律更新的既有用例（见 §5） |
| 文档落点 | `CONTEXT.md`（**回应判断** / **唤醒** / **沉默** 三个词条）、`app/README.md`（唤醒章节）、`docs/adr/0025-task-creation-wakes-the-team-leader.md`（§三 一句事实修正）、本文件 |

---

## 0. 交付概览

```text
notify(target_id, from_seq, reason)                 # 消息落库之后唯一的投递入口
  ├─ 读触发消息：message.kind == "human" ?           # ← 判据是**消息 kind**，不是作者成员 kind
  │    base_signal = ReplySignal.HUMAN_MESSAGE       #    （owner 的成员 kind 是 "owner"，不是 "human"）
  │   else base_signal = ReplySignal.NONE
  ├─ team 频道 ⇒ _notify_team(...)：同一套 base_signal；队长是唯一目标
  └─ 逐成员：member_signal = MENTION if 被点名 else base_signal
        submit(run_round, ..., signal=member_signal)

run_round(..., signal)
  ├─ definite_signal = mentioned or signal.mandatory          # 四条同级、同一处落点
  ├─ copy_prefix = {CLAIM: claim, TEAM_TASK_CHANGE: team_change, HUMAN_MESSAGE: human_message}
  │    ⇒ 收口文案与后缀**按来源**选（提及那一侧沿用 reason.mention_*）
  ├─ definite_signal 下的 ignored / silent / 空正文 ⇒ RoundOutcome(status="failed", 来源化 reason)
  └─ _run_seam(..., signal=signal, direct_message=(target.kind == "dm"))

_system_prompt(agent, mentioned, task_context, signal, direct_message, ...)
  ├─ leader=False 且 direct_message=False ⇒ 频道口径（#57 rubric **逐字不变**）
  ├─ direct_message=True                   ⇒ 一对一口径（不提「多成员频道」、不给「未被点名可沉默」）
  ├─ signal=HUMAN_MESSAGE                  ⇒ 独立一段「本轮由人类消息唤醒：人类直接对你说话……你必须回应。」
  ├─ signal=TEAM_TASK_CHANGE               ⇒ 既有「本轮由 team 任务变化唤醒，你必须回应。」
  └─ signal=CLAIM（任务线程、非队长）        ⇒ 既有「本轮由认领唤醒，你必须回应。」
```

**纪律载体**：`ReplySignal`（`agent_seam.py` 里的 `StrEnum`）——`none / mention / claim / team-task-change /
human-message`。它同时回答「是否必答」（`.mandatory`）与「可读文案用哪一组」（`_SIGNAL_COPY_PREFIX`），
**不读 `reason` 文本**（ADR-0016 五：重复唤醒会被合并，合并后的 reason 取决于先到者）。重复唤醒合并时
按固定优先级取来源（`merged_with`），于是诊断也不随到达顺序漂。

---

## 1. 判据 1：人类消息成为第四类确定信号（四个面都可观察）

**落点**：`orchestration.notify` / `_notify_team` / `_notify_task_assignee` 的投递参数。
**判据**：`message.kind == "human"` 时，每个被唤醒的 Agent 那一轮 `signal = HUMAN_MESSAGE`（被点名则 `MENTION`）。

| 面 | 用例 | 读数 |
| --- | --- | --- |
| DM | `test_a_dm_owner_message_is_a_mandatory_round` | `calls_for(peer)[-1]["mandatory_reply"] is True`；`round_outcome.status == "replied"` |
| autonomous channel | `test_a_human_message_makes_every_member_of_an_autonomous_channel_mandatory` | 两个成员的**第一轮**都是 `mandatory_reply is True` |
| team channel | `test_a_team_channel_human_message_is_a_mandatory_leader_round_with_human_wording` | 队长 `mandatory_reply is True`；成员 `calls_for(member) == []` |
| task target | `test_an_owner_message_in_a_task_thread_makes_the_assignee_mandatory` | 改派轮 `False`（既有纪律），随后的人类消息轮 `True` |

**命令**：`cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py -q`
**读数**：`9 passed in 0.46s`

---

## 2. 判据 2：信号来源可区分（文案不得互相挪用）

**判据**：人类消息那一轮的提示词与轮次结果文案写「人类消息」，且**不含**「任务变化」「认领」「被个人提及」。
**落点**：`i18n.py` 的 `reason.human_message_{ignored,silent,no_body,round}`（zh-CN + en 各四条）、
`agent_seam._system_prompt` 的 `HUMAN_MESSAGE` 段。

| 用例 | 读数 |
| --- | --- |
| `test_a_silent_dm_round_is_a_failure_that_names_the_human_message` | `status == "failed"`；reason 同时含「人类消息」「确定信号」与**原始 silent 理由**（`声明本轮沉默`）；不含「任务变化 / 认领 / 被个人提及」 |
| `test_a_team_channel_human_message_is_a_mandatory_leader_round_with_human_wording` | 队长 prompt 含「人类消息」且**不含**「任务变化」；`reason` 同口径 |
| `test_an_empty_body_on_a_human_message_round_is_a_failure` | `replied` 但零正文 ⇒ `failed`，reason 含「人类消息」与「正文」 |

**命令**：`cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py -q -k "silent or wording or empty_body"`
**读数**：`3 passed`

---

## 3. 判据 3：沉默在必答轮按失败收口（并带出原始理由）

**判据**：必答轮的 `declare_silence` / 空正文 ⇒ `round_outcome.status == "failed"`；非必答轮（人类消息之外的作者）
保持既有纪律（`ignored` / `silent` 仍是它们自己）。
**落点**：`orchestration.run_round` 的 `definite_signal` 分支。

| 用例 | 读数 |
| --- | --- |
| `test_a_silent_dm_round_is_a_failure_that_names_the_human_message` | `failed` + 原始理由 |
| `test_an_empty_body_on_a_human_message_round_is_a_failure` | `failed` |
| `test_an_agent_authored_message_still_leaves_the_round_to_self_judgement` | 同一成员两轮：`[True, False]` / `["failed", "ignored"]` —— 人类消息轮必答，随后的 Agent 消息轮仍可自判 |

---

## 4. 判据 4：DM 提示词改一对一，channel 的 #57 rubric 保持原样

| 用例 | 读数 |
| --- | --- |
| `test_the_dm_prompt_speaks_one_to_one_and_requires_a_reply` | DM prompt **不含**「多成员频道」/「没有被点名时」/「保持沉默」；**含**「人类消息」与「必须回应」 |
| `test_the_channel_prompt_keeps_the_silence_rubric_and_appends_the_human_message_rule` | 含「点名」的块**恰好 1 段**（#57 rubric 未被拆）；「人类消息」块恰好 1 段且与 rubric 不同段 |
| `test_agent_round.py::test_the_silence_rubric_*`（既有，未改） | 仍绿：rubric 仍恰好一段、仍含「不产出正文」与括注/旁白/说明/道歉四种反面 |

**命令**：`cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py tests/test_agent_round.py -q -k "prompt or rubric"`
**读数**：见 §7 全量读数（两个文件全绿）。

---

## 5. 红 → 绿证据

### 5.1 红（实现前，隔离的新用例）

**命令**

```sh
cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py
```

**读数**（实现前）

```text
FAILED tests/test_mandatory_reply.py::test_a_dm_owner_message_is_a_mandatory_round
FAILED tests/test_mandatory_reply.py::test_a_silent_dm_round_is_a_failure_that_names_the_human_message
FAILED tests/test_mandatory_reply.py::test_a_human_message_makes_every_member_of_an_autonomous_channel_mandatory
FAILED tests/test_mandatory_reply.py::test_an_empty_body_on_a_human_message_round_is_a_failure
FAILED tests/test_mandatory_reply.py::test_a_team_channel_human_message_is_a_mandatory_leader_round_with_human_wording
FAILED tests/test_mandatory_reply.py::test_an_owner_message_in_a_task_thread_makes_the_assignee_mandatory
FAILED tests/test_mandatory_reply.py::test_the_dm_prompt_speaks_one_to_one_and_requires_a_reply
FAILED tests/test_mandatory_reply.py::test_the_channel_prompt_keeps_the_silence_rubric_and_appends_the_human_message_rule
FAILED tests/test_mandatory_reply.py::test_an_agent_authored_message_still_leaves_the_round_to_self_judgement
9 failed in 0.52s
```

红读数的关键两行（失败原因就是本票要改的事实）：

```text
assert [False, False] == [True, False]        # 乙的人类消息轮当时不是必答
AssertionError: 甲 也应必答
```

### 5.2 绿（实现后）

```sh
cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py
# 9 passed in 0.46s
```

### 5.3 既有用例按新纪律更新（逐条列出改了哪些断言与原因）

| 文件 / 用例 | 旧断言（钉住「人类消息可合法沉默」） | 新断言 | 原因 |
| --- | --- | --- | --- |
| `test_agent_round.py::test_clearly_irrelevant_message_is_ignored_not_failed` | 人类消息 → `ignored` | 由**另一个 Agent 的消息**唤醒 → `ignored`；消息流断言改为 `["agent"]` | 人类消息自本票起必答，`ignored` 只能作为**非必答轮**的结论被观察 |
| `test_agent_round.py::test_ignored_round_advances_the_cursor_so_it_is_not_reread` | 两条人类消息 → `ignored` 推进游标 | 两条 **Agent 消息** → 断言**不变**（每轮只读新到的那条） | 同上；游标语义本身未变 |
| `test_silent_outcome.py::test_each_round_outcome_kind_is_persisted_with_its_message_count` | 人类消息下四种结论各自落库 | 新增 `trigger_kind` 参数：`replied` / `failed` 走人类消息，`ignored` / `silent` 走 Agent 消息；记录按成员过滤 | 四种状态仍各自落库，但「可沉默」的两种只能在非必答轮取证 |
| `test_silent_outcome.py::test_silent_and_ignored_are_two_distinguishable_rows_in_one_round` | 同一人类消息下 `silent` 与 `ignored` 并存 | 同一条 **Agent 消息**下两者并存 | 同上 |
| `test_silent_outcome.py::test_the_database_check_accepts_silent_and_refuses_an_unknown_word` | 人类消息触发的 `silent` 行 | Agent 消息触发的 `silent` 行 | 库级 CHECK 判据不变，仅触发者改变 |
| `test_round_outcome_visibility.py::_mixed_channel`（辅助）及依赖它的 3 条 | 人类消息下「同一条消息多条 `ignored`」 | 由**发言 Agent** 的消息触发，成员集合不变 | 摘要行要处理的真实场景是「非必答轮同 base_seq 折叠」，人类消息轮已是必答 |
| `test_round_outcome_visibility.py::test_ignored_is_not_recorded_or_reported_as_failure` | 人类消息 → `ignored` + 状态点 online | Agent 消息 → `ignored` + online | 同上 |
| `test_bounded_relay.py::test_plain_message_wakes_every_channel_agent_member_and_excludes_the_author` | 人类消息 → 全员 `ignored` | 人类消息 → 全员 **`failed`**（唤醒面断言**一字未改**） | 本用例守的是**唤醒面**（谁被叫醒、作者排除），结论类型按新纪律更新 |
| `test_bounded_relay.py::test_ignored_and_failed_are_distinguishable_in_the_same_channel` | 人类消息下 `ignored` + `failed` 并存 | Agent 消息下同样并存（加一名发言 Agent） | 「它不想说」只在非必答轮存在；「它坏了」不受影响 |
| `test_bounded_relay.py::test_ignore_leaves_no_fake_acknowledgement` | 人类消息 → 不产生伪造确认 | Agent 消息 → 不产生伪造确认 | 语义（没有假确认）不变，触发者改变 |
| `test_event_stream.py::test_ignored_round_returns_to_online` | 人类消息 → `ignored` → 状态点 online | Agent 消息 → `ignored` → online | 同上 |
| `test_auto_claim.py::test_channel_and_dm_rounds_carry_no_task_context` | 频道轮 `mandatory_reply is False` | `mandatory_reply is True`（并补注释：与任务上下文互不推出） | 本用例守的是「无任务上下文」，那条 `False` 是旧纪律的残留 |
| `test_approvals_http.py::ParkingSeam` | 形参 `mandatory_reply: bool` | 形参 `signal` + `direct_message` | 假 seam 跟随 `AgentSeam` 契约（替身与真实现同一签名） |

**没有**为变绿削弱的断言：唤醒面集合、穿透、预算扣减、未读计数、消息 kind、状态词表、库级 CHECK、
`declare_silence` 结构化通道的既有语义全部保留（上表只改了「由谁触发」与「那一轮的结论类型」）。

---

## 6. 不动的边界（逐条核对）

| 边界 | 证据 |
| --- | --- |
| 唤醒面 / `_notify_team` 成员集合与穿透 | `test_bounded_relay.py`（全员 + 作者排除）、`test_team_mode.py::test_team_channel_wakes_only_the_leader` / `..._piercing_is_a_one_off_outside_the_pipeline`、`test_dm.py` 全绿 |
| 回应预算与 `target_budget` 记账 | `test_bounded_relay.py` / `test_team_budget.py` / `test_channel_budget_*` 全绿；人类消息本身不扣跳（`jump_delivered` 判据未改） |
| 未读口径 | `test_channel_read.py` / `test_dm.py::test_dm_mention_to_third_agent_is_delivery_only_and_counts_as_unread` 全绿 |
| 消息 kind 与 `round_outcome` 词表 | `test_docs_vocabulary_guard.py`、`test_silent_outcome.py` 全绿；`SCHEMA_VERSION` 未改 |
| 非人类作者纪律不变 | `test_an_agent_authored_message_still_leaves_the_round_to_self_judgement`（`[True, False]`）+ 上述 agent 触发用例 |
| team 队长自己的回复零唤醒 | `test_team_mode.py::test_team_channel_human_message_wakes_only_the_leader`（`notify(...) == 0`）全绿 |
| `declare_silence` 结构化通道保留 | `test_silence_tool_channel.py` 全绿（`silent` 仍是独立状态，只在确定信号下收口成 `failed`） |

---

## 7. 门禁读数

**命令**：`scripts/check.sh`（CI 跑同一个脚本）

| 步骤 | 读数 |
| --- | --- |
| pytest（全量） | `943 passed, 10 skipped in 501.32s (0:08:21)` |
| ruff check | `All checks passed!` |
| ruff format --check | `235 files already formatted` |
| mypy（仓库根） | `Success: no issues found in 101 source files` |
| pyright | `0 errors, 0 warnings, 0 informations` |
| node --test（Studio DOM，glob） | `tests 191 / pass 191 / fail 0` |
| 总耗时 | `real 8m23.920s`（首轮含 format 修正前）；修 format 后 `scripts/check.sh --fast` 全绿 |

---

## 8. 一处实现细节（本可以踩坑的地方）

`notify` 最初按**作者成员 kind** 判人类（`message["author"]["kind"] == "human"`），红测试立刻打回：owner 的
成员 kind 其实是 **`owner`**。判据改成票面原句的**消息 kind**（`message.kind == "human"`）后转绿。这条差异
留在代码注释里，避免下一次再从作者身份去推「这是不是人类说的」。

## 9. pi-lens 伪影核查（与本票代码无关，留档避免下一个人重新追一遍）

pi-lens 在本轮反复报 4 条 blocking（`L233 / L1992 / L2027 / L2042`，`unused-ignore`），逐条核查如下：

| 读数 | 结果 |
| --- | --- |
| 被点名的文件长度 | `test_mandatory_reply.py` 259 行 / `scripted.py` 299 行 / `test_approvals_http.py` 368 行 |
| 这些文件里 `type: ignore` 的数量 | 均为 **0** |
| `grep -c 'type: ignore' app/alibot/agent_seam.py` | `1`（只是文档串里提到这个词；**代码中 0 处**） |
| 仓库配置 pyright（`node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests`） | `0 errors, 0 warnings, 0 informations` |
| `ruff check app` / `ruff format --check app` | `All checks passed!` / `235 files already formatted` |
| `lens_diagnostics(source=lsp, mode=full, scope=paths)` 扫四个改动文件 | 仅 15 条**既存** ast-grep 风格提示（`...` 协议体、`int()` 解析），无任何 `type: ignore` 条目 |
| 工具自身汇总 | `18 files diagnosed this session / 8 blocking`，其中多条标注 `line past EOF` 与 `stale — re-run to confirm`；`current turn delta: no issues` |

**结论**：`L1992/2027/2042` 大于被点名文件的长度（不可能存在），且同一组行号被同时贴到多个长度不同的文件上；
这是 pi-lens 会话缓存把**同一份**陈旧诊断复制到本轮各文件的产物，不是仓库门禁判据（`docs/agents/review-rules.md`：
工具已强制的不重复报，跑不过就是红——而门禁是绿的）。

**真实来源已顺手消除**：`agent_seam.py` 原有三处 `# type: ignore[arg-type]`（`FunctionTool` 的 bundled typing
只声明返回 `ToolChunk`）。本票把这三处收口成一个 `_value_returning_tool()`（形参 `Callable[..., Any]`、
返回处局部注解 `ToolBase`），于是既不需要 `# type: ignore`，也不需要 `cast`——`ruff` / `mypy`
(`warn_unused_ignores`, `warn_redundant_casts` 均开) / `pyright` 三种检查器同时干净。
