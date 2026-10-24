# Agent 详情「危险操作」区块与 Agent 软删除：验收记录

## 来源

用户反馈正题（2026-10-24）：Agent 详情里「暂停 / 恢复」与常规配置混在同一区块，删除也没有入口。
本票把暂停 / 恢复搬进页面最后一个「危险操作」区块，新增两步确认的删除，并把删除做成**完整软删除**：

- 页面顺序：统计 → 系统提示 → 配置 → 全局 MCP → 危险操作；
- 危险区：危险说明 + 暂停 / 恢复 + 删除；暂停的已知代价说明保留；
- 删除：第一次点击只展开确认区，第二次「确认删除」才发 `DELETE /api/agents/{id}`；
- 后端：`member.deleted_at` + 清空该成员的 `target_member` 行；消息 / 任务 / DM / `round_outcome`
  一律保留；二次删除与 owner / 未知 id 都是可读 404；
- 任务指派者已不在成员表时显示「已删除的成员」，不露裸 `mem_...` id。

基线：`97184ffec45331c9cecf826ec99efafe2bad3218`（本票 worktree 的切出点）。

## 改动清单

| 层 | 文件 | 改动 |
| --- | --- | --- |
| 前端 | `app/alibot/studio/app.js` | `agentConfigSection()` 不再渲染暂停按钮与 `pauseNote`；新增 `agentDangerSection()` 并排在 `globalMcpSection()` 之后；两步确认删除、成功 / 失败路径、离线与请求中禁用；`taskMemberName()` 的已删除回退 |
| 前端 | `app/alibot/studio/i18n.js` | 中英双语新增 `agentDetail.sectionDanger` / `dangerHint` / `delete` / `deleteConfirm` / `deleteConfirmAction` / `deleteCancel`、`toast.agentDeleted` / `toast.agentDeleteFailed`、`task.deletedAssignee` |
| 前端 | `app/alibot/studio/app.css` | 危险区 / 危险按钮 / 确认区样式，只消费 `--error` / `--warn` 族与 `--border`；按钮可收缩 + 换行 |
| 后端 | `app/alibot/db.py` | `get_agent()` 增加 `deleted_at IS NULL` 过滤；新增 `delete_agent()`（写 `deleted_at` + 删 `target_member`，单事务单锁） |
| 后端 | `app/alibot/service.py` | 新增 `delete_agent()`（人类面动作，配置 / 身份类写路径） |
| 后端 | `app/alibot/api.py` | 新增 `DELETE /api/agents/{id}`；原 `PUT` / `DELETE` 兜底收窄为 `PUT`，改名为 `no_agent_replacement` |
| 后端文案 | `app/alibot/i18n.py` | `error.agent_delete_unsupported` → `error.agent_replace_unsupported`（删除已存在，原句不再成立） |
| 测试 | `app/tests/agent_detail_ui.test.cjs` | 区块顺序 / 危险区内容 / 两步确认 / 成功 / 失败 / 离线 / 英文 / 已删除指派回退 |
| 测试 | `app/tests/test_agent_detail.py` | 软删除契约、二次 404、频道成员、状态点、历史与任务读数不变、owner / 未知 id / PUT 404 |
| 测试 | `app/tests/test_create_agent.py` / `app/tests/test_channel_join.py` | 既有「DELETE 明确 404」断言与注释同步到新契约（最小改动；前者改删一个独立成员） |
| 文档 | `CONTEXT.md` / `app/README.md` | 成员词条与端点表 / 详情页段落同步 |

## 红证据（先写测试，后写实现）

### DOM：隔离基线副本

```sh
scripts/red-evidence.sh 97184ffec45331c9cecf826ec99efafe2bad3218 app/tests/agent_detail_ui.test.cjs
# exit=1
# 基线：97184ffec45331c9cecf826ec99efafe2bad3218 (97184ff)
# 测试：app/tests/agent_detail_ui.test.cjs（取自当前工作区）
# 基线 app.js md5：135cc1fe0b0bab1da16b2fbfb079df22
# 证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.bw9TG1
# ℹ tests 38
# ℹ pass 28
# ℹ fail 10
```

失败用例（全部是产品代码尚未实现的行为）：

```text
✖ 所有区块在同一页纵向铺开：统计 / 系统提示 / 配置 / 全局 MCP / 危险操作
✖ 暂停 / 恢复与启用状态同源：按钮只改草稿，保存时提交 enabled
✖ 删除是两步：第一次点删除只展开确认区，不发请求；取消后确认区收起且仍无请求
✖ 确认删除只发一次 DELETE：成功后详情关闭、列表刷新、toast 可读
✖ 删除请求在途时按钮禁用：请求没回来之前不能重复提交
✖ 删除失败留在当前页、本地镜像不变，错误原因不静默吞掉
✖ 离线时暂停与删除都禁用
✖ 危险区文案有英文取值：切到 English 后同样可见
✖ 任务指派者已不在成员表里：显示「已删除的成员」，不把裸 id 给用户
✖ 本票新增 / 沿用的文案在两份词条里都取到值
```

