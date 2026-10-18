## Summary

`/studio` 的静态响应补 `Cache-Control: no-cache`：浏览器每次回源校验，ETag / Last-Modified 与 304 语义不变。

```diff
 app/alibot/api.py
+class NoCacheStaticFiles(StaticFiles)
+  get_response → response.headers["Cache-Control"] = "no-cache"
+  # 覆盖的是 StaticFiles 已经构造好的响应（含 304），所以两条腿都带头
- app.mount("/studio", StaticFiles(directory=studio_dir(), html=True))
+ app.mount("/studio", NoCacheStaticFiles(directory=studio_dir(), html=True))

 app/tests/test_studio_static_cache.py（新增）
+ 200：/studio/、i18n.js、app.js 三条响应都带 cache-control: no-cache
+ If-None-Match（上一响应 ETag）→ 304，且 304 同样带 cache-control: no-cache
```

根因：`/studio` 原先只有 ETag / Last-Modified。浏览器按启发式缓存可以保留旧的 `i18n.js` 而拿到新的
`index.html`；新 HTML 引用的 `rail.newAgent` 在旧词条表里不存在，`t()` 按设计回退成键名本身——
左栏「新建 Agent」因此显示成 `rail.newAgent`。`no-cache` 只禁止不经校验复用，内容未变时仍是 304。

## Evidence

- **Before:** 基线 worktree（`de3acb5`）跑新用例：`2 failed`，失败行
  `assert None == 'no-cache'`（200 与 304 两条都缺 `cache-control`）。
  **After:** 同一用例：`2 passed`；真实 uvicorn 上 `GET /studio/i18n.js` 返回
  `cache-control: no-cache`，带 ETag 的条件请求返回 `304` + `cache-control: no-cache`。
- 全量门禁 `./scripts/check.sh`：pytest `655 passed, 2 skipped`；ruff / ruff format --check /
  mypy / pyright 全绿；`node --test` `132 pass / 0 fail`。
- 诊断装置（一次性，未入仓）：把 `index.html` 的 `data-i18n="rail.newAgent"` 交给真实的
  `applyStaticI18n()`——旧表得到 `"rail.newAgent"`，当前表得到 `"新建 Agent"`，复现用户症状。

## Merge Danger

**Door:** two-way

只加一条响应头，回滚即恢复旧行为；静态文件内容与 API 契约都未动。

**Blast Radius:** 窄——仅 `/studio` 静态挂载的响应头。

`/design` 挂载、`i18n.js` / `index.html` 内容、公共 JSON API 均未改。浏览器端代价是每次页面加载对
`/studio/*` 多一次条件请求（通常 304，无正文）。
