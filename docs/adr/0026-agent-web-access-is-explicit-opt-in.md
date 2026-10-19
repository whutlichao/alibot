# Agent 联网是显式开启的出网能力：默认关闭、search 只读、fetch 逐次审批并复用数据面确认

用户于 2026-10-19 人定（#182）：给 Agent 的原生联网走**工具**这条路（路径 B：`web_search` /
`fetch`，不是「只配 MCP」也不是「靠 Bash + curl」），并同时钉死四条边界——**默认关闭、显式开启**；
`read-only` 档下 **search 算只读、fetch 不算**；直连第三方 API **复用 MCP 的数据面确认纪律**；
Docker 后端**容器默认无网、显式允许**。

本 ADR 只定边界与归属，不定工具参数、错误文案与 UI 呈现。

## 决策记录

- **Context**：`CONTEXT.md` 与 ADR-0021 已把「本地执行默认没有」写成结构事实（`LocalWorkspace.list_tools()`
  的 Bash 被装配层过滤，`allow_local_shell` 才挂且逐次审批）。补齐前的工具面里没有任何联网入口：AgentScope
  2.0.10 的 14 个内置工具无 net 类；`_build_model` 不带 provider 侧联网开关；`mcp_server` 默认空；Bash 默认不挂。
  出网能力缺位的三个后果是已知的：Agent 无法核实外部事实、无法读远端文档、无法交叉验证自己的结论。
- **Decision**：新增两个原生工具 `web_search` / `fetch`；默认**不装配**；`read-only` 档只装 `web_search`；
  `fetch` 在 `ask-writes` / `trusted-writes` 下**逐次审批**；开启前按 **provider** 取得一次性数据面确认
  （复用 `remote_consent_scope`）；Docker 后端容器默认 `--network none`，显式允许时才放开白名单。
- **Consequences**：出网是一个**配置事实**而不是默认能力，与 ADR-0021 §二同一口径；`remote_consent_scope.scope_kind`
  需要新档（`web`），因此要一次 schema bump + 重建表迁移（ADR-0015 只前进）；`read-only` 档不再等价于
  「内容不出机器」——只有 `fetch` 被排除，`web_search` 会送出查询词；网络请求不新增预算类别，费用只经
  工具结果进入上下文后的既有 token 口径体现（ADR-0007 / ADR-0024 不改）。

## 一、两个工具的边界差异就是它们的权限差异

`web_search` 与 `fetch` 不是「同一个 HTTP 客户端的两个入口」，它们送出去的东西不同：

- `web_search` 送出去的是**查询词**（加上语言、条数这类调用参数）。项目内容不进请求。
- `fetch` 的目标 URL 由模型给出，可能指向内网地址、带 token 的地址，或把项目内容拼进 query。

因此「读默认放行」这条既有规则在两者上得出不同结论：搜索不改本地状态、不泄漏项目内容，按只读处理；
抓取既可能越出本机、又可能把内部地址暴露给第三方，按**执行**处理。这不是产品偏好，是两者数据面的实际差别。

## 二、默认关闭，与 ADR-0021 §二同一条纪律

新增**两个配置位**，形状与既有的 workspace 覆盖同构（频道给默认值，Agent 可覆盖，`NULL` = 跟随频道）：

```text
channel.web_access          TEXT   NULL = 关闭 / 'search' / 'search+fetch'
agent_config.web_access     TEXT   NULL = 跟随频道
```

- **默认 `NULL`**：未配置时频道行为逐字不变（不挂这两个工具），与 ADR-0021 §六「未配置时行为逐字不变」一致。
- **不复用 `allow_local_shell`**：那个开关的风险面是「本机任意进程」，这个是「数据出网」，两者不能合并成
  一个开关——合并会让「我只想开搜索」变成「我也开了 curl」。
- **不由模型自行开启**：与 `reset_tools` 无关。工具组动态开关只影响**已装配**的工具，不改变配置事实。

## 三、权限档映射

