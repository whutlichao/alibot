# Agent 删除确认弹窗与本页两处提示清理：验收记录

## 来源（用户 2026-10-24 反馈，设计反馈工具，视口 1350x978）

1. **删除确认改成弹窗**：危险区「删除」第一次点击原本在危险区内联展开 `.danger-confirm` 区块；
   要求改成真正的模态弹窗，沿用既有 `.overlay` + `.modal` 骨架（与 `#taskOverlay` 同形），
   内联确认区与 `.danger-confirm` 盒子样式一并删除。
2. **删除危险区「暂停只写配置…」那行 `.hint`**：危险说明已经点明暂停的代价。
3. **删除「全局 MCP（只读）」里「来自该成员家目录的 mcp.json…」那行 `.hint`**。
4. **空态文案改写**：zh 逐字为 `“全局mcp.json”里没有声明MCP服务`（全角引号、`mcp.json`
   前后无空格）；en 为 `No MCP servers are declared in the global mcp.json.`。

基线：`ad5ddf9`（PR #255 合并后的 main head）。本票只动 Studio 表现层、一个 DOM 用例文件与文档。

## 判据 → 落点

| 判据 | 落点 |
| --- | --- |
| 点「删除」零请求，弹窗可见，正文带 Agent 名称与「不可撤销」，详情仍开着 | `app.js` `openAgentDeleteModal()`；`index.html#agentDeleteOverlay` |
| 「取消」/ 关闭图标 / 点遮罩 / `Escape` 关闭且零请求 | `closeAgentDeleteModal()` + `bindEvents()` 四个出口；`Escape` 在其他弹窗之前判定 |
| 打开时焦点落到「取消」 | `openAgentDeleteModal()` 里 `#agentDeleteCancel.focus()` |
| 「确认删除」只发一次 `DELETE /api/agents/{id}`；成功收起弹窗、关闭详情、刷新镜像、toast | `deleteAgent()` 成功路径 |
| 请求在途禁用确认 / 取消 / 关闭；删除按钮同禁用 | `paintAgentDeleteControls()` + `paintAgentDangerControls()` |
| 失败时弹窗保持打开、按钮恢复、原因走 toast | `deleteAgent()` catch 分支 |
| 离线时暂停与删除禁用 | `agentDangerSection()` / `paintAgentDangerControls()` |
| 弹窗受中英词条驱动，标题新词条 `agentDetail.deleteTitle` | `i18n.js` 两份词条；`index.html` 全程 `data-i18n` / `data-i18n-attr` |
| 危险区不再有 `.danger-confirm` 节点与 `agentDetail.pauseNote` 文案 | `agentDangerSection()` 删节点；`i18n.js` 删词条；`app.css` 删盒子样式 |
| 全局 MCP 小节不再有 `agentDetail.globalMcpNote`；空态新文案 | `globalMcpSection()`；`i18n.js` |
| `state.agentDelete` 收敛为 `{memberId, busy}` | `emptyAgentDeleteState()`；`confirming` 随内联确认区删除 |

## 改动清单

| 层 | 文件 | 改动 |
| --- | --- | --- |
| 前端 | `app/alibot/studio/index.html` | 新增 `#agentDeleteOverlay`（既有 `.overlay` / `.modal` / `modal-head` / `modal-body` / `modal-foot` / `btn-danger` 骨架），放在 `#agentOverlay` 之后 |
| 前端 | `app/alibot/studio/app.js` | `openAgentDeleteModal()` / `closeAgentDeleteModal()` / `paintAgentDeleteControls()`；`deleteAgent()` 改为弹窗口径；危险区删内联确认区与暂停提示；全局 MCP 删说明行；`Escape` 优先收弹窗 |
| 前端 | `app/alibot/studio/i18n.js` | 新增 `agentDetail.deleteTitle`（zh `删除 Agent` / en `Delete agent`）；删除 `agentDetail.pauseNote` / `agentDetail.globalMcpNote`；改写 `agentDetail.globalMcpEmpty` |
| 前端 | `app/alibot/studio/app.css` | 删除 `.danger-confirm` 盒子；`.danger-confirm-text` 保留给弹窗正文（去掉盒子内边距语义） |
| 测试 | `app/tests/agent_detail_ui.test.cjs` | 弹窗开 / 关 / 四个出口、确认成功 / 在途 / 失败、英文、静态骨架与 CSS 死规则、两处提示删除、空态逐字；harness 补 `id` 反射与 `focus()` 记录 |
| 文档 | `app/README.md` / `CONTEXT.md` | 危险操作段落改弹窗口径、去掉已删提示的说法、同步空态新文案；成员词条「删除（两步确认）」→「删除（确认弹窗）」 |

## 红证据（先改测试，后改实现；隔离基线副本）

命令：

```sh
scripts/red-evidence.sh ad5ddf9 app/tests/agent_detail_ui.test.cjs
```

读数（exit code = **1**，基线产品代码 + 当前工作区测试）：

```
基线：ad5ddf9 (ad5ddf9)
基线 app.js md5：bb1c9e24ff51bffdd749deced04a3792
ℹ tests 42
ℹ pass 31
ℹ fail 11
```

失败用例（11 条）：

