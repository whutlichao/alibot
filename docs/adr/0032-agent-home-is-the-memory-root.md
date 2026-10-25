# Agent 家目录就是记忆根：一个 root 一份扁平家目录，全局与项目同构

用户于 2026-10-23 人定：频道没有项目目录时不该落到 `<data_dir>/.alibot/...` 这种「数据目录再套一层
`.alibot`」的镜像路径；Agent 的家目录 `~/.alibot/agents/<member_id>/` 就是它自己的根，`AGENTS.md`、
`MEMORY.md` 等直接放在这一层。本 ADR 记录这一个决定（含写入白名单、两处记忆的分流与会话状态落点），
不记录实施步骤。

## 决策记录

- **Context**：ADR-0030 把记忆落点写成唯一公式 `<root>/.alibot/memory/<member_id>/Memory/`，
  `root` 只有「项目根 / 数据目录」两种取值。ADR-0031 又把数据目录移到 `~/.alibot/data/`，于是
  没有项目根的轮次实际落在 `~/.alibot/data/.alibot/memory/<member_id>/Memory/`：内层 `.alibot` 与
  外层全局根重名，既不在任何项目里，也不是 Agent 自己的家目录，备份与排障都要靠特殊记忆。同时
  ADR-0031 定的家目录布局（`rules/AGENTS.md`、`memory/Memory/MEMORY.md`）多两层目录，与「家目录
  就是这个 Agent 的根」这个直觉不符。
- **Decision**：一个 root 一份**同构的扁平家目录**。项目层 `<workspace_root>/.alibot/agents/<member_id>/`、
  全局层 `~/.alibot/agents/<member_id>/`，两者都直接承载 `AGENTS.md` 与 `MEMORY.md`（记忆主题文件
  同层）。记忆两处**都可写**：属于项目的写项目侧，属于全局通用的写全局侧；没有项目根的轮次以全局
  家目录为唯一记忆落点。写入只允许 `MEMORY.md` 与 `<topic>.md`，`AGENTS.md`、`skills/`、`mcp.json`
  对应用只读。会话状态离开 `.alibot` 分区：项目根 `<ws>/.alibot/sessions/`、数据目录
  `<data_dir>/sessions/`。
- **Consequences**：路径不再有镜像层；同一 Agent 在任意 target 下都有唯一家目录，无项目根时也不
  例外。代价是 ADR-0031 的「全局记忆对应用永远只读」被修订为「记忆文件可写、其余只读」，
  Docker 的成员家目录挂载从 `:ro` 变 `:rw`（公共技能库仍 `:ro`），写入白名单因此成为必需的第二道闸。

## 一、两层的目录形状（逐级同构）

| 层 | 根 | 形状 | 写权限 |
| --- | --- | --- | --- |
| 全局层 | `~/.alibot/agents/<member_id>/` | `AGENTS.md`（规则基座）、`MEMORY.md`（全局记忆索引）、`<topic>.md`、`skills/`、`mcp.json` | 应用可写记忆文件；其余只读 |
| 项目层 | `<workspace_root>/.alibot/agents/<member_id>/` | `AGENTS.md`（项目补充规则）、`MEMORY.md`（项目记忆索引）、`<topic>.md` | 应用可写记忆文件；`AGENTS.md` 只读 |
| 数据目录 | `<data_dir>/` | `alibot.db`、`attachments/`、`sessions/`、迁移审计台账 | 应用写；备份与搬迁单位 |

`skills/` 与 `mcp.json` 只在全局家目录存在（ADR-0031 的既有口径不变：公共技能库
`~/.alibot/skills/` 面向全部 Agent，成员私有技能在家目录）。修订（#270）：项目层的旧技能字节仍保留在
`.alibot/skills/<scope>/<name>/`，但**不再进入装配**；现行技能字节在成员家目录
`~/.alibot/agents/<member_id>/skills/<name>/`，由宿主应用写入。

一处已知的同名空间：`_AlibotLayoutMixin` 把 AgentScope 的技能分区重定向到 `.alibot/agents/`，
于是项目层 `.alibot/agents/` 下同时出现「技能名目录」与「`mem_*` 成员家目录」。两者键空间不同
（技能名是短 slug，成员 id 是 `mem_<hex>`），冲突概率可接受；本 ADR 不改框架技能落点。

