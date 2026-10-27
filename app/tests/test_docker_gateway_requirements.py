"""#312：docker gateway venv 装什么——需求清单必须钉住本仓的 agentscope pin，并覆盖 import 链。

**这条判据防的是什么**：上游生成镜像时用 ``RUN uv pip install --no-deps "agentscope"``
（``agentscope/workspace/_docker/_make_dockerfile.py:165``），**不带任何版本约束**；而 venv 的基础
清单 ``_GATEWAY_BASE_REQUIREMENTS`` 只是上游打包那一版的快照。两者合起来意味着「镜像里跑的
agentscope 是构建当时 PyPI 的最新版，与本仓 pin 无关」——2026-10-27 那轮 CI 红就是这么来的：
2.0.11 于 02:54Z 发布，03:10Z 那轮 PR 的容器里装到 2.0.11，它的 ``_mcp_client.py:12`` 是
``import httpx2``，基础清单只有四行 ⇒ 容器内 gateway 启动即 ``ModuleNotFoundError: httpx2``。
同一个镜像在上一轮（02:31Z，2.0.11 还没发）是绿的——「有时绿有时红」不是 flake，是**版本脱钩**。

**判据在哪取证**：全部走上游真实构建接缝 :func:`prepare_build_context`（``_build_or_reuse_image``
用的就是它），读它**真实生成**的 ``requirements.txt``，不手抄 ``_GATEWAY_BASE_REQUIREMENTS`` 常量。
这是私有模块路径（上游没有公开构建钩子），升级 AgentScope 时必须重跑本文件；相关记账在
``docs/agents/agentscope-2-deviations.md`` 的 D14。

两条判据各抓一半根因，缺一不可：

1. **版本脱钩**（真根因）：清单里的 ``agentscope`` 约束必须是精确 pin，且等于 ``app/pyproject.toml``；
2. **又缺一个包**：清单（连同它自己的依赖闭包）必须覆盖 ``agentscope.mcp`` import 链的**全部**
   第三方顶层包。判据 2 扫的是已安装 agentscope 的 ``mcp/`` 源码，不是今天的 ``httpx2`` 名单——
   上游下一版改 import 时，红报会**点名**新缺的那个包，而不是静静放过。
"""

from __future__ import annotations

import ast
import importlib.metadata as metadata
import re
import shutil
import sys
import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path

from agentscope.workspace._docker._make_dockerfile import (
    CONTAINER_WORKDIR,
    GATEWAY_HOME,
    prepare_build_context,
)
from alibot.workspace import docker_gateway_extra_pip

REPO = Path(__file__).resolve().parents[2]

#: 归一化包名（PEP 503）：``httpx2`` / ``httpx2>=2.0`` / ``HTTPX2`` 都归到 ``httpx2``。
_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _repo_pin() -> str:
    """本仓在 ``app/pyproject.toml`` 里钉的 agentscope 版本（判据 1 的独立真值来源）。"""
    data = tomllib.loads((REPO / "app" / "pyproject.toml").read_text(encoding="utf-8"))
    deps = [d for d in data["project"]["dependencies"] if d.startswith("agentscope")]
    assert len(deps) == 1, f"app/pyproject.toml 里 agentscope 依赖应恰好一条，实际 {deps}"
    match = re.fullmatch(r"agentscope(?:\[[^\]]*\])?==(\d+\.\d+\.\d+)", deps[0])
    assert match, f"agentscope 必须是精确 pin，实际 {deps[0]}"
    return match.group(1)


def _gateway_requirements(extra_pip: Iterable[str]) -> list[str]:
    """走上游真实构建接缝取 gateway venv 的 ``requirements.txt`` 全文（按行）。

    只读不建镜像：``prepare_build_context`` 会把 Dockerfile / requirements / 两个 helper 脚本写进
    一个临时目录，这里读完即删（它自己不管回收，见其 docstring）。
    """
    ctx_dir, _tag, copy_files = prepare_build_context(
        gateway_home=GATEWAY_HOME,
        container_workdir=CONTAINER_WORKDIR,
        extra_pip=list(extra_pip),
    )
    try:
        return [
            line.strip()
            for line in copy_files["requirements.txt"].decode("utf-8").splitlines()
            if line.strip()
        ]
    finally:
        shutil.rmtree(ctx_dir, ignore_errors=True)


def _requirement_name(spec: str) -> str:
    """从一行需求里取包名（``httpx2>=2.0`` → ``httpx2``）。"""
    match = _NAME_RE.match(spec.strip())
    assert match, f"需求行没有包名：{spec!r}"
    return _normalize(match.group(1))


def _agentscope_root() -> Path:
    """已安装 agentscope 包目录——就是镜像里 ``--no-deps`` 装进去的那一份。"""
    import agentscope

    return Path(agentscope.__file__).resolve().parent


