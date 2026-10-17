<!-- 本文件是 GitHub issue #115 的 Answer 评论留档；tracker 上的评论才是正本，以 tracker 为准。 -->

# Answer —— 实施票 ④ / #115（三处存量注释改口径）

> 正本：https://github.com/whutlichao/alibot/issues/115 ｜ 规格：`app/specs/reminder-trigger-spec.md` C19 ｜ 2026-10-18

## 1. 开工时实际基线（如实登记）

| 项 | 读数 |
| --- | --- |
| `git fetch origin` 后 `origin/main` | **`5f36251`**（`Merge pull request #122 from whutlichao/issue-114-reminder-ui`） |
| 是否前进 / 是否 rebase | **未前进**：开工时本 worktree HEAD 已是 `5f36251`，**无需 rebase**（`git merge-base --is-ancestor origin/main HEAD` ⇒ OK，见收尾读数） |
| 票面给的基线 | `main` = `5c4dc5c` —— ①②③ 已在票面发布后全部合入，实际基线是 `5f36251` |
| `SCHEMA_VERSION` | **13**（①②③ 的产物），本票**零后端改动** |

## 2. 三处落点的实测行号（票面/规格登记的**已漂移**，以实测为准）

| 落点 | 票面与规格 C19 登记的 | **实测（开工时 = 交付后同一行号）** | 漂移 |
| --- | --- | --- | --- |
| `app/alibot/studio/index.html` 提醒注释整块 | `:651-659` | **`:646-654`**（首行 `<!-- ─── 提醒（ADR-0012…`，末行 `…一定会出现在消息流里」。 -->`） | **−5** |
| `app/alibot/studio/i18n.js` zh 注释块 | `:394-397` | **`:392-395`** | **−2** |
| `app/alibot/studio/i18n.js` en 注释块 | `:858-861` | **`:857-860`** | **−1** |

漂移已由任务书第 8 条预先指出（`index.html` 记 `646-654`、`i18n.js` 记 `392-395` / `857-860`），与本次实测**逐行一致**。

## 3. 交付物（只改注释）

| 文件 | 动作 |
| --- | --- |
| `app/alibot/studio/index.html` | 注释整块 `:646-654` 改口径：行形态按 ③ 的**实际形态**重写；「新增控件只有 `type="time"`、没有日期」这句被 ③ 推翻的断言删掉，改写成「日期 + 钟点两个原生输入，对原型的显式偏离（ADR-0017 §六）」；到点口径按两支分支写对 |
| `app/alibot/studio/i18n.js` | zh 注释块 `:392-395`、en 注释块 `:857-860` 同口径改写（zh/en 措辞各自自然，语义一致） |
| **不动的** | `reminder.modalSub` / `reminder.emptyBody` 取值、任何控件、任何渲染、`app.js`、`app/alibot/*.py`、`docs/adr/**`、`CONTEXT.md`、`app/specs/reminder-trigger-spec.md`、`.scratch/**`、历史取证面 |

## 4. C19 契约 → 落点（逐条）

| C19 要求 | 落点 | 交付后读到的口径 |
| --- | --- | --- |
| 前半变真：有归属的提醒到点**确实**落一条 `system` 消息 | 三处 | 「**可以**说『到点后系统事件消息会出现在归属频道的消息流中』——这句现在是**真的**」 |
| 后半仍不成立：到点**不唤醒** | 三处 | 「**不能**说『到点会唤醒频道的 Agent』：到点**不唤醒**任何人、不消耗回应预算、不计未读」 |
| 不许笼统说「一定会出现在消息流里」 | 三处 | 「所以文案也不能笼统说『一定会出现在消息流里』」 |
| 判据 1：`index.html` 不再声称「到点不产生消息 / 到期触发不在授权范围」 | `index.html:646-654` | `grep '到期触发'` 在 `studio/**` **零命中** |
| 判据 2：zh/en 注释块同样不再有该断言 | `i18n.js:392-395` / `:857-860` | 同上零命中；`due-time wake-up`、`到点会发一条` 同样零命中 |
| 判据 3：副标题与空态正文无到点相关的不实承诺 | 取值未改 | `reminder.modalSub` / `reminder.emptyBody` 仍只说「保存在本机后端 / 后端，刷新或重启后仍在」——既未声称唤醒，也未声称一定落消息 |
| 判据 4：`git diff` 里除三处注释块外 `studio/**` 无其它改动 | 全 diff | 只有两个文件的注释块，无取值/渲染/控件改动 |

## 5. 三处偏离 / 需登记的判断（不照抄规格的逐字建议）

**(a) 行形态按 ③ 的实际交付写，不照抄 C19 那张五列表。** 规格 C19 的建议全文写「`.reminder-row`（到期时刻 / 文本 / 归属频道 / 状态后缀 / 删除）」，而 ③（#114，`5f36251`）已按用户 2026-10-17 的裁定（B：时刻只出现一次）把到期时刻并入状态后缀，行里**没有**独立的时刻列。交付文字因此写「（文本 · 归属频道 · 状态后缀 · 删除）」，并注明「状态后缀一列兼任它，为什么不再单立一列见 `app.js` 的 `reminderStateNode`」——正本留给代码，注释不另起一份。

**(b) 「新增控件只有 `type="time"`、没有日期」这句随整块一起处置。** 票面明确要求按整块处置而不是只改末两句，理由实测成立：该句在 `:649-650`，已被 ③ 在 `:672-682` 落地的日期控件推翻。

