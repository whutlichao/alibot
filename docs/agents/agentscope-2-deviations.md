# AgentScope 2.0 偏离账本

规约 §10.2：凡与文档推荐不同、但本仓**有意为之**的做法，都在这里有一条。它的作用不是辩解，是
**记账**——每条都写清"文档说什么、我们为什么不照做、什么条件下要回头看"。

再评估条件必须是**可判定**的：写"升到 2.1 且某 API 出现时"，不写"以后再说"。条目的 D 编号被
`docs/agents/agentscope-2-audit.md` 的判定列引用；ADR 本身不改（历史决策不就地重写）。

---

## D1 权限档位收窄为三档

- **偏离点**：框架有五个 `PermissionMode`，本仓只暴露并映射三档（read-only → `EXPLORE`、
  ask-writes → `DEFAULT`、trusted-writes → `ACCEPT_EDITS`）；`bypass` / `dont_ask` 从配置与 API
  进入即 `ValueError`。
- **依据**：ADR-0021 §四。
- **文档原话**：「AgentScope 支持五种模式」— `permission-system/permission-mode.md`。
- **理由**：本仓是单用户本机工作台，`BYPASS` 会跳过工具安全 ASK（`rm -rf /` 都放行），`DONT_ASK`
  在无人在场时才有意义；暴露它们等于把"关掉安全网"做成一个下拉项。
- **再评估条件**：当产品出现**无人值守**场景（定时任务 / 自动续跑无人看管）或用户明确要求
  第四种档位时，再评估引入 `DONT_ASK`；引入时优先 `DONT_ASK` 而不是 `BYPASS`。

---

## D2 HITL 不回写"建议规则"

- **偏离点**：权限 ASK 时框架会附带自动生成的建议规则；用户接受后，文档说规则会持久化、相同调用
  不再询问。本仓回送 `ConfirmResult` 时剥掉 `suggested_rules`，规则只由人类面显式增删，下一轮生效。
- **依据**：`app/alibot/agent_seam.py:1216-1219`、`app/alibot/approval.py:285`、ADR-0021 §四。
- **文档原话**：「已接受的规则会持久化到权限引擎中」— `building-blocks/agent/human-in-the-loop.md`。
- **理由**：建议规则是"这一次放行"的副产品，直接落库会把一次误点变成永久放行；规则需要能被人类
  看见、编辑、撤销，所以走独立的规则端点（`POST /api/permission-rules`）。
- **再评估条件**：当用户明确要求"同一命令不再询问"（接受即生效）**或**框架给出建议规则的稳定
  回写/展示契约时，再评估"接受即落库 + 可撤销"。

---

## D3 框架会话状态不落盘（AgentState 每轮重建 + 不写 `sessions/context.jsonl`）

- **偏离点**：每轮新建 `Agent`，`AgentState` 只带 `permission_context`；对话历史从本仓 SQLite
  消息重放。`session_id` 只用于工作区目录与 MCP 实例隔离，不写框架的 `sessions/context.jsonl`。
- **依据**：ADR-0001（消息 append-only）、ADR-0021 §三；`agent_seam.py:630-631`、`:1114-1125`。
- **文档原话**：「保存上下文、权限上下文和会话状态」— `building-blocks/agent/configure-agent.md`；
  「sessions/ # 各会话的 context.jsonl 与工具结果文件」— `building-blocks/workspace/run-workspace.md`。
- **理由**：消息正本必须在自己的表里（可搜索、可补拉、可审计），框架状态只是单 Agent 工作记忆；
  两份"上下文"会立刻分叉。跨重启恢复单 Agent 状态不在当前范围（D19 已划出）。
- **再评估条件**：当需要**跨重启恢复单 Agent 上下文**（例如长工具链中断后续跑）**或**引入多会话
  并发（同一 Agent 多 session）时，再评估持久化 `AgentState`，并明确"消息表仍是正本"。
