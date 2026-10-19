"""#186 / D6：web 数据面确认的人类面写入口（``POST /api/web-providers/{provider}/consent``）。

判据来源：**ADR-0026 §四**（出网前按 provider 取得一次性确认；只存「何时确认过」，不存凭证与
连接参数）、**人定 D6**（补齐 ADR §四 可达性的**最小**落点：扩 ``record_remote_consent_scope`` 的
allowlist + 一个形状照 ``POST /api/docker-images/consent`` / ``POST /api/mcp-servers/{id}/consent``
的幂等端点，不是新产品面设计）。

接缝 = 既有 HTTP 黑盒（``client`` 夹具 + ``GET /api/remote-data-flow`` 的只读载荷），与
``test_mcp_servers.py`` / ``test_agent_detail.py`` 的确认用例同族：不新开 seam、不读表结构。
provider 的取值只有工具面自己的枚举（``brave`` / ``tavily``），不接受任意字符串。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _web_consents(client: TestClient) -> list[dict]:
    response = client.get("/api/remote-data-flow")
    assert response.status_code == 200, response.text
    return response.json()["web_consents"]


def test_confirming_a_web_provider_is_idempotent_and_stores_only_the_time(
    client: TestClient,
) -> None:
    """确认一次 → 落一行 ``('web', provider)``；重复确认不覆盖首次时间；两家各自一行。"""
    assert _web_consents(client) == []

    first = client.post("/api/web-providers/brave/consent")
    assert first.status_code == 200, first.text
    consent = first.json()["consent"]
    assert consent["scope_kind"] == "web"
    assert consent["scope_id"] == "brave"
    assert consent["acknowledged_at"].endswith("Z")
    assert _web_consents(client) == [consent]

    second = client.post("/api/web-providers/brave/consent")
    assert second.status_code == 200, second.text
    assert second.json()["consent"]["acknowledged_at"] == consent["acknowledged_at"]

    tavily = client.post("/api/web-providers/tavily/consent")
    assert tavily.status_code == 200, tavily.text
    assert tavily.json()["consent"]["scope_id"] == "tavily"
    assert {row["scope_id"] for row in _web_consents(client)} == {"brave", "tavily"}


def test_an_unknown_web_provider_is_rejected_and_writes_nothing(client: TestClient) -> None:
    """词表外的 provider 整条 400（可读字段错误），且库里不留下那一行。"""
    response = client.post("/api/web-providers/duckduckgo/consent")

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "provider" in error["fields"], error
    assert _web_consents(client) == []
