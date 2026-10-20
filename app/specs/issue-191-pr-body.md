## Summary

看板上的任务终于能「动着流转」了——拖拽与卡片「流转」菜单两个入口，**只暴露后端载荷里的合法迁移**，
非法落点零写入；前端那份手工同步的迁移表副本删掉了。

```text
任务载荷（db.get_task）
  allowed_transitions: string[]        # 由 TASK_STATUS_TRANSITIONS 派生，按 TASK_STATUSES 顺序；终态 []
        │
        ├─ 看板卡片 → draggable（有出边才可拖）
        │     dragover → .drag-over / .invalid-over / 当前列无态
        │     drop     → 合法: 1×PATCH / 非法: 0 请求 + toast
        │
        ├─ 卡片右上角「流转」菜单（共享浮层 #taskMoveMenu）
        │     只列 allowed_transitions；Tab / Enter / Space / ↑↓ / Esc / 点外
        │
        └─ 线程推进 chips（同一份集合，不再读本地表）

写路径不变：PATCH /api/tasks/{id}/status → update_task_status（actor 守卫 / 唤醒 / 预算 / 消息 kind 全不动）
```

改动的文件：

```text
app/
├── alibot/
│   ├── db.py                     # + allowed_task_transitions()；get_task 统一补字段
│   └── studio/
│       ├── app.js                # 卡片改 div role=button；拖拽五事件；共享菜单；删本地表副本
│       ├── app.css               # 拖拽三态 + 菜单（全部既有 token）
│       ├── i18n.js               # zh/en 同键集：board.cardMenuAria / board.cardMenuLabel / toast.transitionInvalid
│       └── index.html            # + #taskMoveMenu 共享浮层
├── tests/
│   ├── test_tasks.py             # + 两条载荷契约用例（派生自常量，不钉字面量）
│   └── taskboard_transitions_ui.test.cjs   # 新增 10 条 DOM 用例
└── specs/
    ├── taskboard-transitions-design.md     # 设计正本（D1–D6 / 交互 / 数据契约 / 不做什么）
    ├── issue-191-acceptance.md             # 判据 → 落点 → 命令与读数 + 双轴 review
    └── issue-191-evidence/                 # 8 张浏览器截图
```

## Evidence

- **Before:** 看板卡片是 `<button>`、没有 `draggable`、也没有任何流转入口（`app.js:1333`，
  `app.css:828` 明确写了「拖拽未实现」）。
  **After:** 卡片可拖、右上角有「流转」菜单，收起/展开与键盘操作真跑过（`02`/`03` 截图）：

  ![菜单](https://github.com/whutlichao/alibot/blob/issue-191-impl/app/specs/issue-191-evidence/02-card-menu.png)

- **拖拽三态**：合法列 `.drag-over`（`--accent-graphic` 边 + `--accent-soft` 底）、
  非法列 `.invalid-over`（`--error` 边）、被拖卡片变淡：

  ![合法落点](https://github.com/whutlichao/alibot/blob/issue-191-impl/app/specs/issue-191-evidence/04-drag-legal-hover.png)
  ![非法落点](https://github.com/whutlichao/alibot/blob/issue-191-impl/app/specs/issue-191-evidence/05-drag-invalid-hover.png)

- **非法松手零写入**：浏览器里计数 `PATCH = 0`，toast 说明事实：

  ![toast](https://github.com/whutlichao/alibot/blob/issue-191-impl/app/specs/issue-191-evidence/06-invalid-drop-toast.png)

- **终态不摆样子**：`closed` 卡片无按钮、`draggable=null`、拖起后任何列都没有落点态：

  ![closed](https://github.com/whutlichao/alibot/blob/issue-191-impl/app/specs/issue-191-evidence/07-closed-card-no-entry.png)

- **门禁读数**（`scripts/check.sh`，最终代码）：

  ```text
  pytest 全量        946 passed, 10 skipped
  ruff check         All checks passed!
  ruff format --check 240 files already formatted
  mypy               Success: no issues found in 101 source files
  pyright            0 errors, 0 warnings, 0 informations
  node --test        201 tests, 201 pass, 0 fail
  门禁全绿
  ```

- **载荷契约**：`pytest tests/test_tasks.py -k allowed_transitions` → `2 passed`；
  列出的迁移被 `PATCH` 接受、未列出的回 `validation_failed`；集合顺序为
  `todo, in_progress, in_review, done, closed`，终态 `[]`。

- **一处与票面的事实校正**：`GET /api/workspace`（`service.bootstrap()`）在 main 上**不带** `tasks` 键，
  票面把它列为第四条读路径不成立。本 PR 不给 `bootstrap` 加新读面，等价入口是
  `GET /api/tasks?channel_id=`（同一 handler、同一派生点），已在设计正本 §五与验收文档 §一 判据 5 披露。

## Merge Danger

**Door:** two-way——纯增量字段 + 前端呈现，回滚只需 revert 本 PR，没有 schema 变更、没有数据迁移、
写路径语义一个字节未动（迁移矩阵 / actor 守卫 / 唤醒 / 预算 / 消息 kind 原样）。

**Blast Radius:** 中低——影响面是任务载荷新增一个只读字段（旧消费方忽略即可）与 Studio 看板交互；
其它读面（列表视图行内入口、`alibot-design-system/` 切片）按票面未动。

Closes #191
