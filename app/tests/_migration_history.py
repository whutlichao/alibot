"""历史逐版 DDL 留档（机器抽取，勿手改）。

取证方式：对每个 ``SCHEMA_VERSION`` 取它落地时那个 commit 的 ``app/alibot/db.py``，抽出其中的
``SCHEMA`` 字符串，再按对象名拆成 ``CREATE TABLE / INDEX / TRIGGER`` 块。commit ↔ 版本号的对照
见 :data:`SOURCE_COMMITS`，因此这份留档可以照着 git 历史逐条复核（不需要任何仓库外的脚本）。

测试用 ``OBJECTS`` + ``VERSION_OBJECTS`` 从 v3 基线逐版搭出「该版形态」的库；既有表在该版新增、
但 SQLite 无法用 ``CREATE`` 表达的列（``task_ref`` / ``dm_peer_member_id`` / ``theme`` /
``claimed_at`` / ``auto_claim``）由测试自己按 ``ADD COLUMN`` 补。
"""

from __future__ import annotations

# 生成的数据表一律保持一行一项：本文件是机器抽取的留档，不做人工排版。
# fmt: off

SOURCE_COMMITS: dict[int, str] = {
    3: '858a92d',
    4: '49b8973',
    5: '4e57b97',
    6: '0c2ef85',
    7: 'f0426c1',
    8: '65c0fe2',
    9: 'd386794',
    10: 'cd23ac5',
    11: 'f0d8426',
    12: 'a700d05',
    13: 'pending',  # 尚未落地：v13 由 issue #112 引入，合并后回填该合并提交
}

