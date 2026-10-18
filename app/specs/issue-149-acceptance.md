# #149 验收：权限三档 + ApprovalBroker + 审批 API / SSE

- 票面：`#149`（Part of `#131`）；票型 impl；门禁 G-impl。
- 分支：`issue-149-impl`；基线：集成分支 tip `31541d2`（已含 #148 workspace 与 #141 SSE）。
- 规格：`app/specs/agentscope-2-alignment.md` §3.6 / §4.3 / §4.4 / §4.5；ADR-0021 §四；
  研究：`issue-136-research.md`（拒绝 = `DENIED`、无内建超时、实例必须跨等待存活）、
  `issue-132-research.md`（`reply_id` 是路由键，挂起时流结束）。
- 范围：`permissions.py`（三档 + 规则匹配）、`ApprovalBroker`、审批 HTTP / SSE、`approval_request` /
  `permission_rule` 读写、真 seam 的审批等待接线与模型/审批超时拆分。**不做** team 模式与频道配置
  端点（#142）、Docker 后端（#150）、跨重启恢复（D19）。

## 改了什么

| 落点 | 改动 |
| --- | --- |
| `app/alibot/permissions.py`（新） | 三档 → `EXPLORE` / `DEFAULT` / `ACCEPT_EDITS`；`BYPASS` / `DONT_ASK` 不进入产品映射；`effective_rules`（同 `(tool_name, pattern)` 由 Agent 级覆盖频道级）；`build_permission_context/engine`；相对路径按项目根归一化的权限中间件。 |
| `app/alibot/approval.py`（新） | 进程内 `ApprovalBroker`：`register` 落 pending + SSE `approval_required`；`wait` 按 `reply_id` 等待（每轮自己的 `asyncio.Future`，HTTP 线程 `call_soon_threadsafe` 唤醒）；`submit` 只接受 pending、幂等、部分决策只回送新决策、相反结论 409；600 秒默认超时 → `expired`；启动 `pending → expired`。 |
| `app/alibot/db.py` | `permission_settings_for_target`（目标 → 频道，Agent 覆盖 + 两级规则）；`permission_rule` 的列表/覆盖写/删除；`approval_request` 的建、读、列表、更新与启动过期。 |
| `app/alibot/agent_seam.py` | 注入 `permission_of` / `approvals` / `team_run_of`；每轮建 `PermissionContext` 与 `AgentState`；消费 `RequireUserConfirmEvent` → 登记 → 等待 → 回送 `UserConfirmResultEvent`；模型调用 120 秒与审批等待分开计时；超时送 `UserInterruptEvent` 收口。 |
| `app/alibot/workspace.py` | `AskEveryTimeBash`：只有 `allow_local_shell=1` 才挂；每次调用 ASK 且 `bypass_immune`（allow 规则 / `trusted-writes` 不能自动放行）；read-only 档不挂。 |
| `app/alibot/api.py` | 生产装配 broker；`GET/POST /api/approvals`、`GET/POST/DELETE /api/permission-rules`；启动过期；`seam_factory` / `approval_timeout_seconds` 测试注入点。 |
| `app/tests/test_permissions.py`（新） | 三档行为、规则匹配、两级覆盖、不暴露 BYPASS / DONT_ASK。 |
| `app/tests/test_approval_seam.py`（新） | 真 AgentScope Agent + 真实文件工具的批准 / 拒绝 / 超时 / 模式 / 规则闭环；含 `TeamPipeline` 按成员 `reply_id` 的审批路由。 |
| `app/tests/test_approvals.py`（新） | broker 状态机、部分决策、幂等 / 冲突、重启过期、不存额外字段。 |
| `app/tests/test_approvals_http.py`（新） | 真实 HTTP + SSE：批准 / 拒绝 / 超时 / 幂等 / 重启 `/approvals` / 规则 CRUD。 |

## 逐条判据

### 1. `read-only` / `ask-writes` / `trusted-writes` 与规格 §3.6 一致；不暴露 BYPASS/DONT_ASK

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_permissions.py -q
........                                                                 [100%]
8 passed
```

读数：`read-only` 下 Read/Glob `ALLOW`、Write `DENY`；`ask-writes` 下读 `ALLOW`、写 `ASK`；
`trusted-writes` 根内写 `ALLOW`、根外写仍 `ASK`；`allow` / `deny` / `ask` 规则按
`tool_name + pattern` 命中；同键 Agent 规则覆盖频道规则。`map_permission_mode("bypass")` 与
`("dont_ask")` 抛 `ValueError`，产品档位表只有三档。HTTP 侧再以 `behavior="bypass"` 提交规则
得到 400（见判据 3 的文件，规则 CRUD 用例）。

### 2. 读允许；写/执行 ask；批准后继续；拒绝后工具结果 `DENIED` 且不执行；越界写仍审批

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_approval_seam.py -q
..........                                                               [100%]
10 passed
```

读数（真 `AgentScope Agent` + 真实 `Write` 工具，脚本模型驱动）：