- **现状（2026-10-21，ADR-0030）**：再评估条件已触发并收口——`AgentState` 现在每轮结束原子写入
  `.alibot/sessions/<session_id>/state.json`，下一轮恢复摘要、工具缓存、Plan 与 middleware 状态；
  消息 context 仍按数据库窗口重建，消息表仍是唯一正本（D12 / ADR-0030）。原文保留作判据来源。

---

## D4 `agent_event` 是 live-only

- **偏离点**：应用事件里只有 `message` / `round_outcome` 可补拉（有 id、进游标）；`agent_event`
  无 id、断线不补、重启即失。
- **依据**：ADR-0020 §四；`events.py:39` `REPLAYABLE_TYPES`、`:94` `is_replayable`。
- **文档原话**：「从任意检查点重放事件序列也能精确恢复消息状态」— `building-blocks/message-and-event.md`。
- **理由**：`AgentEvent` 是过程信号（文本增量、工具进度），不是事实；把每条增量落库会让事件表
  膨胀几个数量级，而事实已由消息与轮次结果承担。
- **再评估条件**：当前端要求**断线续传 agent 事件**，或事件需要进搜索/审计/回放时，再评估给
  事件加 id 与可补拉投影。

---

## D5 文件工具手工装配 + 根检查放在装配层

- **偏离点**：不调用 `workspace.list_tools()`（它无条件含 `Bash`），而是在装配层手装 5 个文件工具，
  每个挂 `WorkspaceRootGuard` / `ContainerRootGuard`；本地 shell 用 `AskEveryTimeBash`。
- **依据**：ADR-0021 §二；`workspace.py:3-11`、`:347`。
- **文档原话**：「通过工作区后端执行的内置工具」— `building-blocks/workspace/overview.md`。
- **理由**：框架内置文件工具没有"必须落在项目根内"的准入；本地后端更不能把宿主 `Bash` 直接交给
  模型。准入必须在工具外面，不能指望模型自觉。
- **再评估条件**：当框架为内置工具提供**根目录约束参数**（或 `list_tools()` 支持传入 root 约束）时，
  再评估改回统一装配 + 保留同样的根检查。

---

## D6 Skill 用 `.alibot/skills/<scope>/<name>` 布局

- **偏离点**：技能字节放本仓自己的分区目录，不进框架工作区的 `skills/`；安装/卸载走本仓的
  zip/目录校验与 hash 复核。
- **依据**：ADR-0021 §三；`skills.py:97-99`、`:349-375`、`:513`。
- **文档原话**：「存放在 `skills/` 目录下的 Markdown 指令集」— `building-blocks/workspace/overview.md`。
- **理由**：技能是"可安装/可卸载/可审计"的产品对象，需要 scope 隔离、hash、拒绝覆盖与原子替换；
  框架的 `add_skill` 语义（目录已存在 → `ValueError`）不足以表达这些。
- **再评估条件**：当需要把技能交给框架工作区统一管理（例如技能市场导入导出、跨工具复用）时，
  再评估改成"标准布局 + 本仓校验层"。

---

## D7 资源增删走配置面，不用运行时 API

- **偏离点**：不使用 `workspace.add_mcp/remove_mcp/add_skill/remove_skill`；增删是 DB 行 + 副本的
  配置动作，下一轮装配时重新读取。
- **依据**：ADR-0021 §三；`mcp.py:212-213`、`skills.py:1-20`、`api.py:1002-1186`。
- **文档原话**：「MCP 服务与技能都可以在工作区运行期间添加或移除」— `building-blocks/workspace/manage-resources.md`。
- **理由**：轮次是短生命周期（一次 `reply`），运行期热插拔的窗口只有秒级；配置面更容易做权限、
  校验与"下一轮生效"的可解释语义。
- **再评估条件**：当需要**运行期热插拔**（不结束当前轮次就生效）且框架 API 稳定后，再评估接入
  `add_mcp` / `add_skill`。

---

## D8 Plan / SOP 状态与任务五态分家

- **偏离点**：`plan_run/plan_task` 的三态（pending/in_progress/completed）由本仓维护；任务五态
  （todo/in_progress/in_review/done/closed）仍在 `task` 行，两者没有自动映射。SOP 的运行状态
  存 `SOPRunState.model_dump_json()`，不写进任务表。
