/* live 事件的会话内日志（按 target 归属，有界保留）

   运行面板、审批卡这类**过程面**的数据源分两层（ADR-0020）：
   - **快照**（`GET /targets/{id}/team-runs` / `/plan` / `/sop-runs/{id}`）是断线期间变化的唯一兜底，
     打开面板与重连成功后重新读；
   - **live 帧**（`agent_event` / `team_assignment` / `plan_update` / `sop_event` …）只活在本次会话
     内存里，断线不补、也不可补（它们没有 seq）。

   因此这里既不是缓存也不是队列：它是「本次会话见过的过程」的**有界日志**，写进去就不再改，
   呈现层按后端 id 与快照合并（`lib/run-panel.js`）。

   两条纪律：
   1. **绝不进消息流 / 未读 / 搜索 / 游标**。`seenSeqs` 是标读与重连补拉的判据，live 帧没有 seq，
      给它记一次就会让全量重取的兜底少一条（旧实现里 `recordLiveEvent` 与 `handleStreamEvent` 的分叉
      就是这条）。
   2. **有界**：256 条 / target，超出的从头部丢。丢掉的后果是「运行面板里更早的过程看不到了」，
      而快照会兜住状态本身——所以宁可丢过程，不让内存随会话时长无限长。 */
import { reactive } from 'vue';

/** 与旧实现同值：`LIVE_EVENTS_LIMIT`。 */
export const LIVE_EVENTS_LIMIT = 256;

/** live-only 的事件种类（没有可补拉的 seq）。判据与 `lib/event-stream.js` 的 `LIVE_EVENT_TYPES` 同源。 */
export const LIVE_ONLY_TYPES = [
  'agent_event',
  'team_assignment',
  'plan_update',
  'sop_event',
  'approval_required',
  'approval_resolved',
  'round.interrupted',
  'round.active',
];

export function isLiveOnlyType(type) {
  return LIVE_ONLY_TYPES.includes(type);
}

/**
 * 建一份 live 日志。
 *
 * `Map<targetId, payload[]>` 装在 `reactive` 里：数组是响应式的，于是「运行面板开着时 new 帧到达」
 * 会自动重渲染——旧实现靠 `renderRunPanel()` 在每个调用点手动重铺，漏一处就是「面板不刷新」。
 */
export function createLiveEventLog() {
  const byTarget = reactive(new Map());

  /** 记一条 live 帧。缺 `target_id` 的帧在这里已经不该出现了（流侧丢弃），但仍然守住。 */
  function record(targetId, payload) {
    const key = targetId || (payload && payload.target_id);
    if (!key || !payload) return null;
    const events = byTarget.get(key) || [];
    events.push(payload);
    // 有界保留：live-only 事件不会补拉，保留旧事件只为本次会话的过程面。
    if (events.length > LIVE_EVENTS_LIMIT) events.splice(0, events.length - LIVE_EVENTS_LIMIT);
    byTarget.set(key, events);
    return payload;
  }

  function forTarget(targetId) {
    return byTarget.get(targetId) || [];
  }

  /** 本次会话里见过的 SOP run id（SOP 没有「按 target 列 run」的读面，live 帧是 id 的唯一来源）。 */
  function sopRunIds(targetId) {
    const ids = [];
    forTarget(targetId).forEach((event) => {
      if (event.type !== 'sop_event' || !event.sop_run_id) return;
      const id = String(event.sop_run_id);
      if (!ids.includes(id)) ids.push(id);
    });
    return ids;
  }

  /** 「最后一个已知帧」：快照读取成功后记下它，作为 Plan / SOP live 覆盖的下界（见 `overlayEvents`）。 */
  function lastFrame(targetId) {
    const events = forTarget(targetId);
    return events.length ? events.at(-1) : null;
  }

  function clear() {
    byTarget.clear();
  }

  return { byTarget, record, forTarget, sopRunIds, lastFrame, clear };
}
