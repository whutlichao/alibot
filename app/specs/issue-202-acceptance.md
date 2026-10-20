# 验收取证：私聊也能用 web_search —— 联网档在 DM 回退到 Agent 级配置（#202）

**票型**：实施票｜**门禁**：G-impl（`--no-ff` 合并由 coordinator 执行 G-merge）
**Ticket**：GitHub issue #202（Part of #182）
**base**：`e2ad031`（`main`）｜**branch**：`issue-202-impl`｜**worktree**：`/Users/apple/orca/workspaces/alibot/dm-web-search`
**采集时间**：2026-10-21｜**环境**：macOS（aarch64），Python 3.11.13（仓库根 `.venv`；本 worktree 用
符号链接 `.venv -> /Users/apple/orca/alibot/.venv`），Node（`node --test`，无 npm 依赖）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的读数**。红证据在隔离副本
`/tmp/issue-202-red` 上取得（生产文件与 `e2ad031` 逐字节相同，见 §2），**不动**本 worktree 的
工作区，也不碰用户正在跑的实例与它的数据目录。

> **前置裁定（2026-10-21，coordinator 经 `orchestration ask` 授权）**：票面 Target 写的
> `app/alibot/i18n.py` 是**后端**错误文案表；Studio 的静态文案与 `t()` 的正本在
> `app/alibot/studio/i18n.js`（`i18n_ui.test.cjs` 校验的就是它）。本票新增的前端词条写进
> `studio/i18n.js` 的中英两份；`app.css` 与 `alibot-design-system/**` 未动。

---

## 0. 前置 skill 的执行

前置 skill：`/Users/apple/.agents/skills/implement/SKILL.md`。它要求：先读规格 → 在约定接缝写用例
（TDD，先红后绿）→ 经常跑类型检查与单文件用例、收尾跑全量 → 收尾调用 `code-review` → 提交到当前
分支。本票的顺序逐条照做：

| 步骤 | 本票实际做的事 | 证据 |
| --- | --- | --- |
| 先读规格 | 逐条拆票面 Change A–D 与 Constraints，落到 Target 文件 | §1 |
| TDD 红 | 先写 3 条 DM 装配用例 + 4 条读路径用例 + 2 条 Studio DOM 用例，在基线副本上跑 | §2（红：`2 failed` / `2 failed` / `4 failed`） |
| 绿 | 实现后同一条命令 | §2（绿：`3 passed` / `10 passed` / `21 pass`） |
| 类型检查 / 单文件 | 每改完一处跑受影响文件；`scripts/check.sh --fast` 含 ruff / ruff format --check / mypy / pyright / Node glob | §3 |
| 全量 | `scripts/check.sh`（全量 pytest + 其余门禁） | §3（`997 passed, 10 skipped`；`门禁全绿`） |
| code-review | 两轴并行子代理（Standards + Spec），结论分开写 | §5 |
| 提交 | 一个 commit 在分支 `issue-202-impl`，随后推送并开 PR | §6 |

---

## 1. 改动面与落点

| 文件 | 动作 |
| --- | --- |
| `app/alibot/db.py` | `web_settings_for_target` 先读 `target.kind`：`dm` → 新增的 `_dm_web_settings_locked`（只读 `agent_config`，形状与频道分支一致）；channel / task 分支**一行未改**；未知 target 仍 `None` |
| `app/alibot/db.py`（`_dm_web_settings_locked`） | `web_access = agent_config.web_access or 'search'`，`'off'` 归一 `None`；`mode='read-only'`；`backend='local'` / `docker_egress=False` / `egress_hosts=[]`；凭据 / 默认 provider / 已确认 provider 与频道分支同一来源 |
| `app/alibot/studio/index.html` | Agent 编辑弹窗新增「联网」字段：`off` / `search` / `search+fetch` 三张 `.radio-card`（`#agentEditWebAccess`）+ 提示「未选择 = 跟随频道；私聊里按默认档『只搜索』，且私聊不装配 fetch」 |
| `app/alibot/studio/app.js` | `openAgentEditModal` 读 `agent.web_access` 定选中态；`submitAgentEdit` 选了三档才提交 `web_access`（未选 = 不提交 = 跟随频道）；`agentRuntimeNodes` 补一行读数；`agentWebAccessLabel` 值缺失显示空字段文案；`bindEvents` 新增三档点击处理 |
| `app/alibot/studio/i18n.js` | 新增中英同键词条：`agent.webAccess*`（8 条）、`agentDetail.kvWebAccess` / `webAccessOff` / `webAccessSearch` / `webAccessSearchFetch` |
| `app/tests/test_web_assembly.py` | 新增 DM 装配用例（默认挂 `web_search`；`off` 不挂；`search+fetch` 在 DM 仍不挂 `fetch`）+ 抽出 `_keyless_environ()` |
| `app/tests/test_web_access_config.py` | 新增 DM 读路径用例（默认 `search`；`off` 归一 `None`；未知 target `None`；channel / task 分支合并顺序与读数逐字不变） |
| `app/tests/agent_detail_ui.test.cjs` | 三档选中态 / 保存 payload / 运行时读数；DOM 替身补 `#agentEditWebAccess` 的三张卡片；词条键清单补新键 |
| `CONTEXT.md` | 「联网档」词条补 DM 语义（回退 Agent 级 + 默认档；DM 不装 `fetch`；2026-10-21 人定） |
| `app/README.md` | 「联网档」段落同步 DM 读路径与 UI 入口 |
| `docs/adr/0026-agent-web-access-is-explicit-opt-in.md` | 新增「修订（2026-10-21，#202）」§十四 |
| `app/specs/issue-202-acceptance.md` | 本文件 |

