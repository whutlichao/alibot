<!-- 本文件是 PR #110 正文的正本；`gh pr create --body-file` 用它提交。 -->

## Summary

把设计票 #107（PR #108）的 **S1–S19 十九条规格输入**合成为一份**可实施的规格**：每条落成规格正文里**可判定的行为契约**（C1–C21），赋予唯一编号、可观察验收判据与来源。

```text
app/specs/
├── reminder-trigger-spec.md            # 新增 976 行：规格正本
├── reminder-trigger-spec-answer.md     # 新增 150 行：Answer 留档
└── reminder-trigger-spec-pr-body.md    # 新增：本文件
```

规格的章节骨架：

```text
reminder-trigger-spec.md
├── 开工时实际基线 + 行号偏差登记      # 自行复核，未照抄票面/设计票
├── Problem Statement / Solution
├── 行为契约 C1–C21（八节，按主题分组）
│    一 产物与分叉   C1 C2(H1) C3
│    二 时间语义     C4 C7(H2)
│    三 已投递与错过 C5 C6 C21
│    四 调度与精度   C8 C9 C10(H4)
│    五 读路径       C20
│    六 迁移         C11 C12 C13(H3) C14
│    七 人类面       C15 C16 C17
│    八 文案与注释   C18(H5) C19(H6)
├── S1–S19 → 契约编号映射表            # 19 行，无落单
├── 硬点 H1–H6 对照表
├── Seams                              # 唯一 seam + 为什么不立第二个 + 被否落点
├── User stories                       # 60 条（A–H 八组）
├── Implementation Decisions D0–D10    # 决策归属 / 逐面落点 / 存量落点清单
├── Testing Decisions T1–T5            # 唯一测试 seam + 每类行为的落点 + prior art
├── Out of Scope                       # 15 条，逐条给理由（前 5 条是设计票显式被否方案）
├── 实施票切分建议                     # 4 票 + 每票覆盖的契约编号 + 依赖
└── 验收判据总表                       # A. 本票自身 / B. 逐条契约的机械判据
```

**唯一 seam** 的形状（`codebase-design` 词汇）：

```text
                 ┌──────────────────────────────────────────┐
   注入时刻 ───▶ │  到期投递器（Seam）                       │ ───▶ 投递后果
                 ├──────────────────────────────────────────┤
                 │  轮询节奏 · 事务边界 · fired_at 幂等写      │
                 │  时区换算（唯一一处）· 24h 窗口 · 文案分档   │
                 │  与 EventBus 的发布顺序                    │
                 └──────────────────────────────────────────┘
   接口宽度 = 1 输入 + 1 输出
   落点     = api.py:303 / 注入点 :321-328 / 关停顺序 :331-342（与既有 scheduler 同处）
```

## Evidence

**票面九条可观察验收逐条自查**（完整读数见 `reminder-trigger-spec-answer.md` §7）：

| # | 判据 | 读数 |
| --- | --- | --- |
| 1 | 规格含六个必备章节，每条契约有唯一编号与可观察判据 | C1–C21 各含「契约陈述 + 判据 + 来源」；Out of Scope 15 条带理由；User stories 60 条 |
| 2 | S1–S19 无落单 | `grep -c` 映射表行数 ⇒ **19** |
| 3 | H1–H6 逐条落实；H2 逐字出现 | H2 逐字句 `grep` 命中 **1** |
| 4 | Seams 声明核心 seam、落点、为什么够深 | 接口宽度 + 7 项隐藏复杂度 + 「几行测试替身覆盖 5 分支」的深度判据 |
| 5 | 零源码改动；`SCHEMA_VERSION` 未 bump | 见下 |
| 6 | `docs/adr/` 零改动 | 见下 |
| 7 | `CONTEXT.md` 只显示相关段落 | **零改动**（上游 #108 已改好三条词条，无缺口） |
| 8 | Answer 含侦察 / ask 归属 / 自决 / 实际基线 | `reminder-trigger-spec-answer.md` §2–§4 |
| 9 | `pr` skill 体例 + `--body-file` + PR 号回填 | 本文件即证据 |

