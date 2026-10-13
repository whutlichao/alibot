# 实施票 ① 验收取证（issue #2）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。
issue 上的 Answer 摘要指向本文件。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-2-channel-http`（Orca 独立顶层 worktree）
- 采集时间：2026-10-13
- 修订时间：2026-10-13（第一次 G-impl 打回后，见 §2.1；测试数字与红→绿表已按最终有效用例重算）
- 环境：macOS，Python 3.13（`uv venv`），真实 Chromium（ego-browser）驱动
- 有效用例数：**36**（`app/tests/`，全部只经 HTTP 面）

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
36 passed

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 14 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 11 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
errors 0 files 11
```

| 文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `app/tests/test_empty_workspace.py` | 5 | 空工作台、数据目录可复制即备份 |
| `app/tests/test_create_channel.py` | 9 | 新建频道、校验失败整体不创建 |
| `app/tests/test_send_message.py` | 12 | 发送、seq 单调与并发唯一、append-only、重启一致 |
| `app/tests/test_channel_read.py` | 10 | 每频道已读位置、未读计数、跨重启 / 跨实例持久化 |
| 合计 | **36** | |

## 2.1 测试范围修正（第一次 G-impl 打回）

gate 指出两处验收偏差：issue #2 明定「每条测试只断言**外部可观察行为**，不断言内部函数、私有结构」
与「本票以**真实 human 消息**取证，不为测试增造 system 消息」。原测试违反这两条（直连 SQLite、
注入 `kind='system'` 行），而 Answer 却称 32 条均黑盒、未造 system —— 证据不自洽。
本次只改测试与证据文档，**未改任何生产行为**（`git diff --stat` 只触及 `app/tests/*`）。

| 原用例 | 原取证方式 | 处置 | 替代的 HTTP 行为证据 |
| --- | --- | --- | --- |
| `test_target_seq_is_unique_at_the_storage_layer` | 直连库插入重号，断言 `sqlite3.IntegrityError` | 删除 | `test_concurrent_sends_never_share_or_skip_a_seq`（8 个后端同目录并发发 8 条，断言 8 个 201 且 seq 互不相同、合起来恰为 1…8） |
| `test_messages_are_append_only`（含库级断言） | 库内 `UPDATE`/`DELETE` 断言触发器报错 | 保留 HTTP 部分 | HTTP 面 `GET/PUT/PATCH/DELETE` 全 404；走完这一圈后频道内正文逐字未变；另加 `test_rejected_message_does_not_consume_a_seq`（超长正文被 400 拒后，后续 seq 无缺口） |
| `test_unread_counts_only_human_and_agent_top_level_messages` | 向库注入 `kind='system'` 行 | **删除** | 本票不产生 system / agent 消息，该口径**明确未覆盖**（测试文件头如实写明，留 #②/#③ 用真实消息验证）；human 侧口径由 `test_history_read_before_the_read_position_is_never_unread_again` 覆盖 |
| `test_channel_read_row_exists_per_channel` | 直读 `channel_read` 表行 | 删除 | `test_read_position_is_persisted_outside_the_process`（换后端进程读同一数据目录，已读位置与未读计数一致，并在新进程上继续推进） |
| `test_empty_name_is_rejected_and_leaves_no_half_written_row`（含三表 `COUNT(*)`） | 直查 `channel`/`channel_member`/`channel_read` | 保留 HTTP 部分 | 列表 + 工作台快照在 HTTP 面零残留；`test_rejected_create_leaves_nothing_for_a_later_backend` 再用第二个后端进程视角复验（并验证后续创建仍从 seq 0 起） |
| `test_data_dir_is_created_in_the_given_location` | 断言 `data_dir/worksplice.db` 存在（依赖内部文件名常量） | 改写 | 只经 `/api/health` 的 `data_dir` 断言目录位置；新增 `test_backup_is_a_directory_copy`（复制目录 → 在新地址重开后端，内容逐字一致） |
| `test_history_survives_backend_restart` | 混用 `channels()` 与重启 | 保留 | 新增 `test_history_survives_a_second_backend_on_the_same_data_dir`（独立后端进程视角） |

`conftest.py` 的 `raw_connection` 夹具与 `sqlite3` / `DATABASE_FILE_NAME` / `OWNER_ID` 的实现模块 import 一并移除，
改为两个 HTTP 启动器：

```python
@contextmanager
def backend(data_dir): ...  # 进 with = 启动，出 with = 正常收尾；同目录重复进入 = 重启
@contextmanager
def peer_backends(data_dir, count): ...  # 同一数据目录上同时开 N 个后端 = N 个并发客户端
```

现状自检（零命中，退出码 1）：

