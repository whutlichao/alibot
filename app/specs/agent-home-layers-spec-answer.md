<!-- 本文件是 GitHub issue #228 的 Answer 评论留档；tracker 上的评论才是正本，以 tracker 为准。 -->

# Answer —— 规格票 #228（Agent 家目录与全局层）

> 正本：https://github.com/whutlichao/alibot/issues/228 ｜ 仓库：whutlichao/alibot ｜ 2026-10-22

## 1. 交付物

| 文件 / 对象 | 动作 |
| --- | --- |
| GitHub issue **#228** | **新建**：to-spec 七节模板、标签 `ready-for-agent`、正文即规格正本 |
| `app/specs/agent-home-layers-spec.md` | **新增**：issue 正文的逐字留档（首行为正本 URL；与 tracker 正文 byte-identical） |
| `app/specs/agent-home-layers-spec-answer.md` | **新增**：本 Answer 留档 |
| PR **#229** | **新建**：base `main`，head `agent-home-layers-spec`；body 走 `--body-file` |

**零源码改动**：未改任何 `app/alibot/**`（含 `studio/**`）、未改任何 `app/tests/**`、未写迁移代码、未改 CI；**未 bump `SCHEMA_VERSION`**（仍为 **23**）；**未新增 ADR**、**未改既有 ADR**、**未改设计笔记与 `CONTEXT.md`**。

## 2. 开工时实际基线（如实登记）

| 项 | 读数 |
| --- | --- |
| 开工第一件事 | `git fetch origin` |
| 开工时 `origin/main` | **`6ea5e9b`**（PR #227 合并后） |
| `origin/main` 是否前进 | **未前进**；本票 HEAD 起点即 `6ea5e9b`，无需 rebase |
| `SCHEMA_VERSION` | **23**（`app/alibot/db.py:59`），未 bump |
| `docs/adr/` 最大编号 | **0031**（ADR-0031 已合入），本票不新增 ADR |
| 设计笔记 §9 | **C1–C18 共 18 条**，本规格逐条落点见规格 §N3 映射表 |
| tracker 标签 | `ready-for-agent` **已存在**，直接应用 |
| 工作区 | 开工时 clean；交付前只新增 `app/specs/` 下两份 Markdown |

## 3. to-spec 阶段的侦察（只读取证）

按 `to-spec` 步骤 1–2，先用 CodeGraph 探索现有装配层，再提 seam 候选。全部只读，未写入任何源码。

| 侦察面 | 结论（本票实测） |
| --- | --- |
| 轮次装配 seam | 成员面适配接缝的依赖注入构造器已在 `create_app` 注入 `workspace_of` / `permission_of` / `mcp_of` / `skills_of` / `web_of` / `locale_of`；这是本能力新增全局读路径钩子的既有落点 |
| 工作区装配 | 本地与 Docker 装配函数把项目根、成员分区与会话分区收在 `workspace` 层；状态分区守卫在创建前拒绝根外 symlink |
| Docker 挂载 | 容器配置由纯函数生成，当前只有一条 `host_workdir:/workspace:rw`；网络策略测试证明「配置 dict 层断言不需要 daemon」 |
| 记忆装配 | `assemble_memory` 以 `AdditionalWorkingDirectory` 挂只读目录，已有 `read_only_dirs` 机制可复用 |
| 技能装配 | `load_skills` 校验 `SKILL.md`、路径与 hash，坏条目跳过；`test_skill_assembly.py` 用真 seam + stub provider 观测系统提示与工具清单 |
| MCP 装配 | `assemble_mcp_tools` 对未确认远端「不建连」；`test_mcp_assembly.py` 用最小 stdio 子进程与传输替身取证 |
| 数据目录 | 默认值由 `default_data_dir()` 解析，`--data-dir` / `ALIBOT_DATA_DIR` 覆盖；进程启动路径已有 CLI 子进程测试先例 |
| 测试先例 | `test_local_workspace.py`（481 行）、`test_docker_workspace.py`（633 行）、`test_skill_assembly.py`（294 行）、`test_mcp_assembly.py`（519 行）；另有 `test_runtime_memory.py`、`test_docker_network_policy.py`、`test_migrate_cli.py` |

## 4. Seam 选址：Q → A → 理由

**Q（本票 ask，原文要点）**：不新开 seam，复用两处既有装配点——A 轮次装配 seam（`AgentScopeAgentSeam` 依赖注入 + 公开装配函数），B 进程启动 seam（数据目录解析）；C 全局远端 MCP 确认不单列。被否：独立 GlobalLayer 服务模块 / 只测 HTTP 面 / 把 DB 读路径当 seam。

**A（coordinator 裁决，原文要点）**：

