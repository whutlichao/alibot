# Issue #150 验收：Docker 后端（按 Agent/会话隔离）

- 票型：impl（G-impl）；规格 `app/specs/agentscope-2-alignment.md` §3.6；ADR-0021
- 研究依据：`app/specs/issue-134-research.md`（OrbStack 29.8.2 / linux arm64、aiodocker 0.27.0、
  失败形状、跨 loop 复用约束）
- 测试入口：`app/tests/test_docker_workspace.py`（真实 daemon，无 daemon 时整组 skip）

## 一、落点

| 文件 | 变更 |
| --- | --- |
| `app/alibot/workspace.py` | `WorkspaceSettings.docker_image_consented`；`DockerWorkspaceUnavailable`；`ProjectDockerWorkspace`（`.alibot` 布局）；`ContainerRootGuard`（容器路径映射到 `host_workdir` 后 `realpath`）；`assemble_docker_workspace` / `assemble_workspace`；env 白名单；失败短 TTL 缓存；容器池（复用 / 空闲 TTL / 进程退出 / 启动清扫）；`reset_docker_workspace` |
| `app/alibot/agent_seam.py` | 按后端分派装配；docker 失败以可读原因收口为 `failed`；轮次结束 `release_workspace_mount`（本地 close、Docker 归还池）；**不把 docker 改写成 local** |
| `app/alibot/orchestration.py` | `WakeOrchestrator(workspace_unavailable=…)`：装配层记录的 Docker 原因进入 `availability`，成员状态点 `offline`、`enabled_reason` 可读 |
| `app/alibot/api.py` | 生产装配注入 `docker_unavailable_reason`；lifespan 启动清扫 + 空闲回收线程，关停 best-effort 清容器 |
| `app/alibot/db.py` | `has_remote_consent_scope` / `record_remote_consent_scope` / `list_remote_consent_scopes`；`workspace_settings_for_target` 返回 `docker_image_consented` |
| `app/alibot/service.py` | 频道配置写入后清 Docker 失败缓存（用户改配置/重试立即生效） |
| `app/alibot/i18n.py` | `workspace.docker-*` 与 `error.remote_scope_*`（zh/en 同键集） |
| `app/pyproject.toml` | 显式依赖 `aiodocker==0.27.0`（AgentScope 的 `workspace-docker` extra） |

关键行为：

- `workspace_id = alibot-<member>--<session>`，容器名 `as_ws_<id>`；稳定 id 让崩溃重启后
  `create_or_replace` 与启动清扫都能识别同一工作区。
- `host_workdir = realpath(channel.workspace_root)` 挂到容器 `/workspace`；容器私有 `/tmp`、`/root`。
- 容器内工具集 = `Bash` + 五个文件工具；文件工具输入先锚到 `/workspace`，映射到
  `host_workdir` 做 `realpath` 根检查（`/etc`、`/root`、`../`、根外 symlink 都返回 ERROR）。
- env 只放 `LANG` / `LC_ALL` / `TZ`（`DOCKER_ENV_ALLOWLIST`）；不透传进程环境、不把 API key 带进容器。
- 一轮结束只关 aiodocker client（必须在创建它的 loop 内），容器留在池里；下一轮换句柄复用。
  空闲 600 s 回收；进程退出 `atexit` best-effort；启动按 `agentscope.workspace=true` +
  `agentscope.workspace.id` 前缀 `alibot-` 清扫遗留容器。
- 远端镜像：本机已有 → 直接用；本机没有 → 必须有 `remote_consent_scope('docker', image)`，
  未确认时**不拉取**、该 Agent offline 并给出可读原因。
- `reset` = 删除容器（可写层）并清池条目；`host_workdir` 与项目文件逐字保留。
- 失败原因缓存 30 s，避免每轮重试 30 s gateway 健康等待；频道配置写入会清缓存。

## 二、判据与读数

环境（本机）：

```console
$ docker version --format '{{.Server.Version}}/{{.Server.Os}}/{{.Server.Arch}}'
29.8.2/linux/arm64
$ docker info --format 'CPUs={{.NCPU}} MemTotal={{.MemTotal}} Driver={{.Driver}}'
CPUs=8 MemTotal=8392925184 Driver=overlayfs
$ .venv/bin/python -c "import agentscope, aiodocker; print(agentscope.__version__, aiodocker.__version__)"
2.0.10 0.27.0
```

### 1. `agent_config.workspace_backend='docker'` 时为该 Agent/会话启动容器；`/workspace` 与频道根一致

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_docker_workspace.py \
    -k mounts_the_channel_root -v
tests/test_docker_workspace.py::test_docker_mounts_the_channel_root_and_tools_work_inside_the_container PASSED
```

用例事实：装配后容器名 `as_ws_alibot-<member>--<session>`；容器内 `pwd` 为 `/workspace`，
`cat /workspace/seed.txt` 读到主机 `host-seed`；容器 `Write` / Bash 写入的文件在主机
`host_workdir` 可见；主机写入的文件容器 `Read` 可见；`<root>/.alibot/agents/<member>` 与
`<root>/.alibot/sessions/<session>` 存在（与本地后端同一布局）。

### 2. 容器内 Bash/Read/Write 可用；容器外主机路径不可见

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_docker_workspace.py \
    -k isolated_and_file_tools_are_rooted -v
tests/test_docker_workspace.py::test_container_filesystem_is_isolated_and_file_tools_are_rooted PASSED
```

