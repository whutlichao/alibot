<!-- 消息流里的一条消息

     呈现面（与旧 `messageNode()` 逐条对齐）：
     - 旁白式交代（#57）先判：它仍是消息（占 seq / 有作者与时间），但降成一行的低权重呈现；
     - 系统事件消息（`kind='system'`）不渲染 Markdown：正文 100% 由后端模板生成；
     - human / agent 的对话正文渲染 Markdown（ADR-0037），提及按后端区间包标签；
     - 乐观发送项（还没 seq）与发送失败项各有自己的状态 capsule（字面量来自词条表，
       写在 `data-state-label` 上，CSS 用 `content: attr()` 取——CSS 里不放语言文案）；
     - owner 自己发的对话消息挂 `own`，由 `app.css` 呈现为右侧气泡（判据见 `isOwn`）；
       system 事件不是人说的话，即使作者是 owner 也留在左侧。

     **任务 chip 归 `TaskRefChip.vue`**（票 #309）：`task_ref` 的引用 chip 与 `task_pointer` 的频道
     指针 chip 是同一个类名、两条语义，判据只在那个组件里有一处——系统消息与对话两条分支都挂它
     （旧 `appendTaskRefChip()` 同样在这两处各调一次）。点击往上抛 `open-task`，落点是 `App.vue`
     的 `openTask()`。

     **单根节点**是刻意：`article.msg` 是 `#messageList` 的直接子节点（旧 DOM 形状），
     三种呈现只是同一个节点上的三种内容，不是三个并列的根——否则父组件无法把属性
     （`data-seq` / `role` / `aria-label`）挂到「这一条消息」上。 -->
<script setup>
import { computed } from 'vue';
import AttachmentChip from './AttachmentChip.vue';
import MarkdownBody from './MarkdownBody.vue';
import MemberAvatar from './MemberAvatar.vue';
import TaskRefChip from './TaskRefChip.vue';
import { t } from '../i18n/index.js';
import { timeLabel } from '../lib/format.js';

const props = defineProps({
  message: { type: Object, required: true },
  // 成员表（作者缺失时退回它；也用于旁白行的作者名与色位）。
  member: { type: Object, default: null },
  // 任务快照查名（`taskOf(taskId)`）：两个 chip 的标题与未读数都从那里读，读不到就用 id。
  taskOf: { type: Function, default: null },
  // 待审批读数（`pendingCountFor(targetId)`）：指针 chip 后面的角标读它，不用本组件再算一遍。
  pendingCountFor: { type: Function, default: null },
  // 本机人类成员的 id（`GET /api/workspace` 的 `member.id`）：作者与它相同的对话消息挂
  // `own`。空串 = 身份数据还没接上——宁可全在左侧，也不凭显示名猜。
  ownerId: { type: String, default: '' },
});

const emit = defineEmits(['retry', 'discard', 'open-task']);

/** 旁白式交代的判据是**形态**，不是措辞表（换措辞、换语言都不会让判据失效）。 */
const NARRATION_MAX_LENGTH = 24;
const NARRATION_PAREN_PAIRS = { '\uFF08': '\uFF09', '(': ')' };

/** 这一条是不是旁白式交代；是就返回降档后要显示的正文，否则 `null`。 */
function narrationOf(message) {
  if (!message || message.kind !== 'agent') return null;
  if (!Number.isInteger(message.seq) || message.seq <= 0) return null;
  const body = typeof message.body === 'string' ? message.body.trim() : '';
  if (!body) return null;
  // 按**码点**数长度（`body.length` 数的是 UTF-16 单元，会把汉字算成 1、生僻字算成 2）。
  const chars = Array.from(body);
  if (chars.length > NARRATION_MAX_LENGTH) return null;
  // 多行的括注更可能是真内容（清单 / 引用），不降档。
  if (/[\r\n]/.test(body)) return null;
  const close = NARRATION_PAREN_PAIRS[chars[0]];
  if (!close || chars[chars.length - 1] !== close) return null;
  let depth = 0;
  for (let index = 0; index < chars.length; index += 1) {
    if (chars[index] === chars[0]) depth += 1;
    else if (chars[index] === close) {
      depth -= 1;
      // 括号在**中途**就回到 0，说明它不是「整条被一对括住」（例如「（一）（二）」）。
      if (depth === 0 && index !== chars.length - 1) return null;
    }
  }
  return depth === 0 ? body : null;
}

