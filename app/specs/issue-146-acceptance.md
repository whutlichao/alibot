# #146 验收取证：Plan 四工具、持久化与运行面板数据面

票面正本：GitHub issue #146（Part of #131）。判据来源：`app/specs/agentscope-2-alignment.md`
§3.5 / §4.4；契约正本：`docs/adr/0023-plan-and-sop-are-not-tasks.md`。

- 分支：`issue-146-impl`（从集成分支 tip `911e8d8` 切出）
- 实现 + 测试 + 文档：本提交（commit SHA 见分支 HEAD）
- 未改 D1–D23、既有 ADR 原文、schema 版本与数据库形态（`SCHEMA_VERSION` 仍是 17）；`plan_run` /
  `plan_task` 由 #138 的 15→16 迁移引入，本票只写它们的运行期数据
- 合并了集成分支的 `cfe1dfe`（#143 team 预算 + #149 审批）与后续 `bbea3ed`（同一审批用例的
  集成修复；两边意图相同，本分支保留已格式化的那一版）。合并适配只有两处：
  1. `PlanSession` 的发布回调改为显式 `Callable[..., None]`（mypy）；
  2. #143 让 team 轮次必须带 `team_run_id` / `budget`，因此 #149 的团队审批用例补上这两个装配
     字段；**断言与审批语义未改**。
- **范围边界**：本票只做 Plan 的**运行装配 + 持久化 + 读端点**。运行面板的布局/交互属 #155；
  `team_assignment` 的展示/SSE 属 #144；SOP 属后续票。Plan 的 live-only 帧复用 #141 的
  `plan_update` 公共发布入口，不自拼帧，也不新增 SSE 类型

## 一、逐条判据 → 落点 → 命令与读数

以下命令都从 worktree 根执行；pytest 从 `app/` 跑（`pyproject.toml` 的 `testpaths`）。

### 判据 1｜四工具在任务/team 轮次可调用；`pending`/`in_progress`/`completed` 正确

- 落点：
  - `app/alibot/plan.py::plan_tools()`：`TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate`
    四个 AgentScope 工具，操作 `AgentState.tasks_context.tasks`。
  - `app/alibot/agent_seam.py::_round_toolkit(..., with_plan=True)`：本轮 toolkit 注册四工具；
    `_agent_instance()` 在 Plan 轮次使用 `PlanAgent`，否则仍是原 `Agent`（无 Plan 的路径逐字不变）。
  - `app/alibot/plan.py::PlanRun.finalize()`：任务状态直接来自框架任务对象；`deleted` 不落行
    （快照里表现为缺席），SSE 用 `state=deleted` 的 `plan_update` 说明删除。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_plan.py -k four_plan_tools
# 1 passed
```

读数（`test_four_plan_tools_write_three_states_and_survive_restart`）：
- 三轮真实 seam + 进程内脚本模型实际收到的 provider tool schema 覆盖
  `{TaskCreate, TaskGet, TaskList, TaskUpdate}`（断言 `PLAN_TOOL_NAMES <= seen`）；
- 三次 `GET /api/targets/{id}/plan` 的 run 快照状态集合恰为
  `{"pending", "in_progress", "completed"}`，且每轮 `status == "completed"`；
- 同一任务 `subject` 跨轮保留（`整理验收清单`），证明第二轮/第三轮的 `TaskGet` / `TaskUpdate`
  操作的是装载回来的同一份清单，而不是每轮重建。

### 判据 2｜轮次开始装载最近 Plan、收口回写；重启后 `GET /targets/{id}/plan` 仍可读

- 落点：
  - `app/alibot/plan.py::PlanSession.open()` / `PlanRun.__init__`：开 run 前调用
    `Database.latest_plan_tasks(target_id, member_id)` 装载最近 run，并用 `seq ↔ Task.id`
    还原任务标识；`PlanAgent.reply_stream` 在回复开始前把 `tasks_context` 换成本轮上下文。
  - `Database.finish_plan_run()`：以本 `plan_run_id` 为界重写 `plan_task` 快照并收口。
  - `GET /api/targets/{id}/plan`：按 member 分组、默认每个成员最近 20 run。
- 命令与读数：同上一条命令（同一用例的 after/restart 段）：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_plan.py -k four_plan_tools
# 1 passed
```

