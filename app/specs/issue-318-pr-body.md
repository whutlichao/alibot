Closes #318
Part of #299

## Summary

旧页面 `/studio/` 退役，工作台只剩 v2；构建产物从「入库」改成「构建时生成」。

```diff
 app/alibot/studio/
 ├── i18n.js              ← 正本，未动
 ├── markdown.js          ← 正本，未动
 ├── app.css              ← 正本，未动
 ├── product-tokens.css   ← 正本，未动
-├── app.js               ← 删（10,634 行）
-├── index.html           ← 删（880 行）
-└── v2/                  ← 退出跟踪，构建时生成
+└── v2/                  ← 构建时生成（.gitignore）

 app/tests/
-├── *.test.cjs           ← 删（24 文件 / 389 用例）
-└── approval_dom_harness.cjs ← 删（只被上面那族消费）
+├── test_studio_v2_serving.py   ← 新：入口 redirect + 旧路径 404
+└── test_studio_static_cache.py ← 判据换成 v2（测试保留）

 app/frontend/tests/
+└── i18n-catalog.test.js ← 迁移：词条键集/参数位守卫
+└── build-guard.test.js  ← 重设计：产物存在 + 源码指纹 + 四条接线
```

入口链路（三条 redirect **先于**静态挂载注册——`html=True` 的 mount 会接住 `/studio/`）：

```text
GET /            → 307 /studio/v2/
GET /studio      → 307 /studio/v2/
GET /studio/     → 307 /studio/v2/
GET /studio/app.js → 404
```

产物的三个生成落点（谁需要产物，谁在自己路径上建）：

```text
scripts/check.sh   → 头部 npm run build（门禁前）
CI check.yml       → npm ci 与 check.sh 之间
app/hatch_build.py → hatch custom hook，打 wheel 前（缺 node 报错退出）
```

## Evidence

**入口与静态挂载**（`pytest tests/test_studio_v2_serving.py tests/test_studio_static_cache.py`）

```
8 passed
```

**全量门禁** `PYTHONPATH=$PWD/app ./scripts/check.sh`

```
pytest                  1271 passed, 11 skipped  (12:02)
ruff check              All checks passed!
ruff format --check     365 files already formatted
mypy                    Success: no issues found in 134 source files
pyright                 0 errors, 0 warnings, 0 informations
Vitest                  22 files / 426 passed
node --test             无（旧页面用例族已退役）
```

**wheel 实测**（`python -m build --wheel` 后解包，新增的 hatch hook 生效）

```
alibot/studio/v2/assets/index-DtB21X7m.js        293,546 bytes
alibot/studio/v2/assets/index-DtB21X7m.js.map  1,414,127 bytes
alibot/studio/v2/build-info.json                   2,180 bytes
alibot/studio/v2/index.html                        2,147 bytes
旧页面文件（app.js / index.html）：[]          ← 空
四个正本 in wheel：app.css / i18n.js / markdown.js / product-tokens.css
```

装进干净 venv 后核对：`v2 exists: True`、`v2 index: True`、`legacy app.js: False`、`legacy index.html: False`。

**缺产物时启动即报错**（不静默起服务然后白页）

```
RuntimeError: 工作台产物缺失：…/v2-absent 不存在。产物不入库（ADR-0039），
先跑：cd app/frontend && env -u NODE_ENV npm run build（或直接用 scripts/check.sh）
```

**ego-browser 真浏览器实测**（`python -m alibot --port 8831 --data-dir /tmp/alibot-318`，打开 `http://127.0.0.1:8831/`）

| 判据 | 读数 |
| --- | --- |
| `/` 进入 v2 | `location.pathname === "/studio/v2/"`，`title === "alibot · 协作工作区"` |
| 页签一 · 消息 | `tabMessages[aria-selected=true]`，消息流与 composer 都可见 |
| 页签二 · 任务看板 | `tabTasks[aria-selected=true]`，`.board-wrap` 在、5 列 |
| 页签三 · 搜索 | `.search-btn` ⇒ `#viewSearch` 可见、`#searchInput` 在；ESC 退回消息流 |
| 四个正本真被加载 | `/studio/i18n.js`、`/studio/markdown.js`、`/studio/app.css`、`/studio/product-tokens.css` 全在 resource timing 里；`window.AlibotI18n` 与 `renderMarkdown` 都为 object/function |
| 无 404 | 静态资源 4xx 计数 `[]` |
| 启动文案 | `alibot 工作台： http://127.0.0.1:8831/studio/v2/` |

**旧用例族 389 用例的覆盖去处**（逐文件映射，接住者都是 Vitest）