1. 所有区块在同一页纵向铺开：统计 / 系统提示 / 配置 / 全局 MCP / 危险操作
2. 删除确认弹窗是 index.html 里的既有模态骨架，危险区不再有内联确认节点
3. 危险区不再消费 .danger-confirm 盒子样式，确认文案样式仍被弹窗复用
4. 点「删除」只打开确认弹窗：零请求、文案带名称与不可撤销；取消 / Escape / 点遮罩都收起且零请求
5. 确认删除只发一次 DELETE：成功后弹窗收起、详情关闭、列表刷新、toast 可读
6. 删除请求在途时按钮禁用：请求没回来之前不能重复提交
7. 删除失败留在当前页、弹窗不收起、原因不静默吞掉
8. 危险区与删除弹窗都有英文取值：切到 English 后同样可见
9. 危险说明与确认文案说「移出列表与全部频道」，不再声称「从工作台隐藏」
10. 全局 MCP 小节不再有「来自家目录 mcp.json」那句提示；空态用新文案
11. 本票新增 / 沿用的文案在两份词条里都取到值

脚本不改当前工作区；证据目录为 `mktemp` 临时目录（本次 `/var/folders/.../T/alibot-red.f6OhCG`）。

## 绿读数（实现后）

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/agent_detail_ui.test.cjs` | `tests 42 / pass 42 / fail 0`，exit 0 |
| `node --test app/tests/i18n_ui.test.cjs` | `tests 53 / pass 53 / fail 0`，exit 0（两份键集同形、`index.html` 无裸中文） |
| `scripts/check.sh --studio` | exit 0；审批 HTTP 契约 `20 passed`、ruff `All checks passed`、mypy / pyright `0 errors`、Node 用例 `295 pass / 0 fail`，末行「门禁全绿」 |

注：本 worktree 没有 `.venv` 时 `check.sh` 会回退到系统 `python3`（无 pytest）；按脚本提示补了
`ln -sfn /Users/apple/orca/alibot/.venv .venv` 后运行（`.venv/` 在 `.gitignore` 里，不入库）。

## 真机视口（隔离后端 + ego-browser）

环境：`PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --port 8799 --data-dir /tmp/alibot-delete-modal-021ec6`
（隔离端口 + 临时数据目录，未触碰 8788 或用户数据目录；结束后已停服务并删除临时目录）。
用 `POST /api/agents` 创建一个测试 Agent，从 `/studio/` 点左栏行打开详情，再点危险区「删除」。

### 1350x978

```json
{
  "viewport": { "w": 1350, "h": 978 },
  "modal": { "left": 455, "top": 117, "right": 895, "bottom": 316, "width": 440, "height": 198 },
  "title": "删除 Agent",
  "text": "确认删除「弹窗测试员」？它会立即从 Agent 列表与全部频道中移除并关闭详情；私信、历史消息与任务保留，但这个 Agent 无法恢复，操作不可撤销。",
  "focused": "agentDeleteCancel",
  "cancelText": "取消",
  "confirmText": "确认删除",
  "confirmDisabled": false,
  "docScrollWidth": 1350,
  "bodyScrollWidth": 1350,
  "pauseNotePresent": false,
  "globalMcpNotePresent": false,
  "emptyText": ["“全局mcp.json”里没有声明MCP服务"]
}
```

### 420 宽度

```json
{
  "viewport": { "w": 420, "h": 900 },
  "modal": { "left": 16, "top": 108, "right": 404, "bottom": 306, "width": 388, "height": 198 },
  "cancelRect": { "left": 253, "top": 257, "right": 303, "bottom": 289 },
  "confirmRect": { "left": 311, "top": 257, "right": 385, "bottom": 289 },
  "docScrollWidth": 420,
  "bodyScrollWidth": 420
}
```

两个视口下弹窗均无水平越界（`left >= 0`、`right <= viewport.w`，文档 `scrollWidth` 等于视口宽），
按钮都在弹窗内。

### 交互与请求计数（真实浏览器）

在页面里包一层 `window.fetch` 统计 DELETE：

| 动作 | 弹窗 | 详情 | DELETE 次数 |
| --- | --- | --- | --- |
| 点「取消」 | 收起 | 仍打开 | 0 |
| 点遮罩（视口坐标 100,500） | 收起 | 仍打开 | 0 |
| `Escape` | 收起 | 仍打开 | 0 |
| 点「确认删除」 | 收起 | 关闭 | 1（`/api/agents/mem_6666c5d80dfb`） |

确认后：左栏不再有该 Agent、`#toastWrap` 文本为「已删除 Agent「弹窗测试员」。」。

截图：`/tmp/alibot-delete-modal-1350.png`、`/tmp/alibot-delete-modal-420.png`、
`/tmp/alibot-delete-modal-after-delete.png`（临时目录，未入库）。

## 已知偏离 / 说明

- 本票 worktree 内**没有**票据正本文件（`app/specs/**` 只有历史验收文档）；票据正文由 dispatch
  的 TASK 块给出。因此 Status / Answer 回填落在协调侧的票据记录里，PR 号与 body 路径在
  `worker_done` 摘要中回报，本文件不重复维护一张可能漂移的票据副本。
- 没有对应 GitHub issue，PR 正文不写 `Closes`。
- `app/specs/agent-danger-zone-delete-acceptance.md`（历史验收证据）**不改写**：它记录的是
  两步确认那一轮的现场；本轮以本文件为准。
- 弹窗正文沿用既有 `agentDetail.deleteConfirm` 原文（含名称与「不可撤销」），标题新增
  `agentDetail.deleteTitle`；未新增第二套弹窗组件或第二份确认文案。
- 删除失败时 `state.agentDelete` 回到 `{memberId, busy: false}`（弹窗仍开着），与「失败不关详情、
  原因走 toast」的既有语义一致。
