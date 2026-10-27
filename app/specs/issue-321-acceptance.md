# 票 #321 验收：频道消息流里的审批块（补回）

本票把旧页面 `studio/index.html:256-261` 的 `#approvalBoard` 补回 v2 的**消息流**：位置是空态之后、
`#messageList` 之前；它是消息流里的行动面，不是独立面板、不是 dock 形态、不新增 `mode` 档位。

**这一面不是新行为面，是补回旧页面既有的一块**：读数（`stores/approvals.js`）、呈现
（`ApprovalCard.vue` + `lib/approvals.js` 的倒计时 / 掩码 / 呈现态）都已存在，本票只加「消息流里的
这一处插入点 + 空态互斥」，因此门禁档位取**窄**（受影响 Vitest 用例 + 全量 Vitest glob），收尾仍跑
一次 `./scripts/check.sh` 全量。

产物不入库：`app/alibot/studio/v2/` 已 gitignore，`scripts/check.sh` 头部自动 build；本票未提交任何产物。

## 判据 → 落点

| 判据（票面） | 落点 | 判据来源 |
| --- | --- | --- |
| 审批块出现在消息流里，位置**空态之后、消息列表之前** | `MessageStream.vue` 模板：两个 `.empty` 之后、`#messageList` 之前插入 `#approvalBoard` | 旧 `index.html:256-261` |
| 排序：pending 在前、其余在后；组内按 `requested_at` 倒序 | 复用 `stores/approvals.js` 的 `recordsFor()` → `lib/approvals.js` 的 `sortApprovalRecords()`（不另算一遍） | 旧 `app.js:3150` `approvalRecordsFor()` |
| 无记录整块隐藏（不渲染空容器） | 模板 `v-if="hasTarget && approvalRecords.length"` | 旧 `board.hidden = !list.length` |
| 「流里只有审批卡」时不显示「该频道还没有消息」 | `#emptyNoMessage` 的 `v-else-if` 加上 `&& !approvalRecords.length` | 旧 `app.js:8542-8547` |
| 决策走 `POST /api/approvals/{reply_id}`、幂等 | `ApprovalCard` 的 `@submit` → `store.submit()`（在途闸门在请求层；未改后端） | 票面「后端已存在」 |
| 与任务线程不串台（同一份镜像、按 target 分开） | 两侧都读 `ctx.approvals`（同一 store 实例），各自按自己的 `target_id` 取记录 | `stores/approvals.js` 文件头 |
| 私信下不出现 | `App.vue` 的 `approvalStreamTarget`：私信下为空串 ⇒ `MessageStream` 不取记录 | 旧判据 `approvalTargetId = state.activeChannelId`（私信下为 `null`） |
| 至少一条用例从 `App.vue` 真实父组件进 | `tests/channel-approvals.test.js` §四（两条：频道页提交、切私信不出现） | `docs/agents/implementation.md` |
| 敏感字段不出现 | 沿用 `ApprovalCard` + `lib/approvals.js` 的掩码；实机读数见下（`api_token=••••`） | 既有遮码纪律 |
| `app.css` 零改动 | `.approval-board[hidden]` / `.approval-board` / `.approval-card` 早已在 `app.css:1345-1353` | — |

### 三个必须先定的问题（结论）

1. **`ApprovalCard.vue` 直接复用**，不改 props：它的入参是 `record` / `targetId` / `member` / `now` /
   `online` / `inFlight` / `error`，消息流侧同样全部具备（`member` 从成员表查一行，查不到退回 id）；
   与 `TaskThread.vue` 的用法逐字同形，因此**没有**包一层、也没有第二套倒计时。
2. **600s 上界判定已存在**：`lib/approvals.js` 的 `approvalDisplayState()` / `APPROVAL_TIMEOUT_SECONDS`
   （store 的 `recordsFor()` / `pendingCountFor()` 已经在用）。按票面「有就复用」处理，未新增判据。
