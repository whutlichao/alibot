/* 任务线程的读数与写路径（票 #307：dock 的第二形态）

   判据正本是旧 `studio/app.js` 的 `openTask()` / `renderTaskThread()` / `sendTaskMessage()` /
   `saveTaskTitle()` / `changeTaskAssignee()` / `markTaskRead()` 那一族。这一层只做三件事：

   1. **读**：打开线程读 `GET /api/tasks/{id}/messages`（响应含 `task` + `messages`），并顺带补
      两条呈现所需的后端读数——所属**频道**的成员表（指派候选，旧 `renderAssignChips` 的理由：
      task target 的成员表只有 {owner} ∪ {当前指派者}，拿它当候选就永远换不了人）与所属频道的配置
      （team 提示要它）。
   2. **写**：回复 / 改标题 / 推状态 / 改指派四条路径。它们都只改后端，然后把回带的最新任务行与
      `system` 变更记录吸收进来（前端不猜「大概改成功了」）。
   3. **作废**：`resetForTarget()` 把「这是哪条任务」与它的消息一起清掉——换 target / 关线程时
      不能留下上一条线程的读数。

   **形态不在这里**：`dockTarget` 是 dock 形态的唯一判据（`App.vue`），store 只回答「我这条线程
   现在读到什么」。于是「关掉了但 dock 还停在旧形态」在构造上不存在。

   状态迁移的合法集合仍只读载荷 `allowed_transitions`（`lib/task-board.js` 的 `canTransition`），
   与看板拖拽、卡片「流转」菜单是同一条判据、同一个后端守卫。 */
import { reactive } from 'vue';
import { api, taskPath } from '../lib/api.js';
import { createEventStream, isLiveEventType } from '../lib/event-stream.js';
import { t } from '../i18n/index.js';
import {
  taskStatusLabel,
  taskTransitionLabel,
  transitionGuard,
  transitionInvalidMessage,
} from '../lib/task-board.js';

/**
 * @param {object} options
 * @param {() => boolean} [options.online]        在线读数（离线时写入口不发请求）
 * @param {(message: string, kind?: string) => void} [options.toast] 失败提示的落点
 * @param {(task: object|null, message: object|null) => void} [options.onTaskChanged]
 *        任务行变化后的同步落点（`App.vue` 据此更新看板快照：页签计数与卡片状态立刻跟着变）
 * @param {(channelId: string) => Promise<any>} [options.refreshChannelMembers] 频道成员表的既有读路径
 * @param {(channelId: string) => Promise<any>} [options.loadChannelConfig] 频道配置的既有读路径
 * @param {() => typeof EventSource} [options.sourceFactory] 任务线程事件流的构造器（测试替身入口；
 *        缺省用浏览器原生 `EventSource`，与全局流同一条注入路径）
 * @param {(payload: object, type: string) => void} [options.onLiveFrame] live-only 运行事件的落点
 *        （`App.vue` 转给 `liveLog.record()` 与运行面板的 SOP 簿记；store 不自己写那份日志）
 * @param {(payload: object, type: string) => void} [options.onTaskFrame] 其余可补拉帧的落点
 *        （`typing` / `member_status` / `round_outcome`：语义与全局流那一侧同一套，由调用方处理）
 * @param {(targetId: string) => void} [options.onStreamOpen] 任务流连上（重连成功后按快照兜底）
 */
