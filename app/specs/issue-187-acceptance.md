# 实施验收：#187 Docker 后端网络策略（默认无网 + host 白名单）

依据 ADR-0026 §五；上游框架事实：AgentScope 2.0.10 的 `DockerWorkspace._create_and_start_container`
只写 `HostConfig.Binds`，**没有网络参数**（`create_or_replace(config=…)` 也不接收网络参数）。

## 判据 → 落点 → 读数

| 票面判据 | 落点 | 读数（命令） |
| --- | --- | --- |
| 容器默认无网（`HostConfig.NetworkMode = "none"`） | `app/alibot/workspace.py`：`docker_container_config()` + `ProjectDockerWorkspace._create_and_start_container` 覆写 | 待补 |
| 默认无网时联网工具返回**可读错误**（不是超时）、**零请求** | `app/alibot/web.py`：`EgressPolicy` + `search_tool` / `fetch_tool` 的出网闸门 | 待补 |
| 显式允许：白名单内 host 成功、白名单外被拒且原因可读 | `web.py` 白名单判定（`url_host()` 单一实现）+ `db.workspace_settings/web_settings` 读路径 | 待补 |
| 不回落：daemon 缺失仍是「成员未启用 + 可读原因」 | 既有 `record_docker_failure` / `DockerWorkspaceUnavailable` 路径不变 | 待补 |
| schema 19：18→19 一步、链连续、旧行不回填 | `app/alibot/db.py` + `migrate.py` + `app/tests/_migration_history.py` | 待补 |

## D3 的落实方式

待补。

## agent 覆盖列的取舍

待补。

## 未实跑项与替代证据

待补。