- 批准：`notes.txt` 内容写入，轮次 `replied`；审批记录 `approved`。
- 拒绝：`notes.txt` **不存在**，审批记录 `denied`，轮次仍能收口（模型继续产出最终消息）。
- 模型超时 0.2 秒、审批等待 0.4 秒：轮次仍 `replied` —— 120 秒模型超时与 600 秒审批等待已拆开。
- 审批超时：记录 `expired`，轮次 `failed`，理由含“审批等待超时”，文件未写入；挂起的 Agent 被
  `UserInterruptEvent` 收口。
- `trusted-writes`：根内写不产生审批；根外写仍产生 `ASK`（测试以等待超时证明“确实停下来等人”，
  且根外文件不存在）。
- `read-only`：写工具被 `DENY`，**不产生**审批记录，文件未写入。
- 规则：频道 `deny` + Agent `allow`（同键）时写入成功；决策里附带的 `rules` 落成该 Agent 的规则。
- team：`TeamPipeline` 下成员挂起，`RequireUserConfirmEvent.reply_id` 是**成员**的 id；批准后只恢复
  该成员、写入文件，队长收口为 `replied`，审批记录的 `member_id` 是成员而不是队长。

**`DENIED` 与规格 `ERROR` 的映射**：票面 / 规格 §5.1 写“拒绝后 ERROR”，实现事实（#136 研究）
是 AgentScope 把拒绝建模为第四种工具结果 `ToolResultState.DENIED`，工具**没有执行**，模型仍可
继续本轮。本票按事实落 `DENIED`，不伪造框架不会发出的 `ERROR` 事件；验收口径 = “未执行的拒绝
路径”。这与规格的意图（拒绝不得执行、结果必须是错误路径）一致，差异只在状态词。

### 3. `approval_request` 落库；`GET /approvals` 可查；`POST /approvals/{reply_id}` 幂等

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_approvals.py tests/test_approvals_http.py -q
................                                                         [100%]
16 passed
```

读数：

- `register` 后 `GET /api/approvals?target_id=…&state=pending` 返回一条 `pending`，字段恰为
  `reply_id / target_id / member_id / team_run_id / tool_calls / state / decisions / requested_at /
  resolved_at`；SSE 依次收到 `approval_required` 与 `approval_resolved(approved)`，且 live-only
  帧不带 `id:`。
- 同一批决策重试：200，状态不变（幂等），不会第二次唤醒；相反结论：409。
- 部分决策：第一次只回送新决策 `tc-a`，第二次回送 `tc-b`；全部决定后才转 `approved` / `denied`。
- `GET /api/approvals?state=nope` → 400；未知 target → 404；未知 `reply_id` → 404。
- 权限规则：`GET/POST/DELETE /api/permission-rules` 可用；非法 `behavior="bypass"` → 400，不落半条规则。
- 不存密钥：登记时夹带 `api_key` / `env` 的调用被收窄为 `{id, name, input}`，回读与 JSON 中都不出现
  夹带值。

### 4. 默认 10 分钟超时 `failed`；重启 pending → expired；审批不存密钥

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_approvals.py -k "timeout or startup or secret" -q
...                                                                      [100%]
3 passed
```

读数：

- broker 默认 `DEFAULT_APPROVAL_TIMEOUT_SECONDS = 600.0`；测试注入 0.05 秒：`wait` 返回 `None`，
  记录 `expired` 且 `resolved_at` 非空；迟到决策 409，不会重新打开。
- 重启一致性：同一数据目录上新建 broker 后 `expire_pending_at_startup()`，残留 `pending`
  变 `expired`；HTTP 侧以“崩溃进程留下的 pending 行”启动后端，`GET /api/approvals` 直接读回
  `expired`（`resolved_at` 非空）。
- 不存密钥：`approval_request` 只写工具调用（`id` / `name` / `input` / 建议规则）与决策；
  载体、env、api key 等额外字段不入库。

### 5. 全量门禁

```console
$ scripts/check.sh
== pytest（全量；本分支共 744 条用例，含既有跳过项）
…（pytest 全绿）

== ruff check
All checks passed!

== ruff format --check（只检查）
174 files already formatted

== mypy（仓库根）
Success: no issues found in 72 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族）
ℹ tests 136
ℹ pass 136
ℹ fail 0

门禁全绿
```

## 已知残余与边界

- **team 模式的 `team_run_id`**：`TeamPipeline` 的权限上下文与审批路由已接入（按成员 `reply_id`），
  但 `TeamRoundContext` 不携带运行 id，因此审批记录的 `team_run_id` 目前为空；装配点可通过
  `team_run_of` 注入后续关联，不需要改审批状态机。
- **单成员超时中止整轮**：AgentScope 2.0.10 的 `UserInterruptEvent` 在 TeamPipeline 里广播给所有
  挂起成员与队长；因此 team 下任一审批超时都会让整轮 `failed`（与 R5 的说明一致）。
- **本地 shell**：只在 `allow_local_shell=1` 且非 `read-only` 档挂载；每次调用 ASK。`read-only` 下
  不挂 Bash（`EXPLORE` 没有“询问”档，只读命令与“每次审批”无法同时成立），这是比“只读命令可用”
  更窄的取舍。
- **跨重启恢复**：不做（D19）。重启时 pending 一律 `expired`，原轮次已随进程消失。
- **审批等待内存上限**：broker 的 `reply_id → session` 只增不减；进程生命周期内数量受实际审批次数
  约束，首版不做回收。
