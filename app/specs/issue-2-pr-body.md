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
$ .venv/bin/python -m pytest app/tests -q          →  32 passed
$ .venv/bin/ruff check app && ruff format --check  →  All checks passed! / 13 files already formatted
$ .venv/bin/python -m mypy                          →  Success: no issues found in 11 source files
$ pyright (app/worksplice app/tests)                →  errors 0
```

**红 → 绿（红证据在 `/tmp` 隔离副本取得，工作树未动）**

| 注入的基线缺陷 | 变红的用例 |
| --- | --- |
| 去掉 append-only 触发器 | `test_messages_are_append_only` |
| `mark_read` 不裁剪到当前 max seq | `test_mark_read_is_clamped_to_current_max_seq` |
| `mark_read` 不取 max（可回退） | `test_read_position_is_monotonic_and_never_rewinds` |
| 校验成员前先写 channel 行 | `test_unknown_initial_member_is_rejected_without_creating` |
| 未读计数把 system 也算进去 | `test_unread_counts_only_human_and_agent_top_level_messages` |

第 5 轮**先暴露了测试自身的空转**（`raw_connection` 不提交 ⇒ 注入的 system 行从未落库），修好后同一基线改动正确判红。

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

**code-review 双轴**（Standards + Spec，并行子代理，互不污染上下文）

```text
Standards: 5 项（.toast.err 档位倒置 / nav-row 用错角色族 / .overlay 缺 blur /
                   .brand-name 硬编码字号 / 重复 SQL + 零消费符号）→ 全部处置
Spec:      P1 失败消息跨频道串流 → 已修并浏览器复验
           P2 每频道标读定时器被覆盖 → 已修并浏览器复验
           P2 system 口径取证方式越界 → 注释改为如实说明边界
           P2 composer placeholder 第二分句 → 属 #②，已标待改点
```

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
