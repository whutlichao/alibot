"""显式迁移入口 ``--migrate`` 的答复矩阵（issue #89）。

规格正本：``app/specs/issue-87-spec.md``（T2：**唯一测试接缝 = CLI 进程入口**）。

本文件只经**真实子进程**取证：``python -m alibot --migrate [--data-dir <目录>]``，配一个磁盘上的
真实数据目录 fixture；断言只落在**外部可观察面**——退出码、stdout / stderr 报告文本、数据目录与库的
终态、文件 sha256。不 import 实现内部函数、不断言私有函数或 SQL 文本（T1）；只从 ``alibot.db`` 取
两个**契约常量**（``DATABASE_FILE_NAME`` / ``SCHEMA_VERSION``）用于造 fixture 与断言。

三条入口（无旗标启动 / ``--migrate`` / ``--repair-targets``）对同一个库必须给**同一个答复**：本文件
直接断言三者的退出码相同、stderr 逐字相同。prior art：``app/tests/test_target_repair.py:419-474``
（真实子进程跑 ``--repair-targets``）、``app/tests/test_schema_version_gate.py``（``tmp_path`` 里的
真实库 fixture + sha256 / 表清单快照）。

fixture 与被测代码**同源**：``tests/conftest.py`` 把本 worktree 的 ``app/`` 插到 ``sys.path`` 最前
（本仓 ``.venv`` 是指向主仓的符号链接，其 editable ``.pth`` 默认指回主仓），子进程则经
``PYTHONPATH=<worktree>/app`` 跑同一份代码。
"""

from __future__ import annotations

import socket
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from alibot.db import DATABASE_FILE_NAME, SCHEMA_VERSION

from tests.conftest import backend

APP_DIR = Path(__file__).resolve().parents[1]

# 三条入口的判据同一个：库不被本代码接受 ⇒ 退出码 3（``__main__.DATA_NOT_ACCEPTED_EXIT_CODE``）。
# 这里写**字面量**而不是 import 常量：断言的是对外契约里的码，不是实现里的标识符。
DATA_NOT_ACCEPTED = 3
# 「库版本已等于本代码版本、但形态不是本入口能处置的」⇒ 退出码 4（复用 ``REPAIR_FAILED_EXIT_CODE``）。
SHAPE_NOT_HANDLED = 4
# 「非 loopback 拒绝启动」⇒ 退出码 2（既有契约，本票不改）。
NON_LOOPBACK = 2

# 造「当前版本但形态不全」的库：删掉一张当前 schema 形态必须存在的表。
# 一句一个模块级常量（与 ``db.py`` / ``repair.py`` 同一体例：不把表名插进 ``execute`` 的实参）。
DROP_REQUIRED_TABLE_SQL = 'DROP TABLE "task"'

