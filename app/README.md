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
| 事件流单连接寿命 | `ALIBOT_SSE_ROTATE_SECONDS`（默认 55 秒，到期服务端主动收尾、浏览器带 `last_seq` 重连） |

改名不保留兼容：只有 `ALIBOT_*` 环境变量生效，旧前缀 `WORKSPLICE_*` 与旧数据目录 `~/Worksplice`
一律不再被识别，也不做迁移（那是另一个项目的目录）。

备份：`cp -R ~/Alibot ~/Alibot-backup-$(date +%F)`。恢复：反向复制回去。
库文件旁边可能有 `-wal` / `-shm` 两个伴随文件，一起复制即可。

从首版升级：本版不做自动迁移，启动时若库的 schema 版本不是本版会**拒绝启动**；若你的库已被旧版代码
半改名（列表页正常但点进频道全 404），可用 `python -m alibot --repair-targets --data-dir <数据目录>`
这个**显式**入口把旧库的作用域数据补齐（幂等、自校验、打印改动行数）——**执行前请先备份该数据目录**。

## 数据模型（决策级）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `member` | id, kind(`owner`/`agent`), 显示名, 头像色位 | 身份正本；空库只有一条 `owner` 行 |
| `agent_config` | member_id, 角色, 模型标识, 系统提示, 启用开关 | 模型与系统提示是**配置数据**，换模型不改代码 |
| `remote_consent` | provider, acknowledged_at | 远端数据流告知的一次性确认；**不存任何凭证** |
| `workspace_settings` | id(=1), locale, message_density, theme, updated_at | **工作区设置**：单行表，列就是明确列出的档位（语言偏好 / 消息密度 / 主题三档）。人类面可写配置类取值，**不得**写用量类事实 |
| `channel` | id, 名称, 描述 | |
| `channel_member` | channel_id, member_id | 每频道显式成员表 |
| `channel_read` | member_id, channel_id, last_read_seq | 已读位置（未读的正本）。owner 的行使未读徽标；agent 的行是它自己的消费游标 |
| `message` | id, target_id, seq, author_member_id, kind, 正文, **提及快照** | **append-only**，`UNIQUE(target_id, seq)` |
| `round_outcome` | member_id, target_id, base_seq, status, reason, 起止时间, 是否消耗预算, 是否被合并 | 轮次结果，**append-only**。`consumed_budget` 是**行级**标记（这一轮由一次已计一次的跳送来），**不可按行求和**——跳数正本是 `channel_budget.used` |

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
- **唤醒面** = 该频道的全部 agent 成员（**排除作者自己**）+ **穿透**（被个人提及但**未加入**该
  频道的 Agent）。穿透不豁免预算。
- **回应判断**：被个人提及是确定信号 → **必须回应**（自判忽略按 `failed` 处理，不静默）；
  明确无关 → `ignored`（正常协议选择，不是失败）。
- **每轮落一条 `round_outcome`**：`replied` / `ignored` / `failed` / `merged` / `budget-exhausted`，
  带可读 reason、起止时间、是否消耗预算、是否被合并。
- **一轮至多一条频道消息**；落库路径只有一条。
- 消费游标只在 `replied` / `ignored` 推进；`failed` 不推进（下次唤醒会重新读到，但不是自动重放）。

### 穿透（点名一个未加入该频道的 Agent）

点名一个不在该频道的 Agent 时它照样被送达（`penetration`，信号里带 `reason=mention`）。它**要
回复就顺带加入**：先写一条 `system` 事件消息说明它加入（`kind="system"`，作者就是它自己），
再写回复（`seq` 依次递增，先成员变化再发言）；**不回复则不加入**（成员关系只随真实回复发生）。
`system` 事件消息**不唤醒任何人、不消耗预算** —— 它不是对话回合。

### 有界响应（策略 B）：预算从「只记录」变成「真压制」

- **人类世代** = 该频道最近一条人类消息的 `seq`；人类消息落库**即**开启新世代，并把该频道的
  回应预算重置为 **3**。
- **一次 agent→agent 跳** = 一条 **Agent 消息**导致**至少一个其它** Agent 被唤醒（即**投递**了
  至少一个唤醒）；一条消息即使唤醒多个成员也只消耗 **1** 次。被**合并**掉的重复唤醒不额外计数、
  也不额外消耗预算。目标全部处于 in-flight ⇒ 零投递 ⇒ **不算一次跳**。
- **预算耗尽**：Agent 消息照常落库（append-only、不可撤回），但**不产生任何唤醒**（一个都不投递），
  为每个「本应被唤醒却被压制」的成员各记一条 `budget-exhausted`。人类消息随后落库即重置。
- **硬上界**：任一人类世代内同一频道的 agent→agent 跳数 **≤ 3**。上界由「耗尽的世代里零投递」
  构造性保证。
