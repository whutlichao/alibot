## Summary

Closes #217

频道里的**指针消息行**（`message.task_pointer` 非空的 system 消息）现在也在该任务 target 有 pending 时
显示与任务卡片**同款**的「待审批 N」角标。归属、词条、类名、收口出口全部复用既有那一份，不新造第二套。

```diff
 #209 建的三处外显：任务卡片 · 列表行 · 频道「任务」页签圆点
+#217 补第四处：频道指针消息行（人在消息流里唯一指向该任务的入口）

 appendTaskRefChip(body, message)            # 频道 system 行的 chip 出口
   task_ref  ─────────────→ .task-ref-chip（既有，任务线程内，未改）
   task_pointer ──────────→ .task-ref-chip「进入任务线程 · N 条」（既有）
+                            └─ approvalBadgeNode(pointedTask)  # 有 pending 才追加 .card-approval

 syncApprovalBadges()                        # 「任务 target → pending 条数」的唯一出口
   signature 未变化 ────────→ return（秒级 tick / 未变化的 12s 轮询都不重铺）
   signature 变化 ──────────→ renderTaskBoard()  # 既有
+                             renderMessages({ approvals: false })  # 只在消息视图里重铺消息行
```

```diff
 app/alibot/studio/
 ├── app.js   # approvalBadgeNode() 抽出共用的角标节点；appendTaskRefChip 追加角标；
-│            # syncApprovalBadges 的签名变化分支只调 renderTabs()（不碰消息流、不碰审批板）
+│            # syncApprovalBadges 的签名变化分支重铺消息流（renderMessages({ approvals: false })，
+│            # 只在消息视图里；看板视图仍只刷页签）
-└── app.css  # 没有指针消息行的角标样式
+└── app.css  # 只为 .msg > .msg-body > .card-approval 补对齐（既有 token，不动 .card-approval 本身）
 app/tests/
-├── approval_dom_harness.cjs  # 只覆盖审批卡 / #209 的角标与圆点
-└── task_pointer_ui.test.cjs  # 只有 #198 的 3 条指针消息用例（自建窄装置）
+├── approval_dom_harness.cjs  # +三处能力：messages 初始载荷、renderMessages 导出、
+│                             # #messageList / elementFor 暴露（装置仍只有一份）
+└── task_pointer_ui.test.cjs  # +10 条 #217 用例（复用上面那份装置，不复制第二套）
```

三个要写进 review 的决定：

1. **角标不是第二个点击目标**：它是 `<span>`（无 `data-open-task` / `role` / `tabindex`），点击继续冒泡到
   既有 chip 与 `#messageList` 的委托路径——整行可点区域一字未改。
2. **归属只按 `pointedTask.target_id`**：频道 target 的 pending、别的任务的 pending 都不出现；任务不在
   任何快照里时**不画**角标（没有读数，不编造 0 以外的数）。
3. **收口挂在 `signature` 变化上**：live `approval_required` / `approval_resolved` 与本地 600s 过期都在
   `syncApprovalBadges()` 的同一支里重铺消息流；未变化时零额外重建（秒级 tick 不重建卡片那条纪律原样保留）。
   重铺沿用既有 `renderMessages()`，草稿 / pending / 滚动位置不新增任何保存恢复逻辑。

## Evidence

- **Before**（隔离基线副本，只换测试不换产品代码）：
  `scripts/red-evidence.sh d138d3f app/tests/task_pointer_ui.test.cjs`（基线 app.js md5
  `bb30c379f3a55801d50f15ee1dc23783`）→ `tests 13 / pass 5 / fail 8`。新增的 **10** 条 #217 用例里
  **8** 条红（角标不存在 / live 帧不更新 / 600s 不收口 / 点角标不该开线程 / 看板快照读域 /
  重铺边界两条 / 重铺后草稿与 pending）；另 2 条（无 pending 不画、归属不串台）在基线上本就该绿——
  它们钉的是「不该发生」的行为，作用是防止实现「一加就到处加」。
  基线 `task_pointer_ui.test.cjs` 只有 **3** 条 #198 用例（`git show d138d3f:… | grep -c '^test('` = 3），
  这 3 条在红证据里全绿 ⇒ 接缝与装置未被新用例破坏。