# 造「版本号在、但一张业务表都没有、一行业务数据都没有」的库文件。
CREATE_SCHEMA_META_SQL = "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
SET_SCHEMA_VERSION_SQL = "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)"
UPDATE_SCHEMA_VERSION_SQL = "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'"
DELETE_SCHEMA_VERSION_SQL = "DELETE FROM schema_meta WHERE key = 'schema_version'"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """真实子进程跑 ``python -m alibot <args>``。

    ``PYTHONPATH`` 指向本 worktree 的 ``app/``：本仓 ``.venv`` 是符号链接指向主仓，其 editable 安装的
    ``.pth`` 指回**主仓**的 ``app``，不显式指定就会测到主仓代码而不是本 worktree（既有 prior art 同）。
    """
    return subprocess.run(
        [sys.executable, "-m", "alibot", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
        cwd=str(APP_DIR),
    )


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup_siblings(data_dir: Path) -> list[str]:
    """数据目录**同级**里以 ``<数据目录名>-backup-`` 开头的副本（规格 D8 的命名）。"""
    if not data_dir.parent.exists():
        return []
    return sorted(
        path.name for path in data_dir.parent.iterdir() if path.name.startswith(f"{data_dir.name}-backup-")
    )


def _build_current_library_with_data(data_dir: Path) -> Path:
    """用本代码真实建一个当前版本的库，并写入业务数据（一个频道 + owner 成员行）。"""
    with backend(data_dir) as client:
        created = client.post("/api/channels", json={"name": "dev"})
        assert created.status_code == 201, created.text
    return data_dir / DATABASE_FILE_NAME


def _stamp_version(db_path: Path, version: str | None) -> None:
    """把库里的 ``schema_version`` 改写成指定读数（``None`` = 删掉那一行）。"""
    conn = sqlite3.connect(db_path)
    try:
        if version is None:
            conn.execute(DELETE_SCHEMA_VERSION_SQL)
        else:
            conn.execute(UPDATE_SCHEMA_VERSION_SQL, (version,))
        conn.commit()
    finally:
        conn.close()


def _build_version_only_library(data_dir: Path) -> Path:
    """只有一个版本行、**没有任何业务表与业务行**的库文件（「没有业务数据且缺表」形态）。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / DATABASE_FILE_NAME
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(CREATE_SCHEMA_META_SQL)
        conn.execute(SET_SCHEMA_VERSION_SQL, (str(SCHEMA_VERSION),))
        conn.commit()
    finally:
        conn.close()
    return db_path


def _drop_required_table(db_path: Path) -> None:
    """删掉一张当前形态必须存在的表（本票复用 ``repair._CURRENT_REQUIRED_TABLES`` 的判据）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(DROP_REQUIRED_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


# ─── 三条入口 ────────────────────────────────────────────────────────────────
def _startup_argv(data_dir: Path) -> list[str]:
    return ["--data-dir", str(data_dir), "--port", str(_free_port())]


def _migrate_argv(data_dir: Path) -> list[str]:
    return ["--migrate", "--data-dir", str(data_dir)]


def _repair_argv(data_dir: Path) -> list[str]:
    return ["--repair-targets", "--data-dir", str(data_dir)]


ENTRY_POINTS: dict[str, Callable[[Path], list[str]]] = {
    "startup": _startup_argv,
    "migrate": _migrate_argv,
    "repair-targets": _repair_argv,
}

# 不被本代码接受的库：版本低于 3 / 版本号缺失但有业务数据 / 认不出的号 / 未来版本
# （以及 ① 阶段尚无迁移步骤、覆盖范围为空时的 ``3``..``SCHEMA_VERSION - 1``）。
REJECTED_VERSIONS = [
    pytest.param("2", id="below-min-supported"),
    pytest.param("3", id="lowest-supported-source"),
    pytest.param(str(SCHEMA_VERSION - 1), id="one-below-code-version"),
    pytest.param(str(SCHEMA_VERSION + 1), id="future-version"),
    pytest.param("not-a-number", id="unrecognised-version"),
    pytest.param(None, id="version-missing-with-business-data"),
]


# ─── S1：命令面 ──────────────────────────────────────────────────────────────
def test_help_lists_the_migrate_flag_with_its_description() -> None:
    """``--help`` 列出 ``--migrate`` **与它的说明**（S1），且进程正常退出。"""
    completed = _run("--help")

    assert completed.returncode == 0
    assert "--migrate" in completed.stdout
    # 说明文本（不是光秃秃一个旗标名）：入口定位与「不起监听」这两句都要看得见。
    assert "显式迁移入口" in completed.stdout
    assert "不起监听" in completed.stdout


def test_migrate_runs_as_a_batch_and_does_not_start_a_listening_server(tmp_path: Path) -> None:
    """``--migrate`` 跑完直接退出、**不起监听**（S1）。

    两道外部观察：① stdout 里没有启动横幅（``http://<host>:<port>/studio/``）；② 把 ``--port`` 指到
    一个**已被占住**的端口，它仍退出 0——真去起了 uvicorn 的话，绑定会因端口占用而失败、退出码非 0。
    """
    data_dir = tmp_path / "empty"
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = int(busy.getsockname()[1])

        completed = _run("--migrate", "--data-dir", str(data_dir), "--port", str(port))

    assert completed.returncode == 0, completed.stderr
    assert "http://" not in completed.stdout


# ─── S3 / S4 / S14：拒绝正文与零字节改动，三条入口同一个答复 ──────────────────
@pytest.mark.parametrize("entry_point", sorted(ENTRY_POINTS))
@pytest.mark.parametrize("stored_version", REJECTED_VERSIONS)
def test_every_entry_point_refuses_the_same_library_with_the_same_verdict(
    tmp_path: Path, stored_version: str | None, entry_point: str
) -> None:
    """版本不被接受 ⇒ 退出码 3 + 新拒绝正文，且库文件 sha256 前后一致。

    正文必须含库路径、库里的版本、本代码需要的版本、``--migrate`` 命令、备份指引，并说明本版尚无
    可用迁移步骤；**不得**再出现「本版不做数据迁移」（S3、S4、S14、S16）。
    """
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _stamp_version(db_path, stored_version)
    digest_before = _sha256(db_path)

    completed = _run(*ENTRY_POINTS[entry_point](data_dir))

    assert completed.returncode == DATA_NOT_ACCEPTED, (
        f"{entry_point} 对 stored={stored_version!r} 的库必须退出 {DATA_NOT_ACCEPTED}，"
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _sha256(db_path) == digest_before, "拒绝路径不得改动库文件一个字节"

    stderr = completed.stderr
    assert str(db_path) in stderr
    assert str(SCHEMA_VERSION) in stderr
    assert "--migrate" in stderr and "--data-dir" in stderr
    assert "备份" in stderr
    assert "源版本范围" in stderr
    assert "本版尚无可用迁移步骤" in stderr
    assert "不做数据迁移" not in stderr
    if stored_version is None:
        assert "缺失" in stderr
    else:
        assert stored_version in stderr


def test_the_three_entry_points_report_byte_identical_refusals(tmp_path: Path) -> None:
    """同一个库、三条路径 ⇒ 同一退出码、**逐字相同**的答复（S3 / S14：「同一个库，一个说法」）。"""
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _stamp_version(db_path, str(SCHEMA_VERSION + 1))

    results = {name: _run(*argv(data_dir)) for name, argv in ENTRY_POINTS.items()}

    assert {result.returncode for result in results.values()} == {DATA_NOT_ACCEPTED}
    assert len({result.stderr for result in results.values()}) == 1, {
        name: result.stderr for name, result in results.items()
    }


def test_below_min_supported_version_reports_the_source_version_range(tmp_path: Path) -> None:
    """版本低于 3 ⇒ 3，且正文给出本版支持的源版本范围（S2 / D4；acceptance 第 4 条）。"""
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _stamp_version(db_path, "2")

    completed = _run("--migrate", "--data-dir", str(data_dir))

    assert completed.returncode == DATA_NOT_ACCEPTED
    assert "3" in completed.stderr and str(SCHEMA_VERSION) in completed.stderr
    assert "源版本范围" in completed.stderr


def test_non_loopback_address_still_exits_with_two(tmp_path: Path) -> None:
    """无旗标启动路径：非 loopback 地址 ⇒ 退出码 2（既有契约不变；S4 的边界）。"""
    completed = _run("--data-dir", str(tmp_path / "data"), "--host", "0.0.0.0", "--port", str(_free_port()))

    assert completed.returncode == NON_LOOPBACK
    assert "loopback" in completed.stderr


def test_migrate_never_exits_with_two_even_on_a_non_loopback_host(tmp_path: Path) -> None:
    """``--migrate`` 不监听 ⇒ ``--host`` 对它无意义：答复集合是 ``{0, 3, 4}``，不含 2（规格 D2）。

    实测路径：非 loopback 主机 + 一本不被接受的库 ⇒ 仍然给出**版本闸门**的答复（3），而不是启动路径
    的「非 loopback ⇒ 2」。
    """
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _stamp_version(db_path, str(SCHEMA_VERSION - 1))

    completed = _run("--migrate", "--host", "0.0.0.0", "--data-dir", str(data_dir))

    assert completed.returncode == DATA_NOT_ACCEPTED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert "loopback" not in completed.stderr


# ─── S10 / D2：0 —— 形态完好的当前版本库、空目录、缺表但无业务数据 ─────────────
def test_migrate_on_a_current_and_complete_library_is_a_noop_without_backup(tmp_path: Path) -> None:
    """当前版本、形态完好 ⇒ 退出 0、无事可做、**不建备份**（S10 的一半、D9）。"""
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    digest_before = _sha256(db_path)

    completed = _run("--migrate", "--data-dir", str(data_dir))

    assert completed.returncode == 0, completed.stderr
    assert _sha256(db_path) == digest_before
    assert _backup_siblings(data_dir) == [], "无事可做时不得新建备份副本"


@pytest.mark.parametrize("precreate", [False, True], ids=["directory-does-not-exist", "empty-directory"])
def test_migrate_on_an_empty_directory_is_zero_and_creates_no_database(
    tmp_path: Path, precreate: bool
) -> None:
    """没有库文件的空目录 ⇒ 0，且**不凭空创建库**（S10 的另一半、D9）。"""
    data_dir = tmp_path / "empty"
    if precreate:
        data_dir.mkdir()

    completed = _run("--migrate", "--data-dir", str(data_dir))

    assert completed.returncode == 0, completed.stderr
    assert not (data_dir / DATABASE_FILE_NAME).exists(), "迁移入口不得在空目录里凭空建库"


def test_migrate_on_a_version_only_library_without_business_data_is_zero(tmp_path: Path) -> None:
    """版本号在、但没有任何业务表与业务行 ⇒ 0（空库口径不变：缺表但没数据不算坏库）。"""
    data_dir = tmp_path / "data"
    db_path = _build_version_only_library(data_dir)
    digest_before = _sha256(db_path)

    completed = _run("--migrate", "--data-dir", str(data_dir))

    assert completed.returncode == 0, completed.stderr
    assert _sha256(db_path) == digest_before
    assert _backup_siblings(data_dir) == []


# ─── S2 的 4 / D2：版本已是本代码版本、但缺当前形态必须存在的表且有业务数据 ────
def test_migrate_on_a_current_but_incomplete_library_with_data_is_four(tmp_path: Path) -> None:
    """该形态 ⇒ 退出 4，**发生在任何写入之前**：sha256 不变、不出现新备份（S2 的 4、D2）。"""
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _drop_required_table(db_path)
    digest_before = _sha256(db_path)

    completed = _run("--migrate", "--data-dir", str(data_dir))

    assert completed.returncode == SHAPE_NOT_HANDLED, (
        f"实际 {completed.returncode}（stdout={completed.stdout!r} stderr={completed.stderr!r}）"
    )
    assert _sha256(db_path) == digest_before, "形态不对的答复必须发生在任何写入之前"
    assert _backup_siblings(data_dir) == [], "4 是「还没动手」：不得新建备份副本"
    assert str(db_path) in completed.stderr


def test_repair_targets_also_reports_four_for_the_same_library(tmp_path: Path) -> None:
    """同一个库上别名给同一个答复（S13 的判据之一；别名与迁移入口同判）。"""
    data_dir = tmp_path / "data"
    db_path = _build_current_library_with_data(data_dir)
    _drop_required_table(db_path)
    digest_before = _sha256(db_path)

    completed = _run("--repair-targets", "--data-dir", str(data_dir))

    assert completed.returncode == SHAPE_NOT_HANDLED, completed.stderr
    assert _sha256(db_path) == digest_before
