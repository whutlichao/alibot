# #234 验收：全局记忆只读（两处读、写项目侧）

> 实施票：<https://github.com/whutlichao/alibot/issues/234>
> 规格正本：#228（`app/specs/agent-home-layers-spec.md` 的 D5、D6、D9、D10、T1/T2/T4/T6）
> 决策正本：`docs/adr/0031-agent-home-is-a-global-layer-beside-the-project-boundary.md`
> 基线：`agent-home-layers-integration` @ `85c42c1`

## 判据 → 落点 → 证据

| # | 验收判据 | 代码落点 | 测试（`app/tests/test_global_memory.py`） |
| --- | --- | --- | --- |
| 1 | 有项目根且长期记忆开启：项目记忆与全局记忆都可读到；新写入只出现在项目侧；全局侧文件 mtime 与目录清单轮次前后不变 | `memory.assemble_memory(..., global_home=...)`；`GlobalMemoryReadMiddleware.on_system_prompt` 追加全局 `MEMORY.md`；项目侧仍由 `AgenticMemoryMiddleware` 读 | `test_project_round_reads_both_memories_and_writes_only_the_project_side`（同一份模型输入里同时出现项目旧记忆与全局记忆；轮次后写入落项目侧，全局 mtime + 清单不变） |
| 2 | 私信轮次：数据目录侧成员记忆与全局记忆都可读；写入仍落数据目录侧 | `agent_seam._prepare_runtime_options` 把 `self._global_home` 传给 `assemble_memory`；DM 的 `offloader.root` 仍是数据目录 | `test_dm_round_reads_global_memory_and_writes_the_data_directory_side` |
| 3 | 全局记忆目录缺失或不可读：只少读一处 + 一条可读日志，本轮正常收口；不创建任何全局目录或占位文件 | `memory._read_global_memory`（`resolve_global_read_path` 返回 `None` / `OSError` / `UnicodeDecodeError` / 空文件各一条日志；中间件同一轮只记一次）；**不**调用 `ensure_state_partition` | `test_missing_global_memory_degrades_with_a_log_without_creating_anything`、`test_unreadable_global_memory_file_is_skipped_with_a_log`、`test_non_utf8_global_memory_is_skipped_with_a_log` |
| 4 | 全局记忆目录本身是指向全局根外的 symlink：跳过该全局侧并记日志，不当作可读来源 | 守卫根是**全局根**（不是成员家目录），因此祖先 symlink 越界会被 `resolve_global_read_path` 拒绝 | `test_global_member_home_symlink_outside_the_global_root_is_ignored` |
| 5 | 长期记忆关闭：两层都不挂、不建目录、不加中间件，与改动前一致 | `agent_seam` 仅在 `memory_mode=agentic` 时调用 `assemble_memory`；本票未改该分支 | `test_memory_off_does_not_touch_either_layer`；既有 `test_runtime_memory.py::test_memory_off_creates_no_memory_directory_and_adds_no_middleware` 继续绿 |
| 6 | `sessions/` 不出现在全局层，也不参与共享 | 未改 `SessionOffloader` / `SessionState`；它们仍只从项目根（DM 为数据目录）起算 | `test_project_round_reads_both_memories_and_writes_only_the_project_side` 断言项目 `sessions/` 存在、全局根与成员家目录下都没有 `sessions/` |
| 7 | Docker 后端可见路径映射到 `/alibot-home/agents/<member_id>/memory/Memory`；容器内路径不存在按缺席降级，不拖垮本轮 | `assemble_memory` 用 `docker_global_member_home(member_id)` 拼可见路径；内容由宿主读取 | `test_docker_global_memory_visible_path_maps_to_the_container_mount`（直接调公开 `assemble_memory`，断言 system prompt 段落里是容器路径、不是宿主路径） |
| 8 | 文件工具相对路径仍从项目根起算；全局记忆路径不是合法写入目标 | 全局目录只进 `PartitionBackend.read_only_dirs`，**不**进 `permission_context.working_directories`；写路径只看项目侧；`PartitionPermissionMiddleware` 对只读目录的写直接 DENY（`is_read_only` 用 realpath 比对，路径别名也拦得住） | `test_project_round_reads_both_memories_and_writes_only_the_project_side`（项目模式下写全局路径返回 `path is read-only`、全局文件不变）、`test_dm_memory_tools_can_read_but_cannot_write_the_global_layer`、`test_global_memory_write_through_a_path_alias_is_still_denied`、`test_dm_working_directories_stay_the_project_side_memory_only` |
| 9 | 测试使用临时全局根与临时 HOME；零 schema 变更、`SCHEMA_VERSION` 不 bump | 测试 autouse 无；每个用例 `monkeypatch.setenv("HOME", ...)` 并把 `global_home` 指向 `tmp_path`；未改 `db.py` / `migrate.py` | `git diff 85c42c1 -- app/alibot/db.py` 为空；`SCHEMA_VERSION` 仍为 23 |

### 「真的读到」是怎么做到的

`AgenticMemoryMiddleware` 在 2.0.10 wheel 里实测有两个行为（见
`docs/agentscope/2.0.10/`，另见探索笔记 §4.4）：

- `_ensure_layout` 在 `MEMORY.md` 缺失时**创建空文件**（`.../_agentic_memory/_middleware.py`）；
- `on_system_prompt` 只读它自己的 `workdir/Memory/MEMORY.md`。

因此本票**不**把全局目录交给它（否则会写占位文件），而是在 `memory.py` 新增公开的
`GlobalMemoryReadMiddleware`（`MiddlewareBase` 子类，2.x 公开钩子 `on_system_prompt`）：

