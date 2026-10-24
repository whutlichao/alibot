"""测试卫生守卫：pytest 进程的 HOME 必须指向临时目录。

ADR-0031 把全局层根固定为 ``$HOME/.alibot``（只看 HOME，不随数据目录移动），#230 的
``create_app`` 又无条件接线它。若测试不隔离 HOME，开发者的真实 ``~/.alibot`` 会被所有
走真实 seam 的 HTTP 用例读到——本机/CI 恰好没有该目录时不会红，于是这是一个只在开发
机上发作的隐性依赖。本用例只断言环境事实，不 import 私有名。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alibot.workspace import global_home_root


def test_home_is_isolated_to_the_pytest_temporary_root(tmp_path_factory: pytest.TempPathFactory) -> None:
    base = tmp_path_factory.getbasetemp().resolve()
    home = Path(os.environ["HOME"]).resolve()

    assert base in home.parents or home == base, f"HOME 没有被指向 pytest 临时根：{home}"
    assert base in Path(global_home_root()).resolve().parents, "默认全局根必须落在临时 HOME 下"
    # 长期记忆始终开启（ADR-0035）后，真实 seam 的用例会建出临时全局层里的家目录与 reme/；
    # 这里只要求它**仍然落在** pytest 临时根下（上面两条已钉住），绝不写开发者真实 HOME。
    assert Path(global_home_root()).resolve().is_relative_to(base)
    if os.environ.get("USERPROFILE"):
        assert Path(os.environ["USERPROFILE"]).resolve() == home
