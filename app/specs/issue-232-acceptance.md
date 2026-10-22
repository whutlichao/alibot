# #232 验收：技能四层合并（项目就近、同名覆盖）

> 实施票：<https://github.com/whutlichao/alibot/issues/232>
> 规格正本：#228（`app/specs/agent-home-layers-spec.md` 的 D4 技能半边、D6、D9、D10、T1/T2/T4/T6）
> 基线：`agent-home-layers-integration` @ `85c42c1`

## 判据 → 落点 → 证据

| # | 验收判据 | 代码落点 | 测试（`app/tests/test_global_skills.py`） |
| --- | --- | --- | --- |
| 1 | 四层优先级：项目 Agent 级 > 项目频道级 > 全局成员家目录 > 全局公共技能库；同名每名恰好装配一次且来自胜出层；四组逐对断言 | `skills.merge_skill_layers`；`agent_seam._skills_for` 按层传入声明 | `test_four_layer_priority_assembles_only_the_winning_skill`（真实 provider 请求的 system prompt 逐对断言胜出/落选描述） |
| 2 | 落选技能不装配；每个落选条目产生一条含「名字 + 胜出层」的可读运行期提示；提示**不进** system prompt | `skills.render` 词条 `skill.assembly.shadowed`；`agent_seam._skills_for(..., notes)`；收口只拼 `RoundOutcome.reason` | `test_shadowed_skills_get_readable_notes_outside_the_system_prompt`（含「频道级被 Agent 级覆盖」与「全局成员被频道覆盖」两条） |
| 3 | team 路径汇总各成员的落选提示，且不重复、不进队长 system prompt | `_run_team_round_stream` 的 `assembly_notes` + `_skills_for_member` 汇总（`dict.fromkeys` 去重） | `test_team_round_collects_shadow_notes_from_every_member` |
| 4 | 全局技能是纯文件：不进 `skill` 表、无 hash 行；零 schema 变更、`SCHEMA_VERSION` 不 bump；无安装/编辑/删除端点 | `skills.load_global_skills` 只读文件；`db.skill_layers_for_target` 只读查询；未改 schema / 路由 | `test_four_layer_priority_assembles_only_the_winning_skill` 末尾断言库里只有项目层行；`git diff` 无 `SCHEMA_VERSION` / 建表改动 |
| 5 | `SKILL.md` 缺失、非法、目录名与 `name` 不一致、不可读、路径越界（含根外 symlink 祖先）：该条跳过 + 一条可读日志，其余照常装配 | `skills._global_skill_layer` 用 `workspace.resolve_global_read_path`（根 realpath + symlink 检查）逐条校验，并复用 `_skill_files` / `_load_manifest_markdown` | `test_bad_global_entries_are_skipped_and_logged`（missing / mismatch / unreadable / escaped，`caplog` 断言每条日志含名字） |
| 6 | 全局成员技能目录或公共库缺失：该层零条、不报错、不创建目录 | `load_global_skills` 对缺失目录直接返回空层；全程无 `mkdir` / 写入 | `test_missing_global_layers_yield_zero_and_are_never_created`（先断言全局根不存在且轮次后仍不存在；再断言仅公共库存在时成员层零条） |
| 7 | 无项目根的私信轮次不装配技能（维持 `settings is None ⇒ []`） | `_skills_for` 的 `if settings is None: return []`（在读取项目/全局层之前） | `test_direct_message_without_project_root_assembles_no_skill` |
| 8 | Docker 后端下全局技能可见目录映射到 `/alibot-home/...`；容器内路径不存在时按缺席降级 | `load_global_skills(..., docker=True)` 使用 `docker_global_member_home` / `docker_global_skills_home`；字节仍从宿主全局根读 | `test_docker_backend_maps_global_skill_dirs_to_alibot_home`（成员 / 公共两种前缀；删除宿主成员家目录后该层缺席） |
| 9 | 运行一轮前后全局根目录清单不变 | 全局层只经 `resolve_global_read_path` 读取，不创建、不写占位 | `test_missing_global_layers_yield_zero_and_are_never_created` 与 `test_direct_message_without_project_root_assembles_no_skill` 的前后同目录快照断言 |
| 10 | 既有语义不回退：停用项目 Agent 级同名技能时，频道级不复活 | 装配层先按名字排胜出者，再只装配 `enabled` 的胜出者（`skill_layers_for_target` 保留停用声明） | `test_disabled_agent_skill_still_shadows_the_channel_skill` |

