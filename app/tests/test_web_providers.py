"""#199：联网 provider 凭据的人类面（``/api/web-providers``）。

判据来源：ADR-0026 §四修订（凭据明文入库、掩码回读；保存 key 同时写数据面确认）、
派发任务书 B3（PUT / GET / DELETE、非法 provider 400 且不部分写入、DELETE 清空）。

接缝 = 既有 HTTP 黑盒（``client`` 夹具）；断言只落在对外载荷与 ``GET /api/remote-data-flow``
的只读确认行上。完整 key **从不**出现在任何响应里——这条由「响应文本里找不到原值」直接取证。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

PROVIDER_ENV_NAMES = (
    "ALIBOT_WEB_SEARCH_PROVIDER",
    "BRAVE_API_KEY",
    "TAVILY_API_KEY",
    "PERPLEXITY_API_KEY",
    "JINA_API_KEY",
    "EXA_API_KEY",
)

SECRET = "sk-1234567890abcd"
MASKED = "sk-…abcd"


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """进程环境会参与 provider 解析；这组用例只断言库内配置，先把环境清空。"""
    for name in PROVIDER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield


def _credential(client: TestClient, provider: str) -> dict:
    response = client.get(f"/api/web-providers/{provider}/credential")
    assert response.status_code == 200, response.text
    return response.json()["credential"]


def _web_consents(client: TestClient) -> list[dict]:
    response = client.get("/api/remote-data-flow")
    assert response.status_code == 200, response.text
    return response.json()["web_consents"]


def test_saving_a_key_returns_only_the_mask_and_writes_the_consent_row(client: TestClient) -> None:
    """PUT → 只回掩码；GET 同样只回掩码；同一动作写入 ``('web', provider)`` 确认行。"""
    saved = client.put(
        "/api/web-providers/brave/credential",
        json={"api_key": SECRET, "is_default": True},
    )
    assert saved.status_code == 200, saved.text
    credential = saved.json()["credential"]
    assert credential["provider"] == "brave"
    assert credential["configured"] is True
    assert credential["masked"] == MASKED
    assert credential["is_default"] is True
    assert "api_key" not in credential
    assert SECRET not in saved.text, "完整 key 绝不能出现在响应里"

    read = _credential(client, "brave")
    assert read == credential
    assert SECRET not in client.get("/api/web-providers").text

    assert [row["scope_id"] for row in _web_consents(client)] == ["brave"]


def test_the_default_provider_can_be_switched_without_reentering_the_key(client: TestClient) -> None:
    """「设为默认」是一次不带 key 的 PUT：切换唯一默认，原 key 与确认行都不受影响。"""
    for provider in ("brave", "tavily"):
        assert (
            client.put(
                f"/api/web-providers/{provider}/credential", json={"api_key": f"{provider}-secret-1234"}
            ).status_code
            == 200
        )

    switched = client.put("/api/web-providers/tavily/credential", json={"is_default": True})
    assert switched.status_code == 200, switched.text
    credential = switched.json()["credential"]
    assert credential["is_default"] is True
    assert credential["masked"] == "tav…1234", "切换默认不该回写或回吐 key"

    providers = {item["provider"]: item for item in switched.json()["providers"]}
    assert providers["tavily"]["is_default"] is True
    assert providers["brave"]["is_default"] is False
    assert providers["tavily"]["masked"] == "tav…1234"
    assert switched.json()["active"] == {"provider": "tavily", "source": "ui"}


def test_delete_clears_the_key_and_the_default_flag_but_keeps_the_consent_row(
    client: TestClient,
) -> None:
    """DELETE 清空凭据与默认标记；历史确认行保留（删除不撤销已发生的告知）。"""
    client.put("/api/web-providers/brave/credential", json={"api_key": SECRET, "is_default": True})

    deleted = client.delete("/api/web-providers/brave/credential")
    assert deleted.status_code == 200, deleted.text
    credential = deleted.json()["credential"]
    assert credential["configured"] is False
    assert credential["masked"] == ""
    assert credential["is_default"] is False
    assert _credential(client, "brave")["configured"] is False
    assert deleted.json()["active"] == {"provider": "exa-mcp", "source": "fallback"}
    assert [row["scope_id"] for row in _web_consents(client)] == ["brave"]


def test_an_unknown_provider_is_rejected_without_a_partial_write(client: TestClient) -> None:
    """词表外的 provider 整条 400，且库里不留下任何凭据或确认行。"""
    before = client.get("/api/web-providers").json()["providers"]

    rejected = client.put(
        "/api/web-providers/duckduckgo/credential", json={"api_key": SECRET, "is_default": True}
    )

    assert rejected.status_code == 400, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "validation_failed"
    assert "provider" in error["fields"], error
    assert client.get("/api/web-providers").json()["providers"] == before
    assert _web_consents(client) == []


def test_an_empty_or_oversized_key_is_rejected_without_writing(client: TestClient) -> None:
    """PUT 校验取值非空、长度有上限；两种失败都不写凭据、也不写确认行。"""
    for payload in ({"api_key": "   "}, {"api_key": "x" * 5000}):
        rejected = client.put("/api/web-providers/brave/credential", json=payload)
        assert rejected.status_code == 400, rejected.text
        error = rejected.json()["error"]
        assert "api_key" in error["fields"], error

    assert _credential(client, "brave")["configured"] is False
    assert _web_consents(client) == []


def test_all_five_credentialed_providers_are_listed(client: TestClient) -> None:
    """列表固定出现五家（未配置也出现），当前 provider 在没有任何凭据时是免凭据回退。"""
    payload = client.get("/api/web-providers").json()

    assert [item["provider"] for item in payload["providers"]] == [
        "brave",
        "tavily",
        "perplexity",
        "jina",
        "exa",
    ]
    assert all(item["configured"] is False for item in payload["providers"])
    assert payload["active"] == {"provider": "exa-mcp", "source": "fallback"}


def test_an_empty_key_is_rejected_even_after_a_key_was_saved(client: TestClient) -> None:
    """显式空串永远是校验失败，不能因为「已有行」就变成一次静默的 200 no-op。"""
    client.put("/api/web-providers/brave/credential", json={"api_key": SECRET})

    rejected = client.put("/api/web-providers/brave/credential", json={"api_key": ""})

    assert rejected.status_code == 400, rejected.text
    assert "api_key" in rejected.json()["error"]["fields"]
    assert _credential(client, "brave")["masked"] == MASKED
