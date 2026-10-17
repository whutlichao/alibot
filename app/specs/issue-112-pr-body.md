## Summary

提醒的库形态升到 13，并给存量行一条**可复现、可读数**的解释口径。本票是四票里唯一的**阻塞者**：没有 `fired_at` 列与新的时间形状，②③④ 都无从谈起。

```diff
 app/alibot/db.py
   SCHEMA_VERSION = 12 → 13            # 号段记账补 13 的分配与代价栏
   REMINDER_TIME_PATTERN               # HH:MM → YYYY-MM-DD HH:MM（一次性）
   reminder 表 DDL
     + fired_at TEXT                   # 可空；NULL = 还没到点

 app/alibot/migrate.py
   STEPS
     + MigrationStep(12, 13, "提醒的到点形态（time 补日期 / fired_at 列）", _step_12_to_13)
   render_report / render_dry_run_report
     + 【时间语义的一次性收窄】说明      # 真跑与预演共用同一份渲染

 app/alibot/i18n.py
   error.reminder_time_shape           # 既有键的取值改写（zh + en，键集不变）
```

迁移那一步分成三段，**报告读数的来源只有一处**：

```text
_step_12_to_13
  create_missing_tables("reminder")     # 缺表的树（v10 那类）先补表
  add_missing_columns("reminder")       # 补 fired_at（列定义从 SCHEMA 解析，CHECK 不会漏）
  UPDATE reminder SET time = <当日> || time
    WHERE time GLOB '[0-2][0-9]:[0-5][0-9]'
      AND time NOT GLOB '*[^0-9:]*'     # 窄：只动旧形状；新形状的行原样留着（幂等）
  → return rowcount                     # = 实际被改写行数（复用既有 changed_rows）
```

`_render_step_lines` 一行未改，收窄说明是**旁挂的一节正文**（`_time_narrowing_lines`），因此 C14 的「不新增报告字段」在构造上成立：

```text
实际执行的步骤（每步改动的数据行数）：
  12 → 13  提醒的到点形态（time 补日期 / fired_at 列）  改动 3 行

【时间语义的一次性收窄】存量提醒的到期语义从「每天该钟点」收窄为「迁移执行当日该钟点」
（本机本地时间口径）：原来的「每天 9 点」现在是「迁移那天 9 点」。
它不丢数据行，但改变了行的含义——这是一次语义收窄，不是一次普通的形变（ADR-0017 §五）。
```

## Evidence

- **Before:** v12 库 + `time='09:00'` 的存量行 ⇒ 写路径拒绝 `2026-10-18 09:00`，`reminder` 表五列、无 `fired_at`。
  **After:** 同一行迁移后为 `<迁移执行当日> 09:00`，`fired_at` 为 `NULL`，列清单 = 五列 + `fired_at`。

- **Before:** `MigrationStep(11, 12)` 是链的末步，`SCHEMA_VERSION = 12`。
  **After:** `MigrationStep(12, 13)` 已登记；v12 库 `--migrate` 退出码 `0`、`schema_version = 13`、报告含 `12 → 13` 那行。删掉那一步 ⇒ 链上 `_assert_chain_is_continuous` 在导入期直接抛错（守卫在 `import` 时生效，不是只靠一条测试）。

- **报告行数可核对**（`app/tests/test_reminder_schema_step.py`，全部经 CLI 真子进程 + 磁盘真实库）：

  | 造法 | `12 → 13` 那行 |
  | --- | --- |
  | 3 条 `HH:MM` | `改动 3 行` |
  | 空提醒表 | `改动 0 行` |
  | 3 条 `HH:MM` + 1 条已是新形状 | `改动 3 行`（新形状不改写） |
  | 迁完把版本退回 12 再跑一遍 | `改动 0 行`（幂等） |

- **门禁读数**（本 worktree，`origin/main` = `271dc2f`，未前进、无需 rebase）：

  ```text
  .venv/bin/python -m pytest                      → 全绿（2 skip：既有 1 + libfaketime 1）
  .venv/bin/ruff check app                        → All checks passed!
  .venv/bin/ruff format --check app               → 116 files already formatted
  .venv/bin/python -m mypy                        → Success: no issues found in 51 source files
  node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
                                                  → 0 errors, 0 warnings, 0 informations
  ```

