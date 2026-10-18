# AgentEvent 走 SSE 直播，消息正本仍只有最终正文；成员面接缝新增 `run_round_stream`

用户于 2026-10-18 人定（D4）：SSE 透传 `AgentEvent`（`ReplyStart` / `TextBlockDelta` / `ToolCall` /
`ToolResult` / `Thinking` 等）；数据库仍只存**最终正文消息**，append-only、未读、搜索、轮次语义不变。
成员面接缝新增**流式出口**（`run_round_stream` 或规格论证的等价物），既有 `run_round` 保留给脚本化替身与
既有测试。SSE 新事件类型必须带 `member_id` / `reply_id` / `target_id`，便于前端归属。

## 一、两层，不是两套正本

**事件层**是运行过程的直播：token 增量、思考块、工具调用、工具结果、审批请求。它**不是**业务正本，
断线不补发，进程重启即消失。**消息层**仍是 `message` 表：append-only、`UNIQUE(target_id, seq)`、未读、
搜索、`round_outcome` 六态、补拉游标都以它为准。`AgentEvent` 只经 `EventBus` 广播；**没有任何代码路径**
把 `TextBlockDelta` 或 `ToolCallStart` 写成 `message` 行。

这条分层的直接推论：

- 前端在消息流里看到的仍只有最终正文；token 增量只出现在运行面板/输入指示里。
- 搜索、未读、提及解析、附件绑定都只读 `message`；事件流不参与。
- `RoundOutcome` 仍是队长轮次的结论；成员在 team 里的“回复”不是频道消息轮次，写 `team_assignment`，
  不写 `round_outcome`（避免污染六态词表）。
- 事件不补拉：`GET /channels/{id}/events?after_seq=N` 仍只补消息与 `round_outcome`；新事件是 **live-only**。

## 二、接缝形状：`run_round_stream` 增量，`run_round` 兼容

```python
@dataclass
class AgentEventRecord:
    type: str          # REPLY_START / TEXT_BLOCK_DELTA / TOOL_CALL_START / ...
    target_id: str
    member_id: str
    reply_id: str
    payload: dict[str, Any]

class AgentSeam(Protocol):
    def run_round(self, agent, messages, *, mentioned, target_id,
                  task_context=None, mandatory_reply=False) -> RoundOutcome: ...
    def run_round_stream(
        self, agent, messages, *, mentioned, target_id,
        task_context=None, mandatory_reply=False,
        emit: Callable[[AgentEventRecord], None] | None = None,
    ) -> RoundOutcome: ...
```

`run_round` 保留为 `run_round_stream(..., emit=None)` 的兼容壳。理由不是“少改测试”，而是**接缝的宽度**：
既有脚本化替身、既有测试、单 Agent 的收口语义都不该因为 team 模式而改。`emit` 是同步回调（编排跑在工作线程），
把事件交给 `EventBus`；它不阻塞模型调用，队列满时丢弃事件（`QUEUE_LIMIT = 256`，与既有行为一致）。

## 三、归属：id 派生名 + reply_id 表

`AgentEvent` 的 `ReplyStartEvent` 有 `name`、`reply_id`、`session_id`；后续 `TextBlock*` / `ToolCall*` /
`ToolResult*` 只有 `reply_id`，没有 `name`。因此：

1. 构造 pipeline 时建立 `member_id → 内部名` 与反向表；内部名由 **member id** 派生（D10），
   不使用可能重复的 `display_name`。
2. 收到 `ReplyStartEvent` 时，用 `name` 查表得到 `member_id`，写入 `reply_id → member_id`。
3. 其余事件按 `reply_id` 查表归属；查不到就丢弃并记 debug，**不猜**。
4. 每条 `AgentEventRecord` 都带 `target_id`（由当前轮次的 target 注入）。

`payload` 是白名单副本：文本/思考/工具调用的结构字段；`DataBlock*` 只保留 media_type 与长度，不透传 base64
正文；框架的 `metadata` 只保留白名单键。事件里不出现凭证、env 值、完整工具结果中的敏感字段。

## 四、SSE 帧与游标

现有 `sse_frame()` 总是发 `id: <seq>`。新事件没有可补拉的 `seq`，若继续发 `id`，浏览器会把 `Last-Event-ID`
推进到一个“库里不存在”的游标，重连时补拉行为会与消息游标混淆。因此：

- `message` 与 `round_outcome`：仍发 `id: <seq>`，可补拉。
- `agent_event` / `team_assignment` / `plan_update` / `sop_event` / `approval_required` / `approval_resolved`：
  **不发 `id` 行**；浏览器保留最后一个可补拉事件的 id。
- 新事件不进入 `EventBus` 的历史缓冲；断线期间的过程不补发。运行面板用
  `GET /targets/{id}/team-runs` / `GET /targets/{id}/plan` / `GET /sop-runs/{id}` / `GET /approvals`
  的快照兜底。

## 五、被否方案与代价

- **把每个 `AgentEvent` 落库**：事件量远大于消息量，会把单文件库与备份体积推向不可控；而且事件不是业务正本，
  落库会制造“第二套历史”。否。
- **只做 SSE、不扩接缝，直接在 `AgentScopeAgentSeam` 里把事件推到全局总线**：适配层需要知道 target/member
  归属与白名单，等于把协作域逻辑塞进适配层；而且脚本化替身无法复现流式行为。否。
- **删除 `run_round`，全部改流式**：既有测试与脚本化替身要重写，且单 Agent 的“一轮一个 `RoundOutcome`”
  语义没有必要改。否。
- **新事件也发 `id`**：会破坏重连游标语义。否。

**代价**：事件是 live-only，用户断开期间看不到过程；`EventBus` 队列满会丢事件；前端需要同时处理“可补拉”
与“仅直播”两类帧；`AgentEvent` 是框架事件，2.0.10 的实验性模块（TeamPipeline/SOP）可能改变事件集合，
需要 D7 与 R1 的核对。
