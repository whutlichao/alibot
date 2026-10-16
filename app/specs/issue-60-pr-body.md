## Summary

#60 定稿：11 面的逐面数据面核实 + 5 条人定裁定的决策账 + 6 张 ADR + 8 条新词条，拆成 **11 张实施票 + 1 张 backlog 入口票**，依赖用 GitHub 原生 `blocked_by` 表达。

```text
#60 规格正本（本 PR）
├─ 文档：app/specs/issue-60-spec.md（= issue body 逐字镜像）
│        app/specs/issue-60-acceptance.md（回执与取证）
│        app/specs/issue-60-pr-body.md（本文件）
├─ 术语：CONTEXT.md ## Language   +8 词条 / 改 3 词条
└─ ADR ：docs/adr/0009…0014（连续 6 张，一决策一 ADR）

#60 实施票（全部 sub-issue；原生 blocked_by）
├─ #61 i18n 地基（词条机制 + GET/PATCH /api/settings + 全量双语 + 设置弹窗）  ← 无 blocker，阻塞 8 票
│   ├─ #62 后端可见文案双语
│   └─ #71 深色 token 族 + 主题三档（写面含 alibot-design-system/）
├─ #63 搜索面：后端三类命中 + 主线视图 + 四 facets + ⌘K
├─ #64 Agent 详情与编辑（dock 第三形态 + PATCH /api/agents/{id}）
│   └─ #65 主动认领（auto_claim + 编排认领 + 每任务一次开世代）
├─ #66 composer 工具行（@提及 + 作为任务发送）
│   └─ #67 附件真上传
├─ #68 提醒 reminders
├─ #69 成员名册弹窗
└─ #70 移动端抽屉壳
#72（Backlog，ready-for-human，不属本轮序列）Agent 运行日志数据面
```

**核实表的结论列**（决定每张票是「对齐」还是「能力新建」）：

| 数据面 | 面 | 处置 |
| --- | --- | --- |
| ✅ 已有真数据面且 studio 已对齐 | 私信 | 不立票 |
| ✅ 已有真数据面、缺落位 | 搜索的消息命中、composer 的 `@提及`、「作为任务发送」、成员名册 | 对齐 |
| 部分（读有写无 / 只有一类命中） | Agent 配置、搜索 | 建写路径与命中来源 |
| ❌ 无数据面（原型是写死的假数据或未绑定字段） | i18n、设置偏好、提醒、附件、Agent 运行观测、主动认领 | **能力新建**（不是照抄界面） |

## Evidence

- **Before:** 工作台第三轮尚无范围裁定；原型里有一批面没有数据面（`RUNTIME` / `LOGS` 是种子常量、设置三字段未绑定、提醒落 localStorage、附件只弹 toast、`claim` 在原型里是硬编码 `owner='atlas'`）。
  **After:** `gh issue view 60 --json body --jq '.body|split("\n")[]|select(startswith("## "))'` → `Problem Statement / 逐面核实表 / 决策账 / Solution / User Stories / Implementation Decisions / Testing Decisions / Out of Scope / Further Notes`；正文占位扫描 `grep -nE '待定|待决|未决|⏳|本票不选'` 无命中。
- `tail -n +7 app/specs/issue-60-spec.md | diff - <(gh issue view 60 --json body --jq '.body')` 只差一个尾随换行（gh 存储时补），去掉后逐字一致。
- 依赖链：`gh api repos/whutlichao/alibot/issues/{61..72} --jq .issue_dependencies_summary` → 61 `blocking:8`；62/63/64/66/68/69/70/71 `blocked_by:1`；64 `blocking:1`、65 `blocked_by:1`；66 `blocking:1`、67 `blocked_by:1`；72 无依赖。子票复核 `gh api repos/whutlichao/alibot/issues/60/sub_issues` → `[61…72]`。
- 改动范围：`git status --porcelain` 只有 `CONTEXT.md` 与 `app/specs/issue-60-*.md`、`docs/adr/0009…0014-*.md`——**`app/alibot/**` 与 `app/tests/**` 零改动**；`git diff --check` 通过。
- 本票按规格票范围**不跑**源码测试、lint/typecheck、双轴 code-review 与 G-impl：diff 全为文档与 tracker 写操作，验收面是模板结构、术语格式、ADR 体例、原生依赖与正文占位扫描（回执见 `app/specs/issue-60-acceptance.md`）。

## Merge Danger

**Door:** Two-way —— 本 PR 只改规格、术语与 ADR；要改判须以新决策记录明确替代。运行数据零触点：不改表结构、不改端点、不动 UI，也没有任何 `app/` 源码改动。

**Blast Radius:** Specification

实现不在本 PR 中。合并后即可按依赖顺序派票：`#61` 是唯一可立即开工的票（其余 7 张等它落地，`#65` 等 `#64`，`#67` 等 `#66`）。两处需要下游注意：① 本轮库内改动会把 `SCHEMA_VERSION` 带到 **7**，**#35 的 `silent`（#58）落地时必须顺延到 8**；② 认领按人定会**每张任务重置一次预算**，实施 #65 时必须把「每任务至多一次」做成后端构造性保证，否则 ADR-0002 的三跳上界可被反复认领绕过。
