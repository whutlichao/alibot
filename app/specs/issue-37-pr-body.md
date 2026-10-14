## Summary

启动路径上加一道**版本闸门**：库的 schema 版本与本代码不一致时**拒绝启动**，且不改旧库一个字节。
另按用户裁定的追加范围，补一个**显式**的手动修复入口，处置已经被旧版代码半改名过的库。

```text
Database.__init__
  _connect
    PRAGMA journal_mode = WAL          # 只动连接参数与文件头，不写业务行、不改 schema
    PRAGMA foreign_keys / busy_timeout
  _initialize
+   _assert_schema_compatible          # ← 闸门：在任何 schema 变更之前
+     _stored_schema_version           #   读 schema_meta.schema_version（只读）
+     _has_business_data               #   版本号缺失时：任意业务表有行吗？（只读）
+     版本 ≠ SCHEMA_VERSION ⇒ raise SchemaVersionMismatch
    executescript(SCHEMA)              # ← 旧库走不到这里
    upsert schema_meta / owner 行
```

```diff
 python -m alibot --data-dir <目录>
   build_parser
+    --repair-targets                   # 与「起服务」并列的另一条分支，不是启动流程的一步
   main
     if not loopback: return 2
+    if args.repair_targets: return _run_repair(data_dir)     # 显式动作，exit 0/3/4
     create_app
+      except SchemaVersionMismatch: stderr + return 3        # 进程级拒绝，不是 HTTP 500
     uvicorn.run(...)                   # → 因为上面 return 了，旧库根本不会开始监听
```

新增文件与职责：

```text
app/alibot/
├── db.py            # + SchemaVersionMismatch、BUSINESS_TABLES/COUNT_SQL、版本闸门、
│                    #   _connect 拆分、open_without_initializing（诊断用：过闸门但不建表）
├── repair.py        # 新增：四条 INSERT OR IGNORE + 自校验，同一写事务内完成
└── __main__.py      # + --repair-targets、_run_repair、两个退出码
app/tests/
├── test_schema_version_gate.py   # 新增 11 条
└── test_target_repair.py         # 新增 13 条
```

**补齐四张表，不是三张**（票面建议的三条语句之外多补 `target_budget`）：事故现场的
`channel_budget` 里 `used=3`（世代**已烧满**）而 `target_budget` 是空的。只补 `target` /
`target_member` / `target_read` 的话，`GET /api/channels/{id}/budget` 会从 **404** 变成
**200 而 `used` 归零**——把「预算已耗尽」静默抹掉，比 404 更糟。故一并补齐并核验。

## Evidence

**Before（base `6d4e6f8` 代码打开 v3 库）** —— 静默半改名：

```text
GET /api/channels      -> 200  {"channels":[{"id":"ch_45cb...","name":"dev","last_seq":12,...}]}
GET  .../members       -> 404  target 不存在：ch_45cb668ecca4
GET  .../budget        -> 404  target 不存在：ch_45cb668ecca4
POST .../messages      -> 404  target 不存在：ch_45cb668ecca4

打开后（同一个库文件）：schema_version: 3 -> 4
  新增表 target / target_budget / target_member / target_read，行数全是 0
  旧表 channel_member=2 / channel_read=2 / message=12 / channel_budget=1 数据仍在
```

新增用例在 base 上**确实是红的**（回归测试若不能红就没有锁住这个 bug）：

```text
$ cd /tmp/issue37-base && pytest app/tests/test_schema_version_gate.py
7 failed, 4 passed in 60.31s
E  Failed: DID NOT RAISE SchemaVersionMismatch
```

**After（修复后代码打开同一个构造库）** —— 拒绝启动：

```text
$ PYTHONPATH=<worktree>/app python -m alibot --data-dir /tmp/issue37-work/green --port 51999
拒绝启动：数据库的 schema 版本与本代码不匹配。
  库文件：/tmp/issue37-work/green/alibot.db
  库里的版本：3
  本代码需要的版本：4
  本版不做数据迁移：请先备份该数据目录，再换一个数据目录启动（例如 --data-dir <另一个目录>）。
退出码 = 3

$ shasum -a 256 v3db/alibot.db green/alibot.db
ff808f46f8adf4ce689653d97ba78f8e63e43995dcfacc7777c3e72891a93889  v3db/alibot.db
ff808f46f8adf4ce689653d97ba78f8e63e43995dcfacc7777c3e72891a93889  green/alibot.db   # ← 逐字节相同
```

**追加范围：修复入口在 `~/Alibot` 的副本上（还原成事故形态）**

```text
事前：dev members=404 / dev2 members=200
$ python -m alibot --repair-targets --data-dir <副本>
  + target: 1 行   + target_member: 3 行   + target_read: 3 行   + target_budget: 1 行
  自校验 ✓ 每个频道都有 channel 型影子行 ✓ 成员行 ✓ 已读行 ✓ 预算行
事后：dev members=3 messages=12 budget=200 used=3   ← used=3 被如实恢复（建议的三条语句会读成 0）
再跑：已是最新：没有需要补齐的行（本次改动 0 行）。退出码 = 0   ← 幂等
```

**门禁四条 + 全量用例**

```text
$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed!              65 files already formatted
$ .venv/bin/python -m mypy
Success: no issues found in 31 source files
$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
0 errors, 0 warnings, 0 informations

$ .venv/bin/python -m pytest
220 passed, 1 skipped in 101.02s
        # 基线 6d4e6f8 实测：196 passed, 1 skipped（票面写 194，实测有出入，见验收件 §3.4.2）
        # 新增 24 条；**既有用例零改动**（git diff --stat 对 app/tests/ 只列出两个新文件）
```

「默认不执行」有**结构证据**，不只是「碰巧没调用」：

```text
$ python -c "import sys; import alibot.api; print('alibot.repair' in sys.modules)"
False
```

## Merge Danger

**Door:** two-way

可回退：纯新增的启动期校验 + 一个只在显式传 `--repair-targets` 时才跑的入口。回退本 PR 后
v4 库与空目录的启动行为回到基线（旧库重新变成「静默半改名」，即回到本 PR 修掉的那个缺陷）。

**Blast Radius:** startup-gate

- **启动期新增一个失败模式**：任何 `schema_version != 4` 的库从此**起不来**。这是本 PR 的目的，
  但它是**行为收紧**——若某处部署有版本号异常的库（例如手工改过 `schema_meta`），升级后会启动失败
  而非带病运行。报错文本已含库路径与两个版本号，可据此定位。
- **`Database` 的构造现在会抛新异常**：`SchemaVersionMismatch` **不是** `DomainError`（后者映射成
  HTTP 400/404）。任何直接构造 `Database` / `create_app` 的调用方需能接住它。本仓内部只有
  `create_app` 一处调用，已在 `__main__` 收口。
- **`repair-targets` 是写操作**：它会补 `target*` 四张表。**不删**旧表、不碰 dm/task 型 target、
  不改 `schema_meta`；自校验不通过则整体回滚。**不对原始库自动执行**，需用户显式触发。
- **未触碰** `~/Alibot`（本机真实数据目录）：全部取证在 `/tmp` 副本与 `pytest` 的 `tmp_path` 上；
  本机 8788 端口上那个 v3 后端进程全程未被干扰。
