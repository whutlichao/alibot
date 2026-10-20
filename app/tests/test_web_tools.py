"""#185：``web_search`` / ``fetch`` 的工具本体（ADR-0026 §一 / §三 / §四 / §七）。

本票只做**工具本体**：不接装配、不读 ``web_access``、不判权限档（那是 #186）。因此这里的用例
全部经 ``alibot.web`` 的公开构造入口（``search_tool`` / ``fetch_tool``）取证，断言两件事：

1. **请求形状**——送出的是什么（查询词 / 条数 / 超时 / 凭据所在的头），由进程内 stub 传输记录；
2. **结果映射**——provider 的 JSON / 网页字节被映射成可读的 ``ToolChunk`` 文本。

**零网络**：默认传输 ``UrlopenTransport`` 只在两条用例里出现，且注入的是替身 opener（回放
``urllib`` 自己会抛的那两类错误），因此跑测试不发一个包、不需要任何 API key。
"""

from __future__ import annotations

import asyncio
import io
import json
import socket
import urllib.error
from email.message import Message
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from agentscope.message import ToolResultState
from alibot.web import (
    FALLBACK_PROVIDER,
    SEARCH_MAX_RESULTS,
    SEARCH_MAX_SNIPPET_CHARS,
    WEB_TIMEOUT_SECONDS,
    HttpRequest,
    HttpResponse,
    ProviderResolutionError,
    UrlopenTransport,
    WebTransportError,
    describe_search_provider,
    fetch_tool,
    mask_credential,
    resolve_search_provider,
    search_tool,
    web_env,
)


class StubTransport:
    """进程内 stub 传输：记录每个请求的形状，回放构造时给的响应（或抛出给的异常）。"""

    def __init__(self, *responses: Any) -> None:
        self.requests: list[HttpRequest] = []
        self._responses: list[Any] = list(responses) or [HttpResponse(status=200, body=b"")]
        self._index = 0

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        if isinstance(response, BaseException):
            raise response
        return response


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


async def _chunk(tool: Any, **kwargs: Any) -> Any:
    """调用工具并取最后一个 chunk（工具不带 middleware 时就是一个 chunk）。"""
    result = await tool(**kwargs)
    chunks = [chunk async for chunk in result] if hasattr(result, "__aiter__") else [result]
    assert chunks, f"{tool.name} 没有返回任何 chunk"
    return chunks[-1]


def _text(chunk: Any) -> str:
    return "".join(block.text for block in chunk.content if getattr(block, "text", None))


def _json_response(payload: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))


BRAVE_PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "AgentScope 文档",
                "url": "https://doc.agentscope.io/",
                "description": "框架 2.0.10 的文档站",
            },
            {
                "title": "ADR-0026",
                "url": "https://example.org/adr-0026",
                "description": "联网是显式开启的出网能力",
            },
        ]
    }
}


def test_web_search_sends_only_the_query_and_count_to_brave() -> None:
    """Brave：GET 到 ``api.search.brave.com``，只带查询词与条数，凭据在请求头里。"""
    stub = StubTransport(_json_response(BRAVE_PAYLOAD))
    tool = search_tool(provider="brave", transport=stub, environ={"BRAVE_API_KEY": "brave-secret"})

    chunk = _run(_chunk(tool, query="AgentScope 2.0.10", count=2))

    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request.method == "GET"
    parsed = urlsplit(request.url)
    assert parsed.netloc == "api.search.brave.com"
    params = parse_qs(parsed.query)
    assert params["q"] == ["AgentScope 2.0.10"]
    assert params["count"] == ["2"]
    assert set(params) == {"q", "count"}, "web_search 只送查询词与条数（ADR-0026 §一）"
    assert request.timeout_seconds == WEB_TIMEOUT_SECONDS
    assert request.headers["X-Subscription-Token"] == "brave-secret"

    assert chunk.state == ToolResultState.SUCCESS
    text = _text(chunk)
    assert "AgentScope 文档" in text
    assert "https://doc.agentscope.io/" in text
    assert "框架 2.0.10 的文档站" in text
    assert "ADR-0026" in text


