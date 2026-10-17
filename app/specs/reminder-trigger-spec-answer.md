<!-- 本文件是 GitHub issue #109 的 Answer 评论留档；tracker 上的评论才是正本，以 tracker 为准。 -->

# Answer —— 规格票 #109（提醒的到点触发）

> 正本：https://github.com/whutlichao/alibot/issues/109 ｜ 仓库：whutlichao/alibot ｜ 2026-10-17

## 1. 交付物

| 文件 / 对象 | 动作 |
| --- | --- |
| `app/specs/reminder-trigger-spec.md` | **新增**（976 行）：行为契约 C1–C21 + S1–S19 映射表 + 硬点 H1–H6 对照表 + Seams + 60 条 User stories + Implementation Decisions（D0–D10）+ Testing Decisions（T1–T5）+ Out of Scope（15 条）+ 实施票切分建议（4 票）+ 验收判据总表 |
| GitHub issue **#109** | **新建**：`Part of #1` 首行、`ready-for-agent` 标签、body 与本规格同源 |
| `app/specs/reminder-trigger-spec-answer.md` | **新增**：本 Answer 留档 |
| `app/specs/reminder-trigger-spec-pr-body.md` | **新增**：PR 正文（`pr` skill 体例，`--body-file` 用） |

**零源码改动**：未改任何 `app/alibot/**`（含 `studio/**`）、未改任何 `app/tests/**`、**未 bump `SCHEMA_VERSION`**（仍是 12）、未写迁移步骤代码、**未新增 ADR**、**未改任何既有 ADR**、**未改 `CONTEXT.md`**。

## 2. 开工时实际基线（如实登记）

| 项 | 读数 |
| --- | --- |
| 开工第一件事 | `git fetch origin` |
| 开工时 `origin/main` | **`ec6310e`**（`docs(reminders): rebase 到 PR #106 后重测存量行号与基线读数`） |
| `origin/main` 是否已前进 | **未前进** —— 与票面给的基线（`main` = `ec6310e`，PR #108 合并后）**一致**，因此**无需 rebase** |
| 本票 HEAD 起点 | `ec6310e`（未产生 rebase 提交） |
| `SCHEMA_VERSION` | **12**（`app/alibot/db.py:59`），未 bump |
| `docs/adr/` 最大编号 | **0017**（ADR-0017 已由 #108 合入），本票不新增 |
| `CONTEXT.md` | 上游 #108 已把**提醒**改新口径、**系统事件消息**加第四来源、新增**到点**词条（实测 `:76-81` / `:295-297` / `:299-301`），**无词条缺口** ⇒ 本票**零改动** |

## 3. to-spec 阶段的侦察（做了哪些只读取证）

全部只读，未写入任何文件。逐条自行 `grep` / `sed` 复核行号，**未照抄票面或设计票的数字**。

