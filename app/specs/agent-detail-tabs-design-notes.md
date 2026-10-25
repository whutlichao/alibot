# Agent 详情页三分页签 —— 设计笔记（决策账 + 规格输入）

- 本票类型：**设计票**，门禁 **G-docs**（其后按 to-spec 落规格、按 to-tickets 落票图，三段同属本票交付面）。
  **零源码改动**：不改 `app/alibot/**`、不改 `app/tests/**`、不 bump `SCHEMA_VERSION`。
- 本票**不跑**：全量 `pytest`、`ruff`、`mypy`、`pyright`、双轴 code-review、`scripts/check.sh` 全量与
  `--studio`。唯一可能触发的机械守卫是「裸中文扫描 / 词表守卫」——本票不碰
  `app/alibot/studio/**` 与 `app/README.md`，因此不触发；读数见 Answer。
- 前置 skill：`grill-with-docs`（已按要求依次加载 `grilling` 与 `domain-modeling`），下游 `to-spec`、
  `to-tickets` 已完整读并按序执行。提问通道：`orca orchestration ask`（禁 `AskUserQuestion`）。
- 基线：`main` = `adc079b`（= 派活时的 `origin/main`，fetch 后未前进）。本文所有 `file:line` 读数基于该基线；
  实施票开工时须重新 `grep` 复核。
- 上游正本（均已合入 `main`，本文不推翻其中未与 D1–D9 冲突的部分）：
  - `app/specs/agent-detail-fullpage-acceptance.md`（详情页整页化：主线视图 `state.mode === 'agent'`、
    六→四区块的历次用户反馈与读数）
  - `app/specs/agent-detail-sections-trim-acceptance.md`（上一轮裁撤区块：四处区块 + 16 个孤儿词条）
  - `docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md`（家目录 / `mcp.json` 口径）
  - `docs/adr/0036-skill-install-has-two-sources.md`（技能安装的三个字节来源与一条写路径）
  - `app/specs/agent-home-layers-design-notes.md` / `agent-home-layers-spec.md`（全局层与守卫）
  - `CONTEXT.md` 的 **Agent 家目录** / **全局层 / 项目层** / **MCP 声明** / **Skill** / **Agent 配置** 词条

---

## Problem Statement

用户在 Studio 的 Agent 详情页上遇到的是「一页越堆越长」的堆叠问题。今天这一页把五件事纵向排在同一列
（`agentDetailNodes()`，`studio/app.js:5317`）：**统计 → 系统提示 → 配置（含 MCP 表单与技能列表）→
全局 MCP 只读小节 → 危险操作**。具体到用户的五个痛点：

1. **改一个字段要滚过一整页。** 配置区有 9 个可编辑控件（保存时整份提交 10 个键，多出来的那个是危险区的 `enabled`），其中三项（上下文条数 / 压缩阈值 / 迭代上限）
   用户从未因为「想调这个数字」而打开这一页；它们把真正常改的字段挤到需要滚动才能看到。
2. **技能区与 MCP 区各自挤成一团。** 技能段今天同时挂着：列表 + 手填绝对路径安装 + 上传 zip 安装 +
   勾选式导入 + 每行「复制到其他成员」弹层。用户要「看这个技能到底是什么」时，页面上**没有正文入口**——
   只有名字、描述与前 12 位 hash。
3. **`mcp.json` 只能看不能改。** 详情页的「全局 MCP」小节是只读行（`globalMcpSection()`，
   `studio/app.js:5607`），而后端只有 `GET /api/agents/{id}/global-mcp-servers`
   （`api.py:1812` → `service.global_mcp_servers()`，`service.py:955`）。想改一条声明，人得离开浏览器去
   编辑家目录里的 JSON 文件；改错了没有任何前置校验。
4. **两块 MCP 面互不说话。** 库行（`mcp_server`，`scope_kind='agent'`，装配正本）与家目录 `mcp.json`
   （只读导入源、过渡期仍是只读装配层）在同一页上是两个不相邻的段，谁也不标注「我是哪一份数据」，
   用户看不出同名时谁胜出。
5. **危险操作与配置混在一起。** 暂停 / 恢复按钮住在「危险操作」区块，删除也在那里；它与配置字段在
   同一列里滚动，误触面与可读面都没被隔开。

这五件事不是五个需求，而是**同一个缺口**：这一页没有**分组**。用户的心智是三类——「这个 Agent 是谁、
怎么配的」「它会哪些技能」「它连哪些 MCP」——而界面把它们摊成了一列。

## Solution

把 Agent 详情页分成**三个页签**，每一类归一类：

- **「基础信息和配置」**：统计 → 系统提示 → 配置 → 危险操作。三项上下文数字（上下文条数 / 压缩阈值 /
  迭代上限）的**界面入口整体撤掉**——库内既有值与后端默认行为一字不动，只是不再在界面上让人改。
- **「skills」**：**双栏**。左栏是当前 Agent 的技能列表 + 一个**下拉框**式的「从本机导入」；右栏是选中
  技能的详情——**元数据（名称 / 描述 / 来源路径 / 内容 hash / 启停态）+ SKILL.md 正文**，下面挂
  「启用 / 停用」与「删除」。安装方式只留「从本机导入」一条；手填绝对路径与上传 zip 两条入口撤掉。
- **「mcp」**：**两块**。上块是表单（增删改**库行** `mcp_server`，`scope_kind='agent'`，装配正本）；
  下块是**直接编辑家目录 `mcp.json`** 的文本域（保存前校验 JSON 与声明形状）。两块各自标注数据来源，
  并写明「同名时库行胜出」。原「全局 MCP」小节并入这一页签，不再单独成段。

两处新的读写面（技能正文只读、`mcp.json` 读写）都落在既有资源族里，**零新增服务模块**；`mcp.json`
获得 Studio 写面与 ADR-0031 §四/§六 的既有口径相向，**同票收口**：改既有 ADR（判为修订，不是新决策），
并在 #275 留一条方向调整评论。#275 的剩余范围（导入命令 `migrate-mcp`）不变。

`POST /api/skills/{skill_id}/copy` 与 `POST /api/skills` 的 zip 分支**后端保留**：本票只撤界面入口，
端点在 docstring 里注明「当前 Studio 无入口，保留为脚本 / API 用法」。

## User Stories

**A. 页签分组**

1. 作为本机用户，我想让 Agent 详情页分成「基础信息和配置」「skills」「mcp」三个页签，这样我能按「这是谁／它会什么／它连什么」三类直接跳到位。
2. 作为本机用户，我想让打开详情时默认停在「基础信息和配置」页签，这样我不会每次都被带到一个空页签。
3. 作为本机用户，我想让切到别的成员时页签回到默认那一页，这样新成员不会继承上一个成员的页签位置。
4. 作为本机用户，我想让三个页签在顺序上固定（基础信息和配置 → skills → mcp），这样位置记忆不会每次变。
5. 作为本机用户，我想让页签用键盘也能抵达与切换（`role="tablist"` / `role="tab"` / `aria-selected`），这样我不用鼠标也能换页。
6. 作为本机用户，我想让页签只切呈现、不丢我正在编辑的草稿，这样我切去 skills 看一眼再回来，输了一半的系统提示还在。
7. 作为本机用户，我想让「有未保存的改动」的保存条跨页签可见，这样我在 skills 页签里也知道配置还有没保存的改动。
8. 作为审查者，我想让页签容器与既有 dock 的页签容器（`.dock-tabs`）不是同一个类，这样「详情是主线视图、不是 dock 形态」这条既有结论不被字面混淆。

