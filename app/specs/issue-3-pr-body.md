## Summary

Agent 从 `AGENTS` 常量变成了「数据库里的成员行 + 模型配置」，激活路径是 **人类消息落库 → 后端进程内唤醒 → Agent 自判 → 回复落库 → 事件流推送**。HTTP 面自始至终只服务人类。

```text
POST /api/channels/{id}/messages          ← 人类面（浏览器 / 人的脚本）
  service.send_message                     # app/worksplice/service.py
    parse_mentions(body, members)          # 提及快照（最左最长 + 引号 + 词边界）
    db.append_message(…)                   # 失败即抛 ⇒ 后面两步都不执行
    events.publish("message")              # 推送（人类与 Agent 走同一条）
    orchestrator.notify(channel, seq)      # 唤醒信号只带 (member, target, from_seq, reason)
      └─ 唤醒面 = 频道全部 agent 成员 − 作者
           run_round(agent, …)             # app/worksplice/orchestration.py
             agent_cursor → db.list_messages(after_seq)   # 同一读接口
             availability()                # 未配置 / 停用 / 未确认告知 / 缺凭证 → failed + 可读 reason
             AgentSeam.run_round(…)         # ★ 薄适配接缝
               ├─ AgentScopeAgentSeam        → agentscope 2.0.9（Agent / model / credential）
               └─ ScriptedAgentSeam（测试）  → 回应 / 忽略 / 失败 / 延迟
             rubric: 被点名 + ignored ⇒ failed
             _commit_reply(…)               # 唯一一条「Agent 消息进频道」的路径
             db.append_round_outcome(…)     # replied / ignored / failed / merged
             events.publish(round_outcome, typing, member_status)

GET /api/channels/{id}/events?after_seq=N   ← SSE：先读库补拉，再实时推送
  message / member_status / typing / round_outcome     每帧 id = 频道内 seq
```

模块布局（新增与改动）：

```diff
 app/worksplice/
+├── agent_seam.py       # 通向 AgentScope 的唯一接缝：AgentSeam 协议 + 2.x 适配 + 凭证窄入口
+├── orchestration.py    # 成员面：唤醒 → 读消息 → 自判 → 落库 → 轮次结果 + 四态状态点
+├── mentions.py         # 提及解析：最左最长 + @"带 空格" + 词边界/左边界
+├── events.py           # 进程内事件总线 + SSE 帧编码
 ├── db.py               # +agent_config / remote_consent / round_outcome；AGENT_COLUMNS
 ├── service.py          # 人类面服务：落库 → 推送 → 唤醒；Agent 创建与远端告知
 ├── api.py              # +/api/agents*、/round-outcomes、/events(SSE)、/remote-data-flow*
 └── studio/             # +新建 Agent 与数据面确认、SSE 追加渲染、四态状态点、提及高亮
 app/tests/
+├── scripted.py         # 脚本化替身 + 手动调度器（测试自己决定何时跑完一轮）
+├── test_create_agent.py / test_remote_data_flow.py / test_mentions.py
+├── test_agent_round.py / test_event_stream.py
+└── test_agent_smoke.py # 真模型连通性：默认 skip
```

## Evidence

**Before:** 空库只有 owner；Agent 只存在于原型的前端常量里，频道里的 `@` 什么都不会发生。

**After:** 独立临时数据目录 + 真实 uvicorn + 真实 Chromium（ego-browser），Agent 行为由脚本化替身固定（零付费调用）：

