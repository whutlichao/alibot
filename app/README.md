# alibot 应用后端 + 浏览器工作台（实施票 ② / ③）

在 #① 的频道消息闭环之上，把 **Agent 做成数据库里的成员行 + 模型配置**：新建 Agent（名称 +
角色 + 模型 + 系统提示）→ 启用远端模型前显式告知并确认数据面 → 在频道里 `@` 它 → 后端进程内的
AgentScope agent 真的读到这条消息并**自判**回应 / 忽略 / 失败 → 回复不经刷新就出现在消息流里，
断线后带 `last_seq` 自动补齐。

本目录服务两面（规格 §1）：**HTTP 面属于人类面**（浏览器与人自己的脚本），**成员面**由后端进程内
的唤醒编排驱动。Agent 的任何动作**不经 HTTP**，因此没有 agent 令牌，也不存在「以某成员名义调
HTTP」的路径。

## 运行

```sh
# 1) 装依赖（Python ≥ 3.11）——在仓库根建虚拟环境
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e "./app[dev]"

# 2) 启动后端（默认只绑定 loopback，默认端口 8788）
.venv/bin/python -m alibot
#   或显式指定端口与数据目录
.venv/bin/python -m alibot --port 8788 --data-dir /tmp/alibot-smoke
```

浏览器打开 <http://127.0.0.1:8788/studio/>（根路径 `/` 会 302 到这个地址）。

首版无认证，因此**默认只绑定 loopback**，不提供局域网/远程访问。
确实需要时设 `ALIBOT_ALLOW_REMOTE=1` 显式越界，并自行承担风险。

### 模型与凭证：只从**进程环境变量**读，库里不存密钥

Agent 的「模型标识」写作 `provider:model`（例如 `dashscope:qwen-max`、`openai:gpt-4.1`、
`ollama:qwen2.5`）。`provider` 取自 AgentScope 2.0.10 的 credential 类型白名单，它决定两件事：
**缺凭证时读哪个环境变量**，以及**这个模型是不是本机**（要不要先做远端数据流确认）。

| provider | 需要的环境变量 | 是否离开本机 |
| --- | --- | --- |
| `ollama` | 无（`OLLAMA_HOST` 可选，默认 `http://localhost:11434`） | 否 |
| `dashscope` | `DASHSCOPE_API_KEY` | 是 |
| `openai` / `openai-response` | `OPENAI_API_KEY` | 是 |
| `anthropic` | `ANTHROPIC_API_KEY` | 是 |
| `gemini` | `GEMINI_API_KEY`（或 `GOOGLE_API_KEY`） | 是 |
| `deepseek` | `DEEPSEEK_API_KEY` | 是 |
| `moonshot` | `MOONSHOT_API_KEY` | 是 |
| `minimax` | `MINIMAX_API_KEY` | 是 |
| `volcengine` | `ARK_API_KEY`（或 `VOLCENGINE_API_KEY`） | 是 |
| `xai` | `XAI_API_KEY` | 是 |

```sh
# 例：本机 Ollama（零凭证、零费用）
OLLAMA_HOST=http://localhost:11434 .venv/bin/python -m alibot
```

- **不新增密钥录入界面、不新增密钥落库表**：应用库里没有任何密钥字段（只有「何时确认过数据面」）。
- 凭证解析收敛在 `alibot/agent_seam.py` 的 `read_api_key()` 一个窄入口，以后换来源（钥匙串等）
  只改这一处。

### 远端数据流告知（首次远端调用之前）

启用远端 provider 之前，界面会显式列出会离开本机的数据面并要一次确认：
**消息正文、发送者显示名、频道名称与描述、被点名成员的显示名**，接收方是所配置的模型提供商
（可能是远端服务）。按 provider 确认一次即全局生效；**未确认时该 Agent 状态点是「未启用」，
被 `@` 时轮次 reason 可读，且绝不发起任何远端调用**。

**本机存储不做应用层端到端加密**；磁盘上的数据库按操作系统文件权限保护。产品文案**不声称 E2EE**。

### 数据目录：备份方式就是复制目录

| 项 | 值 |
| --- | --- |
| 默认端口 | `8788`（`--port` 可改） |
| 默认数据目录 | `~/Alibot/`（可见、可直接复制） |
| 覆盖方式 | `--data-dir <路径>` 或环境变量 `ALIBOT_DATA_DIR` |
| 库文件 | `<数据目录>/alibot.db`（单文件 SQLite，WAL 模式） |
| 附件字节 | `<数据目录>/attachments/<附件标识>`（**不进 SQLite**；库里只存相对路径，因此整个数据目录可整体搬迁） |
| 事件流单连接寿命 | `ALIBOT_SSE_ROTATE_SECONDS`（默认 55 秒，到期服务端主动收尾、浏览器带 `last_seq` 重连） |

改名不保留兼容：只有 `ALIBOT_*` 环境变量生效，旧前缀 `WORKSPLICE_*` 与旧数据目录 `~/Worksplice`
一律不再被识别，也不做迁移（那是另一个项目的目录）。

备份：`cp -R ~/Alibot ~/Alibot-backup-$(date +%F)`。恢复：反向复制回去。
库文件旁边可能有 `-wal` / `-shm` 两个伴随文件，一起复制即可。
**附件字节在 `<数据目录>/attachments/` 里**（#67）：只备份 `alibot.db` 的备份**不再是完整备份**，
它会是「有元数据、没字节」的半份——备份单位是**整个数据目录**。
**迁移前会自动备份**：`--migrate` 在动手之前把整个数据目录复制成同级的
`<数据目录>-backup-<from>-to-<to>-<UTC 时间戳>`；**备份失败就不迁移**（不会退化成"放行 + 一句警告"）。

从首版升级（ADR-0015）：**启动仍然拒绝** schema 版本与本版不等的库（不猜、不跳过），但报错正文会直接给出
迁移命令与备份指引；把旧库升到本版要**显式**执行 `python -m alibot --migrate --data-dir <数据目录>`
（迁移前自动备份整目录、单事务收口、幂等；`--migrate --dry-run` 在临时副本上预演完整链，并打出原库
sha256 前后一致的读数）。`--repair-targets` 已降为 `--migrate` 的**弃用别名**，仍可处置"版本号已是 4、
但 channel 型作用域数据还没搬"的半改名库（列表页正常但点进频道全 404）。

### Schema 版本与迁移（15 → 19）

本代码的 `SCHEMA_VERSION = 19`（`app/alibot/db.py`），依赖 pin `agentscope==2.0.10`。本轮把库形态从 15
推进到 19，由**四个严格 +1** 的步骤组成（规格 §4.1；ADR-0015 的链连续性纪律不变）：

| 步骤 | 名称 | 加什么 |
| --- | --- | --- |
| `15 → 16` | `agentscope-2 team/plan/sop expand` | `channel.mode` / `channel.team_leader_member_id`（+ team 必须有队长的触发器）；`team_run` / `team_assignment` / `plan_run` / `plan_task` / `sop_definition` / `sop_run` |
| `16 → 17` | `agentscope-2 workspace/permission/mcp/skill expand` | `channel.workspace_root` / `workspace_backend` / `permission_mode`；`agent_config.workspace_backend` / `permission_mode` / `allow_local_shell` / `docker_image`；`permission_rule` / `approval_request` / `mcp_server` / `skill` / `remote_consent_scope`；旧 `remote_consent` 行复制成 `('model', provider, acknowledged_at)` |
| `17 → 18` | `agent web access config` | `channel.web_access` / `agent_config.web_access`（`NULL` / `'search'` / `'search+fetch'`）；`remote_consent_scope.scope_kind` 的 `CHECK` 纳入 `web`（表级 `CHECK` 只能**重建表**，重建后既有行逐行保真） |
| `18 → 19` | `docker container egress switch` | `channel.docker_egress`（`0` = 容器 `--network none`，缺省；`1` = 容器接 Docker 默认网络，且联网工具只放行 `permission_rule` 里 `fetch` 的 `allow` 规则的 host） |

- **只加不删、不回填业务数据**：新列可空或带列默认值，旧行按默认值解释——`mode='autonomous'`、
  `team_leader_member_id=NULL`、`workspace_root=NULL`、`workspace_backend='local'`、
  `permission_mode='ask-writes'`；`agent_config` 的四个新列是 `NULL` / `NULL` / `0` / `NULL`；
  `17 → 18` 的两列 `web_access` 也一律停在 `NULL`（= 关闭 / 跟随频道，见下面的**联网档**）；
  `18 → 19` 的 `docker_egress` 停在列默认 `0`（= 容器默认无网，见下面的**容器出网**）。
