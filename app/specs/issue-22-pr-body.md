## Summary

本票交付**第二版能力的规格**（零源码改动）：一条规格正本 issue + 词汇表改写 + 三条 ADR + 五张实施票。
一句话的改动形状——`target` 从「恒为一个频道」升为一等对象，三块新能力长在它上面：

```diff
 消息作用域
-  message.target_id == channel.id（唯一一种"频道"作用域）
+ target(id, kind ∈ {channel, dm, task})            # channel 型复用 channel.id（影子行）
+   ├── message.target_id        （+ task_ref 可空引用列）
+   ├── target_member            # channel_member 改名（成员面：谁能被唤醒 / 未读算谁的）
+   ├── target_read              # channel_read 改名（每成员每 target 已读位置）
+   └── target_budget            # channel_budget 改名（每 target 一份，used 仍是跳数唯一正本）
```

三条新能力各自的纵切：

```text
任务看板   task 行（五态，可变）+ kind='task' 的 target（线程消息落这里）
             ├── 改状态 / 改标题 → 永不唤醒，线程留一条 system 消息
             └── 改指派人        → owner：人类世代 + 预算重置 3 + 唤醒；agent：一次 agent→agent 跳
DM 私信    kind='dm' 的 target（显式创建 + 一 agent 一 DM 幂等）+ 两行 target_member
             └── owner 发言唤醒对方；agent 回复零唤醒 ⇒ 天然无循环
工作区搜索  三种 kind 全搜 + 可限定 target；后端返回高亮区间，前端只包标签
```

仓库文件只动了三处（全部是文档）：

```text
CONTEXT.md                                          # target 词条改写 + 9 个新承重词 + 5 处措辞泛化
docs/adr/0004-target-becomes-a-first-class-object.md
docs/adr/0005-dispatching-a-task-is-a-human-utterance.md
docs/adr/0006-task-state-lives-on-the-row-history-in-the-stream.md
app/specs/issue-22-design-notes.md                  # 本票过程文档：23 问的 Q→A 原文 + 票清单
app/specs/issue-22-pr-body.md                       # 本文件
```

`app/alibot/` 下**未动任何 `.py` / `.js` / `.css` / `.html`**；`alibot-design-system/` 未动。

## Evidence

- **规格正本（新 issue）**：https://github.com/whutlichao/alibot/issues/23 —— 标 `ready-for-agent`，body 20,411 字符，
  `## ` 级小节 **8 个**（用户已确认的边界 + #1 体例的七节），与 #1 体例一致。
  **Before:** 第二版没有规格，只有 #22 的票面（含 A/B/C/D 四组"必须定清"问题）。
  **After:** 每条承重决定都查得到依据——票面已定 9 条 / grill 人定 16 条 / 代答 8 条，逐条标注裁定来源。
- **grill 真的跑了**：23 问分五轮经 `orca orchestration ask` 提出（A 组 6 问 → B 组 6 问 → C+D 组 8 问 →
  拆票 3 问 → 补收尾 2 问；前四轮为 grill 与拆票，第五轮见 design-notes §5），每轮 Q → A 原文与理由逐字记进 `app/specs/issue-22-design-notes.md`。
  **没有**用 AskUserQuestion；**没有**对已定 9 条决策重复提问。
- **词汇表改动前后对照**（同一文件，`git diff` 可核）。
  **Before:** `target` 写着「首版 target 恒为一个频道——它是"频道内单调"里的"频道"」；
  **After:** 「消息、已读位置、回应预算与轮次结果的**共同作用域键**……**channel 型 target 复用 `channel.id`**：
  同一个字符串既是频道主键也是 target 主键，target 表对 channel 型是一张**影子行**」。
  措辞泛化五条（`已读位置` / `人类世代` / `回应预算` / `预算表` / `频道成员`）在 diff 里表现为"频道 → target"的
  逐词替换，语义未变；**穿透**词条新增 DM 上一次性送达的一句。
