# alibot 应用后端 + 浏览器工作台（实施票 ② / ③）

在 #① 的频道消息闭环之上，把 **Agent 做成数据库里的成员行 + 模型配置**：新建 Agent（名称 +
角色 + 模型 + 系统提示）→ 启用远端模型前显式告知并确认数据面 → 在频道里 `@` 它 → 后端进程内的
AgentScope agent 真的读到这条消息并**自判**回应 / 忽略 / 失败 → 回复不经刷新就出现在消息流里，
断线后自动补齐（per-target 流带 `last_seq` 补拉，工作台唯一那条全局流靠全量重取，见「实时呈现」）。

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

### 数据目录：默认在隐藏根下，备份方式仍是复制目录

| 项 | 值 |
| --- | --- |
| 默认端口 | `8788`（`--port` 可改） |
| 默认数据目录 | `~/.alibot/data/`（隐藏根下；旧 `~/Alibot/` 只做**兼容读取**） |
| 覆盖方式 | `--data-dir <路径>` 或环境变量 `ALIBOT_DATA_DIR`（只改数据目录本身，**不移动**全局层根） |
| 库文件 | `<数据目录>/alibot.db`（单文件 SQLite，WAL 模式） |
| 附件字节 | `<数据目录>/attachments/<附件标识>`（**不进 SQLite**；库里只存相对路径，因此整个数据目录可整体搬迁） |
| 迁移审计台账 | `<数据目录>/schema-migrations.jsonl` |
| 会话状态（无项目根） | `<数据目录>/sessions/<target_id>--<member_id>/`（`state.json` 与 `offload/`；ADR-0032 §四） |
| 事件流单连接寿命 | `ALIBOT_SSE_ROTATE_SECONDS`（默认 55 秒，到期服务端主动收尾；前端按既有纪律重连：per-target 流带 `last_seq`，全局流重开后全量重取） |

数据目录按**固定优先级**解析（结果是确定值，不靠时间戳或遍历顺序）：

1. `--data-dir` 或 `ALIBOT_DATA_DIR`：使用显式值（既有语义不变）；
2. 新默认 `~/.alibot/data/` 已存在：使用新默认；
3. 旧 `~/Alibot` 已存在：继续用旧位置，并给一条可读兼容提示；
4. 两者都不存在：新建 `~/.alibot/data/`。

启动时打印**实际数据目录**与来源（显式覆盖 / 新默认 / 旧位置兼容 / 新建）。新旧同时存在时确定地选
新默认、输出说明旧位置被忽略。应用**绝不删除、搬移、合并**用户数据，启动时**绝不自动迁移**；
全局公共库 `~/.alibot/skills/` 仍由人维护，应用不创建。创建 Agent 时应用建出 `agents/<member_id>/`
与 `reme/` 工作区；不在启动时创建 `AGENTS.md`、成员 `skills/` 或 `mcp.json`。应用安装技能时写成员
`skills/`；成员家目录 `mcp.json` 是**导入源 + Studio 的写面**（人类面直接编辑原文，保存前校验），
**不参与运行时装配**——运行期只看 `mcp_server` 的 Agent 级库行；要让它生效先导入（`migrate-mcp`），
未导入的条目仅得到一条可读提示（ADR-0031 §二 / #275）。
旧前缀 `WORKSPLICE_*` 与更早的数据目录 `~/Worksplice` 仍**不被识别**（那是另一个项目的目录）；
本节的兼容读取只针对 `~/Alibot`。

备份 / 恢复（显式路径，可直接执行）：

```sh
# 数据目录（库 + 附件 + 迁移台账 + 技能迁移清单，整目录 = 备份单位）
mkdir -p ~/.alibot-backups
cp -R ~/.alibot/data ~/.alibot-backups/data-$(date +%F)
# 恢复：反向复制回去（先停应用）
cp -R ~/.alibot-backups/data-YYYY-MM-DD ~/.alibot/data
# 若仍在使用旧位置做兼容读取，把上面的 ~/.alibot/data 换成 ~/Alibot。

# 全局层：不在数据目录备份内，必须单独备份
# agents/ 里有人工维护的 AGENTS.md、应用写入的 skills/ 与 ReMe 工作区；mcp.json 是人工维护的导入源，
# 并可在 Studio 的 mcp 页签里直接改原文（写入发生在宿主进程）。
# 整目录复制可覆盖这些素材；公共技能库 ~/.alibot/skills/ 另行备份。
cp -R ~/.alibot/agents ~/.alibot-backups/agents-$(date +%F)
cp -R ~/.alibot/skills ~/.alibot-backups/skills-$(date +%F)
# 恢复：反向复制回去
```

库文件旁边可能有 `-wal` / `-shm` 两个伴随文件，一起复制即可。
**附件字节在 `<数据目录>/attachments/` 里**（#67）：只备份 `alibot.db` 的备份**不再是完整备份**，
它会是「有元数据、没字节」的半份——备份单位是**整个数据目录**。
**迁移前会自动备份**：`--migrate` 在动手之前把整个数据目录复制成同级的
`<数据目录>-backup-<from>-to-<to>-<UTC 时间戳>`；**备份失败就不迁移**（不会退化成"放行 + 一句警告"）。
`python -m alibot migrate-skills` 使用**同一整目录备份机制**，但备份名带 `skills` 标签
（`<数据目录>-backup-skills-<UTC 时间戳>`）以与 schema 迁移区分，并在数据目录内落
`skill-migration-<UTC>.json` 清单（逐条来源 / 目标 / 动作，审计正本）。
`python -m alibot migrate-mcp` 同理，备份名带 `mcp` 标签、清单名 `mcp-migration-<UTC>.json`。

### MCP 声明迁移：先导入、再升级 / 删文件

`mcp_server` 的 Agent 级库行是**唯一装配来源**；家目录 `mcp.json` 只是**导入源 + Studio 的写面**，
**不参与运行时装配**。因此存量文件条目必须**先导入**才会生效，顺序不能颠倒：

```sh
# 1) 停应用（运行中的进程可能正在装配）
# 2) 先预演（默认行为，零写入、零备份）
python -m alibot migrate-mcp --data-dir ~/.alibot/data
# 3) 逐条读「新建 / 跳过 / 拒绝」。有「拒绝」＝库里已有同名但形状不同，先人工裁定
#    （命令不覆盖、没有 --force），处理完再看一遍预演，直到没有「拒绝」。
# 4) 执行（破坏性动作必须两个开关）：先整目录备份 + 落导入清单，再单事务写库行
python -m alibot migrate-mcp --apply --yes --data-dir ~/.alibot/data
# 5) 远端条目（http / sse）的确认**没有被代做**：按报告里逐条的 id 调
#    POST /api/mcp-servers/{id}/consent（或在 Studio 的 mcp 页签点「确认使用」）
# 6) 重启应用，再校验工具面（运行期读数 + 工具清单）
```

顺序的三个要点：

- **先导入、再删文件**：删了 `mcp.json` 不会丢失已导入的库行（正本在库里），但删得早且导入失败就真的丢了源。
  本命令**从不删除源文件**，文件要不要留由人决定。
- **不要指望重启/升级自动搬**：导入是显式动作，启动路径上绝不自动迁移（与 `migrate-skills` 同一口径）。
- **未导入的条目不会静默生效**也不会静默消失：运行期只给一条只进 `RoundOutcome.reason` 的可读提示，
  点名「文件里还有 N 条未导入」并给出这条命令。

从旧默认位置搬到新默认位置是**显式动作**（启动绝不自动搬移）：
**停应用 → 整目录复制 → 校验 → 指向新位置**，复制前先做整目录备份。

```sh
# 1) 停应用；2) 复制前先整目录备份
mkdir -p ~/.alibot-backups
cp -R ~/Alibot ~/.alibot-backups/legacy-$(date +%F)
# 3) 整目录复制（库 + 附件 + 迁移台账一起过去；不要只复制 alibot.db）
mkdir -p ~/.alibot
cp -R ~/Alibot ~/.alibot/data
# 4) 校验：库可打开、附件数量一致、台账存在
python -m alibot --migrate --dry-run --data-dir ~/.alibot/data
ls ~/Alibot/attachments | wc -l
ls ~/.alibot/data/attachments | wc -l
test -f ~/.alibot/data/schema-migrations.jsonl
# 5) 指向新位置：不传 --data-dir 即自动使用新默认；旧 ~/Alibot 此后被忽略（不删除）
```

### 规则与旧记忆落点的搬迁（ADR-0032 / ADR-0034）

ADR-0032 起规则文件的落点是**同构扁平的家目录**；ADR-0034 起长期记忆不再用 Markdown 文件，落点是
每个成员自己的 ReMe 工作区 `~/.alibot/agents/<member_id>/reme/`。应用**只读新规则路径**，旧路径
一律不读取、不搬移、不删除——搬迁是人的显式动作，先备份再复制：

```sh
# 0) 停应用；先备份整个全局层（规则 / 技能 / 记忆都在这棵树里）
mkdir -p ~/.alibot-backups
cp -R ~/.alibot/agents ~/.alibot-backups/agents-$(date +%F)

# 1) 全局层规则：rules/AGENTS.md → AGENTS.md
for home in ~/.alibot/agents/*/; do
  [ -d "$home/rules" ] && cp -n "$home/rules/AGENTS.md" "$home/AGENTS.md" 2>/dev/null
done

# 2) 项目层规则：<ws>/.alibot/rules/<id>/AGENTS.md → <ws>/.alibot/agents/<id>/AGENTS.md
for ws in /path/to/your/projects/*; do
  for old in "$ws"/.alibot/rules/*/; do
    [ -d "$old" ] || continue
    id=$(basename "$old")
    mkdir -p "$ws/.alibot/agents/$id"
    cp -n "$old/AGENTS.md" "$ws/.alibot/agents/$id/AGENTS.md" 2>/dev/null
  done
done

# 3) 校验：规则文件在新路径上
ls ~/.alibot/agents/*/AGENTS.md 2>/dev/null
```

