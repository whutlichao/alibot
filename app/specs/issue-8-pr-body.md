## Summary

轮次失败的原因在后端已经算好、落库、推流，前端把信息丢了：用户 `@` 一个未启用的 Agent，消息流里
一个字都没有，唯一的信号是状态点变色（`--offline`），而带原因的 dock 默认隐藏。本票把它接上——
**只补呈现，不动后端**。

```diff
 renderMessages()
   for each message
     <MessageRow>                                     # 不变
+    if state.outcomes[message.seq]                   # 按 base_seq 绑定到触发它的那条消息
+      for each member whose round failed
+        <RoundOutcomeRow role="status">              # 不是消息：无头像、无 data-seq、无重试按钮
+          「<成员>」[未回应] (没启用|这一轮失败) <时间>
+          「<成员> 现在是「未启用」，所以这一轮没有回复。后端给出的原因：」   # 仅 offline 时
+          <后端 reason 原文>
+
 onStreamOutcome(payload)                             # 实时路径（按 id 去重）
+  state.outcomes[base_seq][member_id] += outcome;  renderMessages()
+onStreamStatus(payload)                              # 状态变了就让行内那句话跟着走，不过期
+  if 该成员有轮次结果行 and status 变了: renderMessages()
+loadWorkspace() / selectChannel()                     # 补拉路径
+  await refreshOutcomes()                             # 只读端点，与消息同一次快照
```

```text
数据来源（全部既有，形状一字未动）
  GET  /api/channels/{id}/round-outcomes   →  status / reason / base_seq     （进入频道取一次）
  SSE  event: round_outcome                →  同形载荷，与只读端点逐字段相等   （实时增量）
```

**「未启用」（成员状态）与「这一轮失败」（轮次结果）分开陈述**：前者取 `status === 'offline'` +
`enabled_reason`，后者取轮次 `status === 'failed'` + `reason`。两者会同时为真，界面因此不把后者
说成前者，也不把前者冒充后者——同一屏里 `没启用` 与 `这一轮失败` 各自可读。

改动文件：`app/worksplice/studio/app.js`（+142）、`app/worksplice/studio/app.css`（+32）、
`app/tests/test_round_outcome_visibility.py`（新）、`app/specs/issue-8-{acceptance,pr-body}.md`。
后端 `.py` **零改动**；`worksplice-design-system/` 只读未动；`#9` 的 composer 区域未碰。

## Evidence

**Before**（`/tmp/wsps-red-8` = `25108fb`，独立 venv + 真实 uvicorn + 真实 Chromium）：
后端三条 `failed`、三条可读 reason 都在，前端**零呈现**。

```text
{ "backend": { "lastSeq": 2, "outcomes": [
    {"status":"failed","base_seq":1,"reason":"模型提供商 dashscope 缺少凭证：请先设置环境变量 DASHSCOPE_API_KEY"},
    {"status":"failed","base_seq":2,"reason":"模型提供商 dashscope 缺少凭证：…"},
    {"status":"failed","base_seq":2,"reason":"模型调用失败：ConnectionError: Failed to connect to Ollama. …"}]},
  "streamText": "今天\nO\nOwner\n08:39\n@royce 看一下这条\nO\nOwner\n08:39\n@flaky 也看一下",
  "messageRows": 2, "failureRowsInStream": 0,
  "reasonInStream": false, "reasonAnywhereOnPage": false,
  "presence": ["offline","error"], "dockHidden": true, "navigations": 1 }
```

截图 `red-baseline.png`：消息流里只有两条 Owner 消息，右侧一个灰点一个红点，页面上没有任何一处提到原因。

**After**（同一场景，同一组探针，本 worktree）：

