#!/usr/bin/env bash
# 门禁的**唯一入口**（判据来源：`docs/agents/implementation.md`）。
#
# 为什么要一个脚本，而不是把命令抄进每张票 / 每份文档：
# 命令清单是**缓存**，缓存会漂移。本仓已经吃过一次：票面门禁里手列了 5 个
# `app/tests/*.test.cjs`，而同一轮新增的 3 套前端用例（`channel_budget_ui` /
# `narration_ui` / `silent_ui`）**不在清单里**——它们只有当有人想起再跑一次 glob 时才会被执行。
# 这里只写一次：Node 用例族一律走 glob，新增文件自动纳入。
#
# 用法：
#   scripts/check.sh                      # 全量门禁（合并 / 开 PR 前；pytest 并行分发）
#   scripts/check.sh --fast               # 除全量 pytest 外的一切（秒级；开发循环）
#   scripts/check.sh --fast tests/test_send_message.py tests/test_budget.py
#                                         # 加跑指定 pytest（路径**相对 app/**，串行）
#   scripts/check.sh --studio             # Studio 窄档：--fast + 审批 HTTP 契约 + 静态守卫
#                                         # （仅限 diff 只落在 app/alibot/studio/**；CI 仍跑全量）
#
# 并行只在**全量**分支：`-n auto --dist loadfile`。`--dist loadfile` 是刻意的——同文件落在同一个
# worker，本仓用例按文件共享装置与顺序假设（本机 `-n 4` 实测与默认 `load` 同速：120s vs 117s，
# 所以选它是因为**语义**不对称，不是因为更快）。
# `--studio` 窄档与 `--fast` 指定文件**保持串行**：单文件并行只会更慢，窄档读数也要能与历史对比。
#
# 前端用例族**只有一套**（Vitest，Vue 版工作台）：`npx vitest run`（app/frontend），Glob 收集。
# 旧页面的 `node --test app/tests/*.test.cjs` 族已随页面一起退役（票 #318 / ADR-0039）。
# 前端产物由本脚本**头部**先 `npm run build` 生成（不再入库）。
#
# 环境变量：
#   CHECK_PYTHON  覆盖解释器（默认 `仓库根/.venv/bin/python`，没有则 `python3`——CI 走这条）
#   PYRIGHT_BIN   覆盖 pyright 入口（默认依次尝试 `node_modules/.bin/pyright`、PATH、
#                 `~/.pi-lens/tools/node_modules/pyright/index.js`）
#   CHECK_FRONTEND_REINSTALL=1  强制 `npm ci` 重装前端依赖（默认只在缺 node_modules 时装）
#
# 退出码：0 = 全绿；非 0 = 第一个失败的门禁（与命令本身的退出码一致）。

set -euo pipefail