- **迁移是显式动作**：`python -m alibot --migrate --data-dir <数据目录>`；`--dry-run` 在数据目录内的临时
  副本上预演完整链，**原库 sha256 前后一致**，不建备份、不写台账、不留副本。迁移前自动把整个数据目录
  复制成同级 `<数据目录>-backup-15-to-19-<UTC 时间戳>`；**备份失败就不迁移**。
- **单向、无降级代码**：回滚 = 恢复迁移前的那份**整目录备份**（`cp -a` / 反向复制），再用旧版本代码打开。
  旧代码**不能**打开新版本库（版本闸门），所以「把 `SCHEMA_VERSION` 改回去」不是回滚。
- **失败即回滚**：步骤链在单事务内跑；任一步失败 ⇒ `ROLLBACK`、库仍停在源版本且未被改动、备份仍在、
  审计台账记一条失败记录。`#157` 的演练（`app/specs/issue-157-acceptance.md`）把 15→16→17、默认值、
  新表可查、备份恢复与失败注入逐项跑过。

## 数据模型（决策级）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `member` | id, kind(`owner`/`agent`), 显示名, 头像色位 | 身份正本；空库只有一条 `owner` 行 |
| `agent_config` | member_id, 角色, 模型标识, 系统提示, 启用开关, **自动认领开关**（`auto_claim`，缺省关闭）, **workspace 后端 / 权限档**（可空 = 跟随频道）, **本地 shell 开关**（`allow_local_shell`，缺省关闭）, **Docker 镜像**（可空 = 内置）, **联网档**（`web_access`，可空 = 跟随频道） | 模型与系统提示是**配置数据**，换模型不改代码；`auto_claim` 决定编排要不要替它在新建任务后认领；workspace 配置见 ADR-0021 |
| `target` | id, kind(`channel`/`dm`/`task`), dm_peer_member_id, created_at | 一切消息面的**唯一作用域键**（ADR-0004）：消息、已读位置、回应预算、轮次结果都挂 `target_id`。channel 型是**影子行**（id 与 `channel.id` 同值、只承载 `kind`） |
| `target_member` | target_id, member_id | **成员关系**的正本（三种 kind 共用一张表）：唤醒面与未读口径的共同来源；频道成员就是它在 channel 型上的读法 |
| `target_read` | member_id, target_id, last_read_seq | 已读位置（未读的正本）。owner 的行是未读徽标基准，agent 的行是它自己的消费游标 |
| `task` | id, target_id, channel_id, 标题, 状态, **`assignee_member_id`**(可空), **`claimed_at`** | 任务当前态在行上；`assignee_member_id` = 受派者；`claimed_at` 非空 = 这张任务**已被认领过**（「每张任务至多一次」的落点）。读路径载荷另派发 **`allowed_transitions`**（当前态的合法迁移目标，顺序固定五态、终态 `[]`；由迁移表派生，呈现面只读它，#191） |
| `permission_rule` | id, scope_kind(`channel`/`agent`), scope_id, tool_name, pattern, behavior(`allow`/`deny`/`ask`), created_at | 权限规则是**配置**：三档行为，不暴露 BYPASS / DONT_ASK（ADR-0021 §四） |
| `approval_request` | reply_id, target_id, member_id, team_run_id(可空), tool_calls(JSON), state(`pending`/`approved`/`denied`/`expired`), decisions(JSON), requested_at, resolved_at | HITL 审批记录；只存工具调用与决策，**不存凭证**（ADR-0021 §四） |
| `mcp_server` | id, name, transport(`stdio`/`http`/`sse`), command/args/url, env_names, enabled, scope_kind/scope_id | MCP 声明；`env_names` 只存变量**名**，值只从进程环境读（ADR-0021 §三） |
| `skill` | id, name, description, source_path, content_hash, enabled, scope_kind/scope_id | Skill 元数据；字节在 `workspace_root/.alibot/skills/<scope_id>/`（Agent 级按 member 分区、频道级是频道分区，装配时 Agent 级同名覆盖频道级），只接受本机目录/上传 zip |
| `remote_consent` | provider, acknowledged_at | 远端数据流告知的一次性确认；**不存任何凭证**（兼容面，迁移后读路径优先读 `remote_consent_scope`） |
| `remote_consent_scope` | scope_kind(`model`/`mcp`/`docker`/`web`), scope_id, acknowledged_at | 远端数据面的统一一次性确认；旧 `remote_consent` 行复制成 `('model', provider, …)` |
| `plan_run` / `plan_task` | run: id, target_id, member_id, base_seq, status, started_at, finished_at；task: plan_run_id, seq, subject, description, state(`pending`/`in_progress`/`completed`), owner, blocks/blocked_by(JSON), metadata(JSON) | **Plan** 是成员轮次内的工作清单（ADR-0023），**不是任务五态**：不进看板、未读、搜索、唤醒、预算，不新增 target。每个 `(target_id, member_id)` 保留最近 20 run 或 30 天，`plan_task` 随 run 级联删除；只进运行面板（`GET /api/targets/{id}/plan` + `plan_update`） |
| `sop_definition` / `sop_run` | definition: id, name, description, steps(JSON: executor/verifier member id、subject、description、max_attempts), enabled；run: id, sop_definition_id, target_id, leader_member_id, state(`SOPRunState` JSON), phase(`pending`/`running`/`awaiting`/`completed`/`failed`), started_at, finished_at | **SOP** 是 owner 显式启动的多步运行（ADR-0023），**不是任务五态**：不改任务行、不写消息、不进看板/未读/搜索/唤醒/预算，不新增 target。运行保留最近 50 个或 90 天；完成/失败不自动删除定义。步骤事件走 `sop_event`，快照是 `GET /api/sop-runs/{id}` |
| `workspace_settings` | id(=1), locale, message_density, theme, updated_at | **工作区设置**：单行表，列就是明确列出的档位（语言偏好 / 消息密度 / 主题三档）。人类面可写配置类取值，**不得**写用量类事实 |
| `reminder` | id, text, time(`YYYY-MM-DD HH:MM`，**一次性**), channel_id(可空 = 不归属), created_at, **`fired_at`**(可空) | **提醒**（ADR-0012 / ADR-0017）：库内对象，后端是唯一正本（不放 localStorage）。它与频道的关系是**归属**，不是 target 作用域——**不**新增 target、不计未读、不参与唤醒；增删**不落消息、不唤醒、不消耗预算**。`time` 是**一次性**到期时刻（按本机本地时间解释，**无重复字段**）；`fired_at` 非空 = 已投递（UTC），`NULL` = 还没到点 |
| `channel` | id, 名称, 描述, **`mode`**(`autonomous`/`team`), **`team_leader_member_id`**(team 必填), **`workspace_root`**(可空 = 未配置), **workspace 后端 / 权限档默认值**, **联网档 `web_access`**（`NULL` = 关闭）, **容器出网开关 `docker_egress`**（缺省 `0` = `--network none`） | `mode` / 队长是**频道级协作拓扑**（ADR-0018）；team 覆盖该频道的 channel 型 target 与**全部 task target**。`workspace_root` 是**频道级项目边界**（ADR-0021 §一）；后端缺省 `local`、权限档缺省 `ask-writes` |
| `target_budget` | target_id, epoch_seq, used, applied_limit | **回应预算的用量正本**（每 target 一行）：`epoch_seq` = 人类世代，`used` = 该世代已投递的跳数，`applied_limit` = 该世代生效上界的快照（`NULL` = 内置默认 3） |
| `channel_budget_limit` | channel_id, budget_limit(`3`/`5`/`10`), updated_at | 回应预算上界的**配置**落点；**没有行 = 回落到内置默认值**。保存只影响**下一个人类世代**，不回溯改写当前世代 |
| `team_run` / `team_assignment` | run: id, target_id, leader_member_id, base_seq, status(`running`/`completed`/`failed`/`interrupted`/`invalid-leader`), started_at, finished_at；assignment: team_run_id, target_id, task_id(可空), leader/member_id, tool_call_id, prompt, state(`started`/`succeeded`/`failed`/`interrupted`/`budget-exhausted`), jump_consumed, reply_excerpt, error | **team 模式的运行记录**（ADR-0018/0019/0022）：`target_id` 可以是 channel 型或该频道下的 task 型；task target 上 `task_id` 非空。它们**不是消息**：不占未读、不进搜索、不写频道消息；`jump_consumed` 只记「这次分派扣了一次跳」 |
| `message` | id, target_id, seq, author_member_id, kind, 正文, **提及快照**, task_ref(可空) | **append-only**，`UNIQUE(target_id, seq)` |
| `attachment` | id, 原文件名, 大小, MIME, **相对路径**, 创建时间, **可空的 message_id** | 附件的元数据；**字节在数据目录**、不进 SQLite。`message_id` 为空 = 未发送的草稿（读取面不暴露）。绑定一次即不可变，**没有**删除路径 |
| `round_outcome` | member_id, target_id, base_seq, status, reason, 起止时间, 是否消耗预算, 是否被合并 | 轮次结果，**append-only**。`consumed_budget` 是**行级**标记（这一轮由一次已计一次的跳送来），**不可按行求和**——跳数正本是 `target_budget.used` |

