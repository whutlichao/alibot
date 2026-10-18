# Issue #136 研究：HITL 暂停/恢复语义

- 日期：2026-10-18
- 票型：research（G-research）；只做研究与最小脚本，**不改 `app/alibot/**`、`app/tests/**`**
- 依赖版本：`agentscope==2.0.10`（Python 3.11.13）
- 复跑脚本：`app/specs/issue-136-research/`（复用 `app/specs/issue-132-research/_harness.py` 的脚本化模型与 ASK 工具）
- 结论：票面 4 条判据全部取得真实读数。批准路径可用；拒绝路径的真实工具结果是 `ToolResultState.DENIED`（不是 `ERROR`/`INTERRUPTED`），且轮次可以正常收口；`TeamPipeline` 确实按**成员**的 `reply_id` 路由；AgentScope 没有内建超时，超时只能由进程内 `ApprovalBroker` 计时并用 `UserInterruptEvent` 收口。

## 0. 环境与复跑入口

```bash
# 仓库根
.venv/bin/python --version
# Python 3.11.13

.venv/bin/python -c "import agentscope; print(agentscope.__version__)"
# 2.0.10

.venv/bin/python app/specs/issue-136-research/probe_single_agent.py
.venv/bin/python app/specs/issue-136-research/probe_team_hitl.py
.venv/bin/python app/specs/issue-136-research/probe_broker.py
```

三个脚本全部进程内运行：脚本化 `ChatModelBase`、真实 `Agent`/`TeamPipeline`、真实权限引擎与事件流；不访问网络、不需要 provider key。`reply_id` 每次运行随机，但结构与计数稳定。

| 脚本 | 覆盖判据 |
| --- | --- |
| `probe_single_agent.py` | 单 Agent 批准 / 拒绝 / 错误 `reply_id` / 重复决策 / 取消 / 空闲取消 |
| `probe_team_hitl.py` | TeamPipeline 挂起、按 `reply_id` 路由、双成员、错误路由、取消、等待 |
| `probe_broker.py` | `ApprovalBroker` 原型：批准、拒绝、部分决策、超时、迟到决策、取消 |

## 1. 判据一：单 Agent 的批准与拒绝

### 源码符号

| 符号 | 位置（`agentscope==2.0.10`） | 作用 |
| --- | --- | --- |
| `Agent._reply_impl` | `agent/_agent.py:1035` | 统一入口；`UserConfirmResultEvent` 与 `UserInterruptEvent` 在这里分流 |
| `UserInterruptEvent` 短路 | `agent/_agent.py:1084-1100` | 只在有挂起工具调用时产出 `ReplyEndEvent(INTERRUPTED)` |
| `Agent._check_incoming_event` | `agent/_agent.py:1843` | 校验工具调用 id；**不校验 `reply_id`** |
| `Agent._handle_incoming_event` | `agent/_agent.py:1926` | 批准 → `ALLOWED`；拒绝 → `_handle_error_tool_call(state=DENIED)` |
| `Agent._execute_tool_call` | `agent/_agent.py:2431` | ASK 时把调用置 `ASKING` 并 yield `RequireUserConfirmEvent`（`:2574`） |
| `Agent._handle_error_tool_call` | `agent/_agent.py:2803` | 写入 `ToolResultBlock(state=...)` 并发 ToolResult 事件 |
| `Agent._close_unfinished_tool_calls` | `agent/_agent.py:970` | 中断时把未完成调用补成 `INTERRUPTED` |
| `ToolResultState` | `message/_block.py:185-193` | `success` / `error` / `interrupted` / `denied` / `running` |

### 真实读数

