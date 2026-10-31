# 验收记录：家目录 mcp.json 文本域增加默认高度

**票型**：实施票｜**门禁**：G-impl｜**测试档**：窄（改动只落共享样式正本的一条作用域规则与前端
一枚 CSS 文本契约用例；不触达 Python、schema、构建配置或跨语言接口）
**Ticket**：#348｜**base**：`e871d33`｜**branch**：`issue-348-impl`
**PR body**：`app/specs/issue-348-pr-body.md`
**采集时间**：2026-10-31｜**环境**：macOS Darwin 27.0.0 / Python 3.11.13 / Node v25.0.0 /
Vitest 4.1.12（jsdom）/ ego-browser 0.6.0.20（Chromium 154.0.8037.44）

> 本文件在**创建 PR 之前**提交，只记当时已得的读数。PR 号与 CI 状态以 GitHub 为准，不写进本文件、
> 也不为回填它们追加提交（判据见 `docs/agents/implementation.md` §交付）。
> 浏览器读数、截图与命令输出留存在仓库外 `/tmp/alibot-348/`（`red-test-final2.txt` /
> `green-test-final3.txt` / `gate-fast-final3.txt` / `server.log` / `shots/`）。

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① 红→绿：改动前新增用例失败，加规则后同一用例通过 | `app/frontend/tests/agent-detail.test.js` | 红（隔离基线 `e871d33`）：`Tests 1 failed \| 55 passed (56)`；绿：`Tests 56 passed (56)` | ✅ |
| ② 窄档全绿 | `cd app/frontend && env -u NODE_ENV npx vitest run tests/agent-detail.test.js` | `Test Files 1 passed (1)` / `Tests 56 passed (56)` | ✅ |
| ③ 门禁全绿 | `scripts/check.sh --fast` | `Test Files 25 passed (25)` / `Tests 502 passed (502)`；ruff / format / mypy / pyright 全绿；`门禁全绿` | ✅ |
| ④ 真实浏览器：`minHeight` 240px、渲染高度 ≥240、截图留证 | `#agentMcpFileText`，端口 8799 + 数据目录副本 | `minHeight` **240px**、`offsetHeight` 240、`clientHeight` 238（border-box 扣上下 1px 边框）、`resize: vertical` | ✅ |
| ⑤ 双轴 `code-review`（Standards + Spec，起点 `e871d33`）已跑，findings 逐条处置 | 见「双轴 review」 | 两轴 findings 与处置见下；修复后复审无新硬违规 | ✅ |
| ⑥ 验收记录在创建 PR 前提交 | 本文件 | 提交在 `issue-348-impl` | ✅ |
| ⑦ 分支已推送、PR 含 `Closes #348` | `issue-348-impl` | 交付步骤；PR 号与 CI 读数写仓库外 `/tmp/alibot-348/run-report.md` | 交付步骤 |
| ⑧ `worker_done` 已发（显式 succeeded、三句摘要、真实元数据） | 本终端 | 交付步骤 | 交付步骤 |

## 红证据（隔离基线 `e871d33`）

用 `git worktree add --detach` 在 `/tmp/alibot-348/red-baseline` 检出 base commit，只把**最终版**
用例文件覆盖进去（基线 `app.css` 不含本票规则），`node_modules` 软链到本 worktree：

```text
cd /tmp/alibot-348/red-baseline/app/frontend
env -u NODE_ENV <worktree>/app/frontend/node_modules/.bin/vitest run tests/agent-detail.test.js

 RUN  v4.1.12 /private/tmp/alibot-348/red-baseline/app/frontend

 ❯ tests/agent-detail.test.js (56 tests | 1 failed) 1997ms
     × 家目录 mcp.json 文本域默认高度 240px（作用域规则，全局 `.textarea` 不动） 37ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  tests/agent-detail.test.js > mcp 页签 > 家目录 mcp.json 文本域默认高度 240px（作用域规则，全局 `.textarea` 不动）
AssertionError: 缺少 mcp.json 文本域的作用域规则: expected -1 to be greater than or equal to 0
 ❯ tests/agent-detail.test.js:1172:45
    1170|     // 按顺序覆盖（退回 70px）。断言完整选择器，防止有人改回单类而用例仍绿。
    1171|     const ruleAt = css.indexOf('.textarea.agent-mcp-file-text {');
    1172|     expect(ruleAt, '缺少 mcp.json 文本域的作用域规则').toBeGreaterThanOrEqual(0);
       |                                             ^
    1173|     const block = css.slice(ruleAt, css.indexOf('}', ruleAt));
    1174|     expect(block).toContain('min-height: 240px');

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯

 Test Files  1 failed (1)
      Tests  1 failed | 55 passed (56)
```