| 删除的旧用例文件 | 覆盖点 | 接住它的 Vitest 文件 |
| --- | --- | --- |
| `agent_capabilities_ui` | Agent 详情 MCP/技能两段 + 名册摘要 | `agent-detail.test.js` |
| `agent_detail_ui` | 详情整页三页签、草稿/保存、暂停恢复、删除确认 | `agent-detail.test.js` |
| `approval_badge_ui` | 线程关着时的待审批外显 | `approvals.test.js` |
| `approval_ui` | 审批卡倒计时/幂等/掩码/按 target 分开 | `approvals.test.js` |
| `channel_budget_ui` | 回应预算四读数 + 3-5-10 写控件 | `channel-tools.test.js` |
| `channel_config_ui` | 频道设置全字段 + 协作模式 pill | `channel-tools.test.js` |
| `channel_resources_ui` | 工作区与数据面段；MCP/技能不再在频道配置 | `channel-tools.test.js` |
| `dock_dm_ui` | 私信下 dock 可见性 | `app-shell.test.js` |
| `global_event_stream_ui` | 整页一条全局流、帧按 target 归属 | `event-stream.test.js` / `app-context.test.js` |
| `i18n_ui` | 词条键集/参数位（**迁移**）+ 裸中文 + 设置三档 | `i18n-catalog.test.js` / `i18n-bridge.test.js` / `rail-modals.test.js` |
| `initial_scroll_ui` | 贴底与静默刷新不改写 scrollTop | `workspace-store.test.js` |
| `markdown_render_ui` | 渲染器六组语法 + 危险输入降级 | `components.test.js` |
| `mobile_shell` | ≤900px 抽屉壳样式契约 + 三态同步 | `components.test.js` / `app-shell.test.js` |
| `narration_ui` | 旁白降档一行 + 展开读 reason | `components.test.js` |
| `rail_identity` | 左栏回复路径不整表换节点 | `components.test.js`（Vue 响应式在构造上消除该症状） |
| `round_outcome_inspect_ui` | 失败块「看成员状态」带成员 id | `agent-detail.test.js` |
| `run_panel_ui` | 运行面板四段 + 重连 | `run-panel.test.js` |
| `search_ui` | 搜索主线视图 + ⌘K + facet 是后端参数 | `search-commands.test.js` |
| `silent_ui` | 结构化沉默进摘要行、与 ignored 区分 | `components.test.js` |
| `sse_events_ui` | live-only 帧不混进消息流、不推进游标 | `event-stream.test.js` / `app-context.test.js` |
| `task_count_ui` | 任务/消息页签计数（含未读口径） | `task-board.test.js` / `components.test.js` |
| `task_pointer_ui` | 指针 chip 未读数 + 点开线程 + 审批角标 | `task-chips.test.js` |
| `task_thread_ui` | 任务线程呈现、关联消息并入讨论 | `task-thread.test.js` |
| `taskboard_transitions_ui` | 流转只出载荷 allowed_transitions + 键盘面 | `task-board.test.js` |

**覆盖缺口**（没有接住者，且理由是可给出的）

1. **「`app.js` / `index.html` 里用到的每个键都存在于两份词条」**：扫描目标已删除。替代品是渲染用例
   （取不到值会露出键名）与 `i18n-bridge.test.js` 的 `hasKey()`。残留风险：旧页面特有、v2 没有对应
   界面的键（如 `app.notMigrated`）不再有「有人在用」的判据，但仍受键集对齐与空值守卫约束。
2. **`index.html` 的裸中文扫描**：判据是文件级的，目标文件消失即作废。

## Merge Danger

**Door:** **单向门**

回滚不是 `git revert` 一下就干净：要把 10,634 行 `app.js`、880 行 `index.html`、24 个用例文件与
389 条用例一并恢复，并撤回已推到远端的产物退出跟踪（`git rm --cached` 过的大体积产物要重新入库）。
ADR-0038 的两条决策（§一 产物入库、§四 旧用例族保留）已被推翻并由 ADR-0039 记录，撤销需要再写一份
ADR 说明为什么走回头路。**由人类手动合并。**

**Blast Radius:** 宽

- **构建链**：`scripts/check.sh` 头部新增 `npm run build`；CI 新增构建步。
- **打包**：`app/pyproject.toml` 声明 hatch custom hook，新增 `app/hatch_build.py`；wheel 内容变了
  （多了 v2 产物、少了旧页面），`python -m build` / `pip wheel` 现在依赖 Node。
- **启动**：`api.py` 的挂载与 redirect、`__main__.py` 的启动文案；缺产物从「白页」变成「启动报错」。
- **测试分层**：前端从两族合为一族；`app/tests/*.test.cjs` 不再存在，`red-evidence.sh` 对新的
  Vitest 族不适用（它只认 `*.test.cjs`）。
- **文档**：新增 ADR-0039；ADR-0038 §一/§四/§五 最小事实修正；`app/README.md`、
  `docs/agents/implementation.md`、`docs/agents/review-rules.md`、`app/specs/vue-refactor-phase1-acceptance.md` 同步。
- **产品形态**：`python -m alibot` 现在要求先有产物——由 `check.sh` / CI / hatch hook 三条路径保证，
  但**手工** `pip install -e app && python -m alibot` 会撞到启动报错（报错文本自带修复命令）。
- **不再存在的入口**：`/studio/app.js`、`/studio/index.html`。任何外部书签/脚本引用它们会 404；
  `/studio` 与 `/studio/` 已做 redirect，不会 404。
