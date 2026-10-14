# Issue #22 设计记录（第二版规格正本）

本票类型：**设计/规格票**，门禁 **G-docs**。零源码改动：本票产物是规格 issue 正文、`CONTEXT.md` 词汇表、
`docs/adr/` 新条目、`to-tickets` 拆出的实施票，以及本文件与 `issue-22-pr-body.md`。

- 本票 issue：https://github.com/whutlichao/alibot/issues/22
- 上游锚点（首版规格正本）：https://github.com/whutlichao/alibot/issues/1
- 前置 skill：`grill-with-docs`（依次调用 `grilling` 与 `domain-modeling`）→ `to-tickets`
- 提问通道：`orca orchestration ask`（禁 AskUserQuestion）

---

## 0. 开工核对的事实（用于提问与规格取证；不是决策）

### 0.1 仓库现状（`app/alibot/`，只读）

- `db.py` 的 `message` 表已有 `target_id` 列，`UNIQUE (target_id, seq)`；行的值**今天就是 channel id**
  （`CHANNEL_SELECT_SQL` 里 `m.target_id = c.id`）。所以「channel 型 target 的 id 用什么」不是纸上问题：
  现有数据的 `target_id` 语义就是 `channel.id`。
- 仍以 `channel_*` 命名、但作用域其实是「消息作用域」的表有三张：`channel_read(member_id, channel_id,
  last_read_seq)`、`channel_budget(channel_id, epoch_seq, used)`、`round_outcome.target_id`（这张已叫 target）。
- 未读口径**只有一处落点**：`db.py` 的 `CHANNEL_SELECT_SQL`（已读位置之后、`kind IN ('human','agent')`、
  作者非本人）；`unread_total` 由 `unread_count` 相加（`service.py`）。
- `channel_member(channel_id, member_id)` 是每频道显式成员表；`orchestration.py` 的唤醒集合 =
  「该 target 的全部 agent 成员 + 穿透」，唤醒信号 `(member_id, target_id, from_seq, reason)`。
- `channel_budget.used` 是**跳数唯一正本**（ADR-0002 / #12）；`round_outcome.consumed_budget` 只是行级标记、
  **不可求和**。
- HTTP 面全在 `/api/channels/{channel_id}/...`（消息、成员、预算、轮次结果、已读、事件流）。

### 0.2 原型痕迹（`alibot-design-system/alibot-app.html`，只读参照，本票不改）

| 能力 | 原型痕迹 | 与票面决策的差异 |
| --- | --- | --- |
| 任务状态机 | `COLUMNS` 四列（待认领 / 进行中 / 待审 / 完成）+ `TRANSITIONS` 表 + `STATUS_COLOR`（`todo→--faint`、`in_progress→--accent-graphic`、`in_review→--working`、`done→--online`、`closed→--offline`） | `STATUS_COLOR` 已有 `closed` 色位 ⇒ 五态在视觉契约里**已有**落点 |
| 任务迁移动作 | `tryMove` / `threadMove` 在非法迁移时 toast 拦截 | 决策 6：本轮不做迁移动作的校验语义细化 |
| 任务 chip | 消息渲染里的 `<button class="task-chip" data-opentask="…">关联任务 #N</button>`（消息对象带 `m.task` 字段） | `#1` 的 Out of Scope 把「指针消息」列为不含项，本票要收回并给语义 |
| 任务线程 | dock 的 `.task-thread`：`#ttTitle` / `#ttStatus` / `#ttTag` / `#ttAssignee`（改派）/ `#ttTransitions` / `#ttLinked`（**关联消息**）/ `#ttReplies`（讨论）/ `#ttHistory` | 线程里有**可编辑**的呈现；与消息 append-only 的关系是承重问题 |
| 任务↔消息 | `linkedMessagesFor(id)`：按 `m.task === id` 跨**全部频道**扫描 | 任务与频道的关系是承重问题 |
| 任务与 DM | `renderTasks` 里 `if (state.dm) { …'私信里没有任务板'…'任务属于频道' }` | 票面 B 组「任务与频道的关系」的一手痕迹 |
| 转为任务 | 消息 hover 工具栏有 `data-convert`（标题「转为任务」），但**点击分发器里没有对应分支**（未接线） | 原型未实现的入口 |
| 任务标题/指派 | 原型里 `#ttTitle` 仅展示；指派可改（`data-ttassign`）；`data-convert` 未接线 | 「任务可变还是 append-only」是承重问题 |
| DM | `DMS = [{id,name,agent,unread}]` 两组两人；`#dmList` **与频道分在同一段**呈现（频道列表之后、Agent 名单之前），行内有状态点 + 未读徽标 | 票面 C 组「左栏呈现」的对照 |
| 搜索 | `renderSearch()`：跨**全部频道**的消息 + 任务标题 + Agent 名/角色；`mark()` 在前端做命中高亮；facets「全部/消息/任务/Agent」；**无 target 限定** | 决策 4 与原型的三处差异（排除 system / 可限定 target / 高亮归属） |
| 命令面板 | `CMDS` + `⌘K` 打开 `#cmdOverlay` | 票面说：与「搜索」是两件事，可拆开 |

### 0.3 与既有 ADR 的关系（本票必须处理，不得静默绕过）

- **ADR-0001 消息 append-only**：任务若「可变」（改标题 / 改指派 / 改状态），必须说清它**不是消息**与
  消息流的呈现方式；任务的**历史**若用消息表达，就是 append-only 的自然延伸。
- **ADR-0002 预算封顶三次**：`channel_budget` 是「每频道一行」，本票要把作用域升为 target ⇒ 该 ADR 的措辞
  需要跟着泛化（跳数正本仍是同一列 `used`，不许削弱）。
- **ADR-0003 Agent 只有结构身份**：任务 / DM / 搜索都不引入新面；搜索是**人类面**读取能力，不给出「以某成员
  名义搜索」的路径。
- **`docs/agents/domain.md` 的懒创建规则**：只收真实决策（难逆 + 无上下文会困惑 + 真取舍），不为每件事写 ADR。

---

## 1. grill 第 1 轮（A 组：target 升为一等对象）

> 原文以 `orca orchestration ask` 发出，问答原文与理由逐轮记录在本节及后续节。

