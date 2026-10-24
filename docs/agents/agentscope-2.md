# AgentScope 2.0 开发规约（判据入口）

本文件是**改用到 AgentScope 的代码时唯一的判据入口**。文档原话在 `docs/agentscope/2.0.10/`（官方中文页
快照，版本与 pin 一致）；"本仓现在怎么用、差在哪"在 `docs/agents/agentscope-2-audit.md`；"为什么故意
不照做"在 `docs/agents/agentscope-2-deviations.md`。三份各司其职，**不互相抄**。

判据层级（冲突时自上而下）：①"能不能这么写"以已安装 wheel 为准（文档会滞后；2.0.10 站点上还挂着
2.0.11dev 的入口）；②"该不该这么写"以文档正文为准（快照页）；③`release-notes.md` 只用于升级判定，
`others/faq.md` 只作补充、不算判据；④产品语义与架构层的冲突：本仓 ADR 优先，但必须在偏离账本登记。

## §1 红线

五条由 `app/tests/test_agentscope_usage_guard.py` 机械拦截，违反即红，没有"这次先这样"。

**1.1 版本三处一致。** `app/pyproject.toml` 写 `agentscope==X.Y.Z`（精确 pin，不许 `>=`/`~=`）；
需要官方 extra 时写成 `agentscope[extra]==X.Y.Z`（extra 不放松版本约束；本仓用 `memory-reme`，见 §1.5），
等于 `docs/agentscope/X.Y.Z/MANIFEST.json` 的 `version`，也等于本文件所有引用。升级是一张单独的票（§2.2）。

**1.2 只用 2.x API。** 禁止 `ReActAgent`、`sys_prompt=`、`agentscope.agents.*`；禁止引用 `/latest/`、
`/stable/` 或无版本路径。官方 `llms.txt` 原话：`/latest/` 可能跑到 dev 版，不要混用 1.x 与 2.x。
注释与 docstring 里解释"为什么不用 1.x"不算违规——守卫按 AST 判。

**1.3 import 面白名单。** 只允许 16 个顶层模块：`agent` `model` `formatter` `message` `event`
`types` `tool` `state` `permission` `workspace` `skill` `mcp` `pipeline` `middleware` `sop`
`credential`。新增一个 = 同时改守卫的 `ALLOWED_MODULES` 与本条；属刻意偏离还要记账本。
**明确不许** `agentscope.deploy` / `agentscope.service` / `agentscope.session`——它们会把身份与
持久化挪出本应用，而本仓的消息正本必须留在自己的表里。

**1.4 文档链接带版本。** 新写的文档里，AgentScope 链接必须是 `docs.agentscope.io/zh/versions/<pin>/…`
形式（`/en/versions/<pin>/` 同样放行）。`app/specs/**` 是历史验收证据，按"不改写历史"豁免；
`docs/agentscope/**` 是原文快照，不归本仓管。

**1.5 实验性与未采用模块。** `pipeline/team`、`sop` 已采用：按 2.0.10 文档照做并锁定当前行为
（文档明写"实验阶段，接口可能调整"，升级必须重核对）。长期记忆已采用 **ReMe**（ADR-0034：仅
`memory_mode=reme` 时装配 `agentscope.middleware.ReMeMiddleware`，默认 `off`；依赖
`agentscope[memory-reme]==2.0.10`（extra 只声明 `reme-ai>=0.4.0.6`，本仓另 pin `reme-ai==0.4.1.10`；
0.4.1.13 与 2.0.10 的最小 ReMe 配置不兼容）；见 D12）。ReMe 的能力页是
`docs.agentscope.io/zh/versions/2.0.10/building-blocks/long-term-memory.md`；中间件的构造参数以 wheel
实测为准（`ReMeMiddleware.Parameters(chat_model=…, mode=…, top_k=…)`，`await mw.list_tools()` 给出
`memory_search`，`await mw.close()` 收尾）。未采用的 `realtime` / `a2a` / `console` / `rag` 不写、
不引；要用先开票。本仓**不启用** ReMe 的 embedding（检索走 BM25），也不读 ReMe 的 `LLM_*` 环境变量
（模型由本轮 `chat_model` 注入）。

## §2 版本与文档

