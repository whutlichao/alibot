# Agent 家目录与全局层 —— 设计笔记（决策账 + 规格输入）

- 本票类型：**设计票**，门禁 **G-docs**。**零源码改动**：不改 `.py`、不改测试、不写迁移代码、
  **不 bump `SCHEMA_VERSION`**。
- 本票**不跑**：`pytest` / `ruff check` / `ruff format --check` / `mypy` / `pyright` / `node --test`、
  G-impl、双轴源码 review（diff 只有 ADR、设计笔记与 `CONTEXT.md` 词条）。
- 前置 skill：`grill-with-docs`（已按要求依次加载 `grilling` 与 `domain-modeling`）。
- 提问通道：`orca orchestration ask`（禁 `AskUserQuestion`）；两轮均一次应答、无超时、无重复提问。
- 交付正本：`docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md`（ADR）与
  本文件（设计规格）。`CONTEXT.md` 的词条决议 inline 落盘，无「待定」占位。

---

## 0. 开工核对的事实（只读取证，不是决策）

### 0.1 现有项目层与派生路径

| 事实 | 落点（实测行号） |
| --- | --- |
| `.alibot` / `agents` / `sessions` 三个目录名常量 | `app/alibot/workspace.py:42-44` |
| `_AlibotLayoutMixin` 把框架状态收进 `<workdir>/.alibot`：`skills/` → `.alibot/agents/`、`sessions/` → `.alibot/sessions/`、`data/` → `.alibot/data/`、`.mcp` → `.alibot/mcp.json` | `workspace.py:267-303` |
| 本地装配创建项目侧成员分区与会话分区 | `workspace.py:416-417`（`agent_dir` / `session_dir`） |
| 状态分区守卫：根外 symlink 祖先在**创建前**拒绝 | `workspace.py:393-403`（`ensure_state_partition`） |
| 文件工具根检查：`realpath` + 根内判定 | `workspace.py:223-264`（`WorkspaceRootGuard`） |
| 技能分区路径 `.alibot/skills/<scope_id>` | `app/alibot/skills.py:52-53`、`:97-99` |
| 技能装载：校验 `SKILL.md`、路径与内容 hash，坏条目跳过 | `skills.py:539-580` |
| 记忆分区 `.alibot/memory/<member_id>/Memory/`，只读附加目录机制 | `app/alibot/memory.py:26-55`（相对路径在 `:27`） |
| 会话状态 `.alibot/sessions/<session_id>/` | `app/alibot/runtime.py:130-137`（`SessionState`）、`:79-96`（`SessionOffloader`） |
| 数据目录默认 `~/Alibot`，`ALIBOT_DATA_DIR` 可覆盖 | `app/alibot/db.py:138-140`、`:1200-1205` |

### 0.2 现有合并语义（可直接沿用的形状）

| 对象 | 去重键 | 现有优先级 | 落点 |
| --- | --- | --- | --- |
| 技能 | `name` | Agent 级覆盖频道级 | `app/alibot/db.py:3814-3845`（`skills_for_target`） |
| MCP 声明 | `name` | Agent 级覆盖频道级 | `db.py:3571-3600`（`mcp_servers_for_target`） |
| 权限规则 | `(tool_name, pattern)` | Agent 级覆盖频道级 | `app/alibot/permissions.py` 的 `effective_rules` |

技能装配到框架由 `agent_seam.py:853-872`（`_skill_rows` / `_skills_for`）完成；MCP 装配由
`agent_seam.py:821-851`（`_assemble_mcp`）完成。两者都只返回**胜出的那一条**，落选条目不进入
工具清单——这是本设计可以直接复用的「同名只留一条」可观察行为。

### 0.3 现有规则与家目录：不存在

仓库里没有「规则文件」这个概念：`grep` `AGENTS.md` 在 `app/alibot/*.py` 零命中；系统提示的基座
只有库内 `agent_config.system_prompt`，运行时由 `agent_seam.py:2048-2155`（`_system_prompt`）
拼装，`base` 取自 `agent_config.system_prompt`（`:2089`）。因此「全局规则基座」与「项目规则补充」
都是**纯新增**，不改既有拼装函数的输入语义，只在 `base` 之前与之后各加一段。

### 0.4 数据目录与备份的现状

