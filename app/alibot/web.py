"""``web_search`` / ``fetch`` 的工具本体（#185 / ADR-0026 §一、§三、§四、§七）。

两个工具**不是**「同一个 HTTP 客户端的两个入口」：``web_search`` 只送出查询词与条数（项目内容
不进请求），``fetch`` 的目标 URL 由模型给出、可能指向内网或带 token 的地址。这条数据面差别就是
它们的权限差别（ADR-0026 §一），因此 ``web_search`` 是只读工具、``fetch`` 不是。
本模块只做**工具本体**：不读 ``web_access``、不判权限档、不做装配（那是 #186）。

四条边界落在代码里：

- **凭据有两个来源，库内优先**（#199 / ADR-0026 §四修订）：``web_provider_credential`` 存明文 key，
  进程环境（:data:`WEB_ENV_ALLOWLIST`）只是兜底；白名单之外的环境变量一个都不进来，错误文案只提
  **provider 与变量名**、绝不回吐取值。人类面读数一律走 ``mask_credential``。
- **provider 由唯一顺序解析**（:func:`describe_search_provider`）：显式环境变量优先，其次凭据表
  默认项，再按已配置项的固定顺序，最后是免凭据的 ``exa-mcp``。模型拿不到 provider 参数——那是一个
  配置事实，不是一次工具调用的选择。
- **不做通用 HTTP 客户端**（ADR-0026 §七）：工具入参只有 ``query`` / ``count`` 与 ``url``，没有
  「任意方法 / 任意头」的入口；:class:`HttpRequest` 是模块内部的传输形状，不是工具面。
- **结果形状是 ``ToolChunk`` 文本块**，且有硬上限（条数 / 字节 / 字符）：超限**截断并说明**，
  不静默丢内容。网络失败一律收口成可读的工具结果（超时 / 域名解析 / HTTP 状态 / 内容过大），
  不向轮次抛未捕获异常。
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Literal, Protocol, TypeAlias

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import FunctionTool, ToolChunk

from .i18n import SOURCE_LOCALE, Locale, render

#: 模块对外的名字（装配层 #186 与测试只用这些；其余一律是内部实现）。
__all__ = [
    "ANY_SEARCH_PROVIDERS",
    "FALLBACK_PROVIDER",
    "FETCH_ACCESS_LEVEL",
    "FETCH_DEFAULT_MAX_BYTES",
    "FETCH_DEFAULT_MAX_CHARS",
    "FETCH_TOOL_NAME",
    "FETCH_USER_AGENT",
    "PROVIDER_ENV_NAME",
    "SEARCH_BACKENDS",
    "SEARCH_DEFAULT_RESULTS",
    "SEARCH_MAX_BYTES",
    "SEARCH_MAX_RESULTS",
    "SEARCH_MAX_SNIPPET_CHARS",
    "SEARCH_PROVIDERS",
    "WEB_ENV_ALLOWLIST",
    "WEB_TIMEOUT_SECONDS",
    "EgressPolicy",
    "FetchTool",
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "ProviderChoice",
    "ProviderResolutionError",
    "SearchBackend",
    "SearchHit",
    "SearchProvider",
    "UrlopenTransport",
    "WebTransportError",
    "describe_search_provider",
    "egress_hosts",
    "fetch_tool",
    "mask_credential",
    "resolve_search_provider",
    "search_tool",
    "url_host",
    "web_env",
]

#: 出网请求的默认超时（秒）。显式取值而不是靠 ``urllib`` 的无限等待；构造工具时可覆盖。
WEB_TIMEOUT_SECONDS = 15.0

#: 联网工具的 UA：本仓没有浏览器，声明自己是脚本比冒充浏览器更诚实。
#: Exa 的公开 MCP 端点会拒绝 Python-urllib 的默认 UA（403），因此搜索请求也带上它。
WEB_USER_AGENT = "alibot-web-tools/0.1"

#: 允许被读取的环境变量清单。它是「哪些变量允许读」的白名单，不是「把这些值透传给子进程」
#: 的清单——形状同 ``workspace.DOCKER_ENV_ALLOWLIST``。库内凭据优先，环境变量只是兜底来源。
WEB_ENV_ALLOWLIST = (
    "ALIBOT_WEB_SEARCH_PROVIDER",
    "BRAVE_API_KEY",
    "TAVILY_API_KEY",
    "PERPLEXITY_API_KEY",
    "JINA_API_KEY",
    "EXA_API_KEY",
)

#: 显式指明搜索 provider 的变量名（取值为 :data:`ANY_SEARCH_PROVIDERS`，其它值一律报错）。
PROVIDER_ENV_NAME = "ALIBOT_WEB_SEARCH_PROVIDER"

#: 抓取工具在工具面与 ``permission_rule`` 里的名字（权限层按这个名字做 host 归一化）。
FETCH_TOOL_NAME = "fetch"

#: ``web_access`` 里唯一会打开 ``fetch`` 的档位（ADR-0026 §二：``'search'`` / ``'search+fetch'``）。
#: 它是**读路径**的唯一判断：写路径的白名单仍以 ``db.WEB_ACCESS_LEVELS`` 为准。
FETCH_ACCESS_LEVEL = "search+fetch"

#: 搜索默认 / 最多返回的条数（条数硬上限的一档）。
SEARCH_DEFAULT_RESULTS = 5
SEARCH_MAX_RESULTS = 10

#: 单条摘要保留的字符数：一条结果不该独占上下文。
SEARCH_MAX_SNIPPET_CHARS = 400

#: 搜索响应体的读取上限：JSON 正常远小于此，超出的部分不读进内存。
SEARCH_MAX_BYTES = 512_000

#: 抓取的默认上限：读多少字节、转成纯文本后保留多少字符。
FETCH_DEFAULT_MAX_BYTES = 1_000_000
FETCH_DEFAULT_MAX_CHARS = 8_000

#: 带凭据的搜索提供方（ADR-0026 §四：确认与凭据的单位是 provider）。它们各自读一把 key；
#: 凭据可以存在库里（界面配置）或进程环境里（兜底）。
SearchProvider: TypeAlias = Literal["brave", "tavily", "perplexity", "jina", "exa"]

#: 免凭据回退的 provider：Exa 的公开 MCP 端点，零配置、不需要确认行。
FallbackProvider: TypeAlias = Literal["exa-mcp"]

#: 显式变量 / 解析结果可能出现的全部取值（五家带凭据 + 一家免凭据回退）。
AnySearchProvider: TypeAlias = Literal["brave", "tavily", "perplexity", "jina", "exa", "exa-mcp"]

#: 允许的带凭据 provider：固定顺序同时是「没有显式默认时」的解析顺序。
SEARCH_PROVIDERS: tuple[SearchProvider, ...] = ("brave", "tavily", "perplexity", "jina", "exa")

#: 免凭据回退（没有可用凭据时的确定结果，不是「静默猜一家」）。
FALLBACK_PROVIDER: FallbackProvider = "exa-mcp"

#: 全部取值（显式变量的白名单）。
ANY_SEARCH_PROVIDERS: tuple[AnySearchProvider, ...] = (*SEARCH_PROVIDERS, FALLBACK_PROVIDER)

#: 凭据掩码的可见前缀 / 尾字符数：HTTP 响应、日志与事件载荷只出现 ``sk-…abcd`` 这一形态。
CREDENTIAL_MASK_PREFIX = 3
CREDENTIAL_MASK_TAIL = 4


class ProviderResolutionError(Exception):
    """搜索 provider 无法确定；``reason`` 是给人与模型看的可读原因（不含凭据取值）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ProviderChoice:
    """一次解析的完整读数：用哪一家，以及这个结论来自哪一层配置。"""

    provider: AnySearchProvider
    source: Literal["env", "ui", "fallback"]


