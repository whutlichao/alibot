# #148 验收：LocalWorkspace（频道项目根 + 文件工具根限定）

- 票面：`#148`（Part of `#131`）；票型 impl；门禁 G-impl。
- 分支：`issue-148-impl`；基线：集成分支 tip `5763122`（`SCHEMA_VERSION=17`），收尾时并入集成分支
  新 tip `798b68c`（merge #141）。
- 规格：`app/specs/agentscope-2-alignment.md` §3.6；ADR-0021；研究：`issue-135-research.md`（根检查
  伪代码与包装清单）、`issue-134-research.md`（Docker 装配建议，W3 使用）。
- 范围：频道项目根的 LocalWorkspace 装配、五个文件工具的根限定、默认无 Bash、`.alibot` 状态分区，
  以及 seam 的 workspace 装配钩子与 DB 读路径。**不做** `GET/PATCH /api/channels/{id}/config`
  （#142）、权限/审批与本地 shell 的逐次审批（W2）、Docker 后端（W3）。

## 改了什么

| 落点 | 改动 |
| --- | --- |
| `app/alibot/workspace.py`（新） | `WorkspaceSettings`（根 / 后端 / 权限档 / `allow_local_shell` / `docker_image`）；`validate_workspace_root`（绝对路径 + 存在 + 目录，先 `realpath`）；`resolve_in_root`（相对路径锚到根、`realpath` 后必须落在根内）；`WorkspaceRootGuard`（Tool middleware：Read/Write/Edit 的 `file_path`、Glob/Grep 的 `path` 先规范化，Glob `pattern` 拒绝 `..` 段）；`ProjectLocalWorkspace`（把 `data/` / `skills/` / `sessions/` / `.mcp` 收进 `.alibot`）；`assemble_local_workspace`（建 `.alibot/agents/<member_id>` 与 `.alibot/sessions/<session_id>`，只返回五个文件工具，不含 Bash）。 |
| `app/alibot/agent_seam.py` | `AgentScopeAgentSeam(workspace_of=…)` 装配钩子；每轮按 `(target_id, member_id)` 取配置，local 后端才装配；`_round_toolkit` 增加 `workspace_tools`（任务动作 / `declare_silence` 原样保留）；轮次结束 `close()` 工作区。未注入钩子时行为逐字不变。 |
| `app/alibot/db.py` | `workspace_settings_for_target(target_id, member_id)`：channel 型直接取频道，task 型经 `task.channel_id` 解析；`workspace_root` 为空 / DM / 未知 target 返回 `None`；Agent 级 `workspace_backend` / `permission_mode` / `allow_local_shell` / `docker_image` 覆盖频道默认值。只读，不写配置。 |
| `app/alibot/api.py` | 生产装配点把 `database.workspace_settings_for_target` 传给真 seam（未配置 workspace 的频道仍不挂文件工具）。 |
| `app/tests/test_local_workspace.py`（新） | 真实临时目录 + 真 seam（进程内 stub provider）取证；24 条用例。 |

## 逐条判据

### 1. 频道设置 `workspace_root` 后，Read/Write/Edit/Glob/Grep 在根内成功

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_local_workspace.py -k "inside_paths or channel_workspace_root" -v
… tests/test_local_workspace.py::test_inside_paths_work_for_all_five_file_tools PASSED
… tests/test_local_workspace.py::test_channel_workspace_root_resolves_for_channel_and_task_targets PASSED
2 passed
```

`test_inside_paths_work_for_all_five_file_tools` 在真实根内依次 Write → Read → Edit → Glob → Grep，
并额外用**相对路径**（`inside.txt`）Read：相对路径锚到根，而不是进程 cwd。DB 用例直接写
`channel.workspace_root` 后，读路径对 **channel target 与 task target** 返回同一份配置（task 经
`task.channel_id` 解析）。

### 2. `../`、根外绝对路径、根外 symlink 返回 ERROR；没有根外写入

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_local_workspace.py -k "escapes or relative_dotdot" -v
… 16 passed
```

参数化覆盖五个工具 × 三类逃逸（根外绝对路径、`root/../outside/…`、根内 symlink → 根外），
外加相对 `../outside/…` 的 Write。每条断言：

