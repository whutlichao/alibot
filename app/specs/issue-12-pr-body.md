## Summary

把「跳数」的记账判据收敛到**一个**正本上：#12 的问题不是语义错，而是同一个「跳」被记在了多条**成员行**上。

```diff
 WakeOrchestrator.notify(channel_id, from_seq)
   targets = _wake_targets(...)                    # 频道 agent 成员 + 穿透
   if system message: return 0                      # 不唤醒、不消耗
-  consumed_budget = author_kind == "agent"          # 名字读作「这一行扣了一次」
+  jump_delivered  = author_kind == "agent"          # 行级标记：这一轮由那次跳送来
   if jump_delivered and not _spend_jump(...):      # ← 唯一写点：channel_budget.used += 1
     return 0
   for target in targets:
     submit(run_round, ..., jump_delivered, ...)     # N 个成员 ⇒ N 条带标记的行
```

```text
记账形态（改前 = 改后，本票只消歧，不改行为）
┌───────────────────────────────┬──────────────────────────────┐
│ channel_budget.used           │ 跳数唯一正本，每次跳恰好 +1   │  ← 读这个
│ round_outcome.consumed_budget │ 行级标记，N 成员 ⇒ N 条为真   │  ← 不可按行求和
└───────────────────────────────┴──────────────────────────────┘
```

- **路线 B（行内消歧）**：不改 schema、不改列名与列集合、不新增表、不引入页面。理由（coordinator 裁定）
  见 `app/specs/issue-12-acceptance.md` §1。
- 语义表述从「这一**跳**」改为「这一**轮所属的跳**」，并在 coordinator 指定的三处写明
  **不可按行求和、正本是 `channel_budget.used`**：`db.py` 表注释 + `append_round_outcome` docstring、
  `orchestration.py` 模块与四处方法 docstring、`app/README.md`（另加两个只读端点 docstring）。

## Evidence

**Before**（#12 引用的 #4 实测事实：`used == 3` 而带标记的行只有 2 条 —— 本票在测试里**原样复现**）：

```text
PROBE budget: {epoch_seq: 1, limit: 3, used: 3, remaining: 0}
PROBE rows:   [(1,'replied',False), (2,'replied',False),
               (3,'replied',True), (5,'budget-exhausted',False), (4,'replied',True)]
PROBE marked: 2                    ← sum(标记行)=2 ≠ used=3
```

**After** —— 新增行为级断言（只经 HTTP 只读面）：一次跳投递 **2** 个成员 ⇒ 跳数仍只 **+1**。

```text
$ .venv/bin/python -m pytest tests/test_bounded_relay.py -q -k one_jump_delivered
1 passed
  assert budget(...)["used"] == 1                 # 跳数正本
  assert len(marked) == 2                         # 两个成员各一条行级标记
  assert {r["base_seq"] for r in marked} == {jump_seq}   # 同一次跳
  assert sum(marked) != budget(...)["used"]       # 按行求和 ≠ 跳数
```

**判别力（变异测试，隔离副本 `/tmp/wsps-mutate-12`，工作树不动）** —— 把记账改回「每成员各扣一次」：

```text
MUTATION#1 每成员各扣一次 → 3 failed
  FAILED test_one_jump_delivered_to_n_members_still_counts_as_one_jump   assert 2 == 1
  FAILED test_multiple_targets_from_one_message_cost_only_one_jump
  FAILED test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch
MUTATION#2 只给第一个目标打标记 → 1 failed
  FAILED test_one_jump_delivered_to_n_members_still_counts_as_one_jump   assert 1 == 2
```

> 诚实说明：本票是**形态**修正、行为不变，所以新测试拷到基线 `2b8615c` 上**也是绿的** —— 判别力由
> 上述**变异测试**证明，而不是由基线红证明（票面即要求如此）。详见验收文档 §3.4。

**门禁全绿**（基线 `2b8615c`：158 passed / 1 skipped）：

```text
$ .venv/bin/python -m pytest         159 passed, 1 skipped in 93.91s   # +1 新用例
$ .venv/bin/ruff check app / ruff format --check app
All checks passed! / 36 files already formatted
$ .venv/bin/python -m mypy           Success: no issues found in 25 source files
$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
errors 0 files 25
```

**行为不变**（#4 验收面，逐条用例名见验收文档 §3.6）：硬上界 ≤3、耗尽零投递、人类消息重置为 3、
合并不消耗、穿透不豁免预算、全 in-flight 不算跳、`round_outcome` append-only。实现层唯一改动是
局部变量改名 + 注释，**没有**任何条件分支/阈值/调用顺序变化；既有测试断言**零删改**。

## Merge Danger

**Door:** two-way

纯记账**形态**修正：不改 schema、不迁移、不改列名，无新增/删除端点，前端 `studio/` 0 改动。回滚 = revert 一个提交。

**Blast Radius:** tiny

`+121 / -6`（另加两份 spec 文档）、5 个文件，其中 4 个是注释/docstring/README 文案，1 个是测试。无生产读取方
（`consumed_budget` 在 `studio/` 与 `service.py` 均 0 命中），故语义修正不会改变任何调用方行为。

- **不影响 #4 已交付的对外约定**：`/round-outcomes` 的 payload 字段名与列集合完全不变（这是选 B 而非 A 的原因）。
- **不影响预算策略**：≤3 / 重置 / 耗尽 / 穿透不豁免，语义一律未动，`used` 仍是唯一正本。
- 唯一「新增行为」是文档层面的引导：告诉调用方去读 `channel_budget.used`，而不是对行求和。
