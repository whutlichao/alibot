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

任务看板与任务线程、审批卡、Agent 详情、搜索与 ⌘K 命令面板、频道配置 / 回应预算 /
成员名册等弹窗、附件**上传**、新建频道 / 新建私信 / 新建 Agent 的表单、
**任务运行面板**（它挂在任务线程头，随线程那张票进来；频道运行面板已迁）。
（提醒与设置两张弹窗已随阶段 A 的票 #302 迁完，不再在这个清单里。）

**这些面在 v2 上不留假入口**（ADR-0038 §五），分两种处理：

| 处理 | 入口 | 理由 |
| --- | --- | --- |
| **不渲染** | 工作台搜索（rail）；Agent 详情（rail 的 Agent 行因此是只读行）；成员名册 / 频道设置 / 回应预算（频道头）；任务页签；附件上传与「作为任务发送」（composer）；任务引用 chip 与频道指针 chip（消息）；「看成员状态」（失败行） | 它们只是**把人带到另一处**，带不到就不该出现 |
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

### 阶段 A · 票 #300：共享弹窗壳 ModalShell + dock 形态扩到三档（读数）

本票是阶段 A 的 **P0**：先把「弹窗外壳」与「dock 三档形态」抽出来，让 T1–T5 与 ⌘K 只写自己的内容。

| 判据 | 落点 | 读数 / 说明 |
| --- | --- | --- |
| 弹窗骨架（四个 slot 位与类名） | `components/ModalShell.vue` | 根 `.overlay` / 内容 `.modal`（`:class="{ wide }"`）/ `.modal-head` / `.modal-body` / `.modal-foot`，**逐字沿用**旧页面十个 `#*Overlay` 段；`app.css` 零改动（`git diff app/alibot/studio/app.css` 为空） |
| 显隐用 `hidden` 属性而不是 `v-if` | 同上 | `.overlay` 的 `hidden` 随 `open` 变，内容节点在开 / 关之间是**同一个 DOM 节点**（用例断言 `element` 引用相等，人填了一半的表单不被撕毁） |
| ESC 只关最上面那一层 | `lib/modal-stack.js` | 模块级栈 + **一个** `document` 捕获阶段监听；叠两层时第一次 ESC 只关上层、下层仍 `hidden:false`，第二次才关下层。旧页面 `app.js:10578-10620` 是无栈、一次全关——这里是**有意改进** |
| `busy` 时整层不收 | `ModalShell.vue` 的 `requestClose()` 与栈的 `isBusy()` | ESC、遮罩点击、关闭键三处同一条判据（等价于旧页面对删除确认弹窗的 `state.agentDelete.busy`，`app.js:10075-10086`） |
| 焦点进入与回收 | 同上的 `firstFocusable()` / `restoreFocus()` | 打开 ⇒ `.modal-body` 里第一个可聚焦元素（跳过 `disabled` 与 `hidden` 子树；正文没有可聚焦件时才落到标题行的关闭键）；关闭 ⇒ 打开那一刻的 `document.activeElement`（不是 `body`） |
| dock 三档由 `dockTarget` 推导 | `components/DockPanel.vue` | `dockTarget = { kind: 'run' \| 'members' \| 'task' }`；`null` ⇒ 整栏不渲染；`run` 档没有 `panel` 也按「没有形态」处理（不铺空壳）。`run` 接 `RunPanel`（`pools` 照旧往下传），另两档是 `<slot name="members">` / `<slot name="task">`，内容归 T3 / T7 |
| 私信下一档都不出现 | `App.vue` 的 `visibleDockTarget` | 私信下 `dockTarget` 恒 `null`（`dockAvailable()` 判据：私信既没有 team 运行也没有频道成员），整栏消失 |
| ESC 层序表 | `App.vue` 的 `onEscape()` | **命令面板 > 模态栈最上层（`busy` 时不收）> 移动左抽屉 > ≤900px 右抽屉 > 搜索视图**，依据 `app.js:10578-10620` 的逐行顺序；T4（#304）直接复用这个函数 |

**测试装置上的一处取舍**：`App.vue` 没有新增 props / `defineExpose`——`mount(App, { props: { sourceFactory }, attachTo: document.body })`
配上 `fetch` 替身就足以驱动 dock 的三档判据与 ESC 层序（挂到 body 之后 `document.dispatchEvent` 与
`document.activeElement` 都是真实落点）。少一个测试专用的公开面，就少一处「只在测试里成立」的行为。

门禁读数：

```
npx vitest run（app/frontend）     11 files / 196 passed（新增 tests/modal-shell.test.js 14 条、
                                   tests/app-shell.test.js 6 条、components.test.js 的 dock 形态 5 条）
node --test app/tests/*.test.cjs   389 passed（旧页面用例族未削弱）
```

真浏览器实测（ego-browser，`python -m alibot --port 8803 --data-dir /tmp/alibot-p2a-300`）：

| 判据 | 读数 |
| --- | --- |
| 弹窗关闭态 | `.overlay#settingsOverlay`：`hidden=true`、`getComputedStyle().display='none'`，四个类名齐全 |
| 弹窗打开态 | `hidden=false`、`display='grid'`、遮罩色 `oklch(0.33 0.045 300 / 0.42)`；`.modal` 宽 **440**，`.modal.wide` 宽 **560** |
| 焦点进入 | 打开后 `document.activeElement` = `INPUT[data-od-id="settings-first"]`（正文第一个可聚焦元素，不是标题行的关闭键） |
| ESC 关闭 + 焦点回收 | ESC 后 `hidden=true`，`document.activeElement` = `BUTTON#opener[data-od-id="opener"]`（打开它的那个按钮） |
| 遮罩点击 | 点根 `.overlay` 自身 ⇒ `hidden=true`；点弹窗内部不关 |
| 叠两层 | 两层都开 ⇒ ESC #1 `{lower:false, upper:true}`（只关上层），ESC #2 `{lower:true, upper:true}` |
| `busy` | ESC 后 `hidden=false`、遮罩点击后 `hidden=false`；解除 `busy` 再 ESC ⇒ `hidden=true` |
| dock 形态与几何（桌面 1970 宽） | 无形态时 `#dock === null`；`run` 档 `.dock.is-open`、`getBoundingClientRect().width` = **380**、`transform: none` |
| 桌面 ESC 不关 dock | ESC 后 `#dock` 仍在、`cls='dock is-open'`、宽 380（它是常驻列） |
| 私信下 | 切到 `@Ada` 后 `#dock === null`，且 `btn-run-panel` 入口本身也不存在 |
| ≤900px 右抽屉（800×900） | 关闭时 `#dock === null`；打开后 `width=380`、`left=420`（= 800−380）、`transform: none`、`visibility: visible`、`cls='dock is-open'` |
| ESC 层序（800×900） | rail 与 dock 都开 ⇒ ESC #1 收 rail（`railOpen=false`、遮罩消失）而 dock 仍在；ESC #2 才收 dock（`#dock === null`） |

