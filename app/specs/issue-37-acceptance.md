# 实施票 #37 验收取证：拒绝旧库 + 手动修复入口

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。

> 阅读说明：本票是**缺陷修复票**，取证重心有三处——
> （a）**红证据**：在 base `6d4e6f8` 的隔离副本上复现「v4 代码打开 v3 库 ⇒ 静默半改名」；
> （b）**绿证据**：同一构造库用修复后代码打开 ⇒ 拒绝启动、库一个字节未改；
> （c）**追加范围**：一个显式、幂等、可自校验的手动修复入口。
>
> 所有取证都跑在**独立临时数据目录**上（`/tmp/issue37-*`，**不是** `~/Alibot`）。基线副本
> `/tmp/issue37-base`（`git worktree add --detach` 在 `6d4e6f8` 上）用于取红侧读数；
> 修复入口则借助 `app/tests/` 里的 `tmp_path`，不依赖任何临时目录长期存在。

## Answer

**做了什么**：按票面把「v4 代码打开旧库」从**静默半改名**改成**拒绝启动**。`Database` 在**任何
schema 变更之前**先读 `schema_meta.schema_version` 与本代码的 `SCHEMA_VERSION` 比对，不等就抛
`SchemaVersionMismatch`（版本号缺失但库里有业务数据同样拒绝——那不是空库）；拒绝路径下不建表、
不写版本号、不改任何行，实测库文件 **sha256 一致**。启动路径（`__main__.main`）捕获该错误，把
「库文件路径 + 库里的版本 + 本代码需要的版本 + 一句指引」打到 **stderr** 并以退出码 **3** 结束。
按用户裁定的追加范围，另交付 `python -m alibot --repair-targets` 这个**显式、幂等、可自校验**的
手动修复入口，把已被旧版代码半改名的 v4 库补齐。

**发现了什么**：四件值得单列的：

1. **事故处置建议的三条语句会留下一个更隐蔽的缺陷**。`~/Alibot` 的事故现场里
   `channel_budget` 有 1 行（`epoch_seq=7`、**`used=3`，即已烧满**），而
   `target_budget` 是空的。只补 `target` / `target_member` / `target_read` 的话，
   `GET /api/channels/{id}/budget` 会从 **404** 变成 **200 而 `used` 归零**——那不是「少一行」，
   而是把「预算已耗尽」这条事实**静默抹掉**。因此本票多补 `target_budget`（§3.3 有前后对照）。
2. **票面写的基线 194 与实测不符**：base `6d4e6f8` 实测是 **196 passed, 1 skipped**（§2.1）。
   差额与本票无关，但「194」这个数在票面出现多次，故如实记录。
3. **`Database(...)` 不能用于「先看一眼库长什么样」**：它的 `_initialize()` 会立刻
   `CREATE TABLE IF NOT EXISTS`，会把「某张表缺失」这个**正要诊断的事实**在诊断前抹掉，而且那是
   一次 schema 变更。因此新增 `Database.open_without_initializing(...)`（同一道版本闸门、
   不做初始化）供修复入口使用（§2.4）。
4. **`PRAGMA journal_mode = WAL` 不是连接级开关，会改写库文件头（review 发现的真缺陷）**：
   初版把它与 `foreign_keys` / `busy_timeout` 一起放在版本闸门**之前**。对一个**非 WAL** 的旧库，
   拒绝启动这条路依然把它改成了 WAL——文件 sha256 由 `d75adcdf…` 变 `250bc5ae…`，即「旧库一个
   字节不改」在那条路径上并不成立。**为什么初版没发现**：取证用的 v3 fixture 本身已是 WAL 形态，
   该 PRAGMA 成了 no-op；而「逐字节」当时只有逻辑快照断言（表内容 + 版本号），文件头不在它视野里。
   修法与红→绿证据见 §5.3（Spec 轴 review 指出，已补 6 条参数化回归用例，在 base 上是红的）。

**留下了什么**：修复入口只处置「v4 库里 channel 型 target 缺失」这一种情形；`schema_version != 4`
时与主修给出**同一个答复**（拒绝）。它**不删除**旧表 `channel_member` / `channel_read` /
`channel_budget`（保留原样以便回退），也不碰 dm / task 型 target。`app/specs/issue-24-acceptance.md`
的 §S7 遗留据此可以关闭（该文件本身一字未改）。真正的 `~/Alibot` 修复由用户在确认后自己执行。

