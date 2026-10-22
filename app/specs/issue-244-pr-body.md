## Summary

自主模式频道的运行面板按设计必然全空，但入口没有解释。本 PR 只改空态分支与文案：

```diff
renderRunPanel()
+ autonomousChannel = runPanelChannelMode() === 'autonomous'   // 只读 state.channelConfigs
  runs empty
-   t('runPanel.runsEmpty')
+   autonomous ? t('runPanel.runsEmptyAutonomous') : t('runPanel.runsEmpty')
  runPanelPlanSection(live, autonomousChannel)
-   t('runPanel.planEmpty')
+   autonomous ? t('runPanel.planEmptyAutonomous') : t('runPanel.planEmpty')

openRunPanel()
+ 频道面板且配置未缓存 → loadTaskChannelConfig(channelId)
+ .then(面板仍开着且同一频道 → renderRunPanel())
```

- 新增两条文案（zh-CN / en 各一份）：运行与分派解释「自主模式没有队长 ⇒ 不产生 team 运行 /
  分派」，并引导到既有「频道设置 → 协作模式」切队长制；PLAN 解释「自主频道对话不产生 Plan」，
  并指向任务线程头的「任务运行面板」。
- 成员事件 / SOP 文案未动；任务线程面板与 team 频道空态未动。
- 配置读取失败或尚未读回时保持通用空态，不把「读不到」误报成自主模式。前端不推算模式。

涉及文件：`app/alibot/studio/app.js`、`app/alibot/studio/i18n.js`、
`app/tests/run_panel_ui.test.cjs`、`app/specs/issue-244-acceptance.md`。

## Evidence

**Before**（隔离基线 `16f6400` 回放当前测试）：

```text
$ ./scripts/red-evidence.sh 16f6400 app/tests/run_panel_ui.test.cjs
✖ 自主模式频道：运行与 Plan 空态解释原因与去处… — 频道面板打开时读一次配置 0 !== 1
✖ 频道配置读取失败：退回通用空态，不误报为自主模式 — 尝试读取配置 0 !== 1
✖ 运行面板的新词条在两份词条表里都取到值 — runPanel.runsEmptyAutonomous 缺失
ℹ pass 13  fail 3
```

**After**：

```text
$ node --test app/tests/run_panel_ui.test.cjs
✔ 自主模式频道：运行与 Plan 空态解释原因与去处…
✔ team 模式频道：沿用原空态文案，不出现自主模式解释
✔ 任务线程运行面板：即使所属频道是自主模式，空态也沿用原文案
✔ 频道配置读取失败：退回通用空态，不误报为自主模式
ℹ tests 16  pass 16  fail 0

$ ./scripts/check.sh --studio          → 门禁全绿（273 passed）
$ node --test app/tests/*.test.cjs     → 273 passed / 0 failed
```

真实浏览器（ego-browser，后端以本 worktree 的 `PYTHONPATH` 启动）：

- 自主频道：运行与分派与 PLAN 显示上述解释文案；成员事件 / SOP 原文案
  （`/tmp/alibot-rp-evidence-20261023/shots/01-autonomous-channel-run-panel.png`）
- 队长制频道：四段仍是原文案、无自主解释
  （`/tmp/alibot-rp-evidence-20261023/shots/02-team-channel-run-panel.png`）
- 自主频道下的任务线程面板：空态仍是原文案
  （`/tmp/alibot-rp-evidence-20261023/shots/03-task-thread-run-panel.png`）

详细判据、读数与截图哈希见 `app/specs/issue-244-acceptance.md`。

## Merge Danger

**Door:** two-way

纯前端空态文案与分支；回滚即恢复原文案。

**Blast Radius:** local

只影响 Studio 运行面板的空态显示；不改后端、API、数据库、ADR 与 CSS，不新增依赖。

Closes #244
