# 历史窗口、上下文压缩与显式长期记忆

普通轮过去只把消费游标之后的新消息交给全新 Agent，跨轮无法接上自己的回复。采用默认最近 40 条的上下文窗口、AgentScope 2.0.10 的阈值压缩与本地卸载，以及成员显式开启的 AgenticMemoryMiddleware；回应纪律、唤醒、base_seq 与标读基线仍只看新消息，ADR-0027 的自动续跑继续使用完整 target 历史。

选择窗口而非每轮完整历史，是为了限制常规输入增长；选择压缩与可读卸载，是为了在工具循环增长时保留可续接摘要与完整取证文件。轮次仍各自创建 Agent，但结束时原子保存 AgentState，下一轮恢复摘要、工具缓存与中间件状态；消息 context 按本轮窗口重建，避免快照与数据库历史重复拼接，权限始终按当前配置重新装配。

长期记忆选择 Markdown 文件与框架 AgenticMemoryMiddleware，不引入 ReMe、Mem0 或外部服务。文件放在 `<root>/.alibot/memory/<member_id>/Memory/`：已配置工作区用该项目根，DM 使用数据目录，同一根内跨 target 的会话按成员共享；不同项目根不混用记忆。关闭时不建目录、不装中间件。DM 的 Write/Edit 及额外工作目录只覆盖记忆目录；Read 另可读取当前会话的卸载目录，不能读取其他会话或项目文件；记忆编辑权限在此范围内单独授予，仍执行显式 deny/ask 规则，不放宽项目文件权限。

新增的轮次结束原因独立于 replied / ignored / failed 等结果状态，旧行保持 NULL。达到迭代上限保留可能不完整的结论并明确提示；用户中断不写消息，按当前 target 与真实 reply_id 取消运行任务或结束挂起审批，finally 关闭 MCP、mount 与 model client，发出 live-only 的 round.interrupted。SSE 每次连接补一份当前 round.active 进程内快照，恢复真实 reply_id 与停止按钮；任务线程按轮次结果 id 去重恢复结束文案。审批超时仍沿既有 failed 口径。Docker 不可用时只保留对话与可读降级，不切换为本地项目工具，修订 ADR-0021 对整轮失败的旧运行时口径。

Schema 22→23 同时增加可空 finished_reason 与上下文窗口、压缩阈值、迭代上限、记忆档四个配置列。显式配置优先，null 跟随环境或默认，省略字段保持原值；默认 40 / 0.7 / 30 / off。升级仍经备份与显式迁移，不在运行中的真实数据目录上自动执行。

框架接口依据：[Agent 配置](https://docs.agentscope.io/zh/versions/2.0.10/building-blocks/agent/configure-agent)、[上下文](https://docs.agentscope.io/zh/versions/2.0.10/building-blocks/context/overview)、[中断](https://docs.agentscope.io/zh/versions/2.0.10/building-blocks/agent/interrupt-agent)、[长期记忆](https://docs.agentscope.io/zh/versions/2.0.10/building-blocks/long-term-memory)；本次实现同时核对本机安装的 2.0.10 源码。
