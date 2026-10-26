<!-- 运行面板（dock 的第三种形态）：成员过程 · 分派 · Plan · SOP 的只读过程面

     它是**独立面**（ADR-0020 / ADR-0023）：`agent_event` / `team_assignment` / `plan_update` /
     `sop_event` 只在这里呈现，绝不进消息流 / 未读 / 搜索 / 看板。两种数据来源严格分层：
     快照兜底（打开 / 刷新 / 重连）＋ live 帧（本会话内存、断线不补），按后端 id 合并。

     合并判据全部在 `lib/run-panel.js`（纯函数，可逐条钉用例），本组件只负责**铺**：
     类名与 `data-od-id` 逐字沿用旧页面，于是旧用例族、自动化取证与两版页面的视觉都是同一套。

     三段「为什么是空的」分开说（旧实现同一条）：
     - 运行与 Plan 的空态要分自主模式频道（没有队长 ⇒ 不产生 team 运行与 Plan），
       而成员事件 / SOP 的空态维持原语义——那两段与协作模式无关，多说一句就是误导；
     - 配置读不到时**不**把「读不到」误报成自主模式（`autonomous` 只读已读回的缓存）。 -->
<script setup>
import { computed, ref } from 'vue';
import MemberAvatar from './MemberAvatar.vue';
import { t } from '../i18n/index.js';
import { timeLabel } from '../lib/format.js';
import {
  assignmentRows,
  durationLabel,
  eventSummary,
  groupAssignmentsByMember,
  groupEventsByMember,
  memberNameOf,
  memberResolver,
  memberSlotOf,
  planGroups,
  sopViews,
  stateLabel,
} from '../lib/run-panel.js';

const props = defineProps({
  // 运行面板 store（`createRunPanelStore()` 的返回值）。
  panel: { type: Object, required: true },
  // 成员名的三个来源池（工作区成员 / Agent / 频道成员），按顺序找。
  pools: { type: Array, default: () => [] },
});

const emit = defineEmits(['close']);

const body = ref(null);
const panelState = computed(() => props.panel.state);
const resolveMember = computed(() => memberResolver(props.pools));

function nameOf(memberId) {
  return memberNameOf(resolveMember.value, memberId);
}

function slotOf(memberId) {
  return memberSlotOf(resolveMember.value, memberId);
}

/** 运行卡片：快照分派 + live 分派按 `assignment_id` 合并后按成员分组。 */
const runCards = computed(() =>
  panelState.value.runs.map((run) => ({
    run,
    leaderName: nameOf(run.leader_member_id),
    leaderSlot: slotOf(run.leader_member_id),
    groups: groupAssignmentsByMember(assignmentRows(run, props.panel.liveEvents.value)),
  })),
);

/** 成员事件段：按「成员 → 回复」两级分组，每回复只留尾部若干条。 */
const eventGroups = computed(() => groupEventsByMember(props.panel.liveEvents.value));

/** Plan 段：快照最近 run 的清单 + live `plan_update` 覆盖（`deleted_at` 的行已移除）。 */
const planCards = computed(() =>
  planGroups(panelState.value.plan, props.panel.overlay.value).filter((group) => group.tasks.length),
);

/** SOP 段：快照 run 视图 + live `sop_event` 覆盖（终态不被迟到的 live 帧拉回）。 */
const sopCards = computed(() => sopViews(panelState.value.sops, panelState.value.sopDefs, props.panel.overlay.value));

/** 首屏加载态：只在**什么都没有**时铺「正在读取」，刷新时不把人已经读到的东西换掉。 */
const firstLoading = computed(
  () =>
    panelState.value.loading
    && !panelState.value.runs.length
    && !panelState.value.plan.length
    && !panelState.value.sops.length,
);

/** 自主模式的解释只用于运行与 Plan 两段。 */
const autonomous = computed(() => props.panel.autonomous.value);

async function refresh() {
  await props.panel.loadSnapshot();
  const node = body.value;
  // 刷新是显式动作：把读数带回顶部，否则「点了刷新但看不出变化」。
  if (node) node.scrollTop = 0;
}
</script>

