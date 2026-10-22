# #235 验收：Docker 第二个只读挂载（成员家目录 + 公共技能库）

> 实施票：<https://github.com/whutlichao/alibot/issues/235>
> 规格正本：#228（`app/specs/agent-home-layers-spec.md` 的 D6、D7、D9、D10、T1/T4/T6）
> 基线：`agent-home-layers-integration` @ `0afdf8e`

## 判据 → 落点 → 证据

| # | 验收判据 | 代码落点 | 测试 |
| --- | --- | --- | --- |
| 1 | 容器配置追加两条只读 bind：成员家目录 → `/alibot-home/agents/<member_id>`，公共技能库 → `/alibot-home/skills`；两个子路径互不冲突 | `workspace.docker_global_binds()`（宿主源 `global_home_root()` + `global_member_home()` / `<根>/skills`，目标用 `docker_global_member_home()` / `docker_global_skills_home()`） | `test_docker_network_policy.py::test_global_binds_are_member_home_then_public_skills_read_only` |
| 2 | 数据目录（库 / 附件 / 台账）不可见；配置里没有对 `~/.alibot` 整根的挂载 | `docker_global_binds()` 只返回成员家目录与 `skills/` 两项；`docker_container_config()` 只追加传入的 bind | `test_the_data_directory_never_enters_the_container_and_the_global_root_is_not_mounted` |
| 3 | 宿主目录不存在 ⇒ 跳过对应 bind，容器照常启动；解析后**不创建**目录 | `docker_global_binds()` 用 `os.path.isdir()` 过滤，全程无 `mkdir` / 写入 | `test_missing_global_directories_are_skipped_and_never_created` |
| 4 | 容器内对两条全局挂载点写入失败；文件工具根仍只有 `/workspace`，`/alibot-home` 被拒绝 | bind 后缀固定 `:ro`；`resolve_in_container_root()` / `ContainerRootGuard` 未改 | `test_global_binds_only_append_to_an_unchanged_container_shape`（`:ro`）；`test_the_global_mount_point_is_still_not_a_file_tool_root`（根检查，回归锁）；daemon：`test_docker_workspace.py::test_docker_mounts_the_global_layer_read_only`（容器内 `printf > …` 写入失败 + 文件工具拒绝） |
| 5 | 容器轮次里全局规则基座可读，与 local 后端一致 | 规则仍由宿主侧 seam 读取（#230）；本票保证容器内 `/alibot-home/agents/<member_id>/rules/AGENTS.md` 指向同一宿主文件 | daemon：`test_docker_mounts_the_global_layer_read_only`（容器内 `cat` 读到基座正文） |
| 6 | 既有容器配置形状（Image / Cmd / WorkingDir / Labels / Env / NetworkMode）逐字不变，只追加 Binds；顺序固定 `/workspace:rw` → 成员家目录 → 公共技能库，绝对路径 + `:ro` | `docker_container_config(..., global_binds: Sequence[tuple[str, str]] = ())`：先写既有字段，再按给定顺序 append | `test_container_config_defaults_to_no_network_and_keeps_the_bind_mount`、`test_the_creation_path_passes_the_network_mode_to_the_container`（两处精确 Binds 断言）、`test_global_binds_only_append_to_an_unchanged_container_shape` |
| 7 | 无 daemon 的配置层断言覆盖上述判据；有 daemon 的端到端在无 daemon 时整组跳过 | — | 本机无 daemon：`test_docker_workspace.py` 读数为 `6 passed, 8 skipped`；新增 daemon 用例沿用 `requires_docker` |
| 8 | 零 schema 变更，`SCHEMA_VERSION` 不 bump；测试不读写真实 `~/.alibot` | 未改 `db.py`；`docker_global_binds()` 只读 HOME | `git diff -- app/alibot/db.py` 为空；`conftest._isolate_home`（session autouse）+ 新用例各自 `monkeypatch.setenv("HOME", tmp_path/...)` |
| 9 | 池复用必须把 bind 规格纳入比较（全局目录后建时旧容器拿不到挂载） | `_DockerEntry.global_binds` + `assemble_docker_workspace` 复用判定；`workspace.global_binds` 在 `initialize()` 前赋值（照 `docker_egress` 形状） | `test_docker_workspace.py::test_a_global_layer_created_later_discards_the_pooled_container`（无 daemon，替身 `_delete_container`） |

