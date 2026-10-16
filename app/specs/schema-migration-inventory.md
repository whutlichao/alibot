# Schema 迁移沿革清单：`SCHEMA_VERSION` 3 → 12

> **性质**：本文件是迁移实现的**原料清单**——逐版记录「库级结构改了什么、证据在哪、要不要搬数据、升版时卡在哪」。
> 它**不是**迁移设计，**不是**实现，不含迁移脚本，也不改任何源码。
>
> **取证基线**：`main` @ `36e452eab43414ba857156cc9cab9ebf22f3b0e5`（`SCHEMA_VERSION = 12`）
> **取证时间**：2026-10-16 22:45 CST（UTC 2026-10-16T14:45:02Z）
> **取证者**：本票 worker（worktree 分支 `schema-history-inventory`，base = `main`）
>
> **未核实项集中在 §5**。任何一条结论若本文件没有给出可复核的命令或路径，就按「未核实」对待。

---

## 0. 怎么读这份清单

### 0.1 版本号语义（必读，否则会读错下面每一节）

`SCHEMA_VERSION` 在本仓**不是**「每次结构变动 +1」的累积计数器，而是**跨在途票的号段分配**。
`app/alibot/db.py:44-56` 的注释是这条纪律的正本，原文要点的可复核位置：

```sh
sed -n '44,57p' app/alibot/db.py          # 号段注释 + SCHEMA_VERSION = 12
```

注释明确：「号段是**跨在途票的分配**而不是「每次 +1」」，并给出原始分配表
（`7 = #61`、`8 = #68`、`9 = #67`、`10 = #71`、`11 = #58`、`12 = #65`），
随后在 `db.py:50-56` 用「补收尾纠正」改写为：**#67 实际取 11（不是预留的 9），12 归 #65**。

两个直接后果，决定了本清单的读法：

1. **main 上实际出现过的读数序列是**：`3 → 4 → 5 → 6 → 7 → 10 → 10 → 11 → 12`
   —— **8 与 9 从未作为 `main` 的读数存在过**（它们只是分支局部值，见 §2.5 / §2.6）。
2. **同一个读数可能对应不同结构**。例：`7f1cb99`（PR #79 合并点）与 `e1c523d`（PR #80 合并点）
   读数都是 `10`，但后者比前者多一张 `reminder` 表。因此「升到 v10」不是一句有唯一含义的话——
   本清单在 §2 里对每个读数都注明它对应的是**哪个 commit 的树**。

闸门纪律（决定「迁移」这件事在本仓的现状）：

- `app/alibot/db.py:830` `_assert_schema_compatible()` 在**任何 schema 变更之前**比对
  `schema_meta.schema_version` 与 `SCHEMA_VERSION`，不等即抛 `SchemaVersionMismatch`。
- `app/alibot/repair.py:3` 首段原文：**「这不是自动迁移。」** 它只在显式
  `python -m alibot --repair-targets` 时运行（`app/alibot/__main__.py:47,94-97`），
  处置对象是 **#24 遗留的一次具体事故**，不是通用跨版本升级路径。

### 0.2 取证方法（为什么不是「读 DDL 文本」）

每一版的结构都是**把该 revision 的 `SCHEMA` 字符串真的建进一个内存 SQLite 库**，
再用 `PRAGMA table_info / index_list / index_info / foreign_key_list` 与 `sqlite_master`
读出来。理由：列序、`NOT NULL`、默认值、外键、索引（含 partial）、触发器的**真实解析形态**
以 SQLite 为准，手工读文本容易漏项。本机环境：Python 3.14.7 / SQLite 3.53.4。

> 本票用的探针脚本在 worktree 的 `.scratch/schema_probe.py` 与 `.scratch/schema_diff.py`
> （`.scratch/` 已被 `.gitignore` 忽略，**不入库**）。下面 §0.3 给的是**不依赖这两个脚本**的
> 自包含命令（§0.3-B 会把它写成一个临时脚本，本文件各处证据块都引用它），供抽检复现。
> 本文中形如 `sh /tmp/canonical_probe.sh <sha>` 的命令，都指 §0.3-B 生成的那个临时脚本。

### 0.3 通用取证命令（抽检用）

**A. 号段与版本读数**（本机 `git` 若因 `GIT_CONFIG_*` 环境变量缺失报
`error: missing config key GIT_CONFIG_KEY_0`，用 `env -u` 变通，**不要改 git 配置**）：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 \
  git show <sha>:app/alibot/db.py | grep -n '^SCHEMA_VERSION'
```

**B. 结构清单（表 / 索引 / 触发器）**——把下面的块整段粘进 shell，生成 `/tmp/canonical_probe.sh`，
之后对任一 revision 运行 `sh /tmp/canonical_probe.sh <sha>`：

```sh
cat > /tmp/canonical_probe.sh <<'SH'
python3 - "$1" <<'PY'
import os, re, sqlite3, subprocess, sys
sha = sys.argv[1]
env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
for path in ("app/alibot/db.py", "app/worksplice/db.py"):   # #20 改名前的路径
    p = subprocess.run(["git", "show", f"{sha}:{path}"], capture_output=True, text=True, env=env)
    if p.returncode == 0:
        src = p.stdout; break
else:
    sys.exit(f"git show {sha} failed")
m = re.search(r'^SCHEMA_VERSION = (\d+)', src, re.M)
schema = re.search(r'^SCHEMA = """(.*?)^"""', src, re.S | re.M).group(1)
con = sqlite3.connect(":memory:"); con.executescript(schema)
print("SCHEMA_VERSION =", m.group(1) if m else "?")
for typ in ("table", "index", "trigger"):
    names = [n for (t, n) in con.execute(
        "select type, name from sqlite_master where type=? and name not like 'sqlite_%' order by name", (typ,))]
    print(f"{typ}({len(names)}): " + ", ".join(names))
