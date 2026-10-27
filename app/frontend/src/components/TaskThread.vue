<!-- 任务线程（dock 的**第二形态**，票 #307；旧页面 `#dockTask` / `renderTaskThread()`）

     它与「频道成员」和「运行面板」共用同一个 380px dock 容器，因此「同时显示两件东西」在构造上
     不可能发生——形态由父组件的 `dockTarget` 推导（`App.vue`），本组件只铺一条线程的内容。

     类名与 `data-od-id` 逐字沿用旧页面的 `#dockTask` 段：`app.css` 零改动，旧用例族与自动化取证
     按同一批名字定位（`.task-thread` / `.tt-summary` / `.tt-status[data-status]` / `.tt-log` /
     `thread-title-input` / `thread-move-<target>` / `thread-assign-<memberId>` …）。

     两条纪律：

     1. **前端不持迁移表副本**：推进 chips 只铺载荷 `allowed_transitions` 里的目标；没有目标时铺一句
        「已关闭，无可用推进」，不是空容器（终态确实是那个意思）。
     2. **正文复用主线那一套渲染**（`MarkdownBody.vue` + `AttachmentChip.vue`）：任务线程的讨论与
        消息流是同一类正文，另造一份渲染器就是让两份实现各自漂。`system` 行走 `.tt-log`（它是**记录**
        不是对话，ADR-0037：只渲染「说话」）。

     写路径一律在 `stores/task-thread.js`：组件只读 state 与调用动作，失败原因由 store 交给它铺。
     审批段归 T8，本票**不渲染假节点**。 -->
<script setup>
import { computed, ref, watch } from 'vue';
import AttachmentChip from './AttachmentChip.vue';
import MarkdownBody from './MarkdownBody.vue';
import MemberAvatar from './MemberAvatar.vue';
import { t } from '../i18n/index.js';
import { timeLabel } from '../lib/format.js';
import { allowedTransitions, taskNumber } from '../lib/task-board.js';

const props = defineProps({
  // 任务线程的读数与写路径（`createTaskThreadStore()` 的返回值）。
  thread: { type: Object, required: true },
  // 工作区镜像里的成员表（指派 pill 的名字与色位从它查）。
  members: { type: Array, default: () => [] },
  // 指派候选 = **所属频道**的 Agent 成员（`null` = 还没读到；空数组 = 真的没有）。
  assignees: { type: Array, default: null },
  // 该频道的成员表读失败了（指派候选不可用，如实说事实而不是摆一个空清单）。
  assigneesFailed: { type: Boolean, default: false },
  // 所属频道的协作模式（`'team'` 才铺那句「任务线程只唤醒队长」；配置读不到 ⇒ `null`，什么都不说）。
  channelMode: { type: String, default: null },
  online: { type: Boolean, default: true },
});

const emit = defineEmits(['close', 'open-run-panel']);

const state = computed(() => props.thread.state);
const task = computed(() => state.value.task);

/** `system` 记录（状态变更历史）与讨论分开：前者是**记录**，后者才是对话（ADR-0037）。 */
const systemMessages = computed(() => state.value.messages.filter((message) => message.kind === 'system'));
const replies = computed(() => state.value.messages.filter((message) => message.kind !== 'system'));

const unread = computed(() => Number(task.value && task.value.unread_count) || 0);

const headSub = computed(() => {
  const current = task.value;
  if (!current) return '';
  return (
    t('task.threadHeadSub', { channel: current.channel_name || '', count: replies.value.length })
    + (unread.value ? t('task.threadUnread', { count: unread.value }) : '')
  );
});

const transitions = computed(() => allowedTransitions(task.value));

const canSend = computed(() => Boolean(draft.value.trim()) && props.online && !state.value.sending);

/** 指派者：成员表里查一行；查不到（成员已删除）时说「已删除的成员」，不编名字。 */
const assigneeMember = computed(() => {
  const id = task.value && task.value.assignee_member_id;
  if (!id) return null;
  const member = props.members.find((item) => item.id === id);
  return member || { id, display_name: t('task.deletedAssignee'), avatar_slot: 0 };
});

// ─── 标题就地编辑（Enter 保存 / Esc 取消 / 失焦保存） ──────────────────────────

const titleDraft = ref('');
const titleInput = ref(null);

/** 外部读数变化时同步输入框；**正在编辑**时不动它（人的半句话不该被一次刷新抹掉）。 */
watch(
  () => (task.value ? task.value.title : ''),
  (title) => {
    const node = titleInput.value;
    if (node && node === document.activeElement) return;
    titleDraft.value = title;
  },
  { immediate: true },
);

async function commitTitle() {
  const value = titleDraft.value;
  const current = task.value;
  if (!current) return;
  // 空标题 / 没改过 ⇒ 不发请求；前者回退到后端事实（任务行不允许无标题）。
  if (!value.trim() || value.trim() === current.title) {
    titleDraft.value = current.title;
    return;
  }
  await props.thread.saveTitle(value);
  if (task.value) titleDraft.value = task.value.title;
}

