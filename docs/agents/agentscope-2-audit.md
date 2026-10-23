# AgentScope 2.0 对齐审计矩阵（45 行）

判据在 `docs/agents/agentscope-2.md`；文档原话在 `docs/agentscope/2.0.10/`；"为什么故意不照做"在
`docs/agents/agentscope-2-deviations.md`。**每一行是验收单位**：有判定、有证据、待修挂票。

判定词表（只有这五个）：

- **符合**：做法与 2.0.10 文档一致。
- **刻意偏离**：与文档推荐不同，但代码里有理由，且账本有对应条目（D 编号）。
- **未采用**：该能力本仓不用，替代做法写在说明里。
- **待修**：与文档不一致且没有理由，已挂票。
- **实测**：文档没明说，结论来自已安装 wheel 的实测。

基线：`agentscope==2.0.10`（`app/pyproject.toml`），快照 `docs/agentscope/2.0.10/`（59 页）。
证据里的路径均相对仓库根；`grep` 命令可直接复跑。

## 汇总

| 判定 | 行数 | 行号 |
| --- | --- | --- |
| 符合 | 27 | 1,3,4,6,7,8,9,10,11,12,14,15,18,19,21,22,24,26,27,29,33,35,36,37,40,41,43 |
| 刻意偏离 | 14 | 2,5,13,16,20,25,28,30,31,32,34,38,39,44 |
| 未采用 | 2 | 17,45 |
| 待修 | 0 | — |
| 实测 | 2 | 23,42 |

> 待修已清零：6 / 8 / 33 三行由 **#205** 收口。刻意偏离对应账本 D1–D12（部分条目覆盖多行）。

## 1–5 agent

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 构造参数（name/system_prompt/model/toolkit/state） | agent/configure-agent.md | `agent_seam.py:1076-1082` `run_round_stream` | 符合 | `Agent.__init__` 签名含五项；`agent_options` 在 1076 组装 | toolkit 为空不传；state 只带 `permission_context` |
| 2 | 无状态引擎 + AgentState 传入传出 | agent/run-agent.md | `agent_seam.py:1081`、`:1114-1125` | 刻意偏离 | `grep -n '"state": AgentState' app/alibot/agent_seam.py` → 1081、1411 | 每轮新建 Agent，历史靠库内消息重放；AgentState 每轮落盘并在下轮恢复，但消息表仍是正本（D3 / ADR-0030） |
| 3 | reply 事件流消费 | agent/run-agent.md | `agent_seam.py:1148` | 符合 | `grep -n 'yield_final_msg=True' app/alibot/agent_seam.py` → 1148 | `reply_stream(..., yield_final_msg=True)`，事件经 emit 直播 |
| 4 | 中断 | agent/interrupt-agent.md | `api.py` 中断端点 + `agent_seam.py` 的 `interrupt_round` | 符合 | `grep -n 'rounds/{reply_id}/interrupt' app/alibot/api.py`；`grep -n 'def interrupt_round' app/alibot/*.py` | 运行中 `task.cancel()`、审批挂起回送 `UserInterruptEvent`；只接受当前 in-flight 的 `reply_id`，错误 target / 重复请求 409（ADR-0030） |
| 5 | HITL 确认往返 | agent/human-in-the-loop.md | `agent_seam.py:1152`、`:1223`；`approval.py:96` | 刻意偏离 | `grep -n suggested_rules app/alibot/agent_seam.py` → 1219 | 回送 `ConfirmResult` 时剥掉 `suggested_rules`，规则由 broker 落库、下一轮生效（D2） |