PY
SH
```

**C. 「这个改动是谁引入的」**：`git show --stat --format='%H%n%s%n%ad' <sha>`；
**D. 全仓搜同一字符串的引入点**：`git log --all --oneline -S'<字符串>' -- app/alibot/db.py`。

---

## 1. 一览表

| 读数 | 引入 commit（分支提交） | 合入 main 的 merge | PR | issue | main 是否读到 | 结构变化（净） | 需搬既有行？ |
|---|---|---|---|---|---|---|---|
| 3 | `89ad07a` | `25108fb` | [#7](https://github.com/whutlichao/alibot/pull/7) | #4 | 是（基线） | 新增 `channel_budget` 表 | 否（新表） |
| 4 | `49b8973` | `6d4e6f8` | [#34](https://github.com/whutlichao/alibot/pull/34) | #24 | 是 | 新 `target` 表；`channel_member/read/budget` → `target_*` 三处改名；`message`/`round_outcome` 加 `target(id)` 引用 | **是**（三表改名 + 影子行） |
| 5 | `4e57b97` | `a1afd67` | [#40](https://github.com/whutlichao/alibot/pull/40) | #25 | 是 | 新 `task` 表；`message.task_ref` 可空列；2 条 BEFORE INSERT 触发器 | 否（纯加法） |
| 6 | `0c2ef85` | `6c617dc` | [#41](https://github.com/whutlichao/alibot/pull/41) | #27 | 是 | `target.dm_peer_member_id` 可空列；`idx_target_dm_peer_unique` 部分唯一索引 | 否（纯加法） |
| 7 | `f0426c1` | `6cef34a` | [#74](https://github.com/whutlichao/alibot/pull/74) | #61 | 是 | 新 `workspace_settings` 单行表 | 否（纯加法） |
| 8 | `65c0fe2` | `e1c523d`（读数已 10） | [#80](https://github.com/whutlichao/alibot/pull/80) | #68 | **否**（分支局部） | 新 `reminder` 表 | 否（纯加法） |
| 9 | `d386794` | `b619336`（读数 11） | [#83](https://github.com/whutlichao/alibot/pull/83) | #67 | **否**（分支局部） | 新 `attachment` 表 + 索引 + 3 触发器 | 否（纯加法；字节在库外） |
| 10 | `cd23ac5` | `7f1cb99` | [#79](https://github.com/whutlichao/alibot/pull/79) | #71 | 是 | `workspace_settings.theme`（`NOT NULL DEFAULT 'system'`）+ CHECK 词表扩展 | 否（有默认值） |
| 11 | `f0d8426` | `b619336` | [#83](https://github.com/whutlichao/alibot/pull/83) | #67 | 是 | 附件触发器由 1 条改为 2 条（`attachment_no_rebind` → 拆成 metadata/binding 两条） | 否（触发器替换） |
| 12 | `a700d05` | `36e452e` | [#84](https://github.com/whutlichao/alibot/pull/84) | #65 | 是 | `agent_config.auto_claim`（`NOT NULL DEFAULT 0`）+ CHECK 扩展；`task.claimed_at` 可空列 | 否（有默认值） |

> **读表提示**：`8` 与 `9` 的「合入 main 的 merge」一列写的是承载其**结构**的那次合并；
> 那两次合并时 main 的读数已分别是 `10` 与 `11`，不是 8/9。详见 §2.5 / §2.6。

---

## 2. 逐版沿革

### 2.1 `3 → 4`：target 升为一等对象（#24）——**本区间唯一一次「必须搬既有行」的改动**

**引入**：分支提交 `49b8973dce8f821130d36a237d9f9f6959cb8013`
（`refactor(db)!: 消息作用域由「恒为一个频道」升为 target(id, kind)`）；
合入 main 的 merge `6d4e6f8`（`Merge pull request #34 from whutlichao/issue-24-target-first-class`）。
issue **#24**，PR **#34**；规划正本 `app/specs/issue-22-design-notes.md`（Q1/Q3 裁定），
实现验收 `app/specs/issue-24-acceptance.md`、`app/specs/issue-24-pr-body.md`，ADR `docs/adr/0004-target-becomes-a-first-class-object.md`。

**库级差异**（基线 = `23e9ed0`（main 的最后一个 v3 树），对比 = `6d4e6f8`）：

- **新增表**：`target(id PK, kind NOT NULL CHECK (kind IN ('channel','dm','task')), created_at NOT NULL)`。
- **三处表名 + 列名改名**（逐列对照，不是「新表旧表各建一张」的语义）：
  | v3 | v4 | 备注 |
  |---|---|---|
  | `channel_member(channel_id, member_id)` | `target_member(target_id, member_id)` | 复合主键列改名；索引 `idx_channel_member_member` → `idx_target_member_member` |
  | `channel_read(member_id, channel_id, last_read_seq)` | `target_read(member_id, target_id, last_read_seq)` | 索引无（主键即索引） |
  | `channel_budget(channel_id, epoch_seq, used)` | `target_budget(target_id, epoch_seq, used)` | CHECK `(used >= 0)` 原样保留 |
- **既有表加引用约束**（列本身**没动**——`message.target_id` / `round_outcome.target_id` 在 v3 就已经叫 `target_id`，只是没有外键）：
  - `message.target_id` 加 `REFERENCES target(id)`；
  - `round_outcome.target_id` 加 `REFERENCES target(id)`。
- **删除**：`channel_member` / `channel_read` / `channel_budget` 三张旧表；
  索引 `idx_channel_member_member` 随之消失。
- **CHECK / NOT NULL / 默认值 / UNIQUE**：区间内 `round_outcome.status` 的 CHECK 词表、`message` 的
  `UNIQUE(target_id, seq)`、其余列的 `NOT NULL` 与默认值**均无变化**
  （本节的结构 diff 未报出任何 `~col` / `~checks` / `~inline_unique` 行；方法见 §0.2）。

**是否需搬迁数据：是。** `target` 的 channel 型行是**影子行**——`id` 与 `channel.id` 同值、
只承载 `kind`（`app/alibot/db.py:11-15`；`CONTEXT.md:72-73`）。因此升 v4 要把
「每个既有 channel → 一行 `kind='channel'` 的 target」+「三张旧表的行按 `channel_id → target_id`
逐列改名搬进新表」。**ADR-0004 与规格决策 8 明确「不做迁移、新库从空数据开始」**
（`docs/adr/0004-target-becomes-a-first-class-object.md:20`；
`app/specs/issue-24-acceptance.md:352-359` 断言「仓库里不存在 `migrations/`，也不存在任何 `migrate*.py`」）。
即：**这次改动的搬迁需求是真实存在的，但当时被「不写迁移脚本」的决策显式搁置了。**

