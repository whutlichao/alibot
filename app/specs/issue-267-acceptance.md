# #267 长期记忆始终开启，新建 Agent 立刻有家目录 验收

- 状态：Done（门禁通过；**单向门**，保留 PR 由人类合并）
- Issue：https://github.com/whutlichao/alibot/issues/267
- 分支：`issue-267-impl`（base `main` = `2c73f52`）
- PR：https://github.com/whutlichao/alibot/pull/268
- 面：`app/alibot/{db,migrate,memory,service,api,agent_seam}.py`、`app/alibot/studio/{app,i18n}.js`、
  `app/tests/**`、`app/README.md`、`CONTEXT.md`、`docs/adr/0035-long-term-memory-is-always-on.md`、
  `docs/agents/agentscope-2*.md`（两处事实修正）
- Door：**单向门**——schema 迁移 + 删除持久化列 + 删除 API 字段。回滚只能恢复迁移前整目录备份。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1a | 新建 Agent 后 `<HOME>/.alibot/agents/<id>/` 与 `reme/` 立即存在；不创建 `AGENTS.md` / `mcp.json` / `skills/` | `WorkspaceService.create_agent` → `memory.memory_workspace_dir` | `pytest tests/test_agent_home_creation.py` → 2 passed（红→绿见下） |
| 1b | 迁移用例在无 `_step_24_to_25` 时失败，实现后通过 | `migrate._step_24_to_25` + `tests/test_memory_schema_step.py` | `pytest tests/test_memory_schema_step.py` → 3 passed（红→绿见下） |
| 1c | Studio 详情页无 `agent-field-memory_mode`、保存载荷无该键；基线上失败 | `studio/app.js` + `agent_detail_ui.test.cjs` | 基线 `scripts/red-evidence.sh main ...` → 51/53（2 failed）；实现后 53/53 |
| 2 | v24 库（`off` + `reme` + `NULL`）→ 25：`PRAGMA table_info` 不含 `memory_mode`，行数 / 其它列逐字保留；迁移链连续 | `migrate._step_24_to_25`、`STEPS`、`tests/_migration_history.py` | 见「迁移读数」；`pytest tests/test_migration_chain_guard.py tests/test_schema_migration.py` → 全绿 |
| 3 | `PATCH /api/agents/{id}` 带 `memory_mode` → 400 且不改任何字段；不带它时既有字段往返不变 | `api.AgentUpdate`（`extra="forbid"`）+ 端点不再传该参数 | `pytest tests/test_runtime_config.py` → 4 passed（1 failed → 4 passed） |
| 4 | 所有装配路径每轮装配 ReMe；运行环境段固定「已开启」；`global_home is None` 仍返回 `None` 并记日志 | `agent_seam._prepare_round` 无条件调用、`_runtime_environment_section(settings)` | `pytest tests/test_runtime_memory.py tests/test_global_rules.py` → 全绿 |
| 5 | `scripts/check.sh` 全量绿（最终 head 复跑） | 仓库 | 读数见「门禁读数」 |
| 6 | 双轴 code-review（Standards + Spec）与 findings 处理 | 本文件「双轴 review」 | 见下 |
| 7 | PR 已创建且 head = 最终提交；Door 写明单向门 | PR | 提交后回填 |

## 红证据（实现前）

### 1a. 家目录不存在（工作区，未实现）

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_agent_home_creation.py
FAILED tests/test_agent_home_creation.py::test_creating_an_agent_makes_its_home_and_reme_workspace
  AssertionError: 新建 Agent 必须立刻有家目录这一级
  where PosixPath('.../home0/.alibot/agents/mem_aa37af900aa0').is_dir = False
FAILED tests/test_agent_home_creation.py::test_a_home_creation_failure_is_logged_and_creation_still_succeeds
  AttributeError: module 'alibot.service' has no attribute 'memory_workspace_dir'
2 failed in 0.24s
```

### 1b. 迁移步骤不存在（工作区，未实现）

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_memory_schema_step.py
FAILED test_a_v23_library_is_accepted_and_reaches_v25_without_the_memory_mode_column
  AssertionError: 报告必须点明删列这一步
FAILED test_a_v24_library_drops_the_memory_mode_column_and_keeps_every_other_value
  AssertionError: 报告必须点明删列这一步
  assert '24 → 25' in '... 已是本代码的 schema 形态（版本 24），无事可做；不建备份。\n'
2 failed, 1 passed in 3.79s
```

