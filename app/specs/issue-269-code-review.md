# #269 集成审查收口（Standards + Spec 双轴）

- **固定点**：`main` @ `830b4ed`（Merge PR #268 from `issue-267-impl`）。
- **审查对象**：集成分支 `capabilities-agent-scope`（子票 #270–#274 已合并 / 关闭），审查起点 HEAD
  `f2455e2`（merge #273）。
- **本轮分支**：`issue-269-review-fix`（从 `f2455e2` 切出）。只做收口，不改 schema / `SCHEMA_VERSION`，
  不搬真实数据，不动装配代码（`agent_seam.py` / `mcp.py` / `skills.py` / `api.py`）。
- **标准来源**：`docs/agents/review-rules.md` 的标准清单 + `CONTEXT.md` Language + `docs/adr/**` +
  `app/README.md`。ADR 处置遵守第 4 条：历史段落不改写，修订一律显式标注「修订（#…）」。

## 结论摘要

| # | 轴 | Finding | 处置 |
| --- | --- | --- | --- |
| S1 | Standards | ADR-0015 仍写「不引入子命令」，与 #274 的 `migrate-skills` 子命令及退出码 6 矛盾 | **fixed**（ADR-0015 §一 末尾新增 `修订（#274）` 节；历史段落一字未改） |
| S2 | Standards | 技能迁移沿用 schema 迁移的备份名 `<data>-backup-<from>-to-<to>-<stamp>`，两种迁移的备份无法区分 | **fixed**（`backup_directory_name` / `_backup_data_dir` 加 `label`；技能迁移 `label="skills"`；schema 迁移名逐字不变） |
| S3 | Standards | `GET /api/skills` 的 README 口径写「旧 channel 行仍按其 scope 保留」，与实现（`scope_kind=channel` 返回空列表）不符 | **fixed**（README 端点表改为弃用 / 空列表 / 不带过滤不删的现行口径） |
| S4 | Standards | 文档多处承诺「应用在导出时写 `mcp.json`」——本仓没有导出写路径 | **fixed**（统一改成过渡期只读装配层口径，指向 follow-up #275） |
| S5 | Standards | `migrate-skills` 自己写 `skill` 行，与 `Database.create_skill` 形成第二条写路径 | **rejected**（见下） |
| S6 | Standards | `migrate.py` 出现 Divergent Change：扫描 / 计划 / 执行 / 备份 / CLI 编排全在一个模块 | **rejected-deferred**（见下） |
| S7 | Standards | `agent_seam._mcp_declarations` 里的 channel 过滤是死代码 | **rejected**（见下） |
| F1 | Spec | 票面「MCP 单正本」与运行时仍读 `mcp.json` 不一致 | **fixed（协调者决定 A）**：保留只读装配层，文档改过渡期口径，收尾交 #275 |
| F2 | Spec | 同 S4 的 Spec 侧：文档承诺未实现的导出写路径 | **fixed**（与 S4 同一批改动） |
| F3 | Spec | 「没有项目根时 Read/Glob/Grep 可读成员技能目录」与实现不符（DM / 无根轮次只有卸载目录的只读 `Read`） | **fixed**（CONTEXT / ADR-0021 / ADR-0031 / README 四处改条件式表述） |
| F4 | Spec | 「技能字节在项目根」的残留表述（`db.py` 表注释 / ADR-0032） | **fixed**（只改注释与文档；DDL 未动） |
| F5 | Spec | #270 票面要求「保留旧语义作为历史说明」，CONTEXT 三条词条缺历史段 | **fixed**（`全局层 / 项目层`、`MCP 声明`、`Skill` 各加「历史（#270 前）」） |
| F6 | Spec | `migrate-skills` 新建 / 更新行的 `source_path` 写目标家目录路径，丢失「从哪搬来」 | **fixed**（`source_path` = `entry.source`；幂等比较也改用来源） |
| F7 | Spec | 规则路径表述被改动（项目补充 `AGENTS.md` 扁平路径） | **accepted as fact correction**（见下） |