TAVILY_PAYLOAD = {
    "results": [
        {
            "title": "Tavily 文档",
            "url": "https://docs.tavily.com/",
            "content": "搜索 API 的接入说明",
        }
    ]
}


def test_web_search_uses_the_tavily_host_and_key_when_configured() -> None:
    """Tavily：POST 到 ``api.tavily.com``，查询词与条数在 JSON 体里，凭据在 Authorization 头。"""
    stub = StubTransport(_json_response(TAVILY_PAYLOAD))
    tool = search_tool(provider="tavily", transport=stub, environ={"TAVILY_API_KEY": "tvly-secret"})

    chunk = _run(_chunk(tool, query="AgentScope 工具", count=3))

    request = stub.requests[0]
    assert request.method == "POST"
    parsed = urlsplit(request.url)
    assert parsed.netloc == "api.tavily.com"
    assert parsed.query == ""
    assert request.body is not None
    payload = json.loads(request.body.decode("utf-8"))
    assert payload["query"] == "AgentScope 工具"
    assert payload["max_results"] == 3
    assert set(payload) == {"query", "max_results"}, "只送查询词与条数（ADR-0026 §一）"
    assert request.timeout_seconds == WEB_TIMEOUT_SECONDS
    assert request.headers["Authorization"] == "Bearer tvly-secret"

    assert chunk.state == ToolResultState.SUCCESS
    text = _text(chunk)
    assert "Tavily 文档" in text
    assert "https://docs.tavily.com/" in text
    assert "搜索 API 的接入说明" in text


def test_web_search_uses_the_perplexity_search_shape() -> None:
    """Perplexity：POST 到 ``/search``，JSON 体，Bearer 凭据；结果映射 ``results[].snippet``。"""
    stub = StubTransport(
        _json_response(
            {"results": [{"title": "P 文档", "url": "https://docs.perplexity.ai/", "snippet": "检索 API"}]}
        )
    )
    tool = search_tool(provider="perplexity", transport=stub, environ={"PERPLEXITY_API_KEY": "pplx-secret"})

    chunk = _run(_chunk(tool, query="AgentScope", count=4))

    request = stub.requests[0]
    assert request.method == "POST"
    assert urlsplit(request.url).netloc == "api.perplexity.ai"
    assert urlsplit(request.url).path == "/search"
    assert request.headers["Authorization"] == "Bearer pplx-secret"
    assert request.body is not None
    assert json.loads(request.body.decode("utf-8")) == {"query": "AgentScope", "max_results": 4}
    assert chunk.state == ToolResultState.SUCCESS
    assert "P 文档" in _text(chunk) and "检索 API" in _text(chunk)


def test_web_search_uses_the_jina_search_shape() -> None:
    """Jina：GET 到 ``s.jina.ai``，查询词在 ``q``，Bearer 凭据；结果映射 ``data[].description``。"""
    stub = StubTransport(
        _json_response({"data": [{"title": "J 文档", "url": "https://jina.ai/", "description": "阅读器"}]})
    )
    tool = search_tool(provider="jina", transport=stub, environ={"JINA_API_KEY": "jina-secret"})

    chunk = _run(_chunk(tool, query="AgentScope", count=2))

    request = stub.requests[0]
    assert request.method == "GET"
    parsed = urlsplit(request.url)
    assert parsed.netloc == "s.jina.ai"
    assert parse_qs(parsed.query) == {"q": ["AgentScope"]}, "Jina 只收查询词"
    assert request.headers["Authorization"] == "Bearer jina-secret"
    assert chunk.state == ToolResultState.SUCCESS
    assert "J 文档" in _text(chunk) and "阅读器" in _text(chunk)