- **依据**：ADR-0023、ADR-0006；`plan.py:3-12`、`db.py:178`、`db.py:200`。
- **文档原话**：「所有任务状态都存在智能体上，位于 `agent.state.tasks_context`」— `building-blocks/plan.md`。
- **理由**：任务五态是产品对象（有人类审阅、指派、频道可见性），plan 三态是单轮执行清单；
  强行合并会让"人类改了任务状态"与"模型改了清单"互相覆盖。
- **再评估条件**：当同一个任务对象需要被 plan 三态与任务五态**同时修改**，或产品要求两者自动同步时，
  再评估引入显式映射层（而不是让任一方成为另一方的隐式投影）。

---

## D9 TeamAssign 跳数靠覆写私有 `_run_assignments`

- **偏离点**：`BudgetedTeamPipeline` 覆写 `TeamPipeline._run_assignments`，在成员运行前逐条原子扣跳；
  耗尽返回 ERROR 且不运行成员。
- **依据**：ADR-0019；`team.py:10-12`、`:95-101`。
- **文档原话**：「分派给谁、分派几次由队长自己决定」— `building-blocks/pipeline/team.md`；
  文档**没有** jump/预算概念。
- **理由**：预算是产品语义（一次分派扣一次跳），框架不提供钩子，只能在分派入口做。覆写签名与
  2.0.10 wheel 逐字对齐，并由 `test_team_budget.py` 钉住。
- **再评估条件**：当 `TeamPipeline` 公开预算/分派钩子，**或**升级到 2.0.11+ 时 `_run_assignments`
  签名变化（升级票必须重核对本行）。

---

## D10 中间件只挂两个钩子，预算裁决不走 `on_acting`

- **偏离点**：只用 `on_check_permission`（Agent 中间件）与 `ToolMiddlewareBase.on_tool_call`
  （工具中间件）；`on_reasoning` / `on_acting` / `on_model_call` 未使用，预算裁决在 pipeline 私有
  方法里。
- **依据**：ADR-0019 §一；`permissions.py:159`、`workspace.py:223`。
- **文档原话**：「AgentScope 暴露了 6 个 hook 位置」— `building-blocks/middleware.md`。
- **理由**：`on_acting` 看不到外部执行工具（源码 `middleware/_base.py`），用它做预算会漏掉
  TeamAssign 这条主要路径；把裁决放在分派入口才不会漏。
- **再评估条件**：当 `on_acting` 能覆盖**外部执行工具**与预算裁决（文档与源码对齐）时，再评估把
  预算逻辑迁回中间件。

---

## D11 上下文压缩/卸载未显式配置

- **偏离点**：不传 `ContextConfig`、不实现 `Offloader`；依赖框架默认自动压缩，被压缩内容丢弃。
- **依据**：ADR-0001；`agent_seam.py:1114-1125`。
- **文档原话**：「若配置了 `offloader`，被摘要的消息还会被卸载到磁盘」— `building-blocks/agent/run-agent.md`；
  「未挂 offloader 时被移除内容直接丢弃」— `building-blocks/context/offload-context.md`。
- **理由**：本仓每轮只交入"未读的新消息"，历史在 SQLite 里可重放；单轮上下文通常远小于窗口，
  压缩很少触发，卸载没有收益。
- **再评估条件**：当单轮上下文**可能超过模型窗口**（例如整段频道历史交入、长工具链累积）或需要
  回查被压缩内容时，再评估 `ContextConfig` + `Offloader`。
- **现状（2026-10-21，ADR-0030）**：再评估条件已触发并收口——普通轮改为最近 40 条历史窗口，单 Agent
  与 team 均显式传 `ContextConfig` / `InjectionConfig`，并实现 `SessionOffloader` 落
  `.alibot/sessions/<id>/offload/`；本条偏离不再成立，原文保留作判据来源。

---

## D12 长期记忆用 ReMe 工作区，不引入第二套消息正本

