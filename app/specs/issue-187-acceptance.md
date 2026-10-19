# 实施验收：#187 Docker 后端网络策略（默认无网 + host 白名单）

依据 **ADR-0026 §五**（容器默认 `--network none`、显式允许才放开白名单）；票面另要求
「`web_access` 已开但容器未允许出网 ⇒ 工具返回**可读错误**（不是超时）」「Docker 不可用仍是
成员未启用、不回落」。本机**没有 Docker daemon**，凡依赖真实容器的判据在下面「未实跑」一节
如实标注并给出替代证据（参照 ADR-0024 对缺项的处置）。

测试只断言外部可观察行为（工具结果、库级 CHECK、迁移报告、装配给容器的配置 dict、迁移后的列集）；
进程内替身只用在既有两个接缝上：`HttpTransport`（#185 的出网传输）与 aiodocker client（上游
`create_or_replace` 的调用点）。

## 判据 → 落点 → 读数

| 票面判据 | 落点 | 用例（绿） |
| --- | --- | --- |
| 容器默认无网 | `workspace.docker_container_config()` + `ProjectDockerWorkspace._create_and_start_container` 覆写 | `test_docker_network_policy.py::test_container_config_defaults_to_no_network_and_keeps_the_bind_mount` / `::test_the_creation_path_passes_the_network_mode_to_the_container` |
| `web_access` 已开、容器未允许出网 ⇒ 可读错误、**零请求** | `web.EgressPolicy` + `search_tool` / `fetch_tool` 的出网闸门；装配层按 `backend == 'docker'` 注入 | `test_web_assembly.py::test_docker_container_egress_off_mounts_the_tools_but_sends_zero_requests`、`test_docker_network_policy.py::test_egress_off_blocks_search_with_zero_requests` / `::test_egress_off_blocks_fetch_with_zero_requests` |
| 显式允许 + 白名单：内成功、外被拒且原因可读 | 白名单 = `permission_rule(fetch, allow, pattern=<host>)` 的 host（`web.egress_hosts`，host 提取仍只有 `web.url_host` 一份） | `test_web_assembly.py::test_docker_egress_on_allows_only_the_hosts_in_the_fetch_allow_rules`、`test_docker_network_policy.py::test_egress_on_still_refuses_hosts_outside_the_whitelist` / `::test_egress_on_allows_a_whitelisted_host` / `::test_the_whitelist_comes_from_fetch_allow_rules_only` |
| 不回落：daemon 缺失 ⇒ 成员未启用 + 可读原因 | 既有 `record_docker_failure` / `DockerWorkspaceUnavailable` 路径不变（本票不新增回落分支） | `test_docker_network_policy.py::test_container_egress_never_falls_back_to_a_local_workspace`（本地装配被替身改成「一调就炸」，仍然只得到 `DockerWorkspaceUnavailable`） |
| schema 19：18→19 一步、链连续、旧行不回填 | `db.SCHEMA_VERSION`、`migrate._step_18_to_19`、`app/tests/_migration_history.py` | `test_docker_egress_schema.py`（两条）、`test_migration_chain_guard.py`（既有，真实子进程） |
| 本地后端行为逐字不变 | `EgressPolicy` 只由装配层在 `backend == 'docker'` 时注入，`local` 传 `None` | `test_docker_network_policy.py::test_local_backend_sends_the_request_as_before` + 既有 `test_web_assembly.py` / `test_web_tools.py` 全绿 |

## D3 的落实方式

- **白名单来源只有一处**：`permission_rule` 里 `tool_name='fetch'` 且 `behavior='allow'` 的
  `pattern`，用 `web.url_host()` 收成 host（与 `permissions._matched_pattern`、`FetchTool.match_rule`
  是**同一份实现**）。不新建 host 白名单表，也不改 `permission_rule` 的既有语义。
- 合并在读路径 `Database.web_settings_for_target` 里做，且**复用** `permissions.effective_rules`
  （Agent 级同键覆盖频道级）——不写第二份合并逻辑。
- 两处「不算白名单」的边界写在 `web.egress_hosts()` 的文档里并有断言：**空 pattern**（= 该工具的
  全部调用）不产生 host（否则就成了「允许一切」）；`ask` / `deny` 与别的工具名不进来。
