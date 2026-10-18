# #147 验收取证：SOP 定义、两步运行、状态持久化与隔离面

票面正本：GitHub issue #147（Part of #131）。判据来源：`app/specs/agentscope-2-alignment.md`
§3.5 / §4.4；契约正本：`docs/adr/0023-plan-and-sop-are-not-tasks.md`。

- 分支：`issue-147-impl`（从集成分支 tip `ceb393c` 切出；完成后合并 `ca995ac`（#145））
- 实现 + 测试 + 文档：本提交（commit SHA 见分支 HEAD）
- 合并适配只有一处：`orchestration.py` 自动合并了 #145 的任务线程改动与本票的
  `scheduler` 只读属性；断言与语义未改。`test_team_task_threads.py` 随集成分支进入，全量门禁已覆盖
- 未改 D1–D23、既有 ADR 原文与 schema 形态（`SCHEMA_VERSION` 仍是 17）；`sop_definition` /
  `sop_run` 由 #138 的 15→16 迁移引入，本票只写它们的运行期数据
- **范围边界**：运行面板的布局/交互属 #155。本票提供 `GET /api/sops*`、
  `GET /api/sop-runs/{id}`、`POST /api/sop-runs/{id}/resume` 与 `sop_event` 生产者；
  实时帧复用 #141 的 `sop_event` 公共入口，不自拼帧，也不新增 SSE 类型

## 一、逐条判据 → 落点 → 命令与读数

以下命令都从 worktree 根执行；pytest 从 `app/` 跑（`pyproject.toml` 的 `testpaths`）。

### 判据 1｜owner 可创建/启动 SOP；Agent 不能自行创建/运行

- 落点：
  - `app/alibot/api.py` 的 `GET/POST /api/sops`、`GET/PATCH /api/sops/{id}`、
    `POST /api/sops/{id}/runs`：全部是**人类面**端点，载荷 `extra="forbid"`；
    `POST /runs` 只接受 `{target_id}`，`leader_member_id` 由服务端固定为 `owner`
    （`SOPRunner.start` → `Database.create_sop_run(leader_member_id=OWNER_ID)`）。
  - `app/alibot/db.py::Database.create_sop_definition / _prepare_sop_steps`：executor/verifier
    必须是未删除、已启用的 **Agent** 成员；任一步非法整体拒绝。
  - 成员面没有任何 SOP 工具：`agent_seam._round_toolkit` 只装配 workspace / 任务动作 /
    沉默 / Plan 四工具；`sop.py` 不被 `plan_tools()` 或任何 toolkit 引用。Agent 只能经
    `sop_agent()` 被装配为**参与者**，没有创建/启动入口。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_sop.py -k "owner_runs_two_step or disabled_definition or validation"
# 3 passed, 5 deselected
```

读数：
- `POST /api/sops` → 201，`GET /api/sops` 列出同一条定义；`GET /api/sops/{id}` 读回；
- `POST /api/sops/{id}/runs` → 201，`run.leader_member_id == "owner"`，`phase == "pending"`；
- `PATCH {enabled: false}` 后启动被 400 `validation_failed` 拒绝；重新启用后可启动；
- 启动请求带 `leader_member_id` → 400（`extra="forbid"`）；`GET /api/sops/{id}/runs` → 405；
- 空 `steps`、`max_attempts=0` → 400；未知成员 → 404；未知定义/运行 → 404
  （`test_definition_validation_and_unknown_ids`）。

### 判据 2｜两步流程：executor 产出、verifier 拒绝一次、重试后完成；`max_attempts` 生效

- 落点：
  - `app/alibot/sop.py::build_sop`：定义行 → 真实 `agentscope.sop.SOPStep`（executor +
    verifier + `max_attempts`）→ `SOP`；`SOPEngine` 是唯一状态机裁决者。
  - `app/alibot/sop.py::SOPRunner._drive_async`：`engine.reply_stream()` 在后台线程里跑；
    每次尝试后把 `engine.state.model_dump_json()` 与派生 `phase` 写回 `sop_run`。
  - 重试原因是真实引擎写进 `SOPStepRunState.verifications` 并由 `SOPStep._brief` 原样交回
    executor 的；测试让 verifier 的第二次调用返回 pass。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_sop.py -k "owner_runs_two_step or max_attempts"
# 2 passed, 6 deselected
```

读数：
- 抓到的 `SOPRunState.steps[0].verifications` 恰好两条：`passed = [False, True]`，
  第一条 `message == "缺少验收命令"`；
- executor 被调用 2 次，且第二次输入里含第一条拒绝原因（`"缺少验收命令" in inputs`）；
- `max_attempts=2` + 永远拒绝 ⇒ `phase == "failed"`，`step.phase == "failed"`，
  `len(verifications) == 2`、executor 调用 2 次（引擎不做第 3 次尝试）。

### 判据 3｜`awaiting` 可恢复；重启后 `SOPRunState` 可恢复