| 事实 | 落点 |
| --- | --- |
| 默认目录名 `Alibot`（可见、可直接复制） | `db.py:138`、`:1200-1205` |
| 数据目录 = `alibot.db` + `attachments/` + `schema-migrations.jsonl`，是备份与搬迁单位 | `CONTEXT.md` 的 **数据目录**、**备份** 词条；`app/README.md:72-86` |
| 项目根不在数据目录内，备份数据目录 ≠ 备份项目文件 | `CONTEXT.md` 的 **工作区项目根** 词条；`app/README.md:163-164` |
| DM 的记忆根是数据目录（无项目根时 `_runtime_root`） | `agent_seam.py:766-778`；`app/README.md:215` |

### 0.5 远端 MCP 确认的库形态

| 事实 | 落点 |
| --- | --- |
| `remote_consent_scope(scope_kind, scope_id, acknowledged_at)`，`scope_kind` CHECK 只允许 `model/mcp/docker/web` | `db.py:745-750`；常量 `db.py:170` |
| `scope_id` 是自由文本，只要求非空；按 `(scope_kind, scope_id)` 幂等 | `db.py:1844-1878` |
| MCP 确认的人类面入口按库内 `server_id` 查行 | `app/alibot/api.py:1209-1218` |

**由此得到的规格结论**：全局 MCP 的合成标识 `global:<member_id>:<name>` 可以直接写进
`('mcp', scope_id)`，**不需要新增 `scope_kind`、不需要改表、不需要 bump `SCHEMA_VERSION`**。
若实施时改成新增 `scope_kind`，则必须重建带 CHECK 的表——本设计明确避开这条更贵的路。

### 0.6 Docker 挂载现状与票面行号偏差

| 事实 | 实测落点 |
| --- | --- |
| 容器配置只生成一条 bind：`host_workdir:/workspace:rw` | `workspace.py:450-481`，bind 在 `:478-480` |
| 装配时传入 `host_workdir=root` | `workspace.py:832-836` |
| 容器内工具根检查只接受 `/workspace` 及其子路径 | `workspace.py:499-532`；`ContainerRootGuard` 在 `:535-543` |
| **票面偏差**：票面写「Docker 只把频道根挂到 `/workspace`（`workspace.py:529-532` 附近）」；`529-532` 实际是 `resolve_in_container_root` 的 `realpath` 根检查，真正的挂载是 `:478-480` | 本文件按实测行号引用 |

### 0.7 ADR 与词条现状

| 事实 | 落点 |
| --- | --- |
| `docs/adr/` 最大编号 0030；本票占用 0031 | `docs/adr/` |
| ADR-0021 正文明确「项目根是频道级，所有成员共享」 | `docs/adr/0021-workspace-is-an-explicit-project-boundary.md:15-23` |
| ADR-0014 明确选择「可见、可直接复制」的 `~/Alibot` | `docs/adr/0014-attachments-are-separate-objects-in-the-data-directory.md:7` |
| **Agent** 词条仍写「首版没有家目录、项目目录与沙箱」 | `CONTEXT.md` 的 **Agent** 词条 |
| 数据目录词条写默认 `~/Alibot` | `CONTEXT.md` 的 **数据目录** 词条 |
| 仓库没有 ADR 索引文件，`docs/agents/domain.md` 已泛指针到 `docs/adr/` | `docs/agents/domain.md:10-12` |

因此本票**不需要**改 `docs/agents/domain.md`（没有新的目录或索引体例要求）。

---

## 1. 决策账（Q → A → 归属 → 理由）

### 1.1 grill 轮次与应答去向