@dataclass(frozen=True)
class SearchBackend:
    """一个搜索提供方的接入点：凭据从哪读、请求长什么样、结果怎么映射。

    各家的差别全部落在这张表里，于是「选 provider」是一张表而不是几段分支代码：

    - Brave：``GET``，查询词放 query（``q`` / ``count``），凭据放 ``X-Subscription-Token``；
    - Tavily：``POST``，JSON 体（``query`` / ``max_results``），``Authorization: Bearer``；
    - Perplexity：``POST``，JSON 体（``query`` / ``max_results``），``Authorization: Bearer``；
    - Jina：``GET``，查询词放 query（``q``），``Authorization: Bearer``；
    - Exa：``POST``，JSON 体（``query`` / ``numResults``），``x-api-key``；
    - ``exa-mcp``：免凭据的 JSON-RPC（回包可能是 SSE），没有凭据头、没有结果路径。
    """

    host: str
    endpoint: str
    method: str
    auth_header: str
    auth_prefix: str
    results_path: tuple[str, ...]
    snippet_keys: tuple[str, ...]
    env_name: str | None = None
    query_name: str = "query"
    count_name: str | None = "max_results"
    protocol: Literal["json", "exa-mcp"] = "json"
    keyless: bool = False


SEARCH_BACKENDS: dict[AnySearchProvider, SearchBackend] = {
    "brave": SearchBackend(
        host="api.search.brave.com",
        endpoint="https://api.search.brave.com/res/v1/web/search",
        method="GET",
        auth_header="X-Subscription-Token",
        auth_prefix="",
        results_path=("web", "results"),
        snippet_keys=("description",),
        env_name="BRAVE_API_KEY",
        query_name="q",
        count_name="count",
    ),
    "tavily": SearchBackend(
        host="api.tavily.com",
        endpoint="https://api.tavily.com/search",
        method="POST",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        results_path=("results",),
        snippet_keys=("content",),
        env_name="TAVILY_API_KEY",
    ),
    "perplexity": SearchBackend(
        host="api.perplexity.ai",
        endpoint="https://api.perplexity.ai/search",
        method="POST",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        results_path=("results",),
        snippet_keys=("snippet", "content"),
        env_name="PERPLEXITY_API_KEY",
    ),
    "jina": SearchBackend(
        host="s.jina.ai",
        endpoint="https://s.jina.ai/",
        method="GET",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        results_path=("data",),
        snippet_keys=("description", "content"),
        env_name="JINA_API_KEY",
        query_name="q",
        count_name=None,
    ),
    "exa": SearchBackend(
        host="api.exa.ai",
        endpoint="https://api.exa.ai/search",
        method="POST",
        auth_header="x-api-key",
        auth_prefix="",
        results_path=("results",),
        snippet_keys=("text", "summary"),
        env_name="EXA_API_KEY",
        query_name="query",
        count_name="numResults",
    ),
    "exa-mcp": SearchBackend(
        host="mcp.exa.ai",
        endpoint="https://mcp.exa.ai/mcp?tools=web_search_exa",
        method="POST",
        auth_header="",
        auth_prefix="",
        results_path=(),
        snippet_keys=(),
        protocol="exa-mcp",
        keyless=True,
    ),
}


