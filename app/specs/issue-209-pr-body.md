## Summary

Closes #209

线程关着时，任务 target 的待审批在任务看板/列表/任务页签都看不见。本 PR 用后端已有的**全工作区 pending
读数**驱动一层外显提示，不碰审批卡与后端语义。

```text
loadWorkspace / reconnect ──→ loadPendingApprovals()
                                  GET /api/approvals?state=pending   （不带 target_id）
                                  按 record.target_id 并入 state.approvals
                                  消失的 pending 标 expired（不伪造记录）
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
#approvalBoard（频道）          taskCardNode / boardList        #tabTasks 圆点
既有审批卡（不变）              「待审批 N」角标 + 根 aria 名     aria「该频道有 N 个任务…」
                                        ▲
        live approval_required / resolved ┘ 同一出口即时更新
        12s 单例轮询：document.hidden / 离线跳过；同一时刻只一个在途请求；
        快照未变化不重铺 DOM（不收起流转菜单、不换掉正在点的按钮）
```

```diff
 app/alibot/studio/
 ├── app.js       # 全量读数 + 统一快照写入口 + 角标/圆点 + 12s 有界刷新
 ├── index.html   # #tabTasks 的圆点元素
 ├── app.css      # .card-approval / .tab-approval-dot（既有 warn token）
 └── i18n.js      # zh + en：board.approvalBadge(Aria) / cardApprovalAria / tabs.tasksApprovalDot
 app/tests/approval_ui.test.cjs   # +8 条 #209 用例（沿用既有 vm + fetch + DOM 替身）
 app/specs/issue-209-acceptance.md
 app/README.md                   # 审批读面补 3 行（全量读数 + 12s 刷新）
```

关键纪律：

- **归属只看 target**：频道 target 的 pending 只在 `#approvalBoard`；任务 target 的 pending 才进卡片/
  列表/页签圆点，反向亦然。
- **「N」按 `approvalDisplayState()`**：后端终态优先，仍 pending 但越过 `requested_at + 600s` 视为
  expired；线程关着时由派生集合签名驱动，不必等下一轮 12s。
- **更新有界**：live 帧即时生效；周期刷新只在「target→pending 条数」变化时重铺看板/页签，快照完全
  未变化时连审批卡都不重建。

## Evidence

- **Before（基线副本 `/tmp/issue-209-red`，studio 文件取自 main，app.js md5 与 main 一致）：**
  `node --test app/tests/approval_ui.test.cjs` → `tests 14 / pass 7 / fail 7`；7 条 #209 用例全红。
- **After（本分支）：** 同一命令 → `tests 15 / pass 15 / fail 0`；全量 node glob → `228 pass / 0 fail`。
- **门禁：** `scripts/check.sh` exit 0：pytest `1046 passed, 10 skipped`、ruff check/format、mypy
  `114 files`、pyright `0 errors`、node glob `228 pass`。
- **真实浏览器（ego-browser + 隔离数据目录 /tmp/alibot-209-data，线程从未打开）：**
  - 任务页签圆点 `hidden=false`，aria `该频道有 1 个任务在等待审批`；
  - 卡片角标可见文本 `待审批 2`，aria `「修一处样式」有 2 条待审批`，卡片根 aria
    `修一处样式，待认领，2 条待审批，打开任务线程`；另一张任务无角标；
  - 频道 target 的 1 条 pending 只在 `#approvalBoard` 出现（截图与读数见验收文档 §4）。
- **双轴 code-review：** Standards 13 条 / Spec 7 条已逐条处置；无遗留 P0/P1（详见
  `app/specs/issue-209-acceptance.md` §5）。

验收文档：[`app/specs/issue-209-acceptance.md`](app/specs/issue-209-acceptance.md)

## Merge Danger

**Door:** two-way —— 纯 Studio 前端 + DOM 用例/文档，不改后端 API、schema、`approval.py` 或事件流语义；
回滚只需 revert 本分支。

**Blast Radius:** 局部（Studio 审批提示）——新增 12s 有界轮询（可见且在线时）；全量读数取代进入工作区时
按 target 的初次读数（同一端点、同一镜像，频道审批卡由全量读数恢复并有脚本用例自证）。审批卡本身的
呈现与提交逻辑未改，审批仍不进消息流 / 未读 / 搜索。
