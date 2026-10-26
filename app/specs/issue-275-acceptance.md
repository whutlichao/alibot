# #275 验收：`mcp.json` 导入命令 `migrate-mcp` 并移除其装配层

父票 #269；分支 `mcp.json-migrate-mcp-mcp`。本票是「MCP 单正本：库行」的收尾一步。

约束复核：

- **不改 schema**：`SCHEMA_VERSION` 未 bump，零 DDL、零回填。
- **不删 `remote_consent_scope('mcp', server_id)` 既有行**：本命令一行都不写（更不删）。
- **不自动执行迁移**：不在启动路径上、不是 HTTP 端点；只有显式跑 `python -m alibot migrate-mcp` 才动。
- **不只留导出而没有导入**：交付的是导入（导出按 #275 评论已取消——人可在 Studio 直接改原文）。
- **与 `migrate-skills`（#274）同一套安全口径**：默认 dry-run、`--apply --yes` 双开关、先整目录备份、
  落迁移清单、不删源文件、同名不同形整体拒绝（无 `--force`）。

## 判据 → 落点

| 判据（票面） | 落点 | 用例 |
| --- | --- | --- |
| 读 `~/.alibot/agents/<member>/mcp.json`，条目写进 `mcp_server`（`scope_kind='agent'`, `scope_id=<member>`） | `migrate.plan_mcp_migration`（遍历全局层 `agents/<partition>/mcp.json`，路径经 `workspace.resolve_global_read_path` 同一守卫）+ `migrate._insert_mcp_row` | `test_apply_imports_entries_as_agent_rows_and_keeps_the_file` |
| 幂等：同名同形 → 跳过；同名不同形 → 默认拒绝并列出（无 `--force`） | `_mcp_declaration_shape` + `_plan_member_entries`（`create` / `skip` / `refuse`）；`apply_mcp_migration` 在动手前整体收口 | `test_same_name_same_shape_is_only_skipped`、`test_same_name_different_shape_is_refused_without_writing` |
| 远端条目的 consent **不代确认**；只报告「导入后仍需 `POST /api/mcp-servers/{id}/consent`」 | `McpMigrationApplyResult.remote_rows` + `MCP_MIGRATION_CONSENT_NOTICE` + `render_mcp_migration_result` | `test_apply_imports_entries_as_agent_rows_and_keeps_the_file`（断言 `_consent_rows(db) == []` 且报告含逐条 consent 入口） |
| 备份 DB；不改 `mcp.json` 文件本身 | `_backup_data_dir(..., label="mcp")`（备份名 `<数据目录>-backup-mcp-<UTC>`）+ `_write_mcp_manifest`；文件只读 | `test_apply_imports_entries_as_agent_rows_and_keeps_the_file`（文件 sha256 前后一致 + 备份名 + 清单存在） |
| dry-run 零副作用 | `plan_mcp_migration` 全程只读；CLI 默认分支打印后直接 `return 0` | `test_dry_run_lists_the_import_and_writes_nothing`（库 sha256 / 数据目录树 / 全局层树 / 文件 sha256 四份读数前后一致） |
| 移除装配层：`mcp.json` 不再进运行时装配，`merge_mcp_layers` 输入只剩 agent 层 | `agent_seam._mcp_declarations` 去掉文件层与合并；`mcp.merge_mcp_layers` 整个删除；`mcp.layer.global` / `mcp.assembly.shadowed` 两词条同删（全仓无引用） | `test_the_file_is_not_assembled_and_an_unimported_entry_is_reported_readably` |
| 迁移后 `test_global_mcp.py` 不再断言 `layer=global` 装配，改为断言「文件存在但未导入 ⇒ 运行期不连接，并给出可读提示」 | `agent_seam._unimported_global_mcp_count` + `i18n.mcp.assembly.not_imported` | 同上 + `test_a_broken_global_file_never_affects_the_database_rows`、`test_a_file_entry_is_not_assembled_even_when_other_entries_are_malformed`、`test_an_unconfirmed_remote_is_not_connected_until_the_consent_row_exists` |
| 文档：README 写清「先导入、再升级/删文件」的迁移顺序；ADR-0031 去掉「过渡期」 | `app/README.md` 新增「MCP 声明迁移：先导入、再升级 / 删文件」；ADR-0031 新增 #275 修订段并改写 §一 / §二 / §四 两行；ADR-0021 §三、`CONTEXT.md` 四处同步 | 对**本票改动的 4 个文档**（ADR-0031 / ADR-0021 / `CONTEXT.md` / `app/README.md`）定向 `rg "过渡期"` 零命中（见下）。历史票面（`app/specs/agent-detail-tabs-*.md`、`issue-269-code-review.md`）仍留 11 处「过渡期」，那是它们当时的记录，不在本票改写范围 |
| 与 `migrate-skills` 同一套安全口径 | `__main__.build_parser` 的 `MIGRATION_SUBCOMMANDS` + `main` 的统一双开关守卫 + `SKILL_MIGRATION_EXIT_CODE = 6` | `test_destructive_switches_require_both_flags`（5 组参数） |

