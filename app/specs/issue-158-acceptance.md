# #158 验收：文档同步（README / CONTEXT / 术语与 D1–D23 索引）

- 票面：[#158](https://github.com/whutlichao/alibot/issues/158)（Part of #131）
- 票型 / 门禁：docs / G-docs
- 分支 / worktree：`issue-158-impl` / `/tmp/alibot-wt/issue-158-impl`（基线 `0e0c606`，收尾合并集成 tip）
- 只改文档：`app/README.md`、`CONTEXT.md`、`app/specs/agentscope-2-alignment.md`（§8.6 决策索引）、
  本文件。**未改** `app/alibot/**`、`app/tests/**`、`app/pyproject.toml`、`.github/**`。

## 判据 1：README 数据模型表与 `SCHEMA_VERSION=17` 一致；新端点 / 新 SSE / workspace 边界 / 备份 / 迁移写清

**落点**

- `app/README.md`「Schema 版本与迁移（15 → 17）」：`SCHEMA_VERSION = 17`、两个严格 +1 步骤、
  只加不删与旧行默认值、`--migrate` / `--dry-run` / 自动整目录备份 / 单事务回滚、**单向**与
  「回滚 = 恢复备份」、`agentscope==2.0.10`。
- `app/README.md`「数据模型（决策级）」：补 `target` / `target_member` / `target_read` /
  `target_budget` / `channel_budget_limit` / `team_run` / `team_assignment`；`channel` 补
  `mode` / `team_leader_member_id`；`task` 改为 `assignee_member_id` + `claimed_at`；
  `channel_member` / `channel_read` 两个旧名（ADR-0004 之前的叫法）改成 target 口径。
- `app/README.md`「team 模式：频道 + 任务线程的拓扑」与「Workspace / 权限 / 审批 / MCP / Skill」：
  team 覆盖 channel+task、只唤醒队长、认领/受派者唤醒停用、TeamAssign 计跳、队长不可用不回落、
  workspace_root 不在数据目录 / 需单独备份、Docker 无 daemon 时 offline 不回落、权限三档与
  `ToolResultState.DENIED`、MCP 只存 env 名、Skill 只接受本机目录/zip。
- `app/README.md`「实时呈现」：`round_outcome` 在 team 下只记队长；SSE `id: <seq>` 只给
  `message` / `round_outcome`，其余 live-only。
- `app/README.md`「HTTP 契约」：补 `GET /api/targets/{id}/plan`（其余本轮新端点原表已有）。

**命令与读数**

```console
$ cd app && ../.venv/bin/python - <<'PY'
import re
from pathlib import Path
from alibot.db import SCHEMA, SCHEMA_VERSION
tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)
readme = Path("README.md").read_text(encoding="utf-8")
print("SCHEMA_VERSION", SCHEMA_VERSION)
print("missing in README", [t for t in tables if t != "schema_meta" and t not in readme])
PY
SCHEMA_VERSION 17
missing in README []
```

27 张表（`schema_meta` 只存版本读数）逐个出现在 README 的数据模型表里。

SSE 词表与代码常量对齐：

```console
$ cd app && ../.venv/bin/python - <<'PY'
from pathlib import Path
from alibot.events import REPLAYABLE_TYPES, LIVE_EVENT_TYPES
readme = Path("README.md").read_text(encoding="utf-8")
print("replayable missing", [t for t in REPLAYABLE_TYPES if t not in readme])
print("live-only missing", [t for t in LIVE_EVENT_TYPES if t not in readme])
PY
replayable missing []
live-only missing []
```

## 判据 2：CONTEXT 词条更新并保留原意；新增团队/计划/SOP/工作区/权限词条带 `_Avoid_`；任务/认领/唤醒写清 team 口径

**落点**

- 新增 `### team 模式、Plan 与 SOP（本轮扩展）`：**team 模式** / **队长** / **TeamAssign / 分派** /
  **运行记录** / **Plan** / **SOP**。
- 新增 `### Workspace、权限与审批（本轮扩展）`：**工作区项目根** / **工作区后端** / **权限档** /
  **权限规则** / **审批** / **MCP 声明** / **Skill**。
- 既有词条**保留原意并标明扩展/收窄**：**Agent**（workspace 启用后才挂文件工具）、**唤醒**（team 只
  唤醒队长、task 不穿透）、**回应判断**（team 下 `claim` 不再出现）、**认领**（team 下不发生、
  `auto_claim` 是读路径规则）、**任务线程**（team 下只唤醒队长、TeamAssign 可用、任务行不变）、
  **成员面适配接缝**（`run_round_stream` 是流式出口，事件不是消息）、**远端数据流告知**
  （`remote_consent_scope` 覆盖 model / mcp / docker）、**备份**（项目根不在数据目录）、
  **数据迁移**（15→17、单向、回滚=恢复备份）。

**命令与读数**

```console
$ python3 - <<'PY'
import re
from pathlib import Path
text = Path("CONTEXT.md").read_text(encoding="utf-8")
terms = {}
for b in text.split("\n\n"):
    m = re.match(r"\*\*([^*]+)\*\*", b)
    if m:
        terms[m.group(1)] = "_Avoid_" in b
new = ["team 模式 (Team Mode)", "队长 (Leader)", "TeamAssign / 分派 (Team Assignment)",
       "运行记录 (Team Run / Assignment Record)", "Plan (Plan Run)", "SOP (SOP Run)",
       "工作区项目根 (Workspace Root)", "工作区后端 (Workspace Backend)",
       "权限档 (Permission Mode)", "权限规则 (Permission Rule)", "审批 (Approval / HITL)",
       "MCP 声明 (MCP Server)", "Skill"]
print("missing _Avoid_:", [t for t in new if not terms.get(t)])
print("entries:", len(terms), "without _Avoid_:", [k for k, v in terms.items() if not v])
PY
missing _Avoid_: []
entries: 76 without _Avoid_: []
```

## 判据 3：`test_docs_vocabulary_guard.py` 绿；D1–D23 可在规格中检索

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_docs_vocabulary_guard.py tests/test_backend_copy.py -o addopts="" -q
........................                                                 [100%]
24 passed in 1.26s
```

```console
$ rg -c 'D1|D23' app/specs/agentscope-2-alignment.md
67
$ for d in $(seq 1 23); do rg -q "D$d\b" app/specs/agentscope-2-alignment.md || echo "missing D$d"; done
（无输出 = D1–D23 全部可检索）
```

新增 `§8.6 决策索引（D1–D23 → 落点）`：每条只做指路（ADR / 代码与数据落点 / 文档落点），
**不复制、不改写** §1 / §1.1 / §1.2 的裁定原文。

## 判据 4：文档票与实施票分开；不改产品源码

```console
$ git diff --name-only
CONTEXT.md
app/README.md
app/specs/agentscope-2-alignment.md
$ git status --porcelain
?? app/specs/issue-158-acceptance.md
```

改动全部落在文档面；`app/alibot/**`、`app/tests/**`、`app/pyproject.toml`、`.github/**` 无 diff。

## 全量门禁

```console
$ ./scripts/check.sh
== pytest（全量）
827 passed, 9 skipped in 358.98s (0:05:58)
All checks passed!                       # ruff check + ruff format --check
Success: no issues found in 89 source files   # mypy
… pyright …
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 164
ℹ pass 164
ℹ fail 0
门禁全绿
```

退出码 `0`。本票只改文档，因此 pytest / mypy / pyright / node 的读数与改动前同集；
`test_docs_vocabulary_guard.py`（10 条：1 条段落存在性 + `round_outcome` 六态 + 三种消息 kind 各在
README 里）与 `test_backend_copy.py`（14 条）逐条通过。

合并集成分支 tip（`7bde01d merge(#156)`）后按纪律**复跑**：

```console
$ ./scripts/check.sh
827 passed, 9 skipped in 354.29s (0:05:54)
All checks passed!
Success: no issues found in 89 source files
ℹ tests 180
ℹ pass 180
ℹ fail 0
门禁全绿
```

Node 用例从 164 增至 180 是集成分支 #156 新增的 `channel_resources_ui.test.cjs`；pytest 集不变。

## 残余与说明

- **README 中 `{id}` 是本仓文档的既有占位写法**：代码里的路径参数名是 `{channel_id}` /
  `{target_id}` / `{definition_id}` / `{run_id}` / `{server_id}` / `{skill_id}` / `{rule_id}`，
  语义一一对应；本票沿用原表风格，没有为改名制造第二套写法。
- **预置任务 / 私信端点的整表**（`GET /api/tasks`、`PATCH /api/tasks/{id}/status` 等）不在本票
  增补范围：它们不是本轮新增端点，README 既有「唤醒/未读」段与 SSE 订阅行已覆盖其口径。
- 规格 `§8.6` 只追加索引；D1–D23 的裁定原文、§1 / §1.1 / §1.2 与既有 ADR 原文**逐字未改**。
