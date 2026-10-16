## Summary

`python -m alibot --migrate --dry-run` 在**数据目录内**的临时副本上跑**完整**迁移路径（链前形态步骤
+ 逐版链，与真跑同一条代码路径），报告**将执行的步骤清单与每步将改动的行数**，并打出**原库文件
sha256 的前后两个读数**作证，随后**无条件删除**副本。它回答的是「这条链在这个库上能不能跑通」，
而不是「大概齐」。副本 = `alibot.db` **连同旁边的** `-wal` / `-shm`（只复制主文件会读到不完整状态：
已提交但未 checkpoint 的行还在 WAL 里）。

```text
app/alibot/migrate.py              # StagedLibraryCopy（副本，try/finally 删）+ dry_run_chain + 预演报告
app/alibot/__main__.py             # _LibraryVerdict / _inspect_library（真跑与预演共用的只读裁决）+ _run_dry_run
app/tests/test_migrate_dry_run.py  # 13 条 CLI 真子进程验收（T2 的唯一接缝）

_run_dry_run(data_dir)
  StagedLibraryCopy(data_dir)        # mkdtemp 在数据目录内 → 复制 db + -wal + -shm
    sha256_file(alibot.db)           # 「前」读数（原库只被读字节，从不被 SQLite 打开）
  _inspect_library(副本, 报原库路径)  # 同一份裁决：闸门 / 缺表 / 有业务数据 / 形态步骤有活
  dry_run_chain(副本, 源版本)         # BEGIN IMMEDIATE → run_chain(...) → ROLLBACK
  __exit__: cleanup() + sha256_file  # 成功 / 失败 / 抛异常都删副本；「后」读数
  render_dry_run_report              # 步骤表（含形态步骤）+ 逐步行数 + 两个读数 + 副本去向
```

**四条承重口径**：

1. **原库零字节改动是构造性的**：预演**从不**用 SQLite 打开原库——闸门、形态判定与整条链全部施在
   副本上，`sha256` 的前后两个读数只是把这件事写成可核对的证据（`D10`）。
2. **报告与真跑同形**：步骤表共用同一份渲染（`_render_step_lines`），形态步骤按既有口径标注
   「不推进版本号」；`--migrate` 的既有报告**逐字未变**（#95 的守卫测试照旧）。
3. **答复矩阵不变**：`{0, 3, 4, 5}`。`3` / `4` 与真跑**逐字相同**（正文报**原库**路径——实现里有一处
   专门的「按原库路径重渲染」，否则副本路径会泄漏进正文）；链在副本上跑不通 ⇒ `5`，措辞按**人定
   裁定**写「预演发现这条链在副本上跑不通；原库未改动」，**不写**「已回滚」（预演没有可回滚之物）。
4. **人定裁定 (A)**：单独给 `--dry-run`（不带 `--migrate`）是 CLI 用法错误 ⇒ argparse 退出 `2`，
   不进 `{0, 3, 4, 5}` 裁决集合。

`plan_steps` 另补一条**运行时**「每步严格 +1」校验：注册表在导入后被改坏（历史那次 `9 → 11` 的形状）
时，预演与真跑都会在**规划**这一步明确失败并指出断点，而不是把跳号那一步静默执行过去。

> Answer（逐条可复现读数 + 双轴 code-review 的 findings 与处理）：`app/specs/issue-93-acceptance.md`；
> tracker 上的摘要见 #93 的 comment。

## Evidence

- **Before**（本票 base `main @ 82a6ffb`，隔离副本）：新增验收文件 **13 红 / 0 绿**
  ——`--dry-run` 在基线不存在，子进程拿到 `alibot: error: unrecognized arguments: --dry-run`（退出 `2`）。
  **After**：同一份文件 **13/13 绿**。
- **v3 库预演**（`alibot.db` sha256 预演前后均 `6071297148…fe544`，数据目录预演后仍是
  `['alibot.db', 'attachments']`，退出码 `0`）：

```text
预演（只读，在数据目录内的临时副本上跑完整迁移路径）：/tmp/final93/v3/alibot.db
源版本：3 → 目标版本：12

将执行的步骤（每步将改动的数据行数）：
  形态步骤（channel 型作用域数据补齐：影子行 / 成员 / 已读 / 预算）（不推进版本号）  改动 6 行
  3 → 4  channel 型作用域数据搬迁（影子行 / 成员 / 已读 / 预算）  改动 0 行
  4 → 5 … 11 → 12  （逐版链各 0 行）

原库文件 sha256（预演前）：60712971481adaf7c203cd36ec894cbbedcea952de5b899b7ba6bb0d935fe544
原库文件 sha256（预演后）：60712971481adaf7c203cd36ec894cbbedcea952de5b899b7ba6bb0d935fe544
原库零字节改动：前后读数一致。
临时副本：…/.alibot-dry-run-v0zyi2qz 已删除；数据目录内不留副本、不建备份、不写审计台账、不改库里的 Schema 版本。
预演结论：这条链在副本上跑通了；本次共将改动 6 行。
```