## 先红后绿

红（实现前，只有测试文件）：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_mcp_migration.py
...
alibot: error: argument command: invalid choice: 'migrate-mcp' (choose from 'migrate-skills')
12 failed, 5 passed in 18.03s
```

> 那 5 个「passed」是 CLI 双开关用例：基线 parser 对任何未知参数都以 2 退出，于是它们因**错误的
> 理由**通过（与 #274 同一情形）。12 个行为用例全部失败。

绿（实现后）：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_mcp_migration.py
.................                                                        [100%]
17 passed in 18.46s
```

## 真实跑一遍（临时 HOME + 临时数据目录，不是用户真实数据）

fixture 由 `tests.conftest.backend` 建库：一个频道（`workspace_root=<tmp>/project`）、一个成员，
家目录 `mcp.json` 里两条声明（一条 stdio、一条 http）。命令环境 `HOME=<tmp>/home`。

### ① dry-run（默认）与零副作用

```text
$ python -m alibot migrate-mcp --data-dir /tmp/wt275/data
MCP 导入计划（migrate-mcp，只读扫描）
数据目录： /private/tmp/wt275/data
全局层根： /private/tmp/wt275/home/.alibot
家目录声明： 2 条（新建 2 / 跳过 0 / 拒绝 0）
  新建 .../home/.alibot/agents/mem_.../mcp.json 的「notes」（stdio / 本机）→ 成员 mem_...
    原因：库里没有同名声明，将按成员作用域新建
  新建 .../home/.alibot/agents/mem_.../mcp.json 的「remote」（http / 远端）→ 成员 mem_...
    原因：库里没有同名声明，将按成员作用域新建
远端条目 1 条：远端条目的数据面确认**未代做**：导入后仍需由人逐条调用
`POST /api/mcp-servers/{id}/consent`（或在 Studio 里点「确认使用」）。
预演结束：未写入任何文件、未改动数据库、未创建备份或迁移清单。
EXIT=0
```

零副作用读数（测试断言的四份读数）：库文件 sha256 前后一致；数据目录树（备份 / 清单 / sidecar）不变；
全局层目录树不变；`mcp.json` sha256 不变；`mcp_server` 零行；`remote_consent_scope` 零行。

### ② apply（`--apply --yes`）

```text
$ python -m alibot migrate-mcp --apply --yes --data-dir /tmp/wt275/data
...（同上的计划）
MCP 导入完成
  数据目录备份： /tmp/wt275/data-backup-mcp-20261026T054500Z
  导入清单： /tmp/wt275/data/mcp-migration-20261026T054500Z.json
处理条目： 2 条（新建 2 / 跳过 0）
  新建 .../mcp.json 的「notes」（stdio / 本机）
    库行：新建 mcp_...（scope_kind=agent，scope_id=mem_...）
  新建 .../mcp.json 的「remote」（http / 远端）
    库行：新建 mcp_...（scope_kind=agent，scope_id=mem_...）
远端条目的数据面确认**未代做**：导入后仍需由人逐条调用
`POST /api/mcp-servers/{id}/consent`（或在 Studio 里点「确认使用」）。
  POST /api/mcp-servers/mcp_.../consent  （成员 mem_...）
EXIT=0
```

备份目录名带 `mcp` 标签（与 schema 迁移的 `<from>-to-<to>`、技能迁移的 `skills` 区分）：

```text
$ ls -d /tmp/wt275/data-backup-*
/tmp/wt275/data-backup-mcp-20261026T054500Z
```

### ③ 幂等（同一条命令再跑）

```text
家目录声明： 2 条（新建 0 / 跳过 2 / 拒绝 0）
  跳过 ... 的「notes」（stdio / 本机）→ 成员 mem_...
    原因：库里已有同名同形声明，不覆盖
  跳过 ... 的「remote」（http / 远端）→ 成员 mem_...
    原因：库里已有同名同形声明，不覆盖
MCP 导入：没有需要导入的条目（全部已同形在库里或没有家目录条目）；未写入任何文件。
处理条目： 0 条（新建 0 / 跳过 0）
EXIT=0
```

不重复插行、不再建备份、行 id 与 `updated_at` 不变。

### ④ 冲突（库里已有同名但形状不同）

