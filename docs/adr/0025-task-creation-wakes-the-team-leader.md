# team 频道建任务也唤醒队长：队长轮次拿到队长身份，并且任务变化唤醒是确定信号

用户于 2026-10-19 人定（本票 A+B）：
**A** team 频道**建任务也唤醒队长**（推翻 ADR-0022 §二表格里「创建任务 / 认领 = 零唤醒」中的**创建**这一格；
**认领**仍然为零）；**B** 队长被**任务变化**唤醒的那一轮是**确定信号**（`mandatory_reply=True`），
并且队长轮次拿到的是**队长身份**的 system prompt（明确 `TeamAssign` 分派职责）。

本 ADR 只声明**覆盖关系**：ADR-0022 / ADR-0016 的正文**一字不改**（沿用 ADR-0007 / 0010 / 0016 处置旧 ADR 的既有惯例）。
被覆盖的部分只在这里、`CONTEXT.md` 的**唤醒** / **回应判断** / **队长** / **TeamAssign / 分派** / **任务线程**词条、
以及 `app/specs/agentscope-2-alignment.md` §3.3 的唤醒矩阵里声明。

## 决策记录

- **Context**：#179 的真实库读数给出了三个同源缺陷：team 频道建任务后任务停在 `todo`、`assignee` 为空、线程只有
  一条 `system 任务已创建`、该 target `round_outcome` **0 行**；owner 指派之后线程里只有一条
  `system 已指派给 X` 就停住；全库 `team_run` / `team_assignment` **各 0 行**。前两条的根因是**零唤醒 +
  可以合法沉默**，第三条的根因是队长轮次的 prompt 从头到尾没有 `TeamAssign` 这三个字（它写的是
  「你的身份是**成员**」「仅**当前受派者**可使用 transfer_task」，而队长恰恰不是受派者）。
- **Decision**：team 频道建任务向队长投递**一轮**（`mandatory_reply=True`，`from_seq` 取建任务那条 `system`
  消息的 `seq`），仍**不认领**；team 任务线程的改派/转派唤醒同样改为 `mandatory_reply=True`；队长轮次使用
  **队长身份**的 prompt（`_system_prompt(..., leader=True)`）。认领（`claim`）、非 team 频道的唤醒面、
  `_team_member_prompt`、autonomous 轮次的 prompt **逐字不变**。
- **Consequences**：team 频道里「建任务」与「owner 改派」从此都会让队长跑一轮并要求它给出回应；队长沉默按
  `failed` 收口，不再有「指派后只有一条消息就停住」这个合法静默窗口。代价是每次建任务多一次队长模型调用；
  队长轮次的可读诊断用**任务变化**那套词条（见 §六），不复用认领文案。

## 一、覆盖 ADR-0022 §二「创建任务」那一格

ADR-0022 §二 的表格里有一行「创建任务 / 认领 = **零唤醒**」。本 ADR 覆盖其中的**创建**：

| 触发 | team 频道的唤醒结果（本 ADR 之后） |
| --- | --- |
| **创建任务**（owner `POST /api/channels/{id}/tasks`） | **向队长投递一轮**，`mandatory_reply=True`；**不认领**、不改任务行、不唤醒成员、不扣跳 |
| 认领（`claim_new_task`） | **零唤醒**；team 频道仍直接返回 0（ADR-0022 §四 的结论不变） |

「认领」这一格**一字不改**：`claim_new_task()` 在 team 频道依旧是早退，`claimed_at` 依旧不被使用，
`auto_claim` 依旧是读路径规则。判据仍然是「team 模式下任务只能由 owner 指派或队长经 `TeamAssign` 分派」——
本 ADR 给队长的正是**知道有这张任务**的那一轮，而不是替谁接手。

投递点不是 `notify()`：`system` 事件消息不是对话回合，`notify` 在它上面早退（ADR-0022 §二「其它 `system` 记录」
那一格不变）。建任务走**独立入口** `WakeOrchestrator.notify_task_created(task_id, channel_id)`：

```text
create_task()（人的动作，唯一的扳机）
  → notify_task_created(task_id, channel_id)
      → 解析 team：task target 经 task.channel_id -> channel.mode（ADR-0022 §一）
      ├─ 非 team：转调既有的 claim_new_task(task_id, channel_id)   ← 认领逻辑只有一份
      └─ team：队长可用 ⇒ 向队长 submit 一轮（target = task target，from_seq = 建任务那条 system 的 seq，
                                            reason = channel-message，jump_delivered = False，
                                            pierced = False，mandatory_reply = True）
               队长不可用 ⇒ 零唤醒 + 一条 team_run(status="invalid-leader")（与 D18 同口径）
```