- 开关本身是**频道级**一列（见下一节），容器侧只消费它：`0 ⇒ NetworkMode=none`、
  `1 ⇒ bridge`。Docker 的 `NetworkMode` 没有「按 host 过滤」这一档，所以 host 判定只能落在工具层
  ——而工具跑在**宿主进程**里（`urllib`），容器无网并不会让它们失败。这条差别正是本票必须在装配/
  工具层落实闸门的原因，代码注释与 `EgressPolicy` 的文档字符串都写明了。

## 新配置位：`channel.docker_egress`（schema 19）

- 形态：`docker_egress INTEGER NOT NULL DEFAULT 0 CHECK (docker_egress IN (0, 1))`，
  `0` = 容器 `--network none`（缺省），`1` = 显式允许出网。
- 迁移：`18 → 19` 一步（`_step_18_to_19`），只 `ADD COLUMN`、**不回填**；旧频道停在 `0`。
  这**不是**「回填旧数据」，而是本版更严的缺省值——ADR-0026 §五 的裁定就是「容器默认无网」，
  升级不该把旧频道静默留在有网状态（验收文档此处记一句理由，属于细节自决）。
- `_migration_history.py` 留档：新增 `channel_v19` 对象、`VERSION_OBJECTS[19]`、
  `SOURCE_COMMITS[19] = 'pending'`（合并后回填合并提交）。
- 写路径：`PATCH /api/channels/{channel_id}/config` 接受 `docker_egress`（布尔）；显式 `null` 与
  其余非空字段同一条纪律（`400`），因为它是两态开关而不是「未配置」。改配置**不落消息、不唤醒、
  不消耗预算**（既有 `update_channel_config` 路径），并清掉 Docker 失败短缓存。
- 池失效：`assemble_docker_workspace` 把 `docker_egress` 记进池条目，与 `docker_image` 同一条
  纪律——开关变了就删掉旧容器重建，否则「改了配置却没生效」。

### 是否给 `agent_config` 也加覆盖列：**不加**（自决，理由如下）

- 票面范围明写「只新增**一个频道级**开关列」；`workspace_backend` / `permission_mode` /
  `web_access` 之所以有 Agent 级覆盖，是因为它们是「这个成员用哪个后端 / 什么档 / 挂什么工具」；
  容器出网是**频道的数据面边界**（ADR-0026 §五 的描述也是「容器默认无网」这一频道级事实）。
- #184 的 D5 已定「Agent 级字段显式 `null` 一律 400」，于是**一旦加覆盖列，就再也回不到
  「跟随频道」**。为一个票面没要求、且天然是频道边界的开关付这个代价不划算。
- 代价（如实记）：Agent 粒度不能单独开/关出网；要更细就得改频道或给该成员换 `local` 后端。

## 门禁读数

- 全量 `scripts/check.sh`（合并 `issue-182-integration` 之后跑）：
  - pytest：`931 passed, 10 skipped in 557.27s (0:09:17)`
  - ruff：`All checks passed!`（含 `ruff format --check`）
  - mypy + pyright：`0 errors, 0 warnings, 0 informations`
  - node `--test app/tests/*.test.cjs`：`tests 191 / pass 191 / fail 0`
  - 末行：`门禁全绿`
- 开发循环：`scripts/check.sh --fast tests/test_docker_network_policy.py tests/test_docker_egress_schema.py tests/test_web_assembly.py tests/test_web_access_schema.py` 全绿。

## 红证据（红→绿）

本票的红证据用**变异**方式逐条取证（临时把实现改坏/回退，跑同一批用例看它们转红，然后还原；
`git status --porcelain` 在每组取证后为空）：

1. **容器默认无网**：把 `docker_container_config()` 里的
   `host_config = {"NetworkMode": network_mode}` 改成 `{}` ⇒
   `test_container_config_defaults_to_no_network_and_keeps_the_bind_mount` 红于 `KeyError: 'NetworkMode'`，
   `test_the_creation_path_passes_the_network_mode_to_the_container` 同样红。还原后两条绿。
2. **开关关闭 ⇒ 零请求**：把 `web.py` 的两处 `if egress is not None and not egress.enabled:`
   短路成 `if False and …` ⇒ `test_egress_off_blocks_search_with_zero_requests`、
   `test_egress_off_blocks_fetch_with_zero_requests` 红（工具真的把请求发了出去，`transport.requests`
   不再为空 / 结果文本里没有「容器网络策略」）。还原后两条绿。
3. **schema 19**：把 `db.SCHEMA_VERSION` 回退成 `18` ⇒
   `test_migration_from_18_adds_the_column_without_backfilling` 红于 `assert (18, 19) in []`
   （迁移报告里没有这一步）。还原后绿。
