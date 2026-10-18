# Issue #143 验收：TeamAssign 预算（原子扣减、耗尽错误 ToolResult）

- 票型：impl（G-impl）
- 分支：`issue-143-impl`
- 基线：集成分支 tip `911e8d8`（含 #142 team 模式与 `_authorize_assignments` 接缝）
- 落点：`app/alibot/db.py`（`try_consume_jump` + `team_assignment` 读写）、`app/alibot/team.py`
  （`TeamRoundContext` 注入 `budget` / `team_run_id`；`BudgetedTeamPipeline` 的裁决、结果合并与收口）、
  `app/alibot/orchestration.py`（把当轮 `team_run` 与 `Database` 交给上下文）、
  `app/alibot/agent_seam.py`（把二者传进 pipeline）、`app/alibot/i18n.py`（三条错误词条）
- 测试：`app/tests/test_team_budget.py`（进程内并发 + 真实 `TeamPipeline` + 脚本模型 + 一条 HTTP 装配用例）

## 1. 判据 → 落点

| 票面判据 | 落点 |
| --- | --- |
| 覆写 `_run_assignments()` 在成员运行前逐条裁决；每次分派对**对应 target** 的 `target_budget.used` 恰好 +1 | `team.BudgetedTeamPipeline._run_assignments` 先调 `_authorize_assignments`，只把获授权的 calls 交给基类 `run_group`；`_authorize_assignments` 按调用顺序逐条调 `_consume_jump()` → `Database.try_consume_jump(target_id)` |
| 检查与扣减原子 | `Database.try_consume_jump`：`BEGIN IMMEDIATE` 内解析 `applied_limit`（缺省 `BUDGET_LIMIT`）+ 条件 `UPDATE target_budget SET used = used + 1 WHERE target_id = ? AND used < ?`；`rowcount == 0` ⇒ 耗尽。没有预算行时同事务惰性建行（`used=0` + 上界快照），不把「缺行」误判成耗尽 |
| task target 用该 task 的预算，不推进频道预算 | pipeline 的 `target_id` 就是 `TeamRoundContext.target_id`（channel 或 task）；`try_consume_jump` 只写该 target 的 `target_budget` 行。频道与任务互不影响（ADR-0004 per-target） |
| 耗尽时第 N+1 次返回 `ToolResultBlock.state=ERROR`，成员未运行 | `_budget_exhausted_result` 生成 `id=call.id` / `name="TeamAssign"` / `output=[TextBlock("预算耗尽：本次分派未运行成员")]` / `state=ERROR`；被拒 call **不进入**基类 `run_group` |
| `team_assignment.state` / `jump_consumed` | `Database.create_team_assignment` / `finish_team_assignment`：获授权先写 `started` + `jump_consumed=1`，收口改 `succeeded` / `failed` / `interrupted`；耗尽直接写 `budget-exhausted` + `jump_consumed=0`。`jump_consumed` 没有改回 0 的路径 |
| 结果按调用顺序合并 | `_run_assignments` 把被拒的 ERROR 结果与基类结果按原始 `tool_calls` 顺序 `sorted` 后写回 `results`，队长看到的顺序稳定 |
| 并发不越过上界 | `try_consume_jump` 的单事务条件 UPDATE（R1 §3 的 100 并发结论）；pipeline 逐条调用同一原语 |
| 队长最终回复不扣跳 | 只有 `_authorize_assignments` 扣跳；队长最终回复不经过该路径。HTTP 用例断言一轮一次分派后 `used == 1` |
| 成员失败/中断不退款 | `_finish_assignments` 只改 `state`，不动 `jump_consumed`；`failed` / `interrupted` 行均为 1 |
| HITL 恢复不重复扣跳 | pipeline 内的 `_assignments[call.id]` 复用首次裁决：恢复路径再送同一 call 时不再扣减、不重复写行 |

## 2. 逐条命令与读数

所有 pytest 命令均在 `app/` 下执行；`-o addopts=""` 只为显示汇总行（仓库默认 `-q`）。

### AC1 每次分派对应 target 的 `used` 恰好 +1（含第 N+1 次 ERROR）

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_budget.py -o addopts="" -q -k each_assignment
```

读数：`1 passed, 8 deselected in 0.72s`。

覆盖：上界 3、队长一次分派 4 条同一成员调用 ⇒ 成员模型恰好运行 3 次；`budget_state.used == 3`；
`team_assignment` 4 行中前 3 行 `state=succeeded, jump_consumed=1`，第 4 行
`state=budget-exhausted, jump_consumed=0`；`sum(jump_consumed) == used`；队长第二次模型调用收到的
4 条工具结果按 `call-1..call-4` 顺序排列，第 4 条正文含「预算耗尽」；`outcome.consumed_budget is False`
（队长最终回复不计跳）。

另有一条边界读数（同一文件）：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_budget.py -o addopts="" -q -k pre_epoch
```

