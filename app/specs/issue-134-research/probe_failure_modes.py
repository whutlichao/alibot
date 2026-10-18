#!/usr/bin/env python3
"""Probe failure modes of AgentScope 2.0.10 ``DockerWorkspace``.

Each scenario runs in this process but isolates state carefully; the
daemon-unavailable case only changes ``DOCKER_HOST`` for the duration of
one workspace construction/initialization.

Run with the repository venv:

    .venv/bin/python app/specs/issue-134-research/probe_failure_modes.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from agentscope.workspace import DockerWorkspace


def emit(event: str, **payload: object) -> None:
    """Print one machine-readable result line."""
    print(json.dumps({"event": event, **payload}, sort_keys=True))
    sys.stdout.flush()


async def run_case(name: str, workspace: DockerWorkspace) -> None:
    """Initialize one workspace and report the failure shape."""
    started = time.perf_counter()
    try:
        await workspace.initialize()
    except BaseException as exc:
        emit(
            "failure",
            case=name,
            seconds=round(time.perf_counter() - started, 2),
            error_type=type(exc).__name__,
            error=str(exc)[:4000],
            traceback_last=(traceback.format_exc().strip().splitlines()[-1][:500]),
        )
    else:
        emit("failure", case=name, unexpected_success=True)
    finally:
        try:
            await workspace.close()
        except Exception as exc:
            emit("teardown_error", case=name, error=repr(exc))


async def no_daemon_child() -> int:
    """Connect to a socket that does not exist (child process only)."""
    workspace = DockerWorkspace(workspace_id="issue134-no-daemon")
    await run_case("no_daemon", workspace)
    return 0


def no_daemon() -> None:
    """Run the no-daemon case with an isolated HOME and DOCKER_HOST.

    aiodocker resolves the active Docker *context* before
    ``DOCKER_HOST``; altering ``DOCKER_HOST`` in-process is therefore
    ignored on this machine (context ``orbstack``). An empty temporary
    HOME makes the child fall back to ``DOCKER_HOST``.
    """
    home = Path(tempfile.mkdtemp(prefix="as-ws-no-daemon-home-"))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["DOCKER_HOST"] = "unix:///tmp/issue134-no-docker.sock"
    result = subprocess.run(
        [sys.executable, __file__, "--no-daemon-child"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    for line in result.stdout.splitlines():
        if line.startswith("{"):
            print(line, flush=True)
    emit(
        "child_exit",
        case="no_daemon",
        returncode=result.returncode,
        stderr_tail=result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "",
    )


async def main() -> int:
    """Run every failure scenario."""
    no_daemon()

    await run_case(
        "missing_base_image",
        DockerWorkspace(
            workspace_id="issue134-missing-base",
            base_image="python:3.11-issue134-missing",
            host_workdir=tempfile.mkdtemp(prefix="as-ws-fail-build-"),
        ),
    )

    await run_case(
        "incompatible_base_image",
        DockerWorkspace(
            workspace_id="issue134-incompatible-base",
            base_image="alpine:3.20",
            host_workdir=tempfile.mkdtemp(prefix="as-ws-fail-dockerfile-"),
        ),
    )

    file_as_root = Path(tempfile.mkstemp(prefix="as-ws-fail-mount-")[1])
    await run_case(
        "host_workdir_is_file",
        DockerWorkspace(
            workspace_id="issue134-bad-mount",
            host_workdir=str(file_as_root),
        ),
    )

    await run_case(
        "invalid_gateway_port",
        DockerWorkspace(
            workspace_id="issue134-bad-port",
            gateway_port=65536,
            host_workdir=tempfile.mkdtemp(prefix="as-ws-fail-port-"),
        ),
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-daemon-child", action="store_true")
    args = parser.parse_args()
    if args.no_daemon_child:
        raise SystemExit(asyncio.run(no_daemon_child()))
    raise SystemExit(asyncio.run(main()))
