# 验收记录：#223 频道指针 chip 的计数改为任务未读数

**票型**：实施票（用户可见行为修复）｜**门禁**：G-impl（`scripts/check.sh --fast` 迭代期；定稿 `scripts/check.sh` 全量，CI 同脚本复核）
**Ticket**：[GitHub issue #223](https://github.com/whutlichao/alibot/issues/223)
**base**：`0e6c56d`（main HEAD；切分支时 `git rev-parse main` 与 `origin/main` 同为 `0e6c56d`）
**branch**：`issue-223-impl`｜**worktree**：`/Users/apple/orca/workspaces/alibot/issue-223-task-pointer-unread`
**采集时间**：2026-10-22｜**环境**：macOS（arm64）、Python（worktree `.venv` 符号链接到主工作区 `.venv`）、Node（`node --test`）
**改动面**：`app/alibot/studio/app.js`（chip 计数来源 + 已读后定点刷新 + 注释）、`app/alibot/studio/i18n.js`（中英各 +2 键）、
`app/tests/task_pointer_ui.test.cjs`（用例更新 + 2 条新增）、`CONTEXT.md`（两个词条）、
`docs/adr/0028-task-creation-leaves-a-channel-pointer.md`（文末修订小节）、本文件

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.claude/skills/implement/SKILL.md`。本票映射：

| implement 步骤 | 本票实际做的事 |
| --- | --- |
| 读票面 | `gh issue view 223`（标题：「fix(studio): 频道指针 chip 的计数改为任务未读数，0 未读时不显示计数」），票面的期望 / 范围逐条抄成 §1 判据 |
| tdd 垂直切片 | 先改 `task_pointer_ui.test.cjs` → 隔离基线 **5 红 / 10 绿**（§2）→ 实现 → 15/15 绿 |
| 单文件用例 + 门禁 | 迭代期 `node --test app/tests/task_pointer_ui.test.cjs` + `node --test app/tests/i18n_ui.test.cjs`；定稿 `scripts/check.sh` 全量 |
| 双轴 review | **本票不跑**（理由见 §5，票面明确豁免） |
| 提交 | 实现提交 + 文档回填提交（见 §6），未合并、未推 main |

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | chip 计数来源由 `message_count` 改为 `unread_count`，后端未读口径不动 | `app.js` `appendTaskRefChip()`：`Number(pointedTask.unread_count)`；`UNREAD_WHERE_SQL` / `get_task()` 未改 | `node --test app/tests/task_pointer_ui.test.cjs -t "文案带任务未读数"` | `1 passed`：任务载荷 `unread_count: 2` / `message_count: 3`，chip 显示 `进入任务线程 · 2 条未读`（证明读的是未读） |
| 2 | 未读 0：chip 文本恰为 `进入任务线程`，aria 为「打开任务 {title} 的任务线程」 | `taskPointerChipText()` / `taskPointerChipAria()` 两个分支；i18n 新增 `msg.taskThreadEntryUnread` / `msg.taskThreadEntryAriaUnread` | `-t "不显示计数段"` / `-t "建任务成功后吸收"` | `2 passed`：任务不在快照时文本严格等于 `进入任务线程`；新建任务（未读 0）同款，且不含数字 |
| 3 | 未读 > 0：`进入任务线程 · {count} 条未读`；aria 「…，有 {count} 条未读」 | 同上两个分支 | `-t "文案带任务未读数"` | `1 passed`：文本与 aria 均为严格相等断言 |
| 4 | 已读后（`openTask` → `markTaskRead`）同一任务 chip 立即归零：不整板重铺、不丢滚动、不重拉频道消息流 | `markTaskRead()` 成功后调用 `syncTaskPointerChips()`；chip 带 `data-task-pointer`，按任务 id 定点改文本与 aria | `-t "打开任务线程完成已读后"` | `1 passed`：同一个 chip / 消息行节点、`scrollTop` 保持 37、`POST /api/tasks/t_1/read` 发生、频道 `/messages` 请求数不变 |
| 5 | 线程内新消息被吸收后（`absorbTaskMessage` → `markTaskRead`）同样归零 | 与判据 4 同一个 `syncTaskPointerChips()` 出口 | `-t "线程内新消息被吸收后"` | `1 passed`：同一 chip 节点就地改写，频道 `/messages` 请求数不变 |
| 6 | 中文 / 英文两套词条同步、同键集、无裸中文 | `i18n.js` 两份表各 +2 键，原两个键去掉 `{count}` | `node --test app/tests/i18n_ui.test.cjs` | `53 passed`（同键集 / 参数位一致 / 无落单键 / 无裸中文） |
| 7 | 文档事实修正 | `CONTEXT.md`「任务线程」「指针消息」两个词条；ADR-0028 文末 `## 修订（2026-10-21，#223）` | `rg -n "message_count" app/alibot/studio` | 0 命中（studio 不再有 chip 计数读取点），见 §4 |
| 约束 | 不新增后端端点 / 不改 `unread_count` 计算 / 不改 `UNREAD_WHERE_SQL` | `git diff --stat` | 同左 | 后端 Python、schema、构建配置均未触碰 |
| 约束 | 不重排 / 格式化 `app.js` / `i18n.js`；不跑 `ruff format` | 局部编辑 + `scripts/check.sh` | 同左 | `git diff --numstat` 只含局部行；门禁只跑 `ruff format --check` |

---

## 2. 先红后绿（隔离基线）

产品代码保持基线字节，只把工作区的测试换进副本：

```console
$ scripts/red-evidence.sh main app/tests/task_pointer_ui.test.cjs
基线：main (0e6c56d)
测试：app/tests/task_pointer_ui.test.cjs（取自当前工作区）
基线 app.js md5：b627ed7048cd5dd64baff54666d139ef
证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.JktjI7
✖ 频道指针消息渲染出进入任务线程的 chip，文案带任务未读数，点击打开线程
✖ 建任务成功后吸收返回的 message：流里立刻出现该行，页签计数 +1
✖ 任务不在快照里时 chip 不显示计数段，也不编造条数
✖ 打开任务线程完成已读后，频道流里同一任务的 chip 立即变为未读 0，且不重铺消息流
✖ 线程内新消息被吸收后，同一任务的 chip 也立即回到未读 0（不重拉频道消息流）
ℹ tests 15　ℹ pass 10　ℹ fail 5
结果：用例失败（红读数）
```

三条关键读数（基线实际输出）：

- 未读 > 0：`actual: '进入任务线程 · 9 条'` vs `expected: '进入任务线程 · 4 条未读'`——基线读的是
  `message_count`（9），不是未读（4）；
- 未读 0（任务不在快照）：`actual: '进入任务线程 · 0 条'` vs `expected: '进入任务线程'`；
- 已读后归零：打开线程后 chip 仍停在基线计数（无定点刷新），断言严格相等失败。

修复后同一文件：

```console
$ node --test app/tests/task_pointer_ui.test.cjs
ℹ tests 15　ℹ pass 15　ℹ fail 0
```

---

## 3. 门禁读数

迭代期：

```console
$ node --test app/tests/i18n_ui.test.cjs
ℹ tests 53　ℹ pass 53　ℹ fail 0

$ scripts/check.sh --fast
== pytest（--fast 且未指定文件：跳过）
== ruff check            All checks passed!
== ruff format --check   276 files already formatted
== mypy                  Success: no issues found in 115 source files
== pyright               0 errors, 0 warnings, 0 informations
== node --test           254 passed
门禁全绿
```

定稿全量（含 pytest）：

```console
$ scripts/check.sh
== pytest（全量）        1062 passed, 10 skipped in 582.95s (0:09:42)
== ruff check            All checks passed!
== ruff format --check   278 files already formatted
== mypy                  Success: no issues found in 115 source files
== pyright               0 errors, 0 warnings, 0 informations
== node --test           254 passed
门禁全绿
```

---

## 4. `message_count` 全部消费点与处置

| 位置 | 类型 | 处置 |
| --- | --- | --- |
| `app/alibot/db.py:2499`、`:2518`（`get_task` 子查询与载荷字段） | 后端 API 事实 | **保留**（票面要求字段不动，UI 已无消费者） |
| `app/alibot/db.py:2516` 注释（「频道 chip 的『进入任务线程 · N 条』读它」） | 后端注释 | **仅文档提及**：已过时；`db.py` 不在本票 Target（不改后端），留待后续票顺手修正 |
| `app/alibot/studio/app.js` `appendTaskRefChip()` | 前端读取点 | **改为未读**（`unread_count`），`rg` 后 studio 零命中 |
| `app/alibot/studio/app.js:3707` 注释（建任务后补快照） | 前端注释 | **改为未读**（说明按 0 处理、不显示计数段） |
| `app/tests/task_pointer_ui.test.cjs` | 前端 DOM 断言 | **改为未读**（`unread_count: 2` / `message_count: 3` 故意不同值） |
| `app/tests/test_task_channel_pointer.py:8,76-82` | 后端载荷断言 | **保留**：钉住 API 字段仍随线程消息增长（字段存在性契约） |
| `CONTEXT.md:392`（任务线程词条） | 领域文档 | **改为未读**：`message_count` 仍是总条数，chip 不再读它 |
| `CONTEXT.md:413`（指针消息词条） | 领域文档 | **改为未读**：N 取 `unread_count`，0 未读不显示计数 |
| `docs/adr/0028-*.md:58`（§五历史正文） | ADR 历史正文 | **不改正文**，文末 `## 修订（2026-10-21，#223）` 最小事实修正 |
| `app/specs/issue-198-acceptance.md`、`issue-217-acceptance.md` 等 | 历史验收记录 | **仅文档提及**：当时事实，按票面约束不修改 |
| `app/alibot/runtime.py:74`（`runtime_prompt(message_count)`） | 同名不同物（本轮上下文消息条数） | **保留**，与任务载荷无关 |
| `app/tests/test_silent_outcome.py` / `issue-58`、`issue-189` 文档 | 轮次结论用例名 / 历史记录 | **保留**，与任务载荷无关 |

---

## 5. 为什么跳过双轴 code-review

票面明确本票不跑双轴 review。影响面可圈定为：一个呈现函数（`appendTaskRefChip`）的计数来源、
其两个词条分支、以及 `markTaskRead()` 末尾一次定点 DOM 写入——无共享接口、无后端改动、无跨调用点
字段换源；新行为全部由 `task_pointer_ui.test.cjs` 的 DOM 用例红→绿覆盖（含「同一节点 / 滚动位置 /
频道消息请求数」这些外部可观察事实）。§4 的 `message_count` 消费点清点补上了「没有漏掉第二个 UI
读取者」这一 Spec 轴最关心的检查。

---

## 6. 提交与 PR

- 实现提交：`4f81ebd`（`fix(studio): 频道指针 chip 计数改为任务未读数，0 未读不显示计数（#223）`；
  全量门禁在这一次上取得读数）
- 验收 / PR 回填提交：`996c803`（`docs(specs): #223 回填 PR 与门禁读数`）
- PR 正文回填：`da1891f`（补 `Closes #223`）；PR head 与分支最终提交一致（`gh pr view 224 --json headRefOid` 核对）
- PR：[#224](https://github.com/whutlichao/alibot/pull/224)，正文 `app/specs/issue-223-pr-body.md`
  （Summary / Evidence / Merge Danger，`Closes #223`）
- 全量门禁读数：§3（`scripts/check.sh` → 门禁全绿；CI 同脚本复核）