### 状态流转

- 改动落在分支 `issue-37-reject-old-db`，worktree 干净（`git status --short` 为空）。
- 合并（G-merge）由 coordinator 执行；本票不自行合并、不自行改 issue 的 label/state。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-37-reject-old-db`（Orca 独立顶层 worktree）
- 分支：`issue-37-reject-old-db`
- 基线：`6d4e6f8`（main HEAD，本票 base）
- 采集环境：macOS，Python 3.11.13，SQLite（`check_same_thread=False`，WAL，`foreign_keys=ON`）
- 票标题（开工时用 `gh issue view 37` 核对）：

> 修复票：v4 代码打开 v3 旧库会静默半改名（列表 200、点进去全 404）——改为版本不匹配时明确拒绝启动

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.pi/agent/skills/diagnosing-bugs/SKILL.md`（前置 skill），依其流程执行：
**Phase 1 先建反馈回路**（不先猜原因）→ Phase 2 复现并最小化 → Phase 3/4 假设与探针 →
Phase 5 红→绿（回归用例**先**在 base 上跑到红）→ Phase 6 清理。收尾阶段另读
`/Users/apple/.pi/agent/skills/code-review/SKILL.md`（双轴 review，两个并行子代理，§5）。

**Phase 1 的回路**（一个命令、秒级、可红可绿）：

```sh
# 造 v3 库 → 用「被测代码」打开 → 比对打开前后的版本号与各表行数
.venv/bin/python make_v3_fixture.py /tmp/issue37-work/v3db
PYTHONPATH=<被测 app 目录> .venv/bin/python red_probe.py /tmp/issue37-work/<副本>
```

它在 base 上红（§1）、在 head 上绿（§3），且断言的是**用户的确切症状**（列表 200 / 点进去 404 /
版本号被改写 / 新表建出来是空的），不是「跑起来没报错」。

## 1. 红证据（修复前复现，base `6d4e6f8`）

`v3 库`由 **v3 源码**（`/tmp/issue37-v3`，`23e9ed0`，`SCHEMA_VERSION = 3`）生成：owner +
1 个 agent、1 个频道 `dev`、2 行 `channel_member`、**12 条消息**、2 行 `channel_read`、
1 行 `channel_budget`；`schema_version = 3`。

```text
$ cd /tmp/issue37-work && rm -rf red && cp -R v3db red
$ PYTHONPATH=/tmp/issue37-base/app .venv/bin/python red_probe.py /tmp/issue37-work/red
== 代码里的 SCHEMA_VERSION = 4 ==
GET /api/channels -> 200 {"channels": [{"id": "ch_45cb668ecca4", "name": "dev",
     "description": "开发频道", "last_seq": 12, "last_read_seq": 0, "unread_count": 0}]}
GET /api/channels/ch_45cb668ecca4/members -> 404 {"error":{"code":"not_found","message":"target 不存在：ch_45cb668ecca4"}}
GET /api/channels/ch_45cb668ecca4/budget -> 404 {"error":{"code":"not_found","message":"target 不存在：ch_45cb668ecca4"}}
POST /api/channels/ch_45cb668ecca4/messages -> 404 {"error":{"code":"not_found","message":"target 不存在：ch_45cb668ecca4"}}

== 打开后（同一个库文件）==
  "schema_version": "4",                      # ← 被静默改写
  "row_counts": {
    "channel_member": 2, "channel_read": 2, "message": 12, "channel_budget": 1,   # ← 旧表数据还在
    "target": 0, "target_member": 0, "target_read": 0, "target_budget": 0         # ← 新表建出来但是空的
  }

== 差异 ==
schema_version: 3 -> 4
新增表: ['target', 'target_budget', 'target_member', 'target_read']
  新表 target 行数 = 0
  新表 target_budget 行数 = 0
  新表 target_member 行数 = 0
  新表 target_read 行数 = 0
```

**症状与票面缺陷表逐条对上**：`schema_version` 被改写成 4（旧库被就地改动）、四张新表被建出来
但全空、旧表数据仍在、`GET /api/channels` 返 **200** 看起来正常，而成员 / 预算 / 发消息 / 标读
**全部 404**。即「列表页看着正常、点进去全部失效」。

