"""#267：新建 Agent 立刻有家目录与 ReMe 工作区；建目录失败不阻断创建。

判据来源：ADR-0035（长期记忆始终开启）+ 本票正本。接缝是 HTTP 黑盒：``POST /api/agents``
成功后直接看文件系统（conftest 已把 HOME 隔离到临时目录），失败路径用进程内替身让
``memory_workspace_dir`` 抛可读配置错误——这条**必须**在进程内，因为「无写权限 / 根外 symlink」
在隔离的临时 HOME 里没法稳定制造。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from alibot.workspace import WorkspaceConfigError
from fastapi.testclient import TestClient

from tests.conftest import create_agent


def _agents_root() -> Path:
    """全局层根下的 ``agents/``（与生产同一个解析入口：只看 HOME）。"""
    return Path(os.environ["HOME"]) / ".alibot" / "agents"


def test_creating_an_agent_makes_its_home_and_reme_workspace(client: TestClient) -> None:
    response = create_agent(client)

    assert response.status_code == 201, response.text
    home = _agents_root() / response.json()["agent"]["id"]
    assert home.is_dir(), "新建 Agent 必须立刻有家目录这一级"
    assert (home / "reme").is_dir(), "长期记忆工作区必须同时建出"
    # ADR-0034 §一 的创建纪律不变：应用不替人创建这些文件 / 目录。
    assert not (home / "AGENTS.md").exists()
    assert not (home / "mcp.json").exists()
    assert not (Path(os.environ["HOME"]) / ".alibot" / "skills").exists()


def test_a_home_creation_failure_is_logged_and_creation_still_succeeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unusable(*_args: object, **_kwargs: object) -> Path:
        raise WorkspaceConfigError("workspace state partition escapes root: '/tmp/elsewhere'")

    monkeypatch.setattr("alibot.service.memory_workspace_dir", unusable)

    with caplog.at_level(logging.WARNING, logger="alibot.service"):
        response = create_agent(client)

    assert response.status_code == 201, response.text
    member_id = response.json()["agent"]["id"]
    # 成员行已落库（家目录不是对话的前提），因此工作台仍能读到它。
    assert [agent["id"] for agent in client.get("/api/agents").json()["agents"]] == [member_id]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings, "建目录失败必须留下一条可读告警"
    assert member_id in "\n".join(record.getMessage() for record in warnings)
