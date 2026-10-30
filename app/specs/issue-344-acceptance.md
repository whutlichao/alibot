# 验收记录：频道操作入口合并为单一「频道工具」入口

**票型**：实施票｜**门禁**：G-impl｜**测试档**：窄（改动只落前端 Vue 组件、词条正本与前端用例；
不触达后端、schema、构建配置或跨语言共享接口）
**Ticket**：#344｜**base**：`f31a9df`｜**branch**：`issue-344-impl`
**PR body**：`app/specs/issue-344-pr-body.md`
**采集时间**：2026-10-30｜**环境**：macOS Darwin 27.0.0 / Node v25.0.0 / Vitest 4.1.12（jsdom）

> 本文件在**创建 PR 之前**提交，只记当时已得的读数。PR 号与 CI 状态以 GitHub 为准，
> 不写进本文件、也不为回填它们追加提交（判据见 `docs/agents/implementation.md` §交付）。
> 需要留存的 PR / CI 读数写仓库外的运行报告（`/tmp/issue-344/run-report.md`）。

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① 红→绿：新增导航用例在改动前失败、改动后通过 | 隔离基线 worktree（`f31a9df`）+ 最终测试文件 | 红：`Test Files 4 failed \| 1 passed (5)` / `Tests 40 failed \| 85 passed (125)`；绿：`5 passed (5)` / `125 passed (125)` | ✅ |
| ② 窄档用例全绿 | 五个既有用例文件（channel-tools / channel-archive / channel-web-providers / components / app-shell） | `Test Files 5 passed (5)` / `Tests 125 passed (125)` | ✅ |
| ③ 门禁全绿 | `scripts/check.sh --fast` | `Test Files 25 passed (25)` / `Tests 500 passed (500)`；`门禁全绿` | ✅ |
| ④ 双轴 code-review（Standards + Spec，起点 `f31a9df`）已跑，findings 逐条处置 | 见「双轴 review」 | 两轴 findings 与处置见下 | ✅ |
| ⑤ 本验收记录按模板写好并在创建 PR 前提交 | 本文件 | 提交在 `issue-344-impl` 分支上 | ✅ |
| ⑥ 分支已推送、PR 含 `Closes #344` | `issue-344-impl` | 交付步骤：PR 创建后由 GitHub 记录（本文件不写 PR 号） | 交付步骤 |
| ⑦ `worker_done` 已发（显式 succeeded/failed、三句摘要、真实元数据） | 本终端 | 交付步骤：读数写仓库外 `/tmp/issue-344/run-report.md` | 交付步骤 |

## 红证据（隔离基线 `f31a9df`）

```text
# 基线 worktree：/tmp/issue-344/baseline（只覆盖分支上的最终测试文件；已清理）
cd /tmp/issue-344/baseline/app/frontend
env -u NODE_ENV npx vitest run tests/channel-tools.test.js tests/channel-archive.test.js \
  tests/channel-web-providers.test.js tests/components.test.js tests/app-shell.test.js

 Test Files  4 failed | 1 passed (5)
      Tests  40 failed | 85 passed (125)
```

失败读数的形状：`btn-channel-tools` 不存在（`Cannot call trigger on an empty DOMWrapper` /
`expected false to be true`）、旧三入口与频道头归档入口仍可点到、设置危险区没有归档按钮。
原文：`/tmp/issue-344/red-final.txt`。实现后同一批用例转绿（`/tmp/issue-344/green-final.txt`）。

## 改动落点

| 文件 | 改动 |
| --- | --- |
| `app/frontend/src/components/ChannelHeader.vue` | 三枚工具入口 + 归档入口收敛为一枚 `btn-channel-tools`（`open-tools`）；运行面板入口不变；注释同步 #344 的入口与归档位置 |
| `app/frontend/src/components/ChannelToolsModal.vue` | 新建：`ModalShell`（`wide`）唯一外壳 + `role="tablist"` 三页签；三面板一次挂载、`v-show` 切换；页脚「完成」；归档成功才关闭 |
| `app/frontend/src/components/ChannelRosterPanel.vue` | 由 `RosterModal.vue` 重命名（按 `CONTEXT.md` 的**成员名册**，避免与 dock 的**成员面板**混名）：去掉 `ModalShell` 与独立页脚，正文与 `roster-*` 标识逐字保留 |
| `app/frontend/src/components/ChannelSettingsPanel.vue` | 由 `ChannelConfigModal.vue` 重命名：去掉外壳与页脚；危险区新增 `btn-channel-archive`（当场执行）并保留删除的行内二次确认 |
| `app/frontend/src/components/ChannelBudgetPanel.vue` | 由 `ChannelBudgetModal.vue` 重命名：去掉外壳与页脚，正文与 `budget-*` 标识逐字保留 |
| `app/frontend/src/App.vue` | `openTool` 三态 → `channelToolsOpen` 单态；`open-tools` 接线；`archiveChannel` 返回成功布尔供弹窗决定是否关闭；删除成功后关闭工具弹窗 |
| `app/alibot/studio/i18n.js` | zh-CN / en 同键集新增 `channelTools.*`（entryTitle / title / sub `{name}` / 三枚页签 / closeAria / tabsLabel / done）；删除 12 个随外壳退役而零调用的旧入口词条（`roster.*` / `channelConfig.*` / `budget.*` 的 entryTitle / title / sub / closeAria） |
| `docs/adr/0040-channel-archive-and-delete-are-soft-delete-tiers.md` | 补一条 2026-10-30 的事实修正：归档入口移入频道工具 → 设置页签危险区，归档语义不变 |
| `app/frontend/tests/channel-tools.test.js` | 导航改为「点频道工具 → 选页签」；新增默认页签、三页签正文、切页签保草稿与档位、关闭重开重读等用例 |
| `app/frontend/tests/channel-archive.test.js` | 归档入口改在设置页签危险区；归档成功断言弹窗关闭、频道头只剩取消归档；新增归档失败不关弹窗 |
| `app/frontend/tests/channel-web-providers.test.js`、`components.test.js` | 导航辅助函数与频道头叶子断言同步新入口 |
| `app/frontend/src/components/DockPanel.vue`、`app/frontend/src/lib/member-view.js` | 因组件改名同步注释里的文件名 |
| `app/specs/issue-344-pr-body.md` | PR 正文（Summary / Evidence / Merge Danger） |

