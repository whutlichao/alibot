# issue #209 验收：任务线程的待审批在外可见（卡片/列表行角标 + 任务页签圆点）

- 票面正本：[issue #209](https://github.com/whutlichao/alibot/issues/209)
- 分支：`issue-209-approval-badge`（worktree `/Users/apple/orca/workspaces/alibot/issue-209-approval-badge`，
  从 main `730b9c3` 切出）
- 采集时间：2026-10-21｜环境：macOS（aarch64），Node `v25.0.0`，Python 3.11.13（worktree `.venv`
  符号链接到主工作区 `.venv`）
- 浏览器证据：临时目录 `/tmp/issue-209-evidence/`（按票面要求不入库）

> 本文件是 Answer 的支撑材料：每条判据记录**命令**与**实际跑出来的读数**。红证据在隔离的基线副本
> `/tmp/issue-209-red` 上取得（四份 studio 文件取自 `main`/HEAD，`app.js` 的 md5 与主工作区
> `29e5d32747e097bcab26252993990ce3` 逐字节相同），**不动** worktree 的源码基线。

---

## 0. 前置 skill 的执行

前置 skill：`/Users/apple/.agents/skills/implement/SKILL.md`。它要求：先读规格 → 按 TDD 在约定接缝写
用例（先红后绿）→ 经常跑单文件用例、收尾跑全量 → 收尾调用 `code-review` → 提交到当前分支。
本票顺序逐条照做：

| 步骤 | 本票实际做的事 | 证据 |
| --- | --- | --- |
| 读规格 | GitHub issue #209 + 派单 TASK 的 Change 1–7 / Constraints / Observable acceptance 逐条拆到 Target 文件 | §1 |
| TDD 红 | 先往既有 `app/tests/approval_ui.test.cjs` 加 7 条 #209 用例，在基线副本上跑 | §2（`0` 条实现 → 7 pass / 7 fail） |
| 绿 | 实现后同一条命令 `14 pass / 0 fail` | §2、§3 |
| 单文件 / 全量 | `node --test app/tests/*.test.cjs`：`227 pass / 0 fail` | §3 |
| 全量门禁 | `scripts/check.sh` exit 0（pytest / ruff / mypy / pyright / node glob） | §3 |
| 浏览器 | ego-browser + 隔离数据目录，读到角标与圆点的可见文本 / aria | §4 |
| code-review | Standards + Spec 两轴并行子代理 | §5 |
| 提交 / PR | 一个实现 commit + 验收文档 commit；PR body 走 `--body-file` | §6 |

---

## 1. 改动面与读取路径

| 文件 | 动作 |
| --- | --- |
| `app/alibot/studio/app.js` | 新增全量 pending 读数 `loadPendingApprovals()`（`GET /api/approvals?state=pending`，不带 `target_id`）、单例周期刷新 `startApprovalRefresh()`（12s；`document.hidden` / `!state.online` 跳过；`approvalRefreshInFlight` 防叠加）、`taskApprovalBadgeNode()`、`renderTaskApprovalDot()`、`taskApprovalSignature()` / `syncApprovalBadges()`、`pendingApprovalCountForTarget()` / `taskPendingApprovalCount()` / `channelPendingTaskCount()`；`taskCardNode()` 与 `boardList()` 挂角标；`renderTabs()` 更新页签圆点；`renderApprovalSurfaces()` 作为 live 帧 / 快照 / 本地过期的统一出口；`loadWorkspace()` 进入工作区与断线重连时读一次全量；`bindEvents()` 启动单例定时器 |
| `app/alibot/studio/index.html` | `#tabTasks` 内新增 `<span class="tab-approval-dot" id="taskApprovalDot" data-od-id="task-approval-dot" hidden>` |
| `app/alibot/studio/app.css` | 新增 `.tab-approval-dot`（6px 圆点，`--warn-graphic`）与 `.card-approval`（角标，`--warn-soft` / `--warn-deep`），只消费既有 token |
| `app/alibot/studio/i18n.js` | 两份词表同键新增 `tabs.tasksApprovalDot`、`board.approvalBadge`、`board.approvalBadgeAria` |
| `app/tests/approval_ui.test.cjs` | 沿用 `vm` + `fetch` 替身 + DOM 替身；新增 7 条 #209 用例（快照 / 角标 / 圆点 / resolved / 600s 过期 / 周期刷新 / 不串台 / live 帧）。替身只补了「定时器带间隔」与 `document.hidden` 两个既有能力缺口，不新开框架 |
| `app/specs/issue-209-acceptance.md` | 本文件 |

**没有改动**（票面 Constraint 逐条）：后端 API / schema / `approval.py` / 事件流语义；审批卡本身的
呈现与提交逻辑；消息流 / 未读 / 搜索；不新增依赖；不在 `main` 工作副本改文件。

### 1.1 读取路径

```text
GET /api/approvals?state=pending   （全工作区，不带 target_id）
        │  loadPendingApprovals()
        ├─→ 按 record.target_id 并入 state.approvals（Map<targetId, Map<replyId, record>>）
        │      └─ 本次已知 pending 在快照里消失 ⇒ 标 expired（不伪造记录）
        ├─→ renderApprovalSurfaces()
        │      ├─ renderChannelApprovals()     频道 #approvalBoard（既有）
        │      ├─ renderTaskApprovals()        线程 #ttApprovalSection（既有）
        │      ├─ syncApprovalBadges()         仅派生集合变化时重铺看板/页签
        │      └─ syncApprovalTicker()         600s 倒计时（既有）
        └─→ 12s 单例定时器：可见 && 在线 && 无在途请求时才再读一次
```

- **归属只按 target**：`task.target_id` 的 pending 才进任务卡片/列表行/页签圆点；频道 target 的
  pending 只在 `#approvalBoard`。反向亦然。
- **「N」的判据**：`approvalDisplayState()`（后端终态优先，仍 pending 但越过 `requested_at + 600s`
  视为 expired），因此本地过期后角标与圆点在下一次渲染/刷新时消失。
- **live 帧**：`approval_required` / `approval_resolved` 仍走既有 `recordLiveEvent → absorbApproval*`，
  只是出口统一到 `renderApprovalSurfaces()`，于是角标/圆点与线程内的审批卡同一拍更新。

---

## 2. 先红后绿

### 2.1 红：基线副本（无实现）

隔离副本 `/tmp/issue-209-red`：四份 studio 文件取自 `HEAD`（= 未改动的 `main` 730b9c3），
只换上新的 `approval_ui.test.cjs`。

```console
$ md5 /tmp/issue-209-red/app/alibot/studio/app.js /Users/apple/orca/alibot/app/alibot/studio/app.js
MD5 (/tmp/issue-209-red/app/alibot/studio/app.js) = 29e5d32747e097bcab26252993990ce3
MD5 (/Users/apple/orca/alibot/app/alibot/studio/app.js) = 29e5d32747e097bcab26252993990ce3

$ cd /tmp/issue-209-red && node --test app/tests/approval_ui.test.cjs
✔ 频道视图从 GET /approvals?state=pending 恢复审批卡…
✔ 倒计时按 requested_at + 600s 递减…
✔ 允许提交全部 tool_call…
✔ 拒绝提交 confirmed=false…
✔ SSE approval_required 实时插入…
✔ 只渲染 {id,name,input}…
✔ 快照里 pending 消失…
✖ #209 全量 pending 快照（不带 target_id）驱动任务卡片与列表行「待审批 N」角标
✖ #209 当前频道有任务 pending 时任务页签圆点出现；DM 或该频道无 pending 时不出现
✖ #209 审批 resolved 后角标与圆点即时消失
✖ #209 越过 600s 上界后角标与圆点在下一次渲染/刷新后消失
✖ #209 周期刷新：在线可见时一轮恰好一次请求；离线 / 隐藏跳过；前一轮未返回不叠加
✖ #209 频道 target 的 pending 只属于 #approvalBoard，不产生任务角标或页签圆点
✖ #209 任务线程 live approval_required / approval_resolved 即时更新角标与圆点
ℹ tests 14   ℹ pass 7   ℹ fail 7
```

红读数要点：既有 7 条审批用例全过 ⇒ 接缝与替身没被新用例破坏；7 条 #209 用例全红 ⇒ 缺的正是
「全量 pending → 卡片/行角标 + 页签圆点 + 有界刷新」这条行为。完整日志：`/tmp/issue-209-red.log`。

### 2.2 绿：同一批用例

```console
$ node --test app/tests/approval_ui.test.cjs      # 第 2 轮（review 修补后）
ℹ tests 15
ℹ pass 15
ℹ fail 0
exit=0
```

---

## 3. 门禁

```console
$ scripts/check.sh          # review 修补后的最终一轮
== pytest（全量）
1046 passed, 10 skipped in 584.19s (0:09:44)
== ruff check
All checks passed!
== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
（无输出 = 全部已格式化）
== mypy（仓库根）
Success: no issues found in 114 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 228
ℹ pass 228
ℹ fail 0

门禁全绿
exit=0
```

完整日志：`/tmp/issue-209-check-final.log`（271 行）。全量 node glob 单独跑同读数：
`228 pass / 0 fail`，`duration_ms 3794.48`。第 1 轮实现后也跑过一次全绿：`/tmp/issue-209-check.log`
（pytest `598.85s`、node `227 pass`）。

---

## 4. 真实浏览器读数

- 服务：隔离数据目录 `/tmp/alibot-209-data`，`PYTHONPATH=app .venv/bin/python -m alibot --port 8899
  --data-dir /tmp/alibot-209-data`（只监听 loopback）。
- 造数：先在库里建频道 `#design`、两个任务，再在**服务运行中**直接插入 3 条 pending（进程启动会把
  残留 pending 标 expired——D19）：任务 target `tgt_57010409926d` 上 2 条，频道 target
  `ch_1dc8255f5f1a` 上 1 条。线程**从未打开**，前端没有任何 task target 的 live 帧。
- 浏览器：ego-browser（ego lite v0.6.0.12）打开 `http://127.0.0.1:8899/studio/`，点左栏 `#design`
  进入频道，再点「任务」页签。

读数（`page.evaluate` 读 DOM；第 2 轮 review 修补后重跑，页面重新加载过，服务日志确认只发
`GET /api/approvals?state=pending` 这一种审批快照请求）：

```json
{
  "tabDot": {
    "hidden": false,
    "aria": "该频道有 1 个任务在等待审批",
    "channelApprovalCard": "reply-209-live-ch"
  },
  "cardBadge": {
    "text": "待审批 2",
    "aria": "「修一处样式」有 2 条待审批",
    "cardAria": "修一处样式，待认领，2 条待审批，打开任务线程"
  },
  "otherTaskBadge": null
}
```

- 任务页签圆点：`hidden=false`，aria 名含任务数 `1`；DM / 无 pending 的隐藏分支由 §2 的 node 用例
  断言。
- 任务卡片角标：可见文本 `待审批 2`（含 N），角标自身 aria 名含任务标题与条数；卡片根节点
  （`role="button"`，可访问名的实际来源）也带「2 条待审批」，屏幕阅读器读得到。
- 另一张无 pending 的卡片 `otherTaskBadge=null`（不渲染空元素）。
- 频道 target 的 1 条 pending 只出现在消息流的 `#approvalBoard`（读数里的 `channelApprovalCard`
  与截图均可见），**没有**变成任务卡片角标或页签圆点 ⇒ 不串台；同时证明「删掉按 target 的初次
  读数、改由全量读数恢复」没有让既有频道审批卡回退（§5 处置 S2/P5）。

截图（临时目录，不入库）：

| 文件 | 内容 |
| --- | --- |
| `/tmp/issue-209-evidence/tab-dot-after-refactor.png` | 消息页：`#approvalBoard` 的频道审批卡 + 「任务 2 •」旁的小圆点 |
| `/tmp/issue-209-evidence/task-card-badge-after-refactor.png` | 任务看板：「修一处样式」卡片上的「待审批 2」角标；另一张卡片无角标 |
| `/tmp/issue-209-evidence/tab-dot.png` / `task-card-badge.png` | 第 1 轮同形态截图（保留作对照） |

---

## 5. 双轴 code-review

两轴各起一个子代理，对本票分支相对 `main` 的 diff 做独立 review（Standards + Spec），findings 与
处置如下。改动前的实现 commit 是 `bcf3201`；下表「处置」列的修补在随后的 review-fix commit 里。

### 5.1 Standards 轴（13 条：1 硬 + 12 判断）

| # | Finding | 类型 | 处置 |
| --- | --- | --- | --- |
| S1 | `app/specs/issue-209-acceptance.md` 还是 untracked，违反 `docs/agents/implementation.md`「每票落验收文档」 | 硬 | 本文件随 docs commit 入库；§6 记 commit |
| S2 | 「合并快照 + 消失 pending 标 expired」在 `loadApprovalsFor` / `loadPendingApprovals` / `reconcileApproval` 三处同形；且 `loadWorkspace` 开局同数据发两次请求 | 判断（最严重） | 提取 `pendingApprovalIndex()` / `applyApprovalSnapshot()` / `approvalRecordFingerprint()` 作为**唯一**合并写入口，两种读数共用；`loadWorkspace` 删掉按 target 的初次读数，只留全量（频道恢复由新用例 `r-boot-ch` 证明不回退）。`reconcileApproval` 是单条 409 对账，语义不同，保留 |
| S3 | 本地 600s 过期只在逐卡比对时探测；线程关着时没有卡，角标最多滞后 12s | 判断 | `refreshApprovalCountdowns()` 追加签名比对，派生集合变化也走 `renderApprovalSurfaces()`；用例改成**线程关着**（`activeTask=null`）并只推进 1s 倒计时 |
| S4 | `hasPendingApprovals()` 与 `pendingApprovalCountForTarget()` 重复遍历 | 判断 | `hasPendingApprovals()` 改成复用 `pendingApprovalCountForTarget()` |
| S5 | 全工作区周期读数未在 README / ADR 口径登记 | 判断 | `app/README.md` 补 3 行（票面 Target 允许最小改动）；不新开端点、不改事件语义，ADR-0020 的决策内容不动 |
| S6 | `.card-approval` 与既有胶囊同形；圆点 6px vs 7px；`--warn-*` 与审批卡 pending 的 `--working-*` 两套色族 | 判断 | **不修**：票面明确要求「样式用警告色」，`--warn-*` 是既有色族；角标与审批卡的状态胶囊是两种语义（前者是跨面提示、后者是卡内状态），不硬合并 |
| S7 | 用例直调 `renderTaskBoard/renderTabs`、用 `data-od-id`、按 10–15s 区间挑定时器 | 判断 | **保留**：沿用既有 `vm` + DOM 替身接缝；10–15s 本来就是票面给定的外部约束，挑定时器按票面区间而不是私有常量；断言只看 DOM / aria / 请求次数与 URL |
| S8 | `taskPendingApprovalCount` 像 Middle Man；两个「N」口径不同、名字相近 | 判断 | **保留**：函数名与注释分别写明「审批条数」与「有 pending 的任务数」，两个口径是票面要求；直接内联会让调用点更难读 |
| S9 | `syncApprovalBadges()` 为重铺一个小角标而重铺整块看板 | 判断 | **保留**：票面要求「派生集合变化时重铺看板/页签」；未变化时新用例断言看板卡片与审批卡节点**同一对象**，不重建、不收起流转菜单 |

其余（`index.html` / `i18n.js` 键集、措辞、裸中文、工具已强制的门禁）reviewer 未报问题。

### 5.2 Spec 轴（7 条）

| # | Finding | 类型 | 处置 |
| --- | --- | --- | --- |
| P1 | 「进入工作区 / 重连后读一次全量」没有用例；删掉 `loadWorkspace()` 那行测试仍全绿 | 缺 | 新增 `#209 进入工作区（loadWorkspace）…` 用例：真实入口、断言审批快照请求恰好 1 次且 URL 不带 `target_id`、任务角标出现、频道审批卡恢复 |
| P2 | 「反向亦然」未测：没有断言任务 target 的 pending 不进 `#approvalBoard` | 缺 | 全量快照用例补 `cardIn(#approvalBoard, "r-1") === null` |
| P3 | 验收文档 §5/§6 空、分支未推送、无 PR | 交付未完成 | 本轮补齐：§5 本表、§6 commit/PR、推送与 `Closes #209` |
| P4 | 未提交的 `app/README.md` 超出「只改 Studio 前端与它的 DOM 用例/验收文档」 | 越界（判断） | 票面 Target 明列 `app/README.md`「仅在审批读面/外显行为需要补一句时才改，最小改动」；本次正好补审批读面的 3 行，保留 |
| P5 | `applyApprovalSnapshot` 重构 / 删掉 `loadWorkspace` 的按 target 读数超出「补全量读数」的最小范围 | 越界（判断） | 重构是对 S2 的处置；行为等价由 P1 用例自证（一次全量读数恢复频道审批卡）；不新增/删除任何 HTTP 端点 |
| P6 | 线程关着时 600s 过期不驱动角标/圆点，只能等 12s 轮询 | 实现错误（最严重） | 已修：见 S3；用例覆盖「线程关着 + 只有 1s 倒计时」 |
| P7 | 12s tick 无条件 `renderApprovalSurfaces()`，pending 未变也重建审批卡，可能丢掉正在点的按钮 | 实现错误 | `applyApprovalSnapshot()` 返回是否真的变了；周期 tick 只在变化时重铺，入口/重连显式 `{render:true}`；用例断言未变化周长下卡片节点同一 |
| P8 | 角标 aria 只在属性层：卡片根 `role=button` / 列表行 `button` 的 aria 名会覆盖子树 | 实现错误 | 新增词条 `board.cardApprovalAria`，有 pending 时把条数并进卡片/列表行**根节点**的 aria 名；角标自身 aria 保留。用例断言根 aria 含「2 / 待审批」 |

**结论**：两轴均无遗留 P0/P1；S6–S9 为判断项，已给出可检验的不修理由（票面要求 / 既有接缝 /
未变化不重建的用例证据）。P1–P8 全部有对应用例或文档落点。

---

## 6. 交付物

| 项 | 值 |
| --- | --- |
| 分支 | `issue-209-approval-badge`（从 `main` 730b9c3 切出） |
| 实现 + 用例 | `bcf3201` feat(studio): 任务待审批角标与页签圆点（#209） |
| review 修补 | `0a7e357` fix(studio): 待审批提示共用快照写入口并补齐可访问名（#209） |
| 验收 / PR 文档 | 本文件与 `app/specs/issue-209-pr-body.md`（随 docs commit 入库） |
| PR | 待创建（`gh pr create --body-file app/specs/issue-209-pr-body.md`，标题 `fix(studio): 任务线程的待审批在外不可见——卡片/列表行角标 + 任务页签圆点`，正文带 `Closes #209`） |
| worktree | `git status --porcelain` 为空（收尾核对） |

---

## 7. Answer 摘要

1. **做了什么**：用后端已有的 `GET /api/approvals?state=pending` 全量读数为任务看板卡片、列表行与
   `#tabTasks` 页签补了「待审批 N」角标 / 圆点（含 zh+en 词条、根节点可读 aria 名），并把 live 帧、
   进入工作区、断线重连与 12s 有界轮询统一到同一个快照写入口 / 外显出口（离线或页面隐藏跳过、
   在途不叠加、快照未变化不重建 DOM）。
2. **发现了什么**：双轴 review 发现并修掉了三个真实缺口——线程关着时本地 600s 过期不驱动角标
   （改为派生集合签名检测）、12s 轮询无条件重建审批卡（改为快照指纹变化才重铺）、角标 aria 被
   卡片根 `role=button` 覆盖（改为把条数并进根节点可访问名）；其余判断项与不修理由见 §5。
3. **遗留**：无功能遗留；合并按 §6 的 PR 流程执行（`Closes #209`），浏览器截图按票面要求留在
   `/tmp/issue-209-evidence/`、未入库。
