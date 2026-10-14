# 实施票 #8 验收取证：轮次失败/未启用 reason 在消息流里可见

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。

> 阅读说明：§4 的浏览器取证全部跑在**独立临时数据目录**上（`/tmp/wsps-*-8-data`，**不是** `~/Worksplice`），
> 后端是真实 uvicorn 进程，浏览器是真实 Chromium（ego-browser）。§4.1 是改动前同一场景的红证据，
> §4.2 是改动后的绿证据，两段用同一套探针、同一个场景，可直接对比。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-8-outcome-visible`（Orca 独立顶层 worktree）
- 分支与 PR：`issue-8-outcome-visible` → **[PR #10](https://github.com/whutlichao/alibot/pull/10)**（OPEN）
- PR body 正本：`app/specs/issue-8-pr-body.md`（`gh pr create --body-file`）
- 基线：`origin/main` 的 `25108fb`（#7 合并后）
- 采集时间：与实际产出同年同月（见 commit 时间戳）；环境：macOS，Python 3.11（`uv venv`），Chromium 152
- 票标题（开工时用 `gh issue view 8` 核对）：

> 修复票：轮次失败/未启用 reason 在消息流里不可见（@ 之后「没有任何反应」的真实缺陷）

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：读票
（`gh issue view 8`）→ 声明票标题 → 只读核实事实（§2）→ 取红证据（§4.1）→ 最小改动（§3）→
补 HTTP/SSE 黑盒测试（§5）→ 全量门禁（§1）→ 聚焦自审（§7）→ 提交、推分支、建 PR。
本票是实施票（修复类），门禁 G-impl / G-merge（合并由 coordinator 执行），按要求**不跑**
G-docs / G-spec / G-tickets，也**不做**完整双轴 code-review（影响面可圈定），改做聚焦自审（§7）。

## 1. 门禁结果（全量）

```text
$ .venv/bin/python -m pytest
149 passed, 1 skipped in 94.60s        # skipped = 默认跳过的真模型冒烟测试（基线 143+1，本票新增 6 条）

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 31 files already formatted

$ .venv/bin/python -m mypy             # 配置在仓库根 mypy.ini
Success: no issues found in 24 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
pyright errors 0
```

## 2. 只读核实（先确认 #8 说的都是事实，再动手）

```text
$ gh issue view 8 --json title,body | head
"title": "修复票：轮次失败/未启用 reason 在消息流里不可见（@ 之后「没有任何反应」的真实缺陷）"

# 基线后端事实（默认视图里看不到、但库里与事件流里都在）：
$ curl -s http://127.0.0.1:8798/api/channels/<id>/round-outcomes | jq
[ { "status": "failed", "base_seq": 1,
    "reason": "模型提供商 dashscope 缺少凭证：请先设置环境变量 DASHSCOPE_API_KEY",
    "member_id": "mem_42584173650e", "consumed_budget": false, "merged": false, "reply_seq": null },
  { "status": "failed", "base_seq": 2,
    "reason": "模型调用失败：ConnectionError: Failed to connect to Ollama. …" } ]
```

- 后端**算了、落了、也推了**：`round_outcome` 行带 `status` / `reason` / `base_seq`，且
  `type: round_outcome` 事件与只读端点给出**同一条**事实（§5 有用例断言两者相等）。
- 事故点确认在**前端呈现**：基线的 `onStreamOutcome()` 只把状态点改成 `error`，`reason` 文本
  从未进入 DOM；唯一能看见 `enabled_reason` 的地方是默认隐藏的 dock（`#dock[hidden]`）。
- 前端据以呈现所需的一切**已经存在**：只读端点 `GET /api/channels/{id}/round-outcomes`
  （`base_seq` 过滤参数自 #4 起就在）与 `round_outcome` 事件（载荷同形）。**因此本票不需要
  任何后端改动**，事实形状一个字未动。

## 3. 改动（最小：只补呈现，不重构渲染层）

```text
$ git diff --stat HEAD~2
 app/worksplice/studio/app.js                 | 142 ++++++++++++++++++++++++
 app/worksplice/studio/app.css                |  32 ++++++
 app/tests/test_round_outcome_visibility.py   | 新增
```

`app/worksplice/studio/app.js`（唯一的呈现改动点）：

