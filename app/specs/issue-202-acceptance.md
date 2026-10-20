# 验收取证：私聊也能用 web_search —— 联网档在 DM 回退到 Agent 级配置（#202）

**票型**：实施票（follow-up）｜**门禁**：G-impl（`--no-ff` 合并由 coordinator 执行 G-merge）
**Ticket**：GitHub issue #202（Part of #182）
**base**：`e2ad031`（`main`）｜**branch**：`issue-202-impl`｜**worktree**：`/Users/apple/orca/workspaces/alibot/dm-web-search`
**采集时间**：2026-10-21（第二轮 follow-up：Agent 侧显式 `null` = 清除覆盖）｜**环境**：macOS
（aarch64），Python 3.11.13（仓库根 `.venv`；本 worktree 用
符号链接 `.venv -> /Users/apple/orca/alibot/.venv`），Node（`node --test`，无 npm 依赖）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的读数**。第一轮的红证据在隔离副本
`/tmp/issue-202-red` 上取得（生产文件与 `e2ad031` 逐字节相同，见 §2.1–2.3），第二轮（§2.4）的红证据
在 `/tmp/issue-202-null-red` 上取得（Studio 三个生产文件与 `24dde4f` 逐字节相同）。全程**不动**本
worktree 的未提交工作，也不碰用户正在跑的实例与它的数据目录。

> **前置裁定（2026-10-21，coordinator 经 `orchestration ask` 授权）**：票面 Target 写的
> `app/alibot/i18n.py` 是**后端**错误文案表；Studio 的静态文案与 `t()` 的正本在
> `app/alibot/studio/i18n.js`（`i18n_ui.test.cjs` 校验的就是它）。本票新增的前端词条写进
> `studio/i18n.js` 的中英两份；`app.css` 与 `alibot-design-system/**` 未动。
>
> **第二轮授权（2026-10-21，coordinator 经 `orca orchestration send --type escalation` 答复）**：
> 允许编辑 `app/alibot/studio/index.html` 的 **Agent 编辑弹窗联网档卡片区块**（新增
> `data-web-access="follow"` 卡片、更新该区块注释与 hint），因为四档入口的标记是静态 HTML；
> 其它区块与 `app.css` / `alibot-design-system/**` 仍未动。

---

## 0. 前置 skill 的执行

前置 skill：`/Users/apple/.agents/skills/implement/SKILL.md`。它要求：先读规格 → 在约定接缝写用例
（TDD，先红后绿）→ 经常跑类型检查与单文件用例、收尾跑全量 → 收尾调用 `code-review` → 提交到当前
分支。本票的顺序逐条照做：

| 步骤 | 本票实际做的事 | 证据 |
| --- | --- | --- |
| 先读规格 | 逐条拆票面 Change A–D 与 Constraints，落到 Target 文件 | §1 |
| TDD 红（第一轮） | 先写 3 条 DM 装配用例 + 4 条读路径用例 + 2 条 Studio DOM 用例，在基线副本上跑 | §2.1–2.3（红：`3 failed` / `4 failed` / `3 failed`） |
| 绿（第一轮） | 实现后同一条命令 | §2.1–2.3（绿：`4 passed` / `12 passed` / `21 pass`） |
| TDD 红（第二轮） | 先写/改写 2+1 条 null 语义用例（读路径 / 端到端）、4 条 Studio 用例；后端在本 worktree 直接红，Studio 在基线副本 `/tmp/issue-202-null-red` 上红 | §2.4（红：`2 failed, 13 passed` / `1 failed, 4 passed` / `5 failed, 17 passed`） |
| 绿（第二轮） | 实现 A（api / service / db）+ B（Studio 四档）后同一条命令；review 处置又补了 1 条静态 HTML 守卫用例 | §2.4（绿：`15 passed` / `5 passed` / `23 pass`） |
| 类型检查 / 单文件 | 每改完一处跑受影响文件；`scripts/check.sh --fast` 含 ruff / ruff format --check / mypy / pyright / Node glob | §3 |
| 全量 | `scripts/check.sh`（全量 pytest + 其余门禁） | §3（`1004 passed, 10 skipped`；ruff / mypy / pyright / Node `212 pass` 全绿；`门禁全绿`） |
| code-review | 两轴并行子代理（Standards + Spec），结论分开写；第二轮复评同一套固定点 | §5 |
| 提交 | 每轮一个 commit 在分支 `issue-202-impl`，随后推送并更新同一个 PR #203 | §6 |

---

## 1. 改动面与落点