| 轮 | 覆盖 | 结论 | 归属 | 应答去向 |
| --- | --- | --- | --- | --- |
| 1 · Q1 | Docker 后端如何看见全局层 | 只读挂载成员家目录 + 公共技能库 | **人定** | ADR §三；本文件 D1、§5 |
| 1 · Q2 | 数据目录迁移与兼容 | 兼容优先 + 显式迁移，绝不自动搬 | **人定** | ADR §五；本文件 D2、§6 |
| 1 · Q3 | 全局技能/MCP 的来源 | 纯文件系统人工维护，不新增 DB scope / 安装端点 | **人定** | ADR §一/§二；本文件 D3、§8 |
| 1 · Q4 | 备份单位 | 保持数据目录为备份单位；全局层单独备份 | **人定** | ADR §五；本文件 D4、§6 |
| 1 · Q5 | 同名合并优先级 | 项目 Agent > 项目频道 > 全局成员 > 全局公共 | **代答** | ADR §二；本文件 D5、§3.2/§3.3 |
| 1 · Q6 | 各层的生效范围 | 规则每轮（含 DM）；技能/MCP 需 workspace；全局记忆可读（含 DM） | **人定** | ADR §二；本文件 D6、§3 |
| 2 · Q7 | 全局 MCP 是否允许远端 | 允许远端 + 合成确认键 `global:<member>:<name>` | **人定** | ADR §四；本文件 D7、§3.3、§5.3 |
| 2 · Q8 | 项目规则文件落点 | `<root>/.alibot/rules/<member_id>/AGENTS.md` | **代答** | ADR §一；本文件 D8、§3.1 |
| 2 · Q9 | 全局记忆与 DM 写入落点 | 全局 `memory/Memory/` 只读；DM 写数据目录侧 | **代答** | ADR §二；本文件 D9、§3.4 |
| 2 · Q10 | 全局目录的创建纪律 | 应用绝不自动创建或写入全局目录 | **代答** | ADR §三；本文件 D10、§4.3 |

两轮 ask 的答复都明确区分了「人定」与「代答」；本文件与 ADR 按该归属记录。Q5 的答复原文明确
「依据用户本轮之前已确认『项目覆盖全局』与『记忆默认写项目』」，因此记为**代答**而不是人定。

### 1.2 裁定表

#### D1 · Docker 后端 — **额外只读挂载全局层**（人定）

把 `~/.alibot/agents/<member_id>/` 与 `~/.alibot/skills/` 只读挂到容器内固定路径
（设计用 `/alibot-home`），不挂 `~/.alibot/data/`。文件工具的根检查仍只认 `/workspace`；全局挂载
不是文件工具的合法输入，只供框架的技能装载器与记忆中间件读取。

理由：local 与 docker 行为必须一致；全局记忆天然只读的语义由只读挂载在容器边界上兑现。
代价：多一个挂载、容器配置与根检查的测试面扩大；全局条目在容器内不可写，符合预期。

#### D2 · 数据目录 — **兼容优先 + 显式迁移**（人定）

默认解析顺序：`--data-dir` / `ALIBOT_DATA_DIR` → `~/.alibot/data/`（已存在）→ `~/Alibot`（已存在，
兼容读取并给可读提示）→ 新建 `~/.alibot/data/`。**绝不自动搬移用户数据**；搬迁是显式动作，
必须整目录备份。旧 `~/Alibot` 用户的私信记忆路径随数据目录一起保留。

#### D3 · 全局技能/MCP 的来源 — **纯文件系统，人工维护**（人定）

不新增 DB `scope='global'`、不新增安装端点、不把全局技能写进 `skill` 表。目录与文件由人创建；
应用只读、只校验、坏条目跳过并记日志。UI 安装/编辑列入 Out of Scope。

#### D4 · 备份单位 — **保持 ADR-0014 的口径**（人定）

数据目录（`~/.alibot/data/` = `alibot.db` + `attachments/` + 台账）仍是备份单位。全局层
（`agents/`、`skills/`）不在其中，必须单独备份；文档与启动提示要写明这一点。

#### D5 · 同名合并优先级（代答）

技能：项目 Agent 级 > 项目频道级 > 全局成员家目录 > 全局公共技能库。MCP：项目 Agent 级 >
项目频道级 > 全局成员家目录。规则：全局基座 → `agent_config.system_prompt` → 项目补充，后者冲突时
胜出。去重键：技能与 MCP 都是 `name`。

#### D6 · 生效范围（人定）

- 规则：每一轮都生效（含 DM）；项目补充只在有项目根时存在。
- 技能/MCP：只在挂载了 workspace 的轮次参与装配（沿用现有 DM 无技能/MCP 的行为）。
- 记忆：`memory_mode=agentic` 的任何轮次都可读全局记忆（含 DM）；写入始终落项目侧，DM 无项目时
  沿用现有数据目录下的成员记忆。

#### D7 · 全局 MCP 远端 — **允许，但必须逐项确认**（人定）