```text
$ .venv/bin/python app/specs/issue-136-research/probe_single_agent.py
agentscope_version=2.0.10
[approve] phase1_events=9 last=RequireUserConfirmEvent
[approve] phase1_has_reply_end=False
[approve] require_tool_calls=['tc-ask']
[approve] require_reply_id_is_agent=True
[approve] parked_after_phase1=True
[approve] phase2_events=7 counts=ModelCallEndEvent=1,ModelCallStartEvent=1,Msg=1,ReplyEndEvent=1,ToolResultEndEvent=1,ToolResultStartEvent=1,ToolResultTextDeltaEvent=1
[approve] phase2_tool_result_states=['success']
[approve] phase2_reply_end=['completed']
[approve] phase2_final_text='solo final'
[approve] context_tool_result_states=['success']
[approve] parked_after_phase2=False
[approve] duplicate_decision_error=Agent is not waiting for user confirmation
[deny] phase1_events=9 last=RequireUserConfirmEvent
[deny] phase1_has_reply_end=False
[deny] require_tool_calls=['tc-ask']
[deny] require_reply_id_is_agent=True
[deny] parked_after_phase1=True
[deny] phase2_events=7 counts=ModelCallEndEvent=1,ModelCallStartEvent=1,Msg=1,ReplyEndEvent=1,ToolResultEndEvent=1,ToolResultStartEvent=1,ToolResultTextDeltaEvent=1
[deny] phase2_tool_result_states=['denied']
[deny] phase2_reply_end=['completed']
[deny] phase2_final_text='solo final'
[deny] context_tool_result_states=['denied']
[deny] parked_after_phase2=False
[deny] duplicate_decision_error=Agent is not waiting for user confirmation
[wrong-reply-id] phase2_events=7
[wrong-reply-id] reply_end=['completed']
[wrong-reply-id] final_text='solo final'
[interrupt] phase2_events=5 counts=Msg=1,ReplyEndEvent=1,ToolResultEndEvent=1,ToolResultStartEvent=1,ToolResultTextDeltaEvent=1
[interrupt] tool_result_states=['interrupted']
[interrupt] reply_end=['interrupted']
[interrupt] fallback_text='I notice the interruption. How can I help you?'
[interrupt] parked_after=False
[interrupt] context_tool_result_states=['interrupted']
[interrupt] next_reply_starts=1 next_reply_events=4
[idle-interrupt] events=0 parked=False
```

### 解释

1. **挂起**：ASK 触发一次 `RequireUserConfirmEvent` 后流自然结束（`phase1_has_reply_end=False`），`reply_id` 等于该 Agent 的 `state.reply_id`，调用处于 `ASKING`。
2. **批准**：回送 `UserConfirmResultEvent(confirmed=True)` 后，工具真正执行：`ToolResultEndEvent.state=success`，模型再被调用一次产出最终 `Msg`，`ReplyEndEvent.finished_reason=completed`。恢复流没有新的 `ReplyStartEvent`。
3. **拒绝**：真实结果是 **`ToolResultEndEvent.state=denied`**，上下文里也是 `denied`；随后模型再被调用一次，`ReplyEnd=completed`，最终消息正常产出。也就是说：
   - AgentScope 把“拒绝”建模为**第四种工具结果状态 `denied`**，而不是 `error` 或 `interrupted`；
   - 拒绝不会自动中断轮次，模型仍可继续（可以再调用别的工具或直接回答）；
   - 规格 §5.1 的“拒绝后 ERROR”是**文案偏差**，W2 与文档需要写成 `denied`（或由产品层显式映射，但不应伪造 AgentScope 事件）。
4. **错误 `reply_id`**：单 Agent 只看工具调用 id，不看 `reply_id`；传错的 id 也会恢复成功。因此**路由不能依赖 Agent**，必须由 `ApprovalBroker` 按 `reply_id` 命中库里的 `pending` 记录，并由 `TeamPipeline` 按 `reply_id` 路由成员。
5. **重复决策**：轮次已收口后再次送确认会抛 `ValueError: Agent is not waiting for user confirmation...`；API 必须先在库里判定 `pending`，不能把重复请求转给已恢复的 Agent。
6. **取消**：`UserInterruptEvent` 产出 `ToolResultEnd(INTERRUPTED)`、`ReplyEnd(INTERRUPTED)` 和一条 fallback `Msg`（“I notice the interruption…”），不进入新一轮推理；之后下一条消息能正常开始新回复（`next_reply_starts=1`），不会卡死。
7. **空闲取消**：没有挂起调用时 `UserInterruptEvent` 是 0 事件的静默 no-op。

