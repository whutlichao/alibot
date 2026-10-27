<!-- 审批卡（票 #308 / T8；旧页面 `app.js:3286-3355` 的 `approvalCardNode`）

     一张卡 = 一条 `approval_request` 的呈现。类名、`data-od-id` 与结构逐字沿用旧页面，
     `app.css` 零改动：`.approval-card[data-approval-id][data-approval-target][data-state]`（`role="group"`）
     > `.approval-head`（`.approval-tag` + 头像 + `.approval-member` + `.approval-state[data-state]` +
     `.approval-remaining[data-urgent]`）/ `.approval-tools`（`.approval-tool` > `.approval-tool-name` +
     `.approval-tool-input`）/ `.approval-actions`（两枚 `[data-approval-action]`）/ `.approval-note`
     （在途）/ `.approval-error`（失败）。

     四条判据（票面第一、三、四节，逐条钉在 `tests/approvals.test.js`）：

     1. **倒计时语义**只从 `requested_at + 600s`（后端等待上界）推导；越过上界 ⇒ 呈现态 `expired`、
        **两个按钮整块不渲染**（不是禁用）；`requested_at` 不可解析 ⇒ `--:--`，不伪造。
     2. **动作只出在 `displayState === 'pending'` 的记录上**：终态只读。谁能审批这条规则不在前端
        ——判据就是后端载荷的 `state` 与 `decisions`。
     3. **只提交未决策的调用**（#215）：卡片只铺未决策的那批，提交的 `decisions` 也由 store 按同一
        判据生成（`approvalDecisions`），否则相反结论会被服务端按 409 整批拒掉。
     4. **在途 / 失败**：在途时两枚按钮 `disabled` 并出现「正在提交…」；失败**不改写**卡片（它仍是
        pending、按钮还在），错误原因由 store 交给这里铺在 `.approval-error`。

     参数摘要一律走 `lib/approvals.js` 的 `approvalInputSummary`：**敏感键掩码**（口令 / 令牌 /
     凭证 / cookie ⇒ `••••`）、对象最多三层、字符串与总长都有界——这是安全判据，不是排版偏好。 -->
<script setup>
import { computed } from 'vue';
import MemberAvatar from './MemberAvatar.vue';
import { t } from '../i18n/index.js';
import {
  approvalCardCalls,
  approvalCardLabel,
  approvalDisplayState,
  approvalInputSummary,
  approvalRemainingText,
  approvalStateLabel,
  approvalUrgentFlag,
} from '../lib/approvals.js';

const props = defineProps({
  // 一条 `approval_request` 载荷（`{ reply_id, target_id, member_id, tool_calls, state, decisions, requested_at, resolved_at }`）。
  record: { type: Object, required: true },
  // 这条记录归属的 target（卡片上的 `data-approval-target`，提交时也带它）。
  targetId: { type: String, required: true },
  // 发起审批的成员（成员表里那一行；`null` = 查不到，退回 id，不编名字）。
  member: { type: Object, default: null },
  // 时钟：由父组件给（与派生读数同一个基准，用例据此免 sleep）。
  now: { type: Number, default: undefined },
  // 离线时动作不可发（与旧 `approvalActionButton` 的 `!state.online` 同一条）。
  online: { type: Boolean, default: true },
  inFlight: { type: Boolean, default: false },
  error: { type: String, default: '' },
});

const emit = defineEmits(['submit']);

const displayState = computed(() => approvalDisplayState(props.record, props.now));
const calls = computed(() => approvalCardCalls(props.record, displayState.value));
const label = computed(() => approvalCardLabel(props.record, displayState.value));
const stateText = computed(() => approvalStateLabel(displayState.value));
const remainingText = computed(() => approvalRemainingText(props.record, displayState.value, props.now));
const urgent = computed(() => approvalUrgentFlag(props.record, displayState.value, props.now));
const memberName = computed(() => {
  if (props.member) return props.member.display_name;
  return String(props.record.member_id || '');
});
const disabled = computed(() => props.inFlight || !props.online);
const errorText = computed(() => (props.error ? t('approval.submitFailed', { reason: props.error }) : ''));

function submit(action) {
  if (disabled.value) return;
  emit('submit', { action, confirmed: action === 'allow' });
}
</script>

<template>
  <div
    class="approval-card"
    role="group"
    :data-approval-id="record.reply_id"
    :data-approval-target="targetId"
    :data-state="displayState"
    :aria-label="label"
  >
    <div class="approval-head">
      <span class="approval-tag">{{ t('approval.tag') }}</span>
      <MemberAvatar :name="memberName" :slot="member ? member.avatar_slot || 0 : 0" size="sm" />
      <span class="approval-member">{{ memberName }}</span>
      <span class="approval-state" :data-state="displayState">{{ stateText }}</span>
      <!-- 终态不再报「剩余」：倒计时只属于待审批（旧 `approvalRemainingText` 同一条）。 -->
      <span class="approval-remaining" :data-urgent="urgent">{{ remainingText }}</span>
    </div>

    <div v-if="calls.length" class="approval-tools">
      <div v-for="(call, index) in calls" :key="(call && call.id) || index" class="approval-tool">
        <div class="approval-tool-name">{{ (call && call.name) || t('approval.unknownTool') }}</div>
        <!-- 参数摘要：只读 `input`，敏感键掩码，长度有界。 -->
        <div class="approval-tool-input">{{ approvalInputSummary(call ? call.input : null) }}</div>
      </div>
    </div>

    <!-- 动作只出在 `displayState === 'pending'`：终态**整块不渲染**（不是禁用）。 -->
    <div v-if="displayState === 'pending'" class="approval-actions">
      <button
        class="btn btn-sm btn-primary"
        type="button"
        data-approval-action="allow"
        :data-approval-reply="record.reply_id"
        :data-approval-target="targetId"
        :disabled="disabled"
        @click="submit('allow')"
      >{{ t('approval.allow') }}</button>
      <button
        class="btn btn-sm"
        type="button"
        data-approval-action="deny"
        :data-approval-reply="record.reply_id"
        :data-approval-target="targetId"
        :disabled="disabled"
        @click="submit('deny')"
      >{{ t('approval.deny') }}</button>
    </div>
    <div v-if="displayState === 'pending' && inFlight" class="approval-note">{{ t('approval.submitting') }}</div>

    <div v-if="errorText" class="approval-error">{{ errorText }}</div>
  </div>
</template>