| 权限档 | `web_search` | `fetch` |
| --- | --- | --- |
| `read-only` | 装配，放行 | **不装配** |
| `ask-writes`（默认） | 装配，放行 | 装配，**每次调用 ASK** |
| `trusted-writes` | 装配，放行 | 装配，仍 ASK（见下） |

`trusted-writes` 的既有语义是「自动放行**根内**写入」。`fetch` 没有「根内」这个概念，因此不能沿用那条
自动放行；它默认 ASK。

**`fetch` 不设 `bypass_immune`**（与 `AskEveryTimeBash` 相反）：`permission_rule` 的 `pattern` 可以表达
「允许抓取这个 host」，于是人类能一次配置好常用域名。这与 ADR-0021 §二对本地 shell 的处理刻意不同——
本地 shell 一旦免审批就等于放弃了整台机器，`fetch` 放行一个 host 的范围是可枚举的。

## 四、数据面确认复用 `remote_consent_scope`

出网前按**提供方**（搜索 API / 抓取目标的服务方）取得一次性确认，落 `remote_consent_scope`
（与 `('model', …)`、`('mcp', …)`、`('docker', …)` 同一张表、同一条纪律：**只存「何时确认过」，
不存凭证、不存连接参数**）。

- **新 `scope_kind = 'web'`**：现有列是 `CHECK (scope_kind IN ('model', 'mcp', 'docker'))`（`db.py:643`），
  加档需要**一次 schema bump**（17 → 18）+ 重建表迁移（SQLite 改表级 CHECK 只能重建）。号段判据仍是
  「必须高于当前 main」（ADR-0015 / `docs/agents/implementation.md`）。
- **确认单位是 provider，不是 host**：host 级粒度交给 `permission_rule`，否则每换一个域名都要一次确认，
  人会被迫一路点「同意」。
- **未确认时**：相关工具不装配，成员状态点可读，且**绝不发起任何调用**——与 `remote_consent` 的
  「未确认时绝不发起远端调用」同一句话。
- **凭据只从进程环境读**（`env_names` 型纪律）：库里没有密钥字段。

## 五、Docker 后端：容器默认无网

- 工作区后端是 `docker` 时，容器默认 `--network none`：**出网与联网工具分开配**。
- 显式允许出网时按 provider 的 host 白名单放开，而不是整段放开。
- 容器内既没有网络也没有工具时，此前的行为逐字不变；Docker 不可用仍是「成员未启用 + 可读原因」，
  **不静默回落**到本地出网。

## 六、预算口径

- 网络请求**不新增预算类别**，也不进 `target_budget.used`：那是**回应预算**（逐世代跳数与用量），
  语义上不是「网络调用次数」。
- 费用只经**工具结果进入上下文后的 token 用量**体现，已落在既有口径内（ADR-0007 / ADR-0024 不改）。
- provider 侧的 API 费用（按次计费的搜索接口）**本仓不记账、不声称成本结论**——与 ADR-0024
  「默认值是策略值而不是成本结论」同一纪律。

## 七、不做什么

- **不做通用 HTTP 客户端工具**：只有两个语义化工具，避免「换个名字的 Bash」。
- **不做远程市场 / 自动安装**：与 MCP / Skill 只接受显式配置的口径一致。
- **不把联网能力塞进 MCP-only 路径**（路径 A 被否）：理由是一旦只走 MCP，「默认关闭 + 权限档映射 +
  数据面确认」这三条就得由外部 server 自行保证，本仓无法验收。
- **不用 Bash + curl**（路径 C 被否）：它把「联网」降级成「本机任意进程」，每次调用一次人工批准，
  且绕过项目根边界主张。

## 八、后续

实施票至少覆盖：工具实现与装配点（`agent_seam._round_toolkit`）、两个配置位的读写面、
schema 18 + 迁移与 `_migration_history.py` 留档、`remote_consent_scope` 的 `web` 档读路径、
Docker 网络策略、`app/specs/issue-<n>-acceptance.md` 的判据与读数。

## 九、需要复核的一条

第六节（预算口径）是**从既有 ADR 推导的结论**，不是本轮 4 个选项之一。若认为网络调用应当独立计数，
本条需先行修改，再进入实施。
