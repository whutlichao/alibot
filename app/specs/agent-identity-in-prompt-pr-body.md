## Summary

Agent 一直不知道自己的显示名：`display_name` 只进消息作者名（`_author_name`）与 `TeamAssign` 的工具描述
（`member_description`），system prompt 里没有一句说「你叫什么」。本票把显示名作为**身份句**每轮注入，
来源只有一个函数，三处拼装点同一位置。

```diff
 app/alibot/
+├── agent_identity.py   # identity_line(member) -> str | None —— 身份句的唯一来源
 ├── agent_seam.py       # _system_prompt / _team_member_prompt 各插一段
 └── sop.py              # sop_agent_prompt 插同一段
```

system prompt 的段落顺序（三处一致）：

```diff
  全局基座：<~/.alibot/agents/<id>/AGENTS.md>
  配置段  ：<agent_config.system_prompt>
  项目补充：<ws>/.alibot/agents/<id>/AGENTS.md>
+ 你在工作台里的名字是「小明」。成员与人类都用这个名字称呼你。
  你的角色：工程师
  回应纪律：…（rubric 逐字不变）
```

- 措辞逐字固定，空 / 全空白显示名 → 该段**缺席**（不写空标题、不写占位）。
- D10 不变：框架内部名仍是 `m-<member_id>`，不进 prompt；身份句只用显示名，且不声明唯一性
  （成员显示名允许重复）。
- `你的角色：` 行与 `_CHANNEL_RUBRIC` / `_DIRECT_MESSAGE_RUBRIC` 一字不动。
- 新增 `docs/adr/0033-agent-knows-its-display-name.md`；`CONTEXT.md` 的 **Agent** 词条补一句。

SOP 参与者仍**不**纳入三段文件规则（#230 的既有裁决，`test_sop_participant_does_not_get_the_three_rule_segments`
继续绿）：身份句不是频道协作规则，它是「同一个 Agent 在某一轮里是谁」的事实，因此三处都加。

## Evidence

**单一来源（验收 6）**

```text
$ grep -rn "你在工作台里的名字是" app/alibot
app/alibot/agent_identity.py:33:    return f"你在工作台里的名字是「{display_name}」。成员与人类都用这个名字称呼你。"
```

**Before → After（隔离基线 worktree `83805a1` + 仅新增 `agent_identity.py`，三处拼装点未接线）**

| 调用点 | 基线读数 | 接线后 |
| --- | --- | --- |
| `_system_prompt`（频道 / DM） | `FAILED test_channel_and_direct_message_prompts_state_the_display_name` | pass |
| `_team_member_prompt`（team 成员） | `FAILED test_team_leader_and_member_each_get_the_three_rule_segments`（`assert leader_identity in leader_prompt`） | pass |
| `sop_agent_prompt`（SOP executor / verifier） | `FAILED test_sop_participant_prompt_states_the_display_name` | pass |

基线断言原文（SOP 路径）：

```text
E  AssertionError: assert '你在工作台里的名字是「小苏」。成员与人类都用这个名字称呼你。' in
   'SOP 配置段。\n\n你的角色：执行\n\n你是一次 SOP 运行的 executor：…'
```

**门禁读数（接线后）**

```text
$ cd app && pytest tests/test_global_rules.py tests/test_sop.py tests/test_team_task_threads.py
50 passed in 9.46s

$ scripts/check.sh --fast
门禁全绿        （node glob：284 pass / 0 fail；ruff check、ruff format --check 311 files、
                mypy 124 files、pyright 0 errors）
```

**空显示名（验收 4）**：`display_name = "   "` 时频道轮次与 team 成员轮次都不出现身份句、`你的角色：`
照旧；SOP 路径同样缺席（`test_blank_display_name_leaves_the_identity_line_out` /
`test_sop_participant_prompt_omits_the_identity_line_when_the_display_name_is_blank`）。

## Merge Danger

**Door:** two-way

纯增量 prompt 行为：无持久化格式变更、无数据迁移、无 schema 变更（`SCHEMA_VERSION` 未动）、
无权限档与工具面变更。回滚方式：`git revert <本票 squash 后的提交>`——revert 后每轮 prompt 少一行，
其余段落与既有测试读数逐字恢复。

**Blast Radius:** prompt-only

- 每个轮次的 system prompt 多一行（约 40 字）：只影响模型输入，不改变任何落库数据与 API 形状。
- 三个调用点覆盖单 Agent（频道 / DM / task / team 队长）、team 成员、SOP 参与者；`ScriptedAgentSeam`
  等替身不经过这条路径，脚本化用例读数不变。
- 显示名重复时不会撞名（D10 的内部名未变），只是多个 Agent 会看到同一个称呼——这是既有的「显示名可
  重复」口径。
