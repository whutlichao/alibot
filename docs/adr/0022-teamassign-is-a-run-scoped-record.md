# team 模式覆盖任务线程：只唤醒队长，五态仍由 owner / 队长工具维护，TeamAssign 只记运行期分派

用户于 2026-10-18 人定（Q11 = **B**，D23）：team 模式**覆盖该频道的 channel 型 target 与全部 task target**；
**任务线程也只唤醒队长**。既有任务五态保留（owner 与成员的结构化工具维护；队长可分派），但**认领与受派者唤醒
在 team 模式任务线程中不再是唤醒路径**；`auto_claim` 已按 D16 全关；`TeamAssign` 在任务线程可用并记入运行期分派。

本 ADR 取代 ADR-0022 的旧版本（旧版本按“team 不覆盖 task target、任务线程沿用既有受派者/认领唤醒”写的）。
ADR-0005 / ADR-0010 / ADR-0016 的正文**一字不改**；覆盖与保留关系只在这里、`CONTEXT.md` 的**任务** /
**任务状态** / **认领** / **唤醒**词条、以及 `app/specs/agentscope-2-alignment.md` 里声明。

## 决策记录

- **Context**：team 模式原先只覆盖 channel target；Q11=B 把它扩到该频道的全部 task target。任务 target 现有三条唤醒路径（通用全成员、受派者、认领），任务五态另有 actor/自审规则；需要在不改写任务行的前提下，把任务线程的注意力收口到队长。
- **Decision**：team 任务线程只唤醒队长；owner 改派/Agent 转派改为唤醒队长，不唤醒新受派者；`claim_new_task()` 在 team 频道不再认领；任务五态仍由 owner HTTP 与队长轮次的结构化任务工具维护；`TeamAssign` 只写 `team_assignment`，不改任务行；`in_review → done` 保留自审禁令。
- **Consequences**：非队长受派者不再收到任务消息，需队长经 `TeamAssign` 转达；队长是受派者时只能由 owner 批准 `in_review → done`；task target 的穿透关闭；任务看板与任务线程消息流保留，但协调权集中到队长。

## 一、作用范围：channel + 全部 task target

`channel.mode='team'` 是该频道的**拓扑开关**，作用于：

- 该频道自己的 channel 型 target；
- 该频道下的**全部 task 型 target**（通过 `task.channel_id` 找到所属频道；`task` 表**不新增 mode 列**）。

DM 恒为 autonomous；不属于任何 team 频道的 task 不受影响。

## 二、任务线程里谁唤醒

| 触发 | team 任务线程的唤醒结果 |
| --- | --- |
| human 消息 | **只唤醒队长** |
| agent 消息（队长是作者） | **零唤醒** |
| agent 消息（非队长作者，含历史消息） | **只唤醒队长**（若队长不是作者） |
| owner 改派（`notify_task_assignment`） | **只唤醒队长**；不唤醒新受派者 |
| Agent 转派（`notify_task_reassignment`） | **只唤醒队长**；actor 是队长时零唤醒 |
| 创建任务 / 认领 | **零唤醒**；`claim_new_task()` 在 team 频道直接返回 0 |
| 其它 `system` 记录 | 零唤醒 |
| 穿透（@ 未加入成员） | **不适用**：team task target 只唤醒队长，不做穿透 |

这与 channel target 的差别是**穿透**：channel target 仍按 D17 做一次性送达；task target 在 team 模式下不做。
`TeamAssign` 的成员不是被 `notify` 唤醒的，而是队长在 pipeline 内运行；这不属于“任务线程消息唤醒”。

## 三、谁维护任务五态

任务五态（`todo` / `in_progress` / `in_review` / `done` / `closed`）与 `task` 行**保留**，维护路径是：

- **owner**：既有 HTTP 端点（创建任务、改状态、改标题、改指派人）不变；owner 改派仍按 ADR-0005 写任务行、
  追加 `system` 记录并开该 task target 的世代。
- **队长轮次**：team 任务线程里只有队长会跑任务轮次，因此只有队长能通过成员面结构化工具提交
  `SetTaskStatusAction` / `TransferTaskAction`；这些动作仍走 `update_task_status` / `update_task_assignee`
  的迁移表与 actor 校验。
- **其它成员**：通过 `TeamAssign` 在自己的上下文里执行队长分派的 prompt；它们**不获得任务上下文**，
  也不能用正文或 Plan/SOP 代替任务状态迁移。