- **表清单与 `SCHEMA_VERSION = 19` 的 `SCHEMA` 一一对应**（`schema_meta` 只存版本读数，不是业务表）；
  新增列/表的默认值必须与读路径解释一致——改一处就要同时改这张表。
- **append-only 是硬约束**：库级触发器拒绝 `message` 与 `round_outcome` 的任何 `UPDATE` / `DELETE`；
  `attachment` 同样没有删除路径（它的 `UPDATE` 只允许**唯一一次**绑定）。HTTP 面也没有编辑 /
  删除这些对象的端点。
- **备份数据目录 ≠ 备份项目文件**：`workspace_root` 指向用户的**项目目录**，不在数据目录内，也不进
  整目录备份的复制；项目文件需要用户自己备份（ADR-0021 §五）。
- **频道内单调 seq**：由后端在同一事务内分配，同时充当渲染顺序、重连补拉游标。
- **提及快照随消息落库**：唤醒与前端高亮都消费同一份快照，不事后按当前成员名表重新解析。
- **未读计数** = `last_read_seq` 之后、`kind ∈ {human, agent}` 的顶层消息条数（不计 `system`）。

## Agent 的采用范围（AgentScope）

pin 的版本是 **`agentscope==2.0.10`**（2026-10-18 核对：`docs.agentscope.io` 站点根重定向到
`/en/versions/2.0.10`，PyPI 最新同为 `2.0.10`）。

- 只用 2.x API：`from agentscope.agent import Agent`、`Agent(name=…, system_prompt=…, model=…)`、
  模型与凭证分模块 `agentscope.model` + `agentscope.credential`。**不**使用 1.x 的
  `ReActAgent(name, sys_prompt, …)` 签名，**不**引用 `/latest/`。
- **不使用**框架的 Agent Service（它会把身份与持久化语义挪到本应用之外）。
- 框架的 Memory 只服务单个 agent 的上下文，**不是**频道消息正本；消息正本始终是自己的表。
- 一轮 ↔ 一条 `Msg`：一次 `reply` 调用累积成恰好一条 `Msg`；`AgentEvent` / `usage` / `error`
  只影响该轮结论，不构成消息正本。
- **不声称** AgentScope 提供本应用的 app 级身份、频道消息持久化或端到端加密。

## 唤醒、自判与轮次结果

- **触发点唯一**：消息**成功落库之后**才唤醒（落库失败不唤醒）。唤醒信号只带
  `(member_id, target_id, from_seq, reason)`，**不带正文**；Agent 经**同一个读接口**自行读消息。
- **唤醒面** = 该频道的全部 agent 成员（**排除作者自己**）+ **穿透**（被个人提及但**未加入**该
  频道的 Agent）。穿透不豁免预算。这是 **`autonomous`** 频道的口径；`team` 频道（以及它的 task
  target）**只唤醒队长**，见下节。
- **回应判断**：**确定信号有四类**——被个人提及、被**认领**唤醒、team 队长被**任务变化**唤醒、
  **人类消息**（`message.kind == "human"`，DM / autonomous 频道 / team 队长 / task 受派者四个面同口径，#189）
  → **必须回应**（自判忽略按 `failed` 处理，不静默）；
  明确无关 → `ignored`（正常协议选择，不是失败）。**声明沉默**（`silent`）在确定信号下同样收口成
  `failed`——`silent` 本身不是失败，只是「它决定不说话」在必须回应的场合不算回应（与 `ignored`
  同款，见 `orchestration.py` 的 `definite_signal` 分支）。四类**同级不同源**：可读文案与提示词按来源选
  （`reason.claim_*` / `reason.team_change_*` / `reason.human_message_*` / 提及那一对），混用就会写下一条
  没发生过的事实。人类消息这一类**不改唤醒面、不扣跳、不改未读口径、不改消息 kind**，也不改非人类作者
  （Agent / system）的纪律。
- **每轮落一条 `round_outcome`**：`replied` / `ignored` / `failed` / `merged` / `budget-exhausted` /
  `silent`（`silent` = 模型经**结构化通道**声明本轮沉默，与 `ignored` 不同、也不是失败），
  带可读 reason、起止时间、是否消耗预算、是否被合并。
- **一轮至多一条频道消息**；落库路径只有一条。
- 消费游标只在 `replied` / `ignored` / `silent` 推进；`failed` 不推进（下次唤醒会重新读到，但不是自动重放）。

### 穿透（点名一个未加入该频道的 Agent）

点名一个不在该频道的 Agent 时它照样被送达（`penetration`，信号里带 `reason=mention`）。它**要
回复就顺带加入**：先写一条 `system` 事件消息说明它加入（`kind="system"`，作者就是它自己），
再写回复（`seq` 依次递增，先成员变化再发言）；**不回复则不加入**（成员关系只随真实回复发生）。
`system` 事件消息**不唤醒任何人、不消耗预算** —— 它不是对话回合。

**`team` 频道里，穿透仍然存在但留在 pipeline 之外**（D17）：被点名的非成员照常被一次性唤醒、
回复照常落为频道消息（占一条消息与未读），但它**不进入** team pipeline、不产生 `team_assignment`、
也不再唤醒任何人。**task target 不穿透**（D23=B）：任务线程只唤醒队长。

### 有界响应（策略 B）：预算从「只记录」变成「真压制」

- **人类世代** = 该频道最近一条人类消息的 `seq`；人类消息落库**即**开启新世代，把该频道的
  回应预算用量重置为 0，并**快照**该世代生效的上界（`target_budget.applied_limit`）。
- **一次 agent→agent 跳** = 一条 **Agent 消息**导致**至少一个其它** Agent 被唤醒（即**投递**了
  至少一个唤醒）；一条消息即使唤醒多个成员也只消耗 **1** 次。被**合并**掉的重复唤醒不额外计数、
  也不额外消耗预算。目标全部处于 in-flight ⇒ 零投递 ⇒ **不算一次跳**。
- **预算耗尽**：Agent 消息照常落库（append-only、不可撤回），但**不产生任何唤醒**（一个都不投递），
  为每个「本应被唤醒却被压制」的成员各记一条 `budget-exhausted`。人类消息随后落库即重置。
- **硬上界**：任一人类世代内同一频道的 agent→agent 跳数 **≤ 该世代生效上界**——未配置时是内置
  默认 **3**（ADR-0002）；频道可在库内把它配成 **3 / 5 / 10** 之一，改动**从下一个人类世代生效**
  （`channel_budget_limit` 表 = 配置，`target_budget.applied_limit` = 该世代的生效上界）。
  上界由「耗尽的世代里零投递」构造性保证。
- **人类面只能写上界**：频道设置经 `PATCH /api/channels/{id}/budget` 保存 3 / 5 / 10 之一
  （ADR-0007）。**用量不可写**——带 `used` 的请求整体被拒；`used` 仍只由后端在人类消息落库开世代时
  重置、并在一次实际投递的跳上 +1。写配置**不落消息、不唤醒、不消耗预算、不开世代**。
- **预算落库**（`target_budget` 表，每 target 一行）：`used` / `epoch_seq` / `applied_limit` 是后端持久事实，重启后可恢复。
- **`budget-exhausted` 不是出错**：它既不是「不想说」也不是「它坏了」，状态点仍是空闲。
- **跳数的单一正本**：`target_budget.used`（= `GET /api/channels/{id}/budget` 的 `used`）是「该人类
  世代已用跳数」的**唯一权威**，每次跳恰好 +1，不因该跳投递了几个成员而变化。`round_outcome` 行上的
  `consumed_budget` 只是**行级**标记（「这一轮由那次跳送来」），一次跳投递 N 个成员就有 N 条为真的行，
  所以 **`sum(round_outcome.consumed_budget)` 不能当跳数用**（行数与跳数之间没有固定关系：投递的成员若
  无可落库内容就不落行，所以它可能偏大也可能偏小）；要从只读面反推跳数，读
  `budget.used`。`round_outcome` 的 append-only 约束不变。
