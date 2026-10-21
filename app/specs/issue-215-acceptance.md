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
| 1 | 终态记录收到**新**调用 id → 合并、复位 `pending`、`resolved_at` 置 NULL、重开 deadline、只发布新调用 | `app/alibot/approval.py` `register` 的终态分支 + `REOPENABLE_APPROVAL_STATES` + 新 `_new_tool_calls()` / `_call_key()` | `python -m pytest tests/test_approvals.py -k "reopens or reopening"` | `2 passed`；帧里 `tool_calls` 只有 `tc-c`；另一条钉住 deadline 确实按现有时长重开（把 `session.deadline` 临时改成 `pass` 后该用例转红：`assert None == [...]`，即首批 deadline 已过） |
| 2 | 全是已知 id（重复 register）→ 不重开、不发布、返回原记录 | 同上（`if record["state"] not in REOPENABLE_APPROVAL_STATES or not fresh: return record`） | `python -m pytest tests/test_approvals.py -k same_calls_again` | `1 passed`；`queue.empty()` 为真、`resolved_at` 不变 |
| 3 | 旧 `decisions` 保留；`submit` 的幂等 / 409 语义不变 | 复用既有 `submit`，重开时不动 `decisions` | `python -m pytest tests/test_approvals.py -k reopens` | 重开后 `pending` + 旧两条决策；同结论幂等 `pending`；相反结论 `409`；只决策新调用即 `approved` |
| 4 | 全部决策后照常 `approved`/`denied` + `approval_resolved` | 既有分支，未新造收口路径 | 同上 + 全量门禁 | `final["state"] == "approved"`，`decisions` 长度 3 |
| 5 | seam：同轮第二次 ASK 批准后该轮继续并收口 `replied` | `app/alibot/agent_seam.py` **未改**（既有循环在重开后即可继续） | `python -m pytest tests/test_approval_seam.py -k second_ask` | `1 passed`；`outcome.status == "replied"`，两个文件都落盘，单行 `tool_calls` 长度 2、`decisions` 长度 2 |
| 6 | 前端收窄终态帧忽略规则 + 新调用时重开 | `app/alibot/studio/app.js` `absorbApprovalRequired` | `node --test tests/approval_ui.test.cjs` | `11 pass / 0 fail`（含四条 #215 用例） |
| 7 | 文档同步（`reply_id` 是回复级、一轮可有多批确认） | `CONTEXT.md` **审批** 词条、`app/README.md` 审批段与 `approval_request` 表、`docs/adr/0021-*` §四 | `scripts/check.sh` | `test_docs_vocabulary_guard.py` 绿；全量门禁绿 |
| 附加 | 重开后快照重载只显示/只提交未决策调用；已收口的卡仍回看全部调用 | `app/alibot/studio/app.js` 新 `approvalPendingToolCalls()` / `approvalCallIds()` | `node --test tests/approval_ui.test.cjs` | pending 卡不显示 `one.txt`、POST 体只有 `call-2`；approved 卡仍显示两个调用与两条 `aria-label` 工具名 |
| 硬边界 | `resolved without new decisions` 不被吞成静默成功 | `app/alibot/agent_seam.py` **未改** | `python -m pytest tests/test_approval_seam.py -k terminal_record` | `1 passed`：`outcome.status == "failed"`、reason 含该句、文件未落盘 |

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
$ .venv/bin/python -m pytest tests/test_approval_seam.py -k "second_ask or terminal_record"
2 passed, 10 deselected in 5.2s

$ .venv/bin/python -m pytest tests/test_approval_seam.py tests/test_approvals.py \
      tests/test_approvals_http.py tests/test_web_assembly.py tests/test_team_budget.py
61 passed in 22.58s