```diff
 renderMessages()
   for each message
     <MessageRow>
+    if state.outcomes[message.seq]                 # 按 base_seq 绑定到触发它的那条消息
+      for each member with a failed outcome
+        <RoundOutcomeRow>                          # role="status"，无头像、无 data-seq、无重试按钮
+          「<成员名>」[未回应] (没启用|这一轮失败)  <时间>
+          「<成员> 现在是「未启用」，所以这一轮没有回复。后端给出的原因：」   # 仅 offline 时
+          <后端 reason 原文>                       # 只读端点/事件流给的那一句

 onStreamOutcome(payload)
+  state.outcomes[base_seq][member_id] += outcome   # 实时路径（按 id 去重）
+  renderMessages()

 onStreamStatus(payload)
+  if 该成员在消息流里有轮次结果行 and 状态变了: renderMessages()   # 不让「没启用」这句话过期

 loadWorkspace() / selectChannel()
+  await refreshOutcomes()                          # 补拉路径（只读端点，与消息同一次快照）
```

`app/worksplice/studio/app.css`：只新增 `.msg.outcome` 一族规则，色位按名字消费既有角色族
（描边/状态词/原因 `--error`，底 `--error-soft`，说明 `--muted`，时间 `--faint`）。

**为什么不动后端、不加字段、不改事件形状**：`reason` 与 `base_seq` 已经足够回答「我发的这条 @ 它，
它没能回应，因为…」；需要「这一轮是失败还是未启用」这一层区分时，前端把两个**已经取到**的后端
事实（成员 `status`/`enabled_reason` 与轮次 `status`/`reason`）分开陈述即可。票面的「修复必须小」
与「超出前端呈现范围要先 ask」两条因此都满足。

## 4. 真实 Chromium 取证（独立临时数据目录）

场景（三例一次跑完，模拟用户主诉）：

1. **未启用（缺凭证）**：Agent 配成 `dashscope:deepseek-v4.1-flash`，进程内**不设** `DASHSCOPE_API_KEY`
   ⇒ 后端算出的 reason 正是 「模型提供商 dashscope 缺少凭证：请先设置环境变量 DASHSCOPE_API_KEY」。
   （**无需真模型调用、零费用**；`DASHSCOPE_API_KEY` 已用 `env -u` 从后端进程环境里剔除。）
2. **这一轮模型调用失败**：另一个 `ollama:qwen2.5` 成员，本机没有 Ollama ⇒ 该轮 `failed`
   且 reason 是连接失败原文。
3. **@ 未加入的成员（穿透）**：再 `@` 一个不在频道里且未启用的成员。

后端：`/tmp/wsps-run-8.sh <checkout> <data> <port>`（真实 `python -m worksplice`，只监听 loopback）。
播种：`/tmp/wsps-seed-8.py <base_url>`（只经 HTTP 人类面：建 Agent → 确认数据流 → 建频道）。

### 4.1 红证据（改动前，隔离 worktree `/tmp/wsps-red-8` = `25108fb`）

```text
$ git worktree add --detach /tmp/wsps-red-8 25108fb
$ uv venv --python 3.11 /tmp/wsps-red-8/.venv && uv pip install --python /tmp/wsps-red-8/.venv/bin/python -e "./app[dev]"
$ /tmp/wsps-run-8.sh /tmp/wsps-red-8 /tmp/wsps-red-8-data 8798     # 端口 8798
```

> ⚠️ 隔离副本必须有自己的 venv：`app` 是 editable 安装，共用一个 venv 时后端会从我**已改**的
> 工作树里读 `studio/`，红证据就变成绿的了（我第一次就踩了这个坑：第一轮「红」取证里页面显示
> 的其实是修好后的前端）。换成独立 venv 后 `curl /studio/app.js | grep -c roundOutcomeNode` = 0。

Chromium 探针（`ego-browser nodejs`）——同一脚本、同一场景：

```js
await page.goto("http://127.0.0.1:8798/studio/");   // 基线前端
await page.click("#channelList .nav-row");
await page.fill("#composerInput", "@royce 看一下这条"); await page.press("#composerInput", "Enter");
await addAgent({name:"flaky", model_id:"ollama:qwen2.5"});   // 第二例
await page.fill("#composerInput", "@flaky 也看一下");  await page.press("#composerInput", "Enter");
await page.waitForFunction(() => [...document.querySelectorAll("#memberList .presence")]
                                  .some(n => n.dataset.status === "error"));
```

输出（**改动前**）：

