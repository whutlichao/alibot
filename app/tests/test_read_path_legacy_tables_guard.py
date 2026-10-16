"""纪律守卫之二（issue #95 / 规格 #87 的 S15 / T4，边界 D12，判据来源 ADR-0015 第六节）：读路径不回退。

三张旧表（``channel_member`` / ``channel_read`` / ``channel_budget``）迁移后**原样保留**作回退面，
但**当前代码的任何读路径都不许再引用它们**——旧值一旦被读，未读 / 预算 / 成员面的口径立刻分裂出
第二个正本。它们只允许活在迁移 / 修复模块（``migrate.py`` / ``repair.py``）与测试里。

判据是**结构性事实**（T4 的既有体例，prior art = ``app/tests/test_target_repair.py`` 的模块图取证）：

1. 扫 ``app/alibot/`` 下**除** ``migrate.py`` / ``repair.py`` 之外的全部模块，取 SQL 形态的字符串常量
   （``SELECT`` / ``INSERT`` / … 开头），断言其中不出现旧表名；
2. 读路径的模块图不加载那两个「会说旧表名」的模块（真实子进程取证）。

**边界（D12，必须写清）**：``db.py`` 的 ``BUSINESS_TABLES`` 是「这个库算不算空库」的判据，它**同时**
列着新旧两代表名；它是空库判定、不是读路径 SQL，**豁免**，保持不动。本测试显式跳过它，并断言豁免
**真的命中**了那三张表（不是「碰巧没扫到」）。

**已知盲区**：表名若由 f-string / 字符串拼接组装，就不落在字面量里、不在扫描域内；本仓这类拼接只有
``BUSINESS_COUNT_SQL``（从 ``BUSINESS_TABLES`` 的字符串常量派生的空库计数语句），拼接结果不在扫描域内，
而 ``BUSINESS_TABLES`` 本身按 D12 豁免。
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from alibot import db

APP_DIR = Path(__file__).resolve().parents[1]
ALIBOT_DIR = APP_DIR / "alibot"

# 迁移 / 修复模块是旧表名的**合法落点**，不参与读路径扫描。
LEGACY_MODULES = ("migrate.py", "repair.py")

LEGACY_TABLES = ("channel_member", "channel_read", "channel_budget")
# 表名清单与整词正则同源，增删表名只改一处。
LEGACY_TABLE_RE = re.compile("|".join(rf"\b{re.escape(name)}\b" for name in LEGACY_TABLES), re.IGNORECASE)

# 「读路径的 SQL 常量」= 以一条 SQL 语句开头的字符串（先剥掉行注释前缀）。i18n 键、裸表名、文档串
# 都不在这个域里——这是本断言的**范围**，写出来免得误伤。
SQL_STATEMENT_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH|REPLACE|PRAGMA|BEGIN|COMMIT)\b",
    re.IGNORECASE,
)

# D12 的豁免：`db.py` 里这个常量是空库判定，不是读路径 SQL。
EXEMPT_LEGACY_HOLDER = "BUSINESS_TABLES"

# 读路径 SQL 常量的命中：``(模块名, 行号, 旧表名)``。
Finding = tuple[str, int, str]


def _legacy_names_in(text: str) -> frozenset[str]:
    """文本里出现的旧表名（整词、大小写不敏感）。"""
    return frozenset(match.group(0).lower() for match in LEGACY_TABLE_RE.finditer(text))


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """模块 / 类 / 函数 docstring 的字符串常量 ``id()``——文档串不是「SQL 常量」，不扫。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _exempt_holder_constant_ids(tree: ast.AST) -> set[int]:
    """``BUSINESS_TABLES`` 值里所有字符串常量的 ``id()``（D12 边界，显式豁免）。"""
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == EXEMPT_LEGACY_HOLDER for target in targets):
            continue
        value = node.value
        if value is None:
            continue
        for inner in ast.walk(value):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                exempt.add(id(inner))
    return exempt


