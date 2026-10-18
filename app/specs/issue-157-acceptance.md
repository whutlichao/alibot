# #157 验收：迁移与回滚演练（15 → 16 → 17）

- 票面：[#157](https://github.com/whutlichao/alibot/issues/157)（Part of #131）
- 判据来源：`app/specs/agentscope-2-alignment.md` §4.1 / §4.2 / §4.3 / §5.1；ADR-0015
- 分支 / worktree：`issue-157-impl` / `/tmp/alibot-wt/issue-157-impl`（基线 `0dd50f9`）
- 演练脚本：`app/specs/issue-157-migration/drill.py`（一键复跑，任何判据不成立即 `FAIL` + 退出 1）
- 失败注入驱动：`app/specs/issue-157-migration/fail_injection.py`（退出 5 = 注入如期生效）
- 新增测试：`app/tests/test_migration_v15_to_v17.py`
- 留档：`app/tests/_migration_history.py`（回填 v15 / v16 / v17 的 `SOURCE_COMMITS`）

## 演练方式（v15 库从哪里来）

用 `git archive 2aebc3f app/alibot` 解出 **#131 规格合入前的 main**（`2aebc3f`，旧代码
`SCHEMA_VERSION = 15`），再用这份**旧代码**真正初始化 v15 库：

```console
$ git -C . archive 2aebc3f app/alibot | tar -x -C /tmp/alibot-157-drill/v15-source
$ PYTHONPATH=/tmp/alibot-157-drill/v15-source/app .venv/bin/python -c \
    'import sys; from alibot.db import Database, SCHEMA_VERSION; Database(sys.argv[1]).close(); print(SCHEMA_VERSION)' \
    /tmp/alibot-157-drill/data
15
```

**没有**用 `app/tests/_migration_history.py` 的 v15 形态拼装演练库（留档只用于 pytest 侧的链守卫），
因此「v15 形态」是历史代码的产物，不是本票对它的复述。业务数据由演练脚本用 SQL 灌入（形状按 v15 的
`SCHEMA`，不加任何 v16 / v17 的列）：

| 表 | 行 |
| --- | --- |
| `member` | `owner`、`mem_x`（agent） |
| `agent_config` | `mem_x`：role=`planner`、model=`deepseek-v4`、auto_claim=0 |
| `channel` / `target` | `ch_dev`（channel 型 target） |
| `target_member` / `target_read` | 各 2 行 |
| `target_budget` | epoch=1、used=3、applied_limit=5 |
| `channel_budget_limit` | 5 |
| `message` | `msg_1`「第一条」、`msg_2`「第二条」 |
| `task` | `task_1`「修复登录」（`task_t1` 任务线程） |
| `round_outcome` | 1 行，status=`silent` |
| `reminder` | 1 行（`2026-10-20 09:00`） |
| `remote_consent` | `anthropic`，确认时间 `2026-10-14T09:00:10Z` |
| `attachment` | `att_1`，16 字节，字节落 `attachments/probe.bin` |

一键命令与结论：

```console
$ .venv/bin/python app/specs/issue-157-migration/drill.py
...
ALL PASS
$ echo $?
0
```

演练工作目录：`/tmp/alibot-157-drill`（每次重建；`data` = 迁移库、`data-pristine` = 迁移前副本、
`data-restored` = 恢复出来的备份、`data-injected` = 失败注入副本）。

---

## 判据 1：v15 库 + `--migrate --dry-run`，原库 sha256 前后一致，报告步骤与行数

命令（演练脚本内部的真实子进程；可单独复跑）：

```console
$ PYTHONPATH=app .venv/bin/python -m alibot --migrate --dry-run --data-dir /tmp/alibot-157-drill/data
```

真实读数（stdout 原样）：

```text
预演（只读，在数据目录内的临时副本上跑完整迁移路径）：/tmp/alibot-157-drill/data/alibot.db
源版本：15 → 目标版本：17

将执行的步骤（每步将改动的数据行数）：
  15 → 16  agentscope-2 team/plan/sop expand  改动 0 行
  16 → 17  agentscope-2 workspace/permission/mcp/skill expand  改动 1 行

原库文件 sha256（预演前）：b3ad373b5bcc99424d5f3b650bcc4a495415cc72de4243fa0e9c495185969909
原库文件 sha256（预演后）：b3ad373b5bcc99424d5f3b650bcc4a495415cc72de4243fa0e9c495185969909
原库零字节改动：前后读数一致。
临时副本：/tmp/alibot-157-drill/data/.alibot-dry-run-… 已删除；数据目录内不留副本、不建备份、不写审计台账、不改库里的 Schema 版本。
预演结论：这条链在副本上跑通了；本次共将改动 1 行。
```

- 退出码 `0`；`15 → 16` 改动 **0** 行、`16 → 17` 改动 **1** 行（旧 `remote_consent` 的复制）。
- 原库 sha256 **前后逐字相同**（`b3ad373b…69909`）。
- 演练脚本另外逐文件比对数据目录：**无**临时副本、**无**备份目录、**无** `schema-migrations.jsonl`，
  库里的 `schema_version` 仍是 15。

---

## 判据 2：真迁移后 17、旧行默认值、新表可查

命令：

```console
$ PYTHONPATH=app .venv/bin/python -m alibot --migrate --data-dir /tmp/alibot-157-drill/data
```

真实读数（stdout 原样）：

```text
迁移：/tmp/alibot-157-drill/data/alibot.db
源版本：15 → 目标版本：17
本次备份：/tmp/alibot-157-drill/data-backup-15-to-17-20261018T074909Z

实际执行的步骤（每步改动的数据行数）：
  15 → 16  agentscope-2 team/plan/sop expand  改动 0 行
  16 → 17  agentscope-2 workspace/permission/mcp/skill expand  改动 1 行

迁移完成：schema_version = 17；本次共改动 1 行。
审计记录：/tmp/alibot-157-drill/data/schema-migrations.jsonl
```

退出码 `0`，`schema_version = 17`。旧行读数（演练脚本直读迁移后的库）：

```json
{"mode": "autonomous", "team_leader_member_id": null, "workspace_root": null,
 "workspace_backend": "local", "permission_mode": "ask-writes"}
{"role": "planner", "auto_claim": 0, "workspace_backend": null,
 "permission_mode": null, "allow_local_shell": 0, "docker_image": null}
[("model", "anthropic", "2026-10-14T09:00:10Z")]
```

- `channel.mode='autonomous'`、`team_leader_member_id=NULL`、`workspace_root=NULL`、
  `workspace_backend='local'`、`permission_mode='ask-writes'`（后两列按列默认值解释旧行）。
- `agent_config` 的新列：`workspace_backend=NULL`、`permission_mode=NULL`、`allow_local_shell=0`、
  `docker_image=NULL`；`auto_claim=0`、`role='planner'` 保持原值。
- 旧 `remote_consent` 行被复制成 `('model', 'anthropic', 原确认时间)`；旧表保留不删。
- 既有业务表行数逐项不变：`message=2`、`channel=1`、`target=2`、`task=1`、`reminder=1`、
  `attachment=1`、`round_outcome=1`、`target_budget=1`、`workspace_settings=1`、`remote_consent=1`。
- **新表可查**（11 张表逐张 `SELECT`，并各写一行探针读回）：

```json
{"team_run": "running", "team_assignment": "started", "plan_run": "running",
 "plan_task": "设计", "sop_definition": "巡检", "sop_run": "pending",
 "permission_rule": "ask", "approval_request": "pending",
 "mcp_server": "[\"TOKEN\"]", "skill": "sha256:probe",
 "remote_consent_scope": "2026-10-14T09:00:10Z",
 "team_trigger": "team channel requires a leader"}
```

`team_trigger` 那一项是**反向探针**：在迁移后的库里插入 `mode='team'` 且队长为 `NULL` 的频道，被
v16 的 `channel_team_requires_leader_insert` 触发器拒绝（`team channel requires a leader`）。

---

## 判据 3：备份目录存在；恢复后旧代码可打开；迁移失败回滚到原版本

### 3a. 备份与恢复

```console
$ ls -d /tmp/alibot-157-drill/data-backup-15-to-17-*
/tmp/alibot-157-drill/data-backup-15-to-17-20261018T074909Z
$ shasum -a 256 /tmp/alibot-157-drill/data-backup-15-to-17-20261018T074909Z/alibot.db
b3ad373b5bcc99424d5f3b650bcc4a495415cc72de4243fa0e9c495185969909  /tmp/alibot-157-drill/data-backup-15-to-17-20261018T074909Z/alibot.db
$ shasum -a 256 /tmp/alibot-157-drill/data-backup-15-to-17-20261018T074909Z/attachments/probe.bin /tmp/alibot-157-drill/data/attachments/probe.bin
0e22a93c611048eae817350dbce895ca674555e54a921a7f90d36f3e14cd005c  /tmp/alibot-157-drill/data-backup-15-to-17-20261018T074909Z/attachments/probe.bin
0e22a93c611048eae817350dbce895ca674555e54a921a7f90d36f3e14cd005c  /tmp/alibot-157-drill/data/attachments/probe.bin
```

- 备份是**整目录**（含 `attachments/` 字节），不是只复制 `alibot.db`；备份里的库与迁移前 sha256 一致。
- 恢复 = `cp -a <备份目录> /tmp/alibot-157-drill/data-restored`，随后用 **`2aebc3f` 的旧代码**打开：

```console
$ PYTHONPATH=/tmp/alibot-157-drill/v15-source/app .venv/bin/python -c '<OLD_CODE_READ>' /tmp/alibot-157-drill/data-restored
{"channels": ["ch_dev"], "consents": ["anthropic"], "members": ["owner", "mem_x"],
 "messages": ["第一条", "第二条"], "old_code_schema_version": 15, "tasks": ["task_1"]}
```

旧代码的版本闸门放行（恢复出来的库是 v15），业务数据（频道、两条消息、成员、任务、远端确认）
逐项读到；恢复库与迁移前 sha256 一致。演练脚本使用的读取入口是旧代码的
`Database.open_without_initializing` + `list_channels/list_messages/list_members/list_tasks/
list_remote_consents`（见 `drill.py` 的 `OLD_CODE_READ`）。

### 3b. 迁移失败回滚

注入方式：`fail_injection.py` 把注册表里的 `16 → 17` 换成「**先执行真实改动、再抛
`sqlite3.OperationalError`**」的同形步骤，然后走 `migrate.migrate_data_dir` 的**真实编排**
（整目录备份 → `BEGIN IMMEDIATE` → 逐步跑链 → `ROLLBACK` → 失败审计）。失败点在 17 的新表 / 新列
已建、旧确认行已复制**之后**，因此回滚必须把 DDL 与数据一并撤销。

```console
$ PYTHONPATH=app .venv/bin/python app/specs/issue-157-migration/fail_injection.py /tmp/alibot-157-drill/data-injected
注入生效，迁移按契约失败：迁移失败并已回滚：库仍停在版本 15，未被改动。原因：注入的失败：16 → 17 的改动已落库，随后拒绝继续
$ echo $?
5
```

回滚后的真实读数：

- 库文件 sha256 与注入前一致（`b3ad373b…69909`）；`schema_version` 仍是 **15**。
- `channel` 的列集回到 v15（新列 `mode` / `team_leader_member_id` / `workspace_root` /
  `workspace_backend` / `permission_mode` 随回滚消失）。
- `team_run` / `team_assignment` / `plan_run` / `plan_task` / `sop_definition` / `sop_run` /
  `permission_rule` / `approval_request` / `mcp_server` / `skill` / `remote_consent_scope`
  **全部不存在**（没有「新表建了、数据没搬」的中间态）。
- 台账留一条失败记录，且失败前的整目录备份已存在：

```json
{"backup_path": "/tmp/alibot-157-drill/data-injected-backup-15-to-17-20261018T074909Z",
 "error": "注入的失败：16 → 17 的改动已落库，随后拒绝继续",
 "finished_at": "2026-10-18T07:49:09.883Z", "from": 15, "result": "failed",
 "started_at": "2026-10-18T07:49:09.872Z", "to": 17}
```

- 回滚后的库仍能被 `2aebc3f` 的旧代码打开，并读到同一份业务数据（频道 / 消息 / 成员 / 任务 /
  远端确认）。

---

## 判据 4：迁移链测试、`_migration_history.py` 留档、回滚报告

### 4a. 迁移链测试

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_migration_chain_guard.py tests/test_migration_v15_to_v17.py
..................                                                       [100%]
18 passed in 2.03s
```

- 既有 `test_migration_chain_guard.py`（CLI 接缝）继续把报告的步骤表解析成数据：连续、每步严格
  +1、覆盖到 `SCHEMA_VERSION = 17`；v15 作为受支持源版本被逐一跑到终态。
- 新增 `test_migration_v15_to_v17.py` 两条：
  1. `test_v15_dry_run_keeps_the_library_byte_identical_and_reports_both_steps`（判据 1）；
  2. `test_v15_to_v17_sets_old_row_defaults_copies_consent_and_the_backup_restores`（判据 2 / 3a）。

### 4b. `_migration_history.py` 留档

回填 `SOURCE_COMMITS` 的 v15 / v16 / v17（此前为 `pending`）：

```python
15: '53fdfaa',  # #58 的合并提交
16: 'b63bfb7',  # #138 的合并提交
17: '5763122',  # #139 的合并提交
```

复核：

```console
$ for c in 53fdfaa b63bfb7 5763122; do git show $c:app/alibot/db.py | grep '^SCHEMA_VERSION'; done
SCHEMA_VERSION = 15
SCHEMA_VERSION = 16
SCHEMA_VERSION = 17
```

留档对象集与这些提交的 `SCHEMA` 对得上（去注释、按空白归一后逐对象比对；差异只有该文件已声明的
两处重建口径：由 `ADD COLUMN` 补的列、以及迁移引擎负责对齐的外键）。对象数：v15 = 24、v16 = 34、
v17 = 39。

### 4c. 回滚报告

见判据 3b：命令、退出码 `5`、失败正文、回滚后的 sha256 / 版本 / 表集 / 旧代码读数均已在上文列出；
同一份读数也可由 `drill.py` 第 6 段一键复跑。

---

## 全量门禁

```console
$ ./scripts/check.sh
== pytest（全量）
827 passed, 9 skipped in 359.28s (0:05:59)
== ruff check
All checks passed!
== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
207 files already formatted
== mypy（仓库根）
Success: no issues found in 89 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 164
ℹ pass 164
ℹ fail 0

门禁全绿
$ echo $?
0
```

（分支基线即集成 tip `0dd50f9`；`git merge --no-ff agentscope2-integration` 返回 `Already up to date`。
退出码 `0`。）

## 演练中的观察（不阻塞本票）

1. `16 → 17` 的旧确认行复制用 `INSERT OR IGNORE`：若 v15 库里存在 `provider IS NULL` 的
   `remote_consent` 行（SQLite 的非 INTEGER 主键允许 NULL），该行会被**静默跳过**，迁移仍成功
   （实测：改动 1 行、不报错）。这是「幂等复制」的既定取舍，NULL provider 本身也不是可解释的确认
   记录；未改实现，记录在此供后续数据清理参考。
2. v15 形态由 `2aebc3f` 的旧代码初始化，**不含**更早的三张兼容表
   （`channel_member` / `channel_read` / `channel_budget`）——新初始化的库本来就不会创建它们；
   半改名库的形态步骤由既有测试覆盖，不在本票范围。

## 残余

- `_migration_history.py` 里 v13 / v14 仍是 `pending`（#112 / #53 合并时留下的记录债，早于本票的
  15 → 17 窗口）；本票只回填 15 / 16 / 17。
- 演练用的是隔离数据目录与合成业务数据，不触碰任何真实用户数据目录。
