## Summary

把「正在回复」拆成**轮次级**与 **reply 级**两层，reply 级事件不再清掉轮次级事实。

```diff
 typing[target]           # 轮次级：该 target 有一轮 in-flight
   _run_round 开始 ──────→ typing{scope:"round", active:true, member_id:本轮成员}
   _run_round 收口 ──────→ typing{scope:"round", active:false}
   team 成员 ───────────→ typing{scope:"member", active:true/false}
                            └─ 前端只让 scope=="round" 的帧写 / 清这个槽位

 activeRounds[target]     # reply 级：多成员名字与停止句柄（每个 reply 一条）
   REPLY_START ─────────→ rounds[reply_id] = member_id
   REPLY_END ───────────→ 只删这一条 reply（旧实现：连带 delete typing）
   round.active（重连） ─→ 只铺 activeRounds（空快照不再清 typing）
```

根因（#333）：`REPLY_END` 是 reply 级事实（模型正文生成完），这一轮还要写记忆再落消息
（实测 5.4 秒）；旧实现在 `REPLY_END` 里清 `activeRounds` 时顺手删了 `typing`，于是 reply 级
事件清掉了轮次级事实——私信里「David 正在回复…」在回复消息出现前就消失。第二条同源缺陷：
`REPLY_START` 写 `typing`、team 成员的 `typing` 帧（`reason=team-assignment`）覆写同一个单槽，
成员的 `active=false` 又会删掉它，队长这一轮的轮次指示被成员帧误清。

- **后端**（`app/alibot/orchestration.py`，只动 typing 帧的发布点）：三个发布点新增 `scope`
  字段——`_run_round` 的起止两帧 `"round"`、`_run_seam.emit` 的 team 成员帧 `"member"`。
  旧字段（`type` / `member_id` / `seq` / `active` / `reason`）一个不动。
- **前端**（`app/frontend/src/app-context.js`）：`REPLY_START` / `REPLY_END` 只维护 `activeRounds`；
  `typing` 帧按 `payload.scope || "round"` 判层级（缺 `scope` 向后兼容），层级不读 `reason` 文案；
  `round.active` 快照只铺 `activeRounds`。清理矩阵只剩四条：轮次级 `active=false` 且成员一致、
  `round.interrupted` 清空后、`member_status` 非 `working` 且一致、断线 `onError`。
- **文档**（`app/README.md`）：SSE 词表 `typing` 行补 `scope` 含义。
- **测试**：前端 3 条新用例（私信真实帧序 / team 两成员 / 清理矩阵），后端新增成员帧用例并给既有
  轮次帧用例补断言。

## Evidence

**Before**（基线 `ba668c3` 的源码 + 新用例，`cd app/frontend && env -u NODE_ENV npx vitest run tests/app-context.test.js`）：

```text
 ❯ tests/app-context.test.js (12 tests | 3 failed) 52ms
   × 私信：REPLY_END 之后指示仍在，直到轮次的 typing{active:false} 才消失
   × team：两个成员同时在飞名字齐全；一个成员 REPLY_END 只收敛它自己，成员帧不碰队长轮次
   × 清理矩阵：只有轮次级事实结束才清 typing；缺 scope 按 round 处理（向后兼容）
AssertionError: expected undefined to be 'mem-1'          ← REPLY_END 之后轮次指示被清
AssertionError: expected 'mem-member' to be 'mem-leader'  ← 成员帧覆写队长轮次槽位
 Tests  3 failed | 9 passed (12)
```

**After**（同一命令）：

```text
 ✓ tests/app-context.test.js (12 tests) 63ms
 Tests  12 passed (12)
```

**门禁读数**：

```text
cd app/frontend && env -u NODE_ENV npx vitest run tests/app-context.test.js tests/components.test.js tests/task-thread.test.js
  → 3 files / 98 tests passed

cd app && ../.venv/bin/python -m pytest tests/test_event_stream.py
  → 30 passed in 26.50s（轮次帧 scope=="round"、成员帧 scope=="member" 各有用例）

scripts/check.sh --fast
  → ruff check / ruff format --check / mypy / pyright / vitest 全绿；24 files / 459 tests passed；门禁全绿
```

完整取证（红/绿原文、清理矩阵、双轴 code-review 逐条处置）：`app/specs/issue-333-acceptance.md`。

## Merge Danger

**Door:** two-way

依据：改动只**新增**一个可选的 `scope` 字段（旧前端读到未知字段不受影响；新前端对缺 `scope` 的帧按
`"round"` 处理，旧后端仍兼容），无 schema / 迁移 / 破坏性 API / 权限 / 计费 / 部署 / 不可逆副作用；
前端这些状态是进程内读数、不落库，回滚 = revert 一个 commit，重启即回到旧行为。

**Blast Radius:** small（SSE `typing` 帧与工作台的进程内「正在回复」指示；不改 `message` /
`round_outcome` / `member_status` 的语义，也不改后端「先写记忆再落库」的顺序）

Closes #333
