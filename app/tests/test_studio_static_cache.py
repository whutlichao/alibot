"""工作台静态资源的缓存契约：每次回源校验，内容未变时仍是 304。

背景：``/studio/`` 原先只发 ETag / Last-Modified，没有 ``Cache-Control``。浏览器按启发式缓存
可以保留旧的 ``i18n.js`` 而拿到新的 ``index.html``；新 HTML 里的词条键在旧表里查不到，界面就回退
成键名本身（「新建 Agent」按钮曾显示 ``rail.newAgent``）。这里只断言 HTTP 外部可观察行为：
响应头与状态码，不读静态文件内容、不 import 实现内部名。

工作台根路径从 ``/`` 的跳转取得，因此本文件不需要自己拼服务端静态目录的布局。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

CACHE_CONTROL = "no-cache"


def _studio_root(client: TestClient) -> str:
    """``/`` 重定向到的静态资源根（``/studio/``）；测试只依赖这个对外入口。"""
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    return response.headers["location"]


def test_studio_static_responses_ask_the_browser_to_revalidate(client: TestClient) -> None:
    root = _studio_root(client)

    for path in (root, root + "i18n.js", root + "app.js"):
        response = client.get(path)

        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == CACHE_CONTROL, (path, dict(response.headers))


def test_studio_static_revalidation_keeps_304_and_carries_no_cache(client: TestClient) -> None:
    root = _studio_root(client)

    first = client.get(root + "i18n.js")
    assert first.status_code == 200
    etag = first.headers["etag"]

    second = client.get(root + "i18n.js", headers={"if-none-match": etag})

    assert second.status_code == 304
    assert second.headers.get("cache-control") == CACHE_CONTROL
    assert second.headers.get("etag") == etag