**旧的 Markdown 记忆不导入 ReMe**：`MEMORY.md`、`<topic>.md`（以及更早的
`memory/<id>/Memory/*.md`）在升级后**原样留在磁盘上**，应用既不读也不删；ReMe 也不会把它们吃进
工作区。要沿用旧内容，需要人工整理后重新交给 Agent（或让它在新会话里重新记住）。旧路径
（`data/.alibot/memory/**`、`<ws>/.alibot/memory/**`、`<ws>/.alibot/rules/**`、
`~/.alibot/agents/<id>/rules/**`、`.../memory/Memory/**`）可以自行清理——应用**不会**替你删。

**旧 sessions 可直接弃用**：`~/.alibot/data/.alibot/sessions/**` 与
`<ws>/.alibot/sessions/**` 里的旧 `state.json` 是**运行状态**，不是事实正本；消息正本在库里，
所以丢掉它只影响下一轮的续接质量（压缩摘要与工具缓存需要重新积累），不会丢消息。无项目根的轮次
现在落在 `<数据目录>/sessions/`（ADR-0032 §四），旧镜像层不再被写入。

从首版升级（ADR-0015）：**启动仍然拒绝** schema 版本与本版不等的库（不猜、不跳过），但报错正文会直接给出
迁移命令与备份指引；把旧库升到本版要**显式**执行 `python -m alibot --migrate --data-dir <数据目录>`
（迁移前自动备份整目录、单事务收口、幂等；`--migrate --dry-run` 在临时副本上预演完整链，并打出原库
sha256 前后一致的读数）。`--repair-targets` 已降为 `--migrate` 的**弃用别名**，仍可处置"版本号已是 4、
但 channel 型作用域数据还没搬"的半改名库（列表页正常但点进频道全 404）。

**MCP 声明**的迁移是另一条命令、另一个阶段：`python -m alibot migrate-mcp`（不推进 schema 版本，
默认预演，`--apply --yes` 才写；先备份 + 落清单；同名不同形整体拒绝；不删源文件；不代做远端确认）。
顺序是**先导入、再升级/删文件**，详见上面的「MCP 声明迁移」一节。

### Schema 版本与迁移（15 → 25）

本代码的 `SCHEMA_VERSION = 25`（`app/alibot/db.py`），依赖 pin `agentscope[memory-reme]==2.0.10`
（2.0.10 的 extra 名就是 `memory-reme`；该 extra 只声明 `reme-ai>=0.4.0.6`，因此 ReMe 另显式 pin
`reme-ai==0.4.1.10`——0.4.1.13 与 2.0.10 的最小 ReMe 配置不兼容，嵌入应用启动即报
`Unregistered backend 'dream_topics_step'`；升级 reme-ai 要单独开票并重跑真实冒烟）。本轮把库形态从 15
推进到 25，由**十个严格 +1** 的步骤组成（规格 §4.1；ADR-0015 的链连续性纪律不变）：

| 步骤 | 名称 | 加什么 |
| --- | --- | --- |
| `15 → 16` | `agentscope-2 team/plan/sop expand` | `channel.mode` / `channel.team_leader_member_id`（+ team 必须有队长的触发器）；`team_run` / `team_assignment` / `plan_run` / `plan_task` / `sop_definition` / `sop_run` |
| `16 → 17` | `agentscope-2 workspace/permission/mcp/skill expand` | `channel.workspace_root` / `workspace_backend` / `permission_mode`；`agent_config.workspace_backend` / `permission_mode` / `allow_local_shell` / `docker_image`；`permission_rule` / `approval_request` / `mcp_server` / `skill` / `remote_consent_scope`；旧 `remote_consent` 行复制成 `('model', provider, acknowledged_at)` |
| `17 → 18` | `agent web access config` | `channel.web_access` / `agent_config.web_access`（`NULL` / `'search'` / `'search+fetch'`）；`remote_consent_scope.scope_kind` 的 `CHECK` 纳入 `web`（表级 `CHECK` 只能**重建表**，重建后既有行逐行保真） |
| `18 → 19` | `docker container egress switch` | `channel.docker_egress`（`0` = 容器 `--network none`，缺省；`1` = 容器接 Docker 默认网络，且联网工具只放行 `permission_rule` 里 `fetch` 的 `allow` 规则的 host） |
| `19 → 20` | `channel task pointer message` | `message.task_pointer`（可空，`REFERENCES task(id)`）+ 部分唯一索引 `idx_message_task_pointer`（一张任务至多一条）+ `message_task_pointer_is_scoped` 触发器（必须是 channel 型 target 的 system 消息，且任务属于该频道）。**不回填**旧任务 |
| `20 → 21` | `auto claim defaults on` | `agent_config.auto_claim` 的 DDL 默认 `0 → 1`；按当前 `SCHEMA` **重建** `agent_config`（升级库的新插入也拿到 1），并把存量 `auto_claim = 0` 一次性回填为 `1`（只改这一列、不动 `updated_at`；报告真实改动行数，重放是空操作） |
| `21 → 22` | `web provider credentials + explicit off level` | `web_provider_credential`（provider 主键、`api_key` 明文、`is_default`、时间戳；部分唯一索引保证至多一个默认）；重建 `channel` / `agent_config` 的 `web_access` 列级 CHECK，纳入 `'off'`（既有行逐字保留） |
| `22 → 23` | `agent runtime context and memory` | `round_outcome.finished_reason`（可空，旧行保持 NULL）；`agent_config.context_window_messages` / `context_trigger_ratio` / `react_max_iters` / `memory_mode`（列默认 `40` / `0.7` / `30` / `'off'`；显式 NULL 跟随运行时默认） |
| `23 → 24` | `long-term memory to ReMe (superseded)` | 历史步骤（ADR-0034 的 ReMe 换档）。v25 起 `memory_mode` 由 `24 → 25` 删除，本版代码里这一步不重建、不改写取值，只保留链连续性；存量旧档 `agentic` 的语义随列删除一起退场 |
| `24 → 25` | `long-term memory is always on` | 重建 `agent_config`：删掉 `memory_mode` 列（当前 DDL 已不声明它，逐列复制即删列）；存量 `off` / `NULL` 行因此变为「始终开启」（ADR-0035），其它列逐字保留 |

- **只加不删（唯二例外：`20 → 21` 的一次性回填与 `24 → 25` 的删列）；除这两处外不回填业务数据**：
  新列可空或带列默认值，旧行按默认值解释——
  `mode='autonomous'`、`team_leader_member_id=NULL`、`workspace_root=NULL`、`workspace_backend='local'`、
  `permission_mode='ask-writes'`；`agent_config` 的运行时配置列取列默认值（`40` / `0.7` / `30`）；
  `17 → 18` 的两列 `web_access` 也一律停在 `NULL`（= 未配置 / 跟随频道，读路径回退默认档 `search`，
  见下面的**联网档**）；`18 → 19` 的 `docker_egress` 停在列默认 `0`（= 容器默认无网，见下面的**容器出网**）；
  `19 → 20` 的 `message.task_pointer` 停在 `NULL`（= 不是指针消息）；
  `21 → 22` 的两列 `web_access` 仍停在 `NULL`（同上，读路径回退默认档 `search`）；
  `22 → 23` 的旧轮次结束原因保持 `NULL`。
  `20 → 21` 是一次**显式的语义变更**：它把存量 `auto_claim = 0` 一次性置 1（ADR-0029），
  因此升级会覆盖此前显式关闭的存量行；它**不回溯任务**，频道里既有的未认领任务保持原状。
  `24 → 25` 是**唯一删列的一步**（ADR-0035）：`agent_config.memory_mode` 整列消失，存量 `off` /
  `NULL` 行因此变成「长期记忆始终开启」——这是决策本身的语义，不是业务数据回填。
- **迁移是显式动作**：`python -m alibot --migrate --data-dir <数据目录>`；`--dry-run` 在数据目录内的临时
  副本上预演完整链，**原库 sha256 前后一致**，不建备份、不写台账、不留副本。迁移前自动把整个数据目录
  复制成同级 `<数据目录>-backup-15-to-22-<UTC 时间戳>`；**备份失败就不迁移**。
- **单向、无降级代码**：回滚 = 恢复迁移前的那份**整目录备份**（`cp -a` / 反向复制），再用旧版本代码打开。
  旧代码**不能**打开新版本库（版本闸门），所以「把 `SCHEMA_VERSION` 改回去」不是回滚。
- **失败即回滚**：步骤链在单事务内跑；任一步失败 ⇒ `ROLLBACK`、库仍停在源版本且未被改动、备份仍在、
  审计台账记一条失败记录。`#157` 的演练（`app/specs/issue-157-acceptance.md`）把 15→16→17、默认值、
  新表可查、备份恢复与失败注入逐项跑过。

