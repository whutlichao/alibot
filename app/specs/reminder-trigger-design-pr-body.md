## Summary

设计票（G-docs）。**零源码改动**：只新增设计笔记与 ADR-0017，并修订 `CONTEXT.md` 两个词条 + 新增一个词条。
本票把「提醒要不要、以及如何到点触发」收敛成可实施的设计，D1–D9 九条决策面全部有主，frontier 为空。

```text
docs/adr/
├── 0012-reminders-are-backend-objects.md          ← 正文零改动（git diff 为空）
└── 0017-reminders-fire-into-the-message-stream.md ← 新增：首段逐句声明取代 0012 的哪三句

app/specs/
└── reminder-trigger-design-notes.md               ← 新增：§0 事实 / §1 决策账 / §2 被否 / §3 规格输入 / §4 落点 / §5 Answer

CONTEXT.md
├── 系统事件消息  ← 加第四来源「提醒到点」
├── 提醒          ← inline 改为新口径（可到点 / 一次性日期+钟点 / 本地时区 / fired 标记）
└── 到点          ← 新增词条（含 _Avoid_）
```

到点的产物（D1 人定）是一次结构性推论，不是随手选的一项：

```text
notify(target_id, from_seq)          orchestration.py:183
  get_message(target_id, from_seq)   ← 必须先有一条已落库的消息
  append_message(...) 也要求真实 target  db.py:2181

⇒ 「唤醒一个 Agent」必然蕴含「先落一条消息」
⇒ 到点 = 落一条 system 消息（不唤醒、不耗预算）
```

## Evidence

**D1–D9 逐条五项齐全，7 条人定可追到具体 ask 答复**（设计笔记 §1.1 / §1.2）：

| 轮 | messageId | answerMessageId | 结论 |
| --- | --- | --- | --- |
| 1 | `msg_81719459bdae` | `msg_d2335fcb15a9` | Q1–Q4 全按推荐（Q1/Q2/Q3 人定，Q4 代答） |
| 2 | `msg_12138ac93caa` | `msg_a63c3867f8db` | Q5–Q8 全按推荐（四条人定，Q8 逐句表整表通过） |
| 3 | `msg_36620f744ace` | `msg_441d710cdda9` | Q9–Q12 全按推荐（Q9/Q10/Q11 人定，Q12 代答） |

第一轮曾超时一次（`answer: null`），按纪律用同一 message ID `--resume` 续等，未重复提问。

**ADR-0012 与其它既有 ADR 零改动：**

```text
$ git diff origin/main -- docs/adr/0012-reminders-are-backend-objects.md
（空）

$ git diff --stat origin/main
 CONTEXT.md                                         |  14 +-
 app/specs/reminder-trigger-design-notes.md         | 581 +++++
 docs/adr/0017-reminders-fire-into-the-message-stream.md | 157 +++
 3 files changed, 747 insertions(+), 5 deletions(-)
```

**`CONTEXT.md` 只动了该动的两段**（PR #104 改的唤醒 / 回应判断 / 沉默 / 成员面适配接缝 / 人类世代 / 认领 / 任务状态 七处未碰）：
`git diff origin/main -- CONTEXT.md` ⇒ 9 insertions / 5 deletions，全部落在**系统事件消息**与**提醒**区块。

**基线偏差已如实登记**：spec 给的基线是 `main = 63e8163`，但开工时 `origin/main` 已是 `04eccc9`（PR #104）、
收尾时已是 `2ccf6fb`（PR #106）。`04eccc9` **已占用 `docs/adr/0016-*.md`** ⇒ 新 ADR 改取 **0017**；两次 `git rebase origin/main` 后再交付。

**没有改动的东西（可逐条核）**：`SCHEMA_VERSION` 仍是 `12`；`app/alibot/**` 与 `app/tests/**` 零改动；
没有迁移步骤代码；没有跑源码测试与双轴 code-review（G-docs 本票不跑）。

## Merge Danger

**Door:** two-way

纯文档 PR。回滚就是 revert 一个 commit，没有数据迁移、没有 API 契约变更、没有运行时行为变化。
`SCHEMA_VERSION` 的 12 → 13 只**写进设计**（D7），实际 bump 与 `MigrationStep(12, 13)` 登记留给实施票。

**Blast Radius:** docs-only

需要留意的下游（都不在本 PR 内发生）：

- **#68（⑧ 提醒 reminders，OPEN）** 的票面写「不落消息、不唤醒」，与本设计冲突。本 PR 不改 #68；
  实施票应在 #68 的 Answer 或新建实施票里引用 ADR-0017 并声明该条被取代。
- **#95（链连续性守卫测试）** 会在实施票 bump 到 13 时自动覆盖 `12 → 13`——S11 的判据正是它。
- **#103（Studio 左栏入口对齐）** 管 rail 入口位置，本设计管指示点**判据**（S17），不冲突。
- `CONTEXT.md` **提醒**词条的新口径（到点落消息）与 `app/alibot/studio/index.html:656-659` 的既有注释
  （「到期触发不在本票授权范围内」）相互矛盾——S19 已把它登记为实施票必须同步处置的落点。
