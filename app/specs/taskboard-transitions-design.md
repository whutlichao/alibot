# 看板任务状态流转：拖拽 + 卡片「流转」菜单（只暴露合法迁移）

- 票面正本：[issue #191](https://github.com/whutlichao/alibot/issues/191)
- 状态：实施轮（本票同时落设计正本与实现）
- 范围：**呈现面**的流转入口 + 任务载荷的派生字段；写路径语义一个字节都不改

## 一、问题与现状

用户反馈（2026-10-20）：**看板里既拖不动任务，也没有下拉能把任务推到下一个状态**；并指出
「任务状态不能随意从一个状态变为另一个状态，需要进行设计」。

在 main `bbfaaa4` 上核实到的事实（行号是当时的读数）：

| # | 事实 | 证据 |
| --- | --- | --- |
| 1 | 后端**已强制**五态状态机：表外迁移一律 `validation_failed` | `app/alibot/db.py:178`（`TASK_STATUSES`）、`:179`（`TASK_STATUS_TRANSITIONS`）、`:2247`（表外拒绝） |
| 2 | 认领是唯一的**非人类**迁移路径，且复用同一张表（`todo → in_progress` 那一格） | `db.py:201`（`CLAIM_START_STATUS`）、`db.py:2415`（`claim_task`） |
| 3 | 看板卡片**没有任何流转入口**：卡片是 `<button>`，注释写明不带 `draggable` | `app/alibot/studio/app.js:1333` |
| 4 | 样式层显式**排除**拖拽三态类（因为当时没有行为支撑） | `app/alibot/studio/app.css:828` |
| 5 | 唯一能改状态的入口是任务线程 dock 的推进 chips（点开卡片才看得到） | `app.js:1510`、`app.js:3210`（`changeTaskStatus`） |
| 6 | 前端自带一份迁移表**副本**，与后端表靠手工同步 | `app.js:104-111` |
| 7 | 设计系统原型有拖拽三态，但**没有**下拉菜单 | `alibot-design-system/screens/task-board.html:209-260` |

结论：「拖不动、没有下拉」属实；「状态不能随意变」在后端已经成立——缺的是**看板侧的合法迁移交互**
与**单一事实来源**（前端那份副本会漂移）。

本轮落地后，上表 3 / 4 / 6 三条现状被本票改变（见「验收映射」）。

## 二、五态矩阵（D1：沿用，不改）

| 从 \ 到 | todo | in_progress | in_review | done | closed |
| --- | --- | --- | --- | --- | --- |
| `todo` | — | ✅ | — | — | ✅ |
| `in_progress` | ✅ | — | ✅ | — | ✅ |
| `in_review` | ✅ | ✅ | — | ✅ | — |
| `done` | — | ✅ | — | — | ✅ |
| `closed` | — | — | — | — | —（终态） |

`closed` 没有出边，因此它是**终态**：卡片不渲染流转按钮，拖拽悬停也不出现任何合法落点。

## 三、决策

- **D1 状态机不动。** 迁移矩阵、五个状态、`update_task_status` 的 actor 守卫（互审 / 自审批拒绝）、
  以及「改状态只落一条 `system` 消息、永不唤醒、不扣跳」的既有口径全部原样保留（ADR-0006 / ADR-0026）。
- **D2 合法集合的唯一来源是后端。** 任务载荷新增 `allowed_transitions: string[]`，由
  `TASK_STATUS_TRANSITIONS` 派生、按 `TASK_STATUSES` 顺序输出；前端**删除**自己的迁移表副本，
  看板拖拽、卡片菜单、线程 chips 一律读载荷。理由：这是「状态不能随意变」的**呈现面契约**——
  前端不持正本，漂移面归零。
- **D3 两个入口都做。** 拖拽（指针设备 / 桌面）与卡片「流转」菜单（触屏 / 键盘 / 精确选择）。
  两者命中同一条受守卫的写路径 `PATCH /api/tasks/{id}/status`，不做第二套语义。
- **D4 非法落点零写入。** 拖到非法列：该列显示非法态（`.invalid-over`）、松手**不发起任何请求**、
  toast 说明「不是合法迁移」；拖到当前列：无动作；拖到合法列：一次 PATCH，成功后刷新看板与
  （若打开的）任务线程。**不做乐观更新**——失败时用户看到的是后端事实。
- **D5 终态不摆样子。** `closed` 无出边 ⇒ 卡片不渲染流转按钮；拖起后每一列都只能是非法落点；
  线程里仍显示既有的「无可用推进」说明。
- **D6 不动其它口径。** 唤醒 / 预算 / 未读 / 消息 kind / schema / 列表视图行内入口一律不变。

## 四、交互规格

### 4.1 拖拽（看板列之间）

- 卡片根节点是 `<div class="card" role="button" tabindex="0" draggable="true">`：`<button>` 里不能再嵌
  菜单按钮，`<div>` 作拖拽源也更稳；Enter / Space 仍打开线程（键盘语义显式补上）。
- 拖起时卡片呈 `.dragging`（`opacity: .4`），并收起可能开着的流转菜单。
- 悬停列三态：**合法** `.drag-over`（`--accent-graphic` 边 + `--accent-soft` 底）、
  **非法** `.invalid-over`（`--error` 边）、**当前列**不标态（拖回原列是无动作，不是错误）。
- 合法判定**只读**该任务的 `allowed_transitions`，不读本地表、不做「同状态不迁移」之外的推断。
- 松手：合法 → 一次 PATCH；非法 → toast（`toast.transitionInvalid`，文案含「从哪个状态到哪个状态」）
  且**零请求**；当前列 → 无动作。
- `dragend` / 松手 / 任何重铺都会清掉落点态（`.drag-over` / `.invalid-over` / `.dragging`）。
- 离线：`dragstart` 不记录被拖任务，松手不发请求（与菜单的离线纪律一致）。

### 4.2 卡片「流转」菜单

- 每张卡片右上角一个图标按钮（lucide `more-horizontal` 同族的三点几何），
  `aria-haspopup="menu"`、`aria-expanded`、`aria-controls="taskMoveMenu"`，
  点击 `stopPropagation`（点菜单不打开线程；看板的点击委派也先判 `[data-task-menu]`）。
- 菜单是**一个共享浮层** `#taskMoveMenu`：`position: fixed`、按触发器 rect 定位（下方优先，
  空间不足翻到上方），因此不被 `.board` 的 `overflow: auto` 裁切；`role="menu"`，子项
  `role="menuitem"`（`<div tabindex="-1">`，激活路径只有一条，不会被原生 click 二次触发）。
- 菜单里**只列** `allowed_transitions` 的目标（顺序即载荷顺序），文案复用既有动词词条
  `task.transition.<target>`（与线程 chips 同一套说法）。
- `closed` 卡片没有这个按钮（无合法目标 ⇒ 不摆一个点不出东西的入口）。
- 键盘：Tab 聚焦按钮；Enter / Space 打开并把焦点移到第一项；↑ / ↓ 循环移动；Esc 关闭并把焦点
  还回触发器；Enter / Space 激活当前项；点外部、滚看板、看板重铺都会关闭。
- 触屏：菜单是唯一入口，热区沿用既有 `--icon-btn`（30px 控件 + 卡片内边距，等效 ≥ 32px）。
- 离线（`state.online === false`）：按钮 `disabled`，打开函数直接返回。
- 打开菜单的卡片若被重渲染（SSE / 刷新）：`renderTaskBoard()` 开头统一关闭菜单，重开时按新载荷重建。

### 4.3 失败与竞态

- PATCH 失败（`validation_failed` / 网络）：toast 带后端可读文案（`toast.statusUpdateFailed`），
  随后重读看板；线程仍开着才重铺线程——用户看到的是后端的当前态，而不是本地猜测。
- 菜单/拖拽打开期间任务状态被他处改变：目标集合按**重渲染后**的载荷重新判定；
  迟到的请求被后端拒绝即走上面的失败路径。

## 五、数据契约

| 载荷 | 新增字段 | 说明 |
| --- | --- | --- |
| `task`（`GET /api/tasks`、`GET /api/tasks/{id}`、三个 PATCH 的 `task`） | `allowed_transitions: string[]` | 当前态的全部合法目标，顺序固定 `todo, in_progress, in_review, done, closed`；终态空数组 |

- 派生点只有一处：`db.allowed_task_transitions()`（`app/alibot/db.py:188`），消费同一张
  `TASK_STATUS_TRANSITIONS` 表；任务载荷由 `get_task` 统一补字段（`db.py:2188`），
  于是列表、单条与三个写路径的响应**同源**。
- 机器可读词，不译；UI 文案仍走前端 i18n（`task.status.*` / `task.transition.*`）。
- 纯增量：旧消费方忽略该字段不受影响。
- **与票面的一处事实校正**：`GET /api/workspace`（`service.bootstrap()`，`app/alibot/service.py:107-125`）
  在 main 上**不带** `tasks` 键——工作台快照只有成员 / 频道 / 私信 / 未读数与 provider。
  票面把「`GET /api/workspace` 的 `tasks`」列为第四条读路径，实际上那条路径不存在。
  本票**不**给 `bootstrap` 加 `tasks`（那会是新的读面与新的载荷，超出「纯增量字段」的范围）；
  等价的第四条读路径由 `GET /api/tasks?channel_id=` 承担（同一 handler、同一 `get_task` 派生）。
  验收用例断言这两条入口（全量 + 频道筛选）与单条读路径给出同一个集合。

## 六、实现约束（踩过的坑）

1. 卡片根从 `<button>` 改成 `<div role="button">`；Enter / Space 打开线程由 `#taskColumns` 的
   keydown 委派补上（`app.js:8081-8088`）。
2. 卡片菜单按钮点击 `stopPropagation`，看板点击委派先判 `[data-task-menu]`（`app.js:8071-8080`）。
3. 菜单是共享浮层（`index.html:549`），`fixed` 定位，不靠 `.board` 的裁剪。
4. 键盘：打开进第一项、↑/↓ 循环、Esc 还焦点、点外部 / 滚动 / 重铺时关闭（`app.js:1507-1545`）。
5. 拖拽判定只读 `task.allowed_transitions`；`dragstart` 记任务 id（`dataTransfer` + 内存兜底）、
   `dragover` 逐列判态、`drop` 非法 → toast 且零请求、`dragend` 清态（`app.js:1561-1617`）。
6. `renderTaskBoard()` 开头统一关闭菜单（`app.js:1685-1688`）。
7. 样式只消费既有 token（`.col.drag-over` / `.col.invalid-over` / `.card.dragging` /
   `.card-menu*`，`app.css:873-923`），不新造色位、不写裸色值。
8. 新词条 zh/en 同键集（`board.cardMenuAria` / `board.cardMenuLabel` / `toast.transitionInvalid`）。
9. `allowed_transitions` 顺序固定 `TASK_STATUSES` 顺序；无出边时是 `[]`（不是 `null`）。
10. pytest 只断言 HTTP 载荷与写路径行为（`app/tests/test_test_layering_guard.py`）：不 import
    私有名、不读 Studio 源码、不钉状态表字面量——断言从常量或写路径结果派生。

## 七、不做什么

- **不改**迁移矩阵、不新增状态、不改 `update_task_status` 的 actor 守卫（互审语义原样保留）。
- **不改**列表视图的行内流转入口（`boardList()` 仍是只读行；记后续票）。
- **不更新** `alibot-design-system/` 原型切片（设计系统是参考物；菜单形态记录在本文件与
  `app.css` 注释里，切片同步留后续）。
- 不引入拖拽库、不新开前端测试框架、不做乐观更新、不加 schema 迁移、不改 `GET /api/workspace`。
- 不给任务增加「创建者」或第二套状态词表。

## 八、验收映射

| 验收判据（issue #191） | 落点 | 证据 |
| --- | --- | --- |
| 卡片可拖、合法列高亮、非法列红框、非法松手零请求 + toast | `app.js:1561-1617`、`app.css:902-905` | `app/tests/taskboard_transitions_ui.test.cjs`（悬停三态 / 非法松手 / 合法松手） |
| 卡片菜单只列载荷 `allowed_transitions`，点击走一次 PATCH 并刷新 | `app.js:1400-1545` | 同上（用一份静态表里不存在的集合证明载荷驱动） |
| `closed` 卡片无按钮、无合法落点 | `app.js:1363-1398`、`app.js:1575-1609` | 同上（终态用例） |
| 前端不再有迁移表副本，线程 chips 与看板同源 | `app.js:111`、`app.js:1770` | `grep -n TASK_STATUS_TRANSITIONS app/alibot/studio/app.js` 无定义 |
| 载荷字段在四条读路径与三个 PATCH 响应中都存在且顺序固定 | `db.py:188`、`db.py:2188` | `app/tests/test_tasks.py` 两条契约用例 |
| `scripts/check.sh` 全绿；双轴 review 结论落验收文档 | 全仓 | `app/specs/issue-191-acceptance.md` |
| 真浏览器：菜单可开、键盘可走、拖拽三态可观察 | Studio | `app/specs/issue-191-evidence/*.png` |