截图（`.scratch/v2-p2a/`，已 gitignore）：`issue-300-01-dock-run-380.png`（桌面 run 档 380）、
`issue-300-02-modal-open.png`（弹窗打开 + 焦点在第一个输入框）、`issue-300-03-modal-esc-closed.png`、
`issue-300-04-modal-stack-both.png`（叠两层）、`issue-300-05-modal-wide.png`（`.modal.wide` 560）、
`issue-300-06-dm-no-dock.png`（私信下整栏消失）、`issue-300-07-dock-mobile-drawer.png`（≤900px 右抽屉）、
`issue-300-08-dock-desktop-esc-kept.png`（桌面 ESC 不关 dock）、`issue-300-09-rail-and-dock.png`（层序现场）。

**本轮的已知边界**：产品 UI 里还**没有**任何入口会打开弹窗（它们归 T1–T3 与 ⌘K），因此弹窗的
真浏览器读数取自一个临时的 Vite harness 页（复用同一份 `app.css` 与同一个 `ModalShell.vue`，
取证后已删除、未入库）。dock 的 `members` / `task` 两档同理没有调用点，只在用例里按 `dockTarget`
直接驱动——那是「形态与几何先立起来」的代价，也是那两张票不必再改外壳的前提。

### 阶段 A · 票 #302：rail 弹窗族——提醒 + 设置（接活 applySettings）（读数）

rail 头的两枚入口（`btn-reminders` / `btn-settings`）在 v2 上原本整块不渲染（ADR-0038 §五的
「不渲染」档）。本票把两张弹窗搬过来，入口从「不渲染」翻成「正常工作」。

| 判据 | 落点 | 读数 / 说明 |
| --- | --- | --- |
| rail 头两枚入口 | `components/RailSidebar.vue` 的 `.rail-head` | `btn-reminders` / `btn-settings` 逐字沿用旧页面（`studio/index.html:69` / `:83`）的 `data-od-id` 与无障碍名；铃铛与齿轮用旧页面的 lucide 几何（`stroke-width` 同为 1.9） |
| 圆点角标的判据是「还有**未到点**的提醒」 | `stores/reminders.js` 的 `hasPending` | `state.reminders.some((r) => !r.fired_at)`；`fired` / `missed` 一律不算等待。判据住在数据层而不是模板里，于是「列表有行」与「圆点亮」不会被写成同一个条件 |
| 列表行 | `components/RemindersModal.vue` | 文本 · ` · #归属频道`（不归属时不出现）· 状态后缀 · 删除（`×` + `aria-label`）。`state` 只读后端原词：`pending` 的后缀是到期时刻本身、`fired` / `missed` 走词条；认不出的取值落回时刻那一档，不猜第三种语义 |
| 新增 = 文本 + 日期 + 钟点 | 同上 | 两个原生输入（`type="date"` 默认今天 / `type="time"` 默认 `09:00`），提交拼成 `YYYY-MM-DD HH:MM` 一个串（ADR-0017 §二）；合法形状由后端裁决。只清正文，日期回今天、钟点保留 |
| 写路径以响应的最新表覆盖镜像 | `stores/reminders.js` 的 `applyPayload` | 读（`GET`）与写（`POST` / `DELETE`）共用一个落点，不本地拼「看起来成功了」的行；失败时不改镜像、把后端的话留在弹窗里（`#reminderFormError`），弹窗不关 |
| 提醒的读取不阻塞主线 | `app-context.js` 的 `start()` / `onOpen({reconnected})` | 首开读一次、重连后随全量重取再读一次；读不到保持未亮，打开弹窗时再试并把失败话留在弹窗里（旧实现同一条） |
| 设置三个档位 | `components/SettingsModal.vue` | 语言 `<select>` + 密度两个 `.radio-card` + 主题三个 `.radio-card`；类名与 `data-od-id` 逐字沿用（`settings-locale` / `settings-density` / `settings-theme` / `density-*` / `theme-*` / `settings-done`），选中态是 `is-on` + `aria-pressed` |
| **`applySettings` 接活** | `App.vue` 把 `ctx.applySettings` 传进弹窗 | 保存成功 ⇒ `applySettings(响应.settings)` 铺 `<html lang>` / `data-theme` / `data-density`；失败 ⇒ 根元素与选中态都不动（不声称一个没落库的档位），错误话留在弹窗里、弹窗不关 |
| **绝不挂 watcher**（防回归） | 同上 | 用例「保存一次只铺一次根元素」数的是**根元素被写了几次**（不是函数被调了几次）；把 `watch(() => mutable.settings, applySettings, {deep:true})` 加回去，这条当场红（`Maximum recursive updates exceeded in component <App>`） |

**一条从真实父组件进的用例**：`tests/rail-modals.test.js` 全部 14 条都从 `App.vue` 进（真实
`createAppContext` + `fetch` 替身 + `attachTo: document.body`）。理由与阶段一那次
`DockPanel` 漏传 `pools` 相同：rail 的圆点要「后端读数 → store → prop」整条链、设置要真的
`applySettings`，直挂叶子组件两件事都测不出来。

门禁读数：

```
npx vitest run（app/frontend）     12 files / 211 passed（新增 tests/rail-modals.test.js 14 条；
                                   components.test.js 的「rail 入口」一条改成「已迁 ⇒ 渲染」）
node --test app/tests/*.test.cjs   389 passed（旧页面用例族未削弱）
```

红→绿（原样读数）：

