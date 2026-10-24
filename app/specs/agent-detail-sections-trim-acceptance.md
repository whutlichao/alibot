# 验收取证：Agent 详情页收敛为四处区块（统计 / 系统提示 / 配置 / 全局 MCP）

**票型**：实施票（前端 UI 调整 + 测试同步 + 文档同步）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：本票**没有**对应的 GitHub issue，沿用 `app/specs/agent-detail-fullpage-acceptance.md` 的先例
用描述性命名（`agent-detail-sections-trim-*`）。
**base**：`83805a1`（= 派活时的 worktree HEAD）｜**branch**：`agent-detail-sections-trim`
**采集时间**：2026-10-24｜**环境**：macOS（aarch64），Node（`node --test`，无需 npm 依赖），
Python 3.11.13（`/Users/apple/orca/alibot/.venv`）
**改动面**：`app/alibot/studio/{app.js,i18n.js}`、`app/tests/agent_detail_ui.test.cjs`、
`app/README.md`、`app/specs/agent-detail-fullpage-acceptance.md`（追加一节）、本文件。
**后端零改动**：`app/alibot/*.py` 一行未动，schema、迁移链与 HTTP 契约不变。

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。红证据在**隔离的基线副本**上
取得（`scripts/red-evidence.sh` 用 `git archive` 导出基线，只换测试不换产品代码），**不动**本 worktree 的工作区。

---

## 1. 用户反馈 → 落点 → 读数

需求来源是本轮用户原话（六条），优先级高于
`app/specs/agent-detail-fullpage-acceptance.md` 里 2026-10-22 那一版六区块验收表；那一版的历史读数
原样保留，该文件末尾追加的「后续修订」一节记录本轮取代关系。

| # | 用户原话 | 落点 | 读数 |
| --- | --- | --- | --- |
| 1 | 「事实」区第三行的「模型标识」删掉 | `agentFactsSection()` 删掉末尾那个 `kvRow(..., 'agent-overview-model')`（连同解释它的注释）；四个数字的 `stat-grid` 原样 | 用例「统计区块的四个数字与后端读数一致」：`byOdId('agent-overview-model') === null`，且统计区子树文本不含 `ollama:qwen2.5` |
| 2 | 「当前状态」整块删掉（含「未启用原因」） | 删 `agentStatusSection()` 函数本体与它在 `agentDetailNodes()` 里的位置；`agent-detail-section-status` / `agent-detail-status-value` / `agent-detail-enabled-reason` 三个标识随节点消失。`statusOf()` / `statusLabel()` **保留**（还有 8 处别的调用者：左栏行、名册、成员面板、详情页头部状态点） | 用例「成功率没有样本时显示「—」」：详情页文本不含「该 Agent 的模型配置已停用」；`grep` 三个标识在 `app/` 下 0 命中。成员名册的 `.dock-member-reason` 未被触碰（`git diff 83805a1 -- app/alibot/studio/app.js` 里 `dockMemberReason` 相关行 0 处） |
| 3 | 「配置」顶部那句草稿 / 整份提交提示删掉 | `agentConfigSection()` 删掉 `section.appendChild(el('div', 'hint', t('agentDetail.configNote')))` 一行；字段、暂停 / 恢复、远端告知块、provider 切换一行未动 | 新用例「配置区块不再有「改动先留在草稿里」那句提示」：标题下第一块不是 `.hint`，而是第一个字段；既有「就地编辑」「暂停 / 恢复」「远端告知块搬进配置区」用例全部原样通过 |
| 4 | 「运行时」整块删掉 | 删 `agentRuntimeSection()` 与只被它使用的 `agentConsentLabel()`；`agent-detail-section-runtime` / `-model` / `-provider` / `-remote` / `-consent` 随之消失 | `grep` 这五个标识 + `agentConsentLabel` 在 `app/` 下 0 命中；删掉的「运行时只读性」旧用例（它断言的是已删除的区块） |
| 5 | 系统提示放到「事实」下面 | `agentDetailNodes()` 里 `agentPromptSection(agent)` 移到 facts 之后、config 之前；区块本体（`agent-detail-section-prompt`、`textarea#agentFieldPrompt`、`data-agent-field="system_prompt"`）未改一字 | 用例「所有区块在同一页纵向铺开」：`#adBody` 子树 `data-od-id` 序为 `facts → prompt → config → global-mcp`；并额外断言 `agent-detail-prompt`（textarea 自己的标识）在 DOM 顺序上夹在 facts 与 config 之间 |
| 6 | 「事实」标题改成「统计」 | `i18n.js` `agentDetail.sectionFacts`：`事实` → `统计`，`Facts` → `Stats`（**key 名不变**） | 用例：区块标题文本等于该词条在两份 catalog 里的取值（不写死字面量） |

