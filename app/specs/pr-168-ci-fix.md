# PR #168 CI 修复：runner 权限模型、缺失 ripgrep、未关闭的 gateway/client

## 观测（run 39114279226，head 39cf5e0）

GitHub Actions `check`：`8 failed, 834 passed, 2 skipped, 2 errors`；本机 OrbStack 全绿。

1. `test_docker_workspace.py` 5 个用例 + `test_mcp_assembly.py` 1 个 Docker 用例：
   `PermissionError: [Errno 13] .../.alibot/agents/<member>`。
2. `test_local_workspace.py` 2 个用例：
   `ripgrep error (code 127): [Errno 2] No such file or directory: 'rg'`。
3. `test_event_stream.py::test_round_outcomes_can_be_filtered_by_base_seq` 与
   `test_mentions.py::test_email_like_text_is_not_a_mention` 在 setup 报
   `ExceptionGroup: multiple unraisable exception warnings`（`unclosed socket/transport`、
   `Unclosed connector`，全指向 `/run/docker.sock` 与容器内 gateway）。

## 根因（按 feedback loop 验证）

| 症状 | 根因 | 复现 |
| --- | --- | --- |
| PermissionError | `assemble_docker_workspace()` 在 `workspace.initialize()` **之后**才由 host 调 `_ensure_layout()`；Linux runner 上容器 root 已把 `/workspace/.alibot` 建到 bind mount，host 再 `makedirs(.alibot/agents/<member>)` 没写权限 | 新回归用例把 `.alibot/agents` 在 `initialize()` 里改成 `0555`，旧代码确定性红 |
| rg code 127 | `ubuntu-latest` 没有 ripgrep；Grep/Glob 走真实 `rg` | CI 日志 |
| unraisable ResourceWarning | `release_workspace_mount()` 只关 aiodocker client，没关 gateway（aiohttp `UnixConnector`）；容器仍留池、`DockerWorkspace.close()` 不会走到 | 新回归用例断言 release 后 `_gateway is None`，旧代码确定性红 |

## 修复

1. `app/alibot/workspace.py::assemble_docker_workspace`
   - 在任何容器操作之前由 host 调用 `_ensure_layout(root, member, session)`，容器 `mkdir -p`
     看到已存在的用户目录。
   - `_new_mount()`（新建与复用两条路径）失败时统一 `close()` + 删容器 + 清池 + 记录可读
     `offline` 原因，避免留下未关闭的 client/gateway。
2. `app/alibot/workspace.py::release_workspace_mount`
   - 在创建它的 loop 内 `await gateway.aclose()` 并清空 `_mcp_instances/_mcp_last_used`；
     随后关闭 aiodocker client。容器仍留池，下一轮 `_rebind_entry` 换句柄。
3. `.github/workflows/check.yml`
   - 新增「安装 ripgrep」步骤（`apt-get install -y ripgrep` + `rg --version`），保持测试测真实工具。

## 回归用例

- `tests/test_docker_workspace.py::test_host_creates_the_alibot_layout_before_container_initialize`
  —— 无 Docker 也可跑；断言 host 布局在 `initialize()` 前存在。
- `tests/test_docker_workspace.py::test_release_closes_the_gateway_and_the_docker_client`
  —— release 后 `_gateway is None`、`_client is None`。

红→绿读数：

```text
# 修复前
tests/test_docker_workspace.py::test_host_creates_the_alibot_layout_before_container_initialize
  PermissionError: [Errno 13] .../.alibot/agents/mem-...
tests/test_docker_workspace.py::test_release_closes_the_gateway_and_the_docker_client
  AssertionError: assert <GatewayClient ...> is None
# 修复后
2 passed, 10 deselected
```

## 本机验证

```text
pytest tests/test_docker_workspace.py tests/test_mcp_assembly.py \
       tests/test_local_workspace.py tests/test_event_stream.py tests/test_mentions.py
82 passed in 147.82s
```

全量 `scripts/check.sh`：见 PR #168 的 Evidence 评论（最终 head）。

## 残余

- 容器仍以 root 运行；native Linux 上 Agent 在项目根写入的文件在容器删除前属主是 root。
  这属于 #150 的既有设计（挂载隔离而非 uid 映射），本票只修 host 侧布局创建顺序。
- Docker daemon 不可用时 `requires_docker` 用例仍按既有约定 skip（不是新增的掩盖）。
