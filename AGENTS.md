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
- 需要浏览器时先跑 `ego-browser skill` 取本机版本匹配的用法指南；脚本里的 `ego.helpers.help()` 可查 API 签名（直接调用，不要 `console.log` 包裹）。
- 脚本挂起时先拆成单动作定位：`goto` / `evaluate` / `screenshot` 分开跑（0.5.x 上多动作串一条曾反复挂死，0.6 已复测通过）。
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

### Implementation

Run the gates through `scripts/check.sh` (CI runs the same script); worktree/merge/push discipline and the schema-number rule: see `docs/agents/implementation.md`.

### Code review

Standards sources and the judgement rules the reviewer enforces: see `docs/agents/review-rules.md`.

## 本机已知环境噪声

- `GIT_CONFIG_COUNT` 已设而 `GIT_CONFIG_KEY_0` 缺失 ⇒ 任何 `git` / `gh` 立即 `fatal: missing config key`；`scripts/check.sh` 已内置 `unset`，手工命令前也先 `unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1`。
- `no_proxy` / `NO_PROXY` 含 `[::1]` ⇒ httpx 报 `InvalidURL: Invalid port: ':1]'`；`app/tests/conftest.py` 的 autouse fixture 已把这类条目摘掉（pytest 直接跑即可），只有绕开 pytest 的手工工具才需要 `env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY`。
- `gh` 的 OAuth token 缺 `workflow` scope ⇒ 改动 `.github/workflows/**` 的分支用 HTTPS 推会被拒（`refusing to allow an OAuth App to create or update workflow`）；用 SSH 推同一分支即可（`git push git@github.com:whutlichao/alibot.git <branch>`），或先 `gh auth refresh -s workflow`。
- 仓库 `.venv` 不含 `pyright` ⇒ `scripts/check.sh` 依次找 `PYRIGHT_BIN` → `node_modules/.bin/pyright` → PATH → `~/.pi-lens/tools/node_modules/pyright/index.js`；手工调用时用最后那条，或在别的 worktree 里加 `--pythonpath .venv/bin/python`。

