## Summary

三处**存量注释**里「到期触发的唤醒不在本票授权范围内 / 到点不会产生消息」这条**已经不实**的断言，改成本能力成立后的真话。**只改注释**：取值、控件、渲染、后端零改动。

```diff
 app/alibot/studio/index.html
   提醒注释整块 :646-654
-    列表 .reminder-row（时间 / 文本 / 归属频道 / 删除）
-    新增控件只有 type="time"、没有日期与重复字段（本票不扩张）
-    副标题与空态正文不照抄原型那句「到点后 ⏰ 系统消息会…」：
-    「到期触发的唤醒」不在本票授权范围内…照抄会让界面声称一个不存在的行为
+    列表 .reminder-row（文本 · 归属频道 · 状态后缀 · 删除）   # 按 ③ 的实际形态，无独立时刻列
+    新增控件：文本 + 日期 + 钟点两个原生输入（对原型的显式偏离，ADR-0017 §六）
+    副标题与空态正文可以说「到点后系统事件消息会出现在归属频道的消息流中」——这句现在是真的
+    但不能说到点会唤醒 Agent；不落消息有两支（不归属 / 超过 24 小时未补投）

 app/alibot/studio/i18n.js
    zh 注释块 :392-395   同口径
    en 注释块 :857-860   同口径
```

三处落点的口径，按 `ReminderDeliverer` 的五分支表写（**不是**规格 C19 的两支）：到点落消息的只有「准点 / 补投」，不落消息的有两支——无归属，以及**有归属但距到期 > 24 小时**才被发现（不补投，人类面标「已错过」）。

```text
有归属 · 窗口内   → 落一条 system 消息    + fired_at      (准点 / 补投)
有归属 · > 24h    → 只写 fired_at，不落消息              (窗口外，界面标「已错过」)
无归属            → 只写 fired_at，不落消息              (无处可落)
到点全员          → 不唤醒、不消耗回应预算、不计未读
```

## Evidence

- **Before:** `grep '到期触发' app/alibot/studio/**` ⇒ 3 hits（`index.html:653`、`i18n.js:394`、`i18n.js:860`）；`index.html:649-650` 还写着「新增控件只有 `type="time"`、**没有**日期」——而 ③ 已在 `:672-682` 落地日期控件。
  **After:** 同一 grep ⇒ **0 hits**；`due-time wake-up`、`到点会发一条`、`新增控件只有` 同样 0 hits。

- **Before:** 注释把到点写成「有归属就落消息」两支。
  **After:** 三处都补上「有归属 + 距到期超过 24 小时不补投 ⇒ 只记已到点」这支（双轴 review 的 Standards P1），与 `app/alibot/reminder_delivery.py` 的五分支表、ADR-0017 §三、`CONTEXT.md` **到点** 逐条对齐。

- **门禁（本 worktree 实跑，全绿）:**

  ```text
  .venv/bin/python -m pytest                     551 passed, 2 skipped   (311s)
  .venv/bin/ruff check app                       All checks passed!
  .venv/bin/ruff format --check app              121 files already formatted
  .venv/bin/python -m mypy                       Success: no issues found in 53 source files
  node ~/.pi-lens/tools/node_modules/pyright/…   0 errors, 0 warnings, 0 informations
  node --test app/tests/i18n_ui.test.cjs         tests 53 / pass 53 / fail 0
  ```

- **两层文案未挪层:** `system.reminder.*` 仍只在后端 `i18n.py`（4 处 = 2 键 × zh/en）；前端 `reminder.*` 仍 16 键、zh/en 各出现恰好 2 次、无新增键；`i18n.js` 全文不含 `system.reminder.` 前缀（守住 `test_reminder_delivery.py:438`）。

- **双轴 review:** Standards 轴 1×P1 + 3×P2（P1「窗口外」已修，两处 P2 已修，重复重述一项按写面判据保留）；Spec 轴 `OK with notes`，无缺失、无 scope creep，唯一 P2 是 `app.js` 的 #68 历史段（在不写面内，登记为后续清理）。

## Merge Danger

**Door:** two-way —— 纯注释，回滚只需 revert 一个文件的两个注释块，无数据、无迁移、无接口面变化。

**Blast Radius:** 无。注释不被任何运行时读；`i18n_ui.test.cjs` 与 `search_ui.test.cjs` 的标记断言都先剥掉注释（`replace(/<!--[\s\S]*?-->/g, "")`），改注释不影响任何判据。

与 ③ 的**物理重叠**已按本票义务处理：本票是后合并者，开工时已 rebase 到 `5f36251`（③ 的合并提交），冲突面为零。
