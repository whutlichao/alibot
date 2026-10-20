import asyncio
import json
import sys

sys.path.insert(0, "/Users/apple/orca/workspaces/alibot/web-search-providers/app")
from alibot.web import HttpRequest, HttpResponse, mask_credential, search_tool


class StubTransport:
    def __init__(self):
        self.requests = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        payload = {
            "results": [{"title": "Tavily 文档", "url": "https://docs.tavily.com/", "content": "检索 API"}]
        }
        return HttpResponse(status=200, body=json.dumps(payload).encode())


async def main():
    stub = StubTransport()
    tool = search_tool(
        provider="tavily",
        credentials={"tavily": "tvly-1234567890abcd"},
        transport=stub,
        environ={},
    )
    result = await tool(query="AgentScope", count=2)
    chunks = [chunk async for chunk in result] if hasattr(result, "__aiter__") else [result]
    request = stub.requests[0]
    print("method:", request.method)
    print("url:", request.url)
    print("auth header:", request.headers["Authorization"])
    print("body:", json.loads(request.body.decode()))
    print("tool state:", chunks[-1].state)
    print("masked read:", mask_credential("tvly-1234567890abcd"))
    print("full key in mask:", "1234567890" in mask_credential("tvly-1234567890abcd"))


asyncio.run(main())
