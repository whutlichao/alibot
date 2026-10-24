## Summary

左栏「已归档」私信段改成**可折叠、默认折叠**：标题从不可点的 `<div class="group-label">` 换成原生
`<button type="button" id="btnDmArchiveToggle">`（内层 `<span data-i18n="rail.groupArchivedDms">` +
右侧 chevron），展开态是会话内的呈现状态 `state.dmArchiveExpanded`（默认 `false`，不写浏览器存储）。

```diff
 Studio rail
 ├── 频道
 ├── 私信
-├── 已归档            # 平铺所有归档行
+├── 已归档  ›         # 默认折叠；组仍在，列表 hidden
+│   └── …             # 点标题展开 ˅，再点收起；行节点始终是同一个
 └── 本机 Agent
```

- `renderDmArchiveSection(hasArchived)` 是**唯一**呈现裁决点：组显隐、`#dmArchiveList.hidden`、
  按钮 `aria-expanded`；`renderDms()` 与按钮点击都只调它，按钮点击不重取 `GET /api/dms`。
- 折叠只调 `hidden`，不清行、不拆节点：归档行在展开 / 收起之间是同一个对象，未读与已读状态原样保留。
- 键盘可达：原生 button + `aria-expanded` + `aria-controls="dmArchiveList"`；焦点环走全局
  `:focus-visible`，不新造焦点样式。
- 不变：归档判据（`peer.deleted_at`）、组的空态隐藏、composer 只读、未读徽标、两段共用的
  `syncRailRows` 就地更新；未新增词条（复用 `rail.groupArchivedDms`），无后端 / API / schema 改动。

## Evidence

- **Before:** `scripts/red-evidence.sh 2497bcc26a72580569c48856b5da6c13d54c0634 app/tests/rail_identity.test.cjs`
  → 基线 `app.js` md5 `e041cd74d3c7f7509e2cf23b900134e9`，`tests 16 / pass 12 / fail 4`：新增与加严的
  4 条红（列表恒可见、无折叠状态、标题仍是 div），既有 12 条绿。
  **After:** `node --test app/tests/rail_identity.test.cjs` → `16/16`。
- **Gate:** `scripts/check.sh --studio` → pytest `20 passed`、ruff `All checks passed`、
  `ruff format --check` `318 files already formatted`、mypy `Success: no issues found in 124 source files`、
  pyright `0 errors`、`node --test app/tests/*.test.cjs` `tests 309 / pass 309 / fail 0`、`门禁全绿`。
- **真实浏览器（ego-browser，隔离 `--port 8799 --data-dir /tmp/alibot-dm-collapse-smoke`）**：
  删除 Agent 前 `#dmArchiveGroup.hidden === true`；软删除后刷新 组可见 / 列表 `hidden` /
  `aria-expanded="false"`（行在 DOM 里但无布局盒）；点标题展开（列表高 34px、`aria-expanded="true"`、
  chevron `rotate(90deg)`、行节点标记未丢、全程 0 次 `/api/dms` 请求）；再点收起（行仍在、节点同一）；
  聚焦后 `Enter` / `Space` 均可切换且焦点环可见；展开后点归档行进入 `@冒烟助手`，composer 与四个工具键
  全 `disabled`、历史消息仍在；刷新回到默认折叠。截图 `/tmp/alibot-smoke-collapse-00..07.png`。
- 验收文档：`app/specs/studio-dm-archive-collapse-acceptance.md`（含红证据目录与全部读数）。

## Merge Danger

**Door:** two-way

纯前端呈现状态（`state.dmArchiveExpanded`）加一个切换控件，没有数据 / 迁移 / 接口破坏；回滚即恢复
平铺的归档列表。

**Blast Radius:** small

- 只碰 `app/alibot/studio/{index.html,app.js,app.css}` 与 `app/tests/rail_identity.test.cjs`；
  后端、`GET /api/dms` 载荷、schema 一律未改。
- 归档段的组显隐判据仍只有一处（`renderDmArchiveSection()`），不会与行归属各自漂。
- 折叠不卸行、不重取列表：SSE 推送时的整体重绘保持展开态与行节点身份（node 用例覆盖）。
