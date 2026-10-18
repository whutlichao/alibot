# Answer —— AgentScope 2 对齐规格票（#131，修订）

> 正本：https://github.com/whutlichao/alibot/issues/131 ｜ 规格：`app/specs/agentscope-2-alignment.md` ｜ 规格 PR：#160 ｜ 2026-10-18

## 1. 开工基线

| 项 | 读数 |
| --- | --- |
| 分支 | `agentscope2-spec`（从 `main` @ `826c96e` 切出） |
| `SCHEMA_VERSION` | **15**（`app/alibot/db.py`） |
| `agentscope` pin | **2.0.9**（`app/pyproject.toml`） |
| 本票写面 | `app/specs/**`、`docs/adr/**`、GitHub Issues #131–#159；**不改** `app/alibot/**`、`app/tests/**`、`app/pyproject.toml`、`.github/**` |

## 2. 交付物

| 交付物 | 落点 |
| --- | --- |
| 规格（8 节，D1–D23） | `app/specs/agentscope-2-alignment.md` |
| ADR-0018 team 模式与成员平等 | `docs/adr/0018-team-mode-is-an-explicit-channel-topology.md` |
| ADR-0019 TeamAssign 计跳 | `docs/adr/0019-teamassign-consumes-one-jump.md` |
| ADR-0020 事件 / seam 扩展 | `docs/adr/0020-agent-events-are-live-and-messages-stay-the-record.md` |
| ADR-0021 Workspace / 权限边界 | `docs/adr/0021-workspace-is-an-explicit-project-boundary.md` |
| ADR-0022 TeamAssign / team 任务线程与任务五态 | `docs/adr/0022-teamassign-is-a-run-scoped-record.md` |
| ADR-0023 Plan/SOP 与任务五态 | `docs/adr/0023-plan-and-sop-are-not-tasks.md` |
| 规格锚点 issue | #131（Part of #1） |
| 子票 | #132–#159（28 张；本轮修订未新增票，理由见 §6） |
| 规格 PR | #160 |

## 3. 决策状态（人定）

- **D1–D12**：用户本轮口述固定，逐条在规格 §1 可检索。
- **D13–D17**：第一轮 `orchestration ask`，用户明确回复“全部选 A”。
- **D18–D22**：第二轮 `orchestration ask`，用户最终回复 **全部 A**（messageId `msg_b065b796831b` / `msg_2e2c47de1dcf`）。
- **D23**：用户最终明确选 **B**（覆盖原推荐 A）：team 模式覆盖该频道的 **channel 型 target 与全部 task target**；**任务线程只唤醒队长**；既有任务五态保留，但**认领与受派者唤醒在 team 模式任务线程中不再是唤醒路径**；`auto_claim` 已按 D16 全关；`TeamAssign` 在任务线程可用并记入运行期分派。

## 4. 本次修订（D23 = B）

| 文件 / 票 | 改动 |
| --- | --- |
| 规格 §1.2 | D18–D23 从未收到回执的暂定值改为“已由用户裁定”；D23 按 B 重写 |
| 规格 §1 表头 / §8.3 | 删除超时与未确认警告；记录 `msg_b065b796831b` / `msg_2e2c47de1dcf` 与最终选择 |
| 规格 §2.1 / §2.7 / §2.8 | 补 task target 的三条既有唤醒路径；ADR-0005/0010/0016 的 team 覆盖与保留 |
| 规格 §3.3 / §3.4 | 生命周期扩到 task target；运行矩阵新增 task 行；task target 用自己的预算；task 不穿透 |
| 规格 §3.5 | Plan 在 team 任务轮次可用；SOP 可挂 task target，但不是任务轮次；TeamAssign 成员无任务上下文 |
| 规格 §4.4 / §4.5 / §4.6 / §4.7 | `team_scope=channel+task`；task SSE 产生 team 事件；任务视图共用频道配置；破坏性变更补 task 线程 |
| 规格 §5 / §6 | 新增 task 线程唤醒/认领/五态/自审/预算验收；删除未决项；新增 task 线程风险 |
| 规格 §7 | T1/T2/T3/T4/E3/E4/U1/U2/DOC1 的验收与作用域同步 |
| ADR-0022 | **整篇重写**：team 覆盖 channel + task target；谁唤醒、谁维护五态、认领为何不生效、`in_review → done` 谁触发；对 ADR-0005/0010/0016 的覆盖/保留 |
| ADR-0018 | 最小修订：team 覆盖 channel + task target；task 线程只唤醒队长、任务 target 不穿透 |
| ADR-0019 | 最小修订：task target 用自己的 `target_budget`，不推进频道预算 |
| ADR-0021 | 最小修订：删除 D19 的未确认字样（D19 已裁定） |
| ADR-0023 | 最小修订：Plan 在 team 任务轮次可用；SOP 可挂 task target |
| #131 | 决策基线改为 D1–D23 已裁定；D23=B |
| #142 | 作用域改为 channel + task target；新增 task 线程只唤醒队长、task 不穿透、owner 改派/转派唤醒队长、创建不认领的验收 |
| #145 | 重写为 team 任务线程：只唤醒队长、TeamAssign 可用、五态由 owner/队长工具维护、自审规则、task 预算 |
| #144 / #143 / #141 / #140 | 补 task 线程可见性 / task 预算 / task SSE / task target 流式验收 |
| #154 / #155 / #158 | 补 `team_scope`、任务视图共用配置、任务运行面板、文档口径 |
| #147 / #149 / #153 | 只删除未确认提示行；行为验收不变 |
| #159 | 未改依赖：仍 blocked_by 全部 27 张实施/文档票（无新增票） |

