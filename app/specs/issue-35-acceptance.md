# 规格票 ㉟ 验收取证（issue #35：Agent 的括注 / 旁白——收敛到可实施的沉默规格）

本文件是 Answer 的支撑材料：只记**跑过后出现的事实**、实际读过的文件清单、以及为「沉默」选定的术语与理由。
规格正本在 GitHub issue tracker：**[#35](https://github.com/whutlichao/alibot/issues/35)**（本票唯一产物）。
本文件与 `app/specs/issue-35-spec.md`（issue 正文逐字留档）都在 worktree 内，便于离线核对。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-silence-narration-spec`（Orca 独立顶层 worktree）
- 分支：`issue-silence-narration-spec`
- 基线：`origin/main` 的 `9fd31bb`（#22 第二版规格合并后的 main HEAD）
- 采集时间：2026-10-14
- 票型：**规格票**，门禁 **G-spec**（本票不跑源码测试、不跑双轴 code-review、不跑 G-impl）
- 前置 skill：`/Users/apple/.agents/skills/to-spec/SKILL.md`（开工前完整读，按其流程执行）

## 0. 前置 skill 的执行（`to-spec`）

`to-spec` 规定三步，逐条落点：

1. **探索仓库理解现状**（不访谈用户，只综合已知）→ §1 的只读取证清单（全部只读，零修改 `app/`）。
2. **勾出测试接缝，优先既有接缝、越少越好** → 规格 Testing Decisions：沿用既有唯一高层接缝
   （发送消息 → 落库 → 唤醒 → 追加回复 → 前端实时呈现）与既有的成员面适配接缝（`AgentSeam` / `RoundOutcome`），
   **不新开 seam**；阶段 3 若需要新行为，扩既有替身（`app/tests/scripted.py`）的注入面而非新接缝。
3. **用模板七节写规格并发布到 issue tracker，标 `ready-for-agent`** → §2 建票回执（七节顺序与标题逐字照模板，
   未自由发挥章节、无草案代码块）。

## 1. 实际读过的仓库文件与来源（只读取证，零修改）

代码（**全部只读**，未做任何编辑；行号对应 base `9fd31bb`）：

| 文件 | 读它的目的 | 用到的事实 |
| --- | --- | --- |
| `app/alibot/agent_seam.py` | 现象的产生点、seam 契约、系统提示原文 | `SEAM_STATUSES = ("replied","ignored","failed")`（:25）；`RoundOutcome.__post_init__` 的状态校验 + 「一轮最多产出 1 条频道消息」（:115-121）；`run_round` 的真实实现（:214）；正文非空 ⇒ `replied`（:253-258，判定在 :254-255）；`_system_prompt`（:261）；**被评估的那句提示**（:270-274，关键在 :273）；`_msg_text`（:283） |
| `app/alibot/orchestration.py` | 唯一的 Agent 消息落库路径、`notify` 的早退条件、跳与预算 | `_commit_reply`（:472，docstring :479-483）；`outcome.status=="replied" and outcome.messages` 才落库（:364-376）；`notify` 的唯一早退是 `kind=="system"`（:173-174）；`jump_delivered = author_kind == "agent"` + `_spend_jump`（:184-188）；唤醒面 `_wake_targets`（:205）；`_run_seam` 把异常收口成 `failed`（:459-470）；`_record_merged`（:426）；模块 docstring 的五条纪律（:1-14） |
| `app/alibot/db.py` | 消息 kind 词表、正文校验、未读口径、round_outcome 表与词表 | `MESSAGE_KINDS`（:42）；`ROUND_STATUSES` 五态（:45）；`message` 表的 `kind` CHECK（:91）与未读只算 `kind IN ('human','agent')` 且非作者自身（:199-203）；`round_outcome` 表与 status CHECK（:138-151，CHECK 在 :143-144）；`append_message` 只校验正文非空（:681-700，判定在 :692-694）；`SCHEMA_VERSION = 3`（:35） |
| `app/alibot/studio/app.js` | 呈现层既有手法（#18）、`msg-tag` 落点 | `OUTCOME_SUMMARY` 三键 + `SUMMARY_STATUSES`（:65-81）；`summarizableOutcomes`（:983）；`roundOutcomeSummaryNode`（:997，含 `data-base-seq` / `aria-expanded` / `aria-controls`，折叠态文案 :1021）；`msg-tag 'Agent'`（:1203） |
| `app/alibot/service.py` | 人类消息落库 → 事件 → 唤醒的顺序 | `send_message` 的三步顺序（:237-256，`notify` 在 :255） |
| `app/alibot/api.py` | 接缝注入点（既有，不新开） | `create_app` 的 `seam` 注入（:45 的 import、:165 的 `AgentScopeAgentSeam()` 默认值） |
| `app/tests/scripted.py` | 既有脚本化替身（验收不联网、零成本） | `ScriptedAgentSeam` 的四种行为固定 + `calls` 记录（:60-90） |
| `app/tests/` 目录清单 | 既有用例族的分布 | `test_bounded_relay.py`（预算 / 唤醒面）、`test_round_outcome_visibility.py`（轮次结果可见化）、`test_agent_round.py`、`test_agent_smoke.py` 等 |

文档与 tracker：

| 来源 | 用到的事实 |
| --- | --- |
| `CONTEXT.md` `## Language` | 「回应判断」（明确无关 ⇒ 应当 `ignored`，正常协议选择）:103-107；「轮次」（一轮 ↔ 至多一条 target 消息）:109-112；「轮次结果」（五态 + append-only）:114-119；「成员面适配接缝」；「穿透 / 提及 / 人类世代 / 回应预算 / 跳 / 预算耗尽」；**`_Avoid_` 纪律**是全篇词条的固定组成 |
| `docs/adr/0001-messages-are-append-only.md` | 消息不可编辑、不可删除（库级触发器兜底）⇒ 历史旁白只能靠呈现层降档 |
| `docs/adr/0002-response-budget-caps-relay-at-three.md` | 跳的定义 = 一条 Agent 消息投递了至少一个唤醒 ⇒ 没有消息就没有跳（本票与之一致，非修改） |
| `docs/adr/0004-target-becomes-a-first-class-object.md` | `SCHEMA_VERSION` bump 与「不做旧数据迁移」的既有口径 |
| `docs/agents/domain.md` | 与既有 ADR 冲突必须**显式上报**，不得静默覆盖；术语用 `CONTEXT.md` 词汇、不回退 `_Avoid_` 词 |
| `docs/agents/issue-tracker.md` | 建票命令（`gh issue create --parent`）、sub-issue 层级表达 |
| `docs/agents/triage-labels.md` | 标签取词：`ready-for-agent` |
| `gh issue view 1` / `22` / `23` / `31` | 规格锚点体例（#1 七节 + User Story 编号风格）；`gh issue view 31` 是本仓最近一次同类规格票的范式；`#22`/`#23` 确认是第二版能力扩展锚点（与本题无关，故不挂其下） |
| `app/specs/issue-31-acceptance.md` / `issue-31-spec.md` | 本仓同类票的取证范式（本文件的结构与章节即沿用），并经 `git fetch` 从 `origin/issue-budget-config-spec` 读取 |

## 2. 建票回执（真实命令与真实输出）

新票建为 **#1 的 sub-issue**（coordinator 裁定 Q1=A：与 #2/#3/#4/#8/#9/#12/#13/#14/#31 同形态挂 `#1`；
**不挂** #22 / #23 —— 那是第二版能力扩展的锚点，与「沉默」无关）。

```console
$ gh --version
gh version 2.95.0 (2026-06-17)

$ gh issue create --parent 1 \
    --title "规格票：Agent 的括注 / 旁白——把「说了等于没说的消息」收敛到可实施的沉默规格（分阶段：prompt 纪律 → seam 最小判据 → 结构化沉默信号）" \
    --body-file /tmp/issue-silence-body.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/35

$ gh issue view 35 --json number,title,labels,state,url,parent \
    --jq '{number,title,state,url,labels:[.labels[].name],parent:(.parent.number//"none")}'
{"number":35,"parent":1,"state":"OPEN",
 "title":"规格票：Agent 的括注 / 旁白——把「说了等于没说的消息」收敛到可实施的沉默规格（分阶段：prompt 纪律 → seam 最小判据 → 结构化沉默信号）",
 "url":"https://github.com/whutlichao/alibot/issues/35",
 "labels":["ready-for-agent"]}

$ gh api repos/whutlichao/alibot/issues/1/sub_issues --jq '[.[].number]'
[2,3,4,8,9,12,13,14,31,35]        # 35 已挂上 #1

$ gh issue view 35 --json body --jq '.body' | grep -n '^## '
1:## 规格票：Agent 的括注 / 旁白 —— 把「说了等于没说」收敛到可实施的沉默规格
11:## 用户已确认的诉求（本轮拍板，先读这段）
27:## Problem Statement
70:## Solution
121:## User Stories
181:## Implementation Decisions
311:## Testing Decisions
346:## Out of Scope
363:## Further Notes

$ gh issue view 35 --json body --jq '.body' | wc -c
   36863
```

模板七节齐全（`Problem Statement` / `Solution` / `User Stories` / `Implementation Decisions` / `Testing Decisions` /
`Out of Scope` / `Further Notes`），顺序与 `to-spec` 模板逐字一致；第 1 行的 `## 规格票…` 与第 11 行的「用户已确认的诉求」
是票面头部（#1 / #31 有同样的头部），七节之外**无自造章节**（`### A.–G.` 是 `User Stories` 节内的分组，
`### D0–D5` 是 `Implementation Decisions` 节内的决策条目）、**无草案代码块**
（正文里唯一的两段 `text` 代码块是「今天 vs 目标」的**数据流示意**，不含可直接落地的实现代码）。

**issue 正文逐字留档**：`app/specs/issue-35-spec.md`（头 6 行为说明，其余与 tracker body 逐字相同）。
核对命令与结果：

```console
$ tail -n +7 app/specs/issue-35-spec.md > /tmp/a.md
$ gh issue view 35 --json body --jq .body > /tmp/issue35-body.md
$ diff -q /tmp/a.md /tmp/issue35-body.md && echo "SPEC COPY == ISSUE BODY ✅"
SPEC COPY == ISSUE BODY ✅
```

## 3. 提问通道（`orca orchestration ask`，禁 AskUserQuestion）

开工前先读 `to-spec`：它规定「不访谈用户，只综合已知」。本票的四个诉求已由用户在本轮选定，所以待定的不是需求，
而是三处**结构性**落位。三问经 `orca orchestration ask` 一次提出（`msg_f50b80b8ec55`），
在 3 次超时续等后由 coordinator 答复（原文要点）：

| 问题 | 裁定 | 规格落点 |
| --- | --- | --- |
| Q1 新规格票怎么挂 | **A：`--parent 1`**（与 #2–#14/#31 同形态）。明确「不要挂 #23（那是第二版能力扩展锚点）」；并纠正了一处认知——#22 是独立的第二版规格票，**#23 才是挂在 #22 下的正本** | §2 建票回执；Further Notes 的落位记录 |
| Q2 判据要不要给推荐序 | **C：两阶段路线**——①先修 prompt 纪律 → ②seam 层给一个最小判据（兼容既有数据与不听 prompt 的模型）→ ③结构化沉默信号为目标态。三条都是**演进阶段**而非被否方案；必须写清「每阶段单独能交付什么、什么条件下才值得进下一阶段」。**明确否掉 A**（四候选并列无推荐）与 **B**（把结构化信号提前成唯一已定方向） | Solution 的三阶段表 + D1 重写为「阶段 2 的最小判据」+ D2 改为「阶段 3 是阶段 2 的目标态」+ 新增 User Stories §G（29–35）+ Testing Decisions 的分阶段口径 + Further Notes 的裁定记录 |
| Q3 术语由谁定 | **A：由我定并写进 Answer 理由**；约束是遵循 `CONTEXT.md` 的 `_Avoid_` 纪律、现象命名与真状态要能分清、词条只在确有决议时才落 `CONTEXT.md` | §4 术语裁定 |

一次 `heartbeat` 在等待期间按 preamble 节奏发出（`msg_064b0bbda97f`，phase=「spec 正文已成稿，等待落位裁定」）。

## 4. 术语裁定：「沉默」与它的反面词（Answer 要求项）

**裁定：正式术语只加一个——「沉默 (Silence)」；现象命名为「旁白式交代 (Silent Narration)」，并明确列入 `_Avoid_`，
不作为正式术语。** 理由如下：

1. **两者必须在词汇表里分清，因为它们的数据面完全不同。** 「沉默」的正本是一条 `round_outcome` 行
   （status `ignored`，不占 `seq`、不计未读、不构成一次跳）；「旁白式交代」的正本是一条 `message` 行
   （`kind="agent"`，占 `seq`、计未读、构成一次跳）。若共用一个词，票面、注释与界面文案会把两个面混成一件事——
   正是本票 Problem Statement 描述的现状。
2. **现象名刻意取得「不好听」。** 取「旁白式交代」而不是「沉默说明 / 空回应 / 括注消息」，是因为后三个词都把
   「说了等于没说」读成**一种低配版回应**，会让下游票面继续把它当成一种回应类型来处理。按 `CONTEXT.md` 的
   `_Avoid_` 纪律，这三个词一同列入 `_Avoid_`（措辞与全篇词条一致，写成 `_Avoid_: …`）。
3. **「沉默」与既有词不冲突。** `CONTEXT.md` 已用 `ignored` 与「自判忽略」表达同一件事（「明确无关 ⇒ 应当 `ignored`，
   这是正常的协议选择而不是失败」，:103-107）。本票不新造第四个 status 语义，只在**用户可见语义**上说「沉默」
   （对应既有的「自判忽略」），指**落库字段值**时仍写 `ignored`——这是**沿用**而不是新增语义。
4. **遵循「只在确有决议时才落盘」的仓库纪律。** Q3 的约束是「词条若新增/修改，只在确有决议时落到 `CONTEXT.md` 的
   `## Language`」。本票虽然对术语有裁定，但**规格本身不产出实施**，且该词条的最终措辞依赖 D1 / D3 的裁定
   （若阶段 3 新增 status 词，词条形状会变）。因此本票**没有**改 `CONTEXT.md`：裁定与理由留在本文件，
   由真正动代码的那一票连同 D1 / D3 的裁定一次性落盘。§6 记录这个判断与触发条件。
5. **命名与既有 `_Avoid_` 风格一致。** 全篇词条都是「中文词 (English Term)」+ `_Avoid_` 划界；
   「沉默 (Silence)」沿此格式，「旁白式交代 (Silent Narration)」作为反面词只出现在 `_Avoid_` 与 Problem Statement 里。

**另有一处措辞纪律**：不把「沉默」说成「过滤 / 优先级」（`CONTEXT.md` 对「回应判断」的 `_Avoid_`），也不把
`budget-exhausted` 那类「被压制」说成沉默——**沉默是 Agent 自己的判断，被压制是后端的裁决**，两者在
Problem Statement 与 User Stories §B 里分开陈述。

## 5. 验收对照（票面 Observable acceptance 逐条）

| 票面要求 | 落点 | 读数 |
| --- | --- | --- |
| 该 issue 已创建、标题与正文可读、带 `ready-for-agent` | #35 | `labels: ["ready-for-agent"]`、`state: OPEN`、body 36,863 字符 |
| 沿用仓库既有规格票做法（参照 #1 / #22 形态） | #35 | 七节模板 + 票面头部「用户已确认的…」+ `Part of #1` + `gh issue create --parent 1`（同 #31） |
| 正文含四条诉求 | §「用户已确认的诉求」表（4 行，逐条给阶段与完成度）+ Solution 的三阶段表 + D1/D3/D4/D5 各自展开 | 四条均可检索且各有一节 |
| 正文含四条代码事实（带文件:行号） | Problem Statement §「代码事实」（4 条，编号 1–4） | ① `app/alibot/agent_seam.py:255` / `:256-258` → `orchestration.py:472` → `db.py:692-694`；② `db.py:42` / `studio/app.js:1203` / `db.py:199-203`；③ `orchestration.py:173-174` / `:184-188`；④ `agent_seam.py:254-255` / `db.py:45` / `studio/app.js:997` / `:1021` |
| 四条诉求作为 Implementation Decisions 里的候选方案与取舍，标注已定 / 待定 | §「已定」D0-a–e + §D1（4 候选带代价）+ §D3（3 路线带契约影响）+ §D4（3 形态带代价） | 已定 5 条、待定 3 处，每处均标 ⏳ |
| Out of Scope 非空、明确不产实施票、不改代码 | §Out of Scope（9 条，前两条即为此） | 「本票不产出实施票」「本票不改任何代码（`app/` 下的 `.py`/`.js`/`.css`/`.html`、`app/tests/`、`alibot-design-system/` 一律不动）」 |
| 「无实质内容的判据」被显式标记为待定决策点，且给至少两个候选及其代价 | §D1（标题含「⏳ 待定决策点」，4 个候选各带代价/风险） | 4 ≥ 2 |
| 明示「判据落点唯一性」与误判方向 | §D1 末「裁定判据时必须一并回答的两个约束」+ §D4 形态-c 的否掉理由 | 两处 |
| Answer 记录建票命令真实回执（issue number/URL） | 本文件 §2 | `https://github.com/whutlichao/alibot/issues/35` |
| Answer 记录实际读过的仓库文件清单 | 本文件 §1（代码 8 项 + 文档/tracker 10 项，各带读它的目的与用到的事实） | 18 行 |
| Answer 记录为「沉默」选定的术语与理由 | 本文件 §4（5 条理由 + 1 条措辞纪律） | 已记 |
| worktree 干净、提交存在、分支已推送、PR 以 `--body-file` 创建或更新 | 本文件 §7 | 见 §7 |
| 最后发送并核实 worker_done 回执 | 本文件 §7 | 见 §7 |

## 6. 文档改动面：为什么本票**没有**动 `CONTEXT.md` / `docs/adr/`

票面授权「仅在确有决议时」才写 `CONTEXT.md` 的 `## Language` 词条与 `docs/adr/` 下的新 ADR。本票的实际情况是
**两处都不到落盘条件**：

| 候选落盘 | 是否落 | 判据 |
| --- | --- | --- |
| `CONTEXT.md` 新增「沉默 (Silence)」词条 +「旁白式交代」进 `_Avoid_` | **否** | 术语裁定依赖 D1 / D3：若阶段 3 选「落点-b」（新增 status 词），词条要说的是**新语义**；若选「落点-a/c」，词条只是既有 `ignored` 的同义说法。现在落盘会把一个待定项固化成词条 |
| 新 ADR（`0007`）声明「沉默」成为第六种 status | **否** | 该 ADR 的前提（D1 采判据-2 **且** D3 采落点-b）尚未成立。规格已把它写成**显式的触发条件与要求**（§「与既有 ADR / 契约的关系」），由实施前的那一次定夺一起产出 |
| 修改 ADR-0002 / ADR-0001 | **否** | 本票与两者均**不冲突**：ADR-0001 下历史不可改（本票尊重），ADR-0002 的跳定义下「没有消息就没有跳」（本票的结论是它的自然推论）。规格里已逐条说明 |

因此本票的产物**只有** `app/specs/` 下的三份文件（issue 正文留档 + 本验收件 + PR body）与 tracker 上的 issue #35。
`app/alibot/**`、`app/tests/**`、`alibot-design-system/**`、`CONTEXT.md`、`docs/adr/**` **零改动**。

## 7. 状态流转

- 改动落在分支 `issue-silence-narration-spec`，基线 `9fd31bb`（main HEAD）。
- worktree 干净（`git status --short` 为空）；提交 `3216355`、已推送、PR **#36** 已以 `--body-file` 创建。
- 合并（G-merge）由 coordinator 执行；本票不自行合并、不自行改 #35 的 label 或 state、**不创建实施票**。

### 收尾回执（真实输出）

```console
$ tail -n +7 app/specs/issue-35-spec.md > /tmp/new35.md && printf '\n' >> /tmp/new35.md
$ gh issue view 35 --json body --jq .body > /tmp/live35.md
$ diff -q /tmp/new35.md /tmp/live35.md && echo "SPEC COPY == ISSUE BODY ✅"
SPEC COPY == ISSUE BODY ✅

$ git add app/specs/issue-35-*.md && git commit -m "docs(spec): #35 Agent 的括注/旁白——沉默规格正本（零源码改动）"
[issue-silence-narration-spec 3216355] docs(spec): #35 Agent 的括注/旁白——沉默规格正本（零源码改动）

$ git push -u origin issue-silence-narration-spec
 * [new branch]      issue-silence-narration-spec -> issue-silence-narration-spec

$ gh pr create --base main --head issue-silence-narration-spec \
    --title "docs(spec): #35 Agent 的括注/旁白——沉默规格正本（零源码改动）" \
    --body-file app/specs/issue-35-pr-body.md
https://github.com/whutlichao/alibot/pull/36

$ gh pr view 36 --json number,state,files --jq '{number,state,files:[.files[].path]}'
{"number":36,"state":"OPEN",
 "files":["app/specs/issue-35-acceptance.md","app/specs/issue-35-pr-body.md","app/specs/issue-35-spec.md"]}

$ git status --short          # 空 ⇒ worktree 干净
$ git rev-parse HEAD
0cb9c4d8d59286a52c46731fff7e3afe0c2489c4        # 收尾提交（规格提交为 3216355）
```

- **PR**：[#36](https://github.com/whutlichao/alibot/pull/36)（OPEN，base `main`）
- **PR body 正本**：`app/specs/issue-35-pr-body.md`
- **提交**：规格提交 `3216355`（issue 留档 + 验收件 + PR body）；本行回填所在的收尾提交 `0cb9c4d` ——
  以该提交为分支 head 时 PR #36 上可见的 head sha 即 `0cb9c4d`（若之后再有提交，以 `git rev-parse HEAD` 为准；
  文件无法包含自身的 sha，故此处记两个提交而不是单一 sha）
- **改动面**：仅 `app/specs/` 下三份文档（三次提交均为 add / 追加，无修改、无删除其它文件）
- **基线**：`9fd31bb`（`origin/main`）
