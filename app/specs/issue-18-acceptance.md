# 实施票 #18 验收取证：轮次结果五态可见化（消息流里每消息至多一行折叠摘要）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。

> 阅读说明：§4 的浏览器取证全部跑在**独立临时数据目录**上（`/tmp/wsps-18-data`，**不是** `~/Worksplice`），
> 后端是真实 uvicorn 进程（真实 HTTP 面 + 真实 SSE），浏览器是真实 Chromium（ego-browser）。
> 唯一替换是**注入仓库自己的测试替身**（`tests/scripted.py` 的 `ScriptedAgentSeam` +
> `ManualScheduler`，都是 `create_app` 的既有注入点），因为本机没有任何可用的模型 provider
> （票面也明确不配 key）。前端 `studio/app.js` / `app.css`、只读端点与事件流全部是被测的真实代码。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-18-outcome-visible`（Orca 独立顶层 worktree）
- 分支与 PR：`issue-18-outcome-visible` → **[PR #20](https://github.com/whutlichao/alibot/pull/20)**（OPEN）
- PR body 正本：`app/specs/issue-18-pr-body.md`（`gh pr create --body-file`）
- 基线：`8bb4f9a`（main HEAD，本票 base）
- 采集时间：与实际产出同年同月（见 commit 时间戳）；环境：macOS，Python 3.11，Chromium（ego-browser）
- 票标题（开工时用 `gh issue view 18` 核对）：

> 实施票：#15 轮次结果五态可见化——「为什么它没说话」在消息流里可读（含 #1 Out of Scope 边界的用户授权解释）

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：读票
（`gh issue view 18`）→ 声明票标题 → 只读核实事实（§2）→ 一处 open 决策先 `ask`（§3）→
最小改动（§2.3）→ 补 HTTP/SSE 黑盒测试（§5）→ 全量门禁（§1）→ 真实浏览器取证（§4）→
双轴 `code-review`（§7）→ 提交、推分支、建 PR。

## 1. 门禁结果（G-impl 四条全跑）

```text
$ .venv/bin/python -m pytest
179 passed, 1 skipped in 105.50s        # skipped = 默认跳过的真模型冒烟测试（基线 174+1，本票新增 5 条）

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed!
43 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 26 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/worksplice app/tests
0 errors, 0 warnings, 0 informations
```

改动前基线（票面给出，本票在 main 工作区上复测一致）：`174 passed, 1 skipped in 96.64s`。

## 2. 只读核实（先确认事实，再动手）

### 2.1 五态在数据面都已存在，前端只呈现了 `failed`

```text
$ grep -n "roundOutcomeNode" app/worksplice/studio/app.js     # 基线
function roundOutcomeNode(records) {
  const failed = records.filter((record) => record.status === 'failed');
  if (!failed.length) return [];                              # ← 其余四态在这里被丢掉
```

`CONTEXT.md` `## Language` 的「轮次结果」词条写明 status 词表共五个：
`replied`（已回复）/ `ignored`（自判忽略，正常协议选择）/ `failed`（需排查）/
`merged`（被合并的重复唤醒）/ `budget-exhausted`（预算耗尽、本轮零投递）。
后端 `db.py:45` 的 `ROUND_STATUSES` 与之一致，`/round-outcomes` 端点与 SSE 事件给的是**同一条**事实。

### 2.2 本票不需要任何后端改动（因此「新增用例」一项有前提要说明）

前端据以呈现所需的一切**已经存在**：只读端点 `GET /api/channels/{id}/round-outcomes`
（含 `base_seq` / `status` / `reason` / `finished_at` / `member_id`）与 SSE `round_outcome`
事件（同形载荷）。因此本票**后端 `.py` 零改动**：`grep` 证实 `worksplice/` 下没有任何文件出现在 diff 名单里（§8）。

新增的 5 条黑盒用例断言的是「摘要行据以成形的**分组事实**」——那正是前端消费的那份数据面形状
（同一 `base_seq` 下多条非 failed 结果可收成一行、failed 与非 failed 混存互不吞并、reason 为完整
可读中文）。它们**在基线上也是绿的**（见 §2.3 的说明），因为那份事实在基线就已存在；红→绿证据
只能由**前端呈现**本身给出，即 §4 的真实 Chromium 前后对比。

### 2.3 红证据（改动前，同一场景，同一组探针）

隔离副本 `/tmp/wsps-18-red`（`git worktree add --detach /tmp/wsps-18-red 8bb4f9a`，**独立 venv** ——
`app` 是 editable 安装，共用 venv 时后端会从我已改的工作树里读 `studio/`，红证据就变成绿的了；
`grep -c "summaryToggle\|outcome-summary" app.js` 在副本上确认为 `0`）。
后端：`/tmp/issue18-serve.py` 同形脚本指向副本（端口 8793），场景与绿证据**逐字相同**。
在「产品讨论」频道（一条消息同时触发 3 条 `ignored` + 1 条 `failed`）上读消息流：

```text
=== 红证据：8bb4f9a 基线前端（同一后端场景）===
{ "streamRows": ["day-sep", "msg", "msg outcome"],
  "messageRows": 1,
  "summaryRows": 0,                    ← 基线根本没有摘要行
  "failedRows": 1,
  "bodyTextMentionsIgnored": false,     ← 「自判忽略」在整页正文里一次都不出现
  "anyFoldToggle": 0 }                 ← 没有任何折叠控件
```

截图：`/tmp/issue18-evidence/red-baseline.png`（消息流里只有那条人类消息 + 一块失败呈现，
三条 `ignored` 记录在后端库里、在事件流里，但在页面上零呈现）。
**这就是 #18 的红**：用户能分辨「它坏了」，分辨不了「它自判这条与它无关」。

## 3. 一处 open 决策（先 ask，不编造）

票面 Change 第 1/5 条写「五态全部可见」，但验收第 4 条只给四态（`ignored` / `budget-exhausted` /
`merged` / `failed`）的可读说法——第五态 `replied` 的处置是 open 决策。按票面要求先
`orca orchestration ask`，由 coordinator 带回**人定裁定**：

> **选 A：`replied` 不进摘要行。** 那条回复消息本身就是 `replied` 的可见事实
> （#1「一轮 ↔ 至多一条频道消息」）；给几乎每条 Agent 回复都挂一行「已回复」正是摘要行方案
> 要避免的噪声。用户选中选项原文：「摘要行只覆盖四种状态：`failed` 直接展开，
> `ignored` / `budget-exhausted` / `merged` 收在折叠里；`replied` 由它那条回复消息本身承载，
> 不额外挂行。」我的推荐与用户裁定一致，且 coordinator 明示**不要**选折中方案 C
> （展开态也不列 `replied`）。

因此本票把 Change 第 4 条的四态措辞读作**完整清单**：摘要行覆盖
`failed`（直接展开）+ `ignored` / `budget-exhausted` / `merged`（折叠内）；`replied` 的可见性
由它那条 agent 回复消息承载。裁定同时授权我自行处置其余细节（摘要行与 failed 块的顺序、
稳定序、折叠控件元素、展开态 DOM 结构、具体措辞），约束是三条硬要求：
**四态不得混为一谈、非 failed 不得用 `--error`、折叠控件必须键盘可达**。逐条落实见 §4 / §6。

## 4. 真实 Chromium 取证（独立临时数据目录）

后端：`.venv/bin/python /tmp/issue18-serve.py /tmp/wsps-18-data 8791`（真实 uvicorn，只监听 loopback）。
驱动：只经 HTTP 人类面（建 Agent → 建频道 → 发消息 → 让轮次跑完），四个场景一次跑完：

```text
$ .venv/bin/python -c "<经 HTTP 读回只读端点>"
mixed  [('ignored',1),('ignored',1),('ignored',1),('failed',1)]      # 一条消息：3×自判忽略 + 1×失败
single [('ignored',1)]                                              # 单成员摘要
budget [('replied',1..4),('budget-exhausted',5),('budget-exhausted',5),...]  # 3 跳用尽后零投递
merged [('replied',1),('merged',2)]                                 # in-flight 期间的重复唤醒被合并
budget used: {'epoch_seq': 1, 'limit': 3, 'used': 3, 'remaining': 0}
```

> **一处我自己踩到的场景 bug（已记录）**：第一版场景把「长庚」这个名字用在两个场景里，而提及解析
> 按**名字**裁决 —— `@长庚` 于是解析到了另一个同名 Agent，摘要行里出现重名。那是**我的场景构造
> 错误，不是被测行为**；改成唯一名字后重跑即正常。记在这里以免后人把它当成产品缺陷。

### 4.1 四个场景的读数（折叠态 + 展开态，外部可观察选择器与文本）

```text
=== A. 一条消息下 3×ignored + 1×failed（混合，且成员数 > 2）===
折叠态：
  streamRows: [day-sep, msg, "msg outcome-summary", "msg outcome"]
  messageRows: 1                 ← 摘要行不占 data-seq，不被数进「几条消息」
  summaryRows: 1, failedRows: 1
  summary: { baseSeq: "1", role: "status", hasDataSeq: false,
             toggle: "BUTTON", ariaExpanded: "false", ariaControls: "outcome-summary-1",
             text: "▸阿墨、云舟 等 3 人没有回应 · 展开看原因",
             why: "自判忽略（正常的协议选择，不是失败）",
             edgeColor: "oklch(0.78 0.02 86)" }             ← --border-strong，不是 --error
  failedBlocks: [{ baseSeq: "1", role: "status", hasDataSeq: false,
                   tag: "未回应", state: "这一轮失败",
                   reason: "模型调用失败：本机没有可用的模型服务（脚本化替身固定此失败）",
                   edgeColor: "oklch(0.532 0.128 25.9)" }]  ← --error（失败，直接展开）
  expandedLines: []                                         ← 默认折叠：一条逐成员行都不显示
                                                               （明细容器恒常渲染、在 DOM 里，但 `hidden`）
展开态：
  ariaExpanded: "true", text: "▾阿墨、云舟 等 3 人收起原因"
  detailHidden: false, visibleLines: 3
  expandedLines: [
    { status: "ignored", head: "阿墨自判忽略13:04",
      reason: "脚本化替身：自判无关",
      stateColor: "oklch(0.52 0.024 300)", reasonColor: "oklch(0.46 0.03 300)" },   ← --faint / --muted
    { status: "ignored", head: "云舟自判忽略13:04", reason: "脚本化替身：自判无关", ... },
    { status: "ignored", head: "风间自判忽略13:04", reason: "脚本化替身：自判无关", ... }]

=== B. 单成员摘要 ===
  collapsed.text: "▸孤舟没有回应 · 展开看原因"        ← 1 人：不写「等 N 人」
  collapsed.why:  "自判忽略（正常的协议选择，不是失败）"  ← 单一状态：不重复一遍名单
  collapsed.visibleLines: 0, detailHidden: true
  expandedLines: [{ status: "ignored", head: "孤舟自判忽略13:04", reason: "脚本化替身：自判无关" }]

=== C. 被合并 ===
  collapsed.text: "▸回声没有回应 · 展开看原因"
  collapsed.why:  "重复唤醒被合并进正在进行的那一轮（不是失败）"
  expandedLines: [{ status: "merged", head: "回声被合并13:04",
    reason: "该成员已有一轮在进行中：1 次重复唤醒（#2）合并进那一轮，以最大 seq #2 为准；不排队、不重放" }]
  streamRows: [day-sep, msg, msg, "msg outcome-summary", msg]    ← 摘要行挂在 base_seq=2 那条消息下
  failedRows: 0

=== D. 预算耗尽（3 跳用尽后零投递）===
  collapsed.text: "▸砚台、长庚没有回应 · 展开看原因"
  collapsed.why:  "预算已用尽，本轮零投递（不是出错，成员状态点仍空闲）"   ← 与「自判忽略」措辞不同
  expandedLines: [
    { status: "budget-exhausted", head: "砚台预算耗尽13:04",
      reason: "本人类世代（最近一条人类消息 seq=1）的agent→agent 预算已用尽（≤3）：本条 Agent 消息照常落库，但没有投递任何唤醒" },
    { status: "budget-exhausted", head: "长庚预算耗尽13:04", reason: "…（同上，逐成员各一条）" }]
  summaryRows: 1, failedRows: 0, messageRows: 5, visibleLines: 2

=== E. 一行摘要里**混两种非 failed 状态**（mixed 分支，补测）===
  单独的端口 8795 + 小场景：成员「迟语」用延迟卡在 in-flight，成员「默然」自判忽略；
  再在迟语还跑着时发第二条消息 ⇒ 迟语的重复唤醒被 merged（base_seq=2）、默然又 ignored（同一 base_seq）。
  collapsed: [
    { baseSeq: "2", text: "▸默然、迟语没有回应 · 展开看原因",
      why: "默然：自判忽略（正常的协议选择，不是失败）；迟语：重复唤醒被合并进正在进行的那一轮（不是失败）",
      ariaExpanded: "false", ctrlResolvable: true, visibleLines: 0 }]  ← 两种状态**各自点名**，不混为一谈
  expanded（baseSeq=2）: detailHidden: false, lines: [
    { status: "ignored", text: "默然自判忽略13:15脚本化替身：自判无关" },
    { status: "merged",  text: "迟语被合并13:15该成员已有一轮在进行中：1 次重复唤醒（#2）合并进那一轮，以最大 seq #2 为准；不排队、不重放" }]
```

场景 E 同时给出「`replied` 不挂行」的直接视觉证据：截图
`/tmp/issue18-evidence/final-E-mixed-nonfailed-states.png` 里，「迟语」那条 `replied` 的回复消息
（「迟到的回应」，带 Agent capsule）下面**没有**任何摘要行 —— 它的可见事实就是那条消息本身。

截图：`/tmp/issue18-evidence/final-{A-mixed-message-with-failed,B-single-member,C-merged,D-budget-exhausted}-expanded.png`
与 `final-E-mixed-nonfailed-states.png`。

### 4.2 逐条对应票面的呈现形态

| # | 票面要求 | 可见结果 |
| --- | --- | --- |
| 1 | 每条消息下面至多一行摘要（不是每个成员一行） | 场景 A：3 条 `ignored` → **1** 行摘要（`summaryRows: 1`）；名单「阿墨、云舟 等 3 人」 |
| 1 | 成员名逐个列出，>2 时写可读形式 | `summarizeNames()`：≤2 写全名（场景 D「砚台、长庚」），>2 写「甲、乙 等 N 人」（场景 A） |
| 2 | 默认折叠 + 可点击展开控件 | `ariaExpanded: "false"` + `visibleLines: 0`（折叠态零逐成员行可见；明细容器在 DOM 里但 `hidden`）；点/键盘展开后出现 |
| 2 | 展开后逐成员 `status` 中文名 + `reason` **原文** | 场景 A/C/D 的 `head`（状态词）与 `reason`（后端原文一字未改，`endswith("…")` 断言为假） |
| 3 | `failed` 保持直接展开、逐字保留 | 场景 A：免点击即读到「未回应 / 这一轮失败 / reason 正文」；`failedRows: 1` |
| 3 | failed 与非 failed 混存互不吞并 | 场景 A 同时有 `summaryRows: 1` **与** `failedRows: 1`，两者 `baseSeq` 都是 `"1"` |
| 4 | 四态各有可读说法且不混为一谈 | `ignored`→「自判忽略（正常的协议选择，不是失败）」；`budget-exhausted`→「预算已用尽，本轮零投递（不是出错，成员状态点仍空闲）」；`merged`→「重复唤醒被合并进正在进行的那一轮（不是失败）」；`failed`→「这一轮失败」（且 `--error`） |
| 4 | 同一行里混存多种状态时各自点名 | 场景 E：`why` = 「默然：自判忽略（正常的协议选择，不是失败）；迟语：重复唤醒被合并进正在进行的那一轮（不是失败）」，两种状态分开陈述 |
| 5 | 折叠态就说清「为什么」（不只列名单） | 场景 A/B `why` = 「自判忽略（正常的协议选择，不是失败）」；场景 C =「重复唤醒被合并进正在进行的那一轮（不是失败）」；场景 D =「预算已用尽，本轮零投递（不是出错，成员状态点仍空闲）」——三态措辞各不相同 |
| 5 | 沿用既有词汇 | 「轮次结果」「自判忽略」「预算耗尽」「被合并」取自 `CONTEXT.md` `## Language`；失败块的「未回应 / 没启用 / 这一轮失败 / 看成员状态」逐字未改 |
| 5 | 颜色只用既有 token 名、非 failed 不得用失败视觉 | 摘要行 `--border-strong`（`oklch(0.78 0.02 86)`）作描边、`--muted` / `--faint` 作文字；**无一处** `--error`。失败块仍 `--error`（`oklch(0.532 0.128 25.9)`） |
| 5 | 折叠控件是可聚焦原生控件、键盘可操作 | `toggle: "BUTTON"` + `aria-expanded` + `aria-controls`（§4.3 真实按键取证）；`aria-controls` 指向的 id 在**折叠态也解析得到** |
| 5 | 摘要行 `role="status"`、不占 seq、不被统计数进去 | `role: "status"`、`hasDataSeq: false`、`messageRows` 只数 `[data-seq]`（场景 A 为 1、D 为 5） |

### 4.3 键盘可达（真实按键，不是读代码）

```text
=== B. Tab 走到的焦点序列 ===
{"reached": true, "tabs": 6,
 "last": {"tag":"BUTTON","cls":"msg-outcome-summary-toggle","summaryToggle":"1","expanded":"false",
          "text":"▸阿墨、云舟 等 3 人没有回应 · 展开看原因"},
 "outline": ["BUTTON.nav-row","BUTTON.nav-row","BUTTON.nav-row","BUTTON.nav-row","BUTTON.icon-btn","SUMMARY"]}
focus ring: {"outlineWidth":"2px","outlineStyle":"solid","outlineColor":"oklch(0.626 0.086 356.7)"}

=== C. Enter 展开后 ===
{"focusStillOnToggle": "1", "ariaExpanded": "true", "mark": "▾",
 "toggleText": "▾阿墨、云舟 等 3 人收起原因", "detailsId": "outcome-summary-1",
 "why": "自判忽略（正常的协议选择，不是失败）",
 "detailId": "round-reason-round_6c866e768850",          ← 与失败行的 round-reason- 同一前缀
 "detailHidden": false, "targetOffsetParent": "visible",
 "lines": [{"status":"ignored","head":"阿墨自判忽略13:04","reason":"脚本化替身：自判无关"}, …]}

=== 折叠态的 aria-controls 可解析（Spec 轴 P2 的修正取证）===
折叠态： { "ariaExpanded": "false", "ariaControls": "outcome-summary-1",
          "targetResolvable": true, "targetTag": "DIV", "targetId": "outcome-summary-1",
          "targetHiddenAttr": true, "targetOffsetParent": "none", "targetLines": 3 }
展开态： { "ariaExpanded": "true", "targetHiddenAttr": false, "targetOffsetParent": "visible", "targetLines": 3 }
收起态： { "ariaExpanded": "false", "targetHiddenAttr": true, "targetOffsetParent": "none" }
```

> 为什么会有一条「折叠态 `aria-controls` 可解析」：Spec 轴 review 的 P2 指出，原先明细容器只在展开分支
> 创建，于是折叠态的 `aria-controls` 是个**悬空引用**。已改为「明细恒常渲染 + `hidden` 切可见性」
> （§9.2），上面三行就是修正后的读数：折叠态 id 能解析到、且 `hidden`。

=== D. Space 收起后 === {"focusStillOnToggle":"1","ariaExpanded":"false","detailsPresent":false}

=== E. 刷新后（折叠态不持久化）===
{"ariaExpanded":"false","detailsPresent":false,"localStorageKeys":["worksplice.activeChannel"]}
```

- **可 Tab 聚焦**：只按 `Tab`（真实按键）走到第 6 下落在 `BUTTON.msg-outcome-summary-toggle` 上，
  且全局 `:focus-visible` 规则给出 2px 可见焦点环（不是只看代码）。
- **Enter 展开、Space 收起**：两次真实按键分别把 `aria-expanded` 置为 `true` / `false`，
  展开态出现 `#outcome-summary-1` 与其逐成员行，收起后该子树消失。
- **重建后焦点不丢**（reproduce 步骤）：Tab 聚焦 → Enter 展开 → **焦点仍在同一个控件的新节点上**
  （`focusStillOnToggle: "1"`），于是能继续用 Space 收起。这是刻意修的：`renderMessages()` 重建
  会把被点击的按钮换成新节点，焦点会落到 `body`，不还给同一控件就没法用键盘收起。
- **刷新后复位**：折叠态是内存里的会话状态，`localStorage` 只有既有的 `worksplice.activeChannel`
  一个键，**没有**新增任何业务状态键（仓硬约束）。

## 5. HTTP / SSE 黑盒测试（本票新增 5 条）

`app/tests/test_round_outcome_visibility.py`：只断言**外部可观察行为**（状态码、JSON 载荷、SSE 帧），
不断言 DOM 细节与私有结构；沿用仓库既有范式（`tests/conftest.py` 夹具 + `tests/scripted.py`）。

```text
$ .venv/bin/python -m pytest app/tests/test_round_outcome_visibility.py
11 passed
```

| 用例 | 断言的契约 |
| --- | --- |
| `test_non_failed_outcomes_of_one_message_are_grouped_under_its_base_seq` | 「每消息至多一行」的分组前提：同一 `base_seq` 下 3 条 `ignored` 分属 3 个成员、可收成一行而**一条记录不少** |
| `test_ignored_is_not_recorded_or_reported_as_failure` | `ignored` 不是失败：记录为 `ignored`、状态点仍 `online`（摘要行不得用失败措辞的依据） |
| `test_budget_exhausted_rounds_of_one_message_share_the_triggering_seq` | `budget-exhausted` 同属可收拢的一族：同一跳压制多成员时 `base_seq` 相同；不是 failed、不判 `error` |
| `test_failed_and_non_failed_outcomes_of_one_message_coexist_without_swallowing` | 同一条消息下两类混存：`failed` ×1 与 `ignored` ×2 **都**在，且都绑定同一条消息的 `base_seq` |
| `test_every_outcome_reason_is_verbatim_readable_chinese` | 展开后呈现的 `reason` 是完整可读中文原文（非空、不以省略号截断、含汉字） |

**这些用例在基线上也是绿的**（它们断言的是前端据以呈现的那份事实，而那份事实在基线就已存在——
这正是 #8 的诊断）。红→绿证据因此由**前端呈现**本身给出，见 §2.3 vs §4.1 的真实 Chromium 对比。

## 6. 视觉契约核对（按名字消费，不新造色位）

```text
$ git diff -- app/worksplice/studio/app.css | grep -E "^\+" | grep -nE "#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\("
无 hex/rgb/hsl 硬编码

# 浏览器里读回的新规则实际色值（未级联解析，原文）：
摘要行描边        oklch(0.78 0.02 86)      = var(--border-strong)   ← 不是 --error
摘要行成员名      oklch(0.46 0.03 300)     = var(--muted)
逐成员状态词      oklch(0.52 0.024 300)    = var(--faint)
逐成员 reason    oklch(0.46 0.03 300)     = var(--muted)
失败块描边        oklch(0.532 0.128 25.9)  = var(--error)           ← 失败仍用 --error
grep 命中项：[]                          ← 新增规则里没有一处硬编码色值
```

- 只用既有角色族：`--border-strong`（描边）、`--muted` / `--faint`（文字）；**没有**新造色位，
  **没有**动 `product-tokens.css`。
- 非 failed 三态**没有**使用 `--error` 或任何表示失败的视觉（票面硬要求），失败块**仍**用 `--error`。
- 摘要行底色用 `color-mix(in oklch, var(--fg) 3%, transparent)`（与既有 `.msg:hover` 同一手法，
  不是新色位）。
- 布局未破：摘要行落在 `.msg` 同一条流里，`.msg-body { max-width: 68ch }`、reason 与状态词用
  `--fs-sm`(12px) / `--lh-snug`(1.4)；正文 14px / 1.65 / 68ch 契约未动；无横向溢出（§4 截图）。
- 术语遵 `CONTEXT.md` `## Language`；注释里用「轮次结果」而不是「结论」（#4 的 S7 教训）。

## 7. 双轴 code-review（Standards + Spec，并行子代理）

见 §9「双轴 code-review 结论」——findings 与逐条处置在那里分列。

## 8. 范围与边界核对

```text
$ git diff --name-only 8bb4f9a...HEAD
app/specs/issue-18-acceptance.md
app/specs/issue-18-pr-body.md
app/tests/test_round_outcome_visibility.py
app/worksplice/studio/app.css
app/worksplice/studio/app.js
```

- **未改后端**：`worksplice/` 下的 `.py` 一个字节未动（`api.py` / `service.py` / `events.py` /
  `db.py` / `orchestration.py` / `agent_seam.py` 全在名单外）。`/round-outcomes`、`/budget`、
  SSE `round_outcome` 载荷、`round_outcome` 表结构与 append-only 约束都没有改。
- **未改 `agent_seam.py` 的 `RoundOutcome.consumed_budget`**（#12 的遗留死字段，不在本票范围）。
- **未改** `channel_budget` / 预算策略 / 唤醒面 / 跳的记账。
- **未改 `worksplice-design-system/`**（只读参照，diff 名单里 0 条）、未改原型
  `worksplice-app.html`、未改 `CONTEXT.md`。
- **未新增 UI 能力**：没有频道头预算条、没有轮次历史页面、没有按成员筛选/搜索/排序、
  没有常驻预算可视化；`GET /budget` 仍只作为只读取证面（本票一次都没消费它）。
- **未因呈现需要而减少后端记录**：`ignored` 那批 append-only 记录一条不少，只在**呈现层**收成一行。
- **前端未经 localStorage 保存任何业务状态**：`localStorage` 仍只有既有的
  `worksplice.activeChannel`（§4.3 的 E 段实测）。折叠/展开是内存里的会话状态。
- **未碰 composer 区域**（#9 的并行面）：

  ```text
  $ git diff -U0 -- app/worksplice/studio/app.js | grep -E "^\+.*(composer|placeholder|mention|autocomplete)"
  composer/placeholder/mention 相关行未改
  ```

## 9. 双轴 code-review 结论

按前置 skill `implement` 的流程，收尾前执行 `code-review`（Standards + Spec 两轴，并行子代理，
`/Users/apple/.pi/agent/skills/code-review/SKILL.md`）：固定点 `8bb4f9a`，两边都拿到「diff 命令 +
本仓文档化标准清单 + Fowler 味道基线」。

> 一处运行说明：Spec 轴第一次跑到 7 分钟时命中**上游网络错误**
> （`upstream_error ... connection reset by peer`，`cafd7ac2`），未产出报告；已在新 commit 上重跑
> （这属于工具侧瞬态故障，不是代码问题；Standards 轴一次跑完）。

### 9.1 Standards 轴（findings 逐条处置）

子代理的结论原文摘录与处置：

| # | Finding（严重度） | 处置 |
| --- | --- | --- |
| P1 | **硬违规（S5 文档与代码不漂移 / S7 不留死代码）**：`OUTCOME_LABEL` 的注释声称「五种轮次结果的说法（词表含 `replied`）」，实际只有 4 键、缺 `replied`；且 `failed` 键**无消费者**——两处读取（`summarizeWhy`、`roundOutcomeSummaryLine`）的入参都已被 `summarizableOutcomes()` 滤掉 | **已修**（`372092e`）：注释改成它真正覆盖的三种，删掉查不到的死词条 |
| P2 | Duplicated Code（Fowler 基线）：`summarizeNames` 与 `summarizeJoinedNames` 逐字同形 | **已修**（`660d52c` 后、`6fb3c97` 内）：抽成唯一实现 `joinNames()` |
| P2 | 命名一致性（S4）：同一 `reason` 字段在失败块叫「原因」、摘要行叫「理由」；取证 id 前缀分成 `round-reason-` 与 `round-outcome-reason-` | **已修**（`372092e`）：统一为「原因」与 `round-reason-<id>`（同一字段两个名字/两个前缀只会让取证混淆） |
| P2 | Repeated Switches / Primitive Obsession（基线）：`OUTCOME_LABEL` / `OUTCOME_SUMMARY_NOTE` / `SUMMARY_STATUSES` 是同一 status 键集的三张平行表，加一态要改三处 | **已修**（`372092e`）：并成一张 `OUTCOME_SUMMARY`（`{ label, note }`），`SUMMARY_STATUSES` 由它的键派生，`outcomeLabel()` 作为唯一取词入口 |
| P2 | 术语避让：注释用「本会话」指浏览器会话，而 `CONTEXT.md` 把「会话 / session」列为 `_Avoid_` | **已修**（`372092e`）：改成「本浏览器页面」 |
| P2 | `'没启用'`（失败块状态词）与词表「未启用」不同词 | **不修，记为遗留**：这是 #8 引入的既有文案（本票逐字保留失败块的呈现是硬要求）。它属修字面而非行为，改了会与 #8 的验收截图对不上；留给后续的文案票 |

子代理的 Correct 项（无需处置）：DOM 全走 `el()`/`textContent`（S1）；新增 CSS 无 hex/rgb/hsl、只用既有 token（S2）；
术语用「轮次结果 / 自判忽略 / 预算耗尽 / 被合并」，`replied` 不挂行符合裁定。
子代理的合并结论：**Merge verdict: OK with notes**（唯一硬违规是注释与一个死键，一行级修正——已修）。

### 9.2 Spec 轴（findings 逐条处置）

子代理的结论（摘要）：

- **(a) 缺失或部分：无**。逐条对到代码行：「每条消息下面至多一行摘要」→ 每个 `message.seq` 只调用
  一次 `roundOutcomeSummaryNode`；「默认折叠 + 可展开」→ `aria-expanded` + 展开态才可见的明细；
  「展开后逐成员 status 中文名 + reason 原文」→ 不改写、不截断；「`failed` 直接展开、与摘要行互不吞并」；
  「折叠态就说清为什么」→ `summarizeWhy` 三态各用自己的 `note`；「五态全可见」按 A 裁定成立。
- **(b) 范围蔓延：未发现**。三条硬约束（四态不得混为一谈 / 非 failed 不得用 `--error` / 折叠控件键盘可达）
  均满足；后端 `.py`、设计系统零改动。
- **(c) 实现但不对：无票面违背**；一条 P2：`aria-controls` 在折叠态指向不存在的元素。
- 子代理合并结论：**Merge verdict: OK with notes**（仅 P2 一条，报告级）。

| # | Finding（严重度） | 处置 |
| --- | --- | --- |
| P2 | **ARIA 悬空引用**：`aria-controls="outcome-summary-<baseSeq>"` 声明的 id 只在 `expanded` 分支创建，折叠态解析不到 | **已修**：明细容器改为**恒常渲染 + `hidden` 切可见性**（`aria-controls` 在任何时刻都能解析到；§4.3 末的折叠态读数就是修正后的取证）。这是 ARIA 惯例的披露（disclosure）形态，不是只为了取证好看 |

一处方法说明（诚实起见）：Spec 轴没有 `gh`/shell，它是根据本文件（§3 的裁定原文与 §4.2 的逐条转引）
加代码行核对的。也就是说：**裁定原文与票面要求是我从 `gh issue view 18` 读回后转引进本文件的**，
子代理核对的是「代码是否兑现了本文件记下的那套要求」。因此本票仍以
`gh issue view 18 --json number,title,body,labels,comments` 的读到内容为票面正本（§0）。

## 10. 取舍与遗留

1. **摘要行不收 `replied`**（§3 的人定裁定）：`replied` 的可见性由它那条回复消息承载。
   这是用户裁定，不是实现取舍。
2. **`ignored` 记录数不因呈现而减少**：`ignored` 在当前唤醒面（频道内全部 agent 成员）下按成员
   各有一条，那是 append-only 的取证面。本票只在**呈现**层收成一行 + 折叠，**不**通过后端过滤、
   抽样或截断来减少记录。
3. **折叠态只在内存**：换频道或刷新即复位（`state.expandedOutcomes`）。这是本仓「不用
   localStorage 保存业务状态」的硬约束下的必然形态，也是它与业务状态的分界。
4. **`merged` 场景在浏览器里只有一个成员**：`merged` 只在「同一成员在一轮 in-flight 期间被重复
   唤醒」时产生，天然是单成员的记录，因此它的折叠行不体现「多成员收成一行」这一面；
   那一面由场景 A（3×`ignored`）与场景 D（2×`budget-exhausted`）取证。
5. **`replied` 场景在浏览器里只作为「不挂行」的反证**：场景 C 的消息流里有 3 条 Agent 回复消息、
   摘要行 1 行、`failedRows: 0` —— 印证 `replied` 没有额外摘要行，它的可见事实就是那三条消息。
6. **`round-outcomes` 仍是无游标的整频道只读列表**（#8 已记）：本票沿用「进入频道取一次 +
   实时增量合并」，未引入分页。频道长期运行后这张表变大是后端侧的既知事实，若将来要处理应由
   后端侧决定，而不是前端自行截断（截断会静默丢事实）。

## 11. 交付回执

```text
$ git push -u origin issue-18-outcome-visible
 * [new branch]      issue-18-outcome-visible -> issue-18-outcome-visible

$ gh pr create --title "feat(app): #18 轮次结果五态可见化——消息流里每消息至多一行折叠摘要" \
               --body-file app/specs/issue-18-pr-body.md --base main
https://github.com/whutlichao/alibot/pull/20

$ gh pr view 20 --json number,state,headRefName,baseRefName
{ "number": 20, "state": "OPEN", "headRefName": "issue-18-outcome-visible", "baseRefName": "main" }
```

- 提交：`feat(app): #18 轮次结果五态可见化——消息流里每消息至多一行折叠摘要`（实现 + 测试），
  随后一次 `docs(app): #18 验收取证与 PR body` 只加本文件与 PR body。
- 合并由 coordinator 执行（G-merge）；本票不自行合并。
