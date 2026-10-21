# 验收记录：#221 任务线程去掉与「讨论」重复的「关联消息」区块

**票型**：实施票（呈现层收敛 + DOM 回归用例）｜**门禁**：G-impl + G-merge（`scripts/check.sh --studio`；最终 head 由 CI 全量复核）
**Ticket**：[GitHub issue #221](https://github.com/whutlichao/alibot/issues/221)
**base**：`61ef770`（main HEAD；`git merge-base main HEAD` 与 `git rev-parse main` 同为 `61ef770`）
**branch**：`issue-221-impl`｜**worktree**：`/Users/apple/orca/workspaces/alibot/issue-221-impl`
**采集时间**：2026-10-21｜**环境**：macOS（aarch64）、Python 3.11.13（worktree `.venv` 符号链接到主工作区 `.venv`）、Node v25.0.0
**改动面**：`app/alibot/studio/{index.html,app.js,app.css,i18n.js}`（+12 / −55 产品代码）、
新增 `app/tests/task_thread_ui.test.cjs`（5 条，+153）、`app/tests/approval_dom_harness.cjs`（+3，暴露词条正本）、
本文件、`app/specs/issue-221-pr-body.md`、`app/specs/issue-221-evidence/*.png`
（读数：`git diff --numstat 61ef770...HEAD`）

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.claude/skills/implement/SKILL.md`。本票映射：

| implement 步骤 | 本票实际做的事 |
| --- | --- |
| 读票面 | `gh issue view 221`（标题：「实施票：任务线程去掉与「讨论」重复的「关联消息」区块」），数据面事实与决定逐条抄成 §1 判据 |
| tdd 垂直切片 | 先写 `task_thread_ui.test.cjs`（5 条）→ 基线产品代码 **4 红 / 1 绿**（§2）→ 实现 → 5/5 绿 |
| 单文件用例 + 门禁 | 迭代期 `node --test app/tests/task_thread_ui.test.cjs`；定稿 `node --test app/tests/*.test.cjs`（252 绿）+ `scripts/check.sh --studio`（门禁全绿） |
| 双轴 review | Standards + Spec 两轴（`code-review` skill），findings 与处置见 §6 |
| 提交 | 实现 commit + review 收口 commit（见 §7），未合并、未推 main |

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 删除「关联消息」正文区块（`.d-sec` + `#ttLinked`）、`linkedTaskMessages`、`.tt-linked*` | `index.html`（删 4 行）、`app.js`（删渲染块 + 函数 + 注释）、`app.css`（删 5 条规则） | `node --test app/tests/task_thread_ui.test.cjs -t "区块从三件套里移除"` | `1 passed`；三件套 `ttLinked` / `tt-linked` / `linkedTaskMessages` / `task.linkedEmpty` / `dock.linked` 命中 0 |
| 2 | 头部副行 = 「#频道 · 讨论 N 条」，N = 非 system 消息数（与「讨论」标题同一计数） | `app.js` `renderTaskThread()`：`count: replies.length` | 同上 `-t "头部副行"` | `1 passed`：2 条非 system + 2 条 system ⇒ `#设计系统重构 · 讨论 2 条`，`#ttReplyTitle` = `讨论 · 2 条` |
| 3 | 未读后缀保持原样 | `task.threadUnread` 未改，仍拼在讨论条数之后 | `-t "未读后缀"` | `1 passed`：`#设计系统重构 · 讨论 1 条 · 3 条未读` |
| 4 | 讨论仍渲染全部非 system 消息；带 `task_ref` 的消息只出现一次且带既有 `.task-ref-chip` | 讨论渲染路径未改（`appendTaskRefChip` 原样） | `-t "只出现一次"` | `1 passed`：正文在 `#ttReplies` 出现 1 次、`.task-ref-chip` 1 个、`dataset.openTask === 't-1'`、文案取任务标题 |
| 5 | i18n：`task.threadHeadSub` zh/en 参数位一致；删除 `task.linkedEmpty` / `dock.linked`；无落单键 | `i18n.js` 两份表 | `node --test app/tests/i18n_ui.test.cjs` | 全绿（同键集 / 参数位 / 无落单键 / 无裸中文 / `index.html` 绑定键全在） |
| 约束 | 不碰后端 Python / schema / CONTEXT.md / ADR / 设计系统原型；不删「讨论」「状态变更记录」 | `git status --porcelain` | 同左 | 只列 `app/alibot/studio/**`、`app/tests/**`、`app/specs/issue-221-*`；`#ttReplyTitle` / `#ttHistory` 段落原样 |
| 约束 | 不跑 `ruff format` | 命令清单 | — | 只经 `scripts/check.sh --studio` 跑 `ruff format --check` |

---

## 2. 先红后绿（隔离基线）

用仓库脚本在**隔离基线副本**上跑工作区里的新用例（产品代码为 base 字节，只替换测试）：

```console
$ scripts/red-evidence.sh 61ef770 app/tests/task_thread_ui.test.cjs
基线：61ef770 (61ef770)
测试：app/tests/task_thread_ui.test.cjs（取自当前工作区）
基线 app.js md5：8542d7e4801682fb6701b38697245e9c
证据目录：/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.aKYohz
✖ 任务线程头部副行显示「#频道 · 讨论 N 条」，N = 非 system 消息数
✖ 未读后缀保持原样，跟在讨论条数之后
✖ 带 task_ref 的用户消息在整条线程里只出现一次，且仍显示既有 .task-ref-chip
✖ 「关联消息」区块从三件套里移除：没有 #ttLinked / .tt-linked / linkedTaskMessages
✖ 词条：task.threadHeadSub 两份参数位一致；task.linkedEmpty / dock.linked 已删除
ℹ tests 5　ℹ pass 0　ℹ fail 5
结果：用例失败（红读数）；证据目录保留在 /var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T//alibot-red.aKYohz
```

第 3 条在**整条线程**上计数（`#ttLinked` + `#ttReplies` 挂在同一个父节点下，模拟真实页面的兄弟关系）：
基线里同一条正文在「关联消息」与「讨论」各一份 ⇒ 2，因此这条也是红的；修复后为 1。
review 前该断言只读 `#ttReplies`（基线本就绿），Spec 轴指出它没有覆盖「只出现一次」的整线程事实，
已改为线程级计数。修复后同一文件：

```console
$ node --test app/tests/task_thread_ui.test.cjs
ℹ tests 5　ℹ pass 5　ℹ fail 0
```

---

## 3. 门禁读数

```console
$ node --test app/tests/*.test.cjs
ℹ tests 252　ℹ pass 252　ℹ fail 0　ℹ cancelled 0　ℹ skipped 0　ℹ todo 0

$ scripts/check.sh --studio
（ruff check / ruff format --check / mypy / pyright / node --test glob / 审批 HTTP 契约 / 前端静态守卫）
门禁全绿
```

全量 pytest 不跑（本票无 `.py` 改动）；schema / 迁移不跑（无数据模型改动）。最终 head 由 CI 全量复核。

---

## 4. 真实浏览器证据（ego-browser）

隔离数据目录 + 独立端口，两台实例并排跑：基线 `61ef770`（`/tmp/alibot-issue221/base`，端口 8898，
数据目录 `base-data`）与改后工作区（端口 8797，数据目录 `new-data`）。两边都用同一脚本建数据：
`POST /api/channels` → `POST /api/channels/{id}/tasks` → 两次
`POST /api/tasks/{id}/messages`（body 为「第一条指针回复」「第二条指针回复」，`task_ref` = 该任务 id）。

打开路径：点左栏 `channel-*` → 点「任务」页签 → 点卡片 `task-card-*` → 右侧 dock 任务线程。

| 读数（`page.evaluate` 在 `#dockTask` 上取） | 基线 61ef770 | 改后 |
| --- | --- | --- |
| `#ttHeadSub` | `#验收频道 · 关联消息 2 条` | `#验收频道 · 讨论 2 条` |
| `#ttReplyTitle` | `讨论 · 2 条` | `讨论 · 2 条` |
| dock 内出现「关联消息」标题 | 是 | **否** |
| `#ttLinked` 容器存在 | 是 | **否** |
| 「第一条指针回复」在 dock 内出现次数 | **2** | **1** |
| 「第一条指针回复」在 `#ttReplies` 内出现次数 | 1 | 1 |
| `#ttReplies` 内 `.task-ref-chip` 数量 | 2 | 2 |
| `#ttScroll` 段落标题 | 审批 / 关联消息 / 讨论 · 2 条 / 状态变更记录 | 审批 / 讨论 · 2 条 / 状态变更记录 |

截图（`app/specs/issue-221-evidence/`）：

- `baseline-61ef770-thread.png`：改前——副行「关联消息 2 条」，正文在「关联消息」与「讨论」各一份。
- `after-issue221-thread.png`：改后——副行「讨论 2 条」，只剩「讨论」「状态变更记录」两段，
  两行正文各出现一次，指针 chip 仍在。

---

## 5. 词条与静态守卫

- `task.threadHeadSub`：zh-CN `'#{channel} · 讨论 {count} 条'`；en `'#{channel} · {count} discussion messages'`
  ——两份参数位同为 `{channel}`、`{count}`（`task_thread_ui.test.cjs` 与 `i18n_ui.test.cjs` 双重覆盖）。
- `task.linkedEmpty` / `dock.linked`：两份表均已删除，`app.js` / `index.html` 不再引用。
- `app.css` 的 `.task-ref-chip` 注释原写「关联消息：…」，改指 `CONTEXT.md` **指针消息** 词条里的
  `task_ref` 字段、并写成「任务 chip」（两轴 review 指出：无限定的「指针消息」会被读成 `task_pointer`）。
  只有措辞，无选择器/色值变化。

---

## 6. 双轴 code-review

固定点 `61ef770`，被审对象 `daa6324`（实现 + 用例 + 证据）。两轴并行、结论不互相抵消。

### 6.1 Standards 轴

**硬违规：无**（术语表无「关联消息」词条；i18n 双表同键集；252 个 node 用例全绿；未新开接缝）。
判断题与处置：

| # | finding | 处置 |
| --- | --- | --- |
| 1 | `.task-ref-chip` 注释无限定地称「指针消息」；`CONTEXT.md` 里该词不加限定时指 `task_pointer` 的 system 记录，`task_ref` 表现为**任务 chip** | **已改**：注释改指 `task_ref` 字段与「任务 chip」 |
| 2 | 用例断言 `/修一处样式/` 依赖装置默认标题（隐藏夹具耦合） | **已改**：`openThread` 显式传 `title: "整理发布清单"`，断言同步 |
| 3 | `placeholders()` 与 `i18n_ui.test.cjs` 的 `parameters` 同形（Duplicated Code） | **已改**：删掉本地 helper，改成就地 `{channel}` / `{count}` 断言；全键参数位比对仍由 `i18n_ui.test.cjs` 机械守住 |
| 4 | 用例头注释称「只落用户可见面」，与第 3 条源码静态扫描自相矛盾 | **已改**：头注释写明「替身按需创建节点，读不到『不在标记里』；结构声明与渲染入口的正本在源码」 |
| 5 | `implementation.md` 的 Studio 窄档字面只列 `app/alibot/studio/**` 与 `app/tests/*.test.cjs`，本 diff 还改了 `app/tests/approval_dom_harness.cjs`（测试辅助，非产品代码） | **记录**：只多暴露 `catalogs` 与文档头一行消费者登记，不触达后端/共享接口/schema/构建配置；仍跑 `--studio`，最终 head 由 CI 全量复核 |
| 6 | 验收 §0 的「−55 / +15 产品代码」与 `git diff --numstat` 不符 | **已改**：产品代码 **+12 / −55**；`+15` 含 harness 的 +3 |

### 6.2 Spec 轴

**(a) 缺失/半成品：无。** Change 1–4、6 均落地；三件套里 `ttLinked` / `tt-linked` /
`linkedTaskMessages` / `task.linkedEmpty` / `dock.linked` 命中 0；`#ttHeadSub` 与 `#ttReplyTitle`
同用 `replies.length`；未读后缀、`.task-ref-chip`、两段标题原样。
**(b) scope creep：1 条。** `app.css` 的 `.task-ref-chip` 注释从「关联消息：…」改指 `task_ref` /
「任务 chip」——首版即存在，属「同步更新过时注释」（票面写的是 app.js，这条落在 app.css）；
无行为影响，§5 已登记。
**(c) 实现有误：2 条，均已处理。**
1. 验收 §0 的 diff 读数不准 ⇒ **已改**（+12 / −55，见 §0）。
2. 用例的「只出现一次」只统计 `#ttReplies`，基线本绿，等于没覆盖「关联消息 + 讨论各一份」的复现路径；
   静态扫描又可能被换 id 的区块绕过 ⇒ **已改**：断言提升到线程级（`#ttLinked` + `#ttReplies` 同父），
   红证据随之变为 **5 红 / 0 绿**（§2 已重跑）。

---

## 7. 提交与 PR

- 提交：`daa6324`（实现 + 用例 + 证据）、`03cd07e`（双轴 review 收口）、`071fc07`（验收记录与 PR 正文）
- PR：[#222 fix(studio): 任务线程去掉与「讨论」重复的「关联消息」区块（#221）](https://github.com/whutlichao/alibot/pull/222)
- PR 正文：`app/specs/issue-221-pr-body.md`（`gh pr create --body-file` 提交）
- 分支：`issue-221-impl`（已推送，跟踪 `origin/issue-221-impl`）；目标：`main`
- CI：初推 head 的 check 由 CI 全量复核（本地未跑全量 pytest——无 `.py` 改动）

---

## 8. 遗留 / 已知道具面

- 无行为遗留：任务线程只剩「讨论」「状态变更记录」两段；`task_ref` 与 `.task-ref-chip` 语义未变。
- `app/tests/approval_dom_harness.cjs` 只多暴露 `catalogs`（词条正本）与文档头的一行消费者登记；
  装置本身（假时钟 / 定时器 / live 帧入口）未复制、未改行为。
- 本票不修改 `CONTEXT.md` / ADR：`task_ref`、**指针消息**、任务 chip 的语义都没变，
  「关联消息」从来不是术语表词条，只是一段呈现。
