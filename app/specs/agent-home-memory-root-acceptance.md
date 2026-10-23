# 验收：Agent 家目录就是记忆根（ADR-0032）

判据 → 落点 → 命令与读数。票面「Observable acceptance」1–8 逐条对应。

## 一、落点总表

| 项 | 之前（ADR-0030/0031） | 之后（ADR-0032） | 落点 |
| --- | --- | --- | --- |
| 全局规则基座 | `~/.alibot/agents/<id>/rules/AGENTS.md` | `~/.alibot/agents/<id>/AGENTS.md` | `workspace.read_global_rules` |
| 项目补充规则 | `<ws>/.alibot/rules/<id>/AGENTS.md` | `<ws>/.alibot/agents/<id>/AGENTS.md` | `workspace.read_project_rules` |
| 全局记忆 | `~/.alibot/agents/<id>/memory/Memory/MEMORY.md` | `~/.alibot/agents/<id>/MEMORY.md` | `memory.assemble_memory` |
| 项目记忆 | `<ws>/.alibot/memory/<id>/Memory/MEMORY.md` | `<ws>/.alibot/agents/<id>/MEMORY.md` | `memory.assemble_memory` |
| 会话状态（有项目根） | `<ws>/.alibot/sessions/<sid>/` | 不变 | `runtime.SessionState` / `SessionOffloader` |
| 会话状态（无项目根） | `<data_dir>/.alibot/sessions/<sid>/` | `<data_dir>/sessions/<sid>/` | `runtime.session_relative_dir` |
| Docker 成员家目录 | `:ro` | `:rw`（记忆可写；白名单在应用面） | `workspace.docker_global_binds` |

## 二、判据 → 用例

### 1. 无项目根把 `MEMORY.md` 写到全局家目录，数据目录下无 `.alibot/memory`

`app/tests/test_global_memory.py::test_dm_round_writes_memory_to_the_global_home_and_never_to_the_data_directory`

```sh
cd app && ../.venv/bin/python -m pytest \
  tests/test_global_memory.py::test_dm_round_writes_memory_to_the_global_home_and_never_to_the_data_directory
```

断言：`<global>/agents/member/MEMORY.md` 内容被更新；`<data_dir>/.alibot` 不存在；
`<data_dir>/sessions/target--member/state.json` 存在。

### 2. 有项目根：两处读写；全局 `AGENTS.md` / `mcp.json` / `skills/x.md` 被拒且未被改

`test_global_memory.py::test_project_round_writes_both_memories_and_rejects_every_other_home_file`

断言：项目 `MEMORY.md` 与全局 `MEMORY.md`、全局 `topic.md` 写入成功；三份只读文件的
`(mtime_ns, bytes)` 快照前后一致；模型输入里出现 `path is read-only`。

补充判据：

- `test_dm_can_write_the_global_topic_file_but_not_rules_mcp_or_skills`（无项目根同口径）
- `test_global_home_subdirectory_md_is_rejected_even_though_it_is_markdown`（`sub/note.md` 仍只读）
- `test_runtime_memory.py::test_project_round_rejects_writing_the_project_rules_file`（项目侧 `AGENTS.md`）

### 3. sessions 落点

```sh
cd app && ../.venv/bin/python -m pytest \
  tests/test_runtime_memory.py::test_sessions_land_in_the_data_directory_without_an_extra_alibot_layer \
  tests/test_runtime_memory.py::test_project_round_sessions_land_under_the_project_alibot_partition
```

- 无项目根：`<data_dir>/sessions/target--member/state.json`，且 `<data_dir>/.alibot` 不存在。
- 有项目根：`<ws>/.alibot/sessions/channel--member/state.json`，项目根下无裸 `sessions/`。
- offload 同父目录：`test_dm_can_read_its_offloaded_context_without_reading_other_partitions`
  断言 `Path(path).is_relative_to(data_root / "sessions" / "target--member" / "offload")`。

### 4. 两段文件规则从扁平路径读出、顺序与标签不变

```sh
cd app && ../.venv/bin/python -m pytest \
  tests/test_global_rules.py::test_the_two_rule_segments_are_read_from_flat_home_paths \
  tests/test_global_rules.py::test_system_prompt_has_the_three_rule_segments_in_order
```

- 扁平路径：断言读到 `agents/<id>/AGENTS.md` 与 `.alibot/agents/<id>/AGENTS.md`；
  只有旧 `rules/` 路径时该段缺席（不回落旧路径）。
- 顺序：`prompt.index(GLOBAL_RULE) < prompt.index(CONFIG_PROMPT) < prompt.index(PROJECT_RULE)`，
  标签仍是 `全局基座：` / `项目补充：`。

### 5. Docker bind 模式：成员家目录 `:rw`、公共技能库 `:ro`

```sh
cd app && ../.venv/bin/python -m pytest \
  tests/test_docker_network_policy.py::test_global_binds_are_member_home_read_write_then_public_skills_read_only \
  tests/test_docker_network_policy.py::test_the_data_directory_never_enters_the_container_and_the_global_root_is_not_mounted
```