**B. 基础信息和配置页签**

9. 作为本机用户，我想让统计、系统提示、配置、危险操作按这个顺序排在第一页签里，这样我最常改的系统提示仍紧贴统计之下。
10. 作为本机用户，我想让危险操作仍是这一页签的**最后一块**，这样误触面离配置字段最远。
11. 作为本机用户，我想让上下文条数、压缩阈值、迭代上限三格从界面上消失，这样配置区只剩我会改的字段。
12. 作为本机用户，我想让撤掉那三格之后保存**不会把它们清成空值**，这样我库里原来的 40 / 0.7 / 30 不会被一次保存抹掉。
13. 作为本机用户，我想让后端 `PATCH /api/agents/{id}` 的字段面与库里的三列都原样保留，这样我自己写脚本调用时行为不变。
14. 作为审查者，我想让「界面撤掉」与「后端字段保留」的边界在文档里写死，这样下一位读者不会顺手把列或 PATCH 字段一起删了。

**C. skills 页签**

15. 作为本机用户，我想让 skills 页签是双栏：左栏列表、右栏详情，这样我点一个技能就能在右边看它是什么，列表不会被详情挤走。
16. 作为本机用户，我想让右栏显示名称、描述、来源路径、内容 hash 与启停态，这样我在界面上就能核对「装的是哪一份」。
17. 作为本机用户，我想让右栏显示 SKILL.md 的**正文**，这样我不用去翻家目录就知道这个技能的指令到底写了什么。
18. 作为本机用户，我想让正文来自一个只读端点而不是把整棵树塞进列表响应，这样列表仍然轻、正文按需读。
19. 作为本机用户，我想让这个只读端点拒绝根外 symlink 与根外目标，这样它不会变成读任意文件的通道。
20. 作为本机用户，我想在技能未安装或正文文件缺失时看到明确空态（元数据照常显示），这样「读不到」不会被误读成「这个技能是空的」。
21. 作为本机用户，我想让安装方式只剩「从本机导入」一条，这样我不必再在三个入口之间判断该用哪个。
22. 作为本机用户，我想让「从本机导入」是一个下拉框，点开后直接选一个技能，这样我不必先看一遍候选列表再勾选。
23. 作为本机用户，我想让下拉框里已安装的与不可导入的候选是禁用的并带原因，这样我不会选中一个注定 409 或注定失败的条目。
24. 作为本机用户，我想让下拉框每次导入**一个**技能，这样成功 / 已存在 / 失败的原因能精确归到那一条上。
25. 作为本机用户，我想让导入成功后候选重扫、技能列表重读，这样刚导入的那条立刻从「可选」变成「已安装」。
26. 作为本机用户，我想让导入 409 时提示「已安装，已跳过」而不是静默失败，这样我不必猜为什么没变化。
27. 作为本机用户，我想让候选来源目录不存在时给一句空态指引，而不是报错，这样第一次用的人知道该去做什么。
28. 作为本机用户，我想保留右栏的「启用 / 停用」与「删除」，这样我在看详情时就能直接停掉或删掉它。
29. 作为本机用户，我想让「复制到其他成员」的弹层与入口消失，这样技能行上不再有一个我很少用的动作。
30. 作为本机用户，我想让技能与 MCP 行的复制入口一起消失（它们共用同一个弹层），这样不会出现「一半还在一半没了」的界面。
31. 作为审查者，我想让 `POST /api/skills/{id}/copy` 端点与它的契约测试原样保留，这样这次收缩不是不可逆的删除，脚本用法仍然有效。

**D. mcp 页签**

32. 作为本机用户，我想让 mcp 页签上半块是库行表单，这样增删改 `mcp_server`（`scope_kind='agent'`）仍在这一页完成。
33. 作为本机用户，我想让上半块标注「这是库行、是装配正本」，这样我知道改这里会真的改变装配结果。
34. 作为本机用户，我想让下半块是一个直接编辑家目录 `mcp.json` 的文本域，这样我不必离开浏览器去改文件。
35. 作为本机用户，我想让下半块标注「这是家目录文件、同名时库行胜出」，这样我不会以为改文件能覆盖同名库行。
36. 作为本机用户，我想让保存前校验 JSON 与每条声明的形状（transport / command / args / url / env 变量名），这样我不会把运行时才发现的问题写进文件。
37. 作为本机用户，我想让校验失败时**整体不写盘**并给出可读原因，这样文件不会停在半坏状态。
38. 作为本机用户，我想让保存保留我自己写的键序与缩进，这样这个文本域不是一次隐式格式化。
39. 作为本机用户，我想让文件不存在时文本域是空的、且保存能新建它（家目录已存在时），这样第一次配置不必先开个终端。
40. 作为本机用户，我想让家目录本身不存在时保存给可读错误而不是替我造目录，这样「应用不自动创建全局层」这条纪律仍成立。
41. 作为本机用户，我想让远端（`http` / `sse`）声明的数据面确认仍是既有那一条动作，这样文本域不会绕过确认纪律。

**E. 不被动的东西**

42. 作为用户，我想让后端装配、技能与 MCP 的优先级、消息面、预算与唤醒纪律一字不动，这样这是纯呈现层重排。
43. 作为用户，我想让 `SCHEMA_VERSION`、迁移链与所有 `.py` 文件在本票内零改动，这样设计票不会夹带实施。
44. 作为审查者，我想让既有测试断言的改写逐条列在规格里（改哪条、改成什么），这样实施票不会静默绕过。

## Implementation Decisions

### D1 · 页签结构与归属（用户已定）

三个页签，顺序固定：**基础信息和配置** → **skills** → **mcp**。

| 页签 | 装什么 | 顺序 |
| --- | --- | --- |
| 基础信息和配置 | 统计 / 系统提示 / 配置（剩余字段）/ 危险操作 | 统计 → 系统提示 → 配置 → 危险操作（危险操作仍是最后一块） |
| skills | 顶部「从本机导入」下拉框；**双栏**：左栏技能列表、右栏选中技能详情 | 导入 → 左栏 / 右栏 |
| mcp | **上块**库行表单（列表 + 表单 + 启停 + 删除 + 远端确认）；**下块** `mcp.json` 文本域 | 库行块 → 文件块 |

「全局 MCP」小节**并入** mcp 页签的下块内容，不再单独成段（`globalMcpSection()` 的只读行语义被下块取代）。

**页签状态**：`state.agentDetailTab ∈ {'base','skills','mcp'}`，默认 `'base'`；`openAgentDetail()` 与
切换成员时重置为 `'base'`；它只是呈现状态，**不落库、不写 URL**。页签只切呈现：切页签**不**丢弃
`state.agentEdit.draft`，保存条（`#adSaveBar`）仍是全页级、跨页签可见。

**标识**：`data-adtab="base|skills|mcp"`；节点 `#adTabBase` / `#adTabSkills` / `#adTabMcp`；
容器类名 `.ad-tabs`（**不复用** `.dock-tabs`——那是 dock 的形态容器，`agent_detail_ui.test.cjs:700`
断言它在正本里不存在，那条断言说明的是「详情不是 dock 形态」，与本页签无关）。
词条键：`agentDetail.tabsLabel` / `agentDetail.tabBase` / `agentDetail.tabSkills` / `agentDetail.tabMcp`。

**a11y**：容器 `role="tablist"` + `aria-label`（走 `agentDetail.tabsLabel`）；每个按钮
`role="tab"`、`aria-selected`、`aria-controls` 指向对应面板；面板 `role="tabpanel"`。键盘用浏览器原生
`<button>` 的 Tab / Enter 可达，**不**自造方向键漫游（与既有「按钮不需要额外 tabindex」的用例同口径，
`agent_detail_ui.test.cjs:1882`）。

