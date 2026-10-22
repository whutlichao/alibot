<!-- 本文件是 GitHub issue #237 的 Answer 评论留档；tracker 上的评论才是正本，以 tracker 为准。 -->

# Answer —— 拆分票 #237（Agent 家目录与全局层的实施票拆分）

> 正本：https://github.com/whutlichao/alibot/issues/237 ｜ 父 issue：#228 ｜ 仓库：whutlichao/alibot ｜ 2026-10-22

## 1. 交付物

| 对象 | 动作 |
| --- | --- |
| GitHub issue **#237**（拆分票，本票） | **新建**：首行 `Part of #228`、标签 `ready-for-agent`；正文即拆分记录 |
| GitHub issue **#230** = ① | **新建**：规则三段拼装 + 全局根契约与只读守卫，`blocked_by 0`（frontier） |
| GitHub issue **#231** = ⑦ | **新建**：数据目录默认与兼容，`blocked_by 0`（frontier） |
| GitHub issue **#232** = ② | **新建**：技能四层合并，`blocked_by 1`（`[230]`） |
| GitHub issue **#233** = ③ | **新建**：MCP 声明三层合并，`blocked_by 1`（`[230]`） |
| GitHub issue **#234** = ⑤ | **新建**：全局记忆只读，`blocked_by 1`（`[230]`） |
| GitHub issue **#235** = ⑥ | **新建**：Docker 第二只读挂载，`blocked_by 1`（`[230]`） |
| GitHub issue **#236** = ④ | **新建**：全局远端 MCP 确认，`blocked_by 1`（`[233]`） |
| `app/specs/agent-home-layers-tickets.md` | **新增**：拆分记录（票图 → 覆盖矩阵 → 偏差登记 → Q/A → 读数） |
| `app/specs/agent-home-layers-tickets-answer.md` | **新增**：本 Answer 留档 |
| `app/specs/agent-home-layers-tickets-pr-body.md` | **新增**：PR 正文正本（`pr` skill 体例，`--body-file` 用） |
| **PR** | **新建**：base `main` ← `agent-home-layers-tickets`，body = 上述 PR 正文（编号创建后回填 §6） |

七张实施票全部是 #228 的 **native sub-issue**，全部 open，全部挂 `ready-for-agent`。**#228 正文、标签与状态未改动**。

## 2. 原生依赖读数（`issue_dependencies_summary.blocked_by`）

| issue | 票 | `blocked_by` | `blocking` | 明细 |
| --- | --- | --- | --- | --- |
| #230 | ① | **0** | 4 | `[]` |
| #231 | ⑦ | **0** | 0 | `[]` |
| #232 | ② | **1** | 0 | `[230]` |
| #233 | ③ | **1** | 1 | `[230]` |
| #234 | ⑤ | **1** | 0 | `[230]` |
| #235 | ⑥ | **1** | 0 | `[230]` |
| #236 | ④ | **1** | 0 | `[233]` |

依赖用 `gh api --method POST repos/whutlichao/alibot/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>` 建立；database id 实测：#230 = `5972987732`、#233 = `5972990482`。frontier = **#230、#231**；图无环。

## 3. 哪些是 ask 回来的（四处，全部经 `orca orchestration ask`）

| # | 问题 | 结论 | 归属 |
| --- | --- | --- | --- |
| Q1 | 7 票粒度与阻塞边是否同意 | **同意**；frontier = ①⑦，②③⑤⑥ ← ①，④ ← ③ | coordinator **人定** |
| Q2 | 设计笔记 §7 的 A（全局层契约与守卫）是否单独成票 | **折叠进 ①**：A 无独立可观察验收面，只读守卫必须在真实读取路径里才验得动 | coordinator **人定** |
| Q3 | 规则生效范围是否含 SOP | **单 Agent + team 队长 + team 成员三条路径都拼三段规则；SOP 执行器排除**（内部执行身份，不是同一个 Agent 在某个频道里的轮次） | coordinator **人定** |
| Q4 | 本票在 tracker 上没有对应 issue，Answer 落点 | **新建拆分票 issue #237 承接 Answer**，并仓库留档 `app/specs/agent-home-layers-tickets-answer.md` | coordinator **代答·推导** |

**自决的**（票面授权细节类）：票标题措辞、拆分记录的章节组织、`app/specs/` 下的文件名、各票「不在本票范围」的边界措辞、sub-issue 与拆分票不挂父子关系的处理。

## 4. 与设计笔记 §7 的三处偏差

| 偏差 | 处置 | 理由 |
| --- | --- | --- |
| A 独立成票 | 折叠进 ① | A 只有常量与守卫语义，没有用户可观察的验收面；单独成票无法独立验证 |
| C 一票覆盖技能 + MCP | 拆成 ②（技能）与 ③（MCP），确认入口独立为 ④ | 输入形状、装配结果、测试替身都不同；合成一票超出单一新鲜 context |
| F 独立 | 保持独立（⑦），与 ① 同为 frontier | 走不同 seam（进程启动 vs 轮次装配），互不等待 |

D11 的 seam 总数 = 2 不变：① 是 Seam A 既有构造器上的新增注入参数，⑦ 是 Seam B；零新增服务模块、零 schema 变更。

## 5. 覆盖自查

| 判据 | 读数 |
| --- | --- |
| 开放 issue 全部以 #228 为 parent 或正文带 `Part of #228` | 七张实施票为 native sub-issue；拆分票 #237 首行 `Part of #228` |
| 每票有标题 / `What to build` / `Blocked by` / 可勾选验收标准 | 七张实施票逐项具备 |
| blocker 指向真实存在的票；图无环；frontier 至少一张 | 5 条边全部指向已创建票；frontier = #230、#231 |
| D1–D12 全覆盖 | 见拆分记录 §2；无遗漏的可实施决策 |
| 零源码 / 测试 / 迁移 / CI 改动；`SCHEMA_VERSION` 未 bump | `git diff origin/main -- app/alibot/ app/tests/ docs/adr/ CONTEXT.md` 为空；`SCHEMA_VERSION = 23` |
| PR 正文含 `Door` 与 `Blast Radius` | 见 `app/specs/agent-home-layers-tickets-pr-body.md`：`Door` = two-way，`Blast Radius` = docs-only |

## 6. PR 与提交

| 项 | 读数 |
| --- | --- |
| 分支 | `agent-home-layers-tickets` |
| 本票 diff 面 | 只新增 `app/specs/` 下三份 Markdown |
| 提交 | `c3cf4b6`（父提交 = `e5f0028`，即 `origin/main`） |
| PR | **#238**（base `main`，body = `app/specs/agent-home-layers-tickets-pr-body.md`） |

## 7. 遗留

- 七张实施票均**未开工**；frontier 当前是 **#230（①）与 #231（⑦）**。
- 票面引用规格的 D / T / C 编号，不复制规格正文；实施时以 #228 的正文与 `app/specs/agent-home-layers-spec.md` 为准。
- 规格 N7 列的未核实项（全局规则缓存策略、Docker 容器内子路径命名、人类面确认入口形态）仍属实施票的机制选择；本票只在 ⑥ 与 ④ 的验收里固定了可观察行为，不预设实现。
