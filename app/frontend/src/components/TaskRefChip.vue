<!-- 任务 chip（**一份判据，两处复用**）

     旧页面把两条语义写在同一处（`studio/app.js:8968` 的 `appendTaskRefChip()`）：
     消息带 `task_ref` ⇒ 引用 chip（「这条消息在说哪个任务」），带 `task_pointer` ⇒ 频道指针 chip
     （「进入任务线程」，后端建任务时落在频道流里的那条 system 记录）。两个**数据列、两条语义**，
     视觉是同一个 `.task-ref-chip`（`CONTEXT.md` **指针消息**）。

     因此判据只在这里有一处：`MessageItem`（系统消息 + 对话两条分支）与 `TaskThread` 的讨论行
     都挂这一个组件，不在各自的模板里再判一次 `task_ref` / `task_pointer`。

     三条沿用旧页面的既有事实：

     1. 类名与属性逐字沿用：`.task-ref-chip` / `data-open-task` / `data-task-pointer`。
        `data-task-pointer` **只**挂在指针 chip 上——旧页面 `syncTaskPointerChips()` 按它定点改写
        （引用 chip 按 `data-open-task` 扫会误伤同 target 的引用行）。
     2. 任务不在快照里时**用 id 本身当名字**、未读按 0 处理：不编名字、不编读数。
     3. 点击 = 打开该任务的线程（`App.vue` 的 `openTask()` 是唯一落点），组件只往上抛一个事件。

     **「待审批 N」角标**（旧 `approvalBadgeNode()`，`data-od-id="message-approval-badge-<id>"`）
     跟在指针 chip 后面，读数归 T8 的审批 store——v2 至今没有那份读数（`app/frontend/src/stores/`
     里没有 approvals），因此这里**不造第二份**：TODO 等 T8 的读数接口落地后按同一个节点挂上来。 -->
<script setup>
import { computed } from 'vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  message: { type: Object, required: true },
  // 任务快照查名（`stores/task-board.js` 的 `taskOf(taskId)`，等价于旧 `taskById()`）。
  // 读不到 ⇒ 返回 `null`（本组件据此用 id 代替标题、未读按 0）。
  taskOf: { type: Function, default: null },
});

const emit = defineEmits(['open-task']);

function taskOfId(taskId) {
  if (!props.taskOf || !taskId) return null;
  const task = props.taskOf(taskId);
  return task && task.id ? task : null;
}

/** 引用 chip：标题是任务标题，任务不在快照里就用 id 本身（不编名字）。 */
const refChip = computed(() => {
  const taskId = props.message && props.message.task_ref;
  if (!taskId) return null;
  const task = taskOfId(taskId);
  const title = task ? task.title || taskId : taskId;
  return {
    key: 'ref',
    taskId,
    text: t('msg.taskRef', { title }),
    aria: t('msg.taskRefAria', { title }),
    pointer: false,
  };
});

/**
 * 指针 chip：未读数取**任务载荷的 `unread_count`**（#223——system 记录与自己的发言都不计入），
 * 任务不在快照里按未读 0 处理。文案是 computed，于是任务载荷一变（标读后未读归 0）已渲染的
 * chip 自己就跟着变——旧页面那条 `syncTaskPointerChips()` 的定点改写在这里由 Vue 的依赖收集承担。
 */
const pointerChip = computed(() => {
  const taskId = props.message && props.message.task_pointer;
  if (!taskId) return null;
  const task = taskOfId(taskId);
  const title = task ? task.title || taskId : taskId;
  const unread = task ? Number(task.unread_count) || 0 : 0;
  return {
    key: 'pointer',
    taskId,
    text: unread > 0 ? t('msg.taskThreadEntryUnread', { count: unread }) : t('msg.taskThreadEntry'),
    aria: unread > 0
      ? t('msg.taskThreadEntryAriaUnread', { title, count: unread })
      : t('msg.taskThreadEntryAria', { title }),
    pointer: true,
  };
});

/** 两条字段可以同时在一条消息上（旧页面也是各挂一个），顺序是引用在前、指针在后。 */
const chips = computed(() => [refChip.value, pointerChip.value].filter(Boolean));
</script>

<template>
  <button
    v-for="chip in chips"
    :key="chip.key"
    class="task-ref-chip"
    type="button"
    :data-open-task="chip.taskId"
    :data-task-pointer="chip.pointer ? chip.taskId : undefined"
    :aria-label="chip.aria"
    @click="emit('open-task', chip.taskId)"
  >{{ chip.text }}</button>
</template>
