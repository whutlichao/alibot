# #152 Skill：安装、启停、分区 — 验收

判据来源：GitHub issue #152 的四条 Acceptance；父规格 §3.6 / §4.3 / §4.4，ADR-0021 §三，D21。
所有命令都在本票 worktree 的 `app/` 下执行；读数来自 2026-10-18 的本机运行。

## 落点

| 面 | 落点 |
| --- | --- |
| 元数据 CRUD / scope 校验 / 同名冲突 / 装配读路径 | `app/alibot/db.py`（`list_skills` / `get_skill` / `create_skill` / `update_skill` / `delete_skill` / `skills_for_target`，以及 `channel_workspace_root` / `is_channel_member` / `workspace_roots_for_member` / `all_workspace_roots`） |
| 安装、校验、hash、分区、卸载、装配装载 | `app/alibot/skills.py` |
| HTTP 人类面 | `app/alibot/api.py`：`GET/POST /api/skills`、`PATCH/DELETE /api/skills/{id}` |
| 轮次装配 | `app/alibot/agent_seam.py`：`skills_of` 读路径 + 单 Agent / team 轮次把 Skill 挂进同一 `Toolkit` |
| 既有 workspace 分区 | `app/alibot/workspace.py`：`ensure_state_partition` / `safe_partition_id` 由私有改为公开（Skill 安装复用同一条根外 symlink 拒绝逻辑） |
| 文案 | `app/alibot/i18n.py`（21 条 skill 错误词条，中英同键） |
| 端点与数据模型登记 | `app/README.md` |
| 依赖 | `app/pyproject.toml`：显式声明 `python-frontmatter`（直接 import 的依赖不靠传递闭包） |

## 实现口径（判据之外的必要事实）

- **字节落点**：`<channel.workspace_root>/.alibot/skills/<scope_id>/<name>/`。
  - `scope_kind='agent'`：`<scope_id>` 就是 member id，即“按 member 分区”；
  - `scope_kind='channel'`：频道分区，装配时与成员分区合并，**Agent 级同名覆盖频道级**（与 `mcp_server` 同一优先级方向；停用 Agent 级覆盖不会让频道级重新浮现）。
- **内容 hash**：排序后的相对路径 + 文件大小 + 字节喂给 SHA-256，输出 64 位小写十六进制。装配前重新计算并与库里的 `content_hash` 比对；不符/缺失只跳过该技能，不影响其他工具。
- **来源**：只接受本机绝对目录（JSON）或上传 zip（原始字节 + `application/zip`），没有任何 URL/市场/自动下载字段；`extra="forbid"` 让 `url` / `market` 之类键整体 400。
- **整体拒绝**：校验在写入前完成；解包/复制先落 `<partition>/.staging-*`，成功后才 `os.replace` 到最终目录；失败路径清 staging、不留库行。DB 写失败时回删刚复制的工作区副本。
- **Agent 级安装的根解析**：显式 `channel_id` 优先；否则要求该成员恰好属于一个配置了 `workspace_root` 的频道，否则 400（可读原因，要求补 `channel_id`）。
- **配置动作**：安装 / 启停 / 删除只写 `skill` 表与工作区副本，不落消息、不唤醒、不消耗预算、不开世代。

## 判据 1：安装本机目录与 zip；校验 front matter、hash、路径；非法输入整体拒绝

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_skills.py -k "local_directory or zip_upload or rejects or garbage"
```

**读数**

```text
4 passed, 4 deselected in 0.29s
```

- `test_local_directory_install_round_trips_and_lands_in_the_member_partition`：本机目录安装 →
  `SKILL.md` / `checklist.txt` 复制到成员分区；响应含 64 位 hex `content_hash`；同一内容在两个 scope
  hash 相同；改一个字节后 hash 变化；同 scope 同名再装 409。
- `test_zip_upload_installs_and_illegal_inputs_are_rejected_wholesale`：zip 安装（含子目录）成功；
  缺 `SKILL.md`、front matter 缺 `description`、非法 `name`、未知 scope、未配置项目根、zip slip
  （`../` 与绝对路径）全部 400，且列表/磁盘都没有新行、新树。
- `test_local_install_rejects_bad_manifests_and_unsafe_paths_wholesale`：本机来源缺少清单、无 front
  matter、非法 name、空 description、技能树内 symlink、相对路径、不存在目录全部 400；分区里不留任何条目。
- `test_garbage_upload_and_patch_identity_fields_are_rejected`：非 zip 字节 400；PATCH 改 `name` /
  `content_hash` 400（scope 与 hash 是身份）。

## 判据 2：按 member 分区；启停/删除只影响该 scope；不删用户源目录

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_skills.py -k "partition"
../.venv/bin/python -m pytest tests/test_skill_assembly.py -k "overrides"
```