1. **同意 seam 总数 = 2（A 轮次装配 + B 进程启动），全部复用既有装配点，零新增服务模块。**
2. A 中为全局层新增的读路径钩子记为**既有构造器上的新增注入参数**，不是新 seam；语义必须是只读、目录缺失即该层缺席、应用不自动创建或写入、不进入文件工具根。
3. B 独立保留；测试用临时目录覆盖四种解析顺序，并断言「绝不自动搬移」。
4. C 不单列；确认断言复用既有 `remote_consent_scope('mcp', scope_id)` 与 `assemble_mcp_tools` 的「未确认不连接」读数面。
5. 三个被否方案同意，记入 Testing Decisions / Further Notes。

**理由（写入规格）**：A 是成员面唯一的装配入口，规则、技能、MCP、记忆与 Docker 挂载的可观察后果都在它之内；B 发生在任何轮次之前，无法折进 A；C 是 A 的输出后果与既有读数面，不产生新的注入点。被否方案与代价见规格 T5。

## 5. 可观察验收自查（逐条对照票面）

| 票面验收 | 落点 | 读数 |
| --- | --- | --- |
| GitHub issue 已创建，正文严格按 to-spec 七节模板，标签 `ready-for-agent` | issue **#228** | 七节各 1 次；标签已应用 |
| `app/specs/agent-home-layers-spec.md` 存在，首行注明正本 URL，正文与 issue 逐字一致 | 新增文件 | 首行 = 正本 URL 注释；`gh issue view 228 --json body` 与文件 **byte-identical** |
| User Stories 覆盖规则拼装、技能/MCP 四层优先级、记忆读写、Docker 只读挂载、数据目录解析顺序、目录缺失降级、全局远端 MCP 确认 | 规格 §User Stories A–H | **65 条**，逐组覆盖 |
| Out of Scope 非空，含设计笔记 §8 条目 | 规格 §Out of Scope | **15 条**，前九条对应设计笔记 §8，逐条带理由 |
| 无「待定 / TBD」占位 | 全文 | `待定` / `TBD` / `TODO` 均 **0** |
| seam 候选已经 coordinator 确认，Answer 记录 Q → A → 理由 | 本文件 §4 + 规格 §D11 / §N6 | 已记录 |
| 零源码 / 测试 / 迁移 / CI 改动；`SCHEMA_VERSION` 未 bump | `git diff origin/main -- app/alibot app/tests` 与 `SCHEMA_VERSION` | diff 为空；仍为 **23** |
| 项目文件有改动时用 `pr` skill 编写 PR body、推分支、`--body-file` 创建 PR | PR **#229** | body 走 `--body-file`；Door = two-way；Blast Radius = docs-only |
| PR 号及 body 路径回填 Answer | 本文件 §7 | 已回填 |

## 6. 读数

| 项 | 读数 |
| --- | --- |
| 提交序列 | `27a4857`（规格留档）→ 本 Answer 提交；均只含 `app/specs/*.md` |
| 规格文件 | `app/specs/agent-home-layers-spec.md`（470 行，42,481 bytes，与 issue 正文 byte-identical） |
| 规格结构 | 七节齐全；65 条 User Stories；15 条 Out of Scope；12 条 Implementation Decisions（D1–D12）；6 条 Testing Decisions（T1–T6）；C1–C18 映射 18 行 |
| 代码块 | **0**（规格无草案代码块，数据形态全部用文字与表格） |
| 源码 / 测试 / 迁移 / CI | **零改动** |
| `SCHEMA_VERSION` | **23**，未 bump |
| PR | **#229** — https://github.com/whutlichao/alibot/pull/229 |

## 7. PR 号与 body 路径回填

- PR：**#229** — https://github.com/whutlichao/alibot/pull/229
- body 路径：`.scratch/agent-home-layers-spec-pr-body.md`（`.scratch/` 被 `.gitignore` 忽略，不入库）
- `state=OPEN`，`base=main`，`head=agent-home-layers-spec`
- PR 正文要点：**Door = two-way**（docs + tracker）；**Blast Radius = docs-only**

## 8. 偏差登记

| 项 | 情况 | 处置 |
| --- | --- | --- |
| 票面给的行号 / 落点 | 本规格按票面要求**不在 Implementation Decisions 写文件路径**；只在基线、侦察与 Testing Decisions 引用已核实的测试文件名与行数 | 无偏差 |
| `CONTEXT.md` | 上游已落齐本能力词条，本票**零改动** | 符合「不重写已定决策」 |
| 设计笔记 §7 切分 | 本规格只在 §N5 指向它，不重新拆票、不建票 | 符合「不拆实施票」 |
| 规格与 issue 正文 | 首次创建后回填正本 URL 并 `gh issue edit`；随后以 `gh` 读回逐字比对 | **byte-identical** |