## 数据模型（决策级）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `member` | id, kind(`owner`/`agent`), 显示名, 头像色位 | 身份正本；空库只有一条 `owner` 行 |
| `agent_config` | member_id, 角色, 模型标识, 系统提示, 启用开关, **自动认领开关**（`auto_claim`，缺省开启，仅在自主频道生效）, **workspace 后端 / 权限档**（可空 = 跟随频道）, **本地 shell 开关**（`allow_local_shell`，缺省关闭）, **Docker 镜像**（可空 = 内置）, **联网档**（`web_access`，可空 = 跟随频道）, **上下文窗口 / 压缩阈值 / 迭代上限** | 模型与系统提示是**配置数据**，换模型不改代码；`auto_claim` 决定编排要不要替它在新建任务后认领；workspace 配置见 ADR-0021。长期记忆没有配置列（始终开启，ADR-0035） |
| `target` | id, kind(`channel`/`dm`/`task`), dm_peer_member_id, created_at | 一切消息面的**唯一作用域键**（ADR-0004）：消息、已读位置、回应预算、轮次结果都挂 `target_id`。channel 型是**影子行**（id 与 `channel.id` 同值、只承载 `kind`） |
| `target_member` | target_id, member_id | **成员关系**的正本（三种 kind 共用一张表）：唤醒面与未读口径的共同来源；频道成员就是它在 channel 型上的读法 |
| `target_read` | member_id, target_id, last_read_seq | 已读位置（未读的正本）。owner 的行是未读徽标基准，agent 的行是它自己的消费游标 |
| `task` | id, target_id, channel_id, 标题, 状态, **`assignee_member_id`**(可空), **`claimed_at`** | 任务当前态在行上；`assignee_member_id` = 受派者；`claimed_at` 非空 = 这张任务**已被认领过**（「每张任务至多一次」的落点）。读路径载荷另派发 **`allowed_transitions`**（当前态的合法迁移目标，顺序固定五态、终态 `[]`；由迁移表派生，呈现面只读它，#191） |
| `permission_rule` | id, scope_kind(`channel`/`agent`), scope_id, tool_name, pattern, behavior(`allow`/`deny`/`ask`), created_at | 权限规则是**配置**：三档行为，不暴露 BYPASS / DONT_ASK（ADR-0021 §四） |
| `approval_request` | reply_id, target_id, member_id, team_run_id(可空), tool_calls(JSON), state(`pending`/`approved`/`denied`/`expired`), decisions(JSON), requested_at, resolved_at | HITL 审批记录；只存工具调用与决策，**不存凭证**（ADR-0021 §四）。主键 `reply_id` 是**回复级**标识（#215）：一条回复里的第 N 次 ASK 复用同一行，`tool_calls` / `decisions` 只追加；重开时 `state` 回 `pending`、`resolved_at` 置 NULL、`requested_at` 刷新到这一批的起点、旧 `decisions` 保留 |
| `mcp_server` | id, name, transport(`stdio`/`http`/`sse`), command/args/url, env_names, enabled, scope_kind/scope_id | MCP 声明正本，也是**唯一**装配来源；现行声明使用 `scope_kind='agent'`；旧 `channel` 行保留、弃用且装配忽略；成员家目录 `mcp.json` 是导入源 + Studio 写面（人类面直接编辑原文），**不参与装配**，导入走显式 `migrate-mcp`（ADR-0021 §三修订 / ADR-0031 §二） |
| `skill` | id, name, description, source_path, content_hash, enabled, scope_kind/scope_id | Skill 元数据；Agent 级字节由应用写入 `~/.alibot/agents/<member_id>/skills/<name>/`，不依赖 workspace；技能只按成员家目录 > 全局公共库装配。旧 `channel` 行与项目层字节保留、装配忽略（ADR-0021 §三修订 / ADR-0031 §二） |
| `remote_consent` | provider, acknowledged_at | 远端数据流告知的一次性确认；**不存任何凭证**（兼容面，迁移后读路径优先读 `remote_consent_scope`） |
| `remote_consent_scope` | scope_kind(`model`/`mcp`/`docker`/`web`), scope_id, acknowledged_at | 远端数据面的统一一次性确认；旧 `remote_consent` 行复制成 `('model', provider, …)`。**不存凭据** |
| `web_provider_credential` | provider(`brave`/`tavily`/`perplexity`/`jina`/`exa`), api_key, is_default(0/1), created_at, updated_at | 联网 provider 的凭据：**一家一行**，`api_key` 明文入库（部署层保证库文件 `0600`），HTTP / 日志 / 事件只回掩码（`sk-…abcd`）；`is_default=1` 至多一行 |
| `plan_run` / `plan_task` | run: id, target_id, member_id, base_seq, status, started_at, finished_at；task: plan_run_id, seq, subject, description, state(`pending`/`in_progress`/`completed`), owner, blocks/blocked_by(JSON), metadata(JSON) | **Plan** 是成员轮次内的工作清单（ADR-0023），**不是任务五态**：不进看板、未读、搜索、唤醒、预算，不新增 target。每个 `(target_id, member_id)` 保留最近 20 run 或 30 天，`plan_task` 随 run 级联删除；只进运行面板（`GET /api/targets/{id}/plan` + `plan_update`） |
| `sop_definition` / `sop_run` | definition: id, name, description, steps(JSON: executor/verifier member id、subject、description、max_attempts), enabled；run: id, sop_definition_id, target_id, leader_member_id, state(`SOPRunState` JSON), phase(`pending`/`running`/`awaiting`/`completed`/`failed`), started_at, finished_at | **SOP** 是 owner 显式启动的多步运行（ADR-0023），**不是任务五态**：不改任务行、不写消息、不进看板/未读/搜索/唤醒/预算，不新增 target。运行保留最近 50 个或 90 天；完成/失败不自动删除定义。步骤事件走 `sop_event`，快照是 `GET /api/sop-runs/{id}` |
| `workspace_settings` | id(=1), locale, message_density, theme, updated_at | **工作区设置**：单行表，列就是明确列出的档位（语言偏好 / 消息密度 / 主题三档）。人类面可写配置类取值，**不得**写用量类事实 |
| `reminder` | id, text, time(`YYYY-MM-DD HH:MM`，**一次性**), channel_id(可空 = 不归属), created_at, **`fired_at`**(可空) | **提醒**（ADR-0012 / ADR-0017）：库内对象，后端是唯一正本（不放 localStorage）。它与频道的关系是**归属**，不是 target 作用域——**不**新增 target、不计未读、不参与唤醒；增删**不落消息、不唤醒、不消耗预算**。`time` 是**一次性**到期时刻（按本机本地时间解释，**无重复字段**）；`fired_at` 非空 = 已投递（UTC），`NULL` = 还没到点 |
| `channel` | id, 名称, 描述, **`mode`**(`autonomous`/`team`), **`team_leader_member_id`**(team 必填), **`workspace_root`**(可空 = 未配置), **workspace 后端 / 权限档默认值**, **联网档 `web_access`**（`NULL` = 未配置 → 默认 `search`；`off` = 显式关闭）, **容器出网开关 `docker_egress`**（缺省 `0` = `--network none`） | `mode` / 队长是**频道级协作拓扑**（ADR-0018）；team 覆盖该频道的 channel 型 target 与**全部 task target**。`workspace_root` 是**频道级项目边界**（ADR-0021 §一）；后端缺省 `local`、权限档缺省 `ask-writes` |
| `target_budget` | target_id, epoch_seq, used, applied_limit | **回应预算的用量正本**（每 target 一行）：`epoch_seq` = 人类世代，`used` = 该世代已投递的跳数，`applied_limit` = 该世代生效上界的快照（`NULL` = 内置默认 3） |
| `channel_budget_limit` | channel_id, budget_limit(`3`/`5`/`10`), updated_at | 回应预算上界的**配置**落点；**没有行 = 回落到内置默认值**。保存只影响**下一个人类世代**，不回溯改写当前世代 |
| `team_run` / `team_assignment` | run: id, target_id, leader_member_id, base_seq, status(`running`/`completed`/`failed`/`interrupted`/`invalid-leader`), started_at, finished_at；assignment: team_run_id, target_id, task_id(可空), leader/member_id, tool_call_id, prompt, state(`started`/`succeeded`/`failed`/`interrupted`/`budget-exhausted`), jump_consumed, reply_excerpt, error | **team 模式的运行记录**（ADR-0018/0019/0022）：`target_id` 可以是 channel 型或该频道下的 task 型；task target 上 `task_id` 非空。它们**不是消息**：不占未读、不进搜索、不写频道消息；`jump_consumed` 只记「这次分派扣了一次跳」 |
| `message` | id, target_id, seq, author_member_id, kind, 正文, **提及快照**, task_ref(可空), **task_pointer**(可空) | **append-only**，`UNIQUE(target_id, seq)`。`task_ref` = 同 target 的任务引用（任务线程内）；`task_pointer` = **频道 system 消息 → 本频道任务**的指针（只由后端在建任务时写入；部分唯一索引 + `BEFORE INSERT` 作用域触发器，ADR-0028） |
| `attachment` | id, 原文件名, 大小, MIME, **相对路径**, 创建时间, **可空的 message_id** | 附件的元数据；**字节在数据目录**、不进 SQLite。`message_id` 为空 = 未发送的草稿（读取面不暴露）。绑定一次即不可变，**没有**删除路径 |
| `round_outcome` | member_id, target_id, base_seq, status, reason, finished_reason（旧行可空）, 起止时间, 是否消耗预算, 是否被合并 | 轮次结果，**append-only**。`consumed_budget` 是**行级**标记（这一轮由一次已计一次的跳送来），**不可按行求和**——跳数正本是 `target_budget.used` |

- **表清单与 `SCHEMA_VERSION = 25` 的 `SCHEMA` 一一对应**（`schema_meta` 只存版本读数，不是业务表）；
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
- **回应判断**：**确定信号有五类**——被个人提及、被**认领**唤醒、team 队长被**任务变化**唤醒、
  **人类消息**（`message.kind == "human"`，DM / autonomous 频道 / team 队长 / task 受派者四个面同口径，#189）、
  **自动续跑**（受派者用 `continue_task` 申请继续推进，ADR-0027）
  → **必须回应**（自判忽略按 `failed` 处理，不静默）；
  明确无关 → `ignored`（正常协议选择，不是失败）。**声明沉默**（`silent`）在确定信号下同样收口成
  `failed`——`silent` 本身不是失败，只是「它决定不说话」在必须回应的场合不算回应（与 `ignored`
  同款，见 `orchestration.py` 的 `definite_signal` 分支）。五类**同级不同源**：可读文案与提示词按来源选
  （`reason.claim_*` / `reason.continue_*` / `reason.team_change_*` / `reason.human_message_*` / 提及那一对），
  混用就会写下一条没发生过的事实。人类消息与自动续跑这两类**不改唤醒面、不扣跳、不改未读口径、不改消息 kind**，
  也不改非人类作者（Agent / system）的纪律。
