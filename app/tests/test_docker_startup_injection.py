"""守卫：测试装配**不跑**真实的 Docker 启动清扫，而生产装配仍然会跑。

为什么需要它（票 #328 的 CI 两次红）：``create_app`` 的 lifespan 启动时会调
``sweep_stale_docker_workspaces()``，判据是「不在**本进程**容器池里 ⇒ 孤儿 ⇒ ``force=True`` 删」。
生产是单进程，成立；测试用 ``-n auto`` 并行时每个 pytest worker 是**独立进程**，任何 worker 进
lifespan 都会删掉别的 worker 正在用的容器（``<no gateway log available>`` /
``DockerError [404] No such exec instance``）。

这条守卫防的是**后来者把替身漏掉或删掉**：那时错误**只有在本机没有 docker 时才看不见**——
本机 docker 用例全部 skip，CI 上才红，而且红的是两条看起来与本次改动无关的 docker 用例。
那是一个很难归因的回归，所以钉住三条：

1. ``build_app`` 装配出来的 app 进 lifespan 时**不**碰真实清扫（替身生效）；
2. `create_app` 用默认参数（生产形态）时**会**碰——否则第 1 条只要「永远不跑」就能过，
   替身接线错了也测不出来；
3. **机械扫描**：`app/tests` 下每一个会进 lifespan 的 ``create_app(...)`` 调用都显式带了
   ``docker_startup=``（唯一例外见 ``_LIFESPAN_EXEMPT``），这样「新增一处忘了传」会立刻红，
   不必等到 CI 上被误删容器。

判据用「记录调用」而不是「让真实函数抛错」：lifespan 那段是 ``contextlib.suppress(Exception)``，
抛出来的异常会被吞掉，布尔读数就永远是「没炸」。前两条**断言的是模块级接线**（接线错了会静默），
没有 HTTP 读数面——这是它必须存在的理由，已记在 ``app/specs/issue-328-acceptance.md`` §7.6。
"""

from __future__ import annotations

import ast
from pathlib import Path

import alibot.api as api_module
from alibot.api import create_app
from fastapi.testclient import TestClient

from tests.conftest import build_app

TESTS_DIR = Path(__file__).resolve().parent

# 唯一允许不带 `docker_startup=` 的调用：它在 `pytest.raises(SchemaVersionMismatch)` 里，
# 异常发生在 **lifespan 之前**，根本进不去（加参数是噪音）。
_LIFESPAN_EXEMPT = {("test_schema_version_gate.py", 222)}


def _record_startup_calls(monkeypatch) -> list[str]:
    """把启动清扫的两个真实入口换成记录器，返回被调用的名字列表。"""
    calls: list[str] = []

    async def record_sweep() -> list[str]:
        calls.append("sweep")
        return []

    def record_reaper() -> None:
        calls.append("reaper")

    monkeypatch.setattr(api_module, "sweep_stale_docker_workspaces", record_sweep)
    monkeypatch.setattr(api_module, "start_docker_reaper", record_reaper)
    return calls


def test_test_assembly_never_runs_the_real_docker_startup(monkeypatch, tmp_path: Path) -> None:
    """``build_app``（测试唯一的装配入口）进 lifespan 时一次都不碰真实清扫。

    替身是 ``tests.scripted.inert_docker_startup``：它**关掉**这条动作，而不是「跑个空函数假装
    扫过了」。这里断言的是「真实入口零调用」，与替身内部实现无关。
    """
    calls = _record_startup_calls(monkeypatch)

    with TestClient(build_app(tmp_path / "data")) as client:
        assert client.get("/api/workspace").status_code == 200

    assert calls == [], f"测试装配跑了真实 Docker 启动动作：{calls}"


def test_production_assembly_still_runs_the_docker_startup(monkeypatch, tmp_path: Path) -> None:
    """正对照：默认参数（生产形态）**必须**仍然调用清扫与空闲回收。

    没有这条，上面那条只要「永远什么都不做」就能过——接线断了也测不出来。
    """
    calls = _record_startup_calls(monkeypatch)

    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        assert client.get("/api/workspace").status_code == 200

    assert calls == ["reaper", "sweep"], f"生产装配没有按默认路径启动 Docker 动作：{calls}"


def _create_app_call_sites() -> list[tuple[str, int, bool]]:
    """``app/tests`` 下所有 ``create_app(...)`` 调用：(文件名, 行号, 是否带 ``docker_startup=``)。

    扫 **AST**（注释与文档串里的 ``create_app`` 不算）。``measure_budget.py`` 不是 ``test_*.py``
    但也进 lifespan，同样纳入。**本文件不扫自己**（与 `test_test_layering_guard.py` 同一条体例：
    定义规则的文件不服从规则）——它的正对照用例必须走一次生产路径，那正是要证的东西。
    """
    sites: list[tuple[str, int, bool]] = []
    paths = [*TESTS_DIR.glob("test_*.py"), TESTS_DIR / "measure_budget.py"]
    for path in sorted(paths):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "create_app":
                continue
            has_docker_startup = any(keyword.arg == "docker_startup" for keyword in node.keywords)
            sites.append((path.name, node.lineno, has_docker_startup))
    return sites


def test_every_lifespan_entering_create_app_call_passes_the_double() -> None:
    """机械扫描：每一个会进 lifespan 的 ``create_app(...)`` 都显式带了 ``docker_startup=``。

    为什么是「显式传参」而不是在 ``create_app`` 里自动检测 pytest：后者会给生产路径加一条隐形
    分支，后来者根本看不到（协调者裁定，见验收文档 §7.3）。
    """
    sites = _create_app_call_sites()
    assert sites, "AST 扫描没找到任何 create_app 调用——判据本身退化了"

    missing = [
        f"{name}:{line}" for name, line, has in sites if not has and (name, line) not in _LIFESPAN_EXEMPT
    ]
    assert missing == [], f"这些 create_app 调用没有传 docker_startup=（#328 / #330）：{missing}"


def test_the_ast_scan_exemption_is_narrow() -> None:
    """例外必须**真的**只有那一条：豁免名单一旦变宽，上面那条扫描就形同虚设。"""
    assert len(_LIFESPAN_EXEMPT) == 1
    (name, line), *_ = _LIFESPAN_EXEMPT
    tree = ast.parse((TESTS_DIR / name).read_text(encoding="utf-8"), filename=name)
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "create_app")
            or getattr(node.func, "attr", "") == "create_app"
        )
    ]
    assert line in calls, f"豁免锚点 {name}:{line} 已不是 create_app 调用——豁免失效"
