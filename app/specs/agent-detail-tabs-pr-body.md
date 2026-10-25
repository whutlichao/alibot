## Summary

设计票的三段交付面（设计笔记 → 规格 → 票图）一次落地，**零源码改动**：diff 只落在 5 个文档路径 + GitHub Issues
（`CONTEXT.md` / ADR-0031 / 设计笔记 / 规格 / 本 PR 正文）。

```text
app/specs/
├── agent-detail-tabs-design-notes.md   # 新增：D1–D11 决策账、seam 选址、断言改写清单、Out of Scope、Answer
└── agent-detail-tabs-spec.md           # 新增：行为契约（2 条 seam / 76 条 user stories / 14 条 Out of Scope）
docs/adr/
└── 0031-...-global-layer-beside-the-project-boundary.md
                                        # 修订 7 处：mcp.json 从「只读、无导出写路径」改成「Studio 写面 + 库行仍为正本」
CONTEXT.md                              # 新增「Agent 详情页签」词条；更新「MCP 声明」词条
```

页签结构（D1）：

```diff
 Agent 详情（主线视图 state.mode === 'agent'）
-├── 统计 → 系统提示 → 配置（含 MCP 表单 + 技能列表）→ 全局 MCP 只读 → 危险操作   (一页纵向铺开)
+├── 页签「基础信息和配置」  统计 → 系统提示 → 配置 → 危险操作
+│     上下文条数 / 压缩阈值 / 迭代上限 三格界面入口撤掉（库内值与 PATCH 字段面不动）
+├── 页签「skills」         「从本机导入」下拉框（一次一个） + 双栏：左栏列表 / 右栏详情(元数据 + SKILL.md 正文)
+│     手填绝对路径安装 与 上传 zip 安装 两条入口撤掉（后端两条分支保留）
+└── 页签「mcp」            上块：库行 mcp_server（装配正本） · 下块：直接编辑家目录 mcp.json（同名时库行胜出）
```

两条新读写面都落在**既有资源族**里（seam 数不增，零新增服务模块）：

```text
GET  /api/skills/{id}/markdown                ← 落在 /api/skills/{id} 同族；复用既有 404 文案 + 作用域校验 + 全局只读守卫
GET  /api/agents/{id}/global-mcp-file         ← 落在 /api/agents/{id}/global-mcp-servers 同族
PUT  /api/agents/{id}/global-mcp-file         ← 三道校验（JSON → 顶层形状 → 每条声明形状）全过才写盘；原文写盘不重排
```

ADR 处置：**判为修订而非新决策**，因此不新建 ADR-0037——「没有上下文会困惑」这条不成立（新口径与既有「库行是正本、
同名库行胜出」一致，只把「本轮不提供导出写路径」改成「写面由 Studio 提供」，而 #275 当年就是这条的收尾票）。

ticket map（原生 sub-issue + 原生 `blocked_by`，图无环，frontier = #280 / #281）：

```text
#279 规格票（父）
├── #280 页签骨架 + 内容归位 + 上下文三项撤除      blocked_by 0   ─┬─> #282 导入下拉框（一次一个）
├── #281 撤「复制到其他成员」弹层（/copy 端点保留）  blocked_by 0   ├─> #283 技能右栏详情 + 正文只读端点
│                                                                └─> #284 mcp 两块面 + mcp.json 读写端点
```

## Evidence

**advisory 之外的机械守卫全部无需跑**：本票不碰 `app/alibot/studio/**`、`app/README.md`、`app/tests/**`，
因此 `test_docs_vocabulary_guard.py`（只读 README 与两个词表常量）与 `i18n_ui.test.cjs`（只扫 Studio 三件套）
都不被触发；也没有任何守卫读 `CONTEXT.md` 或 `docs/adr/**` 的正文（实测 `grep -rln "CONTEXT.md" app/tests/`
命中的三个文件只在注释里引用词条名，不读文件）。

改动面（唯一允许的五个文档路径，`scripts/**` / `.github/**` / schema / 迁移链零触碰）：

