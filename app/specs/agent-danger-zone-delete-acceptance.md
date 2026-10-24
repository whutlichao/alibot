# Agent 详情「危险操作」区块与 Agent 软删除：验收记录

## 来源

用户反馈正题（2026-10-24）：Agent 详情里「暂停 / 恢复」与常规配置混在同一区块，删除也没有入口。
本票把暂停 / 恢复搬进页面最后一个「危险操作」区块，新增两步确认的删除，并把删除做成**完整软删除**：

- 页面顺序：统计 → 系统提示 → 配置 → 全局 MCP → 危险操作；
- 危险区：危险说明 + 暂停 / 恢复 + 删除；暂停的已知代价说明保留；
- 删除：第一次点击只展开确认区，第二次「确认删除」才发 `DELETE /api/agents/{id}`；
- 后端：`member.deleted_at` + 清空该成员的 `target_member` 行；消息 / 任务 / DM / `round_outcome`
  一律保留；二次删除与 owner / 未知 id 都是可读 404；
- 任务指派者已不在成员表时显示「已删除的成员」，不露裸 `mem_...` id。

基线：`97184ffec45331c9cecf826ec99efafe2bad3218`（本票 worktree 的切出点）。

**follow-up（2026-10-24 用户定夺）**：在 PR #255 分支上追加三处修正（配置区去掉「启用状态」档位、
`taskMemberName()` 改用 `workspaceLoaded`、危险文案不再说「从工作台隐藏」），只动 Studio 表现层、
其 DOM 用例与文档；详见文末「follow-up 修正」。

## 改动清单

| 层 | 文件 | 改动 |
| --- | --- | --- |
| 前端 | `app/alibot/studio/app.js` | `agentConfigSection()` 不再渲染暂停按钮与 `pauseNote`；新增 `agentDangerSection()` 并排在 `globalMcpSection()` 之后；两步确认删除、成功 / 失败路径、离线与请求中禁用；`taskMemberName()` 的已删除回退。**follow-up**：配置区移除 `agent-config-enabled` 档位组与它的注释；`taskMemberName()` 判据改用 `workspaceLoaded` |
| 前端 | `app/alibot/studio/i18n.js` | 中英双语新增 `agentDetail.sectionDanger` / `dangerHint` / `delete` / `deleteConfirm` / `deleteConfirmAction` / `deleteCancel`、`toast.agentDeleted` / `toast.agentDeleteFailed`、`task.deletedAssignee`。**follow-up**：`dangerHint` / `deleteConfirm` 改成「从 Agent 列表与全部频道中移除；私信、历史消息与任务保留」；移除已无消费点的 `agent.enabledLabel` / `enabledOn` / `enabledOnHint` / `enabledOff` / `enabledOffHint` |
| 前端 | `app/alibot/studio/app.css` | 危险区 / 危险按钮 / 确认区样式，只消费 `--error` / `--warn` 族与 `--border`；按钮可收缩 + 换行 |
| 后端 | `app/alibot/db.py` | `get_agent()` 增加 `deleted_at IS NULL` 过滤；新增 `delete_agent()`（写 `deleted_at` + 删 `target_member`，单事务单锁） |
| 后端 | `app/alibot/service.py` | 新增 `delete_agent()`（人类面动作，配置 / 身份类写路径） |
| 后端 | `app/alibot/api.py` | 新增 `DELETE /api/agents/{id}`；原 `PUT` / `DELETE` 兜底收窄为 `PUT`，改名为 `no_agent_replacement` |
| 后端文案 | `app/alibot/i18n.py` | `error.agent_delete_unsupported` → `error.agent_replace_unsupported`（删除已存在，原句不再成立） |
| 测试 | `app/tests/agent_detail_ui.test.cjs` | 区块顺序 / 危险区内容 / 两步确认 / 成功 / 失败 / 离线 / 英文 / 已删除指派回退。**follow-up**：配置区无「启用状态」档位、暂停按钮是唯一入口、`taskMemberName()` 两态（含一次失败首载 + 重连成功）、危险文案口径 |
| 测试 | `app/tests/test_agent_detail.py` | 软删除契约、二次 404、频道成员、状态点、历史与任务读数不变、owner / 未知 id / PUT 404 |
| 测试 | `app/tests/test_create_agent.py` / `app/tests/test_channel_join.py` | 既有「DELETE 明确 404」断言与注释同步到新契约（最小改动；前者改删一个独立成员） |
| 文档 | `CONTEXT.md` / `app/README.md` | 成员词条与端点表 / 详情页段落同步。**follow-up**：README 的「配置区块」表述改成 `enabled` 只经危险区改，`taskMemberName()` 判据与危险文案同步 |

