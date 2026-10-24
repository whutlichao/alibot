# Studio「私信下 dock 开关无效/不显示」验收

- 状态：Done（待 coordinator 执行 G-merge）
- 基线：`9a267d5`（`Merge pull request #259 from whutlichao/studio-agent-detail-head`）
- 分支：`studio-dm-dock-toggle`
- 面：`app/alibot/studio/{app.js,i18n.js}`、`app/tests/dock_dm_ui.test.cjs`、`CONTEXT.md`
- 无后端 / API / schema / 迁移 / 样式改动：纯前端可见性与入口收口；`app.css`、`index.html` 未动。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | `dockAvailable()` = `runPanel.open \|\| dockFallbackMode() !== null`，按可显示形态判，不按 `isDm` | `app.js` `dockAvailable()`（紧邻 `dockFallbackMode()`） | `dock_dm_ui` 全 6 条通过 |
| 2 | 频道为当前 target 时 `#btnDockToggle` 可见；私信下 `hidden === true` | `renderHeader()` 与相邻入口同一批 `hidden` 赋值 | `频道为当前 target 时开关可见；私信为当前 target 时隐藏` 通过 |
| 3 | 私信下 `toggleDock()` 不改开合、不写 `aria-expanded="true"`；频道下能开能关且 `aria-expanded` 同步 | `toggleDock()` 首行 `if (!dockAvailable()) return;` | `私信下 toggleDock() 不改开合；频道下能开能关且 aria-expanded 同步` 通过 |
| 4 | 私信下 `commandList()` 不含 `toggle-dock`；频道下含 | `commandList()` 条件展开 | `私信下 commandList() 不含 toggle-dock；频道下含` 通过 |
| 5 | 频道里已打开的 dock，切到私信并 `render()` 后收起（`hidden === true`、无 `.is-open`） | `render()` 在 `setDockMode(...)` 之后的唯一收口 | `频道里打开的 dock 在切到私信并 render() 后收起` 通过 |
| 6 | 反向不误伤：私信 + `activeTaskId` + `setDockMode('task', true)` 时 `render()` 不收起、开关仍可见 | 同一判据 `dockAvailable()` | `私信下 dock 有任务线程形态时 render() 不收起它，开关仍可见` 通过 |
| 7 | 关闭 Agent 详情不得把 `render()` 刚收起的 dock 重新打开 | `closeAgentDetail()` 的恢复开合加 `&& dockAvailable()` | `私信下关闭 Agent 详情：render() 收起的 dock 不会被恢复开合重新打开` 通过 |
| 8 | `renderDock()` 的 `!channel` 空态不再按 `target.kind === 'dm'` 分叉 | 空态只剩 `t('dock.openChannelFirst')` | 代码 diff；`dmNoJoin` 全仓零引用 |
| 9 | 两份词条表同步删除 `dock.dmNoJoin`（zh-CN / en 各一处） | `i18n.js` | `grep -rn dmNoJoin app CONTEXT.md` → 无输出；`i18n_ui` 键集用例通过 |
| 10 | `CONTEXT.md`「成员面板」补「只属于频道」；任务线程 / 运行面板不受此限 | `CONTEXT.md:632` | 文档 diff |
| 11 | `aria-expanded` 仍只由 `setDockOpen()` 写 | `setDockOpen()` 未改 | `channelOpened.toggleAria === "true"` / `dmAfterSwitch.toggleAria === "false"`（浏览器读数） |
| 12 | 既有用例断言不放宽 | `mobile_shell` / `run_panel` / `channel_config` / `channel_budget` / `i18n_ui` | 受影响 6 个文件 `tests 117 / pass 117 / fail 0` |

## 红证据（隔离基线）

```sh
scripts/red-evidence.sh 9a267d5 app/tests/dock_dm_ui.test.cjs
```

| 基线 app.js md5 | 工作区 app.js md5 | 证据目录 | 读数 |
| --- | --- | --- | --- |
| `72689b66138f98cd7d516e069d2b4b83` | `ad5677c96d36424722ff8cc81f4e88ed` | `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.rirIxg` | `tests 6 / pass 1 / fail 5` |

红读数（基线产品代码 + 本票测试）：

- `频道为当前 target 时开关可见；私信为当前 target 时隐藏`：`私信下没有可显示的形态，开关随之隐藏` — `false !== true`（基线从不写 `#btnDockToggle.hidden`）
- `私信下 toggleDock() 不改开合；频道下能开能关且 aria-expanded 同步`：`私信下按开关不打开 dock` — `false !== true`（基线无条件 `setDockOpen(dock.hidden)`）
- `私信下 commandList() 不含 toggle-dock；频道下含`：`true !== false`（基线命令表恒有 `toggle-dock`）
- `频道里打开的 dock 在切到私信并 render() 后收起`：`切成私信后 dock 不得留在右侧` — `false !== true`
- `私信下关闭 Agent 详情：render() 收起的 dock 不会被恢复开合重新打开`：`私信下关闭详情后 dock 仍收起` — `false !== true`

唯一在基线上通过的是第 6 条（反向不误伤）：基线本来就不收 dock，所以「私信 + 任务线程时不收」自然成立；它是防本次改动过界的护栏，不是红读数。

## 绿读数

```sh
node --test app/tests/dock_dm_ui.test.cjs
```

`tests 6 / pass 6 / fail 0`。

受影响用例族：

