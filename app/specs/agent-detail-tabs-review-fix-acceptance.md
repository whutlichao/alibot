# 验收取证：`agent-detail-tabs-integration` 双轴 code-review 的全部 findings 收口

**票型**：review-fix（不是新票；处置对象 = `agent-detail-tabs-integration` 上那一次双轴 `code-review`
的 findings）｜**门禁**：G-impl（全量 `scripts/check.sh`；合并由 coordinator 执行）
**base**：`25da05b`（= `agent-detail-tabs-integration` tip）｜**branch**：`agent-detail-tabs-review-fix`

**上游正本**：

- 规格：`app/specs/agent-detail-tabs-spec.md`（**D9 末段的「文档同步」**、D1 / D2 / D7）
- 设计正本：`app/specs/agent-detail-tabs-design-notes.md`（**D1 第 143 行**、D9 第 333 行、N3）
- 五票验收：`app/specs/issue-280..284-acceptance.md`
- 门禁纪律 `docs/agents/implementation.md`；评审判据 `docs/agents/review-rules.md`（判断题 6 / 7）

**改动面**：`app/README.md`、`app/alibot/api.py`（模块 docstring 两行）、
`app/alibot/studio/{app.js,i18n.js}`、`app/tests/agent_detail_ui.test.cjs`、
`app/specs/{issue-280,issue-283,issue-284}-acceptance.md`（只改与本次修复直接相关的句子）、本文件。

**没动的面**：`docs/adr/**` / `CONTEXT.md` / `.github/**` / `scripts/**` / `app/alibot/db.py` /
`SCHEMA_VERSION` / 迁移链 / 装配层 / 优先级 / 消息面 / 预算与唤醒纪律；
`app/tests/test_skill_copy.py` 一字未动（#281 的端点契约原样绿）；`studio/index.html` 零改动。

---

## 1. F1 · `app/README.md` 补文档同步（Spec 轴 FAIL 项）

D9 末段原文要求「新增状态 / kind / 端点时 README 的相关系段落一起改」（`review-rules.md` 判断题 6）。
逐处处置：

| # | 原句（base `25da05b` 行号） | 处置 | 现落点（`app/README.md`） |
| --- | --- | --- | --- |
| 1 | `:727-729`「所有区块在同一页纵向铺开——统计 / 系统提示 / 配置 / 全局 MCP / 危险操作，**没有页签**（「概览 / 运行时」两个页签已删除；…）」 | 改三分页签口径：基础信息和配置（统计 → 系统提示 → 配置 → 危险操作）→ skills（左栏列表 / 右栏选中技能详情）→ mcp（库行表单 / 家目录 `mcp.json` 文本域与条目视图）；写明打开详情与切换成员都回默认页签、`data-adtab="base\|skills\|mcp"`、`state.agentDetailTab` 不落库不写 URL。**「日志页签仍不做（D24 / #72）」保留** | `:732-738` |
| 2 | `:315`「Studio 详情页的「配置」区块可设置上下文条数、阈值与迭代上限」 | 改撤除口径：三项界面入口已撤除、保存载荷不再提交这三个键、`PATCH` 的三个可选字段与库内默认值（40 / 0.7 / 30）原样 | `:316` |
| 3 | `:91`（数据目录一节）「`mcp.json` 是**只读导入源**…**本轮不提供导出写路径**」 | 改「导入源 + **Studio 的写面**（人类面直接编辑原文，保存前校验）；过渡期仍是只读装配层；**装配正本仍是 `mcp_server` 的 Agent 级库行**；#275 仍收尾导入命令与移除装配层」 | `:91-94` |
| 4 | `:109`（备份小节注释）「mcp.json 是人工维护的只读导入源（应用不写）」 | 改「人工维护的导入源，并可在 Studio 的 mcp 页签里直接改原文（写入发生在宿主进程）」 | `:109-110` |
| 5 | `:245`（`mcp_server` 表行）「…本轮无导出写路径，#275 收尾」 | 同上口径（导入源 + Studio 写面；装配正本仍是库行；#275 收尾导入命令与移除装配层） | `:246` |
| 6 | `:492`（Docker 只读挂载）「成员家目录 `mcp.json` 是只读导入源，宿主进程不写它」 | 按 ADR-0031 §三修订后的口径改成「`mcp.json` 的写入（含 Studio 的直接编辑原文）都发生在宿主进程的人类面」 | `:493-494` |
| 7 | `:520`（MCP 声明 bullet）「`mcp.json` 只作只读导入源…本轮**不提供导出写路径**（#275 收尾）」 | 同上口径 | `:521-523` |
| 8 | `:665`（端点表 `global-mcp-servers` 行）「…本轮不提供导出写该文件的路径（#275）」 | 改成只读**条目视图**口径，并指向新增的三条端点 | `:667` |
| 9 | 端点表（`:600` 一带）缺三条 | 补 `GET /api/agents/{id}/global-mcp-file`（读：原文 + 条目计数，不存在 ⇒ `exists=false` + 空串）、`PUT /api/agents/{id}/global-mcp-file`（整份原文写盘，三道校验全过才写，任一条不合法 ⇒ 400 + `fields` + **零写入**；原文逐字写；家目录不存在 ⇒ 400 且不建目录）、`GET /api/skills/{id}/markdown`（一个技能 `SKILL.md` 的正文，只读；front matter 之后的字节；`ok` / `missing` / `invalid` / `unreadable` 四态） | `:668-669`、`:695` |
| 10 | `:762-764`「「全局 MCP」小节」单段 | 按 F2 后的新位置改：它在 mcp 页签的**下块**里、与文本域是同一份数据的两种视图，不再单独成段 | `:771-774` |