### D2 · 上下文三项撤除的边界（用户已定）

| 面 | 处置 |
| --- | --- |
| 界面控件 | **撤掉**：配置区三个 number 控件（`agentInput('context_window_messages' \| 'context_trigger_ratio' \| 'react_max_iters')`，`studio/app.js:5474-5488`）与只服务它们的 `agent.runtimeDefaultHint` 提示 |
| 前端草稿 | **撤掉**：`agentDraftFrom()` 不再产出这三项（`studio/app.js:4992-4996`），因此保存条不会因它们变脏 |
| 前端提交面 | **撤掉**：`submitAgentEdit()` 的载荷从 **10 个字段降到 7 个**（`display_name` / `role` / `model_id` / `system_prompt` / `enabled` / `auto_claim` / `web_access`），不再提交 `context_window_messages` / `context_trigger_ratio` / `react_max_iters` |
| 库形态 | **不动**：`agent_config` 的三列与 DDL 默认值（40 / 0.7 / 30）与 CHECK 约束原样（`db.py:694-696`） |
| PATCH 契约 | **不动**：`AgentUpdate` 的三个可选字段、`api.py:1746` 的字段清单与 `update_agent` 的三态写入语义原样（`api.py:314-316`、`:1776-1790`） |
| 运行时读取 | **不动**：`runtime.py:45-81` 的 `context_window_messages()` 与 `ReActConfig(max_iters=...)` 兜底原样 |
| 迁移 / 回填 | **不做**：不删列、不改默认值、不回填 |

**为什么这样切**：`PATCH /api/agents/{id}` 是**部分更新**——只有出现在请求体里的字段才写入。前端不再提交这三项
⇒ 库内既有值**保持原值**，这是「撤界面不破坏既有值」的机械保证；反过来若前端继续提交 `null`，
一次保存就会把三列清空。这条边界是本设计里最容易做错的一格，必须写进实施票的验收项。

**不做什么**（本票明确不选的替代方案）：给三项加一个「高级」折叠区（那是同一个入口的换皮，撤销不彻底）；
把三项挪到 Agent 创建弹窗（那会让新建面从 4 个键膨胀到 7 个，且创建之后仍无处可改）。

### D3 · skills 只从本机导入（用户已定）

skills 页签的**唯一安装入口**是「从本机导入」。撤掉的入口（两条，均在 `skillInstallForm()`，
`studio/app.js:7727`）：

| 撤掉的入口 | 现状落点 | 处置 |
| --- | --- | --- |
| 「从本机目录安装（手填绝对路径）」文本框 + 按钮 | `skillInstallForm()` 的 `dirRow`（`data-od-id="skill-source"` / `"skill-install-dir"`）、`submitSkillDirectory()` | 界面撤掉，`skillSourcePath` 呈现状态一并删 |
| 「上传 zip 安装」文件框 | 同函数 `zipRow`（`data-od-id="skill-zip"`）、`uploadSkillZip()` | 同上 |

**后端保留**：`POST /api/skills` 的 directory 与 zip 两条分支**不删**（它们是有契约测试的既有写路径，
删除属于不可逆收缩）。实施票在该端点 docstring 加一行「当前 Studio 无入口，保留为脚本 / API 用法」，
与 D6 对 `/copy` 的处置同一口径。

### D4 · 导入入口 = 下拉框，语义 = 一次一个（代答，票面授权收敛）

**入口形态（用户已定，不得改）**：点击「从本机导入」后**展开一个下拉框**——不是弹窗、不是多选勾选列表。

**语义（本设计收敛的结论）**：**单次选择一个候选、导入一个技能**。三条理由：

1. 候选端点 `/api/skills/candidates` 是**逐条降级**的：每条带 `importable` / `reason` / `installed`
   （`skills.py:268-345`）。单选让 409（已存在）与各类失败原因精确归到那一条上；多选则必须引入一份
   「部分成功」结果面板，而那正是 D4 否掉的形态。
2. 写路径逐字复用 `POST /api/skills`（`source_path` = 候选的 `path`，ADR-0036 §一），一条请求一条技能，
   与 `submitSkillImport()` 今天的「逐条 import」在语义上降级为「一次一条」，不新增批量契约。
3. 用户的真实动作是「把刚才用 `npx skills` 装好的那个技能导进来」——一次一个与这个动作同形。

**下拉框内容**：按候选状态分三组——可选（`importable && !installed`）、已安装（`installed`，禁用）、
不可导入（`!importable`，禁用且把 `reason` 作为选项文本后缀）。**不预选**任何项（空选项文案
`skill.importPickPlaceholder`）。`available=false`（来源目录不存在）时不渲染下拉框，只给一句空态指引
（沿用既有 `skill.importSourceMissing` 一类文案的语义，实施票可选沿用或新增键）。

**刷新**：导入结束（成功 / 409 / 失败）后重扫候选 + 重读技能列表；下拉框回到空选择。
候选来源缺席是正常状态（ADR-0036：应用不创建那个目录）。

### D5 · skills 右栏详情与新的只读正文端点（用户已定 D5；落点由 Q1 人定）

**右栏内容（自上而下）**：元数据（名称 / 描述 / 来源路径 / 内容 hash / 启停态）→ 动作（启用 / 停用、删除）
→ **SKILL.md 正文**。

**端点**：`GET /api/skills/{skill_id}/markdown`（Q1 人定：落在 `/api/skills/{id}` 同族子资源）。

| 项 | 契约 |
| --- | --- |
| 成功 | `200`，`{"skill": {…既有 `_skill_payload` 字段…}, "markdown": "<SKILL.md 正文>", "state": "ok"}` |
| 未安装 / 文件缺失 | `200`，`markdown: null`，`state: "missing"`（元数据照常返回——行在、字节没了是真实状态） |
| 路径不安全（根外 symlink 祖先 / 根外目标） | `200`，`markdown: null`，`state: "invalid"`（**不**回任何根外内容，只记日志） |
| 读不出来（权限 / 非 UTF-8） | `200`，`markdown: null`，`state: "unreadable"` |
| `skill_id` 不存在 | `404`，复用既有 `error.skill_not_found`（与 `get_skill()` 同一条） |
| 行是 `scope_kind='channel'`（弃用旧行） | `400`，复用既有 `error.skill_scope_agent_only`（旧行没有家目录字节，也没有「当前 Agent 的详情」这一语义） |

**守卫**：正文读取经 `workspace.resolve_global_read_path`（`workspace.py:325`）同族的 `realpath` 守卫，
参数是 `(全局根, AGENTS_DIR, safe_partition_id(skill.scope_id), SKILLS_DIR, skill.name, SKILL.md)`——
与技能装配（`skills.py:654-726` 的 `_global_skill_layer`）读同一份字节、同一道守卫。守卫拒绝 ⇒
`state=invalid`，**不**退化成直读路径。

**正文渲染**：首版按**纯文本**呈现（`white-space: pre-wrap` + 等宽族），**不**把 Markdown 解析成 HTML。
理由：本仓前端当前没有 Markdown 渲染面，引入它等于新增一个转义 / XSS 面，而用户要的是「读得到正文」，
不是「读得漂亮」。

**选中态**：`state.agentDetailSkillId`；进入页签时默认选中左栏第一行；列表重读后若选中项已不存在，
回落到第一行，列表为空则右栏给空态（`skill.detailEmpty`）。

