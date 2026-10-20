# 自动续跑：判据 → 落点 → 命令与读数（ADR-0027）

## 来源

用户 2026-10-20 报「接完任务后不会自己接着干」。现场（`~/Alibot/alibot.db` 只读副本 + 任务线程截图）：

```text
task_1b1ea815ce24  「研究一下北极的科考情况」 status=in_progress  claimed_at=13:04:57.970Z
线程：1 任务已创建 / 2 已认领：小红 / 3 认领后自动开工：进行中 / 4 小红回复（末段「我会继续补全…」）
round_outcome：1 条 replied（base_seq=3, reply_seq=4）
target_budget：epoch_seq=2  used=0  limit=3
13:06 起线程再无新消息——任务停在进行中。
```

## 根因（结构性，不是某个分支写错）

1. 唤醒的唯一扳机是**消息落库之后的 `notify`**（ADR-0017 的构造性前提），而 `_commit_reply` 调
   `notify` 时唤醒面**排除作者**；单 Agent 的任务线程因此**零唤醒**，也没有第二个扳机。
2. 一轮是模型的一次新实例，输入只有「游标之后的新消息」且再次排除作者——即使再唤醒，也接不上
   自己上一轮的产出。
3. 正文里的「我会继续」是对话事实，**没有执行面**。

## 判据与落点

| # | 判据（外部可观察） | 落点 | 读数 |
| --- | --- | --- | --- |
| C1 | 受派者提交 `continue_task` 后，编排落一条 `system.task.continue` 并**再唤醒同一人一轮** | `orchestration.py` `_dispatch_continuation` / `_validate_continue_request`；`agent_seam.py` `ContinueTaskAction` + `continue_task` 工具 | `tests/test_task_continue.py::test_the_assignee_can_ask_for_auto_continue_and_is_woken_again` |
| C2 | 续跑轮输入是**整个任务线程**（含自己的历史回复，按 `assistant` 角色回传） | `orchestration.py` `run_round(history=True)`；`agent_seam.py` `Msg(role="assistant")` | 同上：`calls[1]["seqs"] == [1,2,3,4,5]`、`bodies[3]` == 自己上一条回复 |
| C3 | 不申请 ⇒ 不额外跑一轮（既有行为逐字不变） | 同 C1（无动作即不投递） | `test_a_task_stays_at_one_round_without_a_continue_request` |
| C4 | 每世代上限 `TASK_WORK_ROUNDS_PER_EPOCH = 4`（首轮 + 3 轮续跑）；到顶后落 `system.task.continue_paused` 并停止 | `db.py` 常量；`orchestration.py` `_rounds_since_epoch`（读 `round_outcome`） | `test_auto_continue_pauses_at_the_generation_limit`（4 轮、暂停记录、任务仍 `in_progress`） |
| C5 | 续跑**不是跳**：`target_budget.used` 不 +1 | `_dispatch(consumed_budget=False)` | 两个续跑用例里 `budget["used"] == 0` |
| C6 | 续跑轮可用 `set_task_status` 收尾：任务离开 `in_progress` 后不再有下一轮 | 既有五态 + `_dispatch_continuation` 的 `in_progress` 前置 | `test_a_continuation_round_can_finish_the_work_instead_of_looping` |
| C7 | team 任务线程不提供续跑（工具不挂、提示不出现） | `TaskActionContext.allow_continue`（team = False） | `test_the_continue_action_is_not_offered_in_team_task_threads` |
| C8 | 两份语言目录同键集，新增文案不裸中文/不越界进前端表 | `i18n.py` | `tests/test_backend_copy.py`、`app/tests/i18n_ui.test.cjs` |

## 命令与读数

```sh
# 回归（秒级；4 个用例在「禁掉投递」的变异下会红，证明它咬住这个 bug）
cd app && ../.venv/bin/python -m pytest tests/test_task_continue.py -q
# 5 passed

# 全量门禁（CI 同一入口）
scripts/check.sh
# 门禁全绿
# pytest: 956 passed, 10 skipped
# ruff check: All checks passed!
# ruff format --check: 243 files already formatted
# mypy: Success: no issues found in 102 source files
# node --test（glob）: 201 pass / 0 fail
```

## 真机读数（真实 DeepSeek，临时数据目录，2026-10-20）

```text
数据目录 /tmp/alibot-continue-dbg2（一次性），agent=deepseek:deepseek-chat，auto_claim=on
任务：「分三步写一份北极科考简报…一次只做一步」

线程（seq · kind · body 摘要）
  1 system 任务已创建：…
  2 system 已认领：小红 接手了这张任务
  3 system 认领后自动开工：状态推进至 进行中
  4 agent  第一步已完成，后续步骤已申请自动续跑。
  5 system 自动续跑：受派者申请继续推进这张任务
  6 agent  已提交续跑申请，本轮结束后续跑第三步：合作机制。
  7 system 自动续跑：受派者申请继续推进这张任务
  8 agent  三步内容已全部完成，简报提交审阅；…
  9 system 状态推进至 待审

round_outcome（base_seq / reply_seq / status / reason 摘要）
  3 / 4 / replied / 本轮由**认领**唤醒…
  5 / 6 / replied / 本轮由**自动续跑**唤醒…
  7 / 8 / replied / 本轮由**自动续跑**唤醒…

task.status = in_review（第 3 轮用 set_task_status 收尾；之后不再有投递）
target_budget = {epoch_seq: 2, used: 0, limit: 3}   ← 续跑不计跳
```

**反面对照**：同一模型的另一轮（`/tmp/alibot-continue-smoke`）在末尾向用户提问「报告希望偏重中国
视角还是国际全局？」并且**没有**调用 `continue_task`——任务停在进行中，没有多余轮次。这正是
ADR-0027 要的分界：**在等人**不该被自动续跑；`continue_task` 只在「可以自己往下干」时出现。

## 已知边界与代价

- 依赖模型调用工具：`_system_prompt` 用一句显式指令（「必须调用 continue_task 工具…只在正文里
  承诺不会触发任何后续轮次」）+ 工具描述来压住「只在正文里承诺」的旧行为；真机读数见上，
  但这是模型遵循度而不是构造性保证。反例（模型只写正文）仍会停住——被否的「编排无条件续跑」
  会把「在等人的任务」也继续烧，且逼模型用改状态来表达「我在等你」（ADR-0027 §一）。
- 上限数的是「该成员在该世代的轮数」，不是「续跑次数」：同一世代里若还有被提及 / 人类消息带来的
  轮次，实际可续跑次数会少于 3。这是有意取窄（一个计数器、一条可解释的规则）。
- team 任务线程不提供续跑（只唤醒队长的裁决不因此开口子）。
- 续跑轮的输入是整条线程：token 成本随线程长度增长，上限 3 轮是成本闸门。
