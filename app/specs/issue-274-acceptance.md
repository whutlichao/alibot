# #274 验收：显式技能字节迁移命令 `migrate-skills`

父规格 #269；分支 `issue-274-impl`（从集成分支 `capabilities-agent-scope` 的 `d88d912` 切出）。
本票只交付**命令 + 测试**，**未在本机真实数据上执行**任何搬迁：真实迁移由人类在验收后手动跑。

约束复核：`SCHEMA_VERSION` 未 bump、没有新增列 / 表 / 回填；不是 HTTP 端点，也不在启动路径上；
不搬频道级字节、不删除任何源文件；家目录解析走 `workspace.global_home_root()` /
`workspace.resolve_global_read_path` 的根检查口径。

## 判据 → 落点

| 判据（票面） | 落点 | 用例 |
| --- | --- | --- |
| 扫描所有频道根（`channel.workspace_root` 非空）下的 `.alibot/skills/<partition>/<name>/`；成员 id → 成员家目录、频道 id → 只报告 | `migrate.plan_skill_migration`（`validate_workspace_root` + `resolve_in_root` 扫描；`safe_partition_id` 分类成员 / 频道；未知或删除成员 → 跳过并给原因） | `tests/test_skill_migration.py::test_channel_level_bytes_are_reported_only[False/True]`、`::test_dry_run_reports_the_plan_and_writes_nothing` |
| `--dry-run`（默认）逐条计划：源 → 目标、新建 / 跳过 / 拒绝与原因、字节数 / 文件数；**零副作用** | `migrate.render_skill_migration_plan`；扫描只用 `migrate.connect`（连接级 PRAGMA）+ 只读目录 | `::test_dry_run_reports_the_plan_and_writes_nothing`（目录树快照 + 库 sha256 前后一致、不建家目录 / 备份 / 清单） |
| `--apply` 先做整目录备份（`<数据目录>-backup-skills-<UTC 时间戳>`）+ 迁移清单 JSON；复制（不移动）；重算 `content_hash`；更新 `source_path` / `content_hash` / `updated_at` | `migrate.apply_skill_migration`：`migrate._backup_data_dir(..., label="skills")` → `_write_skill_manifest` → `_apply_skill_entry`（staging + `os.rename`）→ `_upsert_skill_row` | `::test_apply_copies_bytes_recomputes_hash_and_updates_the_row` |
| 目标已存在同内容 → 跳过；不同 → 拒绝并列待裁定（无 `--force`） | `_existing_skill_tree` + `_plan_entry`（`skip` / `refuse`）；`apply_skill_migration` 在动手前整体拒绝 | `::test_conflicting_target_is_refused_without_overwriting` |
| 目标成员的库行不存在 → 创建行（`scope_kind='agent'`, `scope_id=<member>`）并标注来源路径 | `_upsert_skill_row`（`INSERT ... scope_kind='agent'`，`source_path` = **旧项目层源路径**即 `entry.source`；更新行同样写来源，`_skill_row_changes` 与来源比较以保持幂等） | `::test_apply_creates_an_agent_row_when_the_library_row_is_missing` |
| 任何一步失败即停、打印已完成条目、已复制文件保留、不回滚删除 | `SkillMigrationFailed` 携带 `SkillApplyResult(completed=…)`；CLI 打印后以非 0 退出 | 冲突用例覆盖「拒绝前零写入」；中途失败路径见 `apply_skill_migration` |
| 源字节不删除，输出「确认后可手工删除」清单 | `SkillApplyResult.removable_sources` + `render_skill_migration_result` | `::test_apply_copies_bytes_recomputes_hash_and_updates_the_row`（断言源仍在 + 报告含清单） |
| 频道级分区只进报告，建议「频道级已弃用，请按成员安装」 | `ChannelLevelSkills` + `render_skill_migration_plan` | `::test_channel_level_bytes_are_reported_only` |
| `--apply` 必须同时 `--yes`；单独 `--yes` → 2；默认 dry-run | `__main__.build_parser`（位置参数 `command` + `--apply` / `--yes`）与 `main` 的 argparse 守卫 | `::test_destructive_switches_require_both_flags`（5 组参数） |
| 幂等：重复 apply 报「跳过（内容相同）」、不重复插行 | `SkillMigrationPlan.pending`（全就位时零写入）+ `_skill_row_changes`（值相同不写 `updated_at`） | `::test_apply_copies_bytes_recomputes_hash_and_updates_the_row`（第二次 apply） |
| 技能树校验 / hash 不复制第二套算法 | `skills.read_skill_tree`（复用 `_skill_files` / `_content_hash` / `_front_matter`），`migrate` 只调用它 | 全文件 |

