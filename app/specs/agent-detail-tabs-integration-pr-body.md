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

**Door:** two-way

五张票都是呈现层重排 + 两条既有资源族内的子资源端点；后端装配、优先级、消息面、预算与唤醒纪律一字未动，`SCHEMA_VERSION` 与迁移链未碰（两条新端点都不需要新列）。回退只需 revert 这一个 PR：没有数据形状变化，没有一次性动作，唯一「人往里存东西」的新面（`mcp.json` 文本域）写的是人本来就有的文件，撤掉写端点即回到上一轮状态。

**Blast Radius:** wide

触碰 `app/alibot/studio/**` 呈现层、`app/alibot/{api,service,mcp,skills,workspace,i18n}.py` 资源面与两个测试族，影响面不可圈定，因此按 `scripts/check.sh` 全量跑（含 344 条 Studio DOM 用例 / 1232 条 pytest / ruff / mypy / pyright）。已知的可观察变化：详情页多了三个页签与两处新面；上下文三项不再能在界面上改（库内值保留）；技能与 MCP 行的「复制到其他成员」入口消失（端点仍在）；安装面只剩「从本机导入」下拉框（zip 与 directory 两个后端分支仍在）。`workspace.py` 的只读守卫被拆成内核 + 两道公开门（只读 / 写目标），只读路径行为与签名不变，`test_global_rules.py` 的守卫用例原样绿。

Closes #280
Closes #281
Closes #282
Closes #283
Closes #284
