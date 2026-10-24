# 长期记忆换到 ReMe：一个成员一个工作区，跨项目召回，文件工具不再写记忆

用户于 2026-10-24 人定：长期记忆不再用「`MEMORY.md` + `<topic>.md` 的 Markdown 文件白名单」，
改用 AgentScope 的 **ReMe** 记忆中间件（`agentscope[memory-reme]==2.0.10`；该 extra 的
`reme-ai>=0.4.0.6` 是 floor，本仓另 pin 实测可用的 `reme-ai==0.4.1.10`——0.4.1.13 与 2.0.10 的
最小 ReMe 配置不兼容，嵌入应用启动即抛 `Unregistered backend 'dream_topics_step'`）。本 ADR 记录
这一个决定（作用域、检索模式、生命周期、schema 迁移与家目录形状），不记录实施步骤。

## 决策记录

- **Context**：ADR-0030 用 `AgenticMemoryMiddleware` 把记忆写成项目 / 全局两处的 Markdown 文件，
  ADR-0032 又把落点摊平成家目录根下的 `MEMORY.md` 与 `<topic>.md`，并为此引入了**记忆写入白名单**
  （文件工具的可写例外、DM 的 `Write`/`Edit`、Docker 的 `rw` 挂载）。这套形态有三个持续成本：
  模型要自己维护索引文件（写错索引就召不回）、召回是全文注入（窗口随记忆增长）、白名单是一条
  与「文件工具只有一个工作根」相冲突的第二套权限面。AgentScope 2.0.10 已把 ReMe 作为
  `middleware` 的公开成员（`ReMeMiddleware`），写回自动、检索有索引（BM25），不需要模型维护文件。
- **Decision**：`memory_mode` 的开启档从 `agentic` 改为 `reme`。每轮按**本轮模型**装配一个
  `ReMeMiddleware`，工作区固定在全局层 `<global_home>/agents/<member_id>/reme`：**一个成员一个
  工作区**（IsolationPolicy.PER_AGENT 语义），跨 session / target / 项目共享召回。记忆**不再**经
  文件工具读写，因此删除记忆白名单、DM 的 `Write`/`Edit` 与 Docker 成员家目录的 `rw` 挂载。
- **Consequences**：召回从「注入一个索引文件」变成「检索若干记忆卡片」，写入从「模型显式编辑
  `MEMORY.md`」变成「轮次收尾自动 `auto_memory`」；旧的 `MEMORY.md` / `<topic>.md` 不再是落点，
  升级时**原样留在磁盘上、不自动导入**。代价是记忆跨项目共享（没有项目过滤），且 ReMe 的嵌入应用
  每轮都要显式关闭。

## 一、装配与工作区形状

| 项 | 取值 | 说明 |
| --- | --- | --- |
| 档位词表 | `off` / `reme` | 装配层与 `PATCH /api/agents/{id}` 共用 `db.MEMORY_MODES`；库级 CHECK 是它的持久化副本 |
| 工作区 | `<global_home>/agents/<member_id>/reme` | `memory_workspace_dir()`；经 `ensure_state_partition` 建目录并拒绝根外 symlink 祖先 |
| 工作区 id | `safe_partition_id(member_id)` | `memory_workspace_id()`：纯函数，不含 session / target / 项目 |
| 模型 | 本轮 `Agent` 的同一个模型实例 | `ReMeMiddleware.Parameters(chat_model=model)`，固定于构造时 |
| 检索条数 | `top_k = 5` | 也是 `memory_search` 的默认 `limit` |
| 装配结果 | `MemoryAssembly(middleware, tools, workspace_dir, mode)` | `setup` 每轮一次，`close()` 每轮一次 |

家目录形状随之变为 `AGENTS.md` + `reme/`（应用自管，文件工具不可写）+ `skills/` + `mcp.json`。
`reme/` 与 `skills/`、`mcp.json` 一样不属于文件工具的输入面：文件工具的唯一根仍是频道项目根。

未接线全局层（`global_home is None`）时 `assemble_memory` 返回 `None` 并记一条可读日志：不装中间件、
**不建目录**。工作区不可建（根外 symlink 祖先、无权限）时同样按「这一轮记忆缺席」降级，不改变
本轮结论。

## 二、作用域：一个成员一个工作区（PER_AGENT），跨项目

ReMe 的检索覆盖整个 `workspace_dir`，写回只按 `session_id` 隔离，**没有项目过滤**。按 AgentScope
workspace-manager 文档的 `IsolationPolicy.PER_AGENT` 语义，本仓把工作区定在成员级：

- 同一 member 的所有 session / target / 项目共享一个 `reme/`；
- 不同 member 的工作区互不可见（不同目录）；
- 记忆不属于任何项目，也不随项目删除。

这**修订**了两条既有结论（只改记忆相关部分）：

- ADR-0030 的「项目记忆按工作区根隔离」不再成立：没有项目侧记忆，也就不存在「项目记忆 / 全局
  记忆」的分流；
