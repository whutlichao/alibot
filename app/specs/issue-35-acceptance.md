# Issue #35 定稿与拆票验收取证

规格正本：[#35](https://github.com/whutlichao/alibot/issues/35)。本文件记录本轮可复核事实与真实 tracker 回执；最终分支 head 以 #35 的唯一 `## Answer` 评论所核对的 PR 读数为准。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-35-silence-spec`
- 基线：`main` 已包含 #31 合并提交 `742188b`
- 前置 skills：完整读取 `/Users/apple/.pi/agent/skills/to-spec/SKILL.md`、`to-tickets/SKILL.md`、`domain-modeling/SKILL.md` 与其 `ADR-FORMAT.md`；写 PR body 前完整读取 `/Users/apple/.pi/agent/skills/pr/SKILL.md`
- 本票不运行源码测试、双轴 code-review 或 G-impl；未修改生产代码或测试。

## 用户裁定

| 项 | 定稿 | 来源 / 方式 |
| --- | --- | --- |
| D1 | 判据-4：仅呈现层降档；后端判据不动；未读、唤醒与预算污染作为已知代价；宁可少降档，不可误吞真实回复 | 用户 2026-10-16 定夺；人定 |
| D3 | 落点-b：`SEAM_STATUSES` 加 `silent`；持久化 CHECK、Studio 中文状态、`SCHEMA_VERSION` 及不做旧数据迁移同步处置；新增 ADR-0008 | 用户 2026-10-16 定夺；人定 |
| D4 | 形态-a：历史与未来旁白保留各自 message 行 / `seq`，轻量呈现并展开看 reason；不合并摘要。形态-c 明确否决，与判据落点唯一冲突 | 用户 2026-10-16 定夺；人定 |
| 拆票 | ① prompt 纪律（含 D5 措辞定稿）+ 呈现降档；② 结构化 `silent` + 互斥校验和状态/CHECK/schema 处置；② blocked by ① | 用户 2026-10-16 定夺；人定 |

## Tracker 外部写操作回执

### 实施票 #57

```console
$ gh issue create --parent 35 --title "实施票：Agent 旁白 prompt 纪律改写 + 呈现层降档" --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue35-child-1.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/57
```

### 实施票 #58 与标题修正

```console
$ gh issue create --parent 35 --title "实施票：结构化沉默信号 `silent` + RoundOutcome 互斥校验" --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue35-child-2.md --label ready-for-agent
/bin/bash: silent: command not found
https://github.com/whutlichao/alibot/issues/58

$ gh issue edit 58 --title '实施票：结构化沉默信号 silent + RoundOutcome 互斥校验'
https://github.com/whutlichao/alibot/issues/58
```

#58 已确认 `ready-for-agent`，Parent 为 #35，OPEN；标题修正后的正文仍以 `Part of #35` 开头。

### 新票数量与父子关系复核

开票前 open issue 编号为 `[55, 54, 53, 35, 31, 1]`；开票后新增的只有 #57 与 #58：

```console
$ gh issue list --state open --json number,title,labels --limit 100 --jq '[.[] | select(.number == 57 or .number == 58) | {number,title,labels:[.labels[].name]}]'
[{"labels":["ready-for-agent"],"number":58,"title":"实施票：结构化沉默信号 silent + RoundOutcome 互斥校验"},{"labels":["ready-for-agent"],"number":57,"title":"实施票：Agent 旁白 prompt 纪律改写 + 呈现层降档"}]

$ gh api repos/whutlichao/alibot/issues/35/sub_issues --jq '[.[].number]'
[57,58]
```

### GitHub 原生依赖 ① → ②

```console
$ gh api repos/whutlichao/alibot/issues/57 --jq .id
5879116289

$ gh api --method POST repos/whutlichao/alibot/issues/58/dependencies/blocked_by -F issue_id=5879116289
... "number":58,"title":"实施票：结构化沉默信号 silent + RoundOutcome 互斥校验" ...
... "issue_dependencies_summary":{"blocked_by":1,"total_blocked_by":1,"blocking":0,"total_blocking":0} ...
```

依赖复核：

```console
$ gh api repos/whutlichao/alibot/issues/58 --jq .issue_dependencies_summary
{"blocked_by":1,"blocking":0,"total_blocked_by":1,"total_blocking":0}
```

### 更新规格正本

```console
$ gh issue edit 35 --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-35-body-final.md
https://github.com/whutlichao/alibot/issues/35

$ if gh issue view 35 --json body --jq '.body' | grep -nE '⏳|待定|待决|未决|本票不选'; then exit 1; else echo 'No pending-decision placeholders in issue #35 body'; fi
No pending-decision placeholders in issue #35 body
```

本地 `app/specs/issue-35-spec.md` 是 issue body 的逐字镜像；D1 / D3 / D4 候选对照表和明确结论均保留。两个新票分别为 #57 / #58，且没有额外新票或治理结构。

## 本轮文档改动

- `app/specs/issue-35-spec.md`：同步规格正本并登记 #57 / #58 与依赖。
- `CONTEXT.md`：加入「沉默 (Silence)」及 `_Avoid_` 中的「旁白式交代 (Silent Narration)」；在「轮次结果」词条 inline 说明 `silent` 与 `ignored` 的边界。
- `docs/adr/0008-structured-silence-is-a-distinct-round-outcome.md`：按既有 ADR 体例记录 status 边界、不能用 reason 表达的理由、schema 版本递增与无旧数据迁移。
- `app/specs/issue-35-pr-body.md`：PR body 使用 Summary / Evidence / Merge Danger 模板。

## 本地文档改动回执

`CONTEXT.md` 修改的实际 diff：

```console
$ git diff -- CONTEXT.md
diff --git a/CONTEXT.md b/CONTEXT.md
index d5dfbb4..4b72064 100644
--- a/CONTEXT.md
+++ b/CONTEXT.md
@@ -106,6 +106,10 @@ Agent 每轮 decide 的回应规则。**确定信号**只有一条：被个人提及 —— **必须回应**，自判忽略按 `failed` 处理
 （不静默、不留"它答应了"的假象）。**明确无关**（他人之间的对话、未点名自己）：应当 `ignored`，这是正常的
 协议选择而不是失败。其余按相关性自判，允许忽略。
 _Avoid_: 过滤、优先级（它们是裁剪信号的手段，不是"要不要回应"的判断）

+**沉默 (Silence)**:
+一轮不以普通正文回应的用户可见语义。现有 `ignored` 表示 seam 的正常自判忽略；结构化状态 `silent` 专指模型经显式结构化信号声明本轮沉默。两者都是正常协议结果，不是失败；`silent` 不是消息 kind，也不由 `reason` 文本推导。
+_Avoid_: **旁白式交代 (Silent Narration)**（反面词：Agent 把「我不需要说话」当成正文说出来，因此仍是一条真实 Agent 消息）；沉默说明、空回应、括注消息（会把「说了等于没说」误读成低配回应）。

 **轮次 (Round)**:
 Agent 一次 wake → 读消息 → 判断 → 回应 → 记结果的完整处理周期。**一轮 ↔ 至多一条 target 消息**，落库路径
 @@ -114,10 +118,11 @@ _Avoid_: 会话、session（那是模型侧的上下文，不是本仓的处理周期）

 **轮次结果 (Round Outcome)**:
 一轮的处理结论，落库为 `round_outcome` 行（**append-only**，与消息同级的取证面）：`member_id`、`target_id`、
-`base_seq`、status、可读 reason、起止时间、是否消耗预算、是否被合并。status 词表共五个：
+`base_seq`、status、可读 reason、起止时间、是否消耗预算、是否被合并。status 词表共六个：
 `replied`（已回复）/ `ignored`（自判忽略，正常协议选择）/ `failed`（需排查，含"被点名却不回应"）/
-`merged`（被合并的重复唤醒）/ `budget-exhausted`（预算耗尽、本轮零投递）。它是"自判忽略"与"处理失败"这两个
-事实的区分来源，也是循环上界的取证面。
+`merged`（被合并的重复唤醒）/ `budget-exhausted`（预算耗尽、本轮零投递）/ `silent`（模型经结构化信号明确声明沉默）。
+`silent` 与 `ignored` 不同：前者有显式结构化声明，后者是既有 seam 的正常自判忽略；两者均不是 `failed`。`reason` 继续提供可读诊断，不能替代 status。
 它是"自判忽略"与"处理失败"这两个事实的区分来源，也是循环上界的取证面。
```

ADR 文件实际内容：

```console
$ cat docs/adr/0008-structured-silence-is-a-distinct-round-outcome.md
# 结构化沉默是独立的轮次结果状态

模型通过结构化通道明确声明本轮不回应，与既有 `ignored`（seam 的正常自判忽略）是不同的可取证事实；若把声明只写入 `reason`，状态查询与库级校验就只能依赖自由文本，无法稳定地区分两种语义。我们决定将 `silent` 加为 `SEAM_STATUSES` 的第四个词，并纳入持久化 `ROUND_STATUSES` / CHECK 与前端状态词表；`silent` 必须与非空待落库消息互斥，`reason` 只保留可读诊断，不承担状态判别。

该状态不新增 `MESSAGE_KINDS`。库级 CHECK 变更要求 `SCHEMA_VERSION` 从 6 增至 7；遵循 ADR-0004，不提供旧数据迁移。该跨模块持久化契约由用户于 2026-10-16 定夺，裁定方式为人定。
```

Issue 正文与离线镜像核对：

```console
$ tail -n +7 app/specs/issue-35-spec.md | diff - <(gh issue view 35 --json body --jq '.body')
(no output; exit 0)

$ git diff --stat -- app/specs/issue-35-spec.md
 app/specs/issue-35-spec.md | 448 +++++++++++++++------------------------------
 1 file changed, 148 insertions(+), 300 deletions(-)

$ git diff --check
(no output; exit 0)
```

## Commit、push 与 PR 回执

```console
$ git commit -m "docs(spec): finalize #35 silence decisions and tickets"
[issue-35-silence-spec 348472f] docs(spec): finalize #35 silence decisions and tickets
 5 files changed, 283 insertions(+), 567 deletions(-)
 create mode 100644 docs/adr/0008-structured-silence-is-a-distinct-round-outcome.md

$ git push -u origin issue-35-silence-spec
 * [new branch]      issue-35-silence-spec -> issue-35-silence-spec
branch 'issue-35-silence-spec' set up to track 'origin/issue-35-silence-spec'.

$ gh pr create --base main --head issue-35-silence-spec --title "docs(spec): #35 finalize silence decisions and split implementation tickets" --body-file app/specs/issue-35-pr-body.md
https://github.com/whutlichao/alibot/pull/59

$ gh pr view 59 --json number,state,url,baseRefName,headRefName,headRefOid,body,files --jq '{number,state,url,baseRefName,headRefName,headRefOid,files:[.files[].path],bodySections:[.body|split("\n")[]|select(startswith("## "))]}'
{"baseRefName":"main","bodySections":["## Summary","## Evidence","## Merge Danger"],"files":["CONTEXT.md","app/specs/issue-35-acceptance.md","app/specs/issue-35-pr-body.md","app/specs/issue-35-spec.md","docs/adr/0008-structured-silence-is-a-distinct-round-outcome.md"],"headRefName":"issue-35-silence-spec","headRefOid":"348472f8b886acc4e2fc664cea3baa3f25c68434","number":59,"state":"OPEN","url":"https://github.com/whutlichao/alibot/pull/59"}
```

PR body 正本：`app/specs/issue-35-pr-body.md`（使用 `--body-file`）。此读数核对的是 PR 创建时的 commit；后续验收文档提交会推进 head，因此不把会自指漂移的最终 SHA 写进本文件，最终 head 由唯一的 #35 `## Answer` 评论记录。未运行源码测试。