def test_web_search_uses_the_exa_rest_shape_when_a_key_exists() -> None:
    """Exa（有 key）：POST 到 ``api.exa.ai/search``，``x-api-key``，body 用 ``numResults``。"""
    stub = StubTransport(
        _json_response({"results": [{"title": "Exa 文档", "url": "https://exa.ai/", "text": "语义检索"}]})
    )
    tool = search_tool(provider="exa", transport=stub, environ={"EXA_API_KEY": "exa-secret"})

    chunk = _run(_chunk(tool, query="AgentScope", count=2))

    request = stub.requests[0]
    assert request.method == "POST"
    assert urlsplit(request.url).netloc == "api.exa.ai"
    assert request.headers["x-api-key"] == "exa-secret"
    assert request.body is not None
    assert json.loads(request.body.decode("utf-8")) == {"query": "AgentScope", "numResults": 2}
    assert chunk.state == ToolResultState.SUCCESS
    assert "Exa 文档" in _text(chunk) and "语义检索" in _text(chunk)


EXA_TEXT = (
    "Title: AgentScope 文档\n"
    "URL: https://doc.agentscope.io/\n"
    "Published: 2026-01-01\n"
    "Highlights:\n"
    "框架 2.0.10 的文档站\n"
    "---\n"
    "Title: AgentScope 仓库\n"
    "URL: https://github.com/agentscope/agentscope\n"
    "Text:\n"
    "多智能体框架的源码与示例\n"
    "Title: 第三条结果\n"
    "URL: https://example.org/third\n"
    "Highlights:\n"
    "这条不该出现在 count=2 的响应里\n"
)


def _exa_sse(text: str) -> HttpResponse:
    """Exa MCP 的典型回包：SSE，一行一个 ``data:``，正文在 ``result.content[0].text``。"""
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}]},
    }
    body = f"event: message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
    return HttpResponse(status=200, body=body.encode("utf-8"))


def test_exa_mcp_posts_a_jsonrpc_tools_call_without_any_credential() -> None:
    """免凭据回退：只发查询词与条数；没有凭据头，URL 是 Exa MCP 的零配置端点。"""
    stub = StubTransport(_exa_sse(EXA_TEXT))
    tool = search_tool(provider=FALLBACK_PROVIDER, transport=stub, environ={})

    chunk = _run(_chunk(tool, query="AgentScope", count=2))

    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request.method == "POST"
    assert request.url == "https://mcp.exa.ai/mcp?tools=web_search_exa"
    assert dict(request.headers) == {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "alibot-web-tools/0.1",
    }, "免凭据 provider 不带任何凭据头"
    assert request.body is not None
    assert json.loads(request.body.decode("utf-8")) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {"query": "AgentScope", "numResults": 2},
        },
    }
    assert request.timeout_seconds == WEB_TIMEOUT_SECONDS

    assert chunk.state == ToolResultState.SUCCESS
    text = _text(chunk)
    assert "AgentScope 文档" in text
    assert "https://doc.agentscope.io/" in text
    assert "框架 2.0.10 的文档站" in text, "Highlights 是摘要的来源"
    assert "AgentScope 仓库" in text
    assert "多智能体框架的源码与示例" in text, "没有 Highlights 时退回 Text:"
    assert "第三条结果" not in text, "条数按请求值截断"


