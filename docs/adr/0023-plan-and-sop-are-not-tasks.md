# Plan 与 SOP 各有自己的状态机：它们不是任务五态，也不进看板、未读、搜索或预算

用户于 2026-10-18 人定（D5(b) / D13）；D20 是第二轮 ask 的推荐默认（待确认）：Plan（`TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate`）与
SOP（`SOPEngine` / `SOPStep`）都进本轮票图。Plan 是**运行期工作表**，与既有「任务」五态严格分离；SOP 是
**owner 启动的多步流程**，同样不改任务五态。两者都只在运行面板 / SSE 展示，不进看板、未读、搜索。

## 一、三套状态机并列

| 对象 | 状态词 | 持久化 | 是否用户可观察的“任务对象” |
| --- | --- | --- | --- |
| 任务 (Task) | `todo` / `in_progress` / `in_review` / `done` / `closed` | `task` 行 + 任务 target 消息流 | 是（看板、任务线程、五态） |
| Plan | `pending` / `in_progress` / `completed`（`deleted` 只作删除标记） | `plan_run` + `plan_task` | 否（运行面板） |
| SOP | `pending` / `running` / `awaiting` / `completed` / `failed` | `sop_run.state`（`SOPRunState` JSON） | 否（运行面板） |

三套状态机**没有自动映射**。`plan_task.metadata` 或 `sop_run` 可以带一个可空的 `task_id` 引用，表示“这次
运行发生在这张任务的线程里”；引用不改任务行、不写任务消息、不触发任务状态迁移。

## 二、Plan：AgentScope 的三态工作清单

AgentScope 2.0.10 的 `TaskCreate/TaskGet/TaskList/TaskUpdate` 操作 `AgentState.tasks`，`Task` 的状态是
`pending` / `in_progress` / `completed`，`TaskUpdate` 另有 `deleted`。本仓的 seam 每轮新建 Agent，若不持久化，
Plan 每轮都会丢失。因此：

- `plan_run` 记录一次成员轮次（`target_id`、`member_id`、`base_seq`、起止时间、状态）；
- `plan_task` 记录该 run 的任务（subject / description / state / owner / blocks / blocked_by / metadata / seq）；
- 轮次开始前把该 `(target_id, member_id)` 最近的 run 装载进 `AgentState.tasks`，收口后回写；
- 保留策略：每个 `(target_id, member_id)` 保留**最近 20 个 run 或 30 天**（先到者为准），启动时与每轮收口后清理；
  `plan_task` 随 `plan_run` 级联删除。Plan 不是 append-only（它是工作态），但没有人类面编辑入口。
- Plan **不**进看板、未读、搜索、唤醒、预算；它不是 target，也不产生 `message`。

## 三、SOP：owner 启动的多步流程

`SOPEngine` 按顺序走 `SOPStep`，每步有 executor / verifier 与 `max_attempts`；`SOPRunState` 是纯数据，
可以持久化后恢复。本仓的边界：

- `sop_definition` 保存定义（name / description / steps JSON / enabled）；`steps` 里的 executor/verifier 用
  **member id** 引用，运行时按当前频道成员装配 Agent。
- `sop_run` 保存一次运行（`SOPRunState.model_dump_json()`、`phase`、`target_id`、`leader_member_id`）。
- **只有 owner 能启动 SOP**（D20）；Agent 不能自行创建并运行 SOP。队长可以在 `TeamAssign` 中建议一个 SOP，
  但启动仍要 owner 确认。这是“运行编排是人的动作”的边界。
- SOP 的 `awaiting` 与 HITL 同形：暂停、落审批/恢复记录、由 owner 送回结果；重启后可恢复 `SOPRunState`
  （但参与者 AgentState 的恢复能力取决于 D19 的后续票）。
- SOP **不**改任务五态、不进看板/未读/搜索/预算；它自己的运行记录与 SSE 事件是唯一过程面。
- 保留策略：`sop_run` 保留最近 50 个或 90 天；`sop_definition` 不自动删除。

## 四、与既有“任务”词条的关系

`CONTEXT.md` 的**任务**词条明写“任务是有状态机与线程的对象”“任务行是可变的、消息 append-only”。本 ADR
**保留**这条定义，并明确 Plan/SOP 不是任务：

- 任务：用户建、owner 指派/Agent 认领、五态、看板、任务线程、预算世代。
- Plan：Agent 在一次轮次内给自己列的工作清单；三态；运行面板；不跨成任务。
- SOP：owner 启动的多步流程；步骤有 executor/verifier；运行面板；不跨成任务。

如果用户希望“把 Plan 项变成任务”或“把 SOP 步骤挂到任务看板”，那是显式的人类动作，需要新的产品决策；
本 ADR 不提供自动升级。

## 五、被否方案与代价

- **把 Plan 直接映射成任务行**：模型会在运行中批量创建用户可见任务，看板被工作清单灌满；任务五态的
  actor/认领/预算语义也被绕过。
- **Plan 只走 SSE、不落库**：刷新/重启即丢，运行面板无法查看“它刚才计划了什么”；排障与复盘都缺证据。
- **SOP 由 Agent 自主启动**：多步流程会自行消耗多轮模型调用与（若在 team 里）多跳预算，用户没有启动点；
  与“SOP 是 owner 的编排”冲突。
- **Plan/SOP 进未读/搜索**：它们不是消息，计数与命中语义会被污染；用户会把工作清单误读成对话。
- **把 `plan_task` 当 append-only**：Plan 是工作态，需要更新状态与删除；用 append-only 会让“当前清单”变成
  重放聚合，违背 Plan 的用途。

**代价**：多两张/三张表与清理逻辑；运行面板要同时呈现三套状态；Plan/SOP 的历史与任务历史分开查询；
SOP 的实验性 API 变更需要适配（ADR-0020 / D7）。
