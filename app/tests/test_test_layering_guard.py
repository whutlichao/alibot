"""纪律守卫之三（retro 收口）：pytest 只断言**外部可观察行为**。

`app/README.md` 的「测试与检查」段早写明这条纪律：Python 测试只断言消息、轮次结果、状态点、
事件流、seq、已读、未读、不可变性、重启一致这些**对外事实**，不检查内部函数或前端 DOM；
只有两处 i18n 词条数据契约例外。它此前只靠 reviewer 人肉守——一张 PR 上就被抓到三处越层，
其中两处是**机械可判**的：

1. 直接断言 ``SEAM_STATUSES == (...)``（内部状态词表的**形状**）；
2. 用正则从 ``studio/app.js`` 里提 ``SUMMARY_STATUSES``（**跨层读前端源码**）。

第三条（直接调用内部函数 ``supports_tool_calling``）不是机械判据：同一批测试里既有合法的
seam 级用例（``test_agent_seam_client_lifecycle.py`` 本就该调 seam），也有行为用例，规则要
区分它们只能靠语义。它按判断题留在 ``docs/agents/review-rules.md``。

本测试把能机械化的三条变成判据（扫 ``app/tests/test_*.py`` 的 **AST**——注释与文档串不算）：

- **不读 Studio 源码**：代码里的字符串常量不出现 ``studio`` 这个词（唯一例外：
  ``test_reminder_delivery.py`` 读 ``studio/i18n.js`` 验「后端词条不许越界进前端表」，
  那条判据没有 HTTP 读数面）；
- **不断言状态词表的形状**：``SEAM_STATUSES`` / ``ROUND_STATUSES`` / ``MESSAGE_KINDS`` /
  ``SUMMARY_STATUSES`` 不许出现在 ``==`` / ``!=`` 的比较里。**成员判断放行**
  （``"silent" in ROUND_STATUSES``、``@pytest.mark.parametrize("s", ROUND_STATUSES)``）：
  那是「这份数据契约里有没有这个词」的事实，与「词表恰好是这几项、这个顺序」不同；
- **不导入私有名**：不从 ``alibot.*`` 导入 ``_`` 开头的名字。

Studio 读豁免是**显式且可验证**的：本测试同时断言「被豁免的文件真的读了 Studio」——否则一次
改名就会让豁免悄悄变宽（这是 ``test_read_path_legacy_tables_guard.py`` 的既有体例）。
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# 例外：词条表是数据契约，它的「后端词条不许进前端表」只能比两份**数据**，没有 HTTP 面。
STUDIO_READ_EXEMPTIONS = frozenset({"test_reminder_delivery.py"})

# 前端源码在仓库里的那一段路径（本文件按 ``_test_sources`` 排除，所以直接写字面量）。
STUDIO_SEGMENT = "studio"

# **HTTP 路径**不是「读源码」。票 #318 把工作台入口改成 `/studio/v2/` 之后，一组只做 HTTP 请求的
# 用例（静态缓存契约 / 入口 redirect）里出现了 `"/studio/v2/"`、`"/studio/i18n.js"` 这类字面量——
# 它们是 URL，不是文件路径，不读任何文件。判据的原意是「pytest 不许**打开** Studio 源码来断言
# 呈现细节」，因此只看「这个字符串会不会被当成**文件**路径用」，不看它长得像不像 `studio`。
#
# **`/` 开头**只说明「是绝对路径」——它既可能是 HTTP 路径（FastAPI 路由、`TestClient.get()` 的入参），
# 也可能是绝对**文件**路径（`/Users/.../app/alibot/studio/i18n.js`，那才真是在读源码）。区分的判据是
# **首段是不是 `studio`**：`/studio/v2/` 的首段就是那一段 URL 前缀，而文件路径的首段是 `/Users` /
# `/home` / `/tmp` 这类根目录，`studio` 只会出现在中间。
#
# 仍然被抓的两种真读法（反向验证写在 ``test_the_http_path_exemption_is_narrow`` 里）：
#   - `"studio/i18n.js"`（相对 cwd 的文件）——首段就是 `studio`，但**不带**前导 `/`；
#   - `"/Users/… /studio/i18n.js"`——绝对文件路径，`studio` 不在首段。


def _is_http_path(value: str) -> bool:
    """``/studio/...``（首段是 ``studio``）是 URL，不是文件路径。"""
    if not value.startswith("/"):
        return False
    head = value.lstrip("/").split("/", 1)[0]
    return head == STUDIO_SEGMENT


VOCABULARY_NAMES = frozenset({"SEAM_STATUSES", "ROUND_STATUSES", "MESSAGE_KINDS", "SUMMARY_STATUSES"})


def _test_sources() -> list[Path]:
    """待扫的测试文件：``test_*.py``，但不含本守卫自己（它定义规则，不服从规则）。"""
    return sorted(path for path in TESTS_DIR.glob("test_*.py") if path.name != Path(__file__).name)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    """模块 / 函数 / 类的第一句文档串（它们**不是**代码里的字符串，规则不该扫到）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _studio_reads(tree: ast.Module) -> list[int]:
    """代码里出现 ``studio`` 的字符串常量（文档串不算；HTTP 路径也不算）。

    这个判据守的是「pytest 不许读 Studio 源码来断言呈现」，因此它要区分**文件路径**与**URL**：
    票 #318 之后 ``/studio/v2/`` 是最常被断言的 HTTP 入口，它不打开任何文件。
    """
    docstrings = _docstring_node_ids(tree)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and STUDIO_SEGMENT in node.value
        and not _is_http_path(node.value)
        and id(node) not in docstrings
    )


