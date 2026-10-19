<!-- 本文件是 #185 的验收文档（判据 → 落点 → 命令与读数）；tracker 上的 issue #185 保持原样。 -->

# Issue #185 验收 —— `web_search` / `fetch` 的工具本体

> 票面：GitHub [#185](https://github.com/whutlichao/alibot/issues/185)（`feat(web): …`，Part of #182）。
> 依据：**ADR-0026** §一 / §三 / §四 / §七（`docs/adr/0026-agent-web-access-is-explicit-opt-in.md`）。
> 票型：实施票。**不涉及 schema 号段与迁移**（`app/alibot/db.py` 的 `SCHEMA_VERSION` 一个字节都没动）。

| 项 | 值 |
| --- | --- |
| 分支 | `issue-185-impl`（从 `issue-182-integration` 的 `98920e8` 切出） |
| 改动文件 | `app/alibot/web.py`（新增）、`app/tests/test_web_tools.py`（新增）、本文件 |
| 新增用例 | `app/tests/test_web_tools.py`（24 条，全部注入进程内 stub，**零网络**） |
| 门禁 | `scripts/check.sh` 全绿（读数见 §5） |

---

## 0. 判据 → 落点

| 票面 / 裁定判据 | 落点 |
| --- | --- |
| `web.py` 只做工具本体：不接装配、不读 `web_access`、不判权限档 | 模块只 export 两个工厂（`search_tool` / `fetch_tool`）与它们要用的类型/常量；`grep -n "web_access" app/alibot/web.py` 无命中；不 import `db` / `orchestration` / `permissions` |
| `web_search` 只送查询词（+ 条数），返回标题 / URL / 摘要 | `search_tool()` 的工具入参只有 `query` / `count`；`_search_request()` 按 provider 组装请求（GET query 或 POST JSON 体），`_search_hits()` 映射成 `SearchHit(title,url,snippet)`，`_render_hits()` 渲染成文本块 |
| `web_search.is_read_only is True` / `fetch.is_read_only is False` | `FunctionTool(..., name="web_search", is_read_only=True)` / `FunctionTool(..., name="fetch", is_read_only=False)`；用例 `test_fetch_and_web_search_declare_their_read_only_difference` |
| `fetch` 按 URL 取正文并转纯文本 | `fetch_tool()` → `_page_text()`（`html.parser.HTMLParser` 子类：跳过 `script`/`style`/`noscript`/`template`/`svg`/`iframe`，块级标签断行，字符实体解码，逐行折叠空白），只用 http / https |
| 硬上限（条数 / 字节 / 字符），超限截断且给可读说明 | 条数：`SEARCH_MAX_RESULTS=10`（`web_search` 内夹取 + `（请求 N 条，按上限 10 条送出）`）；摘要：`SEARCH_MAX_SNIPPET_CHARS=400`（`…（摘要已截断，原文 N 字符）`）；响应体：`FETCH_DEFAULT_MAX_BYTES=1_000_000`（传输层最多读 `max_bytes+1`）；正文：`FETCH_DEFAULT_MAX_CHARS=8_000`；输出末尾 `（已截断：…）` |
| 错误分类：超时 / DNS / HTTP 状态 / 内容过大 → 可读工具结果 | `UrlopenTransport` 把 `URLError(TimeoutError)` / `TimeoutError` / `URLError(gaierror…)` / `OSError` 收口成 `WebTransportError(kind, detail)`；4xx-5xx 由 `HTTPError` 变成 `HttpResponse(status=…)`（状态码是**一档错误分类**，不是异常）；工具把它们渲染成 `ToolResultState.ERROR` 文本 |
| 超时：显式默认值 + 上层可覆盖 | `WEB_TIMEOUT_SECONDS = 15.0`；`search_tool(timeout_seconds=…)` / `fetch_tool(timeout_seconds=…)` 工厂参数，落到 `HttpRequest.timeout_seconds`；用例 `test_the_default_timeout_can_be_overridden_per_assembly` |
| 结果形状：`ToolChunk` 文本块 | `_chunk()` = `ToolChunk(content=[TextBlock(text=…)], state=…, is_last=True)`；两工具都返回它 |
| 凭据只从进程环境读、库里无密钥字段 | `WEB_ENV_ALLOWLIST` + `web_env(environ)`（形状同 `workspace.docker_env`）；错误文案只提**变量名**；用例断言凭据取值不出现在任何结果文本里 |
| 不做通用 HTTP 客户端（无「任意方法 / 任意头」入口） | 工具 schema 只有 `query` / `count` / `url`；`HttpRequest` 是模块内的传输形状，只有 `send(request)` 一个接缝 |
| 不新增 seam / 不新增前端测试框架 | 只新增一个 `app/tests/test_*.py`（既有 pytest 族），无 `.test.cjs`；`test_test_layering_guard.py` 通过（不读 Studio 源码、不断言词表形状、不 import 私有名） |
| **D1 预算口径**：网络请求不独立计数、不进 `target_budget.used` | 本票**不新增任何用量表 / 计数**；`grep -n "budget" app/alibot/web.py` 无命中 |
| **D2**：Brave 与 Tavily **都实现**，以 provider 枚举选客户端 | 见 §1 |
| **D4**：provider 只读进程环境，显式变量优先、否则按唯一 key 推断，异常情况报可读错误 | 见 §2 |

---

## 1. D2 落实：两个 provider、两个 host、两个环境变量

`app/alibot/web.py`：

```python
SearchProvider: TypeAlias = Literal["brave", "tavily"]  # provider 枚举
SEARCH_PROVIDERS: tuple[SearchProvider, ...] = ("brave", "tavily")

SEARCH_BACKENDS = {
    "brave": SearchBackend(
        host="api.search.brave.com",
        endpoint="https://api.search.brave.com/res/v1/web/search",
        env_name="BRAVE_API_KEY",
        method="GET",
        auth_header="X-Subscription-Token",
        auth_prefix="",
        results_path=("web", "results"),
        snippet_key="description",
    ),
    "tavily": SearchBackend(
        host="api.tavily.com",
        endpoint="https://api.tavily.com/search",
        env_name="TAVILY_API_KEY",
        method="POST",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        results_path=("results",),
        snippet_key="content",
    ),
}
```

- **两个 host**：`api.search.brave.com`（GET，查询词与条数在 query）与 `api.tavily.com`
  （POST，查询词与条数在 JSON 体）。两家 API 的形状差异**全部落在表里**，代码按 `backend.method`
  分支，不写两套客户端类。
- **两个环境变量名**：`BRAVE_API_KEY` / `TAVILY_API_KEY`。凭据**只从进程环境读**
  （`web_env()` 按 `WEB_ENV_ALLOWLIST` 挑名字，绝不透传 `os.environ`）；库里没有、也不新增任何
  密钥字段（本票不碰 `db.py`）。
- **stub 注入点**：`HttpTransport` 协议（`send(request: HttpRequest) -> HttpResponse`）。
  - 形状断言：测试的 `StubTransport` 记录每个 `HttpRequest`（method / url / headers / body /
    timeout_seconds / max_bytes），并回放构造时给的 `HttpResponse`；
  - 错误分类：真实传输 `UrlopenTransport(opener=…)` 的 `opener` 也可注入，回放 `urllib` 自己会抛
    的那几类错误（`URLError(gaierror)` / `TimeoutError` / `ConnectionResetError` / `HTTPError`）。
  - 两种注入都**不发一个包**：用例里没有任何真实域名解析或 socket 连接。
- **#187 的容器出网白名单**用 `SEARCH_BACKENDS[*].host` 这两个值（`api.search.brave.com` /
  `api.tavily.com`）；本票保证它们是可读的公开常量，不改 host 契约。

用例：
`test_web_search_sends_only_the_query_and_count_to_brave`（Brave 全形状 + 结果映射）、
`test_web_search_uses_the_tavily_host_and_key_when_configured`（Tavily 全形状 + 结果映射）。

---

## 2. D4 落实：provider 只从进程环境解析

```python
WEB_ENV_ALLOWLIST = ("ALIBOT_WEB_SEARCH_PROVIDER", "BRAVE_API_KEY", "TAVILY_API_KEY")
PROVIDER_ENV_NAME = "ALIBOT_WEB_SEARCH_PROVIDER"

def resolve_search_provider(environ=None) -> SearchProvider   # 失败抛 ProviderResolutionError(reason)
```

| 情形 | 行为 |
| --- | --- |
| `ALIBOT_WEB_SEARCH_PROVIDER` = `brave` / `tavily` | 用它（即使两把 key 都在） |
| 变量有值但**不是**这两家 | `ProviderResolutionError`：`ALIBOT_WEB_SEARCH_PROVIDER 只接受 brave / tavily（收到 'duckduckgo'），不静默回落到其它提供方` |
| 变量未设、只有 `BRAVE_API_KEY` | 推断 `brave` |
| 变量未设、只有 `TAVILY_API_KEY` | 推断 `tavily` |
| 变量未设、两把 key 都在 | `ProviderResolutionError`：`两把搜索凭据都在（BRAVE_API_KEY、TAVILY_API_KEY），需要用 ALIBOT_WEB_SEARCH_PROVIDER 显式指明用哪一家` |
| 变量未设、一把都没有 | `ProviderResolutionError`：`没有可用的搜索凭据：设置 BRAVE_API_KEY、TAVILY_API_KEY 之一（或用 ALIBOT_WEB_SEARCH_PROVIDER 显式指明提供方）` |

纪律落点：

- **不读 DB、不加 schema 列、不加工具参数**：模型拿不到 provider 参数（`web_search` 的 schema 只有
  `query` / `count`）——provider 是配置事实，不是一次工具调用的选择（ADR-0026 §二同一纪律）。
- **`ALIBOT_WEB_SEARCH_PROVIDER` 与两把 key 都在 `WEB_ENV_ALLOWLIST` 里**：allowlist 的语义是
  「哪些环境变量允许被读取」，不是「把这些值透传给子进程」。
- **工具构造接受 `provider` 参数**（#186 解析后传入）；传 `None` 时工具**自己按同一规则解析**
  （同一落点，不会两条路走岔）。任何解析失败都只产出可读的 `ToolResultState.ERROR` 文本，
  **不抛未捕获异常、不打印密钥值**。
- 四条分支各有用例：`test_the_provider_env_var_wins_over_the_keys`（显式 brave / 显式 tavily）、
  `test_the_provider_is_inferred_from_the_only_configured_key`（单 key 推断，两条）、
  `test_two_keys_without_an_explicit_provider_are_a_readable_error`（双 key 报错 + 不泄漏取值）、
  `test_an_unknown_provider_value_is_not_silently_ignored`（非法值不静默回落）、
  `test_without_any_key_the_error_names_the_variables_to_set`（都没有时说明配哪一把）。
  全部注入 stub，**不起网络**。

---

## 3. 红证据（**先跑失败用例，再写实现**）

开发循环用 `scripts/check.sh --fast tests/test_web_tools.py`；每一步都是「一个 seam 一条用例」
（垂直切片），下面是每一轮实测的红读数（命令都在 `app/` 下跑）。

### 3.1 切片一：Brave 请求形状 + 结果映射

```sh
cd app && ../.venv/bin/python -m pytest tests/test_web_tools.py
```

```text
tests/test_web_tools.py:21: in <module>
    from alibot.web import (
E   ModuleNotFoundError: No module named 'alibot.web'
=========================== short test summary info ============================
ERROR tests/test_web_tools.py
!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.12s
```

### 3.2 切片二：Tavily 路径（D2 的两家都要有客户端路径）

```text
provider = 'tavily'
>       backend = SEARCH_BACKENDS[provider]
E       KeyError: 'tavily'

alibot/web.py:190: KeyError
=========================== short test summary info ============================
FAILED tests/test_web_tools.py::test_web_search_uses_the_tavily_host_and_key_when_configured
1 failed, 3 passed in 0.27s
```

### 3.3 切片三：provider 未配置 / 缺 key（凭据判据）

```text
provider = None
>       backend = SEARCH_BACKENDS[provider]
E       KeyError: None
FAILED tests/test_web_tools.py::test_web_search_says_so_when_no_provider_is_configured
1 failed, 6 passed in 0.20s
```

（该用例在 D4 落地后被 §2 的四条分支用例取代，见 §4 的用例清单。）

### 3.4 切片四：条数与摘要上限

```text
tests/test_web_tools.py:21: in <module>
    from alibot.web import (
E   ImportError: cannot import name 'SEARCH_MAX_SNIPPET_CHARS' from 'alibot.web'
!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.18s
```

### 3.5 切片五：`fetch` 本体（5 条一起红）

```text
>       page = fetch_tool(transport=StubTransport())
E       NameError: name 'fetch_tool' is not defined
FAILED tests/test_web_tools.py::test_fetch_converts_a_page_to_plain_text - Na...
FAILED tests/test_web_tools.py::test_fetch_refuses_non_http_urls_without_sending_anything
FAILED tests/test_web_tools.py::test_fetch_reports_an_http_error_status_readably
FAILED tests/test_web_tools.py::test_fetch_truncates_a_huge_body_and_says_so
FAILED tests/test_web_tools.py::test_fetch_and_web_search_declare_their_read_only_difference
5 failed, 10 passed in 0.15s
```

### 3.6 切片六：读响应体时断链（`OSError` 子类逃出工具）

```text
>       raise self.error
E       ConnectionResetError: reset by peer
tests/test_web_tools.py:359: ConnectionResetError
FAILED tests/test_web_tools.py::test_default_transport_reads_a_broken_connection_as_unreachable
1 failed, 19 passed in 0.21s
```

### 3.7 切片七：D4 provider 解析

```text
tests/test_web_tools.py:26: in <module>
    from alibot.web import (
E   ImportError: cannot import name 'ProviderResolutionError' from 'alibot.web'
!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.19s
```

> 说明（诚实读数）：错误分类（超时 / DNS / HTTP 状态）与「缺 key 只给可读错误」的实现，随切片一
> 的构造函数一起落地（工厂必须有**可直接用的默认传输**，否则工具不可构造），因此这两组断言是
> **护栏用例**（写下来即绿），不是红→绿切片；它们的真实行为读数在 §4 与 §3.6（那一条是真红）。

---

## 4. 绿证据（全部 24 条）

```sh
cd app && ../.venv/bin/python -m pytest tests/test_web_tools.py
```

```text
........................                                                 [100%]
24 passed in 0.05s
```

| # | 用例 | 钉住的判据 |
| --- | --- | --- |
| 1 | `test_web_search_sends_only_the_query_and_count_to_brave` | Brave 请求形状（host / method / **只有 q 与 count 两个参数** / timeout / 凭据头）+ 结果映射 |
| 2 | `test_web_search_uses_the_tavily_host_and_key_when_configured` | Tavily 请求形状（POST / JSON 体只有 query 与 max_results / Authorization Bearer）+ 结果映射 |
| 3 | `test_web_env_only_reads_the_whitelisted_names` | 凭据白名单：`PATH` / `OPENAI_API_KEY` 不进读数 |
| 4 | `test_web_env_skips_empty_values` | 空串等于没配置 |
| 5 | `test_web_search_reports_a_missing_key_without_sending_anything` | 缺 key：可读错误 + **零请求** |
| 6 | `test_the_provider_env_var_wins_over_the_keys` | D4-1：显式变量优先（brave / tavily 各一次） |
| 7 | `test_the_provider_is_inferred_from_the_only_configured_key` | D4-2：单 key 推断 |
| 8 | `test_two_keys_without_an_explicit_provider_are_a_readable_error` | D4-3：双 key 报错 + 不泄漏取值 + 零请求 |
| 9 | `test_an_unknown_provider_value_is_not_silently_ignored` | D4-4：非法取值报错、不静默回落 |
| 10 | `test_without_any_key_the_error_names_the_variables_to_set` | D4-5：都没有时说明配哪一把 |
| 11 | `test_web_search_never_echoes_the_key_in_an_error` | HTTP 401 的错误文本不含凭据取值 |
| 12 | `test_web_search_reports_timeout_and_unreachable_readably` | 超时 / 连不上两档分类 |
| 13 | `test_web_search_clamps_the_requested_count_and_says_so` | 条数硬上限 + 可读说明 |
| 14 | `test_web_search_truncates_a_long_snippet_with_a_readable_note` | 摘要字符上限 + 可读说明 |
| 15 | `test_fetch_converts_a_page_to_plain_text` | HTML→纯文本（脚本 / 样式剔除、实体解码、最终 URL） |
| 16 | `test_fetch_refuses_non_http_urls_without_sending_anything` | 只接受 http(s)，`file:` 零请求 |
| 17 | `test_fetch_reports_an_http_error_status_readably` | HTTP 状态是一档可读分类 |
| 18 | `test_fetch_truncates_a_huge_body_and_says_so` | 字节 + 字符两道上限 + 「已截断」说明 + `max_bytes` 传到传输层 |
| 19 | `test_fetch_and_web_search_declare_their_read_only_difference` | `is_read_only` 一真一假、工具名 `web_search` / `fetch` |
| 20 | `test_default_transport_reports_a_dns_failure_readably` | 真实传输的域名解析失败分类 |
| 21 | `test_default_transport_reports_a_timeout_readably` | 真实传输的两条超时路径（裸 `TimeoutError` / `URLError` 包裹） |
| 22 | `test_default_transport_reads_a_broken_connection_as_unreachable` | 读体时断链（`OSError`）也收口 |
| 23 | `test_default_transport_turns_an_http_error_status_into_a_readable_result` | `HTTPError` → 状态码分类，不是异常 |
| 24 | `test_the_default_timeout_can_be_overridden_per_assembly` | 默认超时显式可读、装配层可覆盖（两工具） |

---

## 5. 门禁读数（`scripts/check.sh` 全量，仓库根）

```sh
unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1
scripts/check.sh
```

| 步骤 | 读数 |
| --- | --- |
| pytest（全量，从 `app/`） | `881 passed, 10 skipped in 368.56s` |
| ruff check | `All checks passed!` |
| ruff format **--check**（只检查，**不跑 `ruff format`**） | `224 files already formatted` |
| mypy（仓库根） | `Success: no issues found in 94 source files` |
| pyright | `0 errors, 0 warnings, 0 informations` |
| node --test（Studio DOM 用例族 glob） | 全绿（`ℹ pass 191 / fail 0`） |
| 结论 | `门禁全绿` |

### 5.1 「只跑 `ruff format --check`」这条纪律的本票自查

- 门禁里跑的只有 `ruff format --check`（`scripts/check.sh` 的第二步），本票**没有**把它改成会写文件
  的形态。
- 开发过程中曾对**本票新增的两个文件**（`app/alibot/web.py`、`app/tests/test_web_tools.py`）执行过一次
  `ruff format <这两个路径>`（新文件没有「小改动变大 diff」的风险），命令行**从未**把既有文件交给它。
  事后自查：

  ```sh
  git status --porcelain          # ?? app/alibot/web.py  ?? app/tests/test_web_tools.py
  git diff --stat                 # （空：没有任何既有文件被改动）
  ```

  也就是说本票的改动**只有**两个新增文件 + 本文件（以及合并集成分支 tip 带来的零/若干提交）。

---

## 6. 自决的细节（命名 / 结构 / 手法）与理由

| 细节 | 取值 | 理由 |
| --- | --- | --- |
| 工厂名 vs 工具名 | 工厂 `search_tool()` / `fetch_tool()`，工具名 `web_search` / `fetch` | #186 要按权限档**分别**装配两个工具（read-only 只装 search），一个 `web_tools()` 列表会强迫它再拆开 |
| provider 选择 | `SearchProvider`（`Literal`）+ `SEARCH_BACKENDS` 数据表 | 本仓既有的封闭词表写法就是 `Literal` + 元组常量（`TaskStatus` / `ROUND_STATUSES`），不新引入 `enum` |
| 默认 HTTP 传输 | stdlib `urllib.request` | 不新增依赖：`httpx` 只是 `agentscope` 的传递依赖，而本仓纪律是「直接 import 的依赖必须显式声明」；两家的请求都是「一个 URL + 几个头 + 可选 JSON 体」，`urllib` 够用 |
| HTML→文本 | `html.parser.HTMLParser` 子类 | 同上（不引第三方 readability），且对**被截断的响应体**容错 |
| 上限取值的落点 | 常量在文件头，`max_bytes` / `max_chars` / `timeout_seconds` 是工厂参数 | 票面要求「显式默认值且能被上层按调用覆盖」；常量给读数，工厂参数给覆盖点 |
| 工具结果文案的语言 | 简体中文硬编码（与 `agent_seam.declare_silence`、`workspace` 的工具结果同口径） | 工具结果是**给模型**看的，不是用户可见文案；`i18n.py` 是「后端生成的自然语言（HTTP 错误 / system 消息 / reason）」的落点，塞进去会把两层的边界搅混 |
| `app/README.md` 未改 | — | 本票不装配、无配置位，能力与配置的文档是 #186 的交付面；此刻写进 README 会宣称一个还没接上的能力 |

---

## 7. 待协调者决策 / 遗留风险

### 7.1 待协调者决策（本票**没有**自行拍板）

1. **`fetch` 的 host 级 `permission_rule` pattern**：ADR-0026 §三写「`permission_rule` 的 `pattern`
   可以表达『允许抓取这个 host』」。`FunctionTool` 没有重写 `match_rule()`，框架只支持**工具名级**
   规则（`rule_content=None`）匹配到它。要落地 host 级 pattern，需要在 `web.py` 引入一个
   `ToolBase` 子类（或在工厂里覆盖 `match_rule`）——那是改工具类型面的新决策，票面把权限档留给
   #186，本票未实现。
2. **语言参数**：ADR §一提「查询词（加上语言、条数这类调用参数）」。本票只送 `query` + `count`：
   Brave 有 `search_lang`，Tavily 没有等价字段——不造一个 Tavily 会**静默忽略**的参数。若要求按
   语言偏好发送，需要 #186 把 locale 传进工厂（新增一个工厂参数），或接受「只有 Brave 收语言」。
3. **抓取内容的字符编码**：现在一律 `utf-8 + errors="replace"`。非 UTF-8 页面（GBK/Shift_JIS）会出
   乱码。按 `Content-Type` / `<meta charset>` 解码需要给 `HttpResponse` 加 headers 字段（改传输
   形状）——是否值得是产品/实现权衡，本票不做。

### 7.2 遗留风险

- **未与真实 API 联调**：本机没有 Brave / Tavily 的 key，票面要求单测不起网络，因此两家的 JSON
  形状按公开文档实现（Brave `web.results[].{title,url,description}`、Tavily
  `results[].{title,url,content}`）。线上字段若漂移，`_search_hits()` 会**跳过**缺字段的结果
  （不崩，但结果可能为空）。
- **HTML→文本是最小实现**：不保留表格/列表结构，导航与页脚文字会混进正文（没有 readability 式抽取）。
- **不做过内网 IP 过滤**：票面把「抓哪里」的准入交给 `permission_rule` / #186，本票只拒绝非
  http(s) 协议。也就是说 `fetch` 目前**允许**指向内网地址——装配层的审批与规则是唯一的闸门。
- **同步传输跑在线程池里**（`asyncio.to_thread`）：同一轮的多工具并发受默认线程池上限约束；每轮的
  工具集是新建的，没有共享连接池（每次调用开一个连接）。
- **`web_search` 的 `count` 语义**按 provider 文档映射（Brave `count`、Tavily `max_results`），上限
  统一夹到 10；两家对「条数」的实现细节（去重、截断）不在本仓承诺范围内。