- ADR-0032 的「记忆两处可写、按属于项目 / 全局分流」与家目录根下的 `MEMORY.md` / `<topic>.md`
  落点被替换为单个 `reme/` 工作区；ADR-0032 的会话状态落点、规则文件路径与扁平家目录结论保留。

## 三、检索模式与工具

`mode` 由 provider 的工具能力决定（与沉默工具、联网工具同一道 `supports_tool_calling` 闸门）：

| provider 能携带工具 | `mode` | 记忆工具 |
| --- | --- | --- |
| 是 | `both` | 每轮回复前自动检索注入，并把 `memory_search` 放进 toolkit |
| 否 | `static_control` | 只做自动检索注入；`memory_search` 不送出去 |

- **不启用 embedding**：不传 `embedding_model`，检索走 ReMe 内置的 BM25 关键词索引。因此本仓
  **不新增任何凭据面**（ReMe 的 LLM 用本轮模型注入，不读 `LLM_*` 环境变量）。
- 写回在**所有**模式下都自动发生（`auto_memory`），没有「写记忆」工具；`memory_search` 是唯一的
  模型可调用记忆工具。
- 工具名与可导入性由一条真实 `ReMeMiddleware` 的用例守（不启动应用，只 `list_tools()`）。

## 四、生命周期与文件工具边界

- **每轮装配 / 关闭**：`assemble_memory` 在 `_prepare_runtime_options` 里跟随本轮模型构造；
  单 Agent 与 team 两处 finally 都在**关闭 model client 之前** `await memory.close()`（异常只记日志）。
  ReMe 的嵌入应用带后台索引循环，跨轮复用会让索引绑定到已关闭的 event loop，因此不缓存中间件。
- **文件工具**：恢复为「相对路径从项目根起算、绝对路径 realpath 后必须仍在根内」。删除
  `_memory_homes`、`is_memory_file_path`、`PartitionBackend` 的可写/白名单参数与 `homes=` 例外。
  无 workspace 的轮次只保留**卸载目录的只读 Read**（与记忆开关解耦）；DM 不再授 `Write` / `Edit`。
- **Docker**：成员家目录 bind 由 `rw` 改回 `ro`，公共技能库仍 `ro`。记忆由宿主进程内的 ReMe 写
  宿主路径，容器不需要写权限。
- **项目层规则文件**：`<项目根>/.alibot/agents/<member_id>/AGENTS.md` 在项目根内，写入由项目权限档
  决定（旧的白名单曾把它钉成只读）。它不是记忆落点，规则仍由宿主只读读取进 prompt。

## 五、schema 23 → 24 与升级

- `SCHEMA_VERSION = 24`，`MigrationStep(23, 24, ...)` 严格 +1；`_migration_history.py` 补第 24 项留档。
- `agent_config` 重建（SQLite 改列级 CHECK 只能重建）：`CHECK (memory_mode IN ('off', 'reme'))`。
- 存量 `agentic` **显式映射**为 `reme`：这是同一个决策换了实现（「这个成员有长期记忆」），不能
  静默降级成 `off`。映射写在重建的复制语句里（先改旧表会被旧 CHECK 拒绝，先重建会被新 CHECK 拒绝）。
- 只前进、不回填其它列；`off` 与显式 `NULL` 逐字保留。
- **旧 Markdown 记忆不自动导入**：`MEMORY.md` 与 `<topic>.md` 既不被注入、也不被迁移，升级后原样
  留在项目家目录 / 全局家目录；要沿用旧内容需人工整理成新记忆。ReMe 也不会读取它们。

## 六、与既有 ADR 的逐句关系

| ADR | 原句 | 处置 |
| --- | --- | --- |
| ADR-0030 | 「记忆落点 `<root>/.alibot/memory/<member_id>/Memory/`」「项目记忆按工作区根隔离」 | **替换**：一个成员一个 ReMe 工作区（全局层），跨项目召回 |
| ADR-0030 | 「DM 的 Write/Edit 及额外工作目录只覆盖记忆目录」 | **删除**：记忆不经文件工具，DM 只有卸载目录的只读 Read |
| ADR-0031 §三 | 成员家目录与技能库只读挂到 `/alibot-home` | **恢复**为两条都 `ro`（ADR-0032 曾把成员家目录改成 `rw`） |
| ADR-0032 §三 | 「记忆两处可写……写入白名单」 | **替换**：单工作区 + 自动写回，无白名单 |
| ADR-0032 §一 | 家目录形状含 `MEMORY.md` / `<topic>.md` | **替换**为 `AGENTS.md` + `reme/` + `skills/` + `mcp.json` |
| ADR-0032 §四 | 会话状态落点 | **保留**（`sessions/` 仍在项目根或数据目录） |
| ADR-0032 §五 | Docker 成员家目录 `:rw`、家目录作为额外可写目录 | **删除**：`:ro`，家目录不进入文件工具面 |
