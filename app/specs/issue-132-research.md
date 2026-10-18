# Issue #132 研究：TeamPipeline 拦截点、事件归属与预算原子性

- 日期：2026-10-18
- 票型：research（G-research），只做研究与最小脚本，**不改 `app/alibot/**`、`app/tests/**`**
- 依赖版本：`agentscope==2.0.10`（Python 3.11.13，SQLite 3.49.1）
- 复跑脚本：`app/specs/issue-132-research/`
- 结论：票面 5 条判据全部取得真实读数；`_run_assignments()` 是可行的分派前拦截点，`reply_id` 归属成立，单事务条件 UPDATE 在 100 并发下没有越过上界。

## 0. 环境与复跑入口

```bash
# 仓库根
.venv/bin/python --version
# Python 3.11.13

.venv/bin/python -c "import agentscope; print(agentscope.__version__)"
# 2.0.10

.venv/bin/python app/specs/issue-132-research/probe_team_pipeline.py
.venv/bin/python app/specs/issue-132-research/probe_hitl.py
.venv/bin/python app/specs/issue-132-research/probe_budget_atomicity.py
```

三个脚本都用 `_harness.py` 里的脚本化 `ChatModelBase`，不访问网络、不需要 provider key；`reply_id` 每次运行随机，但结构与计数稳定。

## 1. 判据一：`_run_assignments()` 在成员运行前拿到 `ToolCallBlock`

### 源码符号

| 符号 | 位置（`agentscope==2.0.10`） | 作用 |
| --- | --- | --- |
| `TeamPipeline.reply_stream` | `pipeline/_team_pipeline.py:161` | 公开流式入口；docstring 明写“流在每位参与者完成或挂起在 HITL 时结束” |
| `TeamPipeline._reply` | `pipeline/_team_pipeline.py:205` | 先跑队长 `reply_stream`，收集 `RequireExternalExecutionEvent` 里的 `TeamAssign` 调用 |
| `TeamPipeline._run_assignments` | `pipeline/_team_pipeline.py:284` | 唯一在**成员真正运行前**拿到整批 `ToolCallBlock` 的位置 |
| `member.agent.reply_stream` 调用 | `pipeline/_team_pipeline.py:311` | 在 `run_group()` 内，晚于 `_run_assignments` 的入口 |
| 挂起判断 | `pipeline/_team_pipeline.py:336-337` | 成员挂起后 `break`，不再排后续调用 |

### 探针做法

`probe_team_pipeline.py` 继承 `TeamPipeline` 覆写 `_run_assignments()`：先逐条记录 `ToolCallBlock`（`id` / `name` / 解析出的 `member`），再把原参数交给 `super()._run_assignments(...)`。两个成员（`m-alpha`、`m-beta`）各自包了一层 `TracingAgent`，在进入 `reply_stream()` 时记录 `MEMBER_STREAM_START`。

### 真实读数

```text
$ .venv/bin/python app/specs/issue-132-research/probe_team_pipeline.py
agentscope_version=2.0.10
total_events=55
member_stream_starts=2
intercept_count=2
intercept=('INTERCEPT', 'call-alpha', 'TeamAssign', 'm-alpha', 'fresh')
intercept=('INTERCEPT', 'call-beta', 'TeamAssign', 'm-beta', 'fresh')
all_intercepts_before_first_member_stream=True
assigned_members=['m-alpha', 'm-beta']
assignment_results=[2]
```

- 队长在**同一次模型回复**里发出两个 `TeamAssign`；覆写方法一次拿到 2 条 `ToolCallBlock`，并保持调用顺序。
- 两条拦截记录都早于**所有** `MEMBER_STREAM_START`：`all_intercepts_before_first_member_stream=True`。
- 第二段 `FilteringTeamPipeline` 场景在覆写里直接拒绝 `call-beta`、追加错误 `ToolResultBlock`，只把 `call-alpha` 交给基类：

```text
filtered_total_events=37
filtered_member_stream_starts=['m-alpha']
filtered_denied=[('DENIED', 'call-beta', 'm-beta')]
filtered_denied_member_stream_started=False
```

