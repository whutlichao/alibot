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
| 前端两份词条同键集；`app.js` / `app.css` / `index.html` 无裸中文 | `studio/i18n.js` 新增 `skill.import*` 17 条（zh-CN / en 各一份，共 34 行）；`i18n_ui.test.cjs` 全绿 |
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

### 5.1 `scripts/check.sh --fast tests/test_skill_import.py`

```text
9 passed in 0.50s
== ruff check            All checks passed!
== ruff format --check   340 files already formatted（只检查，不跑 ruff format）
== mypy（仓库根）        Success: no issues found in 128 source files
== pyright               0 errors, 0 warnings, 0 informations
ℹ tests 322 / ℹ pass 322 / ℹ fail 0（node glob）
门禁全绿
```

### 5.2 全量 `scripts/check.sh`

两次全量，都是清理后的树（同一个工作区、同一个 `.venv`、同一台机器）：

```text
# 树 1000632（chore：移除 app/logs + .gitignore 两条规则）
== pytest（全量）        1210 passed, 11 skipped in 695.02s
== ruff check            All checks passed!
== ruff format --check   340 files already formatted
== mypy（仓库根）        Success: no issues found in 128 source files
== pyright               0 errors, 0 warnings, 0 informations
ℹ tests 322 / ℹ pass 322 / ℹ fail 0
门禁全绿

# 树 d2eec4f（docs：本文件 §5.2 / §5.3 / §9 与 PR 正文正本）
== pytest（全量）        1210 passed, 11 skipped in 692.52s
== ruff check            All checks passed!
== ruff format --check   340 files already formatted
== mypy（仓库根）        Success: no issues found in 128 source files
== pyright               0 errors, 0 warnings, 0 informations
ℹ tests 322 / ℹ pass 322 / ℹ fail 0
门禁全绿
```

**代码树 = `1000632`**（`chore(#277): 移除误提交的运行期日志，并堵住产生源`）——即
`git rm -r --cached app/logs`（37 个文件）与 `.gitignore` 新增 `/logs/`、`/app/logs/` 之后的树。
`d2eec4f` 起的所有后续提交只动 `app/specs/**`（本文件与 PR 正文正本），
`git diff --stat 1000632..HEAD` 只列这两份文档，不改任何门禁判据；因此上表两段读数都适用于最终 head。
（第二段本来就是在 `d2eec4f` 上跑的，用来证明这一点不是推断。）

与上一轮（`70fcb6d` 里那两行）的关系：**计数完全相同**（1210 passed / 11 skipped / 340 / 128 / 322），
这不是「凑出来的同值」，而是必然——清掉的是 37 个**未被任何代码/测试引用**的运行期日志文件，删它们
不改变用例集合、源文件数或格式化面。真正不自洽的是上一轮：那个 commit 声称只回填两行数字，实际却把
37 个 `app/logs/2026-10-25_15-*.log` 一并带进了提交树（`main` 上该目录与 `.log` 文件数都是 0）。
本轮用追加 commit 修正这一点，并堵住产生源：`.gitignore` 现在遮蔽 `app/logs/` 与仓库根 `logs/`，
在 `app/` 下重跑 `pytest` 后 `git status --porcelain` 不再出现该目录（见 §5.3）。

### 5.3 运行期日志不再进工作区（产生源的验证）

```sh
git ls-tree -r --name-only HEAD -- app/logs | wc -l        # 0（main 上也是 0）
cd app && ../.venv/bin/python -m pytest tests/test_runtime_memory.py
ls -1 app/logs | wc -l                                     # 1 —— logger 确实又写了
git status --porcelain                                     # 不含 app/logs/
git status --porcelain --ignored | grep '^!!' | grep logs   # !! app/logs/
```

`tests/test_runtime_memory.py` 是复现产生源最短的一条路径（ReMe 的 `application.py` 一被构造就
`log_dir="logs"` 落盘）；`tests/test_skill_import.py` 走不到那条路径，跑它不会生成日志。全量门禁跑完
后盘上有 75 个 `.log`（此后每再跑一次 pytest 就继续累加，本轮收尾时为 112 个——这个数只随跑测试的
次数增长，与 git 无关），而 `git status --porcelain` 全程只有本文件的改动。这就是「堵住产生源」的读数。

`.gitignore` 的两条规则都是**锚定根**写法（`/logs/` 与 `/app/logs/`），已用 `git check-ignore -v`
反向核对：只遮蔽这两个目录，`app/specs/`、`docs/`、`app/alibot/` 都不受影响。

