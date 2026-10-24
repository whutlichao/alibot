"""#150：Docker 后端的装配层取证（真实 Docker daemon）。

本文件不 mock Docker 成功路径：有 daemon 时真实构建/复用镜像、启动容器、在容器内跑
Bash/Read/Write，并核对主机侧挂载与隔离。没有 daemon 的机器上整组跳过（Docker 是可选后端，
不能让 CI 依赖它）；“无 Docker”的失败形状用可控替身验证异常映射与 ``offline`` 原因。

覆盖票面四条：
1. ``agent_config.workspace_backend='docker'`` 时启动容器，``/workspace`` = 频道项目根；
2. 容器内 Bash/Read/Write 可用；容器外主机路径不可见、``/tmp`` 与 ``/root`` 私有；
3. 空闲回收、进程退出清理（``shutdown_docker_workspaces``）、reset 保留 host_workdir、镜像复用；
4. 无 Docker/初始化失败 ⇒ 可读原因 + ``offline``，不回落本地；远端镜像需要 scope 确认。
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agentscope.message import TextBlock, ToolResultState
from agentscope.model import ChatResponse
from alibot import workspace as workspace_module
from alibot.db import open_database
from alibot.events import EventBus
from alibot.orchestration import WakeOrchestrator
from alibot.workspace import (
    DOCKER_ENV_ALLOWLIST,
    DockerWorkspaceUnavailable,
    WorkspaceSettings,
    assemble_workspace,
    docker_global_member_home,
    docker_global_skills_home,
    docker_unavailable_reason,
    global_home_root,
    global_member_home,
    release_workspace_mount,
    reset_docker_workspace,
    session_partition_id,
    shutdown_docker_workspaces,
    sweep_idle_docker_workspaces,
    sweep_stale_docker_workspaces,
)

from tests.test_approval_seam import _ScriptedModel, _StubModelSeam


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def _docker_available() -> bool:
    """用 CLI 探活：它会尊重 Docker context（研究 #134 §五）。"""
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


HAS_DOCKER = _docker_available()
requires_docker = pytest.mark.skipif(not HAS_DOCKER, reason="Docker daemon is not available")


@pytest.fixture
def docker_root(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "seed.txt").write_text("host-seed\n", encoding="utf-8")
    yield root
    # 每个用例结束都清掉本进程登记的容器（容器名带用例唯一 id）。
    _run(shutdown_docker_workspaces())


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _settings(root: Path, **overrides: Any) -> WorkspaceSettings:
    return WorkspaceSettings(root=str(root), backend="docker", **overrides)


def _tool(mount: Any, name: str) -> Any:
    return next(tool for tool in mount.tools if tool.name == name)


async def _latest_chunk(tool: Any, **kwargs: Any) -> Any:
    result = await tool(**kwargs)
    chunks = [chunk async for chunk in result] if hasattr(result, "__aiter__") else [result]
    assert chunks, f"{tool.name} 没有返回任何 chunk"
    return chunks[-1]


def _text(chunk: Any) -> str:
    return "\n".join(getattr(block, "text", "") for block in chunk.content)


async def _container_exists(name: str) -> bool:
    import aiodocker

    client = aiodocker.Docker()
    try:
        await client.containers.get(name)
        return True
    except Exception:
        return False
    finally:
        await client.close()


