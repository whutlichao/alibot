<!-- 本文件是 #94 的 Answer（逐条可复现读数）；tracker 上的 issue #94 保持原样，摘要回填为 comment。 -->

# Issue #94 Answer —— 弃用别名 `--repair-targets`：与 `--migrate` 同入口、同链、同结果、同退出码

> 票面：GitHub #94（⑤ 实施票：弃用别名）；规格正本 `app/specs/issue-87-spec.md`
> （**D11 / S13 / S14 / D0 的 `4`** 为承重判据，另涉 D2 / D14 与 T2 / T3 / T4）；
> ADR：`docs/adr/0015-schema-migration-is-explicit-and-forward-only.md` §五。
> 票面与规格冲突时以**规格**为准（本票无冲突）。

| 项 | 值 |
| --- | --- |
| 分支 | `schema-migration-t94` |
| 提交 | `3b4b800` feat（别名接线）/ `b584a28` test（14 条验收 + 旧口径注释同步）/ `1863497` docs（CONTEXT.md）/ `88b574c` refactor（双轴 review 收口）/ + 本文件与 PR 正文的 docs 提交 |
| 本票 base | `36873c7`（`main`，即 #93 合并后的读数） |
| PR | [#102](https://github.com/whutlichao/alibot/pull/102)（base `main`，head `schema-migration-t94`） |
| PR body | `app/specs/issue-94-pr-body.md`（`--body-file` 用） |
| 新增验收 | `app/tests/test_repair_targets_alias.py`（14 条：4 个具名 + 9 个范围参数 + 1 个 dry-run 等价） |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app/alibot/__main__.py` | `--repair-targets` 与 `--migrate` 收进**同一分支**（`is_migration = args.migrate or args.repair_targets`）：别名先打一句 `REPAIR_TARGETS_DEPRECATION_NOTICE`（**stdout**），随后与 `--migrate` 跑**同一个** `_run_migrate` / `_run_dry_run`。版本门随 `_run_migrate` 的 `_migratable_source_version` 一起放宽为 `[3, SCHEMA_VERSION]`。删掉已不可达的 `_run_repair`（原独立修复入口的 CLI 适配层）。 |
| `app/alibot/repair.py` | **仅注释**：模块定位从「当前 schema 库的作用域数据补齐入口」改为「补齐逻辑与迁移侧的共用来源」——记明别名自 #94 起走 `__main__` 的同一分支、版本门放宽为 `[3, SCHEMA_VERSION]`，`repair_target_rows` 是保留的 #37 补齐原语（CLI 已无自己的入口，删除时机随别名下线）。**行为与数据一字未改**（`CHANNEL_SCOPE_MOVES` / `CHANNEL_SCOPE_GAP_CHECKS` / `channel_scope_gaps*` / `missing_current_required_tables` 都是既有落点）。 |
| `app/alibot/migrate.py` | **未改**（本票的「仅复用点」，无需改动）。 |
| `app/tests/test_repair_targets_alias.py` | **新增 14 条**（T2 的唯一接缝：真实子进程 `python -m alibot --repair-targets --data-dir <tmp 数据目录>`，与 `--migrate` 对跑；只断言退出码 / stdout·stderr / 数据目录与库终态 / sha256）。 |
| `app/tests/test_target_repair.py` | 两个 CLI 用例改按新契约取证（`--repair-targets` 现在是别名）：一次别名迁移 + 第二次「无事可做」；旧库用例改用 **v2**（低于下限，仍被拒）。模块 docstring 的「不做自动迁移」段改写为引用 ADR-0015 / 指向 `--migrate`。 |
| `app/tests/test_migrate_cli.py` / `test_schema_version_gate.py` | **仅注释**：以「决策 8 = 不做迁移」为由的旧口径改写为引用 ADR-0015 / 指向 `--migrate`。 |
| `CONTEXT.md` | 新增**审计台账**词条（含 `_Avoid_`）；**数据目录**词条补上 `alibot.db` + `attachments/` + `schema-migrations.jsonl` 三件套。 |

**不做**（票面 Constraints）：不改链上九步与形态步骤的行为；不改 `--migrate` 与 `--dry-run` 的既有行为；
不 bump `SCHEMA_VERSION`；不新增退出码（集合仍是 `{0, 3, 4, 5}`）。
**未改**：`app/alibot/db.py`、`app/alibot/migrate.py`、`app/tests/test_target_scope.py`（一个字节都没碰）、
**历史取证面** `app/specs/issue-22/24/35/37-*.md`（`git diff` 为空，见 §2.7）、
`app/alibot/studio/**`、`alibot-design-system/**`、`docs/adr/**`、issue 正文。

---

## 1. 未定项与裁定依据（本票**不需要**新 ask）

票面把「弃用提示打在 stdout 还是 stderr」列为「若你认为这属产品取舍，先 ask」。**本票判定它不属产品取舍**，
因为规格已经指定，无需占用人的决策：

- 规格 **T3 的 S13 行**明写观察点是「stdout 含弃用提示」；
- 规格 **S13** 与票面约束同时要求「别名的 **stderr 与 `--migrate` 逐字相同**」——提示只有落在 stdout，
  这条等价性才不被破坏。

因此落点：`REPAIR_TARGETS_DEPRECATION_NOTICE` 打在 **stdout 的首行**（`__main__.py` 的常量注释里写明
依据 T3 §S13）。`--repair-targets --dry-run` 也被接受（不再报用法错误 2）——规格 T2 的接缝是
「`--migrate [--dry-run] --data-dir …`（**别名同理**）」，D11 要求「同一个入口」；强制它报错反而违背 S13。
这条由 Spec 轴独立判为**规格内**（见 §3 的 P-1）。

---

## 2. 验收逐条（票面 Observable acceptance 1–12）

### 2.1 同一库两条入口等价（票面 1；S13）

v3 库（`tests/_migration_history.py` 的逐版 DDL 留档搭出），**两个各自独立**的副本（`migrate/data` 与
`alias/data`，避免前一次迁移影响后一次）：

| 读数 | `--migrate` | `--repair-targets` |
| --- | --- | --- |
| 退出码 | `0` | `0` |
| stderr | `''` | `''`（**逐字相同**） |
| stdout 首行 | `迁移：<数据目录>/alibot.db` | `提示：--repair-targets 已弃用，请改用 --migrate（同一入口、同一条链、同一个退出码）。` |
| stdout 去掉那一行提示后 | —— | 与 `--migrate` 的 stdout **逐字相同**（把两个副本的根路径与备份时间戳/预演副本名抹平后比较；两份报告各 22 行、形态步骤都报 `改动 6 行`） |
| 终态形态快照（表 / 列 / 索引 / 隐式唯一约束 / 触发器 / 版本） | `==` | `==`（两个同源副本推到同一个终态） |

实样（v3 库，别名 stdout）：

```text
提示：--repair-targets 已弃用，请改用 --migrate（同一入口、同一条链、同一个退出码）。
迁移：…/alias/data/alibot.db
源版本：3 → 目标版本：12
本次备份：…/alias/data-backup-3-to-12-20261016T191223Z

实际执行的步骤（每步改动的数据行数）：
  形态步骤（channel 型作用域数据补齐：影子行 / 成员 / 已读 / 预算）（不推进版本号）  改动 6 行
  3 → 4  channel 型作用域数据搬迁（影子行 / 成员 / 已读 / 预算）  改动 0 行
  …（4 → 5 … 11 → 12 逐版各 0 行）…

迁移完成：schema_version = 12；本次共改动 6 行。
审计记录：…/alias/data/schema-migrations.jsonl
```

拒绝路径（`3` / `4`）的等价性用**同一个库文件**跑（两条路径都不写库）：stderr 是**逐字相同**的最强形式
（§2.4 / §2.5）。

### 2.2 半改名库走别名到终态（票面 2；S13 / D11）—— 不是「成功但什么都没变」

`stored=4`、数据仍在 `channel_*` 的半改名库（`test_shape_conditioned_step._build_half_renamed_library`）：

| 读数 | 值 |
| --- | --- |
| 退出码 | `0` |
| stderr | `''` |
| `schema_meta.schema_version` | `12`（= `SCHEMA_VERSION`） |
| `target` / `target_member` / `target_read` / `target_budget` 行数 | `1` / `2` / `2` / `1`（**四张表一起补齐**） |
| `target_budget.used`（`ch_dev`） | `3` = `BUDGET_LIMIT`（**已耗尽预算不被归零**，D5 / User Story 19） |
| 本代码 app 能打开 | `Database(data_dir).list_channels() == ['ch_dev']` 且 HTTP 面 `GET /api/health` = `ok`、`GET /api/channels` 首条 = `ch_dev` |

版本从 **4 推进到 12** 且四张表有行 ⇒ 既不是「成功但什么都没变」，也不是「停在 4 等用户再跑一次」。

### 2.3 接受范围 `[3, SCHEMA_VERSION]`（票面 3；S13 / E1）

对 `3`…`11` 的**每个**源版本各造一个库（复用 #92/#93 的 `tests/_migration_history.py` 留档与
`test_schema_migration._build_library`），跑别名：**9/9 退 `0`**，且终态与「本代码初始化的当前版本库」
在表 / 列 / 索引 / 隐式唯一约束 / 触发器上一致（沿用既有 `_assert_terminal_shape` 的新库对照 + 结构断言，D7）。

### 2.4 未来版本仍拒绝（票面 4；S14「方向没放宽」）

`stored = SCHEMA_VERSION + 1`、有业务数据：

| 读数 | 值 |
| --- | --- |
| `--migrate` 退出码 / `--repair-targets` 退出码 | `3` / `3` |
| 两者 stderr | **逐字相同**（含库路径、两个版本、`--migrate`、备份指引） |
| 库文件 sha256 | 前后一致（实测 `8e30004cc19d2a45…`，别名 stdout 只有那一行提示、不留新备份） |

### 2.5 `4` 同判（票面 5；S2 的 `4` / D0）

版本已是 `SCHEMA_VERSION`、缺一张当前形态必须存在的表（`task`）、且有业务数据：

| 读数 | 值 |
| --- | --- |
| `--migrate` / `--repair-targets` 退出码 | `4` / `4` |
| 两者 stderr | **逐字相同**：`不执行迁移：<库路径> 的版本已是 12，但缺当前形态必须存在的表（task）——这不是本入口能处置的形态。未改动库文件、未建备份。` |
| 别名 stdout | 只有那一行提示（不再有其它输出） |
| 库文件 sha256 / 新备份 | `4` 发生在**任何写入之前**：sha256 前后一致、同级不出现新备份 |

### 2.6 红 → 绿（票面 6）

| 侧 | 读数 |
| --- | --- |
| base `36873c7`（`git worktree` 出的隔离副本 `/tmp/rmt94-base`，放入本票的新测试文件） | 新增验收 **14 failed / 0 passed** |
| 本分支 | 同一份文件 **14 passed** |

对称探针（同一脚本，两侧各跑一次）：

| 场景 | base `36873c7` | 本分支 |
| --- | --- | --- |
| v3 库 `--migrate` / `--repair-targets` 退出码 | `0` / **`3`** | `0` / **`0`** |
| v3 库别名 stdout 首行 | 无（stdout 为空） | `提示：--repair-targets 已弃用，请改用 --migrate…` |
| `stored=4` 半改名库跑别名 | **`3`**（stderr：`拒绝：数据库的 schema 版本与本代码不匹配。`） | **`0`**（stderr 空，库推进到 12、四张表补齐） |

base 的红正是票面所述的旧口径：别名只处置「恰好等于当前版本」的半改名库，更早的版本被闸门拒绝。

### 2.7 豁免面未被触碰（票面 7；规格 D14 末条）

```text
$ git diff 36873c7...HEAD -- app/tests/test_target_scope.py \
    app/specs/issue-22-acceptance.md app/specs/issue-24-acceptance.md \
    app/specs/issue-35-acceptance.md app/specs/issue-37-acceptance.md \
    app/specs/issue-22-design-notes.md | wc -l
0
```

`grep -rn "决策 8" app/tests/*.py` 只剩两处：`test_schema_version_gate.py:4`（**已改写成**
「ADR-0004 / 规格决策 8 当时只登记了「不做旧数据迁移」这条**代价栏**（取代关系见 ADR-0015）」——
引用 ADR-0015 的新口径）与 `test_target_scope.py:391`（**不按 kind 分支**那条，D14 末条豁免，
一个字节未动）。

### 2.8 `CONTEXT.md`（票面 8；Change 5）

```diff
 **数据目录 (Data Directory)**:
 本机存放业务正本的目录（默认 `~/Alibot`，`--data-dir` 或环境变量 `ALIBOT_DATA_DIR` 可覆盖）：单文件库
-`alibot.db` 与附件字节子目录 `attachments/` 都在其中，因此它是**备份与搬迁的单位**——不是库文件的位置，
-而是"这个工作台的全部本机数据"的位置。
+`alibot.db`、附件字节子目录 `attachments/`，以及迁移**审计台账** `schema-migrations.jsonl` 都在其中
+（`alibot.db` + `attachments/` + `schema-migrations.jsonl`），因此它是**备份与搬迁的单位**——不是库
+文件的位置，而是"这个工作台的全部本机数据"的位置。

+**审计台账 (Audit Ledger)**:
+**数据目录**内的 append-only 文件 `schema-migrations.jsonl`（JSON Lines，一行一条迁移记录：from / to /
+起止时间 / 结果 / 备份路径）。每次**真正执行**的迁移写一条，失败也记（带原因）；它不落库表、不改
+**Schema 版本**，人类面（HTTP）没有任何写入口。
+_Avoid_: 日志（会被读成进程的 stdout / stderr）；迁移记录表（它不是库内的一张表）；把台账读成可编辑的配置（append-only，历史行不回改）
```

风格与既有词条一致（`**术语 (English)**` + 正文 + `_Avoid_` 行）；改动只有那两处。

### 2.9 质量命令全过（票面 9）

仓库根，全部在**冻结后**的代码上跑（`88b574c` 之后）：

| 命令 | 读数 |
| --- | --- |
| `.venv/bin/python -m pytest`（**原样**） | **4 failed / 469 passed / 1 skipped**（4 条是本机 `no_proxy` / `NO_PROXY` 注入带方括号 `[::1]` 的**已知环境噪声**：`test_agent_seam_client_lifecycle.py` 的 4 条；基线同样 4 红，**未改这些测试**） |
| 清代理 env（`env -u no_proxy -u NO_PROXY -u http_proxy -u https_proxy -u NODE_USE_ENV_PROXY`） | **473 passed / 1 skipped** |
| 基线对照 | 本票 base `36873c7` 实测 **460 collected**（清代理 env 下 459 passed + 1 skipped）；本票新增 14 条 ⇒ **474 collected** ✓ |
| `.venv/bin/ruff check app` | All checks passed! |
| `.venv/bin/ruff format --check app` | 101 files already formatted |
| `.venv/bin/python -m mypy` | Success: no issues found in 50 source files |
| `node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests` | 0 errors, 0 warnings, 0 informations |
| 4 个 `node --test app/tests/*.test.cjs` | 19 / 43 / 9 / 13 全 pass，fail 0 |

### 2.10 双轴 code-review（票面 10）—— 固定点 = `36873c7`，findings 与处理

两轴各自跑在独立 sub-agent 上（`code-review` skill 体例：Standards / Spec 分开、不合并排序）。

**Standards 轴**（结论：**无硬性违反文档标准**；以下为判断项）

| # | Finding | 处理 |
| --- | --- | --- |
| S-1 | `__main__.py` 的用法错误文案仍写「只能与 `--migrate` 同用」，与同文件 `--help`（已写「或它的别名 `--repair-targets`」）自相矛盾 | **已改**：文案补上别名（`88b574c`）；既有断言 `"--dry-run" in stderr and "--migrate" in stderr` 照旧成立 |
| S-2 | `repair.py` 的 `repair_target_rows` / `RepairError` / `render_outcome` 在 `app/alibot/**` 已无生产调用方（dead code / Speculative Generality）；且它仍要求「版本恰好等于」，与别名放宽后的语义并存，会复活 ADR-0015 §五**被否**的「同一个库、两个答复、两套语义」 | **部分采纳、保留并登记**：函数本身**不可达**（CLI 已无入口），语义分叉因此不可观察；它是 #92 已验收交付里 `channel_scope_gaps_of` 的**既有先例**，其单测是反向 1:1 一致性检查（channel 型 target ↔ 频道行 / 成员行）唯一被跑的地方。**不删**的理由是：票面只授权本票 `repair.py`「若需复用/收口」，删除它要连带删掉 ~450 行绿色测试，属**扩 scope**；ADR-0015 §五自己给的时点是「下个版本周期删别名」。**已加**明确删除时机注释（`88b574c`：随 `--repair-targets` 别名一并下线），并在此登记为遗留（§4） |
| S-3 | `CONTEXT.md` 的**备份**词条仍只写「含 `alibot.db` 与 `attachments/` 的字节」，与刚改的**数据目录**词条（三件套）口径不一致 | **不改、登记**：票面 Change 5 与 Ownership 都把 `CONTEXT.md` 的授权面限定为「仅第 5 条那两处」且要求「改动最小」——扩到第三处属越权。备份是 `shutil.copytree` 的整目录副本，台账自然在内，「含」不是穷举，故不构成事实错误（§4） |
| S-4 | `形态步骤` 已是承重词（`repair.py` / `__main__.py` / 测试都在用），但 `CONTEXT.md` 没有独立词条 | **不改、登记**：规格 **D14 / S18** 只把 `CONTEXT.md` 的面枚举为「数据目录 / 备份 / Schema 版本 / 版本闸门 / 数据迁移 + 附件修订」——`形态步骤` 不在授权清单里（§4） |
| S-5 | 新测试文件从三个兄弟测试模块 import 13 个下划线私有 helper（脆性耦合） | **接受**：与既有 prior art 同体例（`test_migrate_dry_run.py` 从 `test_schema_migration` / `test_shape_conditioned_step` import，`test_migration_chain_guard.py` 同）——仓库既有标准优先于 smell 基线；文件 docstring 已写明「只借文件级外部面」 |
| S-6 | `test_target_repair.py` 现在断言迁移 CLI 的输出（文件标题仍关于补齐原语）→ Divergent Change | **接受**：该文件本就是 `--repair-targets` 这条 CLI 的历史 prior art（规格 T5 引它），别名只是同一个旗标改走新分支；别名等价性的**主证据**另在 `test_repair_targets_alias.py` |

**Spec 轴**（结论）

| # | Finding | 处理 |
| --- | --- | --- |
| P-1 | `--repair-targets --dry-run` 由 usage-error（`2`）变为被接受，票面 7 条 acceptance 未提，疑似 scope creep | **保留并说明**：规格 **T2** 明写接缝是「`--migrate [--dry-run] --data-dir …`（**别名同理**）」、**D11** 要求「同一个入口」；让别名拒绝这个组合反而破坏 S13。「同入口」是承重判据，故判为规格内；已补 `test_alias_dry_run_is_the_same_read_only_preview_as_migrates`（两副本，退出码/报告等价、原库 sha256 前后一致） |
| P-2 | 成功路径的「本代码 app 能打开它」只经 `Database(data_dir)`，未走 T2 的 HTTP 面 | **已改**：半改名库用例补 HTTP 面读数（`GET /api/health` = `ok`、`GET /api/channels` 首条 = `ch_dev`），`88b574c` |
| P-3 | acceptance #1「同一个库分别跑两条路径」在成功路径上是**两个独立副本 + 归一化** | **保留并说明**：票面自己要求成功路径用「两个各自独立的库副本」；拒绝路径（`3` / `4`）在**同一个库文件**上跑，stderr 的逐字相同因此是更强形式。报告里路径/时间戳/副本名的差异是副本固有的，不是行为差异 |
| P-4 | 是否漏掉 D14 列出的存量旧口径 | **已核**：`db.py:46,888`、`test_schema_version_gate.py`、`test_target_repair.py` 的旧口径在 #89–#93 已同步或本票已改；`grep "决策 8"` 只剩豁免的 kind-branch 引用（§2.7） |

处理后的复核：`ruff check` / `ruff format --check` / `mypy` / `pyright` / 14 条新用例 / 全部既有测试模块
（raw 4-噪声红、清代理 env 473 passed + 1 skipped）全绿（§2.9）。

### 2.11 交付物（票面 11 / 12）

| 项 | 值 |
| --- | --- |
| 分支已推送 | `schema-migration-t94` → `origin` |
| PR | [#102](https://github.com/whutlichao/alibot/pull/102)（base `main`，head `schema-migration-t94`） |
| PR body | `app/specs/issue-94-pr-body.md`（`gh pr create --body-file`） |
| #94 摘要 comment | 已发（逐条读数摘要 + Answer / PR 指针） |
| `worker_done` | 从本 worker 终端发送并已收到成功回执（`succeeded`，带 Task ID / Dispatch ID） |

---

## 3. 已知边界

- **别名只放宽「离目标有多远」，没有放宽「方向」**：未来版本（库版本 > 代码版本）三条入口一个都不碰，
  仍退 `3`（§2.4）。别名也不是「后门」：它与 `--migrate` 共用同一道闸门与同一个迁移引擎。
- **别名的 stdout 比 `--migrate` 多一行**——这是规格允许的唯一差异；**stderr 逐字相同**（§2.1/2.4/2.5）。
- **`4` 不是迁移失败**：它是「还没动手」（sha256 不变、不建备份），与「动手后失败并已回滚」的 `5` 分属
  两个时点（§2.5）。
- **两处文档面不一致**（Standards S-3 / S-4）按「不扩 scope」登记为遗留，见 §4。

## 4. 遗留（登记，不属本票授权面）

1. **`CONTEXT.md` 的备份词条**仍只列 `alibot.db` 与 `attachments/`（未列审计台账）。同一小节、一行可改，
   但超出票面「仅第 5 条那两处」的授权。建议随 #87 波的下一张文档票一并补齐。
2. **`形态步骤` 缺 `CONTEXT.md` 词条**。规格 D14 / S18 的面清单里没有它；建议下一次 `CONTEXT.md` 修订时
   补 `**形态步骤 (Shape Step)**` + `_Avoid_`。
3. **`repair_target_rows` / `RepairError` / `RepairOutcome` / `render_outcome` 已是 test-only**（生产不可达）。
   删除时机与 ADR-0015 §五一致：**随 `--repair-targets` 别名在下个版本周期下线时一并收口**（该注释已写进
   `repair.py` 模块 docstring）。
4. **`app/alibot/db.py:719` 的拒绝正文**（`启动路径与 --repair-targets 仍按本规则拒绝`）在别名放宽后
   语义变窄：该句只在**真正不被接受**的库上渲染（可迁移区间根本不打印这句），故未改；`db.py` 也不在
   本票 Ownership 内。如后续要改，属另一次最小措辞对齐。