## 6. 双轴 code-review（固定点 `5f7dd9c`，两个并行子代理）

### 6.1 Standards 轴

**结论：** 3 条 hard + 3 条判断题，全部处置。轴内最严重：`skills.py` 的模块文档串仍写「只接受本机目录
或上传 zip」，与同一文件新增的三来源段落自相矛盾。

| # | Finding | 严重度 | 处置 |
| --- | --- | --- | --- |
| 1 | `skills.py:12` 文档串自相矛盾（旧句「只接受本机目录或上传 zip」+ 新段「第三个来源」） | hard | **修**：合并成一句「安装接受本机目录、上传 zip，或从 `~/.agents/skills/` 导入」，导入段保留为独立说明 |
| 2 | ADR 0036 标题 / 正文说「两个来源」，§一 的表却列三行，`CONTEXT.md` 与 README 又写「三个来源」 | hard | **修**：标题与首段改为「两个**取字节的路**（人自己准备 / 外部 CLI 装完再导入）」，正文显式写明「按字节来源数是三个，按写路径只有一条」，§一 标题改「三个字节来源共用一条写路径」 |
| 3 | `installed` 用**目录名**判定，而安装器落库用的是 front matter 的 `name`：目录名 ≠ 清单名时，装好后候选仍显示可导入，再导入必然 409 | P2 | **修**：`installed` 改按 `_front_matter` 返回的 `manifest_name` 判定（读不到清单时保持目录名判定）；新增用例 `test_installed_follows_the_manifest_name_not_the_directory_name` |
| 4 | `skillImportResults` 存**渲染好的文案**（`text: t(...)`），兄弟实现 `copy.results` 存数据、渲染时取词条；切语言后旧行不换说法（Divergent Change） | 判断 | **修**：结果行改存 `{name, status, reason}`，新增 `skillImportResultText()` 在渲染时取词条（与 `copyResultText()` 同口径） |
| 5 | `_import_reason()` 是 `render(...)` 的单调用包装（Middle Man） | 判断 | **修**：删掉，两处直接 `render(error.key, locale, **error.params)` |
| 6 | 验收文档写「`skill.import*` 21 条」，实际每份语言 17 条 | 判断 | **修**：改成 17 条（两份语言共 34 行） |

**核过且干净：** 候选扫描确实复用 `SKILL_NAME_PATTERN` / `_manifest_root` / `_front_matter` /
`_skill_files`（没有第二套内核）且严格只读；只用 `expanduser("~")`，无硬编码主目录、无
`global_home_root`；新增代码无中文字面量；API 复用 `resolve_install_root` + `validate_skill_scope`，
路由顺序无冲突、无写入；`i18n.py` 新键两份语言同键集且参数位一致；无新增 `addEventListener`，状态都在
`capabilitiesView` / `emptyCapabilitiesView`；409 → skipped 的三元与 `submitCapabilityCopy()` 属可接受的
并行形状（不是重复实现）；两个测试文件都沿用既有接缝。

### 6.2 Spec 轴

**结论：** 3 条 missing/partial（其中 2 条是交付流程未完成）+ 3 条 scope + 2 条 wrong，逐条处置。

| # | Finding | 类别 | 处置 |
| --- | --- | --- | --- |
| 1 | 验收文档 §5.1 / §6 只有占位（「读数回填见下」） | missing | **修**：本文件 §5 与本节即回填内容 |
| 2 | 未推送、未建 PR、issue #277 无 Answer 评论、`git status` 不干净（含 `app/logs/`） | missing | **修**：清掉 `app/logs/`（ReMe 运行时日志，不在 .gitignore 里，属测试运行副产物）、提交、推送、建 PR、发 Answer；读数见 PR 正文与 §8 |
| 3 | 票面写 `data-skill-import-status`，代码只给 `data-od-id="skill-import-status"` | missing | **修**：结果容器补 `data-skill-import-status="1"`（保留 `data-od-id` 供视觉断言），UI 用例改为按 `skillImportStatus` dataset 定位 |
| 4 | `installed` 按目录名判定（同 Standards #3） | wrong | **修**：见 Standards #3 |
| 5 | 包了一层单目录的候选 description 丢失：`_load_manifest_markdown(Path(resolved))` 只看候选根，而 `_manifest_root` 接受 wrapper 形态 | wrong | **修**：先 `_manifest_root()` 拿到清单根，再从**清单根**读 description；新增用例 `test_wrapper_directory_candidate_keeps_its_description` |
| 6 | `skill.importPickRequired` 守卫 | scope creep | **保留**：票面要求「候选为空 / 读失败只落在本区块」，但一个都未勾选时按钮本就该禁用——这条守卫兜住「视图状态被外部改成空勾选」的边界；已写成 UI 用例（按钮 `disabled` + 不发请求），不是新语义 |
| 7 | `skill.importSummary` toast | scope creep | **保留**：逐条结果已在 `data-skill-import-status` 里，toast 只是一句汇总（与 `agentCapabilities.copySummary` 同款）；不改变任何写路径 |
| 8 | `data-skill-import-candidate` 行 dataset | scope creep | **保留**：测试与取证都靠它定位「哪一行是哪条候选」；与既有 `data-skill` / `data-mcp-server` 行标记同款 |

