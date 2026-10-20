# 验收取证：#195 自动认领默认开启，且仅在自主频道生效

**票型**：实施票（schema 20 + 存量回填 + 默认值语义变更）｜**门禁**：`scripts/check.sh` 全量
**Ticket**：<https://github.com/whutlichao/alibot/issues/195>
**base**：`b71f454`（从 main 切出的 `issue-195-impl`）
**采集时间**：2026-10-20｜**环境**：macOS（aarch64）；仓库根 `.venv`（Python 3.11.13）；Node v25

---

## 0. 判据 → 落点

| 判据 | 落点 |
| --- | --- |
| `agent_config.auto_claim` 的 DDL 默认 0 → 1 | `app/alibot/db.py` 的 `SCHEMA`（`DEFAULT 1`）与 `SCHEMA_VERSION = 20` |
| 创建 Agent 继续吃列默认值 | `Database.create_agent` 的 `INSERT INTO agent_config(...)` 仍不写 `auto_claim` |
| `19 → 20` 一步：重建表 + 存量回填 + 真实行数 | `app/alibot/migrate.py` 的 `_step_19_to_20` 与 `STEPS` 登记 |
| 迁移留档 | `app/tests/_migration_history.py` 的 `SOURCE_COMMITS[20]` / `OBJECTS['agent_config_v20']` / `VERSION_OBJECTS[20]` |
| 默认值断言与 PATCH 往返 | `app/tests/test_auto_claim.py::test_auto_claim_defaults_on_and_round_trips_through_the_patch_endpoint` |
| v19 迁移实测（回填 / 默认 / 行与列不变） | `app/tests/test_schema_migration.py::test_v19_upgrade_flips_the_default_and_backfills_existing_auto_claim_rows` |
| 空白表迁移后的默认值 | `app/tests/test_schema_migration.py::test_theme_and_auto_claim_checks_are_present_after_migration` |
| 行为三条（自主+开 / 自主+关 / team+开） | `test_auto_claim.py` 的认领正样本、`test_new_task_stays_unclaimed_when_the_switch_is_off`、`test_new_task_in_a_team_channel_stays_unclaimed_even_with_the_switch_on` |
| 裁决逻辑不动 | `app/alibot/orchestration.py::claim_new_task` 未改（diff 无该文件） |
| 文档默认值与生效条件 | `CONTEXT.md` **认领**词条、`app/README.md` 的 `agent_config` 行 / 认领小节 / 端点说明、`docs/adr/0027-*.md` |

---

## 1. 门禁（全量 `scripts/check.sh`）

```
== pytest（全量）
952 passed, 10 skipped in 536.37s (0:08:56)

== ruff check
All checks passed!

== ruff format --check
242 files already formatted

== mypy（仓库根）
Success: no issues found in 101 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族）
ℹ tests 201
ℹ pass 201
ℹ fail 0

门禁全绿
```

---

## 2. 迁移实测（隔离数据目录，不碰 `~/Alibot`）

用仓库既有 v19 形态 fixture 造库（`tests.test_schema_migration._build_library(..., 19)`），灌入
两条既有 `agent_config` 行：`mem_x` 的 `auto_claim = 0`、`mem_y` 的 `auto_claim = 1`，然后把整个
数据目录复制成副本再迁移：

```
$ PYTHONPATH=$PWD/app .venv/bin/python -m alibot --migrate --data-dir /tmp/alibot-195-migrate/copy
迁移：/tmp/alibot-195-migrate/copy/alibot.db
源版本：19 → 目标版本：20
本次备份：/tmp/alibot-195-migrate/copy-backup-19-to-20-20261020T132814Z

实际执行的步骤（每步改动的数据行数）：
  19 → 20  auto claim defaults on (agent_config rebuild + one-time backfill)  改动 1 行

迁移完成：schema_version = 20；本次共改动 1 行。
EXIT=0
```

迁移前（全列读数）：

```
('mem_x', '前端', 'ollama:qwen2.5', '提示', 1, 0, None, None, 0, None, '2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z')
('mem_y', '研究', 'ollama:qwen2.5', '另一条提示', 1, 1, None, None, 0, None, '2026-10-03T00:00:00Z', '2026-10-04T00:00:00Z')
```

