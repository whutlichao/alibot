# 验收取证：自主模式频道的运行面板空态解释与引导

**票型**：实施票（Studio 前端文案与空态分支）｜**门禁**：G-impl（合并由 coordinator 判定）
**Ticket**：[GitHub issue #244](https://github.com/whutlichao/alibot/issues/244)
**base**：`16f6400`（派活时 main HEAD）｜**branch**：`run-panel-empty-guidance`
**采集时间**：2026-10-23｜**环境**：macOS（aarch64）、Node 内置 test runner、
worktree `.venv` 符号链接到主工作区 `.venv`、后端以
`PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --port 8791 --data-dir /tmp/alibot-rp-evidence-20261023`
启动（确保服务的是本 worktree 的 Studio 静态文件，而非主工作区的 editable 安装）。
**改动面**：`app/alibot/studio/app.js`、`app/alibot/studio/i18n.js`、
`app/tests/run_panel_ui.test.cjs`、本文件。

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.claude/skills/implement/SKILL.md`。本票映射：

| implement 步骤 | 本票实际做的事 |
| --- | --- |
| 读票面 | 以 GitHub #244 为正本（现象 / 根因 / 期望已由 coordinator 只读诊断写定） |
| tdd 垂直切片 | 新增用例先与实现同批落地；实现定稿后用 `scripts/red-evidence.sh 16f6400` 在**隔离基线**回放同一测试，取得 3 条红读数（§2） |
| 单文件用例 + 门禁 | 迭代期跑 `node --test app/tests/run_panel_ui.test.cjs`；定稿跑 `scripts/check.sh --studio`（Studio 窄档）与 `node --test app/tests/*.test.cjs`（§3） |
| 双轴 review | 本票**省略**（票面理由：仅前端文案与空态分支，以 UI 测试与真实浏览器证据替代） |
| 提交 | 实现 + 验收文档 commit，推分支并开 PR（§6） |

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面 Change） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 频道面板 + 缓存 `mode === 'autonomous'` 时，「运行与分派」空态说明不产生 team 运行 / 分派，并引导切队长制 | `app.js` `runPanelChannelMode()` + `renderRunPanel()` 的 `runsEmptyAutonomous` 分支；文案 `i18n.js` | `node --test app/tests/run_panel_ui.test.cjs` | `16 passed`；用例「自主模式频道…」匹配「自主模式频道没有队长，不产生 team 运行与分派」「频道设置 → 协作模式」「队长制」 |
| 2 | 「PLAN」空态说明自主频道对话不产生 Plan，并指向任务线程头的「任务运行面板」 | `runPanelPlanSection(live, autonomousChannel)`；文案 `planEmptyAutonomous` | 同上 | 同一条用例匹配「自主模式频道的对话不产生 Plan」「任务运行面板」 |
| 3 | 「成员事件（仅直播）」「SOP」维持现有空态文案，不改语义 | `runPanelEventsSection()` / `runPanelSopSection()` **未改**（diff 为空） | 同上 | 自主用例仍匹配「还没有成员事件（仅直播」「还没有 SOP 运行」 |
| 4 | 频道面板打开时配置未缓存 → 调既有 `loadTaskChannelConfig(channelId)`；读回后若面板仍开着且同一频道则重绘 | `openRunPanel()` 新增读取块（仅频道面板；任务面板不读） | 同上 | 自主用例断言 `GET /api/channels/ch_1/config` 恰好 1 次；浏览器证据里首帧后即出现解释文案 |
| 5 | 配置读不到（返回 null / 未缓存）→ 通用空态，**不得**误报自主模式 | `loadTaskChannelConfig()` 既有失败返回 `null`；读取块 `.then` 无条件重绘，`runPanelChannelMode()` 因未缓存返回 `null` | 同上 | 用例「频道配置读取失败…」：配置 500 后仍匹配「还没有 team 运行记录」「还没有 Plan。」，且不出现「自主模式」「队长制」 |
| 6 | 任务线程面板（`taskId` 非空）与 team 模式频道空态保持原文案 | `runPanelChannelMode()` 在 `taskId` 非空时直接返回 `null`；team 模式不命中 `autonomous` | 同上 | 用例「team 模式频道…」与「任务线程运行面板…」均匹配原空态、不出现「自主模式」 |
| 7 | 新增文案进 `i18n.js` 的 zh-CN 与 en 两套目录；`app.js` 不写死中英文 | `i18n.js` 两表各新增 `runPanel.runsEmptyAutonomous` / `runPanel.planEmptyAutonomous` | 同上 | 词条用例断言两表均有值且取值不同（不是把中文复制进 en） |
| 8 | 不推算模式：只用 `state.channelConfigs` 缓存，读不到就退化 | `runPanelChannelMode()` 只读缓存，不做任何推断 | 同上 + 浏览器证据 | 配置失败用例；浏览器证据中三个场景的读数与缓存一致 |
| 约束 | 不改后端 / API / 数据库 / ADR / 规格文档；不改 CSS；不新增依赖 | `git diff --stat` | `git diff --stat 16f6400` | 仅 `app.js` / `i18n.js` / `run_panel_ui.test.cjs` / 本文件；CSS 无 diff |
| 约束 | 词表遵循 `CONTEXT.md`：频道、队长制、自主模式、运行面板、任务线程 | `i18n.js` 新文案逐字使用这些词 | — | 见文案原文 |

---

## 2. 先红后绿（隔离基线）

用仓库脚本在**基线产品代码**上回放当前测试（只换测试、不换产品代码）：

```console
$ ./scripts/red-evidence.sh 16f6400 app/tests/run_panel_ui.test.cjs
基线：16f6400 (16f6400)
...
✖ 自主模式频道：运行与 Plan 空态解释原因与去处；成员事件 / SOP 空态保持原样
  AssertionError: 频道面板打开时按既有读取路径读一次配置 — 0 !== 1
✖ 频道配置读取失败：退回通用空态，不误报为自主模式
  AssertionError: 尝试读取配置 — 0 !== 1
✖ 运行面板的新词条在两份词条表里都取到值
  AssertionError: runPanel.runsEmptyAutonomous 在源语言里缺失
ℹ tests 16
ℹ pass 13
ℹ fail 3
✖ failing tests: ...
结果：用例失败（红读数）；证据目录保留在 /var/folders/.../T/alibot-red.cvrqle
```

说明：三条红读数分别对应「打开面板时读配置」「失败仍读一次并保持通用空态」「新词条存在」；
team 频道与任务线程两条用例在基线本来就应该绿（它们断言的是**原有**行为），实现后仍绿。
完整输出另存于 `/tmp/alibot-rp-evidence-20261023/red-baseline.txt`。

实现后的绿读数：

```console
$ node --test app/tests/run_panel_ui.test.cjs
✔ 频道入口打开运行面板：读 team-runs / plan，分派按 member 分组并显示状态、耗时、excerpt
✔ 任务线程入口用 task target 读快照，team-runs 带 task_id 过滤；关闭后落回任务线程
...
✔ 自主模式频道：运行与 Plan 空态解释原因与去处；成员事件 / SOP 空态保持原样
✔ team 模式频道：沿用原空态文案，不出现自主模式解释
✔ 任务线程运行面板：即使所属频道是自主模式，空态也沿用原文案
✔ 频道配置读取失败：退回通用空态，不误报为自主模式
✔ 运行面板的新词条在两份词条表里都取到值
ℹ tests 16
ℹ pass 16
ℹ fail 0
```

---

## 3. 门禁读数

```console
$ ./scripts/check.sh --studio
...
ℹ tests 273
ℹ pass 273
ℹ fail 0
门禁全绿
```

```console
$ node --test app/tests/*.test.cjs
ℹ tests 273
ℹ pass 273
ℹ fail 0
ℹ duration_ms 3791.735125
```

本票按票面**不跑**全量 pytest / pyright / mypy（无 Python 改动）；Studio 窄档已覆盖 ruff / mypy /
pyright / node glob / 审批 HTTP 契约 / 前端静态守卫。

---

## 4. 真实浏览器证据（ego-browser）

后端与数据：临时数据目录 `/tmp/alibot-rp-evidence-20261023`；新建「自主频道」
（`ch_40a154e08f33`，默认 autonomous）与「队长制频道」（`ch_ee22fd7bf0c6`，PATCH 为 team +
队长成员），并在自主频道建一个任务（`task_874eb13966a1`）。浏览器为 ego-browser（真实 Chromium），
操作路径全是页面点击：左栏频道 → 频道头运行面板入口；任务看板卡片 → 任务线程头的任务运行面板。

| 截图 | 场景 | 观察 |
| --- | --- | --- |
| `/tmp/alibot-rp-evidence-20261023/shots/01-autonomous-channel-run-panel.png` | 自主模式频道 · 运行面板 | 运行与分派显示「自主模式频道没有队长，不产生 team 运行与分派。要看 team 运行，请在「频道设置 → 协作模式」把频道切到队长制。」；PLAN 显示「自主模式频道的对话不产生 Plan。任务运行的记录挂在任务上，请从任务线程头的「任务运行面板」查看。」；成员事件 / SOP 文案与原来一致。 |
| `/tmp/alibot-rp-evidence-20261023/shots/02-team-channel-run-panel.png` | 队长制频道 · 运行面板 | 四段仍是原文案（「还没有 team 运行记录。」「还没有 Plan。」等），没有自主模式解释。 |
| `/tmp/alibot-rp-evidence-20261023/shots/03-task-thread-run-panel.png` | 任务线程 · 任务运行面板（所属频道为 autonomous） | 面板标题为任务名「验证任务线程空态」；空态仍是原文案，没有自主模式解释。 |

截图 SHA-256：

```text
2b41c7c4c8340c449b72a42631a22d30a31e5d3827e9660b566a491ac757636a  01-autonomous-channel-run-panel.png
bf146f4f5566616aafb64c33391d9bf7914b2d63e16b47809c9b8c9aae147fcc  02-team-channel-run-panel.png
d194d0a0bc057f492861e4591ff969938a04ae6a3fe4de32c68d517008346204  03-task-thread-run-panel.png
```

页面内读数（`innerText`）与截图一致；布局无需新增 CSS，380px dock 宽度可容纳两段解释。

---

## 5. 改动摘要

```diff
 app/alibot/studio/app.js
+function runPanelChannelMode()          // 只读 state.channelConfigs；task / 未缓存 → null
-function runPanelPlanSection(live)
+function runPanelPlanSection(live, autonomousChannel)
+  emptyKey = autonomous ? 'runPanel.planEmptyAutonomous' : 'runPanel.planEmpty'
 renderRunPanel()
+  autonomousChannel = runPanelChannelMode() === 'autonomous'
+  emptyKey = autonomous ? 'runPanel.runsEmptyAutonomous' : 'runPanel.runsEmpty'
 openRunPanel()
+  if (频道面板 && 配置未缓存) loadTaskChannelConfig(channelId).then(同频道则 renderRunPanel)
 app/alibot/studio/i18n.js
+runPanel.runsEmptyAutonomous   (zh-CN / en)
+runPanel.planEmptyAutonomous   (zh-CN / en)
 app/tests/run_panel_ui.test.cjs
+4 条用例（自主 / team / 任务线程 / 配置读取失败）+ 词条清单补两键
```

`git diff --stat 16f6400`：

```text
 app/alibot/studio/app.js        |  33 ++++++++--
 app/alibot/studio/i18n.js       |   4 ++
 app/tests/run_panel_ui.test.cjs | 138 +++++++++++++++++++++++++++++++++++++++-
 3 files changed, 169 insertions(+), 6 deletions(-)
```

---

## 6. 交付

- 分支：`run-panel-empty-guidance`（从 `16f6400` 切出；本票未合并集成分支，PR 目标 `main`）。
- PR：创建后回填编号与正文路径（见下）。
