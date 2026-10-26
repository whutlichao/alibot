# 验收取证：#283 skills 右栏详情 + `GET /api/skills/{id}/markdown` 只读端点

**票型**：实施票（前端右栏 + 一条只读后端端点 + 回归测试）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：[#283](https://github.com/whutlichao/alibot/issues/283)（验收正本 = 票面 AC）
**父规格**：`app/specs/agent-detail-tabs-spec.md`（D5 / D9 / T1–T5 / N1）
**设计正本**：`app/specs/agent-detail-tabs-design-notes.md`（D5 含守卫参数与响应契约表、D9）
**base**：`f5cc826`（= 派活时 `agent-detail-tabs-integration` tip）｜**branch**：`issue-283-impl`
**改动面**：`app/alibot/api.py`（+1 端点，只读）、`app/alibot/skills.py`（+1 个纯函数，既有函数行为不变）、
`app/alibot/studio/{app.js,app.css,i18n.js}`、`app/tests/test_skill_markdown.py`（新）、
`app/tests/{agent_capabilities_ui,agent_detail_ui}.test.cjs`、本文件。
**没动的面**：`db.py` / `service.py` / `mcp.py` / 迁移链 / `SCHEMA_VERSION` / 装配层 / 优先级 / 消息面 / 预算与唤醒纪律；
`studio/index.html` 未改（复用 #280 的 `data-od-id="agent-skill-detail"` 插槽）。

**正文口径（一句话判据）**：`markdown` = **front matter 之后的正文字节**，与装配层读进
`Skill.markdown` 的同一份逐字相等（规格 US22「front matter 之后的指令块仍然可读」；不是「整份文件原文」）。

---

## 1. 票面 AC → 落点 → 读数

### AC1 · 点左栏一行 → 右栏显示五项元数据 + SKILL.md 正文，正文与磁盘逐字相等

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 右栏五格元数据（名称 / 描述 / 来源路径 / 内容 hash / 启停态） | `app.js` `skillDetailNode()` → `skillDetailRow()`；`data-od-id` 分别是 `skill-detail-{name,description,source,hash,state}` | 用例「skills 右栏：默认选中第一行，五项元数据 + 正文与磁盘 SKILL.md 逐字相等」：五格 `textContent` 逐格 `assert.equal`（hash 是**完整值**，不是列表里那个 12 位前缀） |
| 正文与磁盘 `SKILL.md` 逐字相等 | `skills.py:read_skill_markdown()` → `api.py:get_skill_markdown()`；前端 `skillDetailBody()` | ① 后端用例 `test_ok_returns_the_body_after_the_front_matter`：`payload["markdown"] == BODY`（`BODY` 是 `SKILL.md` 里 front matter 之后那段字节的独立字面量）且**显式**断言 `"description:" not in markdown`、正文不以 `---` 围栏开头；② 又与装配同源：`load_skills(...)` 读出的 `Skill.markdown` 与端点返回**逐字相等**；③ 前端用例：`skill-detail-markdown.textContent === SKILL_BODY`（替身与断言用同一份字面量，逐字比对） |
| 正文不带入 front matter | 同上 | 后端那条用例的两条反向断言 |
| 正文按需读（不塞进列表响应） | `app.js` `loadSkillMarkdown()`：选中项变了才发 `GET /api/skills/{id}/markdown` | 用例「点左栏另一行切换选中项，正文按需重读」：`/markdown$` 的请求序列 `deepEqual(["/api/skills/sk_1/markdown", "/api/skills/sk_2/markdown"])`（不预读整列）；列表响应 `GET /api/skills` 的形状未含正文（后端三处代码可指认） |

### AC2 · 切到 skills 页签默认选中第一行；删掉当前选中项回落第一行；左栏为空给空态

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 默认选中第一行 | `state.agentDetailSkillId` + `syncAgentDetailSkill(view)`（在 `agentSkillsTabNodes()` 里先对齐再铺） | 用例：进入页签后 `state.agentDetailSkillId === 'sk_1'`、`sk_1` 行 `aria-current="true"`、`sk_2` 行为 `false` |
| 选中项有可读的选中态 | `skillRow(skill, current)`：`is-active`（`--selected` 族）+ `aria-current` | 同上；`app.css` `.skill-row.is-active { border-left-color: var(--selected-graphic); background: var(--selected-soft); }` |
| 删掉当前选中项 ⇒ 回落第一行 | `deleteSkill()` → `loadAgentSkills()` → `syncAgentDetailSkill()` | 用例「启停与删除作用于当前选中项…」末段：删掉 `sk_2` 后 `agentDetailSkillId === 'sk_1'`、右栏名称格回到 `review` |
| 左栏为空 ⇒ 右栏空态 | `skillDetailNode()` 的 `!skill` 分支 → `skill.detailEmpty` | 用例「左栏为空时给空态指引，不读正文」：`agent-skill-detail.textContent === 词条值`，且 `/markdown$` 请求数 **0** |

### AC3 · 右栏的启用 / 停用与删除作用于当前选中项，成功后启停态立刻跟着变

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 动作入口在右栏、指向当前选中项 | `skillDetailNode()` 的 `skillToggle` / `skillDelete`（`dataset.skillToggle` / `skillDelete` = 选中项 id） | 用例：选中 `sk_2` 后 `skillToggle=sk_2` 的文案是 `skill.disable` |
| 启停只打当前选中项 | `toggleSkill()` → `PATCH /api/skills/{id}` | 同用例：`PATCH` 的 url 序列 `deepEqual(["/api/skills/sk_2"])`，body `deepEqual({enabled:false})`；`sk_1` 那一行仍显示「已启用」 |
| 成功后启停态立刻跟着变 | `loadAgentSkills()` 重读后同一次重铺里 `skill-detail-state` 与行徽标一起更新 | 同用例：`skill-detail-state.textContent === skill.disabled`、右栏按钮变 `skill.enable` |
| 删除同样只打当前选中项 | `deleteSkill()` → `DELETE /api/skills/{id}` | 同用例：`DELETE` url 序列 `deepEqual(["/api/skills/sk_2"])` |

### AC4 · 正文按纯文本呈现（换行与缩进保留），不解析 Markdown

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 正文节点是纯文本容器 | `skillDetailBody()`：`el('pre', 'skill-detail-markdown', read.markdown)`（只经 `textContent`） | 用例「正文按纯文本呈现…」：`body.tagName === 'pre'`、`body.textContent === SKILL_BODY` |
| 换行与缩进保留 | `app.css` `.skill-detail-markdown { white-space: pre-wrap; }` | 同用例：`textContent` 匹配 `/^# 提交前自审/`、`/\*\*重点\*\*/`、`/\n {4}先读改动，再读测试/` |
| 不解析成 HTML | 无 Markdown 渲染器、无 `innerHTML` | 同用例：`h1/h2/strong/em/ul/li/code/p` 八种标签在正文子树里 **0 命中**；`stripComments(readStudio("app.js")).includes("innerHTML") === false` |
| 四态各有自己的说明文案 | `skillDetailBody()` 的 `state` 分支 → `skill.detailMissing` / `detailInvalid` / `detailUnreadable`；`data-detail-state` 落 `state` | 用例「ok / missing / invalid / unreadable 各有自己的说明文案」：三条空态行 `data-detail-state` 分别是 `missing` / `invalid` / `unreadable`，`ok` 没有空态行；四段文本 `new Set(texts).size === 4` |

### AC5 · 六种响应情形（404 / 400 / ok / missing / invalid / unreadable），invalid 不含根外内容

实测命令（临时 `HOME` + `TestClient` 黑盒；探针脚本留在 `/tmp`，不进仓库）：

```sh
.venv/bin/python /tmp/six_state_probe.py
```

真实读数（逐条对 D5 契约表）：

```text
disk SKILL.md (raw) = "---\nname: review\ndescription: 代码评审清单\n---\n\n# Review\n\n按步骤评审：\n\n    先读改动，再读测试\n\n- 一\n- 二\n\n"
ok           status=200 state='ok' markdown="# Review\n\n按步骤评审：\n\n    先读改动，再读测试\n\n- 一\n- 二"
missing      status=200 state='missing' markdown=null
invalid      status=200 state='invalid' markdown=null outside_in_response=False
unreadable   status=200 state='unreadable' markdown=null
404          status=404 error={"code": "not_found", "message": "Skill 不存在：sk_missing"}
400          status=400 error={"code": "validation_failed", "message": "Skill 只能安装在成员作用域（scope_kind=agent），频道级已弃用", "fields": {"scope_kind": "Skill 只能安装在成员作用域（scope_kind=agent），频道级已弃用"}}
```

同一批读数在**测试里**也有对应断言（`app/tests/test_skill_markdown.py`，8 passed）：

| 情形 | 用例 | 关键断言 |
| --- | --- | --- |
| `ok` | `test_ok_returns_the_body_after_the_front_matter` | `state == "ok"`；`markdown == BODY`；元数据 `payload["skill"] == listed[0]`；与装配同源 |
| `missing`（目录或 SKILL.md 不在） | `test_missing_bytes_keep_the_metadata_and_null_body` | 删 `SKILL.md` ⇒ `missing`；再整目录搬走 ⇒ 仍 `missing`；元数据 `name` / `content_hash` 照常 |
| `invalid`（根外 symlink 目标） | `test_paths_outside_the_global_root_are_never_returned[manifest_symlink]` | `state == "invalid"`、`markdown is None`、`"SECRET-OUTSIDE-BODY" not in response.text` |
| `invalid`（根外 symlink 祖先） | `test_paths_outside_the_global_root_are_never_returned[skills_dir_symlink]` | 同上 |
| `unreadable`（非 UTF-8） | `test_unreadable_bytes_are_their_own_state` 前半 | `state == "unreadable"`、`markdown is None`、元数据照常 |
| `unreadable`（权限 000） | 同用例后半（`os.geteuid() == 0` 时 skip） | 同上 |
| `404` | `test_unknown_skill_is_a_readable_404` | `code == "not_found"`、message 含 `sk_missing` |
| `400` | `test_channel_scoped_legacy_rows_are_rejected` | `code == "validation_failed"`、message 含 `agent`、响应里没有 `markdown` 键 |

**守卫**：`skills.py:read_skill_markdown()` 用 `resolve_global_read_path(global_root, AGENTS_DIR,
safe_partition_id(member_id), SKILLS_DIR, name, SKILL_MANIFEST)`——与技能装配同族的参数；守卫返回
`None` 时对**同一份词法路径**用 `os.path.lexists` 区分「escape」与「absent」（与 `api.py:copy_skill()`
同一写法），**不**退化成直读。

### AC6 · 端点只读：不写盘、不落消息、不改预算

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 不落消息 / 不产生轮次 / 不改预算 | 端点只调 `database.get_skill()`（读）与 `read_skill_markdown()`（`Path.read_text`） | 用例 `test_the_endpoint_is_read_only`：`messages` / `outcomes` / `budget` 调用前后 `assert ==` |
| 不写盘（字节与 mtime 都不变） | 同左（无 `write_text` / 无 `mkdir` / 无 `unlink`） | 同用例：`_tree(home)` 快照（相对路径 → 字节 + `st_mtime_ns`）前后 `assert ==`；成员家目录条目清单也不变 |
| 不 bump `SCHEMA_VERSION` / 不写迁移 | `app/alibot/db.py` 未改 | `git diff f5cc826 -- app/alibot/db.py` 为空 |

### AC7 · 新增文案进两份前端词条 catalog；后端错误文案按既有两层走

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 新增 13 个键在两份 catalog 里同键同参数位 | `studio/i18n.js`（zh-CN 与 en）：`skill.detailTitle` / `detailName` / `detailDescription` / `detailSource` / `detailHash` / `detailState` / `detailEmpty` / `detailBodyTitle` / `detailBodyLoading` / `detailBodyFailed`（`{reason}`）/ `detailMissing` / `detailInvalid` / `detailUnreadable` | 用例「新增文案在两份词条里同键同参数位…」把这 13 个键放进「必须有值」清单；`i18n_ui.test.cjs` 的「parameter placeholders match between the two catalogs」与「every string-catalog key used by app.js exists in both catalogs」两条守卫全绿 |
| 后端错误文案复用既有 `i18n` render | `api.py:get_skill_markdown()` 用 `_render_copy("error.skill_not_found", skill_id=...)` 与 `_render_copy("error.skill_scope_agent_only")` | 400 / 404 两条用例断言中文正文；两份 `alibot/i18n.py` catalog 一字未改（既有键未新增、未改值） |
| 占位键随消费点消失 | `agentDetail.skillsDetailPlaceholder` 的唯一消费点（右栏占位）被本票的详情取代 ⇒ 两份 catalog 一并删 | `grep -rn "skillsDetailPlaceholder" app/alibot/` → **0 命中**（退出码 1）；`agent_detail_ui.test.cjs` 的「必须有值」清单里已移除该项 |

---

## 2. D9 断言改写与偏差登记

### 2.1 与 D9 相邻、本票必改（判据：断言与新行为正相反 ⇒ 必须改，且不许删原意）

| # | 用例 / 位置 | 原断言 | 为何要改 | 改法（原意如何保留） |
| --- | --- | --- | --- | --- |
| A | `agent_capabilities_ui.test.cjs`「skills 页签：左栏技能列表与技能行入口」 | `byDataset(panel,"skillToggle","sk_1")` / `skillDelete` 断言「**技能行**要有启停 / 删除入口」 | 票面 AC3 把这两个动作挪到**右栏**并作用于当前选中项；行上再挂一份就是两个作用对象（歧义）。这是「断言与新行为正相反」 | 断言改指右栏：`byDataset(panel,"skillToggle","sk_1")` 仍命中，但文案改成「右栏的启停入口指向当前选中项」。**原意保留**：「skills 页签里存在且只存在一组作用于当前技能的启停 / 删除入口」 |
| B | `agent_detail_ui.test.cjs`「skills 页签：左栏技能列表 + 右栏占位」 | `detail.textContent.includes(词条 agentDetail.skillsDetailPlaceholder)`，标题写「右栏占位」 | 本票兑现 D5，占位说明被真实详情取代 ⇒ 该断言与词条的正向断言必然红 | 改成断言右栏含 `skill.detailName`（元数据标签）且 `skill-detail-markdown` 节点存在；用例标题改为「右栏选中技能详情」。**原意保留**：「右栏是选中技能的详情落点，不是空壳」 |
| C | `agent_detail_ui.test.cjs`「本票新增 / 沿用的文案在两份词条里都取到值」 | `agentDetail.skillsDetailPlaceholder` 在「必须有值」清单 | 同 B：词条随占位节点一起失去消费点（与 #280/#281/#282 已确立的「随消费点消失而删键」同一规则） | 从「必须有值」清单删掉该项，键本身在两份 catalog 删除。**没有新增反向断言**：`i18n_ui.test.cjs` 的「两份 catalog 同键集」守卫已经保证它不会单边回来，而 0 命中断言写在 §1 AC7 的 `grep` 读数里（避免为一条已删键再占一处测试断言） |
| D | `agent_capabilities_ui.test.cjs` 文件头注释 | 1–6 条里没有右栏详情的口径 | 注释会与断言互相打脸（`#282` 已开过同一条先例） | 新增第 3b 条描述右栏详情。**原意保留**：它描述的就是本文件断言的范围 |

**没有第 5 处**：对两个测试文件的全文扫描（`grep -nE "skillToggle|skillDelete|agent-skill-detail|skillsDetailPlaceholder|skill-list|skill-detail"`）逐条判定后，余下命中要么落在本票新写的用例里，要么是 `channel_resources_ui.test.cjs` 的**频道配置**面 0 命中断言（本票未碰频道配置，照常绿）。

### 2.2 需要记一笔的取法（不是偏差，是判据来源）

- **正文 = front matter 之后**：这是 coordinator 在开工前的裁定（A 方案），理由链写在 §1 的第 0 段。
  特意**没有**复用 `skills.py:_load_manifest_markdown()`：它在 front matter 缺 `name` / `description`
  时返回 `None`——那是「装配不可用」的判定，不是「正文读不出来」；拿它当正文内核会把「front matter
  字段不全但文件可读」误报成 `unreadable`。新函数 `read_skill_markdown()` 只用 `frontmatter.loads(text)`
  取 `parsed.content`，把「字节读不出来（权限 / 非 UTF-8）」与「front matter 形状不合法」分开：
  前者是 `unreadable`，后者（解析抛错 ⇒ 正文边界不可靠）同样归 `unreadable`。
- **`app/alibot/skills.py` 只增一个导出纯函数**（`read_skill_markdown`）与四个状态常量；既有函数
  （`_load_manifest_markdown` / `load_skills` / `_global_skill_layer` / 安装器）行为一字未改，
  `test_skill_assembly.py` / `test_global_skills.py` / `test_skills.py` / `test_skill_copy.py` /
  `test_skill_import.py` 全绿。

---

## 3. 红 → 绿证据

### 3.1 前端（隔离基线副本，只换测试不换产品代码）

```sh
scripts/red-evidence.sh f5cc826 app/tests/agent_capabilities_ui.test.cjs
# 基线：f5cc826；基线 app.js md5：c23d4a0477633eda0b0d135530452419
# 读数：22 tests / 14 pass / 8 fail（证据目录 /var/folders/.../T//alibot-red.0JdQha；前一次同口径运行的目录下读数相同）
```

红的 8 条正是本票要改的点（右栏详情全部 + 一条词条清单）：

```text
✖ skills 右栏：默认选中第一行，五项元数据 + 正文与磁盘 SKILL.md 逐字相等
  AssertionError: 默认选中左栏第一行 (undefined !== 'sk_1')
✖ skills 右栏：点左栏另一行切换选中项，正文按需重读
  AssertionError: 选中态跟着点中那一行 (undefined !== 'sk_2')
✖ skills 右栏：启停与删除作用于当前选中项，启停成功后右栏启停态立刻跟着变
  TypeError: Cannot read properties of null (reading 'textContent')
✖ skills 右栏：左栏行可键盘选中（Enter / Space），选中态带 aria-current
  AssertionError: 行可被 Tab 抵达 (null !== '0')
✖ skills 右栏：左栏为空时给空态指引，不读正文
  AssertionError: zh-CN 缺少词条：skill.detailEmpty
✖ skills 右栏：正文按纯文本呈现，不把 Markdown 解析成 HTML
  AssertionError: 正文节点在右栏 (null !== true)
✖ skills 右栏：ok / missing / invalid / unreadable 各有自己的说明文案
  TypeError: Cannot read properties of null (reading 'textContent')
✖ 新增文案在两份词条里同键同参数位，随入口撤除的键必须消失
  AssertionError: zh-CN 缺少词条：skill.detailTitle
```

```sh
scripts/red-evidence.sh f5cc826 app/tests/agent_detail_ui.test.cjs
# 基线 app.js md5：c23d4a0477633eda0b0d135530452419
# 读数：58 tests / 57 pass / 1 fail（证据目录 /var/folders/.../T//alibot-red.jo20gY）
✖ skills 页签：左栏技能列表 + 右栏选中技能详情，启停 / 删除跟着列表走
  AssertionError: 右栏给选中技能的元数据
```

### 3.2 后端（隔离基线副本上跑同一份新测试）

```sh
EV=$(mktemp -d "${TMPDIR:-/tmp}/alibot-pyred.XXXXXX")
git archive f5cc826 app/alibot app/tests app/pyproject.toml | tar -x -C "$EV"
cp app/tests/test_skill_markdown.py "$EV/app/tests/"
cd "$EV/app" && <repo>/.venv/bin/python -m pytest tests/test_skill_markdown.py
# 证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-pyred.lLWPxM
# 读数：8 failed in 0.59s
```

八条全红，每条都咬在「端点还不存在 ⇒ 404」上：

```text
FAILED tests/test_skill_markdown.py::test_ok_returns_the_body_after_the_front_matter
  AssertionError: assert 404 == 200
FAILED tests/test_skill_markdown.py::test_missing_bytes_keep_the_metadata_and_null_body
FAILED tests/test_skill_markdown.py::test_paths_outside_the_global_root_are_never_returned[manifest_symlink]
FAILED tests/test_skill_markdown.py::test_paths_outside_the_global_root_are_never_returned[skills_dir_symlink]
FAILED tests/test_skill_markdown.py::test_unreadable_bytes_are_their_own_state
FAILED tests/test_skill_markdown.py::test_unknown_skill_is_a_readable_404
FAILED tests/test_skill_markdown.py::test_channel_scoped_legacy_rows_are_rejected
FAILED tests/test_skill_markdown.py::test_the_endpoint_is_read_only
```

### 3.3 实现之后（工作区）

```sh
cd app && ../.venv/bin/python -m pytest tests/test_skill_markdown.py
# → 8 passed in 1.21s

node --test app/tests/agent_capabilities_ui.test.cjs
# → tests 22 / pass 22 / fail 0

node --test app/tests/agent_detail_ui.test.cjs
# → tests 58 / pass 58 / fail 0

node --test app/tests/*.test.cjs
# → tests 335 / pass 335 / fail 0（基线 328 + 本票新增 7 条）
```

---

## 4. 门禁读数（`scripts/check.sh` 全量，动了 `.py` ⇒ 全量一次）

```sh
scripts/check.sh
```

| 步骤 | 读数 |
| --- | --- |
| pytest（全量） | `1218 passed, 11 skipped in 686.60s (0:11:26)`（基线 1210 + 本票新增 8 条） |
| ruff check | `All checks passed!` |
| ruff format --check | 首次红（`test_skill_markdown.py` 两处会重排）⇒ 手工按 format 口径改写两处 ⇒ `347 files already formatted`（**未跑** `ruff format`） |
| mypy（仓库根） | `Success: no issues found in 129 source files`（128 + 新测试文件） |
| pyright | `0 errors, 0 warnings, 0 informations` |
| node --test（glob） | `tests 335 / pass 335 / fail 0` |
| 结论 | **门禁全绿** |

> 顺序说明：全量那一次跑在 `ruff format --check` 修好之前，那一次的唯一红项就是格式；修完只重跑
> `scripts/check.sh --fast`（ruff / format / mypy / pyright / node 全部重跑，读数见上表），pytest 没有
> 第二份代码版本可跑（改动只落在测试文件的两处分行上，且该文件随后被 `pytest tests/test_skill_markdown.py`
> 单独复跑为 8 passed）。

---

## 5. 零命中 / 对照项（可直接复跑）

```sh
# ① 占位键随消费点消失
grep -rn "skillsDetailPlaceholder" app/alibot/          # → 无输出，退出码 1

# ② 正文只经 textContent 写：不引入 HTML 注入面
grep -n "innerHTML" app/alibot/studio/app.js             # → 只有模块头那句「不用 innerHTML」的注释

# ③ 对照项：端点与守卫仍在
grep -n 'get("/api/skills/{skill_id}/markdown"' app/alibot/api.py     # → 命中
grep -n "def read_skill_markdown" app/alibot/skills.py                # → 命中
grep -n "resolve_global_read_path" app/alibot/skills.py | head -3     # → 守卫调用点（含新函数）

# ④ 未改动的面
git diff f5cc826 -- app/alibot/db.py app/alibot/service.py app/alibot/mcp.py   # → 空
git diff f5cc826 -- app/tests/test_skill_copy.py app/tests/test_skill_import.py app/tests/test_skills.py  # → 空
```

---

## 6. 偏差与需要人定的问题

1. **正文口径（已裁定）**：`markdown` = front matter 之后的字节，与装配同源（§1 第 0 段的一句话判据）。
2. **D9 之外必改 4 处**（§2.1 的 A–D），全部按「断言与新行为正相反 ⇒ 必须改、不许删原意」处置，
   没有一处是「删断言让测试变绿」。
3. **`skill.detail*` 的 13 个键是新增**（两份 catalog 同键同参数位）；`agentDetail.skillsDetailPlaceholder`
   是本票唯一删除的键。
4. **`app/README.md` 未改**：本票边界不含它（D9 末段的文档同步面仍挂在骨架票/后续票）。它的
   「Agent 详情、配置编辑与软删除」一节当时仍写「所有区块在同一页纵向铺开……没有页签」——那是 #280 已登记的
   既存偏差，不是本票新引入的。**（review-fix 已补）** 该节的三分页签口径与
   `GET /api/skills/{id}/markdown` 端点行由 `agent-detail-tabs-review-fix` 补上，见
   `app/specs/agent-detail-tabs-review-fix-acceptance.md` 的 F1。
5. **键鼠可达性**：左栏技能行是 `div role="button" tabindex="0"`（与看板卡片同形），Enter / Space 由
   `#adBody` 的 `keydown` 委派补上；动作按钮上的 Enter 不换选中项（有用例咬这条）。
