# Issue #144 验收：成员产出可见性（运行记录 + SSE，不占消息 / 未读 / 搜索）

- 票型：impl（G-impl）
- 分支：`issue-144-impl`
- 基线：集成分支 tip `ecf6501`（含 #141 SSE 词表、#142 team 模式、#143 预算与 `team_assignment` 读写）
- 落点：`app/alibot/team.py`（分派 SSE 发布、成员 `reply_id` 归属）、
  `app/alibot/agent_seam.py`（把 `ReplyStart.reply_id` 交给 pipeline）、
  `app/alibot/orchestration.py`（团队轮次注入发布口）、
  `app/alibot/db.py` / `service.py` / `api.py`（`GET /api/targets/{id}/team-runs` 倒序分页 + `task_id` 过滤）、
  `app/README.md`（端点与事件词表同步）
- 测试：`app/tests/test_team_visibility.py`（HTTP 黑盒 + 真实 HTTP SSE + 真 `BudgetedTeamPipeline` +
  进程内脚本模型；穿透/不穿透用例走既有 `ScriptedAgentSeam`）

## 1. 判据 → 落点

| 票面判据 | 落点 |
| --- | --- |
| 成员输出在 SSE 中带 `member_id` / `reply_id` / `target_id`，运行面板可按成员归属 | `agent_seam._agent_event_record` 的派生名 + `reply_id` 归属（#140/#141 既有）；`test_...channel...` 断言成员 `REPLY_START` 与成员正文都带三字段 |
| 成员结论只走 `team_assignment`，不写 `message`、不计未读、不进搜索 | `BudgetedTeamPipeline._record_assignment` / `_finish_assignments` 只写 `team_assignment`；`run_round` 仍只把队长的 `RoundOutcome.messages` 交给 `_commit_reply`。测试断言频道/任务线程 agent 消息恰好队长 1 条、未读 1、搜索 0 |
| 频道与 task 线程都只落队长最终 1 条 `message`；`round_outcome` 只记队长 | 同上；`test_...channel...` 断言 `GET round-outcomes` 只有队长一行，`test_...task...` 断言 SSE `round_outcome` 只有队长 |
| `team_assignment` 记录 prompt / state / reply_excerpt / 起止时间；`GET /targets/{id}/team-runs` 可查 | #143 的 `create/finish_team_assignment` 写入；本票新增 `Database.list_team_runs_page` + `WorkspaceService.team_runs` + `GET /api/targets/{target_id}/team-runs`（`started_at DESC, id DESC`、`limit` / `offset` / `task_id`、`has_more`） |
| SSE `team_assignment` 用 #141 公共入口，带 `team_run_id` / `assignment_id` / `task_id` / `member_id` / `reply_id` / `state` | `events.team_assignment()` 构造；`BudgetedTeamPipeline` 在 `started` / `budget-exhausted` / 终态三处调用注入的 `TeamRoundContext.publish`；`target_id` 由 `EventBus.publish` 补齐，帧无 `id` 行 |
| 穿透回复按 D17 仍落频道消息，且不产生 `team_assignment`、不再唤醒；team task 不穿透 | 既有 `_notify_team` 的 channel 穿透分支不变；穿透轮次 `_team_round_context` 为 `None`，因此不写 `team_assignment`。测试断言穿透后 `pending_count()==0`、`team_assignment` 为空；task 测试断言外援零调用 |

## 2. 逐条命令与读数

所有 pytest 命令均在 `app/` 下执行；`-o addopts=""` 只为显示汇总行（仓库默认 `-q`）。

### AC1 成员 SSE 归属

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_visibility.py -o addopts="" -q \
  -k "channel_member_output or task_thread_member_output"
