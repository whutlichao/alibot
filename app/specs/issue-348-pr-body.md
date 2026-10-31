## Summary

```diff
 app/alibot/studio/app.css
 /* 全局 MCP 只读行（#236）… */
 .global-mcp-row .v { display: flex; … }
+/* 家目录 `mcp.json` 文本域（#348 设计反馈）：… */
+.textarea.agent-mcp-file-text { min-height: 240px; }

 app/frontend/tests/agent-detail.test.js
+test('家目录 mcp.json 文本域默认高度 240px（作用域规则，全局 `.textarea` 不动）', …)
```

Agent 详情 → MCP 页签的家目录 `mcp.json` 文本域（`textarea#agentMcpFileText`）原先只继承全局
`.textarea` 的 `min-height: 70px`（约 3 行），常见规模的文件只能看到几行。本票只给该文本域一条
作用域规则，把默认高度提到 240px（约 10 行）；`resize: vertical` 沿用全局规则，仍可手动拖大。
全局 `.textarea` 一字未动，表单里的其它文本域不受影响。

选择器写成 `.textarea.agent-mcp-file-text` 而不是单类：全局 `.textarea` 规则位于 `app.css` 更靠后的
表单段，单类选择器同特异性且更早，会被按顺序覆盖（真实浏览器实测仍是 70px）；复合选择器按
特异性取胜，与 `.agent-skill-section > .resource-add` 覆盖全局 `.resource-add` 是同一做法。

## Evidence

- **Before:** 隔离基线 `e871d33`（`git worktree`）+ 只带新用例的工作树 → `Tests 1 failed | 55 passed (56)`，
  失败在「缺少 mcp.json 文本域的作用域规则」。
  真实浏览器（ego-browser 0.6.0.20 / Chromium 154，视口 1982×1011；`~/.alibot/data` 副本 + 端口 8799）：
  单类规则版本 `getComputedStyle(#agentMcpFileText).minHeight` = **70px**，`clientHeight` 68。
  **After:** 同一用例 `Tests 56 passed (56)`；`scripts/check.sh --fast` → 25 files / 502 tests 全绿，
  ruff / format / mypy / pyright 全绿。浏览器：`minHeight` = **240px**，`offsetHeight` = 240
  （`box-sizing: border-box`，含上下 1px 边框），`clientHeight` = 238，`resize` = `vertical`。
  截图：`/tmp/alibot-348/shots/green-mcp-textarea-final.png`；原始读数与输出：`/tmp/alibot-348/`。
- 契约用例同时断言元素 `tagName` 为 `TEXTAREA`、仍带 `textarea` + `agent-mcp-file-text` 两个类、
  正本选择器为 `.textarea.agent-mcp-file-text`、块内 `min-height: 240px`，并反向断言全局
  `.textarea` 仍为 `resize: vertical` / `min-height: 70px`。
- 双轴 review（Standards + Spec，起点 `e871d33`）：findings 与逐条处置见
  `app/specs/issue-348-acceptance.md`。

## Merge Danger

**Door:** two-way

**Blast Radius:** 一个文本域

规则只命中 `textarea#agentMcpFileText`（`agent-mcp-file-text` 在源码中唯一）；全局 `.textarea`、
其它表单文本域、Vue 模板、i18n 与构建配置均未改。回滚即删一条 CSS 规则（与配套用例）。

Closes #348