### 1.1 回归用例在 base 上**确实是红的**（Phase 5 纪律）

新增用例若在 base 上不能红，它就没有锁住这个 bug。实测（把新用例原样挂到 base worktree，
仅用同名替身类替代尚不存在的 `SchemaVersionMismatch`）：
```text
$ cd /tmp/issue37-base && PYTHONPATH=/tmp/issue37-base/app .venv/bin/python -m pytest app/tests/test_schema_version_gate.py
13 failed, 4 passed in 60.40s

$ ... ::test_older_or_unknown_version_is_refused_and_the_file_is_untouched
E  Failed: DID NOT RAISE SchemaVersionMismatch
3 failed in 0.08s
```

红的是**全部 13 条拒绝断言**：版本 3 / 5 / 垃圾值、版本缺失但有数据、拒绝先于建应用、幂等拒绝、
六条非 WAL 逐字节未改（`journal_mode ∈ {delete,truncate,persist}` × 版本 `{3,5}`）、CLI 非 0 退出；
绿的 4 条是「正常路径不回归」那几条——它们在两版上都该绿，符合预期。

其中 CLI 那条在 base 上的失败方式是 **`subprocess.TimeoutExpired`（60 秒）**：进程不但没拒绝，
还真把 HTTP 服务**起起来并对外监听**了（stderr 里有 `Uvicorn running on http://127.0.0.1:52099`）。
这正是「旧库被静默接受」的另一种可见形式，也说明该用例驱动的确实是启动路径。

## 2. 实现要点（与票面 Change 逐条对应）

### 2.1 闸门位置：在**任何** schema 变更之前

`db.py` 的 `_initialize()` 第一句从 `executescript(SCHEMA)` 变成 `_assert_schema_compatible()`：

| 库的形态 | 判据 | 处置 |
| --- | --- | --- |
| 库文件不存在 / 无业务数据（版本号缺失或就是本版） | `stored is None and not _has_business_data()` | 放行，由 `_initialize` 建库 |
| 版本号 **等于** `SCHEMA_VERSION` | `stored == str(SCHEMA_VERSION)` | 放行（既有路径一字不变） |
| 版本号 **不等于** `SCHEMA_VERSION` | 其余全部 | 抛 `SchemaVersionMismatch` |
| 版本号**缺失但有业务数据** | `stored is None and _has_business_data()` | 同上（这不是空库） |

「空库」的判据是一张显式表清单 `BUSINESS_TABLES`：**新旧两代的名字都在里面**——版本号缺失时
无法从版本号判断库来自哪一代，所以只能看「任意一张业务表有没有行」。用一个 13 项常量而不是
按版本分支，是为了让「什么算有数据」只有一个落点。

### 2.2 报错可读且可操作

`SchemaVersionMismatch._render()` 产出（实测输出）：

```text
拒绝启动：数据库的 schema 版本与本代码不匹配。
  库文件：/tmp/issue37-work/green/alibot.db
  库里的版本：3
  本代码需要的版本：4
  本版不做数据迁移：请先备份该数据目录，再换一个数据目录启动（例如 --data-dir <另一个目录>）。
```

四项要求（库路径、库里的版本、本代码需要的版本、一句指引）齐全。

**它为什么不是 `DomainError`**：`DomainError` 映射成 HTTP 错误响应（400/404），而这条事实发生在
**启动路径**上、是进程级的拒绝。分开两个异常类型，就是「不能只在 HTTP 层返 500」这条要求的结构性
保证——HTTP 层拿不到它，因为进程根本不会开始监听。

### 2.3 启动路径：非 0 退出码 + stderr

`__main__.main()` 里 `create_app(...)` 包在 `try/except SchemaVersionMismatch` 内，命中就打 stderr
并返回 `DATA_NOT_ACCEPTED_EXIT_CODE = 3`。**不捕获更宽的异常**：其它启动失败（数据目录不可写等）
的堆栈比一句包装过的话有用，不该被吞掉。`--repair-targets` 是同一处的并列分支，因此**正常启动
路径不可能经过修复逻辑**（§3.5 有结构证据）。

### 2.4 `open_without_initializing`：为什么必须新增它

