## Summary

`#taskCount` 原先只在 `renderTabs()` 里写一次，于是「任务全量快照 `state.tasks` 变了」与「页签计数重算」
是两条互不连通的路：建成任务后计数停在旧值，要等切换视图 / 消息流重渲才顺带刷新。

把那一行抽成计数的**唯一写入点**，由三条重渲路径共同调用：

```diff
 renderTabs()
   msgCount = renderedMessages().length
-  #taskCount = isDm ? 0 : channelTaskCount()
+  renderTaskCount()            // ← 唯一写 #taskCount 的地方

 renderTaskBoard()
+  renderTaskCount()            // 快照的渲染路径：refreshTasks() / markTaskRead() / refreshTaskDetails()

 openTask()                     // 主线停在看板上，不重铺看板 → 自己写一次
   state.tasks.push(thread.task)
+  renderTaskCount()
```

`state.tasks`（未筛选全量快照）的 6 处写入全部被覆盖：`refreshTasks()` 两处（:`1197` / `:1199`）、
`openTask()`（:`1930-1931`）、`markTaskRead()`（:`1956`）、`refreshTaskDetails()`（:`3102`）。
计数仍按**未筛选的全量快照**算，看板的频道筛选不会把它变成筛选后的条数。

新增 `app/tests/task_count_ui.test.cjs`（`vm` + fetch 替身 + DOM 替身，沿用既有接缝，未新开框架），
断言只落在用户可见的 `#taskCount` 文本上，驱动路径是真实 click（`#btnNewTask` → 填标题 / 频道 →
`#taskCreateSubmit`）。

## Evidence

- **Before**（base `c606b4a`，未修复）：`node --test app/tests/task_count_ui.test.cjs`

  ```text
  ✖ 从「新建任务」弹窗建成任务后，「任务」页签计数立刻变成新数量
    AssertionError: 任务快照对齐后，页签计数要立刻跟着变
    '1' !== '2'
  ✖ 打开一条原先不在快照里的任务后，「任务」页签计数同步增加
    AssertionError: 补进快照后页签计数要跟着变，不能只重渲线程
    '1' !== '2'
  ℹ pass 1   ℹ fail 2
  ```

  两条红都停在 `#taskCount.textContent` 上——正是用户报的那个数值，不是别的错。

- **After**（本分支，同一个命令）

  ```text
  ✔ 从「新建任务」弹窗建成任务后，「任务」页签计数立刻变成新数量
  ✔ 打开一条原先不在快照里的任务后，「任务」页签计数同步增加
  ✔ 看板筛到别的频道时，页签计数仍按未筛选的全量快照算
  ℹ pass 3   ℹ fail 0
  ```

- **全量门禁** `scripts/check.sh`（不加 `--fast`，CI 同一脚本）：`pytest 858 passed, 2 skipped`；
  `ruff check` All checks passed；`ruff format --check` 217 files already formatted；
  `mypy` 92 source files 无问题；`pyright` 0 errors；`node --test app/tests/*.test.cjs` 183 pass / 0 fail；
  末行「门禁全绿」，`exit=0`。

- **双轴 code-review**（固定点 `c606b4a`，两轴各一个子代理并行）
  - Standards：**无硬违规**。4 条判断题均判「维持」（`renderTaskBoard()` 顺带写计数不算 Divergent
    Change；`renderTaskCount()` 自算 DM 判据不算 Duplicated Code；仓内 12 份 `FakeNode` 是既有形态；
    第 3 条用例修复前即绿但是有效护栏）。唯一的措辞冲突（文件头称「只断用户可见文本」却断言了
    `state.tasks.length`）**已收口**：删掉该内部断言。
  - Spec：**无 P0/P1**。唯一写入点成立（全文只有 `renderTaskCount()` 写 `#taskCount`），`state.tasks`
    6 处写入全覆盖，`openTask()` 的「不重铺看板」分支写清了理由。P2「未跟踪的 `.evidence/*.txt`」
    **已清理**（读数录入 `app/specs/issue-177-acceptance.md`）。

  逐条读数见 `app/specs/issue-177-acceptance.md`。

## Merge Danger

**Door:** two-way

纯前端渲染的一次函数抽取：无 schema / 迁移 / API 语义变更，无新增依赖，回滚就是 revert 一个函数
与三处调用（计数会回到「只在页签重渲时刷新」的旧行为，没有数据后果）。

**Blast Radius:** `renderTabs()` 的 3 个调用点、`renderTaskBoard()` 的 6 个调用点、`openTask()` 的
2 个入口（看板卡片 / 任务线程里的关联消息）。最坏情况是计数多算一次或写空——三条路径读的都是同一份
`state.tasks`，重算是幂等的；`renderTaskCount()` 在节点缺失时直接返回，不新增抛错面。看板、消息流、
dock 线程的渲染与请求面均未改动。

Closes #177
