## Summary

`ReplySignal`（进程内枚举，不落库）取代「单一布尔」承载回应纪律，同时回答两件事：**是否必答**、
**可读文案按哪个来源说**。

```diff
 notify(target_id, from_seq, reason)
-  jump_delivered = author_kind == "agent" ...
-  submit(run_round, member, ..., )                    # 人类消息 → 非必答
+  base_signal = HUMAN_MESSAGE if message["kind"] == "human" else NONE
+  submit(run_round, member, ..., member_signal)        # 人类消息 → 必答（四类同级不同源）

 run_round(..., signal)
-  definite_signal = mentioned or mandatory_reply
-  leader_task_change = team_context is not None and mandatory_reply
-  key = claim_* | team_change_*                        # 只有两种来源
+  definite_signal = mentioned or signal.mandatory
+  copy_prefix = {CLAIM, TEAM_TASK_CHANGE, HUMAN_MESSAGE} → claim | team_change | human_message
+  key = f"reason.{copy_prefix}_{silent|ignored|no_body|round}"

 _system_prompt(agent, mentioned, task_context, signal, direct_message)
+  direct_message=True  → 一对一口径（不再说「多成员频道」「未被点名可以沉默」）
+  signal=HUMAN_MESSAGE → 独立一段「人类消息唤醒，你必须回应」（channel 的 #57 rubric 逐字不变）
```

四个面同一口径：**DM**（owner 每条消息 → 唯一 Agent 必答）、**autonomous 频道**（→ 每个 Agent 成员必答）、
**team 频道**（→ 队长必答，文案走「人类消息」而非「任务变化」）、**task target**（→ 受派者必答；
team 任务线程按既有裁决走队长）。

**不动的边界**：唤醒面 / 穿透 / 回应预算与 `target_budget` 记账 / 未读口径 / 消息 kind /
`round_outcome` 状态词表 / `SCHEMA_VERSION` / HTTP 端点 / UI；非人类作者（Agent、system）的纪律一律不变，
team 队长自己的回复仍零唤醒，`declare_silence` 结构化通道保留（人类消息必答之外仍适用）。

## Evidence

**Before**（新增用例，实现前，红）：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py
FAILED ...::test_a_dm_owner_message_is_a_mandatory_round
FAILED ...::test_a_silent_dm_round_is_a_failure_that_names_the_human_message
FAILED ...::test_a_human_message_makes_every_member_of_an_autonomous_channel_mandatory
FAILED ...::test_an_empty_body_on_a_human_message_round_is_a_failure
FAILED ...::test_a_team_channel_human_message_is_a_mandatory_leader_round_with_human_wording
FAILED ...::test_an_owner_message_in_a_task_thread_makes_the_assignee_mandatory
FAILED ...::test_the_dm_prompt_speaks_one_to_one_and_requires_a_reply
FAILED ...::test_the_channel_prompt_keeps_the_silence_rubric_and_appends_the_human_message_rule
FAILED ...::test_an_agent_authored_message_still_leaves_the_round_to_self_judgement
9 failed in 0.52s
```

**After**（最终 head）：

```text
$ cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py
10 passed in 0.52s                     # 第 10 条是 review 收口后补的在飞轮次边界用例

$ git show origin/main:app/alibot/orchestration.py > app/alibot/orchestration.py  # 只回退实现、保留新用例
$ cd app && ../.venv/bin/python -m pytest tests/test_mandatory_reply.py
10 failed in 3.47s                     # 10 条用例全部因本票要改的事实而红

$ scripts/check.sh
944 passed, 10 skipped in 536.15s (0:08:56)
All checks passed!                    # ruff check
237 files already formatted           # ruff format --check
Success: no issues found in 101 source files   # mypy（仓库根）
0 errors, 0 warnings, 0 informations   # pyright
ℹ tests 191 / pass 191 / fail 0        # node --test（Studio DOM，glob）
门禁全绿                               # real 550.69s，退出码 0
```

判据 → 落点 → 命令 → 读数的完整表在 `app/specs/issue-189-acceptance.md`（含既有用例逐条更新说明，
以及 `notify` 判据必须读**消息 kind** 而不是作者成员 kind 的实现细节）。

## Merge Danger

**Door:** two-way —— 纯行为与文案改动：无 schema/迁移、无新端点、无新状态词、无前端改动；
回退只需 revert 这一个提交。

**Blast Radius:** 每一轮「被人类消息唤醒」的轮次纪律（DM / 频道 / team / 任务线程）。

合并后行为变化是**本票的目的**：人类消息不再允许合法沉默（此前 `ignored` / `silent` 会落成 `failed`），
轮次结果的 reason 文案按信号来源分家（新增 `reason.human_message_*` 四条词条 ×2 语言）。
非人类作者的消息、唤醒面、预算与未读口径逐字不变；UI 侧沿用既有的 `failed` 呈现，无需改动。

Closes #189
