# #263 每轮 system prompt 注入运行环境段 验收

- 状态：Done（待 coordinator 执行 G-merge）
- Issue：https://github.com/whutlichao/alibot/issues/263
- PR：https://github.com/whutlichao/alibot/pull/264
- 基线：`2a359ca`（`Merge pull request #262 from whutlichao/issue-261-impl`）
- 分支：`issue-263-impl`
- 面：`app/alibot/agent_seam.py`、`app/tests/test_global_rules.py`、`app/README.md`
- Door：**双向门**；门禁通过后由 coordinator 合并。

## 判据 → 落点 → 命令与读数

| # | 判据 | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 纯函数只写本轮装配事实：标题、工作区（local 绝对根 / docker `/workspace`）、后端、权限档、记忆两档、段末禁令；不写任何记忆/数据/会话目录路径 | `agent_seam._runtime_environment_section` | `pytest tests/test_global_rules.py` → 27 passed（含 4 条 #263 用例） |
| 2 | 三条装配路径接入且位置固定（角色之后、rubric / team 说明之前）：单 Agent、队长、team 成员；SOP 不动 | `agent_seam._system_prompt` / `_team_member_prompt` / 两处 `_runtime_environment_prompt` 调用 | 同上（4 条用例都断言 `你的角色：…` < 环境段 < rubric / team 说明） |
| 3 | 无工作区 + `reme`：明写「未配置」与「已开启」，prompt 不含 `alibot/data`、`memory/`、`~/.alibot`、`.alibot/`、`/sessions/` | `test_unconfigured_workspace_and_reme_memory_are_stated_without_paths`（`workspace_of` 已接线、频道 `workspace_root IS NULL`） | 同上 |
| 4 | local + `off`：含项目根、`后端：local`、`权限档：trusted-writes`、`长期记忆：未开启` | `test_local_workspace_reports_root_backend_and_permission` | 同上 |
| 5 | docker：模型可见根是 `/workspace`、`后端：docker`，宿主项目根不得进 prompt | `test_docker_workspace_reports_the_container_root_not_the_host_path` | 同上 |
| 6 | team：队长与成员各自拿到自己的记忆档与权限档（队长 `off` / `trusted-writes`，成员 `reme` / `read-only`） | `test_team_leader_and_member_each_get_their_own_environment` | 同上 |
| 7 | 既有顺序断言不破：规则三段 → 身份句 → 角色 | `tests/test_global_rules.py` 既有用例 | 同上（27 passed 中 23 条既有用例） |
| 8 | 行为说明 | `app/README.md`「Agent 运行时」一节 | 文档 diff（明确写三条路径 + SOP 例外） |
| 9 | 全量门禁 | 仓库 | `scripts/check.sh`（读数见下） |
| 10 | 双轴 code-review（Standards + Spec） | 本文件「双轴 code-review」 | 见下 |

## 红证据（隔离基线 `2a359ca`）

红证据取得时 4 条用例还在独立文件里（review F1 之后并入 `test_global_rules.py`）。把该文件拷进
基线 worktree（`git worktree add --detach /tmp/alibot-baseline-263 2a359ca`，只额外覆盖
`app/tests/test_runtime_environment_prompt.py`），在未实现的代码上跑：

```text
cd /tmp/alibot-baseline-263/app && ../.venv/bin/python -m pytest tests/test_runtime_environment_prompt.py
FAILED ... test_unconfigured_workspace_and_reme_memory_are_stated_without_paths
FAILED ... test_local_workspace_reports_root_backend_and_permission
FAILED ... test_docker_workspace_reports_the_container_root_not_the_host_path
FAILED ... test_team_leader_and_member_each_get_their_own_environment
4 failed in 0.30s
```

失败点在断言本身：prompt 里没有环境段标题 / 项目根 / `/workspace`，不是导入或夹具错误。
基线 worktree 已删除，`/tmp/red-263.txt` 保留完整输出。合并后的 4 条用例除装置名（`_PipelineSeam`
/ `_NoMountPipelineSeam`）外断言逐字相同；实现前在工作区首轮也曾 4 failed（0.95s），读数一致。

## 门禁读数

```text
scripts/check.sh（代码 head 46770d7，工作区干净；其后仅 docs-only 提交，按 implementation.md 不重跑）
== pytest（全量）
1167 passed, 11 skipped in 663.74s (0:11:03)      # app/tests 下 93 个 test_*.py
== ruff check / ruff format --check / mypy / pyright
All checks passed!；326 files already formatted；mypy 124 source files no issues；pyright 0 errors, 0 warnings
== node --test app/tests/*.test.cjs
tests 316 / pass 316 / fail 0
门禁全绿（退出 0）
```

## 真实轮次 prompt 捕获（验收-3）