```text
$ git diff adc079b..HEAD --name-only
CONTEXT.md
app/specs/agent-detail-tabs-design-notes.md
app/specs/agent-detail-tabs-pr-body.md
app/specs/agent-detail-tabs-spec.md
docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md

$ git diff adc079b..HEAD --name-only | grep -E '^(app/alibot/|app/tests/|scripts/|\.github/)'
(无输出 = 源码 / 测试 / 脚本 / CI 零改动)
```

行数随本 PR 的后续修正提交增长，因此**不在这里写死**；以 `git diff adc079b..HEAD --shortstat` 或
PR 页面的读数为准。

ADR-0031 的七处修订逐条可指（`旧 → 新` 对照写在设计笔记 D8 的表里）：文件头修订标注、§一 表格 `mcp.json` 单元格、
§二「技能与 MCP」段、§三「独立守卫」bullet、§三「Docker 后端」bullet、§四 表格 `§三` 行、§四 表格 `§六` 行。
§六「被否方案与代价」原样（它没有断言「只读」）。

占位扫描：`grep -nE "待定|TBD|TODO"` 对设计笔记 / 规格 / ADR-0031 / `CONTEXT.md` —— **正文 0 命中**（唯一命中是
记录这条扫描命令本身的那一行）。

**Before → After（tracker）**

- Before：`gh issue list --state open` = 5 张（#275 / #269 / #131 / #72 / #1）；#275 正文写「导出：应用能生成与导入
  格式一致的 `mcp.json`」。
- After：新增 #279（规格父票）+ #280–#284（5 张实施子票）；#275 有一条方向调整评论
  （[#issuecomment-6398704279](https://github.com/whutlichao/alibot/issues/275#issuecomment-6398704279)），
  说明导出这一项不再单独立项、剩余范围收窄为「导入命令仍作迁移工具」+「移除装配层」。

```text
$ gh api repos/whutlichao/alibot/issues/<n> --jq '.issue_dependencies_summary'
#280 blocked_by=0 blocking=3
#281 blocked_by=0 blocking=0
#282 blocked_by=1 blocking=0
#283 blocked_by=1 blocking=0
#284 blocked_by=1 blocking=0
```

子票正文按票面要求**不含手写依赖行**（blocking 只由原生边承载）；#280 / #281 保留模板里的
「None (can start immediately)」。

设计树 frontier 为空：D1–D9 全部人定；两处 seam 由 Q1 定（双 A）；`/copy` 端点由 Q2 定（保留，只撤前端）；
拆票粒度由 coordinator 授权自选（选 5 票，理由记在设计笔记 D11 与 Answer A2 第 3 行）。

## Merge Danger

**Door:** two-way

docs-only + tracker-only：`CONTEXT.md` / ADR-0031 / 两个新增 specs 文件 + GitHub Issues。回退 = `git revert`
这一条提交并把 #279–#284 关掉；没有任何代码、schema、迁移或数据面被改动，也没有不可逆的跟踪器操作
（#275 只加了评论，未关闭、未改标签与正文）。

**Blast Radius:** docs-only（下游影响是有界的意图改变，不是运行时改变）

三点需要 coordinator 知道的下游后果：

1. **ADR-0031 是决策正本**：这次修订把 `mcp.json` 从「只读、本轮不提供导出写路径」改成「Studio 写面 + 库行仍是
   装配正本」。实施 #284 之前，仓库里会存在一段「ADR 允许写、代码还没有写面」的窗口——这是设计票的正常形态，
   但若 #284 长期不落地，ADR 会读起来超前于代码。
2. **#275 的范围被收窄**：它的正文仍写「导出：应用能生成与导入格式一致的 `mcp.json`」，评论已说明这一项不再单独
   立项。它的正文与标签按票面要求**未改**，读 #275 的人需要看那条评论才知道范围变了。
3. **#280 是 #282/#283/#284 的唯一阻塞**：三张内容票都往同一段渲染函数里加内容，串行落地意味着 #280 之后
   frontier 一次放出三张，但它们的 diff 会在同一批文件上相邻（`app.js` / `agent_capabilities_ui.test.cjs`）。
   并行开工时容易冲突，实施编排适合按 #282 → #283 → #284 排队。
