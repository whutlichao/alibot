# #272 本地后端把成员技能目录作为只读第二根 验收

- 状态：Done（`scripts/check.sh --fast` 绿；Docker 后端按票面不改）
- Issue：https://github.com/whutlichao/alibot/issues/272
- 分支：`issue-272-impl`（base `origin/capabilities-agent-scope` = `04452cb`）
- 面：`app/alibot/workspace.py`、`app/tests/test_local_workspace.py`、本文件

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | `WorkspaceRootGuard` 接受一到多个只读根，默认空；解析顺序：项目根（读写）→ 只读根（只读） | `WorkspaceRootGuard.__init__(root, read_only_roots=())`、`resolve` / `resolve_read` | 见判据 2–6 |
| 2 | `Read` 能读 `<家目录>/agents/<member>/skills/<name>/references/x.md`；`Glob` / `Grep` 同样可读 | `WorkspaceRootGuard.resolve_read` + `_match_read_only_root` | `tests/test_local_workspace.py::test_read_glob_and_grep_reach_the_member_skills_read_only_root`（红→绿见下） |
| 3 | 同一路径 `Write` / `Edit` 被拒，ERROR 形状与既有「根外」逐字一致，不落盘 | `on_tool_call` 的 `write` 分支仍走 `resolve`（只认项目根） | `tests/...::test_write_and_edit_in_the_member_skills_root_are_rejected_like_an_escape` |
| 4 | 既非项目根也非只读根的读仍被拒；全局层兄弟路径（`agents/<member>/AGENTS.md`、`agents/<member>/memory/`、`mcp.json`）**不**放开 | `read_only_roots` 只有技能目录一个 | `tests/...::test_paths_outside_both_roots_are_still_rejected` |
| 5 | 只读根内 symlink 不能逃逸（`realpath` 后仍须在该根内） | `_match_read_only_root` 先 `realpath` 再 `_within` | `tests/...::test_symlinks_inside_the_skills_root_cannot_escape_it` |
| 6 | `assemble_local_workspace` 传 `~/.alibot/agents/<member_id>/skills`；目录不存在按空根处理、不创建 | `assemble_local_workspace` 的 `member_skills` + `_guarded_tools(..., (member_skills,))` | `tests/...::test_absent_member_skills_dir_is_neither_created_nor_readable` |
| 7 | 项目根仍是唯一可写根；Bash 未放开；Docker 后端一行未改 | `_guarded_tools` 的工具清单不变；`ContainerRootGuard` / `_docker_tools` 无 diff | 既有 `tests/test_local_workspace.py` 全绿（含 `test_default_tool_set_has_no_bash…`）、`tests/test_docker_workspace.py` 22 passed / 8 skipped |
| 8 | 门禁 | 仓库 | `scripts/check.sh --fast tests/test_local_workspace.py tests/test_workspace_permission_schema.py tests/test_test_layering_guard.py` → 38 passed，门禁全绿 |

## 实现要点

- `WorkspaceRootGuard.read_only_roots` 是**元组**，只在 `resolve_read`（Read / Glob / Grep）里生效；
  `Write` / `Edit` 走 `resolve` → `resolve_in_root`，行为与 #148 完全一致（同一 `WorkspaceRootViolation`、
  同一 ERROR chunk 文案 `Error: path escapes workspace root: '<raw>'`，无新文案）。
- 只读根匹配**只看绝对路径**：相对路径仍锚到项目根（不因技能目录存在而改变语义）。
- 存在性与 `realpath` 都在**解析时**判断：目录尚未由技能装配建出时自动跳过（不创建）；之后建出即生效，
  不需要重装配。symlink 祖先 / 目标逃逸在 `realpath` 后不落在只读根内，因此不命中。
- `assemble_local_workspace` 用 `global_member_home(member, root=global_home_root())` + `SKILLS_DIR`
  拼出路径（与 ADR-0031 的全局层解析同源），**只给路径不建目录**。全局层的 `AGENTS.md`、
  `mcp.json`、`memory/`、公共技能库 `~/.alibot/skills` 都不是根。
- **Docker 后端不改**：`ContainerRootGuard` / `_docker_tools` / docker 装配零 diff。票面依据是全局家目录
  已以 `:ro` 挂载（`docker_global_binds`），容器只需读 `references/`，写入发生在宿主进程（ADR-0031 §三）。

## 红证据（实现前）

新增 5 条用例，只有「Read / Glob / Grep 可读只读根」在实现前失败——其余 4 条今天被**一切路径都拒**
满足，实现后成为「不越界」的回归护栏（它们的作用是防止只读根被放开成全局层或逃逸）。

```text
$ scripts/check.sh --fast tests/test_local_workspace.py
........................F....                                            [100%]
________ test_read_glob_and_grep_reach_the_member_skills_read_only_root ________
>       assert read.state is not ToolResultState.ERROR, read.content[0].text
E       AssertionError: Error: path escapes workspace root:
        '.../home/.alibot/agents/mem_workspace/skills/code-review/references/x.md'
E       assert <ToolResultState.ERROR: 'error'> is not <ToolResultState.ERROR: 'error'>
1 failed, 28 passed in 2.51s
```

## 绿读数（实现后）

```text
$ scripts/check.sh --fast tests/test_local_workspace.py \
    tests/test_workspace_permission_schema.py tests/test_test_layering_guard.py
== pytest（指定文件）: ...
38 passed in 10.08s
== ruff check
All checks passed!
== ruff format --check（只检查）
331 files already formatted
== mypy（仓库根）
Success: no issues found in 125 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 319 / pass 319 / fail 0
门禁全绿
```

相邻用例族（不属门禁命令，用于确认没有旁路回归）：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_empty_workspace.py \
    tests/test_docker_workspace.py tests/test_agentscope_usage_guard.py
22 passed, 8 skipped in 2.18s
```

`test_workspace_permission_schema.py` 未改：本票不动 schema、不改 `WorkspaceSettings` 字段，装配签名
变化只在 `_guarded_tools` / `WorkspaceRootGuard` 这两个模块内私有面（默认参数保持向后兼容）。

## 遗留 / 冲突

- 无票面冲突。`app/specs/issue-272-acceptance.md` 之外未改文档：ADR-0021 / ADR-0031 已在 #270 写明
  「本地 Read / Glob / Grep 的只读第二根」，本票不改口径。
- `read-only` 权限档语义未变；本票只在**执行前**放行读路径，不新增权限档、不放开 Bash。