## 6–9 model

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 6 | 内置模型与自定义 ChatModelBase | model/llm.md | `agent_seam.py:1181` `_build_model`；`:166` `PROVIDER_MODEL_CLASS` | 符合 | `cd app && OPENAI_API_KEY=sk-x ../.venv/bin/python -c "from alibot.agent_seam import AgentScopeAgentSeam; m=AgentScopeAgentSeam()._build_model('openai-response','gpt-5'); print(type(m).__name__, type(m.formatter).__name__)"` → `OpenAIResponseModel OpenAIResponseMultiAgentFormatter` | 11 家内置模型按 provider 显式映射；`openai` 与 `openai-response` 共用 `openai_credential`，后者建 Responses API 模型 |
| 7 | credential 白名单与 api_key 解析 | model/overview.md | `agent_seam.py:140` `PROVIDER_CREDENTIAL_TYPE`；`:597` `read_api_key` | 符合 | `grep -n 'PROVIDER_CREDENTIAL_TYPE\|def read_api_key' app/alibot/agent_seam.py` | 11 家 provider 与凭据类型一一对应；key 只从环境白名单读 |
| 8 | formatter 与 provider 对应 | model/llm.md | `agent_seam.py:180` `PROVIDER_FORMATTER_CLASS`；`:1196` 注入 `formatter=` | 符合 | `cd app && ../.venv/bin/python -m pytest tests/test_agentscope_model_alignment.py -k "openai_response or every_provider or multi_agent"` → `13 passed, 5 deselected` | 11 家 provider 各用对应 MultiAgentFormatter；多作者轮次的 `name` 进入模型载荷 |
| 9 | usage/token 记账 | message-and-event.md | `agent_seam.py:1645` `_record_usage` | 符合 | `grep -n 'getattr(reply, "usage"' app/alibot/agent_seam.py` → 1654 | 读最终 `Msg.usage` 四字段交给 sink；不落库、不改轮次结论 |

## 10–13 message / event

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 10 | Msg / 内容块组装 | message-and-event.md | `agent_seam.py:1116`、`:1218`；`team.py:300` | 符合 | `grep -n 'ToolCallBlock.model_validate' app/alibot/agent_seam.py` → 1218；`grep -n 'ToolResultBlock(' app/alibot/team.py` → 300 | 文本 TextBlock、确认回送 ToolCallBlock、team 结果 ToolResultBlock |
| 11 | 角色口径（UserMsg/system/assistant） | message-and-event.md | `agent_seam.py:1116-1123` | 符合 | `grep -n 'role="assistant"' app/alibot/agent_seam.py` → 1119 | 他人消息 UserMsg；自己的历史回复 role=assistant；system 走 `system_prompt` |
| 12 | AgentEvent → 应用事件（reply_id） | message-and-event.md | `agent_seam.py:491` `_agent_event_record`；`events.py:126` | 符合 | `grep -n 'def _agent_event_record' app/alibot/agent_seam.py` → 491 | ReplyStart 建 reply_id→member 表；孤儿事件丢弃不猜 |
| 13 | 事件可回放性判定 | message-and-event.md | `events.py:39` `REPLAYABLE_TYPES`；`:94` `is_replayable` | 刻意偏离 | `grep -n REPLAYABLE_TYPES app/alibot/events.py` → 39 | `agent_event` 无 id、断线不补、重启即失；只有 message/round_outcome 可补拉（D4） |

## 14–18 tool

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 14 | FunctionTool/ToolBase 自定义工具 | tool/overview.md | `agent_seam.py:2039` `_value_returning_tool`；`:2054`、`:2081` | 符合 | `grep -n 'def _value_returning_tool' app/alibot/agent_seam.py` → 2039 | 普通函数包成 FunctionTool；typing 差异收口在一处 |
| 15 | Toolkit 注册与工具组 | tool/overview.md | `agent_seam.py:2036` `_round_toolkit` | 符合 | `grep -n 'return Toolkit(' app/alibot/agent_seam.py` → 2036 | 只注册 tools + skills_or_loaders（basic 组）；tool_groups 未用（行 17） |
| 16 | 内置工具与工作区绑定 | workspace/overview.md | `workspace.py:346` `_guarded_tools`；`:546`；`:360` | 刻意偏离 | `grep -n '刻意不调用' app/alibot/workspace.py` → 347 | 不调 `workspace.list_tools()`（无条件含 Bash），手装 5 个文件工具 + 根检查（D5） |
| 17 | 元工具（运行时开关工具组） | tool/manage-tools.md | 未采用 | 未采用 | `grep -rn 'ToolGroup\|reset_tools' app/alibot` → 零命中 | 替代是按轮静态装配 workspace/web/task/plan/silence |
| 18 | 任务工具（TaskCreate 等） | plan.md | `plan.py:43` `plan_tools`；`:198` `_task_context` | 符合 | `grep -n 'def plan_tools' app/alibot/plan.py` → 43 | 四工具只在有 Plan 的轮次注入；任务清单经 `plan_run/plan_task` 持久化 |