**读数**（可复跑）：

```sh
grep -n "没有页签\|导出写路径\|只读导入源\|应用不写\|宿主进程不写它" app/README.md   # → 无输出（退出码 1）
grep -n "global-mcp-file\|skills/{id}/markdown" app/README.md
# → 668 / 669 / 695 三行（两条 global-mcp-file + 一条 skills markdown）
grep -n "data-adtab" app/README.md        # → 735（三个页签值 + 不落库不写 URL 同句）
grep -n "界面入口已撤除" app/README.md     # → 316
```

---

## 2. F2 · 「全局 MCP」只读小节挂错块（Spec 轴必须改）

**finding**：设计正本 D1 / `design-notes:143` 原文是「「全局 MCP」小节**并入** mcp 页签的**下块**
内容，不再单独成段」。base 把它挂在上块（`agentMcpTabNodes()` 里 `rowsBox.appendChild(globalMcpSection())`）。

**处置**：`globalMcpSection()` 改挂到**下块**（`fileBox.appendChild(globalMcpSection())`），位置在文本域
之后——判据是「下块 = 家目录 `mcp.json` 这一份数据」：条目视图就是该文件里真实存在的条目，与文本域
是同一份数据的两种视图。上块（`rowsBox`）只留库行表单。

**落点**：`app/alibot/studio/app.js` `agentMcpTabNodes()`（下块 `fileBox` 追加 `globalMcpSection()`）、
`globalMcpSection()` 的注释改成下块口径。

**读数**：用例「全局 MCP 条目视图在 mcp 页签的下块里（与 mcp.json 文本域同一块），不在库行块里」：

- `fileBox.contains(entries) === true`、`descendants(fileBox).indexOf(entries) > indexOf(textarea)`
- `rowsBox.contains(entries) === false`、`byOdId(rowsBox, 'mcp-form')` 仍命中

---

## 3. F3 · `replaceSectionNode` 的嵌套替换粒度 bug（Standards 轴 P1）

**finding**：base 的实现只在 `#adPanelMcp` 的**直接子节点**里找「自身或后代命中 odId」的那个，然后
`nodes[index] = replacement`。命中落在后代时会把**整个直接子块**换掉。F2 之后 `panel.children ===
[rowsBox, fileBox]`，而条目视图在 `fileBox` 里 ⇒ 一次 `refreshGlobalMcpSection()`（脏草稿时点「确认」
远端 MCP 触发）会把**整个下块（文本域）**换成孤零零的条目视图；F2 之前则会吃掉整个上块。

**修法：选 (a)**，理由三条（写清是因为「选更小更稳的那个」要留判据）：

1. **(a) 让替换粒度本身正确，而不是让调用点绕开它。** 标识挂在 `<section>` 自己身上后，
   「找到自身命中的节点 → 用它的 `parentNode` 原位替换」是**对任何嵌套深度都成立**的语义；
   (b) 只修「命中后代」的路径，若将来同一区块的标题被移进更深的一层，判据仍要再改一次。
