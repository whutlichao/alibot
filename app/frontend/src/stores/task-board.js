/* 任务看板的读数与呈现状态（票 #306 的 store 层）

   三件事分开：

   1. **纯呈现派生**（`lib/task-board.js`）：状态词与动词的说法、载荷的 `allowed_transitions`、
      卡片序号与未读角标的形状。它们是纯函数，与网络、与 Vue 都无关。
   2. **本文件**：读快照、写路径、视图档、筛选项、拖拽态。**呈现状态**（`view` / `filterMine` /
      `filterChannelId` / `drag`）只住在内存里，**不进** `localStorage`（旧页面同一纪律：
      `app.js` 不把业务状态交给浏览器存储），也不进 `stores/workspace.js` 的后端镜像
      （那个 `state` 的每个字段都能在后端载荷里找到同名出点，这里没有）。
   3. **组件**（`TaskBoard.vue` / `TaskCard.vue`）：只读 `state` 与调用动作，自己不做扫描、
      不自己 `classList.add`。

   两条票面纪律在这里落地：

   - **前端不持迁移表副本**：「这张卡能去哪一列」只读载荷 `allowed_transitions`
     （`allowedTransitions()`）；字段缺失就是空集合，不回退到一份本地表。
   - **拖拽是声明式的**：`state.drag` 是唯一事实——`from` 是哪张卡、`over` 落在哪一列、
     `over` 合不合法。列的 `drag-over` / `invalid-over` 由 `dropClass()` 推导，
     卡片弹不弹走由「重读后端事实」决定（**没有乐观移动**）。

   **写路径只有一条**（`changeStatus()`）：拖拽的 `drop()`、菜单项、以及后续线程里的推进 chip
   都走它。票面「卡片动作与拖拽触发同一后端判定」就是这一条函数 + 载荷里的同一份
   `allowed_transitions`——不出现两套规则。 */
import { computed, reactive } from 'vue';
import { api } from '../lib/api.js';
import { t } from '../i18n/index.js';
import {
  SUPPRESS_CLICK_MS,
  TASK_STATUS_ORDER,
  allowedTransitions,
  canTransition,
  isMyTask,
  taskStatusLabel,
  taskTransitionLabel,
} from '../lib/task-board.js';

/**
 * @param {object} options
 * @param {() => string} options.ownerId      本机人类成员的 id（「我的任务」的左半边判据）
 * @param {() => boolean} [options.online]    在线读数（离线时写入口不出、拖拽不记账）
 * @param {(message: string) => void} [options.toast] 提示落点（非法迁就说事实，不静默）
 * @param {() => number} [options.now]        时钟（用例注入假时钟测「拖拽后的补发 click」）
 */
