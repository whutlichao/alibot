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
- CI 修复：`run()` 先判 opt-in / 停机阈值、再查 provider 凭证；dry-run 只在替身路径临时补齐缺失的
  凭证占位，并在退出时逐项还原。干净进程（无 `DEEPSEEK_API_KEY`）下 dry-run 仍跑满 tier 3，
  opt-in 拒绝不再被宿主 key 抢先；生产编排的可用性 / 远端告知 / 凭证规则未改。

**Head branch:** `issue-55-impl`（base `main`，diff 从 `bc6f761` 起；latest commit 见 PR 页面）。
**状态：工具准备阶段（可独立合并）。** 本轮修复真实 CI run `39122684457` 的两项失败。完整 #55 仍
**OPEN**：真实本机 provider 与远端付费 provider 读数尚未取得：本机 Ollama 不可用；远端付费授权仍
pending（人民币 5 元额度），且按模型硬限制的保守预估为 **$6.0864**，预检不通过，工具拒绝执行。
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
- CI 失败修复（run `39122684457`：2 failed, 856 passed, 2 skipped）：
  **Before:** 干净进程（无 `DEEPSEEK_API_KEY`）下 opt-in 用例先报「缺凭证」；dry-run 因远端成员不可用
  停在 `status=incomplete`、`used=1`。
  **After:** 同一命令两个用例 **2 passed in 1.05s**；dry-run 仍 `complete` / `used=3` / `remaining=0`，
  `usage_evidence=false`、`cost_evidence=false`，退出后 `DEEPSEEK_API_KEY` 仍不存在。
- CI 修复门禁（不重跑本机全量）：`pytest tests/test_budget_measure.py
  tests/test_agent_seam_client_lifecycle.py` ⇒ **16 passed in 6.18s**；
  `scripts/check.sh --fast ...` ⇒ **16 passed**；ruff `All checks passed!`；ruff format
  `217 files already formatted`；mypy `Success: no issues found in 92 source files`；pyright
  `0 errors, 0 warnings, 0 informations`；node `tests 180 / pass 180 / fail 0`；`门禁全绿`。
- 历史全量门禁基线（修复前的 `ebde45e` / `99c5012` 代码基线）：**864 passed, 2 skipped in 378.92s**；
  ruff / format / mypy / pyright / node 全绿。该读数**不是**本轮最终 head 的全量结果；最终全量由
  GitHub CI 在同一 head 上执行并记录在 PR 页面。
- 复跑命令与逐条判据见 `app/specs/issue-55-acceptance.md` §4 / §6.1；dry-run 无需凭证、零成本。
- 双轴 code-review（Standards / Spec）：两轮均只读完成。第一轮发现初始唤醒漏算、失败轮漏 usage、
  成本证据可误标完整等已修复；第二轮确认闭合，并补了 `--max-model-calls-per-round` 正整数校验、
  `cost-math-error` 独立诊断、删除死字段、修正 ADR 示例阈值、在 README 登记测量工具测试例外。
  明细见 `app/specs/issue-55-acceptance.md` §9。

## Merge Danger

**Door:** two-way。只新增手动测量工具、测试、价目/证据与 ADR；`db.py` 的默认 3、固定档位、schema 17、
预算读写端点、orchestration 与 Studio 均未改。本轮的 CI 修复也只改 `app/tests/measure_budget.py` 与
其 CLI 测试：opt-in 判定顺序 + dry-run 的临时凭证占位（退出即还原），真实调用路径仍用
`nullcontext()`。回滚 = revert 本 PR。

**Blast Radius:** low。生产路径唯一改动是 `AgentScopeAgentSeam.__init__` 多一个默认 `None` 的
`usage_sink` 参数；不传时行为逐字不变。真实 provider 读数缺失是**交付缺项**而不是合并风险：合并不会
触发任何真实模型或费用；在补齐读数前不要关闭 #55。