- 落点：
  - `SOPRunner._remember_awaiting`：`RequireUserConfirmEvent` / `RequireExternalExecutionEvent`
    的 `reply_id` 与 `tool_calls` 作为**进程内**停车详情；`GET /api/sop-runs/{id}` 的
    `awaiting` 字段返回它（非 awaiting 时为 `null`）。
  - `POST /api/sop-runs/{id}/resume` → `SOPRunner.resume`：只接受 `phase == "awaiting"`，
    构造 `UserConfirmResultEvent`（结构化 `confirm_results`，与审批同形）后从库里的
    `SOPRunState` 继续；`reply_id` 省略时用进程内记录，重启后必须显式给出。
  - 重启：`SOPRunState` 在每次事件后写回 `sop_run.state`；新进程只依赖这一列重建
    `SOPEngine`。已完成步骤直接跳过（引擎行为）；参与者 AgentState 不在本票恢复（D19）。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_sop.py -k "awaiting_run or survives_restart"
# 2 passed, 6 deselected
```

读数：
- `awaiting` 快照：`phase == "awaiting"`、`state.steps[0].phase == "awaiting"`、
  `awaiting.reply_id == "sop-park"`、`awaiting.tool_calls[0].name == "write_file"`；
- `POST /resume {}` 后 `phase == "completed"`、`awaiting == null`、executor 第 2 次调用
  收到的是恢复事件；
- 两段流程在第一步 `completed`、第二步 `awaiting` 时重启：新进程 `GET` 读回的
  `state` 与重启前**逐字相等**（含两步 phase 与 verdicts）；`POST /resume` 显式给
  `reply_id` 后完成；重启进程里第一步的 executor/verifier **零调用**（已完成步骤不重跑）。

### 判据 4｜SOP 不改任务五态、不进看板/未读/搜索/预算；运行记录与 SSE 可查

- 落点：
  - 写路径只有 `sop_definition` / `sop_run`：不写 `task`、`message`、`target`、
    `round_outcome`、`target_budget`、`plan_run`；`sop_run` 不新增 target。
  - `sop_event` SSE：`SOPRunner._observe` 把 `SOP_STEP_STARTED/ENDED` 映射为
    `{sop_run_id, step_index, phase, payload}`（payload 含 `step` / `attempt` /
    `attempts` / `verdict`），收口时再发一帧 run phase；`EventBus.publish` 打上
    `target_id`。live-only：无 `seq` / `id` 行（#141 的 `sse_frame` 规则）。
  - 保留策略：`Database.prune_sop_runs` 保留最近 50 个或 90 天（先到者为准），
    启动时与每次新建 run 后执行；**没有删除定义的路径**。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_sop.py -k "retention or does_not_touch"
# 2 passed, 6 deselected
```

读数：
- SOP 挂在 task target 上运行完成后：`task.status == "todo"`、任务与频道
  `budget.used == 0`、`search(q=步骤 subject).hits == []`、频道 `unread_count == 0`；
- 库内读数：`task` 仍 1 行且 `status = todo`；`message` 条数与运行前**相同**
  （SOP 不落消息）；`target` 仍是 2 行（频道 + 任务）；`plan_run` 为 0（SOP 不写 Plan）；
- 保留策略：连续建 53 个 run 后只留 50；把最早一条回拨 100 天后启动清理只删这 1 条；
  定义仍在（`GET /api/sops/{id}` → 200）；
- `sop_event`：`step_index == 0`、`phase` 覆盖 `running`/`completed`，两个 verdict
  按 `[False, True]` 顺序出现；所有帧都无 `seq` / `id`。

## 二、全量门禁

```bash
scripts/check.sh   # 合并 agentscope2-integration（ca995ac）之后
# pytest（全量）：799 passed, 2 skipped（801 collected）
# ruff check：All checks passed!
# ruff format --check：190 files already formatted
# mypy：Success: no issues found in 81 source files
# pyright：0 errors, 0 warnings, 0 informations
# node --test app/tests/*.test.cjs：136 tests / 136 pass / 0 fail
# 最后一行「门禁全绿」，退出码 0
```

## 三、已知残余

- 运行面板的布局与交互属 #155；本票只提供读端点与 `sop_event` 实时帧。
- `awaiting` 的停车详情（`reply_id` / `tool_calls`）是进程内事实、不落库；重启后恢复必须由
  owner 显式给 `reply_id`。参与者 AgentState 的跨重启恢复按 D19 留给后续票。
- 对 `steps` 的结构性编辑只影响后续 run；既有 run 的 `state` 从不重写。若在 `awaiting`
  期间把步骤数改掉，恢复时真实引擎会因状态与定义步数不一致而把该 run 收口为 `failed`
  （可读日志），不会静默重跑已完成的步骤。
- 本票不提供「Agent 自主启动 SOP」或「把 SOP 步骤升级成任务」的任何路径（ADR-0023 §四）。
