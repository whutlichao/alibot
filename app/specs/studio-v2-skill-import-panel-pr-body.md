## Summary

skills 页签的「从本机导入」区**去掉命令块、换成自绘下拉、加两个按钮**：界面不再教人敲 `npx`，只读
`~/.agents/skills` 这个落点；下拉候选再多也不会把固定高度工作区撑高。

```diff
 section.d-sec.agent-skill-section                     (@1365x978，固定高度工作区的顶端)
-  ├── div.d-sec-title[data-od-id="agent-capabilities-skill"]  「技能」标题
   ├── div.resource-meta[data-od-id="agent-capabilities-scope"]  ← 保留
   └── div.resource-add[data-od-id="skill-import"]
-      ├── div.d-sec-title                                     「从本机导入」标题（词条 skill.importTitle）
-      ├── div.resource-command > code[skill-import-command]    npx skills add <owner/repo> -g -a codex -y
-      ├── div.resource-meta[skill-import-command-hint]         「先在本机终端运行上面的命令…」
-      └── select[skill-import-picker]                          原生下拉（弹出层高度 CSS 管不到）
+      ├── label[for=skillImportPicker]  「从本机导入（读取 ~/.agents/skills 路径下的 skills）」
+      ├── .skill-import-picker-box
+      │     ├── button[skill-import-picker]  aria-haspopup=listbox / aria-expanded
+      │     └── div[skill-import-menu]       role=listbox · max-height:260px · overflow-y:auto
+      │           ├── .skill-import-group  「可选 / 已安装 / 不可导入」
+      │           └── button[role=option]  点一条 = 立即导入（一次一个）
+      ├── .skill-import-actions
+      │     ├── button[skill-import-scan]   「刷新」→ GET /api/skills/candidates
+      │     └── button[skill-import-all]    「全部导入」→ 逐条 POST /api/skills
+      └── .skill-import-status[skill-import-status]   max-height:160px · overflow-y:auto
+            ├── .import-result × N
+            └── [skill-import-summary]  成功 / 跳过 / 失败 三个数
```

写路径**一个字没改**：单条与批量都走同一条 `POST /api/skills`（`source_path` = 候选 path），
`stores/agent-detail.js` 里抽出的 `postSkillImport()` 是两者共用的那一步，批量只多一个串行循环与一行汇总。

三个取舍：

- **自绘下拉，不用原生 `<select>`**：`<option>` 的弹出层由浏览器画，`max-height` / `overflow` 都够不到它
  ——候选一多就铺满一屏。面板改成 `role=listbox` 的普通节点，绝对定位、260px 上限、内部滚动，
  与 `MentionPicker.vue` 同一种交互语汇；不占布局高度，因此不进 #328 留下的固定高度链
  （实测展开前后外层 `.agent-page-scroll` 都是 `883 / 883`）。
- **「刷新」不包第二层**：`loadImportCandidates` 本身就是只读读路径，按票面重新暴露到 detail 返回对象，
  组件直接调；重入面由按钮的 `:disabled` 与 `skillImportLoading` 一起挡。
- **连带的事实修正**（coordinator 授权）：`CONTEXT.md` 的 `Agent 详情页签` 词条、ADR-0036 §二、
  `app/README.md` 的 Skill 段——三处都写着「Studio 只展示这条可复制的命令」，命令块删掉后它们成了事实错误。

## Evidence

**Before / After（隔离实例，ego-browser 0.6.0.20，viewport 1365x978）**

8797 = 本分支（`HOME=/tmp/skill-import-panel-home`、`--data-dir /tmp/skill-import-panel-data`）；
8798 = 基线 `7b0443f`（`git worktree add /tmp/skil-baseline 7b0443f` + 独立 HOME / data-dir）。
种子：1 个 Agent、`~/.agents/skills` 下 11 个条目（10 可导入 + 1 不可导入），其中 1 条先经
`POST /api/skills` 导入，于是候选里出现「已安装」组。

| 读数 | 基线 `7b0443f` | 本分支 |
| --- | --- | --- |
| `agent-capabilities-skill` / `skill-import-command` / `skill-import-command-hint` | 三者都在（命令文本 `npx skills add <owner/repo> -g -a codex -y`） | 三者均 `null` |
| 导入区 `.d-sec-title` / `.resource-command` | 都在 | 均 `null` |
| `label[for=skillImportPicker]` | `从本机导入` | `从本机导入（读取 ~/.agents/skills 路径下的 skills）` |
| 触发控件 | `SELECT`，`maxHeight: none`（弹出层 CSS 管不到） | `BUTTON`，`aria-haspopup=listbox`，`aria-expanded` 正确翻转 |
| 「刷新」/「全部导入」 | 都不存在 | 都在（`刷新` / `全部导入`） |
| 展开面板 | 无面板节点 | `height 260` / `scrollHeight 604` / `clientHeight 258` / `maxHeight 260px` / `overflowY auto` / `position absolute` |
| 面板展开时外层 `.agent-page-scroll` | — | `883 / 883`（展开前后相同，**没被撑高**），面板底在视口内 |
| 点「刷新」 | 无此按钮 | fetch 探针新增 1 次 `GET /api/skills/candidates?...` |
| 点「全部导入」 | 无此按钮 | 3 次 `POST /api/skills`（= 当前可导入且未安装的 3 条），结果行 3 条，汇总 `全部导入完成：成功 3 条，跳过 0 条，失败 0 条。`（已安装 / 不可导入的候选一次都没发） |
| 导入中 | — | 三个控件全 `disabled` + 「正在导入…」 |
| 结果区 | 无上限 | `maxHeight 160px` / `overflowY auto`；9 行时 `160 / 183`（内部在滚） |
| 重扫后 | — | 刚导入的 3 条进「已安装」组（16 条，全部禁用）；`broken-skill` 仍在「不可导入」 |
| Esc / 点面板外 / 卸载 | — | Esc 收起且焦点回触发控件；外点收起；卸载撤掉 `document` 的 `pointerdown` 监听 |

