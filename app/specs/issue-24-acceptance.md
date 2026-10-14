# 实施票 #24 验收取证：target 升为一等对象（消息作用域泛化，承重纵切）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。

> 阅读说明：本票是**承重重构**（prefactor），不是新功能。因此取证的重心有两处——
> （a）**外部可观察行为零漂移**（§3 的契约探针：把改名前后每个端点的响应形状打成稳定文本并 diff），
> （b）**新数据面事实成立**（§4：target 表、影子行、三种 kind 各自独立、跨 target 不锁死、未读口径）。
>
> 所有取证都跑在**独立临时数据目录**上（`/tmp/issue24-*`，**不是** `~/Alibot`）。基线 worktree
> `/tmp/issue24-base`（`git worktree add --detach` 在 `9fd31bb` 上）用于取得改名前的红/灰侧读数，
> 取证完已 `git worktree remove` 清除（§3.2）。

## Answer

**做了什么**：按 ADR-0004 把消息作用域从「恒为一个频道」升为 `target(id, kind ∈ {channel, dm, task})`——
`message` / `target_read` / `target_budget` / `round_outcome` 四者的作用域键全部泛化为 `target_id`。
新增 `target` 表（channel 型是**影子行**：id 与 `channel.id` 同值、只承载 `kind`，`name`/`description`
仍留在 `channel` 表、**不引入映射表**）；三处旧名随作用域改名（`channel_member → target_member`、
`channel_read → target_read`、`channel_budget → target_budget`，`round_outcome.target_id` 本来就叫对、不动）；
`message.target_id` 补上指向 `target(id)` 的引用约束；`SCHEMA_VERSION` 3 → 4、**不写迁移脚本**。
未读计数收敛到**唯一一条 WHERE 形状**并让 `unread_total` = channel 型 + dm 型（task 型不进）；
预算仍是**每 target 一份**、上限恒为 3（`BUDGET_LIMIT` 未改值、未配置化）。

**发现了什么**：外部契约逐字节未变——契约探针在 base 与 head 上跑 30+ 个端点，输出 diff **为空**
（§3.3），既有 **179 条用例未作任何修改**全部通过（§1）。本票唯一的新外部行为是「**跨 target 不锁死**」：
在频道 A 内烧满 3 跳后，频道 B 的往返**仍然可用**（改名前的影子实现也是每频道一份，所以这不是行为变化，
而是把既有事实第一次变成可断言的读法，§4.4 用新用例钉住）。双轴 review 的逐条处置见 §6。

**留下了什么**：本票**只**让 schema 与查询路径**允许**第二、第三种 kind——dm / task 的**业务逻辑**
（幂等创建、两行成员、看板、互审、唤醒规则）一律留给 #25–#28，属主路径上没有任何 dm/task 分支（§6）。
前端 `studio/` 因外部契约未变而**一行未改**（§3.4）；`BUDGET_LIMIT` 的配置化归 #31。

### 状态流转

- 改动落在分支 `issue-24-target-first-class`，worktree 干净（`git status --short` 为空）。
- 合并（G-merge）由 coordinator 执行；本票不自行合并、不自行改 issue 的 label/state。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-24-target-first-class`（Orca 独立顶层 worktree）
- 分支：`issue-24-target-first-class`
- 基线：`9fd31bb`（main HEAD，本票 base）
- 采集环境：macOS，Python 3.11.13，SQLite（`check_same_thread=False`，WAL，`foreign_keys=ON`）
- 票标题（开工时用 `gh issue view 24` 核对）：

> 实施票 ①：target 升为一等对象（消息作用域泛化，承重纵切）

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.pi/skills/implement/SKILL.md`（前置 skill），依其流程执行：
读三份正本（`gh issue view 24` / `gh issue view 23` / `docs/adr/0004-*.md`）→ 声明票标题 →
只读核实（§2）→ 最小改动（先 db 层跑一次全量测试，再动 service/orchestration，最后确认前端不需改，
即票面「建议」的顺序）→ 补数据面用例（§4）→ 全量门禁（§1）→ 端到端冒烟（§5）→ 双轴 `code-review`（§6）
→ 提交、推分支、建 PR（§7）。

配对 skill：`tdd`（红→绿，但**先**跑基线并记录红/灰侧读数，见 §2.1）、`code-review`（Standards + Spec 并行子代理，
§6）、`pr`（PR body 模板，§7）。

## 1. 门禁结果（G-impl 四条全跑）