## 红证据（先写测试，后写实现）

### DOM：隔离基线副本

```sh
scripts/red-evidence.sh 97184ffec45331c9cecf826ec99efafe2bad3218 app/tests/agent_detail_ui.test.cjs
# exit=1
# 基线：97184ffec45331c9cecf826ec99efafe2bad3218 (97184ff)
# 测试：app/tests/agent_detail_ui.test.cjs（取自当前工作区）
# 基线 app.js md5：135cc1fe0b0bab1da16b2fbfb079df22
# 证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.bw9TG1
# ℹ tests 38
# ℹ pass 28
# ℹ fail 10
```

失败用例（全部是产品代码尚未实现的行为）：

```text
✖ 所有区块在同一页纵向铺开：统计 / 系统提示 / 配置 / 全局 MCP / 危险操作
✖ 暂停 / 恢复与启用状态同源：按钮只改草稿，保存时提交 enabled
✖ 删除是两步：第一次点删除只展开确认区，不发请求；取消后确认区收起且仍无请求
✖ 确认删除只发一次 DELETE：成功后详情关闭、列表刷新、toast 可读
✖ 删除请求在途时按钮禁用：请求没回来之前不能重复提交
✖ 删除失败留在当前页、本地镜像不变，错误原因不静默吞掉
✖ 离线时暂停与删除都禁用
✖ 危险区文案有英文取值：切到 English 后同样可见
✖ 任务指派者已不在成员表里：显示「已删除的成员」，不把裸 id 给用户
✖ 本票新增 / 沿用的文案在两份词条里都取到值
```

### API：旧代码直接跑新用例

```sh
cd app && ../.venv/bin/python -m pytest tests/test_agent_detail.py -k delete
# exit=1
# F. [100%]
# FAILED tests/test_agent_detail.py::test_delete_soft_deletes_the_agent_and_keeps_history
#   assert response.status_code == 200
#   E       assert 404 == 200      ← 旧代码没有 DELETE 路由，落到 404 兜底
# 1 failed, 1 passed, 15 deselected in 0.32s
```

`test_delete_rejects_owner_and_unknown_ids_and_put_stays_unsupported` 在旧代码上绿（owner / 未知 id /
PUT 本来就是 404）；它保留作删除路由加入后的回归守卫。

## 目标用例

```sh
node --test app/tests/agent_detail_ui.test.cjs
# ℹ tests 38
# ℹ pass 38
# ℹ fail 0

cd app && ../.venv/bin/python -m pytest tests/test_agent_detail.py
# 17 passed in 1.02s
```

DOM 断言覆盖：

1. 危险区是页面最后一个区块（在全局 MCP 之后）；暂停按钮在危险区内、配置区块里不再有它；
2. 危险说明可见且包含「危险」语义；暂停的已知代价说明仍在危险区；
3. 第一次点删除零请求、确认区出现；取消后确认区收起且仍零请求；
4. 确认删除只发一次 `DELETE /api/agents/<id>`；成功后详情关闭、左栏与频道成员面板刷新、toast 含 Agent 名称；
5. 删除请求在途时删除 / 确认按钮禁用（受控 promise：请求没回来之前不能重复提交）；
6. 失败留在当前页、本地镜像不变、后端原因可见；离线时暂停与删除禁用；
7. 英文词条同样渲染（`Danger zone` / `Delete`）；已删除指派者显示「已删除的成员」，成员表**尚未成功
   载入**时用中性「成员」占位（判据是 `workspaceLoaded`，不是 `state.ready`；见 follow-up 修正 ②）；
8. `i18n_ui.test.cjs` 的两份键集 / 裸中文口径照跑（53 passed）。

### 真机视口读数（ego-browser 0.6.0.16 + 真实 Studio）