## 2. 判据二：TeamPipeline 的挂起、路由与收口

### 源码符号

| 符号 | 位置（`agentscope==2.0.10`） | 作用 |
| --- | --- | --- |
| `TeamPipeline.reply_stream` | `pipeline/_team_pipeline.py:161` | 公开入口；docstring 明写“每位参与者完成或挂起时流结束” |
| `TeamPipeline._reply` | `pipeline/_team_pipeline.py:205` | 中断广播（`:216`）与 `reply_id` 路由（`:224-241`） |
| `_run_assignments` | `pipeline/_team_pipeline.py:284` | 成员挂起后 `break`（`:336-337`）；恢复时把成员结果转成队长的 `ExternalExecutionResultEvent` |
| `_is_parked` / `_parked_members` / `_parked_member` | `pipeline/_team_pipeline.py:378-393` | 用 `AgentState.reply_id` 定位挂起成员 |
| `ReplyStartEvent` / 其余事件 | `event/_event.py:82` / `:442` 起 | 只有 `ReplyStartEvent` 带 `name`；所有 HITL 事件只有 `reply_id` |

### 真实读数

```text
$ .venv/bin/python app/specs/issue-136-research/probe_team_hitl.py
agentscope_version=2.0.10
[approve] phase1_events=17 last=RequireUserConfirmEvent
[approve] phase1_require_reply_id_is_member=True
[approve] phase1_require_reply_id_is_leader=False
[approve] phase1_parked leader=True member=True
[approve] phase1_reply_ends=[] assignments=[0]
[approve] phase1_has_reply_start=True
[approve] phase2_events=11 counts=ModelCallEndEvent=2,ModelCallStartEvent=2,ReplyEndEvent=2,ToolResultEndEvent=2,ToolResultStartEvent=1,ToolResultTextDeltaEvent=2
[approve] phase2_reply_ends=['completed', 'completed']
[approve] phase2_reply_starts=0
[approve] phase2_tool_states=['success', 'success']
[approve] assignments_after=[0, 1]
[approve] leader_tool_results=['TeamAssign:success']
[approve] leader_final_text='leader final'
[approve] leader_parked_after=False
[approve] member_parked_after=False
[deny] phase1_events=17 last=RequireUserConfirmEvent
[deny] phase1_require_reply_id_is_member=True
[deny] phase1_require_reply_id_is_leader=False
[deny] phase1_parked leader=True member=True
[deny] phase1_reply_ends=[] assignments=[0]
[deny] phase1_has_reply_start=True
[deny] phase2_events=11 counts=ModelCallEndEvent=2,ModelCallStartEvent=2,ReplyEndEvent=2,ToolResultEndEvent=2,ToolResultStartEvent=1,ToolResultTextDeltaEvent=2
[deny] phase2_reply_ends=['completed', 'completed']
[deny] phase2_reply_starts=0
[deny] phase2_tool_states=['denied', 'success']
[deny] assignments_after=[0, 1]
[deny] leader_tool_results=['TeamAssign:success']
[deny] leader_final_text='leader final'
[deny] leader_parked_after=False
[deny] member_parked_after=False
[two-members] phase1_events=30 requires=2 members=['m-alpha', 'm-beta']
[two-members] phase1_reply_ends=[] assignments=[0]
[two-members] both_parked=True
[two-members] after_first_events=8 reply_ends=['completed']
[two-members] after_first_assignments=[0, 1]
[two-members] after_first_leader_parked=True
[two-members] after_first_leader_results=['TeamAssign:success']
[two-members] remaining_parked=['m-beta']
[two-members] after_second_events=11 reply_ends=['completed', 'completed']
[two-members] after_second_assignments=[0, 1, 1]
[two-members] leader_final_text='leader final'
[bad-reply-id:leader-reply-id] ValueError=Agent is not waiting for user confirmation, but received UserConfirmResultEvent: ...
[bad-reply-id:unknown] ValueError=No participant is waiting on the reply 'bogus'.
[bad-reply-id] member_still_parked=m-alpha
[interrupt] events=7 counts=ReplyEndEvent=2,ToolResultEndEvent=2,ToolResultStartEvent=1,ToolResultTextDeltaEvent=2
[interrupt] reply_ends=['interrupted', 'interrupted']
[interrupt] tool_states=['interrupted', 'interrupted']
[interrupt] leader_tool_results=['TeamAssign:interrupted']
[interrupt] leader_parked_after=False
[interrupt] member_parked_after=False
[interrupt] assignments_after=[0]
[timeout] waited_seconds=0.50
[timeout] phase1_stream_already_ended=RequireUserConfirmEvent
[timeout] leader_parked=True
[timeout] member_parked=True
[timeout] builtin_timeout_event=False (no timer runs while parked)
[timeout] cleanup_reply_ends=['interrupted', 'interrupted'] cleanup_tool_states=['interrupted', 'interrupted']
```

