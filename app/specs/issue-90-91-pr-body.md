## Summary

`python -m alibot --migrate --data-dir <数据目录>` 从「只回答、不动手」变成**真的把旧库推到本代码的
Schema 版本**。本 PR 一次交付 **#90**（迁移引擎 + `3 → 4` 数据搬迁 + 整目录备份 + 单事务 + 审计 +
报告）与 **#91**（`4 → 5` … `11 → 12` 八步 + 迁移终态判据 + 接受范围 `[3, SCHEMA_VERSION]`）。

```text
app/alibot/
├── migrate.py     # 逐版注册表 + 执行器 + 对齐兜底 + 备份/审计/报告（新）
├── __main__.py    # --migrate 接受 [3, 12]；迁移失败 ⇒ 5（新码）
├── db.py          # 拒绝正文终态口径；共用 STAMP / SETTINGS 语句常量
└── repair.py      # 导出 CHANNEL_SCOPE_MOVES（3 → 4 与 --repair-targets 共用列映射）

migrate_data_dir(data_dir, from)
  backup_whole_data_dir            # 失败 ⇒ BackupError ⇒ 5、库零字节改动
  BEGIN IMMEDIATE
    run_chain(conn, from)          # 从库里版本逐步走、不跳步
      step.apply(conn)             # 显式改动（3→4 搬行；5→6 冲突检出；10→11 触发器替换 …）
      align_to_current_schema      # 表/列/索引/触发器/外键/默认行幂等补齐，只增不减
    stamp schema_version = 12
  COMMIT
  append_audit_record(schema-migrations.jsonl)
```

**留给后续票的挂点**：`run_chain` 只吃「一个**已打开**的库 + 源版本」，不碰 CLI 参数 / 数据目录 /
备份——#93 的预演在**库副本**上直接调它；#92 的链前形态步骤在 `STEPS` 之前插入（或加一个前置钩子），
不需要动编排层。

## Evidence

- **Before**（`main @ a7d12f3`）：v3 库跑 `--migrate` ⇒ 退出码 **3**、库里的版本仍是 3、正文写着
  「本版尚无可用迁移步骤」。新增验收文件 **27/28 红**（`.scratch/evidence/red-baseline.txt`）。
  **After**：同一批用例 **28/28 绿**；`3`…`11` 的每一个源版本都 ⇒ `0` 且终态 = 本代码新建的库
  （`.scratch/evidence/acceptance-readings.json`）。
- **质量命令**（仓库根）：`.venv/bin/python -m pytest` 原样 **4 failed / 410 passed / 1 skipped**
  （4 条是本机 `no_proxy`/`NO_PROXY` 注入带方括号 `[::1]` 的**已知环境噪声**，基线同样 4 红）；
  清掉代理 env ⇒ **414 passed / 1 skipped**。基线 `a7d12f3` 为 **393 collected**。
  `ruff check app` / `ruff format --check app` / `mypy` / `pyright` 全过；
  4 个 `node --test` 全过（11 / 43 / 9 / 13）。
- **单事务**：v4 库 + 存量 DM 重复行 ⇒ 退出 `5`，前面步骤建出的 `task` 表与 `message.task_ref` 列
  一起回滚，库逐项回到动手前形态（含 DDL）。**库被别的进程占住** ⇒ 同样 `5`（不是未捕获堆栈），
  台账留一条 `result=failed`。

## Merge Danger

**Door:** 单向 —— 用户库一旦跑过 `--migrate` 就被推进到 v12；本仓不提供降级，回退靠迁移前自动做的
整目录备份。

**Blast Radius:** 宽 —— 触及版本闸门的拒绝正文、`--migrate` 的整个答复矩阵、以及一条会**重建
`message` / `round_outcome` 表**以补当前 `SCHEMA` 外键的迁移路径。

- 三张旧表 `channel_member` / `channel_read` / `channel_budget` **原样保留**（回退面）。
- `--repair-targets` 行为**不改**（放宽别名 = #94）；`--migrate --dry-run` 未实现（#93）。
- 不 bump `SCHEMA_VERSION`；不新增退出码语义（`0` / `3` / `4` / `5`）。
- 基线 393 条测试里只有 **3 条中间态断言**被同步改写（v3/低版本从「拒绝」改为「接受」、拒绝正文
  终态口径、`no-migration-script` 断言）；其余一字未动。
