# Issue #134 研究：DockerWorkspace 在本机的可用性

- 票型：research（G-research）；规格 `app/specs/agentscope-2-alignment.md` §3.6 / §6 / §7.2（R3）
- ADR：`docs/adr/0021-workspace-is-an-explicit-project-boundary.md`
- 被测版本：`agentscope==2.0.10`；`aiodocker==0.27.0`；Docker Engine 29.8.2（OrbStack）
- 复跑脚本：
  - `app/specs/issue-134-research/probe_docker_workspace.py`（主链路）
  - `app/specs/issue-134-research/probe_failure_modes.py`（失败模式）
  - `app/specs/issue-134-research/probe_process_exit.py`（进程退出清理）
- 本票不改产品源码：`app/alibot/**`、`app/tests/**` 未动。唯一环境改动是共享 venv 安装
  `aiodocker`（见 §一）。

## 结论（给 W3 / #150 用）

1. **W3 前提成立，但实测环境是 OrbStack，不是 Docker Desktop。** `docker version` 客户端
   29.8.0 / 服务端 29.8.2、API 1.56、`linux/arm64`、overlayfs；镜像构建、容器启动、
   `host_workdir` 双向挂载、容器内 Bash/Read/Write、`reset`、`close` 清理全部成功。
2. **`aiodocker` 不是 agentscope 的默认依赖**，它是 extra `workspace-docker`；未安装时
   `DockerWorkspace.initialize()` 在 `import aiodocker` 处抛 `ModuleNotFoundError`。
   W3 必须把 `aiodocker` 声明进部署依赖（或 `agentscope[workspace-docker]`）。
3. **首次使用要网络**：基础镜像 `python:3.11-slim` 拉取约 5 s（213 MB），随后
   apt + uv + PyPI 安装 `agentscope`，整镜像构建 30.4 s；首次 `initialize()` 32.07 s。
   镜像就绪后冷启动容器约 **1.5–2.0 s**（同 id 重开 1.58 s；新工作目录 1.52 s）。
4. **`host_workdir` 挂载语义符合规格**：容器内 `/workspace` 与主机目录双向一致（工具写、
   Bash 写都能在主机看到）；容器 `/tmp`、`/root` 是私有的，主机看不到。
5. **容器内工具没有根限定**：`Read` 可读 `/etc/hostname`，`Write` 可写
   `/root/out-of-workspace.txt`，`Bash` 可 `cat /etc/hostname`。W3/W2 必须在装配层做
   `/workspace` 根检查，不能指望工具自限。
6. **清理是协作式的**：`close()` 删除容器并保留镜像；`reset()` 只删
   `data/`、`skills/`、`sessions/`、`.mcp`，容器继续运行；**进程 `os._exit(0)` 不清理**，
   容器会留在 `docker ps` 里（`AutoRemove=false`、`RestartPolicy=no`）。W3 要自己做空闲回收
   与启动清扫；同名 `create_or_replace` 会在下次 initialize 时替换遗留容器。
7. **失败形状**（全部实测）：无 daemon → `DockerError [900] Cannot connect…`（0.09 s）；
   基础镜像不存在 → `DockerStreamError [0] failed to resolve reference…`（1.52 s）；
   Dockerfile RUN 失败 → `DockerStreamError … non-zero code: 127`（4.24 s）；
   `host_workdir` 是文件 → `FileExistsError [Errno 17]`（0.03 s）；
   非法 gateway 端口 → 30 s 后 `RuntimeError: gateway did not become healthy…`，日志尾部是
   `OverflowError: bind(): port must be 0-65535`。注意：源码想抛带构建日志尾的
   `RuntimeError`，但实测两个构建失败都在 chunk 循环前被 `aiodocker` 抛成
   `DockerStreamError`——W3 两种都要捕获。
8. **一个装配层坑**：`aiodocker.Docker()` 先解析 Docker **context**（`DOCKER_CONTEXT` /
   `~/.docker/config.json` 的 `currentContext`），再退回 `DOCKER_HOST`。本机 context 是
   `orbstack`，所以进程内改 `DOCKER_HOST` **不会**让 workspace 指向坏 socket；探针必须在
   空 `HOME` 的子进程里设置 `DOCKER_HOST` 才测到“无 Docker”。

## 一、环境与依赖