- **并发合并**：同一 Agent 同时最多一轮 in-flight；窗口内到达的重复唤醒**不排队、不重放**，
  收口时合并**恰好一条** `merged` 记录，`base_seq` 取窗口内**最大 seq**。被合并的消息仍在消费
  游标之后，下一次**新的**唤醒会被一并读到（不重放 ≠ 永久吞掉）。

### Agent 主动认领（`auto_claim`，ADR-0010 / ADR-0016）

频道内**新建任务之后**，由**编排**裁决认领——不让模型自己改数据库：候选 = 该频道里**可用**
（模型配置、启用态、凭证、远端告知）且 `auto_claim` 开启的 Agent 成员，按创建顺序取**第一个**。
一次只认领一个，不抢、不重复指派；开关缺省**关闭**，写它只有 `PATCH /api/agents/{id}` 这一个入口。

- **认领是一次开工动作，一个事务里落五项效应**（ADR-0016 二）：落**两条**可读的 `system` 记录、认领者
  进入该任务的 **target 成员**、任务状态推进到 `in_progress`、**唤醒认领者**（与 owner 指派同一条既有
  唤醒路径）。任一步失败则**全部零改动**。
- **两条 `system` 记录分工不同、不共用键**：`system.task.claimed`（`Claimed: {name} took this task` /
  `已认领：{name} 接手了这张任务`，**世代标识取它的 `seq`**）与 `system.task.claim_started`
  （`Claim started the work: status moved to {status}` / `认领后自动开工：状态推进至 {status}`，**新键**）。
  既有键继续只表示「有人推了状态」；`system` 记录的 `author` 恒为 owner，那是**系统事件消息**的既有
  口径、**不表示人推的**——区别只在词条键。
- **认领推状态复用迁移表、不复用 actor 校验**：只用 `TASK_STATUS_TRANSITIONS` 做合法性判定
  （`todo → in_progress`），**不经** `update_task_status` 的 actor 校验——认领的授权来自**编排**
  （配置层裁决），不来自成员身份（ADR-0016 三）。
- **认领轮必须回应**：判据与「被 @ 点名」**同级**（都是**确定信号**）——`ignored`、`silent` 与
  「回复但无正文且无任务动作」都收口成 `failed`，而**动作-only**（只调一次 `set_task_status` 而不说话）**算回应**。
  **只圈认领轮**：owner 手动改派维持既有纪律（任务本体照旧给到它，但不强制发言）。
  **team 频道例外（ADR-0025）**：**建任务 / owner 改派 / Agent 转派唤醒队长**的那一轮也是确定信号
  （等价于认领轮的必答），因此队长不能靠沉默把任务搁置；autonomous 频道的改派纪律不变。
  **人类消息（#189）**：owner 在 DM / 频道 / 任务线程的每一条发言都是确定信号，被唤醒的 Agent 必答；
  它在 team 频道里同样走「人类消息」口径（`reason.human_message_*`），**不**复用任务变化那套文案。
- **认领不是一次跳**：它唤醒认领者，但 `target_budget.used` **不 +1**（跳的载体必须是一条 Agent 消息）。
- **首次认领是任务 target 的一次开世代动作**：`target_budget.epoch_seq` = 那条 `system` 记录的
  `seq`，`used` 按当世代生效的上界重置为满额——这是 q4 的人定裁定（ADR-0010 三）。
- **每张任务至多一次**：落库的 `task.claimed_at` 是这条构造性保证的落点，认领只在
  `claimed_at IS NULL`（且尚未指派）时成立；因此即便任务之后被取消指派，也不会触发第二次世代推进与
  预算重置。**这条是 ADR-0002 三跳上界仍然可执行的前提**——没有它，Agent 就能靠「认领 → 用满 →
  再认领」把「一次人类发言能引发多少 Agent 往返」这道闸门拆掉。
- **`SCHEMA_VERSION` 不 bump、无迁移步骤**：本能力零 DDL（新增的是词条与一个进程内参数），而
  ADR-0015 的链只覆盖 **schema 形态变更**，因此「链连续性」这条纪律不适用于它（ADR-0016 六）。
- **已知代价**：认领会重置一次该任务的预算。触发者是人（人创建了任务），推进世代的动作却由编排
  执行；风险由「每张任务至多一次」封住（见上）。认领现在另多了「推状态 + 强制必答」两条效应，
  比 ADR-0010 当时描述的重；守这条边界的只有 `claimed_at` 那一把锁。

## team 模式：频道 + 任务线程的拓扑（ADR-0018 / ADR-0022）

`channel.mode ∈ {autonomous, team}`，缺省 `autonomous`（旧行逐字按默认值解释）；**DM 恒为
autonomous**。`team` 是**显式 opt-in**，并由 owner 指定一个 Agent 成员作为**队长**；队长为空/被禁用/
被软删除/不在频道里时，配置不合法（`PATCH` 整体拒绝；库里另有触发器守「team 必须有队长」）。

- **配置面**：`GET` / `PATCH /api/channels/{id}/config`；`team_scope` 是固定读数 `channel+task`
  （不可写）。写配置**不落消息、不唤醒、不消耗预算、不开世代**，也**不**自动改任何成员的 `auto_claim`。
- **作用范围（D23=B）**：team 覆盖该频道的 **channel 型 target** 与**全部 task target**。
  `mode` **不复制**到 `task` 行：task target 经 `task.channel_id → channel.mode` 解析，于是任务视图
  读的是所属频道的同一份配置。
- **只唤醒队长**：频道消息、任务线程消息、owner 改派、Agent 转派，**进入 pipeline 的只有队长**
  （作者是队长时零唤醒）。**task target 不穿透**；channel 上被点名的非成员仍走**穿透**——一次性直答、
  留在 pipeline 之外（D17）。
- **队长最终回复不收口成新唤醒**：pipeline 已收回成员结果，队长那条最终回复**不再唤醒成员**，因此
  不会形成 pipeline 外的自主循环。
- **成员产出可见性（D2）**：成员输出只走 **SSE `agent_event`** 与 **`team_assignment`** 运行记录；
  **只有队长的最终回复**落为频道消息（恰好一条）。成员过程**不占**频道消息、未读、搜索。
- **认领与受派者唤醒在 team 下停用（D16 / D23）**：team 频道里新建任务**停在 `todo`、
  `claimed_at IS NULL`、零认领轮次**——即便候选人的 `auto_claim=true`。`auto_claim` 是**读路径规则**
  （编排不读它），列与 autonomous 行为都不变。非队长的任务受派者**不会**被任务消息或改派唤醒。
- **`TeamAssign` 计 1 跳（D3 / ADR-0019）**：队长每分派一次，推进**该 target** 的
  `target_budget.used`（task target 用任务自己的预算，**不动频道预算**）；检查与扣减在同一事务/
  `BEGIN IMMEDIATE` 里完成。预算耗尽时**不运行该成员**，给队长一个错误 `ToolResult`，分派记为
  `budget-exhausted`。一次分派即使成员失败也已计跳、**不退款**；队长最终回复不计跳。
- **队长不可用（D18）**：**不回落 autonomous**。消息照常落库但**零唤醒**；`GET /channels/{id}/config`
  的 `leader_available=false` 与 `leader_reason` 给出可读原因，并留一条 `status=invalid-leader` 的
  `team_run`。状态点与原因读的是编排的同一个判定。
- **任务五态保留（ADR-0022）**：`task` 行、看板、任务线程消息流、任务 target 预算都不变；维护路径
  收窄为 owner 的 HTTP 动作与队长轮次里的结构化任务工具。`in_review → done` 仍不允许受派者批准自己，
  owner 恒可——队长是受派者时只能由 owner 批准。
- **运行记录**：`team_run`（一轮 team 协作）与 `team_assignment`（一次分派，`task_id` 在 task
  target 上非空）；快照是 `GET /api/targets/{id}/team-runs`（分页、时间倒序），SSE `team_assignment`
  是 live-only 直播面。它们不是消息，不改任务行、不写任务变更 `system` 消息。
- **pipeline 装配（D10 / D11）**：`TeamPipeline` 要求成员名唯一且不等于队长名，而本仓 `display_name`
  可重复，因此内部名一律由 **member id 派生**（不落库、不显示）；pipeline **每轮**按当前频道成员
  重建，成员加入/移除从下一轮生效。

## Workspace / 权限 / 审批 / MCP / Skill（ADR-0021）

`workspace` 是**显式配置的项目边界**：频道配项目根（`workspace_root`），Agent 配后端与权限档。**没有
显式配置根的频道行为逐字不变**（不挂文件工具、不挂 shell）。