- **每轮落一条 `round_outcome`**：`replied` / `ignored` / `failed` / `merged` / `budget-exhausted` /
  `silent`（`silent` = 模型经**结构化通道**声明本轮沉默，与 `ignored` 不同、也不是失败），
  带可读 reason、起止时间、是否消耗预算、是否被合并。
- **一轮至多一条频道消息**；落库路径只有一条。
- 消费游标只在 `replied` / `ignored` / `silent` 推进；`failed` 不推进（下次唤醒会重新读到，但不是自动重放）。

### Agent 运行时（schema 25 / ADR-0030 / ADR-0034 / ADR-0035）

普通轮输入最近 40 条 target 消息（本人历史为 assistant），自动续跑使用完整历史；回应纪律、瞬态早退与 base_seq 仍只按新消息计算，历史不重新触发必答。上下文超过阈值时由 AgentScope 压缩；压缩消息与截断工具结果写入会话分区：有项目根时 `.alibot/sessions/<target--member>/offload/`，无项目根时 `<数据目录>/sessions/<target--member>/offload/`（ADR-0032 §四）。

- `ALIBOT_CONTEXT_WINDOW_MESSAGES=40`、`ALIBOT_CONTEXT_TRIGGER_RATIO=0.7`、`ALIBOT_CONTEXT_RESERVE_RATIO=0.2`、`ALIBOT_CONTEXT_TOOL_RESULT_LIMIT=4000`、`ALIBOT_CONTEXT_MAX_IMAGE_NUM=5`、`ALIBOT_REACT_MAX_ITERS=30`、`ALIBOT_MODEL_MAX_RETRIES=2`。
- Agent 的显式数值配置优先；PATCH 显式 null 清除覆盖，省略保持原值。新建 Agent 的数值配置为空，跟随环境与默认；迁移存量行按 schema 默认解释。上下文条数 / 压缩阈值 / 迭代上限这三项在 Studio 详情页的**界面入口已撤除**（保存载荷不再提交这三个键——`PATCH` 是部分更新，库内既有值与默认值 40 / 0.7 / 30 原样保留）；长期记忆没有开关。
- 每轮按 Asia/Shanghai 重新注入 ISO 时间、target 名称与上下文用量；AgentState 原子保存到上述 session 分区的 `state.json`，恢复摘要及运行状态、按消息窗口重建 context。损坏状态降级为空白并记录日志；权限始终用当前配置。
- `finished_reason` 为 `completed | interrupted | exceed_max_iters | error`，与 status 独立。旧行保持 NULL；达到迭代上限的部分正文可保留，reason 明示结论可能不完整。
- `POST /api/targets/{target_id}/rounds/{reply_id}/interrupt` 仅中断当前匹配轮，成功 200，已结束 / 已请求 / target 不匹配 409。运行中取消任务、审批挂起时回送 UserInterruptEvent；中断不落回复消息，只落轮次结果，发出不带游标的 `round.interrupted`，Studio 停止按钮随后消失；SSE 重连通过当前 `round.active` 快照恢复按钮。审批超时仍为 failed。
- **长期记忆始终开启**（ADR-0035：没有开关），使用 AgentScope 的 **ReMe** 中间件（ADR-0034）：
  工作区固定在 `~/.alibot/agents/<member_id>/reme/`，**一个成员一个**（PER_AGENT），跨 session /
  target / 项目共享召回。每轮用**本轮模型**装配 `ReMeMiddleware`（`top_k=5`；provider 不能携带工具时
  退化为 `static_control`，`memory_search` 不送出去），轮次收尾自动写回、回复前按 BM25 关键词检索注入；
  不启用 embedding，不新增凭据面。写回后索引由后台循环**最终一致**：刚写入或刚启动时立即检索可能
  为空，下一轮回复的召回已可用——不要据此判记忆失效。单 Agent 与 team 两处 finally 都在关闭 model
  client **之前** `await close()`。全局层未接线时这一轮不装中间件、不建目录（`assemble_memory` 返回
  `None` 并记日志）。记忆**不经过文件工具**：DM 仍可 Read 当前会话卸载目录；成员技能目录是本地
  Read / Glob / Grep 的只读第二根，Write / Edit 仍只限项目根（无项目根时不可写）。
- 单 Agent、队长与 team 成员三条装配路径的 system prompt 都注入一段「本轮运行环境」（工作区根 / 后端 / 权限档 / 长期记忆固定「已开启」），取值只来自本轮装配事实：没有项目根就明写「未配置」，记忆卡片与本段冲突时以本段为准，并要求模型不得编造本段未给出的路径（#263 / #267）。SOP 参与者不挂工具、不装配记忆，因此没有这一段。
- Docker 不可用时运行时停用工作区工具并给出可读降级，继续对话；不回落为本地项目工具。卸载文件写会话分区：有项目根时是主机 `.alibot/sessions/`（Docker 下显示为 `/workspace/.alibot/sessions/...`），无项目根时是 `<数据目录>/sessions/`（ADR-0032 §四）。

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

### Agent 主动认领（`auto_claim`，ADR-0010 / ADR-0016 / ADR-0029）

频道内**新建任务之后**，由**编排**裁决认领——不让模型自己改数据库：候选 = 该频道里**可用**
（模型配置、启用态、凭证、远端告知）且 `auto_claim` 开启的 Agent 成员，按创建顺序取**第一个**。
一次只认领一个，不抢、不重复指派；开关**缺省开启**（#195 / ADR-0029），生效条件是合取：
`channel.mode = autonomous` **且** `agent.auto_claim = 1` **且** Agent 可用。写它只有
`PATCH /api/agents/{id}` 这一个入口，可逐 Agent 关闭。

- **存量回填（#195 / ADR-0029）**：升级走 `20 → 21`，按当前 `SCHEMA` 重建 `agent_config` 并把存量
  `auto_claim = 0` 的行一次性置 1（只改这一列、不动 `updated_at`，返回真实改动行数）。它**不回溯任务**：
  频道里既有的 `todo` / 未认领任务保持原状，本变更只影响升级之后新建的任务。代价是升级会覆盖此前
  显式关闭的存量行（库内无法区分「显式关闭」与「从未配置」），升级后仍可逐 Agent 关闭。
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
- **`SCHEMA_VERSION` 的历史**：认领能力最初（ADR-0016）零 DDL；`auto_claim` 列由 #65 引入（v12），
  默认值 flip 由 #195 / ADR-0029 引入（`20 → 21`，含存量回填）。此后任何形态变更仍按 ADR-0015 随票
  携带 `from → to` 步骤，链连续性由守卫测试把守。
- **已知代价**：认领会重置一次该任务的预算。触发者是人（人创建了任务），推进世代的动作却由编排
  执行；风险由「每张任务至多一次」封住（见上）。认领现在另多了「推状态 + 强制必答」两条效应，
  比 ADR-0010 当时描述的重；守这条边界的只有 `claimed_at` 那一把锁。

### 自动续跑（ADR-0027）

认领 / 指派只保证**第一轮**发生。任务需要多轮时，**受派者自己**用结构化动作 `continue_task`
（`ContinueTaskAction`）申请：「这一轮还没干完，再叫我一轮」。它**不改任务行**，只是轮次收口时的
一个执行请求——正文里的「我会继续」没有执行面，不算。

- **投递形状**：编排先落一条 `system.task.continue`（`Auto-continue: the assignee asked to keep
  working on this task` / `自动续跑：受派者申请继续推进这张任务`），再以它的 `seq` 唤醒**同一个人**一轮。
  唤醒的载体仍必须是一条消息（ADR-0017）。
- **续跑轮看整个任务线程**：`history=True` 的轮次把该 target 的全部消息交给接缝，**作者是本轮 Agent
  的消息按 `assistant` 角色回传**（普通轮次逐字不变：只喂游标之后的新消息、且不含自己）。
  `mentions` / `base_seq` 只看本轮新消息，历史点名的旧账不会改判本轮。
- **是确定信号**：`ReplySignal.CONTINUE`，可读文案 `reason.continue_*`；沉默 / 自判忽略 / 无正文无动作
  按 `failed` 收口。提示词写明「本轮由自动续跑唤醒」，不复用认领或人类消息的措辞。
- **不是跳**：载体是 `system` 记录、被唤醒的是同一个人，`target_budget.used` 一字不动。
- **每世代有界**：一次人类世代内受派者在这张任务上最多 `TASK_WORK_ROUNDS_PER_EPOCH` 轮
  （首轮 + `TASK_CONTINUE_ROUNDS` = 3 轮续跑）。计数读 `round_outcome`（`base_seq >= epoch_seq`、
  同一成员、非 `merged`），持久且跨重启一致；到上限后落一条 `system.task.continue_paused` 并停止，
  用户再发一条消息即开新世代、额度重来。
- **team 任务线程不提供**：`TaskActionContext.allow_continue` 在 team 下为假——工具不挂、提示词不出现
  （只唤醒队长的裁决不因此开口子）。
