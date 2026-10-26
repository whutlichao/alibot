# Vue 重构 · 第一阶段验收（主链路）

> 判据来源：用户 2026-10-26 人定「用 Vue 重构前端」+ `docs/adr/0038-frontend-moves-to-vue.md`。
> 本票只交付**第一阶段**（主链路），其余面按 ADR-0038 §五逐票迁移。

## 一、交付了什么

| 面 | 旧落点 | 新落点 | 判据 |
| --- | --- | --- | --- |
| 应用外壳（三栏 + 移动顶栏 + 抽屉） | `studio/index.html` / `app.js` | `app/frontend/src/App.vue` | 类名与 `data-od-id` 逐字不变；`components.test.js` 断言 |
| 左栏三段名单 | `renderChannels/Dms/Members` | `components/RailSidebar.vue` | 频道 / 私信 / 已归档 / 本机 Agent 四段；未读徽标与四态状态点按后端事实 |
| 频道头与页签 | `renderHeader` / `renderTabs` | `components/ChannelHeader.vue` | 频道 `#`、私信 `@`；私信下隐藏任务页签与四个频道工具 |
| 消息流 | `renderMessages` / `messageNode` | `components/MessageStream.vue` + `MessageItem.vue` | 日分隔、Markdown、提及、旁白降档、乐观发送、失败重试 |
| 轮次结果 | `roundOutcomeSummaryNode` 等 | `components/OutcomeRows.vue` | 摘要行折叠 + 失败行展开；不是消息（`data-base-seq`） |
| composer | `sendMessage` / 提及补全 | `components/ComposerBox.vue` + `MentionPicker.vue` | Enter / Shift+Enter、`@` 补全、归档只读 |
| 全局事件流 | `connectStream` | `src/lib/event-stream.js` | 一条流、断线重连、`rotate` 帧、缺 `target_id` 丢弃 |
| 标读 | `markCurrentRead` | `src/stores/workspace.js` | 上界 = `last_seq` 与已渲染最大 seq 的较大值；按 target 各自防抖 |
| 词条表 | `studio/i18n.js` | 同一份（桥接层 `src/i18n/index.js`） | **不复制**：桥只把 `locale` 做成响应式依赖 |
| 正文渲染器 | `studio/markdown.js` | 同一份（桥接层 `src/lib/markdown.js`） | **不复制**：`MarkdownBody.vue` 只 `appendChild` 挂载 |
| 运行面板（dock 第三种形态） | `renderRunPanel` 族 | `components/DockPanel.vue` + `components/RunPanel.vue` + `stores/run-panel.js` + `lib/run-panel.js` | 四段（运行与分派 / 成员事件 / Plan / SOP）；快照 + live 按后端 id 合并；重连按快照兜底 |

## 二、门禁读数

### 前端（Vitest）

```sh
cd app/frontend && env -u NODE_ENV npx vitest run
```

```
 ✓ tests/build-guard.test.js (6 tests)
 ✓ tests/mentions.test.js (19 tests)
 ✓ tests/event-stream.test.js (10 tests)
 ✓ tests/i18n-bridge.test.js (6 tests)
 ✓ tests/workspace-store.test.js (19 tests)
 ✓ tests/app-context.test.js (9 tests)
 ✓ tests/ui-state.test.js (4 tests)
 ✓ tests/components.test.js (55 tests)

 Test Files  8 passed (8)
      Tests  128 passed (128)
```

### 旧页面用例族（未被削弱）

```sh
node --test app/tests/*.test.cjs
```

```
ℹ tests 389
ℹ pass 389
ℹ fail 0
```

### 产物新鲜度守卫（正反两向都验过）

正向（源码与产物一致）→ 绿；反向（在 `src/lib/format.js` 尾部加一行注释、不重新构建）→ 红：

```
E       AssertionError: 源码指纹与产物不一致：改了 app/frontend/src 却没重新构建。
E         - eff2d42e5ac80fa5c97ed8d75bd8d9365b6f78b21ebb080afa4a0a2b77aba452
E         + 52e8dd426ee09e7947771537beb75af0a73f98ec6694ef2cf4ab15688fef76d6
```

构建可复现：同一份源码连续构建两次，`assets/*.js` 与 `index.html` 的 md5 相同
（`5e9684a16d10d28c68fcb441b573fa56` / `74062f615ead30f99aeafc2fe8b65aae`）。

## 三、真浏览器实测（ego-browser）

环境：`python -m alibot --port 8799 --data-dir /tmp/alibot-v2-data`，浏览器打开
`http://127.0.0.1:8799/studio/v2/`。