## 二、规则：两段文件规则换到扁平路径，顺序不变

系统提示的拼接顺序逐字保留 ADR-0031 §二（全局基座 → 配置 → 项目补充），只换文件位置：

1. **全局基座**：`~/.alibot/agents/<member_id>/AGENTS.md`；
2. **配置覆盖**：库内 `agent_config.system_prompt`；
3. **项目补充**：`<workspace_root>/.alibot/agents/<member_id>/AGENTS.md`。

两段文件规则仍是原样拼接、不解析、不裁剪；缺失 / 不可读 / 非 UTF-8 = 该段缺席，不写空标题。
`AGENTS.md` 不属于应用的写入白名单：应用只读它，不论落在全局层还是项目层。

## 三、记忆：两处可写，按「属于谁」分流

- **读**两处：项目家目录（有项目根的轮次）与全局家目录。两处 `MEMORY.md` 各自以带标签的段落
  进入 system prompt——项目段由 `AgenticMemoryMiddleware` 注入，全局段由宿主只读读取后追加。
- **写**两处：属于项目的记忆写项目侧，属于全局通用的写全局侧。这条分流是**给模型的判据**，
  不是两套 API：文件工具只有一套，落到哪个目录由写入的路径决定。
- **无项目根**（DM / 频道未配项目根 / task 无频道根）：全局家目录是唯一记忆落点，此时
  `AgenticMemoryMiddleware` 的 workdir 就是它，不再单独注入第二段全局段落（同一文件不重复）。
- **未开启长期记忆**：两处都不挂、不建目录。开启后应用可创建 `~/.alibot/agents/<member_id>/`
  这一级（含 `MEMORY.md`），但**不**创建 `~/.alibot/skills/`、不创建 `AGENTS.md`、不创建 `mcp.json`——
  人的维护面只让出一格：记忆。

### 写入白名单

家目录内应用可写的路径只有两种：

- 家目录根下的 `MEMORY.md`；
- 家目录根下的 `<topic>.md`（不允许子目录）。

`AGENTS.md`、`skills/`、`mcp.json`、任意子目录一律**只读**。白名单由根检查与记忆中间件共同执行：
项目根之外的写入只对这个目录开放，且仍按文件名过滤；越界与非白名单文件返回可读的拒绝，不是静默
跳过。`MEMORY.md` 允许 `MEMORY.md.bak` 之类派生名没有意义，因此白名单按精确名 + `.md` 后缀判定。

## 四、会话状态离开 `.alibot` 分区

`sessions/` 是运行状态（ADR-0031 §二末段：不是记忆、不是规则），但它此前也走 `<root>/.alibot/sessions/`
公式，于是数据目录分支同样出现镜像层。本 ADR 把落点显式化，不再复用 `root` 这一个变量表示两种根：

| 轮次 | `state.json` 与 `offload/` 的位置 |
| --- | --- |
| 有项目根（local） | `<workspace_root>/.alibot/sessions/<session_id>/` |
| 有项目根（docker） | 容器内 `/workspace/.alibot/sessions/<session_id>/`（宿主同一分区） |
| 无项目根（DM / 无根频道） | `<data_dir>/sessions/<session_id>/` |

`session_id` 仍是 `safe_partition_id(f"{target_id}--{member_id}")`（ADR-0030）。`sessions/` 不跨项目
共享的结论不变。

## 五、安全与后端边界

- **全局家目录不是文件工具的工作根**。文件相对路径仍从频道项目根起算（ADR-0021 保留）。唯一的
  例外是记忆写入：家目录作为**受白名单约束的额外可写目录**挂给文件工具，读仍可及、写仅限
  `MEMORY.md` 与 `<topic>.md`。
- **Docker**：成员家目录 `~/.alibot/agents/<member_id>/` 从 `:ro` 改为 `:rw` 挂到
  `/alibot-home/agents/<member_id>`；公共技能库 `~/.alibot/skills/` 仍 `:ro`。数据目录仍不进容器。
  容器内 Bash 可以写家目录（它本来就能写 `/workspace`），因此白名单是**应用面**的约束，不是沙箱
  承诺——与 ADR-0021「本地 shell 无沙箱」的既有口径一致。