- **`SCHEMA_VERSION` 不 bump、无迁移步骤**：零 DDL（新计数器读既有 `round_outcome`），与 ADR-0016 六同一处置。

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
  `claimed_at IS NULL`、零认领轮次**——即便候选人的 `auto_claim=true`：认领裁决在读到 `auto_claim`
  之前就按 `mode = team` 早退，列与 autonomous 行为都不变。非队长的任务受派者**不会**被任务消息或
  改派唤醒。
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

`workspace` 是**显式配置的项目边界**：频道配项目根（`workspace_root`），Agent 配后端与权限档。没有
显式配置根时不挂项目根文件工具与 shell；**只有装配了项目根的 local 轮次**才把成员 `skills/` 作为
只读第二根，DM / 无项目根轮次没有这套文件工具（只有卸载目录的只读 `Read`）。

- **项目根是频道级**：所有成员共享同一个 `workspace_root`；文件工具对项目文件的相对路径从这里起算。根检查在
  **装配层**：Read / Write / Edit / Glob / Grep 每个项目路径先锚到根、再 `realpath`，`..`、根外绝对路径、
  指向根外的 symlink 一律返回 `ToolResultState.ERROR`。有项目根的 local 轮次里，本地 Read / Glob / Grep
  另可只读访问成员技能目录（DM / 无项目根轮次没有这套文件工具）；
  Write / Edit 仍只写项目根。AgentScope 自己的 `data/` / `skills/` / `sessions/` / `.mcp` 收进 `<root>/.alibot`。
- **workspace_root 不在数据目录内**：它指向用户的**项目目录**，不进入整目录备份的复制。**备份数据目录
  ≠ 备份项目文件**——项目文件要用户自己备份（见上面的备份口径）。
- **两个后端**：`local`（默认）或 `docker`（每 Agent/会话一个容器，`host_workdir=workspace_root`，
  容器内固定根 `/workspace`；另把该成员的**全局家目录**与**公共技能库**都以 `:ro` 挂到
  `/alibot-home/agents/<member_id>` 与 `/alibot-home/skills`，数据目录不进容器）。Docker 挂载保持只读：
  技能写入发生在宿主进程；成员家目录 `mcp.json` 的写入（含 Studio 的直接编辑原文）也都发生在宿主进程的人类面。
  Skill.markdown 已由宿主预读，容器只需读取 `references/`，
  ReMe 也由宿主进程写入。无项目根的轮次不装配 Docker。**本地 shell 默认关闭**（`allow_local_shell=0`）：
  本地 Bash 是主机进程、逐次审批、无沙箱；Docker 里的 Bash 在容器内，不需要那条本地通道。
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
- **`reply_id` 是回复级标识（#215）**：AgentScope 的 `reply_id` 是「当前回复的 ID」，**不是一次确认的
  ID**——同一条回复里第 N 个需要确认的工具调用会再发一次 `RequireUserConfirmEvent`，`reply_id` 与
  第一个相同。所以一条 `approval_request` 可以**多批确认**：记录已 `approved` / `denied` 时，`register`
  收到该记录**尚未包含**的调用就把它们合并进 `tool_calls`、把 `state` 复位 `pending`、清掉
  `resolved_at`、把 `requested_at` 移到这一批的起点（前端倒计时按它判定）、按现有时长重开等待窗口，
  并只发布**新调用**的 `approval_required`；既有 `decisions`
  是历史事实，原样保留，人类只需决策新调用。**重复登记同一批调用是幂等返回**（不重开、不发布），
  `expired` 也不因迟到登记复活。两处取舍要记住：重开时刷新 `requested_at` ⇒ **第一批的真实等待时长
  不再可读**（为了让倒计时与新一批同口径）；收口判据是**全部历史决策** ⇒ 第一批被拒绝时，第二批即使
  全批准，记录仍是 `denied`。
- **MCP 声明**：`mcp_server` 是声明正本，也是**唯一装配来源**，现行声明使用 `scope_kind='agent'`；
  `scope_kind='channel'` 的旧行保留但弃用、装配忽略且不回填。`mcp.json` 是导入源 + **Studio 写面**
  （人类面直接编辑原文）——它**不参与运行时装配**：文件里未导入的条目不会被连接，本轮只给一条只进
  `RoundOutcome.reason` 的可读提示（叫人去跑 `python -m alibot migrate-mcp`）。导入是**显式**动作，
  默认 dry-run、`--apply --yes` 双开关、先整目录备份（备份名带 `mcp` 标签）并落导入清单；同名不同形
  **整体拒绝**（无 `--force`），**不删源文件**。MCP 仍以 workspace 为宿主（AgentScope 的 `list_mcps`
  需要 workspace）；没有 workspace 时不装配 MCP。
  远端 MCP（`http` / `sse`）未做数据面确认时不连接；stdio 不需要确认。**导入不代确认**：远端声明
  导入后仍需人逐条 `POST /api/mcp-servers/{id}/consent`（或在 Studio 里点「确认使用」）。
- **Skill**：`skill` 表存 Agent 级技能元数据与内容 hash；字节由宿主应用写入
  `~/.alibot/agents/<member_id>/skills/<name>/`，不再依赖 workspace。技能装配只按「成员家目录 > 全局公共库」，
  DM 与未配置项目根的频道同样生效；不再有项目 Agent 级或项目频道级技能层。
  `scope_kind='channel'` 的旧行与项目层字节 `<workspace_root>/.alibot/skills/<member_id>/` 保留、装配忽略，
  不回填、不自动删除。安装接受三个来源：**本机目录**、**上传 zip**，以及从 `~/.agents/skills/` **导入**
  （ADR-0036：那是 codex / cline / pi 等工具共用的全局技能目录，也是外部 CLI 的落点；只读扫描出候选，
  选中后仍走同一条安装器复制进成员家目录）。三种来源都校验 `SKILL.md` front matter、路径与 hash，
  **不做远程市场/下载**。应用**不执行 `npx`**、不起 subprocess、不下载第三方包；本机技能落进
  `~/.agents/skills/` 这一步由人自己完成（在那之外用什么工具与本产品无关），Studio 的导入区只读那个
  落点：一个下拉（点一条 = 立即导入一次一个）+「刷新」（重扫候选）+「全部导入」（对当前可导入且未安装的
  候选逐个走同一条 `POST /api/skills`）。界面**不再展示命令**，也不再内建第二套安装器。
- **联网档（#184 / #199 / #202 / #242 / ADR-0026 §二修订 / §十四 / §十五 / 2026-10-22 修订节）**：`channel.web_access` 是频道默认值、
  `agent_config.web_access` 是 Agent 级覆盖（`NULL` = 跟随频道），取值 `NULL` / `'off'` / `'search'` /
  `'search+fetch'`。读路径三态合并 `agent_config.web_access or channel.web_access or 'search'`：
  **未配置默认开启搜索**，只有 `'off'` 才不挂任何联网工具。**私聊（DM）没有频道**：DM 的读路径
  回退到 Agent 级配置 + 默认档（`agent_config.web_access or 'search'`），`mode` 固定 `read-only`、
  `backend=local` / `docker_egress=False` / `egress_hosts=[]`，因此 DM 只挂 `web_search`、`fetch`
  永不装配；人类面入口是 Agent 详情页「配置」区块里的「联网」四档（**跟随频道（默认）** + 三档；选中跟随频道
  提交显式 `null` = 清除 Agent 级覆盖，读回 `null` 也选中它）。它**不是**
  `allow_local_shell` 的同义词——那个的风险面是本机任意进程，这个是数据出网。工具面是
  `app/alibot/web.py` 的 `web_search`（只读，只送查询词）与 `fetch`（非只读，但**不再逐次审批**，
  `permission_rule` 的 `pattern` 是 **host**）：`read-only`
  档只装 `web_search`；`ask-writes` / `trusted-writes` 装 `fetch`，**开启 `search+fetch` 即视为一次
  人类授权、调用直接执行**（2026-10-22 人定，#242 / ADR-0026 修订节）。人工收紧仍走规则：`deny` /
  `ask` 规则在工具自身的放行之前判定，因此 `permission_rule(fetch, pattern=<host>, behavior='deny')`
  仍拒绝、`behavior='ask'` 仍把该 host 拉回审批；`behavior='allow'` 成为冗余但无害。网络请求 **不进**
  `target_budget.used`（那是回应预算，ADR-0026 §六）。
- **搜索 provider（#199）**：带凭据的五家 `brave` / `tavily` / `perplexity` / `jina` / `exa`（Exa 有 key
  走 REST），以及免凭据回退 `exa-mcp`（Exa 公开 MCP，JSON-RPC + SSE）。解析顺序唯一：
  `ALIBOT_WEB_SEARCH_PROVIDER` → 凭据表 `is_default=1` → 凭据表 / 环境变量里已配置的固定顺序
  `brave → tavily → perplexity → jina → exa` → `exa-mcp`。凭据优先级：**库内值优先**，
  `BRAVE_API_KEY` / `TAVILY_API_KEY` / `PERPLEXITY_API_KEY` / `JINA_API_KEY` / `EXA_API_KEY` 只是兜底。
  带凭据 provider 需要 `('web', provider)` 确认行；`exa-mcp` 免确认。UI 路径：频道设置 →「联网」→
  档位 / provider 状态 / key 输入（password、掩码回显、保存 / 清除 / 设为默认）。
- **容器出网（#187 / ADR-0026 §五）**：Docker 后端下容器默认 `--network none`；`channel.docker_egress=1`
  才接 Docker 默认网络，且联网工具只对 `permission_rule(tool_name='fetch', behavior='allow', pattern=<host>)`
  的 host 发请求（开关关闭 ⇒ 工具返回说明容器网络策略的可读错误、零请求）。本地后端不受影响。
