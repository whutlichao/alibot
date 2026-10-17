# 认领即开工 —— 设计正本（决策账 + S1–S17 行为契约 + 实施票输入）

- 本票类型：**设计票**，门禁 **G-docs**。**零源码改动**：不改任何 `app/alibot/**`、不改任何测试，不 bump `SCHEMA_VERSION`。
- 本票**不跑**：`pytest` / `ruff check` / `ruff format --check` / `mypy` / `pyright` / `node --test`、双轴源码 code-review（diff 只有 Markdown）。
- 前置 skill：`grill-with-docs`（依次调用 `grilling` 与 `domain-modeling`）。提问通道：`orca orchestration ask`。
- 上游正本（均已合入 `main`）：ADR-0010（认领的预算语义）、ADR-0002（三跳上界）、ADR-0005（派任务同构）、
  ADR-0006（当前态在行上、变更史在流里）、ADR-0008（`silent`）、ADR-0009（双语与语言偏好）、ADR-0015（迁移链）。
- **基线**：`main` = `63e8163`。本文所有 `file:line` 读数基于该基线；实施票开工时须重新 `grep` 复核。

---

## Problem Statement

用户在频道里新建一张任务、指定给开启「自动认领任务」的 Agent 之后，**任务被成功认领，但那张任务不会自动开始**——
状态停在被认领前的样子，被唤醒的 Agent 也从未知道这张任务要干什么。三条根因（均在 `63e8163` 上复核，与票面一致）：

1. **认领本来就不改状态**。`Database.claim_task`（`db.py:1784`）在**一个事务**里只写 `assignee_member_id` +
   `claimed_at` + `target_member` + `target_read` + 一条 `system` 记录 + `target_budget(epoch_seq=该 seq, used=0)`，
   **不碰 `task.status`**。`app/tests/test_auto_claim.py:128` 还把这一条断言成契约（`status == "todo"`）。
   ADR-0010 的动作表把「认领」与「改状态」列为**两格**。
2. **被唤醒的 Agent 拿不到任务本体**。任务线程里只有 `system.task.created`（`i18n.py:82`，正文「任务已创建」，
   **不带标题**）与 `system.task.claimed`（`i18n.py:87`）。`TaskActionContext`（`agent_seam.py:148`）只有
   `task_id` / `assignee_member_id` / `agent_members`；`_system_prompt`（`agent_seam.py:436`）在任务轮次里只说
   「当前在任务线程中」以及工具用法，**既没有任务标题、也没有「你已是这张任务的受派者」**。
3. **唤醒纪律允许它沉默**。`WakeOrchestrator.claim_new_task`（`orchestration.py:245`）→ `notify_task_assignment`
   → `_notify_task_assignee(agent_jump=False)`（`orchestration.py:282`），那一轮传的 reason 是
   `WAKE_REASON_CHANNEL_MESSAGE`（`orchestration.py:305`），即**普通消息唤醒**，基础 rubric 是「没被点名就该保持沉默」
   （`agent_seam.py:445` 起）。只有 `mentioned`（被 @）才是确定信号。

**现场读数**（`~/Alibot/alibot.db` 的只读副本，只作背景）：

```text
task_403c97c5ee6a  title=「分析一下深圳明天的天气。」 assignee=mem_de4c226f7a1d(小张)
                   claimed_at=2026-10-16T15:42:56.354Z  status=in_progress  updated_at=15:44:31
target_budget tgt_3e001af122c6  epoch_seq=2  used=0
线程：1 任务已创建 / 2 已认领：小张 接手了这张任务 / 3 agent 回复「我不是受派者，无人点名，我应保持沉默」
      / 4 状态推进至 进行中（**owner 手动推的**）
round_outcome：round_e7e6eb8d88b6 status=replied consumed_budget=0 reply_seq=3
```

第 3 条那条回复的全文是「This task was claimed by 小张, not me, and no one has addressed me directly — so I should
stay silent.」**任务标题「分析一下深圳明天的天气。」从未出现在任何一条消息里**，也没进过 prompt。

---

## Solution

把「认领」从**只落结构事实**扩展成**编排发起的开工动作**（ADR-0016），四件事一起落地：

- **推状态**：认领在**同一个事务**里把任务从 `todo` 推进到 `in_progress`，并落一条**独立的** `system` 记录
  （新键 `system.task.claim_started`），让人单看线程就知道「这一行不是人推的」。
- **给本体（消息流）**：`system.task.created` 正文改为带标题（同键、加 `title` 参数）；**历史不回填**。
- **给本体（prompt）**：任务轮次的 system prompt 增一段「任务上下文」（标题 / 当前状态 / 受派者身份）；
  `TaskActionContext` 扩 `title` / `status` 两个**输入侧**字段（输出形状不动）。
- **必须推进**：认领轮自判忽略按 `failed` 记录，与「被 @点名」同一套判据、同一处落点；纪律由**显式进程内标记**
  `mandatory_reply` 承载，不落在 `reason` 上。

```text
新建任务（owner HTTP）
  create_task                         → 线程 created(带标题)
  claim_new_task（编排裁决）
    候选 = 该频道的可用 Agent（auto_claim 开 + availability.ok）
    认领事务（单事务）
      指派 + claimed_at + 成员面 + 已读 + 开世代
      system ①「已认领」            ← epoch_seq 取它的 seq
      status todo → in_progress
      system ②「认领后自动开工」
    投递一轮（reason=claim，mandatory_reply=True，不计跳）
      prompt：任务上下文 + 「本轮由认领唤醒，你必须回应」
      收口：replied 或 failed（ignored/无正文无动作 ⇒ failed）
```

---

## User Stories

**A. 认领之后，任务真的开始**

1. 作为本机用户，我想让被认领的任务**立刻出现在「进行中」列**，这样我从看板上看不出「认领了但没动」这种中间态。
2. 作为本机用户，我想在任务线程里看到**两条**记录：「已认领：某人接手」「认领后自动开工：状态推进至 进行中」，
   这样我能从线程本身查出它为什么在动，而不必猜。
3. 作为本机用户，我想让「认领后自动开工」这行**看得出不是人推的**，这样我读历史时不会把编排的自动动作误记成自己的操作。
4. 作为本机用户，我想让这两条记录与状态推进**原子发生**：任一步失败就一条都不留，这样我不会看到「已认领」却查不到开工，
   也不会看到开工却没有认领记录。

