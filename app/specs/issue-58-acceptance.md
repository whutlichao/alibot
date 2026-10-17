# #58 验收取证：结构化沉默信号 `silent` + `RoundOutcome` 互斥校验

票面正本：GitHub issue #58（Part of #35）。判据来源：`app/specs/issue-35-spec.md` 的 D0-b / D0-d /
D3（落点-b）与 Testing Decisions；契约正本：`docs/adr/0008-structured-silence-is-a-distinct-round-outcome.md`；
迁移纪律：`docs/adr/0015-schema-migration-is-explicit-and-forward-only.md`。

- 分支：`issue-58-impl`（从 `alibot-budget-silence/integration` 的 `7867d26` 切出）
- 提交：`042ca52` feat(seam)（代码 + 测试）→ `docs(specs)`（本文件 + ADR-0008 最小事实修正）
  → `docs(seam)`（提交号回填 + 行为枚举补「声明沉默」）
- 基线：`SCHEMA_VERSION` 14（#53 合并后）→ 本票 15；migration 链 `3..15` 连续

## 一、逐条判据 → 落点 → 命令与读数

### 判据 1｜`SEAM_STATUSES` 第四个词 `silent`，与既有三词平级；结构化声明沉默时不产出待落库回复消息

- 落点：`app/alibot/agent_seam.py` 的 `SEAM_STATUSES`（四词元组）；`AgentScopeAgentSeam.run_round`
  在 `declare_silence` 工具被调用时收口成 `RoundOutcome(status="silent")`，且**不**构造 `RoundMessage`。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_silent_outcome.py -q
# 11 passed
#   test_silent_is_the_fourth_seam_status_on_par_with_the_other_three
#   test_a_declared_silence_becomes_a_silent_outcome_without_any_message（真实 seam + 本机 stub provider）
```

### 判据 2｜结构化通道**真的存在**、可被 provider 支持，且不假定所有 provider 一致

见本文第二节（实现形态 + 能力核实结论 + 退化路径）。取证用例：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_silence_tool_channel.py -q
# 5 passed
#   test_a_declared_silence_becomes_a_silent_outcome_without_any_message
#   test_a_provider_without_the_tool_transport_degrades_to_the_existing_outcomes
#   test_the_unknown_provider_is_treated_as_unable_to_declare_silence
#   test_a_model_that_does_not_call_the_tool_keeps_the_plain_reply
#   test_an_empty_body_without_a_declaration_is_still_ignored
```

### 判据 3｜`RoundOutcome(status="silent")` 与非空 `messages` 互斥；既有「一轮至多一条 target 消息」保留

- 落点：`RoundOutcome.__post_init__`——`silent` + 非空 `messages` ⇒ `ValueError`；`len(messages) > 1`
  的既有强校验一字未改（两条校验相邻，都在**唯一**的输出类型上）。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_silent_outcome.py -q -k "silent_outcome_carries_no_storable_message or at_most_one_storable_message"
# 2 passed
```

### 判据 4｜`ignored` 语义不变；`silent` 专指结构化声明；`reason` 只承载可读诊断

- 落点：`ignored` 的判定路径与文案未改；`silent` 的来源只有一个——工具调用记录
  （`silence_declarations`），**不从正文/措辞推断**。`reason` 走 `i18n` 的 `seam.silent`，可读、
  不进任何判据分支。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_silent_outcome.py -q
# 11 passed（含四结论参数化：replied=1 条消息 / ignored=0 / failed=0 / silent=0，
#            且各自的成员状态点读数分别为 online/online/error/online）
cd app && ../.venv/bin/python -m pytest tests/test_round_outcome_visibility.py tests/test_backend_copy.py -q
# 全部通过（#18 的摘要行与 #62 的两层文案契约未受影响）
```