- **远端数据面确认**：`remote_consent_scope(scope_kind ∈ {model, mcp, docker, web}, scope_id)` 是统一的一次性
  确认；旧 `remote_consent` 行保留为兼容面（迁移复制成 `('model', provider, …)`）。四类都**只存确认时间**。
  `web` 档的人类面写路径有两条：`POST /api/web-providers/{provider}/consent`（幂等、只存时间）与
  `PUT /api/web-providers/{provider}/credential`（保存 key 的动作**同时**写确认行）。未确认时装配层
  **不挂**带凭据 provider 的工具且零请求；`exa-mcp` 不需要确认行。已确认的 provider 由
  `GET /api/remote-data-flow` 的 `web_consents` 读回。
- **凭据入库与脱敏（#199）**：`web_provider_credential.api_key` 是明文（部署层须把库文件权限设为 `0600`）；
  任何 HTTP 响应、日志与事件载荷只出现 `mask_credential()` 的掩码形态，错误文案只提 provider 与
  「未配置凭据」，绝不回吐取值。

### Skill / MCP 归属修订与迁移策略（#270 / #274 / #275）

- 新 Skill 字节写入 `~/.alibot/agents/<member_id>/skills/<name>/`，装配只看成员家目录与全局公共库。
- 既有 `scope_kind='channel'` 的 `skill` / `mcp_server` 行以及项目层旧 Skill 字节保留；装配忽略，不自动
  回填或删除。显式迁移由独立 `migrate-skills` 命令执行；本轮不执行、无 schema bump。
- MCP 同理：`mcp.json` 的存量条目由 `migrate-mcp` 一次性导入成库行（默认 dry-run，`--apply --yes` 才写，
  先备份 + 落清单，同名不同形整体拒绝，不删源文件，**不代做远端确认**）。导入后装配只读库行。

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
快照，然后进入实时推送。per-target 流的**前端带最新 `last_seq` 重连**，因此重连既不会漏也不会重复
（全局流不补拉，它靠全量重取，见下）。

**只有 `message` 与 `round_outcome` 的 SSE 帧带 `id: <seq>`**；其余事件是 **live-only**，不发 `id` 行
（ADR-0020）。否则浏览器会把 `Last-Event-ID` 推进到库里不存在的游标，重连补拉就会与消息游标混淆。
新事件断线不补发，运行面板以 `GET /team-runs` / `GET /plan` / `GET /sop-runs/{id}` 的快照兜底；
审批卡以 `GET /api/approvals?target_id=&state=pending` 兜底；`after_seq` 补拉仍只覆盖消息与轮次结果。

线程关着时，Studio 另用 `GET /api/approvals?state=pending`（不带 `target_id`）读一次全工作区 pending，
并在页面可见且在线时按 12s 有界刷新，驱动任务看板卡片 / 列表行角标与任务页签圆点（#209）；
live `approval_*` 帧仍只补本次会话内的变化。

**Studio 另有一条整页共用的全局流 `GET /api/events`**：任意 target 的事件都经它透传，帧里的
`target_id` 是归属正本。它是**纯 live** 的——不接受 `after_seq`、不读库补拉、也不写 `id:` 行
（重连补齐由前端 `loadWorkspace({silent, keepActive})` 的全量重取承担）。理由：浏览器对单一
origin 的 HTTP/1.1 并发连接只有 6 条，按 target 各开一条常驻 `EventSource` 会把连接池占满，
之后的 `fetch` 与刷新都排队。三个 per-target 端点全部保留、语义不变；任务 dock 的
`/api/tasks/{id}/events` 仍是独立一条（全局流 + 任务流 = 2 条；任务 target 的帧两条都在收，运行面板
按 id 合并渲染，不会出现两行）。

单条连接在 `ALIBOT_SSE_ROTATE_SECONDS`（默认 55 秒）后由服务端主动收尾并先发一个
`event: rotate` 帧：前端据此**区分「例行轮换」与「后端不可达」**，不会因为轮换而误报掉线，
随后立刻重连（per-target 流带最新 `last_seq`；全局流重开后由 `loadWorkspace` 全量重取补齐）。
这个轮换同时保证优雅关停不会卡在长连接上。

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
| `PATCH /api/channels/{id}/config` | 写频道配置 `{mode?, team_leader_member_id?, workspace_root?, workspace_backend?, permission_mode?, web_access?, docker_egress?}`；任一字段不合法**整体拒绝**；`workspace_root` 与 `web_access` 的显式 `null` = 清空项目根 / 回到未配置（联网读路径回退默认档 `search`；显式关闭写 `"off"`）；team 要求队长是当前频道成员且未删除；**不**自动改 `auto_claim` |
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
| `GET /api/events` | 全局事件流（SSE，纯 live：不补拉、不发 `id:` 行；Studio 整页只用这一条） |
| `POST /api/channels/{id}/read` | 标读 `{seq}`（同频道内取 max，裁剪到当前最大 seq） |
| `GET /api/agents` | Agent 成员 + 模型配置（含 **`auto_claim`**）+ 四态状态点 + 「为什么未启用」+ **概览读数**（`stats`） |
| `POST /api/agents` | 新建 Agent `{name, role, model_id, system_prompt, workspace_backend?, permission_mode?, allow_local_shell?, docker_image?}`；校验失败**整体不创建**（workspace 四项缺省 = 跟随频道 / 内置镜像；`auto_claim` 缺省开启，见 ADR-0029） |
| `GET /api/agents/{id}` | 单个 Agent（含可读的 `enabled_reason`、`auto_claim`、Agent 级 workspace 四项与 `web_access`、`stats`） |
| `PATCH /api/agents/{id}` | 编辑 Agent 配置 `{display_name?, role?, model_id?, system_prompt?, enabled?, auto_claim?, workspace_backend?, permission_mode?, allow_local_shell?, docker_image?, web_access?}`；任一字段不合法则**整体不写**。「暂停」就是 `enabled: false`，「自动认领任务」就是 `auto_claim`；`docker_image: ""` 是**显式清空**（回到内置镜像）；`web_access: null` 是**清除 Agent 级覆盖**（回到跟随频道，写库 `NULL`；缺省该字段 = 保持原值，其它字段的显式 `null` 仍 400）。响应同形 `agent` + `agents` + `members` |
| `DELETE /api/agents/{id}` | **软删除**一个 Agent：写 `member.deleted_at` 并删掉它在 `target_member` 的全部行（移出全部频道与任务线程）。消息 / 任务 / DM target / `agent_config` / `round_outcome` 保留，不重写任务指派；不落消息、不唤醒、不消耗预算。只接受未删除的 `kind='agent'`；owner、未知 id、二次删除一律可读 404 `not_found`。响应 `{deleted, agents, members}` |
| `GET /api/agents/{id}/status` | 单个成员的状态点 |
| `GET /api/agents/{id}/global-mcp-servers` | 该成员家目录 `mcp.json` 的只读条目视图：条目 + 运行期读数。**该文件不参与运行时装配**（运行期只看 `mcp_server` Agent 级库行）；改原文走上表中的两条 `global-mcp-file` 端点，生效需要先 `migrate-mcp` 导入 |
| `GET /api/agents/{id}/global-mcp-file` | 该成员家目录 `mcp.json` 的**原文** + 条目计数（`{member_id, exists, text, servers}`）。只读，不创建也不写入；文件不存在 ⇒ `exists=false` + 空串（不是「读到空文件」） |
| `PUT /api/agents/{id}/global-mcp-file` | 把整份**原文**写盘 `{text}`：三道校验（JSON 可解析 → 顶层是对象且 `servers` 是数组 → 每条声明经既有归一化内核）**全过才写**，任一条不合法 ⇒ 400 + `fields` + **零写入**。原文**逐字**写（不重排 / 不格式化 / 不动末尾换行）；文件不存在但家目录在 ⇒ 新建，家目录不存在 ⇒ 400 且不建目录 |
| `POST /api/agents/{id}/global-mcp-servers/{name}/consent` | 确认家目录 `mcp.json` 里的**一条远端声明**（写 `remote_consent_scope('mcp', 'global:<member>:<name>')`，幂等、只存时间）。**它不改变装配**：`mcp.json` 不参与运行时装配，要生效得先 `migrate-mcp` 导入成 `mcp_server` 库行，导入后再走 `POST /api/mcp-servers/{id}/consent` |
| `GET /api/member-statuses` | 全部成员的状态点 |
| `GET /api/remote-data-flow` | 远端数据流告知文案与已确认记录（含 `consents` 与联网 provider 的 `web_consents`） |
| `POST /api/remote-data-flow/ack` | 确认一次告知 `{provider}`（幂等） |
| `GET /api/approvals?target_id=&state=pending` | HITL 审批快照（`reply_id` / `target_id` / `member_id` / `tool_calls` / `state` / `decisions` / 时间戳）。只读；`state` 只接受 `pending` / `approved` / `denied` / `expired` |
| `POST /api/approvals/{reply_id}` | 提交审批 `{decisions:[{tool_call_id, confirmed, rules?}]}`；只接受 `pending`，同批重试幂等，相反结论 409；决策内的 `rules` 落成该 Agent 的权限规则。批准后该轮继续，拒绝的工具结果是 `DENIED`（未执行），两者都不改动既有消息正本 |
| `GET /api/permission-rules?scope_kind=&scope_id=` | 权限规则列表（`allow` / `deny` / `ask`；不暴露 `BYPASS` / `DONT_ASK`） |
| `POST /api/permission-rules` | 写一条规则 `{scope_kind, scope_id, tool_name, pattern, behavior}`；同 `(scope, tool, pattern)` 覆盖；配置动作不落消息、不唤醒 |
| `DELETE /api/permission-rules/{id}` | 删除一条规则（配置动作；不删除消息与审批历史） |
| `GET /api/mcp-servers?scope_kind=&scope_id=` | MCP 声明库行列表；只回 `env_names`（**变量名**，永不回值）+ 进程内 `runtime` 读数。现行装配来源为 Agent 级库行 |
| `POST /api/mcp-servers` | 新建声明 `{scope_kind, scope_id, name, transport, command?, args?, url?, env_names?, enabled?}`；新声明使用 `scope_kind='agent'`；正文出现 `env` 之类键整体拒绝（400）；同名同 scope 409 |
| `PATCH /api/mcp-servers/{id}` | 部分更新同名字段；`scope_kind` / `scope_id` 不可改；整条结果重新校验 |
| `DELETE /api/mcp-servers/{id}` | 删除声明（配置动作；不删消息、不唤醒） |
| `POST /api/mcp-servers/{id}/consent` | Agent 级库内远端 MCP（`http` / `sse`）的数据面确认（幂等；stdio 不接受）。未确认时装配层**不连接** |
| `POST /api/docker-images/consent` | 远端 Docker 镜像的数据面确认 `{image}`（幂等；写 `remote_consent_scope('docker', image)`，只存确认时间）。未确认的镜像在装配层**不拉取**、不启动容器 |
| `POST /api/docker-images/consent` | 远端 Docker 镜像的数据面确认 `{image}`（幂等；写 `remote_consent_scope('docker', image)`，只存确认时间）。未确认的镜像在装配层**不拉取**、不启动容器 |
| `POST /api/web-providers/{provider}/consent` | 联网 provider 的数据面确认（幂等；写 `remote_consent_scope('web', provider)`，只存确认时间）。接受五家带凭据 provider；未确认时装配层**不挂**该 provider 的工具且**零请求**；`exa-mcp` 免确认 |
| `GET /api/web-providers` | 五家 provider 的掩码状态（`configured` / `masked` / `is_default` / `env_configured`）+ 当前生效 provider 与来源（`env` / `ui` / `fallback`）；**不含完整 key** |
| `GET /api/web-providers/{provider}/credential` | 一家的掩码凭据读数（未配置时 `configured=false`） |
| `PUT /api/web-providers/{provider}/credential` | 保存 / 换 key `{api_key, is_default?}`（非空、长度上限；保存同时写确认行），或不带 key 只切默认 `{is_default: true}`；响应只回掩码 |
| `DELETE /api/web-providers/{provider}/credential` | 清空凭据与默认标记（确认行保留）；响应只回掩码 |
| `GET /api/skills?scope_kind=&scope_id=` | Skill 列表（`name` / `description` / `content_hash` / `enabled` / scope；不含字节）。旧 channel 行仍在库中保留（迁移前不删），但 `GET` 带 `scope_kind=channel` 返回空列表（弃用，不提供读取面）；不带过滤的旧行不在本 PR 删除 |
| `GET /api/skills/candidates?scope_kind=agent&scope_id=` | **只读**候选扫描：外部 CLI 的落点 `~/.agents/skills/` 下每个直接子目录一条 `{name, description, path, importable, reason, installed}`，响应 `{source, available, candidates}`。来源根缺失 ⇒ `available=false` 且空列表（**不创建目录、不报错**）；坏条目逐条降级（`importable=false` + 可读原因），不整体失败；`installed` = 该成员库里已有同名行。`scope_kind` 只接受 `agent`（否则 400），不落消息、不产生轮次、不改预算、不写文件（ADR-0036） |
| `POST /api/skills` | 安装：Agent 级 JSON 正文 `{scope_kind, scope_id, source_path}`（`scope_kind='agent'`，`scope_id=<member_id>`）从**本机目录**（`source_path` 也可指向 `~/.agents/skills/<name>`，即从外部 CLI 的落点导入）；或 zip 原始字节 + query `scope_kind/scope_id/filename`。历史 `channel_id?` 参数与 `scope_kind='channel'` 已弃用，不构成装配来源；旧 channel 行保留并忽略。校验 `SKILL.md`、路径与 hash，非法整体拒绝；同名同 scope 409；不做远程市场/下载 |
| `PATCH /api/skills/{id}` | 启停 `{enabled}`；scope 与 hash 是身份，不可改 |
| `GET /api/skills/{id}/markdown` | 一个技能 `SKILL.md` 的**正文**（只读；front matter 之后的字节），四态 `ok` / `missing` / `invalid` / `unreadable` |
| `POST /api/skills/{id}/copy` | 把该技能**字节**复制到另一个成员：正文 `{scope_id: <target_member>}`；源字节固定取自源成员家目录 `~/.alibot/agents/<source>/skills/<name>/`（前端不传路径），复制走既有安装器重校验并建目标行；目标已有同名行或同名目录 409、**不覆盖**；源行与源字节只读不改；`scope_kind='channel'` 的旧行 400 |
| `DELETE /api/skills/{id}` | 显式删除该 Skill 配置与其成员家目录副本（不删用户源目录；不自动清理历史 channel 行或项目层旧字节） |
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

