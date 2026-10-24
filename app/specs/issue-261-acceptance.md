# #261 长期记忆换到 ReMe 验收

- 状态：Done（待 coordinator 执行 G-merge）
- 基线：`b73d0a1`（`Merge pull request #260 from whutlichao/studio-dm-dock-toggle`）
- 分支：`issue-261-impl`
- 面：`app/alibot/{memory,agent_seam,workspace,runtime_files,db,migrate}.py`、
  `app/alibot/studio/{app.js,i18n.js}`、`app/pyproject.toml`、`app/tests/**`、
  `docs/adr/0034-reme-long-term-memory.md`、`CONTEXT.md`、`app/README.md`、
  `docs/agents/agentscope-2*.md`
- Door：**one-way**（schema 迁移 + 新运行期依赖 + 默认记忆语义变更）；人类手动合并。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 全量门禁绿（CI 跑同一脚本） | 仓库 | `scripts/check.sh` → 全绿：`pytest` **1163 passed, 11 skipped**（`app/tests/` 下 98 个 `.py` 用例文件，10:33）；`node --test app/tests/*.test.cjs` **316 tests / 0 fail**；`ruff check` / `ruff format --check` / `mypy`（124 files）/ `pyright` 全绿 |
| 2 | ReMe 装配参数正确：工作区 = `<global>/agents/<member>/reme`、`chat_model` = 本轮模型、`mode` 由 provider 工具能力决定、`top_k=5`、`memory_search` 在 toolkit、轮次结束 `close` 被 await | `memory.assemble_memory`；`agent_seam._prepare_runtime_options`；两处轮次 finally | `cd app && ../.venv/bin/python -m pytest tests/test_runtime_memory.py` → `8 passed`（装配参数、`static_control` 退化、关闭时序 `["reme","model"]`） |
| 3 | `off` 不装中间件、不建目录 | 同上 | 同上（`test_memory_off_creates_no_workspace_and_installs_no_middleware`） |
| 4 | 真实 `reme-ai` 可导入，`list_tools()` 返回 `memory_search`（不启动应用） | `tests/test_runtime_memory.py::test_real_reme_middleware_exposes_memory_search` | `pytest tests/test_runtime_memory.py -k real_reme` → `1 passed` |
| 5 | 迁移把 `agentic → reme`，新 CHECK 拒绝 `agentic` | `migrate._step_23_to_24`；`db.py` DDL；`_migration_history.py` v24 | `cd app && ../.venv/bin/python -m pytest tests/test_runtime_memory.py tests/test_runtime_config.py tests/test_memory_schema_step.py tests/test_schema_migration.py tests/test_migration_chain_guard.py` → `80 passed`（迁移语义见 `test_memory_schema_step.py`：旧档 → `reme`、`off`/`NULL` 保留、旧词 UPDATE 抛 `IntegrityError`） |
| 6 | PATCH 校验同步：旧词被 400 拒绝 | `db.update_agent` 用 `db.MEMORY_MODES` | 同上（`tests/test_runtime_config.py` 的非法值参数含 `agentic`） |
| 7 | Studio 表单读写 `reme`；刷新后仍勾选 | `studio/app.js`（草稿 / 读回 / 提交 / 输入事件）、`i18n.js` | `node --test app/tests/agent_detail_ui.test.cjs`（在 `check.sh --fast` 的 316 条内）+ 浏览器验收 |
| 8 | 真实 ReMe 冒烟：写入 → 卡片 → 新会话召回 | `/tmp/reme-smoke/smoke.py`（临时，不入库） | 见下「冒烟证据」 |
| 9 | Docker 成员家目录由 `rw` 改 `ro` | `workspace.docker_global_binds` | `cd app && ../.venv/bin/python -m pytest tests/test_docker_network_policy.py` → `16 passed`（含两条 bind 判据）；`test_docker_workspace.py` 在无 daemon 的机器上按既有 `requires_docker` 跳过 |
| 10 | `grep -R "agentic" app/alibot app/tests` 仅历史迁移面命中 | — | 命中三类：`app/tests/_migration_history.py`（历史 DDL 留档）、`migrate.py` 的 `23 → 24` 步骤（映射字面量）、`test_memory_schema_step.py` / `test_runtime_config.py`（迁移夹具与「旧词被拒」的反向断言）；其余零命中 |

## 门禁读数

```text
scripts/check.sh
== pytest（全量）
1163 passed, 11 skipped in 633.83s (0:10:33)
== ruff check / ruff format --check / mypy / pyright
all passed（mypy: 124 source files, no issues；pyright: 0 errors, 0 warnings）
== node --test app/tests/*.test.cjs
tests 316 / pass 316 / fail 0
门禁全绿（退出 0）
```

补充的单点读数：`pytest tests/test_runtime_memory.py -k real_reme` → `1 passed`；
`pytest tests/test_docker_network_policy.py` → `16 passed`。

## 冒烟证据（真实 ReMe + DeepSeek，进程内）

命令：`cd app && ../.venv/bin/python /tmp/reme-smoke/smoke.py`（临时脚本，不入库；模型
`deepseek:deepseek-chat`，`assemble_memory(..., supports_tools=True)`）。

```text
ROUND1_REPLY: 好的，已记住：你住在杭州，偏好简洁中文。
WORKSPACE: …/agents/mem_smoke/reme MODE: both TOOLS: ['memory_search']
CARDS: ['agents/mem_smoke/reme/daily/2026-10-24.md',
        'agents/mem_smoke/reme/daily/2026-10-24/user-profile-hangzhou-concise-chinese.md']
DIRECT_SEARCH: []                     # 写回后 3 秒：索引循环尚未追上
ROUND2_REPLY: 你住在杭州；喜欢简洁的中文回答。   # 新 session（smoke-session-2）同一工作区召回成功
```

