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
- 备注：会话恢复时宿主误将原 Dispatch 标为 failed。coordinator 已明确指示不再修汇报通道、继续实现，review 基线固定为 3863e39，最终报告另写 `/tmp/alibot-agent-runtime/T5-report.md`。

## T4–T5

实施中；最终补完整门禁与双轴 review。