def _module_findings(path: Path) -> tuple[list[Finding], set[str]]:
    """扫一个模块，返回 ``(读路径 SQL 里命中的旧表名, 被 BUSINESS_TABLES 豁免的旧表名)``。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_constant_ids(tree)
    exempt = _exempt_holder_constant_ids(tree)

    hits: list[Finding] = []
    exempted: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        names = _legacy_names_in(node.value)
        if not names:
            continue
        if id(node) in exempt:
            exempted |= names
        elif id(node) not in docstrings and bool(SQL_STATEMENT_RE.match(node.value)):
            hits.extend((path.name, node.lineno, name) for name in sorted(names))
    return hits, exempted


def _guarded_modules(root: Path = ALIBOT_DIR) -> list[Path]:
    """旧表名的合法落点（迁移 / 修复模块）之外的全部模块——读路径断言作用在它们上面。"""
    return sorted(path for path in root.rglob("*.py") if path.name not in LEGACY_MODULES)


def _scan_read_path(root: Path = ALIBOT_DIR) -> tuple[list[Finding], set[str]]:
    """扫一棵模块树，返回 ``(命中的旧表名, 被豁免的旧表名)``。"""
    findings: list[Finding] = []
    exempted: set[str] = set()
    for path in _guarded_modules(root):
        hits, names = _module_findings(path)
        findings.extend(hits)
        exempted |= names
    return findings, exempted


# ─── 正向：读路径的 SQL 常量里一个旧表名都没有 ───────────────────────────────
def test_read_path_sql_constants_never_name_a_legacy_table() -> None:
    findings, _ = _scan_read_path()
    assert findings == [], f"读路径引用旧表名（回退面会被读成第二个正本）：{findings}"


def test_the_scan_scope_is_the_read_path_and_excludes_the_legacy_modules() -> None:
    """范围守卫：扫描面真的覆盖读路径模块、且不含迁移 / 修复模块（否则上面的绿是空转）。"""
    names = {path.name for path in _guarded_modules()}
    assert {"db.py", "api.py", "service.py", "orchestration.py", "agent_seam.py"} <= names
    assert names.isdisjoint(LEGACY_MODULES)


def test_the_legacy_names_still_have_their_legal_home_in_the_migration_and_repair_modules() -> None:
    """「读路径里没有」不是「哪里都没有」：迁移 / 修复模块仍引用它们，合法。"""
    found: set[str] = set()
    for module in LEGACY_MODULES:
        found |= _legacy_names_in((ALIBOT_DIR / module).read_text(encoding="utf-8"))
    assert set(LEGACY_TABLES) <= found


def test_the_read_path_module_graph_never_loads_the_migration_or_repair_modules() -> None:
    """结构性证据（T4 的 prior-art 体例）：读路径连那两个会说旧表名的模块都不加载。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import alibot.api; "
            "print('alibot.repair' in sys.modules, 'alibot.migrate' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
        cwd=str(APP_DIR),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False False", "读路径不得引入迁移 / 修复模块"


# ─── 边界（D12）：BUSINESS_TABLES 豁免，且豁免不是空转 ────────────────────────
def test_business_tables_is_exempt_and_the_exemption_actually_hits_it() -> None:
    # 它**确实**同时列着新旧两代表名（所以豁免是必要的，不是多余的分支）……
    assert set(LEGACY_TABLES) <= set(db.BUSINESS_TABLES)

    # ……而扫描显式跳过它：豁免真的命中了这三张表，不是「碰巧没扫到」。
    _, exempted = _scan_read_path()
    assert set(LEGACY_TABLES) <= exempted


# ─── 负向：同一判定被喂进一份含旧表名的读路径 SQL ⇒ 必须命中 ─────────────────
def test_the_scan_flags_a_legacy_table_injected_into_a_read_path_sql_constant(tmp_path: Path) -> None:
    """不空转：把旧表名塞进一份读路径 SQL 常量 ⇒ 同一条扫描（走真实的文件遍历）必须命中（红）。"""
    module = tmp_path / "read_path_with_a_legacy_read.py"
    module.write_text(
        'MEMBERS_SQL = "SELECT m.member_id FROM channel_member m WHERE m.channel_id = ?"\n',
        encoding="utf-8",
    )

    findings, exempted = _scan_read_path(tmp_path)

    assert findings == [(module.name, 1, "channel_member")]
    assert exempted == set()


def test_the_scan_does_not_flag_legacy_names_outside_the_sql_domain(tmp_path: Path) -> None:
    """反向边界：i18n 键 / 裸表名元组 / docstring 里的旧表名都不在读路径 SQL 域内，不误伤。"""
    clean = tmp_path / "not_read_path_sql.py"
    clean.write_text(
        'JOIN_FAILED_KEY = "error.channel_member_join_failed"\n'
        'EMPTY_DB_TABLES = ("channel_member", "target_member", "channel_read", "channel_budget")\n'
        "\n"
        "\n"
        "def describe() -> str:\n"
        '    """旧表 ``channel_member`` 原样保留作回退面，不再被读。"""\n'
        '    return "target_member"\n',
        encoding="utf-8",
    )

    assert _module_findings(clean) == ([], set())