# ─── 1. 容器启动、/workspace = 频道根、容器内工具可用 ─────────────────────
@requires_docker
def test_docker_mounts_the_channel_root_and_tools_work_inside_the_container(
    docker_root: Path,
) -> None:
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")

    async def scenario() -> None:
        mount = await assemble_workspace(
            _settings(docker_root),
            member_id=member,
            session_id=session,
            locale="zh-CN",
        )
        try:
            assert mount.reused is False
            assert mount.container_name.startswith("as_ws_alibot-")
            assert {tool.name for tool in mount.tools} >= {
                "Bash",
                "Read",
                "Write",
                "Edit",
                "Glob",
                "Grep",
            }

            bash = _tool(mount, "Bash")
            chunk = await _latest_chunk(bash, command="pwd; cat /workspace/seed.txt")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert "/workspace" in _text(chunk)
            assert "host-seed" in _text(chunk)

            write = _tool(mount, "Write")
            chunk = await _latest_chunk(
                write, file_path="/workspace/from-tool.txt", content="written-by-tool\n"
            )
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert (docker_root / "from-tool.txt").read_text(encoding="utf-8") == "written-by-tool\n"

            chunk = await _latest_chunk(
                bash, command="printf 'written-by-bash\\n' > /workspace/from-bash.txt"
            )
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert (docker_root / "from-bash.txt").read_text(encoding="utf-8") == "written-by-bash\n"

            (docker_root / "from-host.txt").write_text("host-write\n", encoding="utf-8")
            chunk = await _latest_chunk(_tool(mount, "Read"), file_path="from-host.txt")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert "host-write" in _text(chunk)

            # .alibot 分区落在 host_workdir，与本地后端一致。
            assert (docker_root / ".alibot" / "agents" / member).is_dir()
            assert (docker_root / ".alibot" / "sessions" / session).is_dir()
        finally:
            await release_workspace_mount(mount)

    _run(scenario())


