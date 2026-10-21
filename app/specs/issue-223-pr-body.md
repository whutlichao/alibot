## Summary

频道指针 chip 的计数从「任务消息总条数」换成「任务未读数」，0 未读时连计数段一起收掉：

```diff
 appendTaskRefChip (app/alibot/studio/app.js)
-  count = pointedTask.message_count
-  chip  = 「进入任务线程 · {count} 条」
+  unread = pointedTask.unread_count            # 已读位置之后、作者非本人的人/Agent 消息
+  chip   = unread > 0 ? 「进入任务线程 · {count} 条未读」 : 「进入任务线程」
+  chip.dataset.taskPointer = task id           # 定点刷新的选择子

 markTaskRead (POST /api/tasks/{id}/read 之后)
   state.tasks[i] = payload.task
+  syncTaskPointerChips(payload.task)            # 只改既有 chip 的文本与 aria
   renderTaskThread()
   renderTaskBoard()
```

- 后端未读口径（`UNREAD_WHERE_SQL`）、`get_task()`、`message_count` 字段一个字没动。
- 打开线程完成已读、线程内新消息被吸收，两条路径都经 `markTaskRead()` 的同一个出口；
  频道消息流**不重铺**（同一节点、滚动位置与焦点不动），也不重拉 `/api/channels/{id}/messages`。
- 中英词条各 +2 键（`msg.taskThreadEntry[Unread]` / `msg.taskThreadEntryAria[Unread]`）；
  `CONTEXT.md` 两个词条与 ADR-0028 文末修订同步。

## Evidence

- **Before:** `scripts/red-evidence.sh main app/tests/task_pointer_ui.test.cjs`（基线 `0e6c56d`，
  `app.js` md5 `b627ed7048cd5dd64baff54666d139ef`）→ `tests 15 / pass 10 / fail 5`：
  - `actual: '进入任务线程 · 9 条'` vs `expected: '进入任务线程 · 4 条未读'`（读的是 `message_count`）
  - `actual: '进入任务线程 · 0 条'` vs `expected: '进入任务线程'`（任务不在快照时仍显示 0 条）
  - 打开线程已读后 chip 停在旧计数（无定点刷新）
- **After:** `node --test app/tests/task_pointer_ui.test.cjs` → `tests 15 / pass 15 / fail 0`；
  `node --test app/tests/i18n_ui.test.cjs` → `tests 53 / pass 53 / fail 0`；
  `scripts/check.sh --fast` → ruff / format / mypy / pyright 全过、node 254 绿、门禁全绿。
  完整读数见 `app/specs/issue-223-acceptance.md`。

## Merge Danger

**Door:** two-way（纯前端呈现 + 词条；回滚即恢复旧 chip 文案，无迁移、无 schema 变化）

**Blast Radius:** low（Studio 频道消息流里 `task_pointer` 那一行；后端载荷与未读口径不变，其它消费方不受影响）
