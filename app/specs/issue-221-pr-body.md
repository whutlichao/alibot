## Summary

任务线程（右侧 dock）不再把「关联消息」铺成第二段正文。线程输入框发送时必然带 `task_ref.task_id`，
而 `task_ref` 必须与消息同 target（`CONTEXT.md` **指针消息**），所以「带 `task_ref` 的非 system 消息」
恒为「讨论」的子集——两段并排只会让人以为漏了去重。头部副行改用「讨论」的计数（与 `#ttReplyTitle`
同一份），指针 chip 仍在讨论里就地显示；「讨论」「状态变更记录」两段都保留。

```diff
 app/alibot/studio/
 ├── index.html      # 删「关联消息」的 .d-sec 与 #ttLinked
 ├── app.js          # 删关联消息渲染与 linkedTaskMessages；#ttHeadSub 改用 replies.length
 ├── app.css         # 删 .tt-linked* 五条规则
 └── i18n.js         # threadHeadSub →「#频道 · 讨论 N 条」；删 linkedEmpty / dock.linked
 app/tests/
 ├── task_thread_ui.test.cjs   # 新增 5 条 DOM 回归（沿用既有 vm + fetch/DOM 替身，不新开框架）
 └── approval_dom_harness.cjs  # 只多暴露 catalogs（词条正本），装置行为未改
```

```text
renderTaskThread
  history = taskMessages.filter(kind === 'system')      → 状态变更记录
  replies = taskMessages.filter(kind !== 'system')      → 讨论（并集，含带 task_ref 的消息）
  #ttHeadSub = 「#频道 · 讨论 replies.length 条」       → 与 #ttReplyTitle 同一份计数
- linked  = taskMessages.filter(task_ref === task.id)   → 已删除：恒为 replies 的子集
```

后端、schema、`CONTEXT.md` / ADR、设计系统原型零改动；`task_ref` 与 `.task-ref-chip` 的语义不变。

## Evidence

- **Before**（基线 `61ef770`，隔离 worktree + 独立数据目录 `/tmp/alibot-issue221/base-data`、端口 8898；
  通过 HTTP 建频道/任务后 `POST /api/tasks/{id}/messages` 发两条 `task_ref` = 任务 id 的消息）：

  ```text
  #ttHeadSub        = #验收频道 · 关联消息 2 条
  #ttScroll 段落    = 审批 / 关联消息 / 讨论 · 2 条 / 状态变更记录
  「第一条指针回复」在 dock 内出现 2 次（关联消息 1 + 讨论 1）
  ```

  ![baseline](app/specs/issue-221-evidence/baseline-61ef770-thread.png)

- **After**（同一脚本、独立数据目录 `/tmp/alibot-issue221/new-data`、端口 8797）：

  ```text
  #ttHeadSub        = #验收频道 · 讨论 2 条
  #ttScroll 段落    = 审批 / 讨论 · 2 条 / 状态变更记录
  「第一条指针回复」在 dock 内出现 1 次；#ttReplies 内 .task-ref-chip = 2
  ```

  ![after](app/specs/issue-221-evidence/after-issue221-thread.png)

- **红证据 → 绿**（`scripts/red-evidence.sh 61ef770 app/tests/task_thread_ui.test.cjs`）：
  基线 app.js md5 `8542d7e4…`，新用例 **5 红 / 0 绿**（含线程级「正文只出现一次」计数）；
  修复后同文件 **5/5 绿**（证据目录 `/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.aKYohz`）。
- **门禁**：`node --test app/tests/*.test.cjs` ⇒ **252 pass / 0 fail**；`scripts/check.sh --studio` ⇒ **门禁全绿**
  （含 `ruff format --check`，未跑 `ruff format`）。全量 pytest 不适用（无 `.py` 改动），最终 head 由 CI 复核。

## Merge Danger

**Door:** two-way——纯 Studio 呈现层与词条删改，回滚只需 revert 本提交；没有数据迁移、没有后端契约变化，
旧线程数据无需重写。

**Blast Radius:** 小——只影响任务线程 dock 的副行文案、段落数与 CSS；频道消息流、任务看板、
`.task-ref-chip` 的既有跳转语义、未读计数均未改动。

Closes #221