def _module_package(rel: Path) -> tuple[str, ...]:
    """文件（相对 agentscope 根）所属的包路径：``mcp/_mcp_client.py`` → ``("mcp",)``。"""
    return rel.with_suffix("").parts[:-1]


def _resolve(rel_parts: tuple[str, ...], module: str | None, level: int) -> Path | None:
    """把 ``from <level dots><module> import …`` 解析成 ``agentscope/**`` 下的真实文件。

    ``level`` 相对**当前模块所属的包**：``mcp/_mcp_client.py`` 里 ``from .._logging import …``
    是 ``level=2`` ⇒ 上跳一层到 ``agentscope/``。
    """
    root = _agentscope_root()
    # level=1 是当前包，level=2 上跳一层……先按 `level - 1` 上跳到相对 base。
    base = rel_parts[: len(rel_parts) - (level - 1)] if level > 1 else rel_parts if level else ()
    parts = (*base, *((module or "").split(".") if module else ()))
    if not parts or parts[0] != "agentscope":
        return None
    target = root.joinpath(*parts[1:])
    for candidate in (target.with_suffix(".py"), target / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _module_imports(path: Path) -> Iterator[tuple[Path, int, str]]:
    """一个模块里**运行时**发生的顶层 import：产出 ``(文件, 行号, 顶层包名)``。

    跳过 ``if TYPE_CHECKING:`` 块（那些 import 在运行时不存在，镜像里也不需要），跳过相对
    ``agentscope`` 自身以外的相对 import（本包没有这种情况，出现即是另一种问题，交给别的守卫）。
    """
    rel = path.relative_to(_agentscope_root())
    package = _module_package(rel)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        for candidate in (node, getattr(node, "test", None)):
            if isinstance(candidate, ast.Name) and candidate.id == "TYPE_CHECKING":
                break
        else:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top != "agentscope":
                        yield path, node.lineno, top
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    target = _resolve(package, node.module, node.level)
                    if target is not None:
                        yield from _module_imports(target)
                elif node.module and node.module.split(".")[0] == "agentscope":
                    target = _resolve((), node.module, 0)
                    if target is not None:
                        yield from _module_imports(target)
                elif node.module:
                    yield path, node.lineno, node.module.split(".")[0]


def _third_party_imports() -> list[tuple[Path, int, str]]:
    """``agentscope.mcp`` 入口链上的全部第三方顶层 import（递归跟到包内的相对 import）。"""
    root = _agentscope_root()
    hits: list[tuple[Path, int, str]] = []
    seen: set[Path] = set()

    def walk(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        for hit in _module_imports(path):
            if hit[2] not in sys.stdlib_module_names:
                hits.append(hit)
                # 跟着包内相对 import 走，但第三方包的内部导入不在本判据范围内（那是它们的依赖）。
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.ImportFrom) and node.level:
                target = _resolve(_module_package(path.relative_to(root)), node.module, node.level)
                if target is not None:
                    walk(target)
            elif (
                isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "agentscope"
            ):
                target = _resolve((), node.module, 0)
                if target is not None:
                    walk(target)

    for path in sorted((root / "mcp").rglob("*.py")):
        walk(path)
    return hits


def _declared_closure(names: Iterable[str]) -> set[str]:
    """需求清单的**依赖闭包**：装 ``mcp<2.0.0`` 时 pydantic 会跟着进来，所以它算已覆盖。

    例外是 ``agentscope`` 自身：镜像里它是 ``--no-deps`` 装的（``_make_dockerfile.py:165``），
    它的依赖**不会**进容器，所以不能拿它的元数据来「补覆盖」——否则判据会把缺项算成已覆盖。
    """
    pending = [name for name in names if name != "agentscope"]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = metadata.requires(name) or []
        except metadata.PackageNotFoundError:  # 宿主没装这件：清单里写了就够，不深究
            continue
        for spec in requires:
            head, _, marker = spec.partition(";")
            if "extra ==" in marker:  # 只有装某个 extra 时才需要的依赖，镜像里没有
                continue
            match = _NAME_RE.match(head.strip())
            if match:
                pending.append(_normalize(match.group(1)))
    return seen


def test_gateway_requirements_declare_the_same_httpx2_floor_as_pyproject() -> None:
    """判据 3：清单里的 ``httpx2`` 下界必须与 ``app/pyproject.toml`` **同源**（单一地板）。

    为什么不是“判据 2 会抓到它”：把 agentscope pin 回 2.0.10 后，容器**不再需要** ``httpx2``
    （实测：删镜像重建，清单只有四行 + ``agentscope==2.0.10`` 时容器内
    ``from agentscope.mcp import MCPClient`` 绿）。所以 ``httpx2`` 这一项是**防御项**，不是今天的必需项；
    拿“覆盖 import”的判定去卡它只会不得要么。它真挡的风险是另一件事：``httpx2`` 是本仓运行时
    已声明的依赖（各 provider 的 HTTP 客户端走它），而 gateway venv 是**另一个 venv**——两边各写
    一个下界就会漂：改了 ``pyproject.toml`` 忘了这一头，容器里就装到一个旧地板。

    判据只认“两边同一地板”，不认具体数值：``pyproject.toml`` 改了这里不去就是红。
    """
    data = tomllib.loads((REPO / "app" / "pyproject.toml").read_text(encoding="utf-8"))
    declared = [
        dep
        for group in (
            data["project"].get("dependencies", []),
            data["project"]["optional-dependencies"]["dev"],
        )
        for dep in group
        if _requirement_name(dep) == "httpx2"
    ]
    assert declared, "app/pyproject.toml 里没有声明 httpx2（本仓运行时依赖）"
    floor = re.fullmatch(r"httpx2>=([0-9][0-9A-Za-z.]*)", declared[0].strip())
    assert floor, f"app/pyproject.toml 的 httpx2 约束应写成下界（httpx2>=X），实际 {declared[0]!r}"

    lines = _gateway_requirements(docker_gateway_extra_pip())
    in_image = [line for line in lines if _requirement_name(line) == "httpx2"]
    assert in_image, (
        "gateway venv 的需求清单里没有 httpx2：它是本仓运行时已声明的依赖（见 app/pyproject.toml），"
        "gateway venv 是另一个 venv，不能靠宿主碰巧装了就当作有。"
        f"\n  当前清单：{lines}"
    )
    assert in_image[0].strip() == declared[0].strip(), (
        f"gateway venv 的 httpx2 约束 {in_image[0]!r} ≠ app/pyproject.toml 的 {declared[0]!r}："
        "两个 venv 的地板必须同源，否则升地板时只改了一边。"
    )


def test_gateway_requirements_pin_the_same_agentscope_as_this_repo() -> None:
    """判据 1：镜像里的 agentscope 必须钉成**本仓 pin 的同一个版本**（#312 的真根因）。

    上游 install 块 ``--no-deps "agentscope"`` 不带约束，于是镜像装的是构建当时的最新版；
    2026-10-27 那轮 CI 就是容器里装了 2.0.11（``_mcp_client.py:12 import httpx2``）而基础清单还是
    2.0.10 时代的四行，才缺 ``httpx2``。只补缺包治不了这个根：上游每发一版，缺的包就换一个。
    """
    pin = _repo_pin()
    lines = _gateway_requirements(docker_gateway_extra_pip())
    pinned = [line for line in lines if _requirement_name(line) == "agentscope"]
    assert pinned, (
        "gateway venv 的需求清单里没有任何 agentscope 约束（上游 install 块是不带版本的 "
        f'``--no-deps "agentscope"``）⇒ 镜像会装构建当时的 PyPI 最新版，与本仓 pin {pin} 脱钩。'
        f"\n  完整清单：{lines}"
    )
    assert len(pinned) == 1, f"agentscope 约束应恰好一条，实际 {pinned}"
    assert re.fullmatch(rf"agentscope=={re.escape(pin)}", pinned[0]), (
        f"gateway venv 的 agentscope 约束 {pinned[0]!r} 与本仓 pin {pin!r} 不一致："
        f"容器里跑的框架必须就是本仓 pin 的那一版（app/pyproject.toml）。"
    )


def test_gateway_requirements_cover_every_import_of_agentscope_mcp() -> None:
    """判据 2：清单覆盖 ``agentscope.mcp`` import 链上的全部第三方顶层包（缺包）。

    判据**不认名字**：它扫已安装 agentscope 的 ``mcp/`` 源码（含包内相对 import 递归），
    再与「清单 + 清单的依赖闭包」相比。上游换成 ``httpx3``、多加一个 ``orjson``，这里都会点名。
    """
    lines = _gateway_requirements(docker_gateway_extra_pip())
    declared = {_requirement_name(line) for line in lines}
    covered = declared | _declared_closure(declared)
    missing: dict[str, list[str]] = {}
    for path, lineno, name in _third_party_imports():
        if _normalize(name) not in covered:
            missing.setdefault(_normalize(name), []).append(f"{path.name}:{lineno}")
    assert not missing, (
        "gateway venv 的需求清单缺了 agentscope.mcp import 链要用的包（容器内 gateway 会启动即崩）：\n"
        + "\n".join(
            f"  - {name}（被 {', '.join(sorted(set(where)))} 导入）" for name, where in missing.items()
        )
        + f"\n  当前清单：{lines}"
        + "\n  → 把这些包加进 alibot.workspace.docker_gateway_extra_pip()（上游 extra_pip 接缝）。"
    )