**迁移要点与风险**：

1. **搬迁顺序是承重的**：另外三张新表的引用约束都指向 `target`，所以 `target` 影子行必须**最先**落，
   否则新表的行会因外键失败。`repair.py` 的 `_REPAIR_ORDER`（`app/alibot/repair.py:81`）就是这个顺序。
2. **只补三张、漏掉第四张会制造比 404 更坏的假象**：`repair.py:29-33` 原文记录——
   只补 `target` / `target_member` / `target_read` 的话，`GET /api/channels/{id}/budget` 会从
   **404 变成「200 且 `used` 归零」**，即「沉默地抹掉一个已经耗尽的预算」。
3. **改名涉及数据复制，不是 DDL 改名**：SQLite 的 `ALTER TABLE ... RENAME` 在**列**层面可用，
   但本仓当时选择的路径是「新表名 + 新列名」的建表/复制；无论如何，列名映射
   （`channel_id → target_id`）必须逐列显式写出，不能赌列顺序（`repair.py:49-52` 同款理由）。
4. **#37 的半改名事故（本区间的直接后遗症）**：v4 的 `_initialize` 一度会**静默接受 v3 库**，
   打开后 `schema_version` 被就地改写成 `4`（`INSERT ... ON CONFLICT DO UPDATE`，`db.py` 的
   `_initialize`），四张新表建出来但**全空**，旧表数据仍在——症状是「列表页 200 看着正常、
   点进去成员/预算/发消息/标读全部 404」。修复在 `8c9899d`（PR **#38**，merge `7833322`，
   issue **#37**）：把 `_assert_schema_compatible()` 提到**任何 schema 变更之前**，
   并补 `app/alibot/repair.py` 这个显式补齐入口。**#37 不改 schema（v4 → v4）**，
   所以它不在本清单的读数序列里，但它是 v4 迁移风险的正本。红/绿证据见
   `app/specs/issue-37-acceptance.md`（§1 红证据给出了 base `6d4e6f8` 上的实测读数）。
5. **版本闸门让「迁移」必须是一个新入口**：现状是旧库被**拒绝**而不是被升级
   （`db.py:830` `_assert_schema_compatible`），所以要实现 3→4，不能靠「让 `_initialize` 建表」——
   所有 DDL 都是 `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS`，对既有库是 no-op。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show --stat --format='%H %s' 49b8973
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 23e9ed0:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS channel_read/,/^);/p'
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 6d4e6f8:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS target_read/,/^);/p'
sh /tmp/canonical_probe.sh 23e9ed0     # 期望：SCHEMA_VERSION = 3；table(10) 含 channel_member/channel_read/channel_budget
sh /tmp/canonical_probe.sh 6d4e6f8     # 期望：SCHEMA_VERSION = 4；table(11) 含 target/target_member/target_read/target_budget
sed -n '44,57p' app/alibot/db.py           # 号段注释
sed -n '1,40p' app/alibot/repair.py        # #37 事故与补齐纪律
grep -n 'repair_targets' app/alibot/__main__.py
```

---

### 2.2 `4 → 5`：任务看板与线程（#25）

**引入**：分支提交 `4e57b9709`（`feat(tasks): add task board and threads`）；
merge `a1afd67`（`Merge pull request #40 from whutlichao/issue-25-task-board`）。
issue **#25**，PR **#40**；ADR `docs/adr/0005-dispatching-a-task-is-a-human-utterance.md`、
`docs/adr/0006-task-state-lives-on-the-row-history-in-the-stream.md`。

**库级差异**（`6d4e6f8 → a1afd67`）：

- **新增表** `task`：

  ```sql
  CREATE TABLE IF NOT EXISTS task (
    id                 TEXT PRIMARY KEY,
    target_id          TEXT NOT NULL UNIQUE REFERENCES target(id),
    channel_id         TEXT NOT NULL REFERENCES channel(id),
    title              TEXT NOT NULL CHECK (length(trim(title)) > 0),
    status             TEXT NOT NULL CHECK (status IN ('todo','in_progress','in_review','done','closed')),
    assignee_member_id TEXT REFERENCES member(id),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (target_id, id)
  );
  ```

  两条 `UNIQUE` 都要留意：列级 `target_id UNIQUE`（一张任务 target 只挂一行 task）与
  表级 `UNIQUE(target_id, id)`。
- **既有表加列**：`message.task_ref TEXT`（**可空、无默认值**）。
- **既有表加引用（复合外键，2 列 → 2 列）**：
  `FOREIGN KEY (target_id, task_ref) REFERENCES task(target_id, id)`——它正是 `task` 上
  `UNIQUE(target_id, id)` 存在的理由（复合外键需要目标列组有唯一索引）；
  存量行之所以不被它拦下，是因为 `task_ref` 为 `NULL`（SQLite 规则：子键任一列为 `NULL` 即不校验）。
- **新增触发器**（2 条，均 `BEFORE INSERT ON task`）：
  `task_target_must_be_task`（`task.target_id` 必须指向 `kind='task'` 的 target）、
  `task_channel_must_be_channel`（`task.channel_id` 必须指向 `kind='channel'` 的 target）。
- CHECK / NOT NULL / 默认值 / UNIQUE：**除新增项外无变化**（无 `~col`、无既有索引改动）。

**是否需搬迁数据：否（纯加法）。** 新表为空即可；`message.task_ref` 对存量行取 `NULL`，语义正确
（旧消息不指向任何任务）。存量行不会被两条 `BEFORE INSERT` 触发器校验（它们只拦新增）。

**迁移要点与风险**：

1. 两条触发器是**只插不校验存量**的（`BEFORE INSERT`），因此存量数据的完整性不因它们改变；
   但**新插入**的 task 行必须在同一事务里已有对应 target / channel 行，否则报错。
2. `message` 新增的复合外键 `(target_id, task_ref) → task(target_id, id)` 依赖 `task` 上的
   `UNIQUE(target_id, id)` 索引。**重建 `message` 或 `task` 表时必须把这个列组唯一约束一起保留**，
   否则外键会建不上或语义改变。
