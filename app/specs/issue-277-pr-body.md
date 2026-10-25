## Summary

技能安装多一个来源：第三方 CLI（`npx skills add … -g -a codex`）把技能装进 `~/.agents/skills/`，本应用
**只读**那个落点，人选中后经**既有** `POST /api/skills` 复制进成员家目录。应用**不执行 npx**、不起
subprocess、不下载第三方包（ADR-0036）。

```text
Agent 详情 · 技能段（#adBody）
  skillRow()                既有行（启停 / 删除 / 复制到…）
  skillInstallForm()        既有：本机目录 / 上传 zip
  skillImportForm()         新增 ← ADR-0036
    ├─ <code> npx skills add <owner/repo> -g -a codex -y    ASCII 常量，不进词条
    ├─ 说明（-g 全局 / -a codex → ~/.agents/skills）
    ├─ [刷新候选] data-skill-import-scan  → GET /api/skills/candidates
    ├─ 候选行：复选框 data-skill-import-pick + 名字 + 描述 + 状态后缀
    │    默认勾选 importable && !installed；已安装 / 不可导入 → disabled
    ├─ [导入选中] data-skill-import-run   → 逐条 POST /api/skills
    └─ 逐条结果 data-skill-import-status  imported / skipped(409) / failed(reason)
```

```text
GET /api/skills/candidates?scope_kind=agent&scope_id=<member>      ← 只读
  resolve_install_root(...)            # 非 agent → 400 error.skill_scope_agent_only
  scan_import_candidates(database, member_id, locale)
    root = realpath(expanduser("~")/.agents/skills)
    ├─ 缺席 → {source, available:false, candidates:[]}      # 不创建、不报错
    └─ 每个直接子目录一条
         resolve_global_read_path(root, name)               # 根外 symlink 一律拒
         _manifest_root → _front_matter → _skill_files      # 与安装器同一内核
         → {name, description, path, importable, reason, installed}
              installed 按 front matter 的 name 判（安装器落库用的就是它）

POST /api/skills  {scope_kind, scope_id, source_path=<候选 path>}   ← 语义未改，导入复用它
  同名已存在 → 409（界面记「已存在，已跳过」，字节不覆盖）
```

```diff
 app/alibot/
 ├── skills.py        + scan_import_candidates / import_source_root / 两个目录名常量
 ├── api.py           + GET /api/skills/candidates
 ├── i18n.py          + error.skill_import_name_invalid（两份语言）
 └── studio/
     ├── app.js       + skillImportForm / skillImportRow / loadSkillImportCandidates
     │                + submitSkillImport / toggleSkillImportPick / SKILL_INSTALL_COMMAND
     ├── i18n.js      + skill.import* 17 条 × 2 语言
     └── app.css      + .resource-command / .resource-import-actions
 docs/adr/0036-skill-install-has-two-sources.md   + 新 ADR
 CONTEXT.md · app/README.md                        ~ Skill 词条 / 端点表 / 技能段
```

## Evidence

- **Before:** `GET /api/skills/candidates` → 404（路由不存在）；Studio 技能段只有「本机目录 / 上传 zip」，
  没有任何从 `~/.agents/skills` 导入的入口。
- **After（后端，`cd app && ../.venv/bin/python -m pytest tests/test_skill_import.py`）**：`9 passed in 0.50s`。
  九条用例覆盖票面 7 条判据 + 两条 review 追加判据：
  - 来源根缺失 → `available=false` / `candidates=[]` / **目录未被创建**
  - 合法 / 无 `SKILL.md` / front matter 坏三条候选同时列出，坏条目逐条降级带可读 `reason`；
    `.skill-lock.json` 不进候选
  - 已安装同名 → `installed=true`；**目录名 ≠ 清单名**时仍按清单名认出「已安装」
  - 导入 → `201`，字节逐字落进 `<全局根>/agents/<member>/skills/<name>/`，`content_hash` 与
    `read_skill_tree(源)` 相同，源目录未变；**包了一层单目录**的候选 description 不丢
  - 重复导入 → `409`（`skill_conflict`），家目录字节未被覆盖，库里仍只有一行
  - 扫描 + 导入前后 `messages` / `round_outcomes` / `budget` 逐字相同
  - `scope_kind=channel` → `400`
