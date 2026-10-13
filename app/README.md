# worksplice 应用后端 + 浏览器工作台（实施票 ②）

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
.venv/bin/python -m worksplice
#   或显式指定端口与数据目录
.venv/bin/python -m worksplice --port 8788 --data-dir /tmp/worksplice-smoke
```

浏览器打开 <http://127.0.0.1:8788/studio/>（根路径 `/` 会 302 到这个地址）。

首版无认证，因此**默认只绑定 loopback**，不提供局域网/远程访问。
确实需要时设 `WORKSPLICE_ALLOW_REMOTE=1` 显式越界，并自行承担风险。

### 模型与凭证：只从**进程环境变量**读，库里不存密钥

Agent 的「模型标识」写作 `provider:model`（例如 `dashscope:qwen-max`、`openai:gpt-4.1`、
`ollama:qwen2.5`）。`provider` 取自 AgentScope 2.0.9 的 credential 类型白名单，它决定两件事：
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
OLLAMA_HOST=http://localhost:11434 .venv/bin/python -m worksplice
```

- **不新增密钥录入界面、不新增密钥落库表**：应用库里没有任何密钥字段（只有「何时确认过数据面」）。
- 凭证解析收敛在 `worksplice/agent_seam.py` 的 `read_api_key()` 一个窄入口，以后换来源（钥匙串等）
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
| 默认数据目录 | `~/Worksplice/`（可见、可直接复制） |
| 覆盖方式 | `--data-dir <路径>` 或环境变量 `WORKSPLICE_DATA_DIR` |
| 库文件 | `<数据目录>/worksplice.db`（单文件 SQLite，WAL 模式） |
| 事件流单连接寿命 | `WORKSPLICE_SSE_ROTATE_SECONDS`（默认 55 秒，到期服务端主动收尾、浏览器带 `last_seq` 重连） |

备份：`cp -R ~/Worksplice ~/Worksplice-backup-$(date +%F)`。恢复：反向复制回去。
库文件旁边可能有 `-wal` / `-shm` 两个伴随文件，一起复制即可。

## 数据模型（决策级）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `member` | id, kind(`owner`/`agent`), 显示名, 头像色位 | 身份正本；空库只有一条 `owner` 行 |
| `agent_config` | member_id, 角色, 模型标识, 系统提示, 启用开关 | 模型与系统提示是**配置数据**，换模型不改代码 |
| `remote_consent` | provider, acknowledged_at | 远端数据流告知的一次性确认；**不存任何凭证** |
| `channel` | id, 名称, 描述 | |
| `channel_member` | channel_id, member_id | 每频道显式成员表 |
| `channel_read` | member_id, channel_id, last_read_seq | 已读位置（未读的正本）。owner 的行使未读徽标；agent 的行是它自己的消费游标 |
| `message` | id, target_id, seq, author_member_id, kind, 正文, **提及快照** | **append-only**，`UNIQUE(target_id, seq)` |
| `round_outcome` | member_id, target_id, base_seq, status, reason, 起止时间, 是否消耗预算, 是否被合并 | 轮次结果，**append-only** |

- **append-only 是硬约束**：库级触发器拒绝 `message` 与 `round_outcome` 的任何 `UPDATE` / `DELETE`；
  HTTP 面也没有编辑或删除端点。
- **频道内单调 seq**：由后端在同一事务内分配，同时充当渲染顺序、重连补拉游标。
- **提及快照随消息落库**：唤醒与前端高亮都消费同一份快照，不事后按当前成员名表重新解析。
- **未读计数** = `last_read_seq` 之后、`kind ∈ {human, agent}` 的顶层消息条数（不计 `system`）。

## Agent 的采用范围（AgentScope）

pin 的版本是 **`agentscope==2.0.9`**（2026-10-13 核对：`docs.agentscope.io/stable` → 重定向到
`/en/versions/2.0.9/`，PyPI 当时最新为 `2.0.10`）。

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
- **唤醒面** = 该频道的全部 agent 成员，**排除作者自己**。穿透（未加入却被点名）留 #③。
- **回应判断**：被个人提及是确定信号 → **必须回应**（自判忽略按 `failed` 处理，不静默）；
  明确无关 → `ignored`（正常协议选择，不是失败）。
- **每轮落一条 `round_outcome`**：`replied` / `ignored` / `failed` / `merged` / `budget-exhausted`，
  带可读 reason、起止时间、是否消耗预算、是否被合并。
- **一轮至多一条频道消息**；落库路径只有一条。
- 消费游标只在 `replied` / `ignored` 推进；`failed` 不推进（下次唤醒会重新读到，但不是自动重放）。
- **预算策略与穿透在 #③**：本票不实现也不验收；`consumed_budget` 只如实记录事实。

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