2. **不依赖任何新的 DOM API**。两种都走 `parentNode` + `replaceChildren`，替身 DOM（只保证
   `replaceChildren` / `appendChild` / `removeChild`）都能跑通，**没有**用 `Node.replaceWith()`。
3. **od-id 的语义更准**：条目视图是一个「区块」，标识本来就该在区块根上；标题另有
   `agent-detail-section-global-mcp-title`（与 `agent-detail-section-mcp-file` 那种「标识挂区块根」
   的既有形态一致——`agent-mcp-file` / `agent-skill-detail` 都是这个形状）。

**标题 od-id 改名的连带面**（已逐条核对，`grep -rn "agent-detail-section-global-mcp" --include=*.cjs
--include=*.js app/`）：

- `app/alibot/studio/app.js`：`refreshGlobalMcpSection()` 的 odId 参数不变（仍是
  `agent-detail-section-global-mcp`，现在它挂在区块根上）；`sectionTitle(...)` 的 odId 改成
  `…-title`。
- `app/tests/agent_detail_ui.test.cjs`：`sectionOf(body, 'agent-detail-section-global-mcp')` 两处
  （空态中文 / 英文）改成 `byOdId(body, 'agent-detail-section-global-mcp')`——**语义不变**：base 的
  `sectionOf` 就是「标题的父节点 = 区块」，现在标识直接挂在那个区块上，取到的是同一个节点。
- 五票验收文档里指向这个 od-id 的句子：`issue-280-acceptance.md` 两处（§3 归位表、§6 偏差 3）
  补一句实际落点，见下面「文档同步」小节。
- 没有任何其它测试文件、Python 测试、i18n 词条或 `index.html` 引用它。

**落点**：`app/alibot/studio/app.js` 新增 `findNodeByOdId(root, odId)`（前序、只认「自身命中」），
`replaceSectionNode()` 改成用它 + `parentNode` 的 children 原位替换。

**红证据（隔离基线，`scripts/red-evidence.sh 25da05b app/tests/agent_detail_ui.test.cjs`）**：

```text
基线：25da05b (25da05b)
基线 app.js md5：a7815fb54087aeb3ec8071a470115873
✖ 全局 MCP 小节不再有「来自家目录 mcp.json」那句提示；空态用新文案
    AssertionError: 空态逐字等于新文案（全角引号、mcp.json 前后无空格）  false !== true
✖ 全局 MCP 条目视图在 mcp 页签的下块里（与 mcp.json 文本域同一块），不在库行块里
    AssertionError: 条目视图在下块子树里
✖ mcp 页签自己有未提交输入时确认全局 MCP：只换条目视图，下块文本域与上块都不换
    AssertionError: 下块的文本域节点没被换掉
✖ mcp.json 文本域有未提交输入时，后台重绘不换掉文本域节点，人打的字与节点都留着
    AssertionError: 文本域节点没被换成新节点（焦点与光标不丢）
结果：用例失败（红读数）；证据目录保留在 /var/folders/.../alibot-red.XqZXDx
```

（第一条红是 F2 的位移带来的：base 的条目视图在上块，因此按 `agent-detail-section-global-mcp` 取到的
区块里没有空态文案——`hint` 在另一个块；后两条红正是 F3 / F6 的 bug 本身。）

---

## 4. F4 · `api.py` 模块 docstring 端点清单（Standards 轴 P1）

**处置**：

- 补 `GET /api/agents/{id}/global-mcp-file`（家目录 `mcp.json` 的原文 + 条目计数；不存在 ⇒
  `exists=false` + 空串）与 `PUT /api/agents/{id}/global-mcp-file`（整份原文写盘；JSON / 顶层形状 /
  每条归一化三道校验全过才写，原文逐字）两行（`app/alibot/api.py:38-39`）。
- `GET /api/skills/{id}/markdown` **已在**清单里（`api.py:55`），不重复。
- `app/specs/issue-284-acceptance.md` §7.3 原写「本票已在模块 docstring 端点清单里补了两行」——
  **与文件不符**（该提交的 `api.py` diff 只动了 `GlobalMcpDocumentUpdate` 与两条端点的函数
  docstring，没动模块 docstring 清单）。按「改文档让它是真话」处置：那句改成事实修正——
  「本票当时并未真的补两行，那两行由 `agent-detail-tabs-review-fix` 补上」。

