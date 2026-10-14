## Summary

补上「新建 Agent 后可在频道内 `@` 到它」缺失的那条写路径（issue #14）。用户反馈的
「新建 agent 会自动加入到频道 ⋯ 但 @ 时选择不到」经查证是**两处独立成因叠加**，本票一并修掉：

1. **新 Agent 确实不在任何频道里** —— 首版 `channel_member` 只有两个写点（建频道勾选、
   穿透回复顺带加入），`create_agent` 不写任何频道成员行。于是 `@` 补全的候选
   （= 当前频道成员表）里当然没有它。
2. **右侧「频道成员」面板显示的并不是频道成员** —— `renderDock()` 与左栏用的是同一份
   **全工作区** `state.members`，而标题写的是「频道成员」。这就是「前端看得见、@ 选不到」
   这个视觉矛盾的来源：其中一个名单名不副实。

```text
【后端】新增人类面端点 POST /api/channels/{channel_id}/members  body {"member_id": "..."}
  service.add_channel_member(channel_id, member_id)
    ├─ get_channel()                          → 频道不存在 = 404 early-out
    ├─ get_member() + deleted_at + kind 校验   → validation_failed（整体不落库，先校验后写）
    │    └─ kind != 'agent' 的文案与 create_channel 的「初始成员只能是 Agent」同口径
    ├─ db.add_channel_member(...)             ← 复用既有唯一写点（channel_member + channel_read）
    │    └─ 返回值 = 是否新增 ⇒ 幂等的事实来源，不在这里再算一遍
    └─ 返回 {joined, member, members(最新成员表), channel}
         └─ joined: true=新增 / false=本来就在（机械可判定，不靠状态码二次编码）

【前端】右栏「频道成员」面板名实相符（人类裁定 2 选 a）
  renderDock()  state.channelMembers[channelId]（GET /channels/{id}/members）
    ├─ 已加入（N）       本频道的 Agent 成员（既有卡片形态）
    └─ 未加入本频道（N） 工作区 Agent 减去已加入者，每行「加入」（.btn-sm + 虚线边框）
         点击 → POST /members → 用**响应里的** members 覆盖本地缓存 → updateMentionPicker()
           ⇒ 不刷新页面（pageLoads 恒为 1）即可在 @ 清单里选到它
  左栏 renderMembers() 仍是全工作区名单，group-label 改「本机 Agent」（只动文案、不动数据来源）
```

```diff
 app/worksplice/
   api.py       +  POST /api/channels/{id}/members（幂等动作 ⇒ 成功一律 200）
                +  PUT/PATCH/DELETE /channels/{id}/members/{member_id} → 404（本票不做移出）
   service.py   +  add_channel_member()：校验 + 复用既有写点 + 返回最新成员表
 app/worksplice/studio/
   app.js       ~  renderDock() 按频道成员表分两段渲染；+ memberCard() / dockGroups() / joinChannelMember()
                ~  左栏 group-label、mention 空态、建 Agent 提示三处文案指向面板「加入」入口
   app.css      +  .dock-group / .dock-group-label / .dock-member.is-outside / .dock-member-foot（全走既有 token）
   index.html   ~  rail group-label →「本机 Agent」；dock 副标题如实描述两段
 app/tests/test_channel_join.py   新增 15 条（HTTP 黑盒契约）
 CONTEXT.md                       补全 / 成员面板 / 频道成员 口径对齐 + 新增 加入频道、本机 Agent
 app/specs/issue-14-acceptance.md 新增（逐条验收 + 手跑取证 + 裁定记录）
```

**关键不变式：`@` 补全的候选来源与穿透语义一个字都没动。** 前者仍是
`app.js:1072 channelAgentMembers` → `GET /channels/{id}/members` 的频道-only 语义
（`test_mention_completion.py` 未修改且继续通过）；后者 `orchestration.py` 未被改动
（`test_bounded_relay.py` 的「被点名 ≠ 加入」继续通过）。本票只**补**一条加入的写路径，
并把 `CONTEXT.md` 的补全词条反向对齐到已交付的 #9 行为。

## Evidence

**Before:** 新建 Agent 后它不在任何频道；右栏「频道成员」列的是全工作区名单；`@` 清单
候选数 = 0（该 Agent 加不进来），且没有任何人类面端点能把它加进频道。基线 `2b8615c`。

