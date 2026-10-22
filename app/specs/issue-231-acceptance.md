# issue #231 验收：数据目录默认与兼容

- 票面：<https://github.com/whutlichao/alibot/issues/231>（父规格 #228 的 D2、D9 数据目录行、D11 Seam B、D12）
- worktree：`/Users/apple/orca/workspaces/alibot/impl-231`
- branch：`issue-231-impl`（从 `agent-home-layers-integration` 357aa89 切出）
- 测试接缝：**进程启动**（规格 D11 Seam B / T3）——真实子进程 + 临时 `HOME`，不读写真实
  `~/.alibot` 与 `~/Alibot`。

## 1. 判据 → 落点

| 票面 Acceptance criteria | 落点 | 证据（下文命令/用例） |
| --- | --- | --- |
| 四种组合下解析顺序符合「显式覆盖 → 新默认 → 旧位置兼容 → 新建」 | `db.resolve_data_dir()`；`__main__.main` | T1、T2、T3、T4、T5 |
| 新旧同时存在时选新默认，启动输出说明旧位置被忽略 | `resolve_data_dir()` 的 `new-default` 分支 + `notice` | T2 |
| 任何路径下旧目录内容与 mtime 不变；不删除、搬移、合并 | 解析只做 `exists()`；测试快照旧目录 | T2、T3、T4 |
| 启动输出打印实际数据目录与来源 | `__main__.main` 的两行输出 | T1–T5 |
| `--data-dir` / `ALIBOT_DATA_DIR` 只改数据目录，不移动全局层根 | 显式分支只返回数据目录本身；全局层无解析实现也无写入 | T4 |
| 文档给出数据目录与全局层各自可直接执行的显式路径备份/恢复命令；写明全局层不在数据目录备份内 | `app/README.md` §「数据目录：默认在隐藏根下，备份方式仍是复制目录」 | 见 §3 |
| 迁移文档保持「停应用 → 整目录复制 → 校验 → 指向新位置」与「复制前先备份」，启动绝不自动迁移 | 同上 §「从旧默认位置搬到新默认位置是显式动作」 | 见 §3、T3 |
| 现有调用方行为兼容；测试用临时 HOME / 临时目录 | `default_data_dir()` 保留；既有 76 条受影响用例全绿 | §2 |
| 代码与 README 默认路径口径一致；零 schema 变更 | `DEFAULT_DATA_DIR_NAME = "data"`、`DATA_DIR_ROOT_NAME = ".alibot"`；`SCHEMA_VERSION` 未改 | T6、§4 |

「旧目录 mtime 不变」的口径：**解析与启动输出**不改旧目录；当旧位置正是当前数据目录时，
应用按既有语义正常读写它（它仍是运行中的数据目录），但绝不搬移、删除或与新位置合并。

## 2. 红 → 绿读数

### 红（实现前）

```sh
cd app
../.venv/bin/python -m pytest tests/test_data_dir_resolution.py -p no:cacheprovider
```

```
FAILED tests/test_data_dir_resolution.py::test_creates_new_hidden_default_when_neither_location_exists
FAILED tests/test_data_dir_resolution.py::test_existing_new_default_wins_over_legacy_and_legacy_is_untouched
FAILED tests/test_data_dir_resolution.py::test_legacy_location_is_used_with_readable_compatibility_notice
FAILED tests/test_data_dir_resolution.py::test_explicit_override_wins_and_leaves_defaults_and_global_layer_untouched[flag]
FAILED tests/test_data_dir_resolution.py::test_explicit_override_wins_and_leaves_defaults_and_global_layer_untouched[env]
FAILED tests/test_data_dir_resolution.py::test_resolution_is_deterministic_across_runs
FAILED tests/test_data_dir_resolution.py::test_help_reports_new_hidden_default_without_creating_it
7 failed in 8.54s
```

失败原因：启动输出里没有「数据目录 / 来源」两行；默认路径仍是 `~/Alibot`；帮助文本仍是旧默认。

### 绿（实现后）

```sh
cd app
../.venv/bin/python -m pytest tests/test_data_dir_resolution.py -p no:cacheprovider
```

```
7 passed in 7.52s
```

受影响用例族：

```sh
cd app
../.venv/bin/python -m pytest \
  tests/test_data_dir_resolution.py tests/test_migrate_cli.py \
  tests/test_schema_version_gate.py tests/test_budget_measure.py \
  tests/test_target_repair.py -p no:cacheprovider
```

```
76 passed in 38.10s
```

快速门禁（worktree 根）：

```sh
scripts/check.sh --fast
```

```
ruff check：All checks passed!
ruff format --check：1 file would be reformatted → 手工修正后全绿
mypy / pyright：通过
node --test：258 passed / 0 failed
门禁全绿
```

## 3. 文档读数

`app/README.md` 的数据目录段现在写明：

- 默认数据目录 `~/.alibot/data/`，旧 `~/Alibot/` 只做兼容读取；四种解析顺序与启动来源输出；
- 数据目录备份 / 恢复的**显式路径**命令（`~/.alibot/data` ↔ `~/.alibot-backups/data-<日期>`）；
- 全局层的**单独**备份 / 恢复命令（`~/.alibot/agents`、`~/.alibot/skills`），并明确
  **全局层不在数据目录备份内**；
- 迁移步骤：停应用 → 整目录复制 → 校验 → 指向新位置；复制前先整目录备份；启动绝不自动迁移。

复核命令：

```sh
rg -n "默认数据目录|独立|显式动作|整目录复制|不自动迁移|agents-\$|skills-\$" app/README.md
```

## 4. 范围与边界

- **零 schema 变更**：`app/alibot/db.py` 的 `SCHEMA_VERSION` 未改；无迁移步骤、无测试留档变更。
- **不做**：启动时自动迁移、新增迁移命令、全局层根解析/创建、全局规则/技能/记忆/MCP 装配。
- 创建纪律：只有「两者都不存在」的启动路径创建 `~/.alibot/data/`（连同 `~/.alibot` 父目录）；
  `agents/`、`skills/` 不被创建（T1 断言）。
- `--migrate` / `--dry-run` 的输出与行为未改；迁移入口仍不凭空创建目录。

## 5. 复跑清单

```sh
cd /Users/apple/orca/workspaces/alibot/impl-231
scripts/check.sh --fast
cd app && ../.venv/bin/python -m pytest tests/test_data_dir_resolution.py \
  tests/test_migrate_cli.py tests/test_schema_version_gate.py \
  tests/test_budget_measure.py tests/test_target_repair.py -p no:cacheprovider
```
