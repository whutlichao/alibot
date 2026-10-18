# #141 验收取证：SSE 事件词表、live-only 帧与前端消费

票面正本：GitHub issue #141（Part of #131）。判据来源：`app/specs/agentscope-2-alignment.md`
§4.4 / §4.5 / §5.1；契约正本：`docs/adr/0020-agent-events-are-live-and-messages-stay-the-record.md`。

- 分支：`issue-141-impl`（从集成分支 tip `7f691db` 切出）
- 实现 + 测试 + 文档：本提交（commit SHA 见分支 HEAD）
- 未改 D1–D23、既有 ADR 原文、schema 版本、数据库形态；`SCHEMA_VERSION` 仍为 15
- **范围边界**：本票只做「事件词表 + SSE 传输 + 前端消费层」。team / Plan / SOP / 审批的
  **领域生产者**分别落在后续票（#142 / #144 / #146 / #148 / #149 等）；这里提供它们的公共发布入口，
  并证明既有 channel / task / dm 三条流对同一形状的帧全部透传。运行面板布局属 #155，本票不提前做。

## 一、逐条判据 → 落点 → 命令与读数

以下命令都从 worktree 根执行；pytest 必须从 `app/` 跑（`pyproject.toml` 的 `testpaths`）。

### 判据 1｜SSE 帧包含规格 §4.5 字段；新事件不发 `id`；`message` / `round_outcome` 仍发 `id: <seq>`

- 落点：
  - `app/alibot/events.py`：`REPLAYABLE_TYPES = {message, round_outcome}`、`LIVE_EVENT_TYPES`
    （六个新类型）、`is_replayable()`、`sse_frame()` 只在可补拉事件上写 `id`。
  - 六个公共构造函数（帧字段的唯一正本）：
    `agent_event()` / `team_assignment()` / `plan_update()` / `sop_event()` /
    `approval_required()` / `approval_resolved()`。字段与 §4.5 逐字一致；
    `team_assignment` 显式携带可空 `task_id`。
  - `app/alibot/orchestration.py::_run_seam`：把 `run_round_stream` 的 `emit` 接到
    `EventBus.publish(target_id, agent_event(record))`——`AgentEventRecord` 从 #140 的 seam
    真正接到 SSE；事件不落库（ADR-0020）。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_event_stream.py \
  -k "agent_events_are_live or task_stream_forwards or channel_and_dm_streams_forward or task_round_emits or sse_frame or event_bus or seq_as_the_event_id"
# 8 passed, 16 deselected in 3.52s
```

读数（`test_agent_events_are_live_only_frames_with_attribution`）：
- 一轮脚本化回复实际到达的 `agent_event.event_type`：
  `REPLY_START` / `TEXT_BLOCK_DELTA` / `REPLY_END`；
- 每条 `agent_event`：`id` 行为 **无**（`frame["id"] is None`），
  `member_id` = 受派成员、`reply_id` 非空、`target_id` = 频道 id、`payload` 是对象；
- 同一流的 `message` / `round_outcome`：`id == str(seq)`。

`test_sse_frame_emits_id_only_for_replayable_events`（帧形状的数据契约）：
- `message(seq=4)` → 首行 `id: 4`；`round_outcome(seq=5)` → 首行 `id: 5`；
- 六个新类型 + `member_status` + `typing` → **无** `id: ` 行，且 `event: <type>` 正确。

`test_stream_frames_use_seq_as_the_event_id`（既有判据按新契约更新）：
- 可补拉帧的 `id` 全等于 payload 的 `seq`；
- `member_status` 等快照 / live-only 帧 `id is None`（不再把快照写成库里不存在的游标）。

### 判据 2｜`GET /api/tasks/{id}/events` 输出 `agent_event` / `team_assignment` / `plan_update` / `approval_*`

- 落点：
  - `app/alibot/api.py::task_events`：订阅 task target，快照为 `message` + `round_outcome`
    （新增 `WorkspaceService.task_round_outcomes`），实时帧对新类型**原样透传**；
    游标过滤只作用于可补拉事件——旧实现 `seq <= after_seq` 会把没有 seq 的新事件整批吞掉。
  - `channel_events` / `dm_events`：同一处过滤修正，三条流都能在重连（`after_seq >= 1`）后继续收到
    live-only 帧。
- 命令与读数：同上 8 passed 中的三条：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_event_stream.py \
  -k "task_stream_forwards or channel_and_dm_streams_forward or task_round_emits"
# 4 passed（task_stream_forwards 1 + channel/dm 参数化 2 + task_round 1）
```

- `test_task_stream_forwards_every_live_runtime_event_and_task_id`：任务线程 SSE 收到
  `agent_event` / `team_assignment` / `plan_update` / `sop_event` / `approval_required` /
  `approval_resolved` 六类；每帧 `id is None`、`target_id` 全是 task 的 target；
  `team_assignment.task_id == task["id"]`、`state == "started"`；同流快照消息仍带 `id == seq`。