截图：`baseline-1365x978-skills.png`（基线：命令块 + 原生下拉）→ `accept-01-menu-1365x978.png`（自绘面板 260px）、
`accept-02-busy-1365x978.png`（忙态禁用）、`accept-03-import-all-1365x978.png`（结果行 + 汇总）、
`accept-04-installed-1365x978.png`（重扫后进「已安装」）。截图与探针脚本在 `/tmp/skill-import-panel-evidence/`，不入库。

**自动回归**

`app/frontend/tests/agent-detail.test.js` 改写/新增 8 条：被删四个节点、label 新文案、面板固定高度上限
（同时断言 `app.css` 里的规则与面板结构/class）、下拉三组与点选即导入、Esc+外点+卸载清理、
「刷新」一次 GET、「全部导入」逐条 POST + 已安装/不可导入零请求 + 汇总行、导入中三控件禁用。

**红证据在隔离基线上取得**（`git worktree add /tmp/skil-baseline 7b0443f`，只换两个测试文件，
产品代码保持基线字节 `AgentDetailView.vue` md5 `7207dd8c9fc285815f2a2a5b35302ee4`）：

```text
 Test Files  1 failed (1)
      Tests  8 failed | 36 passed (44)
```

失败的 8 条正是本票新增/改写的 8 条。

**门禁**

```sh
cd app/frontend && env -u NODE_ENV npx vitest run tests/agent-detail.test.js tests/i18n-catalog.test.js
# Test Files 2 passed (2) · Tests 49 passed (49)

scripts/check.sh --fast
# build ✓ built in 921ms
# ruff check          All checks passed!
# ruff format --check 373 files already formatted
# mypy                Success: no issues found in 136 source files
# pyright             0 errors, 0 warnings, 0 informations
# Vitest              Test Files 24 passed (24) · Tests 455 passed (455)
# 门禁全绿
```

（`--fast` 按要求跳过全量 pytest：本票无 Python 改动；CI 仍跑全量。）

**双轴 code-review 的处置（`41401dd`）**

Standards 轴报 3 处硬违规，全部采纳并已修：

1. 候选 hover/focus 用 `--selected-deep` 压 `--selected-soft` —— 本仓实测这一档只有 3.44:1（低于未选中的
   7.08:1）。改成文字仍是 `--fg`（信号只走底），与 `.card-menu-item:hover` 真正对齐。
2. 点一条候选后先把焦点还给触发控件、随即它被禁用 ⇒ 焦点掉回 `body`。改成点选导入不回焦
   （Esc 关闭仍按票面回焦）。
3. ADR-0036 §三 的 ADR-0009 条目还写着「命令是 ASCII 常量」——随本票删掉该句。

另收三处判定项：抽 `runSkillImports()` 消除单条/批量重复的收尾段、删掉 `candidateSelectable` 这层
Middle Man、三处禁用条件统一到一个 `importLocked`。**未采纳**的两条已在验收文档记明理由与代价
（`↑↓` 键盘导航超出票面授权；`summary` 的字符串键就是结果区那三个取值）。

Spec 轴子代理挂起无输出，改由主 agent 按票面逐条自检（表在验收文档 §4）。修复后复测：49 passed +
455 passed；浏览器复核候选 hover/focus 后文字色与未选中同色、面板上限与定位不变、点一条恰 1 次 POST。

## Merge Danger

**Door:** two-way（双向门）

纯呈现层 + 词条表 + 文档的改动，revert 一个 commit 就回到原状：没有数据写入、没有 schema、没有迁移、
没有 HTTP 契约变化，也没有用户配置被改写。`POST /api/skills` 与 `GET /api/skills/candidates` 的语义
一个字未动（批量只是同一个端点被调 N 次）。

**Blast Radius:** 小（studio 的一个页签 + 三处文档事实修正）

- 只影响 Agent 详情 → skills 页签的导入区。新 CSS 全部带 `.skill-import-*` 前缀，写在既有 skills 页签
  样式段里；`.d-sec` / `.resource-add` / `.btn` 这些全局共用语汇**一个声明都没改**。
- 删掉了 3 个词条（`agentCapabilities.skillTitle` / `skill.importTitle` / `skill.importCommandHint`）
  与 `SKILL_INSTALL_COMMAND` 常量：全仓 grep（含测试）零引用；其余页签不读它们。
- 面板是绝对定位浮层，不参与 #328 的固定高度链——实测展开前后外层滚动器 `883 / 883`，`≤900px` 单栏
  与另外两个页签的行为未动。
- 「全部导入」是串行、一次一条；一条失败（含 409）只记进结果区，不中断其余条目，也不写右栏错误行。
- 已知取舍：面板没有 `↑↓` 键盘导航（票面只要求 `aria-haspopup` / `aria-expanded` / Esc / 外点 / 卸载清理），
  Tab 可在候选间移动（条目是原生 `<button>`）。

本票**没有** GitHub issue（用户反馈直派），因此正文不写 `Closes #`。
