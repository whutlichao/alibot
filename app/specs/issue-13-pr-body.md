## Summary

治理票，**只对齐领域文档，不改应用行为**（全部改动都是 `.md`）。

`CONTEXT.md` 的 `## Language` 被 `docs/agents/domain.md` 指定为本仓的**领域词汇正本**，但整篇描述的是
规格 #1 §11 **明确舍弃**的旧技术方案；本仓真实在用的承重词（人类世代、回应预算/跳/预算耗尽、
`agent_config`/`channel_budget`、未读口径、轮次结果 status 词表）一条都没进词汇表。
同时 `docs/adr/` 不存在，而 `domain.md` 把它列为「exploring 前必读」。

```diff
 CONTEXT.md                                   # 词汇正本：53 条 → 36 条
+├── 协作域        消息 / seq / target / 系统事件消息 / 提及 / 穿透 …
+├── 唤醒与回应    唤醒 / 回应判断 / 轮次 / 轮次结果（五词 status 表）/ 状态点 …
+├── 有界响应      人类世代 / 回应预算 / 跳 / 预算耗尽 / 预算表(channel_budget)  ← 全新增
+├── 身份与边界    结构身份 / 人类面·成员面 / 远端数据流告知 / 工作台
+├── 未读与订阅    已读位置 / 消费游标 / 未读（kind ∈ {human,agent} 且作者非本人）/ 订阅
+└── 前端与视觉    成员面板 / 补全 / 视觉契约 / 头像色位 / 档位 / 角色族

 docs/
-└── agents/
+├── adr/                                     ← 新建（裁定 A）
+│   ├── 0001-messages-are-append-only.md
+│   ├── 0002-response-budget-caps-relay-at-three.md
+│   └── 0003-agents-have-structural-identity-only.md
+└── agents/

 app/specs/
+└── issue-13-acceptance.md                   # 处置表 + 自审 + 一致性核对

# 未动：规格 #1 正文（只读）、# Worksplice 标题、## Language 章节名、
#       worksplice-design-system/（只读参照包）、任何 .py/.js/.css
```

词条处置（逐条依据见 `app/specs/issue-13-acceptance.md` §2）：

```text
原 53 条 = 移除 33 + 改写 18 + 保留 2

移除 33 ─ §11 舍弃清单点名 27：家目录/工作区/允许根/路径守卫/沙箱、会话文件/固化/无主文件/
          backfill 门禁/跨作者去重、回复动作/观察相、成本看板/模型探测/错误恢复、
          任务/任务视图/任务创建事件/指针消息、DM/懒创建/接续、Cwd 互斥、
          SDK 委托边界/薄 Wrapper、静音、唯一导入面
          §Out of Scope 未保留 6：准入闸、调用者身份、房间、线程回复总数、任务线程未读、深模块

改写 18 ─ 概念在、表述属旧方案（夹带 pi session / 任务 / cap-ack / round_logs / mute /
          globals.css 等本仓不存在的机制），按 main 现行代码逐条校正

保留  2 ─ 轮次、穿透（内容未动）

新增 17 ─ 人类世代 / 回应预算 / 跳 / 预算耗尽 / 预算表 / 未读 / 消费游标 / 已读位置 /
          成员面适配接缝 / Agent 配置 / 系统事件消息 / 消息 / seq / target /
          远端数据流告知 / 工作台 / 头像色位
```

**ADR 处置（裁定 A，票面 (b) 的收窄版）**：核实到 `docs/adr/` **缺失且从未存在**
（`git log --all --diff-filter=A -- '*adr*'` 为空），且 **`app/worksplice/` 源码 0 处 ADR 引用**
——票面原文「代码注释引用 ADR-0013/0016」与树里事实不符，已 `ask` 上报并由 coordinator 更正 issue 正文。
依据不是偏好而是仓库自己的规则：`domain.md` 第 12 行「If any of these files don't exist,
**proceed silently** … creates them lazily when terms or decisions actually get resolved」——
本票正是「决策已定型」的触发时刻。故新建 `docs/adr/`，**只收本仓真实做过的三条决策**；
`worksplice-design-system/` 的 `ADR-0014/0015/0016` 是**该只读参照包自己的编号**，本仓不解析、不复制
（`CONTEXT.md` 的视觉契约/档位词条已改指该包正本并注明）。旧词条的 `ADR-0013` 随词条一并消失，
不为它建 ADR。因此**本票无代码改动**，ruff/format/mypy/pyright **不适用**（不是省略）。

## Evidence

**Before**（`CONTEXT.md`@`2b8615c`）——词汇正本描述的是本仓不存在的机制：

