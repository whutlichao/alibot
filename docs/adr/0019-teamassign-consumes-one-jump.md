# 每次 TeamAssign 计一跳：预算裁决落在 pipeline 的分派执行点，耗尽时给队长错误 ToolResult

用户于 2026-10-18 人定（D3）：在 team 模式下，**每次 `TeamAssign` 计 1 跳**，推进 `target_budget.used`；
预算耗尽时给队长一个**错误 `ToolResult`**，**不运行该成员**。**每次分派前的检查与扣减必须原子。**
队长最终回复不再唤醒成员，因此不与现有“跳”重复计数。

## 决策记录

- **Context**：现行“跳”只由 Agent 消息投递唤醒计数；`TeamAssign` 是 external tool，`on_acting` 看不到其执行，`_spend_jump` 也看不到。
- **Decision**：每次 `TeamAssign` 计 1 跳；在 `_run_assignments` 前用单事务 `UPDATE ... WHERE used < limit` 原子检查并扣减；耗尽返回错误 `ToolResult`、不运行成员；失败不退款；队长最终回复不计跳。
- **Consequences**：team 频道的一次人类世代最多 3/5/10 次分派；新增 `team_assignment` 运行记录；依赖实验性私有方法 `_run_assignments`，升级需重跑 R1。

## 一、为什么不能复用现有的 `_spend_jump`

现行 **跳** 的定义（`CONTEXT.md` / ADR-0002）是“一条 **Agent 消息**投递了至少一个唤醒给其它 Agent”。
team 模式的成员不是被频道消息唤醒的：队长调用 `TeamAssign`，`TeamPipeline` 在 `_run_assignments()` 里
直接运行成员，并把最终回复作为 `ToolResult` 交回队长。这条路径**不经过** `WakeOrchestrator.notify()`，
也不产生一条成员频道消息，所以 `_spend_jump`（`app/alibot/orchestration.py:388`）看不到它。

AgentScope 2.0.10 的 `MiddlewareBase.on_acting()` 也帮不上：它只包住 `toolkit.call_tool`，
**权限检查、输入校验与上下文写入都在 hook 外面**（源码 `middleware/_base.py:124-139` 明写）。
用它做预算会产生“先检查后执行”的窗口，且看不到 external tool 的真实执行。因此裁决点必须放在
`TeamPipeline._run_assignments()`（源码 `pipeline/_team_pipeline.py:284`）之前/之内。

## 二、决定：覆写 `_run_assignments`，先逐条原子裁决，再并发运行

`TeamPipeline` 是实验性接口（官方团队流水线文档明写“流水线模块处于实验阶段”），但 `_run_assignments`
是当前唯一在“成员真正运行前”能看到全部 `ToolCallBlock` 的位置。我们在 `team.py` 里继承它：

```text
BudgetedTeamPipeline._run_assignments(tool_calls, results, resumed=None)
  1. for call in tool_calls（保持调用顺序）
       member_id = name_to_member[parse(call)["member"]]
       decision  = db.try_consume_jump(target_id)   # 单事务：UPDATE ... WHERE used < limit
       if not decision.ok:
           results[call.id] = ERROR ToolResult("预算耗尽：本次分派未运行成员")
           team_assignment(state="budget-exhausted", jump_consumed=0)
       else:
           authorized[call.id] = (member_id, decision)
           team_assignment(state="started", jump_consumed=1)
  2. 只把 authorized 的 call 按成员分组并发运行（沿用基类语义）
  3. 结果按 tool_calls 原始顺序合并，队长看到的顺序稳定
```

`try_consume_jump(target_id)` 是新增的 DB 原语：在 `BEGIN IMMEDIATE` 事务里解析当前世代的生效上界
（`target_budget.applied_limit`，缺省用内置默认值），执行
`UPDATE target_budget SET used = used + 1 WHERE target_id = ? AND used < ?`；受影响行数为 0 ⇒ 耗尽。
检查与扣减在**同一个事务/同一条 UPDATE**里，所以并发分派不可能同时读到“还剩 1”而各扣一次。
这与现行 `_spend_jump` 的“先读后写 + 编排锁”不是同一条实现，但**预算正本仍是 `target_budget`**，
`used` 仍然是跳数的唯一权威。

