# #139 验收：schema 16 → 17（workspace / permission / MCP / Skill / approval）

- 票面：`#139`（Part of `#131`）；票型 impl；门禁 G-impl。
- 分支：`issue-139-impl`；基线：集成分支 tip `b63bfb7`（已含 #138，`SCHEMA_VERSION=16`）。
- 规格：`app/specs/agentscope-2-alignment.md` §4.1 / §4.3；ADR-0015 / ADR-0021。
- 范围：**只有 schema 与迁移**，不改行为代码、不回填其他业务数据（`remote_consent` 的复制是本票明确要求）。

## 改了什么

| 落点 | 改动 |
| --- | --- |
| `app/alibot/db.py` | `SCHEMA_VERSION = 17`；`channel.workspace_root`（可空）/ `workspace_backend`（默认 `local`）/ `permission_mode`（默认 `ask-writes`）；`agent_config.workspace_backend`（可空）/ `permission_mode`（可空）/ `allow_local_shell`（默认 0）/ `docker_image`（可空）；五张新表 `permission_rule` / `approval_request` / `mcp_server` / `skill` / `remote_consent_scope`，CHECK / UNIQUE / 外键按规格 §4.3 逐条落库（本版没有新增显式索引，与规格一致）。 |
| `app/alibot/migrate.py` | `MigrationStep(16, 17, "agentscope-2 workspace/permission/mcp/skill expand", _step_16_to_17)`；该步按当前 `SCHEMA` 建五表、给 `channel` / `agent_config` 补列，并把旧 `remote_consent` 每行复制成 `('model', provider, acknowledged_at)`（`INSERT OR IGNORE`，重放幂等、旧表保留）。 |
| `app/tests/_migration_history.py` | v17 留档：`channel_v17`、`agent_config_v17`、五张新表、`VERSION_OBJECTS[17]`、`SOURCE_COMMITS[17] = 'pending'`。 |
| `app/tests/test_schema_migration.py` | 新增 `_add_historical_column`：整表 DDL 留档（v17 的 `agent_config_v17` 已带 `auto_claim`）与「v3 基线 + 逐版补列」两条路线共存，避免同一列补两次。 |
| `app/tests/test_workspace_permission_schema.py` | 新增 5 条外部可观察测试（见下）。 |
| `app/README.md` | 数据模型表补五张新表、`channel` / `agent_config` 的 workspace 列；新增「备份数据目录 ≠ 备份项目文件」边界（ADR-0021 §五）。 |

## 逐条判据

### 1. `SCHEMA_VERSION = 17`，注册 `MigrationStep(16, 17)`；链连续

```console
$ grep -n "SCHEMA_VERSION = 17\|MigrationStep(" app/alibot/db.py app/alibot/migrate.py | tail -4
app/alibot/db.py:80:SCHEMA_VERSION = 17
app/alibot/migrate.py:815:STEPS: tuple[MigrationStep, ...] = (
app/alibot/migrate.py:839:    MigrationStep(
app/alibot/migrate.py:840:        16,
app/alibot/migrate.py:841:        17,
app/alibot/migrate.py:842:        "agentscope-2 workspace/permission/mcp/skill expand",
app/alibot/migrate.py:843:        _step_16_to_17,

$ .venv/bin/python -m pytest app/tests/test_migration_chain_guard.py
16 passed in 1.93s
```

`test_migration_chain_guard.py` 对每个源版本（`3..16`）用真实子进程跑 `--migrate`，断言报告里的步骤表
从库版本起、每步严格 +1、彼此衔接、终点 `== SCHEMA_VERSION`；以及支持下界仍是 3。全绿。

### 2. `channel` / `agent_config` 新列默认值正确

隔离副本（`/tmp/issue139-evidence/data`，由 v16 留档 fixture 生成并带一条旧 `agent_config` 行）真迁移后：

```console
$ sqlite3 …/data/alibot.db "select workspace_root, workspace_backend, permission_mode from channel where id='ch_dev'"
|local|ask-writes

$ sqlite3 …/data/alibot.db "select workspace_backend, permission_mode, allow_local_shell, docker_image from agent_config where member_id='mem_x'"
||0|
```

`workspace_root` / `docker_image` 为空、Agent 的后端与权限档为 `NULL`（跟随频道）、本地 shell 关闭；
既有列不回填（`name=dev`、`description=开发频道`、`mode=autonomous` 逐字保留）。由
`test_a_v16_library_reaches_v17_and_old_rows_keep_the_documented_defaults` 固化为回归。

### 3. 五张新表；CHECK / UNIQUE 与规格 §4.3 一致；迁移路径与全量建库路径一致

```console
$ sqlite3 …/data/alibot.db "select name from sqlite_master where type='table' and name in ('permission_rule','approval_request','mcp_server','skill','remote_consent_scope') order by name"
approval_request
mcp_server
permission_rule
remote_consent_scope
skill

$ sqlite3 …/data/alibot.db "select count(*) from sqlite_master where type='index' and sql is not null and tbl_name in ('permission_rule','approval_request','mcp_server','skill','remote_consent_scope')"
0
```

规格 §4.3 没有给这五张表新增显式索引，因此读数为 0（唯一性由 `PRIMARY KEY` / `UNIQUE` 的隐式索引承担）。