- 关掉第一个后端、在**同一数据目录**上换一个全新进程（`backend(data_dir)`）后重新请求
  `GET /api/targets/{task_target}/plan`：三个 run 与三态仍在，任务 `subject` 一致。
- `plan_update` 帧带 `plan_run_id` / `member_id` / `task`，按 target 归属；帧里没有 `seq` / `id`
  （live-only，不推进重连游标）。

### 判据 3｜保留策略（20 run / 30 天）在启动与收口后生效；`plan_task` 级联清理

- 落点：
  - `Database.prune_plan_runs()`：每个 `(target_id, member_id)` 保留最近 20 run 且不早于 30 天
    （先到者为准），在**每轮收口后**（`finish_plan_run` 末尾）与**启动时**
    （`api.create_app` 的 lifespan 第一行）执行。
  - `plan_task.plan_run_id REFERENCES plan_run(id) ON DELETE CASCADE` + `PRAGMA foreign_keys=ON`。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_plan.py -k retention
# 1 passed
```

读数（`test_retention_keeps_latest_twenty_and_prunes_at_startup`）：
- 连续收口 25 个 run 后，`GET /plan` 只返回 20 个（收口后清理立即生效）；
- 把当前最早的一条 `started_at` 回拨到 40 天前，重启后只剩 19 个（**启动时**的 30 天边界生效）；
- 重启后 `SELECT COUNT(*) FROM plan_task WHERE plan_run_id NOT IN (SELECT id FROM plan_run)`
  == `0`（级联清理无孤儿）。

### 判据 4｜Plan 不进看板、未读、搜索、唤醒、预算；不新增 target

- 落点：`plan.py` / `db.py` 的 Plan 写路径只触碰 `plan_run` / `plan_task`；不写消息、不改
  `task` 行、不调预算原语、不产生 `target` 行。事件走 `EventBus.publish` 的 live-only
  `plan_update`，不进入 `message` / `round_outcome`。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_plan.py -k "four_plan_tools or team_leader"
# 2 passed
```

读数：
- Plan 轮次结束后：`GET /api/tasks/{id}` 的 `status` 仍是 `todo`（Plan 的 `completed` 不回写任务五态）；
- `GET /api/tasks/{id}/budget` 的 `used == 0`（Plan 不消耗跳数）；
- `GET /api/search?q=整理验收清单` 的 `hits == []`（Plan `subject` 不进搜索）；
- `SELECT COUNT(*) FROM target` == `2`（频道影子行 + 任务 target；Plan 不新增 target）；
- team 模式（`test_team_leader_and_assigned_member_each_have_their_own_plan`）：
  队长有自己的 run、被 `TeamAssign` 的成员有自己的 run；队长的 system prompt 含
  `任务上下文`，**成员的 prompt 不含**它，也不含任务标题（成员拿到 Plan，但**不获得任务上下文**）。

`GET /api/targets/unknown/plan` 对不存在的 target 返回统一 404 形状（第二个用例）：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_plan.py -k 404
# 1 passed
```

## 二、全量门禁

```bash
scripts/check.sh   # 合并 agentscope2-integration 之后
# pytest（全量）：776 passed, 2 skipped（778 collected）
# ruff check：All checks passed!
# ruff format --check：183 files already formatted
# mypy：Success: no issues found in 77 source files
# pyright：0 errors, 0 warnings, 0 informations
# node --test app/tests/*.test.cjs：136 tests / 136 pass / 0 fail
# 最后一行「门禁全绿」，退出码 0
```

## 三、已知残余

- 运行面板的布局与交互属 #155；本票只提供 `GET /api/targets/{id}/plan` 快照与 `plan_update`
  实时帧。`plan_update` 是 live-only（ADR-0020），断线后由快照端点兜底。
- `team_assignment` 的展示/SSE 属 #144；本票只保证 Plan 与 team 轮次的装配关系。
- Plan 的历史按保留策略裁剪（20 run / 30 天）；它不是审计日志，也不是任务历史。
- `ScriptedAgentSeam` 不模拟 Plan 写路径；本票的 Plan 行为取证使用**真实 seam + 进程内脚本模型**
  （零网络、零成本），脚本化替身仍只用于既有编排/唤醒测试。