# 用法正文 = 文件头注释（第 2 行到 `set -euo pipefail` 之前）：改脚本时不用同步改一段 usage。
usage() {
  sed -n '2,/^set -euo pipefail$/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

# 本机噪声：`GIT_CONFIG_COUNT` 设了却没有对应 key 时，**所有** git/gh 命令会报
# `error: missing config key GIT_CONFIG_KEY_0`（Orca 终端会注入这组残缺变量）。
# 门禁不该因为这个红；顺手提示一次，提醒人手工 git 前也先 unset。
if [[ -n "${GIT_CONFIG_COUNT:-}" || -n "${GIT_CONFIG_VALUE_0:-}" ]]; then
  echo "提示：检测到 Orca 终端注入的残缺 GIT_CONFIG_*；本脚本已 unset，手工 git/gh 前也先 unset。" >&2
fi
unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1 2>/dev/null || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAST=0
STUDIO=0
while (( $# )); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --fast) FAST=1; shift ;;
    --studio) STUDIO=1; FAST=1; shift ;;
    *) break ;;
  esac
done

# ─── 解释器 ────────────────────────────────────────────────────────────────
PY="${CHECK_PYTHON:-}"
if [[ -z "$PY" ]]; then
  # 失效的 worktree `.venv` 链接不能静默回退到 python3：依赖缺失会变成一堆假红。
  # 正确接法（与 worktree 层级无关）：
  #   ln -s "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.venv" .venv
  if [[ -e "$ROOT/.venv" || -L "$ROOT/.venv" ]] && [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "错误：$ROOT/.venv 存在但 .venv/bin/python 不可执行（worktree 符号链接失效？）" >&2
    echo "修复：ln -sfn \"\$(dirname \"\$(git rev-parse --path-format=absolute --git-common-dir)\")/.venv\" .venv" >&2
    exit 1
  fi
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi

# ─── pyright 入口解析（不在 repo 依赖里，按可用性从宽到窄找）────────────────
PYRIGHT_CMD=()
if [[ -n "${PYRIGHT_BIN:-}" ]]; then
  PYRIGHT_CMD=("$PYRIGHT_BIN")
elif [[ -x "$ROOT/node_modules/.bin/pyright" ]]; then
  PYRIGHT_CMD=("$ROOT/node_modules/.bin/pyright")
elif command -v pyright >/dev/null 2>&1; then
  PYRIGHT_CMD=("$(command -v pyright)")
elif [[ -f "$HOME/.pi-lens/tools/node_modules/pyright/index.js" ]]; then
  PYRIGHT_CMD=(node "$HOME/.pi-lens/tools/node_modules/pyright/index.js")
else
  echo "找不到 pyright：设 PYRIGHT_BIN，或 npm i -g pyright（见 docs/agents/implementation.md）" >&2
  exit 1
fi

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

# ─── 前端产物：门禁之前先建 ─────────────────────────────────────────────
# `app/alibot/studio/v2/` **不再入库**（ADR-0039 推翻 ADR-0038 §一），由 `npm run build` 生成。
# 它必须在任何门禁之前就绪，因为有三处要读它：
#   - `app/frontend/tests/build-guard.test.js`（产物存在 + 源码指纹一致）
#   - `app/tests/test_studio_static_cache.py`（/studio/v2/ 的缓存契约）
#   - `app/tests/test_studio_v2_serving.py`（`/` 与 `/studio` 都进 v2）
# 忘了构建时这三处全红，而红的原因与改了什么无关——把构建放在最前面，红就只会是真正的问题。
#
# `env -u NODE_ENV`：宿主 `NODE_ENV=production` 会让 npm 跳过 devDependencies，vite 就装不上。
step "构建工作室产物（app/frontend → app/alibot/studio/v2）"
if [[ ! -d app/frontend/node_modules ]] || [[ "${CHECK_FRONTEND_REINSTALL:-0}" == "1" ]]; then
  ( cd app/frontend && env -u NODE_ENV npm ci --include=dev --no-audit --no-fund )
fi
( cd app/frontend && env -u NODE_ENV npm run build )

# ─── pytest ────────────────────────────────────────────────────────────────
# 必须从 `app/` 跑：`app/pyproject.toml` 的 `testpaths = ["tests"]` 与 `[tool.ruff].src`
# 都按它解析。**不要**在命令行再补 `-q`：配置里已经有 `addopts = "-q"`，再补一次会变成
# `-qq`，pytest 会连结果行一起吞掉（「跑了 5 分钟却看不见 passed 数」就是这么来的）。
if (( STUDIO )); then
  step "pytest（Studio 窄档：审批 HTTP 契约 + 前端相关静态守卫）"
  ( cd app && "$PY" -m pytest "$@" tests/test_approvals_http.py tests/test_docs_vocabulary_guard.py tests/test_test_layering_guard.py tests/test_studio_static_cache.py tests/test_studio_v2_serving.py )
elif (( FAST )); then
  if (( $# )); then
    step "pytest（指定文件）: $*"
    ( cd app && "$PY" -m pytest "$@" )
  else
    step "pytest（--fast 且未指定文件：跳过；需要时把路径跟在 --fast 后面）"
  fi
else
  # 全量才并行。`--dist loadfile` 把同一文件的所有用例派给同一个 worker：本仓用例按文件共享装置
  # 与顺序假设，跨文件打散（默认 `load`）会破坏这个前提——速度上两者在本机实测同档
  # （`-n 4`：loadfile 120.1s / load 117.4s），所以选它是**语义**理由，不是提速。
  # `-n auto` 随机器核数走：本机 8 核、CI runner 4 核，两端都拿到等价形态，不必写死数字。
  #
  # 步骤名**保持** `pytest（全量）`：`app/frontend/tests/build-guard.test.js` 用它定位「构建在
  # pytest 之前」这条接线（`script.indexOf('step "pytest（全量）"')`），改名会让那条守卫假红。
  step "pytest（全量）"
  ( cd app && "$PY" -m pytest -n auto --dist loadfile )
fi

# ─── 静态检查 ──────────────────────────────────────────────────────────────
step "ruff check"
"$PY" -m ruff check app

step "ruff format --check（只检查；不要跑 ruff format，它会整文件重排）"
"$PY" -m ruff format --check app

# mypy 必须从**仓库根**跑：`mypy.ini` 的 `files = app/alibot, app/tests` 是相对 cwd 解析的，
# 在 `app/` 下跑会把两个目录都当成顶层包，报 `Duplicate module named "__main__"`。
step "mypy（仓库根）"
"$PY" -m mypy

step "pyright"
"${PYRIGHT_CMD[@]}" app/alibot app/tests

# ─── 前端（Vue 版工作台：Vitest）──
# 用例在 `app/frontend/tests/*.test.js`，由 `app/frontend/vite.config.js` 的 `test.include`
# 收集（同样是 glob：新增文件自动纳入，不需要改清单）。
#
# 依赖与产物已在文件头部那段建好（`npm ci` 只在缺 node_modules 时跑；`npm run build` 每次都跑），
# 这里不再重装——`build-guard.test.js` 读的就是上面那份产物。
step "Vitest（Vue 版工作台：app/frontend）"
(
  cd app/frontend
  if [[ ! -d node_modules ]] || [[ "${CHECK_FRONTEND_REINSTALL:-0}" == "1" ]]; then
    env -u NODE_ENV npm ci --include=dev --no-audit --no-fund
  fi
  env -u NODE_ENV npx vitest run
)

printf '\n\033[1m门禁全绿\033[0m\n'