## 5. 门禁与证据

```console
$ ./scripts/check.sh --fast
pytest（--fast 且未指定文件：跳过；需要时把路径跟在 --fast 后面）
ruff check            → All checks passed!
ruff format --check   → 139 files already formatted
mypy（仓库根）        → Success: no issues found in 61 source files
pyright               → 0 errors, 0 warnings, 0 informations
node --test（glob）   → tests 132 / pass 132 / fail 0
门禁全绿
```

```console
$ grep -c '^| \*\*D[0-9]' app/specs/agentscope-2-alignment.md
23    # D1–D23 逐条可检索

$ gh issue list --state open --search "Part of #131" --json number --jq 'length'
29    # 1 个规格锚点 + 28 个子票

$ gh api repos/whutlichao/alibot/issues/159 --jq '.issue_dependencies_summary.blocked_by'
27    # 集成门仍被全部实施/文档票阻塞
```

- **图无环**：本地 Kahn 拓扑排序覆盖全部 28 个子票；GitHub 原生 `blocked_by` 边已设置。
- **frontier 非空**：`#132`（R1）、`#133`（R2）、`#134`（R3）、`#135`（R4）、`#136`（R5）、`#137`（V1）。
- **未改产品源码**：PR diff 只含 `app/specs/**` 与 `docs/adr/**`。
- **最终提交**：以 PR #160 的 head 与分支 tip 为准；两者一致，分支已推送、工作树干净。

## 6. 为什么没有新增 schema/API 票

Q11=B 的“task target 只唤醒队长”不需要新表或新端点：

- 拓扑由 `task.channel_id -> channel.mode` 解析，`task` 表**不新增 mode 列**；
- 唤醒分支落在既有 `notify` / `notify_task_assignment` / `notify_task_reassignment` / `claim_new_task`；
- 前端通过既有 `GET /api/channels/{id}/config` 新增的 `team_scope=channel+task` 字段读取作用域，不需要新端点；
- `TeamAssign` 已有的 `team_assignment.task_id` 足够表达 task 线程的运行时派。

因此票图不新增票；#142 承载 task target 的唤醒分支与 `team_scope`，#145 承载 task 线程的 TeamAssign/五态验收，#159 的 blocked_by 不变。

## 7. 外部事实核对

- AgentScope 2.0.10 wheel 解包阅读：`pipeline/_team_pipeline.py`（`TeamPipeline` / `_run_assignments` / `TeamAssign`）、`event/_event.py`（`AgentEvent` 联合类型与 `reply_id`）、`middleware/_base.py`（`on_acting` 看不到 external execution）、`permission/*`、`workspace/*`、`sop/*`、`tool/_task/*`、`mcp/_mcp_client.py`。
- 官方 2.0.10 文档：团队流水线与 SOP 均明写“实验阶段，接口可能调整”；Workspace 七种后端与接口；Toolkit/MCP/Skill。
- 2.0.9 → 2.0.10 差异清单落在规格 §2.6 与 V1 票。

## 8. 遗留 / 下一步

1. 实施从 frontier 开始：V1、R1–R5；E1/E2 依赖对应研究票。
2. team 任务线程的验收集中在 #142 / #145；#159 仍为人类手动合并的一向门。
3. 跨重启 HITL 恢复、远程 MCP/Skill 市场、本地 OS 级沙箱仍不在本轮。