**读数**：

```sh
grep -n "global-mcp-file" app/alibot/api.py   # → 38 / 39（模块 docstring 清单）+ 1872 起的端点实现
sed -n '36,42p' app/alibot/api.py
```

---

## 5. F5 · 两处说谎的注释（Standards 轴 P2）

| 位置 | 原注释 | 现注释 |
| --- | --- | --- |
| `app.js:7348` 一带 | 「MCP 声明与技能**只有一个归口**：Agent 详情的能力段（**同屏两个 kind**，作用域恒为该成员…）」 | 「…Agent 详情页（`#280` 起拆成 skills / mcp 两个页签，MCP 在 mcp 页签、技能在 skills 页签；两处作用域恒为该成员…）」 |
| `app.js:8037` 一带 | 「重铺能力相关的那两个页签：读路径与**显式动作**（编辑 / 安装 / **复制** / 启停 / 删除）都走它」 | 删掉「复制」（该入口已由 #281 撤除） |

**顺带扫一遍本票图动过的三件套**（只改注释、不改行为）：

| 位置 | 处置 |
| --- | --- |
| `app.js:209` 「Agent 详情里的**全局 MCP 只读小节**（#236）」 | 改成「全局 MCP **条目视图**（#236；#280 起它在 mcp 页签的下块里）」 |
| `app.js:5243` 「列表失败只影响这一小节」 | 改成「只影响这一块（mcp 页签下块的条目视图）」 |
| `app.js:7369` 「归口在 Agent 详情的能力段」 | 改成「归口在 Agent 详情页（MCP 在 mcp 页签、技能在 skills 页签）」 |
| `app.js:5288` 「**只读**与**只吃后端读数**的那几块」 | 改成「**只读**的全局 MCP 条目视图」 |
| `app.js:5573` `agentMcpTabNodes` 的文档注释「原「全局 MCP」只读小节（条目视图）**并在上块里**」 | 改成下块口径（「下块是同一份文件的两种视图」） |
| `app/alibot/studio/i18n.js` 两份 catalog 的全局 MCP 段注释「成员家目录 mcp.json 是**只读素材层**…不写回文件」 | 改成「条目视图**只读**（它住在 mcp 页签的下块里，与旁边的 mcp.json 文本域是同一份数据的两种视图）…要改原文走上方的文本域」 |
| `index.html` | 已逐条核对：页签 / 面板 / 详情头三处注释与本票实现一致（三分页签、`.ad-tabs`、不复用旧名），**零改动** |

**未改的同类措辞**：`能力段` 这个词在 8 处注释里出现（`app.js:5134/5292/5476/7467/8076/10033/10050/10122`）。
它指的是「Agent 详情里 MCP + 技能这两个能力区块」这一整块，拆页签之后仍准确（两个页签合起来就是
「能力段」），且设计正本 `design-notes:260/344` 与 `spec:263` 也用这个词——改词会让正本与实现对不上。
因此本轮**只改与实况冲突的那两处**（F5 的正题），不做术语清洗。

---

## 6. F6 · `capabilityDraftDirty()` 未纳入 `mcpFileDraft`（Standards 轴 P2）

**finding**：base 的 `capabilityDraftDirty()` 只看 `view.mcpEditingId` 与 `view.mcpDraft` 五个字段，
不看 `mcpFileDraft`。后果：后台重绘（`renderAgentDetail()` 的非脏分支 → `renderAgentTabPanels`）会换掉
正在输入的文本域节点（文字因 `$('#adBody')` 的输入委派兜住不丢，但**焦点与光标丢**），与
`app.js:5278-5280` 自述的纪律相反。

**处置**：

- `emptyCapabilitiesView()` 新增 `mcpFileBase`：**后端读回来的那一份原文**（读路径与保存成功都写它，
  经新的 `setMcpFileText(view, text)` 同时写 `mcpFileDraft` 与 `mcpFileBase`）。
- `capabilityDraftDirty()` 增加一条 `if (view.mcpFileDraft !== view.mcpFileBase) return true;`
  ——语义正是「mcp 文本域有未提交改动」。

