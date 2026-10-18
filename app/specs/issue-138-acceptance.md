# #138 验收：schema 15 → 16（team / Plan / SOP 表与触发器）

- 票面：`#138`（Part of `#131`）；票型 impl；门禁 G-impl。
- 分支：`issue-138-impl`；基线：集成分支 tip `f829d13`。
- 规格：`app/specs/agentscope-2-alignment.md` §4.1 / §4.2；ADR-0015 / 0018 / 0022 / 0023。
- 范围：**只有 schema 与迁移**，不改行为代码（`orchestration` / `agent_seam` 零改动）、不回填业务数据。

## 改了什么

| 落点 | 改动 |
| --- | --- |
| `app/alibot/db.py` | `SCHEMA_VERSION = 16`；`channel.mode`（默认 `autonomous`）与 `team_leader_member_id`（可空）；两条 `channel_team_requires_leader_*` 触发器；六张新表 `team_run` / `team_assignment` / `plan_run` / `plan_task` / `sop_definition` / `sop_run` + 两条 `idx_team_assignment_*` 索引 + 规格 §4.2 的 CHECK / UNIQUE / 外键（含 `plan_task ON DELETE CASCADE`）。 |
| `app/alibot/migrate.py` | `MigrationStep(15, 16, "agentscope-2 team/plan/sop expand", _step_15_to_16)`；该步只按当前 `SCHEMA` 建六表、给 `channel` 补两列，索引 / 触发器交给每步之后既有的对齐兜底。 |
| `app/tests/_migration_history.py` | v16 留档：`channel_v16`、六表、两索引、两触发器、`VERSION_OBJECTS[16]`、`SOURCE_COMMITS[16] = 'pending'`。 |
| `app/tests/test_team_plan_sop_schema.py` | 新增 5 条外部可观察测试（见下）。 |
| `app/tests/test_silent_schema_step.py` | 一处派生断言从「`14 → SCHEMA_VERSION`」改为「`14 → 15`」：原来那条在任意后续 bump 时会假红，与本票的迁移步骤无关。 |

> 注：`app/specs/issue-135-research.md` 的 `ruff format --check` 修正在集成 tip `8b8e0c7` 已由协调者落库；
> 本分支 merge 该 tip 后不再包含这个文件的改动。

## 逐条判据

### 1. `SCHEMA_VERSION = 16`，注册 `MigrationStep(15, 16)`，链连续、严格 +1

```console
$ grep -n "SCHEMA_VERSION = 16\|MigrationStep(15, 16" app/alibot/db.py app/alibot/migrate.py
app/alibot/db.py:76:SCHEMA_VERSION = 16
app/alibot/migrate.py:797:    MigrationStep(15, 16, "agentscope-2 team/plan/sop expand", _step_15_to_16),

$ .venv/bin/python -m pytest app/tests/test_migration_chain_guard.py
15 passed in 1.41s
```

`test_migration_chain_guard.py` 对每个源版本（`3..15`）用真实子进程跑 `--migrate`，断言报告里的步骤表
从库版本起、每步严格 +1、彼此衔接、终点 `== SCHEMA_VERSION`；以及支持下界仍是 3。全绿。

### 2. `channel.mode` 默认 `autonomous`、`team_leader_member_id` 可空；触发器拒绝 team 但无队长

隔离副本上真迁移后的读数：

```console
$ sqlite3 /tmp/issue138-evidence/data/alibot.db \
    "select mode, team_leader_member_id from channel where id='ch_dev'"
autonomous|

$ sqlite3 /tmp/issue138-evidence/data/alibot.db \
    "insert into channel(id,name,description,created_at,mode) values ('ch_team','t','','2026-10-18T09:00:00Z','team')"
Error: ... team channel requires a leader

$ .venv/bin/python -m pytest app/tests/test_team_plan_sop_schema.py::test_a_team_channel_without_a_leader_is_rejected_on_insert_and_update
1 passed
```

该测试同时取证 INSERT 与 `UPDATE OF mode, team_leader_member_id` 两处：只改 `mode='team'` 被拒；
同一条 UPDATE 里给上队长成功；把队长摘成 `NULL`（仍是 team）同样被拒。

### 3. 六张新表；索引 / CHECK / UNIQUE 与规格 §4.2 一致；全量建库与迁移两处一致

```console
$ .venv/bin/python -c "…sqlite_master 查询…"
version 16
tables ['plan_run', 'plan_task', 'sop_definition', 'sop_run', 'team_assignment', 'team_run']
indexes ['idx_team_assignment_target', 'idx_team_assignment_team_run']
triggers ['channel_team_requires_leader_insert', 'channel_team_requires_leader_update']

$ .venv/bin/python -m pytest app/tests/test_team_plan_sop_schema.py
5 passed in 0.53s
```

