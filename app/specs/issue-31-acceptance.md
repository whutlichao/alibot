# 规格票 ㉛ 验收取证（issue #31：回应预算为什么是 3、能不能改）

本文件是 Answer 的支撑材料：只记**跑过后出现的事实**、实际读过的文件清单、以及「改数是否必须新 ADR」的判据。
规格正本在 GitHub issue tracker：**[#31](https://github.com/whutlichao/alibot/issues/31)**（本票唯一产物）。
本文件与 `app/specs/issue-31-spec.md`（issue 正文逐字留档）都在 worktree 内，便于离线核对。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-budget-config-spec`（Orca 独立顶层 worktree）
- 基线：`origin/main` 的 `9fd31bb`（#22 第二版规格合并后的 main）
- 采集时间：2026-10-14
- 票型：**规格票**，门禁 **G-spec**（本票不跑源码测试、不跑双轴 code-review、不跑 G-impl）
- 前置 skill：`/Users/apple/.agents/skills/to-spec/SKILL.md`（开工前完整读，按其流程执行）

## 0. 前置 skill 的执行（`to-spec`）

`to-spec` 规定四步，逐条落点：

1. **探索仓库理解现状**（不访谈用户，只综合已知）→ §1 的只读取证清单。
2. **勾出测试接缝，优先既有接缝** → 规格 Testing Decisions：沿用既有唯一高层接缝（HTTP 黑盒 + 手动调度器 +
   脚本化替身，`app/alibot/api.py:145-157`），**不新开接缝**；成本实测接缝是"真实模型、默认跳过、本机手动
   运行"，也与 #1 既有做法一致。
3. **用模板七节写规格并发布到 issue tracker，标 `ready-for-agent`** → §2 的建票回执（七节顺序与标题逐字
   照模板，未自由发挥章节、无草案代码块）。
4. 项目 glossary 词汇与 ADR 纪律 → 规格全篇沿用 `CONTEXT.md` `## Language` 的中文术语，并在 Further Notes
   写明不回退到 `_Avoid_` 词；与 ADR-0002 的冲突用"必须新 ADR 显式推翻"处理（§3）。

## 1. 实际读过的仓库文件与来源（只读取证，零修改）

代码（**全部只读**，未做任何编辑）：

| 文件 | 读它的目的 | 用到的事实 |
| --- | --- | --- |
| `app/alibot/db.py` | 上界常量、预算表、读取/扣款/世代三个方法、重置落点、数据目录 override | `BUDGET_LIMIT = 3`（:48）；`CREATE TABLE channel_budget`（:109-114）；`budget_state`（:781）；`human_epoch_seq`（:802）；`consume_budget`（:813）；人类消息落库即重置（:728-736）；`round_outcome` 表（:138）；`append_round_outcome` 的"不可求和"docstring（:461, :470-480）；`default_data_dir()`（:238-243） |
| `app/alibot/orchestration.py` | 跳数唯一写点与耗尽记录 | `from .db import BUDGET_LIMIT`（:50）；`_spend_jump`（:234，判定 :249-250，扣款 :253，零投递判据 :246-248）；`_record_budget_exhausted`（:256，reason 文案 :262-265）；模块 docstring 的"跳数单一正本"（:28） |
| `app/alibot/api.py` | 只读端点与 404 兜底 | `GET /api/channels/{id}/budget`（:272，注释"只读，无 UI"）；`PUT/PATCH/DELETE/POST /budget` → 404（:397-404）；`create_app` 的 `scheduler`/`seam` 注入点（:145-157） |
| `app/alibot/service.py` | 只读端点的服务层落点 | `channel_budget`（:146-151） |
| `app/alibot/agent_seam.py` | 替身"不联网、零成本"与 `Msg.usage` 取证面 | 薄接缝理由（:10-12）；`read_api_key` 的 env 窄 seam（:152-169）；`usage`/`finished_reason` 不构成消息正本（:179-180）；Ollama 无需凭证（:29-31） |
| `app/tests/test_bounded_relay.py` | 把 3 当硬断言的既有用例 | `used == 3`（:194）；整块 dict 含 `limit: 3`（:285-290）；`:331`；`used == 0 and remaining == 3`（:336）；`:488`；`:499`；重启持久性读数（:471）；测试名写死 three（:258）、重置用例名（:314） |
| `app/alibot/studio/app.js` | 确认前端今天**没有**预算面（无读数、无控件） | 仅 `budget-exhausted` 的轮次结果文案（:57-58, :70-72），无预算读数/写入口 |
| `app/alibot/studio/index.html` | 同上 | `grep -c budget` = 0 |
| `app/alibot/design system`（`alibot-design-system/`） | 确认原型没有预算控件 | `grep -rn "budget\|预算"` 零命中（只读参照） |

文档与 tracker：

| 来源 | 用到的事实 |
| --- | --- |
| `CONTEXT.md` `## Language` | 五条词条写死 3：**人类世代**（:138-143，"重置为 3"）、**回应预算**（:145-149，"最多 3 次"）、**跳**（:151-155）、**预算耗尽**（:157-161，"已用满 3 次跳"）、**预算表**（:163-167） |
| `docs/adr/0002-response-budget-caps-relay-at-three.md` | 标题即结论（`at three`）；取舍段写明"为什么选零投递"；"跳数的正本是 `channel_budget.used`" |
| `docs/adr/0005-dispatching-a-task-is-a-human-utterance.md` | 该 ADR 是 ADR-0002 的**应用**（改指派人 = 人类世代 + 重置 3），因此改数会连带影响它的措辞 |
| `gh issue view 1` | 「用户已确认的边界」第 2 条原文（规格 Problem Statement 逐字引用）：策略参数来源 |
| `gh issue view 23` | 决策 G2（`target_budget` 机制逐字不变、≤3）与正文「跳数上界 ≤ 3」措辞 → 规格列为**待同步项**（本票不授权写 #1/#23） |
| `docs/agents/issue-tracker.md` / `docs/agents/triage-labels.md` / `docs/agents/domain.md` | 建票命令、`ready-for-agent` 标签词表、domain docs 的 ADR 冲突必须显式上报纪律 |
| `app/specs/issue-22-design-notes.md` / `issue-22-pr-body.md` / `issue-13-acceptance.md` | 本仓同类票的取证范式（本节的结构即沿用） |

## 2. 建票回执（真实命令与真实输出）

新票建为 **#1 的 sub-issue**（按 coordinator 的裁定，与 #2/#3/#4/#8/#9/#12/#13/#14 同挂 `#1`；
与 #24–#28 挂 `#23` 的形态不同，因为本票是 #1 §5 暴露出的缺口）。

```console
$ gh --version
gh version 2.95.0 (2026-06-17)

$ gh issue create --parent 1 \
    --title "规格票：回应预算为什么是 3、能不能改——预算可配置（页面可改）+ 档位重定 + ADR 实测依据 + 五条词条与 ADR-0002 的处置" \
    --body-file /tmp/issue-budget-spec-body.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/31

$ gh issue view 31 --json number,title,labels,state,url,subIssuesSummary
{"labels":["ready-for-agent"],"number":31,"state":"OPEN",
 "title":"规格票：回应预算为什么是 3、能不能改——预算可配置（页面可改）+ 档位重定 + ADR 实测依据 + 五条词条与 ADR-0002 的处置",
 "url":"https://github.com/whutlichao/alibot/issues/31",
 "subIssuesSummary":{"completed":0,"percentCompleted":0,"total":0}}

$ gh issue view 1 --json subIssuesSummary --jq .subIssuesSummary
{"completed":8,"percentCompleted":88,"total":9}      # 8 → 9：#31 已挂上

$ gh issue view 31 --json body --jq .body | wc -c
   33448

$ gh issue view 31 --json body --jq .body | grep -n '^## '
9:## Problem Statement
37:## Solution
55:## User Stories
129:## Implementation Decisions
294:## Testing Decisions
339:## Out of Scope
355:## Further Notes
```

七节齐全且顺序与 `to-spec` 模板逐字一致（无额外章节、无草案代码块）。

**issue 正文逐字留档**：`app/specs/issue-31-spec.md`（头 5 行为说明，其余与 tracker body 逐字相同）。
核对命令与结果：

```console
$ tail -n +6 app/specs/issue-31-spec.md > /tmp/a.md
$ gh issue view 31 --json body --jq .body > /tmp/b.md
$ diff -q /tmp/a.md /tmp/b.md && echo OK
SPEC COPY == ISSUE BODY ✅ (逐字一致)     # 375 行 / 375 行
```

## 3. 「改数是否必须新 ADR」的结论与依据

**结论：分三种情形，只有第一种（不改默认值、不改语义）才不需要推翻 ADR-0002。**

| 情形 | 是否推翻 ADR-0002 | 必须记录什么 |
| --- | --- | --- |
| ① 只把常量搬成配置项，**默认值仍是 3**，上界语义（耗尽零投递）不动 | **不推翻** | 不推翻其取舍；但"新增人类面写入口"改变了 `app/alibot/api.py:397-404` 这条既有边界 ⇒ 需要**一条新 ADR 记录这次边界变化** |
| ② **改默认值**（3 → 其它） | **推翻** | ADR-0002 标题即结论（`at three`），只有新 ADR 能声明它失效，且必须带上实测依据（token / 时延 / 成本） |
| ③ **改上界语义**（把"零投递"换成"投递后刹住"，或允许人类面直接写 `used`） | **推翻** | ADR-0002 的关键取舍段写明"为什么选零投递"，改语义就是改那一段 |

**依据（为何是"推翻"而不是"修订"）**：

1. ADR-0002 的**标题本身就是一个结论**：`回应预算 ≤ 3 是有界响应的手段`（`at three`）。改数 = 改标题所指的
   结论，不是补一句说明。
2. `docs/agents/domain.md` 的纪律：与既有 ADR 冲突必须**显式上报**，不得静默覆盖。静默改 `BUDGET_LIMIT`
   只会让"书面结论"与"实现"分叉 —— 库里没有第二处记录这个数的由来。
3. `docs/adr/0005` 与 `CONTEXT.md:138-167` 的五条词条都复述了 3（"重置为 3"、"最多 3 次"、"已用满 3 次跳"），
   因此 ADR-0002 的**下游引用面**不止一处；不写新 ADR 就无法把这一串引用一次性交代清楚。
4. 新 ADR（懒创建规则）**只收真实决策**：改默认值自带"实测依据 + 新的代价上界"两条难逆取舍，符合
   `domain.md` 的收录标准。

**本票自身的执行**：规格**不改默认值**（走情形 ①），因此本票**没有**新增 ADR —— 这也符合票面约束
「仅在确有决议时」才动 `CONTEXT.md` / `docs/adr/`。规格把"新 ADR 该记录哪四件事"（§5）与"两个待决决策点"
写成下文，交给实现票与用户裁定。

## 4. 验收对照（票面 Observable acceptance 逐条）

| 票面要求 | 落点 | 读数 |
| --- | --- | --- |
| issue 已创建、标题与正文可读、带 `ready-for-agent` | #31 | `labels: ["ready-for-agent"]`、`state: OPEN`、body 33,448 字符 |
| 正文用模板七节 | #31 | 7 个 `## ` 小节，顺序与 `to-spec` 模板一致 |
| 逐项纳入五条诉求 | 1 预算可配置 → §2（L1/L2/L3）；2 页面交给用户 → §3（W1–W4）；3 重新定档位 → §4（T1–T4）；4 补齐 ADR 依据 → §5 + Testing Decisions 第 3 段；5 调查结论 → Problem Statement + Further Notes | 五条各有一节，均可检索 |
| 六条代码/来源事实带文件:行号与 issue 引用 | Implementation Decisions §1 事实基线表 F1–F6 | F1 `app/alibot/db.py:48`；F2 `orchestration.py:234` / `db.py:109-114`；F3 `db.py:802` / `:728-736`；F4 `orchestration.py:256`；F5 `app/tests/test_bounded_relay.py:194/:285-290/:331/:336/:488/:499`；F6 `gh issue view 1` + ADR-0002 + `CONTEXT.md:138-167` |
| 口径是 agent→agent 跳，不是"每个 bot 3 条"；跳数正本是 `channel_budget.used`，`_spend_jump` 是唯一写点 | §1 F2 + §1 末"必须逐字保留的两条既有语义" + §7 | 三处 |
| 人类世代 = 最近一条人类消息 seq，落库即重置满额 | §1 F3 | 正文 |
| 耗尽零投递 + 每个被压制成员各一条 `budget-exhausted` | §1 F4 + §7 + User Story 16 | 三处 |
| 把 3 当硬断言的既有用例必须一并处置 | §1 F5 + Testing Decisions 第 2 段（含处置三选一与"不得删测试"） | 6 处行号 + 2 个测试名 + 3 个处置候选 |
| 来源原文（#1 第 2 条）固化到 ADR-0002 与五条词条 | Problem Statement 逐字引用 + §1 F6 + §6 | 三处 |
| Out of Scope 非空，且明写不产出实施票、不改代码、不合并任何预算语义变更 | Out of Scope 前四条 | 首条"不产出实施票"、第三条"不改默认值、不合并任何预算语义变更" |
| 「页面上的写入口落在哪」与「无实测依据的默认值怎么定」显式标为待定，各≥2 候选及代价 | §3 标题即"显式待决决策点 ①"（W1–W4 各带代价）；§4 即"显式待决决策点 ②"（T1–T4 各带代价）；Further Notes 第 1 条再列一遍 | 各 4 个候选 |
| Answer 记录建票回执、读过文件清单、"改数是否必须新 ADR" | 本文件 §2 / §1 / §3 | 三节 |
| 若产生文档改动：worktree 干净、提交存在、分支已推、PR 已用 `--body-file` 创建、PR 号与 body 路径回填 Answer | §5 | 见下 |

## 5. 文档改动的落盘与 PR（本票确有落盘改动）

票面允许在"确有决议"时动 `CONTEXT.md` 词条与 `docs/adr/`。本票的结论是**没有决议需要落盘**（默认值不改、
没有新 ADR、五条词条的处置方式已写进规格的 §6 交给改数那一票），因此**未动** `CONTEXT.md` 与 `docs/adr/`。
落盘的是本票自己的取证文档（沿用 #13 / #20 / #22 的既有做法：Answer 的支撑材料进 `app/specs/`）：

| 文件 | 作用 |
| --- | --- |
| `app/specs/issue-31-spec.md` | issue #31 正文的逐字留档（离线 diff 用；tracker 仍是正本） |
| `app/specs/issue-31-acceptance.md` | 本文件：Answer 支撑材料与验收对照 |

未动：`app/alibot/**`（生产代码与测试）、`app/tests/**`、`alibot-design-system/**`、`CONTEXT.md`、`docs/adr/**`。
（核对命令 `git status --short` / `git diff --stat` 见 PR body 的 Evidence 段。）

回填读数（PR 已创建/更新后核对，不写死 sha —— 文档提交自身会推进 head）：

| 项 | 值 |
| --- | --- |
| PR | [#32](https://github.com/whutlichao/alibot/pull/32)（base `main`，head 分支 `issue-budget-config-spec`） |
| PR body 正本 | `app/specs/issue-31-pr-body.md`（`gh pr create --body-file` 创建；后续以同一文件更新） |
| head | `git rev-parse HEAD` == 分支 `issue-budget-config-spec` 的 HEAD（`git ls-remote origin issue-budget-config-spec` 应与之相同） |
| 产物提交 | 首个提交 `6535e19`（三份文档，630 insertions，零 `.py/.js/.css/.html`） |
| 分支推送 | `git push -u origin issue-budget-config-spec` → `[new branch]`、已设置 upstream |
