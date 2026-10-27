<!-- 任务看板（票 #306）：工具栏 + 五列 / 列表两档 + 共享「流转」菜单

     形态逐字沿用旧页面：`#taskBoard` 的 `.board-wrap` / `.board-toolbar` / `.seg` / `.filter-chip` /
     `.board` / `.board-cols` / `.col` / `.board-list` / `.result`，以及共享浮层 `#taskMoveMenu`
     （`studio/index.html:228-244` 与 `:585`）。`app.css` 因此零改动——新类名一个都不加。

    两条纪律（票面二、三节）：

     1. **前端不持迁移表副本**：列的顺序是呈现顺序（`lib/task-board.js` 的 `TASK_STATUS_ORDER`），
        而「这张卡能去哪一列」只读载荷 `allowed_transitions`（`board.dropClass()` / 菜单项集合）。
     2. **拖拽是声明式的**：拖拽态与落点态都住在 `stores/task-board.js` 的 `state.drag` 里，
        列的 `drag-over` / `invalid-over` 由 `board.dropClass(status)` 推导、卡片的 `.dragging` 由
        `board.isDragging()` 推导。这里不做 DOM 扫描、不直接改 class。

     点击与拖拽互不污染：拖拽结束后 250ms 内的 `click` 不算「打开线程」（`board.clickSuppressed()`，
     旧页面 `suppressClickUntil` 同一条）——内核会补发那个 click，不抑制就等于「拖完顺手开了线程」。 -->
<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import TaskCard from './TaskCard.vue';
import MemberAvatar from './MemberAvatar.vue';
import { t } from '../i18n/index.js';
import {
  allowedTransitions,
  taskNumber,
  taskStatusLabel,
  taskTransitionLabel,
  unreadBadge,
} from '../lib/task-board.js';

const props = defineProps({
  // 看板 store（`createTaskBoardStore()` 的返回值）：读数、视图档、筛选项与拖拽态。
  board: { type: Object, required: true },
  // 工作台镜像里看板要读的三样：在线读数、成员（owner 名）、频道（筛选 chip）。
  state: { type: Object, required: true },
});

const emit = defineEmits(['open-task', 'new-task']);

const statusOrder = props.board.statusOrder;
const online = computed(() => Boolean(props.state.online));

const columns = computed(() =>
  statusOrder.map((status) => ({ status, items: props.board.tasksIn(status) })),
);

const channels = computed(() => props.state.channels || []);

/** 列表视图的行：与看板同一份筛选结果（`.seg` 是同一批任务的两种呈现，不重取）。 */
const listTasks = computed(() => props.board.visibleTasks.value);

function channelChipId(channelId) {
  return 'filter-channel-' + channelId;
}

// ─── 卡片「流转」菜单（一个共享浮层，`position: fixed` 按触发器 rect 摆） ──────────

const menuTaskId = ref(null);
const menuIndex = ref(0);
const itemEls = ref([]);
const menuEl = ref(null);
const menuStyle = reactive({ top: '0px', left: 'auto', right: '0px' });
let triggerEl = null;

const menuTargets = computed(() => {
  if (!menuTaskId.value) return [];
  return allowedTransitions(props.board.taskOf(menuTaskId.value));
});

function setItemEl(element, index) {
  if (element) itemEls.value[index] = element;
  else delete itemEls.value[index];
}

/** 按触发器 rect 摆菜单：下方优先，空间不足翻到上方；右缘对齐触发器右缘（旧 `positionTaskMoveMenu()`）。 */
function positionMenu() {
  if (!triggerEl || typeof triggerEl.getBoundingClientRect !== 'function') return;
  const rect = triggerEl.getBoundingClientRect();
  const gap = 6;
  const viewportHeight = (typeof window !== 'undefined' && window.innerHeight) || 0;
  const box = menuEl.value && typeof menuEl.value.getBoundingClientRect === 'function'
    ? menuEl.value.getBoundingClientRect()
    : null;
  const menuHeight = box ? box.height : 0;
  const flip = viewportHeight > 0 && menuHeight > 0 && rect.bottom + gap + menuHeight > viewportHeight;
  menuStyle.top = (flip ? Math.max(gap, rect.top - gap - menuHeight) : rect.bottom + gap) + 'px';
  menuStyle.right = (((typeof window !== 'undefined' && window.innerWidth) || rect.right) - rect.right) + 'px';
  menuStyle.left = 'auto';
}

async function openMenu({ taskId, trigger }) {
  const task = props.board.taskOf(taskId);
  if (!task || !allowedTransitions(task).length || !online.value) return;
  triggerEl = trigger || null;
  itemEls.value = [];
  menuTaskId.value = taskId;
  menuIndex.value = 0;
  await nextTick();
  positionMenu();
  focusItem(0);
}

function focusItem(index) {
  const items = itemEls.value.filter(Boolean);
  if (!items.length) return;
  const next = ((index % items.length) + items.length) % items.length;
  menuIndex.value = next;
  const node = items[next];
  if (node && node.focus) node.focus();
}

