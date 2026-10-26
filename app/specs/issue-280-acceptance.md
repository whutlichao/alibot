# 验收取证：#280 Agent 详情三分页签骨架与内容归位（含上下文三项撤除）

**票型**：实施票（前端骨架 + 内容归位 + 既有断言改写）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：[#280](https://github.com/whutlichao/alibot/issues/280)（验收正本 = 票面 AC）
**父规格**：`app/specs/agent-detail-tabs-spec.md`（D1 / D2 / D9 / T1–T4 / Out of Scope / N3）
**设计正本**：`app/specs/agent-detail-tabs-design-notes.md`（D1 / D2 / D9）
**base**：`0a359b8`（`agent-detail-tabs-integration` tip）｜**branch**：`issue-280-impl`
**改动面**：`app/alibot/studio/{app.js,app.css,i18n.js,index.html}`、
`app/tests/{agent_detail_ui,agent_capabilities_ui}.test.cjs`、本文件。
**后端零改动**：`app/alibot/*.py` 一行未动；`PATCH /api/agents/{id}` 的字段面与写入语义一字未改；
`agent_config` 三列与 DDL 默认值 40 / 0.7 / 30 原样；不 bump `SCHEMA_VERSION`、不写迁移。

---

## 1. 票面 AC → 落点 → 读数

### AC1 · 打开详情默认停在「基础信息和配置」；切到另一个成员后仍停在默认页签

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 打开详情停在 `base` | `app.js` `state.agentDetailTab`（默认 `AGENT_DETAIL_DEFAULT_TAB`）+ `openAgentDetail()` 里显式重置 | 用例「三个页签按固定顺序出现，打开详情默认停在基础信息和配置」：`state.agentDetailTab === 'base'`、`#adTabBase[aria-selected="true"]`、`#adPanelBase.hidden === false`，另两页签 `aria-selected="false"` + 面板 `hidden` |
| 切换成员回到默认页签 | `openAgentDetail()` 的 `state.agentDetailTab = AGENT_DETAIL_DEFAULT_TAB`（与 `activeAgentId` 同一处重置） | 用例「打开详情与切换成员都回到默认页签」：停在 `mcp` 时点另一位成员 ⇒ `agentDetailTab === 'base'`；关闭后再打开同样回 `base` |
| 不落库、不写 URL、不记忆上次页签 | 只有 `state` 里一位呈现状态；`grep "localStorage" app/alibot/studio/app.js` 与页签无交集 | 同两条用例：切走再回来永远是 `base` |

### AC2 · 三个页签可按顺序抵达并切换，选中态可读，键盘可用原生 Tab / Enter

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 顺序固定 `base → skills → mcp` | `AGENT_DETAIL_TABS`（`app.js:82`）与 `index.html` 里三个静态按钮同序 | 用例断言 `data-adtab` 序列 `deepEqual(['base','skills','mcp'])` |
| `data-adtab` / 三节点 / 容器类 | `index.html` `#adTabBase` / `#adTabSkills` / `#adTabMcp`，容器 `class="ad-tabs"` | 用例「正本里三个页签节点存在，旧页签名与 dock 页签容器仍不存在」：三个 `data-adtab` 值与三个 id 全部命中；`class="ad-tabs"` 命中 |
| 选中态可读 | `setAgentDetailTab()` 铺 `aria-selected` + `.is-active` + 面板 `hidden` | 用例「切换页签」：切到 `skills` 后 `#adTabSkills[aria-selected="true"]`、`#adTabBase[aria-selected="false"]`、非当前面板 `hidden` |
| 键盘可用原生 button | 正本是 `<button type="button" role="tab">`，无 `tabindex`、无自造方向键漫游 | 同上用例：正本正则断言每个页签是 `<button ... role="tab" ... id="adTabX">`，且 `tabindex=` 在三个页签上 0 命中 |
| a11y 三件 | 容器 `role="tablist"` + `aria-label`（走 `agentDetail.tabsLabel`）；按钮 `role="tab"` + `aria-controls`；面板 `role="tabpanel"` + `aria-labelledby` | 同上：`aria-controls` = `adPanelX`、面板 `role="tabpanel"` / `aria-labelledby` = `adTabX`；`aria-label` 的静态绑定正则为 `class="ad-tabs"[^>]*data-i18n-attr="aria-label:agentDetail\.tabsLabel"` |

### AC3 · 「基础信息和配置」页签内区块顺序 = 统计 → 系统提示 → 配置 → 危险操作，危险操作最后

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 四块顺序 | `agentBaseTabNodes()`（`app.js`）返回 `[facts, prompt, config, danger]` | 用例「基础信息和配置页签：统计 → 系统提示 → 配置 → 危险操作」：`#adPanelBase` 子树里四处 `data-od-id` 的序列 `deepEqual` 到该四元组 |
| 危险操作最后 | 同上（数组末位） | 同用例：`#adPanelBase` 的最后一个子节点的首个子节点 `data-od-id === 'agent-detail-section-danger'` |
| 既有 `data-od-id` 全保留 | 四块沿用 `agent-detail-section-facts` / `-prompt` / `-config` / `-danger`；能力相关沿用 `agent-capabilities-mcp` / `agent-capabilities-skill` / `agent-capabilities-scope` / `skill-list` / `mcp-list` / `mcp-form` / `skill-install` / `skill-import` / `agent-detail-section-global-mcp` / `capability-*-row` | 三条页签用例逐项 `byOdId` / `byDataset` 命中（明细见 §3 判定表的「保留」列） |
| 系统提示紧贴统计之下 | 数组第 2 位，且控件本身在 `agent-detail-prompt` 里 | 同用例：`at(facts) < at(agent-detail-prompt) < at(config)` |

### AC4 · 上下文三项的三格控件从界面消失；保存一次后库里那三列的值不变

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 三格控件撤除 | `agentConfigSection()` 里三段 `agentFieldRow('agent.contextWindow' \| …)` 整段删除；`agentDraftFrom()` 不再产出三个键 | 用例「长期记忆没有开关…」：`agentField(body, 'context_window_messages' \| 'context_trigger_ratio' \| 'react_max_iters') === null`，对应 `data-od-id="agent-field-*"` 也 `null` |
| 只服务它们的提示文案消失 | `agent.runtimeDefaultHint` 随第三格一起删（`i18n.js` 两份 catalog） | 用例「本票新增 / 沿用的文案…」：四条键在两份 catalog 里都 `undefined` |
| 保存一次后库里那三列不变 | 前端不再提交这三个键（`submitAgentEdit()` 的 7 字段载荷）⇒ 后端 `PATCH` 部分更新不写这三列 | 载荷断言见 AC5；后端侧零改动（`git diff 0a359b8 -- app/alibot/*.py` 为空，`python -m pytest app/tests/test_agent_detail.py` 全绿） |
| 「用两份不同的既有值各验一次」 | 见 §3 的一处**说明**：本票的 `PATCH` 是前端载荷口径，两列「两份不同既有值」的等价覆盖落在 (a) 用例「长期记忆没有开关…」用 `60 / 0.6 / 45`，(b) 用例「就地编辑只写草稿…」用默认 `null / null / null`；两处都断言「载荷里不存在这三键」 | 两处均为 `key in body === false` |

### AC5 · 保存请求体里不存在三个键

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 载荷 10 → **7** 字段 | `submitAgentEdit()`（`app.js`）：`display_name` / `role` / `model_id` / `system_prompt` / `enabled` / `auto_claim` / `web_access` | 用例「就地编辑只写草稿…」：`deepEqual(patch[0].body, {…7 个字段…})` |
| **另加**一条「三键不存在」 | 同用例末尾 | 同用例：对三个键逐个 `assert.equal(key in patch[0].body, false)` **且** `assert.equal(key in state.agentEdit.draft, false)`（N3 第一格：照抄旧载荷会一次清空三列） |

### AC6 · 三格对应的文案键在两份词条 catalog 里都已删除（不留孤儿键）

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| `agent.contextWindow` / `contextTrigger` / `maxIters` / `runtimeDefaultHint` 两份 catalog 都删 | `i18n.js`（zh-CN 与 en 各删四行） | 用例「本票新增 / 沿用的文案…」：四条键在两份 catalog 里 `undefined` |
| 不留别的孤儿键 | 拆成两个页签后 `agentCapabilities.title`（跨两 kind 的「能力配置」总标题）失去全部消费点 ⇒ 两份一并删（D9 外第 2 处，见 §4） | `grep "agentCapabilities.title" app/alibot/studio/` **0 命中**；`agent_capabilities_ui.test.cjs` 新增反向断言「两份 catalog 里都 undefined」 |

### AC7 · 切到 skills / mcp 再切回，未保存草稿与保存条仍在

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 切页签不动草稿 | `setAgentDetailTab()` 只改属性，**不重铺**面板 | 用例「切换页签：选中态跟着走…」：改 `display_name` 后切 `skills` → `mcp` → `base`，字段值仍是「打到一半」、`patches().length === 0` |
| 保存条全页级跨页签可见 | `#adSaveBar` 住在 `#viewAgent` 里、`#adBody` 之外（`index.html`），页签只切面板 | 同用例：`skills` / `base` 两次都 `saveBar.hidden === false` |

### AC8 · `PATCH /api/agents/{id}` 的三个可选字段与后端写入语义未改动（接口层零改动）

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 后端零改动 | `app/alibot/**` 未出现在 diff 里 | `git diff --stat 0a359b8` 只列 6 个前端/测试文件（见 §5） |
| 契约仍绿 | `app/tests/test_agent_detail.py`（含 `test_partial_patch_leaves_the_other_fields_alone`、`test_unknown_and_null_fields_are_rejected_whole`） | `scripts/check.sh` 全量：`1210 passed, 11 skipped` |

### AC9 · 既有「详情不是 dock 形态」断言仍绿；编辑配置弹窗仍不存在

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| `#dockDetail` / `.dock-tabs` 不存在 | 用例里四条「不存在」断言**原样保留** | 用例「正本里三个页签节点存在…」：`class="dock-tabs"` / `id="dockDetail"` / `id="adTabOverview"` / `id="adTabRuntime"` 四条正则均 `false` |
| `#agentEditOverlay` 不存在 | 同上保留；运行时也不查询 `#agentEdit*` | 同用例 + 用例「运行时不出现对已删编辑弹窗节点的 `$()` 调用」 |

---

## 2. 规格 D2 边界（票面之外但同票收口）

| D2 面 | 处置 | 落点 / 读数 |
| --- | --- | --- |
| 界面控件 | **撤掉**三格 number 控件与只服务它们的提示 | 见 AC4 |
| 前端草稿 | **撤掉**：`agentDraftFrom()` 不再产出三项 | 用例 `key in state.agentEdit.draft === false` |
| 前端提交面 | **撤掉**：10 → 7 字段 | 见 AC5 |
| 库形态 | **不动**：三列、DDL 默认 40 / 0.7 / 30、CHECK 约束 | `git diff 0a359b8 -- app/alibot/db.py` 为空 |
| `PATCH` 契约 | **不动**：三个可选字段与三态写入语义 | 见 AC8 |
| 运行时读取 | **不动**：`runtime.context_window_messages()` 与 `ReActConfig(max_iters=…)` 兜底 | `git diff 0a359b8 -- app/alibot/runtime.py` 为空 |
| 迁移 / 回填 | **不做** | `SCHEMA_VERSION` 未改；无新迁移步骤 |

**判据（D2 原文）**：`PATCH` 是**部分更新**——只有出现在请求体里的字段才写入。前端不再提交这三项 ⇒
库内既有值保持原值。反过来若前端继续提交 `null`，一次保存就会把三列清空——因此 AC5 的
「三键 `in body === false`」是本票最承重的一条断言。

---

## 3. `data-od-id` 归位表（既有标识全部保留）

| 既有 `data-od-id` | 现状位置 | 读数 |
| --- | --- | --- |
| `agent-detail-section-facts` | `#adPanelBase` | 用例 AC3 序列断言 |
| `agent-detail-prompt` | `#adPanelBase` | 同上 |
| `agent-detail-section-config` | `#adPanelBase` | 同上 |
| `agent-detail-section-danger` | `#adPanelBase`（最后一块） | 同上 |
| `agent-capabilities-skill` | `#adPanelSkills` | 用例「skills 页签…」 |
| `agent-capabilities-scope` | `#adPanelSkills` | 同上（`{member}` 仍渲染 `~/.alibot/agents/mem-1/skills/`） |
| `skill-list` / `capability-skill-row` / `skill-toggle` / `skill-delete` | `#adPanelSkills` | 同上 |
| `skill-install` / `skill-source` / `skill-install-dir` / `skill-zip` | `#adPanelSkills`（**原样搬，不删**，D3 是 #282 的范围） | `agent_capabilities_ui.test.cjs` 既有用例（技能目录安装、导入区块）仍绿 |
| `skill-import` / `skill-import-*` | `#adPanelSkills`（原样搬） | 同上 |
| `capability-copy` / `copyMcp` / `copySkill` / `capability-copy-dialog` | 各自页签内（#281 的范围是撤除） | `agent_capabilities_ui.test.cjs` 复制用例仍绿 |
| `agent-capabilities-mcp` | `#adPanelMcp` | 用例「mcp 页签…」序列断言首位 |
| `mcp-list` / `capability-mcp-row` / `mcp-form` / `mcp-*` | `#adPanelMcp` | 同上 |
| `agent-detail-section-global-mcp` | `#adPanelMcp` → 下块（`agent-detail-section-mcp-file` 那一块）**之内**，按 D1 并入下块、不再单独成段；该 od-id 按 review-fix 从「区块首个标题」改挂到 `<section>` **自身**（标题改 `agent-detail-section-global-mcp-title`） | 见 `agent-detail-tabs-review-fix-acceptance.md` 的 F2 / F3 |
| `agent-skill-detail`（新） | `#adPanelSkills` 右栏占位 | 占位文案 = `agentDetail.skillsDetailPlaceholder` |
| `agent-detail-section-mcp-file`（新） | `#adPanelMcp` 下块 | 占位文案 = `agentDetail.mcpFilePlaceholder` |

**新增标识**：`data-adtab="base|skills|mcp"`、`#adTabBase` / `#adTabSkills` / `#adTabMcp`、
`#adPanelBase` / `#adPanelSkills` / `#adPanelMcp`、容器 `.ad-tabs`、`#adTabs`、
`agent-detail-tabs` / `agent-detail-tab-*` / `agent-detail-panel-*`。

---

## 4. D9 断言改写清单 + 偏差登记（逐条判定表）

### 4.1 D9 已登记（本票照 D9 改）

| D9 # | 用例 | 改法 | 读数 |
| --- | --- | --- | --- |
| 1 | 文件头注释（`agent_detail_ui.test.cjs:11`） | 改成三分页签口径；`#adTabOverview` / `#adTabRuntime` / `.dock-tabs` 仍不存在 | 文件头 §2 段 |
| 2 | 「所有区块在同一页纵向铺开…」 | 拆成三条：`base` 四块顺序 / `skills` 页签内容 / `mcp` 页签两块 | 三条新用例全绿 |
| 3 | 「正本里不再有页签与编辑弹窗」 | 页签那一半**反转**（三个 `data-adtab` + 三节点 + `.ad-tabs`）；四条「不存在」**保留** | 用例「正本里三个页签节点存在…」 |
| 4 | 「本票新增 / 沿用的文案…」 | `tabsLabel` / `tabBase` / `tabSkills` / `tabMcp` 进「必须有值」；`tabOverview` / `tabRuntime` 仍 `undefined` | 该用例两段 |
| 5 | 同用例的「已删词条」清单 | 四条 `agent.contextWindow` / `contextTrigger` / `maxIters` / `runtimeDefaultHint` 移进「必须 `undefined`」 | 该用例末段 |
| 6 | 「就地编辑只写草稿…」载荷断言 | 10 → 7 字段 + **新增**「草稿里不存在这三键」 | 见 AC5 |
| 7 | 「配置区块的标签真的指向控件…」 | `label[for]` 字段清单去掉后三项（只剩 `display_name` / `role` / `provider` / `model`）；档位组 `aria-labelledby` 与 `textarea` 的 `aria-label` 原样 | 该用例 |
| 8 | `agent_capabilities_ui`「能力段：两种能力同屏」 | 拆成三条：skills 页签内左栏列表与行入口 / mcp 页签内库行块 / 两页签各读自己那一份 | 三条新用例全绿 |

### 4.2 D9 **未登记但本票必须改**（偏差登记；判据 = 断言与新行为正相反就必须改、且不许删原意）

| # | 用例 / 行 | 原断言 | 为何 D9 没盖住 | 改法与判据 |
| --- | --- | --- | --- | --- |
| A | `agent_detail_ui.test.cjs`「长期记忆没有开关：详情页无该控件，保存载荷不含 memory_mode」 | 三条 `agentField(...).value === '60' / '0.6' / '45'`；三条 `payload.<三键> === null` | D9 #6 只登记了「就地编辑」那条用例的载荷，漏了这条同类的载荷 + 回填断言 | 三条 `value` → `agentField(...) === null`（控件已撤除）+ 对应 `data-od-id` 也 `null`；三条 `payload.<键> === null` → `'<键>' in payload === false`；`memory_mode` 四条与用例标题**原样保留**。**该用例的新作用**：从「记录旧行为」变成 **D2 的正向守卫**——谁把三键加回载荷或草稿，它立刻红 |
| B | `agent_capabilities_ui.test.cjs`「新增文案在两份词条里同键同参数位」 | `agentCapabilities.title` 在「必须有值」清单里 | D9 #14 只点名 `skill.installDir` / `installDirZip` 一类，未提这个跨两 kind 的总标题 | 从「必须有值」清单删掉，并在末尾加**反向断言**（两份 catalog 都 `undefined`）：拆成两个页签后它失去全部消费点，留着就是孤儿键 |
| C | `agent_detail_ui.test.cjs`「详情头部只剩强调色的「发送消息」」 | 头部切片 = `agent-page-head` → `agent-page-scroll`，断言切片内 `<button>` 恰好 1 个 | 该用例断言「头部只有一个按钮」；页签容器若放进头部就是第二个按钮 | **不动断言，只把切片终点改到页签容器之前**（`class="ad-tabs"`）：页签是头部**下面**的一条，不属于「头部只剩什么」。原意（头部不残留标签 / 状态行 / 关闭控件）完整保留 |

**没有第 4 处**：`grep -nE "context_window_messages|context_trigger_ratio|react_max_iters|memory_mode|copyMcp|copySkill|skill-source|skill-zip|skillInstallDir|data-adtab|global-mcp|agentCapabilities.copy|capability-skill-row|skill-list"` 在两个测试文件的 64 处命中（基线 `0a359b8`：`agent_detail_ui` 42 处 + `agent_capabilities_ui` 22 处）已逐条判定：余下命中要么是替身 / 断言工具的形状（`agentDetail()` 的默认载荷字段）、要么是 `:not` 类负向断言、要么属 #281 / #282 / #283 / #284 的范围（复制入口撤除、两条安装入口撤除、正文右栏、`mcp.json` 文本域）——**本票一律不动**。

### 4.4 扫描判定表（判据口径：`grep -nE "context_window_messages|context_trigger_ratio|react_max_iters|memory_mode|copyMcp|copySkill|skill-source|skill-zip|skillInstallDir|data-adtab|global-mcp|agentCapabilities.copy|capability-skill-row|skill-list"`，基线 `0a359b8`：`agent_detail_ui` 42 处 + `agent_capabilities_ui` 22 处 = 64 处）

口径：① 已在 D9 登记 → 照 D9 改；② 未登记但必须改 → 按同一规则改并登记（§4.2 / §4.4）；③ 与本票无关 → 不动。

| 文件:行（基线） | 命中内容 | 判定 |
| --- | --- | --- |
| `agent_detail_ui:11` | 文件头「页签节点…在正本里不存在」 | ① D9 #1 → 改成三分页签口径 |
| `agent_detail_ui:210-212` | `agentDetail()` 替身载荷的三个字段 | ③ 不动：那是**后端响应形状**的替身，后端三列原样保留，替身必须照旧 |
| `agent_detail_ui:253` | `detailFetchBase` 的 `/global-mcp-servers` 应答 | ③ 不动：端点未改 |
| `agent_detail_ui:617` | 区块顺序断言里的 `agent-detail-section-global-mcp` | ① D9 #2 → 拆成三条页签用例；该 od-id 保留（并进 mcp 页签下块） |
| `agent_detail_ui:697` | `data-adtab=` 必须 `false` | ① D9 #3 → 反转成「三个值必须命中」 |
| `agent_detail_ui:765-767` | 「就地编辑」用例的 `deepEqual` 期望载荷三字段 | ① D9 #6 → 10 → 7 字段 + 新增三键不存在 |
| `agent_detail_ui:1366` | 用例标题「长期记忆没有开关…」 | ② §4.2-A → 标题保留，作用升级为 D2 正向守卫 |
| `agent_detail_ui:1368-1370` | 非默认既有值 `60 / 0.6 / 45` | ② §4.2-A → 保留（用作「库里有值也不回填」的输入），断言改成控件 `null` |
| `agent_detail_ui:1378-1379` | `memory_mode` 控件与探针 id 必须 `null` | ③ 不动：与本票无关的既有断言（长期记忆无开关） |
| `agent_detail_ui:1381-1383` | 三格 `value === '60' / '0.6' / '45'` | ② §4.2-A → `agentField(...) === null` |
| `agent_detail_ui:1385-1387` | 三次 `harness.edit('<三键>', '')` | ② §4.2-A → 三步删除（控件已不存在，edit 会红） |
| `agent_detail_ui:1391` | `'memory_mode' in payload === false` | ③ 不动 |
| `agent_detail_ui:1392-1394` | 三条 `payload.<三键> === null` | ② §4.2-A → `'<键>' in payload === false`（+ 草稿同断言） |
| `agent_detail_ui:1520/1528/1575/1613/1616` | 全局 MCP 用例的 fetch 替身 URL | ③ 不动：端点未改 |
| `agent_detail_ui:1551-1552/1565/1633/1637` | `[data-global-mcp-consent]` 只读确认按钮 | ③ 不动：语义与节点都在（只是搬到 mcp 页签），用例只从 `#adBody` 子树找，随面板移动自动成立 |
| `agent_detail_ui:1562` | POST consent 的 URL 断言 | ③ 不动 |
| `agent_detail_ui:1587/1597` | 全局 MCP 空态用例按 `agent-detail-section-global-mcp` 定位 | ③ 不动：该 od-id 保留在 mcp 页签内 |
| `agent_detail_ui:1905-1906` | `label[for]` 字段清单含后三项 | ① D9 #7 → 清单去掉后三项 |
| `agent_detail_ui:1945` | `detailFetchBase` 的全局 MCP 应答 | ③ 不动 |
| `agent_capabilities_ui:213-215` | 替身载荷的三个字段 | ③ 不动（同 `210-212` 的理由） |
| `agent_capabilities_ui:356` | 全局 MCP 的 fetch 正则 | ③ 不动 |
| `agent_capabilities_ui:645` | `capability-skill-row` 的 od-id 断言 | ① D9 #8 → 移进「skills 页签」那条新用例（断言内容原样） |
| `agent_capabilities_ui:657-658` | `copyMcp` / `copySkill` 复制入口必须存在 | ③ **不动**：撤复制入口是 **#281** 的范围，本票只搬位置 |
| `agent_capabilities_ui:703` | `skill-source` 手填路径安装 | ③ **不动**：撤两条安装入口是 **#282** 的范围（依 supervisor 裁定取「只搬不删」） |
| `agent_capabilities_ui:884/916` | 复制弹层用例 | ③ 不动（#281 范围） |
| `agent_capabilities_ui:948` | 频道配置里 `skill-list` 必须 `null` | ③ 不动：那是频道配置面，本票未碰 |
| `agent_capabilities_ui:994-1004` | `agentCapabilities.copy*` 词条必须有值 | ③ 不动（#281 范围：弹层撤除时一并删这些键） |

**说明**：`agentCapabilities.title`（跨两 kind 的「能力配置」总标题）不在上面这条 grep 的 pattern 里，但它因本票拆页签而失去消费点，属 §4.2-B 的偏差登记。

### 4.3 与 D9 相邻但**不改**的既有绿测（原样保留）

- 危险区 / 删除弹窗 / 私信归档 / 离线禁用 / 发送消息 5 条：只依赖 `#adBody` 子树，随面板移动自动成立。
- `channel_resources_ui.test.cjs` 的 `mcp-list` / `mcp-form` / `skill-list` 0 命中断言：那是**频道配置**面，本票未碰。
- `round_outcome_inspect_ui.test.cjs` 的 `#viewAgent.hidden` 断言：主线视图判据未变。

---

## 5. 门禁与测试读数

```sh
# 全量门禁（本票动了 .cjs 测试与 Studio 三件套 ⇒ 全量一次）
scripts/check.sh
# → pytest 1210 passed, 11 skipped（739.86s；合并 #281 后重跑 734.76s，同一读数）
# → ruff check: All checks passed!
# → ruff format --check: 通过
# → mypy: Success: no issues found in 128 source files
# → pyright: 通过
# → node --test app/tests/*.test.cjs: 329 tests / 329 pass / 0 fail
# → 门禁全绿

# 两个目标文件（合并 #281 之后：`agent_capabilities_ui` 从 16 条变成 15 条 = −2 条复制弹层用例 +1 条撤除守卫）
node --test app/tests/agent_detail_ui.test.cjs        # 58 tests / 58 pass / 0 fail
node --test app/tests/agent_capabilities_ui.test.cjs  # 15 tests / 15 pass / 0 fail

# 全量前端用例
node --test app/tests/*.test.cjs                      # 328 tests / 328 pass / 0 fail
# （合并集成分支 tip `974f5ed`（含 #281）之后：329 − 2（#281 删掉的复制弹层用例）+ 1（#281 的
#   「复制入口与弹层撤除」守卫，本分支解决冲突时一度丢失、已恢复）= 328）
```

### 与集成分支的合并（#281 撤复制弹层之后）

`git merge origin/agent-detail-tabs-integration --no-ff` 的三处冲突与解决口径：

| 文件 | 冲突来源 | 解决 |
| --- | --- | --- |
| `studio/app.js` | 本票用 `agentSkillsTabNodes` / `agentMcpTabNodes` 取代了 `agentCapabilitiesSection()`；#281 删掉了 `capabilityCopyNode()` 一族 | 取本票的页签版本；并把两个页签函数里各一处 `capabilityCopyNode` 调用删掉（否则引用已删函数） |
| `studio/i18n.js` | 段头注释：#281 改成「控件只作用于当前成员」，本票改成三分页签口径 | 取 #281 的句子 + 本票的页签说明；`agentCapabilities.title` 两份仍删（拆页签后无消费点） |
| `agent_capabilities_ui.test.cjs` | 本票「两 kind 各归自己页签」三条用例 vs #281「复制入口撤除」一条 | 两者并存；本票 skills 用例里删掉 `copySkill` 断言（#281 已撤）；「已删词条」清单合并成一份（`agentCapabilities.title` + `agentCapabilities.copy*`） |

合并后的额外自查：`capabilityCopyNode` / `view.copy` / `state.copy` 在 `studio/app.js` 里 **0 命中**
（#281 的守卫用例「复制入口与弹层撤除」会拦这条；实测把 `if (view.copy) capabilityCopyNode(view);`
注入回去 ⇒ 该用例立刻红，删除后恢复绿）。

### 红 → 绿证据

```sh
scripts/red-evidence.sh 0a359b8 app/tests/agent_detail_ui.test.cjs
scripts/red-evidence.sh 0a359b8 app/tests/agent_capabilities_ui.test.cjs
```

- 基线 `app.js` md5：`104ad4258577290d598cfc69fbf3066a`
- `agent_detail_ui.test.cjs` 读数（基线 `0a359b8`，合并前）：**58 tests / 48 pass / 10 fail**
  （证据目录 `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red.PBdqq5`）
  红的 10 条正是本票要改的点：页签节点 / 默认页签 / 切页签不丢草稿 / 三条页签内容 /
  `index.html` 页签正本 / 保存载荷 10→7 / 上下文三项撤除 / 词条四条键变 `undefined`。
- `agent_capabilities_ui.test.cjs` 读数：**16 tests / 13 pass / 3 fail**
  （证据目录 `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red.8tNpH9`）
  红的三条正是 D9 #8 拆出来的三条。
- 同一份测试在工作区（改动后）跑：58/58 与 16/16 全绿。
- 红证据只换测试不换产品代码，因此它证明的是「这些断言确实咬住了旧实现」。

---

## 6. 偏差与需要人定的问题

1. **D9 漏登两处（§4.2 的 A 与 B）**：已按 supervisor 裁定的同一条判据（「断言与新行为正相反就必须改、
   且不许删原意」）处置并登记在本文件；两处都把断言改成 D2 / 孤儿键的**正向守卫**，而不是删掉。
2. **skills 页签保留既有三条安装入口**（手填路径 / 上传 zip / 多选导入）：票面写「导入下拉框由 #282 补，
   本票只留占位」，但同时要求「既有 `data-od-id` 全部保留」。两种落法由 supervisor 裁定取
   **(A) 只搬不删**：#280 原样搬进 skills 页签，#282 在原位把它们收成一个下拉框。因此本票 skills 页签
   顶部**没有**下拉框，D1 的「导入 → 左栏 / 右栏」顺序由 #282 兑现。
3. **mcp 页签下块是占位，不是文本域**：`mcp.json` 的读写端点与文本域是 #284 的范围；本票只留
   `agent-detail-section-mcp-file` 节点 + 一行占位说明，并按 D1 把原「全局 MCP」只读小节并入 mcp 页签
   （该小节不再单独成段，`agent-detail-section-global-mcp` 这个既有 od-id 保住；review-fix 把它落进下块，
   并把 od-id 从标题改挂到区块自身——见 `agent-detail-tabs-review-fix-acceptance.md` F2 / F3）。
4. **AC4 的「用两份不同的既有值各验一次」**：本票的验证面是**前端载荷**（后端零改动、`PATCH` 部分更新
   语义原样），因此两处等价覆盖是「非默认既有值 60 / 0.6 / 45」与「默认 null / null / null」两组读数，
   都断言三键不出现。若要的是「真的落一次 PATCH 再读库」的端到端双值验证，那需要后端写路径用例
   （`app/tests/test_agent_detail.py` 已有 `test_partial_patch_leaves_the_other_fields_alone` 覆盖
   「部分更新不动其它字段」的同一份保证），本票未新开该用例。