```sh
# 临时数据目录 + 真实后端；一个 Agent 经 HTTP 面创建
cd app && ../.venv/bin/python -m alibot --data-dir /tmp/alibot-danger-visual --port 8787
curl --noproxy '*' -X POST http://127.0.0.1:8787/api/agents \
  -H 'Content-Type: application/json' \
  -d '{"name":"小明","role":"前端工程师","model_id":"ollama:qwen2.5","system_prompt":"你只回答一句话。"}'
# ego-browser：打开 /studio/、点 Agent 行、展开删除确认区，再用
# Emulation.setDeviceMetricsOverride 依次量 1350 / 900 / 420，取 danger 区文本与按钮的
# getBoundingClientRect，检查左右越界与按钮两两相交。
```

| viewport | `document.scrollingElement.scrollWidth` | danger 区宽 | 越界元素 | 按钮相交 |
| --- | --- | --- | --- | --- |
| 1350 | 1350 | 880 | 0 | 0 |
| 900 | 900 | 845 | 0 | 0 |
| 420 | 420 | 365 | 0 | 0 |

三档下确认区都展开（`confirmVisible: true`）；1350 / 900 / 420 的页面滚动宽度都等于视口宽度，
说明危险说明、四个按钮与确认文案没有横向溢出、互不遮挡。截图（临时）：
`/tmp/alibot-danger-visual-{1350,900,420}.png`。

API 断言覆盖：

1. 删除返回 200，`deleted` 是真实 id，`agents` / `members` 不再含它；
2. `GET` 404、二次 `DELETE` 404、owner 404、未知 id 404、`PUT` 仍 404；
3. 频道成员表不再含它；`member-statuses` 不再出现它；
4. 删除前后 `messages` / `round_outcome` / `budget` 快照逐字相等；
5. 任务不被改写：`task.assignee_member_id` 与任务线程消息删除前后逐字相等；
6. 未删除 Agent 的 `PATCH` / 暂停（`enabled=false`）继续可用（既有用例 + 本票守卫）。

## 门禁读数

### 开发循环

```sh
scripts/check.sh --fast tests/test_agent_detail.py
# exit=0
# pytest: 17 passed in 0.98s
# ruff check: All checks passed!
# ruff format --check: 315 files already formatted
# mypy: Success: no issues found in 124 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: 291 pass / 0 fail
# 门禁全绿
```

### 全量

```sh
scripts/check.sh
# exit=0
# pytest: 1173 passed, 11 skipped in 636.02s (0:10:36)
# ruff check: All checks passed!
# ruff format --check: 315 files already formatted
# mypy: Success: no issues found in 124 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: 291 pass / 0 fail
# 门禁全绿
```

## 双轴 code-review

基线 `97184ff`，两个子代理并行审查（Standards / Spec），结论与处理如下。

### Standards 轴（2 hard / 7 judgement）

| # | 发现 | 处理 |
| --- | --- | --- |
| H1 | `CONTEXT.md` **加入频道** 的 `_Avoid_` 仍写「移出频道（本形态不做，人类面没有该端点）」，与新的删除语义读起来矛盾 | **已修**：改成「没有**单频道**移出入口；删除 Agent 会把它移出全部频道（见**成员**）」 |
| H2 | README 小节标题把新增的软删除决定挂到 ADR-0011 名下，而 ADR-0011 没有决定删除 | **已修**：标题去掉 ADR 归属；正文把 PATCH 的取舍指向 ADR-0011，删除的取舍指向本验收文档（不就地改写 ADR） |
| J3 | 同一条软删除政策在 CONTEXT / README / 三层 docstring / 验收文档各写一遍 | **不修**：本仓把文档当**数据契约的缓存**（`docs/agents/implementation.md`）、docstring 按层写职责与理由；不新增共享常量或生成文档 |
| J4 | `service.delete_agent` 是薄委托；`deleted` 字段名像布尔 | **部分修**：库层返回 `str` id（去掉未消费的 `deleted_at`）；薄委托与 `delete_reminder` 同形。`deleted` 字段名是票面钉死的 HTTP 形状，不改 |
| J5 | `db` 返回未消费的 `deleted_at`；前端响应后又本地 filter 一次 | **已修** 前者；后者保留为「无论响应形状都保证本地移除」的防御（票面明确要求移除本地镜像） |
| J6 | 禁用规则在初始渲染 / 就地重绘 / 点击守卫出现三次 | **不修**：三处生命周期不同（建节点、草稿脏时改既有节点、事件守卫），抽一个 helper 只会多一层间接，判定仍只有 `!online \|\| busy` 一条 |
| J7 | `taskMemberName()` 对查不到的 id 一律说「已删除」 | **已修**：只有 `workspaceLoaded`（工作台**成功**载入过一轮，见 follow-up 修正 ②）才解释成「已删除」，否则用中性「成员」；补了断言 |
| J8/J9 | 验收文件名未按 `issue-<n>`；PENDING 读数 | **已修** PENDING（本文件已回填）；文件名沿用既有先例（`task-auto-continue-acceptance.md` 等），本票没有 GitHub issue 号 |

