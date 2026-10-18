# 验收记录：`/studio` 静态资源每次回源校验（修复左栏回退成 `rail.newAgent`）

**票型**：实施票（缺陷修复 + 回归测试）｜**门禁**：G-impl（G-docs / G-spec / G-tickets 不适用）
**Ticket**：本票**没有** GitHub issue，也不新建；交付件用描述性命名，PR 不写 issue 关联
（沿 `app/specs/fix-dock-fallback-no-channel-acceptance.md` 先例）。
**base**：`de3acb5`（= 派活时 `main`）｜**branch**：`studio-static-cache`
**代码提交**：`a15d32d`（修复 + 回归用例）｜本文件与 PR 正文随后续提交入 PR
**PR**：[#165](https://github.com/whutlichao/alibot/pull/165)（base `main`，head `studio-static-cache`）
｜**PR body**：`app/specs/fix-studio-static-cache-pr-body.md`
**采集时间**：2026-10-18｜**环境**：macOS（aarch64）、Python 3.11.13（仓库根 `.venv`）、Node v25.0.0
**改动面**：`app/alibot/api.py`（+17 / −1）、`app/tests/test_studio_static_cache.py`（新增，2 条用例）

---

## 0. 前置 skill：`diagnosing-bugs` 六阶段实跑

| Phase | 本票实际做的事 |
| --- | --- |
| 1 构造反馈回路 | **症状级（一次性，未入仓）**：`/tmp/static-cache-symptom.cjs` 用 `vm` 装载旧/新 `i18n.js`，把 `index.html` 的 `data-i18n="rail.newAgent"` 节点交给真实的 `applyStaticI18n()`。旧表输出 `"rail.newAgent"`、当前表输出 `"新建 Agent"`——复现用户截图里的确切症状。**回归级（入仓）**：`pytest app/tests/test_studio_static_cache.py`，断言 `/studio/` 与资源响应必须带 `Cache-Control: no-cache`、条件请求仍 304。单文件 0.09–0.17s，无网络、无时间依赖。 |
| 1 收紧回路 | 回归用例只读 HTTP 状态码与响应头；工作室根路径来自 `/` 的跳转，不拼内部目录；`app/tests/conftest.py` 的代理清理夹具让回环请求与环境无关。 |
| 2 复现 + 最小化 | 症状回路只保留两个元素：真实的 `applyStaticI18n()` 与**缺 `rail.newAgent` 的旧词条表**。砍掉任一元素即不再出现键名（新表直接给出「新建 Agent」）。回归回路只保留「响应头」与「ETag→304」两个外部事实。 |
| 3 假设（先列后验） | H1 磁盘 `i18n.js` 仍缺词条 ⇒ 预测当前文件 grep 不到；**否**（当前 2 处，PR #161 已加）。H2 新 HTML + 旧 JS 是触发组合 ⇒ 预测旧表跑真实 `applyStaticI18n()` 得到键名；**成立**（§1.2）。H3 服务端缺 `Cache-Control` 让启发式缓存保留旧 JS ⇒ 预测新用例红在缺头；**成立**（§4）。H4 Service Worker 钉住旧 JS ⇒ 预测仓库有注册；**否**（`grep -rn serviceWorker app/alibot/studio/` = 0 行）。H5 服务端返回了错误/过期的正文 ⇒ 预测响应正文与磁盘不一致；**否**（响应 ETag 由磁盘 `stat` 生成，正文即当前 `i18n.js`，且症状在新表下消失）。 |
| 4 仪器化 | 探针 = 基线 worktree 上的 HTTP 响应头（200/304）与一次性症状回路的两行输出；一次只改一个变量（先只加响应头，静态文件内容与挂载语义一律不动）。无 `[DEBUG-...]` 插桩。 |
| 5 修复 + 回归测试 | 先写用例并在基线 worktree 看红（§4），再加 `NoCacheStaticFiles` 看绿，最后回真实 HTTP 复跑（§5）。 |
| 6 清理 | 一次性症状脚本只在 `/tmp`；基线 worktree 用后移除；无临时代码进仓。 |

---

## 1. 根因（自行复证）

### 1.1 提交链

```sh
$ git show --stat --oneline 86725cf
86725cf fix(studio): equalize rail create buttons with visible labels
 app/alibot/studio/app.css    | 1 +
 app/alibot/studio/i18n.js    | 2 ++
 app/alibot/studio/index.html | 7 +++++--
 app/tests/search_ui.test.cjs | 7 +++++--

$ git show 86725cf^:app/alibot/studio/i18n.js | grep -n "rail.newAgent"
51:    'rail.newAgentTitle': '新建 Agent',
543:    'rail.newAgentTitle': 'New agent',
# ← 改动前的词条表只有 rail.newAgentTitle，没有 rail.newAgent

$ git show 86725cf:app/alibot/studio/i18n.js | grep -n "rail.newAgent"
51:    'rail.newAgent': '新建 Agent',
52:    'rail.newAgentTitle': '新建 Agent',
544:    'rail.newAgent': 'New agent',
545:    'rail.newAgentTitle': 'New agent'

$ grep -n "rail.newAgent" app/alibot/studio/index.html
111:        <span data-i18n="rail.newAgent">新建 Agent</span>
# ← 新 HTML 引用了新键；旧 HTML 没有这个节点
```

`i18n.js` 的 `t()` 按设计「当前语言 → 源语言 → 键名本身」回退。于是「新 `index.html` + 缓存里的旧
`i18n.js`」这一对组合会把按钮文案渲染成 `rail.newAgent`。服务端磁盘文件现在是自洽的，问题在于
`/studio` 静态响应原先只有 ETag / Last-Modified，没有 `Cache-Control`，浏览器可以按启发式缓存
长期保留旧 JS。

### 1.2 症状级复现（真实 `applyStaticI18n()`，一次性装置）

```sh
$ node /tmp/static-cache-symptom.cjs
old-i18n label: "rail.newAgent"
new-i18n label: "新建 Agent"
```

---

## 2. 修复

```diff
 app/alibot/api.py
+class NoCacheStaticFiles(StaticFiles):
+    async def get_response(self, path: str, scope: Scope) -> Response:
+        response = await super().get_response(path, scope)
+        response.headers["Cache-Control"] = "no-cache"
+        return response
...
-    app.mount("/studio", StaticFiles(directory=str(studio_dir()), html=True), name="studio")
+    app.mount("/studio", NoCacheStaticFiles(directory=str(studio_dir()), html=True), name="studio")
```

- 只改 `/studio` 的挂载；`/design` 仍是原 `StaticFiles`，`i18n.js` / `index.html` 内容一字未动。
- `no-cache` 只禁止**不经校验**复用；`file_response` 先按 ETag / Last-Modified 判 304，再由本钩子
  给返回对象（200 或 304）补头，因此两条腿都带 `Cache-Control`，正文不重复传输。
- `html=True` 的目录索引、Content-Type、相对路径回退逻辑全部由父类原样执行。

---

## 3. 回归用例（`app/tests/test_studio_static_cache.py`，2 条）

| # | 用例 | 断言的外部可观察事实 |
| --- | --- | --- |
| 1 | `test_studio_static_responses_ask_the_browser_to_revalidate` | `/studio/`、`/studio/i18n.js`、`/studio/app.js` 三条 200 响应都带 `cache-control: no-cache` |
| 2 | `test_studio_static_revalidation_keeps_304_and_carries_no_cache` | 首响应 ETag 作为 `If-None-Match` ⇒ 304；304 同样带 `cache-control: no-cache`，ETag 原样保留 |

只经 `TestClient` 断言状态码与响应头，不 import `alibot.*`、不读静态文件内容；`studio` 只出现在文档串里，
`app/tests/test_test_layering_guard.py` 通过。工作室根路径取自 `/` 的 302，测试不硬编码服务端目录布局。

---

## 4. 红 → 绿证据

**红证据在基线 worktree 取得**（不改本 worktree 已提交的工作）：

```sh
$ git worktree add --detach /tmp/static-cache-base de3acb5
$ ln -s /Users/apple/orca/alibot/.venv /tmp/static-cache-base/.venv
$ cp app/tests/test_studio_static_cache.py /tmp/static-cache-base/app/tests/
$ cd /tmp/static-cache-base/app && ../.venv/bin/python -m pytest tests/test_studio_static_cache.py
```

关键输出（基线 `de3acb5`，修复前）：

```text
E           AssertionError: ('/studio/', {'content-type': 'text/html; charset=utf-8', ..., 'etag': '"9452926cc0abebc5d781c42cd76e0439"'})
E           assert None == 'no-cache'
E            +  where None = get('cache-control')
...
E       assert None == 'no-cache'
E        +  where None = get('cache-control')
E        +    where get = Headers({'etag': '"d1e377f78533a8fed50a09bad1af17d7"'}).get
FAILED tests/test_studio_static_cache.py::test_studio_static_responses_ask_the_browser_to_revalidate
FAILED tests/test_studio_static_cache.py::test_studio_static_revalidation_keeps_304_and_carries_no_cache
2 failed in 0.17s
```

修复后（本 worktree，提交 `a15d32d`）：

```sh
$ cd app && ../.venv/bin/python -m pytest tests/test_studio_static_cache.py
2 passed in 0.09s
```

---

## 5. 真实 HTTP 冒烟（uvicorn，127.0.0.1:8799，临时数据目录）

```text
GET /studio/
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
last-modified: Sun, 18 Oct 2026 03:04:30 GMT
etag: "c1809c2199a0da5702d197efc9d00f56"
cache-control: no-cache

GET /studio/i18n.js
HTTP/1.1 200 OK
content-type: text/javascript; charset=utf-8
etag: "6009900975830347fabd8af8b2d862d7"
cache-control: no-cache

GET /studio/i18n.js  (If-None-Match: "6009900975830347fabd8af8b2d862d7")
HTTP/1.1 304 Not Modified
etag: "6009900975830347fabd8af8b2d862d7"
cache-control: no-cache

GET /design/alibot-app.html      # 未改动的对照面
HTTP/1.1 200 OK
etag: "846a47c0b677227c2297939f0de28892"   # 无 cache-control，与改前一致
```

---

## 6. 全量门禁（`./scripts/check.sh`，最终代码 `a15d32d`）

```text
== pytest（全量）
655 passed, 2 skipped in 316.06s (0:05:16)

== ruff check
All checks passed!

== ruff format --check
139 files already formatted

== mypy（仓库根）
Success: no issues found in 62 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test
ℹ tests 132
ℹ pass 132
ℹ fail 0

门禁全绿
exit=0
```

本地全量门禁在本票 diff 上无既存失败；CI 合并态的既存失败另见 §6.1。

### 6.1 CI（GitHub Actions，PR #165）

PR 的 `pull_request` run 在本 worktree 之外执行同一脚本。pytest 侧与本地一致：

```text
655 passed, 2 skipped in 336.13s (0:05:36)
== ruff check
All checks passed!
```

随后 `ruff format --check` 红在**本票未触及的** `app/specs/issue-135-research.md`（run
[39093324729](https://github.com/whutlichao/alibot/actions/runs/39093324729)）：

```text
unformatted: File would be reformatted
   --> app/specs/issue-135-research.md:36:50
...
1 file would be reformatted, 148 files already formatted
```

**这是 main 上的既存失败，不是本票 diff 引入的**：

```sh
$ cd /Users/apple/orca/alibot && .venv/bin/python -m ruff format --check app/specs/issue-135-research.md
1 file would be reformatted
# main 自己的 CI run 39093251823（head 71d853d）在同一文件、同一行失败
```

该文件在本票 base `de3acb5` 上不存在（由后续 main 提交引入），因此本地全量门禁在本票 diff 上是全绿；
CI 的合并态（PR head + 当前 main）额外载入了这枚既存失败。按 ownership，本票不改别票文件，也不把
`ruff format` 的整文件重排塞进本 PR。

---

## 7. 双轴 code-review（Standards + Spec）

按 `/Users/apple/.agents/skills/code-review/SKILL.md`，以 `de3acb5` 为固定点并行跑两轴。

### Standards

| finding | 级别 | 处理 |
| --- | --- | --- |
| 新测试文档串用「工作室」，`CONTEXT.md` 的正本术语是「工作台」 | judgement call（术语漂移） | **已修**：提交前改为「工作台」（`a15d32d` 内） |
| `cache-control` 用精确等值断言，语义等价的 `no-cache, must-revalidate` 会红 | judgement call | **保留**：票面判据就是 `no-cache` 这个确切值，仓库同类断言也取精确值 |
| 其他硬性违规 / smell | 无 | — |

### Spec

| finding | 级别 | 处理 |
| --- | --- | --- |
| 缺失或部分实现的需求 | 无 | — |
| 未被要求的行为（scope creep） | 无 | 只换挂载类；`/studio` → `/studio/` 的 307 由路由层产生，未受影响 |
| 看似实现但错误 | 无 | 复核了 304 头的写入顺序：父类先构造 `NotModifiedResponse`，钩子再补头，200/304 均生效 |

**未省略任何一轴**；两轴均无阻断项。

### 观察（本票不改）

`index.html` 还从 `/design` 加载 `colors_and_type.css` / `tokens.css`，`/design` 挂载仍只有启发式缓存。
票面明确把 `/design` 排除在修复范围外，且这两个文件不含词条键，不会造成 `rail.newAgent` 这类回退；
记录为后续票的候选，不在本票扩大影响面。

---

## 8. 交付回填

| 项 | 值 |
| --- | --- |
| 代码提交 | `a15d32d` |
| 文档提交 | `82f531c`（本文件 + PR 正文）；PR 号回填的后续提交 |
| 远端分支 | `origin/studio-static-cache` |
| PR | [#165](https://github.com/whutlichao/alibot/pull/165)（mergeable；CI 红项见 §6.1，属 main 既存失败） |
| PR head | `a6ed4e3`（PR 号回填提交；本文件后续 docs 提交会推进 head） |
| PR body 路径 | `app/specs/fix-studio-static-cache-pr-body.md` |

无关联 GitHub issue，因此 PR 正文不含 `Closes #`。