| 判据 | 读数 |
| --- | --- |
| 页面装配 | `#app` 有内容、`[data-od-id="app-shell"]` 存在 |
| 词条与渲染器加载 | `window.AlibotI18n` 存在、`window.renderMarkdown` 是函数 |
| 设置铺到根元素 | `<html lang="zh-CN" data-theme="system" data-density="comfortable">` |
| 左栏频道行 | `['#design']`，点开后 `is-active` |
| 消息流 | `data-seq` = `['1','2']`；`.markdown-body strong` = `['加粗']`；`li` = `['列表项','第二项']`；`.day-sep` = 1 |
| 频道头 | `#chanTitle` = `design`；`#chanDesc` = `设计频道`；placeholder = `发消息到 #design —— 输入 @ 提及 Agent` |
| 从 UI 发送 | 输入后发送键启用 → 点发送 → `data-seq` 变 `['1','2','3']`、正文是刚发的那条、输入框清空、无 pending 残留 |
| SSE 实时追加 | 页面外 `POST` 一条 → 2 秒内 `data-seq` 变 `['1','2','3','4']`，最后一条正文是「SSE 推来的第四条」 |
| 左栏 Agent 行 | 建 Agent 并加入频道后出现 `AAda`，状态点 `presence online` |
| `@` 补全 | 输入 `@A` → 清单出现、候选 `['AAda']`、提示行换成 `↑↓ 选择 · Enter 选中 · Esc 关闭` |
| 补全写回 | Enter 选中 → 输入框变 `@Ada `、清单关闭、提示行回到 `Enter 发送 · Shift+Enter 换行`、发送键启用 |

### 移动端抽屉壳（≤900px）

用 CDP `Emulation.setDeviceMetricsOverride` 把视口压到 800×900（无头会话没有持续 vsync，
因此用 `document.getAnimations().forEach(a => a.finish())` 把过渡推进到终点，读到的才是动画结束后的真实值）：

```
closed      : transform matrix(1,0,0,1,-252,0)  visibility hidden   x -252
opened      : transform none                    visibility visible  x 0
closedAgain : transform matrix(1,0,0,1,-252,0)  visibility hidden   x -252
```

同时 `<900px` 时顶栏 `display: flex`、标题是 `#design`；`aria-expanded` 与遮罩节点的出现/消失
与 `.is-open` 三者同进同出。

### 主题与密度

设置铺到根元素后 CSS 立即生效（不需要刷新）：
`data-theme='dark'` ⇒ `body` 背景 `oklch(0.21 0.012 85)`；`data-density='compact'` ⇒ `.msg` 的
`padding` 由 `5px 8px` 变 `2px 8px`。

### 附件与断开重连（本轮补的欠账）

真浏览器（同一台后端、`/tmp/alibot-v2-data2`）：建频道 → 上传 `note.txt`（21 B）→ 带
`attachment_ids` 发一条消息。

```
attachments: 1   tag: A   href: /api/attachments/att_e919053cb39f
download: note.txt   aria-label: note.txt（21 B），下载
页面内 fetch 该 href ⇒ status 200、正文 'hello attachment body'
```

假入口全部消失（同一页面读 `querySelector` 全为 false）：`btn-attach` / `btn-as-task` /
`btn-workspace-search` / `btn-reminders` / `btn-settings` / `btn-channel-members` / `#tabTasks`；
`btn-new-channel` 存在但 `disabled === true`，`title` 是「这个入口还没迁到 Vue 版工作台，请用 /studio/ 打开」；
rail 的 Agent 行 `tagName === 'DIV'`（只读行，不再是按钮），状态点仍在。

断开重连（杀掉后端进程 → 再起）：
`#connBanner` 出现（文案「后端不可达…」）、composer 加 `is-disabled`；重启后端后横幅消失，
服务端日志里出现**一次**完整的全量重取（`GET /api/workspace` + messages + round-outcomes +
members + member-statuses），SSE 连接数 1。

### 旧页面回归

同一台后端、同一份数据，`/studio/`（旧入口）照常工作：`app-shell` 存在、频道行 `['#design']`、
`window.renderMarkdown` 是函数、`window.AlibotI18n` 存在。

旧页面**文件**这一轮只被两处**纯新增**碰过，都不改它的行为：

