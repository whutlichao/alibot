# Agent 详情页三分页签（#280–#284 · 单集成 PR）

父票 #279 · 规格正本 `app/specs/agent-detail-tabs-spec.md` · 设计正本 `app/specs/agent-detail-tabs-design-notes.md`

## Summary

Agent 详情从「一页纵向铺开的区块列」变成三个页签，且每类内容只在自己那一页；两处新读写面落在既有资源族里（零新增服务模块），一个后端端点都没删。

```diff
 app/alibot/studio/app.js
-  agentDetailNodes()          # 统计 → 系统提示 → 配置 → 能力段 → 全局 MCP → 危险操作，一页到底
+  agentBaseTabNodes()         # 基础信息和配置：统计 → 系统提示 → 配置 → 危险操作
+  agentSkillsTabNodes()       # skills：导入下拉框 → 左栏列表 / 右栏选中技能详情（元数据 + SKILL.md 正文）
+  agentMcpTabNodes()          # mcp：上块库行表单（装配正本）/ 下块家目录 mcp.json（文本域 + 条目视图）
+  state.agentDetailTab       # 'base' | 'skills' | 'mcp'；打开详情与切成员都回 'base'，不落库不写 URL

 app/alibot/studio/i18n.js
-  agent.contextWindow / contextTrigger / maxIters / runtimeDefaultHint   # 界面撤除 ⇒ 词条一并删
-  agentCapabilities.copy*（12 个）                                        # 复制弹层撤除 ⇒ 词条一并删
-  skill.installDir* / installZip* / importRunButton / importStatusReady …  # 三条安装入口收成一条
+  agentDetail.tabBase / tabSkills / tabMcp / tabsLabel
+  skill.detail*（13 个）· agentDetail.mcpFile*（8 个）· mcp.sourceDb

 app/alibot/api.py
+  GET  /api/skills/{id}/markdown            # 只读正文（front matter 之后）；ok / missing / invalid / unreadable
+  GET  /api/agents/{id}/global-mcp-file     # 家目录 mcp.json 原文 + 条目计数
+  PUT  /api/agents/{id}/global-mcp-file     # 整份原文写盘：三道校验全过才写，任一条不合法 ⇒ 400 + fields + 零写入
-  # 端点面只增不减：/api/skills/{id}/copy 与 POST /api/skills 的 zip / directory 分支都保留，
-  # 各自 docstring 加「当前 Studio 无入口，保留为脚本 / API 用法」

 app/specs/issue-280..284-acceptance.md       # 逐票「判据 → 落点 → 命令与读数」
 app/specs/agent-detail-tabs-review-fix-acceptance.md
```

票图（原生 `blocked_by`）与推进顺序：

```text
#280 骨架 ──┬──> #282 导入下拉框 ──> #283 skills 右栏 + 正文端点 ──> #284 mcp 两块 + mcp.json 读写
#281 复制弹层撤除（独立，无阻塞）
```

三条最容易做错的边界，都在验收里显式咬住：

1. **保存载荷 10 → 7 字段（N3 第一格）**：`PATCH /api/agents/{id}` 三个可选字段与 `agent_config` 三列（默认 40 / 0.7 / 30）一字未动；前端不再提交那三个键 ⇒ 部分更新不会清空既有值。用例显式断言 `'<key>' in body === false` 且草稿里也没有——照抄旧载荷会一次清空那三列。
2. **共用弹层（N3 第二格）**：复制弹层是 MCP / 技能共用一份实现，两个 kind 的入口与 `capabilityCopyNode()` 一并撤除（`data-copy-*` 在正本里 0 命中），`POST /api/skills/{id}/copy` 端点保留、`test_skill_copy.py` 原样绿。
3. **原文逐字写盘（N3 第三格）**：`mcp.json` 写路径只写同一串字节（不 `json.dumps` 重排、不重缩进、不动末尾换行），先写同目录临时文件再 `os.replace`；用例用「非标准缩进 / 倒键序 / 有无末尾换行」两组样本断言磁盘字节 == 提交字节。

## Evidence