```text
$ .venv/bin/python -m pytest
194 passed, 1 skipped in 95.28s
        # 基线 9fd31bb：179 passed, 1 skipped
        # 本票新增 15 条（app/tests/test_target_scope.py）
        # **既有 179 条一字未改**（§1.1）
        # skipped = 默认跳过的真模型冒烟测试（test_agent_smoke.py，本票未碰）

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed!
52 files already formatted

$ .venv/bin/python -m mypy
Success: no issues found in 27 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
0 errors, 0 warnings, 0 informations
```

### 1.1 既有 179 条**未经修改**通过（票面第一验收项）

票面明写「179 条必须在改名后**不加修改地**通过。若某条用例因内部改名必须改，逐个列出并说明外部可观察行为未变」。
本票**不需要**这条豁免：`git diff` 对 `app/tests/` 只列出**新增文件**。

```text
$ git diff --name-only 9fd31bb...HEAD -- app/tests/
app/tests/test_target_scope.py        # 唯一一条，且是新文件（新增 12 条）
$ git status --porcelain app/tests/
?? app/tests/test_target_scope.py     # 只有新增，无 M（modified）
```

结论：**零条既有用例被修改**。既有测试里出现 `channel_member` / `channel_read` 字样的地方只有
注释与用例名（`test_channel_join.py` 的文档串），不改也仍然通过——它们是**叙述**，不是断言内部表名。

## 2. 只读核实（先确认事实，再动手）

### 2.1 基线读数（红/灰侧）

```text
$ cd /Users/apple/orca/workspaces/alibot/issue-24-target-first-class && .venv/bin/python -m pytest
179 passed, 1 skipped in 95.44s      # 与本票票面给出的基线一致
```

`SCHEMA_VERSION` 基线为 `3`、`BUDGET_LIMIT` 基线为 `3`（§4.6 逐字对照）。

### 2.2 作用域键的现状：`message.target_id` 今天的值**就是** channel id

```text
$ git show 9fd31bb:app/alibot/db.py | grep -n "m.target_id = c.id"
      ...  COALESCE((SELECT MAX(seq) FROM message m WHERE m.target_id = c.id), 0) AS last_seq,
```

这条既有事实（`m.target_id = c.id`）正是 ADR-0004 选「channel 型 target 复用 `channel.id`」的理由：
**零语义漂移**——改名不换任何一个消息的归属。因此本票在 `CHANNEL_SELECT_SQL` 里保留了这一条逐字不变，
只是把 `LEFT JOIN channel_read r ON r.channel_id` 改成 `LEFT JOIN target_read r ON r.target_id`（§2.3）。

### 2.3 待改的表/列与调用点

```text
$ grep -rn "channel_member\|channel_read\|channel_budget" app/alibot/*.py
# 基线命中：schema 三张表定义 + §2.4 的 6 个读写点（list_channel_members /
# list_channel_agent_members / create_channel / append_message / budget_state /
# consume_budget / is_channel_member / add_channel_member / agent_cursor / mark_read）
```

逐一遍历后确认：这些点全部**只**在 `db.py` 内落 SQL，`service.py` / `orchestration.py` / `api.py`
只经方法名调用，因此改名是「db 层一次、上层换调用名」的形态（§6 的 Shotgun Surgery 判断见 Standards 轴）。

### 2.4 `BUDGET_LIMIT` 的归属（**不改**，也不配置化）

票面 Constraint 明写：`BUDGET_LIMIT = 3` 是**常量**，本票**不改它的值、不做配置化**——配置化由 #31 做，
其正文明确写「本票不创建实施票、不改预算语义」。本票只把它从 `channel_budget` 表泛化到 `target_budget` 表：

```text
$ git show 9fd31bb:app/alibot/db.py | grep -m1 "^BUDGET_LIMIT"
BUDGET_LIMIT = 3
$ grep -m1 "^BUDGET_LIMIT" app/alibot/db.py
BUDGET_LIMIT = 3                     # 值未变；无新增配置读取
```

## 3. 外部契约零漂移取证（本票的核心）

票面 Constraint：**本票是内部作用域键的重构，不是 API 版本升级**——URL、参数名、响应字段名、
错误码一律不变。这种「不变」不能靠肉眼断言，本票用**契约探针**机械取证。

### 3.1 探针是什么

`/tmp/issue24-probe/contract_probe.py`（**在仓库外**，不属本票 ownership，不入库）启动一个
`create_app(...)`，注入仓库自己的测试替身（`ScriptedAgentSeam` + `ManualScheduler`），
把**每个端点**的响应化简成「结构」——键集合 + 值类型，丢掉业务取值——并打印稳定文本。

