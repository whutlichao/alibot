<!-- 本文件是 GitHub issue #237 的正文与 Answer 留档；tracker 上的 issue 才是正本，以 tracker 为准。 -->

# Agent 家目录与全局层 —— 实施票拆分记录（#228 规格 → 7 张 tracer-bullet 实施票）

> 拆分票正本：https://github.com/whutlichao/alibot/issues/237 ｜ 父 issue：#228 ｜ 仓库：whutlichao/alibot ｜ 2026-10-22

Part of #228（规格正本：https://github.com/whutlichao/alibot/issues/228）。

**票型**：票据拆分 ｜ **门禁**：**G-tickets** ｜ **本票不跑**：源码测试（`pytest` / `ruff check` / `ruff format --check` / `mypy` / `pyright` / `node --test`）、G-impl、双轴 code-review —— 本票只产出 GitHub issue 与拆分记录文档，**不改任何源码、测试、迁移脚本或 CI**，不 bump `SCHEMA_VERSION`，不写实现。

**上游正本（全部已合入 `main`）**

- 规格正本：`app/specs/agent-home-layers-spec.md`（issue #228，PR #229 合并于 `e5f0028` 之前的规格分支）—— D1–D12 实施决策、T1–T6 测试口径、Out of Scope 15 条、N1–N7 备注。
- ADR：`docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md`。
- 设计正本：`app/specs/agent-home-layers-design-notes.md`（D1–D10 决策账、§3 加载顺序、§4 目录与创建纪律、§5 安全与后端边界、§6 迁移与备份、§7 A–G 切分建议、§8 Out of Scope、§9 C1–C18）。
- 术语：`CONTEXT.md` 的 **Agent 家目录 / 全局层 / 项目层 / 规则基座 / 项目补充 / 记忆落点 / 数据目录 / 备份 / 远端数据流告知**。
- issue 写法与原生依赖命令：`docs/agents/issue-tracker.md`。

---

## 1. 七张实施票（编号 → 簇 → 覆盖 → 直接门控）

> 每张实施票的正本在 tracker（下表「issue」列），本节只是拆分记录与指针。票面正文自足到「实施 worker 只看得到票也能开工」：每票都写了端到端行为、阻塞边与可机械判定的验收标准。