- `test_task_round_emits_agent_events_on_the_task_target`：真实的一轮任务线程回复，
  `agent_event` 的 `target_id` 是 **task target**（不是所属频道），`round_outcome` 带 id。
- `test_channel_and_dm_streams_forward_live_runtime_events_after_a_cursor`（参数化 2 条）：
  以 `after_seq=1` 重连后，channel 与 dm 仍然收到全部六类 live-only 帧。

### 判据 3｜未知 `type` 前端忽略且不报错；重连后不重复消息/轮次结果

- 落点：`app/alibot/studio/app.js`
  - `LIVE_EVENT_TYPES` / `isLiveEventType()` / `recordLiveEvent()`：live-only 事件按 `target_id`
    有界保留在 `state.liveEvents`（上限 256），**不写** `seenSeqs`（那份集合同时决定重连游标），
    不进消息流 / 未读 / 搜索；运行面板（#155）从这里取。
  - `handleStreamEvent()` / `handleTaskStreamEvent()`：先消费 live-only；未知 `type` 直接返回，
    不报错、不推进游标；`message` 仍由 `absorbMessage` / `absorbTaskMessage` 按 seq 去重。
  - channel/dms 与 task 两条事件流都注册了六个新类型的监听器。
- 命令与读数：

```bash
node --test app/tests/sse_events_ui.test.cjs
# 4 pass, 0 fail
```

- `新运行事件按 target 记录，不进消息流、不推进游标`：六类事件按到达顺序记录，
  `state.messages` 仍为 0，`lastSeqOf("ch-1") == 0`，去重集合为空；
- `未知 type 忽略且不报错，也不推进重连游标`：未知事件带 `seq: 99` / `42` 也不改变游标；
- `重连后同一消息与同一轮次结果不重复`：同一 `message` 帧两次 → 消息流 1 条；
  同一 `round_outcome` 两次 → `state.outcomes[1]["mem-1"]` 1 条；
- `任务线程消费新事件：按 target 归属，消息仍按 seq 去重`：任务线程新事件记在
  `tgt-task-1` 下，任务消息重复两次只留 1 条，且不混进频道消息流。

### 判据 4｜`EventBus` 队列满时丢事件、不阻塞写路径；快照端点可兜底

- 落点：`app/alibot/events.py` 保持 `QUEUE_LIMIT = 256`，`publish` 对满队列
  `suppress(queue.Full)`；订阅者消费不了只丢它自己的事件，发布方（工作线程）不被阻塞。
  快照兜底：频道 / 任务事件的 `message` + `round_outcome` 由端点读库；运行记录快照由
  `GET /team-runs` / `GET /plan` / `GET /sop-runs/{id}` / `GET /approvals` 提供
  （随各自领域票落地，本票不改它们的形态）。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_event_stream.py -k "event_bus"
# 1 passed, 23 deselected
```

读数：连续发布 `2 * QUEUE_LIMIT` 条后 `qsize() == QUEUE_LIMIT`（先进先出，最早 256 条保留），
发布调用全部正常返回；无订阅者的 target 照常返回载荷。

### 判据 5｜DOM 用例覆盖新事件渲染 / 忽略逻辑

- 落点：新增 `app/tests/sse_events_ui.test.cjs`（沿用 `vm` + `fetch` + DOM 替身的既有接缝，
  不新开前端测试框架）。
- 命令与读数：

```bash
node --test app/tests/*.test.cjs
# tests 136 / pass 136 / fail 0（新增文件自动纳入 glob）
```

## 二、全量门禁

```bash
scripts/check.sh
# pytest（全量）：666 passed, 2 skipped in 308.92s
# ruff check：All checks passed!
# ruff format --check：156 files already formatted
# mypy：Success: no issues found in 62 source files
# pyright：0 errors, 0 warnings, 0 informations
# node --test app/tests/*.test.cjs：136 tests / 136 pass / 0 fail
# 最后一行「门禁全绿」，退出码 0
```

（合并集成分支后重跑同一入口；最终读数以合并后的那次为准。）

## 三、已知残余

- `team_assignment` / `plan_update` / `sop_event` / `approval_*` 的**领域生产者**不在本票：
  本票提供 `events.py` 的六个构造函数 + `EventBus.publish` 作为唯一发布入口；后续票只调用它们，
  不再自己拼帧。
- 运行面板的布局与交互属 #155；`state.liveEvents` 是本票与它之间的消费契约（按 target 归属、
  有界、live-only），不包含视觉层。
- 事件断线不补发是 ADR-0020 的刻意代价；前端以快照端点兜底，不以本地缓存伪造历史。
