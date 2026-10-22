"""数据目录默认与兼容解析（issue #231）。

接缝：**进程启动**（规格 D11 Seam B / T3）。全部经真实子进程观察，只断言外部可观察面：
启动输出里的实际数据目录与来源、退出码，以及临时 HOME 下的目录 / 文件事实。不 import 私有名。

为了让启动在**打开库之前**收口，用例统一给非 loopback 地址：既有契约是退出码 2，
而数据目录解析与启动输出发生在那之前——于是可以观察解析结果，又不会让 SQLite 改写任何目录。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]

# 「非 loopback 拒绝启动」的既有退出码（对外契约字面量，不 import 实现常量）。
NON_LOOPBACK = 2
# 启动输出里「实际使用哪个数据目录」与「来源」的两行前缀。
DATA_DIR_LINE = "数据目录（备份即复制该目录）："
SOURCE_LINE = "数据目录来源："
# 四种来源的可读标签（规格 D2 / 票面 Acceptance criteria）。
SOURCE_EXPLICIT = "显式覆盖"
SOURCE_NEW_DEFAULT = "新默认"
SOURCE_LEGACY = "旧位置兼容"
SOURCE_CREATED = "新建"

# 固定的旧 mtime：断言「解析 / 启动不碰旧目录」时不依赖真实时间。
FROZEN_MTIME = 1_600_000_000


def _run(home: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """真实子进程跑 ``python -m alibot``，HOME 指向临时目录。"""
    env = {
        "PYTHONPATH": str(APP_DIR),
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        # 帮助文本按终端宽度折行；给足宽度，长临时路径才不会被 argparse 折断。
        "COLUMNS": "500",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "alibot", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(APP_DIR),
    )


def _startup(
    home: Path, *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """非 loopback 启动：解析数据目录并打印启动输出，随后在打开库之前以 2 收口。"""
    return _run(home, "--host", "0.0.0.0", "--port", "0", *args, extra_env=extra_env)


def _line_with_prefix(completed: subprocess.CompletedProcess[str], prefix: str) -> str:
    return next(line for line in completed.stdout.splitlines() if line.startswith(prefix))


def _seed_dir(path: Path, *, file_name: str = "alibot.db", content: str = "fixture") -> Path:
    """造一个带文件、mtime 固定的数据目录。"""
    path.mkdir(parents=True, exist_ok=True)
    target = path / file_name
    target.write_text(content, encoding="utf-8")
    os.utime(target, (FROZEN_MTIME, FROZEN_MTIME))
    os.utime(path, (FROZEN_MTIME, FROZEN_MTIME))
    return path


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """目录树的 (mtime_ns, 文件字节) 快照；目录本身也计入。"""
    snapshot: dict[str, tuple[int, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        snapshot[str(path.relative_to(root))] = (
            stat.st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    snapshot["."] = (root.stat().st_mtime_ns, None)
    return snapshot


@contextmanager
def _home(tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "home"
    home.mkdir()
    yield home


# ─── 判据 4：两者都不存在 ⇒ 新建新默认 ──────────────────────────────────────
def test_creates_new_hidden_default_when_neither_location_exists(tmp_path: Path) -> None:
    with _home(tmp_path) as home:
        completed = _startup(home)

        assert completed.returncode == NON_LOOPBACK, completed.stderr
        new_default = home / ".alibot" / "data"
        assert _line_with_prefix(completed, DATA_DIR_LINE).endswith(str(new_default))
        assert _line_with_prefix(completed, SOURCE_LINE).endswith(SOURCE_CREATED)
        assert new_default.is_dir()
        # 创建纪律的唯一例外：只建数据目录（连同 ``~/.alibot`` 父目录）；
        # ``agents/`` 与 ``skills/`` 仍不被应用创建。
        assert (home / ".alibot").is_dir()
        assert not (home / ".alibot" / "agents").exists()
        assert not (home / ".alibot" / "skills").exists()


# ─── 判据 2：新旧同时存在 ⇒ 选新默认，旧位置被忽略且原样不动 ─────────────────
def test_existing_new_default_wins_over_legacy_and_legacy_is_untouched(tmp_path: Path) -> None:
    with _home(tmp_path) as home:
        legacy = _seed_dir(home / "Alibot", content="legacy-bytes")
        new_default = _seed_dir(home / ".alibot" / "data", content="new-bytes")
        legacy_before = _snapshot(legacy)
        new_before = _snapshot(new_default)

        completed = _startup(home)

        assert completed.returncode == NON_LOOPBACK, completed.stderr
        assert _line_with_prefix(completed, DATA_DIR_LINE).endswith(str(new_default))
        assert _line_with_prefix(completed, SOURCE_LINE).endswith(SOURCE_NEW_DEFAULT)
        assert str(legacy) in completed.stdout
        assert "忽略" in completed.stdout
        # 两份数据都不删除、不搬移、不合并。
        assert _snapshot(legacy) == legacy_before
        assert _snapshot(new_default) == new_before


# ─── 判据 3：只有旧位置 ⇒ 兼容读取并给可读提示，绝不自动搬移 ─────────────────
def test_legacy_location_is_used_with_readable_compatibility_notice(tmp_path: Path) -> None:
    with _home(tmp_path) as home:
        legacy = _seed_dir(home / "Alibot", content="legacy-bytes")
        before = _snapshot(legacy)

        completed = _startup(home)

        assert completed.returncode == NON_LOOPBACK, completed.stderr
        assert _line_with_prefix(completed, DATA_DIR_LINE).endswith(str(legacy))
        assert _line_with_prefix(completed, SOURCE_LINE).endswith(SOURCE_LEGACY)
        assert "兼容" in completed.stdout
        assert _snapshot(legacy) == before
        # 不自动迁移：新默认不会因为「读到旧位置」而被创建。
        assert not (home / ".alibot" / "data").exists()


# ─── 判据 1 / 5：显式覆盖优先，且只改数据目录、不移动全局层根 ────────────────
@pytest.mark.parametrize("override_via", ["flag", "env"])
def test_explicit_override_wins_and_leaves_defaults_and_global_layer_untouched(
    tmp_path: Path, override_via: str
) -> None:
    with _home(tmp_path) as home:
        legacy = _seed_dir(home / "Alibot", content="legacy-bytes")
        new_default = _seed_dir(home / ".alibot" / "data", content="new-bytes")
        global_agent = _seed_dir(
            home / ".alibot" / "agents" / "owner" / "rules", file_name="AGENTS.md", content="global-rules"
        )
        global_skills = _seed_dir(
            home / ".alibot" / "skills" / "shared-skill", file_name="SKILL.md", content="global-skill"
        )
        explicit = tmp_path / "explicit-data"
        before = {path: _snapshot(path) for path in (legacy, new_default, global_agent, global_skills)}

        if override_via == "flag":
            completed = _startup(home, "--data-dir", str(explicit))
        else:
            completed = _startup(home, extra_env={"ALIBOT_DATA_DIR": str(explicit)})

        assert completed.returncode == NON_LOOPBACK, completed.stderr
        assert _line_with_prefix(completed, DATA_DIR_LINE).endswith(str(explicit))
        assert _line_with_prefix(completed, SOURCE_LINE).endswith(SOURCE_EXPLICIT)
        for path, snapshot in before.items():
            assert _snapshot(path) == snapshot


# ─── 判据 1：结果确定（同一磁盘状态、重复启动给同一答案） ─────────────────────
def test_resolution_is_deterministic_across_runs(tmp_path: Path) -> None:
    with _home(tmp_path) as home:
        _seed_dir(home / "Alibot")
        _seed_dir(home / ".alibot" / "data")

        first = _startup(home)
        second = _startup(home)

        assert _line_with_prefix(first, DATA_DIR_LINE) == _line_with_prefix(second, DATA_DIR_LINE)
        assert _line_with_prefix(first, SOURCE_LINE) == _line_with_prefix(second, SOURCE_LINE)


# ─── 判据 9：帮助文本与你实际会用的默认路径同一口径，且帮助不创建目录 ─────────
def test_help_reports_new_hidden_default_without_creating_it(tmp_path: Path) -> None:
    with _home(tmp_path) as home:
        completed = _run(home, "--help")

        assert completed.returncode == 0, completed.stderr
        assert str(home / ".alibot" / "data") in completed.stdout
        assert not (home / ".alibot").exists()