> 本节保留**第一轮**（DM 读路径 + 三档入口）的改动面。第二轮 follow-up 的改动面见 §1.2；
> 两处描述冲突时（例如「未选择 = 不提交该字段」）一律以 §1.2 为准。
| 文件 | 动作 |
| --- | --- |
| `app/alibot/db.py` | `web_settings_for_target` 先读 `target.kind`：`dm` → 新增的 `_dm_web_settings_locked`（只读 `agent_config`，形状与频道分支一致）；channel / task 分支**一行未改**；未知 target 仍 `None` |
| `app/alibot/db.py`（`_dm_web_settings_locked` / `_web_shared_settings_locked`） | `web_access = agent_config.web_access or 'search'`，`'off'` 归一 `None`；`mode='read-only'`；`backend='local'` / `docker_egress=False` / `egress_hosts=[]`；凭据 / 默认 provider / 已确认 provider 走与频道分支**同一条**语句（`_web_shared_settings_locked`） |
| `app/alibot/studio/index.html` | Agent 编辑弹窗新增「联网」字段：`off` / `search` / `search+fetch` 三张 `.radio-card`（`#agentEditWebAccess`）+ 提示「未选择 = 跟随频道；私聊里按默认档『只搜索』，且私聊不装配 fetch」（第二轮改成四档：见 §1.2） |
| `app/alibot/studio/app.js` | `openAgentEditModal` 读 `agent.web_access` 定选中态；`submitAgentEdit` 选了三档才提交 `web_access`（未选 = 不提交 = 跟随频道）；`agentRuntimeNodes` 补一行读数；`agentWebAccessLabel` 值缺失显示空字段文案；`bindEvents` 新增三档点击处理（第二轮的更改为：四档、总是提交、缺失显示「跟随频道」，见 §1.2） |
| `app/alibot/studio/i18n.js` | 新增中英同键词条：`agent.webAccess*`（8 条）、`agentDetail.kvWebAccess` / `webAccessOff` / `webAccessSearch` / `webAccessSearchFetch` |
| `app/tests/test_web_assembly.py` | 新增 4 条 DM 装配用例（默认挂 `web_search`；`off` 不挂；`search+fetch` 在 DM 仍不挂 `fetch`；`PATCH off` → 装配变化）+ 抽出 `_keyless_environ()` |
| `app/tests/test_web_access_config.py` | 新增 4 条 DM 读路径用例（默认 `search`；`off` 归一 `None`；凭据 / 确认行非空读数；`off` 不被「不提交」清空）+ 未知 target / channel / task 分支逐字不变 |
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

### 1.2 第二轮（follow-up）改动面：Agent 侧显式 `null` = 清除覆盖

> 人定（2026-10-21）：`PATCH /api/agents/{id}` 的 `web_access: null` = 回到跟随频道（写库 `NULL`），
> 与频道侧同一口径；**推翻 #184**「Agent 侧显式 null 一律 400」；同一 PATCH 的其它字段显式 `null`
> 仍整体 400。ADR-0026 §十五。

| 文件 | 动作 |
| --- | --- |
| `app/alibot/api.py` | `AgentUpdate.web_access` 移出「显式 null 一律拒绝」的校验器名单；端点用 `payload.model_fields_set` 区分「缺省 = `_UNSET`」与「显式 `null`」，并原样交给服务层；`AgentUpdate` / 端点的文档串改成三态口径 |
| `app/alibot/service.py` | `update_agent(web_access: Any = _UNSET)`：三态**原样透传**（不在服务层再解释一次）；文档串写明「区分发生在请求体层」 |
| `app/alibot/db.py` | `update_agent(web_access: Any = _UNSET)`：三态校验（`_UNSET` / `None` / 合法档位）与 `web_access = CASE WHEN ? THEN ? ELSE web_access END`（第一个参数 = 「提到了吗」），因此「没提到保持原值」与「显式 null 写 NULL」在同一条 UPDATE 里成立；`_UNSET` 的注释补上两处 `web_access` |
| `app/alibot/studio/index.html` | Agent 编辑弹窗联网档新增第四张卡片 `data-web-access="follow"`（「跟随频道（默认）」，排在最前）+ 更新区块注释与 hint；**仅此区块**（coordinator 2026-10-21 授权） |
| `app/alibot/studio/app.js` | 新增 `WEB_ACCESS_FOLLOW = 'follow'`（界面 token，不是后端档位）；`renderAgentEditWebAccess` 把「后端词表外的取值（含 `null`）」落回「跟随频道」；`agentEditWebAccessValue` 总返回四者之一；`submitAgentEdit` **总是**提交 `web_access`（`follow` → 显式 `null`，其余 → 档位）；`agentWebAccessLabel` 无覆盖时返回「跟随频道」词条而不是空字段 |
| `app/alibot/studio/i18n.js` | 中英各新增 `agent.webAccessFollow` / `agent.webAccessFollowHint` / `agentDetail.webAccessFollow`，并改写 `agent.webAccessNote`（跟随频道 = 提交 null） |
| `app/tests/test_web_access_config.py` | 拆开原来的「null 一律 400」用例：非法档位仍 400 + 不部分写入；其它字段（`role: null` 等）仍 400 且同一请求里的 `web_access` 不落库；新增「显式 null 清覆盖 → 读回 null / 库内 NULL / 私聊读数回到 `search`」与「清空与同请求其它字段互不牵连」 |
| `app/tests/test_web_assembly.py` | 新增端到端用例：`PATCH off` → DM 不挂工具；`PATCH null` → 库内 `NULL` → DM 挂回 `web_search` 且仍不挂 `fetch` |
| `app/tests/agent_detail_ui.test.cjs` | DOM 替身补第四张卡片；两条新用例（未配置时选中「跟随频道」且 payload 为 `web_access: null`；从具体档位切回「跟随频道」提交 null 并在运行时读回）；既有「整份字段」用例的期望值补 `web_access: null`；运行时读数断言改成「跟随频道」；词条键清单补三条 |
| `docs/adr/0026-…md` | 修订节新增 §十五（人定、推翻 #184 的那条纪律、其它字段仍 400、Studio 四档） |
| `CONTEXT.md` / `app/README.md` | 「联网档」词条与 `PATCH /api/agents/{id}` 行同步新语义（显式 null = 清除覆盖；四档入口） |