**B. 被唤醒的 Agent 知道自己在干什么**

5. 作为本机用户，我想让任务线程的第一条记录就带**任务标题**，这样人读线程能读懂，Agent 读消息也能读懂。
6. 作为本机用户，我想让被唤醒的那一轮 prompt 里带**标题与当前状态**，这样它不必从两条第三人称记录里推断任务是什么。
7. 作为本机用户，我想让 prompt 明确告诉它**「你是这张任务的受派者」**，这样它不会像那次一样自己推出「我不是受派者」
   然后保持沉默。
8. 作为本机用户，我想让认领轮另有**一句显式声明**「本轮由认领唤醒，你必须回应」，这样纪律不必靠模型猜 rubric。

**C. 认领轮不允许沉默**

9. 作为本机用户，我想让认领轮**自判忽略按 `failed`** 记录，这样「它没反应」是可见的失败而不是一个安静的 `ignored`。
10. 作为本机用户，我想让「回复但没有任何可落库正文、也没提交任务动作」同样按 `failed` 记录，这样「假装回应」不成立。
11. 作为本机用户，我想让「只调一次 `set_task_status` 而不说话」**算回应**（不判失败），这样我不把工具动作误判成沉默。
12. 作为本机用户，我想让 `reason` 文本里能读出**这一轮由认领唤醒**，这样排查时我不用去翻进程内存。

**D. 预算与世代语义不被破坏**

13. 作为本机用户，我想让认领**仍然不消耗跳数**，这样它在预算耗尽的世代里也照常开工。
14. 作为本机用户，我想让认领满额重置**仍只在每张任务的第一次**发生，这样 ADR-0002 的三跳上界不会被「认领 → 用满 → 再认领」拆掉。
15. 作为本机用户，我想让世代标识仍取「已认领」那条的 `seq`（不取后来的状态记录），这样 `epoch_seq` 的既有含义不改。

**E. 不被越权改变的东西**

16. 作为本机用户，我想让「开关关闭 / Agent 不可用 / 任务已被认领」三条路径**行为一张表读得出**，这样它们不会被新语义悄悄改掉。
17. 作为本机用户，我想让存量里「已认领但状态还是 `todo`」的那张任务**不被回填**，这样升级不会替我做我做过的事。
18. 作为开发者，我想让 `RoundOutcome` 的字段集**一字不动**，这样 ADR-0010 一 的边界不被悄悄扩开。
19. 作为开发者，我想让「认领推进」复用迁移表 `TASK_STATUS_TRANSITIONS` 做合法性判定，这样将来收紧迁移表时认领会跟着收紧而不是静默越界。
20. 作为开发者，我想让新增文案同时进**后端**两份目录（简中 / English），这样英文界面的系统消息不会是中文。

---

## Implementation Decisions

### D0. 决策归属（人定 / 代答，逐条可追）

三轮 grill 的原始问答与去向见本文 **§Grammar / Answer**（末节）。归类如下：

| # | 决策点 | 结论 | 归属 |
| --- | --- | --- | --- |
| U1 | 认领后由编排把任务推进到 `in_progress` 并把任务本体交给被唤醒的 Agent | 采纳 | **人定**（票面 §二） |
| U2 | 任务本体来源：消息流 + prompt 都带 | 采纳 | **人定**（票面 §二） |
| U3 | 认领唤醒不允许什么都不做；沉默按 `failed` | 采纳 | **人定**（票面 §二） |
| Q1 | 状态推进的落点与记录形态 | `claim_task` **同事务** UPDATE + **同事务**追加一条 `system.task.claim_started` | 代答（coordinator，第 1 轮） |
| Q2 | 「必须推进」的判据 | **逐字复用** mention 两条（`ignored` ⇒ failed；`replied` 无正文且无任务动作 ⇒ failed）；动作-only 算回应 | **人定**（第 1 轮） |
| Q3 | 强制必答的适用范围 | **只圈认领唤醒**；owner 手动改派维持既有纪律 | **人定**（第 1 轮） |
| Q4a | `system.task.created` 正文 | 「任务已创建：{title}」（同键 + `title` 参数） | 代答（第 1 轮） |
| Q4b | prompt 侧注入形态 | 新增「任务上下文」段（标题 / 状态 / 受派者身份）+ 认领轮显式声明 | 代答（第 1 轮） |
| Q5 | `TaskActionContext` 扩不扩 | 扩**输入侧** `title` / `status`；输出形状不动；同步改接缝词条 | 代答（第 1 轮） |
| Q6 | wake reason 常量与新文案 | 新增 `WAKE_REASON_CLAIM = "claim"`（不落库）+ 可读后缀进 `round_outcome.reason` | 代答（第 1 轮） |
| Q7 | 与 ADR-0002 三跳上界的关系 | 维持「每任务至多一次」足够；认领不计跳；**新增代价登记进 ADR** | **人定**（第 1 轮） |
| Q8 | 既有测试断言的处置 | 对照表 + 实施顺序「先改断言 → 看它红 → 再改实现」+ 行号复核 | 代答（第 1 轮） |
| Q9 | 认领推进用哪个词条键 | **新键** `system.task.claim_started`；既有键继续只表示「有人推了状态」 | 代答（第 2 轮） |
| Q10 | 认领推进绕不绕 actor / 迁移表校验 | **私有**推进实现：复用 `TASK_STATUS_TRANSITIONS`、**不复用** actor 校验；执行者是编排；不新增 actor 痕迹字段 | **人定**（第 2 轮） |
| Q11 | 必答判据 × in-flight 合并 | 判据落**显式布尔** `mandatory_reply`（不落库），合并时**取或**；`reason` 职责收窄 | 代答（第 2 轮） |
| Q12 | 认领轮 × 预算耗尽 | 不受影响：仍投递、仍必须回应（ADR-0010 二的直接推论） | 代答（第 2 轮） |
| Q13 | 三路径行为 | 见 D7 读数表（五格）；含「已落库但 wake 时才不可用」那一格 | 代答（第 2 轮，三条硬要求） |
| Q14 | `CONTEXT.md` 改哪几处 | **七处**（认领 / 任务状态 / 人类世代 / 成员面适配接缝 / 唤醒 / 回应判断 / 沉默）；**轮次结果不改** | **人定**（第 2 轮） |
| Q15 | 词条新正文 / 三路径表 / S1–S17 确认 | 全部确认 + 4 处加固（author 口径 / created 限定新建 / seq 示例标注 / 双语只针对后端） | 代答（第 3 轮） |

