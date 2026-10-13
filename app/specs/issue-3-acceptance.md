# 实施票 ② 验收取证（issue #3）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。
issue 上的 Answer 摘要指向本文件。

> 阅读说明：§3 的表格指到具体用例（跑 `pytest -k <名字>` 即可复现）；§4 的代码块是**多次**
> 真实浏览器 / 真实进程实测的输出片段，每次都在独立临时数据目录上跑，因此频道 id 与 seq 起点
> 在各片段间不同 —— 它们记录的是各自那一次的事实，不是同一条连续时间线。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-3-agent-sse`（Orca 独立顶层 worktree）
- 基线：以 #2 已合并的 main（merge commit `5015383`）为基线
- 采集时间：2026-10-13
- 环境：macOS，Python 3.11（`uv venv`），真实 Chromium（ego-browser）驱动
- 有效用例数：**113 passed, 1 skipped**（`app/tests/`；跳过的就是那条默认跳过的真模型冒烟测试）
- 票标题（开工时用 `gh issue view 3` 核对）：

> 实施票 ②：新建 Agent 与模型配置、远端数据流告知、@提及唤醒 AgentScope 后端 agent 并自判回应/忽略/失败、SSE 实时呈现与断线补拉

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：
读票（`gh issue view 3` + `gh issue view 1`）→ 声明票标题 → **复工第一步核对 AgentScope stable 并 pin**
→ 按 `tdd` 逐条把契约写成 HTTP/SSE 黑盒测试 → 定期跑类型检查与单文件测试 → 全量 → `code-review` 双轴
→ 提交 → PR。`code-review` 的两轴 findigns 与处置见 §6。

## 1. AgentScope stable 版本核对（2026-10-13）与 pin

```text
$ curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" https://docs.agentscope.io/stable/en
308 -> https://docs.agentscope.io/en/versions/2.0.9/

$ curl -s https://pypi.org/pypi/agentscope/json | python3 -c "..."
2.0.10  >=3.11          # PyPI 最新；stable 文档尚未指向它

$ curl -s -o /dev/null -w "%{http_code}\n" https://docs.agentscope.io/en/versions/2.0.9/quickstart
200
```

**pin 的确切版本号：`agentscope==2.0.9`**（`app/pyproject.toml`）。`docs.agentscope.io/stable`
本次核对仍指向 2.0.9。

实际使用的 2.x API 形状（在装好 `agentscope==2.0.9` 的解释器上逐个核对签名后落地）：

```text
Agent.__init__     (self, name: str, system_prompt: str, model: ChatModelBase,
                    toolkit=None, middlewares=None, state=None, offloader=None,
                    model_config=None, context_config=None, react_config=None,
                    injection_config=None)
Agent.reply        (self, inputs: Msg | list[Msg] | ... | None = None,
                    structured_schema=None) -> Msg
Msg.model_fields   name, content, role, id, metadata, created_at, usage,
                   finished_at, finished_reason, structured_output, error
CredentialFactory.get_credential_class(provider_type) -> CredentialBase 子类
<x>Credential.get_chat_model_class() -> ChatModelBase 子类
providers          dashscope / openai / openai-response / anthropic / gemini /
                   deepseek / moonshot / minimax / volcengine / xai / ollama
```

**没有**使用 1.x 的 `ReActAgent(name, sys_prompt, model, formatter, toolkit, memory)` 签名，
**没有**引用 `/latest/`，**没有**使用框架的 Agent Service（FastAPI）。

可查证据（零使用 1.x / Agent Service）：

```text
$ grep -rnE "ReActAgent|sys_prompt|agentscope\.service|AgentService|fastapi" app/worksplice/agent_seam.py
（无命中）

$ grep -rn "from agentscope" app/worksplice/agent_seam.py
agent_seam.py:  from agentscope import credential as cred
agent_seam.py:  from agentscope.model import OllamaChatModel
agent_seam.py:  from agentscope.agent import Agent
agent_seam.py:  from agentscope.message import UserMsg
```

`agentscope` 只在**真实适配器被实例化时**才 import：脚本化替身路径的测试完全不需要它。

### 1.1 不使用框架 Agent Service、不声称框架提供身份/持久化/E2EE

- 唤醒与落库全部由本应用自己的表承载（`message` / `round_outcome` / `agent_config`），
  框架的 `Msg` 只承载「这一轮的输入与输出」。
- `agent_seam.py` 的模块文档与 `README.md` 都逐条写明这三件不可声明的能力归本应用。
- HTTP 面**没有** agent 令牌、没有「以某成员名义调用」的路径（见 §4 证据）。

## 2. 门禁结果（全量）

```text
$ .venv/bin/python -m pytest -q
113 passed, 1 skipped in 79.41s          # skipped = 默认跳过的真模型冒烟测试

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 25 files already formatted

