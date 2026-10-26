<!-- 轮次结果在消息流里的呈现（挂在**触发它的那条消息**下面，按 `base_seq` 绑定）

     三类行，同一份后端事实（`GET /api/channels/{id}/round-outcomes` + live `round_outcome` 帧）：
     1. **摘要行**（默认折叠，一条消息下至多一行）：`ignored` / `budget-exhausted` / `merged` /
        `silent` 四态收成一行——它们是**正常的协议选择**，但不得淹没对话，也不得与「它坏了」混为一谈；
     2. **失败行**（直接展开，每个 `failed` 各一行）：失败是用户主诉的缺口，不藏在折叠后面；
     3. **收尾行**（`replied` 但 `finished_reason ∈ {interrupted, exceed_max_iters}`）：同一套
        逐成员明细形态。

     这三行**都不是消息**：不占 seq（用 `data-base-seq`）、不参与「数几条消息」的统计、
     也没有「重试发送」按钮（消息 append-only，重试那件事的入口是再发一条消息）。

     失败行**不渲染**「看成员状态」入口：它打开的是 Agent 详情，而那个面还没迁到 Vue 版
     （ADR-0038 §五）。状态词与后端 `reason` 原文照常呈现——那才是这一行的实质信息。 -->
<script setup>
import { computed } from 'vue';
import { t } from '../i18n/index.js';
import { outcomeLabel, outcomeNote, timeLabel } from '../lib/format.js';

const props = defineProps({
  records: { type: Array, default: () => [] },
  // 成员表：把 `member_id` 落到显示名（拿不到就退回 id，不伪造名字）。
  members: { type: Array, default: () => [] },
  agents: { type: Array, default: () => [] },
  // 摘要行的展开态由调用方持有：键是**触发**它的那条消息的 seq，重挂之后要还是同一个态。
  expanded: { type: Boolean, default: false },
});

const emit = defineEmits(['toggle']);

/** 摘要行要覆盖的四种轮次结果（`failed` 直接展开，`replied` 由它自己的回复消息承载）。 */
const SUMMARY_STATUSES = ['ignored', 'budget-exhausted', 'merged', 'silent'];

function memberName(memberId) {
  const member = props.members.find((item) => item.id === memberId);
  return member ? member.display_name : memberId;
}

/** 名单的**唯一**拼法：≤2 个逐个列出，>2 个写「甲、乙 等 N 人」（摘要行与折叠说明共用）。 */
function joinNames(names) {
  if (names.length <= 2) return names.join(t('common.nameListSep'));
  const head = names[0] + t('common.nameListSep') + names[1];
  return t('common.nameListMore', { names: head, count: names.length });
}

const summaryItems = computed(() => props.records.filter((record) => SUMMARY_STATUSES.includes(record.status)));
const failedItems = computed(() => props.records.filter((record) => record.status === 'failed'));
const finishedItems = computed(() =>
  props.records.filter(
    (record) => record.status === 'replied' && ['interrupted', 'exceed_max_iters'].includes(record.finished_reason),
  ),
);
const summaryNames = computed(() => joinNames(summaryItems.value.map((record) => memberName(record.member_id))));

/** 折叠行那一句「为什么」：按 status 归组，每组用自己的后端事实说法（不把三态混为一谈）。 */
const summaryWhy = computed(() => {
  const order = [];
  const groups = new Map();
  summaryItems.value.forEach((record) => {
    if (!groups.has(record.status)) {
      groups.set(record.status, []);
      order.push(record.status);
    }
    groups.get(record.status).push(memberName(record.member_id));
  });
  const mixed = order.length > 1;
  return order
    .map((status) => {
      const text = outcomeNote(status);
      // 单一状态：名单已在折叠控件上，这里只陈述「为什么」（不重复一遍名字）。
      if (!mixed) return text;
      return joinNames(groups.get(status)) + t('common.colonSep') + text;
    })
    .join(t('common.clauseSep'));
});

const baseSeq = computed(() => (summaryItems.value[0] ? summaryItems.value[0].base_seq : null));

/** 失败行的状态词：`未启用` 是成员**此刻**的状态，`这一轮失败` 是**库里那一次**轮次的结论。 */
function failedStatus(record) {
  const agent = props.agents.find((item) => item.id === record.member_id);
  const status = agent ? agent.status : null;
  return status === 'offline' ? { disabled: true, label: t('outcome.stateDisabled') } : { disabled: false, label: t('outcome.stateFailed') };
}
</script>

