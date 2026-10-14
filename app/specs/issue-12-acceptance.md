# 修正票 ⑫ 验收取证（issue #12：预算记账粒度——确立 `channel_budget.used` 为跳数单一正本）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。
issue 上的 Answer 摘要指向本文件。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-12-budget-ledger`（Orca 独立顶层 worktree）
- 分支与 PR：`issue-12-budget-ledger` → **[PR #16](https://github.com/whutlichao/alibot/pull/16)**（OPEN，base `main`，head commit `8aa1780`）
- PR body 正本：`app/specs/issue-12-pr-body.md`（`gh pr create --body-file`，即 PR #16 的正文）
- 基线：合并后的 `origin/main` `2b8615c`（worktree 由它创建，未 rebase、未合并 main）
- 采集时间：2026-10-14
- 环境：macOS，Python 3.11
- 票标题（开工时用 `gh issue view 12` 核对）：

> 修正票：预算记账粒度——确立 channel_budget.used 为跳数单一正本，消除按行求和的歧义

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：读票
（`gh issue view 12` 全文 + 报告标题）→ 只读核实事实（`notify()` / `_spend_jump()` /
`_record_merged()` / `run_round()` 的 `consumed_budget` 传递路径，以及 `db.budget_state()` /
`consume_budget()` / `append_round_outcome()` 与 `round_outcome` 表定义）→ 实现 → 定期跑单文件
测试与类型检查 → 全量 → 聚焦自审 → 提交 → PR。

票面有一条**硬性流程要求**：改 `round_outcome` 的列语义或新增表前必须先在 `ask` 里说明方案与代价。
已按此执行，coordinator 裁定 **Q1 = B（行内消歧）**，理由与落地要求见 §1。

## 1. 方案选择：路线 B（行内消歧），及其理由

#12 给的两条路：**(A) 跳级记录**（新增跳表 + 成员行引用）与 **(B) 保持行内字段但消歧**。
**本票选 B**。开工前经 `orca orchestration ask`（messageId `msg_e1130bf91ef9`）提问，coordinator 裁定 B，理由：

1. **不回归 #4 已交付的契约**。#4 的验收准则明写轮次结果要含「是否消耗预算」；A 会把
   `consumed_budget` 从 `/round-outcomes` 的 payload 里挤掉，等于推翻上一票已验收的对外约定。
2. **A 是 Speculative Generality**。`consumed_budget` 目前**没有任何生产消费者**（本票核实：
   `app/worksplice/studio/` 0 命中，前端只读 `status`/`reason`，见 §3.4）。为一处命名歧义新增一张表 +
   一列外键而没有消费方，正是前几轮 `code-review` 反复点过的同一类问题。
3. **票面本来就允许 B**，且 B 不改 schema、不改 #4 的字段名，代价最小、回滚风险最低。

B 的落地（coordinator 明确要求的三处文档 + 行为级断言 + 变异测试）逐条见 §3。

### 1.1 「跳数」的单一正本落在哪（票面 Acceptance 第 1 条）

| 面 | 字段 | 语义 | 是不是跳数正本 |
| --- | --- | --- | --- |
| 只读端点 `GET /api/channels/{id}/budget` | `used` | 该人类世代**已投递**的 agent→agent 跳数 | **是（唯一）** |
| 表 `channel_budget` | `used` | 同上（端点的数据源） | **是（唯一）** |
| 表 `round_outcome` / 端点 `/round-outcomes` | `consumed_budget` | **行级**标记：「这一轮由**一次已计一次的跳**送来」 | 否（**不可按行求和**） |

- **跳数唯一正本的唯一写点**是 `WakeOrchestrator._spend_jump`：一次调用让 `channel_budget.used`
  恰好 +1，与该跳投递了几个成员无关（`orchestration.py` 的该方法 docstring 已写明）。
- **行级标记的语义**从「这一跳」改为「**这一轮所属的跳**」：一次跳投递 N 个成员就有 N 条为真的行。
- 三处文档（coordinator 指定的落点）都已写入：`db.py` 的 `round_outcome` 表注释与
  `append_round_outcome` docstring、`orchestration.py` 模块 docstring 与 `run_round`/`notify`/
  `_spend_jump`/`_record_merged` docstring、`app/README.md` 的数据模型表 +「有界响应」小节，
  以及 `api.py` 两个只读端点的 docstring（明确「不要对它们求和」）。

## 2. 门禁结果（全量）

```text
$ .venv/bin/python -m pytest
159 passed, 1 skipped in 93.91s        # skipped = 默认跳过的真模型冒烟测试

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 36 files already formatted