原文：`/tmp/alibot-348/red-test-final2.txt`。基线 worktree 取证后已删除（`git worktree remove --force`）。

## 绿证据

```text
cd app/frontend
env -u NODE_ENV npx vitest run tests/agent-detail.test.js

 Test Files  1 passed (1)
      Tests  56 passed (56)
```

原文：`/tmp/alibot-348/green-test-final3.txt`。

## 真实浏览器读数

工作台：`http://127.0.0.1:8799/studio/v2/`，数据目录为 `~/.alibot/data` 的副本
（`/tmp/alibot-348/data`），未写用户正在使用的 8788 数据；视口 1982×1011。
服务用本 worktree 的包启动（worktree 无 `app/.venv`，用仓库根 `.venv` + `PYTHONPATH` 指向本 worktree
的 `app/`，确保静态资源与源码都来自本分支）：

```sh
env -u http_proxy -u https_proxy -u all_proxy -u no_proxy -u NO_PROXY \
  PYTHONPATH=$PWD/app .venv/bin/python -m alibot --port 8799 --data-dir /tmp/alibot-348/data
```

路径：Agent 详情 → MCP 页签（`[data-od-id="agent-detail-tab-mcp"]`）→ `textarea#agentMcpFileText`
（Agent `mem_becddea0a83f`）。

| 时点 | `getComputedStyle().minHeight` | `offsetHeight` | `clientHeight` | `resize` |
| --- | --- | --- | --- | --- |
| 中间实现（单类选择器 `.agent-mcp-file-text`） | **70px**（被全局 `.textarea` 覆盖） | 70 | 68 | vertical |
| 最终实现（`.textarea.agent-mcp-file-text`） | **240px** | **240** | 238 | vertical |

- 最终态 `getBoundingClientRect()` 为 880×240，`box-sizing: border-box`；`clientHeight` 238 是
  扣掉上下各 1px 边框后的内容盒高度，渲染盒本身正好 240px。
- 截图：`/tmp/alibot-348/shots/green-mcp-textarea-final.png`（MCP 页签，文本域可见约 10 行）。
- 原始服务日志：`/tmp/alibot-348/server.log`。

## 改动落点

| 文件 | 改动 |
| --- | --- |
| `app/alibot/studio/app.css` | Agent 详情样式区（`.global-mcp-row` 之后）新增 `.textarea.agent-mcp-file-text { min-height: 240px; }` + 中文注释；全局 `.textarea` 不动 |
| `app/frontend/tests/agent-detail.test.js` | mcp 页签新增 CSS 文本契约用例：元素 `tagName` 为 `TEXTAREA`、带 `textarea` + `agent-mcp-file-text` 类；正本选择器为 `.textarea.agent-mcp-file-text`、块内 `min-height: 240px`；反向断言全局 `.textarea` 仍为 `resize: vertical` / `min-height: 70px` |
| `app/specs/issue-348-acceptance.md` | 本文件 |
| `app/specs/issue-348-pr-body.md` | PR 正文 |

## 命令与读数

```sh
cd app/frontend
env -u NODE_ENV npx vitest run tests/agent-detail.test.js
# Test Files  1 passed (1)
# Tests  56 passed (56)

cd ../..
scripts/check.sh --fast
# build: ../alibot/studio/v2/index.html + assets（产物不入库，ADR-0039）
# pytest（--fast 且未指定文件：跳过）
# ruff check: All checks passed!
# ruff format --check: 392 files already formatted
# mypy: Success: no issues found in 138 source files
# pyright: 0 errors, 0 warnings, 0 informations
# Vitest: Test Files 25 passed (25) / Tests 502 passed (502)
# 门禁全绿
```

本票测试档为窄档，不跑全量 `scripts/check.sh`（无 Python / 后端 / schema 改动）；CI 跑全量。

## 双轴 review

两轴各跑一个只读 reviewer 子代理（Standards / Spec 分开，互不抵消），起点 `e871d33`。首轮针对
未修复的中间实现；按 findings 修复用例后，对最终工作树复跑两轴确认收敛。

### Standards 轴

首轮无硬违规；一条交付缺口（验收记录尚未落盘，属交付流程中待办）与两条判断题：

