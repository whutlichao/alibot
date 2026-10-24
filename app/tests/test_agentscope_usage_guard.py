"""纪律守卫之六：AgentScope 2.0 的版本、import 面与文档引用（判据：`docs/agents/agentscope-2.md`）。

为什么要有这层守卫：文档口径已经写在规约里，但「写进文档」不等于「每次改动都记得」。
本仓已有的五次教训（schema 号、词表、迁移链、旧表名、测试分层）都是同一个形状：
**一条能被机械判定的规则，只要没落成测试，就会在某次赶工的 diff 里悄悄破掉。**

本守卫只拦「能机械判」的五件事，不判「某个模块该不该用」——后者是 review 第三轴：

1. **pin 必须是精确版本**，且与本地文档快照的 `MANIFEST.json` 版本一致（规约 §1.1 / §2.3）。
2. **import 面白名单**：只允许已采用的那批 `agentscope.*` 顶层模块；新增要同时改白名单与规约
   §1.3（这不是形式主义：`agentscope.deploy` 会把身份与持久化挪出本应用，正是本仓明令不用的面）。
3. **1.x 遗留符号**：`ReActAgent`、`sys_prompt=`、`agentscope.agents` 一律红（规约 §1.2）。
4. **文档链接必须带 pin 版本**：`/latest/`、`/stable/`、无版本路径一律红（规约 §1.4）。
5. **快照完整性**：`MANIFEST.json` 的版本 / 页数 / 每页 sha256 与 `docs/agentscope/<版本>/` 一致。

失败时必须**一次列全**：只报第一个违规会让人反复跑测试（规约 §1 的报错风格约定）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = "docs/agents/agentscope-2.md"

# 已采用的 agentscope 顶层模块（规约 §1.3）。新增模块 = 改这里 + 规约 + 偏离账本（若属刻意偏离）。
ALLOWED_MODULES = frozenset(
    {
        "agent",
        "credential",
        "event",
        "formatter",
        "mcp",
        "message",
        "middleware",
        "model",
        "permission",
        "pipeline",
        "skill",
        "sop",
        "state",
        "tool",
        "types",
        "workspace",
    }
)

DOC_HOST = "docs.agentscope.io"
SELF = Path(__file__).name
VERSIONED_DOC_RE = re.compile(r"^https://docs\.agentscope\.io/(zh|en)/versions/(\d+\.\d+\.\d+)/")


def _pin(root: Path) -> tuple[str | None, list[str]]:
    """从 pyproject 读 pin，并返回所有不合规的声明（精确 pin、唯一一条）。"""
    pyproject = root / "app" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = [d for d in data.get("project", {}).get("dependencies", []) if d.startswith("agentscope")]
    problems: list[str] = []
    if len(deps) != 1:
        problems.append(f"app/pyproject.toml 里 agentscope 依赖应恰好一条，实际 {deps}")
    # extra 不放松 pin：`agentscope[memory-reme]==2.0.10` 仍是精确版本（ADR-0034 的 ReMe extra）。
    pinned = [d for d in deps if re.fullmatch(r"agentscope(?:\[[A-Za-z0-9_,.-]+\])?==\d+\.\d+\.\d+", d)]
    if not pinned:
        problems.append(
            f"agentscope 必须是精确 pin（如 agentscope==2.0.10 或 agentscope[extra]==2.0.10），实际 {deps}"
        )
    if problems:
        return None, problems
    return pinned[0].split("==", 1)[1], problems


def _imports(root: Path) -> list[tuple[Path, int, str]]:
    """扫描产品与测试代码里的 agentscope 顶层模块引用（AST，不匹配注释与字符串）。"""
    hits: list[tuple[Path, int, str]] = []
    for base in (root / "app" / "alibot", root / "app" / "tests"):
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:  # 语法错误由 ruff/mypy 报，不在这里抢活
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "agentscope":
                        for alias in node.names:  # from agentscope import credential as cred
                            hits.append((path, node.lineno, alias.name))
                    elif node.module.startswith("agentscope."):
                        hits.append((path, node.lineno, node.module.split(".")[1]))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("agentscope."):
                            hits.append((path, node.lineno, alias.name.split(".")[1]))
    return hits


def _legacy_symbols(root: Path) -> list[tuple[Path, int, str]]:
    """按 **AST** 找 1.x 写法：注释与文档字符串里提到 ``ReActAgent`` 不算违规。

    这条区分是必须的——`agent_seam.py` 的类文档字符串正是在解释「**不**使用 1.x 的
    ``ReActAgent(name, sys_prompt, …)``」。按文本 grep 会把它判红，守卫生效的第一天就得关掉。
    """
    hits: list[tuple[Path, int, str]] = []
    for base in (root / "app" / "alibot", root / "app" / "tests"):
        for path in sorted(base.rglob("*.py")):
            if path.name == SELF:  # 守卫自己的正控里有违规样例
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "agentscope.agents" or node.module.startswith("agentscope.agents."):
                        hits.append((path, node.lineno, f"import {node.module}"))
                elif isinstance(node, ast.Call):
                    hits.extend(
                        (path, kw.lineno, "关键字参数 sys_prompt=")
                        for kw in node.keywords
                        if kw.arg == "sys_prompt"
                    )
                elif (isinstance(node, ast.Name) and node.id == "ReActAgent") or (
                    isinstance(node, ast.Attribute) and node.attr == "ReActAgent"
                ):
                    hits.append((path, node.lineno, "ReActAgent"))
    return hits


def _doc_links(root: Path) -> list[tuple[Path, int, str]]:
    """仓库文档里的 AgentScope 链接。

    两处豁免：
    - `docs/agentscope/**` 是**外部原文快照**，里面的相对链接不归本仓管；
    - `app/specs/**` 是**历史验收证据**（写的时候 pin 的是 2.0.9，链接就长那样）。
      本仓纪律是「不能就地改写历史记录」——改这些链接等于伪造当时的证据。
      规约 §1.4 只管**新写**的文档。
    """
    hits: list[tuple[Path, int, str]] = []
    snapshot = (root / "docs" / "agentscope").resolve()
    archive = (root / "app" / "specs").resolve()
    for path in sorted(root.rglob("*.md")):
        resolved = path.resolve()
        if snapshot in resolved.parents or archive in resolved.parents:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for url in re.findall(r"https://docs\.agentscope\.io/[^\s)\]>\"'`]*", line):
                hits.append((path, lineno, url))
    return hits


def _snapshot_problems(root: Path, pin: str) -> list[str]:
    """快照清单与 pin 的一致性 + 每页 sha256（离线，不联网）。"""
    dest = root / "docs" / "agentscope" / pin
    manifest_path = dest / "MANIFEST.json"
    if not manifest_path.exists():
        return [f"缺少快照清单 {manifest_path.relative_to(root)}（跑 scripts/sync-agentscope-docs.sh）"]
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if data.get("version") != pin:
        problems.append(f"快照版本 {data.get('version')} ≠ pin {pin}")
    pages = data.get("pages", {})
    if data.get("page_count") != len(pages):
        problems.append(f"MANIFEST page_count={data.get('page_count')} ≠ pages 条目 {len(pages)}")
    for rel, sha in pages.items():
        path = dest / rel
        if not path.exists():
            problems.append(f"快照缺页 {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            problems.append(f"快照页内容漂移 {rel}（改过就必须重跑同步脚本，不许手改快照）")
    actual = {str(p.relative_to(dest)) for p in dest.rglob("*.md")}
    extra = sorted(actual - set(pages))
    if extra:
        problems.append(f"清单外多出页面 {extra[:5]}")
    return problems


def _format(title: str, items: list[str], how: str, section: str) -> str:
    return f"{title}\n" + "\n".join(f"  - {item}" for item in items) + f"\n  → {how}（{SPEC} {section}）"


def test_guard_1_pin_matches_the_documented_baseline() -> None:
    """守卫 1 正控：非精确 pin 与版本不一致都要被抓到，当前仓库两者都合规。"""
    assert _pin(REPO)[1] == [], _format("agentscope pin 不合规", _pin(REPO)[1], "改成精确 pin", "§1.1")
    pin, _ = _pin(REPO)
    problems = _snapshot_problems(REPO, pin or "")
    assert not problems, _format("文档快照与 pin 不一致", problems, "重跑同步脚本或修正 pin", "§2.3")


def test_guard_2_only_allowlisted_modules_are_imported() -> None:
    """守卫 2：import 面白名单。"""
    offenders = [
        f"{path.relative_to(REPO)}:{lineno} 引用了 agentscope.{module}"
        for path, lineno, module in _imports(REPO)
        if module not in ALLOWED_MODULES
    ]
    assert not offenders, _format(
        "出现了白名单外的 agentscope 模块",
        offenders,
        "确认用法后同时更新 ALLOWED_MODULES 与规约 §1.3；若属刻意偏离还要登记账本",
        "§1.3",
    )


def test_guard_3_no_agentscope_1x_symbols() -> None:
    """守卫 3：1.x 遗留符号（AST 判定，注释与 docstring 里的引用不算）。"""
    offenders = [
        f"{path.relative_to(REPO)}:{lineno} 出现 1.x 写法 {what}"
        for path, lineno, what in _legacy_symbols(REPO)
    ]
    assert not offenders, _format("出现了 AgentScope 1.x 写法", offenders, "改用 2.x API", "§1.2")


def test_guard_4_doc_links_carry_the_pinned_version() -> None:
    """守卫 4：文档链接必须带 pin 版本，禁止 /latest/、/stable/ 与无版本路径。"""
    pin, _ = _pin(REPO)
    offenders: list[str] = []
    for path, lineno, url in _doc_links(REPO):
        match = VERSIONED_DOC_RE.match(url)
        if match is None:
            offenders.append(f"{path.relative_to(REPO)}:{lineno} 无版本链接 {url}")
        elif match.group(2) != pin:
            offenders.append(f"{path.relative_to(REPO)}:{lineno} 版本不是 pin 的 {pin}：{url}")
    assert not offenders, _format(
        "AgentScope 文档链接不合规", offenders, "换成 /zh/versions/<pin>/ 前缀", "§1.4"
    )


def test_guard_5_snapshot_is_intact() -> None:
    """守卫 5：快照完整性与版本一致性（哈希级）。"""
    pin, _ = _pin(REPO)
    problems = _snapshot_problems(REPO, pin or "")
    assert not problems, _format(
        "文档快照不完整", problems, "跑 scripts/sync-agentscope-docs.sh 重建", "§2.3"
    )


def test_guard_1_accepts_an_exact_pin_with_an_extra(tmp_path: Path) -> None:
    """extra 不放松版本约束：`agentscope[memory-reme]==2.0.10` 仍是精确 pin（ADR-0034）。"""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["agentscope[memory-reme]==2.0.10"]\n',
        encoding="utf-8",
    )
    assert _pin(tmp_path) == ("2.0.10", [])


def test_guard_1_fires_on_a_non_exact_pin(tmp_path: Path) -> None:
    """只测「现在绿」证明不了守卫有效：每条都要有故意违规即红的正控。"""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["agentscope>=2.0.10"]\n', encoding="utf-8"
    )
    pin, problems = _pin(tmp_path)
    assert pin is None and problems, "守卫 1 没抓到非精确 pin"


def test_guard_2_fires_on_an_unlisted_import(tmp_path: Path) -> None:
    (tmp_path / "app" / "alibot").mkdir(parents=True)
    (tmp_path / "app" / "alibot" / "bad.py").write_text("import agentscope.deploy\n", encoding="utf-8")
    modules = {m for _, _, m in _imports(tmp_path)}
    assert "deploy" not in ALLOWED_MODULES and "deploy" in modules, f"守卫 2 没抓到越界 import：{modules}"


def test_guard_3_fires_on_1x_symbols(tmp_path: Path) -> None:
    (tmp_path / "app" / "alibot").mkdir(parents=True)
    (tmp_path / "app" / "alibot" / "bad.py").write_text(
        "from agentscope.agents import ReActAgent\nReActAgent(name='x', sys_prompt='y')\n", encoding="utf-8"
    )
    legacy = {what for _, _, what in _legacy_symbols(tmp_path)}
    assert "ReActAgent" in legacy and "关键字参数 sys_prompt=" in legacy, f"守卫 3 没抓到 1.x 写法：{legacy}"


def test_guard_4_fires_on_an_unversioned_or_wrong_version_link(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "bad.md").write_text(
        "见 https://docs.agentscope.io/latest/quickstart 与 https://docs.agentscope.io/zh/versions/0.0.1/x",
        encoding="utf-8",
    )
    links = [url for _, _, url in _doc_links(tmp_path)]
    matched = [m for m in (VERSIONED_DOC_RE.match(url) for url in links) if m is not None]
    assert any("/latest/" in url for url in links) and any(m.group(2) != "2.0.10" for m in matched), (
        "守卫 4 没抓到无版本 / 错版本链接"
    )


def test_guard_5_fires_on_a_hash_mismatch(tmp_path: Path) -> None:
    dest = tmp_path / "docs" / "agentscope" / "2.0.10"
    dest.mkdir(parents=True)
    (dest / "a.md").write_text("hi", encoding="utf-8")
    (dest / "MANIFEST.json").write_text(
        json.dumps({"version": "2.0.10", "page_count": 1, "pages": {"a.md": "0" * 64}}), encoding="utf-8"
    )
    assert _snapshot_problems(tmp_path, "2.0.10"), "守卫 5 没抓到哈希不符"
