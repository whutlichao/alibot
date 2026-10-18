# team 模式是频道的显式拓扑：队长收口、成员产出不落消息，autonomous 原样保留

用户于 2026-10-18 人定（D1 / D2 / D10 / D11 / D12 / D17）；D18 是第二轮 ask 的推荐默认（待确认）：同一个频道容器可以有两种**协作拓扑**。
**默认 `autonomous`** 保留现行的一切——消息落库后唤醒该 target 的全部 Agent 成员（排除作者）、由每个 Agent
自判是否回应、`auto_claim` 照旧生效。**频道可显式设 `team` 模式**，并由 owner 指定一个 Agent 作为**队长**：
频道消息只唤醒队长；队长通过进程内的 `TeamPipeline` 把任务分派给成员；**只有队长的最终回复落为频道消息**，
成员输出走 SSE 事件流与 `team_assignment` 运行记录，不占频道消息、未读、搜索。DM 恒为 autonomous。

## 一、保留了什么

- **`channel` 仍是消息容器**（D9）：team 不是新的 target kind，也不是框架的 IM `deploy/channel`；没有第二套频道概念。
- **成员仍是独立成员**：`member` 行、`target_member` 成员面、状态点、Agent 配置、可用性判定一个字不改。team 只改
  “一条频道消息唤醒谁”，不改身份与权限。
- **autonomous 全部保留**：全成员唤醒、自判、`auto_claim`、认领开世代、任务五态、DM 无 agent→agent 循环，全部按既有 ADR 执行。
- **穿透仍是一次性送达**（D17）：@ 未加入频道的 Agent 仍在 pipeline 外被送达；它要回复就按既有规则加入并落一条
  频道消息。D17 的边界是：**D2 只约束 pipeline 成员输出**，不把穿透直答也算成 pipeline 产出。
- **一轮至多一条频道消息**：队长仍受这条约束；成员产出不是消息，因此不产生“第二条”。

## 二、扩展了什么

- **唤醒面按模式分支**：team 频道的 human 消息只唤醒队长；队长自己的最终回复**零唤醒**；system 记录仍零唤醒；
  task 型 target 仍按既有任务语义唤醒（D23），不因所属频道是 team 而改变。
- **成员产出可见性**：`TeamPipeline` 的成员最终回复本来就以 `ToolResult` 回到队长，不会自动成为频道消息；
  本决策把它固化成契约：成员的进展只经 `agent_event`，成员结论只经 `team_assignment`，不写 `message`。
- **动态成员**（D11）：pipeline 每轮按当前频道成员重建，加入/移除不要求重启；轮次中途的变化从下一轮生效。
- **内部命名**（D10）：`TeamPipeline` 要求成员名唯一且不等于队长名，而本仓 `display_name` 不唯一。pipeline 内部名由
  **member id 派生**（例如 `m-<id>`），显示名仍只用于界面与 prompt；派生名不落库、不显示、不改身份。

## 三、被取代或收窄的既有口径

- **“成员平等自主”在 team 模式下被收窄，不是被删除**：成员不再直接响应频道消息，但仍是独立成员；它的自主性
  体现在被队长分派后如何完成、如何回应队长，而不是“所有成员同时被唤醒”。autonomous 下这条词条原意不变。
- **“一轮一消息”被扩展**：队长的轮次仍至多一条消息；成员的一轮不是频道消息轮次，产出走运行记录。既有
  `round_outcome` 六态仍只描述队长轮次，不为成员新增状态词。
- **“Agent 不做文件与 shell”不在本 ADR 内处置**：它由 workspace 决策（ADR-0021）扩展；team 模式本身不赋予文件能力。

## 四、队长不可用

本仓没有“移出频道”端点，只有 Agent 被禁用或软删除。team 频道在队长不可用时**不回落 autonomous**（D18）：
消息照常落库，但不唤醒任何人；`GET /api/channels/{id}/config` 的 `leader_available=false` 与 `leader_reason`
给出可读原因，状态点仍是可解释的后端事实。owner 必须启用或改选队长。选“静默失败”而不是“偷偷全员唤醒”，
是因为后者会让 team 频道的预算与可见性契约在用户不知情时改变。

## 五、被否方案与代价

- **默认 team**：会让现有频道的行为、预算与消息面在升级后改变；默认必须保持 autonomous。
- **team 模式下成员回复也落频道消息**：直接违反“只落队长最终回复”，并且会把 pipeline 内部过程变成未读与搜索噪音。
- **用 display_name 做 pipeline 内部名**：本仓显示名可重复，构造 `TeamPipeline` 会抛 `ValueError`；派生名是唯一稳定键。
- **队长不可用时回落 autonomous**：静默改变拓扑；用户以为在 team 模式，实际全员被唤醒。
- **把 team 做成新的 target kind / 框架 deploy/channel**：引入第二套频道与身份语义，违反 D9。

**代价**：成员过程不再有 `message` 行，事后只能查 `team_assignment` 与 live SSE；SSE 断开期间的过程不补发；
队长不可用时频道会沉默直到人处理；team 模式的分派成本可能高于 autonomous（见 ADR-0019）。