<template>
  <div class="run-panel" id="dockRun" data-od-id="run-panel">
    <div class="dock-head" data-od-id="run-panel-head">
      <div class="dock-id">
        <div class="meta">
          <div class="dock-name">
            <span class="dock-tag">{{ t('runPanel.tag') }}</span> <span class="dock-task-id" id="rpTitle">{{ panel.title.value }}</span>
          </div>
          <div class="dock-role"><span id="rpSub">{{ t('runPanel.sub') }}</span></div>
        </div>
        <button
          class="icon-btn"
          type="button"
          id="rpRefresh"
          data-od-id="run-panel-refresh"
          :title="t('runPanel.refresh')"
          :aria-label="t('runPanel.refresh')"
          :disabled="panelState.loading"
          @click="refresh"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" />
          </svg>
        </button>
        <button
          class="icon-btn"
          type="button"
          id="rpClose"
          data-od-id="run-panel-close"
          :aria-label="t('runPanel.closeAria')"
          @click="emit('close')"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 6 6 18" /><path d="m6 6 12 12" />
          </svg>
        </button>
      </div>
    </div>

    <div class="dock-scroll" id="rpBody" data-od-id="run-panel-body" ref="body">
      <!-- 错误态整块换掉正文：读到一半的快照不铺（半份读数比没有读数更难判断）。 -->
      <div v-if="panelState.error" class="rp-error" data-od-id="rp-error">
        <div class="rp-error-text">{{ t('runPanel.error', { reason: panelState.error }) }}</div>
        <button class="btn btn-sm" type="button" data-run-retry="1" data-od-id="rp-retry" @click="refresh">
          {{ t('runPanel.retry') }}
        </button>
      </div>

      <div v-else-if="firstLoading" class="tt-empty">{{ t('runPanel.loading') }}</div>

      <template v-else>
        <!-- ─── 运行与分派 ─────────────────────────────────────────────── -->
        <div class="d-sec" data-od-id="rp-runs">
          <div class="d-sec-title">{{ t('runPanel.sectionRuns') }}</div>
          <div v-if="!runCards.length" class="tt-empty">
            {{ t(autonomous ? 'runPanel.runsEmptyAutonomous' : 'runPanel.runsEmpty') }}
          </div>
          <div
            v-for="card in runCards"
            :key="card.run.id"
            class="rp-run"
            :data-od-id="'rp-run-' + card.run.id"
            :data-run="card.run.id"
          >
            <div class="rp-run-head">
              <MemberAvatar :name="card.leaderName" :slot="card.leaderSlot" size="sm" />
              <span class="rp-run-title">{{ card.leaderName }}</span>
              <span class="rp-state" :data-state="String(card.run.status || '')">{{ stateLabel(card.run.status) }}</span>
              <span v-if="card.run.started_at" class="rp-time">{{ timeLabel(card.run.started_at) }}</span>
              <span class="rp-id">#{{ String(card.run.id).slice(-6) }}</span>
            </div>
            <div v-if="!card.groups.length" class="tt-note">{{ t('runPanel.noAssignments') }}</div>
            <div
              v-for="group in card.groups"
              :key="group.memberId"
              class="rp-member"
              :data-od-id="'rp-run-' + card.run.id + '-member-' + group.memberId"
            >
              <div class="rp-member-head">
                <MemberAvatar :name="nameOf(group.memberId)" :slot="slotOf(group.memberId)" size="sm" />
                <span class="rp-member-name">{{ nameOf(group.memberId) }}</span>
                <span class="rp-count">{{ group.items.length }}</span>
              </div>
              <div
                v-for="item in group.items"
                :key="item.id"
                class="rp-assign"
                :data-assignment="String(item.id || '')"
                :data-state="String(item.state || '')"
              >
                <div class="rp-assign-top">
                  <span class="rp-state" :data-state="String(item.state || '')">{{ stateLabel(item.state) }}</span>
                  <span v-if="durationLabel(item)" class="rp-duration">{{ durationLabel(item) }}</span>
                </div>
                <div v-if="item.task_id" class="rp-meta">
                  {{ t('runPanel.taskRef', { id: String(item.task_id).slice(-6) }) }}
                </div>
                <div v-if="item.reply_excerpt" class="rp-excerpt">{{ item.reply_excerpt }}</div>
                <div v-if="item.error" class="rp-error-line">{{ item.error }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- ─── 成员事件（live-only） ───────────────────────────────────── -->
        <div class="d-sec" data-od-id="rp-events">
          <div class="d-sec-title">{{ t('runPanel.sectionEvents') }}</div>
          <div v-if="!eventGroups.length" class="tt-empty">{{ t('runPanel.eventsEmpty') }}</div>
          <div
            v-for="group in eventGroups"
            :key="group.memberId"
            class="rp-member"
            :data-od-id="'rp-events-member-' + group.memberId"
            :data-member="group.memberId"
          >
            <div class="rp-member-head">
              <MemberAvatar :name="nameOf(group.memberId)" :slot="slotOf(group.memberId)" size="sm" />
              <span class="rp-member-name">{{ nameOf(group.memberId) }}</span>
              <!-- 计数进 mono 档：`×N` 是回复条数，不写成一句话（旧实现同一条）。 -->
              <span class="rp-count">{{ group.replyCount ? '×' + group.replyCount : '' }}</span>
            </div>
            <div v-for="reply in group.replies" :key="reply.replyId" class="rp-reply" :data-reply="reply.replyId">
              <div class="rp-reply-id">{{ t('runPanel.replyRef', { id: reply.replyId.slice(-6) }) }}</div>
              <div
                v-for="(frame, index) in reply.frames"
                :key="reply.replyId + '-' + index"
                class="rp-event"
                :data-event-type="String(frame.event_type || '')"
              >
                <span class="rp-event-type">{{ frame.event_type }}</span>
                <span v-if="eventSummary(frame)" class="rp-event-summary">{{ eventSummary(frame) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- ─── Plan ────────────────────────────────────────────────────── -->
        <div class="d-sec" data-od-id="rp-plan">
          <div class="d-sec-title">{{ t('runPanel.sectionPlan') }}</div>
          <div v-if="!planCards.length" class="tt-empty">
            {{ t(autonomous ? 'runPanel.planEmptyAutonomous' : 'runPanel.planEmpty') }}
          </div>
          <div
            v-for="group in planCards"
            :key="group.memberId"
            class="rp-member rp-plan-group"
            :data-od-id="'rp-plan-member-' + group.memberId"
            :data-member="group.memberId"
          >
            <div class="rp-member-head">
              <MemberAvatar :name="nameOf(group.memberId)" :slot="slotOf(group.memberId)" size="sm" />
              <span class="rp-member-name">{{ nameOf(group.memberId) }}</span>
              <span class="rp-count">{{ group.tasks.length }}</span>
            </div>
            <div
              v-for="task in group.tasks"
              :key="task.id"
              class="rp-plan-task"
              :data-plan-task="String(task.id || '')"
              :data-state="String(task.state || '')"
            >
              <span class="rp-plan-subject">{{ task.subject || task.description || task.id }}</span>
              <span class="rp-state" :data-state="String(task.state || '')">{{ stateLabel(task.state) }}</span>
            </div>
          </div>
        </div>

        <!-- ─── SOP ─────────────────────────────────────────────────────── -->
        <div class="d-sec" data-od-id="rp-sop">
          <div class="d-sec-title">{{ t('runPanel.sectionSop') }}</div>
          <div v-if="!sopCards.length" class="tt-empty">{{ t('runPanel.sopEmpty') }}</div>
          <div
            v-for="run in sopCards"
            :key="run.id"
            class="rp-run rp-sop"
            :data-od-id="'rp-sop-' + run.id"
            :data-sop-run="run.id"
          >
            <div class="rp-run-head">
              <span class="rp-run-title">#{{ run.id.slice(-6) }}</span>
              <span class="rp-state" :data-state="String(run.phase || '')">{{ stateLabel(run.phase) }}</span>
              <span v-if="run.startedAt" class="rp-time">{{ timeLabel(run.startedAt) }}</span>
            </div>
            <div v-if="run.awaiting && run.awaiting.reply_id" class="team-notice">
              {{ t('runPanel.awaiting', { reply: String(run.awaiting.reply_id).slice(-6) }) }}
            </div>
            <div v-if="!run.steps.length" class="tt-note">{{ t('runPanel.noSteps') }}</div>
            <div
              v-for="step in run.steps"
              :key="step.index"
              class="rp-step"
              :data-step="String(step.index)"
              :data-state="String(step.phase || '')"
            >
              <div class="rp-step-top">
                <span class="rp-step-name">{{ step.subject }}</span>
                <span class="rp-state" :data-state="String(step.phase || '')">{{ stateLabel(step.phase) }}</span>
              </div>
              <div
                v-if="step.verdict"
                class="rp-verdict"
                :class="step.verdict.passed ? 'is-pass' : 'is-fail'"
                :data-passed="step.verdict.passed ? 'true' : 'false'"
              >
                <span class="rp-verdict-state">
                  {{ step.verdict.passed ? t('runPanel.verdictPassed') : t('runPanel.verdictFailed') }}
                </span>
                <span v-if="step.verdict.message" class="rp-verdict-message">{{ step.verdict.message }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