## 19–21 skill

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 19 | Skill 加载与 front matter | tool/skill.md | `skills.py:292` `_front_matter`；`:539` `load_skills`；`:589` | 符合 | `grep -n 'def _front_matter' app/alibot/skills.py` → 292 | 要求 name+description 并校验形状/长度；解析失败整条拒绝 |
| 20 | 安装/卸载目录布局 | workspace/overview.md | `skills.py:97` `skills_partition`；`:349`；`:513` | 刻意偏离 | `grep -n 'def skills_partition\|def uninstall' app/alibot/skills.py` → 97、513 | 字节放 `.alibot/skills/<scope>/<name>`，不进框架 `skills/`（D6） |
| 21 | 运行时激活路径 | tool/skill.md | `agent_seam.py:1102` `_skills_for`；`:2036` | 符合 | `grep -n skills_or_loaders app/alibot/agent_seam.py` → 2036 | 每轮按启用行重读、复核 hash，再作为 Skill 交给 Toolkit |

## 22–24 mcp

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 22 | MCPClient（stdio/http 配置） | tool/mcp.md | `mcp.py:127` `build_client` | 符合 | `grep -n 'StdioMCPConfig\|HttpMCPConfig' app/alibot/mcp.py` → 145、152 | stdio → StdioMCPConfig；http/sse → HttpMCPConfig；统一 `is_stateful=True` |
| 23 | 工具装配与失败捕获 | tool/mcp.md | `mcp.py:160` `assemble_mcp_tools`；`:102` `_FailureCapture` | 实测 | `grep -n 'Failed to start MCP' .venv/lib/python3.11/site-packages/agentscope/workspace/_base.py` → 648 | 框架 list_mcps 吞单 server 异常只写 warning；装配层用日志捕获器转可读状态 |
| 24 | 沙箱内 MCP 网关 | workspace/mcp-gateway.md | `mcp.py:218` `workspace.list_mcps` | 符合 | `grep -n 'Gateway-wrapped MCP handles' .venv/lib/python3.11/site-packages/agentscope/workspace/_sandboxed_base.py` → 270 | Docker 后端返回 GatewayMCPClient 代理；Local 用本地客户端 |

## 25–28 permission

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 25 | PermissionMode 映射 | permission-system/permission-mode.md | `permissions.py:47` `map_permission_mode`；表 `:40` | 刻意偏离 | 读数：`{read-only:EXPLORE, ask-writes:DEFAULT, trusted-writes:ACCEPT_EDITS}`；`bypass/dont_ask` → `ValueError` | 产品只暴露三档，框架五档中的 BYPASS/DONT_ASK 明确拒绝（D1） |
| 26 | 规则 allow/deny/ask 与路径匹配 | permission-system/permission-rule.md | `permissions.py:55/70/98/118`；`db.py:768` | 符合 | allow `Write=/tmp/proj/src/**`、deny `Bash=rm:*`；相关测试 71 passed | 行为枚举与判定顺序交给框架；相对 glob 先锚到根 |
| 27 | 工具内置检查（危险命令） | permission-system/tool-check.md | Docker：`workspace.py:552` `Bash(backend=…)`；本地：`:360` `AskEveryTimeBash`、`:379` | 符合 | 框架 `Bash` 对 `rm -rf /` 返回 ASK + `bypass_immune=True`；本地子类两者都 ASK | Docker 复用框架检查；本地更严：取消只读快通道 |
| 28 | 权限引擎与 HITL 衔接 | human-in-the-loop.md | `agent_seam.py:1152-1164`、`:1206-1225`；`approval.py:66/96/151` | 刻意偏离 | `agent_seam.py:1216-1219` 丢弃 `suggested_rules`；`api.py:985` 另有规则入口 | 决策逐次回送、建议规则只展示；规则由人类面显式增删（D2） |