索引的最终一致：第二轮跑完后再开一个进程直接 `middleware._search("我住在哪里？…")`，返回两张卡片
（`# 用户信息 … 居住地：杭州 … 语言偏好：简洁中文` 与 daily 索引行）。因此本次观察是
「写回后立刻检索可能为空，索引循环追上后可用」——与票面允许的最终一致一致，不是缺陷。

## Studio 浏览器验收（ego-browser，隔离数据目录与非 8788 端口）

- 服务：`python -m alibot --port 8799 --data-dir /tmp/alibot-studio-261`（临时目录，已停止）。
- 步骤：API 建 Agent（初始 `memory_mode=off`）→ Studio 打开详情 → 勾选「长期记忆」→ 保存 →
  刷新 → 重新打开详情。
- DOM 读数：`{checked: true, hint: '开启后自动记录对话要点并在本机工作区检索，供这个 Agent 跨会话、跨项目回忆。'}`
- 后端读数：`GET /api/agents` → `[('mem_42ce0c584162', 'reme')]`。
- 截图：`/tmp/alibot-studio-261-memory-reme.png`。

## 双轴 code-review

两个独立 reviewer 并行跑（Standards / Spec），均只读；findings 与处理如下。

### Standards

| # | finding | 处理 |
| --- | --- | --- |
| 1 | `app/README.md` 同一文件里 `SCHEMA_VERSION = 24` 与「表清单与 `SCHEMA_VERSION = 23` 一一对应」矛盾 | 已改 24；`Agent 运行时（schema 23 / ADR-0030）` 标题同步改为 `schema 24 / ADR-0030 / ADR-0034` |
| 2 | `CONTEXT.md`「本轮（15 → 23）是八个步骤」与追加的 `23→24` 自相矛盾 | 已改「（15 → 24）九个」 |
| 3 | 验收文档留占位符、判据 9 无读数 | 已回填门禁/复核读数与 Docker 命令读数 |
| 4 | 迁移测试断言 `sqlite_master` 的 DDL 文本（仓库禁「断言 SQL 文本」） | 已删除该断言；同判据由「旧词 UPDATE 抛 `IntegrityError`、`reme` UPDATE 成功」的行为覆盖 |
| 5 | `memory.MEMORY_MODES` 无人使用、与 db/Studio 各写一份词表（假单一来源） | 词表正本移到 `db.MEMORY_MODES`，`memory.py` 重新导出、`db.update_agent` 直接使用；DDL 是持久化副本（改词表要开 schema 迁移） |
| 6 | `runtime_files` 的 `is_read_only` / `read_only_dirs` 已无调用点（Speculative Generality） | 一并删除，`PartitionBackend` 只剩一个只读分区 |
| 7 | `CONTEXT.md` 规则 `_Avoid_` 仍写「`AGENTS.md` 对应用只读」，与项目层由权限档决定冲突 | 已限定为「全局层 `AGENTS.md` 对应用只读；项目层由项目权限档决定」 |

### Spec

| # | finding | 处理 |
| --- | --- | --- |
| 1 | `reme-ai` 未被真正 pin：`agentscope==2.0.10` 的 extra 只声明 `reme-ai>=0.4.0.6`，文档却声称「带入 `reme-ai==0.4.1.13`」 | 已显式 pin。**实测发现 0.4.1.13 与 2.0.10 的最小 ReMe 配置不兼容**：嵌入应用 `start()` 抛 `Unregistered backend 'dream_topics_step'`（`reme/components/job/base_job.py`）。改为 pin 实测可用的 `reme-ai==0.4.1.10`，README / ADR / 规约 / 偏离账本同步写明升级需单独开票并重跑冒烟 |
| 2 | `MEMORY_MODES` 死代码 | 同 Standards #5 |
| 3 | CONTEXT 迁移句矛盾 | 同 Standards #2 |
| 4 | README 仍写 schema 23 | 同 Standards #1 |
| 5 | `workspace.docker_container_config` docstring 仍写「成员家目录 `rw`」 | 已改为「两条 bind 都是 `ro`（ADR-0034 §四）」 |
| 6 | 审计矩阵第 44 行的 `grep 'RAGMiddleware\|embedding'` 零命中不成立（`memory.py` docstring 有 embedding） | 证据改为 `grep -n 'RAGMiddleware\|embedding_model' app/alibot` 零命中，并注明散文不算 |
| 7 | 验收门禁读数为占位符 | 已回填 |

两轴结论：**无运行期缺陷**；Spec 的 `reme-ai` 版本问题已用真实 `start()` 冒烟定案（不是文档措辞问题）。

## 遗留与已知边界

- 旧 `MEMORY.md` / `<topic>.md` 原样留在磁盘、不自动导入（写进 `app/README.md` 升级说明与 ADR-0034 §五）。
- 项目层 `.alibot/agents/<id>/AGENTS.md` 在项目根内，写入由项目权限档决定——旧白名单曾把它钉成只读；
  这是删除记忆白名单的已知副作用，记录在 ADR-0034 §四。
- ReMe 会在进程 cwd 写 `logs/`（本轮 Studio 验收时落在 `app/logs/`，未入库）；生产启动目录不变。
- **依赖口径与派发说明不一致**：派发写「extra 带入 `reme-ai==0.4.1.13`」，实测 extra 只有 floor
  `>=0.4.0.6`，且 0.4.1.13 与 `agentscope==2.0.10` 的嵌入式最小配置不兼容（见复核 Spec #1）。本票
  改为显式 pin `reme-ai==0.4.1.10`（真实冒烟通过）；升级 ReMe 是一张单独的票。