## 三、耗尽、失败与不退款

- **耗尽**：给队长 `ToolResultBlock(id=call.id, name="TeamAssign", state=ERROR, output=[TextBlock(...)])`；
  不运行成员；`team_assignment.state="budget-exhausted"`、`jump_consumed=0`；`target_budget.used` 不动。
  队长可以继续推理，自己回答或减少分派。
- **成员失败/中断**：跳已经消耗，**不退款**。`team_assignment.state` 记 `failed` / `interrupted`，
  `jump_consumed=1`。预算是“分派发生过”的会计，不是“成员成功”的退款单。
- **队长最终回复**：不扣跳，也不唤醒成员（D2）。team 模式下队长的 `round_outcome.consumed_budget=0`；
  它由人类消息或任务指派唤醒，不由跳送来。
- **与现行 `notify` 的关系**：team 频道的人类消息唤醒队长不扣跳（唤醒载体是 human 消息）；队长最终消息
  `notify` 返回 0；穿透回复在 team 频道也不唤醒任何人（D17）。因此同一个 `target_budget.used` 不会
  因为“队长最终消息”再 +1。

## 四、与 ADR-0002 / ADR-0005 / ADR-0010 / ADR-0016 的关系

- **ADR-0002（≤3 跳）**：上界仍然是 3（或频道配置的 3/5/10），但 team 频道的一次人类世代内，**可发生的
  TeamAssign 次数**受这个上界约束。一次队长轮最多 3/5/10 次分派；耗尽后队长只能自己总结。原文不改。
- **ADR-0005（派任务是人的发言）**：owner 改指派仍是一次人类世代；Agent 转派仍是跳。team 的 `TeamAssign`
  是**运行期分派**，不是任务行改派；它计跳，但不改 `task.assignee_member_id`（ADR-0022）。
- **ADR-0010 / ADR-0016（认领）**：team 模式关闭该频道全部成员的 `auto_claim`（D16），所以认领不会与
  TeamAssign 抢同一张任务；task 型 target 仍按既有认领语义（D23），但 team 模式不覆盖 task。
- **“跳”的词条**：本 ADR 扩展了跳的**载体**（从“Agent 消息投递”扩到“team 轮次内的 TeamAssign”），
  但**没有**改变“一次分派计 1、不按成员数重复计”的会计口径。`CONTEXT.md` 的**跳**词条需要补一句
  “team 模式下 TeamAssign 也是一次跳”，但不改写 autonomous 的定义。

## 五、被否方案与代价

- **用 `on_acting` 中间件扣预算**：看不到 external execution，且权限/校验在 hook 外，存在检查-执行窗口。
- **在 `TeamPipeline` 外面按事件扣预算**：事件是异步流，扣减时成员可能已经跑了；无法做到“耗尽不运行”。
- **先运行成员、事后结算**：无法构造性保证上界，违反 ADR-0002 的“零投递/不运行”纪律。
- **成员失败退款**：把预算变成“成功次数”，与“跳是投递事实”的定义冲突，也让“耗尽”可被失败重试绕过。
- **把 TeamAssign 记进 `round_outcome`**：`round_outcome` 是成员轮次结论的六态封闭词表，成员在 team 里没有
  频道消息轮次；塞进去会污染数据契约。运行记录用 `team_assignment`（ADR-0020）。

**代价**：team 模式的预算上限直接限制“队长能分派几次”，3 跳可能很快耗尽；`team_assignment` 需要额外的
表和清理；`_run_assignments` 是实验性私有方法，升级 2.0.10 之外的小版本时必须重跑拦截点研究（R1）。
