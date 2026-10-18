#!/usr/bin/env python3
"""Issue #135 probe: are AgentScope 2.0.10 file tools confined to a root?

Builds a throw-away workspace in a temp directory and calls the five builtin
file tools (Read / Write / Edit / Glob / Grep) through the same objects that
``LocalWorkspace.list_tools()`` returns.  Every case is printed as one line:

    <tool> | <case> | <verdict> | state=<raw> | <detail>

``verdict`` is SUCCESS (any non-error chunk), ERROR (``ToolResultState.ERROR``)
or EXCEPTION (the tool call itself raised).  Bash is included as one control
row: it is a host process, not a confined file tool.

Run from the repository root with the shared venv::

    .venv/bin/python app/specs/issue-135-research/probe_workspace_root.py

The script exits 0 regardless of tool outcomes; it is a measurement, not a
test.  It never touches the repository (all paths live under a temp dir).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import tempfile
from typing import Any

from agentscope.message import ToolResultState
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionContext,
    PermissionMode,
)
from agentscope.tool import ToolChunk
from agentscope.workspace import LocalWorkspace


def _text(chunk: ToolChunk) -> str:
    """Return the text blocks of a chunk with whitespace collapsed."""
    parts: list[str] = []
    for block in chunk.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    if not parts:
        return "<no text block>"
    return " ".join(" ".join(parts).split())[:200]


async def _invoke(tool: Any, **kwargs: Any) -> tuple[str, str]:
    """Invoke one tool and normalise the outcome to (raw state, detail)."""
    try:
        result = await tool(**kwargs)
        if inspect.isasyncgen(result):
            chunks = [chunk async for chunk in result]
            state = chunks[-1].state if chunks else ToolResultState.RUNNING
            raw = "\n".join(_text(chunk) for chunk in chunks)
        else:
            state = result.state
            raw = _text(result)
        return str(state.value if hasattr(state, "value") else state), raw
    except Exception as exc:
        return "EXCEPTION", f"{type(exc).__name__}: {exc}"


def _verdict(state: str) -> str:
    if state == "EXCEPTION":
        return "EXCEPTION"
    if state == ToolResultState.ERROR.value:
        return "ERROR"
    return "SUCCESS"


def _row(tool: str, case: str, state: str, detail: str) -> None:
    safe = detail.replace("|", "\\|")
    print(f"{tool} | {case} | {_verdict(state)} | state={state} | {safe}")


def _value_row(tool: str, case: str, value: object, detail: str) -> None:
    safe = detail.replace("|", "\\|")
    print(f"{tool} | {case} | {value} | {safe}")


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _read_with(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


async def main() -> None:
    build = os.path.realpath(tempfile.mkdtemp(prefix="as2-135-"))
    root = os.path.join(build, "root")
    outside = os.path.join(build, "outside")
    process_cwd = os.path.join(build, "process-cwd")
    for directory in (root, outside, process_cwd):
        os.makedirs(directory)

    # Root contents.
    _write_file(os.path.join(root, "inside.txt"), "inside-marker\n")
    _write_file(os.path.join(root, "edit-target.txt"), "alpha\n")
    _write_file(os.path.join(process_cwd, "cwd-marker.txt"), "process-cwd-marker\n")

    # Outside contents, one separate victim per tool so the rows below do not
    # contaminate each other.
    _write_file(os.path.join(outside, "read-secret.txt"), "read-outside-marker\n")
    _write_file(os.path.join(outside, "read-hardlink-victim.txt"), "read-hardlink-original\n")
    _write_file(os.path.join(outside, "edit-abs.txt"), "alpha\n")
    _write_file(os.path.join(outside, "edit-dotdot.txt"), "alpha\n")
    _write_file(os.path.join(outside, "grep-secret.txt"), "grep-outside-marker\n")
    _write_file(os.path.join(outside, "write-victim.txt"), "write-victim-original\n")
    _write_file(os.path.join(outside, "edit-link-victim.txt"), "alpha\n")
    _write_file(os.path.join(outside, "write-hardlink-victim.txt"), "write-hardlink-original\n")
    _write_file(os.path.join(outside, "edit-hardlink-victim.txt"), "alpha\n")

    # Escapes that a lexical ``startswith`` check would miss or that cross the
    # root through the filesystem rather than through the string.
    read_link = os.path.join(root, "read_link.txt")
    read_hardlink = os.path.join(root, "read_hardlink.txt")
    write_link = os.path.join(root, "write_link.txt")
    edit_link = os.path.join(root, "edit_link.txt")
    link_dir = os.path.join(root, "link_dir")
    grep_link_file = os.path.join(root, "grep_link.txt")
    write_hardlink = os.path.join(root, "write_hardlink.txt")
    edit_hardlink = os.path.join(root, "edit_hardlink.txt")
    os.symlink(os.path.join(outside, "read-secret.txt"), read_link)
    os.link(os.path.join(outside, "read-hardlink-victim.txt"), read_hardlink)
    os.symlink(os.path.join(outside, "write-victim.txt"), write_link)
    os.symlink(os.path.join(outside, "edit-link-victim.txt"), edit_link)
    os.symlink(outside, link_dir)
    os.symlink(os.path.join(outside, "grep-secret.txt"), grep_link_file)
    os.link(os.path.join(outside, "write-hardlink-victim.txt"), write_hardlink)
    os.link(os.path.join(outside, "edit-hardlink-victim.txt"), edit_hardlink)

    # The tools default their base directory to ``LocalBackend.getcwd()``,
    # which is ``os.getcwd()``.  Move away from the repo to make that visible.
    os.chdir(process_cwd)

    workspace = LocalWorkspace(workdir=root)
    await workspace.initialize()
    tools = {tool.name: tool for tool in await workspace.list_tools()}

    print(f"# agentscope 2.0.10; LocalWorkspace.workdir = {workspace.workdir}")
    print(f"# root realpath            = {root}")
    print(f"# process cwd              = {os.getcwd()}")
    print(f"# tools                    = {', '.join(sorted(tools))}")

    read = tools["Read"]
    write = tools["Write"]
    edit = tools["Edit"]
    glob = tools["Glob"]
    grep = tools["Grep"]
    bash = tools["Bash"]

    # ── Read ────────────────────────────────────────────────────────────
    for case, path in (
        ("inside absolute", os.path.join(root, "inside.txt")),
        ("outside absolute", os.path.join(outside, "read-secret.txt")),
        ("outside via `..`", os.path.join(root, "..", "outside", "read-secret.txt")),
        ("relative `../`", os.path.join("..", "outside", "read-secret.txt")),
        ("symlink -> outside file", read_link),
        ("hardlink -> outside inode", read_hardlink),
    ):
        state, detail = await _invoke(read, file_path=path)
        _row("Read", case, state, detail)

    # ── Write ───────────────────────────────────────────────────────────
    for case, path in (
        ("inside absolute", os.path.join(root, "written.txt")),
        ("outside absolute", os.path.join(outside, "pwned.txt")),
        ("outside via `..`", os.path.join(root, "..", "outside", "pwned2.txt")),
        ("outside new nested dirs", os.path.join(outside, "newdir", "deep", "created.txt")),
        ("relative `../`", os.path.join("..", "outside", "pwned3.txt")),
        ("symlink -> outside file", write_link),
        ("hardlink -> outside inode", write_hardlink),
    ):
        state, detail = await _invoke(write, file_path=path, content="WRITTEN-BY-PROBE\n")
        _row("Write", case, state, detail)
    for label, path in (
        ("inside", os.path.join(root, "written.txt")),
        ("outside", os.path.join(outside, "pwned.txt")),
        ("outside via `..`", os.path.join(outside, "pwned2.txt")),
        ("outside new nested dirs", os.path.join(outside, "newdir", "deep", "created.txt")),
        ("outside symlink target", os.path.join(outside, "write-victim.txt")),
        ("outside hardlink inode", os.path.join(outside, "write-hardlink-victim.txt")),
    ):
        if os.path.exists(path):
            _row("Write:side-effect", label, "success", _read_with(path)[:40])
        else:
            _row("Write:side-effect", label, "error", "<missing>")

    # ── Edit ────────────────────────────────────────────────────────────
    for case, path in (
        ("inside absolute", os.path.join(root, "edit-target.txt")),
        ("outside absolute", os.path.join(outside, "edit-abs.txt")),
        ("outside via `..`", os.path.join(root, "..", "outside", "edit-dotdot.txt")),
        ("relative `../`", os.path.join("..", "outside", "edit-dotdot.txt")),
        ("symlink -> outside file", edit_link),
        ("hardlink -> outside inode", edit_hardlink),
    ):
        state, detail = await _invoke(edit, file_path=path, old_string="alpha", new_string="beta")
        _row("Edit", case, state, detail)
    for label, path in (
        ("inside", os.path.join(root, "edit-target.txt")),
        ("outside", os.path.join(outside, "edit-abs.txt")),
        ("outside via `..`", os.path.join(outside, "edit-dotdot.txt")),
        ("outside symlink target", os.path.join(outside, "edit-link-victim.txt")),
        ("outside hardlink inode", os.path.join(outside, "edit-hardlink-victim.txt")),
    ):
        _row("Edit:side-effect", label, "success", _read_with(path))

    # ── Glob ────────────────────────────────────────────────────────────
    for case, kwargs in (
        ("inside path", {"path": root, "pattern": "*.txt"}),
        ("outside absolute path", {"path": outside, "pattern": "*.txt"}),
        ("outside via `..`", {"path": os.path.join(root, "..", "outside"), "pattern": "*.txt"}),
        ("relative `../` path", {"path": os.path.join("..", "outside"), "pattern": "*.txt"}),
        ("symlink dir as base", {"path": link_dir, "pattern": "*.txt"}),
        ("symlink dir inside pattern", {"path": root, "pattern": "link_dir/*.txt"}),
        ("omitted path (default cwd)", {"pattern": "*.txt"}),
        ("pattern with `..`", {"path": root, "pattern": "../outside/*.txt"}),
    ):
        state, detail = await _invoke(glob, **kwargs)
        _row("Glob", case, state, detail)

    # ── Grep ────────────────────────────────────────────────────────────
    for case, kwargs in (
        ("inside path", {"path": root, "pattern": "inside-marker"}),
        ("outside absolute path", {"path": outside, "pattern": "grep-outside-marker"}),
        ("outside via `..`", {"path": os.path.join(root, "..", "outside"), "pattern": "grep-outside-marker"}),
        ("relative `../` path", {"path": os.path.join("..", "outside"), "pattern": "grep-outside-marker"}),
        ("symlink dir as path", {"path": link_dir, "pattern": "grep-outside-marker"}),
        ("symlink file as path", {"path": grep_link_file, "pattern": "grep-outside-marker"}),
        ("omitted path (default cwd)", {"pattern": "process-cwd-marker"}),
    ):
        state, detail = await _invoke(grep, **kwargs)
        _row("Grep", case, state, detail)

    # ── Bash control (mounted by LocalWorkspace.list_tools) ─────────────
    state, detail = await _invoke(
        bash,
        command=f"pwd; cat {os.path.join(outside, 'read-secret.txt')}; id -u",
    )
    _row("Bash", "cwd + read outside via absolute path", state, detail)

    # ── ToolBase realpath helper (ACCEPT_EDITS auto-allow gate) ─────────
    context = PermissionContext(
        mode=PermissionMode.ACCEPT_EDITS,
        working_directories={
            root: AdditionalWorkingDirectory(path=root, source="probe"),
        },
    )
    for case, path in (
        ("inside", os.path.join(root, "inside.txt")),
        ("root itself", root),
        ("outside", os.path.join(outside, "read-secret.txt")),
        ("outside via `..`", os.path.join(root, "..", "outside", "read-secret.txt")),
        ("symlink -> outside", read_link),
        ("hardlink -> outside inode", read_hardlink),
        ("sibling prefix root-evil", root + "-evil/x.txt"),
    ):
        for tool_name in ("Write", "Edit", "Read", "Glob", "Grep"):
            tool = tools[tool_name]
            value = tool._path_in_allowed_working_path(path, context)
            _value_row("realpath-gate", f"{tool_name}:{case}", value, path)

    # A root passed as a symlink: LocalWorkspace stores ``abspath``, not
    # ``realpath``; comparing tool realpaths against it must not false-reject.
    root_alias = os.path.join(build, "root-alias")
    os.symlink(root, root_alias)
    alias_workspace = LocalWorkspace(workdir=root_alias)
    print(f"# root alias                = {root_alias}")
    print(f"# root alias realpath       = {os.path.realpath(root_alias)}")
    same_workdir = alias_workspace.workdir == os.path.realpath(root)
    print(f"# workspace(alias).workdir == realpath(root): {same_workdir}")

    if os.environ.get("AS2_PROBE_KEEP"):
        print(f"# kept temp dir            = {build}")
    else:
        shutil.rmtree(build, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
