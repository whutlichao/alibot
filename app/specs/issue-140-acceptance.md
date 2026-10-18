# #140 验收取证：流式 seam `run_round_stream` + 事件归属

票面正本：GitHub issue #140（Part of #131）。判据来源：`app/specs/agentscope-2-alignment.md`
§3.2 / §4.5 与 D4 / D10；契约正本：`docs/adr/0020-agent-events-are-live-and-messages-stay-the-record.md`；
归属算法的实测依据：`app/specs/issue-132-research.md` §2 / §4（`reply_id` 表与孤儿事件策略）。

- 分支：`issue-140-impl`（从集成分支 tip `f829d13` 切出）
- 实现提交：`1f6b295` feat(seam)（代码 + 测试）
- 文档提交：本文件
- 未改 D1–D23、既有 ADR 原文、schema、数据库、编排与 SSE 层

## 一、逐条判据 → 落点 → 命令与读数

以下命令都从 worktree 根执行；pytest 必须从 `app/` 跑（`pyproject.toml` 的 `testpaths`）。

### 判据 1｜`AgentSeam.run_round_stream(...)` 存在；`run_round(...)` 语义不变并委托给它

- 落点：
  - `app/alibot/agent_seam.py`：`AgentSeam` Protocol 新增 `run_round_stream`；
    `AgentScopeAgentSeam.run_round` 变成 `return self.run_round_stream(..., emit=None)` 的兼容壳；
    `ScriptedAgentSeam` 同形（替身也委托，两处入口结论必然一致）。
  - 参数与规格 §3.2 逐字一致：`agent / messages / * / mentioned / target_id /
    task_context / mandatory_reply / emit`。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_agent_event_stream.py -k "delegates or matches_run_round"
# 2 passed, 4 deselected
#   test_run_round_delegates_to_run_round_stream：monkeypatch 掉 run_round_stream 后，
#     run_round 确实调用了它，且 emit=None
#   test_run_round_stream_without_emit_matches_run_round：两条入口的 RoundOutcome 完全相等
```

### 判据 2｜单 Agent 轮次发出五类记录、字段齐全；task target 的 `target_id` 正确

- 落点：`AgentScopeAgentSeam.run_round_stream` 内 `_consume()` 消费
  `instance.reply_stream(..., yield_final_msg=True)`；`_agent_event_payload` 是白名单副本。
  有 `emit` 时才把模型建为流式（`_build_model(..., stream=True)`），否则 provider 请求形状与
  #140 之前一致——既有 stub 测试因此不需要改成 SSE。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_agent_event_stream.py -k "whitelisted or task_target"
# 2 passed, 4 deselected
#   test_stream_emits_whitelisted_records_with_full_attribution
#     - 实际类型集合（脚本化模型：两段文本 + 一次不存在的工具调用）：
#       REPLY_START / TEXT_BLOCK_START / TEXT_BLOCK_DELTA / TEXT_BLOCK_END /
#       TOOL_CALL_START / TOOL_CALL_DELTA / TOOL_CALL_END /
#       TOOL_RESULT_START / TOOL_RESULT_TEXT_DELTA / TOOL_RESULT_END / REPLY_END
#     - 白名单外的 MODEL_CALL_START / MODEL_CALL_END 一条都没有
#     - 归属字段：仅一个 reply_id；每条记录的 member_id=mem_140、target_id=ch_140
#     - payload：TOOL_CALL_START={"tool_call_id":"call-1","tool_call_name":"NoSuchTool"}；
#       TOOL_RESULT_END 的 state="error"；REPLY_END 的 finished_reason="completed"；
#       TEXT_BLOCK_DELTA 前两段 = ["你好", "，世界"]
#     - RoundOutcome.status="replied"，messages=["完成"]（一轮恰一条正文）
#   test_records_carry_the_task_target_id：target_id="task_140" 的轮次里，
#     每条记录 target_id 都是 task_140（不是所属频道），member_id 不变
```

### 判据 3｜归属用 id 派生名 + `reply_id` 表；查不到的事件丢弃并记 debug

- 落点：`member_internal_name()`（D10：`m-<member id>`）与 `_agent_event_record()`。
  `ReplyStartEvent.name` 只用于建 `reply_id → member_id` 表；其余事件只按 `reply_id` 查表。
  没有 `reply_id`（`CustomEvent`）、`ReplyStart.name` 不在派生名表、`reply_id` 不在表内、
  类型不在白名单——四种情况都丢弃并记 `agent event dropped: …` 的 debug，不猜。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_agent_event_stream.py -k orphan
