<!-- 本文件是 #184 的验收文档（判据 → 落点 → 命令与读数）；tracker 上的 issue #184 保持原样。 -->

# Issue #184 验收 —— schema 18：`web` 档 + 两个 `web_access` 配置位

> 票面：GitHub [#184](https://github.com/whutlichao/alibot/issues/184)（Part of #182，`ready-for-agent`）。
> 依据：**ADR-0026** §二 / §四（`docs/adr/0026-agent-web-access-is-explicit-opt-in.md`）、ADR-0015（迁移显式、
> 只前进、链连续、不回填旧数据）、ADR-0011（配置写入不部分写）。
> 票型：实施票。本票**只加库形态与读写面**：不实现工具本体（#185）、不装配（#186）、不动 Docker 网络（#187）。

| 项 | 值 |
| --- | --- |
| 分支 | `issue-184-impl`（从 `issue-182-integration` 的 `98920e8` 切出） |
| 改动文件 | `app/alibot/db.py`、`app/alibot/migrate.py`、`app/alibot/api.py`、`app/alibot/service.py`、`app/alibot/i18n.py`、`app/tests/_migration_history.py`、`app/tests/test_migration_v15_to_v17.py`、`app/README.md`；新增 `app/tests/test_web_access_schema.py`、`app/tests/test_web_access_config.py`、本文件 |
| 新增用例 | 11 条（库级 5 + 读写面 6），全部落在既有接缝（库级 CHECK / 真实迁移子进程 / HTTP 黑盒） |
| 门禁 | `scripts/check.sh` 全绿（读数见 §4） |

---

## 0. 判据 → 落点

| 票面 / 裁定判据 | 落点 | 用例 |
| --- | --- | --- |
| `scope_kind = 'web'` 可写、`'webx'` 被 CHECK 拒 | `db.py` `SCHEMA` 的 `remote_consent_scope` 表级 `CHECK (scope_kind IN ('model','mcp','docker','web'))` | `test_web_access_schema.py::test_scope_kind_web_is_accepted_and_unknown_kinds_are_rejected` |
| `web_access` 三合法值（`NULL` / `'search'` / `'search+fetch'`）可写、`'fetch-only'` 被拒 | `db.py` 的 `channel` / `agent_config` 两列列级 `CHECK` + `WEB_ACCESS_LEVELS` 白名单（库层第二道闸） | `…::test_channel_and_agent_web_access_take_only_the_two_documented_levels` |
| 迁移 `17 → 18` **一步**、链连续 | `migrate._step_17_to_18` + `STEPS` 注册（严格 +1） | `…::test_a_v17_library_reaches_the_current_version_in_one_step_and_keeps_every_row`；`test_migration_chain_guard.py`（真实子进程） |
| 重建 `remote_consent_scope` 保真（改表级 CHECK 只能重建） | `_rebuild_table(conn, "remote_consent_scope")`（逐列复制 + 改名）；表里已有 `web` 时跳过（幂等，重放是空操作） | 同上（三条既有确认行逐字还在） |
| 旧库推进后 `channel.web_access` / `agent_config.web_access` 全为 `NULL` | `_step_17_to_18` 只 `ADD COLUMN`、不写任何行（返回 0 行改动） | 同上（两列 `[(None,)]`） |
| 迁移后的列集与形态 = 本代码全量建库（D7 终态） | 对齐兜底 + `_add_missing_columns` | `…::test_the_migrated_web_access_columns_match_a_fresh_library` |
| 本版 DDL 留档可复核 | `app/tests/_migration_history.py`：`channel_v18` / `agent_config_v18` / `remote_consent_scope_v18` + `VERSION_OBJECTS[18]` + `SOURCE_COMMITS[18] = 'pending'`（合并后回填） | `…::test_the_v18_archive_encodes_the_web_scope_and_the_two_web_access_columns` |
| 频道设置端点与 `PATCH /api/agents/{id}` 接受新字段、GET 读回 | `api.py` 的 `ChannelConfigUpdate` / `AgentUpdate` + 两个 PATCH；`service.py` 的 `write_channel_config` 白名单与载荷；`db.update_channel_config` / `db.update_agent_config` | `test_web_access_config.py`（6 条） |
| 非法值**整条拒绝、不部分写入** | 校验先于写入（`db.update_channel_config` 先在事务外收集 `fields` 错误再 UPDATE；`AgentUpdate` 的 body 校验） | `…::test_channel_web_access_rejects_an_unknown_level_without_partial_write`、`…::test_agent_web_access_rejects_null_and_unknown_levels_without_partial_write`（失败后**读回原值**比对） |
| 号段不钉死 | `test_migration_v15_to_v17.py` 的终点改为从 `SCHEMA_VERSION` 派生（原来写死 `17`） | `test_schema_version_pins_guard.py` 绿 |
| 未配置 `web_access` 时工具面逐字不变 | 本票**没有任何装配层改动**：`web_access` 只被写路径与读路径带出，`agent_seam` / `workspace` 一个字节都没动 | `test_local_workspace.py` 的默认工具集断言保持绿（§4 全量读数） |

## 1. 人定决策的落实

### D1（预算口径）：网络请求不独立计数、不进 `target_budget.used`

- schema 18 **只有**「`remote_consent_scope` 多一个 `web` 档 + 两列 `web_access`」这一件事：
  `git diff` 里没有任何新建表、没有任何 `used` / 计数列（`grep -n "budget" app/alibot/migrate.py` 的
  `_step_17_to_18` 段落无命中）。
- 与 ADR-0026 §六一致：费用只经「工具结果进上下文后的 token 口径」体现，本仓不记 provider 侧费用。

### D5（`web_access` 的显式 `null`）：channel 允许清空、agent 沿用 400

- `PATCH /api/channels/{id}/config`：`web_access: null` = **关闭**（`_UNSET` 区分「没提到」与「显式清空」，
  与既有 `workspace_root` 同一手法）；`'search'` / `'search+fetch'` 合法；其它值整条 400、其它字段也不写。
- `PATCH /api/agents/{id}`：`web_access: null` 一律 400（`AgentUpdate._reject_explicit_null`，与该端点
  既有 `workspace_backend` / `permission_mode` 同一纪律）；Agent 级只能在两档之间切换。
- 两条不对称在 `api.py` 的模型 docstring 与 `db.update_channel_config` 的 docstring 里都写明了理由
  （「不给清空路径就变成单向开关」vs「该端点的既有纪律」）。

## 2. 红证据（先红后绿的「红」）

红证据在**本票自己的分支**上取得：先写用例、在**未改 DDL** 的树上跑（当时的 `db.py` 仍是 v17），
所以失败原因就是库形态本身，而不是断言写错。

```sh
cd /Users/apple/orca/workspaces/alibot/issue-184-impl/app
../.venv/bin/python -m pytest tests/test_web_access_schema.py
```

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________ test_scope_kind_web_is_accepted_and_unknown_kinds_are_rejected _________
>           conn.execute(
                "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) VALUES ('web', ?, ?)",
                ("duckduckgo", NOW),
            )
E           sqlite3.IntegrityError: CHECK constraint failed: scope_kind IN ('model', 'mcp', 'docker')
tests/test_web_access_schema.py:52: IntegrityError
=========================== short test summary info ============================
FAILED tests/test_web_access_schema.py::test_scope_kind_web_is_accepted_and_unknown_kinds_are_rejected
1 failed in 0.64s
```

```text
FAILED tests/test_web_access_schema.py::test_channel_and_agent_web_access_take_only_the_two_documented_levels
1 failed, 1 passed in 0.32s
```

两条红都来自 v17 库里**没有** `web` 档与 `web_access` 列——这正是本票要加的形态。

## 3. 绿证据

```sh
cd /Users/apple/orca/workspaces/alibot/issue-184-impl/app
../.venv/bin/python -m pytest tests/test_web_access_schema.py tests/test_web_access_config.py
```

```text
...........                                                              [100%]
11 passed in 1.18s
```

迁移链连续性（真实子进程，既有守卫）：

```sh
cd /Users/apple/orca/workspaces/alibot/issue-184-impl/app
../.venv/bin/python -m pytest tests/test_migration_chain_guard.py tests/test_schema_version_pins_guard.py tests/test_migration_v15_to_v17.py
```

读数见 §4 的全量 pytest（包含这三条）。

## 4. 门禁读数（`scripts/check.sh` 全量，仓库根）

```sh
unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1
scripts/check.sh
```

| 步骤 | 读数 |
| --- | --- |
| pytest（全量，从 `app/`） | ``872 passed, 10 skipped in 372.75s`` |
| ruff check | ``All checks passed!`` |
| ruff format **--check**（只检查，**不跑 `ruff format`**） | ``225 files already formatted`` |
| mypy（仓库根） | ``Success: no issues found in 94 source files`` |
| pyright | ``0 errors, 0 warnings, 0 informations`` |
| node --test（Studio DOM 用例族 glob） | ``tests 191 / pass 191 / fail 0`` |
| 结论 | ``门禁全绿`` |

## 5. 已知限制（如实记，留后续票）

- **Agent 级覆盖回不到「跟随频道」**：`PATCH /api/agents/{id}` 的所有字段都拒绝显式 `null`，因此
  `agent_config.web_access` 一旦写过就只能换成另一档，不能回到 `NULL`（跟随频道）。这是**既有缺陷**，
  同样适用于 `workspace_backend` / `permission_mode` / `allow_local_shell` / `docker_image`；按人定 D5
  第 3 条，本票**不顺手修**（修它会改所有 agent 覆盖字段的 PATCH 契约，超出 #184 范围）。
- 本票**没有**做任何装配：`web_access` 非 `NULL` 时工具面仍然逐字不变，联网工具要等 #186。

## 6. 自决的细节与理由

- **`WEB_ACCESS_LEVELS` 常量放在 `db.py`**（与 `PERMISSION_MODES` / `WORKSPACE_BACKENDS` 同处）：
  库级 `CHECK` 与库层校验共用同一份白名单，避免「DDL 一套、校验一套」漂移。
- **两列都允许 `NULL`（无列默认值）**：`NULL` 是「关闭 / 跟随频道」这个合法状态的正本表达，
  给默认值会凭空造出一个「已配置」的假事实。
- **错误文案走 i18n 两端**（`error.field.channel_web_access_invalid` / `error.field.agent_web_access_invalid`）：
  与同端点既有字段错误同一族，前端不需要新分支。
- **`_step_17_to_18` 幂等**：表级 DDL 已点名 `web` 就跳过重建（与 `14 → 15` 同一纪律），
  防止手工重放把同一张表反复重建。
- **`_migration_history.SOURCE_COMMITS[18]` 填 `'pending'`**：留档的既有形状是「合并后回填该合并提交」，
  本票合并时由协调者/后续维护者补。