## 逐条处置与证据

### S1 ADR-0015 缺 #274 修订 — fixed

`docs/adr/0015-schema-migration-is-explicit-and-forward-only.md` §一 末尾新增：

> **修订（#274，2026-10-25）**：本仓新增**显式**技能字节迁移子命令 `python -m alibot migrate-skills`…
> 默认 dry-run；破坏性动作必须两个开关同给（`--apply --yes`）…有自己的退出码 **`6`**（拒绝 / 中途失败），
> 与 schema 迁移的 `3` / `4` / `5` 区分…schema 迁移**仍是** `--migrate` 单旗标入口、事务收口、
> **没有「部分完成」码**。

代码事实对照：`app/alibot/__main__.py:62` `SKILL_MIGRATION_EXIT_CODE = 6`；`--migrate` 仍是单旗标
（`--dry-run` / `--data-dir` 不变）。ADR-0015 原来的「本仓 CLI 是单 argparse 入口，不引入子命令」作为
2026-10-16 的历史决策原样保留，不就地改写。

### S2 备份命名区分 — fixed

- `migrate.backup_directory_name(..., label: str | None = None)`：`label` 非空 → `<数据名>-backup-<label>-<stamp>`；
  默认 `None` → 仍是 `<数据名>-backup-<from>-to-<to>-<stamp>`（schema 迁移调用点未改）。
- `migrate._backup_data_dir(..., label=None)` 透传；`apply_skill_migration` 传 `label="skills"`。
- 测试：`tests/test_skill_migration.py` 断言唯一备份目录名含 `-backup-skills-`；schema 迁移断言
  （`test_schema_migration.py` / `test_reminder_schema_step.py` 的 `-backup-<from>-to-<to>-`）不变且仍绿。
- 文档：`app/README.md` 数据目录备份段、`docs/adr/0021-*.md` §五 数据目录行各补一句 #274 命名与
  `skill-migration-<UTC>.json` 清单。

### S3 `GET /api/skills` 弃用口径 — fixed

实现：`app/alibot/api.py:1140-1143` —— `scope_kind == "channel"` 直接返回 `{"skills": []}`；不带过滤时
`database.list_skills()` 仍返回库内旧行（本 PR 不删）。README 端点行改为：

> 旧 channel 行仍在库中保留（迁移前不删），但 `GET` 带 `scope_kind=channel` 返回空列表（弃用，不提供读取面）；
> 不带过滤的旧行不在本 PR 删除。

### S4 / F2 `mcp.json` 导出承诺 — fixed（过渡期只读装配层）

**现行运行时事实**（未改动）：`agent_seam._mcp_declarations` 把「Agent 级库行」与「家目录 `mcp.json`」
交给 `mcp.merge_mcp_layers`，同名先出现者胜出（库行在前 = 优先级更高）；落选条目只往 notes 追加
`mcp.assembly.shadowed`（`layer=global`）→ 进 `RoundOutcome.reason`，不进 provider system prompt。
`test_global_mcp.py` / `test_mcp_assembly.py` 行为全部未动。

改的是文档口径（`app/README.md` 90-94 / 109 / 242 / 487 / 517-519 / 659；`CONTEXT.md` Agent 家目录 /
全局层 / MCP 声明；ADR-0021 §三；ADR-0031 顶部修订说明 / §一表 / §二 / §三 / §四表）：

- 家目录 `mcp.json` 是**只读导入源**，过渡期仍是**只读装配层**（同名低于成员库行，落选进 `layer=global` 提示）；
- 它不是与库行并列的正本，**本轮不提供导出写路径**；
- 单正本收尾交 follow-up #275（`migrate-mcp` 导入命令并移除装配层）。