**读数**

```text
2 passed, 6 deselected in 0.55s
1 passed, 3 deselected in 0.03s
```

- `test_scope_partitions_are_isolated_and_delete_never_touches_the_source`：两个成员各自
  `.alibot/skills/<member_id>/review/`；停用第一个后第二个仍 `enabled=true`；删除第一个只删它的
  分区与库行，第二个副本与**两份用户源目录**原样保留；频道级技能落在频道分区，删除只清频道分区。
- `test_agent_scope_overrides_channel_scope_by_name`：`skills_for_target` 只返回 Agent 级同名技能；
  停用 Agent 级后该技能整体不装配（频道级不浮现）；删除 Agent 级后频道级默认恢复。

## 判据 3：配置动作不落消息/不唤醒/不耗预算；不做远程下载/市场

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_skills.py -k "configuration_changes or market"
```

**读数**

```text
2 passed, 6 deselected in 0.14s
```

- `test_configuration_changes_do_not_message_wake_or_spend_budget`：安装 → 停用 → 删除前后，频道
  消息、轮次结果、预算读数逐字相同（手动调度器未收到任何任务）。
- `test_extra_fields_cannot_smuggle_a_remote_market`：正文带 `url` 或 `market` 时 400，没有落库；
  端点本身没有 URL/下载参数。

## 判据 4：重启后技能列表与启用态保持

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_skills.py -k "restart"
```

**读数**

```text
1 passed, 7 deselected in 0.13s
```

`test_restart_keeps_list_and_enabled_state`：同一数据目录换一个后端进程（`harness.restart()`）后，
`GET /api/skills` 仍返回同一 id / `content_hash` / `enabled=false`，工作区副本仍在磁盘上。

## 装配（判据之外的实现取证）

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_skill_assembly.py
```

**读数**

```text
4 passed in 1.37s
```

- `test_enabled_skill_reaches_the_toolkit_and_the_system_prompt`：真 seam 下一轮 provider 的工具
  清单含内置 `Skill` 查看器，系统提示含技能名与描述；停用后两者都消失。
- `test_tampered_skill_bytes_are_not_assembled_and_do_not_block_other_tools`：安装后篡改工作区副本，
  hash 不匹配 → 不装配，但五个受限文件工具照常。
- `test_docker_backend_points_the_skill_at_the_container_mount`：Local 用 host 绝对路径；Docker 后端
  把 `dir` 翻译成 `/workspace/.alibot/skills/<member_id>/<name>`（字节仍来自同一个 host 项目根，
  容器只做挂载）。
- `test_agent_scope_overrides_channel_scope_by_name`：见判据 2。

## 全量门禁

**命令**

```sh
scripts/check.sh
```

**读数**（合并集成分支 tip `b594859` 后重跑）

```text
825 passed, 9 skipped in 377.43s (0:06:17)

== ruff check
All checks passed!

== ruff format --check
202 files already formatted

== mypy（仓库根）
Success: no issues found in 88 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test
tests 157 / pass 157 / fail 0

门禁全绿
```

退出码 0（`scripts/check.sh` 末行即「门禁全绿」）。pytest 的 9 个 skip 是本机 Docker daemon
未运行时的既有 `skipif`（MCP/Docker 成功路径），与 #152 的用例无关。

## 已知残余

- 安装是“先复制、后写库”。进程在两者之间被强杀会留下一个没有库行的技能目录；它不会出现在任何
  读取面，下一次同 scope 同名安装会得到文件系统层的 409（可读），需要人工删掉该目录。正常失败路径
  （校验失败 / 落库冲突）都会清 staging 与副本，已由测试覆盖。
- 内容 hash 在安装时计算一次；运行期若有人改工作区副本，技能不会被装配（hash 不符，只记日志），
  但列表仍显示库里的元数据。重新安装同 scope 同名会冲突——这是“已安装事实与磁盘不一致”的可读
  信号，不做静默自动修复。
- 频道改 `workspace_root` 后，旧根下的技能副本仍在（频道级删除只清当前根）；Agent 级删除会遍历
  库里所有已配置根，因此不会留下可清理的成员分区副本。旧根是用户项目目录，本票不主动扫描/删除
  项目根之外的历史路径。
- 运行时装配失败的细节只写日志（不像 MCP 有进程内 `runtime` 读数）；U3 的 Skill 管理面只需要
  `name/description/hash/enabled`，本轮没有新增运行时状态列。
- 不做远程 Skill 市场/下载/自动更新（D21）。