- **After**（本分支）：
  `node --test app/tests/task_pointer_ui.test.cjs` → `tests 13 / pass 13 / fail 0`；
  票面指定的两份一起跑 → `tests 21 / pass 21 / fail 0`。
- **门禁**：`scripts/check.sh --studio` exit 0 —— pytest 窄档 `20 passed`、ruff check 全过、
  `ruff format --check` `271 files already formatted`、mypy `114 source files` 无问题、pyright
  `0 errors, 0 warnings`、node glob `247 pass / 0 fail`。本地不跑全量 pytest（CI 跑同一脚本）。
- **变异验证**（证明新用例咬得住）：把 `renderMessages({ approvals: false })` 改回 `renderMessages()`
  → 「一次签名变化只把频道审批板铺一次」转红；把签名读域改回 `state.tasks` → 「任务只在看板快照里」转红；
  各自还原后 13/13。
- **真实浏览器**（隔离数据目录 `/tmp/issue217-data2` + `127.0.0.1:8792`，ego-browser）：
  指针消息行读到 `待审批 1` / `「整理发布清单」有 1 条待审批`，`SPAN` 且 `data-open-task` 为 `null`，
  计算样式与卡片同 token（`--warn-deep` + 等宽）；从 HTTP 收口该审批后角标消失，全程
  `messagesRequests: 0`。另一轮（`/tmp/issue217-data` @ 8791）补测了「只靠 12s 派生集合刷新就出现」、
  「live 帧收口」与「本地 600s 上界由秒级 tick 收口」。截图 `/tmp/issue217-final-*.png`（不入库）。
- **双轴 code-review**（Standards + Spec 两个并行子代理）：Standards 报 2 条硬伤（审批板同一拍重建两遍、
  composer 断言是假绿）+ 4 条中 + 若干软；Spec 报 2 条必须修（签名读域漏 `boardTasks`/`activeTask`、
  看板视图下重铺消息流会清空 `#approvalBoard`）。**全部修完并各补了会转红的用例**；两条有意不修
  （用例文件搬迁、live 入口补一条同义路径）连同理由写在 `app/specs/issue-217-acceptance.md` §7。
- 逐条判据与读数：`app/specs/issue-217-acceptance.md`。

## Merge Danger

**Door:** two-way

纯前端呈现改动：`app/alibot/studio/{app.js,app.css}` + 两份前端用例与一份装置的最小扩展。回滚只需 revert
这一个 commit，无迁移、无数据改写、无后端契约变化。

**Blast Radius:** studio-message-stream

- **不新增外部契约**：没有新端点、没有新 SSE 类型、没有新词条（`i18n.js` **未改**）；`message` / `approval_request`
  的存储与语义一字未动。审批**仍然不是消息**：不占 seq、不进未读、不进搜索。
- **后端零改动**：`app/alibot/**/*.py`、schema、审批 API 全部未动。
- **可点区域不变**：`task_ref` 行与 `task_pointer` 行的 chip 逐字未改；角标是纯呈现节点。
- **重铺纪律保留**：`syncApprovalBadges()` 仍然只在 `signature` 变化时重铺；秒级 tick 仍只改倒计时文本。
  重铺消息流时带 `approvals: false`（`renderApprovalSurfaces()` 已经铺过频道审批板，不再重复铺一次），
  且只在**消息视图**里重铺——看板视图下消息流是 `hidden` 的，这一拍只刷页签，审批板不被清空。
- **签名读域变了**：`taskApprovalSignature()` 从只看 `state.tasks` 扩到 `activeTask ∪ tasks ∪ boardTasks`
  （与 `taskById()` 对齐）。这是一处行为修正：筛选视图 / 刚打开的线程里，任务快照不在 `state.tasks` 时
  角标以前不会跟着变。读的仍是同一个 `pendingApprovalCountForTarget()`，没有新增计数路径。
- **失效方向**：任务不在快照里 ⇒ 不画角标（少显示，不多显示）；读数只来自 `state.approvals`，与卡片同一份镜像。
