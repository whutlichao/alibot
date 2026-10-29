# 验收记录：MCP 声明以成员家目录 `mcp.json` 为唯一正本

**票型**：实施票｜**门禁**：G-impl｜**测试档**：宽（MCP 装配 / 管理面 / 迁移命令 / 前端 mcp 页签）
**Ticket**：用户反馈直派（Orca dispatch `task_e96d2022ca35`）｜**base**：`b80412b`｜**branch**：`task-e96d2022ca35`
**PR body**：`app/specs/agent-mcp-home-pr-body.md`
**采集时间**：2026-10-29｜**环境**：macOS（Darwin 27.0.0）/ Python 3.11.13 / Node v25.0.0

> 本文件在**创建 PR 之前**提交，只记当时已得的读数。PR 号与 CI 状态以 GitHub 为准，
> 不写进本文件、也不为回填它们追加提交（判据见 `docs/agents/implementation.md` §交付）。
> 需要留存的 PR / CI 读数写仓库外的运行报告（`/tmp/e96d-live/run-report.md`）。

---

## Status

用户明确要求改变 ADR-0031 的「MCP 声明以 `mcp_server` 库行为正本」设计：每个 Agent 家目录
`~/.alibot/agents/<member_id>/mcp.json` 是这份 Agent MCP 声明的唯一正本；管理面与运行期装配读写
同一份文件；库内 `mcp_server` 表只作历史与迁移来源。DM 与频道共用同一份声明，DM 不因此获得项目
文件权限；存量库行有安全、可验证的显式迁移路径；远端确认与敏感环境变量保护保持不变。

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ① 旧代码下 DM 无 MCP（红证据） | 基线 worktree（`b80412b`）跑 `app/specs/agent-mcp-home-red-dm-mcp.py` | `AssertionError: 基线 DM 工具面：['declare_silence', 'memory_search']` | ✅ |
| ② 新代码下 DM 读到该 Agent 的 `mcp.json`，且拿不到项目文件工具 | `agent_seam._assemble_mcp` + `workspace.open_runtime_mcp_host`（宿主在 `<数据目录>/mcp-hosts/<session_id>`） | `tests/test_dm_mcp.py` 2 passed：`mcp__stub__echo` 在工具面，`Write/Edit/Glob/Grep/Bash` 不在；stdio cwd 在数据目录内 | ✅ |
| ③ DM 与频道读取**同一份文件** | `mcp.load_member_mcp_declarations`（两条路径唯一的读实现） | `test_mcp_assembly.py::test_a_channel_round_and_a_dm_round_read_the_same_member_file` | ✅ |
| ④ 文件缺失 ⇒ 零条且不建目录；非法 JSON / 顶层形状不对 ⇒ 零条 + 可读问题；单条非法 ⇒ 跳过该条并留提示 | `mcp.load_member_mcp_declarations` 的 `issues`；`mcp.assembly.*` 词条只进 `RoundOutcome.reason` | `test_global_mcp.py` 5 个加载用例 + `test_a_broken_file_still_replies_and_reports_the_issue`（并断言提示**不进** provider system prompt）+ `test_a_bad_entry_does_not_block_good_entries_and_is_reported` | ✅ |
| ⑤ 文件是管理面与运行期的**同一份正本**：增 / 改 / 删经 `/api/mcp-servers` 直接写文件，重名 409，坏文件不被覆盖，env 值永不出现 | `service.mcp_servers / create / update / delete`；`mcp.normalize_mcp_declaration` | `tests/test_mcp_servers.py` 6 passed（含 `env_values_never_appear_in_responses_or_on_disk`、`channel_scope_and_legacy_ids_are_refused`） | ✅ |
| ⑥ 文件不存在时**有可用的创建路径**：家目录在 ⇒ 保存即新建；家目录不在 ⇒ 400 且不建目录 | `service._mcp_payload_for_write`；`PUT /api/agents/{id}/global-mcp-file` | `test_mcp_servers.py::test_declarations_round_trip_through_the_agent_home_file`；`test_global_mcp_file.py` 14 passed | ✅ |
| ⑦ 存量库行**显式、可验证**地导出：默认 dry-run 零写入；`--apply --yes` 先整目录备份 + 清单 + 逐文件备份；同名不同形整体拒绝；保留文件里的额外条目；**不删库行**；写后用同一读路径回读 | `migrate.plan_mcp_export / apply_mcp_export / _verify_mcp_export`；`__main__._run_mcp_export` | `tests/test_mcp_migration.py` 19 passed；活体读数见下「迁移读数」 | ✅ |
| ⑧ 频道级旧行**只报告、不导出、不装配**，绝不静默启用 | `plan_mcp_export` 的 `channel_rows`；`load_member_mcp_declarations` 不读库 | `test_channel_rows_are_reported_but_not_exported`；`test_mcp_assembly.py::test_a_channel_only_legacy_declaration_never_reaches_the_provider` | ✅ |
| ⑨ 远端确认沿用 `remote_consent_scope('mcp', 合成标识 global:<member>:<name>)`，未确认不建连；stdio 不需要确认；导出把旧行上**已有**的确认复制到合成标识（新建与「文件里早已同形」两种都算），不代做新确认 | `mcp.load_member_mcp_declarations` 的 `consent_of`；`service.confirm_mcp_server`；`McpExportPlan.consents_pending` | `test_mcp_assembly.py::test_unconfirmed_remote_is_not_connected_and_consent_unlocks_the_attempt`；`test_mcp_servers.py::test_remote_consent_is_required_and_idempotent`；`test_mcp_migration.py::test_an_existing_same_shape_remote_still_gets_its_consent_copied` + `test_apply_writes_entries_to_the_member_file_and_keeps_the_rows` | ✅ |
| ⑩ 资源关闭：轮末先关客户端再关宿主；每 `(member, session)` 实例隔离 | `mcp.MCPAssembly.close`；`mcp._drop_live_clients`；seam 的 `finally` | `test_mcp_assembly.py::test_close_drops_the_clients_before_closing_the_runtime_host`（顺序 `client → host`、实例层清空）+ `test_clients_are_isolated_per_agent_and_session` | ✅ |
| ⑪ 旧命令 `migrate-mcp` 退役：只打印指向 `export-mcp` 的提示，退出码 6，零读写（**带 `--apply --yes` 也一样**） | `__main__.MCP_MIGRATION_RETIRED_NOTICE`（先于旗标裁决收口） | `test_the_retired_import_command_points_at_the_export`（两种参数组合）；活体 `exit=6` 见下 | ✅ |
| ⑫ 文档与 ADR 同步 | ADR-0021 §三、ADR-0031（标题 + 文件头修订 + §一/§二/§三/§四/§六）、ADR-0038 阶段 C、`CONTEXT.md`、`app/README.md` | 见「改动落点」中的文档行 | ✅ |