def test_exa_mcp_parses_a_bare_json_body_and_reports_errors_readably() -> None:
    """裸 JSON 同样可读；JSON-RPC error / ``result.isError`` / 空结果都收口成可读结果。"""
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": EXA_TEXT}]},
    }
    stub = StubTransport(_json_response(message))
    chunk = _run(_chunk(search_tool(provider=FALLBACK_PROVIDER, transport=stub, environ={}), query="x"))
    assert chunk.state == ToolResultState.SUCCESS
    assert "AgentScope 文档" in _text(chunk)

    empty = StubTransport(_exa_sse(""))
    empty_chunk = _run(
        _chunk(search_tool(provider=FALLBACK_PROVIDER, transport=empty, environ={}), query="没有结果")
    )
    assert empty_chunk.state == ToolResultState.SUCCESS
    assert "没有找到结果" in _text(empty_chunk)

    jsonrpc_error = _json_response(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "上游炸了"}}
    )
    error_chunk = _run(
        _chunk(
            search_tool(provider=FALLBACK_PROVIDER, transport=StubTransport(jsonrpc_error), environ={}),
            query="x",
        )
    )
    assert error_chunk.state == ToolResultState.ERROR
    assert "上游炸了" in _text(error_chunk)

    tool_error = _json_response(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"isError": True, "content": [{"type": "text", "text": "query is required"}]},
        }
    )
    tool_error_chunk = _run(
        _chunk(
            search_tool(provider=FALLBACK_PROVIDER, transport=StubTransport(tool_error), environ={}),
            query="x",
        )
    )
    assert tool_error_chunk.state == ToolResultState.ERROR
    assert "query is required" in _text(tool_error_chunk)


def test_a_429_is_a_readable_result_and_no_retry_happens() -> None:
    """429 与其它非 200 一样：写明 provider 与状态码，一个请求只发一次。"""
    stub = StubTransport(HttpResponse(status=429, body=b"slow down"))
    tool = search_tool(provider="brave", transport=stub, environ={"BRAVE_API_KEY": "k"})

    chunk = _run(_chunk(tool, query="AgentScope"))

    assert chunk.state == ToolResultState.ERROR
    text = _text(chunk)
    assert "429" in text and "api.search.brave.com" in text
    assert len(stub.requests) == 1, "不重试"


def test_the_database_credential_wins_over_the_environment() -> None:
    """凭据来源优先级：库内值优先，环境变量只是兜底；响应头只出现库内那份。"""
    stub = StubTransport(_json_response(BRAVE_PAYLOAD))
    tool = search_tool(
        provider="brave",
        transport=stub,
        environ={"BRAVE_API_KEY": "env-secret"},
        credentials={"brave": "db-secret"},
    )

    _run(_chunk(tool, query="AgentScope"))

    assert stub.requests[0].headers["X-Subscription-Token"] == "db-secret"


def test_mask_credential_never_reveals_the_full_value() -> None:
    """掩码是唯一的可回读形态：首三 + 尾四；短值（含 8 位）只回省略号。"""
    assert mask_credential("sk-1234567890abcd") == "sk-…abcd"
    assert mask_credential("sk-123456") == "…"
    assert mask_credential("abcdefgh") == "…"
    assert mask_credential("sk-1234567") == "…"
    assert mask_credential("sk-12345678") == "sk-…5678"
    assert "1234567890" not in mask_credential("sk-1234567890abcd")


def test_web_env_only_reads_the_whitelisted_names() -> None:
    """凭据只从**名字白名单**里读：其它环境变量一个都不进来（形状同 ``docker_env``）。"""
    environ = {
        "BRAVE_API_KEY": "brave-secret",
        "TAVILY_API_KEY": "tavily-secret",
        "PERPLEXITY_API_KEY": "pplx-secret",
        "JINA_API_KEY": "jina-secret",
        "EXA_API_KEY": "exa-secret",
        "PATH": "/usr/bin:/bin",
        "OPENAI_API_KEY": "openai-secret",
    }
    assert web_env(environ) == {
        "BRAVE_API_KEY": "brave-secret",
        "TAVILY_API_KEY": "tavily-secret",
        "PERPLEXITY_API_KEY": "pplx-secret",
        "JINA_API_KEY": "jina-secret",
        "EXA_API_KEY": "exa-secret",
    }


def test_web_env_skips_empty_values() -> None:
    """空串等于没有配置：不产生一个「看起来有 key」的条目。"""
    assert web_env({"BRAVE_API_KEY": "", "TAVILY_API_KEY": "tavily-secret"}) == {
        "TAVILY_API_KEY": "tavily-secret"
    }


