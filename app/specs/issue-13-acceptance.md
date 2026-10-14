# 治理票 ⑬ 验收取证（issue #13：CONTEXT.md 词汇表与现行实现对齐）

本文件是 Answer 的支撑材料：逐条列出**依据**与**实际取证命令**，只记跑过后出现的事实。

- 仓库：`whutlichao/alibot`
- 交付物：PR [#15](https://github.com/whutlichao/alibot/pull/15)（OPEN，base `main`，head `issue-13-glossary`，head commit `50e48dc`）；PR body 正本 `app/specs/issue-13-pr-body.md`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-13-glossary`（Orca 独立顶层 worktree）
- 基线：`origin/main` 的 `2b8615c`（#11 合并后的 main）；worktree 由它创建，未 rebase
- 采集时间：2026-10-14
- 票标题（开工时用 `gh issue view 13` 核对）：

> 治理票：CONTEXT.md 领域词汇表与现行实现对齐（清理规格已舍弃的旧方案词条 + 补入承重术语 + 处置 ADR 死引用）

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`；票型是**文档票（治理）**，
因此依其流程执行：读票（`gh issue view 13` + 母票 `gh issue view 1`）→ 声明票标题 → **先只读核实全部事实来源**
→ 改写文档 → 文档自审 → 提交 → PR。按票面「本票不跑源码测试与源码双轴 review」执行门禁：
**文档自审**（结构完整、删改逐条有依据、无死引用、`_Avoid_` 仍有效）＋ 本文件。

票面要求「影响交付、方案分叉或 scope 边界的决策走 `ask`」。本票 1 组，经 `orca orchestration ask`
提问并取得回执（§5）。

## 1. 改动前的取证：票面描述 vs 树里的事实

票面（issue #13 原文）称「**代码注释**已在引用 ADR（如 `orchestration.py` 提到「ADR-0013」、
`service.py`/`app.css` 提到「ADR-0016」）」。**核实结果与此不符**，已 `ask` 上报，coordinator 据此更正了
issue #13 正文并加了更正说明。取证如下：

```console
$ grep -rniE "\badr[ _-]?0*[0-9]+" app/worksplice/    # Python / CSS / JS 源码
（0 命中）

$ git grep -niE "\badr[ _-]?0*[0-9]+"                 # 跟踪文件全仓
CONTEXT.md:56  回复动作 (Reply Action / op) ……（ADR-0013）
CONTEXT.md:72  backfill 归属门禁 ……（ADR-0004 / ADR-0003）
CONTEXT.md:200 视觉契约 ……（ADR-0014 / ADR-0015）
CONTEXT.md:208 档位 ……（ADR-0015）
app/specs/issue-2-acceptance.md:322  消息正文局部例外（ADR-0016）：
docs/agents/domain.md:37  > _Contradicts ADR-0007 (event-sourced orders)…_
worksplice-design-system/{DESIGN.md,README.md,SKILL.md,colors_and_type.css,
  ui_kits/app/app.css,preview/colors-primary.html,worksplice-app.html}  17 处 ADR-0015/0016/0014

$ git log --all --diff-filter=A --name-only --format='' -- '*adr*' | sort -u
（空）    # 全历史（含所有分支）从未存在过任何 ADR 文件
$ ls docs/                                  # 只有 agents/，没有 adr/
```

三条结论：

1. **`docs/adr/` 缺失是真的**，且**从来**没有过（不是被删掉）。
2. **`app/worksplice/` 源码里没有任何 ADR 引用**——票面「代码注释引用 ADR」不成立。
   ADR-0013 只活在 `CONTEXT.md` 的**回复动作**词条（本票要删的旧方案词条）里；ADR-0014/0015/0016
   只活在 `worksplice-design-system/`（该包自带编号）、`CONTEXT.md` 的**保留**词条里、以及
   `app/specs/issue-2-acceptance.md`（历史取证）与规格 #1 §10 自己的一行引用里。
3. 因此票面三(a)（「把**代码注释**的 ADR 引用改为指向规格章节」）**没有可改的对象**。

## 2. 验收一：词条处置表（逐条 + 依据）

判据：**规格 #1 §11 的保留 / 舍弃清单为准**，**`main` 上的现行代码（`2b8615c`）为事实来源**。
规格 §11 原文：

> **保留（语汇与语义）**：频道 / 成员 / 消息 / 提及 / 穿透 / 唤醒 / 回应判断 / 轮次 / 轮次结果 的定义；
> 消息永久记录；成员 soft-delete 保留作者渲染；"唤醒是注意力不是命令"；target 内单调 seq +
> `UNIQUE(target_id, seq)`；"消息落库后触发唤醒"的顺序。
>
> **舍弃（旧技术方案，与本版形态不匹配）**：pi runtime 与 SDK 委托边界；会话文件 / 固化 / 无主文件 /
> backfill 归属门禁 / 跨作者内容去重；家目录 / 工作区 / 允许根 / 路径守卫 / 沙箱；Cwd 互斥；
> 回复动作 op / 观察相；成本看板 / 模型探测 / 错误恢复；DM 懒创建与接续；任务状态机 / 指针消息 / 任务视图。

处置词表：**保留**（本仓当前仍成立）/ **改写**（概念在，表述属旧方案）/ **移除**（规格已舍弃）。

### 2.1 移除（27 条）——规格 §11「舍弃」清单点名

| # | 原词条 | 依据 |
| --- | --- | --- |
| 3 | 家目录 (Home) | §11 舍弃「家目录 / 工作区 / 允许根 / 路径守卫 / 沙箱」；§Out of Scope「Agent 的本地能力：文件与 Shell 工具、沙箱与路径约束、agent 家目录与项目目录」。代码里无任何目录概念。 |
| 4 | 项目目录 (Project Directory) | 同上 |
| 5 | 工作区 (Workspace) | 同上（另见「明确的差异」：成员表是正本，身份不再由 pi session 承载） |
| 6 | 允许根 (Allowed Roots) | 同上 |
| 7 | 路径守卫 (Path Guard) | 同上 |
| 8 | 沙箱 (Sandbox) | 同上 |
| 13 | 回复动作 (Reply Action / op) | §11 舍弃「回复动作 op / 观察相」。`grep -rn "ops" app/worksplice/` 无该机制；成员面只有「一轮 → 零或多条待落库回复」一个接缝（`agent_seam.py`） |
| 14 | 观察相 (Observation Step) | 同上 |
| 15 | 会话文件 (Session File) | §11 舍弃「会话文件 / 固化 / 无主文件 / …」 |
| 16 | 无主文件 (Unowned Session File) | 同上 |
| 17 | backfill 归属门禁 (Backfill Ownership Gate) | 同上 |
| 18 | 跨作者内容去重 (Cross-author Content Dedup) | 同上 |
| 21 | 私信 (DM) | §11 舍弃「DM 懒创建与接续」；§Out of Scope「私信（DM）：包括懒创建与接续」；用户已确认边界第 5 条 |
| 22 | 懒创建 (Lazy Create) | 同上 |
| 23 | 接续 (Resume) | 同上 |
| 32 | 静音 (Mute) | §11 未列入保留清单，且 §Out of Scope 未含该能力；`app/worksplice/` 无 mute 写点，唤醒只排除作者与 `system` 消息 |
| 33 | 任务 (Task) | §11 舍弃「任务状态机 / 指针消息 / 任务视图」；§Out of Scope「任务（Task）」 |
| 34 | 任务创建事件消息 | 同上 |
| 37 | 任务视图 (Task View) | 同上 |
| 38 | 指针消息 (Pointer Message) | 同上 |
| 41 | Cwd 互斥 (CwdMutex) | §11 舍弃「Cwd 互斥」 |
| 42 | 成本看板 (Cost Board) | §11 舍弃「成本看板 / 模型探测 / 错误恢复」；§Out of Scope「流式呈现的细化：…成本看板与可观测页面」 |
| 44 | 模型探测 (Model Probe) | 同上 |
| 45 | 错误恢复 (Error Recovery) | §11 舍弃；§3「Agent 状态点…**不再**从任何会话存活推导」 |
| 46 | SDK 委托边界 (SDK Delegation Boundary) | §11 舍弃「pi runtime 与 SDK 委托边界」；§1「浏览器**不直接接触** AgentScope」 |
| 47 | 薄 Wrapper (Thin Wrapper) | §11 舍弃「pi runtime 与 SDK 委托边界」（该条词条引的是 `lib/rpc/session.ts`，本仓无此文件） |
| 49 | 唯一导入面 (Single Entry / Facade) | 出自旧技术方案（`lib/domain/collab/index.ts`、TS 单口导入）；§11 保留清单不含它，本仓无 `index.ts` |

### 2.2 移除（6 条）——规格未保留 + 本仓无对应事实

| # | 原词条 | 依据 |
| --- | --- | --- |
| 9 | 准入闸 (Access Gate) | §Out of Scope「多用户与安全层：账号、登录、鉴权、邀请、权限分级、**准入闸**（共享密码 / Host 白名单 / 来源校验）」。本仓只有 loopback 绑定（`__main__.py`），没有准入闸机制；保留它会描述一个不存在的层 |
| 10 | 调用者身份 (Caller Identity) | 它的两种形态里「凭证自述」本仓不存在（`agent_seam.py` 无令牌；§1「首版不引入 agent 令牌」）。旧词条用「用户 / session / 准入闸」做 `_Avoid_`，全属旧方案。其中真正承重的部分（结构身份 + 人类面/成员面）已由第 11、12 条承载 |
| 20 | 房间 (Room) | §11 未保留；旧词条是双作用域（工作台 / target）的历史残留，本仓 target 恒为频道（`UNIQUE(target_id, seq)`、`get_channel(target_id)`）。保留会让「作用域」与「工作台」混成一件事 |
| 35 | 线程回复总数 (Thread Reply Count) | 首版无线程（§2「首版没有 task/thread，不引入线程未读语义」） |
| 36 | 任务线程未读 (Task Thread Unread) | 同上 |
| 48 | 深模块 (Deep Module) | 出自旧技术方案（`index.ts` re-export、TS 模块形态）；§11 保留清单不含它，本仓无对应形态 |

### 2.3 改写（18 条）

| # | 原词条 | 依据（现行代码 / 规格） |
| --- | --- | --- |
| 1 | 成员 (Member) | 原 `_Avoid_` 是「会话、session（**那是 pi session**）」——pi runtime 已舍弃。改为不含 pi 的表述；补记 soft-delete **无删除入口**（`grep -rn "deleted_at" app/` 只有读取侧过滤与列定义，无 UPDATE） |
| 2 | Agent | 原表述「拥有家目录、可绑定项目目录…可认领任务」三件事全属舍弃清单。改为现行事实：持有 Agent 配置、可被唤醒、可穿透、不做文件/shell、不经 HTTP |
| 11 | 结构身份 (Structural Identity) | 概念在 §1 明确保留（「Agent 身份因此是**结构身份**」）。原表述夹带「agent-loop」「凭证自述」等旧措辞；改为单条词条、引现行落点（不经 HTTP、无令牌） |
| 12 | 人类面 / 成员面 | 概念在 §1 与 `service.py` docstring 里。原 `_Avoid_` 用「内网/外网」——那是旧方案的分层；改为按**调用者类别**表述 |
| 19 | 频道 (Channel) | §11 保留清单之一。原定义「加入 = 订阅该频道…」会把「成员关系」与「连接」绑死；改为「加入 = 成为**频道成员**」（§2「旧稿的 `#all` 自动加入语义**不保留**」）|
| 24 | 订阅 (Subscription) | 原词条把「订阅」定义成「加入频道（drain 可见…）」——那是旧方案的技术事实；本仓「订阅」的真实落点是**事件流**（`events.py`、`GET /api/channels/{id}/events`）。改为事件流订阅，并把「成员关系」剥给 `频道成员` 词条 |
| 25 | 唤醒 (Wake) | §11 保留。原表述夹带「静音者 / 任务 owner 自醒 / 提醒作者 / 秘书」四处旧机制；改为现行 wake 面（频道 Agent 成员 − 作者 + 穿透）与现行 reason 词表（`WAKE_REASON_CHANNEL_MESSAGE` / `WAKE_REASON_MENTION`）|
| 26 | 回应判断 | §11 保留。原表述的确定信号含「进行中任务 owner 收到线程他人消息 / 提醒指向自己」，并把 ignore 描述成「error 状态点 + cap-ack 防死循环」——现行是 `failed` 轮次结果、无 cap-ack。改为现行 rubric（确定信号只有个人提及）|
| 28 | 轮次结果 (Round Outcome) | §11 保留。原表述落 `round_logs` 表（本仓无此表，`grep -c round_logs` = 0 命中）与「可观测页」（§Out of Scope 不做）。改为现行 `round_outcome` 表 + 五词 status 词表（`db.py` 的 CHECK 约束）|
| 29 | 频道成员 (Channel Member) | 原表述「`#all` 自动加入且不可离开；thread 回复继承频道规则」与 §2 直接冲突（显式成员表）且含首版没有的 thread；改为显式成员表 + 穿透是唯一自动增长路径 |
| 30 | 提及 (Mention) | 原表述「穿透 mute 与未加入限制」「点击可查看成员（agent → 详情面板，人类 → 简介弹窗）」：mute 已移除；`grep -n "dataset.member" app.js` 只有 `dataset` 赋值，**没有** `[data-member]` 的 click 监听 → 详情面板由**失败/未启用提示**打开（`data-dock-toggle`），不是由提及点击。另校正解析纪律（左边界只排除邮箱式）|
| 43 | 状态点 (Status Point) | §11 保留概念，但 §3 重定义语义（「**不再**从任何会话存活推导」）。原表述含「模型探测」「生命周期动作（重启 / 重置 / 换工作区 / 删除身份）」——全属舍弃。改为现行四态推导（`orchestration.status_of`：availability → in-flight → 最近非 `merged` 轮次结果 → `online`）|
| 50 | 视觉契约 (Visual Contract) | §11 保留（§10 明写「视觉契约按名字消费…不得重命名」）。校正三处：①正本是 `worksplice-design-system/`**只读参照**，产品扩展层在 `app/worksplice/studio/product-tokens.css`（旧稿写的 `app/globals.css` 本仓不存在）；②补 `--on-accent`（§10 点名）；③ADR-0014/0015 的引用改指设计系统正本并注明「本仓不解析其编号」（见 §4）|
| 51 | 头像色 (Avatar Tint) → **头像色位 (Avatar Slot)** | 校正术语：库里叫 `avatar_slot`（`member.avatar_slot`、`AVATAR_SLOT_COUNT`），旧词条叫 "Tint"。另「human 保留中性格」与代码不符（`owner` 的 `OWNER_AVATAR_SLOT = 4` 是确定的色位，不是中性格）|
| 39 | 成员面板 (Member Panel) | §11 未舍弃；现行存在（`index.html` 的 `<aside class="dock">` + `dockMembers`）。但旧词条把它定义成「频道头部 `Users` 图标展开区…点击打开成员详情」——现行 dock 是由消息流里的**失败/未启用提示**（`data-dock-toggle`）打开的，且 dock 本身由独立按钮开关。按现行事实校正 |
| 40 | 补全 (Mention Completion) | 概念成立且 **#9 已交付**（`tests/test_mention_completion.py`）。旧词条写「列出全部 agent（非频道成员标注"未加入"）」——现行候选只来自**当前频道的 Agent 成员表**，没有「未加入」标注态，另有三种空态（无成员 / 读不到 / 过滤无匹配）与「名字无任何合法插入形态时不插入」的边界。按现行事实校正 |
| 52 | 档位 (Tier) | §11 保留（§10「每个角色族四档 `-fill / -soft / -deep / -graphic`」）。仅把 ADR-0015 的引用改指设计系统正本（见 §4）|
| 53 | 角色族 (Role Family) | 同上（仅 ADR 引用改指；族名与后缀纪律按 §10 原文不变）|

### 2.4 保留（2 条）

| # | 原词条 | 保留理由 |
| --- | --- | --- |
| 27 | 轮次 (Round) | §11 保留；现行 `Round` 概念即 `run_round` 一轮（内容未改）|
| 31 | 穿透 (Penetration) | §11 保留；现行 `target.pierced` + `_WakeTarget`（内容未改）|

**合计覆盖原表 53 条**：移除 27 + 6 = 33、改写 18、保留 2，33 + 18 + 2 = **53**。
每条都在这四张表里各占一行（`#` 列即 `CONTEXT.md`@`2b8615c` 的原词条序号）。

交叉核对（脚本，名单提取自旧/新两份 `CONTEXT.md`）：

```console
$ python3 -c "…提取两份文件的词条名…"
old 53 new 36
carried over: 19        # 名字沿用的 19 条 = 改写表里同名的 17 条 + 保留 2 条
brand new:    17        # §3 新增表里的 17 条
dropped:      34        # 移除 33 条 + 头像色→头像色位（改名，不是移除）
```

## 3. 验收二：改写后的 `## Language`

结构自审（**文档自审**项之一）：

```console
$ grep -c '^\*\*' CONTEXT.md                       # 以 ** 开头的行数（含强调句换行）
43
$ python3 - <<'EOF'          # 只数「**术语 (English)**:」形态的词条块
import re
blocks = re.split(r'\n\s*\n', open('CONTEXT.md', encoding='utf-8').read())
entries = [b for b in blocks if re.match(r'^\*\*[^*]+(\([^)]*\))?\*\*:', b.strip())]
print(len(entries), [b.split('\n')[0] for b in entries if '_Avoid_:' not in b])
EOF
36 []
$ grep -n '^#\|^###' CONTEXT.md
1:# Worksplice                                    ← 标题未动
6:## Language                                     ← 章节名未动（domain.md 靠它定位）
8:### 协作域            64:### 唤醒与回应        106:### 有界响应（策略 B）
136:### 身份与边界        161:### 未读与订阅        187:### 前端与视觉
```

- **结构完整**：**36 条词条**，每条都是 `**术语 (English)**:` + 定义 + `_Avoid_:`（`domain.md` 规定形态），
  正文中文（`AGENTS.md` 语言约定）。按 `GLOSSARY-FORMAT.md`「Group terms under subheadings when natural
  clusters emerge」分了 6 个子标题。
- **`_Avoid_` 仍有效**：上面脚本核对 **36/36** 条各自带一条 `_Avoid_`，**无孤儿 `_Avoid_`**
  （没有"删了词条、留下 Avoid"的情况——旧词条的 `_Avoid_` 随词条一并处置）。
  新 `_Avoid_` 点名的词（会话 / 通知 / room / 房间 / 工作区 / 状态灯 / 日志 / 色阶 / 主题…）
  在本仓输出里仍应避免，且都有依托词条。
- **删改逐条有依据**：见 §2 三张表（27 + 6 + 14 + 6 = 53，覆盖原表全部词条）。
- **未动**：`# Worksplice` 标题、`## Language` 章节名、规格 #1 正文（只读）。

**新增承重词（17 条 = 现行 36 条 − 从旧表延续的 19 条）**，均为 `main` 上真实在用、由 #2/#3/#4 交付：

| 新词条 | 代码 / 规格落点 |
| --- | --- |
| 消息 (Message) | `message` 表 + `message_no_update`/`message_no_delete` 触发器；规格 §2「append-only 是硬约束」 |
| seq | `UNIQUE(target_id, seq)`；规格 §2「频道内单调 seq」 |
| target | `message.target_id` / `channel_read.channel_id`；`get_channel(target_id)` |
| 系统事件消息 | `kind="system"`；`orchestration.py:476` 穿透加入时落库 |
| 消费游标 (Agent Read Cursor) | `db.agent_cursor()`；`replied`/`ignored` 推进、`failed` 不推进 |
| 成员面适配接缝 (Agent Seam) | `agent_seam.py` 的 `AgentSeam` Protocol + `tests/scripted.py` |
| **人类世代 (Human Epoch)** | `db.human_epoch_seq()`；规格 §5 |
| **回应预算 (Response Budget)** | `BUDGET_LIMIT = 3`、`db.consume_budget()`；规格 §5 |
| **跳 (Jump)** | `_spend_jump()`；`notify()` 的「投递了至少一个唤醒」判据 |
| **预算耗尽 (Budget-exhausted)** | status 词表之一；`notify()` 耗尽的世代零投递 |
| **预算表 (channel_budget)** | `channel_budget` 表（`epoch_seq` / `used`）；正本不是行数求和（见 #12） |
| **未读 (Unread)** | `CHANNEL_SELECT_SQL` 里那**唯一一条** SQL：`kind IN ('human','agent') AND author_member_id != :member_id` |
| **Agent 配置 (agent_config)** | `agent_config` 表；规格 §2 表 |
| 已读位置 / 远端数据流告知 / 工作台 / 头像色位 | `channel_read` / `remote_consent` / `GET /api/workspace` / `avatar_slot` |

## 4. 验收三：ADR 缺口处置结论与理由

**核实的事实**（见 §1）：`docs/adr/` 缺失且从未存在；`app/worksplice/` 源码 **0** 处 ADR 引用；
ADR-0013 只活在 `CONTEXT.md` 的**回复动作**词条（本票删除），ADR-0014/0015/0016 只活在
`worksplice-design-system/`（只读参照包，自带编号）与 `CONTEXT.md` 的**保留**词条里。

**处置结论：票面 (b) 的收窄版——裁定 A**（coordinator 于本票 `ask` 回执中裁定，见 §5），
且依据不是偏好而是**仓库自己的规则**。`docs/agents/domain.md` 第 12 行原文：

> If any of these files don't exist, **proceed silently. Don't flag their absence; don't suggest creating
> them upfront.** The `/domain-modeling` skill … **creates them lazily when terms or decisions actually
> get resolved.**

所以 `docs/adr/` 的缺失是**设计如此**（懒创建），而本票正是"决策已经定型"的触发时刻。

**做了什么**：

1. **新建 `docs/adr/`，只收本仓真实做过的决策**（三条，均符合 `ADR-FORMAT.md` 的三项收录标准：
   难回退 + 没上下文会让人困惑 + 是真实取舍的结果）：
   - `0001-messages-are-append-only.md` —— 消息 append-only、不可编辑不可删除；
     含否掉"作者可撤回"的理由与 soft-delete 的同源理由。
   - `0002-response-budget-caps-relay-at-three.md` —— 预算 ≤3 是有界响应的**手段**（零投递使其成为
     构造性保证）；含"跳数正本是 `channel_budget.used` 而非行数求和"。
   - `0003-agents-have-structural-identity-only.md` —— Agent 只有结构身份、不经 HTTP；
     含否掉自述凭证的三条理由（可伪造 / 可转发 / 必然可被持有者读出）。
   编号从 0001 起——本仓此前无 ADR，按 `ADR-FORMAT.md`「Scan `docs/adr/` for the highest existing
   number and increment」。
2. **不改任何代码**：票面三(a) 的"改代码注释里的 ADR 引用"**没有对象**（源码 0 处 ADR 引用）。
   因此本票**没有**运行 ruff / format / mypy / pyright 的必要（无源码改动），
   如实记录为"不适用"而不是"省略"。
3. **不建 0014/0015/0016**：那等于把**只读参照包**自己的 ADR 塞进本仓编号空间。
   规格 §10 与 `app/README.md` 都把 `worksplice-design-system/` 定为只读参照，其内部编号是**它自己的**。
   改为在 `CONTEXT.md` 的**视觉契约 / 档位**词条里注明"该包自身编号，本仓不解析、不复制"，
   并把引用改指设计系统正本（`DESIGN.md` / `README.md` / `SKILL.md`）。
4. **不为 ADR-0013 建 ADR**：它挂在**回复动作**词条上，而该词条本票要删（旧方案）。随词条一并消失。
5. **不动** `app/specs/issue-2-acceptance.md:322` 的 ADR-0016（那是**历史取证**，改了就是篡改历史）、
   规格 #1 §10 的 ADR-0016（规格正本，本票只读）、`worksplice-design-system/` 包内 17 处（只读参照）、
   `docs/agents/domain.md:37`（那是 ADR 冲突的**举例格式**，不是引用）、
   `docs/agents/domain.md` 与 `AGENTS.md` 里 `docs/adr/` 的声明（**刻意留着**，正等懒创建；
   现在目录已真实存在，这两处引用由死引用变成有效引用）。

## 5. 决策记录（`orca orchestration ask`）

本票影响文件布局与 scope 边界，按票面走 ask：

- **问**：#13 三的 ADR 处置三选一（票面描述与取证事实不符：`app/worksplice/` 0 处 ADR 引用；
  本仓 ADR 编号空间为空）+ 取证文档落点。
- **答（回执摘要）**：**选 A**；并给出上述"懒创建"依据。同时**明确授权**本票可写
  `app/specs/issue-13-acceptance.md`（本文件）——票面 Ownership 原写 `CONTEXT.md` + `docs/`，
  取证文档按本仓惯例（#8/#9）应落在 `app/specs/`。coordinator 同时更正了 issue #13 正文里
  "代码注释引用 ADR-0013/0016"的错误表述。
- **不选 B**（另补 0014/0015/0016 使引用全解析）：等于把只读参照包的编号塞进本仓编号空间。
- **不选 C**（完全不建 ADR、全改指规格章节）：需删掉 `domain.md` / `AGENTS.md` 里 `docs/adr/` 的
  声明，而那是刻意留着的目录结构声明，删了反而把仓库声明的结构改坏。

## 6. 验收四：一致性核对

### 6.1 `domain.md`「Before exploring, read these」的路径（无死引用）

```console
$ ls -d CONTEXT.md docs/adr/  && ls docs/adr/
CONTEXT.md   docs/adr/
0001-messages-are-append-only.md
0002-response-budget-caps-relay-at-three.md
0003-agents-have-structural-identity-only.md
$ sed -n '/## Language/,+1p' CONTEXT.md     # 章节名仍在
## Language
```

- `CONTEXT.md` 的 `## Language` —— **存在**（且仍叫这个名）。
- `docs/adr/` —— 改动前**不存在**（`domain.md` 的规则是"proceed silently"，不必 flag），
  改动后**真实存在且可读**（三条真实 ADR）。无死引用。
- 顺带核对 `docs/agents/` 下另外两处声明：`domain.md` 的 `GLOSSARY.md` / `GLOSSARY-MAP.md` 两条是
  **条件句**（"If a root `GLOSSARY.md` is ever added…"），`AGENTS.md` 的 `docs/agents/*.md` 三份均存在。

### 6.2 抽查本仓现行输出的术语漂移

抽查对象：issue 标题、测试名、代码注释（票面点名的三类）。

```console
$ for n in 2 3 4 8 9 12; do gh issue view $n --json title -q .title; done
实施票 ①：从空数据新建频道、发送并持久化频道消息、前端呈现、每频道已读位置与未读徽标
实施票 ②：新建 Agent 与模型配置、远端数据流告知、@提及唤醒 AgentScope 后端 agent 并自判回应/忽略/失败、SSE 实时呈现与断线补拉
实施票 ③：多 Agent 同频道接力、普通消息唤醒与自判、未加入被点名穿透、agent→agent 预算 ≤3 与并发/失败隔离、刷新后可追溯
修复票：轮次失败/未启用 reason 在消息流里不可见（@ 之后「没有任何反应」的真实缺陷）
补票：composer 里 @ 的成员补全（#2 承诺、#3 漏接的能力）
修正票：预算记账粒度——确立 channel_budget.used 为跳数单一正本，消除按行求和的歧义
```

| 抽查项 | 术语 | 与新词汇表 | 结论 |
| --- | --- | --- | --- |
| issue #2 标题 | 频道 / 消息 / 已读位置 / 未读徽标 | 一致（频道 / 消息 / 已读位置 / 未读） | 无漂移 |
| issue #3 标题 | Agent / 模型配置 / 远端数据流告知 / @提及 / 唤醒 / 回应 / 忽略 / 失败 / 实时呈现 | 一致（Agent / Agent 配置 / 远端数据流告知 / 提及 / 唤醒 / **轮次结果**词表 `ignored`） | 一致：标题里的「忽略」是 `ignored` 的中文口语形态，词表已把两端都写出 |
| issue #4 标题 | 接力 / 唤醒 / 自判 / 穿透 / agent→agent 预算 / 失败隔离 | 一致（**跳** / 唤醒 / 回应判断 / 穿透 / **回应预算** / 轮次结果） | 一致：标题的「接力」对应的正是新词条**跳**的中文说法，已在该词条 `_Avoid_` 之外的定义句中显式对齐 |
| issue #8 标题 | 轮次 / 失败 / 未启用 / reason | 一致（轮次 / `failed` / **未启用**是状态点 `offline` 的 UI 名 / 轮次结果的 reason） | 无漂移 |
| issue #9 标题 | composer / @ / 补全 | 一致（**补全**） | 无漂移 |
| issue #12 标题 | 预算记账 / `channel_budget.used` / 跳数 | 一致（**预算表** / **跳** 的正本） | 无漂移；#12 是并行票，本票的**预算表**词条已写明"跳数正本不是行数求和"，与 #12 的裁定同向 |
| 测试名 | `test_..._bounded_by_three_jumps_per_human_epoch`、`test_..._pierced_agent_that_replies_joins_and_leaves_a_system_event`、`test_..._budget_state_is_persisted_and_survives_restart` | 一致（**人类世代** / **跳** / **穿透** / **系统事件消息** / **预算表**） | 无漂移：测试名用的英文标识与新词表的 English 名逐字对应 |
| 代码注释 / docstring | `orchestration.py`「人类世代 / 一次 agent→agent 跳 / 预算耗尽 / 穿透不豁免预算」；`db.py`「消息 append-only / 频道内单调 seq / 未读计数…作者不是本人」；`service.py`「人类面 / 成员面」 | 一致 | **无漂移**：新词表的定义句多处直接取自这些 docstring |

**一处有意保留的差异（记录，不在本票修）**：`app/worksplice/studio/app.js:700` 与 `index.html:131`
在注释里用「空闲 / 正在回复 / 出错 / 未启用」描述四态，与**状态点**词条一致；但 `index.html` 的
`<title>` 与 rail 的 `aria-label` 仍叫「协作工作区 / 工作区导航」，而 **`工作区 (Workspace)`** 旧词条
本票已移除、改用 **工作台 (Workspace)**。这是 2 处**UI 文案**漂移（不是逻辑），且旧词条的 `_Avoid_`
本就写着"目录、文件夹"（与"工作台"并非同义冲突）。按票面「不要求本票全量修代码」，**如实记录**：
不改这两处文案，也不把矛盾留在词汇表里（新词表已不再定义"工作区"这个领域概念）。
另外 `worksplice-design-system/DESIGN.md` 里 3 处 "room" 指的是设计系统的界面语义（茶室文案等
静态文案），不是本仓领域术语，按只读参照不动。

## 7. 文档自审（本票的门禁）

按票面「门禁：文档自审（结构完整、删改逐条有依据、无死引用、`_Avoid_` 仍有效）」四项逐条自审：

| 门禁项 | 结论 | 取证 |
| --- | --- | --- |
| **结构完整** | 通过 | §3：36 条词条，形态 `**术语 (English)**:` + 定义 + `_Avoid_`；6 个子标题分组；标题与章节名未动 |
| **删改逐条有依据** | 通过 | §2 四张表覆盖原 53 条（27 + 6 移除 = 33、改写 18、保留 2；33+18+2=53），每条给规格 §11 / §Out of Scope / 现行代码的落点 |
| **无死引用** | 通过 | §6.1：`CONTEXT.md` `## Language` 与 `docs/adr/` 均真实存在；`domain.md` 的另外两条是条件句 |
| **`_Avoid_` 仍有效** | 通过 | §3：36/36 条各带一条，0 孤儿 `_Avoid_`；被删词条的 `_Avoid_` 随词条处置 |

**本票不跑的项**（票面明写）：源码测试与源码双轴 review；若改代码注释才需 ruff/format/mypy/pyright
——本票**无源码改动**（§4 第 2 点），因此这些检查**不适用**。

## 8. 取舍与遗留

**取舍**

- **旧词条以"处置"而非"静默删除"处理**：29 条移除全部留依据（§2.1 / §2.2），本文件就是这张表的正本；
  词汇表本身不留历史包袱。
- **ADR 只收三条**：按 `domain.md` 的懒创建规则与本仓真实决策；`worksplice-design-system/` 的编号
  明确划在仓外，因此本仓 ADR 编号空间从 0001 干净起步。
- **`频道成员` 与 `订阅` 拆成两个概念**：旧词表把"加频道"与"被唤醒/可见"绑在一个词条里，
  而本仓的两件事分别落在 `channel_member` 表与 SSE 事件流上。拆开后 `_Avoid_` 也各归其位。
- **`头像色` 改名 `头像色位`**：跟 `avatar_slot` 这个真实列名对齐，而不是保留一个描述性英文名。
- **未改 2 处 UI 文案（工作区）**：见 §6.2；记录而不修，符合票面"不要求全量修代码"。

**遗留（记录，不在本票范围）**

- 2 处 UI 文案漂移（`index.html` 的 `<title>` 与 rail `aria-label` 里的"工作区"）——如实记录，未改。
- `app/specs/issue-2-acceptance.md` 里 1 处 ADR-0016 引用按 coordinator 裁定**不动**（历史取证，
  改了就是篡改历史）。因此本仓仍存在一处指向**仓外编号空间**的历史引用，这是**有意的**，
  新词汇表已注明"该包自身编号，本仓不解析"。
- `channel_budget.used` 的记账粒度由并行票 #12 处置；本票只在词汇表层面写明"跳数正本不是行数求和"，
  未改任何编排/持久化逻辑（票面 Work constraints 明写不与 #12 抢同一处注释）。