有 daemon 时另跑（容器内写入落到宿主同一目录）：
`tests/test_docker_workspace.py::test_docker_mounts_the_member_home_read_write_and_the_skill_library_read_only`。

### 6. 既有全绿

```sh
scripts/check.sh
```

读数（本地，macOS，2026-10-24）：

```text
1162 passed, 11 skipped in 670.01s (0:11:10)      ← pytest（全量）
All checks passed!                                ← ruff check app
309 files already formatted                       ← ruff format --check app
Success: no issues found in 123 source files      ← mypy（仓库根）
0 errors, 0 warnings, 0 informations              ← pyright app/alibot app/tests
ℹ tests 284  ℹ pass 284  ℹ fail 0                 ← node --test app/tests/*.test.cjs
门禁全绿
```

### 7. 双轴 review

`code-review` 技能跑 Standards + Spec 两轴（两个并行子代理），结论见 PR 的 Evidence 段。

### 8. PR

按 `pr` 技能三段（Summary / Evidence / Merge Danger）写，`--body-file` 创建。

## 三、逐条对照票面 Change

| # | 要求 | 落点 | 判据 |
| --- | --- | --- | --- |
| 1 | 同构扁平家目录，删 `rules/` 与 `memory/<id>/Memory/` | `workspace.py` 的 `read_*_rules`、`memory.py` 的 `assemble_memory` | 判据 4 用例 |
| 2 | 规则改扁平路径，顺序与标签一字不动 | `workspace.py:398-415`；标签在 `agent_seam.py` | 判据 4 用例 |
| 3 | 记忆两处可写，按归属分流；无项目根只落全局；关闭时不挂不建 | `memory.py:assemble_memory` | 判据 1/2 + `test_dm_round_does_not_inject_a_second_global_memory_segment` |
| 4 | 写入白名单（机械判据 + 可读拒绝 + 读覆盖家目录） | `workspace.is_memory_file_path`、`runtime_files.PartitionBackend` | 判据 2 的三个用例 |
| 5 | 会话状态显式化 | `runtime.session_relative_dir`、`SessionState`、`SessionOffloader` | 判据 3 用例 |
| 6 | Docker `:rw` / `:ro`，数据目录不进容器 | `workspace.docker_global_binds` / `docker_container_config` | 判据 5 用例 |
| 7 | 无 schema 变更、不动 pin 与快照 | — | `git diff main -- app/alibot/db.py app/pyproject.toml docs/agentscope/` 为空 |
| 8 | 迁移不自动；README 升级段给人工搬迁命令 | `app/README.md`「记忆与规则落点搬迁（ADR-0032）」 | 文档审阅 |
| 9 | CONTEXT.md 词条 + README；ADR-0030/0031 正文不改 | `CONTEXT.md`、`app/README.md`、`docs/adr/0032-*.md` | `git diff main -- docs/adr/0030-*.md docs/adr/0031-*.md` 为空 |

## 四、已知代价与残余

1. **Docker 成员家目录从 `:ro` 变 `:rw`**：这是 ADR-0031「全局记忆对应用永远只读」的显式修订。
   白名单（`is_memory_file_path`）是应用面的第二道闸；容器内 Bash 本来就能写 `/workspace`，
   所以这不是沙箱承诺（与 ADR-0021「本地 shell 无沙箱」同一口径）。
2. **旧路径不自动迁移**：`data/.alibot/memory/**`、`<ws>/.alibot/memory/**`、`<ws>/.alibot/rules/**`、
   `~/.alibot/agents/<id>/rules/**`、`.../memory/Memory/**` 不再被读取。搬迁是人的显式动作，
   命令见 `app/README.md`。
3. **同名空间**：`_AlibotLayoutMixin` 把 AgentScope 技能分区重定向到 `.alibot/agents/`，于是项目层
   `.alibot/agents/` 下同时有「技能名目录」与「`mem_*` 家目录」。键空间不同（短 slug vs `mem_<hex>`），
   本票不改框架技能落点（ADR-0032 §一已记）。
4. **有项目根时相对路径仍从项目根起算**：全局家目录是**显式白名单例外**（只对绝对路径生效），
   不是第二个工作根。
5. **`SESSION` 语义不变**：`session_id` 仍是 `safe_partition_id(f"{target_id}--{member_id}")`。

## 五、测试卫生

- 全部用例用 `monkeypatch.setenv("HOME", …)` 或 `_global_root(tmp_path, monkeypatch)` 指向临时目录；
  `app/tests/conftest.py` 的 session 级 autouse fixture 也把 HOME 收到临时根
  （`test_home_isolation.py` 守这一条）。
- 真实 `~/.alibot` 在本机只有 `data/`（10-22 创建）；`agents/` 与 `skills/` 均不存在，验证了
  「测试不读写真实家目录」。