3. 迁移时若把 task 行搬进新库，`id` 与 `target_id` 都要原样保留，否则
   `message.task_ref` 会把消息指到不存在的任务上（`task_ref` 非空的消息行会违反外键）。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show --stat --format='%H %s' 4e57b97
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show a1afd67:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS task/,/^);/p'
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show a1afd67:app/alibot/db.py | grep -n 'task_target_must_be_task\|task_channel_must_be_channel'
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show a1afd67:app/alibot/db.py | grep -n 'FOREIGN KEY (target_id, task_ref)'
sh /tmp/canonical_probe.sh a1afd67     # 期望：SCHEMA_VERSION = 5；trigger(6) 含两条 task_*
```

---

### 2.3 `5 → 6`：DM 与「一对一唯一」（#27）

**引入**：分支提交 `0c2ef8573`（`feat(dm): add idempotent direct messages`）；
merge `6c617dc`（`Merge pull request #41 from whutlichao/issue-27-dm`）。
issue **#27**，PR **#41**。同一读数 `6` 也被 `issue-26-task-write-semantics` 分支使用
（`git for-each-ref` 显示其 tip 读数为 6）——这是「号段共享」的现场例子，**不代表 v6 有第二套结构**：
`v6` 的结构差异以本节的 diff 为准。

**库级差异**（`a1afd67 → 6c617dc`）：

- **既有表加列**：`target.dm_peer_member_id TEXT`（可空、无默认值）+ `REFERENCES member(id)`。
- **新增索引**（**部分唯一索引**，本区间唯一的「UNIQUE 收紧」）：

  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS idx_target_dm_peer_unique
    ON target(dm_peer_member_id)
    WHERE kind = 'dm' AND dm_peer_member_id IS NOT NULL;
  ```

- 表 / 触发器 / CHECK 词表：无变化。

**是否需搬迁数据：否（纯加法）。** 存量行的 `dm_peer_member_id` 全为 `NULL`，而索引的
`WHERE` 子句显式排除了 `NULL`，因此不会冲突。**但搬迁时要注意**：如果目标库已有重复的
`kind='dm'` + 相同 `dm_peer_member_id` 行，建这条索引会失败——唯一性是新收紧的约束，
存量数据不保证满足。

**迁移要点与风险**：

1. 这是「**UNIQUE 收紧**」类改动：`CREATE UNIQUE INDEX IF NOT EXISTS` 在已存在冲突行的库上会**报错**，
   不会静默成功。迁移脚本需要先检出并处置重复行（或拒绝升级），不能假设一定建得上。
2. 索引是 **partial 的**：`kind='dm' AND dm_peer_member_id IS NOT NULL`。任何重建表的动作
   都必须原样保留 `WHERE` 子句，否则会把「channel/task 型允许同 `NULL`」变成意外约束。
3. 列可空无默认值——不需要回填，`NULL` 就是「非 DM 或 peer 未定」。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show --stat --format='%H %s' 0c2ef85
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 6c617dc:app/alibot/db.py | grep -n 'dm_peer_member_id\|idx_target_dm_peer_unique'
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git for-each-ref --format='%(refname:short)' refs/remotes/origin | while read r; do printf '%s ' "$r"; env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show "$r:app/alibot/db.py" 2>/dev/null | grep -m1 '^SCHEMA_VERSION' || echo -; done
sh /tmp/canonical_probe.sh 6c617dc     # 期望：SCHEMA_VERSION = 6；index(4) 含 idx_target_dm_peer_unique
```

---

### 2.4 `6 → 7`：工作区设置面（#61）

**引入**：分支提交 `f0426c1e1`（`feat(settings): 工作区设置面：语言偏好 + 消息密度（GET/PATCH /api/settings）`）；
merge `6cef34a`（`Merge pull request #74 from whutlichao/issue-61-i18n-foundation`）。
issue **#61**，PR **#74**；母规格 `app/specs/issue-60-spec.md`（决策 **D13**：号段先后）、
`app/specs/issue-60-acceptance.md`；ADR `docs/adr/0009-i18n-is-two-layers-with-a-stored-locale.md`。

**库级差异**（`9e3657b → 6cef34a`；`9e3657b` 是 `6cef34a` 之前的 main 树，读数 6）：

- **新增表** `workspace_settings`（**单行表**：`CHECK (id = 1)`）：

  ```sql
  CREATE TABLE IF NOT EXISTS workspace_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    locale          TEXT NOT NULL DEFAULT 'zh-CN'  CHECK (locale IN ('zh-CN','en')),
    message_density TEXT NOT NULL DEFAULT 'comfortable' CHECK (message_density IN ('comfortable','compact')),
    updated_at      TEXT NOT NULL
  );
  ```

- 表 / 列 / 索引 / 触发器：除此之外无变化。

**是否需搬迁数据：否（纯加法）。** 空表也会被 `_initialize` 用
`INSERT INTO workspace_settings(...) VALUES (1, 缺省值...) ON CONFLICT(id) DO NOTHING`
补上那一行（`app/alibot/db.py` 的 `_initialize`，搜 `ON CONFLICT(id) DO NOTHING` 可定位），
缺省值 = 源语言 `zh-CN` + `comfortable`。存量库不搬到这行也不影响启动（读取路径有缺省），
但**迁移到新库时应补这一行**，否则「设置面可读」这条纪律落空。

**迁移要点与风险**：

1. 单行表靠 `CHECK (id = 1)` 而不是 `UNIQUE`/触发器钉死；搬行时必须保留 `id = 1`，
   否则 CHECK 会拒。
2. `locale` 的默认值在**两个**地方出现：DDL 的 `DEFAULT 'zh-CN'` 与 Python 常量
   `DEFAULT_LOCALE = SOURCE_LOCALE`（`db.py:71`）。DDL 里是字面量、Python 里是符号——
   迁移/重建表时若把 DDL 默认值写死成别的语言，两边会漂移。