- **偏离点**：审计矩阵第 44 行原把长期记忆判为「未采用」，理由是「会引入第二套事实源」。现采用
  `agentscope.middleware.ReMeMiddleware`（进程内 ReMe；`agentscope[memory-reme]` 的 floor 之外另 pin `reme-ai==0.4.1.10`，0.4.1.13 会启动失败）：
  一个成员一个工作区 `<global_home>/agents/<member_id>/reme`（PER_AGENT，跨项目 / 跨会话召回），
  `top_k=5`，BM25 关键词检索、**不启用 embedding**、不新增凭据面；provider 不能携带工具时用
  `static_control`（不暴露 `memory_search`）。记忆**不经过文件工具**：删除 Markdown 写入白名单，
  DM 只有卸载目录的只读 `Read`，Docker 成员家目录回到 `:ro`。
- **依据**：ADR-0034（修订 ADR-0030 / ADR-0032 的记忆相关结论）；`app/alibot/memory.py`；
  `app/alibot/agent_seam.py`（每轮装配、关闭在 model client 之前）；`app/alibot/runtime_files.py`。
- **文档原话**：「AgentScope middleware that adds long-term memory backed by ReMe … ReMe is embedded
  **in-process** (no separate service)」— 2.0.10 wheel 的 `ReMeMiddleware` docstring；能力页
  `zh/versions/2.0.10/building-blocks/long-term-memory.md`。
- **理由**：记忆是模型的长期偏好与经验，不是消息正本；消息仍在 SQLite，记忆始终开启（ADR-0035），
  但也只写成员工作区、不碰项目根。写回与检索由中间件负责，模型不再维护
  索引文件；作用域从「项目 / 全局两处」收窄为「一个成员一个工作区」，因此没有项目过滤（跨项目召回）。
- **再评估条件**：默认开启已经落地（ADR-0035）；当记忆需要跨成员共享、需要向量召回（RAG）或更强
  隔离时，再评估 embedding 与隔离策略；若框架把 ReMe 迁出 `middleware` 顶层模块，按 §1.3 开票调整白名单。

---

## D13 装配阶段确定性启动 ReMe，调用私有 `_ensure_started()`

- **偏离点**：`alibot.memory.assemble_memory` 在返回前调用
  `ReMeMiddleware._ensure_started()`（pinned 2.0.10 的私有成员），并包一层
  `asyncio.shield`；取消时先让启动跑完，再由装配层 `close()` 收口。上游的设计是「第一个 job
  懒启动」，本仓改成「装配即启动」。
- **依据**：`app/alibot/memory.py`（`_start_middleware` / `assemble_memory`）；
  `app/tests/test_runtime_memory.py::test_a_cancelled_assembly_does_not_leak_the_tokenizer_file`；
  issue #267 的远端 CI 红（`PytestUnraisableExceptionWarning`：`reme/components/tokenizer/stopwords`
  未关闭句柄）；§1.2 只禁 1.x，私有成员属于「文档没写，按 §2.3 wheel 实测」的处置。
- **文档原话**：「ReMeMiddleware … The app is built lazily and started once (idempotent guards),
  since middleware has no framework-managed lifecycle.」— 2.0.10 wheel 的
  `_middleware.py::__init__` docstring；`close()` 只在 `self._started` 为真时关应用。
- **理由**：本轮检索是**可被取消的后台 task**（`on_reply` 的 `finally` 会 cancel + await）。
  取消若落在上游 `aiofiles` 的 `AiofilesContextManager.__aenter__` 里，`_open` 已在线程池
  打开的文件没人关；`filterwarnings = ["error"]` 下会在后续任意测试里炸成
  `PytestUnraisableExceptionWarning`（远端 CI 13 failed）。把启动挪到装配阶段（主任务）并
  shield，取消窗口里不再有「打开中的句柄」；启动失败则降级为「这一轮没有记忆」并记日志。
- **再评估条件**：当 `ReMeMiddleware` 公开启动生命周期（构造参数 / 公开 `start()` /
  `async with`）**或**升级到 2.0.11+ 时 `_ensure_started` 改名/变签名（升级票必须重核对本行）；
  上游修好「取消时关闭已打开句柄」后，可回退到懒启动并删掉 `shield` 包装。