@dataclass(frozen=True)
class SearchHit:
    """一条搜索命中：模型要的三件事（标题、URL、摘要），不含 provider 的原始字段。"""

    title: str
    url: str
    snippet: str


def web_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """只挑白名单里的进程环境值；绝不透传整个 ``os.environ``（ADR-0026 §四）。"""
    source = os.environ if environ is None else environ
    return {name: source[name] for name in WEB_ENV_ALLOWLIST if source.get(name)}


def mask_credential(value: str) -> str:
    """凭据的**唯一**可回读形态：``sk-…abcd``；太短的值只回 ``…``。

    掩码要留下可见前缀与尾字符时，值必须至少比「前缀 + 尾字符」多一位——否则前缀与尾字符
    会拼出完整取值。任何 HTTP 响应、日志与事件载荷都只许出现这个函数的返回值。
    """
    text = value or ""
    # 至少藏住 4 个字符，避免「前缀 + 尾字符」拼出完整短 key。
    if len(text) < CREDENTIAL_MASK_PREFIX + CREDENTIAL_MASK_TAIL + 4:
        return "…"
    return f"{text[:CREDENTIAL_MASK_PREFIX]}…{text[-CREDENTIAL_MASK_TAIL:]}"


def describe_search_provider(
    environ: Mapping[str, str] | None = None,
    *,
    credentials: Mapping[str, str] | None = None,
    default_provider: str | None = None,
) -> ProviderChoice:
    """按**唯一**的解析顺序确定 provider，并说明结论来自哪一层配置。

    1. 进程环境 ``ALIBOT_WEB_SEARCH_PROVIDER`` 显式指定（取值非法仍报错）；
    2. 凭据表里 ``is_default = 1`` 的 provider（``default_provider``）；
    3. 已配置 provider 的固定顺序 ``brave → tavily → perplexity → jina → exa``
       （「已配置」= 库里有 key，或该 provider 的环境变量有值）；
    4. 都没有 ⇒ 免凭据的 :data:`FALLBACK_PROVIDER`（``exa-mcp``）。

    ``source`` 是给人类面显示「当前 provider 与来源」的事实：``env`` / ``ui`` / ``fallback``。
    模型拿不到 provider 参数——它是配置事实，不是一次工具调用的选择。
    """
    env = web_env(environ)
    keys = credentials or {}
    explicit = (env.get(PROVIDER_ENV_NAME) or "").strip()
    if explicit:
        for provider in ANY_SEARCH_PROVIDERS:
            if explicit == provider:
                # ``exa`` 有两条接入路径：有 key 走 REST，没有 key 走 MCP。显式点名 exa 却没有
                # 任何来源的 key 时，落到免凭据的 exa-mcp，而不是报「未配置凭据」。
                if provider == "exa" and not (keys.get("exa") or env.get("EXA_API_KEY")):
                    return ProviderChoice(provider=FALLBACK_PROVIDER, source="fallback")
                return ProviderChoice(provider=provider, source="env")
        allowed = " / ".join(ANY_SEARCH_PROVIDERS)
        raise ProviderResolutionError(
            f"{PROVIDER_ENV_NAME} 只接受 {allowed}（收到 {explicit!r}），不静默回落到其它提供方"
        )

    configured: list[SearchProvider] = [
        provider
        for provider in SEARCH_PROVIDERS
        if keys.get(provider) or env.get(SEARCH_BACKENDS[provider].env_name or "")
    ]
    default = (default_provider or "").strip()
    for provider in configured:
        if provider == default:
            return ProviderChoice(provider=provider, source="ui")
    if configured:
        provider = configured[0]
        source: Literal["env", "ui"] = "ui" if keys.get(provider) else "env"
        return ProviderChoice(provider=provider, source=source)
    return ProviderChoice(provider=FALLBACK_PROVIDER, source="fallback")


