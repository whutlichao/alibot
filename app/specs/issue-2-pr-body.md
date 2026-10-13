## Summary

实施票 ①（#2）：从**空数据**走完人类侧第一段闭环 —— 新建频道 → 发送 → 后端持久化 → 前端呈现，
每频道未读徽标始终正确。浏览器只走 HTTP；后端是业务状态的唯一正本。

```text
app/
├── worksplice/
│   ├── db.py        # 单文件 SQLite 正本：append-only 消息 · UNIQUE(target_id,seq) · channel_read
│   ├── service.py   # 协作域应用服务（HTTP 人类面唯一的读写入口）
│   ├── api.py       # HTTP 契约：无消息编辑/删除端点
│   ├── __main__.py  # 默认只绑 127.0.0.1:8788；非 loopback 直接拒绝启动
│   └── studio/      # 浏览器工作台（只消费设计系统 token 名）
└── tests/           # HTTP 黑盒契约测试（每条独立临时数据目录）
```

```text
新建频道（名称+描述+初始成员勾选）
  ├─ 校验失败 → 整体不创建，库中零半成品行
  └─ 通过 → 同一事务分配频道内单调 seq
       └─ 发送消息 → 本地 pending → 服务端 seq 或显式失败（无本地队列）
            └─ 标读（切入频道 / 正查看时到达）→ channel_read.last_read_seq 单调 max
                 └─ 未读 = 已读位置之后 human/agent 顶层消息（不计 system）
```

设计系统只读参照、未改动（`worksplice-design-system/` 逐字未变）。`--unread*` / `--selected*` / `--warn*`
三个**产品角色族**在 foundation 里本就没有定义 —— 设计系统文档指明它们归 product extension layer
（`app/globals.css`），由消费方声明，故新增 `studio/product-tokens.css` 逐字照抄原型的值。

## Evidence

**门禁（全绿）**

```text
$ .venv/bin/python -m pytest app/tests -q          →  36 passed
$ .venv/bin/ruff check app && ruff format --check  →  All checks passed! / 14 files already formatted
$ .venv/bin/python -m mypy                          →  Success: no issues found in 11 source files
$ pyright (app/worksplice app/tests)                →  errors 0
```

**测试只走 HTTP 面（第一次 G-impl 打回后的修正）**

原测试有直连 SQLite（`raw_connection`）、断言 `sqlite3.IntegrityError`、读 `channel_read` 表行与注入
`kind='system'` 行的用例，与 issue #2「只断言外部可观察行为」+「以真实 human 消息取证」冲突。
本次把这批取证方式全部换掉，**未改任何生产行为**（diff 只触及 `app/tests/*` 与证据文档）：

```diff
 app/tests/
-├─ conftest.py            # raw_connection 直连库 + 实现模块 import
+├─ conftest.py            # backend() / peer_backends()：换进程 = 重启，同目录多后端 = 并发客户端
 ├─ test_empty_workspace.py   # 5 → 新增 backup_is_a_directory_copy（复制目录即备份）
 ├─ test_create_channel.py    # 8 → 9：拒建后的残留改由「列表 + 快照 + 后续后端视角」取证
 ├─ test_send_message.py      # 10 → 12：+并发不重号/缺口、+拒发不占号
 └─ test_channel_read.py      # 10 → 10：已读持久化改由「换后端读同一数据目录」取证，-注入 system 行用例
```

```text
$ grep -rnE "sqlite3|raw_connection|INSERT INTO|SELECT |worksplice\.db|from worksplice import" app/tests/*.py
exit=1        # 零命中
$ grep -rniE "kind\s*=\s*['\"](system|agent)|'system'|\"system\"" app/tests/*.py
exit=1        # 零命中：测试不造任何 system/agent 消息
```

**红 → 绿（11 组注入，全部在 `/tmp` 独立副本获得，工作树未动）**

| 注入的基线缺陷 | 判红用例数 |
| --- | --- |
| A 频道内 seq 在事务外计算（锁外读 `MAX(seq)`） | 1（并发唯一性） |
| B `mark_read` 不裁剪到当前 max seq | 1 |
| C `mark_read` 不取 max（可回退） | 1 |
| D `mark_read` 不把已读位置写进正本 | 6 |
| E 校验失败仍提交频道行（留半成品） | 3 |
| F 人类面多出删除端点 + 去掉 append-only 触发器 | 1 |
| G 正文长度校验缺失 | 1 |
| H seq 在校验前分配（被拒的发送也占号） | 4 |
| I 未读计数忽略已读位置 | 6 |
| J `after_seq` 游标用 `>=` | 1 |
| K 库改为内存库（数据目录不再是正本） | 5 |

