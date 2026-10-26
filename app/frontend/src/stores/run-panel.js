/* 运行面板的读数与呈现状态（dock 的**第三种形态**）

   判据正本是旧 `studio/app.js` 的 `runPanel*` 族 + `app/specs/agentscope-2-alignment.md` 的 D13。
   这一层只做三件事：读快照、持有「面板现在开在哪个 target 上」、把 live 日志与本 target 的快照
   交给 `lib/run-panel.js` 合并。合并判据一条都不在这里（那里是纯函数，可以逐条钉用例）。

   两层数据的职责（ADR-0020）：
   - **快照**是断线期间变化的唯一兜底，打开面板 / 刷新 / 重连成功时读；
   - **live 帧**只活在本会话内存里（`lib/live-events.js`），断线不补。

   为什么面板的形态**不单独存一份**（旧实现同一条）：形态由「现在打开着哪一个对象」推导，
   `open` 为真就是这一形态，关掉就落回别的形态。于是「关闭了但 dock 还停在旧形态」这类漂移
   在构造上不存在。

   **与旧实现的一处刻意差异**：旧版每次写状态后手动调 `renderRunPanel()`；这里状态是响应式的，
   组件按依赖重渲染。手动重铺漏一处就是「面板不刷新」，而顺手多铺一次会白白重建整棵子树。 */
import { computed, reactive } from 'vue';
import { api } from '../lib/api.js';
import {
  RUN_PANEL_RUN_LIMIT,
  channelMode,
  overlayEvents,
  panelLiveEvents,
} from '../lib/run-panel.js';

/**
 * @param {object} options
 * @param {object} options.liveLog        `createLiveEventLog()` 的返回值
 * @param {object} options.channelConfigs `channelId → config` 的只读缓存（协作模式空态解释用）
 * @param {() => object|null} options.activeTask 当前打开的任务线程（任务面板的标题 / 过滤来源）
 * @param {() => object|null} options.activeChannel 当前打开的频道
 * @param {() => object[]} [options.channels] 频道名单（标题取名用；缺省用空表，标题退到 id 尾段）
 * @param {(targetId: string) => Promise<any>} options.loadChannelConfig 频道配置的既有读取路径
 * @param {(message: string) => void} [options.onRefresh] 快照落地后的回调（滚动 / 其它呈现善后）
 */