### 1.1 Q → A 原文（第 1 轮：A 组）

问题原文见 `git log` 对应的 chat 记录摘要；答复原文如下（coordinator 回信，逐字保留）：

> 第 1 轮逐题裁定。Q1/Q2/Q4/Q5 是**人定**（用户本轮拍板）；Q3/Q6 是 coordinator **代答**（纯命名与措辞，不涉产品取舍）。六题**全部接受你的推荐**，连同你给的理由。
>
> **Q1（人定）：接受 (a) 复用 channel.id。** 用户选中选项原文要点：「同一个字符串既是 channel 主键也是 target 主键；message.target_id 今天的值就是 channel id（零语义漂移）。target 表对 channel 型是影子行，channel 表继续持 name/description。」写进规格时请把「影子行」这个词与它的含义（id 同值、只承载 kind）说清楚，并写明被否的 (b) 为什么不要。
>
> **Q2（人定）：接受——每 target 一份，表泛化为 `target_budget(target_id, epoch_seq, used)`。** 机制（人类世代内 ≤3 跳、耗尽零投递、`used` 是跳数唯一正本）**逐字不变**。用户原话要点：「跨 target 共享预算会造成『一个频道烧完把另一个频道锁死』。」另请写明 DM 的事实（agent 回复不唤醒 ⇒ 跳数天然近 0），以及为什么仍要保留同一机制（不出现特例）。
>
> **Q3（代答）：接受改名。** `target_read(member_id, target_id, last_read_seq)`、`target_budget(target_id, …)`；`round_outcome.target_id` 本来就叫对，不动。理由：`kind IN ('dm','task')` 的行里写 `channel_id` 是**错名**。另：`SCHEMA_VERSION` 要 bump（决策 8 说不做迁移、新库从空开始），这条要写进规格的 Implementation Decisions。
>
> **Q4（人定）：接受 (b) 泛化 `target_member(target_id, member_id)`。** 用户选中选项原文要点：「DM『两人』由创建时写两行保证；任务指派 = 更新那一行。唤醒面/可见面/未读三处查询只用一个 target_id 键，不按 kind 分支。」你那条「`target_member` 表达**成员面**，而任务 target 的可见性本来是全工作区（单用户），成员行表达的是『谁能被唤醒 + 未读算谁的』」的区分很重要，请**原样写进规格**。
>
> **Q5（人定）：接受——`unread_total` = channel 型 + dm 型之和；task 型单列，不进 unread_total。** 用户选中选项原文要点：「两者都是『消息流』，用户心智一致；任务未读单独成一类（看板卡片徽标 + 任务视图）。channel 型的三条既有纪律一字不改（不计 system、排除本人、只算顶层 ⇒ 任务线程消息**不算**频道未读）。」请写明「计数 SQL 仍只有一个形状」这条纪律，并给出它如何同时服务三种 kind。
>
> **Q6（代答）：接受你的方案。** `target` 改写（承重，收回规格自己预留的扩展点）；措辞泛化五个：**已读位置** / **人类世代** / **回应预算** / **预算表**（channel_budget → target_budget）/ **频道成员**（点明「channel 型 target」，与新增的 target 成员关系对齐）；保持：`频道` / `消息` / `提及` / `穿透` / `唤醒` / `轮次` / `轮次结果` / `跳` / `预算耗尽` / `结构身份`。你的判断（「频道仍是人对这个容器的叫法，它只是 target 的一种 kind」）与用户在本轮选择里的措辞一致，照此写。新增承重词第 2 轮起一并写。
>
> **另两件事**：1. Q1 定了「影子行」之后，请特别把**删除/生命周期**说清（本仓频道没有删除入口，但 target 表引入了新的行，要写明「频道不删 ⇒ 影子行也不删」或你的等价说法）。2. 接着问 **B 组（任务看板）**。B 组里我预判有承重决策要问用户（任务的**可变性** vs 消息 append-only、指针消息语义、互审由谁改状态），照 Q1-Q5 的格式一次问完，附推荐与理由。

### 1.2 第 1 轮裁定表

| # | 问题 | 裁定 | 方式 |
| --- | --- | --- | --- |
| Q1 | channel 型 target 的 id | **复用 `channel.id`**（target 表为**影子行**：id 同值、只承载 `kind`）；被否的映射方案代价写入规格 | 人定 |
| Q2 | 预算作用域与表名 | **每 target 一份**，`target_budget(target_id, epoch_seq, used)`；机制逐字不变 | 人定 |
| Q3 | `channel_read` / `channel_budget` 改名 | **改名**为 `target_read` / `target_budget`；`round_outcome.target_id` 不动；`SCHEMA_VERSION` bump | 代答 |
| Q4 | 成员关系 | **泛化 `target_member(target_id, member_id)`**；DM 两人由创建写两行保证，任务指派 = 那一行 | 人定 |
| Q5 | 未读口径与汇总 | **`unread_total` = channel 型 + dm 型**；task 型单列（看板徽标）；计数 SQL 只有一个形状 | 人定 |
| Q6 | 词汇表改动范围 | **target 改写 + 五个措辞泛化 + 其余保持** | 代答 |

附加义务（已写入规格要求）：影子行的**生命周期**必须写清（频道不删 ⇒ 影子行不删）；Q4 的
「target_member 表达成员面，任务可见性本是全工作区」原样进规格；`SCHEMA_VERSION` bump 进 Implementation Decisions。

### 1.3 Q → A 原文（第 2 轮：B 组）