$ .venv/bin/python -m mypy             # 配置在仓库根 mypy.ini
Success: no issues found in 25 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
errors 0 files 25
```

基线对照（隔离 worktree `/tmp/wsps-base-12` = `git worktree add --detach /tmp/wsps-base-12 2b8615c`）：

```text
$ cd /tmp/wsps-base-12/app && .venv/bin/python -m pytest
158 passed, 1 skipped in 94.92s        # 本票 +1 条用例（159 → 160 collected）
```

> 环境说明：`pyrightconfig.json` 用 `venvPath: "."` + `venv: ".venv"`，而 Orca worktree 默认不带
> `.venv`（被 `.gitignore` 忽略）。本 worktree 按仓库既有做法指向同一份 venv（一个被忽略的
> `.venv/` 目录：`bin`/`lib` 为符号链接），两个类型检查器因此在 25 个源文件上跑满；交付物里不含它。

## 3. 逐条验收（票面 Acceptance criteria）

### 3.1 确立单一正本

见 §1.1。落点说明与三处文档的写入位置如上；`channel_budget` 表的既有注释已写「`used` = 该世代内
**已投递**的 agent→agent 跳数」，本票未改它（它本来就是正确的），只补齐**与之相对**的行级字段语义。

### 3.2 让「一次跳」在可追溯面上可被无歧义计数（票面第 2 条）

选 **B**：保留行内字段、改正语义为「这一轮所属的跳」，并在 docstring/README 写明**不可按行求和**、
正本是 `channel_budget.used`。**不改 schema、不改列名与列集合、不新增表**（B 的前提）。
「不要从行数反推跳数」这件事在只读面上有两条引导：`/budget` 的 docstring 指向 `used`，
`/round-outcomes` 的 docstring 明确「不要对它们求和」。

### 3.3 关键判别断言：一次跳投递 N（≥2）个成员 ⇒ 跳数仍只 +1

新增 `app/tests/test_bounded_relay.py::test_one_jump_delivered_to_n_members_still_counts_as_one_jump`
（`N = 2`：甲回复同时 `@` 同在频道里的乙与丙 ⇒ 两条真实投递）。

```text
$ .venv/bin/python -m pytest tests/test_bounded_relay.py -q -k one_jump_delivered
1 passed
```

断言（**行为级**：只经 HTTP 只读面取证，不碰内部结构）：

| 断言 | 事实 |
| --- | --- |
| `budget(...)["used"] == 1` | 跳数正本：这次跳只 +1 |
| 两条投递都真实发生（两次 `seam` 调用的 `seqs` 都含甲那条 Agent 消息的 seq） | 防止「仍只 +1」退化成本来就没投递的恒真断言 |
| 带行级标记的行**恰好 2 条**，且都属乙、丙 | 每个被投递成员一条 —— 它是**行级**标记 |
| 两条标记行的 `base_seq` **都等于**甲那条 Agent 消息的 `seq` | 它们是**同一次跳** |
| `sum(带标记的行) != used` | 消歧的核心：「按行求和得跳数」被证明是错的 |
| 唤醒甲的那一轮（人类消息触发）**不带标记** | 标记确实只属于「跳送来的」轮次 |

### 3.4 变异测试：把记账改回「每成员各扣一次」 ⇒ 断言必须变红

在隔离副本 `/tmp/wsps-mutate-12`（只拷 `app/`，工作树不动）逐个注入变异。**先跑控制组**确认未变异的
副本全绿，再注入：

| 变异 | 期望变红的用例 | 结果 |
| --- | --- | --- |
| **#1** `_spend_jump` 的一跳扣款改成「对每个目标各 `consume_budget` 一次」（= #12 描述的错误粒度） | `test_one_jump_delivered_to_n_members_still_counts_as_one_jump` | ✅ `assert 2 == 1` |
| #1 同上 | `test_multiple_targets_from_one_message_cost_only_one_jump` | ✅ 失败 |
| #1 同上 | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch`（`used==3` 场景） | ✅ 失败 |
| **#2** 只给该跳的第一个目标打行级标记（把「成员行」当「跳行」的另一种误读） | `test_one_jump_delivered_to_n_members_still_counts_as_one_jump` | ✅ `assert 1 == 2` |