**2.1 快照是只读缓存。** `docs/agentscope/<版本>/` 由 `scripts/sync-agentscope-docs.sh` 生成，
`MANIFEST.json` 记录版本、抓取时间、页数与每页 sha256。**不许手改任何一页**——守卫按哈希校验。
`--check` 可离线核对；文档站偶发 502，脚本已内置 4 路并发 + 3 轮补抓，仍失败就重跑，不许删页凑数。
CI **不联网**：只校验 pin、哈希、页数，不设定时抓取（挂掉没人看的定时任务比没有更糟）。

**2.2 升级流程（单独一票）。** 改 pin → 重跑同步脚本 → 重核对审计矩阵里标"EXP / 实测"的行 →
更新偏离账本的再评估条件 → `scripts/check.sh` 全绿。**不许**"顺手升一下"：EXP 接口与权限矩阵
都可能在次版本变语义。

**2.3 文档没写怎么办。** 先在 wheel 里实测（`python -c` 打印签名/行为），矩阵判定写"实测"并注明；
**不许**把推断写成官方承诺。稳定结论可以补进本文件，但要标"实测"。

## §3 Agent 构造与一次运行

**3.1 构造。** 必填 `name` / `system_prompt` / `model`；`toolkit`、`state`、`middlewares`、
`context_config`、`react_config` 按需。本仓集中在 `app/alibot/agent_seam.py` 的
`AgentScopeAgentSeam` 构造，**不许**业务模块直接 `Agent(...)`。

**3.2 无状态引擎。** 状态全在 `AgentState`（上下文、压缩摘要、权限规则、工具状态、回复位置），
可序列化为 JSON；跨轮/跨重启恢复必须**显式**传回 `state`。本仓消息正本在自己的表里，
`AgentState` 只是单 Agent 的工作记忆。

**3.3 一轮 ↔ 一条 `Msg`。** `reply()` 返回最终 `Msg`；要过程事件用 `reply_stream()`。
`finished_reason` ∈ `completed` / `interrupted` / `exceed_max_iters` / `error`；**`None` = 暂停中、
尚未结束**——最容易被误判成"成功了"的一个值。`exceed_max_iters` 时框架会追加**一次**禁用工具的
收尾调用，消息里通常仍有可用文本。

**3.4 消息与事件。** 一次 `reply` 产出一个 `Msg`；用 `UserMsg` / `AssistantMsg` / `SystemMsg`
（字符串自动包 `TextBlock`）。role 有硬约束：user 只 Text/Data，system 只 Text，assistant 全部。
`ToolResultBlock.id` 必须等于对应 `ToolCallBlock.id`。事件顺序 start→delta→end，`reply_id` 关消息、
`block_id` 关内容块；`ReplyStartEvent` 时用 `event.reply_id` 建 `AssistantMsg`。只有
`yield_final_msg=True` 时流末尾才额外产出最终 `Msg`。事件**不是**消息正本（ADR-0001/0020）。

**3.5 中断。** 运行中：对承载 `reply`/`reply_stream` 的协程 `task.cancel()`；暂停中：
`UserInterruptEvent(reply_id=agent.state.reply_id)`。**未暂停时它是空操作**，`reply` 会立即返回。
中断时要为每个待处理调用合成"被用户中断"的 `ToolResultBlock`（否则模型看到半截工具状态）。

**3.6 HITL。** 权限 ASK → `RequireUserConfirmEvent`；外部执行 → `RequireExternalExecutionEvent`。
恢复带 `reply_id` + `confirm_results[]`（`ConfirmResult` 的 `confirmed` / `tool_call` 必填、
`rules` 可选）或 `execution_results[]`（`ToolResultBlock` 的 id/name/output/state）。**只回传部分结果时，框架
不会为未处理的调用重发请求事件**——要么全回，要么明确放弃那几条。恢复时**不要再传
`structured_schema`**；本仓确认走 `approval.py` + SSE 的 `approval_required` / `approval_resolved`。