**没有改动**（Constraint 逐条）：schema / 迁移；`channel.web_access` 写路径与频道配置端点语义；
`fetch` 的任何闸门（DM 仍不挂 `fetch`）；`app/alibot/studio/app.css`；`alibot-design-system/**`；
`agent_seam.py`；`app/alibot/i18n.py`（本轮没有新的后端错误文案）。没有跑 `ruff format`。

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
| 红（基线副本） | `3 failed, 1 passed, 14 deselected in 2.24s` |
| 绿（本 worktree） | `4 passed, 14 deselected in 2.37s` |

红的三条：`test_a_dm_mounts_web_search_by_default_and_needs_no_consent_row`（`'web_search' not in
{'declare_silence'}`）、`test_a_dm_stays_read_only_even_when_the_agent_level_says_search_plus_fetch`
（`fetch` 之前全部模型都收不到联网工具）与 `test_patching_the_agent_web_access_off_stops_web_search_in_a_dm`
（DM 读路径返回 `None`，`PATCH` 写进去什么都不改变）。`off` 那一条在基线上本来就绿（没有工具可
挂），它是回归护栏而不是红证据。

### 2.2 读路径：同一条命令，先红后绿

```sh
cd app && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_web_access_config.py --no-header
```

| 状态 | 读数 |
| --- | --- |
| 红（基线副本） | `4 failed, 8 passed in 0.71s` |
| 绿（本 worktree） | `12 passed in 0.56s` |

红的四条：`test_a_dm_reads_the_default_search_level_from_agent_config_only`（`settings is None`）、
`test_a_dm_normalizes_an_explicit_off_to_none`（`settings is None`）、
`test_a_dm_reads_the_same_credential_and_consent_sources_as_a_channel`（`settings is None`）、
`test_omitting_web_access_keeps_the_previous_explicit_value`（`settings is None`）。未知 target 与
channel / task 两条在基线上就是绿的——它们的作用是钉住「逐字不变」，不是红证据。

### 2.3 Studio DOM：同一条命令，先红后绿

```sh
node --test app/tests/agent_detail_ui.test.cjs
```

| 状态 | 读数 |
| --- | --- |
| 红（基线副本） | `tests 21 / pass 18 / fail 3` |
| 绿（本 worktree） | `tests 21 / pass 21 / fail 0` |

红的三条：运行时页签的行集合（缺 `agentDetail.kvWebAccess`）、「选中态 + PATCH 提交」用例
（三档不存在）、词条键缺失（`agent.webAccessLabel` 等）。「未配置时没有选中档、保存不提交该字段」
在基线上也绿（旧实现同样不提交 `web_access`）——它钉住的是「未选择 = 跟随频道」这条不变量。

### 2.4 第二轮（2026-10-21 人定）：null 语义，同一条命令先红后绿

**后端**：新用例**先落地**、生产文件仍是 `24dde4f`（第一轮交付态），在本 worktree 直接跑：

```sh
cd app && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_web_access_config.py --no-header
cd app && /Users/apple/orca/alibot/.venv/bin/python -m pytest tests/test_web_assembly.py -k dm --no-header
```

| 命令 | 红（实现前） | 绿（实现后） |
| --- | --- | --- |
| `test_web_access_config.py` | `2 failed, 13 passed in 0.91s` | `15 passed in 0.93s` |
| `test_web_assembly.py -k dm` | `1 failed, 4 passed, 14 deselected in 2.97s` | `5 passed, 14 deselected in 2.99s` |

红的两条读路径用例是 `test_agent_web_access_explicit_null_clears_the_override` 与
`test_an_explicit_null_clears_the_override_while_other_fields_still_apply`，失败都是同一句读数：
`{"code":"validation_failed","fields":{"body":"该字段不能为 null（要保留原值请省略该字段）"}}`
（`400 != 200`）——正是 #184 那条纪律。红的一条端到端用例是
`test_patching_the_agent_web_access_null_brings_web_search_back_in_a_dm`，卡在同一处 400。
**新加的拒绝面用例在基线上本来就绿**（`role: null` 仍 400；非法档仍 400 且不部分写入）——它们钉的是
「放宽面只有 `web_access` 一个字段」，不是红证据。

**Studio**：红证据在隔离副本 `/tmp/issue-202-null-red` 上取得——把本 worktree 复制过去后，只把
`app/alibot/studio/{app.js,i18n.js,index.html}` 换回 `24dde4f` 的版本（逐文件核对 md5）：

```sh
md5 -q /tmp/issue-202-null-red/app/alibot/studio/app.js   # f02d2520c189058309f95c9e707bab8e
git show 24dde4f:app/alibot/studio/app.js | md5 -q       # f02d2520c189058309f95c9e707bab8e
node --test app/tests/agent_detail_ui.test.cjs           # 在 /tmp/issue-202-null-red 下
```