```bash
$ docker version --format '{{json .}}'
{"Client":{"Version":"29.8.0","ApiVersion":"1.56","Os":"darwin","Arch":"arm64",
 "Context":"orbstack"},"Server":{"Version":"29.8.2","ApiVersion":"1.56",
 "Os":"linux","Arch":"arm64"}}
$ docker info --format 'CPUs={{.NCPU}} MemTotal={{.MemTotal}} Driver={{.Driver}}'
CPUs=8 MemTotal=8392925184 Driver=overlayfs
$ .venv/bin/python -c "import agentscope; print(agentscope.__version__)"
2.0.10
```

- 主机：macOS 25.5.0 / darwin arm64，8 核 / 16 GiB；Docker VM 8 核 / 7.8 GiB。
- agentscope 2.0.10 的 METADATA 声明 `Provides-Extra: workspace-docker` +
  `Requires-Dist: aiodocker; extra == "workspace-docker"`。共享 venv 里原本没有。
- 安装方式（本次实际执行；`uv 0.11.22`）：

```bash
uv pip install --python /Users/apple/orca/alibot/.venv/bin/python aiodocker
# Resolved 11 packages in 500ms
# Installed 1 package in 4ms
#  + aiodocker==0.27.0
```

- 效果：`app/pyproject.toml` 未改。W3 落地时应显式加入依赖（推荐
  `agentscope[workspace-docker]` 或 `aiodocker==0.27.0`），否则新机器上 Docker 后端在
  import 阶段就不可用。
- 未安装时的真实形状（安装前实测）：
  `ModuleNotFoundError: No module named 'aiodocker'`，从
  `_docker_workspace._provision_backend` 的 `import aiodocker` 抛出。

## 二、镜像构建 / 拉取 / 冷启动

镜像 tag 是 Dockerfile + COPY 内容的 sha256 前缀（源码
`_make_dockerfile.compute_image_tag`），本次为 `agentscope-workspace:d59f48dc0941`。

| 读数 | 值 | 证据 |
| --- | --- | --- |
| 基础镜像拉取 | ≈5.0 s（213 MB） | build 开始 `11:36:26.63`；`python:3.11-slim` 的 `LastTagTime` = `11:36:31.62` |
| 镜像构建（含拉取） | 30.4 s | `building image` `11:36:26.63` → `finish building` `11:36:57.03` |
| 首次 `initialize()` | 32.07 s | `11:36:26.509` → `11:36:58.824` |
| 镜像大小 | 398,095,069 B（≈380 MiB） | `docker image inspect … {{.Size}}` |
| 基础镜像大小 | 210,734,743 B（≈201 MiB） | 同上 |
| 再次 initialize（同 id，镜像命中） | 1.58 s / 1.56 s | 两次独立跑；日志 `image cache hit 'agentscope-workspace:d59f48dc0941'` |
| 新工作目录（同镜像） | 1.52 s / 1.51 s | 同上 |
| `close()` | 0.205 s / 0.246 s | 容器删除，镜像保留 |
| `reset()` | 0.078 s / 0.080 s | 容器仍在运行 |

时间线（第一次构建，本机日志）：

```text
11:36:26,509 Initialize workspace ... from DockerWorkspace
11:36:26,630 DockerWorkspace: building image 'agentscope-workspace:d59f48dc0941'
11:36:57,035 DockerWorkspace: finish building image 'agentscope-workspace:d59f48dc0941'
11:36:58,824 Finished initializing workspace
```

构建期间需要网络：`apt-get update/install curl ca-certificates ripgrep`、Astral `uv`
安装脚本、`uv pip install -r requirements.txt`（`mcp<2.0.0`、`uvicorn`、`fastapi`、
`httpx`）、以及 `uv pip install --no-deps "agentscope"`。基础镜像和 PyPI 任一不可达都会
让首次 initialize 失败。

## 三、`host_workdir` 挂载与隔离

构造：

```text
DockerWorkspace(workspace_id="probe-…",
                host_workdir=<主机临时目录>,
                env={"PROBE_ENV": "probe-value"})
```

`_create_and_start_container` 生成
`Binds=["<host_workdir>:/workspace:rw"]`（`Mounts` 显示 `Propagation=rprivate`），
`WorkingDir=/workspace`，`Cmd=["sleep","infinity"]`，`AutoRemove=false`。

