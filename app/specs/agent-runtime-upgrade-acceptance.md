# Agent 运行时升级：验收与 Answer

规格正本：本次 Orca task `task_7586fc46fea4` 的 T1→T5（AgentScope 2.0.10）。
测试边界沿用规格明确指定的适配接缝、HTTP/SSE、迁移、Studio DOM；不调用付费模型。

## T1 上下文窗口

- 落点：`orchestration.py` 保留 fresh / pending / mentioned / signal / base_seq / mark_read 的判据，仅把历史装入 inputs；普通轮默认 40 条，续跑仍为完整历史。
- 红证据：隔离基线 `.scratch/red-t1`，`.scratch/t1-red.log`，2 failed；第二轮实际只有 `[3]`，应为 `[1, 2, 3]`。
- 验证：`scripts/check.sh --fast tests/test_context_window.py tests/test_agent_round.py tests/test_channel_join.py tests/test_task_continue.py`，57 passed；随后 `scripts/check.sh --fast`，静态检查全绿、Node 212 passed。
- 覆盖：同 target 第二轮含上一轮人与自己的回复、旧点名不成为新纪律、base_seq 与读位停在 3、环境覆盖窗口不截断轮次读取。

## T2 压缩、卸载、每轮注入

- 落点：真实单 Agent / team 成员均传入 ContextConfig、ReActConfig、ModelConfig 与 InjectionConfig；运行时默认阈值 0.7、保留比例 0.2、工具结果上限 4000、图像 5、迭代 30、重试 2。
- Offloader 把压缩上下文与工具结果独占创建到 `.alibot/sessions/<target--member>/offload/`；Docker bind mount 下返回 `/workspace/.alibot/...`，不用 Docker API 落盘。
- 每轮重新读取上海时间（ISO 时区）、target 名称、上下文消息数；框架注入器另负责动态 token 用量。
- Docker 不可用：受限装配层仍报错且不回落本地；运行时捕获此错误，关闭工作区工具并继续对话，reason 与日志含可读降级说明。此处按新规格替代旧 offline 整轮失败口径。
- 红证据：隔离基线 `.scratch/red-t2`、`.scratch/t2-red.log`，2 failed。绿证据：`scripts/check.sh --fast tests/test_runtime_context.py tests/test_agent_seam_client_lifecycle.py tests/test_approval_seam.py tests/test_docker_workspace.py`，24 passed / 7 skipped（本机无 Docker daemon）；新增真实 Read 大结果卸载后 `test_runtime_context.py` 3 passed，随后 fast 门禁全绿 / Node 212 passed。
- 接缝例外说明：框架配置、压缩调用、实际卸载在 ScriptedAgentSeam 下不可见，因此沿既有真实 seam + 本机脚本模型测试（与生命周期、审批测试同族），不调用内部收口函数、不联网。

## T3 轮次结束原因 / schema 23

- 落点：RoundOutcome、适配器收口、编排转存、数据库往返、Studio 消息流 reason 展示。`interrupted` 不产出消息，`exceed_max_iters` 可保留部分正文；status 仍使用 replied，结束原因独立存储。
- Schema 23 一次声明完整终态：round_outcome.finished_reason 可空，旧行保持 NULL；四个 runtime 配置列随同迁移，为 T5 的读写面预备。历史 DDL 留档与 22→23 MigrationStep 连续。
- 红证据：`.scratch/red-t3`、`.scratch/t3-red.log`（3 failed）、`.scratch/t3-ui-red.log`（新文案两例失败）。
- 绿证据：运行迁移与链守卫时 64 passed，仅新增用例使用库文件路径代替数据目录一处失败；修正后 `scripts/check.sh --fast tests/test_runtime_finished.py` 为 3 passed、mypy/pyright/ruff 全绿、Node 214 passed。全量门禁在 T5 收尾重跑。
- 备注：会话恢复时宿主误将原 Dispatch 标为 failed。coordinator 已明确指示不再修汇报通道、继续实现，review 基线固定为 3863e39；最终报告未单独成文，读数与证据见文末「交付」。

