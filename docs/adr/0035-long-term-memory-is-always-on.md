# 长期记忆始终开启：删掉开关，新建 Agent 立刻有家目录

用户于 2026-10-24 人定：**长期记忆没有开关**，所有 Agent 一律开启；**新建 Agent 时就把家目录建好**。
本 ADR 记录这一个决定（配置面、装配面、schema 迁移与家目录创建时机），不记录实施步骤。

本 ADR **取代** ADR-0034 §一里「`memory_mode` 的档位词表 `off` / `reme`」与「由成员显式开启」的口径，
也取代 `app/specs/agent-home-layers-design-notes.md` 中 D10 / C16 的旧读法「应用不创建家目录」。
ADR-0034 与设计笔记的正文**一字不改**（历史记录不回写）；冲突处以本 ADR 为准。

## 决策记录

- **Context**：ADR-0034 把长期记忆换到 ReMe，但保留了 `agent_config.memory_mode` 的 `off` / `reme`
  两档，默认 `off`。实际使用里这个开关没有产生过「用户真的想让某个成员没有长期记忆」的场景，反倒
  带来两处持续成本：**新建的 Agent 在文件系统上什么都没有**（家目录与 `reme/` 只在开启记忆的成员
  第一次跑轮次时才被顺手建出），以及**一条只表达「关」的持久化列 + API 字段 + Studio 复选框**要
  一起维护。记忆是成员的基础能力，不是逐成员的可选档位。
- **Decision**：删除 `agent_config.memory_mode`。装配层不再判档——**每一轮**只要全局层已接线就装配
  ReMe；`PATCH /api/agents/{id}` 不再接受该字段（`extra="forbid"` ⇒ 400）；Studio 详情页删掉复选框；
  运行环境段固定写「长期记忆：已开启」。`POST /api/agents` 成功后立刻建出
  `<全局根>/agents/<member_id>/` 与其中的 `reme/`。
- **Consequences**：schema 前进到 25（**单向门**：删持久化列 + 删 API 字段）；存量 `off` / `NULL`
  的成员在迁移后自动变为「始终开启」，行内其它列逐字保留。建目录失败**不**回滚、**不**让创建失败
  （成员行已落库，家目录不是对话的前提），只记一条可读告警。

## 一、配置面：没有档位

| 面 | 旧形态（ADR-0034） | 新形态 |
| --- | --- | --- |
| 库列 | `agent_config.memory_mode TEXT DEFAULT 'off' CHECK (… IN ('off','reme'))` | **无此列**（`24 → 25` 重建表删除） |
| 词表常量 | `db.MEMORY_MODES`（`memory.py` 再导出） | 删除 |
| `PATCH /api/agents/{id}` | `memory_mode` 是可写字段 | 字段不存在；带它的请求整体 400（`extra="forbid"`） |
| Studio 详情页 | 「长期记忆」复选框 + 两条词条 | 无控件、无词条；保存载荷不含该键 |
| 运行环境段 | `reme` → 已开启；其余 → 未开启 | 固定「长期记忆：已开启（ReMe…）」 |

删列沿用 SQLite 的唯一路径——重建表（与 `14 → 15`、`20 → 21`、`21 → 22`、`23 → 24` 同一形态）。
迁移步骤返回**重建前** `memory_mode IS NULL OR memory_mode <> 'reme'` 的行数（这些成员由「关」变为
始终开启）；列已不存在时重放返回 0。

## 二、装配面：每一轮都装

单 Agent、队长与 team 成员三条装配路径在走到记忆装配点时**无条件**调用 `assemble_memory`。
唯一会让记忆缺席的情形仍是**全局层未接线**（`global_home is None`）或工作区不可建（根外 symlink
祖先 / 无权限）：前者返回 `None` 并记一条可读日志，后者按「这一轮记忆缺席」降级，都不改变本轮结论。
SOP 参与者仍不挂工具、不装配记忆（既有边界不变）。

## 三、家目录：创建 Agent 即建出

`POST /api/agents` 成功（成员行落库）之后，应用立刻调用 `memory_workspace_dir(global_home, member_id)`：

- 建出 `<全局根>/agents/<member_id>/` 这一级与 `<...>/reme/` 工作区；
- **不**创建 `AGENTS.md`、**不**创建 `mcp.json`、**不**创建 `<全局根>/skills/`（ADR-0034 §一 的
  创建纪律不变）；
- 失败（`WorkspaceConfigError` / `OSError`）只记一条可读 `logging` 告警：**不**回滚成员行、
  **不**让 HTTP 创建失败。

存量成员不会在升级时被遍历建目录——迁移**不碰文件系统**；它们的家目录在下一轮装配记忆时由
`memory_workspace_dir` 自然建出（与本次改动前的「按需建」行为一致，只是现在每一轮都会走到）。

## 四、被取代的口径（正文不改，只在此处引用本 ADR）

- ADR-0034 §一：`off` / `reme` 两档与「由成员显式开启」；
- `app/specs/agent-home-layers-design-notes.md` D10 / C16：把「应用不创建家目录」读成创建纪律的一部分；
  创建纪律（不建 `AGENTS.md` / `mcp.json` / 公共技能库）仍有效，改变的是**家目录这一级本身**在
  创建 Agent 时就建出。
