# 验收取证：#282 skills 页签的「从本机导入」下拉框（一次一个），撤掉手填路径与上传 zip 两条入口

**票型**：实施票（前端入口收敛 + 回归测试 + 一处后端 docstring）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：[#282](https://github.com/whutlichao/alibot/issues/282)（验收正本 = 票面 AC）
**父规格**：`app/specs/agent-detail-tabs-spec.md`（D3 / D4 / D9 / T1–T4 / Out of Scope / N1）
**设计正本**：`app/specs/agent-detail-tabs-design-notes.md`（D3 / D4 / D9 第 10–14 条）
**base**：`a5bdf49`（= 派活时 `agent-detail-tabs-integration` tip）｜**branch**：`issue-282-impl`
**改动面**：`app/alibot/studio/{app.js,i18n.js}`、`app/alibot/api.py`（**只加 docstring**）、
`app/tests/agent_capabilities_ui.test.cjs`、本文件。
**后端行为零改动**：`api.py` 只加三处 docstring 句子，`POST /api/skills` 的 zip 与 directory 两条分支
逐字未动；`app/tests/test_skill_import.py` / `test_skills.py` 一字未改、照常绿；不 bump `SCHEMA_VERSION`、
不写迁移。

本文件是票面 AC 的支撑材料：每条判据记录**命令**与**实际跑出来的结果**。红证据在**隔离的基线副本**
上取得（`scripts/red-evidence.sh` 用 `git archive` 导出基线，只换测试不换产品代码），
**不动**本 worktree 的工作区。

---

## 1. 票面 AC → 落点 → 读数

| # | 票面 AC | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | skills 页签里安装入口只有一个：一个下拉框（点开后展开候选），页面上不再有手填路径输入框、不再有文件选择控件 | `app.js`：`agentSkillsTabNodes()` 只在顶部挂 `skillImportForm(view)`；`skillInstallForm()` 整函数删除（含 `dirRow` / `zipRow`） | 用例「两条既有安装入口撤除…」：运行时 `skill-source` / `skill-install-dir` / `skill-zip` 三处 `null`，skills 页签里 `input[type=file]` 数组为空；`grep -rn "skill-source\|skill-install-dir\|skill-zip" app/alibot/studio/` → **0 命中**（退出码 1） |
| 2 | 下拉框默认不预选；候选按 可选 / 已安装 / 不可导入 三组呈现，后两组禁用，不可导入项的选项文本带原因 | `app.js`：`skillImportPicker()` 建 `<select data-od-id="skill-import-picker">`，空选项文案 `skill.importPickPlaceholder`，三个 `<optgroup>`（`skillImportGroupKey()` 分组），`option.disabled = !skillImportSelectable(...)`，文本由 `skillImportOptionText()` 拼后缀（不可导入项带 `skill.importStatusBlocked` 的 `{reason}`） | 用例「导入下拉框：候选按 可选 / 已安装 / 不可导入 三组呈现…」：`picker.tagName === 'select'`、`picker.value === ''`、空选项文本 = `skill.importPickPlaceholder`、三组 `label` 序列 = 三个组标题词条、`ready.disabled === false` / `installed.disabled === true` / `blocked.disabled === true`、`blocked.textContent` 匹配 `/缺少 SKILL\.md/`；同用例断言页签里 `input[type=checkbox]` 数为 **0**（不是多选勾选列表） |
| 3 | 选中一项后发**一次**安装请求（`source_path` = 该候选路径），不会批量提交多项 | `app.js`：`pickSkillImportCandidate()` → `submitSkillImport()`（单个 `candidate`，一次 `api('/api/skills', {method:'POST', body:{scope_kind,scope_id,source_path}})`） | 用例「导入：选中一项后发一次 POST /api/skills…」：`imports.length === 1` 且 `deepEqual(imports[0].body, {scope_kind:'agent', scope_id:'mem-1', source_path:'/Users/me/.agents/skills/review'})` |
| 4 | 同名（409）显示「已安装，已跳过」而不是静默失败；其他失败显示后端给出的可读原因 | `app.js`：`submitSkillImport()` 里 `err.status === 409 → status:'skipped'`、其余 `status:'failed'` 且 `reason` 取 `err.message`；`skillImportForm()` 给 failed 那一行加 `data-od-id="skill-import-error"` + `role="alert"` | 用例「导入归因：409 记「已安装，已跳过」…」：`/review：已安装，已跳过/`（该行的文案键是 `skill.importResultSkipped`，两份 catalog 均已改为「已安装，已跳过」/「already installed, skipped」）、`/other：导入失败：缺少 SKILL\.md/` 落在 `skill-import-error`，且 `skill-error` 为 `null`、右栏 `agent-skill-detail` 仍在；同用例断言 `PATCH /api/skills/*` 数为 **0**（冲突不许改用 PATCH 覆盖） |
| 5 | 导入结束后候选重扫、技能列表重读、下拉框回到空选择 | `app.js`：`submitSkillImport()` 末尾 `view.skillImportPick = ''` → `await loadAgentSkills()` → `await loadSkillImportCandidates()` | 同用例 3：候选 GET 计数 > 导入前、`/api/skills?scope_kind=agent&scope_id=mem-1` 计数 > 导入前、刷新后 `byOdId(...,'skill-import-picker').value === ''` |
| 6 | 候选来源目录不存在时给空态指引且不报错 | `app.js`：`skillImportForm()` 在 `!candidates.length` 分支渲染 `skill.importUnavailable`（`{source}` 来自后端 `source`），不渲染下拉框 | 用例「候选来源缺席 / 扫描失败…」：`candidatesAvailable:false` 时 `skill-import-picker === null`、区块文本匹配 `/先运行上面的命令/`、`skill-error === null`；同一用例的另一半（扫描 500）断言 `skill-import-error` 存在而 `skill-error` 为 `null` |
| 7 | 正本里 `skill-source` / `skill-install-dir` / `skill-zip` 三处标识零命中 | 见 AC1 | `grep -rn "skill-source\|skill-install-dir\|skill-zip" app/alibot/studio/` → **无输出，退出码 1**；用例里对三件套（`app.js` + `index.html` + `app.css`）逐一断言 8 个标识（含 `skillSourcePath` / `submitSkillDirectory` / `uploadSkillZip` / `data-skill-source` / `data-skill-zip`）全部 `false` |
| 8 | `POST /api/skills` 的 zip 与 directory 两条分支行为不变（既有安装器契约用例绿） | `api.py`：`install_skill()` 函数体一字未动，只在 docstring 末尾加两句说明 | `grep -n 'post("/api/skills"' app/alibot/api.py` → `1174:`；`cd app && ../.venv/bin/python -m pytest tests/test_skill_import.py tests/test_skills.py -q` → **18 passed**；全量 pytest 见 §3.2 |

### 1.1 撤除后不留死代码（规格 D3 / 评审判据 6）

| 撤除项 | 原落点 | 读数 |
| --- | --- | --- |
| `skillInstallForm()`（手填路径 + zip 文件框整块） | 原 `:7781` | `grep -rn "skillInstallForm" app/alibot/` → **0 命中** |
| `submitSkillDirectory()` | 原 `:7968` | **0 命中** |
| `uploadSkillZip()` | 原 `:8032` | **0 命中** |
| `view.skillSourcePath` 呈现状态 + `capabilityDraftDirty()` 里对它的判定 | 原 `:7158` / `:7200` | **0 命中**（`capabilityDraftDirty()` 现在只看 MCP 草稿） |
| `view.skillInstalling` 呈现状态 | 原 `:7156` | **0 命中**（只服务那两条入口） |
| `skillImportRow()` / `skillImportStatusKey()` / `toggleSkillImportPick()`（多选行的渲染与勾选） | 原 `:7366` / `:7470` / `:7377` | **0 命中** |
| `view.skillImportPicks`（数组）→ `view.skillImportPick`（单值） | `emptyCapabilitiesView()` | 单选值，`grep skillImportPicks` → **0 命中** |
| 四个委派分支（`data-skill-source` input / `data-skill-zip` change / `data-skill-import-pick` 勾选 / `data-skill-install-dir` click / `data-skill-import-run` click） | `bindEvents()` 的 `#adBody` 三条链 | 只剩 `data-skill-import-pick`（现在是下拉框的 change）与 `data-skill-import-scan`（刷新按钮） |

## 2. D9 断言改写（第 10–14 条）与偏差登记

### 2.1 D9 已登记（本票照 D9 改）

| D9 # | 原用例 | 改法 | 读数 |
| --- | --- | --- | --- |
| 10 | 「技能本机目录安装在 agent scope 下提交，成功按后端重读」 | **删除**；改成「两条既有安装入口撤除：正本里 skill-source / skill-install-dir / skill-zip 三处零命中」（运行时三处 `null` + 无 file input + 正本 8 个标识零命中） | 该用例绿 |
| 11 | 「导入区块：读候选、默认勾选「可导入且未安装」、已安装与不可导入项禁用」 | 改成「导入下拉框：候选按 可选 / 已安装 / 不可导入 三组呈现，不可选项禁用且带原因，默认不预选」；**保留原意**：命令提示逐字照旧、候选按 agent scope 读、已安装与不可导入项禁用、不可导入原因可见 | 该用例绿 |
| 12 | 「导入选中：逐条 POST /api/skills…409 记已跳过，失败记原因」 | 改成「导入：选中一项后发一次 POST…」+ 新增「导入归因：409 记「已安装，已跳过」，其他失败记后端可读原因并留在导入区自己的错误行」；**保留原意**：写路径仍是 `POST /api/skills` + `source_path` = 候选 path、409 → skipped、失败 → 可读原因、冲突不改用 PATCH | 两条用例绿 |
| 14 | 「新增文案在两份词条里同键同参数位，撤除的复制键必须消失」 | 「必须有值」清单：删 `skill.importRunButton` / `skill.importStatusReady` / `skill.importPickRequired` / `skill.importSummary`，加 `skill.importPickLabel` / `skill.importPickPlaceholder` / `skill.importGroupReady` / `skill.importGroupInstalled` / `skill.importGroupBlocked`；「必须 `undefined`」清单加 13 个随入口撤除的键 | 该用例绿（并在基线红，见 §4） |
| — | 同一条用例里「候选为空」「候选来源缺席 / 扫描失败」「mcp 页签里不出现安装表单」三条相邻断言 | 跟随 D9 第 11 条的同一处形态：空候选 / 来源缺席断言「不渲染下拉框」；`skill-install` 断言换成 `skill-import-picker` | 三条用例绿；`agent-capabilities-scope` / `skill-list` 等原意断言原样保留 |

### 2.2 D9 **未登记但本票必须改**（偏差登记；判据 = 断言与新行为正相反就必须改、且不许删原意）

| # | 用例 / 行 | 原断言 | 为何 D9 没盖住 | 改法与判据 |
| --- | --- | --- | --- | --- |
| A | 「skills 页签：左栏技能列表与技能行入口，请求固定带 agent scope」 | `assert.ok(byOdId(panel, "skill-install"), "技能段要有安装入口")` | D9 #10 只说「删除技能本机目录安装用例」，没点名这条**别的**用例里对 `skill-install` 节点的存在断言。入口撤除后它必然红 | 换成 `assert.ok(byOdId(panel, "skill-import-picker"), "skills 页签的安装入口是「从本机导入」下拉框")`。**原意保留**：「skills 页签里存在且只存在这一条安装入口」 |
| B | 「两个页签各读自己那一份」 | `assert.equal(byOdId(mcp, "skill-install"), null, "mcp 页签里不出现安装表单")` | 同上：这是**负向**断言，但断的是已删节点的 id | 换成 `skill-import-picker`。**原意保留**：「mcp 页签里不出现 skills 的安装入口」 |
| C | 文件头注释第 1–5 条 | 第 1 条写「MCP 行与技能行**同屏**」，没有第 3 条关于安装面的口径 | D9 只登记断言，不登记测试文件的头注释；而 #280 已把两 kind 分到两个页签，本票又把安装面收成一个下拉框，头注释会与断言互相打脸 | 头注释重写为 1–6 条（含新第 3 条「安装面只剩一个下拉框」）。**原意保留**：它描述的就是本文件断言的范围 |

> **没有第 4 处**：对 `agent_capabilities_ui.test.cjs` 的全文扫描（`grep -nE "skill-install|skill-source|skill-zip|skillImport|importPick|installDir|installZip"`）共 23 处命中，
> 逐条判定后除上表 A / B / C 与 D9 第 10–14 条之外，余下命中要么在本票新写的用例里，要么是 `channel_resources_ui.test.cjs:450`
> 的 `"skill-install"`（那是**频道配置**面的 0 命中断言，本票未碰频道配置，它照常绿）。
> `agent_detail_ui.test.cjs` **一字未改**（58 / 58 绿）：它的 skills 页签用例只断言左栏列表、右栏占位与启停 / 删除入口，
> 没有断言任何安装入口；D9 第 8 条的「页签内容」在本票前已由 #280 落地。

### 2.3 词条键的实际变动（D9 第 14 条规则的延伸）

| 动作 | 键 |
| --- | --- |
| 删（两份 catalog） | `skill.installTitle` / `skill.installDir` / `skill.installDirPlaceholder` / `skill.installDirButton` / `skill.installDirHint` / `skill.installZip` / `skill.installZipHint` / `skill.installing` / `skill.sourceRequired` / `skill.importRunButton` / `skill.importStatusReady` / `skill.importPickRequired` / `skill.importSummary`（13 个） |
| 加（两份 catalog，同键同参数位） | `skill.importPickLabel` / `skill.importPickPlaceholder` / `skill.importGroupReady` / `skill.importGroupInstalled` / `skill.importGroupBlocked`（5 个，**无参数位**） |
| 改值（同键同参） | `skill.importTitle`（「从 ~/.agents/skills 导入」→「从本机导入」）/ `skill.importCommandHint`（补一句「装好之后在下面的下拉框里选它」）/ `skill.importResultSkipped`（「已存在，已跳过」→「已安装，已跳过」，票面原话） |
| 保留不动 | `skill.importScanButton` / `skill.importLoading` / `skill.importScanFailed` / `skill.importUnavailable` / `skill.importEmpty` / `skill.importStatusInstalled` / `skill.importStatusBlocked` / `skill.importBusy` / `skill.importResultDone` / `skill.importResultFailed` / `skill.installed` / `skill.installFailed`（`skill.installed` 与 `skill.installFailed` 仍被导入路径消费） |

> 判据来源：D9 第 14 条点名的 `skill.installDir` / `installDirButton` / `installZip`「等 → 必须 `undefined`」，
> 加上 #280 / #281 已确立的「随消费点消失而删键」规则（`agentCapabilities.title`、12 个 `copy*` 键同一口径）。
> `skill.importStatusReady`（可导入）随之失去消费点：选项文本对**可选项**只写名字（组标题已经说了「可选」）；
> `skill.importPickRequired` / `skill.importSummary` 随「导入选中」按钮与批量汇总一起消失。

## 3. 测试读数

### 3.1 目标用例文件

```sh
node --test app/tests/agent_capabilities_ui.test.cjs
# → ℹ tests 15 / ℹ pass 15 / ℹ fail 0
```

逐条（15 条全绿）：

```text
✔ skills 页签：左栏技能列表与技能行入口，请求固定带 agent scope
✔ mcp 页签：库行列表、表单与行入口，请求固定带 agent scope
✔ 两个页签各读自己那一份：MCP 读数只在 mcp 页签、技能读数只在 skills 页签
✔ 复制入口与弹层撤除：正本里 data-copy-* 零命中，运行时也没有弹层节点
✔ 能力段里没有 scope 选择器：作用域恒为当前成员
✔ MCP 编辑表单沿用既有形状，提交走 agent scope 的 PATCH / POST
✔ 两条既有安装入口撤除：正本里 skill-source / skill-install-dir / skill-zip 三处零命中
✔ 导入下拉框：候选按 可选 / 已安装 / 不可导入 三组呈现，不可选项禁用且带原因，默认不预选
✔ 导入：选中一项后发一次 POST /api/skills（source_path = 候选 path），结束后重扫候选并回到空选择
✔ 导入归因：409 记「已安装，已跳过」，其他失败记后端可读原因并留在导入区自己的错误行
✔ 候选为空（来源在但没有技能）给空态指引，不渲染下拉框
✔ 候选来源缺席 / 扫描失败：只落在导入区块自己的行，不拖垮技能段
✔ 频道配置里不再出现 MCP / 技能段，也不再按频道 scope 读两份清单
✔ 名册的 Agent 行显示 MCP n / 技能 n，读不到时只让那一行显示占位
✔ 新增文案在两份词条里同键同参数位，随入口撤除的键必须消失
```

条数账：基线（`a5bdf49`）是 **15** 条（`git show a5bdf49:app/tests/agent_capabilities_ui.test.cjs | grep -c '^test('` → 15），
本票 **−1**（删 D9 #10 那条）+ **+1**（D9 #12 拆成「一次一个」与「归因」两条，原用例的断言全部落在两条里）
= **15** 条；D9 #11 / #14 与一条相邻用例是原地改写，条数不变。
核对：`grep -c '^test(' app/tests/agent_capabilities_ui.test.cjs` → 15。

### 3.2 全量前端用例（glob）

```sh
node --test app/tests/*.test.cjs
# → ℹ tests 328 / ℹ pass 328 / ℹ fail 0
```

与派活时给的基线一致（328 全绿），没有把别的用例弄红。

### 3.3 后端契约用例（**一字未改**）

```sh
cd app && ../.venv/bin/python -m pytest tests/test_skill_import.py tests/test_skills.py -q
# → 18 passed
```

### 3.4 全量门禁 `scripts/check.sh`

| 步骤 | 读数 |
| --- | --- |
| pytest（全量） | `1210 passed, 11 skipped in 682.20s (0:11:22)` |
| ruff check | `All checks passed!` |
| ruff format --check | `346 files already formatted`（未跑 `ruff format`） |
| mypy（仓库根） | `Success: no issues found in 128 source files` |
| pyright | `0 errors, 0 warnings, 0 informations` |
| node --test（glob） | `ℹ tests 328 / ℹ pass 328 / ℹ fail 0`（duration 3749ms） |
| 结论 | **门禁全绿** |

## 4. 红 → 绿证据

### 4.1 隔离基线红读数（只换测试，不换产品代码）

```sh
scripts/red-evidence.sh a5bdf49 app/tests/agent_capabilities_ui.test.cjs
```

- 基线：`a5bdf49 (a5bdf49)`
- 基线 `app.js` md5：`19411d32b5267330716d4c75ee5399b3`
- 证据目录：`/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.5yHXI5`
- 读数：**15 tests / 9 pass / 6 fail**

```text
✖ skills 页签：左栏技能列表与技能行入口，请求固定带 agent scope
  AssertionError [ERR_ASSERTION]: skills 页签的安装入口是「从本机导入」下拉框 (null !== true)
✖ 两条既有安装入口撤除：正本里 skill-source / skill-install-dir / skill-zip 三处零命中
  AssertionError [ERR_ASSERTION]: 不再有手填路径的输入框（actual: [FakeNode], expected: null）
✖ 导入下拉框：候选按 可选 / 已安装 / 不可导入 三组呈现，不可选项禁用且带原因，默认不预选
  AssertionError [ERR_ASSERTION]: 安装入口是一个下拉框 (null !== true)
✖ 导入：选中一项后发一次 POST /api/skills（source_path = 候选 path），结束后重扫候选并回到空选择
  TypeError: Cannot set properties of null (setting 'value')
✖ 导入归因：409 记「已安装，已跳过」，其他失败记后端可读原因并留在导入区自己的错误行
  TypeError: Cannot set properties of null (setting 'value')
✖ 新增文案在两份词条里同键同参数位，随入口撤除的键必须消失
  AssertionError [ERR_ASSERTION]: zh-CN 缺少词条：skill.importPickLabel
```

六条红点分别咬住：安装入口的存在形态（A / 下拉框）、两条既有入口仍在、下拉框的首次写路径、
409 / 失败的归因落点、以及新增词条键。其余 9 条在基线上就是绿（它们断言的是本票**不**许改坏的行为：
复制撤除守卫、MCP 表单与两个页签的读数归属、scope 恒为成员、频道配置无能力段、名册摘要）。

### 4.2 同一份测试在实现之后

`node --test app/tests/agent_capabilities_ui.test.cjs` → **15 / 15 通过**。

红 → 绿的方向证明：新断言咬住旧实现（红），实现收成一个下拉框后变绿；没有靠删断言让别的用例变绿
（基线 15 条里只有 D9 第 10 条登记的那一条被删、D9 第 12 条拆成两条 —— 条数不变）。

## 5. 门禁读数（`scripts/check.sh` 全量）

> 本票动了 `app/alibot/api.py` 的 docstring ⇒ 按 `docs/agents/implementation.md` 跑**全量**一次。

```sh
scripts/check.sh
```

| 步骤 | 读数 |
| --- | --- |
| pytest（全量） | `1210 passed, 11 skipped in 682.20s (0:11:22)` |
| ruff check | `All checks passed!` |
| ruff format --check | `346 files already formatted`（未跑 `ruff format`，避免整文件重排） |
| mypy（仓库根） | `Success: no issues found in 128 source files` |
| pyright | `0 errors, 0 warnings, 0 informations` |
| node --test（glob） | `ℹ tests 328 / ℹ pass 328 / ℹ fail 0` |
| 结论 | **门禁全绿** |

## 6. 零命中证据（可直接复跑）

```sh
# ① 票面点名的三处标识在 Studio 里一处不剩
grep -rn "skill-source\|skill-install-dir\|skill-zip" app/alibot/studio/
# → 无输出，退出码 1

# ② 只服务那两条入口的函数 / 呈现状态 / 委派标识一并删除
grep -rn "submitSkillDirectory\|uploadSkillZip\|skillSourcePath\|skillInstalling\|skillInstallForm\|data-skill-source\|data-skill-zip" app/alibot/studio/
# → 无输出，退出码 1

# ③ 随入口撤除的 13 个词条键在两份 catalog 里都没了
grep -rn "skill\.installDir\|skill\.installZip\|skill\.installTitle\|skill\.installing\|skill\.sourceRequired\|skill\.importRunButton\|skill\.importStatusReady\|skill\.importPickRequired\|skill\.importSummary" app/alibot/
# → 无输出，退出码 1

# ④ 对照项（不是零命中）：后端两条分支与它们的保留说明仍在
grep -n 'post("/api/skills"' app/alibot/api.py
# → 1174:    @app.post("/api/skills", status_code=201)
grep -n "zip 上传当前 Studio 无入口\|zip 上传这条界面入口当前" app/alibot/api.py
# → 51:  （模块 docstring）  1188:  （install_skill() docstring）

# ⑤ 新入口的落点
grep -rn "skill-import-picker" app/alibot/studio/
# → app.js:7470（data-od-id 赋值）
```

`skill-source` / `skill-zip` 等字面量在全仓仍有命中，但只在**本票测试文件**（断言用的字面量）与
**设计笔记 D9 表**（登记「改成断言 0 命中」）里 —— 产品代码 0 命中。

## 7. 与集成分支的合并

```sh
git fetch origin agent-detail-tabs-integration
git merge agent-detail-tabs-integration
# → Already up to date.
```

`origin/agent-detail-tabs-integration` 的 tip 仍是 `a5bdf49`（本分支的 base），因此**零冲突**、
无需解决。若之后 integration 推进到 #283 / #284 的 tip，本分支的改动面（`skillImportForm()` /
`agentSkillsTabNodes()` 顶部一处挂载 / 两份 catalog 的 `skill.import*` 段）与那两张票的改动面
（右栏详情、`mcp.json` 文本域）不重叠，唯一的邻接是 `agentSkillsTabNodes()` 与
`agentDetail.skillsDetailPlaceholder` 这两个位置。

## 8. 已知边界与偏差

| 项 | 说明 |
| --- | --- |
| 「一次一个」的触发点 | 选中即发（`change` 事件），不另加一个「导入」按钮。票面要求「选中一项后发**一次**安装请求」，下拉框本身没有提交动作；多一个按钮会让「一次一个」多一步。 |
| 下拉框哪些组会缺席 | 空集合的 `<optgroup>` 不渲染（一个没有内容的组标题只是噪声）。因此「三组」是**有候选时**的分组口径，不是恒定三个 DOM 节点。 |
| 失败与扫描错误共用 `skill-import-error` 这个 od-id | 两者不可能同时出现：扫描失败时不渲染结果行；导入失败时会重新扫描（`loadSkillImportCandidates()` 清掉 `skillImportError`），失败那条来自结果行。用例「导入归因」与「候选来源缺席 / 扫描失败」分别咬了这两种情形。 |
| `skill.importTitle` 的值改了 | 「从 ~/.agents/skills 导入」→「从本机导入」：票面与 D3 / D4 用的入口名是「从本机导入」。命令提示与 `skill.importUnavailable` 仍然逐字说明来源是 `~/.agents/skills`，不丢来源信息。 |
| `agentDetail.skillsDetailPlaceholder` 的值改了 | 原值有一句「「从本机导入」下拉框同在后续补上」，本票兑现后那句话会变成谎言（D9 文档同步条：不留说谎的注释 / 文案）。只删那一句，其余不变。 |
| 未改动 | 未碰 `docs/adr/**`、`CONTEXT.md`、`.github/**`、`scripts/**`、`app/alibot/{service,skills,mcp,db}.py`；未改 `SCHEMA_VERSION`、迁移链、装配层、唤醒与预算纪律；`app/tests/test_skill_import.py` / `test_skills.py` 一字未改；`app/README.md` 的详情页小节仍写「没有页签」（那是 #280 骨架票的文档同步面，不在本票 Ownership）。 |
