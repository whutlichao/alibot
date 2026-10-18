#!/usr/bin/env python3
"""Probe whether an abrupt process exit cleans up the container.

The parent process is expected to check ``docker inspect`` after this
script exits with ``os._exit(0)`` (no ``close``, no context manager).

Run with the repository venv:

    .venv/bin/python app/specs/issue-134-research/probe_process_exit.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from agentscope.workspace import DockerWorkspace


async def main() -> None:
    """Start a workspace and exit the process without closing it."""
    workspace_id = os.environ.get(
        "PROBE_WORKSPACE_ID",
        f"issue134-exit-{os.getpid()}",
    )
    workspace = DockerWorkspace(
        workspace_id=workspace_id,
        host_workdir=tempfile.mkdtemp(prefix="as-ws-exit-"),
    )
    await workspace.initialize()
    print(
        json.dumps(
            {
                "event": "started",
                "workspace_id": workspace_id,
                "container_name": f"as_ws_{workspace_id}",
                "pid": os.getpid(),
            },
        ),
        flush=True,
    )
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
