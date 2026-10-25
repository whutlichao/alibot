# 验收取证：#281 撤掉「复制到其他成员」弹层与两个 kind 的复制入口（端点保留）

**票型**：实施票（前端撤除 + 回归测试 + 一处后端 docstring）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：GitHub #281（父规格 #279 / `app/specs/agent-detail-tabs-spec.md`）
**base**：`0a359b8`（= 派活时 `agent-detail-tabs-integration` tip）｜**branch**：`issue-281-impl`
**采集时间**：2026-10-25｜**环境**：macOS（arm64），Node v25.0.0，Python 3.11.13（仓库根 `.venv`）
**改动面**：`app/alibot/studio/{app.js,i18n.js}`、`app/alibot/api.py`（两处 docstring）、
`app/tests/agent_capabilities_ui.test.cjs`。
**后端行为零改动**：`api.py` 只加两行 docstring，端点实现、schema、迁移链与 HTTP 契约一字未动；
`app/tests/test_skill_copy.py` 一字未改（8 passed）。

本文件是票面 AC 的支撑材料：每条判据记录**命令**与**实际跑出来的结果**。红证据在**隔离的基线副本**
上取得（`scripts/red-evidence.sh` 用 `git archive` 导出基线，只换测试不换产品代码），
**不动**本 worktree 的工作区。

---

## 1. 票面 AC → 落点 → 读数

票面 AC 是验收正本（`gh issue view 281 --json body`）。

| # | 票面 AC | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 技能行与 MCP 行都不再有复制入口（正本里 `data-copy-*` 一类标识零命中） | `app.js` `skillRow()` / `mcpRow()` 各删掉那一行 `resourceButton('agentCapabilities.copyTo', 'copy*', …)` | `grep -rn "data-copy" app/alibot/studio/` → **0 命中**（退出码 1）；用例「复制入口与弹层撤除」断言 `data-copy-mcp` / `-skill` / `-target` / `-confirm` / `-cancel` / `capability-copy` 在 Studio 三件套（`app.js` + `index.html` + `app.css`）里 **0 命中**，并断言运行时 `data-copy-mcp` / `data-copy-skill` 节点为 `null` |
| 2 | 详情页运行时不再出现复制弹层节点（点不到、也查不到对应选择器） | `agentCapabilitiesSection()` 里两处 `if (view.copy && …) section.appendChild(capabilityCopyNode(view))` 删除 | 同一条用例断言 `capability-copy-dialog` / `capability-copy-targets` / `capability-copy-confirm` 三个 `data-od-id` 在渲染后的 `#adBody` 里都是 `null`；`grep -rn "capabilityCopyNode\|openCapabilityCopy\|..." app/alibot/` → **0 命中** |
| 3 | 只为复制弹层存在的函数与呈现状态全部删除（无死代码、无只被这些函数调用的辅助函数） | 见下面 §2 的逐条删除清单 | `grep -rnE "capabilityCopyNode\|openCapabilityCopy\|closeCapabilityCopy\|toggleCapabilityCopyTarget\|submitCapabilityCopy\|copyResultText\|capabilityMemberName\|copyTarget\|state\.copy\|view\.copy" app/alibot/` → **0 命中**（退出码 1）；用例对 `app.js`（剥注释后）逐符号断言同上 8 个符号 + `view.copy` / `state.copy` 均不出现 |
| 4 | `POST /api/skills/{skill_id}/copy` 端点仍存在且行为不变：既有契约用例全绿 | `app/alibot/api.py:1267` 的 `@app.post(...)` 装饰器与整个 `copy_skill()` 函数体**一字未动**；`app/tests/test_skill_copy.py` **一字未改** | `cd app && ../.venv/bin/python -m pytest tests/test_skill_copy.py` → **8 passed in 1.58s**（覆盖同名 409 不覆盖 / 复制给自己 400 / 弃用频道级行 400 / 路径不安全拒绝 / 目标缺失 400）；`grep -n 'post("/api/skills/{skill_id}/copy"' app/alibot/api.py` → 命中 `1267:` |
| 5 | 该端点（或模块 docstring）有一行注明「当前 Studio 无入口，保留为脚本 / API 用法」 | ① `api.py` 模块 docstring 的 `POST /api/skills/{id}/copy` 那一行（约第 52 行）追加第二行；② 同一句也补进 `copy_skill()` 自己的 docstring（读者不必回溯模块头） | `grep -n "当前 Studio 无入口，保留为脚本 / API 用法" app/alibot/api.py` → 命中 **53** 与 **1282** 两行 |
| 6 | 撤除没有连带改坏 MCP 行的启停 / 删除 / 远端确认三个动作 | `mcpRow()` 只删了复制那一行，`mcpToggle` / `mcpDelete` / `mcpConsent` 三条 `resourceButton` 与 `bindEvents()` 里对应的三个 `closest(...)` 分支原样 | `grep -n "mcpToggle\|mcpDelete\|mcpConsent" app/alibot/studio/app.js` → 三条入口（`7517` / `7523` / `7526`）与三条委派分支（`9849-9861`）都在；用例「Agent 详情的能力段」断言 `byDataset(agentBody, "mcpToggle"/"mcpDelete", "mcp_1")` 非空，用例「MCP 编辑表单沿用既有形状」断言编辑路径照常发出 `PATCH /api/mcp-servers/mcp_1` |

