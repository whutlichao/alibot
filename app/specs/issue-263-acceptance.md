# #263 每轮 system prompt 注入运行环境段 验收

- 状态：In progress（待全量门禁与双轴 review 收口）
- Issue：https://github.com/whutlichao/alibot/issues/263
- 基线：`2a359ca`（`Merge pull request #262 from whutlichao/issue-261-impl`）
- 分支：`issue-263-impl`
- 面：`app/alibot/agent_seam.py`、`app/tests/test_runtime_environment_prompt.py`、`app/README.md`
- Door：**双向门**；门禁通过后由 coordinator 合并。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 纯函数只写本轮装配事实：标题、工作区（local 绝对根 / docker `/workspace`）、后端、权限档、记忆两档、段末禁令；不写任何记忆/数据/会话目录路径 | `agent_seam._runtime_environment_section` | `pytest tests/test_runtime_environment_prompt.py` → 4 passed |
| 2 | 三条装配路径接入且位置固定（角色之后、rubric / team 说明之前）：单 Agent、队长、team 成员；SOP 不动 | `agent_seam._system_prompt` / `_team_member_prompt` / 两处 `_runtime_environment_prompt` 调用 | 同上（4 条用例都断言 `你的角色：…` < 环境段 < rubric / team 说明） |
| 3 | 无工作区 + `reme`：明写「未配置」与「已开启」，prompt 不含 `alibot/data`、`memory/`、`~/.alibot`、`.alibot/`、`/sessions/` | `test_unconfigured_workspace_and_reme_memory_are_stated_without_paths` | 同上 |
| 4 | local + `off`：含项目根、`后端：local`、`权限档：trusted-writes`、`长期记忆：未开启` | `test_local_workspace_reports_root_backend_and_permission` | 同上 |
| 5 | docker：模型可见根是 `/workspace`、`后端：docker`，宿主项目根不得进 prompt | `test_docker_workspace_reports_the_container_root_not_the_host_path` | 同上 |
| 6 | team：队长与成员各自拿到自己的记忆档与权限档（队长 `off` / `trusted-writes`，成员 `reme` / `read-only`） | `test_team_leader_and_member_each_get_their_own_environment` | 同上 |
| 7 | 既有顺序断言不破：规则三段 → 身份句 → 角色 | `tests/test_global_rules.py` | `pytest tests/test_global_rules.py` → 23 passed（含三条规则顺序与 team 双路径） |
| 8 | 行为说明 | `app/README.md`「Agent 运行时」一节 | 文档 diff |
| 9 | 全量门禁 | 仓库 | `scripts/check.sh`（读数见下） |
| 10 | 双轴 code-review（Standards + Spec） | 本文件「双轴 code-review」 | 见下 |

## 红证据（隔离基线 `2a359ca`）

把新用例文件拷进 `git archive` 等价的基线 worktree（`git worktree add --detach /tmp/alibot-baseline-263 2a359ca`，
只额外覆盖 `app/tests/test_runtime_environment_prompt.py`），在未实现的代码上跑：

```text
cd /tmp/alibot-baseline-263/app && ../.venv/bin/python -m pytest tests/test_runtime_environment_prompt.py
FAILED ... test_unconfigured_workspace_and_reme_memory_are_stated_without_paths
FAILED ... test_local_workspace_reports_root_backend_and_permission
FAILED ... test_docker_workspace_reports_the_container_root_not_the_host_path
FAILED ... test_team_leader_and_member_each_get_their_own_environment
4 failed in 0.30s
```

失败点在断言本身：prompt 里没有环境段标题 / 项目根 / `/workspace`，不是导入或夹具错误。
基线 worktree 已删除，`/tmp/red-263.txt` 保留完整输出。

## 门禁读数

（全量门禁跑完后回填）

## 双轴 code-review

（review 后回填）

## Answer

（收口时回填）