**为什么需要 `mcpFileBase` 而不是「`mcpFileDraft` 非空」**：文件本来就非空时（真实场景的常态），
「非空」恒真，脏判据会退化成「mcp 页签永远不重铺」，把后台读数刷新也一起关掉。用「与后端读数不一致」
既精确又保留「不脏时照常重铺」的既有行为。

**读数**：用例「mcp.json 文本域有未提交输入时，后台重绘不换掉文本域节点，人打的字与节点都留着」：

- 前提：`textarea.value === MCP_FILE_TEXT`（后端那份原文）
- 在文本域里改字 → 派发 `input` → `renderAgentDetail()`
- 断言：`byOdId(panel, 'agent-mcp-file-text') === textarea`（**同一节点**）、`textarea.value === typed`

---

## 7. F7 · 回归用例（三条，落在既有缝 `app/tests/agent_detail_ui.test.cjs`）

| 用例（新增） | 覆盖 | 关键断言 |
| --- | --- | --- |
| 「全局 MCP 条目视图在 mcp 页签的下块里（与 mcp.json 文本域同一块），不在库行块里」 | **F2** | `fileBox.contains(entries)`；`indexOf(entries) > indexOf(textarea)`；`rowsBox.contains(entries) === false`；`byOdId(rowsBox,'mcp-form')` 命中 |
| 「草稿脏时确认全局 MCP：上下两块都还在，只有条目视图被就地换掉」 | **F3**（页级草稿脏 → 走 `refreshGlobalMcpSection()` 分支） | 上块 `agent-capabilities-mcp` / `mcp-list` / `mcp-source-db` / `mcp-form` / 库行都在；下块 `agent-detail-section-mcp-file` 与 `agent-mcp-file-text` 都在且值仍是原文；条目视图已更新为已确认态 |
| 「mcp 页签自己有未提交输入时确认全局 MCP：只换条目视图，下块文本域与上块都不换」 | **F3**（同一分支的节点身份断言） | `byOdId(after,'agent-mcp-file-text') === textareaBefore`；`textareaBefore.value === typed`；`byOdId(after,'mcp-list') === listBefore`；条目视图已确认 |
| 「mcp.json 文本域有未提交输入时，后台重绘不换掉文本域节点，人打的字与节点都留着」 | **F6** | 文本域节点身份不变 + 值仍是人打的 |

**同时改写的既有断言（判据 = 与新行为正相反、且不许删原意）**：

| 既有断言 | 原说法 | 改成 | 为何必须改 |
| --- | --- | --- | --- |
| 「mcp 页签：库行块在上、家目录 mcp.json 块在下，两块各归其位」的序列断言 | 三个 od-id 的顺序 = `agent-capabilities-mcp` → `agent-detail-section-global-mcp` → `agent-detail-section-mcp-file`（即「条目视图夹在两个块之间」） | 两个 od-id 的顺序 = `agent-capabilities-mcp` → `agent-detail-section-mcp-file`（两块各归其位），条目视图的位置由**新增**的 F2 用例咬住（在下块子树里、在文本域之后） | 原文把条目视图断成「两个块之间的第三段」，正是 D1 明令取消的「单独成段」；改后**原意（上块库行、下块文件）完整保留**，条目视图的归属由新增用例更精确地覆盖 |
| 「全局 MCP 小节不再有「来自家目录 mcp.json」那句提示；空态用新文案」的定位方式 | `sectionOf(body, 'agent-detail-section-global-mcp')`（标题的父节点 = 区块） | `byOdId(body, 'agent-detail-section-global-mcp')`（标识现在挂在区块根上） | F3 的 od-id 归位使 `sectionOf` 不再适用；**取到的仍是同一个区块节点**，两句断言（无重复说明 / 空态逐字）一字未改 |

这两处按「D9 之外必须改的断言」登记在本文档（对应 §9「D9 之外必改」），处置口径与 #280 的 §4.2 相同。

**读数**：`node --test app/tests/*.test.cjs` → `tests 344 / pass 344 / fail 0`（base 是 340 条，新增 4 条）。

---

## 8. 文档同步（本轮除了 README 之外动到的验收文档）

只改与本次修复**直接相关**的句子，不重写：