修复入口要先看清「某张表到底在不在、里面有多少行」才能决定补什么。用 `Database(...)` 做不到：
它的 `_initialize()` 会立刻把缺的表 `CREATE IF NOT EXISTS` 出来——

- 把「表缺失」这个**正要诊断的事实**在诊断之前抹掉；
- 而且那已经是一次 schema 变更，违反了「先诊断、后写入」的顺序。

因此新增类方法 `Database.open_without_initializing(data_dir)`：建立连接、过**同一道版本闸门**、
**不**做初始化。修复入口用它（§3.3），于是「版本不符 ⇒ 连看都不让看」与「修复只在确认形态之后
才写」两件事同时成立。它复用同一个 `_connect()`，所以连接参数与运行时完全一致。

## 3. 绿证据（修复后）

### 3.1 同一个构造库：拒绝启动 + 非 0 退出码 + stderr

```text
$ cd /tmp/issue37-work && rm -rf green && cp -R v3db green
$ PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --data-dir /tmp/issue37-work/green --port 51999
拒绝启动：数据库的 schema 版本与本代码不匹配。
  库文件：/tmp/issue37-work/green/alibot.db
  库里的版本：3
  本代码需要的版本：4
  本版不做数据迁移：请先备份该数据目录，再换一个数据目录启动（例如 --data-dir <另一个目录>）。
退出码 = 3
```

### 3.2 旧库**一字未改**（可断言：逐项 + sha256）

```text
$ # 打开前把版本号与各表行数存一份，打开后重新采一遍比对
打开前: {"version": "3", "counts": {"agent_config": 1, "channel": 1, "channel_budget": 1,
         "channel_member": 2, "channel_read": 2, "member": 2, "message": 12,
         "remote_consent": 0, "round_outcome": 0, "schema_meta": 1}}
打开后: {"version": "3", "counts": {……逐字相同……}}

逐项一致 = True
schema_version 未改 = True
没有多出 target* 表 = True

$ shasum -a 256 /tmp/issue37-work/v3db/alibot.db /tmp/issue37-work/green/alibot.db
ff808f46f8adf4ce689653d97ba78f8e63e43995dcfacc7777c3e72891a93889  /tmp/issue37-work/v3db/alibot.db
ff808f46f8adf4ce689653d97ba78f8e63e43995dcfacc7777c3e72891a93889  /tmp/issue37-work/green/alibot.db
```

库文件 **sha256 逐字节相同**，且 `green/` 下没有多出任何文件（WAL/`-shm` 是 SQLite 连接期的正常
伴随文件，`journal_mode` 也已在 base 上单独核实不改动库文件）。

### 3.3 追加范围：修复入口在真实事故现场上有效

用 `~/Alibot` 的**副本**（`/tmp/orch-alibot/repair-test`）还原出事故形态：`dev` 的 target
作用域数据被搬回旧表名、`dev2` 保持已被手工补好的状态。

```text
=== 事前：dev 点进去全 404、dev2 正常 ===
  dev    members=404 messages=0
  dev2   members=200 messages=0

=== 执行手动修复入口 ===
$ PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --repair-targets --data-dir /tmp/issue37-work/e2e
目标库：/tmp/issue37-work/e2e/alibot.db

本次补齐的行：
  + target: 1 行
  + target_member: 3 行
  + target_read: 3 行
  + target_budget: 1 行
本次共补 8 行。

行数（前 → 后）：
  target               1 → 2      (+1)
  target_member        3 → 6      (+3)
  target_read          3 → 6      (+3)
  target_budget        0 → 1      (+1)
  channel              2 → 2
  channel_member       3 → 3
  channel_read         3 → 3
  channel_budget       1 → 1

自校验通过：
  ✓ 每个频道都有 channel 型影子行
  ✓ channel_member 的每一行都在 target_member 里
  ✓ channel_read 的每一行都在 target_read 里
  ✓ channel_budget 的每一行都在 target_budget 里
退出码 = 0

=== 事后：dev 恢复可读 ===
  dev    members=3 messages=12 budget=200 used=3
  dev2   members=3 messages=0  budget=200 used=0

=== 第二次跑（幂等）===
目标库：/tmp/issue37-work/e2e/alibot.db
已是最新：没有需要补齐的行（本次改动 0 行）。
退出码 = 0
```

