## Summary

定稿 issue #31 的回应预算规格，并将决定落盘为 glossary / ADR 与三张有依赖的实施票。

```text
CONTEXT.md（五条词条处置：四条泛化、跳口径不变） + ADR-0007（人类面只写上界）
                         ↓
issue #31 + app/specs/issue-31-spec.md（逐字一致的正本镜像）
  ├─ #53 配置落点 + 只读呈现
  ├─ #54 人类面写入口 + ADR            blocked by #53
  └─ #55 ADR 实测依据 + 默认档位复核    blocked by #54
```

裁定为 W1 + L2（频道设置 `PATCH /api/channels/{id}/budget`、库内逐频道配置、缺省内置值）、固定档位 3 / 5 / 10 且默认仍 3；修改在下一人类世代生效。ADR-0002 保留原文，ADR-0007 只记录写入边界收窄。

**文件**：`CONTEXT.md`、`docs/adr/0007-human-face-writes-budget-limit-only.md`、`app/specs/issue-31-spec.md`、`app/specs/issue-31-acceptance.md`、本 PR body 文件。零生产代码与测试改动。

## Evidence

- **Before:** 上界是 `BUDGET_LIMIT = 3`；频道只有只读预算 GET，没有设置 UI 或人类面写入口。
- **After:** `gh issue edit 31 --repo whutlichao/alibot --body-file ...` → `https://github.com/whutlichao/alibot/issues/31`；`diff -q` 验证 GitHub body 与离线规格正文逐字一致（stdout 为空，exit 0）。
- **Tickets:** #53 / #54 / #55 均为 #31 sub-issue 且 `ready-for-agent`；`gh api .../issues/54 --jq .issue_dependencies_summary` → `{"blocked_by":1,"blocking":1,"total_blocked_by":1,"total_blocking":1}`；#55 → `{"blocked_by":1,"blocking":0,"total_blocked_by":1,"total_blocking":0}`。
- **Gate:** `git diff --check` 通过；按任务限制未运行 `pytest`、`node --test`、双轴 code-review、G-impl 或任何实施动作。

## Merge Danger

**Door:** two-way — 仅规格、词汇与决策记录；若需要调整，可通过新规格 / ADR 提交修订。ADR-0002 保持不变。

**Blast Radius:** documentation-only — 不改变运行时行为；三个实施 issue 在后续独立执行并按原生依赖排队。
