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

## 数据模型（决策级）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `member` | id, kind(`owner`/`agent`), 显示名, 头像色位 | 身份正本；空库只有一条 `owner` 行 |
| `agent_config` | member_id, 角色, 模型标识, 系统提示, 启用开关, **自动认领开关**（`auto_claim`，缺省关闭）, **workspace 后端 / 权限档**（可空 = 跟随频道）, **本地 shell 开关**（`allow_local_shell`，缺省关闭）, **Docker 镜像**（可空 = 内置） | 模型与系统提示是**配置数据**，换模型不改代码；`auto_claim` 决定编排要不要替它在新建任务后认领；workspace 配置见 ADR-0021 |
| `task` | id, target_id, channel_id, 标题, 状态, 指派者, **`claimed_at`** | 任务当前态在行上；`claimed_at` 非空 = 这张任务**已被认领过**（「每张任务至多一次」的落点） |
| `permission_rule` | id, scope_kind(`channel`/`agent`), scope_id, tool_name, pattern, behavior(`allow`/`deny`/`ask`), created_at | 权限规则是**配置**：三档行为，不暴露 BYPASS / DONT_ASK（ADR-0021 §四） |
| `approval_request` | reply_id, target_id, member_id, team_run_id(可空), tool_calls(JSON), state(`pending`/`approved`/`denied`/`expired`), decisions(JSON), requested_at, resolved_at | HITL 审批记录；只存工具调用与决策，**不存凭证**（ADR-0021 §四） |
| `mcp_server` | id, name, transport(`stdio`/`http`/`sse`), command/args/url, env_names, enabled, scope_kind/scope_id | MCP 声明；`env_names` 只存变量**名**，值只从进程环境读（ADR-0021 §三） |
| `skill` | id, name, description, source_path, content_hash, enabled, scope_kind/scope_id | Skill 元数据；字节在 `workspace_root/.alibot/skills/`，只接受本机来源 |
| `remote_consent` | provider, acknowledged_at | 远端数据流告知的一次性确认；**不存任何凭证**（兼容面，迁移后读路径优先读 `remote_consent_scope`） |
| `remote_consent_scope` | scope_kind(`model`/`mcp`/`docker`), scope_id, acknowledged_at | 远端数据面的统一一次性确认；旧 `remote_consent` 行复制成 `('model', provider, …)` |
| `workspace_settings` | id(=1), locale, message_density, theme, updated_at | **工作区设置**：单行表，列就是明确列出的档位（语言偏好 / 消息密度 / 主题三档）。人类面可写配置类取值，**不得**写用量类事实 |
| `reminder` | id, text, time(`YYYY-MM-DD HH:MM`，**一次性**), channel_id(可空 = 不归属), created_at, **`fired_at`**(可空) | **提醒**（ADR-0012 / ADR-0017）：库内对象，后端是唯一正本（不放 localStorage）。它与频道的关系是**归属**，不是 target 作用域——**不**新增 target、不计未读、不参与唤醒；增删**不落消息、不唤醒、不消耗预算**。`time` 是**一次性**到期时刻（按本机本地时间解释，**无重复字段**）；`fired_at` 非空 = 已投递（UTC），`NULL` = 还没到点 |
| `channel` | id, 名称, 描述, **`workspace_root`**(可空 = 未配置), **workspace 后端 / 权限档默认值** | `workspace_root` 是**频道级项目边界**（ADR-0021 §一）；后端缺省 `local`、权限档缺省 `ask-writes` |
| `channel_member` | channel_id, member_id | 每频道显式成员表 |
| `channel_read` | member_id, channel_id, last_read_seq | 已读位置（未读的正本）。owner 的行使未读徽标；agent 的行是它自己的消费游标 |
| `message` | id, target_id, seq, author_member_id, kind, 正文, **提及快照** | **append-only**，`UNIQUE(target_id, seq)` |
| `attachment` | id, 原文件名, 大小, MIME, **相对路径**, 创建时间, **可空的 message_id** | 附件的元数据；**字节在数据目录**、不进 SQLite。`message_id` 为空 = 未发送的草稿（读取面不暴露）。绑定一次即不可变，**没有**删除路径 |
| `round_outcome` | member_id, target_id, base_seq, status, reason, 起止时间, 是否消耗预算, 是否被合并 | 轮次结果，**append-only**。`consumed_budget` 是**行级**标记（这一轮由一次已计一次的跳送来），**不可按行求和**——跳数正本是 `channel_budget.used` |

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
  频道的 Agent）。穿透不豁免预算。
