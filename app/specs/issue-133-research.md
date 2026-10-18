# Issue #133 研究：provider 的 TeamAssign 工具调用可靠性

- 日期：2026-10-18
- 票型：research（G-research），只做研究与最小探针，**不改 `app/alibot/**`、`app/tests/**`**
- 依赖版本：`agentscope==2.0.10`、Python 3.11.13、`openai==3.29.0`、`httpx==0.28.1`
- 结论：**一次人类授权的 DeepSeek 真实调用成功命中了 `TeamAssign`（1/1，成员执行成功，无重试）**；thinking 模式、工具 schema 被拒、`tool_choice` 差异、无分派回退均取得真实读数或源码级读数；ollama 因本机无服务端而记为 partial。总成本约 **$0.0001131（off-peak）/ $0.0002262（peak 上限）**，用量 482 输入 + 68 输出 tokens。

## 0. 环境与复跑入口

```bash
# 仓库根（worktree /tmp/alibot-wt/issue-133-research）
.venv/bin/python --version                 # 3.11.13
.venv/bin/python -c "import agentscope; print(agentscope.__version__)"   # 2.0.10

# 离线探针（无网络、无 key）
env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
    .venv/bin/python app/specs/issue-133-research/probe_known_differences.py

# 真实调用探针：会**再花一次**付费请求，只有拿到新的授权才可运行
env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
    .venv/bin/python app/specs/issue-133-research/probe_team_assign_deepseek.py
```

- 凭证来源：只使用环境变量 **`DEEPSEEK_API_KEY`**。探针只判断它是否存在；不打印、不写文件、不进入报告。
- 探针复用 `app/specs/issue-132-research/_harness.py` 的脚本化 `ChatModelBase` / `TracingAgent` / `InterceptingTeamPipeline`（成员与第二次队长调用都不联网）。
- 本票没有改产品源码；改动只有 `app/specs/issue-133-research.md` 与 `app/specs/issue-133-research/` 下的两个探针。

## 1. 判据一：真实 DeepSeek 队长的 `TeamAssign` 调用

### 配置

| 项 | 值 |
| --- | --- |
| 模型标识（请求） | `deepseek-chat`（`DeepSeekChatModel`，base URL `https://api.deepseek.com`） |
| thinking | `thinking_enable=False`（与 `agent_seam.py` 的生产接线一致：`Parameters()` 默认关闭） |
| 流式 | `stream=False`（与 `agent_seam.py:326-349` 的 `_build_model` 一致） |
| 重试 | `max_retries=0`（探针强制；生产接线**没有**传这个参数，见 §6） |
| `max_tokens` | 2048 |
| 工具 schema | 只有 `TeamAssign`（由 `TeamPipeline` 注册；`tool_choice` 未设置） |
| 成员 | `m-alpha`，脚本化模型，不调真实 provider |
| 授权边界 | 1 次真实请求、最多 1 次 `TeamAssign`、输入 ≤ 8k tokens、无重试 |

### 真实输出（`probe_team_assign_deepseek.py`，2026-10-18T03:47:49Z）

```text
real_api_calls=1
scripted_leader_calls=1
tool_schema_names=["TeamAssign"]
tool_choice_seen=null
estimated_input_tokens=272
latency_s=1.121
usage.input_tokens=482
usage.output_tokens=68
usage.cache_input_tokens=0
usage.time_s=1.121345
raw ToolCallBlock: TeamAssign
  input={"member": "m-alpha", "prompt": "Produce exactly this line, with no extra words: hello from alpha"}
team_assign_calls_emitted=1
assignments_executed=[{"id": "call_00_foB8syhbgXW0PPHlSSkb9030", "state": "success"}]
assignments_dropped_by_cap=[]
member_stream_starts=1
assignment_success=true
outcome=assignment
```

事件计数（同一次运行）：