- **项目根是频道级**：所有成员共享同一个 `workspace_root`；文件工具的相对路径从这里起算。根检查在
  **装配层**：Read / Write / Edit / Glob / Grep 每个路径先锚到根、再 `realpath`，`..`、根外绝对路径、
  指向根外的 symlink 一律返回 `ToolResultState.ERROR`。AgentScope 自己的 `data/` / `skills/` /
  `sessions/` / `.mcp` 收进 `<root>/.alibot`，项目根不被框架文件污染。
- **workspace_root 不在数据目录内**：它指向用户的**项目目录**，不进入整目录备份的复制。**备份数据目录
  ≠ 备份项目文件**——项目文件要用户自己备份（见上面的备份口径）。
- **两个后端**：`local`（默认）或 `docker`（每 Agent/会话一个容器，`host_workdir=workspace_root`，
  容器内固定根 `/workspace`）。**本地 shell 默认关闭**（`allow_local_shell=0`）：本地 Bash 是主机进程、
  逐次审批、无沙箱；默认只开受限文件工具。Docker 里的 Bash 在容器内，不需要那条本地通道。
- **Docker 不可用时是显式失败**：没有 daemon / 镜像起不来 ⇒ 该成员状态点 **`offline`**、
  `enabled_reason` 可读，**绝不静默回落本地 shell**；失败原因短 TTL 缓存（改配置会立即清掉重试），
  避免每轮重试把轮次堆起来。
- **权限三档（D22）**：`read-only` / `ask-writes`（默认）/ `trusted-writes`，分别映射框架的
  `EXPLORE` / `DEFAULT` / `ACCEPT_EDITS`。**不暴露 `BYPASS` 与 `DONT_ASK`**：来自配置/API 的这两个
  取值一律拒绝。读默认放行；写/执行在 `ask-writes` 下走人审批；`trusted-writes` 只自动放行**根内**
  写入，根外路径仍要审批。
- **规则**：`permission_rule` 按 `(scope_kind, scope_id, tool_name, pattern)` 存 `allow` / `deny` /
  `ask`；同样的键 **Agent 级覆盖频道级**，不同键并存。规则是**配置**：增删改不落消息、不唤醒。
- **HITL 审批（D19）**：`RequireUserConfirmEvent` ⇒ `approval_request` 落库为 `pending` + SSE
  `approval_required`；`POST /api/approvals/{reply_id}` 只接受 `pending`、幂等、部分决策只回送新决策、
  相反结论 409。**批准后该轮继续；拒绝的工具结果是 `ToolResultState.DENIED`（工具未执行，本轮仍可
  继续）**——实现事实如此，不伪造框架不会发出的 `ERROR`。默认等待 600 秒，超时收口 `failed`；
  进程重启时残留 `pending` 一律标 `expired`，**不支持跨重启恢复**。
- **MCP 声明**：`mcp_server` 存 transport / command / args / url 与 `env_names`——**只存变量名，
  值只从进程环境读**，库里不存密钥。远端 MCP（`http` / `sse`）未做数据面确认时**不连接**；
  stdio 不需要确认。装配层按 target 解析启用且已确认的声明，运行时读数（`connected` / `tools` /
  `error`）只回进程内事实。
- **Skill**：`skill` 只存元数据与内容 hash，字节在 `<workspace_root>/.alibot/skills/<scope_id>/`；
  安装只接受**本机目录或上传 zip**（校验 `SKILL.md` front matter、路径与 hash），**不做远程市场/下载**。
  Agent 级按 member 分区、频道级是频道分区，装配时 Agent 级同名覆盖频道级；删除只删配置与工作区副本，
  不删用户源目录。
- **联网档（#184 / ADR-0026 §二）**：`channel.web_access` 是频道默认值、`agent_config.web_access` 是
  Agent 级覆盖（`NULL` = 跟随频道），取值只有 `NULL` / `'search'` / `'search+fetch'`。频道 `NULL` = **关闭**
  （不挂任何联网工具，旧行因此逐字保持既有行为）；它**不是** `allow_local_shell` 的同义词——那个的风险面
  是本机任意进程，这个是数据出网。工具面是 `app/alibot/web.py` 的 `web_search`（只读，只送查询词）与
  `fetch`（非只读，逐次审批，`permission_rule` 的 `pattern` 是 **host**）：`NULL` 不装配；`read-only` 档
  只装 `web_search`；`ask-writes` / `trusted-writes` 装 `fetch` 且每次 ASK；缺 `('web', provider)` 确认行时
  两个工具都不装配（provider 由进程环境的 `ALIBOT_WEB_SEARCH_PROVIDER` / 单 key 推断决定）。网络请求
  **不进** `target_budget.used`（那是回应预算，ADR-0026 §六）。
- **容器出网（#187 / ADR-0026 §五）**：Docker 后端下容器默认 `--network none`；`channel.docker_egress=1`
  才接 Docker 默认网络，且联网工具只对 `permission_rule(tool_name='fetch', behavior='allow', pattern=<host>)`
  的 host 发请求（开关关闭 ⇒ 工具返回说明容器网络策略的可读错误、零请求）。本地后端不受影响。
- **远端数据面确认**：`remote_consent_scope(scope_kind ∈ {model, mcp, docker, web}, scope_id)` 是统一的一次性
  确认；旧 `remote_consent` 行保留为兼容面（迁移复制成 `('model', provider, …)`）。四类都**只存确认时间**。
  `web` 档的人类面写路径是 `POST /api/web-providers/{provider}/consent`（只接受 `brave` / `tavily`，
  幂等、只存确认时间）：未确认时装配层**不挂** `web_search` / `fetch` 且零请求，确认后下一轮按
  `web_access` 与权限档装配。已确认的 web provider 由 `GET /api/remote-data-flow` 的 `web_consents` 读回。

## 实时呈现（SSE 与断线补拉）

事件流按 target 订阅：频道 `GET /api/channels/{id}/events?after_seq=N`、任务线程
`GET /api/tasks/{id}/events?after_seq=N`、私信 `GET /api/dms/{id}/events?after_seq=N`。
三者同形；任务线程在 team 模式下还会产生 `agent_event` / `team_assignment` / `plan_update` /
`approval_*`（见下表）。事件类型：

| `type` | 含义 | 可补拉 |
| --- | --- | --- |
| `message` | 新消息落库（载荷与 `GET /messages` 的元素同形） | 是（读库） |
| `member_status` | 成员状态点变化（四态） | 否（连接时快照） |
| `typing` | 「正在回复」指示（一轮 in-flight 时 `active=true`；team 模式下成员也带 `member_id`） | 否 |
| `round_outcome` | 轮次结果（team 模式下只记录**队长**的轮次；成员产出在 `team_assignment` / `agent_event`） | 是（读库） |
| `agent_event` | `AgentEvent` 白名单透传（`event_type` / `member_id` / `reply_id` / `target_id` / `payload`；不含 secrets / base64） | 否 |
| `team_assignment` | 一次分派的状态（`team_run_id` / `assignment_id` / `task_id` / `member_id` / `reply_id` / `state`；task target 上 `task_id` 非空） | 否（快照 `GET /team-runs`） |
| `plan_update` | Plan 任务创建/更新/删除（`plan_run_id` / `member_id` / `task`） | 否（快照 `GET /plan`） |
| `sop_event` | SOP 步骤开始/结束/verdict（`sop_run_id` / `step_index` / `phase` / `payload`） | 否（快照 `GET /sop-runs/{id}`） |
| `approval_required` | 需要人类确认的工具调用（`reply_id` / `member_id` / `tool_calls`） | 否（快照 `GET /approvals`） |
| `approval_resolved` | 审批结果回执（`reply_id` / `state` / `decisions`） | 否 |

**补拉由后端读库完成**：连接建立时先发 `seq > after_seq` 的消息与轮次结果，再发一份当前状态点
快照，然后进入实时推送。前端**带最新 `last_seq` 重连**，因此重连既不会漏也不会重复。

**只有 `message` 与 `round_outcome` 的 SSE 帧带 `id: <seq>`**；其余事件是 **live-only**，不发 `id` 行
（ADR-0020）。否则浏览器会把 `Last-Event-ID` 推进到库里不存在的游标，重连补拉就会与消息游标混淆。
新事件断线不补发，运行面板以 `GET /team-runs` / `GET /plan` / `GET /sop-runs/{id}` 的快照兜底；
审批卡以 `GET /api/approvals?target_id=&state=pending` 兜底；`after_seq` 补拉仍只覆盖消息与轮次结果。