1. 宿主每轮经 `workspace.resolve_global_read_path(global_home, "agents", <member>, "memory", "Memory", "MEMORY.md")`
   读取全局 `MEMORY.md`；
2. 以 `全局记忆（只读）` 标签原样追加到 system prompt（本地来源显示宿主路径，Docker 显示
   `/alibot-home/agents/<member>/memory/Memory`）；
3. 同一份内容**不**写回任何文件，全局目录**不**进 `permission_context.working_directories`。

DM 额外把全局目录放进 `PartitionBackend.read_only_dirs`：记忆 `Read` 工具可以显式读取主题文件，
`Write` / `Edit` 被 `PartitionPermissionMiddleware` 直接拒绝（不是转成 ASK）。

## 红 → 绿读数

| 切片 | 红（实现前） | 绿（实现后） |
| --- | --- | --- |
| 新增 API 尚不存在 | `pytest tests/test_global_memory.py` → `ImportError: cannot import name 'GlobalMemoryReadMiddleware' from 'alibot.memory'`（collection error） | `11 passed in 0.26s` |
| 首轮行为切片 | 实现后首跑：3 个用例红——项目 / DM 记忆写回被上游 `Write` 的「先读后写」拦住；DM 全局写被权限引擎当作 ASK（`AgentScope requires user confirmation but no ApprovalBroker is wired`） | 用例补上 `Read`（真实框架行为）；`PartitionPermissionMiddleware` 对只读目录的写改为 DENY，3 个用例转绿 |
| 两处读 / 只写项目侧 / mtime 与清单不变 | 全局内容进入 system prompt；项目侧写入出现，全局侧快照不变 | 同用例通过 |
| 缺失 / 不可读 / 非 UTF-8 / 根外 symlink | 每类降级一条日志、本轮 `replied`、全局根不被创建 | 同用例通过 |
| 关闭记忆 / Docker 可见路径 | 关闭时零中间件、零目录；Docker 段落里只有容器路径 | 同用例通过 |

命令（从 `app/` 运行；`app/pyproject.toml` 已带 `-q`，命令行不再补 `-q`）：

```sh
../.venv/bin/python -m pytest tests/test_global_memory.py
# 11 passed in 0.26s

../.venv/bin/python -m pytest tests/test_runtime_memory.py tests/test_global_memory.py \
  tests/test_agent_round.py tests/test_team_mode.py tests/test_agent_smoke.py
# 72 passed, 1 skipped in 13.55s

../.venv/bin/python -m pytest tests/test_runtime_memory.py tests/test_global_memory.py \
  tests/test_agent_round.py tests/test_team_mode.py tests/test_agent_smoke.py \
  tests/test_global_rules.py tests/test_local_workspace.py tests/test_approval_seam.py \
  tests/test_runtime_interrupt.py tests/test_mcp_assembly.py
# 138 passed, 2 skipped in 41.53s
```

## 门禁读数

从 worktree 根运行：

```sh
scripts/check.sh --fast
# ruff check: All checks passed!
# ruff format --check: 290 files already formatted
# mypy（仓库根）: Success: no issues found in 118 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: tests 258 / pass 258 / fail 0
# 门禁全绿
```

## 范围与偏差

- **零 schema 变更**：`git diff 85c42c1 -- app/alibot/db.py app/alibot/migrate.py` 为空；
  `SCHEMA_VERSION` 仍为 23（`app/alibot/db.py:114`）。
- **零新增 seam / 零新服务模块**：全局记忆路径是既有 `_prepare_runtime_options` 的新增 keyword 参数，
  由既有构造器注入的 `self._global_home` 提供；`assemble_memory` 的新增参数默认 `None`，
  未接线时中间件列表、工具与权限上下文与改动前逐字相同。
- **共享层的一处收紧**：`runtime_files.PartitionPermissionMiddleware` 原先对「写只读目录」调用
  `next_handler()`，在 `ask-writes` 下会变成一次 ASK（无人确认时整轮失败）。本票改为直接
  `PermissionBehavior.DENY`（`Error: path is read-only`），并用 `PartitionBackend.is_read_only`
  做 realpath 比对（`/var` ↔ `/private/var` 这类路径别名不会漏成 ASK）。受影响面仍是既有的
  `read_only_dirs`（会话卸载 / 全局记忆），不改三档语义、不新增可写根；既有
  `test_runtime_memory.py` / `test_approval_seam.py` 全绿。
- **Docker 的「容器内路径不存在按缺席降级」口径**：全局内容由**宿主**读取，因此容器内没有 bind
  也不影响 system prompt 注入；容器路径只作为可见来源文案与 `read_only_dirs` 的 visible 路径。
  file-tool 对 `/alibot-home/...` 的写入也会因落在只读目录而 DENY（测试未起 daemon，按 T4/T6
  用配置 / 装配层判据）。
- **日志**：沿用仓库既有英文日志风格；降级在**同一轮中间件实例内只记一次**，避免 ReAct 多次
  迭代刷屏。
- **已知代价**：全局 `MEMORY.md` 每轮重新读取（不缓存，规格把缓存列为 Out of Scope）；
  只注入 `MEMORY.md` 索引，不注入全局主题文件（上游 `AgenticMemoryMiddleware` 也只注入索引，
  主题文件由 DM 的只读 `Read` 通道显式读取）。
- **本票不做**：应用写入全局记忆、全局记忆版本管理 / 同步、跨项目共享 `sessions/`、
  Docker bind 的实际落地、全局规则 / 技能 / MCP。
