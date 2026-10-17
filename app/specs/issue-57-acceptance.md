<!-- 本文件是 #57 的验收取证（逐条判据 → 证据）；tracker 上的 issue #57 保持原样，摘要回填为 comment。 -->

# Issue #57 验收取证 —— Agent 旁白 prompt 纪律改写 + 呈现层降档

> 票面：GitHub [#57](https://github.com/whutlichao/alibot/issues/57)（实施票①，Part of #35）。
> 父规格：`app/specs/issue-35-spec.md` —— **D1 判据-4**（只做呈现层降档）、**D4 形态-a**
> （消息级降档、不合并）、**D5**（prompt 纪律改写，最终措辞由本票定稿）、Testing Decisions。
> 术语：`CONTEXT.md` **沉默 (Silence)** / `_Avoid_` **旁白式交代 (Silent Narration)**。

| 项 | 值 |
| --- | --- |
| worktree | `/Users/apple/orca/workspaces/alibot/wt-57` |
| 分支 | `issue-57-impl` |
| 本票 base | `7ab75a1`（`alibot-budget-silence/integration` 的 tip） |
| schema | **未动**：`app/alibot/db.py`、`SCHEMA_VERSION`、迁移链、`MESSAGE_KINDS` 全部零改动（14/15 留给 #53 / #58） |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app/alibot/agent_seam.py` | `_system_prompt` 的回应 rubric 定稿（D5）：删掉自相矛盾的括注，把**沉默**表述成「这一轮不产出正文」，并把括注 / 旁白 / 说明 / 道歉逐个点名为「不是沉默」；「被点名必须回应」与「未点名且话题无关才轮到沉默」写在同一段里。 |
| `app/alibot/studio/app.js` | 新增旁白的**形态判据** `narrationOf`（`NARRATION_MAX_LENGTH = 24`）、一行低权重的 `narrationNode`、`messageNode` 的前置分支、旁白折叠态与点击处理；`groupOutcomes` 换成 `applyOutcomes`（同一批后端行建**两份**索引：`base_seq` 分组 + `reply_seq` 直取）。 |
| `app/alibot/studio/app.css` | 新增 `.msg.narration` 一族的低权重形态（只消费既有色位，不硬编码颜色）。 |
| `app/alibot/studio/i18n.js` | 新增 5 个键（zh / en 同键集）：`narration.tag` / `narration.expand` / `narration.collapse` / `narration.toggleAria` / `narration.reasonMissing`。 |
| `app/tests/test_agent_round.py` | 新增 3 条后端用例（prompt 措辞定稿 ×2、数据面不变 ×1）。 |
| `app/tests/narration_ui.test.cjs` | 新增 11 条 DOM 用例（历史/未来旁白、一行、展开、键盘/可访问性、普通回复不误降档、形态-a、词条、CSS 低权重）。 |

**未改**：`app/alibot/db.py`（含 `SCHEMA_VERSION` 与迁移链）、`app/alibot/orchestration.py`、
`app/alibot/service.py`、`app/alibot/api.py`、`app/alibot/i18n.py`、`index.html`、
`docs/adr/**`、`CONTEXT.md`、以及**未读口径 / 唤醒 / 预算 / 消息 kind / `replied`-`ignored` 判据**。

---

## 1. 判据 → 证据（逐条）

| # | 票面判据 | 证据（命令 + 关键读数） | 结论 |
| --- | --- | --- | --- |
| 1 | 系统提示不再示范「空字符串之外的简短说明」作为沉默方式；沉默被表述为**不产出正文**，并写清「被点名必须回应」与「正常忽略」的边界 | `cd app && ../.venv/bin/python -m pytest tests/test_agent_round.py -q -k silence` ⇒ **2 passed**；断言片段：`"空字符串" not in prompt`、`"只回一个空字符串之外的简短说明不算回应" not in prompt`、`"不产出正文" in prompt`、`括注/旁白/说明/道歉` 四个词逐个在 prompt 里、`被点名必须回应` 与 `与你无关` 与 `不产出正文` 在**同一段** rubric 里（`len(rubric) == 1`） | ✅ |
| 2 | 历史与未来旁白式交代均按自己的 `seq` 保留原 message 行，一行、低权重、可展开看关联轮次结果 `reason` | `node --test app/tests/narration_ui.test.cjs` ⇒ **11 passed**。历史：`rowAt(2).dataset.seq === 2` + `.msg.narration` + 无 `.msg-text` 段落（正文在 `.msg-head` 同一行）。未来：`handleStreamEvent("ch-1", {data}, "message")` 真到达路径后同样降档。展开：`aria-controls` 指向的节点读到 `round_outcome.reason` 原文（`模型自主判断需要回应`）；低权重由 CSS 用例守：`.msg-narration-text` 用 `var(--muted)`、标签栏用 `var(--faint)`、无 hex/rgb/hsl | ✅ |
| 3 | 普通实质回复仍按普通 Agent 消息呈现；失效方向**宁可少降档，不可误吞真实回复** | 同上族：普通长回复 `!row.classList.contains("narration")` 且仍有 1 个 `.msg-text`、无旁白展开控件。7 种「拿不准」形态（超长括注 / 多行括注 / 中途闭合的第二对括号 / `【重要】` / `《规范》` / 只左括号 / 只右括号）逐条断言**不降档**；人类消息整条括注也不降档 | ✅ |
| 4 | 不引入后端正则、措辞表或哨兵；不改后端 `replied`/`ignored` 判据、消息 kind、未读口径、唤醒与预算 | 后端只改了 `_system_prompt` 的**文案**（一个 string literal，无新分支、无判据）。`git diff --stat` 里 `orchestration.py` / `db.py` / `service.py` / `api.py` **零行改动**。数据面另有正向取证：`test_a_narration_body_is_still_an_ordinary_agent_message` ⇒ 旁白式正文仍是 `seq=2` / `kind="agent"` / `status="replied"` / `reply_seq=2`（未读与唤醒的污染是 #35 登记的**已知代价**，本条只证「没动」） | ✅ |
| 5 | 不跨 `message` / `round_outcome` 数据面拼装、不并入摘要行、不采用仅改 `msg-tag` 的形态 | 旁白行 `role` 保持默认（摘要行是 `role="status"`）、保留 `data-seq`；`ignored` 的摘要行仍在自己的 `base_seq=1` 锚点上、其文本**不含**旁白正文；旁白行除了标签还多出正文与可展开结论（不是只换 tag）。关联 `reason` 按 `round_outcome.reply_seq` 直取本条消息**自己那一轮**的结论 | ✅ |
| 6 | 通过既有前端用例族验证历史/未来消息、一行呈现、展开/键盘/可访问性、普通回复不误降档；不新开 seam | 新增 `app/tests/narration_ui.test.cjs` 沿用 `agent_detail_ui.test.cjs` / `search_ui.test.cjs` 的同一套驱动（`vm` 装载 `i18n.js` + `app.js`、`fetch` 替身、DOM 替身、`bindEvents()` + 真 DOM 事件派发）；seam 数量不变（无新端点、无新后端接口、不新建摘要实现）。既有 5 个前端用例文件（105 条）全过，加新文件共 **116 passed** | ✅ |

---

## 2. D5 定稿的 prompt 措辞（原文）

`app/alibot/agent_seam.py::_system_prompt` 里那一段（与成员 base prompt、`role`、任务上下文以空行拼接）：

```text
你在一个多成员频道里协作者的身份是「成员」，不是人类。回应纪律：被个人提及（@你的名字）时必须回应，而且正文要有实质内容；话题明确与你无关、或你没有被点名时，正常的做法是保持沉默——沉默指的是这一轮不产出正文，什么都不写，也不用括注、旁白、说明或道歉去交代「本轮消息与我无关」：那仍然是一条正文，属于说了等于没说；其余情况有话要说就直接写成正文，不要把要说的话整体包在括注里。
```

改掉的三件事（对照 D5「现行提示自相矛盾」）：

1. **不再用「回什么」描述沉默**。旧句的括注把「回一个空字符串之外的简短说明」示范成合规形态，
   正是「（本轮消息与我无关，无需回应）」的模板；新措辞里**一个「空字符串」字都没有**（用例钉住
   `"空字符串" not in prompt`），不再要求任何形态的「回」。
2. **沉默只有一个形态：这一轮不产出正文**，并且把它的反面**逐个点名**（括注 / 旁白 / 说明 / 道歉）。
   点名的是**形态**而不是措辞：不论模型写「（本轮无关）」还是「（没什么要补充的）」，都在被排除的
   那一类里，因此不构成措辞表。
3. **边界写在同一段**：被点名必须回应（且要有实质内容）／未点名且话题明确无关才轮到沉默／其余情况
   有话直说、不要把话整体包进括注。分开写会留下「这一轮该不该说话」的空档。

`app/tests/scripted.py` 复用同一个 `_system_prompt`，因此上述断言取的是**真的会被送进模型的那段
文本**（与 `test_auto_claim.py` 既有 prompt 断言的取证方式一致），不是手抄副本。

---

## 3. 呈现判据：规则、理由与失效方向

### 3.1 规则（只看形态，不看措辞）

一条消息被判定为旁白式交代，当且仅当**全部**成立：

1. `kind === 'agent'` 且 `seq` 是正整数（**已落库**的 Agent 消息；乐观发送项与失败项没有 seq，不参与）；
2. 正文 `trim()` 后非空，按**码点**计长度 **≤ 24**；
3. 正文只有一行（不含 `\r` / `\n`）——多行的括注更可能是真内容（清单、引用）；
4. 正文首字符是圆括号开号（`（` 或 `(`），末字符是**同一对**的闭号；
5. 从首字符扫到末字符，括号深度**只在最后一个字符**回到 0（即「整条被一对括住」；
   `（一）（二）` 在中途就回 0，不成立）。

**为什么是这五条**：它们都是**形态**性质（长度、行数、括号配对与嵌套深度、消息来源），
不含任何字面量词表、正则白名单或语言相关的措辞——换语言、换措辞、换模型都不会让判据失效，
因此不违反「不引入后端正则、措辞表或哨兵」。长度上限 24 的依据：整条括注式交代是**一句自我
说明**（「（本轮消息与我无关，无需回应）」15 个码点、「（这条不需要我发言，我先旁听）」16 个码点），
超过这个长度的括注更可能是真的在说一件事（引用、长句说明）。

### 3.2 失效方向（**宁可少降档，不可误吞真实回复**）

判据是**纯保守**的：任何一条不成立就按普通 Agent 消息呈现。因此以下形态都被如实渲染成普通回复
（`app/tests/narration_ui.test.cjs` 逐条钉住）：超长括注、多行括注、中途闭合的第二对括号、
`【重要】…`、`《…》`、只有一侧括号、以及人类消息。方向性说明：

- **误降档的代价**是有限的**呈现**代价，不是内容丢失：降档后正文仍**完整可见**（同一行里原文照排）、
  作者名与时间仍在、可展开、且它仍然是这条消息自己的行。因此即使把一句「（同意）」误判成旁白，
  信息也没有被吞掉。
- **漏降档**的代价只是「这一条读起来仍像普通回复」——这正是本票愿意承担的一侧。
- 关联轮次结果查不到（`/round-outcomes` 失败 → `state.replyOutcomes` 为空）时，**不编造理由**：
  展开块显示「这一轮的结论暂时读不到。」（`narration.reasonMissing`）。

### 3.3 渲染形态（D4 形态-a 的三条硬约束如何落地）

| 约束 | 落地 |
| --- | --- |
| 它**仍是消息**，保留自己的 `seq` | `article.msg.narration` 带 `data-seq`，`role` 保持默认（不是摘要行的 `role="status"`），有头像 / 作者名 / 时间 |
| **一行** | 正文 `.msg-narration-text` 与作者名、标签、展开控件、时间同处 `.msg-head` 这一行；折叠态**没有** `.msg-text` 段落 |
| **低权重** | 正文与作者名降到 `--muted`（普通正文是 `--fg`）、标签与展开控件用 `--faint`、头像降 `sm` 档、整行 `--fs-sm`；全部按名字消费既有色位 |
| **可展开看关联 `reason`** | 展开控件是原生 `<button>`（Tab / Enter / Space 天然可用）+ `aria-expanded` + `aria-controls` 恒可解析；展开内容是该轮 `round_outcome.reason` 原文（`data-od-id="round-reason-<id>"`，与失败行同一取证前缀） |
| 不并入摘要行 / 不跨数据面拼装 | 摘要行与失败行不动；旁白行的 reason 按 `round_outcome.reply_seq == message.seq` 直取**它自己那一轮**，不做跨行聚合、不改摘要的消息计数与无障碍语义 |
| 不是只改 `msg-tag` | 同一个 `.msg` 家族里它是**独立形态**（`.msg.narration`）：正文位置、展开块、折叠态都是新的，标签只是其中一个元素 |

折叠态是**会话内呈现状态**（`state.expandedNarrations`，键 = 旁白自己的 `seq`；与摘要行的
`state.expandedOutcomes`（键 = **触发**消息的 `base_seq`）分开，否则两者会撞号）。换 target 时清空。

`groupOutcomes` 改名为 `applyOutcomes` 的原因：旁白的 reason 需要 `base_seq → 成员` 与
`reply_seq → 记录` **两份**索引，两份必须从同一批后端行建、在同一处换，否则会各自漂
（`refreshOutcomes` / 载入失败 / `onStreamOutcome` 增量三处都要同时更新）。

---

## 4. 数据面：一个字都没动（登记为 #35 的已知代价）

| 项 | 读数 |
| --- | --- |
| `app/alibot/db.py` / `SCHEMA_VERSION` / 迁移链 / `MESSAGE_KINDS` | `git diff --stat` 中零行改动（schema 号 14 归 #53、15 归 #58） |
| `replied` / `ignored` 判据 | `orchestration.py` 零改动；`test_a_narration_body_is_still_an_ordinary_agent_message` 证旁白正文仍走 `replied` + 普通 `agent` 消息 |
| 未读 / 唤醒 / 预算 | 未动。旁白仍占 `seq`、计未读、照旧唤醒并消耗预算——**这是 #35 登记的已知代价，不是本票漏做** |
| `app/alibot/i18n.py` | 未动：本票新增的是**界面文案**（进 `studio/i18n.js` 的两份取值），不是后端生成的自然语言；提示文案是**模型侧**措辞，沿用 `_system_prompt` 里既有中文 literal 的先例（不进任何词条表） |

---

## 5. 门禁读数

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_agent_round.py -q
35 passed

$ node --test app/tests/narration_ui.test.cjs
tests 11 / pass 11 / fail 0

$ node --test app/tests/search_ui.test.cjs app/tests/agent_detail_ui.test.cjs \
    app/tests/i18n_ui.test.cjs app/tests/rail_identity.test.cjs \
    app/tests/mobile_shell.test.cjs app/tests/narration_ui.test.cjs
tests 116 / pass 116 / fail 0        # 既有 5 个文件 105 条 + 本票新增 11 条

$ .venv/bin/ruff check app
All checks passed!

$ .venv/bin/ruff format --check app
124 files already formatted

$ .venv/bin/python -m mypy          # 仓库根（配置里的 files 相对 cwd 解析）
Success: no issues found in 53 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
0 errors, 0 warnings, 0 informations

$ cd app && env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY ../.venv/bin/python -m pytest -q
554 passed, 2 skipped in 307.16s (0:05:07)
```

**两处环境注记（不是本票的回归）**：

1. `cd app && ../.venv/bin/python -m mypy`（不带参数）在本机会报
   `app/tests: error: Duplicate module named "__main__" (also at "app/alibot")` —— 这是 mypy 把
   `mypy.ini` 里的 `files = app/alibot, app/tests` 按 **cwd** 解析导致的路由假象（cwd=`app` 时同一份
   配置指向了 `app/` 之下）。在仓库根跑同一条检查（或 `cd app && ../.venv/bin/python -m mypy alibot tests`）
   都是 `Success: no issues found in 53 source files`。本票未新增/移动任何 `__main__.py`。
2. 全量 pytest 按既有约定带 `env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY`
   跑（`test_agent_seam_client_lifecycle.py` 的 `InvalidURL: Invalid port: ':1]'` 是本机代理变量的
   基线噪声，与基线同样失败/同样被绕开）。

---

## 6. 相邻缺陷与残留风险（**未修**，登记备查）

| # | 事项 | 处置 |
| --- | --- | --- |
| R1 | 旁白仍占 `seq`、计未读、触发唤醒并消耗预算 | **不修**：#35 D1 明确选「只做呈现层降档」，这是**已登记的已知代价** |
| R2 | 高度括注化的**真实**短回复（如「（同意）」）也会被降档 | 判据侧**有意接受**：降档不隐藏内容（正文完整可见、可展开、仍是消息），而漏降档的代价只是读起来重一点；判据长度上限已压到 24 收紧误判面 |
| R3 | `round_outcome` 的 `reply_seq` 目前只在 `replied` 且真有回复消息时非 `null` | 与既有取证字段同义，未扩语义；若将来 #58 增 `silent` 状态，旁白判据与 reason 查找都不受影响 |
| R4 | 任务线程（dock 第二形态）里的消息走另一套渲染（`.tt-reply-row`） | **不在本票写面**：票面判据说的是消息流的呈现降档；未改动、未降档，登记备查 |
| R5 | `loadWorkspace` 失败路径与换 target 路径都直接清 `state.outcomes` | 已一并改为同步清 `replyOutcomes` / `expandedNarrations`（同一份索引不漂），无行为扩展 |
| R6 | 私信（DM）没有 `round-outcomes` 读面（只有 `/api/channels/{id}/round-outcomes`），因此 DM 里的旁白展开显示「这一轮的结论暂时读不到。」 | **既有不对称**：摘要行与失败行在 DM 里同样不出现；本票不扩后端读面（会动 API 写面），如实降级、不编造理由 |
| R7 | 未做**浏览器截图**取证 | 票面判据全是「呈现形态 / 交互行为」，已由 DOM 用例 + `app.css` 规则断言（低权重只消费既有色位）覆盖；本机未起 live Studio 抓图，登记备查 |

---

## 7. `integration` 合并

```console
$ git log --oneline -1 alibot-budget-silence/integration
7ab75a1 Merge pull request #124 from whutlichao/issue-115b-rail-dot-comment

$ git merge alibot-budget-silence/integration
Already up to date.
```

交付期内 `alibot-budget-silence/integration` **未前进**（仍是本票 base `7ab75a1`），因此无需解冲突；
受影响测试（`test_agent_round.py` + `narration_ui.test.cjs` + 5 个既有前端用例文件）在合并后按
§5 的命令复跑，读数与 §5 一致。
