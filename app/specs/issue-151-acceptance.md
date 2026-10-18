# #151 MCP：配置、装配、远端告知 — 验收

判据来源：GitHub issue #151 的四条 Acceptance；父规格 §3.6 / §4.3 / §4.4 与 ADR-0021 §三 / §五。
所有命令都在本票 worktree 的 `app/` 下执行；读数来自 2026-10-18 的本机运行。

## 落点

| 面 | 落点 |
| --- | --- |
| 声明 CRUD 与校验 | `app/alibot/db.py`（`mcp_server` 读路径 / 写路径 / scope 校验 / 同名冲突 / 确认） |
| HTTP 人类面 | `app/alibot/api.py`：`GET/POST /api/mcp-servers`、`PATCH/DELETE /{id}`、`POST /{id}/consent` |
| 装配 | `app/alibot/mcp.py`（声明 → `MCPClient`、env 按名取值、远端确认过滤、失败可读、生命周期） |
| 轮次接线 | `app/alibot/agent_seam.py`：单 Agent 与 team 轮次都把 MCP 工具挂进同一个 toolkit |
| 文案 | `app/alibot/i18n.py`（校验错误与运行时失败原因，中英同键） |
| 词表 / 端点登记 | `app/README.md` |

## 判据 1：`mcp_server` CRUD；Local/Docker 装配 `MCPClient`；按 agent/session 隔离

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_mcp_servers.py tests/test_mcp_assembly.py
```

**读数**

```text
11 passed, 1 skipped
```

- CRUD / scope 过滤 / PATCH / DELETE / 404：`test_declarations_round_trip_and_scope_filtering`。
- 真实 stdio 子进程物化为 `mcp__stub__echo`：`test_stdio_declaration_becomes_tools_and_reads_env_from_process`。
- 同一工作区里不同 `(agent, session)` 得到不同客户端实例：`test_clients_are_isolated_per_agent_and_session`。
- 真 seam 的下一轮把 `mcp__stub__echo` 送进 provider 的 `tools`：
  `test_a_real_round_exposes_the_mcp_tool_to_the_provider`。
- Docker 路径：`test_docker_backend_materializes_mcp_through_the_workspace_gateway`（本机 Docker daemon
  当前未运行，测试按 `skipif` 跳过；Docker 后端复用 AgentScope 的容器内 MCP 网关，装配代码与 Local
  共用同一入口。启动 Docker 后重跑该用例即可执行成功路径）。

## 判据 2：`env_names` 只存变量名；响应与 UI 不出现 env 值；库里无密钥

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_mcp_servers.py -k env
```

**读数**

```text
1 passed, 4 deselected
```

`test_env_values_never_appear_in_responses_or_the_database` 做四件事：

1. 进程环境里放一个哨兵值，声明只写变量名；
2. `POST` 与 `GET` 的响应正文里断言哨兵值**不出现**、`env_names` 等于 `["ALIBOT_MCP_TEST_TOKEN"]`；
3. 正文带 `env`（值映射）时整条请求 400，且没有落下一行声明；
4. 扫描数据目录全部文件（含 SQLite WAL/SHM）断言哨兵值字节不存在、变量名存在。

UI 面：本票不新增 MCP 前端视图，客户端能看到的唯一形状就是上面这个 HTTP 载荷；它只含
`env_names`。将来 MCP 管理 UI 直接消费该载荷，不存在第二条带值的接口。

## 判据 3：远端未确认不连接；确认幂等；配置动作不落消息/不唤醒/不耗预算

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_mcp_servers.py -k "consent or configuration_changes"
../.venv/bin/python -m pytest tests/test_mcp_assembly.py -k unconfirmed
```

**读数**

```text
2 passed, 3 deselected
1 passed, 6 deselected
```

- `test_remote_consent_is_required_and_idempotent`：远端声明初始 `consented=false`；两次确认返回**同一个**
  `acknowledged_at`；stdio 声明调用确认 400（本机进程不是远端数据面）。
- `test_unconfirmed_remote_is_not_connected_and_consent_unlocks_the_attempt`：用 monkeypatch 的
  `MCPClient.connect` 替身计数——未确认时尝试次数 `0`，确认后次数 `1`；替身抛错仍不阻塞装配。
- `test_configuration_changes_do_not_message_wake_or_spend_budget`：创建 → 确认 → 停用 → 删除前后，
  频道消息表、轮次结果、预算读数逐字相同。

## 判据 4：连接失败可读；不阻塞其他工具

**命令**

```sh
cd app
../.venv/bin/python -m pytest tests/test_mcp_assembly.py -k broken
```

**读数**

```text
1 passed, 6 deselected
```

`test_a_broken_server_is_readable_and_does_not_block_other_tools` 同时声明一个可用的 stdio server 与一个
不存在的可执行文件：装配**不抛异常**，可用 server 的工具照常返回；坏 server 的进程内读数
`connected=false`、`error` 含 `alibot-mcp-binary`（AgentScope 的启动失败原因被转成可读状态）。

## 全量门禁

**命令**

```sh
scripts/check.sh
```

**读数**

```text
门禁全绿
```

同一轮里：`ruff check` 全通过、`ruff format --check` 85 个 Python 文件已格式化、
`mypy` 85 个源文件无问题、`pyright` 0 errors / 0 warnings；Node 用例
`tests 136 / pass 136 / fail 0`。退出码 0（`scripts/check.sh` 末行即「门禁全绿」）。

## 已知残余

- 本机 Docker daemon 未运行，Docker MCP 成功路径的自动化用例被 `skipif` 跳过；Local 与 Docker 共用
  `assemble_mcp_tools` 入口，Docker 侧只多一层 AgentScope 容器内网关。启动 daemon 后重跑
  `tests/test_mcp_assembly.py::test_docker_backend_materializes_mcp_through_the_workspace_gateway`。
- 运行时读数（`connected` / `tools` / `error`）是**进程内**观察面，不落库；进程重启后为 `null`，
  下一轮装配时重新记录。这是刻意的：它不是配置事实，也不该成为数据目录的备份内容。
- 本票不新增 MCP 管理 UI；`env_names` 只进响应载荷，前端按规格 §4.6 只显示变量名。