$ .venv/bin/python -m mypy                 # 配置在仓库根 mypy.ini
Success: no issues found in 22 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
errors 0 files 22
```

### 2.1 用例分布（#2 的 36 条语义未变）

| 文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `test_empty_workspace.py` | 5 | 空工作台、数据目录可复制即备份（#2 沿用） |
| `test_create_channel.py` | 9 | 新建频道、校验失败整体不创建（#2 沿用） |
| `test_send_message.py` | 12 | 发送、seq 单调与并发唯一、append-only、重启一致（#2 沿用） |
| `test_channel_read.py` | 10 | 每频道已读位置、未读计数、跨重启一致（#2 沿用） |
| `test_create_agent.py` | 8 | Agent 入口、校验原子性、配置落库、勾选加入频道、无 HTTP 冒充路径 |
| `test_remote_data_flow.py` | 10 | 数据面告知清单、未确认不生效、缺凭证可读、幂等、零密钥、跨重启 |
| `test_mentions.py` | 13 | 最左最长、引号形式、不存在名字、邮箱式与紧贴中文不误判、快照落库与跨重启 |
| `test_agent_round.py` | 32 | 正向闭环、唤醒面、回应判断、状态点、失败隔离、合并、恢复、不可变性、未读 |
| `test_event_stream.py` | 14 | 补拉、四类事件、按 seq 追加、轮换帧、状态点快照、跨频道不泄漏 |
| `test_agent_smoke.py` | 1 | 真模型连通性（**默认跳过**，本地手动运行） |
| 合计 | **114**（113 跑 + 1 skip） | |

## 3. 逐条验收（#3 全部验收项）

### 3.1 新建 Agent 与模型配置

| 验收项 | 证据 |
| --- | --- |
| 入口接受名称 + 角色 + 模型标识 + 系统提示 | `test_create_agent_returns_name_role_model_and_prompt` |
| 名称或模型留空 → 整体不创建、不留半成品、明确报错 | `test_empty_name_leaves_no_half_written_agent`、`test_empty_model_leaves_no_half_written_agent`（工作台快照里 owner 仍是唯一成员行） |
| 模型与系统提示落库为**配置数据**；换模型不改协作域代码 | `test_agent_config_is_not_a_code_constant`（重启后仍是用户填的那个模型）、`test_create_agent_returns_name_role_model_and_prompt`（落库内容 == 用户输入） |
| 创建后出现在新建频道表单的初始成员勾选清单 | `test_agent_appears_in_the_channel_member_picker_and_joins_when_checked`；浏览器：`memberPickerHint` = 「勾选的 Agent 会成为该频道的显式成员…」（旧票的过期提示已删） |
| 缺模型或缺凭证 → 状态点「未启用」；被 @ 时 reason **可读** | `test_unavailable_agent_reason_is_readable`（reason 含 `dashscope` 与 `DASHSCOPE_API_KEY`）、`test_unconfirmed_remote_agent_reason_is_readable`、`test_missing_credential_is_reported_readably` |

### 3.2 远端数据流告知（必须在首次远端调用之前）

| 验收项 | 证据 |
| --- | --- |
| 告知数据面：消息正文、发送者显示名、频道名称与描述、被点名成员显示名 + 接收方 | `test_data_flow_statement_lists_everything_that_leaves_the_machine`；浏览器截图 `remote-notice.png`：正文逐项列出四类数据 + 「接收方是远端服务，不在你的机器上」 |
| 用户确认后才生效，未确认不生效 | `test_remote_agent_is_offline_until_the_notice_is_acknowledged`、`test_acknowledgement_turns_the_agent_on`、`test_agent_in_channel_is_woken_only_after_consent`（**未确认时适配层一次都没被调用**：`harness["seam"].call_count == 0`） |
| 按 provider 一次确认、幂等 | `test_acknowledgement_is_per_provider_and_idempotent`（重复确认不覆盖首次时间） |
| 文案**不得**声称 E2EE | `test_data_flow_statement_lists_everything_that_leaves_the_machine`（`end_to_end_encrypted is False`）；界面文案：「本机存储不做应用层端到端加密，也不会声称端到端加密」 |
| 后端默认只绑 loopback | `test_data_flow_statement_lists_everything_that_leaves_the_machine`（`loopback_only is True`）+ `__main__.py` 的 loopback 闸门（#2 已交付） |
| 库里不存任何密钥 | `test_consent_and_registry_hold_no_secrets`（`remote_consent` 只有 `provider` / `acknowledged_at` 两列；Agent 载荷里没有 `api_key`/`secret`/`token` 等字段） |

凭证来源（人类裁定 A）：**只从进程环境变量读**，收敛在 `agent_seam.read_api_key()` 一个窄入口。
`test_missing_credential_is_reported_readably` 用 `monkeypatch.delenv` 显式拿掉变量，断言 reason 可读。

### 3.3 AgentScope 采用范围

| 验收项 | 证据 |
| --- | --- |
| 开工第一步核对 stable 并 pin | §1（`agentscope==2.0.9`） |
| 只用 2.x API；不照搬 1.x 签名 | §1 的 `grep`（零 `ReActAgent` / `sys_prompt`）；`app/pyproject.toml` 的 pin 与注释 |
| **不使用**框架 Agent Service | §1 的 `grep`（零 `agentscope.service` / `AgentService`）；`agent_seam.py` 模块文档 |
| **不声称**框架提供身份/持久化/E2EE | `agent_seam.py` 与 `README.md` 的「Agent 的采用范围」小节逐条写明 |
| 一轮 ↔ 一条 `Msg`；`AgentEvent` 只驱动状态点，不做消息正本 | `RoundOutcome.__post_init__` 在接口契约上拒绝「一轮 >1 条消息」；`AGENT_COLUMNS`/消息表是该应用自己的正本（`test_reply_from_a_different_backend_instance_sees_the_same_stream`） |

框架侧「一轮↔一条 Msg」的依据：`Agent.reply()` 的签名与文档（消费全部流式事件后返回**一个** `Msg`），
已在装好的 2.0.9 上核对（§1）。

### 3.4 边界：Agent 只有结构身份

| 验收项 | 证据 |
| --- | --- |
| HTTP 面自始至终属于人类面；Agent 动作不经 HTTP | `test_agent_is_not_an_http_caller`（没有改配置 / 删 Agent 的端点；GET/PUT/PATCH/DELETE 全 404） |
| 首版不引入 agent 令牌，不存在「以某成员名义调 HTTP」的路径 | `test_agent_is_not_an_http_caller`（`{"kind": "agent"}` 这类自述被 400 拒；没有端点接受「我以某成员身份」） |
| 人类面不能往频道里塞 agent/system 消息 | 同一条用例（`kind` 被 `extra="forbid"` 拒） |

### 3.5 提及 → 唤醒 → 自判回应

| 验收项 | 证据 |
| --- | --- |
| 最左最长匹配 | `test_leftmost_longest_wins`（`小明` vs `小明王`） |
| 支持 `@"带空格名字"` 引号形式 | `test_quoted_form_supports_names_with_spaces`（`@"前端 小明"` 命中带空格的成员；无引号形式另命中真实存在的 `小明`） |
| 不存在同名成员不误判 | `test_unknown_name_is_not_a_mention`、`test_at_sign_without_a_member_is_not_a_mention`（`@小明丰` 命中前缀不算）、`test_quoted_form_with_unknown_name_is_not_a_mention` |
| 邮箱式文本不误判 | `test_email_like_text_is_not_a_mention`（`user@example.com`、`user@小明`、`/var/lib@小明`、`v1.2@小明`、`user_name@小明`、`user-name@小明`、`user+tag@小明`）+ `test_mention_right_after_chinese_text_is_not_a_mention`（`你好@小明` 不算） |
| 解析结果作为**快照随消息落库**；不事后重新解析 | `test_snapshot_is_persisted_and_not_re_resolved_later`（再建同名成员后重读仍是原快照）、`test_snapshot_survives_backend_restart`、`test_mention_snapshot_is_not_forgeable_by_the_client` |
| 前端复用同一解析结果 | `app.js` 的 `messageText()` 只读 `message.mention_snapshot`（`start`/`end`），不自己实现解析；浏览器证据见 §4 |
| 触发点唯一：落库之后才唤醒；落库失败不唤醒 | `test_rejected_message_never_wakes_anyone`（超长正文 400 + 频道 404 两条路径都是零唤醒） |
| 唤醒信号只带标识、不带正文；Agent 经同一读接口自行读取 | `test_the_agent_reads_messages_through_the_same_read_path`（适配层拿到的是它自己从库里读到的待读消息） |
| 唤醒集合 = 该频道全部 agent 成员，排除作者自己 | `test_all_channel_agents_are_woken_and_the_author_is_excluded`、`test_agent_author_is_excluded_from_the_wake_set`、`test_agent_reply_does_not_wake_itself` |
| 穿透留给 #③，本票不实现不验收 | `test_all_channel_agents_are_woken_and_the_author_is_excluded`（不在频道的 Agent 不被唤醒） |
| 被个人提及 → 必须回应；被点名不回应记 `failed`（不静默） | `test_mention_is_a_certain_signal_so_ignoring_it_is_failed`（failed + 状态点 error + 无半条消息，reason 保留原自判理由） |
| 明确无关 → `ignored`（正常协议选择，不失败、不伪造确认） | `test_clearly_irrelevant_message_is_ignored_not_failed`（`ignored` + 状态点 online + 流里只有那一条人类消息） |
| 每轮落一条轮次结果（含 base_seq / 可读 reason / 起止时间 / 两个布尔字段） | `test_positive_loop_human_message_to_agent_reply`；`test_round_outcome_distinguishes_replied_ignored_and_failed` |
| 一轮至多产出 1 条频道消息 | `RoundOutcome.__post_init__`（>1 条直接抛）；落库路径只有 `_commit_reply` 一条（`test_positive_loop_human_message_to_agent_reply` 断言 seq 为 1、2） |

### 3.6 实时呈现与断线补拉

| 验收项 | 证据 |
| --- | --- |
| 服务端推送事件流（SSE 级别），至少覆盖新消息 / 状态点变化 / 「正在回复」，轮次结果为可选事件 | `test_event_stream_replays_messages_after_the_cursor`、`test_status_event_reports_error_after_a_failed_round`、`test_typing_indicator_is_emitted_while_a_round_is_in_flight`、`test_event_stream_replays_round_outcomes` |
| 前端订阅后按 seq 追加渲染；Agent 完整回复不刷新即出现 | 浏览器 §4：DOM `#messageList .msg` 从 `[1]` → `[1,2]`，`performance.getEntriesByType('navigation').length` 未变（零导航），MutationObserver 记录 3 次追加 |
| 出现重复或错序即失败 | `test_replay_does_not_duplicate_or_reorder`（seq 严格递增、无重复、恰为 1…N）；浏览器证据 `dupes: false` |
| 断线时显示连接状态，重连带 `last_seq` 补拉 | 浏览器 §4：停后端 → `online=false`、`connBanner` 可见、发送禁用；重启后端 → 自动重连、`seqs` 补齐成 `1…8` 无重复 |
| 人类消息此后也走同一推送路径 | `test_events_of_other_channels_do_not_leak` + §4（人类消息经 HTTP 落库后由事件流推给另一个页面） |
| Agent 回复沿用人类消息渲染（方形头像色位、作者名、Agent capsule、时间、`--accent-deep` 作者名） | 浏览器 §4：Agent 消息节点为 `author=小明`、`agentTag=Agent`、`avatar av-0`；`app.css` 的 `.msg-author.is-agent { color: var(--accent-deep) }` 与 `.msg-tag` |
| Agent 正文里的 `@名字` 同样以 `--accent-soft` 底 + `--accent-deep` 字高亮 | 浏览器 §4：`mention` 节点存在并带 `data-member`；`app.css` 的 `.msg-text .mention { color: var(--accent-deep); background: var(--accent-soft) }` |
| 四态状态点只表达后端事实（空闲 / 正在回复 / 出错 / 未启用） | `test_status_event_reports_offline_for_a_disabled_agent`（未确认 → offline）、`test_status_event_reports_error_after_a_failed_round`（failed → error）、`test_typing_indicator_is_emitted_while_a_round_is_in_flight`（in-flight → working，收口 → online）、`test_ignored_round_returns_to_online` |
| `working` 脉冲在 `prefers-reduced-motion: reduce` 下关闭 | `app.css`：`@media (prefers-reduced-motion: reduce) { .presence.working { animation: none; } }` 与 `.typing-line::before` 同款 |
| 不从任何会话存活推导状态 | 状态点的四个来源分别是：`availability()`（配置 + 凭证 + 告知）、`_in_flight`（运行态）、库里最近一轮的 `round_outcome`。**没有**任何输入来自会话/连接存活 |
| token 级打字机不在本票 | 事件流只有「正在回复」指示，没有 token 增量帧；`RoundMessage` 只承载完整正文 |