4. **白名单外拒绝**：把 `if host is None or host not in egress.allowed_hosts:` 收缩成
   `if host is None:` ⇒ `test_egress_on_still_refuses_hosts_outside_the_whitelist` 红（结果从
   「可读拒绝」变成 `success`，请求真的发了出去）。还原后绿。

## 未实跑项与替代证据（本机无 Docker daemon）

| 判据 | 状态 | 替代证据 |
| --- | --- | --- |
| 真容器以 `--network none` 启动、容器内 `curl` 失败 | **未实跑** | `test_the_creation_path_passes_the_network_mode_to_the_container`：把 aiodocker client 换成进程内替身，断言交给 `create_or_replace` 的 `HostConfig.NetworkMode == "none"`（同一个 dict 还带着 `/workspace` 的 bind mount，证明其余配置未被改写） |
| 显式允许时容器真的能出网（`bridge`） | **未实跑** | 同上第二条断言（`NetworkMode == DOCKER_EGRESS_NETWORK`）；端到端留待有 daemon 的 CI/开发机（`test_docker_workspace.py` 的 `@requires_docker` 族） |
| daemon 缺失时成员状态点未启用、原因可读、不回落本地 | **未实跑**（真实 daemon 缺席）+ 已在无 daemon 环境用替身取证 | `test_container_egress_never_falls_back_to_a_local_workspace`：`initialize` 抛 `DockerError(900)`、本地装配被改成「一调就炸」，仍然只得到 `DockerWorkspaceUnavailable` 且失败原因进了短缓存；既有 `test_docker_workspace.py::test_failed_initialization_is_readable_and_never_falls_back_to_local` 覆盖同一族 |
| 容器池在出网开关变化后重建 | **未实跑**（需要真容器） | 池失效分支与 `docker_image` 同一条代码（`pooled.image != … or pooled.egress != …`）；纯函数与装配顺序（`docker_egress` 在 `initialize()` **之前**赋值）由上面两条替身用例间接证明 |
| 升级 agentscope 后覆写是否仍对位 | **未实跑** | 覆写对应的上游代码段（config 构造 / `create_or_replace` / `start` / backend）已逐字注释在 `docker_container_config` 的文档字符串里，升级时按 `test_docker_network_policy.py` 先红再改 |

## 待协调者决策

- **不在本票做前端**：`docker_egress` 目前只有后端读写面（`PATCH /api/channels/{id}/config`）。
  频道设置视图要不要出现这个开关（以及它与 `web_access` 两个开关怎么摆放），票面没写；本票
  只交付后端与库形态。若需要 UI，建议单独开票（同时要补 `channel_config_ui.test.cjs` 与 i18n 前端词条）。

## 遗留 / 已知限制

- Agent 级不能覆盖容器出网（理由见上，代价已记）。
- 白名单只来自 `fetch` 的 allow 规则：**搜索**工具的目标 host 是 provider 配置事实（`SEARCH_BACKENDS`），
  不再要求额外写一条 `fetch` 规则；开关关闭时两个工具都拒（ADR-0026 §五 的「联网工具与出网分开配」）。
  这条取舍属于细节自决：若以后要给搜索单独限网，应扩 `permission_rule` 的语义而不是在这里加第二套判定。
- 容器网络在「允许」档是 **Docker 默认 bridge**：容器能到的地方比白名单**宽**，白名单只在工具层
  生效。要真正按 host 收敛到容器内核层，需要在容器内跑代理或自定义网络 + iptables——票面明确
  「不做通用网络策略 DSL」，本票不做，风险在此如实记。
- **空 pattern 的 `fetch` allow 规则不构成容器白名单**（`web.egress_hosts` 丢弃无 host 的
  pattern）：`permission_rule` 里「空 pattern = 该工具的全部调用」这条语义**没有被改**（权限判定
  仍按它放行），容器出网是**另一个维度**的白名单，缺 host 就没有可放行的 host。方向是**更少**
  出网（fail-safe），不会把「人为漏配」变成「放开一切」；若以后要支持「放开全部 host」，应显式
  加一档而不是复用空 pattern。
- **`docker_egress` 用 `ADD COLUMN … NOT NULL DEFAULT 0`**：存量行在 `ALTER TABLE` 时就取到列默认
  `0`（= 容器无网），本票**没有**任何 `UPDATE` 回填业务数据——「不回填」在本仓的口径是「不按今天
  的配置倒推历史取值」，列默认值不是回填。
