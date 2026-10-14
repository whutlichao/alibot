# 验收取证：事件流在作用域行缺失时先发 200、再于补拉处抛 `NotFound`

**票型**：实施票（缺陷修复 + 回归测试）｜**门禁**：G-impl、G-merge（合并由 coordinator 执行）
**Ticket**：本票**没有**对应的 GitHub issue，也**不复用 #37**——用户裁定：不新建 issue、不复用 #37、
交付件用描述性命名（`app/specs/fix-sse-target-fail-fast-*`），Answer 与 PR body 不带 issue 关联。
**base**：`6d4e6f8`（main HEAD = 本票 worktree 的起点 commit）｜**branch**：`fix-sse-missing-target`｜**PR**：[#39](https://github.com/whutlichao/alibot/pull/39)
**采集时间**：2026-10-14｜**环境**：macOS（aarch64），Python 3.11.13（仓库根 `.venv`）
**改动面**：`app/alibot/api.py`（+23 / −6）、`app/tests/test_event_stream.py`（+69）

### 历史重写与脱敏（用户第二次裁定）

用户明确授权：**对本未合并 PR 的单提交历史进行重写**，移除公开可见的本机标识。本次重写**已执行**：
`0ad6024` 已被 amend 为重写后的提交（同一 PR #39、同一分支、仍为单一提交），并以精确 lease 的
`git push --force-with-lease=refs/heads/fix-sse-missing-target:0ad6024` 推送成功——lease 锁定重写前的
旧 SHA，只有远端仍停在该 SHA 时才允许覆盖（避免覆盖他人更新）；推送后已核验远端分支与 PR #39
head 均指向本地新提交（新 SHA 以远端分支为准，本文不写死以避免自指漂移）。
本次重写**只改文档与注释**，不改 `app/alibot/api.py` 的运行逻辑（重写前后 `api.py` 逐字节相同）。
脱敏面：两个公开文档与 Python 注释中的本机绝对路径（用户主目录与数据目录）与本机真实频道名/ID、
数据库行数、以及合成红测输出里的固定 ID，均已替换为不带个人数据的泛化表述与占位符
（如 `ch_<redacted>`、`<isolated-temp-dir>`）。

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。红证据在 base commit 的
**隔离条件**下取得（把 `api.py` 单独还原成 base 版本，其余工作区不动），**不在** live 数据目录做任何写入。

---

## 0. 前置 skill 执行证据（`diagnosing-bugs`）

前置 skill：`.agents/skills/diagnosing-bugs/SKILL.md`（仓库外的全局 skill），六阶段逐步实跑。

| Phase | 要求 | 本票实际做的事 |
| --- | --- | --- |
| 1 构造反馈回路 | 一条能**在本 bug 上变红**、确定、快、可无人值守运行的命令 | 两级回路。**装置级**（最贴近事故）：`<isolated-temp-dir>/sse-red-repro/red_stream.py <data_dir>`，真 uvicorn + 真 socket，读 `/api/channels` → 点进去的事件流。**回归级**（进仓、快）：`pytest tests/test_event_stream.py -k missing -q`。两者首跑即红，见 §2.1 |
| 1 收紧回路 | 更快、信号更锐、更确定 | 回归级把「先发 200 再断流」压成**一个状态码断言 + 一个错误码断言**，单用例 ~1s；构造形态的 helper 自带**双向自校验**（channel 行在、target 行缺），排除「退化成频道不存在」的假绿 |
| 2 复现 + 最小化 | 症状与用户描述一致；逐项砍到每一步都 load-bearing | 症状与日志逐字一致（`NotFound: target 不存在：…` → `RuntimeError: Caught handled exception, but response already started`）。最小化见 §2.2 |
| 3 假设 | 先列 3–5 条可证伪假设再验证 | §2.3，5 条，逐条给了证伪预测与实测结论 |
| 4 仪器化 | 每个探针映射到一条具体预测；一次只改一个变量 | 探针 = 真 uvicorn 的**逐帧 stderr 堆栈**（`uvicorn log_level=info`）+ 客户端收到的**字节数**。两条信号分别证伪「HTTP 层就 404 了」与「流被正常关闭」。清理见 §5.1（全仓 grep 无 `[DEBUG-` 前缀、无 `print(` 留存） |
| 5 修复 + 回归测试 | 先写测试看它红，再修，再看它绿 | TDD 顺序见 §3；红/绿证据见 §4 |
| 6 清理 | 原始回路不再复现；回归测试通过；插桩清除；一次性原型删除或标记 | §5：原始装置级回路转绿；回归测试绿；无插桩；临时目录下的一次性原型**明确标记为不入仓** |

### 0.2 与票面/前置 skill 的冲突处置（按目标行为最小修改并报告）

票面 Target 段写「确保**未存在频道**在响应发出前正确返回 404」，这把触发面圈在「频道不存在」。
实测与真机取证表明：**频道不存在那条路径本来就正确**（`database.get_channel` 在 `StreamingResponse`
之前就早退，已有用例 `test_unknown_channel_event_stream_is_not_found` 覆盖）。真正会先发 200 再抛错的
是**「channel 行在、channel 型 target 影子行缺失」**——本地旧库只读取证确认的形态。两件事的修法在
实现上是同一个（把三类快照读前置到 `StreamingResponse` 之前），但**回归测试必须钉住后者**，否则用例恒绿。
处置：按目标行为（响应前 404）最小修改，并把真正触发的那条路径写成回归测试；**如实报告**这一差异。

前置 skill 与技术事实无冲突：`diagnosing-bugs` 的 Phase 1「先造回路、不许先立假设」正是本票的做法。

---

## 1. 根因

`app/alibot/api.py` 的 `channel_events` 在返回 `StreamingResponse` **之前**只做了一次
`database.get_channel(channel_id)` 早退，而**补拉用的三类读全部写在 `stream()` 里**：

```text
channel_events(channel_id, after_seq)
  database.get_channel(channel_id)  # 404 早退（只校验 channel 行）

async def stream():
    snapshot = service.channel_messages(channel_id, after_seq=after_seq)   # ← 这里才碰 target 行
    ...
return StreamingResponse(stream(), ...)   # headers(200) 已在此刻送出
```

`StreamingResponse` 的 headers 在 `stream()` 首次迭代**之前**就已发出。于是 `stream()` 里任何
`DomainError` 都**再也换不成状态码**——Starlette 的 `_exception_handler` 只能报
`RuntimeError: Caught handled exception, but response already started`，客户端拿到 **200 + 0 字节** 后断流。

**触发时序不是 UI 删除操作**（票面要求调查，不假设）。`app/alibot` 里**没有**删除频道或删除 target
的代码路径，也没有任何 `DELETE FROM` / `DROP TABLE`（`grep` 全仓确认；仅 `test_target_scope.py` 里有
测试自己的触发器取证）。真实触发是**旧库被新代码打开**：

1. `db.Database._initialize()` 只执行 `executescript(SCHEMA)`（全是 `CREATE TABLE IF NOT EXISTS`）与
   `INSERT INTO schema_meta('schema_version') … ON CONFLICT DO UPDATE`；**不读旧版本、不比对、不拒绝**。
2. 用 v4 代码打开一个 **v3 库**（首版 `49b8973^`）时：`channel` 表被**原样留下**（数据还在），而承载
   作用域的 `target` 影子行**不会被补**——v3 的成员关系在 `channel_member` / `channel_read` 里，
   代码已不再读它们（改名成 `target_member` / `target_read`）。
3. 结果：`GET /api/channels` **照旧 200**（它只查 `channel` 表，列表里能看到这个频道），但点进去的每个
   端点都在读写那一刻撞上「channel 行在、target 行不在」的不一致。事件流的症状最糟——它先发 200 再抛错。

**本地旧库（只读取证）实测正是这个形态**（未写入）：

| 观察 | 实测形态 |
| --- | --- |
| `schema_meta.schema_version` | `4`（已被 v4 代码静默改写） |
| `channel` | 多行（多个频道） |
| `target` | **缺行**——至少一个频道的 channel 型影子行**缺失** |
| `message` | 有行（历史消息仍在） |
| `channel_member` / `target_member` | 均有行，但分属不同频道（旧表未搬） |

这与 GitHub issue **#37**（`v4 代码打开 v3 旧库会静默半改名`）记录的形态**逐项一致**。#37 处置的是
「启动时拒绝旧库 + 手动修复入口」；**本票处置的是同一个数据不一致在事件流端点上的表现形式**（先发 200
再抛错），两者不重叠、也不互相替代。#37 的自动拒绝与修复入口落地后，仍会有存量 v4 库带着这种
不一致（本地就是），所以事件流仍然必须能把它变成一个**正常的 404**，而不是 200 + 0 字节。

---

## 2. `diagnosing-bugs` Phase 1–4 取证

### 2.1 Phase 1：反馈回路与红证据

**装置级回路**（最贴近事故，真 uvicorn + 真 socket）。用 **v3 真实代码**（`git worktree add <isolated-temp-dir>/v3-base 49b8973^`）建库并写数据，再用 v4 代码打开一次（复现静默半改名），形态因此与本地旧库同形：

```sh
$ .venv/bin/python <isolated-temp-dir>/sse-red-repro/build_v3_db.py <isolated-temp-dir>/sse-red-repro/v3data
v3 db: <isolated-temp-dir>/sse-red-repro/v3data channel ch_<redacted> agent mem_<redacted>
$ .venv/bin/python -c "from alibot.db import Database; Database('<isolated-temp-dir>/sse-red-repro/poisoned').close()"
$ .venv/bin/python <isolated-temp-dir>/sse-red-repro/red_stream.py <isolated-temp-dir>/sse-red-repro/poisoned
```

**修复前（红）**——逐帧堆栈证明「先发 200、再于补拉处抛错」：

```text
[1] GET /api/channels                     -> 200
    channels: [('ch_<redacted>', '<name>', 1)]
[2] GET /api/channels/ch_<redacted>/messages   -> 404
[3] GET /api/channels/ch_<redacted>/events     -> 200 (headers sent)
    stream aborted mid-flight: RemoteProtocolError: peer closed connection without sending
    complete message body (incomplete chunked read)
    bytes received: b''
```

服务端同一时刻的堆栈（`uvicorn` stderr，节选关键帧）：

```text
  File ".../starlette/responses.py", line 253, in stream_response
  File ".../alibot/api.py", line 343, in stream
    snapshot = service.channel_messages(channel_id, after_seq=after_seq)
  File ".../alibot/service.py", line 101, in channel_messages
    return self._db.list_messages(channel_id, after_seq=after_seq)
  File ".../alibot/db.py", line 794, in list_messages
    self.get_target(target_id)  # 404 早退
  File ".../alibot/db.py", line 622, in get_target
    raise NotFound(f"target 不存在：{target_id}")
alibot.db.NotFound: target 不存在：ch_<redacted>
...
  File ".../starlette/_exception_handler.py", line 56, in wrapped_app
    raise RuntimeError("Caught handled exception, but response already started.") from exc
RuntimeError: Caught handled exception, but response already started.
```

**回归级回路**（进仓，同一缺陷，快且确定）——把 `api.py` 单独还原成 base 版本，新用例即红：

```sh
$ .venv/bin/python -m pytest tests/test_event_stream.py -k missing -q
```

```text
            if response_started:
>               raise RuntimeError("Caught handled exception, but response already started.") from exc
E               RuntimeError: Caught handled exception, but response already started.

../../../../<venv>/lib/python3.11/site-packages/starlette/_exception_handler.py:56: RuntimeError
=========================== short test summary info ============================
FAILED tests/test_event_stream.py::test_event_stream_of_a_channel_whose_target_row_is_missing
```

回路检查单：**红capable** ✅（旧代码上失败，修复后转绿，钉住的是「响应前 404」这个确切症状）；
**确定** ✅（重复 3 次同判）；**快** ✅（单用例 ~1s）；**可无人值守** ✅（pytest，无人工步骤）。

### 2.2 Phase 2：最小化（每一步都 load-bearing）

| 元素 | 砍掉后会怎样 | 结论 |
| --- | --- | --- |
| **channel 行在而 target 行缺** | 只剩「频道不存在」→ `get_channel` 直接 404，用例恒绿 | **load-bearing**（唯一触发条件） |
| 该频道**至少一条消息** | 消息表为空时 `list_messages` 仍会先 `get_target`，所以仍会抛；但留一条让「补拉真有内容可发」这一语义成立 | 保留（增强真实性，非严格必需） |
| `target_member` / `target_read` 行 | 只删 `target` 行、留着 `target_member`（外键悬空）时，`list_messages` 一样在 `get_target` 抛。删除它们只为精确复刻旧库形态 | 保留（形态保真） |
| 真 uvicorn + 真 socket（**仅装置级**） | 换成 `TestClient` 读不到「200 后断流」——`TestClient` 会把响应体读完 | 装置级 load-bearing；**回归测试**用 `TestClient` 即可（`RuntimeError` 直接抛出，判据更强） |
| `GET /api/channels` 仍 200 | 这**不是**去掉的一步，而是「列表页看着正常」这个症状的对照组 | 断言它，证明不一致是「局部失效」而非「整库坏掉」 |

### 2.3 Phase 3：五条可证伪假设（先列后验，逐条给预测与结论）

| # | 假设 | 证伪预测 | 实测结论 |
| --- | --- | --- | --- |
| 1 | **目标行缺失**导致 `stream()` 内补拉抛 `NotFound`（`channel_messages` → `get_target`） | 把三类快照读前置到 `StreamingResponse` 之前 ⇒ 应变成 404，且不再有 `RuntimeError` | **成立**（§4 绿证据；堆栈逐帧对上） |
| 2 | HTTP 层已经 404 了，客户端读到的错是别的问题 | 若成立，响应状态码应已是 404、stderr 不应有 `response already started` | **否**：状态码 200，且 `RuntimeError` 就出在 Starlette 的 `response_started` 分支 |
| 3 | 是 UI **删除频道/成员**后的竞态（票面提醒不要假设） | 若成立，仓里应有删除路径（`DELETE FROM` / 删除端点） | **否**：全仓无删除路径；`stream()` 里唯一会抛的读就是补拉三类 |
| 4 | 是并发删除与订阅之间的**中途删除竞态** | 若成立，用一个不删除任何东西的静态不一致库应**无法**复现 | **否**：静态库（无任何并发写）稳定复现 ⇒ 不是竞态。因此**不加**任何「窄捕获」 |
| 5 | 只是 `get_channel` 与 `get_target` 的**校验不对齐**（前者查 channel 表、后者查 target 表） | 若成立，前置任意一类读即可；只前置消息读应仍可能半路失败 | **部分成立**：不对齐确实是根因；但**只前置消息不够**——轮次结果/成员快照仍会在流中途抛，故三类读都前置 |

排序依据：假设 1 直接解释堆栈，假设 3/4 是票面点名要不假设的方向，优先证伪。

### 2.4 Phase 4：仪器化

探针即真 uvicorn 的 **stderr 堆栈**（`log_level=info`）与客户端**收到的字节数**：

- 堆栈 → 映射假设 1/2：`response_started` 分支与 `get_target` 帧同时出现 ⇒ 假设 2 假、假设 1 真。
- 字节数 `b''` → 映射假设 4：无任何并发写也复现 ⇒ 不是竞态。

**一次只改一个变量**：先只加「消息读前置」（假设 5 的探针），确认仍会漏 → 再把三类读一起前置。
无 `[DEBUG-...]` 前缀留存（§5.1）。

---

## 3. Phase 5：修复 + 回归测试（TDD 顺序）

1. **先写用例**：`test_event_stream_of_a_channel_whose_target_row_is_missing` + helper `_poisoned_target`。
2. **看它红**：§2.1 的回归级红证据（`RuntimeError`），且 `diff` 证明我只动了 `api.py` 那一处（§4）。
3. **再改实现**：三类快照读前置到 `StreamingResponse` 之前。
4. **看它绿**：§4。
5. **原始（未最小化）回路复跑**：§5.1。

### 3.1 修复

```diff
 app/alibot/api.py  channel_events
   database.get_channel(channel_id)  # 404 早退
+  # 快照在响应开始之前读完：headers(200) 一旦送出，后面的 DomainError 换不成状态码
+  snapshot_messages = service.channel_messages(channel_id, after_seq=after_seq)
+  snapshot_outcomes = service.round_outcomes(channel_id, after_seq=after_seq)
+  snapshot_statuses = orchestrator.member_statuses(
+      [member["id"] for member in database.list_target_agent_members(channel_id)]
+  )

   async def stream():
-      snapshot = service.channel_messages(channel_id, after_seq=after_seq)
-      for message in snapshot: yield sse_frame({...})
-      for outcome in service.round_outcomes(channel_id, after_seq=after_seq): yield sse_frame({...})
-      for member_id, status in orchestrator.member_statuses(...).items(): yield sse_frame({...})
+      for message in snapshot_messages: yield sse_frame({...})
+      for outcome in snapshot_outcomes: yield sse_frame({...})
+      for member_id, status in snapshot_statuses.items(): yield sse_frame({...})
       with events.subscribe(channel_id) as channel:   # 顺序不变
```

**保持既有行为（逐条对照票面 Constraint）**：

- **补拉顺序不变**：消息 → 轮次结果 → 状态点快照，逐类逐条，顺序与内容一字未动。
- **游标去重与边界不变**：`if int(event.get("seq", 0)) < after_seq: continue` 未动；快照仍读
  `seq > after_seq`（`channel_messages(after_seq=…)` / `round_outcomes(after_seq=…)`）。
- **`with events.subscribe(...)` 仍在快照之后**：订阅点未移动 ⇒ 「补拉与实时推送之间不留缝隙/不重复」
  这条时序前提不变（快照先读完、再订阅、再推实时）。**这是本修复唯一需要留意的时序点**：
  前置的三类读现在发生在订阅**之前**，与修复前完全同序（它们本来就在 `stream()` 的开头、订阅之前），
  因此没有引入新的丢事件窗口。
- **正常流行为不变**：`test_rotate_frame_...`（轮换帧）、`test_typing_indicator_...`、
  各条 `test_event_stream_replays_*` / `test_replay_does_not_duplicate_or_reorder` 全部照旧通过。
- **不改 DB 通用 404 语义**：`db.get_target` / `get_channel` 的 `NotFound` 与状态码未动；本修复只是把
  已有的 `DomainError` 放到「还能换成状态码」的位置。
- **不加假设性窄捕获**：假设 4 已证伪「不是中途删除竞态」，因此**没有**任何 `try/except` 包裹；
  不吞其他异常——流内其他异常照旧向上抛（行为与修复前一致）。

### 3.2 回归测试

`app/tests/test_event_stream.py` 新增：

- `_poisoned_target(data_dir, channel_id)`：把该频道变成「channel 行在、target 行缺」，**双向自校验**
  （channel 行在 ✅ / target 行缺 ✅），避免退化成「频道不存在」那条既有路径。
- `test_event_stream_of_a_channel_whose_target_row_is_missing`：先断言该频道在正常形态下**可订阅**
  （排除恒真假绿），再毒化，再断言 **404** 且 `error.code == "not_found"`。

---

## 4. 红 → 绿证据

| 命令 | base（未修复的 `api.py`） | 修复后（本分支） |
| --- | --- | --- |
| `pytest tests/test_event_stream.py -k missing -q` | **FAILED** — `RuntimeError: Caught handled exception, but response already started.` | **1 passed** |
| `pytest`（全量） | **未实测**（本 follow-up 不重跑全量；baseline 全量数字不作为事实） | **197 passed, 1 skipped**（collect: 198） |
| `ruff check app` | — | **All checks passed!** |
| `ruff format --check app` | — | **63 files already formatted** |
| `mypy`（仓库根 cwd） | — | **Success: no issues found in 28 source files** |
| `pyright app/alibot app/tests` | — | **0 errors, 0 warnings, 0 informations** |

说明：**修复后全量**的 `197 passed, 1 skipped`（155.34s，collect 198）是本票修复提交时期已取得的实测输出
（命令：`.venv/bin/python -m pytest`，仓库根 cwd，exit=0）。**baseline 全量从未跑过**，因此本表不声称
baseline 的全量 pass/collect 数；base 一侧只报告已实测的**定点回归红**（未修复 `api.py` 时 `RuntimeError`）。
本 follow-up 只改文档/注释，**未重跑全量**（票面约束）；定点测试本次实测 exit=0：

```sh
$ .venv/bin/python -m pytest tests/test_event_stream.py -k missing -q
$
```

修复后的装置级回路（**同一份** v3 形态库、真 uvicorn、真 socket）：

```text
[1] GET /api/channels                     -> 200
    channels: [('ch_<redacted>', '<name>', 1)]
[2] GET /api/channels/ch_<redacted>/messages   -> 404
[3] GET /api/channels/ch_<redacted>/events     -> 404 (headers sent)
    bytes received: b'{"error":{"code":"not_found","message":"target \xe4\xb8\x8d\xe5\xad\x98\xe5\x9c\xa8\xef\xbc\x9ach_<redacted>"}}'
```

即：**404 + 统一错误封装**，`RuntimeError` 与「200 + 0 字节」双双消失。

---

## 5. `diagnosing-bugs` Phase 6：清理

- [x] 原始回路不再复现（§4 的装置级复跑）
- [x] 回归测试通过（§4）
- [x] `[DEBUG-...]` 插桩全部移除：`grep -rn "DEBUG-\|print(" app/alibot/api.py app/tests/test_event_stream.py` → 无输出
- [x] 一次性原型**不入仓**（全部在临时目录下，已明确标记）：`<isolated-temp-dir>/sse-red-repro/`（`make_db.py` /
      `build_v3_db.py` / `red_stream.py` / `v3-base` worktree / `v3data` / `poisoned`）。
      另：`git worktree remove <isolated-temp-dir>/sse-red-repro/v3-base` 已在收尾执行，不留悬空 worktree。
- [x] 正确假设写进本文件与 commit message（假设 1+5：目标行缺失 + 两表校验不对齐，修法=三类快照前置）

### 5.1 live 数据目录纪律

**本地旧库全程只读**：§1 的表格用 `sqlite3` 只读查询取得；所有实验、修复、测试都在临时副本
与 `tmp_path` 上做。未对 live 库执行任何写入（无迁移、无修复、无 `INSERT`/`UPDATE`/`DELETE`）。

---

## 6. 验收项逐条对照

| 票面 Observable acceptance | 结果 | 证据 |
| --- | --- | --- |
| 旧版确切堆栈路径红 | ✅ | §2.1（`api.py:343 → service.py:101 → db.py:794 → db.py:622` → `RuntimeError`） |
| 修复后新回归测试绿 | ✅ | §4（1 passed） |
| 已有 SSE 用例绿 | ✅ | `tests/test_event_stream.py` 17 passed（全量跑中含在内） |
| 相关 API 测试绿 | ✅ | 全量 197 passed, 1 skipped |
| 适用 lint / typecheck 绿 | ✅ | §4（ruff / ruff format / mypy / pyright 四项全绿） |
| 异常大的 diff 需保护现场 | ✅ | diff 仅 2 文件 +92/−6，无格式化噪声（见 §7 的 `git diff --stat`） |
| Answer 写明根因、假设与证据、测试、剩余风险、省略双轴 review 的理由 | ✅ | 本文件 §1–§9 |
| 项目文件有改动时：pr skill 写 PR body、推分支、`--body-file` 创建/更新 PR、回填 PR 号与 body 路径 | ✅ | §8 |

### 6.1 红/绿测试环境与「不在 live 数据目录跑测试」

所有测试用 `tmp_path`（`conftest.py` 的 `data_dir` fixture）或 `<isolated-temp-dir>/sse-red-repro` 下的副本；
`tests/` 的既有约定（每条用例独占临时数据目录）未破。

---

## 7. 改动清单与现场保护

```text
$ git diff --stat
 app/alibot/api.py              | 29 ++++++++++++++----
 app/tests/test_event_stream.py | 69 ++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 92 insertions(+), 6 deletions(-)
```

diff 里**没有**任何与本次修复无关的行（无重新格式化、无顺带重构）。`api.py` 的 6 处删除全是把三类读
从 `stream()` 内部提到返回之前；其余是 docstring 与注释。

**未改动**（票面 Ownership / Constraint）：`app/alibot/db.py`、`app/alibot/service.py`、
`app/alibot/events.py`、`app/alibot/orchestration.py`、`CONTEXT.md`、`docs/adr/`、
`alibot-design-system/`、`app/specs/issue-24-acceptance.md`（#24 历史取证）。

---

## 8. PR

- **PR**：[whutlichao/alibot#39](https://github.com/whutlichao/alibot/pull/39)（真实编号，**不带 issue 关联**）
- **body 文件**：`app/specs/fix-sse-target-fail-fast-pr-body.md`
- **head 分支**：`fix-sse-missing-target`
- **创建命令**：`gh pr create --base main --head fix-sse-missing-target --title "..." --body-file app/specs/fix-sse-target-fail-fast-pr-body.md`
- **正文更新**：`gh pr edit 39 --body-file app/specs/fix-sse-target-fail-fast-pr-body.md`
- **回填方式**：PR 号与 body 路径由本条记录（head sha 不写死，以远端分支为准，避免自指漂移）
- **历史重写**：本 PR 为未合并、单一提交；用户授权后已 amend 并以
  `git push --force-with-lease=refs/heads/fix-sse-missing-target:0ad6024 origin fix-sse-missing-target` 推送成功，
  已核验远端分支与 PR #39 head 均指向本地新提交（新 SHA 以远端分支为准，不在此写死）。

---

## 9. 剩余风险与显式遗留

### 9.1 剩余风险

1. **旧库不一致本身仍未修复**：本票只让事件流（以及同类前置校验）把「channel 行在、target 行缺」
   如实报成 404。**数据仍然缺失**——用户看到的是「频道 404」而不是「频道内容恢复」。
   处置归属 **#37**（启动时拒绝旧库 + 显式手动修复入口 `--repair-targets`），本票不越 scope。
   本地旧库里缺影子行的频道在该修复入口执行前仍会 404（这是**诚实**的表现，不是本票的缺陷）。
2. **同类不一致在其他端点上的表现**：`/messages`、`/members`、`/budget`、`/round-outcomes`、`/read`
   这些**非流式**端点在 `stream()` 之外调用，本来就返回正常 404（§2.1 第 [2] 行实测 404）。
   本票未改它们，也无需改。
3. **`member_statuses` 前置的微小语义变化**：状态点快照现在在**返回响应之前**计算（此前在 `stream()`
   开头，两者都在订阅之前，时序等价）。理论上「快照计算」与「订阅」之间的间隔略微变短/变长，但
   订阅点未移动、`seq < after_seq` 去重未变，因此**可观察行为不变**（§3.1 已论证；既有用例覆盖）。
4. **未对 live 的 v3 形态库做「修复后全端点巡检」**：受「不碰本地旧库」约束，只在临时副本上
   验证。副本上 `/messages` 与 `/events` 均返回 404（一致且在响应前）。

### 9.2 显式遗留（不在本票 scope）

- **#37**：v4 打开 v3 库时的启动拒绝 + 手动修复入口（本票**不实现**）。
- `db.Database._initialize()` 仍只 upsert 版本号、不比对旧版本（#37 的范围）。
- `app/README.md` 的 schema 表仍写 `channel_member/channel_read/channel_budget`（#24 记的遗留 S8）。

---

## 10. 省略双轴 code-review 的理由

票面 G-impl 明确：「小型修复省略双轴 code-review（Standards + Spec），理由：只涉及单一流端点与直接
回归测试，不跨共享接口；如发现范围扩大须报告。」

**范围未扩大**，理由成立且可核验：

1. **单一端点、单函数**：改动全部落在 `channel_events` 一个函数内，且只是把三类读**移动**到
   `StreamingResponse` 之前——没有新增/修改任何共享接口、没有改 `db.py` / `service.py` / `events.py`
   的签名或语义。
2. **不跨共享接口**：`service.channel_messages` / `service.round_outcomes` / `database.list_target_agent_members`
   的调用形态与参数一字未变（只是从生成器内部移到函数体）。
3. **回归测试直接钉住缺陷**：新增用例在不修复时红、修复后绿（§4），并有装置级真 socket 复跑（§4）。
4. **改动面小且有界**：2 文件 +92/−6，无格式化噪声（§7）。

若后续发现需要动共享接口（例如把「作用域一致性校验」下沉为 `db`/`service` 的公共入口，或引入旧库
拒绝逻辑），那属于**范围扩大**，届时须回到 coordinator 升级门禁并补双轴 review。