## T4 中断与状态持久化

- 落点：POST `/api/targets/{target_id}/rounds/{reply_id}/interrupt`；编排只接受当前 in-flight turn 已发布的 reply_id；适配器跨线程取消该轮任务，审批挂起时回送 UserInterruptEvent，finally 内独立关闭 MCP、mount 与 model client。
- 新事件 `round.interrupted` 为 live-only，不推进 SSE 游标；Studio 频道 / DM 的 typing 与任务线程旁都有停止按钮，点击使用实际 reply_id，成功或结束帧后移除。
- AgentState 原子写入每成员每 target 的 `.alibot/sessions/<session_id>/state.json`；下一轮恢复摘要、工具缓存、Plan 与 middleware 状态。权限使用当前配置；消息 context 从数据库窗口重建，避免快照与历史输入重复拼接。损坏、越界符号链接及写失败记录可读日志后降级。
- 红证据：`.scratch/red-t4` / `.scratch/t4-red.log`（2 failed）、`.scratch/t4-ui-red.log`（停止按钮失败）。
- 绿证据：`scripts/check.sh --fast tests/test_runtime_interrupt.py tests/test_approval_seam.py tests/test_team_mode.py tests/test_agent_event_stream.py`，41 passed；ruff / mypy / pyright 全绿，Node 215 passed。
- 观测：错误 target 409、正确中断 200、重复 409；轮次 finished_reason=interrupted，reply_seq=NULL，无 Agent 消息；事件可序列化为 SSE 且无 id；运行中 model client 关闭；挂起 Write 未执行、审批 expired；下一轮读取 middle_context，损坏状态不失败。

## T5 长期记忆与配置面

- 落点：`memory.py` 的 AgenticMemoryMiddleware、受限 backend 与权限中间件；既有单 Agent / team 装配点复用相同逻辑。记忆 off / null 时不挂、不创建目录；agentic 时只授予 `.alibot/memory/<member>/Memory/` 的编辑权，保留显式 deny/ask 规则。
- 同一 member 的两个 target 会话通过真实 Read/Write 写回并召回 MEMORY.md；DM 没有 workspace 时只补 Read/Write/Edit，不能读项目文件。目录祖先符号链接、越界路径与文件符号链接经根检查拒绝。
- PATCH 四字段沿用显式 null / 省略 / 具体值的三态；新建数值配置为 null，跟随环境与默认，schema DDL 默认仍为 40 / 0.7 / 30 / off。Studio 编辑弹窗有长期记忆开关与三项数值配置，空值提交 null。
- 红证据：`.scratch/red-t5` / `.scratch/t5-red.log`，2 failed / 5 passed；`.scratch/t5-ui-red.log` 新增开关用例失败。
- 绿证据：六个 runtime 测试文件合计 19 passed（随后增补 error 结束原因用例），`scripts/check.sh --fast` 的 ruff、mypy、pyright 全绿，Node 217 passed。完整门禁及两轴审查原始读数见文末「交付」（`/tmp/alibot-agent-runtime/check-final.log`）。
- 浏览器：ego-browser 在临时数据目录完成 45 / 0.6 / 35 / agentic 的表单保存，真实消息轮次出现停止按钮、点击后无 Agent 消息且读到 interrupted 结果。截图 `/tmp/alibot-agent-runtime/studio-runtime-settings.png`、`studio-stop-round.png`、`studio-round-interrupted.png`。滚动数字输入时的原生值漂移已修正并重验。
- 全量门禁发现的旧断言同步：共享轮次结果读面新增 finished_reason；原来按同毫秒最后一行推断成员唯一结论的用例在开工隔离基线第二次运行也失败（`.scratch/outcome-order-baseline.log`），改为核对该成员真实产生的结果集合，不改生产排序或回应纪律。
- 文档：CONTEXT 新增上下文窗口、轮次结束原因、长期记忆，并修订输入词条；ADR-0030 记录窗口 / 压缩 / 记忆与新消息纪律边界；README 同步 schema 23、端点、环境配置与降级。

