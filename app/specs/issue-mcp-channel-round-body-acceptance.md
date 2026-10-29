# 验收记录：频道 Agent 收不到 MCP 工具（真因：一轮正文被拆开、只落了第一段）

**票型**：实施票｜**门禁**：G-impl、G-merge｜**测试档**：宽（装配涉及运行时共享收口）
**Ticket**：无 issue（用户反馈直派，`task_a762f70e57c6` / dispatch `ctx_5621d70aeedd`）
**base**：`0472b6c`｜**branch**：`task-a762f70e-behavior-rule`
**PR**：[#338](https://github.com/whutlichao/alibot/pull/338)｜**PR body**：`/tmp/mcp-channel-root-cause-evidence/PR_BODY.md`
**CI**：run 40793453619 success（`1cc3b59`）
**采集时间**：2026-10-29｜**环境**：macOS（Darwin 27.0.0）/ Python 3.11 / AgentScope 2.0.10

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| ①「区分工具装配、模型调用和响应落库」 | 三段各有独立读数 | 装配：`GET /api/mcp-servers` → `runtime.connected=true`、`tools=["mcp__codegraph__codegraph_explore"]`（`live-mcp-servers-20261029.json`）；模型调用：生产 `state.json` 的 `call_00_GhKoZNs5MskE3wFFEHrF7158` 名为 `mcp__codegraph__codegraph_explore` 且有 `tool_result`；落库：同一 `reply_id` 对应两条 assistant 消息 | ✅ |
| ②「构建真实路径可重复反馈循环」 | `app/tests/test_round_body_assembly.py` 第 9–10 节 | 真 `run_round` / `run_round_stream` + 真 `assemble_memory` 装配点 + 脚本化模型；一键 4.5s | ✅ |
| ③「红到绿」 | 基线 worktree `/tmp/mcr-baseline`（`0472b6c` 源码 + 新测试） | 基线 `3 failed, 9 passed`；本分支 `12 passed` | ✅ |
| ④「找到缺陷则在授权范围内修复」 | `app/alibot/agent_seam.py` | 见「改动落点」 | ✅ |
| ⑤「运行 scripts/check.sh」 | 全量 | `1286 passed, 11 skipped`；ruff / mypy(136 files) / pyright(0 errors) / Vitest(24 files, 469 tests) 全绿 → `门禁全绿` | ✅ |
| ⑥「code-review 双轴 Standards + Spec」 | 两个并行子代理 | Standards：2 条硬违规（README 契约未同步、判据编号写错）+ 3 条判断题；Spec：3 条缺失/偏差 | ✅ 已按报告处置，见下 |
| ⑦ 不泄露凭证 / 不改用户真实配置 / 不发送消息 | `git status` | 只改 3 个文件；未写 `~/.alibot`、未发频道消息（生产库 wal 停在 10-28 23:01Z） | ✅ |

## 红证据（隔离基线 `0472b6c`）

```text
$ cd /tmp/mcr-baseline/app && PYTHONPATH=. pytest -c /dev/null -p no:cacheprovider \
    tests/test_round_body_assembly.py -q
FAILED test_a_memory_hint_mid_round_does_not_swallow_the_final_body
FAILED test_a_memory_hint_mid_round_keeps_the_preamble_and_the_final_body
FAILED test_the_leader_round_also_merges_segments_split_by_a_memory_hint
3 failed, 9 passed in 4.64s
```

基线是 `git archive 0472b6c` 导出的干净副本 + 覆盖新测试文件；`agent_seam.py` 的 md5 与
`0472b6c` 一致（`dd3dad5f81f4578c373f4d18a4d49071`），证明红点来自未修复源码。

## 改动落点

| 文件 | 改动 |
| --- | --- |
| `app/alibot/agent_seam.py` | `_round_assistant_message` → `_round_assistant_messages`（返回全部同 `reply.id` 的 assistant 消息）；`_round_body` 合并它们的 `text` 块；队长轮次把队长 `state.context` 交进 `_outcome_from_reply`；类与 `_outcome_from_reply` 的 docstring 按新契约改写 |
| `app/tests/test_round_body_assembly.py` | 新增 3 条回归用例（单 Agent 轮整轮被判 `ignored`、单 Agent 轮只落开场白、队长轮次同形）+ 复刻生产写法的记忆中间件替身 |
| `app/README.md` | 「一轮 ↔ 一条 `Msg`」按实际契约改写 |
| `app/specs/issue-219-acceptance.md` | 对「队长不传 state_context」加事实修正注记（决策不改） |

## 命令与读数

```sh
scripts/check.sh --fast   # 门禁全绿（Vitest 24 files / 469 tests）
scripts/check.sh          # 1286 passed, 11 skipped in 69.33s；ruff / mypy / pyright / Vitest 全绿
```

## code-review 双轴处置

| 轴 | 发现 | 处置 |
| --- | --- | --- |
| Standards | `app/README.md:314` 的「一轮 ↔ 一条 `Msg`」与新契约不符（review-rules 第 6 条） | 已改 |
| Standards | 测试模块 docstring 写「第 9–11 条」，实际 3 条用例分属 2 节 | 已改为「第 9–10 节（三条用例）」 |
| Standards | `_HintSplittingMemory` 与 `test_runtime_memory._FakeReMeMiddleware` 同形（Duplicated Code） | **不修**：两者的 `Parameters` 形状、注入时机与断言面都不同（前者在 `on_reasoning` 注入、后者只记录构造参数），抽公共替身会把「断言什么」藏进参数；跨测试文件 import 私有替身也违反 review-rules 第 3 条的同一条纪律 |
| Standards | `finally` 里「取活 context 再 save」与单 Agent 轮同形（Shotgun Surgery 倾向） | **不修**：两处的 `save` 目标不同（单条 `state_store` vs 多成员 `state_stores`），合并需要引入新的出参形状，收益不抵 |
| Standards | 新 fixture 未隔离 `HOME` | 已改：`monkeypatch.setenv("HOME", …)` 与 `test_runtime_memory.global_root` 同一条纪律 |
| Spec | `GET /api/mcp-servers` 读数没留下证据文件 | 已补 `live-mcp-servers-20261029.json` |
| Spec | 未按 `docs/agents/implementation.md` §交付 落 `app/specs/issue-<n>-acceptance.md` | 已补：本文件 |
| Spec | 队长 `leader_context` 与 #219 验收文档的既有结论冲突 | 已改 `issue-219-acceptance.md`：加「事实修正」注记，说明旧前提失效与新的机器判据 |
| Spec | RCA/Answer 的轮次计数（5 轮/4 轮、5 份/4 份）不精确 | 已按库与 state 实测改为「6 轮（另 1 条 merged）、5 轮 ignored/failed」与「12 份里 4 份」 |
| Spec | README 措辞改动属 scope creep | **接受**：契约与实现不一致时，`review-rules` 第 6 条要求同步；不做会留下错误文档 |

## 说明

- **历史正文不回溯**：已经落成 `ignored` 的轮次不会补写（`message` 表 append-only）。
- **不需要重启即生效**：修复合并进已运行的二进制后，下一轮起走新口径。
- 未改 ADR、MCP 装配层、频道配置与用户数据。