export function createRunPanelStore(options) {
  const config = options || {};
  const liveLog = config.liveLog;
  const channelConfigs = config.channelConfigs || reactive({});
  const activeTask = config.activeTask || (() => null);
  const activeChannel = config.activeChannel || (() => null);
  const loadChannelConfig = config.loadChannelConfig || (async () => null);

  const state = reactive({
    open: false,
    targetId: null,
    channelId: null,
    taskId: null,
    loading: false,
    error: '',
    runs: [],
    plan: [],
    sops: [],
    sopDefs: {},
    // 本次会话见过的 SOP run id（SOP 没有「按 target 列 run」的读面，live 帧是 id 的唯一来源）。
    sopIds: [],
    // 快照落地时的「最后一个已知 live 帧」：Plan / SOP 的 live 覆盖只应用它之后的帧。
    overlayMarker: null,
    // 每次打开 / 关闭都推进：晚到的响应不能把已经换掉的面板铺回来。
    version: 0,
  });

  /** 面板的标题：任务面板用任务标题，频道面板用频道名（读不到就给 id 的尾段，不编名字）。 */
  const title = computed(() => {
    if (state.taskId) {
      const task = activeTask();
      return task ? task.title : '#' + String(state.taskId).slice(-6);
    }
    const channels = config.channels ? config.channels() : [];
    const channel = channels.find((item) => item.id === state.channelId);
    return channel ? channel.name : String(state.channelId || '');
  });

  /** 自主模式频道：运行与 Plan 的空态要解释「为什么不产生」以及「去哪看」。 */
  const autonomous = computed(() => channelMode(state, channelConfigs) === 'autonomous');

  /** 本 target 的 live 帧（运行面板认的那一批）。 */
  const liveEvents = computed(() => panelLiveEvents(liveLog.forTarget(state.targetId)));

  /** 覆盖层用的 live 帧：只保留快照之后到达的 Plan / SOP 帧。 */
  const overlay = computed(() => overlayEvents(liveEvents.value, state.overlayMarker));

  function reset() {
    state.targetId = null;
    state.channelId = null;
    state.taskId = null;
    state.loading = false;
    state.error = '';
    state.runs = [];
    state.plan = [];
    state.sops = [];
    state.sopDefs = {};
    state.overlayMarker = null;
  }

  /** 换 target / 关任务线程时把面板整个作废：它的快照与 live 归属都只属于旧 target。 */
  function resetForTarget(close) {
    if (close) state.open = false;
    state.version += 1;
    reset();
  }

  function isOpenFor(targetId) {
    return state.open && Boolean(targetId) && state.targetId === targetId;
  }

  /**
   * 读一次快照。
   *
   * 三段是**串行**的（后端的依赖是真的）：`team-runs` + `plan` 并行 → 再按已知 `sop_run_id` 逐个读
   * run → 再按 run 的 `definition_id` 读定义（步骤名只在定义里）。串行的代价是刷新慢一点，
   * 收益是「读到一半抛错」不会留下半份读数——`error` 一铺，呈现层就整块换成错误态。
   */
  async function loadSnapshot() {
    if (!state.open || !state.targetId) return null;
    const version = ++state.version;
    const targetId = state.targetId;
    const taskId = state.taskId;
    const sopIds = state.sopIds.slice();
    state.loading = true;
    state.error = '';
    let succeeded = false;
    try {
      let runsUrl =
        '/api/targets/' + encodeURIComponent(targetId) + '/team-runs?limit=' + String(RUN_PANEL_RUN_LIMIT);
      if (taskId) runsUrl += '&task_id=' + encodeURIComponent(taskId);
      const [runsPayload, planPayload] = await Promise.all([
        api(runsUrl),
        api('/api/targets/' + encodeURIComponent(targetId) + '/plan'),
      ]);
      const sopPayloads = await Promise.all(
        sopIds.map((runId) => api('/api/sop-runs/' + encodeURIComponent(runId)).catch(() => null)),
      );
      // 步骤名在定义里（run state 只存 phase / verdict）：按 run 的 definition id 读定义。
      const definitionIds = sopPayloads
        .map((payload) => payload && payload.run && payload.run.sop_definition_id)
        .filter(Boolean)
        .filter((id, index, ids) => ids.indexOf(id) === index);
      const definitionPayloads = await Promise.all(
        definitionIds.map((id) => api('/api/sops/' + encodeURIComponent(id)).catch(() => null)),
      );
      // 晚到的响应不能把已经换掉的面板铺回来（切 target / 关掉 / 又打开别的 target）。
      if (version !== state.version || !state.open || state.targetId !== targetId) return null;
      state.runs = runsPayload.runs || [];
      state.plan = (planPayload.plan && planPayload.plan.members) || [];
      state.sops = sopPayloads.filter((payload) => payload && payload.run).map((payload) => payload.run);
      const defs = {};
      definitionPayloads.forEach((payload) => {
        if (payload && payload.sop && payload.sop.id) defs[payload.sop.id] = payload.sop;
      });
      state.sopDefs = defs;
      // 快照已经合并了此刻之前的所有变化：Plan / SOP 的 live 覆盖只应用它之后的帧。
      state.overlayMarker = liveLog.lastFrame(targetId);
      state.loading = false;
      state.error = '';
      succeeded = true;
    } catch (err) {
      if (version !== state.version || !state.open || state.targetId !== targetId) return null;
      state.loading = false;
      state.error = err.message;
    }
    if (succeeded && config.onRefresh) config.onRefresh();
    return succeeded ? state : null;
  }

  /**
   * 打开面板。`targetId` 是**数据归属**（频道 target 或任务 target），`channelId` / `taskId` 是
   * 「这是谁的运行」——任务面板还要用 `task_id` 过滤同一份 `team-runs`。
   */
  async function open(target) {
    const next = target || {};
    state.open = true;
    state.version += 1;
    reset();
    state.targetId = next.targetId || null;
    state.channelId = next.channelId || null;
    state.taskId = next.taskId || null;
    // 本次会话里已经见过的 SOP run：面板重开后仍要能按 id 拉快照。
    state.sopIds = state.targetId ? liveLog.sopRunIds(state.targetId) : [];
    loadSnapshot();
    // 频道面板的空态解释依赖协作模式：缓存未命中时按既有读取路径读一次；读失败保持未缓存，
    // 继续铺通用空态（**不**把读不到误报成自主模式）。
    if (state.channelId && !state.taskId && !channelConfigs[state.channelId]) {
      const channelId = state.channelId;
      await loadChannelConfig(channelId);
      if (!state.open || state.taskId || state.channelId !== channelId) return state;
    }
    return state;
  }

  function openForChannel() {
    const channel = activeChannel();
    if (!channel) return null;
    return open({ channelId: channel.id, targetId: channel.id, taskId: null });
  }

  /** 关闭：`open` 置假并把读数作废（下次打开是干净的一帧）。落回哪个形态由调用方决定。 */
  function close() {
    state.open = false;
    state.version += 1;
    reset();
  }

  /**
   * 重连成功后的兜底：只有面板开着、且这个 target 正是面板的 target 时才读。
   *
   * live 帧断线不补（ADR-0020），断线期间的分派 / Plan / SOP 变化只能靠这一份快照补齐；
   * 漏了它就是「面板看起来还在工作、读数永远停在断线前」。
   */
  function refreshFor(targetId) {
    if (!state.open || !state.targetId) return null;
    if (targetId && state.targetId !== targetId) return null;
    return loadSnapshot();
  }

  /**
   * 记一条 live 帧。**只有面板开着、且帧属于面板的 target 时**才记 SOP run id——
   * 别的时候 live 日志由应用层统一写（它在收帧时已经记过了），这里只补面板自己的簿记。
   */
  function noteSopFrame(payload) {
    if (!state.open || !payload) return;
    if (state.targetId !== (payload.target_id || state.targetId)) return;
    const runId = String(payload.sop_run_id || '');
    if (runId && !state.sopIds.includes(runId)) state.sopIds.push(runId);
  }

  return {
    state,
    title,
    autonomous,
    liveEvents,
    overlay,
    open,
    openForChannel,
    close,
    resetForTarget,
    isOpenFor,
    loadSnapshot,
    refreshFor,
    noteSopFrame,
  };
}
