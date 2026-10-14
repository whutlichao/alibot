## Summary

修掉事件流的一个 fail-fast 缺口：**作用域行（channel 型 `target` 影子行）缺失时，`/events` 先发 200、
再在补拉处抛 `NotFound`**，客户端只能拿到 200 + 0 字节。修法是把三类快照读从 `stream()` 内部提到
`StreamingResponse` 返回**之前**——headers 一旦送出，后面的 `DomainError` 就再也换不成状态码了。

```text
channel_events(channel_id, after_seq)
  database.get_channel(channel_id)                      # 404 早退（不变）

  # ↓ 本票的改动：三类快照读前置到响应开始之前
  snapshot_messages = service.channel_messages(...)      # 原来在 stream() 里
  snapshot_outcomes = service.round_outcomes(...)        # 原来在 stream() 里
  snapshot_statuses = orchestrator.member_statuses(...)  # 原来在 stream() 里

  return StreamingResponse(stream(), ...)                # headers(200) 在此送出

stream()                                                 # 只消费快照，不再读库
  yield 消息 → 轮次结果 → 状态点快照                      # 顺序、游标、去重边界一字未动
  with events.subscribe(channel_id) as channel:          # 订阅点未移动
    … 实时推送 / keep-alive / rotate
```

**根因**（不是 UI 删除）：`app/alibot` 里**没有**删除频道或 target 的代码路径
（全仓无 `DELETE FROM` / `DROP TABLE`）。真实触发是**旧库被新代码打开**——`db.Database._initialize()`
只做 `CREATE TABLE IF NOT EXISTS` 与版本号 upsert，**不比对、不拒绝**：

```text
v4 代码打开 v3 库（首版 49b8973^）
  ├─ channel 表原样留下（数据还在）→ GET /api/channels 照旧 200，列表里能看到这个频道
  └─ target 影子行不会被补        → 点进去的每个端点都在读写那一刻撞上不一致
       └─ /events 的症状最糟：补拉在 stream() 里，于是先发 200 再抛错
```

本地旧库只读取证确认了这个形态：`channel` 有多行，而 `target` 缺少至少一个频道的 channel 型
影子行（旧表 `channel_member` / `channel_read` 未搬）。与 issue **#37**
（v4 打开 v3 库静默半改名）记录的形态一致。#37 修「启动拒绝 + 修复入口」；本票修同一个数据不一致
在事件流端点上的**表现形式**，两者不重叠——存量 v4 库（本地就是）在 #37 落地后仍带着这个不一致。

```diff
 app/alibot/api.py              # +23 / −6：channel_events 里三类快照读前置
 app/tests/test_event_stream.py # +69：新增 helper + 1 条回归用例
 app/specs/fix-sse-target-fail-fast-acceptance.md  # Answer
 app/specs/fix-sse-target-fail-fast-pr-body.md     # 本文件
```

**刻意未动**（票面 Constraint）：`db.py` / `service.py` / `events.py` / `orchestration.py` 一字未改；
`db` 的通用 404 语义不动；补拉顺序、游标去重、`subscribe` 时序、正常流行为全部保持。
**没有加任何 `try/except`**——「中途删除竞态」这条假设已用静态不一致库证伪（无并发写也复现），
不为假设性路径扩 scope；流内其他异常照旧向上抛，不吞。

## Evidence

**Before**（base `6d4e6f8` 的 `api.py` 单独还原）｜**After**（本分支）

装置级回路（**真 uvicorn + 真 socket**，同一份 v3 形态库）：

| | Before | After |
| --- | --- | --- |
| `GET /api/channels` | 200（列表正常） | 200（不变） |
| `GET …/messages` | 404 | 404（不变） |
| `GET …/events` | **200** 后断流，`bytes received: b''` | **404** + `{"error":{"code":"not_found",…}}` |
| 服务端 | `NotFound: target 不存在：ch_…` → `RuntimeError: Caught handled exception, but response already started.` | 无异常 |

修复前的确切断栈（`api.py` 行号为 base 版本）：

```text
File ".../starlette/responses.py", line 253, in stream_response
File ".../alibot/api.py", line 343, in stream
  snapshot = service.channel_messages(channel_id, after_seq=after_seq)
File ".../alibot/service.py", line 101, in channel_messages
File ".../alibot/db.py", line 794, in list_messages
  self.get_target(target_id)  # 404 早退
File ".../alibot/db.py", line 622, in get_target
  raise NotFound(f"target 不存在：{target_id}")
...
  raise RuntimeError("Caught handled exception, but response already started.") from exc
```

回归测试（进仓、快、确定）：

| 命令 | Before（base `6d4e6f8` 的 `api.py`） | After（本分支） |
| --- | --- | --- |
| `pytest tests/test_event_stream.py -k missing -q` | **FAILED**（`RuntimeError`） | **1 passed** |
| `pytest`（全量） | **未实测**（baseline 全量未跑，不作为事实） | **197 passed, 1 skipped**（collect: 198，155.34s） |
| `ruff check app` / `format --check app` | — | **All checks passed!** / **63 files already formatted** |
| `mypy`（仓库根） / `pyright app/alibot app/tests` | — | **28 files 干净** / **0 errors, 0 warnings, 0 informations** |

说明：**baseline 全量从未跑过**，因此本表不声称它的 pass/collect 数；Before 一侧只列已实测的
**定点回归红**（未修复 `api.py` 时 `RuntimeError`）。修复后的全量 `197 passed, 1 skipped`
（collect 198，155.34s）是本票修复提交时期已取得的实测输出（命令 `.venv/bin/python -m pytest`，exit=0）；
本 follow-up 只改文档/注释，**未重跑全量**，定点测试本次实测 exit=0。

新增用例先断言该频道在正常形态下**可订阅**（排除恒真假绿），再毒化（`target` 影子行删除，**双向
自校验** channel 行在 / target 行缺），最后断言 404。修复后装置级回路同库复跑亦转绿。

## Merge Danger

**Door:** two-way — 单函数内读操作位置的调整，无数据迁移、无 schema 变更、无接口变更、无依赖变更。回滚只需 revert 这一个 commit。

**Blast Radius:** narrow

- 影响面**只有** `/api/channels/{id}/events` 的失败路径：正常流（补拉顺序、游标去重、keep-alive、
  `rotate` 轮换、实时推送、typing/状态点）行为一字未变，既有 17 条 SSE 用例未修改全部通过。
- **唯一需留意的时序点**：三类快照读现在在返回响应之前执行，与修复前完全同序（它们本来就在 `stream()`
  的开头、`subscribe` 之前），订阅点未移动 ⇒ 不引入新的丢事件窗口。
- 真实用户可见的**剩余风险**：库里的不一致**本身**没有被修复。事件流现在如实报 404，用户看到的是
  「频道 404」而不是「内容恢复」。恢复归属 **#37** 的启动拒绝 + 手动修复入口，本票不越 scope；
  本地旧库里缺影子行的频道在该入口执行前仍会 404（诚实表现，非遗漏）。
- 本地旧库 live 数据目录**全程只读**：所有实验与测试在临时副本与 `tmp_path` 上做，未执行任何写入。
