"""显式技能字节迁移 ``migrate-skills``（issue #274 / ADR-0031 §二）。

唯一测试接缝是 **CLI 进程入口**：``python -m alibot migrate-skills --data-dir <目录> [--apply --yes]``。
断言只落在外部可观察面——退出码、stdout / stderr 报告文本、目录树快照、文件 sha256、``skill`` 行。

本文件守四件事（票面 Observable acceptance）：

1. ``--dry-run``（默认）**零副作用**：目录树与库文件 sha256 前后一致，不建家目录、不建备份、
   不写迁移清单；
2. ``--apply --yes`` 复制字节、**重算 hash** 并更新既有行的 ``source_path`` / ``content_hash`` /
   ``updated_at``；无库行时按 ``scope_kind='agent'`` 新建；
3. 目标同名但内容不同 → **拒绝**、不覆盖、退出非 0、源字节仍在；
4. 频道级分区只出现在报告里（数量 + 「频道级已弃用，请按成员安装」），不复制、不入库。

外加 CLI 双开关（``--apply`` 缺 ``--yes`` / 单独 ``--yes`` ⇒ 2）与幂等（二次 apply 不重复插行）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alibot.db import DATABASE_FILE_NAME

from tests.conftest import backend, create_agent, create_channel

APP_DIR = Path(__file__).resolve().parents[1]

# 对外契约码（与既有迁移测试同体例：写字面量，不 import 实现常量）。
SUCCESS = 0
USAGE_ERROR = 2
SKILL_MIGRATION_REFUSED = 6

# 造存量行用的语句：旧安装器留下的行（hash 故意写成占位值，方便断言「重算过」）。
INSERT_SKILL_SQL = (
    "INSERT INTO skill(id, name, description, source_path, content_hash, enabled, "
    "scope_kind, scope_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)"
)
SELECT_SKILL_SQL = "SELECT * FROM skill WHERE scope_kind = 'agent' AND scope_id = ? AND name = ?"
COUNT_MEMBER_SKILLS_SQL = "SELECT COUNT(*) FROM skill WHERE scope_kind = 'agent' AND scope_id = ?"
PLACEHOLDER_HASH = "0" * 64
LEGACY_UPDATED_AT = "2020-01-01T00:00:00.000Z"


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


def _skills_partition(home: Path, member_id: str) -> Path:
    """全局层里该成员的技能分区（与 ``workspace.global_home_root`` 的 realpath 口径一致）。"""
    return Path(os.path.realpath(home / ".alibot")) / "agents" / member_id / "skills"


def _backup_dirs(data_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in data_dir.parent.iterdir()
        if path.is_dir() and path.name.startswith(f"{data_dir.name}-backup-")
    )


# ─── 技能树与库行 fixture ───────────────────────────────────────────────────
def _write_skill(directory: Path, *, name: str, description: str, body: str = "# Review\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    (directory / "notes.txt").write_text("a\nb\n", encoding="utf-8")
    return directory


def _insert_skill_row(
    db_path: Path,
    *,
    scope_kind: str,
    scope_id: str,
    name: str,
    description: str,
    source_path: Path,
    content_hash: str = PLACEHOLDER_HASH,
) -> str:
    skill_id = f"sk_{scope_kind}_{name}"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            INSERT_SKILL_SQL,
            (
                skill_id,
                name,
                description,
                str(source_path),
                content_hash,
                scope_kind,
                scope_id,
                LEGACY_UPDATED_AT,
                LEGACY_UPDATED_AT,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return skill_id


def _read_row(db_path: Path, member_id: str, name: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(SELECT_SKILL_SQL, (member_id, name)).fetchone()
    finally:
        conn.close()


def _member_skill_count(db_path: Path, member_id: str) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(conn.execute(COUNT_MEMBER_SKILLS_SQL, (member_id,)).fetchone()[0])
    finally:
        conn.close()


@contextmanager
def _seeded(tmp_path: Path) -> Iterator[dict[str, object]]:
    """临时 HOME + 数据目录 + 项目根：造出频道、两个成员与一棵待迁移的旧技能树。

    ``expected_hash`` 来自**真实安装器**（另一个成员的 HOME 副本）——同一份字节经由产品自己的
    安装路径算出的 hash，迁移行必须与它相等（独立真值，不在测试里复制 hash 算法）。
    """
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    source = _write_skill(tmp_path / "source", name="review", description="代码评审清单")

    with backend(data_dir) as client:
        agent = create_agent(client, name="迁移成员").json()["agent"]
        control = create_agent(client, name="对照成员").json()["agent"]
        channel = create_channel(client, name="项目频道", member_ids=[agent["id"]]).json()["channel"]
        patched = client.patch(f"/api/channels/{channel['id']}/config", json={"workspace_root": str(project)})
        assert patched.status_code == 200, patched.text
        installed = client.post(
            "/api/skills",
            json={
                "scope_kind": "agent",
                "scope_id": control["id"],
                "source_path": str(source),
            },
        )
        assert installed.status_code == 201, installed.text
        expected_hash = installed.json()["skill"]["content_hash"]

    yield {
        "home": home,
        "data_dir": data_dir,
        "project": project,
        "source": source,
        "agent": agent,
        "channel": channel,
        "expected_hash": expected_hash,
        "db": data_dir / DATABASE_FILE_NAME,
    }


def _legacy_skill(ctx: dict[str, object], *, partition: str, name: str) -> Path:
    """把源树复制到项目层旧落点 ``<root>/.alibot/skills/<partition>/<name>/``。"""
    destination = Path(str(ctx["project"])) / ".alibot" / "skills" / partition / name
    shutil.copytree(Path(str(ctx["source"])), destination)
    return destination


# ─── ① 预演零副作用 ─────────────────────────────────────────────────────────
def test_dry_run_reports_the_plan_and_writes_nothing(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        agent = ctx["agent"]
        assert isinstance(agent, dict)
        source = _legacy_skill(ctx, partition=str(agent["id"]), name="review")
        _insert_skill_row(
            Path(str(ctx["db"])),
            scope_kind="agent",
            scope_id=str(agent["id"]),
            name="review",
            description="代码评审清单",
            source_path=source,
        )
        data_dir = Path(str(ctx["data_dir"]))
        home = Path(str(ctx["home"]))
        data_before = _tree_snapshot(data_dir)
        project_before = _tree_snapshot(Path(str(ctx["project"])))
        db_sha_before = _sha256(Path(str(ctx["db"])))

        result = _run(home, "migrate-skills", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stderr
        target = _skills_partition(home, str(agent["id"])) / "review"
        assert str(source) in result.stdout
        assert str(target) in result.stdout
        assert "新建" in result.stdout
        assert "预演结束" in result.stdout
        # 零副作用：库、数据目录（备份 / 清单 / sidecar）与项目树都没变，家目录没被创建。
        assert _sha256(Path(str(ctx["db"]))) == db_sha_before
        assert _tree_snapshot(data_dir) == data_before
        assert _tree_snapshot(Path(str(ctx["project"]))) == project_before
        assert not home.exists()


# ─── ② apply 复制、重算 hash、更新行；幂等 ──────────────────────────────────
def test_apply_copies_bytes_recomputes_hash_and_updates_the_row(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        agent = ctx["agent"]
        assert isinstance(agent, dict)
        source = _legacy_skill(ctx, partition=str(agent["id"]), name="review")
        db = Path(str(ctx["db"]))
        skill_id = _insert_skill_row(
            db,
            scope_kind="agent",
            scope_id=str(agent["id"]),
            name="review",
            description="代码评审清单",
            source_path=source,
        )
        data_dir = Path(str(ctx["data_dir"]))
        home = Path(str(ctx["home"]))

        result = _run(home, "migrate-skills", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stderr
        target = _skills_partition(home, str(agent["id"])) / "review"
        assert (target / "SKILL.md").is_file()
        assert (target / "notes.txt").read_text(encoding="utf-8") == "a\nb\n"
        # 源字节不删除。
        assert (source / "SKILL.md").is_file()
        # 行：source_path 标注来源（旧项目层源路径），content_hash / updated_at 对齐真实安装器的 hash。
        row = _read_row(db, str(agent["id"]), "review")
        assert row is not None
        assert row["id"] == skill_id
        assert row["source_path"] == str(source)
        assert row["content_hash"] == ctx["expected_hash"]
        assert row["updated_at"] != LEGACY_UPDATED_AT
        # 备份 + 迁移清单都落在数据目录侧（清单在数据目录里）；备份名与 schema 迁移可区分。
        backups = _backup_dirs(data_dir)
        assert len(backups) == 1
        assert "-backup-skills-" in backups[0].name
        manifests = sorted(data_dir.glob("skill-migration-*.json"))
        assert len(manifests) == 1
        assert str(source) in manifests[0].read_text(encoding="utf-8")
        assert "可手工删除的源路径" in result.stdout

        # 幂等：第二次 apply 不复制、不重复插行、不再建备份。
        backups_before = _backup_dirs(data_dir)
        second = _run(home, "migrate-skills", "--apply", "--yes", "--data-dir", str(data_dir))
        assert second.returncode == SUCCESS, second.stderr
        assert "跳过" in second.stdout
        assert "没有需要写入的条目" in second.stdout
        assert _backup_dirs(data_dir) == backups_before
        row_after = _read_row(db, str(agent["id"]), "review")
        assert row_after is not None
        assert row_after["id"] == skill_id
        assert row_after["updated_at"] == row["updated_at"]
        assert _member_skill_count(db, str(agent["id"])) == 1


# ─── ②b 无库行 → 按成员作用域新建 ───────────────────────────────────────────
def test_apply_creates_an_agent_row_when_the_library_row_is_missing(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        agent = ctx["agent"]
        assert isinstance(agent, dict)
        source = _legacy_skill(ctx, partition=str(agent["id"]), name="review")
        db = Path(str(ctx["db"]))
        data_dir = Path(str(ctx["data_dir"]))
        home = Path(str(ctx["home"]))
        assert _member_skill_count(db, str(agent["id"])) == 0

        result = _run(home, "migrate-skills", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == SUCCESS, result.stderr
        row = _read_row(db, str(agent["id"]), "review")
        assert row is not None
        assert row["scope_kind"] == "agent"
        assert row["scope_id"] == agent["id"]
        assert row["source_path"] == str(source)
        assert row["content_hash"] == ctx["expected_hash"]
        assert row["enabled"] == 1


# ─── ③ 同名不同内容 → 拒绝、不覆盖 ──────────────────────────────────────────
def test_conflicting_target_is_refused_without_overwriting(tmp_path: Path) -> None:
    with _seeded(tmp_path) as ctx:
        agent = ctx["agent"]
        assert isinstance(agent, dict)
        source = _legacy_skill(ctx, partition=str(agent["id"]), name="review")
        db = Path(str(ctx["db"]))
        _insert_skill_row(
            db,
            scope_kind="agent",
            scope_id=str(agent["id"]),
            name="review",
            description="代码评审清单",
            source_path=source,
        )
        home = Path(str(ctx["home"]))
        target = _skills_partition(home, str(agent["id"])) / "review"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            "---\nname: review\ndescription: 人工改过的版本\n---\n\n# Manual\n",
            encoding="utf-8",
        )
        unrelated_before = _sha256(target / "SKILL.md")
        data_dir = Path(str(ctx["data_dir"]))
        backups_before = _backup_dirs(data_dir)

        result = _run(home, "migrate-skills", "--apply", "--yes", "--data-dir", str(data_dir))

        assert result.returncode == SKILL_MIGRATION_REFUSED, result.stdout + result.stderr
        assert "裁定" in result.stderr
        # 不覆盖、不写库、不建备份；源字节仍在。
        assert _sha256(target / "SKILL.md") == unrelated_before
        row = _read_row(db, str(agent["id"]), "review")
        assert row is not None
        assert row["source_path"] == str(source)
        assert row["content_hash"] == PLACEHOLDER_HASH
        assert _backup_dirs(data_dir) == backups_before
        assert (source / "SKILL.md").is_file()


# ─── ④ 频道级字节只进报告 ───────────────────────────────────────────────────
@pytest.mark.parametrize("apply", [False, True])
def test_channel_level_bytes_are_reported_only(tmp_path: Path, apply: bool) -> None:
    with _seeded(tmp_path) as ctx:
        agent = ctx["agent"]
        channel = ctx["channel"]
        assert isinstance(agent, dict)
        assert isinstance(channel, dict)
        legacy = _legacy_skill(ctx, partition=str(channel["id"]), name="channel-only")
        db = Path(str(ctx["db"]))
        _insert_skill_row(
            db,
            scope_kind="channel",
            scope_id=str(channel["id"]),
            name="channel-only",
            description="频道级技能",
            source_path=legacy,
        )
        data_dir = Path(str(ctx["data_dir"]))
        home = Path(str(ctx["home"]))

        args = ["migrate-skills", "--data-dir", str(data_dir)]
        if apply:
            args[1:1] = ["--apply", "--yes"]
        result = _run(home, *args)

        assert result.returncode == SUCCESS, result.stderr
        assert "频道级已弃用，请按成员安装" in result.stdout
        assert "1 个分区 / 1 个技能" in result.stdout
        # 不复制：成员家目录里没有这棵树，全局层根也没被创建。
        assert not home.exists()
        assert not any(path.name == "channel-only" for path in (tmp_path / "home").rglob("*"))
        # 不入库：没有新增 agent 行，频道行原样保留。
        assert _member_skill_count(db, str(agent["id"])) == 0
        assert (legacy / "SKILL.md").is_file()


# ─── CLI 双开关 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "args",
    [
        ["migrate-skills", "--apply"],
        ["migrate-skills", "--yes"],
        ["--apply", "--yes"],
        ["migrate-skills", "--apply", "--yes", "--dry-run"],
        ["migrate-skills", "--migrate"],
    ],
)
def test_destructive_switches_require_both_flags(tmp_path: Path, args: list[str]) -> None:
    result = _run(tmp_path / "home", *args, "--data-dir", str(tmp_path / "missing"))
    assert result.returncode == USAGE_ERROR, result.stdout + result.stderr