全局 `mcp.json` 的 `http` / `sse` 条目首次使用前，按合成标识
`global:<member_id>:<name>` 写 `remote_consent_scope('mcp', …)`。不新增 `scope_kind`、不改表。
`env_names` 仍只存变量名，值只从进程环境读。这是对「MCP/Skill 只接受本机来源」口径的**扩展**，
ADR 已记录数据面扩大；实施票需要新增一个人脸确认入口（按成员 + 名字），入口形态属实施细节。

#### D8 · 项目规则落点（代答）

`<workspace_root>/.alibot/rules/<member_id>/AGENTS.md`。理由：`.alibot/agents/<member_id>/` 已经是
「AgentScope 技能分区 + stdio MCP cwd」，把规则文件塞进去可能被技能扫描当成技能目录；`rules/` 是
`.alibot/` 下的纯增量兄弟目录，不搬家、不改名任何既有结构。

#### D9 · 全局记忆与 DM 写入（代答）

全局记忆 = `~/.alibot/agents/<member_id>/memory/Memory/`（框架记忆中间件的 `workdir` 是
`~/.alibot/agents/<member_id>/memory`）。DM 无项目根时，写入沿用
`<data_dir>/.alibot/memory/<member_id>/Memory/`；全局家目录只读，不承接 DM 写入。

#### D10 · 全局目录创建纪律（代答）

应用**绝不**自动创建或写 `~/.alibot/agents/<member_id>/`、`~/.alibot/skills/`。目录缺失 = 该层
缺席：规则为空段、技能零条、记忆不挂只读目录；只记可读提示，不报错、不阻断本轮。

---

## 2. 被否方案与代价（汇总表）

| 被否方案 | 代价/风险 | 结论 |
| --- | --- | --- |
| 全局层塞进项目根 | 无法跨项目复用；污染用户项目 | 否 |
| 全局层成为文件工具的第二工作根 | 根检查、权限档、Docker 挂载同时复杂化；违反「文件工具路径只在项目根」 | 否 |
| 全局记忆可写 | 跨项目隐式共享写竞争；违背「全局层由人维护」 | 否 |
| Docker 只保留项目层（全局层仅 local） | 切换后端静默丢失全局技能与记忆 | 否 |
| 启动时自动搬 `~/Alibot` → `~/.alibot/data` | 不可逆移动用户数据；违背显式迁移纪律 | 否 |
| 全局 MCP 静默允许远端 | 绕过数据面确认 | 否 |
| 新增 DB `scope='global'` + 安装端点 | 扩大 schema 与人类面；首版无此需要 | 否（Out of Scope） |
| 全局技能写进 `skill` 表并存 hash | 需要新的 scope 形态与安装路径；人工维护层不需要 | 否 |
| 复用 `<root>/AGENTS.md` 作为项目规则 | 与用户自己的工具配置混淆；把 alibot 状态写进用户业务文件 | 否 |

---

## 3. 加载顺序与冲突矩阵

### 3.1 规则

固定顺序（逐对：配置覆盖段胜过全局基座，项目补充段胜过前两段）：

1. 全局基座 `~/.alibot/agents/<member_id>/rules/AGENTS.md`；
2. 库内 `agent_config.system_prompt`；
3. 项目补充 `<workspace_root>/.alibot/rules/<member_id>/AGENTS.md`。

三段都是文本原样拼接，带可读段落标签；不解析、不裁剪、不做语义合并。既有的回应 rubric、角色句、
任务上下文仍按 `_system_prompt` 的现有顺序排在**配置覆盖段之后**——本设计只在 `base` 前后插入两段，
不改 rubric 与纪律句。

| 场景 | 结果 |
| --- | --- |
| 三段都有 | 全局 → 配置 → 项目；项目段最靠后 |
| 无项目根（DM） | 全局 → 配置 |
| 全局文件缺失 | 配置 → 项目 |
| 项目文件缺失 | 全局 → 配置 |
| 文件不可读 / 非 UTF-8 | 该段缺席 + 可读日志；本轮继续 |
| 两段规则都缺且 `system_prompt` 为空 | 没有规则正文；既有 rubric 与身份句照旧 |

### 3.2 技能

去重键 = 技能名。优先级从高到低：项目 Agent 级 > 项目频道级 > 全局成员家目录 > 全局公共技能库。

