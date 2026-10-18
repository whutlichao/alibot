"""#157 迁移与回滚演练：15 → 16 → 17 的可复跑脚本。

用法（仓库根，用本仓 ``.venv``）::

    .venv/bin/python app/specs/issue-157-migration/drill.py [--work-dir /tmp/alibot-157-drill]

演练的数据目录**不是**用测试夹具拼出来的，而是由 ``2aebc3f``（#131 规格合入前的 main，
``SCHEMA_VERSION = 15``）里的**旧代码**真正初始化出来的库，再灌入业务数据。因此「v15 形态」是
历史代码的产物，而不是本票对它的复述。

演练的七段：

1. 从 ``2aebc3f`` 取旧 ``app/alibot``（``git archive``，不建 worktree、不碰主副本）并初始化 v15 库；
2. 灌业务数据（成员 / 频道 / target 面 / 消息 / 任务 / 预算 / 提醒 / 附件 / 轮次结果 / 远端确认）；
3. ``--migrate --dry-run``：原库 sha256 前后一致、步骤清单与行数、无备份 / 无台账；
4. 真迁移：``schema_version = 17``、旧行默认值、新表可查（含写入探针）、备份与审计；
5. 恢复备份：用 ``2aebc3f`` 的**旧代码**打开并读到原业务数据；
6. 失败注入：``fail_injection.py`` 在 16 → 17 的改动之后抛错 ⇒ 回滚到 v15（库零字节改动）；
7. 汇总证据路径与读数。

任何一条判据不成立 ⇒ 打印 ``FAIL`` 并以 1 退出；全部成立 ⇒ ``ALL PASS`` 与 0。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = REPO_ROOT / "app"
DRILL_SCRIPT = Path(__file__).resolve()

# v15 的证据来源：#131 规格合入前的 main（SCHEMA_VERSION = 15）。
V15_COMMIT = "2aebc3f"

DB_FILE = "alibot.db"
LEDGER_FILE = "schema-migrations.jsonl"
DEFAULT_WORK_DIR = Path("/tmp/alibot-157-drill")
WORK_DIR_MARKER = ".alibot-157-drill"

# v16 / v17 新增的表（规格 §4.2 / §4.3）。演练逐张 `SELECT`，再各写一行探针。
V16_TABLES = ("team_run", "team_assignment", "plan_run", "plan_task", "sop_definition", "sop_run")
V17_TABLES = (
    "permission_rule",
    "approval_request",
    "mcp_server",
    "skill",
    "remote_consent_scope",
)

# 由旧代码打开库并读出业务面的 JSON（``Database.open_without_initializing`` 走同一道版本闸门：
# 库不是 v15 时它会直接抛 ``SchemaVersionMismatch``，于是「旧代码能打开」不是一句空话）。
OLD_CODE_READ = """
import json
import sys

from alibot.db import Database, SCHEMA_VERSION

db = Database.open_without_initializing(sys.argv[1])
try:
    payload = {
        "old_code_schema_version": SCHEMA_VERSION,
        "channels": [channel["id"] for channel in db.list_channels()],
        "messages": [message["body"] for message in db.list_messages("ch_dev")],
        "members": [member["id"] for member in db.list_members()],
        "tasks": [task["id"] for task in db.list_tasks("ch_dev")],
        "consents": [consent["provider"] for consent in db.list_remote_consents()],
    }
finally:
    db.close()
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""

OLD_CODE_INITIALIZE = """
import sys

from alibot.db import Database, SCHEMA_VERSION

Database(sys.argv[1]).close()
print(SCHEMA_VERSION)
"""

# 业务数据的期望读数（灌进去的那一份；迁移后与恢复后都必须逐项相等）。
SEEDED = {
    "channels": ["ch_dev"],
    "messages": ["第一条", "第二条"],
    "members": ["owner", "mem_x"],
    "tasks": ["task_1"],
    "consents": ["anthropic"],
}

