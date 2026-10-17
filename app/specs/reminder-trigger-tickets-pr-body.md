<!-- 本文件是 PR 正文的正本；`gh pr create --body-file` 用它提交。 -->

## Summary

把规格 #109（`app/specs/reminder-trigger-spec.md`，PR #110 合并于 `5c4dc5c`）「实施票切分建议」的 **4 票**落成 tracker 上的 **4 张真实实施票**，并建立**原生阻塞关系**；拆分记录与 Answer 留档落在 `app/specs/`。

```text
tracker（正本）
├── #111  拆分票（本 PR 的票；G-tickets）
├── #112  ① 库形态与迁移     C4 C5 C11 C12 C13 C14      blocked_by 0
├── #113  ② 到期投递器 seam   C1 C2 C3 C6 C7 C8 C9 C10 C20 C21 + C18后端半边   blocked_by 1 → [112]
├── #114  ③ 人类面           C15 C16 C17 + C18前端半边   blocked_by 1 → [113]
└── #115  ④ 注释改口径        仅 C19                     blocked_by 3 → [112,113,114]

app/specs/
├── reminder-trigger-tickets.md          # 新增：拆分记录（依赖图 + 偏差登记 + 行号复核）
└── reminder-trigger-tickets-answer.md   # 新增：Answer 留档（正本在 #111）
```

依赖图：

```text
① #112 ──▶ ② #113 ──▶ ③ #114 ──┐
  └──────────────┴──────────────┴──▶ ④ #115   （注释描述的对象 = ②③ 交付的行为）
```

**与规格原划分的三处偏差**（登记在拆分记录 §2，不静默改）：

```diff
- ④ 承担整条 C18（后端两键 + 前端三键）
+ ② 承担 C18 后端半边（system.reminder.fired / fired_late）   ← 谁用谁负责（人定）
+ ③ 承担 C18 前端半边（reminder.dateLabel / stateFired / stateMissed）
- ④ 只依赖 ①
+ ④ 被 ①②③ 全部阻塞（C19 的替换注释描述的对象必须先存在）
```

票数与分簇**未改**（Q2 = 4 票是用户人定）。

## Evidence

**Before** — 规格只写了切分建议，tracker 上没有实施票：

```console
$ gh api repos/whutlichao/alibot/issues/1 --jq '.sub_issues_summary'
{"completed":8,"percent_completed":80,"total":10}

$ gh api "repos/whutlichao/alibot/issues?state=all&sort=created&direction=desc&per_page=3" --jq '.[].number'
110 109 108          # 最新是 PR #110 / 规格票 #109，其后无任何新 issue
```

**After** — 4 张实施票 + 原生依赖读数：

```console
$ for n in 112 113 114 115; do
    printf "#%s blocked_by=%s blockers=[%s]\n" "$n" \
      "$(gh api repos/whutlichao/alibot/issues/$n --jq '.issue_dependencies_summary.blocked_by')" \
      "$(gh api repos/whutlichao/alibot/issues/$n/dependencies/blocked_by --jq '[.[].number]|join(",")')"
  done
#112 blocked_by=0 blockers=[]
#113 blocked_by=1 blockers=[112]
#114 blocked_by=1 blockers=[113]
#115 blocked_by=3 blockers=[112,113,114]
```

**契约覆盖逐票对账**（票面小节标题 vs 规格分配，脚本断言差集为空）：

```console
$ python3 - <<'PY'
spec = {112: "C4 C5 C11 C12 C13 C14", 113: "C1 C2 C3 C6 C7 C8 C9 C10 C20 C21",
        114: "C15 C16 C17", 115: "C19"}
# 逐票抓 ^### C<n> 标题，与规格分配做对称差
PY
112 headings: C4 C5 C11 C12 C13 C14   diff: set()
113 headings: C1 C2 C3 C6 C7 C8 C9 C10 C18 C20 C21   diff: {'C18'}   # C18 后端半边（偏差 1）
114 headings: C15 C16 C17 C18   diff: {'C18'}                        # C18 前端半边（偏差 2）
115 headings: C19   diff: set()
```

**四条额外硬要求在票面的落点**（`gh issue view` 逐条 grep 命中，无缺项）：

```text
#112  C12 迁移执行当日 · C13 一次性收窄（每天9点→迁移那天9点）· C14 改动 N 行
#113  C5 「落消息与写 fired_at 同一事务」· C2 「只写 fired_at、不落消息」显式分叉
#113  C9  POLL_INTERVAL_SECONDS = 30 + 单元断言 · 注入时刻 · 断言行「< 5 秒」证明没真等
#115  C19 建议替换注释全文（逐字可抄）+ 三处落点 651-659 / 394-397(zh) / 858-861(en)
```

**零源码改动**：

```console
$ git diff origin/main -- app/alibot/ app/tests/ docs/adr/ CONTEXT.md
$ echo $?
0
$ grep -m1 '^SCHEMA_VERSION' app/alibot/db.py
SCHEMA_VERSION = 12
```

**行号复核的唯一偏差**（已在拆分记录 §4 与 #112 票面登记）：`migrate.py` 的 `STEPS` 末步 `11 → 12` 规格记 `:658`，实测 **`:660`**。

## Merge Danger

**Door:** two-way

本 PR 只新增两份 Markdown（`app/specs/reminder-trigger-tickets.md` / `-tickets-answer.md`），零源码、零测试、零 ADR、零 `CONTEXT.md`、不 bump `SCHEMA_VERSION`。回滚只需 revert 本 PR；tracker 上的 5 张 issue（#111–#115）与 4 条 `blocked_by` 边是**外部状态**，revert 不会自动撤销它们（需要手工 `DELETE .../dependencies/blocked_by` 与关闭 issue），这是本 PR 唯一的「回滚不是纯 git 操作」之处。

**Blast Radius:** isolated

合并后 frontier 只有 **#112（①）** 可被认领（`blocked_by 0`）；③④ 的物理重叠要求「后合并者 rebase 并重跑全部门禁」，实施时行号很可能已漂移（③ 会在 `index.html:679` 附近加日期控件、在 `i18n.js:398-410` / `:862-875` 附近加三条词条）。