## 29–32 workspace

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 29 | Local/Docker 后端选择 | workspace/run-workspace.md | `workspace.py:1038` `assemble_workspace`（`:406` local / `:755` docker） | 符合 | `from_mapping` 下 local→`is_local`、docker→`is_docker`；Docker 失败不回落 | 频道定根、Agent 覆盖后端；Docker 不可用直接 offline 并给可读原因 |
| 30 | 会话分区与状态持久化 | workspace/run-workspace.md | `workspace.py:170`、`:162`、`:267`、`:393` | 刻意偏离 | `session_partition_id('m-7','t-3')='t-3--m-7'`；无 `save_state/offload` 产品引用 | 分区只用于目录/MCP 隔离；对话状态走 SQLite append-only 消息（D3） |
| 31 | 运行时资源增删（MCP/skill） | workspace/manage-resources.md | `api.py:1002-1186`；`mcp.py:160/213`；`skills.py:378/513/539` | 刻意偏离 | 产品代码无 `add_mcp/remove_mcp/add_skill/remove_skill` | 增删是配置动作（DB 行 + 副本），下一轮重新装配（D7） |
| 32 | 根目录护栏（路径逃逸） | workspace/overview.md | `workspace.py:223/233/198/499/535/347` | 刻意偏离 | `src/a.py`→根内；`../outside.py`、`/etc/passwd` 拒绝；Docker `/etc/passwd` 拒绝 | 框架内置文件工具没有根准入；本仓在装配层给五个工具挂中间件（D5） |

## 33–34 plan

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 33 | 计划模式工具面 | plan.md | `plan.py:38/43/50`；`agent_seam.py:1446`/`:2500` 单 Agent；`:1841`/`:1867` team | 符合 | `cd app && ../.venv/bin/python -c "import asyncio; from agentscope.permission import PermissionEngine, PermissionContext, PermissionMode; from agentscope.tool import TaskCreate; print([(m.name, asyncio.run(PermissionEngine(PermissionContext(mode=m)).check_permission(TaskCreate(), {})).behavior.name) for m in (PermissionMode.EXPLORE, PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS)])"` → `[('EXPLORE', 'DENY'), ('DEFAULT', 'ALLOW'), ('ACCEPT_EDITS', 'ALLOW')]`；`cd app && ../.venv/bin/python -m pytest tests/test_agentscope_model_alignment.py -k "plan_tools_or_open_a_plan_run or plan_tool_surfaces"` → `5 passed, 13 deselected` | read-only 轮次既不注入四工具、也不开 Plan run（单 Agent 与 team 队长/成员同一判据） |
| 34 | plan run 与任务五态边界 | plan.md | `plan.py:136/162/174`；`db.py:4428/178/200` | 刻意偏离 | 三态 `pending/in_progress/completed` 与五态 `todo/in_progress/in_review/done/closed` 并存、无映射 | 任务五态归 `task` 行（ADR-0006）；两套状态机并列（D8） |

## 35–36 sop

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 35 | SOPEngine 执行 | sop.md | `sop.py:70/113/236/95` | 符合 | `test_sop.py` 8 项通过；生产/恢复都由真实 `SOPEngine.reply_stream` 驱动 | executor/verifier 按 member id 装配；参与者不挂工具 |
| 36 | SOPRunState 持久化与恢复 | sop.md | `sop.py:143-148/301-306/152-186/232-236`；`db.py:4825` | 符合 | `sop_run.state` 每次引擎事件后回写；resume 从库里 state 重建 | 流程状态可跨重启；参与者 AgentState 不恢复（D19 已划出） |

