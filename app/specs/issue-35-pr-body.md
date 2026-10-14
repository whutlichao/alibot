## Summary

规格票 ㉟：把「Agent 用括注 / 旁白交代沉默」这一现象收敛成一张可实施的规格。**零源码改动** —— 产物是一张 issue
（规格正本）+ 三份 `app/specs/` 离线留档。

```text
issue #35（Part of #1，sub-issue，标 ready-for-agent）   规格正本：七节模板 + 四条代码事实
  ├─ 四条诉求，按 coordinator 裁定的「两阶段路线」排成三个阶段：
  │     阶段 1  先修 prompt 纪律（第 4 条）—— 归因已定：现行提示在教模型用括注（agent_seam.py:273）
  │     阶段 2  seam 层最小判据（第 2 条）—— 判据本身是 ⏳ 待定（D1：4 候选带代价）
  │     阶段 3  结构化沉默信号（第 3 条）—— 目标态；落点 ⏳ 待定（D3：3 路线带契约影响）
  │     呈现层降档（第 1 条）横跨三阶段，不依赖判据裁定（D4：3 形态带代价）
  │     每阶段都写清「单独能交付什么」+「什么条件下才值得进下一阶段」
  ├─ 四条代码事实（带文件:行号）
  │     ① agent_seam.py:255 正文非空 ⇒ replied → _commit_reply → db.py:692-694 只校验非空
  │     ② db.py:42 MESSAGE_KINDS 无第四值 / app.js:1203 msg-tag 恒为 'Agent' / db.py:199-203 未读也算它
  │     ③ orchestration.py:173-174 notify 只对 system 早退 ⇒ 旁白照常跳一次 + 烧一格预算
  │     ④ agent_seam.py:254-255 ignored 才是真沉默路径（不落消息，只在 round_outcome 留一条）
  ├─ 已定 D0-a–e：收敛方向 / 不动 MESSAGE_KINDS / 历史不可回填 / 轮次契约不放松 / 沿用 #18 呈现手法
  └─ ⏳ 待定 D1 / D3 / D4 + ADR 触发条件（若 D1 选判据-2 且 D3 选落点-b ⇒ 实施前须写 ADR-0007）

app/specs/issue-35-spec.md        issue 正文逐字留档（tracker 仍是正本）
app/specs/issue-35-acceptance.md  Answer 支撑材料：建票回执 / 读过文件清单 / 术语裁定理由 / 验收对照
app/specs/issue-35-pr-body.md     本文件
```

**未动**：`app/alibot/**`、`app/tests/**`、`alibot-design-system/**`、`CONTEXT.md`、`docs/adr/**`。
票面授权「仅在确有决议时」才写词汇表与新 ADR；本票的两处候选落盘**都不到条件**（术语形状依赖 D1/D3 的裁定；
新 ADR 的前提尚未成立），规格把触发条件写成显式的，理由记在
`app/specs/issue-35-acceptance.md` §6 —— 不是遗漏，是拒绝把一个待定项固化成词条。

## Evidence

**建票真实回执**

```console
$ gh issue create --parent 1 \
    --title "规格票：Agent 的括注 / 旁白——把「说了等于没说的消息」收敛到可实施的沉默规格（分阶段：prompt 纪律 → seam 最小判据 → 结构化沉默信号）" \
    --body-file /tmp/issue-silence-body.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/35

$ gh api repos/whutlichao/alibot/issues/1/sub_issues --jq '[.[].number]'
[2,3,4,8,9,12,13,14,31,35]        # 35 已挂上 #1
```

**七节齐全 + 正文逐字留档一致**

```console
$ gh issue view 35 --json body --jq '.body' | grep -n '^## '
27:## Problem Statement         70:## Solution          121:## User Stories
181:## Implementation Decisions  311:## Testing Decisions  346:## Out of Scope
363:## Further Notes

$ tail -n +7 app/specs/issue-35-spec.md > /tmp/a.md
$ diff -q /tmp/a.md <(gh issue view 35 --json body --jq .body) && echo "SPEC COPY == ISSUE BODY ✅"
SPEC COPY == ISSUE BODY ✅
```

**「待定」不是含糊，是可检索的**

```console
$ gh issue view 35 --json body --jq .body | grep -c '⏳'
3
$ gh issue view 35 --json body --jq .body | grep -n '待定决策点'
209:### D1｜阶段 2 的 ⏳ 待定决策点（本票最大未决项）：什么算「无实质内容」的可靠判据
240:### D3｜⏳ 待定决策点：结构化沉默信号的落点（对现有 `RoundOutcome` 契约的影响）

$ # D4「呈现层降档的形态」同样带 ⏳ 与三候选代价表；三处待定项互不阻塞
```

## 为什么这样切（本票的三个取舍）

1. **判据刻意留白。** 用户明确要求「不要自行拍一个正则」。四个候选（文本启发式 / 模型结构化声明 / 哨兵值 /
   只做呈现降档）各自带代价，D1 只摆事实不选。规格的价值在这里是**把不确定性显式登记**。
2. **阶段而不是并列方案。** coordinator 裁定「两阶段路线」（①prompt → ②seam 最小判据 → ③结构化信号），
   并明确否掉「四候选并列无推荐」与「把结构化信号提前成唯一已定方向」。因此每个阶段都配了
   「单独能交付什么」与「进下一阶段的条件」，包括诚实的局限：阶段 1 是概率性的、给不出正确性保证，
   也**不产生**「零条新 message 行」那条红→绿验收。
3. **历史旁白只能在呈现层降档。** ADR-0001 定死 append-only，没有回填路径 —— 这条被写成已定项 D0-c，
   并决定了 D4 的形态比较（摘要行的锚点是 `base_seq`，旁白消息的锚点是 `seq`，两者跨数据面）。

## Merge Danger

- 本票**不创建实施票**、**不建立任何 sub-issue 依赖**、**不改 #1 / #22 / #23 的任何内容**。合并后 #35 是沉默
  这件事的唯一规格正本；后续变更以对 #35 的评论为准。
- 两处待定项（D1 / D3）**尚未裁定**，合并**不等于**授权进入实施；实施前需先定判据与结构化落点，
  并（若触发）先写 ADR-0007。
- 本票的术语裁定（「沉默 (Silence)」+「旁白式交代」进 `_Avoid_`）**没有**落到 `CONTEXT.md`，理由是它的最终
  措辞依赖 D1 / D3；若下游误以为词条已落盘，以 `CONTEXT.md` 的实际内容为准。
