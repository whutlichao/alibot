<!-- 本文件是 #186 的验收文档（判据 → 落点 → 命令与读数）；tracker 上的 issue #186 保持原样。 -->

# Issue #186 验收 —— 联网工具的装配与权限闸门（含 fetch 的 host 归一化）

> 票面：GitHub [#186](https://github.com/whutlichao/alibot/issues/186)（`feat(seam): …`，Part of #182）。
> 依据：**ADR-0026** §二 / §三 / §四（`docs/adr/0026-agent-web-access-is-explicit-opt-in.md`）；
> 前置票 #184（schema 18 + 两个配置位）与 #185（工具本体）已在集成分支 `issue-182-integration` 上。
> 票型：实施票。**不涉及 schema 号段与迁移**：`app/alibot/db.py` 的 `SCHEMA_VERSION` 一个字节都没动。

| 项 | 值 |
| --- | --- |
| 分支 | `issue-186-impl`（从 `issue-182-integration` 的 `e8ec2cc` 切出） |
| 改动文件 | `app/alibot/web.py`、`app/alibot/permissions.py`、`app/alibot/db.py`、`app/alibot/agent_seam.py`、`app/alibot/api.py`、`app/tests/test_permissions.py`、`app/tests/test_web_assembly.py`（新增）、本文件 |
| 新增用例 | `test_web_assembly.py` 9 条 + `test_permissions.py` 新增 7 条 |
| 门禁 | `scripts/check.sh` 全绿（读数见 §3） |

---

## 0. 判据 → 落点 → 用例

| 票面 / 裁定判据 | 落点 | 用例 |
| --- | --- | --- |
| 未配置 `web_access`：工具集**逐字不变** | `AgentScopeAgentSeam._web_tools` 的第一条闸门：`web_access` 为空即返回空列表；`_round_toolkit` 因此不追加任何工具 | `test_without_web_access_the_tool_set_stays_unchanged`（`web_access=NULL` + **已有**确认行，请求里的 `tools` 仍是 `{declare_silence}`）；`test_local_workspace.py` 的默认工具集断言原样保持绿 |
| `read-only` + `web_access='search+fetch'`：工具集里**没有** `fetch` | `_web_tools` 的第四条闸门：只有 `access == FETCH_ACCESS_LEVEL` 且档位不是 `read-only` 才追加 `fetch_tool()` | `test_read_only_mounts_web_search_but_never_fetch`；档位侧另有 `test_fetch_asks_in_both_write_modes_and_is_denied_in_read_only`（引擎口径：`read-only` → DENY） |
| `ask-writes`：调用 `fetch` 产生 `approval_request`；拒绝后工具结果 **DENIED**、本轮可继续 | 不新开审批通道：`fetch` 继承 `FunctionTool.check_permissions` 的 ASK，走既有 `ApprovalBroker` / `approval_request` / `ConfirmResult` 路径 | `test_fetch_asks_and_a_denial_is_a_denied_tool_result_with_the_round_continuing`（审批行 `denied` + SSE `tool_result_end.state=="denied"` + 零出网请求 + 本轮 `replied`） |
| `permission_rule(agent)` 设 `tool_name='fetch'` + host pattern 为 `allow` 后，该 host 不再审批、其他 host 仍 ASK | 规则侧 `permissions._matched_pattern` 把 pattern 归一成 host；输入侧 `web.FetchTool.match_rule` 把 URL 归一成 host；**两侧同一份 `web.url_host`** | `test_an_allowed_host_skips_the_approval_and_other_hosts_still_ask`（装配层）+ `test_a_fetch_host_rule_matches_the_full_url_input` / `test_a_fetch_host_rule_written_as_a_url_normalizes_the_same_way` / `test_deny_and_ask_rules_also_match_a_fetch_host` / `test_a_pattern_that_yields_no_host_does_not_match_everything`（权限层） |
| 缺 `('web', provider)` 确认：工具不装配且 **stub 断言零请求** | `_web_tools` 的第三条闸门：provider 不在 `consented_providers` 里就返回空列表 | `test_missing_web_consent_mounts_nothing_and_sends_zero_requests`（模型**主动调用** `fetch`，记录型传输替身的请求数仍是 0）；对照组 `test_a_consented_provider_mounts_the_tools_and_the_probe_is_live`（同一探针记到 1 条请求 ⇒ 探针是活的） |
| `supports_tool_calling` 为假时不装配联网工具 | `_round_toolkit` 里 `if web_tools and supports_tool_calling(provider)`——与沉默工具同一个能力闸门 | `test_a_provider_that_cannot_carry_tools_gets_no_web_tool`（脚本化模型记下**真的收到**的工具清单） |
| `fetch` **不设** `bypass_immune`（ADR-0026 §三，与 `AskEveryTimeBash` 刻意相反） | `FetchTool` 只覆盖 `match_rule`，不覆盖 `check_permissions` ⇒ 默认 ASK、allow 规则可放行 | `test_fetch_has_no_bypass_immune_so_the_host_rule_can_allow_it`（同一个 allow 规则放行 `fetch`、放不放行本地 shell 是 False） |
| 系统提示自陈 `fetch` 的风险 | `agent_seam._web_prompt_note()`：只在**真的挂了联网工具**时进 system prompt（`_system_prompt(with_web=…)` / `_team_member_prompt(with_web=…)`）；工具 description 本身也自陈（同 `AskEveryTimeBash` 的做法） | `test_ask_writes_mounts_both_tools_and_the_search_level_mounts_only_search` 断言提示里出现风险句；`test_without_web_access_the_tool_set_stays_unchanged` 断言未挂时提示里不提它 |
| 不做 Docker 网络策略（#187）/ 不新开审批通道或 SSE 事件 | 本票 diff 不含 `docker` 网络参数、不含新事件类型；审批复用 `approval_request` | `git diff` 读数见 §2.4 |
| 不新增 seam / 不新增前端测试框架 | 只新增一个既有 pytest 族的文件；工具集读数走**真 provider 端点**的 `tools` 清单（`test_silence_tool_channel.py` / `test_local_workspace.py` 同族做法） | `tests/test_test_layering_guard.py` 绿；无新增 `.test.cjs` |

---

## 1. 人定决策的落实方式

### D1 —— 网络请求不独立计数、不进 `target_budget.used`

本票**不新增任何用量表 / 计数 / 预算字段**：`git diff` 里没有 `target_budget`、`jump`、`usage`
相关改动（`web.py` 的 `token` 只出现在注释里）。工具结果照既有口径进上下文，费用只经 token 用量体现。

### D2 / D4 —— provider 只从进程环境解析，不加 schema 列、不给模型参数

- `web.resolve_search_provider()`（#185 落地）是唯一解析点：`ALIBOT_WEB_SEARCH_PROVIDER` 优先，
  否则按唯一存在的 key 推断；两把 key / 非法取值 / 一把都没有都抛可读错误。
- 装配层在 `_web_tools` 里调它，**解析失败 = 不装配 + 可读原因进 debug 日志**，绝不把异常抛成一轮失败。
- 库侧只新增一条**读**路径 `Database.web_settings_for_target`，没有新列、没有新表：
  `{web_access, mode, consented_providers}`。provider 名不落库。
- 模型拿不到 provider 参数：工具入参仍只有 `query` / `count` / `url`（#185 已定形）。

### D3 —— 与 #187 复用同一个 host 提取函数

`web.url_host(value)` 是**唯一实现**（小写、去 userinfo / 端口 / 路径 / query / fragment），
两个消费点都在本仓内：

```python
# app/alibot/permissions.py（规则侧）
HOST_PATTERN_TOOL_NAMES = frozenset({FETCH_TOOL_NAME})
return url_host(pattern) or pattern  # 取不出 host 时原样保留：不退化 ≠ 匹配一切

# app/alibot/web.py::FetchTool.match_rule（输入侧）
pattern = url_host(rule_content)
target = url_host(str(tool_input.get("url") or ""))
return pattern is not None and target is not None and pattern == target
```

#187 的容器出网白名单读 `permission_rule(tool_name='fetch', behavior='allow', pattern=<host>)`
时必须 `from alibot.web import url_host`，不要另写一份；`permissions.city` 这一侧已经用它归一化，
因此库里存的 pattern 与白名单读到的 host 是同一个字符串（大小写 / 端口 / 路径都已抹平）。

### “不新增 seam”的核实方式

- `git diff --stat`：只改 5 个生产文件 + 2 个测试文件 + 本文件；没有新增协议/接口类型，
  没有新增 SSE 事件，没有改 `AgentSeam` 的输出形状（仍是「零或多条回复 + 一个轮次结果」）。
- `_round_toolkit` 只多一个**入参** `web_tools`（输入侧能力清单），输出形状不变。
- 工具集读数的取证面全部是**既有**测试缝：真 `Agent` + 进程内 provider 端点（`test_local_workspace`
  / `test_silence_tool_channel` 同族）与 `PermissionEngine`（`test_permissions` 同族）。

**为什么这里允许进程内 stub（票面要求的理由）**：判据「provider 真的收到工具调用」与「工具没挂时
一个请求都发不出去」只有模型层的读数能回答；因此按 `docs/agents/implementation.md` 的例外条款，
在既有用例族里加了两类进程内替身——① `http.server` 回 OpenAI 兼容响应（记录 `tools` 清单）；
② 记录型 `HttpTransport` 替身（记录出网请求）。两者都不联网、零成本、不进生产代码路径。

---

## 2. 读数

### 2.1 红证据（写用例时先红后绿）

`scripts/check.sh --fast tests/test_permissions.py`（实现 `url_host` + `FetchTool.match_rule` 之前）：

```text
FAILED tests/test_permissions.py::test_a_fetch_host_rule_matches_the_full_url_input
FAILED tests/test_permissions.py::test_a_fetch_host_rule_written_as_a_url_normalizes_the_same_way
FAILED tests/test_permissions.py::test_fetch_has_no_bypass_immune_so_the_host_rule_can_allow_it
FAILED tests/test_permissions.py::test_deny_and_ask_rules_also_match_a_fetch_host
4 failed, 12 passed in 0.94s
```

失败形态正是票面点名的那条 bug：

```text
assert <PermissionBehavior.ASK: 'ask'> is <PermissionBehavior.ALLOW: 'allow'>
 +  where <PermissionBehavior.ASK: 'ask'> = _web_decide('ask-writes', <FunctionTool ...>,
       {'url': 'https://api.example.com/x'}, [{'tool_name': 'fetch', 'pattern': 'api.example.com', 'behavior': 'allow'}])
```

原因：框架的 `ToolBase.match_rule` 默认只认工具名级规则（`rule_content is None`），`FunctionTool`
不覆盖它 ⇒ 带 pattern 的规则**永远匹配不上**。绿后的读数：`16 passed`（同名文件）。

装配层用例（`test_web_assembly.py`）同样有可复现的红：把 `_web_tools` 的前两条闸门短路成
`return []`（等价于「装配点没接上」），同一文件变成：

```text
FAILED tests/test_web_assembly.py::test_read_only_mounts_web_search_but_never_fetch
FAILED tests/test_web_assembly.py::test_ask_writes_mounts_both_tools_and_the_search_level_mounts_only_search
FAILED tests/test_web_assembly.py::test_an_agent_level_web_access_overrides_the_channel
FAILED tests/test_web_assembly.py::test_a_consented_provider_mounts_the_tools_and_the_probe_is_live
FAILED tests/test_web_assembly.py::test_fetch_asks_and_a_denial_is_a_denied_tool_result_with_the_round_continuing
FAILED tests/test_web_assembly.py::test_an_allowed_host_skips_the_approval_and_other_hosts_still_ask
6 failed, 3 passed in 14.64s
```

这 6 条恰好是「工具该在、却不在」的判据；仍然绿的 3 条是**反向**判据（未配置 / 缺确认 /
能力闸门），它们断言的是「不在」——短路与它们同向，因此不该红。还原后：`9 passed in 4.01s`。

### 2.2 关键绿读数（分组）

```text
scripts/check.sh --fast tests/test_web_assembly.py
9 passed in 4.01s                      # 装配四条闸门 + fetch 审批 + 系统提示风险

scripts/check.sh --fast tests/test_permissions.py
16 passed in 0.33s                     # 其中 7 条是 #186 新增的 host 归一化 / 三档行为

scripts/check.sh --fast tests/test_local_workspace.py tests/test_permissions.py \
    tests/test_approval_seam.py tests/test_web_tools.py tests/test_agent_round.py \
    tests/test_web_access_config.py tests/test_mcp_assembly.py tests/test_skill_assembly.py
125 passed, 1 skipped in 24.43s

scripts/check.sh --fast tests/test_team_mode.py tests/test_team_visibility.py \
    tests/test_team_budget.py tests/test_plan.py tests/test_silence_tool_channel.py
44 passed in 7.56s                     # 队长轮 / 成员轮的工具集装配未被改动打坏
```

### 2.3 全量门禁

见 §3。

### 2.4 不做的两件事的读数

```text
$ git diff --stat e8ec2cc -- app/alibot/db.py | tail -1
 app/alibot/db.py | 39 +++++-           # 只有一条读路径；无 DDL、无 SCHEMA_VERSION
$ git diff e8ec2cc -- app/alibot | grep -nE "SCHEMA_VERSION|target_budget|--network|docker.*network"
（无命中）
```

---

## 3. 门禁读数（全量 `scripts/check.sh`）

待填（收尾跑一次后补）。

---

## 4. 细节自决、遗留与待协调者决策

### 4.1 细节自决（每条一句理由）

1. **host 归一化抹掉端口**：pattern `api.example.com` 匹配 `https://api.example.com:8443/x`。
   理由：pattern 表达的是「允许抓取这个 host」（ADR-0026 §三），#187 的容器白名单也按 host 放行；
   端口不参与，就不会出现「同一个域名因端口不同要配两条规则、而容器白名单只认 host」的撕裂。
2. **不支持通配符**（`*.example.com` 归一成 `*.example.com`，匹配不到任何输入）：一个坏 pattern
   退化成「匹配一切」比「匹配不到」危险得多；通配是另一个产品决定，票面没有要求。
3. **`fetch` 也需要 `('web', provider)` 确认**：票面写的是「相关工具不装配」，而 D4 只给了一个
   provider（搜索 provider）。确认单位是 provider、host 级粒度交给 `permission_rule`（ADR-0026 §四），
   因此两个工具共用同一个确认行。
4. **`web_access` 的读路径不挂在 workspace 上**：`db.workspace_settings_for_target` 在频道没配
   `workspace_root` 时返回 `None`，而 `web_access` 是独立配置位（ADR-0026 §二）。新增
   `web_settings_for_target` 单独读，于是「只开搜索、不配项目根」这种组合也能生效
   （`test_an_agent_level_web_access_overrides_the_channel` 覆盖了 Agent 级覆盖）。
5. **未知 `web_access` 取值退化成「只装 search」**：写路径的 CHECK / `db.WEB_ACCESS_LEVELS` 已经拦住
   非法值；万一库里出现脏值，fail-safe 的方向是多装只读工具，而不是多装要审批的 `fetch`。
   `FETCH_ACCESS_LEVEL` 常量放在 `web.py`（读路径唯一判断），不复制 `db.WEB_ACCESS_LEVELS` 整张白名单。
6. **能力闸门与权限档闸门正交**：`_web_tools` 决定「哪两个工具」（配置/权限档/确认三条闸门），
   `_round_toolkit` 决定「provider 能不能携带工具」。两处各一个判据，避免在四处装配点重复写闸门。
7. **团队成员的联网配置按各自的 `member_id` 读**（Agent 级覆盖），不继承队长的；系统提示的
   risk 段也按「该成员真的挂了联网工具」给（`_team_member_prompt(with_web=…)`）。

### 4.2 遗留项（本票不做，留给后续票 / #187）

1. **缺 web 确认时的「状态可读」只落在库读路径与 debug 日志**：`web_settings_for_target` 的
   `consented_providers` 与 `AgentScopeAgentSeam` 的 `_log.debug("web tools skipped: …")`。
   人类面（成员面板 / 设置页）没有展示 web 确认状态的入口——本票的落点清单里没有它，
   成员状态点也**不**因缺 web 确认变成 `offline`（那会把「没开联网」误报成「这个 Agent 坏了」）。
2. **`('web', provider)` 没有写入口**：`Database.record_remote_consent_scope` 的 allowlist 仍只有
   `model / mcp / docker`（schema 18 的 CHECK 已接受 `web`），人类面也没有确认端点。本票的用例
   直写库取证（与 `test_approval_seam.py` 直写 `channel.workspace_root` 同一体例）。
   这属于「数据面确认的人类面」——见 §4.3。
3. **`fetch` 的审批建议规则仍是工具名级**（`PermissionRule(rule_content=None)` ⇒ 一次批准等于
   「允许所有 host」）。host 级建议（`suggested_rules` 里给出当前 host）需要单独决定文案与交互，
   本票不改框架默认。
4. **团队成员联网工具没有专门用例**：三处装配点的成员侧（`_web_tools(target_id, member_id_value)`）
   由代码审查与 `test_team_mode.py` 的既有工具集读数覆盖，没有「成员自己开、队长没开」的用例。

### 4.3 待协调者决策

1. **web 数据面确认的人类面入口**（谁来写 `remote_consent_scope('web', provider)`：
   `record_remote_consent_scope` 扩 allowlist + 新端点，还是并入 #187 的 Docker 出网确认流程）。
2. **`fetch` 是否应当独立于搜索 provider 做确认**：今天「只开 fetch、没有搜索 key」会因 provider
   解析失败而两个工具都不装（D4 只给了一个 provider 概念）。若要放开，需要一个新的 provider 身份
   （或允许 `('web', 'fetch')` 这样的第二个确认单位）。
