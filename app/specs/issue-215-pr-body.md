## Summary

Closes #215

AgentScope 的 `reply_id` 是**一条回复**的标识，不是一次确认的标识：同一条回复里第二个需要确认的工具调用会
再发一次 `RequireUserConfirmEvent`，`reply_id` 与第一个相同。修复前 `ApprovalBroker.register()` 见到记录
已是终态就直接返回，seam 的 `wait()` 拿到空批，于是
`raise RuntimeError(f"approval {pending_reply_id} resolved without new decisions")`，整轮 `failed`。

```text
register(reply_id, tool_calls)                # reply_id = 回复级
  record 不存在 ──────────────→ 建 pending + 发布整批
  record pending ─────────────→ 合并调用（既有）
  record approved / denied
    ├─ 本批全是已知 id ───────→ 原样返回（幂等边界：不重开、不发布）
    └─ 本批含新 id ──────────→ tool_calls 追加 / state=pending / resolved_at=NULL
                                requested_at=now / session deadline 重开
                                只发布**新调用**的 approval_required
  record expired ────────────→ 原样返回（迟到登记不复活）
```

```diff
 app/alibot/
 ├── approval.py     # register 终态分支重开 + _new_tool_calls()
 ├── db.py           # update_approval_request：resolved_at 哨兵（可显式写 NULL）+ requested_at
 └── studio/app.js  # absorbApprovalRequired 收窄；approvalPendingToolCalls 接线
 app/tests/
 ├── test_approval_seam.py  # +2 同轮两次 ASK 端到端 / 失败出口不被吞
 ├── test_approvals.py      # +5 幂等边界 / 重开保留旧决策 / deadline 重开 / expired 不复活 / 哨兵
 └── approval_ui.test.cjs   # +4 重开 / 幂等边界 / 收口卡仍有明细 / 快照重载只提交新调用
 CONTEXT.md · app/README.md · docs/adr/0021-*.md   # 回复级 reply_id 的措辞同步
```

前端有一处必须一起收窄：`absorbApprovalRequired` 原本「已收口就忽略」，会让第二批卡片永远不出现；
现在只有「没有新调用」时才忽略。另外卡片与提交改用只读的 `approvalPendingToolCalls(record)`——否则
快照重载后对第二批点「拒绝」会把旧调用的相反结论一起带上，被服务端按 409 整批拒掉。

## Evidence

- **Before**（未改代码，只加用例）：
  `python -m pytest tests/test_approval_seam.py -k second_ask` →
  `1 failed`，`AssertionError: 模型调用失败：RuntimeError: approval f4c1… resolved without new decisions`
  （`assert 'failed' == 'replied'`）。
  `scripts/red-evidence.sh HEAD app/tests/approval_ui.test.cjs`（隔离副本，基线 app.js md5
  `7571556470743e71a9eeba5b7dcea2ee`）→ `tests 10 / pass 8 / fail 2`，两条 #215 用例红：
  `+ 'approved' - 'pending'` 与「已决策的旧调用不再要求人类决策」。
- **After**（本分支）：
  `-k "second_ask or terminal_record"` → `2 passed`；受影响 pytest 五件套 → `61 passed in 22.58s`；
  `node --test tests/approval_ui.test.cjs` → `tests 11 / pass 11 / fail 0`。
- **门禁**：`scripts/check.sh` exit 0 —— pytest `1053 passed, 10 skipped`、ruff check 全过、
  `ruff format --check` `269 files already formatted`、mypy `114 source files` 无问题、pyright
  `0 errors, 0 warnings`、node glob `237 pass / 0 fail`。
- 逐条判据与读数：`app/specs/issue-215-acceptance.md`。

## Merge Danger

**Door:** two-way

行为变更集中在一个进程内 broker 方法与其前端呈现；回滚只需 revert 这一个 commit，无迁移、无数据改写。
既有审批记录在库里仍是合法行（`pending` 之外的三态语义未变），旧进程读新库与新进程读旧库都不会遇到
未知列——`SCHEMA_VERSION` 保持 23，未加列、未加表。

**Blast Radius:** approval

- 审批主路径（`register` / `wait` / `submit` / `expire`）的**对外语义未变**：`POST /api/approvals/{reply_id}`
  仍只接受 pending、幂等、相反结论 409；600 秒超时与「不跨重启恢复（D19）」不变。
- `expired` 明确**不**因迟到登记复活（有 `test_an_expired_record_is_not_reopened_by_a_late_registration` 钉住）。
- `Database.update_approval_request` 的 `resolved_at` 改成哨兵：既有调用方（`_expire`、`submit`）行为逐字
  不变，仅 `register` 的重开分支显式传 `None` 以写回 NULL。
- 有意取舍：重开会刷新 `requested_at`，**第一批的实际等待时长不再可读**——这是为了避免「新卡片一出现就
  被本地判 expired、UI 无法决策」的死角（详见验收文档 §6，`CONTEXT.md` / `app/README.md` 已同步）。
- 收口判据仍是**全部历史决策**：第一批被拒绝时，第二批即使全批准，记录仍 `denied`（复用既有分支，
  不新造第二套收口路径）；这条也写进了两份文档。
- 前端 `approval_required` 载荷形状未变（`reply_id` / `member_id` / `tool_calls`），无新 SSE 类型、无新端点。