# ─── 1b. 全局层挂载（#235 / ADR-0031 / ADR-0034）：成员家目录与公共技能库都 ro，数据目录不可见 ─
@requires_docker
def test_docker_mounts_the_member_home_and_the_skill_library_read_only(
    docker_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成员家目录只读（记忆由宿主进程内的 ReMe 写）、公共技能库只读；``data/`` 不可见。"""
    home = docker_root.parent / "home"
    monkeypatch.setenv("HOME", str(home))
    member = _unique("mem")
    session = session_partition_id(member, "ch_global")
    member_home = Path(global_member_home(member))
    member_home.mkdir(parents=True)
    (member_home / "AGENTS.md").write_text("全局基座规则\n", encoding="utf-8")
    (member_home / "MEMORY.md").write_text("全局记忆\n", encoding="utf-8")
    skills_home = Path(global_home_root()) / "skills"
    skills_home.mkdir(parents=True)
    (skills_home / "shared.txt").write_text("公共技能\n", encoding="utf-8")
    data_home = Path(global_home_root()) / "data"
    data_home.mkdir()
    (data_home / "alibot.db").write_text("secret\n", encoding="utf-8")
    before = sorted(path.name for path in Path(global_home_root()).iterdir())

    async def scenario() -> None:
        mount = await assemble_workspace(
            _settings(docker_root),
            member_id=member,
            session_id=session,
            locale="zh-CN",
        )
        try:
            bash = _tool(mount, "Bash")
            target = docker_global_member_home(member)

            # 容器轮次里全局规则基座可读（与 local 后端同一份宿主文件，扁平路径）。
            chunk = await _latest_chunk(bash, command=f"cat {target}/AGENTS.md")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert "全局基座规则" in _text(chunk)

            chunk = await _latest_chunk(bash, command=f"cat {docker_global_skills_home()}/shared.txt")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert "公共技能" in _text(chunk)

            # ADR-0034 §四：成员家目录仍 ``:ro``——记忆由宿主侧进程内的 ReMe 写入，容器不需要写权限。
            chunk = await _latest_chunk(
                bash, command=f"printf 'written' > {target}/topic.md 2>&1 || echo write-denied"
            )
            assert "write-denied" in _text(chunk), "成员家目录必须是只读挂载"
            assert not (member_home / "topic.md").exists()
            # 公共技能库仍只读。
            chunk = await _latest_chunk(
                bash,
                command=f"printf x > {docker_global_skills_home()}/written.txt 2>&1 || echo write-denied",
            )
            assert "write-denied" in _text(chunk), "公共技能库必须是只读挂载"

            # 数据目录（库 / 附件 / 台账）不进容器。
            chunk = await _latest_chunk(
                bash, command="test -e /alibot-home/data && echo visible || echo absent"
            )
            assert "absent" in _text(chunk), "数据目录不得进容器"

            # 文件工具根仍只认 /workspace：``/alibot-home`` 不是文件工具的输入面。
            chunk = await _latest_chunk(_tool(mount, "Read"), file_path=f"{target}/AGENTS.md")
            assert chunk.state is ToolResultState.ERROR, "/alibot-home 不是文件工具的合法输入"
        finally:
            await release_workspace_mount(mount)

    _run(scenario())
    # 只读挂载下容器什么都没写进来：全局根的新增条目只有测试自己建的 ``skills/`` 与 ``data/``。
    after = {path.name for path in Path(global_home_root()).iterdir()}
    assert after - set(before) <= {"skills", "data"}


# ─── 2. 隔离：容器外路径不可见、/tmp 与 /root 私有；文件工具仍锚在 /workspace ──
@requires_docker
def test_container_filesystem_is_isolated_and_file_tools_are_rooted(
    docker_root: Path, tmp_path: Path
) -> None:
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")

    async def scenario() -> None:
        mount = await assemble_workspace(
            _settings(docker_root),
            member_id=member,
            session_id=session,
            locale="zh-CN",
        )
        try:
            bash = _tool(mount, "Bash")
            marker = _unique("marker")

            # 主机 tmp_path 不是挂载点：容器里看不到。
            chunk = await _latest_chunk(bash, command=f"test -e {tmp_path} && echo visible || echo invisible")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert "invisible" in _text(chunk)

            # 容器私有 /tmp：主机看不到同名文件。
            chunk = await _latest_chunk(bash, command=f"printf 'private' > /tmp/{marker}.txt")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert not (Path("/tmp") / f"{marker}.txt").exists()

            # 容器私有 /root：主机工作目录里没有 root/。
            chunk = await _latest_chunk(bash, command=f"printf 'root' > /root/{marker}.txt")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert not (docker_root / "root" / f"{marker}.txt").exists()

            # 文件工具在容器里仍是 root shell，装配层必须把路径锚到 /workspace。
            chunk = await _latest_chunk(_tool(mount, "Read"), file_path="/etc/hostname")
            assert chunk.state is ToolResultState.ERROR, "容器内 /etc 也要被根检查拒绝"

            chunk = await _latest_chunk(_tool(mount, "Write"), file_path="/root/escape.txt", content="nope")
            assert chunk.state is ToolResultState.ERROR
            chunk = await _latest_chunk(
                _tool(mount, "Write"), file_path="/workspace/../../escape.txt", content="nope"
            )
            assert chunk.state is ToolResultState.ERROR

            chunk = await _latest_chunk(_tool(mount, "Read"), file_path="../seed.txt")
            assert chunk.state is ToolResultState.ERROR

            chunk = await _latest_chunk(_tool(mount, "Read"), file_path="seed.txt")
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert "host-seed" in _text(chunk)
        finally:
            await release_workspace_mount(mount)

    _run(scenario())


# ─── 3. 复用、空闲回收、进程退出清理、reset 保留 host_workdir ──────────────
@requires_docker
def test_workspace_reuses_the_container_then_reclaims_it_when_idle(
    docker_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")
    settings = _settings(docker_root)

    async def acquire() -> Any:
        mount = await assemble_workspace(settings, member_id=member, session_id=session)
        await release_workspace_mount(mount)
        return mount

    async def scenario() -> None:
        first = await acquire()
        second = await acquire()
        assert second.reused is True
        assert second.container_name == first.container_name
        assert await _container_exists(first.container_name)

        # 空闲超过 TTL：后台回收删除容器，host_workdir 不受影响。
        monkeypatch.setattr(workspace_module, "DOCKER_IDLE_TTL_SECONDS", 0.0)
        removed = await sweep_idle_docker_workspaces(now=time.monotonic() + 1)
        assert first.container_name in removed
        assert not await _container_exists(first.container_name)
        assert (docker_root / "seed.txt").read_text(encoding="utf-8") == "host-seed\n"

        third = await acquire()
        assert third.reused is False
        assert third.container_name == first.container_name  # 稳定 id，create_or_replace
        assert await _container_exists(third.container_name)

        # reset：删容器可写层，保留 host_workdir 与项目文件。
        (docker_root / "keep.txt").write_text("keep\n", encoding="utf-8")
        assert await reset_docker_workspace(settings, member_id=member, session_id=session) is True
        assert not await _container_exists(third.container_name)
        assert (docker_root / "keep.txt").read_text(encoding="utf-8") == "keep\n"
        assert (docker_root / "seed.txt").read_text(encoding="utf-8") == "host-seed\n"

    _run(scenario())


@requires_docker
def test_shutdown_deletes_running_containers(docker_root: Path) -> None:
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")

    async def scenario() -> None:
        mount = await assemble_workspace(_settings(docker_root), member_id=member, session_id=session)
        await release_workspace_mount(mount)
        removed = await shutdown_docker_workspaces()
        assert mount.container_name in removed
        assert not await _container_exists(mount.container_name)

    _run(scenario())


@requires_docker
def test_startup_sweep_removes_orphan_alibot_containers(tmp_path: Path) -> None:
    """模拟 os._exit 留下的容器：启动清扫按 workspace id 前缀删除。"""
    workspace_id = _unique("alibot-stale")
    name = f"as_ws_{workspace_id}"

    async def scenario() -> None:
        import aiodocker

        client = aiodocker.Docker()
        container = await client.containers.create(
            {
                "Image": "python:3.11-slim",
                "Cmd": ["sleep", "infinity"],
                "Labels": {
                    "agentscope.workspace": "true",
                    "agentscope.workspace.id": workspace_id,
                },
            },
            name=name,
        )
        await container.start()
        await client.close()
        assert await _container_exists(name)

        removed = await sweep_stale_docker_workspaces()
        assert name in removed
        assert not await _container_exists(name)

        # 非本仓前缀（例如 #134 探针留下的容器）不在清扫面内。
        probe = f"as_ws_{_unique('probe-keep')}"
        client = aiodocker.Docker()
        container = await client.containers.create(
            {
                "Image": "python:3.11-slim",
                "Cmd": ["sleep", "infinity"],
                "Labels": {
                    "agentscope.workspace": "true",
                    "agentscope.workspace.id": _unique("probe"),
                },
            },
            name=probe,
        )
        await container.start()
        await client.close()
        try:
            await sweep_stale_docker_workspaces()
            assert await _container_exists(probe)
        finally:
            client = aiodocker.Docker()
            try:
                target = await client.containers.get(probe)
                await target.delete(force=True)
            finally:
                await client.close()

    _run(scenario())


# ─── 4. 无 Docker / 初始化失败：可读原因、offline、不回落本地 ──────────────
def test_failed_initialization_is_readable_and_never_falls_back_to_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 本机 provider：可用性检查会先过模型/凭证/远端确认，才轮到 Docker 原因。
    docker_root = tmp_path / "project"
    docker_root.mkdir()
    (docker_root / "inside.txt").write_text("inside\n", encoding="utf-8")
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")
    settings = _settings(docker_root)
    workspace_module.clear_docker_failure(member)

    async def explode(self: Any) -> None:
        from aiodocker.exceptions import DockerError

        raise DockerError(900, "Cannot connect to Docker Engine via unix:///tmp/issue-150.sock")

    monkeypatch.setattr(workspace_module.ProjectDockerWorkspace, "initialize", explode)

    async def assemble() -> None:
        with pytest.raises(DockerWorkspaceUnavailable) as caught:
            await assemble_workspace(settings, member_id=member, session_id=session, locale="zh-CN")
        assert "Docker" in str(caught.value)
        assert "Cannot connect" in str(caught.value)

    _run(assemble())

    # 失败短 TTL 缓存 → 可用性离线，原因就是可读的 Docker 原因。
    reason = docker_unavailable_reason(member)
    assert reason and "Cannot connect" in reason

    # 真 seam：Docker 不可用时继续对话、原因可读；不回落到本地项目工具。
    data_dir = tmp_path / "data"
    database = open_database(data_dir)
    try:
        agent = database.create_agent("小明", "工程师", "ollama:stub-model", "简短回复。")
        database.update_agent(agent["id"], enabled=True)
        channel = database.create_channel("项目频道", member_ids=[agent["id"]])
        with sqlite3.connect(database.path) as connection:
            connection.execute(
                "UPDATE channel SET workspace_root = ?, workspace_backend = 'docker' WHERE id = ?",
                (str(docker_root), channel["id"]),
            )
            connection.commit()
        database.close()
        database = open_database(data_dir)
        agent = database.get_agent(agent["id"])
        seam = _StubModelSeam(
            _ScriptedModel([[ChatResponse(content=[TextBlock(text="收到")], is_last=True)]]),
            timeout_seconds=5.0,
            workspace_of=database.workspace_settings_for_target,
        )
        outcome = seam.run_round(
            agent,
            [{"seq": 1, "kind": "human", "body": "你好", "author": {"id": "owner"}}],
            mentioned=False,
            target_id=channel["id"],
        )
        assert outcome.status == "replied"
        assert "Docker" in outcome.reason and "Cannot connect" in outcome.reason

        orchestrator = WakeOrchestrator(
            database,
            seam,
            EventBus(),
            workspace_unavailable=docker_unavailable_reason,
        )
        availability = orchestrator.availability(agent)
        assert availability.ok is True
        assert "Cannot connect" in availability.reason
    finally:
        database.close()
        workspace_module.clear_docker_failure(member)


def test_remote_image_requires_consent_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docker_root = tmp_path / "project"
    docker_root.mkdir()
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")
    image = "registry.example/agent-runtime:1.2.3"
    settings = _settings(docker_root, docker_image=image, docker_image_consented=False)
    workspace_module.clear_docker_failure(member)

    async def absent(image_name: str) -> bool:
        assert image_name == image
        return False

    monkeypatch.setattr(workspace_module, "_image_present_locally", absent)

    async def scenario() -> None:
        with pytest.raises(DockerWorkspaceUnavailable) as caught:
            await assemble_workspace(settings, member_id=member, session_id=session, locale="zh-CN")
        assert image in str(caught.value)
        assert "确认" in str(caught.value)

    _run(scenario())
    assert image in (docker_unavailable_reason(member) or "")
    workspace_module.clear_docker_failure(member)


def test_db_scope_consent_gate_reaches_workspace_settings(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database = open_database(data_dir)
    image = "registry.example/agent-runtime:1.2.3"
    try:
        agent = database.create_agent("小明", "工程师", "ollama:stub-model", "简短回复。")
        channel = database.create_channel("项目频道", member_ids=[agent["id"]])
        with sqlite3.connect(database.path) as connection:
            connection.execute(
                "UPDATE channel SET workspace_root = ?, workspace_backend = 'docker' WHERE id = ?",
                (str(tmp_path), channel["id"]),
            )
            connection.execute(
                "UPDATE agent_config SET docker_image = ? WHERE member_id = ?",
                (image, agent["id"]),
            )
            connection.commit()

        settings = database.workspace_settings_for_target(channel["id"], agent["id"])
        assert settings is not None
        assert settings["backend"] == "docker"
        assert settings["docker_image"] == image
        assert settings["docker_image_consented"] is False

        record = database.record_remote_consent_scope("docker", image)
        assert record["scope_kind"] == "docker"
        assert database.has_remote_consent_scope("docker", image) is True
        settings = database.workspace_settings_for_target(channel["id"], agent["id"])
        assert settings is not None and settings["docker_image_consented"] is True
    finally:
        database.close()


def test_changed_docker_image_discards_the_pooled_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改 ``docker_image`` 后旧镜像容器不得复用：池条目被移除、容器被删除、按新配置重建。"""
    root = tmp_path / "project"
    root.mkdir()
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")
    workspace_id = workspace_module.docker_workspace_id(
        workspace_module.safe_partition_id(member), workspace_module.safe_partition_id(session)
    )
    key = (
        workspace_module.safe_partition_id(member),
        workspace_module.safe_partition_id(session),
    )
    old = workspace_module._DockerEntry(
        key=key,
        workspace=object(),  # type: ignore[arg-type]  # 这条用例只验证池的失效判定
        settings=_settings(root, docker_image="old:1"),
        root=str(root),
        member_id=key[0],
        session_id=key[1],
        workspace_id=workspace_id,
        container_name=f"as_ws_{workspace_id}",
        last_used=0.0,
        active=0,
        image="old:1",
    )
    deleted: list[str] = []

    async def fake_delete(name: str) -> None:
        deleted.append(name)

    async def absent(image_name: str) -> bool:
        assert image_name == "new:2"
        return False

    monkeypatch.setattr(workspace_module, "_delete_container", fake_delete)
    monkeypatch.setattr(workspace_module, "_image_present_locally", absent)
    workspace_module.clear_docker_failure(key[0])
    with workspace_module._pool_lock:
        workspace_module._pool[key] = old
    try:
        with pytest.raises(DockerWorkspaceUnavailable):
            _run(
                assemble_workspace(
                    _settings(root, docker_image="new:2", docker_image_consented=False),
                    member_id=member,
                    session_id=session,
                    locale="zh-CN",
                )
            )
    finally:
        with workspace_module._pool_lock:
            workspace_module._pool.pop(key, None)
        workspace_module.clear_docker_failure(key[0])

    assert deleted == [old.container_name], "旧镜像容器必须被删掉，而不是被复用"
    assert key not in workspace_module._pool


def test_a_global_layer_created_later_discards_the_pooled_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全局目录在容器建立后才出现：bind 规格变化 ⇒ 旧容器不得复用（否则新挂载永远不生效）。"""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "project"
    root.mkdir()
    member = _unique("mem")
    session = session_partition_id(member, "ch_global_binds")
    workspace_id = workspace_module.docker_workspace_id(
        workspace_module.safe_partition_id(member), workspace_module.safe_partition_id(session)
    )
    key = (
        workspace_module.safe_partition_id(member),
        workspace_module.safe_partition_id(session),
    )
    old = workspace_module._DockerEntry(
        key=key,
        workspace=object(),  # type: ignore[arg-type]  # 这条用例只验证池的失效判定
        settings=_settings(root, docker_image="img:1"),
        root=str(root),
        member_id=key[0],
        session_id=key[1],
        workspace_id=workspace_id,
        container_name=f"as_ws_{workspace_id}",
        last_used=0.0,
        active=0,
        image="img:1",
        global_binds=(),
    )
    # 容器已经按「没有全局层」建好；此后人手工建了成员家目录。
    Path(global_member_home(member)).mkdir(parents=True)
    deleted: list[str] = []

    async def fake_delete(name: str) -> None:
        deleted.append(name)

    async def absent(image_name: str) -> bool:
        assert image_name == "img:1"
        return False

    monkeypatch.setattr(workspace_module, "_delete_container", fake_delete)
    monkeypatch.setattr(workspace_module, "_image_present_locally", absent)
    workspace_module.clear_docker_failure(key[0])
    with workspace_module._pool_lock:
        workspace_module._pool[key] = old
    try:
        with pytest.raises(DockerWorkspaceUnavailable):
            _run(
                assemble_workspace(
                    _settings(root, docker_image="img:1", docker_image_consented=False),
                    member_id=member,
                    session_id=session,
                    locale="zh-CN",
                )
            )
    finally:
        with workspace_module._pool_lock:
            workspace_module._pool.pop(key, None)
        workspace_module.clear_docker_failure(key[0])

    assert deleted == [old.container_name], "bind 规格变化后旧容器必须被删掉，而不是被复用"
    assert key not in workspace_module._pool


def test_host_creates_the_alibot_layout_before_container_initialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI 回归：容器初始化会以 root 创建 ``.alibot``；host 必须先把成员分区建好。

    Linux runner 上 bind mount 的属主是容器 root，host 在容器初始化后再 ``makedirs``
    成员分区会拿到 ``PermissionError``。这里用「初始化时把 `.alibot/agents` 改成只读」
    复现同一个形状，并断言 host 布局在 ``initialize()`` 之前已经存在。
    """
    root = tmp_path / "project"
    root.mkdir()
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")
    seen: dict[str, bool] = {}

    class _FakeWorkspace:
        def __init__(self, **options: Any) -> None:
            self.options = options
            self.env = dict(options.get("env") or {})
            self._client = None
            self._gateway = None

        async def initialize(self) -> None:
            seen["agent_dir_before_init"] = (root / ".alibot" / "agents" / member).is_dir()
            seen["session_dir_before_init"] = (root / ".alibot" / "sessions" / session).is_dir()
            agents = root / ".alibot" / "agents"
            agents.mkdir(parents=True, exist_ok=True)
            agents.chmod(0o555)  # 模拟容器 root 创建出的不可写祖先

        async def close(self) -> None:
            return None

        def get_backend(self) -> Any:
            return object()

    monkeypatch.setattr(workspace_module, "ProjectDockerWorkspace", _FakeWorkspace)
    try:
        mount = _run(assemble_workspace(_settings(root), member_id=member, session_id=session))
        assert seen == {"agent_dir_before_init": True, "session_dir_before_init": True}
        assert Path(mount.agent_dir).is_dir()
    finally:
        (root / ".alibot" / "agents").chmod(0o755)
        with workspace_module._pool_lock:
            workspace_module._pool.clear()
        workspace_module.clear_docker_failure(member)


@requires_docker
def test_release_closes_the_gateway_and_the_docker_client(docker_root: Path) -> None:
    """一轮结束必须关掉 gateway（aiohttp UnixConnector）与 aiodocker client，避免 unraisable。"""
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")

    async def scenario() -> None:
        mount = await assemble_workspace(_settings(docker_root), member_id=member, session_id=session)
        assert mount.workspace._gateway is not None
        await release_workspace_mount(mount)
        assert mount.workspace._gateway is None
        assert mount.workspace._client is None

    _run(scenario())


@requires_docker
def test_env_is_an_allowlist_and_does_not_leak_process_secrets(
    docker_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = _unique("mem")
    session = session_partition_id(member, "ch_docker")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-issue-150-secret")
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    async def scenario() -> None:
        mount = await assemble_workspace(_settings(docker_root), member_id=member, session_id=session)
        try:
            assert set(mount.workspace.env) <= set(DOCKER_ENV_ALLOWLIST)
            assert "OPENAI_API_KEY" not in mount.workspace.env
            chunk = await _latest_chunk(
                _tool(mount, "Bash"), command='printf "%s" "${OPENAI_API_KEY:-absent}"'
            )
            assert chunk.state is not ToolResultState.ERROR, _text(chunk)
            assert "absent" in _text(chunk)
        finally:
            await release_workspace_mount(mount)

    _run(scenario())
