<!-- 本文件是 GitHub issue #112 的 Answer 评论留档；tracker 上的评论才是正本，以 tracker 为准。 -->

# Answer —— 实施票 ① / #112（提醒的库形态与迁移）

> 正本：https://github.com/whutlichao/alibot/issues/112 ｜ PR：https://github.com/whutlichao/alibot/pull/118 ｜ 2026-10-17

## 1. 开工时实际基线（如实登记）

| 项 | 读数 |
| --- | --- |
| `git fetch origin` 后 `origin/main` | **`271dc2f`**（`Merge pull request #116 from whutlichao/reminder-trigger-tickets`） |
| 是否前进 / 是否 rebase | **未前进** —— 与本票 HEAD 相同，**无需 rebase** |
| 票面给的基线 | `main` = `5c4dc5c`、`SCHEMA_VERSION` = 12 |
| 实测 `SCHEMA_VERSION` | **12**（`app/alibot/db.py:59`），与票面一致 |
| `docs/adr/` 最大编号 | **0017**，本票不新增 ADR |
| 号段是否被抢 | **否** —— `origin/main` 上仍是 12，因此按判据取 **13**（「高于当前 main 的最小号」），未走 ask 确认 |

**行号复核（自行实测，未照抄票面）**：票面给的 `db.py:59` / `:182-183` / `:378-388` / `:1341` / `:1359-1360`、`migrate.py:651` / `:660`、`i18n.py:60` / `:242` 全部与实测一致。

## 2. 交付物

| 对象 | 动作 |
| --- | --- |
| `app/alibot/db.py` | `SCHEMA_VERSION` 12 → **13**；号段记账补 13 的分配与代价栏；`REMINDER_TIME_PATTERN` 改为 `YYYY-MM-DD HH:MM`（含注释口径）；`reminder` DDL 加 **`fired_at TEXT`**（可空）与列注释 |
| `app/alibot/migrate.py` | 新增 `_step_12_to_13` + `REWRITE_LEGACY_REMINDER_TIME_SQL` + `_reminder_legacy_time_prefix()`；`STEPS` 末尾登记 `MigrationStep(12, 13, …)`；新增 `_time_narrowing_lines()` 并把收窄说明挂进**真跑与预演共用**的报告路径（`_render_step_lines` 一行未改） |
| `app/alibot/i18n.py` | 改写既有键 `error.reminder_time_shape` 的取值（zh `:60` / en `:242`），**键集不变、参数位不变、不新增键** |
| `app/README.md` | 数据模型表与 HTTP 契约表同步新形状 + `fired_at` 列 |
| `app/tests/test_reminder_schema_step.py` | **新增**（676 行）：C4 / C5 / C11 / C12 / C13 / C14 的可观察判据 |
| `app/tests/test_reminders.py` | 既有用例按 C4 同步（旧形状 `HH:MM` → 新形状），并修正 docstring |
| `app/tests/_migration_history.py` | 补 v13 的对象集与 DDL 留档（v8/v11 的历史读数保持原样） |
| **PR #118** | base `main` ← `issue-112-reminder-schema`，body 用 `--body-file app/specs/issue-112-pr-body.md` |

## 3. 六条契约的落点与可观察判据

