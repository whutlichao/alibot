# issue #31 规格定稿与拆票取证

本文件记录当前 Dispatch 的执行证据；规格正本是 [GitHub issue #31](https://github.com/whutlichao/alibot/issues/31)，离线正文副本为 `app/specs/issue-31-spec.md`。

- **日期 / 裁定来源**：2026-10-16；W1、L2、固定 3 / 5 / 10 档位、默认仍 3、下一人类世代生效、三票拆分均为用户人定裁定。
- **票型**：规格定稿 + 票据拆分。
- **限制**：不改生产代码或测试，不运行源码测试、双轴 code-review、G-impl 或实施动作。
- **基线**：开工前 `git status --short --branch` 输出 `## issue-31-budget-spec-final`，`git diff --stat` 无输出；分支基于 `origin/main` `0234d32`。

## 1. Skills 与设计核实

开工前完整读取：

- `/Users/apple/.agents/skills/to-spec/SKILL.md`：遵守七节模板，声明并复用最高层现有测试接缝。
- `/Users/apple/.agents/skills/to-tickets/SKILL.md`：按纵向 tracer bullet 拆票；GitHub 使用 sub-issue 与原生 blocking 关系。
- `/Users/apple/.agents/skills/domain-modeling/SKILL.md` 及 ADR / Glossary 格式文件：只在有真实边界决定时新增 ADR；词条 inline 更新 `CONTEXT.md`。
- `/Users/apple/.pi/agent/skills/pr/SKILL.md`：PR 正文使用 Summary / Evidence / Merge Danger。

已核实的设计边界：`PATCH /api/channels/{id}/budget` 是单频道写入口；ADR-0004 规定 channel 型 target 复用 `channel.id`，因此库内逐频道 L2 与写入作用域一致。数据库预算正本是 `target_budget(target_id, epoch_seq, used)`；配置化不改变 `used` 的后端所有权。没有发现需要推翻 L2 的矛盾。

## 2. 本地文档写入回执

```text
functions.patch — Update CONTEXT.md（人类世代、回应预算、预算耗尽、预算表词条定义；跳词条保持原口径）
→ Success: M CONTEXT.md

functions.patch — Add docs/adr/0007-human-face-writes-budget-limit-only.md
→ Success: A docs/adr/0007-human-face-writes-budget-limit-only.md

functions.patch — Delete then Add app/specs/issue-31-spec.md with the finalized seven-section specification
→ Success: D app/specs/issue-31-spec.md
→ Success: A app/specs/issue-31-spec.md

functions.patch — Delete then Add app/specs/issue-31-acceptance.md with this evidence record
→ Success: D app/specs/issue-31-acceptance.md
→ Success: A app/specs/issue-31-acceptance.md

shell — cat > app/specs/issue-31-acceptance.md <<'EOF' ... EOF
→ stdout empty, exit code 0
```

ADR-0002 未改动，结论在规格中明确为“保留，不修订、不取代”；W1 写入边界记录在 ADR-0007。五条既有预算词条均已处置：人类世代、回应预算、预算耗尽、预算表泛化为当前有效上界与后端用量正本；跳的定义保持不变，因为跳数口径没有改变。上界不再被固定定义为 3。

## 3. issue #31 正本更新回执

```console
$ gh issue edit 31 --repo whutlichao/alibot --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-31-body.md
https://github.com/whutlichao/alibot/issues/31

$ gh issue edit 31 --repo whutlichao/alibot --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-31-body.md
https://github.com/whutlichao/alibot/issues/31
(正文镜像措辞复核后的同步更新)

$ gh issue view 31 --repo whutlichao/alibot --json body --jq '.body | @base64' | base64 -d > /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-31-body.exact.md
$ diff -q <(tail -n +6 app/specs/issue-31-spec.md) /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-31-body.exact.md
（stdout 为空，退出码 0：正文与离线副本逐字一致）
```

## 4. 三张实施票与原生依赖回执

### Issue 创建

```console
$ gh issue create --repo whutlichao/alibot --parent 31 --title "实施票：回应预算配置落点 + 只读呈现" --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-31-ticket-1.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/53

$ gh issue create --repo whutlichao/alibot --parent 31 --title "实施票：回应预算人类面上界写入口 + 边界 ADR" --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-31-ticket-2.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/54

$ gh issue create --repo whutlichao/alibot --parent 31 --title "实施票：回应预算 ADR 实测依据 + 默认档位复核" --body-file /private/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/opencode/issue-31-ticket-3.md --label ready-for-agent
https://github.com/whutlichao/alibot/issues/55
```

### Database ID 与 dependency 创建

```console
$ gh api repos/whutlichao/alibot/issues/53 --jq .id
5878234981
$ gh api repos/whutlichao/alibot/issues/54 --jq .id
5878235756

$ gh api --method POST repos/whutlichao/alibot/issues/54/dependencies/blocked_by -F issue_id=5878234981
（HTTP 成功；返回 issue 54，issue_dependencies_summary：{"blocked_by":1,"blocking":0,"total_blocked_by":1,"total_blocking":0}）

$ gh api --method POST repos/whutlichao/alibot/issues/55/dependencies/blocked_by -F issue_id=5878235756 --jq .issue_dependencies_summary
{"blocked_by":1,"blocking":0,"total_blocked_by":1,"total_blocking":0}
```

### Tracker 复核

```console
$ gh api repos/whutlichao/alibot/issues/54 --jq .issue_dependencies_summary
{"blocked_by":1,"blocking":1,"total_blocked_by":1,"total_blocking":1}
$ gh api repos/whutlichao/alibot/issues/55 --jq .issue_dependencies_summary
{"blocked_by":1,"blocking":0,"total_blocked_by":1,"total_blocking":0}

$ gh api repos/whutlichao/alibot/issues/31/sub_issues --jq '.[] | {number,title}'
{"number":53,"title":"实施票：回应预算配置落点 + 只读呈现"}
{"number":54,"title":"实施票：回应预算人类面上界写入口 + 边界 ADR"}
{"number":55,"title":"实施票：回应预算 ADR 实测依据 + 默认档位复核"}
```

`gh issue list --repo whutlichao/alibot --state open --json number,title,labels` 复核结果中，新增的 #53 / #54 / #55 均带 `ready-for-agent`；没有新增第四张票。依赖顺序为 #54 blocked by #53、#55 blocked by #54。按 coordinator 经 `orca orchestration ask` 裁定，#31 保持 OPEN，本 Dispatch 不改状态 / 标签 / 标题；待三张实施票全部落地并合并后由后续 Dispatch / coordinator 关闭。

## 5. 门禁与余项

- **G-spec**：七节齐全；W1/L2/固定档位/生效时机/三票依赖均写入正本并标注用户 2026-10-16 人定；测试接缝只声明现有 HTTP 黑盒 + 手动调度器 / 脚本化 seam；Out of Scope 非空。
- **G-tickets**：恰好三张纵向实施票，均为 #31 sub-issue 且标记 `ready-for-agent`；②→①、③→② 使用 GitHub 原生 `blocked_by` 并已复核。
- 本票未运行 `pytest` / `node --test`，未运行双轴 review 或 G-impl；没有修改生产代码或测试。
- PR 与最终 head SHA、唯一追加的 `## Answer` 评论及其状态回执以 issue #31 的最终 Answer 为准。