### D6 · skills 动作范围与「复制到其他成员」的撤除（用户已定 D6；端点处置由 Q2 人定）

保留：右栏「启用 / 停用」（`PATCH /api/skills/{id}`）与「删除」（`DELETE /api/skills/{id}`）。

撤除：**「复制到其他成员」弹层与其入口**。注意它是 MCP / 技能**两 kind 共用**的一份实现
（`capabilityCopyNode()`，`studio/app.js:8092`；入口 `agentCapabilities.copyTo`），因此撤弹层的连带后果是
**技能行与 MCP 行的复制入口一起消失**——这是同一份呈现状态的收缩，不是两件事。
`state.copy` / `openCapabilityCopy()` / `closeCapabilityCopy()` / `toggleCapabilityCopyTarget()` /
`submitCapabilityCopy()` / `copyResultText()` / `copyResultText` 依赖的 `state.rosterCapabilities` 清理逻辑
一并删除（不留死代码，评审判据 6）。

**端点处置（Q2 = A，人定）**：`POST /api/skills/{skill_id}/copy`（`api.py:1266`）**后端保留**，
`app/tests/test_skill_copy.py` 原样保留、原样绿。实施票在 `api.py` 的模块 docstring 或该端点 docstring
加一行注明「当前 Studio 无入口，保留为脚本 / API 用法」。**不采纳**「同票删端点」（不可逆收缩 + 与本票
Ownership 冲突）与「标注弃用」（把「暂留」写成「已弃用」，将来真删还要再改一次文档）。
MCP 行的复制走 `POST /api/mcp-servers`（无专用端点），故无对应保留说明。

### D7 · mcp 页签 = 库行表单 + 直接编辑 `mcp.json`（用户已定）

**上块（库行表单）**：沿用既有能力段的 MCP 部分（`agentCapabilitiesSection()` 的 MCP 分支，
`studio/app.js:7613`）——列表 + 表单（`mcpForm()`）+ 启停 + 删除 + 远端数据面确认
（`POST /api/mcp-servers/{id}/consent`）。数据来源标注：**库行 `mcp_server`（`scope_kind='agent'`），
装配正本**（`CONTEXT.md` **MCP 声明**词条）。

**下块（`mcp.json` 文本域）**：新增两条端点，落在 `GET /api/agents/{id}/global-mcp-servers` 同族
（Q1 人定：扩展 `service.py` 的 `global_mcp_servers()` 同族）。

| 端点 | 契约 |
| --- | --- |
| `GET /api/agents/{member_id}/global-mcp-file` | `{"member_id", "exists": bool, "text": "<原文；不存在时为空串>", "servers": <条目计数>}`；成员不存在 ⇒ 404（与 `service.global_mcp_servers()` 同一条 `get_agent` 校验） |
| `PUT /api/agents/{member_id}/global-mcp-file` | body `{"text": "<整份 JSON 原文>"}`；校验通过则**原样写盘**并回 `{"member_id", "exists": true, "text", "servers"}`；校验失败 ⇒ `400` + 可读原因 + 字段级错误，**零写入** |

**保存前校验（三道，全部在写盘之前）**：① JSON 可解析；② 顶层是对象且 `servers` 是数组；
③ 每条经 `mcp.normalize_mcp_declaration()`（`mcp.py:76`）——与库写路径、全局文件读路径**同一份内核**，
因此 transport / command / args / url / `env_names` 形状与装配层逐字同源。任一条不合法 ⇒ 整体 400。

**写盘**：先写同目录临时文件再 `os.replace`（既有先例 `runtime.py:199`），**保留用户自己的键序与缩进**
（不重排、不 `json.dumps` 回写、不格式化——文本域不是格式化器）。文件不存在且成员家目录存在 ⇒ 新建；
家目录本身不存在 ⇒ `400`（应用不自动创建全局层，ADR-0031 §三）。文件存在 ⇒ 覆盖（人类显式动作）。

**守卫**：读经 `resolve_global_read_path`；写前用同一内核取目标 `realpath` 并拒绝根外 symlink 祖先与
根外目标（`workspace.py:310-335` 的 `_resolve_read_path` 是同一份判据，写路径复用其判定而不新写一份）。

**优先级标注（界面文案，不改变装配）**：两块各自一行小字——上块「来自库行 `mcp_server`（`scope_kind='agent'`），
装配正本」；下块「来自成员家目录 `mcp.json`；**同名时库行胜出**，落选条目会进轮次结果的可读提示」。
这与 ADR-0031 §二现行口径一致（`mcp.json` 过渡期仍是只读装配层，同名低于 Agent 级库行，落选条目进
`RoundOutcome.reason` 的 `layer=global` 提示）。远端条目的数据面确认**仍走既有那条动作**，文本域不绕过它。

**与 #275 的关系**：本票只给 `mcp.json` 加**写面**，不改装配层、不删「过渡期只读装配层」这条降级路径。
#275 的剩余范围（`migrate-mcp` 导入命令把既有条目一次性搬进库行、移除装配层）不变。

### D8 · 与 ADR-0031 的冲突在同一票内收口（用户已定；判为修订而非新决策）

**判定：这属于对既有 ADR 的**修订**，不新建 ADR。** 按 `domain-modeling` 的三条件逐条核：

| 条件 | 判定 | 依据 |
| --- | --- | --- |
| 难以回头 | 部分成立 | 写面一旦存在，人会往里存声明；但**正本仍是库行**，`mcp.json` 的定位（导入源 + 过渡期只读装配层）不变，因此「改回来」只需撤一条写端点 |
| 没有上下文会困惑 | **不成立** | 新口径与既有「库行是正本、同名库行胜出」完全一致；它只把「本轮不提供导出写路径」改成「写面由 Studio 提供」，#275 当年就是这条的收尾票 |
| 是真权衡的结果 | **已记过** | 「Studio 写 vs 只读」这条权衡在 ADR-0031 §四/§六 的 #270 修订里已登记并指向 #275；本次是那条的**方向调整**，不是新权衡 |

三条件未全满足 ⇒ **不新建 ADR-0037**（少即是过）。按 `docs/agents/review-rules.md` 判断规则 4
「ADR 只做最小事实修正」处理：改 ADR-0031 里所有断言「`mcp.json` 只读 / 本轮不提供导出写路径 / #275 收尾」
的句子，写成「写面由 Studio 提供、装配正本仍是库行、同名库行胜出」，并加一条带日期与 issue 号的修订标注。

**逐处改动清单**（可 `git diff` 指认）：

| 位置 | 现在的句子 | 改成 |
| --- | --- | --- |
| 文件头「修订（#270，2026-10-25）」段 | `mcp.json` 保持只读导入源……**本轮不提供导出写路径**，由 follow-up #275 收尾 | 追加「修订（本票 issue，日期）」子句：Studio 提供 `mcp.json` 写面，装配正本仍是库行 |
| §一 表格「全局成员家目录」行的 `mcp.json` 单元格 | `mcp.json` 只读（人工维护的导入源；…本轮不提供导出写路径，#275 收尾） | `mcp.json` 由 Studio 经人类面写（导入源 + Studio 写面）；装配正本仍是库行 |
| §二 「技能与 MCP」段 | 「只作只读导入源……**本轮不提供导出写路径**（#275 收尾）」 | 「Studio 提供写面（人类面直接编辑原文）；装配正本仍是 `mcp_server` 的 Agent 级行，同名库行胜出」 |
| §三 「独立守卫」bullet | 「MCP 无导出写路径（`mcp.json` 只读）」 | 「`mcp.json` 的写入发生在宿主进程的人类面（Studio），不经文件工具的 Write / Edit 路径；容器内仍只读」 |
| §三 「Docker 后端」bullet | 「`:ro` 保留的理由是技能写入发生在宿主进程（`mcp.json` 只读，应用不写它）」 | 「写入（含成员家目录 `mcp.json`）都发生在宿主进程，容器只需读取」 |
| §四 表格「ADR-0021 §三」行 | 「`mcp.json` 只读导入源……本轮不提供导出写路径（#275）」 | 「`mcp.json` 是导入源 + Studio 写面；装配正本仍是库行，同名库行胜出；#275 仍收尾导入命令与移除装配层」 |
| §四 表格「ADR-0021 §六」行 | 「`mcp.json` 只读（本轮无导出写路径，#275 收尾）」 | 「`mcp.json` 由 Studio 经人类面写；其余素材仍由人维护或由应用按其 ADR 写入」 |

