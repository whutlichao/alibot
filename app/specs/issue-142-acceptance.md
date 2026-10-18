# Issue #142 验收：team 模式（channel + task target、队长、只唤醒队长、pipeline 重建）

- 票型：impl（G-impl）
- 分支：`issue-142-impl`
- 基线：集成分支 tip `798b68c`（含 #138 schema 16、#140 stream、#141 SSE）
- 落点：`app/alibot/team.py`（新增）、`orchestration.py`、`service.py`、`api.py`、`db.py`、`agent_seam.py`、`i18n.py`
- 测试：`app/tests/test_team_mode.py`（HTTP 黑盒 + `ManualScheduler` + 脚本化 seam；另有两个真适配器 + 进程内脚本模型的装配用例）

## 1. 判据 → 落点

| 票面判据 | 落点 |
| --- | --- |
| `GET/PATCH /api/channels/{id}/config` 读写 mode / `team_scope=channel+task` / leader / workspace / backend / permission；team 无合法队长整体拒绝 | `api.ChannelConfigUpdate` + `GET/PATCH`；`WorkspaceService.channel_config/update_channel_config`；`Database.get_channel_config/update_channel_config`（校验先于写入，单事务一次 UPDATE）；`TEAM_SCOPE` 在 `team.py` |
| channel 人类消息只唤醒队长；队长最终 1 条 message；队长回复零唤醒 | `WakeOrchestrator._team_config/_notify_team`；`_team_round_context` 只在队长轮次装配 pipeline；`notify` 对 team 频道的 Agent 消息返回 0 |
| task 人类/agent 消息也只唤醒队长；task 不穿透；非队长受派者无轮次 | `_notify_team` 的 task 分支：只投递队长、忽略 mention 穿透；`_team_round_context` 只对队长返回上下文 |
| owner 改派 / Agent 转派只唤醒队长；`claim_new_task()` 在 team 频道返回 0 | `notify_task_assignment/notify_task_reassignment` 新增 `actor_member_id`；`_notify_team_task_assignee`（actor=队长 ⇒ 0）；`claim_new_task` 读 `channel.mode` 早退 |
| 成员加入/移除后下一轮 pipeline 反映变化，不重启 | `_team_round_context` 每轮调用 `list_target_agent_members` + 可用性过滤；不缓存成员表（D11） |
| 内部名由 member id 派生；重名不失败 | `team.member_internal_name`（= `m-<member id>`）；`build_team_pipeline` 只用派生名构造 `TeamPipeline`，显示名只进成员描述 |
| autonomous / DM 不变 | `_team_config` 对 DM 返回 `None`、对 autonomous 频道返回 `None`；既有 `notify` 分支原样执行 |
| 队长不可用 ⇒ 零唤醒 + `invalid-leader` 可读原因 | `leader_status()`（不存在/删除/停用/非成员/不可运行）；`_record_invalid_leader` 写 `team_run(status='invalid-leader')`；`GET config` 的 `leader_available/leader_reason` 与裁决同一方法 |

## 2. 逐条命令与读数

所有 pytest 命令均在 `app/` 下执行；`-o addopts=""` 只为显示汇总行（仓库默认 `-q`）。

### AC1 频道配置读写 + 整体拒绝

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k config
```

读数：`4 passed, 17 deselected`。

覆盖：默认值（`autonomous` / `channel+task` / 无队长 / `local` / `ask-writes`）；五字段往返；`mode=team` 缺队长、非法 mode/backend/permission、非成员队长、相对项目根、显式 null、未知键全部 400 且配置**逐字未变**；`workspace_root: null` 可清空。

### AC2 channel 人类消息只唤醒队长、队长最终 1 条 message、回复零唤醒

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k team_channel
```

读数：`3 passed, 18 deselected`。

覆盖：两条消息（队长 + 成员）的频道里，人类消息后 `ManualScheduler.pending_count()==1`；`round_outcomes` 只有队长一行且 `status=replied`；Agent 消息恰好 1 条且作者是队长；成员 seam 调用为 0；队长那一轮的 `team.members` 含当前成员。队长回复落库后再调 `notify` 读数为 0、调度队列为空。穿透成员仍一次性送达、`team` 上下文为 `None`（不进入 pipeline）。

