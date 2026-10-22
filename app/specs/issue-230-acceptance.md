# #230 验收：规则三段拼装 + 全局只读守卫

> 实施票：<https://github.com/whutlichao/alibot/issues/230>
> 规格正本：#228（`app/specs/agent-home-layers-spec.md` 的 D1、D3、D6、D9、D11 Seam A、D10、T1/T2/T4/T6）
> 基线：`agent-home-layers-integration` @ `357aa89`

## 判据 → 落点 → 证据

| # | 验收判据 | 代码落点 | 测试（`app/tests/test_global_rules.py`） |
| --- | --- | --- | --- |
| 1 | 真实 provider 请求里三段按「全局基座 → `agent_config.system_prompt` → 项目补充」出现，各带可读标签；角色句 / rubric / 任务上下文仍在配置段之后 | `workspace.read_global_rules` / `read_project_rules`；`agent_seam._system_prompt` 的 `global_base` / `project_supplement`；`AgentScopeAgentSeam._rule_layers` | `test_system_prompt_has_the_three_rule_segments_in_order`（HTTP stub provider 读真实请求体） |
| 2 | 私信只出现前两段 | 单 Agent 路径：无项目根 ⇒ `project_root=None` | `test_direct_message_has_only_the_global_base_and_the_config` |
| 3 | 单 Agent、team 队长、team 成员三条路径一致 | 单 Agent：`_system_prompt`；队长：`_leader_prompt`；成员：`_team_member_prompt` | `test_team_leader_and_member_each_get_the_three_rule_segments`（真实 TeamPipeline + 脚本模型驱动 `TeamAssign`） |
| 4 | SOP 路径不含这三段 | `AgentScopeAgentSeam.sop_agent` 只调 `sop_agent_prompt`，本票未改 | `test_sop_participant_does_not_get_the_three_rule_segments` |
| 5 | 缺失 / 目录缺失 / 不可读 / 非 UTF-8：该段缺席 + 一条可读日志，本轮正常收口；无空标题或占位 | `workspace._read_layer_text`（`FileNotFoundError` / `OSError` / `UnicodeDecodeError` / 空文件各一条日志） | `test_missing_rule_files_are_absent_with_a_readable_log`、`test_broken_rule_files_are_absent_with_a_readable_log`（non-utf8 / unreadable / empty） |
| 6 | 三段都为空时既有身份句与回应 rubric 照旧 | `_system_prompt` 只在段存在时插标签 | `test_empty_rules_keep_the_existing_identity_and_response_rubric` |
| 7 | 运行一轮前后全局根 `agents/` / `skills/` 清单不变；项目 `.alibot/rules/` 不被创建 | 读路径只 `read_bytes`，无 `mkdir` / 写 | `test_a_round_neither_creates_nor_changes_the_global_or_project_rule_dirs` |
| 8 | 文件工具相对路径仍从频道项目根起算；全局层路径与根外 symlink 被拒绝；三档权限档一致 | 未改 `WorkspaceRootGuard` / `resolve_in_root` / 权限层；全局层只由宿主读成提示文本 | `test_global_layer_is_not_a_file_tool_root`（`read-only` / `ask-writes` / `trusted-writes` 三档参数化） |
| 9 | 全局根固定 `~/.alibot/`，`--data-dir` / `ALIBOT_DATA_DIR` 不改变规则读取位置 | `workspace.global_home_root()` 只看 HOME；`api.create_app` 显式接线 | `test_global_root_ignores_the_data_dir_override` |
| 10 | 只读守卫以自己根做 realpath，拒绝根外 symlink 祖先与根外目标 | `workspace._read_layer_text` 的 `_within(realpath(candidate), realpath(root))` | `test_symlink_outside_the_global_root_is_rejected`（祖先 symlink / 目标 symlink 两种形态） |
| 11 | 为后续票提供 Docker 全局挂载点常量与成员家目录解析 | `workspace.GLOBAL_HOME_DIRNAME`、`DOCKER_GLOBAL_HOME`、`global_member_home`、`docker_global_member_home`、`docker_global_skills_home` | `test_global_member_home_and_docker_mount_constants` |
| 12 | 测试 HOME 与全局根都指向临时目录；零 schema 变更、`SCHEMA_VERSION` 不 bump | 测试 `isolated_home` autouse fixture；未改 `db.py` | `git diff 357aa89 -- app/alibot/db.py` 为 0 行；`SCHEMA_VERSION` 仍为 23 |

