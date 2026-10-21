# 验收记录：#225 主会话顶部「消息」页签显示未读数

**票型**：实施票（用户可见行为修复）｜**门禁**：G-impl（迭代期 `scripts/check.sh --studio`；定稿 `scripts/check.sh` 全量，CI 同脚本复核）
**Ticket**：[GitHub issue #225](https://github.com/whutlichao/alibot/issues/225)
**base**：`664cfc2`（main HEAD；切 worktree 时 `git rev-parse --short HEAD` 与 `origin/main` 同为 `664cfc2`）
**branch**：`issue-225-message-tab-unread`｜**worktree**：`/Users/apple/orca/workspaces/alibot/issue-225-message-tab-unread`
**采集时间**：2026-10-22｜**环境**：macOS（arm64）、Python（worktree `.venv` 符号链接到主工作区 `.venv`）、Node（`node --test`）
**改动面**：`app/alibot/studio/app.js`（`#msgCount` 取值 + 3 处注释）、
`app/tests/task_count_ui.test.cjs`（装置扩展 + 4 条新增用例）、
`app/tests/task_pointer_ui.test.cjs`（频道 fixture 增加未读读数 + 3 条旧语义断言更新）、本文件

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.claude/skills/implement/SKILL.md`（按流程另加载了 `tdd`）。本票映射：

| implement 步骤 | 本票实际做的事 |
| --- | --- |
| 读票面 | `gh issue view 225`（标题：「fix(studio): 主会话顶部「消息」页签显示未读数，不再显示消息总条数」），票面期望 / 验收 / 范围逐条抄成 §1 判据 |
| tdd 垂直切片 | 先改两套 Studio DOM 用例（`#msgCount` 的可观察面 + 既有 3 条旧语义断言）→ 隔离基线 **5 红**（§2）→ 改 `renderTabs()` 一个取值表达式 → 22/22 绿；预期值取自票面（3 / 0 / 4 / 0），不是按实现复算 |
| 单文件用例 + 门禁 | 迭代期 `node --test app/tests/task_count_ui.test.cjs app/tests/task_pointer_ui.test.cjs` + `scripts/check.sh --studio`；定稿 `scripts/check.sh` 全量 |
| 双轴 review | **本票不跑**（理由见 §5，票面明确豁免） |
| 提交 | 实现提交 + 验收 / PR 回填提交（见 §6），未合并、未推 main |

测试接缝（tdd 的 seam）保持不变：沿用 `vm` + `fetch` 替身 + DOM 替身的既有装置，断言只落在
用户可见文本（`#msgCount` 的 textContent）、消息流 GET 请求数、`/read` 请求三种外部可观察事实上。

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 频道 `unread_count = 3`、消息流只有 1 条 ⇒ 页签为 `3` | `app.js` `renderTabs()`：`$('#msgCount').textContent = target ? target.unread_count : 0` | `node --test app/tests/task_count_ui.test.cjs -t "频道未读 3"` | `1 passed`：两个数字故意不同（未读 3 / 消息 1），证明读的是未读 |
| 2 | `/read` 把该 target 未读归零 ⇒ 页签立即 `0`，且不新增消息流 GET | 既有链路 `markCurrentRead()` → `scheduleRead()` → `replaceChannel()` → `render()` → `setViewMode()` → `renderTabs()`；未新增请求 | `-t "已读推进后"` | `1 passed`：`POST /api/channels/ch_1/read` 发生；消息流 GET 次数在前后相等 |
| 3 | 私信 target 同样按 `unread_count` 显示 | 同 #1（`activeTarget()` 同时返回频道与私信，载荷均带 `unread_count`） | `-t "私信 target"` | `1 passed`：DM `unread_count: 4` ⇒ 页签 `4` |
| 4 | 没有 target 时显示 `0` | 同 #1 的三元分支 | `-t "没有当前 target"` | `1 passed` |
| 5 | system 指针消息（不计未读）不增加页签计数 | 前端不重算未读，直接读后端 `unread_count`；建任务后计数保持原值 | `node --test app/tests/task_pointer_ui.test.cjs -t "频道指针消息"` / `-t "建任务成功后"` | `2 passed`：频道未读 5 / 消息 1 ⇒ 页签 `5`；建任务吸收 system 行后页签仍为 `0` |
| 6 | 未读 0 时显示 `0`，不隐藏计数节点 | 同 #1：0 照写 | 同 #2 / #4 | 页签文本严格等于 `"0"`，节点仍由 `renderTabs()` 写入 |
| 7 | 注释说明唯一写入点 / 派生自 `unread_count`；不重排文件 | `app.js` `renderTabs()` 上方 3 行注释；`renderMessages()` / `submitTask()` 两处过时注释就地修正 | `git diff --numstat` | 见 §4：`app.js` 仅 7 增 3 删，全部为局部行 |
| 约束 | 不改后端 Python / schema / 迁移 / API 载荷；不新增端点或字段；不改未读口径 | `git diff --stat` | 同左 | 后端文件零改动；`UNREAD_WHERE_SQL` 未触碰 |
| 约束 | 不重排 / 格式化整份文件；不跑 `ruff format` | 局部编辑 + `scripts/check.sh` | 同左 | `git diff --numstat` 只含局部行（§4） |

---

## 2. 先红后绿（隔离基线）

产品代码保持基线字节，只把工作区的测试换进副本：

```console
$ scripts/red-evidence.sh 664cfc2 app/tests/task_count_ui.test.cjs
基线：664cfc2 (664cfc2)
测试：app/tests/task_count_ui.test.cjs（取自当前工作区）
基线 app.js md5：56fb2935b2564328d5168a401c20ab52
证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.pDioLC
✖ 频道未读 3、消息流只有 1 条时，「消息」页签显示 3
✖ 已读推进后「消息」页签立即变为 0，且不新增消息流 GET 请求
✖ 私信 target 的「消息」页签同样按 unread_count 显示
ℹ tests 7　ℹ pass 4　ℹ fail 3
结果：用例失败（红读数）
```

```console
$ scripts/red-evidence.sh 664cfc2 app/tests/task_pointer_ui.test.cjs
基线：664cfc2 (664cfc2)
测试：app/tests/task_pointer_ui.test.cjs（取自当前工作区）
基线 app.js md5：56fb2935b2564328d5168a401c20ab52
证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.aHYl2R
✖ 频道指针消息渲染出进入任务线程的 chip，文案带任务未读数，点击打开线程
✖ 建任务成功后吸收返回的 message：流里立刻出现该行，页签未读数不变
ℹ tests 15　ℹ pass 13　ℹ fail 2
结果：用例失败（红读数）
```

五条失败全部是 `#msgCount` 断言（基线读的是 `renderedMessages().length`）：

- 频道未读 3 / 消息 1：`actual: '1'` vs `expected: '3'`；
- 已读推进：起始读数即 `actual: '1'` vs `expected: '3'`（基线先在此处失败）；
- 私信未读 4：`actual: '0'` vs `expected: '4'`；
- 频道未读 5 / 消息 1：`actual: '1'` vs `expected: '5'`；
- 建任务吸收 system 指针消息后：`actual: '1'` vs `expected: '0'`。

修复后同一组用例：

```console
$ node --test app/tests/task_count_ui.test.cjs app/tests/task_pointer_ui.test.cjs
ℹ tests 22　ℹ pass 22　ℹ fail 0
```

---

## 3. 门禁读数

迭代期：

```console
$ scripts/check.sh --studio
== pytest（Studio 窄档：审批 HTTP 契约 + 前端相关静态守卫）  20 passed in 3.05s
== ruff check            All checks passed!
== ruff format --check   279 files already formatted
== mypy                  Success: no issues found in 115 source files
== pyright               0 errors, 0 warnings, 0 informations
== node --test           258 passed
门禁全绿
```

定稿全量（含 pytest）：

```console
$ scripts/check.sh
== pytest（全量）        1062 passed, 10 skipped in 576.65s (0:09:36)
== ruff check            All checks passed!
== ruff format --check   279 files already formatted
== mypy                  Success: no issues found in 115 source files
== pyright               0 errors, 0 warnings, 0 informations
== node --test           258 passed
门禁全绿
```

---

## 4. `renderedMessages()` 消费点与 `#msgCount` 写入点清点

| 位置 | 类型 | 处置 |
| --- | --- | --- |
| `app/alibot/studio/app.js:1270` `renderTabs()` | `#msgCount` **唯一**写入点 | **改为** `target.unread_count` |
| `app/alibot/studio/index.html:201` `#msgCount` 初值 | 静态标记 | **保留**（`0`，与无未读形态一致） |
| `app/alibot/studio/app.js` `renderMessages()` | `renderedMessages()` 消费者（消息流铺排） | **保留**：消息流仍按总条数铺，页签不再读它 |
| `app/alibot/studio/app.js` `markCurrentRead()` | `renderedMessages()` 消费者（已读推进的最大 seq） | **保留**：未读口径与已读位置的正本都在后端，这里只上报 seq |
| `app/tests/task_pointer_ui.test.cjs` | 旧语义断言（把 `#msgCount` 当消息总条数） | **更新** 3 条（原约 496 / 522 / 533 行，现 507 / 533 / 545–548 行） |
| `app/tests/task_count_ui.test.cjs` | 新行为用例 | **新增** 4 条（频道读未读 / 已读归零 / 私信 / 无 target） |

`git diff --numstat`：

```console
7	3	app/alibot/studio/app.js
132	4	app/tests/task_count_ui.test.cjs
23	8	app/tests/task_pointer_ui.test.cjs
```

后端与 schema 零改动；`UNREAD_WHERE_SQL` 未触碰。

---

## 5. 为什么跳过双轴 code-review

票面明确本票不跑双轴 review。影响面可圈定为：`renderTabs()` 里 `#msgCount` 的**一个取值表达式**，
外加 `renderMessages()` / `submitTask()` 两处同源注释——无共享接口、无字段换源、无后端改动；
新行为全部由两套既有 Studio DOM 用例红→绿覆盖（`#msgCount` 文本、消息流 GET 请求数、`/read` 请求
三类外部可观察事实）。§4 的写入点 / 消费点清点补上了 Spec 轴最关心的检查：`#msgCount` 只有一个
写入点，`renderedMessages()` 的其余消费者语义未变。

---

## 6. 提交与 PR

- 实现提交：`3aba123`（`fix(studio): 「消息」页签计数改为当前 target 未读数（#225）`；
  全量门禁在这一次上取得读数）
- 验收记录提交：`640aa83`（`docs(specs): #225 验收记录与门禁读数`；docs-only，代码未变，
  不重跑本地全量）
- PR：[#226](https://github.com/whutlichao/alibot/pull/226)（base `main`，head `issue-225-message-tab-unread`）
- PR 正文：`/tmp/issue-225-pr-body.md`（Summary / Evidence / Merge Danger，`Closes #225`；
  已通过 `gh pr create --body-file` 持久化到 PR #226）
- PR head 与分支最终提交一致（`gh pr view 226 --json headRefOid` 核对；docs 提交后会再核对一次）