def test_web_search_reports_a_missing_key_without_sending_anything() -> None:
    """环境里没有 key：返回可读错误、**一个请求都不发**，且不抛未捕获异常。"""
    stub = StubTransport()
    tool = search_tool(provider="brave", transport=stub, environ={})

    chunk = _run(_chunk(tool, query="AgentScope"))

    assert chunk.state == ToolResultState.ERROR
    assert "BRAVE_API_KEY" in _text(chunk)
    assert stub.requests == []


def test_the_explicit_env_var_wins_over_the_database_default() -> None:
    """① 进程环境显式指定优先于凭据表的默认项；来源标记为 ``env``。"""
    choice = describe_search_provider(
        {"ALIBOT_WEB_SEARCH_PROVIDER": "brave", "BRAVE_API_KEY": "b"},
        credentials={"tavily": "t"},
        default_provider="tavily",
    )
    assert (choice.provider, choice.source) == ("brave", "env")

    switched = describe_search_provider(
        {"ALIBOT_WEB_SEARCH_PROVIDER": "tavily", "BRAVE_API_KEY": "b"},
        credentials={"brave": "b", "tavily": "t"},
        default_provider="brave",
    )
    assert (switched.provider, switched.source) == ("tavily", "env")


def test_the_database_default_wins_over_the_fixed_order() -> None:
    """② 凭据表里 ``is_default`` 的 provider 优先于固定顺序；来源标记为 ``ui``。"""
    choice = describe_search_provider(
        {},
        credentials={"brave": "b", "jina": "j"},
        default_provider="jina",
    )
    assert (choice.provider, choice.source) == ("jina", "ui")
    assert (
        resolve_search_provider({}, credentials={"brave": "b", "jina": "j"}, default_provider="jina")
        == "jina"
    )


def test_configured_providers_follow_the_fixed_order() -> None:
    """③ 没有默认项时按 brave → tavily → perplexity → jina → exa 的固定顺序（不再报「两把都在」）。"""
    both_env = {"BRAVE_API_KEY": "b", "TAVILY_API_KEY": "t"}
    assert resolve_search_provider(both_env) == "brave"
    assert describe_search_provider(both_env).source == "env"

    choice = describe_search_provider({}, credentials={"tavily": "t", "brave": "b"})
    assert (choice.provider, choice.source) == ("brave", "ui")

    # 只配了后面的某一家时选它（顺序是「已配置集合」的顺序，不是「前面没配就报错」）。
    assert resolve_search_provider({}, credentials={"exa": "e", "jina": "j"}) == "jina"


def test_without_any_credential_the_fallback_is_the_keyless_exa_mcp() -> None:
    """④ 都没有 ⇒ 免凭据回退；工具照常发一次请求、不带任何凭据头。"""
    choice = describe_search_provider({})
    assert (choice.provider, choice.source) == (FALLBACK_PROVIDER, "fallback")

    stub = StubTransport(_exa_sse(EXA_TEXT))
    tool = search_tool(transport=stub, environ={})

    chunk = _run(_chunk(tool, query="AgentScope"))

    assert chunk.state == ToolResultState.SUCCESS
    assert len(stub.requests) == 1
    assert stub.requests[0].url.startswith("https://mcp.exa.ai/mcp")
    assert "AgentScope 文档" in _text(chunk)


def test_an_unknown_provider_value_is_not_silently_ignored() -> None:
    """显式变量只接受全部已知取值，其它值报可读错误，**不静默回落**。"""
    with pytest.raises(ProviderResolutionError):
        resolve_search_provider({"ALIBOT_WEB_SEARCH_PROVIDER": "duckduckgo", "BRAVE_API_KEY": "b"})

    stub = StubTransport()
    tool = search_tool(transport=stub, environ={"ALIBOT_WEB_SEARCH_PROVIDER": "duckduckgo"})
    chunk = _run(_chunk(tool, query="AgentScope"))
    assert chunk.state == ToolResultState.ERROR
    assert "duckduckgo" in _text(chunk)
    assert stub.requests == []