> 第 2 轮逐题裁定。Q1/Q2/Q4/Q5 **人定**；Q3/Q6 **代答**。六题全部接受你的推荐。
>
> **Q1（人定）：接受 (b) —— task 行可变（UPDATE）+ 每次变更追加一条 `kind='system'` 消息进任务 target 的消息流。** 用户选中选项原文要点：「task 行可 UPDATE（改标题/状态/指派），但每次变更追一条 `kind='system'` 的消息进任务 target 的消息流（历史 = 消息流）。消息本身仍 append-only（ADR-0001 不推翻）。代价：任务流比频道流『话多』。」你那条「ADR-0001 的措辞不需要改，但规格要写明『task 行的 UPDATE 不是消息的 UPDATE』」——**请原样写进规格**，这句是防止后续把两者混为一谈的关键。
>
> **Q2（人定）：接受引用字段。** `message` 加可空列 `task_ref`；**不是**新增消息 kind。并接受你的限定：**chip 指向的任务必须与该消息同 target**（不做跨频道引用）。用户选中选项原文要点：「chip 是已有消息的一个属性，不是新 kind；不做新 kind：消息流里不会出现无作者的行，未读/唤醒口径不多分支。」写规格时请把「本票收回 #1 的『指针消息』不含项——收回的是『消息可以指向任务』这件事，实现形态取引用字段而不是新 kind」写明，并给出该列的可空性与外键/完整性约束（指向的 task 必须存在且同 target）。
>
> **Q3（代答）：接受 (a) —— 任务自己有 target（kind='task'），线程消息落在该 task target 的消息流里。** 依据：决策 1 明写 `kind ∈ {channel,dm,task}`，这是它的直接推论；且它天然给出决策 3 要的未读分离（`target_read(member, task_target_id)` 与频道那一行是两个 target ⇒ 两张徽标独立，不需要额外机制）。你写的 (b) 代价（线程消息混进频道流 ⇒ 未读口径从一处变成两处）是决定性的，照此写规格。
>
> **Q4（人定）：接受——指派者不能自批自己的产出；`in_review → done` 只能由指派者之外的成员触发，owner 恒可。** 用户选中选项原文要点：「『待审』只有在存在一个独立审阅者时才有意义；否则它退化成一个多余的中间态。但 owner 被指派时能不能自批？→ owner 恒可（本机单用户，owner 是最终裁决者）。」请把「owner 恒可」这条**例外**在规格里单独成句。
>
> **Q5（人定）：接受——改状态永远不唤醒；改指派人走「人类发言」路径。** 用户选中选项原文要点：「**改状态**永远不唤醒（不消耗预算）；**改指派人**（owner 指派给某 agent）走『人类发言』路径：新世代 + 预算重置 3 + 唤醒被指派者。这样『谁被派了活』有唤醒，『活干到哪了』没噪声。」你那条「唤醒的载体 =『指派』这个动作，而不是『状态从 todo 到 in_progress』这个动作」——**请原样写进规格**。另请写清：**agent 改指派人**（agent 把任务转派给别人）算不算一次跳跃？按决策 2「agent 派任务 = 一次 agent→agent 跳（消耗 1/3）」推，应当是**算**；如果你的判断不同，直接在规格里写明并给理由（这属于落地细节，我不再单独问用户，但要在规格里有一句明确的话，不许留空）。
>
> **Q6（代答）：接受 (a) + 你的补充——任务归属恰好一个 channel 型 target，看板是全工作区视图（可按频道筛选）；创建阶段不写频道消息。** 依据一：用户在决策 1 的选项里已确认「task 型 target 挂在频道下」。依据二：「创建不写频道消息」与 `CONTEXT.md` 的**加入频道**词条同构——那里的理由正是「人的显式动作不需要后端替他解释」。但请把三件事在规格里分清，避免实现时含糊：1. **创建任务**：task 行 + 首条 task-target 的 system 消息（如「任务已创建」）？还是连 task-target 的首条消息也不写？给出你的取舍与理由（我倾向**不在频道流里写**，但 task target 自己的开场 system 消息可以有，因为它就是那张卡片的线程起点）。2. **改指派**（Q5 已定：唤醒 + 预算重置/消耗）。3. **改状态**（Q5 已定：不唤醒），三条各自写哪条消息、写进哪个 target 的消息流，请列表说清。注意别把「频道流」与「task target 流」混起来——Q3 已定它们是两个 target。
>
> 接着问 **C 组（DM）与 D 组（搜索）**。

### 1.4 第 2 轮裁定表

| # | 问题 | 裁定 | 方式 |
| --- | --- | --- | --- |
| B-Q1 | 任务可变性 | **task 行可 UPDATE**（标题/状态/指派）+ **每次变更追加一条 `kind='system'` 消息进 task target 消息流**；ADR-0001 不推翻 | 人定 |
| B-Q2 | 指针消息语义 | **引用字段 `message.task_ref`（可空）**，不是新 kind；**同 target 限定**；收回 #1 的 Out of Scope 条 | 人定 |
| B-Q3 | 任务线程住在哪个 target | **任务自己有 target（`kind='task'`）**，线程消息落该 target 的消息流 | 代答 |
| B-Q4 | 待审→完成由谁改 | **指派者不能自批**；`in_review → done` 由指派者之外的成员触发；**owner 恒可**（例外单独成句） | 人定 |
| B-Q5 | 改状态是否唤醒 / 改指派 | **改状态永不唤醒**；**改指派走「人类发言」路径**（owner：新世代+重置 3+唤醒；agent：一次 agent→agent 跳） | 人定 |
| B-Q6 | 任务归属与创建 | 任务归属**恰好一个 channel 型 target**；看板是**全工作区视图**（可按频道筛选）；**创建不写频道消息** | 代答 |

附加义务：规格必须**原样**写入两句 —— 「task 行的 UPDATE 不是消息的 UPDATE」、「唤醒的载体是『指派』这个动作，
而不是『状态从 todo 到 in_progress』这个动作」；并列表说清「创建 / 改指派 / 改状态」三条各写哪条消息、写进哪个 target；
agent 改指派人**算一次 agent→agent 跳**（决策 2 的推论，规格必须有一句明确的话，不许留空）。

### 1.5 Q → A 原文（第 3 轮：C 组 DM + D 组搜索）