11/11 组判红，零漏网；不稳定的只有 A（并发时序），单独连跑 10 次全红，绿侧全量连跑 15 次全绿。

**浏览器实跑（隔离数据目录 + 真实后端，真实 Chromium）**

```json
// 空数据 = 空工作台
{ "channelRows": 0, "memberListEmpty": true, "dockHidden": true,
  "localStorageBusinessKeys": [] }

// 乐观 pending：注入 900ms 延迟，在 Keydown 处理函数返回的同一 tick 观察
{ "className": "msg pending", "hasSeqAttr": null, "markedAsSent": false, "composerCleared": true }

// 后端不可达
{ "sendButtonWasEnabled": false,
  "banner": "后端不可达：…发送已被禁止。", "localStorageBusinessKeys": [] }

// 后端明确拒绝（不发）→ 不得「看起来已发送」
{ "className": "msg failed", "looksSent": false, "badge": "发送失败", "serverState": { "seqs": [1,2,3] } }

// 断点：rail 252 / 消息流 880 / dock 380 → 340(≤1080) → 列布局(≤900)
// 消息正文：14px / 1.65 / 恰好 68ch；方形头像 7px

// 未读徽标
AFTER_BROWSER_RELOAD   甲 inactive 徽标"2"，乙 active 无徽标
AFTER_SWITCH_TO_A      甲 active 无徽标，乙 inactive 无徽标
OTHER_CHANNEL_BADGE    甲 inactive 徽标"1"
SERVER_READ_STATE      甲 last_seq=4 read=3 unread=1 ；乙 last_seq=1 read=1 unread=0
（kill 后端 → 同数据目录重启 → 上述状态逐项保持）
```

**code-review 双轴**（Standards + Spec，并行子代理，互不污染上下文；第一次打回前的记录）

```text
Standards: 5 项（.toast.err 档位倒置 / nav-row 用错角色族 / .overlay 缺 blur /
                   .brand-name 硬编码字号 / 重复 SQL + 零消费符号）→ 全部处置
Spec:      P1 失败消息跨频道串流 → 已修并浏览器复验
           P2 每频道标读定时器被覆盖 → 已修并浏览器复验
           P2 system 口径取证方式越界 → 后由 G-impl 打回指出该用例仍违反黑盒契约，已删除
           P2 composer placeholder 第二分句 → 属 #②，已标待改点
```

打回后的修正只动测试与证据（见上节），不改生产行为，故未重跑完整双轴；聚焦自审记录在验收文档 §2.1。

```text
# P1 复验：在甲发一条被拒消息（>8000 字），切到乙
IN_CHANNEL_A_AFTER_REJECT  { "failedCount": 1 }
IN_CHANNEL_B               { "failedCount": 0, "emptyVisible": true }   ← 修前会渲染进乙
BACK_IN_A                  { "failedCount": 1, "hasRetry": true }        ← 仍可重试，不丢

# 标读定时器复验：甲(未读2) → 60ms 内切乙（两次标读落在同一 120ms 窗口）
AFTER  [{"name":"甲","last_seq":2,"read":2,"unread":0}]                  ← 修前甲会停在 read=0
```

逐条验收的完整命令与输出见 `app/specs/issue-2-acceptance.md`。

## Merge Danger

**Door:** two-way

纯新增目录（`app/`）+ 仓库根两份检查器配置 + `.gitignore` 追加。不改任何既有文件的行为，
`worksplice-design-system/` 与 `CONTEXT.md` 逐字未变；回滚 = 删除 `app/` 与两个配置 + revert `.gitignore`。

**Blast Radius:** 局部 —— 全新的应用目录，除本 PR 外无消费方

- 落库格式是**首版**：schema 一旦有真实数据，改列需要迁移。消息表 append-only，不会就地改写。
- 端口 8788 与 `~/Worksplice/` 被占时会启动失败/共用目录；`--port` 与 `--data-dir` 可改。
- 首版**无认证**，默认只绑 loopback（容器/远程环境绑非 loopback 会被拒启动，需显式 `WORKSPLICE_ALLOW_REMOTE=1`）。
- 本票**不含** Agent / 事件流 / @提及 / 任务 / DM / 搜索，也不写 system 消息；AgentScope 零 import（供 #② pin 的版本核对结论见 #2 评论）。

Closes #2