### Agent 详情、配置编辑与软删除（`PATCH` / `DELETE /api/agents/{id}`）

配置写路径的取舍见 ADR-0011；删除这一组取舍（完整软删除、不级联、不可恢复、清 `target_member`、
二次删除 404）记录在本票验收文档 `app/specs/agent-danger-zone-delete-acceptance.md`。

Agent 详情是**主线视图**（`state.mode === 'agent'`，入口：左栏 Agent 行、成员面板里的名字、
失败轮次块的「看成员状态」）：它占据整条右侧，右侧 dock 在那种视图下让位。头部只有名称与强调色
的「发送消息」按钮——没有 Agent 小标签、没有状态行、也没有关闭控件；离开详情走左栏的频道 /
私信行，或键盘 `M` / `T`（消息 / 任务，`leaveAgentView()`）。「消息 / 任务」页签随频道头在
详情视图一起让位，因此它们不是详情里的出口；dock 的进入前开合只在删除成功的
`closeAgentDetail()` 路径恢复。
正文是**三个页签**，顺序固定：**基础信息和配置**（统计 → 系统提示 → 配置 → 危险操作）→
**skills**（左栏技能列表 / 右栏选中技能详情）→ **mcp**（上块库行表单 / 下块家目录 `mcp.json` 的
文本域与条目视图）。打开详情与切换成员都回到默认页签（基础信息和配置）——新成员不继承上一个成员的
页签位置；三个页签用 `data-adtab="base|skills|mcp"` 定位，`state.tab` 是纯呈现状态，
**不落库、不写 URL**。页签只切呈现、不丢草稿（保存条是全页级、跨页签可见）；日志页签仍不做
（用户人定 D24，入口另立 backlog 票 #72）。系统提示紧贴统计之下（用户 2026-10-24 反馈：它是这页上
最常改的一格，不该排在页面最底）。
配置字段直接长在「配置」区块里（**`enabled` 除外**：它的唯一界面入口是「危险操作」区块的暂停 / 恢复
按钮，配置区没有「启用状态」档位），控件只写草稿，草稿与后端读数不一致时页面底部
出现固定保存条（保存 / 放弃）；离开详情、切换成员或切换主线视图时草稿作废，不弹二次确认。

- **「统计」区块的四个数字全部来自既有事实**，由后端在 `stats` 里聚合，前端不自己算：
  `channel_count` = 它是成员的 channel 型 target 数；`round_count` = 它的 `round_outcome` 行数；
  `completed_task_count` = 指派给它且 `status='done'` 的任务行数；`success_rate` =
  `replied ÷ (replied + ignored + failed)`（分母**不含 `silent`**：`silent` 与 `merged` /
  `budget-exhausted` 一样不计入分母——`db.py` 的 `AGENT_STATS_ROUND_SQL` 只把
  `replied` / `ignored` / `failed` 数进分母；没有样本时为 `null`，界面显示「—」）。原型里的 Token 消耗 / 上下文占用 / 内存 / 延迟在本仓**没有数据面**，
  因此不呈现。
- **详情页不再有只读的「运行时」与「当前状态」两节**（用户 2026-10-24 反馈：模型标识 / 提供商 /
  是否远端 / 远端数据流告知复读一遍没有新信息；未启用原因 `enabled_reason` 随之不再在详情页出现，
  成员名册里那处小字保留）：模型标识与提供商只长在「配置」区块里（它们可编辑），切到远端 provider
  时的数据流告知块 `agent-edit-remote-notice` 也在那一节里，同一份读数不在这页上留第二处呈现。
  Agent 级 workspace 覆盖（`workspace_backend` / `permission_mode` / `allow_local_shell` /
  `docker_image`）目前**只经 API 读写**（详情/编辑 UI 尚未暴露这四个控件）；`GET` / 列表响应逐字
  带回落库值，`null` 表示「跟随频道默认」。
- **写路径唯一**：`PATCH /api/agents/{id}` 可写 `display_name` / `role` / `model_id` /
  `system_prompt` / `enabled` / `auto_claim` / `workspace_backend` / `permission_mode` /
  `allow_local_shell` / `docker_image` / `web_access`，**任一字段不合法则整体不写**（不得部分写入）；
  `model_id`
  沿用与创建同一套校验与**远端数据流告知**语义。「自动认领任务」开关就在详情页的「配置」区块里
  （不另造第二个编辑面，独立的「编辑配置」弹窗已删除），**缺省开启**（#195 / ADR-0029）、
  只在自主频道生效，写它只影响**之后新建**的任务；升级时的存量回填见上文「存量回填」。
- **配置变更不是发言**：不落消息、不唤醒、不消耗预算、不开世代（ADR-0011 与 ADR-0007 的边界：
  人类面可写配置类取值）。`auto_claim` 同此——它是编排在**新建任务之后**读的配置，不是动作。
