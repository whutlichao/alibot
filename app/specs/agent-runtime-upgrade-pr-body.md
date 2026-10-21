## Summary

2026-10-21 01:29–09:00 的一次真实 DM 暴露了根因：模型每一轮都从零开始。`WakeOrchestrator.run_round` 只把
`agent_cursor` 之后的新消息喂给模型，轮末把读位推到 `base_seq`；Agent 实例每轮新建，既不携带上一轮自己的
回复，也没有可续接的摘要。ADR-0027 已把「每轮只见新消息」写成结构性事实，但只给任务自动续跑开了
`history=True` 的口子，普通轮与 DM 仍然失忆。

本次按 AgentScope 2.0.10 把运行时补齐为：带窗口的历史上下文、超阈值自动压缩与可读卸载、可观测的轮次结束
原因、用户可中断的一轮、跨会话长期记忆，并把阈值与档位通过 schema 23 暴露到 Studio。核心边界保持不变：
**回应纪律、唤醒判定、`base_seq` 与标读仍只看新消息**，历史只进 `inputs`。

规格正本：Orca `task_7586fc46fea4`；验收与证据：`app/specs/agent-runtime-upgrade-acceptance.md`。

```text
run_round
  load_window                     # 最近 40 条，旧→新；仅普通轮，续跑仍用完整历史
  build_agent                     # AgentScope 2.0.10
    ContextConfig / ReActConfig   # 阈值 0.7 / 保留 0.2 / 工具结果 4000 / 图 5 / 迭代 30 / 重试 2
    InjectionConfig               # 每轮：上海时间、target 名称、上下文消息数
    SessionOffloader              # .alibot/sessions/<target--member>/offload/
    AgenticMemoryMiddleware       # .alibot/memory/<member>/Memory/MEMORY.md
  run ─→ RoundOutcome(finished_reason)
  save AgentState                 # .alibot/sessions/<session_id>/state.json 原子写
```

### T1 历史窗口（`14b9093`）

`orchestration.py` 保留 fresh / pending / mentioned / signal / `base_seq` / `mark_read` 的既有判据，只把最近
`context_window_messages`（默认 40，`ALIBOT_CONTEXT_WINDOW_MESSAGES` 可覆盖）条 target 消息按 seq 升序装进
`inputs`：本人历史记为 assistant，其他人沿用 `UserMsg`。续跑轮（`history=True`）仍用完整历史，窗口不作用于
读接口。旧点名不会变成新纪律。关键文件：`app/alibot/orchestration.py`、`app/alibot/runtime.py`、
`app/tests/test_context_window.py`。

### T2 压缩 / 卸载 / 每轮注入（`b647bff`）

单 Agent 与 team 成员两条装配路径都传入 `ContextConfig`、`ReActConfig`、`ModelConfig` 与 `InjectionConfig`。
新增 `Offloader` 实现，把压缩上下文与被截断的工具结果独占写入 `.alibot/sessions/<target--member>/offload/`；
Docker bind mount 下对外显示 `/workspace/.alibot/...`，不调用 Docker API 落盘。上海时间（ISO 带时区）、target
名称、上下文消息数每轮重新读取，token 用量由框架注入器负责。Docker 不可用时运行时关闭工作区工具并给出可读
降级、继续对话，不再整轮 failed。关键文件：`app/alibot/agent_seam.py`、`app/alibot/runtime.py`
（`SessionOffloader`）、`app/tests/test_runtime_context.py`。

### T3 轮次结束原因 / schema 23（`cfbbcdd`）

`RoundOutcome` 新增 `finished_reason`（`completed | interrupted | exceed_max_iters | error`），与 status 独立：
`interrupted` 不产出消息，`exceed_max_iters` 可保留部分正文，status 仍是 replied。适配器收口、编排转存、
数据库往返与 Studio 消息流展示贯通；中英各一条说明「结论可能不完整」。schema 23 一次声明完整终态：
`round_outcome.finished_reason` 可空（旧行 NULL）、`agent_config` 四个 runtime 配置列随同迁移，22→23 的
`MigrationStep` 与历史 DDL 留档连续。关键文件：`app/alibot/db.py`、`app/alibot/migrate.py`、
`app/alibot/agent_seam.py`、`app/alibot/i18n.py`、`app/alibot/studio/app.js`、`app/tests/test_runtime_finished.py`。

