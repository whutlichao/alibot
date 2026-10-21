#!/usr/bin/env bash
# 在**隔离的基线副本**上跑一个 Studio DOM 用例，取得「修复前失败」的红读数。
#
# 用法：
#   scripts/red-evidence.sh <base-rev> app/tests/approval_badge_ui.test.cjs
#
# 它做的事：
#   1. 用 `git archive` 把 <base-rev> 的 `app/alibot/studio` 与 `app/tests` 导出到临时目录；
#   2. 用**当前工作区**的 `app/tests/**` 覆盖副本里的测试（只换测试，不换产品代码）；
#   3. 跑 `node --test <test-file>`，打印基线 app.js 的 md5 与证据目录。
#
# 证据目录不自动删除：把红读数贴进验收文档后由人清理。脚本不改当前工作区。
# 退出码：0 = 用例通过（全绿）；1 = 有用例失败（红，通常正是想要的结果）；
#         2 = 用法/基线错误。
set -euo pipefail

unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1 2>/dev/null || true

usage() {
  sed -n '2,/^set -euo pipefail$/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -ne 2 ]]; then
  usage
  exit 2
fi

BASE_REF="$1"
TEST_FILE="$2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null; then
  echo "错误：基线 rev 不存在：$BASE_REF" >&2
  exit 2
fi
if [[ ! -f "$TEST_FILE" ]]; then
  echo "错误：测试文件不存在：$TEST_FILE" >&2
  exit 2
fi

TEST_REL="${TEST_FILE#"$ROOT"/}"
case "$TEST_REL" in
  app/tests/*.test.cjs) ;;
  *) echo "错误：测试文件必须在 app/tests/**/*.test.cjs 下：$TEST_REL" >&2; exit 2 ;;
esac

EVIDENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/alibot-red.XXXXXX")"
mkdir -p "$EVIDENCE_DIR/app/alibot" "$EVIDENCE_DIR/app/tests"

git archive "$BASE_REF" app/alibot/studio app/tests | tar -x -C "$EVIDENCE_DIR"
# 只覆盖测试（含 harness 等非 *.test.cjs 的辅助文件）；产品代码保持基线字节。
cp -R app/tests/. "$EVIDENCE_DIR/app/tests/"

md5_of() {
  if command -v md5 >/dev/null 2>&1; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi
}

echo "基线：$BASE_REF ($(git rev-parse --short "$BASE_REF"))"
echo "测试：$TEST_REL（取自当前工作区）"
echo "基线 app.js md5：$(md5_of "$EVIDENCE_DIR/app/alibot/studio/app.js")"
echo "证据目录：$EVIDENCE_DIR"
echo

status=0
node --test "$EVIDENCE_DIR/$TEST_REL" || status=$?

echo
if (( status == 0 )); then
  echo "结果：用例通过（不是红读数；确认基线是否已包含修复）"
else
  echo "结果：用例失败（红读数）；证据目录保留在 $EVIDENCE_DIR"
fi
exit "$status"