**零改动取证（实测）**：

```text
$ git diff origin/main --stat -- app/alibot/ app/tests/
（空）

$ git diff origin/main --stat -- docs/adr/
（空 —— 未新增 ADR，未改既有 ADR）

$ git diff origin/main --stat -- CONTEXT.md
（空 —— 上游 #108 已把提醒 / 系统事件消息 / 到点改好，本票无词条缺口）

$ grep -m1 '^SCHEMA_VERSION' app/alibot/db.py
SCHEMA_VERSION = 12
```

**基线**：开工时 `git fetch origin` ⇒ `origin/main` = **`ec6310e`（未前进）**，与票面一致，**无需 rebase**。

**自行复核出的行号偏差**（未照抄票面/设计票，逐条登记在规格 §行号偏差登记）：

| 对象 | 设计票记的 | 实测 |
| --- | --- | --- |
| `studio/index.html` 过时注释 | `:656-659` | **`:651-659`**（整块 9 行；设计票只记了末两句，而整块里「只有 `type="time"`」那句也变不实了） |
| 同一条过时断言的**第二、三处** | **漏登记** | **`studio/i18n.js:394-397`（zh）/ `:858-861`（en）** |
| 未读判据本体 | `db.py:536` | **`db.py:496-502`**（`:536` 是消费点） |
| 搜索排 `system` | 未给行号 | **`db.py:599` / `:610`** |

**ask 归属**（三轮，全走 `orca orchestration ask`）：

```text
1 · Q1 Seams 数量        → 只一个 seam           人定
1 · Q2 切分票数          → 4 票                 人定
1 · Q3 H6 交付形态       → 给替换全文            代答
2 · Q4 fired_at 进契约   → 写进 C20              代答·推导（附推导链）
2 · Q5 文案分档判据      → 30 秒分档（C21）      代答·推导（与 C10 共用常量）
2 · Q6 报告读数落点      → 复用 changed_rows     代答·细节
3 · Q7 fired_at 口径     → UTC 审计戳            人定
3 · Q8 「已错过」推导侧  → 后端派生 state（C20）  代答·推导
```

## Merge Danger

**Door:** **two-way**

纯 Markdown 新增（3 个文件、1126 行），零源码、零测试、零 ADR、零 `CONTEXT.md`、零 `SCHEMA_VERSION` 改动。回退就是 revert 这一个提交，没有数据迁移、没有行为变更、没有契约生效。

**Blast Radius:** **isolated**

改动面只在 `app/specs/` 与 tracker（issue #109）。但它的**下游影响**值得说明：这份规格一旦合入，实施票将以它为准承担三件不可逆的事——

1. **一次时间语义的一次性收窄**（规格 C13）：存量 `HH:MM` 提醒在迁移后从「每天该钟点」变成「迁移执行当日该钟点」。它不丢行，但**改变了行的含义**。这是本规格要求「迁移报告必须能读到这句、且报告给出被改写行数（C16）」的原因。
2. **`SCHEMA_VERSION` 12 → 13**（规格 C11）：实施票会 bump 它并登记 `MigrationStep(12, 13)`。按 ADR-0015，被 12 及更早代码创建过的库在那之后会被闸门拒绝，处置路径是 `--migrate`。
3. **推翻 ADR-0012 的三句**（ADR-0017 首段已逐句声明）。规格不重新裁定这一点，只把「哪条边界变了、哪条没变」落成可判定的契约（C1–C3 继承「不唤醒 / 不耗预算 / 不计未读 / 不进搜索」，C2 守住「不新增 target」）。

此外两个**范围边界**在规格里显式登记，避免实施票二次裁定：`CONTEXT.md` 本票零改动（上游已改好，无缺口）；`studio/index.html` 那句注释的改正属实施票（规格 C19 给了逐字可抄的替换全文）。
