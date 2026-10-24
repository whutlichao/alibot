# 删除 Agent 后私信实时进入「已归档」：验收记录

## 来源

Issue [#265](https://github.com/whutlichao/alibot/issues/265)：在 Agent 详情页删除一个 Agent 后，
左栏该 Agent 的私信行仍留在「私信」段，刷新页面后才进入「已归档」。

根因：`deleteAgent()` 成功后只覆盖 DELETE 响应里的 `agents` / `members`，从未重读 `state.dms`，
于是本地镜像里的 peer 仍没有 `deleted_at`；`dmIsArchived()` 只认这个后端事实，整页重载拉
`GET /api/dms` 之后才变化。

基线：`2a359ca`（PR #262 合并后的 main head）。分支 `issue-265-impl`。
本票只动 Studio 表现层、一个 DOM 用例文件与文档；不改后端 `.py`、DB/schema、DELETE 响应形状。

## 判据 → 落点

| 判据 | 落点 |
| --- | --- |
| 删除成功后重读私信正本（`GET /api/dms`），不新增后端字段、不伪造 `peer.deleted_at` | `app.js` `deleteAgent()` 成功路径：`await refreshDmList()`（既有 `GET /api/dms` 读路径） |
| 私信行立即从 `#dmList` 移到 `#dmArchiveList`，`#dmArchiveGroup` 由隐藏变可见 | 既有 `renderDms()` / `renderDmArchiveSection()`，由重读后的 `state.dms` 驱动 |
| 「已归档」段仍默认折叠（`aria-expanded="false"`） | 未改 `state.dmArchiveExpanded`，`renderDmArchiveSection()` 原样 |
| 当前 target 正是这条私信时，删除成功后 composer 立即只读：正文 / 附件 / @提及 / 作为任务发送禁用，hint = `composer.archivedHint` | 既有 `closeAgentDetail()` → `render()` → `renderHeader()` → `syncComposerLock(isArchivedDm)` / `syncMentionHint()` |
| 删除失败路径不额外请求 `GET /api/dms`，行留原处，弹窗与错误文案不变 | `refreshDmList()` 只在 try 的成功分支里调用 |
| `refreshDmList()` 读失败保留旧镜像，不阻塞删除成功与 toast | 既有 catch 语义不变；新增形状校验：非数组 `dms` 不覆写镜像 |
| 既有「确认删除只发一次 DELETE」仍成立 | 用例断言不变；成功路径唯一新增请求是 `GET /api/dms` |
| 不改 `dmIsArchived()` 判据、不改归档段展开态存储 | diff 未触碰 |

## 改动清单

| 层 | 文件 | 改动 |
| --- | --- | --- |
| 前端 | `app/alibot/studio/app.js` | `deleteAgent()` 成功路径在关闭弹窗后 `await refreshDmList()`，再走 `closeAgentDetail()` / `render()`；`refreshDmList()` 增加 `Array.isArray(payload.dms)` 形状校验（畸形应答保留旧镜像） |
| 测试 | `app/tests/agent_detail_ui.test.cjs` | 新增 3 条 DOM 回归用例：归档移段（含段可见 / 默认折叠）、当前私信立即只读、失败路径不重读 |
| 文档 | `app/specs/issue-265-acceptance.md` | 本文件 |

## 红证据（修复前，隔离基线副本）

命令：

```sh
scripts/red-evidence.sh 2a359ca app/tests/agent_detail_ui.test.cjs
```

读数（exit code = **1**）：

```
基线：2a359ca (2a359ca)
测试：app/tests/agent_detail_ui.test.cjs（取自当前工作区）
基线 app.js md5：86800f4d3c856a54706f44b7a29d6f67
证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.7DtVQS

ℹ tests 53
ℹ pass 51
ℹ fail 2
```

两条失败正是新用例：

```
✖ 删除 Agent 成功后重读私信正本：该私信行立即移入「已归档」段，无需刷新页面
  AssertionError: 「私信」段不再有该行（未刷新页面）  actual: [FakeNode]  expected: null
✖ 删除的正是当前私信时：删除成功后 composer 立即进入已归档只读态
  AssertionError: 正文禁用  false !== true
```

第三条（失败路径不重读）在基线即通过：它锁的是「不要新增行为」。

## 绿证据（修复后）

```sh
node --test app/tests/agent_detail_ui.test.cjs
# ℹ tests 53 / ℹ pass 53 / ℹ fail 0

node --test app/tests/rail_identity.test.cjs app/tests/dock_dm_ui.test.cjs app/tests/i18n_ui.test.cjs
# ℹ tests 75 / ℹ pass 75 / ℹ fail 0

scripts/check.sh --studio
# pytest（Studio 窄档）：20 passed
# ruff check：All checks passed!
# ruff format --check：326 files already formatted
# mypy：Success: no issues found in 124 source files
# pyright：0 errors, 0 warnings, 0 informations
# node --test（Studio DOM 用例族 glob）：tests 319 / pass 319 / fail 0
# 门禁全绿
```

合并 `origin/main`（`5a95507`，含 #263）后复跑 `scripts/check.sh --studio`：同样全绿，
读数仅 `ruff format --check` 变成 `327 files already formatted`（多出 main 的
`app/specs/issue-263-acceptance.md`），其余逐项不变。

本票不跑全量 pytest（`--studio` 已覆盖适用门禁；CI 跑全量）。

## 决策

1. **重读时机**：`await refreshDmList()` 放在 `closeAgentDeleteModal()` 之后、`closeAgentDetail()` /
   `render()` 之前。这样关闭详情那一次整体渲染已经拿到新的私信镜像，归档移段与 composer
   只读态在同一拍落定，不需要第二次 `render()`，也不会先画一帧「未归档」。
2. **新增请求的边界**：删除成功路径唯一新增的是既有的 `GET /api/dms`；失败路径零新增
   （`refreshDmList()` 只在 try 内成功分支调用）。
3. **`refreshDmList()` 形状校验**：它的调用点从「流事件」扩到「删除成功」，而删除成功路径
   一旦在渲染中抛错会被 `deleteAgent()` 的 catch 当成删除失败（错误的 toast + 弹窗重开）。
   因此非数组 `dms` 时直接保留旧镜像——与既有「读失败保留旧镜像、不伪造」同一条语义。
4. **折叠默认值**：本票不改 `state.dmArchiveExpanded`。段可见后仍按既有语义默认折叠，
   归档行已就位（`#dmArchiveList` 的 `hidden` 只控制展示）。
5. **toast 时序**：成功 toast 在重读之后发出；`GET /api/dms` 慢时 toast 与界面落位一起晚一次
   RTT，不引入「先报成功再挪段」的中间态。
6. **不做本地「有没有这条私信」的守卫**：`onStreamMessage()` 里 `refreshDmList()` 带
   `state.dms.some(...)` 守卫，是因为未读计数只对 DM 有意义；删除这里**刻意无条件重读**——
   本地镜像过期时（别的标签页刚建了这条私信）恰恰需要正本说了算。代价是每次删除多一次
   `GET /api/dms`，单次请求量小，换掉「按旧镜像猜有没有 DM」的隐式前提。
7. **读失败静默保留旧镜像**：票面明确要求沿用 `refreshDmList()` 的既有 catch 语义——删除已成功、
   toast 照常，归档段要等下一次整页重载才变化；不把失败的读伪装成删除失败。

## Code review（双轴，对 `2a359ca` 的真实 diff）

| 轴 | 结论 |
| --- | --- |
| Standards | 无硬违规。判断题：成功路径的 `await` 会推迟关闭详情与 toast（已把注释口径改成「最多晚一次 RTT」）；`refreshDmList()` 的守卫差异（已在决策 6 写明取舍）；测试替身重复的 500 应答体（已抽到 `dmArchiveFetch()` 并让既有失败用例复用它）；替身内部变量 `state` 与产品 `state` 同名（已改名 `backend`）。 |
| Spec | 规格逐条命中，无缺失、无 scope creep。确认 `dmIsArchived()` / `state.dmArchiveExpanded` / DELETE 响应形状均未改动；失败路径 `/api/dms` 计数为 0。 |

review 提出的两条**超出本票范围、未顺手改**的既有风险（记录在此，不作为本票缺陷）：

1. `deleteAgent()` 的 try 覆盖了其后的 `closeAgentDetail()` / `render()`：DELETE 已成功但渲染抛错时
   会走进 catch，报「删除失败…未改动任何数据」，属于误导性文案。这是基线就有的结构，本票
   `refreshDmList()` 内部自带 catch（不会抛），没有扩大暴露面。
2. `renderDms()` 本身不刷新 header / composer 锁；本路径靠紧随其后的 `closeAgentDetail()` /
   `render()` 兜住。若未来新增「只重读私信、不整体渲染」的入口，需要自己再刷一次输入区。

## 遗留

- 全量 pytest 由 CI 跑（本票按窄档门禁验收）。
- 归档段默认折叠是既有产品语义，本票刻意未动；若后续要「删除后自动展开」是另一张票。
- 上面 review 记录的两条既有风险：需要单独立票时再改，本票不扩大范围。