```text
== 门禁（全量）
$ .venv/bin/python -m pytest -q
113 passed, 1 skipped in 79.41s        # 那 1 个 skip = 默认跳过的真模型冒烟测试
$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 25 files already formatted
$ .venv/bin/python -m mypy
Success: no issues found in 22 source files
$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/worksplice app/tests
errors 0 files 22

== 正向闭环：Agent 的完整回复不刷新页面即出现
# 页面保持打开，从另一个 HTTP 客户端发人类消息
before: seqs=[1]   posted human seq: 3
after:  seqs=[1,2,3,4]   navigations=0   renders=3
        agent 作者=小明 tag=Agent avatar=av-0  提及=@小明 高亮

== 断线 → 重启 → 带 last_seq 补齐（页面未刷新）
kill -9 后端 → { online:false, streamState:'reconnecting', sendDisabled:true,
                 toast:'后端不可达，发送已禁止；消息没有被送出。', pendingOrFailed:0 }
重启后端（期间另一个客户端写入 seq 5..8）→
  { online:true, streamState:'open', seqs:[1..8], dupes:false, sendDisabled:false }
  bodies: [… '断线期间错过的第一条', '收到（5）', '断线期间错过的第二条 @小明', '收到（7）']

== 远端数据流告知（未确认不生效）
切到 DashScope → 告知块出现，正文逐项列出「消息正文、发送者显示名、频道名称与描述、
被点名成员的显示名」+「接收方是远端服务，不在你的机器上」+「不做应用层端到端加密」
不确认就创建 → status='offline'；确认后 → remote_consent=true

== 优雅关停（收尾发现的缺陷）
修前：STILL RUNNING after 20.0s — hung（进程残留、监听套接字已没、新请求全拒）
修后：EXITED cleanly in 0.2s rc=-15
```

**红 → 绿**（红证据在 `git archive` 隔离副本上取得，工作树未动）：15 组注入**全部判红、零组漏网**，
判红用例全部是 HTTP/SSE 黑盒用例。其中一组（消费游标在 failed 时也推进）最初**零判红** —— 那是本轮
测试唯一被红证据补出来的空洞，已补两条对照用例。逐条见 `app/specs/issue-3-acceptance.md` §5。

**双轴 review**（`code-review` skill，固定点 `5015383`，两轴各自的 sub-agent）：

- Spec：7 条 findings，6 条已处置（删掉越界的轮次结果 UI、状态点的「出错」改为读库里的最近一轮、
  SSE 轮换不再被误报成掉线、删掉过期的「下一票交付」提示、拆开两条误导性用例标题），1 条记为报告项。
- Standards：7 条，5 条已处置（术语回归 CONTEXT.md「轮次结果」、抽 `AGENT_COLUMNS`、删 6 处零消费
  代码与 1 条永不命中的死 CSS 规则、保活常量改为真正在用的 `SSE_POLL_SECONDS`），2 条记为有意接缝。
  处置与理由见 `app/specs/issue-3-acceptance.md` §6。

## Merge Danger

**Door:** two-way — 纯增量：新表（`agent_config` / `remote_consent` / `round_outcome`）由
`CREATE TABLE IF NOT EXISTS` 建立，`schema_version` 从 1 升到 2，旧数据目录照常打开。#2 的 36 条
用例语义未变，回滚只需 revert 这个分支。

**Blast Radius:** whole-app

- **新增常驻连接**：每打开一个频道就多一条 SSE 长连接（浏览器侧）。单连接默认 55 秒由服务端轮换，
  前端带 `last_seq` 立即重连 —— 这正是修复「长连接阻塞优雅关停」的手段，但意味着网络面板里会看到
  周期性的重连。
- **新增后台线程池**：唤醒编排默认用 4 个工作线程。轮次内部的异常已在 `run_round` 边界收口，
  不会打穿线程。
- **依赖变重**：`agentscope==2.0.9` + 各 provider 的 HTTP 客户端（`openai` / `anthropic` /
  `google-genai` / `xai-sdk` / `ollama`）进主依赖。`agentscope` 只在真实适配器实例化时 import，
  因此脚本化替身路径的测试不依赖它，但安装体积与解析时间会明显增加。
- **agent→agent 跳会产生真实轮次**：一个频道里有 ≥2 个 Agent 时，一条 Agent 回复会唤醒其它 Agent，
  因而**真的调用模型**（并可能产生费用）。本票不实现预算压制（#③），因此当前没有上界。
  这是本 PR 唯一需要人工留意的行为面。
- **数据面扩张**：启用远端 provider 后，消息正文、发送者显示名、频道名称与描述、被点名成员显示名
  会发给该提供商。由「首次启用前的一次确认」把住，库内零密钥。
- 预算策略（#③）与穿透（#③）**未实现**，`consumed_budget` / `merged` 只是如实记录的事实字段。