| 文件 | 改动 | 为什么必须动 |
| --- | --- | --- |
| `app/alibot/studio/i18n.js` | 两份取值各加一条 `app.notMigrated` | 词条表只有一份（ADR-0038 §三），新增界面文案必须进它，且两份语言取值必须覆盖同一键集 |
| `app/alibot/studio/app.css` | 加 `.nav-row.is-static`（取消 hover 高亮与指针光标） | 只读行的样式落点；旧页面从不使用这个类，它的行行为一个字不变 |

截图：`.scratch/v2-check/v2-channel.png`（三栏布局、Markdown 列表、composer 与左栏状态点）、
`.scratch/v2-check/v2-mobile-open.png`（移动端抽屉）。

### 实测中发现并修掉的缺陷

**页面白屏 + 主线程卡死**：`createAppContext` 里挂了
`watch(() => mutable.settings, applySettings, { deep: true })`，而 `applySettings` 自己写
`mutable.settings` ⇒ 自触发无限循环。症状是 `page.goto` / `page.reload` 超时、`page.evaluate` 也超时
（渲染进程忙死）。

修法：删掉那个 watcher，`applySettings` 成为**唯一写入口**（启动读回、`loadWorkspace` 读回、设置弹窗
写回都显式调它），并在函数文档里写明为什么不能挂 watcher。这类缺陷 Node 侧测不出来（jsdom 里
`watch` 的同步递归会直接抛栈溢出而不是卡死），只有真浏览器能暴露——这也是本票坚持跑 ego-browser
的理由。

## 四、已知边界（**不是**缺陷，是分阶段迁移的边界）

v2 页面**只承诺已迁完的链路**。以下面仍由旧页面 `/studio/` 承载：

任务看板与任务线程、审批卡、Agent 详情、搜索与 ⌘K 命令面板、提醒 / 设置 / 频道配置 /
回应预算 / 成员名册等弹窗、附件**上传**、新建频道 / 新建私信 / 新建 Agent 的表单、
**任务运行面板**（它挂在任务线程头，随线程那张票进来；频道运行面板已迁）。

**这些面在 v2 上不留假入口**（ADR-0038 §五），分两种处理：

| 处理 | 入口 | 理由 |
| --- | --- | --- |
| **不渲染** | 工作台搜索、提醒、设置（rail）；Agent 详情（rail 的 Agent 行因此是只读行）；成员名册 / 频道设置 / 回应预算（频道头）；任务页签；附件上传与「作为任务发送」（composer）；任务引用 chip 与频道指针 chip（消息）；「看成员状态」（失败行） | 它们只是**把人带到另一处**，带不到就不该出现 |
| **可见但禁用**（`title` 说明理由） | 新建频道 / 新建 Agent | 空库里唯一的引导文案就是「新建一个频道」——把按钮也藏掉等于让人无路可走 |

频道头的四个入口不归同一张票管，因此**开关分开了**：`channelToolsAvailable`（名册 / 配置 / 预算，
默认关）与 `runPanelAvailable`（运行面板，**已迁 ⇒ 默认开**）。迁完一个面翻一个开关，组件不用改。
运行面板入口与成员名册同理，在**私信下不存在**（私信没有 team 运行）。

**附件的读与写分开了**：消息里**收到**的附件照常渲染成可下载的原生 `<a download>`（绑定与否决定
是不是链接）；**上传**入口等到那张票再回来。

## 四·补、第一阶段自查修掉的 6 处欠账

第一阶段交付后自查发现 6 处问题，本轮全部修掉并补了用例（`tests/components.test.js`、
`tests/ui-state.test.js`、`tests/app-context.test.js`、`tests/workspace-store.test.js`）：