def test_explicit_exa_without_a_key_uses_the_keyless_mcp_path() -> None:
    """显式点名 ``exa`` 但没有任何 key ⇒ 走免凭据的 exa-mcp（有 key 才走 REST）。"""
    choice = describe_search_provider({"ALIBOT_WEB_SEARCH_PROVIDER": "exa"})
    assert (choice.provider, choice.source) == (FALLBACK_PROVIDER, "fallback")

    with_key = describe_search_provider({"ALIBOT_WEB_SEARCH_PROVIDER": "exa", "EXA_API_KEY": "exa-secret"})
    assert (with_key.provider, with_key.source) == ("exa", "env")


def test_an_explicit_credentialed_provider_without_a_key_reports_it_readably() -> None:
    """显式指定带凭据 provider 却缺 key ⇒ 可读错误（只提 provider 与变量名），零请求。"""
    stub = StubTransport()
    tool = search_tool(transport=stub, environ={"ALIBOT_WEB_SEARCH_PROVIDER": "brave"})

    chunk = _run(_chunk(tool, query="AgentScope"))

    assert chunk.state == ToolResultState.ERROR
    text = _text(chunk)
    assert "brave" in text and "BRAVE_API_KEY" in text and "未配置凭据" in text
    assert stub.requests == []


def test_web_search_never_echoes_the_key_in_an_error() -> None:
    """HTTP 状态错误只报状态码与 host，凭据取值绝不进工具结果（ADR-0026 §四）。"""
    stub = StubTransport(HttpResponse(status=401, body=b'{"error": "invalid api key"}'))
    tool = search_tool(provider="tavily", transport=stub, environ={"TAVILY_API_KEY": "tvly-very-secret"})

    chunk = _run(_chunk(tool, query="AgentScope"))

    assert chunk.state == ToolResultState.ERROR
    text = _text(chunk)
    assert "401" in text
    assert "tvly-very-secret" not in text


def test_web_search_reports_timeout_and_unreachable_readably() -> None:
    """超时与「连不上（含域名解析失败）」各自给可读结果，不抛未捕获异常。"""
    timeout_tool = search_tool(
        provider="brave",
        transport=StubTransport(WebTransportError("timeout", "请求超时（15 秒）")),
        environ={"BRAVE_API_KEY": "brave-secret"},
    )
    timeout_chunk = _run(_chunk(timeout_tool, query="AgentScope"))
    assert timeout_chunk.state == ToolResultState.ERROR
    assert "超时" in _text(timeout_chunk)

    unreachable_tool = search_tool(
        provider="brave",
        transport=StubTransport(
            WebTransportError("unreachable", "无法连接目标地址：Name or service not known")
        ),
        environ={"BRAVE_API_KEY": "brave-secret"},
    )
    unreachable_chunk = _run(_chunk(unreachable_tool, query="AgentScope"))
    assert unreachable_chunk.state == ToolResultState.ERROR
    assert "无法连接目标地址" in _text(unreachable_chunk)


def test_web_search_clamps_the_requested_count_and_says_so() -> None:
    """条数有硬上限：超出时按上限送出，并在结果里说明「请求了几条、实际送了几条」。"""
    stub = StubTransport(_json_response(BRAVE_PAYLOAD))
    tool = search_tool(provider="brave", transport=stub, environ={"BRAVE_API_KEY": "brave-secret"})

    chunk = _run(_chunk(tool, query="AgentScope", count=50))

    params = parse_qs(urlsplit(stub.requests[0].url).query)
    assert params["count"] == [str(SEARCH_MAX_RESULTS)]
    text = _text(chunk)
    assert str(SEARCH_MAX_RESULTS) in text
    assert "50" in text