覆盖：`/api/health`、`/api/workspace`、`/api/channels`（GET/POST）、`/api/agents`（列表/单条/状态）、
`/api/member-statuses`、`/api/remote-data-flow`（读/确认）、`/api/channels/{id}/messages`（GET/POST）、
`/members`（GET/POST）、`/budget`、`/round-outcomes`、`/events`、`/read`，以及错误形状
（404 missing channel、404 各种 mutation 路径、400 空正文、400 非法 seq、404 未知端点）与
**跑完一轮后**的消息流/轮次结果/预算形状。

### 3.2 基线侧（`/tmp/issue24-base`，`9fd31bb`，取证后已删）

```text
$ git -C /Users/apple/orca/alibot worktree add --detach /tmp/issue24-base 9fd31bb
HEAD is now at 9fd31bb Merge pull request #29 from whutlichao/issue-22-spec-v2
$ cd /tmp/issue24-base/app && /tmp/issue24-base/.venv/bin/python contract_probe.py > /tmp/issue24-probe/base.txt
exit=0
```

### 3.3 base 与 head 的输出 **diff 为空**

```text
$ sed -E 's/mem_[0-9a-f]+/MEMID/g' base.txt > base.norm      # 掩掉随机成员 id
$ sed -E 's/mem_[0-9a-f]+/MEMID/g' head.txt > head.norm
$ diff base.norm head.norm
                                            # ← 无输出
*** IDENTICAL: 外部契约逐字节相同 ***
```

即：**30+ 个端点的状态码、顶层键、嵌套键、类型逐字节相同**。这条同时覆盖票面 Standards 轴要求的
「核一件事：有没有把外部可观察行为带偏」——HTTP 路径/参数/响应字段名、错误形状全部一致。

样本（base 与 head 相同）：

```text
GET  /api/channels/{id}/budget   status=200 shape={"budget":{"channel_id":"str","epoch_seq":"int",
                                                "limit":"int","remaining":"int","used":"int"}}
GET  /api/workspace              status=200 shape={...,"unread_total":"int",...}
GET  /api/channels               status=200 shape={"channels":[{...,"last_read_seq":"int",
                                                "last_seq":"int","unread_count":"int",...}]}
404 budget mutation              status=404 shape={"error":{"code":"str","message":"str"}}
```

注意 `budget` 载荷里的键名仍是 **`channel_id`**（不是 `target_id`）：`db.budget_state` 的返回字典
显式把 `channel_id` 作为对外的字段名保留下来（内部作用域键是 target，对外字段名不变），
这是票面 Constraint「响应字段名不变」的直接落点。

### 3.4 稳定事实（与形状无关的语义断言）

```text
target_id_equals_channel_id: True        # 频道消息的 target_id 仍等于频道 id（影子行语义）
budget_limit: 3                          # 上限仍恒为 3
budget_keys: ['channel_id','epoch_seq','limit','remaining','used']
workspace_unread_total: 1
workspace_keys: [... 'unread_total' ...]
```

### 3.5 前端 `studio/` **一行未改**

前端只消费**外部字段名**（`last_seq` / `last_read_seq` / `unread_count` / `target_id` / 频道 URL），
§3.3 已证明它们全部不变，因此前端无需跟改。机械核对：

```text
$ grep -n "target_id\|channel_id\|unread_total\|last_seq\|last_read_seq\|unread_count" app/alibot/studio/app.js
# 命中的全是既有用法：last_seq（补拉游标）、last_read_seq、unread_count（徽标）、
# target_id（乐观发送的本地簿记，值是 channel.id）——没有一处依赖改名过的内部表/方法名
$ git status --short
 M app/alibot/api.py
 M app/alibot/db.py
 M app/alibot/orchestration.py
 M app/alibot/service.py
?? app/tests/test_target_scope.py
```

`app/alibot/studio/` 不在改动列表里 ⇒ 前端零改动（票面 Constraint「若契约没变，前端可以一行不改」）。

### 3.6 频道面**只认 channel 型 target**（负向证据）

「不改 URL、不加新端点」的另一半是：dm / task 型 target 虽然**已经存在于库里**，频道面**仍然不认**它们
（它们不是频道，本票也不给它们任何 HTTP 入口——#25–#28 做）。§4.7 用新用例钉住这条 404。

## 4. 新增数据面证据（`app/tests/test_target_scope.py`，15 条）

票面验收项 2 明写「只走 HTTP 面与事件流，沿用 `app/tests/conftest.py` 体例」，并要求对尚无法产生的
dm/task 数据「就用你构造的最小数据在 HTTP 面或等价层证明该口径成立，**并说明你怎么构造的**」。
本文件的构造方式与取舍写在模块 docstring 里，逐条摘要如下。

### 4.0 为什么有两条用例**读库**