### 解释

1. **成员挂起时流结束**：`phase1_reply_ends=[]`，最后一个事件是 `RequireUserConfirmEvent`；队长与成员同时处于挂起状态（队长停在 `TeamAssign` 的外部执行上），本轮没有完成任何 `ReplyEnd`。
2. **按 `reply_id` 路由回成员**：`RequireUserConfirmEvent.reply_id` 等于**成员**的 `state.reply_id`，不等于队长；批准后 `_parked_member()` 命中 `m-alpha`，恢复流 11 个事件、**0 个新 `ReplyStartEvent`**，成员与队长各自 `ReplyEnd=completed`，`assignments` 从 `[0]` 变为 `[0,1]`。
3. **队长继续/收口**：成员最终回复被包装成 `TeamAssign:success` 的 `ToolResultBlock` 送回队长；队长再被调用一次并产出 `leader final`。
4. **拒绝**：成员的内部工具结果是 `denied`，但成员仍完成并产出最终回复，因此队长看到的 `TeamAssign` 状态是 **`success`**（`[deny] phase2_tool_states=['denied', 'success']`）。**队长无法从 `TeamAssign` 结果状态区分成员内部是否有工具被拒绝**；如产品需要，必须由成员回复正文或额外事件表达。
5. **双成员**：两人同时挂起时先批一个，只运行那一个成员（`assignments=[0,1]`），队长仍然挂起，另一个成员仍在 `_parked_members()` 中；再批第二个后队长才收口（`[0,1,1]`，两个 `ReplyEnd`）。**一次 HTTP 决策只影响一个 `reply_id`**，不能把多个成员的审批合并成一个事件。
6. **错误路由**：
   - 传队长的 `reply_id`：`TeamPipeline` 把它当成队长的输入，队长抛 `ValueError: Agent is not waiting for user confirmation`；
   - 传未知 `reply_id`：`_parked_member()` 抛 `ValueError: No participant is waiting on the reply 'bogus'.`；
   - 两种情况后成员仍保持挂起，未被破坏。
7. **取消**：`UserInterruptEvent` 会**广播**给所有挂起成员，再中断队长；本次读数 7 个事件、两个 `ReplyEnd=interrupted`、两个工具结果 `interrupted`，队长 `TeamAssign` 结果也是 `interrupted`。注意：
   - 事件里的 `reply_id` **不参与路由**（探针故意传了不存在的 id，仍然全员中断）；
   - 成员尚未产出最终回复，所以 `assignments` 保持 `[0]`，不会出现“中断也计一次成功分派”。
8. **等待没有内建超时**：挂起后第一个流已经结束，再等 0.5 秒状态完全不变，没有任何计时器或超时事件；清理仍然只能靠 `UserInterruptEvent`。

## 3. 判据三：超时、取消与 `ApprovalBroker` 设计

### 3.1 `ApprovalBroker` 原型读数

