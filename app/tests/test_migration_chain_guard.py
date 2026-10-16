"""纪律守卫之一（issue #95 / 规格 #87 的 S11 / S17 / D13，判据来源 ADR-0015 第三节）：迁移链的连续性。

接缝 = 规格 **T2 的唯一测试接缝**：真实子进程 ``python -m alibot --migrate --data-dir <目录>``，
配磁盘上的真实数据目录 fixture；断言只落在**外部可观察面**——退出码与 stdout 报告（T1：不断言私有
函数、SQL 文本、注册表内部表示）。spec T2 明确把「步骤注册表（直接读步骤表数据结构）」列为被否的接缝。

守卫的是纪律：此后每次 schema 变更都必须随票携带一条 ``from → to`` 步骤。

- 谁 bump 了 ``SCHEMA_VERSION`` 却不带 ``from → to`` 步骤 ⇒ 计划覆盖不到目标版本 ⇒ 这里红（S17）；
- 谁把某步的 ``from`` 写成落后号（历史上那次 ``9 → 11``）⇒ 报告的步骤表不连续 ⇒ 这里红（S11）。

因此断言必须显式落在两件事上，而不是「迁移成功」：**报告的步骤表自身连续**、**终点覆盖到
``SCHEMA_VERSION``**；外加**钉住下界 3**（版本读数低于 3 的库被拒、v3 库被接受）。

与既有 ``app/tests/test_schema_migration.py`` 的分工：那里已覆盖「每个源版本都迁到终态」与「报告里
含每一步 ``from → to`` 文本」；本文件只加**增量**——把报告的步骤表当**数据**解析出来，断言它自身
连续、起点正确、终点覆盖目标版本，并钉住下界。

**接缝裁定的来历（记录在案）**：本票票面验收语写「断言步骤注册表」，这与规格 T1（不断言私有函数 /
SQL 文本 / 注册表内部表示）和 T2（把「步骤注册表」列为被否接缝）冲突。按票面自己「票面与规格冲突时
以规格为准」的条款，coordinator 裁定链连续性走 T2 的 CLI 接缝；直接读 ``migrate.STEPS`` 的注入式
判定降级为 ``.scratch/registry_guard_red_check.py`` 的取证脚本——**不入库、不进 PR 的测试面**。
"""

from __future__ import annotations

import hashlib
import itertools
import re
import sqlite3
from pathlib import Path

import pytest
from alibot.db import MIN_SUPPORTED_SOURCE_VERSION, SCHEMA_VERSION

# v3 形态库的搭建逻辑很重（历史对象 + 补列 + 灌数据），复用既有迁移测试的 fixture builder。
# 本票的硬约束是「只新增测试文件、不改既有测试文件」，而仓里放共享 fixture 的 ``tests/conftest.py``
# 与 ``tests/_migration_history.py`` 都在禁改范围内，因此这里直连那个 builder——它只碰文件级外部面。
from tests.test_schema_migration import _build_library, _migrate_argv, _run

# 迁移入口的对外契约码（与既有迁移测试同体例：写**字面量**而不是 import 实现常量）。
MIGRATION_SUCCEEDED = 0
DATA_NOT_ACCEPTED = 3

# 受支持源版本的**字面**下界（规格 D4 / ADR-0015 §二：「从 3 起连续支持到 SCHEMA_VERSION」）。
SUPPORTED_FLOOR = 3

# 报告里的步骤行形如 ``  {from} → {to}  {label}  改动 {n} 行``（缩进两格）；报告头部那行
# ``源版本：{from} → 目标版本：{to}`` 没有缩进，因此不会被这条正则命中。
STEP_LINE_RE = re.compile(r"^ {2}(\d+) → (\d+) {2}", re.MULTILINE)


def _reported_steps(report: str) -> list[tuple[int, int]]:
    """把 ``--migrate`` 报告里的步骤表解析成 ``(from, to)`` 序列（只取数据，不依赖渲染细节之外的东西）。"""
    return [(int(frm), int(to)) for frm, to in STEP_LINE_RE.findall(report)]


def _stamp_version(db_path: Path, version: int) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(version),))
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_continuous_and_covering(steps: list[tuple[int, int]], first: int) -> None:
    """步骤表必须从 ``first`` 起、每步严格 +1、彼此衔接、最后一个 ``to`` == ``SCHEMA_VERSION``。"""
    assert steps, "报告里没有步骤表"
    assert steps[0][0] == first, f"步骤表没有从库版本 {first} 起步：{steps[0]}"
    assert all(to == frm + 1 for frm, to in steps), f"步骤表里有不是严格 +1 的一步：{steps}"
    for previous, current in itertools.pairwise(steps):
        assert current[0] == previous[1], f"步骤表不连续：{previous} 之后接 {current}"
    assert steps[-1][1] == SCHEMA_VERSION, (
        f"计划没有覆盖到 SCHEMA_VERSION = {SCHEMA_VERSION}：止于 {steps[-1][1]}"
    )


# ─── 链连续性（正向） ────────────────────────────────────────────────────────
def test_the_reported_step_table_is_continuous_and_covers_the_schema_version(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _build_library(data_dir, MIN_SUPPORTED_SOURCE_VERSION)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, completed.stderr
    _assert_continuous_and_covering(_reported_steps(completed.stdout), MIN_SUPPORTED_SOURCE_VERSION)


@pytest.mark.parametrize("version", range(MIN_SUPPORTED_SOURCE_VERSION, SCHEMA_VERSION))
def test_every_source_version_reports_a_continuous_step_table_to_the_target(
    tmp_path: Path, version: int
) -> None:
    """执行器从库里的版本起逐步走、不跳步：每个受支持源版本的报告都是连续且覆盖到目标版本的步骤表。"""
    data_dir = tmp_path / f"data-v{version}"
    _build_library(data_dir, version)

    completed = _run(*_migrate_argv(data_dir))

    assert completed.returncode == MIGRATION_SUCCEEDED, (
        f"v{version} 必须被接受，实际 {completed.returncode}（stderr={completed.stderr!r}）"
    )
    _assert_continuous_and_covering(_reported_steps(completed.stdout), version)


# ─── 受支持范围的下界 = 3（规格 D4 / ADR-0015 §二） ─────────────────────────
def test_the_supported_range_floor_is_three(tmp_path: Path) -> None:
    """v3 被接受；版本读数低于 3 的库被拒（退出码 3、库零字节改动）。

    下界按规格 **字面**钉死：常量若被挪到 4（或别处），这条先红——否则「从 3 起」这条契约会被
    「两侧同步漂移」静默绕过。
    """
    assert MIN_SUPPORTED_SOURCE_VERSION == SUPPORTED_FLOOR, (
        f"受支持源版本的起点被移动了：{MIN_SUPPORTED_SOURCE_VERSION} != {SUPPORTED_FLOOR}"
    )

    accepted_dir = tmp_path / "accepted"
    _build_library(accepted_dir, SUPPORTED_FLOOR)
    accepted = _run(*_migrate_argv(accepted_dir))
    assert accepted.returncode == MIGRATION_SUCCEEDED, accepted.stderr

    refused_dir = tmp_path / "refused"
    db_path = _build_library(refused_dir, SUPPORTED_FLOOR)
    _stamp_version(db_path, SUPPORTED_FLOOR - 1)
    digest_before = _sha256(db_path)

    refused = _run(*_migrate_argv(refused_dir))

    assert refused.returncode == DATA_NOT_ACCEPTED, refused.stdout
    assert _sha256(db_path) == digest_before, "被拒的库不得被改动一个字节"