**收尾发现的缺陷与修复**（浏览器实跑才发现，已加测试与固定）：

1. **事件流会阻塞优雅关停**：两条长连接不结束，进程在 graceful shutdown 里等它们到超时，
   表现为「进程还在、监听套接字已没、新请求全拒」。修法：进程加 `shutting_down` 信号让事件流退出
   + 单连接在 `WORKSPLICE_SSE_ROTATE_SECONDS`（默认 55 秒）后主动轮换。
   证据：`/tmp/e2e_restart_check.py` → `EXITED cleanly in 0.2s rc=-15`（修前是「STILL RUNNING」）。
2. **例行轮换被前端误报成掉线**：每分钟闪一次「后端不可达」并短暂禁用发送。修法：服务端轮换前先发
   `event: rotate` 命名帧，前端据此区分「服务端请我重连」与「后端挂了」。
   证据：`test_rotate_frame_lets_the_client_tell_a_rotation_from_an_outage`；
   浏览器把轮换周期设成 3 秒、观察 12 秒 → `offlineSeen: 0`。
3. **后端短暂不可达后前端永不恢复**：`loadWorkspace` 失败后没有重试。
   修法：加唯一的本地重连定时器 + 事件流 `onerror` 触发自动重连。
   证据：§4 的停机 / 重启往返。

