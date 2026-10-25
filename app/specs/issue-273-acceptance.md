# #273 验收：能力配置收敛到 Agent 详情（MCP / 技能 + 复制到其他成员 + 名册摘要）

- 票：https://github.com/whutlichao/alibot/issues/273
- 父规格 #269；集成分支 `capabilities-agent-scope`，本票分支 `issue-273-impl`
- 起点：`d88d912`（集成 tip，已含 #270 / #271 / #272）
- 门禁：`scripts/check.sh --studio`、`scripts/check.sh --fast tests/test_skills.py tests/test_skill_assembly.py`

## 1. 判据 → 落点

| 票面判据 | 落点 |
| --- | --- |
| Agent 详情新增能力段，两 kind 同屏、同一行组件 | `app.js`：`agentCapabilitiesSection()` 进入 `agentDetailNodes()`（统计 → 系统提示 → 配置 → **能力** → 全局 MCP → 危险操作）；行组件复用 `mcpRow()` / `skillRow()`，表单复用 `mcpForm()`，安装复用抽出来的 `skillInstallForm()` |
| 作用域恒为该成员，没有 scope 选择器 | `capabilitiesView(memberId)` + 所有请求固定 `scope_kind=agent&scope_id=<member>`；表单里没有 scope 控件 |
| MCP 行补齐编辑入口 | `mcpRow()` 的 `data-mcp-edit` → `beginMcpEdit()` → `mcpForm()`（字段 / 校验 / `env_names` 只收名字，形状与 #156 一致） |
| 技能行补齐安装入口（本机目录 + zip） | `skillInstallForm()`：`data-skill-install-dir`（JSON 正文）、`data-skill-zip`（原始 zip 字节 + query） |
| 顶部影响范围一句话 | `agentCapabilities.scopeNote`（`~/.alibot/agents/{member}/skills/`） |
| 每行「复制到…」+ 多选成员 | `openCapabilityCopy()` / `capabilityCopyNode()` / `toggleCapabilityCopyTarget()`；目标清单 = `state.agents` 排除当前成员 |
| 复制幂等：同名 → 逐条「已跳过」、不覆盖 | MCP 用既有 `POST /api/mcp-servers`（同 scope 同名 409）；技能用新 `POST /api/skills/{id}/copy`（409）；`submitCapabilityCopy()` 按 `err.status === 409` 记 `skipped`，逐条渲染（`capability-copy-results`） |
| 频道配置删掉 MCP / 技能两段 | `renderChannelConfigModalContent()` 只留 `workspaceManagementSection()`；`state.resources` / `emptyResourcesView()` / `loadChannelResources()` / `loadMcpServers()` / `loadSkills()` / `mcpSection()` / `skillSection()` 全部删除；`#channelConfigBody` 上不再有这些动作的委派 |
| 频道级旧行不展示、不做「已弃用」提示 | 频道配置不读 `scope_kind=channel`；旧行留在库里（本票不动 schema、不迁移） |
| 名册每行 `MCP n / 技能 n` | `rosterCapabilitiesNode()` + `refreshRosterCapabilities()` / `readMemberCapabilityCounts()`（`data-od-id="roster-capabilities-<member>"`） |
| 单成员读取失败只占位、不阻塞名册 | `readMemberCapabilityCounts()` 捕获后返回 `failed`，只改那一行的文字（`roster.capabilitiesUnavailable`） |
| 文案走 `i18n.js` 双语 | 新增 `agentCapabilities.*`（17 条）与 `roster.capabilities*`（3 条）；两份词条同键集、占位参数一致 |
| 服务端复制端点 | `api.py`：`SkillCopy` 模型 + `POST /api/skills/{id}/copy`（源路径经全局只读守卫 `resolve_global_read_path` 解析，根外 symlink 一律 400；目标成员校验在前；复制给自己 400；走既有 `install_directory` 重校验；落库失败回收目标副本） |

## 2. 先红后绿

### 2.1 红（隔离基线 `d88d912`）

```sh
scripts/red-evidence.sh d88d912 app/tests/agent_capabilities_ui.test.cjs
# 基线：d88d912 (d88d912)
# 基线 app.js md5：d408c613a88d99e68c2d55c00d4ecf96
# 证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.5ZqCpc
# ℹ tests 9
# ℹ pass 1
# ℹ fail 8
# 结果：用例失败（红读数）
```