**3.7 上下文。** `trigger_ratio` 上限 `0.9`（留 10% 给压缩调用）；`context_buffer_ratio` **必须小于**
`trigger_ratio`，否则构造即 `ValueError`。摘要固定五字段（task_overview / current_state /
important_discoveries / next_steps / context_to_preserve）。**系统提示词本身超阈值时
`compress_context` 抛 `RuntimeError`**——系统提示词要短。未挂 `offloader` 时被移除内容**直接丢弃**；
需要回查就必须实现 `offload_context` / `offload_tool_result`。固定信息写进系统提示，会变的信息用
`HintBlock` 注入（等同 user 消息，建议加 XML 标签区分）。

**3.8 结构化输出。** `structured_schema` 以单次回复为作用域，可跨暂停/重启；结果在
`Msg.structured_output`（dict），达到迭代上限仍无结果时为 `None`——**用前必须判空**。序列化恢复后
校验会降级（多余字段保留、自定义校验器跳过），关键校验不要只写在 Pydantic 校验器里。

## §4 模型与凭证

**4.1 统一形态。** 所有模型类是 `Model(credential=..., model=...)`；换 provider 只换这两个类。
**但 `credential.get_chat_model_class()` 只给 Chat 模型**：`openai-response` 必须显式建
`OpenAIResponseModel`，不能照抄工厂返回值（2.0.10 wheel 实测）。`formatter` 由模型构造参数传入，
默认 `ChatFormatter`——**它丢 `name`**；多作者对话（频道/团队）必须显式传 `MultiAgentFormatter`
（对应 API 版本），否则模型分不清谁说的。

**4.2 凭证白名单。** 本仓 `provider` 取自 `agentscope.credential` 的类型；`api_key` 由 `read_api_key`
解析，**不许**业务代码直接读环境变量拼 key。支持面以 wheel 实测为准（文档 `model/overview` 的凭证表
与 `model/llm` 的模型类不一致，例如 MiniMax）。

**4.3 usage 记账。** `Msg.usage` 是本轮所有模型调用的累计值，只用于展示与成本，**不是**预算正本
（预算正本是 `target_budget.used`）。

## §5 工具面

**5.1 自定义工具。** 继承 `ToolBase`，实现 `check_permissions` 与 `call`；**不要 override `__call__`**
（框架调度入口）。`FunctionTool` 包装的函数**默认走 ASK**，每次都要放行；约束表达不出来时用
`input_schema` 传 Pydantic 模型。文件工具先读后写，`Edit` 的 `old_string` 必须唯一。

**5.2 `Toolkit` 的 basic 组。** 只传 `tools=` 时全部进 `"basic"` 组、**始终激活**；`mcps`、
`skills_or_loaders`、`tool_groups` 是扩展位。

**5.3 元工具整体覆盖。** 存在非 basic 工具组时自动注册 `reset_tools`；**每次调用都整体覆盖**
激活集合——没显式置 `True` 的组会被停用（文档 WARNING）。要用就每次传全量期望状态。

**5.4 内置工具绑工作区。** `Bash` / `Read` / `Write` / `Edit` / `Glob` / `Grep` 经工作区后端执行，
不要绕过它直接 `subprocess`。`Bash(dangerous_files=...)` 是**替换**默认危险文件列表，不是追加。

**5.5 任务工具。** `TaskCreate` / `TaskUpdate` / `TaskList` / `TaskGet` 读写
`agent.state.tasks_context`，状态只有 pending / in_progress / completed（`deleted` 是操作、不是状态），
阻塞只是建议性的（运行时不强制）。本仓的任务五态在 `plan.py` / `team.py` 另有投影，
**不许**让两套状态各自成为正本（ADR-0022/0023）。

**5.6 工具中间件。** 用 `ToolMiddlewareBase` 包横切策略（如路径护栏）；同一个策略不要散落在各工具内部。

## §6 技能与 MCP

**6.1 Skill。** 每个技能是一个目录、固定含一个 `SKILL.md`（front matter 元数据 + 详细指令）；用
`agentscope.skill.Skill` 或 `skills_or_loaders` 注册。**技能不能被直接调用**，模型先经 `Skill` 查看器
读取；只有存在技能时框架才自动注册查看器并注入名称/描述。front matter 的必填键文档**没写**——以
wheel 实测为准（`skills.py` 的实现就是证据）。