| 侦察面 | 结论（本票实测） |
| --- | --- |
| 提醒的库形态与写路径 | `reminder` 五列在 `db.py:382-388`；`time` 正则 `db.py:183`、校验 `:1359-1360`；`channel_id` 可空 `:386`；`REMINDER_SELECT_SQL` 用 `r.*` 在 `:563-565`（加列可能无需改）；`list_reminders` `:1304` / `_reminder_payload` `:1330` / `create_reminder` `:1341` / `delete_reminder` `:1384` |
| 人类面三端点 | `api.py:471`（GET）/ `:476`（POST）/ `:487`（DELETE）；`api.py:34-35` 明写「没有创建或编辑系统消息的端点」 |
| 唤醒的结构约束 | `notify` 定义 `orchestration.py:183`；`system` 早退 `:205`；`ThreadScheduler` `:76-88`（无定时能力）；`append_message` `db.py:2181`，事务边界 `:2238-2277`（`_begin`→写→`_commit`/`_rollback`） |
| 未读 / 搜索的既有口径 | `UNREAD_WHERE_SQL` 本体 `db.py:496-502`（`kind IN ('human','agent')` 在 `:500`）；搜索排 `system` 在 `db.py:599` 与 `:610` |
| 装配与关停顺序 | `create_app` `api.py:303`；注入点 `:321-328`；`lifespan` 关停 `:331-342`（`shutting_down.set()` `:338` → `scheduler.shutdown()` `:339-341` → `database.close()` `:342`） |
| 迁移链与号段 | `STEPS` `migrate.py:651`，末步 `11 → 12` 在 `:658`；`_assert_chain_is_continuous` `:664-682`；`_step_7_to_8` `:574-577`；号段记账 `db.py:44-58`；`MIN_SUPPORTED_SOURCE_VERSION = 3` 在 `db.py:65` |
| 迁移报告形状 | `_render_step_lines` `migrate.py:848-863`（真跑与预演共用）；`render_report` `:866-883` |
| i18n 两层 | 后端 `i18n.py:57-61`(zh) / `:239-243`(en) 的 `error.reminder_*`；`system.*` 区 `:82-90` / `:273-281`。前端 `studio/i18n.js:398-410`(zh) / `:862-875`(en) |
| 前端呈现 | `renderReminderDot` `app.js:789-793`（判据 `:792` 的 `length === 0`）；`reminderRowNode` `:860-881`；`submitReminder` `:815-834`；`index.html:679` 只有 `type="time"` |
| 定时/时区设施 | `POLL_INTERVAL` / `Timer(` / `time.sleep` / `asyncio.create_task` 在 `app/alibot/*.py` **零命中**；`ZoneInfo` / `astimezone` / `localtime` **零命中**；`fired` / `due` **零命中** ⇒ 本能力是全新概念 |
| 既有测试 prior art | `test_reminders.py`（HTTP 黑盒 + 「提醒不是对话事实」的边界读数）、`scripted.py` 的 `ManualScheduler`（注入式、不 sleep）、`conftest.py:42-46` 的注入点、`test_backend_copy.py`（按库内偏好渲染 + 历史不回填）、`test_migration_chain_guard.py`（CLI 接缝 + 解析报告步骤表）、`i18n_ui.test.cjs`（**唯一**前端测试框架 + 裸中文扫描） |

### 3.1 行号偏差登记（自行复核的结果）

| 对象 | 设计票 / 票面记的 | **本票实测** | 差异 |
| --- | --- | --- | --- |
| `studio/index.html` 的那段过时注释 | `:656-659` | **`:651-659`**（9 行整块） | 设计票只登记了**注释块末两句**。整块里还有「新增控件只有 `type="time"`」这句，本能力成立后**也**不实（要加日期）——只改末两句会留下另一半谎。**按 651-659 全文处置**（规格 C19 给出替换全文） |
| **同一条过时断言的第二、三处** | 设计票**未登记** | **`studio/i18n.js:394-397`（zh）与 `:858-861`（en）** 的注释块 | 设计票漏登记。规格 C19 一并列入，实施票必须改三处 |
| `src` 提醒读/写路径 | `:1290-1393` | `:1304` / `:1330` / `:1341` / `:1384` | 按函数名 + 实测行号引用 |
| 「不计未读（`db.py:536`）」 | `:536` | 本判据本体在 **`:496-502`**（`:536` 是消费点 `UNREAD_COUNT_SQL`） | 引用改为本体行号 |
| 搜索排 `system` | 未给行号 | **`db.py:599`（全工作区）/ `:610`（单 target）** | 补实测 |
| `app.js` 提醒行渲染 | `:860-880` | **`:860-881`** | 末行 +1 |
| `studio/i18n.js` 前端提醒词条 | `:399-411` / `:865-878` | **`:398-410`** / **`:862-875`** | −1 / −3 |
| `api.py` 装配 / 关停 | `:321-328` / `:331-345` | 注入 `:321-328`、关停 `:331-342` | 关停区间收窄到 `database.close()` 那一行 |

**与票面或设计票一致的部分**（逐条复核过，不是照抄）：`db.py:382-388` 提醒表 DDL、`db.py:183` 时间正则、`orchestration.py:183` 的 `notify`、`migrate.py:574-577` 的 `_step_7_to_8`、`i18n.py:57-61`/`:239-243`、`app.js:789-793`、`index.html:679`、`api.py:471-494`。

## 4. 决策归属：哪些是 ask 回来的、哪些是自决的

三轮 ask **全部经 `orca orchestration ask`**（禁 `AskUserQuestion`，遵守行为规则第 1 条）。

### 4.1 ask 记录（逐轮可追）