- **After（前端，`node --test app/tests/agent_capabilities_ui.test.cjs`）**：`14 pass / 0 fail`（新增 5 条）。
- **After（词条守卫，`node --test app/tests/i18n_ui.test.cjs`）**：`53 pass / 0 fail`。
- **After（真机，ego-browser + 隔离端口 8891 + 临时 HOME/数据目录，绝不碰 8788）**：
  DOM 读数 `command="npx skills add <owner/repo> -g -a codex -y"`；默认勾选
  `review: checked=true disabled=false`、`t18-escape / broken-front-matter: disabled=true`；
  导入后结果行 `review：已导入`、技能行出现、`md5` 与源目录逐字相同；另一路先装好再点导入 →
  `review：已存在，已跳过`（`data-import-status="skipped"`）、技能列表仍 1 行、字节 md5 不变；
  刷新候选后 `review` 变 `disabled=true` + 后缀「已安装」；英文界面 `Import from ~/.agents/skills` /
  `Refresh candidates` / `Cannot import: …`。截图见 `app/specs/issue-277-acceptance.md` §4。
- **门禁（`scripts/check.sh`，全量）**：代码树 `1000632` 起共跑六次全量，**五次全绿**，计数完全一致
  （只有 pytest 耗时随负载波动：695.02s / 692.52s / 689.73s / 689.09s / 693.00s）：pytest
  `1210 passed, 11 skipped`、ruff check `All checks passed!`、ruff format `--check`
  `340 files already formatted`、mypy `no issues in 128 source files`、pyright `0 errors`、
  node glob `322 pass / 0 fail`、`门禁全绿`。`1000632` 之后只有 `app/specs/**` 的文档提交，
  不改任何门禁判据；读数与上一轮同值，因为清掉的是 37 个没有任何代码/测试引用的运行期日志
  （详见 `app/specs/issue-277-acceptance.md` §5.2）。
- **一次既有偶发（如实登记，未修）**：第六次全量红过一次，唯一失败是
  `test_agent_round.py::test_ignored_round_advances_the_cursor_so_it_is_not_reread`
  （`assert [2, 1] == [1, 2]`）。根因在 `db.py:2502` 的 `ORDER BY finished_at, id`：同一毫秒的两条
  `round_outcome` 由**随机 UUID** 决胜。该行在 `main` 上完全相同，本分支从未触碰 `db.py` 与该测试；
  单跑 3 次、整文件连跑 30 次全过，同树重跑全量即绿。修它要改排序键（另一张票），本票范围内未动。
  详见 `app/specs/issue-277-acceptance.md` §5.3。
- **误提交的运行期日志（本轮修正）**：`70fcb6d` 把 37 个 `app/logs/2026-10-25_15-*.log` 带进了提交树
  （`main` 上该目录与 `.log` 文件数都是 0）。`1000632` 用 `git rm -r --cached app/logs` + 删盘移除，
  并在 `.gitignore` 补 `/logs/` 与 `/app/logs/`（锚定根，附成因注释：ReMe / AgentScope 的 logger 默认
  `log_dir="logs"`，按进程 cwd 落盘）。验证：`git ls-tree -r --name-only HEAD -- app/logs` 为空；
  在 `app/` 下跑 pytest 后 logger 仍会落盘（全量门禁跑完盘上 75+ 个 `.log`），但
  `git status --porcelain` 不含 `app/logs/`。
- **双轴 review（固定点 `5f7dd9c`）**：Standards 3 hard + 3 判断 → 6 条全修（文档串自相矛盾、
  ADR 计数与 CONTEXT/README 冲突、`installed` 按目录名判定、结果行存渲染后文案、Middle Man、
  证据计数）；Spec 3 missing + 3 scope + 2 wrong → missing 3 修（验收文档回填、交付流程、`data-skill-import-status`）、
  wrong 2 修（同 Standards #3、wrapper 目录 description 丢失）、scope 3 保留并写明理由。
  逐条处置见 `app/specs/issue-277-acceptance.md` §6。

## Merge Danger

**Door:** two-way。新增的是一条**只读**端点与一个界面区块；没有 schema 变更、没有迁移、没有新的写语义
（导入复用既有 `POST /api/skills`）。回滚只需撤掉端点与区块，库与成员家目录字节不受影响——已导入的技能
就是普通的既有安装。

**Blast Radius:** 技能安装面。`skills.py` 新增函数不改既有 `install_directory` / `install_zip` /
`load_skills` / 装配两层顺序；`api.py` 只加路由（`POST /api/skills` 的 hunk 是 27 行插入、0 删除）。
`~/.agents/skills/` 是**别人的目录**，应用只读、不创建、不留标记；`~/.alibot/` 下没有任何新增落点。
风险面集中在两处：候选端点读文件系统的行为（路径经 `resolve_global_read_path`，根外 symlink 一律拒），
以及前端新增的委派分支（在既有 `#adBody` 链里，与 `data-skill-install-dir` 同段）。

Closes #277