`probe_broker.py` 里的 broker 是**研究原型**（不是产品代码），用真实 Agent 的 HITL 协议验证等待/超时/部分决策语义：

```text
$ .venv/bin/python app/specs/issue-136-research/probe_broker.py
agentscope_version=2.0.10
[approve] state=approved
[approve] reply_end=completed tool_results=['success']
[deny] state=denied
[deny] reply_end=completed tool_results=['denied']
[partial] require_events=2 reply_ids=1
[partial] pending_tool_calls=['tc-a', 'tc-b']
[partial] state=pending first_resume_events=3 require_again=0
[partial] first_resume_reply_end=none still_awaiting=True
[partial] replay_finished_call_error=Received UserConfirmResultEvent with tool call ids {'tc-a'} that are not waiting for confirmation.
[partial] state=approved second_resume_reply_end=completed
[partial] tool_results=['success', 'success']
[timeout] elapsed_seconds=0.30 decision_event=None state=expired
[timeout] late_resolve_state=expired
[timeout] interrupt_reply_end=interrupted tool_results=['interrupted']
[timeout] parked_after=False
[cancel] decision_event=None state=cancelled
[cancel] interrupt_reply_end=interrupted parked_after=False
```

关键事实：

- **多工具调用共享一个 `reply_id`**：并发批次里两个不同 ASK 工具产生两个 `RequireUserConfirmEvent`（`require_events=2`），但 `reply_id` 只有一个。broker 必须按 `reply_id` 聚合 `tool_call_id`。
- **部分决策可行**：只确认 `tc-a` 后，Agent 执行 `tc-a`，然后再次挂起；**不会重新发出 `tc-b` 的 `RequireUserConfirmEvent`**（`require_again=0`）。UI/broker 必须保留第一次事件里的待办卡片，不能等“重发”。
- **只能送新决策**：把已完成的 `tc-a` 再放进下一次 `UserConfirmResultEvent` 会抛 `ValueError: ... tool call ids {'tc-a'} that are not waiting for confirmation.`。
- **超时没有协议内建机制**：`wait_for` 到 0.30 秒返回 `None`，记录转 `expired`；此时 Agent 仍挂起，唯一的收口方式是 `UserInterruptEvent`（工具结果转 `INTERRUPTED`，`ReplyEnd=interrupted`）。
- **迟到决策被拒绝**：`state=expired` 后 `resolve()` 返回 `expired`，不得再唤醒已超时的轮次。
- **用户取消**：与超时共用同一条收口路径（`UserInterruptEvent`）；broker 只需先把记录标成终态，防止并发决策。

### 3.2 建议的 `ApprovalBroker` 设计（W2 落点）

**记录与状态**

- 键：`reply_id`（挂起参与者的 `Agent.state.reply_id`）。团队模式下它是**成员**的 id，不是队长的。
- 持久化：`approval_request(reply_id, target_id, member_id, team_run_id?, tool_calls, state, decisions, requested_at, resolved_at)`，状态只用 schema 允许的 `pending / approved / denied / expired`。
- 内存：`reply_id → {tool_calls: {id: block}, decided: {id: bool}, deadline, queue, state}`；`register()` 幂等，后续同一个 `reply_id` 的新 `RequireUserConfirmEvent` 只**合并**新 `tool_call_id`，不重置 deadline。

**注册（轮次 runner 消费事件时）**

1. 收到 `RequireUserConfirmEvent`：从轮次上下文补 `target_id` / `member_id` / `team_run_id`（事件本身只有 `reply_id` 与 `tool_calls`）。
2. 事务内插入/更新 `approval_request`（`pending`），记录完整工具调用块与建议规则。
3. 发 SSE `approval_required`（`reply_id`、`member_id`、当前 pending 的 `tool_calls`）。
4. 注册到 broker；等待决策。

**等待**

```text
deadline = requested_at + 600s          # 默认 10 分钟，从第一次 pending 起算
event    = await asyncio.wait_for(queue.get(), remaining)
```