def resolve_search_provider(
    environ: Mapping[str, str] | None = None,
    *,
    credentials: Mapping[str, str] | None = None,
    default_provider: str | None = None,
) -> AnySearchProvider:
    """解析结果的**唯一**取值入口（判据见 :func:`describe_search_provider`）。"""
    return describe_search_provider(
        environ, credentials=credentials, default_provider=default_provider
    ).provider


@dataclass(frozen=True)
class HttpRequest:
    """一次出网请求的**全部**形状（内部传输形状，不是工具入参）。"""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout_seconds: float = WEB_TIMEOUT_SECONDS
    max_bytes: int = SEARCH_MAX_BYTES
    """响应体的读取上限。传输层最多读 ``max_bytes + 1`` 字节——多出来的那 1 字节是「超限了」
    这个事实的最小证据，客户端据此给出截断说明（不静默丢内容）。"""


@dataclass(frozen=True)
class HttpResponse:
    """一次出网响应：状态码、至多 ``max_bytes`` 字节的响应体、跟随重定向后的最终 URL。"""

    status: int
    body: bytes = b""
    url: str = ""


class HttpTransport(Protocol):
    """出网传输（唯一的网络接缝）：单测注入进程内 stub，于是不起网络也能断言请求形状。"""

    def send(self, request: HttpRequest) -> HttpResponse: ...


