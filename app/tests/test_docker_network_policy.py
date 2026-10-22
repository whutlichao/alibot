"""#187：Docker 后端的容器网络策略（ADR-0026 §五）——默认无网 + host 白名单。

本机没有 Docker daemon，因此本文件**不**启动容器；取证分三层，全部在既有接缝上：

- **容器配置 dict**（纯函数 ``workspace.docker_container_config``）：网络模式与 bind mount 都在
  同一份配置里，于是「默认无网」不是口头约定；
- **创建路径**：``ProjectDockerWorkspace._create_and_start_container`` 用一个进程内 client 替身
  跑一次，断言它交给 ``create_or_replace`` 的 ``HostConfig.NetworkMode``（上游 2.0.10 没有网络
  参数，所以这条覆写必须自证）；
- **工具闸门**（``web.EgressPolicy``）：开关关闭 / host 不在白名单 ⇒ **零请求** + 可读错误；
  白名单内的 host 照常发出请求。传输层是既有 ``HttpTransport`` 接缝的进程内替身。

有 daemon 的端到端判据仍留在 ``test_docker_workspace.py``（不 mock 成功路径那一族）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agentscope.message import ToolResultState
from alibot import workspace as workspace_module
from alibot.db import open_database
from alibot.web import (
    EgressPolicy,
    HttpRequest,
    HttpResponse,
    fetch_tool,
    search_tool,
)
from alibot.workspace import (
    ProjectDockerWorkspace,
    WorkspaceRootViolation,
    WorkspaceSettings,
    docker_container_config,
    docker_global_binds,
    docker_global_member_home,
    docker_global_skills_home,
    docker_network_mode,
    global_home_root,
    resolve_in_container_root,
)

FETCH_URL = "https://api.example.com/docs"
OFF_LIST_URL = "https://other.example.net/x"


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def _settings(root: Path, **overrides: Any) -> WorkspaceSettings:
    return WorkspaceSettings(root=str(root), backend="docker", **overrides)


class _RecordingTransport:
    """出网传输替身：记录请求，回一段可读的 HTML（不发一个包）。"""

    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(status=200, body=b"<html><body>page</body></html>", url=request.url)


class _StubContainer:
    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True


class _StubContainers:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.container = _StubContainer()

    async def create_or_replace(self, *, name: str, config: dict[str, Any]) -> Any:
        self.calls.append({"name": name, "config": config})
        return self.container


class _StubClient:
    def __init__(self) -> None:
        self.containers = _StubContainers()


def _text(chunk: Any) -> str:
    return "\n".join(getattr(block, "text", "") for block in chunk.content)


# ─── 1. 容器配置：默认无网，显式允许才接网络 ─────────────────────────────────
def test_container_config_defaults_to_no_network_and_keeps_the_bind_mount(tmp_path: Path) -> None:
    """出网开关关闭（缺省）⇒ ``NetworkMode = none``；``/workspace`` 仍第一，后接两条只读全局 bind。"""
    member_home = tmp_path / "global" / "agents" / "mem-235"
    skills_home = tmp_path / "global" / "skills"
    member_home.mkdir(parents=True)
    skills_home.mkdir()
    config = docker_container_config(
        image_tag="alibot-test:1",
        workspace_id="alibot-mem--sess",
        env={"LANG": "C.UTF-8"},
        host_workdir=str(tmp_path),
        network_mode=docker_network_mode(False),
        global_binds=(
            (str(member_home), docker_global_member_home("mem-235")),
            (str(skills_home), docker_global_skills_home()),
        ),
    )

    assert config["HostConfig"]["NetworkMode"] == "none"
    assert config["HostConfig"]["Binds"] == [
        f"{tmp_path}:/workspace:rw",
        f"{member_home}:/alibot-home/agents/mem-235:ro",
        f"{skills_home}:/alibot-home/skills:ro",
    ]
    assert config["WorkingDir"] == "/workspace"
    assert config["Image"] == "alibot-test:1"


def test_the_egress_switch_is_the_only_thing_that_attaches_a_network() -> None:
    """两态：关 = ``none``，开 = 一个显式网络名（Docker 没有按 host 过滤这一档）。"""
    assert docker_network_mode(False) == "none"
    assert docker_network_mode(True) == workspace_module.DOCKER_EGRESS_NETWORK
    assert docker_network_mode(True) != "none"


# ─── 2. 创建路径：覆写把网络模式交给 create_or_replace ───────────────────────
def test_the_creation_path_passes_the_network_mode_to_the_container(tmp_path: Path) -> None:
    """上游没有网络参数，因此这条覆写必须自己把 ``NetworkMode`` 写进配置（替身 client 取证）。"""
    member_home = tmp_path / "global" / "agents" / "mem-235"
    skills_home = tmp_path / "global" / "skills"
    member_home.mkdir(parents=True)
    skills_home.mkdir()
    workspace = ProjectDockerWorkspace(workspace_id="alibot-mem--sess", host_workdir=str(tmp_path))
    workspace._image_tag = "alibot-test:1"
    workspace.global_binds = (
        (str(member_home), docker_global_member_home("mem-235")),
        (str(skills_home), docker_global_skills_home()),
    )
    client = _StubClient()
    workspace._client = client

    _run(workspace._create_and_start_container())
    assert client.containers.calls[-1]["config"]["HostConfig"]["NetworkMode"] == "none"
    assert client.containers.calls[-1]["config"]["HostConfig"]["Binds"] == [
        f"{tmp_path}:/workspace:rw",
        f"{member_home}:/alibot-home/agents/mem-235:ro",
        f"{skills_home}:/alibot-home/skills:ro",
    ]
    assert client.containers.container.started is True

    workspace.docker_egress = True
    _run(workspace._create_and_start_container())
    assert (
        client.containers.calls[-1]["config"]["HostConfig"]["NetworkMode"]
        == workspace_module.DOCKER_EGRESS_NETWORK
    )


def test_settings_carry_the_channel_egress_switch() -> None:
    """读路径把频道开关带进装配配置；缺省（旧的库读路径）仍是「无网」。"""
    assert WorkspaceSettings.from_mapping({"root": "/tmp", "backend": "docker"}).docker_egress is False
    assert (
        WorkspaceSettings.from_mapping(
            {"root": "/tmp", "backend": "docker", "docker_egress": True}
        ).docker_egress
        is True
    )


# ─── 3. 库读路径：白名单只来自 fetch 的 allow 规则（D3） ─────────────────────
def _library(tmp_path: Path) -> Any:
    database = open_database(tmp_path / "data")
    agent = database.create_agent("小明", "工程师", "openai:stub-model", "简短回复。")
    channel = database.create_channel("容器频道", member_ids=[agent["id"]])
    return database, agent, channel


def test_the_whitelist_comes_from_fetch_allow_rules_only(tmp_path: Path) -> None:
    """D3：host 白名单 = ``permission_rule(fetch, allow, pattern=<host>)`` 的 host 集合。

    三种「不算白名单」的行同时在场：空 pattern（= 该工具的全部调用，不产生 host）、``ask`` /
    ``deny`` 行为、以及别的工具名。Agent 级的同键 allow 覆盖频道级的 deny（合并仍只有一处实现）。
    """
    database, agent, channel = _library(tmp_path)
    try:
        root = tmp_path / "project"
        root.mkdir()
        database.update_channel_config(
            channel["id"],
            docker_egress=True,
            workspace_backend="docker",
            workspace_root=str(root),
        )
        database.create_permission_rule(
            scope_kind="channel", scope_id=channel["id"], tool_name="fetch", pattern="", behavior="allow"
        )
        database.create_permission_rule(
            scope_kind="channel",
            scope_id=channel["id"],
            tool_name="fetch",
            pattern="https://Docs.Example.com/a?b=1",
            behavior="deny",
        )
        database.create_permission_rule(
            scope_kind="agent",
            scope_id=agent["id"],
            tool_name="fetch",
            pattern="docs.example.com:8443",
            behavior="allow",
        )
        database.create_permission_rule(
            scope_kind="channel",
            scope_id=channel["id"],
            tool_name="Read",
            pattern="notes.md",
            behavior="allow",
        )
        database.create_permission_rule(
            scope_kind="channel",
            scope_id=channel["id"],
            tool_name="fetch",
            pattern="ask.example.com",
            behavior="ask",
        )

        settings = database.web_settings_for_target(channel["id"], agent["id"])

        assert settings is not None
        assert settings["backend"] == "docker"
        assert settings["docker_egress"] is True
        # 空 pattern 不产生 host（否则就成了「允许一切」）；ask / 别的工具名同样不进来。
        assert settings["egress_hosts"] == ("docs.example.com",)
        workspace_settings = database.workspace_settings_for_target(channel["id"], agent["id"])
        assert workspace_settings is not None
        assert workspace_settings["docker_egress"] is True
    finally:
        database.close()


# ─── 4. 工具闸门：关闭 / 白名单外 ⇒ 零请求 + 可读错误 ────────────────────────
def _search(transport: _RecordingTransport, egress: EgressPolicy | None) -> Any:
    tool = search_tool(
        provider="brave",
        environ={"BRAVE_API_KEY": "brave-issue-187"},
        transport=transport,
        egress=egress,
    )
    return _run(tool(query="issue 187"))


def _fetch(url: str, transport: _RecordingTransport, egress: EgressPolicy | None) -> Any:
    tool = fetch_tool(transport=transport, egress=egress)
    return _run(tool(url=url))


def test_egress_off_blocks_search_with_zero_requests() -> None:
    """容器默认无网：搜索工具返回**说明容器网络策略**的可读错误，且一个请求都不发。"""
    transport = _RecordingTransport()

    chunk = _search(transport, EgressPolicy(enabled=False))

    assert chunk.state == ToolResultState.ERROR
    assert "容器网络策略" in _text(chunk)
    assert transport.requests == []


def test_egress_off_blocks_fetch_with_zero_requests() -> None:
    transport = _RecordingTransport()

    chunk = _fetch(FETCH_URL, transport, EgressPolicy(enabled=False))

    assert chunk.state == ToolResultState.ERROR
    assert "容器网络策略" in _text(chunk)
    assert transport.requests == []


def test_egress_on_still_refuses_hosts_outside_the_whitelist() -> None:
    """显式允许后仍只放行白名单 host；白名单外可读拒绝、零请求。"""
    transport = _RecordingTransport()
    egress = EgressPolicy(enabled=True, allowed_hosts=frozenset({"api.example.com"}))

    chunk = _fetch(OFF_LIST_URL, transport, egress)

    assert chunk.state == ToolResultState.ERROR
    assert "other.example.net" in _text(chunk)
    assert "api.example.com" in _text(chunk), "可读原因里要给出当前白名单"
    assert transport.requests == []


def test_egress_on_allows_a_whitelisted_host() -> None:
    """白名单内的 host 成功：请求真的发出去了（对照组，证明探针是活的）。"""
    transport = _RecordingTransport()
    egress = EgressPolicy(enabled=True, allowed_hosts=frozenset({"api.example.com"}))

    chunk = _fetch(FETCH_URL, transport, egress)

    assert chunk.state == ToolResultState.SUCCESS
    assert [request.url for request in transport.requests] == [FETCH_URL]


def test_local_backend_sends_the_request_as_before() -> None:
    """本地后端不注入策略（``egress=None``）：行为与 #186 逐字相同——票面「不改本地后端行为」。"""
    transport = _RecordingTransport()

    assert _fetch(FETCH_URL, transport, None).state == ToolResultState.SUCCESS
    assert len(transport.requests) == 1