3. 版本号协调事实：`issue-60-spec.md` 的 D13 与 `issue-60-acceptance.md:31` 明确「本轮库内改动
   落地取 7；**#35 的 `silent`（#58）顺延到 8**」。实际后续号段并未按此推进（见 §2.5–§2.8），
   这是一条**已发生的历史漂移**，迁移实现不能拿规格里的号段当结构定义。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show --stat --format='%H %s' f0426c1
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 6cef34a:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS workspace_settings/,/^);/p'
grep -n 'D13' app/specs/issue-60-spec.md app/specs/issue-60-acceptance.md
sh /tmp/canonical_probe.sh 6cef34a     # 期望：SCHEMA_VERSION = 7；table(13) 含 workspace_settings
```

---

### 2.5 `7 → 8`（**分支局部**：reminder 表，#68）

**引入**：分支提交 `65c0fe2c9`（`feat(reminders): 提醒是库内对象：reminder 表 + 增删读端点 + rail 入口与弹窗（#68）`）。
**main 从未以读数 8 存在**：该结构随 PR **#80** 合入 main 时，main 的读数已经是 `10`
（`e1c523d`；`git show e1c523d:app/alibot/db.py | grep '^SCHEMA_VERSION'` → `10`）。
issue **#68**，PR **#80**；ADR `docs/adr/0012-reminders-are-backend-objects.md`。

**库级差异**（`65c0fe2^ → 65c0fe2`，即相对 v7）：

- **新增表**：

  ```sql
  CREATE TABLE IF NOT EXISTS reminder (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL CHECK (length(trim(text)) > 0),
    time       TEXT NOT NULL,
    channel_id TEXT REFERENCES channel(id),     -- 可空
    created_at TEXT NOT NULL
  );
  ```

- 其余无变化。

**是否需搬迁数据：否（纯加法）。**

**迁移要点与风险**：

1. `channel_id` 可空、`REFERENCES channel(id)`（注意：引用的是 **channel** 而不是 target）——
   迁移时别顺手改成 target 引用，那会改变语义。
2. 该结构在 main 上的落点是 `e1c523d`（读数 10）。任何「按读数取结构」的迁移实现
   都必须用 **commit** 而不是读数来定位这一版（这正是 §0.1 的警告）。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show --stat --format='%H %s' 65c0fe2
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 65c0fe2:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS reminder/,/^);/p'
sh /tmp/canonical_probe.sh 65c0fe2     # 期望：SCHEMA_VERSION = 8；table(14) 含 reminder
sh /tmp/canonical_probe.sh e1c523d     # 期望：SCHEMA_VERSION = 10；table(14) 含 reminder
```

---

### 2.6 `8 → 9`：**未发生**（v9 从 v7 分出，不是从 v8 演进）

**事实**：读数 9 由分支提交 `d386794`（issue **#67**）设置，其 **parent 的读数是 7**
（`git show d386794^:app/alibot/db.py | grep '^SCHEMA_VERSION'` → `7`）。
因此 **main 上不存在「8 → 9」这条边**，也不存在「v8 的结构 + 附件表」这样一棵树。

**库级差异**（`d386794^ → d386794`，即相对 v7）：

- **新增表**：

  ```sql
  CREATE TABLE IF NOT EXISTS attachment (
    id            TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL CHECK (size_bytes >= 0),
    mime_type     TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    message_id    TEXT REFERENCES message(id)     -- 可空 = 尚未绑定
  );
  ```

- **新增索引**：`idx_attachment_message ON attachment(message_id)`（非唯一）。
- **新增触发器（3 条）**：`attachment_no_delete`（append-only）、
  `attachment_no_rebind`（`OLD.message_id IS NOT NULL` 时禁止 UPDATE）。
  > 该版随后被 #67 自己的 review 处置提交 `138cefb` 改写（`attachment_no_rebind` 拆成两条），
  > 最终随 11 落到 main——见 §2.8。
- 其余无变化。

**是否需搬迁数据：否（纯加法）。** 附件**字节不在库里**，落本机数据目录
（`ATTACHMENTS_DIR_NAME = "attachments"`，ADR `docs/adr/0014-attachments-are-separate-objects-in-the-data-directory.md`）；
库里只有元数据行。纯加法，存量行无需改写。

**迁移要点与风险**：

1. `message_id` 可空是「先上传、后绑定」的承载点；迁移时若把它写成 `NOT NULL`，
   会破坏「上传期间消息还没发出去」的中间态。
2. 字节在库外：**库级迁移不等于数据完整迁移**——只搬库不搬数据目录（或反之）会得到悬空元数据。
3. 读数 9 在 main 上**不存在**：不要把 v9 当作 main 的一个历史状态。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show d386794^:app/alibot/db.py | grep -n '^SCHEMA_VERSION'
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show d386794:app/alibot/db.py | grep -n 'CREATE TABLE IF NOT EXISTS attachment\|CREATE INDEX IF NOT EXISTS idx_attachment_message\|attachment_no_rebind'
sh /tmp/canonical_probe.sh d386794     # 期望：SCHEMA_VERSION = 9；table(14) 含 attachment；trigger(8)
```

---

### 2.7 `9 → 10`：**未发生**；以及 main 上 `7 → 10` 的**实际**跃迁（主题三档，#71）

**事实**：读数 10 由分支提交 `cd23ac5`（issue **#71**）设置，其 **parent 的读数也是 7**。
所以 **`9 → 10` 这条边不存在**。

但 **main 上确实发生过一次 `7 → 10` 的读数跳跃**，落点是 `7f1cb99`
（`Merge pull request #79 from whutlichao/issue-71-theme-dark`）。这次跃迁的**净结构差异**是：

**库级差异**（`1b6b879 → 7f1cb99`，`1b6b879` 是跳跃前的 main 树，读数 7）：

- **既有表加列**：`workspace_settings.theme TEXT NOT NULL DEFAULT 'system'`。
- **CHECK 变化**：`workspace_settings` 的 CHECK 词表由
  `(id = 1)`, `(locale IN ...)`, `(message_density IN ...)`
  变为再加入 `(theme IN ('light','dark','system'))`。
- 表 / 索引 / 触发器：无变化。

**是否需搬迁数据：否（有默认值）。** 新增列带 `NOT NULL DEFAULT 'system'`，
既有行由 SQLite 在加列时回填 `'system'`；语义上「跟随系统」是缺省档
（`db.py:62-73` 的 `THEMES` / `DEFAULT_THEME`），回填值即正确缺省。