def test_web_search_truncates_a_long_snippet_with_a_readable_note() -> None:
    """摘要也有字符上限：截断而不是把整段描述塞进上下文。"""
    payload = {
        "web": {
            "results": [
                {
                    "title": "长摘要",
                    "url": "https://example.org/long",
                    "description": "很长" * (SEARCH_MAX_SNIPPET_CHARS * 2),
                }
            ]
        }
    }
    stub = StubTransport(_json_response(payload))
    tool = search_tool(provider="brave", transport=stub, environ={"BRAVE_API_KEY": "brave-secret"})

    chunk = _run(_chunk(tool, query="长摘要"))

    text = _text(chunk)
    assert "很长" * SEARCH_MAX_SNIPPET_CHARS not in text
    assert "摘要已截断" in text


HTML_PAGE = (
    "<html><head><title>ADR-0026 联网</title><style>.x{color:red}</style></head>"
    "<body><h1>Agent 联网</h1><p>Entities: &amp; &lt;tag&gt;</p>"
    "<script>var leaked = 1;</script><p>正文第二段</p></body></html>"
).encode()


def test_fetch_converts_a_page_to_plain_text() -> None:
    """fetch：GET 那个 URL，把 HTML 转成纯文本（脚本与样式不进结果），并报出最终 URL。"""
    stub = StubTransport(HttpResponse(status=200, body=HTML_PAGE, url="https://example.org/final"))
    tool = fetch_tool(transport=stub)

    chunk = _run(_chunk(tool, url="https://example.org/start"))

    request = stub.requests[0]
    assert request.method == "GET"
    assert request.url == "https://example.org/start"
    assert request.timeout_seconds == WEB_TIMEOUT_SECONDS
    assert chunk.state == ToolResultState.SUCCESS
    text = _text(chunk)
    assert "Agent 联网" in text
    assert "正文第二段" in text
    assert "& <tag>" in text
    assert "leaked" not in text, "脚本内容不该进正文"
    assert "color:red" not in text, "样式内容不该进正文"
    assert "https://example.org/final" in text, "要能告诉模型正文的真实来源"
    assert "<h1>" not in text


def test_fetch_refuses_non_http_urls_without_sending_anything() -> None:
    """只抓 http / https：``file:`` 之类的本地读取入口一个都不开（ADR-0026 §七）。"""
    stub = StubTransport()
    tool = fetch_tool(transport=stub)

    chunk = _run(_chunk(tool, url="file:///etc/passwd"))

    assert chunk.state == ToolResultState.ERROR
    assert "http" in _text(chunk)
    assert stub.requests == []


def test_fetch_reports_an_http_error_status_readably() -> None:
    stub = StubTransport(HttpResponse(status=404, body=b"not found"))
    tool = fetch_tool(transport=stub)

    chunk = _run(_chunk(tool, url="https://example.org/missing"))

    assert chunk.state == ToolResultState.ERROR
    assert "404" in _text(chunk)


def test_fetch_truncates_a_huge_body_and_says_so() -> None:
    """超大响应体：按字节与字符两道上限截断，并在结果里说明「截断过」（不静默丢内容）。"""
    body = ("<html><body><p>" + "A" * 5_000 + "END-OF-BODY</p></body></html>").encode("utf-8")
    stub = StubTransport(HttpResponse(status=200, body=body))
    tool = fetch_tool(transport=stub, max_bytes=2_000, max_chars=500)

    chunk = _run(_chunk(tool, url="https://example.org/huge"))

    assert stub.requests[0].max_bytes == 2_000, "字节上限要传到传输层，太大的响应体根本不读完"
    text = _text(chunk)
    assert "END-OF-BODY" not in text
    assert "字节" in text and "截断" in text
    assert "字符" in text
    assert chunk.state == ToolResultState.SUCCESS