```text
拒绝 ... 的「notes」（stdio / 本机）→ 成员 mem_...
  原因：库里已有同名但形状不同的声明，需人工裁定后再导入（本命令不覆盖、不提供 --force）
...
拒绝执行：存在需人工裁定的条目（同名不同形，或整份文件不可用）。请先手动裁定
（本命令不覆盖、不提供 --force）；本次未写入任何文件。
EXIT=6
```

零写入：库行原样、零确认行、无备份、无清单、`mcp.json` 未动。

### ⑤ 文件不可用 → 整份拒绝

`{ not json` / 顶层不是对象 / `servers` 不是数组 / 某条 transport 非法 / 文件内重名，五种都给
`EXIT=6`，报告写明「文件不可用，整份都不导入」并指到条目与字段位置：

```text
拒绝（文件不可用，整份都不导入）： 1 份
  .../mcp.json（成员 mem_...）：文件不可用，未导入（servers 的第 1 条的 transport 不合法；位置 servers[0].transport）
```

理由与 Studio 写路径同一口径（`normalize_mcp_declaration` 同一份内核）：静默丢掉人写的一条才是坏结果。

## 移除装配层的行为读数

`mcp.json` 里有条目、库里没有对应行时：**运行期不连接**（工具清单里没有它，`runtime_status` 也没有它），
只发一条只进 `RoundOutcome.reason` 的可读提示：

```text
mcp.json 里还有 2 条声明未导入库行，本轮未装配（运行 `python -m alibot migrate-mcp`）
```

这条提示**不进** provider 的 system prompt。落选/合并那一档已随装配层一起消失：
`mcp.merge_mcp_layers` 删除，`mcp.layer.global` 与 `mcp.assembly.shadowed` 两词条删除（全仓零引用）。

## 文档读数

```text
$ rg -n "过渡期" docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md \
    docs/adr/0021-workspace-is-an-explicit-project-boundary.md CONTEXT.md app/README.md
（无输出）
```

README 的迁移顺序（`app/README.md`「MCP 声明迁移：先导入、再升级 / 删文件」）三步可照做：

1. 停应用 → `python -m alibot migrate-mcp --data-dir ~/.alibot/data`（默认预演，零写入）；
2. 读到「拒绝」先人工裁定（无 `--force`），处理完再看一遍预演直到零拒绝；
3. `python -m alibot migrate-mcp --apply --yes --data-dir ~/.alibot/data` → 按报告逐条确认远端条目 →
   重启应用。**先导入、再删文件**：删早了且导入失败就真的丢了源（本命令从不删源文件）。

## 门禁读数

```text
$ scripts/check.sh --fast tests/test_mcp_migration.py tests/test_global_mcp.py \
    tests/test_mcp_assembly.py tests/test_global_mcp_consent.py tests/test_global_mcp_file.py
== pytest（指定文件）: ... 25 passed, 1 skipped in 2.71s
All checks passed!                             # ruff check
355 files already formatted                    # ruff format --check
Success: no issues found in 131 source files   # mypy（仓库根）
0 errors, 0 warnings, 0 informations           # pyright
ℹ tests 347 / pass 347 / fail 0                # node --test app/tests/*.test.cjs
门禁全绿
```

```text
$ cd app && ../.venv/bin/python -m pytest      # 全量回归
1249 passed, 11 skipped in 755.96s (0:12:35)
```

`git diff --check`：无输出。

## 遗留 / 已知代价

- **Studio 的「全局 MCP」条目视图与文件条目确认按钮仍在**：`GET /api/agents/{id}/global-mcp-servers`
  与 `POST .../global-mcp-servers/{name}/consent` 保持可用（票面约束不动 HTTP 面、不删既有确认行）。
  但装配层移除后，**给文件条目写的确认行不再影响装配**——要生效必须先 `migrate-mcp` 导入成库行。
  该端点在 §HTTP 契约里的描述**本票已改为如实口径**（`app/README.md` 的 `POST .../global-mcp-servers/{name}/consent`
  行）：它确认的是**文件条目**（写 `global:<member>:<name>`），而 #275 后这个确认**不改变装配**——
  code-review 曾指出该行原写「只对已导入的 Agent 级 `mcp_server` 声明生效」，与
  `service.confirm_global_mcp_server` 的实际读路径矛盾，已按代码现状改写。
  UI 侧的引导文案（`agentDetail.mcpFileSource`）已同步改成「导入源、不参与装配 + 给出导入命令」。
  是否把该视图改成「引导导入」的形态，属于呈现层的后续票。
- **远端条目的确认永远是人逐条动作**：导入报告只给 id 与入口，本命令不代确认（票面硬要求）。
- **同一成员家目录里多条声明指向同一形状冲突**：一律「拒绝 + 列清单」，不猜、不自动合并。
- 频道级旧行、项目层旧技能字节仍保留不删（与 #270 / #274 同一处置）。