读数：`1 passed, 8 deselected`。保存频道上界 5、但还没有人类世代（`target_budget` 无行）时，
第 4 次 `try_consume_jump` 就返回耗尽——当前上界仍是内置默认 3，5 只是 `pending_limit`。

### AC2 task target 用自己的预算、不推进频道预算

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_budget.py -o addopts="" -q -k task_target
```

读数：`1 passed, 8 deselected in 0.58s`。

覆盖：同一队长的两轮（channel 一轮、task 一轮）各分派 1 次 ⇒ `channel.used == 1` 且
`task target.used == 1`；task 的分派行 `task_id == task["id"]`、`state=succeeded`、`jump_consumed=1`。

### AC3 耗尽错误 ToolResult 与 `budget-exhausted`

与 AC1 同一用例（第 4 条调用）。额外读数（HTTP 装配用例）：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_budget.py -o addopts="" -q -k http_round
```

读数：`1 passed, 8 deselected in 0.58s`。

覆盖：编排在开轮时创建 `team_run` 并把它与 `Database` 一起交给 `TeamRoundContext`；HTTP 一轮后
`used == 1`、`team_assignment.team_run_id == team_run.id`、`team_run.status == completed`，
频道里只有队长最终 1 条 Agent 消息（成员输出不落消息）。

### AC4 并发不越界（进程内 + 多连接）

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_budget.py -o addopts="" -q -k "epoch_limit or across_connections or concurrent_team_rounds"
```

读数：`3 passed, 6 deselected in 0.68s`。

覆盖：

- 单连接 100 次并发 `try_consume_jump`：恰好 3 次成功、`used == 3`、每次返回的 `used <= 3`；
- 8 个独立 `Database` 连接（模拟多进程）100 次并发：仍恰好 3 次成功、`used == 3`；
- 4 条并发的队长轮各分派 1 次：成员模型总共运行 3 次，4 条记录状态为 3×`succeeded` + 1×`budget-exhausted`，
  `sum(jump_consumed) == 3`。

### AC5 失败/中断不退款、`jump_consumed` 与 `used` 一致

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_budget.py -o addopts="" -q -k refund
```

读数：`2 passed, 7 deselected in 0.61s`。

覆盖：

- 成员模型抛错：`used == 1`、分派行 `state=failed`、`jump_consumed=1`；
- 成员流被中断（`CancelledError` ⇒ 框架 `INTERRUPTED` 结果）：`used == 1`、行 `state=interrupted`、
  `jump_consumed=1`，队长继续给出最终回复；
- 两条用例都断言 `sum(jump_consumed) == used`。

### 全量测试

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_budget.py -o addopts="" -q
```

读数：`9 passed in 1.11s`（8 条预算用例 + 1 条 HTTP 装配用例）。

## 3. 设计说明与边界

- **拦截点**：仍按 ADR-0019 / R1 覆写实验性 `_run_assignments`；T1 留的 `_authorize_assignments`
  继续作为**唯一预算裁决点**，T2 只把默认放行换成原子扣减。结果合并与运行记录收口在
  `_run_assignments` 内完成，因为基类在生成器结束时才把结果写回 `results`。
- **没有预算正本就不静默放行**：`budget is None` 且出现 `TeamAssign` 时整轮失败
  （`RuntimeError`），避免「不可审计的越界」。既有 #142 用例（无 `TeamAssign`）不受影响。
- **HITL**：同一 `call.id` 的重复裁决复用首次结果；第一次流在成员挂起时把未完成的授权分派收口为
  `interrupted`，恢复后同一行更新为 `succeeded`，`jump_consumed` 仍为 1。
- **运行记录写入与预算扣减不在同一事务**：`try_consume_jump` 与 `create_team_assignment` 是两次
  短事务（spec §3.4 的 `_record_assignment` 形状）。进程在两者之间崩溃会少一条运行记录，但不会
  越过预算上界（`used` 已 +1；缺口是审计面，不是额度面）。这是 R1 §7 已记录的同一条残余。
- **不做退款**：`finish_team_assignment` 没有写 `jump_consumed` 的路径；DB CHECK 继续兜底
  `jump_consumed IN (0,1)`。
- **T3（#144）**：`list_team_assignments(target_id)` 已提供读面；展示/SSE 与快照端点仍归 T3。
  T2 只保证 `state` / `jump_consumed` / `task_id` / `tool_call_id` / `member_id` 写入正确。

## 4. 门禁

```bash
cd /tmp/alibot-wt/issue-143-impl && scripts/check.sh
```

读数：`门禁全绿`。

- pytest 全量：`738 passed, 2 skipped`（含本票 9 条）；
- `ruff check`：`All checks passed!`；
- `ruff format --check`：`173 files already formatted`；
- mypy（仓库根）：`Success: no issues found in 69 source files`；
- 前端 DOM 用例（Bun）：`tests 136 / pass 136 / fail 0`。

合并前已确认集成分支 tip 仍为 `911e8d8`（无新提交可合入）。