```text
{
 "backend": { "lastSeq": 2,
   "outcomes": [ {status:"failed", base_seq:1, reason:"模型提供商 dashscope 缺少凭证：请先设置环境变量 DASHSCOPE_API_KEY"},
                 {status:"failed", base_seq:2, reason:"模型提供商 dashscope 缺少凭证：请先设置环境变量 DASHSCOPE_API_KEY"},
                 {status:"failed", base_seq:2, reason:"模型调用失败：ConnectionError: Failed to connect to Ollama. …"} ] },
 "streamText": "今天\nO\nOwner\n08:39\n@royce 看一下这条\nO\nOwner\n08:39\n@flaky 也看一下",
 "messageRows": 2,
 "failureRowsInStream": 0,        ← 消息流里零呈现
 "reasonInStream": false,         ← 消息流里看不到任何一个 reason
 "reasonAnywhereOnPage": false,   ← 整页（含默认隐藏的 dock）都没有这句话
 "presence": ["offline", "error"],← 唯一的信号是状态点变色
 "dockHidden": true,
 "navigations": 1
}
```

截图：`/tmp/wsps-evidence-8/red-baseline.png`（消息流里只有两条 Owner 消息，右侧状态点一个灰
一个红，页面上没有任何一处提到原因）。

**这就是 #8 的红**：后端三条 `failed`、三条可读 reason、`lastSeq` 已推进，前端一句话都没说。

### 4.2 绿证据（改动后，同一场景）

```text
$ /tmp/wsps-run-8.sh <worktree> /tmp/wsps-green-8-data 8799   # 本 worktree 的 .venv
```

输出（**改动后**，节选探针结果）：

```text
{
 "existingBehaviors": { "optimisticPendingSeen": true, "typingSeen": true,
                       "unreadBefore": 0, "unreadAfter": 0, "lastSeq": 3, "pageErrors": [], "navigations": 1 },
 "rows": [
   { "cls":"day-sep" },
   { "cls":"msg",          "seq":"1", "text":"O | Owner | 08:45 | @royce 看一下这条" },
   { "cls":"msg outcome",  "baseSeq":"1",
     "text":"royce | 未回应 | 没启用 | 08:45 | royce 现在是「未启用」，所以这一轮没有回复。后端给出的原因： | 模型提供商 dashscope 缺少凭证：请先设置环境变量 DASHSCOPE_API_KEY" },
   { "cls":"msg",          "seq":"2", "text":"O | Owner | 08:45 | @flaky 也看一下" },
   { "cls":"msg outcome",  "baseSeq":"2", "text":"royce | 未回应 | 没启用 | …DASHSCOPE_API_KEY" },
   { "cls":"msg outcome",  "baseSeq":"2",
     "text":"flaky | 未回应 | 这一轮失败 | 看成员状态 | 08:45 | 模型调用失败：ConnectionError: Failed to connect to Ollama. …" },
   { "cls":"msg",          "seq":"3", "text":"O | Owner | 08:45 | @outsider 你来一句" },
   { "cls":"msg outcome",  "baseSeq":"3", "text":"royce | 未回应 | 没启用 | …DASHSCOPE_API_KEY" },
   { "cls":"msg outcome",  "baseSeq":"3", "text":"outsider | 未回应 | 没启用 | …DASHSCOPE_API_KEY" }
 ],
 "outcomeStates": ["没启用","没启用","这一轮失败","没启用","没启用"],
 "messageRows": 3,        ← 消息仍只有 3 条（失败不写半条消息、不占 seq）
 "agentReplies": 0,       ← 零条 Agent 消息：不伪造「它答应了」
 "outcomeRows": 5,
 "navigations": 1         ← 全程零刷新（事件流实时送达）
}
```

截图：`/tmp/wsps-evidence-8/green-final.png`。可见的是三条人类消息，每条下面挂它自己触发的那几行
失败呈现；`royce` 的行写「没启用」，`flaky` 的行写「这一轮失败」——**同一屏里就能看出两者的区别**。

**逐条对应验收：**

