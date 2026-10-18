"""Criterion 3 probe: atomic jump accounting under concurrent attempts.

Run from the repository root:

    .venv/bin/python app/specs/issue-132-research/probe_budget_atomicity.py

Each attempt opens its own SQLite connection and executes::

    BEGIN IMMEDIATE;
    UPDATE target_budget SET used = used + 1
     WHERE target_id = ? AND used < ?;
    COMMIT;

The control shows the same workload with a check-then-increment window, to
make explicit why the single conditional UPDATE is required.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import agentscope

LIMIT = 7
ATTEMPTS = 100
ROUNDS = 3
TARGET = "t1"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _fresh_db(directory: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix="budget-", suffix=".db", dir=directory)
    Path(name).unlink()
    db_path = Path(name)
    conn = _connect(db_path)
    conn.execute(
        "CREATE TABLE target_budget (target_id TEXT PRIMARY KEY, used INTEGER NOT NULL)",
    )
    conn.execute("INSERT INTO target_budget VALUES (?, 0)", (TARGET,))
    conn.commit()
    os.close(fd)
    conn.close()
    return db_path


def atomic_attempt(db_path: Path, start: threading.Barrier) -> bool:
    conn = _connect(db_path)
    try:
        start.wait()
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE target_budget SET used = used + 1 WHERE target_id = ? AND used < ?",
            (TARGET, LIMIT),
        )
        granted = cursor.rowcount == 1
        conn.execute("COMMIT" if granted else "ROLLBACK")
        return granted
    finally:
        conn.close()


def naive_attempt(db_path: Path, start: threading.Barrier) -> bool:
    """Read-check-increment with a deliberate window between the steps."""

    conn = _connect(db_path)
    try:
        used = conn.execute(
            "SELECT used FROM target_budget WHERE target_id = ?",
            (TARGET,),
        ).fetchone()[0]
        start.wait()
        if used < LIMIT:
            conn.execute(
                "UPDATE target_budget SET used = used + 1 WHERE target_id = ?",
                (TARGET,),
            )
            return True
        return False
    finally:
        conn.close()


def run_round(
    directory: Path,
    attempt: Callable[[Path, threading.Barrier], bool],
) -> dict[str, Any]:
    db_path = _fresh_db(directory)
    start = threading.Barrier(ATTEMPTS)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        granted = attempt(db_path, start)
        with lock:
            results.append(granted)

    threads = [threading.Thread(target=worker) for _ in range(ATTEMPTS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    conn = _connect(db_path)
    used = conn.execute(
        "SELECT used FROM target_budget WHERE target_id = ?",
        (TARGET,),
    ).fetchone()[0]
    conn.close()
    return {
        "success": sum(results),
        "failure": len(results) - sum(results),
        "final_used": used,
    }


def main() -> None:
    print(f"agentscope_version={agentscope.__version__}")
    print(f"sqlite_version={sqlite3.sqlite_version}")
    print(f"attempts={ATTEMPTS} limit={LIMIT} rounds={ROUNDS}")
    with tempfile.TemporaryDirectory(prefix="issue132-budget-") as tmp:
        directory = Path(tmp)
        for round_index in range(1, ROUNDS + 1):
            reading = run_round(directory, atomic_attempt)
            ok = (
                reading["success"] == LIMIT
                and reading["final_used"] == LIMIT
                and reading["failure"] == ATTEMPTS - LIMIT
            )
            print(
                f"round={round_index} atomic_success={reading['success']} "
                f"atomic_failure={reading['failure']} "
                f"final_used={reading['final_used']} "
                f"upper_bound_respected={ok}"
            )
        control = run_round(directory, naive_attempt)
        print(
            f"control_naive_check_then_increment_success={control['success']} "
            f"control_naive_check_then_increment_failure={control['failure']} "
            f"final_used={control['final_used']} "
            "upper_bound_respected="
            f"{control['final_used'] <= LIMIT}"
        )


if __name__ == "__main__":
    main()