## 37–38 team

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 37 | TeamPipeline/TeamMember 构造 | pipeline/team.md | `team.py:365/386/89`；`agent_seam.py:1296` | 符合 | `test_team_mode.py` 通过；成员名由 member id 派生（D10） | 构造参数与文档一致；每轮按成员快照重建 |
| 38 | TeamAssign 分派与跳数 | pipeline/team.md | `team.py:139/187/217/236` | 刻意偏离 | 覆写签名与 wheel `pipeline/_team_pipeline.py:284-291` 对齐；`test_team_budget.py` 9 项通过 | 分派沿用基类；jump 是框架没有的预算层，靠覆写实验性私有方法（D9） |

## 39–40 middleware

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 39 | 中间件钩子 | middleware.md | `permissions.py:159/172`；`workspace.py:223/233` | 刻意偏离 | `grep 'MiddlewareBase\|on_reasoning\|on_acting'` 仅命中两处 | 只挂权限与工具两个钩子；预算裁决不走 `on_acting`（D10） |
| 40 | 自定义中间件护栏 | tool/python-tool.md | `workspace.py:223` + `:245-259`；`permissions.py:178-183` | 符合 | `test_local_workspace.py` / `test_docker_workspace.py` 通过；逃逸读数见行 32 | 两层：工具中间件做 realpath 根检查；Agent 中间件只改权限判定副本 |

## 41–43 state / context

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 41 | AgentState 序列化恢复 | agent/run-agent.md | `runtime.py`（`SessionState`） | 符合 | `grep -n 'model_dump_json' app/alibot/runtime.py` | AgentState 用 `model_dump_json` 原子写 `.alibot/sessions/<session_id>/state.json`、下一轮 `load` 恢复；单用户本机不用 RedisStorage（D3 现状 / ADR-0030） |
| 42 | ReplyFinishedReason 判定 | agent/run-agent.md | `agent_seam.py:1675` `_outcome_from_reply` | 实测 | `python -c "from agentscope.types import ReplyFinishedReason;print([r.value for r in ReplyFinishedReason])"` → 4 值；`grep -n 'reply_error = '` → 1682 | 只把 error 收口为 failed；completed/exceed_max_iters 按正文收口，interrupted 由超时路径先转 failed |
| 43 | 上下文压缩 / 卸载 / 环境感知 | context/compress-context.md、offload-context.md | `runtime.py`（`runtime_configs` / `SessionOffloader`）+ `agent_seam.py` 装配 | 符合 | `grep -rn 'ContextConfig\|InjectionConfig\|SessionOffloader' app/alibot` → `runtime.py`、`agent_seam.py` 均有命中 | 单 Agent 与 team 都显式传 `ContextConfig` / `InjectionConfig`，压缩内容与被截断工具结果经 `SessionOffloader` 落 `.alibot/sessions/<id>/offload/`；每轮重取时间与 target 名（D11 现状 / ADR-0030） |

## 44–45 未采用

| # | 能力点 | 文档页 | 当前落点 | 判定 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 44 | RAG + 长期记忆 | rag.md、long-term-memory.md | `memory.py`（AgenticMemory 装配） | 刻意偏离 | `grep -n AgenticMemoryMiddleware app/alibot/memory.py`；`grep 'RAGMiddleware\|embedding'` 在 product 代码零命中 | RAG 未采用（检索走 SQLite）；长期记忆采用 `AgenticMemoryMiddleware`，作用域是成员记忆目录而非第二套消息正本（ADR-0030 / D12） |
| 45 | 实时语音 / A2A / 终端 UI / Agent Service / deploy | realtime/overview.md、a2a.md、console.md、deploy/agent-service.md | 未采用 | 未采用 | import 面只有 16 个白名单模块（`test_agentscope_usage_guard.py` 守卫 2） | 产品形态是 Web 频道 + 进程内 Agent；没有终端/语音/跨进程 Agent 服务需求 |

## 待修去向

无。行 6 / 8 / 33 已由 **#205** 收口（判定改「符合」，证据列已补可复跑命令与读数）。