- 每条记录的 deadline 固定一次；后续同 `reply_id` 的新调用不延长（避免“多问一次多送 10 分钟”）。
- 等待期间**不持有数据库事务**，也不占用模型调用；实测挂起状态可以无限等待。
- 同一个回复可能需要多轮等待（部分决策）：runner 在恢复后检查 `state.has_awaiting_tool_calls()`；仍挂起就再次 `wait()`，直到全部决定或超时。

**`POST /api/approvals/{reply_id}`**

- body 按规格：`{"decisions":[{"tool_call_id", "confirmed", "rules"?}]}`。
- 只接受库中 `state='pending'` 的记录；先做状态判定，再唤醒，避免重复决策打穿 Agent。
- 校验每个 `tool_call_id` 属于该 `reply_id` 的待决集合；未知/已完成 id → 400；状态冲突 → 409。
- **不要信任客户端传来的工具块内容**：用库里保存的 `ToolCallBlock` 重建 `ConfirmResult`，只接受 `tool_call_id` + `confirmed` + `rules`。
- 决策可以部分提交：把新决策写入 `decisions` JSON；只有待决集合全部有决策时才把 `state` 转成 `approved`（全 true）或 `denied`（任一 false）。
- 每次提交只把**本次新决策**打包成一个 `UserConfirmResultEvent` 入队；runner 恢复 Agent 后若仍挂起，再等待。已完成的 id 绝不重发（实测会抛错）。
- 幂等：同一批决策重试返回当前状态，不入队第二次；冲突决策（先 true 后 false）→ 409。

**超时（默认 600s）**

1. broker 把记录标 `expired`、写 `resolved_at`，发 SSE `approval_resolved(state=expired)`。
2. runner 向挂起对象送 `UserInterruptEvent(reply_id=<该参与者的 reply_id>)`：
   - 单 Agent：工具结果 `INTERRUPTED`，`ReplyEnd=interrupted`，fallback 消息；
   - `TeamPipeline`：会中断**所有**挂起成员与队长（实测），因此超时是**轮次级**失败。
3. 轮次结果落 `round_outcome.status='failed'`（reason 例如 `approval-timeout`）；团队记录 `team_run.status='failed'`。
4. 不要把超时实现成 `UserConfirmResultEvent(confirmed=False)`：实测拒绝会让模型继续推理并可能再发起工具调用/审批，而规格要求超时 `failed`。

**用户取消**

- 人类显式取消 = 把待决调用全部记为拒绝（`state='denied'`，保留逐条 `decisions`），然后同样送 `UserInterruptEvent` 收口；轮次 `failed`，reason `approval-cancelled`。
- 只写 `denied` 而不中断会让 Agent 继续（实测 `denied` 后 `ReplyEnd=completed`），不符合“取消”语义。

**进程重启**

- 启动时 `UPDATE approval_request SET state='expired', resolved_at=... WHERE state='pending'`（D19）。
- 内存 broker 为空，挂起的 Agent 对象已随进程消失；不存在恢复路径。对应轮次在下一次可观测的收口点记 `failed`；若进程被硬杀，至少 DB 里不会留下永远 `pending` 的记录。

**并发与线程**

- broker 绑定事件循环；Agent 的 HITL 状态在内存，不能跨 loop/线程恢复。
- 如果 HTTP 层是同步线程池，提交决策必须 `loop.call_soon_threadsafe(...)` 回到 runner 的 loop，不能直接改 `asyncio.Queue/Future`。

## 4. 对实现票的硬约束（研究结论）