### AC3 task 人类/agent 消息只唤醒队长、不穿透

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k "team_task or task_agent or leader_agent"
```

读数：`3 passed, 18 deselected`。

覆盖：team 频道建任务后停在 `todo`、`claimed_at IS NULL`、零唤醒；任务人类消息（@ 未加入 Agent）只产生 1 个待跑轮次，外援与普通成员 seam 调用均为 0；task target 的 `round_outcome` 只有队长一行；任务线程 `message` 只有队长 1 条。非队长 Agent 的任务消息唤醒队长；队长自己的任务消息零唤醒。

### AC4 改派/转派/认领

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k "reassignment or transfer or claiming"
```

读数：`4 passed, 17 deselected`。

覆盖：owner 改派后只唤醒队长、新受派者零调用；Agent 转派（actor=非队长）只唤醒队长；actor=队长时转派零唤醒（`pending_count()==0`）且任务行已更新；team 频道即使成员 `auto_claim=true`，创建任务不认领，`claim_new_task()` 返回 0、`claimed_at IS NULL`。

### AC5 成员变化下一轮生效

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k "next_round or cannot_run"
```

读数：`2 passed, 19 deselected`。

覆盖：加入第二个成员后，**下一轮** `team.members` 从 `{甲}` 变为 `{甲, 乙}`（进程未重启）；成员不可运行（停用）后，下一轮不再进入 pipeline。注：本仓频道成员按既有契约**只能加入、不能移出**（`error.channel_members_append_only`），所以「移除」的产品路径是「成员不可运行」；两者都只经每轮重建生效。

### AC6 内部名派生 + 重名不失败

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k "pipeline_names or duplicate_display or real_seam"
```

读数：`3 passed, 18 deselected`。

覆盖：两个 `display_name` 相同的成员得到 `m-mem_a` / `m-mem_b` 两个不同内部名，`BudgetedTeamPipeline` 构造成功且不含队长名；真适配器（脚本模型）通过 `TeamPipeline.reply_stream` 跑完队长轮次，事件归属只有队长、最终结论仍来自队长。两个同名成员的真轮次同样成功。

### AC7 autonomous / DM 不变

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k "autonomous or dms"
```

读数：`2 passed, 19 deselected`。

覆盖：autonomous 频道仍是全成员唤醒（两名成员都有轮次与调用，`team` 上下文均为 `None`）；DM 仍只唤醒单一 Agent 且无 team 上下文。

### 队长不可用的可读失败面

```bash
cd app && ../.venv/bin/python -m pytest tests/test_team_mode.py -o addopts="" -q -k invalid_leader
```

读数：`1 passed, 20 deselected`。

覆盖：停用队长后人类消息零唤醒、零 `round_outcome`；`GET config` 返回 `leader_available=false` 且 `leader_reason` 含「停用」；`team_run` 落一行 `status='invalid-leader'`。

### 全量

```bash
scripts/check.sh
```

读数：见 §3。

## 3. 全量门禁读数

```text
$ scripts/check.sh
== pytest（全量）
729 passed, 2 skipped in 316.02s (0:05:16)

== ruff check
All checks passed!

== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
171 files already formatted

== mypy（仓库根）
Success: no issues found in 68 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 136
ℹ pass 136
ℹ fail 0

门禁全绿
```

## 4. 残余与移交给后续票

- **T2（#143）**：`team.BudgetedTeamPipeline._authorize_assignments()` 是预算的独占覆写点；T1 原样放行，不扣跳、不生成错误 `ToolResult`。`name_to_member` / `member_of()` / `target_id` / `task_id` 已就绪。
- **T3（#144）**：成员输出只走事件与 `team_assignment`；T1 的真适配器已不把成员回复写成 `message`，但 `team_assignment` 写入与快照端点仍归 T3。
- **T4（#145）**：任务五态与 `TeamAssign` 的 `task_id` 记录。当前 `SetTaskStatusAction` 仍要求 actor 是 task target 成员；队长通常只是频道成员，T4 需按票面扩展这条规则。
- **成员移除**：频道成员面只有加入端点（既有契约），因此验收用「成员不可运行」验证下一轮重建；没有新增移除路径。
- **`consumed_budget`**：team 频道的人类消息唤醒队长时恒为 0（队长轮不由跳送来）。task 线程里「非队长 Agent 消息唤醒队长」仍沿用既有 agent→agent 跳预算（该路径在 team 下没有正常产品写入口）。