```text
ReplyStart=2  ReplyEnd=2  ModelCallStart=3  ModelCallEnd=3
ToolCallStart=1 ToolCallDelta=1 ToolCallEnd=1
ToolResultStart=1 ToolResultTextDelta=1 ToolResultEnd=1
TextBlockStart=1 TextBlockDelta=1 TextBlockEnd=1
HintBlock=2
```

- **`TeamAssign` 调用次数/成功率：1/1 = 100%（n=1）**。模型一次回复就产出恰好一个 `TeamAssign`，没有多余工具调用；`CappedTeamPipeline` 的截断列表为空。
- 成员确实开始并完成了自己的流（`member_stream_starts=1`），工具结果状态是 `success`。
- 队长在收到成员结果后还有一次模型调用；那一次由脚本化响应代替，**没有产生第二个付费请求**（`scripted_leader_calls=1`）。
- 请求接受了 `deepseek-chat` 这个模型名。AgentScope 的 `_parse_completion_response` 不保留服务端返回的 `model` 字段，因此**无法从本次读数判断实际服务版本**（见 §8）。

### 未调用时的回退行为（结构性 + 脚本化读数）

真实调用中队长**调用了** `TeamAssign`，所以“没调用”的行为不能用同一次付费调用观察。它由两层可复现读数确定：

1. **结构**：`TeamPipeline._reply()` 在队长流结束后执行 `if not assignments: return`（`pipeline/_team_pipeline.py:265`）。没有 `TeamAssign` 时不会运行任何成员，也不会给队长喂第二条工具结果；队长自己的正文就是这一轮的唯一输出。
2. **脚本化复现**（`probe_known_differences.py` 的 `no_assignment_fallback`）：

```text
events=5
event_types=[HintBlockEvent, ModelCallEndEvent, ModelCallStartEvent, ReplyEndEvent, ReplyStartEvent]
member_stream_starts=0
intercepts=[]
assignment_results=[]
model_calls=1
error=null
leader_context_blocks=[["TextBlock"], ["HintBlock", "TextBlock"]]
```

即：**队长自己回答**，没有普通工具调用、没有成员运行、没有报错。这也是 `supports_tool_calling(provider)` 为假时应当走的降级形态。

## 2. 判据二：ollama（partial / blocked）

- `which ollama` **无输出**（本机没有 ollama 二进制）；`curl -m 2 http://localhost:11434/api/tags` 返回 HTTP 状态 `000`（连接不上）。
- Python 侧的 `ollama` 客户端模块存在，但**没有服务端**，因此无法做真实的 ollama `TeamAssign` 调用；本票**没有**伪造读数。
- 能做的源码级读数（`probe_known_differences.py`，不联网）：

```text
ollama_tool_choice_required:
  formatted_tool_choice=null
  tools_kept=["TeamAssign"]
  warning="Ollama ignores tool_choice.mode; tool_choice.tools is still applied to filter tool schemas."
```

`agentscope/model/_ollama/_model.py:193-228` 的 `_format_tools` 永远返回 `(tools, None)`：`tool_choice` 的 `mode` 被忽略（只打 warning），`tools` 列表仍会转发。因此 ollama 上的 `TeamAssign` 只能靠模型**自愿**调用，不能强制。

- 残余：ollama 的模型标识、真实调用次数/成功率、时延与本地成本都**没有读数**；需要一台装有 ollama 且有模型的机器重跑同一形状的探针。

## 3. thinking 模式

### 3.1 源码/文档事实

- `DeepSeekChatModel` 用 `parameters.thinking_enable` 决定 `extra_body.thinking.type`（`_deepseek/_model.py:207-217`）；`thinking_enable=False` 时显式发送 `{"thinking": {"type": "disabled"}}`。
- DeepSeek 官方文档（2026-10-18 读取）：thinking 模式支持工具调用；但 **forced `tool_choice`（`"required"` 或指定函数名）与 thinking 同时出现会返回 HTTP 400**，需要强制时须先关掉 thinking（<https://api-docs.deepseek.com/guides/thinking_mode/>、<https://api-docs.deepseek.com/api/create-chat-completion/>）。
- 同一文档要求：请求带 `tools` 时，后续轮次必须把历史 assistant 的 `reasoning_content` 原样传回，否则 400。