| 状态 | 读数 |
| --- | --- |
| 红（基线 Studio 生产文件 + 新用例） | `tests 22 / pass 17 / fail 5` |
| 绿（本 worktree） | `tests 23 / pass 23 / fail 0`（第 23 条是 review 处置时补的静态 HTML 守卫，见 §5 第二轮） |

红的五条：两条新用例（四档不存在 / 切回「跟随频道」提交的不是 `null`）、“运行时读数”用例
（基线显示空字段文案而不是「跟随频道」）、“整份字段”用例（基线不提交 `web_access`）、词条键清单
（缺 `agent.webAccessFollow` 等三条）。

### 2.5 库内读数（手工取证：`agent_config.web_access IS NULL`）

票面验收 3 要的「库内为 NULL」是**存储层读数**。本仓的 Python 用例只断言对外可观察行为
（`app/README.md`「测试只断言外部可观察行为」；`sqlite3` 直连只用于**造数**），因此这条读数用一条
独立脚本在同一台机器上手工取证，而不是塞进 pytest 断言：

```sh
cd app && env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY PYTHONPATH=. \
  /Users/apple/orca/alibot/.venv/bin/python /tmp/issue-202-null-reading.py
```

脚本（`/tmp/issue-202-null-reading.py`，一次性的取证工具，不入库；这里的围栏标 `text`，避免
`ruff format --check app` 把它当成仓库里的 Python 源重排）：

```text
import sqlite3, tempfile
from pathlib import Path
from alibot.api import create_app
from alibot.db import open_database
from fastapi.testclient import TestClient

with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"; data.mkdir()
    with TestClient(create_app(data_dir=data)) as client:
        agent = client.post("/api/agents", json={"name": "读数", "role": "资料员",
            "model_id": "ollama:qwen2.5", "system_prompt": "查资料"}).json()["agent"]
        aid = agent["id"]
        dm = client.post("/api/dms", json={"agent_id": aid}).json()["dm"]
        print("create        ->", client.get(f"/api/agents/{aid}").json()["agent"]["web_access"])
        print("PATCH off     ->", client.patch(f"/api/agents/{aid}", json={"web_access": "off"}).status_code)
    with sqlite3.connect(data / "alibot.db") as con:
        print("db after off  ->", con.execute(
            "SELECT web_access FROM agent_config WHERE member_id = ?", (aid,)).fetchone())
    with TestClient(create_app(data_dir=data)) as client:
        cleared = client.patch(f"/api/agents/{aid}", json={"web_access": None})
        print("PATCH null    ->", cleared.status_code, cleared.json()["agent"]["web_access"])
        print("GET read back ->", client.get(f"/api/agents/{aid}").json()["agent"]["web_access"])
    with sqlite3.connect(data / "alibot.db") as con:
        print("db after null ->", con.execute(
            "SELECT web_access, typeof(web_access) FROM agent_config WHERE member_id = ?", (aid,)).fetchone())
    database = open_database(data)
    try:
        settings = database.web_settings_for_target(dm["id"], aid)
    finally:
        database.close()
    print("dm settings   -> web_access =", repr(settings["web_access"]), "| mode =", settings["mode"])
```

实际读数（2026-10-21，临时数据目录，同一进程内先 `PATCH off` 再 `PATCH null`）：

```text
create        -> None
PATCH off     -> 200
db after off  -> ('off',)
PATCH null    -> 200 None
GET read back -> None
db after null -> (None, 'null')
dm settings   -> web_access = 'search' | mode = read-only
```

- `db after null` 的 `typeof(web_access) == 'null'` 排除了「写空串」这种假清空；
- `dm settings` 是 `Database.web_settings_for_target(dm_id, agent_id)` 的公开读数，`mode` 仍是
  `read-only`（DM 不挂 `fetch`）；
- 「装配层挂回 `web_search`」由 §2.4 的端到端用例取证（工具清单断言）。

---

## 3. 门禁读数（唯一入口 `scripts/check.sh`）

命令原文（仓库根，本 worktree）：

```sh
scripts/check.sh
```

实际读数（2026-10-21，exit code `0`；第二轮 follow-up 之后的最终一次全量）：

```text
== pytest（全量） ==
1004 passed, 10 skipped in 536.42s (0:08:56)

== ruff check ==
All checks passed!

== ruff format --check（只检查；不要跑 ruff format，它会整文件重排） ==
252 files already formatted

== mypy（仓库根） ==
Success: no issues found in 104 source files

== pyright ==
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族：glob，新增文件自动纳入） ==
ℹ tests 212
ℹ pass 212
ℹ fail 0

门禁全绿
```

第一轮（`a3d70df` 之前）的同一条命令读数：`1000 passed, 10 skipped` / `251 files already formatted` /
Node `210 pass / 0 fail`。第二轮新增 4 条 pytest 用例（读路径 3 条 + 装配层 1 条）与 2 条 Node 用例，
因此本轮的计数是 `1004` 与 `212`。

开发循环另有 `scripts/check.sh --fast`（不含全量 pytest）：同样全绿（Node `212 pass / 0 fail`）。

