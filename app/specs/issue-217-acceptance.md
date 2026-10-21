# 验收记录：#217 待审批只在任务卡片可见——频道指针消息行也要有角标

**票型**：实施票（新行为 + 回归测试）｜**门禁**：G-impl（合并由 coordinator 执行；本票不合并、不 push main）
**Ticket**：[GitHub issue #217](https://github.com/whutlichao/alibot/issues/217)
**base**：`d138d3f`（main @ 派活时 HEAD）｜**branch**：`issue-217-approval-badge`
**worktree**：`/Users/apple/orca/workspaces/alibot/issue-217-approval-badge`
**采集时间**：2026-10-21｜**环境**：macOS（aarch64）、Python 3.11.13（worktree `.venv` 符号链接到主工作区
`.venv`）、Node v25.0.0
**改动面**：`app/alibot/studio/app.js`（+49 / −9）、`app/alibot/studio/app.css`（+7 / −0）、
`app/tests/approval_dom_harness.cjs`（+15 / −3）、`app/tests/task_pointer_ui.test.cjs`（+434 / −1）、
本文件与 `app/specs/issue-217-pr-body.md`（新增）

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.agents/skills/implement/SKILL.md`（含其指向的 `tdd`；收尾的 `code-review` 与
`pr` 见 §7 / §8）。`implement` 要求「先读票面、按 TDD 的垂直切片推进、定期跑类型检查与单文件用例、
最后跑双轴 review」，本票的实际映射：

| implement 步骤 | 本票实际做的事 |
| --- | --- |
| 读票面 | `gh issue view 217`（标题：「fix(studio): 待审批只在任务卡片可见——频道指针消息行也要有角标」），逐条抄成 §1 的判据表 |
| tdd 垂直切片 | 一次只推一条判据：先写 (a)(b)(c) 三条（红）→ 实现角标 → 绿；再写 (d) live 帧（红）→ 接到同一收口出口 → 绿；再写 (e) 600s 过期（红）→ 绿；最后补 (f) 点击面与「重铺不丢草稿 / pending」 |
| 单文件用例 + 类型检查 | 每次改完跑 `node --test app/tests/task_pointer_ui.test.cjs`；定稿跑 `scripts/check.sh --studio`（含 ruff / mypy / pyright） |
| 双轴 review | Standards + Spec 两个并行子代理，结论见 §7 |

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面 / spec） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 指针消息行在该任务 target 有 pending 时显示 `.card-approval`，文本取 `board.approvalBadge`、aria 取 `board.approvalBadgeAria`；**不新增词条** | `app/alibot/studio/app.js` `appendTaskRefChip()` 末尾；角标本体收敛到 `approvalBadgeNode(task, odId)`（`taskApprovalBadgeNode()` 变成它的薄封装）；`i18n.js` **未改** | `node --test app/tests/task_pointer_ui.test.cjs` | `tests 13 / pass 13 / fail 0`；断言 `badge.textContent === t('board.approvalBadge', {count:2})`、`aria-label === t('board.approvalBadgeAria', {title:'修一处样式',count:2})`、`data-od-id === 'message-approval-badge-msg-pointer'` |
| 2 | 角标不是第二个点击目标：无 `data-open-task` / `role` / `tabindex`，点击继续走既有 chip 与 `#messageList` 委托 | 同上（`el('span', ...)`，只设文本 / `aria-label` / `data-od-id`） | 同上 | 断言 `tagName === 'SPAN'`、`data-open-task === null`、`role === null`、`tabindex === null`；点角标本身 **0** 次 `/api/tasks/t-1/messages`，点既有 chip **≥1** 次 |
| 3 | 归属严格：count 只按 `pointedTask.target_id`；频道 target / 别的任务的 pending 不出现；`pointedTask` 为 null 不画 | 同上（`approvalBadgeNode(pointedTask, ...)` 内部走既有的 `taskPendingApprovalCount()`） | 同上 | 「归属严格」用例：快照里同时给 `ch-1`（频道 target）与 `tg-2`（别的任务）的 pending ⇒ 指针行 0 个角标，而 `task-approval-badge-t-2` 照常出现（读数确实到了，只是没串台） |
| 4 | 派生集合变化时消息流跟着重铺：live 帧出现/消失、600s 过期消失，且**不重新请求** `/api/messages`；**不能**每 12s 无条件重铺 | `taskApprovalSignature()` 的读域与 `taskById()` 对齐（`activeTask` + `tasks` + `boardTasks`）；`syncApprovalBadges()` 的 signature 变化分支里重铺消息流（`renderMessages({ approvals: false })`，只在消息视图里；否则仍只 `renderTabs()`） | 同上 | live 帧用例：`approval_required` → 角标出现；`approval_resolved` → 消失；期间 `callsTo('/messages')` 计数不变。600s 用例：`clock.now = NOW + 600_000` 后跑 1s 倒计时那一支 ⇒ 角标消失。「未变化不重铺」由两条用例钉住：`签名未变化的周期刷新不重铺消息流`（节点对象同一）与 `一次签名变化只把频道审批板铺一次`（整板重建恰好一轮）；两条都做过变异验证（见 §7.3） |
| 5 | 样式：`.card-approval` 已是行内 pill；只在 `app.css` 加最小对齐规则、只消费既有 token、**不改** `.card-approval` 本身 | `app/alibot/studio/app.css` 新增 `.msg-body > .card-approval`（`margin-top` / `margin-left` 用 `--sp-2`，`vertical-align`） | `grep -n "card-approval" app/alibot/studio/app.css` | 原有 `.card-approval` 规则**一字未动**（仍在 `#209` 那一段），新增规则只命中消息体里的角标 |
| 6 | 中英两份词表都不新增键 | `i18n.js` **未改** | `git status --short app/alibot/studio/i18n.js` | 空输出（文件未改） |
| 约束 | 不改后端 / 不碰 schema / 不覆盖 `task_ref` 行 / 不加横幅 / 不新开测试框架 | `git status --short` | 同左 | 只有 4 个文件改动，全部在 Target 列内；`app/alibot/**/*.py` 无改动 |

### 1.1 与 #209 的判据关系（复用，而非第二套）

「N 是多少」只有一条读数：`pendingApprovalCountForTarget(targetId)` → `taskPendingApprovalCount(task)`。
本票没有新增任何计数路径，只是把同一个 `approvalBadgeNode()` 挂到消息行上；于是**卡片与消息行的 N
永远相等**（浏览器读数里两者都是「待审批 1」，见 §4）。反向也成立：`#209` 已有的「频道 target 不串到
任务面」在本票里变成双向——频道 target 的 pending 也不得出现在指针消息行。

---

## 2. 先红后绿

### 2.1 红：隔离基线副本（未实现）

```console
$ scripts/red-evidence.sh HEAD app/tests/task_pointer_ui.test.cjs
基线：HEAD (d138d3f)
测试：app/tests/task_pointer_ui.test.cjs（取自当前工作区）
基线 app.js md5：bb30c379f3a55801d50f15ee1dc23783
✖ #217 指针消息行的角标：有 pending 时出现，文本与 aria 名复用任务卡片同一对词条
✖ #217 live approval_required / approval_resolved 让角标出现与消失，且没有新的 /api/messages 请求
✖ #217 越过 requested_at + 600s 后，下一次秒级 tick 让角标消失
✖ #217 重铺消息流保留 composer 草稿与 pending 消息（沿用既有 renderMessages，无新增保存/恢复）
✖ #217 指针消息行仍走既有 chip：点 chip 打开任务线程，点角标本身不触发任何打开
✖ #217 任务只在看板快照里（不在 state.tasks）时，live 帧仍让指针消息行的角标出现
✖ #217 一次签名变化只把频道审批板铺一次（消息流的重铺不重复铺它）
✖ #217 签名未变化的周期刷新不重铺消息流（同一批节点还在）
ℹ tests 13   ℹ pass 5   ℹ fail 8
```

红读数要点：

- 既有 5 条指针消息用例（`#198`）在基线上**全绿** ⇒ 装置与接缝没被新用例破坏；
- 8 条 #217 用例全红 ⇒ 缺的正是「角标本体」「消息流参与同一条重铺出口」「点角标不打开任何东西」，
  以及 review 之后补的两条读域/重铺边界用例（基线上没有这些实现，自然红）；
- 两条**本来就该绿**的用例（无 pending 时不画、归属不串台）在基线上也是绿的——它们钉的是**不该发生**的
  行为，红不了是正常的；它们的作用是防止实现「一加就到处加」。

### 2.2 绿：同一批用例

```console
$ node --test app/tests/task_pointer_ui.test.cjs
✔ 频道指针消息渲染出进入任务线程的 chip，文案带任务线程消息数，点击打开线程
✔ 建任务成功后吸收返回的 message：流里立刻出现该行，页签计数 +1
✔ 任务不在快照里时 chip 的计数显示 0，不编造条数
✔ #217 指针消息行的角标：有 pending 时出现，文本与 aria 名复用任务卡片同一对词条
✔ #217 没有 pending 时指针消息行完全不渲染角标
✔ #217 归属严格：频道 target 与别的任务的 pending 都不让这一行出现角标
✔ #217 live approval_required / approval_resolved 让角标出现与消失，且没有新的 /api/messages 请求
✔ #217 越过 requested_at + 600s 后，下一次秒级 tick 让角标消失
✔ #217 重铺消息流保留 composer 草稿与 pending 消息（沿用既有 renderMessages，无新增保存/恢复）
✔ #217 指针消息行仍走既有 chip：点 chip 打开任务线程，点角标本身不触发任何打开
✔ #217 任务只在看板快照里（不在 state.tasks）时，live 帧仍让指针消息行的角标出现
✔ #217 一次签名变化只把频道审批板铺一次（消息流的重铺不重复铺它）
✔ #217 签名未变化的周期刷新不重铺消息流（同一批节点还在）
ℹ tests 13   ℹ pass 13   ℹ fail 0
```

受影响的两份用例一起跑（票面指定）：

```console
$ node --test app/tests/approval_badge_ui.test.cjs app/tests/task_pointer_ui.test.cjs
ℹ tests 21   ℹ pass 21   ℹ fail 0
```

---

## 3. 门禁

```console
$ scripts/check.sh --studio          # exit=0
== pytest（Studio 窄档：审批 HTTP 契约 + 前端相关静态守卫）
20 passed in 2.26s
== ruff check            All checks passed!
== ruff format --check   271 files already formatted
== mypy（仓库根）        Success: no issues found in 114 source files
== pyright               0 errors, 0 warnings, 0 informations
== node --test           ℹ tests 247   ℹ pass 247   ℹ fail 0
门禁全绿
```

- 本地**不跑** pytest 全量（票面：本票不跑；CI 会跑同一脚本）。
- diff 只落 `app/alibot/studio/{app.js,app.css}` 与 `app/tests/*.cjs` + 两份 spec 文档，符合 Studio 窄档
  的适用面；`i18n.js` 与全部 `.py` 未改。

---

## 4. 真实浏览器读数

环境：隔离数据目录 `/tmp/issue217-data2`（**不是** `~/Alibot`），本地服务 `127.0.0.1:8792`
（`python -m alibot --data-dir /tmp/issue217-data2 --port 8792`），浏览器用 **ego-browser**
（`ego lite` 0.6.0.12；本机可用，因此**没有**回退 Playwright）。

隔离数据目录里构造的现场：

```console
$ curl -s -X POST http://127.0.0.1:8792/api/channels -d '{"name":"设计系统重构"}'
{"channel":{"id":"ch_8b1d36b57883", …}}
$ curl -s -X POST http://127.0.0.1:8792/api/channels/ch_8b1d36b57883/tasks -d '{"title":"整理发布清单"}'
task_25e087e6be12  tgt_18a9d8feb50a  msg_7a3e19fbe2e1
# 一个 task target 的 pending 审批（站内没有 Agent 可跑，直接在隔离库里插一条待决行）
$ sqlite3 /tmp/issue217-data2/alibot.db "INSERT INTO approval_request(... target_id='tgt_18a9d8feb50a' …)"
$ curl -s "http://127.0.0.1:8792/api/approvals?state=pending"
[('reply_217_final', 'tgt_18a9d8feb50a')]
```

### 4.1 角标出现：可见文本、aria 名、计算样式（原文）

```json
{
  "rowText": "O Owner 事件 19:36 任务已创建：整理发布清单 进入任务线程 · 1 条待审批 1",
  "badgeText": "待审批 1",
  "badgeAria": "「整理发布清单」有 1 条待审批",
  "badgeOdId": "message-approval-badge-msg_7a3e19fbe2e1",
  "badgeTag": "SPAN",
  "badgeOpenTask": null,
  "badgeRole": null,
  "badgeTabindex": null,
  "chipText": "进入任务线程 · 1 条",
  "chipOpenTask": "task_25e087e6be12",
  "badgeDisplay": "inline-flex",
  "badgeColor": "oklch(0.8 0.084 51.9)",
  "badgeFontFamily": "ui-monospace, \"SF Mono\", \"JetBrains Mono\", Menlo, Consolas, monospace",
  "badgeMarginLeft": "4px",
  "boardBadgeText": "待审批 1",
  "badgeCountInRow": 1,
  "chipCountInRow": 1
}
```

截图：`/tmp/issue217-final-badge.png`（不入库）。要点：

- 角标的可见文本 `待审批 1` 与同一任务的**卡片角标**（`boardBadgeText`）逐字相同；
- 颜色 `oklch(0.8 0.084 51.9)` 就是 `--warn-deep` 在暗色档的取值、字体族是等宽——与卡片同一套 token
  （只多出 `margin-left: 4px` = `--sp-2` 的行内对齐）；
- `SPAN` 且 `data-open-task` / `role` / `tabindex` 全为 `null`；行内 chip 仍恰好 1 个、`data-open-task`
  仍指向 `task_25e087e6be12` ⇒ 可点区域没有变成两个。

### 4.2 收口：从 HTTP 收口这条审批后角标消失，且不重新拉消息

```json
{
  "resolveStatus": 200,
  "before": { "badges": 1, "messages": 0 },
  "badgeGone": true,
  "after": {
    "badges": 0,
    "rowText": "O Owner 事件 19:36 任务已创建：整理发布清单 进入任务线程 · 1 条",
    "messagesRequests": 0,
    "approvalRequests": ["/api/approvals/reply_217_final", "/api/approvals?state=pending"],
    "chips": 1
  }
}
```

截图：`/tmp/issue217-final-resolved.png`。`badgeGone: true` 由页面内的 `waitForFunction` 等到，期间
`/messages` 请求数为 **0**——角标的变化只消费 `state.approvals`（快照 + live 帧的同一份镜像）。

### 4.3 更早一轮（同一实现、另一份隔离数据目录 `/tmp/issue217-data` @ 8791）的补充读数

那一轮额外覆盖了三条路径，读数原文如下（文件行号/端口与 4.1/4.2 不同，实现相同）：

- **只靠派生集合重铺就出现**（不刷新页面、不点东西，只等既有 12s 周期刷新）：
  `{"badgeText":"待审批 1","badgeAria":"「整理发布清单」有 1 条待审批","badgeTag":"SPAN","badgeOpenTask":null,…}`
- **live 帧收口**（打开任务线程 ⇒ 页面订阅 task target 的 SSE；从 HTTP 收口 ⇒ 后端发布
  `approval_resolved`）：
  `{"removedByLiveFrame":true,"after":{"pointerBadgeCount":0,"messagesRequests":0,"approvalRequests":3,"threadCards":5}}`
- **本地 600s 上界**（`requested_at + 600s` 到点，故意不依赖 12s 轮询）：
  `{"before":{"badgeText":"待审批 1","badgeAria":"「整理发布清单」有 1 条待审批","count":1},"expiredByLocalBound":true,"after":{"count":0,"rowText":"… 进入任务线程 · 1 条","chipCount":1}}`

对应截图：`/tmp/issue217-pointer-badge*.png`、`/tmp/issue217-live-resolved.png`、
`/tmp/issue217-thread-open.png`（均不入库）。

---

## 5. 「不能每 12s 无条件重铺消息流」是怎么保证的

调用链（改后）：

```text
renderApprovalSurfaces()
  ├─ renderChannelApprovals()      # 频道审批板（既有）
  ├─ renderTaskApprovals()         # 线程审批卡（既有）
  ├─ syncApprovalBadges()          # ← 本票唯一改动处
  │    signature = taskApprovalSignature()        # activeTask ∪ tasks ∪ boardTasks × pending 条数
  │    if (signature === approvalBadgeSignature) return;   # ← 未变化：什么都不做
  │    approvalBadgeSignature = signature
  │    renderTaskBoard()                          # 既有
  │    state.mode === 'channel'
  │      ? renderMessages({ approvals: false })   # 本票新增：只铺消息流（不再重铺审批板）
  │      : renderTabs()                           # 看板视图：消息流是 hidden 的，不碰它
  └─ syncApprovalTicker()
```

四条纪律各有落点：

1. **未变化零重铺**：`signature` 相同就直接 return。12s 周期刷新在快照 `changed === false` 且入口没带
   `render: true` 时**连 `renderApprovalSurfaces()` 都不进**；进了也因为签名相同提前返回。
   用例：`#217 签名未变化的周期刷新不重铺消息流（同一批节点还在）`——两次 tick 后消息行节点仍同一对象。
2. **秒级 tick 不重建卡片**：`refreshApprovalCountdowns()` 只改 `.approval-remaining` 的文本；只有「呈现态
   变了」或「签名变了」才走 `renderApprovalSurfaces()`。600s 过期那一拍正是签名变化，所以角标与卡片同拍消失。
   用例：`#217 越过 requested_at + 600s 后，下一次秒级 tick 让角标消失`。
3. **`renderMessages({ approvals: false })` 不重复铺审批板**：`renderApprovalSurfaces()` 在调
   `syncApprovalBadges()` 之前已经铺过 `#approvalBoard`；不带这个开关的话，同一拍会整板重建**两轮**
   （审批卡里的允许 / 拒绝按钮是点击目标，重建会把正在按的那个换掉——#209 的既有纪律）。
   用例：`#217 一次签名变化只把频道审批板铺一次`，数的是 `.d-sec-title` 被追加的次数；把
   `{ approvals: false }` 去掉后这条用例**转红**（见 §7.3 的变异验证）。
4. **看板视图不碰消息流**：`state.mode === 'board'` 时消息流整个是 `hidden` 的，这一拍只 `renderTabs()`；
   切回消息视图时 `render()` 会照常重铺（读数与这一拍同一个来源）。
   用例：`#217 一次签名变化只把频道审批板铺一次` 的后半段（看板视图下 `#approvalBoard` 仍铺着 1 张
   频道 target 的卡、`hidden === false`）。

**composer 草稿 / pending 消息 / 滚动位置**：三者在 `renderMessages()` 之外（输入框、`state.pending`、
`#viewMessages` 的滚动状态），因此沿用既有 `renderMessages()` 即可，本票**没有**新增任何保存 / 恢复逻辑。
用例：`#217 重铺消息流保留 composer 草稿与 pending 消息`（重铺前后草稿与 pending 都在）。

## 6. 有意取舍

**角标读的是「任务快照里的那个任务」，不是「指针消息自带的字段」**：`taskById(message.task_pointer)`
回退链是 `activeTask → state.tasks → state.boardTasks`。任务不在任何快照里时（`pointedTask === null`）
**不画角标**——如实是「没有读数」，不编造 0。代价：极端时序下（刚建任务、任务快照还没到）指针消息行会
先没有角标，等任务快照到齐后随下一次签名变化出现；这与既有 chip 的计数（任务不在快照里显示 0 条）
口径一致，不另造第二套判定。

**没有做「原地批准」**：角标是纯呈现，审批的决策面仍是 `#approvalBoard` 与线程审批卡（票面「不做」段）。

**没有覆盖任务线程内的 `task_ref` 行**：本票只做频道指针消息（`task_pointer`），`task_ref` 分支一行未改。

---

## 6.5 Answer

**做了什么**：在 `appendTaskRefChip()` 渲染完 `task_pointer` 的「进入任务线程 · N 条」chip 之后，按
`pointedTask.target_id` 读取 pending 条数（与卡片同一个 `taskPendingApprovalCount()`），有则追加一个
`.card-approval` 的 `<span>`——文本取 `board.approvalBadge`、aria 取 `board.approvalBadgeAria`（**两份词表
都没新增键**）。角标是纯呈现（无 `data-open-task` / `role` / `tabindex`），整行可点区域逐字不变。
为了让这条角标随审批状态收口，`syncApprovalBadges()` 的 signature 变化分支里重铺消息流
（`renderMessages({ approvals: false })`，只在消息视图里；看板视图仍只刷页签），并把
`taskApprovalSignature()` 的读域对齐到 `taskById()`（`activeTask` ∪ `tasks` ∪ `boardTasks`）。
样式只加一条作用域受限的 `.msg > .msg-body > .card-approval` 对齐规则（`--sp-2`），`.card-approval` 本身未改。

**发现了什么**：审批派生集合的**读域**与**呈现域**本来就不一致——`taskById()` 会回退到看板快照 / 当前
线程，而 `taskApprovalSignature()` 只看 `state.tasks`。不补这一层，筛选视图或刚打开的线程里角标会「读数
到了但不显示」（双轴 review 抓到并已修，见 §7.2 S1）。另外直接把 `renderMessages()` 接到这个出口会让
频道审批板在同一拍被整板重建两遍（审批卡里的按钮是点击目标，重建会换掉正在按的节点），因此给它加了一个
`approvals: false` 开关并在看板视图下完全不碰消息流——两处都补了会转红的用例（§7.3 变异验证）。

**留下了什么**：本票只做**频道指针消息行**；任务线程内的 `task_ref` 行按票面「不覆盖」一行未改。
`task_pointer_ui.test.cjs` 现在同时承载 #198 的窄装置与 #217 复用的共享审批装置——搬迁老用例属扩大票面，
留作后续清理（§7.1 #3）。真实浏览器读数里 live 帧那条用的是任务线程的 SSE（`handleTaskStreamEvent`）；
频道流那条入口由 #153 起的既有用例覆盖，本票未新增同义路径（§7.2 S9）。PR 打开后**不自合并**，等
coordinator 验收合并。

---

## 7. 双轴 code-review

判据来源：`docs/agents/review-rules.md`（两轴分工：Standards 看越界改动 / 重复形状 / 死代码；Spec 看票面
要求的有没有做全、有没有做票面没要求的事）。两个并行子代理各自跑一遍，**互不抵消**。

### 7.1 Standards 轴（结论：需修后再并 → 硬伤已修完）

| # | 位置 | 发现 | 严重度 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | `app.js` `syncApprovalBadges()` | 改用 `renderMessages()` 后同一拍里 `renderChannelApprovals()` 被调两次（`renderApprovalSurfaces()` 一次 + `renderMessages()` 尾段一次）⇒ 审批板整板重建两轮，审批卡里的按钮会被换成新节点 | 硬 | **已修**：`renderMessages(options)` 新增 `approvals: false` 开关，`syncApprovalBadges()` 带着它调；并补用例 `一次签名变化只把频道审批板铺一次`（去掉开关即转红，§7.3） |
| 2 | `task_pointer_ui.test.cjs` 的 composer 断言 | `harness.elementFor` 未导出 ⇒ `composer` 恒 `null` ⇒ `if (composer)` 让断言整段跳过，是一条**假绿** | 硬 | **已修**：harness 导出 `elementFor`；用例改为先写草稿再重铺，并断言 `composer.value` 仍是原值（不再有 `if` 守卫） |
| 3 | 同一用例文件里的两份 DOM 装置 | 上半自建 FakeNode、下半 `require` 共享装置并改别名；两份替身行为已分叉（`appendChild` 是否清 `_textContent`、选择器是否支持 `[attr="value"]`） | 中 | **部分修 / 不修其余**：共享装置的复用方向是对的（时钟 / 定时器 / live 帧只有一份），但把 #198 的 3 条老用例整体搬迁属于**扩大票面**（Target 明确写「新行为的主用例文件」就是 `task_pointer_ui.test.cjs`）。处置：文件头写清「两份装置的边界与各自唯一性」，并把 #217 组的取用改为显式别名（已做）；搬迁留作后续清理票。 |
| 4 | `showMessages()` 直接调 `renderMessages()` + 装置直塞 `state.messages` | 对照 `narration_ui.test.cjs` 的「走真的那条路径」纪律，这里绕过了 `handleStreamEvent → onStreamMessage → absorbMessage` | 中 | **不修（有意）**：本票判据是「审批派生集合变化时**重铺**消息流」，被观察的是重铺，而不是消息到达；裁决「重铺会不会丢东西」必须在同一个被重铺的 DOM 上对比前后。`#198` 的既有用例已经用 `absorbMessage` 真路径覆盖了「消息怎么进来」，两条正交。已在用例注释里写明这一点。 |
| 5 | `approvalBadgeNode(task, odId)` | 拆分本身值得（两个调用点共用同一节点 / 同一对词条，`syncUnreadBadge` 也有 `odId` 先例），但 `if (odId)` 分支永远为假却允许造出「没有 `data-od-id` 的角标」 | 中 | **已修**：`odId` 改成必填（无守卫），并补 `data-od-id` 断言 |
| 6 | `app.js` 角标 docstring | 「两处不会撞成同一个 `data-od-id`」的**理由**写错（真正防撞的是前缀），占位符也与真实取值不符 | 中 | **已修**：改成「前缀本身保证不撞」，占位符改成真实键 |
| 7 | `'message-approval-badge-' + (message.id \|\| message.seq)` | 真实载荷里 `id` 恒存在，`\|\| message.seq` 让 od-id 有了两种形状，探针没法稳定书写 | 软 | **已修**：改成 `message.id` |
| 8 | `syncApprovalBadges()` 的一行摘要 | 仍写「重铺**看板/页签**」，与实现（消息流）不一致 | 软 | **已修**：改成「看板 / 页签 / 消息流里的角标」 |
| 9 | `app.css` 的 `.msg-body > .card-approval` | 作用域比需要的大：任务线程的回复行也是 `.msg-body`（当前只因线程回复不带 `task_pointer` 才看不出差别） | 软 | **已修**：收窄成 `.msg > .msg-body > .card-approval`，注释写明线程回复行不参与 |
| 10 | composer / pending 那条用例 | 草稿那半是死断言（见 #2），pending 那半是「自己塞 `state.pending` 再断言会渲染」——属既有行为的越层复述 | 软 | **已修**：重写成「重铺前先摆好草稿与 pending → 触发重铺 → 断言两者都还在」，与 #217 的因果直接相连 |
| 11 | `approval_dom_harness.cjs` 文件头 | 写「只补两处」，实际是三处 | 软 | **已修**：改成三处并逐条点名 |
| 12 | `#217 没有 pending 时…` 与第一条开头的断言重合 | 两条都在断言 `pointerBadges.length === 0` | 软 | **不修**：第二条多了「入口 chip 仍在」的反向对照，且它是票面判据 (b) 的独立署名用例；合并会让「没有 pending」这一条失去自己的名字。 |

**Standards 轴的变异验证**（reviewer 自己跑过）：把 `syncApprovalBadges()` 里的重铺改回 `renderTabs()`，
#217 组有 5 条失败 ⇒ 测试确实咬住了这次改动，不是同义反复。

### 7.2 Spec 轴（结论：主体符合，2 项必须修 → 均已修）

| # | 判据 | 结论 | 证据 | 处置 |
| --- | --- | --- | --- | --- |
| S1 | 判据 4 的读域漏洞 | **不符 → 已修** | `taskApprovalSignature()` 只遍历 `state.tasks`，而 `taskById()` 会回退 `boardTasks` / `activeTask`；任务只在前两者时签名不变 ⇒ 消息流不重铺。reviewer 探针：live 帧后 badge=0，手动 `renderMessages()` 后 badge=1。生产可达路径：搜索打开任务、筛选频道视图（`refreshTasks()` 的两次查询） | **已修**：签名读域对齐 `taskById()`（`activeTask` ∪ `tasks` ∪ `boardTasks`，按 target 去重）。新用例 `任务只在看板快照里（不在 state.tasks）时，live 帧仍让指针消息行的角标出现`；把读域改回 `state.tasks` 后该用例**转红**（§7.3） |
| S2 | 判据 5 的越界（看板视图下清空审批板） | **不符 → 已修** | `renderMessages()` 尾段按视图可见性铺 `#approvalBoard`；看板视图下 `activeChannelId` 仍是频道，本票的重铺会在看板视图里把它清空 | **已修**：`syncApprovalBadges()` 只在 `state.mode === 'channel'` 时重铺消息流，否则退回原有的 `renderTabs()`；同时用 `approvals: false` 消除同一拍的双次重建（S2 与 Standards #1 是同一个出口的两面）。用例 `一次签名变化只把频道审批板铺一次` 覆盖 |
| S3 | 判据 1（落点 / 词条 / 不新增键） | 符合 | `i18n.js` 未改；`board.approvalBadge` / `board.approvalBadgeAria` 均为既有键 | — |
| S4 | 判据 2（纯呈现、不承载点击） | 符合 | 逐条断言 `SPAN` / `data-open-task` / `role` / `tabindex`；点角标本身 0 次请求 | — |
| S5 | 判据 3（严格 target 归属） | 符合 | 频道 target + 别的任务的 pending ⇒ 指针行 0 角标；反向对照卡片角标照常 | — |
| S6 | 判据 4a（live 帧更新 + 不重新拉消息） | 符合 | 用例真的比较 `callsTo('/messages')` 的前后计数 | — |
| S7 | 判据 4c（600s 上界） | 符合 | 真驱动 1s 倒计时那一支；`refreshApprovalCountdowns()` 的 `needsRender` 分支确实走到 `renderApprovalSurfaces()` | — |
| S8 | 判据 6（不动后端 / `task_ref` 分支 / 无 toast / 复用装置） | 符合 | `git status` 只有 4 个前端文件 + 2 份 spec 文档；`appendTaskRefChip` 的 `task_ref` 分支一字未改 | — |
| S9 | 非阻塞：缺一条走频道流 `handleStreamEvent` 的 live 入口覆盖 | 部分 | `recordLiveEvent` 由 `handleStreamEvent` 与 `handleTaskStreamEvent` 两个入口共同抵达；本票只覆盖后者 | **不修**：`handleStreamEvent` 的 `payload.target_id \|\| targetId` 归属逻辑是 #153 起的既有代码，`approval_ui.test.cjs` 已覆盖；本票改的是它下游的同一个 `renderApprovalSurfaces()` 出口，再加一条同义路径只会复制既有覆盖。 |

### 7.3 变异验证（reviewer 提出的「测试真的咬住了吗」）

对最终实现做两次变异，确认新增用例不是同义反复：

| 变异 | 命令 | 读数 |
| --- | --- | --- |
| 把 `renderMessages({ approvals: false })` 改回 `renderMessages()` | `node --test app/tests/task_pointer_ui.test.cjs` | `✖ #217 一次签名变化只把频道审批板铺一次（消息流的重铺不重复铺它）`，`tests 13 / pass 12 / fail 1`；还原后 13/13 |
| 把签名读域改回 `state.tasks.forEach(...)` | 同上 | `✖ #217 任务只在看板快照里（不在 state.tasks）时，live 帧仍让指针消息行的角标出现`，`tests 13 / pass 12 / fail 1`；还原后 13/13 |

### 7.4 两轴结论

- **Standards**：需修后再并 → 两条硬伤（重复铺审批板、假绿断言）已修，其余按软 / 中顺手收敛（#3 的搬迁
  与 #4 的入口选择是**有意不修**，理由已写在表中）。
- **Spec**：主体符合，2 项必须修（签名读域、看板视图越界）——均已修，并各自补了会转红的用例。
