# #277 验收：技能安装的两个来源（从 `~/.agents/skills` 导入；应用不执行 npx）

- 票：https://github.com/whutlichao/alibot/issues/277
- 分支：`issue-277-skill-import`；固定点（base commit）：`5f7dd9c`
- 门禁：G-impl；测试档**宽（全量）**——改动同时触及 `app/alibot/skills.py`（校验内核）、
  `app/alibot/api.py`（共享 HTTP 面）、前端三件套与文档
- 本票**不新增 schema**：`SCHEMA_VERSION` 未动，`app/tests/_migration_history.py` 未动

## 1. 判据 → 落点

| 票面判据 | 落点 |
| --- | --- |
| 只读候选扫描；来源根 `~/.agents/skills` 经 `expanduser("~")` 解析（不复用 `global_home_root`、不硬编码主目录） | `skills.py`：`IMPORT_SOURCE_DIRNAME` / `IMPORT_SOURCE_SKILLS_DIR` 与 `SKILLS_DIR` 并列；`import_source_root()` 只经 `os.path.expanduser("~")` |
| 根不存在 → 「来源缺席」形态，**不创建目录、不报错** | `scan_import_candidates()` 先 `os.path.isdir(root)`，缺席即 `{"source", "available": False, "candidates": []}` |
| 只遍历直接子目录；非目录条目跳过 | `Path(root).iterdir()` + `if not entry.is_dir(): continue`（`~/.agents/.skill-lock.json` 不进候选） |
| 每条的 `name` / `description` / `path` / `importable` / `reason` / `installed` | `scan_import_candidates()` 的 `candidate` 字典；`installed` 取 `database.list_skills(scope_kind="agent", scope_id=member_id)` 的 name 集合 |
| 校验**复用既有内核**，不写第二套 | 直接调 `SKILL_NAME_PATTERN` / `_manifest_root` / `_front_matter` / `_skill_files`；`read_skill_tree` / 安装器 / 候选扫描共用同一份 |
| 坏条目**逐条降级**（本机 `~/.agents/skills/t18-escape` 无 `SKILL.md`） | `try/except SkillValidationError` 每条各自收口，`reason` 用 `i18n.render` 按 `locale` 渲染（skills.py 里没有中文字面量） |
| 路径安全：以来源根自己的 realpath 为根，拒绝根外 symlink 祖先 / 根外目标 | `resolve_global_read_path(root, name)`；解析不到 → `importable=false` + `error.skill_path_invalid` |
| **只读**：不复制、不创建、不改字节 | 函数体只有 `isdir` / `iterdir` / `resolve_global_read_path` / `_front_matter` / `_skill_files` / `database.list_skills`；测试 ① 断言来源目录未被创建，④ 断言来源树逐字未变 |
| `GET /api/skills/candidates`，query `scope_kind` / `scope_id` | `api.py`：`list_skill_candidates()`；`scope_kind != agent` 走既有 `resolve_install_root` 口径 400（`error.skill_scope_agent_only`），`scope_id` 走 `database.validate_skill_scope` |
| 响应形状固定 `{source, available, candidates}` | 同上；`source` 是来源根的绝对路径（`realpath` 规范化） |
| 只读端点：不落消息、不产生轮次、不改预算、不写文件 | 无任何写调用；测试 ⑥ 前后比对 `messages` / `outcomes` / `budget` |
| 不改 `POST /api/skills` 语义（导入复用写路径，`source_path` = 候选 `path`） | `api.py` 的 `install_skill()` 一字未改；前端 `submitSkillImport()` 逐条 POST 同一端点 |
| Studio：命令提示（`<code>`、ASCII 常量不进词条） | `app.js`：`SKILL_INSTALL_COMMAND` 常量 + `skillImportForm()` 的 `data-od-id="skill-import-command"` |
| 「刷新候选」按钮 `data-skill-import-scan` → GET 候选 | `skillImportForm()` + `loadSkillImportCandidates(memberId)`；委派在既有 `#adBody` 点击链里 |
| 候选行：复选框 `data-skill-import-pick="<name>"` + 名字 + 描述 + 状态后缀 | `skillImportRow()`；状态键 `skill.importStatusReady` / `…Installed` / `…Blocked`（`{reason}`） |
| **默认勾选**「`importable` 且未 `installed`」；已安装 / 不可导入**禁用** | `loadSkillImportCandidates()` 填 `skillImportPicks`；`skillImportSelectable()` 决定 `checkbox.disabled` |
| 「导入选中」`data-skill-import-run` → 逐条 `POST /api/skills`；409 记「已跳过」，其他失败记原因；逐条渲染 `data-skill-import-status`；结束后重读技能列表 | `submitSkillImport()`：`err.status === 409` → `skipped`，其余 → `failed`；末尾 `loadAgentSkills(memberId)` |
| 读失败只落在本区块自己的错误行；候选为空给空态说明（含「先运行上面的命令」） | `skillImportForm()` 的 `data-od-id="skill-import-error"` 与 `skill.importEmpty` / `skill.importUnavailable` |
| 视图状态沿用 `capabilitiesView` + `emptyCapabilitiesView`；成员切换按 `resetAgentCapabilities` 丢弃 | `emptyCapabilitiesView()` 新增 `skillImportLoading` / `skillImportError` / `skillImportSource` / `skillImportAvailable` / `skillImportCandidates` / `skillImportPicks` / `skillImportResults` / `skillImportBusy` |
| 安装中 `is-faint` 忙碌提示；按钮沿用 `resourceButton`，`state.online` 为假时禁用 | `skill.importBusy` 的 `is-faint` 行；两个按钮都来自 `resourceButton`（它已按 `state.online` 设 `disabled`） |
| 事件委派加在既有 `document` 委派链（与 `data-skill-install-dir` 同段），不新开监听器 | `#adBody` 的 click 链（scan / run）与 change 链（pick），无新增 `addEventListener` |
| 前端两份词条同键集；`app.js` / `app.css` / `index.html` 无裸中文 | `studio/i18n.js` 新增 `skill.import*` 21 条（zh-CN / en）；`i18n_ui.test.cjs` 全绿 |
| 后端词条只在需要新原因时新增，两份语言同键集 | `alibot/i18n.py` 只加 `error.skill_import_name_invalid`（目录名不能当技能名）；其余复用 `error.skill_*` |
| 新 ADR 0036（Context / Decision / Consequences + 与 0026 / 0031 的逐条关系） | `docs/adr/0036-skill-install-has-two-sources.md` |
| `CONTEXT.md` 的 **Skill** 词条 | 安装来源改为「本机目录、上传 zip，或从 `~/.agents/skills` 导入」；`_Avoid_` 补「`npx skills` 是外部 CLI，本应用只读它的落点，不代为执行」；历史段未动 |
| `app/README.md`：端点表新增一行 + `POST` 那行补 `source_path` 也可指向导入源 + 技能段落补「不执行 npx」 | 端点表 `GET /api/skills/candidates`；`POST /api/skills` 行内补注；`## Workspace / 权限 / 审批 / MCP / Skill` 的 **Skill** 段 |