function closeMenu(restoreFocus) {
  const trigger = triggerEl;
  menuTaskId.value = null;
  menuIndex.value = 0;
  itemEls.value = [];
  triggerEl = null;
  if (restoreFocus && trigger && trigger.focus) trigger.focus();
}

async function chooseTarget(target) {
  const taskId = menuTaskId.value;
  closeMenu(false);
  if (!taskId) return;
  await props.board.changeStatus(taskId, target);
}

/** 菜单的键盘面：↑/↓ 循环、Esc 关闭并还焦点、Enter / Space 激活当前项（旧 `onTaskMoveMenuKeydown()`）。 */
async function onMenuKeydown(event) {
  if (!menuTaskId.value) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeMenu(true);
    return;
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    focusItem(menuIndex.value + 1);
    return;
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault();
    focusItem(menuIndex.value - 1);
    return;
  }
  if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
  const target = menuTargets.value[menuIndex.value];
  if (!target) return;
  event.preventDefault();
  await chooseTarget(target);
}

/** 点外部 / 滚看板时收起：触发器与菜单自身不算「外部」（旧 `onDocumentClickCloseTaskMoveMenu()`）。 */
function onDocumentClick(event) {
  if (!menuTaskId.value) return;
  const node = event.target;
  if (node && typeof node.closest === 'function'
    && (node.closest('[data-od-id="task-move-menu"]') || node.closest('.card-menu-btn'))) return;
  closeMenu(false);
}

// ─── 拖拽（只写 store 的状态，落点类由它推导） ────────────────────────────────

function onDrop() {
  props.board.drop();
}

/** 指针离开看板（不是在看板内部换列）才清落点态：否则最后一列会一直亮着。 */
function onColumnsLeave(event) {
  const next = event.relatedTarget;
  const host = event.currentTarget;
  if (next && host && typeof host.contains === 'function' && host.contains(next)) return;
  props.board.leaveBoard();
}

/** 指派者名与色位：成员表里查一行；查不到（成员已删除）时说「已删除的成员」，不编名字。 */
function assigneeMember(task) {
  if (!task.assignee_member_id) return null;
  return (props.state.members || []).find((item) => item.id === task.assignee_member_id) || null;
}

function assigneeName(task) {
  const member = assigneeMember(task);
  return member ? member.display_name : t('task.deletedAssignee');
}

function assigneeSlot(task) {
  const member = assigneeMember(task);
  return member ? member.avatar_slot || 0 : 0;
}

function openTask(taskId) {
  if (props.board.clickSuppressed()) return;
  emit('open-task', taskId);
}

function openFromRow(taskId) {
  if (props.board.clickSuppressed()) return;
  emit('open-task', taskId);
}

onMounted(async () => {
  document.addEventListener('click', onDocumentClick);
  if (!props.board.state.loaded) await props.board.refresh();
});
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick));
</script>