| # | 问题 | 修法 | 判据 |
| --- | --- | --- | --- |
| 1 | 消息里的**附件不渲染**（旧页面有下载入口，v2 上文件凭空消失） | 新增 `AttachmentChip.vue`；`MessageItem` 在系统消息与对话消息两个分支都挂 `.msg-attachments` | 「已绑定 ⇒ `<a download>`；草稿 ⇒ `<span>`；无附件 ⇒ 整块不占版面」 |
| 2 | 频道流的**指针 chip / 引用 chip 不渲染** | 随任务面整票不动；这两个 chip 属于任务面，现在**显式不渲染**并在文件头记明 | 「消息带 `task_ref` / `task_pointer` 时不产出 `.task-ref-chip`」 |
| 3 | 4 个**假按钮**（composer 的附件 / 作为任务发送、消息里的 open-task、失败行的 inspect）**外加**自查才发现的 9 个（rail 的搜索 / 提醒 / 设置 / Agent 行，频道头的四个工具，任务页签） | 未迁面的**导航入口**整块不渲染；空工作台的出路（新建）改为**禁用 + `title` 说明** | 「默认不渲染」与「开关打开时照常出现」正反两条都断言 |
| 4 | 切 target 时**没清折叠态**（`resetForTarget()` 定义了却没人调用） | `App.vue` 的 `selectTarget()` 在换 target **之前**清（顺序反了会清掉新 target 刚铺的状态） | 折叠态按 seq 认，跨 target 不通用——`ui-state.test.js` 四条 |
| 5 | `firstSuccessfulLoad` 是**死返回值**（声明后从未置真） | store 拿到模块级 `workspaceLoaded`：整轮读数全部落地才置真；返回值区分「这一次尝试成功」与「第一次成功」 | 「首次为真、静默刷新为假、首载失败后重连成功仍为真」三条 |
| 6 | **重连成功后没有全量重取**（全局流不补拉，等于断线期间的变化永远丢了，且界面上毫无异常） | `onOpen({reconnected})` ⇒ `loadWorkspace({silent: true, keepActive: true})` + 标读 | 单测：重连后 `/api/workspace` 请求数 1→2；首连不触发。浏览器实测：杀后端→横幅出现→重启→横幅消失且日志里出现**一次**全量重取 |

退路：删掉 `app/alibot/studio/v2/` 与 `app/frontend/` 即回到重构前；旧页面代码一行未改。

## 四·补二、第二轮：运行面板（dock 的第三种形态）

第一轮的清单里「运行面板」排在建议顺序第一位（只读为主、最简单）。本轮的交付与判据：

| 判据 | 落点 | 说明 |
| --- | --- | --- |
| 四段的 DOM 契约 | `components/RunPanel.vue` | 运行与分派 / 成员事件 / Plan / SOP；类名与 `data-od-id` 逐字沿用旧页面（`.rp-run` / `.rp-assign` / `.rp-member` / `.rp-plan-task` / `.rp-step` / `.rp-verdict`）、`data-state` 恒为后端原词，**文案**才走词条 |
| 合并判据（纯函数） | `lib/run-panel.js` | 分派的**秩守卫**（迟到的 `started` 不把终态降回去）、Plan 的 `(plan_run_id, task.id)` 覆盖与 `deleted_at` 移除、SOP 的**终态不回退**、`overlayMarker`（快照之后才应用 Plan / SOP 的 live 帧） |
| 读数与簿记 | `stores/run-panel.js` | 打开 / 刷新 / 重连各读一次快照；`task_id` 过滤；版本守卫（晚到的响应或已关掉的面板不落地）；SOP 的**两步读取**（先按 live 见过的 run id 读 run，再按 definition id 读定义——步骤名只在定义里） |
| live 帧的会话内日志 | `lib/live-events.js` | 按 target 有界保留 256 条；`sop_run_id` 的唯一来源；**绝不**写 `seenSeqs` / 消息流 / 未读 / 搜索 |
| 重连兜底 | `app-context.js` 的 `onOpen({reconnected})` | live 帧不补拉（ADR-0020），断线期间的分派 / Plan / SOP 变化只能靠这份快照补齐；面板没开时是空操作 |
| 换 target 作废 | `App.vue` 的 `selectTarget()` | 面板的快照与 live 归属都只属于旧 target；换 target 时整个作废并收起抽屉 |
| 自主模式的空态解释 | `RunPanel.vue` + `store.loadChannelConfig()` | 只读**已读回的**配置缓存；读不到就铺通用空态，**不**把一次网络抖动说成频道是自主模式 |

门禁读数（`scripts/check.sh`，exit 0）：

```
pytest        1252 passed / 11 skipped
ruff / mypy / pyright   干净
node --test app/tests/*.test.cjs        389 passed（旧页面用例族未削弱）
npx vitest run（app/frontend）          9 files / 171 passed（新增 tests/run-panel.test.js 42 条）
```

真浏览器实测（ego-browser，真 uvicorn + 真 SSE；快照走库层真实写方法落库，live 帧走 `events.py`
的构造函数 + `EventBus`，因此帧与生产同形）：