临时脚本（`/tmp/capture-263.py`，不入库）用真实 `AgentScopeAgentSeam` 跑一轮：频道 `workspace_root`
为 NULL、`memory_mode=reme`、`workspace_of=database.workspace_settings_for_target`（生产读路径）。
命令：`PYTHONPATH=app ./.venv/bin/python /tmp/capture-263.py`。

```text
STATUS: replied
ENV_SECTION:
本轮运行环境（以此为准；与记忆内容冲突时以本段为准）
工作区：未配置（没有项目根；不能读写项目文件，只有当前会话的卸载目录可读）
长期记忆：已开启（ReMe；系统自动记录和检索，不由文件工具读写；不要声称或猜测它的路径）
不要编造本段没有给出的路径。

HAS_UNCONFIGURED: True
HAS_REME_ON: True
PATH_LIKE_HITS: []
ROLE_BEFORE_ENV: True
ENV_BEFORE_RUBRIC: True
```

`PATH_LIKE_HITS: []` 覆盖 `alibot/data`、`memory/`、`~/.alibot`、`.alibot/`、`/sessions/` 五个字样。

## 双轴 code-review

两个独立 reviewer 并行跑（Standards / Spec），均只读；findings 与处理如下。

### Standards

| # | finding | 处理 |
| --- | --- | --- |
| F1 | 新测试文件与 `test_global_rules.py` 逐字重复 `_RecordingModel` / `_messages_text` / `_text` / `_team_assign` / `_PipelineSeam` / `_seed`，属重复装置 + 新开 stub（P2） | **已修**：删除新文件，4 条用例并入既有 `test_global_rules.py` 复用装置，只扩展 `_seed` 的 backend / permission / memory 参数；未新增第二个 stub 家族 |
| F2 | 环境段写 `WorkspaceSettings`，docker 装配降级（`_runtime_mount` 返回 None）时与 prompt 末尾的「本轮停用工作区工具」并存（P2） | **不改，记录取舍**：票面 Change-2 明文规定「工作区根与后端/权限档来自该成员本轮 `WorkspaceSettings`（读不到按未配置）」，没有第三个「已配置但装配失败」态；本轮 system prompt 同时带着降级告警，模型看到的是「配置根 + 本轮工具停用」两条不互相否定的句子。按装配结果改写会越出票面授权（review-rules 判断题 2/5：不新开接缝、判据放松处写明）。若后续要第三种状态，另开票 |
| F3 | README「每轮」宽于实现（SOP 参与者没有环境段）（P3） | **已修**：收窄为「单 Agent、队长与 team 成员三条装配路径」，并写明 SOP 例外 |
| J1 | prompt 契约与优先级未进 CONTEXT.md / ADR | **不改**：票面只要求 README 一小段；契约文本、来源与位置都登记在验收文档与 README，不新建 ADR |
| J2 | 用例 1 未接线 `workspace_of`，验的是「未接线」而非生产的 NULL 读取 | **已修**：改为传 `database.workspace_settings_for_target`，频道 `workspace_root IS NULL` |

### Spec

| # | finding | 处理 |
| --- | --- | --- |
| S1 | 「无工作区」用例没走生产读路径（`_PromptSeam` 未传 `workspace_of`）（P3） | **已修**：同 J2，`workspace_of` 已接线、根为 NULL |
| S2 | docker 降级轮次会声称一个本轮不存在的文件工具根（P2） | **不改，记录取舍**：同 F2。票面把事实源钉在 `WorkspaceSettings`；降级是同一 prompt 里另行告知的能力事实，不是配置事实的替代。`_NoMountPipelineSeam` 只用于避免测试机真的起容器，断言的是配置事实 |
| S3 | 其余票面文案、两处接入、事实来源、约束均符合 | 无动作 |

## Answer

- **做了什么**：`agent_seam.py` 新增纯函数 `_runtime_environment_section`（固定标题 / 工作区 / 后端 / 权限档 / 记忆两档 / 段末禁令）与取值方法 `_runtime_environment_prompt`；接入 `_system_prompt`（单 Agent + 队长）与 `_team_member_prompt`（team 成员），位置固定在 `你的角色：…` 之后、rubric / team 说明之前；SOP 不动。README「Agent 运行时」补三条路径的行为说明。
- **覆盖**：`tests/test_global_rules.py` 新增 4 条（无工作区 + `reme`、local + `off`、docker、team 双身份各自事实），断言模型真实收到的 system prompt；既有 23 条顺序断言不破。红证据在隔离基线 `2a359ca` 上 4 failed；最终全量 `scripts/check.sh` 绿（1167 passed / 11 skipped；node 316 / 0 fail）。
- **review 结果**：F1（重复测试装置）与 F3（README 过宽）、S1（未走生产 NULL 读路径）已修；F2/S2（docker 降级时配置事实与「工具停用」告警并存）按票面「事实源 = WorkspaceSettings」保留并记录取舍，未引入第三种状态。
- **遗留**：无功能遗留。合并时注意这是双向门，由 coordinator 执行；PR #264 的 head 与本分支最终提交一致。