`test_the_six_new_tables_and_indexes_exist_and_match_a_fresh_library` 把「v15 迁移后的库」与
「本代码全新初始化的库」逐表比列、比对索引与触发器，两处（`SCHEMA` 全量路径 vs 迁移路径）一致。
同文件的 `test_state_checks_and_plan_sequence_uniqueness_are_enforced` 取证：

- `team_run.status='bogus'` ⇒ `CHECK constraint failed: status IN ('running', 'completed', 'failed', 'interrupted', 'invalid-leader')`；
- `team_assignment.state='bogus'`、`jump_consumed=2` ⇒ CHECK 拒绝；
- `plan_task.state='bogus'` ⇒ CHECK 拒绝；同一 `(plan_run_id, seq)` 第二条 ⇒
  `UNIQUE constraint failed: plan_task.plan_run_id, plan_task.seq`；
- `sop_definition.enabled=2`、`sop_run.phase='bogus'` ⇒ CHECK 拒绝；
- 删除 `plan_run` 后其 `plan_task` 归零（`ON DELETE CASCADE`）。

### 4. 旧行默认值正确；不改既有列类型 / 默认值；不回填业务数据

```console
$ sqlite3 /tmp/issue138-evidence/data/alibot.db \
    "select mode, team_leader_member_id from channel where id='ch_dev'"
autonomous|
$ sqlite3 /tmp/issue138-evidence/data/alibot.db "select name, description from channel where id='ch_dev'"
dev|开发频道
```

`test_a_v15_library_reaches_v16_and_old_channels_default_to_autonomous` 追加取证：频道名与描述逐字未改，
`round_outcome` 等既有行的保真由既有 `test_silent_schema_step.py` / `test_schema_migration.py` 继续覆盖。
迁移步骤只 `ADD COLUMN` + `CREATE TABLE`，没有 `DROP` / `ALTER COLUMN` / `UPDATE`，因此既有列类型与
默认值不变、业务行零回填（报告的「改动 0 行」）。

### 5. `--migrate --dry-run` 原库 sha256 前后一致；真迁移后各表可查；链守卫绿

隔离副本（`/tmp/issue138-evidence/data`，由 v15 留档 fixture 生成）：

```console
$ shasum -a 256 …/data/alibot.db
cd4df96644380f0bbb4aab908a4512cb8a12c43b3dea941e7c59c877026906d8  …/data/alibot.db

$ python -m alibot --migrate --dry-run --data-dir …/data
预演（只读，在数据目录内的临时副本上跑完整迁移路径）：…/data/alibot.db
源版本：15 → 目标版本：16

将执行的步骤（每步将改动的数据行数）：
  15 → 16  agentscope-2 team/plan/sop expand  改动 0 行

原库文件 sha256（预演前）：cd4df96644380f0bbb4aab908a4512cb8a12c43b3dea941e7c59c877026906d8
原库文件 sha256（预演后）：cd4df96644380f0bbb4aab908a4512cb8a12c43b3dea941e7c59c877026906d8
原库零字节改动：前后读数一致。
预演结论：这条链在副本上跑通了；本次共将改动 0 行。

$ shasum -a 256 …/data/alibot.db
cd4df96644380f0bbb4aab908a4512cb8a12c43b3dea941e7c59c877026906d8  …/data/alibot.db
```

真迁移（同一副本）：

```console
$ python -m alibot --migrate --data-dir …/data
源版本：15 → 目标版本：16
本次备份：…/data-backup-15-to-16-20261018T033904Z
  15 → 16  agentscope-2 team/plan/sop expand  改动 0 行
迁移完成：schema_version = 16；本次共改动 0 行。
```

`test_dry_run_keeps_the_original_library_byte_identical` 把上面两个 sha256 与退出码固化成回归测试
（同时断言预演不建备份）。

## 门禁

```console
$ scripts/check.sh
…
ℹ tests 132
ℹ pass 132
ℹ fail 0

门禁全绿
```

（退出码 0；pytest 全量、`ruff check`、`ruff format --check`、pyright、Node 用例族全绿。）

## 残余 / 说明

- `SOURCE_COMMITS[16] = 'pending'`：按该文件既有约定，合并后回填合并提交号。
- 新表在本票只有 DDL 与约束；读写行为、保留策略（Plan 20 run/30 天、SOP 50 run/90 天）属于后续票。
