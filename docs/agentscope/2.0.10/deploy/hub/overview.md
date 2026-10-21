> ## Documentation Index
> Fetch the complete documentation index at: https://docs.agentscope.io/llms.txt
> Use this file to discover all available pages before exploring further.

> ## Agent Instructions
> For AgentScope Python, use https://docs.agentscope.io/stable/en/index for new projects. For existing projects, check the installed agentscope version and use matching versioned documentation.
> The /latest/ alias points to the newest documentation and may move ahead of /stable/ when a development version exists. Do not mix AgentScope 1.x and 2.x APIs.
> State the AgentScope version when providing installation commands or code examples. ReMe uses its own continuously updated /reme/latest/ documentation.

# 概览

> 快速接入不同来源的 MCP 与技能中心。

MCP 与技能中心指的是以代码方式实现在 [智能体服务](/zh/versions/2.0.10/deploy/agent-service) 中注册 MCP 与技能来源，并在对话中分配给指定的智能体服务。
该“中心”可以来自受信的第三方服务，也可以来自自定义实现。

按资源类型区分，分为 MCP 和技能两类：

> **[MCP 中心](/zh/versions/2.0.10/deploy/hub/mcp-hub)**
>
> 安装 [MCP](/zh/versions/2.0.10/building-blocks/tool/mcp) 服务，并配置凭证。

> **[技能中心](/zh/versions/2.0.10/deploy/hub/skill-hub)**
>
> 安装他人发布的 [技能](/zh/versions/2.0.10/building-blocks/tool/skill)。

## 工作方式

安装与装配是两个独立的步骤，因此用户只需配置一次，之后处处可用：

**1. 安装到自己的资源池**

用户浏览某个中心并安装 MCP 服务或技能，填写的凭证一并保存下来。所有中心汇入同一个资源池，装完之后就不必再关心它来自哪个来源。

**2. 装配给智能体的工作区**

在会话中，用户从资源池里挑选，把它加入该智能体的 [工作区](/zh/versions/2.0.10/deploy/workspace-manager)，其工具随即对智能体可见。

资源池属于用户，而不属于某一次会话。同一个 MCP 服务可以装配到十个不同的工作区而无需重复填写密钥，也不会随任何一次会话结束而消失。

> [!TIP]
>
> MCP 和技能中心是可选的。即使一个都不注册，用户依然可以通过粘贴配置来添加 MCP 服务，通过上传文件夹来添加技能。

## 延伸阅读

> **[智能体服务](/zh/versions/2.0.10/deploy/agent-service)**
>
> MCP 与技能中心所依托的托管层。

> **[工作区管理](/zh/versions/2.0.10/deploy/workspace-manager)**
>
> 装配好的 MCP 服务与技能的运行环境。


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.