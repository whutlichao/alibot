# issue #215 验收：同一轮第二次审批可决策

- 票面正本：[issue #215](https://github.com/whutlichao/alibot/issues/215)
- 分支：`issue-215-approval-second-ask`（worktree `/Users/apple/orca/workspaces/alibot/issue-215-approval-second-ask`，
  从 main `8e20b19` 切出）
- 采集时间：2026-10-21｜环境：macOS（aarch64），Node `v25.0.0`，Python 3.11.13（worktree `.venv`
  符号链接到主工作区 `.venv`）
- 框架快照：`docs/agentscope/2.0.10/building-blocks/agent/human-in-the-loop.md`（未改）

> 本文件是 Answer 的支撑材料：每条判据记录**命令**与**实际跑出来的读数**。

---

## 0. 根因（复述，不重新论证）

AgentScope 的 `reply_id` 是**一条回复**的标识，不是一次确认的标识（快照原话：「当前回复的 ID，用于恢复
智能体」）。同一条回复里第二个需要确认的工具调用会再发一次 `RequireUserConfirmEvent`，`reply_id` 与第一
个相同。修复前 `ApprovalBroker.register()` 见到记录已是终态就直接 `return record`：既不合并新工具调用、
也不发 `approval_required`。随后 seam 的 `wait()` 看到 `state != pending` 返回空批，
`app/alibot/agent_seam.py:1313-1319` 于是
`raise RuntimeError(f"approval {pending_reply_id} resolved without new decisions")`，整轮 `failed`。
生产库里两条 `round_outcome` 的 reason 就是这一句。

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 终态记录收到**新**调用 id → 合并、复位 `pending`、`resolved_at` 置 NULL、重开 deadline、只发布新调用 | `app/alibot/approval.py` `register` 的终态分支 + 新 `_new_tool_calls()` | `python -m pytest tests/test_approvals.py -k reopens` | `1 passed`；帧里 `tool_calls` 只有 `tc-c` |
| 2 | 全是已知 id（重复 register）→ 不重开、不发布、返回原记录 | 同上（`if record["state"] not in ("approved","denied") or not new_calls: return record`） | `python -m pytest tests/test_approvals.py -k same_calls_again` | `1 passed`；`queue.empty()` 为真、`resolved_at` 不变 |
| 3 | 旧 `decisions` 保留；`submit` 的幂等 / 409 语义不变 | 复用既有 `submit`，重开时不动 `decisions` | `python -m pytest tests/test_approvals.py -k reopens` | 重开后 `pending` + 旧两条决策；同结论幂等 `pending`；相反结论 `409`；只决策新调用即 `approved` |
| 4 | 全部决策后照常 `approved`/`denied` + `approval_resolved` | 既有分支，未新造收口路径 | 同上 + 全量门禁 | `final["state"] == "approved"`，`decisions` 长度 3 |
| 5 | seam：同轮第二次 ASK 批准后该轮继续并收口 `replied` | `app/alibot/agent_seam.py` **未改**（既有循环在重开后即可继续） | `python -m pytest tests/test_approval_seam.py -k second_ask` | `1 passed`；`outcome.status == "replied"`，两个文件都落盘，单行 `tool_calls` 长度 2、`decisions` 长度 2 |
| 6 | 前端收窄终态帧忽略规则 + 新调用时重开 | `app/alibot/studio/app.js` `absorbApprovalRequired` | `node --test tests/approval_ui.test.cjs` | `10 pass / 0 fail`（含两条 #215 用例） |
| 7 | 文档同步（`reply_id` 是回复级、一轮可有多批确认） | `CONTEXT.md` **审批** 词条、`app/README.md` 审批段与 `approval_request` 表、`docs/adr/0021-*` §四 | `scripts/check.sh` | `test_docs_vocabulary_guard.py` 绿；全量门禁绿 |
| 附加 | 重开后快照重载只显示/只提交未决策调用 | `app/alibot/studio/app.js` 新 `approvalPendingToolCalls()` | `node --test tests/approval_ui.test.cjs -k` 见 §2.2 | 卡片不显示 `one.txt`；POST 体只有 `call-2` |

### 1.1 seam 侧为什么没改

`_run_round_stream` 的循环在 `register()` 重开记录之后即可继续：`pending_reply_id` 仍是同一个
`reply_id`（重开不换 id），`wait()` 会拿到新一批决策，`stored` 从 `tool_calls()` 读到合并后的调用，
`ConfirmResult` 只回送新决策。因此本票**没有**触碰两条轮次路径的等待/回送形状，也没有把
`resolved without new decisions` 这个 RuntimeError 改成静默成功（它仍是终态且无新决策时的唯一出口）。

---

## 2. 先红后绿

### 2.1 红：未改代码的工作树

先只加用例、不改实现，在 `app/` 下跑：

```console
$ .venv/bin/python -m pytest tests/test_approval_seam.py -k second_ask
F                                                                        [100%]
...
>       assert outcome.status == "replied", outcome.reason
E       AssertionError: 模型调用失败：RuntimeError: approval f4c1f65c4cfa40a3bee90f009bca168f resolved without new decisions
E       assert 'failed' == 'replied'
1 failed, 10 deselected in 10.14s
```

前端红读数用 `scripts/red-evidence.sh`（隔离副本，基线 `HEAD` = `8e20b19`，只换测试文件）：

```console
$ scripts/red-evidence.sh HEAD app/tests/approval_ui.test.cjs
基线：HEAD (8e20b19)
基线 app.js md5：7571556470743e71a9eeba5b7dcea2ee
✖ #215 已 approved 的卡收到带新 tool_call 的 approval_required → 回到 pending 并显示新调用
  AssertionError: + 'approved' - 'pending'
✖ #215 快照重载后只显示/只提交未决策的调用（拒绝第二批不再被 409 卡死）
  AssertionError: 已决策的旧调用不再要求人类决策
ℹ tests 10   ℹ pass 8   ℹ fail 2
```

红读数要点：既有 8 条审批 UI 用例全过 ⇒ 接缝与替身没被新用例破坏；两条 #215 用例全红 ⇒ 缺的正是
「终态帧带新调用时重开」与「只对未决策调用显示/提交」。

### 2.2 绿：同一批用例

```console
$ .venv/bin/python -m pytest tests/test_approval_seam.py -k second_ask
1 passed, 10 deselected in 0.13s

$ .venv/bin/python -m pytest tests/test_approval_seam.py tests/test_approvals.py \
      tests/test_approvals_http.py tests/test_web_assembly.py tests/test_team_budget.py
59 passed in 17.43s

$ node --test tests/approval_ui.test.cjs
ℹ tests 10   ℹ pass 10   ℹ fail 0
```

---

## 3. 门禁

### 3.1 开发循环（`--fast`，多次）

```console
$ scripts/check.sh --fast
== ruff check           All checks passed!
== ruff format --check  267 files already formatted
== mypy                 Success: no issues found in 114 source files
== pyright              0 errors, 0 warnings, 0 informations
== node --test          ℹ tests 236   ℹ pass 236   ℹ fail 0
门禁全绿
```

### 3.2 定稿全量（本机只跑这一次）

```console
$ scripts/check.sh          # exit=0
== pytest（全量）        1051 passed, 10 skipped in 568.21s (0:09:28)
== ruff check            All checks passed!
== ruff format --check   267 files already formatted
== mypy                  Success: no issues found in 114 source files
== pyright               0 errors, 0 warnings, 0 informations
== node --test           ℹ tests 236   ℹ pass 236   ℹ fail 0
```

---

## 4. 改动面

| 文件 | 动作 |
| --- | --- |
| `app/alibot/approval.py` | `register` 的终态分支：新增 `_new_tool_calls()` 判定「这批里有记录尚未包含的调用」；有则合并 `tool_calls`、`state=pending`、`requested_at=utc_now()`、`resolved_at=NULL`、重开 session deadline（现有时长），只发布新调用；无则原样返回。`expired` 仍是终态，不因迟到登记复活 |
| `app/alibot/db.py` | `update_approval_request` 的 `resolved_at` 改成与既有 `_UNSET` 同形的哨兵（区分「不修改」与「显式写回 NULL」），并新增可选 `requested_at`。不加列、不加表、`SCHEMA_VERSION` 保持 23；其它写路径未动 |
| `app/alibot/studio/app.js` | `absorbApprovalRequired` 收窄终态帧忽略规则（无新调用才忽略）；重开分支只展示新调用并刷新 `requested_at`；新增只读 `approvalPendingToolCalls(record)`，`approvalCardNode` / `submitApproval` 用它 |
| `app/tests/test_approval_seam.py` | 新增同轮两次 ASK 的端到端用例（真 AgentScope `Agent` + 真实文件工具 + `_ScriptedModel`），外加逐批批准的辅助 `_run_approving_every_batch()` |
| `app/tests/test_approvals.py` | 新增 4 条：幂等边界、重开保留旧决策、`expired` 不复活、`update_approval_request` 的哨兵语义 |
| `app/tests/approval_ui.test.cjs` | 新增 3 条 #215 用例（重开、幂等边界、快照重载只提交新调用） |
| `CONTEXT.md` | **审批** 词条写清 `reply_id` 是回复级标识、一条回复可有多批确认、重开语义与幂等边界 |
| `app/README.md` | 审批段新增「`reply_id` 是回复级标识（#215）」；`approval_request` 表说明同步 |
| `docs/adr/0021-*.md` | §四补一段「事实修正（#215）」，不改原决策 |

**没有改动**：schema / 迁移 / `SCHEMA_VERSION`（23）；`app/alibot/agent_seam.py`（既有循环够用，
不重写等待/回送形状）；`docs/agentscope/2.0.10/**` 快照；`submit()` 的决策合并与冲突判定；
`.github/workflows/**`；未跑 `ruff format`。

---

## 5. 本票引入并一并修掉的死角（409 卡死）

重开之后，**同一条记录**里同时躺着「已决策的旧调用」与「新调用」。live `approval_required` 帧按票面只
含新调用，但 12s 周期刷新与重连快照读的是 `GET /api/approvals` 的**整条记录**。若前端照着整条记录渲染
与提交：

- 对第二批点**允许**：请求里带上旧调用 `confirmed=true`，与旧决策同结论 → 服务端幂等跳过 → 正常；
- 对第二批点**拒绝**：请求里带上旧调用 `confirmed=false`，与旧决策相反 → 服务端按「相反结论 409」把
  **整批**拒掉 → 第二批永远无法决策，只能等 600s 过期。

处置：新增只读 `approvalPendingToolCalls(record)`（按 `record.decisions` 过滤），卡片渲染、卡片
`aria-label` 与 `submitApproval` 都只用它。服务端 `submit()` 的「只回送新决策 / 相反结论 409 / 幂等」
语义**一字未改**；前端只是不再把已决策的调用重新提交一遍。用例：
`#215 快照重载后只显示/只提交未决策的调用（拒绝第二批不再被 409 卡死）`。

---

## 6. 有意取舍

**重开会刷新 `requested_at`**（后端与前端重开分支同口径）。代价：同一行的请求时间被覆盖，**第一批的
实际等待时长不再可读**——这是有意的取舍，不是遗漏。理由：前端 `approvalDisplayState()` 与倒计时都以
`requested_at + 600s` 为唯一判据；若沿用第一批的时间窗，一旦第一批耗时接近 600s，重开后的新卡片一出现
就被本地判成 `expired`（没有允许/拒绝按钮、`submitApproval` 直接 return），而库里其实是 `pending`、
后端还在等新决策——那会引入一个「UI 无法决策」的死角。

其余既有语义不变：**不跨重启恢复（D19）**、**600 秒默认超时**、**`POST /api/approvals/{reply_id}` 只接受
pending**；不暴露 `BYPASS` / `DONT_ASK`，不放宽任何权限档；不引入新的审批存储或第二套收口路径。

---

## 7. code-review 双轴

见 §7.1 / §7.2（由 Standards + Spec 两个并行子代理各自给出，处置结论逐条记录）。

### 7.1 Standards 轴

（待填）

### 7.2 Spec 轴

（待填）

---

## 8. 交付

- PR：见 Answer（`--body-file` 创建，正文含 Summary / Evidence / Merge Danger，`Closes #215`）
- PR body 草稿：`app/specs/issue-215-pr-body.md`
- **PR 打开后不自合并**，等 coordinator 验收合并
