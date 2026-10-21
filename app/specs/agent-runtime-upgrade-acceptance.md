# Agent 运行时升级：验收与 Answer

规格正本：本次 Orca task `task_7586fc46fea4` 的 T1→T5（AgentScope 2.0.10）。
测试边界沿用规格明确指定的适配接缝、HTTP/SSE、迁移、Studio DOM；不调用付费模型。

## T1 上下文窗口

- 落点：`orchestration.py` 保留 fresh / pending / mentioned / signal / base_seq / mark_read 的判据，仅把历史装入 inputs；普通轮默认 40 条，续跑仍为完整历史。
- 红证据：隔离基线 `.scratch/red-t1`，`.scratch/t1-red.log`，2 failed；第二轮实际只有 `[3]`，应为 `[1, 2, 3]`。
- 验证：`scripts/check.sh --fast tests/test_context_window.py tests/test_agent_round.py tests/test_channel_join.py tests/test_task_continue.py`，57 passed；随后 `scripts/check.sh --fast`，静态检查全绿、Node 212 passed。
- 覆盖：同 target 第二轮含上一轮人与自己的回复、旧点名不成为新纪律、base_seq 与读位停在 3、环境覆盖窗口不截断轮次读取。

## T2–T5

实施中；本文件随每票提交更新，最终补完整门禁与双轴 review。
