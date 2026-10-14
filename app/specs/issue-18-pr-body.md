## Summary

轮次结果落库、只读端点与 SSE 事件都已存在，但前端只渲染 `failed`（`roundOutcomeNode()` 里
`records.filter((record) => record.status === 'failed')`，其余四态静默）。于是用户能分辨「它坏了」，
分辨不了「它自判忽略」「预算已用尽」「被合并」——而这三条恰恰是「有界响应」与「唤醒是注意力不是命令」
两条核心设计在用户侧的**唯一可观察面**。

本票把它们变成可读事实，但**不淹没对话**：每一条消息下面**至多一行**默认折叠的摘要行。

```diff
 renderMessages()
   for each message
     <MessageRow>                                      # 不变
     if state.outcomes[message.seq]
+      <RoundOutcomeSummary role="status">              # 同一条消息下**至多一块**
+        <button aria-expanded aria-controls>           # 原生 button：Tab 可聚焦、Enter/Space 可切
+          ▸阿墨、云舟 等 3 人 没有回应 · 展开看原因     # 名单逐个列出；>2 人写「等 N 人」
+        </button>
+        自判忽略（正常的协议选择，不是失败）            # 折叠态：三态各自的后端事实说法
+        (展开) <div id="outcome-summary-N">
+          <div class="msg-outcome-line">                # 逐成员一行
+            <成员名> <status 中文名> <时间>
+            <后端 reason 原文>                          # 不重新措辞、不截断
+          </div>
+        </div>
       for each failed outcome
         <RoundOutcomeRow role="status">                # 保持直接展开，逐字未改
           未回应 · 这一轮失败 · 看成员状态 · <reason 原文>
         </RoundOutcomeRow>

 onStreamOutcome(payload)                               # 五种结果都重建消息流
+  if 该 outcome 是新记录: renderMessages()
```

```text
数据来源（全部既有，形状一字未动）
  GET  /api/channels/{id}/round-outcomes   →  status / reason / base_seq / finished_at   （进入频道取一次）
  SSE  event: round_outcome                →  同形载荷，与只读端点逐字段相等              （实时增量）
```

**摘要行的覆盖范围**（用户本轮裁定，见「Decisions」）：`failed` 直接展开 +
`ignored` / `budget-exhausted` / `merged` 收在折叠里；**`replied` 不挂行**——它的可见事实就是
那条 agent 回复消息本身（#1「一轮 ↔ 至多一条频道消息」）。

**四态不混为一谈**（票面硬要求）：`ignored` = 自判忽略（正常协议选择，**不是失败**）；
`budget-exhausted` = 预算已用尽、本轮零投递（**不是出错**，成员状态点仍空闲）；
`merged` = 重复唤醒被合并进正在进行的那一轮（**不是失败**）；`failed` = 需排查。

改动文件：`app/worksplice/studio/app.js`、`app/worksplice/studio/app.css`、
`app/tests/test_round_outcome_visibility.py`（+5 条）、`app/specs/issue-18-{acceptance,pr-body}.md`。
后端 `.py` **零改动**；`worksplice-design-system/` 只读未动。

## Evidence

**Before**（`/tmp/wsps-18-red` = `8bb4f9a`，独立 venv + 真实 uvicorn + 真实 Chromium，
与 After **同一后端场景**：一条消息同时触发 3×`ignored` + 1×`failed`）：

```text
{ "streamRows": ["day-sep", "msg", "msg outcome"],
  "summaryRows": 0,                    ← 基线根本没有摘要行
  "bodyTextMentionsIgnored": false,     ← 「自判忽略」在整页正文里一次都不出现
  "anyFoldToggle": 0,                   ← 没有任何折叠控件
  "failedRows": 1 }
```

截图 `red-baseline.png`：三条 `ignored` 记录在库里、在事件流里，但页面上零呈现。

**After**（同一场景，同一组探针，本 worktree）：

```text
折叠态：
  streamRows: [day-sep, msg, "msg outcome-summary", "msg outcome"]     ← 消息 1 条，+1 摘要行，+1 failed 块
  messageRows: 1                       ← 摘要行不占 data-seq，不被数进「几条消息」
  summaryRows: 1, failedRows: 1
  summary: { baseSeq: "1", role: "status", hasDataSeq: false, toggle: "BUTTON",
             ariaExpanded: "false", ariaControls: "outcome-summary-1",
             text: "▸阿墨、云舟 等 3 人没有回应 · 展开看原因",
             why: "自判忽略（正常的协议选择，不是失败）",
             edgeColor: "oklch(0.78 0.02 86)" }            ← --border-strong，不是 --error
  failedBlocks: [{ tag: "未回应", state: "这一轮失败",
                   reason: "模型调用失败：…（脚本化替身固定此失败）",
                   edgeColor: "oklch(0.532 0.128 25.9)" }] ← --error（失败，免点击可读）
展开态（Enter）：
  ariaExpanded: "true", detailsId: "outcome-summary-1"
  lines: [{ status: "ignored", head: "阿墨自判忽略13:04", reason: "脚本化替身：自判无关",
            stateColor: "oklch(0.52 0.024 300)", reasonColor: "oklch(0.46 0.03 300)" }, ×3]
```