```text
# 变异 #1（#12 要求的那个变异）注入后：
$ .venv/bin/python -m pytest tests/test_bounded_relay.py -q -k "one_jump_delivered or relay_is_bounded or multiple_targets"
FAILED tests/test_bounded_relay.py::test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch
FAILED tests/test_bounded_relay.py::test_multiple_targets_from_one_message_cost_only_one_jump
FAILED tests/test_bounded_relay.py::test_one_jump_delivered_to_n_members_still_counts_as_one_jump

# 变异 #2 注入后：
$ .venv/bin/python -m pytest tests/test_bounded_relay.py -q -k one_jump_delivered
E       assert 1 == 2        ← 只给第一个目标打标记
FAILED tests/test_bounded_relay.py::test_one_jump_delivered_to_n_members_still_counts_as_one_jump
```

**红→绿证据的诚实说明**：本票是**记账形态**修正，**行为一律不变**（见 §3.6），因此把新测试拷到
基线 `2b8615c` 上跑**也是绿的** —— 这不构成缺陷，正是票面要求用**变异测试**当判别点的原因：

```text
# 隔离基线 worktree，只拷新测试文件、不带任何实现改动：
$ cd /tmp/wsps-base-12/app && .venv/bin/python -m pytest tests/test_bounded_relay.py -q
26 passed        ← 形态改动不产生行为差异，故基线也绿
```

即：这条断言的判别力由**变异 #1**（记账改回每成员各扣一次 ⇒ 变红）证明，而不是由基线红证明。

### 3.5 票面三种场景