这证明拦截点不仅能“看到”，还能在成员运行前**阻止**某条分派。正式实现需在这一点把未获预算的 call 转成 `ToolResultBlock(state=ERROR)`，并且只把获授权的 calls 交给 `super()`；合并结果时按原始 `tool_calls` 顺序排序（基类只对已运行的 `round_results` 排序，预先塞入的错误结果需要实现自己保证顺序）。

## 2. 判据二：`ReplyStartEvent.name` + `reply_id` 的归属

### 源码符号与字段

- `ReplyStartEvent`（`event/_event.py:82`）同时有 `name`（`:91`）与 `reply_id`（`:89`）。
- 后续 `TextBlock*` / `ThinkingBlock*` / `ToolCall*` / `ToolResult*` / `RequireUserConfirmEvent` 等只有 `reply_id`，没有 `name`。
- 事件里的 `reply_id` 由 `Agent._reasoning_impl()` 写入，例如 `TextBlockDeltaEvent`（`agent/_agent.py:3828-3832`）、`ToolCallStartEvent`（`agent/_agent.py:3845-3849`）。

### 归属算法（已在探针中运行）

1. 建立 `pipeline 内部名 → member id` 表：`m-alpha → alpha`、`m-beta → beta`（D10 的派生名）。
2. 收到 `ReplyStartEvent` 时用 `event.name` 查表，写入 `reply_id → member_id`。
3. 其余事件按 `reply_id` 查表归属；查不到就丢弃并记 debug，不猜。
4. `target_id` 由当前轮次注入（探针里未模拟 DB，归属链只验证到 member）。

### 真实读数

```text
reply_start_count=3
reply_id_to_member={'0f6b77a4dfbc48c1b1b38dbf01433c6e': 'leader', '15f6760bdbab418897422e99650516b4': 'alpha', '397a4a01367a4de7a26196d161006497': 'beta'}
events_before_first_reply_start=0
orphan_count=0
orphan_events=[]
member=alpha TextBlockDelta=2 ToolCallStart=1 ToolResultStart=1 ReplyEnd=1 total=18
member=beta TextBlockDelta=2 ToolCallStart=1 ToolResultStart=1 ReplyEnd=1 total=18
member=leader TextBlockDelta=0 ToolCallStart=2 ToolResultStart=2 ReplyEnd=1 total=19
event_classes_without_reply_id=['CustomEvent']
```

- 55 个事件全部归到 3 个 `ReplyStart` 名下，没有孤儿；`TextBlockDelta`、`ToolCallStart`、`ToolResultStart` 都能稳定落到成员。
- 队长的 `ToolCallStart` 有 2 个（两个 `TeamAssign`），`ToolResultStart` 有 2 个（外部执行结果）。
- 队长在该探针里没有 `TextBlockDelta`，因为最终文本用单个 `is_last=True` 响应返回；这本身验证了 `ToolCall*` 事件只有在**流式非最终块**里才会被 `_convert_chat_response_to_event` 转换出来（`agent/_agent.py:1704` 起）。正式 seam 若要把队长/成员的文本增量都发出来，模型流式输出必须真的分块。

### 没有 `reply_id` 的事件与丢弃策略

对 `agentscope.event._event` 里所有具体事件类做字段内省，唯一没有 `reply_id` 的是：

```text
event_classes_without_reply_id=['CustomEvent']
```

补充事实：

- `CustomEvent`（`event/_event.py:533`）只有 `name` / `value`，设计上允许未知前端跳过；它**无法**满足 D4 的“每条新 SSE 事件带 `reply_id`”。
- `EventBase` 本身也没有 `reply_id`，但它不是可发出的事件。
- `Msg` 不是 `AgentEvent`。`TeamPipeline` 在成员侧消费 `Msg`（`pipeline/_team_pipeline.py:318-335`），调用方拿不到；队长侧不带 `yield_final_msg=True`。
- **HITL 恢复流没有新的 `ReplyStart`**：见 §4，`phase2_reply_start_count=0`。如果每个 `reply_stream()` 调用都新建 `reply_id → member` 表，恢复流的 11 个事件会全部变成孤儿（`phase2_orphans_if_map_starts_fresh=11`）。

