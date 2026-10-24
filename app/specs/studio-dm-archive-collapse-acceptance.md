# Studio「已归档」私信段默认折叠验收

- 基线：`2497bcc26a72580569c48856b5da6c13d54c0634`（PR #257 合并点）
- 分支：`studio-dm-archive-collapse`
- 面：`app/alibot/studio/**`（零构建，由后端静态托管）+ `app/tests/*.test.cjs`
- 无后端 / API / schema / 迁移改动：折叠是**会话内的呈现状态**（`state.dmArchiveExpanded`，默认
  `false`），不写浏览器存储、不新增字段，归档判据仍只消费 `GET /api/dms` 已有的 `peer.deleted_at`。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 有归档 DM 时组可见、列表默认折叠、标题按钮报 `aria-expanded="false"`；归档行仍在容器里 | `renderDmArchiveSection()`（唯一裁决点）+ `state.dmArchiveExpanded` | `node --test app/tests/rail_identity.test.cjs`：`已删除 peer 的私信进「已归档」段…` 通过 |
| 2 | 点标题展开（`hidden=false` / `aria-expanded="true"`），再点收起；两次都不拆行节点、不发任何请求 | `#btnDmArchiveToggle` 的 click 绑定 | 同上：`点「已归档」标题展开、再点收起：行节点身份不变，且全程不重取列表` 通过（`harness.calls` 增量 `[]`，行节点对象身份相同） |
| 3 | 后端推送触发的整体重绘保持展开态、不拆节点 | `renderDms()` → `renderDmArchiveSection()` | 同上：同一条用例里 `api.render()` 之后仍 `hidden === false` 且行节点同一 |
| 4 | 无归档 DM 时整组 `hidden`；有归档 DM 后组出现但仍默认折叠 | 同一裁决点 | 同上：`没有已归档私信时整段隐藏；全部归档时「私信」段显示空态` 通过 |
| 5 | 展开后点归档行仍进入该 DM 且 composer 只读 | `#dmArchiveList` 的既有 click 委派 → `selectTarget()`；`syncComposerLock()` | 同上：`点「已归档」里的行仍能打开该私信读历史` 通过（先展开，`#composerInput.disabled === true`） |
| 6 | 标题是原生 `<button type="button">`，带 `aria-expanded` / `aria-controls="dmArchiveList"`，可见文案挂 `rail.groupArchivedDms`，右侧有 chevron；展开时 chevron 转向下 | `index.html` / `app.css` | 同上：`「已归档」标题的静态正本：原生 button + aria 状态 + 词条绑定 + chevron` 通过 |
| 7 | 无新词条（按钮复用 `rail.groupArchivedDms`，未新增 aria-label/title）；HTML 中文全挂 `data-i18n`，JS/CSS 无裸中文 | `index.html` / `i18n.js` | `scripts/check.sh --studio` 的 `i18n_ui.test.cjs` 键集 / 裸中文用例通过 |
| 8 | 正常「私信」段、未读徽标、空态文案、无归档时整组隐藏都不变 | 既有用例 | 同上：既有 14 条全绿 |
| 9 | 真实浏览器冒烟（隔离端口 + 隔离数据目录） | 见下节 | 见下节 |

## 红证据（隔离基线）

```sh
scripts/red-evidence.sh 2497bcc26a72580569c48856b5da6c13d54c0634 app/tests/rail_identity.test.cjs
```

| 基线 app.js md5 | 证据目录 | 读数 |
| --- | --- | --- |
| `e041cd74d3c7f7509e2cf23b900134e9` | `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.w78O49` | `tests 16 / pass 12 / fail 4`：基线既有的 12 条全绿（含「点归档行仍能读历史」「就地更新」），新增 / 加严的 4 条全红 |

红读数（基线产品代码 + 本票测试）：

- `已删除 peer 的私信进「已归档」段…`：`有已归档私信时列表默认折叠` — `false !== true`（基线没有折叠状态，列表恒可见）
- `点「已归档」标题展开、再点收起…`：`默认折叠：列表不可见` — `false !== true`
- `没有已归档私信时整段隐藏…`：`组出现时列表仍默认折叠（不自动展开）` — `false !== true`
- `「已归档」标题的静态正本…`：`分组标题是 <button id="btnDmArchiveToggle">，不是 div 冒充按钮` — `null !== true`（基线是 `<div class="group-label">`）

## 门禁读数

```sh
scripts/check.sh --studio
```

- pytest（Studio 窄档）：`20 passed in 2.06s`
- `ruff check`：`All checks passed!`
- `ruff format --check`：`318 files already formatted`（未运行 `ruff format`，无整文件重排）
- `mypy`（仓库根）：`Success: no issues found in 124 source files`
- `pyright`：`0 errors, 0 warnings, 0 informations`
- `node --test app/tests/*.test.cjs`：`tests 309 / pass 309 / fail 0`（既有 307 + 本票新增 2）
- 收尾：`门禁全绿`