<template>
  <section class="board-wrap" data-od-id="tasks-view">
    <div class="board-toolbar">
      <div class="seg" role="tablist" :aria-label="t('board.viewLabel')" data-od-id="task-view-seg">
        <button
          class="tab"
          :class="{ 'is-active': board.state.view === 'board' }"
          type="button"
          role="tab"
          data-taskview="board"
          data-od-id="taskview-board"
          aria-controls="taskColumns"
          :aria-selected="board.state.view === 'board' ? 'true' : 'false'"
          @click="board.setView('board')"
        >{{ t('board.board') }}</button>
        <button
          class="tab"
          :class="{ 'is-active': board.state.view === 'list' }"
          type="button"
          role="tab"
          data-taskview="list"
          data-od-id="taskview-list"
          aria-controls="taskListWrap"
          :aria-selected="board.state.view === 'list' ? 'true' : 'false'"
          @click="board.setView('list')"
        >{{ t('board.list') }}</button>
      </div>
      <div class="board-filters" role="group" :aria-label="t('board.filtersLabel')" data-od-id="board-filters">
        <!-- 「我的任务」= 指派给我或未认领（任务不记录创建者归属，所以这不是「我建的」）。 -->
        <button
          class="filter-chip"
          :class="{ 'is-on': board.state.filterMine }"
          type="button"
          data-od-id="filter-mine"
          :aria-pressed="board.state.filterMine ? 'true' : 'false'"
          @click="board.toggleFilterMine()"
        >{{ t('board.mine') }}</button>
        <span class="filter-sep"></span>
        <button
          class="filter-chip"
          :class="{ 'is-on': board.state.filterChannelId === '' }"
          type="button"
          data-od-id="filter-channel-all"
          :aria-pressed="board.state.filterChannelId === '' ? 'true' : 'false'"
          @click="board.setFilterChannel('')"
        >{{ t('board.allChannels') }}</button>
        <button
          v-for="channel in channels"
          :key="channel.id"
          class="filter-chip"
          :class="{ 'is-on': board.state.filterChannelId === channel.id }"
          type="button"
          :data-od-id="channelChipId(channel.id)"
          :aria-pressed="board.state.filterChannelId === channel.id ? 'true' : 'false'"
          @click="board.setFilterChannel(channel.id)"
        >#{{ channel.name }}</button>
      </div>
      <!-- 新建任务表单（票 #307）：与命令面板的 `new-task` 打开的是同一张 `TaskFormModal`，
           因此两个入口一套字段与一条写路径。 -->
      <button
        class="btn btn-sm"
        type="button"
        data-od-id="btn-new-task"
        :disabled="!online"
        @click="emit('new-task')"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14" /><path d="M12 5v14" /></svg>
        <span>{{ t('board.newTask') }}</span>
      </button>
    </div>

    <div class="board">
      <div
        v-if="board.state.view === 'board'"
        class="board-cols"
        id="taskColumns"
        data-od-id="task-columns"
        @dragleave="onColumnsLeave"
      >
        <section
          v-for="entry in columns"
          :key="entry.status"
          class="col"
          :class="board.dropClass(entry.status)"
          :data-status="entry.status"
          :data-od-id="'task-column-' + entry.status"
          @dragenter.prevent="board.hoverColumn(entry.status)"
          @dragover.prevent="board.hoverColumn(entry.status)"
          @drop.prevent="onDrop"
        >
          <div class="col-head">
            <span class="st-dot"></span>
            <b>{{ taskStatusLabel(entry.status) }}</b>
            <span class="col-count">{{ board.columnCount(entry.status) }}</span>
          </div>
          <div class="col-body">
            <TaskCard
              v-for="task in entry.items"
              :key="task.id"
              :task="task"
              :board="board"
              :members="state.members || []"
              :online="online"
              :menu-open="menuTaskId === task.id"
              @open="openTask"
              @open-menu="openMenu"
              @drag-start="board.beginDrag($event)"
              @drag-end="board.endDrag()"
            />
            <!-- 空态只陈述事实：拖拽入口由卡片承担，落点提示不是必需文案。 -->
            <div v-if="!entry.items.length" class="drop-hint">{{ t('board.emptyColumn') }}</div>
          </div>
        </section>
      </div>

      <div v-else class="board-list" id="taskListWrap" data-od-id="task-list">
        <div v-if="!listTasks.length" class="empty">
          <b>{{ t('board.noMatchTitle') }}</b>
          <p>{{ t('board.noMatchBody') }}</p>
        </div>
        <button
          v-for="task in listTasks"
          :key="task.id"
          class="result"
          type="button"
          :data-task="task.id"
          :data-status="task.status"
          :data-od-id="'task-row-' + task.id"
          :aria-label="t('board.cardAria', { title: task.title, status: taskStatusLabel(task.status) })"
          @click="openFromRow(task.id)"
        >
          <div class="r-top">
            <span class="st-dot"></span>
            <span>{{ taskStatusLabel(task.status) }}</span>
            <span class="hash">{{ taskNumber(task.id) }}</span>
            <span v-if="task.channel_name" class="card-tag">#{{ task.channel_name }}</span>
            <span class="r-open">{{ t('board.openThread') }}</span>
          </div>
          <div class="r-title">{{ task.title }}</div>
          <div class="r-snip">
            <template v-if="task.assignee_member_id">
              <MemberAvatar :name="assigneeName(task)" :slot="assigneeSlot(task)" size="sm" />
              <span>{{ assigneeName(task) }}</span>
            </template>
            <span v-else class="card-owner unassigned">{{ t('task.unassigned') }}</span>
            <span
              v-if="task.unread_count > 0"
              class="card-unread"
              :aria-label="t('board.unreadAria', { count: task.unread_count })"
            >{{ unreadBadge(task.unread_count) }}</span>
          </div>
        </button>
      </div>
    </div>

    <!-- 「流转」菜单：一个共享浮层，按触发器 rect 定位（`position: fixed`，因此不被 `.board`
         的 `overflow: auto` 裁切）。项只来自该任务的载荷 `allowed_transitions`，顺序即载荷顺序；
         文案复用既有动词词条 `task.transition.<target>`（与线程 chips 同一套说法）。
         项是 `<div role="menuitem" tabindex="-1">` 而不是 `<button>`：于是只有这一条激活路径，
         不会出现「键盘一次、原生 click 再一次」的双写。 -->
    <div
      v-if="menuTaskId"
      id="taskMoveMenu"
      ref="menuEl"
      class="card-menu"
      role="menu"
      :aria-label="t('board.cardMenuLabel')"
      data-od-id="task-move-menu"
      :style="menuStyle"
      @keydown="onMenuKeydown"
    >
      <div
        v-for="(target, index) in menuTargets"
        :key="target"
        :ref="(element) => setItemEl(element, index)"
        class="card-menu-item"
        role="menuitem"
        tabindex="-1"
        :data-move-target="target"
        @click.stop="chooseTarget(target)"
      >{{ taskTransitionLabel(target) }}</div>
    </div>
  </section>
</template>