这个名字是刻意的：`claim_new_task` 的语义是**认领**（改任务行 + 开世代 + 唤醒认领者），team 分支既不改行也不
开世代，让它承担「唤醒但不认领」会让这个方法名与它的副作用脱钩。对照被否方案见 §五。

## 二、`from_seq` 与「不认领 / 不扣跳」的边界

- **`from_seq` = 建任务那条 `system` 消息的 `seq`**。它同时是这一轮的 `base_seq`、`team_run.base_seq` 与
  「队长在轮次里能看到哪条待读消息」的基准；取别的值会让队长看不到「任务已创建：<标题>」（ADR-0016 四 的
  同一条记录），或让 `round_outcome.base_seq` 与线程首条记录脱钩。读法是从任务线程的消息流取第一条 `system`，
  **不硬编码 1**、也不新增映射表：流是它唯一的正本。
- **不认领**：`task.status` / `assignee_member_id` / `claimed_at` / `updated_at` 四个字段一律不变；任务停在 `todo`。
- **不扣跳**：人类建任务是**人的动作**，不是 agent→agent 跳（ADR-0005 / ADR-0019）；投递这一轮不写
  `target_budget`，因此 `target_budget.used` 与「人类世代」都不动。
- **不写 `team_assignment`**：分派只在队长真的调 `TeamAssign` 时才发生，建任务本身不是一次分派。
- **不唤醒成员、不穿透**：成员只在队长分派它们时运行（ADR-0022 §二 的穿透结论不变）。

## 三、覆盖 ADR-0016 §五 的确定信号范围：任务变化唤醒队长也是确定信号

ADR-0016 §五 的适用范围写的是「只圈认领唤醒」，确定信号因此只有 `mention` 与 `claim` 两类。本 ADR 增补第三类：

| 确定信号 | 载体 | 落点 |
| --- | --- | --- |
| 被个人提及 | `mention` | ADR-0016 §五（不变） |
| 被认领唤醒 | `claim` | ADR-0016 §五（不变；team 频道不发生） |
| **team 队长被任务变化唤醒** | 建任务 / owner 改派 / Agent 转派 | **本 ADR**：`mandatory_reply=True` |

判据仍是那一个**显式布尔**（`mandatory_reply`，进程内、不落库），不读 `reason`：重复唤醒合并、跨 target
deferred 继承参数时，「谁先到」不能决定纪律（ADR-0016 §五 的推理逐字有效）。因此在确定信号下：

- 队长自判忽略（`ignored`）或结构化声明沉默（`silent`）⇒ 收口成 `failed`；
- 回复但没有可落库正文、也没有提交任务动作 ⇒ `failed`；
- **只提交一个动作**（`TeamAssign` 走 pipeline、或 `set_task_status`）**算回应**——`TeamAssign` 的那一轮
  队长的正文可以为空，这与 ADR-0016 §五 对认领轮的既有裁定同构。

`actor` 是队长时的改派（队长自己 `transfer_task`）仍然**零唤醒**：不自我唤醒是 ADR-0022 §二 的既有结论，
本 ADR 不改它。

## 四、队长身份：`_system_prompt(..., leader=True)`

队长轮次此前**复用成员**的 prompt，于是模型读到的三条都是错的事实：身份是成员、`transfer_task` 只属受派者、
`TeamAssign` 一个字都没有。本 ADR 定的是：队长轮次换**身份句**、另加一段 `TeamAssign` 指引、必答时用队长口径的
「必须回应」句，其余 rubric 逐字保留。

- **身份句**：「你是这个 team 频道的队长，不是成员：队长负责拆解任务、把具体工作分派给成员，再汇总他们的结论给出最终回复。」
- **分派指引**：需要成员动手时用 `TeamAssign`（成员 + 任务描述）；成员在**同一轮**里运行、结果作为工具结果回到队长，
  不需要也不能指望他们另开一轮；成员看不到频道上下文，所以任务描述要写全。
  **不在 prompt 里再列一份成员名单**：`TeamAssign` 的可选成员由**该工具自身的描述**给出（`team.py` 从本轮的
  `TeamRoundContext.members` 生成），而任务上下文那侧能拿到的 `transfer_options_for()` 来源是
  `Database.agent_members()`（**全工作区**）——把它标成「可 TeamAssign 分派的成员」会把频道外的 Agent 写成可分派对象。
  两处列同一份名单也会漂移（第二正本），所以队长**不是受派者**时 prompt 只说「需要成员动手时用 TeamAssign，
  可选成员见该工具的描述」；队长**就是受派者**时保留的仍是 `transfer_task` 那份（来源与工具授权一致，措辞也对得上）。
- **`transfer_task` 的边界**：仍**只属受派者**（ADR-0022 §三 不变），队长不能用它改指派人；需要换人时用 `TeamAssign`。
- **「必须回应」句**：`mandatory_reply` 且队长轮次时是「本轮由 team 任务变化唤醒，你必须回应。」——
  **不复用**认领轮的「本轮由认领唤醒，你必须回应。」（team 频道不认领，复制会写下不存在的事实）。

