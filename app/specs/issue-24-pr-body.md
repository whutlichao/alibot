## Summary

把**消息作用域**从「恒为一个频道」升为 `target(id, kind ∈ {channel, dm, task})`，让消息、已读位置、
回应预算、轮次结果四者全部挂到它上面。这是一个 **prefactor 票**：它不加新功能，但把"加新能力"从
"改三张表的语义"变成"插一行 target"。

```diff
 schema（SCHEMA_VERSION 3 → 4，不写迁移脚本）
+ target(id, kind CHECK IN ('channel','dm','task'), created_at)
- channel_member(channel_id, member_id)
+ target_member(target_id, member_id)
- channel_read(member_id, channel_id, last_read_seq)
+ target_read(member_id, target_id, last_read_seq)
- channel_budget(channel_id, epoch_seq, used)
+ target_budget(target_id, epoch_seq, used)
  message(id, target_id → REFERENCES target(id), seq, …)     # 加引用约束；UNIQUE(target_id,seq) 不变
  round_outcome(…, target_id)                                # 本来就叫对了，不动
```

**channel 型 target 复用 `channel.id`**：同一个字符串既是频道主键也是 target 主键，`target` 行对
channel 型是一张**影子行**（id 同值、只承载 `kind`）。频道的 `name` / `description` 仍留在 `channel`
表——**不引入映射表**。既有的 `m.target_id = c.id` 因此逐字不变，升级不换任何一个消息的归属。

```text
写路径（频道面入口不变，只换内部作用域键）
  create_channel          → channel 行 + target 影子行 + target_member + target_read   （同一事务）
  append_message(target_id, …)  → 分配 **target 内**单调 seq；kind='human' 时 target_budget 开新世代
  add_target_member       → target_member 一行（幂等）+ 补 target_read
  mark_read(target_id, …) → target_read 一行，同一 target 内取 max、裁剪到当前最大 seq
  notify / _wake_targets  → 唤醒面读 target_member（同一 target_id 键，**不按 kind 分支**）
  _spend_jump             → target_budget.used +1（跳数唯一正本；**每 target 一份**）

读路径
  CHANNEL_SELECT_SQL      → unread_count 与 target_read 同一条 WHERE 形状
  UNREAD_TOTAL_SQL        → unread_total = channel 型 + dm 型之和；**task 型不进它**
```

未读计数**只有一个 WHERE 形状**（已读位置之后、`kind ∈ {human,agent}`、作者非本人、只算顶层），
三种 kind 共用；预算是**每 target 一份**、上限仍恒为 3（`BUDGET_LIMIT` 未改值、未配置化——配置化归 #31）。

改动文件：`app/alibot/{db,service,orchestration,api}.py`（api 只换一处内部调用名、**路径/参数/响应字段名零变化**）、
`app/tests/test_target_scope.py`（+15 条）、`app/specs/issue-24-{acceptance,pr-body}.md`。
前端 `app/alibot/studio/` **一行未改**（外部契约未变）。

## Evidence

**外部契约零漂移**（本票是承重重构，这是核心证据）：一个契约探针把每个端点的响应化简成「键集合 + 类型」，
在 base `9fd31bb` 与 head 上各跑一次，输出 diff **为空**：

```text
$ diff <(sed -E 's/mem_[0-9a-f]+/MEMID/g' base.txt) <(sed -E 's/mem_[0-9a-f]+/MEMID/g' head.txt)
                                            # ← 无输出
*** IDENTICAL: 30+ 端点的状态码/顶层键/嵌套键/类型逐字节相同 ***
```

覆盖 `/api/health`、`/api/workspace`、`/api/channels`（GET/POST）、`/api/agents`（列表/单条/状态）、
`/api/member-statuses`、`/api/remote-data-flow`（读/确认）、`/messages`（GET/POST）、`/members`（GET/POST）、
`/budget`、`/round-outcomes`、`/events`、`/read`，以及错误形状（404/400）与**跑完一轮后**的形状。
`budget` 载荷里的键名仍是 `channel_id`、`unread_total` / `last_seq` / `last_read_seq` / `unread_count` 全部不变。

**Before**（基线 `9fd31bb`）：

```text
$ .venv/bin/python -m pytest
179 passed, 1 skipped in 95.44s
$ git show 9fd31bb:app/alibot/db.py | grep -E "^SCHEMA_VERSION|^BUDGET_LIMIT"
SCHEMA_VERSION = 3
BUDGET_LIMIT = 3
```

**After**（本 PR，门禁四条全跑）：

```text
$ .venv/bin/python -m pytest
194 passed, 1 skipped in 95.28s        # 既有 179 条**未经修改**通过（+15 条新增）

$ git diff --name-only 9fd31bb...HEAD -- app/tests/
app/tests/test_target_scope.py        # 唯一一条，且是新文件（零条既有用例被改）

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! · 52 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 27 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
0 errors, 0 warnings, 0 informations
```

**端到端冒烟**（真实 uvicorn 进程 + 真实库文件，数据目录 `/tmp/issue24-smoke`）：