**(c) 补上「窗口外也不落消息」这支（规格 C19 只写了两支）。** 双轴 review 的 Standards 轴把它列为 P1，核对代码后**成立**：`ReminderDeliverer._fire` 的五分支表（`app/alibot/reminder_delivery.py`）里，「有归属 + 距到期 > 24 小时」这一支**只写 `fired_at`、不落消息**，人类面呈现为**已错过**（ADR-0017 §三、`CONTEXT.md` **到点**）。原逐字建议的「有归属的提醒到点确实落一条 `system` 消息」在这条分支上不真——在一张专门消灭不实断言的票里留下这句半真，等于重犯 C19 的病。因此三处注释改成「**不落消息**有两支：不归属 / 有归属但距到期超过 24 小时才被发现（不补投）」，其余口径不变。**这是同一种处置（照实际写），不是扩 scope**：仍只有注释、仍只有三处落点。

## 6. 双轴 code-review（Standards 轴 / Spec 轴独立评，不合并结论）

**Standards 轴**（读 AGENTS.md / CONTEXT.md / ADR-0012 / ADR-0017 / `docs/agents/*.md` / `app/README.md`，并核对 `app.js` 的 `reminderRowNode` / `reminderStateNode` 与 `index.html` 的输入控件）：

| 级别 | 发现 | 处置 |
| --- | --- | --- |
| **P1** | 三处注释把到点写成两支，实际有三支（窗口外有归属只记已到点、不落消息） | **已修**：见 §5(c) |
| P2 | `index.html` 把「行形态的正本」指向 `reminderStateNode`，而行（单元格组成）在 `reminderRowNode` | **已修**：改为「行里没有独立时刻列…为什么不再单立一列见 `reminderStateNode`」——只指向「为什么不单立」，不主张「行形态」 |
| P2 | en 注释块写 `CONTEXT.md "Due time"`，词条名不存在（正本是 **到点**），违反 `docs/agents/domain.md` 词汇纪律 | **已修**：改为 `CONTEXT.md` **到点** |
| P2（判断项） | 同一组事实现由 4 处重述（`index.html`、`i18n.js` zh/en、`app.js:52-56`），存在 Duplicated Code 味道 | **保留**：本票的写面就是这三处注释，删成一行引用会让「三处落点各自说对」这条判据无法逐处核验；重述的代价由「四处同错」暴露过（本次正是如此），处置方式是补上第三支而非删点 |
| P2（隐患） | `test_reminder_delivery.py:438` 断言 `"system.reminder." not in` `i18n.js` 全文（含注释） | **已核对**：本票注释里未出现该前缀（`grep` 零命中），测试常绿 |

**Spec 轴**（读 #115 正文 + 规格 C19 + ADR-0017 §一/§三/§六 + `CONTEXT.md`）：三处落点全部改口径、行形态按 ③ 实际交付、`type="time"`/日期那句已修、zh/en 两块口径一致、判据 1–4 全部满足；**无缺失、无 scope creep、无取值变更**。`verdict: OK with notes`。

- **Spec 轴的唯一 P2（明确不入本票）**：`app/alibot/studio/app.js:45-46` 的 #68 交付历史段仍写「列表（时间 / 文本 / 归属频道 / 删除）」「新增控件（文本 + 时间）」，是同类过时表述。但该段紧随其后已被 #114 声明为「原文保留作交付历史」（`app/alibot/studio/app.js:48-50` 一带），且 `app.js` 在 #115 的**不写面**内 ⇒ **不改**，登记为后续票据的清理项。

## 7. 门禁读数（全部在本 worktree 实跑）

```text
.venv/bin/python -m pytest                                  551 passed, 2 skipped   (311s)
.venv/bin/ruff check app                                    All checks passed!
.venv/bin/ruff format --check app                           121 files already formatted
.venv/bin/python -m mypy                                    Success: no issues found in 53 source files
node ~/.pi-lens/tools/node_modules/pyright/index.js …       0 errors, 0 warnings, 0 informations
node --test app/tests/i18n_ui.test.cjs                      tests 53 / pass 53 / fail 0
```

本 worktree 原本没有 `.venv`（`uv venv --python 3.11 .venv` + `uv pip install -e "./app[dev]"` 建好，`.venv` 已在 `.gitignore` 内、不进 diff）。

## 8. 两层文案归属的校验方向（确认没有挪层）

| 校验 | 读数 |
| --- | --- |
| `system.reminder.*` 只在后端 `app/alibot/i18n.py` | `grep -c "system.reminder" app/alibot/i18n.py` ⇒ **4**（fired / fired_late × zh/en），前端零命中 |
| 前端 `reminder.*` 键集未变 | **16 条**，zh/en 各出现**恰好 2 次**（每条两语言 1:1），无新增键 |
| 注释里没有点名 `system.reminder.*` | `i18n.js` 全文 grep 零命中（守住 `test_reminder_delivery.py:438`） |
| `reminder.dateLabel` / `stateFired` / `stateMissed` 仍只在前端 | 三者各出现 2 次（zh/en），后端 `i18n.py` 零命中 |

注释改动不构成用户可见文案，因此**不新增任何词条、不移动任何一条文案的层**：后端 `app/alibot/i18n.py` 本票 **0 行改动**。

## 9. 收尾读数

```text
git merge-base --is-ancestor origin/main HEAD && echo REBASE_OK
REBASE_OK
git diff --stat origin/main HEAD
 app/specs/issue-115-answer.md    | …（本文件）
 app/specs/issue-115-pr-body.md   | …
 app/alibot/studio/i18n.js        | …（注释）
 app/alibot/studio/index.html     | …（注释）
```

diff 面 = 三处注释块 + Answer/PR body 两份留档（仓库既有体例 `app/specs/issue-NNN-answer.md` / `issue-NNN-pr-body.md`），无其它文件。
