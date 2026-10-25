# #271 验收：装配与安装器切到「成员家目录单正本」

父规格 #269；分支 `issue-271-impl`。本票只改装配 / 安装器 / HTTP 面与相关测试：schema 未动、
`SCHEMA_VERSION` 未 bump、没有任何数据搬迁（旧项目层字节与频道级旧行原样保留）。

## 判据 → 落点

| 判据（票面） | 落点 | 用例 |
| --- | --- | --- |
| 安装器落到 `~/.alibot/agents/<member>/skills/<name>/`；频道根解析与其两支错误路径删除 | `app/alibot/skills.py`（`skills_partition` / `_ensure_partition` / `resolve_install_root` / `install_directory` / `install_zip` / `_install_prepared` / `uninstall`） | `tests/test_skills.py::test_local_directory_install_round_trips_and_lands_in_the_member_home`、`::test_zip_upload_installs_and_illegal_inputs_are_rejected_wholesale` |
| 装载只读两层：成员家目录（复核库里 `content_hash`）> 全局公共库 | `skills.load_skills(rows, member_home=…)`、`skills.load_global_skills(..., exclude_member_names=…)`、`agent_seam._skills_for` | `tests/test_skill_assembly.py::test_enabled_skill_reaches_the_toolkit_and_the_system_prompt`、`tests/test_global_skills.py::test_two_layer_priority_assembles_member_home_before_public_library` |
| ① 频道级 skill 行不再装配（行保留） | `db.skills_for_target` 只查 `scope_kind='agent'` | `tests/test_global_skills.py::test_channel_scope_rows_and_project_layer_bytes_are_never_assembled`、`tests/test_skill_assembly.py::test_channel_scope_rows_are_never_the_assembly_source` |
| ① 频道级 mcp 行不再装配（行保留） | `db.mcp_servers_for_target` / `db.mcp_layers_for_target` + `agent_seam._mcp_declarations` | `tests/test_mcp_assembly.py::test_channel_scope_rows_are_ignored_and_only_member_rows_are_resolved`、`::test_a_channel_only_declaration_never_reaches_the_provider`、`tests/test_global_mcp.py::test_two_layers_keep_one_entry_per_name_and_report_losers_only_in_the_reason` |
| ② DM（`settings is None`）仍装配成员技能 | `agent_seam._skills_for` 去掉 `settings is None` 早退；`_skills_for_member` 同样去掉 | `tests/test_global_skills.py::test_direct_message_assembles_member_skills_without_a_project_root` |
| ③ 家目录里无库行的手工目录也装配 | `load_global_skills(..., exclude_member_names=…)` 的成员层 | `tests/test_global_skills.py::test_a_manual_member_directory_without_a_database_row_is_assembled` |
| ④ 删除只清成员家目录 | `api.delete_skill`（只对 `scope_kind='agent'` 调 `uninstall`） | `tests/test_skills.py::test_scope_partitions_are_isolated_and_delete_never_touches_the_source` |
| `POST /api/skills` 不再需要 `channel_id`；`scope_kind=channel` → 明确 400 | `api.SkillCreateLocal`（删字段）、`skills.resolve_install_root`（`error.skill_scope_agent_only`） | `tests/test_skills.py::test_channel_scope_is_rejected_explicitly_and_listing_stays_shaped`、`::test_extra_fields_cannot_smuggle_a_remote_market`（`channel_id` 作为多余键被拒） |
| `GET /api/skills?scope_kind=channel` 空列表且形状不变 | `api.list_skills` | 同上 |
| 停用 / 坏行不被「手工目录」复活 | `agent_seam._skills_for` 用**全部**成员行名字做排除 | `tests/test_global_skills.py::test_a_disabled_member_row_is_not_revived_by_its_manual_directory` |
| MCP 仍以 workspace 为宿主（无 workspace 不装配）、`runtime` 读数保留 | `db` target 门控 + `_assemble_mcp` 未改 | `tests/test_global_mcp.py::test_a_round_without_a_project_mount_assembles_no_mcp_at_all`、`tests/test_mcp_assembly.py` 其余用例 |
| Docker `Skill.dir` 指向容器内成员家目录 | `load_skills(..., container_home=docker_global_member_home(...))` | `tests/test_skill_assembly.py::test_docker_backend_points_the_skill_at_the_container_mount` |

## 先红后绿

红（实现前，DM 用例）：

```text
$ scripts/check.sh --fast tests/test_global_skills.py::test_direct_message_assembles_member_skills_without_a_project_root
>           assert "Skill" in _tool_names(body)
E           AssertionError: assert 'Skill' in {'declare_silence', 'memory_search'}
1 failed in 1.92s
```

绿（实现后）：

```text
$ ../.venv/bin/python -m pytest app/tests/test_global_skills.py::test_direct_message_assembles_member_skills_without_a_project_root -q
.
1 passed
```

## 被删符号与调用点处置