```text
$ curl -s http://127.0.0.1:58857/api/health
{"status":"ok","member_id":"owner","data_dir":"/tmp/issue24-smoke"}
$ curl -s -X POST http://127.0.0.1:58857/api/channels -d '{"name":"冒烟频道","description":"e2e"}'
{"channel":{"id":"ch_d984b5f98035",…,"last_seq":0,"last_read_seq":0,"unread_count":0}}
$ curl -s -X POST http://127.0.0.1:58857/api/channels/ch_d984b5f98035/messages -d '{"body":"冒烟一条"}'
{"message":{"id":"msg_39e77f1260a6","target_id":"ch_d984b5f98035","seq":1,"kind":"human",…}}

库文件三张表存在（票面验收项）：
['channel','message','round_outcome','schema_meta','target','target_budget','target_member','target_read']
schema_version = 4 ; target = [('ch_d984b5f98035','channel')]   # 影子行 id == 频道 id
legacy channel_* tables present? []                             # 旧表名已不存在
```

**新增数据面用例**（15 条，逐条读数见 `app/specs/issue-24-acceptance.md` §4）：
`target` 表 + `kind` CHECK 约束 + **无映射表**；影子行 id 同值、`name`/`description` 仍在 `channel` 表；
`message.target_id` 引用约束生效；三种 kind 的 seq **各自独立单调**（`[1,2]` / `[1]` / `[1,2]`）；
**跨 target 不锁死**（频道 A 烧满 3 跳后 B 的往返仍可用，`target_budget` 里 A/B 各一行；**跨 kind**
的那一半另有一条：channel 烧满后 dm 型仍保留自己的世代与用量）；`unread_total` = channel + dm、task 不进
（channel 与 dm **两侧都非零**时断言，且 task 自己的读数仍在）；dm/task 型 target 经频道面一律 404；
三种 kind 的成员面/唤醒面查询走**同一个键、不分 kind**；append-only 触发器仍在（先真跑一轮填出行、
再断言 UPDATE/DELETE 被拒）；`SCHEMA_VERSION` 已 bump、无迁移脚本；未读「只有一个 SQL 形状」由一条
**结构断言**钉住。

**双轴 code-review**：Standards + Spec 并行子代理（固定点 `9fd31bb`）。两轴均**无 P0/P1、无文档标准的硬违规**，
共 13 条 findings、**8 条已修**（死常量/悬空引用/注释错位/测试脆性/`mark_read` 的 kind 分支/测试覆盖缺口）、
2 条记遗留（见上）。Standards 轴专门核了「承重重构有没有把外部可观察行为带偏」——
**四点逐条「保持」**（HTTP 路径/参数/响应字段名；未读口径；预算上限恒为 3；唤醒面不按 kind 分支），
机械证据就是上文的契约探针 diff 为空。逐条处置见 `app/specs/issue-24-acceptance.md` §6。

## Merge Danger

**Door:** one-way

`SCHEMA_VERSION` 3 → 4 且**不写迁移脚本**（ADR-0004 决策 8 / 规格决策 8）：第二版**不打开**首版的库，
从空库开始。这不是回滚一条 commit 就能撤销的数据决定——一旦在这个版本上产生了数据，退回首版代码
就读不了（旧版按 `channel_read` 等旧表名查询）。合并前请确认「本机数据可以丢弃 / 从空库开始」这个前提成立
（本仓自身没有删除频道的入口，影子行也没有删除路径）。

**Blast Radius:** wide

改动面是**整个持久化作用域键**：四张表改名 + 一张新表 + 一条外键约束 + 未读/预算两条 SQL 的形状收敛，
涉及 `db` / `service` / `orchestration` / `api` 四个模块。影响面之所以可控，靠的是两条闸门：

1. **外部契约逐字节未变**（§Evidence 的契约探针 diff 为空）——浏览器、既有测试、任何脚本调用方零调整；
2. **既有 179 条用例未经修改全部通过**，且 `app/tests/` 的 diff 只含新增文件。

**已知遗留 / 有意留给下游**（细节在 `app/specs/issue-24-acceptance.md` §6.3）：

- 本票**只**让 schema 与查询路径**允许**第二、第三种 kind——dm / task 的**业务逻辑**（幂等创建、
  两行成员、穿透一次性送达、任务五态看板、互审、唤醒规则）一律留给 **#25–#28**，属主路径上没有任何
  dm/task 分支（`create_target` 只插一行 target、不做任何业务校验，docstring 已明标本票无生产调用点）；
- `BUDGET_LIMIT` 的**配置化**留给 **#31**（本票不改它的值、不读任何配置）；
- 未读口径按 G5 已实现为「channel + dm、task 不进」，但 dm / task 的真实数据由后续票产生——本票用
  最小构造证明口径成立，不新增任何产生 dm/task 数据的端点；
- **`app/README.md` 的 schema 表仍列旧表名**（`channel_member` / `channel_read` / `channel_budget`）：
  该文件**不在本票 ownership**（票面 Ownership 只给 `app/alibot/`、`app/tests/`、`app/specs/issue-24-*.md`），
  且 README 正文同时描述了首版预算机制，属文档层重写；建议由后续文档票（或 #25 开工时）一并改为 `target_*`；
- **旧库版本检测**：`_initialize` 只 upsert `schema_version`，不**拒绝**旧版本库。决策 8 只要求「不做迁移」，
  未要求「拒绝打开」；若希望 v3 库被显式拒绝（而不是半改名状态下静默可用），需产品裁定。