单条连接在 `ALIBOT_SSE_ROTATE_SECONDS`（默认 55 秒）后由服务端主动收尾并先发一个
`event: rotate` 帧：前端据此**区分「例行轮换」与「后端不可达」**，不会因为轮换而误报掉线，
随后立刻带最新 `last_seq` 重连。这个轮换同时保证优雅关停不会卡在长连接上。

状态点四态（`--online` 空闲 / `--working` 正在回复 / `--error` 出错 / `--offline` 未启用）
**只表达后端事实**，不从任何会话存活推导；`working` 脉冲在 `prefers-reduced-motion: reduce` 下关闭。

## HTTP 契约

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/workspace` | 工作台快照（成员、Agent、频道、未读合计、可用 provider） |
| `GET /api/settings` | 工作区设置（**语言偏好** / **消息密度** / **主题**；后端是唯一正本） |
| `PATCH /api/settings` | 写工作区设置 `{locale?, message_density?, theme?}`（部分更新；未知键与非法档位整体拒绝）。改设置**不落消息、不唤醒、不消耗预算** |
| `GET /api/reminders` | 全部提醒（`id` / `text` / `time`（`YYYY-MM-DD HH:MM`，**一次性**）/ `channel_id` + `channel_name`（可空 = 不归属）/ `created_at` / `fired_at`（`null` = 还没到点）/ `state`（`pending` / `fired` / `missed`，**后端派生的只读值**，不落库不占列））。每条恒含 `fired_at` 与 `state`——**无归属**提醒到点不落消息，这两个键是它唯一的到点证据。正本是库里的 `reminder` 表（ADR-0012 / ADR-0017） |
| `POST /api/reminders` | 新建一条提醒 `{text, time, channel_id?}`（`channel_id` 省略或 `null` = 不归属任何频道）。校验失败**整体不创建**；**不落消息、不唤醒、不消耗预算**、不新增 target、不计未读 |
| `DELETE /api/reminders/{id}` | 删除一条提醒；返回被删掉的那条与最新列表。不存在的 id ⇒ 404 |
| `GET /api/search?q=<关键词>&target_id=<可选>&limit=<可选>` | 跨 channel / DM / task target 搜索普通消息；默认上限 200，target 可收窄到单一作用域；每条命中返回消息、target（id / kind / 显示名）与后端计算的全部 `highlight_ranges` |
| `POST /api/attachments?name=<原文件名>` | **上传一个未绑定的附件**（请求体是文件原始字节、`Content-Type` 是声明 MIME）；返回标识与可读元数据。大小 / 类型上限在这里拒绝（可读原因，`code` + `fields`） |
| `GET /api/attachments/{id}` | 已绑定附件的内容（**草稿回 404**）；`Content-Disposition` 是原文件名，`?disposition=inline` 为浏览器内预览 |
| `GET /api/channels` | 频道列表（含 `last_seq` / `last_read_seq` / `unread_count`） |
| `POST /api/channels` | 新建频道 `{name, description, member_ids}`；校验失败**整体不创建** |
| `GET /api/channels/{id}/messages` | 按 seq 升序取消息（`?after_seq=n` 取增量） |
| `POST /api/channels/{id}/messages` | 发送人类消息 `{body, attachment_ids?}`（同事务分配 seq 并绑定附件，随后唤醒成员面） |
| `GET /api/channels/{id}/members` | 频道成员 |
| `POST /api/channels/{id}/members` | 把 Agent 加入频道 `{member_id}`；幂等（`joined` 区分新增与本来就在），只接受 Agent，校验失败**整体不加入** |
| `GET /api/channels/{id}/config` | 频道配置：`mode`（`autonomous` / `team`）、`team_scope`（固定 `channel+task`）、`team_leader_member_id`、`leader_available` / `leader_reason`、`workspace_root` / `workspace_backend` / `permission_mode` / `web_access` / `docker_egress` |
| `PATCH /api/channels/{id}/config` | 写频道配置 `{mode?, team_leader_member_id?, workspace_root?, workspace_backend?, permission_mode?, web_access?, docker_egress?}`；任一字段不合法**整体拒绝**；`workspace_root` 与 `web_access` 的显式 `null` = 清空 / 关闭（两者都是合法状态）；team 要求队长是当前频道成员且未删除；**不**自动改 `auto_claim` |
| `GET /api/targets/{id}/team-runs` | team 运行记录与分派快照（`?limit=&offset=&task_id=`；`started_at` 倒序、`has_more` 供翻页）。`target_id` 可以是频道或任务线程；`task_id` 只做命中过滤 |
| `GET /api/targets/{id}/plan` | 运行面板的 Plan 快照：按成员分组、每个 `(target_id, member_id)` 保留最近 20 run 或 30 天（`plan_task` 随 run 级联删除）。`target_id` 可以是频道或任务线程；**不是**任务看板（Plan 不进看板/未读/搜索） |
| `GET /api/sops` / `POST /api/sops` | SOP 定义列表 / 新建（owner；`steps` 含 executor/verifier member id、description 与 `max_attempts`） |
| `GET /api/sops/{id}` / `PATCH /api/sops/{id}` | 读取 / 启停与编辑（编辑只影响后续 run；既有 run 的 `state` 不重写） |
| `POST /api/sops/{id}/runs` | owner 启动一次 SOP 运行 `{target_id}`；`leader` 恒为 owner，返回 `run`（`phase=pending`） |
| `GET /api/sop-runs/{id}` | 运行快照：`phase`、逐步 verdict 与当前 `awaiting`（进程内详情，可空；重启后为 `null`） |
| `POST /api/sop-runs/{id}/resume` | 对 `awaiting` 的运行送入人类确认/外部结果 `{reply_id?, confirm_results?}`（与审批同形的结构化结果）；重启后必须显式给 `reply_id` |
| `GET /api/channels/{id}/round-outcomes` | 轮次结果（只读；首版**没有**可观测页面） |
| `GET /api/channels/{id}/budget` | 有界响应预算（后端持久事实）：`{epoch_seq, limit, used, remaining, source, pending_limit}`。`limit` = 当前人类世代的**有效上界**（`remaining` 按它算），`source` = `channel-override` / `built-in-default`，`pending_limit` = 已保存、**从下一个人类世代起生效**的上界（无待生效差异时 `null`）。频道覆盖存 `channel_budget_limit`，本世代生效值存 `target_budget.applied_limit` 快照 |
| `PATCH /api/channels/{id}/budget` | 保存回应预算**上界** `{budget_limit}`，只接受固定档位 `3` / `5` / `10`（默认 3）；其它取值**整体拒绝**、不留半成品。**用量不可写**：请求体带 `used`（或 `remaining` / `epoch_seq` / `applied_limit`）一律整体拒绝。成功返回刷新后的读数（与 `GET` 同形）；当前世代不被回溯改写，新值从**下一个人类世代**生效 |
| `GET /api/channels/{id}/events` | 事件流（SSE，`?after_seq=n` 补拉） |
| `POST /api/channels/{id}/read` | 标读 `{seq}`（同频道内取 max，裁剪到当前最大 seq） |
| `GET /api/agents` | Agent 成员 + 模型配置（含 **`auto_claim`**）+ 四态状态点 + 「为什么未启用」+ **概览读数**（`stats`） |
| `POST /api/agents` | 新建 Agent `{name, role, model_id, system_prompt, workspace_backend?, permission_mode?, allow_local_shell?, docker_image?}`；校验失败**整体不创建**（workspace 四项缺省 = 跟随频道 / 内置镜像；开关缺省关闭） |
| `GET /api/agents/{id}` | 单个 Agent（含可读的 `enabled_reason`、`auto_claim`、Agent 级 workspace 四项与 `web_access`、`stats`） |
| `PATCH /api/agents/{id}` | 编辑 Agent 配置 `{display_name?, role?, model_id?, system_prompt?, enabled?, auto_claim?, workspace_backend?, permission_mode?, allow_local_shell?, docker_image?, web_access?}`；任一字段不合法则**整体不写**。「暂停」就是 `enabled: false`，「自动认领任务」就是 `auto_claim`；`docker_image: ""` 是**显式清空**（回到内置镜像）。响应同形 `agent` + `agents` + `members` |
| `GET /api/agents/{id}/status` | 单个成员的状态点 |
| `GET /api/member-statuses` | 全部成员的状态点 |
| `GET /api/remote-data-flow` | 远端数据流告知文案与已确认记录（含 `consents` 与联网 provider 的 `web_consents`） |
| `POST /api/remote-data-flow/ack` | 确认一次告知 `{provider}`（幂等） |
| `GET /api/approvals?target_id=&state=pending` | HITL 审批快照（`reply_id` / `target_id` / `member_id` / `tool_calls` / `state` / `decisions` / 时间戳）。只读；`state` 只接受 `pending` / `approved` / `denied` / `expired` |
| `POST /api/approvals/{reply_id}` | 提交审批 `{decisions:[{tool_call_id, confirmed, rules?}]}`；只接受 `pending`，同批重试幂等，相反结论 409；决策内的 `rules` 落成该 Agent 的权限规则。批准后该轮继续，拒绝的工具结果是 `DENIED`（未执行），两者都不改动既有消息正本 |
| `GET /api/permission-rules?scope_kind=&scope_id=` | 权限规则列表（`allow` / `deny` / `ask`；不暴露 `BYPASS` / `DONT_ASK`） |
| `POST /api/permission-rules` | 写一条规则 `{scope_kind, scope_id, tool_name, pattern, behavior}`；同 `(scope, tool, pattern)` 覆盖；配置动作不落消息、不唤醒 |
| `DELETE /api/permission-rules/{id}` | 删除一条规则（配置动作；不删除消息与审批历史） |
| `GET /api/mcp-servers?scope_kind=&scope_id=` | MCP 声明列表；只回 `env_names`（**变量名**，永不回值）+ 进程内 `runtime` 读数（`connected` / `tools` / `error`） |
| `POST /api/mcp-servers` | 新建声明 `{scope_kind, scope_id, name, transport, command?, args?, url?, env_names?, enabled?}`；正文出现 `env` 之类键整体拒绝（400）；同名同 scope 409 |
| `PATCH /api/mcp-servers/{id}` | 部分更新同名字段；`scope_kind` / `scope_id` 不可改；整条结果重新校验 |
| `DELETE /api/mcp-servers/{id}` | 删除声明（配置动作；不删消息、不唤醒） |
| `POST /api/mcp-servers/{id}/consent` | 远端 MCP（`http` / `sse`）的数据面确认（幂等；stdio 不接受）。未确认的远端声明在装配层**不连接** |
| `POST /api/docker-images/consent` | 远端 Docker 镜像的数据面确认 `{image}`（幂等；写 `remote_consent_scope('docker', image)`，只存确认时间）。未确认的镜像在装配层**不拉取**、不启动容器 |
| `POST /api/docker-images/consent` | 远端 Docker 镜像的数据面确认 `{image}`（幂等；写 `remote_consent_scope('docker', image)`，只存确认时间）。未确认的镜像在装配层**不拉取**、不启动容器 |
| `POST /api/web-providers/{provider}/consent` | 联网 provider 的数据面确认（幂等；写 `remote_consent_scope('web', provider)`，只存确认时间）。只接受 `brave` / `tavily`；未确认时装配层**不挂** `web_search` / `fetch` 且**零请求** |
| `GET /api/skills?scope_kind=&scope_id=` | Skill 列表（`name` / `description` / `content_hash` / `enabled` / scope；不含字节） |
| `POST /api/skills` | 安装：JSON 正文 `{scope_kind, scope_id, source_path, channel_id?}` 从**本机目录**；或 `Content-Type: application/zip` 的原始字节 + query `scope_kind/scope_id/channel_id/filename` 从**上传 zip**。两者都先校验 `SKILL.md` front matter、路径与内容 hash，非法输入**整体拒绝**；同名同 scope 409；不做远端市场/下载 |
| `PATCH /api/skills/{id}` | 启停 `{enabled}`；scope 与 hash 是身份，不可改 |
| `DELETE /api/skills/{id}` | 删除配置与工作区副本（不删用户源目录；只影响该 scope） |
| `GET /api/health` | 存活与数据目录 |

错误统一为 `{"error": {"code", "message", "fields"?}}`。

### 附件：两步上传（先拿标识，随消息提交时绑定；ADR-0014）

附件是**独立对象**：元数据（原文件名 / 大小 / MIME / **相对路径** / 创建时间 / 可空的
`message_id`）落库，**字节落数据目录**的独立子目录（`<数据目录>/attachments/<附件标识>`）——
单文件库是业务正本与备份单位，把二进制塞进去会让库体积、备份与损坏半径一起失控。

1. `POST /api/attachments?name=<原文件名>`，请求体是文件原始字节、`Content-Type` 是声明 MIME
   （因此后端不需要 multipart 解析依赖）。上限：单文件 **10 MiB**、类型取一份窄白名单
   （文本 / JSON / CSV / PDF / PNG / JPEG / GIF / WebP），声明类型不在其中即拒。
   被拒的请求**不留**库内行、也不留孤儿字节。
2. 发送消息时带上 `attachment_ids: [...]`：**绑定与消息落库在同一事务里**——任一条绑定失败
   （不存在 / 已被绑定）都整体回滚，于是不会留下「绑了一半的草稿」或「有消息没附件」。
3. `GET /api/attachments/{id}` 取内容（草稿回 404）。

**未绑定的上传 = 未发送的草稿**：它不出现**任何**消息读取面（消息流 / 任务线程 / 私信 / 搜索
命中的消息形状里只有已绑定的附件），因此不需要后台清理器。附件**不可变**：上传后不改内容、
不改名、不覆盖；与消息同口径，**没有**删除附件的端点（`PUT` / `PATCH` / `DELETE` 都回 404）。
附件**不进搜索**（命中本体仍是文本），也**不进成员面**（附件内容不喂给 Agent）。

### Agent 详情与配置编辑（`PATCH /api/agents/{id}`，ADR-0011）

右侧 dock 的**第三形态**是 Agent 详情（入口：左栏 Agent 行、成员面板里的名字），含「概览」与「运行时」
两个页签；**日志页签不做**（用户人定 D24，入口另立 backlog 票 #72）。

- **概览的四个数字全部来自既有事实**，由后端在 `stats` 里聚合，前端不自己算：
  `channel_count` = 它是成员的 channel 型 target 数；`round_count` = 它的 `round_outcome` 行数；
  `completed_task_count` = 指派给它且 `status='done'` 的任务行数；`success_rate` =
  `replied ÷ (replied + ignored + failed)`（分母**不含 `silent`**：`silent` 与 `merged` /
  `budget-exhausted` 一样不计入分母——`db.py` 的 `AGENT_STATS_ROUND_SQL` 只把
  `replied` / `ignored` / `failed` 数进分母；没有样本时为 `null`，界面显示「—」）。原型里的 Token 消耗 / 上下文占用 / 内存 / 延迟在本仓**没有数据面**，
  因此不呈现。
- **运行时页签只呈现 `agent_config` 里真实存在的字段**：`model_id`、`provider`、`is_remote`、
  远端数据流告知状态、`role`、`system_prompt`、`enabled`、`auto_claim`（外加可用性推导出的当前状态）。
  Agent 级 workspace 覆盖（`workspace_backend` / `permission_mode` / `allow_local_shell` /
  `docker_image`）目前**只经 API 读写**（详情/编辑 UI 尚未暴露这四个控件）；`GET` / 列表响应逐字
  带回落库值，`null` 表示「跟随频道默认」。
- **写路径唯一**：`PATCH /api/agents/{id}` 可写 `display_name` / `role` / `model_id` /
  `system_prompt` / `enabled` / `auto_claim` / `workspace_backend` / `permission_mode` /
  `allow_local_shell` / `docker_image`，**任一字段不合法则整体不写**（不得部分写入）；`model_id`
  沿用与创建同一套校验与**远端数据流告知**语义。「自动认领任务」开关就在这个既有编辑面里
  （不另造第二个编辑面），写它只影响**之后新建**的任务。
- **配置变更不是发言**：不落消息、不唤醒、不消耗预算、不开世代（ADR-0011 与 ADR-0007 的边界：
  人类面可写配置类取值）。`auto_claim` 同此——它是编排在**新建任务之后**读的配置，不是动作。
- **「暂停」= 写 `enabled = 0`**，复用既有可用性判定：状态点为**未启用**、`enabled_reason` 可读；
  已知代价是它**仍会被唤醒**并落一条可读原因的 `failed` 轮次结果，只是不产出回复——「从唤醒面排除」
  是唤醒面的另一个决策，本轮不做。

### 未读口径

未读 = 已读位置之后、`kind ∈ {human, agent}`、**且作者不是本人**的顶层消息条数；`system` 事件
消息不计入。**作者排除本人**是实施票 ③ 按 #4 票面字面改判的（改前 #2 把 owner 自己的消息也
计入自己的未读）。`unread_count`（频道列表 / 单条）与 `unread_total`（workspace 快照）由
`db.py` 里**同一条 SQL** 得出，因此只有一个口径落点。

## 测试与检查

```sh
./scripts/check.sh            # 全量门禁：pytest + ruff check + ruff format --check + mypy + pyright + node --test（glob）
./scripts/check.sh --fast     # 开发循环：除全量 pytest 外的一切（秒级）
./scripts/check.sh --fast tests/test_send_message.py   # 再补跑指定 pytest（路径相对 app/）
```

`pytest` 是 HTTP + SSE 黑盒契约测试（每条用例独立临时数据目录）；ruff / mypy / pyright 的配置分别
在 `app/pyproject.toml` / 仓库根 `mypy.ini` / 仓库根 `pyrightconfig.json`。**不要在命令行补 `-q`**
（配置里已有 `addopts = "-q"`，补了会变 `-qq`，结果行会被吞掉）；**不要跑 `ruff format`**，只跑
`--check`。Studio 的 DOM 用例族由 `node --test app/tests/*.test.cjs` **按 glob** 跑（新增文件自动
纳入；每个文件覆盖什么写在各自的文件头注释里）。CI（`.github/workflows/check.yml`）跑的是同一个
`scripts/check.sh`。

Python 测试只断言**外部可观察行为**（消息出现与顺序、轮次结果、状态点、事件流帧、seq 单调性、
已读位置、未读计数、不可变性、重启后一致），不检查内部函数或前端 DOM；Studio 的 DOM 行为（搜索面、
Agent 详情面、旁白降档、频道设置弹窗、结构化沉默的摘要行与两种语言说法）由上面的 Node 内置测试
单独覆盖。每条 pytest 用例的库都在 `tmp_path` 下，**不触碰** `~/Alibot`。

上面这条纪律、以及「本文件的词表必须跟着 `db.py` 走」，都有**机械判据**：
`app/tests/test_test_layering_guard.py`（不读 Studio 源码 / 不断言词表形状 / 不 import 私有名）、
`app/tests/test_docs_vocabulary_guard.py`（状态词表与 kind 不与 README 漂移）、
`app/tests/test_schema_version_pins_guard.py`（不把 `SCHEMA_VERSION` 钉成历史号）。

**两处例外**，理由同源（词条表是**数据契约**，其键集不经 HTTP 面暴露）：

1. `app/tests/test_backend_copy.py` 直接读 `app/alibot/i18n.py` 的两份取值——两份取值必须覆盖同一键集、
   参数位一致，且后端文案必须取自**当前语言**的那一条。
2. `app/tests/test_reminder_delivery.py` 的 `test_the_two_copy_keys_are_backend_only` 直接读同一个
   `i18n.CATALOGS` 与 `app/alibot/studio/i18n.js` 的文本——它验的是**越界方向**：落库的
   `system.reminder.*` 正文只许进后端那一层，前端词条表里出现同一条就是 ADR-0009 明确否掉的「第三层」。
   这条反向判据没有 HTTP 读数面（它比的是两份**词条数据**本身）。

两处都只读数据契约，**行为断言仍然全部走 HTTP 面**（含 `test_reminder_delivery.py` 里其余每一条用例：
它们只经 `GET /api/reminders`、消息流、`budget`、`round-outcomes`、`search` 读数）。

五类 Agent 行为由 **脚本化替身**（`tests/scripted.py`）固定：回应 / 忽略 / 失败 / 延迟 / 声明沉默
（`SILENT`：经结构化通道声明本轮不说话，与「忽略」不是同一件事）。
测试**不联网、零成本**；「什么时候跑完一轮」由测试自己拨（手动调度器），所以 in-flight 与
合并这类窗口可以稳定观察。真模型连通性另有一条**默认跳过、本地手动运行**的冒烟测试：

```sh
# 本机 Ollama（零凭证、零费用）
ALIBOT_SMOKE_MODEL=ollama:qwen2.5 .venv/bin/python -m pytest tests/test_agent_smoke.py -q -s
# 远端 provider（会离开本机、可能产生费用，需要 key 与显式放行）
ALIBOT_SMOKE_MODEL=dashscope:qwen-max ALIBOT_SMOKE_ALLOW_REMOTE=1 \
  DASHSCOPE_API_KEY=... .venv/bin/python -m pytest tests/test_agent_smoke.py -q -s
```

回应预算的实测（#55 / ADR-0024）另有一个**手动入口**，默认不触网：

```sh
cd app
# 零成本 dry-run：脚本化替身走完整编排；报告标 usage_evidence=false
../.venv/bin/python -m tests.measure_budget \
  --local-model ollama:qwen2.5 --remote-model deepseek:deepseek-flash \
  --tier 3 --dry-run --out /tmp/issue-55-dry-run.json

# 真实测量：先预检（不建库、不调用模型），再显式 opt-in；--max-remote-usd 是观测后停机阈值，
# 不是硬费用上限。预检不通过时工具拒绝执行。
../.venv/bin/python -m tests.measure_budget \
  --local-model ollama:qwen2.5 --remote-model deepseek:deepseek-flash \
  --tier 3 --max-remote-usd <人定阈值> --preflight-only
ALIBOT_MEASURE_ALLOW_REMOTE=1 ../.venv/bin/python -m tests.measure_budget \
  --local-model ollama:qwen2.5 --remote-model deepseek:deepseek-flash \
  --tier 3 --prices specs/issue-55-model-prices-2026-10-18.json \
  --max-remote-usd <人定阈值> --out specs/issue-55-evidence-<date>.json
```

## 文案双语（后端）

后端**生成的自然语言**按**语言偏好**取值：HTTP 错误的 `message` 与 `fields` 的值、落库的
`system` 消息正文、`round_outcome.reason`、Agent 的 `enabled_reason`、远端数据流告知文案。
词条表是 `app/alibot/i18n.py`（源语言**简体中文** + `en` 两份取值，键集必须一致、参数位必须一致，
由 `app/tests/test_backend_copy.py` 当着机器判据）；语言偏好读 `workspace_settings` 那一行
（`GET` / `PATCH /api/settings`），**不读请求头 / `Accept-Language` / 环境变量**——写 `system`
消息与 `reason` 的是**唤醒编排**，它跑在 HTTP 请求之外，那里根本没有请求头可读（ADR-0009）。
库内没有**用户选过**的偏好时取源语言。

机器可读的东西**一律不译**：错误的 `code`、`fields` 的**键**、`round_outcome.status` 的词、
任务状态词、`provider` / `model_id` / 环境变量名。**历史不回填**：已落库的正文保持**写入时**的
语言（append-only，ADR-0001）——切换语言后此前的 `system` 消息仍是原语言，这是**已知代价**，
不是缺陷。前端界面文案是**另一层**，见 `app/alibot/studio/i18n.js`（#61）；CLI 诊断文案
（`__main__.py` / `repair.py`）不在双语范围内。

## 视觉契约

工作台只**只读**参照 `alibot-design-system/`（原型 `alibot-app.html` 保持原样，不被修改）：
token 名、三栏尺寸（rail 252 / 消息流 880 / dock 380）、方形头像块（7px 圆角 + `--av-0…--av-4`）、
未读徽标色族（`--unread` / `--unread-soft` / `--unread-deep` / `--unread-graphic`）、
四态状态点、提及高亮（`--accent-soft` 底 + `--accent-deep` 字）与空态文案都按名字消费。
色值一律 `oklch()`，界面为简体中文。

**主题三档（#71）**：`workspace_settings.theme ∈ {light, dark, system}`，前端把档位写到
`<html data-theme>` 上。取值层在 `alibot-design-system/`——两个 foundation 与产品扩展层的
两个落点各持**同一 token 名集合的第二组取值**（不改名、不新增色相）：显式档位由
`:root[data-theme='dark']` 命中，「跟随系统」由该包的 `@media (prefers-color-scheme: dark)`
裁决（本工作台**不**解析媒体查询）。工作台侧的呈现改动只有三处「用错层」：头像块的墨色改用
`--on-accent`（底是 fill 档）、`.overlay` 的遮罩改用 `--on-accent`（遮罩必须两档都暗）、
toast 的状态点在深色档改用 `--bg`（深色档 toast 反相成浅片）。`app.css` 里的裸色值纪律
在深色档下仍然成立。

业务状态的唯一正本是后端；前端不使用 `localStorage` 保存任何业务状态
（唯一允许的本地记忆是「上次打开的频道 id」）。后端不可达时前端**禁止提交**并给出明确提示，
不做本地消息队列、不静默吞消息，重连后自动恢复。