# 新表各写一行探针（证明「可查」不止是 ``SELECT COUNT(*)`` 能跑）。
NEW_TABLE_PROBES = """
INSERT INTO team_run(id, target_id, leader_member_id, base_seq, status, started_at)
VALUES ('tr_probe', 'ch_dev', 'mem_x', 3, 'running', '2026-10-18T00:00:00Z');
INSERT INTO team_assignment(
  id, team_run_id, target_id, task_id, leader_member_id, member_id, tool_call_id,
  prompt, state, jump_consumed, reply_excerpt, error, started_at
) VALUES ('ta_probe', 'tr_probe', 'task_t1', 'task_1', 'mem_x', 'mem_x', 'call_1',
          '做点什么', 'started', 1, '', '', '2026-10-18T00:00:01Z');
INSERT INTO plan_run(id, target_id, member_id, base_seq, status, started_at)
VALUES ('pr_probe', 'ch_dev', 'mem_x', 3, 'running', '2026-10-18T00:00:02Z');
INSERT INTO plan_task(
  id, plan_run_id, seq, subject, description, state, owner, blocks, blocked_by, metadata,
  created_at, updated_at
) VALUES ('pt_probe', 'pr_probe', 1, '设计', '', 'pending', NULL, '[]', '[]', '{}',
          '2026-10-18T00:00:03Z', '2026-10-18T00:00:03Z');
INSERT INTO sop_definition(id, name, description, steps, enabled, created_at, updated_at)
VALUES ('sd_probe', '巡检', '', '[{"executor": "mem_x", "subject": "检查"}]', 1,
        '2026-10-18T00:00:04Z', '2026-10-18T00:00:04Z');
INSERT INTO sop_run(id, sop_definition_id, target_id, leader_member_id, state, phase,
                    started_at, updated_at)
VALUES ('sr_probe', 'sd_probe', 'ch_dev', 'mem_x', '{"phase": "pending"}', 'pending',
        '2026-10-18T00:00:05Z', '2026-10-18T00:00:05Z');
INSERT INTO permission_rule(id, scope_kind, scope_id, tool_name, pattern, behavior, created_at)
VALUES ('perm_probe', 'channel', 'ch_dev', 'Bash', 'rm -rf', 'ask', '2026-10-18T00:00:06Z');
INSERT INTO approval_request(reply_id, target_id, member_id, team_run_id, tool_calls, state,
                             decisions, requested_at)
VALUES ('ap_probe', 'ch_dev', 'mem_x', 'tr_probe', '[]', 'pending', '[]', '2026-10-18T00:00:07Z');
INSERT INTO mcp_server(id, name, transport, command, args, url, env_names, enabled, scope_kind,
                       scope_id, created_at, updated_at)
VALUES ('mcp_probe', 'fs', 'stdio', 'npx', '["-y", "server"]', '', '["TOKEN"]', 1, 'channel',
        'ch_dev', '2026-10-18T00:00:08Z', '2026-10-18T00:00:08Z');
INSERT INTO skill(id, name, description, source_path, content_hash, enabled, scope_kind, scope_id,
                  created_at, updated_at)
VALUES ('sk_probe', 'review', '', '/tmp/skills/review', 'sha256:probe', 1, 'channel', 'ch_dev',
        '2026-10-18T00:00:09Z', '2026-10-18T00:00:09Z');
"""


class DrillFailure(AssertionError):
    """一条判据不成立：停止演练并打印原因。"""