**键盘可达（真实按键，不是读代码）**：

```text
Tab ×6 → focus 落在 BUTTON.msg-outcome-summary-toggle（:focus-visible 2px 焦点环）
Enter  → aria-expanded: "true"， 展开子树出现，焦点仍在同一个控件的新节点上
Space  → aria-expanded: "false"，展开子树消失，焦点仍在同一个控件上
刷新   → aria-expanded 复位为 "false"；localStorage 只有既有的 worksplice.activeChannel
```

**其余三个场景**（同一条消息下的三类正常选择的措辞与依据）：

```text
单成员    ：「▸孤舟没有回应 · 展开看原因」/ why「自判忽略（正常的协议选择，不是失败）」
被合并    ：「▸回声没有回应 · 展开看原因」/ why「重复唤醒被合并进正在进行的那一轮（不是失败）」
预算耗尽  ：「▸砚台、长庚没有回应 · 展开看原因」/ why「预算已用尽，本轮零投递（不是出错，成员状态点仍空闲）」
```

**门禁**：`179 passed, 1 skipped`（基线 `174 passed, 1 skipped`；本票新增 5 条）·
`ruff check`/`format --check` 全过 · `mypy` 26 files 无问题 · `pyright` 0 errors。
新增用例只断言 HTTP/SSE 外部可观察行为（状态码 / JSON / SSE 帧），前端呈现用真实 Chromium
手跑取证；逐条读数见 `app/specs/issue-18-acceptance.md` §4。

**双轴 code-review**：Standards 轴已跑（本仓既有范式 + Fowler 基线），findings 逐条处置见
`app/specs/issue-18-acceptance.md` §9；Spec 轴因上游网络错误（`connection reset by peer`）重跑。

## Merge Danger

**Door:** two-way

纯前端呈现 + 测试与取证文档；后端 `.py` 一个字节未动，数据库结构、`round_outcome` 形状、
SSE 载荷、状态点推导都没有变化。回滚就是回滚这两个 commit，无数据迁移、无不可逆副作用。

**Blast Radius:** narrow

影响面是消息流一处（`renderMessages` + 轮次结果的取数与消费）。摘要行不占 `seq`、
没有 `data-seq`、没有头像、`role="status"`，因此不会污染任何「消息条数 / Agent 消息数」的统计；
它只消费既有的只读端点与 SSE 事件，不新增写路径、不新增状态源。非 failed 三态**不使用** `--error`
或任何失败视觉（`--border-strong` / `--muted` / `--faint`），失败块**仍**用 `--error`；未新造色位、
无硬编码 hex/rgb。折叠态是内存里的会话状态，不进 `localStorage`（仓硬约束）。与 `#9` 并行改动
同一对文件的**不同区域**（composer vs 消息流），`git diff -U0 | grep composer|placeholder|mention` 为空。

**已知遗留**（细节在 `app/specs/issue-18-acceptance.md` §10）：`replied` 不挂摘要行（用户裁定）；
`ignored` 的 append-only 记录数**不因呈现而减少**（只在呈现层收成一行 + 折叠）；
`merged` 天然是单成员记录，因此浏览器里取证不到它「多成员收成一行」那一面（该面由 3×`ignored`
与 2×`budget-exhausted` 取证）。

### Decisions（记录在案，以免被当成越界）

1. **#1 Out of Scope 的边界解释（用户本轮显式授权，原文记录）**：#1 的 Out of Scope 写了
   「成本看板与可观测页面（轮次结果落库即可，不做页面）」，#3 的 review 曾把「把轮次结果渲染进消息流」
   判为越界并删除（`app/specs/issue-3-acceptance.md` 的 S2 条）。本票经用户显式授权**收窄该禁令**：
   允许「消息流内、每消息至多一行的就地摘要」（与 `failed` 的既有呈现同层、同锚点 `base_seq`）；
   **仍然禁止**「成本看板／轮次全览页面／常驻预算可视化」（用户明确未选频道头预算条）。
2. **`replied` 不进摘要行（人定，选 A）**：依据 #1「一轮 ↔ 至多一条频道消息」——那条回复消息本身
   就是 `replied` 的可见事实。给几乎每条 Agent 回复都挂一行「已回复」正是摘要行方案要避免的噪声。
   coordinator 同时明示**不选**折中方案（展开态也不列 `replied`）。
3. **顺序与措辞由实现决定**（裁定授权）：摘要行在 failed 块**之前**（失败文本更重，放最后离触发消息
   更近）；稳定序用后端返回序（`finished_at, id` 升序）；折叠控件用原生 `<button>`。
