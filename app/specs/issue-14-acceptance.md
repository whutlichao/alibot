# 补票 ⑭ 验收取证（issue #14：新建 Agent 后可在频道内 @ 到它）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。
issue 上的 Answer 摘要指向本文件。

> 阅读说明：§4 的读数来自**独立临时数据目录**上的真实 uvicorn 进程 + 真实 Chromium
> （ego-browser），Agent 行为由**脚本化替身**固定 ⇒ 零付费模型调用、零凭证。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-14-channel-join`（Orca 独立顶层 worktree）
- 基线：`2b8615c`（#11 合并后的 main 与本票 base commit 同一提交）
- 采集时间：2026-10-14
- 环境：macOS，Python 3.11（`uv venv`），真实 Chromium（ego-browser）驱动
- 基线读数（coordinator 在本票 base 上实测）：`158 passed, 1 skipped`
- 本票收尾读数：**`173 passed, 1 skipped`**（158 + 15 条新增；skipped 仍是那条默认跳过的真模型冒烟测试）

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：
读票（`gh issue view 14`，标题「实施票：新建 Agent 后可在频道内 @ 到它（显式加入频道端点 +
成员面板入口 + 补全词条对齐）」）→ 只读核实事实来源 → 实现 → 单文件测试 → 全量 →
**双轴 code-review**（§7）→ 提交 → PR。

票面要求「open 决策 ask 回来，不编造」。本票共 3 组提问，均经 `orca orchestration ask`
取得回执，裁定见 §5。

## 1. 门禁结果（全量）

从仓库根 `/Users/apple/orca/alibot` 执行（venv 在仓库根 `.venv/`）：

```text
$ .venv/bin/python -m pytest app
173 passed, 1 skipped in 95.01s

$ .venv/bin/ruff check app
All checks passed!

$ .venv/bin/ruff format --check app
37 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 25 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/worksplice app/tests
0 errors, 0 warnings, 0 informations
```

**关于 pyright 的 cwd**：`pyrightconfig.json` 里是 `"venvPath": "."`，而 worktree 里没有
`.venv/`（venv 在仓库根）。因此 pyright 必须**从仓库根**执行、并把 worktree 的路径作为参数传入，
才等价于 CI 里的那次检查：

```text
$ cd /Users/apple/orca/alibot && node ~/.pi-lens/tools/node_modules/pyright/index.js \
    /Users/apple/orca/workspaces/alibot/issue-14-channel-join/app/worksplice \
    /Users/apple/orca/workspaces/alibot/issue-14-channel-join/app/tests
0 errors, 0 warnings, 0 informations
```

（在 worktree 目录内直接跑同一条命令会因找不到 `.venv` 而报 29 条
`Import "fastapi" could not be resolved` —— 那是解释器环境差异，不是本票代码的问题；
`mypy.ini` 顶部对同一个坑有说明。）

本票不需要真模型调用、不需要任何凭证、不产生费用。

## 2. 事实来源核实（只读，先做）

| 事实 | 正本 | 本票怎么用 |
| --- | --- | --- |
| 频道成员只有三个来处：建频道勾选、穿透回复顺带加入、（本票新增）显式加入 | `db.py::create_channel`（`channel_member` 插入）、`db.py:837::add_channel_member`、`orchestration.py:468`（唯一既有调用点） | 本票只**复用** `add_channel_member`，不新增 SQL 路径 |
| `add_channel_member` 返回「是否新增」并同步补 `channel_read` | `db.py::add_channel_member` | 幂等的事实来源与 `channel_read` 就绪都由它保证；`service` 不再自己算一遍 |
| 校验失败的整体不落库 + `{"error":{"code","message","fields"}}` | `db.py::create_channel`、`DomainError.payload()`、`api.py` 的异常处理器 | 新端点的错误形态与文案口径与建频道一致 |
| 「初始成员只能是 Agent」的既有口径 | `db.py::create_channel` 的 `fields["member_ids"]` | 新端点对 `kind != 'agent'` 用**同一句**文案（不另写一套措辞） |
| `@` 补全的候选来源 = 当前频道的 Agent 成员 | `app.js:1072 channelAgentMembers` → `GET /api/channels/{id}/members`；`test_mention_completion.py` 钉住 | 本票**不改**它；只把 CONTEXT.md 反向对齐 |
| 穿透语义：被点名 ≠ 加入 | `orchestration.py::_wake_targets` / `_commit_reply`；`test_bounded_relay.py` 两条断言 | 本票不动 `orchestration.py`，只在加入之后让成员路径接管 |
| dock 面板此前用的是**全工作区**成员（与标题「频道成员」不符） | `app.js:721 renderDock()` 旧实现、`index.html:130` 标题 | 这正是反馈里「前端看得见、@ 选不到」的矛盾来源；本票把它改成频道成员表 |

## 3. 逐条验收（命令 + 可见结果）

### 3.1 端点语义：幂等、404、校验失败、只接受 Agent

```text
$ curl -s -X POST .../api/channels/{id}/members -d '{"member_id":"<agent>"}'（首次）
201→200 {"joined": true,  "member": {"id":"mem_5245ca0a4b1c","kind":"agent","display_name":"小验",…},
         "members": [owner, 小验], "channel": {"id":"ch_26ab2f42d90a",…}}
