# 验收记录：#333 私信「正在回复」提前消失——把轮次级与 reply 级拆开

**票型**：实施票｜**门禁**：G-impl｜**测试档**：窄（前端 `app-context.test.js` / `components.test.js` /
`task-thread.test.js` + 后端 `test_event_stream.py`；收尾一次 `scripts/check.sh --fast`；最终 head 以 PR CI 全量为准）
**Ticket**：[#333](https://github.com/whutlichao/alibot/issues/333)（只读引用；不 close、不改标签）
**base**：`ba668c3`（= 派活时 `main` 的 tip，也是本分支的起点）｜**branch**：`issue-333-impl`
**worktree**：`/Users/apple/orca/workspaces/alibot/typing-indicator-round-scope`
**PR**：（见 `## Answer` 回填）｜**PR body**：`app/specs/issue-333-pr-body.md`
**采集时间**：2026-10-28｜**环境**：macOS（aarch64）、Python 3.11.13（仓库根 `.venv` 以符号链接接上）、
Node + Vitest 4.1.12（`cd app/frontend && env -u NODE_ENV npm ci`）

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① 红→绿：复刻 #333 真实帧序的用例先红后绿，读数写进本文件 | `app/frontend/tests/app-context.test.js` 的「「正在回复」的两个层级（#333）」 | 基线 `3 failed \| 9 passed (12)`；实现后 `12 passed (12)`（§2） | ✅ |
| ② team 场景：#210 的多成员名字、单个 `REPLY_END` 只收敛自己、成员帧不碰队长轮次 | 同文件第二条用例 | 两成员名字 `['mem-leader','mem-member']`；成员收口后只剩 `['mem-leader']`；成员 `active:false` 后队长 `typing` 仍在。停止按钮：当前工作台没有它的渲染实现（见 `## Answer`），本票锁的是数据面——`activeRounds` 里每个在飞 reply 一个键（与 `round.active` 的 `rounds` 同形） | ✅ |
| ③ 断线清理的既有用例仍绿 | 同文件「断线清掉「正在回复」」 | 该条在红、绿两次读数里都通过（§2） | ✅ |
| ④ 后端断言轮次帧 `scope=="round"`、team 成员帧 `scope=="member"`；既有 typing 用例仍绿 | `app/tests/test_event_stream.py` | 新增 `test_team_member_typing_frames_carry_member_scope` + 既有 typing 用例补断言；`30 passed`（§3） | ✅ |
| ⑤ 门禁：窄档三条 + 后端 + 收尾 `scripts/check.sh --fast` | 见 §4 | 前端 `98 passed`、后端 `30 passed`、`--fast` 全绿（459 前端用例） | ✅ |
| ⑥ 双轴 code-review（Standards + Spec）findings 与逐条处置写进 Answer | `## Answer` | 两轴报告 + 逐条处置 | ✅ |
| ⑦ 交付：PR body（Summary / Evidence / Merge Danger / `Closes #333`），Door `two-way`，分支已推送 | `app/specs/issue-333-pr-body.md` | 见 `## Answer` 的 PR 号 | ✅ |

---

## 0. 前置 skill 的执行（`diagnosing-bugs`）

票面指定前置 skill `/Users/apple/.agents/skills/diagnosing-bugs/SKILL.md`，开工前完整读过，按阶段执行：

| 阶段 | 本票实际做的事 |
| --- | --- |
| Phase 1 反馈回路 | 一条命令：`cd app/frontend && env -u NODE_ENV npx vitest run tests/app-context.test.js`。它驱动**真实帧序**（把后端推送顺序逐帧喂进 `handleFrame`）并断言**用户看到的症状**（`typingNames` 里的成员与 `MessageStream` 的 `[data-od-id="typing-line"]`）。红能力：先写用例、源码未改时跑，3 条新用例红；确定性：同一命令连跑两次同一读数；速度：~0.7s；可无人值守。 |
| Phase 2 复现 + 最小化 | 红读数 `3 failed \| 9 passed (12)`，失败点正是「`REPLY_END` 之后 `typing['ch-1']` 变成 `undefined`」。最小复现是帧序 `typing{active:true,scope:"round"}` → `REPLY_START` → `REPLY_END`：三条缺一不红（少 `REPLY_START` 就没有 reply 级事实可清；少开始帧则槽位本来就空）。 |
| Phase 3 假设（排名） | H1 `REPLY_END` 分支连带删 `typing`（**证实**：删掉那两行后私信用例转绿）。H2 `REPLY_START` 写 + 成员帧覆写单槽（**证实**：team 用例红在 `expected 'mem-member' to be 'mem-leader'`）。H3 `typingNames` 优先 `activeRounds`，所以 reply 级清空后没有回落（**是 H1 的后果，不是独立原因**）。H4 `round.active` 的空快照会清 `typing`（**实现过程中从清理矩阵反查出来的第五个清理路径**，已一并收口，见 §1）。 |
| Phase 4 仪表 | 不需要临时日志：断言直接落在两条分支上（`handleFrame` 的 `REPLY_END` 分支、`handleTargetFrame` 的 `typing` 分支），红/绿读数本身就是定位。 |
| Phase 5 修复 + 回归 | 同一份用例即是回归测试（票面要求 Phase 1 与 Phase 5 是同一份）；实现后同命令 `12 passed`。 |
| Phase 6 清理 | 无 `[DEBUG-…]` 残留（`grep -rn "DEBUG-"` 于改动文件为空）；临时读数只落在 `/tmp`，不入库。 |

---

## 1. 改动面

| 文件 | 动作 |
| --- | --- |
| `app/alibot/orchestration.py` | typing 帧的**三个发布点**新增 `scope` 字段：`_run_round` 的轮次开始帧与收口帧 `scope=TYPING_SCOPE_ROUND`（`"round"`）；`_run_seam.emit` 的 team 成员帧 `scope=TYPING_SCOPE_MEMBER`（`"member"`）。旧字段（`type` / `member_id` / `seq` / `active` / `reason`）一个不动。 |
| `app/frontend/src/app-context.js` | ①`REPLY_START` / `REPLY_END` 只维护 `activeRounds`，删掉对 `typing` 的写入与清空；②`round.active` 快照只铺 `activeRounds`（不再写 / 清 `typing`——空快照会在「正文已生成、记忆还在写」的 5.4 秒里清掉指示）；③`typing` 帧按 `payload.scope \|\| 'round'` 判层级，只有 `round` 才写 / 清（缺 `scope` 向后兼容）；④`typingNames` 语义不变。 |
| `app/README.md` | SSE 词表 `typing` 行补 `scope` 含义（`round` = 轮次级 / `member` = team 成员级 / 缺省按 `round`）。 |
| `app/frontend/tests/app-context.test.js` | 新增「「正在回复」的两个层级（#333）」三条用例：私信真实帧序、team 两成员、清理矩阵（含 `round.active` 与缺 `scope` 的向后兼容）。 |
| `app/tests/test_event_stream.py` | 既有 `test_typing_indicator_is_emitted_while_a_round_is_in_flight` 补「轮次帧 `scope=="round"`」；新增 `test_team_member_typing_frames_carry_member_scope`（替身在队长轮次里经同一个 `emit` 发成员 reply，断言成员帧 `scope=="member"` 且 `reason` 仍为 `team-assignment`）。 |
| `app/specs/issue-333-acceptance.md` / `-pr-body.md` | 本文件与 PR 正文。 |

**明确没改**（票面 Constraint 逐条）：后端「先写记忆再落库」的顺序；`round_outcome` / `message` /
`member_status` 的既有语义；schema / 迁移 / 构建配置；npm / Python 依赖；`.venv` / `package-lock.json`；
`typingNames` 的「有 `activeRounds` 就列其中成员，否则回落 `typing`」判据。

### 1.1 两个层级（改后的数据流）

```text
_run_round 开始 ──→ typing{scope:"round", active:true,  member_id:本轮成员} ──→ typing[target] = 成员
                      │
Agent 流式回答 ──→ agent_event REPLY_START ──→ activeRounds[target][reply_id] = 成员
                      │                         （typingNames 有 activeRounds 时列它，去重保序）
模型正文生成完 ──→ agent_event REPLY_END ────→ activeRounds 只删这一条 reply
                      │                         （旧实现同时 delete typing —— 指示在这里提前消失）
记忆写入（实测 5.4s）+ 消息落库
                      │
_run_round 收口 ──→ round_outcome ──→ typing{scope:"round", active:false} ──→ typing[target] 删除
                      │                         └─→ member_status（状态点回到 online）
team 成员分派 ──→ agent_event REPLY_START/END（member_id=成员）
                      └─→ typing{scope:"member", active:true/false} ──→ 前端**不**碰 typing 槽位
```

### 1.2 `typing` 的清理矩阵（改后只有这四条）

| 触发 | 判据 | 落点 |
| --- | --- | --- |
| 轮次收口 | `typing` 帧 `active=false` 且 `scope`（缺省按 `round`）且 `member_id` 与槽位一致 | `handleTargetFrame` |
| 用户中断 | `round.interrupted` 清空 `activeRounds` 之后 | `handleFrame` |
| 成员状态点离开 working | `member_status` 非 `working` 且与该成员一致 | `handleTargetFrame` |
| 断线 | `onError` 清空 `typing` / `activeRounds` | `onError` |

「其余一律不清」：`REPLY_START` / `REPLY_END`、`round.active` 空快照、`scope==="member"` 的 typing 帧都不再动 `typing`。

---

## 2. 红→绿读数（Phase 1 / 5 同一份用例）

**命令**（红、绿两次完全相同）：

```sh
cd app/frontend && env -u NODE_ENV npx vitest run tests/app-context.test.js
```

**红**（源码仍是 `ba668c3` 的字节，工作区只多了新用例；`/tmp/issue-333-red.txt`）：

```text
 ❯ tests/app-context.test.js (12 tests | 3 failed) 52ms
   ✓ 断线清掉「正在回复」：断开期间不可能有 in-flight 事实 1ms
   ✓ typing 帧：active 时记下「谁在回」，inactive 时清掉 1ms
   × 私信：REPLY_END 之后指示仍在，直到轮次的 typing{active:false} 才消失 5ms
   × team：两个成员同时在飞名字齐全；一个成员 REPLY_END 只收敛它自己，成员帧不碰队长轮次 2ms
   × 清理矩阵：只有轮次级事实结束才清 typing；缺 scope 按 round 处理（向后兼容） 0ms

AssertionError: expected undefined to be 'mem-1'          ← 私信：REPLY_END 之后 typing 被清（#333 的症状）
AssertionError: expected 'mem-member' to be 'mem-leader'  ← team：成员帧覆写了队长轮次槽位
AssertionError: expected undefined to be 'mem-1'          ← 矩阵：成员级帧不该清槽位

 Test Files  1 failed (1)
      Tests  3 failed | 9 passed (12)
```

**绿**（实现后，同命令）：

```text
 ✓ tests/app-context.test.js (12 tests) 63ms

 Test Files  1 passed (1)
      Tests  12 passed (12)
```

三条新用例断言的关键帧序（第一条即票面要求复刻的真实帧序）：

```text
typing{active:true, scope:"round"} → agent_event REPLY_START → agent_event REPLY_END
  ├─ 断言：typing['ch-1'] == 'mem-1'，typingNames == ['mem-1']，MessageStream 的 typing-line 仍在
→ message（Agent 回复落库）→ 断言 typing-line 仍在
→ round_outcome → typing{active:false, scope:"round"} → 断言 typing 空、typingNames 空、typing-line 消失
```

---

## 3. 后端读数

**命令**：

```sh
cd app && ../.venv/bin/python -m pytest tests/test_event_stream.py
```

**读数**：`30 passed in 26.50s`（新增 1 条 + 既有 1 条补断言）。

- `test_typing_indicator_is_emitted_while_a_round_is_in_flight`：既有行为不变，另断言两条轮次帧
  `[active] == [True, False]` 且 `scope == "round"`。
- `test_team_member_typing_frames_carry_member_scope`：替身在队长轮次里经同一个 `emit` 补发一条成员的
  `REPLY_START` / `REPLY_END`（真适配器 `TeamPipeline` 就是这么发布的），断言成员帧 `[active] == [True, False]`、
  `scope == "member"`、`reason == "team-assignment"`，队长帧 `scope == "round"`。等待信号用 `round.active`
  ——它在服务端 `events.subscribe()` **之内**发出，而状态点快照在订阅之前就发完了。

**同族回归**（不在窄档清单，只因改动邻接而补跑一次）：`tests/test_team_visibility.py` 的 §4.5 断言
「成员 typing 帧 `reason == team-assignment`、与队长帧互不覆盖」，**5 passed**。

---

## 4. 门禁读数

| 门禁 | 命令 | 读数 |
| --- | --- | --- |
| 窄档前端 | `cd app/frontend && env -u NODE_ENV npx vitest run tests/app-context.test.js tests/components.test.js tests/task-thread.test.js` | `3 files / 98 tests passed` |
| 窄档后端 | `cd app && ../.venv/bin/python -m pytest tests/test_event_stream.py` | `30 passed in 26.50s` |
| 收尾快档 | `scripts/check.sh --fast` | ruff check / ruff format --check / mypy / pyright / vitest 全绿；`24 files / 459 tests passed`，末尾 `门禁全绿` |

`scripts/check.sh --fast` 的第一次运行在 **ruff format --check** 上红过：新用例里两处换行不符合格式化器的
建议。按票面「不跑 `ruff format`」的纪律，手工按它给出的**局部**建议改那两处（不整文件重排），第二次全绿。
本机噪声按仓库约定处理：前端命令一律 `env -u NODE_ENV`；手工 `git` / `gh` 前 `unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1`。

**改动量**（No whole-file reformat check）：

```text
 app/README.md                          |   2 +-
 app/alibot/orchestration.py            |  19 ++-
 app/frontend/src/app-context.js        |  21 +--
 app/frontend/tests/app-context.test.js | 222 +++++++++++++++++++++++++++++++++
 app/tests/test_event_stream.py         | 106 ++++++++++++++++-
```

（`git diff --numstat ba668c3...HEAD`：`1/1`、`17/2`、`13/8`、`222/0`、`104/2`。）

`app/frontend/src/app-context.js` 是 21 行左右的定向改动（13 增 8 删），没有整文件重排；三个源码文件都是
逐行编辑，没有任何 formatter 整文件重排的痕迹（`git diff` 里没有无关行的位置移动）。

---

## 5. 双轴 code-review 与逐条处置

按 `/Users/apple/.agents/skills/code-review/SKILL.md`：固定点 `ba668c3`，diff 命令 `git diff ba668c3...HEAD`，
Standards / Spec 各跑一个并行子代理，结论分开报、不互相抵消。两轴都**复跑过本票命令**（Standards：
`--fast` 范围、Spec：`app-context.test.js` 与后端 `-k typing`）。

### 5.1 Standards 轴（2 条硬边界 + 6 条判断项）

| # | finding | 处置 |
| --- | --- | --- |
| S1 | `scope` 撞既有领域词：`CONTEXT.md` 的 `scope_kind` / `scope_id` 指**配置作用域**（频道 / Agent），新 `scope=round\|member` 是第二含义 → Mysterious Name（按 §Language 措辞纪律判，可争辩） | **未采纳（票面固定字段名）**。Observable acceptance ② 明确规定字段名 `scope` 与取值；改名会违背规格。**缓解**：`app/README.md` 该行明写「此 `scope` 是**事件层级**，与配置作用域 `scope_kind` 无关」。 |
| S2 | `CONTEXT.md` 未同步（`docs/agents/review-rules.md` 第 6 条要求 `app/README.md` 与 `CONTEXT.md` 一起改） | **未处置（越 ownership）**：本票只允许改 `app/README.md` 的 typing 行。且「轮次 / in-flight / reply / 成员状态点」词条**已存在**，本票没引入新词；「轮次级 vs reply 级」是既有 `轮次 (Round)` 词条的拆分口径。遗留给文档票（见 Answer 遗留 1）。 |
| S3 | 注释与代码打架：`app-context.js` 的 `round.active` 注释宣称「轮次级事实只由 typing 帧表达」，但 `round.interrupted` / `member_status` 仍清 `typing` | **采纳并已修**：改成「轮次槽位的**写入**只来自 `scope==="round"` 的 typing 帧，清理只走清理矩阵那四条」。 |
| S4 | Shotgun Surgery：三处手搓 `"type": "typing"` dict，建议收敛成 `_publish_typing(...)` | **未采纳（判断项）**：三处帧本就不完全同形（轮次帧带 `seq`、reason 各自不同、成员帧挂在闭包里），抽 helper 会改发布点结构，超出「只新增 `scope` 字段」的最小改动；字面量重复已由常量消除（S5）。 |
| S5 | Primitive Obsession：`"round"` / `"member"` 裸字面量，而同模块已有 `TYPING_REASON_TEAM_ASSIGNMENT` 之类常量 | **部分采纳**：后端新增 `TYPING_SCOPE_ROUND` / `TYPING_SCOPE_MEMBER` 并在三处使用；前端只在**单一判定点**出现字面量（含缺省回退 `payload.scope \|\| 'round'`），不复制常量表。 |
| S6 | 同一段「层级由字段判定、不靠 reason 文案」的理由在 4 处代码 + 2 处 docstring 重复 | **部分采纳**：理由收敛到后端常量块与前端判定注释；成员 emit 的注释缩短。测试 docstring 保留（用例要能被单独读懂）。 |
| S7 | 测试替身 `MemberReplySeam` 定义在**函数体内**、`member_id=""` 外部赋值、`**kwargs` + `kwargs["target_id"]` 抹签名（既有 `_SopSeam` 是模块级）；「为什么非它不可」没进验收文档 | **采纳**：改为模块级 `_TeamMemberReplySeam`，`target_id` 显式入参；「为什么非它不可」写进类 docstring 与本文 §3。`member_id` 在 HTTP 建出 Agent 后赋值，与 `seam.behaviors[...]` 的既有写法同一时序。 |
| S8 | Divergent Change：在「应用接线」用例里 `mount(MessageStream)` 断 DOM，`setProps` 是征兆 | **未采纳（票面明确要求）**：Observable acceptance ① 明确要求在同一用例里断言 `MessageStream` 的 `[data-od-id="typing-line"]`。已用 `typingLineVisible(ctx)` 把挂载与断言收成一个辅助函数、去掉两处 `setProps`。 |
| S9 | `app/README.md` 小瑕：「成员也带 `member_id`」像 `member` 帧独有特征 | **采纳并已修**：改为「`member` = team 成员级，**成员分派那一支**」。 |

### 5.2 Spec 轴（3 条偏差）

| # | finding | 处置 |
| --- | --- | --- |
| P1 | 验收文档 / PR 正文不在提交里、Answer 还是空：票面「交付文档」「读数写进验收文档」「findings 与处置写进 Answer」无法从提交核对 | **采纳**：本文与 `-pr-body.md` 作为 docs commit 入库，Answer 回填 PR 号；红→绿读数带命令与原文（§2）。仓库的红证据惯例是**隔离基线**（`docs/agents/implementation.md`），不是一条红提交，因此历史里不设「先红」commit。 |
| P2 | 「停止按钮按 reply 各一个」只有 `activeRounds` 数据面断言，没有 UI 断言 | **说明（无法在本票做 UI 断言）**：当前 Vue 工作台**没有停止按钮的渲染实现**——`grep -rn "activeRounds" app/frontend/src` 的唯一 UI 消费者是 `typingNames`；`round.interrupted` 只在 live 词表里，没有触发按钮。本票能锁的是数据面（每个在飞 reply 一个键，形状与 `round.active` 的 `rounds` 一致）。UI 停止按钮属于另一张票（见 Answer 遗留 2）。 |
| P3 | `round.active` 分支把 typing 的**种子写入**也删了，超出票面「只约束清」；删掉后「轮次中途接入」只见停止句柄、没有「正在回复」 | **部分采纳（只按建议删了 `else delete`，保留「单一来源」的取舍并写明理由）**。理由：① Observable acceptance ② 把**写入**也限定在 `scope==="round"` 的帧（「前端只让 `scope==="round"` 的帧写/清 `typing`」）；② `round.active` 是 **reply 级**快照，用它写轮次槽位等于给「正在回复」造第二个判据来源，team 模式下还可能把成员 id 写进轮次槽；③ 当前工作台只开**全局流**，`api.py` 明确全局流不发 `round.active`（该分支今天近乎不可达，删写入无可观察行为变化）；④ 「中途接入 / 重连恢复轮次指示」正需要「工作台快照带 active-rounds」，票面已把它列为**范围外**。空快照的清空（`else delete`）则确实违反「其余一律不清」，已删。 |

---

## Answer

### 结论

#333 的症状在**前端状态机**里修好：`typing` 只承担「该 target 有一轮 in-flight」，`activeRounds` 只承担
reply 级在飞（名字与停止句柄）；`REPLY_START` / `REPLY_END` 不再写 / 清 `typing`，typing 帧新增可判定的
`scope`（`round` / `member`），缺 `scope` 按 `round`（向后兼容）。红→绿读数、清理矩阵、门禁与双轴处置见
上文各节。交付物：分支 `issue-333-impl`、PR（号与链接见下）、`app/specs/issue-333-acceptance.md`、
`app/specs/issue-333-pr-body.md`。

- **PR**：（回填）
- **PR body**：`app/specs/issue-333-pr-body.md`
- **Door**：`two-way`（只新增可选 `scope` 字段；无 schema / 迁移 / 破坏性 API / 权限 / 计费 / 部署 /
  不可逆副作用；前端状态是进程内读数，revert 即回到旧行为）

### 遗留 / 说明

1. **`CONTEXT.md` 的同步不在本票 ownership**（Standards S2）。本票没引入新词，只把「轮次级 vs reply 级」
   的既有口径落到 `app/README.md` 的 typing 行与代码注释；若要写进 `CONTEXT.md` 的 `订阅` / `轮次` 段，
   需要一张文档票或 coordinator 明确扩权。
2. **停止按钮的 UI 不在本票范围**（Spec P2）：当前工作台没有停止按钮组件，`activeRounds` 的唯一 UI
   消费者是「正在回复」那一行。本票锁住了数据面形状；停止按钮要另开票（渲染 + `POST /interrupt` 接线）。
3. **「中途接入 / 重连恢复轮次指示」不做**（Spec P3）：需要「工作台快照带 active-rounds」，票面已列为
   范围外，另开票。
4. **`round.interrupted` 的清理路径保留**：票面清理矩阵明确列了它；它不属于「被删掉的 reply 级清理」。