| 检查 | 读数 |
| --- | --- |
| 容器内 `pwd` / `id` / `uname -m` | `/workspace`、`uid=0(root) gid=0(root)`、`aarch64` |
| 容器内 Python | `Python 3.11.17` |
| `env` 传入 | `PROBE_ENV=probe-value`（Bash `$PROBE_ENV`） |
| 主机预置文件 → 容器 `Read` | `/workspace/seed.txt` → `host-seed` |
| 容器 `Write` → 主机 | `/workspace/from-container.txt`；主机内容 `written-by-container-write\n` |
| 容器 Bash 写 → 主机 | `printf 'shell-write\n' > /workspace/from-container-shell.txt`；主机内容一致 |
| 容器私有 `/tmp` | 容器写 `/tmp/private.txt`；主机 `/tmp/private.txt` 不存在 |
| 容器私有 `/root` | 容器写 `/root/private.txt`；主机工作目录下无 `root/` |
| 主机侧文件属主（macOS） | 容器写的文件在主机显示为当前用户（uid 501），未出现 root 属主问题 |

`list_tools()` 返回六个工具：`Bash`、`Edit`、`Glob`、`Grep`、`Read`、`Write`，
全部绑定 `DockerBackend`。

### 3.1 工具在容器内没有 `/workspace` 根限定

| 调用 | 读数 |
| --- | --- |
| `Read(file_path="/etc/hostname")` | 成功，返回容器 id；`state` 为普通结果 |
| `Write("/root/out-of-workspace.txt", …)` | 成功写入；随后 `ls /root` 可见该文件 |
| `Bash("cat /etc/hostname; ls /root")` | 成功；输出容器 id 与 `/root` 下文件 |

结论：Docker 只提供“容器 vs 主机”的隔离；容器内部仍然是一个 root shell + 全盘可读写。
ADR-0021 §二要求文件工具在 local/docker 都做根检查，W3 必须在装配层把 `/workspace`
当作根（相对路径锚定、`realpath` 后必须落在 `/workspace` 内），不能复用裸 `list_tools()`。

## 四、清理、进程退出与镜像复用

| 场景 | 读数 |
| --- | --- |
| `reset()` | 删掉 `/workspace/data/reset-me.txt`、`.mcp`；`/workspace/reset-kept.txt` 保留；容器 `State.Running=true` |
| `close()` | 容器被删除（`docker inspect` 失败）；镜像 `agentscope-workspace:d59f48dc0941` 仍在 |
| 进程 `os._exit(0)`（未 close） | 容器残留：`Up 1 second`；`AutoRemove=false`、`RestartPolicy=no` |
| 同 `workspace_id` 再次启动 | `create_or_replace` 自动替换旧容器：旧 id `ee17d3bf5050…`、新 id `6756c1972655…`，`docker ps -a` 计数 1 |
| 镜像复用 | 第二次/第三次 initialize 均 `image cache hit`；容器启动 ~1.5 s |

因此 W3 不能假设“进程退出会清理”。可用的回收顺序：

1. 每轮/会话结束或空闲 TTL 到期显式 `await workspace.close()`；
2. 进程信号 / 退出钩子做 best-effort close（仍可能被 `SIGKILL` 绕过）；
3. 启动时按 label `agentscope.workspace=true` + `agentscope.workspace.id` 清扫不在注册表里的
   容器；同名 `as_ws_<workspace_id>` 由 `create_or_replace` 兜底。

## 五、失败模式

复跑：`.venv/bin/python app/specs/issue-134-research/probe_failure_modes.py`（约 35 s，
含 30 s gateway 健康等待）。真实读数：

| 场景 | 异常类型 | 耗时 | 可读形状（截断） |
| --- | --- | --- | --- |
| 无 Docker daemon（空 `HOME` + `DOCKER_HOST=unix:///tmp/issue134-no-docker.sock`） | `aiodocker.exceptions.DockerError` | 0.09 s | `[900] Cannot connect to Docker Engine via unix:///tmp/issue134-no-docker.sock [Cannot connect to unix socket … No such file or directory]` |
| 基础镜像不存在（`python:3.11-issue134-missing`） | `aiodocker.exceptions.DockerStreamError` | 1.52 s | `[0] failed to resolve reference "docker.io/library/python:3.11-issue134-missing": … not found` |
| 基础镜像不兼容（`alpine:3.20`，Dockerfile 需要 apt-get） | `aiodocker.exceptions.DockerStreamError` | 4.24 s | `[0] The command '/bin/sh -c apt-get update …' returned a non-zero code: 127` |
| `host_workdir` 指向普通文件 | `FileExistsError` | 0.03 s | `[Errno 17] File exists: '…/as-ws-fail-mount-…'` |
| gateway 端口非法（`gateway_port=65536`） | `RuntimeError` | 30.0 s | `gateway did not become healthy within 30.0s. Tail of /root/.agentscope/gateway.log: … OverflowError: bind(): port must be 0-65535.` |