$ 再调一次（已加入）
{"joined": false, "member": {…}, "members": [owner, 小验], "channel": {…}}      ← 不报错、不产生重复行
$ 频道不存在
POST /api/channels/ch_missing/members                                    → 404 {"error":{"code":"not_found"}}
$ member_id 是 owner
{"error":{"code":"validation_failed","message":"加入频道校验未通过，未加入任何成员",
          "fields":{"member_id":"初始成员只能是 Agent：owner"}}}          ← 与 create_channel 同口径
$ member_id 不存在
{"error":{"code":"validation_failed",…,"fields":{"member_id":"成员不存在或已删除：mem_ghost"}}}
$ body 缺 member_id
{"error":{"code":"validation_failed",…,"fields":{"member_id":"成员标识不能为空"}}}
$ 校验失败后成员表
GET .../members → ["Owner"]                                              ← 整体不落库，无半成品行
$ 消息流
GET .../messages → []                                                    ← 显式加入不写事件消息（裁定 1）
$ 移出（本票不做）
DELETE .../members/{agent} → 404 "频道成员只能加入、不能移出：人类面不存在该端点"
```

**状态码口径**：成功一律 **200**（不是 201），与「确认一次远端数据流告知」这个同样幂等的动作
同形 —— 「新增」还是「本来就在」由载荷里的 `joined` 机械区分，不用状态码二次编码。

### 3.2 加入后成员表、唤醒面与 `channel_read`

```text
$ pytest app/tests/test_channel_join.py -q
...............                                                          [100%]   （15 passed）

$ pytest app/tests/test_channel_join.py app/tests/test_mention_completion.py \
         app/tests/test_bounded_relay.py
.................................................                        [100%]   （49 passed）
```

逐条对应见 §6 的表格。关键外部读数是 `harness["seam"].calls_for(agent_id)`：它记录该成员
**收到了哪些 seq**，于是「被唤醒且看到那条消息」「历史不重复投递」都能直接断言；
另外可用「穿透回复会额外落一条 `system` 事件消息」来区分成员路径与穿透路径。

### 3.3 加入后 `@该 agent` 命中并与前端链路一致

真实浏览器手跑（§4）里，composer 补全插入的 `@小验 ` 经真实 HTTP 面发送后：

```text
seq 1 human '@小验 这条走频道成员路径' mention_snapshot= [{'member_id': 'mem_5245ca0a4b1c',
                                            'display_name': '小验', 'start': 0, 'end': 3}]
