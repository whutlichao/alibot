# Answer —— AgentScope 2 对齐规格票（#131）

> 正本：https://github.com/whutlichao/alibot/issues/131 ｜ 规格：`app/specs/agentscope-2-alignment.md` ｜ 规格 PR：#160 ｜ 2026-10-18

## 1. 开工基线

| 项 | 读数 |
| --- | --- |
| 分支 | `agentscope2-spec`（从 `main` @ `826c96e` 切出） |
| `SCHEMA_VERSION` | **15**（`app/alibot/db.py`） |
| `agentscope` pin | **2.0.9**（`app/pyproject.toml`） |
| 本票写面 | `app/specs/**`（新文件）、`docs/adr/**`（新文件）；**不改** `app/alibot/**`、`app/tests/**`、`app/pyproject.toml`、`.github/**` |

## 2. 交付物

| 交付物 | 落点 |
| --- | --- |
| 规格（8 节，D1–D23） | `app/specs/agentscope-2-alignment.md` |
| ADR-0018 team 模式与成员平等 | `docs/adr/0018-team-mode-is-an-explicit-channel-topology.md` |
| ADR-0019 TeamAssign 计跳 | `docs/adr/0019-teamassign-consumes-one-jump.md` |
| ADR-0020 事件 / seam 扩展 | `docs/adr/0020-agent-events-are-live-and-messages-stay-the-record.md` |
| ADR-0021 Workspace / 权限边界 | `docs/adr/0021-workspace-is-an-explicit-project-boundary.md` |
| ADR-0022 TeamAssign 与任务五态 | `docs/adr/0022-teamassign-is-a-run-scoped-record.md` |
| ADR-0023 Plan/SOP 与任务五态 | `docs/adr/0023-plan-and-sop-are-not-tasks.md` |
| 规格锚点 issue | #131（Part of #1） |
| 子票 | #132–#159（28 张：研究 5、升版 1、schema/seam 4、team 4、Plan/SOP 2、Workspace 6、UI 3、迁移 1、文档 1、集成门 1） |
| 规格 PR | #160 |

## 3. 决策状态（必须如实登记）

- **D1–D12**：用户本轮口述固定，逐条在规格 §1 可检索。
- **D13–D17**：第一轮 `orchestration ask`，用户明确回复“全部选 A”。
- **D18–D23**：第二轮 `orchestration ask` 两次超时，**未收到回执**（messageId `msg_2e2c47de1dcf`；900s 与 600s 均超时）。规格 §1.2 / §8.3 已把它们标为**推荐默认 A，待 owner 确认**；实施前必须确认，若回执不同先改规格与相关票。
- 已用 `orca orchestration send --type escalation` 上报该阻塞（msg_684f8f7f652f）。

## 4. 门禁与证据

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
$ gh issue list --state open --search "Part of #131" --json number --jq 'length'
29    # 1 个规格锚点 + 28 个子票

$ gh api repos/whutlichao/alibot/issues/159 --jq '.issue_dependencies_summary.blocked_by'
27    # 集成门被全部实施/文档票阻塞

$ grep -c '^| \*\*D[0-9]' app/specs/agentscope-2-alignment.md
23    # D1–D23 逐条可检索
```

- **图无环**：本地 Kahn 拓扑排序覆盖全部 28 个子票；GitHub 原生 `blocked_by` 边已设置。
- **frontier 非空**：`#132`（R1）、`#133`（R2）、`#134`（R3）、`#135`（R4）、`#136`（R5）、`#137`（V1）。
- **未改产品源码**：`git status` 只显示规格/ADR 新文件；PR diff 不含 `app/alibot/**`、`app/tests/**`、`app/pyproject.toml`、`.github/**`。

## 5. 外部事实核对

- AgentScope 2.0.10 wheel 解包阅读：`pipeline/_team_pipeline.py`（`TeamPipeline` / `_run_assignments` / `TeamAssign`）、`event/_event.py`（`AgentEvent` 联合类型与 `reply_id`）、`middleware/_base.py`（`on_acting` 看不到 external execution）、`permission/*`、`workspace/*`、`sop/*`、`tool/_task/*`、`mcp/_mcp_client.py`。
- 官方 2.0.10 文档：团队流水线与 SOP 均明写“实验阶段，接口可能调整”；Workspace 七种后端与接口；Toolkit/MCP/Skill。
- 2.0.9 → 2.0.10 差异清单落在规格 §2.6 与 V1 票。

## 6. 遗留 / 下一步

1. **D18–D23 待 owner 确认**（阻塞实施，不阻塞本规格 PR）。
2. 实施从 frontier 开始：V1、R1–R5；E1/E2 依赖对应研究票。
3. 集成 PR 必须保持开放，由人类手动合并（D8）；本票不创建实施 PR。