**迁移要点与风险**：

1. 这是「**新增带默认值的 NOT NULL 列 + CHECK 词表扩展**」型改动。回填值的选择有语义：
   `'system'` 是缺省档，不是 `'light'`。
2. `theme` 的 CHECK 是**列级**的（写在 `theme TEXT NOT NULL DEFAULT 'system' CHECK (...)` 这一行里，
   与 `locale` / `message_density` 同款写法），不是表级 `CHECK(...)` 子句。补列时若只写
   `ADD COLUMN theme TEXT NOT NULL DEFAULT 'system'` 而漏掉 `CHECK`，库级约束会与 `SCHEMA` 文本不一致。
3. 读数 `10` 在 main 上先后对应**两棵不同的树**（`7f1cb99` 无 reminder，`e1c523d` 有 reminder）。
   迁移实现必须用 commit 定位。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show cd23ac5^:app/alibot/db.py | grep -n '^SCHEMA_VERSION'   # 期望 7
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 7f1cb99^:app/alibot/db.py | grep -n '^SCHEMA_VERSION'   # 期望 7
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 7f1cb99:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS workspace_settings/,/^);/p'
sh /tmp/canonical_probe.sh 1b6b879     # 期望：SCHEMA_VERSION = 7
sh /tmp/canonical_probe.sh 7f1cb99     # 期望：SCHEMA_VERSION = 10；table(13)
```

---

### 2.8 `10 → 11`（main 实际）：附件端到端（#67），触发器被改写

**引入**：分支提交 `f0d8426`
（`fix(schema): SCHEMA_VERSION 9 → 11（补收尾：号段以「高于当前 main」为准，不是预留号）（#67）`）；
merge `b619336`（`Merge pull request #83 from whutlichao/issue-67-attachments`）。
issue **#67**，PR **#83**；ADR `docs/adr/0014-attachments-are-separate-objects-in-the-data-directory.md`。

**main 净差异**（`477220d → b619336`，`477220d` 是 #83 合并前的 main 树，读数 10）：

- **新增表** `attachment`（定义同 §2.6）。
- **新增索引** `idx_attachment_message`。
- **新增触发器 3 条**：`attachment_no_delete`、
  `attachment_metadata_immutable`、`attachment_binding_is_immutable`。

**分支内部 9 → 11 的差异**（`d386794 → f0d8426`，用于理解「同一票内触发器怎么变的」）：

- `attachment_no_rebind` **被删除**，替换为两条更细的触发器：
  - `attachment_metadata_immutable`：`id / original_name / size_bytes / mime_type / relative_path / created_at`
    任一被改即 `RAISE(ABORT, 'attachment is immutable')`；
  - `attachment_binding_is_immutable`：`message_id` 从非空改成另一个值即
    `RAISE(ABORT, 'attachment binding is immutable')`。
- 该替换的实际引入提交是 `138cefb`（`fix(attachments): 双轴 review 处置 —— … 元数据不可变 …`），
  可用 `git log --all --oneline -S'attachment_metadata_immutable' -- app/alibot/db.py` 复核。
- 该 diff 同时带入 `reminder` 表与 `workspace_settings.theme`——因为 `f0d8426` 的树是基于
  main 的（已含这两项），这再次说明「读数差 ≠ 单票结构差」。

**是否需搬迁数据：否（纯加法 + 触发器替换）。** 但触发器替换对**已升级过的库**是一个真风险：
DDL 用的是 `CREATE TRIGGER IF NOT EXISTS`，一个曾按 v9 建过 `attachment_no_rebind` 的库，
重新执行 `SCHEMA` **不会**删掉旧触发器，也不会改写它——会出现新旧触发器并存。
迁移实现若要改触发器语义，必须显式 `DROP TRIGGER` 再 `CREATE`。

**迁移要点与风险**：

1. **触发器是「先删后建」而不是「IF NOT EXISTS 就够」**：本仓所有触发器都是
   `CREATE TRIGGER IF NOT EXISTS`，这使「建」幂等，但**不使「替换」幂等**。
2. **不可变语义被重写**，方向要分清：
   - 旧 `attachment_no_rebind` 只管**已绑定**行（`OLD.message_id IS NOT NULL`）——这类行的
     **任何** UPDATE 都被拒；
   - 新 `attachment_metadata_immutable` 管**所有**行（含未绑定行）的元数据列集——对未绑定行的
     元数据是**新增**约束；
   - 新 `attachment_binding_is_immutable` 只管 `message_id` 改绑（`OLD IS NOT NEW`）。
   - 两者都不改写存量数据，只约束未来写入。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show --stat --format='%H %s' f0d8426
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git log --all --oneline -S'attachment_metadata_immutable' -- app/alibot/db.py
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 477220d:app/alibot/db.py | grep -n '^SCHEMA_VERSION'   # 期望 10
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show b619336:app/alibot/db.py | grep -n 'CREATE TRIGGER'
sh /tmp/canonical_probe.sh b619336     # 期望：SCHEMA_VERSION = 11；trigger(9)
```

---

### 2.9 `11 → 12`（main 实际）：编排认领与每任务一次（#65）

**引入**：分支提交 `a700d05b5`（`feat(claim): auto_claim 开关 + 编排认领 + 每任务一次的开世代（#65）`）；
merge `36e452e`（`Merge pull request #84 from whutlichao/issue-65-auto-claim`，即当前 `main`）。
issue **#65**，PR **#84**；ADR `docs/adr/0010-claiming-a-task-opens-one-epoch-per-task.md`。
（分支提交自身相对的是读数 10 的树——`a700d05^` 读数为 10；main 上是 11 → 12。）

**main 净差异**（`b619336 → 36e452e`）：

- **既有表加列 + CHECK 扩展**：`agent_config.auto_claim INTEGER NOT NULL DEFAULT 0 CHECK (auto_claim IN (0, 1))`；
  `agent_config` 的原 CHECK `(enabled IN (0, 1))` 之外新增 `(auto_claim IN (0, 1))`。
- **既有表加列**：`task.claimed_at TEXT`（**可空、无默认值**）。
- 表 / 索引 / 触发器：无变化。