## 2. 后端测试（`app/tests/test_skill_import.py`，HTTP 黑盒）

七条判据逐条对应一个用例：

| # | 用例 | 断言 |
| --- | --- | --- |
| ① | `test_missing_source_root_is_available_false_and_is_not_created` | `200` / `available=false` / `candidates=[]` / `source` 是来源根；**`not root.exists()`** |
| ② | `test_bad_entries_degrade_one_by_one_and_the_rest_are_listed` | 三条候选（合法 / 无 `SKILL.md` / front matter 坏）全部列出；坏条目 `importable=false` + 非空 `reason`；`.skill-lock.json` 不进候选 |
| ③ | `test_a_name_already_installed_is_reported_as_installed` | 先经 `POST /api/skills` 装好，候选该条 `installed=true` |
| ④ | `test_import_writes_the_bytes_into_the_member_home_with_the_source_hash` | `201`；字节落 `<全局根>/agents/<member>/skills/review/` 且逐字等于源；`content_hash` == `read_skill_tree(源)`；源树未被改动 |
| ⑤ | `test_a_second_import_is_409_and_leaves_the_installed_bytes_untouched` | 来源在两次之间被改过 → 仍 `409`（`code=skill_conflict`），家目录字节**未覆盖**，库里仍只有一行 |
| ⑥ | `test_scanning_and_importing_have_no_message_round_or_budget_side_effects` | 扫描 + 导入前后 `messages` / `round_outcomes` / `budget` 逐字相同 |
| ⑦ | `test_channel_scope_is_rejected_with_400` | `scope_kind=channel` → `400`（`validation_failed`） |