- **`TeamAssign`**：只写 `team_assignment`（`task_id` 在 task target 上非空），**不改** `task.status`、
  `assignee_member_id`、`claimed_at`，也不写任务变更 `system` 消息。

## 四、认领与受派者唤醒为什么在 team 任务线程不生效

- `auto_claim` 已按 D16 全关；`claim_new_task()` 在 team 频道不再认领。认领的“开世代 / 推状态 / 唤醒认领者”
  三件事在 team 频道都不发生。
- owner 改派 / Agent 转派仍改任务行（ADR-0005），但**唤醒对象改为队长**；新受派者不会被唤醒。这样做的原因是
  Q11=B 的“任务线程也只唤醒队长”：如果继续唤醒新受派者，任务线程就会重新出现 pipeline 外的自主轮次。
- `claimed_at` 列保留；存量已认领任务不因切到 team 模式而回填或改写。team 模式只改变**之后的唤醒路径**，
  不改变历史行。

## 五、`in_review → done` 的审批者规则

既有规则**保留**：`update_task_status` 拒绝 `actor == assignee` 的 `in_review → done`；owner 恒可。
在 team 任务线程里，能触发状态迁移的 agent 只有队长（因为只有队长被唤醒/跑任务轮次）：

- **队长不是受派者**：队长可以批准 `in_review → done`（它是非受派 agent）。
- **队长是受派者**：队长不能批准自己的产出；只能由 **owner** 批准。owner 也可以先改派给其它成员，
  但在 team 模式下新受派者不会被任务消息唤醒，实际路径仍是 owner 批准或 owner 经队长协调。
- **其它成员**：没有任务轮次，不能直接触发状态迁移；它们的工作通过 `TeamAssign` 回到队长，由队长汇总后
  由队长或 owner 推进状态。

这条规则不新增角色、不放宽自审禁令；它只是把“谁有机会触发”收窄到队长与 owner。

## 六、与 ADR-0005 / ADR-0010 / ADR-0016 的覆盖与保留

| 既有 ADR | 覆盖的部分 | 保留的部分 |
| --- | --- | --- |
| **ADR-0005**（派任务是人的发言） | team task target 的 owner 改派 / Agent 转派仍写任务行、开世代，但**唤醒对象改为队长**；`TeamAssign` 不是任务行改派 | “唤醒的载体是指派动作”“owner 改派 = 人类世代”“Agent 转派 = 1 跳”“改状态/标题不唤醒”全部保留；`TeamAssign` 另按 ADR-0019 计跳 |
| **ADR-0010**（认领是编排发起的指派） | team 频道不认领；`claim_new_task()` 不运行 | 认领的“每任务至多一次 / 不是跳 / 开世代 / 开工动作”在 **autonomous** 频道不变；`claimed_at` 列保留 |
| **ADR-0016**（认领即开工） | team 频道不产生认领轮，因此“认领者必须回应”不发生 | autonomous 频道的认领即开工、两条 `system` 记录、状态推进与必答纪律不变 |
| **ADR-0006**（任务态在行上、变更史在流里） | 无覆盖 | 任务行仍可变；owner/队长的每次任务变更仍追加 `system` 消息；`TeamAssign` 不写任务变更消息 |

## 七、被否方案与代价

- **让任务线程继续走既有受派者/认领唤醒（旧推荐 A）**：直接违反 Q11=B；任务线程会重新出现 pipeline 外的
  自主轮次，与“只唤醒队长”冲突。
- **TeamAssign 自动改 assignee / 状态**：一次队长轮可多次分派，单一 assignee 无法表达多成员并发；还会绕过
  `update_task_status` 的 actor 校验与自审禁令。
- **team 任务线程保留穿透**：Q11=B 明确“任务线程也只唤醒队长”；穿透会引入 pipeline 外的成员轮次。
- **认领继续在 team 频道运行**：会与“只唤醒队长”和 D16 的 `auto_claim` 全关冲突，并可能重置 task 预算世代。
- **给 task 表加 mode 列**：同一个 topology 由所属频道决定，`task.channel_id -> channel.mode` 已经是唯一事实；
  加列会制造第二正本。

**代价**：非队长受派者不再收到任务消息，必须由队长经 `TeamAssign` 转达；队长是受派者时 `in_review → done`
只能由 owner 批准；task target 不穿透，@ 未加入成员在任务线程无效；任务看板不自动反映运行期分派，用户要看
运行面板；`team_assignment` 需要按 task target 查询与保留。