```
# 第一条切片（rail 入口 + 圆点）
FAIL  tests/rail-modals.test.js > rail 头的弹窗入口 > 圆点：全部已提醒 / 已错过时**不出现**
AssertionError: expected false to be true // Object.is equality
 Test Files  1 failed (1)   Tests  3 failed (3)

# 第二条切片（提醒弹窗）
FAIL  tests/rail-modals.test.js > 提醒弹窗 > 新增：文本 + 日期 + 钟点拼成 `YYYY-MM-DD HH:MM`…
Error: Cannot call setValue on an empty DOMWrapper.
 Test Files  1 failed (1)   Tests  6 failed | 3 passed (9)

# 第三条切片（设置弹窗）
FAIL  tests/rail-modals.test.js > 设置弹窗 > 点「深色」⇒ PATCH 一档 + 根元素 data-theme 变 dark…
 Test Files  1 failed (1)   Tests  5 failed | 9 passed (14)

# 全绿
 Test Files  12 passed (12)   Tests  211 passed (211)
```

真浏览器实测（ego-browser，`python -m alibot --port 8805 --data-dir /tmp/alibot-p2a-302`，
`http://127.0.0.1:8805/studio/v2/`）：

| 判据 | 读数 |
| --- | --- |
| rail 头入口 | `#rail .rail-head` 两枚 `.icon-btn`：`btn-reminders`（`aria-label=提醒`）/ `btn-settings`（`aria-label=设置`）；无提醒时 `#reminderDot` 不存在 |
| 提醒弹窗骨架 | 点铃铛 ⇒ `#remindersOverlay.hidden=false`、`display: grid`、`.modal.wide` 宽 **560**；焦点落在 `reminder-text`（正文第一个可聚焦件，不是标题行关闭键） |
| 空态 | `还没有提醒` + `提醒保存在后端，不依赖这台浏览器。`；日期格默认今天 `2026-10-27`、钟点 `09:00` |
| 新增（无归属） | 填 `喝水` / `2030-03-04` / `07:05` → 点「添加提醒」⇒ 行出现：`喝水` + `state=2030-03-04 07:05`（`data-state=pending`）；正文清空、日期回今天、钟点保留 `07:05`；圆点出现（6px） |
| 新增**不产生消息** | 页面内包一层 `fetch` 计数 `/messages`：整段新增流程后 `messageRequests = 0`（也没有新的 target / 未读） |
| 归属频道 | 打开 `#design` 后再新增 ⇒ 行显示 `带频道归属的 · #design`；后端 `channel_id` 落在 `ch_0528efdbdb57` |
| 删除 + 圆点熄灭 | 两条 → 删一条 `rows=1  dot=true` → 删净 `rows=0  dot=false`，空态回来 |
| 已错过不亮圆点 | 后端投递器把一条过点提醒标成 `state=missed, fired_at=2026-10-26T23:44:23.236Z`；刷新页面后 `dot=false`，弹窗里那一行后缀是 `已错过`（`data-state=missed`） |
| 设置弹窗骨架 | 点齿轮 ⇒ `#settingsOverlay.hidden=false`、`display: grid`、宽 **440**（非 wide）；语言下拉 `zh-CN`；五个 `.radio-card` 的 `is-on` / `aria-pressed` 与当前档位一致（舒适 + 跟随系统） |
| 改主题 | 点「深色」⇒ `document.documentElement.dataset.theme = 'dark'`、`theme-dark` 带 `is-on`/`aria-pressed=true`、`theme-system` 摘掉 `is-on`；弹窗**不关** |
| 改密度 | 点「紧凑」⇒ `data-density='compact'`；`.msg` 的 `padding` 由 `5px 8px` 变 **`2px 8px`**（切回舒适又是 `5px 8px`），CSS 立即生效 |
| 切语言 | 选 `English` ⇒ `<html lang='en'>`、`settings-done` 文案变 `Done`、主题组标题变 `Theme`、rail 的铃铛 `title` 变 `Reminders` |
| 收尾 | 切回 `zh-CN` + 跟随系统 ⇒ `{lang: 'zh-CN', theme: 'system', density: 'comfortable'}` |

截图（`.scratch/v2-p2a/`，已 gitignore）：`issue-302-01-rail-head.png`（rail 头两枚入口、无圆点）、
`issue-302-02-reminders-empty.png`（`.modal.wide` 560 + 空态 + 日期默认今天）、
`issue-302-03-reminders-added.png`（新增一行 + 圆点出现）、
`issue-302-04-reminders-two-rows.png`（第二行带 `· #design` 归属）、
`issue-302-05-settings-open.png`（设置弹窗 440，选中态）、
`issue-302-06-settings-dark.png`（改为深色后根元素与选中态同步，弹窗不关）、
`issue-302-07-settings-en.png`（切英文后整页文案与 `<html lang>`）、
`issue-302-08-reminders-emptied.png`（删净后圆点熄灭）、
`issue-302-09-reminder-missed.png`（`state=missed` 的行 + 圆点不亮）。

**本轮的已知边界**：

- **旧页面目录一个字没动**：`app/alibot/studio/app.js` / `index.html` 零改动；`i18n.js` 与 `app.css` 也没动——
  提醒与设置的词条（`reminder.*` / `settings.*`）与类名（`.reminder-row` / `.icon-btn .dot` /
  `.radio-card` / `.modal.wide`）早就为这个面定义好了，v2 只是把它们用在同一个落点上。
- **频道配置 / 预算 / 名册三张弹窗仍归它们自己的票**：`ModalShell` 已经是共享外壳，那三张只需写内容。
- **提醒的到点投递仍由后端负责**：前端不比对时钟、不轮询；`state` 与 `fired_at` 都是后端派生的只读值。
### 阶段 A · 票 #301：新建频道 / 新建 Agent / 新建私信表单（读数）

本票让 v2 的**空工作台有出路**：`btn-new-channel` / `btn-new-agent` 从「可见但禁用」翻正，
三个新建对象（频道 / Agent / 私信）都接到后端既有写面上。外壳**没有**新造：两张表单的内容
套在 #300 的 `ModalShell` 里（ADR-0038 §五「弹窗的外壳与关闭语义只有一份」）。

