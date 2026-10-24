# Studio Agent 详情头部精简验收

- 基线：`2497bcc26a72580569c48856b5da6c13d54c0634`（PR #257 合并点，本票 base）
- 分支：`studio-agent-detail-head`
- 面：`app/alibot/studio/**`（零构建前端）+ `app/tests/agent_detail_ui.test.cjs` + `app/README.md`
- 无后端 / API / schema / 迁移 / 构建配置改动；本票只删呈现节点与词条，不发新请求。

用户 2026-10-24 对 `/studio/` 的四条反馈：发送消息按钮要强调色、去掉「Agent」小标签、去掉
状态行、去掉头部关闭按钮。行为正本（`POST /api/dms`、`PATCH/DELETE /api/agents/{id}`、
`applyPresence()` 的其它落点、`closeAgentDetail()` 的删除成功路径）一律不动。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 「发送消息」是强调色按钮：复用既有 `.btn-primary` / `--accent` / `--on-accent`，零新色值、零内联样式 | `index.html` 的 `#btnAgentSendMessage` class 改为 `btn btn-sm btn-primary` | `node --test app/tests/agent_detail_ui.test.cjs`：`详情头部只剩强调色的「发送消息」…` 通过（`btn-primary` 断言）；浏览器读数 `btnBg === accent`、`btnFg === onAccent` |
| 2 | 行为不变：禁用判据（请求在途 / 离线）、`agentDetail.sendMessage` 文案、点击发 `POST /api/dms` 并离开详情 | `syncAgentSendButton()` / `messageActiveAgent()` 未改 | 同文件：`请求在途时「发送消息」禁用…`、`离线时「发送消息」禁用…`、`点「发送消息」发出 POST /api/dms…`、`新建路径…` 全部通过 |
| 3 | 「Agent」小标签整段删除；`agentDetail.tag` 两份词条一起删；`.dock-tag` 的其它用法与 CSS 不动 | `index.html` / `i18n.js` | 同文件：`详情头部只剩强调色…`（`agent-detail-tag` / `dock-tag` 不在头部）；`本票新增 / 沿用的文案…`（两份 `agentDetail.tag` 为 `undefined`） |
| 4 | 状态行整段删除（`#adPresence` / `#adStatusText`），`renderAgentDetail()` 不再写状态；`applyPresence` / `presenceNode` / `statusLabel` 其它调用点保留 | `index.html` / `app.js` 的 `renderAgentDetail()` | 同文件：`详情头部只剩强调色…`（正本无 `.dock-role` / `adPresence` / `adStatusText`）、`打开详情后不再查询已删的状态行节点…`（运行期 `$()` 零命中） |
| 5 | 头部关闭按钮（连 svg）、监听、「唯一出口」注释、两份 `agentDetail.closeAria` 一起删除；`closeAgentDetail()` 保留给删除成功路径 | `index.html` / `app.js` 的 `bindEvents()` / `i18n.js` | 同文件：`详情头部只剩强调色…`（头部只剩 1 个 button）；`本票新增 / 沿用的文案…`（`closeAria` 为 `undefined`） |
| 6 | 真实出口仍能离开详情：左栏频道 / 私信行（`selectTarget()`）、键盘 `M` / `T`；离开走 `leaveAgentView()`，**不**恢复 dock 开合 | `selectTarget()` / `showMessagesView()` / `showTaskBoard()` / `leaveAgentView()` 未改 | 同文件：`左栏频道行是另一个真实出口…`、`键盘 M 离开详情…`、`进入前 dock 是收起的…`、`无频道时用键盘 M 离开详情…` 通过 |
| 7 | dock 进入前开合只在删除成功的 `closeAgentDetail()` 路径恢复 | `closeAgentDetail()` 未改；`confirmAgentDelete()` 成功路径仍调用 | 同文件：`确认删除只发一次 DELETE…` 断言 `dock.hidden === false`、`dockAgent.hidden === false` |
| 8 | 暂停 / 恢复仍可观察（不依赖已删节点）：PATCH body `enabled === false` + 危险区按钮文案「恢复」+ 本地镜像 | 空证据：状态行删除后，原断言改到这两处 | 同文件：`暂停 / 恢复是 enabled 的唯一界面入口…` 通过 |
| 9 | 文案切换不破：zh-CN / en 头部都只有名称 + 发送消息，无裸中文 | `i18n.js` 的 `agentDetail.sendMessage` | 浏览器冒烟（下节）：zh `冒烟助手 发送消息`、en `冒烟助手 Send message`；`headHasChineseCopy === false` |
| 10 | 文档与实现同步：README 的详情段落按现状改写 | `app/README.md` | 人工核对：离开详情 = 左栏行 / 键盘 `M` / `T`；页签随频道头让位；dock 开合只在删除成功恢复 |
| 11 | 门禁全绿 | 见下节 | `scripts/check.sh --studio`：`门禁全绿` |

