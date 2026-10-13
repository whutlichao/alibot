# 实施票 ① 验收取证（issue #2）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。
issue 上的 Answer 摘要指向本文件。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-2-channel-http`（Orca 独立顶层 worktree）
- 采集时间：2026-10-13
- 环境：macOS，Python 3.13（`uv venv`），真实 Chromium（ego-browser）驱动

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：
读票 → 声明票标题 → **先核对 stable 版本** → 按 `tdd` 逐条把契约写成 HTTP 黑盒测试 →
定期跑类型检查与单文件测试 → 收尾跑全量 → `code-review` 双轴 → 提交到当前分支。

票标题（开工时用 `gh issue view 2` 核对）：

> 实施票 ①：从空数据新建频道、发送并持久化频道消息、前端呈现、每频道已读位置与未读徽标

## 1. AgentScope stable 版本核对（2026-10-13）

```text
$ curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" https://docs.agentscope.io/stable/en
308 -> https://docs.agentscope.io/en/versions/2.0.9/

$ curl -s https://pypi.org/pypi/agentscope/json | jq -r '.info.version'
2.0.10          # 发布 2026-10-12（2.0.9 为 2026-09-28）

$ curl -s -o /dev/null -w "%{http_code}\n" https://docs.agentscope.io/en/versions/2.0.10/index
404             # stable 尚未指向 2.0.10

$ curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" https://docs.agentscope.io/latest/en/index
308 -> https://docs.agentscope.io/en/versions/2.0.10dev   # 开发版，按纪律不用
```

**结论**：stable 文档 = **2.0.9**；PyPI 最新 2.0.10。建议 #② pin `agentscope==2.0.9`（文档与包对得上）。
本票零 `agentscope` import、零依赖声明：

```text
$ grep -rn "agentscope" app/ | wc -l
0
$ grep -c "agentscope" app/pyproject.toml
0
```

## 2. 门禁结果（全量）

```text
$ .venv/bin/python -m pytest app/tests -q
32 passed

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 12 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 11 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
errors 0 files 11
```

## 3. 红 → 绿证据（红证据在隔离副本取得）

做法：`git archive HEAD | tar -x -C /tmp/<隔离目录>`，在副本里注入基线缺陷，跑同一条测试观察变红；
仓库工作树未被改动（`git status --short` 在取证前后一致）。

| 轮 | 注入的基线缺陷 | 变红的用例 |
| --- | --- | --- |
| 1 | 去掉 `message_no_update` / `message_no_delete` 触发器 | `test_messages_are_append_only` |
| 1 | `mark_read` 不裁剪到当前最大 seq | `test_mark_read_is_clamped_to_current_max_seq` |
| 1 | `mark_read` 不取 max（已读位置可回退） | `test_read_position_is_monotonic_and_never_rewinds` |
| 2 | 校验成员前先写 `channel` 行（留半成品） | `test_unknown_initial_member_is_rejected_without_creating`、`test_owner_cannot_be_listed_as_initial_member` |
| 3 | 未读计数把 `system` 也算进去 | `test_unread_counts_only_human_and_agent_top_level_messages` |

第 3 轮**先暴露了测试自身的空转**：`raw_connection` 不提交，注入的 system 行从未落库，于是基线改动没能让它变红。
修好 `raw_connection`（正常结束才 commit）后同一基线改动**正确判红**（`assert 1 == 0`），随后恢复为绿。
这条修正单独提交（`5411868`）。

配置侧也留了红→绿：

```text
$ (cd app && ~/.pi-lens/pip-tools/bin/mypy ... worksplice/__main__.py)   # 旧配置 python_executable=.venv/bin/python
mypy: Invalid python executable '.venv/bin/python': No such file or directory

$ (cd app && ~/.pi-lens/pip-tools/bin/mypy ... worksplice/__main__.py)   # 现配置
exit=0
```

原因：`python_executable` 按 **cwd** 而不是按配置文件位置解析，`.venv/bin/python` 在仓库根可用、在 `app/` 下失效。

## 4. 以独立临时数据目录从空数据实跑（后端 + 浏览器）

### 4.1 启动

```text
$ .venv/bin/python -m worksplice --port 8788 --data-dir /tmp/alibot-ui-accept/data6
worksplice 工作台： http://127.0.0.1:8788/studio/
数据目录（备份即复制该目录）： /tmp/alibot-ui-accept/data6