- **ADR 编号与格式**：`docs/adr/` 既有 0001–0003，新增 **0004 / 0005 / 0006**，格式与既有三条一致
  （段落式，含取舍与否掉的方案与代价）；按 `docs/agents/domain.md` 的懒创建规则只收真实决策。
  **既有 ADR 冲突处理**：0001 **未被推翻**（消息仍 append-only，0006 明写"task 行的 UPDATE 不是消息的 UPDATE"）；
  0002 被**泛化**（每频道 → 每 target，≤3 与 `used` 正本不变）；0003 不受影响。
- **票据拆分（5 张，原生关系）**：`#24 blocked_by=0` / `#25 blocked_by=1` / `#26 blocked_by=1` /
  `#27 blocked_by=1` / `#28 blocked_by=4`，全部 `gh api repos/whutlichao/alibot/issues/<n>` 可核；
  `#23` 的 `sub_issues_summary.total = 5`。每票带 **blocked-by + 可独立验收的依据**两段，
  ② / ③ 的验收依据写成"能断言 / 能读出什么数字"（②：卡片徽标 +1 而频道徽标不变；③：`used` 读数 0 / 归零 / +1）。
- **术语一致性自查（`grep` 读数）**：新写的规格 / ADR / 词汇表里**无** `_Avoid_` 词用作自有术语
  （唯一命中在 Out of Scope 的"不做清单"里，且已改写以避开 `_Avoid_` 词）；**无**自造简称
  （`频道` / `成员` / `唤醒` / `轮次` / `跳` / `人类世代` / `target` 用法与既有词条一致）。
- **本票不跑测试**（门禁 G-docs，零源码改动）：规格的 Testing Decisions 节给出下游实施票的验收面，
  含 11 组新增行为测试（target 泛化 / 每 target 一份预算 / 未读分级 / 五态与互审 / 任务写入后果 /
  线程未读分离 / 指针消息 / DM / 搜索 / 隔离 / 恢复）。

- **双轴文档 review 已跑并逐条处置**（门禁 G-docs：本票审**文档一致性**，不审源码风格）：
  **Standards 轴 OK with notes**（P1 四处作用域措辞 `频道`→`target`、P1 `epoch_seq` 来源、P2 数据面含 DM/任务、
  P2 自查读数）与 **Spec 轴 OK with notes**（P1 补改动前后对照表、P2 题数更正、P2「任务创建」来源）——
  **全部 findings 已在 `9831813` 处置**，逐条结论与实测读数见 `app/specs/issue-22-design-notes.md` §6。
- **补收尾两轮裁定已落地**（第 5 轮，均由用户人定）：① `epoch_seq` 改记「**开世代动作**所在消息的 seq」
  （`human` 消息 **或** owner 在 task 型 target 上的改指派）；② **补全**作用域由「当前频道」泛化为
  「**当前 target**」（三种 kind 同规则，保留"不列 target 外成员"，**不**改成全工作区）。

## Merge Danger

**Door:** two-way（可回退）。

**Blast Radius:** 窄——全部改动落在文档与 issue；`app/alibot/` 与 `alibot-design-system/` 零改动，
没有任何运行时行为随之改变，回退只需 revert 这一个 commit（issue 与 ADR 编号不回退，历史即记录）。

需要合并者注意的两点：

1. **本 PR 合并的是"规格"，不是"实现"**。规格里的决定（`SCHEMA_VERSION` bump、不做旧数据迁移、三条新 ADR）
   一旦被合并，后续实施票就按它开工；若要改其中任一条，正确做法是**对本 PR 或用新的 ADR 修订**，
   而不是让实施票静默偏离。
2. **不做迁移是本版最明确的一刀**：合并后 `~/Alibot` 里的首版库**不能**被第二版直接打开（规格决策 8）。
   这是有意的，不是遗漏。