**6.2 目录与隔离。** 工作区布局固定 `.mcp` / `data` / `skills` / `sessions`；MCP 按
`agent_id + session_id` 隔离，技能按 `agent_id` 隔离。**`reset()` 之后 `skill_paths` 不会重新播种**
（`default_mcps` 会）——重置后要自己补技能。安装/卸载走本仓的目录解析与校验，不许模型写技能目录以外
的路径；`add_skill` 缺 `SKILL.md` 或目录已存在抛 `ValueError`。

**6.3 运行时激活。** 未激活的工具组与 skill **不会出现在模型输入里**；靠工具组激活，不是靠提示词
"请你使用某技能"。

**6.4 MCP。** 有状态客户端**必须在构造 Toolkit 前 `await connect()`**；无状态只支持 HTTP，每次调用
临时建会话。工具名固定为 `mcp__{server}__{tool}`。`set_runtime_headers` 只对 Streamable HTTP 有效
（STDIO/SSE 抛 `ValueError`），且运行时 header 不参与 `model_dump` 与工作区持久化。列工具失败的服务
会被跳过并告警，不中断整次回复——本仓 `mcp.py` 的 `_FailureCapture` 是同一方向。

**6.5 沙箱内 MCP。** 沙箱类工作区里的 MCP 跑在隔离环境内，主机经工作区网关访问；主机侧缺活跃客户端
会 `RuntimeError`。不要在宿主机再起一份同名服务。

## §7 权限与 HITL

**7.1 五个模式。** `DEFAULT` / `ACCEPT_EDITS` / `EXPLORE` / `BYPASS` / `DONT_ASK`。决策顺序固定：
拒绝规则 → 询问规则 → 只读快速通道 → 工具 `check_permissions` → 允许规则 → 兜底。

**7.2 规则优先级。** 拒绝与询问规则**永远先于**允许规则（含 BYPASS）；`DONT_ASK` 下询问转 DENY。
`EXPLORE` **不咨询**允许规则与工具 `check_permissions`，但 deny/ask 与只读通道仍生效——别指望
EXPLORE 下靠 allow 规则放行写操作。

**7.3 只读快速通道。** 事实只读的调用在所有模式自动放行，判定是**每次调用**而非工具静态标记
（`git status` 过，写文件的 bash 不过，命令注入风险的一律不算只读）。

**7.4 安全 ASK。** 工具返回 `bypass_immune=True` 的 ASK 不能被允许规则静默；普通 ASK 可以。
工作目录自动放行从属于安全检查，不豁免危险路径。

**7.5 无人值守用 `DONT_ASK`，不用 `BYPASS`。** 文档理由：`DONT_ASK` 保留工具安全网且从不打扰用户；
`BYPASS` 连安全 ASK 都跳过（`rm -rf /`、写 `~/.bashrc` 都放行），只剩 deny 规则兜底。本仓的频道
权限映射见 `permissions.py` 的 `map_permission_mode`。

**7.6 ASK 的闭环。** ASK 带自动生成的**建议规则**；用户接受后规则持久化，相同调用不再询问。本仓把
"用户接受"落在 `permission_context` 的规则集合里，不另造规则表。

## §8 工作区
**8.1 七种后端，一个接口。** `LocalWorkspace` / `BubblewrapWorkspace` / `DockerWorkspace` /
`E2BWorkspace` / `DaytonaWorkspace` / `K8sWorkspace` / `OpenSandboxWorkspace`，都派生自
`WorkspaceBase`。本仓只采用 Local 与 Docker（`workspace.py`），其余不引入。**`LocalWorkspace` 直接用
宿主机，不提供沙箱隔离**——需要隔离就换 Docker，别把 Local 当沙箱。

**8.2 生命周期。** `initialize()` / `close()` / `reset()`；资源发现用 `list_tools()` / `list_mcps()` /
`list_skills()` / `get_instructions()`；动态管理用 `add_mcp()` / `remove_mcp()` / `add_skill()` /
`remove_skill()`。**必须**保证 `close()` 在异常路径也执行（`async with` 或 `try/finally`）。临时
`DockerWorkspace` 的 MCP 列表只在内存，销毁即丢。