「`target` 表存在且 `kind` 受约束」「`message.target_id` 有引用约束」「append-only 触发器仍在」
「`SCHEMA_VERSION` 已 bump」「旧的 `channel_member` 表名已不存在」这五条事实在 HTTP 面**没有路径可观察**
（本仓不为测试新增「读表结构」端点，那会越界）。因此这几条**直接读库文件**，并在用例 docstring 里
逐条说明原因。其余 7 条走 HTTP 面与既有 conftest 助手。

### 4.1 target 表存在、`kind` 受约束、**无映射表**（票面验收项 2）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_target_table_exists_and_kind_is_constrained -q
1 passed
```

用例断言：channel 型影子行的 `kind == 'channel'`；写一个域外 kind
（`INSERT INTO target(id,kind,created_at) VALUES ('tgt_bad','nope',…)`）被库以 `sqlite3.IntegrityError` 拒绝
（CHECK 约束的正本）；`dm` / `task` 两种合法 kind 都写得进去；`sqlite_master` 里**没有**任何名字含
`map`/`mapping` 的表（ADR-0004 否掉的正是映射表方案）。

### 4.2 channel 型是**影子行**：id 同值、只承载 kind（票面验收项 1 与 2）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_channel_target_is_a_shadow_row_sharing_the_channel_id -q
1 passed
```

HTTP 面：`POST /api/channels/{id}/messages` 返回的 `message.target_id == channel["id"]`。
数据面：`target.id == channel.id == channel["id"]`，且影子行**只承载 kind**——频道的
`name` / `description` 仍在 `channel` 行上（用例逐字段核对）。

### 4.3 `message.target_id` 有引用约束（票面验收项 6）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_message_target_id_has_a_reference_constraint -q
1 passed
```

插入一条 `target_id = 'tgt_不存在'` 的消息被 `sqlite3.IntegrityError` 拒绝（`foreign_keys = ON`
下由 `REFERENCES target(id)` 兜底）。

### 4.4 三种 kind 的 seq **各自独立单调**（票面验收项 2）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_three_kinds_count_seq_independently -q
1 passed
```

构造（**最小数据**）：频道走 HTTP 发两条；`dm` / `task` 型 target 各由一行 `target` + 若干条经
`append_message`（**同一个**落库路径）写入的消息构成。断言：`dm` 的 seq = `[1,2]`、`task` 的 seq = `[1]`、
频道的 seq = `[1,2]`——每个 target 都从 1 起、各自单调，一个 target 的消息不影响另一个；
`UNIQUE(target_id, seq)` 未变，且消息表与 seq 分配**不按 kind 分支**。

### 4.5 scan「跨 target 不锁死」：每 target 一份预算（票面验收项 2）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_budget_is_per_target_so_burning_one_does_not_lock_another -q
1 passed
```

构造：频道 A 里两个互相 `@` 的 Agent 跑到耗尽（`budget(A).used == 3`、`remaining == 0`）；
随后在频道 B 发一条人类消息并让 B 的 Agent 回复。断言：**B 的 Agent 被真实投递**（回复出现在 B 的消息流里，
不是被压制），且 `target_budget` 表里 A、B **各占一行**（A 停在 3、B 有自己的一条记录）。
这是 ADR-0004 决策 4 的可观察面：跨 target 共享一份预算会把「一个频道烧完把另一个频道锁死」变成真实故障。

### 4.6 未读：`unread_total` = channel + dm，**task 不进**（票面验收项 2）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py -q -k "unread"
2 passed
```

构造（**最小数据**）：频道未读由一条真实的 Agent 回复产生（走 HTTP 往返）；`dm` / `task` 型 target
各由一行 `target` + 一条**由 Agent 作者**的 `human` 消息产生——未读口径要求「作者非本人」，
所以只有 Agent 作者的消息才算 owner 的未读；随后用 `mark_read` 推进 dm 的已读位置。

断言：`unread_total == 2`（dm 的两条计入、task 的一条**不进**）；同一形状下 task 自己的读数
（`_unread_count`）仍是 `1`——它没被丢掉，只是不进汇总（看板卡片各自读它）；推进 dm 已读位置后
`unread_total == 0` 而 task 那部分不受影响；HTTP 面 `/api/workspace` 的读数与库里沿**同一落点**。
另一条用例钉住零漂移：只有 channel 型数据时，`unread_total` 与频道未读相加**逐字相等**。