- **不写面取证**：`git diff origin/main...HEAD -- app/alibot/studio/` **为空**（本票不碰前端，归 ③④）；`docs/adr/`、`CONTEXT.md`、`app/specs/` **零改动**。

- **本地 vs UTC 的分野是必然可判的**（两条互补证据）：① 本文件所有真跑用例都在 `TZ="UTC-8"` 下起子进程，并断言改写结果 = 那个时区的「当日」；② 另有一条用例**按当前 UTC 小时动态挑偏移**（`UTC+12` / `UTC-12`，12 小时的整体位移必然跨日界线），先自查「本地日 ≠ UTC 日」这个前提成立，再断言迁移写的是**本地**那个——一个误用 `datetime.now(UTC)` 取日期的实现每一次都会红。

- **一处基线既有代码的静态检查误报（登记，不在本票写面）**：pi-lens 反复报 `migrate.py` 的 `StagedLibraryCopy.cleanup` 里 `shutil.rmtree(self.copy_dir, ignore_errors=True)` 为 blocker。取证：该行在 `origin/main` 上**逐字相同**（第 989 行），不在本票 diff 内（本票对 `migrate.py` 的改动只落在 6 处，不含它），票面「不写面」未授权扩 scope；且 `ignore_errors=True` 本身就吞掉异常，这条规则对该行是误报。仓库自有门禁（ruff / ruff format / mypy / pyright）全绿，因此**登记不改**，不把无关改动混进这个阻塞者的 PR。

- **一处如实登记的取证局限**：C12 判据 2 的**强形式**（同一天两个不同钟点各迁一次 ⇒ 同一日期）需要用 `libfaketime` 把子进程时钟挪走。本机未安装它，那一条**跳过**而不是伪造——判据 1（已知执行日）、判据 2 的弱形式（N 条不同钟点 ⇒ 全部同一日期）、以及上面那条跨日界线的本地/UTC 判据都在任何机器上都真跑且已绿。

## Merge Danger

**Door:** two-way —— 代码本身可 `git revert`；但**号段是单向的**：合并后 `main` 的 `SCHEMA_VERSION` 由 12 升到 13，被 12 及更早代码创建过的库在本版下会被闸门拒绝（既有纪律，`db.py:44-58` 的代价栏已登记）。处置由拒绝正文给出：先备份整个数据目录，再 `python -m alibot --migrate` 逐版推进。迁移前的整目录备份是构造性前置，失败即拒绝迁移。

**Blast Radius:** schema-wide（一次迁移步骤 + 一张表的形状），但**改动面窄**：`reminder` 只多一列，`system` 消息的四条既有口径（不计未读 / 不进搜索 / 不唤醒 / 不耗预算）一个字节都没动。

**Ramifications:**

- 这是一次**时间语义的一次性收窄**——「每天 9 点」变成「迁移那天 9 点」。不丢数据行，但改变了行的含义；报告与 `app/specs/reminder-trigger-spec.md`（C13）都要求读者能读到这句。
- **合入到 ③ 之间有一个已知的中间态**（四票拆分的既定代价，不是越界）：本票让写路径只收新形状 `YYYY-MM-DD HH:MM`，而 ③ 尚未交付日期输入，现网前端仍把 `#reminderTime` 的原值（`09:00`）直接 POST 上去 ⇒ **在这段窗口里 UI 建不出提醒**（后端会回 `400 validation_failed` + `fields.time`，可读正文已指向新形状）。存量提醒的读、删、迁移都不受影响。
- 到期判定按**本机本地时间**、`created_at` / `fired_at` 仍是 **UTC 审计戳**；库内不做换算比较。代价（ADR-0017 §二已记账）：**跨时区搬数据目录后到期语义会漂**。
- ② 是本票的下游：`fired_at` 的**写入原子性**（落消息 + 写标记同一事务）归 ②，本票只交付列、形状与「迁移后为 `NULL`」。
