# 实施与门禁（多票实施约定）

本文件是**多票实施 run** 的操作约定：门禁入口、环境坑、schema 号段、测试分层、worktree 与合并、
交付形态。它有指针性质——具体行为判据仍以 `app/README.md`、`CONTEXT.md`、`docs/adr/**` 为准；
这里只记「每轮都会重新踩一遍」的操作事实。

## 门禁只有一个入口

```sh
scripts/check.sh            # 全量：pytest + ruff check + ruff format --check + mypy + pyright + node --test（glob）
scripts/check.sh --fast     # 开发循环：除全量 pytest 外的一切（秒级）
scripts/check.sh --fast tests/test_send_message.py   # 再补跑指定 pytest（路径**相对 app/**）
scripts/check.sh --studio   # Studio 窄档：--fast + 审批 HTTP 契约 + 前端静态守卫（不跑全量 pytest）
```

- **Studio 窄档只用于「diff 只落在 `app/alibot/studio/**` 与 `app/tests/*.test.cjs`」的票**：
  它仍跑 ruff / mypy / pyright / node glob，外加审批 HTTP 契约与三条前端静态守卫；CI 依旧跑全量。
  动到 `.py` / schema / 构建配置时用全量，不用窄档。

- CI（`.github/workflows/check.yml`）跑的就是同一个脚本：本地与 CI 不维护第二份命令清单。
- **本地全量只跑一次**：开发循环用 `--fast` / 指定文件；代码定稿时跑一次 `scripts/check.sh`
  并留存读数，PR 的 CI 再跑同一脚本复核。代码未再变化的 docs-only commit 不重跑本地全量；
  同一份代码不在本机重复跑全量。
- **Node 用例族一律走 glob**（`app/tests/*.test.cjs`）：新增一个 DOM 用例文件不需要改任何清单。
  曾经把 5 个文件名抄进票面门禁，同一轮新增的 3 套前端用例就不在门禁里了。
- **不要在命令行再补 `-q`**：`app/pyproject.toml` 已有 `addopts = "-q"`，再补一次变成 `-qq`，
  pytest 会连结果行一起吞掉（「跑了五分钟却看不到 passed 数」就是这么来的）。
- pyright 不在 repo 依赖里：脚本按 `PYRIGHT_BIN` → `node_modules/.bin/pyright` → PATH →
  `~/.pi-lens/tools/node_modules/pyright/index.js` 依次解析；CI 用 `pip install pyright`。
- **不要跑 `ruff format`**：它会整文件重排，把一次小改动变成不可 review 的大 diff。
  只跑 `ruff format --check`（脚本已含）。

## 红证据（隔离基线）

修复类 / 新行为票要先在**隔离的基线副本**上留下失败读数，再去实现。手工复制容易漏文件，也容易
把工作区改动混进基线；用脚本：

```sh
scripts/red-evidence.sh <base-rev> app/tests/approval_badge_ui.test.cjs
```

它用 `git archive` 把 `<base-rev>` 的 `app/alibot/studio` 与 `app/tests` 导出到临时目录，再覆盖
工作区里指定的测试文件，最后跑 `node --test`。脚本会打印证据目录与基线文件的 md5 对照；基线
目录**不自动删除**，便于把读数贴进验收文档。

## AgentScope 的版本与守卫

- 判据正本是 `docs/agents/agentscope-2.md`（红线在 §1）；机械守卫是
  `app/tests/test_agentscope_usage_guard.py`，随 pytest 自动跑，不额外加命令。
- **升级 `agentscope` pin 是一张单独的票**：改 pin → 跑 `scripts/sync-agentscope-docs.sh` 重抓快照 →
  重核对 `docs/agents/agentscope-2-audit.md` 里标「EXP / 实测」的行 → 更新偏离账本的触发条件。
- 快照（`docs/agentscope/<版本>/`）**不许手改**：守卫按 `MANIFEST.json` 的 sha256 校验，改了必红。
- 文档站偶发 502，同步脚本已内置 4 路并发 + 3 轮补抓；仍失败就重跑，不要删页凑数。

## 环境

- 虚拟环境在**仓库根** `.venv/`；新 worktree 用与层级无关的符号链接接上（Orca 的 worktree 不在
  仓库同级，`../alibot/.venv` 会指错）：

  ```sh
  ln -s "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.venv" .venv
  ```

  `scripts/check.sh` 遇到「`.venv` 存在但 `bin/python` 不可执行」会直接报错，不再静默回退到
  `python3`（缺依赖的假红比一条明确的链接错误更难查）。
- pytest 从 `app/` 跑（`testpaths = ["tests"]`）；**mypy 从仓库根跑**——`mypy.ini` 的
  `files = app/alibot, app/tests` 按 cwd 解析，在 `app/` 下跑会报
  `Duplicate module named "__main__"`（假象，不是回归）。
