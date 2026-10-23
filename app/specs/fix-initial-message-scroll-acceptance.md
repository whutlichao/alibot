# fix-initial-message-scroll 验收（Answer）

- **Status:** Done
- **Branch:** `fix-initial-message-scroll`（Orca 从 `main` 切出的 worktree 分支）
- **PR:** https://github.com/whutlichao/alibot/pull/248
- **Commit:** `428c856 fix(studio): 首次载入工作台时消息流停在最新一条`

## 现象与根因

刷新页面（或重开浏览器）后，工作台会选中记住的频道，但 `#viewMessages` 的消息列表从最老一条
开始显示。根因：`loadWorkspace()` 完成快照读取并 `render()` 重建消息面后没有恢复滚动位置；
既有 `scrollToBottom()` 只在 live 新消息、切 target 与发送消息等路径被调用。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `app/alibot/studio/app.js` | +11 / -3：`loadWorkspace()` 首次成功载入后贴底 |
| `app/tests/initial_scroll_ui.test.cjs` | 新增：两条行为用例（首次载入、失败后重连） |
| `app/specs/fix-initial-message-scroll-acceptance.md` | 本 Answer |
| `app/specs/fix-initial-message-scroll-pr-body.md` | PR body |

未改后端、schema、i18n 词条与其它前端模块；未新增滚动位置追踪状态；`render()` /
`selectTarget()` / `onStreamMessage()` 行为不变。

## 关键 diff 行（`app/alibot/studio/app.js`）

```diff
+// 工作台是否已**成功**载入过一轮。不能用 `state.ready`：它在失败分支也会置 true，
+// 而「首载失败 → 重连成功」的那一次仍属于票面说的「首次成功载入」，必须贴底。
+let workspaceLoaded = false;
+
 async function loadWorkspace(options) {
+  const firstSuccessfulLoad = !workspaceLoaded;
   ...
     await loadPendingApprovals({ render: true });
+    // 整轮读数都落地了才算「成功载入」：中途任何一步抛错都会走 catch，重连那一次才补得上贴底。
+    workspaceLoaded = true;
   } catch (err) {
   ...
   render();
+  // 首次成功载入：消息面刚重建，贴到最新一条。
+  if (firstSuccessfulLoad && workspaceLoaded) scrollToBottom();
   markCurrentRead();
 }
```

## Spec 内部冲突的判定（经 coordinator 定夺）

票面第 2 条是行为正本：「页面首次**成功**载入工作台时，当前 target 的消息流应停在最新一条」。
第 3 条却提示依据 `state.ready` 判断，而它在 `catch`（首载失败）分支也会置 true：首载失败后
由 `scheduleReconnect()` 完成的第一次成功载入会被误判为非首次，继续保留票面要修的现象
（review 已在隔离副本实测 `scrollTop` 仍为 0）。

已向 coordinator 提问并取得定夺：**按第 2 条目标语义实施**——新增模块级「已成功载入过」标记
`workspaceLoaded`（不改 `state.ready` 的既有语义、不跟踪滚动位置），第 3 条视为实现提示。
用例 c 覆盖该路径。

## 红 → 绿证据

**红（基线）：** `scripts/red-evidence.sh main app/tests/initial_scroll_ui.test.cjs`

```text
基线：main (81143dd)
基线 app.js md5：bec25335a3372138ff7e32827d33c05b
证据目录：/var/folders/.../T//alibot-red.iHT1fo
✖ 首次载入工作台后消息流贴底；静默刷新不改变回看位置
  AssertionError: 首次载入后消息流应停在最新一条 — 0 !== 640
✖ 首次载入失败后，重连完成的第一次成功载入仍贴底
  AssertionError: 重连完成的首次成功载入应贴底
ℹ tests 2 / pass 0 / fail 2；exit=1
```

（首轮红证据在基线 `HEAD` = `81143dd` 上取得，证据目录 `/var/folders/.../T//alibot-red.U5UGFm`，
基线 `app.js` md5 同为 `bec25335a3372138ff7e32827d33c05b`；补用例 c 后按最终用例复跑，
证据目录 `alibot-red.iHT1fo`。）

**绿（当前树）：** `node --test app/tests/initial_scroll_ui.test.cjs`

```text
✔ 首次载入工作台后消息流贴底；静默刷新不改变回看位置
✔ 首次载入失败后，重连完成的第一次成功载入仍贴底
ℹ tests 2 / pass 2 / fail 0
```

断言只落在外部可观察行为上：`#viewMessages.scrollTop` 是否等于 `scrollHeight`；
替身把 `scrollHeight` 设成非零值，用非底值验证静默刷新不改写 `scrollTop`。不断言内部调用次数。

## 门禁读数

`scripts/check.sh --studio` → **exit=0，门禁全绿**

```text
pytest（审批 HTTP 契约 + 静态守卫）：20 passed
ruff check：All checks passed!
ruff format --check / mypy / pyright：通过
node --test app/tests/*.test.cjs：tests 276 / pass 276 / fail 0
```

## 双轴 code-review 结论

**Spec 轴：** 无缺失、无 scope creep；发现 1 处目标行为未覆盖（上节冲突）已按 coordinator 定夺修复。
修复后首载失败路径不再滚动空消息面（`workspaceLoaded` 仅在整轮读数成功后置位）。

**Standards 轴：** 未发现共享层被单票需求改写、未新开接缝、无词条/端点变更。

| 发现 | 处置 |
| --- | --- |
| 缺验收文档 | 本文件补上 |
| 分支名 / commit 未挂票号 | 票面明确由 Orca 从 `main` 切分支且本票无外部 issue，按票面执行 |
| `workspaceFetch` 装置写在用例文件内 | 先例见 `approval_badge_ui.test.cjs`；票面限定只改两个文件，不把 fixture 挪进 harness |
| 入口与调用点注释重复 | 已删去调用点重复理由，仅保留一行说明 |
| `state.ready` 语义（首载失败后重连） | escalation 后按 coordinator 定夺 B 实施，并加用例 c |
| `approval_dom_harness.cjs` 顶部用例清单未登记 | 既有漂移，不在本票范围 |
| 重叠载入窄窗口（两次 `loadWorkspace` 同时在飞时都可能判首次） | 票面未要求；概率低，记为已知边界 |

## Merge Danger

**Door:** two-way。**Blast Radius:** minimal。纯前端滚动行为，无 schema / 接口 / 依赖变更；
回滚只需撤掉一处条件调用。