| # | Finding | 类型 | 处置 |
| --- | --- | --- | --- |
| S1 | 缺 `app/specs/issue-348-acceptance.md`（PR 前落验收记录） | 硬（流程） | ✅ 本文件即该产物，随 docs 提交在 PR 前落盘 |
| S2 | 测试名写「全局 `.textarea` 不动」却无反向断言；`indexOf('.agent-mcp-file-text {')` 是子串匹配，改回单类选择器仍绿而浏览器会退回 70px | 判断 | ✅ 已修：断言元素同时带 `textarea` 与 `agent-mcp-file-text`、完整选择器 `.textarea.agent-mcp-file-text`，并补全局 `.textarea` 仍为 70px 的反向断言 |
| S3 | CSS 注释 6 行解释 1 行规则，偏密 | 判断 | ⏭ 部分采纳：删去不可复跑的瞬时读数，保留级联理由（选择器为何带 `.textarea`）；该理由正是复合选择器的存在依据，压到无法解释机制反而有害 |
| S4 | CSS 文本契约样板（`indexOf` → `slice` → `toContain`）在本文件第 3 次出现（Duplicated Code） | 判断 | ⏭ 不修：抽 `cssRuleBlock(css, selector)` 要改 `tests/helpers/studio-globals.js` 与至少两个既有测试文件，超出本票 Target 与窄档；与 #346 S1 同一处置，另票处理 |
| S5 | `240px` 是裸尺寸常量、未走 token（Primitive Obsession） | 判断 | ⏭ 不修：本文件同族尺寸（`70px` / `160px` / `260px`）均为裸值，且 token 表没有尺寸档；引入新 token 属票面明确禁止 |

复审：S2 的断言缺口已闭合；S3–S5 为可接受/另票处理的判断题。复审子代理在验收文件落盘前启动，
因此仍按当时文件系统报告 S1 未解决；本文件随后落盘并随 docs 提交在 PR 前提交，S1 闭合。

### Spec 轴

首轮确认规格要求全部落地、无 scope creep；复合选择器不构成偏离（仍是作用域规则、不改全局、
实际 240px，且与 `.agent-skill-section > .resource-add` 先例同法）。三条非阻断项：

| # | Finding | 处置 |
| --- | --- | --- |
| P1 | 测试契约与实际选择器脱节（只命中后缀；模板丢 `textarea` 类仍绿） | ✅ 已修：同 S2，断言两个类与完整选择器 |
| P2 | 测试名声称全局 `.textarea` 不动却无对应断言 | ✅ 已修：补反向断言 |
| P3 | 注释中「浏览器实测仍是 70px」不可复核 | ✅ 已修：从 CSS 注释删除；该读数连同命令与截图改记入本文件「真实浏览器读数」 |
| P4 | 用例只断言类名，未断言 `tagName` 为 `TEXTAREA`（模板换成同类 `div` 仍绿） | ✅ 已采纳：补 `tagName === 'TEXTAREA'` |
| P5 | 反向断言只覆盖全局 `min-height: 70px`，未覆盖规格同句的 `resize: vertical` | ✅ 已采纳：全局块同时断言 `resize: vertical` |

复审：无规格缺失、无 scope creep、无实现缺陷；P1–P5 均已闭合，结论「可接受，无规格缺口」。

## 说明

- **为什么选择器是 `.textarea.agent-mcp-file-text` 而不是单类**：全局 `.textarea`
  （`app.css:840`）定义在本文件靠后的表单段，单类选择器与它同特异性且更早，会被按顺序覆盖。
  首版按字面用单类规则时，真实浏览器读数仍是 70px；改为复合选择器（0,2,0）后稳定取胜。这与
  `.agent-skill-section > .resource-add` 覆盖全局 `.resource-add` 是同一做法。
- **全局 `.textarea` 未动**：diff 只新增一条规则与注释；表单里的其它文本域仍继承
  `min-height: 70px`，用例里有反向断言守护。
- **测试比派发 brief 的字面要求多了几条断言**：brief 要求断言类名与规则块；review 指出子串匹配
  守不住复合选择器、缺全局反向断言、`tagName` 与 `resize: vertical` 无守护，故补为完整选择器 +
  `tagName` + 两个类 + 全局 `resize: vertical` / `min-height: 70px`。核心判据未变。
- **浏览器验收的隔离**：使用 `~/.alibot/data` 的副本与独立端口 8799，未写用户正在使用的 8788
  数据；全程只读页面，未点保存 / 删除。
- **构建产物未入库**（ADR-0039）：`scripts/check.sh --fast` 在门禁头部构建
  `app/alibot/studio/v2/`，工作区保持只有源码与文档改动。
- **环境准备**：新 worktree 把 `.venv` 指向仓库根；`npm ci --include=dev` 装前端 devDependencies
  （宿主 `NODE_ENV=production` 会跳过它们）。