| 来源组合（同名声明的集合） | 胜出 | 可观察行为 |
| --- | --- | --- |
| 项目 Agent + 项目频道 | 项目 Agent | 频道条目正常存在但不装配；工具/技能清单只出现一次 |
| 项目任一级 + 全局成员 | 项目 | 全局条目不装载；记一条运行期提示 |
| 全局成员 + 全局公共 | 全局成员 | 公共条目不装载 |
| 项目 + 全局成员 + 全局公共 | 项目 | 两个全局条目都不装载 |
| 只有全局公共 | 全局公共 | 正常装载 |
| 同一 scope 内重名 | 沿用既有约束 | 沿用既有行为，不新增规则 |
| 全局条目 `SKILL.md` 非法 / 路径越界 / 不可读 | 该条目跳过 | 其余技能照常；记日志 |

### 3.3 MCP

去重键 = 声明名。优先级：项目 Agent 级 > 项目频道级 > 全局成员家目录。

| 场景 | 结果 |
| --- | --- |
| 项目同名 + 全局同名 | 项目胜出；全局条目不连接、不进工具清单 |
| 只有全局条目（stdio） | 正常连接；`cwd` 仍是项目侧成员分区（Docker 内是 `/workspace`） |
| 只有全局条目（远端，未确认） | **不连接**；运行时读数给可读原因（`consent_required`） |
| 只有全局条目（远端，已确认） | 正常连接；确认键 `global:<member_id>:<name>` |
| 全局 `mcp.json` 缺失 | 全局层零条，不报错 |
| 全局 `mcp.json` 非法 JSON / 条目形状不合法 | 跳过该条目并记日志；不影响项目层 |

### 3.4 记忆

| 场景 | 读 | 写 |
| --- | --- | --- |
| 有项目根 + `memory_mode=agentic` | 项目记忆 + 全局记忆（只读附加目录） | 项目记忆 |
| DM（无项目根）+ agentic | 数据目录侧成员记忆 + 全局记忆（只读） | 数据目录侧成员记忆 |
| 全局记忆目录缺失 | 只读项目侧；不报错 | 项目侧 |
| 全局记忆不可读 | 跳过全局侧 + 日志 | 项目侧 |
| `memory_mode=off` / null | 不挂中间件、不建目录 | — |

**并发后果**：同一成员同时最多一轮 in-flight（既有编排约束），应用侧写入天然串行；同一项目根内
同一成员的多个 target 共享同一记忆目录（ADR-0030）。跨进程同时打开同一项目根不在支持范围。
全局记忆没有应用写入，因此不存在应用侧的全局写竞争；人对全局记忆的编辑发生在应用之外，不属于
本设计的并发面。

### 3.5 会话状态

`sessions/` 只在项目层；全局层没有 `sessions/`，也不得共享。它是运行状态，不是记忆或规则。

---

## 4. 目录清单与创建纪律

### 4.1 全局层与数据目录（`~/.alibot/`）

| 路径 | 内容 | 谁创建 | 谁写 |
| --- | --- | --- | --- |
| `agents/<member_id>/rules/AGENTS.md` | 规则基座 | 人 | 人 |
| `agents/<member_id>/skills/<name>/SKILL.md` | 成员私有全局技能 | 人 | 人 |
| `agents/<member_id>/memory/Memory/` | 全局记忆 | 人 | 人 |
| `agents/<member_id>/mcp.json` | 全局 MCP 声明 | 人 | 人 |
| `skills/<name>/SKILL.md` | 跨项目公共技能库（可选） | 人 | 人 |
| `data/` | `alibot.db` + `attachments/` + 台账 | 应用（首次启动） | 应用 |

### 4.2 项目层（`<workspace_root>/.alibot/`）

| 路径 | 内容 | 状态 |
| --- | --- | --- |
| `agents/<member_id>/` | AgentScope 技能分区 / stdio MCP cwd | 既有，不动 |
| `skills/<scope>/<name>/` | 项目技能字节 | 既有，不动 |
| `memory/<member_id>/Memory/` | 项目记忆 | 既有，不动 |
| `sessions/<session_id>/` | 会话状态与卸载文件 | 既有，不动 |
| `mcp.json` | 项目 MCP 声明 | 既有，不动 |
| `rules/<member_id>/AGENTS.md` | 项目规则补充 | **纯新增** |

