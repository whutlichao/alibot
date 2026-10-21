# 验收记录：#219 一轮落库正文 = 这一轮全部用户可见正文段，而不是最后一次模型调用

**票型**：实施票（缺陷修复 + 回归测试）｜**门禁**：G-impl（`scripts/check.sh` 全量；合并由 coordinator 执行）
**Ticket**：[GitHub issue #219](https://github.com/whutlichao/alibot/issues/219)
**base**：`c84eac8`（main @ 派活时 HEAD）｜**branch**：`issue-219-round-body`
**worktree**：`/Users/apple/orca/workspaces/alibot/issue-219-round-body`
**代码提交**：`c7b146d`（实现 + 用例）、`6269a20`（双轴 review 收口）
**采集时间**：2026-10-21｜**环境**：macOS（aarch64）、Python 3.11.13（worktree `.venv` 符号链接到主工作区
`.venv`）、Node v25.0.0、AgentScope 2.0.10（pin 未动）
**改动面**：`app/alibot/agent_seam.py`（+120 / −22）、新增 `app/tests/test_round_body_assembly.py`（348 行，9 条用例）、
本文件与 `app/specs/issue-219-pr-body.md`

---

## 0. 前置 skill 实跑

前置 skill：`/Users/apple/.agents/skills/implement/SKILL.md`（含其指向的 `tdd`；收尾的 `code-review` 与
`pr` 见 §6 / §7）。本票的实际映射：

| implement 步骤 | 本票实际做的事 |
| --- | --- |
| 读票面 | `gh issue view 219`（标题：「fix(seam): 同一轮里工具调用前后的正文被丢弃——落库只剩最后一句收尾话」），逐条抄成 §1 的判据表 |
| tdd 垂直切片 | 先写第 1 条（两段正文都落库 → 红）→ 实现 `_round_body` → 绿；再补 exceed_max_iters / 单段不变 / 空正文 / 沉默 / 队长轮次，逐条红→绿；review 后补空白、ignored、纯 thinking 三条 |
| 单文件用例 + 类型检查 | 每轮 `cd app && ../.venv/bin/python -m pytest tests/test_round_body_assembly.py`；代码定稿后跑一次 `scripts/check.sh` 全量 |
| 双轴 review | Standards + Spec 两个并行子代理，结论与处置见 §6 |
| 提交 | 两个 commit（实现 / review 收口），未合并、未推 main |

**没有用 `scripts/red-evidence.sh`**：它按 `git archive` 导 `app/alibot/studio` + `app/tests`，只覆盖 Studio
用例族；本票是 Python 用例，因此按票面「等价手工法」取证（§2.2）。

---

## 1. 判据 → 落点 → 命令与读数

| # | 判据（票面） | 落点 | 命令 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 正文正本是本轮**累积** assistant 消息，不是 `reply` 本身；按 `reply.id` 在 `state.context` 里找 id 相同且 role 为 assistant 的那条，找不到回退 `reply` | `agent_seam.py` 新 `_round_assistant_message()` | `python -m pytest tests/test_round_body_assembly.py -k model_order` | `1 passed`；落库正文 `'【首版进展简报】…\n\n上面就是这轮的实质进展简报…'`（修复前只有后半句，见 §2.2） |
| 2 | 只拼 `text` 块（`getattr(block, "text", None)` 且是 str），跳过 hint / thinking / tool_call / tool_result / data | 新 `_text_segments()`（「哪些块算正文」的唯一判定） | `-k thinking` | `1 passed`：同一轮里 `ThinkingBlock` + `TextBlock` ⇒ `body == LAST_BODY`，thinking 不进正文 |
| 3 | 顺序 = 块序；段间 `"\n\n"`；每段先 `strip()`、空段跳过；不产生连续空行或首尾空白 | 新 `_round_body()` | `-k "model_order or stripped"` | `2 passed`；白位面用例送 `'\n\n  A  \n'` + `'\n   B\n\n'` ⇒ 落库 `'A\n\nB'`，断言 `not body.startswith((" ", "\n"))`、`"\n\n\n" not in body` |
| 4 | 没有任何 text 块 ⇒ `""`（空正文 → `ignored` 的路径不变） | `_round_body` 末尾 `join` 空序列返回 `""`；`_outcome_from_reply` 的 `if not text` | `-k empty_round` | `1 passed`：频道轮次（无任务动作通道）空正文 ⇒ `status == "ignored"`、`messages == []` |
| 5 | `exceed_max_iters` 分支走同一个提取器 | `_outcome_from_reply` 的 exceed 分支改调 `_round_body` | `-k exceed_max_iters` | `1 passed`：`finished_reason == "exceed_max_iters"` 且两段正文都在、A 在 B 前 |
| 6 | **不动**：`RoundOutcome.__post_init__` 校验、silent 互斥、`_outcome_from_reply` 判定顺序、`declare_silence` 语义、`_final_reply_message`、schema、前端、消息种类 | `git diff main...HEAD -- app/alibot/db.py app/alibot/orchestration.py app/alibot/studio/` | 同左 | **空输出**（三个路径零改动）；`RoundOutcome` / `declare_silence` / `_final_reply_message` 在 diff 里 0 行改动（`git diff -U0 \| grep` 无命中） |
| 7 | 保留 `_msg_text(msg)` 作为单条消息的取文本工具，并在 docstring 写清谁用哪个 | `_msg_text` docstring 重写（两条 bullet：一轮的正文走 `_round_body`；单条消息的取文本留给 team 成员结论 / 事件路径） | `grep -n "def _msg_text" -A 12 app/alibot/agent_seam.py` | 分工两段可见；`_msg_text` 仍无分隔相连（语义未变），`_round_body` 不再反向依赖它 |
| 约束 | 不碰 `db.py` / `orchestration.py` / `studio/**`；不改 `RoundOutcome` 字段或校验；不新增 status；不跑 `ruff format` | `git status --porcelain` / `git diff --stat main...HEAD` | 同左 | 只有 2 个文件改动，全在 Ownership 内；`SEAM_STATUSES` 未改 |

### 1.1 两条路径各取哪个上下文（为什么不额外传参）

- **单 Agent 轮次**：`run_round_stream` 在 `finally` 里把 `instance.state.context` 记进 `round_context`
  （`nonlocal`，**保存之前**取，只存 list 引用不拷贝），收口时传给 `_outcome_from_reply(state_context=…)`。
- **队长轮次**：**不传**。`_final_reply_message()` 返回的是队长 `state.context` 里那条**累积** assistant
  消息本身（不是最后一次模型调用的快照），`_round_body` 退回读 `reply` 时读到的就是这一轮的全部正文段。
  队长用例 `-k leader_round` 在「不传 state_context」下通过即这条事实的读数。
- 这条第二行是 review 收口时**删掉**的：初版给队长另加了一个 `leader_context` 拷贝，Standards 与 Spec
  两轴都判它零行为增益（`Spec` 轴实测「强制 `state_context=None` 后队长用例仍绿」），已删除。

---

## 2. 先红后绿

### 2.1 红：手工法（只换产品文件，不动测试）

票面要求的等价手工法。本次做了两轮，第二轮（最终 9 条用例）读数如下。

**存档与替换**（全程只动 `app/alibot/agent_seam.py` 一个文件）：

```console
$ git status --porcelain                      # 替换前
 M app/alibot/agent_seam.py
 M app/tests/test_round_body_assembly.py

$ md5 -q app/alibot/agent_seam.py             # 最终实现
050538ae99f406414cbc0080c45b5656
$ git show main:app/alibot/agent_seam.py > app/alibot/agent_seam.py
$ md5 -q app/alibot/agent_seam.py             # 现在 == 基线原件
5fea630e9cd291af67bbb5cad7197710
$ git show main:app/alibot/agent_seam.py | md5 -q
5fea630e9cd291af67bbb5cad7197710
$ git diff --stat main -- app/alibot/agent_seam.py
（空输出 = 产品文件与 main 逐字一致）
```

**红读数原文**（`cd app && ../.venv/bin/python -m pytest tests/test_round_body_assembly.py -q`）：

```console
F.F..FF..                                                                [100%]
=================================== FAILURES ===================================
________ test_a_round_keeps_every_user_visible_paragraph_in_model_order ________
>       assert FIRST_BODY in body, f"工具调用前那段正文被丢掉了，实际落库正文={body!r}"
E       AssertionError: 工具调用前那段正文被丢掉了，实际落库正文='上面就是这轮的实质进展简报，已提交审阅。'
E       assert '【首版进展简报】这是我这一轮的实质正文。' in '上面就是这轮的实质进展简报，已提交审阅。'

_____________ test_paragraphs_are_stripped_and_blank_ones_dropped ______________
>       assert body == f"{FIRST_BODY}\n\n{LAST_BODY}", repr(body)
E       AssertionError: '\n   上面就是这轮的实质进展简报，已提交审阅。\n\n'
E       assert '\n   上面就是这轮的...简报，已提交审阅。' == '【首版进展简报】这是我这...实质进展简报，已提交审阅。'

_______________ test_the_exceed_max_iters_branch_also_keeps_the_earlier_paragraph _______________
>       assert FIRST_BODY in body, f"迭代上限分支丢了前面的正文，实际落库正文={body!r}"
E       AssertionError: 迭代上限分支丢了前面的正文，实际落库正文='上面就是这轮的实质进展简报，已提交审阅。'

_______________ test_the_leader_round_also_keeps_every_paragraph _______________
E       AssertionError: assert '【首版进展简报】这是我这...实质进展简报，已提交审阅。' == '【首版进展简报】这是我这一轮的实质正文。\n\n上面就是这轮的实质进展简报，已提交审阅。'

=========================== short test summary info ============================
FAILED tests/test_round_body_assembly.py::test_a_round_keeps_every_user_visible_paragraph_in_model_order
FAILED tests/test_round_body_assembly.py::test_paragraphs_are_stripped_and_blank_ones_dropped
FAILED tests/test_round_body_assembly.py::test_the_exceed_max_iters_branch_also_keeps_the_earlier_paragraph
FAILED tests/test_round_body_assembly.py::test_the_leader_round_also_keeps_every_paragraph
4 failed, 5 passed
```

**这是语义失败，不是 import 错误**：用例跑到了断言、读到了真实落库正文——旧实现只把**最后一次模型调用**
的块写进 `RoundMessage.body`（`'上面就是这轮的实质进展简报，已提交审阅。'`），前面那段实质正文
（`'【首版进展简报】这是我这一轮的实质正文。'`）静默消失。另两条断言形态说明得更细：

- 白位面用例里旧实现连**分隔**都没有（`'\n   上面就是…\n\n'` 既丢了前段、又保留了后段的首尾空白）；
- 队长用例的 `+`/`-` 差分直接显示「旧 = 只有后半句」。

**恢复**：

```console
$ cp /tmp/agent_seam_final_219.py app/alibot/agent_seam.py
$ md5 -q app/alibot/agent_seam.py
050538ae99f406414cbc0080c45b5656          # 与替换前一致
$ git status --porcelain
 M app/alibot/agent_seam.py
 M app/tests/test_round_body_assembly.py   # 与替换前一致，测试文件全程未动
```

### 2.2 红：第一轮（实现前，`git stash push app/alibot/agent_seam.py`）

实现前也取过一轮，用来确认「先红后绿」的顺序没有被事后编造：

```console
$ git status --porcelain
?? app/tests/test_round_body_assembly.py
$ git stash push app/alibot/agent_seam.py
$ git status --porcelain
?? app/tests/test_round_body_assembly.py          # 只 stash 了产品文件
$ cd app && ../.venv/bin/python -m pytest tests/test_round_body_assembly.py -q
F...F                                                                   [100%]
FAILED tests/test_round_body_assembly.py::test_a_round_keeps_every_user_visible_paragraph_in_model_order
FAILED tests/test_round_body_assembly.py::test_the_exceed_max_iters_branch_also_keeps_the_earlier_paragraph
E       AssertionError: 工具调用前那段正文被丢掉了，实际落库正文='上面就是这轮的实质进展简报，已提交审阅。'
$ git stash pop
$ git status --porcelain
 M app/alibot/agent_seam.py
?? app/tests/test_round_body_assembly.py
```

（`git stash list` 里原本就有一条别人的 `stash@{0}`，与本次无关；本票那条 pop 后已消失。）

### 2.3 绿：修复后

```console
$ cd app && ../.venv/bin/python -m pytest tests/test_round_body_assembly.py -q
.........                                                                [100%]
9 passed in 4.25s
```

九条用例：两段正文顺序 + 整串相等、白位面 trim、单段逐字不变、只有工具块无正文（动作-only）、
空正文 + 无声明 `ignored`、声明沉默零条消息、exceed_max_iters、队长轮次、纯 thinking 不进正文。

---

## 3. 不回归：专门复核（逐条写「为什么没变」）

| 复核点 | 命令 | 读数 | 为什么没变 |
| --- | --- | --- | --- |
| 声明沉默仍 `status="silent"` 且 `messages == []` | `python -m pytest tests/test_silence_tool_channel.py` | `5 passed`；其中 `test_a_declared_silence_becomes_a_silent_outcome_without_any_message` 断言 `outcome.messages == []` | `silence_declarations` 分支在 `_outcome_from_reply` 里**先于**正文判定，且它是 `return RoundOutcome(status="silent")`——根本不把 `text` 放进 `RoundOutcome`。拼接只改 `text` 的来源，拼接后仍走同一条 `if not text` / `silent` 判定顺序。新用例 `-k declared_silence` 还专门钉住「模型在工具前后都写了正文也不复活」 |
| 单段场景 `body == "保留正文"` 逐字不变 | `python -m pytest tests/test_agent_event_stream.py` | `7 passed`；`test_orphan_and_unknown_events_are_dropped_with_debug` 的 `outcome.messages[0].body == "保留正文"` 通过 | 该用例里 `Msg(id=<随机>, content=[TextBlock(text="保留正文")], role="assistant")` 由假 `reply_stream` 直接 yield，`state.context` 里没有 id 相同的那条 → `_round_assistant_message` **回退到 `reply`**，`_text_segments` 拿到单个 `"保留正文"`、`strip()` 后原样 → `"\n\n".join(["保留正文"]) == "保留正文"`。单段不产生任何分隔符 |
| 空正文 + 无声明仍 `ignored` | `python -m pytest tests/test_round_body_assembly.py -k empty_round`；`tests/test_silence_tool_channel.py` 的 `test_an_empty_body_without_a_declaration_is_still_ignored` | `1 passed` / `5 passed` | `_round_body` 在没有任何 `text` 块时返回 `""`；`_outcome_from_reply` 的判据从 `not text.strip()` 改成 `not text`（等价：`_round_body` 已保证无首尾空白），空正文仍落 `ignored`，`messages` 仍为零条 |
| 其余 seam 面 | `pytest tests/test_agent_round.py tests/test_approval_seam.py tests/test_silent_outcome.py` | `35 passed` / `12 passed` / `9 passed` | 这三份用例走 HTTP 面与队长/审批路径；`messages[0].body` 的单段断言（`"leader final"`）同样回退读 `reply`，逐字不变 |

---

## 4. 全量门禁 `scripts/check.sh`

```console
$ scripts/check.sh
exit=0

== pytest（全量）
1062 passed, 10 skipped in 565.75s (0:09:25)

== ruff check
All checks passed!

== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
272 files already formatted

== mypy（仓库根）
Success: no issues found in 115 source files

== pyright
0 errors, 0 warnings, 0 informations

== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 247
ℹ pass 247
ℹ fail 0

门禁全绿
```

（**没有跑 `ruff format`**；只跑了脚本里的 `--check`。`272 files` 是**代码定稿、两份 specs 尚未写入**
时的读数；写完本文件与 PR body 后重跑同一命令 = `274 files already formatted`——该检查把 `app/**/*.md`
也计入文件数，两份新增 specs 正好 +2，仍全过。）

---

## 5. 双轴 code-review（`/Users/apple/.agents/skills/code-review/SKILL.md`）

对 `git diff main...HEAD` 跑 Standards + Spec 两个并行子代理。原文结论与处置：

### 5.1 Standards 轴

| 发现 | 类型 | 处置 |
| --- | --- | --- |
| diff 用「最后一跳 / 第一跳 / 第二跳 / 两跳」指一次**模型调用**，而 `CONTEXT.md` 的 **跳 (Jump)** 正本是「一条 Agent 消息投递了至少一个唤醒」——同名异义 | **硬**（措辞纪律） | **已修**（`6269a20`）：`agent_seam.py` 与测试里全部改为「模型调用」；`grep -n "跳" app/tests/test_round_body_assembly.py` 现在只命中「跳过空段」这类正常动词 |
| **Speculative Generality**：队长分支的 `leader_context` 空转（`reply` 本就来自同一个 `state.context`，按 id 必然找回同一条） | 判断（最该处理） | **已删**（`6269a20`）：`finally` 那 5 行与 `extend` 全删，改为「不传、回退读 `reply`」，并在调用点写明理由 |
| 两种「把协程内的值带出来」写法不一致（`round_state: dict` 存引用 vs `leader_context: list` 做 `.extend` 拷贝） | 判断 | **已统一**：只剩一种形态——`nonlocal round_context: list[Any]`，`finally` 里赋 list 引用（不拷贝），不再有可变容器当出参 |
| **Duplicated Code**：`_msg_text` 与 `_round_body` 各遍历一遍 content 取 `text`；改后 `_msg_text` 只剩 `_round_body` 的 str 分支一个调用点（近 Middle Man） | 判断 | **已修**：抽出 `_text_segments(message)` 作为唯一判定，`_msg_text` = `"".join(...)`、`_round_body` 在同一判定上 trim + 空行相连；调用方向变成单向（两个调用者都依赖 `_text_segments`） |
| 文档与实现不符：`_outcome_from_reply` 称不传时「行为逐字不变」，实际每段会 `strip()` | 判断 | **已修 docstring + 补用例**：改成「判定顺序、状态词与条数不变；唯一差别是按判据 3 每段先 strip」，并新增白位面用例钉住 strip/空段（旧实现下同样红） |
| 建议 `_round_body` 改名 `_round_message_body`（`round` 易读成「轮次持正文」） | 判断 | **不修**：票面本身就写 `_round_body(agent_state_or_context, reply)` 作为示例名，且 docstring 首句已定义「一轮落库的正文」。改名会让验收文档、PR body 与票面示例脱节，收益不抵成本 |
| 新测试是否越层 / 新开接缝 | 判断 | 越层＝**否**（只用公开 `run_round`/`run_round_stream`、不 import 私有名、只断言 `RoundOutcome`）；新开接缝＝边缘但**成立**：票面明确要求「在真 seam 上复现」，本文件承载该豁免理由（§5.3） |

### 5.2 Spec 轴

| 发现 | 类型 | 处置 |
| --- | --- | --- |
| 规则 1「按 reply.id 找、找不到回退」是否逐字实现 | — | **是**（`_round_assistant_message`，条件 `id == reply_id and role == "assistant"`，否则 `return reply`） |
| 规则 3 是否会产生连续空行 / 首尾空白 | — | **否**（逐段 strip → 过滤空段 → `"\n\n".join`，join 只在段间插分隔） |
| 规则 6 的「不动」清单 | — | **无残留**：判定顺序原样；两处 `_msg_text(reply)` 均已换 `_round_body`；`__post_init__` / `declare_silence` / `_final_reply_message` / schema 未动 |
| 是否有「A 在 B 之前」的顺序断言 | — | **是**（`body.index(FIRST_BODY) < body.index(LAST_BODY)`，用例 1 另有整串相等） |
| **队长 `leader_context` 越出 Target 且冗余** | scope creep | **已删**（同 Standards 轴第 2 条）；删除后队长用例仍绿 |
| **验收文档与 PR body 缺失** | 缺失 | **已补**：本文件 + `app/specs/issue-219-pr-body.md`（含 `Closes #219`） |
| **判据 4「空正文 + 无声明仍 ignored」无真 seam 用例** | 缺失 | **已补**：`test_an_empty_round_without_a_declaration_is_still_ignored`（频道轮次，无任务动作通道，落 `ignored`） |
| **「纯 thinking 不产生正文」无用例** | 缺失 | **已补**：`test_a_thinking_block_never_becomes_body`（脚本化模型，理由见用例 docstring：OpenAI 兼容传输会静默丢掉 ThinkingBlock，stub provider 送不进去） |
| **`_msg_text` docstring 没点明 team / 事件路径** | 缺失 | **已补**：docstring 改成两条 bullet，写明「单条消息的取文本场景 = team 轮次的成员结论、事件路径上的文本摘要」 |
| 称「不传时行为逐字不变」但 str 分支会 strip | 实现口径 | **已改文案 + 补用例**（见 §5.1 末两行） |

**两轴都没有 P1 遗留**：硬违规 1 条（措辞）、缺失 3 条（文档 2 份 + 两条判据用例）、scope creep 1 条
（`leader_context`），全部在 `6269a20` 里处置完毕；「不修」的唯一一条是命名建议（理由见上）。

### 5.3 为什么必须是进程内 stub provider（票据豁免记录）

`docs/agents/review-rules.md` 判断题 2 要求「不新开接缝」，只有票面明确要求核实某项能力时才加进程内
stub，且验收文档要写清理由。本票的理由：

1. 票面的验收判据 1 **逐字**要求「新增一个进程内 stub provider 用例，两次模型跳（原文措辞）」——被测的
   能力正是「AgentScope 的 ReAct 循环在一轮里把多次模型调用的块 append 进同一条累积 `Msg`」；
2. 这件事在 HTTP 面**不可见**：`ScriptedAgentSeam` 直接返回 `RoundOutcome`，绕过了整个正文提取环节，
   用它写这条用例等于自证（会永远绿）；
3. 形态照抄 `test_silence_tool_channel.py` 的 `_StubProviderHandler` + `stub_provider` fixture（本机
   `127.0.0.1:0`、零成本、零外发），不新开测试框架、不引入第二套装置。仓库里已有四份同族 stub
   （`test_agent_seam_client_lifecycle` / `test_silence_tool_channel` / `test_web_assembly` / 本文件），
   各自按需扩展字段，没有共用基类——本票沿用这个既有体例，而不是顺手做一次跨票的装置重构。

---

## 6. 改动点（`git diff --stat main...HEAD`）

```text
 app/alibot/agent_seam.py              | 120 +++++++++---
 app/tests/test_round_body_assembly.py | 348 ++++++++++++++++++++++++++++++++++
 2 files changed, 446 insertions(+), 22 deletions(-)
```

`agent_seam.py` 的四处：

1. 新增 `_text_segments(message)`：**哪些块算正文**的唯一判定（只认 `text` 且是 `str`；hint / thinking /
   tool_call / tool_result / data 都没有 `text` 属性，自然落空）。
2. `_msg_text(message)` 语义回到「单条消息的纯文本」= `"".join(_text_segments(...))`，docstring 写明
   谁用哪个。
3. 新增 `_round_assistant_message(state_context, reply)` + `_round_body(state_context, reply)`：按
   `reply.id` 找回累积消息；逐段 strip、跳空段、`"\n\n"` 相连；无正文返回 `""`。
4. `_outcome_from_reply(...)` 增加 `state_context` 关键字参数，两条收口分支（正常完成 / exceed_max_iters）
   都改调 `_round_body`；单 Agent 轮次在 `finally` 里用 `nonlocal round_context` 把 `state.context`
   带出来。

**未改**：`RoundOutcome` 的字段与两条校验、`SEAM_STATUSES`、`declare_silence` 的语义、
`_final_reply_message`、库 schema、前端、消息种类、唤醒与预算。

---

## 7. 遗留与取舍

1. **历史消息不回溯**（ADR-0001 append-only）：生产库里已经落错的那几条消息（issue 正文的 `seq=4`）保持
   原样，本票只保证**新**轮次不再丢正文。
2. **单段正文会被 trim**：判据 3 要求「每段先 `strip()`」，泛化到单段就是「首尾空白不再原样保留」。这是一
   次**可见的**行为微调（不是回归）：既有用例都没有带空白边界的单段正文，所以没有既有断言转红；新增的
   白位面用例把新口径钉住了。若将来有调用方依赖首尾空白（例如缩进敏感的代码块正文），需要在 `_round_body`
   里对「单段」另开分支——但那与「一轮 = 多段拼接」的统一口径冲突，届时按新票处理。
3. **`_round_body` 仍在逐块遍历**：一轮的块数最多是 `max_iters × 每跳块数`（默认 30 × 少量），没有做
   增量或缓存；与旧实现同一个数量级，不引入状态。
4. **`text` 属性之外的正文形态**（`DataBlock` 的音频、`ToolResultBlock` 的富输出）不进正文——这是票面
   明确要求的「纯文本正文」，未做扩展。
5. **队长轮次不需要额外上下文**这一事实依赖 AgentScope 2.0.10 的 `_final_reply_message` 语义（它返回
   上下文中那条累积消息本身）。将来升级框架要重核对这一行与审计矩阵 D3 行；已在调用点注释里写明。

---

## Answer

**Status**：`implemented_pending_review`（实现 + 用例 + 红绿证据 + 双轴 review 处置 + 全量门禁全绿；
PR 待创建，号与 body 路径在 §8 回填）

**判据 1（红→绿）**：红读数原文贴在 §2.2（`git stash push app/alibot/agent_seam.py`，只 stash 产品文件，
stash 前后 `git status --porcelain` 都存档）与 §2.1（最终 9 条用例，用 `git show main:…` 换回基线原件，
md5 `5fea630e9cd291af67bbb5cad7197710` ↔ 实现 `050538ae99f406414cbc0080c45b5656`，恢复后核对一致）。
红读数是**语义失败**：`AssertionError: 工具调用前那段正文被丢掉了，实际落库正文='上面就是这轮的实质进展
简报，已提交审阅。'`，不是 import 错误。绿读数：`9 passed in 4.25s`。

**判据 2（不回归）**：§3 四行逐条写明「为什么没变」——silent 分支在正文判定**之前** return、单段场景
`_round_assistant_message` 回退 `reply` 后 `join(["保留正文"])` 原样、空正文仍返回 `""`、HTTP 面的单段断言
（`"leader final"`）同样回退路径不变。

**判据 3（专门复核）**：`test_silence_tool_channel.py` 5 passed（含「零条 messages」断言）、
`test_agent_event_stream.py` 7 passed（含 `body == "保留正文"`）、`test_agent_round.py` 35 passed、
`test_approval_seam.py` 12 passed、`test_silent_outcome.py` 9 passed。

**判据 4（全量门禁）**：§4，`scripts/check.sh` exit 0 —— pytest `1062 passed, 10 skipped`、
ruff check 全过、`ruff format --check` `272 files already formatted`、mypy `115 source files` 无问题、
pyright `0 errors, 0 warnings`、node glob `247 pass / 0 fail`。

**判据 5（双轴 review）**：§5 两轴逐条列发现与处置。Standards 硬违规 1 条（「跳」的措辞）已修；
Spec 缺失 3 条（两份文档 + 两条判据用例）已补；`leader_context` scope creep 已删；唯一「不修」的是
`_round_body` → `_round_message_body` 的建议（票面示例名，理由见 §5.1）。**无 P1 遗留**。

**判据 6（PR）**：见 §8。

**遗留**：§7 五条（历史不回溯、单段 trim 的可见微调、无增量缓存、非 text 正文形态不纳入、队长语义依赖
AgentScope 2.0.10 的 `_final_reply_message`，升级需重核对）。

---

## 8. PR

- 分支：`issue-219-round-body`（base `main`）
- 提交：`c7b146d`（实现 + 用例）、`6269a20`（review 收口）
- PR body 文件：`app/specs/issue-219-pr-body.md`
- PR：见下方回填（创建后补号与本文件链接）