## 命令与读数

```sh
cd app/frontend
env -u NODE_ENV npx vitest run tests/channel-tools.test.js tests/channel-archive.test.js \
  tests/channel-web-providers.test.js tests/components.test.js tests/app-shell.test.js
# Test Files  5 passed (5)
# Tests  125 passed (125)

scripts/check.sh --fast
# ruff check: All checks passed!
# ruff format --check: 390 files already formatted
# mypy: Success: no issues found in 138 source files
# pyright: 0 errors, 0 warnings, 0 informations
# Vitest: Test Files 25 passed (25) / Tests 500 passed (500)
# 门禁全绿
```

本票测试档为窄档，不跑全量 `scripts/check.sh`（无 Python / 后端 / schema 改动）；CI 跑全量。

## 双轴 review

两轴各跑一个只读 reviewer 子代理（Standards / Spec 分开，互不抵消），起点 `f31a9df`。

### Standards 轴

审查对象：`git diff f31a9df...HEAD`（HEAD `26aab75`）＋审查期间的工作区改动；9 条 findings，7 条已修，
2 条判断项为 pre-existing / 范围外，不修并记理由。

| # | Finding | 类型 | 处置 |
| --- | --- | --- | --- |
| S1 | ADR-0040 决策⑤仍写「归档在频道头工具行」，与本分支实现冲突（文档与代码不一致，硬违规） | 文档 | ✅ 已修：ADR 补「2026-10-30 的事实修正」（入口移入设置页签危险区，归档语义不变） |
| S2 | 组件名 `ChannelMembersPanel` 与 `CONTEXT.md` 的**成员名册**词条不一致，且与 dock 的**成员面板**混名（Mysterious Name） | 命名 | ✅ 已修：改为 `ChannelRosterPanel.vue`，引用点同步 |
| S3 | 12 个旧入口词条（`roster.*` / `channelConfig.*` / `budget.*` 的 entryTitle / title / sub / closeAria）随外壳退役变为死键（Speculative Generality） | 死代码 | ✅ 已修：zh-CN / en 两表同键集删除 |
| S4 | 设置面板 `load()` 里的 `refreshChannelMembers()` 与成员页签重复读同一端点（Duplicated Code，一次打开两次 GET） | 重复 | ✅ 已修：读成员由成员页签独占 |
| S5 | 页签选择用 `TABS` 数组 + `includes` 做防御校验（Speculative Generality：页签 id 全在本组件内） | 过度防御 | ✅ 已修：`selectTab(tab)` 直接赋值 |
| S6 | `archiveChannel()` 里 `toolChannel \|\| target` 的 fallback 掩盖了「弹窗必有频道」的不变量（Speculative Generality） | 过度防御 | ✅ 已修：去掉 fallback，改注释写明不变量 |
| S7 | `ChannelRosterPanel` 的 `loaded` 注释与 `ChannelHeader` 的 `channelToolsAvailable` props 注释停留在旧入口语义 | 注释 | ✅ 已修 |
| S8 | 测试辅助 `openRoster` 只是转调 `openTools` + 选页签（Middle Man） | 测试重复 | ✅ 已修：删除，调用点直接用 `openTools` |
| S9 | 三个面板各自的 load / reset 骨架重复（Duplicated Code）；`ChannelSettingsPanel` 同时承担配置 / 凭证 / 危险区（Divergent Change） | 判断项 | ⏭ 不修：均为 pre-existing 形状——每个面板独立读自己的端点、设置面板自 #303 起就是三块合一；抽 composable 或拆文件是跨票重构，会扩大本票 blast radius，另票处理 |

### Spec 轴

