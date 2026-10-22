## Summary

把规格 #228 拆成 7 张 tracer-bullet 实施票，发布到 GitHub Issues，并留下仓库侧拆分记录。

```text
#228 规格（Agent 家目录与全局层）
├── #230 ① 规则三段拼装 + 全局根契约与只读守卫   (frontier, blocked_by 0)
├── #231 ⑦ 数据目录默认与兼容                    (frontier, blocked_by 0)
├── #232 ② 技能四层合并                          (blocked_by #230)
├── #233 ③ MCP 声明三层合并                      (blocked_by #230)
│   └── #236 ④ 全局远端 MCP 确认                 (blocked_by #233)
├── #234 ⑤ 全局记忆只读                          (blocked_by #230)
└── #235 ⑥ Docker 第二只读挂载                   (blocked_by #230)

仓库留档
├── app/specs/agent-home-layers-tickets.md
├── app/specs/agent-home-layers-tickets-answer.md
└── app/specs/agent-home-layers-tickets-pr-body.md
```

与设计笔记 §7 的三处调整（按 to-tickets 垂直切片原则）：A 折叠进 ①（无独立可观察验收面）；C 拆成 ②/③（输入、装配结果与测试替身不同）；F 保持独立（不同 seam）。D11 的 seam 总数 = 2 不变，零 schema 变更。

## Evidence

- **Before:** `gh issue list --state open` 里本能力只有规格票 #228；没有任何实施票或阻塞边。

  ```text
  228  规格票：Agent 家目录与全局层 —— ADR-0031 + C1–C18 合成为可实施行为契约（两条既有 seam）
  ```

- **After:** 7 张实施票全部 open、全部是 #228 的 native sub-issue、全部 `ready-for-agent`；5 条原生 `blocked_by` 边，读数 `0 / 0 / 1 / 1 / 1 / 1 / 1`，frontier = #230、#231。

  ```text
  #230 blocked_by=0 blocking=4 labels=ready-for-agent
  #231 blocked_by=0 blocking=0 labels=ready-for-agent
  #232 blocked_by=1 blocking=0 labels=ready-for-agent
  #233 blocked_by=1 blocking=1 labels=ready-for-agent
  #234 blocked_by=1 blocking=0 labels=ready-for-agent
  #235 blocked_by=1 blocking=0 labels=ready-for-agent
  #236 blocked_by=1 blocking=0 labels=ready-for-agent
  ```

- **零源码改动:** `git diff origin/main -- app/alibot/ app/tests/ docs/adr/ CONTEXT.md` 为空；`SCHEMA_VERSION` 仍为 23（`app/alibot/db.py:113`）。

## Merge Danger

**Door:** two-way

只新增 tracker 票与 `app/specs/` 下的三份 Markdown；不触碰源码、测试、迁移、CI、ADR、设计笔记、`CONTEXT.md` 与 #228。回退只需 revert 本 PR，并关闭 #230–#236。

**Blast Radius:** docs-only

票图的编号与依赖边已发布到 tracker；若本 PR 被回退，tracker 上的票仍在，需要同时关闭，避免留下无正本的开放票。