红 → 绿（逐票，隔离基线 `scripts/red-evidence.sh` 只换测试不换产品代码）：

| 票 | 红读数（基线） | 绿读数 |
| --- | --- | --- |
| #280 | `agent_detail_ui` 58 / 48 pass / **10 fail** | 58 / 58 |
| #281 | `agent_capabilities_ui` 13 / 11 pass / **2 fail** | 13 / 13 |
| #282 | `agent_capabilities_ui` 15 / 9 pass / **6 fail** | 15 / 15 |
| #283 | 前端 22 / 14 pass / **8 fail**；后端 `test_skill_markdown.py` **8 failed（404）** | 前端 22 / 22；后端 8 passed |
| #284 | 前端 **6 fail**；后端 `test_global_mcp_file.py` **14 failed** | 前端 +5 用例绿；后端 14 passed |
| review-fix | `agent_detail_ui` **4 fail**（F2 位移 / F3 替换粒度 / F6 脏判据） | 344 / 344 |

后端契约（真实临时 `HOME` + `TestClient` 黑盒，绝不读写真实 `~/.alibot`）：

- `GET /api/skills/{id}/markdown`：`ok`（正文 == front matter 之后那段字节，且与装配读进 `Skill.markdown` 的同一份逐字相等）/ `missing`（元数据照常 + `markdown=null`）/ `invalid`（根外 symlink 祖先与目标两种，**响应不含根外内容**）/ `unreadable`（非 UTF-8 与权限 000）/ `404` / `400`（弃用频道级行）；另断言只读（消息 / 轮次 / 预算 / 磁盘树与 mtime 前后不变）。
- `GET/PUT /api/agents/{id}/global-mcp-file`：读（存在 ⇒ 原文逐字 + 计数；不存在 ⇒ `exists=false` + 空串且**不建文件**）；写九格——非法 JSON（mtime + 内容**完全不变**）/ 顶层形状不对 ×2 / 单条声明非法（`fields` 指到 `servers[1].transport`）/ 合法原文逐字写盘（两组样本 + 覆盖）/ 家目录不存在（400 且**整棵 HOME 树目录清单不变**）/ 文件不存在但家目录在（新建）/ 根外 symlink（目标与祖先，根外文件未变）/ 成员不存在（GET 与 PUT 都 404）。

全量门禁 `scripts/check.sh`（integration tip `ce4cde6`）：

```text
== pytest（全量）       1232 passed, 11 skipped
== ruff check           All checks passed!
== ruff format --check  351 files already formatted
== mypy（仓库根）       Success: no issues found in 130 source files
== pyright              0 errors, 0 warnings, 0 informations
== node --test（glob）   tests 344 / pass 344 / fail 0
门禁全绿
```

双轴 code-review（integration 上跑一次，Standards + Spec 各一个 reviewer）：

- **Spec 轴**：FAIL → 两条必须改（`app/README.md` 未做 D9 末段的文档同步；「全局 MCP」只读小节挂在上块而不是 D1 要求的**下块**）→ 已在 review-fix 收口（README 10 处 + 条目视图落进下块）。
- **Standards 轴**：PASS-WITH-FINDINGS → 四个必须改（`replaceSectionNode` 的嵌套替换粒度会吃掉整块、`api.py` 模块 docstring 端点清单缺两行、两处说谎注释、`capabilityDraftDirty()` 未纳入 `mcpFileDraft` 导致后台重绘丢焦点）→ 全部收口并各配一条回归用例；四条「可以不改」逐条写理由（`agent-detail-tabs-review-fix-acceptance.md` §10）。
- 处置记录：`app/specs/agent-detail-tabs-review-fix-acceptance.md`。

## Merge Danger

**Door:** one-way（**移除功能**）

判据问的不是「回退难不难」，而是这个 PR 对**用户可观察能力**做了什么。除了「呈现层重排 + 两条既有资源族内的子资源端点」之外，本 PR 还从界面上撤掉了四类**用户原本能做的事**，因此是单向门（`git diff 0a359b8 origin/agent-detail-tabs-integration -- app/alibot/studio/app.js` 可逐条指认）：

