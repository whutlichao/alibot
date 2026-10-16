<!-- 本文件是 #92 的 Answer（逐条可复现读数）；tracker 上的 issue #92 保持原样，摘要回填为 comment。 -->

# Issue #92 Answer —— 链前的形态步骤（不依赖版本号地补齐半改名的四张表）

> 票面：GitHub #92（③ 实施票：形态步骤）；规格正本 `app/specs/issue-87-spec.md`（S12 / D5 / D2 / D11）；
> ADR `docs/adr/0015-schema-migration-is-explicit-and-forward-only.md`。

| 项 | 值 |
| --- | --- |
| 分支 | `schema-migration-t92` |
| 提交 | `feat(schema): 链前形态步骤补齐半改名库的四张表（#92）` |
| 本票 base | `9f3b008`（`main`） |
| PR | 见 §7（`--body-file` 用 `app/specs/issue-92-pr-body.md`） |
| PR body | `app/specs/issue-92-pr-body.md` |
| 证据 | `.scratch/red-baseline.txt`、`.scratch/green-fixed.txt`、`.scratch/shape-step-report.txt`、`.scratch/pytest-cleared.txt`、`.scratch/pytest-raw.txt` |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app/alibot/repair.py` | 把四条**正向探测**抽成公开常量 `CHANNEL_SCOPE_GAP_CHECKS`（label + 来源/目标表 + 语句），`_CONSISTENCY_CHECKS` / `_CHECK_SQL` 由它 + 反向两条经 `_ALL_CONSISTENCY_CHECKS` **派生**（同一份 SQL，不写第二份）；新增只读探针 `channel_scope_gaps(conn, existing)` 与 `channel_scope_gaps_of(database)`（把 `_conn` 访问收在本模块，与 `repair_target_rows` 同一先例）；docstring 记明形态步骤与 `--repair-targets` 共用同一份列映射与同一份探针。**版本门未动。** |
| `app/alibot/migrate.py` | 新增 `run_shape_step(conn, source_version)`（有缺才搬 + 随后的共享对齐）；`run_chain` 把它作为**不可省前置**在 `STEPS` 之前调用；`render_report` 对不推进版本号的形态步骤不再打「4 → 4」。四张表一次补齐、影子行最先落——复用 `repair.CHANNEL_SCOPE_MOVES` 与 `_step_3_to_4`（与逐版链 `3 → 4` 同一段代码）。 |
| `app/alibot/__main__.py` | `_run_migrate`：版本已是 `SCHEMA_VERSION`、`missing == ()` 时，先用**只读**探针问一句「形态有没有缺」（`repair.channel_scope_gaps_of`）；有缺 ⇒ 走**同一条**迁移编排（备份 + 单事务 + 审计 + 报告），链为空；无缺 ⇒ 仍是「无事可做、不建备份」。缺表 ⇒ `4` 的既有分支**先于**探针。 |
| `app/tests/test_shape_conditioned_step.py` | **新增 14 条**验收（真实子进程 `python -m alibot --migrate` + 磁盘真实库 fixture，`_migration_history` 逐版 DDL 留档 + 手工钉版本号；断言只落退出码 / stdout / 库终态 / sha256）。 |

**不做**（票面 Constraints）：不改链上九步、不 bump `SCHEMA_VERSION`、不实现 `--dry-run`（#93）、
不放宽 `--repair-targets` 的版本门（#94）、不新增退出码（`0/3/4/5` 语义不变）。
**未改**：`app/alibot/db.py`、`BUSINESS_TABLES`、三张旧表（不删、不双写）、`docs/adr/**`、`CONTEXT.md`、
`app/specs/issue-22/24/35/37-*.md` 历史取证面、并行票 #95 的测试文件。

---

## 1. 验收逐条（票面 + Observable acceptance）

### 1.1 半改名库端到端（票面验收 1；Observable 1）

Fixture：`_migration_history` 的 v4 对象集（`target*` 表在但**全空**）+ 三张旧表，旧表里灌入频道
**ch_dev**、成员 2 行、已读 2 行、预算 1 行（`used = BUDGET_LIMIT = 3`）、消息 2 条，
`schema_version` 钉成 **4**。跑 `python -m alibot --migrate --data-dir <目录>`：

| 读数 | 值 |
| --- | --- |
| 退出码 | `0` |
| 库里 `schema_version` | `12`（= `SCHEMA_VERSION`） |
| `target` | `[('ch_dev', 'channel')]` |
| `target_member` | `[('ch_dev','mem_x'), ('ch_dev','owner')]` |
| `target_read` | `[('mem_x','ch_dev',2), ('owner','ch_dev',1)]` |
| `target_budget` | `[('ch_dev', 1, 3)]` |
| 三张旧表 | 行数 > 0、原样保留（回退面） |
| 本代码 app 能打开 | `Database(data_dir)` 构造成功；`list_channels() == ['ch_dev']`；`GET /api/health` 200；`GET /api/channels/ch_dev/messages` ⇒ `msg_1` / `msg_2`；`GET /api/channels/ch_dev/budget` ⇒ `200` 且 `used = 3` |

报告（`.scratch/shape-step-report.txt`）逐条列出形态步骤与九步链：

```text
源版本：4 → 目标版本：12
  形态步骤（channel 型作用域数据补齐：影子行 / 成员 / 已读 / 预算）（不推进版本号）  改动 6 行
  4 → 5 … 11 → 12  各 0 行
迁移完成：schema_version = 12；本次共改动 6 行。
```

### 1.2 预算保真（票面验收 2；Observable 2）

两条用例都钉 `target_budget.used == BUDGET_LIMIT`：

- 四张表全缺：迁移后 `[('ch_dev', 1, 3)]`；`budget_state('ch_dev')` ⇒ `used = 3`、`remaining = 0`；
  HTTP `GET /api/channels/ch_dev/budget` ⇒ `200` 且 `used = 3`。
- **影子行已在、只缺成员 / 已读 / 预算**（最锋利的一刀）：迁移后 `target_budget == [('ch_dev', 1, 3)]`。
  基线代码上这条 ⇒ 退出码 `0` 但 `target_budget == []`（即 `GET /api/channels/{id}/budget` 从 404
  变成「200 且 `used` 归零」，见 §2 红读数）。

### 1.3 幂等（票面验收 / Observable 3）

补齐后的库再跑一次：退出 `0`、stdout 含「无事可做」、库 sha256 不变、`target_*` 数据不变、
同级 `-backup-` 目录数**不增**。

### 1.4 健康库不受影响（Observable 4）

当前版健康库（`GET /api/channels` 建的库，并把三张旧表**镜像成与 `target_*` 一致的回退面**）跑
`--migrate` ⇒ 仍 `0`、「无事可做」、sha256 不变、**不建备份**。这条同时证明「有旧表名 ≠ 有缺」：
判据是逐行比对旧表 → 新表，不是「看到旧表就搬」。

### 1.5 判定只看形态、不看版本号（票面验收 3）

- 读数已是 `12`、六张必须存在的表都在、但 channel 型作用域行还留在旧表名里 ⇒ `--migrate` 同样补齐
  （报告出现形态步骤），**不是**「无事可做」的 `0`；再跑一次才是真正的无事可做。
- `stored` 取 **5…11** 的每一版（同样的「号已到位、数据没搬」形态，7 条参数化用例）⇒ 都退出 `0`、
  数据进 `target_*`、版本推进到 `SCHEMA_VERSION`。

### 1.6 不误伤 dm / task 型作用域（票面验收 4）

同一形态的库里加一个 `kind='dm'` 的 target（含它的 `target_member` / `target_read` /
`target_budget` 行）⇒ 迁移后这四行**逐字不变**。搬迁只从 `channel*` 旧表出发，dm / task 型 target
没有来源表，因此没有误伤路径。

### 1.7 缺表的库仍走 `4`（票面验收 5）

当前版库删掉 `task` 表（有业务数据）⇒ 仍退出 `4`、sha256 不变、不建备份。`missing` 的分支**先于**
形态探针，形态步骤不会把它当成可补齐对象。

---

## 2. 红 → 绿（Observable 5）

同一份 `app/tests/test_shape_conditioned_step.py` 在两个 worktree 上跑：

| 侧 | 命令 | 读数 |
| --- | --- | --- |
| **红**（`/tmp/alibot-t92-base`，detached @ `9f3b008`） | `.venv/bin/python -m pytest app/tests/test_shape_conditioned_step.py -v` | **11 failed / 3 passed**（`.scratch/red-baseline.txt`） |
| **绿**（本 worktree） | 同上 | **14 passed**（`.scratch/green-fixed.txt`） |

红侧的三种失败形态（**都是缺失的行为**，不是测试写错）：

1. `stored=4`…`11`、`target*` 全空：退出码 **5**、`FOREIGN KEY constraint failed`、库停在原版本
   （链在把空的 `target` 接上 `message` / `round_outcome` 外键时回滚）——链跳过 `3 → 4` 的直接后果。
2. 影子行在、成员 / 已读 / 预算缺：退出码 **0** 但 `target_budget == []`
   （`assert [] == [('ch_dev', 1, 3)]`）——沉默抹掉一个已耗尽的预算。
3. 读数已是 `12`、表都在、影子行缺：打印「已是本代码的 schema 形态……无事可做」、**不搬**。

基线通过的 3 条是**本来就正确的边界**：健康库无事可做、dm 行不被误伤、缺必需表仍 `4`。

---

## 3. 质量命令（Observable 6）

| 命令 | 读数 |
| --- | --- |
| `.venv/bin/python -m pytest`（本机 `no_proxy`/`NO_PROXY` 原样） | **4 failed / 424 passed / 1 skipped** —— 4 条是 `test_agent_seam_client_lifecycle.py` 的**已知环境噪声**（带方括号 `[::1]` 的代理变量），基线同 4 红。`.scratch/pytest-raw.txt` |
| `env -u no_proxy -u NO_PROXY .venv/bin/python -m pytest` | **428 passed / 1 skipped**。`.scratch/pytest-cleared.txt` |
| 基线（本票 base `main @ 9f3b008`）清代理 env | **414 passed / 1 skipped**（本票新增 14 条 ⇒ 414 + 14 = 428） |
| `.venv/bin/ruff check app` | `All checks passed!` |
| `.venv/bin/ruff format --check app` | `95 files already formatted` |
| `.venv/bin/python -m mypy --cache-dir=/tmp/t92-mypy` | `Success: no issues found in 46 source files` |
| `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | `0 errors, 0 warnings, 0 informations` |
| `node --test app/tests/{agent_detail_ui,i18n_ui,mobile_shell,search_ui}.test.cjs` | 全过：`19 / 43 / 9 / 13`，`fail 0` |

---

## 4. 双轴 code-review（Observable 7）

固定点 = 本票 base `9f3b008`；`git diff 9f3b008...HEAD`（两轴并行、各自独立成文）。两轴都**未发现**
硬性违反票面 Constraints 的行为（未改链上九步 / 未 bump 版本 / 未实现 `--dry-run` / 未动别名版本门 /
退出码集合仍 `{0,3,4,5}`），findings 与逐条处理如下。

### 4.1 Standards 轴

| # | Finding | 处理结果 |
| --- | --- | --- |
| S1 | `__main__` 取 `database._conn`（私有），把私有连接访问扩散到第二个模块 | **已修**：探针移入 `repair.channel_scope_gaps(conn, existing)` + `repair.channel_scope_gaps_of(database)`；`_conn` 访问收回 `repair.py`（本就有 `repair_target_rows` 先例），CLI 层只用公开面。 |
| S2 | `has_business_data = database.has_business_data() if missing else False`——读取与分支耦合 | **已修**：改为 `bool(missing) and database.has_business_data()`。 |
| S3 | `CHANNEL_SCOPE_GAP_CHECKS` 上方注释说「语句本体在下面按标签索引的字典里」，实际内联在元组 | **已修**：注释改为「内联在元组里，喂给 `execute` 的一律是模块级常量」。 |
| S4 | `_CONSISTENCY_CHECKS` 与 `_CHECK_SQL` 各自重复展开 `(*GAP, *REVERSE)` | **已修**：抽 `_ALL_CONSISTENCY_CHECKS`，两处从它投影。 |
| S5 | 新测试与 `test_schema_migration.py` 的 helper 重复（`_COLUMN_DDL` / `_COLUMN_ADDITIONS` / `_run` / `_sha256` / `_backup_*` / `_connect`） | **部分处置 + 判断项**：删掉重复的 `_COLUMN_DDL` / `_COLUMN_ADDITIONS`（只保留 `dm_peer_member_id` 一条，它是唯一被索引依赖的列）；`_run` / `_migrate_argv` / `_sha256` / `_backup_siblings` / `_connect` 保留文件内——与同族 `test_migrate_cli.py` 同体例（本仓每个特性测试文件自带 CLI 小工具），跨测试模块 import 私有 helper 更脆。 |
| S6 | `LEGACY_TABLES` 与 `LEGACY_OBJECTS` 取值完全相同、同概念两名 | **已修**：合并为 `LEGACY_TABLES`。 |
| S7 | `_row(conn, f'SELECT COUNT(*) FROM "{table}"')` 把表名插进 `execute` 实参 | **已修**：改用模块级 `_LEGACY_COUNT_SQL` 字典（同 `test_schema_migration._ROW_COUNT_SQL` 体例）。 |
| S8 | `run_shape_step` 复用 `StepReport`（`from == to`），报告打「4 → 4」语义不诚实 | **已修（可观察部分）+ 判断项**：`render_report` 对 `from == to` 的步骤改打「…（不推进版本号）」（逐版步一律 `from + 1 == to`，由 `_assert_chain_is_continuous` 守着，不会误落）；仍复用 `StepReport`——为 #93 另造报告类型属 Speculative Generality。 |

### 4.2 Spec 轴

| # | Finding | 处理结果 |
| --- | --- | --- |
| P1 | 别名（`--repair-targets`）不调用形态步骤，仍走自己的补齐循环与 `== SCHEMA_VERSION` 闸门 | **按期延后（#94）**：票面明确把别名版本门与接线留给 #94；本票交付**可复用件**并在 §5 记明复用点。补一句事实：`stored == SCHEMA_VERSION` 的半改名库**今天已能被** `--repair-targets` 的既有补齐循环处置（那正是 #37 修复），缺的只是 `3..11` 区间——正是 #94 的版本门放宽。 |
| P2 | 未断言「消息面恢复」（S12 观察点含消息 / 未读） | **已修**：stored=4 用例补 `GET /api/channels/ch_dev/messages` ⇒ `msg_1` / `msg_2` 两条恢复（fixture 同步补第二条消息）。 |
| P3 | 版本无关性只在 `4` 与 `SCHEMA_VERSION` 取证，`5..11` 无用例 | **已修**：新增 `stored=5..11` 逐版参数化用例 7 条（都 ⇒ `0`、数据进 `target_*`、版本到 `SCHEMA_VERSION`）。 |
| P4 | `run_shape_step` 额外跑终态对齐，可能把「无事可做 `0`」翻成「`5`」（current 版本库 + 未搬 channel 行 + 重复 DM 行） | **接受为设计后果并记录**：D7 要求成功的迁移达到当前 `SCHEMA` 终态，而重复 DM 行本来就使 `idx_target_dm_peer_unique` 建不上——迁移引擎的既有契约就是「显式拒绝升级」（`StepError` ⇒ `5`，已回滚）。相比改前对一本**坏库**给静默 `0`，`5` 更诚实。未为这个边角新增用例（免越 scope）。 |
| P5 | 报告行数不实：形态步骤自己对齐插入的 `workspace_settings` 缺省行不计入，`6 → 7` 报 0 | **判断项（非本票引入）**：在 base `9f3b008` 上实测 v3 → 12 也报「本次共改动 6 行」而实际插入 7 行——对齐插入的默认行不归属任何版本步，是 #90 / #91 的既有记账口径。本票未改它，票面也未要求。 |
| P6 | `channel_scope_gaps` 只按「行存在性」判缺，不做值校验（`used` 错值也算无缺） | **判断项**：规格 D5 的判据就是「channel 型作用域**行**有缺」；预算保真是对**搬迁**的要求（`INSERT OR IGNORE … SELECT used` 原样搬），已由 `target_budget.used == BUDGET_LIMIT` 用例钉住。 |
| P7 | 测试直接构造 `Database` 读 `list_channels` / `budget_state`，即 T2 列为「被否的第二接缝」者 | **保留（轻微偏离，已声明）**：票面 Observable 1 明确要求「**本代码 app 能打开它**」，既有 prior art `test_schema_migration.test_v3_library_migrates_to_terminal_state_and_the_app_can_open_it` 同样用 `Database(data_dir)`；CLI 真子进程仍是本文件的主接缝，`Database` 只用于「能否打开 / 读数」这一条验收。 |

---

## 5. 边界与「留给后续票的挂点」

- **#93（`--dry-run`）**：预演在库副本上跑 `run_chain`，自然包含形态步骤（`run_chain` 现在是
  「形态步骤 + 逐版链」）。报告里形态步骤与逐版步骤并列，且不打假版本对。
- **#94（放宽 `--repair-targets` 版本门）复用点**：形态步骤判定 = `repair.channel_scope_gaps` /
  `repair.channel_scope_gaps_of`；搬运 = `migrate.run_shape_step(conn, from)` →
  `migrate._step_3_to_4`（与逐版链 `3 → 4` 同一段代码）+ `repair.CHANNEL_SCOPE_MOVES` 的同一份列
  映射。别名将来要么直接调 `run_shape_step`，要么（推荐）走 `migrate.migrate_data_dir` 的同一编排
  ⇒「同一入口、同一条链、同一退出码」。**本票未触碰 `_run_repair` 与它的版本门。**
- **`4` 与形态步骤互补**：`4` = 「版本已是本版本、缺当前 `SCHEMA` 必须存在的表、且有业务数据」，
  发生在任何写入之前；形态步骤处置「表都在、但 channel 型作用域**行**有缺」。缺表时前者优先。
- **只读探测**：`channel_scope_gaps` 全是 `SELECT COUNT(*)`，所以「版本已是 12 但有没有活」可以在
  决定备份**之前**问清楚——健康库因此仍然零字节改动、零备份。