$ lsof -nP -iTCP:8788 -sTCP:LISTEN
Python  ...  TCP 127.0.0.1:8788 (LISTEN)      # 只绑 loopback
```

### 4.2 空数据 = 空工作台

```text
$ curl -s http://127.0.0.1:8788/api/workspace
channels 0 members [ 'owner' ] unread_total 0
```

浏览器（空态）：

```json
{ "railW": "252px", "streamMax": "880px", "dockW": "380px", "dockHidden": true,
  "channelRows": 0, "channelListEmpty": true, "memberListEmpty": true,
  "chanHeadHidden": true, "composerHidden": true,
  "localStorageBusinessKeys": [] }
```

侧栏是空态、没有 `AGENTS`/`MESSAGES`/`LOGS`/`RUNTIME`/`TASKS` 种子；
`localStorage` 里只有 `worksplice.activeChannel`（上次打开的频道 id），**没有业务正本**。

### 4.3 新建频道（名称 + 描述 + 初始成员；原子校验）

弹窗：

```json
{ "title": "新建频道",
  "memberPicker": "还没有可勾选的 Agent：Agent 的创建入口在下一票交付，交付后这里自动可用。",
  "pickerIsEmptyState": true, "pickerControlExists": true }
```

空名称提交（**整体不创建**）：

```json
{ "errorShown": true, "errorText": "频道校验未通过，未创建任何数据：频道名称不能为空",
  "overlayStillOpen": true, "channelRows": 0 }
```

建后立刻能发第一条（无需刷新/重启）；空频道空态文案：

```json
{ "channelRows": 1, "chanTitle": "设计系统重构", "composerPlaceholder": "发消息到 #设计系统重构",
  "emptyNoMessage": "该频道还没有消息 这里的消息不可编辑、不可删除，是永久记录。", "sendDisabled": true }
```

### 4.4 发送、乐观显示、多行与键盘

多行（`Shift+Enter` 换行、`Enter` 发送）：

```text
COMPOSER_VALUE_BEFORE_SEND "第一行\n第二行"    # Shift+Enter 真的换行而不是发送
```

乐观 pending（给页面注入 900ms 网络延迟后，在 Keydown 处理函数返回的同一 tick 内观察）：

```json
{ "rtt": 921,
  "immediate": { "pendingCount": 1, "className": "msg pending",
                 "text": "延迟下观察 pending", "hasSeqAttr": null,
                 "pendingAttr": "pending-1791880566005-5bwns",
                 "markedAsSent": false, "composerCleared": true } }
```

→ 本地先出现 pending（无服务端 `seq`、显式 `pending` 标记、**不**呈现为已发送），
后端确认后替换为带服务端 `seq` 的正式消息：

```text
CONFIRMED { "count": 3, "seqs": ["1","2","3"], "pendingLeft": 0 }
```

后端不可达：发送被禁止 + 明确提示，**不做本地队列**：

```json
{ "sendButtonWasEnabled": false, "failedCount": 0,
  "banner": "后端不可达：当前显示的是最后一次成功读到的内容，发送已被禁止。",
  "toast": "后端不可达，发送已禁止；消息没有被送出。",
  "localStorageBusinessKeys": [] }
```

后端明确拒绝（正文超上限）→ 不得停留在"看起来已发送"：

```json
{ "className": "msg failed", "hasPendingClass": false, "hasFailedClass": true,
  "looksSent": false, "badge": "发送失败", "hasSeq": null,
  "retryHint": "未送达：消息正文最多 8000 个字符",
  "serverState": { "seqs": [1,2,3] } }
```

### 4.5 每频道已读位置与未读徽标

（隔离环境，两个频道 甲/乙，真实 human 消息取证）

```text
AFTER_SENDING_IN_ACTIVE_CHANNEL   甲 active → 无徽标
AFTER_CREATING_SECOND_CHANNEL     甲 inactive → 无徽标，乙 active
AFTER_SENDING_IN_SECOND           乙 active   → 无徽标
AFTER_SWITCH_TO_A                 甲 active   → 无徽标，乙 inactive 无徽标
AFTER_ARRIVAL_WHILE_VIEWING       甲 active   → 无徽标（查看期间到达的也立即已读）
OTHER_CHANNEL_BADGE               甲 inactive → 徽标 "1"，乙 active → 无徽标
SERVER_READ_STATE   甲 last_seq=4 last_read=3 unread=1 ；乙 last_seq=1 last_read=1 unread=0
```

徽标呈现（沿用 `--unread` 角色族）：

```json
{ "badgeText": "2", "badgeBg": "oklch(0.91 0.1 94.8)",   // = --unread 的 base 档
  "badgeColor": "oklch(0.33 0.045 300)",                  // = --on-accent 墨色
  "badgeTabular": "tabular-nums", "badgeRadius": "99px" }