单条连接在 `WORKSPLICE_SSE_ROTATE_SECONDS`（默认 55 秒）后由服务端主动收尾并先发一个
`event: rotate` 帧：前端据此**区分「例行轮换」与「后端不可达」**，不会因为轮换而误报掉线，
随后立刻带最新 `last_seq` 重连。这个轮换同时保证优雅关停不会卡在长连接上。

状态点四态（`--online` 空闲 / `--working` 正在回复 / `--error` 出错 / `--offline` 未启用）
**只表达后端事实**，不从任何会话存活推导；`working` 脉冲在 `prefers-reduced-motion: reduce` 下关闭。

## HTTP 契约

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/workspace` | 工作台快照（成员、Agent、频道、未读合计、可用 provider） |
| `GET /api/channels` | 频道列表（含 `last_seq` / `last_read_seq` / `unread_count`） |
| `POST /api/channels` | 新建频道 `{name, description, member_ids}`；校验失败**整体不创建** |
| `GET /api/channels/{id}/messages` | 按 seq 升序取消息（`?after_seq=n` 取增量） |
| `POST /api/channels/{id}/messages` | 发送人类消息 `{body}`（同事务分配 seq，随后唤醒成员面） |
| `GET /api/channels/{id}/members` | 频道成员 |
| `GET /api/channels/{id}/round-outcomes` | 轮次结果（只读；首版**没有**可观测页面） |
| `GET /api/channels/{id}/events` | 事件流（SSE，`?after_seq=n` 补拉） |
| `POST /api/channels/{id}/read` | 标读 `{seq}`（同频道内取 max，裁剪到当前最大 seq） |
| `GET /api/agents` | Agent 成员 + 模型配置 + 四态状态点 + 「为什么未启用」 |
| `POST /api/agents` | 新建 Agent `{name, role, model_id, system_prompt}`；校验失败**整体不创建** |
| `GET /api/agents/{id}` | 单个 Agent（含可读的 `enabled_reason`） |
| `GET /api/agents/{id}/status` | 单个成员的状态点 |
| `GET /api/member-statuses` | 全部成员的状态点 |
| `GET /api/remote-data-flow` | 远端数据流告知文案与已确认记录 |
| `POST /api/remote-data-flow/ack` | 确认一次告知 `{provider}`（幂等） |
| `GET /api/health` | 存活与数据目录 |

错误统一为 `{"error": {"code", "message", "fields"?}}`。

## 测试与检查

```sh
.venv/bin/python -m pytest          # HTTP + SSE 黑盒契约测试（每条用例独立临时数据目录）
.venv/bin/ruff check app && .venv/bin/ruff format --check app
.venv/bin/python -m mypy            # 配置在仓库根 mypy.ini
node ~/.pi-lens/tools/node_modules/pyright/index.js app/worksplice app/tests   # 配置在仓库根 pyrightconfig.json
```

测试只断言**外部可观察行为**（消息出现与顺序、轮次结果、状态点、事件流帧、seq 单调性、
已读位置、未读计数、不可变性、重启后一致），不断言内部函数或前端 DOM。每条用例的库都在
`tmp_path` 下，**不触碰** `~/Worksplice`。

四类 Agent 行为由 **脚本化替身**（`tests/scripted.py`）固定：回应 / 忽略 / 失败 / 延迟。
测试**不联网、零成本**；「什么时候跑完一轮」由测试自己拨（手动调度器），所以 in-flight 与
合并这类窗口可以稳定观察。真模型连通性另有一条**默认跳过、本地手动运行**的冒烟测试：

```sh
# 本机 Ollama（零凭证、零费用）
WORKSPLICE_SMOKE_MODEL=ollama:qwen2.5 .venv/bin/python -m pytest tests/test_agent_smoke.py -q -s
# 远端 provider（会离开本机、可能产生费用，需要 key 与显式放行）
WORKSPLICE_SMOKE_MODEL=dashscope:qwen-max WORKSPLICE_SMOKE_ALLOW_REMOTE=1 \
  DASHSCOPE_API_KEY=... .venv/bin/python -m pytest tests/test_agent_smoke.py -q -s
```

## 视觉契约

工作台只**只读**参照 `worksplice-design-system/`（原型 `worksplice-app.html` 保持原样，不被修改）：
token 名、三栏尺寸（rail 252 / 消息流 880 / dock 380）、方形头像块（7px 圆角 + `--av-0…--av-4`）、
未读徽标色族（`--unread` / `--unread-soft` / `--unread-deep` / `--unread-graphic`）、
四态状态点、提及高亮（`--accent-soft` 底 + `--accent-deep` 字）与空态文案都按名字消费。
色值一律 `oklch()`，界面为简体中文。

业务状态的唯一正本是后端；前端不使用 `localStorage` 保存任何业务状态
（唯一允许的本地记忆是「上次打开的频道 id」）。后端不可达时前端**禁止提交**并给出明确提示，
不做本地消息队列、不静默吞消息，重连后自动恢复。