| 轮 | 覆盖 | 结论 | 归属 |
| --- | --- | --- | --- |
| 1 · Q1 | Seams 数量 | **只一个 seam**（可注入的到期投递器）；读数面（payload / 消息流 / 指示点）**不是**第二个 seam | **人定**（用户 2026-10-17） |
| 1 · Q2 | 实施票切分票数 | **4 票**（库形态+迁移 / 投递器 seam / 人类面 / 文案与注释） | **人定** |
| 1 · Q3 | H6 注释的交付形态 | 规格给出**建议替换全文**，并声明本票不改 | **代答** |
| 2 · Q4 | `fired_at` 是否写进 payload 契约 | **写进契约**（C20）；它是无归属提醒**唯一**可观察的到点证据 | **代答·推导**（S2 人定 + S16 人定 + 「断言落外部可观察面」纪律） |
| 2 · Q5 | 补投文案的分档判据 | 按 **30 秒**分档（C21），与 C10 共用**同一个常量** | **代答·推导**（H4/S9 人定 + 备选判据外部不可观察） |
| 2 · Q6 | 被改写行数的报告落点 | 复用既有 `changed_rows`（C16），不新增报告字段 | **代答·细节** |
| 3 · Q7 | `fired_at` 按哪一口径写 | **UTC 审计时间戳**（与 `created_at` 同族）；「已错过」判定要换算一次，与 C10 判据**共用同一方法** | **人定**（用户 2026-10-17） |
| 3 · Q8 | 「已错过」由哪一侧推导 | **后端推导派生字段** `state ∈ {pending, fired, missed}`（C20），不落库、不进表列、不 bump 版本 | **代答·推导** |

**ask 无超时、无重复提问**（三轮各一次应答）。coordinator 在 ask 回信里追加的两条硬要求已落实：① `fired_at` 的写入时机必须写成契约（→ **C5**，含「与落消息同一事务」与 `db.py:2238-2277` 的既有原子性依据）；② 「已错过」的推导侧必须定（→ **C20**）。

### 4.2 自决的（票面授权「命名、目录、文档内部组织自己定」）

| 项 | 自决内容 |
| --- | --- |
| 契约编号 | **C1–C19 ↔ S1–S19 一一对应**（引用时不必查表），另加 **C20/C21** 两条推导契约（无对应 S） |
| 章节组织 | 契约按**主题**分八节（不按编号顺序），并显式说明「正文顺序不是编号顺序，按编号查找请 `grep '#### C<n>'`」 |
| `state` 三值命名 | `pending` / `fired` / `missed`（机器可读、不译，与 `round_outcome.status` 同族） |
| 「已过点但尚未投递」的 `state` | 定为 `pending`（可判定的瞬态，不是推测） |
| H6 替换注释措辞 | 按 `index.html` 既有注释体例写全文（C19） |
| `POLL_INTERVAL_SECONDS` 常量名 | 沿用设计票 D8，不另起 |
| `reminder.timePast` 是否新增 | **不要求**（前端不做该本地校验，交给后端 `validation_failed` 既有形态）⇒ 前端只新增三键 |

## 5. 硬点 H1–H6 的落实位置

| 硬点 | 落点 | 落实方式 |
| --- | --- | --- |
| **H1** 无归属分叉不许写成脚注 | **C2**（§一「到点的产物与它的分叉」下的**独立小节**） | 独立 `#### C2` 一节，开篇即声明「这一节不是脚注」，带 4 条判据 |
| **H2** 两种时间口径**逐字**进规格 | **C7** 的逐字引用块 | `grep` 实测命中 1 次；另加四条展开规矩 + 「唯一换算口径只有一处、三个消费点共用」 |
| **H3** 一次性收窄 + 迁移报告可读 | **C13** | 契约陈述两半；判据 1 查规格正文、判据 2 查迁移 stdout |
| **H4** 30 秒常量 + 注入时刻 + 不许真等 | **C10** | 判据 1 单元断言常量；判据 2 注入时刻验后果；判据 3 用实测墙钟耗时反证「没真等」 |
| **H5** 文案两层、同一条不许两边登记 | **C18** | 四条硬纪律 + 6 条判据（含「互不重复」与「历史不回填」） |
| **H6** 存量注释改口径（本票不改） | **C19** | 三处落点表（含设计票漏登记的两处）+ **建议替换全文** + 4 条判据（第 4 条即本票判据：`studio/**` diff 为空） |