> 第 3 轮逐题裁定。**人定**：Q1 / Q3 / Q4（C 组）、Q7 的 ⌘K 部分（D 组）。**代答**：Q2 / Q5 / Q6 / Q8 与 Q7 的 FTS 部分。全部接受你的推荐，连同理由。
>
> **Q1（人定）：接受 (a) + 幂等。** 用户选中选项原文要点：「左栏『新建 DM』→ 选一个 Agent → 立刻创建 target(kind='dm') + 两行成员；一 agent 一 DM 唯一（幂等，已存在则复用）。」请写明唯一性怎么在 schema 上表达（你提的 `dm_peer_member_id` 列 + 唯一索引，或去掉 owner 那一行的唯一索引），以及二次创建的响应形状（复用 / `created` 布尔区分，与既有「加入频道」的 `joined` 布尔同构）。
>
> **Q2（代答）：接受——需要，且是 `target_member` 的两行；DM 成员行不可增删（无端点）。** 依据：A-Q4 已定泛化 `target_member`，「两人」由写路径保证。请在规格里写明「DM 不提供加入/离开端点」这条负向约束。
>
> **Q3（人定）：接受——DM 里被 @ 的第三个 Agent 被唤醒但**不加入**（一次性送达）。** 用户选中选项原文要点：「DM 里被 @ 的第三个 Agent 被唤醒但不加入（一次性送达：回复落库后不写成员行、不写 system 加入消息）。保住『DM 就是两人』这条心智。」你指出的代价请照写：`CONTEXT.md` 的**穿透**词条要加一句「DM 型 target 上穿透只送达、不改变成员关系」。另请写清一件事：穿透进 DM 的那次回复落库后，DM 的**成员行不变**，但这条回复**算不算** DM 的未读？按未读口径它是 `human`/`agent` 正文消息 ⇒ 应当算。若你的判断不同，在规格里写明并给理由。
>
> **Q4（人定）：接受——沿用原型的同段呈现 + 加分区小标题（「频道 / 私信 / Agent」三段）。** 用户选中选项原文要点：「频道区之后、本机 Agent 区之前，加一个分区小标题；DM 行显示头像 + 状态点 + 未读徽标（与频道行的 `#` 前缀区分）。」
>
> **Q5（代答）：接受——后端返回高亮区间数组 `[{start, end}, …]`，前端只按区间包 `<mark>`；标出全部命中。** 依据：本仓既有纪律「解析只有一个落点」（`CONTEXT.md` 的**补全**词条：「前端**不解析**提及」），搜索命中判定是同类问题。你那条边界（「前端仍做**呈现**（包标签、滚动定位），只是不做**判定**」）请**原样写进规格**。另请写明「标出全部命中区间，不只第一处」是**规格要求**。
>
> **Q6（代答）：接受——只排除 `kind='system'`；soft-delete 成员的消息**仍可被搜到**（行内标注作者已删除）；不加 (c) 可见性过滤。** 依据：本仓「成员 soft-delete 只影响是否还能参与、不影响历史消息的作者渲染」。你明确不采纳 (c) 的理由（单用户、所有 target 对 owner 可见、加过滤会引入不存在的权限模型）请写进规格的 Out of Scope 或 Further Notes。
>
> **Q7（代答 + 人定）**：**FTS5 部分（代答）**：接受——本版用 `LIKE '%kw%'`（大小写折叠）+ 上限（如 200 条）+ 既有索引，**不用** FTS5；理由（过早优化 + FTS5 引入第二个「正文解析」落点即分词，与 Q5 纪律相冲）请写进规格；并写一条**明确的负向边界**：搜索**不做**分词、不做模糊匹配、不做拼音/近义词；命中 = 子串（大小写不敏感）。**⌘K 部分（人定）**：用户裁定**不做**——「本版不做命令面板（只做搜索），列进 Out of Scope + 写明理由（它是 UI 能力，需要一组『可执行命令』的定义，而本版没有第二个可执行命令，现有的视图切换按钮就够）。」你那句「它若要做，应先定『命令』这个词的世界，而不是复用搜索的返回结构」请写进 Further Notes。
>
> **Q8（代答）：接受——搜索跨全部三种 kind 的 target；结果行标明所属 target 的 kind 与名字。**
>
> **接下来（收尾前的最后几步）**：1. 三组裁定已齐。若还有**必须由用户定的承重问题**，再问一轮；否则**直接落盘**：规格 issue 正本 + `CONTEXT.md` 词汇表变更 + `docs/adr/0004…` + `to-tickets` 拆票。2. ADR 至少覆盖：**target 升为一等对象**（含影子行与 `target_read`/`target_budget` 改名、bump SCHEMA_VERSION、不做迁移）与**派任务与有界响应同构**（含「唤醒的载体是指派动作而非状态变更」）两条；若你认为「任务可变 + 变更写进消息流」（B-Q1）也构成一条独立决策，一并收（它是与 ADR-0001 相邻的边界，值得单列）。3. 拆票时把**承重结构票（A 组）排在最前**，B/C/D 三组按依赖挂其后；每票声明 blocked-by。注意本仓现状：`target` 升一等会同时改 db / service / orchestration / api / 前端五处，**不要**把它切成「每处一张票」（那是水平切分，违反 to-tickets 的垂直切片要求）——按「一块能力一条可验收纵切」来切。

### 1.6 第 3 轮裁定表

| # | 问题 | 裁定 | 方式 |
| --- | --- | --- | --- |
| C-Q1 | DM 创建入口 | **显式创建 + 一 agent 一 DM 唯一（幂等）**；响应以 `created` 布尔区分「新建 / 复用」（与「加入频道」的 `joined` 同构） | 人定 |
| C-Q2 | DM 成员表 | **需要，`target_member` 两行**；**不提供加入/离开端点**（负向约束） | 代答 |
| C-Q3 | DM 里 @ 第三个人 | **被唤醒但不加入**（一次性送达：不写成员行、不写 system 加入消息）；该回复**计入 DM 未读** | 人定 |
| C-Q4 | DM 左栏呈现 | 沿用原型**同段呈现** + 新增分区小标题（频道 / 私信 / Agent 三段） | 人定 |
| D-Q5 | 高亮区间归属 | **后端返回区间数组**，前端只包 `<mark>`；**标出全部命中**（规格要求） | 代答 |
| D-Q6 | 搜索可见性 | 只排除 `kind='system'`；soft-delete 成员的消息**仍可被搜到**（标注作者已删除）；**不加** target 可见性过滤 | 代答 |
| D-Q7 | 索引与 ⌘K | **不用 FTS5**（`LIKE` + 上限 + 既有索引）；**不做分词/模糊/拼音**；**⌘K 命令面板本版不含** | 代答 + 人定（⌘K） |
| D-Q8 | 搜索跨 kind | **跨全部三种 kind**；结果行标明所属 target 的 kind 与名字 | 代答 |

