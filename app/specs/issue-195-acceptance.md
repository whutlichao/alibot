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

---

## 5. 双轴 code-review（Standards + Spec）

两轴按 `docs/agents/review-rules.md` 分开跑（diff = `b71f454..c090663` 加未跟踪的 ADR-0027；
Spec 正本 = issue #195）。本节补正只改文档、不改代码与测试，因此沿用 `c090663` 的全量门禁读数
（§1）；代码与测试未变。

### 5.1 Standards 轴：五条 findings 与处理

1. **P2（hard）— `app/README.md:439` 仍写着旧默认。** 原文：`POST /api/agents` … “（workspace 四项缺省
   = 跟随频道 / 内置镜像；开关缺省关闭）”。`git show a700d05 -- app/README.md` 表明这句是给
   `auto_claim`（#65）写的，不是 `allow_local_shell`；现在与 DDL `DEFAULT 1` 和同 diff 的
   125 / 238 / 507 行矛盾，违反 `docs/agents/review-rules.md` #6（文档与数据契约同步）。处理：**已改**，
   `app/README.md` 的 `POST /api/agents` 端点行改为「`auto_claim` 缺省开启，见 ADR-0027」。
2. **P2（hard）— `app/README.md:94-96, 113, 148` 仍钉在 v19。** 原文引用了 `### Schema 版本与迁移
   （15 → 19）`、`本代码的 SCHEMA_VERSION = 19`、`<数据目录>-backup-15-to-19-<UTC 时间戳>` 与
   `表清单与 SCHEMA_VERSION = 19 的 SCHEMA 一一对应`；而代码是 20、有第五步，ADR-0027 自己写
   `19 → 20`。ADR-0015 §六把 README 当迁移登记面（「只改『从首版升级』与『备份』两段」）。此外
   `只加不删、不回填业务数据` 已不能描述整条链。处理：**已改**，`app/README.md` 的
   「Schema 版本与迁移」章节：标题 `15 → 20`、常量改 20、备份名 `15-to-20`、表清单版本改 20，
   新增 `19 → 20` 步骤行，并把「只加不删」改为「除 `19 → 20` 的显式回填外，不回填业务数据」。
3. **P3（judgement）— `CONTEXT.md:592-594` 的轮次已过时。** 原文：`**本轮（15 → 19）**是四个严格 +1
   的步骤（… `18→19` 容器出网开关）`，与同文件新增的 **认领** 段落（`19 → 20`）及代码矛盾。
   `CONTEXT.md` 是领域语言正本（review-rules #1；ADR-0015 §六 点名该词条）。处理：**已改**，
   `CONTEXT.md` **数据迁移 (Schema Migration)** 词条改为 `（15 → 20）` 五个严格 +1 的步骤，列出
   `19→20`，并注明「除 `19→20` 的显式一次性回填外只加不删」。
4. **P3（judgement）— `app/README.md:507-508` 交叉引用断裂（本补丁引入）。** 原文：`写它只影响
   **之后新建**的任务；升级时的存量回填见下一条。` 下一条实际是 **配置变更不是发言**，存量回填
   在更上面的 242 行。处理：**已改**，`app/README.md` 该句改为「升级时的存量回填见上文『存量回填』」。
5. **P3（judgement）— `app/specs/issue-195-acceptance.md:86-92` 读数不可复核。** 原文：`ROWS 2 /
   FLIPPED 2` 与「改动 1 行 … 恰好 `mem_x` 一行」并排，却没有定义 `FLIPPED` 的查询；review-rules #7
   要求每条判据给命令与读数。处理：**已改**，该段改为逐条 SQL 注释（`COUNT(*)`、`WHERE auto_claim = 1`、
   `mem_x AND auto_claim = 1`、`schema_version`、新插入默认值），并删掉无定义的 `FLIPPED`。

Standards 轴另有一条「verified clean」结论，未产生 finding：`_step_19_to_20` 的顺序
（create → rebuild → `UPDATE … WHERE auto_claim = 0`）、`max(rowcount, 0)`、对齐兜底恢复索引/触发器、
「只改这一列 / 不动 `updated_at` / 不回溯 task」均与 ADR-0027 和新迁移测试一致；受默认 flip 影响的
测试改动是显式前置条件，不是放宽断言。

### 5.2 Spec 轴：结论与依据

**结论：correct（通过）。** 票面 6 条「改动」逐条有落点，没有缺失或越界实现：

| 票面条目 | 结论 | 依据 |
| --- | --- | --- |
| 默认值 flip：DDL `0 → 1`、`SCHEMA_VERSION 19 → 20`、创建路径继续吃列默认 | 已实现 | `app/alibot/db.py` 的 `SCHEMA` 与 `SCHEMA_VERSION`；`Database.create_agent` 的 INSERT 仍不写 `auto_claim`；`AgentCreate` 没有该字段（无第二套缺省） |
| `19 → 20` 同一 `MigrationStep`：重建 `agent_config` + 存量 `auto_claim = 0` 一次性置 1、只改该列、返回真实行数、幂等 | 已实现 | `migrate._step_19_to_20` + `STEPS`；报告 1 行 = 库内实际 0→1 行；`updated_at` 与其它列逐行不变；重放空操作 |
| 裁决不动；补测试锁「team + `auto_claim = 1` 不认领」 | 已实现 | `orchestration.py` 无 diff；`test_auto_claim.py::test_new_task_in_a_team_channel_stays_unclaimed_even_with_the_switch_on` |
| 不回溯任务 | 已实现 | 迁移只触碰 `agent_config`；无任何 `task` 语句；既有未认领任务不变 |
| ADR（默认开启 + 自主频道 + 一次性回填、三个被否方案、代价）+ CONTEXT/README 同步 | 已实现 | `docs/adr/0027-*.md`；CONTEXT **认领**词条；README 的 schema 章节 / `agent_config` 行 / 认领小节 / 端点说明；无残留「缺省关闭」 |
| 测试六项（默认 true、PATCH 往返、v19 迁移、新插入默认、三条行为） | 已实现 | `test_auto_claim.py` 的默认/往返与三条行为；`test_schema_migration.py::test_v19_upgrade_flips_the_default_and_backfills_existing_auto_claim_rows`；受 flip 影响的既有用例显式关闭 `auto_claim` |

非目标逐条守住：未改 `claim_new_task` / `availability` / DM-team 唤醒面 / Studio 控件与文案 / 任意 `task`
行；迁移只做「重建 + 回填」。验收证据见 §1–§3。
