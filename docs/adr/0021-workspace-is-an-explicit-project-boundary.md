# Workspace 是显式配置的项目边界：本地只开受限文件工具，执行默认进 Docker，权限与 HITL 不改结构身份

用户于 2026-10-18 人定（D6 / D14 / D15 / D21 / D22）：workspace 选择“全部”，包括 LocalWorkspace + 每频道
项目根 + 路径限定、Docker 沙箱作为可选后端（每 Agent/会话隔离）、MCP 与 Skill 的装配/管理、权限模式与工具
审批（默认读允许、写/执行需人审批，可配置），以及与“数据目录可整体备份”“库里不存密钥”“Agent 不经 HTTP”
的关系。随后两轮 ask 把粒度与边界钉死：频道配项目根，Agent 配后端与权限；本地默认只开文件工具并强制根限定，
Bash 默认仅在 Docker 开放；MCP/Skill 只接受本机来源；权限只暴露三档，不暴露 BYPASS。

## 决策记录

- **Context**：`CONTEXT.md` 说 Agent 不做文件/shell；AgentScope 2.0.10 提供 Workspace/Permission/MCP/Skill；本地 Bash 是主机进程，cwd 不是沙箱。
- **Decision**：频道配项目根、Agent 配 local/docker 后端与权限档；本地只开文件工具并强制根限定；Bash 默认仅 Docker，本地需显式开启 + 逐次审批 + 无沙箱告知；MCP/Skill 只接受本机来源；三档权限不暴露 BYPASS；HITL 进程内有界等待。
- **Consequences**：项目文件不在数据目录备份内；Docker 是可选依赖；远端 MCP/Docker registry 扩大数据面；库仍不存密钥；Agent 仍不经 HTTP。

## 一、项目根是频道级，后端与权限是 Agent 级

`channel.workspace_root` 是**频道的项目边界**：所有成员共享这个根，文件工具的相对路径从这里起算。
`channel.workspace_backend` / `channel.permission_mode` 是默认值；`agent_config.workspace_backend` /
`agent_config.permission_mode` / `agent_config.allow_local_shell` / `agent_config.docker_image` 是每个 Agent
的覆盖。Agent 不能把根改到频道根之外；local 后端的每个文件工具路径都要 `realpath` 后落在根内。

修订（#270）：项目侧 `.alibot/agents/<member_id>/` 与 `.alibot/sessions/<session_id>/` 仍用于运行状态与
stdio MCP cwd；技能字节不再放在项目根，而落入 Agent 家目录，且不依赖 workspace（见 §三）。

## 二、本地 shell 的边界必须写成“默认没有”

AgentScope 的 `LocalBackend` 用主机进程执行 Bash；`cwd=workdir` **不是**沙箱，命令可以 `cd /`、读任意绝对路径。
因此本仓不能声称“本地 Bash 被限制在项目根内”。决定：

- 本地模式默认**不挂** `Bash`；`LocalWorkspace.list_tools()` 返回的 Bash 被装配层过滤掉。
- `allow_local_shell=1` 时，Bash 走 `PermissionEngine` 的 `ask`，**每次调用**都要人类审批；工具说明与设置页
  明写“本机进程、无沙箱、可越过项目根”。这是用户显式接受的风险，不是默认能力。
- 需要执行且要隔离时用 **Docker 后端**：每 Agent/会话一个容器，`host_workdir=workspace_root` 挂到
  `/workspace`；容器内 Bash/Read/Write 只能看到挂载与容器私有目录。Docker 缺失时该 Agent `offline` 并给
  可读原因，**不静默回落到本地 shell**。
- 文件工具（Read/Write/Edit/Glob/Grep）在 local/docker 都做根检查；2.0.10 的 `ToolBase` 已把路径校验改成
  `realpath`（`tool/_base.py:467`），但装配层仍需自己的根检查，因为内置工具并未承诺“只允许 workdir”。

## 三、MCP / Skill 是配置，不是消息或任务

**修订（#270，2026-10-25）**：本节原有的项目根 Skill 字节路径与频道级 MCP / Skill 装配口径已修订。以下为现行决策：

- `mcp_server` 是 MCP 声明的数据库正本，也是**唯一装配来源**；现行声明使用 `scope_kind='agent'`；`scope_kind='channel'` 的旧行保留但弃用，装配忽略、不回填。Agent 家目录 `mcp.json` 是**导入源**，并由 Studio 经人类面提供写面（直接编辑原文）；它**不参与运行时装配**——文件里未导入的条目不会被连接，只给一条只进 `RoundOutcome.reason` 的可读提示。导入是**显式**动作（`python -m alibot migrate-mcp`，默认 dry-run、`--apply --yes` 才写、先备份、同名不同形整体拒绝、不删源、**不代做远端确认**，#275）。MCP 仍以 workspace 为宿主（AgentScope 的 `list_mcps` 需要 workspace）；没有 workspace 时不装配 MCP。
- `skill` 仍存元数据与 hash；字节由宿主应用写入 `~/.alibot/agents/<member_id>/skills/<name>/`，不再依赖 workspace。安装只接受本机目录或上传 zip，校验 `SKILL.md` front matter 与 hash；不做远程市场、自动下载或自动更新。
- `scope_kind='channel'` 的旧 Skill 行与项目层字节 `<workspace_root>/.alibot/skills/<member_id>/` 保留但弃用、装配忽略；不回填、不自动删除。迁移由独立 `migrate-skills` 命令显式执行（#274；默认 dry-run，`--apply --yes` 才动手，退出码 6 = 拒绝/中途失败），启动时绝不自动迁移。
- MCP / Skill 的增删是**配置动作**：不落消息、不唤醒、不消耗预算、不开世代（与 ADR-0011 同族）。