## 先红后绿

红（把 `app/alibot/{__main__,migrate,skills}.py` 还原到 `d88d912`、只保留新测试）：

```text
$ .venv/bin/python -m pytest app/tests/test_skill_migration.py
...
alibot: error: unrecognized arguments: migrate-skills --apply --yes
6 failed, 5 passed in 9.74s
```

> 那 5 个「passed」是 CLI 双开关用例：基线 parser 对任何未知参数都以 2 退出，于是它们因**错误的
> 理由**通过；6 个行为用例全部失败（没有 `migrate-skills` 子命令）。

绿（实现后）：

```text
$ .venv/bin/python -m pytest app/tests/test_skill_migration.py
........... [100%]
11 passed in 10.24s
```

## 真实跑一遍（临时 HOME + 临时数据目录，不是用户真实数据）

fixture 由 `alibot.db.Database` 建库：一个频道（`workspace_root=/tmp/wt274b/project`）、一个
Agent 成员；项目层旧字节含成员分区 `review`（有库行、hash 故意写成 `0…0`）与 `notes`（无库行），
以及频道分区 `channel-only`。命令环境固定 `HOME=/tmp/wt274b/home`。

**修订（#269 review，2026-10-25）**：`source_path` 的语义改成**来源**（旧项目层源路径），不再是新家目录
路径；技能迁移备份名带 `skills` 标签。下面的读数按现行代码重跑（临时 HOME + 临时数据目录）。

### ① dry-run（默认）与零副作用读数

```text
$ env HOME=/tmp/wt274b/home PYTHONPATH=app .venv/bin/python -m alibot migrate-skills --data-dir /tmp/wt274b/data
技能迁移计划（migrate-skills，只读扫描）
数据目录： /tmp/wt274b/data
全局层根： /private/tmp/wt274b/home/.alibot
成员级技能： 2 条（新建 2 / 跳过 0 / 拒绝 0）
  新建 /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/notes → /private/tmp/wt274b/home/.alibot/agents/mem_2307eb44660f/skills/notes（2 个文件 / 65 字节；hash 13c2e31a0efa1543a38b6defcb18372a16d3de3287c1e15e7b197568f1e631ca）
    原因：目标不存在，将复制字节；库行不存在，将按成员作用域新建
  新建 /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/review → /private/tmp/wt274b/home/.alibot/agents/mem_2307eb44660f/skills/review（2 个文件 / 67 字节；hash 188b124866aa976250a4a09958323fb98180b8e4dc78c8cdaaac103ed2a40be9）
    原因：目标不存在，将复制字节；将同步库行（source_path, content_hash, updated_at）
频道级技能（不迁移）： 1 个分区 / 1 个技能 —— 频道级已弃用，请按成员安装
  频道 ch_b206fc2afa64：/private/tmp/wt274b/project/.alibot/skills/ch_b206fc2afa64（1 个技能：channel-only）
    频道级已弃用，请按成员安装
预演结束：未写入任何文件、未改动数据库、未创建备份或迁移清单。
EXIT=0

DB sha before: 119b936bafaaf9f9ad9c9fb74f3f1e550e20e2999d369b79f64a685725e2643b
DB sha after : 119b936bafaaf9f9ad9c9fb74f3f1e550e20e2999d369b79f64a685725e2643b
home exists: no
```

### ② apply（同一 fixture；`--apply --yes`）

