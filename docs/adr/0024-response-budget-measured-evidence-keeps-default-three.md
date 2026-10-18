# 回应预算的实测依据：默认 3 在固定 3 / 5 / 10 档位中的复核

## 决策记录

- **Context**：ADR-0002 把「每个人类世代最多 3 次 agent→agent 跳」定为**策略裁定**，仓库没有 token /
  时延 / 金额读数支持这个具体数值。#31 的实测票（#55）要求在隔离数据目录里跑满一个人类世代，
  至少取一种本机 provider 与一种远端付费 provider，并按有日期的官方价目换算 provider 成本。
- **Decision**：**保留默认 3**，固定档位仍是 3 / 5 / 10；不修改 ADR-0002，也不把 3 写成「实测得出的
  最优值」。若实测结果支持改变默认值，必须另立新 ADR 明确取代或修订 ADR-0002 的相关结论。
- **Consequences**：默认值继续是策略值而不是成本结论；测量工具默认不触网，真实读数只在显式 opt-in
  且有人定授权时产生；本 ADR 的缺项必须在验收文档中如实标注，不能以脚本化替身的零 usage 补位。

## 一、测量方法（可复跑）

入口是 `app/tests/measure_budget.py`（文件名不是 `test_*.py`，pytest 不会收集）：

```sh
cd app
ALIBOT_MEASURE_ALLOW_REMOTE=1 ../.venv/bin/python -m tests.measure_budget \
  --local-model ollama:qwen2.5 \
  --remote-model deepseek:deepseek-flash \
  --tier 3 \
  --prices specs/issue-55-model-prices-2026-10-18.json \
  --max-remote-usd 1.0 \
  --out specs/issue-55-evidence-<date>.json
```

- **隔离**：`--data-dir` 必须是全新空目录；缺省在系统临时目录新建 `alibot-budget-measure-*`。工具拒绝
  默认用户数据目录（`default_data_dir()`，即 `~/Alibot` 或其 `ALIBOT_DATA_DIR` 覆盖值），不读取、
  不修改任何用户真实数据。消息正文只来自本工具写死的合成提示。
- **真实编排**：`create_app(data_dir=…)` + `AgentScopeAgentSeam` + 单工作线程的真实 `ThreadScheduler`。
  单线程是为了把「人类唤醒 → 回复 → 下一跳」固定成 FIFO；多线程会让两个初始轮次同时 in-flight，
  互相唤醒按既有合并语义并掉，接力可能在预算耗尽前停止。它是被测条件，不是省成本。
- **usage**：`AgentScopeAgentSeam` 新增**默认 `None`** 的 `usage_sink` 观察口，把每轮最终 `Msg.usage`
  原样交出（`input_tokens` / `output_tokens` / `cache_input_tokens` / `cache_creation_input_tokens`）。
  它不落库、不进 `RoundOutcome`，不扩 ADR-0010 的接缝输出形状；`usage is None` 与 `usage=0` 在报告里
  是两种事实。开箱即用的生产路径没有 sink，行为逐字不变。
- **时延**：读 `round_outcome.started_at` / `finished_at`，分别汇总初始人类唤醒、跳轮次与全部轮次；
  世代总墙钟时间由脚本从发送人类消息到「无 working 状态 + 预算耗尽 + 静默期无新行」计时。
- **完整世代**：脚本等到 `budget.remaining == 0` 且无 Agent 处于 `working`，再要求消息 / 轮次结果 /
  预算读数在静默期内不再变化。`budget-exhausted` 轮次结果与 `target_budget.used` 一起入证。
  未耗尽 / 超时 / 成本观测阈值触发 / 远端 usage 缺失的一律记 `incomplete`、`aborted-cost-cap` 或
  `aborted-usage-missing`，不算完成样本。
- **成本**：价目快照 `app/specs/issue-55-model-prices-2026-10-18.json`，官方来源
  `https://api-docs.deepseek.com/quick_start/pricing/`（取得日期 2026-10-18）。DeepSeek 的
  `input_tokens` 含缓存命中，换算时按 `input − cache_input` 计 miss 价、`cache_input` 计命中价；
  峰时 / 谷时是两条价，报告显式记录 `billing_basis`。本机 provider 没有 provider 账单，价格为 0，
  电费与硬件折旧不计入。
- **opt-in 与成本约束**：真实远端调用必须同时满足 `ALIBOT_MEASURE_ALLOW_REMOTE=1` 与显式
  `--max-remote-usd`。这里必须说清楚：`--max-remote-usd` 是**观测后停机阈值**，不是硬费用上限——
  只有一轮结束、provider 已返回 usage 之后才累加，正在进行的轮次、每轮内部的多次模型调用与重试
  都可能把费用推过阈值；它只能阻止**下一轮**继续提交。远端轮次缺 usage 时费用无法归因，
  `GuardedScheduler` 同样停止提交新轮次，报告标 `aborted-usage-missing`，不把账目当完整。
  因此真实付费执行前必须先用 `preflight_cost_estimate` 按模型输入/输出硬限制、最大费率与显式假设的
  每轮调用次数做保守预估；预估超过人定预算就拒绝执行。默认 pytest 不触网：`--dry-run` 用
  `ScriptedAgentSeam` 走完整编排，但报告标 `mode=scripted-double`、`usage_evidence=false`、
  `cost_evidence=false`，替身零 token 不作为成本证据。

## 二、结果（截至 2026-10-18 成文时）