迁移后：

```
('mem_x', '前端', 'ollama:qwen2.5', '提示', 1, 1, None, None, 0, None, '2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z')
('mem_y', '研究', 'ollama:qwen2.5', '另一条提示', 1, 1, None, None, 0, None, '2026-10-03T00:00:00Z', '2026-10-04T00:00:00Z')
# SELECT COUNT(*) FROM agent_config;                              -- 2（行数不变）
# SELECT COUNT(*) FROM agent_config WHERE auto_claim = 1;         -- 2
# SELECT COUNT(*) FROM agent_config WHERE member_id = 'mem_x' AND auto_claim = 1; -- 1（本次由 0 变 1 的行）
# SELECT value FROM schema_meta WHERE key = 'schema_version';      -- 20
# INSERT INTO agent_config(member_id, created_at, updated_at) VALUES ('mem_z','x','x');
# SELECT auto_claim FROM agent_config WHERE member_id = 'mem_z';   -- 1（新插入的默认值）
```

- 报告「改动 1 行」= 库内实际由 `0 → 1` 的行数（恰好 `mem_x` 一行）；已是 1 的 `mem_y` 不动。
- `agent_config` 行数 2 → 2；`member_id` 与其它列（含 `updated_at`）逐行不变。
- 迁移后直接 `INSERT INTO agent_config(member_id, created_at, updated_at)` 不写 `auto_claim`，
  读回 1（重建后的 DDL 默认值已生效）。

---

## 3. 行为实测（HTTP 黑盒，隔离实例）

实例：`PYTHONPATH=$PWD/app .venv/bin/python -m alibot --port 8897 --data-dir /tmp/alibot-195-behavior`
（本机 8799 已被一个旧进程占用，改用 8897；**不碰**用户实例与默认数据目录）。

| # | 场景 | 命令要点 | 读数 |
| --- | --- | --- | --- |
| A | 自主频道 + 默认开启 | `POST /api/agents` → `POST /api/channels`（含成员）→ `POST /api/channels/{id}/tasks` | `agent.auto_claim=true`；`task.status=in_progress`、`assignee_member_id=mem_e499b6c6b8cd`、`claimed_at=2026-10-20T13:28:42.882Z` |
| B | 自主频道 + 显式关闭 | 同上，创建后 `PATCH /api/agents/{id} {"auto_claim":false}` | 建 Agent 读回 `auto_claim=true`（默认），关闭后建任务：`status=todo`、`assignee=null`、`claimed_at=null` |
| C | team 频道 + 开关开 | 创建队长与成员（成员默认 `true`）→ `PATCH /api/channels/{id}/config {"mode":"team","team_leader_member_id":...}` → 建任务 | `channel.mode=team`；`task.status=todo`、`assignee=null`、`claimed_at=null` |

原始输出：

```text
== A: autonomous + auto_claim default on
agent=mem_e499b6c6b8cd auto_claim=true
task=task_c40df715067f status=in_progress assignee=mem_e499b6c6b8cd claimed_at=2026-10-20T13:28:42.882Z
== B: autonomous + auto_claim off
agent=mem_37a49167c7fc default=true
task=task_69dcbfb9d909 status=todo assignee=null claimed_at=null
== C: team + auto_claim on
leader=mem_a23ac8120d04 member=mem_907c26e69d4a member_default=true
{"mode":"team","leader":"mem_a23ac8120d04"}
task=task_b498cb1408b7 status=todo assignee=null claimed_at=null
```

---

## 4. 边界与已知代价

- **不回溯任务**：迁移只改 `agent_config.auto_claim`，不扫频道既有 `todo` / 未认领任务，不改任何
  `task` 行。升级前已存在的未认领任务保持原状。
- **升级会覆盖此前显式关闭的存量行**：库内无法区分「显式关闭」与「从未配置」，这是人定规则
  「存量 Agent 一次性回填」的代价；升级后仍可经 `PATCH /api/agents/{id}` 逐 Agent 关闭。
- **team 行为不变**：`claim_new_task` 的 `mode != team` 早退原样保留；本票只加测试锁定
  「team + `auto_claim = 1` 仍不认领」。
- **Studio 未改**：控件与文案原样，开关仍可逐 Agent 关闭。