`frontier 状态：为空`。所有决策有主，无 `open` 项（自查扫描命令与读数见 A5）。

### D1. 认领事务：七件事，一个事务

`Database.claim_task` 在既有事务内**增加**两件事（其余五件逐字不变）：

| # | 动作 | 落点 |
| --- | --- | --- |
| 1 | `assignee_member_id` / `claimed_at` / `updated_at` | `db.py:1827` 一带 |
| 2 | `target_member` + `target_read` | 既有两行 `INSERT … ON CONFLICT DO NOTHING` |
| 3 | `system` 记录 ①「已认领：{name} 接手了这张任务」 | 既有 `system.task.claimed`，`seq` 记为 `claim_seq` |
| 4 | `target_budget(epoch_seq = claim_seq, used = 0)` | 既有开世代（ADR-0010 三不变） |
| 5 | `task.status: todo → in_progress` | **新增**（私有推进实现，见 D2） |
| 6 | `system` 记录 ②「认领后自动开工：状态推进至 {status}」 | **新增**键 `system.task.claim_started`；其 `seq` = `claim_seq + 1` |
| 7 | 唤醒（由调用方 `claim_new_task` 在事务提交后投递） | 既有 `notify_task_assignment` 路径，reason 改 `claim` |

**任一步失败 ⇒ 全部零改动**（既有 `try/except: self._rollback(); raise` 结构继续兜住）。

**世代标识取 ①**，不取 ②：`epoch_seq` 的既有含义（ADR-0005 / ADR-0010 三 / `CONTEXT.md` **人类世代**）不变。

### D2. 状态推进的合法性：复用迁移表，不复用 actor 校验

`claim_task` 内部走一条**私有**推进实现（同事务 `UPDATE task SET status=…` + 追加记录 ②）：

- **复用** `TASK_STATUS_TRANSITIONS`（`db.py:99`）做合法性判定：`todo → in_progress` 合法；表的其它格子不因此放开。
  实现应按「`in_progress` 必须 ∈ `TASK_STATUS_TRANSITIONS['todo']`」取判，而不是硬写一个 `in_progress` 字面量。
- **不复用** `update_task_status`（`db.py:1602`）的 actor 校验：那条路径要求 actor 是存在的成员、并在
  `in_review → done` 分支拒绝受派者自我批准。认领**不是** actor 发起的动作。
- **不新增 actor 痕迹字段、不改 DDL**。认领的**执行者是编排**；`claimed_at` 记录的是被裁定接手的人。

**author 口径（逐字写准）**：

> `system` 记录的 `author` 恒为 owner，这是**系统事件消息**的既有口径（`db.py` 的 `_append_task_system_message_locked`），
> **不表示人推的**；「人推的」与「编排替它开工」的区别**只在词条键**（`system.task.status` vs `system.task.claim_started`），
> **不在 author**。因此本能力**不新增** actor 痕迹字段、**不改** DDL、**不改** `update_task_status` 的 author 口径。

### D3. 文案与词条（后端两份目录）

| 键 | 动作 | 简中 | English（第二语言取值，参数位一致） |
| --- | --- | --- | --- |
| `system.task.created` | **改正文**，加 `title` 参数 | 任务已创建：{title} | Task created: {title} |
| `system.task.claim_started` | **新增**，参数 `status` | 认领后自动开工：状态推进至 {status} | Claim started the work: status moved to {status} |
| `reason.claim_round` | **新增**，参数 `reason` | {reason}；本轮由**认领**唤醒（编排把这张任务交给你，你必须回应） | {reason}; this round was woken by the claim (orchestration handed you this task, you must reply) |

`status` 参数取既有 `status.task.*`（`i18n.py:92-96` 一带），不新造状态说法。
**双语义务只针对后端 `app/alibot/i18n.py` 的两份目录；不改 `app/alibot/studio/i18n.js`**（前端无新文案，见 D9）。

### D4. 任务本体的注入：消息流 + prompt

**消息流（4a）**：`system.task.created` 正文带标题、**不带状态/频道**。不带状态的理由：本能力要求任务随即被推进，
把「写记录时的状态」固化进 append-only 历史会立刻与紧随其后的 `in_progress` 记录矛盾。**历史不回填**。

**prompt（4b）**：`_system_prompt`（`agent_seam.py:436`）的任务分支（`if task_context is not None:`，`agent_seam.py:454`）
在既有那段工具说明**之前**插入一段「任务上下文」，至少含三句：

1. 任务标题（`任务：{title}`）；
2. 当前状态（`当前状态：{status}`，状态说法走词条）；
3. 受派者身份——**仅当本轮 `agent["id"] == task_context.assignee_member_id`** 时：`你是这张任务的受派者。`
   （非受派者不给这句，避免制造「我是受派者」的假事实。）

**认领轮**（由 D6 的 `mandatory_reply` 驱动，不由 reason 驱动）另加一句：`本轮由认领唤醒，你必须回应。`

**`TaskActionContext` 扩字段（Q5）**：新增 `title: str` / `status: str`（`agent_seam.py:148`），装配点仍是
`_task_action_context`（`orchestration.py:650`）**唯一一处**；`RoundOutcome`（`agent_seam.py:161`）**一字不动**。

### D5. 唤醒 reason 与新常量（不落库）

- 新增 `WAKE_REASON_CLAIM = "claim"`（挨着 `WAKE_REASON_MENTION` / `WAKE_REASON_CHANNEL_MESSAGE`，`orchestration.py:66-67`）。
- 它只进**进程内**轮次参数与 typing 事件（`{"type":"typing", … "reason": reason}`），**不落库**。
- 「本轮由认领唤醒」按 `reason.pierced_suffix`（`orchestration.py:510`）的既有先例，以新键 `reason.claim_round`
  **追加进** `round_outcome.reason` 文本（既有 TEXT 列 ⇒ 无 DDL）。
- `mention` 覆盖 `claim`：`notify` 里 `member_reason = WAKE_REASON_MENTION if member["id"] in mentions else reason`
  （`orchestration.py:229`）——**覆盖不削弱判据**，因为两者都是确定信号。这句话要写进代码注释，否则实施票会把它当 bug 修掉。

### D6. 「必须推进」的判据：显式布尔，不落在 reason 上