| 契约 | 落点 | 可观察判据（本票实测） |
| --- | --- | --- |
| **C4** | `REMINDER_TIME_PATTERN`、`create_reminder` 的校验、`error.reminder_time_shape` | `2026-10-18 09:00` ⇒ 201 且逐字回读；`09:00` / `2026-10-18` / `24:00` / `09:00:00` 等 8 种不合形状 ⇒ 400 + `fields.time`（可读自然语言）；`repeat` / `date` / `every` ⇒ 400、不静默忽略 |
| **C5**（本票那一半） | `fired_at TEXT` 可空列 + 既有 `REMINDER_SELECT_SQL` 用 `r.*`（**无需改**） | 迁移后所有存量行 `fired_at IS NULL`；列清单 = 五列 + `fired_at`（**无第三列**）。**不写任何投递逻辑**——原子性归票 ② |
| **C11** | `SCHEMA_VERSION = 13`；`MigrationStep(12, 13, …)` | v12 库 `--migrate` ⇒ 退出 0、`schema_version = 13`、报告含 `12 → 13` 且终点覆盖 `SCHEMA_VERSION`；**删掉那一步会让 `_assert_chain_is_continuous` 在导入期直接抛错**（守卫不是只靠一条测试） |
| **C12** | `_step_12_to_13` 的窄改写（`GLOB '[0-2][0-9]:[0-5][0-9]'` + `NOT GLOB '*[^0-9:]*'`） | `09:00` 存量行 ⇒ `<当日> 09:00`、`fired_at` 为 `null`；5 条不同钟点 ⇒ **全部同一日期** + 各自原钟点；已是新形状的行**不被改写**（幂等：退回版本号重跑 ⇒ 改动 0 行） |
| **C13** | `_time_narrowing_lines`（真跑 + 预演共用） | stdout 报告里能读到「一次性收窄」「每天」「迁移执行当日」三个标记——**不止步数统计** |
| **C14** | 复用既有 `changed_rows` | 3 条存量 ⇒ `改动 3 行`；空表 ⇒ `改动 0 行`；3 旧 + 1 新 ⇒ `改动 3 行`；报告无「改写行数」这种新字段（`_render_step_lines` 一行未改） |

## 4. 时区口径的取证（两种互补证据）

「到期判定用**本机本地时间**；`created_at` 仍是 **UTC 审计时间戳**」（C7）与「迁移执行当日」（C12）都只在本机 TZ 下可判定，而一个**误用 UTC 当日**的实现只在「本地日 ≠ UTC 日」的窗口里才会红。本票用两条互补证据把它变成**必然可判**：

1. 所有真跑子进程钉 `TZ="UTC-8"`（POSIX 语义 = UTC+8，恒 8 小时偏移），断言改写结果 = 该时区当日；
2. 新增一条按**当前 UTC 小时动态挑偏移**的用例（`UTC+12` / `UTC-12`，12 小时整体位移必然跨日界线），先自查「本地日 ≠ UTC 日」这个前提成立，再断言迁移写的是**本地**那个。

## 5. 双轴 code-review 的发现与处置

两轴**独立**运行（`Standards` 守本仓既有规范 / `Spec` 对本票契约），各自独立发现了**同一个真实缺陷**——这是这两轴分开跑的价值所在。

| 轴 | 发现 | 处置 |
| --- | --- | --- |
| Standards · P1 | `app/README.md` 契约表未随契约变更同步（`:103` 数据模型仍写 `time(HH:MM)`、无 `fired_at`；`:224` HTTP 契约同） | **已改**（仓内先例把这类漂移判为 P1：`issue-14-acceptance.md:333`） |
| Standards · P1 | `assert not (...).exists() or True` 恒真，名为守卫实不守 | **已删**（连同另两条不验任何契约的凑数用例） |
| Standards · P2 | `_build_v12_library(times=…)` 参数无调用者（Speculative Generality）；`hashlib_sha256` 重复实现且函数内 `import` | **已改**：删参数、复用既有 `_sha256` |
| Standards · P2 | `test_reminders.py` docstring 仍称「合法形状是 `HH:MM`」 | **已改** |
| Standards · P2 | `_step_12_to_13` 里 `if "reminder" not in _table_names(conn)` 在 `_create_missing_tables` 之后不可达 | **已删** |
| **两轴同时** · P1 | `_migration_history.py` 的 v8 对象集被误指向 `reminder_v13`（含 `fired_at`）⇒ v8 历史库被造成「已有 `fired_at`」，`_add_missing_columns("reminder")` 再也走不到；且 `13: '271dc2f'` 自指一个不含该 DDL 的提交 | **已改**：v8 指回当时的 `reminder`（五列）；13 的 commit 读数标为 `pending`（合并后回填） |
| Spec 轴 · P2 | 「本地而非 UTC」只在窗口内被证 | **已硬化**（见 §4） |
| Spec 轴 · P2（须登记） | C4 落地后写路径只收新形状，而 ③ 尚未交付日期输入 ⇒ **① 合入到 ③ 之间 UI 建不出提醒** | **已在 PR 正文的 Merge Danger 里登记**为四票拆分的既定代价（非越界） |

