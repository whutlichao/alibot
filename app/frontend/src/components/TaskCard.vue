<!-- 看板卡片（`.card`，#191）：标题 + mono 序号 + 频道 tag + 指派者 / 未认领 + 未读 + 流转菜单

     根节点是 `<div role="button" tabindex="0">`：它同时承载「拖拽源」与卡片内右上角的
     「流转」菜单按钮，而 `<button>` 里不能再嵌按钮（旧页面 `taskCardNode()` 同一条）。
     `draggable` 只在**有合法目标**时挂上——终态（`closed`）没有出边，摆一个拖不动的拖拽源
     同样是「不实现就不摆样子」。

     拖拽与点击都是**声明**：五个拖拽事件只往 `stores/task-board.js` 的 `state.drag` 写一笔，
     `.dragging` 由 `board.isDragging(task.id)` 推导（不在这里 `classList.add`）。

     动作入口有两条：拖拽（指针设备）与「流转」菜单（触屏 / 键盘）。两条命中同一条写路径，
     菜单项集合就是载荷的 `allowed_transitions`——这里不判断「什么状态能到什么状态」。 -->
<script setup>
import { computed } from 'vue';
import MemberAvatar from './MemberAvatar.vue';
import { t } from '../i18n/index.js';
import { allowedTransitions, taskNumber, taskStatusLabel, unreadBadge } from '../lib/task-board.js';

const props = defineProps({
  task: { type: Object, required: true },
  // 看板 store（`createTaskBoardStore()` 的返回值）：拖拽态、在途态、动作都在那里。
  board: { type: Object, required: true },
  // 成员表（owner 名的来源是任务载荷的 `assignee_member_id` 在这个表里的那一行）。
  members: { type: Array, default: () => [] },
  online: { type: Boolean, default: true },
  // 这张卡的「流转」菜单现在开着吗（菜单本体由 `TaskBoard.vue` 铺，触发器只陈述自己的状态）。
  menuOpen: { type: Boolean, default: false },
  // 这张卡所属任务 target 的待审批条数（票 #308）：读的是 `stores/approvals.js` 那一份镜像，
  // 与审批卡、页签圆点同一份读数。`0` ⇒ 不渲染角标（不摆空元素）。
  approvalCount: { type: Number, default: 0 },
});

const emit = defineEmits(['open', 'open-menu', 'drag-start', 'drag-end']);

const targets = computed(() => allowedTransitions(props.task));
const draggable = computed(() => props.online && targets.value.length > 0 && !props.board.isMoving(props.task.id));

const owner = computed(() => {
  const id = props.task.assignee_member_id;
  if (!id) return null;
  const member = props.members.find((item) => item.id === id);
  // 指派者可能已被删除（任务行不因此改历史）：如实说「已删除的成员」，不编名字。
  return member || { id, display_name: t('task.deletedAssignee'), avatar_slot: 0 };
});

const ariaLabel = computed(() =>
  props.approvalCount
    ? t('board.cardApprovalAria', {
      title: props.task.title,
      status: taskStatusLabel(props.task.status),
      count: props.approvalCount,
    })
    : t('board.cardAria', { title: props.task.title, status: taskStatusLabel(props.task.status) }),
);

const menuExpanded = computed(() => props.menuOpen);
</script>

<template>
  <div
    class="card"
    :class="{
      'has-menu': targets.length > 0,
      dragging: board.isDragging(task.id),
    }"
    role="button"
    tabindex="0"
    :draggable="draggable ? 'true' : undefined"
    :data-task="task.id"
    :data-status="task.status"
    :data-od-id="'task-card-' + task.id"
    :aria-label="ariaLabel"
    @click="emit('open', task.id)"
    @keydown.enter.prevent="emit('open', task.id)"
    @keydown.space.prevent="emit('open', task.id)"
    @dragstart="emit('drag-start', task.id)"
    @dragend="emit('drag-end')"
  >
    <div class="card-title">{{ task.title }}</div>
    <div class="card-meta">
      <span class="card-num">{{ taskNumber(task.id) }}</span>
      <span v-if="task.channel_name" class="card-tag">#{{ task.channel_name }}</span>
      <!-- 「待审批 N」角标（票 #308 / `#209`）：有 pending 才渲染，读的是同一份审批镜像。 -->
      <span
        v-if="approvalCount > 0"
        class="card-approval"
        :data-od-id="'task-approval-badge-' + task.id"
        :aria-label="t('board.approvalBadgeAria', { title: task.title, count: approvalCount })"
      >{{ t('board.approvalBadge', { count: approvalCount }) }}</span>
      <span v-if="owner" class="card-owner">
        <MemberAvatar :name="owner.display_name" :slot="owner.avatar_slot || 0" size="sm" />
        {{ owner.display_name }}
      </span>
      <span v-else class="card-owner unassigned">{{ t('task.unassigned') }}</span>
      <span
        v-if="task.unread_count > 0"
        class="card-unread"
        :aria-label="t('board.unreadAria', { count: task.unread_count })"
      >{{ unreadBadge(task.unread_count) }}</span>
    </div>
    <!-- 终态没有出边 ⇒ 不摆一个点不出东西的入口（#191 D5）。 -->
    <button
      v-if="targets.length > 0"
      class="card-menu-btn"
      type="button"
      :aria-haspopup="'menu'"
      :aria-expanded="menuExpanded ? 'true' : 'false'"
      aria-controls="taskMoveMenu"
      :aria-label="t('board.cardMenuAria', { title: task.title })"
      :disabled="!online"
      @click.stop="emit('open-menu', { taskId: task.id, trigger: $event.currentTarget })"
      @keydown.enter.stop.prevent="emit('open-menu', { taskId: task.id, trigger: $event.currentTarget })"
      @keydown.space.stop.prevent="emit('open-menu', { taskId: task.id, trigger: $event.currentTarget })"
    >
      <span class="card-menu-glyph" aria-hidden="true">···</span>
    </button>
  </div>
</template>