建议的丢弃/保留策略（研究结论，不是产品裁定）：

| 事件 | 策略 |
| --- | --- |
| 有 `reply_id` 且在表内 | 归属并透传 |
| 有 `reply_id` 但表内没有 | 丢弃 + debug（规格 §3.2），不猜 |
| `CustomEvent`（无 `reply_id`） | 默认丢弃；若业务必须透传，需适配层用当前轮次/参与者上下文显式补 `reply_id`，否则违反 D4 |
| `Msg`（非 `AgentEvent`） | 不作为事件透传；成员 `Msg` 已被 pipeline 消费为 `ToolResult` |

**关键实现约束**：`reply_id → member_id` 表必须跨 `TeamPipeline.reply_stream()` 的多次调用存活（至少覆盖同一个队长轮次），或者在恢复时从 `pipeline._parked_members()` 的 `agent.state.reply_id` 预填。否则 HITL 恢复、以及任何“先返回再续流”的路径都会丢掉归属。

## 3. 判据三：SQLite 单事务预算扣减

### SQL（探针逐字执行）

```sql
BEGIN IMMEDIATE;
UPDATE target_budget
   SET used = used + 1
 WHERE target_id = ? AND used < ?;
COMMIT;   -- rowcount = 0 时改用 ROLLBACK，used 不变
```

每次尝试使用独立连接，`PRAGMA busy_timeout=30000`；100 个线程在同一 barrier 后同时开始。

### 真实读数

```text
$ .venv/bin/python app/specs/issue-132-research/probe_budget_atomicity.py
agentscope_version=2.0.10
sqlite_version=3.49.1
attempts=100 limit=7 rounds=3
round=1 atomic_success=7 atomic_failure=93 final_used=7 upper_bound_respected=True
round=2 atomic_success=7 atomic_failure=93 final_used=7 upper_bound_respected=True
round=3 atomic_success=7 atomic_failure=93 final_used=7 upper_bound_respected=True
control_naive_check_then_increment_success=100 control_naive_check_then_increment_failure=0 final_used=100 upper_bound_respected=False
```

- 3 轮、每轮 100 次并发尝试，上界 7：每轮正好 7 次成功、93 次失败，`final_used=7`。
- 对照组的“先读 `used`、再 `used = used + 1`”在 barrier 窗口下 100 次全部通过检查，`final_used=100`，越过上界——说明必须使用**同一条条件 UPDATE**，不能先读后写。
- 注意 `rowcount = 0` 时事务里没有写入；`ROLLBACK` 只是收尾。若 `target_budget` 行不存在，条件 UPDATE 同样返回 0；实现需要先保证行存在（upsert 或由既有的预算行创建路径保证），不能把“缺行”误判为“预算耗尽”以外的状态。

## 4. 判据四：`RequireUserConfirmEvent` 的 `reply_id` 路由与挂起时的流结束

### 源码符号

- 成员在工具权限为 `ASK` 时发出 `RequireUserConfirmEvent(reply_id=self.state.reply_id, ...)`：`agent/_agent.py:2574-2577`。
- `TeamPipeline._reply()` 对 `UserConfirmResultEvent` / `ExternalExecutionResultEvent` 先比较 `inputs.reply_id != self.leader.state.reply_id`（`pipeline/_team_pipeline.py:224-229`），再用 `_parked_member(inputs.reply_id)` 找到挂起成员（`:235`、`:386-393`）。
- 成员挂起后 `run_group` 在 `_is_parked()` 为真时 `break`（`:336-337`）；`_reply()` 在 `results` 为空时直接 `return`（`:242-243`），因此流结束但队长仍停在等待工具调用上。

### 真实读数