## 红证据（隔离基线 `b80412b`）

红测试随票入库（`pytest` 不收集 `app/specs/`）：`app/specs/agent-mcp-home-red-dm-mcp.py`。

```sh
git worktree add /tmp/alibot-baseline-e96d b80412b
cp app/specs/agent-mcp-home-red-dm-mcp.py /tmp/alibot-baseline-e96d/app/tests/test_red_dm_mcp.py
cd /tmp/alibot-baseline-e96d/app
/Users/apple/orca/workspaces/alibot/agent-mcp-home/.venv/bin/python -m pytest tests/test_red_dm_mcp.py -q
```

```text
>           assert "mcp__stub__echo" in tools, f"基线 DM 工具面：{sorted(tools)}"
E           AssertionError: 基线 DM 工具面：['declare_silence', 'memory_search']
E           assert 'mcp__stub__echo' in {'declare_silence', 'memory_search'}
FAILED tests/test_red_dm_mcp.py::test_baseline_dm_round_has_no_mcp_tools
```

同一场景在新代码里（`app/tests/test_dm_mcp.py`，文件为唯一正本、无库行）：

```text
..                                                                       [100%]
```

## 迁移读数（`export-mcp`，临时 HOME + 临时数据目录 `/tmp/e96d-live`，**未触碰真实数据**）

播种两个成员：A 只有库行（`notes` stdio + `search` http，`search` 旧行 id 已确认）；B 的文件里
**早已有同形** `search`（典型：先跑过旧 `migrate-mcp`），旧行 id 上也已确认。另有一条频道级旧行。

```text
$ python -m alibot export-mcp --data-dir /tmp/e96d-live/data
库行声明： 3 条（新建 2 / 跳过 1 / 拒绝 0）
将复制既有远端确认： 2 条（旧行 id → 合成标识）
  global:mem_7c002d06e93a:search（旧库行 mcp_search）
  global:mem_e4977eb22988:search（旧库行 mcp_search_old）
频道级旧行： 1 条不导出（ADR-0031 #270 修订已弃用；行仍留在库里）
预演结束：未写入任何文件、未改动数据库、未创建备份或导出清单。        → exit=0

$ python -m alibot export-mcp --apply --yes --data-dir /tmp/e96d-live/data
  数据目录备份： /tmp/e96d-live/data-backup-mcp-export-20261029T094315Z
  导出清单： /tmp/e96d-live/data/mcp-export-20261029T094315Z.json
写入条目： 2 条
复制远端确认： 2 条（旧行 id → 合成标识）
  global:mem_7c002d06e93a:search
  global:mem_e4977eb22988:search
写后校验： 通过（装配层读路径逐条读回同形条目）                          → exit=0

# 库里的确认行（旧行 id 与新合成标识都在；不删库行）
['global:mem_7c002d06e93a:search', 'global:mem_e4977eb22988:search', 'mcp_search', 'mcp_search_old']

# 幂等：再跑一次什么都不做、不建备份
$ python -m alibot export-mcp --apply --yes --data-dir /tmp/e96d-live/data
MCP 导出：没有需要导出的条目，也没有需要复制的远端确认（…）；未写入任何文件。 → exit=0

# 冲突：把文件里 notes 的 command 改成 python3.11 后再 apply
拒绝执行：存在需人工裁定的条目（同名不同形、文件不可用或家目录不存在）…本次未写入任何文件。
sha_before c7974fe3670b835cff493ec80bdf412ae112e4a145e76516914b9b616c56a167
sha_after  c7974fe3670b835cff493ec80bdf412ae112e4a145e76516914b9b616c56a167   → exit=6

# 退役命令（带双开关也一样）
$ python -m alibot migrate-mcp --apply --yes --data-dir /tmp/e96d-live/data
migrate-mcp 已退役：…请改用 `python -m alibot export-mcp`…                    → exit=6
```

