## Summary

为 #55 增加**隔离、手动、默认不触网**的回应预算实测工具，并把「默认 3 仍是策略值」的复核留在新 ADR。

```text
app/tests/measure_budget.py        # 手动入口：隔离目录 + 真实编排 + usage/时延/成本采集
  ├─ usage_sink (agent_seam.py)    # 最终 Msg.usage；默认 None，不落库/不扩 RoundOutcome
  ├─ preflight_cost_estimate       # 模型硬限制 × 最高费率 × 调用数假设；不通过拒跑
  ├─ CostGuard / GuardedScheduler  # 观测后停机阈值；远端 usage 缺失即停
  └─ build_report                  # 脱敏证据：tokens / 时延 / 成本 / 3·5·10 外推
app/specs/issue-55-*.json          # 价目快照 / dry-run 证据 / 预检证据
docs/adr/0024-…md                  # 结果、缺项、局限、保留默认 3 的依据
```

- 默认仍为 3，固定档位仍为 3 / 5 / 10；ADR-0002 原文未改。
- 新增 `usage_sink` 默认关闭；生产装配不传它，`RoundOutcome` / `AgentSeam` 输出形状不变。
- 真实远端调用必须 `ALIBOT_MEASURE_ALLOW_REMOTE=1` + 显式 `--max-remote-usd`；先过
  `--preflight-only` 的保守预估。
- 脚本化替身只用于 `--dry-run` 行为取证，报告标 `usage_evidence=false` / `cost_evidence=false`。
- follow-up：usage 记录一一归轮（歧义 / 未命中 / 不可定价都标 incomplete）；`test_budget_measure.py`
  改为 CLI 黑盒并撤回 README 临时例外；报告 / ADR / help 记录框架实际默认（ReAct 50+5、DeepSeek
  retries 3 等）并明确「尚未完整推导总调用界」；`--no-ff` 吸收 origin/main `6c7d0e7`（仅图标差异）。

**Head branch:** `issue-55-impl`（base `main`，diff 从 `bc6f761` 起；latest commit 见 PR 页面）。
**Worker:** `task_0475697480f9` / dispatch `ctx_8131fddc678c`（本 follow-up 的 `worker_done` 在交付
结尾发送，回执由 coordinator 的 Run 记录核对）。**状态：draft。** 真实本机 provider 与远端付费 provider 读数尚未取得：本机 Ollama 不可用；远端付费
授权仍 pending（人民币 5 元额度），且按模型硬限制的保守预估为 **$6.0864**，预检不通过，工具拒绝执行。
因此本 PR **不关闭 #55**，也不把 dry-run 或预检写成实测成本。

## Evidence

- **Before:** 没有可复跑的测量入口；`Msg.usage` 不进入任何采集面；`docs/adr/0002` 的 3 是未实测的策略值。
  **After:** 隔离 dry-run 跑满 tier 3：`used=3` / `remaining=0` / `rounds_total=5` /
  `jump_round_groups=2` / `suppressed_rounds=1`；证据
  `app/specs/issue-55-dry-run-evidence.json`。
- 成本换算单测：DeepSeek cache hit/miss 分开计，`0.0008412 USD`（peak）与 `0.0004206 USD`（off-peak）；
  usage 缺失拒绝换算，不当 0。
- 预检（不建库/不调用模型）：`deepseek:deepseek-flash`、tier 3、每轮 2 次调用 ⇒ 单调用上限 `$0.7608`、
  保守预估 `$6.0864`、`fits=false`；证据
  `app/specs/issue-55-preflight-deepseek-flash-2026-10-18.json`。
- follow-up 门禁（不重跑全量）：受影响 pytest **115 passed in 26.02s**；
  `scripts/check.sh --fast ...` 全绿（ruff / format / mypy / pyright / node `180 pass`）。
- 全量门禁基线（follow-up 前的 `ebde45e` / `99c5012` 代码基线）：**864 passed, 2 skipped in 378.92s**；
  ruff `All checks passed!`；ruff format `217 files already formatted`；mypy `Success: no issues found
  in 92 source files`；pyright `0 errors, 0 warnings, 0 informations`；node `tests 180 / pass 180 / fail 0`。
  follow-up 只改测量工具 / 测试 / 文档，并 merge 仅图标差异的 main，故该证据仍适用。
- 复跑命令见 `app/specs/issue-55-acceptance.md` §4；dry-run 无需凭证、零成本。
- 双轴 code-review（Standards / Spec）：两轮均只读完成。第一轮发现初始唤醒漏算、失败轮漏 usage、
  成本证据可误标完整等已修复；第二轮确认闭合，并补了 `--max-model-calls-per-round` 正整数校验、
  `cost-math-error` 独立诊断、删除死字段、修正 ADR 示例阈值、在 README 登记测量工具测试例外。
  明细见 `app/specs/issue-55-acceptance.md` §9。

## Merge Danger

**Door:** two-way。只新增手动测量工具、测试、价目/证据与 ADR；`db.py` 的默认 3、固定档位、schema 17、
预算读写端点、orchestration 与 Studio 均未改。回滚 = revert 本 PR。

**Blast Radius:** low。生产路径唯一改动是 `AgentScopeAgentSeam.__init__` 多一个默认 `None` 的
`usage_sink` 参数；不传时行为逐字不变。真实 provider 读数缺失是**交付缺项**而不是合并风险：合并不会
触发任何真实模型或费用；在补齐读数前不要关闭 #55。
