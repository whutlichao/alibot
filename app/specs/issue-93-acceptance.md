<!-- 本文件是 #93 的 Answer（逐条可复现读数）；tracker 上的 issue #93 保持原样，摘要回填为 comment。 -->

# Issue #93 Answer —— 只读预演 `--migrate --dry-run`（临时副本上跑完整链 + 逐步行数 + 原库 sha256 前后一致）

> 票面：GitHub #93（④ 实施票：迁移预演）；规格正本 `app/specs/issue-87-spec.md`
> （**D10 / S5** 为承重判据，另涉 D2 / D3 / D4 / D5 / D9 / D12 与 T1 / T2）；
> ADR `docs/adr/0015-schema-migration-is-explicit-and-forward-only.md` 第一节的「预演」段。

| 项 | 值 |
| --- | --- |
| 分支 | `schema-migration-t93` |
| 提交 | `48cfdd1` feat（预演编排 + CLI）/ `2104689` test（13 条验收）/ `7cc8077` refactor（双轴 review 收敛）/ `df773ce` docs（PR 正文）/ 本文件 docs（Answer） |
| 本票 base | `82a6ffb`（`main`，即 #95 合并后的读数） |
| PR | [#101](https://github.com/whutlichao/alibot/pull/101)（base `main`，head `schema-migration-t93`） |
| PR body | `app/specs/issue-93-pr-body.md`（`--body-file` 用） |
| 新增验收 | `app/tests/test_migrate_dry_run.py`（13 条，真实子进程 + 磁盘真实库 fixture） |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app/alibot/migrate.py` | 新增只读预演一节：`StagedLibraryCopy`（副本落在**数据目录内**的隐藏临时子目录、复制 `alibot.db` 连同 `-wal` / `-shm`、`__exit__` 无条件删除并把副本的**三种去向**如实记进报告）、`dry_run_chain`（副本上 `BEGIN IMMEDIATE` → `run_chain` → `ROLLBACK`，副本侧任何失败都归 `MigrationFailed`）、`sha256_file`、`DryRunReport`、`render_dry_run_report` / `render_dry_run_untouched`；`_render_step_lines` 抽出，真跑与预演**共用同一份**步骤表渲染（真跑输出逐字未变）；`plan_steps` 补一条**运行时**「每步严格 +1」校验。 |
| `app/alibot/__main__.py` | 新增 `--dry-run` 旗标与 `_run_dry_run`；把只读裁决收成 `_LibraryVerdict` + `_inspect_library`（判据全部复用既有的闸门 / `missing_current_required_tables` / `channel_scope_gaps_of` / `has_business_data`），`_run_migrate` 与预演**共用同一份**；`_report_verdict` 把「哪一档打 stderr、给哪个退出码」收在一处。 |
| `app/tests/test_migrate_dry_run.py` | **新增 13 条**验收（T2 的唯一接缝：真实子进程 `python -m alibot --migrate --dry-run --data-dir <tmp 数据目录>`；只断言退出码 / stdout·stderr / 数据目录与库终态 / sha256）。 |

**不做**（票面 Constraints）：不改链上九步与形态步骤的行为；不 bump `SCHEMA_VERSION`；不放宽
`--repair-targets` 的版本门（#94）；不动 `CONTEXT.md`（#94）；不新增退出码；不改
`app/tests/test_migration_chain_guard.py` / `test_read_path_legacy_tables_guard.py`（#95 的守卫）。
**未改**：`app/alibot/db.py`、`repair.py`、`BUSINESS_TABLES`、三张旧表、`docs/adr/**`、
`CONTEXT.md`、`app/README.md`、历史取证面 `app/specs/issue-22/24/35/37-*.md`。

---

## 1. 人定裁定（`orca orchestration ask` 回信，逐字要点）

本票只有一条**未定项**够格提交coordinator，回信（原文）要点：

> 【人定】选 **(A) 视为 CLI 用法错误**：`--dry-run` 必须与 `--migrate` 同用；单独给时按 argparse
> 用法错误处理，退出码 **2**，**不进入 `{0,3,4,5}` 裁决集合**。……另确认：`--dry-run` 遇「库不被接受 /
> 形态不对」时**沿用真跑的 `3` / `4` 与**（与另两条路径）**逐字相同的正文**；只有「在副本上跑不通这条
> 链」才用 `5`，且 `5` 的正文与报告**必须写明原库一个字节都没动**（预演没有可回滚之物，所以措辞上
> 不要写「已回滚」，写「预演发现这条链跑不通；原库未改动」）。

落地：`__main__.py` 的 `parser.error("--dry-run 只能与 --migrate 同用：预演的就是迁移这条路径。")`
（退出 `2`）；`3` / `4` 的正文与真跑**逐字相同**（见 §2.9 的实测）；`5` 的正文与报告都写
「原库未改动」，全文**没有**「已回滚」（见 §2.6）。

## 1.1 规格输入（供将来 to-spec 补齐）

规格 **D1**（命令面）只定义 `python -m alibot --migrate [--data-dir X]`，**D10**（预演）只定义
`--migrate --dry-run`：**两者都没有定义「单独给 `--dry-run`」时该怎么办**。本票把它按上面的**人定
裁定 (A)** 落地（CLI 用法错误 ⇒ `2`），并在此登记为规格缺口——建议 D1 补一句「`--dry-run` 是
`--migrate` 的修饰旗标，单独出现即用法错误（退出码 `2`，不属于该入口的答复矩阵）」。

---

## 2. 验收逐条（票面 Observable acceptance 1–12）

### 2.1 原库 sha256 前后一致，且报告打出这两个读数（票面 1；D10 / S5）

v3 库（`tests/_migration_history.py` 的逐版 DDL 留档搭出）。`python -m alibot --migrate --dry-run --data-dir <目录>`：

| 读数 | 值 |
| --- | --- |
| 退出码 | `0` |
| 原库 `alibot.db` sha256（预演前） | `60712971481adaf7c203cd36ec894cbbedcea952de5b899b7ba6bb0d935fe544` |
| 原库 `alibot.db` sha256（预演后） | `60712971481adaf7c203cd36ec894cbbedcea952de5b899b7ba6bb0d935fe544`（一致） |
| 报告里出现的两个读数 | 同上两个（正则 `原库文件 sha256（预演[前后]）：([0-9a-f]{64})` 取到两个、且相等） |

实现要点：预演**从不**用 SQLite 打开原库（闸门、形态判定与整条链全在副本上），因此零字节改动是
**构造性**的，不是「打开之后希望它没写」。

### 2.2 不留临时副本（票面 2；S5）

| 数据目录条目 | 预演前 | 预演后 |
| --- | --- | --- |
| v3 库 | `['alibot.db', 'attachments']` | `['alibot.db', 'attachments']` |
| 以 `.alibot-dry-run-` 开头的条目 | 0 | 0 |
| `-wal` / `-shm` 残留 | 0 | 0 |

副本落在**数据目录内**的隐藏临时子目录（`tempfile.mkdtemp(dir=data_dir, prefix=".alibot-dry-run-")`），
`__exit__` 里 `try/finally` 无条件 `rmtree`；删不干净则**报错**并且**不让报告说「已删除」**（见 §3）。

### 2.3 不建备份、不写台账、不改版本、不搬数据（票面 3；D10）

| 读数 | 值 |
| --- | --- |
| 同级 `<数据目录>-backup-*` 目录 | 0 个 |
| 数据目录内 `schema-migrations.jsonl` | 不存在 |
| 库里 `schema_meta.schema_version` | 仍是 `3`（未推进） |
| 原库里 `target` 表 | 不存在（形态 / 逐版步骤**一行都没落**） |

### 2.4 报告内容：步骤清单 + 每步将改动的行数（票面 4；S5）

v3 库（形态步骤行数非零）：

```text
预演（只读，在数据目录内的临时副本上跑完整迁移路径）：/tmp/final93/v3/alibot.db
源版本：3 → 目标版本：12

将执行的步骤（每步将改动的数据行数）：
  形态步骤（channel 型作用域数据补齐：影子行 / 成员 / 已读 / 预算）（不推进版本号）  改动 6 行
  3 → 4  channel 型作用域数据搬迁（影子行 / 成员 / 已读 / 预算）  改动 0 行
  4 → 5  任务表与任务线程（task + message.task_ref + 触发器）  改动 0 行
  5 → 6  私信一对一唯一（dm_peer_member_id + 部分唯一索引）  改动 0 行
  6 → 7  工作区设置单行表（workspace_settings + id = 1 缺省行）  改动 0 行
  7 → 8  提醒表（reminder）  改动 0 行
  8 → 9  附件表 + 索引 + 触发器（attachment）  改动 0 行
  9 → 10  主题三档（workspace_settings.theme + 列级 CHECK）  改动 0 行
  10 → 11  附件触发器替换（先删 attachment_no_rebind，后建两条新触发器）  改动 0 行
  11 → 12  认领与 claimed_at（auto_claim / task.claimed_at）  改动 0 行
…
预演结论：这条链在副本上跑通了；本次共将改动 6 行。
```

`stored=4` 的半改名库（`test_shape_conditioned_step._build_half_renamed_library`）：同一形状，步骤表从
**4** 起步、形态步骤 **6 行**（非零）；预演后原库仍是半改名形态——`target` 里 **0** 行 channel 影子行、
`schema_version` 仍是 `4`、三张旧表行数 > 0。**报告说的是「将要搬」，原库一行都没搬。**

`3 → 4` 那一步报 0 行不是漏报：链前的形态步骤已经把该搬的行搬进**副本**了，`3 → 4` 在副本上自然
幂等（0 行）。这与真跑的读数**同形**（真跑同一库也是「形态步骤 6 行 + 3 → 4 0 行」）。

### 2.5 WAL / SHM 情形（票面 5；D10）

构造：v3 库 + 一条另开的 WAL 连接（`PRAGMA journal_mode = WAL` + `PRAGMA wal_autocheckpoint = 0`），
在**未 checkpoint** 的窗口里多写 1 行 `member` 与 1 行 `channel_member`——后者**只在 `-wal` 里**。

| 读数 | 值 |
| --- | --- |
| 构造后 `alibot.db-wal` / `alibot.db-shm` | 都在 |
| 预演报告的形态步骤行数 | **7 行**（`1 影子行 + 3 成员 + 2 已读 + 1 预算`） |
| 若只复制主文件（对照） | 只会是 **6 行**——这正是「只复制主文件会读到不完整状态」的可观察后果 |
| 预演后数据目录条目集合 | 与预演前**相同**（`-wal` / `-shm` 仍在，没有多出副本） |
| 原库主文件 sha256 | 预演前后一致 |

### 2.6 断链暴露（票面 6；S11 的可观察面）—— 红证据在**隔离副本**，不入库

**（a）注册表被改成历史 `9 → 11 → 12` 的形状**（隔离副本 `/tmp/break93`，导入后重写 `migrate.STEPS`：
去掉 `9 → 10`、把 `10 → 11` 的 `from` 改成落后号 `9`）⇒ 预演退出 **`5`**：

```text
[stderr] 预演发现这条链在副本上跑不通；原库未改动（预演没有可回滚之物，写操作只落在副本上）。
         原因：迁移链不连续：注册表里的 9 → 11 不是严格 +1 的一步（它跳过了 10）。
         迁移链要求每步严格 +1（规格 D5）。
[stderr] 预演未改动原库：…/alibot.db 的 sha256 前后一致（6071297148…fe544 → 6071297148…fe544）；
         原库一个字节都没动。
[stdout] 原库文件 sha256（预演前）：6071297148…fe544
[stdout] 原库文件 sha256（预演后）：6071297148…fe544
[stdout] 原库零字节改动：前后读数一致。
退出码 = 5；库仍是 version 3；数据目录仍是 ['alibot.db', 'attachments']
```

**（b）直接在源码里把某步 `from` 改成落后号** ⇒ 更早的**导入期**不变量
`_assert_chain_is_continuous()`（#90 / #91 带进来的，本 PR 未改）先抛并点名断点：
`RuntimeError: 迁移链不连续：期望 10 → 11，注册表给的是 9 → 11`（退出 `1`）。真跑路径同读数。

**（c）仓库里留下来的**是正向观察：`test_the_dry_run_report_lists_the_planned_steps_with_row_counts`
与 `test_the_dry_run_reports_the_shape_step_with_rows_for_v3_and_half_renamed` 把**预演报告的步骤表**
当数据解析，断言它「从库版本起、每步**严格 +1**、彼此衔接、最后一个 `to == SCHEMA_VERSION`」——
注册表被改坏时这份计划会断，测试即红（#95 的守卫在真跑接缝上是同一条判据）。

### 2.7 预演之后真跑仍正常（票面 7）

同一 v3 库：预演一次（`0`，原库 sha256 不变）→ 再真跑 `--migrate` ⇒

| 读数 | 值 |
| --- | --- |
| 退出码 | `0` |
| `schema_version` | `12`（= `SCHEMA_VERSION`） |
| `target` / `target_member` / `target_read` | `1` / `2` / `2` |
| `target_budget.used` | `3`（**已耗尽预算不归零**） |
| `message` | `2` |

### 2.8 红 → 绿（票面 8）

| 侧 | 读数 |
| --- | --- |
| base `82a6ffb`（`git archive` 出的隔离副本，把本票的测试文件放进去） | **13 failed / 0 passed**；子进程拿到 `alibot: error: unrecognized arguments: --dry-run`（退出 `2`） |
| 本分支（改动后） | **13 passed** |

### 2.9 与真跑同一个答复（D3 / D2；人定裁定的「逐字相同」）

未来版本库（`stored = SCHEMA_VERSION + 1`）：`--migrate` 与 `--migrate --dry-run` 的 **stderr 逐字相同**
（退出都 `3`、库 sha256 不变、预演不留副本）。实现里为此专门做了一步「按**原库**路径重渲染拒绝」——
预演读的是副本，若不重渲染，正文里的库路径与 `--migrate --data-dir <目录>` 指引会指向复制品，
「同一个库、同一个答复」当场被打破。`4`（版本匹配、缺当前形态必须存在的表且有业务数据）同样逐字相同。

### 2.10 质量命令全过（票面 9）

仓库根，全部在**冻结后**的代码上跑：

| 命令 | 读数 |
| --- | --- |
| `.venv/bin/python -m pytest`（**原样**） | **4 failed / 455 passed / 1 skipped**（4 条是本机 `no_proxy` / `NO_PROXY` 注入带方括号 `[::1]` 的**已知环境噪声**：`test_agent_seam_client_lifecycle.py` 的 4 条；基线同样 4 红，**未改这些测试**） |
| 清代理 env（`env -u no_proxy -u NO_PROXY -u http_proxy …`） | **459 passed / 1 skipped**（= 460 collected） |
| 基线对照 | `82a6ffb` 实测 **447 collected**（清代理 env 下 446 passed + 1 skipped）；本票新增 13 条 ⇒ **460 collected** ✓ |
| `.venv/bin/ruff check app` | All checks passed! |
| `.venv/bin/ruff format --check app` | 98 files already formatted |
| `.venv/bin/python -m mypy --cache-dir=/tmp/t93-mypy` | Success: no issues found in 49 source files |
| `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | 0 errors, 0 warnings, 0 informations |
| 4 个 `node --test app/tests/*.test.cjs` | 19 / 43 / 9 / 13 全 pass，fail 0 |

### 2.11 双轴 code-review（票面 10）—— 固定点 = `82a6ffb`，findings 与处理

两轴各自跑在独立 sub-agent 上，下面是**结论 + 每条 finding 的处理结果**。

**Standards 轴**（结论：无硬性违反文档标准；以下为判断项）

| # | Finding | 处理 |
| --- | --- | --- |
| S-1 | `_run_migrate` 与 `_run_dry_run` 对同一 `kind` 各写一遍 if 级联（Repeated Switches / Duplicated Code / Feature Envy） | **已改**：抽出 `_report_verdict(verdict) -> int \| None` 一处，两条路径共用；行为不变（消息与退出码逐字相同） |
| S-2 | `_LibraryVerdict.kind: str` 是 Primitive Obsession（五个裸字符串承载域概念） | **已改**：改用 `Literal["accepted", "shape-only", "nothing", "not-accepted", "shape-missing"]`，拼错在类型检查期被拦下 |
| S-3 | 空目录文案、「一个字节都没动」在多处重复（轻） | **部分采纳**：`DryRunError` / `untouched_note` / `_dry_run_proof_lines` 三处语境不同（异常正文、stderr 一行、报告行），抽成常量只会把句子拆得更碎——**保留**，理由记在此；空目录文案真跑与预演的首词（「迁移：」/「预演：」）本就不同，**保留** |
| S-4 | `StagedLibraryCopy` 与 `DryRunReport` 持有同一数据簇；`report()` 用 `None` 当布尔；digest 用裸 `str`（Data Clumps / Mysterious Name） | **部分采纳**：新增 `has_readings` 属性把「有没有读数」问清楚，`report()` 仍在没有读数时给 `None`（调用方不再把 `None` 当布尔用）；digest 保持裸 `str`——本仓没有 newtype 体例，为两个字符串引入一个类型属**Speculative Generality**，**不改** |
| S-5 | 测试里 `after == before` 之后的两条断言是冗余 | **已改**：把两条**具体**断言（副本前缀 / 边文件）提到前面（失败时信息更好读），`after == before` 移到最后当兜底；并在注释里写明蕴含关系 |

**Spec 轴**（结论）

| # | Finding | 处理 |
| --- | --- | --- |
| P-1 | 「断链会被预演暴露」没有可观察落点、也没有用例 | **已澄清 + 已补正向用例**：票面明确要求断链的**红证据不得留在仓库**，因此负例在隔离副本取证（§2.6(a)(b)）；仓库里留的是**正向**观察——预演报告的步骤表被当数据解析并断言「连续、严格 +1、覆盖到 `SCHEMA_VERSION`」（§2.6(c)）。另**修正**了 test 提交信息的措辞（原文写成仓库里有用例，不准确） |
| P-2 | 副本建不起来时「没有报告」，违反「`5` 的报告与正文都要写明原库未改动」 | **已改**：`__enter__` 的两条失败路径也实测一次原库 sha256（真实读数，不是推断），于是仍然打得出一份报告；报告里那行如实写「临时副本：没能建起来（预演没跑）」。对应新增用例：`test_dry_run_without_a_place_for_the_copy_still_proves_the_untouched_library` |
| P-3 | `plan_steps` 新增的运行时 `+1` 校验改了真跑共用的规划路径（scope creep） | **保留并说明**：它不新增任何行为，只把「注册表里出现跳号步骤时静默执行那一步」变成「规划期明确失败并指出断点」；这正是 D5「每步严格 +1」与票面验收 5「链有断点/落后号时报告（或退出码）要能暴露它」的落点，且对健康注册表零影响（链上九步与形态步骤的行为一个都没动） |
| P-4 | `3` / `4` 分支也往 stdout 打「预演未改动原库」报告，超出 ask（D3 只要求正文相同） | **保留并说明**：**正文（stderr）与真跑逐字相同**——答复的同一性没有被破坏；多出的 stdout 是「预演没碰原库」的**证据**（票面验收 1 要求报告里打出两个读数，且「原库零字节改动」这条纪律对拒绝路径同样成立）。若将来 to-spec 明确拒绝路径不打 stdout，删这一行即可 |
| P-5 | 副本删不掉时报告仍说「已删除」，与 stderr 自相矛盾 | **已改（真 bug）**：`DryRunReport` 增 `copy_removed`，报告按副本的**三种去向**（没建起来 / 已删除 / 没能删掉）分述；`cleanup()` 删不干净时 `copy_removed` 保持为假，报告写「**没能删掉**（数据目录里还留着它，见 stderr）」。开发期自查（隔离脚本把 `rmtree` 换成空操作）实测该分支输出正确 |
| P-6 | 副本 `connect` 失败逃逸成 `1`（`connect` 在 `try` 之外） | **已改**：`connect` 一并纳入映射，副本侧任何失败都归 `MigrationFailed` ⇒ `5` |

处理后的复核：`ruff check` / `ruff format --check` / `mypy` / `pyright` / 13 条新用例 / 8 个既有迁移相关
测试模块全绿（§2.10）。

---

## 3. 已知边界与遗留

- **预演与真跑之间有时间差**：副本不会被真跑复用（ADR-0015 的代价栏）。预演说「这条链能跑通」，
  不等于真跑那一瞬间不会因为库被别的进程占住而失败（真跑那条路径照旧给 `5`）。
- **副本的磁盘开销**：与库同量级（附件字节不复制——副本只含库文件与它的两个边文件）。
- **`-shm` 的复制**：SQLite 会校验 WAL 索引并自行重建，因此复制 `-shm` 是安全的；它的存在让构造
  「未 checkpoint」场景可复现，也让「只复制主文件会读到不完整状态」这条判据在测试里可观察。
- **不做**：不一致数据库的修复、备份清理策略、旧表删除入口、启动自动迁移、子命令、HTTP / UI 入口
  都不是本票范围（规格 Out of Scope）。