---

## 2. 逐条删除 / 改写与决策出处的对应（D6 / D9）

### 2.1 产品代码（`app/alibot/studio/app.js`，净 −208 行）

| 删 / 改 | 位置 | 指回 |
| --- | --- | --- |
| `state` 注释里「`copy` 是复制弹层状态」一句 | `state.agentCapabilities` 上方注释 | D6（呈现状态一并删除） |
| `emptyCapabilitiesView()` 的 `copy: null` 字段与其上方两行注释 | `emptyCapabilitiesView` | D6 |
| `mcpRow()` 的 `copyMcp` 入口 | MCP 行 actions | D6 / N3 第 2 格（两 kind 共用一份实现，必须一起撤） |
| `skillRow()` 的 `copySkill` 入口 | 技能行 actions | D6 / N3 第 2 格 |
| `agentCapabilitiesSection()` 里两处 `capabilityCopyNode(view)` 挂载 | MCP 小节末 / 技能小节末 | D6 |
| `capabilityMemberName()`（**只被 `copyResultText()` 调用**的辅助函数） | 原 `:7769` 一带 | D6「只被这些函数调用的辅助函数一并删」 |
| 整段 `/* ── 复制到其他成员 … ── */`：`openCapabilityCopy` / `closeCapabilityCopy` / `toggleCapabilityCopyTarget` / `copyResultText` / `submitCapabilityCopy`（含其中「复制后清名册缓存」的 `delete state.rosterCapabilities[targetId]`）/ `capabilityCopyNode` | 原 `:8082-8243`（158 行） | D6 + 票面「弹层本体、目标多选、逐条结果、复制后清名册缓存的清理逻辑一并删除」 |
| `bindEvents()` 的 `change` 分支 `data-copy-target` | 原 `:9965` | D6 |
| `bindEvents()` 的 `click` 分支五条（`data-copy-mcp` / `-skill` / `-confirm` / `-cancel`，以及随之只剩空壳的 `openCapabilityCopy` / `submitCapabilityCopy` / `closeCapabilityCopy` 调用点） | 原 `:10070-10087` | D6 |
| 四处已说谎的注释（能力段标题「含各自的复制弹层」、`paintAgentCapabilities()` 的动作清单含「复制」、模块注释「增删改 / 启停 / 确认 / 复制」、`skillImportResultText()` 引用已删的 `copyResultText`） | 见 `git diff` | D9「不留说谎的注释」（评审判据 6） |

### 2.2 词条（`app/alibot/studio/i18n.js`，净 −33 行）

| 删 / 改 | 指回 |
| --- | --- |
| 两份 catalog 各删 12 个键：`agentCapabilities.copyTo` / `copyTitle` / `copyHint` / `copyConfirm` / `copyCancel` / `copyNoTargets` / `copyPickRequired` / `copyBusy` / `copyResultCopied` / `copyResultSkipped` / `copyResultFailed` / `copySummary` | D9 第 14 条（键清单随入口撤除而变）+ D9 第 5 条同一规则（「与上一轮 16 个孤儿键同一规则」：控件撤除 ⇒ 词条一并删） |
| 两份 catalog 的能力配置段头注释：从「复制是行复制 / 字节复制，同名目标跳过并逐条说明」改成「控件只作用于当前成员（#281 撤掉了跨成员通道）」 | D9 文档同步条（不留说谎的注释） |

> **本次实际变动了 12 个词条键**（11 个 `copy*` + `copySummary`）。`copySummary` 原本没有独立断言，
> 按同一条「随消费点消失而删键」的规则一并删除并纳入 `undefined` 断言。

### 2.3 后端（`app/alibot/api.py`，+6 −3 行，全是 docstring）