**为什么不能读 reason**：同一 Agent 同 target 的重复唤醒**不排队不重放**，收口时合成一条 `merged`（`orchestration.py:416`）；
跨 target 的 deferred 轮次会**继承**参数，而只有 `mention` 会覆盖 deferred 的 reason（`orchestration.py:433-434`）。
于是若判据读 `reason == "claim"`，一条同时到达的普通消息就可能让「必答」**静默失效**——取决于谁先到。

**改成**：`run_round` 增一个进程内参数 `mandatory_reply: bool = False`（**不落库**），由 `_notify_task_assignee`
在 reason 为 `claim` 时算好并沿途传递；`merged` / `deferred` 合并时**取或**：

> 任一方强制 ⇒ 合并后强制。

对照既有合并写法（`orchestration.py:428-435` 的 `consumed_budget` / `pierced` 用 `or`，`reason` 用条件覆盖），
`mandatory_reply` 与 `consumed_budget` / `pierced` 同款：`deferred.mandatory_reply = deferred.mandatory_reply or mandatory_reply`。

**收窄 `reason` 的职责**：`reason` 从此**只**承担「可读诊断 + 正在回复指示」，
**不再**承担纪律判据。这是对既有代码语义的一次收窄，需在 docstring 里写明。

**判据两条**（逐字复用 mention 先例，`orchestration.py:479-494`）：

| 轮次输出 | 判据 | 结果 |
| --- | --- | --- |
| `ignored` | `mandatory_reply` 为真 | ⇒ `failed`，reason 用 `reason.mention_ignored` 的**同级**新文案（见 D3 的 `reason.claim_round` 组合） |
| `replied` 但 `messages` 为空 **且** `task_action is None` | `mandatory_reply` 为真 | ⇒ `failed` |
| `replied` 且有正文 | — | 照常落库 |
| `replied` 且**只有** `task_action`（无正文） | — | **算回应**（不判失败） |

**与 `in_review` 的边界**：本能力**不做**任何自动流转（`in_review → done` 仍只能由指派者之外的成员或 owner 触发）。

**适用范围**：只认领唤醒。owner 手动改指派那一轮 `mandatory_reply=False`，维持既有纪律。

### D7. 三路径读数表（可判定）

| 路径 | 任务终态 | 线程里的 `system` 记录（按 seq） | `epoch_seq` | `used` | 唤醒 | `round_outcome` |
| --- | --- | --- | --- | --- | --- | --- |
| 开关关闭（`auto_claim=0`，缺省） | `todo`，`assignee=NULL`，`claimed_at=NULL` | 仅 `created`（1 条；**新建**任务正文带标题，存量历史不回填） | 0 | 0 | 无 | 无 |
| 有候选但**不可用**（`enabled=0` / 缺凭证 / 未告知远端 / 未配置模型） | 同上 | 同上 | 0 | 0 | 无 | 无 |
| **已认领**（`claimed_at IS NOT NULL` 或 `assignee IS NOT NULL`） | 不动（幂等无操作） | 不动 | 不动 | 不动 | 无 | 无 |
| **正样本**（开关开 + 可用） | `in_progress`，`assignee=认领者`，`claimed_at` 非空 | `created` → `claimed` → `claim_started`（3 条，seq 依次递增） | = `claimed` 那条的 seq | 0 | 1 次（不计跳） | 1 条：`replied` 或 `failed`（**不得**为 `ignored`） |
| **正样本但 wake 时才发现不可用**（PATCH `enabled` 的并发窗口） | `in_progress`（已推进，**不回滚**） | 同正样本（3 条） | 同上 | 0 | 1 次 | 1 条 `failed`，`reason` = 既有 `availability.*` |

> 表中 seq 的具体数字（示例 2 / 3）**是示例值**：`seq` 由 `MAX(seq)+1` 运行期分配。判据是
> 「`epoch_seq` == `claimed` 那条的 seq」与「`claim_started` 的 seq == `epoch_seq + 1`」，**不是**具体数字。

最后一格是**预期可观察结果**，不是 bug：编排的裁决（谁该接手、该不该开工）与「模型此刻能不能跑」是两件事；
`availability` 在裁决阶段判过一次，但 PATCH `enabled` 的并发窗口是真实的。

**存量不回填**：迁移链不做、启动路径不做、本能力不做。见到 `claimed_at IS NOT NULL` 而 `status` 仍是 `todo` 的
存量行，行为是**不动它**——明确的不作为，不是遗漏。活证据：`task_403c97c5ee6a`（`claimed_at=2026-10-16T15:42:56.354Z`，
状态是被 owner 于 15:44:31 **手动**推到 `in_progress` 的）。

### D8. `schema_version` 结论与理由

**结论：不 bump `SCHEMA_VERSION`，不带 `from → to` 迁移步骤。**
**理由**：本能力零 DDL——`message.body` 与 `round_outcome.reason` 都是既有 TEXT 列，新增/改动的词条是**代码**不是库形态；
ADR-0015 的链只覆盖 **schema 形态变更**，「链连续性」这条纪律不适用于「新增一条文案与一个进程内参数」。
`app/alibot/migrate.py` 的步骤注册表**无新边**。

### D9. 前端

**零改动**：`system` 行由通用渲染承担（`studio/app.js:1293` 的「状态变更记录」按 `kind === 'system'` 过滤），
`round_outcome.reason` 按**原文**呈现（`studio/app.js:3337`，不重新措辞、不截断）。可观察后果两条：
任务线程的「状态变更记录」会多一行；`round_outcome` 展开后的 reason 多一句后缀。**不改** `studio/i18n.js`。

### D10. 与 ADR-0010 / ADR-0005 / ADR-0002 / ADR-0006 的边界

| ADR | 处置 |
| --- | --- |
| ADR-0010 一（编排发起、不扩**输出**形状） | **不变**；本能力只扩输入侧任务上下文 |
| ADR-0010 二（认领不是跳） | **不变**；认领仍不计跳 ⇒ 预算耗尽世代里照常投递 |
| ADR-0010 三（认领开一次世代） | **不变**；世代标识仍取「已认领」那条的 seq |
| ADR-0010 四（每张任务至多一次） | **不变**，且是本能力仍然可执行的前提 |
| ADR-0010 「认领只落结构事实 / 不改状态」 | **被扩展**：认领现在是开工动作（本能力的核心） |
| ADR-0005（唤醒的载体是指派） | **不变**；动作表多一格，原文不改 |
| ADR-0002（三跳上界） | **不变**；理由链见 ADR-0016 七 |
| ADR-0006（当前态在行上、变更史在流里） | **直接应用**：认领既改行又留两条记录 |