```text
$ env HOME=/tmp/wt274b/home PYTHONPATH=app .venv/bin/python -m alibot migrate-skills --apply --yes --data-dir /tmp/wt274b/data
技能迁移计划（migrate-skills，只读扫描）
数据目录： /tmp/wt274b/data
全局层根： /private/tmp/wt274b/home/.alibot
成员级技能： 2 条（新建 2 / 跳过 0 / 拒绝 0）
  新建 /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/notes → /private/tmp/wt274b/home/.alibot/agents/mem_2307eb44660f/skills/notes（2 个文件 / 65 字节；hash 13c2e31a0efa1543a38b6defcb18372a16d3de3287c1e15e7b197568f1e631ca）
    原因：目标不存在，将复制字节；库行不存在，将按成员作用域新建
  新建 /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/review → /private/tmp/wt274b/home/.alibot/agents/mem_2307eb44660f/skills/review（2 个文件 / 67 字节；hash 188b124866aa976250a4a09958323fb98180b8e4dc78c8cdaaac103ed2a40be9）
    原因：目标不存在，将复制字节；将同步库行（source_path, content_hash, updated_at）
频道级技能（不迁移）： 1 个分区 / 1 个技能 —— 频道级已弃用，请按成员安装
  频道 ch_b206fc2afa64：/private/tmp/wt274b/project/.alibot/skills/ch_b206fc2afa64（1 个技能：channel-only）
    频道级已弃用，请按成员安装
技能迁移完成
  数据目录备份： /tmp/wt274b/data-backup-skills-20261025T032333Z
  迁移清单： /tmp/wt274b/data/skill-migration-20261025T032333Z.json
处理条目： 2 条（复制 2 / 跳过 0）
  复制 /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/notes → /private/tmp/wt274b/home/.alibot/agents/mem_2307eb44660f/skills/notes（2 个文件 / 65 字节；hash 13c2e31a0efa1543a38b6defcb18372a16d3de3287c1e15e7b197568f1e631ca）
    库行：新建（scope_kind=agent，scope_id=mem_2307eb44660f）
  复制 /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/review → /private/tmp/wt274b/home/.alibot/agents/mem_2307eb44660f/skills/review（2 个文件 / 67 字节；hash 188b124866aa976250a4a09958323fb98180b8e4dc78c8cdaaac103ed2a40be9）
    库行：更新 sk_review（source_path, content_hash, updated_at）
可手工删除的源路径（确认后；本命令不删除任何源文件）：
  /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/notes
  /private/tmp/wt274b/project/.alibot/skills/mem_2307eb44660f/review
EXIT=0
```

备份目录名带 `skills` 标签（与 schema 迁移的 `<数据目录>-backup-<from>-to-<to>-…` 区分）：

```text
$ ls -d /tmp/wt274b/data-backup-*
/tmp/wt274b/data-backup-skills-20261025T032333Z
```

库行终态（只读连接；`updated_at` 是本次迁移时刻；`source_path` 记录**来源**，即旧项目层路径）：

```text
{'id': 'sk_33805fc8189b', 'name': 'notes', 'scope_kind': 'agent', 'scope_id': 'mem_...', 'source_path': '/private/tmp/wt274b/project/.alibot/skills/mem_.../notes', 'h': '13c2e31a0efa', 'updated_at': '2026-10-25T03:23:33.965Z'}
{'id': 'sk_review', 'name': 'review', 'scope_kind': 'agent', 'scope_id': 'mem_...', 'source_path': '/private/tmp/wt274b/project/.alibot/skills/mem_.../review', 'h': '188b124866aa', 'updated_at': '2026-10-25T03:23:33.967Z'}
```

家目录里只有两个成员级技能（频道级 `channel-only` 不在其中）：

```text
/tmp/wt274b/home/.alibot/agents/mem_.../skills/notes/{SKILL.md,notes.txt}
/tmp/wt274b/home/.alibot/agents/mem_.../skills/review/{SKILL.md,notes.txt}
```

迁移清单 JSON（节选；`source` 是旧项目层路径、`target` 是成员家目录）：

```text
entries: [('notes', 'create', 'mem_...', source='.../project/.alibot/skills/mem_.../notes', target='.../home/.alibot/agents/mem_.../skills/notes', 2, 65),
          ('review', 'create', 'mem_...', source='.../project/.alibot/skills/mem_.../review', target='.../home/.alibot/agents/mem_.../skills/review', 2, 67)]
channel_level: [('ch_...', ['channel-only'], '频道级已弃用，请按成员安装')]
```

### ③ 幂等（同一条命令再跑一次）