def test_fetch_and_web_search_declare_their_read_only_difference() -> None:
    """ADR-0026 §三：search 只读、fetch 不是（它按执行处理、逐次审批）。"""
    search = search_tool(provider="brave", transport=StubTransport(), environ={"BRAVE_API_KEY": "k"})
    page = fetch_tool(transport=StubTransport())

    assert search.name == "web_search"
    assert page.name == "fetch"
    assert search.is_read_only is True
    assert page.is_read_only is False


class _FailingOpener:
    """替身 opener：抛出给定的异常，于是「真实传输的错误分类」也有读数且不联网。"""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __call__(self, request: Any, timeout: float) -> Any:
        raise self.error


def test_default_transport_reports_a_dns_failure_readably() -> None:
    """真实传输（``UrlopenTransport``）把域名解析失败收口成可读的「无法连接」，不向上抛异常。"""
    opener = _FailingOpener(urllib.error.URLError(socket.gaierror(-2, "Name or service not known")))
    tool = fetch_tool(transport=UrlopenTransport(opener=opener))

    chunk = _run(_chunk(tool, url="https://no-such-host.invalid/page"))

    assert chunk.state == ToolResultState.ERROR
    text = _text(chunk)
    assert "无法连接目标地址" in text
    assert "Name or service not known" in text


def test_default_transport_reports_a_timeout_readably() -> None:
    """超时有两条真实路径：``urllib`` 直接抛 ``TimeoutError``，或把它包进 ``URLError``。"""
    direct = fetch_tool(transport=UrlopenTransport(opener=_FailingOpener(TimeoutError("timed out"))))
    direct_chunk = _run(_chunk(direct, url="https://example.org/slow"))
    assert direct_chunk.state == ToolResultState.ERROR
    assert "超时" in _text(direct_chunk)

    wrapped = fetch_tool(
        transport=UrlopenTransport(opener=_FailingOpener(urllib.error.URLError(TimeoutError("timed out"))))
    )
    wrapped_chunk = _run(_chunk(wrapped, url="https://example.org/slow"))
    assert wrapped_chunk.state == ToolResultState.ERROR
    assert "超时" in _text(wrapped_chunk)


def test_default_transport_reads_a_broken_connection_as_unreachable() -> None:
    """读响应体时断链（``OSError`` 子类，``urllib`` 不会再包成 ``URLError``）也是可读结果。"""
    tool = fetch_tool(
        transport=UrlopenTransport(opener=_FailingOpener(ConnectionResetError("reset by peer")))
    )

    chunk = _run(_chunk(tool, url="https://example.org/flaky"))

    assert chunk.state == ToolResultState.ERROR
    assert "无法连接目标地址" in _text(chunk)


def test_default_transport_turns_an_http_error_status_into_a_readable_result() -> None:
    """4xx / 5xx 不是异常：状态码是一档错误分类，工具结果是可读文本。"""
    error = urllib.error.HTTPError(
        "https://example.org/boom",
        503,
        "Service Unavailable",
        Message(),
        io.BytesIO(b""),
    )
    tool = fetch_tool(transport=UrlopenTransport(opener=_FailingOpener(error)))

    chunk = _run(_chunk(tool, url="https://example.org/boom"))

    assert chunk.state == ToolResultState.ERROR
    assert "503" in _text(chunk)


def test_the_default_timeout_can_be_overridden_per_assembly() -> None:
    """超时默认值显式可读，且装配层能按调用覆盖它（两个工具都要）。"""
    search_stub = StubTransport(_json_response(BRAVE_PAYLOAD))
    search = search_tool(
        provider="brave",
        transport=search_stub,
        environ={"BRAVE_API_KEY": "brave-secret"},
        timeout_seconds=3.5,
    )
    _run(_chunk(search, query="AgentScope"))
    assert search_stub.requests[0].timeout_seconds == 3.5

    page_stub = StubTransport(HttpResponse(status=200, body=HTML_PAGE))
    page = fetch_tool(transport=page_stub, timeout_seconds=3.5)
    _run(_chunk(page, url="https://example.org/start"))
    assert page_stub.requests[0].timeout_seconds == 3.5