seq 2 agent '收到（1）'                  mention_snapshot= []
members= [('owner','Owner'), ('mem_5245ca0a4b1c','小验')]
```

`mention_snapshot` 命中该 Agent；它经**频道成员路径**被唤醒并回复（seq 2）；消息流里
**没有** system 事件消息（对比：穿透加入会多一条）。

### 3.4 持久化（跨重启）

```text
--- before restart:
channel ch_26ab2f42d90a last_seq 2
members [('owner','Owner'), ('mem_5245ca0a4b1c','小验')]
--- after restart:            （杀掉 uvicorn 进程、在同一数据目录上重启）
members [('owner','Owner'), ('mem_5245ca0a4b1c','小验')]        ← 成员关系来自磁盘
re-join joined= False                                          ← 重启后仍机械判为「本来就在」
```

### 3.5 `channel_read` 就绪的口径（**一处如实说明**）

票面 Acceptance 写「不会把加入前的历史消息算成未读的重复投递；口径与 `db.add_channel_member`
既有行为一致」。这两半在本仓是**两件事**，本文件按事实分别陈述：

- **口径一致**：本票**原样复用** `db.add_channel_member`（票面 Constraints 明写「直接复用…
  不要另写 INSERT 路径，也不要改它的语义」）。而该写点在新增时给 `channel_read` 插入的
  `last_read_seq` 是 **0**——与「穿透加入」的起点逐字相同。因此**加入后的第一次唤醒会读到该频道
  既有的历史**，这不是本票引入的行为，也不是本票有权修改的语义。
- **不重复投递**：一轮收口后游标推进（`orchestration.py` 的 `mark_read`，既有规则），
  因此那些历史**只被投递一次**。这一条是本票测试真正钉住的：同一成员的第二轮
  `calls[1]["seqs"] == [新消息 seq]`，历史没有再次出现。

对应用例 `test_the_channel_read_row_is_ready_so_history_is_never_redelivered` 的注释里写明了
这一段，避免读者把「起点 0」误读成缺陷或把「不重复」误读成「不读历史」。

### 3.6 反向证据（证明测试真的在断言成员关系）

`test_an_outsider_never_appears_in_the_members_table`：没加入过的 Agent **不在**成员表里，
且它仍在工作区 Agent 名单里；并列断言同一个 Agent 经此端点加入后**会**出现
（否则「不在」是恒真的）。

**红证据**：把本票的测试文件拷到**隔离的基线 worktree**（`git worktree add /tmp/wsps-red-14 2b8615c`）
上跑：

```text
$ cd /tmp/wsps-red-14 && .venv/bin/python -m pytest app/tests/test_channel_join.py -q
15 failed in 0.42s          ← 全部判红（写入前 12 条，强化断言后 15 条全红）
```

在主 worktree 上是 `15 passed`。隔离副本用完即以 `git worktree remove --force` 删除，
主工作树全程未被该取证改动。

## 4. 真实 Chromium 手跑取证（前端链路）

数据目录 `/tmp/wsps14-data`（**不是** `~/Worksplice`），后端是真实 uvicorn 进程，浏览器是真实
Chromium（ego-browser）。Agent 行为由脚本化替身固定 ⇒ **零付费模型调用、零凭证**。

```text
$ WORKSPLICE 后端（脚本化替身 + 内联调度器，回复立刻可见）
  /Users/apple/orca/alibot/.venv/bin/python /tmp/wsps14-server.py /tmp/wsps14-data 8791
$ curl -s http://127.0.0.1:8791/api/health
{"status":"ok","member_id":"owner","data_dir":"/tmp/wsps14-data"}
```

浏览器侧用 `ego-browser nodejs` 驱动（仓库 `AGENTS.md` 的 BROWSER 一节：优先 ego-browser）。

### 4.1 从 UI 完成完整链路

```text
新建频道「加入验收」 → 打开右栏「频道成员」面板（此时无任何 Agent）
  dockTitle = "频道成员"
  groups = [已加入（0）: "这个频道还没有 Agent 成员：从下面「未加入」里点「加入」即可。",
            未加入本频道（0）: "还没有 Agent：用左上角「Agent」按钮创建一个，再回到这里点「加入」。"]
  → 截图 /tmp/wsps14-evidence/01-empty-dock.png

新建 Agent「小验」（显式选本机 provider，避免远端确认步骤）
  toast = "已创建 Agent 小验，在右侧「频道成员」面板的「未加入」一段点「加入」即可让它进入当前频道。"
  左栏 group-label = ["频道", "本机 Agent"]        ← 文案不再与「频道成员」冲突
  railAgents = ["小验"]
  groups    = [已加入（0）: [],
               未加入本频道（1）: [{name:"小验", outside:true, joinBtn:"加入"}]]