## node 用例读数

```sh
node --test app/tests/rail_identity.test.cjs   # 16 条全绿（14 旧 + 2 新；其中 2 条旧用例加严）
```

断言只落在外部可观察行为：`#dmArchiveGroup.hidden` / `#dmArchiveList.hidden`、行节点**对象身份**、
行仍在容器里、`aria-expanded`、`fetch` 调用增量、`#composerInput.disabled`、以及静态标记与
`aria-expanded` 驱动的 chevron 旋转规则。`aria-controls` 是不变量，只压在 `index.html` 的静态断言上
（JS 不重写它，替身节点也不解析 HTML 属性）。

## 真实浏览器冒烟

- URL：`http://127.0.0.1:8799/studio/`
- 启动：`cd app && ../.venv/bin/python -m alibot --port 8799 --data-dir /tmp/alibot-dm-collapse-smoke`
- 数据目录：`/tmp/alibot-dm-collapse-smoke`（隔离，未使用 `8788` 与真实数据）
- 驱动：ego-browser（真实 Chromium，spaceId `4`）
- 播种：`POST /api/agents`（`ollama:smoke-model`）→ `POST /api/dms {agent_id}` → 一条历史消息
  （`这是一条归档前的历史消息`）→ `DELETE /api/agents/{id}`（软删除，DM 变归档）
- 截图：`/tmp/alibot-smoke-collapse-00-no-archive.png` … `/tmp/alibot-smoke-collapse-07-history.png`

| 步骤 | 观察 |
| --- | --- |
| 删除 Agent 之前加载页面 | `#dmArchiveGroup.hidden === true`（无归档 DM 时整组不出现）；`#dmList` 有 1 行 |
| 软删除 Agent 后刷新 | `#dmArchiveGroup.hidden === false`、`#dmArchiveList.hidden === true`、`aria-expanded="false"`；归档行在 DOM 里但 `list` 高度 `0`、行无可用布局盒（不可见 / 不可点） |
| 点「已归档」标题 | `list.hidden === false`、`aria-expanded="true"`、列表高度 `34px`；行节点 `data-smoke-mark` 仍是展开前那个节点；`window.__dmsRequests` 为空（全程无 `/api/dms` 请求） |
| 等 400ms 后读 chevron | `matrix(0, 1, -1, 0, 0, 0)`（= `rotate(90deg)`，展开向下；折叠时无旋转，即向右） |
| 再点一次标题 | `list.hidden === true`、`aria-expanded="false"`；行仍在 DOM 里、`data-smoke-mark` 未丢（未清空、未重建） |
| 键盘：聚焦标题后 `Enter` / `Space` | 两次都切换到位（`hidden` 与 `aria-expanded` 同步翻转），`document.activeElement.id === "btnDmArchiveToggle"`，焦点环 `outlineStyle === "solid"`（全局 `:focus-visible`） |
| 展开后点归档行 | 标题 `@冒烟助手`；`#composerInput` / `#btnSend` / `#btnAttach` / `#btnMention` / `#btnAsTask` 全 `disabled`；placeholder「该 Agent 已删除，这段私信只读归档」、hint「已归档 · 只读，不能发送消息」；历史消息仍在页面上 |
| 刷新页面 | 回到默认折叠：`list.hidden === true`、`aria-expanded="false"`、组仍可见 |

## Answer

- 交付物：`#dmArchiveGroup` 的标题从不可点的 `<div class="group-label">` 换成原生
  `<button type="button" id="btnDmArchiveToggle" aria-expanded="false" aria-controls="dmArchiveList">`
  （内层 `<span data-i18n="rail.groupArchivedDms">` + 右侧 chevron）；`state.dmArchiveExpanded`
  是唯一的展开态，`renderDmArchiveSection()` 是唯一的呈现裁决点（组显隐 / 列表 `hidden` /
  `aria-expanded`），`renderDms()` 与按钮点击都只调它；折叠只调 `hidden`，不清行、不重取列表。
- 不变的部分：归档判据（`peer.deleted_at`）、两段共用的 `syncRailRows` 就地更新、归档 DM 的
  composer 只读、未读徽标与空态文案、无归档时整组隐藏；未新增词条，未新增后端字段或接口调用。
- **两轴 code-review 按 §3 小改动豁免跳过**：本票只在既有 `已归档` rail 段里加**一个**折叠状态与
  一个切换控件，不改数据 / API / 共享接口，可观察契约由 node 用例（默认折叠、切换、节点身份、
  无 `/api/dms`、只读进入、静态正本）+ 真实浏览器冒烟（含键盘）完整覆盖；无跨模块设计取舍可供两轴
  评审，因此不另开 reviewer 子代理。
- 无遗留项；PR：[#258](https://github.com/whutlichao/alibot/pull/258)（OPEN，head =
  `studio-dm-archive-collapse` 最终提交），正文 `app/specs/studio-dm-archive-collapse-pr-body.md`。
