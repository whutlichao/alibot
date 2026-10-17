<!-- 本文件是 GitHub issue #111 的 Answer 评论留档；tracker 上的评论才是正本，以 tracker 为准。 -->

# Answer —— 拆分票 #111（提醒到点触发的实施票拆分）

> 正本：https://github.com/whutlichao/alibot/issues/111 ｜ 仓库：whutlichao/alibot ｜ 2026-10-17

## 1. 交付物

| 对象 | 动作 |
| --- | --- |
| GitHub issue **#111**（本票） | **新建**：`Part of #1` 首行、`ready-for-agent`；票型 票据拆分 / 门禁 G-tickets |
| GitHub issue **#112** = ① | **新建**：库形态与迁移，覆盖 **C4 C5 C11 C12 C13 C14**，`blocked_by 0` |
| GitHub issue **#113** = ② | **新建**：到期投递器 seam，覆盖 **C1 C2 C3 C6 C7 C8 C9 C10 C20 C21 + C18 后端半边**，`blocked_by 1`（`[112]`） |
| GitHub issue **#114** = ③ | **新建**：人类面，覆盖 **C15 C16 C17 + C18 前端半边**，`blocked_by 1`（`[113]`） |
| GitHub issue **#115** = ④ | **新建**：三处存量注释改口径，覆盖 **仅 C19**，`blocked_by 3`（`[112,113,114]`） |
| `app/specs/reminder-trigger-tickets.md` | **新增**：拆分记录（4 票的编号 → 簇 → 覆盖契约 → 依赖图 + 偏差登记 + 行号复核 + 本 Answer 留档） |

四张实施票全部挂 `ready-for-agent`，全部首行 `Part of #1`。**票数与分簇未改**（Q2 = 4 票是用户人定）。

## 2. 原生依赖读数（`issue_dependencies_summary.blocked_by`）

| issue | 票 | `blocked_by` | `blocking` | `dependencies/blocked_by` 明细 |
| --- | --- | --- | --- | --- |
| #112 | ① | **0** | 2 | `[]` |
| #113 | ② | **1** | 2 | `[112]` |
| #114 | ③ | **1** | 1 | `[113]` |
| #115 | ④ | **3** | 0 | `[112,113,114]` |

依赖用 `gh api --method POST repos/whutlichao/alibot/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>` 建立，database id 实测：#112 = `5894332399`、#113 = `5894333078`、#114 = `5894333859`、#115 = `5894334469`。

**没有建立原生 sub-issue 父子关系**：四票首行写 `Part of #1`、正文引用本票 #111（与 #60 / #87 体系的非 sub-issue 写法一致；native sub-issue 与 `blocked_by` 是两套机制，不混用）。

## 3. 哪些是 ask 回来的（四处，全部经 `orca orchestration ask`）

| # | 问题 | 结论 | 归属 |
| --- | --- | --- | --- |
| 1 | 本票在 tracker 上没有对应 issue（最新是 #109 规格票，其后只有 PR #110），Answer 该落在哪 | **新建一张拆分票 issue（= #111）**，Answer 追加到它，并回填 `app/specs/reminder-trigger-tickets.md` | 用户 **人定**（并指出这是他 spec 的漏写） |
| 2 | **规格级缺陷**：② 覆盖 C1/C21（落消息正文取 `system.reminder.fired` / `fired_late`），但规格把这两条后端词条划给 ④，而 ④ 只依赖 ① ⇒ ② 可能先落地却无词条可用 | **C18 后端半边从 ④ 移到 ②**（谁用谁负责）；④ 只留 C19；两票各验一个方向的跨层校验 | 用户 **人定** |
| 3 | 同构缺口在前端：③ 消费 `reminder.dateLabel` / `stateFired` / `stateMissed`，同样被划给 ④ 且 ④ 不阻塞 ③ | **C18 前端半边从 ④ 移到 ③**（与第 2 条同一口径） | coordinator **代答·推导** |
| 4 | 依赖修正：④ 的 C19 **建议替换注释全文描述的正是 ②③ 交付的行为**，④ 若先合并会让注释重新变成「声称不存在的行为」；另有 ③④ 的文件物理重叠 | **④ 改为被 ①②③ 全部阻塞**；③④ 两票正文都写明「后合并者负责 rebase 并重跑全部门禁」 | 用户 **人定** |

