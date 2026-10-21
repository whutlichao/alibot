# 验收记录：#210 多个 Agent 同时回复时 typing 行只显示一个名字、停止按钮文案重复

**票型**：实施票（缺陷修复 + 回归测试）｜**门禁**：G-impl（合并由 coordinator 执行；本票不合并、不 push main）
**Ticket**：[GitHub issue #210](https://github.com/whutlichao/alibot/issues/210)
**base**：`730b9c3`（main @ 派活时 HEAD）｜**branch**：`issue-210-impl`
**worktree**：`/Users/apple/orca/workspaces/alibot/issue-210-stop-buttons`
**代码提交**：`197b0c2`（修复 + 回归用例）｜本文件与 PR 正文随后续提交入 PR
**PR**：[#211](https://github.com/whutlichao/alibot/pull/211)（base `main`，head `issue-210-impl`）
**采集时间**：2026-10-21｜**环境**：macOS（aarch64）、Python 3.11.13（仓库根 `.venv`）、Node v25.0.0
**改动面**：`app/alibot/studio/app.js`（+24 / −8）、`app/alibot/studio/i18n.js`（+6 / −4）、
`app/tests/sse_events_ui.test.cjs`（+112 / −12）、`app/specs/issue-210-acceptance.md`（新增）

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.agents/skills/diagnosing-bugs/SKILL.md`、`/Users/apple/.agents/skills/tdd/SKILL.md`、
`/Users/apple/.agents/skills/pr/SKILL.md`。

| diagnosing-bugs Phase | 本票实际做的事 |
| --- | --- |
| 1 构造反馈回路 | 回归级回路：`node --test app/tests/sse_events_ui.test.cjs`（本文件既有 vm + fetch/DOM 替身，真入口 `handleStreamEvent`）。先改/写 7 条用例，在**基线副本** `/tmp/issue-210-red/` 上首跑即红；同一份用例在本分支转绿。单次 ~0.15s，无网络、无时间依赖。 |
| 2 复现 + 最小化 | 最小场景 = 同一 target 两条 `REPLY_START`（`r1/mem-1` + `r2/mem-2`）。砍掉后端、浏览器与真实模型，只留 DOM 入口；两条事件即复现「两个按钮文案相同 + typing 行只有一个名字」。 |
| 3 假设（先列后验） | H1 typing 行只读 `state.typing[target.id]`（单槽）⇒ 预测双人时只剩后到的名字；**成立**（红测实际 `'第二位 正在回复…'`）。H2 按钮可见文案硬编码 `typing.stop` ⇒ 预测两个按钮文本相同；**成立**（红测实际 `['停止本轮','停止本轮']`）。H3 `aria-label` 已带名字、只有可见文案缺 ⇒ 预测 aria 断言在基线也是对的；**成立**（既有 `typing.stopNamed`）。H4 任务线程走另一套渲染 ⇒ 预测 `#taskTypingLine` 行为不同；**否**（`renderTyping()` 两条线共用 `renderTargetTyping()`）。 |
| 4 仪器化 | 探针 = 基线副本上的 DOM 读数（`line.textContent`、按钮 `textContent`、`data-reply-id`、`aria-label`）与 fetch 请求列表；一次只改一个变量。无 `[DEBUG-...]` 插桩进仓。 |
| 5 修复 + 回归测试 | 先红后绿：用例先在基线红，再改 `renderTargetTyping()` 与两份词条，同一份用例转绿（§4）。 |
| 6 清理 | 一次性基线副本只在 `/tmp/issue-210-red/`，不入仓；worktree 无临时代码、无调试输出。 |

`tdd`：垂直切片按「单条按钮文案 → 双人 typing/按钮 → REPLY_END 收敛 → 同成员多轮次 → 任务线程 → 回退窗口」逐条推进；
每次先让当前切片红，再改到绿。`pr`：PR 正文按仓库 `app/specs/*-pr-body.md` 的 Summary / Evidence / Merge Danger 结构。

---

## 1. 根因

缺陷在 `app/alibot/studio/app.js` 的 `renderTargetTyping()`。修复前：

```js
const memberId = target ? state.typing[target.id] : null;          // 单槽：最后一次 typing / REPLY_START
const agent = state.agents.find((item) => item.id === memberId);
container.textContent = agent ? t('typing.working', { name: agent.display_name }) : '';
...
Object.entries(state.activeRounds[target.id] || {}).forEach(([replyId, activeMemberId]) => {
  const button = el('button', 'btn small', t('typing.stop'));      // 可见文案硬编码「停止本轮」
  button.dataset.replyId = replyId;
  button.setAttribute('aria-label', t('typing.stopNamed', { name: outcomeMemberName(activeMemberId) }));
});
```

- `state.typing[target.id]` 只有一个槽位，后到的 `REPLY_START` / `typing` 事件会覆盖先到的；两个 Agent 同时回复时，
  typing 行只显示后一个名字。
- 停止按钮虽然按 `state.activeRounds[target.id]` 的每个 `reply_id` 各渲染一个（数据结构本来就支持多轮次），
  但可见文案统一取 `typing.stop`（「停止本轮」），于是并排出现两个一模一样的按钮；成员名只藏在 `aria-label` 里，
  视觉上完全无法区分。
- 频道 / DM 走 `#typingLine`，任务线程走 `#taskTypingLine`，两条线都由 `renderTyping()` 调同一个
  `renderTargetTyping()`，所以缺陷在三处一致出现。

后端事实无需改动：`agent_event.REPLY_START` / `round.active` 已经以 `reply_id` 为键给出 `<reply_id, member_id>` 对，
同一 target 可以同时有多条在飞轮次（issue #210 根因段已确认）。

---

## 2. 修复

```text
renderTargetTyping(container, target)
  memberIds = activeRounds[target.id] 的 values，按插入顺序去重
  若 memberIds 为空且 state.typing[target.id] 有值 → 用它兜底（REPLY_START 尚未到达的窗口）
  names = memberIds.map(outcomeMemberName)
  typing 文本 = t('typing.working', { name: names.join(t('typing.nameSeparator')) })
  文本用 document.createTextNode 追加（后面还要 append 按钮，文本节点与按钮共同构成可观察文本）

  对 activeRounds 的每个 [replyId, memberId]：
    可见文案 = t('typing.stopNamedShort', { name: outcomeMemberName(memberId) })
    aria-label = t('typing.stopNamed', { name: outcomeMemberName(memberId) })   # 语义不变
    点击 → POST /api/targets/{target}/rounds/{replyId}/interrupt → finishRoundFor(target, replyId)
    409 → finishRoundFor(target, replyId)；!state.online → disabled
```

词条改动（两份表键集与占位符一致）：

| 键 | zh-CN | en | 说明 |
| --- | --- | --- | --- |
| `typing.nameSeparator` | `、` | `, ` | 新增；多名字拼接 |
| `typing.stopNamedShort` | `停止 {name}` | `Stop {name}` | 新增；按钮可见文案 |
| `typing.stopNamed` | `停止 {name} 的本轮` | `Stop the current round for {name}` | 保留；`aria-label` |
| `typing.stop` | — | — | 删除；已无引用 |

保持不变的语义：

1. 一个按钮只中断它自己的 `reply_id`（端点与请求形状未动）。
2. `409` 时移除该按钮；`state.online === false` 时按钮 disabled。
3. 同一成员有多条在飞轮次：名字去重后只显示一次，按钮仍按轮次各一个。
4. 频道、DM、任务线程共用 `renderTargetTyping()`，三处行为一致。

刻意未动：后端 Python、API / SSE 事件形状、`reply_id` 的逐轮中断语义、`CONTEXT.md` / ADR / CI 配置。

---

## 3. 用例（`app/tests/sse_events_ui.test.cjs`，本文件 14 条）

| # | 用例 | 断言的外部可观察面 |
| --- | --- | --- |
| 1 | 当前 reply_id 的停止按钮可见文案带成员名，aria-label 说明停的是谁的本轮 | typing 行含「小明 正在回复…」；按钮文本 `停止 小明`；`aria-label` `停止 小明 的本轮`；点击发 `/rounds/reply-stop/interrupt`，成功后指示清空 |
| 2 | 两个成员同时回复时 typing 行列出两个名字，停止按钮各自带名字且只中断自己 | typing 行含 `小明、第二位 正在回复…`；两个按钮 `data-reply-id` 为 `r1/r2`、文本 `停止 小明` / `停止 第二位`；点 `r1` 只请求 `r1`，剩下 `r2`，typing 收敛到 `第二位` |
| 3 | 任务线程的 typing 行与频道共用同一份渲染 | `#taskTypingLine` 文本同为 `小明、第二位 正在回复…停止 小明停止 第二位`，两个按钮按轮次各一个 |
| 4 | 一个成员 REPLY_END 后 typing 行与停止按钮同步收敛到剩下的人 | `REPLY_END(r1/mem-1)` 后文本 `第二位 正在回复…停止 第二位`，只剩 `r2` |
| 5 | 同一成员有多条在飞轮次：名字只显示一次，停止按钮仍按轮次各一个 | 文本 `小明 正在回复…停止 小明停止 小明`；点掉一个后剩 `r2` |
| 6 | REPLY_START 尚未到达时，typing 事件仍按那一个成员显示且没有停止按钮 | 回退窗口显示 `小明 正在回复…`，无按钮 |
| 7 | 中断一轮只移除它的停止按钮，同 target 的其他成员继续显示（既有用例更新） | 先含两个名字；`round.interrupted(r1)` + `typing inactive` 后只剩 `r2`，文本收敛为 `第二位 正在回复…停止 第二位` |
| 8 | 重连快照恢复当前轮的停止按钮，空快照清掉旧指示（既有用例更新） | 按钮文本 `停止 小明`；空快照清空指示 |

测试替身的一处校正（同一文件内）：`FakeNode.appendChild()` 原先会清掉此前 `textContent` 赋值留下的文本，
与真实 DOM 不符，会让「名字 + 按钮」永远无法同时观察。现在 `appendChild` 不再清 `_textContent`，
与真实 DOM 的文本节点语义一致；本文件全部 14 条用例在此语义下通过。

---

## 4. 红 → 绿

### 4.1 红（基线 `730b9c3`，隔离副本）

```sh
$ rm -rf /tmp/issue-210-red && mkdir -p /tmp/issue-210-red
$ git archive 730b9c3 app/alibot/studio app/tests | tar -x -C /tmp/issue-210-red
$ cp app/tests/sse_events_ui.test.cjs /tmp/issue-210-red/app/tests/
$ cd /tmp/issue-210-red && node --test app/tests/sse_events_ui.test.cjs
```

实际读数：`tests 14`、`pass 7`、`fail 7`。关键失败行：

```text
✖ 当前 reply_id 的停止按钮可见文案带成员名…
  actual: '停止本轮'
  expected: '停止 小明'

✖ 两个成员同时回复时 typing 行列出两个名字…
  AssertionError: 两个停止按钮的可见文案必须各自带成员名，不能一模一样
  actual: [ '停止本轮', '停止本轮' ]
  expected: [ '停止 小明', '停止 第二位' ]

✖ 任务线程的 typing 行与频道共用同一份渲染…
  actual: '第二位 正在回复…停止本轮停止本轮'
  expected: '小明、第二位 正在回复…停止 小明停止 第二位'

✖ 中断一轮只移除它的停止按钮…
  AssertionError: The input did not match the regular expression /小明、第二位 正在回复…/
  actual: '第二位 正在回复…停止本轮停止本轮'
```

失败信息同时指向 ticket 的两个症状：**两个按钮文案相同**（`['停止本轮','停止本轮']`）与
**typing 行只显示一个名字**（`'第二位 正在回复…'`）。

### 4.2 绿（本分支 `197b0c2`）

```sh
$ node --test app/tests/sse_events_ui.test.cjs
ℹ tests 14
ℹ pass 14
ℹ fail 0
```

同一份用例文件、同一入口；仅 `app/alibot/studio/app.js` 与 `i18n.js` 不同。

---

## 5. 全量门禁（worktree 根，`scripts/check.sh` 全量）

```sh
$ bash scripts/check.sh
== pytest（全量）
1046 passed, 10 skipped in 598.92s (0:09:58)
== ruff check
All checks passed!
== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
（通过，无输出）
== mypy（仓库根）
Success: no issues found in 114 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 225
ℹ pass 225
ℹ fail 0
ℹ skipped 0

门禁全绿
```

`i18n_ui.test.cjs` 单独跑：`53 pass / 0 fail`（两份词条键集、占位符一致、`app.js` 裸中文扫描均通过）。

---

## 6. 交付物与遗留

- 分支 `issue-210-impl`，代码提交 `197b0c2`；验收文档与 PR 正文随后续提交入 PR。
- 改动文件：`app/alibot/studio/app.js`、`app/alibot/studio/i18n.js`、`app/tests/sse_events_ui.test.cjs`、
  本文件。
- worktree 提交后 `git status` 干净；未 push main、未合并。
- **刻意未跑浏览器端到端**：双人并发回复需要真实付费模型同时被唤醒。本票的 DOM 用例走真入口
  `handleStreamEvent`，已覆盖渲染面（typing 文本、按钮可见文案、`data-reply-id`、`aria-label`、中断请求）；
  票面亦注明本票不强制浏览器端到端。未触碰用户正在运行的 `127.0.0.1:8788` 实例与数据目录。
- 遗留：无。合并由 coordinator 在 G-impl 执行。