`dev` 的 12 条消息、3 个成员、未读位置**全部回来**，`dev2` 不受影响；第二次跑 **0 改动**。

**`used=3` 是本票的关键差异**：`dev` 的世代已烧满（`channel_budget.used = 3`）。若按事故处置建议
只补三张表，`budget` 会返 200 而 `used=0`——把「已耗尽」伪装成「还没用过」。多补 `target_budget`
之后 `used=3` 被如实恢复。这就是本票比建议的语句多一条的理由（也是 §Answer「发现了什么」第 1 条）。

### 3.4 正常路径不回归

```text
$ .venv/bin/python -m pytest
228 passed, 1 skipped in 102.17s
        # 基线 6d4e6f8 实测：196 passed, 1 skipped
        # 本票新增 32 条：test_schema_version_gate.py 17 条 + test_target_repair.py 15 条
        # **既有用例零改动**（§3.4.1）
        # skipped = 默认跳过的真模型冒烟测试（test_agent_smoke.py，本票未碰）
```

**空目录首次启动成功**（真进程 + 真 socket）：

```text
$ PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --data-dir /tmp/issue37-work/fresh --port 52001
alibot 工作台： http://127.0.0.1:52001/studio/
数据目录（备份即复制该目录）： /tmp/issue37-work/fresh
$ curl -s -w "  HTTP %{http_code}\n" http://127.0.0.1:52001/api/health
{"status":"ok","member_id":"owner","data_dir":"/tmp/issue37-work/fresh"}  HTTP 200
```

**v4 库再次启动成功**：

```text
$ PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --data-dir /tmp/issue37-work/fresh --port 52002
$ curl -s -w "  HTTP %{http_code}\n" http://127.0.0.1:52002/api/health
{"status":"ok","member_id":"owner","data_dir":"/tmp/issue37-work/fresh"}  HTTP 200
```

#### 3.4.1 既有用例**未作任何修改**通过（票面第一/第四验收项）

```text
$ git diff --stat 6d4e6f8...HEAD -- 'app/tests/*.py' | grep -v test_schema_version_gate | grep -v test_target_repair
(空)
```

既有测试文件**零改动**：diff 对 `app/tests/` 只列出两个**新文件**，没有任何 `M`。

#### 3.4.2 票面写的基线 194 与实测 196 的差异

票面多处写「基线 194 passed, 1 skipped」。**实测 base `6d4e6f8` 是 196 passed, 1 skipped**
（`cd /tmp/issue37-base && PYTHONPATH=... .venv/bin/python -m pytest`）。差额来源未在票面记录，
与本票改动无关；本票按「既有用例原样通过」的实质要求执行（零改动、全绿），并在此如实记录该差异。

**关于「194」的说明**：本票新增 32 条，228 − 196 = 32，账对得上。若以票面的 194 为基准，
则差额是 196 − 194 = 2 条既有用例的计数出入，不是新增用例的数目。

### 3.5 「默认不执行」的结构证据（不只是「碰巧没调用」）

```text
$ .venv/bin/python -c "import sys; import alibot.api; print('alibot.repair' in sys.modules)"
False
```

`create_app` 的模块图里**没有**修复模块：启动路径不 import 它，因此不存在「哪天有人顺手加一句调用」
的隐蔽路径。另有一条用例 `test_normal_startup_never_repairs_anything` 钉住行为面：起服务后半改名的
库**照旧坏着**（仍 404、库里一行没变）。

### 3.6 保证测的是**本 worktree** 的代码（票面提醒的坑）

本 worktree 的 `.venv` 是指向主仓的符号链接，其 editable 安装的 `.pth` 指回**主仓** `app/`：

```text
$ .venv/bin/python -c "import alibot; print(alibot.__file__)"
/Users/apple/orca/alibot/app/alibot/__init__.py          # ← 不带 PYTHONPATH 会测到主仓代码

$ PYTHONPATH=<worktree>/app .venv/bin/python -c "import alibot; print(alibot.__file__)"
/Users/apple/orca/workspaces/alibot/issue-37-reject-old-db/app/alibot/__init__.py   # ← 本 worktree
```

