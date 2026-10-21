## Summary

Closes #219

AgentScope 的 ReAct 循环里**一轮 = `state.context` 尾部那一条累积 assistant `Msg`**：每次模型调用的块都
`append` 进去（`AgentState.append_context`）。而落库正文的提取只读了**最终 `Msg`**（最后一次模型调用的
快照），于是「正文 → 工具调用 → 正文」这种形态里，前面那段正文静默消失——任务线程的进展简报没落库，
用户只看到一句**引用不存在内容**的收尾话（issue 的 `seq=4`）。

```diff
 _outcome_from_reply(reply, …)
-  text = _msg_text(reply)                       # 只读最后一次模型调用
+  text = _round_body(state.context, reply)      # 读这一轮**累积** assistant 消息
                                                
 _round_body(state_context, reply)
-  # 旧：没有这一层
+  message = 在 state.context 里找 id == reply.id 且 role == "assistant" 的那条
+            └─ 找不到（单段场景 / 上下文不可用）→ 回退 reply 自己
+  segments = _text_segments(message)            # 只认 getattr(block, "text", None) 且是 str
+            └─ hint / thinking / tool_call / tool_result / data 自然落空（它们没有 text 属性）
+  return "\n\n".join(段.strip() for 段 in segments if 段.strip())
+            └─ 一段都没有 → ""  ⇒ 既有「空正文 → ignored」路径不变
```

一轮仍然只落**一条**消息（`RoundOutcome.__post_init__` 的两个校验一字未动）；变的是这条消息的正文
取哪几段、按什么顺序、怎么分隔。

```diff
 app/alibot/
 └── agent_seam.py   # _text_segments()「哪些块算正文」的唯一判定
                     # _msg_text()      单条消息的纯文本（team 成员结论 / 事件路径仍可能需要它）
-                    # _msg_text()      既是一轮的正本，也是单条消息的工具（两种语义挤在一个函数里）
+                    # _round_body()    一轮的正文：累积消息 → 按块序 → 段间空行
                     # _outcome_from_reply(state_context=…)  两条收口分支（正常 / exceed_max_iters）共用
 app/tests/
-└── （无）
+└── test_round_body_assembly.py  # 9 条：顺序 + 整串、白位面 trim、单段不变、动作-only、
+                                 # 空正文 ignored、声明沉默、exceed_max_iters、队长轮次、纯 thinking
```

两个要写进 review 的决定：

1. **按 `reply.id` 找，不假设位置。** 不读 `state.context[-1]`——位置不是契约。按 id 找既修好累积场景，
   又在「最终 `Msg` 就是上下文尾部」的单段场景**回退到同一条消息**，于是单段行为逐字不变。
2. **队长轮次不额外传上下文。** `_final_reply_message()` 返回的是队长 `state.context` 里那条累积消息
   **本身**，提取器退回读 `reply` 时读到的就是全部正文段。review 时删掉了一版多余的 `leader_context`
   拷贝（两轴都判它零行为增益）。

## Evidence

- **Before**（换成 `git show main:app/alibot/agent_seam.py` 的基线原件，md5 `5fea630e9cd291af67bbb5cad7197710`）：

  ```console
  $ cd app && ../.venv/bin/python -m pytest tests/test_round_body_assembly.py -q
  F.F..FF..                                                                [100%]
  E  AssertionError: 工具调用前那段正文被丢掉了，实际落库正文='上面就是这轮的实质进展简报，已提交审阅。'
  E  assert '【首版进展简报】这是我这一轮的实质正文。' in '上面就是这轮的实质进展简报，已提交审阅。'
  4 failed, 5 passed
  ```

  语义失败，不是 import 错误：用例跑到了断言、读到了真实落库正文。修复前的规律是「只留最后一次模型调用
  的块」；白位面用例还额外暴露「连段间分隔都没有，且保留了后段的首尾空白」。

  **After**（本分支，md5 `050538ae99f406414cbc0080c45b5656`）：

  ```console
  $ cd app && ../.venv/bin/python -m pytest tests/test_round_body_assembly.py -q
  9 passed in 4.25s
  ```

- **不回归**（逐条「为什么没变」写在验收文档 §3）：

  ```console
  tests/test_silence_tool_channel.py    5 passed    # 声明沉默仍 silent + messages == []
  tests/test_agent_event_stream.py      7 passed    # body == "保留正文" 单段逐字不变
  tests/test_agent_round.py            35 passed
  tests/test_approval_seam.py          12 passed    # 队长 messages[0].body == "leader final"
  tests/test_silent_outcome.py          9 passed
  ```

- **门禁**：`scripts/check.sh` exit 0 —— pytest `1062 passed, 10 skipped`、ruff check 全过、
  `ruff format --check` `272 files already formatted`、mypy `115 source files` 无问题、pyright
  `0 errors, 0 warnings`、node glob `247 pass / 0 fail`。
- 逐条判据与读数：`app/specs/issue-219-acceptance.md`。

## Merge Danger

**Door:** two-way

行为变更集中在一个模块级私有提取器与它的一条收口路径；回滚只需 revert 这两个 commit，无迁移、无数据
改写。`SCHEMA_VERSION` 保持 23，未加列、未加表、未改任何既有行。

**Blast Radius:** seam

- **共享通道**：这一处提取被**每个人格、每个目标类型**共用（单 Agent / 队长 / team 成员 / DM / 频道 /
  任务线程都经过 `_outcome_from_reply`）。这是本票跑全量门禁、而不是窄档的原因。
- **只放宽、不收紧**：新实现只会让落库正文**变多**（把这一轮前面被丢掉的正文档补回来）；判定顺序、
  状态词、消息条数、`silent` / `ignored` / `failed` 的边界一律未变。
- **一处可见微调**：判据 3 要求「每段先 `strip()`」，泛化到单段就是首尾空白不再原样保留。既有用例里没有
  带空白边界的单段正文，因此没有断言转红；新增的白位面用例把新口径钉住。若将来有调用方依赖首尾空白
  （缩进敏感的代码块正文），需要按新票单开分支。
- **历史不回溯**（ADR-0001 append-only）：库里已经落错的消息保持原样，本票只保证新轮次不再丢正文。
- **框架依赖**：队长轮次「不需要额外上下文」这一点依赖 AgentScope 2.0.10 的 `_final_reply_message`
  语义（返回上下文中那条累积消息本身）。升级框架需重核对 `docs/agents/agentscope-2-audit.md` 的 D3 行；
  已在调用点注释写明。