§六「被否方案与代价」**不改**：它没有断言「只读」，只断言「全局 MCP 静默允许远端」被否（那条仍成立）。

**#275 评论（本票内产出）**：说明方向调整——`mcp.json` 的写面由 Studio 提供，因此 #275 里「导出」这一项
不必再等应用实现（人可以直接在 Studio 里改原文）；#275 剩余范围 = ① `migrate-mcp` 导入命令（把既有条目
一次性搬进库行）仍作为**迁移工具**保留；② 移除装配层（`mcp.json` 不再进运行时装配）仍有效；
③ ADR 去「过渡期」措辞的时机改为「② 落地之后」。**不关闭 #275、不改它的标签与正文。**

### D9 · 既有测试与文档断言的改写清单（用户已定；本票只登记，不改）

`app/tests/agent_detail_ui.test.cjs` 今天断言「页签已删除」，D1 恢复页签意味着这些断言必须**改写**，
不许静默绕过。逐条登记如下（行号基于 `adc079b`，实施票须重新 `grep` 复核）：

| # | 现有断言（落点） | 现在说什么 | 改成什么 |
| --- | --- | --- | --- |
| 1 | 文件头注释（`:11`） | 「页签节点（`#adTabOverview` / `#adTabRuntime` / `data-adtab`）在正本里不存在」 | 改成三分页签口径：`data-adtab="base\|skills\|mcp"` 存在，`#adTabOverview` / `#adTabRuntime` 仍不存在（不复用这两个旧名） |
| 2 | 「所有区块在同一页纵向铺开……」（`:606`） | 断言 `#adBody` 子树的六处 `data-od-id` 顺序 = 统计 / 系统提示 / 配置 / 能力段 / 全局 MCP / 危险操作，且「不需要切换」 | 拆成三条：① 基础信息和配置页签内的 `data-od-id` 顺序 = `facts → prompt → config → danger`；② skills 页签内 = 导入下拉 + 左栏列表 + 右栏详情；③ mcp 页签内 = 库行块 → 文件块。删除「同一页不需要切换」这句理由 |
| 3 | 「正本里不再有页签与编辑弹窗（index.html）」（`:695`） | `data-adtab=` **必须 false**；`#adTabOverview` / `#adTabRuntime` false；`class="dock-tabs"` false；`#dockDetail` false；`#agentEditOverlay` false | 反转页签那一半：`data-adtab=` 断言匹配三个值、`#adTabBase` / `#adTabSkills` / `#adTabMcp` 存在；**保留** `#adTabOverview` / `#adTabRuntime` 为 false、`class="dock-tabs"` false、`#dockDetail` false、`#agentEditOverlay` false（这四条是「详情不是 dock 形态、编辑弹窗已删」，与本票无关） |
| 4 | 「本票新增 / 沿用的文案在两份词条里都取到值」（`:1838`） | `agentDetail.tabsLabel` / `tabOverview` / `tabRuntime` 在两个 catalog 里都 `undefined` | `tabsLabel` 移进「必须有值」列表；新增 `tabBase` / `tabSkills` / `tabMcp` 三项必须有值；`tabOverview` / `tabRuntime` 仍保持 `undefined`（旧名不复用） |
| 5 | 同一条用例的「已删词条」清单（`:1793-1796`） | `agent.contextWindow` / `contextTrigger` / `maxIters` / `runtimeDefaultHint` **必须有值** | 四项移进「必须 `undefined`」（随控件撤除删词条，与上一轮 16 个孤儿键同一规则） |
| 6 | 「就地编辑只写草稿……」的载荷断言（`:757-770`） | `deepEqual(patch[0].body, {…10 个字段…})`，其中含三项 `null` | 改成 7 个字段（去掉 `context_window_messages` / `context_trigger_ratio` / `react_max_iters`），并**新增**一条断言：草稿里不存在这三个键 |
| 7 | 「配置区块的标签真的指向控件……」（`:1899`） | 单控件 `label[for]` 的字段清单是 `display_name` / `role` / `provider` / `model` / `context_window_messages` / `context_trigger_ratio` / `react_max_iters` 七项 | 清单**去掉后三项**（只剩 `display_name` / `role` / `provider` / `model`）；档位组 `auto_claim` 的 `aria-labelledby` 与系统提示 `textarea` 的 `aria-label` 两条断言原样 |

`app/tests/agent_capabilities_ui.test.cjs` 同样要改：

| # | 现有断言（落点） | 改成什么 |
| --- | --- | --- |
| 8 | 「能力段：两种能力同屏」（`:619`） | 拆成三条：skills 页签内的左栏列表 / mcp 页签内的库行表单 / 两页签各自只读自己那一份（MCP 读数在 mcp 页签、技能读数在 skills 页签） |
| 9 | 同一条里的复制入口断言（`:657-658`，`copyMcp` / `copySkill`） | **删除**（弹层与入口撤除，D6）；改为断言两个 `data-copy-*` 属性在正本里 0 命中 |
| 10 | 「技能本机目录安装在 agent scope 下提交」（`:699`） | **删除**（入口撤除，D3）；改为断言 `data-od-id="skill-source"` / `"skill-install-dir"` / `"skill-zip"` 三处 0 命中 |
| 11 | 「导入区块：读候选、默认勾选……」（`:720`） | 改成「导入下拉框：候选按 可选 / 已安装 / 不可导入 分组，不可选项禁用且带原因，默认不预选」 |
| 12 | 「导入选中：逐条 POST /api/skills……」（`:760`） | 改成「选中一项后发**一次** POST /api/skills（`source_path` = 候选 path）；409 记「已安装，已跳过」；失败记可读原因」 |
| 13 | 「复制入口列出其他成员……」（`:880`）与「目标已存在同名时逐条提示已跳过」（`:912`） | **删除**（功能与弹层撤除，D6） |
| 14 | 「新增文案在两份词条里同键同参数位」（`:987`） | 删掉随入口撤除的键（`skill.installDir` / `installDirButton` / `installZip` 等 → 必须 `undefined`），新增下拉框与右栏详情、mcp 文件块的键 |

**`app/tests/test_skill_copy.py` 原样保留**（Q2 人定 A）：它测的是后端端点而不是界面入口，删除它等于删掉
一条仍然有效的契约。

**文档同步（实施票负责，本票不改）**：`app/README.md:716-760` 的「Agent 详情、配置编辑与软删除」一节
（现文写「所有区块在同一页纵向铺开……没有页签」）要改成三分页签口径、补两条新端点、写明上下文三项撤除；
`studio/i18n.js` 两份 catalog 的段头注释里「没有页签」的说明同步改写；`studio/index.html` 与
`studio/app.js` 的相关注释同步（不留说谎的注释，评审判据 6）。