- 确定信号下的纪律（CONTEXT.md **回应判断**词条：#58 落地后 `silent` 与 `ignored` 同级）：被点名 /
  被认领唤醒时声明沉默按 `failed` 收口，文案是**新增**的 `reason.mention_silent` /
  `reason.claim_silent`（不复用 `*_ignored` 的正文——那会写出一个不存在的事实）。
  用例：`test_silence_under_a_definite_signal_is_recorded_as_failed`。

### 判据 5｜持久化：`ROUND_STATUSES` 与库级 CHECK 纳入 `silent`；schema 14 → 15 + `MigrationStep(14, 15)`

- 落点：`app/alibot/db.py`（`ROUND_STATUSES` 六词 + `SCHEMA` 的 `round_outcome.status` CHECK）、
  `app/alibot/migrate.py`（`_step_14_to_15` + 注册项）、`app/tests/_migration_history.py`（v15 留档）。
- 命令与读数：

```bash
cd app && ../.venv/bin/python -m pytest tests/test_silent_schema_step.py tests/test_migration_chain_guard.py tests/test_schema_migration.py -q
# 48 passed
```

迁移报告的真实读数（v14 形态库 + 一条既有 `round_outcome` 行）：

```
$ python -m alibot --migrate --data-dir <tmp>/data      # exit 0
迁移：<tmp>/data/alibot.db
源版本：14 → 目标版本：15
本次备份：<tmp>/data-backup-14-to-15-20261017T131017Z

实际执行的步骤（每步改动的数据行数）：
  14 → 15  结构化沉默状态（round_outcome.status 的库级 CHECK 重建，纳入 silent）  改动 0 行
```

### 判据 6｜Studio：`silent` 的准确中文说法（两种语言）且可区分，不是失败、不是 `ignored`、不用 `--error`

- 落点：`app/alibot/studio/app.js` 的 `SUMMARY_STATUSES`（四词）与 `outcomeLabel` / `outcomeNote`
  的既有单一落点；`app/alibot/studio/i18n.js` 的 `outcome.silent.label` / `outcome.silent.note`
  （源语言与第二语言各一处）。
  - 源语言：`模型声明沉默` / `模型经结构化通道声明本轮沉默，没有产出正文（不是失败，也不是自判忽略）`
  - 第二语言：`Silence declared` / `The model declared silence for this round through the structured
    channel and produced no body (not a failure, and not an ignored round)`
- 与规格措辞的对应：票面提到的 `OUTCOME_SUMMARY` 在本仓的实现里就是这张「status → 说法」的唯一落点
  （`SUMMARY_STATUSES` + `outcome.<status>.{label,note}`），本票不新建第二套摘要实现。
- 命令与读数：

```bash
node --test app/tests/silent_ui.test.cjs        # 5 passed
node --test app/tests/*.test.cjs                # 121 passed / 0 failed
```

`silent_ui.test.cjs` 的四条断言：进摘要行（不是 `.msg-outcome` 失败卡）、与 `ignored` 说法不同且
同轮可同列、两种语言都有取值、状态词沿用 `.msg-outcome-line-state` 的 `--faint`（不是 `--error`）。

### 判据 7｜测试覆盖四结论 + 互斥 + 单条 `round_outcome` + 无回复消息 + `reason` 可读 + 库级 CHECK + 词表一致

| 覆盖点 | 用例 | 读数 |
| --- | --- | --- |
| `silent` / `ignored` / `replied` / `failed` 四结论 | `test_each_round_outcome_kind_is_persisted_with_its_message_count`（参数化 4 例） | 4 passed |
| `silent` 与非空 `messages` 被拒 | `test_a_silent_outcome_carries_no_storable_message` | passed |
| `silent` 恰一条 `round_outcome`、无回复 message | 同上参数化用例（`[record["status"]] == ["silent"]`、消息 kinds 只有 `human`） | passed |
| `reason` 可读 | 参数化用例的 `record["reason"].strip()` + `silent_ui.test.cjs` 的 reason 原文断言 | passed |
| 库级 CHECK 接受 `silent` / 拒绝非法词 | `test_the_database_check_accepts_silent_and_refuses_an_unknown_word`、`test_a_v14_library_migrates_to_a_check_that_accepts_silent` | passed |
| `SCHEMA_VERSION` 与前端词表一致 | `test_studio_vocabulary_agrees_with_the_backend_status_words`（前端摘要词集 ⊆ `ROUND_STATUSES`、`SEAM_STATUSES ⊆ ROUND_STATUSES`、两种语言各一处） | passed |