```sh
node --test app/tests/dock_dm_ui.test.cjs app/tests/mobile_shell.test.cjs \
  app/tests/run_panel_ui.test.cjs app/tests/channel_config_ui.test.cjs \
  app/tests/channel_budget_ui.test.cjs app/tests/i18n_ui.test.cjs
```

`tests 117 / pass 117 / fail 0`。

## 门禁读数（G-impl）

```sh
scripts/check.sh --studio
```

```text
== pytest（Studio 窄档：审批 HTTP 契约 + 前端相关静态守卫）
20 passed in 2.27s
== ruff check
All checks passed!
== ruff format --check
322 files already formatted
== mypy（仓库根）
Success: no issues found in 124 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob）
ℹ tests 316
ℹ pass 316
ℹ fail 0
门禁全绿
```

## 真实浏览器冒烟（ego-browser）

服务从**本 worktree** 的 `app/` 启动（避免 `.venv` editable 安装指向主仓库的旧前端）：

```sh
cd app && env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
  ../.venv/bin/python -m alibot --port 50145 --data-dir /tmp/alibot-dock-smoke-20261024
```

隔离数据目录里新建频道「冒烟频道」、Agent「冒烟助手」与对应私信，然后在 `http://127.0.0.1:50145/studio/` 上验证：

| 观察点 | 读数 |
| --- | --- |
| 服务端实际发出的 `app.js` | `hasDockAvailable: true`、`hasDmNoJoin: false` |
| 频道：`#btnDockToggle` | `hidden === false`、`display: grid`、`30×30` |
| 频道：点开关后 | `#dock.hidden === false`、`.is-open`、`aria-expanded="true"` |
| 频道：命令面板 | 含 `toggle-dock` |
| 切到私信后 | 开关 `hidden === true`、`display: none`、`0×0`、`aria-expanded="false"`；dock `hidden === true`、无 `.is-open` |
| 私信：程序化再点开关 | dock 仍 `hidden`、`aria-expanded` 仍 `false` |
| 私信：命令面板 | 不含 `toggle-dock` |

截图：`/tmp/ego-dock-smoke/dm-final.png`。

## 双轴 code-review（先读 `docs/agents/review-rules.md`）

### Standards 轴

1. **缺少验收文档（硬项）** → 已补本文件（`app/specs/studio-dm-dock-toggle-acceptance.md`），含判据、红 / 绿读数、门禁与浏览器读数。
2. **新用例复制了整套 DOM 装置（判断题）** → 保留。票面明确要求「接缝照 `channel_config_ui.test.cjs`……不新开前端测试框架」；仓库现有 19 个 `.test.cjs` 同形，抽公共 harness 会改动本票范围外的文件，列为后续独立票。
3. **「dock 形态」推导仍有一处内联（判断题）** → 保留。`render()` 的 `setDockMode(...)` 实参是既有代码，本票只在其后加收口条件；改它会把 diff 扩到与本票无关的形态推导。
4. **`toggleDock()` 注释提到「移动端抽屉」（判断题）** → 保留。票面原文即「频道头开关 / 命令面板 / 移动端抽屉共用同一动作」；≤900px 时 `#btnDockToggle` 就是右抽屉的入口，抽屉自己的关闭控件另走 `setDockOpen(false)`，两处不矛盾。
5. **`CONTEXT.md` 括注读成全域否定（判断题）** → 已改为「dock 的开关随之不出现……任务线程 / 运行面板仍占用同一个 dock 容器，开关也仍可用」，与用例 6 一致。

### Spec 轴

- 8 步 + 5 条必测用例逐条核对：全部 implemented；无 scope creep。
- **P2 发现：`closeAgentDetail()` 会把 `render()` 刚收起的 dock 重新打开**（私信 + 详情关闭后留下一个开关已隐藏、只能按 ESC 关掉的空面板）→ 已修：恢复开合改为 `Boolean(back.dockOpen) && dockAvailable()`；并新增第 6 条用例（基线红 / 现绿）。
- 非阻断项：移动端在切私信的同一拍里 `render()` 的 `setDockOpen(false)` 可能先把焦点送到随后被隐藏的开关上。它是 `setDockOpen()` 既有的焦点归还纪律，改动会让「收 dock」的焦点行为与本票无关地变化；未改，记录在此。

## Answer

- 改动：`dockAvailable()` 判据 + 频道头开关 / `toggleDock()` / 命令面板私信下关闭；`render()` 单点收起无形态的 dock；`closeAgentDetail()` 不再越权恢复；`renderDock()` 删 DM 死分支；两份词表删 `dock.dmNoJoin`；`CONTEXT.md` 同步。
- 证据：红 `tests 6 / pass 1 / fail 5`（证据目录 `alibot-red.rirIxg`，基线 md5 `72689b66…` / 工作区 md5 `ad5677c9…`）；绿 `6/6`、受影响 6 文件 `117/117`；`scripts/check.sh --studio` 全绿（node `316/316`）；真实浏览器读数如上。
- review：Standards 硬项 1 已补验收文档，判断题 2–4 保留并给理由，判断题 5 已改；Spec P2 已修并加回归用例。
- PR：[#260](https://github.com/whutlichao/alibot/pull/260)；body 正本 `app/specs/studio-dm-dock-toggle-pr-body.md`。
- 遗留：无功能遗留；共享 DOM harness 抽取、`render()` 形态推导收口是范围外的后续改进。