**After:** 真实 Chromium（ego-browser）+ 真实 uvicorn，独立临时数据目录 `/tmp/wsps14-data`
+ 脚本化替身（零付费模型调用、零凭证）：

```text
新建频道 → 面板 [已加入（0）, 未加入本频道（0）]           ← 两条空态各自如实
新建 Agent「小验」→ 面板 [已加入（0）, 未加入本频道（1）: 小验 +「加入」按钮]
                  左栏 group-label = [频道, 本机 Agent]     ← 文案不再与「频道成员」冲突
【加入前】composer 输入 @：optionCount = 0                  ← 加入前选项数
【点击「加入」】posts = [{url: ".../members", body: '{"member_id":"mem_5245..."}'}]
【加入后】不刷新页面：pageLoads = 1, optionCount = 1         ← 加入后选项数（0 → 1）
【选中 + 发送】value = "@小验 ", caret = 4
  后端：seq1 human '@小验 这条走频道成员路径' mention_snapshot=[{member_id: mem_5245..., start:0, end:3}]
        seq2 agent '收到（1）'                mention_snapshot=[]
        members=[owner, 小验]
        ← 快照命中它、它经**频道成员路径**回复（没有 system 事件消息 ⇒ 不是穿透）
  跨重启：同一数据目录重启后端 → members 不变，re-join joined=false
```

截图（`/tmp/wsps14-evidence/`）：`01-empty-dock.png`、`02-before-join.png`、
`03-after-join-mention.png`、`04-sent-mention.png`、`05-final-dock-and-message.png`。

**三栏契约与断点实测**（CDP 覆盖视口宽度）：

```text
1400px → rail 252 / dock 380      1080px → dock 340      900px → flex-direction: column
```

**门禁全绿**（仓库根，venv 在仓库根 `.venv/`）：

```text
$ .venv/bin/python -m pytest app
173 passed, 1 skipped in 95.01s        # 基线 158+1 ⇒ 新增 15 条，无回归

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed!  /  37 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 25 source files

$ cd <repo root> && node ~/.pi-lens/tools/node_modules/pyright/index.js <worktree>/app/worksplice <worktree>/app/tests
0 errors, 0 warnings, 0 informations    # 必须从仓库根跑：pyrightconfig 的 venvPath 是 "."，
                                        # worktree 里没有 .venv
```

**红证据**（隔离的基线 worktree `/tmp/wsps-red-14`，主工作树未被改动）：

```text
$ cd /tmp/wsps-red-14 && .venv/bin/python -m pytest app/tests/test_channel_join.py -q
15 failed in 0.42s      # 主 worktree 上 15 passed ⇒ 测试真的在断言本票的新行为
```

## Merge Danger

- **低**。新增 1 个端点 + 1 个 service 方法；前端只改 `renderDock()` 与三处文案；`CONTEXT.md`
  文档对齐。`db.py`、`orchestration.py`、`mentions.py` 一行未动。
- **两条被钉住的语义**（本票明确不改，且测试继续通过）：`@` 补全的频道-only 候选来源
  （`test_mention_completion.py` 未修改）、穿透的「被点名 ≠ 加入」（`test_bounded_relay.py`）。
- **一处已知缺口**（按人类裁定不测、已在验收文档写明）：`member_id` **已删除** → 校验失败
  这一分支已实现但**无 HTTP 可达路径**（首版没有删除端点），因此没有测试；代码保留该判据。
- **`channel_read` 口径**：加入后第一次唤醒会读到该频道的既有历史（游标从 0 起），这是
  `db.add_channel_member` 的**既有语义**，票面明写「复用、不改它的语义」，且与穿透加入同一
  起点。真正被钉住的是「历史只被投递一次」。验收文档 §3.5 按事实分两半陈述，避免误读。
- **状态码口径**：成功一律 200（非 201）——这是幂等动作，与既有 `remote-data-flow/ack` 同形，
  「新增 / 本来就在」由载荷 `joined` 表达。
- 视觉上复用既有 token / 组件语汇，无新造色位、无硬编码色值，断点与三栏契约未变；
  `worksplice-design-system/` 与原型未修改。
- 本票**不做**：移出频道、批量加入、`create_channel` 初始成员语义变更。