## 四、权限与 HITL

三档对外语义：

| 档位 | 映射 | 行为 |
| --- | --- | --- |
| `read-only` | AgentScope `EXPLORE` | 只允许只读工具与只读命令；修改类工具/命令拒绝 |
| `ask-writes`（默认） | `DEFAULT` + 读工具 allow + 写/执行 ask | 读允许；Write/Edit/Bash 等按规则或逐次审批 |
| `trusted-writes` | `ACCEPT_EDITS` | 工作目录内自动写；越界写仍审批 |

不暴露 `BYPASS`（完全跳过安全审批）与 `DONT_ASK`（把 ASK 变成 DENY）；用户规则可以是 allow/deny/ask，
但“默认无审批”不是一档产品能力。

HITL（D19 已裁定）：`RequireUserConfirmEvent` → `approval_request`（pending）→ SSE `approval_required` → 人类面
`POST /api/approvals/{reply_id}` → `ApprovalBroker` 唤醒该轮 → `UserConfirmResultEvent` 回到 Agent/TeamPipeline。
默认等待 10 分钟；超时 `failed`；进程重启把 pending 标 `expired`，该轮 `failed`。跨重启恢复不在首版（D19）。

事实修正（#215）：`reply_id` 是 AgentScope 的**回复级**标识（「当前回复的 ID」），不是一次确认的标识——
同一条回复里第 N 个需要确认的工具调用会再发一次 `RequireUserConfirmEvent`、`reply_id` 与第一个相同。因此
一条 `approval_request` 可以承载**多批确认**：已收口的记录收到尚未包含的调用时重开为 `pending`（合并
`tool_calls`、清 `resolved_at`、刷新 `requested_at`、只发布新调用），既有 `decisions` 作为历史保留。
「只接受 pending」「600 秒」「不跨重启恢复」三条语义不变。

## 五、与三条既有边界的关系

| 既有边界 | 处置 |
| --- | --- |
| **数据目录可整体备份** | 数据目录仍只含 `alibot.db` + `attachments/` + 迁移台账。修订（#274）：技能迁移会在数据目录写 `skill-migration-<UTC>.json` 清单，并使用同一整目录备份机制（备份名 `<数据目录>-backup-skills-<UTC 时间戳>`）；数据目录仍是业务正本 + 备份单位。`workspace_root` 是用户项目目录，**不**进数据目录；README 必须写“备份数据目录 ≠ 备份项目文件”。 |
| **库里不存密钥** | `mcp_server.env_names` 只存变量名；Docker env 与 MCP 值只从进程环境读；`agent_config` 仍只有 model_id。审批记录只存工具调用与决策，不存凭证。 |
| **Agent 不经 HTTP**（ADR-0003） | 文件工具、MCP、Skill、审批回传都在进程内/容器内执行；Agent 没有 HTTP 令牌，也没有“以某成员名义调 HTTP”的路径。人类审批走 HTTP，但那是人类面。 |

## 六、与 CONTEXT.md “Agent 不做文件与 shell”的关系

`CONTEXT.md` 的**Agent** 词条原写“它不做文件与 shell 操作（首版没有家目录、项目目录与沙箱）”。本 ADR
**扩展**首版口径；#270 再补充成员技能的只读第二根，明确区分项目根与技能读取：

- **保留**：没有配置 workspace 时不挂项目根文件工具与 shell。只有**装配了项目根的 local 轮次**才把成员
  `skills/` 作为只读第二根（Read / Glob / Grep 可读，Write / Edit 仍只限项目根）；DM / 无项目根轮次没有
  这套文件工具，只有卸载目录的只读 `Read`。
- **扩展**：频道显式设置 `workspace_root` 后，成员获得以项目根为可写根的受限文件工具；技能装配与是否配置
  workspace 无关。Docker 后端或显式本地开关才获得 shell。
- **收窄**：本地 Bash 默认关闭；`trusted-writes` 仍只限项目工作根内；`BYPASS` 不暴露。
- **词条处置**：`CONTEXT.md` 保留“首版没有家目录、项目目录与沙箱”作为历史 / 默认形态说明；新增
  **Workspace（项目根）**、**权限档位**、**审批**与 Skill 只读根语义。

## 七、被否方案与代价

- **本地 Bash 只靠审批、不承诺根限定**：与 D6 的“不得越出根”冲突；审批是人的判断，不是结构保证。
- **所有执行都进 Docker、不做 LocalWorkspace**：D6 明确要 LocalWorkspace；Docker 是可选后端，不能成为唯一路径。
- **频道根之外再无边界，Agent 可读任意主机路径**：本地文件工具会越界；必须在装配层做 realpath 根检查。
- **把 MCP env 值存库**：违反“库里不存密钥”；只存变量名。
- **暴露 BYPASS**：与“写/执行需人审批”的默认信任边界冲突；不提供“完全跳过”的 UI 档位。
- **HITL 跨重启恢复首版就做**：需要持久化参与者 `AgentState` 并重建 pipeline，复杂度与风险高；D19 明确另开票。

**代价**：workspace 配置增加人类面面积；项目文件不在数据目录备份内，用户要自己备份；Docker 依赖、镜像构建与
冷启动成本；本地 Bash 显式开启后仍有主机越界风险；MCP 远端与 Docker registry 扩大数据面，需要逐项告知。
