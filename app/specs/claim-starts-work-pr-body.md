## Summary

把「认领」从**只落结构事实**扩成**编排发起的开工动作**，并把这个边界写成三份正本。

```text
新建任务（owner HTTP）
  create_task                      → 线程 created(带标题)
  claim_new_task（编排裁决）
    候选 = 该频道可用 Agent（auto_claim 开 + availability.ok）
    认领事务（单事务，任一步失败 ⇒ 全部零改动）
      指派 + claimed_at + 成员面 + 已读
      system ①「已认领」                ← epoch_seq 取它的 seq
      status todo → in_progress
      system ②「认领后自动开工」          ← 新键 system.task.claim_started
    投递一轮（reason=claim，mandatory_reply=True，不计跳）
      prompt：任务上下文（标题 / 状态 / 受派者身份）
              + 「本轮由认领唤醒，你必须回应」
      收口：replied 或 failed（ignored、无正文无动作 ⇒ failed）
```

三条用户已定的裁定（U1/U2/U3）各自落在哪：

| 裁定 | 落点 |
| --- | --- |
| **U1 认领即开工** | 认领事务内推进 `todo → in_progress`（复用 `TASK_STATUS_TRANSITIONS`、不复用 actor 校验），落新键 `system.task.claim_started` |
| **U2 任务本体** | 消息流：`system.task.created` 带标题；prompt：新增「任务上下文」段；`TaskActionContext` 扩**输入侧** `title`/`status`（输出形状不动） |
| **U3 必须推进** | 判据落**显式进程内布尔** `mandatory_reply`（不落库，合并时取或）；`ignored` ⇒ `failed`、`replied` 无正文无动作 ⇒ `failed`；动作-only 算回应 |

改动文件（4 个，全是 Markdown）：

```text
docs/adr/0016-claiming-a-task-starts-the-work.md      新增（ADR：「扩展，不是推翻」承接 ADR-0010）
CONTEXT.md                                            改七处词条（+23 / −10 行）
app/specs/claim-starts-work-design-notes.md           新增（设计正本：决策账 + S1–S17 + 三路径表）
app/specs/claim-starts-work-pr-body.md                新增（本文件）
```

## Evidence

本票是**设计票**（门禁 G-docs），**不跑**源码门禁、**不跑**双轴 code-review（它审源码 diff，本票 diff 只有 Markdown）。

- **Before:** `main` = `63e8163`；`Database.claim_task`（`db.py:1784`）只写 6 件事、**不碰 `task.status`**；
  `test_auto_claim.py:128` 把 `status == "todo"` 断言成契约；任务线程里只有「任务已创建」（不带标题）与「已认领：小张 接手了这张任务」；
  `_system_prompt`（`agent_seam.py:436`）的任务分支无标题、无状态、无受派者身份；认领唤走的 reason 是 `channel-message`。
  **现场失败读数**（`~/Alibot/alibot.db` 只读副本）：`task_403c97c5ee6a` 的线程第 3 条是模型自己说
  「this task was claimed by 小张, not me, and no one has addressed me directly — so I should stay silent」，
  第 4 条（推到进行中）是 **owner 手动**的；标题「分析一下深圳明天的天气。」从未出现在任何一条消息里。
- **After:** 设计正本给出 S1–S17 逐条可判定契约（`app/specs/claim-starts-work-design-notes.md` T3），
  含三路径**五格**读数表（D7）、既有断言**对照表** 11 行（T4，每条指回一条正本决策）、`CONTEXT.md` 七处新正文（A1 第 3 轮）。
  收尾自查读数：占位扫描无命中、`CONTEXT.md` 净增 13 行（七处量级）。

验收自查（逐条对票面 §七）：

| 票面验收 | 读数 |
| --- | --- |
| 新 ADR 存在、章节与既有 ADR 一致 | `docs/adr/0016-claiming-a-task-starts-the-work.md`，七节 + 代价栏（沿用 0015 的分节写法） |
| ADR 三条件 | 难以回头 / 无上下文会困惑 / 真权衡，逐条见正本 A4 |
| `CONTEXT.md` 七处对齐、无占位 | `grep -nE '待定\|待决\|未决\|⏳'` 三个文件均无命中 |
| 设计正本含 S1…Sn、覆盖 §四 七问 | S1–S17；映射见正本 A3 |
| Answer 有完整 grill 轮次记录 | 三轮表（8 问 + 6 问 + 收口确认），逐条标「人定 / 代答」 |
| frontier 为空 | 四条构造性理由（正本 A1 末），coordinator 第 3 轮确认 |
| ADR-0010 原文不改 | `git diff main -- docs/adr/0010-*.md` 为空 |

## Merge Danger

**Door:** two-way

纯文档改动，无 DDL、无 `SCHEMA_VERSION` 变更（S12）、无源码、无测试、无前端。回退就是 revert 这一个提交。

**Blast Radius:** isolated（Markdown only）

合并后的下游影响：

- **下一张实施票**（`to-tickets` 产出）直接消费 S1–S17；它必须按 T4 的**硬纪律**开工：**先改断言 → 看它红 → 再改实现**。
  改断言不是「为凑绿」，每条改动都指回一条正本决策（T4 最后一列）。
- **`CONTEXT.md` 是承重文档**：本次只改七处（+23 / −10 行）。合并后若有人发现某处需要大改，应另开票而不是就地重写。
- **一处已登记的不一致（不属本票）**：`CONTEXT.md` / ADR-0008 写了 `silent`，但 `main` 代码三处枚举都没有它（`db.py:108`、
  `db.py:406` 的 DDL CHECK、`agent_seam.py:33`），#58 仍开。本票的判据只建在现有三态上，`silent` 相关的新句一律写成
  「（#58 落地后）」的前瞻式表述。**不要**把这条读成本票的缺陷。
- **一处同类缺口（登记不做）**：owner 手动改指派 + 受派者沉默这一格不在本票内（U3 原句只圈认领唤醒），
  已登记在 ADR-0016 的代价栏，是否另立票由用户决定。