| # | 被撤掉的用户能力 | 后端是否保留 |
| --- | --- | --- |
| 1 | **上下文三项的界面编辑**：上下文条数 / 压缩阈值 / 迭代上限三格控件与词条（`agent.contextWindow` / `agent.contextTrigger` / `agent.maxIters` / `agent.runtimeDefaultHint`）一并删除 | **保留**：`PATCH /api/agents/{id}` 的三个可选字段（`context_window_messages` / `context_trigger_ratio` / `react_max_iters`）、`agent_config` 三列与默认值 40 / 0.7 / 30 原样，脚本仍可写 ⇒ 「界面彻底没了，API 还在」 |
| 2 | **技能「从本机目录安装」（手填绝对路径）**：文本框、按钮及其专有词条（`skill.installTitle` / `installDir` / `installDirPlaceholder` / `installDirButton` / `installDirHint` / `installing` / `sourceRequired`）一并删除 | **保留**：`POST /api/skills` 的 directory 分支（`source_path`）；docstring 标注「当前 Studio 无入口，保留为脚本 / API 用法」 |
| 3 | **技能「上传 zip 安装」**：文件框与 `skill.installZip` / `installZipHint` 词条删除 | **保留**：`POST /api/skills` 的 zip 分支；docstring 同上标注 |
| 4 | **「复制到其他成员」**：技能行与 MCP 行的复制入口、整个 `capabilityCopyNode()` 弹层（两 kind 共用一份实现）与 12 个 `agentCapabilities.copy*` 词条（两份 catalog 共 24 行）删除 | **保留**：`POST /api/skills/{skill_id}/copy`（`api.py:1288`）与 `app/tests/test_skill_copy.py` 原样绿；MCP 侧的复制本来就只走 `POST /api/mcp-servers`（无专用端点） |

第 1 类尤其是能力上的净减少：三项从界面上**完全消失**，只是库内既有值不会被一次保存清空（载荷 7 字段，三键不提交——这是本次刻意守住的第一格）。第 2–4 类是「界面没了、API 还在」的可回退收缩：后端分支与端点都在，撤掉界面不是不可逆的删除。

**Gate：** 因含单向门触发项，**本 PR 保持开放，由人类手动合并**（不设自动合并路径）。人类读完 Evidence 与上表后手动执行：

```sh
# 合并（merge commit，保留五票的 --no-ff 结构）
gh pr merge 286 --merge

# 合并后同步本地 main（主工作副本 /Users/apple/orca/alibot）
cd /Users/apple/orca/alibot
unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1
git switch main && git pull --ff-only origin main
```

**Blast Radius:** wide

触碰 `app/alibot/studio/**` 呈现层、`app/alibot/{api,service,mcp,skills,workspace,i18n}.py` 资源面与两个测试族，影响面不可圈定，因此按 `scripts/check.sh` 全量跑（含 344 条 Studio DOM 用例 / 1232 条 pytest / ruff / mypy / pyright）。已知的可观察变化：详情页多了三个页签与两处新面；上下文三项不再能在界面上改（库内值保留）；技能与 MCP 行的「复制到其他成员」入口消失（端点仍在）；安装面只剩「从本机导入」下拉框（zip 与 directory 两个后端分支仍在）。

**既有模块结构的变化（`workspace.py`）**：只读守卫被拆成「判定内核 + 两道公开门」——私有 `_resolve_path()` 只判逃逸（根外 symlink 祖先 / 根外目标），`_resolve_read_path()` 在它上面加「存在吗」（行为与原因串 `escape` / `absent` 一字不变），新增的公开 `resolve_global_write_path()` 复用同一个内核、只不判存在性（写路径要区分「父目录不存在」与「目标文件不存在」）。**只读路径的行为与签名不变**，既有调用点（技能装配 / `copy_skill()` / `load_global_mcp_declarations` / `migrate.py`）零改动，`app/tests/test_global_rules.py` 的守卫用例**原样绿**。它不是单向门，但写面从此多了一个公开入口，一并记在这里。

Closes #280
Closes #281
Closes #282
Closes #283
Closes #284