因此**所有**真进程冒烟（§3.1、§3.3、§3.4、§3.5）与 §1 的红侧探针都显式带 `PYTHONPATH`，并在
报告里给出这一对照。§1 用 `PYTHONPATH=/tmp/issue37-base/app`、§3 用 `<worktree>/app`，
两者在同一台机器上给出**相反**的结论（红 vs 绿），这本身就是「确实换成了被测代码」的证据。
两条 subprocess 用例（CLI 拒绝、CLI 修复）在 `env=` 里显式传 `PYTHONPATH`，不继承外部环境。

## 4. 质量门禁（G-impl 四条全跑）

```text
$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed!
64 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 31 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
0 errors, 0 warnings, 0 informations
```

### 4.1 pi-lens / semgrep 的两类误报（已核实为基线事实，不改代码）

**（a）`Import "fastapi.testclient" / "pytest" / "uvicorn" could not be resolved`。**
pi-lens 自带的 `pyright-langserver` 的 `resolvedCwd` 是 `app/`，且不读仓根 `pyrightconfig.json` 的
`venvPath/venv`，因此解析不到 `.venv`。判定依据：**未被本票改动的 baseline 文件报同一个错**——

```text
$ # app/tests/test_empty_workspace.py 是既有文件（git status 显示未改动）
app/tests/test_empty_workspace.py:13:6: error[Pyright] (reportMissingImports):
    Import "fastapi.testclient" could not be resolved
```

三个包在项目 `.venv` 里都可正常导入（`pytest 9.1.1` / `fastapi 0.143.0` / `uvicorn 0.54.0`），
且票面指定的权威 pyright 命令读的是仓根配置、输出 **0 errors**。故不改代码。

**（b）`Potential SQL injection sink`。** 这条规则在 `#24` 的验收件里已被裁决为误报
（`app/specs/issue-24-acceptance.md` §6.3 第 4 条：该规则是 **SQLAlchemy 专用**，本仓用 `sqlite3`、
所有值经命名/位置占位符绑定）。本票仍做了两件事以把入口收得更紧：**所有** SQL 都是模块级常量
（与 `db.py` 的 `AGENT_SELECT_SQL` / `UNREAD_COUNT_SQL` 同一体例），`execute()` 的实参不再出现
循环变量或运行时拼接；`grep` 全文无 `execute(f"` / `execute(f'` / `%` 拼接。规则仍会命中
「变量作为第一个实参」这一形态（`conn.execute(_CONST, {...})` 亦然），而这恰是参数化调用的正常写法。

**（c）ast-grep `unchecked-numeric-parse-python`。** 全仓基线同规则告警（`db.py` 有十余处），
非本票引入；改动未新增该形态。

## 5. 双轴 code-review（Standards + Spec）

照 `/Users/apple/.pi/agent/skills/code-review/SKILL.md`：两个**并行**子代理，各自独立上下文，
本机聚合、不合并两轴结论。

- 固定点：`6d4e6f8`（merge-base）；diff 命令：`git diff 6d4e6f8...HEAD`
- Standards 轴的标准来源：`AGENTS.md`、`CONTEXT.md`（§ Language）、`app/README.md`、
  `app/pyproject.toml`（ruff 配置）、`mypy.ini`、`pyrightconfig.json`、
  `app/specs/issue-24-acceptance.md`（本仓自身的取证体例）、`docs/adr/*.md`，外加 Fowler ch.3 的
  smell 基线（仅作判断项，仓内成文标准优先）。
- Spec 轴的正本：`gh issue view 37`（正文 + 用户裁定的追加范围评论）。

<!-- REVIEW_RESULTS -->

### 5.1 Standards 轴

**结论：OK with notes（P1 建议合入前修，一行级改动）。**

核验通过项：中文讲解 WHY 的注释密度与风格达标（无一处「念代码」的 WHAT 注释）；
`repair.py` 的 `except Exception: … raise` 与 `db.py` 既有 `_begin/_commit/_rollback` 体例一致
（回滚 + 重抛，属**正当**的宽捕获）；两个新测试文件沿用 `conftest` 体例。