前端门禁用例族与归属：沿用既有 `vm` + `fetch` 替身 + DOM 替身那一族（与 `narration_ui.test.cjs` 同形）。
**新建的 `app/tests/silent_ui.test.cjs` 已被既有门禁命令的 glob 覆盖**，命令仍是
`node --test app/tests/*.test.cjs`（不需要额外追加命令；若将来改成显式文件清单，必须把它列进去）。

### 判据 8｜本文件（逐条验收判据 → 落点 → 命令与读数）

即本文件。ADR-0008 的**最小事实修正**也已落地：正文末尾补一段「事实修正（#58 实施时补，不改上面的
决策内容）」——`6 → 7` 是成文时基线，实施基线为 14、实际 **14 → 15**，并因 ADR-0015 已取代 ADR-0004
的「不做旧数据迁移」而随票携带 `14 → 15`。ADR-0008 的决策内容与 ADR-0004 一字未改，未新建 ADR。

## 二、结构化沉默通道的实现形态与 provider 能力核实

### 2.1 形态：`declare_silence` 工具，落在既有 toolkit 那一层

- 工具：`agent_seam._silence_tools()` 建一个 `FunctionTool(declare_silence, is_read_only=True,
  permission=ALLOW)`——它**只**往本轮内存里的 `silence_declarations` 列表追加一条记录；不写消息、
  不改 `MESSAGE_KINDS`（仍是 `human` / `agent` / `system`）、不碰任务。
- 装配：`_round_toolkit()` 把「任务动作工具（仅任务线程）」与「沉默工具」合成**一个** `Toolkit`。
  频道轮次此前没有 toolkit，现在有了；任务轮次是既有两个工具 + 这一个（`tools` 三个）。
- 收口：`run_round` 只读那份记录，`silence_declarations` 非空 ⇒ `silent` + 零条 `messages`（模型在
  工具之后又写了一段正文也不落库——声明与正文互不推演）。若同一轮还提交了任务动作，按既有的
  「动作-only 算回应」收口成 `replied` + 该动作（否则会静默丢掉一个已校验的结构化动作），
  `reason` 用 `seam.silence_with_action` 把两件事都说清。
- prompt：`_system_prompt(..., structured_silence=True)` 只**补一段说明书**（这个出口怎么用），
  #57 定稿的回应纪律那一段**一字未改**（`test_the_silence_rubric_states_the_mention_and_ignore_boundary`
  仍然通过——那段纪律仍是「恰好一段」）。

### 2.2 为什么不是 `structured_schema`

AgentScope 2.0.9 确有第二条结构化出口 `Agent.reply(..., structured_schema=…)`，但它的实现是**强制**
`tool_choice`：`agentscope/model/_base.py:500` 的
`ToolChoice(mode="generate_structured_output")`，并自带一条 fallback 阶梯
（`forced → auto → no_think`，同文件 457–545）。强制工具选择是各家差异最大的一档：思考模式下会被
拒绝，`OllamaChatModel._format_tools` 直接明写忽略 `tool_choice.mode`
（`agentscope/model/_ollama/_model.py:200`、`223`）。本仓只需要「模型想沉默时有一个正式出口」，
因此选**自愿调用**的普通工具，**不做任何正文解析**、不依赖强制 tool choice。

### 2.3 核实方法（逐 provider，可复跑）

```bash
cd .venv/lib/python3.11/site-packages/agentscope/model
grep -n "tools: list\[dict\] | None = None," */_model.py
```

读数（十一家 ChatModel 的 `_call_api` 都把 `tools` 转发给服务端，且各自实现了 `_format_tools`）：