| 判据 | 落点 | 说明 |
| --- | --- | --- |
| 新建频道表单 | `components/ChannelFormModal.vue` | 字段与旧页面逐字一致（名称 / 描述 / 初始成员胶囊）；类名与 `data-od-id` 沿用 `#channelOverlay` / `.member-pick` / `#channelFormError` / `btn-create-channel`；提交 `POST /api/channels` |
| 新建 Agent 表单 | `components/AgentFormModal.vue` | 名称 / 角色 / 提供商选择器 / 模型名 / 系统提示；`model_id = provider + ':' + model`；提交 `POST /api/agents`；提供商清单仍取 `/api/workspace` 的 `model_providers`（不留第二份常量表） |
| 远端数据流告知块 | 同一个 `AgentFormModal.vue` | **票面没写但必须做**（见下面「关键设计决定」）：当前 provider 是远端且未确认时显示，正文用 `GET /api/remote-data-flow` 的 `data_faces` 拼，确认走幂等的 `POST /api/remote-data-flow/ack` |
| 新建私信 | `RailSidebar.vue` 的 Agent 行 + `App.vue` 的 `openAgentRow()` | 幂等 `POST /api/dms {agent_id}`；`created` 决定「已创建」还是「已打开」的说法 |
| rail 的 Agent 行换回可点 | `RailSidebar.vue` 的 `#memberList` | 由**一个** switch 裁决：prop `agentDetailAvailable`（默认 `false`）关 ⇒ 打开私信，开 ⇒ 打开详情（详情面归 T10，本票只留判据） |
| 在途重复提交不产生两个对象 | 两张表单各自的 `busy` + `ModalShell` 的 `busy` | 提交键禁用 + `ModalShell` 挡掉 ESC / 遮罩 / 关闭键；用例断言「连点三次只发一次 POST」 |
| 错误留在弹窗内 | 两处 `#channelFormError` / `#agentFormError` | 不关弹窗、不吞错、人填的内容不丢；按钮退出 busy 可重试 |
| 建完自动切过去 | `App.vue` 的 `submitChannel()` → `selectTarget()` | `selectTarget` 是进入一个 target 的**唯一**入口，因此这条接线只能在 App 里（旧页面 `submitChannel` 同一条） |
| 建完 Agent 出现在 rail | `stores/workspace.js` 的 `createAgent()` | 响应回带整份 `agents` 与 `members`，落到镜像即可（列表正本在后端，前端不拼） |

**「至少一条从真实父组件进」是哪一条**：本票的接线判据几乎全部从 `App.vue` 进
（`tests/create-forms.test.js` 里 19 条用例中 15 条经 `mount(App, { attachTo: document.body })`），
只有「Agent 行点击语义由开关裁决」那一条直挂 `RailSidebar`（因为那段判据的输入正是 rail 的 prop，
不需要 App 的 store）。理由：`selectTarget`、`busy`、「建完谁出现在 rail 里」这三件事都由 App 裁决，
直挂叶子组件只能证明「表单自己长对了」，证明不了接线——阶段一实测漏过 `DockPanel` 的 `pools` 传递，
就是只看叶子组件的代价。**「建频道后当前选中切过去」这一条明确从 `App.vue` 进**
（`新建频道：建完就在 rail 里、并自动切过去 › 提交成功后：rail 名单出现新频道且它成了当前行`），
它断言的是 `#chanTitle` 变成新频道名 + 新行带 `is-active`，而不是表单自己发了什么事件。

#### 关键设计决定：为什么新建 Agent 弹窗里保留了「远端数据流告知块」

票面只写「字段与旧页面一致；提交 `POST /api/agents`」，没提告知块。**做它，是因为不做会打破票面
自己的验收项**：后端 `model_providers` 的首项是 `dashscope`（远端），而 `orchestration.availability()`
对「远端 provider 且 `remote_consent` 里没有这一行」的 Agent 一律判「未启用」。也就是说
**默认选择下建出来的 Agent 恒为「未启用」，而界面上没有任何一句解释为什么**——「建 Agent 后 rail 的
成员名单出现新 Agent」这条验收项表面上成立了（行出现了），实际是「出现了但永远哑掉」。

这不是「顺手多做一块 UI」，而是「写了却发不出去的对象」这条纪律的另一面（ADR-0038 §五
「不放按下去什么都不发生的入口」）。落点的三条边界：

- **只做创建弹窗里那一块**。Agent 详情页的同一确认面（`agentDetail.*`）归 T10，本票不搬。
- **复用既有词条与既有端点**：`agent.remoteTitle` / `remoteBody` / `remoteHint` / `remoteConfirm`、
  `GET /api/remote-data-flow`、`POST /api/remote-data-flow/ack`；不改后端，不新增 schema。
- **读不到就不编**：`data_faces` 读不到时正文换成新词条 `agent.remoteBodyUnavailable`，
  确认键不可点——不能把一个还没读到的空集合说成「没有数据会离开本机」，也不能渲染成空确认框。

#### 红 → 绿（原文）

**红 #1**（第一刀：rail 两个按钮翻正 + 新建频道表单从 `App.vue` 进；实现尚未落地）
```
 FAIL  tests/create-forms.test.js > 空库的出路：两个新建入口可用 > 点「新建频道」⇒ 弹窗打开，焦点落在名称输入框（正文第一个可聚焦件）
AssertionError: expected false to be true // Object.is equality
- Expected
+ Received
- true
+ false
 ❯ tests/create-forms.test.js:142:30
    142|     expect(overlay.exists()).toBe(true);

 FAIL  tests/create-forms.test.js > 空库的出路：两个新建入口可用 > 关掉再打开：上一次填的内容不残留（旧页面 openChannelModal 同一条）
Error: Cannot call setValue on an empty DOMWrapper.
 ❯ tests/create-forms.test.js:153:44

 Test Files  1 failed (1)
      Tests  3 failed (3)
```

**红 #2**（第二刀：远端告知块的确认在重复点击下发了两次；以及我自己写错的一条测试夹具）
```
 FAIL  tests/create-forms.test.js > 远端数据流告知块（建完不可用的挡板） > 点确认 ⇒ 只发一次 ack、弹窗不关、告知块消失
AssertionError: expected [ { …(3) }, { …(3) } ] to have a length of 1 but got 2
- Expected
+ Received
- 1
+ 2
 ❯ tests/create-forms.test.js:493:8

 FAIL  tests/create-forms.test.js > rail 的 Agent 行：关着详情开关时打开私信 > 已有私信时用「已打开」而不是「已创建」的说法（后端 `created` 决定）
AssertionError: expected '已打开与 Bob 的现有私信。' to contain '已打开与 Ada 的现有私信'
 ❯ tests/create-forms.test.js:555:62

 Test Files  1 failed (1)
      Tests  2 failed | 17 passed (19)
```
（第一条是真缺陷：`emit('acknowledge', …)` **没有返回值**，组件 `await` 不到后端往返，于是
`ackBusy` 在同一个微任务里就被复位，连点两次发两次。修法是把确认改成与 `submit` 同形的
**prop 回调**（`props.acknowledge(provider)` 返回 promise），复用 `busy` 那一套。
第二条是我的夹具把 `dm.peer` 写成了 Bob 却断言 Ada——夹具修正，不是产品缺陷。）