| 改 | 指回 |
| --- | --- |
| 模块 docstring 的 `POST /api/skills/{id}/copy` 行追加「当前 Studio 无入口，保留为脚本 / API 用法」 | D6「端点处置（Q2 = A，人定）」/ 票面 AC 5 |
| `copy_skill()` 自身 docstring 补同一句（含「端点与契约用例原样保留」） | 同上（D6 允许「该端点或 `api.py` 模块 docstring」，两处都写是为了读者任一路径都能看见） |

> **MCP 行没有对应说明**：它的复制走既有 `POST /api/mcp-servers`，无专用端点（D6 原文）。

### 2.4 测试（`app/tests/agent_capabilities_ui.test.cjs`，+103 −65 行）

| # | D9 登记项 | 本票做法 |
| --- | --- | --- |
| 9 | 复制入口断言（原 `:657-658` 的 `copyMcp` / `copySkill`）删除，改成断言 `data-copy-*` 在正本里 0 命中 | 删掉那两条 `assert.ok`；新增用例「复制入口与弹层撤除：正本里 data-copy-* 零命中，运行时也没有弹层节点」（6 个 `data-*` 标识 × 三件套 + 9 个符号 × `app.js` 剥注释后） |
| 13 | 「复制入口列出其他成员…」（原 `:880`）与「目标已存在同名时逐条提示已跳过…」（原 `:912`）两条用例删除 | 两条用例整段删除；只为它们服务的 harness 桩（`POST /api/mcp-servers` 的 `copyConflict` 分支、整条 `/api/skills/{id}/copy` POST 桩）也一并删除，不留死的测试装置 |
| 14 | 键清单随入口撤除而变 | 「新增文案在两份词条里同键同参数位」用例改名并补一段反向断言：12 个 `copy*` 键在 `zh-CN` 与 `en` 两份 catalog 里都必须 `undefined` |
| — | 文件头注释第 2 条原写「复制到其他成员：每行一个入口，弹多选…」 | 改写成撤除口径（与上面第 9 / 13 条同步，否则注释与断言互相打脸） |

> 除上面登记的 9 / 13 / 14 三条以外，`agent_capabilities_ui.test.cjs` 里没有第二条断言必须改：
> 其余 11 条用例在实现撤除后**原样通过**（见 §3 的 13 / 13 读数）。

---

## 3. 测试读数

### 3.1 目标用例文件（工作区，实现之后）

```sh
node --test app/tests/agent_capabilities_ui.test.cjs
# → ℹ tests 13 / ℹ pass 13 / ℹ fail 0
```

逐条（13 条全绿）：

```
✔ Agent 详情的能力段：两种能力同屏，请求固定带 agent scope
✔ 复制入口与弹层撤除：正本里 data-copy-* 零命中，运行时也没有弹层节点
✔ 能力段里没有 scope 选择器：作用域恒为当前成员
✔ MCP 编辑表单沿用既有形状，提交走 agent scope 的 PATCH / POST
✔ 技能本机目录安装在 agent scope 下提交，成功按后端重读
✔ 导入区块：读候选、默认勾选「可导入且未安装」、已安装与不可导入项禁用
✔ 导入选中：逐条 POST /api/skills（source_path = 候选 path），409 记已跳过，失败记原因
✔ 候选为空（来源在但没有技能）给空态指引，不渲染空列表
✔ 导入结果：409 记「已存在，已跳过」且不覆盖，其他失败记可读原因
✔ 候选来源缺席 / 扫描失败：只落在导入区块自己的行，不拖垮技能段
✔ 频道配置里不再出现 MCP / 技能段，也不再按频道 scope 读两份清单
✔ 名册的 Agent 行显示 MCP n / 技能 n，读不到时只让那一行显示占位
✔ 新增文案在两份词条里同键同参数位，撤除的复制键必须消失
```

基线（`0a359b8`，实现之前，本文件是 14 条）是 14 / 14 绿；本票删掉 D9 第 13 条的两条用例、
新增一条反向用例，净 −1 条。

### 3.2 后端契约用例（**一字未改**）

```sh
cd app && ../.venv/bin/python -m pytest tests/test_skill_copy.py
# → 8 passed in 1.58s
```

覆盖票面 AC 4 逐条点名的五种行为：同名 409 不覆盖 / 复制给自己 400 / 弃用频道级行 400 /
路径不安全拒绝 / 目标缺失 400。

### 3.3 Studio DOM 用例族（全量 glob）

```sh
node --test app/tests/*.test.cjs
# → ℹ tests 321 / ℹ pass 321 / ℹ fail 0 / duration_ms 3881
```

### 3.4 全量门禁 `scripts/check.sh`

```sh
scripts/check.sh
```