v24 源库的读数直接说明「无步骤可做」：迁移入口把 24 当作终态，`agent_config` 仍带 `memory_mode`。

### 1c. Studio 基线（隔离副本，`scripts/red-evidence.sh main`）

```text
基线：main (2c73f52)
测试：app/tests/agent_detail_ui.test.cjs（取自当前工作区）
基线 app.js md5：93b816b4db632cdce4e37836ad302e77（与实现前工作区一致）
证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red.MK84EL
ℹ tests 53 / pass 51 / fail 2
✖ 就地编辑只写草稿：改一个字段后出现保存条，点保存一次性提交全部可写字段
✖ 长期记忆没有开关：详情页无该控件，保存载荷不含 memory_mode
结果：用例失败（红读数）
```

两条失败都落在断言本身（控件存在 / 载荷多一个键），不是装置错误。实现后同一文件 53/53。

### 1d. API 契约（工作区，未实现）

```text
$ ... -m pytest tests/test_runtime_config.py
FAILED test_memory_mode_is_no_longer_accepted_by_the_agent_update_contract
  assert 200 == 400（PATCH 带 memory_mode 被接受，role 也一起被改了）
1 failed, 4 passed in 0.43s
```

## 迁移读数

临时脚本（`/tmp/mig267.py`，不入库）用 `tests.test_schema_migration._build_library` 造源库并跑真实子进程
`python -m alibot --migrate --data-dir <目录>`。

### v24 → 25（本票主路径）

```text
迁移：.../data/alibot.db
源版本：24 → 目标版本：25
本次备份：.../data-backup-24-to-25-20261024T153448Z

实际执行的步骤（每步改动的数据行数）：
  24 → 25  long-term memory is always on (drop agent_config.memory_mode)  改动 2 行

迁移完成：schema_version = 25；本次共改动 2 行。
COLUMNS ['member_id', 'role', 'model_id', 'system_prompt', 'enabled', 'auto_claim', 'workspace_backend',
         'permission_mode', 'allow_local_shell', 'docker_image', 'web_access', 'context_window_messages',
         'context_trigger_ratio', 'react_max_iters', 'created_at', 'updated_at']
ROWS [('mem_null', '角色', 'openai:keep', '提示', 1, 1),
      ('mem_off', '角色', 'openai:keep', '提示', 1, 1),
      ('mem_reme', '角色', 'openai:keep', '提示', 1, 1)]
VERSION 25
```

`mem_off` / `mem_reme` / `mem_null` 三行都在，除 `memory_mode` 外的每列逐字保留（`test_memory_schema_step.py`
逐列比对完整 v24 行，包含 `web_access` / `workspace_*` / 三个运行时数值列）。`改动 2 行` = `off` + `NULL`。

### v23 → 25（受支持源，验证链）

```text
源版本：23 → 目标版本：25
  23 → 24  long-term memory to ReMe (superseded by 24 -> 25; chain continuity only)  改动 0 行
  24 → 25  long-term memory is always on (drop agent_config.memory_mode)  改动 1 行
V23_COLUMNS [...（无 memory_mode）]
V23_ROW ('mem_legacy', '旧角色')
```

v23 源的 `agentic` 行不再有独立语义（列马上被删），因此计入「存量档位不是 `reme`」的读数。

## 门禁读数

最终 head 前的代码（review 修复后的工作区）在仓库根跑 `scripts/check.sh`（退出 0、「门禁全绿」）：

```text
== pytest（全量）
1173 passed, 11 skipped in 706.22s (0:11:46)

== ruff check
All checks passed!
== ruff format --check
329 files already formatted
== mypy（仓库根）
Success: no issues found in 125 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test app/tests/*.test.cjs
ℹ tests 319 / pass 319 / fail 0
```