| # | 级别 | 内容 | 处置 |
| --- | --- | --- | --- |
| S1 | **P1（硬违规）** | `db.py` 与 `test_schema_version_gate.py` 把表名 f-string 直插 `execute`；本仓自述体例明写「不把拼接写进 `execute` 的实参」，同一 PR 内三种写法自相矛盾 | **已修**：全部改为模块级常量字典 + **下标取常量**（`BUSINESS_COUNT_SQL[table]` / `_ROW_COUNT_SQL[table]` / `_REPAIR_SQL[table]` / `_CHECK_SQL[label]`）；`grep` 全文已无裸变量入 `execute` |
| S2 | P2（Duplicated Code） | `repair.py` 三段注释逐字重复 | **已修**：合并为一块 |
| S3 | P2（注释与代码矛盾） | 注释称「不放进元组再循环取用」，而下方正是元组 + 循环 | **已修**：按事实改写 |
| S4 | P2（Middle Man） | `_check_label` 只返回 `check[0]` | **已修**：删除，改用生成式 |
| S5 | P2（封装契约） | 直取 `database._conn` 并自行 `BEGIN/COMMIT/ROLLBACK`，绕过 `_begin/_commit/_rollback` 与 `self._lock`（类文档承诺「写路径串行化」） | **已修**：改用容器既有事务助手并持它的锁 |
| S6 | P2（文档漂移） | README 数据模型表仍写 `channel_*` 旧名 | **记遗留**：属 #24 已记遗留（§6），且正文表改写不在本票 ownership；本票只新增一句升级指引 |

### 5.2 Spec 轴

**结论：OK with notes（P1 为自校验口径的窄缺口）。**

核验通过项：需求 1（三分支 + 版本缺失但有数据也拒绝）、需求 2（报错四项 + stderr + 退出码 3）、
需求 3（快照断言与规格括号口径一致）、需求 6（`sys.modules` 结构证据）、需求 7/8/9（幂等、
版本复用同一闸门、三组用例齐备）均**逐条落实**。

| # | 级别 | 内容 | 处置 |
| --- | --- | --- | --- |
| P1 | P1（部分实现） | 自校验只验**单向**：只证「每条 channel 都有影子行」，**未**反向证「每个 channel 型 target 都有频道行与成员行」，故「target<->channel 1:1」不成立也能通过 | **已修**：补两条反向检查（孤儿 channel 型 target / 无成员行的 channel 型 target）并入 `_CONSISTENCY_CHECKS` |
| P2 | **P2 → 实为真缺陷** | 「逐字节未改」在**非 WAL** 库上不成立：`PRAGMA journal_mode = WAL` 在闸门**之前**执行，它会改写库文件头（实测 sha256 由 `d75adcdf…` → `250bc5ae…`）。初版未发现是因为 v3 fixture 本身已是 WAL 形态（该 PRAGMA 成 no-op）。且「逐字节」原本只有逻辑快照断言，无 sha256 断言 | **已修**：拆出 `_apply_write_pragmas()` 在闸门**之后**调用（§5.3）；补 3×2 参数化用例断言 sha256 与文件清单逐字不变 |
| P2 | P2（未要求的副作用） | 空目录上跑修复入口会先建出一个空库再报「不是 v4 形态」并退出码 4 | **已修**：没有库文件直接空操作；「形态不对但无业务数据」也只报 0 行（只有「有数据却形态不对」才报错）；补两条用例 |
| P2 | P2（超量交付） | README 让加「一句」，实际加了 3 句 4 行 | **已修**：收紧为一句 |
| P2 | judgement | 多补 `target_budget` 是否 scope creep | **判定正当**（ADR-0004 三处改名之一，`used` 归零属静默丢事实），保留；新增 `open_without_initializing` 同样为「先诊断后写入」所必需 |

两轴共 11 条 findings（硬违规 1 条、真缺陷 1 条、其余为判断项与超量交付），**全部已修或记遗留，无遗留未处置项**。

### 5.3 review 发现的真缺陷：修复与红→绿证据

**缺陷**：`PRAGMA journal_mode = WAL` 在版本闸门**之前**执行。它不是连接级开关，而要把库文件头的
journal mode 从 `delete` 改成 `wal`——于是「拒绝启动」这条路径**改动了旧库**，违反票面
「不修改旧库一个字节」。

**为什么这么难发现**：取证用的 v3 fixture 本身已是 WAL 形态，该 PRAGMA 成了 no-op；
且 sha256 相等只覆盖了那一种形态。**换个非 WAL 旧库就露出来了**：