```text
成员级技能： 2 条（新建 0 / 跳过 2 / 拒绝 0）
  跳过 .../notes → .../skills/notes（2 个文件 / 65 字节；hash 13c2e3…）
    原因：目标已存在且内容相同，不覆盖
  跳过 .../review → .../skills/review（2 个文件 / 67 字节；hash 188b12…）
    原因：目标已存在且内容相同，不覆盖
技能迁移：没有需要写入的条目（全部已就位或没有成员级字节）；未写入任何文件。
处理条目： 0 条（复制 0 / 跳过 0）
EXIT=0
```

### ④ 冲突（把目标 `review/SKILL.md` 人工改掉后再 apply）

```text
成员级技能： 2 条（新建 0 / 跳过 1 / 拒绝 1）
  跳过 .../notes → .../skills/notes（2 个文件 / 65 字节；hash 13c2e3…）
    原因：目标已存在且内容相同，不覆盖
  拒绝 .../review → .../skills/review（2 个文件 / 67 字节；hash 188b12…）
    原因：目标已存在但内容不同，需人工裁定后再迁移（本命令不覆盖、不提供 --force）
...
拒绝执行：存在目标同名但内容不同的条目。请先手动裁定（本命令不覆盖、不提供 --force）；本次未写入任何文件。
EXIT=6
```

## 门禁读数

```text
$ scripts/check.sh --fast tests/test_skill_migration.py tests/test_migration_chain_guard.py tests/test_schema_version_pins_guard.py
== pytest（指定文件）: tests/test_skill_migration.py tests/test_migration_chain_guard.py tests/test_schema_version_pins_guard.py
37 passed in 32.15s
All checks passed!                       # ruff check
Success: no issues found in 126 source files   # mypy（仓库根）
0 errors, 0 warnings, 0 informations     # pyright
ℹ tests 319 / pass 319 / fail 0          # node --test app/tests/*.test.cjs
门禁全绿
```

`git diff --check`：无输出。

## 人类执行步骤（本机真实数据）

> **本票未在本机真实数据上执行。** 以下步骤由人类在验收后手动跑；应用运行中不要执行。

1. **停应用**（不要带迁移参数启动）。
2. **先预演**（默认行为，零写入）：
   ```sh
   python -m alibot migrate-skills --data-dir ~/.alibot/data
   ```
   逐条读「新建 / 跳过 / 拒绝」与频道级分区数量。若有「拒绝」条目：先人工裁定（比对两棵树、
   决定保留哪一份），本命令不覆盖、没有 `--force`。频道级分区按提示改为「按成员安装」。
3. **执行**（破坏性动作必须两个开关）：
   ```sh
   python -m alibot migrate-skills --apply --yes --data-dir ~/.alibot/data
   ```
   先整目录备份到 `~/.alibot/data-backup-skills-<UTC>/`，再写迁移清单
   `~/.alibot/data/skill-migration-<UTC>.json`，随后逐条复制并更新 `skill` 行。
4. **核对**：目标字节在 `~/.alibot/agents/<member>/skills/<name>/`；`skill` 行的 `content_hash` /
   `updated_at` 已对齐新字节，`source_path` 记录**来源**（旧项目层路径）；源字节仍在项目层。
5. **确认后再手工删除源路径**（命令只打印清单，不删除）：
   ```sh
   rm -r <workspace_root>/.alibot/skills/<member>/<name>
   ```
   频道级 `<workspace_root>/.alibot/skills/<channel>/` 不迁移、不删除，由人类自行处置。
6. **重启应用**。若中途失败：命令已停止并列出已完成条目；已复制文件保留，不回滚删除；按报错修复后
   重复第 2–3 步（幂等：已就位的条目会报「跳过」）。

## 遗留 / 已知代价

- 同一技能名在**多个频道根**都有旧字节且内容不同时，命令会把这些条目标为「拒绝」，要求人工裁定；
  它不会按频道优先级猜一个。
- 目标已存在且内容相同的条目会对齐库行（`source_path` / `content_hash` / `updated_at`）：这样
  「字节已复制、行没来得及写」的半完成状态可以在重跑时收敛，而不是永久停在旧行上。`source_path`
  写的是**来源**（旧项目层路径），重复 apply 以「来源 + hash」判定已对齐，因此零写入。
- 迁移清单是**计划快照**（写入发生在复制之前），不记录逐条结果；逐条结果在 stdout。