**自决的**（票面授权细节类）：票标题措辞、拆分记录的章节组织、`app/specs/` 下的文件名（票面已指定）。

## 4. 四条额外硬要求的落点（逐条可核）

| 硬要求 | 落点 |
| --- | --- |
| ① 的 C12/C13/C14：存量 `HH:MM` 解释成**迁移执行当日**该钟点 + **时间语义的一次性收窄** + 报告给出被改写行数 | #112 的 C12（含「为什么不是下一个该钟点」的理由）、C13（每天 9 点 → 迁移那天 9 点，且报告必须能读到）、C14（复用 `changed_rows`，`改动 N 行` 可核对） |
| ② 的 C5：落消息与写 `fired_at` **同一事务** | #113 的 C5 分工段：本票只交付列与形状，**原子性归 ②**，并点明 `append_message`（`db.py:2181`，事务边界 `:2238-2277`）的既有形状 |
| ② 的 C2/H1：无归属提醒到点**只写 `fired_at`、不落消息**的**显式分叉** | #113 的 C2 小节标题即「硬要求：必须在票面与实现里显式分叉，不是脚注」，带 4 条双分支判据 |
| ② 的 C9/H4：`POLL_INTERVAL_SECONDS = 30` 常量 + 单元断言；行为测试走**注入时刻**；**任何测试不许真等 30 秒** | #113 的 C9 四条契约陈述 + 四条判据（含「整套提醒测试 < 5 秒」的反证判据） |
| ④ 的 C19：引用**建议替换注释全文** + 三处落点 | #115 的「建议的替换后注释全文（逐字可抄）」代码块 + 三处落点（`index.html:651-659` 整块 / `i18n.js:394-397` zh / `:858-861` en），并注明是**整块 651-659** 而非设计票记的 `656-659` |

## 5. 基线事实与零改动取证

| 项 | 读数 |
| --- | --- |
| 开工第一件事 | `git fetch origin` |
| 开工时 `origin/main` | **`5c4dc5c`**（PR #110 的合并提交） |
| 是否前进 | **未前进** —— 与票面基线一致，**无需 rebase** |
| `SCHEMA_VERSION` | 仍是 **12**（`db.py:59`），**未 bump** |
| 源码 / 测试 / ADR / CONTEXT | `git diff origin/main -- app/alibot/ app/tests/ docs/adr/ CONTEXT.md` **为空** |
| 本票 diff 面 | 只有 `app/specs/reminder-trigger-tickets.md`（新增）+ `app/specs/reminder-trigger-tickets-answer.md`（新增）+ `app/specs/reminder-trigger-tickets-pr-body.md`（新增） |

## 6. 行号复核（自决地把唯一偏差登记出来）

全部 `file:line` 都在 `5c4dc5c` 上自行复核过，**与规格一致，唯一例外**：

- `app/alibot/migrate.py` 的 `STEPS` 末步（`11 → 12`）：规格记 `:658`，**实测 `:660`**（`:658` 是 `9 → 10` 那步）。票 ① 的正文已按实测值 `:660` 写，偏差登记在拆分记录 §4。

## 7. 遗留

- 四张实施票均**未开工**（本票只拆票，不写源码）。frontier 当前只有 **#112**（①，`blocked_by 0`）。
- ③④ 的物理重叠要求「后合并者 rebase 并重跑全部门禁」——实施时行号很可能已漂移（③ 会在 `index.html:679` 附近加日期控件、在 `i18n.js:398-410` / `:862-875` 附近加三条词条），以当时的实际行号为准。