1. **E3 是前置**：当前 `AgentScopeAgentSeam.run_round` 调 `instance.reply(...)`，拿不到 `RequireUserConfirmEvent`；W2 必须消费 `run_round_stream` 的事件流，否则审批无法被发现。
2. **Agent 实例必须跨暂停存活**：挂起状态在 `AgentState`（`ASKING` 工具调用 + `reply_id`），没有可序列化恢复的公开 API。W2 的 runner 要在同一个事件循环里保留 Agent/TeamPipeline 对象，直到批准/拒绝/超时；不能每轮重建。
3. **现有 120s 轮次超时会杀死审批**：`run_round` 用 `asyncio.wait_for(..., timeout_seconds=120)`。默认审批等待 600s，因此必须把“模型调用超时”和“审批等待”拆开，或把整体 deadline 扩展到 `model_timeout + approval_timeout`（并明确 sum 语义）。否则 120s 时 Agent 被取消，走 `INTERRUPTED` 路径，broker 永远等不到决策。
4. **路由只用 `reply_id`**：
   - 单 Agent 不校验 `reply_id`，HTTP 层必须用库里的 `pending` 记录把门；
   - `TeamPipeline` 按成员 `reply_id` 路由；未知/队长 id 分别抛不同 `ValueError`（见 §2）；
   - `UserInterruptEvent.reply_id` 在 `TeamPipeline` 里不参与路由，取消是全员广播；单成员超时因此会中止整轮。这是一个已知限制，若产品要“只超时一个成员”，AgentScope 2.0.10 没有对应 API。
5. **事件归属要跨 `reply_stream()` 调用**：恢复流没有新的 `ReplyStartEvent`（单 Agent、Team 均为 0），`reply_id → member` 表必须从上一阶段保留，或用 `_parked_members()` 的 `state.reply_id` 预填（与 R1 结论一致）。
6. **工具结果状态词表**：拒绝是 `denied`，不是 `error`。W2/README/SSE 若展示“拒绝”，应使用 `denied` 或映射后的产品词，但不要伪造 `error`；规格 §5.1 的“拒绝后 ERROR”需要同步修正。
7. **`TeamAssign` 结果不暴露成员内部拒绝**：拒绝后成员仍可完成，队长拿到 `TeamAssign:success`（实测）。若队长需要知道“工具被拒绝”，必须由成员回复内容或额外运行记录表达，不能看 `TeamAssign` 状态。
8. **部分决策不重发**：一次 `RequireUserConfirmEvent` 可能只覆盖部分调用；恢复后剩余的 ASK 调用不会重新产生事件（实测 `require_again=0`）。审批 UI 必须以 `approval_request.tool_calls` 为待办正本，而不是“等下一次事件”。

## 5. 残余与风险

- broker 原型的 10 分钟默认值只用 0.3s 参数验证了逻辑，没有真实等待 600s；生产默认值来自规格 §3.6，不是实测值。
- 数据库 `approval_request`（schema 17）在本票分支尚未存在（当前 `SCHEMA_VERSION=15`），§3.2 的持久化设计未经过迁移/重启演练；由 E2/M1/W2 覆盖。
- 跨重启恢复明确不在首版（D19）；本报告只覆盖“启动时把 pending 标 expired”。
- `TeamPipeline` 与 `AgentEvent` 是实验性接口；`_parked_member` / `_run_assignments` 是私有方法，升级 2.0.10 之外的小版本必须重跑本票三个探针。
- 并发 ASK 的同名工具会被批次去重（第二次 ASK 暂不浮现，等第一次决策后再以新事件浮现）；不同名工具才同时浮现两个事件。broker 必须同时支持这两种时序（注册函数幂等合并，等待循环可重入）。
- 本票未验证真实 provider 的 `tool_choice`/流式差异；HITL 只依赖工具权限与事件协议，不依赖模型厂商。
- 未改 `app/alibot/**`、`app/tests/**`、`app/pyproject.toml`、`.github/**`；新增脚本通过 `ruff check` 与 `ruff format --check`（见提交前门禁记录）。

## 6. 文件清单

- 本报告：`app/specs/issue-136-research.md`
- 探针：`app/specs/issue-136-research/probe_single_agent.py`
- 探针：`app/specs/issue-136-research/probe_team_hitl.py`
- 探针：`app/specs/issue-136-research/probe_broker.py`
- 复用的共享夹具：`app/specs/issue-132-research/_harness.py`（本票未修改）