## 红 → 绿读数

| 切片 | 红（实现前） | 绿（实现后） |
| --- | --- | --- |
| 四层合并 / 全局加载函数 | `pytest tests/test_global_skills.py` → `ImportError: cannot import name 'SKILL_LAYER_GLOBAL_MEMBER' from 'alibot.skills'`（collection error） | `8 passed` |
| 停用 Agent 级不复活（既有语义锁） | 该用例单独运行 → `AssertionError: assert 'Skill' not in {'Edit', ..., 'Skill', ...}`（频道级违规复活） | 同用例通过 |
| 落选提示 / 坏条目 / 缺失层 / DM / Docker / team | 随第一切片一次性落地；对应用例首次运行即绿，作为行为锁保留 | 同用例通过 |

命令（从 worktree 根运行）：

```sh
.venv/bin/python -m pytest app/tests/test_global_skills.py
# 8 passed in 4.30s

.venv/bin/python -m pytest app/tests/test_skill_assembly.py app/tests/test_local_workspace.py \
  app/tests/test_agent_round.py app/tests/test_team_mode.py
# 84 passed in 16.59s

.venv/bin/python -m pytest app/tests/test_mcp_assembly.py app/tests/test_agent_seam_client_lifecycle.py \
  app/tests/test_round_body_assembly.py app/tests/test_global_rules.py \
  app/tests/test_backend_copy.py app/tests/test_test_layering_guard.py \
  app/tests/test_data_dir_resolution.py
# 65 passed, 1 skipped in 26.39s（skip 为需要 Docker daemon 的用例）
```

## 门禁读数

```sh
scripts/check.sh --fast
# ruff check: All checks passed!
# ruff format --check: 已按仓库格式化
# mypy / pyright: 0 errors
# node --test: tests 258 / pass 258 / fail 0
# 门禁全绿
```

## 范围与偏差

- **零 schema 变更**：`SCHEMA_VERSION` 仍为 23；未改表结构、未新增安装 / 编辑 / 删除端点。
- **零新增服务模块 / 零新 seam**：全局技能装载与合并函数放在既有 `app/alibot/skills.py`；
  项目层未合并读路径是既有 `Database` 的新方法 `skill_layers_for_target`；装配仍是
  `AgentScopeAgentSeam.__init__` 的新增 keyword-only 参数 `skill_layers_of`（默认 `None`）。
- **未接线行为逐字相同**：`skill_layers_of=None` 时退回既有 `skills_of` 的已合并结果；
  `global_home=None` 时不读全局层。
- **`skill_layers_for_target` 返回停用行**：这是对「未合并行必须自行过滤 `enabled`」的偏差
  ——过滤放在**按名字排出胜出者之后**（装配层），否则停用的 Agent 级会消失、频道级同名条目
  会复活，破坏既有测试 `test_agent_scope_overrides_channel_scope_by_name` 所锁定的语义。
  `load_skills` 仍只看到启用的胜出者，停用条目的字节从不读取。
- **全局技能树校验**：复用 `_skill_files`，因此全局技能内部的 symlink / 超大文件也会整条跳过
  （比票面最低要求更严；日志可读，其余技能照常）。
- **团队提示去重**：不同成员产生同一行提示时在收口处按出现顺序去重；单个落选条目仍各有一条。
- **本票不做**：全局 MCP 声明、全局规则 / 记忆、Docker bind 实际落地、全局技能 UI 端点。