**红 #3**（构建产物新鲜度守卫，改了 `src/` 还没构建）
```
 FAIL  tests/build-guard.test.js > 构建产物 > 源码没变时指纹一致（改了 src/ 就要重新构建并提交产物）
AssertionError: expected [ 'index.html', …(35) ] to deeply equal [ 'index.html', …(33) ]
    "src/app-context.js",
+   "src/components/AgentFormModal.vue",
    "src/components/AttachmentChip.vue",
+   "src/components/ChannelFormModal.vue",
    "src/components/ChannelHeader.vue",
```

**绿**
```
 ✓ tests/create-forms.test.js (19 tests)
 Test Files  12 passed (12)
      Tests  216 passed (216)
```
（构建后 `build-guard.test.js` 由红转绿；`tests/components.test.js` 里两条守「禁用 + `is-static`」的
旧断言同步改写成新契约——不改就没有意义地红，而那条契约本来就是本票要翻的。）

#### 门禁读数

```
npx vitest run（app/frontend）     12 files / 216 passed（新增 tests/create-forms.test.js 19 条）
node --test app/tests/*.test.cjs   389 passed（旧页面用例族未削弱）
scripts/check.sh --fast            exit 0（ruff / ruff format --check / mypy / pyright 干净）
```

#### 真浏览器实测（ego-browser，`python -m alibot --port 8804 --data-dir /tmp/alibot-p2a-301`，空库启动）

| 票面验收项 | 读数 | 截图 |
| --- | --- | --- |
| `btn-new-channel` / `btn-new-agent` 都可点 | 两个按钮 `disabled: false`；`title` 分别是 `null` / `"新建 Agent"`（`app.notMigrated` 已不在任何一个上，页面 `innerText` 里也搜不到） | `issue-301-01-new-channel-empty.png` |
| 建频道后 rail 出现新频道且**当前选中**已切过去 | `#channelList` → `[{id: ch_3a6d21b41a0a, text: "#design", active: true, ariaCurrent: "true"}]`；`#chanTitle` = `design`、`#chanDesc` = `设计频道`；placeholder 换成 `发消息到 #design —— 输入 @ 提及 Agent`；toast「已创建频道 #design，可以直接发送第一条消息。」 | `issue-301-02-channel-created-active.png` |
| 建 Agent 后 rail 成员名单出现新 Agent | ack 后 `overlayStillOpen: true`（弹窗不关）+ `noticeGone: true` + toast「已记录数据流告知确认：dashscope。」；建完 `#memberList` → `[{id: mem_ce4d8e354187, tag: BUTTON, text: "AAda", label: "打开与 Ada 的私信", presence: "offline"}]`，弹窗 `hidden: true` | `issue-301-04-agent-created-in-rail.png` |
| 远端告知块（票面没要求、本票的补充裁定） | 打开弹窗即 `noticeVisible: true`；标题「启用 DashScope（远端） 前需要你确认一次」、正文「…消息正文、发送者显示名、频道名称与描述、被点名成员的显示名…」、`ackDisabled: false`；provider 选项 11 个且默认 `dashscope` | `issue-301-03-new-agent-remote-notice.png` |
| 点 rail 的 Agent 行（`agentDetailAvailable` 关）⇒ 出现/切到私信 | **部分取到**：行本身 `tag: BUTTON`、`is-static: false`、`aria-label: "打开与 Ada 的私信"`（只读行确实换回了可点按钮）；点击后后端 `GET /api/workspace` 的 `dms` 里确实多了 `('dm_1731dc07edbe', 'Ada')`（写路径落地），但**那一帧的界面切换没读到**——截图里 `#chanTitle` 仍是 `design`、`#dmList` 仍为空 | `issue-301-05-dm-opened-from-rail.png`（**反面证据**，见下） |
| 提交失败：弹窗不关、错误可见、按钮可重试 / 在途重复提交只发一次 | 只有 Vitest 读数（`#channelFormError` / `#agentFormError` 断言 + 连点三次只 1 次 POST），**未取到浏览器读数** | — |

截图（`.scratch/v2-p2a/`，已 gitignore）：`issue-301-01-new-channel-empty.png`、
`issue-301-02-channel-created-active.png`、`issue-301-03-new-agent-remote-notice.png`、
`issue-301-04-agent-created-in-rail.png`、`issue-301-05-dm-opened-from-rail.png`。

#### 本轮的已知边界

- **浏览器那轮被环境打断，不是实现问题**：验收进行到「点 Agent 行开私信」时，此前反复 `goto`
  留下的十几条 SSE 长连接把 Chrome 每主机 6 连接的上限占满，`page.goto` 开始 `document.readyState`
  永远停在 `loading`（静态与接口用 `curl` 单独探都正常：`/studio/v2/` 200、`/api/workspace` 200）。
  重启后端仍未清干净，随后撞上 30 分钟硬超时。因此**「私信出现/切到」与「提交失败留在弹窗内」
  两条只有用例读数**，其中前者另有「后端确实建出了 `dm_1731dc07edbe`」这条旁证。
  复验办法：干净起一台后端、只开一个页面，点 Agent 行后读 `#chanTitle` 应为 `@Ada`、`#dmList`
  出现该行且带 `is-active`（用例 `点 Agent 行 ⇒ 发一次幂等的 POST /api/dms {agent_id} 并切到该私信`
  断言的就是这一组 DOM）。
- **Agent 详情面仍归 T10**：`agentDetailAvailable` 只是把开关与两段语义摆好（开 ⇒ 详情、关 ⇒ 私信），
  详情本身一行没写。T10 落地时**只翻这个 prop 的默认值**，不改 `RailSidebar.vue` 里那段点击逻辑。