3. **插入点**：v2 的消息流组件是 `app/frontend/src/components/MessageStream.vue`（旧
   `#viewMessages > #streamInner` 的直译），插入点是两个 `.empty` 与 `#messageList` 之间。

### 一处票面措辞与正本的差异（按正本实现，记录在此）

票面「验收」写「提交决策后消失」，但同票「驱动判据」写的是「pending 在前、**其余在后**」——
若终态记录不铺出来，「其余」就没有意义。旧页面正本（`renderChannelApprovals()` 3344）铺的是该
target 的**全部**记录，收口的那条作为历史留下、只是没有动作；v2 的任务线程审批段同理
（`tests/approvals.test.js` 已钉住「卡片留着**只是没了动作**」）。因此本票按正本实现：**决策后卡片进入
终态（已批准 / 已拒绝）、两个动作按钮与倒计时退场；下次读数（重新载入页面）不再取该记录，整块消失**。
两条路径都在下面的实机读数里。

## 红证据（实现前）

在**隔离的基线副本**上留读数：`scripts/red-evidence.sh` 只覆盖 `app/` 下的旧式 Node 用例族，对
Vitest 用例族不适用（`git archive` 的是 `app/alibot/studio` 与 `app/tests`，不含 `app/frontend`）。
本票的红证据按同一思路手工取：用例文件属于**新增文件**，基线（`9d97bf0`）里既没有 `#approvalBoard`
也没有 `approval-target-id` 这套接线，因此在改动源码之前先跑一次新用例即可——红只可能来自缺功能。

```sh
cd app/frontend && env -u NODE_ENV npx vitest run tests/channel-approvals.test.js
# 实现前：Tests 7 failed | 4 passed (11)
#   × 有记录 ⇒ 消息流里出现审批块…（#approvalBoard 不存在）
#   × 只有审批卡、没有消息 ⇒ 不显示「该频道还没有消息」空态
#   × 有消息也有审批卡 ⇒ 两者并存
#   × pending 在前、其余在后…
#   × 只铺本 target 的记录…
#   × 私信下不出现…
#   × 从 App.vue 进：频道页出现审批卡…
# 实现后：Test Files 1 passed (1) / Tests 11 passed (11)
```

## 绿读数

```sh
cd app/frontend && env -u NODE_ENV npx vitest run        # Test Files 24 passed (24) / Tests 448 passed (448)
cd app/frontend && env -u NODE_ENV npm run build          # ✓ 65 modules transformed（产物不入库）
./scripts/check.sh                                        # 全量，读数见下表
```

| 门禁 | 读数 |
| --- | --- |
| pytest（全量） | `1271 passed, 11 skipped in 831.57s (0:13:51)` |
| ruff check | `All checks passed!` |
| ruff format --check | `369 files already formatted` |
| mypy | `Success: no issues found in 134 source files` |
| pyright | `0 errors, 0 warnings, 0 informations`（逐字引用工具输出，故保留其 `informations` 拼法） |
| Vitest（v2 工作台） | `Test Files 24 passed (24)` / `Tests 448 passed (448)` |

全量门禁末行 `门禁全绿`，`EXIT=0`。

## 实机读数（ego-browser，独立数据目录 `/tmp/alibot-321` + 端口 8931）

装置：`python -m alibot --data-dir /tmp/alibot-321 --port 8931`；频道 `#design`（`ch_34161eafe31f`）、
频道 `#ops`（空）、Agent `Ada`、私信 `dm_89ba3aae5ca4`；审批记录用 `Database.create_approval_request()`
直接落库（工具调用 `write_file` 与 `run_shell`，后者参数里带 `api_token` 用来验掩码）。