审查对象为 HEAD `26aab75`（审查期间工作区尚未提交后续修复）；报告的 6 条 findings 全部在后续提交中消解，
无 scope creep 结论。

**(a) spec 要求但缺失 / 半实现**

| # | Finding | 处置 |
| --- | --- | --- |
| P1 | `app/specs/issue-344-acceptance.md` 不在 HEAD（交付契约要求每票落此文件） | ✅ 本文件随后提交，消解 |
| P2 | HEAD 的 ADR-0040 决策⑤仍写归档在频道头工具行，与实现冲突 | ✅ 后续提交补事实修正，消解（同 S1） |

**(b) spec 没要求的行为（scope creep）**：未发现。`DockPanel.vue` / `member-view.js` 只有注释随
组件改名同步；无后端、API、schema、依赖、`.github/workflows/**` 改动。`channelTools.tabsLabel` 与
`channel-tools-tabs/close/done` 等 `data-od-id` 是无害补充。

**(c) 看起来实现了但实现不对**

| # | Finding | 处置 |
| --- | --- | --- |
| P3 | HEAD 底栏用 `t('common.close')`（「关闭」），契约要求「完成」 | ✅ 后续提交改为 `channelTools.done`（「完成」）；关闭行为原本正确 |
| P4 | HEAD 的 12 个旧入口词条已成死键（契约不强制清理，仅提示） | ✅ 后续提交删除（同 S3） |

**核对通过的关键项**（Spec 轴原文）：频道头只剩 `btn-channel-tools` + `btn-run-panel`；归档 / 运行面板
在归档态整组不渲染；私信判据不变；`ModalShell` 唯一外壳且 `wide`；三页签顺序与 `data-od-id` 正确、
`role="tablist"`；默认成员页签；`v-show` 切换不重读、草稿与档位保留；关闭再开复位重读；三面板原
`data-od-id` 除各自 `*-done` 页脚外逐字保留；危险区归档当场执行、成功关弹窗、失败留弹窗；删除行为不变；
新旧测试导航已改写且未删断言。

### 逐条处置

两轴合计 15 条 findings（Standards 9 + Spec 6），互不重复地覆盖同一批根因：

- **已修 13 条**（Standards S1–S8、Spec P1–P4；其中 S1＝P2、S3＝P4 是同一事实的两轴表述）：

| 改动 | 涉及 finding | 落点 |
| --- | --- | --- |
| ADR-0040 补事实修正 | S1 / P2 | `docs/adr/0040-...md` |
| `ChannelMembersPanel.vue` → `ChannelRosterPanel.vue` | S2 | 组件文件 + `App.vue` / `ChannelToolsModal.vue` / 测试 / 注释引用 |
| 删除 12 个死词条 | S3 / P4 | `app/alibot/studio/i18n.js`（两表） |
| 设置面板不再重复读 members | S4 | `ChannelSettingsPanel.vue` |
| 去掉 `TABS`/`includes` 防御 | S5 | `ChannelToolsModal.vue` |
| 去掉 `toolChannel \|\| target` fallback | S6 | `App.vue` |
| 两处过时注释 | S7 | `ChannelRosterPanel.vue` / `ChannelHeader.vue` |
| 删除测试 `openRoster` 中间人 | S8 | `tests/channel-tools.test.js` / `channel-archive.test.js` |
| 验收记录入分支 | P1 | 本文件 |

- **不修 2 条**（S9 的两个判断项）：三面板 load/reset 骨架重复、设置面板三块合一的 Divergent Change
  都是 `f31a9df` 就存在的形状，本票只换外壳、不改各面板内部的读写结构；修它们属于跨票重构。
- **实现侧无 spec 偏差**：Spec 轴的 P1–P4 均因审查只看到 `26aab75`，后续提交已逐条消除；修复后
  窄档与门禁在最终代码上重跑（读数见「命令与读数」）。

## 说明

- **ADR-0040 的入口位置**：ADR 成文时记录「归档在频道头工具行」；#344 按用户新决定把入口移进
  频道工具 → 设置页签危险区。归档的实质语义不变（当场执行、可恢复、不弹二次确认、归档态只读、
  频道头只显示取消归档）。ADR 正文不在本票可编辑范围，未改；需要把入口位置写成事实修正时另票处理。
- **`CONTEXT.md` 的「成员名册」词条**仍写「弹窗里的全名单、唯一动作是关闭」；该描述在 #303 加入
  「加入」动作时已部分过时，本票只改了入口位置（成员页签）。文档不在本票 Target 范围内，未改。
- **浏览器真机验收**未跑：本票测试档只要求 jsdom DOM 契约，真机截图是可选项。
- **构建产物**未入库（ADR-0039）：`scripts/check.sh --fast` 在门禁头部构建
  `app/alibot/studio/v2/`，工作区保持只是源码改动。
- **环境准备**：新 worktree 按 `docs/agents/implementation.md` 把 `.venv` 指向仓库根；
  `npm ci --include=dev` 装前端 devDependencies（宿主 `NODE_ENV=production` 会跳过它们）。
