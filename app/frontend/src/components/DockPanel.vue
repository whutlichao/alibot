<!-- 右侧详情面板（dock）：**三形态**容器

     旧页面的 `#dock` 里住着三种形态（`#dockAgent` 频道成员 / `#dockTask` 任务线程 / `#dockRun`
     运行面板），共用一个 380px 容器，于是「同时显示两件东西」在构造上不可能发生。

     形态是**推导**出来的，不另存一份状态：`dockTarget` 由父组件给（`{ kind: 'run' | 'members' |
     'task' }`），`null` ⇒ 整栏不渲染。于是「关掉了但 dock 还停在旧形态」这类漂移在构造上不存在。

     两档已接到真内容上：`run` ⇒ `RunPanel.vue`（票 #300 + 阶段一），`task` ⇒ `TaskThread.vue`
     （票 #307）。`members` 档仍是具名插槽——成员面在 v2 走的是弹窗（`RosterModal.vue`），
     这一档至今没有调用点，保留插槽只是为了让形态判据完整。

     线程的读数与写路径都在 `stores/task-thread.js`：外壳只把 props 往下递、把两个动作往上抛
     （关面板 / 开任务运行面板），不自己判「能不能推进」「能不能指派」。

     ≤900px 下它是右抽屉（`app.css` 的 `.dock.is-open` / `.dock[hidden]` 两条规则）：`hidden` 的语义
     被改写成「离屏 + 不可见」，`.is-open` 决定滑入。`.dock-close` 是这个抽屉**自己的**出口
     ——那时频道头里的开关正好被抽屉挡住。>900px 它是常驻列，`.dock-close` 由 CSS 藏起来。 -->
<script setup>
import { computed } from 'vue';
import RunPanel from './RunPanel.vue';
import TaskThread from './TaskThread.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  // 当前打开着哪一个对象：`{ kind: 'run' | 'members' | 'task', ... }`；`null` = 没有可显示的东西。
  dockTarget: { type: Object, default: null },
  // 运行面板 store（`createRunPanelStore()` 的返回值）。只有 `kind === 'run'` 的形态用它。
  panel: { type: Object, default: null },
  // 成员名的三个来源池（工作区成员 / Agent / 频道成员）——**必须往下传**：
  // 不传的话运行面板会退回把 `member_id` 当名字显示。真浏览器实测抓到过这一次，
  // 单测直接挂 RunPanel（自己传 pools）看不到它，因此 `run-panel.test.js` 里另有一条
  // 「通过 DockPanel 挂上来也拿到成员名」的用例。
  pools: { type: Array, default: () => [] },
  // 抽屉形态（≤900px）：由 App.vue 决定开合，与 `hidden` 同进同出。
  open: { type: Boolean, default: false },
  // ─── 任务线程（`kind === 'task'`）的读数与写路径 ───────────────────────────
  thread: { type: Object, default: null },
  threadMembers: { type: Array, default: () => [] },
  threadAssignees: { type: Array, default: null },
  threadAssigneesFailed: { type: Boolean, default: false },
  threadChannelMode: { type: String, default: null },
  // 审批记录镜像（票 #308）：往下递给任务线程的审批段（与页签圆点 / 卡片角标同一份读数）。
  approvals: { type: Object, default: null },
  // 待审批**条数**读数（`stores/approvals.js` 的 `pendingCountFor`）：线程讨论行里指针 chip
  // 后面的角标读它。与上面那份 `approvals` 是同一份镜像的两个读法，不各算一遍。
  pendingCountFor: { type: Function, default: null },
  online: { type: Boolean, default: true },
  // 任务快照查名（`stores/task-board.js` 的 `taskOf`）：线程讨论行上的任务 chip 读它（票 #309）。
  threadTaskOf: { type: Function, default: null },
});

const emit = defineEmits(['close', 'open-run-panel', 'open-task']);

/**
 * 当前形态：由 `dockTarget` 推导。
 *
 * 两种「有形态但没有内容」要按没有形态处理：`run` 缺 store、`task` 缺 thread——否则人会看到
 * 一个空壳 dock，而它既不是运行面板也不是任务线程。
 */
const mode = computed(() => {
  const target = props.dockTarget;
  if (!target || !target.kind) return null;
  if (target.kind === 'run' && !props.panel) return null;
  if (target.kind === 'task' && !props.thread) return null;
  return target.kind;
});
</script>

<template>
  <aside
    v-if="mode"
    id="dock"
    class="dock"
    :class="{ 'is-open': open }"
    data-od-id="detail-dock"
    :aria-label="t('dock.label')"
  >
    <RunPanel
      v-if="mode === 'run'"
      :panel="panel"
      :pools="pools"
      @close="emit('close')"
    />
    <TaskThread
      v-else-if="mode === 'task'"
      :thread="thread"
      :members="threadMembers"
      :assignees="threadAssignees"
      :assignees-failed="threadAssigneesFailed"
      :channel-mode="threadChannelMode"
      :approvals="approvals"
      :online="online"
      :task-of="threadTaskOf"
      :pending-count-for="pendingCountFor"
      @close="emit('close')"
      @open-run-panel="emit('open-run-panel')"
      @open-task="emit('open-task', $event)"
    />
    <!-- 频道成员面板（T3）的落点：成员面在 v2 走弹窗，这一档至今没有调用点。 -->
    <slot v-else-if="mode === 'members'" name="members" />
  </aside>
</template>