实现前的首轮全量（同一脚本）为 `4 failed, 1169 passed, 11 skipped`——4 条都是「全局层只被记忆工作区写入」
这一新事实的旧断言（`test_global_mcp` / `test_global_skills` ×2 / `test_home_isolation`），已按新契约改写
（只过滤/允许 `agents/<id>/reme/**`，技能与 MCP 层仍零创建）。

## 远端 CI 红 → 绿（follow-up：修 #268 门禁）

PR #268 的首个远端读数**不是**绿：本地 `scripts/check.sh` 全绿，CI 在 `f3c7d1b` 上红。

### 红读数（远端）

```text
run:   https://github.com/whutlichao/alibot/actions/runs/40109384338
head:  f3c7d1b220802897a5821fd9eb60942fe5e603f2
结果:  exit 1，14m9s，13 failed / 1169 passed / 2 skipped
```

13 条失败全部是 `pytest.PytestUnraisableExceptionWarning`，根对象是 pin 住的 ReMe 未关闭文件句柄：

```text
ResourceWarning: unclosed file <_io.TextIOWrapper name='.../site-packages/reme/components/tokenizer/stopwords' mode='r' encoding='utf-8'>
Exception ignored in: <_io.FileIO name='.../site-packages/reme/components/tokenizer/stopwords' mode='rb' closefd=True>
```

受害者（GC 落点，不是肇事者）集中在 `tests/test_global_mcp.py`（3）、`tests/test_global_rules.py`（6）、
`tests/test_global_skills.py`（4）；`app/pyproject.toml` 的 `filterwarnings = ["error"]` 把这条 warning
变成失败。

### 根因

- pinned `agentscope==2.0.10` 的 `ReMeMiddleware` 把嵌入应用的启动**懒放**在第一个 job 里
  （`_run_job` → `_ensure_started()`）；`close()` 只在 `_started` 为真时关应用。
- 本轮的检索是**可被取消的后台 task**：`on_reply` 开 `asyncio.create_task(self._search(...))`，
  收尾的 `finally` 对它 `cancel()` + `await`。取消若落在上游 `aiofiles` 的
  `AiofilesContextManager.__aenter__` 里（`_open` 正在 `run_in_executor` 里同步 `open()`），
  已经打开的原始文件就没人关：`__aexit__` 不会执行，`close()` 也救不了（应用还没标记 started）。
- 远端没有开 tracemalloc，取消的精确落点不可见；但本地按这一机制确定性复现出的 warning 与 CI
  逐字同类，且修复后同一读数消失。
- 该句柄只在 GC 时报 `ResourceWarning`，于是失败落在**之后任一条**测试上——CI 上就是 36–42%
  区间的那 13 条；本地是否复现取决于取消恰好落在哪个 await 上，是夹在时间窗里的竞态。

### 判据化复现（本地，修复前红）

正常跑全量在本机绿（1173 passed / 11 skipped；另加「每轮测试前后 `gc.collect()`」的仓库外插件
也是 1173 passed），因为取消没有落进那个窗口。因此把窗口放大成确定的：替换
`aiofiles.threadpool.sync_open` 为「先 set 事件再 sleep 0.3s」，等事件后取消一条检索。

```text
$ cd app && ../.venv/bin/python -m pytest \
    tests/test_runtime_memory.py::test_a_cancelled_assembly_does_not_leak_the_tokenizer_file
E       AssertionError: ReMe 的启动不能发生在可取消的后台检索里
FAILED tests/test_runtime_memory.py::test_a_cancelled_assembly_does_not_leak_the_tokenizer_file
ERROR tests/test_runtime_memory.py::test_a_cancelled_assembly_does_not_leak_the_tokenizer_file
  - pytest.PytestUnraisableExceptionWarning: Exception ignored in: <_io.FileIO
    name='.../reme/components/tokenizer/stopwords' mode='rb' closefd=True>
1 failed, 1 error in 0.70s
```

与远端 CI 的 warning 逐字同类（同一个 stopwords 句柄、同一个 `Exception ignored in`）。

### 修复（装配层收口，不改第三方、不改 pin）

`app/alibot/memory.py`：

- `assemble_memory` 返回前**确定性启动**：新增 `_start_middleware()` 调 pinned 2.0.10 的私有
  `ReMeMiddleware._ensure_started()`，并包一层 `asyncio.shield`——取消照旧传出去，但启动先跑完，
  再交给调用方 `close()`；启动失败则降级为「这一轮没有记忆」并记日志（不改变轮次结论）。