### D10 · Seam 选址（Q1 人定 = 双 A）

**核心结论：seam 总数不增，两处新端点都落在既有资源族里，零新增服务模块。**

| 新增能力 | 落点族 | 代价一句话 |
| --- | --- | --- |
| `mcp.json` 读写 | 扩展 `service.py` 的 `global_mcp_servers()` 同族：新增 `global_mcp_document(member_id)` / `save_global_mcp_document(member_id, text)`，路由挂在 `GET /api/agents/{id}/global-mcp-servers` 同族子资源 `…/global-mcp-file` | 成员存在性、`get_agent` 404 与守卫复用同一份，零新增服务模块；代价是「原文」与「只读条目视图」同族，需要 docstring 写清两者边界（Q1 要求） |
| `SKILL.md` 正文只读 | `GET /api/skills/{skill_id}/markdown`，落在 `/api/skills/{id}` 同族 | 复用 `database.get_skill` 的 404、`error.skill_scope_agent_only` 与 `resolve_global_read_path`；代价是 `/api/skills/{id}` 从「只有一个 PATCH / DELETE」变成有子资源，读者需要看清它仍是同一个资源 |

**被否方案**：新开独立模块（如 `mcp_home.py`）+ 新顶层路由 `/api/agents/{id}/mcp-file`——文件边界更干净，
但多一个服务面与一份成员校验 / 守卫转发层，seam 数 +1；`/api/skill-documents/{id}`——要把作用域校验、
404 文案与守卫再串一遍，前端还要多记一套资源族名。两者都违背「既有缝优先、缝越少越好」。

**测试面（两条，都是既有缝）**：① 前端呈现 seam（既有 `vm` + DOM 替身 + fetch 替身）；② HTTP 人类面 seam
（既有 `httpx` + `TestClient` 契约测试）。**装配 seam 不动**——`mcp.json` 写完之后装配读的是同一份文件，
既有 `test_global_mcp.py` 覆盖那条路径。

### D11 · 分票粒度（Q2 授权：to-tickets 原语言自选，理由记 Answer）

候选方案与选定结果见规格的 Implementation Decisions 与票图 issue；一句理由：
**按「页面骨架一次到位、三块内容各自独立穿全层」切 5 票**——骨架票不引任何新后端面（只重排既有内容 +
撤一项界面入口 + 改写既有断言），三块内容票各自只碰一个页签且互不依赖（skills 导入 / skills 详情 /
mcp 文件），因此骨架之后 frontier 一次放出三张，且每张都能装进一个新鲜 context。

## Testing Decisions

### T1 · 什么算好测试

只断言**外部可观察行为**：DOM 树里的 `data-od-id` / `data-adtab`、节点文本走词条取值、发出的 HTTP 请求
（URL + body）、后端返回的状态码与响应形状、写盘后的文件内容。**不**断言私有函数名、内部状态字段名、
SQL、目录遍历顺序。沿用本仓既有两条纪律：前端只用 `vm` + DOM 替身（不新开第二套框架），后端只用 HTTP
黑盒（`TestClient`，不 import 私有名）。

### T2 · 呈现层用例（既有缝）

- **落点**：`app/tests/agent_detail_ui.test.cjs`（页签骨架、内容归属、上下文三项撤除、保存载荷）、
  `app/tests/agent_capabilities_ui.test.cjs`（skills 双栏与下拉框、mcp 两块、复制弹层撤除）。
- **怎么观测**：`harness.agentBody` 子树的 `data-od-id` 顺序、`#adTab*` 节点的存在与 `aria-selected`、
  点击后发出的请求体、词条表取值。
- **先例**：`agent_detail_ui.test.cjs:606`（区块顺序）、`:757`（PATCH 载荷逐字段）、
  `agent_capabilities_ui.test.cjs:720`（候选分组与禁用）。

### T3 · HTTP 契约用例（既有缝）

- **落点**：新增 `app/tests/test_skill_markdown.py` 与 `app/tests/test_global_mcp_file.py`（或并入既有
  `test_global_mcp.py`；实施票自选，判据是不新开第二套装置）。
- **怎么观测**：真实临时 `HOME` 下的 `~/.alibot` 树，`TestClient` 打端点，断言状态码 / `state` 字段 /
  磁盘上的文件字节。
- **必测边界**：`skill_id` 不存在 404；`scope_kind='channel'` 400；SKILL.md 缺失 ⇒ `state=missing`；
  根外 symlink ⇒ `state=invalid` 且响应里不含根外内容；`PUT` 非法 JSON ⇒ 400 且文件 mtime 不变；
  `PUT` 形状非法的单条 ⇒ 400；`PUT` 合法 ⇒ 写盘内容与提交文本**逐字相等**（不重排）；
  家目录缺失 ⇒ 400 且不创建目录。
- **先例**：`app/tests/test_skill_copy.py`（守卫拒绝 + 404 文案）、`app/tests/test_global_mcp.py`
  （真实临时 `HOME` + 家目录 mcp.json）、`app/tests/test_skill_assembly.py`（装配读同一份字节）。

### T4 · 断言改写的真实性

改写不许「删掉就完事」：每条旧断言要么**换成**对新形状的等价断言（顺序变了两段、载荷少三项要显式断言
「不存在」），要么**删掉并说明它断言的节点已不存在**（例如 zip 安装入口、复制弹层）。判据：
`git diff` 里每一条删除/改写都能指回 D1–D9 的一条决策。

### T5 · 不测什么

不测 Markdown 渲染（首版是纯文本）；不测 `mcp.json` 的装配后果（`test_global_mcp.py` 已覆盖，写面不改
装配）；不测真实浏览器像素（本仓没有浏览器测试框架）；不测 `POST /api/skills/{id}/copy`（端点未改）。

## Out of Scope

1. **本票不写任何源码、测试、迁移脚本或 CI 配置。** 交付面是设计笔记 + 规格 + 票图 + ADR 修订 + #275 评论。
2. **不改后端装配、优先级、消息面、预算与唤醒纪律。** 这是纯呈现层重排 + 两条既有资源族内的读写端点。
3. **不删 `POST /api/skills/{skill_id}/copy`，不删 `POST /api/skills` 的 zip / directory 分支。**
   本票只撤界面入口（Q2 人定 A）；将来真要删端点另开实施票。
4. **不做 `mcp.json` 的导入命令与装配层移除。** 那仍是 #275 的范围；本票只加写面。
5. **不新增 schema、不 bump `SCHEMA_VERSION`、不写迁移步骤。** 两条新端点都不需要新列。
6. **不引入 Markdown 渲染器。** 正文按纯文本呈现。
7. **不做页签状态的持久化**（不落库、不写 URL、不记忆上次页签）。
8. **不新增第四个页签**（例如「日志」）：`#72` 是 backlog 的入口票，不在本能力范围内。
9. **不改 `agent_config` 的三列与 `PATCH` 字段面**（D2 的边界）；不做列删除、不改默认值、不回填。
10. **不改 `app/README.md`**：它不在本票 Ownership 内；文档同步由实施票承担（清单见 D9 末段）。
11. **不做移动端像素级核对**：本仓没有浏览器测试框架，页签在 ≤900px 的行为只做 CSS 与逻辑层核对。
12. **不重排/格式化人的 `mcp.json`**：保存原样写盘。

## Further Notes

### N1 · 术语决议（inline 落进 `CONTEXT.md` 的 `## Language`）