| provider | 证据（文件:行） |
| --- | --- |
| anthropic（minimax 继承它） | `_anthropic/_model.py:217`（`_call_api`）/ `:221`（`tools`）；`_minimax/_model.py:13` `class MiniMaxChatModel(AnthropicChatModel)` |
| dashscope | `_dashscope/_model.py:196` |
| deepseek | `_deepseek/_model.py:161` |
| gemini | `_gemini/_model.py:262` |
| moonshot | `_moonshot/_model.py:160` |
| ollama | `_ollama/_model.py:136`（`_format_tools` 在 `:193`） |
| openai（chat） | `_openai_chat/_model.py:199` |
| openai-response | `_openai_response/_model.py:188` |
| volcengine | `_volcengine/_model.py:163` |
| xai | `_xai/_model.py:164` |

**结论分两层，两层都不做整体假设：**

1. **传输层**：十一家都携带 `tools`。因此本仓的能力闸门 `PROVIDER_TOOL_TRANSPORT` 今天等于
   `PROVIDERS` 全集（逐 provider 判定，`supports_tool_calling(provider)`）。
2. **模型层**：AgentScope 2.0.9 **没有**给 chat 模型提供工具能力元数据——chat 的 `ModelCard`
   （`agentscope/model/_model_card.py`）只有 input/output 类型，`supports_tools` 只存在于 **realtime**
   的 card（`agentscope/realtime/_model_card.py:59`）。所以「某个具体模型会不会真的调工具」只能由
   **运行时**回答：本仓因此把判据落在「这一轮到底有没有收到这个工具调用」上，而不是静态猜。

### 2.4 不支持时如何退化（结构性，不是侥幸）

- 闸门为假（未知 provider / 将来某家传输层丢掉 `tools`）⇒ 这一轮**根本不挂**沉默工具：
  请求里没有 `tools`、prompt 里没有那段说明书 ⇒ 模型无法声明 ⇒ 结论只可能来自正文，即既有的
  `replied` / `ignored` / `failed`。
- 闸门为真但**模型不调**工具 ⇒ 同样退回既有判据：正文 ⇒ `replied`；空正文且无声明 ⇒ `ignored`。
- 两条退化都有用例（`test_a_provider_without_the_tool_transport_degrades_to_the_existing_outcomes`、
  `test_a_model_that_does_not_call_the_tool_keeps_the_plain_reply`、
  `test_an_empty_body_without_a_declaration_is_still_ignored`），并且第一条同时断言「请求里没有
  `tools` 键、prompt 里没有工具名」——退化是**看得见**的。

## 三、库级 CHECK 迁移的具体形态（14 → 15）

- SQLite 没有 `ALTER TABLE … ADD CONSTRAINT`，而 `status` 的白名单是**表级** CHECK（列级 CHECK 才能
  随 `ADD COLUMN` 补）⇒ 只能重建表。步骤 `_step_14_to_15` 复用既有的 `_rebuild_table`：按**当前**
  `SCHEMA` 的 DDL 建临时表 → 逐列复制既有行 → `DROP` 原名表 → 改名回来（ADR-0015 第三节的
  「建新表 / 搬迁 / 回填」形态）。
- **幂等**：表级 DDL 已点名 `silent` 就跳过（`plan_steps` 本身也不会重跑）。
- **不回填语义、不丢数据**：既有行逐列复制，v14 的库里不可能有 `silent`（那一版没有任何路径能产生它），
  升级只**放宽允许的取值**，不推断历史。用例断言迁移后那条旧行 `status/reason` 逐字不变，
  且第二次 `--migrate` 退出 0、不新建备份、行数不变。
- **索引 / 触发器**随 `DROP TABLE` 消失，由执行器每步之后的 `_align_to_current_schema` 按当前
  `SCHEMA` 补回（既有路径，`test_schema_migration` 的终态断言覆盖）。