- **`stored=4` 半改名库**：同一形状的报告，步骤表从 **4** 起步、形态步骤 **6 行**（非零）；
  预演后 `target` 里仍无 channel 影子行、`schema_version` 仍是 `4`、三张旧表数据原样在——报告说的是
  「将要搬」，原库**一行都没搬**。
- **WAL / SHM 完整状态**：v3 库 + 一条关掉自动 checkpoint 的 WAL 连接（多写 1 行 `channel_member`，
  该行只在 `-wal` 里）⇒ 形态步骤报 **7 行**（`1 影子行 + 3 成员 + 2 已读 + 1 预算`）。只复制主文件的
  实现只会算出 6 行。用完 `-wal` / `-shm` 仍在，数据目录条目集合与预演前一致。
- **零写副作用**：同级无 `*-backup-*` 目录、数据目录内无 `schema-migrations.jsonl`、库里
  `schema_version` 仍是 `3`、原库里没有 `target` 表；**预演之后真跑** `--migrate` ⇒ `0`、版本到 `12`、
  `target=1` / `target_member=2` / `target_read=2` / `target_budget.used=3` / `message=2`。
- **断链暴露（红证据在隔离副本，不入库）**：把注册表改成历史 `9 → 11 → 12` 的形状 ⇒ 预演退出 **`5`**、
  stderr 指出断点、正文与报告都写明原库未改动：

```text
[stderr] 预演发现这条链在副本上跑不通；原库未改动（预演没有可回滚之物，写操作只落在副本上）。
         原因：迁移链不连续：注册表里的 9 → 11 不是严格 +1 的一步（它跳过了 10）。…
[stdout] 原库文件 sha256（预演前/后）：6071297148…fe544 / 6071297148…fe544 —— 前后读数一致。
```

  （若直接在源码里把某步 `from` 改成落后号，则更早的导入期不变量 `_assert_chain_is_continuous()`
  先抛并点名断点——这条守卫来自 #90 / #91，本 PR 未改。）

- **质量命令**（仓库根）：`pytest` 原样 **4 failed / 455 passed / 1 skipped**（4 条是本机
  `no_proxy` / `NO_PROXY` 注入带方括号 `[::1]` 的**已知环境噪声**，基线同样 4 红）；
  清代理 env ⇒ **459 passed / 1 skipped**。基线 `82a6ffb` 实测 **447 collected**
  （清代理 env 下 446 passed + 1 skipped），本 PR 新增 13 条 ⇒ **460 collected**。
  `ruff check app` / `ruff format --check app`（98 files）/ `mypy` / `pyright`（0 errors）
  全过；4 个 `node --test` 全过（19 / 43 / 9 / 13）。

## Merge Danger

**Door:** 双向 —— 预演本身零写副作用（不建备份、不写台账、不改原库、不留副本），删掉这条 CLI 路径
就是一次纯回退。唯一落在**真跑**路径上的是 `plan_steps` 的运行时「严格 +1」校验：它不是新行为，
只是把「跳号那一步被静默执行」变成「明确失败并指出断点」。

**Blast Radius:** 小-中 —— 新增一条 CLI 路径与一个修饰旗标；`--migrate` 的既有输出与 `{0,3,4,5}`
答复**逐字不变**，三张旧表 / `BUSINESS_TABLES` / `SCHEMA_VERSION` / 链上九步与形态步骤的行为一个都
没动。

- **不做**（票面 Constraints）：不 bump `SCHEMA_VERSION`；不放宽 `--repair-targets` 的版本门（#94）；
  不动 `CONTEXT.md`（#94）；不新增退出码；不改 `app/tests/test_migration_chain_guard.py` 与
  `test_read_path_legacy_tables_guard.py`（#95 的守卫）。
- 预演**不建**临时副本之外的任何东西，也不碰 `attachments/` 字节（副本只含库文件与它的边文件）。
- 已知边界：预演与真跑之间有时间差——副本不会在真跑时被复用（与 ADR-0015 的代价栏一致）。