| 文件 | 改动 | 理由 |
| --- | --- | --- |
| `app/specs/issue-280-acceptance.md` §3 归位表的 `agent-detail-section-global-mcp` 行 | 位置改成「`#adPanelMcp` → 下块**之内**」，并注明 od-id 从「区块首个标题」改挂到 `<section>` 自身、标题改 `…-title` | 原文写「并入下块」，但实测落点上块；本轮把位置与标识都改成真话 |
| 同文件 §6 偏差 3 | 补一句 review-fix 把它落进下块、od-id 改挂区块自身 | 同上 |
| `app/specs/issue-283-acceptance.md` §7.4 | 补「review-fix 已补 README 的三分页签口径与 `GET /api/skills/{id}/markdown` 端点行」 | 该条原文是「README 未改」的既存偏差，本轮已消解 |
| `app/specs/issue-284-acceptance.md` §1 AC1 的「上块行为不变」行 | `globalMcpSection()` 从「属于上块」改成「按 D1 在下块」 | F2 让那句落点描述失真 |
| 同文件 §7.3 | 事实修正：本票**并未**真的在模块 docstring 清单里补两行，那两行由 review-fix 补 | 原文是与文件不符的自述（F4 点名项） |

---

## 9. D9 之外必改（登记）

| # | 用例 | 为何 D9 没盖住 | 处置 |
| --- | --- | --- | --- |
| A | 「mcp 页签：库行块在上、家目录 mcp.json 块在下」的三 od-id 序列 | D9 #2 只登记了「拆成三条页签用例」，没预见到「条目视图该在下块里」这一层 | 改成两 od-id + 新增 F2 用例（见 §7 表） |
| B | 「全局 MCP 小节不再有…那句提示」用 `sectionOf` 定位 | D9 没有任何一条涉及 od-id 的挂载层级 | 改成 `byOdId`（取到同一节点，断言正文不变） |

两处都按「断言与新行为正相反 ⇒ 必须改、不许删原意」处置，没有一处是「删断言让测试变绿」。

---

## 10. 不改的 findings 与逐条理由（Standards 轴「可以不改但请写理由」）

1. **`agentSkillDetail` 那个字面量形状重复写了三次**——`state.agentSkillDetail` 的空形状在
   `app.js:201`（模块级初始值）、`:5127`（切成员时清空）、`:7633`（右栏没有选中项时清空）各写了一次
   `{ skillId: null, loading: false, error: '', state: '', markdown: null }`。
   **不改的理由**：它只有 5 个字段、三处都在同一套右栏逻辑里，抽成
   `emptyAgentSkillDetail()` 与既有的 `emptyAgentEditDraft()` / `emptyAgentDeleteState()` /
   `emptyMcpDraft()` / `emptyCapabilitiesView()` 同一形态**本来可以**做，但那是一次纯形状重构：
   本票图只为一处字面量新增一次间接层，会把 diff 扩大到与本轮 findings 无关的代码，且没有行为收益
   （三处当前一致，且 #283 的右栏用例与 #280 的「切换成员」用例会同时发现漂移）。
   留作后续纯重构票的候选，**不在 review-fix 里做**。
2. **`mcp.py` 三处重复解析**（`global_mcp_servers()` / `mcp_document_entry_count()` /
   `validate_mcp_document()` 各自 `json.loads` + 取 `servers`）。
   **不改的理由**：三处的**输入契约不同**——①读路径要按「首条同名生效」容忍坏条目并逐条降级；
   ②计数只数原文条目、**不做任何合规过滤**（这是 #284 AC2 明写的正文口径）；③校验要把第一处错误
   变成 `InvalidMCPDocument(location, key)` 的字段级定位并**整体拒绝**。抽一个共用内核要么给它
   加三个模式开关（比三段直白的实现更难读），要么把三处收紧成同一口径（会静默改掉 ② 的计数语义或
   ① 的降级语义）。这三段是「同一份数据的三种不同问题」，不是重复形状；`mcp.py` 的既有注释也写明
   了每条路径的容错等级。**不改**，理由记在这里。