## 红证据（隔离基线）

```sh
scripts/red-evidence.sh 2497bcc26a72580569c48856b5da6c13d54c0634 app/tests/agent_detail_ui.test.cjs
```

| 测试文件 | 基线 app.js md5 | 证据目录 | 读数 |
| --- | --- | --- | --- |
| `app/tests/agent_detail_ui.test.cjs` | `e041cd74d3c7f7509e2cf23b900134e9` | `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.2JFX1h` | `tests 50 / pass 47 / fail 3` |

三条红读数对应的新事实（旧代码上红、新代码上绿）：

1. `本票新增 / 沿用的文案在两份词条里都取到值` —— 基线仍有两份 `agentDetail.tag`（`actual: 'Agent'`）。
2. `详情头部只剩强调色的「发送消息」…` —— 基线头部按钮是 `btn btn-sm`（无 `btn-primary`）。
3. `打开详情后不再查询已删的状态行节点…` —— 基线 `renderAgentDetail()` 查询 `#adPresence` /
   `#adStatusText` 各 3 次。

改写的「离开详情」用例在基线上也绿：它们验证的是既有出口（左栏行 / 键盘 `M`）与
「`leaveAgentView()` 不恢复 dock」这条既有事实——本票只删掉了头部入口，没有改这些路径。

## 门禁读数

```sh
scripts/check.sh --studio
```

- pytest（Studio 窄档：审批 HTTP 契约 + 文档词表守卫 + 测试分层守卫）：`20 passed in 2.15s`
- `ruff check`：`All checks passed!`
- `ruff format --check`：通过（未运行 `ruff format`，无整文件重排）
- `mypy`（仓库根）：`Success: no issues found in 124 source files`
- `pyright`：`0 errors, 0 warnings, 0 informations`
- `node --test app/tests/*.test.cjs`：`tests 308 / pass 308 / fail 0`
- 收尾：`门禁全绿`

`agent_detail_ui.test.cjs` 单文件：`tests 50 / pass 50 / fail 0`（旧 49 条 + 头部新事实拆分出的 2 条 −
合并的 1 条，见下节说明）。

## 测试改写说明（不许删用例变绿）

- 头部断言拆成两条：静态正本（只剩 `#btnAgentSendMessage` 且带 `btn-primary`；`#btnAgentDetailClose` /
  `#agent-detail-tag` / `.dock-role` / `#adPresence` / `#adStatusText` 都不存在）+ 运行期
  `$()` 不再查询已删节点。
- `BUTTON_IDS` 去掉关闭按钮、harness 的 `closeDetail()` 辅助删除；依赖它的三条用例改走仍在的
  真实出口：左栏频道行（`selectTarget()`）与键盘 `M`（`showMessagesView()`）。「关闭详情恢复进入前
  视图 / dock」改写为「离开详情不恢复 dock 开合」，恢复断言落到删除成功用例。
- 暂停保存用例不再读已删节点，改断言 PATCH body、危险区按钮文案与本地镜像。
- 词条键清单去掉 `agentDetail.tag` / `agentDetail.closeAria`，两条并入既有「已删词条」数组。

## code-review（Standards / Spec 双轴）

按本机 `code-review` 技能对 `git diff 2497bcc` 跑两个并行子代理，findings 与处置：

