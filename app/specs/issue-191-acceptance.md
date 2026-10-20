# issue #191 验收：看板任务状态流转（拖拽 + 卡片「流转」菜单）

- 票面正本：[issue #191](https://github.com/whutlichao/alibot/issues/191)
- 设计正本：[`app/specs/taskboard-transitions-design.md`](./taskboard-transitions-design.md)
- 证据截图：[`app/specs/issue-191-evidence/`](./issue-191-evidence/)
- 分支 / worktree：`issue-191-impl`（从 main `bbfaaa4` 切出）

## 一、判据 → 落点 → 命令与读数

### 判据 1：看板卡片可拖、合法列高亮、非法列红框、非法松手零请求且 toast 说明

- 落点：`app/alibot/studio/app.js` 的 `onBoardDragStart` / `onBoardDragOver` / `onBoardDragLeave` /
  `onBoardDrop` / `onBoardDragEnd`（`#taskColumns` 上委派五个事件）；
  `app/alibot/studio/app.css` 的 `.col.drag-over` / `.col.invalid-over` / `.card.dragging`。
- 命令：`node --test app/tests/taskboard_transitions_ui.test.cjs`
- 读数：10 个用例全过，其中
  - 「拖拽悬停：合法列 `.drag-over`、非法列 `.invalid-over`、当前列不标态」；
  - 「非法列松手不发任何请求，并给出一条说明「不是合法迁移」的 toast」（同时断言松手后落点态清掉）；
  - 「合法列松手发起一次 PATCH /api/tasks/{id}/status」（并断言 drop 之后的内核补发 click 不打开线程）。
- 浏览器读数（隔离数据目录，见 §三）：
  `legal hover {"dragging":true,"legalOver":true}`、`invalid hover {"legalOver":false,"invalidOver":true}`、
  `illegal drop {"patches":0} toast: 不能从「进行中」移到「完成」——不是合法迁移`。

### 判据 2：卡片「流转」菜单只列载荷 `allowed_transitions`，点击走一次 PATCH 并刷新

- 落点：`app.js` 的 `taskMoveButton` / `openTaskMoveMenu` / `onTaskMoveMenuKeydown` / `changeTaskStatus`；
  共享浮层在 `app/alibot/studio/index.html`（`#taskMoveMenu`）。
- 命令：`node --test app/tests/taskboard_transitions_ui.test.cjs`
- 读数：
  - 「卡片「流转」菜单只列载荷里的 allowed_transitions（顺序也照载荷）」用一份**静态五态表里不存在**
    的集合（`todo → done / in_review`）证明集合只能来自载荷；
  - 「点菜单项发起一次 PATCH /api/tasks/{id}/status，并重读看板」：`patches.length === 1`、
    `url === /api/tasks/t_1/status`、`body === {status:"in_review"}`，且 `GET /api/tasks` 被重读；
  - 浏览器读数：`menu {"items":["推进至进行中","关闭任务"],"targets":["in_progress","closed"],"expanded":"true"}`。

### 判据 3：`closed` 卡片无流转按钮、无合法落点

- 落点：`taskCardNode`（`targets.length` 才挂 `draggable` 与菜单按钮）、`onBoardDragStart`
  （无合法目标不成为拖拽源）。
- 命令：`node --test app/tests/taskboard_transitions_ui.test.cjs`
- 读数：「closed 卡片没有流转按钮、不是拖拽源，也没有任何合法落点」全过；浏览器读数：
  `closed {"hasMenu":false,"draggable":null,"dragging":false,"anyDropState":false}`。

### 判据 4：前端不再有迁移表副本，线程 chips 与看板同源

- 命令与读数：

  ```console
  $ grep -n "TASK_STATUS_TRANSITIONS" app/alibot/studio/app.js
  108:    集合的正本在后端：任务载荷的 `allowed_transitions` 由 `TASK_STATUS_TRANSITIONS` 派生。
  ```

  只剩一行**提到词条名**的注释，没有定义、没有取值表。线程 chips 与看板菜单都调
  `taskAllowedTransitions(task)`（`app.js:111`），该函数只读 `task.allowed_transitions`。

### 判据 5：载荷字段在三个读入口与三个 PATCH 响应中都存在且顺序固定

> **与票面的一处事实校正**：票面把「`GET /api/workspace` 的 `tasks`」列为第四条读路径，但
> `service.bootstrap()`（`app/alibot/service.py:107-125`）在 main 上**不带** `tasks` 键。
> 本票不给 `bootstrap` 加新读面（那是新载荷，超出「纯增量字段」）；等价的入口是
> `GET /api/tasks?channel_id=`（同一 handler、同一 `get_task` 派生）。详见设计正本 §五。

- 落点：`app/alibot/db.py` 的 `allowed_task_transitions()`（唯一派生点）与 `get_task()`（统一补字段）。
- 命令：

  ```console
  $ cd app && ../.venv/bin/python -m pytest tests/test_tasks.py -k allowed_transitions
  ```

- 读数：`2 passed, 12 deselected`。两条用例分别是
  - 「任务载荷带 allowed_transitions 于每条读入口」：全量 / 频道筛选 / 单条 + 改名 / 改派两个 PATCH 的 `task`；
  - 「allowed_transitions 恰好等于写路径接受的集合」：对每个状态逐个目标 `PATCH`，列出的被接受（200，
    且响应里的集合等于目标态的期望值），未列出的回 `validation_failed` 且后端事实不变。
  期望值由迁移表集合按 `TASK_STATUSES` 排序派生（不钉字面量，也不复用生产表达式的「过滤」写法）。
- 真实后端读数（隔离实例 `GET /api/tasks`）：

  ```text
  整理发布清单 todo ['in_progress', 'closed']
  为看板加入状态机拖拽校验 in_progress ['todo', 'in_review', 'closed']
  关闭旧版导出脚本 closed []
  ```

### 判据 6：`scripts/check.sh` 全绿；双轴 review 结论与处理落进验收文档

- 命令：`scripts/check.sh`（仓库根；唯一门禁入口）
- 读数（最终代码，`946 passed, 10 skipped`）：

  ```text
  == pytest（全量）            946 passed, 10 skipped in 494.65s (0:08:14)
  == ruff check                All checks passed!
  == ruff format --check       240 files already formatted
  == mypy（仓库根）             Success: no issues found in 101 source files
  == pyright                   0 errors, 0 warnings, 0 informations
  == node --test（glob）        201 tests, 201 pass, 0 fail
  门禁全绿
  ```

- **一次与本改动无关的既有 flake（如实登记）**：同一轮全量 pytest 曾出现 1 failed
  （`tests/test_agent_round.py::test_round_outcome_distinguishes_replied_ignored_and_failed`）。
  复现与定位：

  ```console
  $ cd app && for i in 1 2 3; do ../.venv/bin/python -m pytest tests/test_agent_round.py -k round_outcome_distinguishes; done
  1 passed / 1 failed / 1 passed
  $ cd /Users/apple/orca/alibot/app && for i in 1 2 3 4 5; do .venv/bin/python -m pytest tests/test_agent_round.py -k round_outcome_distinguishes; done
  1 failed / 1 failed / 1 passed / 1 failed / 1 passed
  ```

  它在**未改动的 main 工作副本**里同样间歇失败，与本票改动无因果关系（本票未触碰轮次结果 / 调度器）；
  按票面范围不修无关用例，最终门禁重跑为全绿（上表读数）。
- 双轴 review 见 §四。

### 判据 7：真实浏览器证据（菜单、键盘、拖拽三态、closed 卡片）

- 命令与准备见 §三；截图清单见 §五。

## 二、实现条目 → 落点速查

| 条目 | 落点 |
| --- | --- |
| 载荷字段派生 | `app/alibot/db.py`：`allowed_task_transitions()`、`get_task()` |
| 载荷契约用例 | `app/tests/test_tasks.py`：`test_task_payload_carries_allowed_transitions_on_every_read_path`、`test_allowed_transitions_are_exactly_what_the_write_path_accepts` |
| 删除前端表副本 | `app/alibot/studio/app.js`：`taskAllowedTransitions()`（无表、无默认表） |
| 卡片为拖拽源 + 菜单按钮 | `app.js`：`taskCardNode()`、`taskMoveButton()` |
| 共享「流转」菜单 + 键盘面 | `app.js`：`openTaskMoveMenu()`、`closeTaskMoveMenu()`、`onTaskMoveMenuKeydown()`、`onDocumentClickCloseTaskMoveMenu()`；`index.html`：`#taskMoveMenu` |
| 拖拽四态与写入 | `app.js`：`onBoardDragStart/DragOver/DragLeave/Drop/DragEnd`、`changeTaskStatus()` |
| 文案（zh/en 同键集） | `app/alibot/studio/i18n.js`：`board.cardMenuAria`、`board.cardMenuLabel`、`toast.transitionInvalid` |
| 样式（只消费 token） | `app/alibot/studio/app.css`：`.card.dragging`、`.col.drag-over`、`.col.invalid-over`、`.card-menu*` |
| DOM 用例 | `app/tests/taskboard_transitions_ui.test.cjs`（10 条） |
| 文档 | `app/specs/taskboard-transitions-design.md`、`app/README.md`（`task` 行）、`CONTEXT.md`（**任务看板**词条） |

## 三、浏览器证据的复跑步骤

```console
# 1) 起一个**隔离**的实例（不碰本机工作副本的数据）
$ cd app && ../.venv/bin/python -m alibot --port 8931 --data-dir /tmp/alibot-191-evidence

# 2) 造现场：一个频道 + 三张任务（todo / in_progress / closed）
$ API=http://127.0.0.1:8931
$ CID=$(curl -s -X POST $API/api/channels -H 'Content-Type: application/json' \
    -d '{"name":"设计系统重构","description":"","member_ids":[]}' | jq -r .channel.id)
$ T1=$(curl -s -X POST $API/api/channels/$CID/tasks -H 'Content-Type: application/json' -d '{"title":"整理发布清单"}' | jq -r .task.id)
$ T2=$(curl -s -X POST $API/api/channels/$CID/tasks -H 'Content-Type: application/json' -d '{"title":"为看板加入状态机拖拽校验"}' | jq -r .task.id)
$ T3=$(curl -s -X POST $API/api/channels/$CID/tasks -H 'Content-Type: application/json' -d '{"title":"关闭旧版导出脚本"}' | jq -r .task.id)
$ curl -s -X PATCH $API/api/tasks/$T2/status -H 'Content-Type: application/json' -d '{"status":"in_progress"}' > /dev/null
$ curl -s -X PATCH $API/api/tasks/$T3/status -H 'Content-Type: application/json' -d '{"status":"in_progress"}' > /dev/null
$ curl -s -X PATCH $API/api/tasks/$T3/status -H 'Content-Type: application/json' -d '{"status":"closed"}' > /dev/null
```

浏览器侧（ego-browser，`ego-browser nodejs`；本机网络噪声用 `env -u http_proxy …` 时 curl 也通）：

1. `goto http://127.0.0.1:8931/` → 点频道 → 点「任务」页签；
2. **菜单**：点 `[data-task][data-status="todo"] [data-task-menu]`，断言菜单项 = 该卡片的
   `allowed_transitions`（`["in_progress","closed"]`，文案 `推进至进行中` / `关闭任务`）；
3. **键盘**：Esc 关闭并还焦点 → `focus` 触发器 → `keyboard.press("Enter")` 打开 →
   `ArrowDown` → `document.activeElement` 是第二项（`关闭任务`）；
4. **拖拽三态**（合成 `DragEvent` + `DataTransfer`，走的是页面里真实的五个监听器）：
   `dragstart` 在 in_progress 卡片上 → `dragover` 到 `待审`（合法）= `.drag-over`；
   `dragover` 到 `完成`（非法）= `.invalid-over`；
5. **非法松手**：`drop` 到 `完成`，`window.fetch` 计数 `PATCH = 0`，toast 文案
   `不能从「进行中」移到「完成」——不是合法迁移`；
6. **closed**：`[data-status="closed"] [data-task]` 无 `[data-task-menu]`、`draggable` 为 `null`，
   `dragstart` 后逐列 `dragover` 任何列都不带落点态；
7. **合法松手**：todo 卡片 `drop` 到 `进行中`，`PATCH = 1`，看板重铺后卡片落在 `in_progress` 列。

> 截图时系统处于**深色**外观（应用默认跟随系统），因此本组证据是深色档；拖拽三态与菜单在深色档
> 同样可读（token 全部来自既有色位，无裸色值）。

## 四、双轴 code-review（Standards + Spec）

两轴分别由独立 reviewer 在 `git diff bbfaaa4...HEAD` 上跑，互不污染上下文。第 1 轮之后有一轮
修订（commit `6da19b4` 起）并做了第 2 轮复核。

### Standards 轴

第 1 轮（findings 与处理）：

| # | Finding | 类型 | 处理 |
| --- | --- | --- | --- |
| S1 | 缺 `issue-191-acceptance.md`（`docs/agents/implementation.md` 的交付要求） | 硬 | 本文件 |
| S2 | 验收证据不可复跑（只有「已通过」与截图） | 判断 | 本文件 §一/§三给命令与读数；浏览器步骤可重放 |
| S3 | `_expected_allowed()` 与生产 `allowed_task_transitions` 逐字同形（自证） | 判断 | 改为「迁移表集合按 `TASK_STATUSES` 排序」，并用写路径用例交叉验证 |
| S4 | `onBoardDragOver` 重复调用 / 重复条件 | 判断 | 收敛为一次取值 + `legal` / `sameColumn` |
| S5 | `changeTaskStatus` 两套「线程开着」判据 | 判断 | 只留 `state.activeTaskId` |
| S6 | `moreHorizontalIcon` 的替身分支 | 判断 | **保留**：既有 DOM 替身（`task_count_ui` / `i18n_ui`）没有 `createElementNS`，删掉会让它们在渲染看板时抛错 |
| S7 | `taskAllowedTransitions` 的 `[]` 兜底 | 判断 | **保留**：载荷缺失时「一个目标都不给」正是 D2（不回落本地表） |
| S8 | CONTEXT 用词「流转入口」偏离 glossary | 判断 | 改为「状态迁移入口」，「流转」只作菜单名 |
| S9 | CSS/HTML/设计正本三处复述交互规格 | 判断 | 保留：注释贴近代码，正本是设计文档 |

第 2 轮（复核 + 新 findings）：

| # | Finding | 类型 | 处理 |
| --- | --- | --- | --- |
| S10 | 激活键判定重复三次 | 判断 | 抽 `isActivationKey(event)`，三处共用 |
| S11 | 测试导出未使用的内部函数（与「只断言可观察行为」自相矛盾） | 判断 | 导出收窄为 `state / bindEvents / renderTaskBoard` |
| S12 | 键盘用例里一条内部状态填充断言 | 判断 | 删除 |
| S13 | 菜单项 30px 低于 `--tap-min: 44px`；触发器 32px 亦低于 | 判断 | 菜单项 `min-height: var(--tap-min)`；触发器用 `--control-h`（32px，本票判据）并在规则旁登记低于 tap-min 的理由（与 `.group-label button` 同一体例） |
| S14 | `app.js` 注释说「四个事件」实际五个 | 硬（事实错误） | 改为「五个事件」 |
| S15 | `board.cardMenuAria` 的 en 是动作式、zh 是名词式 | 判断 | en 改为 `Transition menu for “{title}”`，与 zh 同义 |

第 2 轮结论：**R1 六项全部解决；无硬违规**（CSS 只消费既有 token、无裸色值、Studio 无中文字符串字面量、
zh/en 同键集、pytest 未越层、`alibot-design-system/` 未改）。

### Spec 轴

第 1 轮（findings 与处理）：

| # | Finding | 类型 | 处理 |
| --- | --- | --- | --- |
| P1 | 缺验收文档（实施范围 4 / 验收判据 6） | 缺 | 本文件 |
| P2 | `GET /api/workspace` 的 `tasks` 不存在，判据 5 字面上的「四条读路径」未按字面满足 | 偏离 | 设计正本 §五披露 + 本文件 §一 判据 5 复述；不加 `bootstrap.tasks`（新读面，超出纯增量），以 `?channel_id=` 等价入口验收 |
| P3 | 触屏热区 32px 的「等效」写法是自我解释 | 判断 | 改为 `--tap-min` 的菜单项 + `--control-h` 的触发器，并登记 |
| P4 | 终态卡片仍 `draggable`，拖它会让整块看板亮红框，与 D5「不摆样子」相悖 | 越界感 | 终态不挂 `draggable`、`dragstart` 直接返回；用例与设计正本同步 |
| P5 | 未处理 `dragleave`，离开看板后最后一列一直亮 | 缺 | 补 `onBoardDragLeave` + 用例断言 |
| P6 | 竞态：落点按「重渲染后的载荷」判定 | 判断 | 拖拽/菜单在每次判定时读当前 `state.tasks` 快照；菜单项被别处改掉时给 `toast.transitionInvalid` 而不是静默（见 §二） |

第 2 轮（新 findings 与处理）：

| # | Finding | 类型 | 处理 |
| --- | --- | --- | --- |
| P7 | 设计正本 §八 仍写「四条读路径」，与 §五 自相矛盾 | 硬（文档自相矛盾） | 改为「三个读入口（全量 / 频道筛选 / 单条）+ 三个 PATCH」 |
| P8 | 「拖拽不触发点击」依赖内核不补 click，未加固 | 判断 | 加 `boardDrag.suppressClickUntil` 短窗口 + 用例断言 drop 后 tap 不打开线程 |
| P9 | 菜单项过期目标被前端守卫静默 return，与「走失败路径」有偏差 | 判断 | 改为给 `toast.transitionInvalid` 的可读原因；新增用例「菜单项的目标被别处改掉后…」 |

第 2 轮结论：**无缺失、无越界；P7–P9 已处理**。P2 属票面事实错误（`/api/workspace` 无 `tasks`），
按「以事实为准 + 披露偏离」处理。

## 五、截图清单

| 文件 | 内容 | 判据 |
| --- | --- | --- |
| `01-board.png` | 看板三张卡片：todo / in_progress 有 `⋯`，closed 没有 | 3 |
| `02-card-menu.png` | 打开卡片「流转」菜单，只有「推进至进行中」「关闭任务」两项 | 2 |
| `03-keyboard-menu.png` | 键盘 ↑↓ 把焦点移到第二项（可见选中底），Esc 可还焦点 | 2 / 7 |
| `04-drag-legal-hover.png` | 拖起 in_progress 卡片（卡片变淡），合法列「待审」= `.drag-over` | 1 |
| `05-drag-invalid-hover.png` | 悬停非法列「完成」= `.invalid-over`（合法列已清态） | 1 |
| `06-invalid-drop-toast.png` | 非法松手：`PATCH = 0`，toast「不能从「进行中」移到「完成」——不是合法迁移」 | 1 |
| `07-closed-card-no-entry.png` | closed 卡片无 `⋯`、`draggable=null`、拖起无落点态 | 3 |
| `08-legal-drop-moved.png` | 合法松手：`PATCH = 1`，卡片落到「进行中」列 | 1 / 2 |

## 六、遗留与后续

- **列表视图的行内流转入口**按票面「不做什么」留后续票。
- `GET /api/workspace` 是否该带 `tasks`（票面预设它有）是产品面的取舍，本票只披露事实、不改读面。
- 全量 pytest 的既有 flake（`test_round_outcome_distinguishes_replied_ignored_and_failed`）与本票无关，
  建议另开票修（调度器 drain 的时序假设），本票不越界改。
- `alibot-design-system/` 的看板切片还没有「流转菜单」形态；按票面留给后续同步。
