/* 成员名单的呈现派生（纯函数，读后端镜像，不做判定）

   两件事在阶段 A 的 diff 里各被写了不止一遍（`RailSidebar.vue` 与 `RosterModal.vue` 的
   `statusOf()` 同形；`kind === 'agent'` 的过滤在 rail / 名册 / 新建频道 / 新建 Agent 四处出现）。
   重复的形状在改动时一定会漂，因此收成一处：**只在这里判「这个成员是不是 Agent」与
   「它的状态点该显示什么」**，两个调用方都调它。

   判据全部是后端事实：`state.agents[].status`（`GET /api/workspace` 的 agent 行）优先、
   其次 `state.statuses[memberId]`（`GET /api/member-statuses`）、都没有就是 `offline`。
   不从会话存活、不从 `kind` 反推。 */

/** 一个成员是不是 Agent（`owner` 不是）。 */
export function isAgentMember(member) {
  return Boolean(member) && member.kind === 'agent';
}

/** 名单里的全部 Agent（保持后端给的顺序）。 */
export function agentMembers(members) {
  return (members || []).filter(isAgentMember);
}

/** 状态点的读数：agent 行给的 `status` 优先，其次状态表，最后 `offline`。 */
export function memberStatus(state, member) {
  const agents = (state && state.agents) || [];
  const agent = agents.find((item) => item.id === member.id);
  if (agent && typeof agent.status === 'string') return agent.status;
  const statuses = (state && state.statuses) || {};
  return statuses[member.id] || 'offline';
}
