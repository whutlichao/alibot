<!-- 频道消息流（主线视图）

     只渲染**当前 target** 的消息 + 乐观发送项（pending），按 seq 排序；日分隔行按
     `created_at` 的本机日期切。轮次结果按 `base_seq` 挂到**触发它的那条消息**下面
     ——不是与消息无关的横幅（`CONTEXT.md` **轮次结果**）。

     三条纪律（沿用旧实现）：
     - 消息 append-only：没有编辑 / 删除入口，失败项只有「重试发送 / 移除」；
     - 「正在回复」是进程内事实（`activeRounds` / `typing`），不是消息，也不占 seq；
     - 空态与「流里只有审批卡」是两件事（票 #321 补回的那一面，见下）。

     **审批块**（票 #321；旧页面 `studio/index.html:256-261` 的 `#approvalBoard`、`app.js` 的
     `renderChannelApprovals()` / `approvalCardNode()`）：它是消息流里的**行动面**，不是独立面板、
     不是 dock 形态、不新增 `mode` 档位，也不是消息——不占 seq / 未读、不进搜索。位置逐字沿用
     旧页面：**空态之后、`#messageList` 之前**。四条判据：

     1. 读数与排序只有一份：`stores/approvals.js` 的 `recordsFor()`（pending 在前、其余在后，
        组内按 `requested_at` 倒序）——本组件不另算一遍，卡片也不复制第二套倒计时
        （倒计时 / 呈现态 / 掩码全在 `ApprovalCard.vue` + `lib/approvals.js`）；
     2. 空态互斥（旧 `renderMessages()` 8542-8547）：流里只有审批卡时不留「该频道还没有消息」；
     3. 没有记录整块不渲染（旧 `board.hidden = !list.length`）——不摆空容器；
     4. 作用域：只铺**本 target** 的记录（`approvalTargetId`）；私信下 App 给空串，于是这块不出现
        （旧判据：审批归属 target = `state.activeChannelId`，私信下为空）。 -->
<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import ApprovalCard from './ApprovalCard.vue';
import MessageItem from './MessageItem.vue';
import OutcomeRows from './OutcomeRows.vue';
import { t } from '../i18n/index.js';
import { dayLabel } from '../lib/format.js';
import { useUiState } from '../stores/ui-state.js';

const props = defineProps({
  state: { type: Object, required: true },
  messages: { type: Array, default: () => [] },
  typingNames: { type: Array, default: () => [] },
  // 任务快照查名（`stores/task-board.js` 的 `taskOf(taskId)`）：消息上的两个任务 chip 读它取
  // 标题与未读数（票 #309）。查不到就用 id / 未读 0，不编读数。
  taskOf: { type: Function, default: null },
  // 待审批读数（`stores/approvals.js` 的 `pendingCountFor`）：指针 chip 后面的「待审批 N」角标
  // 读它（票 #308 的同一份镜像，本层不另算）。
  pendingCountFor: { type: Function, default: null },
  // 审批镜像（`stores/approvals.js` 的整份 store）：审批块与任务线程的审批段读**同一份**读数，
  // 因此这里收 store 而不是一份拷贝。`null` = 还没接上（不渲染审批块）。
  approvals: { type: Object, default: null },
  // 审批块的归属 target：频道 id；私信下 App 传空串（旧 `state.activeChannelId` 在私信下为空）。
  approvalTargetId: { type: String, default: '' },
  // 离线时审批动作不可发（与旧 `approvalActionButton` 的 `!state.online` 同一条）。
  online: { type: Boolean, default: true },
});

const emit = defineEmits(['retry', 'discard', 'open-task']);

const ui = useUiState();
const stream = ref(null);

/** 消息 + 日分隔行：一次算出来，模板里只遍历（不在渲染函数里做二次加工）。 */
const rows = computed(() => {
  const out = [];
  let lastDay = null;
  props.messages.forEach((message) => {
    const day = dayLabel(message.created_at);
    if (day !== lastDay) {
      out.push({ kind: 'day', key: 'day-' + day + '-' + message.seq, label: day });
      lastDay = day;
    }
    out.push({ kind: 'message', key: message.seq ? 'seq-' + message.seq : 'pending-' + message.clientId, message });
  });
  return out;
});

/** 某条消息下面的轮次结果（按 `base_seq` 取；同一 `base_seq` 下的全部成员记录一起给）。 */
function outcomesFor(seq) {
  const byMember = props.state.outcomes[seq];
  if (!byMember) return [];
  return Object.keys(byMember).flatMap((memberId) => byMember[memberId]);
}

const hasTarget = computed(() => Boolean(props.state.activeTargetId));

/** 本机人类成员的 id（`state.member` 是 `/api/workspace` 的 owner 行）：own 气泡的身份判据。 */
const ownerId = computed(() => (props.state.member && props.state.member.id) || '');