【加入前】composer 输入 @
  { pickerHidden: false, optionCount: 0,
    empty: "该频道还没有 Agent 成员：在右侧「频道成员」面板的「未加入」一段点「加入」，或在新建 Agent 后到那里加入。" }
  ← 加入前 @ 清单选项数 = 0
  → 截图 /tmp/wsps14-evidence/02-before-join.png

【点击面板里的「加入」】
  posts = [{url:"/api/channels/ch_26ab2f42d90a/members", body:'{"member_id":"mem_5245ca0a4b1c"}'}]
  toast = "已把 小验 加入 #加入验收，现在可以在 composer 里用 @ 提到它。"
  groups = [已加入（1）: [{name:"小验", outside:false}], 未加入本频道（0）: []]

【加入后】不刷新页面，重开 @ 清单
  pageLoads = 1                    ← 页面只加载过一次，**没有跳变或 reload**
  { pickerHidden: false, optionCount: 1, options: ["小小验"], activeName: "小小验" }
  ← 加入后 @ 清单选项数 = 1        【加入前 0 → 加入后 1】
  → 截图 /tmp/wsps14-evidence/03-after-join-mention.png

【键盘选中 + 发送】
  after select: { value: "@小验 ", caret: 4, pickerHidden: true }    ← Enter 选中而非发送
  sent body: "@小验 这条走频道成员路径"
  消息流 = [ {kind: msg, text: "@小验 这条走频道成员路径", mentionCount: 1},
             {kind: msg, text: "收到（1）",                  mentionCount: 0} ]
  → 截图 /tmp/wsps14-evidence/04-sent-mention.png（@小验 有既有 --accent-soft 底高亮）
```

后端读数（§3.3）与浏览器读数一致：`mention_snapshot` 命中该 Agent、它经频道成员路径回复。

### 4.2 面板名列其实（截图人工核对）

`/tmp/wsps14-evidence/04-sent-mention.png`：右栏标题「频道成员」，下分
**已加入（1）**（卡片：头像 + `小验` + 角色胶囊 `前端工程师` + 状态点 + `ollama:qwen2.5` + `空闲`）
与 **未加入本频道（0）**（文案「本工作区的 Agent 都已在这个频道里。」）。左栏是
「频道 / 本机 AGENT」两段 —— 两个名单的范围在视觉上不再混淆。

最终面板读数：`{title: "频道成员", groups: ["已加入（1）", "未加入本频道（0）"]}`
→ 截图 `/tmp/wsps14-evidence/05-final-dock-and-message.png`

### 4.3 视觉与布局契约

色值自查（新组件全部消费既有 token，无硬编码）：

```text
$ grep -nE "#[0-9a-fA-F]{3,8}\b|rgb\(" app/worksplice/studio/app.css
（无输出）

浏览器实测新组件的 computed style：
  .dock-group-label      color: oklch(0.52 0.024 300)      ← 即 var(--faint)
  .dock-member.is-outside 边框：虚线（border-style: dashed，不新造色位）
  .dock-member-foot .btn   transitionDuration: 0s
```

三栏契约与断点（CDP `Emulation.setDeviceMetricsOverride` 实测宽度）：

```text
1400px: { rail: 252, dock: 380, appDirection: "row"     }   ← 契约值 252 / 380
1080px: { rail: 252, dock: 340, appDirection: "row"     }   ← 契约值 ≤1080px → 340
1000px: { rail: 252, dock: 340, appDirection: "row"     }
 900px: { rail: 900, dock: 900, appDirection: "column"  }   ← 契约值 ≤900px → 列
 760px: { rail: 760, dock: 760, appDirection: "column"  }
