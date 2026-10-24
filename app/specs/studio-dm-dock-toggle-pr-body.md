## Summary

用户 2026-10-24 反馈：私信窗口里 dock 的开关应该无效或者不显示。私信没有「频道成员」这个 dock 形态，
任务线程 / 运行面板又是独立形态，因此判据按「有没有可显示的形态」走，不按 `isDm` 一刀切：

```diff
 dockAvailable() = Boolean(state.runPanel.open) || dockFallbackMode() !== null

 #btnDockToggle
-  频道 / 私信都可见；点下去私信里会打开一个空面板
+  hidden = !dockAvailable()        // 私信下不出现
 toggleDock()
+  if (!dockAvailable()) return
 commandList()
-  toggle-dock 恒在
+  ...(dockAvailable() ? [toggle-dock] : [])
 render()
+  if (!dockAvailable() && !$('#dock').hidden) setDockOpen(false)
 closeAgentDetail()
-  setDockOpen(back.dockOpen)
+  setDockOpen(back.dockOpen && dockAvailable())
 renderDock()  // !channel 分支
-  私信 → dock.dmNoJoin；其它 → dock.openChannelFirst
+  dock.openChannelFirst（DM 分支已不可达，死代码删除）
```

- 「成员面板只属于频道」写进 `CONTEXT.md`；任务线程 / 运行面板仍占用同一个 dock 容器，因此
  私信 + 任务线程时开关仍可见（反向护栏用例）。
- `i18n.js` 两份词条表各删一处 `dock.dmNoJoin`；`#btnDockToggle` 的 `aria-expanded` 仍只由
  `setDockOpen()` 维护。
- 零后端 / API / schema / 样式改动；`app.css`、`index.html`、原型目录未动。

## Evidence

- **Before:** `scripts/red-evidence.sh 9a267d5 app/tests/dock_dm_ui.test.cjs`
  → 基线 `app.js` md5 `72689b66138f98cd7d516e069d2b4b83`（证据目录
  `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.rirIxg`）：`tests 6 / pass 1 / fail 5`。
  5 条红分别是：私信下开关没隐藏、`toggleDock()` 仍会打开、命令面板仍有 `toggle-dock`、
  切私信后已开的 dock 没收起、关闭 Agent 详情会把 dock 重新打开。
  **After:** `node --test app/tests/dock_dm_ui.test.cjs` → `6/6`；受影响 6 个用例文件
  （dock_dm / mobile_shell / run_panel / channel_config / channel_budget / i18n）→ `117/117`。
- **Gate:** `scripts/check.sh --studio` → pytest `20 passed`、ruff `All checks passed`、
  `ruff format --check` `322 files already formatted`、mypy `Success: no issues found in 124 source files`、
  pyright `0 errors, 0 warnings, 0 informations`、`node --test app/tests/*.test.cjs`
  `tests 316 / pass 316 / fail 0`、`门禁全绿`。
- **真实浏览器（ego-browser，隔离 `--port 50145 --data-dir /tmp/alibot-dock-smoke-20261024`，
  服务从本 worktree 的 `app/` 启动）**：频道下 `#btnDockToggle` 可见（`30×30`、`display:grid`），
  点开后 `#dock.hidden === false`、`.is-open`、`aria-expanded="true"`，命令面板含 `toggle-dock`；
  切到私信后开关 `hidden === true`、`display:none`、`0×0`、`aria-expanded="false"`，dock
  `hidden === true`、无 `.is-open`，命令面板不含 `toggle-dock`；私信里程序化再点开关无变化。
  服务端实际 `app.js`：`hasDockAvailable: true` / `hasDmNoJoin: false`。
  截图 `/tmp/ego-dock-smoke/dm-final.png`。
- 验收文档：`app/specs/studio-dm-dock-toggle-acceptance.md`（判据表、红 / 绿读数与证据目录、
  门禁 / 浏览器读数、Standards + Spec 双轴 review 与处置）。

## Merge Danger

**Door:** two-way

纯前端入口可见性与 dock 收口；无数据 / schema / 迁移 / API 改动。回滚即恢复旧行为
（私信下开关可见、可打开空面板）。

**Blast Radius:** small

- 只碰 `app/alibot/studio/{app.js,i18n.js}`、`app/tests/dock_dm_ui.test.cjs`、`CONTEXT.md`，
  外加验收 / PR 文档；`app.css`、`index.html`、后端、原型均未改。
- 私信 + 任务线程 / 运行面板的既有形态不受影响（`dockAvailable()` 对两者为真）；
  频道下的开关、命令面板条目、开合与 `aria-expanded` 行为逐字不变。
- `closeAgentDetail()` 只多了一个 `dockAvailable()` 与条件；频道下恢复进入前开合的语义不变。

Closes: 无（用户 2026-10-24 对 `/studio/` 的反馈，无对应 issue）