### 3.2 脚本化读数（`thinking_plus_assign`）

同一响应里先出现 `ThinkingBlock`、再出现 `TeamAssign`：

```text
events=22
ThinkingBlockStart=1 ThinkingBlockDelta=1 ThinkingBlockEnd=1
ToolCallStart=1 ToolCallDelta=2 ToolCallEnd=1
member_stream_starts=1
assignment_results=[1]
leader_context_blocks=[["TextBlock"], ["HintBlock", "ThinkingBlock", "ToolCallBlock", "ToolResultBlock", "TextBlock"]]
formatted_assistant_reasoning_content=["I should delegate this."]
model_calls=2
error=null
```

- thinking 块**不会**吞掉同一响应里的 `TeamAssign`；拦截点仍拿到 call，成员仍运行。
- `Agent._save_to_context` 把 `ThinkingBlock` 写进上下文（`_agent.py:1811`），`DeepSeekChatFormatter` 会把它格式化成 assistant 的 `reasoning_content`（`_formatter/_deepseek_formatter.py:82-83`、`:230-235`）。因此“带 tools 的多轮必须回传 reasoning_content”这一条在 AgentScope 2.0.10 的 DeepSeek 适配层是**满足的**。
- 但本票的**真实**调用是 `thinking_enable=False`（生产接线默认）。真实的 thinking 模式 + `TeamAssign` 仍需一次额外付费调用才能给出端到端读数；受授权上限约束，记为 **partial**。

## 4. 工具 schema 被拒

脚本化 provider 在第一次模型调用抛出 `openai.BadRequestError`（HTTP 400，代表服务端拒绝工具 schema）：

```text
events=3
event_types=[HintBlockEvent, ModelCallStartEvent, ReplyStartEvent]
member_stream_starts=0
intercepts=[]
assignment_results=[]
model_calls=1
error={"class": "BadRequestError", "module": "openai", "message": "probe: provider rejected the tool schema"}
```

关键读数：

- **异常直接穿过 `TeamPipeline.reply_stream()`**：流里只有 3 个事件，没有 `ReplyEndEvent`，也没有 `finished_reason=ERROR`。调用方必须自己 catch，否则 SSE 会以异常收尾。
- **只调用 1 次**：`DeepSeekChatModel._get_retryable_exceptions()` 只包含 `APIConnectionError` / `APITimeoutError` / `RateLimitError` / `InternalServerError`（`_deepseek/_model.py:139-148`），`BadRequestError` 不在其中（探针读数 `deepseek_bad_request_is_retryable=false`）。schema 被拒不会重试。
- 现有 seam 的 `run_round` 已经把所有异常收口成 `RoundOutcome(status="failed")`（`app/alibot/agent_seam.py:426-435`）。team 模式的等价实现应复用这个收口，而不是让异常穿过事件流。

## 5. `tool_choice` 被忽略 / 被强制

`probe_known_differences.py` 用假 OpenAI 兼容客户端读取**实际要发的 kwargs**（不联网）：

| 场景 | 实际请求 |
| --- | --- |
| DeepSeek，thinking 开，`tool_choice="required"` | `extra_body={"thinking":{"type":"enabled"}}` + `tool_choice="required"` + `tools=[TeamAssign]` |
| DeepSeek，thinking 关，指定函数 `TeamAssign` | `extra_body={"thinking":{"type":"disabled"}}` + `tool_choice={"type":"function","function":{"name":"TeamAssign"}}` |
| DeepSeek，`tool_choice=None` | `extra_body.thinking=disabled` + `tools=[TeamAssign]`，**没有 `tool_choice` 键** |
| Ollama，`tool_choice="required"` / 指定函数 | `tool_choice=None` + warning（被忽略） |
| 未知工具名 | `ValueError: Invalid tool name 'NotATool' in tool_choice.mode. Available tools: TeamAssign`（本地校验，未发请求） |

补充读数：