- **链连续**：`MigrationStep(14, 15, "结构化沉默状态（round_outcome.status 的库级 CHECK 重建，纳入 silent）", _step_14_to_15)`
  注册在链尾；`test_migration_chain_guard` 用真实子进程 `--migrate` 对 `3..14` 每个源版本断言报告
  步骤表**自身连续**且终点 `== SCHEMA_VERSION`（15）——通过。
- **历史留档**：`app/tests/_migration_history.py` 增 `SOURCE_COMMITS[15] = 'pending'`（落地后由合并
  提交回填）与 `OBJECTS['round_outcome_v15']` / `VERSION_OBJECTS[15]`（照 v13 / v14 的体例：新形态
  单独一个对象名，v15 用它替换 v14 里的 `round_outcome`）。

## 四、门禁读数（wt-58 根 / app 目录）

```bash
cd app && env -u http_proxy -u https_proxy -u no_proxy -u NO_PROXY ../.venv/bin/python -m pytest -q -o addopts=""
# 600 passed, 2 skipped in 302.24s   （两条 skip 是既有的真模型冒烟用例，默认跳过）
cd .. && .venv/bin/ruff check app
# All checks passed!
.venv/bin/ruff format --check app
# 130 files already formatted
.venv/bin/python -m mypy            # 必须用仓库根
# Success: no issues found in 57 source files
node ~/.pi-lens/tools/node_modules/pyright/index.js app/alibot app/tests
# 0 errors, 0 warnings, 0 informations
node --test app/tests/*.test.cjs
# 121 pass / 0 fail（含新建的 silent_ui.test.cjs 5 条）
```

只跑过 `ruff format --check`（未跑 `ruff format app`）；三个**新建**的测试文件单独 format 过。

## 五、触碰的既有断言（两处，均为本票的必然结果，已在 diff 里可见）

1. `app/tests/test_agent_seam_client_lifecycle.py::test_task_tools_return_only_structured_actions`：
   原先断言请求里的工具集**恰好**是 `{set_task_status, transfer_task}`；现在任务轮次也带
   `declare_silence`（沉默是**轮级**事实，不该只在频道轮次可用）。改为断言三件齐全，其余断言不变。
2. `app/tests/test_channel_budget_limit.py::test_the_schema_version_and_the_default_limit_are_what_the_ticket_says`：
   #53 原先钉 `SCHEMA_VERSION == 14`；本票把号推到 15。按 ADR-0015 第三节的既有判据（「必须高于当前
   main」而不是「等于被分配的预留号」）改为 `>= 14`，内置默认仍钉 `3`。

## 六、遗留问题 / 相邻缺陷 / 不确定点（未扩大范围）

- **不是本票能修的概率性问题**：`declare_silence` 是**自愿**调用，模型可能仍然什么都不写（⇒ 既有的
  `ignored`）或仍然写旁白（⇒ `replied`）。结构化通道让「声明沉默」成为可取证的事实，**不保证**模型
  一定用它——这与 #35 D1 的取舍一致（提示改写是概率性改善，不是正确性保证）。
- **模型层能力无法静态枚举**：见 §2.3。若某个具体模型在支持工具的 provider 上仍不产工具调用，它的
  表现是既有的 `replied` / `ignored`（有测试覆盖），不会变成失败。
- **相邻观察（未修，不属本票写面）**：`docs/adr/0008` 首段仍写「遵循 ADR-0004，不提供旧数据迁移」——
  该口径已被 ADR-0015 取代；本票只补了最小事实修正，没有改写那句历史表述（ADR 的处置惯例见
  ADR-0015 末段）。`app/specs/issue-35-spec.md` 里的 `6 → 7`、`基线为 6` 属同一类过时读数，
  规格正文不在本票写面内，未改。
- **不确定点**：前端「准确中文说法」的具体措辞由本票定稿（`模型声明沉默`），若产品侧要改词，
  只需改 `studio/i18n.js` 的两处取值，测试断言的是「两种语言都有、且不等于 `ignored` 的说法」+
  「含「沉默」字样」，不钉整句。
