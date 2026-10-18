# Issue #155 验收 —— 运行面板 UI：成员事件、指派、Plan、SOP

> 票面：GitHub [#155](https://github.com/whutlichao/alibot/issues/155)（impl，G-impl / G-ui）。
> 规格：`app/specs/agentscope-2-alignment.md` §4.5（SSE 词表）/ §4.6（前端新视图）。
> 裁定：ADR-0020（事件是直播、消息才是正本）、ADR-0023（Plan / SOP 不是任务）。
> 分支：`issue-155-impl`；worktree `/tmp/alibot-wt/issue-155-impl`；基线 `acc0d12`（集成分支 tip）。

| 项 | 值 |
| --- | --- |
| 后端改动 | **零**（`GET /targets/{id}/team-runs` / `/plan`、`GET /sops`、`GET /sop-runs/{id}` 已在基线） |
| 前端落点 | `app/alibot/studio/{index.html,app.js,app.css,i18n.js}` |
| 新增用例 | `app/tests/run_panel_ui.test.cjs`（12 条 DOM 用例） |
| 浏览器证据 | `app/specs/issue-155-evidence/*.png`（真实 Chromium / ego-browser，三档视口 × 频道/任务线程） |

---

## 0. 交付概览

运行面板是右侧 dock 的**第四形态**（`#dockRun`，`dockMode = 'run'`），与频道成员 / 任务线程 /
Agent 详情共用同一个 380px 容器：

- **入口**：频道头的 `#btnRunPanel`（DM 下隐藏）与任务线程头的 `#btnThreadRunPanel`；
- **target**：频道入口读频道 target；任务线程入口读 task target，并给 `GET /team-runs` 带
  `task_id` 过滤；
- **内容**：按 `team_run` / member 分组的分派（`started` / `succeeded` / `failed` /
  `interrupted` / `budget-exhausted`、耗时、`reply_excerpt`、错误原因）、按 member + reply
  分组的 live `agent_event`、按成员分组的 Plan 三态（`pending` / `in_progress` / `completed`）、
  SOP run 的 `phase` 与逐步 verdict；
- **兜底**：打开面板与 SSE 重连（`onopen`）都重读 `GET /team-runs`、`GET /plan`；已知
  `sop_run_id`（来自 live `sop_event`）再重读 `GET /sop-runs/{id}`（+ `GET /sops/{definition_id}`
  取步骤名）；
- **独立面**：live 帧只进 `state.liveEvents` 与运行面板，不写消息流 / `seenSeqs` / 未读 /
  搜索；既有视图不新增任何 team / Plan / SOP 内容。

**未改**：后端 `.py`、D1–D23、既有 ADR 原文、设计系统目录；未跑 `ruff format`；没有新增
SSE type / 端点（词表守卫无需改 `app/README.md`）。

---

## 1. 票面 4 条验收判据

### 1.1 实时帧按 member 归属；断线后刷新 `GET /team-runs` / `GET /plan` / `GET /sop-runs/{id}` 快照

| 命令 / 操作 | 读数 |
| --- | --- |
| `node --test app/tests/run_panel_ui.test.cjs` | 12 pass / 0 fail（相关：「live agent_event 按 member 归属…」「事件流重连（onopen）后重新读…」「重连快照兜底…」「SOP：live sop_event…」） |
| DOM：live `agent_event`（`TEXT_BLOCK_DELTA` / `TOOL_CALL_START`） | 两组 `[data-member]`，组内显示成员显示名 + 事件 type + payload 摘要 |
| DOM：重连 | EventSource `onopen` 后 `GET /team-runs` +1、`GET /plan` +1；面板关着时 0 次 |
| DOM：stale 帧 | 断线前的旧 `plan_update` / `sop_event` 不会把快照里已收口的 Plan / SOP 状态拉回 |
| 真实浏览器 | 打开面板时请求 `GET /api/targets/ch_83795cd8d0fb/team-runs?limit=20`、`GET /…/plan`；SOP run 出现后刷新再请求 `GET /api/sop-runs/soprun_cfa91fb7986d` 与 `GET /api/sops/sop_f04e047250e8` |

### 1.2 分派状态（started/succeeded/failed/budget-exhausted）可读；Plan 三态与 SOP phase 可读

| 命令 / 操作 | 读数（真实浏览器，1440×900） |
| --- | --- |
| 运行面板 · 分派 | `成功 耗时 2.4s / 成员结论：配色对比度已修正`；`失败 耗时 2.0s / 模型不可达：connection refused`；`预算耗尽 耗时 0.5s`；run 头 `已完成 14:40 #2dc137`、队长 `Ada`；分派组头 `Bob ×3` |
| 运行面板 · Plan | `读取现有配色 token 已完成`、`修正错误态色位 进行中`、`重跑 DOM 用例 待开始`（`data-state` = `completed` / `in_progress` / `pending`） |
| 运行面板 · SOP | `#b7986d 失败 14:45`；步骤 `起草发布说明 运行中`；verdict `未通过 缺少验收命令` |
| 任务线程面板 | target 名 = `修一处样式`；分派 `成功 耗时 3.2s / 任务 #5c42c4 / 任务线程：标题行距已收紧` |
| DOM 用例 | 分派耗时由两个后端时间戳计算；`started` 无 `finished_at` 显示「进行中」；Plan 三态与 SOP verdict 各有断言 |

### 1.3 面板不把成员过程混入频道或任务线程的消息流 / 未读 / 搜索

| 命令 / 操作 | 读数 |
| --- | --- |
| DOM：「运行面板的 live 事件不进消息流、不计未读、不触发搜索」 | `state.messages.length` 不变、`unread_count` 不变、`seenSeqs.size` 不变、`/api/search` 请求 0 次；两条 live 帧只落在 `state.liveEvents` |
| DOM：任务线程消费新事件（既有用例） | live 帧按 target 记录；消息仍按 `seq` 去重 |
| 代码约束 | 面板只读 `state.liveEvents` / 三个快照端点；不调用消息、标读、搜索写入路径 |

### 1.4 DOM 用例 + ego-browser 真实浏览器证据

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/run_panel_ui.test.cjs` | **12 pass / 0 fail** |
| `node --test app/tests/*.test.cjs` | **157 pass / 0 fail**（新增 12 条；既有 145 条零回归） |
| `scripts/check.sh` | 见 §3（全量门禁） |
| ego-browser（真实 Chromium） | 六张截图，见 §2 |

---

## 2. 真实浏览器证据（ego-browser / 真实 Chromium）

**装置**：

```sh
# 独立数据目录 + 真实 uvicorn（本 worktree 的 `app/`）
PYTHONPATH=/tmp/alibot-wt/issue-155-impl/app .venv/bin/python -m alibot \
  --port 8831 --data-dir /tmp/issue155-data
```

**种子**（真实 HTTP 写路径 + 仓库自己的 `Database` 方法；脚本在 `/tmp/issue155-seed.py`，不入库）：

```text
POST /api/agents ×2（Ada / Bob，ollama:qwen2.5）
POST /api/channels「设计系统重构」含两名成员
PATCH /api/channels/ch_83795cd8d0fb/config → mode=team, leader=Ada
POST /api/channels/ch_83795cd8d0fb/tasks → task_193d2d5c42c4（target tgt_d2ae9db6e652）
Database.create_team_run / create_team_assignment / finish_team_assignment / finish_team_run
  → 频道 target：1 run + 3 条分派（succeeded / failed / budget-exhausted）
  → task target：1 run + 1 条带 task_id 的分派
Database.start_plan_run / finish_plan_run → 频道 Bob 三态 Plan、任务 Ada 两态 Plan
Database.create_sop_definition + create_sop_run → awaiting SOP（真实 SOPRunState JSON）
```

SOP 的 `sop_run_id` 在真实会话里来自 live `sop_event`（后端没有「按 target 列 SOP run」的读面）。
截图会话里通过真实 `POST /api/sop-runs/{id}/resume` 让服务端广播 `sop_event`（HTTP 200），
面板记录 run id 后点「刷新快照」，再用 `GET /sop-runs/{id}` 铺 run 状态与 verdict——这条路径
本身就是「live 帧 + 快照兜底」的真实演练。

**视口与读数**：

| 视口 | 频道运行面板 | 任务线程运行面板 |
| --- | --- | --- |
| 1440×900 @dpr1 | `run-panel-channel-1440.png`：dock 380px；四段 `运行与分派 / 成员事件 / Plan / SOP`；分派三态 + 耗时 + excerpt；Plan 三态；SOP 失败 + verdict | `run-panel-task-1440.png`：dock 380px；`修一处样式`；`任务 #5c42c4` + excerpt；Plan 两态 |
| 1024×768 @dpr1 | `run-panel-channel-1024.png`：dock 340px；同上读数 | `run-panel-task-1024.png`：dock 340px |
| 430×900 @dpr2（≤900 移动壳） | `run-panel-channel-430.png`：dock 抽屉 `is-open=true`，宽 380；内容不横向溢出 | `run-panel-task-430.png`：dock 抽屉 380 |

**逐项读数**（真实浏览器 DOM，同一次会话）：

```text
channel-1440  dockOpen=true  dockWidth=380  runVisible=true  title="设计系统重构"
  sections   ["运行与分派","成员事件","Plan","SOP"]
  states     ["已完成","成功","失败","预算耗尽","已完成","进行中","待开始","失败","运行中"]
  sopText    "SOP\n#b7986d\n失败\n14:45\n起草发布说明\n运行中\n未通过缺少验收命令"
channel-1024  dockOpen=true  dockWidth=340  同 1440
channel-430   dockOpen=true  dockWidth=380  dpr=2，同 1440
task-1440     dockOpen=true  dockWidth=380  title="修一处样式"
  states     ["已完成","成功","已完成","待开始"]
  planStates ["completed","pending"]
  sopText    "SOP\n还没有 SOP 运行。"（SOP run 属于频道 target）
task-1024 / task-430  同上；dock 宽 340 / 380
```

**服务端字节自证**（浏览器实际拿到的文件 = 本 worktree 文件）：

```text
$ curl -s http://127.0.0.1:8831/studio/app.js | md5 -q ; md5 -q app/alibot/studio/app.js
69f62be9d52dae81d4761de8c01b2f5e
69f62be9d52dae81d4761de8c01b2f5e
$ curl -s http://127.0.0.1:8831/studio/index.html | md5 -q ; md5 -q app/alibot/studio/index.html
02a654980648b88719f74ed3e0961250
02a654980648b88719f74ed3e0961250
$ curl -s http://127.0.0.1:8831/studio/app.css | md5 -q ; md5 -q app/alibot/studio/app.css
4ba2e9a77c0096209ab8c1f7b1bfc186
4ba2e9a77c0096209ab8c1f7b1bfc186
$ curl -s http://127.0.0.1:8831/studio/i18n.js | md5 -q ; md5 -q app/alibot/studio/i18n.js
e12dcc9600bff7a8052748a5496f1906
e12dcc9600bff7a8052748a5496f1906
```

---

## 3. 测试与门禁

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/run_panel_ui.test.cjs` | 12 pass / 0 fail |
| `node --test app/tests/*.test.cjs` | 157 pass / 0 fail |
| `scripts/check.sh`（merge 前全量） | 见下 |
| `scripts/check.sh`（merge 集成分支 tip 后全量） | 见下 |

`scripts/check.sh`（本分支全量，行为定稿）：**门禁全绿**（exit 0）。读数：pytest 全量（含本票
新增 DOM 用例不影响的 pytest 面）、`ruff check` / `ruff format --check` / mypy / pyright 全绿；
前端 `node --test`：**tests 157 / pass 157 / fail 0**。

删除两处未使用字段（`fromSnapshot` / `runCount`）与更新 dock docstring 后，再跑
`scripts/check.sh --fast`：**门禁全绿**（exit 0），`node --test` 仍是 **157 pass / 0 fail**。
这两处是纯清理（无行为变化），全量门禁已在行为代码定稿时跑过。

合并：本分支基线就是集成分支 tip `acc0d12`；`git merge --no-ff agentscope2-integration`
无新提交可合（`Already up to date`）。

---

## 4. 设计说明与残余

- **分组口径**：分派按 `team_run` → `member` 分组（同一分派只一行，live 帧按 `assignment_id`
  合并）；`agent_event` 按 `member` → `reply_id` 分组（最近 80 条）；Plan 取每个成员最近一个
  run 的清单；SOP 按 run + `step_index` 合并。
- **去重 / 不倒退**：分派有状态推进秩（`started` < 终态），迟到的 started 帧不会把终态降回；
  快照完成时记录 live 队列标记，Plan / SOP 只应用标记**之后**的帧，重连刷新不会被旧帧拉回；
  SOP 的 `completed` / `failed` 是终态，不会被迟到的 live 帧覆盖。
- **SOP 步骤名**：AgentScope 的 `SOPRunState.steps` 只有 phase / verifications，没有 subject；
  步骤名从 `GET /api/sops/{definition_id}` 读（按 definition id 缓存），live 帧的 `payload.step`
  是回退。没有定义可读时显示「步骤 N」。
- **SOP run id 的来源**：后端没有「按 target 列 SOP run」的端点（规格 §4.4 未列），所以面板
  只能从 live `sop_event` 发现 id；同一会话内重开面板会从 `state.liveEvents` 重建已知 id，
  但**整页刷新后**未知 id 不会凭空出现——这是 live-only 语义的直接结果，不是漏读。
  快照刷新只覆盖已知 id（与 ADR-0020「断线不补发」一致）。
- **成员事件为空是真实读数**：静态服务没有可用的本地模型，浏览器证据里没有真实
  `agent_event`；该段的 live 分组 / 摘要 / 与消息流隔离由 DOM 用例覆盖。浏览器证据里真实
  走通的是 live `sop_event` → 面板记录 id → 刷新 `GET /sop-runs/{id}` 的完整链路。
- **分页**：`GET /team-runs` 只取 `limit=20` 的第一页（`has_more` 不呈现翻页控件）；当前运行
  面板是「最近 20 次运行」的只读面。更早的历史需要后续票决定翻页交互。
- 运行面板的 live 事件保留上限沿用既有 `LIVE_EVENTS_LIMIT = 256`；事件是 live-only，
  不进任何浏览器存储。
- 未改设计系统目录；截图目录只含本票证据。