### 1.7 grill 收口

三组 20 问全部裁定（另有第 4 轮拆票 3 问与第 5 轮补收尾 2 问，全票共 25 问），四组「必须定清」清单（票面 A/B/C/D）**逐条有主**，无遗留待问项：

- A 组：id 复用（G1）/ 预算边界（G2）/ 改名（G3）/ 成员关系（G4）/ 未读口径（G5）/ 词条范围（G6）——6/6。
- B 组：可变性 / 指针消息 / 线程 target / 互审 / 状态-指派唤醒 / 归属与入口——6/6。
- C 组：创建入口 / 成员表 / 穿透 / 左栏呈现——4/4。
- D 组：高亮归属 / 可见性 / 索引 / ⌘K——4/4（D-Q8 为确认项）。

设计树已无未访问分支，按 `grilling` 的收口条件（frontier 为空）进入落盘。以下各题的**落地推论**（未单独成问、
由已定决策推导）在规格中写明为「推论」，不冒充用户裁定：**三种 target 的唤醒面**（由 G4 `target_member` 推导）、
**DM 穿透回复计入未读**（由未读口径推导）、**agent 改指派人算一次 agent→agent 跳**（由决策 2 推导）。

---

## 2. 票清单（`to-tickets` 产物，5 张实施票）

**拆票裁定**：5 票粒度、依赖边、不加额外票，三项**全部人定**（第 4 轮 ask）。用户在同一轮明确补充：
**「#1 的子票已完结，新票不再往 #1 挂」** ⇒ 5 张票的 parent 是**规格正本 #23**，不是 #1。

