## Summary

Agent 详情头部按用户 2026-10-24 反馈精简为**名称 + 强调色「发送消息」**：去掉「Agent」小标签、
状态行与头部关闭按钮，按钮复用既有 `.btn-primary`（`--accent` / `--on-accent`），零新色值、零内联样式。

```diff
 #viewAgent > .agent-page-head
-├── adName + "<span class=dock-tag>Agent</span>"
-├── dock-role: #adPresence + #adStatusText
-├── <button class="btn btn-sm" id=btnAgentSendMessage>发送消息</button>
-└── <button class="icon-btn" id=btnAgentDetailClose>✕</button>
+├── #adName
+└── <button class="btn btn-sm btn-primary" id=btnAgentSendMessage>发送消息</button>
```

- 行为正本不动：`syncAgentSendButton()` 的禁用判据、`agentDetail.sendMessage` 文案、
  `POST /api/dms` 后进入该私信并离开详情。
- 删除入口后，离开详情仍走左栏频道 / 私信行（`selectTarget()`）与键盘 `M` / `T`
  （`showMessagesView()` / `showTaskBoard()` → `leaveAgentView()`）；dock 的进入前开合只在删除成功的
  `closeAgentDetail()` 路径恢复。`closeAgentDetail()` 本身保留。
- 词条 `agentDetail.tag` / `agentDetail.closeAria` 随节点一起删；`applyPresence` / `presenceNode` /
  `statusLabel` 的其它调用点（左栏 presence 格、dock 成员卡、名册）一字未动。

## Evidence

- **Before:** `scripts/red-evidence.sh 2497bcc26a72580569c48856b5da6c13d54c0634 app/tests/agent_detail_ui.test.cjs`
  → 基线 `app.js` md5 `e041cd74d3c7f7509e2cf23b900134e9`（证据目录 `alibot-red.2JFX1h`）：`tests 50 / pass 47 / fail 3`。
  三条红 = 基线仍有两份 `agentDetail.tag`、发送按钮没有 `btn-primary`、`renderAgentDetail()` 各查 3 次
  `#adPresence` / `#adStatusText`。
  **After:** `node --test app/tests/agent_detail_ui.test.cjs` → `50/50`。
- **Gate:** `scripts/check.sh --studio`（合并 `origin/main` 后）→ pytest `20 passed`、ruff
  `All checks passed`、`ruff format --check` 通过、mypy `Success: no issues found in 124 source files`、
  pyright `0 errors, 0 warnings, 0 informations`、`node --test app/tests/*.test.cjs`
  `tests 310 / pass 310 / fail 0`、`门禁全绿`。
- **真实浏览器（ego-browser，隔离 `--port 50144 --data-dir /tmp/alibot-head-smoke-20261024`）**：
  zh-CN 头部 `button` 数 `1`、文本 `冒烟助手 发送消息`、`btnPrimary === true`、
  `btnBg === --accent`、`btnFg === --on-accent`；`#btnAgentDetailClose` / `#agent-detail-tag` /
  `.dock-role` / `#adPresence` / `#adStatusText` 全部不存在。切 English 后头部
  `冒烟助手 Send message`、无裸中文、同一组已删节点仍不存在。详情内 `#chanHead.hidden === true`
  且 `#tabMessages` 无可见布局盒；键盘 `M` 与点左栏频道行都能离开详情（`#viewAgent.hidden === true`）。
  截图 `/tmp/alibot-head-zh.png`、`/tmp/alibot-head-en.png`。
- 验收文档：`app/specs/studio-agent-detail-head-acceptance.md`（判据表、红证据目录与 md5、门禁与浏览器读数、
  双轴 review findings 与处置）。

## Merge Danger

**Door:** two-way

纯前端呈现节点与两条词条的删除；无数据 / schema / 迁移 / API 改动，回滚即恢复旧头部（含关闭按钮）。
唯一的行为变化是「详情页没有头部关闭入口」——用户已明确接受，剩余真实出口是左栏行与键盘 `M` / `T`。

**Blast Radius:** small

- 只碰 `app/alibot/studio/{index.html,app.js,i18n.js,app.css}`、`app/tests/agent_detail_ui.test.cjs`、`app/README.md`。
- `#viewAgent` 的其它区块、保存条、危险区、删除确认弹窗、全局 MCP、`dock` 其它形态一律未改。
- 死 CSS 已清理：`.agent-page-head .dock-role`（状态行删除后不再命中任何节点）随本票删除；
  `.dock-role` / `.dock-tag` 的其它落点保留。清理后 `scripts/check.sh --studio` 重跑全绿（node `310/310`）。

Closes: 无（用户 2026-10-24 对 `/studio/` 的反馈，无对应 issue）
