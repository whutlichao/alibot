<!-- 本文件是 #177 的 Answer（逐条可复现读数）；tracker 上的 issue #177 保持原样。 -->

# Issue #177 Answer —— 「任务」页签计数跟随任务全量快照刷新

> 票面：GitHub [#177](https://github.com/whutlichao/alibot/issues/177)（bug：从「新建任务」弹窗建成任务后，
> 频道 header 上「任务」页签计数 `#taskCount` 不刷新）。
> 票型：实施票（G-impl）。本票不产出文档票 / 规格票，不涉及 schema 号段与迁移
> （`app/alibot/db.py` 的 `SCHEMA_VERSION` 一个字节都没动）。

| 项 | 值 |
| --- | --- |
| 分支 | `issue-177-taskcount`（Orca 建好的 worktree 分支；派发文里写的 `issue-177-impl` 是计划名，实际分支以此为准） |
| 提交 | `1fa2795` fix（`renderTaskCount()` 抽成唯一写入点 + 三个调用点）/ `84e3756` test+docs（双轴 review 收口 + 本文件 + PR 正文）/ `+本提交：回填 PR 号` |
| 本票 base | `c606b4a`（`main`，#176 合并后的读数；`origin/main` 与之一致，无需再 merge 集成分支） |
| 新增用例 | `app/tests/task_count_ui.test.cjs`（3 条；`vm` + fetch 替身 + DOM 替身，沿用既有接缝） |
| PR | [#178](https://github.com/whutlichao/alibot/pull/178)（base `main`，head `issue-177-taskcount`） |
| PR body | `app/specs/issue-177-pr-body.md`（`--body-file` 用） |

---

## 0. 交付概览（判据 → 落点）

| 票面要求 | 落点 |
| --- | --- |
| ①建成任务后 `#taskCount` **立刻**变 | `app/alibot/studio/app.js`：`renderTaskCount()` 在 `renderTaskBoard()` 里被调用，而新建任务 `submitTask()` → `refreshTasks()` → `renderTaskBoard()`，因此提交一完成计数就是新数量 |
| ②计数刷新**唯一归属** | 新增 `renderTaskCount()`（`app.js:1258-1269`）是 `#taskCount` 的**唯一**写入点；`renderTabs()`（页签重渲）、`renderTaskBoard()`（快照的渲染路径）、`openTask()` 成功路径三处共同调用 |
| ②`refreshTasks()` 路径 | 经其末尾的 `renderTaskBoard()` 覆盖（未筛选全量在 `app.js:1197-1199` 写进 `state.tasks`） |
| ②`openTask()` 成功路径 | 补进 `state.tasks` 之后**显式**调 `renderTaskCount()`（`app.js:1932-1935`）：这条路径刻意**不**重铺看板（主线还停在它上面，重铺会丢掉人的滚动与焦点），所以计数不能指望 `renderTaskBoard()` 顺带 |
| ②`markTaskRead()` 之后的快照替换 | **不算新路径**：它替换的是同 id、同 `channel_id` 的那一条（计数不可能变），且它已经调 `renderTaskBoard()`，于是计数由看板重铺顺带覆盖——`renderTaskCount()` 被提到 `renderTaskBoard()` 的早退之前，看板 DOM 缺失时也不会漏写 |
| ③新增 DOM 用例断言用户可见文本 | `app/tests/task_count_ui.test.cjs`：断言全在 `#taskCount` 的 `textContent` 上，驱动路径是真实 click（`#btnNewTask` → 填 `#taskCreateTitle` / `#taskCreateChannel` → click `#taskCreateSubmit`） |

**未改**：后端（`app/alibot/**` 除 `studio/app.js` 外零改动）、HTTP 端点、SSE、schema、迁移、i18n 词条
（本票不需要新文案）、`app/alibot/studio/index.html`、`docs/**`、ADR。

`state.tasks`（未筛选全量快照）的写入点共 6 处，全部被覆盖：
`app.js:1197` / `:1199`（`refreshTasks()` → `renderTaskBoard()`）、
`:1930-1931`（`openTask()` → 显式调用）、`:1956`（`markTaskRead()` → `renderTaskBoard()`）、
`:3102`（`refreshTaskDetails()` → `renderTaskBoard()`）。
SSE 处理器不写 `state.tasks`；没有 `DELETE /api/tasks` 与「撤销读」路径；boot 经 `refreshTasks()`。

---

## 1. 红证据（**写任何修复代码之前**，在 base `c606b4a` 上取得）

```sh
git rev-parse --short HEAD      # c606b4a（main，#176 之后）
node --test app/tests/task_count_ui.test.cjs
```

```text
✖ 从「新建任务」弹窗建成任务后，「任务」页签计数立刻变成新数量 (11.499333ms)
✖ 打开一条原先不在快照里的任务后，「任务」页签计数同步增加 (8.017333ms)
✔ 看板筛到别的频道时，页签计数仍按未筛选的全量快照算 (5.532291ms)
ℹ tests 3
ℹ pass 1
ℹ fail 2
...
✖ failing tests:

test at app/tests/task_count_ui.test.cjs:381:1
✖ 从「新建任务」弹窗建成任务后，「任务」页签计数立刻变成新数量 (11.499333ms)
  AssertionError [ERR_ASSERTION]: 任务快照对齐后，页签计数要立刻跟着变
  '1' !== '2'
      at TestContext.<anonymous> (app/tests/task_count_ui.test.cjs:397:10)

test at app/tests/task_count_ui.test.cjs:402:1
✖ 打开一条原先不在快照里的任务后，「任务」页签计数同步增加 (8.017333ms)
  AssertionError [ERR_ASSERTION]: 补进快照后页签计数要跟着变，不能只重渲线程
  '1' !== '2'
      at TestContext.<anonymous> (app/tests/task_count_ui.test.cjs:414:10)
exit=1
```

失败点**正是**「提交完成后 `#taskCount` 仍是旧值」这条断言（`'1' !== '2'`），不是别的错：
两条红都停在 `#taskCount.textContent` 的断言上，POST 与弹窗关闭的断言都已先通过。

第 3 条（筛选态下计数仍按未筛选全量快照）在修复前就是绿的——它是护栏而非红证据：它钉住本修复
最诱人的错解（从筛选后的 `state.boardTasks` 算计数）。

---

## 2. 绿证据（修复后，同一命令）

```sh
node --test app/tests/task_count_ui.test.cjs
```

```text
✔ 从「新建任务」弹窗建成任务后，「任务」页签计数立刻变成新数量 (10.697708ms)
✔ 打开一条原先不在快照里的任务后，「任务」页签计数同步增加 (4.709709ms)
✔ 看板筛到别的频道时，页签计数仍按未筛选的全量快照算 (3.027708ms)
ℹ tests 3
ℹ pass 3
ℹ fail 0
```

用例族的入口仍是既有那一条（glob，新增文件自动纳入）：`node --test app/tests/*.test.cjs` → 183 通过 / 0 失败。

---

## 3. 全量门禁

```sh
scripts/check.sh            # 默认全量，不加 --fast（在交付 tip `321cdbc` 上跑的最后一次）
```

```text
== pytest（全量）
858 passed, 2 skipped in 380.02s (0:06:20)

== ruff check
All checks passed!

== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
219 files already formatted

== mypy（仓库根）
Success: no issues found in 92 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 183
ℹ pass 183
ℹ fail 0

门禁全绿
exit=0
```

无既存失败，无需基线比对。`ruff format` 未运行（只跑 `--check`）。

---

## 4. 双轴 code-review（改动完成的 branch 上）

固定点 `c606b4a`，diff 命令 `git diff c606b4a...HEAD`，两轴各一个子代理并行跑。

### Standards 轴 —— 无硬违规

按 `docs/agents/review-rules.md` + `CONTEXT.md` 的 `## Language` + `docs/adr/**` + `app/README.md` +
`AGENTS.md` / `docs/agents/implementation.md` 逐条对照：测试分层（沿用 `vm` + fetch 替身 + DOM 替身，
未新开框架）、词条纪律（无新文案）、术语（任务看板 / 私信）均通过；「唯一写入点」这条断言经
`grep '#taskCount'` 核实为真。4 条判断题结论与处理：

| finding | 结论 | 处理 |
| --- | --- | --- |
| `renderTaskBoard()` 里调 `renderTaskCount()` 是否隐藏耦合 / Divergent Change | **不算**：该函数仍只服务「任务面重渲」一个原因，且 `renderTaskCount()` 位于早退之前；把计数塞到 `state.tasks` 的每个写入缝反而是 Shotgun Surgery | 维持 |
| `renderTaskCount()` 重算 `target.kind === 'dm'` 与 `renderTabs()` 的 `isDm` 重复 | **不构成 Duplicated Code**：一个两词布尔，且前者必须自洽（`openTask()` 不经过 `renderTabs()`）；抽 `isDmTarget()` 属 Speculative Generality | 维持 |
| 新用例又抄了一份 `FakeNode`（仓内 12 份） | 本仓已接受形态（各文件头注释互相点名「同形」），归并 12 个文件是独立决策 | 维持 |
| 第 3 条用例修复前就绿，是否凑数 | **有效护栏**：钉住「从筛选后的 `boardTasks` 算计数」这个错解 | 保留 |
| 文件头称「断言只落在用户可见文本」，却有一处断言 `state.tasks.length` | 与自述冲突（既有用例族也断言 state，故非违规） | **已收口**：删掉那行内部断言，只留 `#taskCount.textContent` 的断言 |

### Spec 轴 —— 无 P0 / P1

逐条核对票面 Change 1/2/3 与 Observable acceptance：唯一写入点成立（全文只有 `renderTaskCount()` 写
`#taskCount`），`state.tasks` 的 6 处写入全被覆盖，`openTask()` 的「不重铺看板」分支写清了理由，
用例真实驱动弹窗提交并断可见文本，红/绿/门禁读数与票面一致。2 条 P2 处理：

| finding | 处理 |
| --- | --- |
| 用例 2 直接调 `api.openTask()` 而非点卡片，并断言内部 `state.tasks.length` | 内部断言**已删**（见上表末行）。直接调函数这一半保留：票面点名该路径，且看板卡片 click → `openTask()` 是同一入口（`#ttReplies` / 卡片共用），替身里没有可点的卡片树 |
| 未跟踪的 `.evidence/*.txt` 会让「worktree 干净」严格说不成立 | **已清理**：读数已录入本文件（§1/§2/§3），`.evidence/` 在交付前删除 |

两轴结论一致：**Spec 通过 / Standards 通过（含 1 处已收口）**。

---

## 5. 交付物

| 项 | 值 |
| --- | --- |
| 提交 | `1fa2795` fix(studio) + `84e3756` test/docs（review 收口 + 本文件 + PR 正文）+ docs（回填 PR 号） |
| 分支推送 | `issue-177-taskcount` → `origin`（`git push -u`，本地与远端同一 tip） |
| PR | [#178](https://github.com/whutlichao/alibot/pull/178)；body 路径 `app/specs/issue-177-pr-body.md` |
| worktree | `git status --porcelain` 为空（`.venv` 是本地符号链接，已被 `.gitignore` 覆盖；临时读数目录 `.evidence/` 已删除） |

PR 的 `Door` 如实声明 **two-way**（纯前端渲染逻辑，无 schema / 迁移 / API 语义变更、无新增依赖、
回滚就是 revert 一个函数抽取）；`Blast Radius` 写清 `renderTabs()` / `renderTaskBoard()` / `openTask()`
三类调用点的影响面。

---

## 6. 不做 / 遗留

- 不合并 PR（coordinator 合并）。
- 未归并 12 份 DOM 替身（`FakeNode`）：本仓既有形态，独立决策、独立票更合适。
- 未动后端任何一行；未新增 i18n 词条（本票不需要新文案）。