> 取证注意：第一次跑全量时我把输出接给了 `tee`，管道的退出码盖住了 `ruff format --check` 的失败
> （当时验收文档里的 Python 代码块被当成仓库源文件要求重排）。改完之后重跑，用
> `scripts/check.sh > log 2>&1; echo $?` 单独记录退出码，上表是那次的结果（`exit code 0`）。

---

## 4. 验收判据 → 落点 → 读数

| # | 判据（外部可观察） | 落点 | 读数 |
| --- | --- | --- | --- |
| D1 | DM target 默认挂 `web_search`（免凭据 `exa-mcp`，不需要确认行） | `db._dm_web_settings_locked` + 既有 `agent_seam._web_tools` | `test_web_assembly.py::test_a_dm_mounts_web_search_by_default_and_needs_no_consent_row`；`consented_providers == ()` |
| D1 | `PATCH /api/agents/{id} {"web_access":"off"}` 后 DM 不再挂 `web_search`（HTTP 写路径 → 装配） | 同上 | `test_patching_the_agent_web_access_off_stops_web_search_in_a_dm` |
| A1 | 凭据 / 默认 provider / 已确认 provider 与频道分支同一来源（非空读数） | `_web_shared_settings_locked` | `test_a_dm_reads_the_same_credential_and_consent_sources_as_a_channel`（`{'brave': 'sk-…'}` / `'brave'` / `('brave',)`） |
| D1 | `agent_config.web_access='off'` 时 DM 不挂任何联网工具 | 同上（`off` → `None`） | `test_a_dm_with_agent_level_off_mounts_no_web_tool`（工具清单 `{'declare_silence'}`） |
| D1 | `'search+fetch'` 在 DM 仍不挂 `fetch` | `mode` 固定 `read-only`（既有权限档闸门） | `test_a_dm_stays_read_only_even_when_the_agent_level_says_search_plus_fetch` |
| D2 | DM 返回默认档 `search`；`off` 归一 `None`；未知 target `None` | `db.web_settings_for_target` | `test_web_access_config.py` 三条 DM 用例 |
| D2 | channel / task 分支逐字不变（`agent → channel → 'search'`） | 既有分支未改 | `test_channel_and_task_levels_keep_their_merge_order`（含 `docker` / `read-only` / `docker_egress` 读数） |
| B1 | Agent 编辑弹窗三档；未选择 = 不提交 `web_access`；选了就随 PATCH 提交 | `studio/index.html` + `app.js` | `agent_detail_ui.test.cjs` 两条新用例（payload 断言） |
| B2 | Agent 详情「运行时」补一行联网档读数，缺失显示空字段文案 | `agentRuntimeNodes` / `agentWebAccessLabel` | 运行时用例（第 8 行 = `agentDetail.kvWebAccess`） |
| B3 | 频道设置弹窗的联网区块行为不变 | 未改 `webAccessSection` / `channel-config-web-access` | `channel_config_ui.test.cjs`（既有联网区块用例） |
| C | ADR-0026 修订节 / `CONTEXT.md` / `app/README.md` 同步 | 三份文档 | §1 的 diff |

**第二轮（follow-up）新增判据**：

| # | 判据（外部可观察） | 落点 | 读数 |
| --- | --- | --- | --- |
| A-1 | `PATCH {"web_access": null}` → `200`，读回 `agent.web_access == null` | `api.AgentUpdate`（移出 null 校验名单）+ 端点 `model_fields_set` + `service.update_agent` + `db.update_agent`（`_UNSET` 三态） | `test_web_access_config.py::test_agent_web_access_explicit_null_clears_the_override` |
| A-1 | 写库真的是 `NULL`（不是空串 / `'off'`），且私聊读路径回到默认档 | `db.update_agent` 的 `CASE WHEN ? THEN ? ELSE web_access END` | **§2.5 的手工读数**：`typeof(web_access) == 'null'`；用例侧断言 `web_settings_for_target(...)["web_access"] == "search"`（Python 用例不读表结构） |
| A-1 | 只交其它字段时 `web_access` 保持原值（`off` 还是 `off`） | 同一 `_UNSET` 缺省 | `test_omitting_web_access_keeps_the_previous_explicit_value` |
| A-1 | 清空与同请求的其它字段互不牵连（两件事都落库） | 同上 | `test_an_explicit_null_clears_the_override_while_other_fields_still_apply`（`role == '资料员'`） |
| A-3 | 非法档仍整体 400，且同请求的 `role` 不落库 | `db.update_agent` 白名单（校验先于写入） | `test_agent_web_access_rejects_an_unknown_level_without_partial_write` |
| A-2 | 其它字段的显式 `null` 仍整体 400，且同请求里的 `web_access` 也不落库 | `AgentUpdate._reject_explicit_null`（`web_access` 已移出） | `test_other_agent_fields_still_reject_an_explicit_null`（`role` / `display_name` / `enabled` 三种） |
| D-1 | 端到端三跳：`off` → DM 不挂工具；`null` → 库内 `NULL` → DM 挂回 `web_search` 且不挂 `fetch` | 写路径 + 读路径 + 既有装配层 | `test_web_assembly.py::test_patching_the_agent_web_access_null_brings_web_search_back_in_a_dm` |
| B-1 | 四档卡片；未配置时选中「跟随频道」；选中它提交 `web_access: null` | `index.html`（第四张卡）+ `app.js`（`WEB_ACCESS_FOLLOW` / `submitAgentEdit`） | `agent_detail_ui.test.cjs`「联网档四档：未配置时选中『跟随频道』…」 |
| B-0 | 静态 HTML 的正本里四张卡 + 四个词条键 + hint 齐全（替身不能替代正本） | `index.html` 的 `#agentEditWebAccess` 区块 | `agent_detail_ui.test.cjs`「静态 HTML 的联网档四张卡片齐全（替身与正本同形）」 |
| B-2 | 从具体档位切回「跟随频道」提交 `null`（不是只改界面） | 同上 | 「联网档：从具体档位切回『跟随频道』提交 null…」（payload 断言 `web_access === null`） |
| B-3 | 运行时页签：有覆盖显示档位、无覆盖显示「跟随频道」 | `agentWebAccessLabel` + `agentDetail.webAccessFollow` | 运行时用例（`values[7] == agentDetail.webAccessFollow`）+ 档位用例（`webAccessSearchFetch`） |
| C-1 | ADR-0026 修订节补 §十五（人定、推翻 #184、频道侧不变） | `docs/adr/0026-…md` | §1.2 的 diff |
| C-2 | `CONTEXT.md` / `app/README.md` 词条同步新语义 | 两份文档 | §1.2 的 diff |

