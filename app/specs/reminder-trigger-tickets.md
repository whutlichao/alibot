<!-- 本文件是 GitHub issue #111 的正文与 Answer 留档；tracker 上的 issue 才是正本，以 tracker 为准。 -->

# 提醒的到点触发 —— 实施票拆分记录（C1–C21 → 4 张实施票 + 原生依赖）

> 拆分票正本：https://github.com/whutlichao/alibot/issues/111 ｜ 仓库：whutlichao/alibot ｜ 2026-10-17

Part of #1（首版规格锚点）。

**票型**：票据拆分 ｜ **门禁**：**G-tickets** ｜ **本票不跑**：源码测试（`pytest` / `ruff check` / `ruff format --check` / `mypy` / `pyright` / `node --test`）、双轴 code-review —— 本票只产出 GitHub issue 与拆分记录文档，**不改任何源码与测试**，不 bump `SCHEMA_VERSION`，不写实现。

**上游正本（全部已合入 `main`）**

- 规格正本：`app/specs/reminder-trigger-spec.md`（PR #110 合并于 `5c4dc5c`）—— **C1–C21 二十一条契约**（每条含契约陈述 + 可观察验收判据）、S1–S19 → 契约映射表、硬点 H1–H6 对照表、唯一 seam、60 条 user stories、Implementation Decisions、Testing Decisions、Out of Scope（15 条）、**实施票切分建议**（4 票）、验收判据总表。
- ADR：`docs/adr/0017-reminders-fire-into-the-message-stream.md`。
- 设计票：`app/specs/reminder-trigger-design-notes.md`（D1–D9 决策账、§4.4 存量落点、§4.5 与其它在途票交互）。
- issue 写法与原生依赖命令：`docs/agents/issue-tracker.md`。
- 词条：`CONTEXT.md` 的**提醒** / **到点** / **系统事件消息** / **回应预算** / **未读** / **搜索命中** / **target** / **档位**。

---

## 1. 四张实施票（编号 → 簇 → 覆盖契约 → 依赖）

> **每张实施票的正本在 tracker**（下表「issue」列），本节只是拆分记录与指针。票面正文自足到「实施 worker 只看得到票也能开工」，因此正文里内联了各自契约的**契约陈述摘要 + 可观察验收判据**，不只给规格指针。