## 命令与读数

```sh
scripts/check.sh            # 全绿：pytest 1291 passed, 11 skipped（207.58s）；ruff / ruff format 全过；
                            # mypy Success: no issues found in 137 source files；pyright 0 errors；
                            # Vitest 24 files / 471 tests passed；构建工作室产物 ✓ built
```

## 改动落点

| 文件 | 改动 |
| --- | --- |
| `app/alibot/mcp.py` | 文件读路径唯一实现；`MEMBER_MCP_FILENAME` / 合成标识 `member_mcp_server_id` / `split_member_mcp_server_id` / 原子写 `write_mcp_document_atomic`；`MCPAssembly.close` 先客户端后宿主 |
| `app/alibot/agent_seam.py` | 声明改读成员文件；DM / 无项目根轮次开运行宿主（`_assemble_mcp`），失败路径关宿主；轮末统一关闭 |
| `app/alibot/workspace.py` | `open_runtime_mcp_host`（`<数据目录>/mcp-hosts/<session_id>`，无项目文件工具） |
| `app/alibot/service.py` | 管理面读写同一份文件：列表 / 增 / 改 / 删 / 确认；坏文件拒绝覆盖；原文读写端点；删除库层 MCP 方法 |
| `app/alibot/db.py` | 移除旧 MCP 声明读写方法（`mcp_server` 表只作历史与迁移来源） |
| `app/alibot/migrate.py` | `export-mcp` 计划 / 执行 / 备份 / 清单 / 冲突拒绝 / 写后校验；新建**与跳过**两种条目都复制既有远端确认 |
| `app/alibot/__main__.py` | `export-mcp` 子命令；`migrate-mcp` 退役提示先于旗标裁决（退出码 6） |
| `app/alibot/api.py` | MCP 端点委托 service；seam 注入文件确认读取与 `runtime_root` |
| `app/alibot/i18n.py` | `mcp.assembly.*`（文件读写 / 跳过 / 宿主不可用）、`error.mcp_file_unusable` 等词条；删除孤键 |
| `app/frontend/src/**` | mcp 页签两块读写同一份文件；结构化变更后统一刷新、脏草稿保护；文件级可读问题逐条展示 |
| `app/alibot/studio/i18n.js` | `mcp.fileChangedElsewhere` 等前端词条（zh / en 同键集） |
| `docs/adr/0021`、`docs/adr/0031`、`docs/adr/0038`、`CONTEXT.md`、`app/README.md` | 正本切换、运行宿主、导出迁移与确认复制口径 |
| `app/tests/test_dm_mcp.py`、`test_mcp_servers.py`、`test_mcp_assembly.py`、`test_mcp_migration.py`、`test_global_mcp*.py` | 红 / 绿证据与上述判据 |
| `app/specs/agent-mcp-home-red-dm-mcp.py` | 红测试留档（复现命令见上） |

## 说明

- **未跑**：真实用户数据迁移（票面明确不做；只提供命令、备份与测试证据）。
- **已知代价**：未跑 `export-mcp` 的既有安装，合并后运行期不再装配旧库行——这是正本切换的显式
  迁移代价，ADR / README / 退役提示都写明；导出**从不删除**库行与源文件，且改写前有整目录备份。
- **已知边界**：运行期宿主目录 `mcp-hosts/<session_id>/` 是可丢弃状态，随会话增长，本票不做定期
  清理（与既有 `sessions/` 同口径）；结构化编辑以稳定 JSON 重写整份文件，**被编辑的那一条**经
  归一化内核重写（该条的未知键会被丢弃），其他条目与未知顶层键原样保留；要逐字保留请用原文文本域。
- **迁移只处理 Agent 级旧行**；频道级旧行永久保留、不导出、不装配，需要人手工决定是否另建成员文件。
