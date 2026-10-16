"""弃用别名 ``--repair-targets``（issue #94）。

规格正本：``app/specs/issue-87-spec.md``（**D11** 弃用别名 / **S13** 等价性 / **S14** 方向不放宽；
T2：**唯一测试接缝 = CLI 进程入口**）。ADR：
``docs/adr/0015-schema-migration-is-explicit-and-forward-only.md`` 第五节。

只经**真实子进程**取证：``python -m alibot --repair-targets --data-dir <目录>`` 与
``python -m alibot --migrate --data-dir <目录>``，配磁盘上的真实数据目录 fixture；断言只落在
**外部可观察面**——退出码、stdout / stderr 报告文本、数据目录与库的终态、文件 sha256。不 import
实现内部函数、不断言私有函数或 SQL 文本（T1）。

本文件守的四件事（对应票面 Observable acceptance）：

1. **同一库、两条入口等价**（S13）：退出码相同、**stderr 逐字相同**；stdout 的唯一差异是别名最前面
   多一行「请改用 ``--migrate``」。可迁库用**两个各自独立**的副本跑（避免前一次迁移影响后一次）；
   拒绝路径（``3`` / ``4``）不写库，两条入口在**同一个库文件**上跑，于是「逐字相同」是最强的形式。
2. **半改名库走到终态**（S13 / D11）：``schema_version`` 已是 4、数据还在 ``channel_*`` 的库跑别名
   ⇒ 退 ``0``、四张表补齐、``target_budget.used`` 不归零、库里版本 = ``SCHEMA_VERSION``、本代码 app
   能打开它——**不是**「成功但什么都没变」。
3. **接受范围 = ``[3, SCHEMA_VERSION]``**（S13 / E1）：对 ``3``…``11`` 的每个源版本，别名都退 ``0``
   并到终态；未来版本（``SCHEMA_VERSION + 1``）仍退 ``3``、库 sha256 不变（S14：方向没放宽）。
4. **``4`` 同判**（S2 / D0）：版本已是本代码版本、缺当前形态必须存在的表、且有业务数据 ⇒ 别名退
   ``4``，且不写入、不建备份（发生在任何写入之前）。

历史逐版 fixture 复用既有留档与 builder（与 ``test_migration_chain_guard.py`` / ``test_migrate_dry_run.py``
同一体例）：只借它们的**文件级外部面**（起真实子进程、造磁盘上的真实数据目录），不改那些文件、
也不在它们上面加断言。
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

import pytest
from alibot.db import BUDGET_LIMIT, DATABASE_FILE_NAME, MIN_SUPPORTED_SOURCE_VERSION, SCHEMA_VERSION, Database

from tests.conftest import backend
from tests.test_migrate_cli import (
    _build_current_library_with_data,
    _drop_required_table,
    _stamp_version,
)
from tests.test_schema_migration import (
    _assert_terminal_shape,
    _backup_dirs,
    _build_library,
    _fresh_current_library,
    _migrate_argv,
    _row_counts,
    _run,
    _sha256,
    _shape,
)
from tests.test_shape_conditioned_step import _build_half_renamed_library

# 迁移入口的对外契约码（与既有迁移测试同体例：写**字面量**而不是 import 实现常量）。
MIGRATION_SUCCEEDED = 0
DATA_NOT_ACCEPTED = 3
SHAPE_NOT_HANDLED = 4

# 备份落点的时间戳（``%Y%m%dT%H%M%SZ``）：两条入口的备份各自带一次时间戳，比较 stdout 时把它抹平。
_TIMESTAMP_RE = re.compile(r"\d{8}T\d{6}Z")
# 预演副本名（``.alibot-dry-run-<随机后缀>``）：两个独立副本各有自己的随机名，同样要在比较前抹平。
_DRY_RUN_COPY_RE = re.compile(r"\.alibot-dry-run-[A-Za-z0-9_]+")


def _repair_argv(data_dir: Path) -> list[str]:
    return ["--repair-targets", "--data-dir", str(data_dir)]


def _strip_notice(stdout: str) -> str:
    """去掉别名多出来的那一行弃用提示，返回其后与 ``--migrate`` 应当逐字相同的正文。

    弃用提示必须打在**最前面**（规格 D11「先打印一句」），且必须含「请改用」与 ``--migrate``。
    """
    lines = stdout.splitlines(keepends=True)
    assert lines, "别名必须先打一句弃用提示，实际 stdout 为空"
    assert "请改用" in lines[0] and "--migrate" in lines[0], f"首行不是弃用提示：{lines[0]!r}"
    return "".join(lines[1:])


def _normalize(text: str, *roots: Path) -> str:
    """把两个独立副本各自的根路径 / 备份时间戳 / 预演副本名抹平成占位符，使报告可直接逐字比较。"""
    for root in roots:
        text = text.replace(str(root), "<ROOT>")
    text = _TIMESTAMP_RE.sub("<TS>", text)
    return _DRY_RUN_COPY_RE.sub(".alibot-dry-run-<COPY>", text)


def _assert_alias_matches_migrate(
    migrate: subprocess.CompletedProcess[str],
    alias: subprocess.CompletedProcess[str],
    *,
    normalize_stdout: bool = False,
    roots: tuple[Path, ...] = (),
) -> None:
    """两条入口的等价性判据（S13）：退出码相同、stderr 逐字相同、stdout 只差那一行弃用提示。"""
    assert alias.returncode == migrate.returncode, (
        f"退出码不同：--migrate={migrate.returncode} --repair-targets={alias.returncode}"
    )
    assert alias.stderr == migrate.stderr, (
        f"stderr 不逐字相同：\n--migrate={migrate.stderr!r}\n--repair-targets={alias.stderr!r}"
    )
    alias_body = _strip_notice(alias.stdout)
    migrate_stdout = migrate.stdout
    if normalize_stdout:
        alias_body = _normalize(alias_body, *roots)
        migrate_stdout = _normalize(migrate_stdout, *roots)
    assert alias_body == migrate_stdout, (
        f"去掉弃用提示后的 stdout 与 --migrate 不同：\n--migrate={migrate_stdout!r}\n"
        f"--repair-targets={alias_body!r}"
    )


# ─── 验收 1：同一库、两条入口等价（S13） ─────────────────────────────────────
def test_alias_matches_migrate_on_a_migratable_library(tmp_path: Path) -> None:
    """可迁的 v3 库：两个**各自独立**的副本上分别跑两条入口 ⇒ 同一退出码、同一结果、同一终态。

    别名的 stderr 与 ``--migrate`` 逐字相同（成功路径上两者都为空）；stdout 唯一差异是别名多出的
    那一行弃用提示（各副本的路径与备份时间戳在比较前抹平）。
    """
    migrate_dir = tmp_path / "migrate" / "data"
    alias_dir = tmp_path / "alias" / "data"
    _build_library(migrate_dir, 3)
    _build_library(alias_dir, 3)

    migrate = _run(*_migrate_argv(migrate_dir))
    alias = _run(*_repair_argv(alias_dir))

    assert migrate.returncode == MIGRATION_SUCCEEDED, migrate.stderr
    _assert_alias_matches_migrate(
        migrate, alias, normalize_stdout=True, roots=(tmp_path / "migrate", tmp_path / "alias")
    )

    # 「同一结果」= 两条路径把两个同源副本推到**同一个终态**（形态快照逐项相同）。
    assert _shape(migrate_dir / DATABASE_FILE_NAME) == _shape(alias_dir / DATABASE_FILE_NAME)
    assert _shape(alias_dir / DATABASE_FILE_NAME)["version"] == str(SCHEMA_VERSION)


def test_alias_refuses_a_future_version_library_with_migrates_own_verdict(tmp_path: Path) -> None:
    """未来版本（``SCHEMA_VERSION + 1``）：别名退 ``3``、库 sha256 不变、与 ``--migrate`` 同一答复（S14）。

    两条入口都不写库，因此在**同一个库文件**上跑：stderr 逐字相同是最强的形式；别名 stdout 只有那一行
    弃用提示，不留新备份。
    """
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _stamp_version(db_path, str(SCHEMA_VERSION + 1))
    digest_before = _sha256(db_path)

    migrate = _run(*_migrate_argv(data_dir))
    alias = _run(*_repair_argv(data_dir))

    assert migrate.returncode == DATA_NOT_ACCEPTED, migrate.stderr
    _assert_alias_matches_migrate(migrate, alias)
    assert "请改用" in alias.stdout
    assert "源版本范围" in alias.stderr
    assert _sha256(db_path) == digest_before, "拒绝路径不得改动库文件一个字节"
    assert _backup_dirs(data_dir) == [], "拒绝路径不得新建备份"


def test_alias_reports_four_for_a_current_but_incomplete_library(tmp_path: Path) -> None:
    """``4`` 同判（S2 的 ``4`` / D0）：版本已是本代码版本、缺当前形态必须存在的表且有业务数据。

    两条入口都在**任何写入之前**给 ``4``：库 sha256 不变、不出现新备份；别名与 ``--migrate`` 的 stderr
    逐字相同。
    """
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _drop_required_table(db_path)
    digest_before = _sha256(db_path)

    migrate = _run(*_migrate_argv(data_dir))
    alias = _run(*_repair_argv(data_dir))

    assert migrate.returncode == SHAPE_NOT_HANDLED, migrate.stderr
    _assert_alias_matches_migrate(migrate, alias)
    assert _sha256(db_path) == digest_before, "4 是「还没动手」：库文件必须一个字节不变"
    assert _backup_dirs(data_dir) == [], "4 是「还没动手」：不得新建备份"


# ─── 验收 2：半改名库走别名到终态（S13 / D11） ───────────────────────────────
def test_half_renamed_library_at_stored_four_reaches_the_terminal_state_via_the_alias(
    tmp_path: Path,
) -> None:
    """``schema_version=4``、数据仍在 ``channel_*`` 的库跑别名 ⇒ ``0``、补齐四张表并推进到终态。

    这三个读数一起排除「成功但什么都没变」与「停在 4 等用户再跑一次」：库里版本 = ``SCHEMA_VERSION``、
    四张表有行、``target_budget.used`` 仍是旧表里那份**已耗尽**的账、且本代码 app 能打开它。
    """
    data_dir = tmp_path / "data"
    db_path = _build_half_renamed_library(data_dir, stored_version=4)

    completed = _run(*_repair_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "请改用" in completed.stdout
    assert completed.stderr == ""
    assert _shape(db_path)["version"] == str(SCHEMA_VERSION), "别名必须把库推进到本代码版本"

    counts = _row_counts(db_path)
    assert counts["target"] == 1, "影子行必须补齐"
    assert counts["target_member"] == 2
    assert counts["target_read"] == 2
    assert counts["target_budget"] == 1

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        used = conn.execute("SELECT used FROM target_budget WHERE target_id = 'ch_dev'").fetchone()[0]
    finally:
        conn.close()
    assert used == BUDGET_LIMIT, "已耗尽的预算不得被归零（D5 / User Story 19）"

    # 本代码的 app 能打开它——这才是「到终态」的可观察判据（HTTP 面与本代码的库入口各取一次）。
    database = Database(data_dir)
    try:
        assert [channel["id"] for channel in database.list_channels()] == ["ch_dev"]
    finally:
        database.close()
    with backend(data_dir) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/channels").json()["channels"][0]["id"] == "ch_dev"


# ─── 验收 3：接受范围 = [3, SCHEMA_VERSION]（S13 / E1） ──────────────────────
@pytest.mark.parametrize("version", range(MIN_SUPPORTED_SOURCE_VERSION, SCHEMA_VERSION))
def test_alias_accepts_every_source_version_in_the_supported_range(tmp_path: Path, version: int) -> None:
    """对 ``3``…``11`` 的每个源版本各造一个库并跑**别名** ⇒ 都 ``0`` 且到终态。

    终态判据沿用既有迁移测试的「新库对照 + 结构断言」（D7）：与 ``--migrate`` 走同一条链，因此两个
    入口的终态是可对照的同一份。
    """
    data_dir = tmp_path / f"data-v{version}"
    _build_library(data_dir, version)

    completed = _run(*_repair_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"v{version} 必须被别名接受，实际 {completed.returncode}"
        f"（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "请改用" in completed.stdout
    _assert_terminal_shape(
        data_dir / DATABASE_FILE_NAME, _fresh_current_library(tmp_path, f"fresh-v{version}")
    )


# ─── 别名是「同一个入口」：--dry-run 这条修饰旗标也一并继承 ──────────────────
def test_alias_dry_run_is_the_same_read_only_preview_as_migrates(tmp_path: Path) -> None:
    """``--repair-targets --dry-run`` 与 ``--migrate --dry-run`` 等价：同一退出码、同一报告、原库零改动。

    别名既然是**同一个入口**（规格 D11），``--dry-run`` 这条修饰旗标就不该在它上面变成用法错误：它与
    ``--migrate --dry-run`` 跑同一条只读路径，两个各自独立的副本上 sha256 前后读数一致。
    """
    migrate_dir = tmp_path / "migrate" / "data"
    alias_dir = tmp_path / "alias" / "data"
    _build_library(migrate_dir, 3)
    _build_library(alias_dir, 3)
    migrate_digest = _sha256(migrate_dir / DATABASE_FILE_NAME)
    alias_digest = _sha256(alias_dir / DATABASE_FILE_NAME)

    migrate = _run("--migrate", "--dry-run", "--data-dir", str(migrate_dir))
    alias = _run("--repair-targets", "--dry-run", "--data-dir", str(alias_dir))

    assert migrate.returncode == MIGRATION_SUCCEEDED, migrate.stderr
    _assert_alias_matches_migrate(
        migrate, alias, normalize_stdout=True, roots=(tmp_path / "migrate", tmp_path / "alias")
    )
    assert _sha256(migrate_dir / DATABASE_FILE_NAME) == migrate_digest, "预演不得碰原库"
    assert _sha256(alias_dir / DATABASE_FILE_NAME) == alias_digest, "预演不得碰原库"