### T4 中断端点 + Studio 停止按钮 + AgentState（`33479a6`）

新增 `POST /api/targets/{target_id}/rounds/{reply_id}/interrupt`：只接受当前 in-flight turn 已发布的
`reply_id`，匹配则 200，错误 target、已结束或重复请求 409。运行中 `task.cancel()`，审批挂起时回送
`UserInterruptEvent`，两条路径都走完 `finally` 的 MCP / mount / model client 关闭链。新事件 `round.interrupted`
为 live-only，不推进 SSE 游标；SSE 每次连接补一份当前 `round.active` 快照，频道 / DM / 任务线程的 typing 旁
都能恢复停止按钮。`AgentState` 原子写入 `.alibot/sessions/<session_id>/state.json`，下一轮恢复摘要、工具缓存、
Plan 与 middleware 状态；消息 context 从数据库窗口重建，损坏或越界符号链接降级并记日志。关键文件：
`app/alibot/api.py`、`app/alibot/orchestration.py`、`app/alibot/events.py`、`app/alibot/runtime.py`
（`SessionState`）、`app/alibot/studio/app.js`、`app/tests/test_runtime_interrupt.py`。

### T5 长期记忆 + Studio 配置档（`8841d09`）

`memory.py` 实现记忆中间件装配与受限 backend：`memory_mode=agentic` 时按 `.alibot/memory/<member_id>/Memory/`
挂 `AgenticMemoryMiddleware`，只授予该目录的编辑权，保留显式 deny/ask 规则；`off` / null 时不挂中间件也不建
目录。同一 member 的两个 target 会话通过真实 Read/Write 写回并召回 `MEMORY.md`。PATCH 四字段沿用显式 null /
省略 / 具体值三态，新建行数值配置为 null 跟随默认；Studio 编辑弹窗有长期记忆开关与上下文条数、阈值、迭代
上限输入。关键文件：`app/alibot/memory.py`、`app/alibot/agent_seam.py`、`app/alibot/api.py`、
`app/alibot/studio/app.js`、`app/tests/test_runtime_memory.py`。

### 双轴 review 收口（`9d7a665`）

Standards 初审 2 项硬违反：编排把必答轮判失败时丢失原结束原因（改为保留原 `RoundOutcome` 字段）、迁移说明
仍写 schema 22（同步 README / CONTEXT 的 22→23）。1 项判断题：单 Agent 与 team 的中断收口保留两条控制流，
没有引入跨生命周期的通用回调抽象。Spec 初审 3 项 P2：任务线程缺结束文案、重连后丢失停止按钮、DM 无法读
卸载文件，均已补齐；DM 未启记忆时只补当前会话卸载目录的 Read，不增加项目根权限。该 commit 同时把记忆目录
的路径边界收进 `runtime_files.py`，统一祖先 / 文件符号链接的根检查。关键文件：`app/alibot/runtime_files.py`、
`app/alibot/api.py`、`app/alibot/orchestration.py`、`app/alibot/studio/app.js`、`app/tests/test_runtime_interrupt.py`。

### Commit 清单

```text
14b9093 feat(runtime): T1 普通轮使用历史窗口并保留新消息纪律
b647bff feat(runtime): T2 配置压缩卸载与每轮运行状态注入
cfbbcdd feat(schema): T3 schema 23 保存轮次结束原因并展示诊断
33479a6 feat(runtime): T4 支持中断本轮与原子保存 Agent 状态
8841d09 feat(runtime): T5 接入成员长期记忆与 Studio 配置档
9d7a665 feat(runtime): 补齐重连中断句柄与卸载读取边界
```

## Evidence

- **Before:** 各票在隔离基线副本上的红证据（`.scratch/red-t1` … `.scratch/red-t5`、`.scratch/review-*.log`）：
  T1 2 failed（第二轮只看到 `[3]`，应为 `[1, 2, 3]`）、T2 2 failed、T3 3 failed + 新文案 UI 2 failed、
  T4 2 failed + 停止按钮失败、T5 2 failed / 5 passed；review 修复前另有 2 / 3 / 1 / 2 failed。
- **After:** `scripts/check.sh` 全绿（退出码 0，末尾「门禁全绿」），coordinator 在隔离 worktree 复跑读数与
  worker 本机一致：