| 场景 | 用例 | 结果 |
| --- | --- | --- |
| ① 两个成员被同一条 Agent 消息唤醒 ⇒ 跳数 +1 | `test_one_jump_delivered_to_n_members_still_counts_as_one_jump`（两句成员、`@` 两人）+ 既有 `test_multiple_targets_from_one_message_cost_only_one_jump` | ✅ `used == 1`，标记行 2 条 |
| ② 完整互相 `@` 的两 Agent 接力 ⇒ 该世代 `used == 3` 且**不再增长** | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch`：新增 `pending_count() == 0` 断言（接力**自己停下**，不是被测试的 12 次上限截断）+ 新增「`used=3` 而标记行只有 2 条」的 #12 原样复现 | ✅ `used == 3`、`remaining == 0`、标记行 **2** 条 |
| ③ 合并掉的重复唤醒不增加跳数 | `test_repeated_wakes_during_a_round_are_merged_into_one`：既有「恰好一条 merged 行、`base_seq` 取 max、模型只调一次、`used == 0`」+ 本票新增「该频道**没有任何**行带跳级标记」（与 `used == 0` 一致） | ✅ |

**②的原样复现正是 #12 正文引用的实测事实**：`budget.used == 3`，而 `consumed_budget=True` 的轮次结果
只有 **2** 条。本票把它从「观察项」变成一条**会随记账形态变化而变红的断言**（变异 #1 下该用例失败）。

### 3.6 行为不变（#4 的验收面，语义一律不动）

| 行为 | 是否改动 | 证据 |
| --- | --- | --- |
| 硬上界 ≤ 3 | **未动** | `test_agent_to_agent_relay_is_bounded_by_three_jumps_per_human_epoch` 仍断言 `used == 3` 且 `≤ 3` |
| 耗尽零投递 | **未动** | 同用例：耗尽后 agent 消息照常落库（≥4 条）、存在 `budget-exhausted` 结论 |
| 人类消息重置为 3 | **未动** | `test_a_new_human_message_resets_the_budget_and_the_relay_works_again` |
| 合并的重复唤醒不消耗 | **未动** | `test_repeated_wakes_during_a_round_are_merged_into_one`（`used == 0`） |
| 穿透不豁免预算 | **未动** | `test_piercing_wake_consumes_a_jump_like_any_other`、`test_piercing_is_suppressed_once_the_budget_is_exhausted` |
| 全 `in-flight` ⇒ 零投递 ⇒ 不算跳 | **未动** | `test_all_targets_in_flight_is_not_a_jump` |
| 零投递不算跳 | **未动** | `test_a_zero_delivery_agent_message_does_not_consume_budget` |
| `round_outcome` append-only，无编辑/删除路径 | **未动** | `test_no_message_edit_or_delete_path_even_after_budget_exhaustion`（四种写方法 404、流未变）；未新增任何表或列 |

**实现层的行为不变性**：`git diff` 里**没有**任何条件分支、阈值或调用顺序的变化 —— 唯一的代码改动是
一个**局部变量改名 + 注释/docstring**（`consumed_budget` → `jump_delivered`，值恒等于原表达式
`author_kind == "agent"`），以及把它继续原样传给各成员（与改前完全一致）。**纯记账形态修正**：

```diff
-        consumed_budget = author_kind == "agent"
-        if consumed_budget and not self._spend_jump(
+        jump_delivered = author_kind == "agent"
+        if jump_delivered and not self._spend_jump(
```

**既有测试断言零删改**：`git diff -U0 -- app/tests/` 无任何删除行（只有新增，已核实），既有
`consumed_budget` 断言（`test_agent_round.py` 与 `test_bounded_relay.py` 里共 8 处引用）全部保留且仍绿
—— 因此票面「若改动使既有测试断言变化必须逐条说明」这一条**无需触发**：没有一条既有断言发生变化。

### 3.7 不引入可观测页面、不顺带重构

- `git diff --stat`：只 5 个文件、`+111 / -6`，其中 4 个是**注释/docstring/README 文案**、
  1 个是测试文件。
- `app/worksplice/studio/` **0 改动**（`git diff --stat` 无该目录）。
- 没有新端点、没有 schema 变更、没有新表。

## 4. 聚焦自审（本票影响面 + 未被破坏的既有行为）

票面允许省完整双轴 `code-review`（影响面可圈定为「编排模块的一处记账落点 + 其断言」），但**要求**做
聚焦自审并记录。逐条如下：

### 4.1 影响面（改了什么、波及到谁）

| 落点 | 改动 | 波及 |
| --- | --- | --- |
| `orchestration.py::notify` | 局部变量 `consumed_budget` → `jump_delivered` + 注释 | **无行为变化**（值恒等）。唯一的真实变化是**可读性**：名字不再读作「这一行扣了一次」 |
| `orchestration.py` docstring × 6 处（模块 + `notify`/`_spend_jump`/`run_round`/`_record_merged`） | 写明行级 vs 跳级 | 只影响阅读者 |
| `db.py`：`round_outcome` 表注释 + `append_round_outcome` docstring | 同上 | 只影响阅读者；**列名/列集合/触发器未动** |
| `api.py`：两个只读端点 docstring | 同上 | **payload 未动**（`consumed_budget` 仍在每行里，字段名不变） |
| `app/README.md`：数据模型表 + 有界响应小节 | 同上 | 只影响阅读者 |
| `tests/test_bounded_relay.py` | +1 用例、+2 处断言、+1 处 `pending_count` 断言 | 无既有断言被删改 |

- **生产代码的读取方**：`consumed_budget` 没有任何生产消费者（§3.4 已核实：`studio/` 0 命中、
  `service.py` 不读它、`api.py` 只把它原样透出）。因此本票的「语义修正」不会让任何调用方行为改变 ——
  这正是选 B 而非 A 的安全前提。
- **持久化面**：无 schema 变更、无迁移；`SCHEMA_VERSION` 未动（`3` → `3`），因为列集合没变。

### 4.2 我确认了哪些既有行为未被破坏

逐条跑过、并在 §3.6 表里给了用例名（全部在本次全量 159 passed 里）：

1. **硬上界 ≤3 与「构造性压制」**：`used` 恰好停在 3、耗尽后零投递、agent 消息仍 append-only 落库。
2. **世代重置**：人类消息落库即 `used=0`、`remaining=3`、`epoch_seq` 推进。
3. **合并语义**：一轮 in-flight 期间重复唤醒 ⇒ 恰好一条 merged 行、`base_seq` 取 max、不重放不吞掉。
4. **穿透**：不豁免预算、要回复才加入、`system` 事件消息不唤醒不消耗。
5. **零投递 / 全 in-flight 不算跳**：`_spend_jump` 的两道判据未被触碰。
6. **轮次结果 append-only**：无编辑/删除路径，预算端点四种写方法仍 404。
7. **重启恢复**：预算 `used`/`epoch_seq` 与轮次结果跨进程一致（`test_budget_state_is_persisted_and_survives_restart`）。
8. **未读口径与 system 消息**：未受本票影响（未改 `CHANNEL_SELECT_SQL` 或未读路径）。
9. **状态点四态**：`budget-exhausted` 仍不算 `error`。
10. **测试范式**：新用例只用既有的 HTTP 黑盒面 + `conftest`/`scripted` 夹具，未开第二套接缝、未 import 实现内部模块。

### 4.3 自审发现并处置的问题

- **发现 1（已修）**：我最初的测试构造让「人类消息也唤醒乙丙」，于是乙丙各跑两轮，
  `calls_for(...) == 1` 断言失败。改为按**触发消息的 seq** 定位那一次跳（`jump_seq in call["seqs"]`），
  断言更精确且不再依赖调用次数。
- **发现 2（已修）**：场景 ③ 原本只在**单成员**频道覆盖（`used == 0` 恒真、判别力弱）。改为在既有
  两成员合并测试里补一条「该频道没有任何带跳标记的行」，与 `used == 0` 互证。
- **发现 3（已修）**：场景 ② 原来的循环 `for _ in range(12)` 无法区分「自己停下」与「撞上测试上限」，
  补 `pending_count() == 0` 断言，使「不再增长」成为可观察事实。
- **发现 4（记录）**：`RoundOutcome.consumed_budget`（`agent_seam.py:113`）是一个**从未被读取**的
  死字段 —— 它既不由 seam 设置，也不被编排读取（编排用的是自己算的局部变量）。本票**未删除**它：
  删除会改动 adapter 契约（#3 的交付面），超出「只修记账形态」的本票范围。作为遗留项记录于 §5.2。

### 4.4 分析器噪声的核实（非本票问题）

pi-lens 曾报 4 处 `Pyright reportMissingImports`（`api.py:38-41`）与 1 处
（`test_bounded_relay.py:18`），以及 3 处 Semgrep SQL 拼接告警。逐条核实为**环境/误报**，
已用 `lens_diagnostic_mark` 逐一标为 false-positive（带核验理由），这四个文件现在报 **clean**：

```text
$ # lens_diagnostics source=lsp mode=full scope=paths（上述四个文件）
Files checked: 4 · Total diagnostics: 0 · Outcomes: clean=4, findings=0
```

- **根因（已定位并修复）**：pi-lens 的 python LSP（`pyright-langserver`）在 **02:44:39Z** 启动，而本 worktree
  的 `.venv` 建成于 **02:51Z** —— 守护进程把「未解析的第三方 import」状态缓存了下来。清理陈旧缓存并让
  LSP 以存在 `.venv` 的环境重启后，live 条目变为 **0 import errors**（清缓存前已备份到 `/tmp/`）。
  这是工具环境问题，不是代码缺陷；`mypy.ini` 已记录同类「检查器环境差异」。
- 被报的 import 行与基线 `2b8615c` **逐字节相同**；本票 diff 里新增/删除的 import 行数 = **0**。
- 仓库自带 `pyrightconfig.json` 跑 CLI pyright：**errors 0 / files 25**（#4 验收文档用的同一命令）。
- Semgrep 命中的 3 处 `"SELECT " + AGENT_COLUMNS` 拼的是**模块级常量**（无用户输入进 SQL 文本，
  值全经命名占位符绑定），在基线与本次**都是 3 处**，且不在本票的 diff hunk 内。

### 4.5 自审发现并修正的一处措辞不准确

初稿在 `db.py` 与 `app/README.md` 里写「按行求和得到**偏大**的数」。自审时对照本次实测发现这不准确：
接力场景是 `used=3` 而标记行 **2** 条（**偏小**）。行数与跳数之间**没有固定关系** —— 多成员一跳时行数
偏大（N vs 1），成员已无新消息不落行时偏小。已把两处措辞改为「行数与跳数之间没有固定关系……既可能偏大
也可能偏小」，使文档与实测一致（测试文件里同一处的注释也一并更正）。

## 5. 取舍与遗留

1. **选 B 的代价**：「按行求和得跳数」这条路被**文档 + 断言**判为错误，但库层面没有**强制**它不可行
   （类型上仍是 `bool` 列）。这是 B 相对 A 的唯一弱点；票面认可 B，且 coordinator 明确「不引入调用方
   从行数反推跳数这件事变成误导」已由三处文档 + 消歧断言满足。
2. **遗留（未做，超本票范围）**：`RoundOutcome.consumed_budget`（`agent_seam.py`）是死字段 ——
   无写入方、无读取方。删除它会改动 adapter 契约，属 #3 交付面，本票不动（§4.3 发现 4）。
3. **遗留（未做，超本票范围）**：`app/specs/issue-4-acceptance.md` 的 §3.3/§3.4 表格里对
   `consumed_budget` 的描述是「是否消耗预算」，严格说仍是旧措辞。该文件是**上一票的历史取证记录**
   （记录它当时跑过什么），本票不改写历史文档；`app/README.md`（面向使用者的正本）已按 B 更新。
4. **不新增只读出口**：coordinator 裁定 B 后，Q2（是否给「跳」加只读出口）**无需回答** —— B 不建跳表，
   没有需要出口的跳级记录；判别力由 §3.4 的变异测试而非新端点提供。
5. **未做的**：可观测页面（#1 明确「首版不做」）、编排模块重构（票面明确排除）、预算策略变更
   （≤3/重置/耗尽/穿透不豁免，全部保持原样）。