- 于是「启动」不再发生在任何可取消的后台检索里；CI 上那些轮次的检索任务只会 `run_job`，
  不再碰到 `aiofiles` 的开启窗口。
- 私有成员按 §2.3 的「wheel 实测」处置，并在偏离账本登记 **D13**（版本 pin、私有符号、再评估条件）。

同一命令修复后：

```text
$ cd app && ../.venv/bin/python -m pytest \
    tests/test_runtime_memory.py::test_a_cancelled_assembly_does_not_leak_the_tokenizer_file
1 passed in 2.70s
```

同一文件的装配用例同时钉住「返回前已启动」：`_FakeReMeMiddleware.started == 1`。

### 修复后读数

本地全量 `scripts/check.sh`（仓库根，退出 0）：

```text
== pytest（全量）
1174 passed, 11 skipped in 740.87s (0:12:20)

== ruff check
All checks passed!
== ruff format --check
329 files already formatted
== mypy（仓库根）
Success: no issues found in 125 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test app/tests/*.test.cjs
ℹ tests 319 / pass 319 / fail 0
```

`Closes #267` 与 Door=**单向门**不变；本 follow-up **不合并**。

## 双轴 review（Standards + Spec）

两个只读 reviewer 并行跑 `git diff main...HEAD`（单提交 `d73f2b4`），票面正本 = issue #267 正文，
标准来源 = `docs/agents/review-rules.md` 列的六份 + Fowler smell baseline。结论分轴记录，不互相抵消。

### Standards 轴

| # | Finding（文件:行） | 处理 |
| --- | --- | --- |
| S1 | `migrate.py` 的 `23 → 24` 已是 no-op，但 `STEPS` 标签与 `db.py` 版本注释仍写 `agentic -> reme` | **已修**：标签改 `long-term memory to ReMe (superseded by 24 -> 25; chain continuity only)`；`db.py` 注释同步说明本版不做映射 |
| S2 | `_rebuild_table(expressions=...)` 的最后调用点删除后成为死参数 | **已修**：删掉 `expressions` 参数、`AS` 改写分支与 `Mapping` import；docstring 改成「逐列原样复制」 |
| S3 | 返回值注释称「此前关闭」，但谓词会把 v23 的 `agentic` 计入 | **保留谓词（票面原式），已改口径**：`migrate` docstring / ADR-0035 明写「存量档位不是 `reme`；v23 旧档也计入」。理由见「决策 D2」 |
| S4 | 运行环境段声称「只写装配事实」，却固定写「已开启」 | **已修 docstring**：固定形态是产品决策（ADR-0035），本轮装配降级（未接线 / 不可建）不在这段表达；输出仍按票面固定 |
| S5 | Studio `agentConfigSection` 头注释仍列「长期记忆」 | **已修**：注释删词；README `23 → 24` 行改为「历史步骤 / 不重建、不改写」 |
| S6 | `_without_memory` 在两个测试文件里逐字重复 | **不修（判断题）**：两个用例族各自维护 `_global_root` 等同形 helper 是既有体例；抽到 `conftest.py` 会扩大测试装置的共享面，超出本票 |

### Spec 轴

| # | Finding | 处理 |
| --- | --- | --- |
| P1 | ADR-0035 §四只取代 ADR-0034 §一，没取代 §五（`23 → 24` 的 CHECK 重建与 `agentic → reme`），冻结正文与代码矛盾 | **已修**：§四补 ADR-0034 §五条目，写明「v25 起只保留链连续性、不改写取值」 |
| P2 | README `23 → 24` 同一行前后矛盾；步骤标签每次迁移都打印 `agentic -> reme` | **已修**：见 S1 / S5 |
| P3 | `.gitignore` 新增 `/app/logs/` 属票面外改动，且只遮蔽来源 | **已撤回**：`.gitignore` 无 diff；测试产物不入库。副作用记入「遗留」 |
| P4 | 返回值谓词把 v23 的 `agentic` 算作「由关变开」 | 与 S3 同一处置：**票面逐字写了该谓词**，不擅自改判据；口径与 ADR 改成事实描述 |