### 随区块删除的孤儿词条

逐个 `grep` 确认无代码 / 测试消费者后，从 `zh-CN` 与 `en` 两份 catalog **同步删除** 16 个：

| 词条 | 原消费者（已随区块删除） |
| --- | --- |
| `agentDetail.sectionStatus` / `kvStatus` / `kvReason` | `agentStatusSection()` |
| `agentDetail.sectionRuntime` / `runtimeNote` / `kvModel` / `kvProvider` / `kvRemote` / `remoteYes` / `remoteNo` / `kvConsent` / `consentAck` / `consentPending` / `consentNotNeeded` | `agentRuntimeSection()`、`agentConsentLabel()`，以及 facts 里的模型标识行 |
| `agentDetail.configNote` | `agentConfigSection()` 顶部那句提示 |
| `agentDetail.emptyField` | 删运行时与 `agentConsentLabel()` 后**只剩**这两处消费者，因此一并删除（票面「不再被任何代码 / 测试引用的词条同步删除」的同一条规则；已逐个 grep 确认） |

保留的词条：`sectionFacts`（键名不变、值改成「统计」）、`sectionConfig`、`kvPrompt`、`pause` / `resume` /
`pauseNote`、`save` / `discard` / `unsaved`、`globalMcp*` 与全部 `agent.*` 字段词条。

### 注释同步（不留说谎的注释）

- `app.js` 的「Agent 详情」区块头：`所有区块…：事实 / 当前状态 / 配置 / 运行时 / 全局 MCP / 系统提示`
  → `统计 / 系统提示 / 配置 / 全局 MCP`；原「运行时**只**呈现 `agent_config` 里真实存在的字段」一段改成
  「模型标识 / 提供商 / 是否远端 / 远端数据流告知不再在详情页重复一份只读读数」。
- `i18n.js` 两份 catalog 的头注释同源改写（`事实的每个数字` → `统计的每个数字`，并说明模型标识 / 提供商
  的可编辑那一份长在「配置」区块里）。
- `agentFactsSection()` 的 jsdoc：`事实：四个数字 + 模型标识` → `统计：四个数字`。

### 没有动 `app.css` 的理由

删掉的两个区块用的是通用类（`.d-sec` / `.kv` / `.hint`），这两个类在详情页其余区块与预算面板里仍在用；
`grep 'agent-detail\|overview-model\|section-runtime\|section-status' app/alibot/studio/app.css` 只命中一行注释，
没有任何选择器按被删的 `data-od-id` 收样式。区块是 `.d-sec` 兄弟节点，移除后不留空隙，因此本票不改 CSS。

---

## 2. 门禁与测试读数

```sh
# 窄门禁（票面指定的选择器）
bash scripts/check.sh --studio
```

| 步骤 | 读数 |
| --- | --- |
| pytest（Studio 窄档：`test_approvals_http` + `test_docs_vocabulary_guard` + `test_test_layering_guard`） | `20 passed in 2.57s` |
| `ruff check` | `All checks passed!` |
| `ruff format --check` | 通过（无输出） |
| `mypy`（仓库根） | `Success: no issues found in 123 source files` |
| `pyright app/alibot app/tests` | `0 errors, 0 warnings, 0 informations` |
| `node --test app/tests/*.test.cjs`（全量前端用例 + Studio 静态守卫） | `tests 284 / pass 284 / fail 0` |

```sh
# 本票的目标文件单独再跑一次
node --test app/tests/agent_detail_ui.test.cjs
# → tests 31 / pass 31 / fail 0 / duration_ms 259.8
```

