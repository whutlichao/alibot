"""工作台入口：``/`` 与 ``/studio`` 都进 v2，旧页面不再存在（票 #318 / ADR-0039）。

旧页面退役后，`app/alibot/studio/` 里只剩**正本资产**（`i18n.js` / `markdown.js` / `app.css` /
`product-tokens.css`）与构建产物目录 `v2/`。于是「哪个 HTML 是入口」不能再靠目录里恰好只有一份
`index.html` 来表达，必须是一条**判据**：

- ``/`` 与 ``/studio`` / ``/studio/`` 都落到 ``/studio/v2/``；
- 旧路径下的 ``app.js`` 回 404（它被删了，服务端也没有第二条退路）；
- ``/studio/v2/`` 本身就是一份可用页面（含模块脚本与四份正本资产引用）；
- 正本资产仍然从 ``/studio/`` 提供（v2 页面按绝对路径加载它们，删掉任一个就白屏）。

只断言 HTTP 外部可观察行为：状态码、``location``、响应体里的引用。不读服务端静态目录布局。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

V2_ROOT = "/studio/v2/"


def test_root_redirects_into_the_v2_workbench(client: TestClient) -> None:
    """``/`` 的直接落点是 v2 工作台，不是旧页面。"""
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == V2_ROOT


def test_studio_root_redirects_into_the_v2_workbench(client: TestClient) -> None:
    """``/studio`` 与 ``/studio/`` 都进 v2 —— 旧地址是被记住过的，不能变成 404。"""
    for path in ("/studio", "/studio/"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 307), path
        assert response.headers["location"] == V2_ROOT, path


def test_the_v2_root_serves_a_usable_page(client: TestClient) -> None:
    """v2 根路径是页面本身：模块脚本 + 四份正本资产引用都在。"""
    response = client.get(V2_ROOT)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert 'type="module"' in body
    for asset in (
        "/studio/i18n.js",
        "/studio/markdown.js",
        "/studio/app.css",
        "/studio/product-tokens.css",
    ):
        assert asset in body, asset


def test_the_legacy_page_is_gone(client: TestClient) -> None:
    """旧 ``app.js`` 与旧 ``index.html`` 都不再由任何路径提供（删掉的文件不该还有退路）。"""
    assert client.get("/studio/app.js").status_code == 404
    assert client.get("/studio/app.js.map").status_code == 404
    # 旧页面是 `/studio/index.html`；它已随页面一起删除，`/studio/` 现在跳 v2。
    assert client.get("/studio/index.html", follow_redirects=False).status_code in (302, 307, 404)


def test_the_shared_source_assets_are_still_served(client: TestClient) -> None:
    """四个正本资产仍从 ``/studio/`` 提供：v2 页面按绝对路径加载它们，删一个就白屏。"""
    for asset in ("i18n.js", "markdown.js", "app.css", "product-tokens.css"):
        response = client.get(f"/studio/{asset}")
        assert response.status_code == 200, asset