OBJECTS: dict[str, str] = {
    'schema_meta': 'CREATE TABLE IF NOT EXISTS schema_meta (\n  key   TEXT PRIMARY KEY,\n  value TEXT NOT NULL\n);',
    'member': "CREATE TABLE IF NOT EXISTS member (\n  id           TEXT PRIMARY KEY,\n  kind         TEXT NOT NULL CHECK (kind IN ('owner', 'agent')),\n  display_name TEXT NOT NULL,\n  avatar_slot  INTEGER NOT NULL,\n  created_at   TEXT NOT NULL,\n  deleted_at   TEXT\n);",
    'channel': "CREATE TABLE IF NOT EXISTS channel (\n  id          TEXT PRIMARY KEY,\n  name        TEXT NOT NULL,\n  description TEXT NOT NULL DEFAULT '',\n  created_at  TEXT NOT NULL\n);",
    'channel_member': 'CREATE TABLE IF NOT EXISTS channel_member (\n  channel_id TEXT NOT NULL REFERENCES channel(id),\n  member_id  TEXT NOT NULL REFERENCES member(id),\n  PRIMARY KEY (channel_id, member_id)\n);',
    'message': "CREATE TABLE IF NOT EXISTS message (\n  id               TEXT PRIMARY KEY,\n  target_id        TEXT NOT NULL,\n  seq              INTEGER NOT NULL,\n  author_member_id TEXT NOT NULL REFERENCES member(id),\n  kind             TEXT NOT NULL CHECK (kind IN ('human', 'agent', 'system')),\n  body             TEXT NOT NULL,\n  created_at       TEXT NOT NULL,\n  mention_snapshot TEXT NOT NULL DEFAULT '[]',\n  UNIQUE (target_id, seq)\n);",
    'channel_read': 'CREATE TABLE IF NOT EXISTS channel_read (\n  member_id     TEXT NOT NULL REFERENCES member(id),\n  channel_id    TEXT NOT NULL REFERENCES channel(id),\n  last_read_seq INTEGER NOT NULL DEFAULT 0,\n  PRIMARY KEY (member_id, channel_id)\n);',
    'channel_budget': 'CREATE TABLE IF NOT EXISTS channel_budget (\n  channel_id TEXT PRIMARY KEY REFERENCES channel(id),\n  epoch_seq  INTEGER NOT NULL DEFAULT 0,\n  used       INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)\n);',
    'agent_config': "CREATE TABLE IF NOT EXISTS agent_config (\n  member_id     TEXT PRIMARY KEY REFERENCES member(id),\n  role          TEXT NOT NULL DEFAULT '',\n  model_id      TEXT NOT NULL DEFAULT '',\n  system_prompt TEXT NOT NULL DEFAULT '',\n  enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),\n  created_at    TEXT NOT NULL,\n  updated_at    TEXT NOT NULL\n);",
    'remote_consent': 'CREATE TABLE IF NOT EXISTS remote_consent (\n  provider        TEXT PRIMARY KEY,\n  acknowledged_at TEXT NOT NULL\n);',
    'round_outcome': "CREATE TABLE IF NOT EXISTS round_outcome (\n  id              TEXT PRIMARY KEY,\n  member_id       TEXT NOT NULL REFERENCES member(id),\n  target_id       TEXT NOT NULL,\n  base_seq        INTEGER NOT NULL,\n  status          TEXT NOT NULL CHECK (status IN\n                    ('replied', 'ignored', 'failed', 'merged', 'budget-exhausted')),\n  reason          TEXT NOT NULL DEFAULT '',\n  started_at      TEXT NOT NULL,\n  finished_at     TEXT NOT NULL,\n  consumed_budget INTEGER NOT NULL DEFAULT 0 CHECK (consumed_budget IN (0, 1)),\n  merged          INTEGER NOT NULL DEFAULT 0 CHECK (merged IN (0, 1)),\n  reply_seq       INTEGER\n);",
    'idx_round_outcome_target': 'CREATE INDEX IF NOT EXISTS idx_round_outcome_target ON round_outcome(target_id, finished_at);',
    'idx_channel_member_member': 'CREATE INDEX IF NOT EXISTS idx_channel_member_member ON channel_member(member_id);',
    'message_no_update': "CREATE TRIGGER IF NOT EXISTS message_no_update\nBEFORE UPDATE ON message\nBEGIN\n  SELECT RAISE(ABORT, 'message is append-only');\nEND;",
    'message_no_delete': "CREATE TRIGGER IF NOT EXISTS message_no_delete\nBEFORE DELETE ON message\nBEGIN\n  SELECT RAISE(ABORT, 'message is append-only');\nEND;",
    'round_outcome_no_update': "CREATE TRIGGER IF NOT EXISTS round_outcome_no_update\nBEFORE UPDATE ON round_outcome\nBEGIN\n  SELECT RAISE(ABORT, 'round_outcome is append-only');\nEND;",
    'round_outcome_no_delete': "CREATE TRIGGER IF NOT EXISTS round_outcome_no_delete\nBEFORE DELETE ON round_outcome\nBEGIN\n  SELECT RAISE(ABORT, 'round_outcome is append-only');\nEND;",
    'target': "CREATE TABLE IF NOT EXISTS target (\n  id         TEXT PRIMARY KEY,\n  kind       TEXT NOT NULL CHECK (kind IN ('channel', 'dm', 'task')),\n  created_at TEXT NOT NULL\n);",
    'target_member': 'CREATE TABLE IF NOT EXISTS target_member (\n  target_id TEXT NOT NULL REFERENCES target(id),\n  member_id TEXT NOT NULL REFERENCES member(id),\n  PRIMARY KEY (target_id, member_id)\n);',
    'target_read': 'CREATE TABLE IF NOT EXISTS target_read (\n  member_id     TEXT NOT NULL REFERENCES member(id),\n  target_id     TEXT NOT NULL REFERENCES target(id),\n  last_read_seq INTEGER NOT NULL DEFAULT 0,\n  PRIMARY KEY (member_id, target_id)\n);',
    'target_budget': 'CREATE TABLE IF NOT EXISTS target_budget (\n  target_id TEXT PRIMARY KEY REFERENCES target(id),\n  epoch_seq INTEGER NOT NULL DEFAULT 0,\n  used      INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)\n);',
    'idx_target_member_member': 'CREATE INDEX IF NOT EXISTS idx_target_member_member ON target_member(member_id);',
    'task': "CREATE TABLE IF NOT EXISTS task (\n  id                 TEXT PRIMARY KEY,\n  target_id          TEXT NOT NULL UNIQUE REFERENCES target(id),\n  channel_id         TEXT NOT NULL REFERENCES channel(id),\n  title              TEXT NOT NULL CHECK (length(trim(title)) > 0),\n  status             TEXT NOT NULL CHECK (status IN ('todo', 'in_progress', 'in_review', 'done', 'closed')),\n  assignee_member_id TEXT REFERENCES member(id),\n  created_at         TEXT NOT NULL,\n  updated_at         TEXT NOT NULL,\n  UNIQUE (target_id, id)\n);",
    'task_target_must_be_task': "CREATE TRIGGER IF NOT EXISTS task_target_must_be_task\nBEFORE INSERT ON task\nWHEN (SELECT kind FROM target WHERE id = NEW.target_id) != 'task'\nBEGIN\n  SELECT RAISE(ABORT, 'task must own a task target');\nEND;",
    'task_channel_must_be_channel': "CREATE TRIGGER IF NOT EXISTS task_channel_must_be_channel\nBEFORE INSERT ON task\nWHEN (SELECT kind FROM target WHERE id = NEW.channel_id) != 'channel'\nBEGIN\n  SELECT RAISE(ABORT, 'task must belong to a channel target');\nEND;",
    'idx_target_dm_peer_unique': "CREATE UNIQUE INDEX IF NOT EXISTS idx_target_dm_peer_unique\nON target(dm_peer_member_id) WHERE kind = 'dm' AND dm_peer_member_id IS NOT NULL;",
    'workspace_settings': "CREATE TABLE IF NOT EXISTS workspace_settings (\n  id              INTEGER PRIMARY KEY CHECK (id = 1),\n  locale          TEXT NOT NULL DEFAULT 'zh-CN' CHECK (locale IN ('zh-CN', 'en')),\n  message_density TEXT NOT NULL DEFAULT 'comfortable'\n                  CHECK (message_density IN ('comfortable', 'compact')),\n  updated_at      TEXT NOT NULL\n);",
    'reminder': 'CREATE TABLE IF NOT EXISTS reminder (\n  id          TEXT PRIMARY KEY,\n  text        TEXT NOT NULL CHECK (length(trim(text)) > 0),\n  time        TEXT NOT NULL,\n  channel_id  TEXT REFERENCES channel(id),\n  created_at  TEXT NOT NULL\n);',
    'reminder_v13': 'CREATE TABLE IF NOT EXISTS reminder (\n  id          TEXT PRIMARY KEY,\n  text        TEXT NOT NULL CHECK (length(trim(text)) > 0),\n  time        TEXT NOT NULL,\n  channel_id  TEXT REFERENCES channel(id),\n  created_at  TEXT NOT NULL,\n  fired_at    TEXT\n);',
    'attachment': 'CREATE TABLE IF NOT EXISTS attachment (\n  id            TEXT PRIMARY KEY,\n  original_name TEXT NOT NULL,\n  size_bytes    INTEGER NOT NULL CHECK (size_bytes >= 0),\n  mime_type     TEXT NOT NULL,\n  relative_path TEXT NOT NULL,\n  created_at    TEXT NOT NULL,\n  message_id    TEXT REFERENCES message(id)\n);',
    'idx_attachment_message': 'CREATE INDEX IF NOT EXISTS idx_attachment_message ON attachment(message_id);',
    'attachment_no_rebind': "CREATE TRIGGER IF NOT EXISTS attachment_no_rebind\nBEFORE UPDATE ON attachment\nWHEN OLD.message_id IS NOT NULL\nBEGIN\n  SELECT RAISE(ABORT, 'attachment binding is immutable');\nEND;",
    'attachment_no_delete': "CREATE TRIGGER IF NOT EXISTS attachment_no_delete\nBEFORE DELETE ON attachment\nBEGIN\n  SELECT RAISE(ABORT, 'attachment is append-only');\nEND;",
    'attachment_metadata_immutable': "CREATE TRIGGER IF NOT EXISTS attachment_metadata_immutable\nBEFORE UPDATE ON attachment\nWHEN OLD.id IS NOT NEW.id\n  OR OLD.original_name IS NOT NEW.original_name\n  OR OLD.size_bytes IS NOT NEW.size_bytes\n  OR OLD.mime_type IS NOT NEW.mime_type\n  OR OLD.relative_path IS NOT NEW.relative_path\n  OR OLD.created_at IS NOT NEW.created_at\nBEGIN\n  SELECT RAISE(ABORT, 'attachment is immutable');\nEND;",
    'attachment_binding_is_immutable': "CREATE TRIGGER IF NOT EXISTS attachment_binding_is_immutable\nBEFORE UPDATE ON attachment\nWHEN OLD.message_id IS NOT NULL AND NEW.message_id IS NOT OLD.message_id\nBEGIN\n  SELECT RAISE(ABORT, 'attachment binding is immutable');\nEND;",
}