### 三轴 AgentScope

`docs/agents/agentscope-2.md` §1 红线未触碰：pin 不变、未新增 import、未引 1.x 符号、快照未改。
两处文档事实修正（`agentscope-2.md` §1.5 与偏离账本 D12）只把「`memory_mode=reme` 才装配 / 默认 `off`」
改成「每轮装配、没有开关」，并保留 ADR 引用。

## 字段换来源的调用点（`memory_mode` 的写路径 / 读路径 / UI / 测试）

| 面 | 旧来源 | 新来源 / 处置 |
| --- | --- | --- |
| 写路径（底层） | `Database.update_agent(memory_mode=…)` + `UPDATE agent_config ... memory_mode` | 形参、`runtime_values` 校验项、UPDATE 子句与参数全部删除 |
| 写路径（服务 / HTTP） | `WorkspaceService.update_agent(memory_mode=…)`；`AgentUpdate.memory_mode`；PATCH 传参 | 三处删除；带该键请求由 pydantic `extra="forbid"` → 400（整体不写） |
| 读路径 | `AGENT_COLUMNS ... c.memory_mode`；`_agent_payload["memory_mode"]` | 列清单与 payload 都删除；`GET` 载荷不再出现该键 |
| 装配 | `if agent.get("memory_mode") == "reme": assemble_memory(...)` | 无条件 `await assemble_memory(...)`；缺席只由 `global_home` / 工作区不可建决定 |
| 运行环境段 | `_runtime_environment_section(settings, memory_mode)` 两档 | 单参固定写 `_RUNTIME_MEMORY_REME`；`_RUNTIME_MEMORY_OFF` 删除 |
| Studio | `agentDraftFrom.memory_mode` / 复选框 / 保存载荷 / 两条词条 | 四处删除；DOM 探针 `agent-field-memory_mode` 不再存在 |
| 测试 | 往返、非法档、关闭不建目录、team 两档、UI fixture / 载荷 / i18n 键、`_seed` 参数 | 全部按真实行为改写；新增「PATCH 带键 → 400」「每轮都装配」「无控件 / 载荷无键」 |
| 迁移留档 | v24 的 `agent_config_v24` | 新增 `agent_config_v25`（v24 去掉两行注释与一列）+ `SOURCE_COMMITS[25]` + `VERSION_OBJECTS[25]` |

## 决策与遗留

- **D1（人定，已落地）**：长期记忆没有开关；每一轮装配 ReMe；新建 Agent 即建家目录与 `reme/`。
  见 `docs/adr/0035-long-term-memory-is-always-on.md`。
- **D2（实施取舍）**：`24 → 25` 的返回值保留票面原式
  `memory_mode IS NULL OR memory_mode <> 'reme'`。v24 源读数就是「`off` + `NULL` = 2 行」；v23 源的旧档
  `agentic` 也计入，因为该列在此步整体删除、`agentic` 不再有语义。改谓词会让票面判据与代码不一致，
  因此只改措辞、不改判据。
- **D3（实施取舍）**：`23 → 24` 在 v25 代码里成为 no-op（只 `_create_missing_tables`，返回 0）。
  当前 `SCHEMA` 已无该列，任何重建都会提前删列；保留注册项只为链连续，标签已写明 superseded。
- **D4（测试口径）**：`POST /api/agents` 的实际状态码是 **201**（票面写 200）；新用例按真实契约断言 201。
- **遗留 L1**：ReMe 的嵌入应用在**进程 cwd** 写 `logs/*.log`（上游行为）。长期记忆始终开启后，真实 seam
  的 pytest 也会产生 `app/logs/`；本票按票面范围未改 `.gitignore`，验证后已删除，产物不入库。
  真正的处置（配置 ReMe `log_to_file` / 指定日志目录）需要上游能力或单独开票。
- **遗留 L2**：`app/specs/issue-234/261/263-*.md` 等历史验收文档仍保留当时的 `memory_mode` 读数——按
  「不改写历史证据」保留；`agent-home-layers-design-notes.md` 同样一字未改，由 ADR-0035 §四引用取代。