**ADR-0010 原文一字不改**（沿用 ADR-0005 / ADR-0007 处置旧 ADR 的既有惯例）；边界写在新 ADR-0016 里。

---

## Testing Decisions

### T1. 好测试的判据

只断言**外部可观察行为**：HTTP 读数（任务行、线程消息、成员表、预算）、`round_outcome` 行、injected seam 的
调用参数（`task_context` 与 prompt 产物）。**不**断言私有函数名、SQL 文本、`_in_flight` 的内部结构。

### T2. 接缝（沿用既有，不新增）

唯一接缝是**既有的** HTTP 黑盒 + 脚本化替身（`app/tests/conftest.py` 的 `harness["seam"]` /
`harness["scheduler"]`），与 `test_auto_claim.py` 今天用的**同一个**。本能力不新增接缝：
prompt 侧断言走替身已经收到的调用参数（替身需要记录合成后的 system prompt —— 这是对既有测试替身的**字段补全**，
不是新接缝；若替身今天不记 prompt，实施票可断言 `_system_prompt` 的产物的**可观察片段**）。

### T3. 行为契约 S1–S17

每条给【契约】与【可判定读法】。**S13 的断言变更是实施票的事，本正本只登记。**

| # | 契约 | 可判定读法 |
| --- | --- | --- |
| **S1 单事务** | 正样本认领在**一个事务**内完成 D1 的 7 件事（含状态推进与记录②） | 注入失败（如让记录② 的插入抛错）⇒ 任务行、`target_member`、`target_read`、`target_budget`、线程消息**全部零改动** |
| **S2 推进合法性** | 只接受 `TASK_STATUS_TRANSITIONS['todo']` 里的目标；复用该表判定、**不复用** actor 校验；执行者是编排；不新增 actor 痕迹字段 | 把 `todo` 的合法目标改成 `{'closed'}` ⇒ 认领**失败且零改动**；`task` 表列集不变 |
| **S3 记录键** | 新键 `system.task.claim_started`，正文「认领后自动开工：状态推进至 {status}」；**不**复用 `system.task.status` | 线程第 3 条正文与 `i18n.render('system.task.claim_started', locale, status=render('status.task.in_progress', locale))` **逐字相等** |
| **S4 created 带标题** | `system.task.created` 正文「任务已创建：{title}」（同键 + `title` 参数）；**历史不回填** | 新建任务第 1 条正文逐字等于渲染结果；改标题**不回改**它；存量任务的旧正文不变 |
| **S5 prompt 注入** | 任务轮次的 system prompt 含**标题**、**当前状态**、**受派者身份句**（仅当本轮 Agent == assignee）；认领轮另含「本轮由认领唤醒，你必须回应」。频道 / DM 轮次**不含**任何一项 | 替身收到的 prompt 断言含标题与身份句；非受派者的任务轮次**不含**身份句；频道轮次不含任务上下文 |
| **S6 `TaskActionContext`** | 新增 `title` / `status`；装配点唯一；`RoundOutcome` 字段集**不变** | 替身收到的 `task_context.title/status` 与库内任务行相等；`dataclasses.fields(RoundOutcome)` 的集合与基线相同 |
| **S7 reason 常量** | 新增 `WAKE_REASON_CLAIM = "claim"`；**不落库**；可读后缀以 `reason.claim_round` 追加进 `round_outcome.reason`；`reason` 不再承担纪律判据 | 正样本那一轮的 `round_outcome.reason` 含该后缀；`round_outcome` 表的列集不变；typing 事件的 `reason == "claim"` |
| **S8 必须推进** | 纪律由显式布尔 `mandatory_reply` 承载（不落库），合并时**取或**；判据逐字复用 mention 两条；动作-only **算回应**；**只圈认领轮** | `IGNORE` 替身 ⇒ 认领轮 `round_outcome.status == 'failed'`；「`replied` 且无正文无动作」⇒ `failed`；「只有 `task_action`」⇒ `replied`；owner 改派轮仍是 `ignored` |
| **S9 预算与世代** | 认领不计跳 ⇒ 耗尽世代里仍投递、仍必须回应；`used` 认领后为 0；`epoch_seq` = `claimed` 那条的 seq；**每张任务至多一次** | 既有三条预算读数继续绿（`used==0` / `epoch_seq==claim_seq` / 二次触发不前进）；在耗尽世代下建任务：仍投递、仍必须回应 |
| **S10 三路径** | 见 D7 的**五格**读数表，逐格成立 | 五格逐格按表断言（含第五格的「状态已推进而那一轮 failed」） |
| **S11 双语义务** | 新增/改动键 = `system.task.created`（改正文 + `title` 参数）、`system.task.claim_started`（新）、`reason.claim_round`（新）。**只针对后端** `app/alibot/i18n.py` 的两份目录；**不改** `app/alibot/studio/i18n.js` | 两份目录同键集、同参数位（既有 `test_backend_copy.py:69` 那条守）；ZH/EN 各写一次比正文不同；前端词条表零改动 |
| **S12 schema** | **不 bump**、**不带** `from → to` 步骤；理由 = 零 DDL + ADR-0015 的链只覆盖 schema 形态变更 | `SCHEMA_VERSION` 不变；`migrate.py` 注册表无新边；链连续性测试绿 |
| **S13 断言变更** | 见 D11 的对照表；实施顺序**先改断言 → 看它红 → 再改实现** | 改断言后该测试**红**（红→绿证据），再改实现转绿 |
| **S14 词条落地** | 七处 inline 落进 `CONTEXT.md` 的 `## Language`，无占位符 | 占位扫描命令（含四个占位词）在 `CONTEXT.md` / 本文 / ADR-0016 里对**正文**无命中（读数见 A5） |
| **S15 ADR 处置** | 新增 `docs/adr/0016-*.md`；**ADR-0010 原文一字不改** | `git diff main -- docs/adr/0010-claiming-a-task-opens-one-epoch-per-task.md` 为空 |
| **S16 前端** | 零改动；可观察后果 = 线程多一行 + reason 多一句后缀 | 人工读数两处；`studio/i18n.js` 零 diff |
| **S17 明确不做** | `in_review` 自动流转、存量回填、措辞质量（#35/#57/#58）、`silent` 落地（#58）、owner 改派的必答、前端新交互 | 见 Out of Scope |