```text
$ .venv/bin/python app/specs/issue-132-research/probe_hitl.py
agentscope_version=2.0.10
phase1_events=17
phase1_last_event=RequireUserConfirmEvent
phase1_require_count=1
phase1_require_reply_id=ab6ac2c99f4d4756bb99322f2af0eb9f
leader_reply_id=dd3f59e2996743fe8234e9fde5ef8e00
member_reply_id=ab6ac2c99f4d4756bb99322f2af0eb9f
require_reply_id_is_member=True
require_reply_id_is_leader=False
routed_member=m-alpha
leader_parked=True
member_parked=True
phase1_assignment_results=[0]
phase1_stream_exhausted_while_parked=True
phase2_events=11
phase2_reply_start_count=0
phase2_events_with_reply_id=11
phase2_orphans_if_map_starts_fresh=11
phase2_last_event=ReplyEndEvent
phase2_assignment_results=[0, 1]
leader_parked_after=False
member_parked_after=False
```

解释：

- `RequireUserConfirmEvent.reply_id` 等于**成员**的 `state.reply_id`，不等于队长的；`_parked_member(reply_id)` 返回 `m-alpha`。
- 第一个流在 `RequireUserConfirmEvent` 之后**自然结束**（`phase1_stream_exhausted_while_parked=True`），队长与成员都处于 `has_awaiting_tool_calls=True`；`results` 为空（`[0]` 表示 0 条结果）。
- 用同一个 `reply_id` 回送 `UserConfirmResultEvent` 后，恢复流只产生成员/队长的后续事件，**没有新的 `ReplyStartEvent`**；成员完成、结果转成队长的 `ExternalExecutionResultEvent`，队长继续并 `ReplyEnd`，两者都不再挂起。
- 若适配层把每次 `reply_stream()` 当成独立归属上下文，恢复流的 11 个事件会全部无法归属。必须复用/预填 `reply_id → member` 表（见 §2）。

## 5. 判据五：报告与源码改动范围

- 本报告：`app/specs/issue-132-research.md`
- 可复跑探针：`app/specs/issue-132-research/`（`_harness.py`、`probe_team_pipeline.py`、`probe_hitl.py`、`probe_budget_atomicity.py`）
- 未改 `app/alibot/**`、`app/tests/**`、`app/pyproject.toml`、`.github/**`；`scripts/check.sh` 的 ruff / ruff format 对新增脚本通过（见提交前门禁记录）。

## 6. 对后续实现票的约束（研究结论）

1. **拦截点**：`team.py` 继承 `TeamPipeline`，覆写 `_run_assignments()`；先逐条解析并做预算裁决，再把获授权的 calls 交给基类。基类会并发运行不同成员、同一成员串行；结果要按原始 `tool_calls` 顺序合并。
2. **原子扣减**：`try_consume_jump(target_id)` 必须在一个 `BEGIN IMMEDIATE` 里解析 `applied_limit` 并执行条件 UPDATE；不能先读后写，也不能用 `on_acting`。
3. **事件归属**：`ReplyStartEvent.name` 只用于建表，后续一律按 `reply_id`；表要跨 `reply_stream()` 调用存活，HITL 恢复时用 `_parked_members()` 的 `state.reply_id` 预填。
4. **没有 `reply_id` 的事件**：`CustomEvent` 不能直接透传为满足 D4 的新事件；要么补上下文，要么丢弃。`Msg` 不是事件。
5. **实验性 API**：`_run_assignments` 是私有方法，升级 2.0.10 之外的小版本必须重跑本票的三个探针。

## 7. 残余与风险

- `_run_assignments` 在**两个路径**被调用：队长的普通分派（`:252-258`）和 HITL 恢复（`:236-240`，带 `resumed`）。覆写必须同时覆盖，且恢复路径传入的 `tool_calls` 是 `_assigning_calls(member)`，可能包含该成员排队的多条调用。
- 预算扣减与 `team_assignment` 记录要同事务/同写路径，否则崩溃窗口会出现“扣了跳但没有运行记录”或反之；本票只验证预算上界，不验证运行记录一致性。
- 本探针的“拒绝分派”场景把错误结果直接塞进 `results`，没有演示与基类 `round_results` 的稳定排序；正式实现需要自己按 `tool_calls` 顺序合并。
- `TeamPipeline` 与事件集合都是实验性接口；2.0.10 的 `_run_assignments` 在 2.0.9→2.0.10 已改过一次并发收尾（规格 §2.6），升级需重跑。
- 事件量：探针只验证归属，不验证 `EventBus` 的 256 队列上限；丢事件策略仍按规格。