| 验收项 | 可见结果 |
| --- | --- |
| 失败在消息流里可读，含该轮 `reason` 文本 | 每一行都原样带后端 reason（`DASHSCOPE_API_KEY` / `模型调用失败…`） |
| 绑定到「哪条消息触发的哪一轮」 | `msg outcome` 行的 `data-base-seq` 等于它**紧邻上一条消息**的 `data-seq`（`1→1`、`2→2`、`3→3`）；见 §4.3 的判别力验证 |
| 不伪造「它答应了」 | `messageRows=3`（只有人类消息）、`agentReplies=0`、行上**没有** `data-seq`（用 `data-base-seq`）、没有头像、`role="status"` |
| 后续成功回应不显得「说了两次」 | 失败行锚在**触发它的那条消息**下，不是锚在成员上；用例 `test_a_later_successful_round_does_not_duplicate_the_failure`（§5）断言 `agent_messages == ["第二次我来了"]` 且两条轮次结果 `["failed","replied"]` 均可追溯 |
| 状态点语义不变 + 未启用与轮次失败可区分 | 侧栏 `presence` 仍 `["offline","error","offline"]`（四态只表达后端事实）；行内状态词 `没启用` vs `这一轮失败` |
| 沿用既有角色族、不新造色位、无 hex/rgb | §6 |
| 三栏布局 / 68ch / 14px / 1.65 不破坏 | §4.4 |
| 不破坏 SSE / 补拉 / typing / 乐观发送 / 已读 | 见 `existingBehaviors`（`navigations: 1`、`typingSeen: true`、`optimisticPendingSeen: true`、`unread` 前后一致）+ §4.5 刷新路径 |

三条路径都取到了：**实时事件流**（`navigations: 1`，不刷新就出现）、**只读端点补拉**（刷新后
`outcomeCount: 5`，同样 5 行、同样的状态词）、**从失败现场打开 dock**（点行内「看成员状态」⇒
`dockHidden: false`，dock 列出 `royce … 未启用 + 原因` 与 `flaky … 出错`）。

### 4.3 绑定关系的判别力（运行时变异，不是「看起来对」）

把「按 `base_seq` 分组」故意改错一位（运行时注入，等价于「不按触发消息绑定」的实现）：

```text
=== 绑定判别力（运行时变异） ===
{ "before": ["day-sep","msg:1","msg outcome@1","msg:2","msg outcome@2","msg outcome@2","msg:3","msg outcome@3","msg outcome@3"],
  "after":  ["day-sep","msg:1","msg outcome@3","msg outcome@3","msg:2","msg outcome@2","msg outcome@2","msg:3"] }
page errors: []
```

注入变异后呈现位置**确实随之改变**（`@3` 的两行跑到了 `msg:1` 下面，`msg:3` 下面空了），
说明「挂在哪条消息下面」由 `base_seq` 决定，而不是巧合。变异的注入与还原都在浏览器内存里完成，
工作树未受影响（`page errors: []`，页面无异常）。

### 4.4 布局与视觉契约

```text
=== 布局契约 ===
1440: {"rail":252,"dock":380,"streamInner":768,"outcomeRow":784,"overflowX":false,"flexDirection":"row", "reasonFontSize":"12px","reasonLineHeight":"16.8px","reasonMaxWidth":"513.98px"}
1080: {"rail":252,"dock":340,"streamInner":448,"outcomeRow":464,"overflowX":false,"flexDirection":"row", "msgTextSize":"14px", "msgTextLineHeight":"23.1px", "msgTextMaxWidth":"599.65px"}
 880: {"rail":880,"dock":880,"streamInner":837,"outcomeRow":853,"overflowX":false,"flexDirection":"column"}
```

- 三栏尺寸与断点（rail 252 / dock 380，`≤1080px` 340，`≤900px` 列）**未变**：新元素落在
  `.msg` 同一条流里，宽度就是 `--stream-max` 减去 2×`--sp-4`（1440 下 880−96=784）。
- 无横向溢出（三档 `overflowX: false`）。
- 正文契约未破：`.msg-text` 仍 `max-width: 68ch`（实测 600px）、14px / 1.65；
  失败行的 reason 用 `--fs-sm`(12px) / `--lh-snug`(1.4) / `max-width: 68ch`，与「消息正文 14px」
  是**两种**东西（它是元信息，正文本体没变）。
- 截图：`/tmp/wsps-evidence-8/green-final.png`（dock 隐藏）、`green-dock-from-failure-row.png`（dock 打开）。

### 4.5 断线补拉与刷新

```text
=== 刷新后（只读端点补拉路径） ===
{ "outcomeStates": ["没启用","没启用","这一轮失败","没启用","没启用"],
  "outcomeRows": 5, "messageRows": 3, "navigations": 1 }
```