# 1 passed, 5 deselected
#   test_orphan_and_unknown_events_are_dropped_with_debug
#   构造：未知 name 的 ReplyStart(r1) → 孤儿 delta；CustomEvent；合法 ReplyStart(r2)；
#         ModelCallStart(r2)；合法 delta(r2)；未知 reply_id(r3) 的 delta；ReplyEnd(r2)
#   透传结果只有 3 条：REPLY_START / TEXT_BLOCK_DELTA("保留正文") / REPLY_END，全部 reply_id=r2
#   捕获到 5 条 debug：
#     ReplyStart name 'not-a-member' is not a known member
#     reply_id r1 has no ReplyStart
#     CUSTOM has no reply_id
#     MODEL_CALL_START is outside the whitelist
#     reply_id r3 has no ReplyStart
```

### 判据 4｜`scripted.py` 的替身支持流式行为；既有测试语义不变

- 落点：`ScriptedAgentSeam.run_round_stream`（`_resolve_outcome` 保持原有五种行为，
  再按固定形状发 `REPLY_START` → 每条待落库正文一条 `TEXT_BLOCK_DELTA` → `REPLY_END`）。
  `run_round` 委托给它，`calls` 日志与结论逐字不变。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_agent_event_stream.py -k scripted
# 1 passed, 5 deselected
#   test_scripted_seam_streams_without_changing_the_outcome：
#   记录 = [REPLY_START, TEXT_BLOCK_DELTA, REPLY_END]，delta="脚本回复"，
#   member_id/target_id/reply_id 齐全；同一行为的 run_round 返回值与之相等

cd app && ../.venv/bin/python -m pytest \
  tests/test_silence_tool_channel.py tests/test_agent_seam_client_lifecycle.py \
  tests/test_event_stream.py tests/test_silent_outcome.py
# 35 passed in 132.67s（这四套都在全量门禁里，且没有改任何断言）
```

> 唯一的既有测试改动是机械迁移：`test_silence_tool_channel.py` 的
> `_StubTransportSeam._build_model` 覆写跟着新增的 `stream` 关键字参数加了一行
> （`super()._build_model("openai", model_name, stream=stream)`），断言一字未改。

### 判据 5｜事件不落库、不改变 `RoundOutcome` 与 `message` 正本

- 落点：`AgentEventRecord` 只经同步 `emit` 回调离开 seam；`agent_seam.py` 不 import 数据库，
  也没有任何写路径。`_consume()` 只把 `Msg` 赋给 `reply`，`RoundOutcome` 的构造逻辑
  （silent/ignored/replied/failed 与 `RoundMessage`）一行未改。
- 命令与读数：

```bash
grep -rn "AgentEventRecord" app/alibot | grep -v "agent_seam.py"
# （无输出：事件类型没有被 db / api / orchestration 引用）

git diff --stat f829d13 -- app/alibot/db.py app/alibot/orchestration.py app/alibot/api.py
# （无输出：消息正本、轮次落库、SSE 层都没有改动）

cd app && ../.venv/bin/python -m pytest tests/test_agent_event_stream.py -k "matches_run_round"
# 1 passed, 5 deselected：带 emit 的流式出口与不带 emit 的 run_round 结论相等
```

## 二、实现形态（为什么是这样）

1. **流式只在有观察者时打开。** `emit is None` 时模型仍以 `stream=False` 构造：没有事件消费者时，
   provider 请求形状与 #140 之前逐字相同。既有的进程内 HTTP stub（`test_silence_tool_channel.py`、
   `test_agent_seam_client_lifecycle.py`）因此不需要改成 SSE，`run_round` 的语义也不受 streaming
   实现细节影响。
2. **派生名只用于建表。** `Agent` 的框架内部名改为 `m-<member id>`（D10 / ADR-0018）；
   `ReplyStartEvent.name` 用它命中归属表。派生名不放进 payload、不落库、不显示。
3. **白名单是结构字段，不是整个事件。** `TextBlock*` / `ThinkingBlock*` / `ToolCall*` /
   `ToolResult*` / `ReplyStart` / `ReplyEnd` / `HintBlock` / `RequireUserConfirm` 透传结构字段；
   `DataBlock*` 与 `ToolResultDataDelta` 只给 `media_type` + `length`，不给 base64/URL 正文；
   `ModelCall*` / `ExceedMaxIters` 等不在白名单。事件只直播，不发 SSE `id`（ADR-0020 四）。
4. **观察面不能改写轮次。** `emit` 抛异常只记一条 debug；本轮结论仍只由最终 `Msg` 与结构化工具
   记录决定。事件处理失败不把一轮变成 failed。

## 三、门禁

```bash
scripts/check.sh
# pytest（全量）：659 passed, 2 skipped
# ruff check：All checks passed
# ruff format --check：148 files already formatted
# mypy：Success: no issues found in 62 source files
# pyright：0 errors, 0 warnings, 0 informations
# node --test（Studio DOM 用例族）：全部通过
# 门禁全绿
```

（合并集成分支 tip 后重跑同一条命令；读数见 PR/issue 评论。）

## 四、残余与已知边界

1. **team / HITL 不在本票。** 本票只覆盖单 Agent 的 `reply_stream` 消费；`BudgetedTeamPipeline`
   与 HITL 恢复流的归属在 #142。研究结论（issue-132 §4）提醒：恢复流没有新的 `ReplyStart`，
   需要复用或从 `_parked_members()` 预填 `reply_id` 表——那是 #142 的落点，本票的 per-call 表
   只覆盖单轮单次 `reply_stream`。
2. **流式行为只用脚本化 `ChatModelBase` 取证。** 真实 provider 的流式分块形状由 D7/冒烟票覆盖；
   本票的 HTTP stub 用例仍走非流式路径，故意不改。
3. **部分白名单分支没有专属生产者。** `ThinkingBlock*` / `DataBlock*` / `RequireUserConfirm` /
   `HintBlock` 的 payload 分支已实现，但脚本化模型不产生这些事件；它们的正确性依赖框架字段
   内省（`model_fields` 核对于 2.0.10）与 #142 的真实 pipeline 用例。
4. **`CustomEvent` 永远被丢弃。** 它没有 `reply_id`，无法满足 D4 的归属要求；研究结论
   （issue-132 §2）建议默认丢弃。若将来业务必须透传，必须由适配层显式补 `reply_id`/上下文，
   不能猜。