/** 本 target 的审批记录（pending 在前；终态是历史，仍如实铺出来，只是没有动作）。
    读数只从 store 来——排序、呈现态、倒计时都在那一层与 `ApprovalCard.vue` 里，本层不重算。 */
const approvalRecords = computed(() => {
  if (!props.approvals || !props.approvalTargetId) return [];
  return props.approvals.recordsFor(props.approvalTargetId);
});

/** 发起审批的成员：成员表里查一行；查不到（成员已删除）时退回 id，不编名字。 */
function approvalMember(record) {
  const id = record && record.member_id;
  if (!id) return null;
  const channelMembers = (props.state.channelMembers || {})[props.state.activeTargetId] || [];
  const found = [...(props.state.members || []), ...channelMembers].find((item) => item.id === id);
  return found || null;
}

function submitApproval(record, payload) {
  if (!props.approvals || !record || !props.approvalTargetId) return;
  props.approvals.submit(props.approvalTargetId, record.reply_id, payload.confirmed);
}

/** 贴底：只有**首次成功载入**与「自己在看时新消息到达」才滚；回看历史时不打断。 */
function scrollToBottom() {
  const node = stream.value;
  if (node) node.scrollTop = node.scrollHeight;
}

watch(
  () => props.messages.length,
  async (next, previous) => {
    if (!previous) return;
    await nextTick();
    const node = stream.value;
    if (!node) return;
    // 已经在底部附近（120px 以内）才跟随：用户往上回看时不能被拉回来。
    if (node.scrollHeight - node.scrollTop - node.clientHeight < 120) scrollToBottom();
  },
);

defineExpose({ scrollToBottom });
</script>

<template>
  <section id="viewMessages" ref="stream" class="stream" data-od-id="messages-view">
    <div class="stream-inner" id="streamInner">
      <div v-if="!hasTarget" class="empty" id="emptyNoChannel">
        <b>{{ t('empty.noChannelTitle') }}</b>
        <p>{{ t('empty.noChannelBody') }}</p>
      </div>
      <!-- 空态互斥（旧 `renderMessages()` 8542-8547）：流里只有一张审批卡时，
           不该同时留着「该频道还没有消息」。 -->
      <div v-else-if="!messages.length && !approvalRecords.length" class="empty" id="emptyNoMessage">
        <b>{{ t('empty.noMessageTitle') }}</b>
        <p>{{ t('empty.noMessageBody') }}</p>
      </div>

      <!-- 审批卡（票 #321 / 旧 `index.html:256-261`）：消息流里的行动面。它不是消息——不占 seq /
           未读，不进搜索；没有记录时整块不渲染（不留空容器）。 -->
      <div
        v-if="hasTarget && approvalRecords.length"
        id="approvalBoard"
        class="approval-board"
        role="region"
        :aria-label="t('approval.section')"
        data-od-id="approval-board"
      >
        <div class="d-sec-title">{{ t('approval.section') }}</div>
        <ApprovalCard
          v-for="item in approvalRecords"
          :key="item.reply_id"
          :record="item"
          :target-id="approvalTargetId"
          :member="approvalMember(item)"
          :now="approvals.now()"
          :online="online"
          :in-flight="approvals.isInFlight(item.reply_id)"
          :error="approvals.errorFor(item.reply_id)"
          @submit="submitApproval(item, $event)"
        />
      </div>

      <div id="messageList">
        <template v-for="row in rows" :key="row.key">
          <div v-if="row.kind === 'day'" class="day-sep"><span>{{ row.label }}</span></div>
          <template v-else>
            <MessageItem
              :message="row.message"
              :member="state.member"
              :task-of="taskOf"
              :pending-count-for="pendingCountFor"
              :owner-id="ownerId"
              @retry="emit('retry', $event)"
              @discard="emit('discard', $event)"
              @open-task="emit('open-task', $event)"
            />
            <!-- 轮次结果挂在**触发它的那条消息**下面：同一 `base_seq` 至多一行摘要 +
                 每个 failed 各一行（由 OutcomeRows 内部拆）。 -->
            <OutcomeRows
              v-if="row.message.seq && outcomesFor(row.message.seq).length"
              :records="outcomesFor(row.message.seq)"
              :members="state.members"
              :agents="state.agents"
              :expanded="ui.isOutcomeExpanded(row.message.seq)"
              @toggle="ui.toggleOutcome(row.message.seq)"
            />
          </template>
        </template>
      </div>

      <!-- 「正在回复」：进程内事实（该成员有一轮 in-flight），不是消息，不占 seq。 -->
      <div v-if="typingNames.length" class="typing-line" data-od-id="typing-line">
        <span>{{ t('typing.working', { name: typingNames.map((item) => item.name).join(t('typing.nameSeparator')) }) }}</span>
      </div>
    </div>
  </section>
</template>