| 路径 | 读数 |
| --- | --- |
| 有待审批 | `#streamInner` 直接子节点 = `["approvalBoard","messageList"]`（board 下标 0 < messageList 1）；`role="region"`、`aria-label="审批"`、`data-od-id="approval-board"`；标题「审批」；两张 pending 卡（`write_file` / `run_shell`），剩余「09:14」，成员名 Ada；`#emptyNoMessage` 不存在 |
| 掩码 | `run_shell` 的参数摘要渲染为 `cmd=ls · api_token=••••` |
| 排序 | 决策 `rpl_demo_1` 后：`["rpl_demo_0_old:pending", "rpl_demo_1:approved"]`（pending 在前，终态在后） |
| 提交决策 | 点「允许」⇒ `data-state="approved"`、状态字「已批准」、剩余文案为空、`[data-approval-action]` 数量 0、无错误行；点「拒绝」⇒「已拒绝」同上 |
| 刷新后 | 重新载入 `#design`（pending 已全部收口）⇒ `{"board": false, "cards": 0}`，`#emptyNoMessage` 出现 |
| 无任何记录 | `#ops` ⇒ `{"board": false, "emptyNoMessage": true}`，`#streamInner` 子节点 = `["emptyNoMessage","messageList"]` |
| 私信（该 target 上**确实有一条 pending 审批**） | `@Ada` ⇒ `{"board": false, "cards": 0}` |

截图（`.scratch/` 之外，未入库）：`/tmp/ego-321/board.png`、`design-after-deny.png`、`ops-empty.png`、
`dm.png`、`design-reload.png`（临时目录随实机验证一起清理）。ego-browser 结束时调用了
`page.close()` 与 `task.finish({ keep: [] })`。

## 双轴 code-review 为何省略

影响面可圈定在**消息流一处新增区块**（`MessageStream.vue` 模板 + 三个 props + 三个小函数）、
`App.vue` 的一处 computed 与三行 prop 接线、外加一个新用例文件；`ApprovalCard.vue` /
`stores/approvals.js` / `lib/approvals.js` / 后端 / 构建配置 / `app.css` **零改动**。按票面
「若改动超出上述范围立刻 ask 升级评审」——没有超出，因此省略双轴，改为按
`docs/agents/review-rules.md` 八条判断题逐条给结论：

1. **共享行为不为单票需求而变**：未改任何共享层。排序 / 呈现态 / 掩码全部复用既有实现；新代码只做
   「取本 target 的记录 + 摆到消息流里」，没有新增全局判据。
2. **不新开接缝**：沿用既有 Vitest + `@vue/test-utils` + `createFetchStub` 装置，没有新增 stub 种类；
   新用例文件放在既有 glob 里，未新增测试清单。
3. **不越层调内部函数**：用例只从 `MessageStream` / `App.vue` 进（真实父组件），断言落在渲染元素、
   属性和 `fetch` 请求上；`seed()` 用的是 store 的公开写入口（`applyFrame` / `patchRecord`），
   与 live 帧同一条路径。
4. **ADR 只做最小事实修正**：未改任何 ADR。
5. **失效方向可放松的判据**：本票的「宁可少出、不可误出」判据是作用域——私信下给空串 ⇒ 整块不出现
   （漏判代价是没有审批卡，误判代价是在私信里露出频道审批，后者更重）。`approvalTargetId` 为空时
   取不到记录，路径上不存在「猜一个 target」的分支。
6. **文档与数据契约同步**：未新增状态 / kind / 端点，`app/README.md` 与 `CONTEXT.md` 无对应条目需要改
   （审批块不是消息、不占 `seq`、不进搜索——`CONTEXT.md` 的「消息」定义不变）；本文件是唯一的验收记录。
7. **验收证据可复跑**：上表每条判据都给了落点与命令/读数；实机部分给了端口、数据目录形态与
   `create_approval_request()` 的构造方式，任何人可重放。
8. **AgentScope 用法**：本票不碰 `agentscope`（纯前端呈现），未新增任何 import 或签名。

**Door：双向**——纯前端补面，端点已存在、无 schema 变更、无移除功能；回滚即删这一块模板与三个 props。

**Blast Radius：小**——消息流多一块有序区块（`.approval-board` 已有样式，`app.css` 零改动）；
`#emptyNoMessage` 的判据多了一个与条件（只在有审批记录时影响）。已知代价：终态审批卡在本次会话内
继续占位（与旧页面、任务线程一致），刷新后整块消失。