- **无项目根的轮次不可能是 Docker**：没有 workspace 就没有容器，因此「全局家目录可写」在无项目根
  分支只发生在本地后端，不会与只读挂载冲突。
- **守卫**：全局层仍用自己的根做 `realpath` 检查（ADR-0031 §三的独立守卫保留）；新增的写通道
  在同一守卫下按白名单放行，不复用项目根的 `ensure_state_partition` 语义。

## 六、与既有 ADR 的逐句关系

**ADR-0030 正文一字不改。** 本 ADR 只替换它的落点公式，其余结论（Markdown + AgenticMemoryMiddleware、
不引入外部服务、同根内跨 target 共享、关闭不建目录、DM 的额外工作目录）全部保留。

| ADR | 原句 | 处置 |
| --- | --- | --- |
| ADR-0030 | 「文件放在 `<root>/.alibot/memory/<member_id>/Memory/`」 | **替换**为「项目根 / 全局家目录 `agents/<member_id>/` 的 `MEMORY.md` 与主题文件」 |
| ADR-0030 | 「DM 使用数据目录」 | **替换**为「无项目根时使用全局家目录」 |
| ADR-0030 | 「DM 的 Write/Edit 及额外工作目录只覆盖记忆目录」 | **保留**，记忆目录即全局家目录（受白名单） |
| ADR-0031 §二 记忆 | 「读两处……写只落项目侧……全局记忆对应用永远只读」 | **修订**为「两处都可写，按属于项目 / 全局分流；`AGENTS.md`、`skills/`、`mcp.json` 仍只读」 |
| ADR-0031 §二 规则 | 「`agents/<member_id>/rules/AGENTS.md`」「`.alibot/rules/<member_id>/AGENTS.md`」 | **替换**为扁平 `agents/<member_id>/AGENTS.md`（两层同构） |
| ADR-0031 §二 会话状态 | 「全局层没有、也不应该有 `sessions/`」 | **保留**：`sessions/` 仍在项目根或数据目录，不进家目录 |
| ADR-0031 §三 | 「把 `~/.alibot/agents/<member_id>/` 与 `~/.alibot/skills/` **只读**挂到 `/alibot-home`」 | **修订**：成员家目录 `:rw`，公共技能库仍 `:ro` |
| ADR-0031 §四 | 「全局层只增加只读输入，不增加可写根」 | **修订**：增加一个受白名单约束的可写目录（记忆），不增加第四档权限 |
| ADR-0021 §一 | 「文件工具的相对路径从项目根起算；每个文件工具路径都要 `realpath` 后落在根内」 | **保留**，家目录写入是显式例外并单独过滤文件名 |

## 七、迁移

应用**不自动搬移**旧路径（沿用 ADR-0031 §五的迁移原则）：

- `~/.alibot/data/.alibot/memory/**`、`<ws>/.alibot/memory/**`、`<ws>/.alibot/rules/**` 与
  `~/.alibot/agents/<id>/rules/**`、`.../memory/Memory/**` 只做**一次性人工搬迁**，应用新路径不再读取它们；
- `~/.alibot/data/.alibot/sessions/**` 属运行状态，可直接弃用（消息正本在库，摘要丢失只影响下一轮
  的续接质量）；
- 搬迁命令与备份指引写在 `app/README.md` 的升级段，与 ADR-0031 的隐藏目录说明放在一起。

## 八、被否方案

- **保留 `<root>/.alibot/memory/...` 只改 `root`**：`root` 同时表示「项目根」与「数据目录」是这次
  镜像路径的根因，只换取值不消除歧义。
- **全局家目录继续只读、另立 `~/.alibot/state/<id>/`**：路径又变成两个根，与用户「全局只有一个
  路径」的决定相反。
- **统一写全局家目录、项目侧不再有记忆**：跨项目串味（项目 A 的结论出现在项目 B），且丢掉
  ADR-0030「不同项目根不混用记忆」这条已有结论。