环境备注：本 worktree 出发时**没有** `.venv`（`check.sh` 的 pyright 因此找不到依赖，先报 259 个
`reportMissingImports` 假红）。按 `scripts/check.sh` 文件头给的接法建了指向主仓的符号链接
（`ln -sfn "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.venv" .venv`），
`.gitignore` 的 `.venv/` 覆盖它，因此 `git status --porcelain` 仍然干净。

### 测试同步的真实性（没有为了绿而放宽）

- 「所有区块在同一页纵向铺开」：断言从六项缩到**四项**，并新增一条顺序断言（系统提示控件夹在
  统计与配置之间）——不是把旧断言删掉就完事。
- 「统计区块…」：旧断言 `kvKeys(...)[0] === kvModel` 换成**两条**新断言（无 `agent-overview-model`、
  统计区不重复模型标识），并加了「标题取词条值」。
- 「成功率没有样本」：把「详情页出现未启用原因」的旧断言反过来，断言它**不出现**。
- 删掉的只有「运行时区块只呈现只读字段」这一条——它断言的区块已整体删除，留着只能测一个不存在的节点。
- 词条用例：16 个已删键在两份 catalog 里断言为 `undefined`（与既有「已删页签词条」同一种写法）。

---

## 3. 红 → 绿证据

```sh
bash scripts/red-evidence.sh 83805a1 app/tests/agent_detail_ui.test.cjs
```

- 基线：`83805a1`（本票 base）
- 基线 `app.js` md5：`47475c509fd4cbb36e2e2a72e21c2a31`
- 证据目录：`/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red.qpF677`
- 读数：**31 tests / 26 pass / 5 fail**（红）

红的正是本票要改的五处：

| 红掉的用例 | 为什么在基线红 |
| --- | --- |
| 所有区块在同一页纵向铺开：统计 / 系统提示 / 配置 / 全局 MCP | 基线是六区块、系统提示在最后 |
| 配置区块不再有「改动先留在草稿里」那句提示 | 基线配置区标题下第一个子节点就是那句 `.hint` |
| 统计区块的四个数字与后端读数一致，每个数字都写明来源 | 基线有 `agent-overview-model`，且标题是「事实」 |
| 成功率没有样本时显示「—」，不编一个 0% | 基线详情页显示未启用原因 |
| 本票新增 / 沿用的文案在两份词条里都取到值 | 基线的两份 catalog 里仍有 16 个已删键 |

同一份测试在当前工作区（改动后）跑：**31 / 31 通过**。红证据只换测试不换产品代码，因此它证明的是
「这些断言确实咬住了旧实现」，而不是「新实现被测试倒推着写」。

## 4. 双轴评审

按 `/Users/apple/.pi/agent/skills/code-review/SKILL.md` 对 `git diff 83805a1...HEAD` 跑两个**并行**子代理
（互不共享上下文），结论并列如下。两轴均无 P0 / P1，各有一条指向同一处 scope 边界
（`index.html` 注释），经 coordinator 裁定后**本票内**修正。

### Standards 轴（`docs/agents/review-rules.md` + AGENTS.md + `alibot-design-system/SKILL.md` + smell 基线）

| 发现 | 级别 | 处置 |
| --- | --- | --- |
| `app/alibot/studio/index.html:311-317` 注释仍写「事实 / 当前状态 / 配置 / 运行时 / 全局 MCP / 系统提示」并称「运行时**只**呈现 `agent_config` 里真实存在的字段」 | 硬违规（review-rules 判断规则 6：注释必须与实现一致） | **已修**：经 coordinator 裁定「同一处事实修正，不是扩 scope」，把这 5 行注释改成四处区块并删掉「运行时只呈现…」那句；节点结构、id、属性一律未动（`#viewAgent` / `#adBody` / `#adSaveBar` 等原样） |
| `app/README.md` 原「不再重复呈现…远端数据流告知」易读成「远端告知已删」，实际 `agentRemoteNoticeNode()` 仍在「配置」区块渲染一份 | 建议 | **已修**：改成「详情页不再有只读的『运行时』与『当前状态』两节……切到远端 provider 时的数据流告知块 `agent-edit-remote-notice` 也在那一节里」 |
| `agentDetail.kvPrompt` 键名带 `kv` 但实为区块标题 | 判断项 | **保留**：改名要动两份 catalog 与用例，票面明确「不做顺手重构」；键名本身不在本轮反馈里 |
| 核查结论：无死代码（`kvRow` / `statCell` / `statusOf` / `statusLabel` 仍被他处调用）；16 个词条两份 catalog 删净且无误删；三件套无裸中文；`app.css` 不改合理（无按被删 `data-od-id` 收样式的选择器，`.d-sec` 兄弟节点移除后不留空隙）；改过的注释与实现一致 | — | 记录 |

