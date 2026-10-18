# Issue #145 验收：team 任务线程（只唤醒队长、TeamAssign 可用、五态由既有工具维护）

- 票型：impl（G-impl）
- 分支：`issue-145-impl`
- 基线：集成分支 tip `ecf6501`（含 #142/#143 team + budget）；收尾合并至最新集成 tip（含 #149 修复与 #144 快照端点）
- 落点：`app/alibot/orchestration.py`（队长轮次的结构化任务动作 actor 校验）；测试
  `app/tests/test_team_task_threads.py`
- 不动：D1–D23、既有 ADR 原文、`organization` 之外的模块形状；`TeamAssign` 的写入/预算由
  #143 的 `BudgetedTeamPipeline` 承担，本票只验证其在 task target 的读数。

## 1. 判据 → 落点

| 票面判据 | 落点 |
| --- | --- |
| task 线程 human / agent 消息只唤醒队长；task target 不穿透；非队长受派者没有任务轮次 | `WakeOrchestrator._notify_team` 的 task 分支（#142）；本票用 HTTP + `ManualScheduler` 断言受派者/外援都没有 seam 调用与 `round_outcome` |
| owner 改派 / Agent 转派只唤醒队长；actor 是队长时零唤醒；`claimed_at` 不被改写 | `notify_task_assignment` / `notify_task_reassignment` → `_notify_team_task_assignee`（#142）；本票补充 `claimed_at` 与任务行读数 |
| team 频道创建任务后 `claim_new_task()` 返回 0、停在 `todo`、无认领轮次 | `claim_new_task` 的 `channel.mode == "team"` 早退（#142）；本票断言即使候选人 `auto_claim=true` 也不认领 |
| `TeamAssign` 在 task target 写 `team_assignment.task_id` / `target_id`；不改任务行；不写任务变更 `system`；消耗该 task 预算、频道预算不变 | `_team_round_context` 传 `task_id`；`BudgetedTeamPipeline._record_assignment` / `_consume_jump`（#143）；本票经 HTTP 一轮对账 |
| 五态仍由 owner HTTP 与队长轮次的结构化工具维护；队长非受派者可 `in_review → done`；队长是受派者只能 owner 批准 | `_apply_task_action` 的成员闸门扩为「任务成员 **或** 该频道队长」（`_is_team_leader`）；自审禁令与迁移表仍在 `Database.update_task_status` |
| task target 的 `TeamAssign` 消耗该 task 预算；频道预算不变 | `TeamRoundContext.target_id` / `budget.try_consume_jump(target_id)`（#143）；本票断言两个 `budget_state` |
| `GET /targets/{id}/team-runs` 可按 task 过滤；看板五态不变 | 快照端点归 #144（本票只消费其形状）；看板仍是 `GET /api/tasks` 的任务行五态，本票逐态断言 |

## 2. 逐条命令与读数

所有 pytest 命令均在 `app/` 下执行；`-o addopts=""` 只为显示汇总行（仓库默认 `-q`）。

### AC1 task 线程只唤醒队长、不穿透、非队长受派者无轮次

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py -o addopts="" -q -k "non_leader_assignee or agent_message_mention"
```

读数：`2 passed, 8 deselected`。

覆盖：

- team 频道建任务后停在 `todo`、`claimed_at IS NULL`、调度队列为 0；
- owner 改派后只投递 1 个待跑轮次（队长），受派者 seam 调用为 0；
- 任务线程人类消息 `@` 受派者与外援：待跑轮次仍只有 1 个，两者 seam 调用为 0（task target 不穿透）；
- task target 的 `round_outcome` 只有队长（两轮）；任务线程 `message` 只有队长正文；
- 非队长 Agent 的任务消息即使 `@` 外援也只唤醒队长：外援与作者都没有 seam 调用；
- 改派后 `status=todo`、`claimed_at IS NULL`。

### AC2 改派/转派的唤醒与 `claimed_at`

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py -o addopts="" -q -k "agent_transfer or leader_transfer or non_leader_assignee"
```

读数：`3 passed, 7 deselected`。

- owner 改派：`pending_count()==1`，队长 seam 1 次，受派者 0 次，`claimed_at IS NULL`；
- Agent 转派（actor=非队长）：`notify_task_reassignment(...) == 1`，队长 seam 累计 2 次，新受派者 0 次，
  `claimed_at IS NULL`；
- actor=队长：转派成功后 `pending_count()==0`，新受派者 seam 0 次，`claimed_at IS NULL`。