| # | issue | 簇 | 覆盖 | 直接门控（原生 `blocked_by`） |
| --- | --- | --- | --- | --- |
| ① | [#230](https://github.com/whutlichao/alibot/issues/230) | **规则三段拼装 + 全局根契约与只读守卫** | D1、D3、D6、D9、D11 Seam A | 无 |
| ② | [#232](https://github.com/whutlichao/alibot/issues/232) | **技能四层合并**（项目就近、同名覆盖） | D4 技能、D10 | #230 |
| ③ | [#233](https://github.com/whutlichao/alibot/issues/233) | **MCP 声明三层合并**（项目就近、同名覆盖） | D4 MCP、D8 装配前置、D10 | #230 |
| ④ | [#236](https://github.com/whutlichao/alibot/issues/236) | **全局远端 MCP 确认**（成员 + 名字） | D8 | #233 |
| ⑤ | [#234](https://github.com/whutlichao/alibot/issues/234) | **全局记忆只读**（两处读、写项目侧） | D5、D10 | #230 |
| ⑥ | [#235](https://github.com/whutlichao/alibot/issues/235) | **Docker 第二只读挂载**（成员家目录 + 公共技能库） | D7、D6 容器边界、D10 | #230 |
| ⑦ | [#231](https://github.com/whutlichao/alibot/issues/231) | **数据目录默认与兼容**（解析顺序 / 启动来源 / 备份文档） | D2、D9 数据目录行、D11 Seam B、D12 | 无 |

### 1.1 依赖图

```text
        ① #230 规则三段拼装 + 全局根/守卫
         │        D1 D3 D6 D9 D11-SeamA
         │
         ├── ② #232 技能四层合并            D4 技能 / D10
         ├── ③ #233 MCP 三层合并 ── ④ #236 全局远端 MCP 确认（D8）
         ├── ⑤ #234 全局记忆只读            D5 / D10
         └── ⑥ #235 Docker 第二只读挂载      D7 / D6 容器边界 / D10

        ⑦ #231 数据目录默认与兼容（独立 frontier）
                D2 / D9 数据目录行 / D11-SeamB / D12
```

frontier = **#230、#231**。图无环：全部边由 ①（与 ③）指向下游；②③⑤⑥ 互不依赖。

### 1.2 依赖边与读数（发布后实测）

依赖用 `docs/agents/issue-tracker.md` 的原生命令建立，`issue_id` 用 blocker 的 **database id**：

```sh
# ① → ②③⑤⑥
gh api --method POST repos/whutlichao/alibot/issues/232/dependencies/blocked_by -F issue_id=5972987732  # #230
gh api --method POST repos/whutlichao/alibot/issues/233/dependencies/blocked_by -F issue_id=5972987732  # #230
gh api --method POST repos/whutlichao/alibot/issues/234/dependencies/blocked_by -F issue_id=5972987732  # #230
gh api --method POST repos/whutlichao/alibot/issues/235/dependencies/blocked_by -F issue_id=5972987732  # #230
# ③ → ④
gh api --method POST repos/whutlichao/alibot/issues/236/dependencies/blocked_by -F issue_id=5972990482  # #233
```

| issue | `blocked_by` | `blocking` | 明细 |
| --- | --- | --- | --- |
| #230 | **0** | 4 | — |
| #231 | **0** | 0 | — |
| #232 | **1** | 0 | `[230]` |
| #233 | **1** | 1 | `[230]` |
| #234 | **1** | 0 | `[230]` |
| #235 | **1** | 0 | `[230]` |
| #236 | **1** | 0 | `[233]` |

七张实施票全部是 #228 的 **native sub-issue**（`gh issue create --parent 228`），全部 open，全部应用 `ready-for-agent`。**#228 的正文、标签与状态均未改动**。拆分票 #237 自身以正文首行 `Part of #228` 关联，不挂 sub-issue（与前例 #111 一致：它是拆分记录，不是实施切片）。

---

## 2. 覆盖矩阵（D1–D12 → 票）

| 决策 | 落点 |
| --- | --- |
| D1 两层归属与目录模型 | ① 全局根 / 成员家目录 / 规则、`sessions/` 不共享；② 公共技能库；③ 全局 `mcp.json`；⑤ 全局记忆；⑦ 数据目录 |
| D2 数据目录解析顺序 | ⑦ |
| D3 规则三段固定顺序 | ① |
| D4 技能与 MCP 同名合并 | ②（技能四层）、③（MCP 三层） |
| D5 记忆读写落点 | ⑤ |
| D6 只读守卫与权限边界 | ①（守卫、非文件工具根、三档不变）；⑥（容器根仍只有 `/workspace`） |
| D7 Docker 只读挂载 | ⑥ |
| D8 全局远端 MCP 确认 | ③（未确认不连接 + `consent_required`）、④（确认入口与持久化） |
| D9 创建纪律与降级表 | ① 立规；各票自守本层降级行；⑦ 守数据目录行与 `data/` 唯一例外 |
| D10 可观察读数总表 | ① system prompt / 段落标签 / 目录清单；②③ 胜出清单 / 落选提示；③④ `consent_required`；⑤ mtime；⑥ bind 列表；⑦ 启动输出 |
| D11 seam 选址 | ① Seam A（既有构造器新增注入参数）；⑦ Seam B（进程启动解析） |
| D12 备份与文档 | ⑦ |

**没有未被任何票覆盖的可实施决策。** Out of Scope 15 条不建票；每票正文的「不在本票范围」与规格 #228 的 Out of Scope 逐条对齐（不新增写端点、不写全局记忆、不自动迁移/创建、不共享 `sessions/`、不新增 `scope_kind`、不改 ADR、不做缓存策略、不存凭证、不新增权限档、不把全局层放进数据目录备份、不把数据目录挂进容器）。

---

## 3. 与设计笔记 §7 的三处偏差（逐条登记，不许静默改）

### 偏差 1 · A（全局层契约与守卫）折叠进 ①

- **设计笔记原划分**：A 独立成票，B / C / D / E 均依赖 A。
- **缺口**：A 只有常量、守卫与「缺失即缺席」的语义，**没有用户可观察的验收面**；只读守卫只有被真实读取路径使用时才验得动。把它单独成票，票面只能断言内部结构，违反 to-tickets 的 tracer-bullet 判据（每片可独立验证）。
- **处置**：A 折叠进 ①（规则三段拼装）作**前置 prefactor**；① 因此成为真正的 tracer bullet，并成为 ②③⑤⑥ 的全局根 / 只读守卫 / Docker 挂载点常量提供者。**coordinator 2026-10-22 人定**（Q2）。

### 偏差 2 · C（技能与 MCP 合并）拆成 ② 与 ③

- **设计笔记原划分**：C 一票同时覆盖全局技能装载与 `mcp.json` 解析。
- **缺口**：技能是「纯文件 + 四层合并 + 技能清单读数」，MCP 是「纯文件 + 三层合并 + 连接/确认读数」；两者的输入形状、装配结果、测试替身（stub provider vs 最小 stdio 子进程）都不同。合成一票会超出单一新鲜 context。
- **处置**：拆成 ②（技能）与 ③（MCP）。③ 另把「未确认不连接 + `consent_required`」留在装配票，人类面确认入口独立成 ④（沿用设计笔记「G 依赖 C」的边）。

### 偏差 3 · F 独立成 ⑦，与 ① 同为 frontier

- **设计笔记原划分**：F（数据目录默认与兼容）独立，无依赖。
- **处置**：保持独立。⑦ 与 ① 共享 `~/.alibot` 命名，但走不同 seam（进程启动 vs 轮次装配），互不等待；`data/` 是「应用不自动创建全局层」的唯一例外，票面已写明。

### 不改的部分

- **D11 的 seam 总数 = 2 不变**：① 新增的是 Seam A 既有构造器上的注入参数，不是第三个 seam；⑦ 是 Seam B。
- **零新增服务模块、零 schema 变更**：所有票都不得新增 `scope_kind`、不得 bump `SCHEMA_VERSION`。
- **术语不改**：票面只用 `CONTEXT.md` 的 **全局层 / 项目层 / Agent 家目录 / 规则基座 / 项目补充 / 记忆落点 / 数据目录 / 备份 / 远端数据流告知**，不另造同义词。

---

## 4. 切分粒度 Q → A（coordinator 裁决）

| 轮 | 问题 | 结论 | 归属 |
| --- | --- | --- | --- |
| Q1 | 7 票粒度与阻塞边是否同意 | **同意**；frontier = ①⑦，②③⑤⑥ ← ①，④ ← ③ | coordinator **人定** |
| Q2 | A 折叠进 ① 的理由是否接受 | **接受**；A 无独立可观察验收面，守卫必须在真实读取路径里才验得动 | coordinator **人定** |
| Q3 | 规则生效范围是否含 SOP | **单 Agent + team 队长 + team 成员三条路径都拼三段规则；SOP 执行器排除**（内部执行身份，不是同一个 Agent 在某个频道里的轮次） | coordinator **人定** |
| Q4 | 本票 Answer 落点 | **新建拆分票 issue #237 承接 Answer**，并仓库留档 `app/specs/agent-home-layers-tickets-answer.md` | coordinator **代答·推导** |

**自决的**（票面授权细节类）：票标题措辞、拆分记录的章节组织、`app/specs/` 下的文件名、各票「不在本票范围」的边界措辞。

---

## 5. 开工与验收读数

| 项 | 读数 |
| --- | --- |
| 开工第一件事 | `git fetch origin`（工作区已 clean，与 `origin/main` 一致） |
| 开工时 `origin/main` | **`e5f0028`**（PR #229 合并后） |
| 是否前进 | **未前进** —— 与票面基线一致，**无需 rebase** |
| `SCHEMA_VERSION` | **23**（`app/alibot/db.py:113`），本票**不 bump** |
| `docs/adr/` 最大编号 | **0031**，本票不新增 ADR、不改既有 ADR |
| tracker 标签 | `ready-for-agent` 已存在，直接应用；不新增其它状态标签 |
| 新建 issue | 拆分票 **#237** + 实施票 **#230 / #231 / #232 / #233 / #234 / #235 / #236**（共 8 张） |
| 原生依赖 | 5 条边（见 §1.2），读数 `0 / 0 / 1 / 1 / 1 / 1 / 1` |
| 零源码改动 | `git diff origin/main -- app/alibot/ app/tests/ docs/adr/ CONTEXT.md` **为空** |
| 本票 diff 面 | 只新增 `app/specs/` 下三份 Markdown（票图、Answer、PR 正文） |

---

## 6. Answer

Answer 的正本在 issue #237 的评论；同源留档见 `app/specs/agent-home-layers-tickets-answer.md`。要点：

1. 交付 = 拆分票 #237 + 7 张实施票 #230–#236 + 三份仓库留档；原生依赖 5 条边，frontier = #230、#231。
2. 四处 ask 回来的决定见 §4；三处与设计笔记 §7 的偏差见 §3。
3. 遗留：七张实施票均未开工；票面引用的规格行号以 #228 正文为准，实施时若行号漂移，以当时实际代码为准。