```

读数：`2 passed, 3 deselected`。

覆盖：真 pipeline 运行成员后，`agent_event` 里存在 `member_id == 成员` 的 `REPLY_START`，每条成员事件
`reply_id` 非空、`target_id` 是频道或 task target；成员正文以队长 `TOOL_RESULT_TEXT_DELTA` 出现在直播里；
`team_assignment` 帧的 `member_id`、`assignment_id`、`team_run_id` 齐全，终态帧 `reply_id` 非空，且
`agent_event` / `team_assignment` 都没有 `id` 行（live-only，不推进重连游标）。

### AC2 消息 / 未读 / 搜索 / 轮次结果

与 AC1 同一组用例（两条 target 各一遍）。读数：`2 passed, 3 deselected`。

- 频道：agent `message` 恰好 `["队长最终答复"]`；`GET /api/channels` 的 `unread_count == 1`；
  `GET /api/search?q=成员过程结论&facet=message` 返回 `[]`；`round_outcomes` 只有队长一行。
- 任务线程：`GET /api/tasks/{id}/messages` 的 agent 消息恰好 `["队长任务答复"]`；任务
  `unread_count == 1`；同一搜索返回 `[]`；SSE `round_outcome` 只有队长、状态 `replied`。

### AC3 运行记录字段、快照端点、分页与 task 过滤

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_visibility.py -o addopts="" -q \
  -k "team_runs_are_paginated or task_thread_member_output"
```

读数：`2 passed, 3 deselected`。

覆盖：

- `GET /api/targets/{channel_id}/team-runs?limit=10` 返回 3 条 `completed` run；前两条与
  `limit=2&offset=0` 的返回**逐 id 相同**，`offset=2` 返回第三条且 `has_more=false`；
  `started_at` 非递增（倒序）。
- 分派快照字段：`prompt == "写出你的结论"`、`state == "succeeded"`、
  `reply_excerpt == "成员过程结论-绝密-只该出现在运行记录"`、`started_at` / `finished_at` 非空、
  `member_id` 是成员、频道分派 `task_id is null`。
- `GET /api/targets/{task_target_id}/team-runs?task_id=<task_id>` 返回该 task 的 run 与分派，
  `assignments[0].task_id == task_id`；不存在的 `task_id` 返回空 `runs`。

### AC4 穿透（channel 落消息、不产生分派、不再唤醒；task 不穿透）

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_visibility.py -o addopts="" -q \
  -k "pierced_reply or team_task_does_not_pierce"
```

读数：`2 passed, 3 deselected`。

- 频道：人类消息 @ 未加入的外援后，agent 消息为 `["队长最终答复", "外援一次性答复"]`（D17 仍落频道
  消息）；`list_team_assignments(channel) == []`；`ManualScheduler.pending_count() == 0`（回复不再唤醒）；
  外援正文可被搜索（它是真正的频道消息）。
- task：@ 外援后外援 `seam.calls_for(...) == []`；任务线程 agent 消息只有队长 1 条。

### 全量用例

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_visibility.py -o addopts="" -q
```

读数：`5 passed`。

## 3. 全量门禁读数

```bash
cd /tmp/alibot-wt/issue-144-impl && scripts/check.sh
```

读数：`门禁全绿`。

- pytest 全量：`777 passed, 2 skipped`（含本票 5 条）；
- `ruff check`：`All checks passed!`；
- `ruff format --check`：`182 files already formatted`；
- mypy（仓库根）：`Success: no issues found`；
- pyright：无错误；
- 前端 DOM 用例（Bun）：`tests 136 / pass 136 / fail 0`。

合并说明：集成分支 `bbea3ed` 的 #149 审批用例装配补丁有一处未格式化；本分支为保持
`scripts/check.sh` 全绿只做了**等价的单行换行**（`app/tests/test_approval_seam.py`，无行为改动）。

## 4. 设计说明与边界

- **`team_assignment` 的两帧**：`started` 在成员运行前发出（此时还没有成员的 `reply_id`，该字段为空
  字符串）；终态帧在成员回复事件都已消费之后发出，`reply_id` 指向该成员本次分派的回复。
  分派本身用 `assignment_id` 关联，前端不依赖两个帧的 `reply_id` 相等。
- **不做 DDL**：`team_assignment` 的 prompt / state / reply_excerpt / 起止时间在 #143 已落表；
  本票只加读面与直播，`SCHEMA_VERSION` 不变。
- **分页用 `offset` 而不是游标**：运行面板按「倒序 + 条数」翻页已足够；`has_more` 由多取一条判定，
  不会在恰好取完时谎报下一页。`task_id` 只做命中过滤（未知 task 与空页同形）。
- **SSE 断线不补**：`agent_event` / `team_assignment` 是 live-only（ADR-0020）；重连后运行面板以
  `GET /team-runs` 的快照恢复，`after_seq` 仍只补消息与 `round_outcome`。
- **发布失败不改轮次结论**：`publish` 回调异常只记 debug，与 `agent_event` 的 `emit` 同一条纪律。