### 4.1 读数（口径）

- DM 默认：`web_settings_for_target(dm_id, agent_id)["web_access"] == "search"`；
- 显式 `off`：`PATCH /api/agents/{id} {"web_access": "off"}` 之后同一读数为 `None`，DM 的
  `web_search` 不再装配；
- 回到默认档（第二轮已可达）：`PATCH /api/agents/{id} {"web_access": null}` 之后
  `agent_config.web_access IS NULL` 成立，同一读数为 `"search"`，DM 重新挂上 `web_search`（仍不挂
  `fetch`）。「不提交该字段」仍是保持原值（`off` 还是 `off`），二者是**两条**不同的路径，各有一条
  用例钉住（`test_omitting_web_access_keeps_the_previous_explicit_value` /
  `test_agent_web_access_explicit_null_clears_the_override`）。

---

## 5. 双轴 code-review（`/Users/apple/.agents/skills/code-review/SKILL.md`）

两个并行子代理，固定点 `e2ad031`，diff `git diff e2ad031...HEAD`，各自只报告、不改文件。结论分开
记录（不做跨轴重排）：

### Standards 轴

**无硬违规**（ADR 只追加修订节、schema 未动、README / CONTEXT / 验收证据同步）。判断项：

| 级别 | 发现 | 处置 |
| --- | --- | --- |
| P1 | `_dm_web_settings_locked` 与频道分支尾部重复（consent SELECT / 凭据读数 / `off` 归一 / 8 键形状） | **已修**：抽 `_web_shared_settings_locked()`，两条读路径共用同一来源 |
| P1 | 三套词条（`channelConfig.webAccess.*` / `agent.webAccess*` / `agentDetail.webAccess*`）映射同一枚举；`agentWebAccessLabel` 是 if 链 | **记录（P2）**：字面键 if 链是本仓既有约定（`webProviderLabel` 注释：动态键会让词条守卫看不见）；三套分别属于频道设置 / Agent 编辑 / Agent 运行时三个命名空间，且运行时文案不同（「已关闭」vs「关闭」）。不改 |
| P1 | 「未选择 = 跟随频道」在保存过一次后 UI 不可达（PATCH 拒绝显式 `null`） | **记录**：与验收 3 的冲突同源，见 §7.1；不在本票擅自改 API 语义 |
| P2 | `web_settings_for_target` 先 SELECT target，解析器又查同一行 | 保持：复用**同一** target→channel 解析器，避免第二条解析旁路；主键查询可忽略 |
| P2 | 控件助手（`renderEditTier` / `renderAgentEditWebAccess`）形状重复 | 保持：三态与布尔是两种取值；不新增样式，复用既有 `.radio-card` |
| P2 | 编辑面标签「联网」vs 运行时「联网档」 | 保持：区块名沿用票面「联网」（与频道设置同名），运行时字段名按 CONTEXT 词条「联网档」 |
| 记录 | 读路径用例直调公开库方法 | 该读数没有 HTTP 面；与既有 `test_docker_network_policy.py` 同体例，验收文档已写明 |

### Spec 轴

**无 P0**；Change A / B / C / D 逐条对得上，scope creep 无（改动全在票面 Target；schema / 迁移、
`studio/app.css`、`alibot-design-system/**`、频道联网区块均未动）。