读数：

```sh
cd app && ../.venv/bin/python -m pytest tests/test_skill_import.py
# 7 passed in 0.56s

cd app && ../.venv/bin/python -m pytest tests/test_skill_import.py tests/test_skills.py tests/test_skill_copy.py tests/test_global_skills.py
# 34 passed in 18.52s
```

## 3. 前端测试（`app/tests/agent_capabilities_ui.test.cjs`，追加 5 条）

沿用该文件既有的 `FakeNode` / fetch 替身，不新开第二套框架。新增 `candidatePayload()` 与
`importConflict` / `importFail` / `candidates` / `candidatesAvailable` 替身开关。

| 用例 | 断言 |
| --- | --- |
| 导入区块：读候选、默认勾选规则、禁用态 | 区块在既有 `skill-install` **之后**；命令文本逐字 `npx skills add <owner/repo> -g -a codex -y`；候选按 `?scope_kind=agent&scope_id=mem-1` 读；可导入且未安装 → `checked=true` / `disabled=false`；不可导入与已安装 → `disabled=true`，状态后缀含原因 |
| 导入选中：逐条 POST + 重读 | 取消全部勾选 → 按钮 `disabled`，不发请求；重新勾上 → 按钮恢复、`POST /api/skills` 正文 `{scope_kind, scope_id, source_path}`；结束后重读技能列表 |
| 候选为空 | 不渲染候选列表，给「先运行上面的命令」的空态指引，不算错误 |
| 409 / 失败逐条结果 | 两条各一行：`review：已存在，已跳过`、`other：导入失败：…`；不出现 PATCH 覆盖 |
| 来源缺席 / 扫描失败 | 缺席不是错误（`skill-import-error` 为 null）；扫描失败只落在 `skill-import-error`，技能列表的 `skill-error` 不受牵连，安装表单照常 |

```sh
node --test app/tests/agent_capabilities_ui.test.cjs
# ℹ tests 14 / ℹ pass 14 / ℹ fail 0

node --test app/tests/*.test.cjs
# ℹ tests 322 / ℹ pass 322 / ℹ fail 0
```

## 4. 真实浏览器取证（ego-browser，隔离端口 + 临时数据目录）

**绝不碰 8788。** 后端在 `127.0.0.1:8891` 上启动，`HOME` 与 `ALIBOT_DATA_DIR` 都指向
`/tmp/issue277-live-145250/`；`PYTHONPATH` 钉到本 worktree 的 `app/`（`.venv` 的 editable 安装指向
主工作区，不钉会跑到 main 的代码上）。

来源根造了三条候选 + 一个外部 CLI 的锁文件：

```text
/tmp/issue277-live-145250/home/.agents/.skill-lock.json   （非目录，不进候选）
/tmp/issue277-live-145250/home/.agents/skills/review/     （合法）
/tmp/issue277-live-145250/home/.agents/skills/t18-escape/ （没有 SKILL.md）
/tmp/issue277-live-145250/home/.agents/skills/broken-front-matter/（front matter 坏）
```