```

`prefers-reduced-motion: reduce`：本票新增的 CSS **不含**任何 `animation` 与新增媒体查询
（只用了 `.btn` 既有的 `transition`，实测 `transitionDuration: 0s`），因此既有
`@media (prefers-reduced-motion: reduce)` 契约不受影响；既有动画位于未改动的规则里。
简体中文：新增文案全为简体中文。

`worksplice-design-system/` 只读参照（`git diff --stat 2b8615c...HEAD` 里不含该目录），
原型 `worksplice-app.html` 未修改。

## 5. 提问与裁定（走 `orca orchestration ask`，不自行拍板）

| # | 问题 | 裁定 | 对实现的影响 |
| --- | --- | --- | --- |
| Q1 | 显式加入要不要也写一条 `system` 事件消息（与穿透加入对称）？ | **A（人定）**：不写。显式加入是人的显式动作，人自己知道；票面只要求复用 `add_channel_member` + 返回成员表。写事件会占用 seq 并改变消息流，其真实动机只属于穿透（agent 自己不会解释的变化）。**注意：不写事件 ≠ 不写 `channel_read`。** | 端点只改成员关系；测试断言消息流与 seq 完全不动（`test_joining_leaves_no_message_behind`） |
| Q2 | 未加入的 Agent 呈现在哪：面板内分两段 (a)，还是别处给入口 (b)？ | **A（人定）**：选 **(a)** 面板内分「已加入 / 未加入」两段，未加入行给「加入」按钮。人类已看过并认可这个版面。 | `renderDock()` 分两段渲染；未加入段给 `[data-join]` 控件 |
| Q3 | 允许小幅调整与「频道成员」语义冲突的既有文案吗？ | **A（代答，spec Constraints 已授权）**：允许，**结构不变、只动文案**；左栏数据来源不动。改 mention 空态提示时保留 `data-od-id="mention-picker-empty"` 钩子（#9 取证依赖它）。 | 左栏 group-label →「本机 Agent」；mention 空态与建 Agent 提示指向面板「加入」入口；钩子保留 |
| Q4 | 要不要加 SSE 成员变更事件让其他标签页同步？ | **A（人定）**：不加。只要求发起方加入后不刷新页面即可选到它（靠重读成员表）；其他标签页下次切频道 / composer 重新聚焦时自然重读。**补充纪律**：加入后要先把该频道成员表缓存覆盖再触发补全重判，否则会读到旧缓存。 | `joinChannelMember()` 用响应里的 `members` 覆盖 `state.channelMembers[channelId]`，再 `updateMentionPicker()` |
| Q5 | 运行时发现：验收要求覆盖「member_id **已删除** → validation_failed」，但本仓 HTTP 面没有任何删除端点，该分支在 HTTP 面不可达；而 Constraints 又禁止测试连库/读表结构。 | **A（人定）**：**保代码，不测不可达分支**。代码保留该判据（供将来加删除端点时直接生效）；不引入直连数据库的准备夹具（那会破坏本仓「只走 HTTP 面」的先例）；pytest 只覆盖 HTTP 可达的失败形态（不存在 / owner / 空 id）。**缺口必须在验收文档里明写**。 | 见 §6 的缺口说明；`service.add_channel_member` 保留 `deleted_at` 判据 |

## 6. pytest 覆盖（`app/tests/test_channel_join.py`，15 条）

只经 HTTP 面与事件流断言外部可观察行为：不 import 实现内部模块、不连数据库、不读表结构
（`conftest.py` 顶部约定）。成员关系从 `GET /channels/{id}/members` 与「它收到了哪些 seq」
两条外部事实读出。

| 用例 | 断言 |
| --- | --- |
| `test_join_adds_the_agent_and_returns_the_fresh_member_table` | 加入前它不在频道；加入后 `joined is True`、响应自带最新成员表、独立再读一次也一致、频道表没多出条目 |
| `test_join_is_idempotent_and_the_payload_distinguishes_new_from_already_in` | 首次 `joined is True`、再次 `joined is False`；成员表里它恰好出现一次（不重复行） |
| `test_a_repeated_join_does_not_widen_the_wake_surface` | 先钉住加入确实发生（成员表含它），再重复加入三次；一条提及只唤醒它一次（`calls == 1`） |
| `test_missing_channel_is_404` | `POST` 到不存在的频道 → 404 `not_found` |
| `test_unknown_member_is_rejected_and_nothing_changes` | 不存在成员 → 400 `validation_failed` + `fields.member_id`；成员表与消息流无残留 |
| `test_owner_cannot_join_through_this_endpoint` | owner → `validation_failed`，文案含「只能是 Agent」（与 `create_channel` 同口径）；成员表未被改写 |
| `test_join_without_a_member_id_is_rejected` | 空 `member_id` → `validation_failed` + `fields.member_id` |
| `test_a_joined_agent_is_woken_through_the_channel_member_path` | 加入后 `@它`：快照命中、`calls[0]["seqs"] == [1]`、`mentioned is True`、它回复了、**没有** system 事件消息 |
| `test_joining_replaces_the_penetration_path_for_mentions` | 对照面：加入前点名靠穿透唤醒且**不加入**；加入后走成员路径（消息种类恰为 `human, human, agent`，无 system） |
| `test_the_channel_read_row_is_ready_so_history_is_never_redelivered` | 起点 0（既有写点语义，历史与这条一起被读到）→ 第二轮只看到新消息（`calls[1]["seqs"] == [新 seq]`） |
| `test_joining_leaves_no_message_behind` | 显式加入**不写**事件消息：`last_seq` 不变、消息种类仍只有 `human` |
| `test_membership_survives_a_restart` | 换进程读同一数据目录：成员表与频道表一致；重启后 `joined is False`（幂等的来源是持久化） |
| `test_an_outsider_never_appears_in_the_members_table` | **反向证据**：未加入者不在成员表、仍在工作区名单；同一个 Agent 加入后**会**出现 |
| `test_a_deleted_member_is_refused_by_a_later_backend` | 被拒的加入在**另一个后端**读同一目录时也看不到残留（跨进程「不落库」取证） |
| `test_channel_members_cannot_be_removed_through_the_human_face` | 人类面没有移出端点：`DELETE` → 404，成员表不变 |

### 缺口（如实列出，不静默省略）

`member_id` **已删除** → `validation_failed` 这一分支**已实现但无 HTTP 可达路径**：首版没有
删除成员/身份的端点（`DELETE /api/agents/{id}` 明确回 404，也没有别的删成员路径），因此测试
无法经 HTTP 面构造出「已删除成员」。按 Q5 的人类裁定，**不为它引入直连数据库的准备性夹具**
（那会破坏本仓「测试只走 HTTP 面」的硬约定，后续票会拿它当先例）。代码里保留该判据
（与 `create_channel` 的 `member_ids` 校验同口径），供将来加删除端点时直接生效。

## 7. 双轴 code-review（Standards + Spec）

按前置 skill `implement` 的流程，收尾前执行 `code-review`（Standards + Spec 两轴，并行子代理）。
两轴的结论、findings 清单与逐条处理结果见 issue #14 的 Answer（每条标明「已修 / 不修 + 理由」）。

## 8. ADR 判定（按 `docs/agents/domain.md`）

**不新增 ADR。** 理由：

- `docs/agents/domain.md` 写明 ADR「lazily when terms or decisions actually get resolved」，
  只要求「**架构级**决断」。本票的决断是「显式加入是一个人类面端点，且只改成员关系、
  不写事件消息」——它在既有架构里落位明确：仍然是「人类面 HTTP 端点 + 复用既有写点」，
  没有引入新的机制、新的边界或新的持久化结构（`channel_member` / `channel_read` 表与写点都不变）。
- 与之最接近的既有架构边界（人类面 / 成员面分离、穿透的语义）本票**没有修改**，只是新增了
  一个不违反它们的动作；`CONTEXT.md` 新增的 **加入频道 (Channel Join)** 词条已经承载了
  「显式动作 vs 穿透副作用」这一区分，那正是该区分应当被记录的地方。
- 仓库目前**没有** `docs/adr/` 目录（`ls docs` 只有 `agents/`），既有 ADR 编号（ADR-0003/0004/
  0013/0014/0015/0016）都只在 `CONTEXT.md` 与设计系统文档里被**引用**。本票不新建这个目录——
  新建目录本身就是一条应当先有裁定的仓库结构变更。

## 9. 交付物

- worktree 干净（`git status --porcelain` 为空）
- 4 个提交（按逻辑单元：后端端点 / 前端入口 / 文档对齐 / 测试；commit message 为仓库既有的
  Conventional Commits 风格，正文简体中文）
- 分支已推送、PR 已创建（body 正本 `app/specs/issue-14-pr-body.md`），PR 号与 body 路径回填
  issue #14 的 Answer