```text
$ grep -rnE "sqlite3|raw_connection|INSERT INTO|SELECT |worksplice\.db|from worksplice import|import worksplice" app/tests/*.py
exit=1        # 无命中

$ grep -rniE "kind\s*=\s*['\"](system|agent)|'system'|\"system\"" app/tests/*.py
exit=1        # 无命中：测试文件里没有任何 system/agent 消息字符串，
              # 只有说明「本票不写入任何 system 消息」的中文注释
```

**本次 diff 的聚焦自审**（本票影响面仅为测试与证据，按票面免完整双轴）：

- **会不会把实现缺陷写成“不可测”？** 不会：并发 seq 唯一、append-only、原子性、已读持久化四项各有一组
  独立注入能判红（§3 A / F / H / D、K），测试仍有真实判别力，只是判别只经 HTTP 面。
- **有没有引入不稳定断言？** 并发用例是唯一涉及时序的：注入侧 10/10 判红、绿侧 15/15 全绿；
  `peer_backends` 的并发点不多不少就是 8 个并发写，不靠 sleep 或重试。
- **有没有把内部结构写成断言？** 特意留下两处“实现事实”文字说明（`UNIQUE(target_id, seq)`、模块 doc），
  但它们只出现在 docstring / 文档里，不作为断言依据；断言只读 HTTP 响应。
- **数字是否一致？** `app/tests` 实际收集 36 条（本文件 §2 表、PR body 与 #2 更正评论三处一致）。

## 3. 红 → 绿证据（红证据在隔离副本取得）

做法：`git archive HEAD | tar -x -C /tmp/<隔离目录>`，在副本里注入基线缺陷，跑同一条测试观察变红；
仓库工作树未被改动（`git status --short` 在取证前后一致）。

每条注入由 `/tmp/red_evidence.py` 批量执行：`git archive HEAD` → 独立副本 → 打补丁 → 跑 `pytest tests`。
11 组注入**全部判红、零组漏网**，且判红用例全部是 HTTP 黑盒用例（没有任何一条依赖直连库）：

| # | 注入的基线缺陷 | 判红用例数 | 判红的用例 |
| --- | --- | --- | --- |
| A | 频道内 seq 在事务外计算（锁外先读 `MAX(seq)`） | 1 | `test_concurrent_sends_never_share_or_skip_a_seq` |
| B | `mark_read` 不裁剪到当前最大 seq | 1 | `test_mark_read_is_clamped_to_current_max_seq` |
| C | `mark_read` 覆盖写而不取 max（可回退） | 1 | `test_read_position_is_monotonic_and_never_rewinds` |
| D | `mark_read` 不把已读位置写进正本 | 6 | `test_read_position_is_persisted_outside_the_process`、`test_unread_survives_browser_and_backend_restart`、`test_mark_read_clears_only_that_channel`、`test_read_position_is_monotonic_and_never_rewinds`、`test_mark_read_is_clamped_to_current_max_seq`、`test_history_read_before_the_read_position_is_never_unread_again` |
| E | 校验失败仍提交频道行（留半成品） | 3 | `test_empty_name_is_rejected_and_leaves_no_half_written_channel`、`test_rejected_create_leaves_nothing_for_a_later_backend`、`test_too_long_name_is_rejected_without_creating` |
| F | 人类面多出删除消息的端点 + 去掉 append-only 触发器 | 1 | `test_messages_are_append_only` |
| G | 消息正文长度校验缺失 | 1 | `test_rejected_message_does_not_consume_a_seq` |
| H | seq 由内存计数器在校验前分配（被拒的发送也占号） | 4 | `test_rejected_message_does_not_consume_a_seq`、`test_concurrent_sends_never_share_or_skip_a_seq`、`test_history_survives_backend_restart`、`test_read_position_is_persisted_outside_the_process` |
| I | 未读计数忽略已读位置 | 6 | 同 D 行的 6 条 |
| J | `after_seq` 游标用 `>=` 而不是 `>` | 1 | `test_after_seq_cursor_returns_only_newer_messages` |
| K | 库改为内存库（数据目录不再是正本） | 5 | `test_history_survives_a_second_backend_on_the_same_data_dir`、`test_read_position_is_persisted_outside_the_process`、`test_backup_is_a_directory_copy`、`test_unread_survives_browser_and_backend_restart`、`test_history_survives_backend_restart` |

注入 A 是全套里唯一依赖时序的一组（并发丢 seq），单独连跑 10 次全部判红（`10/10 FAILED`），
绿侧同一条用例连跑全量 15 次**全绿**（无抖动）：

```text
$ for i in $(seq 1 10); do pytest tests/test_send_message.py::test_concurrent_sends_never_share_or_skip_a_seq; done   # 注入 A 副本
FAILED ×10
$ for i in $(seq 1 15); do pytest app/tests -q; done   # 工作树
36 passed ×15
```