### 4.3 创建纪律

- 全局层：应用**只读**，绝不自动创建或写入（D10）。缺失即缺席，不报错。全局层根固定为
  `~/.alibot/`，**不随 `--data-dir` 移动**；`--data-dir` 只改变数据目录本身。
- 项目层既有目录：维持既有创建与守卫语义（`ensure_state_partition`）。
- 数据目录：应用在首次启动时按 D2 的解析顺序创建/使用；旧位置只做兼容读取。

---

## 5. 安全与后端边界（规格）

### 5.1 全局层守卫

- 全局层不是文件工具的根：Read/Write/Edit/Glob/Grep 的相对路径仍从项目根起算，根检查不变。
- 新增一道**只读**守卫：以自己的根（`~/.alibot` 或成员家目录）做 `realpath`，拒绝根外 symlink
  祖先与根外目标；它不提供项目根那样的写分区。
- 技能由框架技能装载器直接读文件；规则由宿主读出后作为提示文本注入；记忆以只读附加目录形式挂给
  记忆中间件。三者都不把全局层暴露成文件工具的合法输入。

### 5.2 Docker

- 额外只读 bind：`~/.alibot/agents/<member_id>/` 与 `~/.alibot/skills/` → 容器内固定路径
  （如 `/alibot-home`）。
- **不挂** `~/.alibot/data/`：库与附件字节不进容器。
- 容器内文件工具的根检查仍只认 `/workspace`；`/alibot-home` 是框架级只读输入，不是工具根。
- 成员家目录或公共库不存在时跳过对应挂载，不报错、不创建。

### 5.3 权限档与密钥

- 三档 `read-only` / `ask-writes` / `trusted-writes` 语义不变；不引入 `BYPASS`。
- 全局层只增加只读输入，不增加可写根。
- 全局 MCP 的 `env_names` 仍只存变量名；值只从进程环境读；库与文件都不存密钥。
- 远端全局 MCP 的数据面确认：`remote_consent_scope('mcp', 'global:<member_id>:<name>')`；
  不新增 `scope_kind`，不需要 schema 变更。

---

## 6. 迁移与备份策略

### 6.1 数据目录默认值

| 优先级 | 条件 | 结果 |
| --- | --- | --- |
| 1 | `--data-dir` 或 `ALIBOT_DATA_DIR` | 使用显式值（既有语义不变） |
| 2 | `~/.alibot/data/` 已存在 | 使用新默认 |
| 3 | `~/Alibot` 已存在 | 使用旧位置 + 可读提示（兼容，不搬） |
| 4 | 都不存在 | 新建 `~/.alibot/data/` |

若第 2 与第 3 条同时成立（新旧目录都存在），使用新默认，并在启动提示里说明旧位置被忽略；不删除、
不搬移、不合并两份库。两份库同时存在时选哪一份是**确定的**，不靠时间戳猜测。

### 6.2 已有 `~/Alibot` 库的兼容路径

- 继续用旧位置即可完整工作；不需要迁移。
- 搬到新位置是**显式动作**：停应用 → 整目录复制到 `~/.alibot/data/` → 校验（库可打开、附件数量
  一致、台账存在）→ 指向新位置。复制前先做整目录备份。
- 迁移共享同一份数据目录的私信记忆路径：整目录复制会一并带走，不需要单独搬记忆。
- 本票不实现迁移命令；实施票可以选择「文档化的手工搬迁」或「显式 CLI 入口」，但**不得**在启动时
  自动搬。

### 6.3 备份说明

- 备份单位仍是数据目录：`alibot.db` + `attachments/` + `schema-migrations.jsonl`。
- 全局层（`~/.alibot/agents/`、`~/.alibot/skills/`）**不在**数据目录备份内，需单独备份。
- 隐藏目录削弱可见性，文档必须给出显式路径的备份与恢复命令；启动时照旧打印实际数据目录。

### 6.4 Schema

本设计**不需要** `SCHEMA_VERSION` 变更：合成确认键写进既有的 `('mcp', scope_id)` 自由文本列。
若实施时选择新增 `scope_kind`，那会触发带 CHECK 的表重建——本设计明确不选这条路。

---

## 7. 实施切分建议（只给切分，不建票）