**为什么不是「删掉装配层」**（协调者决定 A）：删除会连带改 `agent_seam` / `mcp.py` 与既有回归
（`test_global_mcp.py`），超出 #269 的收口范围；#275 才是单一正本的迁移票。

### F3 DM / 只读第二根 — fixed

实现：`agent_seam.py:810-819` —— `mount is None` 的轮次只装卸载目录的只读 `Read`（`PartitionBackend`），
没有 Glob / Grep / 项目根工具；成员技能只读第二根由 `assemble_workspace` 在有项目根的轮次里挂。
四条文档去掉「没有项目根时 Read/Glob/Grep 可读成员技能目录」，改为：

- `CONTEXT.md` **Agent** / **Agent 家目录** / **工作区项目根**：只有装配了项目根的 local 轮次才挂只读第二根；
  DM / 无项目根轮次没有这套文件工具（只有卸载目录的只读 `Read`）。
- `docs/adr/0021-*.md` §六「保留」条、`docs/adr/0031-*.md` §三第一条与 §四表第一行、`app/README.md` 475-482。
- 技能**装配**不依赖 workspace 这句保留（它描述的是技能合并顺序，与文件工具无关）。

### F4 「技能字节在项目根」残留 — fixed

- `app/alibot/db.py:824-827`（仅注释，DDL 未动）：字节由宿主应用写入
  `~/.alibot/agents/<member_id>/skills/<name>/`；库里只存来源路径与 hash；旧的
  `workspace_root/.alibot/skills/<member_id>/` 字节保留但不再进入装配。
- `docs/adr/0032-*.md` 34-37：项目层旧字节保留、不再进入装配；现行字节在成员家目录（标注
  「修订（#270）」）。历史事实句保留，未删段。

### F5 CONTEXT 历史说明 — fixed

按 #270 票面「保留旧语义作为历史说明（不删）」，在三条现行定义之后各加一段，均以「历史（#270 前）」起头：

- **全局层 / 项目层**：旧四层优先级「项目 Agent 级 > 项目频道级 > 全局成员家目录 > 全局公共库」，
  项目层字节落 `<workspace_root>/.alibot/skills/<scope_id>/`。
- **MCP 声明**：`mcp.json` 纯人工维护，并与项目层声明合并后一起装配。
- **Skill**：四层装配与项目层字节路径。

历史段明确标为历史，不与现行段混写。

### F6 `migrate-skills` 的 `source_path` — fixed

- `_upsert_skill_row`：新建行的 `source_path` = `entry.source`（旧项目层源路径）；更新行同样写来源。
- `_skill_row_changes(row, source, content_hash)` 与来源比较；`_plan_entry` 传 `source`。
- 幂等：第一次 apply 后行里已是来源路径，第二次 apply 命中「跳过（内容相同）」且零写入。

实测（临时 HOME + 临时数据目录，非用户真实数据）：

```text
('sk_review', 'review', 'agent', 'mem_...', '/tmp/wt274b/project/.alibot/skills/mem_.../review', '188b124866aa', '2026-10-25T03:23:33.967Z')
```

`source_path` 是项目层来源；`content_hash` 已是重算后的真实值。`app/specs/issue-274-acceptance.md`
的判据表与真实输出示例已同步（含备份名 `data-backup-skills-…`）。

### F7 规则路径 — accepted as fact correction

审查把「项目补充规则路径」的文档改动记为疑似越界。核对结论：`workspace.read_project_rules` 读的是
`.alibot/agents/<member_id>/AGENTS.md`（`workspace.py:384-392`，`ALIBOT_DIR / AGENTS_DIR / member_id / RULES_FILE`），
与 ADR-0032 §二 的扁平同构决策一致；ADR-0031 §一 的历史 `rules/AGENTS.md` 只是旧路径。因此这是**事实修正**：
不新增决策、不改 ADR-0031 的历史段，只在现行段落写扁平路径。

### S5 重复写路径 — rejected