| 门禁项 | 读数 |
| --- | --- |
| pytest（全量） | **1036 passed, 10 skipped**（583.62s；跳过为本机无 Docker daemon 等既有口径） |
| ruff check | **All checks passed!** |
| ruff format --check | **262 files already formatted** |
| mypy（仓库根） | **Success: no issues found in 113 source files** |
| pyright | **0 errors, 0 warnings, 0 informations** |
| node --test（Studio DOM，glob） | **tests 220 / pass 220 / fail 0** |

原始日志：`/tmp/alibot-agent-runtime/check-final.log`（worker）、`/tmp/alibot-agent-runtime/coordinator-check.log`
（coordinator 隔离复跑）。

### 逐票可观察结果

| 票 | 落点 | 定向绿证据 |
| --- | --- | --- |
| T1 | 普通轮输入含上一轮人类消息与自己的回复；`mentioned` / `signal` 与只喂新消息时一致 | `test_context_window.py` 等 57 passed |
| T2 | 超阈值压缩、offload 落盘、system prompt 带当天日期、Docker 降级可读 | `test_runtime_context.py` 等 24 passed / 7 skipped |
| T3 | `exceed_max_iters` / `interrupted` 呈现后端原因；22→23 迁移后形态与 DDL 一致，旧行 NULL | `test_runtime_finished.py` 3 passed |
| T4 | 中断 200、轮次 `interrupted` 且 `reply_seq=NULL`、无 Agent 消息；错误 / 重复 409；重连恢复句柄 | `test_runtime_interrupt.py` 等 41 passed |
| T5 | `agentic` 挂中间件并跨会话写回 / 召回 `MEMORY.md`；`off` 不建目录；PATCH 四字段三态往返 | 六个 runtime 测试文件 19 passed |

Review 基线固定为 `3863e39`（与 `origin/main` 同点）。两轴结论、红绿证据与处理记录见
`app/specs/agent-runtime-upgrade-acceptance.md`；修复后回归红证据 `.scratch/review-finished-red.log`
（3 failed）与定向绿证据 `.scratch/review-*-green.log` 均保留。

浏览器证据（ego-browser，临时数据目录）：`/tmp/alibot-agent-runtime/studio-runtime-settings.png`（45 / 0.6 / 35 /
agentic 表单保存）、`studio-stop-round.png`（真实轮次出现停止按钮）、`studio-round-interrupted.png`
（点击后无 Agent 消息且读到 interrupted 结果）。

## Merge Danger

**Door:** one-way（schema 22 → 23 前向单向，回滚 = 还原备份）

**Blast Radius:** 全 Agent 运行时——上下文输入、压缩卸载、轮次结果读面与 Studio 消息流、Agent 配置 PATCH
白名单与 DM 装配都受影响。

**迁移**：`python -m alibot --migrate --data-dir <数据目录>`。22→23 是严格 +1 的单步，`round_outcome.finished_reason`
（可空，不回填）+ `agent_config.context_window_messages` / `context_trigger_ratio` / `react_max_iters` /
`memory_mode`（列默认 40 / 0.7 / 30 / off）一次声明完整终态。只加不删、不回填业务数据，旧行按列默认或 NULL
解释；迁移在单事务内完成，失败即回滚并返回非零码。

**备份**：迁移前先整目录备份（备份即复制数据目录），**手工备份时也必须连同 SQLite 的 `-wal` / `-shm` 一起
复制**，并停掉写入进程：

```bash
cp -a alibot.db alibot.db-wal alibot.db-shm /path/to/backup/
```

**回滚**：schema 23 不提供降级脚本，旧代码的版本闸门会拒收 23 的库。回滚方式只能是停进程后用备份覆盖还原
（同样包含 `-wal` / `-shm`），因此升级前那次备份是唯一退路；降级前新增的业务数据会随还原丢失。

## 遗留项

- 未在真实付费模型上跑端到端：全部测试使用脚本模型与临时库，无付费调用，未操作用户真实数据目录。
- 本机无 Docker daemon，Docker 相关用例按既有口径 skipped（全量共 10 skipped）；容器内实际卸载路径与降级
  行为只由受限装配层测试覆盖。
- 审批跨重启恢复不在本次范围（沿用既有 `expired` 口径）；中断只对当前 in-flight 轮有效。
- 浏览器验证在临时数据目录完成（ego-browser），未触碰真实数据目录。
