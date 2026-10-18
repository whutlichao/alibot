# TeamAssign 是运行期分派记录，不是任务行的改派：任务五态只由 owner / 认领 / 既有动作推进

用户于 2026-10-18 人定（D5(a) / D23）：team 模式要覆盖“任务指派映射”，但 team 模式只作用于 **channel 型
target**；该频道下的**任务线程仍按既有任务语义**（受派者/认领）唤醒，`TeamAssign` 在任务线程里只作
**运行期分派记录**，不改任务五态。

## 一、两个对象，各自的状态机

- **任务 (Task)**：用户可观察的工作对象，挂在 `kind='task'` 的 target 上，有五个状态
  `todo` / `in_progress` / `in_review` / `done` / `closed`，有看板、任务线程、assignee、`claimed_at`、
  开世代与预算。任务的变更史在任务 target 的消息流里（ADR-0006）。
- **TeamAssign**：队长在一个轮次内把一段 prompt 交给某个成员执行，成员在独立上下文里跑，最终回复作为
  `ToolResult` 回队长。它没有 target、没有看板、没有未读、没有五态；它的记录是 `team_assignment`
  （`team_run_id`、`member_id`、`prompt`、`state`、`jump_consumed`、`reply_excerpt`、起止时间）。

两者可以**关联**，但不能**互相改写**：`team_assignment.task_id` 只在当前 target 是 task 时写入，作为
“这次分派发生在这张任务的线程里”的引用；它不更新 `task.assignee_member_id`、不改 `task.status`、不写
`system` 任务变更记录。

## 二、为什么不做“TeamAssign 自动改任务”

- **一次队长轮可以分派给多个成员**：如果每次 `TeamAssign` 都把 `task.assignee_member_id` 改成该成员，
  最后一个分派会覆盖前面的，任务行无法表达“多个成员同时在干”。看板会显示一个假的单一受派者。
- **五态的 actor 纪律会被绕过**：`in_review → done` 只能由指派者之外的成员触发，owner 恒可（`CONTEXT.md`
  **任务状态**）。如果 `TeamAssign` 能改状态，队长就获得了一条绕过 actor 校验的路径；ADR-0016 已经明写
  “认领推状态复用迁移表、不复用 actor 校验”，那是由编排发起的一条受控路径，不能复制给运行期分派。
- **预算与世代会纠缠**：任务行改派在 ADR-0005 里是“唤醒的载体”，会开世代/重置预算。TeamAssign 已经按
  ADR-0019 计跳；如果再触发任务改派，就会同一次动作既计跳又开世代，预算口径分裂。
- **历史只有一个落点**：任务变更史在任务 target 的消息流；TeamAssign 的历史在 `team_assignment`。
  把两者混在一起会让“任务变过什么”出现第二条历史。

## 三、任务线程里的 TeamAssign

D23 明确：team 模式不覆盖 task target。任务线程仍由既有 `notify_task_assignment` / 认领路径唤醒受派者；
如果受派者是队长，队长可以调用 `TeamAssign` 把工作分给其他成员。此时：

- `team_assignment.task_id` = 当前任务 id；`target_id` = 任务 target；
- 成员输出只走 SSE 与 `team_assignment`，不落任务线程消息（它是 pipeline 成员输出，D2）；
- 队长的最终回复按既有任务轮次落为任务线程的一条消息；
- 任务五态、assignee、`claimed_at`、预算世代全部不变；任务线程的消息流不出现成员分派记录。

如果用户希望“这次分派变成任务”，那是显式的人类动作（新建/改派任务），不在本 ADR 内自动发生。

## 四、被否方案与代价

- **TeamAssign 自动新建任务**：一次队长轮可能生成 N 张任务，看板被运行期分派灌满；而且任务创建是 owner
  的动作（ADR-0005），不该由模型在运行中批量产生。
- **TeamAssign 自动改 assignee**：覆盖多成员并发，且绕过 actor 校验。
- **TeamAssign 写任务 `system` 消息**：任务线程会出现“分派记录”与“任务变更”两种同形记录，破坏 ADR-0006
  的“当前态在行上、变更史在流里”的单一历史。
- **把 `team_assignment` 塞进 `round_outcome`**：`round_outcome` 是成员轮次结论的六态封闭词表；成员在 team
  里没有频道消息轮次，塞进去会污染数据契约（ADR-0020）。

**代价**：看板不会自动反映“队长把活分给了谁”，用户要看运行面板；`team_assignment` 需要按 target/task 的
查询与保留策略；任务线程与运行记录是两个视图，需要在 UI 里并列呈现，不能互相替代。
