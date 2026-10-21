#!/usr/bin/env bash
# 把 AgentScope 文档站**当前 pin 版本**的中文页抓成本地快照（判据：`docs/agents/agentscope-2.md` §2）。
#
# 为什么要快照：
#   1. 开发时不能假设联网；联网抓一次 59 页也不是每票都该付的成本。
#   2. 文档站有 `/latest/` 与 `/stable/` 两个会动的别名；本仓只认 **pin 的版本号**。
#      `llms.txt` 自己也写了：/latest/ 可能跑到 dev 版，不要混用 1.x / 2.x。
#   3. review 时需要能把「文档原话」贴到 PR 里，而不是贴一个未来可能变成别的内容的链接。
#
# 用法：
#   scripts/sync-agentscope-docs.sh              # 按 app/pyproject.toml 的 pin 抓取
#   scripts/sync-agentscope-docs.sh --check      # 离线校验：MANIFEST 与本地文件、pin 三者一致
#   scripts/sync-agentscope-docs.sh --version 2.0.10
#
# 退出码：0 = 抓取成功 / 校验通过；非 0 = 网络失败、页面数不符、或哈希漂移。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PIN="$(sed -n 's/.*"agentscope==\([0-9][0-9.]*\)".*/\1/p' app/pyproject.toml | head -1)"
[[ -n "$PIN" ]] || { echo "❌ app/pyproject.toml 里找不到 agentscope==<版本> 的精确 pin" >&2; exit 1; }

VERSION="$PIN"
MODE="sync"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --version) VERSION="${2:?--version 需要一个版本号}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -euo pipefail$/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

# 解释器：与 scripts/check.sh 同源（CHECK_PYTHON 覆盖），没有 .venv 时退回 python3。
PY="${CHECK_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"; else PY="python3"; fi
fi

DEST="docs/agentscope/$VERSION"
MANIFEST="$DEST/MANIFEST.json"
INDEX="https://docs.agentscope.io/_llms/zh/agent-scope/${VERSION//./-}.txt"

if [[ "$MODE" == "check" ]]; then
  "$PY" - "$ROOT" "$PIN" "$VERSION" <<'PY'
import json, sys, hashlib, pathlib
root, pin, version = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
dest = root / "docs" / "agentscope" / version
manifest = dest / "MANIFEST.json"
if not manifest.exists():
    sys.exit(f"❌ 缺少快照清单：{manifest.relative_to(root)}（先跑 scripts/sync-agentscope-docs.sh）")
data = json.loads(manifest.read_text(encoding="utf-8"))
problems = []
if data.get("version") != pin:
    problems.append(f"快照版本 {data.get('version')} ≠ pin {pin}")
if data.get("version") != version:
    problems.append(f"快照版本 {data.get('version')} ≠ 本次校验版本 {version}")
pages = data.get("pages", {})
for rel, sha in pages.items():
    p = dest / rel
    if not p.exists():
        problems.append(f"缺页：{rel}")
        continue
    if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
        problems.append(f"内容漂移（哈希不符）：{rel}")
actual = {str(p.relative_to(dest)) for p in dest.rglob("*.md")}
extra = actual - set(pages)
if extra:
    problems.append(f"清单外的页：{sorted(extra)[:5]}")
if problems:
    print("\n".join("❌ " + p for p in problems))
    sys.exit(1)
print(f"✅ 快照一致：{version}，{len(pages)} 页，pin={pin}")
PY
  exit $?
fi

echo "→ 抓取 AgentScope $VERSION 中文页清单：$INDEX"
INDEX_TMP="$(mktemp)"
trap 'rm -f "$INDEX_TMP"' EXIT
curl -fsSL "$INDEX" -o "$INDEX_TMP" || { echo "❌ 索引抓取失败（网络？）" >&2; exit 1; }

mapfile -t URLS < <(grep -o "https://docs.agentscope.io/zh/versions/${VERSION}/[^)]*\.md" "$INDEX_TMP" | sort -u)
COUNT="${#URLS[@]}"
[[ "$COUNT" -ge 50 ]] || { echo "❌ 只解析到 $COUNT 页，索引结构可能变了" >&2; exit 1; }

rm -rf "$DEST"
mkdir -p "$DEST"
cp "$INDEX_TMP" "$DEST/_INDEX.txt"

# 文档站偶发 502（实测一次抓取里出现过），所以：4 路并发 + 失败重试（3 轮、指数退避）。
# 每页写成 `<out>.tmp` 再 mv，避免半截文件被当成"抓到了"。
fetch_one() {
  local url="$1" rel out
  rel="${url#https://docs.agentscope.io/zh/versions/${VERSION}/}"
  out="$DEST/$rel"
  mkdir -p "$(dirname "$out")"
  curl -fsSL --retry 2 --retry-delay 1 --max-time 60 "$url" -o "$out.tmp" && mv "$out.tmp" "$out"
}
export -f fetch_one
export DEST VERSION

printf '%s\n' "${URLS[@]}" | xargs -P 4 -I{} bash -c 'fetch_one "$@"' _ {} || true

for round in 1 2 3; do
  missing=()
  for url in "${URLS[@]}"; do
    rel="${url#https://docs.agentscope.io/zh/versions/${VERSION}/}"
    [[ -s "$DEST/$rel" ]] || missing+=("$url")
  done
  [[ ${#missing[@]} -eq 0 ]] && break
  echo "→ 第 $round 轮补抓 ${#missing[@]} 页"
  sleep $((round * 2))
  for url in "${missing[@]}"; do fetch_one "$url" || true; done
done

missing=()
for url in "${URLS[@]}"; do
  rel="${url#https://docs.agentscope.io/zh/versions/${VERSION}/}"
  [[ -s "$DEST/$rel" ]] || missing+=("$url")
done
[[ ${#missing[@]} -eq 0 ]] || { echo "❌ 仍有 ${#missing[@]} 页抓不到：${missing[*]}" >&2; exit 1; }
echo "→ 已抓 ${#URLS[@]} 页"

"$PY" - "$DEST" "$VERSION" "$COUNT" <<'PY'
import hashlib, json, pathlib, sys, datetime
dest, version, count = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
pages = {}
for p in sorted(dest.rglob("*.md")):
    pages[str(p.relative_to(dest))] = hashlib.sha256(p.read_bytes()).hexdigest()
manifest = {
    "version": version,
    "source": f"https://docs.agentscope.io/zh/versions/{version}/",
    "index": "https://docs.agentscope.io/_llms/zh/agent-scope/" + version.replace(".", "-") + ".txt",
    "fetched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "page_count": len(pages),
    "pages": pages,
}
(dest / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
assert len(pages) == count, f"清单 {len(pages)} 页 ≠ 索引 {count} 页"
print(f"✅ 快照完成：{len(pages)} 页 → {dest}")
PY