刷新后行数与状态词与实时路径**完全一致**——两条路读的是同一份后端事实（`refreshOutcomes()`
用只读端点，`onStreamOutcome()` 用事件），也就不存在「刷新后失败痕迹消失」的窗口。
实时路径与补拉路径对同一条轮次结果按 `id` 去重，因此不会呈现两次。

浏览器边界（与前两票同一条已知边界，不新增风险面）：**「成员先失败、随后同一成员成功回应」这一
路径**在浏览器里没有取到 —— 本机环境没有任何可用的模型 provider（没有 Ollama、没有远端 key，
票面也明确要求不配 key），所以拿不到一条真实的 `replied` 轮次。该路径由脚本化替身的用例
`test_a_later_successful_round_does_not_duplicate_the_failure`（§5）取证；它的结构保证是
「失败行锚在触发消息下、不锚在成员上」，而这一点已由 §4.3 的变异验证。

## 5. HTTP / SSE 黑盒测试（本票新增 6 条）

`app/tests/test_round_outcome_visibility.py`：只断言**外部可观察行为**（状态码、JSON 载荷、
SSE 帧），不断言前端 DOM 细节；用仓库既有的唯一高层接缝（`tests/conftest.py` 夹具 +
`tests/scripted.py` 的脚本化替身），每条用例独立临时数据目录。

```text
$ .venv/bin/python -m pytest app/tests/test_round_outcome_visibility.py
6 passed in 1.27s
```

| 用例 | 断言的契约 |
| --- | --- |
| `test_disabled_round_failure_is_readable_and_bound_to_the_triggering_message` | 缺凭证被 @：`status=failed`、`reason` 可读（含 provider 与 `DASHSCOPE_API_KEY`）、`base_seq == triggering.seq == 1` |
| `test_disabled_round_writes_no_half_message_and_does_not_take_a_seq` | 失败不伪造回应：流里只有那条人类消息，`last_seq` 不因失败前进 |
| `test_unavailable_member_state_and_failed_round_outcome_are_distinguishable` | 两个后端事实分别可取：成员 `status=offline` + `enabled_reason`，与轮次 `status=failed` + `reason` |
| `test_model_failure_round_carries_its_own_readable_reason` | 第二种情形（成员可用、这一轮模型调用失败）：`reason` 原样、`base_seq` 绑定、状态点 `error` 而 `enabled_reason` 为空 |
| `test_a_later_successful_round_does_not_duplicate_the_failure` | 失败后同一成员成功：流里**恰好一条**它的回复；两条轮次结果 `["failed","replied"]` 均可追溯（`base_seq = [1,2]`） |
| `test_failed_round_outcome_reaches_the_event_stream` | 失败的轮次结果**被推给订阅者**（真实 HTTP 流），且与只读端点读到的是**同一条**事实 |

**这些用例在基线上也是绿的**（它们在基线 worktree 上同样是 `6 passed`）：它们断言的是
**前端据以呈现的那份事实**，而那份事实在基线就已经存在——这正是 #8 的诊断（后端已算、已落、
已推，前端把信息丢了）。红→绿证据因此只能由**前端呈现**本身给出，即 §4.1 vs §4.2 的真实
Chromium 对比（那里红得干净：`failureRowsInStream: 0`、`reasonAnywhereOnPage: false`）。

## 6. 视觉契约核对（按名字消费，不新造色位）

```text
$ git diff -- app/worksplice/studio/app.css | grep -E "^\+" | grep -nE "#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\("
无 hex/rgb/hsl 硬编码

# 浏览器里读回的失败行规则（未级联解析，原文）：
[ ".msg.outcome { … border-left: 2px solid var(--error); background: var(--error-soft); }",
  ".msg.outcome .msg-tag { … color: var(--error); border-color: var(--error); }",
  ".msg-outcome-author { color: var(--error); }",
  ".msg-outcome-state { … color: var(--error); }",
  ".msg-outcome-act { … color: var(--muted); … }",
  ".msg-outcome-reason { … color: var(--error); max-width: 68ch; overflow-wrap: anywhere; }",
  ".msg-outcome-note { … color: var(--muted); }" ]
grep 命中项：[]      ← 新增规则里没有一处硬编码色值
```

- 只用既有角色族：`--error`（deep 档：文字/描边）、`--error-soft`（soft 档：底）、
  `--muted` / `--faint`（既有文字档）。**没有**新造色位，**没有**动 `product-tokens.css`。