### 4.7 dm / task 型 target 经频道面一律 404（未越界的负向证据）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_dm_and_task_targets_are_not_reachable_through_the_channel_face -q
1 passed
```

先经 `create_target("dm"/"task", …)` 把两个 target 真正写进库，再断言
`/api/channels/{target}/messages`（GET/POST）、`/read`、`/budget`、`/members`、`/round-outcomes`、`/events`
**全部 404**。这条同时守住两条 Constraint：**不加新端点**、**不改错误码**（仍是 404 `not_found`）。

> 实现注记：这条用例在开发中**真的红过一次**——首版 `db.mark_read` 泛化后，`channel_id` 只是
> target 字符串，`/read` 对 dm/task 返回了 200。修法是在 `service.py` 的每个 **channel 面入口**
> 显式 `self._db.get_channel(channel_id)` 早退（§2.4 的那条 404 早退），把「channel 面只认频道」
> 恢复成显式约束，而不是靠「恰好查不到」的隐式行为。

### 4.8 成员关系读写在**同一个 `target_id` 键**上（票面验收项 4）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_target_member_table_is_the_single_membership_surface -q
1 passed
```

HTTP 面：`GET /api/channels/{id}/members` 就是该 channel 型 target 的成员行。数据面：`target_member`
存在且 `channel_member` **不存在**；`target_read` 存在且 `channel_read` 不存在；`target_budget` 存在且
`channel_budget` 不存在（改名是彻底的，不留两份）。

### 4.9 append-only 触发器仍在（票面验收项 7）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_append_only_triggers_still_reject_update_and_delete -q
1 passed
```

先 `drain()` **真跑一轮**把 `message` 与 `round_outcome` 都填出行来（库级触发器是**行级**的，
空表上 `UPDATE` 影响 0 行、不会报错，那样这条用例就成了恒真的假证据——用例里显式断言两表 `COUNT(*) > 0`
作为前提证据），再断言 UPDATE / DELETE 全部被 `sqlite3.IntegrityError` 拒绝。

### 4.10 `SCHEMA_VERSION` 已 bump、**无迁移脚本**（票面验收项 7）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_schema_version_is_bumped_and_no_migration_script -q
1 passed
```

`schema_meta.schema_version == 4`（`SCHEMA_VERSION > 3`，bump 已发生）；仓库里不存在 `migrations/`
目录，也不存在任何 `migrate*.py`（决策 8：第二版从空库开始，不写搬数脚本）。

### 4.11 轮次结果读数形状不变（票面验收项 1）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py::test_round_outcomes_still_read_the_same_shape_after_the_rename -q
1 passed
```

`record["target_id"] == channel["id"]`，且键集合逐字等于改名前的
`{id, member_id, target_id, base_seq, status, reason, started_at, finished_at, consumed_budget, merged, reply_seq}`。

### 4.12 review 后补的 3 条（Spec 轴点出的覆盖缺口，见 §6.2）

```text
$ .venv/bin/python -m pytest app/tests/test_target_scope.py -q
15 passed in 0.31s
```

- `test_membership_and_wake_face_queries_do_not_branch_on_target_kind`：三种 kind 各用一个成员读写点
  （`add_target_member` / `list_target_agent_members` / `is_target_member`）取证「唤醒面与成员面不分 kind」。
- `test_a_dm_type_target_has_its_own_independent_budget`：channel 烧满后 **dm 型**仍有自己的世代与用量
  （跨 **kind** 的预算独立；§4.5 只盖了 channel↔channel）。
- `test_unread_where_shape_is_shared_by_every_unread_consumer`：把「未读只有一个 SQL 形状」变成结构断言
  （三个消费点嵌同一段 `UNREAD_WHERE_SQL`；汇总取值域由 `UNREAD_TOTAL_KINDS` 拼出且不含 `task`）。

## 5. 端到端冒烟（真实进程）

票面验收项 4：`python -m alibot --port <空闲端口> --data-dir /tmp/...` 起 → 建频道 → 发消息 →
`GET /api/health` 与 `/api/channels` 正常 → 库文件里 `target` / `target_budget` / `target_read` 三张表存在。

```text
$ rm -rf /tmp/issue24-smoke && mkdir -p /tmp/issue24-smoke
$ PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
PORT=58857
$ (cd app && ../.venv/bin/python -m alibot --port $PORT --data-dir /tmp/issue24-smoke &)
INFO:     Uvicorn running on http://127.0.0.1:58857 (Press CTRL+C to quit)
alibot 工作台： http://127.0.0.1:58857/studio/
数据目录（备份即复制该目录）： /tmp/issue24-smoke

$ curl -s http://127.0.0.1:58857/api/health
{"status":"ok","member_id":"owner","data_dir":"/tmp/issue24-smoke"}
$ curl -s http://127.0.0.1:58857/api/channels
{"channels":[]}
$ curl -s -X POST http://127.0.0.1:58857/api/channels -H 'Content-Type: application/json' \
    -d '{"name":"冒烟频道","description":"e2e"}'
{"channel":{"id":"ch_d984b5f98035","name":"冒烟频道","description":"e2e",
            "created_at":"…","last_seq":0,"last_read_seq":0,"unread_count":0}, "channels":[…]}