3. **「400 承载 IO 失败」**（`PUT /api/agents/{id}/global-mcp-file` 在文件写入失败 / 家目录不存在 /
   路径不安全时回 `400`，而不是 `500` / `409`）。
   **不改的理由**：这三格都是**调用方可修正的请求问题**（家目录没建、路径被 symlink 指到根外、
   目标不可写），文案逐条可读（`error.global_mcp_home_missing` / `_path_invalid` / `_write_failed`），
   前端把它们落在**下块自己的错误行**、文本域保留人的输入。回 `500` 会让「人按提示建目录再存一次」
   与「服务端崩溃」长得一样；回 `409` 会与「同名同 scope 冲突」的既有语义撞车。这是 #284 票面
   九条 AC 之外的两格，已在 `issue-284-acceptance.md` §7.2 登记为「本票新增 2 条读结果口径」，
   review-fix **沿用**该取舍、不改契约（README 端点表已把「任一条不合法 ⇒ 400 + `fields` + 零写入」
   与原文字节口径写清，`review-rules.md` 判断题 6 因此成立）。
4. **`/tmp` 探针不入仓**（#284 过程里用过 `/tmp/verbatim_probe.py` 做逐字节写盘实测）。
   **不改的理由**：那些读数是**取证**，不是回归——它们要断言的同一个事实已经落在
   `app/tests/test_global_mcp_file.py` 的既有 pytest 用例里（`test_a_valid_document_is_written_verbatim`
   的两个参数化样本、`test_a_missing_member_home_is_a_400_and_creates_no_directory` 等），
   探针本身是一次性的 `TestClient` 脚手架（临时 HOME + 直连 app），没有独立判据、也不该成为第二套
   测试入口（`review-rules.md` 判断题 2「不新开接缝」）。把探针搬进仓等于给同一判据加第三条路径。

---

## 11. 门禁读数

### 11.1 Studio DOM 用例（全量 glob）

```sh
node --test app/tests/*.test.cjs
# → ℹ tests 344 / ℹ pass 344 / ℹ fail 0
```

（base `25da05b`：340 / pass 340；本轮 +4 条。）

### 11.2 后端相关 pytest（本票触到的端点族）

```sh
cd app && ../.venv/bin/python -m pytest tests/test_skills.py tests/test_skill_copy.py \
  tests/test_skill_markdown.py tests/test_global_mcp.py tests/test_global_mcp_consent.py \
  tests/test_global_mcp_file.py tests/test_mcp_assembly.py
# → 63 passed, 1 skipped
```

### 11.3 全量 `scripts/check.sh`

后台跑（`nohup`），跑完读 `/tmp/check_reviewfix.log`：

```text
== pytest（全量）      1232 passed, 11 skipped in 689.04s (0:11:29)
== ruff check          All checks passed!
== ruff format --check 351 files already formatted
== mypy（仓库根）      Success: no issues found in 130 source files
== pyright             0 errors, 0 warnings, 0 informations
== node --test（glob）  # tests 344 / pass 344 / fail 0 / skipped 0
门禁全绿
EXIT=0
```

### 11.4 未改动的面（一次核对）

```sh
git diff 25da05b --stat          # 见 §12 的改动面清单
git diff 25da05b -- app/tests/test_skill_copy.py app/alibot/db.py app/alibot/studio/index.html \
  docs/adr CONTEXT.md .github scripts            # → 空
```

---

## 12. 改动面清单

| 文件 | 性质 |
| --- | --- |
| `app/alibot/studio/app.js` | F2（条目视图落进下块 / od-id 挂区块根）、F3（`findNodeByOdId` + `replaceSectionNode`）、F5（两处正题注释 + 6 处顺带注释）、F6（`mcpFileBase` + 脏判据） |
| `app/alibot/studio/i18n.js` | F5 顺带：两份 catalog 的全局 MCP 段注释改成「条目视图只读 + 改原文走文本域」（**键与文案零改动**） |
| `app/alibot/api.py` | F4：模块 docstring 端点清单 + 两行 |
| `app/README.md` | F1：10 处（三分页签 / 撤除口径 / 六处 `mcp.json` 表述 / 三条端点 / 全局 MCP 小节新位置） |
| `app/tests/agent_detail_ui.test.cjs` | F7：+4 条用例、改 2 条既有断言（§7 / §9） |
| `app/specs/issue-280-acceptance.md` | 文档同步（2 句） |
| `app/specs/issue-283-acceptance.md` | 文档同步（1 句） |
| `app/specs/issue-284-acceptance.md` | 文档同步（2 处：AC1 落点 + §7.3 事实修正） |
| `app/specs/agent-detail-tabs-review-fix-acceptance.md` | 本文件（新） |