### Spec 轴（2 missing/partial / 1 scope-creep / 1 wrong）

| # | 发现 | 处理 |
| --- | --- | --- |
| M1 | 配置区仍有「启用状态」档位，写的是同一个 `draft.enabled`，用户仍能在配置区暂停 | **已修（follow-up 修正 ①）**：用户 2026-10-24 定夺移除配置区档位，危险区的暂停 / 恢复按钮成为 `enabled` 的唯一界面入口；`draft.enabled` 的初始化、保存与 `PATCH` 语义不变 |
| M2 | 1350px / 窄视口与「请求在途禁用」缺少证据 | **已修**：新增受控 promise 的在途禁用 DOM 用例；用 ego-browser 在真实 Studio 量 1350 / 900 / 420 三档（读数见上），越界与按钮相交均为 0 |
| S3 | 把 `error.agent_delete_unsupported` 改名为 `error.agent_replace_unsupported`，并把 PUT 404 文案改成「只能部分更新」 | **保留**：新增删除后，旧文案「Agent 不可删除」已变成假话；这个兜底只服务 PUT，键名与文案必须一起改。`PUT` 的状态码与行为仍是 404，未改契约 |
| W4 | `taskMemberName()` 把「成员表未就绪」误判成「已删除」 | **已修**：判据改成 `workspaceLoaded`（失败分支不会置位，`state.ready` 会），未成功载入时回退中性「成员」；DOM 用例覆盖两态（见 follow-up 修正 ②） |

## 遗留与边界

- 删除不回收正在进行的轮次 / 容器：本票只覆盖「软删除 + 移出成员面」；编排层没有为删除开新接缝。
- 「暂停」只有**一条**界面入口：危险区的暂停 / 恢复按钮（follow-up 修正 ① 删掉了配置区的
  「启用状态」档位）。点击仍只写草稿里的 `enabled`，保存条统一提交；`enabled` 的配置面读写语义未变。
- 已删除成员的历史消息仍按 `author.deleted` 渲染（既有机制）；任务历史保留原
  `assignee_member_id`，界面显示「已删除的成员」。DM target 与私信保留，不级联删除。
- `taskMemberName()` 的判据是 `workspaceLoaded` 的**既有语义**（整轮工作台读数成功后才置 true），
  因此「成员表已到、后续某个读数失败」的那段窗口里，已删除的指派者会暂时显示中性「成员」。这是
  保守方向（不把仍然存在的人误标成已删除），且改这个置位点会连带改首次贴底滚动，留给独立票。
- 没有恢复入口：`deleted_at` 只写不回填，恢复是一张独立票。

## follow-up 修正（2026-10-24 用户定夺，PR #255 续）

上一轮合入前用户对三处判据再定夺。本轮只改 Studio 表现层（`app.js` / `i18n.js`）、它的 DOM 用例与
文档，不动 `.py`、schema 与 HTTP 契约；全量门禁由 CI 在最终 head 上跑。

### ① 移除配置区的「启用状态」档位

`agentConfigSection()` 不再渲染 `agent-config-enabled` 那一组档位卡片（连同 field row 与注释）。
`draft.enabled` 的初始化（`agentDraftFrom()`）、保存路径（`submitAgentEdit()` 的 `enabled`）与后端
`PATCH` 语义**一字未改**。危险区的「暂停 / 恢复」按钮成为 `enabled` 的**唯一**界面入口：点击仍只写
草稿，保存条提交 `enabled`；危险说明与 `pauseNote` 仍覆盖「暂停会留下失败轮次结果」。