- 返回 `ToolResultState.ERROR`，文案含 `path escapes workspace root`；
- 根外文件**不存在**（`pwned.txt` / `pwned-dotdot.txt` / `pwned-relative.txt` / symlink 目标）；
- 根外既有文件内容不变（`edit-target.txt` 仍是 `alpha`）。

`Glob` / `Grep` 省略 `path` 时也被锚到根（`test_glob_without_path_is_anchored_to_the_root_not_the_process_cwd`），
不再落到进程 cwd（#135 的实测逃逸形状）。

### 3. 默认工具集不含 Bash；`allow_local_shell` 只暴露标记

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_local_workspace.py -k "bash" -v
… test_default_tool_set_has_no_bash_and_exposes_the_shell_flag PASSED
```

工具名集合恰为 `{Read, Write, Edit, Glob, Grep}`；`allow_local_shell=False`。以
`allow_local_shell=True` 装配时，标记为 `True`，工具集仍**不含** Bash——本层不在权限/审批之外挂
本地 shell（D15 / ADR-0021 §二）。逐次审批、`ask` 决策与“本机进程、无沙箱、可越过项目根”的告知
由 W2 在该标记上接线。

### 4. `.alibot/agents/<member_id>` / `.alibot/sessions/<session_id>` 存在，不污染项目根

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_local_workspace.py -k "alibot_partitions" -v
… test_alibot_partitions_exist_and_state_does_not_pollute_the_project_root PASSED
```

装配后 `.alibot/agents/<member_id>/` 与 `.alibot/sessions/<target>--<member>/` 存在；项目根下
**没有** AgentScope 的 `data/` / `skills/` / `sessions/` / `.mcp`；根内业务文件原样保留。
`ProjectLocalWorkspace` 把框架状态目录分别映射到 `.alibot/agents`、`.alibot/sessions`、
`.alibot/data`、`.alibot/mcp.json`。若项目里预置 `.alibot` → 根外的 symlink，装配在**创建前**拒绝
（`WorkspaceConfigError`），不会把状态写到根外。

### 5. 真 seam 的轮次工具集接上文件工具（默认无 Bash）

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_local_workspace.py -k "seam" -v
… test_seam_round_mounts_the_guarded_tools_and_creates_partitions PASSED
… test_seam_uses_the_channel_workspace_root_from_the_database PASSED
```

进程内 stub provider 收到的请求里 `tools` 包含 Read/Write/Edit/Glob/Grep、不含 Bash；同一轮建立
`.alibot` 分区。第二条用例走生产读路径：直接写 `channel.workspace_root` → 注入
`database.workspace_settings_for_target` → 真 seam 的下一轮即挂上文件工具。任务动作与
`declare_silence` 的既有工具集不受影响（全量 pytest 里的既有 seam/沉默/任务用例全绿）。

### 6. 全量门禁

```console
$ scripts/check.sh
== pytest（全量）
708 passed, 2 skipped in 315.63s (0:05:15)

== ruff check
All checks passed!

== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
168 files already formatted

== mypy（仓库根）
Success: no issues found in 66 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 136
ℹ pass 136
ℹ fail 0

门禁全绿
```

## 残余与已知边界

- **hardlink**：根内 hardlink 指向根外 inode，路径 `realpath` 仍在根内，路径检查无法识别（#135 §四）；
  与检查 ↔ 执行之间的 **TOCTOU** 一样是已知残余，本票只承诺“路径 realpath 落在根内”。
- **Docker 后端**：`workspace_settings_for_target` 会返回 `backend=docker`，但 `agent_seam` 只在
  `backend=local` 时装配（不静默回落本地）；容器装配属 W3（#150）。
- **本地 shell 审批**：`allow_local_shell` 只作为标记暴露；逐次审批与无沙箱告知属 W2。
- **非法根**：配置了不存在 / 非绝对 / 非目录的根时，装配抛 `WorkspaceConfigError`，该轮收口为
  `failed`（`seam.call_failed`）；端点侧的整体校验由 #142 负责。