**8.3 分区与持久化。** 会话分区 id 由本仓生成（`session_partition_id`），不要把 `member_id` 直接当
目录名拼路径。Docker 后端的工作目录按分区隔离，重启要能重新挂接。

**8.4 根目录护栏（本仓自定，文档未写）。** 所有路径解析经 `resolve_in_root` / `_within`，拒绝 `..`
与指向根外的 symlink。这不是 AgentScope 的能力，是本仓的安全边界——不要因为"框架会管"而绕开。

## §9 编排：plan / SOP / team / middleware

**9.1 Plan。** 计划工具的状态注入在 `agent.state.tasks_context`，只有三态（§5.5）；手改它要自己保住
数字 ID 与双向依赖。**四工具只在"有 plan 且本轮权限档允许写操作"时注入**——`EXPLORE` 下注入等于
让模型拿到一组必然 DENY 的工具。本仓的任务五态**不是**它，plan 只投影（ADR-0023）。

**9.2 SOP。** `SOPRunState` 只含流程状态，执行者/验证者上下文另存；`SOPStep` 默认 `max_attempts=3`，
用尽则整个运行失败；保存后增删步骤会让 `SOPEngine` 抛 `ValueError`——改流程要新开一次运行。

**9.3 Team pipeline。** leader 必须带 toolkit，流水线要注册 `TeamAssign`；成员名互不相同且不能与队长
重名（否则 `ValueError`）；`TeamAssign` 只有 `member` 与 `prompt`，成员看不到队长上下文；成员结束原因
映射 SUCCESS / INTERRUPTED / ERROR。**`reset_members` 默认 `True`**：队长回复后会清空成员上下文。
**预算/跳数文档没写**——本仓的 `jump_consumed` 是应用层语义（ADR-0019），按"实测/自定"对待。

**9.4 Middleware。** 6 个 hook + 可选 `list_tools`，未实现的自动跳过；onion 顺序第一个在最外层，
`on_system_prompt` 左到右接力。**`list_tools` 不会被自动调用**，要自己合并进 toolkit。只有
`ReplyEndEvent` 穿过整链 reply 才真正结束；**`INTERRUPTED` 不能靠吞事件续跑**，必须原样外传；吞
`EXCEED_MAX_ITERS` 前先释放 `cur_iter`。`on_acting` **不追踪外部执行工具**。中间件实例保持无状态，
状态放 `agent.state.middle_context`。

## §10 审计、偏离与报错

**10.1 审计矩阵。** `docs/agents/agentscope-2-audit.md` 的 45 行是验收单位：每行有判定
（符合 / 刻意偏离 / 未采用 / 待修 / 实测）、证据与票号。"待修"必须挂票，不许留在矩阵里过夜。

**10.2 偏离账本。** 与文档推荐不同但本仓有意为之的做法，都要在
`docs/agents/agentscope-2-deviations.md` 有一条：偏离点 / ADR 依据 / 文档原话 / 再评估条件。
**再评估条件必须可判定**（"升到 2.1 且文档给出 X 时"），不写"以后再说"。

**10.3 报错风格。** 守卫失败时一次列全所有违规，每条给「错在哪 → 该怎么做 → 本文件第几节」。
只报第一个会让人反复跑测试。

## 附录：模块 → 文档页（快照相对路径）

| 模块 | 先读 |
| --- | --- |
| agent | `building-blocks/agent/overview.md`、`configure-agent.md`、`run-agent.md`、`interrupt-agent.md`、`human-in-the-loop.md` |
| model / credential | `building-blocks/model/overview.md`、`llm.md` |
| message / event | `building-blocks/message-and-event.md` |
| tool | `building-blocks/tool/overview.md`、`python-tool.md`、`manage-tools.md` |
| skill / mcp | `building-blocks/tool/skill.md`、`building-blocks/tool/mcp.md`、`building-blocks/workspace/mcp-gateway.md` |
| permission | `building-blocks/permission-system/` 四页 |
| workspace | `building-blocks/workspace/` 四页 |
| context | `building-blocks/context/overview.md`、`compress-context.md`、`offload-context.md` |
| plan / sop / team | `building-blocks/plan.md`、`building-blocks/sop.md`、`building-blocks/pipeline/team.md` |
| middleware | `building-blocks/middleware.md` |
