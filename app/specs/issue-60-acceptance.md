# Issue #60 定稿与拆票验收取证

规格正本：[#60](https://github.com/whutlichao/alibot/issues/60)。本文件记录本轮可复核事实与真实 tracker 回执；最终分支 head 以 #60 的唯一 `## Answer` 评论所核对的 PR 读数为准。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/ui-parity-spec`
- 分支：`ui-parity-spec`
- 基线：`main` = `fb36dfa2`（2026-10-16 取证；`SCHEMA_VERSION = 6`，`round_outcome.status` CHECK 仍为五个词）
- 前置 skills：完整读取 `/Users/apple/.agents/skills/grill-with-docs/SKILL.md`（它要求再读 `grilling/SKILL.md` 与 `domain-modeling/SKILL.md`，三份均已完整读）；`domain-modeling` 的 `GLOSSARY-FORMAT.md` 与 `ADR-FORMAT.md` 已读；写 PR body 前完整读取 `/Users/apple/.agents/skills/pr/SKILL.md`
- 本票不运行源码测试、lint/typecheck、双轴 code-review 或 G-impl；未修改生产代码或测试

## 裁定

### 用户先前的既成约束（不提问、不推翻）

| 项 | 定稿 | 来源 / 方式 |
| --- | --- | --- |
| U1 | 交付终点 = 收敛 → 规格 → 拆票 → 逐票实施并自动合并到 main，不停在票据 | 用户 2026-10-16；人定 |
| U2 | 语言切换 = 完整 i18n、全量双语（简体中文 / English） | 用户 2026-10-16；人定 |
| U3 | 新建任务后 Agent 主动认领 = 真做行为（配置开关 + 编排认领） | 用户 2026-10-16；人定 |
| U4 | 额外范围并入本轮：设置弹窗 / 提醒 / composer 工具行 / 成员弹窗 / 移动端抽屉壳 | 用户 2026-10-16；人定 |

### 一次批量 ask 的裁定（`msg_34986f2cc0c4`）

| 项 | 定稿 | 来源 / 方式 |
| --- | --- | --- |
| q1 | 主题 = 做（新增深色 token 族 + 三档；写面扩到 `alibot-design-system/**`；包内编号空间自定） | 用户本人选 (b)；人定 |
| q2 | 附件 = 真上传（存储 + 上传端点 + 消息关联 + 渲染 / 下载预览） | 用户本人选 (c)；人定 |
| q3 | Agent 详情 = 概览做实 + **运行时页签保留但只放 `agent_config` 真字段**；日志页签 / Token 消耗 / 上下文用量本轮不做 | 用户；人定 |
| q4 | 认领 = **不是跳**，但 **首次认领开该 task target 的世代并重置预算用量，每张任务至多一次** | 用户本人裁定（三个候选都不采用）；人定 |
| q5 | 语言偏好 / 消息密度 / 主题与提醒 = **后端落库**（`GET/PATCH /api/settings` + `reminder` 表）；`SCHEMA_VERSION` 6→7，#58 顺延到 8 | 用户；人定 |

**协调者先期转达的四项**（`msg_eea53a81aed2`）：Agent 详情做实与运行时口径、附件真上传、搜索扩后端（facets 四档 + 三类结果 + ⌘K）、i18n 含后端生成的用户可见文案。四项均已并入规格（决策账 §2 的 D20–D24）。

**两处推荐与裁定不同**（如实登记）：q2 我推荐「不做本体」、q4 我推荐「不消耗预算」，用户分别选了「真上传」与「开世代 + 每任务一次」。按裁定执行，推荐文本保留在决策账备查。

## Tracker 外部写操作回执

### 锚点 issue #60

```console
$ gh issue create --title "规格票：工作台 UI 第三轮收敛 —— 六面点名能力 + 设置/提醒/composer/成员/移动端补充面（含逐面数据面核实与拆票）" --body-file /tmp/anchor-wip.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/60
```

（建票时正文是 WIP 占位；定稿后以 `gh issue edit 60 --body-file /tmp/issue60-body.md` 覆盖，见下。）

### 12 张子票（`--parent 60`，即 GitHub 原生 sub-issue）

```console
$ gh issue create --parent 60 --title "① i18n 地基：词条机制 + 工作区设置面 + 前端全量双语 + 设置弹窗（语言 / 消息密度）" --body-file /tmp/t60/①\ .md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/61
$ gh issue create --parent 60 --title "② 实施票：后端可见文案双语（HTTP 错误 / system 消息 / reason / 远端告知）" ...
https://github.com/whutlichao/alibot/issues/62
$ ...（同一命令，依次得到 #63 #64 #65 #66 #67 #68 #69 #70 #71）
$ gh issue create --parent 60 --title "（Backlog）Agent 运行日志数据面 —— 日志页签的入口票（本轮不做）" ... --label ready-for-human
https://github.com/whutlichao/alibot/issues/72
```

编号与交付的对应：

```console
$ gh issue list --state open --json number,title,labels --limit 100 --jq '[.[]|select(.number>=61)]|sort_by(.number)|.[]|"\(.number)\t\([.labels[].name]|join(","))\t\(.title)"'
61	ready-for-agent	① i18n 地基：词条机制 + 工作区设置面 + 前端全量双语 + 设置弹窗（语言 / 消息密度）
62	ready-for-agent	② 实施票：后端可见文案双语（HTTP 错误 / system 消息 / reason / 远端告知）
63	ready-for-agent	③ 实施票：搜索面 —— 后端三类命中 + 主线搜索视图 + 四 facets + ⌘K 命令面板
64	ready-for-agent	④ 实施票：Agent 详情与编辑 —— dock 第三形态 + PATCH /api/agents/{id} + 概览 / 运行时 / 暂停
65	ready-for-agent	⑤ 实施票：Agent 主动认领 —— auto_claim 开关 + 编排认领 + 每任务一次的世代重置
66	ready-for-agent	⑥ 实施票：composer 工具行 —— @提及 落位 + 作为任务发送
67	ready-for-agent	⑦ 实施票：附件真上传 —— 存储 + 消息附件关联 + 渲染与下载
68	ready-for-agent	⑧ 实施票：提醒 reminders —— 库内对象 + 端点 + rail 入口与弹窗
69	ready-for-agent	⑨ 实施票：成员名册弹窗（频道成员只读全名单）
70	ready-for-agent	⑩ 实施票：移动端抽屉壳 —— topbar / rail 抽屉 / scrim / dock 抽屉 / 看板单列
71	ready-for-agent	⑪ 实施票：深色 token 族 + 主题三档（浅色 / 深色 / 跟随系统）
72	ready-for-human	（Backlog）Agent 运行日志数据面 —— 日志页签的入口票（本轮不做）
```

### 原生依赖边（`blocked_by`）

```console
$ gh api --method POST repos/whutlichao/alibot/issues/62/dependencies/blocked_by -F issue_id=5879116289
... "issue_dependencies_summary":{"blocked_by":1,"total_blocked_by":1,"blocking":0,"total_blocking":0} ...
（同法：63←61 / 64←61 / 66←61 / 68←61 / 69←61 / 70←61 / 71←61 / 65←64 / 67←66）
```

依赖复核（逐票读数，`blocked_by` = 未关闭的 blocker 数，`blocking` = 它阻塞几票）：

```console
$ for n in 61 62 63 64 65 66 67 68 69 70 71 72; do gh api repos/whutlichao/alibot/issues/$n --jq '.issue_dependencies_summary|{b:.blocked_by,k:.blocking}'; done
61 {"b":0,"k":8}   62 {"b":1,"k":0}   63 {"b":1,"k":0}   64 {"b":1,"k":1}
65 {"b":1,"k":0}   66 {"b":1,"k":1}   67 {"b":1,"k":0}   68 {"b":1,"k":0}
69 {"b":1,"k":0}   70 {"b":1,"k":0}   71 {"b":1,"k":0}   72 {"b":0,"k":0}
```

- **无循环依赖**：所有边都指向更小的①或以④/⑥为中继的短链（① → {②③④⑥⑧⑨⑩⑪}，④ → ⑤，⑥ → ⑦）。
- **无孤立票**：12 张全部是本票 #60 的 sub-issue。
- **#72 是 backlog 入口而不是实施票**：人定要求「日志页签不做 + 另立票记入口」，故它标 `ready-for-human`（按 `docs/agents/triage-labels.md` 的角色词表）、不挂依赖、不参与实施顺序；其余 11 张一律 `ready-for-agent`。
- **子票关系复核**：`gh api repos/whutlichao/alibot/issues/60/sub_issues --jq '[.[].number]'` → `[61,62,63,64,65,66,67,68,69,70,71,72]`。

### 更新规格正本（锚点 issue body = `app/specs/issue-60-spec.md` 第 7 行起的逐字镜像）

```console
$ gh issue edit 60 --body-file /tmp/issue60-body.md
https://github.com/whutlichao/alibot/issues/60

$ tail -n +7 app/specs/issue-60-spec.md | diff - <(gh issue view 60 --json body --jq '.body')
404a405
>            # 唯一差异：gh 存 body 时补了一个尾随换行（远端 body = 本地正文 + "\n"）

$ tail -n +7 app/specs/issue-60-spec.md | diff - <(gh issue view 60 --json body --jq '.body' | sed -e '${/^$/d;}')
(no output; exit 0)     # 去掉那个尾随空行后逐字一致

$ if gh issue view 60 --json body --jq '.body' | grep -nE '待定|待决|未决|⏳|本票不选'; then exit 1; else echo 'No pending-decision placeholders in issue #60 body'; fi
No pending-decision placeholders in issue #60 body

$ gh issue view 60 --json body --jq '.body|split("\n")[]|select(startswith("## "))'
## Problem Statement
## 逐面核实表（原型 vs 当前 studio vs 后端数据面）
## 决策账（Q → 谁答 → 答案 → 依据）
## Solution
## User Stories
## Implementation Decisions
## Testing Decisions
## Out of Scope
## Further Notes
```

正文含**非空 Out of Scope**、**逐面核实表**（六面 + 五个补充面，每条带 `file:line` 与「有真数据面 / 无数据面」标注）、**决策账**（U1–U4 / q1–q5 / D20–D24 / D3–D18），与 `issue-31-spec.md` / `issue-35-spec.md` 同构同级。

## 本轮文档改动

- `app/specs/issue-60-spec.md`：规格正本（issue body 的离线镜像）。
- `CONTEXT.md`：新增 8 条词条（**附件** / **提醒** / **任务命中 · Agent 命中** / **成员名册** / **认领** / **词条 · 词条表** / **语言偏好** / **工作区设置**），改写 3 条（**人类世代** 的开世代动作两种 → 三种并写明名字口径、**搜索命中** inline 三类同级、**成员面板** `_Avoid_` 收窄）。
- `docs/adr/0009-i18n-is-two-layers-with-a-stored-locale.md`：i18n 二层机制 + 语言偏好落库（决定性理由：唤醒编排跑在 HTTP 之外，无请求头可读）+ 历史不回溯 + 源语言口径。
- `docs/adr/0010-claiming-a-task-opens-one-epoch-per-task.md`：认领是编排发起的指派动作；不是跳；开一次世代；每任务至多一次；ADR-0005 原文不改（与 ADR-0007 处置 ADR-0002 同构）。
- `docs/adr/0011-agent-config-write-path-is-a-patch-endpoint.md`：`PATCH /api/agents/{id}`；配置变更不是对话动作；暂停 = `enabled=0` 与其已知代价。
- `docs/adr/0012-reminders-are-backend-objects.md`：提醒落库，不用 localStorage（依据 `CONTEXT.md` 工作台的正本纪律）。
- `docs/adr/0013-search-hits-extend-to-three-types.md`：搜索命中扩为三类，判定仍唯一落在后端。
- `docs/adr/0014-attachments-are-separate-objects-in-the-data-directory.md`：附件是独立对象、字节落数据目录、两步上传、无删除路径。
- ADR 编号 **0009–0014 连续**（`docs/adr/` 原有 0001–0008）；主题的 token 决策按 q1 写进**设计系统包自己的编号空间**，不占这里的号。

## 本地改动回执

本轮改动只落在授权面（`app/specs/**`、`CONTEXT.md`、`docs/**`），**零源码改动**：

```console
$ git status --porcelain
 M CONTEXT.md
?? app/specs/issue-60-acceptance.md
?? app/specs/issue-60-pr-body.md
?? app/specs/issue-60-spec.md
?? docs/adr/0009-i18n-is-two-layers-with-a-stored-locale.md
?? docs/adr/0010-claiming-a-task-opens-one-epoch-per-task.md
?? docs/adr/0011-agent-config-write-path-is-a-patch-endpoint.md
?? docs/adr/0012-reminders-are-backend-objects.md
?? docs/adr/0013-search-hits-extend-to-three-types.md
?? docs/adr/0014-attachments-are-separate-objects-in-the-data-directory.md

$ git add -N . && git diff --stat && git reset -q
 CONTEXT.md                                         |  44 ++-
 app/specs/issue-60-acceptance.md                   | 174 +++++++++
 app/specs/issue-60-pr-body.md                      |  52 +++
 app/specs/issue-60-spec.md                         | 410 +++++++++++++++++++++
 ...0009-i18n-is-two-layers-with-a-stored-locale.md |  16 +
 ...010-claiming-a-task-opens-one-epoch-per-task.md |  20 +
 ...-agent-config-write-path-is-a-patch-endpoint.md |   9 +
 docs/adr/0012-reminders-are-backend-objects.md     |  11 +
 docs/adr/0013-search-hits-extend-to-three-types.md |   9 +
 ...s-are-separate-objects-in-the-data-directory.md |  13 +
 10 files changed, 755 insertions(+), 3 deletions(-)

$ git diff --check
(no output; exit 0)
```

**改动面自检**：10 个文件全部落在 `app/specs/**`、`CONTEXT.md`、`docs/adr/**` 内；`app/alibot/**` 与 `app/tests/**` 零改动（`git status --porcelain` 里没有任何 `app/alibot/` 或 `app/tests/` 条目）。

`CONTEXT.md` 的实际改动（节选，逐条可复核）：**附件** 词条插在 **消息** 之后；**人类世代** 的开世代动作 ① ② → ① ② ③（③ 认领）；**认领** 词条插在 **任务** 之后；**提醒** 词条插在 **私信** 之后；**搜索命中** 与 **高亮区间** 之后新增 **任务命中 / Agent 命中**；**成员面板** 的 `_Avoid_` 去掉「成员详情弹窗」并新增 **成员名册** 词条；文件末新增 `### 语言与偏好` 段（**词条 / 词条表** / **语言偏好** / **工作区设置**）。

## 与既有票的协调事实（不是冲突）

- `SCHEMA_VERSION` 基线 6；本轮库内改动落地取 **7**，**#35 的 `silent`（#58）顺延到 8**。已写进规格决策账 D13 与 ADR 口径，并要求 #58 落地前复核。
- 本票不修改 #1 / #22 / #23 / #31 / #35 的票面，也不动 #53/#54/#55/#57/#58 的实施范围。

## 未运行项（如实登记）

本票不运行源码测试、lint/typecheck、双轴 code-review 与 G-impl：diff 全为文档与 tracker 写操作，没有源码行为改动，验收面是模板结构、术语格式、ADR 体例、tracker 原生依赖与正文占位扫描。