class WebTransportError(Exception):
    """传输层失败，``kind`` 是错误分类的一档，``detail`` 是给模型看的原因。"""

    def __init__(self, kind: Literal["timeout", "unreachable"], detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


class UrlopenTransport:
    """默认传输：stdlib ``urllib``（不引入新依赖）。

    ``opener`` 默认是 ``urllib.request.urlopen``；测试注入替身即可在不联网的情况下回放
    ``urllib`` 自己会抛的那几类错误，于是「错误分类」这条判据也有读数。
    """

    def __init__(self, opener: Any = None) -> None:
        self._opener = urllib.request.urlopen if opener is None else opener

    def send(self, request: HttpRequest) -> HttpResponse:
        http_request = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener(http_request, timeout=request.timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or 0)
                body = response.read(request.max_bytes + 1)
                return HttpResponse(status=status, body=body, url=str(response.geturl()))
        except urllib.error.HTTPError as error:
            with error:
                body = error.read(request.max_bytes + 1)
            return HttpResponse(status=int(error.code), body=body, url=request.url)
        except urllib.error.URLError as error:
            raise _transport_error(error.reason, request.timeout_seconds) from error
        except TimeoutError as error:
            raise WebTransportError("timeout", f"请求超时（{request.timeout_seconds:.0f} 秒）") from error
        except OSError as error:
            # 读响应体时断链 / 连接重置：``urllib`` 不会再包一层 ``URLError``，这里统一收口。
            raise WebTransportError("unreachable", f"无法连接目标地址：{error}") from error


def _transport_error(reason: Any, timeout_seconds: float) -> WebTransportError:
    """把 ``urllib`` 的失败原因分成「超时」与「连不上（含域名解析失败）」两类。"""
    if isinstance(reason, TimeoutError):
        return WebTransportError("timeout", f"请求超时（{timeout_seconds:.0f} 秒）")
    return WebTransportError("unreachable", f"无法连接目标地址：{reason}")


def url_host(value: str) -> str | None:
    """从 URL 或裸 host 里取出**归一化**的 host；取不出时返回 ``None``。

    归一化 = 小写、去掉 userinfo / 端口 / 路径 / query / fragment（``urlsplit().hostname`` 已经
    把这几项做掉）。这条归一化有两处消费点，**只有这一份实现**：

    - ``permissions._matched_pattern`` 把 ``permission_rule`` 的 pattern 收成 host（规则侧）；
    - :class:`FetchTool` 把工具输入里的 URL 收成 host（输入侧）。

    #187 的容器出网白名单读同一批 ``permission_rule`` 的 pattern，必须复用本函数而不是另写一份
    （D3：host 提取单一实现）。

    刻意**不**支持通配符：``*.example.com`` 不是一个 host，归一后仍是它自己，于是既不等于任何
    输入 host、也不会变成「匹配一切」——一个坏 pattern 应当什么都不匹配，而不是允许全部调用。
    """
    target = (value or "").strip()
    if not target:
        return None
    # 裸 host（或无 scheme 的 ``host:port/path``）补上 ``//`` 才能让 urlsplit 走 netloc 解析。
    if "://" not in target:
        target = "//" + target.lstrip("/")
    try:
        host = urllib.parse.urlsplit(target).hostname
    except ValueError:  # 方括号不配对之类的畸形输入：没有 host，而不是抛给一轮
        return None
    if not host:
        return None
    return host.rstrip(".") or None


@dataclass(frozen=True)
class EgressPolicy:
    """容器出网策略（#187 / ADR-0026 §五）：默认无网，显式允许时只放行白名单 host。

    ``backend == 'docker'`` 时由装配层构造；本地后端传 ``None``（行为逐字不变）。
    ``allowed_hosts`` 的来源是 ``permission_rule`` 里 ``fetch`` 的 allow 规则的 host（D3，
    ``Database.web_settings_for_target`` 用 :func:`egress_hosts` 算出来）。

    为什么闸门在**工具层**而不只靠容器：两个工具在**宿主机进程内**执行（``urllib``），
    容器真的无网并不会让它们失败；反过来，容器无网也不能替工具做「白名单外拒绝」这种按 host
    的判定（Docker 的 ``NetworkMode`` 只有 none / 网络名两态）。因此两侧各说各的一半：容器按
    开关接不接网络，工具按同一个开关 + 白名单决定发不发请求。
    """

    enabled: bool
    allowed_hosts: frozenset[str] = frozenset()
    locale: Locale = SOURCE_LOCALE


def egress_hosts(rules: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """把 ``permission_rule`` 行收成容器出网的 host 白名单（D3）。

    只认 ``tool_name == 'fetch'`` 且 ``behavior == 'allow'`` 的规则：``allow`` 是唯一表示
    「这个 host 已被人显式放行」的行为；``ask`` / ``deny`` 都不构成白名单。pattern 收成 host
    走 :func:`url_host`（与权限层、工具输入侧**同一份实现**），取不出的 pattern 丢弃——
    「坏 pattern 什么都不匹配」与 ``match_rule`` 同一方向。空 pattern（= 该工具的全部调用）
    不产生 host，因此它不会变成「允许一切」。
    """
    hosts: list[str] = []
    for rule in rules:
        if str(rule.get("tool_name") or "") != FETCH_TOOL_NAME:
            continue
        if str(rule.get("behavior") or "") != "allow":
            continue
        host = url_host(str(rule.get("pattern") or ""))
        if host is not None and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def _chunk(text: str, state: ToolResultState) -> ToolChunk:
    return ToolChunk(content=[TextBlock(text=text)], state=state, is_last=True)


def _error(text: str) -> ToolChunk:
    return _chunk(text, ToolResultState.ERROR)


def search_tool(
    *,
    provider: AnySearchProvider | None = None,
    transport: HttpTransport | None = None,
    environ: Mapping[str, str] | None = None,
    credentials: Mapping[str, str] | None = None,
    default_provider: str | None = None,
    timeout_seconds: float = WEB_TIMEOUT_SECONDS,
    egress: EgressPolicy | None = None,
) -> Any:
    """构造 ``web_search`` 工具：只读（ADR-0026 §三），只送查询词与条数。

    ``provider`` 由装配层（#186）按 :func:`resolve_search_provider` 解析后传入；传 ``None``
    时本工具自己按同一规则解析——**确定性不会从两条路走岔**。凭据优先取库内值
    （``credentials``），再回退到该 provider 的环境变量。显式指定的 provider 缺凭据时，
    **一个请求都不发**，返回可读的错误结果：配置问题不该变成一轮的未捕获异常。
    """
    http: HttpTransport = transport if transport is not None else UrlopenTransport()
    resolution_error = ""
    resolved = provider
    if resolved is None:
        try:
            resolved = resolve_search_provider(
                environ, credentials=credentials, default_provider=default_provider
            )
        except ProviderResolutionError as error:
            resolution_error = error.reason
    backend = SEARCH_BACKENDS[resolved] if resolved is not None else None
    stored = (credentials or {}).get(resolved) if resolved is not None else None
    env_key = ""
    if backend is not None and backend.env_name:
        env_key = web_env(environ).get(backend.env_name, "")
    key = "" if backend is None else (stored or env_key)

    def web_search(query: str, count: int = SEARCH_DEFAULT_RESULTS) -> ToolChunk:
        """在公网检索，返回若干条结果的标题、URL 与摘要。

        只送出 query 与 count（外加 provider 凭据所在的请求头），不要把项目内容或密钥写进 query。

        Args:
            query: 检索词。
            count: 期望结果条数，默认 5，最多 10。
        """
        if egress is not None and not egress.enabled:
            return _error(render("workspace.docker-egress-disabled", egress.locale, tool="web_search"))
        if backend is None:
            reason = resolution_error or "没有配置搜索 provider"
            return _error(f"web_search 未启用：{reason}（本次没有发起任何请求）。")
        if not backend.keyless and not key:
            return _error(
                f"web_search 未启用：provider {resolved} 未配置凭据"
                f"（环境变量 {backend.env_name} 或界面配置），本次没有发起任何请求。"
            )
        sent = max(1, min(count, SEARCH_MAX_RESULTS))
        note = "" if sent == count else f"（请求 {count} 条，按上限 {SEARCH_MAX_RESULTS} 条送出）"
        request = _search_request(backend, key, query, sent, timeout_seconds)
        try:
            response = http.send(request)
        except WebTransportError as error:
            return _error(f"web_search 失败：{error.detail}")
        if response.status != 200:
            return _error(f"web_search 失败：{backend.host} 返回 HTTP {response.status}")
        if backend.protocol == "exa-mcp":
            hits, reason = _exa_mcp_hits(response.body)
            if reason:
                return _error(f"web_search 失败：{backend.host} {reason}")
            return _chunk(_render_hits(query, hits[:sent], note), ToolResultState.SUCCESS)
        try:
            payload = json.loads(response.body.decode("utf-8", errors="replace"))
        except ValueError:
            return _error(f"web_search 失败：{backend.host} 返回的内容不是 JSON")
        hits = _search_hits(backend, payload)
        return _chunk(_render_hits(query, hits[:sent], note), ToolResultState.SUCCESS)

    async def call_web_search(query: str, count: int = SEARCH_DEFAULT_RESULTS) -> ToolChunk:
        return await asyncio.to_thread(web_search, query, count)

    return FunctionTool(call_web_search, name="web_search", is_read_only=True)


def _search_request(
    backend: SearchBackend,
    key: str,
    query: str,
    count: int,
    timeout_seconds: float,
) -> HttpRequest:
    """按 provider 的形状组装请求：查询词与条数是**唯一**送出去的业务内容。"""
    headers: dict[str, str] = {"User-Agent": WEB_USER_AGENT}
    if backend.auth_header:
        headers["Accept"] = "application/json"
        headers[backend.auth_header] = backend.auth_prefix + key
    if backend.protocol == "exa-mcp":
        headers["Accept"] = "application/json, text/event-stream"
        headers["Content-Type"] = "application/json"
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "web_search_exa",
                    "arguments": {"query": query, "numResults": count},
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        return HttpRequest(
            method="POST",
            url=backend.endpoint,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )
    if backend.method == "GET":
        params = {backend.query_name: query}
        if backend.count_name:
            params[backend.count_name] = str(count)
        url = backend.endpoint + "?" + urllib.parse.urlencode(params)
        return HttpRequest(method="GET", url=url, headers=headers, timeout_seconds=timeout_seconds)
    headers["Content-Type"] = "application/json"
    body_fields: dict[str, Any] = {backend.query_name: query}
    if backend.count_name:
        body_fields[backend.count_name] = count
    return HttpRequest(
        method="POST",
        url=backend.endpoint,
        headers=headers,
        body=json.dumps(body_fields, ensure_ascii=False).encode("utf-8"),
        timeout_seconds=timeout_seconds,
    )


def _search_hits(backend: SearchBackend, payload: Any) -> list[SearchHit]:
    """把 provider 的 JSON 映射成 :class:`SearchHit`；缺字段的结果跳过而不是让整次失败。"""
    raw: Any = payload
    for step in backend.results_path:
        raw = raw.get(step, []) if isinstance(raw, dict) else []
    hits: list[SearchHit] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        snippet = ""
        for key in backend.snippet_keys:
            snippet = str(item.get(key) or "").strip()
            if snippet:
                break
        hits.append(SearchHit(title=title, url=url, snippet=snippet))
    return hits


def _jsonrpc_messages(body: bytes) -> list[Any]:
    """从 SSE（逐行 ``data:``）或裸 JSON 回包里取出全部消息对象。"""
    text = body.decode("utf-8", errors="replace")
    messages: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[len("data:") :].strip()
        if not data:
            continue
        try:
            messages.append(json.loads(data))
        except ValueError:
            continue
    if messages:
        return messages
    try:
        return [json.loads(text)]
    except ValueError:
        return []


def _exa_mcp_text(body: bytes) -> tuple[str, str]:
    """取 JSON-RPC 的可读文本：``(正文, 错误原因)``；两者至多一个非空。"""
    messages = _jsonrpc_messages(body)
    if not messages:
        return "", "返回的内容不是 JSON / SSE"
    for message in messages:
        if not isinstance(message, dict):
            continue
        error = message.get("error")
        if isinstance(error, dict):
            return "", str(error.get("message") or "JSON-RPC 调用失败")
        result = message.get("result")
        if not isinstance(result, dict):
            continue
        content = result.get("content")
        texts = (
            [
                str(block.get("text") or "").strip()
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if isinstance(content, list)
            else []
        )
        text = next((item for item in texts if item), "")
        if result.get("isError"):
            return "", text or "工具返回了错误"
        return text, ""
    return "", "回包里没有 JSON-RPC 结果"


def _exa_mcp_hits(body: bytes) -> tuple[list[SearchHit], str]:
    """把 Exa MCP 的文本结果映射成 :class:`SearchHit`：``(结果, 错误原因)``。"""
    text, error = _exa_mcp_text(body)
    if error:
        return [], error
    hits = _exa_text_hits(text)
    return hits, ""


def _exa_text_hits(text: str) -> list[SearchHit]:
    """解析 Exa MCP 的文本块：``Title:`` / ``URL:`` / ``Highlights:``（或 ``Text:``）。

    也接受 JSON 数组（部分部署直接回结构化的 ``title`` / ``url`` / ``text``）。
    """
    stripped = text.strip()
    if stripped.startswith(("[", "{")):
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None
        if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            return _search_hits(SEARCH_BACKENDS["exa"], {"results": payload})
    hits: list[SearchHit] = []
    current: dict[str, Any] | None = None
    snippet_lines: list[str] = []

    def flush() -> None:
        nonlocal current, snippet_lines
        if current and current.get("title") and current.get("url"):
            hits.append(
                SearchHit(
                    title=str(current["title"]),
                    url=str(current["url"]),
                    snippet=" ".join(snippet_lines).strip(),
                )
            )
        current = None
        snippet_lines = []

    for line in stripped.splitlines():
        if line.startswith("Title: "):
            flush()
            current = {"title": line[len("Title: ") :].strip()}
            continue
        if current is None:
            continue
        if line.startswith("URL: "):
            current["url"] = line[len("URL: ") :].strip()
        elif line.startswith(("Published: ", "Author: ")):
            continue
        elif line.startswith(("Highlights:", "Text:")):
            rest = line.split(":", 1)[1].strip()
            if rest:
                snippet_lines.append(rest)
        elif line.strip() == "---":
            continue
        else:
            snippet_lines.append(line.strip())
    flush()
    return hits


def _render_hits(query: str, hits: list[SearchHit], note: str = "") -> str:
    """把结果渲染成文本块；条数与单条摘要都受硬上限约束，截断时把原因写在结果里。"""
    if not hits:
        return f"搜索「{query}」没有找到结果。{note}"
    lines = [f"搜索「{query}」返回 {len(hits)} 条结果{note}：", ""]
    for index, hit in enumerate(hits, start=1):
        lines.append(f"{index}. {hit.title}")
        lines.append(f"   URL：{hit.url}")
        if hit.snippet:
            lines.append(f"   摘要：{_clip(hit.snippet, SEARCH_MAX_SNIPPET_CHARS, '摘要已截断')}")
    return "\n".join(lines)


def _clip(text: str, limit: int, reason: str) -> str:
    """超过 ``limit`` 字符就截断，并把「截断过」写成一句可读说明（不静默丢内容）。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（{reason}，原文 {len(text)} 字符）"


#: 抓取时带上的 UA：本仓没有浏览器，声明自己是脚本比冒充浏览器更诚实。
FETCH_USER_AGENT = WEB_USER_AGENT


class _HtmlText(HTMLParser):
    """把 HTML 收成纯文本：跳过脚本 / 样式 / 模板，块级标签断行，字符实体解码。"""

    #: 内容不属于正文的标签（``title`` 单独读，因此这里不含 ``head`` 的标题捕获）。
    SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "iframe"})

    #: 块级标签：它们的边界在纯文本里表现为换行，否则段落会粘成一行。
    BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "td",
            "th",
            "tr",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skipping = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP_TAGS:
            self._skipping += 1
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP_TAGS and self._skipping:
            self._skipping -= 1
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skipping:
            return
        self._parts.append(data)

    def text(self) -> str:
        """逐行收拢空白：每行内部折叠成单个空格，丢掉空行（HTML 的缩进不该进上下文）。"""
        lines = [" ".join(line.split()) for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _page_text(markup: str) -> tuple[str, str]:
    """把一段 HTML 转成 ``(标题, 纯文本)``；解析器对残缺标签容错（被截断的响应体也能读）。"""
    parser = _HtmlText()
    parser.feed(markup)
    parser.close()
    return " ".join(parser.title.split()), parser.text()


def fetch_tool(
    *,
    transport: HttpTransport | None = None,
    timeout_seconds: float = WEB_TIMEOUT_SECONDS,
    max_bytes: int = FETCH_DEFAULT_MAX_BYTES,
    max_chars: int = FETCH_DEFAULT_MAX_CHARS,
    egress: EgressPolicy | None = None,
) -> Any:
    """构造 ``fetch`` 工具：**不是**只读（ADR-0026 §三：抓取按执行处理，逐次审批）。

    ``max_bytes`` / ``max_chars`` 是本票的硬上限（读多少字节、转成纯文本后保留多少字符）；
    ``timeout_seconds`` 是显式默认值，装配层（#186）可按需覆盖。

    ``egress`` 是 #187 的容器出网策略（``backend == 'docker'`` 时由装配层注入）：开关关闭或
    host 不在白名单时**先拒绝、零请求**，返回说明容器网络策略的可读错误，而不是等一次超时。
    本地后端不注入（``None``），工具行为与 #186 逐字相同。
    """
    http: HttpTransport = transport if transport is not None else UrlopenTransport()

    def fetch(url: str) -> ToolChunk:
        """按 URL 抓取网页，把正文转成纯文本返回。

        警告：这会离开本机——目标地址（可能含内网地址或带 token 的 query）会被第三方看到，
        因此每次调用都需要人类逐次审批（按 host 的权限规则可放行；read-only 档不装配本工具）。
        只接受 http / https。正文有硬上限：超出的部分被截断，并在结果里说明。

        Args:
            url: 要抓取的 http / https 地址。
        """
        if egress is not None and not egress.enabled:
            return _error(render("workspace.docker-egress-disabled", egress.locale, tool=FETCH_TOOL_NAME))
        target = url.strip()
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return _error(f"fetch 未执行：只支持 http / https 地址（收到 {target!r}）。")
        if egress is not None:
            host = url_host(target)
            if host is None or host not in egress.allowed_hosts:
                return _error(
                    render(
                        "workspace.docker-egress-host",
                        egress.locale,
                        tool=FETCH_TOOL_NAME,
                        host=host or target,
                        hosts="、".join(sorted(egress.allowed_hosts)) or "-",
                    )
                )
        request = HttpRequest(
            method="GET",
            url=target,
            headers={
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "User-Agent": FETCH_USER_AGENT,
            },
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        try:
            response = http.send(request)
        except WebTransportError as error:
            return _error(f"fetch 失败：{error.detail}（{parsed.netloc}）")
        if not 200 <= response.status < 300:
            return _error(f"fetch 失败：HTTP {response.status}（{parsed.netloc}）")
        return _chunk(_render_page(target, response, max_bytes, max_chars), ToolResultState.SUCCESS)

    async def call_fetch(url: str) -> ToolChunk:
        return await asyncio.to_thread(fetch, url)

    return FetchTool(call_fetch, name=FETCH_TOOL_NAME, is_read_only=False)


class FetchTool(FunctionTool):
    """``fetch`` 工具：唯一的差别是**权限规则的匹配方式**。

    框架的 ``ToolBase.match_rule`` 默认只认工具名级规则（``rule_content is None``），文件工具各自
    覆盖它做 glob 匹配。``fetch`` 的输入是完整 URL，而人类配置的 pattern 是 host（ADR-0026 §三：
    「允许抓取这个 host」），因此这里把**两侧**都收成 host 再比；规则侧的归一化在
    ``permissions._matched_pattern``，两份都调用同一份 :func:`url_host`。

    刻意**不**覆盖 ``check_permissions``：它继承 ``FunctionTool`` 的 ASK，于是
    ``ask-writes`` / ``trusted-writes`` 都逐次审批，而 allow 规则（host 级）仍能把它放行——
    这正是「不设 ``bypass_immune``」的落点（与 ``AskEveryTimeBash`` 刻意相反）。
    """

    async def match_rule(self, rule_content: str | None, tool_input: dict[str, Any]) -> bool:
        """规则是否命中这次调用：空 pattern = 该工具的全部调用，否则按 host 精确比较。"""
        if not rule_content:
            return True
        pattern = url_host(rule_content)
        target = url_host(str(tool_input.get("url") or ""))
        return pattern is not None and target is not None and pattern == target


def _render_page(target: str, response: HttpResponse, max_bytes: int, max_chars: int) -> str:
    """把响应渲染成「来源 / 标题 / 正文 + 截断说明」。超限只说事实，不静默丢内容。"""
    body = response.body
    notes: list[str] = []
    if len(body) > max_bytes:
        body = body[:max_bytes]
        notes.append(f"响应体超过 {max_bytes} 字节上限，只读了前 {max_bytes} 字节")
    title, text = _page_text(body.decode("utf-8", errors="replace"))
    if len(text) > max_chars:
        notes.append(f"正文超过 {max_chars} 字符上限，只保留了前 {max_chars} 字符")
        text = text[:max_chars]
    lines = [f"来源：{response.url or target}"]
    if title:
        lines.append(f"标题：{title}")
    lines.extend(["", text or "（这个地址没有可读的正文）"])
    if notes:
        lines.extend(["", f"（已截断：{'；'.join(notes)}）"])
    return "\n".join(lines)