**没有改动**（票面 Constraint 逐条）：schema / 迁移；`app/alibot/studio/app.css` 与
`alibot-design-system/**`；`channel.web_access` 的写路径；`fetch` 的任何闸门；`agent_seam.py`
（装配层仍是同一条 `_web_tools`，读路径返回 `None` 即不挂）。

### 1.1 读路径形状（DM 与 channel / task 的同形读数）

```text
web_settings_for_target(target_id, member_id)
  target.kind = 'dm'      → agent_config.web_access or 'search'   （无频道级合并）
                            mode='read-only' / backend='local' / docker_egress=False / egress_hosts=[]
  target.kind = 'channel' → agent_config.web_access or channel.web_access or 'search'   （逐字未改）
  target.kind = 'task'    → 同上（经 task.channel_id）
  未知 target             → None
```

DM 的读数与频道分支**同形状**（8 个键一个不少），因此装配层的 `_web_tools` 不需要 DM 专支；
`mode='read-only'` 让既有 `FETCH_ACCESS_LEVEL and mode != "read-only"` 闸门直接挡住 `fetch`。

---

## 2. 红→绿证据

红证据的取得方式：把**当前 worktree**（只含新用例、生产文件仍是 baseline）复制到隔离目录
`/tmp/issue-202-red`，逐文件核对生产文件与 `e2ad031` 逐字节相同：

```sh
git show e2ad031:app/alibot/db.py            | md5 -q   # 92c92dba35a3b796c3f3fe18c469f876
md5 -q /tmp/issue-202-red/app/alibot/db.py             # 92c92dba35a3b796c3f3fe18c469f876
git show e2ad031:app/alibot/studio/app.js    | md5 -q   # 08389b364d321bcce772548a04d0a1e8
git show e2ad031:app/alibot/studio/index.html| md5 -q   # 4401e9e18531e087a63a657456f3ed36
git show e2ad031:app/alibot/studio/i18n.js   | md5 -q   # 834bbd878f15381a36eb5f6a1a46880b
```

### 2.1 DM 装配：同一条命令，先红后绿

```sh
cd app && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_web_assembly.py -k dm --no-header
```

| 状态 | 读数 |
| --- | --- |
| 红（基线副本） | `2 failed, 1 passed, 14 deselected in 1.74s` |
| 绿（本 worktree） | `3 passed, 14 deselected in 1.70s` |

红的两条：`test_a_dm_mounts_web_search_by_default_and_needs_no_consent_row`（`'web_search' not in
{'declare_silence'}`）与 `test_a_dm_stays_read_only_even_when_the_agent_level_says_search_plus_fetch`
（`fetch` 之前全部模型都收不到联网工具）。`off` 那一条在基线上本来就绿（没有工具可挂），它是
回归护栏而不是红证据。

### 2.2 读路径：同一条命令，先红后绿

```sh
cd app && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_web_access_config.py --no-header
```

| 状态 | 读数 |
| --- | --- |
| 红（基线副本） | `2 failed, 8 passed in 0.56s` |
| 绿（本 worktree） | `10 passed in 0.47s` |

红的两条：`test_a_dm_reads_the_default_search_level_from_agent_config_only`（`settings is None`）与
`test_a_dm_normalizes_an_explicit_off_to_none`（`settings is None`）。未知 target 与 channel / task
两条在基线上就是绿的——它们的作用是钉住「逐字不变」，不是红证据。

### 2.3 Studio DOM：同一条命令，先红后绿

```sh
node --test app/tests/agent_detail_ui.test.cjs
```

| 状态 | 读数 |
| --- | --- |
| 红（基线副本） | `tests 21 / pass 17 / fail 4` |
| 绿（本 worktree） | `tests 21 / pass 21 / fail 0` |

红的四条：运行时页签的行集合（缺 `agentDetail.kvWebAccess`）、两条新用例（三档不存在 / 无选中）、
词条键缺失（`agent.webAccessLabel` 等）。

---

## 3. 门禁读数（唯一入口 `scripts/check.sh`）