| 票号 | 标题 | 交付物 | 依赖（原生 blocked-by） | 可独立验收的依据 |
| --- | --- | --- | --- | --- |
| [#24](https://github.com/whutlichao/alibot/issues/24) | 实施票 ①：target 升为一等对象（消息作用域泛化，承重纵切） | `target(id, kind)` 表 + channel 型**影子行**（复用 `channel.id`）+ `channel_member→target_member` / `channel_read→target_read` / `channel_budget→target_budget` 三处改名 + `message.target_id` 加引用 + `SCHEMA_VERSION` bump + **每 target 一份预算**；**不写迁移脚本** | 无（可立即开始） | **既有频道闭环的每一条验收原样通过**（发消息 / 唤醒 / 穿透 / 未读 / 预算 ≤3 / 断线补拉）；三种 kind 各自独立计数 seq 与预算；跨 target 不锁死预算 |
| [#25](https://github.com/whutlichao/alibot/issues/25) | 实施票 ②：任务对象与看板（含任务线程与指针消息） | `task` 行 + `kind='task'` 的 target + **五态** + 全工作区看板（可按频道筛选、五个既有状态色位）+ 任务线程消息流 + `message.task_ref`（同 target 约束）+ chip + **任务未读与频道未读分离** + 创建（task 行 + 首条 task 流 system 消息，**不写频道消息**） | #24（blocked_by=1） | 能断言：**新建任务 → 看板有卡 → 线程发一条消息 → 卡片徽标 +1、频道徽标不变**；跨 target 的 `task_ref` 被拒 |
| [#26](https://github.com/whutlichao/alibot/issues/26) | 实施票 ③：任务的写路径语义（指派 / 状态 / 互审） | **改状态永不唤醒**（不消耗预算，线程留 system 消息）+ **改指派人走人类发言路径**（owner：新人类世代 + 预算重置 3 + 唤醒被指派者；agent 转派：一次 agent→agent 跳）+ **互审**（指派者不能自批，**owner 恒可**）+ 改标题不唤醒 | #25（blocked_by=1） | 能读出数字：三种动作对 `target_budget.used` 的读数分别为 **0 / 归零 / +1**；自批被拒且原因可读，同一张任务 owner 能改 |
| [#27](https://github.com/whutlichao/alibot/issues/27) | 实施票 ④：DM 私信（显式创建 + 幂等 + 可唤醒 + 穿透只送达） | `target(kind='dm')` 显式创建 + **一 agent 一 DM 唯一（幂等，`created` 布尔）** + 两行 `target_member` + 无加入/离开端点 + owner 发言唤醒对方 + **agent 回复零唤醒** + 穿透一次性送达（不改成员关系，回复计入 DM 未读）+ 左栏三段呈现 + DM 未读计入 `unread_total` | #24（blocked_by=1） | 创建两次得到**同一个** target（`created=false`）；agent 回复后**零唤醒投递**；私信里 @ 第三个 Agent 后**成员行数不变**且该回复使 DM 未读 +1 |
| [#28](https://github.com/whutlichao/alibot/issues/28) | 实施票 ⑤：工作区搜索（全工作区 + 可限定 target + 后端返回高亮区间） | 全工作区搜索（三种 kind）+ 可限定 target + 排除 `system` + soft-delete 成员消息仍可命中（标注）+ **后端返回高亮区间数组**（前端只包标签、标全部命中）+ `LIKE` + 上限 + 既有索引，**不用 FTS5** | #24 / #25 / #26 / #27（blocked_by=4） | 多命中消息返回 **>1 个区间**；限定 target 后结果只来自该 target；`system` 不出现；只差一个字符的查询**不**命中（证明不做模糊匹配） |

**原生关系核对（`gh api` 读数）**：`#24 blocked_by=0`、`#25 blocked_by=1`、`#26 blocked_by=1`、
`#27 blocked_by=1`、`#28 blocked_by=4`；`#23` 的 `sub_issues_summary.total = 5`。

**⑤ 的依赖是「验收面缺失」而不是形式依赖**（规格裁定，已写进票面）：dm / task 的 target 不存在时，
"搜索跨三种 kind"这条**无法断言**。

**为什么不切成「每层一票」**：`target` 升一等会同时改 db / service / orchestration / api / 前端五处，
按层切是**水平切分**（违反 `to-tickets` 的垂直切片规则，也违反用户第 4 轮的明确要求）；① 是一条**完整纵切**：
改完即可用既有频道闭环端到端验收。

**不加治理票**（第 4 轮裁定）：视觉契约（任务卡片色位、DM 行、三段分组）是各票的**呈现部分**，拆出来
会变成水平切分；ADR / 词汇表已在 #23 内完成。

**发布前两项自查（第 4 轮要求）**：
1. 每票的 blocked-by 用 **GitHub 原生 dependency** 表达（`POST /issues/{n}/dependencies/blocked_by`），
   不是正文里一行 `Blocked by:`——读数见表与上段。
2. 每票都有 **blocked-by 声明 + 可独立验收的依据**两段；② 与 ③ 的验收依据具体到"能断言 / 能读出什么数字"。

---

## 3. PR 与回填

| 项 | 值 |
| --- | --- |
| 规格正本 issue | [#23](https://github.com/whutlichao/alibot/issues/23)（#22 的 sub-issue，标 `ready-for-agent`） |
| 实施票 | [#24](https://github.com/whutlichao/alibot/issues/24) / [#25](https://github.com/whutlichao/alibot/issues/25) / [#26](https://github.com/whutlichao/alibot/issues/26) / [#27](https://github.com/whutlichao/alibot/issues/27) / [#28](https://github.com/whutlichao/alibot/issues/28) |
| 本票 PR | [#29](https://github.com/whutlichao/alibot/pull/29)（`--body-file app/specs/issue-22-pr-body.md`） |
| 分支 | `issue-22-spec-v2` |
| PR 正文 | `app/specs/issue-22-pr-body.md`（按 `pr` skill 的 Summary / Evidence / Merge Danger 体例） |

## 4. 术语一致性自查（`grep` 读数，发布后复查）

- **`_Avoid_` 词扫描（实测读数，按 review P2 更正）**：对 `CONTEXT.md` 与 `docs/adr/0004…0006` 扫既有
  `_Avoid_` 词表（群组 / room / 群成员 / 艾特 / 待办 / 工单 / 聊天记录 / 机器人 / 状态灯 / 指示灯 / 配额 /
  限流 / 搜索结果页 / 全文检索 / 前端高亮 / 正则替换 …）。**结论分两类**：
  **① 本 diff 新增的文本：零命中**；**② 既有文本保留 2 处**——`CONTEXT.md` 正文的「会话」（出现在
  「不从任何会话是否存活推导」这类**引用既有决策**的句子里）与 `_Avoid_` 行自身的登记。
  保留理由：那是 #1 §3 的原话与词条登记，改动它会制造无谓 diff 并让 `_Avoid_` 行失去被引用的锚点。
  **不作"零冲突"的声明**（前稿这么写与 review 读数不符，已更正）。
- **规格正文（#23）**：初稿在 Out of Scope 的"不做清单"里写过「群组 DM」，那会让 `频道` 词条的 `_Avoid_`
  词（群组、room）以自有术语的姿态出现 ⇒ **已改写为「三人及以上的私密 target」**并 push 修正。
  修订后重扫：除 Out of Scope 的"不做清单"外零命中（那是"明确不做"的清单，不是术语使用）。
- **自造简称**：检查「通道 / 私聊 / 任务卡 / 讨论区」等，**正文里只用** `target` / `频道` / `私信` / `任务` /
  `任务线程`（词汇表已收录）；"任务卡片 / 任务卡"仅作为**呈现元素**的普通名词出现，未当术语使用。
- **与既有词条不冲突**：`频道` / `成员` / `唤醒` / `轮次` / `跳` / `人类世代` 的用法在规格与 ADR 里与
  `CONTEXT.md` 一致；`target` 的新定义已被 #23 正文与 ADR-0004 引用同一段措辞。
- **ADR 编号**：`0004` / `0005` / `0006`，与既有 `0001`–`0003` 连续且不重号；格式（段落式 + 取舍 + 代价）
  与既有三条一致。
- **与既有 ADR 的冲突处理**：0001 **未推翻**、0002 **泛化**（措辞在 ADR-0004 与规格 §2 写明）、
  0003 **不受影响**；三处都在 #23 的 Further Notes 显式列出。

---

> **更正（数字）**：本节原写「三组 20 问全部裁定」。**实际题数：全票共 25 问**——A 组 6 + B 组 6 +
> C+D 组 8 = **20 问（grill 三组）**，另加第 4 轮**拆票 3 问**与第 5 轮**补收尾 2 问**。
> 前文 `pr-body` 曾写「20 问分四轮」，**已更正为「23 问分五轮（前四轮）+ 第 5 轮 2 问 = 25 问」**。
> 三组 grill 的题数是 20（18 个决策题 + D-Q8 一个确认题），这个数不是错的；错的是把"三轮 grill 的题数"
> 当成了"分四轮的总题数"。

## 5. grill 第 5 轮（补收尾：网络中断后补定的两问）

第 4 轮（拆票裁定）之后，本票的 turn 被**上游网络错误**（`upstream_error: connection reset by peer`）打断，
规格 #23、`CONTEXT.md`、ADR 0004–0006、票据 #24–28、PR #29 均已落盘且完好；被打断的是最后的收尾提问与
`worker_done`。coordinator 在本轮补定了两个缺口（**均已人定**，除标注外）：

❓ **Q1 - 「人类世代」的世代标识来源**：`epoch_seq` 记的是「最近一条**人类消息**的 seq」，但 owner 在 task 型
target 上改指派落的是一条 `system` 消息（ADR-0005），于是这次「预算重置为 3」没有 seq 可指。
➡️ 候选：(a) 世代标识改为「最近一次**开世代动作**所在消息的 seq」，并写明开世代动作 = ① 一条 `kind='human'`
消息落库，或 ② owner 在 task 型 target 上的改指派（那条 `system` 消息就是开世代的那条）；(b) owner 改指派时
**额外再落一条 `human` 消息**（有 seq 可指，但一次动作产生两条消息）。
➡️ 裁定：**(a)**。被否的 **(b) 不采纳**——它让一次动作产生两条消息，与"一次变更一条记录"相冲。
**证据**：`CONTEXT.md` 的**人类世代**词条已改写为「最近一次**开世代动作**所在消息的 `seq`……① 一条
`kind='human'` 消息落库；② owner 在 **task 型** target 上**改指派人**（它落的 `system` 消息就是开世代的那条，
其 `seq` 即 `epoch_seq`，见 ADR-0005）」；ADR-0005 尾部新增一段「`epoch_seq` 记的是**开世代动作所在那条消息
的 seq**」并记下被否方案；#23 §2 的 `target_budget` 条目新增同名子条。

❓ **Q2 - 「补全」（`@` 成员菜单）的作用域**：`CONTEXT.md` 的**补全**词条写「候选 = **当前频道**的 Agent 成员」，
第二版有了 dm / task 两种 target，这条词条的作用域要不要泛化？
➡️ 候选：(a) 泛化为**当前 target** 的 Agent 成员（三种 kind 同一条规则；**不列 target 外成员**这条负向约束
**保留**，穿透仍只能靠**手打全名**触发）；(b) 改成「列**全工作区** Agent」。
➡️ 裁定：**(a)**。**(b) 不采纳**——它会推翻 #14 已对齐的裁决。**用户选中选项的原文要点**：「频道（现有行为，
不变）：@ → 只列已加入的 Agent，想 @ 未加入的手打全名 → 穿透；DM(owner,阿墨)：@ → 只列阿墨，想 @ 小语手打
全名 → 一次性送达；任务线程：#4 指派给阿墨：@ → 只列阿墨。」
**证据**：`CONTEXT.md` 的**补全**词条作用域已从「当前频道」泛化为「当前 target」，`GET /api/channels/{id}/members`
改为 `GET /api/targets/{id}/members`，并保留「**不列 target 外成员**」与三种 kind 的逐条读法；
#23 §6 新增一条同名 Implementation Decision。

### 5.1 第 5 轮裁定表

| # | 问题 | 裁定 | 方式 |
| --- | --- | --- | --- |
| 5-Q1 | `epoch_seq` 的来源 | 改为「**开世代动作**所在消息的 `seq`」（human 消息 **或** owner 在 task 型 target 上的改指派）；不额外落 human 消息 | 人定 |
| 5-Q2 | 补全的作用域 | 泛化为**当前 target** 的 Agent 成员（三种 kind 同规则）；**保留**「不列 target 外成员」；**不**改成全工作区 | 人定 |

---

## 6. Answer（本票收口：双轴 review 结论与逐条处置）

### 6.1 双轴 review（本票 diff 是文档 ⇒ 审的是**文档一致性**，不是源码风格）

按票面门禁 G-docs，本票**不跑** pytest / ruff / mypy / pyright，也**不跑双轴源码 review**；改为对
`git diff main...HEAD` 跑**两轴文档审查**（Standards = 仓库文档标准 / 术语 / ADR 格式与编号；
Spec = 与 #22 票面、「交付物」四条、「Observable acceptance」七条的一致性），两轴**并行**子代理执行。

#### Standards 轴结论：OK with notes（2 条 P1 + 1 条 P2，均不阻断，纯文档）

| 发现 | 处置 |
| --- | --- |
| **P1 术语内部矛盾**：`seq`（"频道内单调"）/ `消息`（"频道内的一条永久记录"）/ `唤醒`（"该频道的全部 Agent 成员"）/ `轮次`（"至多一条频道消息"）四处仍是频道作用域措辞，与 `target` 词条（target 内单调、三 kind 共用）及 ADR-0004 直接矛盾 | **已修**：四处一律改说 **target**（`seq` → "**target 内**单调"；`消息` → "一个 **target** 内的一条永久记录"；`唤醒` → "该 target 的 **target 成员**里的全部 Agent"；`轮次` → "至多一条 **target** 消息"）。并在 #23 §1 新增一条说明这四处是首版 `target 恒为频道` 时期的遗留措辞 |
| **P1 ADR 冲突留空**：`人类世代` 定义 `epoch_seq` = "最近一条人类消息的 seq"，但 ADR-0005 的 owner 改指派只落 `system` 消息却"重置预算为 3" ⇒ 新世代无 seq 可指 | **已修**：按第 5 轮 Q1 的**人定**裁定改写（见 §5），并补 ADR-0005 尾段与 #23 §2 |
| **P2**：`远端数据流告知` 的数据面清单未含 DM / 任务正文 | **已修**：数据面改写为"消息正文、发送者显示名、**target 的显示名**（含频道名称与描述、DM 对方显示名、任务标题）、被点名成员的显示名" |
| **P2**：design-notes §4 自查称"与既有词条不冲突"，与上述 P1 读数不符 | **已修**：本节即为更正后的完整自查（§7），不再声称"零冲突" |

#### Spec 轴结论：OK with notes（1 条 P1 + 3 条 P2，均为独立性/准确性，无 scope creep）

| 发现 | 处置 |
| --- | --- |
| **P1**：design-notes 缺 Answer 段、也缺**改动前后对照表**（验收 3 要求"哪条词条怎么改的、为什么"） | **已修**：本节（Answer）与 §6.3（改动前后对照，11 行）已补进 design-notes；此前该表只存在于 #22 的 Answer 评论草稿里 |
| **P2**：「20 问分四轮」数字自相矛盾（=23） | **已修**：见本文件顶部**更正**与 §1.7；全票实为 **25 问**（grill 三组 20 + 拆票 3 + 补收尾 2） |
| **P2**：`系统事件消息` 漏列 #23 §3 要求的「**任务创建**」开场 `system` 消息 | **已修**：`CONTEXT.md` 的**系统事件消息**词条已补"**任务创建**（那张卡片的线程起点）与**任务变更**两个来源" |
| **P2**：术语自查"除 `_Avoid_` 行外零命中"不成立（`CONTEXT.md` 正文含"会话"） | **已核**：该处是**既有文本**、非本 diff 引入，且"会话是否存活"是 #1 §3 的原话（"不从任何会话是否存活推导"），保留不改；**§4 的自查结论按实际读数更正**为："新增文本零命中；既有文本保留 2 处，均非本 diff 引入" |
| 限制（子代理自陈）：其环境无 shell / `gh`，"20,411 字符 / blocked_by / sub_issues / PR #29"未核验 | **本会话已独立核验**：见 §6.2 的实测读数（子代理无法访问 `gh`，不构成 finding） |

### 6.2 收尾修正清单与实测读数

#### 修正清单（本轮落地，全部文档）

| # | 修正 | 依据 | 落点 |
| --- | --- | --- | --- |
| 1 | 四处作用域措辞 `频道` → **target**（`seq` / `消息` / `唤醒` / `轮次`） | review P1 + 自查 | `CONTEXT.md`；#23 §1 新增说明 |
| 2 | `人类世代` 的 `epoch_seq` 来源改为「**开世代动作**所在消息的 seq」 | 第 5 轮 Q1 **人定** | `CONTEXT.md`；ADR-0005 尾段；#23 §2 |
| 3 | **补全**作用域「当前频道」→「**当前 target**」，保留"不列 target 外成员" | 第 5 轮 Q2 **人定** | `CONTEXT.md`；#23 §6 |
| 4 | `系统事件消息` 补「任务创建」来源 | review P2 | `CONTEXT.md` |
| 5 | `远端数据流告知` 数据面含 DM / 任务正文 | review P2 | `CONTEXT.md` |
| 6 | 题数更正 20 → **25**（grill 20 + 拆票 3 + 补收尾 2） | review P2 + 自查 | 本文件顶部**更正**、§1.7、`pr-body` |
| 7 | design-notes 补 Answer 段与「改动前后对照表」 | review P1 | 本文件 §6 / §6.3 |
| 8 | 术语自查结论按实测读数更正（不再称"零冲突"） | review P2 | 本文件 §4 / §7 |

#### 实测读数（本会话用 `gh` 与 `git` 核验）

| 项 | 读数 |
| --- | --- |
| 规格正本 | #23，body **21,288 字符**（修正后），`## ` 小节 **8 个**，label `ready-for-agent` |
| 实施票 parent | #24–#28 的 sub-issue parent 均为 **#23**（不是 #1）；#23 的 `sub_issues_summary.total = 5` |
| 原生 blocked-by | `#24 = 0`、`#25 = 1`、`#26 = 1`、`#27 = 1`、`#28 = 4` |
| PR | #29，head = 本地 `HEAD`（收尾提交后需重核，见 §6.4） |
| 词条改动量 | `CONTEXT.md` diff：**+95 / −22**（首轮）+ 本轮修正 |

### 6.3 `CONTEXT.md` 改动前后对照（验收 3 要求）

| 词条 | 改前 | 改后 | 为什么 |
| --- | --- | --- | --- |
| **target** | 「消息与已读位置的作用域键。**首版 target 恒为一个频道**」 | 「消息、已读位置、回应预算与轮次结果的**共同作用域键**，`target(id, kind ∈ {channel, dm, task})`……channel 型**复用 `channel.id`**，target 表对 channel 型是**影子行**……影子行的生命周期由频道决定」 | 承重改写：收回规格自己预留的扩展点 |
| **频道成员** | 「已加入某频道的成员」 | 「已加入某**频道**的成员——即该频道对应的 channel 型 target 的 **target 成员**行」 | 与新增的 `target_member` 对齐 |
| **已读位置** | 「每个成员每个**频道**一行」 | 「每个成员每个 **target** 一行」 | 措辞泛化，语义不变 |
| **人类世代** | 「某**频道**最近一条人类消息的 seq」 | 「某 **target** **最近一次开世代动作**所在消息的 `seq`……**开世代动作**有两种：① 一条 `kind='human'` 消息落库；② owner 在 **task 型** target 上**改指派人**（见 ADR-0005）」 | 措辞泛化 + 第 5 轮 Q1 裁定（补准 `epoch_seq` 来源） |
| **回应预算** | 「每个**频道**每个人类世代最多 3 次跳」 | 「每个 **target**……预算是**每 target 一份**：一个 target 烧完不会锁死另一个」 | 措辞泛化（≤3 不变） |
| **预算表** | 「**channel_budget**：每频道一行」 | 「**target_budget**：每 **target** 一行」 | 随改名 |
| **穿透** | 只有频道上的"回复即加入" | 增补：「在 **DM 型** target 上它只**一次性送达**……不改成员关系」 | DM 上穿透语义不同 |
| **未读** | 「三条纪律」（不计 system / 排除本人 / 只算频道顶层） | 「**五条纪律**」，新增「**计数 SQL 只有一个形状**」与「**汇总分级**（`unread_total` = channel + dm；task 单列）」 | 决策 3 与 G5 |
| **系统事件消息** | 唯一来源是穿透加入 | 新增来源「**任务创建**（线程起点）」与「**任务变更**（改标题 / 改状态 / 改指派）」 | ADR-0006 + review P2 |
| **补全** | 「候选 = **当前频道**的 Agent 成员……**不列频道外成员**」 | 「候选 = **当前 target** 的 Agent 成员……三种 kind **同一条规则**……**不列 target 外成员**这条负向约束**保留**」 | 第 5 轮 Q2 裁定（作用域泛化，#14 裁决不变） |
| **远端数据流告知** | 数据面「消息正文、发送者显示名、频道名称与描述、被点名成员的显示名」 | 数据面「消息正文、发送者显示名、**target 的显示名**（含频道名称与描述、DM 对方显示名、任务标题）、被点名成员的显示名」 | review P2 |
| **seq / 消息 / 唤醒 / 轮次** | 均写「频道内 / 该频道的 / 频道消息」 | 均改说 **target 内 / 该 target 的 / target 消息** | review P1（首版遗留措辞，与 `target` 词条矛盾） |
| **保持不动** | `提及` / `轮次结果` / `跳` / `预算耗尽` / `结构身份` / `Agent` / `订阅` / `视觉契约` 等 | 同前 | 语义未变，不制造无谓 diff |

### 6.4 收尾顺序核对

1. 修正落地 → `git status` 干净、无异常大 diff（本次只动 `CONTEXT.md`、`docs/adr/0005`、`app/specs/issue-22-*.md`）。
2. 双轴 findings 逐条处置 → §6.1。
3. design-notes 追加第 5 轮 Q → A、修正清单、数字更正 → §5 / §6.2 / 顶部更正。
4. 提交、推分支、`gh pr edit 29 --body-file app/specs/issue-22-pr-body.md`，核对 PR head == 本地 HEAD。
5. 在 #22 留一条 Answer 评论（收口报告）；规格正本 #23 不重复评论。
6. 发送并核实 `worker_done`（真实 Task ID / Dispatch ID / 显式 succeeded）。
