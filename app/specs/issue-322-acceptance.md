# 验收记录：#322 搜索的任务命中行可点（打开对应任务线程）

**票型**：实施票｜**门禁**：G-impl｜**Door**：双向
**Ticket**：[GitHub issue #322](https://github.com/whutlichao/alibot/issues/322)
**base**：`origin/main`（`5e2d973`）｜**branch**：`search-task-hit-clickable`
**worktree**：`/Users/apple/orca/workspaces/alibot/search-task-hit-clickable`
**采集时间**：2026-10-27｜**环境**：macOS（aarch64）、Python 3.11（worktree `.venv` 符号链接到主工作区）、Node v25
**改动面**：`app/frontend/src/components/SearchView.vue`、`app/frontend/src/App.vue`、
`app/frontend/tests/search-commands.test.js`，加本文件与 `issue-322-pr-body.md`。
**后端零改动**、`app.css` 零改动、产物不入库。

---

## 0. 开工前的三个必答问题（先查载荷，不猜）

| 问题 | 结论 | 证据 |
| --- | --- | --- |
| `target_id` 怎么拿到 | **载荷已有**，不需要加后端字段。`hit.task` 带 `id` / `target_id` / `channel_id` / `channel_name` / `title` / `status` | 源码：`app/alibot/db.py::_task_hit_payload()`；实测：见 §3 的 `/api/search` 真实响应 |
| 打开线程后搜索视图怎么办 | **留在搜索视图 + 右侧开线程**。旧 `openTask()` 从不写 `state.mode` | `git show 635ed8f:app/alibot/studio/app.js` 的 `openTask()`（`state.activeTaskId` / `setDockMode('task', true)`，无 `state.mode =`） |
| 函数放哪 | **不造新函数**。判据直接落 `SearchView.vue` 的 `rows` computed（每行算一个 `taskId`），模板按它选 `button` / `div` | 票面「选简单的那条，不要为对齐旧命名而造一层」 |

### 0.1 票面未覆盖、开工后 ask 升级的边界：私信下的 dock

`App.vue` 的 `visibleDockTarget` 原为 `isDm.value ? null : dockTarget.value`——私信下整条 dock 恒不渲染。
而搜索入口在私信下是**可用**的（rail 的搜索键不按 target 类型分身），于是「私信里搜到任务命中 → 点开 →
右侧什么都不出现」正是票面禁止的「点了没反应的按钮」。

旧页面不是这样：`dockFallbackMode()` 的判据是「有 `activeTaskId` ⇒ `'task'`」，注释明写
「任务线程 / 运行面板是独立形态，私信下仍算可用」。

此事已 `orca orchestration ask` 上报，协调者裁定**按形态判断**（不是按 `isDm` 一刀切），并要求核实另外三处
「私信下不成立」的判据。核实结论见 §1.2。

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 读数 |
| --- | --- | --- | --- |
| 1 | 任务命中行渲染成 `<button>`；消息 / Agent 命中仍是普通 `div` | `SearchView.vue` 的 `rows` 计算 `taskId`，模板用 `<component :is="row.taskId ? 'button' : 'div'">` | ego-browser 实测（§3）：消息行 `DIV`、两条任务行 `BUTTON`；Vitest 断言 `results[1].element.tagName === 'BUTTON'`、`results[0]/[2]` 为 `DIV` |
| 2 | 判据与旧页面一致：看 `hit.task.id`，不按 `kind` 分派 | 同上（`taskId: (hit && hit.task && hit.task.id) \|\| null`） | 专条用例「kind 是任务但载荷缺 id ⇒ 仍是 `div`」 |
| 3 | 非任务命中**不是**禁用按钮 | `div` 分支不带 `:type` / `disabled` / `tabindex` | 用例断言 `disabled` / `tabindex` 为 `undefined`；ego 实测三个属性均为 `null` |
| 4 | 点击任务命中 ⇒ 打开对应任务线程，线程属于**正确 target** | `SearchView` 抛 `open-task` → `App.vue` 的 `@open-task="openTask"`（与看板卡片、消息 chip 同一条路径） | ego 实测：点「设计任务甲」→ 线程标题 `设计任务甲`、副标题 `#design · 讨论 1 条`、正文是甲线程自己的消息；Vitest 断言待审批快照读到 `target_id=tgt-abcdef` 而**不是**当前频道 `ch-1` |
| 5 | Tab 能到任务命中行、Enter 能打开 | 原生 `<button>`（无 `tabindex="-1"`、无自造 keydown） | ego 实测 Tab 序列：`search-input → 全部 → 消息 → 任务 → Agent → search-task`（消息命中行被跳过，因为它不可聚焦）；Enter 后线程打开 |
| 6 | 至少一条用例从真实父组件进，断言线程里是**正确的那个任务** | `search-commands.test.js` 的四条用例全部 `mount(App)` | 见 §2 读数 |
| 7 | 私信下也能开线程（旧 `dockFallbackMode()` 同判据） | `App.vue` 的 `visibleDockTarget` 改成按形态判断 | 专条用例「私信下也能开线程」 |
| 8 | `./scripts/check.sh` 全绿；产物由脚本生成、不入库 | — | 见 §4 |
| 9 | ego-browser 实测：任务命中可点开、消息命中不可点 | — | 见 §3 |
| 约束 | 不改后端响应形状 / `app.css` 零改动 / 不用 `innerHTML`·`v-html` / 词条不新增 | — | `git diff --stat` 只三个前端文件；`i18n.js` 未改；模板只是 `<component>` + 既有文本插值 |

### 1.1 为什么不用 `disabled` 或 `role="button"`

禁用按钮**仍然可聚焦**（`<button disabled>` 在部分浏览器/AT 组合里仍会进 Tab 序列或被读到），
人看到一个「看起来能按、按了没反应」的行；而 `role="button"` 挂在 `div` 上要自己补 Tab 索引与
Enter/Space 处理。原生元素只做一件事：有任务标识就是真按钮，没有就是真文本行。这也与旧页面
（`el(taskId ? 'button' : 'div', 'result')`）逐字一致。

### 1.2 另外三处「私信下不成立」的判据核实（协调者要求）

| 位置 | 判据 | 是否需跟着改 | 理由 |
| --- | --- | --- | --- |
| `App.vue` 的 `dockToggleAvailable`（`search-commands.test.js` 的 `toggle-dock` 不列） | `!isDm.value && Boolean(target.value)` | **不需要** | 它是「频道头的运行面板开关 / 命令面板那条『切换详情面板』在不在」，开的是**频道**运行面板——频道专属入口，私信下确实不成立。与本票改的「dock 能不能显示一个**已有形态**」是两件事 |
| `ChannelHeader` 的 `isDm` 分支（`components.test.js`） | 私信下没有任务页签与四个频道工具按钮 | **不需要** | 页签与工具是**频道**的面；任务线程不是页签。搜索命中开线程走的是 dock，不经过页签 |
| `channel-tools.test.js` 的「私信下三个入口不存在」 | 名册 / 配置 / 预算三个频道头入口 | **不需要** | 同上，三个都是频道专属工具 |

三处都没有各自复制一份 `isDm` 判断到「dock 形态」上——本票只改 `visibleDockTarget` 这一处。
v2 里 `dockTarget` 的写点（`openTask` / `openRunPanel` / `closeDock` / `selectTarget`）都不读 `isDm`，
形态判据仍然只在 `visibleDockTarget` 这一个读点上收口。

---

## 2. Vitest 读数

```text
$ cd app/frontend && env -u NODE_ENV npx vitest run tests/search-commands.test.js
 Test Files  1 passed (1)
      Tests  36 passed (36)
```

新增 / 改写的用例（全部从 `mount(App)` 进）：

| 用例 | 断言要点 |
| --- | --- |
| 任务命中行是 `<button>`，消息 / Agent 命中仍是 `div` | `tagName` / `type` / `data-opentask` / `data-od-id` / 无 `disabled` 与 `tabindex` |
| 判据只看载荷里有没有 `task.id` | `kind: 'task'` 但 `task` 缺 `id` ⇒ `DIV`、无 `data-opentask` |
| 点命中 ⇒ dock 开 `task` 档，线程里是**这条**任务 | 线程标题 = `设计任务甲`；`/api/tasks/tsk-abcdef/messages` 被请求；待审批快照读 `target_id=tgt-abcdef`（0 次读 `ch-1`） |
| 点命中**不**退出搜索视图 | `search-view` 仍可见且 `detail-dock` 存在 |
| 键盘可达 | 行无 `tabindex`、无 `disabled`；原生 `click()` 打开线程 |
| 私信下也能开线程 | 先进私信 → 搜 → 点命中 ⇒ `detail-dock` 与 `task-thread` 都在 |

原用例「命中行不是按钮：任务线程与 Agent 详情都没迁」按票面要求**改写**（它描述的是旧契约）。

### 2.1 既有用例的连带影响：一条被如实改写

`app/frontend/tests/app-shell.test.js` 的「切到私信 ⇒ `#dock` 整栏消失」仍然通过，原因：它先点
**频道头的运行面板**入口（`kind: 'run'`、`channelId: ch-1`）再切私信，而 `selectTarget()` 会把
`dockTarget` 整个置 `null`——所以「切 target 收 dock」这条既有行为**逐字不变**。判据的差别只出现在
「私信里主动开一个 task 形态」这条本票新增的路径上。

## 3. ego-browser 实测（真实浏览器 + 真实后端）

装置：临时数据目录 `/tmp/alibot-hit-smoke`，`.venv/bin/python -m alibot --port 8791`；
用 `POST /api/channels` 建频道 `design`，用 `POST /api/channels/{id}/tasks` 建两条任务
（`设计任务甲` / `设计任务乙`），在甲线程与频道各发一条含「设计」的消息。

**载荷实测**（确认 §0 第 1 条，不需要加后端字段）：

```json
{ "kind": "task", "text": "设计任务甲", "highlight_ranges": [{"start": 0, "end": 2}],
  "target": { "id": "ch_81e347a4887c", "kind": "channel", "display_name": "design" },
  "task": { "id": "task_73bfd2ab54ea", "target_id": "tgt_982fc0dc658d",
            "channel_id": "ch_81e347a4887c", "channel_name": "design",
            "title": "设计任务甲", "status": "todo" } }
```text

**行形态**（三行命中）：

```
消息命中  tag=DIV     opentask=null               tabindex=null  disabled=null  role=null
任务命中  tag=BUTTON  opentask=task_73bfd2ab54ea  type=button    od-id=search-task
任务命中  tag=BUTTON  opentask=task_2449532bb1e2  type=button    od-id=search-task
```text

**点「设计任务甲」**：

```
dockExists=true  dockClass="dock is-open"  threadExists=true
threadTitle="设计任务甲"
threadHeadSub="任务线程#ab54ea#design · 讨论 1 条"   ← 属于正确的 target（design 频道下的这条任务）
searchStillVisible=true                             ← 搜索视图仍在主线
```text

**键盘**（CDP 真实按键，从搜索框起 Tab）：

```
search-input → 「全部」→「消息」→「任务」→「Agent」→ [search-task / task_73bfd2ab54ea]
```text

消息命中行不在 Tab 序列里（`div` 不可聚焦）。在 `search-task` 上按 Enter：

```
ENTER_RESULT {"threadExists":true,"title":"设计任务甲","searchVisible":true}
```text

**消息命中行点不动**（先确认 dock 已真的关掉，排除上一轮残留）：

```
AFTER_CLOSE {"dockExists":false,"threadExists":false}
MESSAGE_ROW_CLICK {"msg":{"tag":"DIV","opentask":null,"tabindex":null,"disabled":null,"role":null},
                   "afterMsg":{"dockExists":false,"threadExists":false}}
```text

浏览器侧：`page.close()` + `task.finish({ keep: [] })`；冒烟服务已停止。

## 4. 全量门禁

```text
$ ./scripts/check.sh
== pytest（全量）     1271 passed, 11 skipped in 744.86s (0:12:24)
== ruff check         All checks passed!
== ruff format --check 366 files already formatted
== mypy（仓库根）     Success: no issues found in 134 source files
== pyright            0 errors, 0 warnings, 0 informations
== Vitest             Test Files 22 passed (22) / Tests 431 passed (431)
门禁全绿
```

产物由 `check.sh` 头部 `npm run build` 生成，`git status` 里**看不到** `app/alibot/studio/v2/`（已 gitignore）。

---

## 5. 双轴 code-review：本票省略，改为自查判断题清单

按派活口径（`orchestra-auto §3` + `docs/agents/implementation.md`）：影响面可圈定在搜索视图的命中行渲染
与一条 dock 接线，不触达后端、共享 store 或构建配置；改动是把一个既有非交互行改成 `button`。
逐条对照 `docs/agents/review-rules.md` 的判断题：

| # | 规则 | 结论 |
| --- | --- | --- |
| 1 | 共享行为不为单票需求而变 | **符合**。`visibleDockTarget` 是 dock 的**唯一**读点，改的是它自己的判据（按形态而非按 `isDm`），没有动 `dockTarget` 的写点、没有动 store、没有动 `DockPanel` |
| 2 | 不新开接缝 | **符合**。用例全在既有 `search-commands.test.js` 里，用既有 `mount(App)` + `fetch` 替身装置；没有新的替身类型 |
| 3 | 直接调用内部函数属越层 | **符合**。断言全落在 DOM（`tagName` / `data-opentask` / 可见性）与发出的请求上，没有直调 `rows` / `openRow` |
| 4 | ADR 只做最小事实修正 | **符合**。ADR-0038 §237 一句与实现不符的描述（原文「私信下 `dockTarget` 恒为 `null`（整栏消失）」）已按本条做**最小事实修正**：改成按形态裁决，并以「**事实修正（#322）**」标记指回本票与旧页面 `dockFallbackMode()`；模块、三档形态、`dockTarget` 是唯一判据这些**决策内容一字未动**（见 §5.2） |
| 5 | 失效方向可放松的判据要写明 | **已写**。私信下 dock 的失效方向是「宁可多显示一个已知形态，不可让点了没反应」；已知代价是私信里也可能出现运行面板（目前没有这条路径，因为 `openRunPanel` 只从频道头 / 线程头进） |
| 6 | 文档与数据契约同步 | **不涉及**。没有新增状态 / kind / 端点，`app/README.md` 与 `CONTEXT.md` 的相关段落未变 |
| 7 | 验收证据要能被复跑 | **符合**。§2 给命令与读数、§3 给真实浏览器探针脚本与逐项读数、§4 给全量门禁读数 |
| 8 | AgentScope 用法先查规约 | **不涉及**。本票没有碰 `agentscope` |

### 5.1 一处刻意偏离票面字面的地方（已 ask 后定）

票面写「非任务命中保持 `div`（非交互），不要用 `disabled`」——**照做了**。但票面没有覆盖「私信下
dock 整栏不渲染」，那条是在开工后用 `orca orchestration ask` 上报、由协调者裁定按形态判断的，
不与票面冲突（改动仍只在 §1 第 7 条的范围内，且协调者明确同意「只改 `visibleDockTarget` 这一处」）。

### 5.2 收口时的两处口径修正（代码行为未改）

主实现核过都对，收口只修正两处**叙述**：

| 位置 | 原来怎么说 | 改成什么 |
| --- | --- | --- |
| `app/frontend/tests/app-shell.test.js:143` | 标题「切到私信 ⇒ `#dock` 整栏消失（私信下三档一档都不出现）」 | 「切到私信 ⇒ 运行面板收起（`resetRunPanelState()` 清掉 `dockTarget`，不是 `isDm` 一刀切）」，用例体加注释分清「切 target 作废形态」与「私信下 dock 恒不渲染」；**断言一条未动** |
| `docs/adr/0038-frontend-moves-to-vue.md:237` | 「私信下 `dockTarget` 恒为 `null`（整栏消失）」 | 按形态裁决（`members` 屏蔽，`task` / `run` 照常可见）+ 「**事实修正（#322）**」指回旧页面 `dockFallbackMode()` 的注释 |

---

## 6. 遗留

- 消息命中的「滚动定位」与 Agent 命中的「详情面」仍未接进来（旧页面也没有），保持非交互行。
- `app/specs/vue-refactor-phase1-acceptance.md:295`（#304 的**历史验收记录**）里同样记着旧口径
  「私信下 `dockTarget` 恒 `null`，整栏消失」。那是成文时的读数记录、不是活契约，本票按「不扩到
  别的文件」未动；如需同步由协调者另派。
- ADR-0038 内已 grep 确认**仅 237 行一处**重复该说法（119 / 121 / 133 / 183–184 行提到「私信」但
  讲的是新建私信、Agent 行开关与归档入口，与本判据无关），已一并确认无需再改。
