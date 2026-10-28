# 验收记录：Agent 详情 skills 页签的「从本机导入」区改版

**票型**：实施票｜**门禁**：G-impl + G-merge｜**测试档**：窄（`scripts/check.sh --fast` + 受影响的两个 vitest 文件）
**Ticket**：本票**没有** GitHub issue（用户反馈直派），交付件用描述性命名。
**base**：`7b0443f`（= 派活时 `main`）｜**branch**：`studio-v2-skill-import-panel`
**PR**：[#331](https://github.com/whutlichao/alibot/pull/331)｜**PR body**：`app/specs/studio-v2-skill-import-panel-pr-body.md`
**采集时间**：2026-10-28｜**环境**：macOS（aarch64）、Python 3.11.13（仓库根 `.venv`）、ego-browser 0.6.0.20

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① 四个被删节点不存在 | `AgentDetailView.vue` skills 面板 | 浏览器探针：`agent-capabilities-skill` / `skill-import-command` / `skill-import-command-hint` 均 `null`；导入区 `.d-sec-title` 与 `.resource-command` 均 `null`；作用域那句 `agent-capabilities-scope` **仍在** | ✅ |
| ② label 文案是新值 | 词条 `skill.importPickLabel`（两份语言表） | 浏览器读到 `从本机导入（读取 ~/.agents/skills 路径下的 skills）`（= `i18n.t('skill.importPickLabel')`）；en 为 `Import from this machine (reads skills under ~/.agents/skills)` | ✅ |
| ③ 下拉面板有固定高度上限 | `app.css` `.skill-import-menu { max-height: 260px; overflow-y: auto; position: absolute }` | 浏览器 @1365x978：面板 `height 260`、`scrollHeight 604` > `clientHeight 258`、`maxHeight 260px`、`overflowY auto`、`position absolute`；外层 `.agent-page-scroll` 前后都是 `883 / 883`（**没被撑高**），面板 `bottom` 在视口内 | ✅ |
| ④ 点「刷新」发一次 `GET /api/skills/candidates` | `data-od-id="skill-import-scan"` → `detail.loadImportCandidates(memberId)` | fetch 探针：点击前 1 次（打开详情时那次），点击后 2 次；新增那次是 `GET /api/skills/candidates?...`；服务端日志同计 | ✅ |
| ⑤ 点「全部导入」对每个可导入候选各发一次 `POST /api/skills`，已安装 / 不可导入一次都不发 | `importAllCandidates()`（`stores/agent-detail.js`） | 浏览器：当前 3 条可导入（`omicron/pi/rho`）⇒ `postCount 3`；同一次会话里 16 条已安装与 1 条不可导入候选一次都没发（结果行只有那 3 条）。vitest 用例另用 4 条候选（1 已安装 + 2 可导入 + 1 不可导入）钉住同一事实 | ✅ |
| ⑥ 导入中按钮禁用 | 三个控件同读 `importLocked` | 浏览器在点击后 200ms 取样：`allDisabled true`、`scanDisabled true`、`pickerDisabled true`，并显示「正在导入…」；结束后 `allDisabled` 因「已无可导入候选」仍为 true、`scan` 恢复可点 | ✅ |
| 结果区高度上限 + 内部滚动 | `.skill-import-status { max-height: 160px; overflow-y: auto }` | 浏览器：`maxHeight 160px`、`overflowY auto`；9 行那次（前一轮）`statusHeight 160` / `scrollHeight 183`（内部在滚），本次 3 行时 `74.375` | ✅ |
| 汇总行出现 | 新词条 `skill.importAllSummary`，`data-od-id="skill-import-summary"` | 浏览器读到 `全部导入完成：成功 3 条，跳过 0 条，失败 0 条。` | ✅ |
| 收尾与单条导入同一条（重读技能列表 + 重扫候选） | `settleSkillImport()` | 重扫后展开下拉：刚导入的 3 条已进「已安装」组（16 条，全部 `disabled`） | ✅ |
| 三组语义保留、后两组禁选、原因写进条目 | 面板分组标题 + `:disabled` | 浏览器读到「已安装」「不可导入」两组；`broken-skill（不可导入：技能根目录缺少 SKILL.md）` 为禁用 | ✅ |
| Esc 收起并把焦点还给触发控件；点面板外收起 | `onImportKeydown` / `onImportOutsidePointer` | 浏览器：Esc 后面板消失、`aria-expanded=false`、`document.activeElement` 是触发控件；点面板外同样收起 | ✅ |
| 卸载不留监听 | `onBeforeUnmount` + `watch(importMenuOpen)` | vitest：卸载时 `document.removeEventListener('pointerdown', …)` 被调用 1 次；卸载后派 `pointerdown` 不抛错、面板不复活 | ✅ |
| 窄档命令全绿 | `scripts/check.sh --fast` + 两个 vitest 文件 | 见 §2 读数 | ✅ |

### 红证据（隔离基线 `7b0443f`）

同一份用例在基线 worktree（`/tmp/skil-baseline` = `git worktree add … 7b0443f`，只把两个测试文件换成当前工作区版本，
产品代码保持基线字节 `AgentDetailView.vue` md5 `7207dd8c9fc285815f2a2a5b35302ee4`）上跑：

```text
 Test Files  1 failed (1)
      Tests  8 failed | 36 passed (44)
```

失败的 8 条正是本票新增/改写的 8 条（被删节点、label 文案、面板高度上限、下拉分组与点选导入、Esc/外点/卸载、
「刷新」、 「全部导入」、忙态禁用）。同一支浏览器探针在基线实例（8798）上读到：`pickerTag SELECT`、
`labelText 从本机导入`、命令块文本 `npx skills add <owner/repo> -g -a codex -y`、
`scanButton null`、`allButton null`、`menuNode false`、`cssMaxHeight none`（原生 `<select>` 的弹出层高度
不受 CSS 控制，这正是 6a 要改成自绘的原因）。

---

## 1. 实现

**改动面**

| 文件 | 改动 |
| --- | --- |
| `app/frontend/src/components/AgentDetailView.vue` | 删四个节点；原生 `<select>` → 自绘下拉（`role=listbox`，Esc / 外点 / 卸载清理）；加「刷新」「全部导入」两个按钮与汇总行 |
| `app/frontend/src/stores/agent-detail.js` | 删 `SKILL_INSTALL_COMMAND`；抽 `postSkillImport` / `settleSkillImport`；新增 `importAllCandidates`；`skillImportSummary` 新状态；`loadImportCandidates` 重新暴露 |
| `app/alibot/studio/i18n.js` | 删 3 个空词条（`agentCapabilities.skillTitle` / `skill.importTitle` / `skill.importCommandHint`）；改 `skill.importPickLabel` / `importUnavailable` / `importEmpty`；加 `importScanButton` / `importAllButton` / `importAllSummary`（两份语言表同键同参数位） |
| `app/alibot/studio/app.css` | 删 `.resource-command`；新增本页签独占的 `.skill-import-*` 段（260px 菜单 / 160px 结果区 / 绝对定位浮层） |
| `app/frontend/tests/agent-detail.test.js` | 8 条用例改写或新增 |
| `CONTEXT.md` | `Agent 详情页签` 词条：撤掉「顶部只有下拉框一个安装入口」的旧口径 |
| `docs/adr/0036-skill-install-has-two-sources.md` | §二 事实修正：界面不再展示命令；重扫改为「刷新」按钮 |
| `app/README.md` | Skill 段那句「Studio 只展示这条可复制的命令」的事实修正（协调者另行授权，见 Answer） |

**三个取舍**

- **自绘而不是原生 `<select>`**：`<option>` 的弹出层由浏览器绘制，`max-height` / `overflow` 都够不到它。
  面板用 `role=listbox` 的普通节点、绝对定位、自带 260px 上限与内部滚动（与 `MentionPicker.vue` 同一种
  交互语汇），高度上限才落得进 CSS，也不占布局高度、不进 #328 的固定高度链。
- **单条与批量共用一步写路径**：`postSkillImport()` 只发一次 `POST /api/skills` 并按 `409` 归因，
  `importPickedSkill()` 与 `importAllCandidates()` 只在「怎么挑出候选」和「要不要汇总」上分叉。
  批量串行（后端按条校验与复制，并发没有收益，只多出一种交错失败面）。
- **「刷新」不包第二层**：`loadImportCandidates` 本身就是只读读路径，按票面要求重新暴露到 detail 返回对象，
  组件直接调；重入面由按钮的 `:disabled` 与 store 内的 `skillImportLoading` 一起挡。

---

## 2. 门禁读数

```sh
cd app/frontend && env -u NODE_ENV npx vitest run tests/agent-detail.test.js tests/i18n-catalog.test.js
# Test Files 2 passed (2) · Tests 49 passed (49)

scripts/check.sh --fast
# build: ✓ built in 921ms（v2 产物由本脚本在门禁前生成，不入库）
# ruff check:           All checks passed!
# ruff format --check:  373 files already formatted
# mypy（仓库根）:       Success: no issues found in 136 source files
# pyright:              0 errors, 0 warnings, 0 informations
# Vitest:               Test Files 24 passed (24) · Tests 455 passed (455)
# 门禁全绿
```

（`--fast` 按要求跳过全量 pytest：本票无 Python 改动；CI 仍跑全量。）

**CI（全量复核）**：

- head `09a97a5`（含全部代码改动）的 `check` run
  [40629656072](https://github.com/whutlichao/alibot/actions/runs/40629656072) → **success**（含全量 pytest）。
- head `7a700ed`（仅本文档与 PR body 的回填）→ run
  [40630467848](https://github.com/whutlichao/alibot/actions/runs/40630467848) → **success**。
- head `8cb7dbc`（仅本文档的回填）→ run
  [40631400246](https://github.com/whutlichao/alibot/actions/runs/40631400246) → **success**。
- 本机 `--fast` 跳过的正是全量 pytest 那一段；上面三次 CI 都跑了它。此后不再逐条登记纯文档回填
  触发的 run（它们不改任何代码，工作流相同）。

---

## 3. 浏览器证据（隔离后端，ego-browser 0.6.0.20）

两个隔离实例，**都不碰用户的 8788 与真实数据目录**：

- **8797 本分支**：本 worktree 的 `app/`，`HOME=/tmp/skill-import-panel-home`、`--data-dir /tmp/skill-import-panel-data`。
- **8798 基线 `7b0443f`**：`git worktree add /tmp/skil-baseline 7b0443f`，
  `HOME=/tmp/skil-baseline-home`、`--data-dir /tmp/skil-baseline-data`。

种子 `/tmp/skill-import-panel-evidence/seed.py <port> <home>`：建 1 个 Agent、往隔离 `~/.agents/skills/`
放 11 个条目（10 可导入 + 1 不可导入），其中 `alpha-skill` 通过既有 `POST /api/skills` 导入（于是候选里
出现「已安装」组）；后续轮次又补了 `lambda/mu/nu/omicron/pi/rho` 用于忙态与终验复测。

**终验（重构后 + 重建产物，viewport 1365x978）**

| 读数 | 基线 `7b0443f`（8798） | 本分支（8797） |
| --- | --- | --- |
| `agent-capabilities-skill` | 存在 | `null` |
| `skill-import-command` / `-hint` | 存在（`npx skills add <owner/repo> -g -a codex -y`） | 均 `null` |
| 导入区 `.d-sec-title` / `.resource-command` | 存在 | 均 `null` |
| `label[for=skillImportPicker]` | `从本机导入` | `从本机导入（读取 ~/.agents/skills 路径下的 skills）` |
| 触发控件 | `SELECT`，`maxHeight none` | `BUTTON`，`aria-haspopup=listbox`，`aria-expanded` 正确翻转 |
| 「刷新」/「全部导入」 | 都不存在 | `刷新` / `全部导入`（词条取值） |
| 展开面板 | 无面板节点（原生弹出层，CSS 管不到） | `height 260` / `scrollHeight 604` / `clientHeight 258` / `maxHeight 260px` / `overflowY auto` / `position absolute` |
| 面板展开时外层 `.agent-page-scroll` | — | `883 / 883`（前后不变，**没被撑高**），面板 `bottom` 在视口内 |
| 点「刷新」 | 无此按钮 | fetch 探针新增 1 次 `GET /api/skills/candidates?...` |
| 点「全部导入」 | 无此按钮 | `postCount 3`（= 当前可导入且未安装的 3 条），结果行 3 条，汇总 `全部导入完成：成功 3 条，跳过 0 条，失败 0 条。` |
| 导入中三控件 | — | `skill-import-all` / `skill-import-scan` / 触发控件三个全 `disabled`，显示「正在导入…」 |
| 结果区 | 无上限（`.resource-meta` 直排） | `maxHeight 160px` / `overflowY auto`；9 行时 `160 / 183`（内部在滚） |
| 重扫后分组 | — | 16 条进「已安装」组（全部禁用），`broken-skill` 仍在「不可导入」 |
| Esc / 点面板外 / 卸载 | — | Esc 收起且焦点回触发控件；外点收起；卸载撤掉 `document` 上的 `pointerdown` 监听 |

截图（`/tmp/skill-import-panel-evidence/`，不入库）：`baseline-1365x978-skills.png`（基线：命令块 + 原生下拉）、
`accept-01-menu-1365x978.png`（自绘面板、260px 上限）、`accept-02-busy-1365x978.png`（忙态三控件禁用）、
`accept-03-import-all-1365x978.png`（结果行 + 汇总）、`accept-04-installed-1365x978.png`（重扫后进「已安装」）、
`final-1365x978-skills.png`。

---

## 4. 双轴 code-review 与逐条处置

按 `/Users/apple/.agents/skills/code-review/SKILL.md`，两个 reviewer 子代理并行跑（Standards / Spec 各一）。

### Standards 轴：3 处硬违规（**全部采纳**，已修在 `41401dd`）

| Finding | 处置 | 理由 |
| --- | --- | --- |
| `app.css` 候选 hover/focus 用 `--selected-deep` 压 `--selected-soft`，本仓实测 3.44:1（低于未选中的 7.08:1），且与紧邻注释「与 `.card-menu-item` 同一条」自相矛盾 | **采纳** | 文字色改回 `--fg`（信号只走底），注释按实测数字重写 |
| 点一条候选后先还焦、随即被 `importLocked` 禁用 ⇒ 焦点掉回 `body`，违 `stores/agent-detail.js:641` 已写明的仓内规则 | **采纳** | 点选导入不再回焦；Esc 仍把焦点还给触发控件（票面 6a 的原文要求） |
| `docs/adr/0036` §三 ADR-0009 条目仍写「命令本身是 ASCII 常量」，与同文件新增事实修正冲突（`review-rules.md` 第 4 条） | **采纳** | 改成「命令块已随本票删除，不再有这类取值」 |

判定项 1 处（**不采纳，登记代价**）：注释声称与 `MentionPicker` 同一种交互语汇，但后者契约含 `↑↓`/Enter，
新面板只有 Esc。票面 6a 只要 `aria-haspopup` / `aria-expanded` / Esc / 外点 / 卸载清理；条目是原生 `<button>`，
Tab 可逐个停留。已在 §5 记明差异——补齐方向键导航超出本票授权面。

基线异味 5 处：抽 `runSkillImports()`（前两段收尾逐行重复）、删 `candidateSelectable` 这层 Middle Man、
`importLocked` 统一三处禁用条件 —— **采纳**；`summary[result.status]` 裸字符串计数键 **不采纳**（三个键
就是结果区的三个取值，词表已在顶部一处）；测试里 `css.slice` 抄 CSS 数值 **部分采纳**（票面 ③ 明写这是
两种判据之一，且同时断言了面板结构与 class）。

**已核对无问题**：三处文档口径一致、被删常量/词条/CSS 类全仓无残留（除历史 specs 与反向断言）、
token 未越界、未新开接缝。

### Spec 轴

Spec 轴的子代理挂起 12 分钟无文本输出（transcript 里只有工具调用），因此改由主 agent 按票面逐条自检
——判据与读数就是上面 §Status 与 §1–§3 的那张逐条表：四条删除、`importUnavailable`/`importEmpty` 改写、
label 逐字、6a 的九个观察点、6b 的两个按钮与汇总、结果区上限、以及全部 Constraints（零 Python 改动、
三个全局语汇零声明改动、四态 `data-od-id` 原样、两份语言表对齐、没跑 `ruff format`、产物未入库）。

### 修复后的复测

`41401dd` 之后重跑：`npx vitest run tests/agent-detail.test.js tests/i18n-catalog.test.js` → **49 passed**；
`scripts/check.sh --fast` → 24 files / **455 passed**、ruff / format / mypy / pyright 全绿。
浏览器（1365x978）复核：候选 hover/focus 后 `color` 仍是 `oklch(0.93 0.018 300)`（= `--fg`，与未选中同色），
面板 `maxHeight 260px` / `overflowY auto` / `position absolute` 不变；点一条候选 ⇒ `POST /api/skills` 恰 1 次、
面板收起、占位文案复位、结果行 `omega-skill：已导入`、无汇总行（单条不产汇总）。

---

## 5. 遗留 / 说明

- `≤900px` 单栏与另外两个页签的行为未动；面板的 260px 上限与绝对定位在两种布局下都成立（不依赖 `is-skills-fixed`）。
- 面板没有做键盘方向键导航（`↑↓`）：票面只要求 `aria-haspopup` / `aria-expanded` / Esc / 外点 / 卸载清理，
  Tab 可在候选间移动（条目是原生 `<button>`）。这是与 `MentionPicker` 的差异点，若后续要一致，单开一票。
- 点选导入**不把焦点还回触发控件**（这一下点击会立刻让它禁用，焦点会掉回 `body`）；Esc 关闭仍按票面回焦。
- 本地只跑 `--fast`；全量 pytest 由 CI 在本 head 上复核。
- 隔离实例（8797 / 8798）在证据归档后关闭，只关本票创建的 PID。
