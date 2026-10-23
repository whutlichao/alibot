## Summary

Studio 的**每 target 一条常驻 `EventSource`** 换成了**整页一条全局流**：`GET /api/events`。
连接数与 target 数因此彻底解耦——2 频道 + 4 私信不再占满浏览器对单一 origin 的 6 条 HTTP/1.1 连接，
之后的 `fetch`（发消息 / 切频道 / 刷新列表）与 F5 的文档请求不再排队。

根因（已在真实 Chromium 里实测确认）：`app/alibot/studio/app.js` 的 `syncStreams()` 给每个 target
各开一条常驻 `EventSource`；Chromium 单 origin HTTP/1.1 并发上限 6，uvicorn 无多路复用，
6 个 target 正好占满。症状是 `/studio/` 渲染出来但完全不响应、刷新也刷不动（服务端一直健康）。

```text
app/alibot/events.py       EventBus.subscribe_all()：全局订阅通道（同容量 / 同 finally 摘除 / 同一把锁）
app/alibot/api.py          GET /api/events：纯 live 全局流 + live_sse_frame()（摘掉 id: 行）
app/alibot/studio/app.js   state.streams(Map) → state.stream 单例；handleGlobalStreamEvent 按 target_id 分流
app/tests/test_event_stream.py           +4 条后端用例（全局订阅 / 释放 / 透传无 id: / rotate）
app/tests/global_event_stream_ui.test.cjs  新增 7 条前端用例（六 target → 一条流是核心断言）
app/README.md              SSE 段一句 + 契约表一行（仅此两处）
```

**四条承重口径**：

1. **一条流，按帧分流**：`handleStreamEvent(targetId, event, type)` 的签名与分流逻辑**未改**，
   全局流用 `payload.target_id` 作为 `targetId` 调它，于是 message / member_status / typing /
   round_outcome / live-only 全部复用既有处理。缺 `target_id` 的帧丢弃：不猜归属、不写 `seenSeqs`
   （那份集合同时是「我看到哪一条」的游标）。
2. **全局流不补拉**：不接受 `after_seq`、不读库、**不写 `id:` 行**。`sse_frame` 一字未改（它的语义仍
   只由事件类型决定），`id:` 行由路由层的 `live_sse_frame()` 摘掉——不推进浏览器的 `Last-Event-ID`。
   重连补齐由既有的 `loadWorkspace({silent:true, keepActive:true})` 全量重取承担（ADR-0020）。
3. **既有三个 per-target 端点全部保留、语义不变**（`/api/channels/{id}/events`、
   `/api/dms/{id}/events`、`/api/tasks/{id}/events`），任务 dock 的 `connectTaskStream()` 仍是独立一条
   （全局流 + 任务流 = 2 条）。重连纪律的语义不变：`rotate` → 250ms 重连；`onerror` →
   `reconnecting` + `online=false` + 横幅 + 1500ms 重连；`onopen` → 恢复 online 并在 `wasOffline` 时
   做一次全量兜底。
4. **释放路径两处**：订阅在返回 `StreamingResponse` 之前建立，`finally` + `BackgroundTask` 各释放一次
   （闸门吃掉重复释放），照 `dm_events` 的写法；`subscribe_all` 与 `subscribe` 共用一把锁与同一份
   `QUEUE_LIMIT`，跟不上的订阅者丢事件、不阻塞写路径。

取舍（如实记，票面预定）：全局流**不发** `round.active` 快照（没有单一 target 的语义）。后果是
「有一轮 in-flight 时按 F5」要等下一帧 live 事件才恢复停止按钮；要补的话正路是给工作台快照加
active-rounds 字段，而不是把 `round.active` 塞回全局流。重连后的审批兜底从
`loadApprovalsFor(targetId)` 换成全工作区 `loadPendingApprovals()`（一条流断了就是所有 target 都漏帧）。

> Answer（逐条可复现读数 + 双轴 code-review）：`app/specs/issue-250-acceptance.md`；
> tracker 摘要见 #250 的 comment。

## Evidence

**红证据（先红后绿，隔离基线 `77281c7`）**

| 口径 | 基线 | 修复后 |
| --- | --- | --- |
| `scripts/red-evidence.sh 77281c7 app/tests/global_event_stream_ui.test.cjs` | **6 failed / 1 passed**，核心断言 `6 !== 1`（证据目录 `alibot-red.drjKg0`） | 7 passed |
| 基线 worktree 上跑新增后端用例 | **4 failed**（`'EventBus' object has no attribute 'subscribe_all'` ×2、`assert 404 == 200`、等不到全局流的订阅建立） | 4 passed |

**真实浏览器（ego-browser / 真实 Chromium，2 频道 + 4 私信 = 6 个 target，任务 dock 关闭）**

| 读数 | 修复前（8898） | 修复后（8899） |
| --- | --- | --- |
| 并发打开的 `/events` 连接（CDP `Network.*` 口径） | **6**（全是 `/{channels,dms}/{id}/events?after_seq=0`） | **1**（`/api/events`） |
| 页面内 `fetch('/api/channels')` | **5002ms `AbortError`** | **11ms**（重载后 3ms） |
| `page.reload()` | **12s 超时失败** | **294ms 成功** |
| 服务端 access log 一次载入的流请求 | 6 条 per-target | 1 条 `/api/events` |
| 端到端直播（POST 消息 → 不刷新出现在消息面） | 被连接池饿死的 `fetch` 挡住 | `postStatus 201` → 消息 seq 1 立即出现 |

自建实例 + 独立数据目录（`/tmp/alibot-browser-4331/{after,before}`），**没有**碰用户正在跑的 8788。
截图：`/tmp/alibot-ego-4331/after-studio.png`、`/tmp/alibot-ego-4331/after-live-message.png`。

**门禁（`scripts/check.sh` 全量，定稿只跑一次）**

```text
pytest       1155 passed, 11 skipped in 624.53s
ruff check   All checks passed!
ruff format --check   308 files already formatted
mypy         Success: no issues found in 123 source files
pyright      0 errors, 0 warnings, 0 informations
node --test  284 tests / 284 pass / 0 fail（glob 自动纳入新用例文件）
门禁全绿
```

## Merge Danger

**Door:** two-way——纯增量：新增 `GET /api/events` 与 `EventBus.subscribe_all()`，旧端点全部保留、
语义不变；无 schema 变更、无新依赖、无 pin 改动。前端不再开 per-target 流，但那一层只服务 Studio
自己，没有外部调用方。

**Blast Radius:** Studio 事件流层（`syncStreams` / `connectStream` / `handleGlobalStreamEvent`）+
`EventBus` 新增全局通道。风险点与已验证项：全局订阅的释放路径（`finally` + `BackgroundTask`，
用例 `test_event_bus_subscribe_all_stops_receiving_after_the_context_exits`）；
`handleStreamEvent` 的 `targetId` 在所有分支都来自 `payload.target_id`（前端用例 2/3/4）；
是否残留 per-target 连接代码（`grep state.streams` 已无引用，`closeStream(targetId)` 已删）；
任务 dock 的独立流未受影响（`run_panel_ui.test.cjs` 重连用例仍绿）。

Closes #250