```text
# 修复前（把 PRAGMA 放在闸门之前）
打开前 = d75adcdf9401d81078f2f7a910fcccfa88113b5d3156ed31b709bf1f445fa060
退出码=3
打开后 = 250bc5aef3d7a76bf30c0de2ff85b595387d6f4261ec7bad0a75ecfa1d49388b   # ← 变了
逐字节一致 = NO

# 修复后（PRAGMA 移到闸门之后）
打开前 = d75adcdf9401d81078f2f7a910fcccfa88113b5d3156ed31b709bf1f445fa060
退出码=3
打开后 = d75adcdf9401d81078f2f7a910fcccfa88113b5d3156ed31b709bf1f445fa060
逐字节一致 = YES      # 且没有多出 -wal / -shm
```

新增的回归用例（`journal_mode ∈ {delete, truncate, persist}` × 版本 `{3, 5}`，共 6 条）
在 **base 上是红的**：

```text
$ cd /tmp/issue37-base && pytest app/tests/test_zz_gate.py
13 failed, 4 passed in 60.40s
FAILED ...test_older_or_unknown_version_is_refused_and_the_file_is_untouched[3]
FAILED ...[5]  FAILED ...[not-a-number]
FAILED ...test_version_missing_but_business_rows_exist_is_refused
FAILED ...test_the_refusal_happens_before_any_schema_change_not_just_a_500
FAILED ...test_refused_database_is_still_refused_on_the_second_attempt
FAILED ...test_refusal_leaves_the_file_byte_identical_even_when_it_is_not_already_wal[3-delete]
FAILED ...[3-truncate]  FAILED ...[3-persist]
FAILED ...[5-delete]    FAILED ...[5-truncate]  FAILED ...[5-persist]
FAILED ...test_cli_process_exits_non_zero_with_a_readable_stderr
```

**这条缺陷值得单独记下**：它说明「只对一个恰好处于理想形态的 fixture 取证」是不够的——
同一段拒绝路径在另一种 journal mode 下就会破要求。也说明「逻辑快照一致」不等于「文件未变」：
快照只读表内容与版本号，而文件头不在它的视野里，因此**必须**补 sha256 断言。

## 6. 交付物与收尾

- 提交：见 §7 的 head sha（worktree 干净）。
- PR：用本机 `pr` skill 的模板写 body（Summary / Evidence / Merge Danger），`--body-file` 创建；
  PR 号与 head 见 §7。
- `worker_done`：按 live preamble 的原命令发送，带真实 Task ID / Dispatch ID 与显式
  `succeeded`，并核对成功回执。

## 7. 收尾读数

```text
$ git log --oneline 6d4e6f8..HEAD
03c40ca docs(spec): #37 PR body 补 review 发现的真缺陷（WAL PRAGMA 改写文件头）与最终读数
c260c1b docs(spec): #37 验收件（红/绿证据、门禁读数、双轴 review 结论与逐条处置）
86e3b6e fix(db,repair): #37 处置 Spec 轴 review 的 findings（含一处真实缺陷）
8c9899d fix(db): #37 版本不匹配时拒绝启动旧库，并补一个显式的手动修复入口
8c7bfec docs(spec): #37 PR body（Summary / Evidence / Merge Danger，按本机 pr skill 模板）
1ea0624 docs(readme): #37 升级指引收紧为票面要求的「新增一句」，去掉未要求的细节
4a2e16b refactor(db,repair): #37 处置 Standards 轴 review 的 findings
$ git status --short      # 空
```

- **head sha**：以远端分支 `origin/issue-37-reject-old-db` 为准（不内嵌 sha：那种自指会在每次
  收尾提交后漂移，本仓 #35 已为此立过规矩，见 `f858ee7`）
- **PR**：[#38](https://github.com/whutlichao/alibot/pull/38)（`issue-37-reject-old-db` → `main`）
- **最终读数**：`228 passed, 1 skipped`（基线 196 + 新增 32；既有用例零改动）
- **`~/Alibot`**：全程未读写；`lsof` 显示持有者是开工前就存在的 v3 后端进程（PID 69482，
  启动于 22:00:28），本票未干扰它。
- **遗留**：`app/README.md` 的数据模型表仍列 `channel_member` / `channel_read` / `channel_budget`
  旧名（#24 已记 S8 遗留；该表的正文重写不在本票 ownership）；票面写的基线 194 与实测 196 有出入
  （§3.4.2 已记）。