补充：

- **“无 Docker”不能用进程内 `DOCKER_HOST` 模拟。** 本机 `currentContext=orbstack`，
  aiodocker 优先解析 context；第一次探针把 `DOCKER_HOST` 指向坏 socket 仍然成功启动。
  这与 W3 的健康检查直接相关：探活/初始化必须尊重 context，或直接调用 `DockerWorkspace`
  并捕获异常，不要只检查 `DOCKER_HOST` 环境变量。
- **构建失败不一定抛源码里那个 `RuntimeError`。** `_build_or_reuse_image` 本想缓冲 build
  日志再抛 `RuntimeError(docker build failed: … + last N lines)`；实测 aiodocker 在 chunk
  循环前就把 `error` chunk 转成 `DockerStreamError`。W3 的异常映射要同时覆盖
  `DockerStreamError`、`DockerError`、`FileExistsError`、`RuntimeError`。
- **端口/资源不足**：`DockerWorkspace` 不发布宿主端口（gateway 端口只在容器内，host
  `PortBindings` 为空），也不暴露 CPU/内存/磁盘限制（`HostConfig` 只有 `Binds`）。因此
  “宿主端口冲突”在它的 API 上不可达；实测的端口失败只有非法 `gateway_port`。资源耗尽只能
  由 daemon 侧以通用 `DockerError` 暴露，未在本机安全复现。Docker VM 资源读数：8 核 /
  7.8 GiB；单镜像 ~380 MiB，容器启动后常驻 `sleep infinity`。

## 六、W3（#150）装配建议

以下按“每 Agent/会话一个容器、`host_workdir=workspace_root`、env、超时、空闲回收”给最小方案。

### 6.1 身份与生命周期

- `workspace_id` 用稳定键派生，例如 `channel-<channel_id>-member-<member_id>`（会话级再拼
  `session-<session_id>`）。不要用随机 UUID：容器名是 `as_ws_<workspace_id>`，稳定 id 才能让
  `create_or_replace` 在崩溃重启后替换遗留容器；同名替换已实测（§四）。
- 一个工作区实例对应一个容器；`initialize()` 后把 `list_tools()` 结果注入 `_round_toolkit`
  （`app/alibot/agent_seam.py` 当前注入 tools 的位置），每轮复用，不要每轮新建。
- `reset()` 语义是“清工作区状态、保留容器与镜像”，与规格 §3.6 的“reset 删除容器可写层但保留
  `host_workdir`”不完全一致：2.0.10 的 reset 不删容器、只删 `data/skills/sessions/.mcp`，
  也不删绑定目录里的用户文件。W3 若要“删容器可写层”，应 `close()` + 重新 `initialize()`；
  若只想清 AgentScope 状态，用 `reset()`。两者都要在报告/UI 里写清。
- 空闲回收：维护 `last_used`，TTL（建议 10–30 分钟）到期 `close()`；镜像保留，下次冷启动
  ~1.5 s。不要设置过短 TTL（每轮 1.5 s 起）。

### 6.2 工作目录与状态分区

- `host_workdir = realpath(channel.workspace_root)`；先校验：绝对路径、存在、是目录、
  可写、且不在数据目录内。`os.makedirs(host_workdir, exist_ok=True)` 是上游行为，
  不要把“不存在”当失败；但传普通文件会得到 `FileExistsError`（§五）。