- `worksplice-design-system/` 只读：`git diff --name-only` 里 0 个该目录条目（§7）。
- 简体中文；标识符与计数用 mono + `tabular-nums`（`.mono, .count, .badge` 规则未动）；
  按钮文案动词开头（「看成员状态」）。
- 时间戳沿用既有 `--fs-mono-xs` 的 `.msg-time` 形态（与消息行同一个组件类）。

## 7. 范围与边界核对

```text
$ git diff --name-only 25108fb...HEAD
app/specs/issue-8-acceptance.md
app/specs/issue-8-pr-body.md
app/tests/test_round_outcome_visibility.py
app/worksplice/studio/app.css
app/worksplice/studio/app.js
```

- **未改后端**：`worksplice/` 下的 `.py` 一个字节未动（`api.py` / `service.py` / `events.py` /
  `db.py` / `orchestration.py` / `agent_seam.py` 全在名单外）。轮次语义、`round_outcome` 结构、
  状态点推导规则、事件形状都没有改。
- **未改 `worksplice-design-system/`**（只读参照，原型未被修改）。
- **未碰 composer 区域**（#9 会并行改那里）：

  ```text
  $ git diff -U0 -- app/worksplice/studio/app.js | grep -E "^\+.*(composer|placeholder|mention|autocomplete)"
  composer/placeholder/mention 相关行未改
  ```

  唯一与 composer 相邻的改动是消息流自身的渲染链（`renderMessages` / `onStream*` /
  `refreshOutcomes`）——`#composerInput` 的 `placeholder`、`syncSendButton`、`autosizeComposer`
  与提及补全相关的行都在 diff 之外。
- **未新增写路径**：没碰任何「编辑/删除消息」的路径；失败行没有重试按钮（重试的入口仍是
  「再发一条消息」，因为消息 append-only）。`role="status"`、无头像、无 `data-seq`，任何把 DOM
  行数当消息数的统计都不会被它骗到。
- **未引入真模型依赖、未要求任何凭证**：验收全程 `env -u DASHSCOPE_API_KEY …`，第三例的
  `ollama` 失败是「本机没有 Ollama」的真实结果，不产生任何费用。
- **未实现 #9 的 @ 补全**，未做可观测页面（#1 Out of Scope 未变）。

## 8. 聚焦自审（替代完整双轴 review）

影响面可圈定（前端呈现一处 + 它的取证），按票面要求做聚焦自审，两轴各记一节。

### 8.1 Standards 轴（本仓的既有写法与契约）

| # | 检查项 | 结论 |
| --- | --- | --- |
| S1 | DOM 一律 `createElement`/`textContent`，不用 `innerHTML`（app.js 头部契约） | 新行用 `el()` 构建，reason 走 `textContent`（后端文本不被当 HTML） |
| S2 | 不新造色位、不硬编码 hex/rgb、色值一律 oklch | §6：新增规则 0 处硬编码，只用 `var(--error*)` / `var(--muted)` / `var(--faint)` |
| S3 | 术语遵仓库语言（`CONTEXT.md`） | 用「轮次结果」「成员」「未启用」「频道」；注释里用「轮次结果」而非「结论」（#4 的 S7 教训） |
| S4 | 命名一致性 | `refreshOutcomes` 与既有 `refreshMessages`/`refreshStatuses` 同形；`groupOutcomes` / `outcomeMembers` / `roundOutcomeNode` 与既有 `messageNode`/`render*` 同风格；JS 用 camelCase、DOM 属性用 `data-base-seq`（kebab） |
| S5 | 文档与代码不漂移 | 文件头补了本票新增说明；类型字段名（`base_seq`/`reason`/`status`）与后端同形，未另起名字 |
| S6 | 注释不写「过期事实」 | `roundOutcomeNode` 的 docstring 写清「只呈现 failed，ignored/预算耗尽/合并保持不呈现」的理由，不声称做了没做的事 |
| S7 | 不留下死代码/未用符号 | `ruff` 与编辑器诊断均干净；写这段时先写了一个 `OUTCOME_LABEL` 常量，随后发现在「只呈现 failed」的口径下它是无用的一般化，已删除（不留 speculative generality） |
| S8 | 一个渲染关注点一个函数 | 新逻辑集中在 `roundOutcomeNode`，`renderMessages` 只多了一个「按 `base_seq` 取该消息的失败组」的三行调用 |
| S9 | 不引入第二个数据通道 | 数据只来自既有的只读端点与 SSE（`api()` 是唯一 HTTP 入口），没有 localStorage 之类的新状态源 |

