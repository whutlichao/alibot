## Summary

把 Agent 详情危险区的「删除」从内联确认区改成既有模态骨架的确认弹窗；顺带删掉两行重复提示，
并改写全局 MCP 空态文案。

```diff
 <AgentDetail> (app/alibot/studio, state.mode === 'agent')
   <DangerZone> agentDangerSection()
     dangerHint
-    hint: agentDetail.pauseNote
     暂停 / 恢复 (button)
-    删除 (button)
-    <div.danger-confirm hidden=!confirming>
-      agentDetail.deleteConfirm
-      <确认删除> / <取消>
+    删除 (button → 零请求，打开弹窗)
+    <GlobalMcp> globalMcpSection()
-    hint: agentDetail.globalMcpNote
+    hint: “全局mcp.json”里没有声明MCP服务
+
+ <Overlay#agentDeleteOverlay> (index.html, 与 #taskOverlay 同骨架)
+  modal-head: agentDetail.deleteTitle
+  modal-body: #agentDeleteText ← agentDetail.deleteConfirm (名称 + 不可撤销)
+  modal-foot: 取消 / 确认删除
+
+ on(删除): openAgentDeleteModal()        // 0 requests, focus → 取消
+ on(取消|关闭|遮罩|Escape): closeAgentDeleteModal()  // 0 requests
+ on(确认删除): deleteAgent(state.agentDelete.memberId) // 1 × DELETE
+   success: close modal → refresh agents/members/channelMembers → close detail → toast
+   failure: keep modal open, re-enable buttons, reason via toast
+   in-flight: confirm/cancel/close + 删除 all disabled
```

`state.agentDelete` 收敛为 `{ memberId, busy }`；`resetAgentDeleteState()` 由
`closeAgentDeleteModal()` 取代。删除成功路径与失败路径的镜像刷新语义不变。

## Evidence

**Before（隔离基线 `ad5ddf9` + 当前工作区测试）**：`scripts/red-evidence.sh ad5ddf9 app/tests/agent_detail_ui.test.cjs`
→ exit 1，`tests 42 / pass 31 / fail 11`，基线 `app.js` md5 `bb1c9e24ff51bffdd749deced04a3792`。
失败清单（11 条）见 `app/specs/agent-delete-confirm-modal-acceptance.md`。

**After（窄档）**：

- `node --test app/tests/agent_detail_ui.test.cjs` → `42 pass / 0 fail`
- `node --test app/tests/i18n_ui.test.cjs` → `53 pass / 0 fail`（两份键集同形、`index.html` 无裸中文）
- `scripts/check.sh --studio` → exit 0：审批 HTTP 契约 `20 passed`、ruff / mypy / pyright 0 错误、
  Node 用例 `295 pass / 0 fail`，末行「门禁全绿」

**真机（隔离端口 8799 + 临时数据目录，ego-browser，1350x978 / 420 宽）**：

| 动作 | 弹窗 | 详情 | DELETE |
| --- | --- | --- | --- |
| 点「删除」 | 打开（焦点 = 取消） | 仍打开 | 0 |
| 取消 / 遮罩 / Escape | 收起 | 仍打开 | 0 |
| 确认删除 | 收起 | 关闭 | 1 |

- 1350x978：`#agentDeleteOverlay .modal` rect `455,117 → 895,316`（440×198）；
  420 宽：`16,108 → 404,306`（388×198）；两处 `documentElement.scrollWidth == viewport.w`，无水平越界。
- 确认后左栏已无该 Agent，toast = 「已删除 Agent「弹窗测试员」。」。

## Merge Danger

**Door:** two-way

**Blast Radius:** app/alibot/studio/** 表现层 + app/tests/agent_detail_ui.test.cjs + 文档

只改零构建 Studio 表现层与一个既有 DOM 用例文件；后端 / HTTP 契约 / schema / 构建配置未动。
删除的接口语义、成功 / 失败路径、`agents`/`members`/频道成员缓存刷新与 toast 均保持原样，
回滚只需 revert 本 PR。