## 6. S1–S19 逐条映射（无落单）

见规格 §S1–S19 → 契约编号映射表（19 行）与 §编号约定（`Cn ↔ Sn` 一一对应）。机械核对：`grep -c '^| S[0-9]' app/specs/reminder-trigger-spec.md` = **19**。

## 7. 可观察验收自查（逐条对票面）

| 票面验收 | 本票落点 | 读数 |
| --- | --- | --- |
| 1. 规格存在，含 Seams / User stories / Out of Scope（非空）/ 切分建议 / 判据总表；每条契约有唯一编号与可观察判据 | 六个章节齐全；C1–C21 各含「契约陈述 + 可观察验收判据 + 来源」；Out of Scope **15 条逐条带理由**；User stories **60 条**（A–H 八组） | 通过 |
| 2. S1–S19 十九条逐条有映射，无落单 | §S1–S19 映射表 19 行 + 编号约定 `Cn ↔ Sn` | `grep -c` = 19 |
| 3. H1–H6 逐条落实，H2 逐字出现 | §硬点 H1–H6 对照表 + C2/C7/C13/C10/C18/C19 | H2 `grep` 命中 1 |
| 4. Seams 声明核心 seam 与落点、说明为什么足够深 | §Seams：接口宽度（1 输入 + 1 输出）、隐藏复杂度（7 项）、深度判据（几行测试替身覆盖 5 分支）、落点（`api.py:303`/`:321-328`/`:331-342`） | 通过 |
| 5. 零源码改动；`SCHEMA_VERSION` 未 bump | `git diff origin/main -- app/alibot/ app/tests/` **空**；`grep -m1 '^SCHEMA_VERSION' app/alibot/db.py` ⇒ `12` | 通过 |
| 6. `git diff origin/main -- docs/adr/` 为空 | **空**（未新增 ADR、未改既有 ADR） | 通过 |
| 7. 若改 `CONTEXT.md` 只显示相关段落 | **未改**（`git diff origin/main -- CONTEXT.md` **空**）——上游 #108 已把三条词条改好，无缺口 | 通过（零改动） |
| 8. Answer 能看到侦察 / ask 归属 / 自决 / 开工时实际基线 | 本文件 §2 / §3 / §4 | 通过 |
| 9. `pr` skill 体例的 PR body、推分支、`--body-file` 建 PR（base `main`）、PR 号回填 | PR 见 §8 | 通过 |

## 8. 项目改动与收尾读数

| 项 | 读数 |
| --- | --- |
| 分支 | `reminder-trigger-spec`（已推送） |
| 提交 | 见 PR 的 commits |
| PR | 见 §9 回填（base `main`，body 走 `--body-file`） |
| PR 正文 | `app/specs/reminder-trigger-spec-pr-body.md` |
| 规格票正本 | GitHub issue **#109** |
| 改动文件 | 3 个：`app/specs/reminder-trigger-spec.md`、`app/specs/reminder-trigger-spec-answer.md`、`app/specs/reminder-trigger-spec-pr-body.md` |
| 源码改动 | **0**（`git diff origin/main -- app/` 只含 `app/specs/*.md`） |
| 既有 ADR 改动 | **0** |
| `CONTEXT.md` 改动 | **0** |
| `SCHEMA_VERSION` | **仍 12** |

## 9. PR 号回填

- PR：**#110**（base `main`，head `reminder-trigger-spec`）
- PR body 路径：`app/specs/reminder-trigger-spec-pr-body.md`

## 10. 偏差登记

| 项 | 情况 | 处置 |
| --- | --- | --- |
| 基线 | 票面给 `ec6310e`；开工实测 `ec6310e` | **一致，无偏差**，无需 rebase |
| 行号 | 设计票登记的 `index.html:656-659` 与 `i18n.js` 漏登记 | 按实测写（`651-659` + 三处）；已在 §3.1 与规格 §行号偏差登记 逐条列出 |
| 契约编号 | 票面建议「C1…Cn 或沿用 S 编号并说明映射」 | 选 **C 编号且与 S 一一对应**，另加 C20/C21；已在规格 §编号约定 说明 |
| `CONTEXT.md` | 票面允许「必要时改」 | 实测**无词条缺口** ⇒ 零改动（更安全，也满足「不许重排既有词条」） |