<template>
  <!-- 摘要行：一条消息下至多一行，默认折叠。 -->
  <article
    v-if="summaryItems.length"
    class="msg outcome-summary"
    :data-base-seq="baseSeq"
    role="status"
    :aria-label="t('outcome.summaryAria', { names: summaryNames, count: summaryItems.length })"
  >
    <div class="msg-body">
      <div class="msg-head">
        <button
          class="msg-outcome-summary-toggle"
          type="button"
          :data-summary-toggle="baseSeq"
          :aria-expanded="expanded ? 'true' : 'false'"
          :aria-controls="'outcome-summary-' + baseSeq"
          @click="emit('toggle')"
        >
          <span class="msg-outcome-summary-mark">{{ expanded ? '\u25be' : '\u25b8' }}</span>
          <span class="msg-outcome-summary-names">{{ summaryNames }}</span>
          <span class="msg-outcome-summary-text">{{ expanded ? t('outcome.collapse') : t('outcome.expand') }}</span>
        </button>
        <span class="msg-time">{{ timeLabel(summaryItems[0].finished_at) }}</span>
      </div>
      <div class="msg-outcome-summary-why">{{ summaryWhy }}</div>
      <!-- 逐成员明细**恒常渲染**、用 `hidden` 切可见性：`aria-controls` 指向的 id 在任何时刻
           都要能解析到，否则折叠态就是一个悬空引用。 -->
      <div :id="'outcome-summary-' + baseSeq" class="msg-outcome-details" :hidden="!expanded">
        <div
          v-for="record in summaryItems"
          :key="record.id"
          class="msg-outcome-line"
          :data-outcome="record.id"
          :data-member="record.member_id"
          :data-status="record.status"
        >
          <div class="msg-outcome-line-head">
            <span class="msg-outcome-line-author">{{ memberName(record.member_id) }}</span>
            <span class="msg-outcome-line-state">{{ outcomeLabel(record.status) }}</span>
            <span class="msg-time">{{ timeLabel(record.finished_at) }}</span>
          </div>
          <div class="msg-outcome-line-reason" :data-od-id="'round-reason-' + record.id">
            {{ record.reason || t('outcome.lineReasonEmpty') }}
          </div>
        </div>
      </div>
    </div>
  </article>

  <!-- 失败行：直接展开，每个 failed 各一行。 -->
  <article
    v-for="record in failedItems"
    :key="'failed-' + record.id"
    class="msg outcome"
    :data-outcome="record.id"
    :data-member="record.member_id"
    :data-base-seq="record.base_seq"
    role="status"
    :aria-label="t('outcome.failedAria', { name: memberName(record.member_id), reason: record.reason || t('outcome.lineReasonEmpty') })"
  >
    <div class="msg-body">
      <div class="msg-head">
        <span class="msg-author msg-outcome-author">{{ memberName(record.member_id) }}</span>
        <span class="msg-tag">{{ t('outcome.tag') }}</span>
        <span class="msg-outcome-state">{{ failedStatus(record).label }}</span>
        <span class="msg-time">{{ timeLabel(record.finished_at) }}</span>
      </div>
      <div v-if="failedStatus(record).disabled" class="msg-outcome-note">
        {{ t('outcome.disabledNote', { name: memberName(record.member_id) }) }}
      </div>
      <div class="msg-outcome-reason" :data-od-id="'round-reason-' + record.id">
        {{ record.reason || t('outcome.lineReasonEmpty') }}
      </div>
    </div>
  </article>

  <!-- 收尾行：`replied` 但被中断 / 超过迭代上限——同一套逐成员明细形态。 -->
  <div
    v-for="record in finishedItems"
    :key="'finished-' + record.id"
    class="msg-outcome-line"
    :data-outcome="record.id"
    :data-member="record.member_id"
    :data-status="record.status"
  >
    <div class="msg-outcome-line-head">
      <span class="msg-outcome-line-author">{{ memberName(record.member_id) }}</span>
      <span class="msg-outcome-line-state">{{ outcomeLabel(record.status) }}</span>
      <span class="msg-time">{{ timeLabel(record.finished_at) }}</span>
    </div>
    <div class="msg-outcome-line-reason" :data-od-id="'round-reason-' + record.id">
      {{ record.reason || t('outcome.lineReasonEmpty') }}
    </div>
  </div>
</template>