| 实测项 | 读数 |
| --- | --- |
| 候选端点（curl） | `source=/private/tmp/issue277-live-145250/home/.agents/skills`、`available=true`、三条候选，`review` 的 `description` 来自 front matter，两条坏条目带可读 `reason` |
| 区块渲染（DOM） | `hasSection=true` / `hasBlock=true` / `command="npx skills add <owner/repo> -g -a codex -y"` / `scanButton=true` / `runButton=true` |
| 默认勾选 | `broken-front-matter: checked=false disabled=true`；`review: checked=true disabled=false`；`t18-escape: checked=false disabled=true` |
| 逐条状态后缀 | `broken-front-matter不可导入：SKILL.md 的 front matter 无法解析：…`；`review在提交前做一次结构化自审可导入`；`t18-escape不可导入：技能根目录缺少 SKILL.md` |
| 导入成功 | 结果行 `review：已导入`；技能列表出现 `review已启用…hash 4b58ff2e2c4c停用删除复制到…`；toast `导入完成：成功 1，跳过 0，失败 0` |
| 字节落点 | `find ~/.alibot/agents/mem_d4dc57a317ec/skills` → `review/SKILL.md` + `review/references/checklist.txt`；`md5` 与源目录逐字相同 |
| 库内读数 | `GET /api/skills` → `source_path=/private/tmp/…/.agents/skills/review`，`content_hash=4b58ff2e2c4cad81a49e8d32a1ca973d88c9d745c9b2f5f4863734d7500e970a` |
| 重复导入 → 409 | 另一路先把 `review` 装进同一成员家目录（页面上的候选读数已过期）→ 点「导入选中」得到 `review：已存在，已跳过`（`data-import-status="skipped"`），技能列表仍只有 1 行，家目录字节 `md5` 不变 |
| 刷新候选 | `review` 变为 `checked=false disabled=true`，状态后缀 `已安装` |
| 英文 | `Import from ~/.agents/skills` / `Refresh candidates` / `Import selected` / `Cannot import: …` / `Already installed`；命令提示仍是同一条 ASCII 命令 |
| 视觉 | 命令块等宽、两个按钮 `width=66px`（`flex: 0 0 auto`，不铺满整行）；截图 `01-candidates.png` / `02-imported.png` / `03-repeat-skipped.png` / `04-refreshed.png` / `06-import-block-final.png` / `07-english.png`（`/tmp/issue277-evidence/`） |

实测中发现并修掉的一个真问题：导入成功清空勾选后，「导入选中」按钮保持禁用（重铺只在 `busy`
翻转时发生），导致人重新勾选也点不动。修法：`toggleSkillImportPick()` 末尾 `paintAgentCapabilities()`，
让按钮可用性跟着勾选走；UI 用例补了「取消全部勾选 → 按钮禁用 → 重新勾上 → 恢复可用」的读数。

## 5. 门禁读数

```sh
scripts/check.sh --fast tests/test_skill_import.py
# pytest: 7 passed
# == ruff check            All checks passed!
# == ruff format --check   通过（只检查，不跑 ruff format）
# == mypy（仓库根）        通过
# == pyright               通过
# ℹ tests 322 / ℹ pass 322 / ℹ fail 0（node glob）
# 门禁全绿

scripts/check.sh
# （全量读数见 §5.1）
```

### 5.1 全量

```sh
scripts/check.sh
# （读数回填见下）
```

## 6. 双轴 code-review（固定点 `5f7dd9c`）

两轴并行子代理，结论与 findings 处置见下。

## 7. 约束自检

- 未改 `SCHEMA_VERSION`、未新增迁移、未动 `app/tests/_migration_history.py`
- 无 subprocess / `asyncio.create_subprocess_*` / 新网络请求；未引入 npm / Node 运行时依赖
- 未复制第二套校验实现：候选扫描直接调 `SKILL_NAME_PATTERN` / `_manifest_root` / `_front_matter` / `_skill_files`
- `POST` / `PATCH` / `DELETE /api/skills` 与 `copy` 端点语义未改；装配两层顺序未改
- `migrate-skills` 与 `~/.alibot/skills` 公共库写入路径未动
- 未跑 `ruff format`；未新增前端测试框架；未 import 私有名做行为断言