```

token 解析（三个产品角色族由 `studio/product-tokens.css` 声明，见 §5）：

```json
{ "unread": "oklch(91% 0.100 94.8)", "unread-deep": "oklch(51.8% 0.100 94.5)",
  "selected": "oklch(84.9% 0.081 299.9)", "warn": "oklch(86.3% 0.084 52.2)" }
```

浏览器刷新 + **后端进程重启**后一致：

```text
SERVER_BEFORE_RESTART 甲 {last:2, read:2, unread:0} 乙 {last:0, read:0, unread:0}
(kill 后端 → 同数据目录重启)
SERVER_AFTER_RESTART  甲 {last:2, read:2, unread:0} 乙 {last:0, read:0, unread:0}
数据目录：worksplice.db / -shm / -wal（单文件库 + 伴随文件，复制即可备份）
AFTER_BACKEND_RESTART_IN_BROWSER 消息 seq [1,2]、正文原样、刷新后仍一致
```

未读口径：只算 `human`/`agent` 顶层消息，不计 `system`（见 §3 第 3 轮红证据；
本票不写入任何 system 消息，`test_unread_counts_only_human_and_agent_top_level_messages` 以库内注入的 system 行取证）。

### 4.6 布局与视觉契约

| 视口 | `flex-direction` | rail | dock | 消息流 |
| --- | --- | --- | --- | --- |
| 1440×900 | row | 252px | 380px | 880px |
| 1080×900 | row | 252px | **340px** | 880px |
| 900×900 | **column** | 100% | 100% | 880px |

三栏底色按契约：rail `--panel` + 右发丝线（`1px oklch(0.9 0.013 86)`）、main `--bg`、dock `--surface`。

消息正文局部例外（ADR-0016）：

```json
{ "fontSize": "14px", "lineHeight": "23.1px(1.65)", "maxWidth": "599.648px",
  "ch68Px": 599.648, "match68ch": true }
```

方形头像 `border-radius: 7px`（不是圆形）；`--av-0…--av-4` 确定性色位。

## 5. 设计系统消费方式（只读，未改动该目录）

`worksplice-design-system/` 全程只读：本票未编辑其中任何文件。

发现并处理的一件事：`--unread*` / `--selected*` / `--warn*` 三个产品角色族在
`colors_and_type.css` 与 `tokens.css` **两个 foundation 里都没有定义**。设计系统自己的文档写明了归属：

```text
SKILL.md:31  「Product role families. If you are building *inside the worksplice app* (not a
              standalone surface), three more families come from the product extension layer
              (`app/globals.css`): `--selected*` … `--unread*` … `--warn*` …」
README.md:71 「colors_and_type.css（+ the product role families in the app layer）」
ui_kits/app/app.css 头部:「whose definitions live in the product extension layer (`app/globals.css`),
              not in the two foundations above. Dropping this kit into a product therefore means
              loading that layer too (or declaring the three role families yourself).」
```

本票新增的工作台就是那个「inside the worksplice app」的消费方，因此新增
`app/worksplice/studio/product-tokens.css` 承担这个扩展层：**逐字照抄原型 `worksplice-app.html`
`:root` 的值**（不改名、不新造色位、不改档位语义），加载顺序 =
foundation → 产品扩展层 → 组件层。

## 6. 未纳入本票（规格 Out of Scope，本票未实现）

Agent 与唤醒、事件流 / SSE、@提及解析与补全、任务 / 线程、DM、搜索、通知 / 提醒、
reactions / pin / 附件、多用户与鉴权、云端 / 多租户 / 同步、加密、Agent 本地能力（文件 / shell）、
token 级打字机流式、AgentScope 高级形态（Agent Service / A2A / Agent Team）、成本与可观测页面。

本票也**不写入任何 `system` 消息**，不创建 Agent，不提供消息编辑 / 删除端点。