- **私信的去重口径**：`POST /api/dms` 幂等（后端 `created` 区分），因此重复点同一个 Agent 行不会
  建出第二条私信——这一点由后端的幂等保证，前端不另加去重表。

### 阶段 A · 票 #304：搜索主线视图 + ⌘K 命令面板（读数）

本票把 v2 从「只有 `channel` 一态」推到**两个主线视图**：搜索视图（占消息流那块位置）与
消息流；同时把 rail 的 `btn-workspace-search` 从「不渲染」翻正，并把 ⌘K 命令面板接上。
旧落点是 `studio/app.js:613-750`（`renderSearchResults` / `renderSearchFacets` / `showSearchView` /
`setSearchFacet` / `runSearch`）与 `:755-800`（`commandList` / `renderCmd` / `openCmd` / `closeCmd` /
`runCmd`）；旧页面文件**一行未改**。

#### 一、`mode: 'channel' | 'search'` —— 一个**新的状态维度**

票面明写「这是 ADR-0038 §五 里没提的新状态维度」，因此把它记在这里：v2 的呈现状态除了
「当前 target」「activeTab（消息 / 任务）」「dockTarget（dock 的形态）」之外，还多出
**主线视图**这一维。

| 判据 | 落点 | 说明 |
| --- | --- | --- |
| 主线视图是 `'channel' \| 'search'` 二选一 | `App.vue` 的 `const mode = ref('channel')` | 搜索**占住消息流那块位置**，不是盖住界面的弹层（旧页面 `showSearchView()` 的注释与人定 2026-10-17 的 D14）。因此没有遮罩、不进 `modal-stack`，显隐由 `mode` 驱动 |
| 为什么用 `App.vue` 的 `ref` 而不是新 store | 同上 | `stores/workspace.js` 的 `state` 是**后端镜像**（每个字段都能在 `/api/workspace` 等载荷里找到同名出点）；`mode` 是纯呈现状态，混进去会让「哪些字段是后端说的」这条边界失守。它又与 `activeTab` / `dockTarget` 同级（都由 App 裁决、都只影响呈现），放同一个 `ref` 层级最省事；不新建 `stores/view-mode.js` 是因为本票只有这一个字段，多一个模块只多一处要读的文件 |
| 搜索让位哪些面 | `App.vue` 模板的 `:hidden="searchActive"` | 频道头（含页签）与 composer 与消息流一起让位——旧 `syncChromeVisibility()` 的 `mode === 'search'` 分支同一条。用 `hidden` 而不是 `v-if`：这三块有「在这一帧之前填到一半的东西」（滚动位置、未发送的草稿） |
| dock 不受影响 | `visibleDockTarget` | dock 是右侧常驻列，与主线视图是**两个维度**（旧 `setViewMode()` 从不碰 dock） |
| 换 target 时退出搜索 | `selectTarget()` 的第一句 `showMessagesView()` | 旧 `selectTarget()` 的 `state.mode = 'channel'` 同一条。这句话放在「同 target 早退」**之前**：点当前那一条同样回消息流（旧页面没有那道早退） |
| 搜索的读数与请求 | `stores/search.js`（`createSearchStore()`） | 只做三件事：发请求、把命中落进响应式镜像、守版本 |

#### 二、搜索的五条行为判据

| 判据 | 落点 | 读数 / 说明 |
| --- | --- | --- |
| **120ms 防抖** | `stores/search.js` 的 `SEARCH_DEBOUNCE_MS` + `setQuery()` | 落笔后延后一拍发请求；连打 5 个字符只发 **1** 次。facet 切换与 Enter **立即执行**（Enter 先取消防抖那一拍，因此仍然只 1 次请求） |
| **facet 是后端的过滤参数** | `setFacet()` | 切档位 = **换一次请求**（`?q=&facet=`），前端**不对已取回的命中集再筛一遍**。用例断言请求次数 1→2 且第二发带新 facet，同时后端仍回三条 ⇒ 界面仍显示三条（前端没按 `kind` 过滤） |
| **四档 facet** | `lib/search.js` 的 `SEARCH_FACETS` + `facetLabel()` | `all` / `message` / `task` / `agent`，取值与顺序照原型 `:631-635`（也是后端 `SearchFacet` 的枚举）；选中态是 `.filter-chip.is-on` + `aria-pressed` |
| **空态三档各不相同** | `components/SearchView.vue` 的 `emptyState` | 未输入 ⇒ `search.idleTitle` / `idleBody`；零命中 ⇒ `search.emptyTitle`（带查询词）/ `emptyBody`；出错 ⇒ 结果面**不铺**「没有找到」，错误话自己占 `#searchError`。`searching` 是第四档（加载中）。**出错 ≠ 零命中**是旧 `renderSearchResults()` 的第一条分支，被本票原样搬过来 |
| **`search.offline`** | `stores/search.js` 的 `run()` | 断线（`isOnline()` 为假）时**不打请求**，直接 `state.error = t('search.offline')` |
| **版本守卫** | `stores/search.js` 的模块内 `let version` | 每发一次请求 +1；晚到的响应（成功**与**失败两条路径）在 `requestVersion !== version` 时整份丢弃。与 `stores/run-panel.js` 的版本守卫同形（照它写，不发明第二种） |
| 高亮只按后端区间切 | `lib/search.js` 的 `hitSegments()` | 只按 `highlight_ranges` 切文本段，越界 / 倒序 / 重叠 / 非整数的区间一律**跳过**（不修正、不重排——修一次就等于在猜后端想高亮哪里）；UTF-16 下标（与后端 `db.py` 的 `_search_highlight_ranges` 同口径），因此可直接 `slice` |
| 命中行是只读行 | `lib/search.js` 的 `hitIsInteractive()` / `SearchView.vue` | 旧页面只有任务命中可点（打开右侧任务线程），而任务线程随 T6 未迁 ⇒ 本版三类命中一行都不做 `button`。T6/T7 落地时只改 `hitIsInteractive()` |
| 查询词未输入时不发请求 | `setQuery()` 空串分支 + `setFacet()` 无查询词分支 | 空串 ⇒ 回到「未输入」空态并作废挂起的那一拍；无查询词时切档位只换选中态 |
| 离开搜索视图 | `search.leave()` | 只取消防抖那一拍（人已经在看消息流了，不该多发一次搜索）；**在途那一发照旧落地**（旧 `showMessagesView()` 不碰 `state.search`），于是再进来看到的是上一次的结果而不是空镜 |