def _vocabulary_shape_assertions(tree: ast.Module) -> list[tuple[int, str]]:
    """``==`` / ``!=`` 里出现状态词表常量 ⇒ 断言的是内部形状（成员判断不算）。"""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        for operand in [node.left, *node.comparators]:
            if isinstance(operand, ast.Name) and operand.id in VOCABULARY_NAMES:
                hits.append((node.lineno, operand.id))
            elif isinstance(operand, ast.Attribute) and operand.attr in VOCABULARY_NAMES:
                hits.append((node.lineno, operand.attr))
    return sorted(hits)


def _private_alibot_imports(tree: ast.Module) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if (node.module or "").split(".")[0] != "alibot":
            continue
        for alias in node.names:
            if alias.name.startswith("_") and not alias.name.startswith("__"):
                found.append((node.lineno, alias.name))
    return found


def _offenders(find) -> dict[str, object]:
    """对每个待扫文件跑一条判据，只留下有命中的（返回值即失败信息）。"""
    found = {path.name: find(_parse(path)) for path in _test_sources()}
    return {name: hits for name, hits in found.items() if hits}


def test_the_studio_read_exemption_is_real() -> None:
    """豁免必须命中真文件、且真的读了 Studio——改名或删掉那条用例都不该让豁免悄悄变宽。"""
    names = {path.name for path in _test_sources()}
    assert names >= STUDIO_READ_EXEMPTIONS, f"豁免了不存在的文件：{STUDIO_READ_EXEMPTIONS - names}"
    exempt_tree = _parse(TESTS_DIR / next(iter(STUDIO_READ_EXEMPTIONS)))
    assert _studio_reads(exempt_tree), "这条 Studio 读豁免已经不需要了（文件不再读 Studio）"


def test_pytest_does_not_read_studio_sources() -> None:
    offenders = {
        name: lines for name, lines in _offenders(_studio_reads).items() if name not in STUDIO_READ_EXEMPTIONS
    }
    assert not offenders, (
        "pytest 读到了 Studio 源码（应改用 Node DOM 用例族断言呈现）："
        f"{offenders}；唯一例外见 STUDIO_READ_EXEMPTIONS"
    )


def test_pytest_does_not_assert_the_shape_of_vocabulary_constants() -> None:
    offenders = _offenders(_vocabulary_shape_assertions)
    assert not offenders, (
        "状态词表是数据契约：断言它的**形状**属于检查内部实现。行为请经 HTTP / 库级 CHECK / DOM 断言；"
        '要断言「这份契约里有这个词」用成员判断（`"x" in ROUND_STATUSES`）：'
        f"{offenders}"
    )


def test_pytest_does_not_import_private_alibot_names() -> None:
    offenders = _offenders(_private_alibot_imports)
    assert not offenders, f"pytest 导入了实现私有名（测试接缝应是外部行为）：{offenders}"


def test_the_http_path_exemption_is_narrow() -> None:
    """``/studio/...`` 的豁免必须只放行**URL**，不能顺手把「真读源码」也放掉。

    这条是豁免的反向判据（与 ``test_the_studio_read_exemption_is_real`` 同一个体例）：一个**过宽**的
    豁免会让整条规则静默失效，因此正面与反面各钉一遍——
    """
    import ast as _ast

    http = ['"/studio/v2/"', '"/studio/i18n.js"', '"/studio"', '"/studio/"']
    for literal in http:
        assert _studio_reads(_ast.parse(f"x = {literal}")) == [], f"HTTP 路径不该被当成读源码：{literal}"

    # 真读源码的两种写法，无论相对还是绝对，都必须被抓到。
    file_paths = [
        '"studio/i18n.js"',  # 相对 cwd
        '"/Users/apple/orca/alibot/app/alibot/studio/i18n.js"',  # 绝对文件路径
        '"./studio/app.css"',
    ]
    for literal in file_paths:
        assert _studio_reads(_ast.parse(f"x = {literal}")) == [1], f"读源码必须被抓到：{literal}"