export function createTaskBoardStore(options) {
  const config = options || {};
  const ownerId = config.ownerId || (() => '');
  const online = config.online || (() => true);
  const toast = config.toast || (() => {});
  const now = config.now || (() => Date.now());

  const state = reactive({
    // 全量快照（未筛选）。页签计数、卡片查名、写路径的默认落点都读它。
    tasks: [],
    // 上一次读是否落地（读失败保留旧快照，不清空——任务面读不到不影响频道消息与发送）。
    loaded: false,
    // 呈现状态（内存，不持久化）。
    view: 'board', // 'board' | 'list'（旧页面 `state.taskView` 初值是 board）
    filterMine: false,
    filterChannelId: '',
    // 拖拽态（唯一事实）：`from` = 正在拖的任务 id，`over` = 指针下的列，`status` 那一档合不合法。
    drag: { from: null, over: null },
    // 在飞迁移的任务 id 集合（同一张任务至多一个在飞请求，避免叠加写）。
    moving: [],
    // 拖拽结束后抑制「内核补发的那个 click」的截止时刻（旧 `suppressClickUntil` 同值）。
    suppressClickUntil: 0,
  });

  /* ─── 读 ─────────────────────────────────────────────────────────────── */

  /**
   * 读一次任务快照。
   *
   * 只读 `/api/tasks`（**不**按频道再读一次）：筛选是本地判据（`filterChannelId`），
   * 而页签计数要的是「当前频道的任务数」，那必须来自全量快照——拿筛选后的清单冒充全量，
   * 会让未筛选的计数偏低（旧 `refreshTasks()` 的注释记着这一次教训）。
   */
  async function refresh() {
    try {
      const payload = await api('/api/tasks');
      state.tasks = Array.isArray(payload && payload.tasks) ? payload.tasks : [];
      state.loaded = true;
    } catch {
      // 读失败保留最后一次成功快照（不静默清空、不伪造空看板）。
    }
    return state.tasks;
  }

  /** 从频道页签进看板：默认筛到**当前频道**（人在 #甲 的任务里，就该先看 #甲 的任务）。 */
  async function openForChannel(channelId) {
    // 「全部频道」这一档是 ''，与 null / undefined 不是一回事：只有真的给了频道才筛。
    state.filterChannelId = channelId || '';
    if (!state.loaded) await refresh();
    return state;
  }

  /** 查一张任务（三个读点共用一份：快照 / 在飞迁移后的回写 / 菜单目标校验）。 */
  function taskOf(taskId) {
    return state.tasks.find((item) => item.id === taskId) || null;
  }

  /* ─── 派生读数 ──────────────────────────────────────────────────────── */

  const visibleTasks = computed(() =>
    state.tasks.filter((task) => {
      if (state.filterMine && !isMyTask(task, ownerId())) return false;
      if (state.filterChannelId && task.channel_id !== state.filterChannelId) return false;
      return true;
    }),
  );

  function tasksIn(status) {
    return visibleTasks.value.filter((task) => task.status === status);
  }

  function columnCount(status) {
    return tasksIn(status).length;
  }

  /** 当前频道的任务数（**不是**筛选后的可见数：页签计数与看板筛选无关）。 */
  function channelTaskCount(channelId) {
    if (!channelId) return 0;
    return state.tasks.filter((task) => task.channel_id === channelId).length;
  }

  /* ─── 呈现状态的写入口（视图档与筛选） ────────────────────────────────── */

  function setView(view) {
    state.view = view === 'list' ? 'list' : 'board';
  }

  function toggleFilterMine() {
    state.filterMine = !state.filterMine;
  }

  function setFilterChannel(channelId) {
    state.filterChannelId = channelId || '';
  }

  /* ─── 拖拽（只写状态，落点类由它推导） ────────────────────────────────── */

  function isDragging(taskId) {
    return state.drag.from === taskId;
  }

  function isMoving(taskId) {
    return state.moving.includes(taskId);
  }

  function beginDrag(taskId) {
    const task = taskOf(taskId);
    // 没有合法目标（终态）的卡片不是拖拽源：它连 `draggable` 都不挂，这里再守一次。
    if (!task || !online() || !allowedTransitions(task).length || isMoving(taskId)) return;
    state.drag = { from: taskId, over: null };
    state.suppressClickUntil = 0;
  }

  /** 指针进到某一列：合法 / 非法由载荷决定，同一列不算落点（拖回原列无动作）。 */
  function hoverColumn(status) {
    if (!state.drag.from) return;
    const task = taskOf(state.drag.from);
    if (!task) return;
    state.drag.over = status;
  }

  /**
   * 这一列现在挂哪个落点类。
   *
   * 三种情况分开：`drag-over` 是**可以**落、`invalid-over` 是**落不了**（同一列是第三种：
   * 两个都不挂——拖回原列既不是迁移，也不该给一句「非法」的指责）。
   */
  function dropClass(status) {
    const from = state.drag.from;
    const over = state.drag.over;
    if (!from || over !== status) return {};
    const task = taskOf(from);
    if (!task || status === task.status) return {};
    const legal = canTransition(task, status);
    return legal ? { 'drag-over': true } : { 'invalid-over': true };
  }

  /** 指针离开看板（不是在看板内部换列）：落点态清掉，否则最后一列会一直亮着。 */
  function leaveBoard() {
    state.drag.over = null;
  }

  /** 拖拽结束（`dragend`）：清掉拖拽态，并给点击一个短窗口（内核可能补发一个 click）。 */
  function endDrag() {
    state.drag = { from: null, over: null };
    state.suppressClickUntil = now() + SUPPRESS_CLICK_MS;
  }

  function clickSuppressed() {
    return now() < state.suppressClickUntil;
  }

  /* ─── 写路径（拖拽、菜单、线程 chips 共用这一条） ─────────────────────── */

  /**
   * 请求一次状态迁移（`PATCH /api/tasks/{id}/status`）。
   *
   * 四件事：① 前端守卫只读载荷——目标不在 `allowed_transitions` 里就**不发请求**，但仍把
   * 「不是合法迁移」这条事实说出来（静默什么都不发生，人只会以为界面坏了）；② **没有乐观移动**：
   * 在途时卡片留在原列；③ 成功后按响应 + 重读刷新（后端事实是唯一权威）；④ 失败也回到后端事实。
   */
  async function changeStatus(taskId, target) {
    const task = taskOf(taskId);
    if (!task || !target || target === task.status) return false;
    if (!online()) return false;
    if (!canTransition(task, target)) {
      toast(t('toast.transitionInvalid', {
        from: taskStatusLabel(task.status),
        to: taskStatusLabel(target),
      }), 'err');
      return false;
    }
    // 同一张任务至多一个在飞迁移：叠加的第二个请求只会得到一次注定被拒的写。
    if (isMoving(taskId)) return false;
    state.moving.push(taskId);
    try {
      const payload = await api('/api/tasks/' + encodeURIComponent(taskId) + '/status', {
        method: 'PATCH',
        body: { status: target },
      });
      // 响应里的任务行是**最新的**那份载荷（含新的 `allowed_transitions`）：就地替换，
      // 于是菜单目标集合与拖拽合法性立刻跟着变，不必等下一次全量重读。
      if (payload && payload.task) replaceTask(payload.task);
      await refresh();
      return true;
    } catch (err) {
      toast(t('toast.statusUpdateFailed', { reason: err.message }), 'err');
      // 失败路径也回到后端事实（前端不猜「大概是没成功」）。
      await refresh();
      return false;
    } finally {
      const index = state.moving.indexOf(taskId);
      if (index >= 0) state.moving.splice(index, 1);
    }
  }

  function replaceTask(task) {
    if (!task || !task.id) return;
    const index = state.tasks.findIndex((item) => item.id === task.id);
    if (index >= 0) state.tasks[index] = task;
    else state.tasks.push(task);
  }

  /**
   * 落点松手 = **请求一次**状态迁移（票面第三节）。不在这里改 `state.tasks`：
   * 「摆到哪一列」是后端事实，不是我们算出来的意图。
   */
  async function drop() {
    const from = state.drag.from;
    const over = state.drag.over;
    const task = from ? taskOf(from) : null;
    endDrag();
    if (!task || !over || over === task.status) return false;
    return changeStatus(task.id, over);
  }

  return {
    state,
    statusOrder: TASK_STATUS_ORDER,
    visibleTasks,
    tasksIn,
    columnCount,
    channelTaskCount,
    taskOf,
    refresh,
    openForChannel,
    setView,
    toggleFilterMine,
    setFilterChannel,
    beginDrag,
    hoverColumn,
    dropClass,
    drop,
    leaveBoard,
    endDrag,
    clickSuppressed,
    isDragging,
    isMoving,
    changeStatus,
    replaceTask,
    // 词条说法由 store 一并给出，组件不必各自 import 两套词条函数（一处取值，一处口径）。
    statusLabel: taskStatusLabel,
    transitionLabel: taskTransitionLabel,
  };
}