### API：旧代码直接跑新用例

```sh
cd app && ../.venv/bin/python -m pytest tests/test_agent_detail.py -k delete
# exit=1
# F. [100%]
# FAILED tests/test_agent_detail.py::test_delete_soft_deletes_the_agent_and_keeps_history
#   assert response.status_code == 200
#   E       assert 404 == 200      ← 旧代码没有 DELETE 路由，落到 404 兜底
# 1 failed, 1 passed, 15 deselected in 0.32s
```

`test_delete_rejects_owner_and_unknown_ids_and_put_stays_unsupported` 在旧代码上绿（owner / 未知 id /
PUT 本来就是 404）；它保留作删除路由加入后的回归守卫。

## 目标用例

```sh
node --test app/tests/agent_detail_ui.test.cjs
# ℹ tests 38
# ℹ pass 38
# ℹ fail 0

cd app && ../.venv/bin/python -m pytest tests/test_agent_detail.py
# 17 passed in 1.02s
```

DOM 断言覆盖：

1. 危险区是页面最后一个区块（在全局 MCP 之后）；暂停按钮在危险区内、配置区块里不再有它；
2. 危险说明可见且包含「危险」语义；暂停的已知代价说明仍在危险区；
3. 第一次点删除零请求、确认区出现；取消后确认区收起且仍零请求；
4. 确认删除只发一次 `DELETE /api/agents/<id>`；成功后详情关闭、左栏与频道成员面板刷新、toast 含 Agent 名称；
5. 删除请求在途时删除 / 确认按钮禁用（受控 promise：请求没回来之前不能重复提交）；
6. 失败留在当前页、本地镜像不变、后端原因可见；离线时暂停与删除禁用；
7. 英文词条同样渲染（`Danger zone` / `Delete`）；已删除指派者显示「已删除的成员」，成员表未就绪时用中性「成员」占位；
8. `i18n_ui.test.cjs` 的两份键集 / 裸中文口径照跑（53 passed）。

### 真机视口读数（ego-browser 0.6.0.16 + 真实 Studio）

```sh
# 临时数据目录 + 真实后端；一个 Agent 经 HTTP 面创建
cd app && ../.venv/bin/python -m alibot --data-dir /tmp/alibot-danger-visual --port 8787
curl --noproxy '*' -X POST http://127.0.0.1:8787/api/agents \
  -H 'Content-Type: application/json' \
  -d '{"name":"小明","role":"前端工程师","model_id":"ollama:qwen2.5","system_prompt":"你只回答一句话。"}'
# ego-browser：打开 /studio/、点 Agent 行、展开删除确认区，再用
# Emulation.setDeviceMetricsOverride 依次量 1350 / 900 / 420，取 danger 区文本与按钮的
# getBoundingClientRect，检查左右越界与按钮两两相交。
```

| viewport | `document.scrollingElement.scrollWidth` | danger 区宽 | 越界元素 | 按钮相交 |
| --- | --- | --- | --- | --- |
| 1350 | 1350 | 880 | 0 | 0 |
| 900 | 900 | 845 | 0 | 0 |
| 420 | 420 | 365 | 0 | 0 |

三档下确认区都展开（`confirmVisible: true`）；1350 / 900 / 420 的页面滚动宽度都等于视口宽度，
说明危险说明、四个按钮与确认文案没有横向溢出、互不遮挡。截图（临时）：
`/tmp/alibot-danger-visual-{1350,900,420}.png`。

API 断言覆盖：

1. 删除返回 200，`deleted` 是真实 id，`agents` / `members` 不再含它；
2. `GET` 404、二次 `DELETE` 404、owner 404、未知 id 404、`PUT` 仍 404；
3. 频道成员表不再含它；`member-statuses` 不再出现它；
4. 删除前后 `messages` / `round_outcome` / `budget` 快照逐字相等；
5. 任务不被改写：`task.assignee_member_id` 与任务线程消息删除前后逐字相等；
6. 未删除 Agent 的 `PATCH` / 暂停（`enabled=false`）继续可用（既有用例 + 本票守卫）。

## 门禁读数

### 开发循环

```sh
scripts/check.sh --fast tests/test_agent_detail.py
# exit=0
# pytest: 17 passed in 0.98s
# ruff check: All checks passed!
# ruff format --check: 315 files already formatted
# mypy: Success: no issues found in 124 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: 291 pass / 0 fail
# 门禁全绿
```

### 全量