- **布局冲突（需要 W1/W3 共同裁定）**：`DockerWorkspace.workdir` 固定为 `/workspace`，
  `WorkspaceBase` 把 `data/`、`skills/`、`sessions/`、`.mcp` 直接放在 workdir 根
  （`_utils.py`: `DEFAULT_DATA_DIR="data"` 等）。规格 §3.6 要求这些状态在
  `<root>/.alibot/agents/<member_id>/` 与 `<root>/.alibot/sessions/<session_id>/`。
  可选方案：
  1. 薄子类覆盖 `_data_dir` / `_skills_dir` / `_sessions_dir` / `_mcp_file`，把它们指到
     `/workspace/.alibot/...`；`workdir` 仍是 `/workspace`，工具根检查也用它。这是最小改动，
     但依赖上游私有属性，必须 pin 2.0.10 并加回归测试。
  2. 接受 AgentScope 的根级 `data/skills/sessions/.mcp`，只在应用自己的 metadata 上用
     `.alibot`；这与规格文字冲突，需要产品裁定，研究票不替它决定。
- 容器 `/workspace` 是**多个 Agent 共享**的（同一频道根）；“每 Agent 一个容器”隔离的是
  进程、`/tmp`、`/root` 和容器可写层，不是项目文件。成员间文件共享是规格预期，但意味着
  Agent A 的容器可以写 Agent B 的项目文件；权限档位（W2）只能约束工具调用，不能做文件级隔离。

### 6.3 env

- `env` 只放装配层显式挑选的变量（MCP/Skill 所需的 `env_names` 对应的进程环境值）；不要
  透传整个 `os.environ`。
- 实测 `env` 会出现在容器配置里（`docker inspect` 可见明文），所以**不要把密钥放进去**，
  除非该 MCP 确实需要，并在设置页说明“容器 env 可被本机 Docker 用户读取”。这与 ADR-0021
  “库不存 env 值”一致；值的生命周期只在进程/容器内。
- 模型凭证不需要进容器：Agent 的模型调用在宿主进程（`agent_seam`），容器只跑工具。

### 6.4 超时

- `initialize()` 没有任何内建总超时；构建失败会抛异常，但网络挂起可能很久。装配层用
  `asyncio.wait_for`：首次/可能构建建议 180 s，已知镜像存在建议 30 s；超时后
  `await workspace.close()` 并标 offline。
- gateway 健康等待是固定 30 s（`_sandboxed_base.py`），失败信息含 `gateway.log` 尾部
  （§五），可直接作为 `offline` 原因。
- 容器内 Bash 工具默认 120 s、上限 600 s（`Bash.call(timeout=…)`，单位 ms）；`Read` /
  `Write` 没有超时参数，靠装配层的轮次级取消。
- 不要在 `asyncio.wait_for` 外层取消后立刻重试同一个实例；失败实例要 `close()` 丢弃。

### 6.5 无 Docker / 构建失败时的 `offline`

- 判定：不要只看 `DOCKER_HOST`。直接尝试构造并 `initialize()`（或先 `system.info()` 探活），
  捕获：
  - `ModuleNotFoundError` / `ImportError`（没装 `aiodocker`）；
  - `DockerError`（`[900] Cannot connect…`，daemon 不在）；
  - `DockerStreamError`（拉取/构建失败，消息已含 registry/命令）；
  - `RuntimeError`（gateway 未就绪，含日志尾）；
  - `FileExistsError` / `PermissionError`（挂载路径配置错误）。
- 把异常映射成 Agent 状态 `offline` + `enabled_reason`（例如“Docker 不可用：Cannot connect to
  Docker Engine via …”“镜像构建失败：failed to resolve reference …”）；**不静默回落**
  `LocalWorkspace` 或本地 Bash。回退只能由人类显式改配置。
- 失败原因短 TTL 缓存（如 30 s），避免每轮重试 30 s gateway 超时导致轮次堆积；用户改配置或
  手动重试时清缓存。
- 影响面：一个频道里多个 Agent 各自 initialize 会各自建容器；daemon 不可用时每个都要给出
  可读原因，不要只报第一条。

### 6.6 权限与工具过滤

- `list_tools()` 无条件包含 `Bash`；`read-only` / `ask-writes` 必须过滤或包审批。
- 文件工具在容器里可访问 `/`（§3.1），装配层需对 `Read/Write/Edit/Glob/Grep` 做
  `/workspace` 根检查；`Bash` 只能靠容器边界 + 审批，不能承诺根限定。
- 不要在 `DockerWorkspace` 之外再拼一个“本地 fallback”工具集。

### 6.7 镜像与依赖