### 3.7 Agent 消息计入未读

| 验收项 | 证据 |
| --- | --- |
| 作者非 owner 的 agent 消息计入其它频道未读（`kind ∈ {human, agent}`） | `test_agent_messages_count_toward_unread`（human + agent 各一条 = 2） |
| `system` 事件消息不计数 | `test_system_messages_do_not_count_toward_unread`（本票**不产生** system 消息，因此这条断言的是「Agent 回复只算一条」：若实现多写一条 system 通知，数字会变 3） |
| 正在查看时到达立即已读 | `test_agent_reply_is_read_immediately_when_viewing`；浏览器 §4（正在查看频道 1 时经 HTTP 发消息，徽标始终为空） |
| 重启后未读仍正确 | `test_unread_including_agent_replies_survives_restart`；浏览器 §4（刷新后徽标仍是 2） |

### 3.8 预算字段（只落字段，策略在 #③）

| 验收项 | 证据 |
| --- | --- |
| 轮次结果携带「是否消耗预算」「是否被合并」 | `test_positive_loop_human_message_to_agent_reply`（两字段都在载荷里） |
| 本票**不实现也不验收**预算策略 | `orchestration.py` 模块文档明写；`test_consumed_budget_is_false_for_human_epoch`、`test_non_agent_author_never_causes_a_budget_consuming_jump`（事实记录，不是恒定值） |
| 单 Agent 场景触发不了 agent→agent 跳 | `test_agent_reply_does_not_wake_itself`（回复后唤醒集为空） |
| 预算重置/耗尽/上界 ≤3/并发合并 → #③ | `test_merged_flag_is_recorded_for_rounds_that_were_not_merged`（字段是事实）；`merged` 只有 in-flight 期间的重复唤醒才为真 |

### 3.9 失败隔离与恢复

| 验收项 | 证据 |
| --- | --- |
| 模型失败/超时 → 该轮 `failed`、状态点 `--error`、**不写半条消息** | `test_a_failed_round_writes_no_half_message`（流里只有那一条人类消息） |
| 一个 Agent 失败不影响其它 Agent 轮次与人类发送路径 | `test_failure_isolated_from_other_agents_and_from_human_sending`（失败的那位 error、另一位 online 且 replied；后续人类发送 seq=3） |
| 进行中的轮次在重启后不被自动重放 | `test_in_flight_round_is_not_replayed_after_restart`（真在 in-flight 时重启：新后端零待跑轮次、状态点 online、无轮次结果） |
| 重启后消息/成员/轮次结果完整 | `test_round_outcomes_and_agent_replies_survive_restart` |

### 3.10 测试范式

