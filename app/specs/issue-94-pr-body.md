## Summary

`python -m alibot --repair-targets` 降为 `--migrate` 的**弃用别名**：同一个入口、同一条「形态步骤 + 逐版链」、
同一结果、同一退出码集合 `{0, 3, 4, 5}`。它在最前面多打一句「请改用 `--migrate`」（**stdout**），随后与
`--migrate` 的 stdout 只差那一行、**stderr 逐字相同**。版本门随之放宽为 `[3, SCHEMA_VERSION]`——半改名库
（`schema_version=4`、数据仍在 `channel_*`）不再被它拒之门外；**方向没有放宽**，未来版本仍退 `3`。

```diff
 __main__.py
-  if args.migrate:            return _run_migrate(data_dir)      # 迁移入口
-  ...
-  if args.repair_targets:     return _run_repair(data_dir)       # 独立修复入口（版本门 = 恰好当前版本）
+  is_migration = args.migrate or args.repair_targets            # 同一条入口
+  if is_migration:
+      if args.repair_targets:
+          print(REPAIR_TARGETS_DEPRECATION_NOTICE)               # stdout 首行，唯一多出的差异
+      return _run_dry_run(data_dir) if args.dry_run else _run_migrate(data_dir)
```

```text
--repair-targets
  is_migration = args.migrate or args.repair_targets     # 两条旗标收进同一分支
  print(废弃提示)                                        # stdout，首行
  _run_migrate(data_dir)                                 # 与 --migrate 逐字相同的那条路
    _inspect_library(data_dir)                           # 闸门 → 缺表/业务数据 → 形态步骤有活
      版本 ∈ [3, SCHEMA_VERSION) → accepted（放宽后的版本门）  # 别名与 --migrate 共用
      未来版本 / 低于 3 / 缺号有数据 / 认不出的号 → not-accepted（3）
      版本 = SCHEMA_VERSION 且缺当前形态的表且有业务数据 → shape-missing（4，动手之前）
    migrate.migrate_data_dir(...)                        # 备份 + 单事务 + 形态步骤 + 逐版链 + 审计
```

```text
app/alibot/__main__.py              # 别名接线 + 弃用提示常量；删掉已不可达的 _run_repair
app/alibot/repair.py                # 仅注释：模块定位（别名走 __main__ 同一分支、版本门放宽、原语保留与删除时机）
app/tests/test_repair_targets_alias.py  # 新增 14 条（真实子进程 + 磁盘真实库 fixture）
app/tests/test_target_repair.py     # 两个 CLI 用例改按新契约取证；docstring 引 ADR-0015
app/tests/test_migrate_cli.py       # 旧口径注释同步
app/tests/test_schema_version_gate.py  # 旧口径注释同步
CONTEXT.md                          # 新增「审计台账」词条 + 「数据目录」补三件套
```

**四条承重口径**：

1. **等价性是可执行断言**：成功路径用**两个各自独立**的库副本对跑，退出码相同、stderr 逐字相同、
   stdout 去掉提示行后逐字相同（路径/时间戳/预演副本名抹平）、终态形态快照 `==`；拒绝路径（`3` / `4`）
   在**同一个库文件**上跑（两条路径都不写库），stderr 逐字相同是更强形式。
2. **`4` 同判**（规格 D0）：「版本已等于本代码版本、缺当前形态必须存在的表、且有业务数据」⇒ 两条入口
   都给 `4`，且发生在**任何写入之前**（sha256 不变、不建备份）。
3. **方向不放宽**（S14）：`stored = SCHEMA_VERSION + 1` ⇒ 别名退 `3`、库 sha256 不变、与 `--migrate` 同答复。
4. **弃用提示落点 = stdout**：规格 T3 的 S13 行明写「stdout 含弃用提示」，且 S13 要求别名 stderr 与
   `--migrate` 逐字相同——提示落在 stdout 才不破坏这条等价性（不属产品取舍，无需 ask）。

`--repair-targets --dry-run` 也一并继承（不再报用法错误 `2`）：T2 的接缝写的是「`--migrate [--dry-run]`
（**别名同理**）」，D11 要求「同一个入口」。

> Answer（逐条可复现读数 + 双轴 code-review 的 findings 与处理）：`app/specs/issue-94-acceptance.md`；
> tracker 上的摘要见 #94 的 comment。

## Evidence

- **Before**（本票 base `main @ 36873c7`，`git worktree` 隔离副本）：新增验收文件 **14 红 / 0 绿**。
  对称探针：
  - v3 库：`--migrate` 退 `0`，`--repair-targets` 退 **`3`**（stderr「拒绝：数据库的 schema 版本与本代码不匹配。」，stdout 空）。
  - `stored=4` 半改名库跑别名：退 **`3`**（同上拒绝），库停在 4、四张表仍空。
