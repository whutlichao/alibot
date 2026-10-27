"""工作台静态资源的缓存契约：每次回源校验，内容未变时仍是 304。

背景：静态文件原先只发 ETag / Last-Modified，没有 ``Cache-Control``。浏览器按启发式缓存
可以保留旧的 ``i18n.js`` 而拿到新的 ``index.html``；新 HTML 里的词条键在旧表里查不到，界面就回退
成键名本身（「新建 Agent」按钮曾显示 ``rail.newAgent``）。

**对象随票 #318 换成 v2**（ADR-0039）：旧页面退役后，工作台入口是 ``/studio/v2/`` 的构建产物，
而四份正本资产仍从 ``/studio/`` 提供。缓存契约对**两处**都成立，因此这里两处都断言：

- ``/studio/v2/`` 下的产物（``index.html`` 与构建出的 assets）；
- ``/studio/`` 下的四份正本（v2 页面按绝对路径加载它们，词条表尤其不能缓存旧版本）。

这里只断言 HTTP 外部可观察行为：响应头与状态码，不读静态文件内容、不 import 实现内部名。
工作台根路径从 ``/`` 的跳转取得，因此本文件不需要自己拼服务端静态目录的布局。
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

CACHE_CONTROL = "no-cache"


def _studio_root(client: TestClient) -> str:
    """``/`` 重定向到的静态资源根（``/studio/v2/``）；测试只依赖这个对外入口。"""
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    return response.headers["location"]


def _built_asset(client: TestClient, root: str) -> str:
    """产物里那份带 hash 的模块脚本（从 v2 首页读出来，不猜文件名）。"""
    html = client.get(root).text
    match = re.search(r'/studio/v2/(assets/[^"]+\.js)', html)
    assert match is not None, "v2 首页里没有模块脚本引用"
    return match.group(1)


def test_studio_static_responses_ask_the_browser_to_revalidate(client: TestClient) -> None:
    root = _studio_root(client)

    # 产物根 + 产物里的模块脚本 + 正本资产（正本住在 `/studio/`，不在 `v2/` 里）。
    for path in (
        root,
        root + _built_asset(client, root),
        "/studio/i18n.js",
        "/studio/app.css",
    ):
        response = client.get(path)

        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == CACHE_CONTROL, (path, dict(response.headers))


def test_studio_static_revalidation_keeps_304_and_carries_no_cache(client: TestClient) -> None:
    root = _studio_root(client)
    asset = root + _built_asset(client, root)

    first = client.get(asset)
    assert first.status_code == 200
    etag = first.headers["etag"]

    second = client.get(asset, headers={"if-none-match": etag})

    assert second.status_code == 304
    assert second.headers.get("cache-control") == CACHE_CONTROL
    assert second.headers.get("etag") == etag


def test_the_shared_source_assets_are_cached_the_same_way(client: TestClient) -> None:
    """四份正本资产与产物同一条缓存口径——词条表拿到旧版本，界面就回退成键名本身。"""
    for asset in ("i18n.js", "markdown.js", "app.css", "product-tokens.css"):
        path = f"/studio/{asset}"
        first = client.get(path)
        assert first.status_code == 200, path
        assert first.headers.get("cache-control") == CACHE_CONTROL, path

        second = client.get(path, headers={"if-none-match": first.headers["etag"]})
        assert second.status_code == 304, path
        assert second.headers.get("cache-control") == CACHE_CONTROL, path