| 验收项 | 证据 |
| --- | --- |
| 在 AgentScope 之前保留**一个薄适配接口**（成员 + 待读新消息 → 零或多条回复 + 一个轮次结果） | `app/worksplice/agent_seam.py` 的 `AgentSeam.run_round(agent, messages, *, mentioned, target_id) -> RoundOutcome` |
| 测试注入脚本化替身，固定四种行为 | `app/tests/scripted.py` 的 `ScriptedAgentSeam`（`reply` / `ignore` / `fail` / `delay`）+ `ManualScheduler` |
| 真模型连通性另有一条默认跳过、本地手动运行的冒烟测试 | `test_agent_smoke.py`（默认 skip；本轮全量结果里的那 1 个 skip 就是它） |
| 测试不联网、零成本 | 全量跑完无任何出网调用；真实适配器只在被实例化时才 import agentscope |
| 正向闭环（含事件流推送） | `test_positive_loop_human_message_to_agent_reply` + `test_event_stream_replays_messages_after_the_cursor` |
| 唤醒面（含排除作者） | §3.5 三条用例 |
| 提及（引号 / 最左最长 / 不存在名字 / 邮箱式不误判） | §3.5 五条用例 |
| 回应判断（点名不应 → failed；无关 → ignored） | §3.5 两条用例 |
| agent 回复计入未读 / 到达即已读 / 重启后不变 | §3.7 四条用例 |
| 失败隔离 | §3.9 两条用例 |
| 未启用（有可读原因） | §3.1 与 §3.2 |
| 恢复（消息/成员/轮次结果完整、进行中不重放） | §3.9 |
| 不可变性 | `test_round_outcome_is_append_only`、`test_agent_reply_body_is_immutable`、`test_messages_are_append_only`（#2 沿用） |
| 前端契约（不可达禁发并明确提示、重连按 last_seq 补齐不重复不错序） | §4 浏览器 + `test_replay_does_not_duplicate_or_reorder` |
| 每条测试独立临时数据目录，不触碰用户真实数据目录 | `conftest.py` 的 `data_dir` 夹具（`tmp_path`）；全量跑完 `~/Worksplice` 未被创建/修改 |
| 只断言经 HTTP 面与事件流可观察的行为 | `grep` 零命中直连库/内部结构（见下）；不因换持久化引擎或换模型提供商而失败（替身经接口注入） |

```text
$ grep -rnE "sqlite3|raw_connection|INSERT INTO|SELECT |worksplice\.db|from worksplice import" app/tests/*.py
exit=1        # 无命中：测试不连数据库、不读表结构

$ grep -rn "import agentscope" app/tests/*.py
（无命中：测试不 import 框架）
```

### 3.11 前端诚实性

| 验收项 | 证据 |
| --- | --- |
| 后端不可达时禁止提交并给明确提示 | 浏览器 §4：点击发送 + Enter 两条路径都被拦住（`sendDisabled: true`、toast 「后端不可达，发送已禁止；消息没有被送出。」、`pendingOrFailed: 0`、输入框内容保留） |
| 不做本地消息队列、不静默吞消息 | 同一条：`#messageList .msg.pending` / `.failed` 数量为 0（没送出就不留假象） |
| 重连后恢复 | §4 停机 → 重启往返：`online` 回到 true、`connBanner` 隐藏、`seqs` 补齐 |
| 原型四处前端假象被替换 | `localStorage` 只记「上次打开的频道 id」；提及解析在后端（前端只消费快照）；状态点由后端事实推导（无写死字符串）；无 `AGENTS`/`MESSAGES`/`LOGS` 种子常量 |
| `worksplice-app.html` 保持原样、不被本票修改 | `git status --short worksplice-design-system` 零命中；`git diff 5015383...HEAD --stat -- worksplice-design-system` 空 |

## 4. 以独立临时数据目录实跑（后端 + 真实浏览器）

数据目录 `/tmp/wsps-e2e-data`（**不是** `~/Worksplice`），后端 `worksplice` 真实 uvicorn 进程，
浏览器为真实 Chromium（ego-browser）。Agent 行为由脚本化替身固定 ⇒ 零付费模型调用。

```text
$ E2E_DATA=/tmp/wsps-e2e-data .venv/bin/python /tmp/e2e_backend.py
data dir: /tmp/wsps-e2e-data
```

### 4.1 空库 → 建 Agent → 建频道 → @它 → 回复不刷新即出现

```text
浏览器（真实 Chromium，ego-browser）：
$ ego-browser nodejs  # 打开 http://127.0.0.1:8799/studio/
# 新建频道后打开它、在 composer 输入「@小明 帮我看下配色对比度」并点发送

{ messages: [
    { seq: '1', author: 'Owner', agentTag: null, mention: ['@小明'],
      text: '@小明 帮我看下配色对比度' },                      ← --accent-soft 底高亮的提及
    { seq: '2', author: '小明', agentTag: 'Agent', mention: [],
      text: '收到（1）' } ],                                    ← 方形头像 av-0 + Agent capsule
  memberRows: [ ['小小明', 'presence online'], ['远远山', 'presence offline'] ],
  dock: [ '小小明前端工程师ollama:qwen2.5空闲',
          '远远山远端审阅dashscope:qwen-max未启用尚未确认远端数据流告知：dashscope 会把消息正文…' ] }
```

「不刷新即出现」的机器判据（Agent 回复由后端唤醒编排落库、经 SSE 推给页面）：

```text
{ before: [1],  posted human seq: 3,
  after:  { seqs: ['1','2','3','4'], navigations: 0, renders: 3 } }
```