- 镜像 tag 只覆盖 Dockerfile + COPY 文件，**不包含**从 PyPI 安装的 `agentscope` 版本
  （安装语句是 `uv pip install --no-deps "agentscope"`，未 pin）。宿主升级 agentscope 或
  镜像层内容变化后，旧 tag 仍会命中缓存，gateway venv 里可能是旧版本。W3 应在升级时显式
  bump 镜像 tag 或删除旧 `agentscope-workspace:*`，并在装配层记录使用的 tag。
- `base_image` 覆盖只接受已有镜像或 registry 引用（规格 §3.6）；不存在时的错误形状见 §五。
- 首次构建需要网络；无网络时不要把容器启动失败误报成“Docker 不可用”，应按错误消息区分
  “拉取/构建失败”。

## 七、判据对照

| 票面判据 | 落点 | 读数 |
| --- | --- | --- |
| Docker 版本、`aiodocker` 安装方式、构建/拉取/冷启动耗时 | §一、§二 | 29.8.2 / 0.27.0（`uv pip install`）/ 拉取 ≈5 s、构建 30.4 s、首次 32.07 s、冷启动 1.5–2.0 s |
| `host_workdir` 挂载一致；容器私有目录隔离 | §三 | 工具读/写、Bash 写主机均可见；容器 `/tmp`、`/root` 主机不可见 |
| 容器清理、进程退出清理、镜像复用；失败模式 | §四、§五 | close 删容器留镜像；`os._exit` 留容器（AutoRemove=false）；同 id 替换；cache hit；五类失败形状 |
| W3 装配建议 | §六 | 身份、mount、env、超时、offline、空闲回收、根检查、镜像缓存 |
| 报告在 `app/specs/`、命令可复跑、不改产品源码 | 本文件 + 三个探针 | `git diff --stat` 仅 `app/specs/issue-134-research*` |

复跑（在 worktree 根）：

```bash
.venv/bin/python -c "import agentscope, aiodocker; print(agentscope.__version__, aiodocker.__version__)"
docker version --format '{{.Server.Version}}'

# 主链路（首次会构建镜像：网络 + ~30 s；之后 ~7 s）
env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
  .venv/bin/python app/specs/issue-134-research/probe_docker_workspace.py

# 失败模式（约 35 s，其中 30 s 是 gateway 健康等待）
env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
  .venv/bin/python app/specs/issue-134-research/probe_failure_modes.py

# 进程退出清理（输出容器名；脚本故意不 close）
env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY \
  .venv/bin/python app/specs/issue-134-research/probe_process_exit.py
docker ps -a --filter label=agentscope.workspace=true
```

每个探针只写自己的临时目录；主探针会留下 `as-ws-probe-*` 临时目录（macOS `/var/folders/...`），
可用 `rm -rf` 清理。探针不会动产品数据。

## 八、残余风险与未验证项

- **厂商差异**：本机是 OrbStack（Docker Engine 29.8.2），票面写的是 Docker Desktop。挂载、
  权限映射、冷启动在 Docker Desktop 上可能不同；`chown` 分支只在 Linux 原生 Docker 生效
  （源码 `sys.platform == "linux"`）。未在 Docker Desktop 上复测。
- **CI 无 Docker**：本票读数不能进 CI；W3 的测试需以“有 daemon 时跑、无 daemon 时跳过或
  mock”的分层，不能把 Docker 变成默认依赖。
- **资源限制**：`DockerWorkspace` 不暴露 CPU/内存/磁盘限制；一个容器里的 Bash 可以吃满
  7.8 GiB VM。W3 若需要限额，得自己创建容器或扩展 HostConfig（超出上游 API）。
- **网络隔离**：容器默认走 bridge，没有网络隔离；规格也只承诺进程/文件系统隔离。
- **进程退出**：信号/`atexit` 只能 best-effort；`SIGKILL`、宿主崩溃会留下容器，依赖启动清扫。
- **私有 API 依赖**：`.alibot` 布局若通过覆盖 `_data_dir` 等私有 property 实现，会随上游小版本
  变化；升级 agentscope 时要重跑本探针。
- **构建可复现性**：镜像内 `agentscope` 未 pin，PyPI 内容变化会改变 gateway venv；tag 不感知。
- **端口/资源不足**：未做真实的 daemon 资源耗尽实验（会破坏共享 daemon）；只验证了非法端口
  的失败形状与“无 host 端口映射”这一结构事实。