**是否需搬迁数据：否（有默认值 / 可空）。** 存量 `agent_config` 行回填 `auto_claim = 0`
（= **关闭**自动认领，行为安全缺省）；存量 `task` 行 `claimed_at = NULL`
（= 「从未被认领过」）。

**迁移要点与风险**：

1. `claimed_at IS NULL` 是「**每张任务至多一次**」这条构造性保证的落点
   （`app/alibot/db.py` 的 `task` DDL 注释、ADR-0010）。DDL 层 `claimed_at TEXT` 无默认值，
   加列后存量行取 `NULL`（=「从未被认领过」），因此**存量任务升级后仍可被认领一次**并开一次世代；
   若改成回填 `updated_at` / `created_at` 之类的既有时间，存量任务会被当作**已认领**、
   认领不再触发世代推进——两种口径的编排后果不同，**需要迁移设计明确选择**，
   本清单只记录 DDL 层结果（`NULL`）。
2. `auto_claim` 默认 `0` 是**行为缺省**：存量 agent 升级后不会突然开始自动认领。
   若迁移实现选了默认 `1`，会改变编排行为——这是一处必须显式确认的口径。
3. `auto_claim` 的 CHECK 是**列级**的（`auto_claim INTEGER NOT NULL DEFAULT 0 CHECK (auto_claim IN (0,1))`），
   与 `enabled` 同款写法。补列时漏掉 `CHECK` 会让库级约束与 `SCHEMA` 文本不一致（同 §2.7 的 `theme`）。

**证据**：

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show --stat --format='%H %s' a700d05
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 36e452e:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS agent_config/,/^);/p'
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_VALUE_1 git show 36e452e:app/alibot/db.py | sed -n '/CREATE TABLE IF NOT EXISTS task/,/^);/p'
sh /tmp/canonical_probe.sh b619336     # 期望：SCHEMA_VERSION = 11
sh /tmp/canonical_probe.sh 36e452e     # 期望：SCHEMA_VERSION = 12
```

---

### 2.10 跨版不变量与「负结论」（同样经过逐列 diff 核实）

在 `3 → 12` 全区间内，逐列 diff **没有**出现以下变化（即这些是负结论，不是省略）：

- **没有任何既存列被删除**。区间内唯一的「消失列」是 §2.1 三张被改名表里的旧列名
  （`channel_id`），随旧表一起消失。
- **没有任何既存列的 `NOT NULL` / 默认值 / 类型被修改**（逐版 diff 无 `~col` 行）。
- **没有「新增无默认值的 `NOT NULL` 列」加到既有表**。加到既有表的新列共 5 个：
  `message.task_ref`（可空）、`target.dm_peer_member_id`（可空）、
  `workspace_settings.theme`（`NOT NULL DEFAULT 'system'`）、
  `agent_config.auto_claim`（`NOT NULL DEFAULT 0`）、`task.claimed_at`（可空）。
- **表级 `UNIQUE` 只在 v5 新增过一次**：`task` 的 `UNIQUE(target_id, id)`（新表自带）；
  **唯一一次对既有表的唯一性收紧**是 v6 的 partial unique index `idx_target_dm_peer_unique`。
- **`message` 的 `UNIQUE(target_id, seq)`** 从 v1 起存在（v1/v2 时包目录还叫 `worksplice`），
  全区间未变。复核：`git show 5015383:app/worksplice/db.py | grep -n 'UNIQUE'`。
  注意 v3 读数**跨越了 #20 的改名**（PR #21，merge `f6b8039`）：改名前 `app/worksplice/db.py`、
  改名后 `app/alibot/db.py`（§0.3-B 的两个候选路径就是为这件事准备的）。
- **`schema_meta` / `member` / `channel` / `remote_consent` 四张表**全区间无结构变化。
- **所有 DDL 都是 `IF NOT EXISTS`**（`CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS`）：
  这使「重跑 `SCHEMA`」对既有库是 no-op，**也意味着它不能用来实现迁移**——
  唯一的防线是 §0.1 的版本闸门。

---

## 3. 当前 `main` 的结构快照

- **`SCHEMA_VERSION`**：`12`（`app/alibot/db.py:57`）
- **commit sha**：`36e452eab43414ba857156cc9cab9ebf22f3b0e5`
  （`Merge pull request #84 from whutlichao/issue-65-auto-claim`）
- **取证时间**：2026-10-16 22:45 CST（UTC 2026-10-16T14:45:02Z）
- **复现命令**：`sh /tmp/canonical_probe.sh 36e452e`（§0.3-B 是自包含版本）

### 3.1 表（15 张，含 `schema_meta`）

| 表 | 列（`*` = 主键成员） |
|---|---|
| `schema_meta` | `key*`, `value` |
| `member` | `id*`, `kind`, `display_name`, `avatar_slot`, `created_at`, `deleted_at` |
| `agent_config` | `member_id*`, `role`, `model_id`, `system_prompt`, `enabled`, `auto_claim`, `created_at`, `updated_at` |
| `channel` | `id*`, `name`, `description`, `created_at` |
| `target` | `id*`, `kind`, `dm_peer_member_id`, `created_at` |
| `target_member` | `target_id*`, `member_id*` |
| `target_read` | `member_id*`, `target_id*`, `last_read_seq` |
| `target_budget` | `target_id*`, `epoch_seq`, `used` |
| `task` | `id*`, `target_id`, `channel_id`, `title`, `status`, `assignee_member_id`, `claimed_at`, `created_at`, `updated_at` |
| `message` | `id*`, `target_id`, `seq`, `author_member_id`, `kind`, `body`, `created_at`, `mention_snapshot`, `task_ref` |
| `attachment` | `id*`, `original_name`, `size_bytes`, `mime_type`, `relative_path`, `created_at`, `message_id` |
| `reminder` | `id*`, `text`, `time`, `channel_id`, `created_at` |
| `workspace_settings` | `id*`, `locale`, `message_density`, `theme`, `updated_at` |
| `remote_consent` | `provider*`, `acknowledged_at` |
| `round_outcome` | `id*`, `member_id`, `target_id`, `base_seq`, `status`, `reason`, `started_at`, `finished_at`, `consumed_budget`, `merged`, `reply_seq` |