- **预算落库**（`channel_budget` 表）：`used` / `epoch_seq` 是后端持久事实，重启后可恢复。
- **`budget-exhausted` 不是出错**：它既不是「不想说」也不是「它坏了」，状态点仍是空闲。
- **跳数的单一正本**：`channel_budget.used`（= `GET /api/channels/{id}/budget` 的 `used`）是「该人类
  世代已用跳数」的**唯一权威**，每次跳恰好 +1，不因该跳投递了几个成员而变化。`round_outcome` 行上的
  `consumed_budget` 只是**行级**标记（「这一轮由那次跳送来」），一次跳投递 N 个成员就有 N 条为真的行，
  所以 **`sum(round_outcome.consumed_budget)` 不能当跳数用**（行数与跳数之间没有固定关系：投递的成员若
  无可落库内容就不落行，所以它可能偏大也可能偏小）；要从只读面反推跳数，读
  `budget.used`。`round_outcome` 的 append-only 约束不变。
- **并发合并**：同一 Agent 同时最多一轮 in-flight；窗口内到达的重复唤醒**不排队、不重放**，
  收口时合成**恰好一条** `merged` 记录，`base_seq` 取窗口内**最大 seq**。被合并的消息仍在消费
  游标之后，下一次**新的**唤醒会被一并读到（不重放 ≠ 永久吞掉）。

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
| `GET /api/search?q=<关键词>&target_id=<可选>&limit=<可选>` | 跨 channel / DM / task target 搜索普通消息；默认上限 200，target 可收窄到单一作用域；每条命中返回消息、target（id / kind / 显示名）与后端计算的全部 `highlight_ranges` |
| `GET /api/channels` | 频道列表（含 `last_seq` / `last_read_seq` / `unread_count`） |
| `POST /api/channels` | 新建频道 `{name, description, member_ids}`；校验失败**整体不创建** |
| `GET /api/channels/{id}/messages` | 按 seq 升序取消息（`?after_seq=n` 取增量） |
| `POST /api/channels/{id}/messages` | 发送人类消息 `{body}`（同事务分配 seq，随后唤醒成员面） |
| `GET /api/channels/{id}/members` | 频道成员 |
| `POST /api/channels/{id}/members` | 把 Agent 加入频道 `{member_id}`；幂等（`joined` 区分新增与本来就在），只接受 Agent，校验失败**整体不加入** |
| `GET /api/channels/{id}/round-outcomes` | 轮次结果（只读；首版**没有**可观测页面） |
| `GET /api/channels/{id}/budget` | 有界响应预算 `{epoch_seq, limit, used, remaining}`（只读，后端持久事实） |
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

### 未读口径

未读 = 已读位置之后、`kind ∈ {human, agent}`、**且作者不是本人**的顶层消息条数；`system` 事件
消息不计入。**作者排除本人**是实施票 ③ 按 #4 票面字面改判的（改前 #2 把 owner 自己的消息也
计入自己的未读）。`unread_count`（频道列表 / 单条）与 `unread_total`（workspace 快照）由
`db.py` 里**同一条 SQL** 得出，因此只有一个口径落点。

## 测试与检查

```sh
.venv/bin/python -m pytest          # HTTP + SSE 黑盒契约测试（每条用例独立临时数据目录）
.venv/bin/ruff check app && .venv/bin/ruff format --check app
.venv/bin/python -m mypy            # 配置在仓库根 mypy.ini
node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests   # 配置在仓库根 pyrightconfig.json
node --test app/tests/search_ui.test.cjs # Studio 搜索的 DOM 呈现、错误与并发行为
node --test app/tests/i18n_ui.test.cjs    # 词条机制（同键集 / 无落单键 / 裸中文扫描 / <html lang> / 设置弹窗两档）
```

Python 测试只断言**外部可观察行为**（消息出现与顺序、轮次结果、状态点、事件流帧、seq 单调性、
已读位置、未读计数、不可变性、重启后一致），不检查内部函数或前端 DOM；Studio 搜索 DOM 行为由
上面的 Node 内置测试单独覆盖。每条 pytest 用例的库都在 `tmp_path` 下，**不触碰** `~/Alibot`。

**唯一例外**：`app/tests/test_backend_copy.py` 直接读 `app/alibot/i18n.py` 的两份取值——词条表是
**数据契约**（两份取值必须覆盖同一键集、参数位一致，且后端文案必须取自**当前语言**的那一条），
它的键集不经 HTTP 面暴露，因此按与前端 `i18n_ui.test.cjs` 同款的做法直接读那份数据。行为断言仍然
全部走 HTTP 面。

四类 Agent 行为由 **脚本化替身**（`tests/scripted.py`）固定：回应 / 忽略 / 失败 / 延迟。
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