## 最终门禁与 review

review 比较基线由 coordinator 确认为 `3863e39`。最终全量门禁与两轴发现见下；测试仅使用临时库和脚本模型，无付费调用，未操作用户真实数据目录。

`scripts/check.sh` 全绿（worker 本机 `/tmp/alibot-agent-runtime/check-final.log`；coordinator 在隔离 worktree 复跑 `/tmp/alibot-agent-runtime/coordinator-check.log`，读数一致）：

- pytest 全量：**1036 passed, 10 skipped**（583.62s；跳过为本机无 Docker daemon 等既有口径）
- ruff check：**All checks passed!**；ruff format --check：**262 files already formatted**
- mypy（仓库根）：**Success: no issues found in 113 source files**
- pyright：**0 errors, 0 warnings, 0 informations**
- node --test（Studio DOM 用例族，glob）：**tests 220 / pass 220 / fail 0**

## Standards

- 初审 2 项硬违反：编排把必答轮判失败时丢失原结束原因；迁移说明仍写 schema 22。已改为保留原 RoundOutcome 字段，并同步 README / CONTEXT 的 22→23 说明。
- 初审 1 项判断题：单 Agent 与 team 的中断收口有重复形状。保留两条控制流：单 Agent 经事件消费器结束挂起审批，team 逐参与者结束，事件转发和资源关闭顺序不同；没有引入跨两种生命周期的通用回调抽象。
- 回归：`test_runtime_finished.py` 的三种正常完成但业务判失败行为仍保存 completed；隔离红证据 `.scratch/review-finished-red.log` 为 3 failed。

## Spec

- 初审 3 项 P2：任务线程缺结束文案；重连后丢失停止按钮；DM 无法读卸载文件。均已补齐。
- 任务结果按 id 去重恢复全量，诊断挂到触发消息或 system 历史行，不计入消息数。SSE 每次连接从当前 target 的 in-flight 轮次生成 round.active 快照，频道 / DM / 任务均可恢复真实句柄。
- DM 未启记忆时只补当前会话卸载目录的 Read；启记忆时同一个 Read 覆盖记忆与当前会话卸载，Write/Edit 仍只写记忆。其他会话和项目文件符号链接被 backend 拒绝；不增加项目根权限。
- 隔离红证据：`.scratch/review-task-ui-red.log`（2 failed）、`.scratch/review-reconnect-red.log`（3 failed）、`.scratch/review-reconnect-ui-red.log`（1 failed）、`.scratch/review-offload-red.log`（2 failed）。定向绿证据 `.scratch/review-*.log`，最终读数随报告留存。

## 交付

- PR：#207 `feat(runtime): AgentScope 2.0.10 运行时升级——历史窗口 / 压缩 / 中断 / 长期记忆 / schema 23`（base `main`；代码 head `9d7a665`，其上叠加本文档回填提交）。
  - https://github.com/whutlichao/alibot/pull/207
- PR 正文文件：`app/specs/agent-runtime-upgrade-pr-body.md`（按 `pr` 技能三段：Summary / Evidence / Merge Danger，含逐票 commit、门禁读数、review 基线 `3863e39` 与遗留项）。
- 提交清单：`14b9093` T1、`b647bff` T2、`cfbbcdd` T3、`33479a6` T4、`8841d09` T5、`9d7a665` 双轴 review 收口。
- 遗留：未跑真实付费模型的端到端；本机无 Docker daemon，Docker 相关用例 skipped（全量 10 skipped）；审批跨重启恢复与已结束轮的中断不在本次范围；浏览器验证只在临时数据目录完成。