#### 三、⌘K 命令面板

| 判据 | 落点 | 说明 |
| --- | --- | --- |
| **不套 `ModalShell`** | `components/CommandPalette.vue` | 协调者裁定（ADR-0038 §五）：根是 `.overlay` + `.cmd`，不是 `.overlay` + `.modal`；关闭语义与 z-index 档位都不同。因此它**不进** `lib/modal-stack.js` |
| **复用同一份 ESC 层序** | `App.vue` 的 `handleEscape()` | 第①格（命令面板）由本票补齐，剩余四格（模态栈 / 移动左抽屉 / ≤900px 右抽屉 / 搜索视图）**原样保留**（#300 立起来的那张表）。`⌘K`、`ESC`、`M`/`T` 共用**一个** document 监听（`onGlobalKeydown`），没有第二份层序 |
| `⌘K` / `Ctrl+K` 打开 | 同上 | `(metaKey \|\| ctrlKey) && key.toLowerCase() === 'k'` ⇒ `cmdOpen = true`（旧 `openCmd()`）。它真绑定的是**打开面板本身**，不是搜索视图——因此 rail 的搜索按钮上不印 `⌘K` 徽标 |
| 命令集 | `App.vue` 的 `commands` computed | 跳转（`view-messages` / `view-board`）+ 动作（`new-channel` / `new-task` / `toggle-dock`）+ **动态**频道组（`state.channels` 现读，不是写死的种子名） |
| **只呈现真绑定的快捷键** | 同上 | 只有 `M`（消息视图）与 `T`（任务看板）带 `kbd`；原型 `CMDS` 的 `N` / `⇧N` / `D` 没有绑定任何按键 ⇒ 不出现。用例断言面板里 `.cmd-item .kbd` 恰为 `['M','T']` |
| **稳定 id 做归属** | `data-cmd` | `view-messages` / `new-channel` / `channel:<后端 id>`；不用标签文本（频道名可能与某条命令同名，标签不是键） |
| 指向未迁面的命令是**禁用条目** | `disabled` + `title`（`app.notMigrated`） | 任务看板（T6）与新建任务（T7）还没迁 ⇒ `view-board` / `new-task` 是 `disabled` 条目，不渲染成按下去什么都不发生的假入口（ADR-0038 §五）。Enter 跑**第一条可执行的**，禁用条目占着头位时不吞掉一次回车 |
| 私信下 `toggle-dock` **不列** | `dockToggleAvailable` | 与频道头运行面板入口**同一份判据**（私信既没有 team 运行也没有频道成员）；是「这一条不列」，不是整块消失 |
| `M` / `T` 只在人不在输入框里时生效 | `onGlobalKeydown()` | `target.matches('input,textarea')` 就 return（旧页面同一条）；弹窗与命令面板开着时也不穿透 |
| 焦点 | `CommandPalette.vue` 的 `watch(open)` | 打开把焦点送进 `#cmdInput`（旧 `openCmd()` 最后一行）；每次打开清空筛选词 |
| 关闭的三条路径 | 同上 + `handleEscape()` | ESC（层序第①格）、点遮罩**自身**（`event.target === event.currentTarget`）、跑完一条命令后先关再动作（旧 `runCmd()`） |

#### 四、红 → 绿（原样读数）

**红 #1**（第一刀：rail 入口 + `mode` + 搜索视图三档空态；实现尚未落地）
```
 FAIL  tests/search-commands.test.js > 搜索视图是主线视图 > rail 有 btn-workspace-search；点击后主线进入搜索视图，消息流 / composer / 频道头让位
AssertionError: expected false to be true // Object.is equality
 ❯ tests/search-commands.test.js:184:28
    184|     expect(entry.exists()).toBe(true);

 FAIL  tests/search-commands.test.js > 搜索视图是主线视图 > 未输入：`搜索整个工作区` 空态，且不发请求
Error: Cannot call trigger on an empty DOMWrapper.
 ❯ openSearch tests/search-commands.test.js:159:60

 Test Files  1 failed (1)
      Tests  4 failed (4)
```

**红 #2**（第二刀：命令面板接线摘掉后单独复跑，证明那 9 条真的依赖它）
```
 FAIL  tests/search-commands.test.js > ⌘K 命令面板 > ⌘K 打开面板、ESC 关闭；面板里只有 M / T 两枚 kbd（N / ⇧N / D 不出现）
AssertionError: expected false to be true // Object.is equality
 FAIL  tests/search-commands.test.js > ⌘K 命令面板 > 命令集：跳转 + 动作 + 动态频道组；未迁面的两条是禁用条目（不是假入口）
AssertionError: expected [] to deeply equal [ 'view-messages', 'view-board', …(5) ]
 FAIL  tests/search-commands.test.js > ⌘K 命令面板 > 私信下 `toggle-dock` 那条命令不列（与频道头运行面板入口同一份判据）
AssertionError: expected [] to include 'view-messages'
 ...
      Tests  9 failed | 1 passed | 17 skipped (27)
```

**绿**
```
 Test Files  14 passed (14)
      Tests  262 passed (262)
```
（其中 `tests/search-commands.test.js` 31 条；`components.test.js` 里「工作台搜索还不渲染」那一条同步
改写成新契约「已迁 ⇒ 入口在 `.search-shell` 里、点它只发一个事件、按钮上没有 `kbd`」——不改就会
无意义地红，而那条契约本来就是本票要翻的。）

#### 五、门禁读数

```
npx vitest run（app/frontend）     14 files / 262 passed（新增 tests/search-commands.test.js 31 条）
node --test app/tests/*.test.cjs   389 passed / 0 failed（旧页面用例族未削弱）
scripts/check.sh --fast            exit 0（ruff check / ruff format --check / mypy / pyright 干净）
```
全量 `scripts/check.sh`（含 pytest）由协调者在集成分支统一跑一次，本票不重复跑。

#### 六、真浏览器实测（ego-browser，`python -m alibot --port 8807 --data-dir /tmp/alibot-p2a-304`）

造数：`POST /api/channels`（design / 设计频道）+ 两条 `POST /api/channels/{id}/messages`
（正文含「设计」）+ 一条 `POST /api/channels/{id}/tasks`（「设计系统 token 补齐」）+
`POST /api/agents`（Ada / 设计师 / `ollama:qwen-max`），于是四档 facet 都有真命中。