---

## D14 gateway venv 在 `extra_pip` 接缝上自钉 agentscope 版本 + 补 `httpx2`

- **偏离点**：`alibot.workspace.docker_gateway_extra_pip()` 往上游 `DockerWorkspace(extra_pip=[…])`
  里塞两项：`agentscope==<本进程安装版本>` 与 `httpx2>=2.0`。上游自己没有这一步——它的镜像 install
  块是 `RUN uv pip install --no-deps "agentscope"`（`_make_dockerfile.py:165`），**不带任何版本约束**。
- **依据**：`app/alibot/workspace.py` 的 `docker_gateway_extra_pip`
  （装配点在 `assemble_docker_workspace` 的 `options`）；判据是
  `app/tests/test_docker_gateway_requirements.py`（三条，全部走上游真实构建接缝
  `prepare_build_context` 读它生成的 `requirements.txt`）；issue #312。
- **文档原话**：「Extra Python packages installed into the gateway venv at image-build time」—
  2.0.10 wheel 的 `DockerWorkspace.__init__` 对 `extra_pip` 的 docstring；同 wheel 的
  `_utils._GATEWAY_BASE_REQUIREMENTS` 注释承认这个漏洞：*"``agentscope`` goes in with ``--no-deps``,
  so anything it imports has to be named here"*——它只列了四项
  （`mcp<2.0.0` / `uvicorn` / `fastapi` / `httpx`），是**打包那一版**的快照。
- **理由**：两个上游事实叠起来会让「本仓 pin 住 AgentScope」这件事在容器里失效：
  ① install 块不锁版本 ⇒ 镜像装的是**构建当时 PyPI 的最新版**；② 基础清单只是上游打包那一版的快照。
  2026-10-27 那轮 CI 就是这么坏的：2.0.11 于 02:54Z 发布，03:10Z 那轮 PR 的容器装到 2.0.11，它的
  `agentscope/mcp/_mcp_client.py:12` 是 `import httpx2`，而基础清单四行里没有 ⇒ gateway 启动即
  `ModuleNotFoundError: No module named 'httpx2'`（8 条 docker 用例红）。同一镜像在 02:31Z 那轮是绿的
  （2.0.11 还没发）——**不是 flake，是版本脱钩**，只是被镜像 tag 缓存藏起来了。
  实测（删镜像后真实 build + 容器内 `from agentscope.mcp import MCPClient`）：
  无 `extra_pip` ⇒ 红（缺 `httpx2`）；只加 `httpx2` ⇒ **仍红**（装到 2.0.11，换报
  `ImportError: cannot import name 'Client' from 'mcp'`）；加 `agentscope==2.0.10` ⇒ 绿。
  所以「只补缺包」治不了根——上游每发一版，缺的包就会换一个。
  版本从**已安装的分发元数据**取而不写字面量：写字面量等于在代码里放第二份 pin，下次升 pin 忘改这里
  就是本仓自己脱钩。
- **再评估条件（升级时的复核动作）**：每次升 AgentScope 时，按顺序核对三件事——
  ① 新 wheel 的 `agentscope/workspace/_docker/_make_dockerfile.py` 的 install 块是否已带版本约束；
  ② 新 wheel 的 `_utils._GATEWAY_BASE_REQUIREMENTS` 是否已含 `httpx2`；
  ③ `app/tests/test_docker_gateway_requirements.py` 三条是否仍绿。
  ①② **都补齐** ⇒ 删掉 `docker_gateway_extra_pip()` 里的版本项（约束由上游承担）与 `httpx2` 项
  （清单已覆盖），本条目随之删除；只有一项补齐 ⇒ 只删对应那一项，并在本条里改注。
  反向：若上游把 `extra_pip` 参数改名或移除，本票的接缝就没了，必须按 §1.3 开票重新选位
  （`workspace.py` 里 `#187` 覆写私有 `_create_and_start_container` 是同类先例）。
