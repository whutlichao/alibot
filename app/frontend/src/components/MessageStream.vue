<!-- 频道消息流（主线视图）

     只渲染**当前 target** 的消息 + 乐观发送项（pending），按 seq 排序；日分隔行按
     `created_at` 的本机日期切。轮次结果按 `base_seq` 挂到**触发它的那条消息**下面
     ——不是与消息无关的横幅（`CONTEXT.md` **轮次结果**）。

     三条纪律（沿用旧实现）：
     - 消息 append-only：没有编辑 / 删除入口，失败项只有「重试发送 / 移除」；
     - 「正在回复」是进程内事实（`activeRounds` / `typing`），不是消息，也不占 seq；
     - 空态与「流里只有审批卡」是两件事：本票只迁消息面，审批卡随它自己的票进来。 -->
<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import MessageItem from './MessageItem.vue';
import OutcomeRows from './OutcomeRows.vue';
import { t } from '../i18n/index.js';
import { dayLabel } from '../lib/format.js';
import { useUiState } from '../stores/ui-state.js';

const props = defineProps({
  state: { type: Object, required: true },
  messages: { type: Array, default: () => [] },
  typingNames: { type: Array, default: () => [] },
});

const emit = defineEmits(['retry', 'discard']);

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
      <div v-else-if="!messages.length" class="empty" id="emptyNoMessage">
        <b>{{ t('empty.noMessageTitle') }}</b>
        <p>{{ t('empty.noMessageBody') }}</p>
      </div>

      <div id="messageList">
        <template v-for="row in rows" :key="row.key">
          <div v-if="row.kind === 'day'" class="day-sep"><span>{{ row.label }}</span></div>
          <template v-else>
            <MessageItem
              :message="row.message"
              :member="state.member"
              @retry="emit('retry', $event)"
              @discard="emit('discard', $event)"
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
