<!-- 本文件是 #90 / #91 合并交付的 Answer（逐条可复现读数）；两张票在 tracker 上保持原样。 -->

# Issue #90 / #91 Answer —— 迁移引擎与整条逐版链

> 票面：GitHub #90（②a 引擎与 `3 → 4`）、#91（②b 补齐八步与终态判据）；规格正本
> `app/specs/issue-87-spec.md`；ADR `docs/adr/0015-schema-migration-is-explicit-and-forward-only.md`。
> 用户 2026-10-16 定夺：两张票由**一次实施**交付（引擎与完整链不可分）。

| 项 | 值 |
| --- | --- |
| 分支 | `schema-migration-t90t91` |
| 提交 | `d24029a feat(schema): 迁移引擎与 3 → 12 整条逐版链（#90 #91）` |
| 本票 base | `a7d12f3`（`main`） |
| PR | [#98](https://github.com/whutlichao/alibot/pull/98)（base `main`，head `schema-migration-t90t91`） |
| PR body | `app/specs/issue-90-91-pr-body.md` |
| 证据 | `.scratch/evidence/`（原始输出）、`.scratch/readings.py`（读数脚本） |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app/alibot/migrate.py` | **新增**：逐版步骤注册表（`3→4`…`11→12`）、执行器、对齐兜底、整目录备份、单事务编排、append-only 审计台账、报告。 |
| `app/alibot/__main__.py` | `--migrate` 接受范围放宽为 `[3, SCHEMA_VERSION]`；新增退出码 `5`（迁移失败、已回滚）；`--migrate` 帮助文本与退出码注释同步。 |
| `app/alibot/db.py` | 拒绝正文改为终态口径（去掉「本版尚无可用迁移步骤」）；`STAMP_SCHEMA_VERSION_SQL` / `INSERT_WORKSPACE_SETTINGS_SQL` 抽为共享常量（`_initialize` 与迁移共用）。 |
| `app/alibot/repair.py` | 导出 `CHANNEL_SCOPE_MOVES`：`3 → 4` 那一步与 `--repair-targets` 共用**同一份**列映射。**行为不改**。 |
| `app/tests/test_schema_migration.py` | **新增**：28 条验收用例（CLI 真实子进程 + 磁盘真实库 fixture）。 |
| `app/tests/_migration_history.py` | **新增**：从 git 历史抽取的逐版 DDL 留档（含 `SOURCE_COMMITS` 对照，可复核）。 |
| `app/tests/test_migrate_cli.py`、`test_schema_version_gate.py`、`test_target_scope.py` | **同步 #89 的中间态断言**为终态口径（见 §5）。 |

**不做**（票面第 10 条）：链前形态步骤（#92）、`--dry-run`（#93）、放宽别名版本门（#94）、链连续性守卫测试（#95）。
**未 bump** `SCHEMA_VERSION`（审计落默认台账文件，不需要库内表）。
**未改** `--repair-targets` 行为、`BUSINESS_TABLES`、`app/alibot/studio/**`、`alibot-design-system/**`、
`docs/adr/**`、`app/specs/issue-22/24/35/37-*.md` 历史取证面。

---

## 1. #90 验收（逐条可复现读数）

读数文件：`.scratch/evidence/acceptance-readings.json`（由 `.venv/bin/python .scratch/readings.py` 生成）。

### 1.1 端到端：v3 库 ⇒ 0，且本代码的 app 能打开它

造 v3 形态库（频道 + 成员 + 已读 + 预算 + 消息 + `attachments/` 字节）跑
`python -m alibot --migrate --data-dir <目录>`：

| 读数 | 值 |
| --- | --- |
| 退出码 | `0` |
| 库里 `schema_version` | `12`（= `SCHEMA_VERSION`） |
| 本代码打开 | `Database(data_dir)` 构造成功、`list_channels() == ['ch_dev']`；`backend(data_dir)` 的 `GET /api/health` ⇒ 200 |
| 备份 | `e2e-v3-backup-3-to-12-20261016T170004Z` |
| 步骤清单 | 报告逐条列出 `3 → 4` … `11 → 12`（9 步），`3 → 4` 改动 **6 行**（影子行 1 + 成员 2 + 已读 2 + 预算 1），`6 → 7` 改动 **1 行**（`workspace_settings` 缺省行） |

### 1.2 影子行与新表按 `channel_id → target_id` 一一对应；三张旧表原样保留

| 表 | 迁移后 | 旧表 |
| --- | --- | --- |
| `target` | `[('ch_dev','channel','2026-10-14T09:00:02Z')]` | — |
| `target_member` | `[('ch_dev','owner'), ('ch_dev','mem_x')]` | `channel_member` 行数 2 → 2 |
| `target_read` | `[('owner','ch_dev',1), ('mem_x','ch_dev',2)]` | `channel_read` 行数 2 → 2 |
| `target_budget` | `[('ch_dev',1,3)]` | `channel_budget` `[('ch_dev',1,3)]`，行数 1 → 1 |

**已耗尽预算的 `used = 3` 未被归零**（`target_budget` 与旧表都是 `3`）。四条 `INSERT OR IGNORE` 的
列名映射逐列显式写出（来自 `repair.CHANNEL_SCOPE_MOVES`），不用 `SELECT *`、不赌列顺序；影子行最先落。

### 1.3 备份落点与内容

成功后数据目录**同级**存在 `<数据目录>-backup-<from>-to-<to>-<UTC 时间戳>`；实测目录名
`backup-ok-backup-3-to-12-20261016T171053Z`，其中 `alibot.db` 与 `attachments/probe.bin` **同时在**。

### 1.4 备份失败即拒绝迁移

`chmod 0o500` 数据目录的父目录后跑迁移：**退出码 `5`**、库 sha256 **不变**、同级**没有任何**
`-backup-` 目录（`backups: []`）。不退化成「放行 + 一句警告」。

### 1.5 单事务与中断回滚

- v4 库 + 存量 DM 重复行（冲突在 `4 → 5` 之后的索引对齐处触发）：退出 `5`，**逐项等于**动手前形态
  ——`task` 表不存在、`message` 无 `task_ref` 列、版本仍是 `4`、`target` 行数不变。
  「新表建了但数据没搬」的中间态不可达。
- 库被另一连接 `BEGIN IMMEDIATE` 占住：退出 `5`（不是未捕获堆栈），库 SHA 不变，台账留
  `result=failed` 一条。

### 1.6 审计

迁移成功后数据目录内 `schema-migrations.jsonl` 恰好一条：
`{"from": 3, "to": 12, "started_at": "…", "finished_at": "…", "result": "success", "backup_path": "…"}`。
再跑一次（幂等、无迁移）不新增记录 ⇒ append-only。**人类面无写入口**：`app/alibot/` 除 `migrate.py` /
`__main__.py` 外的全部源码里不出现台账文件名（用例直接扫源码树）。

### 1.7 报告

stdout 打实际执行的步骤清单（`3 → 4` … `11 → 12`）、每步改动的**数据行数**、本次备份落点，以及
本次共改动行数与审计台账路径。

---

## 2. #91 验收（逐条可复现读数）

### 2.1 接受范围 = `[3, SCHEMA_VERSION]`

| 源版本 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 退出码 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 终态版本 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 |

未来版本（`SCHEMA_VERSION + 1`）：`--migrate` = `3`、`--repair-targets` = `3`、启动 = `3`，三条路径
stderr 逐字相同，库 sha256 不变。

### 2.2 终态判据 D7

把迁移库与**本代码初始化**的当前版本库逐项对照：

```json
{"missing_tables": [], "missing_indexes": [], "missing_implicit_indexes": [],
 "missing_triggers": [], "legacy_tables_kept": ["channel_member","channel_read","channel_budget"]}
```

- 表 / 列 / 索引（含 partial `idx_target_dm_peer_unique`）/ **隐式唯一约束**（`UNIQUE(target_id, seq)`
  等 autoindex）/ 触发器齐备。
- **外键约束也补齐**：`message.target_id` / `round_outcome.target_id` 的 `REFERENCES target(id)` 在 v3
  表上不存在、SQLite 又不能用 `ALTER TABLE ADD CONSTRAINT`，由对齐兜底**重建表**补上。行为取证：
  迁移库与本代码新建的库一样拒绝孤儿 `target_id` 的 message / round_outcome。
- 各业务表行数与迁移前一致（除新表与 `workspace_settings` 的 `id = 1` 缺省行）；三张旧表原样保留。

### 2.3 存量行取目标形态默认值

`theme = 'system'`、`auto_claim = 0`、`claimed_at = NULL`（列定义从 `SCHEMA` 解析出来，列级 `CHECK`
一起落库：`theme = 'neon'` / `auto_claim = 7` / `id = 2` 都被库级约束拒绝）。从 `10` 起步的链不经过
`6 → 7`，缺 `workspace_settings` 行的树由对齐兜底补上 `(1, 'zh-CN', 'comfortable', 'system')`。

### 2.4 `5 → 6` 存量冲突：显式拒绝

两个 `kind='dm'` target 指向同一 peer ⇒ 退出 `5`，stderr 含「冲突」与冲突明细；库逐项不变。

### 2.5 `10 → 11` 触发器替换（先删后建）

从 v9 树（带 `attachment_no_rebind`）与 v10 树（缺 `attachment`）迁移后，`sqlite_master` 的触发器
清单里**没有** `attachment_no_rebind`，且有 `attachment_metadata_immutable` /
`attachment_binding_is_immutable` / `attachment_no_delete`。新旧并存不可能。

### 2.6 缺表的 v10 树与「从未作为 main 读数」的 8 / 9

- v10 fixture 本来缺 `reminder`、缺 `attachment`；迁移后终态与新建库一致（对齐吸收读数歧义）。
- 8 / 9 两个号段（`main` 从未有过这两个读数）的树都能沿链推进、链上无断点（§2.1 表）。

---

## 3. 质量命令读数

| 命令（仓库根） | 读数 |
| --- | --- |
| `.venv/bin/python -m pytest`（**原样** env） | `4 failed, 410 passed, 1 skipped`（415 collected）。4 条全是 `test_agent_seam_client_lifecycle.py` 的**已知环境噪声**（本机 `no_proxy`/`NO_PROXY` 注入带方括号的 `[::1]` ⇒ `InvalidURL: Invalid port: ':1]'`） |
| 同上，`env -u no_proxy -u NO_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` | `414 passed, 1 skipped`（0 failed） |
| **本票 base 基线**（`main @ a7d12f3`） | `393 collected`（同样 4 条环境噪声红、388 passed、1 skipped） |
| `.venv/bin/ruff check app` | 全过 |
| `.venv/bin/ruff format --check app` | `88 files already formatted` |
| `.venv/bin/python -m mypy --cache-dir=/tmp/mypy-t90t91` | `Success: no issues found in 45 source files` |
| `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | `0 errors, 0 warnings, 0 informations` |
| `node --test app/tests/*.test.cjs`（4 个，含 README 未登记的 `mobile_shell`） | agent_detail 11 / i18n 43 / mobile_shell 9 / search 13，`fail 0` |

---

## 4. 红 → 绿

- **红**：在基线 worktree（`git worktree add --detach … a7d12f3`，拷入本票的测试文件）上跑
  `app/tests/test_schema_migration.py` ⇒ **27/28 红**（`.scratch/evidence/red-baseline.txt`）。
  典型读数：v3 库跑 `--migrate` ⇒ **退出码 3**、stderr 里是旧句「本版尚无可用迁移步骤，实际可迁移
  范围为空」、库里版本仍是 3。唯一在基线也绿的是「未来版本三条入口同一答复」——它断言的是**未改变**
  的拒绝语义。
- **绿**：本票代码下同一文件 **28/28 绿**；`3…11` 每个源版本 ⇒ `0` 且终态 = 新建库。

---

## 5. 双轴 code-review（固定点 = 本票 base `a7d12f3`）

按 `code-review` skill 跑 Standards 与 Spec 两轴（并行子代理），结果与**每条 finding 的处理**如下。

### 5.1 Standards 轴

| # | finding | 处理 |
| --- | --- | --- |
| S-1 | f-string 直插 `execute`（`PRAGMA table_info("…")`、`ALTER TABLE "…"`、`DROP TRIGGER "…"`），与仓库「SQL 作模块级常量、不把拼接写进 execute」体例冲突 | **改了**：查列名改用表值函数 `SELECT name FROM pragma_table_info(:table)`（真参数化）；DDL 提为模块级模板常量（`ADD_COLUMN_SQL` / `DROP_TRIGGER_SQL` / …），拼接文本只来自内部 `SCHEMA` 解析结果，并写明理由 |
| S-2 | `STAMP_SCHEMA_VERSION_SQL` / `INSERT_WORKSPACE_SETTINGS_SQL` 与 `db.py::_initialize` 逐字重复 | **改了**：两条语句抽到 `db.py` 作公开常量，`_initialize` 与迁移共用同一份 |
| S-3 | `_CHANNEL_SCOPE_MOVES` 是「目标表 ← 旧表 ← 语句」的第三份配对（`repair` 已有两份） | **改了**：配对收敛为 `repair.CHANNEL_SCOPE_MOVES`，`repair` 与 `3 → 4` 共用；`repair._REPAIR_ORDER` / `_REPAIR_SQL` 由它派生 |
| S-4 | 步骤函数转发对齐、执行器再对齐一遍（Middle Man 气味） | **不改**（理由）：规格 D6 明确要求「所有步骤共享同一条对齐兜底」，步骤自带的显式改动是 D6 逐条库级事实的落点；两者保留可让 #92 的插入点与 #95 的守卫有明确形状 |
| S-5 | `_CHANNEL_SCOPE_MOVES` 第一列解包后未使用 | **改了**（随 S-3）：改为直接用 `repair.CHANNEL_SCOPE_MOVES`，注释写明三列语义 |
| S-6 | `append_audit_record` 8 个位置参数、字段成簇（Data Clumps） | **不改**（理由）：单调用点、参数即台账记录字段本身；抽类型只增一层间接，判断性气味 |
| S-7 | `plan_steps`/`run_chain` 的 `to_version` 与 `backup_directory_name` 的 `moment` 是投机性泛化 | **部分改了**：删掉 `backup_directory_name` 的 `moment`；保留 `to_version`——票面第 10 条明确要求给 #93（库副本上跑链）与 #92 留挂点 |
| S-8 | 模块级 `REFERENCE` 名字不揭示含义 | **改了**：重命名为 `CURRENT_SCHEMA_SHAPE` |
| S-9 | 拒绝正文说「本版支持的源版本范围 3..12」，而启动 / `--repair-targets` 仍拒这段区间 | **改了**：正文补「启动路径与 `--repair-targets` 仍按本规则拒绝」，消掉表面矛盾 |
| S-10 | `_migration_history.py` 声称来自 `.scratch/gen_history.py`（被 gitignore、未入库），无法复现 | **改了**：docstring 改为**自足**取证说明（抽取规则 + `SOURCE_COMMITS` commit↔版本对照表），不再指向仓库外脚本 |
| S-11 | `VERSION_COLUMNS` 里混进 `"('replied',"` 这类生成器按逗号切列的**死数据** | **改了**：从生成物移除 `VERSION_COLUMNS`；测试改用显式的 4 条 `_COLUMN_ADDITIONS` |
| S-12 | 测试 `_COLUMN_DDL` 是历史列 DDL 的第二份手抄（Shotgun Surgery） | **不改**（理由）：它是**造历史形态的夹具**，与实现判据（实现侧列定义从 `SCHEMA` 解析）本就该分开；已收敛为 4 条显式条目，且来源 commit 已登记在留档里 |
| S-13 | `CONTEXT.md` 未收「审计台账」词条、且「数据目录」词条未提它 | **不改（记为遗留）**：本票授权面不含 `CONTEXT.md`（票面 Constraints 只列源码与测试）；台账是数据目录的第三个成员，已登记为后续文档票输入 |

### 5.2 Spec 轴

| # | finding | 处理 |
| --- | --- | --- |
| P-1 | `BEGIN IMMEDIATE` 落在内层 `try` 之外 ⇒ 拿不到写锁时抛未捕获的 `sqlite3.OperationalError`（rc=1），违反 D2「迁移失败 ⇒ 5」 | **改了**：`BEGIN` 收进同一个 `try`，任何失败都 ⇒ `MigrationFailed` ⇒ 5；新增「锁定库 ⇒ 5 + 库不变 + 失败审计」用例 |
| P-2 | 链前形态步骤缺失（#37 半改名库 `stored=4` 会 rc=0 但不搬 `channel_*` 数据） | **不改（#92 范围）**：票面第 10 条明确「本票**不**实现链前形态步骤（#92）」。已记为 §7 遗留，并确认终态判据不依赖它（健康 v4+ 库的数据本就在 `target_*`） |
| P-3 | 对齐兜底不补「目标形态的默认行」：从 ≥7 起步、缺 `workspace_settings` 行的树迁移后表是空的 | **改了**：新增 `_ensure_workspace_settings_row`（幂等 `ON CONFLICT(id) DO NOTHING`）+ 用例 |
| P-4 | `PRAGMA busy_timeout = 5000` 是规格没要求的（scope creep） | **不改**（理由）：与 `db.Database._connect` 既有口径一致；P-1 的锁定库用例证明它把并发场景变成可读的失败而不是立刻崩 |
| P-5 | 报告多打「审计记录：<path>」（规格只要步骤 / 行数 / 备份落点） | **不改**（理由）：审计是 D8 契约的一部分，报告给出落点让用户可核对，属增量信息而非行为偏差 |
| P-6 | `plan_steps` / `run_chain` 的公开挂点是规格没要求的 | **不改**（理由）：票面第 10 条**明确要求**为 #92 / #93 留接口并说明挂点 |
| P-7 | `test_target_scope.py` 删掉 `assert not list(rglob("migrate*.py"))`，放宽既有断言 | **不改**（理由）：那是 #89 钉住「尚无迁移脚本」的**中间态断言**，票面要求同步改写为终态口径；`migrations/` 目录断言保留 |
| P-8 | 表级外键整体没落地：`message` / `round_outcome` 永不被重建，缺 `REFERENCES`；D7「与 SCHEMA 文本一致」不成立 | **改了**：新增「缺外键的既有表按当前 `SCHEMA` **重建**」的对齐步骤（`PRAGMA defer_foreign_keys` + 临时表 + 保真复制 + RENAME），并加行为用例（孤儿 `target_id` 被拒）；终态对照同时纳入**隐式唯一约束** |

---

## 6. 规格 D6 漂移与后续票挂点

- **D6 漂移：无。** 审计落**默认方案**（数据目录内的 append-only 台账文件 `schema-migrations.jsonl`），
  因此**没有**建库内表、**没有** bump `SCHEMA_VERSION`、**没有** `12 → 13` 这一步。
- **#92 接进来要动的地方**：在 `migrate.STEPS` 之前插入一个不依赖版本号的形态步骤（判定「`channel`
  行在，而 channel 型影子 / 成员 / 已读 / 预算有缺」即执行 `CHANNEL_SCOPE_MOVES` 的四条搬迁，幂等）；
  它复用 `repair.CHANNEL_SCOPE_MOVES` 与同一条对齐兜底，不需要动备份 / 事务 / 审计 / 报告。
- **#93 接进来要动的地方**：在**数据目录内的临时副本**（`alibot.db` + `-wal`/`-shm`）上 `connect()`
  后直接调 `run_chain(conn, from_version)` —— 它不碰备份、审计、退出码与 CLI 参数；预演报告直接消费
  返回的 `ChainResult.steps`（含每步 `changed_rows`）。
- **#95 接进来要动的地方**：`migrate.STEPS` 已是一条连续注册表，守卫测试断言 `from` 严格 +1、唯一、
  覆盖 `MIN_SUPPORTED_SOURCE_VERSION .. SCHEMA_VERSION` 即可（`plan_steps` 已把「不连续」变成异常）。

---

## 7. 遗留 / 明确不做

1. **#92 链前形态步骤未实现**（票面第 10 条）。后果：`schema_version` 已是 4、而 channel 型作用域数据
   还停在 `channel_*` 旧表名的 **#37 半改名库**，走 `--migrate` 会得到 `0` 但不会搬那批行。健康库不受
   影响（它们的 `target_*` 数据本就在）。`--repair-targets` 本次**未放宽版本门**（#94），因此它对这类库
   仍按退出码 `3` 拒绝——两条入口在这个形态上的答复差异是 #92 / #94 的交付面，本票不擅自扩 scope。
2. **`CONTEXT.md` 未新增「审计台账」词条**（本票授权面不含它），已按 `docs/agents/domain.md` 登记为后续
   文档票输入。
3. **`--migrate --dry-run`（#93）、别名放宽（#94）、链连续性守卫测试（#95）** 均未实现。

---

## 8. 交付物回填

| 项 | 值 |
| --- | --- |
| 分支推送 | `origin/schema-migration-t90t91`（`9343e9f` + 本表回填提交） |
| PR | [#98](https://github.com/whutlichao/alibot/pull/98) —— title `feat(schema): 迁移引擎与 3 → 12 整条逐版链（#90 #91）`，body = `app/specs/issue-90-91-pr-body.md`，base `main` |
| `gh issue edit 90 --add-assignee @me` / `91` | 已执行（两张票 assignee 已加） |
| Answer 摘要 comment | 已 comment 到 [#90](https://github.com/whutlichao/alibot/issues/90) 与 [#91](https://github.com/whutlichao/alibot/issues/91) |

（本节即 Answer 的收尾记录；PR 号与正文路径已回填，两张票在 tracker 上保持原样。）