### T4. 既有测试断言的预期变更（**实施票**执行）

> 行号基于 base `63e8163`；实施票开工时须**重新 `grep` 复核**（`main` 可能已前进）。

| 落点 | 旧断言 | 新断言 | 指回 |
| --- | --- | --- | --- |
| `test_auto_claim.py:128` | `assert task["status"] == "todo"` | `== "in_progress"` | S1 / U1 |
| `test_auto_claim.py:166-168` | `records == [created, claimed]` | `[created(带标题), claimed, claim_started]` | S1 / S3 / S4 |
| `test_auto_claim.py:170` | `thread(...)[1]["mention_snapshot"] == []` | 索引改 `[2]`（`claim_started`）；断言不变 | S1 |
| `test_auto_claim.py:196` | `status == "todo"`（开关关闭） | **不变**（无认领 ⇒ 仍 `todo`） | S10 |
| `test_auto_claim.py:198` | `["任务已创建"]` | `["任务已创建：整理验收清单"]` | S4 |
| `test_auto_claim.py:268` | `["ignored"]` | `["failed"]` | S8 |
| `test_auto_claim.py:281` | `["system", "system", "agent"]` | `["system", "system", "system", "agent"]` | S1 |
| `test_tasks.py:40` | `["任务已创建"]` | `["任务已创建：{title}"]` | S4 |
| `test_tasks.py:134` | `'"body":"任务已创建"'` | 带标题的新正文 | S4 |
| `test_backend_copy.py:161/162/383/390/400` | `render("system.task.created", ZH)` | 加 `title=` 实参（断言形态不变） | S4 / S11 |
| `test_backend_copy.py:69` | 两份目录同键集 / 同参数位 | **不改**（自动覆盖新键） | S11 |

**为什么这不是「为了让测试绿而改测试」**：上表每一行都承载一条**旧契约**（旧契约说「认领不改状态」「created 不带标题」），
契约由用户在 2026-10-17 改定后它们就该跟着改；判据是**每条改动都能指回本正本的一条决策**（最后一列）。
实施顺序硬纪律：**先改断言 → 看它红 → 再改实现**（红→绿证据，不是事后凑绿）。

---

## Out of Scope

- **不实现**：不改 `app/alibot/**`、不改测试、不 bump `SCHEMA_VERSION`、不写迁移步骤。
- **不做 `in_review` 自动流转**：本票**不做**任何 `in_review → done` 的自动推进，那一格的「独立审阅者」语义不变。
- **不做存量回填**：迁移链、启动路径、本能力都不回填「已认领但状态是 `todo`」的存量行。
- **不收敛措辞质量**：「说了等于没说」（旁白、括注）归 #35 / #57 / #58；本能力只判定「有没有回应」。
- **不落地 `silent`**：`silent` 是 #58 的范围（见 Further Notes 的不一致登记）。
- **不扩 owner 改派的必答**：owner 手动改指派那轮维持既有纪律（登记为同类缺口，见 ADR-0016 代价栏）。
- **不改前端**：`studio/**` 零改动，含 `studio/i18n.js`。
- **不改 `system` 记录的 author 口径**，**不新增 actor 痕迹字段**，**不改 DDL**。
- **不拆实施票**：本文的交付终点是设计正本；拆票属 `to-tickets`。

---

## Further Notes

### N1. 已登记的不一致：`silent` 在 base 代码里不存在

`CONTEXT.md` 的**轮次结果** / **沉默**词条与 ADR-0008 都把 `silent` 写进词表（「status 词表共六个」），
但基线 `63e8163` 的代码里**三处枚举都没有它**：

| 落点 | 实际值 |
| --- | --- |
| `db.py:108` `ROUND_STATUSES` | 五态（`replied` / `ignored` / `failed` / `merged` / `budget-exhausted`） |
| `round_outcome` DDL 的 `status` CHECK（`db.py:406`） | 同上五态 |
| `agent_seam.py:33` `SEAM_STATUSES` | 三态（`replied` / `ignored` / `failed`） |

而 **#58**（「结构化沉默信号 silent + RoundOutcome 互斥校验」）仍 OPEN。⇒ **本能力的判据只建在现有三态上**；
`CONTEXT.md` 的两处新句写成「`silent`（#58 落地后）同样适用」这样的**前瞻式**表述，不假装它已在。
这条不一致**归 #58**，本能力不修、不因它改语义。给后续接手 #58 的人一条可核入口：以 `db.py:108` / `db.py:406` /
`agent_seam.py:33` 三处枚举与 `SCHEMA_VERSION` 的现状为准。

### N2. 既有留白：「系统事件消息」词条的来源清单不含认领

`CONTEXT.md` 的**系统事件消息**词条把来源写作「任务创建 + 任务变更（改标题 / 改状态 / 改指派）」。
`system.task.claimed` 在 ADR-0010 落地时就**没有**进这份清单；本能力新增的 `claim_started` 属于「任务变更」的既有一格
（actor 是编排而不是人，但来源仍是任务变更），因此**同样不进**。**这是既有留白，不是本能力的决策**——词条按用户裁定只改七处，
该词条不在其中。

### N3. 与「人类世代」这个名字的关系

ADR-0010 已登记「人类世代」这个名字不再字面准确（三种开世代动作里有一种由编排发起）。本能力**没有**新增第四种开世代动作
（认领仍只是那一次），但让「认领」这个动作本身多背了两件事（推状态、要求必答）。名字不改，代价继续由 ADR-0010 与本能力的代价栏承担。

### N4. 一句话版本的现场因果链

> 任务标题没进消息流也没进 prompt ⇒ 模型只能看到「某人认领了这张任务」这条第三人称记录 ⇒ 它正确地判断出
> 「我不是受派者、也没人点名我」⇒ 按既有 rubric 保持沉默 ⇒ 用户看到「认领了但没开始」，且那一轮还是 `replied`（不是失败）。

本能力在这条链的**三个环**各补一处：消息流带标题（环 1）、prompt 带本体与身份（环 2）、认领轮必须回应（环 3）。

---

## Answer（本票收口：grill 轮次记录 + 交付物 + 验收自查）

### A1. grill 轮次记录（每轮的问、谁答、答案、依据）

三轮 grill 全部经 `orca orchestration ask` 发出，coordinator 逐轮回复；人定项由 coordinator 转呈用户后带回原话。

#### 第 1 轮（8 问：Q1–Q8 + 两条自判）