VERSION_OBJECTS: dict[int, tuple[str, ...]] = {
    3: ('schema_meta', 'member', 'channel', 'channel_member', 'message', 'channel_read', 'channel_budget', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'idx_channel_member_member', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete',),
    4: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member',),
    5: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel',),
    6: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique',),
    7: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique', 'workspace_settings',),
    8: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique', 'workspace_settings', 'reminder',),
    9: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique', 'workspace_settings', 'attachment', 'idx_attachment_message', 'attachment_no_rebind', 'attachment_no_delete',),
    10: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique', 'workspace_settings',),
    11: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique', 'workspace_settings', 'reminder', 'attachment', 'idx_attachment_message', 'attachment_no_delete', 'attachment_metadata_immutable', 'attachment_binding_is_immutable',),
    12: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique', 'workspace_settings'),
    13: ('schema_meta', 'member', 'channel', 'message', 'agent_config', 'remote_consent', 'round_outcome', 'idx_round_outcome_target', 'message_no_update', 'message_no_delete', 'round_outcome_no_update', 'round_outcome_no_delete', 'target', 'target_member', 'target_read', 'target_budget', 'idx_target_member_member', 'task', 'task_target_must_be_task', 'task_channel_must_be_channel', 'idx_target_dm_peer_unique', 'workspace_settings', 'reminder_v13',),
}

# fmt: on