| 级别 | 发现 | 处置 |
| --- | --- | --- |
| P1 | 验收 3 第三步 `{"web_access":null}` 实际 400；原文「不提交」只在原值本就是 `null` 时成立 | **已改文档口径**：§4.1 写明「回到默认档当前不可达」，并补 `off → 不提交 → 仍 off` 的读数；裁定请求见 §7.1 |
| P2 | B1 文案「未选择 = 跟随频道」在首次保存后不可达 | 如实披露（§7.1）；是否加「清除 / 跟随频道」动作待裁定 |
| P2 | 验收 3「PATCH off → DM 不再挂 web_search」缺端到端用例 | **已补**：`test_patching_the_agent_web_access_off_stops_web_search_in_a_dm`（HTTP 写 → DM 装配） |
| P2 | 凭据 / 默认 provider / 已确认 provider 只有空读数 | **已补**：`test_a_dm_reads_the_same_credential_and_consent_sources_as_a_channel`（非空 `{'brave': …}` / `'brave'` / `('brave',)`） |
| P2 | DM 提示词仍提 `fetch`（既有 `_web_prompt_note`，与频道 `search` 档同源） | 记录（§7.2）；`agent_seam.py` 不在 Target |
| P2 | 验收 6（PR）未完成 | 本轮完成，见 §6 |

### 第二轮复评（固定点 `24dde4f`，diff `git diff 24dde4f...HEAD`）

> 上面两张表是**第一轮**的复评记录；其中「显式 `null` 不可达 / 待裁定」的三条已在第二轮由人定
> 解决（§7.1），保留原文是为了不改写历史结论。

两个并行子代理、各自只报告不改文件。**无 P0**；P1 一条（Standards），已修。

**Standards 轴**：P1 ×1 / P2 ×6。

| 级别 | 发现 | 处置 |
| --- | --- | --- |
| P1 | 两个新用例用 `sqlite3` 直读 `agent_config.web_access` 断言列值，违反本仓「Python 用例只断言外部可观察行为、不读表结构」的测试判据（`app/README.md` / `docs/agents/review-rules.md`） | **已修**：两条用例只断言 HTTP 读回 `null` + `web_settings_for_target(...)["web_access"] == "search"`；「库内列是 `NULL`」改为 §2.5 的手工读数（`typeof(web_access) == 'null'`），不再进 pytest |
| P2 | ADR-0026 §十四 就地改写上一轮句子（三档→四档）且未标「事实修正」 | **已修**：恢复原句，另起一行标 **事实修正** 并指向 §十五 |
| P2 | `_UNSET` 是 db 的私有裸 `object()`，被 api / service 跨模块 import；三态参数是 `Any`，类型检查失效；新增可清空字段要改三层 | **保持**：哨兵必须**同一对象**穿透三层（与既有 `update_channel_config` 同一条路），做成公开常量等于对外承诺一个我们不想要的契约；`Any` 与频道配置的同名参数一致。ADR §十五 要求的是「真的区分」，不是某种具体类型写法 |
| P2 | 「UNSET / None / 白名单」归一分两处（agent 与 channel）；端点层也分叉（频道 `model_dump`、Agent `model_fields_set`） | **保持**：两条写路径的校验文案与现状来源不同（agent 用 `CASE WHEN` 就地写、channel 先读整行再整体 UPDATE）；端点层显式枚举字段保住了服务层签名的类型信息。合流是一次独立重构，不在本票 |
| P2 | `WEB_ACCESS_FOLLOW` 与后端档位共用变量空间，`includes(...) ? ... : null` 反解；未来新增档位可能被静默清覆盖 | **已修一半**：`agentEditWebAccessValue()` 现在直接返回**提交值**（`follow` → `null`），`submitAgentEdit` 不再反解；剩余风险是前端 `WEB_ACCESS_LEVELS` 镜像漂移（与 `CHANNEL_MODES` 同一既有模式），记入 §7.4；另补一条静态 HTML 守卫用例（缺卡 / 缺词条键会红） |
| P2 | `studio/i18n.js` 英文注释仍是旧口径（「three levels / no selection」）而中文注释已更新 | **已修**：英文注释改成四档 + 显式 null |
| P2 | 显式 null 用例与端到端用例重复断言「库内 NULL + 读回 `search`」 | **保持**：两处接缝不同（库读路径 / 装配层工具清单），重复的那一条断言在 P1 修复后已各自收敛到「公开读数」 |

**Spec 轴**：无 P0 / P1，P2 ×5。

| 级别 | 发现 | 处置 |
| --- | --- | --- |
| P2 | `app/alibot/db.py` 被改但不在票面 Target 清单（coordinator 只另授权了 `index.html`） | **结构性必需**（票面 A.1 要求「写库为 `NULL`」，而落库只在 `db.update_agent`）：已在 §1.2 与 §7.5 显式披露，并在开工后追加一条 `escalation` 请 coordinator 追认；没有其它越界文件 |
| P2 | 票面把「区分缺省 / null」标在 `service.py`，实际落在 `api.py`（`model_fields_set`）+ `db.py`（哨兵） | **如实记录**：`service.update_agent` 只做**透传**（三态不在服务层二次解释，避免第三套语义）；§1.2 的落点表按实际写 |
| P2 | 同一文件内注释与新行为矛盾：`app.js` 编辑弹窗「三档都不选中」、运行时「显示空字段文案」 | **已修**：两处注释改成四档 / 「跟随频道」词条 |
| P2 | 读数缺失（`null`/`undefined`）会被映射成「跟随频道」且保存时总是提交，存在静默清覆盖路径；DOM 替身自造卡片，`index.html` 少卡不红 | **部分修**：新增静态 HTML 守卫用例（四卡 + 词条键）；「总是提交」是票面 B.1 的要求（选中跟随频道必须提交 null），且 `_agent_payload` **总是**返回 `web_access`（新建就是 `null`），因此读数缺失不是可达状态；镜像漂移风险记入 §7.4 |
| P2 | 范围蔓延：仅 `db.py`（同第一条） | 无其它蔓延（i18n.py 未动、无 schema / 迁移、`fetch` 闸门未放宽） |