```sh
scripts/check.sh
# exit=0
# pytest: 1173 passed, 11 skipped in 636.02s (0:10:36)
# ruff check: All checks passed!
# ruff format --check: 315 files already formatted
# mypy: Success: no issues found in 124 source files
# pyright: 0 errors, 0 warnings, 0 informations
# node --test（glob）: 291 pass / 0 fail
# 门禁全绿
```

## 双轴 code-review

基线 `97184ff`，两个子代理并行审查（Standards / Spec），结论与处理如下。

### Standards 轴（2 hard / 7 judgement）

| # | 发现 | 处理 |
| --- | --- | --- |
| H1 | `CONTEXT.md` **加入频道** 的 `_Avoid_` 仍写「移出频道（本形态不做，人类面没有该端点）」，与新的删除语义读起来矛盾 | **已修**：改成「没有**单频道**移出入口；删除 Agent 会把它移出全部频道（见**成员**）」 |
| H2 | README 小节标题把新增的软删除决定挂到 ADR-0011 名下，而 ADR-0011 没有决定删除 | **已修**：标题去掉 ADR 归属；正文把 PATCH 的取舍指向 ADR-0011，删除的取舍指向本验收文档（不就地改写 ADR） |
| J3 | 同一条软删除政策在 CONTEXT / README / 三层 docstring / 验收文档各写一遍 | **不修**：本仓把文档当**数据契约的缓存**（`docs/agents/implementation.md`）、docstring 按层写职责与理由；不新增共享常量或生成文档 |
| J4 | `service.delete_agent` 是薄委托；`deleted` 字段名像布尔 | **部分修**：库层返回 `str` id（去掉未消费的 `deleted_at`）；薄委托与 `delete_reminder` 同形。`deleted` 字段名是票面钉死的 HTTP 形状，不改 |
| J5 | `db` 返回未消费的 `deleted_at`；前端响应后又本地 filter 一次 | **已修** 前者；后者保留为「无论响应形状都保证本地移除」的防御（票面明确要求移除本地镜像） |
| J6 | 禁用规则在初始渲染 / 就地重绘 / 点击守卫出现三次 | **不修**：三处生命周期不同（建节点、草稿脏时改既有节点、事件守卫），抽一个 helper 只会多一层间接，判定仍只有 `!online \|\| busy` 一条 |
| J7 | `taskMemberName()` 对查不到的 id 一律说「已删除」 | **已修**：只有 `state.ready`（成员表已就绪）才解释成「已删除」，否则用中性「成员」；补了断言 |
| J8/J9 | 验收文件名未按 `issue-<n>`；PENDING 读数 | **已修** PENDING（本文件已回填）；文件名沿用既有先例（`task-auto-continue-acceptance.md` 等），本票没有 GitHub issue 号 |

### Spec 轴（2 missing/partial / 1 scope-creep / 1 wrong）

| # | 发现 | 处理 |
| --- | --- | --- |
| M1 | 配置区仍有「启用状态」档位，写的是同一个 `draft.enabled`，用户仍能在配置区暂停 | **不修（记为边界）**：票面只要求移除「暂停 / 恢复」按钮与 `pauseNote`（`agentConfigSection` 不再渲染它们），「启用状态」是配置字段本身，标签与语义不同；删掉它会让 enabled 失去显式配置入口。见「遗留与边界」 |
| M2 | 1350px / 窄视口与「请求在途禁用」缺少证据 | **已修**：新增受控 promise 的在途禁用 DOM 用例；用 ego-browser 在真实 Studio 量 1350 / 900 / 420 三档（读数见上），越界与按钮相交均为 0 |
| S3 | 把 `error.agent_delete_unsupported` 改名为 `error.agent_replace_unsupported`，并把 PUT 404 文案改成「只能部分更新」 | **保留**：新增删除后，旧文案「Agent 不可删除」已变成假话；这个兜底只服务 PUT，键名与文案必须一起改。`PUT` 的状态码与行为仍是 404，未改契约 |
| W4 | `taskMemberName()` 把「成员表未就绪」误判成「已删除」 | **已修**：加 `state.ready` 判据，未就绪时回退中性「成员」；DOM 用例覆盖两态 |

## 遗留与边界

- 删除不回收正在进行的轮次 / 容器：本票只覆盖「软删除 + 移出成员面」；编排层没有为删除开新接缝。
- **「暂停」仍有两条入口**：危险区的暂停 / 恢复按钮，与配置区的「启用状态」档位。两者写同一个
  `draft.enabled`（既有语义）；票面只要求搬走按钮与代价说明，因此「启用状态」保留。若要让所有暂停
  都经过危险提示，需要再开一票改造配置字段，而不是在本票里删掉它。
- 已删除成员的历史消息仍按 `author.deleted` 渲染（既有机制）；任务历史保留原
  `assignee_member_id`，界面显示「已删除的成员」。DM target 与私信保留，不级联删除。
- 没有恢复入口：`deleted_at` 只写不回填，恢复是一张独立票。

## PR

_PENDING_