### Spec 轴（票面「用户原话 + Change + Constraints + Observable acceptance」）

| 发现 | 级别 | 处置 |
| --- | --- | --- |
| `index.html` 注释：Change 6 引用的正是同一句，但 Target 未含 `index.html` | P2 | **已修**（同 Standards 轴，coordinator 裁定）；这也正是票面「不许留下说谎的注释」那条的同一事实 |
| `agent_detail_ui.test.cjs` 文件头注释仍列旧六区块（事实 / 状态 / 运行时），与用例名矛盾 | P2 | **已修**：改成「四处区块（统计 / 系统提示 / 配置 / 全局 MCP），系统提示紧贴统计之下」 |
| `kvKeys()` / `kvValues()` 随旧 `kvModel` 断言删除后成死代码 | P2 | **已修**：两个 helper 删除 |
| 验收正本 Answer 仍「待回填」 | P2 | **已修**：即本节与 §5 |
| 未核验：子代理无 shell，diff 规模 / 无关区块未直属核查 | — | **已补**：`check.sh --studio` 与 `node --test` 读数见 §2；`git diff --stat` 见 §5 |

### 已知噪声（reviewer 可能看到的工具告警）

pi-lens 会对 `app/alibot/studio/i18n.js` 的 `applyStaticI18n()`（HEAD `:1653` / `:1659`）报两条风格
告警（negation test 反转 / `forEach` 改 `for...of`）。这两行在基线 `83805a1:app/alibot/studio/i18n.js:1683`
就已**逐字存在**，本票所有 hunk 都落在 `I18N_CATALOGS` 里，没有碰它们；按票面「不做顺手重构」与
`AGENTS.md` 的「pi-lens 报告可能错位 / 环境差异不是代码问题」两条，**记录不改**。

## 5. Answer

**结论**：Agent 详情页收敛为四处区块（统计 → 系统提示 → 配置 → 全局 MCP），六条用户反馈逐条落地；
测试同步到新形状且红证据证明它们咬住旧实现；后端零改动、行为零改动。门禁全绿，双轴评审无 P0/P1。

| 项 | 值 |
| --- | --- |
| 分支 | `agent-detail-sections-trim` |
| 本票 base | `83805a1` |
| 提交 | 见 `git log 83805a1..HEAD --oneline`（实现一个提交 + 本文件） |
| PR | 见 PR 链接（本节在该提交之后回填） |
| PR body | 见该 PR 描述（`--body-file` 用的临时文件路径：`/tmp/agent-detail-sections-trim-pr-body.md`；票面 Ownership 限定改动文件，因此**不**把 body 副本放进仓库，与 `app/specs/*-pr-body.md` 的既有先例不同，理由记在这里） |
| diff 规模 | `git diff 83805a1 --stat`：6 个文件，约 140 行增 / 150 行删（与本票范围相称，无整文件重排） |
| 改动文件 | `app/alibot/studio/{app.js,i18n.js,index.html(仅 5 行注释)}`、`app/tests/agent_detail_ui.test.cjs`、`app/README.md`、`app/specs/agent-detail-fullpage-acceptance.md`、本文件 |

**遗留 / 已知限制**

- 详情页不再显示 `enabled_reason`：可读性仍在 API（`GET /api/agents/{id}` 照旧返回）与成员名册里那处小字。
  若以后有人要在详情页看「为什么没启用」，需要另立票在别处给出口。
- 本轮未在真实浏览器里做像素级核对（本仓没有浏览器测试框架；纯区块增删、`.d-sec` 兄弟节点，
  CSS 未改）。若 coordinator 要视觉读数，可另跑 ego-browser 手工核对一次。