| 词条 | 动作 | 理由 |
| --- | --- | --- |
| **Agent 详情页签 (Agent Detail Tab)** | **新增** | 三个页签的名字、归属与「不是弹窗 / 不是 dock 形态」的边界需要一个正本词条；否则「页签」这个词在 Studio 里既指频道头的消息 / 任务页签、又指详情页的三分页签，会被读成同一件东西 |
| **MCP 声明 (MCP Server)** | **更新** | D7 给 `mcp.json` 加了写面，原词条断言「`mcp.json` 是只读导入源……本轮不提供导出写路径」已不成立；新口径必须写清「库行是装配正本、`mcp.json` 是导入源 + Studio 写面、同名库行胜出」 |

其余词条（**Agent 家目录** / **全局层 / 项目层** / **Skill** / **Agent 配置**）不改：本票没有改变它们的语义
（`mcp.json` 仍在家目录里、技能字节仍由应用写入、`agent_config` 三列仍在）。

### N2 · 与既有 ADR 的关系

- **ADR-0031**：按 D8 做最小事实修正（§一 / §二 / §三 两处 / §四 两行 + 文件头修订标注）；**不新建 ADR**。
  ADR-0031 的核心决策（全局层存在、以库行为 MCP 正本、同名库行胜出、守卫与容器边界）一字不动。
- **ADR-0036**（技能安装有两个来源）：**不动**。本票撤的是界面入口形态（手填路径 / 上传 zip），
  不改变「三个字节来源共用一条写路径」与「应用不执行 `npx`」这两条决策；导入仍是唯一界面入口这一条
  反而更像 ADR-0036 §二 的落点。
- **ADR-0011**（配置写路径是 PATCH 端点）：**不动**。D2 只撤界面控件，PATCH 的字段面与部分更新语义原样。
- **ADR-0009**（文案分两层）：新增文案按既有两层分别落——前端词条（`studio/i18n.js` 两份 catalog）与
  后端按语言偏好渲染的错误文案（`alibot/i18n.py` 两份 catalog）各管自己那一面。

### N3 · 本设计里最容易做错的三格

1. **D2 的载荷**：前端不再提交三项 ⇒ 既有值保持；若照抄旧载荷（提交 `null`），一次保存就把 40 / 0.7 / 30
   清空。实施票的验收项必须显式断言「载荷里没有这三个键」。
2. **D6 的共用弹层**：`capabilityCopyNode()` 是 MCP / 技能共用的一份实现，只删技能那一半会留下一个
   「MCP 行还能点出弹层」的界面——连带后果必须写进票面。
3. **`mcp.json` 的写盘**：`json.dumps` 回写会重排人的键序与缩进（等于一次隐式格式化）；必须原文写盘。

### N4 · 与票面的偏差登记

- 票面把「拆票粒度」列为必须上抛的 Q2；coordinator 答复「按 to-tickets 原语言自选方案、写进票图并在
  Answer 记一句理由」。因此本票**没有**第二轮的 Q2 选择题，而是由本设计给出候选并选定（见 D11 与 Answer）。
- 票面 Q2 的位置被一次**票面外**的 seam 决策占用（`POST /api/skills/{id}/copy` 端点保留与否，票面 D6 明列为
  上抛项）。两次 ask 依次是：Q1 seam 选址 → Q2（`/copy` 端点）；拆票粒度随后按授权自选。

## Answer（本票收口：grill 轮次记录 + 交付物 + 验收自查）

### A1 · 交付物