| 问 | 分类 | 谁答 | 答复 |
| --- | --- | --- | --- |
| Q1 状态推进的落点与记录形态 | 细节类（落点 / 事务边界） | coordinator 代答 | **(B)** `claim_task` 同事务 UPDATE + 同事务追加 status 记录。依据：`studio/app.js:1293,1379` 的「状态变更记录」只渲染 `system` 行，只 UPDATE 不落记录会让看板说进行中而线程查不到原因 |
| Q2 「必须推进」的判据 | 决策类 | **人定** | **(A)** 逐字复用 mention 两条；动作-only 算回应。原话：「与『被 @点名』**同一套判据、同一处落点**，不新造第二条纪律」 |
| Q3 强制必答的适用范围 | 决策类 | **人定** | **(A)** 只圈认领唤醒；owner 改派维持既有纪律。同要求把「owner 改派 + 沉默」登记为未完成项 |
| Q4a `system.task.created` 正文 | 细节类（文案） | coordinator 代答 | **(A)** 「任务已创建：{title}」 |
| Q4b prompt 侧注入形态 | 细节类（落点） | coordinator 代答 | **(A)** 新增「任务上下文」段 + 认领轮显式声明 |
| Q5 `TaskActionContext` 扩不扩 | 边界判断 | coordinator 代答 | **(A)** 扩输入侧 `title` / `status`；输出形状不动；**必须**同步改接缝词条 |
| Q6 reason 常量 + schema 结论 | 细节类（命名）+ 事实 | coordinator 代答 | **(A)+(C)** 新常量 + 可读后缀；**不 bump**，要求「写结论 + 写理由」；`claim` 被 `mention` 覆盖不削弱判据 |
| Q7 与 ADR-0002 三跳上界 | 决策类 | **人定** | **(A)** 维持「每任务至多一次」足够、认领不计跳；新增代价登记进 ADR |
| Q8 既有测试断言处置 | 细节类（测试写法） | coordinator 代答 | 采纳 + 三条硬要求：对照表 / 先改断言看红 / 行号复核 |
| 自判 ①：ADR 取 0016 | — | coordinator 认可 | 已扫全部 `refs/remotes/origin/*` 与 `main`：`docs/adr/` 最大 0015，无分支占用 0016+ |
| 自判 ②：正本落 `claim-starts-work-design-notes.md` | — | coordinator 认可 | 沿用 `schema-migration-design-notes.md` 体例 |

**本轮票面外发现（coordinator 采信）**：`CONTEXT.md` / ADR-0008 写了 `silent`，但 `main` 代码里不存在（三处枚举都没有，`#58` 仍开）
⇒ 本能力判据只建在现有三态上（登记见 N1）。

#### 第 2 轮（6 问：Q9–Q14，由第 1 轮答复引出）

| 问 | 分类 | 谁答 | 答复 |
| --- | --- | --- | --- |
| Q9 认领推进用哪个词条键 | 细节类（文案 / 键命名） | coordinator 代答 | **(B)** 新键 `system.task.claim_started`；既有键继续只表示「有人推了状态」；将来再加编排自动动作照此模式加键 |
| Q10 绕不绕 actor / 迁移表校验 | 决策类 | **人定** | **(A)** 私有推进实现：**复用** `TASK_STATUS_TRANSITIONS`、**不复用** actor 校验；执行者是编排；不新增 actor 痕迹字段。那句纪律**写进 ADR 原文** |
| Q11 必答判据 × in-flight 合并 | 细节类（判据落点） | coordinator 代答 | **(B)** 显式布尔 `mandatory_reply`（不落库），合并时**取或**；`reason` 职责收窄为「可读诊断 + typing」；deferred 也要传 |
| Q12 必答 × 预算耗尽 | 细节类（三路径对照） | coordinator 代答 | **(A)** 不受影响；写成 ADR-0010 二的**直接推论**，并写明「不存在『因为预算耗尽所以认领轮可以沉默』这条路」 |
| Q13 三路径行为 | 细节类（对照表） | coordinator 代答 | 三条采信 + 三条硬要求（第五格必须写「可能发生」/ 存量不回填点名活证据 / 写成可判定读数表） |
| Q14 `CONTEXT.md` 改哪几处 | 决策类 | **人定** | **七处**：认领 / 任务状态 / 人类世代 / 成员面适配接缝 / 唤醒 / 回应判断 / 沉默；**轮次结果不改**；`silent` 那句写成前瞻式 |

#### 第 3 轮（收口确认：七处正文 + 三路径表 + S1–S17）

| 内容 | 谁答 | 答复 |
| --- | --- | --- |
| §一 七处词条**新正文**（逐条贴出） | coordinator 代答（确认） | **全部确认**，coordinator 复核了 7 处引用的代码事实（`db.py:98-105` / `i18n.py:92-96` / `CONTEXT.md:76-81` / `test_auto_claim.py` 六处行号），行号可用 |
| §二 三路径读数表（五格） | coordinator 代答（确认） | 确认 + 三处加固：开关关闭那格限定「**新建**任务」、seq 数字标注为示例、`epoch_seq`/`claim_started` 的判据写成相对关系而非数字 |
| §三 S1–S17 契约 | coordinator 代答（确认） | 确认 + **加固 2 处**：S2/S3 必须写清「`system` 记录的 `author` 恒为 owner，不表示人推的」；S11 必须指名「只针对后端 `i18n.py` 两份目录，不改 `studio/i18n.js`」 |
| §四 frontier 已空的四条理由 | coordinator 代答（确认） | **全部确认**，frontier 为空 |
| 「系统事件消息」词条不改 | coordinator 确认 | 确认；要求在 Further Notes 写清**既有留白**（`system.task.claimed` 当年就没进清单），已落 N2 |
| `silent` 不一致的登记位置 | coordinator 确认 | 放 Further Notes（不放 Implementation Decisions）；要求写明三处枚举的具体落点，已落 N1 |
| S13 对照表 | coordinator 确认 | 行号已核；要求表头加「基于 base `63e8163`，实施票须重新 grep」，已落 T4 |
| additional 确认 | coordinator | 「系统事件消息」词条不改 = 确认；七处词条只改那七处（`git diff --numstat CONTEXT.md` 应与七处量级相称，见 A3 读数） |

**三处加固的落点**：加固 1 → D2（author 口径逐字段落）；加固 2 → D7 表格「开关关闭」格 + T4 表头；
加固 3 → D7 脚注（seq 示例值 + 相对判据）；加固 4 → D3 末句 + S11。