> 前版 §3 的 5 组红证据里有两组依赖直连库断言（重号插入、`UPDATE`/`DELETE` 触发器）与一组
> 依赖库内注入的 `system` 行。这三组连同对应用例已在 §2.1 一并移除，本表是重算后的结果。
> 另：`5411868` 那条「`raw_connection` 不提交导致用例空转」的修正，随 `raw_connection` 本身被删除而失效
> —— 现在没有任何用例需要写库，也就没有这类空转风险。

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

未读口径：只算 `human`/`agent` 顶层消息，不计 `system`。**本票只能产生 human 消息**，因此浏览器与 HTTP 面
实测到的只是 human 侧口径（见 §3 注入 I 的红证据，以及 §2.1 删掉的那条注入 system 行用例）。
`system` 与 `agent` 消息的真实口径**本票未覆盖**，由 #② / #③ 用真实消息验证。

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

## 5. code-review 双轴与处置

按 `implement` 前置的要求，收尾跑了 `code-review` 的双轴（Standards + Spec），两个子代理并行、互不污染上下文。
复核发现后逐条处置（详见提交 `d5e7005`）：

| 轴 | finding | 处置 |
| --- | --- | --- |
| Spec | **P1** 失败消息跨频道串流：`renderedMessages()` 无条件并入 `state.pending`，而切频道时保留所有 failed 项（不限 `target_id`）→ 在甲被拒的消息会渲染进乙的流 | 改为只渲染 `target_id === 当前频道`；实测 甲1条failed → 乙0条failed 且空态正常，回甲仍可重试 |
| Spec | **P2** 共用标读定时器被覆盖：离开有未读的频道时 `scheduleRead(previous,…)` 被紧随的 `markCurrentRead()` clearTimeout 掉，已读推进被静默丢弃 | 改为每频道各一个定时器（`readTimers: Map`）；实测 甲(未读2) → 60ms 内切乙，甲仍正确推进到 read=2 |
| Standards | **P2** `.toast.err` 用 `--error`（deep 档）当填充 + `--bg` 墨色 —— 档位倒置 | 改回 `--error-fill` + `--on-accent`（`-fill` 只与 `--on-accent` 配对） |
| Standards | **P2** nav-row 激活条用 `--accent`、`#` 用 `--accent-deep`，而契约要求 `--selected-graphic` + `--selected-deep` | 改为 selected 族；否则 `product-tokens.css` 声明的 selected 档位无消费点 |
| Standards | **P2** `.overlay` 缺 `backdrop-filter: blur(2px)` | 补上（DESIGN.md §6 / SKILL.md §4 明列） |
| Standards | **P2** `.brand-name` 硬编码 `13.5px` | 改为 `var(--fs-body-lg)` |
| 两轴 | **P2** 投机泛化：`UNREAD_KINDS` / `UNREAD_BADGE_MIN` / `count_messages` / `GET /api/channels/{id}` 零消费；`list_channels` 与 `get_channel` 各抄一份未读口径 SQL（漂移风险） | 删除零消费项；两处 SQL 合并为模块级单条 `CHANNEL_SELECT_SQL`（无拼接） |
| Spec | **P2** `system` 不计入未读这条口径，issue 明言留待 #②/#③ 用**真实消息**覆盖，原测试注释声称已闭 | **已由 §2.1 进一步处置**：该用例本身（库内注入 system 行）违反 issue 的黑盒契约，已删除；测试文件头如实写明「本票不产生 system/agent 消息，该口径未覆盖，留 #②/#③」 |
| Spec | **P2** composer placeholder 的第二分句（`—— 输入 @ 提及 Agent`）缺失 | 本票没有可被点名的 Agent（提及属 #②），已在代码注释里标为 #② 的待改点 |

两条 P2 属"报告项/可解释偏离"，其余已修。修复后复跑全部门禁（§2）全绿，并在真实浏览器复验 P1 与标读定时器（命令与结果见本节表格）。

> 本节是第一次 G-impl 打回**之前**的双轴结果。打回只涉及测试与证据契约（§2.1），不改生产行为，
> 故未重跑完整双轴；本次 diff 的聚焦自审见 §2.1。

## 6. 设计系统消费方式（只读，未改动该目录）

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

## 7. 未纳入本票（规格 Out of Scope，本票未实现）

Agent 与唤醒、事件流 / SSE、@提及解析与补全、任务 / 线程、DM、搜索、通知 / 提醒、
reactions / pin / 附件、多用户与鉴权、云端 / 多租户 / 同步、加密、Agent 本地能力（文件 / shell）、
token 级打字机流式、AgentScope 高级形态（Agent Service / A2A / Agent Team）、成本与可观测页面。

本票也**不写入任何 `system` 消息**，不创建 Agent，不提供消息编辑 / 删除端点；
因此 `system` 与 `agent` 消息的未读口径在本票**未被测试覆盖**（无真实输入可依据），留 #② / #③。