| 轴 | 级别 | Finding | 处置 |
| --- | --- | --- | --- |
| Standards | P2 | `app/README.md` 与测试注释把「消息 / 任务页签」称为详情里的出口，但 `#tabMessages` / `#tabTasks` 在 `#chanHead` 内，`syncChromeVisibility()` 在 `mode === 'agent'` 时把频道头整个隐藏，真实浏览器里页签不可点 | 已改：README / `setViewMode()` 注释 / 测试文件头改为「左栏频道 / 私信行，或键盘 `M` / `T`」；测试改用键盘 `M`；浏览器冒烟实测 `chanHead.hidden === true`、`tabMessages` 无可见矩形；页签隐藏属既有形态（用户接受头部无关闭控件），不在本票扩范围 |
| Spec | P3 | 同一条（README / 测试注释与实现不符，页签不是可达出口） | 同上 |
| Spec | P3 | 新增的「已删头部词条」断言是第四段同形循环（Duplicated Code） | 已改：两条键并入既有「已删词条」数组，只留一个循环 |

其它：两轴均未发现 scope creep；`closeAgentDetail()`、`applyPresence` / `presenceNode` /
`statusLabel` 的其它调用点、dock 其它形态、发送消息语义均未改。

### 已知遗留（本票不处理）

- `app.css:463` 的 `.agent-page-head .dock-role` 选择器随状态行删除后不再命中任何节点；票面
  约束「不动 `app.css` 的既有类」，故保留。`.dock-role` 本身仍被 `#dockRole` / 任务线程 / 运行面板使用。

## 真实浏览器冒烟（隔离端口 + 隔离数据目录）

- URL：`http://127.0.0.1:50144/studio/`
- 启动：`cd app && ../.venv/bin/python -m alibot --port 50144 --data-dir /tmp/alibot-head-smoke-20261024`
- 数据目录：`/tmp/alibot-head-smoke-20261024`（隔离；未使用 `8788` 与真实数据）
- 造数：`POST /api/agents`（`冒烟助手` / `mem_d27205d92d85`）+ `POST /api/channels`（`冒烟频道` /
  `ch_bfba94ef0659`）
- 驱动：ego-browser（真实 Chromium，task space 6）
- 截图：`/tmp/alibot-head-zh.png`、`/tmp/alibot-head-en.png`

| 步骤 | 观察 |
| --- | --- |
| zh-CN：点左栏 Agent 行 | `#viewAgent` 可见；头部 `button` 数 `1`，文本 `冒烟助手 发送消息`；`btnPrimary === true`；`btnBg === accent (oklch(0.857 0.086 356.8))`、`btnFg === on-accent (oklch(0.33 0.045 300))`；`sendEnabled === true`；`#btnAgentDetailClose` / `#agent-detail-tag` / `[data-i18n="agentDetail.tag"]` / `.dock-role` / `#adPresence` / `#adStatusText` 全部不存在；页面 5 个区块 |
| 切 English（设置 → 语言 → 完成） | 头部 `冒烟助手 Send message`；`btnPrimary === true`；背景仍是 accent；`brandSub === 'LOCAL · this machine'`；`headHasChineseCopy === false`；同一组已删节点仍不存在 |
| 详情内读可见性 | `chanHead.hidden === true`、`#tabMessages` 的 `getClientRects().length === 0`（页签不是详情里的可达出口） |
| 键盘 `M` | `#viewAgent.hidden === true`、`#chanHead` 可见、`#viewMessages` 可见（离开详情落回消息视图） |
| 点左栏频道行 | `#viewAgent.hidden === true`、`#chanHead` / `#composer` 可见；dock 保持收起（不凭空顶出成员面板） |
| 收尾 | `task.finish` + 杀掉该端口进程；未触碰 8788 |

## PR 与 CI

- PR：#258 之后的 `studio-agent-detail-head` 分支（PR 创建后回填号与 body 路径）
- CI 读数：待 PR CI 完成后回填（本地已跑 `scripts/check.sh --studio`；CI 跑同一脚本的全量）