命令原文（仓库根，本 worktree）：

```sh
scripts/check.sh
```

实际读数（2026-10-21，exit code `0`）：

```text
== pytest（全量） ==
997 passed, 10 skipped in 513.65s (0:08:33)

== ruff check ==
All checks passed!

== ruff format --check（只检查；不要跑 ruff format，它会整文件重排） ==
250 files already formatted

== mypy（仓库根） ==
Success: no issues found in 104 source files

== pyright ==
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族：glob，新增文件自动纳入） ==
ℹ tests 210
ℹ pass 210
ℹ fail 0

门禁全绿
```

开发循环另有 `scripts/check.sh --fast`（不含全量 pytest）：同样全绿（Node `210 pass / 0 fail`）。

---

## 4. 验收判据 → 落点 → 读数

| # | 判据（外部可观察） | 落点 | 读数 |
| --- | --- | --- | --- |
| D1 | DM target 默认挂 `web_search`（免凭据 `exa-mcp`，不需要确认行） | `db._dm_web_settings_locked` + 既有 `agent_seam._web_tools` | `test_web_assembly.py::test_a_dm_mounts_web_search_by_default_and_needs_no_consent_row`；`consented_providers == ()` |
| D1 | `agent_config.web_access='off'` 时 DM 不挂任何联网工具 | 同上（`off` → `None`） | `test_a_dm_with_agent_level_off_mounts_no_web_tool`（工具清单 `{'declare_silence'}`） |
| D1 | `'search+fetch'` 在 DM 仍不挂 `fetch` | `mode` 固定 `read-only`（既有权限档闸门） | `test_a_dm_stays_read_only_even_when_the_agent_level_says_search_plus_fetch` |
| D2 | DM 返回默认档 `search`；`off` 归一 `None`；未知 target `None` | `db.web_settings_for_target` | `test_web_access_config.py` 三条 DM 用例 |
| D2 | channel / task 分支逐字不变（`agent → channel → 'search'`） | 既有分支未改 | `test_channel_and_task_levels_keep_their_merge_order`（含 `docker` / `read-only` / `docker_egress` 读数） |
| B1 | Agent 编辑弹窗三档；未选择 = 不提交 `web_access`；选了就随 PATCH 提交 | `studio/index.html` + `app.js` | `agent_detail_ui.test.cjs` 两条新用例（payload 断言） |
| B2 | Agent 详情「运行时」补一行联网档读数，缺失显示空字段文案 | `agentRuntimeNodes` / `agentWebAccessLabel` | 运行时用例（第 8 行 = `agentDetail.kvWebAccess`） |
| B3 | 频道设置弹窗的联网区块行为不变 | 未改 `webAccessSection` / `channel-config-web-access` | `channel_config_ui.test.cjs`（既有联网区块用例） |
| C | ADR-0026 修订节 / `CONTEXT.md` / `app/README.md` 同步 | 三份文档 | §1 的 diff |

### 4.1 读数（口径）

- DM 默认：`web_settings_for_target(dm_id, agent_id)["web_access"] == "search"`；
- 显式 `off`：`PATCH /api/agents/{id} {"web_access": "off"}` 之后同一读数为 `None`，DM 的
  `web_search` 不再装配；
- 回到默认档：**不提交 `web_access`**（PATCH 的部分更新语义 = 保持原值）后仍是「跟随频道」，
  DM 读路径按默认档 `search`。Agent 端点的显式 `null` 仍按 #184 纪律整体拒绝（400）——本票
  不改这条既有语义，见 §7 的遗留项。

---

## 5. 双轴 code-review

（见下节，两轴并行子代理的原始结论）

---

## 6. 提交 / 推送 / PR

- commit：（见 `git log -1`）
- PR 号 / body 路径：（见 Answer）

---

## 7. 遗留 / 已知边界（不是本票的静默缺口）

1. **Agent 级「回到跟随频道」没有 HTTP 写入口**：#184 纪律下 `PATCH /api/agents/{id}` 的显式
   `null` 整体拒绝，因此一旦把 Agent 写成某个显式档，界面上没有「清除回未配置」的按钮。本票的
   编辑面按票面只做三档（未选择 = 不提交 = 跟随频道）；要恢复「跟随频道」需要新增写语义或第二个
   动作，属于另一张票。
2. **DM 的系统提示仍提到 `fetch`**：`_web_prompt_note()` 是「本轮挂了联网工具」的通用边界句，
   与频道 `search` 档的既有行为一致；本票不改 `agent_seam.py`（不在 Target 内）。DM 的工具清单
   里确实没有 `fetch`，闸门未被放宽。
3. **DM 的 `docker_egress` / `egress_hosts` 读数是固定值**：DM 没有频道 workspace，也没有
   `permission_rule` 的频道 scope；这避免了「私聊借某个频道的白名单出网」。