`Database.create_skill` 是「安装」入口：它只接受新建（`_assert_skill_name_free_locked` 同名 409），
没有更新 `source_path` / `content_hash` 的参数；迁移要做的正是**更新既有行**并对齐 hash。
`migrate` 侧的统一校验已经落在 `skills.read_skill_tree`（front matter / 路径 / hash），迁移不复制第二套算法；
把 `create_skill` 扩成「可更新」会让公共写 API 承担迁移语义，并引入第二条连接写入层。故保留迁移层自己的
`INSERT/UPDATE`，由 CLI 的场景（离线、一次性）与测试覆盖。

### S6 Divergent Change — rejected-deferred

`migrate.py` 现在承载 schema 链 + 技能迁移的扫描 / 计划 / 执行 / 备份编排，确实是两类变化。但 #274 票面
指定落在 `migrate.py` 并复用 `connect` / 整目录备份；拆模块会改调用面与测试接缝，属于独立重构（后续票），
不在 #269 收口范围内。

### S7 装配层 channel 过滤是死代码 — rejected

`test_mcp_assembly.py` 直接向 seam 注入含 `channel` 行的 `_mcp_of`，用来守住「频道级旧行不进入装配」。
装配层 `agent_rows = [row for row in database_rows if scope_kind == "agent"]` 因此是**防御性过滤**：
它把「库里有旧行」与「装配不看旧行」这两件事分开，去掉它会让注入式用例与将来的读路径变化直接漏进装配。
非死代码。

## 验证读数

（命令均在 `wt-review` worktree、`.venv` 下跑；只跑相关用例族，全量由协调者合并后跑。）

### 迁移 / 备份 / 来源路径

```text
$ scripts/check.sh --fast tests/test_skill_migration.py tests/test_schema_migration.py tests/test_migrate_cli.py
77 passed in 103.35s (0:01:43)
All checks passed!                              # ruff check
337 files already formatted                     # ruff format --check
Success: no issues found in 127 source files    # mypy（仓库根）
0 errors, 0 warnings, 0 informations            # pyright
ℹ tests 317 / pass 317 / fail 0                 # node --test（Studio DOM 用例族）
门禁全绿
```

单跑 `tests/test_skill_migration.py`：`11 passed in 12.21s`（含新增的「备份目录名含 `-backup-skills-`」
与「新建行 `source_path` = 旧项目层源路径」断言）。

schema 迁移的备份名未被改动：`tests/test_schema_migration.py` 与 `tests/test_reminder_schema_step.py`
仍断言 `<数据目录>-backup-<from>-to-<to>-<UTC>`（上面 77 passed 覆盖前两者）。

### 文档守卫

```text
$ scripts/check.sh --fast tests/test_docs_vocabulary_guard.py tests/test_test_layering_guard.py tests/test_backend_copy.py
28 passed in 2.23s
All checks passed!
Success: no issues found in 127 source files
0 errors, 0 warnings, 0 informations
ℹ tests 317 / pass 317 / fail 0
门禁全绿
```

### 手工复跑（migrate-skills，临时 HOME + 临时数据目录）

```text
$ ls -d /tmp/wt274b/data-backup-*
/tmp/wt274b/data-backup-skills-20261025T032333Z

# 第二次 apply（幂等）
成员级技能： 2 条（新建 0 / 跳过 2 / 拒绝 0）
技能迁移：没有需要写入的条目（全部已就位或没有成员级字节）；未写入任何文件。
EXIT=0
```

`git diff --check`：无输出。

## 遗留

- **#275**（follow-up）：`migrate-mcp` 导入命令并移除 `mcp.json` 只读装配层；届时同步 `agent_seam` /
  `merge_mcp_layers` 的调用与 `test_global_mcp.py`。
- `migrate.py` 的 Divergent Change（S6）留作重构票，不在本轮拆。
- 未跑全量门禁（协调者在合并后跑一次全量）。