删除后 `agent.enabledLabel` / `enabledOn` / `enabledOnHint` / `enabledOff` / `enabledOffHint`
已无任何消费点（`grep -rn` 逐个确认：除词条表自身外，只剩 `agent_detail_ui.test.cjs` 的文案清单，
该清单引用的是同一批将被删除的键）：

```sh
grep -rn "enabledLabel\|enabledOn\|enabledOnHint\|enabledOff\|enabledOffHint\|agent-config-enabled" \
  app/alibot/studio/ app/tests/ | grep -v "mcp.enabledLabel"
# 只剩 i18n.js 的两份词条与测试里的键清单 → 全部移除
```

两份词条表与测试的键清单同步移除，并把它们加进「已删词条」断言（防孤儿键回潮）。

### ② `taskMemberName()` 的就绪判据改用 `workspaceLoaded`

同文件 `loadWorkspace()` 上方的注释本来就写明「不能用 `state.ready`：它在失败分支也会置 true」。
判据改为模块级 `workspaceLoaded`（`loadWorkspace()` 整轮读数都成功后才置 true）：

- 未成功载入 ⇒ `t('toast.memberFallback')`（中性「成员」，不把仍然存在的人误标成已删除）；
- 已成功载入但查不到 id ⇒ `t('task.deletedAssignee')`（「已删除的成员」）。

DOM 用例覆盖两态，且**走产品自己的路径**：harness 的 `/api/workspace` 首次返回 503（失败路径也会置
`state.ready = true`），断言此时渲染中性「成员」且工作台被标成离线；随后让重连定时器
（`scheduleReconnect()`）成功跑完一遍，再断言此时才渲染「已删除的成员」。这条同时覆盖了票面说的
「一次失败的首载」。

### ③ 危险说明不再声称「从工作台隐藏」

删除后 DM target 与私信历史保留（本票既有语义），因此中英 `agentDetail.dangerHint` 与
`agentDetail.deleteConfirm` 改成「把这个 Agent 从 **Agent 列表与全部频道**中移除；**私信、历史消息
与任务**保留」。不新增 DM 隐藏 / 标注逻辑（超出本 follow-up 范围），只保证文案不与实际行为冲突。

### 红证据

```sh
scripts/red-evidence.sh 657bc85 app/tests/agent_detail_ui.test.cjs
# exit=1
# 基线：657bc85 (657bc85)
# 基线 app.js md5：ff0a101ef2e2f05fbfefd76066e1486e
# ℹ tests 39
# ℹ pass 34
# ℹ fail 5
```

失败项（全部是本轮三处修正的产品行为）：

```text
✖ 暂停 / 恢复是 enabled 的唯一界面入口：配置区没有「启用状态」档位，按钮只改草稿
✖ 危险说明与确认文案说「移出列表与全部频道」，不再声称「从工作台隐藏」
✖ 任务指派者已不在成员表里：成员表成功载入过才说「已删除」
✖ 本票新增 / 沿用的文案在两份词条里都取到值                ← 已删词条仍在两份表里
✖ 配置区块的标签真的指向控件：单控件 label[for]、档位组 aria-labelledby  ← enabled 档位组还在
```

### 目标读数

```sh
node --test app/tests/agent_detail_ui.test.cjs
# ℹ tests 39 / pass 39 / fail 0

node --test app/tests/i18n_ui.test.cjs
# ℹ tests 53 / pass 53 / fail 0

scripts/check.sh --fast
# exit=0
# ruff check: All checks passed!
# ruff format --check: 315 files already formatted
# mypy: Success: no issues found in 124 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: 292 pass / 0 fail
# 门禁全绿
```

DOM 断言覆盖（本轮新增 / 改写）：

1. 配置区没有 `agent-config-enabled`、没有 `data-agent-tier="enabled"`、没有「启用状态」字段行；
   危险区按钮点击后零 `PATCH`、保存条出现，保存时才提交 `enabled: false`；
2. `taskMemberName()` 两态：首载失败时中性「成员」，重连成功后才「已删除的成员」；断言只看渲染文本
   与 `state.online`（不读模块内的 `workspaceLoaded`，也不再断言 `state.ready`）；两态都不外露裸
   `mem_...` id；