### 8.2 Spec 轴（#8 的验收项逐条核对）

| # | #8 的验收项 | 结论 |
| --- | --- | --- |
| P1 | 失败轮次在消息流里可读、含 `reason`、覆盖「未启用」与「模型调用失败」两种情形 | §4.2 三例取证（`没启用` × 4、`这一轮失败` × 1，reason 原文可见） |
| P2 | 呈现绑定到「哪条消息触发的哪一轮」 | §4.2 的 `data-base-seq == 上一条消息 data-seq`，§4.3 变异验证判别力 |
| P3 | 不伪造「它答应了」（不渲染成 Agent 消息、不占 `seq`） | `messageRows=3` / `agentReplies=0` / 无 `data-seq` / 无头像 / `role="status"` |
| P4 | 后续成功回应不显得「说了两次」，历史失败仍可追溯 | 用例 `test_a_later_successful_round_does_not_duplicate_the_failure`（浏览器边界见 §4.5 末） |
| P5 | 状态点四态语义不变；未启用与轮次失败可区分 | `presence: ["offline","error","offline"]`；行内 `没启用` vs `这一轮失败` 分开陈述，且**两者可能同时为真时不互相冒充** |
| P6 | 沿用既有角色族、不新造色位、不硬编码 hex/rgb | §6 |
| P7 | 简体中文、mono + tabular-nums、动词开头按钮、68ch/14px/1.65、三栏契约 | §4.4 / §6 |
| P8 | `worksplice-design-system/` 只读、原型不改 | §7（diff 名单里 0 条） |
| P9 | 不破坏 SSE、断线补拉、typing、乐观发送、已读未读、预算 ≤3 与穿透 | §8.3 逐条 |
| P10 | 不新增编辑/删除消息路径 | §7（失败行无重试/无删除；消息与轮次结果端点未动） |
| P11 | 沿用既有测试范式 + 真实 Chromium 手跑取证 + 红→绿 | §4 / §5（范式与边界说明写在 §5 末） |
| P12 | 跑全量测试与 ruff/format/mypy/pyright | §1 |
| P13 | 不实现 @ 补全（#9）、不做可观测页面 | 未实现（§7） |
| P14 | 不改后端轮次语义 / `round_outcome` 结构 / 状态点推导 | 未改（§7 第一行） |

**一个口径选择（已在 §3 与代码注释里写明）**：只呈现 `failed`。`ignored`（自判忽略）、
`budget-exhausted`（预算耗尽）、`merged`（重复唤醒合并）同属轮次结果，但它们是**正常协议
选择**而不是「它没做到」，且既有前端对它们一字未呈现。票面要修的是「@ 之后没有任何反应」——
那是 failed。为免消息流被协议簿记淹没，本票不扩大呈现面；若规格后续要求「自判忽略也要可见」，
那是另一张票（属行为扩张，不属修复）。

### 8.3 逐条确认既有行为未被破坏

| 既有行为 | 本轮改动是否触及 | 证据 |
| --- | --- | --- |
| SSE 事件流（四类事件、按 seq 追加） | 只**新增消费** `round_outcome` 的载荷，其余帧处理一字未改 | `navigations: 1`（全程不刷新就出现）+ `test_event_stream.py` 16 条全绿 |
| `last_seq` 断线补拉 | 未改 | `test_round_outcome_visibility.py::test_failed_round_outcome_reaches_the_event_stream`、`test_event_stream.py` 全绿；§4.5 刷新后行数一致 |
| 四态状态点 | 未改推导与渲染 | §4.2 `presence: ["offline","error","offline"]`；`test_event_stream.py::test_status_event_reports_*` / `test_ignored_round_returns_to_online` 全绿 |
| `typing`「正在回复」 | 未改 `showTypingFor` / `renderTyping` | §4.2 `typingSeen: true`；`test_event_stream.py::test_typing_indicator_...` 全绿 |
| 乐观发送（pending → seq / 显式失败） | 未改 `sendMessage` / `deliver` / `messageNode` 的 pending 与 failed 分支 | §4.2 `optimisticPendingSeen: true`；`test_send_message.py` 12 条全绿 |
| 每频道已读/未读 | 未改 | §4.2 `unread` 前后 `0`；`test_channel_read.py` 12 条全绿 |
| 预算 ≤3 与穿透 | 未改唤醒面/预算；穿透的 system 行渲染未改 | `test_bounded_relay.py` 24 条、`test_agent_round.py` 32 条全绿 |
| 消息 append-only | 未新增任何写路径 | 全量 149 条含既有 `test_no_message_edit_or_delete_path_...`、`test_round_outcome_is_append_only` 全绿 |

