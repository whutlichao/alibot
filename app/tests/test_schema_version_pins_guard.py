"""纪律守卫之五（retro 收口）：`SCHEMA_VERSION` 不许被钉成一个历史号。

ADR-0015 §三：判据是「**必须高于当前 main**」，不是「等于被分配的预留号」。把版本号钉成固定值
会在下一次合法 bump 时假红——历史上 ``assert SCHEMA_VERSION == 6``、``== 13`` 都发生过，每次
bump 的作者都得回头改**别票**的测试（本 run 里 #53 与 #58 各交了一次这类税）。

判据（AST 扫 ``app/tests/test_*.py``）：一旦比较里出现 ``SCHEMA_VERSION``，另一侧不许是
**字面量整数**，也不许是**同文件里以字面量整数赋值的名字**（如 ``REMINDER_SCHEMA_VERSION = 13``）。
``>=`` / ``<=`` / ``<`` / ``>``、以及从 ``SCHEMA_VERSION`` 自身派生的表达式一律放行：
「本版至少覆盖到第 N 步」是正确的写法，「本版恰好等于 13」不是。
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
NAME = "SCHEMA_VERSION"


def _test_sources() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_int_names(tree: ast.Module) -> frozenset[str]:
    """同文件里被赋成**字面量整数**的名字：拿这种名字跟 `SCHEMA_VERSION` 比等值同样是钉死。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant) and type(node.value.value) is int):
            continue
        names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return frozenset(names)


def _pins(tree: ast.Module) -> list[tuple[int, str]]:
    literal_names = _literal_int_names(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        if not any(isinstance(item, ast.Name) and item.id == NAME for item in operands):
            continue
        for operand in operands:
            if isinstance(operand, ast.Name) and operand.id == NAME:
                continue
            if isinstance(operand, ast.Constant) and type(operand.value) is int:
                hits.append((node.lineno, f"{NAME} == {operand.value}（字面量钉死）"))
            elif isinstance(operand, ast.Name) and operand.id in literal_names:
                hits.append((node.lineno, f"{NAME} == {operand.id}（同文件里被赋成字面量整数）"))
    return sorted(hits)


def test_the_scanner_catches_both_pin_shapes_and_allows_the_derived_forms() -> None:
    """正控：规则真的能抓到两种钉死形态，也不会误伤「至少到第 N 步」与派生比较。"""
    sample = ast.parse(
        "PIN = 13\n"
        "assert SCHEMA_VERSION == 13\n"
        "assert SCHEMA_VERSION == PIN\n"
        "assert SCHEMA_VERSION >= 13\n"
        "assert SCHEMA_VERSION - 1 == 12\n"
        "assert steps[-1][1] == SCHEMA_VERSION\n"
        "assert _version(db) == str(SCHEMA_VERSION)\n"
    )
    hits = _pins(sample)
    assert [line for line, _ in hits] == [2, 3], f"规则没有准确命中两种钉死形态：{hits}"


def test_no_test_file_pins_the_schema_version_to_a_historical_number() -> None:
    offenders = {path.name: _pins(_parse(path)) for path in _test_sources()}
    offenders = {name: hits for name, hits in offenders.items() if hits}
    assert not offenders, (
        "测试把 SCHEMA_VERSION 钉成了历史号（下一次合法 bump 会假红）："
        f"{offenders}；改成 `>=` 或从 SCHEMA_VERSION 自身派生（ADR-0015 §三）"
    )
