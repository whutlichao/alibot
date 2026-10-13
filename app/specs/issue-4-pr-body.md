## Summary

把 #3 落下的「预算只记录」改成**真压制**，并闭合穿透、并发合并与未读口径。实施票 ③（issue #4）。

```text
消息落库之后
  notify(target, seq)
    message.kind == "system"          → return 0        # 穿透加入的事件消息不是对话回合
    targets = 频道 agent 成员（除作者）+ 穿透（被点名却未加入的 Agent）
    targets 为空                      → return 0
    author.kind == "agent" → _spend_jump(targets)
        全部目标 in-flight             → True（零投递，不算跳，不扣款）
        used >= 3                      → 逐成员记 budget-exhausted，return 0   # 一个都不投递
        否则                            → consume_budget(epoch) 一次，投递
    投递 targets

run_round(agent, seq)
    已在 in-flight → 记进那一轮的簿记（不排队、不重放），return None
    否则            → 取待读消息 → 自判 → 落库 → 收口时落一条 merged（base_seq = max seq）

_commit_reply(agent, body)
    穿透且未加入 → add_channel_member + 先写一条 system「已加入」事件消息
    → 写回复 → notify(回复 seq)
```

```diff
 app/worksplice/
   db.py               + channel_budget 表（epoch_seq/used，落库）
                       + budget_state / human_epoch_seq / consume_budget
                       + is_channel_member / add_channel_member
                       ~ 人类消息落库同事务开启新世代并把预算重置为 3
                       ~ CHANNEL_SELECT_SQL 的未读口径加 author_member_id != :member_id
   orchestration.py    + _wake_targets / _pierce_target / _spend_jump
                       + _record_budget_exhausted / _record_merged / _Committed / _Turn
                       ~ notify：穿透 + 预算压制 + system 早退
                       ~ run_round：合并只在收口落一行、以最大 seq 为准
                       ~ _commit_reply：穿透回复顺带加入 + system 事件消息
                       ~ status_of：budget-exhausted 不再算 --error
   api.py              + GET /api/channels/{id}/budget（只读）+ 写方法 404 守卫
   service.py          + channel_budget 只读转发
   studio/app.js       ~ system 行复用既有消息行形态（方形头像色位 + 作者名 + 时间）
   studio/app.css      ~ .msg.sys 样式（只用既有 --muted/--border/--mono，不新造色位）
 app/tests/
   test_bounded_relay.py   + 24 条：唤醒面/穿透/上界/合并/失败隔离/恢复/未读/不可变性
   test_event_stream.py    + system 加入事件推送与补拉、预算耗尽在事件流可见
   test_channel_read.py    ~ 未读口径修正（见下）+ 2 条新用例
   test_agent_round.py     ~ 合并语义与未读期望
 app/README.md             ~ 唤醒面/穿透/策略 B/未读口径章节
 app/specs/issue-4-acceptance.md   + 逐条验收取证
```

关键取值：**人类世代** = 该频道最近一条人类消息的 `seq`；**一次 agent→agent 跳** = 一条 Agent 消息
**投递**了至少一个唤醒（多个目标仍只算 1，合并掉的重复唤醒不算也不扣）；**硬上界** = 任一人类世代
内跳数 ≤ 3（由「耗尽的世代里零投递」构造性保证，不是事后统计）。

### 已交付 #2 的未读口径修正（人类明确授权）

#4 票面字面「作者为 owner 时不计入自己的未读」按裁定为 **B**：未读从「已读位置之后
`kind ∈ {human, agent}`」改为「**且作者不是本人**」。口径仍只在 `db.py` 的 `CHANNEL_SELECT_SQL`
写一次（`unread_count` / `unread_total` 共享）。受影响的 #2 用例清单与逐条处置见
`app/specs/issue-4-acceptance.md` §6。

## Evidence

- **Before:** 隔离基线 worktree `/tmp/wsps-red-4`（`git worktree add /tmp/wsps-red-4 4126dd4`，
  只拷入本票测试文件、不带实现改动）：

  ```text
  $ cd /tmp/wsps-red-4/app && .venv/bin/python -m pytest \
      tests/test_bounded_relay.py tests/test_event_stream.py tests/test_agent_round.py tests/test_channel_read.py
  20 failed, 52 passed
  # 含 test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch（上界）
  #    test_pierced_agent_that_replies_joins_and_leaves_a_system_event（穿透加入）
  #    test_repeated_wakes_during_a_round_are_merged_into_one（合并=一行、max seq）
  #    test_budget_state_is_persisted_and_survives_restart（预算落库）
  #    test_channel_read.py 的未读口径（基线里 owner 自己的消息也被计入 → assert 2 == 1）
  ```

  **After:** 本 worktree：

  ```text
  $ .venv/bin/python -m pytest
  142 passed, 1 skipped in 92.95s
  $ .venv/bin/ruff check app && .venv/bin/ruff format --check app
  All checks passed! / 28 files already formatted
  $ .venv/bin/python -m mypy
  Success: no issues found in 23 source files
  $ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
  errors 0 files 23
  ```

- **真实进程 + 真实 Chromium**（独立临时数据目录 `/tmp/wsps-e2e-data-4`，脚本化替身 ⇒ 零付费调用）
  跑「两个互相 @ 的 Agent」：

  ```text
  seq= 1 kind=human  author=Owner  body=开始接力
  seq= 2 kind=agent  author=甲      body=@乙 接力 #1
  seq= 3 kind=agent  author=乙      body=@甲 接力 #2
  seq= 4 kind=agent  author=甲      body=@乙 接力 #3
  seq= 5 kind=agent  author=乙      body=@甲 接力 #4
  seq= 6 kind=agent  author=甲      body=@乙 接力 #5      ← 照常落库
  budget: {"epoch_seq": 1, "limit": 3, "used": 3, "remaining": 0}
  round_outcome: … seq=6 budget-exhausted …（零投递）
  ```

  穿透加入的真实事件消息与未读口径（同一浏览器会话）：

  ```text
  message seq=3 kind=system author=局外人 body=局外人 加入了频道：它被点名时并不在此频道，回复时顺带加入…
  message seq=4 kind=agent  author=局外人 body=我来了，这条我接。
  DOM: { avatar:'avatar sm av-2', tag:'事件', badge:null, navigations:1 }
  ```

  截图：`relay-channel.png`、`penetration-system-row.png`（本票证据目录）。

## Merge Danger

**Door:** two-way —— 纯本机单进程应用，回滚即 `git revert`；数据目录里新增的 `channel_budget`
表对旧版本无害（旧版本忽略未知表），消息与轮次结果表的结构未变。

**Blast Radius:** 本仓 `app/`（后端 3 个模块 + 前端 2 个文件 + 测试）；无外部消费者、无下游票。

需要注意的两处**行为变化**（都是有意的，且在验收文档 §6 记录）：

1. 未读口径从「含 owner 自己的消息」改为「作者为本人不计入」——按 #4 裁定修正了 #2 的交付语义。
2. `budget-exhausted` 不再把成员状态点标成 `--error`（它既不是「不想说」也不是「它坏了」）。