**一处刻意的额外行为（我自己决定并记在此处）**：「看成员状态」按钮。它不是票面要求的最小项，
但「未启用 → 原因」目前只有默认隐藏的 dock 能看见（#8 诊断里的这句话）。加一个只**打开面板**、
不滚动页面、不写任何状态的按钮，让 `--offline` 的原因在失败现场一跳可达。风险面极小（碰的是
`#dock.hidden` 与 `renderDock()`，没有新增数据通道）；若 human 认为超出最小范围，删掉它不影响
本票任何验收项。

### 8.4 一处诚实性修正（自审发现）

写第一版时，`没启用` / `这一轮失败` 的状态词由 `statusOf(agent)` 推导；当某个成员**既不在
`state.members` 也不在 `state.agents`**（拿不到状态）时，`statusOf(undefined)` 会返回 `offline`，
于是界面会写「没启用」——把一个「我不知道」说成「未启用」。已改为「状态未知时不声明任何成员
状态」（`const status = agent ? statusOf(agent) : null`，状态词仅在取到时渲染）。另一处同类修正：
失败行原本复用 `.msg-author.is-agent`，那会让「数几条 Agent 消息」的统计把失败行也数进去
（我自己的取证探针就被骗到过一次：`agentReplies: 5`）；已改为独立类名 `msg-outcome-author`。

## 9. 取舍与遗留

1. **只呈现 `failed`**（§8.2 末）：`ignored` / `budget-exhausted` / `merged` 保持不呈现。
   这是刻意的范围选择，理由与升级路径已写明。
2. **`round-outcomes` 是整频道的只读列表**（没有 `base_seq` 游标推进）：本票按「进入频道取一次
   + 实时事件增量合并」消费，够用且不需要后端改动。频道长期运行后这张表变大是后端侧的既知
   事实（#4 已记「首版不做可观测页面」），若将来消息量级上来，应由后端侧决定是否加分页，而不是
   前端自行截断（截断会静默丢事实）。
3. **`@` 未加入且未启用的成员**：穿透失败会产生两条 failed 记录（频道成员 + 穿透目标），
   呈现上是两行、各自带自己的 reason。这如实反映「两个成员各有一轮 failed」，未做合并；
   若规格认为穿透未加入这一行是噪音，也是一个可选的产品决定，不在本票范围。
4. **浏览器未覆盖「失败后成功」**（§4.5 末）：环境里没有任何可用 provider，票面又明确不配 key。
   该路径由脚本化替身的用例取证。
5. **`app/README.md` 未改**：本票不需要新的运行/契约说明（行为与端点都没变），且 #9 正在并行改
   `app.js`/`app.css` 的 composer 区域，避免无谓的同文件竞争；如需在 README 记一句前端口径，
   可由规格侧或 #9 一并处理。
6. **未做完整双轴 code-review**（票面允许）：影响面可圈定（一处呈现 + 其取证），改做 §8 的
   聚焦自审，两轴各一节 + 一条变异验证（§4.3）。

## 10. 交付回执

```text
$ git push -u origin issue-8-outcome-visible
 * [new branch]      issue-8-outcome-visible -> issue-8-outcome-visible

$ gh pr create --title "fix(app): #8 把轮次失败/未启用 reason 变成消息流里可见的事实" \
               --body-file app/specs/issue-8-pr-body.md --base main
https://github.com/whutlichao/alibot/pull/10

$ gh pr view 10 --json number,state,headRefName,baseRefName
{ "number": 10, "state": "OPEN", "headRefName": "issue-8-outcome-visible", "baseRefName": "main" }
```

- 提交：`3d3db36 fix(app): #8 把轮次失败/未启用的原因变成消息流里可见的事实`（实现 + 测试 + 文档，
  5 文件 +935 行），随后一次 `docs(app): #8 回填 PR 号与二次取证读数` 只改本文件。
- 合并由 coordinator 执行（G-merge）；本票不自行合并。
