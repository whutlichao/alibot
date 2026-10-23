<!-- 本文件是 #250 的 Answer（逐条可复现读数）；tracker 上的 issue #250 保持原样，摘要回填为 comment。 -->

# Issue #250 Answer —— 整页一条全局事件流：连接数与 target 数解耦

> 票面：GitHub #250（实施票，门禁 **G-impl**，测试档**宽**：全量）。
> 前置 skill：`diagnosing-bugs` / `tdd` / `code-review` / `pr`（全文读过，流程按它们走）。

| 项 | 值 |
| --- | --- |
| 分支 | `studio-global-event-stream`（本 worktree 的分支） |
| 本票 base | `77281c7`（`main` 合并后的 tip） |
| PR | [#251](https://github.com/whutlichao/alibot/pull/251)（head `studio-global-event-stream`） |
| PR body | `app/specs/issue-250-pr-body.md`（`--body-file` 用） |
| 新增验收 | `app/tests/global_event_stream_ui.test.cjs`（前端 7 条）；`app/tests/test_event_stream.py` 追加后端 4 条 |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app/alibot/events.py` | `EventBus` 新增 `subscribe_all()`（与 `subscribe(target_id)` 同形：同样的 `QUEUE_LIMIT`、同样的 `finally` 摘除、同一把 `_lock`）；`publish()` 在投递给该 target 的订阅者**之后**再投递给全局订阅者，`suppress(queue.Full)` 同前。`sse_frame` / `REPLAYABLE_TYPES` / `LIVE_EVENT_TYPES` / `is_live_only` / `is_replayable` **一字未改**。 |
| `app/alibot/api.py` | 新增 `GET /api/events`（纯 live：不接受 `after_seq`、不读库补拉、不写 `id:` 行、不发 `round.active` 快照）。沿用既有三条 SSE 路由的全部工程约定（`text/event-stream` / 三个 header / `SSE_STREAM_MAX_SECONDS` 到点 `event: rotate` / `queue.Empty` 发 keep-alive / `shutting_down` / `is_disconnected()`）；订阅在返回 `StreamingResponse` 之前建立，`finally` + `BackgroundTask` 两处释放（照 `dm_events`）。另加模块级 `live_sse_frame()`：**只摘掉** `sse_frame` 给可补拉事件写的 `id:` 行（见 §3 决策 1）。 |
| `app/alibot/studio/app.js` | `state.streams`（Map，每 target 一个 entry）→ `state.stream`（单例 `{source, timer, rotating}`）；`syncStreams()` 只保证「至多一条 `EventSource('/api/events')`」，`closeStream` 的 per-target 语义删除；`connectStream(entry)` 不再取 per-target 游标；新增 `handleGlobalStreamEvent(event, type)` 按 `payload.target_id` 调既有 `handleStreamEvent(targetId, event, type)`（**签名与分流逻辑未改**）；缺 `target_id` 丢弃。`connectTaskStream()` 保持独立、未改。 |
| `app/tests/test_event_stream.py` | 追加 4 条：全局订阅的归属与载荷同形 / 退订后不再收到 / `GET /api/events` 透传任意 target 且帧无 `id:` / 全局流的 `rotate` 且轮换前无补拉帧。既有三条端点的用例全部保留、全绿。 |
| `app/tests/global_event_stream_ui.test.cjs` | **新增** 7 条前端用例（沿用 `vm` 装载 + `fetch` 替身 + DOM 替身 + `FakeEventSource` 接缝，未新开第二套框架）。 |
| `app/README.md` | 仅「实时呈现」段加一句全局流说明 + HTTP 契约表加一行 `GET /api/events`（diff 极小；per-target 三条端点的描述**未动**）。 |
| `app/specs/issue-250-*.md` | 本文件与 PR body。 |

**不做**（票面 Constraints）：不删、不改语义地保留 `/api/channels/{id}/events`、`/api/dms/{id}/events`、
`/api/tasks/{id}/events`；不改 `sse_frame()` 与两份事件词表；不引入 schema 变更 / 新依赖 / pin 改动；
没有 `ruff format` 整文件重排；不动 `alibot-design-system/`；任务 dock 的 `connectTaskStream()` 未并进全局流。

---

## 1. 人定裁定（`orca orchestration ask` 回信，逐字要点）

票面 Ownership 的文件清单里没有 `app/README.md`，而实现会让 README 的 SSE 段漂移。提问后回信：

> 允许：补 README 的 SSE 段与契约表一行…… 理由：这是细节类（文档落点），不是 scope 扩张。
> `app/README.md` 第 500 行那段与第 567 行契约表是**本仓 SSE 契约的唯一文档正本**……
> 约束补充：1. README 只加必要的那一行/一句，diff 尽量小；不要改 per-target 三条端点的既有描述。
> 2. 全局流「不补拉」这件事必须在 README 里说清，否则下一个人会以为它也该有 after_seq。
> 3. Ownership 相应扩大为：… / `app/README.md`（仅 SSE 段与契约表）/ `app/specs` 文档。

落地：README 只动了两处（SSE 段新增一段「Studio 另有一条整页共用的全局流 `GET /api/events`」、
契约表新增一行），并明写「不接受 `after_seq`、不读库补拉、也不写 `id:` 行」。

---

## 2. 验收逐条

### 2.1 红证据（先取得，后实现）

**前端**（隔离基线副本，脚本 `scripts/red-evidence.sh`，只换测试不换产品代码）：

```sh
scripts/red-evidence.sh HEAD app/tests/global_event_stream_ui.test.cjs
```

```text
基线：HEAD (77281c7)
测试：app/tests/global_event_stream_ui.test.cjs（取自当前工作区）
基线 app.js md5：2c3f2b0f7f0e95c491369ed3bd970063
证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.drjKg0

✖ 六个 target 的快照载入后只有一条全局流：/api/events，不带任何 per-target 游标
    AssertionError: 整页最多一条事件流（连接数与 target 数解耦）
    6 !== 1
✖ 全局流的 message 帧按 target_id 归属：当前 target 进消息面、其它 target 只推进游标
✖ 帧缺 target_id ⇒ 丢弃：不猜归属、不写 seenSeqs、不推进游标
✖ onerror ⇒ 不可达 + reconnecting，onopen 恢复并只保留一条流（7 !== 2）
✖ rotate 只是例行轮换：不算掉线，250ms 后重连那一条流
✖ EventSource 不存在（测试替身 / 极旧浏览器）时 syncStreams 不抛错
ℹ tests 7 / pass 1 / fail 6
```

核心红读数就是票面要的那一条：**改动前 `sources.length === 6`**（6 个 target 各一条），断言 `=== 1` 失败。
唯一在基线上通过的是「live 运行事件按 target 归属记录」——基线把每 target 的流都开出来了，
往其中一条投一个带 `target_id` 的帧照样能被归属，所以它不是本票的判据，保留它是为了证明
「全局流不改变既有归属语义」。

**后端**（隔离基线 worktree `/tmp/alibot-baseline-4331`，`git worktree add … 77281c7`，只把新测试文件
拷进去，产品代码保持基线字节）：

```sh
cd /tmp/alibot-baseline-4331/app && ../.venv/bin/python -m pytest tests/test_event_stream.py \
  -k "subscribe_all or global_event_stream"
```

```text
E   AttributeError: 'EventBus' object has no attribute 'subscribe_all'   (× 2)
E   assert 404 == 200            # GET /api/events 在基线上不存在
E   AssertionError: 等不到全局流的订阅建立；已收到的帧：[]   # 404 的流读不到任何帧
4 failed, 25 deselected
```

### 2.2 前端用例（`app/tests/global_event_stream_ui.test.cjs`，7 条）

```sh
node --test app/tests/global_event_stream_ui.test.cjs
```

| # | 用例 | 断言 |
| --- | --- | --- |
| 1 | 六个 target 载入后只有一条全局流 | `sources.length === 1` 且 url 恰为 `/api/events`；没有任何 `/events?after_seq=`；删一个私信、加一个频道后再 `syncStreams()` 仍然只有一条、且那条没被关掉 |
| 2 | `message` 帧按 `target_id` 归属 | 当前 target 进 `state.messages`（1 条）、游标推进；非当前 target 不进消息面但自己的游标推进，且未读按后端重取（`/api/dms`）；同一帧重复到达只留一条 |
| 3 | live 运行事件按 `target_id` 归属 | `agent_event` → `ch-2`、`team_assignment` → `dm-1`，各自进 `liveEvents`；不推进游标 |
| 4 | 帧缺 `target_id` ⇒ 丢弃 | 消息面 0 条、`liveEvents` 空、**任何** target 的 `seenSeqs` 都没被写、所有 target 的游标仍为 0 |
| 5 | `onerror` / `onopen` | `online === false` + `streamState === 'reconnecting'` + 横幅出现 + 旧连接 `close()`；重连后仍只有一条流；`onopen` 恢复 `online`/`streamState === 'open'` 且不再开连接 |
| 6 | `rotate` 不算掉线 | `online`/`streamState` 保持；250ms 后重连，仍是一条 |
| 7 | `EventSource` 为 `undefined` | `syncStreams()` 不抛错、不开流 |

```text
✔ 7 passed / 0 failed（duration_ms 164.7）
```

全量前端用例族（glob，既有契约不回归）：

```sh
node --test app/tests/*.test.cjs
```

```text
ℹ tests 284 / pass 284 / fail 0
```

（其中 `run_panel_ui.test.cjs` 的「事件流重连后刷新 team-runs / plan」与 `mobile_shell.test.cjs` 的
`EventSource: undefined` 场景都仍在跑、仍绿——全局流换承载对象没有改语义。）

### 2.3 后端用例（`app/tests/test_event_stream.py`，追加 4 条）

| 用例 | 断言 |
| --- | --- |
| `test_event_bus_subscribe_all_receives_every_target_with_attribution` | 任意 target 的 `publish` 都到全局订阅者；载荷与单 target 订阅者**相等**；`target_id` 由 `publish` 补齐；单 target 订阅者不跨 target 收 |
| `test_event_bus_subscribe_all_stops_receiving_after_the_context_exits` | 退出 `with` 后再 `publish` 收不到（`finally` 摘除生效）；重新订阅是**新**通道，旧通道不会复活 |
| `test_global_event_stream_forwards_any_target_without_id_lines` | 真实 HTTP：全局流收到另一个 target 的 `typing` 与频道的一次性真实消息；`id` 全为 `None`；帧里除 `typing` 外只有 `message`（没有补拉快照、没有 `round.active`） |
| `test_global_event_stream_rotates_and_sends_no_replay_before_it` | `ALIBOT_SSE_ROTATE_SECONDS=1`：连接建立后第一个命名事件就是 `rotate`（此前只有 keep-alive 注释），随后正常收尾 |

```sh
cd app && ../.venv/bin/python -m pytest tests/test_event_stream.py tests/test_agent_event_stream.py
```

```text
36 passed（25 条既有 + 4 条新增 + 已有 agent_event 流用例）
```

### 2.4 真实浏览器验收（ego-browser，真实 Chromium）

自建实例、独立数据目录，**没有**碰用户正在跑的 8788：

```sh
# 造 2 频道 + 4 私信 = 6 个 target（当前票面说的那个数量）
cd app && ../.venv/bin/python -m alibot --port 8899 --data-dir /tmp/alibot-browser-4331/after
# 基线（未修复）用同一份数据目录的副本，在隔离 worktree 里跑
cd /tmp/alibot-baseline-4331/app && ../.venv/bin/python -m alibot --port 8898 --data-dir /tmp/alibot-browser-4331/before
```

| 读数（同一浏览器、同一份数据、任务 dock 关闭） | 修复前（8898） | 修复后（8899） |
| --- | --- | --- |
| 并发打开的 `/events` 连接（CDP `Network.requestWillBeSent`/`loadingFinished` 口径） | **6**，全是 `/{channels,dms}/{id}/events?after_seq=0` | **1**，`/api/events` |
| 页面内 `fetch('/api/channels')` | **5002ms `AbortError`** | **11ms**（重载后再测 3ms） |
| `page.reload()` | **12s 超时失败**（`page.reload timed out after 12000ms waiting for load`） | **294ms 成功** |
| 服务端 access log 里一次页面载入的流请求 | 6 条 per-target | 1 条 `/api/events` |
| 页面内 `state.streams` 键 / `state.stream` | `Map{ch_…,ch_…,dm_…×4}`（6 个键） | `state.stream` 单例、`streamState='open'`、`online=true` |
| 页面载入耗时 | 910ms | 277ms |

端到端直播（真实浏览器，不经刷新）：

```text
{ channelId: 'ch_72582d7c1f13', active: { activeChannelId: 'ch_72582d7c1f13', streamState: 'open', online: true } }
{ postStatus: 201 }                      # POST /api/channels/{id}/messages
{ live: true, messages: [ { seq: 1, body: '全局流直送：…' } ], lastSeq: [[ch_…, 1], [ch_…, 0]] }
```

截图：`/tmp/alibot-ego-4331/after-studio.png`（修复后工作台）、
`/tmp/alibot-ego-4331/after-live-message.png`（全局流直送的消息已落进消息面）。

> 测量口径备注：票面建议的 `performance.getEntriesByType('resource')` 在本机 Chromium 里对 SSE
> **返回 0 条**（SSE 请求不产生 resource timing 条目），因此连接数改用两个可核对的口径：浏览器侧
> CDP `Network.*`（开了几条、几条没结束）与服务端 access log（一次载入请求了几条流）。两者一致。

### 2.5 门禁（`scripts/check.sh` 全量，定稿只跑一次）

```sh
unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1
scripts/check.sh
```

```text
== pytest（全量）
1155 passed, 11 skipped in 668.71s (0:11:08)
== ruff check
All checks passed!
== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
309 files already formatted
== mypy（仓库根）
Success: no issues found in 123 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 284 / pass 284 / fail 0
门禁全绿
EXIT=0
```

`ruff format` **只**跑了 `--check`（票面约束：不整文件重排）。新增的 `global_event_stream_ui.test.cjs`
由 glob 自动纳入 node 用例族，没有改任何清单文件。

### 2.6 双轴 code-review（`code-review` skill：两个平行子代理，Standards / Spec 两轴不合并）

固定点 `77281c7`，被审区间 `git diff 77281c7...HEAD`（成文时为 `06a8530`）。两轴结论分开记：

**Standards 轴**——4 项「确认正确」（全局订阅的释放路径与 `dm_events` 同形、`handleStreamEvent` 的
`targetId` 在所有分支都来自 `payload.target_id`、无残留 per-target 代码（`state.streams` /
`closeStream` / `/events?after_seq=` 全仓零命中）、注释风格合规）；**3 硬违规 / 4 判断题**，硬违规全是文档同步：

| # | Findings | 处理 |
| --- | --- | --- |
| S1 | `CONTEXT.md` 的「订阅 (Subscription)」仍只写 per-target 流，且仍称「带最新 `last_seq` 重连」 | **已修**：按 `docs/agents/review-rules.md` 判断题 6（新增端点时 README 与 CONTEXT 相关段落一起改），该词条补了全局流一句 + 把 `last_seq` 重连限定到 per-target 流（改动 6 行） |
| S2 | README 自相矛盾：新增段说不补拉，但 §「补拉由后端读库完成」与 rotate 段仍笼统写「带最新 `last_seq` 重连」（含开头与运行配置表两处） | **已修**：四处都限定为「per-target 流带 `last_seq`；全局流重开后全量重取」，并把新增段从 live-only 段中间移到审批段之后（不再切断同一话题） |
| S3 | 交付文档未入库、正文残留 placeholder | **已修**：本文件与 PR body 随本票第三个提交入库，placeholder 全部填实 |
| J1 | `handleGlobalStreamEvent` 与 `handleStreamEvent`（及任务流的 `handleTaskStreamEvent`）各 `JSON.parse` 一次 | **保留**（票面约束：`handleStreamEvent` 签名与分流尽量不改；为此抽 `parseFrame` 会把任务流一起改）。三次解析同一小帧的代价可忽略，写进本表备查 |
| J2 | `state.stream !== entry` 守卫永不成立（无置空点）、`openStream()` 是两行转向器 | **已修**：删掉两处不可达守卫，`openStream` 并入 `syncStreams()`（少 6 行，行为不变，`rotate`/`onerror` 重连用例仍绿） |
| J3 | 任务 target 的帧被全局流与任务流各消费一次（`recordLiveEvent` 记两次；每条任务消息多一次 `GET /api/channels`） | **接受并写实**：票面要求全局流「任意 target 都透传」，所以不在前端过滤（过滤会和票面相反）；运行面板按 id 合并渲染不出现两行，多出的一次列表重取只在任务 dock 打开时发生。README 新增句与 PR body 都写明这层重叠 |
| J4 | README 新段插在 live-only/审批段之间 | **已修**（同 S2：移到审批段之后） |

**Spec 轴**——「`id:` 的处理忠实（`sse_frame` 不改词表，只在路由层摘首行）、`subscribe_all` 同形、
三条既有端点未动、`handleStreamEvent` 与基线逐字节相同、重连纪律保留、无残留 per-target 流路径」；
**3 缺失/部分 / 2 scope / 2 可疑**，处理见下：

| # | Findings | 处理 |
| --- | --- | --- |
| P1 | `api.py` docstring 说「停止句柄快照仍由既有的 per-target 流或 `loadWorkspace` 快照提供」——但 Studio 已不开 per-target 流、`/api/workspace` 也没有 active-rounds 字段 | **已修**：docstring 改说实话——F5 后要等下一帧带 `REPLY_START` / `round.active` 的 live 事件才恢复停止按钮；要补就给工作台快照加 active-rounds（另票） |
| P2 | README 仍写「立刻带最新 `last_seq` 重连」 | **已修**（同 S2） |
| P2 | 新增 rotate 用例里的 `ALIBOT_SSE_ROTATE_SECONDS=1` 无效（`SSE_STREAM_MAX_SECONDS` 在 `alibot.api` **导入期**绑定，`monkeypatch.setenv` 对已导入模块无效），且读到 rotate 就 `break`、没验证收尾 | **已修**：删掉无效 setenv 并在 docstring 写明原因（要真正缩短得改产品读 env 的时机，另票）；改为读到 **EOF**，断言 rotate 之后没有任何命名事件（流确实正常收尾）。代价是该用例跑默认 55 秒——与既有同类用例一致 |
| P2 | task target 双流重复消费 + 多一次 `/api/channels` | **接受并写实**（同 J3） |
| P2 | 前端用例把 `setTimeout` 替身成 `setImmediate`，「250ms / 1500ms」只验证了「会重连」 | **已修**：替身记录 `delay`，`rotate` 用例断言 250、`onerror` 用例断言 1500（现在用例名的节奇说法是真的） |
| J | `onopen` 把审批兜底扩到全工作区 `loadPendingApprovals({render:true})`、`refreshRunPanelFor()` 无参 | **接受**：一条全局流断了就是所有 target 都漏帧，按单一 target 补已不可能；与 `loadWorkspace` 同一写入口。已写进 §3 决策 3 |

两轴的**无 P0**、Standards 初判 `BLOCK` 的三项全部落在文档同步上（已全部收口）；Spec 轴初判
`OK with notes`，两条建议修项（docstring 失实、rotate 用例）已修。复审后本票代码与文档一致。

### 2.7 交付物

| 项 | 值 |
| --- | --- |
| worktree | 干净（`git status --short` 空；`.venv` 是本地符号链接、已被 gitignore） |
| 提交 | `06a8530` fix（实现 + 前端/后端用例 + README）/ `b2cb0af` refactor（双轴 review 收敛：文档同步 + app.js 清死代码 + 用例真实性）/ `651cc1a` docs（本文件与 PR body）/ 最后一笔 docs 只回填 PR 号 |
| 分支 | `studio-global-event-stream` 已推送 |
| PR | [#251](https://github.com/whutlichao/alibot/pull/251)，head = 本票最终提交 |
| issue | [#250](https://github.com/whutlichao/alibot/issues/250)（`ready-for-agent` 标签），PR body 带 `Closes #250` |

---

## 3. 决策与取舍

1. **`sse_frame` 一字不改，`id:` 行由路由摘掉**。票面同时要求「`sse_frame` 一字不改」与「全局流不写
   `id:` 行」，而 `sse_frame` 会给 `message` / `round_outcome` 写 `id: <seq>`。因此新增
   `api.live_sse_frame(event)`：拿到 `sse_frame` 的输出后**只摘掉**开头的 `id:` 行。上一层的语义
   （哪种事件带 id）仍只由 `sse_frame` 判定，没有第二份词表。
2. **全局流不发 `round.active` 快照**（票面明确取舍）。后果如实记下：**在有一轮 in-flight 时按 F5**，
   停止按钮要等下一帧 live 事件（`REPLY_START` / `round.active`）才回来，而不是像从前那样由
   per-target 流的连接快照立刻恢复。这条取舍是票面预定的（「全局流没有单一 target 的语义」），
   本票没有为此扩 scope；如果将来要补，正路是给工作台快照加一个 active-rounds 字段，而不是把
   `round.active` 塞回全局流。
3. **`loadApprovalsFor(targetId)` → `loadPendingApprovals({render:true})`**（只在重连成功那一次）。
   全局流断开 = **所有** target 都漏了帧，无法只补一个 target 的 pending；全工作区 pending 读数是
   既有端点、也是 `loadWorkspace` 的同一个写入口，是这里唯一诚实的兜底。
4. **`onerror` 清掉的是全部 target 的「正在回复」**（例外：任务 dock 自己的 target，它那条流还活着）。
   语义翻译：per-target 时代断哪条清哪条；现在一条流承载全部，断了就是全都断了。
5. **`state.streams` → `state.stream` 单例**，`closeStream(targetId)` 随之删掉（没有调用点了）。
   `lastSeqOf(targetId)` 保留并**接入 `markCurrentRead()`**（标读的上界），因为它是「我看到哪一条」的
   唯一表达式，`sse_events_ui.test.cjs` 也仍在用它取证；不删它、也不留死代码。
6. **测试替身里 `/api/dms` 与 `/api/channels` 必须返回真实形状**：非当前 target 的消息到达会触发一次
   未读重取，重取会把 `state.dms` 换成后端读数（未读的正本在库）。用例因此断言「游标推进 + 触发重取」，
   而不是断言本地 `last_seq` 镜像（后者会被后端读数覆盖，这是既有语义，不是本票改的）。
7. **`CONTEXT.md` 的「订阅」词条一并改**（review S1）：`docs/agents/review-rules.md` 判断题 6 把
   「新增端点时 `app/README.md` 与 `CONTEXT.md` 相关段落一起改」定为仓内标准，所以这不是 scope 扩张。
   改法最小：该词条补全两条订阅路径（per-target / 全局流），并把「带最新 `last_seq` 重连」限定到
   per-target 流——**没有**改 `_Avoid_` 行、也没有重写词条其余位置。
8. **任务 target 的帧两条流都在收**（review J3 / Spec P2）：全局流按票面要求「任意 target 都透传」，
   所以**不**在前端过滤任务 target；代价是运行面板的 live 队列记两次（渲染按 id 合并，不会出两行）
   与任务消息多一次 `GET /api/channels`。只在任务 dock 打开时发生。已在 README 与 PR body 写明。
9. **两个既有缺陷**本票**不**修，但在用例里写实：（a）`ALIBOT_SSE_ROTATE_SECONDS` 在 `alibot.api`
   导入期绑定，测试里 `monkeypatch.setenv` 对已导入模块无效（既有 rotate 用例也有这层局限）；
   （b）F5 时 in-flight 轮次的停止按钮恢复时机（见决策 2）。两者都要改产品代码的时机/载荷，属另票。

---

## 4. 遗留与观察（不阻断本票）

- **本地工具噪声**：本会话的 pi-lens 编辑钩子会把一条**陈旧缓存条目**（`[unused-ignore]`，锚点
  `app/alibot/events.py:383`）反复挂在「刚编辑的那个文件」下回灌：`events.py` 只有 238 行、零个
  `type: ignore`；该条目自身在 lens 缓存里已标 `stale: true / staleReason: "past-eof" /
  disposition: "false-positive"`；`lens_diagnostics(source=lsp, paths=[events.py])` 实测返回
  `clean=1, findings=0`。同一会话里它的 Pyright 也解析不到 `fastapi` / `httpx` / `agentscope`
  （LSP 未用仓库 `.venv`）。判据仍是 `scripts/check.sh` 用的那份 pyright / mypy
  （`pyrightconfig.json` + 仓库 `.venv`）：全项目 **0 errors**、mypy **123 source files clean**。
  这条噪声**没有**改动任何源码去「迎合」它。
- 服务端 `lsof -iTCP:8899 -sTCP:ESTABLISHED` 在修复后仍有 5 条连接——那是浏览器对普通 `fetch` 的
  HTTP/1.1 keep-alive 空闲连接，不是 SSE；判据要看「未结束的 `/events` 请求数」或 access log。