| 票面验收项 | 读数 | 截图 |
| --- | --- | --- |
| rail 出现 `btn-workspace-search`，点击进搜索视图 | 入口存在、`title` = 「搜索工作台」、文案「搜索消息、任务…」、按钮内**没有** `kbd`；点开后面板读数为 `searchView {hidden:false, display:flex, visible:true}` / `messagesView {hidden:true, display:none}` / `composer {hidden:true}` / `#chanHead.hidden = true`，焦点落在 `searchInput` | `issue-304-01-rail-search-entry.png`、`issue-304-02-search-idle.png` |
| 未输入空态 | `搜索整个工作区` + `消息不可编辑、不可删除；搜索覆盖消息、任务与 Agent。`，四档 chip 全部 `is-on/aria-pressed` 一致（`all` 选中），**不打请求** | `issue-304-02-search-idle.png` |
| 真查询（防抖） | 逐字输入「设计」后请求日志只有 **1** 条 `{q:'设计', facet:'all'}`；四行命中：两条消息（`频道 · #design` / `消息` / `.r-title mark` = `['设计']` / 片段「来自 Owner」）、一条任务（`#59f762 · 待认领`）、一条 Agent（`成员` / `空闲 · ollama:qwen-max`） | `issue-304-03-search-results.png` |
| 切 facet 发一次新请求 | 依次切 `message` → `task` → `agent` → `all`：请求数 **2 / 3 / 4 / 5**，每一发的 `facet` 与落点档位一致；命中行数随后端结果变（2 / 1 / 1 / 4），**不是前端筛出来的**；chip 的 `is-on:aria-pressed` 每档都对 | `issue-304-04-search-facet-task.png`、`issue-304-05-search-facet-agent-empty.png` |
| 零命中空态 | 输入 `zzz不存在` ⇒ `没有找到“zzz不存在”` + `换个关键词，或检查拼写。`，`#searchError.hidden = true` | `issue-304-06-search-zero-results.png` |
| 出错 ≠ 零命中 | `/api/search` 换成 reject（等价后端这条不可达）⇒ `#searchError` `hidden=false, display=block`，文案「搜索失败：后端不可达或请求未完成（模拟后端不可达）。」，结果面 `innerText` 为**空**且**没有** `.empty` 节点 | `issue-304-07-search-error.png` |
| `⌘K` 打开面板 | `#cmdOverlay` `hidden=false, display=grid`，焦点 `cmdInput`，分组 `['跳转','动作','频道']`，条目 6 条（`view-messages`/`view-board`/`new-channel`/`new-task`/`toggle-dock`/`channel:ch_71298c7f13be`），**`kbd` 恰为 `['M','T']`**；`view-board` 与 `new-task` 是 `disabled` 且 `title` = 「这个入口还没迁到 Vue 版工作台，请用 /studio/ 打开」 | `issue-304-08-cmd-palette.png` |
| ESC 关闭面板 | ESC 后 `#cmdOverlay` 整个消失 | — |
| 私信下 `toggle-dock` 不出现 | 点 rail 的 Agent 行 ⇒ `#chanTitle = '@Ada'`、`#dock` 不存在；`⌘K` 后命令 id 为 `['view-messages','view-board','new-channel','new-task','channel:ch_71298c7f13be']`，`hasToggleDock = false` | `issue-304-09-cmd-dm-no-toggle-dock.png` |
| `M` 只在人不在输入框里时生效 | 搜索视图里焦点在 `#searchInput` 时按 `m` ⇒ 只是打字（`inputValue = 'm'`、搜索视图仍在）；`blur()` 之后按 `m` ⇒ `searchVisible=false`、`messagesVisible=true`、`#chanHead` 可见 | `issue-304-10-m-back-to-messages.png` |
| `toggle-dock` 命令真的开 dock | 面板里点 `toggle-dock` ⇒ `#dock` 出现、`class='dock is-open'`、宽 **380**、`#rpTitle = 'design'` | `issue-304-11-cmd-toggle-dock.png` |
| 断线时 `search.offline`，不打请求 | 杀后端（`kill -9`）后 5 秒内 `#connBanner` 出现（「后端不可达：…」）；此时进搜索、输入「设计」⇒ `searchCalls = 0`、`#searchError` 文案「后端不可达：无法执行搜索。」、结果面为空 | `issue-304-12-search-offline.png` |
| `T` 在输入框内不生效 | 焦点在 composer 的 textarea 里按 `t` ⇒ `composerValue = 't'`、搜索视图未打开（`T` 也不切看板） | — |

截图（`.scratch/v2-p2a/`，已 gitignore）：`issue-304-01-rail-search-entry.png`、
`issue-304-01-channel-before-search.png`（进搜索前的消息流，3 条消息）、
`issue-304-02-search-idle.png`、`issue-304-03-search-results.png`、
`issue-304-04-search-facet-task.png`、`issue-304-05-search-facet-agent-empty.png`、
`issue-304-06-search-zero-results.png`、`issue-304-07-search-error.png`、
`issue-304-08-cmd-palette.png`、`issue-304-09-cmd-dm-no-toggle-dock.png`、
`issue-304-10-m-back-to-messages.png`、`issue-304-11-cmd-toggle-dock.png`、
`issue-304-12-search-offline.png`。

#### 七、本轮的已知边界

- **旧页面目录一个字没动**：`git diff` 对 `studio/app.js` / `index.html` / `i18n.js` / `app.css` 全为空
  ——`search.*`（24 条）与 `cmd.*`（8 条）词条早就在 `studio/i18n.js` 里，本票**一条词条都没新增**，
  `app.css` 也一分没改（`.search-view` / `.result` / `.cmd` 那一整套类名早就为这个面定义好了）。
- **任务看板与任务线程仍归 T6 / T7**：`view-board` / `new-task` 是禁用条目，`T` 按下去什么都不发生
  （落点还没迁）。任务命中行因此也是只读行（旧页面那条可点是打开右侧任务线程）。
- **Agent 详情仍归 T10**：搜索里的 Agent 命中同样是只读行。
- **搜索结果不随 `target_id` 收窄**：旧页面 `runSearch()` 只传 `q` + `facet`，`target_id` 是后端
  支持的参数但前端没用过；本票原样沿用（搜索是**全工作区**的，不是当前频道的）。
