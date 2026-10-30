## Summary

频道头右侧五个图标入口合并为两枚，三张频道面收进一个宽弹窗的顶部页签：

```diff
 <ChannelHeader>
-  <button data-od-id="btn-channel-members" />
-  <button data-od-id="btn-channel-settings" />
-  <button data-od-id="btn-channel-budget" />
-  <button data-od-id="btn-channel-archive" />
+  <button data-od-id="btn-channel-tools" />
   <button data-od-id="btn-run-panel" />   <!-- 不变：dock 的过程视图 -->
```

```text
<ChannelToolsModal> (ModalShell.wide)
  role=tablist: 成员 / 设置 / 预算
  ├─ ChannelRosterPanel     (原 RosterModal)
  ├─ ChannelSettingsPanel   (原 ChannelConfigModal；危险区 = 归档 + 删除)
  └─ ChannelBudgetPanel     (原 ChannelBudgetModal)
```

- 三个面板在打开时一次挂载并各自读一次后端；页签只切 `v-show`，不重读、不丢未保存草稿与档位选择。
- 归档仍在「设置」页签危险区当场执行（不弹二次确认）；成功后关弹窗、左栏把频道移入归档段、
  频道头只剩「取消归档」（ADR-0040 的只读归档语义不变）。
- 运行面板保持独立入口；私信与归档态的入口可见性不变。

## Evidence

- **Before:** 基线 `f31a9df` + 最终测试文件（隔离 worktree）：
  `Test Files 4 failed | 1 passed (5)` / `Tests 40 failed | 85 passed (125)`。
- **After:** 同一命令：`Test Files 5 passed (5)` / `Tests 125 passed (125)`。
- **Gate:** `scripts/check.sh --fast` → `Test Files 25 passed (25)` / `Tests 500 passed (500)`，`门禁全绿`。
- 新增用例覆盖：频道头单入口与旧入口移除、默认成员页签、三页签分别铺正文、
  切页签保留设置草稿与预算档位、危险区归档按钮、归档成功关弹窗并只剩取消归档、
  归档失败不关弹窗、私信与归档态可见性、运行面板入口不变。

## Merge Danger

**Door:** two-way

**Blast Radius:** 前端导航（`/studio/v2` 的频道头与成员 / 设置 / 预算三张面）；无 API、schema
或后端改动。回滚即 revert 本提交，三张面与入口原位恢复。