- `GIT_CONFIG_COUNT` 设了却没有对应 key 时，**所有** git/gh 命令报
  `error: missing config key GIT_CONFIG_KEY_0`。这是 Orca Terminal Host 注入的残缺变量；
  `scripts/check.sh` 检测到会提示并 `unset`，手工命令前也先
  `unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1`。
- 代理噪声：宿主 `NO_PROXY` 里带方括号的 IPv6 条目（如 `[::1]`）会让 httpx 把回环请求解析成
  `InvalidURL: Invalid port: ':1]'`。`app/tests/conftest.py` 有一个 autouse fixture 把这类条目
  摘掉，所以**直接跑 pytest 就该绿**；如果你在别的语言栈里遇到同类报错，先查宿主环境。

- `AGENTS.md` 末节维护一份更短的本机噪声清单；改 `.github/workflows/**` 时注意 `gh` token 缺 `workflow` scope（用 SSH 推，见那里）。

## schema 号段（ADR-0015）

- 号段判据是「**必须高于当前 main**」，不是「等于被分配的预留号」。派发时写的号过时是常态，
  以 `app/alibot/db.py` 的 `SCHEMA_VERSION` 为准。
- 每次 bump 必须随票携带 `MigrationStep(from, to)`（严格 +1）、`app/tests/_migration_history.py`
  留档、链保持连续（`app/tests/test_migration_chain_guard.py` 用真实子进程验）。
- **只前进、不回填旧数据**：新列可空 + 读路径解释旧值，而不是造数据。
- 别在**别票**的测试里把版本号钉成固定值（历史上 `== 6`、`== 13` 都发生过，每次 bump 都要回头
  改别人的测试）。改成 `>=` 或从 `SCHEMA_VERSION` 派生——`test_schema_version_pins_guard.py`
  会拦两种钉死形态。

## 测试分层

- pytest 只断言**外部可观察行为**（HTTP / SSE / 库级 CHECK 的对外结果 / 重启一致性），
  不 import 私有名、不读 Studio 源码、不断言状态词表的形状：
  `app/tests/test_test_layering_guard.py` 是机械判据（Studio 读的 i18n 例外在文件里列明）。
- Studio 的 DOM 行为由 `node --test app/tests/*.test.cjs` 覆盖，沿用既有 `vm` + fetch 替身 + DOM
  替身；**不新开第二套前端测试框架**。每个用例文件只放断言；共用装置放
  `app/tests/approval_dom_harness.cjs` 这类非 `*.test.cjs` 的辅助文件。
- 浏览器探针优先按 `data-od-id` 定位（左栏频道 `channel-<id>`、卡片 `task-card-<id>`…），不要用
  纯文本选择器：同一个名字可能同时出现在左栏与看板筛选 chip，会命中两个元素。
- Agent 行为由 `app/tests/scripted.py` 的脚本化替身固定（回应 / 忽略 / 失败 / 延迟 / 声明沉默）；
  **不新开 seam**——票面要求核实某项能力（例如 provider 真的收到工具调用）时，才允许在既有
  用例族里加进程内 stub，并在验收文档里写明理由。
- 文档是数据契约的缓存：新增 `round_outcome` 状态 / 消息 kind 时，`app/README.md` 的词表要一起改
  （`test_docs_vocabulary_guard.py` 会拦漏词）。

## worktree 与合并

- 一票一个 worktree、一个分支（`issue-<n>-impl`），都从集成分支切出；implementer 收尾前把
  集成分支 tip **merge 进自己分支**再报告。
- 合并固定 `--no-ff`，并**立刻推送**：

  ```sh
  scripts/merge-ticket.sh issue-53-impl 53
  ```

  合并后推送这条不是洁癖：真实发生过五张实施 worktree 连同分支在运行中被外部删除，而集成
  分支当时一次都没 push 过——只剩主仓对象库里的悬空对象可以救。推送之后，同样的清理只是
  「重开一个 worktree」。
- 双轴 code-review 的收口单独开一个分支（例如 `issue-review-fix`），合回集成分支后同样立刻推送。

## 交付

- 每票落 `app/specs/issue-<n>-acceptance.md`：判据 → 落点 → 命令与读数。
- 一张集成 PR：先 `--draft`，全部完成后转 ready；正文按 `pr` 技能的 Summary / Evidence /
  Merge Danger 三段写，并用 `Closes #<n>` 逐票挂上。
- 主工作副本不动：所有改动都在各自 worktree 的分支上，经集成分支与 PR 交付。

## 子代理等待

不要用 `sleep` 轮询子代理（一轮 PR 上这样做会烧掉几十分钟墙钟）。让 implementer 把进度写到
自己的报告文件，协调者在自然检查点读文件；子代理 settle 时会有通知，等通知即可。