### AC3 team 频道创建任务不认领

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py -o addopts="" -q -k team_channel_creation_never_claims
```

读数：`1 passed, 9 deselected`。

覆盖：候选人 `auto_claim=true` 时创建任务仍 `status=todo` / `claimed_at=NULL` / 零待跑轮次；
直接调用 `claim_new_task()` 返回 `0`，`claimed_at` 仍为 `NULL`。

### AC4 `TeamAssign` 在 task target 的运行记录与任务行不变

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py -o addopts="" -q -k team_assign_in_a_task_thread
```

读数：`1 passed, 9 deselected`。

覆盖（HTTP 一轮 + 进程内脚本模型 + 真实 `TeamPipeline`）：

- `team_assignment.task_id == task.id`、`target_id == task.target_id`、`member_id` 是成员、
  `leader_member_id` 是队长、`state=succeeded`、`jump_consumed=1`、`reply_excerpt` 是成员产出；
- `team_run` 落在 task target 且 `status=completed`，分派挂在它下面；
- 任务行 `status` / `assignee_member_id` / `claimed_at` / `updated_at` 与分派前逐字相同；
- 任务线程 `system` 消息集合与分派前逐字相同（没有任务变更 `system`）；成员产出不落 `message`；
  线程只有队长最终 1 条 agent 消息。

### AC5 五态：owner HTTP 与队长轮次工具；自审规则

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py -o addopts="" -q -k "leader_who or owner_http"
```

读数：`3 passed, 7 deselected`。

覆盖：

- owner HTTP 在 team 频道逐态推进 `todo → in_progress → in_review → done → closed`，每一步
  `GET /api/tasks?channel_id=` 的读数都是任务行的状态；`closed` 后继续推进返回 400；
- 队长不是受派者（也不在任务成员表里）时，队长轮次提交 `SetTaskStatusAction(done)`：
  状态落到 `done`、线程恰有 1 条「状态推进至 完成」、轮次 reason 无拒绝；
- 队长是受派者时，同样的结构化动作留在 `in_review`、没有「状态推进至 完成」，
  轮次 reason 含「不能批准」；随后 owner HTTP 批准成功。

### AC6 task target 预算隔离

与 AC4 同一条命令。读数：`1 passed, 9 deselected`。

- `budget_state(task_target).used == 1`；
- `budget_state(channel).used == 0`。

### AC7 运行记录快照按 task 过滤 + 看板五态

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_task_threads.py -o addopts="" -q -k "snapshot_can_be_filtered or owner_http"
```

读数：`2 passed, 8 deselected`（#144 合入集成后复跑）。

- `GET /api/targets/{task_target_id}/team-runs?task_id=<task_id>` 返回该 task target 的 run，
  `runs[0].assignments[0].task_id == task.id`（端点形状由 #144 提供：`{target_id, runs, offset, limit, has_more}`）；
- 看板五态用例同 AC5 的 `owner_http`。

## 3. 设计说明与边界

- **actor 校验的扩展点**：`_apply_task_action` 原来要求动作发起者是任务 target 成员；team 任务线程
  里队长不是受派者时通常也不在任务成员表里（创建任务只加 owner，改派只加受派者）。本票把闸门扩为
  「任务成员 **或** 该任务所属频道的队长」（`_is_team_leader`），其余不动：动作不得跨 target、
  `TransferTaskAction` 仍由 `update_task_assignee` 的 actor 校验把关、
  `in_review → done` 的自审禁令仍由 `Database.update_task_status` 执行。
- **TeamAssign 不碰任务行**：`BudgetedTeamPipeline` 只写 `team_run` / `team_assignment` /
  `target_budget`；本票的任务行与 `system` 消息断言把这条边界固定在测试里。
- **看板没有第六态**：team 模式只改唤醒路径与分派记录，不改任务行状态词；看板仍读同一份 `task` 行。
- **端点归属**：`GET /api/targets/{id}/team-runs` 由 #144（T3）实现；#145 只消费其读数，不重复实现。

## 4. 门禁

```bash
cd /tmp/alibot-wt/issue-145-impl && scripts/check.sh
```

读数：`门禁全绿`。

- pytest 全量：`791 passed, 2 skipped`（含本票 10 条；AC7 在 #144 合入后复跑）；
- `ruff check`：`All checks passed!`；
- `ruff format --check`：`187 files already formatted`；
- mypy（仓库根）：`Success: no issues found in 79 source files`；
- pyright：无错误；
- 前端 DOM 用例（Bun）：`tests 136 / pass 136 / fail 0`。

分支已 merge 集成 tip `ceb393c`（含 #144 快照端点、#149 修复与 #146 Plan）。同一合并还修正了集成 tip 上
`app/tests/test_approval_seam.py` 的一处行宽格式（`ruff format --check` 会拒绝；纯换行，无语义变化）。