$ node --test tests/approval_ui.test.cjs
ℹ tests 11   ℹ pass 11   ℹ fail 0
```

---

## 3. 门禁

### 3.1 开发循环（`--fast`，多次）

```console
$ scripts/check.sh --fast
== ruff check           All checks passed!
== ruff format --check  269 files already formatted
== mypy                 Success: no issues found in 114 source files
== pyright              0 errors, 0 warnings, 0 informations
== node --test          ℹ tests 237   ℹ pass 237   ℹ fail 0
门禁全绿
```

### 3.2 定稿全量（本机只跑这一次）

```console
$ scripts/check.sh          # exit=0
== pytest（全量）        1053 passed, 10 skipped in 566.46s (0:09:26)
== ruff check            All checks passed!
== ruff format --check   269 files already formatted
== mypy                  Success: no issues found in 114 source files
== pyright               0 errors, 0 warnings, 0 informations
== node --test           ℹ tests 237   ℹ pass 237   ℹ fail 0
```

---

## 4. 改动面

| 文件 | 动作 |
| --- | --- |
| `app/alibot/approval.py` | `register` 的终态分支：`REOPENABLE_APPROVAL_STATES` 标出「已收口但可被下一批重开」的两态；`_new_tool_calls()`（与 `_merge_tool_calls` 共用 `_call_key()`）判定「这批里有记录尚未包含的调用」；有则合并 `tool_calls`、`state=pending`、`requested_at=utc_now()`、`resolved_at=NULL`、重开 session deadline（现有时长），只发布新调用；无则原样返回。`expired` 仍是终态，不因迟到登记复活 |
| `app/alibot/db.py` | `update_approval_request` 的 `requested_at` / `resolved_at` 都用与既有 `_UNSET` 同形的哨兵（区分「不修改」与「显式写入」，`resolved_at` 因此能写回 NULL）。不加列、不加表、`SCHEMA_VERSION` 保持 23；其它写路径未动 |
| `app/alibot/studio/app.js` | `absorbApprovalRequired` 收窄终态帧忽略规则（无新调用才忽略）；重开分支刷新 `requested_at`，镜像的 `tool_calls` 与后端一样保持累积；新增只读 `approvalPendingToolCalls(record)` / `approvalCallIds()`：**pending** 卡片与提交只看未决策调用，**已收口**卡片仍回看整条记录 |
| `app/tests/test_approval_seam.py` | 新增同轮两次 ASK 的端到端用例（真 AgentScope `Agent` + 真实文件工具 + `_ScriptedModel`）+ 钉住 `resolved without new decisions` 失败出口的用例；`_run_with_decision` / `_run_approving_every_batch` 合并到唯一的 `_run_round(on_pending=...)` 脚手架 |
| `app/tests/test_approvals.py` | 新增 5 条：幂等边界、重开保留旧决策、deadline 按现有时长重开、`expired` 不复活、`update_approval_request` 的哨兵语义 |
| `app/tests/approval_ui.test.cjs` | 新增 4 条 #215 用例（重开、幂等边界、已收口卡仍显示全部调用、快照重载只提交新调用） |
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

处置：新增只读 `approvalPendingToolCalls(record)`（按 `record.decisions` 过滤）。**pending** 卡片的
渲染、`aria-label` 与 `submitApproval` 都只用它；**已收口**（approved/denied/expired）的卡片仍回看
`record.tool_calls` 全集——否则第一批一被批准，卡片的明细与 aria-label 就会整片消失（这是 review 抓到的
回退，已补 `#215 已收口的卡仍回看整条记录的全部调用` 钉住）。服务端 `submit()` 的「只回送新决策 /
相反结论 409 / 幂等」语义**一字未改**；前端只是不再把已决策的调用重新提交一遍。用例：
`#215 快照重载后只显示/只提交未决策的调用（拒绝第二批不再被 409 卡死）`。

---

## 6. 有意取舍

**重开会刷新 `requested_at`**（后端与前端重开分支同口径）。代价：同一行的请求时间被覆盖，**第一批的
实际等待时长不再可读**——这是有意的取舍，不是遗漏。理由：前端 `approvalDisplayState()` 与倒计时都以
`requested_at + 600s` 为唯一判据；若沿用第一批的时间窗，一旦第一批耗时接近 600s，重开后的新卡片一出现
就被本地判成 `expired`（没有允许/拒绝按钮、`submitApproval` 直接 return），而库里其实是 `pending`、
后端还在等新决策——那会引入一个「UI 无法决策」的死角。

**收口判据是全部历史决策**（既有 `submit` 分支的必然结果，不是新造的语义）：第一批被拒绝时，第二批
即使全部批准，记录仍然是 `denied`——「批准第二批即通过」是误读。这条已写进 `CONTEXT.md` 与
`app/README.md`，不另造第二套收口路径。

其余既有语义不变：**不跨重启恢复（D19）**、**600 秒默认超时**、**`POST /api/approvals/{reply_id}` 只接受
pending**；不暴露 `BYPASS` / `DONT_ASK`，不放宽任何权限档；不引入新的审批存储或第二套收口路径。

---

## 7. code-review 双轴

