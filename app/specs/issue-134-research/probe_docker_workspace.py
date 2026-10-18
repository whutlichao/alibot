#!/usr/bin/env python3
"""End-to-end probe for AgentScope 2.0.10 ``DockerWorkspace``.

Run with the repository venv:

    .venv/bin/python app/specs/issue-134-research/probe_docker_workspace.py

The probe exercises the real public surface (``initialize`` → builtin
``Bash``/``Read``/``Write`` tools → ``close``) against a temporary host
directory. Every measurement is printed as one JSON object per line with
an ``event`` key so the report can quote individual readings.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agentscope.tool import Bash, ToolChunk
from agentscope.workspace import DockerWorkspace

CONTAINER_WORKDIR = "/workspace"


def emit(event: str, **payload: object) -> None:
    """Print one machine-readable result line."""
    print(json.dumps({"event": event, **payload}, sort_keys=True))
    sys.stdout.flush()


def docker(*args: str) -> str:
    """Run one docker CLI command and return stripped stdout."""
    return subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def drain_bash(tool: Bash, command: str, timeout: int = 120000) -> dict:
    """Execute one Bash tool call and flatten its chunks."""
    text: list[str] = []
    state = None
    started = time.perf_counter()
    async for chunk in tool.call(command, timeout=timeout):
        state = chunk.state
        for block in chunk.content:
            value = getattr(block, "text", None)
            if value is not None:
                text.append(value)
    return {
        "state": str(state),
        "text": "".join(text),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def main() -> int:
    """Run the probe and print the collected readings."""
    docker_version = json.loads(docker("version", "--format", "{{json .}}"))
    emit(
        "docker_version",
        client=docker_version["Client"]["Version"],
        server=docker_version["Server"]["Version"],
        server_os=docker_version["Server"]["Os"],
        server_arch=docker_version["Server"]["Arch"],
        api=docker_version["Server"]["ApiVersion"],
        context=os.environ.get("DOCKER_CONTEXT", "default"),
    )

    host_root = Path(tempfile.mkdtemp(prefix="as-ws-probe-"))
    seed = host_root / "seed.txt"
    seed.write_text("host-seed\n", encoding="utf-8")
    workspace_id = f"probe-{int(time.time())}"
    ws = DockerWorkspace(
        workspace_id=workspace_id,
        host_workdir=str(host_root),
        env={"PROBE_ENV": "probe-value"},
    )

    started = time.perf_counter()
    await ws.initialize()
    init_1_s = time.perf_counter() - started
    image_tag = ws._image_tag
    container = ws._container
    container_id = container.id
    container_name = docker(
        "inspect",
        "-f",
        "{{.Name}}",
        container_id,
    ).lstrip("/")
    emit(
        "first_initialize",
        workspace_id=workspace_id,
        seconds=round(init_1_s, 2),
        image_tag=image_tag,
        image_present=bool(docker("images", "-q", image_tag)),
        container_name=container_name,
        container_id=container_id[:12],
        is_persistent=ws.is_persistent,
    )
    emit(
        "image_size",
        bytes=int(docker("image", "inspect", image_tag, "--format", "{{.Size}}")),
    )

    tools = {type(t).__name__: t for t in await ws.list_tools()}
    emit("tools", names=sorted(tools))

    bash = tools["Bash"]
    read = tools["Read"]
    write = tools["Write"]

    bash_pwd = await drain_bash(bash, "pwd; id; uname -m; python --version")
    emit("container_bash", **bash_pwd)

    bash_env = await drain_bash(bash, "printf '%s' \"$PROBE_ENV\"")
    emit("container_env", **bash_env)

    read_seed = await read.call(f"{CONTAINER_WORKDIR}/seed.txt")
    emit(
        "tool_read_host_seed",
        state=str(read_seed.state),
        text=_text_of(read_seed),
    )

    write_result = await write.call(
        f"{CONTAINER_WORKDIR}/from-container.txt",
        "written-by-container-write\n",
    )
    emit(
        "tool_write",
        state=str(write_result.state),
        text=_text_of(write_result),
    )
    container_written = Path(host_root / "from-container.txt")
    emit(
        "host_sees_container_write",
        exists=container_written.exists(),
        content=(container_written.read_text(encoding="utf-8") if container_written.exists() else None),
    )

    bash_write = await drain_bash(
        bash,
        "printf 'shell-write\\n' > /workspace/from-container-shell.txt && "
        "cat /workspace/from-container-shell.txt",
    )
    emit("container_bash_write", **bash_write)
    shell_written = Path(host_root / "from-container-shell.txt")
    emit(
        "host_sees_shell_write",
        exists=shell_written.exists(),
        content=(shell_written.read_text(encoding="utf-8") if shell_written.exists() else None),
    )

    private = await drain_bash(
        bash,
        "printf 'private\\n' > /tmp/private.txt && "
        "printf 'private\\n' > /root/private.txt && "
        "cat /tmp/private.txt",
    )
    emit("container_private_write", **private)
    host_tmp_private = Path("/tmp/private.txt")
    emit(
        "host_tmp_isolated",
        host_path=str(host_tmp_private),
        host_exists=host_tmp_private.exists(),
    )
    emit(
        "container_private_absent_from_host",
        workspace_tmp=(host_root / "tmp" / "private.txt").exists(),
        workspace_root=(host_root / "root" / "private.txt").exists(),
    )

    read_outside = await read.call("/etc/hostname")
    emit(
        "tool_read_outside_workspace",
        state=str(read_outside.state),
        text=_text_of(read_outside),
    )

    write_outside = await write.call(
        "/root/out-of-workspace.txt",
        "written-outside-workspace\n",
    )
    emit(
        "tool_write_outside_workspace",
        state=str(write_outside.state),
        text=_text_of(write_outside),
    )

    bash_outside = await drain_bash(bash, "cat /etc/hostname; ls /root")
    emit("container_bash_outside_workspace", **bash_outside)

    reset_write = await write.call(
        f"{CONTAINER_WORKDIR}/data/reset-me.txt",
        "reset-me\n",
    )
    root_write = await write.call(
        f"{CONTAINER_WORKDIR}/reset-kept.txt",
        "kept\n",
    )
    emit(
        "reset_setup",
        state=str(reset_write.state),
        root_state=str(root_write.state),
    )
    started = time.perf_counter()
    await ws.reset()
    reset_s = time.perf_counter() - started
    emit(
        "reset",
        seconds=round(reset_s, 3),
        data_dir_gone=not (host_root / "data" / "reset-me.txt").exists(),
        root_file_kept=(host_root / "reset-kept.txt").exists(),
        mcp_file_absent=not (host_root / ".mcp").exists(),
        container_still_running=docker(
            "inspect",
            "-f",
            "{{.State.Running}}",
            container_name,
        ),
    )

    started = time.perf_counter()
    await ws.close()
    close_s = time.perf_counter() - started
    emit(
        "close",
        seconds=round(close_s, 3),
        container_removed=_container_gone(container_name),
        image_retained=bool(docker("images", "-q", image_tag)),
    )

    second = DockerWorkspace(
        workspace_id=workspace_id,
        host_workdir=str(host_root),
        env={"PROBE_ENV": "probe-value"},
    )
    started = time.perf_counter()
    await second.initialize()
    second_init_s = time.perf_counter() - started
    emit(
        "second_initialize_same_id",
        seconds=round(second_init_s, 2),
        image_tag=second._image_tag,
        image_cache_hit=second._image_tag == image_tag,
        host_seed_still_present=seed.exists(),
    )
    await second.close()

    third_root = Path(tempfile.mkdtemp(prefix="as-ws-probe-fresh-"))
    third = DockerWorkspace(
        workspace_id=f"{workspace_id}-fresh",
        host_workdir=str(third_root),
    )
    started = time.perf_counter()
    await third.initialize()
    third_init_s = time.perf_counter() - started
    emit(
        "third_initialize_new_workdir",
        seconds=round(third_init_s, 2),
        image_cache_hit=third._image_tag == image_tag,
    )
    await third.close()
    emit("host_root", path=str(host_root), third_root=str(third_root))
    return 0


def _text_of(chunk: ToolChunk) -> str:
    """Flatten one ToolChunk into its text payload."""
    parts: list[str] = []
    for block in chunk.content:
        value = getattr(block, "text", None)
        if value is not None:
            parts.append(value)
    return "".join(parts)


def _container_gone(name: str) -> bool:
    """Return whether a container name no longer resolves."""
    result = subprocess.run(
        ["docker", "inspect", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