**核过且符合票面：** 六个候选字段与默认值（`reason:null` / `description:""`）；`{source, available,
candidates}` 形状；channel → 400 经 `resolve_install_root`；`POST /api/skills` 未改（api.py 该段 27 行
插入、0 删除）；导入区块在 `skillInstallForm` **之后**；命令走 `<code>` 且两份词条表都不含它；默认勾选
= `importable && !installed`；已安装 / 不可导入禁用；409 → 跳过、其余 → 原因；结束后 `loadAgentSkills()`；
8 个状态字段都在 `emptyCapabilitiesView`；忙碌提示 `is-faint`；按钮来自 `resourceButton`；委派在既有
`#adBody` 链；7 条 pytest 判据全覆盖且只读 `HOME`；ADR 0036 有 Context/Decision/Consequences 与
0026/0031 的逐条关系；CONTEXT `_Avoid_` 保留、历史段未动；README 三处齐；无 schema / 迁移 / subprocess /
第二套校验器。

## 7. 约束自检

- 未改 `SCHEMA_VERSION`、未新增迁移、未动 `app/tests/_migration_history.py`
- 无 subprocess / `asyncio.create_subprocess_*` / 新网络请求；未引入 npm / Node 运行时依赖
- 未复制第二套校验实现：候选扫描直接调 `SKILL_NAME_PATTERN` / `_manifest_root` / `_front_matter` / `_skill_files`
- `POST` / `PATCH` / `DELETE /api/skills` 与 `copy` 端点语义未改；装配两层顺序未改
- `migrate-skills` 与 `~/.alibot/skills` 公共库写入路径未动
- 未跑 `ruff format`；未新增前端测试框架；未 import 私有名做行为断言

## 8. 交付

- 分支：`issue-277-skill-import`；PR：https://github.com/whutlichao/alibot/pull/278（OPEN）
- 验收文档：本文件（`app/specs/issue-277-acceptance.md`）
- PR 正文正本：`app/specs/issue-277-pr-body.md`

## 9. 打回补齐（同票 follow-up 轮）

上一轮 Dispatch 结算后，coordinator 复核发现一处与提交树不自洽的事实：`70fcb6d` 除两行数字外，
把 37 个 `app/logs/2026-10-25_15-*.log` 一并提交了（`main` 上该目录与 `.log` 文件数都是 0）。
本轮只做三件事，**未动任何功能代码 / 测试 / ADR / CONTEXT 词条**。

| # | 动作 | 读数 |
| --- | --- | --- |
| 1 | `git rm -r --cached app/logs` + 删盘，追加 commit `1000632`（不 rebase / 不 amend / 不 force-push） | `git ls-tree -r --name-only HEAD -- app/logs \| wc -l` → `0` |
| 2 | `.gitignore` 补 `/logs/` 与 `/app/logs/`（锚定根，紧挨「本机数据目录候选 / 工作区临时目录」两条，附成因注释） | `git check-ignore -v` 只命中这两个目录；`app/specs/`、`docs/`、`app/alibot/` 不命中 |
| 3 | 清理后的树（`1000632`）上重跑全量 `scripts/check.sh`，把真实读数回填 §5.2 并写明 commit SHA | `1210 passed, 11 skipped in 695.02s`；`门禁全绿` |

产生源的验证见 §5.3：全量门禁跑完后盘上有 75 个 `.log`，`git status --porcelain` 不含 `app/logs/`。

数字与上一轮完全相同的原因写在 §5.2：删掉的是 37 个没有任何代码/测试引用的运行期日志，用例集合、
源文件数与格式化面都不变；**不自洽的是上一轮那个 commit 的内容，不是读数本身**。