const narration = computed(() => narrationOf(props.message));
const isSystem = computed(() => props.message.kind === 'system');
const failed = computed(() => props.message.status === 'failed');
// 失败的消息还没有 seq，但它不是「待确认」——两个状态不能同时挂在一个节点上。
const pending = computed(() => !props.message.seq && !failed.value);
const author = computed(
  () => props.message.author || props.member || { display_name: 'owner', kind: 'owner', avatar_slot: 4 },
);
const isAgent = computed(() => author.value.kind === 'agent');
/** 是不是「我」发的对话消息：只认身份数据（作者 id），不认显示名；system 事件不算。
    乐观 / 失败项自带 `author`（`stores/workspace.js` 的 pending 项），因此三种状态一起覆盖。 */
const isOwn = computed(() => {
  const authorId = props.message.author && props.message.author.id;
  return Boolean(props.ownerId) && Boolean(authorId) && authorId === props.ownerId && !isSystem.value;
});

const rootClass = computed(() => {
  if (narration.value !== null) return 'msg narration';
  if (isSystem.value) return 'msg sys';
  return 'msg'
    + (isOwn.value ? ' own' : '')
    + (pending.value ? ' pending' : '')
    + (failed.value ? ' failed' : '');
});

/** 两个 capsule 的字面量走 CSS 的 `content: attr(data-state-label)`（语言取值只在这一处）。 */
const stateLabel = computed(() => {
  if (pending.value) return t('msg.pendingTag');
  if (failed.value) return t('msg.failedTag');
  return undefined;
});

const avatarSize = computed(() => (narration.value !== null || isSystem.value ? 'sm' : ''));
/** 附件（ADR-0014）：绑定与否决定它是链接还是纯文本，判据只在 `AttachmentChip` 里有一处。 */
const attachments = computed(() =>
  Array.isArray(props.message.attachments) ? props.message.attachments : [],
);
</script>

<template>
  <article
    :class="rootClass"
    :data-seq="pending ? undefined : message.seq"
    :data-pending="pending ? message.clientId : undefined"
  >
    <MemberAvatar :name="author.display_name" :slot="author.avatar_slot" :size="avatarSize" />

    <!-- 旁白式交代：一行、低权重，但**仍是**消息（占 seq / 有作者与时间）。 -->
    <div v-if="narration !== null" class="msg-body">
      <div class="msg-head">
        <span class="msg-author is-agent">{{ author.display_name }}</span>
        <span class="msg-tag">{{ t('narration.tag') }}</span>
        <span class="msg-narration-text">{{ narration }}</span>
        <span class="msg-time">{{ timeLabel(message.created_at) }}</span>
      </div>
    </div>

    <!-- 系统事件消息：正文不渲染 Markdown，降一档到 --muted。 -->
    <div v-else-if="isSystem" class="msg-body">
      <div class="msg-head">
        <span class="msg-author">{{ author.display_name || t('msg.systemAuthor') }}</span>
        <span class="msg-tag">{{ t('msg.tagEvent') }}</span>
        <span class="msg-time">{{ timeLabel(message.created_at) }}</span>
      </div>
      <div class="msg-text">{{ message.body }}</div>
      <div v-if="attachments.length" class="msg-attachments">
        <AttachmentChip v-for="item in attachments" :key="item.id" :attachment="item" />
      </div>
      <!-- 任务 chip（旧 `appendTaskRefChip()` 在 system 分支同样会挂）：频道指针消息就是一条
           system 记录，不挂这里它就一个入口都没有。 -->
      <TaskRefChip
        :message="message"
        :task-of="taskOf"
        :pending-count-for="pendingCountFor"
        @open-task="emit('open-task', $event)"
      />
    </div>

    <!-- human / agent 的对话正文。 -->
    <div v-else class="msg-body">
      <div class="msg-head" :data-state-label="stateLabel">
        <span class="msg-author" :class="{ 'is-agent': isAgent }">{{ author.display_name }}</span>
        <span v-if="isAgent" class="msg-tag">{{ t('msg.tagAgent') }}</span>
        <span class="msg-time">{{ timeLabel(message.created_at) }}</span>
      </div>
      <MarkdownBody :body="message.body" :message="message" />
      <div v-if="attachments.length" class="msg-attachments">
        <AttachmentChip v-for="item in attachments" :key="item.id" :attachment="item" />
      </div>
      <TaskRefChip
        :message="message"
        :task-of="taskOf"
        :pending-count-for="pendingCountFor"
        @open-task="emit('open-task', $event)"
      />
      <div v-if="failed" class="msg-retry">
        <span>{{ t('msg.notDelivered', { reason: message.reason || t('msg.sendFailed') }) }}</span>
        <button class="btn btn-sm" type="button" @click="emit('retry', message.clientId)">{{ t('msg.retry') }}</button>
        <button class="btn btn-sm" type="button" @click="emit('discard', message.clientId)">{{ t('msg.discard') }}</button>
      </div>
    </div>
  </article>
</template>
