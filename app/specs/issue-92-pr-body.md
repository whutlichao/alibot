## Summary

`python -m alibot --migrate` 在走逐版链**之前**先跑一个**不依赖版本号**的形态步骤：一个库的
`channel` 行在、而 channel 型的**影子行 / 成员 / 已读 / 预算**还留在旧表名
（`channel_member` / `channel_read` / `channel_budget`）里时，按 `repair.CHANNEL_SCOPE_MOVES` 的
**同一份**列映射一次补齐**四张**表，影子行最先落。它处置 **#37** 那类半改名库：
`schema_version` 已是新一代，按版本号走链会**跳过** `3 → 4`，只有形态步骤能把数据搬回来。

```text
app/alibot/repair.py    # CHANNEL_SCOPE_GAP_CHECKS：四条「旧表 → 新表」正向探测（判据唯一落点）
app/alibot/migrate.py   # channel_scope_gaps() 只读判定 + run_shape_step() 链前前置
app/alibot/__main__.py  # 版本已是 12 但形态有缺 ⇒ 不是「无事可做」，走同一条迁移编排
app/tests/test_shape_conditioned_step.py   # 14 条 CLI 真子进程验收（含 5…11 逐版）

run_chain(conn, from)
  run_shape_step(conn, from)         # 有缺才搬；无缺不动（改动 0 行、不进报告）
    channel_scope_gaps(conn)         # 只读：SELECT COUNT(*)，不写库一个字节
    _step_3_to_4(conn)               # 与逐版链 3 → 4 同一段代码、同一份列映射
    _align_to_current_schema(conn)   # 空链（from == 12）时终态对齐由这里落
  for step in plan_steps(from, 12)   # 逐版链照常推进
```

**判定只看形态、不看版本号**：`channel` 行在、影子行 / 成员 / 已读 / 预算任一项有缺 ⇒ 有活。来源表
不在 ⇒ 跳过（全新的当前版本库没有 `channel_*` 旧表）；旧表只是**回退面镜像**（数据早就在
`target_*`）⇒ 逐行比对后判成「无事可做」，不产生任何改动。**缺当前形态必须存在的表**仍走既有的
退出码 `4`（零写入、不建备份），形态步骤不碰它。

**#94 的复用点**：别名将来直接调 `migrate.run_shape_step`（或走 `migrate.migrate_data_dir` 的同一
编排）即可拿到同一份形态步骤；`--repair-targets` 的**版本门**仍归 #94，本 PR 未动它一个字节。

## Evidence

- **Before**（本票 base `main @ 9f3b008`，隔离 worktree）：新增验收文件 **11 红 / 3 绿**
  （`.scratch/red-baseline.txt`）——
  - `stored=4`、四张表有缺的半改名库跑 `--migrate` ⇒ 退出码 **5**
    （`FOREIGN KEY constraint failed`，链在把 `target` 空表接上外键时回滚），库停在 4；
  - 影子行在、成员 / 已读 / **预算**有缺 ⇒ 退出码 **0 但 `target_budget` 为空**
    （`assert [] == [('ch_dev', 1, 3)]`）——正是规格警告的「200 且 `used` 归零」假象；
  - 读数已是 `12`、表都在但影子行有缺 ⇒ 打印「无事可做」，**不搬**。
  - `stored=5..11`、数据仍在旧表名的库同样「0 但不搬」。
  **After**：同一批 **14/14 绿**（`.scratch/green-fixed.txt`）；真实报告（`.scratch/shape-step-report.txt`）：

```text
源版本：4 → 目标版本：12
  形态步骤（channel 型作用域数据补齐：影子行 / 成员 / 已读 / 预算）（不推进版本号）  改动 6 行
  4 → 5 … 11 → 12  （逐版链照常推进，各 0 行）
迁移完成：schema_version = 12；本次共改动 6 行。
```

- **端到端终态**：迁移后 `target_member` / `target_read` / `target_budget` 与旧表逐行对应、消息面可读，
  `target_budget = [('ch_dev', 1, 3)]`（**已耗尽预算 `used` 不归零**）；`Database(data_dir)` 可打开、
  `list_channels() == ['ch_dev']`、`GET /api/channels/ch_dev/budget` ⇒ `200` 且 `used = 3`；
  三张旧表原样保留。第二次跑 ⇒ `0`、sha256 不变、数据不变、**不新建备份**。
- **质量命令**（仓库根）：`pytest` 原样 **4 failed / 417 passed / 1 skipped**（4 条是本机
  `no_proxy`/`NO_PROXY` 注入带方括号 `[::1]` 的**已知环境噪声**，基线同样 4 红）；
  清掉代理 env ⇒ **428 passed / 1 skipped**。基线 `9f3b008` 实测清代理 env **414 passed / 1 skipped**。
  `ruff check app` / `ruff format --check app` / `mypy`（`--cache-dir=/tmp/t92-mypy`）/ `pyright`
  全过；4 个 `node --test` 全过（19 / 43 / 9 / 13）。

## Merge Danger

**Door:** 单向 —— 用户库一旦跑过 `--migrate` 就被推进到 v12；本仓不提供降级，回退靠迁移前自动做的
整目录备份。**但**本 PR 新增的形态步骤只做「`INSERT OR IGNORE` 四张 `target_*` 表」，不 `DROP`、
不改既有列。

**Blast Radius:** 中 —— 触及 `--migrate` 对**当前版本库**的判定：一个「读数已是 12、表都在、但
channel 型作用域行还留在旧表名里」的库会从「无事可做」变成「真跑一次迁移（含备份与审计）」；
健康库（含只是回退面镜像的库）行为不变（`0`、改动 0 行、不建备份）。

- 不改链上九步（`3 → 4` … `11 → 12`）；不 bump `SCHEMA_VERSION`；不实现 `--dry-run`（#93）；
  不放宽 `--repair-targets` 的版本门（#94）；不新增退出码（`0/3/4/5` 语义不变）。
- 三张旧表 `channel_member` / `channel_read` / `channel_budget` **原样保留**（回退面），不删、不双写；
  `BUSINESS_TABLES` 不动；`docs/adr/**`、`CONTEXT.md`、历史取证面 `app/specs/issue-22/24/35/37-*.md` 不动。