**两轴的结论**：Standards = BLOCK（两条 P1，均已修）；Spec = OK with notes（P1 建议合并前修，已修）。

## 6. 基线既有代码的静态检查误报（登记，未改）

pi-lens 反复报 `migrate.py` 的 `StagedLibraryCopy.cleanup` 里 `shutil.rmtree(self.copy_dir, ignore_errors=True)` 为 blocker。取证：

- 该行在 `origin/main` 上**逐字相同**（第 989 行），**不在本票 diff 内**（本票对 `migrate.py` 的改动只落在 6 处：118 / 633 / 710 / 916 / 949 / 1173 起）；
- 票面「不写面」未授权扩 scope；
- `ignore_errors=True` 本身就吞掉异常，这条规则对该行是**误报**；
- 仓库自有门禁（ruff / ruff format --check / mypy / pyright）**全绿**。

按 coordinator 裁定：**登记为「基线既有代码的 pi-lens 误报，不在本票写面」，不改**——不把无关改动混进这个阻塞者的 PR。

## 7. 门禁读数（本 worktree 实测）

```text
.venv/bin/python -m pytest                      → 全绿（2 skip：既有 1 + libfaketime 1）
.venv/bin/ruff check app                        → All checks passed!
.venv/bin/ruff format --check app               → 117 files already formatted
.venv/bin/python -m mypy                        → Success: no issues found in 51 source files
node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
                                                → 0 errors, 0 warnings, 0 informations
```

## 8. 不写面取证

| 项 | 读数 |
| --- | --- |
| `git diff origin/main...HEAD -- app/alibot/studio/` | **为空**（本票不碰前端，归 ③④） |
| `docs/adr/` / `CONTEXT.md` / `app/specs/reminder-trigger-*` 既有文件 | **零改动**（只新增本 Answer 与 PR body 两份留档） |
| `system` 消息的四条既有口径（`UNREAD_WHERE_SQL` / 搜索排 `system` / `notify` 早退 / 不耗预算） | **一个字节未动** |
| 第三个列（`state` / `due_date` / 时区列） | **未添加**；`state` 是 ② 的派生值，不是列 |
| 号段 | **13**，未 bump 到 14 |

## 9. 遗留与下游

1. **票 ② 可直接开工**：`fired_at` 列与新的 `time` 形状已就位；`fired_at` 的**写入原子性**（落消息 + 写标记同一事务）与全部投递逻辑归 ②，本票**未写**任何投递代码，也未给读路径加 `fired_at` / `state` 键。
2. **① → ③ 之间的已知中间态**（已在 PR 正文登记）：写路径只收 `YYYY-MM-DD HH:MM`，而 ③ 尚未交付日期输入 ⇒ 这段窗口里 UI 建不出提醒（后端回 400 + 可读 `fields.time`）。存量提醒的读 / 删 / 迁移不受影响。
3. **`libfaketime` 那条强判据仍默认跳过**：本机未安装，故「同一天两个不同钟点的各迁一次 ⇒ 同一日期」这一条在 CI 上也会 skip；但 §4 的两条互补证据已把「本地 vs UTC」变成必然可判。
4. **本 Answer 与 PR body 在 `app/specs/` 下新增了两份留档**（`issue-112-answer.md` / `issue-112-pr-body.md`），沿用 #111 的既有体例。