读数：`test -e <主机 tmp_path>` 在容器里输出 `invisible`；容器写 `/tmp/<marker>`、`/root/<marker>`
后主机看不到同名文件；`Read("/etc/hostname")`、`Write("/root/escape.txt")`、
`Read("../seed.txt")`、`Write("/workspace/../../escape.txt")` 全部
`ToolResultState.ERROR`；`Read("seed.txt")`（相对路径）成功。容器 Bash 仍可按审批执行
`/etc` 等容器内路径——Docker 只承诺容器 vs 主机隔离（ADR-0021 §二/§三）。

### 3. 空闲回收、进程退出清理、镜像复用；无 Docker 时 offline 且不静默回落

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_docker_workspace.py \
    -k 'reuses_the_container or shutdown_deletes or startup_sweep or failed_initialization' -v
...reuses_the_container_then_reclaims_it_when_idle PASSED
...test_shutdown_deletes_running_containers PASSED
...test_startup_sweep_removes_orphan_alibot_containers PASSED
...test_failed_initialization_is_readable_and_never_falls_back_to_local PASSED
```

- 复用：第二轮 `mount.reused is True`，容器名相同；空闲超过 TTL 后 `sweep_idle_docker_workspaces`
  返回该容器名并删除；`host_workdir` 文件不变。
- 进程退出：`shutdown_docker_workspaces()`（atexit / lifespan 关停共用）返回容器名，容器被删；
  `os._exit` 的遗留容器由启动清扫按 `alibot-` 前缀删除，其它前缀（例如 #134 探针）不动。
- 镜像复用：AgentScope 镜像 tag 命中 `image cache hit`（研究 #134 §二；本机 1.5–2.0 s 冷启动、
  首轮构建 30.4 s），容器删除不删镜像。
- 无 Docker：真实 `DockerError [900] Cannot connect …` 与超时/构建失败被映射为
  `workspace.docker-*` 词条；`assemble_workspace` 抛 `DockerWorkspaceUnavailable`，seam 的
  `RoundOutcome.status == "failed"` 且 `reason` 含原始错误；`docker_unavailable_reason` 让
  `WakeOrchestrator.availability` 返回 `ok=False`（状态点 `offline`）。该轮**没有**装配本地
  `LocalWorkspace` / 本地 Bash。

### 4. 远端镜像/registry 走数据面告知

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_docker_workspace.py \
    -k 'remote_image or scope_consent' -v
...test_remote_image_requires_consent_scope PASSED
...test_db_scope_consent_gate_reaches_workspace_settings PASSED
```

读数：`docker_image='registry.example/agent-runtime:1.2.3'` 且本机无该镜像、无
`remote_consent_scope('docker', image)` ⇒ 抛 `DockerWorkspaceUnavailable`，原因含镜像名与
“确认”，**没有触发 pull/build**；`record_remote_consent_scope('docker', image)` 后
`workspace_settings_for_target` 的 `docker_image_consented=True`，装配才继续
（后续拉取/构建仍受超时与镜像 tag 缓存约束）。

### 全量门禁

```console
$ scripts/check.sh --fast
门禁全绿
$ scripts/check.sh
门禁全绿（exit 0；ruff check / ruff format --check / mypy / pyright / 全量 pytest / Node 用例）
```

## 三、残余与边界

- **平台**：本机是 OrbStack Docker Engine 29.8.2（linux/arm64），未在 Docker Desktop 上复测；
  挂载 uid 映射在 Linux 原生 Docker 上走上游 `chown` 分支，本机未覆盖。
- **UI / HTTP 确认入口**：本票只落装配层与 DB gate；`docker_image` 与 scope 确认的人类面写入
  端点/界面不在本票（规格 §4.4 未列 Docker consent 端点）。在管理 UI 接入前，自定义远端镜像
  会以“需要确认”原因保持 offline，**不会偷偷拉取**。
- **资源限制**：AgentScope `DockerWorkspace` 不暴露 CPU/内存/磁盘限额；容器内 Bash 可吃满
  Docker VM（8 核 / 7.8 GiB 读数）。超出上游 API，本票不扩展 HostConfig。
- **网络隔离**：Docker 只提供进程/文件系统隔离；容器默认走 bridge，远端 MCP 仍是数据面。
- **私有 API 依赖**：`.alibot` 布局覆盖上游 `_data_dir` / `_skills_dir` / `_sessions_dir` /
  `_mcp_file`；跨 loop 复用依赖 `_client` / `_container` / `_backend` 换绑。升级 agentscope
  必须重跑本票测试与 #134 探针。
- **进程退出**：`SIGKILL` / 宿主崩溃不执行 atexit，依赖下一次启动清扫（只扫 `alibot-` 前缀）。
  启动清扫假设**每个 Docker daemon 只跑一个 alibot 实例**；多实例共享 daemon 时会互删同前缀容器。
- **失败缓存**：30 s 内同一 Agent 的后续轮次直接 offline；频道配置写入会清缓存，Agent 级
  `docker_image` 配置写入口（#154/#156）接入时也应清缓存/scope。
