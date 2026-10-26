"""显式 MCP 声明导入 ``migrate-mcp``（issue #275 / ADR-0031 §二）。

唯一测试接缝是 **CLI 进程入口**：``python -m alibot migrate-mcp --data-dir <目录> [--apply --yes]``。
断言只落在外部可观察面——退出码、stdout / stderr 报告文本、``mcp_server`` 行、文件 sha256、
``remote_consent_scope`` 行。

本文件守四件事（票面 Observable acceptance 与约束）：

1. ``--dry-run``（默认）**零副作用**：库 sha256 与数据目录 / 全局层目录树前后一致，不建备份、
   不写迁移清单、不动 ``mcp.json`` 本身；
2. ``--apply --yes`` 把家目录条目写进 ``mcp_server``（``scope_kind='agent'`` / ``scope_id=<member>``），
   先整目录备份 + 写迁移清单；同名同形幂等（第二次报「跳过」、不再建备份、行 id 不变）；
3. 同名不同形 → **拒绝**并列待裁定（无 ``--force``），零写入、退出码 6；
4. 远端条目的确认**不代做**：``remote_consent_scope('mcp', …)`` 一行都不写，报告写明「导入后仍需
   ``POST /api/mcp-servers/{id}/consent``」。

外加 CLI 双开关（``--apply`` 缺 ``--yes`` / 单独 ``--yes`` / ``--dry-run`` 同用 ⇒ 2）。
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
IMPORT_REFUSED = 6


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
        "env_names": ["ALIBOT_MCP_IMPORT_TOKEN"],
    }


def _remote(name: str = "remote", *, transport: str = "http") -> dict[str, Any]:
    url = "http://127.0.0.1:9/sse" if transport == "sse" else "http://127.0.0.1:9/mcp"
    return {"name": name, "transport": transport, "url": url}


# ─── 库行读数（只读连接；与 test_skill_migration.py 同体例） ────────────────
def _mcp_rows(db_path: Path, member_id: str) -> list[dict[str, Any]]:
    """该成员的 Agent 级 ``mcp_server`` 行（只读连接；不导入实现模块）。"""
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
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]


def _consent_rows(db_path: Path) -> list[tuple[str, str]]:
    """已存在的 ``remote_consent_scope('mcp', …)`` 行（本命令一行都不该写）。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT scope_kind, scope_id FROM remote_consent_scope WHERE scope_kind = 'mcp'"
        ).fetchall()
    finally:
        conn.close()
    return [(str(row[0]), str(row[1])) for row in rows]