| 票 | 范围 | 依赖 |
| --- | --- | --- |
| A · 全局层契约与守卫 | 全局根常量、只读守卫、目录缺失语义、`CONTEXT.md` 已落的词条对齐 | 本设计 |
| B · 规则拼装 | 三段固定顺序、项目 `rules/` 目录、缺失/不可读降级、system prompt 段落标签 | A |
| C · 技能与 MCP 合并 | 全局技能装载（纯文件、校验、跳过）、`mcp.json` 解析、四层优先级、落选提示 | A |
| D · 全局记忆只读 | 记忆中间件的只读附加目录、local/docker 可见路径映射 | A |
| E · Docker 只读挂载 | 第二个只读 bind、`/alibot-home` 映射、缺目录跳过、根检查不变 | A |
| F · 数据目录默认与兼容 | 解析顺序、旧位置提示、文档与备份指引；不自动搬 | 无 |
| G · 全局远端 MCP 确认 | 人脸确认入口、合成键写入、`consent_required` 读数 | C |

建议顺序：A →（B、C、D 可并行）→ E → G；F 独立。所有票共享一份验收口径：本文件 §9。

---

## 8. Out of Scope

- 全局技能/MCP 的 UI 安装、编辑、删除端点（纯文件系统，人维护）。
- 应用写入全局记忆；全局记忆的版本管理、冲突合并、同步。
- 启动时自动迁移 `~/Alibot`；自动创建全局目录。
- 跨项目共享 `sessions/`。
- 全局层的多用户/多租户隔离。
- 新增 `scope_kind` 或任何 `SCHEMA_VERSION` 变更。
- 修改 ADR-0021、ADR-0014 正文。
- 全局规则的热重载缓存策略（每轮装配时读取，具体缓存属实施细节）。
- 远端全局 MCP 的凭证存储（仍只从进程环境读）。

---

## 9. 规格输入清单（逐条可验收）

| 编号 | 规格 |
| --- | --- |
| C1 | 全局根为 `~/.alibot/`；成员家目录为 `~/.alibot/agents/<member_id>/`；公共技能库为 `~/.alibot/skills/`；数据目录为 `~/.alibot/data/`。 |
| C2 | 规则按「全局基座 → `agent_config.system_prompt` → 项目补充」固定顺序拼接；后段冲突时胜出；无项目根时只有前两段。 |
| C3 | 规则文件缺失或不可读 = 该段缺席 + 可读日志，不阻断本轮。 |
| C4 | 项目规则路径为 `<root>/.alibot/rules/<member_id>/AGENTS.md`。 |
| C5 | 技能优先级：项目 Agent > 项目频道 > 全局成员 > 全局公共；去重键 `name`；只装配胜出条目。 |
| C6 | 全局技能是纯文件：不进 `skill` 表、无 hash 行、无安装端点；加载时校验，坏条目跳过。 |
| C7 | MCP 优先级：项目 Agent > 项目频道 > 全局成员；去重键 `name`；落选条目不连接。 |
| C8 | 全局 MCP 声明在 `~/.alibot/agents/<member_id>/mcp.json`；`env_names` 只存变量名。 |
| C9 | 全局远端 MCP 用 `remote_consent_scope('mcp', 'global:<member_id>:<name>')` 确认；不新增 `scope_kind`、不改 schema。 |
| C10 | 记忆两处可读；写入只落项目侧（DM 落数据目录侧）；全局侧只读。 |
| C11 | 同一成员轮次串行；跨进程同项目根不在支持范围；全局侧无应用写入。 |
| C12 | `sessions/` 只在项目层，不共享。 |
| C13 | 全局层不是文件工具根；新增只读守卫做 realpath 检查；三档权限语义不变。 |
| C14 | Docker 额外只读挂载成员家目录 + 公共技能库；不挂数据目录；文件工具根仍是 `/workspace`。 |
| C15 | 数据目录解析顺序：显式覆盖 → 新默认存在 → 旧 `~/Alibot` 存在（兼容提示）→ 新建新默认。 |
| C16 | 应用绝不自动创建/写入全局目录；缺失即缺席。 |
| C17 | `CONTEXT.md` 词条与 ADR-0031 是本设计的术语与决策正本；实施票不得另造同义词。 |
| C18 | 备份单位仍是数据目录；全局层需单独备份，文档必须写明。 |

---