export function createTaskThreadStore(options) {
  const config = options || {};
  const online = config.online || (() => true);
  const toast = config.toast || (() => {});
  const onTaskChanged = config.onTaskChanged || (() => {});
  const refreshChannelMembers = config.refreshChannelMembers || (async () => null);
  const loadChannelConfig = config.loadChannelConfig || (async () => null);
  const sourceFactory = config.sourceFactory || null;
  const onLiveFrame = config.onLiveFrame || (() => {});
  const onTaskFrame = config.onTaskFrame || (() => {});
  const onStreamOpen = config.onStreamOpen || (() => {});

  const state = reactive({
    // 现在打开着哪条任务（`null` = 没有线程）。
    taskId: null,
    // 任务行（可变的：标题 / 状态 / 指派者）+ 它的消息流（append-only）。
    task: null,
    messages: [],
    loading: false,
    error: '',
    sending: false,
    // 发送失败的可读原因：铺在 composer 上方，草稿留在输入框里（重试发的是同一句话）。
    sendError: '',
  });

  /** 每次打开 / 作废都推进：晚到的响应不能把已经换掉的线程铺回来（旧 `activeTaskId !== taskId` 早退）。 */
  let version = 0;

  /* ─── 任务线程自己的事件流（旧 `connectTaskStream()`，`studio/app.js:2386-2421`） ───

     为什么线程需要**第二条**流（票 #307 补做）：全局流管主线（频道 / 私信），它的帧按
     `target_id` 分流；而任务线程的 `message` 帧后端**不带 `target_id`**（单 target 订阅，
     `api.py` 的 `task_events` 只写 `type` / `seq` / `message`），全局流那条「缺 target_id 就丢弃」
     的判据会把这些帧全扔掉。因此任务线程按旧页面同一条做：打开线程时建
     `GET /api/tasks/{id}/events?after_seq=<当前最大 seq>`，关闭即销毁。

     两条流**分工不同、不是二选一**：全局流常驻、管主线；任务流只在打开线程时存在，
     关闭就 close + 清 timer（关掉后不许有在途流）。重连 / rotate 的语义与全局流同一套——
     因此这里复用 `createEventStream()` 的状态机（`url` 每次重连都按当前游标求值，
     `requireTargetId: false` 表示这是单 target 订阅）。 */

  /** 现在这条任务流（`null` = 没有线程开着，或已销毁）。 */
  let taskStream = null;

  /** 线程流的端点：`after_seq` 取**当前**最大 seq，于是每次重连都是「从我看到的地方补」。 */
  function streamUrl(taskId) {
    return taskPath(taskId, '/events') + '?after_seq=' + String(maxSeq());
  }

  /**
   * 一条任务帧的落点（旧 `handleTaskStreamEvent()` 的判据）。
   *
   * - `message`：按 seq 去重吸收进线程消息，并重读任务详情（未读 / 状态可能变了）；
   * - live-only（`agent_event` / `team_assignment` / `plan_update` / `sop_event` / `approval_*` …）：
   *   交给 `onLiveFrame`（`App.vue` 写 `liveLog` 与运行面板簿记），**不进消息流 / 未读 / 搜索**；
   * - `typing` / `member_status` / `round_outcome`：交给 `onTaskFrame`（与全局流同一套语义）；
   * - 未知 type：忽略，不报错、不推进任何游标。
   *
   * `target_id` 的取法与旧页面逐字一致：帧里的 → 当前任务的 → 任务 id。
   */
  function handleTaskFrame(payload, type) {
    if (!payload) return;
    const targetId = payload.target_id
      || (state.task && state.task.target_id)
      || state.taskId;
    if (type === 'message') {
      if (!payload.message) return;
      // 帧可能属于**已经换掉**的线程（关掉/切走之后才到）：按 target 与 seq 双重收口。
      if (!absorb(payload.message)) return;
      refreshTaskDetails();
      markRead(payload.message.seq);
      return;
    }
    if (isLiveEventType(type)) {
      onLiveFrame({ ...payload, target_id: targetId }, type);
      return;
    }
    if (type === 'typing' || type === 'member_status' || type === 'round_outcome') {
      onTaskFrame({ ...payload, target_id: targetId }, type);
    }
    // 未知 type：忽略。
  }

  /** 重读「任务行」（不是整条线程）：`message` 帧之后未读与状态可能变了，但消息已经就地吸收。 */
  function refreshTaskDetails() {
    const taskId = state.taskId;
    if (!taskId) return;
    const current = version;
    api(taskPath(taskId))
      .then((payload) => {
        // 线程已经换掉 / 关掉 ⇒ 这份读数不属于任何人的界面。
        if (current !== version || !isOpenFor(taskId) || !payload || !payload.task) return;
        state.task = payload.task;
        onTaskChanged(state.task, null);
      })
      .catch(() => {
        // 读不到保留上一份：线程正文仍是对的，只是未读数略陈旧。
      });
  }

  /** 建流（只在**打开**一条线程之后调）。已有的流先销毁——不许两条并存。 */
  function connect(taskId) {
    closeStream();
    if (!taskId || !isOpenFor(taskId)) return;
    const stream = createEventStream({
      // 单 target 订阅：后端在这些帧上不带 `target_id`，因此不套全局流那条丢弃判据。
      requireTargetId: false,
      url: () => streamUrl(taskId),
      // `sourceFactory` 本身就是一个「返回构造器」的函数（与全局流同一条注入约定）：
      // 直接传，不要再包一层（包了会把 `new (箭头函数)` 变成「不是构造器」）。
      sourceFactory: sourceFactory || undefined,
      onFrame: (payload, type) => {
        // 晚到的帧不属于现在这条线程：直接丢（切线程 / 关线程之后到达的那一批）。
        if (!isOpenFor(taskId)) return;
        handleTaskFrame(payload, type);
      },
      onOpen: (info) => {
        if (!isOpenFor(taskId)) return;
        // 任务 target 的 live 帧同样不补拉：重连后按运行面板快照兜底（旧 `source.onopen` 同一条）。
        onStreamOpen(state.task ? state.task.target_id : taskId, info);
      },
    });
    taskStream = { taskId, stream };
    try {
      stream.start();
    } catch {
      // 构造器不可用（极旧浏览器 / 替身缺失）：线程照常可读可写，只是没有实时追加。
      // 这一条**不**回落到全局流——任务帧不带 `target_id`，全局流会按判据把它们丢掉。
      taskStream = null;
    }
  }

  /** 销毁这条流：`close()` + 清重连 timer。关掉之后不许有在途流（票面第 2 条）。 */
  function closeStream() {
    const entry = taskStream;
    taskStream = null;
    if (!entry) return;
    // `stop()` 里同时清 timer 与 close source（`createEventStream()` 的既有收尾）。
    entry.stream.stop();
  }

  function clear() {
    // 作废线程 = 同时销毁它的流（旧 `closeTaskStream()` 在 `closeTaskDock()` / `selectTarget()`
    // 的每一条路径上都被调过）。放在这里，任何清空读数的路径都不会漏掉这一笔。
    closeStream();
    state.taskId = null;
    state.task = null;
    state.messages = [];
    state.loading = false;
    state.error = '';
    state.sending = false;
    state.sendError = '';
  }

  /** 换 target / 关线程：整个作废（旧 `closeTaskDock()` 的 `taskMessages = []` 那一段）。 */
  function resetForTarget() {
    version += 1;
    clear();
  }

  /** 线程的最大 seq：标读与「本次已见过」的游标都读它。 */
  function maxSeq() {
    return state.messages.reduce((max, message) => Math.max(max, Number(message.seq) || 0), 0);
  }

  function isOpenFor(taskId) {
    return Boolean(taskId) && state.taskId === taskId;
  }

  /** 任务行与变更记录就位：写路径与 live 帧共用这一条（顺序 = seq 顺序，去重按 seq）。 */
  function applyTask(task, message) {
    if (task && task.id) {
      state.task = task;
      state.taskId = task.id;
    }
    absorb(message);
    onTaskChanged(state.task, message || null);
  }

  /** 吸收一条线程消息（**按 seq 去重**：live 帧与写路径的回带可能各来一次）。 */
  function absorb(message) {
    if (!message || !Number.isInteger(message.seq)) return false;
    if (state.task && message.target_id && message.target_id !== state.task.target_id) return false;
    if (state.messages.some((item) => item.seq === message.seq)) return false;
    state.messages.push(message);
    state.messages.sort((a, b) => a.seq - b.seq);
    return true;
  }

  /**
   * 标读（`POST /api/tasks/{id}/read`）。
   *
   * 打不开线程时标读失败不是主链路的错：它只影响未读角标，因此失败**不抛**、不改线程读数
   * （旧 `markTaskRead()` 只 toast）。前端不自己算未读——正本在库。
   */
  async function markRead(seq) {
    const taskId = state.taskId;
    if (!taskId || !seq) return null;
    try {
      const payload = await api(taskPath(taskId, '/read'), { method: 'POST', body: { seq } });
      if (!isOpenFor(taskId)) return null;
      if (payload && payload.task) {
        state.task = payload.task;
        onTaskChanged(state.task, null);
      }
      return payload;
    } catch (err) {
      toast(t('toast.taskReadFailed', { reason: err.message }), 'err');
      return null;
    }
  }

  /**
   * 打开一条线程（点卡片 / 命令面板的入口）。
   *
   * 读失败要**如实收口**：`error` 一铺，dock 就回落到「没有形态」（`App.vue` 的 `openTask()`
   * 据 `null` 返回值关掉 dock 并说明原因）——不然人看到的是一个空壳线程。
   */
  async function open(taskId) {
    if (!taskId) return null;
    const current = ++version;
    clear();
    state.taskId = taskId;
    state.loading = true;
    try {
      const payload = await api(taskPath(taskId, '/messages'));
      if (current !== version) return null;
      state.task = payload.task || null;
      state.messages = Array.isArray(payload.messages) ? payload.messages.slice() : [];
      state.messages.sort((a, b) => a.seq - b.seq);
      state.loading = false;
      state.error = '';
      onTaskChanged(state.task, null);
      const task = state.task;
      if (task && task.channel_id) {
        // 两条读数互不依赖；各自失败时自己收口（成员表读不到 → 指派 chip 说「读不到」，
        // 配置读不到 → 不铺 team 提示）。
        await Promise.all([refreshChannelMembers(task.channel_id), loadChannelConfig(task.channel_id)]);
      }
      await markRead(maxSeq());
      // 读到快照之后才建流：`after_seq` 要的是**已经拿到**的最大 seq，早建会从头重放一遍。
      connect(taskId);
      return state;
    } catch (err) {
      if (current !== version) return null;
      state.loading = false;
      state.error = err && err.message ? err.message : String(err);
      return null;
    }
  }

  /** 重读一条已打开的线程（live 消息落库后补一次「任务行」的读数：未读与状态都可能变了）。 */
  async function refresh() {
    const taskId = state.taskId;
    if (!taskId) return null;
    const current = ++version;
    try {
      const payload = await api(taskPath(taskId, '/messages'));
      if (current !== version || !isOpenFor(taskId)) return null;
      state.task = payload.task || state.task;
      state.messages = Array.isArray(payload.messages) ? payload.messages.slice() : [];
      state.messages.sort((a, b) => a.seq - b.seq);
      return state;
    } catch {
      // 保留上一次已知读数：刷新失败不该把正在看的东西清空。
      return null;
    }
  }

  /**
   * 回复一条线程（`POST /api/tasks/{id}/messages`，body 必须带 `task_ref`）。
   *
   * 失败**不吞**：`sendError` 留给呈现层，草稿留在输入框里，重试走同一条路径。
   * `task_ref` 是票面「断言请求 target」的那一位——它让后端把这条消息与线程绑定。
   */
  async function send(text) {
    const task = state.task;
    const body = String(text == null ? '' : text).trim();
    if (!task || !body) return { ok: false };
    if (!online()) return { ok: false, offline: true };
    state.sending = true;
    state.sendError = '';
    try {
      const payload = await api(taskPath(task.id, '/messages'), {
        method: 'POST',
        body: { body, task_ref: task.id },
      });
      state.sending = false;
      absorb(payload.message);
      await markRead(payload.message.seq);
      return { ok: true, message: payload.message };
    } catch (err) {
      state.sending = false;
      state.sendError = err && err.message ? err.message : String(err);
      return { ok: false, error: err };
    }
  }

  /** 标题就地保存（`PATCH /api/tasks/{id}/title`）。空标题 / 未改动 ⇒ 不发请求。 */
  async function saveTitle(title) {
    const task = state.task;
    const next = String(title == null ? '' : title).trim();
    if (!task || !next || next === task.title) return false;
    if (!online()) return false;
    try {
      const payload = await api(taskPath(task.id, '/title'), { method: 'PATCH', body: { title: next } });
      applyTask(payload.task, payload.message);
      return true;
    } catch (err) {
      toast(t('toast.titleUpdateFailed', { reason: err.message }), 'err');
      return false;
    }
  }

  /**
   * 推进状态（`PATCH /api/tasks/{id}/status`）。
   *
   * 前端守卫只读载荷：目标不在 `allowed_transitions` 里就**不发请求**，但把「不是合法迁移」说
   * 出来（静默什么都不发生，人只会以为界面坏了）。与看板拖拽、卡片菜单是同一条判据。
   */
  async function changeStatus(target) {
    const task = state.task;
    const guard = transitionGuard(task, target);
    if (!guard.ok) {
      if (guard.reason === 'illegal') toast(transitionInvalidMessage(task, target), 'err');
      return false;
    }
    if (!online()) return false;
    try {
      const payload = await api(taskPath(task.id, '/status'), { method: 'PATCH', body: { status: target } });
      applyTask(payload.task, payload.message);
      return true;
    } catch (err) {
      toast(t('toast.statusUpdateFailed', { reason: err.message }), 'err');
      return false;
    }
  }

  /** 改指派（`PATCH /api/tasks/{id}/assignee`，取消指派传 `null`）。 */
  async function changeAssignee(memberId) {
    const task = state.task;
    if (!task) return false;
    if (!online()) return false;
    if ((task.assignee_member_id || null) === (memberId || null)) return false;
    try {
      const payload = await api(taskPath(task.id, '/assignee'), {
        method: 'PATCH',
        body: { member_id: memberId || null },
      });
      applyTask(payload.task, payload.message);
      return true;
    } catch (err) {
      toast(t('toast.assigneeUpdateFailed', { reason: err.message }), 'err');
      return false;
    }
  }

  return {
    state,
    open,
    refresh,
    resetForTarget,
    isOpenFor,
    maxSeq,
    absorb,
    closeStream,
    /** 现在有没有在途的任务流（用例断言「关掉后不许有在途流」读它）。 */
    streamOpen: () => Boolean(taskStream),
    markRead,
    send,
    saveTitle,
    changeStatus,
    changeAssignee,
    // 词条说法由 store 一并给出：组件不必各自 import 两套词条函数（一处取值，一处口径）。
    statusLabel: taskStatusLabel,
    transitionLabel: taskTransitionLabel,
  };
}