| 判据 | 读数 |
| --- | --- |
| 入口 | 频道下 `[data-od-id="btn-run-panel"]` 存在；私信下不存在；成员名册入口仍不存在（未迁） |
| 三栏几何 | rail 252 / main 自适应 / dock **380**；dock 类名 `dock is-open` |
| 首次快照 | 标题 `design`、副标题「成员过程 · 分派 · Plan · SOP（只读过程面）」；四段 `rp-runs` / `rp-events` / `rp-plan` / `rp-sop`；运行卡队长 `Ada` + pill「已完成」`data-state=completed` + `#7a0a08`；分派「成功 · 耗时 180s · 成员结论：改了三个类名」；Plan 三态「已完成 / 进行中 / 待开始」 |
| live `agent_event` | 成员事件段出现 `Bob`、`×1`、`TEXT_BLOCK_DELTA 成员正在整理结论（live 帧）`、`TOOL_CALL_START Read` |
| live `team_assignment` | 同一分派行由 `succeeded` 变 `failed`（**不新插一行**） |
| live `plan_update` | 第 2 条由 `in_progress` 变 `completed`，其余不动 |
| live `sop_event` | SOP 段出现 `#ive001`、步骤「起草」、`未通过`、`缺少验收命令（live 帧）` |
| 刷新 | 点刷新只重读 `team-runs` + `plan` 各一次（`/api/targets/{id}/team-runs?limit=20`、`/plan`），不改别的 |
| 关闭 | 面板与整个 dock 节点一起消失（桌面常驻列也是「没有形态就不占位」） |
| 换 target | 切到另一个频道后运行面板整个消失、旧分派不再留在新频道旁边 |
| 断线 | 横幅「后端不可达…」出现、composer 禁用、面板仍显示断线前的读数 |
| 重连 | 横幅消失；请求序里出现**一次** `team-runs` + `plan` 快照兜底（在全量重取之前） |
| 移动端（800×900） | `dock is-open`、`transform: none`、`visibility: visible`、left 420（= 800−380）；顶栏 `display: flex`；点运行面板自己的关闭键后 dock 节点整个消失 |

截图：`.scratch/v2-check/v2-run-panel.png`（桌面三栏 + 四段含 live 帧）、
`.scratch/v2-check/v2-run-panel-mobile.png`（≤900px 右抽屉）。

### 实测中发现并修掉的两件事

| # | 问题 | 判据为什么测不到 | 修法 |
| --- | --- | --- | --- |
| 1 | `DockPanel.vue` **漏把 `pools` 传给 `RunPanel`** ⇒ 运行卡显示 `mem_74dabd617be5` 而不是 `Ada` | 单测直接挂 `RunPanel`（自己传 `pools`），绕过了外壳这一层 | `DockPanel` 补上 `pools` prop 并往下传；新增一条**从 `DockPanel` 进**的用例断言「名字不是 `mem_` 开头」 |
| 2 | 早期取证时 SOP 段一直是空态 | 不是缺陷：「SOP run id 只能从 live 帧得知」是后端的既有事实（没有「按 target 列 SOP run」的读面），因此**首开 + 从未收到 `sop_event`** 就是空态。旧页面同一条 | 用例里显式先喂一条 `sop_event` 再断言，并在 `RunPanel.vue` 与 `stores/run-panel.js` 的文件头写明这条来源 |

### 本轮的已知边界

- **任务运行面板**（`btnThreadRunPanel`）没迁：它挂在任务线程头，随线程那张票进来。
  `stores/run-panel.js` 的 `open({taskId, targetId})` 已经能接这条路径（`task_id` 过滤与标题取值都在），
  缺的只是任务线程那个入口本身。
- **dock 的另两棵子树**（频道成员 / 任务线程）在本版**根本不渲染**，不是 `hidden`：占着位置、
  看不见又可能被 Tab 命中的空容器比不渲染更难解释。
- **SOP 段在「首开且本次会话没见过 sop_event」时是空态**，这是后端的读面形状决定的，不是前端的漏。

## 五、被修订的既有纪律（逐条记账）

| 纪律 | 修订 | 依据 |
| --- | --- | --- |
| 「仓库没有构建步骤」（ADR-0037 §二） | 构建期允许依赖；**运行时仍零依赖**（后端启动路径不需要 Node） | ADR-0038 §一 |
| 「不新开第二套前端测试框架」 | 按**页面范围**读：旧页面不新开；v2 是 Vue 组件，用 Vitest 是同一层选择 | ADR-0038 §四 |
| pytest 只断言外部可观察行为 | **不变**。产物新鲜度判据没有塞进 pytest（曾写过 `test_studio_v2_build_guard.py`，被 `test_test_layering_guard.py` 判红后移到前端用例族） | ADR-0038 §六 |
| 「DOM 一律 createElement / textContent」 | **不变**，且扩到 Vue：组件里不出现 `v-html`，正文仍由渲染器产出节点树后 `appendChild` | ADR-0038 §二 |
