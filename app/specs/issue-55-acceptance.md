# issue #55 验收取证：回应预算 ADR 实测依据 + 默认档位复核

票面正本：[GitHub issue #55](https://github.com/whutlichao/alibot/issues/55)。父规格：
`app/specs/issue-31-spec.md` §Implementation Decisions 4「三张实施票及依赖」第 ③ 条与 §Testing Decisions。
相关 ADR：新增 `docs/adr/0024-response-budget-measured-evidence-keeps-default-three.md`；
被复核的既有决策 `docs/adr/0002-response-budget-caps-relay-at-three.md`（**原文未改**），
`docs/adr/0007-human-face-writes-budget-limit-only.md`（**原文未改**）。

- worktree：`/Users/apple/orca/workspaces/alibot/issue-55-impl`
- 分支：`issue-55-impl`（base `bc6f761cc129b2e53e06f6b55b4d4c072f945ace`，与启动时核对一致）
- schema：**未动**，仍是 `SCHEMA_VERSION = 17`（测量只读轮次结果与预算，不新增列/表）
- 状态：**真实 provider 读数缺项**（缺一项即不能宣称 #55 验收通过或关闭）；工具、行为测试、价格快照、
  ADR 与 dry-run 取证已完成。

## 1. 范围与隔离

测量工具是 `app/tests/measure_budget.py`（**不是** `test_*.py`，pytest 不收集；手动入口见 §4）。

| 隔离判据 | 落点 / 证据 |
| --- | --- |
| 全新隔离数据目录 | `prepare_data_dir()`：缺省在系统临时目录建 `alibot-budget-measure-*`；`--data-dir` 必须为空目录 |
| 拒绝真实用户数据 | 路径等于 `default_data_dir()`（`~/Alibot` 或 `ALIBOT_DATA_DIR`）⇒ `MeasurementError`；非空目录同样拒绝 |
| 不读取真实应用数据 | 工具只经 `create_app(data_dir=<隔离目录>)` 建库；消息正文全部是本文件写死的合成提示 |
| 证据脱敏 | 报告只存消息的 `seq/kind/author/body_chars/body_sha256`，不存正文；凭证值不读取、不输出；`_redact()` 兜底处理 `Bearer` / `api_key` / `sk-*` |
| 干跑证据 | `app/specs/issue-55-dry-run-evidence.json`：`environment.data_dir` 是系统临时目录下的 `alibot-budget-measure-*`，`isolated_data_dir=true`（每次运行随机名，不在文档里钉死） |

## 2. 测量实现落点

| 采集项 | 实现 |
| --- | --- |
| `Msg.usage` token | `AgentScopeAgentSeam(usage_sink=…)`（`app/alibot/agent_seam.py`）：每轮最终 `Msg.usage` 的 `input_tokens` / `output_tokens` / `cache_input_tokens` / `cache_creation_input_tokens` 原样交出；默认 `None`，不落库、不进 `RoundOutcome`；`usage is None` 与 `0` 分开记录。失败/超时轮没有最终 `Msg`，发 `usage=None` 记录，报告 `accounting_complete=false`、`usage_evidence=false` |
| 时延 | `GET /api/channels/{id}/round-outcomes` 的 `started_at` / `finished_at`；报告分 `initial_wake` / `jump_rounds` / `all_rounds` 三组汇总 |
| 世代结束判定 | `budget.remaining == 0` + 无 Agent `working` + 消息/轮次/预算读数在静默期内不变；未耗尽/超时/护栏触发记 `incomplete` / `aborted-cost-cap` |
| 成本 | `app/specs/issue-55-model-prices-2026-10-18.json`（官方 DeepSeek 价目，取得日期 2026-10-18，快照 sha256 入报告）；DeepSeek `input_tokens` 含缓存命中，按 miss/hit 分开计；本机 `ollama:*` 为 0（无 provider 账单，电费/硬件不计） |
| 外推 | 初始唤醒成本 + `tier × max/mean(jump_cost)`；另给 `empirical_fanout_estimate = members × max_round + tier × (members−1) × max_round`（经验估计，不是系统普遍上界；`system_monetary_upper_bound=null`）；假设与限制写在 ADR §三/§五 |
| 成本阈值（**不是硬上限**） | `ALIBOT_MEASURE_ALLOW_REMOTE=1` + 必填 `--max-remote-usd`；`CostGuard` 只在一轮结束拿到 usage 后累计，越限后 `GuardedScheduler` 不再提交**下一轮**，报告标 `aborted-cost-cap`；远端 usage 缺失则同样停止提交，标 `aborted-usage-missing`。真实执行前 `preflight_cost_estimate` 按模型硬限制/最大费率/显式调用数假设做保守预估，不通过就拒绝执行 |

**为什么新增 `usage_sink` 仍是「局部采集实现」而不是扩接缝形状**：`RoundOutcome` 的字段没有增加，
`AgentSeam` 协议没有增加方法；sink 与既有 `emit` 同级（进程内观察面、默认关闭、异常不影响轮次结论）。
生产装配点不传它，行为逐字不变。

## 3. 票面 AC → 落点 → 命令与读数

| # | AC | 落点 | 命令 / 读数 |
| --- | --- | --- | --- |
| 1 | 隔离数据目录，不碰真实数据 | `prepare_data_dir()`、`RunConfig.data_dir`、报告 `environment.isolated_data_dir` | `pytest tests/test_budget_measure.py::test_a_non_empty_or_default_data_dir_is_refused` ⇒ passed；dry-run 证据里的数据目录是临时目录 |
| 2 | 量 `Msg.usage`、`round_outcome` 时延、按价目换算成本 | `AgentScopeAgentSeam._record_usage`、`build_report()`、价格快照 | `pytest tests/test_agent_seam_client_lifecycle.py::test_usage_sink_receives_the_final_msg_usage_without_changing_the_outcome` ⇒ passed（stub provider 的真实 `usage={prompt_tokens:1, completion_tokens:1}` 被读到）；`test_cost_separates_cache_hit_tokens_from_cache_miss_tokens` ⇒ passed |
| 3 | 至少一种本机 + 一种远端付费；记录模型/环境/价格源/条件；默认跳过、仓库无凭证 | CLI 必填 `--local-model` / `--remote-model`；`validate_models()`；`ALIBOT_MEASURE_ALLOW_REMOTE=1`；价格快照 | `test_real_remote_runs_require_the_explicit_environment_switch`、`test_real_remote_runs_require_an_explicit_cost_cap` ⇒ passed；`git diff` 中无凭证值；**真实读数缺项见 §5** |
| 4 | 跑满一个人类世代，解释 3/5/10 的实测与外推最坏成本 | `run_epoch()` / `_wait_for_epoch()` / `_extrapolate()` | dry-run：`used=3`、`remaining=0`、`suppressed_rounds=1`、`rounds_total=5`、`jump_round_groups=2`（证据 §5 表）；真实外推未填，见 §5 |
| 5 | 结果、限制、默认档位依据进新 ADR + 证据 | `docs/adr/0024-…md`；本文件；`app/specs/issue-55-dry-run-evidence.json` | ADR 已写方法/缺项/外推口径/保留 3 的条件；证据文件可复跑 |
| 6 | 档位固定 3/5/10，默认 3；改默认需新 ADR | `db.py` 的 `BUDGET_LIMIT` / `BUDGET_LIMIT_TIERS` **未改**；ADR-0002 未改 | `pytest tests/test_channel_budget_limit.py` 相关既有用例仍绿（全量见 §6）；dry-run 的 `budget_final.limit=3`、`source=channel-override` |
| 7 | 脚本化替身只用于行为测试，零 usage 不算成本证据 | `--dry-run` 分支、`usage_evidence` / `cost_evidence` 标志 | `test_dry_run_exhausts_a_full_epoch_with_doubles_and_marks_it_not_cost_evidence` ⇒ passed；证据里 `raw.usage_records=[]`、`cost.total_measured_usd=null` |

## 4. 复跑命令（真实 provider 需人定授权）

```sh
cd app
# 默认行为：不触网、零成本，仅证明编排/采集/报告路径
../.venv/bin/python -m tests.measure_budget \
  --local-model ollama:qwen2.5 \
  --remote-model deepseek:deepseek-flash \
  --tier 3 --dry-run --out /tmp/issue-55-dry-run.json

# 真实测量（会离开本机、会产生费用；必须显式 opt-in + 人定停机阈值）
# 注意：--max-remote-usd 是观测后停机阈值，不是硬上限；先跑 --preflight-only 看保守预估。
ALIBOT_MEASURE_ALLOW_REMOTE=1 ../.venv/bin/python -m tests.measure_budget \
  --local-model ollama:<本机已加载模型> \
  --remote-model deepseek:deepseek-flash \
  --tier 3 \
  --prices specs/issue-55-model-prices-2026-10-18.json \
  --max-remote-usd <人定 USD 阈值> \
  --out specs/issue-55-evidence-<date>.json
```

- 真实测量前先满足：本机 Ollama 在 `OLLAMA_HOST`（默认 `http://localhost:11434`）运行且已有目标模型；
  远端 provider 的凭证变量已由人类环境提供。工具只检查变量**名**是否存在，不打印值。
- 真实测量入口要求 `ALIBOT_MEASURE_ALLOW_REMOTE=1` 与 `--max-remote-usd`；缺任一即 `MeasurementError`，
  且在检查本机 provider 之后才可能创建远端 Agent。
- 真实执行前先跑（不建库、不调用任何模型）：
  `../.venv/bin/python -m tests.measure_budget --local-model ollama:<模型> --remote-model deepseek:deepseek-flash --tier 3 --max-remote-usd <人定阈值> --preflight-only`。
  预估超过人定预算时工具拒绝真实执行。
- 三个价位档位分别用 `--tier 3` / `--tier 5` / `--tier 10` 各跑一次即可得到同口径读数；工具不自动循环，
  以免一次运行的费用越过人定阈值。

## 5. 读数

### 5.1 dry-run（脚本化替身；**不是**成本证据）

命令与证据：`app/specs/issue-55-dry-run-evidence.json`（`mode=scripted-double`）。

| 读数 | 值 |
| --- | --- |
| `status` / `complete` | `complete` / `true` |
| 档位 | `tier=3`，`limit=3`，`source=channel-override` |
| 跳数正本 | `used=3`、`remaining=0`（耗尽） |
| 轮次 | `rounds_total=5`，`jump_round_groups=2`，`suppressed_rounds=1` |
| 初始人类唤醒 | 2 条轮次（min 0 / mean 1 / p50 1 / max 2 ms；第二条的 `base_seq=2`，因为单线程下它在首条回复落库后才开始） |
| 时延 | 全部 5 轮：min 0 / mean 0.4 / p50 0 / max 2 ms（替身即时返回，**不代表真模型**） |
| 世代墙钟 | 822 ms |
| usage / 成本 | `usage_records=[]`；`usage_evidence=false`、`cost_evidence=false`、`total_measured_usd=null` |
| 结论 | 只证明「编排 → 设档 → 发人类消息 → 预算耗尽 → 轮次/时延/抑制结果采集」路径可用 |

### 5.2 真实 provider（缺项）

| provider | 模型/版本 | 环境 | 状态 | 原因 |
| --- | --- | --- | --- | --- |
| 本机 Ollama | 待定 | `127.0.0.1:11434` | **未取得** | 探测时无服务、无 `ollama` 命令；未发现既有可加载模型；按票面不下载大模型、不安装系统级服务 |
| 远端 DeepSeek（付费） | `deepseek:deepseek-flash`（DeepSeek-V4.1-Flash-1017） | 官方 API | **未取得** | `DEEPSEEK_API_KEY` 变量存在，但人定付费授权在成文时尚未到达；未发起真实调用 |

### 5.3 事前约束预检（不调用模型，2026-10-18）

证据：`app/specs/issue-55-preflight-deepseek-flash-2026-10-18.json`
（命令：`--tier 3 --max-model-calls-per-round 2 --preflight-only`）。

| 读数 | 值 |
| --- | --- |
| 单次调用上限 | $0.7608（1M input × 最高 $0.3/M + 384K output × 最高 $1.2/M） |
| 远端轮次假设 | 4（初始人类唤醒 1 + tier 3 每跳至多投递远端 1 次） |
| 模型调用数假设 | 8（4 轮 × 每轮 2 次） |
| 保守预估 | **$6.0864** |
| 结论 | pending 额度是人民币 5 元，尚未折算为 USD；报告 `approved_usd=null`、`fits=false`。框架内部调用次数没有公开硬上限 ⇒ **拒绝真实执行**，符合「不能事前约束就不跑」 |

因此：**本票不声称任何真实 token / 时延 / 金额读数**，也不把 dry-run 的零 usage 写成实测成本；
默认 3 的保留依据是 ADR-0002 的策略裁定 + 没有相反证据（ADR-0024 §四），不是实测结论。

## 6. 门禁读数（本 worktree）

| 门禁 | 命令 | 读数 |
| --- | --- | --- |
| 新增行为测试 | `cd app && ../.venv/bin/python -m pytest tests/test_budget_measure.py tests/test_agent_seam_client_lifecycle.py` | **22 passed**（7.8s，review 收口后） |
| 预算相邻族 | `cd app && ../.venv/bin/python -m pytest tests/test_budget_measure.py tests/test_agent_seam_client_lifecycle.py tests/test_bounded_relay.py tests/test_channel_budget_limit.py tests/test_channel_budget_write.py tests/test_target_scope.py` | **121 passed in 24.10s** |
| 全量门禁 | `scripts/check.sh` | **864 passed, 2 skipped in 378.92s**；ruff `All checks passed!`；ruff format `217 files already formatted`；mypy `Success: no issues found in 92 source files`；pyright `0 errors, 0 warnings, 0 informations`；node `tests 180 / pass 180 / fail 0`；`门禁全绿` |

两个 skip 是既有跳过：`test_agent_smoke.py`（需 `ALIBOT_SMOKE_MODEL`）与
`test_reminder_schema_step.py`（需 `libfaketime`）。本票没有新增 skip；dry-run 走的是独立手动入口，
不在 pytest 收集范围内。

## 7. 缺项、限制与未决

1. **真实测量未执行**：本机 provider 不可用、远端付费授权未到；两者都需要人类环境动作，不是代码问题。
   在真实读数补齐前，PR 保持 draft，`Closes #55` 不得视为已完成。
2. **单次样本**：工具每次只跑 1 个世代；即使授权后取得读数，也只代表该模型版本、该价格日、该两成员
   拓扑。ADR-0024 §五已登记。
3. **单工作线程**：为让接力按 FIFO 发生，测量固定 `ThreadScheduler(max_workers=1)`；生产默认 4 线程的
   合并/延迟形态可能不同，报告 `conditions.scheduler_max_workers=1`。
4. **team 模式未覆盖**：`usage_sink` 只拿队长最终 `Msg`，不采集被分派成员 usage；team 不在本票范围。
5. **价格会变**：复跑必须核对快照的 `retrieved_at` 与官方页；峰时/谷时用 `--billing-basis` 显式选择，
   默认 `peak`（保守）。
6. **未改 schema / 未改默认值 / 未改 ADR-0002**：`git diff` 可核。

## 8. 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `app/alibot/agent_seam.py` | 新增默认 `None` 的 `usage_sink` 观察口；单 Agent / 队长轮次在收口前记录最终 `Msg.usage`；异常不影响轮次 |
| `app/tests/measure_budget.py` | **新增**：隔离目录、真实编排、成本阈值与预检、usage/时延采集、价格换算、外推与脱敏报告 |
| `app/tests/test_budget_measure.py` | **新增**：价目/成本/缺失 usage/护栏/隔离/opt-in/dry-run 行为测试 |
| `app/tests/test_agent_seam_client_lifecycle.py` | 新增 2 条 seam 级用例：usage sink 收到真实 stub provider usage；sink 异常不改轮次 |
| `app/specs/issue-55-model-prices-2026-10-18.json` | **新增**：官方价格快照（DeepSeek，带来源与日期；本机零价） |
| `app/specs/issue-55-dry-run-evidence.json` | **新增**：脱敏 dry-run 证据 |
| `app/specs/issue-55-preflight-deepseek-flash-2026-10-18.json` | **新增**：不调用模型的事前约束预检（保守预估 $6.0864，拒绝在 pending 预算内执行） |
| `CONTEXT.md` | 「成员面适配接缝」补一句默认关闭的用量观察口（不是第三种输出） |
| `app/README.md` | 「测试与检查」补一段手动实测入口（dry-run / preflight / opt-in） |
| `app/specs/issue-55-acceptance.md` | 本文件 |
| `app/specs/issue-55-pr-body.md` | PR #175 正文正本 |
| `docs/adr/0024-response-budget-measured-evidence-keeps-default-three.md` | **新增** ADR；不改 ADR-0002 / 0007 |

**未改动**：`app/alibot/db.py`（默认 3 / 档位 3,5,10 / schema 17）、`app/alibot/orchestration.py`、
预算读写端点、Studio 前端、迁移链。

## 9. 双轴 code-review 与处理

固定点 `bc6f761`，两轮都按 `/Users/apple/.agents/skills/code-review/SKILL.md` 用只读 `reviewer`
子代理跑 Standards + Spec 两轴。

**第一轮（c94d339）**

| 轴 | finding | 处理 |
| --- | --- | --- |
| Spec | 初始唤醒按 `base_seq == human_seq` 分组，单线程下第二位成员的首轮 `base_seq=2`，成本/时延被漏算 | 改为「`consumed_budget=false` 且非 `merged` / `budget-exhausted`」；dry-run 证据现在得到 2 条初始轮次 |
| Spec | seam 的失败/超时分支在 `_record_usage` 前返回，已计费但无 usage 的轮次可让证据仍标完整 | 失败/超时/无队长回复都发 `usage=None`；报告按 `unaccounted_rounds` / `accounting_complete` 收口，成本合计不完整时为 `null` |
| Standards | 验收文档钉死的临时目录随机名已过期 | 改为 `alibot-budget-measure-*` 通配，不钉随机名 |
| Standards | `utc_now` 与 `db.utc_now` 重复；`validate_models` 返回值无人用；`PriceBook.currency` / `ModelPrice.note` 未用 | 改用 `db.utc_now`；`validate_models` 返回 `None`；把 `currency` 与每模型 `price_sources`（含 `note`）写进报告 |
| Standards | `CONTEXT.md` 的成员面适配接缝未登记 usage 观察口 | 已补一句：默认关闭、不落库、不是第三种输出 |
| Standards | 测试直接调测量工具纯函数，与「pytest 只断言外部行为」有张力 | 在 `app/README.md` 登记第三处例外：工具无 HTTP 面，纯函数就是它的契约；完整编排仍由 dry-run 走真实 `create_app` |

**第二轮（d539209）**

| 轴 | finding | 处理 |
| --- | --- | --- |
| Spec | `--max-model-calls-per-round 0` 会让预检恒真（$0），绕过预算约束 | `preflight_cost_estimate` 要求正整数，否则 `finite=false` / `fits=false`，新增用例 |
| Spec | 成本换算错误也走 `aborted-usage-missing`，诊断不准确 | `stop_reason=cost-math-error` 时返回独立的「成本换算失败」原因 |
| Spec | 真实本机 + 远端读数仍缺（AC2/3/4） | **接受为未完成**：PR 保持 draft、不写 `Closes #55`、ADR/本文件如实标缺项 |
| Standards | `run_epoch` 返回的 `agents` 字段无人读，多一次 `GET /api/agents` | 删除该返回键（`_wait_for_epoch` 自己的状态读取保留） |
| Standards | ADR 示例 `--max-remote-usd 1.0` 低于预检值，示例必失败 | 改为占位符「人定阈值，且不低于预检保守值」 |

结论：两轴均无 blocking；缺项是**外部条件**（本机模型未准备、付费授权未到、预检不通过），
不是代码遗留。

## Answer（Status / Decisions）

- **Status**：`implemented_tooling_pending_real_measurement`。工具、行为测试、价格快照、ADR、dry-run
  证据已完成；真实本机 + 远端付费读数缺失，不能标记 #55 验收通过，PR 保持 draft。
- **Decisions**：
  1. 默认仍为 3，固定档位仍为 3 / 5 / 10；ADR-0002 不修订、不取代。
  2. 测量采集用默认关闭的 `usage_sink`，不扩 `RoundOutcome` / `AgentSeam` 的输出形状。
  3. `--max-remote-usd` 是观测后停机阈值，不是硬上限；远端 usage 缺失即停止提交。真实执行前必须
     `--preflight-only` 按模型硬限制/最大费率/调用数假设做保守预估；本次预估 $6.0864 高于 pending
     人民币 5 元，因此**不执行付费调用**。
  4. 外推只给 `scenario_bound` 与 `empirical_fanout_estimate`；不声称系统普遍金额上界（调用/重试/
     上下文增长无有限硬约束）。
  5. 替身 dry-run 只作行为证据，报告与 ADR 都显式标 `usage_evidence=false` / `cost_evidence=false`。
- **PR**：[#175](https://github.com/whutlichao/alibot/pull/175)（draft，base `main`，head branch
  `issue-55-impl`；正文正本 `app/specs/issue-55-pr-body.md`）。PR 不含 `Closes #55`。
- **Next**：人定提高预算/收窄条件后，在人类环境启动 Ollama 并准备本机模型、按 §4 先预检再跑；把本机
  与远端两组读数补进 ADR-0024 与本节，再转 ready、执行 `Closes #55`。