```console
$ git grep -n "pi session\|round_logs\|#all\|globals.css" HEAD -- CONTEXT.md | head
CONTEXT.md:9:  _Avoid_: 会话、session（那是 pi session，临时运行状态）
CONTEXT.md:56: …成员面向 app 请求系统动作的唯一通道（ADR-0013）：… 加一条 `ops` 项 …
CONTEXT.md:116: …落盘 round_logs 供可观测页展示…
CONTEXT.md:120: 已加入某频道的成员。`#all` 自动加入且不可离开；thread 回复继承频道规则。
CONTEXT.md:164: …深模块 `lib/cwd-mutex.ts` 唯一事实来源…（本仓无此文件）
CONTEXT.md:200: …产品经 `app/globals.css` 的 `@import` 直接消费它（ADR-0014）

$ git grep -l "人类世代\|回应预算\|channel_budget\|agent_config" HEAD -- CONTEXT.md
（0 命中）   # 承重术语一条都没进词汇表

$ ls docs/ && ls docs/adr
agents/
ls: docs/adr: No such file or directory
```

**After**：

```console
$ python3 -c "…提取两份 CONTEXT.md 的词条名…"
old 53 new 36
carried over: 19      # 改名/沿用
brand new:    17
dropped:      34      # 33 移除 + 头像色→头像色位（改名）

$ python3 -c "…核对 _Avoid_…"
entries: 36
missing _Avoid_: []          # 36/36 各带一条
orphan _Avoid_:  []          # 0 孤儿（删词条时其 _Avoid_ 一并处置）

$ python3 -c "…核对处置表覆盖…"
ids: 53 unique: 53 min 1 max 53
missing: []  dupes: []       # 原表 53 条各占一行、无重复

$ ls CONTEXT.md docs/adr/                    # domain.md 的引用路径真实可读
CONTEXT.md
docs/adr/0001-messages-are-append-only.md
docs/adr/0002-response-budget-caps-relay-at-three.md
docs/adr/0003-agents-have-structural-identity-only.md
$ sed -n '/## Language/,+1p' CONTEXT.md
## Language                                  # 章节名未动（domain.md 靠它定位）

$ git diff --cached --name-only | grep -vE '\.md$' || echo "(only .md)"
(only .md)                                   # 无源码改动 → 不改应用行为

$ git status --short
（空）                                        # worktree 干净
```

一致性抽查（`app/specs/issue-13-acceptance.md` §6.2）：issue #2/#3/#4/#8/#9/#12 标题、
测试名（`test_..._bounded_by_three_jumps_per_human_epoch` 等）、代码 docstring 的术语
与新词表**逐项一致，无漂移**；记录 2 处 UI 文案漂移（`index.html` 的「工作区」）留待后续修，
不在本票范围（票面明写「不要求本票全量修代码」）。

## Merge Danger

**Door:** two-way —— 纯文档改动，`git revert` 即可完全回退，无迁移、无数据影响。

**Blast Radius:** narrow（仅 `CONTEXT.md` + 新增 `docs/adr/` 与一份取证文档）。

合并后的影响面有三点需要知情：

1. **词汇表是行为约束面**：`domain.md` 要求 issue 标题 / 测试名 / 提案使用词汇表里的术语，
   并避免 `_Avoid_` 点名的词。合并后 33 条旧术语从正本消失，下游写作应改用新词
   （如「通知」→ 唤醒、「房间」→ 频道 / target、「工作区」→ 工作台）。
2. **`docs/adr/` 由缺失变为存在**：`domain.md` 与 `AGENTS.md` 里那两处声明从**死引用变成有效引用**
   ——这是本票的预期效果，不是副作用。后续 ADR 从 `0004` 起编号。
3. **本票不删除任何代码**：只删词汇表词条。`app/specs/issue-2-acceptance.md` 里 1 处 ADR-0016 引用
   按 coordinator 裁定**不动**（历史取证，改了就是篡改历史），新词表已注明「该包自身编号，本仓不解析」。

**与 #12 的关系**：`#12` 并行改 `orchestration.py`/`db.py`（预算记账粒度）。本票**未触碰任何源码**，
故无冲突；新词条的「预算表」定义已写明「跳数的正本是 `channel_budget.used`，不是轮次结果行数」，
与 #12 的裁定同向。

## 验收对照（issue #13 四项）

| 验收项 | 落点 |
| --- | --- |
| ① 词条处置表（逐条 + 依据） | `app/specs/issue-13-acceptance.md` §2（53 条各一行：27+6 移除、18 改写、2 保留）|
| ② 改写后的 `## Language` | `CONTEXT.md`（36 条、6 子标题、`_Avoid_` 36/36、标题与章节名未动）|
| ③ ADR 处置结论与理由 | 同上 §4（裁定 A + `domain.md` 懒创建依据 + 未改注释的理由）|
| ④ 一致性核对结果 | 同上 §6（domain.md 引用路径无死链 + 抽查 6 个 issue 标题 / 测试名 / docstring）|
| 文档自审记录 | 同上 §7（结构 / 依据 / 死引用 / `_Avoid_` 四项逐条）|