- `TeamPipeline` 源码中 `tool_choice` 出现次数：**0**。`TeamAssign` 在框架里本来就是**自愿**调用，不靠强制。
- 因此对 team 模式而言，“`tool_choice` 被忽略”不是 `TeamAssign` 的主要风险；主要风险是**有人为了“确保分派”而强行设置 `required`/指定函数**：在 DeepSeek thinking 模式下会 400，在 Ollama 上会被静默忽略。**不要强制。**

## 6. 用量、时延与成本

### 6.1 真实用量

| 读数 | 值 |
| --- | --- |
| 真实 API 请求数 | 1（`max_retries=0`） |
| 输入 tokens | 482（cache hit 0） |
| 输出 tokens | 68 |
| 服务端计时 | 1.121345 s |
| 客户端墙钟（单次调用） | 1.121 s |
| 整轮墙钟（含脚本化成员与第二次脚本化队长回复） | 1.174 s |
| 估算输入（bytes/4，发送前预算检查） | 272 tokens ≤ 8,000 |

> 诚实记录：第一次运行因探针自身的 `asyncio.wait_for` 用法错误在**发请求前**失败（`real_api_calls=0`，无网络、无用量）。修正后只发出**一次**真实请求，即上表。没有重试，也没有第二次付费调用。

### 6.2 单价来源与时点

- 来源：DeepSeek 官方 Models & Pricing，<https://api-docs.deepseek.com/quick_start/pricing/>，读取时间 **2026-10-18**。
- 该页面当前列出 `deepseek-flash` 与 `deepseek-v4-pro`，**不再列出 `deepseek-chat`**。本次请求虽然被接受，但无法确认服务端按哪一档计价。下表按 `deepseek-flash` 公开单价估算：

| 项目 | Peak | Off-peak |
| --- | --- | --- |
| 输入 cache hit（/1M） | $0.006 | $0.003 |
| 输入 cache miss（/1M） | $0.3 | $0.15 |
| 输出（/1M） | $1.2 | $0.6 |

- 2026-10-18 是**周日**，按官方“周末全时段 off-peak”的规则，本次实际应落在 off-peak。
- 估算：

```text
off-peak = 482 * 0.15/1e6 + 68 * 0.6/1e6 = 0.0000723 + 0.0000408 = $0.0001131
peak     = 482 * 0.30/1e6 + 68 * 1.2/1e6 = 0.0001446 + 0.0000816 = $0.0002262
```

- 本票总费用：**$0.0001131（按 off-peak 计）**；若按 peak 上限计为 $0.0002262。ollama 未调用，成本 $0。

### 6.3 生产接线的重试差异（重要）

- `agent_seam.py:326-349` 的 `_build_model` 构造 `DeepSeekChatModel(..., stream=False)` 时**没有传 `max_retries`**，因此用默认值 **3**（`_deepseek/_model.py:85`）。
- Agent 层自己的 `ModelConfig.max_retries` 默认是 **0**（`agent/_config.py:422-431`），不会与模型内层重试叠加；但模型内层仍可能对连接/超时/限流/500 做最多 4 次尝试。
- team 模式一次队长轮可能触发多次成员调用；若队长调用遇到 429/超时，内层重试会把成本与时延放大。建议 team 轮显式传 `max_retries`（0 或 1），并在运行记录里保留尝试次数。

## 7. team 模式的降级建议（研究结论，不是产品裁定）