`navigations: 0` = 期间零导航（没有刷新）；`renders: 3` = MutationObserver 记录到消息流被追加。
其中 `3` 是另一个客户端经 HTTP 发的，`4` 是 Agent 的完整回复 —— 两者都由事件流送达同一个页面。

截图：`/tmp/wsps-evidence/agent-reply.png`、`/tmp/wsps-evidence/remote-notice.png`。

### 4.2 远端数据流告知（未确认不生效）

```text
打开「新建 Agent」→ provider 下拉切到 DashScope：
{ noticeHidden: false,
  title: '启用 DashScope（远端） 前需要你确认一次',
  body: '启用后，以下数据会离开本机并发送给该模型提供商：消息正文、发送者显示名、频道名称与描述、
         被点名成员的显示名。接收方是远端服务，不在你的机器上。',
  hint: '本机存储不做应用层端到端加密，也不会声称端到端加密；后端默认只监听 loopback。' }

不确认就创建 → agents: [小... 'offline', false]   ← 未确认 = 未启用
确认后 →       consents: [{provider: 'dashscope', acknowledged_at: '…Z'}]
               agents: [ … 'offline' → 状态点随后端事实更新 ]
```

本机 provider（Ollama）无需确认：`test_local_model_needs_no_remote_notice`，且界面切到 Ollama 时
告知块隐藏、提示为「本机模型：不离开本机，不需要数据流确认」。

### 4.3 未读徽标（跨刷新 / 跨重启）

```text
在频道 2 里、频道 1 未打开：
{ channels: ['#设计系统重构2', '#第二个频道'], badges: [['unread-ch_64b44c29c912', '2']] }

刷新页面后：
{ channels: ['#设计系统重构2', '#第二个频道'], badges: [['unread-ch_64b44c29c912', '2']] }   ← 未读仍正确

打开频道 1（正在查看）后：badges: []                                              ← 立即已读
正在查看时经 HTTP 发一条新消息（Agent 也回）：badges: []，seqs: [1,2,3,4]         ← 到达即已读
```

### 4.4 现场失效与自动恢复

```text
$ kill -9 <backend pid>          # 无 FIN，最像进程崩溃 / 断网
浏览器（页面未刷新，轮换周期设为 3 秒）：
{ online: false, streamState: 'reconnecting',
  connBannerHidden: false,
  streamBannerHidden: true, streamText: '',
  sendDisabled: true }

后端不可达时提交（点击 + Enter 两条路径）：
{ sendDisabled: true,
  toasts: ['后端不可达，发送已禁止；消息没有被送出。'],
  pendingOrFailed: 0, inputStillHasText: true }

$ 重启后端；期间用另一个 HTTP 客户端写入两条消息（seq 5..8）
重连后（页面未刷新）：
{ online: true, streamState: 'open', connBannerHidden: true,
  seqs: ['1','2','3','4','5','6','7','8'],
  bodies: [ … '断线期间错过的第一条', '收到（5）', '断线期间错过的第二条 @小明', '收到（7）' ],
  dupes: false, sendDisabled: false }
```

即：**带 `last_seq` 补拉，补齐且不重复不错序**，且没有本地队列、没有静默吞消息。

### 4.5 例行轮换不误报为不可达

```text
$ WORKSPLICE_SSE_ROTATE_SECONDS=3 <后端>       # 把 55 秒的轮换周期压到 3 秒以便观察
浏览器观察 12 秒（经历 3 次轮换）：
{ offlineSeen: 0, online: true, streamState: 'open',
  connBannerHidden: true, streamBannerHidden: true, dupes: false }
```

### 4.6 优雅关停不再卡在长连接上

```text
$ .venv/bin/python /tmp/e2e_restart_check.py
up: 200
stream open, frames seen: 2
EXITED cleanly in 0.2s rc=-15
```

（修前是「STILL RUNNING after 20.0s — hung」，且进程残留、监听套接字消失。）

## 5. 红 → 绿证据（红证据在隔离副本取得）

做法：`git archive HEAD | tar -x -C /tmp/red3` + 复制工作树的 `app/worksplice` 与 `app/tests`
到副本，在副本里注入基线缺陷，跑同一条测试观察变红；工作树未被改动
（`git status --short` 在取证前后一致）。

本票与 #2 的差别：**红证据必须覆盖新行为**，因此 15 组注入是逐条对着新验收项设计的。