| 步骤 | 读数 |
| --- | --- |
| pytest（全量） | `1210 passed, 11 skipped in 739.52s (0:12:19)` |
| ruff check | `All checks passed!` |
| ruff format --check | 通过（未跑 `ruff format`，避免整文件重排） |
| mypy（仓库根） | `Success: no issues found in 128 source files` |
| pyright | `0 errors, 0 warnings, 0 informations` |
| node --test（glob） | `ℹ tests 321 / ℹ pass 321 / ℹ fail 0` |
| 结论 | **门禁全绿** |

---

## 4. 红 → 绿证据

### 4.1 隔离基线红读数（只换测试，不换产品代码）

```sh
scripts/red-evidence.sh 0a359b8 app/tests/agent_capabilities_ui.test.cjs
```

- 基线：`0a359b8 (0a359b8)`
- 基线 `app.js` md5：`104ad4258577290d598cfc69fbf3066a`
- 证据目录：`/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.0Lwr7N`
- 读数：**13 tests / 11 pass / 2 fail**

```
✖ 复制入口与弹层撤除：正本里 data-copy-* 零命中，运行时也没有弹层节点
  AssertionError [ERR_ASSERTION]: MCP 行不该再有复制入口
  at agent_capabilities_ui.test.cjs:676
✖ 新增文案在两份词条里同键同参数位，撤除的复制键必须消失
  AssertionError [ERR_ASSERTION]: zh-CN 里不该再有 agentCapabilities.copyTo（true !== false）
  at agent_capabilities_ui.test.cjs:1034
```

两条红点分别咬住「入口 / 弹层还在」与「词条还在」，正是本票要改的两处；
其余 11 条在基线上就是绿（它们断言的是本票**不**许改坏的行为）。

### 4.2 同一份测试在实现之后

`node --test app/tests/agent_capabilities_ui.test.cjs` → **13 / 13 通过**。

红 → 绿的方向证明：新断言咬住旧实现（红），实现撤除后变绿，且不靠删断言让别的用例变绿
（基线 14 条里只有 D9 第 13 条登记的两条被删）。

---

## 5. 零命中证据（可直接复跑）

```sh
# ① Studio 三件套里不再有任何 data-copy-* 标识
grep -rn "data-copy" app/alibot/studio/
# → 无输出，退出码 1

# ② 复制通道的函数 / 呈现状态一个都不剩（含只被它们调用的辅助函数）
grep -rnE "capabilityCopyNode|openCapabilityCopy|closeCapabilityCopy|toggleCapabilityCopyTarget|submitCapabilityCopy|copyResultText|capabilityMemberName|copyTarget|state\.copy|view\.copy" app/alibot/
# → 无输出，退出码 1

# ③ 复制专用词条键在两份 catalog 里都没了
grep -rn "agentCapabilities\.copy" app/alibot/
# → 无输出，退出码 1

# ④ 端点与保留说明仍在（对照项，不是零命中）
grep -n 'post("/api/skills/{skill_id}/copy"' app/alibot/api.py
# → 1267:    @app.post("/api/skills/{skill_id}/copy", status_code=201)
grep -n "当前 Studio 无入口，保留为脚本 / API 用法" app/alibot/api.py
# → 53:  （模块 docstring） …  1282:  （copy_skill() docstring）
```

`data-copy` 全仓仍有命中，但只在**本票测试文件**（断言用字面量）与**设计笔记 D9 表**
（登记「改成断言 `data-copy-*` 零命中」）里 —— 产品代码 0 命中。

---

## 6. 已知边界与偏差

| 项 | 说明 |
| --- | --- |
| 词条键的删除范围 | 本次动了 12 个键（11 个 `copy*` + `copySummary`），按 D9 第 5 条「随消费点消失而删键」的既有规则处理；`copySummary` 原本无独立断言，一并纳入 `undefined` 断言。coordinator 已裁定这条属 D9 第 5 条规则的延伸，不是新增授权。 |
| `#280` 的邻接 | 本分支从 `0a359b8`（integration tip）切出，未预合并 #280。D9 第 14 条用例（词条键清单）在 #280 里也会被重写；推进顺序是 #281 先合入 integration，#282 / #280 从更新后的 tip 切分支。冲突若落在 D9 邻接处，按 D9 意图解（本票已把 12 个 `copy*` 键移进 `undefined` 断言）。 |
| 未改动 | 未碰 `docs/adr/**` / `CONTEXT.md` / `.github/**` / `scripts/**` / `app/tests/test_skill_copy.py`；未改 `SCHEMA_VERSION`、迁移链、装配层、唤醒与预算纪律。`app/README.md` 的详情页小节仍写「所有区块在同一页纵向铺开……**没有页签**」——那是 #280 骨架票的文档同步面，不在本票 Ownership。 |