> 复核初稿发现的一条 P1 风险（「若验收文档没有落库内 `IS NULL` 读数就升 P1」）已由 §2.5 覆盖：
> 手工读数 + 脚本原文都在文档里。

## 6. 提交 / 推送 / PR

- 第一轮 commits：`6928394`（实现）→ `ce5a523`（review 处置）→ `f6e21e2`（门禁读数与 PR 正文）
- 第二轮 commits：`a3d70df`（实现：API 三态 + Studio 四档 + 用例 + 文档）→ 本轮收尾提交（验收读数 /
  双轴处置 / PR 正文；SHA 见 PR #203 的提交列表）
- 分支：`issue-202-impl`（`git push -u origin issue-202-impl`；**不新开分支**）
- **PR #203**：https://github.com/whutlichao/alibot/pull/203（**同一个 PR**，正文用
  `gh pr edit --body-file` 更新，保留 `Closes #202`）
- PR body 路径：`app/specs/issue-202-pr-body.md`（与 PR 正文逐字同源）

---

## 7. 遗留 / 已知边界（不是本票的静默缺口）

### 7.1 Agent 级「回到跟随频道」：**已由人定解决**（第二轮）

第一轮披露的缺口是「Agent 级没有 HTTP 写入口能回到跟随频道」：`PATCH /api/agents/{id}` 的显式
`null` 按 #184 纪律整体 400，而「不提交该字段」只保持原值。**2026-10-21 人定**选择了「放开显式
`null` = 清除覆盖」这一条（本文件第一轮列的选项 ①），因此：

- `PATCH {"web_access": null}` 现在**可达**：写库 `NULL`、读回 `null`、私聊读路径回到默认档
  `search`（§2.4 / §4 的 A-1 行）；
- 编辑面的「跟随频道（默认）」是一个**可逆**档位：从具体档位切回它会提交显式 `null`（§4 的 B-2 行）；
- 频道侧语义**不变**（`null` 仍是「未配置」），同一条 PATCH 的其它字段显式 `null` 仍整体 400。

### 7.2 DM 的系统提示仍提到 `fetch`

`_web_prompt_note()` 是「本轮挂了联网工具」的通用边界句，与频道 `search` 档的既有行为一致；
本票不改 `agent_seam.py`（不在 Target 内）。DM 的工具清单里确实没有 `fetch`，闸门未被放宽。

### 7.3 DM 的 `docker_egress` / `egress_hosts` 是固定读数

DM 没有频道 workspace，也没有 `permission_rule` 的频道 scope；这避免了「私聊借某个频道的白名单
出网」。若将来 DM 需要容器出网，必须另开票设计，而不是在这里放宽。

### 7.4 界面 token `follow` 不是后端档位

`WEB_ACCESS_FOLLOW = 'follow'` 只活在 Studio：它是「没有 Agent 级覆盖」的界面 token，由
`agentEditWebAccessValue()` 翻译成提交值 `web_access: null`（保存路径不认识这个 token）。后端白名单
仍只有 `off` / `search` / `search+fetch`；把 `'follow'` 发给 API 会得到 400（非法档）。这样界面的
第四档就不需要后端多认一个档位，也不需要 schema 改动。

**镜像漂移的已知边界**：前端 `WEB_ACCESS_LEVELS` 是后端 `db.WEB_ACCESS_LEVELS` 的镜像（与
`CHANNEL_MODES` / `PERMISSION_MODES` 同一既有模式）。若后端将来新增一档而前端没同步，编辑面没有
那档的卡片，`renderAgentEditWebAccess(新值)` 会落回「跟随频道」——这是**渲染**上的降级，不是静默
写库：只有用户真的点了「跟随频道（默认）」再保存才会提交 `null`。新增档位时两边一起改是本仓既有
约定，静态 HTML 守卫用例只能保证「已知四档」在正本里齐全。

### 7.5 `app/alibot/db.py` 的改动是票面 A.1 的结构性必需（已披露）

票面的 Target 清单列了 `api.py` / `service.py`，但没有列 `db.py`；而「显式 `null` 写库为 `NULL`」
这条只能在 `Database.update_agent` 落。本票因此改了 `db.py` 的**同一个** `update_agent`（三态入参 +
`CASE WHEN` 写列 + `_UNSET` 注释），没有碰 `web_settings_for_target` 与频道分支。开工后已追加一条
`orca orchestration send --type escalation` 向 coordinator 披露这一处越出 Target 清单的改动并请追认；
`index.html` 的编辑则是 coordinator 事前明确授权的。除此之外没有越界文件。