9 条里唯一的绿是**反向断言**「能力段里没有 scope 选择器」——基线上整段都不存在，它自然成立；
其余 8 条（能力段、编辑表单、安装、复制逐条结果、频道段已删、名册摘要、词条）在基线上全红。

### 2.2 绿

```sh
node --test app/tests/agent_capabilities_ui.test.cjs
# ℹ tests 9 / ℹ pass 9 / ℹ fail 0

node --test app/tests/*.test.cjs
# ℹ tests 317 / ℹ pass 317 / ℹ fail 0
```

## 3. 门禁读数

```sh
scripts/check.sh --fast tests/test_skill_copy.py
# 8 passed in 4.38s
# == ruff check            All checks passed!
# == ruff format --check   通过（不跑 ruff format）
# == mypy / pyright        通过
# ℹ tests 317 / ℹ pass 317 / ℹ fail 0（node glob）
# 门禁全绿

scripts/check.sh --fast tests/test_skills.py tests/test_skill_assembly.py
# （ruff check / ruff format --check / mypy / pyright / node glob 全过）
# ℹ tests 317 / ℹ pass 317 / ℹ fail 0
# 门禁全绿

scripts/check.sh --studio
# ℹ tests 317 / ℹ pass 317 / ℹ fail 0
# 门禁全绿
```

同一轮的 pytest 读数（`--fast` 里补跑的那两个文件，`app/` 下执行）：

```sh
.venv/bin/python -m pytest tests/test_skills.py tests/test_skill_assembly.py
# 13 passed in 4.72s   （test_skills 9 + test_skill_assembly 4）
```

`app/alibot/api.py` 的独立复核：

```sh
.venv/bin/python -m ruff check app/alibot/api.py   # All checks passed!
.venv/bin/python -m mypy app/alibot/api.py         # Success: no issues found in 1 source file
```

## 4. 复制端点：pytest 覆盖（`app/tests/test_skill_copy.py`）

```sh
.venv/bin/python -m pytest tests/test_skill_copy.py     # 在 app/ 下
# 8 passed in 0.53s
```

| 用例 | 判据 |
| --- | --- |
| `test_copy_writes_the_same_bytes_and_inherits_enabled` | 201；目标家目录整棵树与源**逐字节相同**；`content_hash` 与源一致；`enabled=False` 继承；源行 / 源字节不变 |
| `test_copy_to_a_member_that_already_has_the_name_conflicts_without_overwriting` | 409 `skill_conflict`；目标字节与目标行 hash 都保持原样（目标自己的版本没被覆盖）；源字节不变 |
| `test_copy_to_the_same_member_is_rejected_without_a_second_copy` | 400 `validation_failed` + 可读文案；清单一字不多、分区里只有那一个目录 |
| `test_unknown_skill_and_unknown_target_are_readable_errors` | 未知 `skill_id` → 404 `not_found`；未知目标成员 → 400 且正文带那个 id，目标家目录不被创建 |
| `test_channel_scoped_legacy_rows_are_rejected` | 频道级旧行 → 400（含「agent」可读原因）；目标清单为空、无字节（旧行用公开 `open_database` 预置——这条形状没有 HTTP 创建面） |
| `test_extra_fields_in_the_body_are_rejected` | 正文带 `source_path` → 400；目标无行无字节（复制不接受调用方给路径） |
| `test_source_outside_the_global_root_is_never_copied[skills_dir_symlink / skill_dir_symlink]` | `<家目录>/skills` 或 `<家目录>/skills/<name>` 指向根外 → 400「技能路径不安全（… symlink …）」，根外内容不被复制进目标 |

实现要点：

- 源路径经**全局只读守卫**解析，与技能装配同口径：
  `resolve_global_read_path(global_home, AGENTS_DIR, safe_partition_id(source_member), SKILLS_DIR, name)`；
  `None` ⇒ 400（`os.path.lexists` 为真按 `error.skill_path_invalid`，否则按
  `error.skill_source_not_directory`），因此根外 symlink 祖先 / 根外目标在**复制之前**就被拒。
- 复制给自己（`scope_id` == 源成员）显式 400：那不是一次复制，而是把同一份字节写到同一处
  （必然冲突、没有语义）。响应 `code=validation_failed`，正文复用 `error.skill_conflict` 的文案。
- 目标字节由服务端从源成员家目录复制，hash 在复制结果上重算；落库失败回收目标副本。

