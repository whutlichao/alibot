# alibot

<!-- CODEGRAPH_START -->

## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.

<!-- CODEGRAPH_END -->

<!-- BROWSER_START -->

## 浏览器操作/测试（ego-browser 优先）

需要进行浏览器测试、页面操作、自动化验证时，**优先使用 ego-browser skill（ego lite）**，不要默认使用 Playwright：

- 用法：`skill("ego-browser")` 加载后，通过 Bash 运行 `ego-browser nodejs <<'EOF' ... EOF` heredoc 驱动真实浏览器。
- 适用：打开页面、点击/输入、截图观察、`js()` 注入探针做前端行为验证（如监测重渲染/滚动跳变）、网络抓取等。
- 仅当 ego-browser 不可用（未安装且安装失败）或任务明确要求 Playwright 特有能力时才回退到 Playwright。
  <!-- BROWSER_END -->

<!-- LANGUAGE_START -->

## Language

- 使用中文进行交流。
- 文档除了指定结构外，正文尽量使用中文
  <!-- LANGUAGE_START -->

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues, driven by the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary; label strings equal role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: glossary in `CONTEXT.md` (section `## Language`), ADRs under `docs/adr/`. See `docs/agents/domain.md`.