```text
{ "rows": [
   {"cls":"msg",         "seq":"1", "text":"Owner | @royce 看一下这条"},
   {"cls":"msg outcome", "baseSeq":"1", "text":"royce | 未回应 | 没启用 | royce 现在是「未启用」，所以这一轮没有回复。后端给出的原因： | 模型提供商 dashscope 缺少凭证：请先设置环境变量 DASHSCOPE_API_KEY"},
   {"cls":"msg",         "seq":"2", "text":"Owner | @flaky 也看一下"},
   {"cls":"msg outcome", "baseSeq":"2", "text":"royce | 未回应 | 没启用 | …DASHSCOPE_API_KEY"},
   {"cls":"msg outcome", "baseSeq":"2", "text":"flaky | 未回应 | 这一轮失败 | 看成员状态 | 模型调用失败：ConnectionError: Failed to connect to Ollama. …"},
   {"cls":"msg",         "seq":"3", "text":"Owner | @outsider 你来一句"},
   {"cls":"msg outcome", "baseSeq":"3", "text":"royce | 未回应 | 没启用 | …DASHSCOPE_API_KEY"},
   {"cls":"msg outcome", "baseSeq":"3", "text":"outsider | 未回应 | 没启用 | …DASHSCOPE_API_KEY"} ],
  "outcomeStates": ["没启用","没启用","这一轮失败","没启用","没启用"],
  "messageRows": 3, "agentReplies": 0, "navigations": 1,      ← 消息仍只有 3 条，零 Agent 消息
  "typingSeen": true, "optimisticPendingSeen": true, "unread": [0, 0] }
```

截图 `green-final.png`：三条人类消息，每条下面挂着它自己触发的那几行失败呈现。

**红→绿的条件**：本票的测试在基线上也是绿的（它们断言的是「前端据以呈现的那份事实」，
而那份事实在基线就已存在——这正是 #8 的诊断）。红→绿只能由呈现本身给出，因此用上面的
Chromium 前后对比。绑定的**判别力**另做运行时变异验证（把分组故意挪错一位）：

```text
before: [day-sep, msg:1, outcome@1, msg:2, outcome@2, outcome@2, msg:3, outcome@3, outcome@3]
after:  [day-sep, msg:1, outcome@3, outcome@3, msg:2, outcome@2, outcome@2, msg:3]
```

**门禁**：`149 passed, 1 skipped`（新增 6 条）· `ruff check`/`format --check` 全过 ·
`mypy` 24 files 无问题 · `pyright` errors 0。新增用例只断言 HTTP/SSE 外部可观察行为
（状态码 / JSON / SSE 帧），前端呈现按前两票边界用真实 Chromium 手跑取证，见
`app/specs/issue-8-acceptance.md` §4。

## Merge Danger

**Door:** two-way

纯前端呈现 + 测试与验收文档；后端 `.py` 一个字节未动，数据库结构、`round_outcome` 形状、
事件形状、状态点推导都没有变化。回滚就是回滚这一个 commit，没有数据迁移、没有不可逆副作用。

**Blast Radius:** narrow

影响面是消息流一处（`renderMessages` + 轮次结果的取数与消费）。新行不占 `seq`、没有 `data-seq`、
没有头像、`role="status"`，因此不会污染任何「消息条数 / Agent 消息数」的统计；它只消费既有的
只读端点与 SSE 事件，不新增写路径、不新增状态源。`--error` / `--error-soft` / `--muted` 是既有
角色族，未新造色位、无硬编码 hex/rgb。三栏布局与断点实测未变（rail 252 / dock 380 / ≤1080 340 /
≤900 列），无横向溢出，正文 68ch/14px/1.65 契约未动。与 `#9` 并行改动同一对文件的**不同区域**
（composer vs 消息流），`git diff -U0 | grep composer|placeholder|mention` 为空。

**已知遗留**（细节在 `app/specs/issue-8-acceptance.md` §9）：只呈现 `failed`（`ignored` /
`budget-exhausted` / `merged` 是正常协议选择，保持不呈现）；浏览器取证未覆盖「失败后同一成员
成功回应」（本机无任何可用 provider，票面也明确不配 key），该路径由脚本化替身的用例覆盖。