1. **分派保持自愿**：不设置 `tool_choice`。`TeamAssign` 和 `declare_silence` 一样是自愿通道；强制分派在 DeepSeek thinking 下 400、在 Ollama 下被忽略，只会制造 provider 差异。
2. **没有分派 = 队长单干**：队长正文就是这一轮的唯一输出（`_reply()` 的 `if not assignments: return`）。只落队长消息，不创建成员消息/运行记录；这与规格 §6 的“team 退化为队长单干（仍只落队长消息）”一致。
3. **能力闸门按 provider 前置**：沿用 `supports_tool_calling(provider)`（`agent_seam.py:105-107`）。为假时**不要注册** `TeamAssign`，让队长从不带 `tools` 的请求开始；不要等运行时 400 再退。
4. **运行时 schema 拒绝要收口**：闸门为真但 provider/model 仍返回 400 时，异常会直接穿过 `TeamPipeline.reply_stream()`（本票读数）。team 轮应在边界 catch，并按既有 seam 的纪律落成 `failed`（`agent_seam.py:426-435`）；若要“降级成队长单干”，只能做**一次**无工具重试，并明确记录这是降级，避免无限重试/重复计费。
5. **thinking 与多轮**：若开启 thinking，必须保留 `ThinkingBlock` 到上下文并回传 `reasoning_content`；2.0.10 的 DeepSeek 适配层已满足（§3.2），但升级版本后要重跑本票的 `thinking_plus_assign` 场景。
6. **成员失败不要拖垮队长流**：`_run_assignments` 会把成员异常向上抛（本票只验证了队长侧 400）；team 实现应把“成员这一条 assignment 失败”转成 `ToolResultBlock(state=ERROR)`，让队长看到失败结果而不是整轮崩掉——这条是建议，本票未做真实成员失败读数。

## 8. 残余与风险

- **样本量 n=1**：真实 DeepSeek 只调用了一次。1/1 只证明“这个 prompt + 这个模型版本可以调用”，不能推出成功率；多次采样需要新的授权与预算。
- **服务版本不可见**：`deepseek-chat` 已不在官方价格表；AgentScope 的解析结果不保留响应里的 `model` 字段，无法确认实际服务的是哪个版本。成本按 `deepseek-flash` 估算，可能与账单有偏差。
- **thinking 真实调用未做**：受一次调用上限约束；只有脚本化 + 源码/文档读数。
- **ollama 完全 blocked**：本机无服务端；本地模型的 `TeamAssign` 可靠性、时延、本地资源占用均无读数。
- **工具 schema 被拒是模拟的 400**：用 `openai.BadRequestError` 走真实的 Agent/pipeline 异常路径，但没有真的让 DeepSeek 拒绝一个 schema（那需要第二次付费请求）。
- **`TeamPipeline` 是实验性接口**：本票依赖 `_run_assignments` / `_reply` 的现有形状；升级 2.0.10 之外的小版本必须重跑两个探针（与 #132 的结论一致）。
- **第二次队长调用是脚本化的**：因此本票没有测“队长看到成员结果后的真实总结质量/额外 token”，只测第一次真实决策。

## 9. 判据映射

| 票面判据 | 读数 / 落点 | 状态 |
| --- | --- | --- |
| 每个 provider 的模型标识、`TeamAssign` 次数/成功率、未调用回退 | DeepSeek `deepseek-chat`：1/1、`success`、回退=队长直接回答（结构 + 脚本化）；ollama 无服务端 | DeepSeek ✅；ollama partial |
| token、时延、估算成本 | 482 in / 68 out；1.121 s；$0.0001131 off-peak（peak 上限 $0.0002262） | ✅ |
| thinking、schema 被拒、`tool_choice` 差异、降级建议 | §3–§7；thinking 为脚本化+源码读数，schema 拒绝为脚本化 400，`tool_choice` 为真实请求 kwargs 读数 | ✅（thinking 真实调用 partial） |
| 报告落 `app/specs/`、环境/凭证变量名/复跑命令、不改产品源码 | 本文件 + `app/specs/issue-133-research/`；只写 `DEEPSEEK_API_KEY` | ✅ |

## 附录：探针清单

| 文件 | 用途 | 联网 |
| --- | --- | --- |
| `app/specs/issue-133-research/probe_team_assign_deepseek.py` | 一次真实 DeepSeek 队长调用 + 脚本化成员；预算/重试/截断硬约束 | 是（1 次，需新授权才能重跑） |
| `app/specs/issue-133-research/probe_known_differences.py` | thinking + 分派、无分派回退、schema 400、`tool_choice` kwargs、重试默认值、Ollama 格式化 | 否 |