## 5. 真实浏览器（隔离端口 8793 + 隔离 HOME/数据目录）

启动：

```sh
cd app && HOME=/tmp/alibot273-home ALIBOT_DATA_DIR=/tmp/alibot273-data ../.venv/bin/python -m alibot --port 8793
```

种子：`POST /api/agents` ×2（小明 `mem_b2e5ef52917b` / 小红 `mem_8def24d57b91`）、
`POST /api/channels`（`ch_1465f61c3982`，两名成员都在里面）。技能的源目录：`/tmp/alibot273-skill`
（`SKILL.md` + `references/checklist.md`）。

用 ego-browser 驱动真实 Chromium，逐条读数（全部来自页面 DOM 的 `innerText`）：

1. 打开 `http://127.0.0.1:8793/studio/` → 点「查看 小明 的详情」。能力段原文：
   `能力配置 / 这些能力只属于这个 Agent；技能字节在 ~/.alibot/agents/mem_b2e5ef52917b/skills/（公共库 ~/.alibot/skills/ 只读）。 / MCP 声明 … / 技能 …`（截图 `/tmp/alibot273-detail-before.png`）。
2. 技能安装：`[data-od-id="skill-source"]` 填 `/tmp/alibot273-skill` → 点「安装目录」→ 行出现
   `codereview | 已启用 | 提交前做一次结构化自审 | hash a8c07ee11e70 | 停用 | 删除 | 复制到…`，
   toast `技能已安装：codereview`。
3. 复制：点该行「复制到…」→ 弹层列出 `小红`（没有小明自己）→ 勾选 → 「开始复制」→ 结果
   `小红：已复制`（截图 `/tmp/alibot273-copy.png`）。
4. 再复制一次同一行到小红 → 结果 `小红：已存在同名，已跳过`
   （截图 `/tmp/alibot273-copy-skip.png`）；小红的技能行仍是同一条（hash 未变）。
5. 打开「查看 小红 的详情」→ 技能行出现 `codereview … hash a8c07ee11e70`，作用域一句话是
   `…/agents/mem_8def24d57b91/skills/…`（截图 `/tmp/alibot273-target-detail.png`）。
6. MCP：在小明详情里新增 `filesystem`（stdio / `npx -y @modelcontextprotocol/server-filesystem /tmp` /
   `GITHUB_TOKEN`）→ 行出现；同一行「复制到…」→ 小红 → `小红：已复制`
   （截图 `/tmp/alibot273-mcp-copy.png`）。
7. 名册：频道头「频道成员」→ 行原文（两次读数）
   - 技能复制完成、MCP 尚未添加时：`小明 … MCP 0 / 技能 1`、`小红 … MCP 0 / 技能 1`
     （截图 `/tmp/alibot273-roster.png`）；
   - 第 6 步的 MCP 复制之后重开名册：`小明 … MCP 1 / 技能 1`、`小红 … MCP 1 / 技能 1`，
     owner 行没有摘要（截图 `/tmp/alibot273-roster-after.png`）。
8. 频道配置：点频道行 → `#btnChannelSettings`。`#channelConfigBody` 里 `data-od-id` 清单
   **没有** `mcp-*` / `skill-*` / `channel-config-section-mcp` / `channel-config-section-skill` 任何一项
   （`hasMcp = false`），正文只有 协作模式 / 生效范围 / 队长 / 项目根与默认后端 / 默认权限档 / 联网 /
   工作区与数据面（截图 `/tmp/alibot273-channel-settings.png`）。

（截图在 `/tmp` 是本机验收留档；判据本身由第 2 节的 DOM 用例与第 3 节门禁复核，不依赖截图。）

## 6. 遗留 / 说明

- **后端复制端点的 pytest 用独立文件** `app/tests/test_skill_copy.py`（不碰 `test_skills.py`、
  也不碰 #274 的 `test_skill_migration.py`）；覆盖率见第 4 节。
- 频道级 MCP / 技能旧行仍在库里（本票不迁移、不回填、不删），频道配置面不展示、也不做「已弃用」提示（ADR-0031 §二）。
- `POST /api/skills/{id}/copy` 写入的目标行 `source_path` 记录的是**源成员的技能目录**（复制来源），
  与安装路径的语义一致；目标成员的家目录副本才是装配来源。
- 名册摘要只在名册打开期间缓存，重开即重读；复制成功后清掉相关目标的缓存。