## 10. Answer（本票收口）

### 10.1 交付物

| 文件 | 动作 |
| --- | --- |
| `docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md` | 新增：ADR，记录一个决定（分层 + 合并语义 + 安全/Docker 边界），逐句声明与 ADR-0021、ADR-0014 的关系 |
| `app/specs/agent-home-layers-design-notes.md` | 新增：本文件（决策账、被否方案、加载顺序与冲突矩阵、迁移策略、实施切分、Out of Scope、规格输入） |
| `CONTEXT.md` | 更新相关词条：Agent、Agent 家目录、全局层/项目层、记忆落点、Skill、MCP、数据目录、备份 |
| 源码 / 测试 / 迁移 / CI | **零改动**；`SCHEMA_VERSION` 未 bump |

### 10.2 grill 轮次与每轮应答去向

| 轮 | 问题 | 归属 | 去向 |
| --- | --- | --- | --- |
| 1 | Q1 Docker 边界、Q2 数据目录迁移、Q3 全局来源、Q4 备份单位、Q5 合并优先级、Q6 生效范围 | Q1/Q2/Q3/Q4/Q6 **人定**；Q5 **代答** | ADR §二/§三/§五；本文件 §1.2 D1–D6、§3、§4、§6 |
| 2 | Q7 全局远端 MCP、Q8 项目规则落点、Q9 记忆落点、Q10 创建纪律 | Q7 **人定**；Q8/Q9/Q10 **代答** | ADR §一/§二/§四；本文件 §1.2 D7–D10、§3、§4.3、§5.3 |

两轮都经 `orca orchestration ask`，各一次应答；没有超时、没有重复提问、没有用 `AskUserQuestion`。
Q7 与推荐不同（人定允许远端），设计按人定执行并在 ADR 里显式记录数据面扩大与 schema 影响。

### 10.3 G-docs 自查（逐条对照可观察验收）

| 验收项 | 结论 | 证据 |
| --- | --- | --- |
| `docs/adr/0031-*.md` 存在，且逐句写出与 ADR-0021、ADR-0014 的关系 | 通过 | ADR §四（0021 九句逐行）、§五（0014 取舍） |
| ADR 只记录一个决定，不含实施步骤或代码 | 通过 | 全文无代码块；实施切分只在设计笔记 §7 |
| 设计规格存在，含决策账、被否方案、加载顺序、冲突矩阵、迁移策略、Out of Scope | 通过 | 本文件 §1、§2、§3、§6、§8 |
| 无悬而未决的 open 决策占位 | 通过 | 两轮 grill 全部收口；全文无「待定/TBD」 |
| `CONTEXT.md` 相关术语 inline 落盘，至少覆盖 Agent 家目录、全局层/项目层、记忆读写落点 | 通过 | `CONTEXT.md` 的 `## Language` 新增/更新词条 |
| 设计文档无草案代码块或实施步骤代码 | 通过 | 全部为段落与表格 |
| 零源码、测试、迁移、`SCHEMA_VERSION`、CI 改动 | 通过 | `git diff --stat` 只含文档；下方 §10.4 |
| PR 使用 `pr` skill 体例、`--body-file`、写明 Door 与 Blast Radius | 通过 | PR 正文与回执见 §10.4 |

### 10.4 证据与读数

| 项 | 读数 |
| --- | --- |
| 设计提交 | `c9d5291`（`docs(adr): 全局层与项目层设计（ADR-0031）`） |
| 改动 | 3 files changed, 661 insertions(+), 10 deletions(-)：`CONTEXT.md`、ADR-0031、本设计笔记 |
| 源码 / 测试 / 迁移 / CI | 零改动；`SCHEMA_VERSION` 未 bump |
| PR | **#227**（`https://github.com/whutlichao/alibot/pull/227`），base `main` |
| PR 正文 | `pr` skill 体例；本地路径 `.scratch/agent-home-layers-pr-body.md`（该目录被 `.gitignore` 忽略，不入库） |
| PR 正文要点 | Door = **two-way**；Blast Radius = **docs-only** |
| 门禁 | 本票不跑源码门禁（票面明确不跑）；G-docs 结论见 §10.3 |

开工时工作区为 clean；交付前复核 `git status`，确认分支 diff 只落在 ADR、设计笔记与 `CONTEXT.md`。