@contextmanager
def _seeded(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """临时 HOME + 数据目录：一个频道 + 一个成员（+ 一个对照成员，没有家目录文件）。"""
    home = tmp_path / "home"
    home.mkdir()
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()

    with backend(data_dir) as client:
        agent = create_agent(client, name="导入成员").json()["agent"]
        control = create_agent(client, name="对照成员").json()["agent"]
        channel = create_channel(client, name="导入频道", member_ids=[agent["id"]]).json()["channel"]
        patched = client.patch(f"/api/channels/{channel['id']}/config", json={"workspace_root": str(project)})
        assert patched.status_code == 200, patched.text

    yield {
        "home": home,
        "data_dir": data_dir,
        "db": data_dir / DATABASE_FILE_NAME,
        "agent": agent,
        "control": control,
        "channel": channel,
    }


# ─── ① 预演零副作用 ─────────────────────────────────────────────────────────
def test_dry_run_lists_the_import_and_writes_nothing(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        file = _write_member_mcp(ctx["home"], member, [_stdio(), _remote()])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        db_sha_before = _sha256(db)
        file_sha_before = _sha256(file)
        data_before = _tree_snapshot(data_dir)
        home_before = _tree_snapshot(home)

        result = _run(home, "migrate-mcp", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert str(file) in result.stdout
        assert "新建" in result.stdout
        assert "notes" in result.stdout and "remote" in result.stdout
        # 远端条目的确认不代做：报告点出「导入后仍需 POST …/consent」。
        assert "POST /api/mcp-servers/" in result.stdout
        assert "/consent" in result.stdout
        assert "预演结束" in result.stdout

        # 零副作用：库、数据目录（备份 / 清单 / sidecar）、全局层目录树、mcp.json 逐字节不变。
        assert _sha256(db) == db_sha_before
        assert _sha256(file) == file_sha_before
        assert _tree_snapshot(data_dir) == data_before
        assert _tree_snapshot(home) == home_before
        assert _mcp_rows(db, member) == []
        assert _consent_rows(db) == []


def test_a_member_without_a_file_is_left_alone(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        result = _run(Path(ctx["home"]), "migrate-mcp", "--data-dir", str(ctx["data_dir"]))
        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert "0 条" in result.stdout
        assert _mcp_rows(Path(ctx["db"]), str(ctx["agent"]["id"])) == []


# ─── ② apply 写库行、备份、清单、幂等 ──────────────────────────────────────
def test_apply_imports_entries_as_agent_rows_and_keeps_the_file(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        file = _write_member_mcp(ctx["home"], member, [_stdio(), _remote()])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        file_sha_before = _sha256(file)

        result = _run(home, "migrate-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        rows = _mcp_rows(db, member)
        assert [(row["name"], row["transport"], row["scope_kind"], row["scope_id"]) for row in rows] == [
            ("notes", "stdio", "agent", member),
            ("remote", "http", "agent", member),
        ]
        notes = next(row for row in rows if row["name"] == "notes")
        assert notes["command"] == "python3"
        assert notes["args"] == ["-m", "stub_server"]
        assert notes["env_names"] == ["ALIBOT_MCP_IMPORT_TOKEN"]
        assert notes["enabled"] == 1
        remote = next(row for row in rows if row["name"] == "remote")
        assert remote["url"] == "http://127.0.0.1:9/mcp"
        assert remote["command"] == ""

        # 源文件不改：导入只读它。
        assert _sha256(file) == file_sha_before
        # 备份 + 迁移清单都落在数据目录侧；备份名与 schema 迁移 / 技能迁移可区分。
        backups = _backup_dirs(data_dir)
        assert len(backups) == 1
        assert "-backup-mcp-" in backups[0].name
        manifests = sorted(data_dir.glob("mcp-migration-*.json"))
        assert len(manifests) == 1
        assert str(file) in manifests[0].read_text(encoding="utf-8")
        # 远端条目的确认不代做：零确认行，报告给出逐条 id 的确认入口。
        assert _consent_rows(db) == []
        assert f"POST /api/mcp-servers/{remote['id']}/consent" in result.stdout
        assert f"POST /api/mcp-servers/{notes['id']}/consent" not in result.stdout

        # 幂等：第二次 apply 报「跳过」、不重复插行、不再建备份、id / updated_at 不变。
        backup_names_before = [path.name for path in _backup_dirs(data_dir)]
        second = _run(home, "migrate-mcp", "--apply", "--yes", "--data-dir", str(data_dir))
        assert second.returncode == SUCCESS, second.stdout + second.stderr
        assert "跳过" in second.stdout
        assert "没有需要导入的条目" in second.stdout
        assert [path.name for path in _backup_dirs(data_dir)] == backup_names_before
        assert _mcp_rows(db, member) == rows


def test_apply_imports_disabled_entries_as_disabled_rows(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        _write_member_mcp(ctx["home"], member, [{**_stdio(), "enabled": False}])
        result = _run(
            Path(ctx["home"]), "migrate-mcp", "--apply", "--yes", "--data-dir", str(ctx["data_dir"])
        )
        assert result.returncode == SUCCESS, result.stdout + result.stderr
        rows = _mcp_rows(Path(ctx["db"]), member)
        assert [(row["name"], row["enabled"]) for row in rows] == [("notes", 0)]


# ─── ③ 同名不同形 → 拒绝、零写入 ────────────────────────────────────────────
def test_same_name_different_shape_is_refused_without_writing(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        with backend(data_dir) as client:
            created = client.post(
                "/api/mcp-servers",
                json={
                    "scope_kind": "agent",
                    "scope_id": member,
                    "name": "notes",
                    "transport": "stdio",
                    "command": "python3",
                    "args": ["-m", "another_server"],
                },
            )
            assert created.status_code == 201, created.text
        rows_before = _mcp_rows(db, member)
        _write_member_mcp(home, member, [_stdio(), _remote()])

        result = _run(home, "migrate-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == IMPORT_REFUSED, result.stdout + result.stderr
        assert "拒绝" in result.stdout
        assert "同名" in result.stdout or "内容不同" in result.stdout
        # 零写入：行原样、没有备份、没有清单、mcp.json 没动。
        assert _mcp_rows(db, member) == rows_before
        assert _consent_rows(db) == []
        assert _backup_dirs(data_dir) == []
        assert list(data_dir.glob("mcp-migration-*.json")) == []
        assert "未写入" in result.stderr


def test_same_name_same_shape_is_only_skipped(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        with backend(data_dir) as client:
            created = client.post(
                "/api/mcp-servers",
                json={
                    "scope_kind": "agent",
                    "scope_id": member,
                    "name": "notes",
                    "transport": "stdio",
                    "command": "python3",
                    "args": ["-m", "stub_server"],
                    "env_names": ["ALIBOT_MCP_IMPORT_TOKEN"],
                },
            )
            assert created.status_code == 201, created.text
        rows_before = _mcp_rows(db, member)
        _write_member_mcp(home, member, [_stdio(), _remote()])

        result = _run(home, "migrate-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert "跳过" in result.stdout
        rows_after = _mcp_rows(db, member)
        notes = next(row for row in rows_after if row["name"] == "notes")
        assert notes == rows_before[0]
        # 只有另一条被新建：行数 2（原有 notes + 新 remote）。
        assert sorted(row["name"] for row in rows_after) == ["notes", "remote"]


# ─── ③b 文件本身不可用时拒绝（不静默跳过、不部分导入） ──────────────────────
@pytest.mark.parametrize(
    ("raw", "hint"),
    [
        ("{ not json", "JSON"),
        ('[{"name": "x", "transport": "stdio"}]', "servers"),
        (
            json.dumps({"servers": [{"name": "carrier", "transport": "carrier-pigeon"}]}),
            "transport",
        ),
        (json.dumps({"servers": [_stdio("dup"), _stdio("dup")]}), "dup"),
    ],
)
def test_an_unusable_file_is_refused_wholesale(tmp_path: Path, raw: str, hint: str) -> None:
    with _seeded(tmp_path) as ctx:
        member = str(ctx["agent"]["id"])
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        _write_member_mcp(home, member, [], raw=raw)

        result = _run(home, "migrate-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == IMPORT_REFUSED, result.stdout + result.stderr
        assert "拒绝" in result.stdout
        assert hint in result.stdout
        assert _mcp_rows(db, member) == []
        assert _backup_dirs(data_dir) == []


def test_a_partition_that_is_not_a_member_is_reported_only(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        db = Path(ctx["db"])
        data_dir = Path(ctx["data_dir"])
        home = Path(ctx["home"])
        stray = _write_member_mcp(home, "not-a-member", [_stdio("stray")])

        result = _run(home, "migrate-mcp", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stdout + result.stderr
        assert str(stray.parent) in result.stdout
        assert "未导入" in result.stdout or "不是" in result.stdout
        assert _mcp_rows(db, "not-a-member") == []


# ─── CLI 双开关 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "args",
    [
        ["migrate-mcp", "--apply"],
        ["migrate-mcp", "--yes"],
        ["migrate-mcp", "--apply", "--yes", "--dry-run"],
        ["migrate-mcp", "--migrate"],
        ["migrate-mcp", "migrate-skills"],
    ],
)
def test_destructive_switches_require_both_flags(tmp_path: Path, args: list[str]) -> None:
    result = _run(tmp_path / "home", *args, "--data-dir", str(tmp_path / "missing"))
    assert result.returncode == USAGE_ERROR, result.stdout + result.stderr


def test_missing_database_is_a_readable_refusal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_member_mcp(home, "agent-1", [_stdio()])
    result = _run(home, "migrate-mcp", "--data-dir", str(tmp_path / "missing"))
    assert result.returncode == IMPORT_REFUSED
    assert "数据目录里没有" in result.stderr
