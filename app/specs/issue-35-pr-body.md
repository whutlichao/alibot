## Summary

#35 已定稿：D1=判据-4（仅呈现层降档，未读与预算污染为已知代价）、D3=落点-b（新增 `silent` status + ADR-0008）、D4=形态-a（消息级降档，不合并）。实施拆为两张子票，#58 原生 blocked by #57。

```text
#35 规格正本
├─ #57 prompt 纪律改写（含 D5 措辞定稿）+ 历史/未来旁白消息级降档
└─ #58 结构化 `silent` + RoundOutcome 互斥校验 + 状态/CHECK/schema 处置
   └─ blocked by #57

CONTEXT.md                 沉默 / 旁白式交代 / silent 边界
docs/adr/0008-*.md         持久化状态契约与迁移口径
app/specs/issue-35-spec.md  issue 正文离线留档
app/specs/issue-35-acceptance.md  回执与验收取证
```

## Evidence

- **Before:** #35 的 D1 / D3 / D4 尚未定稿，且没有实施拆票。
  **After:** `gh issue view 35 --json body` 显示三项裁定、候选对照表与结论、两票拆分及已知代价；正文无待决占位。
- `gh api repos/whutlichao/alibot/issues/58 --jq .issue_dependencies_summary` → `{"blocked_by":1,"blocking":0,"total_blocked_by":1,"total_blocking":0}`。
- `git diff --check` 通过；本 PR 仅含规格、领域词条、ADR 与验收文档，不改生产代码或测试。按规格票范围不跑源码测试、双轴 code-review 或 G-impl。

## Merge Danger

**Door:** Two-way — 本 PR 记录规格与 ADR；若未来要改判，须以新的决策记录明确替代，不涉及当前运行数据。

**Blast Radius:** Specification

实现不在本 PR 中；两张实施票仍需按原生依赖顺序推进。D1 明确不修复旁白造成的未读、唤醒与预算污染。