| # | 注入的基线缺陷 | 判红用例数 | 代表判红用例 |
| --- | --- | --- | --- |
| A | 消息落库失败也照样唤醒（触发点不唯一） | 2 | `test_rejected_message_never_wakes_anyone`、`test_empty_body_is_rejected_and_not_stored` |
| B | 唤醒面不排除作者（自己唤醒自己） | 2 | `test_second_wake_while_a_round_is_in_flight_is_merged`、`test_typing_indicator_is_emitted_while_a_round_is_in_flight` |
| C | 被点名却不回应不按 `failed` 处理（静默） | 1 | `test_mention_is_a_certain_signal_so_ignoring_it_is_failed` |
| D | 失败轮次也落库回复（不看轮次结论） | 9 | `test_a_failed_round_writes_no_half_message`、`test_failure_isolated_from_other_agents_and_from_human_sending` |
| E | 提及不区分词边界（`@小明丰` 命中 `小明`） | 1 | `test_at_sign_without_a_member_is_not_a_mention` |
| F | 提及忽略左边界（`user@example.com` 误判） | 3 | `test_email_like_text_is_not_a_mention`、`test_mention_right_after_chinese_text_is_not_a_mention` |
| G | 提及快照不落库 | 9 | `test_snapshot_is_persisted_and_not_re_resolved_later`、`test_leftmost_longest_wins` |
| H | 事件流不按 `after_seq` 补拉（总是全量重发） | 1 | `test_event_stream_replays_messages_after_the_cursor` |
| I | 轮次结果不落盘（HTTP/事件流都读不到） | 22 | `test_round_outcome_is_append_only`、`test_round_outcome_distinguishes_replied_ignored_and_failed` |
| J | in-flight 期间重复唤醒静默丢弃（不落 `merged`） | 1 | `test_second_wake_while_a_round_is_in_flight_is_merged` |
| K | 消费游标在 `failed` 时也推进（失败不再重读） | 1 | `test_failed_round_does_not_advance_the_cursor_so_the_next_wake_rereads` |
| L | 缺凭证也照常调用远端（未启用判定失效） | 2 | `test_missing_credential_is_reported_readably`、`test_unavailable_agent_reason_is_readable` |
| M | 未确认远端告知也照常启用 | 2 | `test_agent_in_channel_is_woken_only_after_consent`、`test_unconfirmed_remote_agent_reason_is_readable` |
| N | Agent 创建不做校验（名称/模型可留空） | 2 | `test_empty_name_leaves_no_half_written_agent`、`test_model_needs_a_known_provider_prefix` |
| O | 状态点收口后仍停在 `working`（in-flight 不清） | 10 | `test_typing_indicator_is_emitted_while_a_round_is_in_flight`、`test_ignored_round_returns_to_online` |

**15 组注入全部判红、零组漏网**，且判红用例全部是 HTTP/SSE 黑盒用例（没有任何一条依赖直连库）。

两个特例值得记录，因为它们各自暴露过一次真实的判别力缺口（本轮已补）：

- **A** 最初注入「把唤醒挪到落库之前」，但那个改动同时改了 seq 语义，导致几十条用例连带变红，
  判别不出「唤醒点」这一件事。改成「except 分支里补一次唤醒」后，判红收敛到两条恰到好处的用例
  （两条都要先有落库失败）。
- **K**（消费游标）最初**零判红**：当时没有任何用例区分「failed 不推进游标」与「failed 也推进游标」。
  补了 `test_failed_round_does_not_advance_the_cursor_so_the_next_wake_rereads`（下一轮的输入必须
  同时含「失败那轮没吃掉的那条」）与 `test_ignored_round_advances_the_cursor_so_it_is_not_reread`
  （对照面）之后才判红。**这是本轮测试唯一被红证据补出来的空洞。**

```text
$ grep -c "没有任何用例判红" /tmp/red3_full.txt
0
```

## 6. code-review 双轴 findings 与处置

按 `/Users/apple/.pi/agent/skills/code-review/SKILL.md` 跑双轴（两个并行 sub-agent，各自独立上下文），
固定点 `5015383`（#2 合并后的 main）。

### 6.1 Spec 轴

| # | 级别 | Finding | 处置 |
| --- | --- | --- | --- |
| S1 | P1 | `index.html` 的静态提示「Agent 的创建入口在下一票交付」从不隐藏，与 #3「控件在本票变得可用」冲突 | **已修**：改成「勾选的 Agent 会成为该频道的显式成员…」，并让空态文案指向新建入口 |
| S2 | P2 | 越界：把最近 5 条轮次结果渲染进消息流，违反「首版不做可观测页面」；只读端点已授权，UI 未授权 | **已修**：删掉 `#outcomeList` / `renderOutcomes` / `.outcome-*` 与 `state.outcomes`；只读端点与事件流保留（它们是必需测试的取证面） |
| S3 | P2 | 半做：出错态仅由内存推导，重启后状态点回 `online` 而库里最近一轮仍是 `failed` | **已修**：`status_of` 读 `Database.latest_round_status()`（库里的正本）。实测：failed 轮次 + 后端重启 → 状态点仍是 `error`、轮次结果仍是 `failed` |
| S4 | P2 | SSE 例行轮换被前端 `onerror` 判为掉线，每分钟闪「后端不可达」并短暂禁用发送 | **已修**：服务端轮换前先发 `event: rotate`，前端据此不进入掉线分支；新增一条测试 + 浏览器实测（§4.5） |
| S5 | P2 | 「缺模型被 @ 有可读原因」不可达（创建入口要求模型非空），原用例以「未确认远端」替代但标题误导 | **已修**：拆成两条名副其实的用例（缺凭证 / 未确认告知），并把「缺模型不可达」如实写进 docstring |
| S6 | P2 | 「后端不可达时发送被禁止」只有浏览器手跑证据 | **接受为报告项**：本票测试范式是 HTTP/SSE 黑盒，浏览器行为无法进 pytest 断言；证据在 §4.4（两条提交路径 + toast + 零 pending）。已记入本文件与 Answer 的遗留 |
| S7 | Correct | 预算策略与穿透确未实现 | 确认，符合规格 |

### 6.2 Standards 轴