| # | issue | 簇 | 覆盖契约 | 依赖（原生 `blocked_by`） | 发布时 `blocked_by` 读数 |
| --- | --- | --- | --- | --- | --- |
| ① | [#112](https://github.com/whutlichao/alibot/issues/112) | **库形态与迁移**：`SCHEMA_VERSION` 13 + `MigrationStep(12, 13)` + `fired_at` 列 + 存量改写 + 报告 | **C4、C5、C11、C12、C13、C14** | 无 | `0` |
| ② | [#113](https://github.com/whutlichao/alibot/issues/113) | **到期投递器 seam**：轮询 + 启动先扫 + 24h 窗口 + 幂等 + shutdown 接线 + 精度常量 | **C1、C2、C3、C6、C7、C8、C9、C10、C20、C21** + **C18 后端半边** | blocked_by ① | `1`（`[112]`） |
| ③ | [#114](https://github.com/whutlichao/alibot/issues/114) | **人类面**：两个原生输入 + 状态后缀 + 指示点 + payload 消费 | **C15、C16、C17** + **C18 前端半边** | blocked_by ② | `1`（`[113]`） |
| ④ | [#115](https://github.com/whutlichao/alibot/issues/115) | **文案两层 + 三处存量注释改口径**（本票只余注释） | **仅 C19** | blocked_by ①②③（全部） | `3`（`[112,113,114]`） |

### 1.1 依赖图

```text
        ① #112  库形态与迁移（SCHEMA_VERSION 13 / fired_at / 存量改写）
         │        覆盖 C4 C5 C11 C12 C13 C14
         │
         ▼
        ② #113  到期投递器 seam（轮询 / 启动先扫 / 24h 补投 / 幂等 / shutdown / 30s 常量）
         │        覆盖 C1 C2 C3 C6 C7 C8 C9 C10 C20 C21 + C18 后端半边
         │
         ▼
        ③ #114  人类面（date+time 两输入 / 状态后缀 / 指示点）
         │        覆盖 C15 C16 C17 + C18 前端半边
         │
         ▼
        ④ #115  三处存量注释改口径（仅 C19）
                  注释描述的对象 = ②③ 交付的行为，因此被 ①②③ 全部阻塞
```

依赖边（`docs/agents/issue-tracker.md` 的原生命令，用 **database id**）：

```sh
# ① → ②
gh api --method POST repos/whutlichao/alibot/issues/113/dependencies/blocked_by -F issue_id=5894332399  # #112
# ② → ③
gh api --method POST repos/whutlichao/alibot/issues/114/dependencies/blocked_by -F issue_id=5894333078  # #113
# ①②③ → ④
gh api --method POST repos/whutlichao/alibot/issues/115/dependencies/blocked_by -F issue_id=5894332399  # #112
gh api --method POST repos/whutlichao/alibot/issues/115/dependencies/blocked_by -F issue_id=5894333078  # #113
gh api --method POST repos/whutlichao/alibot/issues/115/dependencies/blocked_by -F issue_id=5894333859  # #114
```

验收读数（`issue_dependencies_summary.blocked_by`，发布后实测）：

| issue | `blocked_by` | `blocking` | `dependencies/blocked_by` 明细 |
| --- | --- | --- | --- |
| #112 | **0** | 2 | `[]` |
| #113 | **1** | 2 | `[112]` |
| #114 | **1** | 1 | `[113]` |
| #115 | **3** | 0 | `[112,113,114]` |

**没有建立原生 sub-issue 父子关系**（四票首行写 `Part of #1`，正文引用拆分票 #111）。依据：与 #60 / #87 体系里非 sub-issue 的写法一致，且 native sub-issue 与 `blocked_by` 是两套机制，不混用。（coordinator 2026-10-17 人定）

---

## 2. 与规格原划分的三处偏差（逐条登记，不许静默改）

规格「实施票切分建议」给的是 4 票 4 簇 + 三条依赖边。**票数与分簇未改**（Q2 = 4 票是用户人定，改它属 §5 决策类）。改的只有三处**交付归属**与随之而来的**依赖边**：

### 偏差 1 · C18 后端半边由 ④ 移到 ②（**用户 2026-10-17 人定**）

- **规格原划分**：整条 C18（后端两键 + 前端三键）归 ④。
- **缺口**：② 覆盖 C1 / C21，落消息的正文要取 `system.reminder.fired` / `system.reminder.fired_late`；而 ④ 只依赖 ① ⇒ ② 可能先落地却**无词条可用**。
- **处置**：`system.reminder.fired` / `system.reminder.fired_late` 两条**后端**词条移到 **②**（`app/alibot/i18n.py` zh/en 两份）。口径 = **谁用谁负责**。

### 偏差 2 · C18 前端半边由 ④ 移到 ③（**coordinator 2026-10-17 代答·推导**）

- **缺口**：③ 覆盖 C15 / C16，判据直接消费 `reminder.dateLabel`（日期输入的可访问名）/ `reminder.stateFired` / `reminder.stateMissed`；而 ④ 只依赖 ① ⇒ ③ 可能先落地却无键可用。
- **处置**：三条**前端**词条移到 **③**（`app/alibot/studio/i18n.js` zh/en 两份）。口径与偏差 1 相同（同一份规格不许并存两种归属规则）。

### 偏差 3 · ④ 的依赖由「只依赖 ①」改为「被 ①②③ 全部阻塞」（**用户 2026-10-17 人定**）

- **规格原划分**：④ 只依赖 ①（理由是「词条与注释不依赖 ②③ 的实现」）。
- **缺口**：偏差 1/2 把词条移走后，④ **只剩 C19**（三处注释改口径）。而 C19 的**建议替换注释全文描述的正是 ②③ 交付的行为**——「有归属的提醒到点确实落一条 `system` 消息」是 ② 的产物，「新增控件（文本 + 日期 + 钟点）… 两个原生输入」是 ③ 的产物。④ 若在 ②③ 之前合并，注释会**反过来变成第二个「声称不存在的行为」**，而这正是 C19 要消灭的那类谎。
- **处置**：依赖图改为 **①→②→③**、**①②③→④**。这是四条依赖边里唯一被改的一条（原为一条边，现为三条边）。

### 偏差不改的部分

- **C18 的「不许有第三层」这条约束不随责任转移而变**，对四票**全部有效**——四张票的正文里都保留了这句。
- **两票各验一个方向**的跨层校验：② 验**后端没越界写前端键**，③ 验**前端没越界写后端键**。
- **物理重叠与 rebase 义务**：③④ 在 `app/alibot/studio/i18n.js`（zh 注释块 `:394-397` / en 注释块 `:858-861`）与 `app/alibot/studio/index.html`（注释块 `:651-659`，紧邻 ③ 要改的 `:679` 控件）上有物理重叠；**两票正文都写明「后合并者负责 rebase 并重跑全部门禁」**。

---

## 3. 四条额外硬要求（本票的逐条自检）

| 硬要求 | 落点（票面证据） |
| --- | --- |
| 票 ① 写明「存量 `HH:MM` 行解释成**迁移执行当日**该钟点」+「这是**时间语义的一次性收窄**，迁移报告要给出被改写行数」 | #112 的 **C12** 小节（含「为什么是迁移执行当日而不是下一个该钟点」的理由）与 **C13** 小节（含「每天 9 点 → 迁移那天 9 点」与「报告必须能读到」）；被改写行数在 **C14** |
| 票 ② 写明「落消息与写 `fired_at` **同一事务**」 | #113 的 **C5** 分工段：本票只交付列与形状，**原子性归 ②**；正文 C1/C6 与「写面」里点明 `append_message`（`db.py:2181`，事务边界 `:2238-2277`）的既有形状 |
| 票 ② 写明「无归属提醒到点**只写 `fired_at`、不落消息**」的**显式分叉** | #113 的 **C2** 小节，标题即「**硬要求：必须在票面与实现里显式分叉，不是脚注**」，并带 4 条双分支判据 |
| 票 ② 写明精度判据的**可验收形态**：`POLL_INTERVAL_SECONDS = 30` 作常量 + 单元断言，行为测试走**注入时刻**，**任何测试不许真等 30 秒** | #113 的 **C9** 小节四条契约陈述 + 四条判据（判据 1 常量单元断言；判据 2 注入时刻 + 声明式 30 秒上界；判据 3 实测墙钟 < 5 秒；判据 4 禁止本地/UTC 混比） |
| 票 ④ 引用 C19 给出的**建议替换注释全文** + 写明三处落点 | #115 的「**建议的替换后注释全文（逐字可抄）**」代码块 + 三处落点清单（`index.html:651-659` / `i18n.js:394-397` zh / `:858-861` en），并注明**整块 651-659** 而非设计票记的 `656-659` |

---

## 4. 行号复核（自行复核，未照抄设计票与规格）

票面 Constraints 要求「凡引用 `file:line`，自行复核」。本票在 `5c4dc5c` 上逐条复核过，与规格记的读数一致（规格已在 PR #110 里做过一次同样的事）：

| 对象 | 规格记的 | 本票实测 | 结论 |
| --- | --- | --- | --- |
| `SCHEMA_VERSION` | `db.py:59` | `db.py:59` = `SCHEMA_VERSION = 12` | 一致 |
| 时间形状正则 | `db.py:183` | `db.py:183` = `REMINDER_TIME_PATTERN = …` | 一致 |
| `reminder` 表 DDL | `db.py:382-388` | `db.py:382-388` | 一致 |
| `UNREAD_WHERE_SQL` | `db.py:496-502` | `db.py:496-502`（`kind IN ('human','agent')` 在 `:500`） | 一致 |
| 搜索排 `system` | `db.py:599` / `:610` | `db.py:599` / `:610` | 一致 |
| `REMINDER_SELECT_SQL` | `db.py:563-565` | `db.py:563-565`（用 `r.*`） | 一致 |
| 提醒读/写路径 | `:1304` / `:1330` / `:1341` / `:1384` | `list_reminders` `:1304`、`_reminder_payload` `:1330`、`create_reminder` `:1341`、`delete_reminder` `:1384` | 一致 |
| `utc_now` | `db.py:756-758` | `db.py:756-758` | 一致 |
| `append_message` / 事务边界 | `db.py:2181` / `:2238-2277` | `db.py:2181` / `_begin()` 在 `:2238` | 一致 |
| `notify` / `system` 早退 | `orchestration.py:183` / `:205` | `:183` / `:205` | 一致 |
| `STEPS` 与末步 | `migrate.py:651` / `:658`（末步 `11 → 12`） | `STEPS` 在 `:651`，末步 `MigrationStep(11, 12, …)` 在 **`:660`** | **偏差**：规格记 `:658`，实测 `:660`（`:658` 是 `9 → 10` 那步）。**已在 #112 票面写实测值 `:660`** |
| `_assert_chain_is_continuous` | `migrate.py:664-682` | `:664-682` | 一致 |
| `_render_step_lines` | `migrate.py:848-863` | `:848-863` | 一致 |
| `_step_7_to_8` | `migrate.py:574` | `:574` | 一致 |
| `create_app` / 注入点 / lifespan | `api.py:303` / `:321-328` / `:331-342` | `:303` / `:321-328` / `:331-342`（`shutting_down.set()` `:338`、`shutdown()` `:339-341`、`close()` `:342`） | 一致 |
| 提醒三端点 | `api.py:471` / `:476` / `:487` | `:471` / `:476` / `:487` | 一致 |
| `error.reminder_time_shape` | `i18n.py:60` / `:242` | `:60` / `:242` | 一致 |
| `system.*` 词条区 | `i18n.py:82-90` / `:273-281` | `:82-90` / `:273-281` | 一致 |
| `index.html` 注释块 | `:651-659` | `:651-659`（首行 `<!-- ─── 提醒（ADR-0012…`，末行 `不存在的行为。… -->`） | 一致 |
| `index.html` 时间控件 | `:679` | `:679`（`id="reminderTime"`、`type="time"`） | 一致 |
| `i18n.js` zh 注释块 | `:394-397` | `:394-397` | 一致 |
| `i18n.js` en 注释块 | `:858-861` | `:858-861` | 一致 |
| `i18n.js` 前端提醒词条 | `:398-410`（zh）/ `:862-875`（en） | `:398-410` / `:862-875` | 一致 |
| `app.js` 指示点 | `:789-793`（判据 `:792`） | `:789-793`（判据 `:792` = `dot.hidden = state.reminders.length === 0;`） | 一致 |
| `app.js` `submitReminder` | `:815` | `:815` | 一致 |
| `app.js` `reminderRowNode` | `:860-881` | `:860-881` | 一致 |
| `service.py` 提醒三方法 | `:490` / `:494` / `:506` | `:490` / `:494` / `:506` | 一致 |

**唯一的行号偏差**：`migrate.py` 的 `STEPS` 末步 —— 规格记 `:658`，实测 **`:660`**。票 ① 的正文已按实测值写，并在本表登记。

---

## 5. 开工与验收读数

| 项 | 读数 |
| --- | --- |
| 开工第一件事 | `git fetch origin` |
| 开工时 `origin/main` | **`5c4dc5c`**（PR #110 的合并提交） |
| 是否前进 | **未前进** —— 与票面给的基线（`main` = `5c4dc5c`）**一致**，因此**无需 rebase** |
| `SCHEMA_VERSION` | **12**（`db.py:59`），本票**不 bump** |
| `docs/adr/` 最大编号 | **0017**，本票不新增 ADR、不改既有 ADR |
| 新建 issue | 拆分票 **#111** + 实施票 **#112 / #113 / #114 / #115**（共 5 张） |
| 4 张实施票标签 | 全部 `ready-for-agent`；全部首行 `Part of #1` |
| 原生依赖 | #113 ← #112；#114 ← #113；#115 ← #112,#113,#114（读数见 §1.1） |
| 零源码改动 | `git diff origin/main -- app/alibot/ app/tests/ docs/adr/ CONTEXT.md` **为空** |

---

## 6. Answer（追加到 tracker #111）

Answer 的正本在 issue #111 的评论；同源留档见 `app/specs/reminder-trigger-tickets-answer.md`。要点：

1. 交付 = 拆分票 #111 + 4 张实施票 #112–#115 + 本文件；原生依赖 4 条边，读数 `blocked_by` = `0 / 1 / 1 / 3`。
2. **ask 回来的四处决定**（全部经 `orca orchestration ask`）：Answer 落点（新建拆分票 issue）、C18 后端半边移到 ②（人定）、C18 前端半边移到 ③（代答·推导）、④ 改为被 ①②③ 全部阻塞 + ③④ 的 rebase 义务（人定）。
3. **自决的**：票标题措辞、本文档章节组织、文件名（票面指定）；不建立原生 sub-issue 父子关系（人定，见 §2）。
4. **遗留**：`migrate.py` 的 `STEPS` 末步行号规格记 `:658`、实测 `:660`（已在 §4 登记，票 ① 正文按实测写）。四张实施票均未开工。