#### frontier 为空的四条理由（第 3 轮提交，coordinator 全部确认）

1. **认领轮被合并（merged）时会不会丢必答**：**构造上不可达**——认领只发生在新 task target 上
   （`create_task` 当场新建 target，`claim_new_task` 只被 `service.create_task`（`service.py:238`）在紧邻的下一行调用），
   该 target 上不可能已有在飞轮次；已认领的任务 `claim_new_task` 在读库前置（`orchestration.py:249`）就返回 0。
   为防将来复用，S8 仍写死「合并时取或」。
2. **同轮被 `mention` 覆盖**：已写进 `CONTEXT.md` **回应判断**词条（同级、覆盖不削弱判据）与 D5。
3. **`silent` 不存在的不一致**：登记在 N1（归 #58，本能力不修、不假装存在、不因它改语义）。
4. **剩下的都是文案措辞与实现细节**：属代答档（coordinator 已代答并确认）。

### A2. 交付物

| 文件 | 动作 |
| --- | --- |
| `docs/adr/0016-claiming-a-task-starts-the-work.md` | **新增**：七节 + 代价栏；「扩展，不是推翻」承接 ADR-0010 |
| `CONTEXT.md` | 改**七处**词条（认领 / 任务状态 / 人类世代 / 成员面适配接缝 / 唤醒 / 回应判断 / 沉默） |
| `app/specs/claim-starts-work-design-notes.md` | 本文件：决策账 + S1–S17 + 三路径表 + 断言对照表 + Answer |
| `app/specs/claim-starts-work-pr-body.md` | PR 正文（按 `pr` skill 体例，`--body-file` 用） |

**没有**第二张票的范围：未动任何 `app/alibot/**`、未动任何测试、未 bump `SCHEMA_VERSION`、未开 GitHub issue
（票面 Constraints：本票由 coordinator 派发，正本就是 worktree 内的文档 + PR）。

### A3. 可观察验收自查（逐条对票面 §七）

| 票面验收 | 本票落点 |
| --- | --- |
| `docs/adr/` 下存在新 ADR，章节结构与既有 ADR 一致 | `0016-claiming-a-task-starts-the-work.md`（七节 + 代价栏；沿用 ADR-0015 的分节写法）。三条件逐条成立见 A4 |
| `CONTEXT.md` 三处（+ 其它受影响词条）已与 U1/U2/U3 对齐，无占位符 | 改七处；占位扫描对正文无命中（读数见 A5） |
| `app/specs/` 设计正本含 S1…Sn 且覆盖 §四 七个 frontier | S1–S17（T3）覆盖票面 §四 七问：Q1→S1/S2/S3、Q2→S8、Q3→S5/S6、Q4→S7、Q5→S12、Q6→S9、Q7→T4 |
| Answer 有完整 grill 轮次记录（每轮问了什么、谁答、答案与依据） | A1 三轮表 |
| design tree frontier 为空 | A1 末节的「frontier 为空的四条理由」（第 3 轮 coordinator 全部确认） |
| 项目文件有改动 / PR | A2 + PR（号回填见 A6） |
| `git status` 与 diff 检查 | A5 读数 |

### A4. ADR 三条件（domain-modeling）逐条

| 条件 | 判定 | 依据 |
| --- | --- | --- |
| **难以回头** | 成立 | 认领是否推状态、是否要求必答一旦落地就有存量任务与存量 `round_outcome` 行依它取证；回退会让「已开工」变回 `todo`（不可逆的历史语义），且预算/世代的边界与它耦合 |
| **没有上下文会让人困惑** | 成立 | 读代码的人会问两个问题：为什么认领（一个指派动作）要改状态？为什么它**不计跳**却**必须回应**？答案只在 ADR-0010 + 本 ADR 的边界里 |
| **是真权衡的结果** | 成立 | 单事务 vs 两条记录、私有推进 vs 复用 `update_task_status`、新键 vs 复用键、判据落 reason vs 落显式布尔——每条都有被否的替代方案与代价（ADR-0016 三、五、D1/D2/D6） |

### A5. 实测读数（本会话）

| 项 | 读数 |
| --- | --- |
| 占位扫描 | `grep -nE '待定\|待决\|未决\|⏳' CONTEXT.md app/specs/claim-starts-work-design-notes.md docs/adr/0016-*.md` 无命中（exit 1） |
| `CONTEXT.md` 改动量 | `+23 / −10`（七处词条，与「七处」量级相称） |
| `git status` | 仅四个 Markdown 路径（`CONTEXT.md` 修改 + 三个新增） |
| 源码改动 | **0**（未动 `app/alibot/**`、未动 `app/tests/**`、未 bump `SCHEMA_VERSION`） |
| ADR-0010 原文 | `git diff main -- docs/adr/0010-claiming-a-task-opens-one-epoch-per-task.md` 为空 |

### A6. PR 与收尾

| 项 | 值 |
| --- | --- |
| 分支 | `claim-starts-work`（已推送，`-u origin`） |
| 本票 PR | [#104](https://github.com/whutlichao/alibot/pull/104)（base `main`，head `claim-starts-work`） |
| PR 正文 | `app/specs/claim-starts-work-pr-body.md`（`--body-file` 直接取仓库内路径，随本票一起被跟踪） |

**为什么不新开 issue**：本票由 coordinator 派发，票面 Constraints 已定正本就是 worktree 内的设计文档 + PR。

### A7. 回填读数（本会话实测）

| 项 | 读数 |
| --- | --- |
| 提交 | `b46f211`（docs(specs): 认领即开工设计（ADR-0016 + 七处词条 + S1–S17 契约）） |
| 分支推送 | `origin/claim-starts-work`（新分支） |
| PR | `#104`，`state=OPEN`，`head=claim-starts-work`，`base=main`，`headRefOid=b46f211`（= 本地 HEAD） |
| PR 改动文件 | `CONTEXT.md` / `app/specs/claim-starts-work-design-notes.md` / `app/specs/claim-starts-work-pr-body.md` / `docs/adr/0016-claiming-a-task-starts-the-work.md` |
| 本票门禁 | **G-docs**（设计票）；**未跑** pytest / ruff / mypy / pyright / `node --test`，**未跑**双轴 code-review（理由：diff 只有 Markdown，双轴 review 审的是源码 diff） |
