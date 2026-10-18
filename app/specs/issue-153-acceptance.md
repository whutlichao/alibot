# Issue #153 验收 —— 审批 UI：审批卡、倒计时、resolved

> 票面：GitHub [#153](https://github.com/whutlichao/alibot/issues/153)（impl，G-impl / G-ui）。
> 规格：`app/specs/agentscope-2-alignment.md` §4.5（SSE 词表）/ §4.6 第 4 条（前端新视图）。
> 裁定：ADR-0021 §四（权限与 HITL）；ADR-0020（事件只直播、快照兜底）。
> 依赖：#149 的审批 HTTP / SSE / `approval_request` 已在基线。
> 分支：`issue-153-impl`；worktree `/tmp/alibot-wt/issue-153-impl`；基线 `b594859`（集成分支 tip）。

| 项 | 值 |
| --- | --- |
| 后端改动 | **零**（`GET /api/approvals?target_id=&state=pending`、`POST /api/approvals/{reply_id}`、live `approval_required` / `approval_resolved` 均已在基线） |
| 前端落点 | `app/alibot/studio/{index.html,app.js,app.css,i18n.js}` |
| 新增用例 | `app/tests/approval_ui.test.cjs`（7 条 DOM 用例） |
| 浏览器证据 | `app/specs/issue-153-evidence/*.png`（真实 Chromium / ego-browser，三档视口 × 频道 / 任务线程 + resolved） |
| 文档 | `app/README.md` 补一句：审批卡以 `GET /api/approvals?state=pending` 兜底（与运行面板分开） |

---

## 0. 交付概览

审批卡出现在两个面：**频道消息流**（`#approvalBoard`，在 `#messageList` 之上）与**任务线程**
（`#ttApprovalSection` / `#ttApprovals`）。两处共用同一张卡：

- 工具调用：只渲染后端已收窄的 `{id, name, input}`；工具名 + 参数摘要；
- 请求成员：成员显示名 + 头像色位（找不到成员时退回 member id）；
- 状态：`pending` / `approved` / `denied` / `expired`；
- 倒计时：`requested_at + 600s`（`DEFAULT_APPROVAL_TIMEOUT_SECONDS`），每秒只改文本，不重建卡片；
- 动作：允许 / 拒绝 → `POST /api/approvals/{reply_id}`，一次提交该卡全部 `tool_call`；
  提交中按钮 disabled 且只发一次请求；resolved / expired 后动作消失；
- 敏感键：`password` / `secret` / `token` / `api_key` / `authorization` / `cookie` /
  `credential` / `private_key` / `access_key` / `session_id` 递归掩码为 `••••`；
  调用对象上多出来的字段（例如 `credentials`）不读、不渲染。

**恢复与实时**：

- 打开频道（`selectTarget` / 工作区恢复）与任务线程（`openTask`）都读
  `GET /api/approvals?target_id=<target>&state=pending`；
- SSE 重连（`EventSource.onopen`）对当前 target 再读一次 pending 快照；
- live `approval_required` 立即插入 / 合并（同一 `reply_id` 不重置 `requested_at`）；
  live `approval_resolved` 只更新本次会话里已经出现的卡，不凭回执伪造记录；
- 本次已知但快照里消失的 pending 视为 `expired`（进程重启会把 pending 标 expired，D19）；
- 提交失败（例如 409）按 target 重读历史审批并覆盖本地状态，再显示可读错误。

**未改**：后端 `.py`、D1–D23、既有 ADR 原文、设计系统目录；未跑 `ruff format`；
未新增 SSE type / 端点（词表守卫无需改状态词表）。

---

## 1. 票面 4 条验收判据

### 1.1 审批卡显示工具名、参数摘要、请求成员、剩余时间

| 命令 / 操作 | 读数 |
| --- | --- |
| `node --test app/tests/approval_ui.test.cjs` | **7 pass / 0 fail**（相关：「频道视图从 GET /approvals?state=pending 恢复审批卡…」） |
| DOM：`GET /api/approvals?target_id=ch-1&state=pending` 返回 2 条 | 请求恰好 1 次；`#approvalBoard` 可见；`approval-card` 2 张；第一张含 `Write`、`notes.txt`、`Ada`、`剩余 09:00`、`待审批` |
| 真实浏览器 1440×900 | `Write / Ada / 待审批 / 剩余 08:42`；`Bash / Bob / 待审批 / 剩余 08:57`（见 §2） |
| 真实浏览器 1024 / 430 | 卡数不变；430×900 @dpr2 下 `scrollWidth = 430`，无横向溢出 |
| 任务线程 | `#ttApprovalSection` 可见，显示 `Write / Ada / 待审批 / 剩余 08:49`；频道卡不串入任务线程（`channelCards` 只在频道容器） |

倒计时由 `requested_at + 600s` 派生：DOM 用例把时钟推到 `+60s` 断言 `09:00`，再推到 `+600s`
断言 `expired`；秒级刷新只改写 `.approval-remaining` 文本，不替换按钮节点。

### 1.2 允许 / 拒绝后进入 resolved；超时显示 expired；不重复提交

| 命令 / 操作 | 读数 |
| --- | --- |
| DOM：允许 | 一次性提交全部 `tool_call`（`call-1` / `call-2` 均 `confirmed: true`）到 `POST /api/approvals/reply-1`；响应后 `data-state="approved"`、`已批准`、动作数 0 |
| DOM：提交中双击 | 第一次点击进入 in-flight 后按钮 `disabled`；第二次点击**不**产生第二个 POST（`postCalls.length === 1`） |
| DOM：resolved 后再点旧按钮 | 不发请求、状态保持 `approved` |
| DOM：拒绝 | `confirmed: false` 一次提交；`data-state="denied"`、`已拒绝`、动作数 0 |
| DOM：超时 | 倒计时归零后 `data-state="expired"`、`已过期`、动作数 0 |
| DOM：重启 / 快照丢失 | 第二次 pending 快照为空（模拟进程启动把 pending 标 expired）后卡转 `expired`、动作数 0 |
| 真实浏览器 | 点「允许」后 `reply_live_channel_mask` 变为 `approved`、动作数 0；另一张 pending 仍有 2 个动作（两卡互不影响） |

### 1.3 `node --test` DOM 用例 + ego-browser 真实浏览器证据

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/approval_ui.test.cjs` | **7 pass / 0 fail** |
| `node --test app/tests/*.test.cjs` | **164 pass / 0 fail**（新增 7 条；既有 157 条零回归） |
| `scripts/check.sh` | 见 §3（全量门禁） |
| ego-browser（真实 Chromium） | 七张截图，见 §2 |

### 1.4 审批不泄露 env / 凭证

| 判据 | 读数 |
| --- | --- |
| 渲染字段 | 只读 `tool_calls[i]` 的 `id` / `name` / `input`；调用对象上的 `credentials` 等额外字段不读 |
| 敏感键递归掩码 | `api_key=••••`、`"password":"••••"`、`"TOKEN":"••••"`、`"Authorization":"••••"`；`NODE_ENV` 等非敏感键保留 |
| DOM 用例泄露扫描 | `sk-live-DO-NOT-LEAK` / `hunter2-DO-NOT-LEAK` / `Bearer DO-NOT-LEAK` / `env-DO-NOT-LEAK` / `call-level-DO-NOT-LEAK` 均 `includes === false` |
| 真实浏览器泄露扫描 | `document.body.textContent` 对 `sk-live-DO-NOT-LEAK`、`env-DO-NOT-LEAK` 的命中数 `[]`；截图只出现 `••••` |
| 后端约束 | #149 已把 `approval_request.tool_calls` 收窄为 `{id,name,input}`、不存凭证；前端不再放大 |

---

## 2. 真实浏览器证据（ego-browser / 真实 Chromium）

**装置**：

```sh
# 独立数据目录 + 真实 uvicorn（本 worktree 的 `app/`）
PYTHONPATH=/tmp/alibot-wt/issue-153-impl/app .venv/bin/python -m alibot \
  --port 8832 --data-dir /tmp/issue153-data
```

**种子**（仓库自己的 `Database` 方法；脚本在 `/tmp/issue153-seed2.py`，不入库）：

```text
Database.create_agent ×2（Ada / Bob，ollama:qwen2.5）
Database.create_channel「设计系统重构」含两名成员
Database.create_task → task_9774ae6ec526（target tgt_8220373600e2）
Database.create_approval_request ×3（服务启动之后再写，避免 startup expiry）：
  reply_live_channel_pending  target=ch_…   Ada / Write
  reply_live_channel_mask     target=ch_…   Bob / Bash（input 含 api_key / env.TOKEN）
  reply_live_task             target=tgt_…  Ada / Write
```

**视口与读数**（同一次真实会话）：

| 视口 | 频道消息流 | 任务线程 |
| --- | --- | --- |
| 1440×900 @dpr1 | `approval-channel-1440.png`：两卡、成员 Ada / Bob、倒计时 08:42 / 08:57、允许 / 拒绝；`scrollWidth=1440` | `approval-task-1440.png`：dock 380px；`Write / Ada / 08:49`；`channelCards=2`（频道容器仍在但不在 dock） |
| 1024×768 @dpr1 | `approval-channel-1024.png`：两卡；`scrollWidth=1024` | `approval-task-1024.png`：dock 340px；一卡 |
| 430×900 @dpr2 | `approval-channel-430.png`：两卡；`scrollWidth=430`；移动壳 | `approval-task-430.png`：dock 380px（抽屉 `is-open`）；一卡；`scrollWidth=430` |
| 1440×900（允许后） | `approval-channel-resolved-1440.png`：mask 卡 `approved` / `已批准`、动作 0；另一卡仍 `pending`、动作 2 | — |

**逐项读数**（真实浏览器 DOM）：

```text
CHANNEL_1440  boardHidden=false
  reply_live_channel_mask     pending  剩余 08:57  Bash  command=… · api_key=•••• · env={"TOKEN":"••••","NODE_ENV":"test"}  按钮 允许/拒绝
  reply_live_channel_pending  pending  剩余 08:42  Write path=src/theme/tokens.css · content=…
  leaks=[]                 scrollWidth=1440
CHANNEL_1024  cards=2  remaining=08:55  scrollWidth=1024
CHANNEL_430   cards=2  dpr=2  scrollWidth=430
TASK_1440     sectionHidden=false  dockOpen=true  dockWidth=380
              reply_live_task pending 剩余 08:49 Write
TASK_1024     cards=1  dockWidth=340  scrollWidth=1024
TASK_430      cards=1  dockWidth=380  dpr=2  scrollWidth=430
RESOLVED      state=approved  buttons=0  otherState=pending  otherButtons=2  pendingCount=1
```

**服务端字节自证**（浏览器实际拿到的文件 = 本 worktree 文件）：

```text
$ curl -s http://127.0.0.1:8832/studio/app.js | md5 -q ; md5 -q app/alibot/studio/app.js
3046ee5051baf7eb7e03f07325f1cc56
3046ee5051baf7eb7e03f07325f1cc56
$ curl -s http://127.0.0.1:8832/studio/index.html | md5 -q ; md5 -q app/alibot/studio/index.html
92b78a8b1097845d7afb2b4e7578e063
92b78a8b1097845d7afb2b4e7578e063
$ curl -s http://127.0.0.1:8832/studio/app.css | md5 -q ; md5 -q app/alibot/studio/app.css
3619c49c8af3eccfa5f3de9f47ca6d04
3619c49c8af3eccfa5f3de9f47ca6d04
$ curl -s http://127.0.0.1:8832/studio/i18n.js | md5 -q ; md5 -q app/alibot/studio/i18n.js
0e170704cc7c492e99a8177d12d39655
0e170704cc7c492e99a8177d12d39655
```

---

## 3. 测试与门禁

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/approval_ui.test.cjs` | 7 pass / 0 fail |
| `node --test app/tests/*.test.cjs` | 164 pass / 0 fail |
| `scripts/check.sh --fast` | 门禁全绿；node 164 / pass 164 / fail 0 |
| `scripts/check.sh`（merge 前全量） | 见下 |
| `scripts/check.sh`（merge 集成分支 tip 后全量） | 见下 |

`scripts/check.sh`（本分支全量，行为定稿，merge 前）：**门禁全绿**（exit 0）；
前端 `node --test`：**tests 164 / pass 164 / fail 0**。

合并：基线 `b594859`；集成分支 tip 仍是 `b594859`（本票从它切出后未再前进），
`git merge --no-ff agentscope2-integration` 返回 `Already up to date`；合并后重跑全量门禁：

```text
pytest:        813 passed, 9 skipped in 365.54s
ruff check:    All checks passed!
ruff format:   199 files already formatted
mypy:          Success: no issues found in 85 source files
pyright:       0 errors, 0 warnings, 0 informations
node --test:   tests 164 / pass 164 / fail 0
门禁全绿（exit 0）
```

---

## 4. 设计说明与残余

- **live 帧没有 `requested_at`**：规格 §4.5 的 `approval_required` 只有 `reply_id` / `member_id` /
  `tool_calls`。因此 live 插入时暂以「收到帧的时刻」作为倒计时起点；任何一次 pending 快照
  （打开视图 / 重连）会用后端的 `requested_at` 覆盖它。这是前端在协议字段既定的前提下能做的最准取值。
- **重启后的 expired**：后端启动即把 pending 标 expired（D19），所以**整页刷新**后不会再拿到这些
  pending 行，UI 也不会凭空造卡。可见的 expired 来自两条真实路径：①本次会话里倒计时越过 600s；
  ②本次会话已经显示过 pending，随后重连 / 快照里它消失（模拟后端重启），卡转 `expired`。
  另外提交得到 409 时会按 target 重读历史审批，把真实终态铺回来。
- **只恢复 pending**：`GET /api/approvals?state=pending` 是规格指定的恢复读面；本次会话内的
  `approved` / `denied` / `expired` 卡保留显示，刷新页面后不再回放历史（避免把审批历史铺进消息流）。
- **运行面板不是审批面**：票面写「运行面板/消息流」，本票落在消息流与任务线程；运行面板继续是
  只读过程面（#155）。`#approvalBoard` 在消息视图内，任务线程有自己的 task target 容器，两者互不串台。
- **倒计时刷新不重建 DOM**：每秒只改 `.approval-remaining`；只有 pending → expired 才重铺该面，
  避免用户点击瞬间按钮被替换（真实浏览器验证时曾复现 `element is not connected`，已修正）。
- **敏感键掩码是前端第二道闸**：后端已收窄 `tool_calls`；前端仍递归掩码，且只读 `id`/`name`/`input`，
  防止未来输入里出现凭证字段时被摘要原样铺开。
- 未新增 SSE type / 端点 / DB 字段；无需迁移；未改设计系统目录；截图目录只含本票证据。