| provider | 模型 / 版本 | 环境 | 输入/输出上限 | 成员 | 重复 | 状态 | 读数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 本机（Ollama） | 待人定 | 本机 127.0.0.1:11434 | 由所选模型决定 | 2 | 1 | **未取得** | 本机 Ollama 未运行、未发现既有可加载模型；未下载大模型、未安装系统级服务 |
| 远端付费（DeepSeek） | `deepseek:deepseek-flash` / DeepSeek-V4.1-Flash-1017 | 官方 API | 1M context / 384K max output | 2 | 1 | **未取得** | `DEEPSEEK_API_KEY` 变量存在，但人定付费授权尚未到达；未发起任何真实调用 |

**结论必须按缺项读**：上表没有真实 token / 时延 / 金额读数，因此本 ADR **不声称**默认 3 已被实测支持，
也不声称 3 / 5 / 10 中哪一档更合适。2026-10-18 的 dry-run 只证明工具与编排路径可用（预算 `used=3`、
`budget-exhausted` 轮次存在、初始唤醒 / 跳轮次 / 静默收口都可采集），它不是成本证据。

**事前约束预检**（`--preflight-only`，不建库、不调用模型；证据
`app/specs/issue-55-preflight-deepseek-flash-2026-10-18.json`）：按 deepseek-flash 的 1M input / 384K
output 硬限制、峰时最高费率（input $0.3 / output $1.2 per 1M）、tier 3 下 4 个远端轮次、每轮 2 次
模型调用的显式假设，单次调用上限 $0.7608，保守预估 **$6.0864**。当前 pending 的是人民币 5 元额度
（尚未折算为 USD，本 ADR 不替人定做汇率换算）；在没有 USD 阈值前报告的 `approved_usd=null`、
`fits=false`。而且框架内部调用次数没有公开硬上限，工具在预检不通过时**拒绝发起付费调用**。因此
本次没有执行真实远端测量；要执行必须先由人定确认足以覆盖保守预估的额度或收窄条件（例如降低显式
调用数假设、限制输入/输出或只跑 tier 3 的一次受控样本），并重新预检。

## 三、成本外推口径（有真实样本后按同一口径填写）

工具把实测拆成三组，禁止把跳数与轮数混用：

1. **初始人类唤醒成本**：`consumed_budget=false` 且不是 `merged` / `budget-exhausted` 的轮次 usage
   之和。它是「人发一句话」的固定开销，不是一次跳。判据不能写成 `base_seq == human_seq`：单线程下
   第二位成员的这一轮可能在该消息的首条回复落库之后才开始，`base_seq` 因此是那条回复的 seq。
2. **每次跳的成本**：`consumed_budget=true` 的轮次按 `base_seq` 分组；一次跳投递多个成员时该组有
   多条轮次，成本是组内 usage 之和。目标全部 in-flight 的零投递不产生轮次，也不计跳。
3. **外推**：`scenario_bound` 假设后续每一跳的成员集合与本次观测一致，`initial + tier × max(或 mean)
   jump_cost`；`empirical_fanout_estimate` 假设每一跳唤醒频道内其余全部成员、每轮都取观测到的最大
   单轮成本：`members × max_round + tier × (members−1) × max_round`。后者是**经验 fan-out 估计，
   不是系统普遍最坏上界**；报告同时写 `system_monetary_upper_bound=null`，理由是上下文增长、
   thinking / 工具批次 / 重试与 provider 价目调整没有有限硬约束。

外推不包含 provider 上限（1M context × 384K output）拉满的绝对上界；报告单列模型输入/输出限制，
并明确不包含上下文增长、重试、工具调用增多与峰谷价切换。跳数正本仍是 `target_budget.used`；
`round_outcome.consumed_budget` 是行级标记，不能求和解跳数。

## 四、保留默认 3 的依据与适用条件

- **依据是「没有相反证据 + 保持既有策略」**，不是「3 最便宜」或「3 最合适」。ADR-0002 的取舍（耗尽
  零投递、消息照常落库）不因本 ADR 改变；3 仍是保守的默认，允许频道显式选择 5 / 10。
- **适用条件**：本机单用户、频道成员数量级为个位数、每次跳唤醒的成员数小于全部成员、模型没有
  长上下文/工具循环放大。成员数、模型单价、上下文长度或并发形态变化后，应重跑同一工具再判断。
- **改变默认值的门槛**：新 ADR 必须同时给出本机与远端付费 provider 的完整世代读数（token、时延、
  成本）、价格来源与日期、样本条件，并明确写出与 ADR-0002 的关系。单次样本、替身读数或纯外推都
  不足以改变默认值。

## 五、局限

- 每个 provider 只有 1 次重复、1 个两成员拓扑；模型回复长度与是否继续接力由模型自判，单次世代
  可能未耗尽预算（工具会记 `incomplete`，不补算）。
- 单工作线程固定了并发条件；生产默认 4 线程下的合并 / 延迟形态可能不同。
- 本机 provider 的成本只记 provider 账单（0），不含电费 / 硬件折旧；不同本机模型的能力与 token
  统计口径也可能不同。
- `usage_sink` 采集的是单 Agent 轮次的最终 `Msg` 聚合值；team 轮次只拿队长最终 `Msg`，不采集被分派
  成员的 usage，因此 team 模式不在本次测量范围。失败/超时轮没有最终 `Msg`，会发一条 `usage=None`
  记录；只要远端轮出现这种记录，成本账目即标不完整并停止提交新轮次，不会把失败轮的费用漏掉。
- 价格会变；证据文件必须同时保存快照哈希与取得日期，复跑时若价目已变，结论要重新换算。
- `--max-remote-usd` 是观测后停机阈值，不是硬费用上限；预检里的「每轮模型调用次数」也是显式假设，
  框架内部的 thinking / 工具批次 / 重试没有公开硬上限，因此本 ADR 不宣称系统金额上界。