### 3.2 索引（5 个）

| 索引 | 表 | 列 | 唯一 / partial |
|---|---|---|---|
| `idx_target_dm_peer_unique` | `target` | `dm_peer_member_id` | **UNIQUE + partial**：`WHERE kind='dm' AND dm_peer_member_id IS NOT NULL` |
| `idx_target_member_member` | `target_member` | `member_id` | 非唯一 |
| `idx_round_outcome_target` | `round_outcome` | `target_id, finished_at` | 非唯一 |
| `idx_round_outcome_member` | `round_outcome` | `member_id, finished_at` | 非唯一 |
| `idx_attachment_message` | `attachment` | `message_id` | 非唯一 |

> 另有若干由 `PRIMARY KEY` / `UNIQUE` 约束隐式创建的索引（如 `message` 的
> `UNIQUE(target_id, seq)`、`task` 的列级 `target_id UNIQUE` 与表级 `UNIQUE(target_id, id)`），
> 它们不在 `sqlite_master` 的命名索引清单里，但同样是库级结构的一部分。

### 3.3 触发器（9 个）

| 触发器 | 表 | 时机 / 作用 |
|---|---|---|
| `message_no_update` | `message` | append-only：禁止 UPDATE |
| `message_no_delete` | `message` | append-only：禁止 DELETE |
| `round_outcome_no_update` | `round_outcome` | 禁止 UPDATE |
| `round_outcome_no_delete` | `round_outcome` | 禁止 DELETE |
| `task_target_must_be_task` | `task` | `BEFORE INSERT`：`target_id` 必须指向 `kind='task'` 的 target |
| `task_channel_must_be_channel` | `task` | `BEFORE INSERT`：`channel_id` 必须指向 `kind='channel'` 的 target |
| `attachment_metadata_immutable` | `attachment` | `BEFORE UPDATE`：元数据列集任一被改即拒绝 |
| `attachment_binding_is_immutable` | `attachment` | `BEFORE UPDATE`：已绑定的 `message_id` 不许改绑 |
| `attachment_no_delete` | `attachment` | append-only：禁止 DELETE |

---

## 4. 迁移实现可能要看的「现状约束」（不是设计建议）

以下是本清单取证过程中读到的、**已经写在仓库里**的约束，列在这里是为了让迁移实现知道边界，
不代表本文件给出方案：

- `app/alibot/db.py:830` `_assert_schema_compatible()`：版本不等即**拒绝启动**，且**不做任何 schema 变更**。
  文档串原文：「**不修版本号、不跳过、不猜测**……本版不做迁移（规格决策 8 / ADR-0004）」。
- `app/alibot/repair.py`：**唯一**的「把旧表数据搬进新表」入口，四条 `INSERT OR IGNORE`，
  顺序为 `target → target_member → target_read → target_budget`（`repair.py:81-92`），
  且**不是自动迁移**、默认不执行。
- `app/alibot/db.py:112` `BUSINESS_TABLES`：把**新旧两代表名**都列进「什么算有数据」的判据，
  迁移实现若改表名，需要同步这里（否则「有数据的旧库」会被误判成空库）。
- 号段正本在 `db.py:44-56`；任何新改动按注释要求「**必须高于当前 main**」，
  而不是「等于被分配的预留号」。

---

## 5. 未核实与缺口

| 项 | 状态 | 卡在哪 |
|---|---|---|
| `v8` / `v9` 是否曾被任何**非 main** 分支部署过（是否产生过真实 v8/v9 库） | **未核实** | 判据应是「运行时数据目录里存在 `schema_meta.schema_version ∈ {8,9}` 的库」，本票只读源码与 git 历史，**没有**也**不应**触碰本机数据目录；无法从仓库内证实。 |
| #25 / #26 / #27 / #61 / #65 / #67 / #68 / #71 是否有独立「规格票正本」文件 | **未核实为「无」** | `app/specs/` 下**不存在** `issue-25-*` / `issue-26-*` / `issue-27-*` / `issue-61-*` / `issue-65-*` / `issue-67-*` / `issue-68-*` / `issue-71-*` 文件（`ls app/specs/`）。这些票的规格可能只存在于 GitHub issue 或 `CONTEXT.md`，本票不访问 GitHub。可确认的是**仓库内没有**这些编号的规格文件。 |
| `#58`（结构化沉默 `silent`）是否最终实施 | **已核实为「未实施」** | `round_outcome.status` 的 CHECK 词表至今仍是 5 个词（无 `silent`）：`ROUND_STATUSES`（`db.py:100`）与 §3.1 的 DDL；`agent_seam.py:33` 的 `SEAM_STATUSES` 仍是 3 个词。ADR-0008 与 `issue-35-spec.md` 记录的「v6→v7」计划与 main 实际跃迁不一致（见 §2.4 证据）。 |
| 各版之间**数据行数**层面的迁移量 | **未核实** | 需要真实旧库样本；本票只做结构层沿革。（`app/specs/issue-37-acceptance.md` §1 给过一个 v3 样本库的行数读数，可作参考，但不是全量。） |
| SQLite 对「向既有表补带 CHECK 的新列」的确切机制（是否必须重建表） | **未核实（超出本票范围）** | 这是迁移设计与实现的机制问题，本票不裁定。可复核的结构事实：当前 `SCHEMA` 里**所有** `CHECK` 都写在列定义内（列级），没有表级 `CHECK(...)` 约束子句；其中 `message_density` / `theme` 两条因折行书写而看起来像独立行，实为列级（复核：`sed -n '177,450p' app/alibot/db.py \| grep -nE '^[[:space:]]*CHECK'` 只命中这两条折行续行）。 |

---

## 6. 本文件的证据边界

- 全部结构结论由 §0.2 的方法得出，命令见 §0.3；每个版本节的「证据」块给出该版的 sha 与命令。
- 全部「引入 commit / PR / issue」结论来自：merge commit 的
  `Merge pull request #NN from whutlichao/<branch>` 主题行、分支提交的 `(#NN)` 尾注、
  `app/specs/*` 与 `docs/adr/*` 内的票号引用。
- 标为「未核实」的项**没有**用推测补齐；需要时请按 §0.3 的命令重新取证。