- **「暂停」= 写 `enabled = 0`**，复用既有可用性判定：状态点为**未启用**、`enabled_reason` 可读；
  已知代价是它**仍会被唤醒**并落一条可读原因的 `failed` 轮次结果，只是不产出回复——「从唤醒面排除」
  是唤醒面的另一个决策，本轮不做。按钮住在页面最后的「危险操作」区块里，语义不变
  （点击仍只写草稿里的 `enabled`）；那句「暂停只写配置…」的独立提示已从界面删除，代价只由
  上一条与危险说明说明一次，不重复占一行。
- **「全局 MCP」条目视图**（只读，mcp 页签的**下块**）：它与下块的 `mcp.json` 文本域是**同一份
  数据的两种视图**（文本域改原文、条目视图看条目与运行期读数），因此并进下块、不再单独成段；
  标题下直接列真实条目，不再有一行「来自该成员家目录的 mcp.json…」的重复说明；空态逐字为
  `“全局mcp.json”里没有声明MCP服务`（英文 `No MCP servers are declared in the global mcp.json.`）。
- **「危险操作」区块**（用户 2026-10-24 反馈：暂停与删除是危险动作，不该和常规配置混在一起）：
  区块标题 + 一句点明暂停与删除后果的危险说明，随后是暂停 / 恢复与删除按钮。
  危险说明与确认文案写的是实际行为：删除把这个 Agent 从 **Agent 列表与全部频道**中移除，
  **私信、历史消息与任务保留**。
  点「删除」**只打开确认弹窗**（沿用既有 `.overlay` + `.modal` 骨架；文案带 Agent 名称与不可逆后果），
  不发请求；「取消」/ 关闭图标 / 点遮罩 / `Escape` 都只收起弹窗，焦点落在「取消」上；
  弹窗里的「确认删除」才发 `DELETE /api/agents/{id}`。请求进行中禁用按钮，离线（`state.online === false`）
  时暂停与删除都禁用。删除成功后按响应刷新 `agents` / `members`、关闭详情并回到进入前的视图，给一条
  可读 toast；失败时弹窗保持打开、详情不关、本地镜像不变，原因按后端那一句显示。任务指派者已不在成员表里时，
  `taskMemberName()` 以 `workspaceLoaded`（工作台**成功**载入过一轮）为判据：已成功载入且查不到 ⇒
  「已删除的成员」，尚未成功载入 ⇒ 中性「成员」；两种情况都不把裸 `mem_...` id 露给用户。

### 未读口径

未读 = 已读位置之后、`kind ∈ {human, agent}`、**且作者不是本人**的顶层消息条数；`system` 事件
消息不计入。**作者排除本人**是实施票 ③ 按 #4 票面字面改判的（改前 #2 把 owner 自己的消息也
计入自己的未读）。`unread_count`（频道列表 / 单条）与 `unread_total`（workspace 快照）由
`db.py` 里**同一条 SQL** 得出，因此只有一个口径落点。

## 测试与检查

```sh
./scripts/check.sh            # 全量门禁：npm run build（产物不入库）+ pytest + ruff + mypy + pyright + vitest
./scripts/check.sh --fast     # 开发循环：除全量 pytest 外的一切（秒级）
./scripts/check.sh --fast tests/test_send_message.py   # 再补跑指定 pytest（路径相对 app/）
./scripts/check.sh --studio   # Studio 窄档：--fast + 审批 HTTP 契约 + 前端静态守卫（diff 只在 studio/**）
```

**产物在门禁之前构建**：`app/alibot/studio/v2/` 不再入库（ADR-0039），`scripts/check.sh` 头部先跑
`npm run build`，CI 在 `npm ci` 与门禁之间另有一个显式构建步，`pip wheel` 由 `app/hatch_build.py`
的 hatch hook 兜住。因此**改前端源码不需要手工记得构建**就能跑门禁；但 `python -m alibot` 与
`pip install -e app` 之后起服务**要求先有产物**——缺产物时 `create_app` 会在启动时抛一条带修复命令的错。

`--studio` 不替代 CI 的全量 pytest；动到 `.py` / schema / 构建配置时仍用全量。红证据（隔离基线
上的先失败后通过）用 `scripts/red-evidence.sh <base-rev> <test-file>`，它不会碰当前工作区。

`pytest` 是 HTTP + SSE 黑盒契约测试（每条用例独立临时数据目录）；ruff / mypy / pyright 的配置分别
在 `app/pyproject.toml` / 仓库根 `mypy.ini` / 仓库根 `pyrightconfig.json`。**不要在命令行补 `-q`**
（配置里已有 `addopts = "-q"`，补了会变 `-qq`，结果行会被吞掉）；**不要跑 `ruff format`**，只跑
`--check`。工作台（`/studio/v2/`，ADR-0038）的组件、store、事件流、构建守卫与词条表数据契约为
**一套** Vitest 用例族（`app/frontend/tests/*.test.js`，**按 glob** 收集——新增文件自动纳入；每个文件
覆盖什么写在各自的文件头注释里）。前端依赖在 `app/frontend/`，`scripts/check.sh` 缺 `node_modules`
时自己 `npm ci --include=dev`。旧页面的 `node --test app/tests/*.test.cjs` 族已随页面退役
（ADR-0039）；**不要再往 `app/tests/` 加 `*.test.cjs`**。
CI（`.github/workflows/check.yml`）跑的是同一个
`scripts/check.sh`。**本地全量只跑一次**：开发循环用 `--fast` 或指定用例；代码定稿时跑一次全量
并留存读数，PR 的 CI 再跑同一脚本复核；代码未再变化的 docs-only commit 不重跑本地全量。

Python 测试只断言**外部可观察行为**（消息出现与顺序、轮次结果、状态点、事件流帧、seq 单调性、
已读位置、未读计数、不可变性、重启后一致），不检查内部函数或前端 DOM；工作台的 DOM 行为（搜索面、
Agent 详情面、旁白降档、频道设置弹窗、结构化沉默的摘要行与两种语言说法）由上面的 Vitest 用例族
单独覆盖。每条 pytest 用例的库都在 `tmp_path` 下，**不触碰** `~/.alibot/data` 与旧 `~/Alibot`。

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

此外还有**一类**不以 HTTP 面读数的守卫：它们断言的是**装配层的接线**或**配置的默认值**——这类事实
没有任何端点能观察到（要读它只能「真的等 55 秒」或「真的在另一个进程里删容器」）。目前是两处，
均须在文件头写明「为什么非按模块级断言不可」：

1. `app/tests/test_sse_lifetime_config.py`——事件流寿命的**生产默认值**与「调用时读取」的时机。
2. `app/tests/test_docker_startup_injection.py`——`create_app` 的启动清扫**接线**（测试装配零调用 /
   生产装配必调，顺序 `reaper→sweep`），以及「每个进 lifespan 的装配点都显式传替身」的 AST 扫描。
   为什么非按模块级断言不可：本机没有 docker daemon，9 条 docker 用例（8 + 1）全 skip，所以「测试装配
   漏传替身」在本机**看不见**，只在有 daemon 的 CI 上表现成别的 worker 的容器被删（见
   `app/specs/issue-328-acceptance.md` §7.6）。

这两类的共同边界：断言的是**接线与默认值**，不是实现函数的行为；行为断言仍全部走 HTTP 面。

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

**工作台只有一条入口、一套正本（ADR-0038 / ADR-0039）**：入口是 `/studio/v2/`（Vue 版，Vite 构建），
`/` 与 `/studio` / `/studio/` 都 redirect 到它；旧页面 `/studio/` 已退役，`/studio/app.js` 回 404。
v2 页面加载**同一批** `app.css` / `product-tokens.css` / 设计系统 token，并共用同一份 `i18n.js`
（词条表）与 `markdown.js`（正文渲染器）——因此上面这些 token 名、尺寸与色位只有一处定义。
未提供面**不做假按钮**：纯导航类的（频道级审批板）**整块不渲染**；
空工作台的出路（新建频道 / 新建 Agent）**可见但禁用**，理由写在 `title` 里（词条 `app.notMigrated`）。
频道设置里的**联网 provider 凭据段**（#320）已补：位置在联网档位卡之后，五行各报掩码 / 是否默认 /
数据面确认态，写路径是保存 key（`PUT` 带 `api_key`）/ 设为默认（`PUT` **不带** `api_key`）/ 清空
（`DELETE`，行内二次确认）/ 数据面确认（`POST .../consent`）。
产物新鲜度是机械判据：`app/frontend/build-info.js` 写源码指纹，`app/frontend/tests/build-guard.test.js`
复算比对——改了 `app/frontend/src` 没重新 `npm run build`，门禁就红。产物**不入库**（ADR-0039），
由 `scripts/check.sh` 头部 / CI 构建步 / `app/hatch_build.py` 的 hatch hook 各自在构建时生成。

**Agent 详情 skills 页签的固定高度工作区**：长技能列表与长 `SKILL.md` 会把页面撑高，右栏详情
随之跑出视口，因此桌面档（`≥901px`）下 skills 页签改成**固定高度**：顶部导入区留在原处，
双栏占满余下高度、左右**各自**内部滚动，页面本身不再被列表撑高；右栏只有**一处**滚动条
（正文不再自带 `60vh` 滚动盒，避免 Markdown 嵌套滚动）。状态是 `.agent-page` 上的
`is-skills-fixed`，由 `setAgentDetailTab()` 落/撤；整块样式写在 `app.css` 的 `min-width: 901px` 档，
因此基础信息和配置 / mcp 两个页签仍走原来的整页滚动，`≤900px` 的单栏断点与页面滚动也不变
（那里两栏上下堆叠、高度由内容决定，固定盒子反而会把右栏挤出视口）。极矮视口下双栏有
`min-height` 兜底，再往下由外层整页滚——宁可整页滚，不把两栏压成一条缝。
