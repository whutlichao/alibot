"""显式 MCP 声明导出 ``export-mcp``（ADR-0021 §三 / ADR-0031 修订）。

唯一测试接缝是 **CLI 进程入口**：``python -m alibot export-mcp --data-dir <目录> [--apply --yes]``。
断言只落在外部可观察面——退出码、stdout / stderr 报告文本、``mcp.json`` 内容、``mcp_server``
旧行、``remote_consent_scope`` 行、备份与清单。

本文件守四件事：

1. 默认（dry-run）**零副作用**：库 sha256 与数据目录 / 全局层目录树前后一致，不建备份、
   不写清单、不动 ``mcp.json``；
2. ``--apply --yes`` 把 Agent 级旧行追加进成员家目录 ``mcp.json``（现行唯一正本），先整目录
   备份 + 每份被改写文件的备份 + 清单；同名同形幂等（第二次报「跳过」、不再建备份）；
3. 同名不同形 / 文件不可用 / 家目录不存在 → **拒绝**（无 ``--force``），零写入、退出码 6；
4. 远端条目的既有确认从旧行 id **复制**到合成标识；频道级旧行只进报告、不导出。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from alibot.db import DATABASE_FILE_NAME

from tests.conftest import backend, create_agent, create_channel

APP_DIR = Path(__file__).resolve().parents[1]

# 对外契约码（与既有迁移测试同体例：写字面量，不 import 实现常量）。
SUCCESS = 0
USAGE_ERROR = 2
EXPORT_REFUSED = 6


# ─── 子进程与目录辅助 ────────────────────────────────────────────────────────
def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """真实子进程跑 ``python -m alibot <args>``，HOME 指向临时全局层根。"""
    return subprocess.run(
        [sys.executable, "-m", "alibot", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={"HOME": str(home), "PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
        cwd=str(APP_DIR),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_snapshot(root: Path) -> dict[str, str]:
    """目录树的相对路径 → sha256（目录以一个空串占一位，用来发现新建的空目录）。"""
    if not root.exists():
        return {}
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        snapshot[relative + ("/" if path.is_dir() else "")] = "" if path.is_dir() else _sha256(path)
    return snapshot


def _member_mcp_path(home: Path, member_id: str) -> Path:
    """成员家目录里的 ``mcp.json``（与 ``workspace.global_home_root`` 的 realpath 口径一致）。"""
    return Path(os.path.realpath(home / ".alibot")) / "agents" / member_id / "mcp.json"


def _write_member_mcp(
    home: Path, member_id: str, servers: list[dict[str, Any]], *, raw: str | None = None
) -> Path:
    path = _member_mcp_path(home, member_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        raw if raw is not None else json.dumps({"servers": servers}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _backup_dirs(data_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in data_dir.parent.iterdir()
        if path.is_dir() and path.name.startswith(f"{data_dir.name}-backup-")
    )


def _stdio(name: str = "notes", *, args: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "transport": "stdio",
        "command": "python3",
        "args": ["-m", "stub_server"] if args is None else args,
        "env_names": ["ALIBOT_MCP_EXPORT_TOKEN"],
    }


def _remote(name: str = "remote", *, transport: str = "http") -> dict[str, Any]:
    url = "http://127.0.0.1:9/sse" if transport == "sse" else "http://127.0.0.1:9/mcp"
    return {"name": name, "transport": transport, "url": url}


# ─── 库行读写（只读 / 播种；不导入实现模块） ─────────────────────────────────
def _insert_row(
    db_path: Path,
    *,
    row_id: str,
    member_id: str,
    declaration: dict[str, Any],
    scope_kind: str = "agent",
    enabled: bool = True,
) -> None:
    """直接播种一条旧 ``mcp_server`` 行（导出工具的输入）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO mcp_server(id, name, transport, command, args, url, env_names, enabled, "
            "scope_kind, scope_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (
                row_id,
                declaration["name"],
                declaration["transport"],
                declaration.get("command", ""),
                json.dumps(declaration.get("args") or []),
                declaration.get("url", ""),
                json.dumps(declaration.get("env_names") or []),
                1 if enabled else 0,
                scope_kind,
                member_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _mcp_rows(db_path: Path, member_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM mcp_server WHERE scope_kind = 'agent' AND scope_id = ? ORDER BY name",
            (member_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "transport": str(row["transport"]),
            "command": str(row["command"]),
            "args": json.loads(row["args"] or "[]"),
            "url": str(row["url"]),
            "env_names": json.loads(row["env_names"] or "[]"),
            "enabled": int(row["enabled"]),
            "scope_kind": str(row["scope_kind"]),
            "scope_id": str(row["scope_id"]),
        }
        for row in rows
    ]


def _consent_rows(db_path: Path) -> list[tuple[str]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT scope_id FROM remote_consent_scope WHERE scope_kind = 'mcp' ORDER BY scope_id"
        ).fetchall()
    finally:
        conn.close()
    return [(str(row[0]),) for row in rows]


def _seed_consent(db_path: Path, scope_id: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO remote_consent_scope(scope_kind, scope_id, acknowledged_at) "
            "VALUES ('mcp', ?, '2026-01-02T00:00:00Z')",
            (scope_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _file_servers(path: Path) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8"))["servers"])


@contextmanager
def _seeded(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """临时 HOME + 数据目录：一个频道 + 一个成员（+ 一个对照成员）。"""
    home = tmp_path / "home"
    home.mkdir()
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()

    # 建成员时后端要往「该进程的全局层根」写家目录：把 HOME 临时指向本用例的临时根，
    # 让播种出来的成员家目录与 CLI 子进程（同样带这个 HOME）看到同一个位置。
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        with backend(data_dir) as client:
            agent = create_agent(client, name="导出成员").json()["agent"]
            control = create_agent(client, name="对照成员").json()["agent"]
            channel = create_channel(client, name="导出频道", member_ids=[agent["id"]]).json()["channel"]
            patched = client.patch(
                f"/api/channels/{channel['id']}/config", json={"workspace_root": str(project)}
            )
            assert patched.status_code == 200, patched.text
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home

    yield {
        "home": home,
        "data_dir": data_dir,
        "db": data_dir / DATABASE_FILE_NAME,
        "agent": agent,
        "control": control,
        "channel": channel,
    }


# ─── ① 预演零副作用 ─────────────────────────────────────────────────────────
def test_dry_run_lists_the_export_and_writes_nothing(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        _insert_row(db, row_id="mcp_notes", member_id=member, declaration=_stdio())
        _insert_row(db, row_id="mcp_remote", member_id=member, declaration=_remote())
        _seed_consent(db, "mcp_remote")
        db_sha_before = _sha256(db)
        data_before = _tree_snapshot(data_dir)
        home_before = _tree_snapshot(home)

        result = _run(home, "export-mcp", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert str(_member_mcp_path(home, member)) in result.stdout
        assert "新建" in result.stdout
        assert "notes" in result.stdout and "remote" in result.stdout
        assert "预演结束" in result.stdout

        # 零副作用：库、数据目录（备份 / 清单 / sidecar）、全局层目录树逐字节不变。
        assert _sha256(db) == db_sha_before
        assert _tree_snapshot(data_dir) == data_before
        assert _tree_snapshot(home) == home_before
        assert not _member_mcp_path(home, member).exists()


def test_a_member_without_rows_is_left_alone(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        result = _run(Path(ctx["home"]), "export-mcp", "--data-dir", str(ctx["data_dir"]))
        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert "0 条" in result.stdout


# ─── ② apply 写文件、备份、清单、确认复制、幂等 ─────────────────────────────
def test_apply_writes_entries_to_the_member_file_and_keeps_the_rows(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        _insert_row(db, row_id="mcp_notes", member_id=member, declaration=_stdio())
        _insert_row(db, row_id="mcp_remote", member_id=member, declaration=_remote())
        _seed_consent(db, "mcp_remote")
        rows_before = _mcp_rows(db, member)

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        file = _member_mcp_path(home, member)
        servers = {entry["name"]: entry for entry in _file_servers(file)}
        assert set(servers) == {"notes", "remote"}
        assert servers["notes"]["command"] == "python3"
        assert servers["notes"]["args"] == ["-m", "stub_server"]
        assert servers["notes"]["env_names"] == ["ALIBOT_MCP_EXPORT_TOKEN"]
        assert servers["remote"]["url"] == "http://127.0.0.1:9/mcp"

        # 旧行不删（回滚面），只被复制进文件。
        assert _mcp_rows(db, member) == rows_before
        # 备份 + 清单 + 文件备份；备份名与 schema / 技能迁移可区分。
        backups = _backup_dirs(data_dir)
        assert len(backups) == 1
        assert "-backup-mcp-export-" in backups[0].name
        manifests = sorted(data_dir.glob("mcp-export-*.json"))
        assert len(manifests) == 1
        manifest = manifests[0].read_text(encoding="utf-8")
        assert str(file) in manifest
        assert "mcp_notes" in manifest
        # 新建文件没有可备份的旧字节；改写既有文件的那条用例会断言备份（见下）。
        assert not list(file.parent.glob("mcp.json.bak-*"))
        # 远端确认从旧行 id 复制到合成标识；stdio 不写确认行。
        assert _consent_rows(db) == [
            (f"global:{member}:remote",),
            ("mcp_remote",),
        ]
        assert "写后校验" in result.stdout

        # 幂等：第二次 apply 全部「跳过」、不再建备份、文件不变。
        file_sha = _sha256(file)
        backup_names_before = [path.name for path in _backup_dirs(data_dir)]
        second = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(data_dir))
        assert second.returncode == SUCCESS, second.stdout + second.stderr
        assert "跳过" in second.stdout
        assert "没有需要导出的条目" in second.stdout
        assert [path.name for path in _backup_dirs(data_dir)] == backup_names_before
        assert _sha256(file) == file_sha


def test_apply_preserves_extra_file_entries_and_disabled_rows(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        home = Path(ctx["home"])
        file = _write_member_mcp(
            home,
            member,
            [{"name": "hand-written", "transport": "stdio", "command": "python3", "args": ["x.py"]}],
        )
        _insert_row(db, row_id="mcp_off", member_id=member, declaration=_stdio("off"), enabled=False)

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(ctx["data_dir"]))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        servers = {entry["name"]: entry for entry in _file_servers(file)}
        # 人手工写的条目原样保留；停用行的 enabled 也原样带过去。
        assert servers["hand-written"]["args"] == ["x.py"]
        assert servers["off"]["enabled"] is False
        # 改写既有文件前先备份原字节（同目录、带时间戳）。
        assert list(file.parent.glob("mcp.json.bak-*")), "改写前必须备份原文件"


# ─── ③ 同名不同形 / 文件不可用 / 家目录缺失 → 拒绝、零写入 ───────────────────
def test_same_name_different_shape_is_refused_without_writing(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        _insert_row(db, row_id="mcp_notes", member_id=member, declaration=_stdio())
        file = _write_member_mcp(
            home,
            member,
            [_stdio(args=["-m", "another_server"])],
        )
        file_sha_before = _sha256(file)

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == EXPORT_REFUSED, result.stdout + result.stderr
        assert "拒绝" in result.stdout
        assert "同名" in result.stdout or "形状不同" in result.stdout
        # 零写入：文件原样、没有备份、没有清单。
        assert _sha256(file) == file_sha_before
        assert _backup_dirs(data_dir) == []
        assert list(data_dir.glob("mcp-export-*.json")) == []
        assert "未写入" in result.stderr


def test_same_name_same_shape_is_only_skipped(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        home = Path(ctx["home"])
        _insert_row(db, row_id="mcp_notes", member_id=member, declaration=_stdio())
        file = _write_member_mcp(home, member, [_stdio()])
        file_sha_before = _sha256(file)

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(ctx["data_dir"]))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert "跳过" in result.stdout
        assert "没有需要导出的条目" in result.stdout
        assert _sha256(file) == file_sha_before


def test_an_existing_same_shape_remote_still_gets_its_consent_copied(tmp_path: Path) -> None:
    """文件里早已同形的远端（典型：跑过旧 migrate-mcp 的安装）也要把既有确认搬到合成标识。

    不复制的话，升级后远端会被读成未确认而不再建连——既有确认是用户已给过的授权，随声明搬。
    """
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        home = Path(ctx["home"])
        _insert_row(db, row_id="mcp_remote", member_id=member, declaration=_remote())
        _seed_consent(db, "mcp_remote")
        file = _write_member_mcp(home, member, [_remote()])
        file_sha_before = _sha256(file)

        # 预演就要点名「将复制既有远端确认」，不静默。
        planned = _run(home, "export-mcp", "--data-dir", str(ctx["data_dir"]))
        assert planned.returncode == SUCCESS, planned.stdout + planned.stderr
        assert "将复制既有远端确认" in planned.stdout
        assert _consent_rows(db) == [("mcp_remote",)]

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(ctx["data_dir"]))
        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert "跳过" in result.stdout
        assert _consent_rows(db) == [(f"global:{member}:remote",), ("mcp_remote",)]
        assert _sha256(file) == file_sha_before, "同形条目只跳过：文件一个字节都不动"

        # 幂等：确认已搬过 ⇒ 再跑一次什么都不做、也不建备份。
        backups_before = [path.name for path in _backup_dirs(Path(ctx["data_dir"]))]
        again = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(ctx["data_dir"]))
        assert again.returncode == SUCCESS, again.stdout + again.stderr
        assert "没有需要导出的条目" in again.stdout
        assert [path.name for path in _backup_dirs(Path(ctx["data_dir"]))] == backups_before
        assert _consent_rows(db) == [(f"global:{member}:remote",), ("mcp_remote",)]


@pytest.mark.parametrize(
    "raw",
    [
        "{ not json",
        '[{"name": "x", "transport": "stdio"}]',
    ],
)
def test_an_unusable_file_is_refused_wholesale(tmp_path: Path, raw: str) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        _insert_row(db, row_id="mcp_notes", member_id=member, declaration=_stdio())
        file = _write_member_mcp(home, member, [], raw=raw)
        file_sha_before = _sha256(file)

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == EXPORT_REFUSED, result.stdout + result.stderr
        assert "拒绝" in result.stdout
        assert _sha256(file) == file_sha_before
        assert _backup_dirs(data_dir) == []


def test_a_missing_member_home_is_refused(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        _insert_row(db, row_id="mcp_notes", member_id=member, declaration=_stdio())
        # 应用不自动创建全局层：家目录缺席时要人先建出来。
        import shutil

        shutil.rmtree(_member_mcp_path(home, member).parent)

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == EXPORT_REFUSED, result.stdout + result.stderr
        assert "家目录不存在" in result.stdout
        assert _backup_dirs(data_dir) == []


# ─── ④ 频道级旧行：点名、不导出 ─────────────────────────────────────────────
def test_channel_rows_are_reported_but_not_exported(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        channel_id = str(ctx["channel"]["id"])
        db = Path(ctx["db"])
        home = Path(ctx["home"])
        _insert_row(
            db,
            row_id="mcp_channel",
            member_id=channel_id,
            declaration=_stdio("shared"),
            scope_kind="channel",
        )

        result = _run(home, "export-mcp", "--apply", "--yes", "--data-dir", str(ctx["data_dir"]))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert "频道级旧行" in result.stdout
        assert "shared" in result.stdout
        assert not _member_mcp_path(home, member).exists()


# ─── CLI 双开关与退役命令 ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    "args",
    [
        ["export-mcp", "--apply"],
        ["export-mcp", "--yes"],
        ["export-mcp", "--apply", "--yes", "--dry-run"],
        ["export-mcp", "--migrate"],
        ["export-mcp", "migrate-skills"],
    ],
)
def test_destructive_switches_require_both_flags(tmp_path: Path, args: list[str]) -> None:
    result = _run(tmp_path / "home", *args, "--data-dir", str(tmp_path / "missing"))
    assert result.returncode == USAGE_ERROR, result.stdout + result.stderr


@pytest.mark.parametrize("args", [["migrate-mcp"], ["migrate-mcp", "--apply", "--yes"]])
def test_the_retired_import_command_points_at_the_export(tmp_path: Path, args: list[str]) -> None:
    home = tmp_path / "home"
    home.mkdir()
    # 带 --apply / --yes 也照旧是「退役指引 + 退出码 6」，而不是走成用法错误（2）。
    result = _run(home, *args, "--data-dir", str(tmp_path / "missing"))
    assert result.returncode == EXPORT_REFUSED
    assert "已退役" in result.stderr
    assert "export-mcp" in result.stderr


def test_missing_database_is_a_readable_refusal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run(home, "export-mcp", "--data-dir", str(tmp_path / "missing"))
    assert result.returncode == EXPORT_REFUSED
    assert "数据目录里没有" in result.stderr