## 实现摘要

- `workspace.docker_global_binds(member_id, *, global_root=None)`：新增公开纯函数，返回
  `((宿主源, 容器内目标), ...)`，顺序固定「成员家目录 → 公共技能库」，仅包含 `isdir()` 为真的宿主目录；
  不读取、不创建 `data/`，不返回全局根整根。
- `workspace.docker_container_config(..., global_binds=())`：新参数带默认值（既有 keyword 调用不破）；
  `Binds` = 既有 `host_workdir:/workspace:rw` + 每条 `host:target:ro`；其余字段逐字不变。
- `ProjectDockerWorkspace.global_binds` 类属性（缺省空元组），`_create_and_start_container()` 传给配置。
- `assemble_docker_workspace()`：在池判定与 `initialize()` 之前计算 bind 规格；复用条件加入
  `pooled.global_binds != global_binds`，建容器前赋值 `workspace.global_binds`，池条目记录同一规格。
- `app/README.md` 的「两个后端」段补一句只读挂载事实（文档同步；无新状态 / 端点 / schema）。

## 红 → 绿读数

### 红（实现前）

```sh
# app/
../.venv/bin/python -m pytest tests/test_docker_network_policy.py
# ImportError: cannot import name 'docker_global_binds' from 'alibot.workspace'
# 1 error in 0.19s（collection error）
```

池失效用例的红读数在隔离基线副本上取得（`git archive 0afdf8e` + 只覆盖新测试文件，命令从副本
`app/` 运行）：

```sh
../.venv/bin/python -m pytest \
  tests/test_docker_workspace.py::test_a_global_layer_created_later_discards_the_pooled_container
# TypeError: _DockerEntry.__init__() got an unexpected keyword argument 'global_binds'
# 1 failed in 0.54s
```

### 绿（实现后）

```sh
# app/
../.venv/bin/python -m pytest tests/test_docker_network_policy.py
# 16 passed in 0.07s

../.venv/bin/python -m pytest tests/test_docker_workspace.py
# 6 passed, 8 skipped in 0.84s（跳过 = 本机没有 Docker daemon，含新增 daemon 用例）

../.venv/bin/python -m pytest tests/test_docker_network_policy.py tests/test_docker_workspace.py \
  tests/test_global_rules.py tests/test_local_workspace.py tests/test_agent_round.py \
  tests/test_docker_egress_schema.py
# 101 passed, 8 skipped in 22.84s
```

## 门禁读数

```sh
scripts/check.sh --fast
# ruff check: All checks passed!
# ruff format --check: 292 files already formatted
# mypy（仓库根）: Success: no issues found in 119 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: tests 258 / pass 258 / fail 0
# 门禁全绿
```

Docker daemon 探活读数（本机）：

```sh
docker info --format '{{.ServerVersion}}'
# failed to connect to the docker API at unix:///Users/apple/.orbstack/run/docker.sock …
```

## 范围与偏差

- **未执行 daemon 端到端**：本机无 daemon，新增的 `test_docker_mounts_the_global_layer_read_only`
  按 `requires_docker` 跳过；契约由无 daemon 的配置层断言承担（`Binds` 顺序 / `:ro` / 宿主源 /
  目标路径 / 缺目录跳过 / 数据目录不可见），daemon 用例在有 daemon 的环境补跑容器内写失败与可读性。
- **「容器内写入失败」的配置层等价证据**是 `:ro` 后缀；真正的写失败读数在有 daemon 的环境由上述用例
  给出。宿主侧可写不构成本票的只读证据。
- `/alibot-home` 的根检查用例在实现前即绿（#230 的 `resolve_in_container_root` 已只接受
  `/workspace`），作为回归锁保留，不计入新行为的红读数。
- 宿主 bind 源只做 `abspath`、不做 `realpath`：挂载源来自 HOME 派生的固定两级路径，规格未要求
  对 bind 源做守卫；全局层**读取**的根外 symlink 防护仍由 #230 的 `resolve_global_read_path` 承担。
- 零 schema 变更：未改 `app/alibot/db.py`，`SCHEMA_VERSION` 仍为 23。
- 范围外未做：全局规则 / 技能 / 记忆 / MCP 的装配语义、数据目录解析、任何新文件工具根、权限档变更。
