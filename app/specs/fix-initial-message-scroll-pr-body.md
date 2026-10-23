## Summary

刷新页面或重开浏览器后，工作台会选中记住的频道，但 `#viewMessages` 从最老一条开始显示：
`loadWorkspace()` 完成快照读取并 `render()` 重建消息面后没有恢复滚动位置。
现在只在**首次成功载入**时补一次既有的 `scrollToBottom()`；之后的静默刷新（重连、
`{ silent: true, keepActive: true }`）不滚动，正在回看历史的用户不会被拉回底部。

```diff
+let workspaceLoaded = false;
+
 async function loadWorkspace(options) {
+  const firstSuccessfulLoad = !workspaceLoaded;
   try {
     ...
     await loadPendingApprovals({ render: true });
+    workspaceLoaded = true;
   } catch (err) {
     ...
   }
   render();
+  if (firstSuccessfulLoad && workspaceLoaded) scrollToBottom();
   markCurrentRead();
 }
```

`workspaceLoaded` 记录的是「整轮工作台读数已成功落地」，因此首载失败、由
`scheduleReconnect()` 完成的第一次成功载入也算「首次成功载入」，同样贴底。
没有新增滚动位置追踪状态；`render()` / `selectTarget()` / `onStreamMessage()` 未改动。

## Evidence

**Before：** `scripts/red-evidence.sh main app/tests/initial_scroll_ui.test.cjs`
（基线 `main` = `81143dd`，基线 `app.js` md5 `bec25335a3372138ff7e32827d33c05b`）

```text
✖ 首次载入工作台后消息流贴底；静默刷新不改变回看位置
  AssertionError: 首次载入后消息流应停在最新一条 — 0 !== 640
✖ 首次载入失败后，重连完成的第一次成功载入仍贴底
  AssertionError: 重连完成的首次成功载入应贴底
ℹ tests 2 / pass 0 / fail 2
exit=1
```

**After：** `scripts/check.sh --studio`

```text
✔ 首次载入工作台后消息流贴底；静默刷新不改变回看位置
✔ 首次载入失败后，重连完成的第一次成功载入仍贴底
ℹ tests 276 / pass 276 / fail 0
pytest 20 passed；ruff check / ruff format --check / mypy / pyright 通过
门禁全绿
```

用例只断言外部可观察行为：`#viewMessages.scrollTop` 是否等于 `scrollHeight`；
替身把 `scrollHeight` 设成非零值，用非底值验证静默刷新不改写 `scrollTop`。

## Merge Danger

**Door:** two-way —— 纯前端滚动行为，回滚只需撤掉一处条件调用。

**Blast Radius:** minimal —— 只影响 `loadWorkspace()` 成功载入后的滚动位置；
无 schema / 接口 / 依赖变更，不触达后端与其它前端模块。

## Spec 判定记录

票面第 2 条（首次**成功**载入贴底）与第 3 条（依据 `state.ready` 判断）存在内部冲突：
`state.ready` 在失败分支也会置 true。经 coordinator 定夺按第 2 条行为正本实施，
新增「已成功载入过」标记；第 3 条视为实现提示。