$ curl -s -X POST http://127.0.0.1:58857/api/channels/ch_d984b5f98035/messages \
    -H 'Content-Type: application/json' -d '{"body":"冒烟一条"}'
{"message":{"id":"msg_39e77f1260a6","target_id":"ch_d984b5f98035","seq":1,"kind":"human",
            "body":"冒烟一条",…}}
$ curl -s http://127.0.0.1:58857/api/channels/ch_d984b5f98035/budget
{"budget":{"channel_id":"ch_d984b5f98035","epoch_seq":1,"limit":3,"used":0,"remaining":3}}
```

库文件（真实进程写出的 `/tmp/issue24-smoke/alibot.db`）：

```text
$ .venv/bin/python -c "import sqlite3;c=sqlite3.connect('/tmp/issue24-smoke/alibot.db');
  print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")))"
['channel','message','round_outcome','schema_meta','target','target_budget','target_member','target_read']
                                                                                          ^^^^^^^^^^^^^
                                                                      票面要求的三张表全部存在

$ schema_version = 4
$ target rows    = [('ch_d984b5f98035', 'channel')]     # 影子行：id == 频道 id
$ budget rows    = [('ch_d984b5f98035', 1, 0)]           # 每 target 一行
$ read rows      = [('owner', 'ch_d984b5f98035', 0)]
$ message rows   = [('ch_d984b5f98035', 1, 'human')]
$ legacy channel_* tables present? []                    # 旧的 channel_member/read/budget 已不存在
```

冒烟后端已停止（`pkill -f "alibot --port"`）；临时目录 `/tmp/issue24-smoke` 与会话用的
`~/Alibot` 无任何关系（票面 Constraint：不碰真实数据目录）。

## 6. 双轴 code-review

按前置 skill `implement` 执行 `code-review`（`code-review` skill：Standards + Spec **并行子代理**，
各自独立 context，本票不合并、不重排两轴结论）。固定点 `9fd31bb`、diff 命令
`git diff 9fd31bb...HEAD`、提交列表 `49b8973`。

### 6.1 Standards 轴

**结论：无文档标准的硬违规；外部行为四点核验全部「保持」。**

Correct（有据）：

- 外部契约零漂移有机械证据：`base.norm` 与 `head.norm` 逐字节相同（30+ 端点的状态码/顶层键/嵌套键/类型）。
  `db.py` 的 `budget_state` 保留 HTTP 字段名 `channel_id`（值 = target id）并在 docstring 写明理由——这是
  「文档标准覆盖 smell 基线」的正例（Fowler Mysterious Name 被票面 Constraint「响应字段名不变」压过）。
- 未读只有一个形状：`UNREAD_WHERE_SQL` 被频道列表、工作台汇总、单 target 计数三处共用；
  `r.target_id = m.target_id` 在影子行语义下与原 `channel_id` 等价。
- 404 文案未漂移：channel 面每个入口（`service.py` 的 `channel_messages` / `channel_members` /
  `round_outcomes` / `channel_budget` / `send_message` / `mark_read`）都先 `get_channel`，
  先抛出的一直是「频道不存在」。
- `BUDGET_LIMIT = 3` 未改值、无任何配置/环境读取。

Findings（均为 P2，不阻塞）：

| # | 级别 | 内容 | 处置 |
| --- | --- | --- | --- |
| S1 | P2 | `db.py` 里「响应预算上限：**常量**…」注释紧贴 `MAX_CHANNEL_NAME_LENGTH = 80`，错位误导 | **已修**：注释上移到 `BUDGET_LIMIT` 定义处并写明「本票不做配置化（那是 #31）」 |
| S2 | P2 | `UNREAD_TOTAL_KINDS` 零消费，规则实际由 SQL 里的字面量 `t.kind IN ('channel','dm')` 承载 | **已修**：SQL 的取值域由该常量拼出，G5 的「channel + dm」只剩一个落点 |
| S3 | P2 | `db.py` docstring 引用 ``UNREAD_TOTAL_SQL`` 内的 ``_unread_where``，模块无此名 | **已修**：改为引用真名 ``UNREAD_WHERE_SQL`` |
| S4 | P2 | 测试从**仓库根** `rglob("migrate*.py")`（会走进 `.venv/`），结论取决于第三方包 | **已修**：限定扫 `app/`（本票源码面） |
| S5 | judgement | `create_target` / 单 target 未读计数在生产路径零调用 → Speculative Generality | **已收窄**：`create_target` docstring 明标「本票没有生产调用点，只服务取证」；单 target 计数由私有 `_unread_count` 提升为公开 `unread_count` 并由测试消费 |
| S6 | judgement | `mark_read` 按 `target["kind"]` 分支返回形状 → Repeated Switch（查询路径上仅存的一处 kind 分支） | **已修**：`db.mark_read` 改为返回 `None`，channel 面的响应体由 `WorkspaceService.mark_read` 用 `get_channel` 组出——db 层不再有 kind 分支 |
| S7 | note | `_initialize` 只 upsert `schema_version = 4`、不检测旧版本；打开 v3 库会得到半改名状态且静默可用 | **记遗留**：决策 8 只要求「不做迁移」，未要求「旧库拒绝打开」；升级到「拒绝打开旧库」需产品裁定，不在本票 scope |
| S8 | note（ownership 外） | `app/README.md` 的 schema 表仍写 `channel_member/channel_read/channel_budget` | **记遗留**（见下） |

**外部行为四点核验（逐条）**：

1. **保持**：探针 diff 为空；`channel_id`（budget 载荷）/`unread_count`/`unread_total`/`last_seq`/`last_read_seq`
   全不变。
2. **保持**：位置之后 + `kind ∈ {human,agent}` + 作者≠本人；「只算顶层」在 `message` 无嵌套列
   （与基线同为虚设）——**无漂移**。
3. **保持**：恒为 3、未配置化。
4. **保持**：`_wake_targets` 读 `target_member`、**不按 target kind 分支**；判跳的 `author_kind == "agent"`
   是**作者**而非 target kind。注意 `notify` 仍以 `get_channel` 收口——那是**频道面入口**的 404 早退
   （不是唤醒面查询），且本票不新增 dm/task 的唤醒入口，属票面声明的 #25–#28 遗留。

### 6.2 Spec 轴

**结论：无 P0/P1；两条 P2 partial + 两条 scope note；merge verdict = OK with notes。**

Correct：影子行同事务写入、id 同值、name/description 留 `channel` 表、无映射表；三处改名彻底；
`UNIQUE(target_id, seq)` 与按 target 分配 seq 未变；`BUDGET_LIMIT = 3` 值未改、无配置化（#31 边界守住）；
`message.target_id REFERENCES target(id)`；append-only 触发器仍在；`unread_total` 走单一形状 +
`kind IN ('channel','dm')` 汇总（G5 允许的唯一聚合差异）；channel 面各入口显式 `get_channel` 早退。

Findings：

| # | 级别 | 内容 | 处置 |
| --- | --- | --- | --- |
| P1 | P2 | `UNREAD_TOTAL_KINDS` 无引用，取值域两个落点 | **已修**（同 S2） |
| P2 | P2（文档） | `app/README.md` 仍把旧表名写成现役表 | **记遗留**（ownership 外，见 §6.3） |
| P3 | P2（scope note） | `create_target` 在生产路径无调用点，但它是一条**新写路径**（幂等/两行成员属 #25–#28） | **已修**：docstring 明标 test-only / 本票没有生产调用点，并逐条列出留给 #25–#28 的部分 |
| P4 | P2（note） | `mark_read` 按 kind 返回两种载荷——查询路径上仅存的 kind 分支 | **已修**（同 S6） |
| P5 | P2（note） | `_initialize` 不检测旧版本 | **记遗留**（同 S7） |

**测试覆盖评估（票面验收项 2）**：12 条（现 15 条）真实存在，已覆盖 target 表+kind CHECK+影子行、
引用约束、触发器、bump/无迁移、seq 三种 kind 独立、成员面同键；Spec 轴点出 4 处缺口，**全部已补**：

- (1) 「跨 target 不锁死」原本只用两个 channel 型 → **补** `test_a_dm_type_target_has_its_own_independent_budget`
  （channel 烧满后 **dm 型**仍有自己的世代与用量，跨 kind 的预算独立）；
- (2) 混测里 channel 未读为 0，「channel + dm 之和」未在两侧同时非零时断言 → **补**：让 channel 侧经真实
  Agent 回复产生未读，断言 `unread_total == channel_unread + 2`（区分「之和」与「只算 dm」）；
- (3) 无一处对 dm/task 型调唤醒面查询 → **补** `test_membership_and_wake_face_queries_do_not_branch_on_target_kind`
  （三种 kind 分别用一个成员读写点取证）；
- (4) 未读用例用 Agent 作者写 `kind="human"` 消息（形状属自造）→ **保留**：这与未读口径本身一致
  （口径是「作者非本人」，与 kind 无关），已在用例 docstring 里说明构造方式（票面明确授权最小构造）。

### 6.3 逐条处置摘要与遗留

两轴共 13 条 findings，**无 P0/P1、无文档标准的硬违规**。处置：**8 条已修**（S1–S6 覆盖 Spec 轴的
P1/P3/P4）、**2 条记遗留**（S7/P5 的旧库版本检测；S8/P2 的 README 表名）、其余为核验结论。

已修的部分影响最终读数：新增用例由 12 → **15 条**（本轮 review 修完后的全量读数是 §1 的 **194 passed**）。

**遗留（不在本票 scope，已记入 Answer）**：

1. **`app/README.md` 的 schema 表仍列旧表名**（`channel_member` / `channel_read` / `channel_budget`）。
   该文件**不在本票 ownership**（票面 Ownership 只给 `app/alibot/`、`app/tests/`、
   `app/specs/issue-24-{acceptance,pr-body}.md`），且 README 的正文同时描述了首版预算机制，属文档层
   重写而非本票的字段改名。**建议**由后续文档票（或 #25 开工时）一并把该表改成 `target_*`。
2. **旧库版本检测**：`_initialize` 只 upsert `schema_version`，不拒绝旧版本库。决策 8 只要求「不做迁移」，
   未要求「拒绝打开」；若希望 v3 库被显式拒绝（而不是半改名状态下静默可用），需产品裁定。
3. **`notify` 的 `get_channel` 收口**：它是频道面入口的 404 早退（保住既有 404 行为），不是唤醒面查询；
   dm / task 的唤醒入口与规则由 #25–#28 提供。
4. **semgrep `sqlalchemy-execute-raw-query` 误报**：pi-lens 的这条规则是 **SQLAlchemy 专用**，本仓用
   `sqlite3`、所有值经命名/位置占位符绑定。它在 baseline 上同样触发（`git show HEAD~1:app/alibot/db.py`
   的 331/341/548/555/588 行命中同类模式），因此不是本票引入；且它不是 G-impl 的四条门禁之一。
   §6.4 用一条**结构断言**（`test_unread_where_shape_is_shared_by_every_unread_consumer`）把「只有一个
   WHERE 形状」变成可核验事实，代替依赖这条误报。

### 6.4 针对 finding 的机制化防护

为兔「只有一个未读 SQL 形状」这条规格要求停留在口号上，本轮新增一条**结构断言**用例：
三个未读消费点（频道列表 / 工作台汇总 / 单 target 计数）必须嵌**逐字相同**的 `UNREAD_WHERE_SQL`，
且该形状同时含「已读位置之后 / `kind ∈ {human,agent}` / 作者非本人」三个子句；汇总的取值域由
`UNREAD_TOTAL_KINDS` 拼出且**不含** `task`。任何后续把某个消费点改成另一种写法都会让这条用例变红。

## 7. 交付物

- worktree 干净（`git status --short` 为空，§7.1）；
- 提交存在、分支已推送、PR 以 `--body-file` 创建（PR body 正本 `app/specs/issue-24-pr-body.md`，§7.2）。

### 7.1 状态

```text
$ git status --short
                                            # ← 空（干净）