- **After**（本分支）：同一份新增文件 **14/14 绿**；对称探针两条都 `0`，别名 stdout 首行是
  `提示：--repair-targets 已弃用，请改用 --migrate（同一入口、同一条链、同一个退出码）。`
- **同库两条入口等价**（v3 库，两个独立副本，实测）：两条都退 `0`、stderr 都为空、别名 stdout 去掉首行
  提示后与 `--migrate` **逐字相同**（形态步骤都报 `改动 6 行`）：

```text
[--migrate]        迁移：…/migrate/data/alibot.db
[--repair-targets] 提示：--repair-targets 已弃用，请改用 --migrate（同一入口、同一条链、同一个退出码）。
[--repair-targets] 迁移：…/alias/data/alibot.db
                   …（其后 22 行逐字相同）…
```

- **半改名库 `stored=4` 走别名到终态**：退 `0`、`schema_version = 12`、`target` / `target_member` /
  `target_read` / `target_budget` = `1` / `2` / `2` / `1`、`target_budget.used = 3`（= `BUDGET_LIMIT`，
  **已耗尽预算不归零**）、本代码 `Database` 与 HTTP 面都能打开（`GET /api/channels` 首条 = `ch_dev`）。
- **接受范围 `3`…`11`**：9/9 退 `0`，终态与「本代码初始化的当前版本库」在表 / 列 / 索引 / 隐式唯一约束 /
  触发器上一致。
- **未来版本**：两条入口都退 `3`、**stderr 逐字相同**、库 sha256 前后一致（`8e30004cc19d2a45…`）。
- **`4` 同判**：两条入口都退 `4`，stderr 逐字相同（`不执行迁移：… 缺当前形态必须存在的表（task）……未改动库文件、未建备份。`），
  sha256 不变、不出现新备份。
- **豁免面未被触碰**：`git diff 36873c7...HEAD -- app/tests/test_target_scope.py app/specs/issue-22/24/35/37-*.md`
  输出 **0 行**；`grep "决策 8" app/tests` 只剩 `test_target_scope.py:391`（kind 分支，D14 末条豁免）。
- **质量命令**（仓库根，冻结后代码）：`pytest` 原样 **4 failed / 469 passed / 1 skipped**（4 条是本机
  `no_proxy` / `NO_PROXY` 注入带方括号 `[::1]` 的**已知环境噪声**，基线同样 4 红，未改这些测试）；
  清代理 env ⇒ **473 passed / 1 skipped**。基线 `36873c7` 实测 **460 collected**，本 PR 新增 14 条 ⇒ **474**。
  `ruff check app` / `ruff format --check app`（101 files）/ `mypy`（50 files）/ `pyright`（0 errors）
  全过；4 个 `node --test` 全过（19 / 43 / 9 / 13）。

## Merge Danger

**Door:** 双向 —— 别名不引入任何新的写路径：它只是把已有旗标接到已有的 `--migrate` 入口上。回退本 PR
就是恢复「`--repair-targets` 只处置恰好当前版本的半改名库」的旧口径，`--migrate` 的既有行为与输出
**逐字未变**。唯一不可逆的是用户主动跑迁移本身——那由既有的整目录备份兜底。

**Blast Radius:** 小-中 —— 改的是 CLI 的分支接线与一段用户可见文案；迁移链、形态步骤、备份 / 事务 /
审计引擎一行未改。放宽的是别名的**版本门**（`[3, SCHEMA_VERSION]`），因此半改名库与 `3`…`11` 的旧库
现在能被 `--repair-targets` 推进——这正是本票的目的，也是唯一扩大了的可达面。

- **不做**（票面 Constraints）：不改链上九步与形态步骤的行为；不改 `--migrate` 与 `--dry-run` 的既有行为；
  不 bump `SCHEMA_VERSION`；不新增退出码。
- **未改**：`app/alibot/db.py`、`app/alibot/migrate.py`、`app/tests/test_target_scope.py`（一个字节未动）、
  历史取证面 `app/specs/issue-22/24/35/37-*.md`、`app/alibot/studio/**`、`alibot-design-system/**`、
  `docs/adr/**`、issue 正文。
- 登记遗留（见 Answer §4）：`CONTEXT.md` 的备份词条与「形态步骤」词条超出本票授权的两处；`repair_target_rows`
  等已是 test-only，删除时机与 ADR-0015 §五一致（随别名下线）。