### 段落标签

固定为 `全局基座：` 与 `项目补充：`（`agent_seam._GLOBAL_RULE_LABEL` / `_PROJECT_RULE_LABEL`），
只在该段真的存在时出现。顺序与放置位置按设计笔记 §3.1「只在 `base` 前后插入两段」：
全局基座 → 配置 → 项目补充 → 角色句 / rubric / 任务上下文。规则文本**原样拼接**，不 `strip`、
不解析、不做语义合并（测试用带尾随换行的原文断言完整子串）。

## 红 → 绿读数

| 切片 | 红（实现前） | 绿（实现后） |
| --- | --- | --- |
| 单 Agent 三段顺序 | `pytest tests/test_global_rules.py` → `ImportError: cannot import name 'global_home_root' from 'alibot.workspace'`（collection error） | `17 passed`（最终，见下） |
| team 队长 / 成员 | `tests/test_global_rules.py::test_team_leader_and_member_each_get_the_three_rule_segments` → `AssertionError: assert '全局基座：\n全局基座正文…' in '配置段正文：你是严谨的工程师。…'` | 同用例通过 |
| 降级 / 守卫 | helper 在切片 1 按票面一次性落地；对应用例首次运行即绿，作为行为锁保留（不含未实现的推断分支） | 同用例通过 |
| 创建纪律 / 根固定 / SOP 排除 | 回归守卫；本票没有改动这些既有路径 | 同用例通过 |

命令（从 `app/` 运行；`app/pyproject.toml` 已带 `-q`，命令行不再补 `-q`）：

```sh
../.venv/bin/python -m pytest tests/test_global_rules.py
# 17 passed in 6.71s

../.venv/bin/python -m pytest tests/test_global_rules.py tests/test_skill_assembly.py \
  tests/test_local_workspace.py tests/test_agent_round.py tests/test_team_mode.py \
  tests/test_team_budget.py tests/test_team_visibility.py tests/test_team_task_threads.py \
  tests/test_plan.py tests/test_sop.py tests/test_web_assembly.py \
  tests/test_test_layering_guard.py tests/test_agentscope_usage_guard.py
# 177 passed in 38.91s
```

## 门禁读数

从 worktree 根运行：

```sh
scripts/check.sh --fast
# ruff check: All checks passed!
# ruff format --check: 286 files already formatted
# mypy（仓库根）: Success: no issues found in 116 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: tests 258 / pass 258 / fail 0
# 门禁全绿

scripts/check.sh
# 全量：pytest + 上述各段 + node --test → 门禁全绿
```

## 范围与偏差

- **零 schema 变更**：`git diff 357aa89 -- app/alibot/db.py` 为空；`SCHEMA_VERSION` 仍为 23。
- **零新增服务模块 / 零新 seam**：常量、守卫与两层读函数放在既有 `app/alibot/workspace.py`；
  全局读路径是 `AgentScopeAgentSeam.__init__` 的新增注入参数 `global_home`，默认 `None`。
- **未接线行为逐字相同**：`global_home=None` 时 `_rule_layers` 直接返回 `(None, None)`，不读盘；
  `_system_prompt` 的两个新参数默认 `None`，既有调用点（含 `app/tests/scripted.py`）输出不变。
- **本票不做**：Docker 第二个只读 bind 落地（只给 `DOCKER_GLOBAL_HOME` 与容器内路径解析）、
  全局技能 / MCP / 记忆、数据目录解析与备份、任何全局写端点或 UI 入口。
- **已知代价**：规则文件每轮读取、不缓存（规格 Out of Scope 明确把缓存留给实施细节）；
  生产接线后单 Agent 路径多一次 `workspace_settings_for_target` 只读（用于取项目根），
  读失败按「项目补充缺席」降级，不改变本轮结论。
- **日志语言**：沿用仓库既有日志风格（英文、含层名与可读原因），不做 i18n。
