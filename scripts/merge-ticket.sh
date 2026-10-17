#!/usr/bin/env bash
# 把一个实施分支合进 integration 并**立刻推送**（`docs/agents/implementation.md` 的合并纪律）。
#
# 为什么合并后必须推送：工作只活在本地 worktree 里时，一次外部清理就能把它抹掉。
# 真实发生过：五张实施 worktree 连同其中的分支在运行中被外部删除，只有主仓对象库里
# 还留着对象——而当时集成分支**一次都没 push 过**。推送之后，同样的清理从「事故」
# 变成「重开一个 worktree」。
#
# 用法：
#   scripts/merge-ticket.sh issue-53-impl 53
#   scripts/merge-ticket.sh issue-53-impl 53 alibot-budget-silence/integration
#
# 前置：工作树干净；集成分支存在。合并策略固定 `--no-ff`（保留「这一票是哪一个 merge」）。

set -euo pipefail

unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0 GIT_CONFIG_VALUE_1 2>/dev/null || true

BRANCH="${1:-}"
TICKET="${2:-}"
INTEGRATION="${3:-alibot-budget-silence/integration}"

if [[ -z "$BRANCH" || -z "$TICKET" ]]; then
  echo "用法：scripts/merge-ticket.sh <分支> <票号> [集成分支=${INTEGRATION}]" >&2
  exit 2
fi

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "工作树不干净：先提交或 stash（当前分支：$(git branch --show-current)）" >&2
  exit 1
fi

git rev-parse --verify --quiet "$INTEGRATION^{commit}" >/dev/null || {
  echo "集成分支不存在：$INTEGRATION" >&2
  exit 1
}
git rev-parse --verify --quiet "$BRANCH^{commit}" >/dev/null || {
  echo "分支不存在：$BRANCH" >&2
  exit 1
}

echo "合并 $BRANCH → $INTEGRATION（#${TICKET}）"
git merge --no-ff "$BRANCH" -m "merge(#${TICKET}): ${BRANCH}"
git push origin "$INTEGRATION"

printf '\nbranch=%s\nintegration=%s\nsha=%s\nticket=#%s\n' \
  "$BRANCH" "$INTEGRATION" "$(git rev-parse --short HEAD)" "$TICKET"