# ─── 5. 不回落：daemon 缺失仍是「没有工作区 + 可读原因」 ────────────────────
def test_container_egress_never_falls_back_to_a_local_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """出网开关打开也不改变失败纪律：Docker 起不来就失败，**不**改走本地装配。"""
    root = tmp_path / "project"
    root.mkdir()
    member = "mem-issue-187"
    monkeypatch.setattr(
        workspace_module,
        "assemble_local_workspace",
        _explode_local,
    )

    async def explode(self: Any) -> None:
        from aiodocker.exceptions import DockerError

        raise DockerError(900, "Cannot connect to Docker Engine via unix:///tmp/issue-187.sock")

    monkeypatch.setattr(workspace_module.ProjectDockerWorkspace, "initialize", explode)

    async def assemble() -> None:
        with pytest.raises(workspace_module.DockerWorkspaceUnavailable) as caught:
            await workspace_module.assemble_workspace(
                _settings(root, docker_egress=True), member_id=member, session_id="sess", locale="zh-CN"
            )
        assert "Docker" in str(caught.value)

    try:
        _run(assemble())
        reason = workspace_module.docker_unavailable_reason(member)
        assert reason and "Docker" in reason
    finally:
        workspace_module.clear_docker_failure(member)


async def _explode_local(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Docker 后端失败时不允许回落到本地工作区")


# ─── 6. 全局层只读挂载（#235）：成员家目录 + 公共技能库，固定顺序 ───────────
def _home_with_global_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 HOME 指向临时目录，返回 ``<HOME>/.alibot``（全局层根）。测试不碰真实 ``~/.alibot``。"""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    return Path(global_home_root())


def test_global_binds_are_member_home_then_public_skills_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个子路径互不冲突、都带 ``:ro``，顺序固定在 ``/workspace:rw`` 之后。"""
    global_root = _home_with_global_layer(tmp_path, monkeypatch)
    member_home = global_root / "agents" / "mem-235"
    skills_home = global_root / "skills"
    member_home.mkdir(parents=True)
    skills_home.mkdir()

    binds = docker_global_binds("mem-235")

    assert binds == (
        (str(member_home), "/alibot-home/agents/mem-235"),
        (str(skills_home), "/alibot-home/skills"),
    )
    config = docker_container_config(
        image_tag="alibot-test:1",
        workspace_id="alibot-mem-235",
        env=None,
        host_workdir=str(tmp_path),
        network_mode=docker_network_mode(False),
        global_binds=binds,
    )
    assert config["HostConfig"]["Binds"] == [
        f"{tmp_path}:/workspace:rw",
        f"{member_home}:/alibot-home/agents/mem-235:ro",
        f"{skills_home}:/alibot-home/skills:ro",
    ]


def test_missing_global_directories_are_skipped_and_never_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宿主目录不存在 ⇒ 跳过该条 bind，且解析本身不创建目录（不能依赖 daemon 兜底）。"""
    global_root = _home_with_global_layer(tmp_path, monkeypatch)
    assert docker_global_binds("mem-235") == ()
    assert not global_root.exists(), "解析 bind 不得创建全局根"

    member_home = global_root / "agents" / "mem-235"
    member_home.mkdir(parents=True)
    assert docker_global_binds("mem-235") == ((str(member_home), "/alibot-home/agents/mem-235"),)
    assert not (global_root / "skills").exists(), "缺失的公共技能库不得被顺手创建"

    skills_home = global_root / "skills"
    skills_home.mkdir()
    assert docker_global_binds("mem-235") == (
        (str(member_home), "/alibot-home/agents/mem-235"),
        (str(skills_home), "/alibot-home/skills"),
    )


def test_the_data_directory_never_enters_the_container_and_the_global_root_is_not_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据目录（库 / 附件 / 台账）不进容器；不得用整根 ``~/.alibot`` 代替两条子路径。"""
    global_root = _home_with_global_layer(tmp_path, monkeypatch)
    (global_root / "data").mkdir(parents=True)
    (global_root / "data" / "alibot.db").write_text("secret\n", encoding="utf-8")
    (global_root / "agents" / "mem-235").mkdir(parents=True)
    (global_root / "skills").mkdir()

    binds = docker_global_binds("mem-235")
    host_sources = {host for host, _target in binds}
    assert str(global_root) not in host_sources, "不得挂 ~/.alibot 整根"
    assert str(global_root / "data") not in host_sources

    config = docker_container_config(
        image_tag="alibot-test:1",
        workspace_id="alibot-mem-235",
        env=None,
        host_workdir=str(tmp_path),
        network_mode=docker_network_mode(False),
        global_binds=binds,
    )
    rendered = config["HostConfig"]["Binds"]
    assert rendered[0].endswith(":/workspace:rw"), "项目根仍排第一且可写"
    assert all(bind.endswith(":ro") for bind in rendered[1:])
    assert not any("/data" in bind for bind in rendered)


def test_global_binds_only_append_to_an_unchanged_container_shape(tmp_path: Path) -> None:
    """镜像 / 命令 / 工作目录 / 标签 / 环境 / 网络模式逐字不变，只追加 Binds。"""
    project = tmp_path / "project"
    project.mkdir()
    without = docker_container_config(
        image_tag="alibot-test:1",
        workspace_id="alibot-mem-235",
        env={"LANG": "C.UTF-8"},
        host_workdir=str(project),
        network_mode="none",
    )
    with_binds = docker_container_config(
        image_tag="alibot-test:1",
        workspace_id="alibot-mem-235",
        env={"LANG": "C.UTF-8"},
        host_workdir=str(project),
        network_mode="none",
        global_binds=(
            ("/host/agents/mem-235", "/alibot-home/agents/mem-235"),
            ("/host/skills", "/alibot-home/skills"),
        ),
    )

    for key in ("Image", "Cmd", "WorkingDir", "Labels", "Env"):
        assert with_binds[key] == without[key]
    assert with_binds["HostConfig"]["NetworkMode"] == without["HostConfig"]["NetworkMode"] == "none"
    assert with_binds["HostConfig"]["Binds"][0] == without["HostConfig"]["Binds"][0]
    assert with_binds["HostConfig"]["Binds"][1:] == [
        "/host/agents/mem-235:/alibot-home/agents/mem-235:ro",
        "/host/skills:/alibot-home/skills:ro",
    ]


def test_the_global_mount_point_is_still_not_a_file_tool_root(tmp_path: Path) -> None:
    """``/alibot-home`` 不是 Read/Write/Edit/Glob/Grep 的合法输入（回归 #230 的契约）。"""
    project = tmp_path / "project"
    project.mkdir()
    assert resolve_in_container_root("/workspace", str(project)) == "/workspace"
    for raw in (
        "/alibot-home",
        "/alibot-home/agents/mem-235/rules/AGENTS.md",
        "/alibot-home/skills/shared",
    ):
        with pytest.raises(WorkspaceRootViolation):
            resolve_in_container_root(raw, str(project))