| 文件 / 面 | 动作 |
| --- | --- |
| `app/specs/agent-detail-tabs-design-notes.md` | 新增：本文件（决策账、seam 选址、断言改写清单、Out of Scope、Answer） |
| `app/specs/agent-detail-tabs-spec.md` | 新增：规格（to-spec 模板 + 两条 seam + user stories + Out of Scope） |
| `docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md` | 修订：§一 / §二 / §三（两处）/ §四（两行）+ 文件头修订标注（D8） |
| `CONTEXT.md` | 更新：新增 **Agent 详情页签**，更新 **MCP 声明**（N1） |
| GitHub Issues | 规格票 [#279](https://github.com/whutlichao/alibot/issues/279)（父票）+ 实施票 #280–#284（子票），blocking 用原生依赖 |
| issue #275 | 一条评论：方向调整 + 剩余范围收窄（[comment](https://github.com/whutlichao/alibot/issues/275#issuecomment-6398704279)） |
| 源码 / 测试 / schema / 迁移 / CI / README | **零改动** |

### A2 · grill 轮次与应答去向

| 轮 | 问题 | 谁答 | 答复 | 去向 |
| --- | --- | --- | --- | --- |
| 1 | **Q1 seam 选址**：`mcp.json` 读写落在 `service.global_mcp_servers()` 同族还是新独立面；`SKILL.md` 正文落在 `/api/skills/{id}` 同族还是新路径 | **人定**（coordinator 转呈） | **双 A**：`mcp.json` 扩展 `global_mcp_servers()` 同族（`…/global-mcp-file`），正文走 `GET /api/skills/{id}/markdown`；要求 docstring 写清「原文」与「只读条目视图」的边界 | D5、D7、D10 |
| 2 | **Q2（票面外但票面明确要求上抛）**：`POST /api/skills/{id}/copy` 端点在后端保留与否 | **人定** | **A：保留端点，只撤前端**；要求把「为什么端点还在」写进交付物（设计文档 + spec），并落成实施票的验收项（docstring 一行）；不采纳 B（删端点）/ C（标弃用） | D6、D9、票图 |
| 3 | 拆票粒度 | **授权自选**（coordinator 答复：按 to-tickets 原语言给候选并自选，理由记 Answer） | 5 票：页签骨架 / 撤复制弹层 / 导入下拉框 / 技能详情右栏 / `mcp.json` 文件块；骨架之后 frontier 一次放出三张内容票 | D11、票图、A4 |

**frontier 为空**：D1–D9 全部由用户定；两处 seam 由 Q1 定；`/copy` 端点由 Q2 定；剩下的是文案键名与控件
实现这类**细节档**，本设计已逐条给出结论（无「待定 / TBD」占位）。`grep -nE "待定|TBD|TODO"` 对本文件
零命中（读数见 A5）。

### A3 · 可观察验收自查

| 票面验收项 | 结论 | 证据 |
| --- | --- | --- |
| 设计文档存在，章节严格等于 effort 模板（Problem Statement / Solution / User Stories / Implementation Decisions / Testing Decisions / Out of Scope / Further Notes）+ Answer，无自由发挥章节、无草案代码块残留 | 通过 | 本文件；正文只有段落、表格与判据，无 ``` 代码块 |
| D1–D9 每条都有落点与结论，无「待定」占位 | 通过 | D1–D9 各一节；占位扫描 0 命中（A5） |
| 术语决议 inline 落进 `CONTEXT.md` 的 `## Language` | 通过 | 新增 **Agent 详情页签** + 更新 **MCP 声明**（N1） |
| ADR 处置按 D8 收口 | 通过 | ADR-0031 七处修订（`git diff` 可指）；#275 评论已发 |
| 规格文件 seams 已声明且尽量少、user stories 足够长、Out of Scope 非空 | 通过 | `agent-detail-tabs-spec.md`：**2 条 seam**（都是既有缝）、**76 条 user stories**、**14 条 Out of Scope**；三个数字的产生命令与读数见 A5（不在这里二次自报） |
| 票图逐票带标题 / 要构建什么 / 验收标准；blocking 用原生依赖；图无环；frontier ≥ 1 | 通过 | 见 A4 的票号回填与依赖读数 |
| 项目文件改动时按 `pr` skill 写 PR body、推分支、`--body-file` 建/更新 PR | 通过 | 见 A6 |
| worker_done 带真实 Task/Dispatch ID 并核实回执 | 通过 | 见 A6 |

### A4 · 票图与依赖（已发布）

| # | issue | 标题 | 直接阻塞（原生 `blocked_by`） | 交付什么 |
| --- | --- | --- | --- | --- |
| 1 | [#280](https://github.com/whutlichao/alibot/issues/280) | 实施票：Agent 详情三分页签骨架与内容归位（含上下文三项撤除） | 无（frontier） | 三个页签可切换、内容各归其位、`data-adtab` 恢复、保存载荷降到 7 字段、既有断言改写 |
| 2 | [#281](https://github.com/whutlichao/alibot/issues/281) | 实施票：撤掉「复制到其他成员」弹层与两个 kind 的复制入口（端点保留） | 无（frontier） | 弹层、入口、呈现状态与死代码消失；`/copy` 端点保留并在 docstring 注明 |
| 3 | [#282](https://github.com/whutlichao/alibot/issues/282) | 实施票：skills 页签的「从本机导入」下拉框（一次一个），撤掉手填路径与上传 zip 两条入口 | #280 | 下拉框分组 / 禁用 / 单发导入 / 409 归因；两条旧入口撤除 |
| 4 | [#283](https://github.com/whutlichao/alibot/issues/283) | 实施票：skills 右栏详情与 SKILL.md 正文只读端点 | #280 | `GET /api/skills/{id}/markdown` + 元数据 + 正文 + 空态 + 启停删除 |
| 5 | [#284](https://github.com/whutlichao/alibot/issues/284) | 实施票：mcp 页签的两块面与家目录 mcp.json 读写端点 | #280 | 库行表单归位 + `GET/PUT …/global-mcp-file` + 三道校验 + 原文写盘 + 优先级标注 |

**父票**：[#279](https://github.com/whutlichao/alibot/issues/279)（规格票，5 张实施票全部是它的 native sub-issue）。

**依赖读数**（`gh api repos/whutlichao/alibot/issues/<n> --jq .issue_dependencies_summary`）：

| issue | `blocked_by` | `blocking` |
| --- | --- | --- |
| #280 | 0 | 3 |
| #281 | 0 | 0 |
| #282 | 1 | 0 |
| #283 | 1 | 0 |
| #284 | 1 | 0 |

**图无环**：全部边由 #280 指向 #282/#283/#284；#281 独立。**frontier = #280、#281**（无阻塞、无 assignee）。
每张子票正文**不写依赖行**（依赖只由原生边承载）；#280 / #281 保留模板里的「None (can start immediately)」。

### A5 · 实测读数

每条读数都是**可复核**的：给命令与当时的输出，而不是二次自报。

| 项 | 命令 | 读数 |
| --- | --- | --- |
| user stories 条数 | `awk '/^## User Stories/,/^## Implementation Decisions/' app/specs/agent-detail-tabs-spec.md \| grep -cE '^\s*[-*] \|^[0-9]+\. '` | **76** |
| Out of Scope 条数 | `awk '/^## Out of Scope/,/^## Further Notes/' app/specs/agent-detail-tabs-spec.md \| grep -cE '^[0-9]+\. '` | **14** |
| seam 条数 | `awk '/^### D10 · Seam 选址/,/^### D11/' app/specs/agent-detail-tabs-spec.md \| grep -cE '^\| `(mcp\.json\|SKILL\.md)`'` | **2**（都是既有缝，零新增服务模块） |
| D9 断言改写清单条数 | `awk '/### D9 · 既有测试与文档断言的改写清单/,/^\*\*`app\/tests\/test_skill_copy.py`/' app/specs/agent-detail-tabs-design-notes.md \| grep -cE '^\| [0-9]+ \|'` | **14**（7 条 `agent_detail_ui` + 7 条 `agent_capabilities_ui`） |
| ADR-0031 被改的句子 | `git diff adc079b -- docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md`，逐块读 `-` / `+` 配对 | 七处对句（文件头标注 + §一 表格 + §二段 + §三 两处 bullet + §四 两行） |
| 占位扫描（`待定\|TBD\|TODO`） | `grep -rnE '待定\|TBD\|TODO'` 对本文件 / 规格 / ADR-0031 / `CONTEXT.md`，再 `grep -vE '占位\|grep -rnE\|grep -nE\|0 命中'` 排掉自指行 | 原始 **3 行**（全是描述这条扫描本身的句子），排掉自指行后 **0 行**——即正文没有真实占位 |
| 机械守卫 | — | 未触发：本票不碰 `app/alibot/studio/**`、`app/README.md`、`app/tests/**`；`test_docs_vocabulary_guard.py` 只读 `README` 与两个词表常量，`i18n_ui.test.cjs` 只扫 Studio 三件套 |
| `git status` | `git status --porcelain` | 提交前只有本文档 / 规格 / PR 正文三处被本修正票改动（`CONTEXT.md` 与 ADR-0031 是上一轮已提交的） |
| 源码 / 测试 / schema | `git diff adc079b..HEAD --name-only \| grep -E '^(app/alibot/\|app/tests/\|scripts/\|\.github/)'` | **无输出**（零改动）；`SCHEMA_VERSION` 未 bump |

**为什么这三个数会错**（本票修正的一处）：A3 原来写「71 条 user stories、12 条 Out of Scope」——那是设计
初稿的估计值，规格定稿后正文扩写到 76 / 14 时没回头改 A3。同一错误在 pr-body 里**没有**发生（那里写的
是 76 / 14），而 `worker_done` 摘要里写的也是 76 / 14——因此这是一处**单点遗漏**，不是成体系的数错。
修正后凡是引用这三个数的地方（本文件 A3 / A5、pr-body Summary）都指到本表的命令与读数。**同一个错误模式**
在本票修正时又抓出三处（pr-body 的「4 个文档路径」实为 5 个；pr-body 里一段提交前的过期 `git diff --stat` 快照；
A5 占位扫描的「唯一一行」实为 3 行），均已改掉或改成不随提交漂移的口径。存量错误一律换成**可复核命令 + 当时读数**。

### A6 · PR 与收尾

| 项 | 值 |
| --- | --- |
| 分支 | `agent-detail-tabs-design` |
| 本票 base | `adc079b`（= `origin/main`） |
| 改动规模（不随提交漂移的口径） | 两个历史锚点实测：`git diff adc079b..6219931 --shortstat` → `5 files changed, 1146 insertions(+), 15 deletions(-)`；`git diff adc079b..ce701ed --shortstat` → `5 files changed, 1148 insertions(+), 15 deletions(-)`（差 2 行 = 回填 PR 号那次）。**本修正提交之后的现值不在这里写死**，以 `git diff adc079b..HEAD --shortstat` 或 PR #285 的读数为准（会随后续修正提交继续增长） |
| PR 正文 | `app/specs/agent-detail-tabs-pr-body.md`（`--body-file` 直接取仓库内路径，随本票一起被跟踪） |
| PR | [#285](https://github.com/whutlichao/alibot/pull/285)（base `main`，head `agent-detail-tabs-design`）——本票的读数修正提交也推在**同一个** PR 上，未开新 PR |
| 提交 | `6219931`（设计 + 规格 + ADR + 词条）→ `ce701ed`（回填 PR 号）→ 本次读数修正提交（只改 design-notes 与 pr-body） |