```console
$ .venv/bin/python -m pytest app/tests/test_workspace_permission_schema.py
5 passed in 0.59s
```

其中 `test_the_new_tables_and_columns_match_a_fresh_library` 把「v16 迁移后的库」与「本代码全新初始化的
库」逐表比列、比隐式唯一索引，两处（`SCHEMA` 全量路径 vs 迁移路径）一致；并先断言 v16 库里**没有**这些
表与列（纯增量）。`test_workspace_permission_and_registry_constraints_are_enforced` 逐条取证：

- `channel.workspace_backend='bogus'` / `permission_mode='bogus'`、`agent_config` 的同名列、`allow_local_shell=2`
  ⇒ `CHECK constraint failed`；
- `permission_rule.scope_kind='org'` / `behavior='maybe'` ⇒ CHECK 拒绝；
- `approval_request.state='waiting'` ⇒ CHECK 拒绝；`target_id='ch_missing'` ⇒ 外键拒绝；
- `mcp_server.transport='websocket'` / `enabled=2` ⇒ CHECK 拒绝；同一 `(scope_kind, scope_id, name)` 第二条 ⇒
  `UNIQUE constraint failed`；
- `skill.enabled=2` ⇒ CHECK 拒绝；同一 `(scope_kind, scope_id, name)` 第二条 ⇒ UNIQUE 拒绝；
- `remote_consent_scope.scope_kind='registry'` ⇒ CHECK 拒绝；复合主键重复 ⇒ UNIQUE/PRIMARY KEY 拒绝。

### 4. 旧 `remote_consent` 行复制成 `('model', provider, acknowledged_at)`；旧表保留

```console
$ sqlite3 …/data/alibot.db "select provider, acknowledged_at from remote_consent order by provider"
ollama|2026-10-17T01:00:00Z
openai|2026-10-17T02:00:00Z

$ sqlite3 …/data/alibot.db "select scope_kind, scope_id, acknowledged_at from remote_consent_scope order by scope_id"
model|ollama|2026-10-17T01:00:00Z
model|openai|2026-10-17T02:00:00Z
```

真迁移报告里该步读数为「改动 2 行」，正是实际复制的旧行数。
`test_legacy_remote_consent_rows_become_model_scopes_once_and_the_step_is_idempotent` 固化两件事：
复制计数 = 旧行数；把 `schema_version` 退回 16 再跑同一步 ⇒ 报告「改动 0 行」，新表仍是两条、确认时间
未被覆盖（`INSERT OR IGNORE` 幂等）。旧表逐字保留（ADR-0015 的兼容面）。

### 5. `--migrate --dry-run` 原库 sha256 前后一致；真迁移后各表可查；链守卫绿

隔离副本（`/tmp/issue139-evidence/data`，v16 留档 fixture + 2 条旧 consent）：

```console
$ shasum -a 256 …/data/alibot.db
0756e41be91ba3766d5138154eaf38a08fef92a6ddba69514d4f25f5962c7256  …/data/alibot.db

$ python -m alibot --migrate --dry-run --data-dir …/data
预演（只读，在数据目录内的临时副本上跑完整迁移路径）：…/data/alibot.db
源版本：16 → 目标版本：17

将执行的步骤（每步将改动的数据行数）：
  16 → 17  agentscope-2 workspace/permission/mcp/skill expand  改动 2 行

原库文件 sha256（预演前）：0756e41be91ba3766d5138154eaf38a08fef92a6ddba69514d4f25f5962c7256
原库文件 sha256（预演后）：0756e41be91ba3766d5138154eaf38a08fef92a6ddba69514d4f25f5962c7256
原库零字节改动：前后读数一致。
预演结论：这条链在副本上跑通了；本次共将改动 2 行。

$ shasum -a 256 …/data/alibot.db
0756e41be91ba3766d5138154eaf38a08fef92a6ddba69514d4f25f5962c7256  …/data/alibot.db
```

真迁移（同一副本）：

```console
$ python -m alibot --migrate --data-dir …/data
源版本：16 → 目标版本：17
本次备份：…/data-backup-16-to-17-20261018T035158Z
  16 → 17  agentscope-2 workspace/permission/mcp/skill expand  改动 2 行
迁移完成：schema_version = 17；本次共改动 2 行。
```

`test_dry_run_keeps_the_original_library_byte_identical` 把两个 sha256 与退出码固化成回归测试
（同时断言预演不建备份）；`test_migration_chain_guard.py` 全绿。

## 门禁

```console
$ scripts/check.sh
…
ℹ tests 132
ℹ suites 0
ℹ pass 132
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 3637.925125

门禁全绿
```

（退出码 0；pytest 全量、`ruff check`、`ruff format --check`、pyright、Node 用例族全绿。上面是 Node
用例族的汇总读数。）

## 残余 / 说明

- `SOURCE_COMMITS[17] = 'pending'`：按该文件既有约定，合并后回填合并提交号。
- 五张新表在本票只有 DDL 与约束；权限引擎、HITL 端点、MCP / Skill 的装配与读取路径属于后续票
  （#140 起）。本票不改任何读路径，旧 `remote_consent` 的读取方仍原样工作。
- `workspace_root` 指向用户项目目录，不在数据目录备份内（ADR-0021 §五，README 已写明）。