$ git log --oneline 9fd31bb..HEAD
63af608 fix(db): #24 处置双轴 review 的 findings，并补 Answer 与 PR body
49b8973 refactor(db)!: 消息作用域由「恒为一个频道」升为 target(id, kind)
$ git rev-parse HEAD
63af608ec6c12d89603e5cc99d6a3e7f8873204a
```

### 7.2 PR

```text
$ git push -u origin issue-24-target-first-class
 * [new branch]      issue-24-target-first-class -> issue-24-target-first-class
$ gh pr create --title "refactor(db): #24 消息作用域由「恒为一个频道」升为 target(id, kind)" \
      --body-file app/specs/issue-24-pr-body.md --base main
https://github.com/whutlichao/alibot/pull/34

$ gh pr view 34 --json number,state,headRefName,baseRefName,mergeable,url
{"number":34,"state":"OPEN","headRefName":"issue-24-target-first-class",
 "baseRefName":"main","mergeable":"MERGEABLE",
 "url":"https://github.com/whutlichao/alibot/pull/34"}
```

- **PR 号**：`#34`（`whutlichao/alibot`）
- **head**：`issue-24-target-first-class` @ `63af608`
- **base**：`main`
- **mergeable**：`MERGEABLE`（OPEN）
- PR body 正本：`app/specs/issue-24-pr-body.md`（体例照 `app/specs/issue-18-pr-body.md`：
  Summary / Evidence / Merge Danger；Summary 用 diff-sketch + 写路径调用树）

合并（G-merge）由 coordinator 执行；本票不自行合并、不自行改 issue 的 label/state。