- **回应判断**：被个人提及是确定信号 → **必须回应**（自判忽略按 `failed` 处理，不静默）；
  明确无关 → `ignored`（正常协议选择，不是失败）。**声明沉默**（`silent`）在确定信号下同样收口成
  `failed`——`silent` 本身不是失败，只是「它决定不说话」在必须回应的场合不算回应（与 `ignored`
  同款，见 `orchestration.py` 的 `definite_signal` 分支）。
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
- **预算落库**（`channel_budget` 表）：`used` / `epoch_seq` 是后端持久事实，重启后可恢复。
- **`budget-exhausted` 不是出错**：它既不是「不想说」也不是「它坏了」，状态点仍是空闲。
- **跳数的单一正本**：`channel_budget.used`（= `GET /api/channels/{id}/budget` 的 `used`）是「该人类
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

## 实时呈现（SSE 与断线补拉）

事件流按频道订阅：`GET /api/channels/{id}/events?after_seq=N`。事件类型：

| `type` | 含义 |
| --- | --- |
| `message` | 新消息落库（载荷与 `GET /messages` 的元素同形） |
| `member_status` | 成员状态点变化（四态） |
| `typing` | 「正在回复」指示（一轮 in-flight 时 `active=true`） |
| `round_outcome` | 轮次结果 |

**补拉由后端读库完成**：连接建立时先发 `seq > after_seq` 的消息与轮次结果，再发一份当前状态点
快照，然后进入实时推送。前端**带最新 `last_seq` 重连**，因此重连既不会漏也不会重复。
每一帧的 SSE `id` 就是频道内 `seq`。

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
| `GET /api/channels/{id}/round-outcomes` | 轮次结果（只读；首版**没有**可观测页面） |
| `GET /api/channels/{id}/budget` | 有界响应预算（后端持久事实）：`{epoch_seq, limit, used, remaining, source, pending_limit}`。`limit` = 当前人类世代的**有效上界**（`remaining` 按它算），`source` = `channel-override` / `built-in-default`，`pending_limit` = 已保存、**从下一个人类世代起生效**的上界（无待生效差异时 `null`）。频道覆盖存 `channel_budget_limit`，本世代生效值存 `target_budget.applied_limit` 快照 |
| `PATCH /api/channels/{id}/budget` | 保存回应预算**上界** `{budget_limit}`，只接受固定档位 `3` / `5` / `10`（默认 3）；其它取值**整体拒绝**、不留半成品。**用量不可写**：请求体带 `used`（或 `remaining` / `epoch_seq` / `applied_limit`）一律整体拒绝。成功返回刷新后的读数（与 `GET` 同形）；当前世代不被回溯改写，新值从**下一个人类世代**生效 |
| `GET /api/channels/{id}/events` | 事件流（SSE，`?after_seq=n` 补拉） |
| `POST /api/channels/{id}/read` | 标读 `{seq}`（同频道内取 max，裁剪到当前最大 seq） |
| `GET /api/agents` | Agent 成员 + 模型配置（含 **`auto_claim`**）+ 四态状态点 + 「为什么未启用」+ **概览读数**（`stats`） |
| `POST /api/agents` | 新建 Agent `{name, role, model_id, system_prompt}`；校验失败**整体不创建**（开关缺省关闭） |
| `GET /api/agents/{id}` | 单个 Agent（含可读的 `enabled_reason`、`auto_claim` 与 `stats`） |
| `PATCH /api/agents/{id}` | 编辑 Agent 配置 `{display_name?, role?, model_id?, system_prompt?, enabled?, auto_claim?}`；任一字段不合法则**整体不写**。「暂停」就是 `enabled: false`，「自动认领任务」就是 `auto_claim`。响应同形 `agent` + `agents` + `members` |
| `GET /api/agents/{id}/status` | 单个成员的状态点 |
| `GET /api/member-statuses` | 全部成员的状态点 |
| `GET /api/remote-data-flow` | 远端数据流告知文案与已确认记录 |
| `POST /api/remote-data-flow/ack` | 确认一次告知 `{provider}`（幂等） |
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
- **写路径唯一**：`PATCH /api/agents/{id}` 可写 `display_name` / `role` / `model_id` /
  `system_prompt` / `enabled` / `auto_claim`，**任一字段不合法则整体不写**（不得部分写入）；`model_id`
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