3. 危险说明与确认文案在中英两门语言里都含「Agent 列表 / 全部频道 / 私信 / 历史消息」，且不再出现
   「从工作台隐藏」/「hides it from the workspace」；
4. 无障碍用例同步：档位组只剩「自动认领」一组，并明确断言 enabled 档位组已不存在。

### 双轴复核（只审本轮 follow-up diff）

基线 `657bc85`，两个子代理并行审查（Standards / Spec），只审这三处修正与它们带出的测试 / 文档改动。

#### Spec 轴

| # | 发现 | 处理 |
| --- | --- | --- |
| S1 | 验收 6 只改了本地 PR body，GitHub #255 线上 body 仍是旧版 | **已修**：推送后 `gh pr edit --body-file app/specs/agent-danger-zone-delete-pr-body.md` |
| S2 | 本文件「双轴复核」「CI」两节还是占位 | **已修**：本小节与下面「CI」已回填 |
| S3 | `paintAgentDraftControls()` 把 `[data-agent-tier]` 一律按 `draft.auto_claim` 画；将来新增档位组会被绑死 | **已修**：改成按组名取草稿字段（`draft[group.dataset.agentTier]`），与点击时的 `setAgentDraft(group.dataset.agentTier, …)` 共用同一份知识 |
| S4 | README 里塞进了评审元话（「…同样是本票既有语义，不声称『从工作台隐藏』」） | **已修**：README 只留事实句；「不再声称 X」的判据留在用例里 |
| S5 | `workspaceLoaded` 在 `loadWorkspace()` **整轮**读数成功后才置位：成员表已到但 `refreshMessages` / `refreshStatuses` 抛错时仍为 false，已删指派者会显示中性「成员」 | **不修（记为边界）**：票面钉死用 `workspaceLoaded`（「首次成功载入后为 true 的模块级标志」），它的置位时机同时决定首次贴底滚动，改它等于改另一张票的行为。失效方向是**保守**——宁可把已删除的成员暂时叫「成员」，也不把仍然存在的人误标成已删除。见「遗留与边界」 |
| S6 | 两态用例里仍有 `assert.equal(state.ready, true)`（作为前提） | **已修**：删掉该断言，改用 `state.online === false` 作失败路径旁证；用例不再读 `state.ready` |

已核通过：三处修正都在；`agent.enabled*` 五条词条除测试清单 / 文档外零消费点；「从工作台隐藏」只
出现在断言与「不再声称」的说明里；`node --test` 两套 39/39、53/53 绿。

#### Standards 轴

hard：**无**。README 已同步两条行为（`review-rules` #6）；i18n 两份表同删同留、无孤儿键；删除文案与
`CONTEXT.md` 的**成员**词条一致。

| # | 发现 | 处理 |
| --- | --- | --- |
| T1 | `paintAgentDraftControls()` 硬编码 `draft.auto_claim`，而点击处按 `dataset.agentTier` 路由——同一字段知识两处（Repeated Switches 残留） | **已修**：见 Spec S3，两边共用「组名 = 草稿字段名」一份知识 |
| T2 | `workspaceLoaded` 实为「整轮载入成功」，注释读作「成员表载入过」，与 README 口径不一（Mysterious Name） | **已修**：注释改成「工作台（含成员表）**成功**载入过一轮」，与 README 同口径 |
| T3 | 测试为读模块级 `let` 新开 `isWorkspaceLoaded` 注入孔，并断言内部 `state.ready`（`review-rules` #2 / #3 判断题） | **已修**：删掉注入孔与 `state.ready` 断言；两态完全由 `loadWorkspace()` + 产品重连路径驱动，断言落在渲染文本与 `state.online` 上 |
| T4 | 危险文案用例用区块 `textContent`，把隐藏的确认区一起读进来，`dangerHint` 的断言可被 `deleteConfirm` 满足 | **已修**：按 `.danger-hint` 节点单独取，确认区按 `agent-delete-confirm` 单独取，中英两门都逐节点断言 |

### CI

（CI 读数回填见下）

## PR

- 编号：**#255** — https://github.com/whutlichao/alibot/pull/255
- body 正本：`app/specs/agent-danger-zone-delete-pr-body.md`（用 `gh pr create --body-file` 提交）
- 状态：已推送、CI 待跑；**未合并**（合并由 coordinator 做）。
