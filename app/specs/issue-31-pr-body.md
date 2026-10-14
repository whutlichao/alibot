## Summary

规格票 ㉛：把「回应预算为什么是 3、能不能改」收敛成一张可实施的规格。**零源码改动**：产物是一张 issue
（规格正本）+ 两份离线留档。

```text
issue #31（Part of #1）        规格正本，标 ready-for-agent；正文 7 节 + 事实基线 F1–F6
  ├─ 五条诉求  ① 预算可配置      → §2 落点候选 L1 env / L2 库内逐频道 / L3 工作区·成员级，逐条附代价
  │            ② 页面上交给用户  → §3 显式待决 ①：W1 频道设置写端点 / W2 仅创建期 / W3 工作区 / W4 发言承载
  │            ③ 重新定档位      → §4 显式待决 ②：T1 固定 / T2 按成员数 / T3 按成本 / T4 按用途；本票立场=默认值不改
  │            ④ 补齐 ADR 依据    → §5 + Testing Decisions：token(`Msg.usage`) / 时延(round_outcome) / 金额，≥2 模型
  │            ⑤ 调查结论        → 3 是 #1 拍板的策略参数，仓库无任何成本模型或对比实验
  ├─ 六条事实   F1 db.py:48 `BUDGET_LIMIT = 3`（三面皆不可配置）
  │            F2 口径是 agent→agent 跳；正本 channel_budget.used；唯一写点 _spend_jump
  │            F3 人类世代 = 最近一条人类消息 seq；落库即重置满额
  │            F4 耗尽零投递 + 每个被压制成员各一条 budget-exhausted
  │            F5 把 3 当硬断言的用例在 test_bounded_relay.py（6 处行号 + 2 个测试名）
  │            F6 来源 = `gh issue view 1` 边界第 2 条 + ADR-0002 + CONTEXT.md:138-167 五条词条
  └─ 两个待决   写入口落在哪 / 新默认值怎么定 —— 各 4 候选带代价，规格不自行拍板

app/specs/issue-31-spec.md        issue 正文逐字留档（tracker 仍是正本）
app/specs/issue-31-acceptance.md  Answer 支撑材料：建票回执 / 读过文件清单 / 「改数必须新 ADR」判据
```

**未动**：`app/alibot/**`、`app/tests/**`、`alibot-design-system/**`、`CONTEXT.md`、`docs/adr/**` ——
本票的结论是「没有决议需要落盘」（默认值不改、不新增 ADR），五条词条与 ADR-0002 的处置**方式**写进规格的
§6，交给真正改数的那一票执行。

一处结构性未决项经 `orca orchestration ask` 取得 coordinator 裁定后执行：新票以
`gh issue create --parent 1` 挂为 **#1 的 sub-issue**（与 #2/#3/#4/#8/#9/#12/#13/#14 同形态），
且**不授权**对 #1 / #23 做任何写操作 —— #23 决策 G2 的「逐字不变（≤3）」措辞只在 #31 正文的 Further Notes
里记为待同步项。

## Evidence

**Before**（开工前的取证：三个面都是硬的）

```console
$ grep -rn "BUDGET_LIMIT" app/alibot/                                  # 结论：源码常量 + 唯一 import 点
app/alibot/db.py:48:BUDGET_LIMIT = 3
app/alibot/orchestration.py:50:from .db import BUDGET_LIMIT, Database, utc_now
app/alibot/orchestration.py:250:            if state["used"] >= BUDGET_LIMIT:
$ grep -rn "budget" app/alibot/studio/index.html                        # 结论：0 命中
$ grep -rn "budget\|预算" alibot-design-system/                          # 结论：0 命中（原型无该控件）
$ sed -n '397,404p' app/alibot/api.py                                   # 结论：写端点一律 404
    @app.api_route("/api/channels/{channel_id}/budget",
        methods=["PUT", "PATCH", "DELETE", "POST"], include_in_schema=False)
    def no_budget_mutation(channel_id: str) -> JSONResponse:
        """预算只由后端在「人类消息重置 / 一次跳投递」时推进：人类面不能直接改它。"""
        return _not_found("回应预算只读：人类面不存在写入它的端点")
```

**After**（建票回执与正文读数，逐条可复核）

```console
$ gh issue create --parent 1 --title "规格票：回应预算为什么是 3、能不能改——…" \
    --body-file /tmp/issue-budget-spec-body.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/31

$ gh issue view 31 --json number,labels,state --jq '{number,state,labels:[.labels[].name]}'
{"labels":["ready-for-agent"],"number":31,"state":"OPEN"}

$ gh issue view 1 --json subIssuesSummary --jq .subIssuesSummary
{"completed":8,"percentCompleted":88,"total":9}          # 8 → 9：挂上 #1

$ gh issue view 31 --json body --jq .body | grep -n '^## '   # 七节，顺序同 to-spec 模板
9:## Problem Statement / 37:## Solution / 55:## User Stories / 129:## Implementation Decisions
294:## Testing Decisions / 339:## Out of Scope / 355:## Further Notes

$ tail -n +6 app/specs/issue-31-spec.md > /tmp/a.md
$ gh issue view 31 --json body --jq .body > /tmp/b.md
$ diff -q /tmp/a.md /tmp/b.md && echo "SPEC COPY == ISSUE BODY"
SPEC COPY == ISSUE BODY ✅                                # 375 行 / 375 行逐字一致

$ git status --short
?? app/specs/issue-31-acceptance.md                      # 仅两份新增文档，无任何 .py/.js/.css/.html 改动
?? app/specs/issue-31-spec.md
```

**门禁**：票型为规格票、门禁 **G-spec**。按票面「本票不跑源码测试（无源码改动）、不跑双轴 code-review
（改的是票据与文档，不是 diff）、不跑 G-impl」执行 —— 因此证据是上面的只读取证读数、建票回执、正文结构与
逐字留档核对，而不是测试输出。`to-spec` 的四步流程已按其原文走完（README/取证：`issue-31-acceptance.md` §0）。

## Merge Danger

**Door:** two-way —— 合并的是两份 `app/specs/` 文档；规格正本在 issue #31（合并前已存在，不依赖本 PR）。
回滚只需 revert 这两个文件，不影响 issue、不影响任何运行时行为。

**Blast Radius:** minimal —— 零生产代码、零测试、零设计系统、零 `CONTEXT.md` / `docs/adr/` 改动。
唯一的外部副作用是 issue #31 的创建与它对 #1 的 sub-issue 归属，这两件事都不由本 PR 引入。
唯一需要注意的下游影响：`app/specs/issue-31-spec.md` 是 issue 正文的**副本**，若日后修改 issue 正文
而不同步该文件会形成分叉 —— 已在文件头写明「以 tracker 为准」。