见 §7.1 / §7.2（由 Standards + Spec 两个并行子代理各自给出，处置结论逐条记录）。

### 7.1 Standards 轴（处置：全部修完）

| # | 发现 | 处置 |
| --- | --- | --- |
| 1（硬） | `register` docstring 仍写「不重置 deadline」，与重开分支矛盾；`app.js` 注释同样过时 | 两处重写：docstring 写清「pending 时不动窗口、收口后重开按现有时长」，前端注释去掉「不重置 `requested_at`」 |
| 2 | 同名字段两义：`record.tool_calls` 服务端累积、前端重开时只留本批 → 指纹恒变 | 前端镜像改为**与后端同形地累积**（`[...existing.tool_calls, ...fresh]`），过滤只在 `approvalPendingToolCalls` 一处做 |
| 3 | 按 id 去重形状 4 份 | 后端抽 `_call_key()` 给 `_merge_tool_calls` / `_new_tool_calls` 共用；前端抽 `approvalCallIds()` 给 `approvalPendingToolCalls` / `absorbApprovalRequired` 共用 |
| 4 | `("approved","denied")` 字面量是第 4 份状态集 | 新增 `REOPENABLE_APPROVAL_STATES` 常量并加注释（`expired` 不在其中） |
| 5 | `published` / `shown` 两名一物，`cast` 只为抹平 Any | 合并为 `new_calls`（`None` = 发布整条记录），保留的必要 `cast` 加在同一处 |
| 6 | `_run_approving_every_batch` 是 `_run_with_decision` 的脚手架副本 | 两个辅助合并成唯一的 `_run_round(on_pending=...)`，两条既有 helper 变成薄封装 |
| 7 | 文档三处不一致：ADR 提到刷新 `requested_at`，CONTEXT / README 漏；db 两个时间戳的缺席约定不一 | `CONTEXT.md` / `app/README.md` 补 `requested_at`；`db.update_approval_request` 的两个时间戳统一用 `_UNSET` 哨兵 |

### 7.2 Spec 轴（处置：3 条补用例、2 条补文档、1 条修错）

| # | 发现 | 处置 |
| --- | --- | --- |
| 1 | 判据 1 的「重开 deadline（现有时长）」无直接断言 | 新增 `test_reopening_restarts_the_waiting_window_with_the_existing_duration`；把 `session.deadline = ...` 临时改成 `pass` 后该用例转红（`assert None == [...]`），证明它真的钉住重开 |
| 2 | 判据 5 的「不得吞成静默成功」无钉子 | 新增 `test_a_terminal_record_without_new_decisions_still_fails_the_round`：记录被带外推到终态、`wait()` 拿空批 → `outcome.status == "failed"` 且 reason 含 `resolved without new decisions` |
| 3 | `requested_at` 的取舍只写在验收文档 | `CONTEXT.md` 与 `app/README.md` 各补一句「第一批的真实等待时长不再可读」 |
| 4 | ADR-0021 的「事实修正」段属票面外改动 | 保留：`docs/agents/domain.md` 要求 ADR 冲突显式标注；只补事实、不改决策 |
| 5 | 行为等价重构（`_call_key` / `REOPENABLE_APPROVAL_STATES`） | 保留，且正是 Standards 轴第 3、4 条要求的收敛 |
| 6 | 验收文档引用的是已提交版的字面代码 | 本文件已按最终提交重写引用（`REOPENABLE_APPROVAL_STATES` / `_call_key` / 累积镜像） |
| 7（错） | **终态卡丢明细**：`approvalCardNode` 全用 `approvalPendingToolCalls` 后，approved/denied 记录的 decisions 覆盖全部调用 ⇒ 卡片不再渲染任何 `approval-tools`，`aria-label` 退化成空工具名 | 改为「pending 用未决策项、已收口用全集」；补 `#215 已收口的卡仍回看整条记录的全部调用` 钉住明细与 aria-label |
| 8 | 重开 `denied` 记录后永远回不到 approved（收口看全部历史决策） | 不新增第二套收口路径（票面第 4 条明确要求复用既有分支）；改为把后果写进 `CONTEXT.md` 与 `app/README.md` |

---

## 8. 交付

- PR：见 Answer（`--body-file` 创建，正文含 Summary / Evidence / Merge Danger，`Closes #215`）
- PR body 草稿：`app/specs/issue-215-pr-body.md`
- **PR 打开后不自合并**，等 coordinator 验收合并