| # | 级别 | Finding | 处置 |
| --- | --- | --- | --- |
| T1 | P2 | 术语漂移：CONTEXT.md 词条是「轮次结果」，新代码写「轮次结论」 | **已修**：`agent_seam.py` 全部改回「轮次结果」 |
| T1b | P2 | 状态点四态词条是 在线/正在干活/出错/离线，UI 用 空闲/正在回复/未启用 | **接受为报告项（有据）**：#3 正文与 #1 §3 的状态点定义**逐字**用的是「未启用 / 正在回复 / 出错」，且票面把三态名与 token 绑在一起（`--online` 空闲 / `--working` 正在回复 / `--error` 出错 / `--offline` 未启用）。以**票面**为准，README 与 UI 保持一致；CONTEXT.md 的旧词条与票面冲突，属规格正本的用词未同步，不在本票授权范围内改 |
| T2 | P2 | Duplicated Code：agent 列清单在 `db.py` 抄三份 | **已修**：抽 `AGENT_COLUMNS`（与 #2 的 `CHANNEL_SELECT_SQL` 同先例）；三处 FROM/WHERE 各自保留（起点与过滤条件本就不同） |
| T3 | P2 | Speculative Generality（零消费）：`STATUS_HEALTHY`/`STATUS_UNAVAILABLE`、`owner_visibility`/`owner_unread_guard`、`EVENT_TYPES`、`EventBus.subscriber_count`、`state.destinations` | **已修**：全部删除 |
| T4 | P2 | Mysterious Name + 死规则：`.conn-banner.steam-state` 拼错，且元素没有该 class ⇒ 规则永不命中 | **已修**：连同那条死规则一起删除 |
| T5 | P2 | `SSE_KEEPALIVE_SECONDS` 无消费（保活实际是 0.5 秒轮询） | **已修**：改名 `SSE_POLL_SECONDS` 并在推送循环里真正使用 |
| T6 | P2 | 可能的 Primitive Obsession / Data Clumps：agent/message 裸 dict 穿过多层 | **接受为报告项**：本票的适配接口契约本身就是「输入 dict（成员 + 消息）」——那是**故意**的窄接缝，为的是让测试替身与协作域解耦（票面要求）。改成 dataclass 会把框架无关性变薄，且属重构而非本票交付。记入遗留 |
| T7 | P2 | 可能的 Feature Envy：`service._agent_view` 调 orchestrator 拼视图 | **接受为报告项**：状态点与可用性由编排器拥有（它是唯一知道 in-flight 与可用性判据的地方），服务层只做拼装。这正是「成员面唯一入口」的形态 |

修复后复跑全部门禁（§2）全绿，并在真实浏览器复验 §4 全部场景。

## 7. 取舍与遗留

### 7.1 取舍

1. **凭证只从进程环境变量读**（人类裁定 A）：库里零密钥、界面零密钥录入。判定收敛在
   `agent_seam.read_api_key()` 一个窄 seam，以后换来源（钥匙串等）只改这一处。
2. **`provider:model` 单一字符串**：provider 前缀取自 AgentScope 的 credential 白名单，
   非白名单整体不创建。这样一个字段同时决定凭证环境变量与「是否远端（要不要数据流确认）」，
   不必新增第二列再维护两者的一致性。
3. **按 provider 一次确认**（裁定 2A）：依据 #1 §8 原文「首次启用远端模型前必须显式告知并取得
   **一次**确认」。未确认时该 Agent 状态点 `offline`、被 @ 时轮次 `failed` + 可读 reason、
   绝不发起远端调用 —— 由 `test_agent_in_channel_is_woken_only_after_consent` 的
   `seam.call_count == 0` 断言。
4. **`round_outcome` 与消息同为 append-only**：轮次结果是「当时为什么没说话」的事实，
   与消息一样不接受事后改写（库级触发器 + 无 HTTP 端点）。
5. **事件流每频道一条 + 补拉由后端读库完成**：`after_seq` 就是频道内消息 seq，正是规格
   「重连带 `last_seq` 补拉」的读法；补拉与实时推送共用同一个下界，前端不必自己缓存事件。
6. **单连接轮换（默认 55 秒）**：解决「长连接阻塞优雅关停」这个实跑才暴露的缺陷；
   轮换前先发 `event: rotate` 命名帧，前端据此刻意不误报掉线。
7. **事件流用例跑真实 HTTP 服务**：`TestClient` 会读完整个响应体，无法逐帧读持续响应。
   为不牺牲取证强度，事件流用例起一个真 uvicorn（端口 0），用 `httpx` 逐帧读 —— 这也正是
   浏览器 `EventSource` 的行为（并显式 `trust_env=False`，不让环境代理劫持本地请求）。

### 7.2 遗留（明确未做 / 明确未验收）

1. **回应预算策略（#③）**：重置 / 耗尽 / 硬上界 ≤3 / 并发合并的完整语义。本票只落
   `consumed_budget` 与 `merged` 两个**事实字段**，不实现任何压制。
2. **穿透（#③）**：`@` 一个未加入该频道的 Agent 也被唤醒。本票唤醒面只含频道成员。
3. **可观测页面**：轮次结果只有只读端点与事件流，**没有** UI（并且刻意删掉了 review 指出的那个）。
4. **token 级打字机流式**：不在首版；「实时」的验收定义是「不刷新看到完整回复」。
5. **浏览器行为的自动化断言**：不可达禁发、重连补齐、状态点脉冲在减少动效下关闭，这三条
   目前是真实浏览器手跑证据（§4），未进 pytest。原因：本票测试范式是 HTTP/SSE 黑盒。
6. **`agent`/`message` 的裸 dict 契约**与 `_agent_view` 的 Feature Envy：见 §6.2 的 T6/T7，
   是有意的窄接缝，不以本票重构。
7. **CONTEXT.md 状态点词条与票面用词不一致**（§6.2 T1b）：以票面为准；词条同步不在本票范围。
