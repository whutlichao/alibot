> ## Documentation Index
> Fetch the complete documentation index at: https://docs.agentscope.io/llms.txt
> Use this file to discover all available pages before exploring further.

> ## Agent Instructions
> For AgentScope Python, use https://docs.agentscope.io/stable/en/index for new projects. For existing projects, check the installed agentscope version and use matching versioned documentation.
> The /latest/ alias points to the newest documentation and may move ahead of /stable/ when a development version exists. Do not mix AgentScope 1.x and 2.x APIs.
> State the AgentScope version when providing installation commands or code examples. ReMe uses its own continuously updated /reme/latest/ documentation.

# AgentScope 2.0 是什么？

> 安全、高效、灵活、完备

AgentScope 2.0 是一款安全、高效、灵活且完备的生产级智能体框架——并将**多租户**、**多会话管理**和**分布式部署**作为一等公民原生支持。

> [!NOTE]
>
> AgentScope 2.0 与 1.0 之间存在破坏性变更，进行了全方位的改进。我们强烈建议现有用户迁移至 2.0 版本，以充分利用这些全新特性与体验升级。

* **安全**：依托特定工具审计、人机协同（Human-in-the-Loop）权限系统与沙箱安全隔离，构筑三位一体的安全防线。
* **高效**：依据工具特性柔性编排，支持多工具并发或顺序智能调度，在保证正确性的前提下大幅提升执行效率。
* **灵活**：借助智能体与工具双重中间件，无需修改核心逻辑，即可无侵入地定制和重塑智能体运行时行为。
* **完备**：覆盖从轻量级 UI、开箱即用 SDK，到支持多租户、多会话并发管理后端及分布式部署的完整工具链。

## 全栈能力，生产级打磨

从单智能体推理到复杂企业级应用，AgentScope 为开发者提供覆盖其完整生命周期的全栈支持。

> **[ReAct 智能体](/zh/versions/2.0.10/building-blocks/agent/overview)**
>
> 具备自主 ReAct 推理和多工具协同执行能力的智能体，内置人机协作审核与高并发工具调度机制。

> **[多维工具包](/zh/versions/2.0.10/building-blocks/tool/overview)**
>
> 智能体自主管理的工具栈体系，支持原生 Python 函数、MCP（模型上下文协议）和多种外部技能（Skill）的无缝拼装。

> **[上下文管理](/zh/versions/2.0.10/building-blocks/context/overview)**
>
> 精细化的上下文剪裁、卸载与智能体主动检索，深度融合了 Mem0、ReMe 等第三方记忆实现。

> **[安全沙箱隔离](/zh/versions/2.0.10/building-blocks/workspace/overview)**
>
> 支持在物理隔离的安全环境（如本地隔离沙箱、Docker、E2B、Kubernetes 等）中安全执行外部代码，支持用户、智能体、会话三级精细隔离。

> **[智能体即服务](/zh/versions/2.0.10/deploy/agent-service)**
>
> 一键部署的生产级后端，自带可视化前端与开发 SDK，多租户多会话并发与分布式集群调度触手可得。


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.