def phase(title: str) -> None:
    print(f"\n=== {title} ===")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise DrillFailure(message)
    print(f"  [ok] {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_snapshot(root: Path) -> dict[str, str]:
    """目录快照：相对路径 → 文件 sha256 / ``<dir>``（用于证明「零字节改动」含无新文件）。"""
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        snapshot[relative] = "<dir>" if path.is_dir() else sha256_file(path)
    return snapshot


def run_python(args: list[str], *, pythonpath: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(pythonpath)}
    env.pop("PYTHONHOME", None)
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


def run_old_code(old_app: Path, code: str, data_dir: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return run_python(["-c", code, str(data_dir)], pythonpath=old_app, cwd=cwd)


def run_new_cli(data_dir: Path, *flags: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return run_python(["-m", "alibot", *flags, "--data-dir", str(data_dir)], pythonpath=APP_DIR, cwd=cwd)


def extract_old_source(work_dir: Path) -> Path:
    """把 ``2aebc3f`` 的 ``app/alibot`` 解到工作目录（``git archive``：不建 worktree、不写主副本）。"""
    archived = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", V15_COMMIT, "app/alibot"],
        check=True,
        capture_output=True,
    )
    destination = work_dir / "v15-source"
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as archive:
        archive.extractall(destination)
    return destination / "app"


def seed_v15_business_data(db_path: Path, data_dir: Path) -> None:
    """灌入 v15 形态的业务数据（形状按 v15 的 ``SCHEMA``；不加任何 v16 / v17 的列）。"""
    attachments = data_dir / "attachments"
    attachments.mkdir(exist_ok=True)
    (attachments / "probe.bin").write_bytes(b"attachment-bytes")

    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            INSERT OR IGNORE INTO member(id, kind, display_name, avatar_slot, created_at) VALUES
              ('owner', 'owner', 'Owner', 4, '2026-10-14T09:00:00Z'),
              ('mem_x', 'agent', '小明', 1, '2026-10-14T09:00:01Z');

            INSERT INTO agent_config(member_id, role, model_id, system_prompt, enabled,
                                     created_at, updated_at, auto_claim)
            VALUES ('mem_x', 'planner', 'deepseek-v4', '你是一个助手', 1,
                    '2026-10-14T09:00:01Z', '2026-10-14T09:00:01Z', 0);

            INSERT INTO channel(id, name, description, created_at)
            VALUES ('ch_dev', 'dev', '开发频道', '2026-10-14T09:00:02Z');
            INSERT INTO target(id, kind, created_at)
            VALUES ('ch_dev', 'channel', '2026-10-14T09:00:02Z');
            INSERT INTO target_member(target_id, member_id)
            VALUES ('ch_dev', 'owner'), ('ch_dev', 'mem_x');
            INSERT INTO target_read(member_id, target_id, last_read_seq)
            VALUES ('owner', 'ch_dev', 1), ('mem_x', 'ch_dev', 2);
            INSERT INTO target_budget(target_id, epoch_seq, used, applied_limit)
            VALUES ('ch_dev', 1, 3, 5);
            INSERT INTO channel_budget_limit(channel_id, budget_limit, updated_at)
            VALUES ('ch_dev', 5, '2026-10-14T09:00:02Z');

            INSERT INTO message(id, target_id, seq, author_member_id, kind, body, created_at) VALUES
              ('msg_1', 'ch_dev', 1, 'owner', 'human', '第一条', '2026-10-14T09:00:03Z'),
              ('msg_2', 'ch_dev', 2, 'mem_x', 'agent', '第二条', '2026-10-14T09:00:04Z');

            INSERT INTO target(id, kind, created_at)
            VALUES ('task_t1', 'task', '2026-10-14T09:00:05Z');
            INSERT INTO task(id, target_id, channel_id, title, status, assignee_member_id,
                             created_at, updated_at, claimed_at)
            VALUES ('task_1', 'task_t1', 'ch_dev', '修复登录', 'todo', 'mem_x',
                    '2026-10-14T09:00:05Z', '2026-10-14T09:00:05Z', NULL);

            INSERT INTO round_outcome(id, member_id, target_id, base_seq, status, reason,
                                      started_at, finished_at)
            VALUES ('ro_1', 'mem_x', 'ch_dev', 1, 'silent', 'no-op',
                    '2026-10-14T09:00:06Z', '2026-10-14T09:00:07Z');

            UPDATE workspace_settings
               SET updated_at = '2026-10-14T09:00:08Z'
             WHERE id = 1;

            INSERT INTO reminder(id, text, time, channel_id, created_at, fired_at)
            VALUES ('rem_1', '站会', '2026-10-20 09:00', 'ch_dev', '2026-10-14T09:00:09Z', NULL);

            INSERT INTO remote_consent(provider, acknowledged_at)
            VALUES ('anthropic', '2026-10-14T09:00:10Z');

            INSERT INTO attachment(id, original_name, size_bytes, mime_type, relative_path,
                                   created_at, message_id)
            VALUES ('att_1', 'probe.bin', 16, 'application/octet-stream',
                    'attachments/probe.bin', '2026-10-14T09:00:11Z', 'msg_1');
            """
        )
    finally:
        conn.close()


def read_counts(db_path: Path) -> dict[str, int]:
    tables = (
        "member",
        "channel",
        "target",
        "message",
        "task",
        "reminder",
        "attachment",
        "round_outcome",
        "target_budget",
        "workspace_settings",
        "remote_consent",
    )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}
    finally:
        conn.close()


def read_version(db_path: Path) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return "" if row is None else str(row[0])
    finally:
        conn.close()


def table_exists(db_path: Path, table: str) -> bool:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def column_names(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]
    finally:
        conn.close()


def backup_dirs(data_dir: Path) -> list[Path]:
    prefix = f"{data_dir.name}-backup-"
    return sorted(path for path in data_dir.parent.iterdir() if path.name.startswith(prefix))


def old_code_readings(old_app: Path, data_dir: Path, cwd: Path) -> dict[str, Any]:
    completed = run_old_code(old_app, OLD_CODE_READ, data_dir, cwd)
    check(
        completed.returncode == 0,
        f"旧代码（{V15_COMMIT}）能打开 {data_dir.name}：exit={completed.returncode} "
        f"stderr={completed.stderr.strip()!r}",
    )
    return json.loads(completed.stdout)


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    if row is None:
        raise DrillFailure(f"新表探针查询没有结果：{sql}")
    return row[0]


def _probe_new_tables(db_path: Path) -> dict[str, Any]:
    """新表逐张写入一行、读回关键列；再用一条违反约束的插入证明新触发器生效。"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(NEW_TABLE_PROBES)
        payload = {
            "team_run": _scalar(conn, "SELECT status FROM team_run WHERE id = 'tr_probe'"),
            "team_assignment": _scalar(conn, "SELECT state FROM team_assignment WHERE id = 'ta_probe'"),
            "plan_run": _scalar(conn, "SELECT status FROM plan_run WHERE id = 'pr_probe'"),
            "plan_task": _scalar(conn, "SELECT subject FROM plan_task WHERE id = 'pt_probe'"),
            "sop_definition": _scalar(conn, "SELECT name FROM sop_definition WHERE id = 'sd_probe'"),
            "sop_run": _scalar(conn, "SELECT phase FROM sop_run WHERE id = 'sr_probe'"),
            "permission_rule": _scalar(conn, "SELECT behavior FROM permission_rule WHERE id = 'perm_probe'"),
            "approval_request": _scalar(
                conn, "SELECT state FROM approval_request WHERE reply_id = 'ap_probe'"
            ),
            "mcp_server": _scalar(conn, "SELECT env_names FROM mcp_server WHERE id = 'mcp_probe'"),
            "skill": _scalar(conn, "SELECT content_hash FROM skill WHERE id = 'sk_probe'"),
            "remote_consent_scope": _scalar(
                conn,
                "SELECT acknowledged_at FROM remote_consent_scope "
                "WHERE scope_kind = 'model' AND scope_id = 'anthropic'",
            ),
        }
        try:
            conn.execute(
                "INSERT INTO channel(id, name, description, created_at, mode, team_leader_member_id) "
                "VALUES ('ch_probe', 'probe', '', '2026-10-18T00:00:10Z', 'team', NULL)"
            )
        except sqlite3.IntegrityError as exc:
            payload["team_trigger"] = str(exc)
        else:
            raise DrillFailure("team 模式缺队长的插入没有被触发器拒绝")
        return payload
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="#157 迁移与回滚演练（15 → 16 → 17）")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR), help="演练工作目录（每次重建）")
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir)
    if work_dir.exists():
        # 只清理本演练自己建过的目录：别的非空目录一律拒绝，避免 --work-dir 指错地方时被清空。
        if any(work_dir.iterdir()) and not (work_dir / WORK_DIR_MARKER).exists():
            raise DrillFailure(f"工作目录非空且不是本演练创建的：{work_dir}（换一个 --work-dir）")
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    (work_dir / WORK_DIR_MARKER).write_text("#157 迁移演练工作目录（可安全清空）\n", encoding="utf-8")

    data_dir = work_dir / "data"
    pristine_dir = work_dir / "data-pristine"
    injected_dir = work_dir / "data-injected"
    restored_dir = work_dir / "data-restored"

    phase(f"1/7 取 {V15_COMMIT} 的旧代码并初始化 v15 库")
    old_app = extract_old_source(work_dir)
    initialized = run_old_code(old_app, OLD_CODE_INITIALIZE, data_dir, cwd=work_dir)
    check(initialized.returncode == 0, f"旧代码初始化成功（stderr={initialized.stderr.strip()!r}）")
    print(f"  旧代码 SCHEMA_VERSION = {initialized.stdout.strip()}")
    check(initialized.stdout.strip() == "15", "旧代码自报版本 = 15")
    db_path = data_dir / DB_FILE
    check(read_version(db_path) == "15", "库落盘后 schema_version = 15")

    phase("2/7 灌业务数据")
    seed_v15_business_data(db_path, data_dir)
    if pristine_dir.exists():
        shutil.rmtree(pristine_dir)
    shutil.copytree(data_dir, pristine_dir)
    counts_before = read_counts(db_path)
    print(f"  行数：{json.dumps(counts_before, ensure_ascii=False, sort_keys=True)}")
    check(counts_before["message"] == 2 and counts_before["target_budget"] == 1, "业务数据已就位")
    digest_before = sha256_file(db_path)
    snapshot_before = tree_snapshot(data_dir)
    print(f"  alibot.db sha256 = {digest_before}")

    phase("3/7 --migrate --dry-run（原库必须零字节改动）")
    dry_run = run_new_cli(data_dir, "--migrate", "--dry-run", cwd=APP_DIR)
    print(dry_run.stdout.rstrip())
    if dry_run.stderr.strip():
        print(f"  stderr: {dry_run.stderr.strip()}")
    check(dry_run.returncode == 0, f"dry-run 退出码 = 0（实际 {dry_run.returncode}）")
    check("15 → 16" in dry_run.stdout and "16 → 17" in dry_run.stdout, "报告列出 15 → 16 与 16 → 17")
    check(
        "15 → 16  agentscope-2 team/plan/sop expand  改动 0 行" in dry_run.stdout,
        "15 → 16 将改动 0 行",
    )
    check(
        "16 → 17  agentscope-2 workspace/permission/mcp/skill expand  改动 1 行" in dry_run.stdout,
        "16 → 17 将改动 1 行（旧 remote_consent 复制）",
    )
    digest_after_dry_run = sha256_file(db_path)
    check(digest_after_dry_run == digest_before, f"dry-run 前后 sha256 一致：{digest_after_dry_run}")
    check(tree_snapshot(data_dir) == snapshot_before, "数据目录逐文件一致（无副本 / 备份 / 台账残留）")
    check(backup_dirs(data_dir) == [], "dry-run 未建备份目录")
    check(not (data_dir / LEDGER_FILE).exists(), "dry-run 未写审计台账")

    phase("4/7 真迁移 15 → 17")
    migrated = run_new_cli(data_dir, "--migrate", cwd=APP_DIR)
    print(migrated.stdout.rstrip())
    if migrated.stderr.strip():
        print(f"  stderr: {migrated.stderr.strip()}")
    check(migrated.returncode == 0, f"--migrate 退出码 = 0（实际 {migrated.returncode}）")
    check(read_version(db_path) == "17", "schema_version = 17")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        channel = dict(
            conn.execute(
                "SELECT mode, team_leader_member_id, workspace_root, workspace_backend, permission_mode "
                "FROM channel WHERE id = 'ch_dev'"
            ).fetchone()
        )
        agent = dict(
            conn.execute(
                "SELECT role, auto_claim, workspace_backend, permission_mode, allow_local_shell, "
                "docker_image FROM agent_config WHERE member_id = 'mem_x'"
            ).fetchone()
        )
        consent_scope = [
            tuple(row)
            for row in conn.execute("SELECT scope_kind, scope_id, acknowledged_at FROM remote_consent_scope")
        ]
        budget = dict(conn.execute("SELECT * FROM target_budget WHERE target_id = 'ch_dev'").fetchone())
    finally:
        conn.close()
    print(f"  旧 channel 行：{json.dumps(channel, ensure_ascii=False)}")
    print(f"  旧 agent_config 行：{json.dumps(agent, ensure_ascii=False)}")
    print(f"  remote_consent_scope：{consent_scope}")
    check(channel["mode"] == "autonomous", "旧 channel.mode = 'autonomous'")
    check(channel["team_leader_member_id"] is None, "旧 channel.team_leader_member_id = NULL")
    check(channel["workspace_root"] is None, "旧 channel.workspace_root = NULL")
    check(channel["workspace_backend"] == "local", "旧 channel.workspace_backend = 'local'（列默认值）")
    check(channel["permission_mode"] == "ask-writes", "旧 channel.permission_mode = 'ask-writes'")
    check(agent["role"] == "planner" and agent["auto_claim"] == 0, "旧 agent_config 业务列保持原值")
    check(agent["workspace_backend"] is None, "旧 agent_config.workspace_backend = NULL")
    check(agent["permission_mode"] is None, "旧 agent_config.permission_mode = NULL")
    check(agent["allow_local_shell"] == 0, "旧 agent_config.allow_local_shell = 0")
    check(agent["docker_image"] is None, "旧 agent_config.docker_image = NULL")
    check(
        consent_scope == [("model", "anthropic", "2026-10-14T09:00:10Z")],
        "旧 remote_consent 复制成 ('model', 'anthropic', 原确认时间)",
    )
    check(budget["used"] == 3 and budget["applied_limit"] == 5, "预算行原样保留（used=3）")

    counts_after = read_counts(db_path)
    print(f"  迁移后行数：{json.dumps(counts_after, ensure_ascii=False, sort_keys=True)}")
    check(counts_after == counts_before, "既有业务表的行数逐项不变")

    missing = [table for table in (*V16_TABLES, *V17_TABLES) if not table_exists(db_path, table)]
    check(missing == [], f"v16 / v17 新表全部可查（缺：{missing}）")
    probes = _probe_new_tables(db_path)
    print(f"  新表写入探针：{json.dumps(probes, ensure_ascii=False, sort_keys=True)}")
    check(
        "team channel requires a leader" in str(probes["team_trigger"]),
        "v16 的「team 必须有队长」触发器在新库里生效",
    )

    backups = backup_dirs(data_dir)
    check(len(backups) == 1, f"备份目录存在：{backups}")
    backup_path = backups[0]
    backup_db = backup_path / DB_FILE
    check(backup_db.exists(), f"备份里有 {DB_FILE}")
    check(sha256_file(backup_db) == digest_before, "备份的库文件与迁移前 sha256 一致")
    check(
        sha256_file(backup_path / "attachments" / "probe.bin")
        == sha256_file(data_dir / "attachments" / "probe.bin"),
        "备份含 attachments 字节（整目录备份，不只是库文件）",
    )
    ledger_lines = (data_dir / LEDGER_FILE).read_text(encoding="utf-8").splitlines()
    ledger = json.loads(ledger_lines[0])
    print(f"  备份：{backup_path}")
    print(f"  审计：{json.dumps(ledger, ensure_ascii=False, sort_keys=True)}")
    check(
        ledger["result"] == "success" and ledger["from"] == 15 and ledger["to"] == 17,
        "审计台账记录 success 15 → 17",
    )
    check(str(backup_path) == ledger["backup_path"], "台账里的备份路径与实际落点一致")

    phase("5/7 恢复备份 → 旧代码打开并读原业务数据")
    if restored_dir.exists():
        shutil.rmtree(restored_dir)
    shutil.copytree(backup_path, restored_dir)
    check(read_version(restored_dir / DB_FILE) == "15", "恢复出来的库 schema_version = 15")
    check(
        sha256_file(restored_dir / DB_FILE) == digest_before,
        "恢复出来的库与迁移前 sha256 一致",
    )
    restored_readings = old_code_readings(old_app, restored_dir, work_dir)
    print(f"  旧代码读数：{json.dumps(restored_readings, ensure_ascii=False, sort_keys=True)}")
    for key, expected in SEEDED.items():
        check(restored_readings[key] == expected, f"旧代码读到 {key} = {expected}")
    check(restored_readings["old_code_schema_version"] == 15, "旧代码自报 SCHEMA_VERSION = 15")

    phase("6/7 失败注入 → 回滚到原版本")
    if injected_dir.exists():
        shutil.rmtree(injected_dir)
    shutil.copytree(pristine_dir, injected_dir)
    injected_db = injected_dir / DB_FILE
    injected_digest = sha256_file(injected_db)
    injected_columns = column_names(injected_db, "channel")
    print(f"  注入前 sha256 = {injected_digest}")
    injection = run_python(
        [str(DRILL_SCRIPT.with_name("fail_injection.py")), str(injected_dir)],
        pythonpath=APP_DIR,
        cwd=APP_DIR,
    )
    print(f"  注入驱动 stdout: {injection.stdout.strip()}")
    if injection.stderr.strip():
        print(f"  注入驱动 stderr: {injection.stderr.strip()}")
    check(injection.returncode == 5, f"注入后的退出码 = 5（实际 {injection.returncode}）")
    check("迁移失败并已回滚" in injection.stdout, "失败正文写明「已回滚」")
    check(sha256_file(injected_db) == injected_digest, "回滚后库文件 sha256 与注入前一致")
    check(read_version(injected_db) == "15", "回滚后 schema_version 仍是 15")
    check(column_names(injected_db, "channel") == injected_columns, "channel 的新列随回滚消失")
    leftover = [table for table in (*V16_TABLES, *V17_TABLES) if table_exists(injected_db, table)]
    check(leftover == [], f"v16 / v17 新表随回滚消失（残留：{leftover}）")
    failed_ledger = [
        json.loads(line) for line in (injected_dir / LEDGER_FILE).read_text(encoding="utf-8").splitlines()
    ]
    print(f"  失败审计：{json.dumps(failed_ledger[-1], ensure_ascii=False, sort_keys=True)}")
    check(failed_ledger[-1]["result"] == "failed", "台账留下一条 failed 记录")
    check(len(backup_dirs(injected_dir)) == 1, "失败前已完成整目录备份")
    injected_readings = old_code_readings(old_app, injected_dir, work_dir)
    for key, expected in SEEDED.items():
        check(injected_readings[key] == expected, f"回滚后的库仍能被旧代码读到 {key}")

    phase("7/7 汇总")
    print(f"  工作目录：{work_dir}")
    print(f"  迁移后库：{db_path}（schema_version = 17）")
    print(f"  备份：{backup_path}")
    print(f"  恢复副本：{restored_dir}（旧代码可读）")
    print(f"  失败注入副本：{injected_dir}（schema_version = 15，已回滚）")
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DrillFailure as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