| 被删符号 | 位置 | 调用点处置 |
| --- | --- | --- |
| `SKILL_LAYER_PROJECT_AGENT` / `SKILL_LAYER_PROJECT_CHANNEL` | `skills.py` | 唯一使用者 `agent_seam._skill_layers` 已删；`i18n` 的 `skill.layer.project-*` 词条同删（全仓 grep 无引用） |
| `_real_root` | `skills.py` | 只被频道根解析使用，随之一并删除 |
| 频道根解析（`channel` 分支、`channel_id` 分支、`skill_channel_ambiguous`、`skill_member_not_in_channel`、`skill_workspace_root_missing`） | `resolve_install_root` | `resolve_install_root` 收敛为「只接受 `agent` + 返回全局根」；HTTP 调用点改传 `global_root` / `member_id` |
| `resolve_delete_roots` | `skills.py` | 唯一调用点 `api.delete_skill` 改为直接 `uninstall(global_home_root(), member_id=…)`（仅 agent 行） |
| `Database.all_workspace_roots` | `db.py` | 唯一调用点是 `resolve_delete_roots`；全仓 grep 无残留引用 |
| `Database.workspace_roots_for_member` | `db.py` | 唯一调用点是 `resolve_install_root` 的频道根解析；全仓 grep 无残留引用 |
| `Database.is_channel_member` | `db.py` | 唯一调用点是 `resolve_install_root` 的 `channel_id` 校验；全仓 grep 无残留引用 |
| `Database.channel_workspace_root` | `db.py` | 唯一调用点是安装/删除根解析；全仓 grep 无残留引用（`error.field.channel_workspace_root_not_absolute` 是频道配置校验的另一条词条，保留） |
| `Database.skill_layers_for_target` | `db.py` | 调用点 `api.create_app(skill_layers_of=…)` 与 `agent_seam._skill_layers` 一并删除；测试注入同步移除 |
| `AgentScopeAgentSeam.__init__(skill_layers_of=…)` | `agent_seam.py` | 构造点 `api.create_app` 与 `tests/test_global_skills.py::_seam` 同步移除 |
| `AgentScopeAgentSeam._skill_rows` / `_skill_layers` | `agent_seam.py` | 被 `_skills_for` 内联的成员行读取取代；无其他调用者 |
| `i18n.error.skill_workspace_root_missing` / `error.skill_workspace_root_invalid` / `error.skill_channel_ambiguous` / `error.skill_member_not_in_channel` | `i18n.py` | 两个语言同键集删除；新增 `error.skill_scope_agent_only` 与 `error.skill_member_home_invalid` |
| `i18n.mcp.layer.channel` | `i18n.py` | 频道层分支删除后无引用；`mcp.layer.agent` 文案改为「成员级库行」（不再有项目层） |

保留说明：
- `Database.validate_skill_scope` **仍接受** `channel`：只为既有历史行保留（`create_skill` 是历史行
  写入面，票面要求「行不删」）；HTTP 安装路径与安装器都在更前面拒绝频道作用域。
- `GET /api/skills`（不带 `scope_kind`）仍会列出频道级旧行：票面只要求 `scope_kind=channel`
  返回空列表，删行归 T5 的显式迁移。

## 门禁读数

```text
$ scripts/check.sh --fast tests/test_skills.py tests/test_skill_assembly.py tests/test_global_skills.py \
    tests/test_mcp_assembly.py tests/test_global_mcp.py tests/test_test_layering_guard.py tests/test_backend_copy.py
…（ruff / ruff format --check / mypy / pyright / node --test 全绿）
ℹ tests 319
ℹ pass 319
ℹ fail 0
门禁全绿
```

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_skills.py tests/test_skill_assembly.py \
    tests/test_global_skills.py tests/test_mcp_assembly.py tests/test_global_mcp.py
42 passed, 1 skipped in 33.52s        # skip 是 Docker daemon 不在本机

$ cd app && ../.venv/bin/python -m pytest tests/test_test_layering_guard.py tests/test_backend_copy.py
18 passed in 2.40s

$ git diff --check
（无输出）

$ cd app && ../.venv/bin/python -m pytest          # 全量回归（已合入集成分支 tip a3036b2）
1182 passed, 11 skipped in 674.57s (0:11:14)
```

`scripts/check.sh --fast` 的 ruff / ruff format --check / mypy / pyright 与 node 用例族（319 个）
均全绿；上表「门禁读数」段贴的是该次运行的尾部。

## 遗留 / 冲突

- Studio 仍以频道作用域安装/列技能（T4 才移除入口）：本票按要求让 `POST` 返回明确 400、
  `GET` 返回空列表，App 端入口在 T4 前会显示错误——这是票面明确的行为，不是静默忽略。
- 旧项目层技能字节与频道级旧行保持原样；搬迁由 T5 `migrate-skills` 显式执行。
- 未改 `workspace.py`、`studio/**`、schema 与 `SCHEMA_VERSION`；`remote_consent_scope('mcp', server_id)`
  键形状未动。