**不变的措辞**（逐字，有现成断言守着）：autonomous 轮次与 team 成员轮次（`_team_member_prompt`）的 prompt；
认领轮（`leader=False` + `mandatory_reply=True`）的「本轮由认领唤醒，你必须回应。」
（`app/tests/test_auto_claim.py` 的三处断言）。

## 五、被否方案

- **只改 prompt 不改唤醒**：队长会拿到正确的分派指引，但建任务之后它**根本不跑**——线程里仍然只有一条
  `system 任务已创建`。真实库读数（该 target `round_outcome` 0 行）说明这是零唤醒，不是措辞问题。
- **只改唤醒不改 prompt**：队长被叫醒了，但 prompt 说它是「成员」、说它不能用 `transfer_task`、也没提 `TeamAssign`；
  它的合法动作集合里没有任何一项能推进这张任务。现场那次「队长没有再分派」正是这条。
- **让 `notify()` 为建任务特判**：`system` 早退是「system 事件消息不是对话回合」这条既有判据的落点，为一个入口
  开例外会让「`system` 零唤醒」在别处（改状态、改标题、改指派人之后的那条记录）重新变得不可判。
- **在 team 频道保留认领**：ADR-0022 §四 已经否掉（与 `auto_claim` 全关、只唤醒队长冲突，还可能重置 task 预算世代）。
- **把认领逻辑复制一份到 team 分支**：两份「谁该接手」的裁决会在下次改规则时漂移；因此 team 分支只做「投递一轮」，
  非 team 分支**转调**既有的 `claim_new_task`。

## 六、代价与可观察后果

- **每次建任务多一次队长模型调用**：team 频道建任务不再是纯写库。队长轮次会落一条 `round_outcome`（`replied` /
  `failed` / …）与一条 `team_run`；`team_run` 从「指派之后才有」变成「建任务就有」。
- **队长失败不再合法沉默**：建任务 / 改派唤醒的那一轮 `ignored` / `silent` 一律按 `failed` 记账。可观察后果：
  用户的运行面板会看到失败而不是「它只是没说话」；这是有意的——「指派后只有一条消息就停住」正是要被消灭的形态。
- **措辞与信号来源一致（2026-10-19 用户裁定后补齐）**：`mandatory_reply` 仍只承载**纪律**，但它的**可读文案**要按信号来源选。
  队长任务变化轮用 `reason.team_change_round` / `_ignored` / `_silent` / `_no_body` 四条（「本轮由**任务变化**唤醒」），
  认领轮仍用 `reason.claim_*` 四条（`autonomous` 的读数与改动前**逐字相同**），提及轮仍用 `reason.mention_*`。
  判据不读 `reason`：`run_round` 只用既有的结构事实（`_team_round_context(...)` 非空 = 队长轮）挑文案，
  没有新增第六种纪律语义——纪律标记仍是同一个 `mandatory_reply`。
- **`TeamAssign` 仍然不改任务行**：ADR-0022 §三 全部保留。队长的分派结果只看运行记录；
  任务行/五态仍由 owner HTTP 与队长轮次的结构化工具维护。
- **没有新增唤醒 reason 词**：建任务那一轮的 `reason` 仍是 `channel-message`——`reason` 只做可读诊断
  （ADR-0016 §五），纪律由 `mandatory_reply` 承载，因此不需要新的 reason 取值。

## 七、与既有 ADR 的覆盖与保留

| 既有 ADR | 被覆盖的部分 | 保留的部分 |
| --- | --- | --- |
| **ADR-0016**（认领即开工 / 确定信号） | §五「确定信号只含 mention / claim」的范围：**team 队长被任务变化唤醒**也是确定信号 | 认领的一切（开世代、推状态、两条 `system`、每任务至多一次、不是跳）、`mandatory_reply` 不落库、`reason` 不承担纪律、autonomous 轮次文案 |
| **ADR-0022**（team 覆盖任务线程） | §二表格「创建任务 / 认领 = 零唤醒」中的**创建**这一格 | 「认领」那一格、`claim_new_task` 早退、`TeamAssign` 不写任务行（§三）、`in_review → done` 的自审禁令（§五）、task target 不穿透、owner 改派 / Agent 转派的唤醒对象是队长 |
| **ADR-0005**（派任务是人的发言 / 有界响应） | 无 | 「建任务是人的动作，因此不是跳」这条读法正是本 ADR 不扣跳的依据 |
| **ADR-0018 / 0019**（team 拓扑 / `TeamAssign` 计跳） | 无 | 分派仍计 1 跳、`team_assignment` 仍是运行记录 |