function cancelTitle() {
  titleDraft.value = task.value ? task.value.title : '';
}

function onTitleKeydown(event) {
  if (event.key === 'Enter') {
    event.preventDefault();
    commitTitle();
    if (titleInput.value) titleInput.value.blur();
    return;
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    cancelTitle();
    if (titleInput.value) titleInput.value.blur();
  }
}

// ─── 回复（Enter 发送 / Shift+Enter 换行） ──────────────────────────────────

const draft = ref('');
const replyInput = ref(null);

async function submitReply() {
  if (!canSend.value) return;
  const text = draft.value;
  const result = await props.thread.send(text);
  // 失败时草稿**仍留在输入框里**（重试发的是同一句话），原因铺在 composer 上方。
  if (result && result.ok) draft.value = '';
}

function onReplyKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey) return;
  event.preventDefault();
  submitReply();
}

function focusReply() {
  if (replyInput.value) replyInput.value.focus();
}
</script>

<template>
  <div class="task-thread" data-od-id="task-thread">
    <div class="dock-head">
      <div class="dock-id">
        <div class="meta">
          <div class="dock-name">
            <span class="dock-tag">{{ t('task.threadTag') }}</span>
            <span class="dock-task-id" id="ttHeadId">{{ taskNumber(task && task.id) }}</span>
          </div>
          <div class="dock-role"><span id="ttHeadSub">{{ headSub }}</span></div>
        </div>
        <!-- 任务运行面板入口（旧 `#btnThreadRunPanel`）：把 task target 的分派 / 成员过程 / Plan / SOP
             在同一个 dock 里打开（`task_id` 过滤同一份 `GET /team-runs`）。 -->
        <button
          class="icon-btn"
          type="button"
          data-od-id="btn-thread-run-panel"
          :title="t('runPanel.taskEntryTitle')"
          :aria-label="t('runPanel.taskEntryTitle')"
          @click="emit('open-run-panel')"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />
          </svg>
        </button>
        <button
          class="icon-btn"
          type="button"
          data-od-id="btn-thread-close"
          :aria-label="t('dock.threadClose')"
          @click="emit('close')"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 6 6 18" /><path d="m6 6 12 12" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 吸顶摘要：标题（就地可编辑）+ 状态 pill + 指派 pill。标题是 `<input>` 而不是只读 `<h2>`：
         任务行是可变的（`CONTEXT.md` **任务**词条：标题 / 状态 / 指派者）。 -->
    <div class="tt-summary">
      <h2 class="tt-title">
        <label class="sr-only" for="ttTitleInput">{{ t('task.titleLabel') }}</label>
        <input
          id="ttTitleInput"
          ref="titleInput"
          v-model="titleDraft"
          class="tt-title-input"
          data-od-id="thread-title-input"
          @keydown="onTitleKeydown"
          @blur="commitTitle"
        />
      </h2>
      <div class="tt-meta">
        <!-- 状态色位由 CSS 的 `.tt-status[data-status=…]` 表驱动（与 `.col` / `.card` 共用同一张表）：
             这里只声明状态，不内联色位。 -->
        <span
          class="tt-status"
          data-od-id="thread-status"
          :data-status="task ? task.status : undefined"
        >
          <span class="dot"></span>
          <span>{{ task ? thread.statusLabel(task.status) : '' }}</span>
        </span>
        <!-- 指派 pill 是 `<span>` 而不是 `<button>`：它不对应任何动作（真正的入口是下方「指派」chips）。 -->
        <span
          v-if="assigneeMember"
          class="assignee"
          data-od-id="thread-assignee"
        >
          <MemberAvatar :name="assigneeMember.display_name" :slot="assigneeMember.avatar_slot || 0" size="sm" />
          {{ assigneeMember.display_name }}
          <span class="assignee-hint">{{ t('task.reassignHint') }}</span>
        </span>
        <span v-else class="assignee unassigned" data-od-id="thread-assignee">{{ t('task.unassignedPill') }}</span>
      </div>
      <!-- team 频道的事实提示（#154 / ADR-0018）：任务线程只唤醒队长。配置读不到时不说任何结论。 -->
      <div
        v-if="channelMode === 'team'"
        class="team-notice"
        role="note"
        data-od-id="thread-team-notice"
      >{{ t('task.teamNotice') }}</div>
    </div>

    <div class="tt-actions">
      <div class="tt-actions-row">
        <span class="tt-label">{{ t('dock.move') }}</span>
        <div class="tt-chips" data-od-id="thread-transitions">
          <template v-if="transitions.length">
            <button
              v-for="target in transitions"
              :key="target"
              class="filter-chip"
              type="button"
              :data-od-id="'thread-move-' + target"
              :disabled="!online"
              :aria-pressed="false"
              @click="thread.changeStatus(target)"
            >{{ thread.transitionLabel(target) }}</button>
          </template>
          <!-- 终态没有出边：说事实，不摆一个空容器。 -->
          <span v-else class="tt-note">{{ t('task.noTransitions') }}</span>
        </div>
      </div>
      <div class="tt-actions-row">
        <span class="tt-label">{{ t('dock.assign') }}</span>
        <div class="tt-chips" data-od-id="thread-assign">
          <span v-if="assigneesFailed" class="tt-note">{{ t('task.assignUnavailable') }}</span>
          <span v-else-if="assignees === null" class="tt-note">{{ t('task.assignLoading') }}</span>
          <span v-else-if="!assignees.length" class="tt-note">{{ t('task.assignNoAgents') }}</span>
          <template v-else>
            <button
              v-for="member in assignees"
              :key="member.id"
              class="filter-chip"
              :class="{ 'is-on': task && task.assignee_member_id === member.id }"
              type="button"
              :data-od-id="'thread-assign-' + member.id"
              :aria-pressed="task && task.assignee_member_id === member.id ? 'true' : 'false'"
              :disabled="!online"
              @click="thread.changeAssignee(member.id)"
            >
              <MemberAvatar :name="member.display_name" :slot="member.avatar_slot || 0" size="sm" />
              {{ member.display_name }}
            </button>
            <button
              v-if="task && task.assignee_member_id"
              class="filter-chip"
              type="button"
              data-od-id="thread-unassign"
              :disabled="!online"
              @click="thread.changeAssignee(null)"
            >{{ t('task.clearAssign') }}</button>
          </template>
        </div>
      </div>
    </div>

    <div class="tt-scroll">
      <div v-if="state.error || state.loading" class="tt-empty">
        {{ state.error ? t('toast.openTaskFailed', { reason: state.error }) : t('runPanel.loading') }}
      </div>
      <template v-else>
        <div class="d-sec">
          <div class="d-sec-title">{{ t('task.discussionCount', { count: replies.length }) }}</div>
          <div id="ttReplies">
            <div v-for="item in replies" :key="item.id" class="tt-reply-row">
              <MemberAvatar
                :name="(item.author || {}).display_name || ''"
                :slot="(item.author || {}).avatar_slot || 0"
                size="sm"
              />
              <div class="msg-body">
                <div class="msg-head">
                  <span class="msg-author" :class="{ 'is-agent': item.author && item.author.kind === 'agent' }">
                    {{ (item.author || {}).display_name || '' }}
                  </span>
                  <span v-if="item.author && item.author.kind === 'agent'" class="msg-tag">{{ t('msg.tagAgent') }}</span>
                  <span class="msg-time">{{ timeLabel(item.created_at) }}</span>
                </div>
                <!-- 主线同一套渲染器（不另造）：提及区间由后端给，MarkdownBody 只按区间包标签。 -->
                <MarkdownBody :body="item.body" :message="item" />
                <div v-if="item.attachments && item.attachments.length" class="msg-attachments">
                  <AttachmentChip v-for="file in item.attachments" :key="file.id" :attachment="file" />
                </div>
              </div>
            </div>
            <div v-if="!replies.length" class="tt-empty">{{ t('task.repliesEmpty') }}</div>
          </div>
        </div>
        <div class="d-sec">
          <div class="d-sec-title">{{ t('dock.history') }}</div>
          <ul class="tt-log" data-od-id="thread-history">
            <li
              v-for="(item, index) in systemMessages"
              :key="item.id"
              :class="{ 'is-now': index === systemMessages.length - 1 }"
            >
              {{ ((item.author || {}).display_name ? (item.author || {}).display_name + ' ' : '') + item.body
              }}<span class="ts">{{ timeLabel(item.created_at) }}</span>
            </li>
          </ul>
          <div v-if="!systemMessages.length" class="tt-empty">{{ t('task.historyEmpty') }}</div>
        </div>
      </template>
    </div>

    <div class="tt-reply">
      <div v-if="state.sendError" class="form-error" role="alert" data-od-id="thread-reply-error">
        {{ t('toast.taskMessageFailed', { reason: state.sendError }) }}
      </div>
      <div class="composer-box">
        <textarea
          id="taskComposerInput"
          ref="replyInput"
          v-model="draft"
          class="composer-input"
          rows="1"
          data-od-id="thread-reply-input"
          :placeholder="t('dock.replyPlaceholder')"
          :aria-label="t('dock.replyInputLabel')"
          @keydown="onReplyKeydown"
        ></textarea>
        <div class="composer-bar">
          <span class="composer-hint">{{ t('dock.replyHint') }}</span>
          <span class="sep"></span>
          <button
            class="composer-send"
            type="button"
            data-od-id="thread-reply-send"
            :aria-label="t('dock.replySend')"
            :disabled="!canSend"
            @click="submitReply"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" /><path d="m21.854 2.147-10.94 10.939" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
