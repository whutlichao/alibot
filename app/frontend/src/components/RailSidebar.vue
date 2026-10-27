<!-- 左栏（工作区导航）

     三段名单：频道 / 私信 / 本机 Agent。每段的行都是**后端事实的镜像**，段空时给空态。

     与旧实现的一处结构性差异：旧版为了「Agent 每回复一条消息左栏不闪」手写了一套
     `syncRailRows()` 就地复用节点（对比 id、只改真的变了的子节点、换序只动错位那一段）。
     Vue 的 `v-for` + `:key` 天然就是这件事——键是后端 id，集合不变时节点被复用，
     行内文本与 class 按依赖最小化更新，`.nav-row` 的过渡与 `.presence.working` 的动画
     因此不会重头开始（`rail_identity.test.cjs` 守的就是这条外部可观察行为）。

     状态点只读 `state.statuses` / agent.status（后端事实），不从会话存活推导。

     **这一版里 rail 上的入口分三类**（ADR-0038 §五：不放按下去什么都不发生的按钮）：
     1. 背后是**已迁面**的：频道 / 私信行的切换，提醒与设置两个弹窗（`.rail-head` 的两枚
        `.icon-btn`），正常工作；
     2. 背后是**未迁面的导航入口**（工作台搜索、Agent 详情）：整块**不渲染**——
        它们的价值就是把人带到另一处，带不到就不该出现；
     3. **空工作台的出路**（新建频道 / 新建 Agent）：保留可见但**禁用**，理由写在 `title` 里。
        它们不能一起消失：空库里唯一的引导文案就是「新建一个频道」，把按钮也藏掉等于让人无路可走。 -->
<script setup>
import { computed, ref } from 'vue';
import MemberAvatar from './MemberAvatar.vue';
import PresenceDot from './PresenceDot.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  state: { type: Object, required: true },
  // 抽屉形态（≤900px）：由父组件决定 `.is-open` 与遮罩。
  open: { type: Boolean, default: false },
  // 提醒圆点角标：**还有未到点的提醒**才亮（判据在 `stores/reminders.js` 的 `hasPending`，
  // 那里只消费后端的 `fired_at`）。已提醒 / 已错过不等待任何事，圆点不能退化成历史计数器。
  reminderPending: { type: Boolean, default: false },
});

const emit = defineEmits(['select-target', 'open-reminders', 'open-settings']);

/** 「已归档」段的展开态：会话内的呈现状态（不是业务正本，不写任何浏览器存储）。 */
const archiveOpen = ref(false);

/** 已归档私信：判据只有后端事实 `peer.deleted_at`（Agent 软删除后 DM 与历史保留、只读）。 */
function isArchived(dm) {
  return Boolean(dm && dm.peer && dm.peer.deleted_at);
}

const archivedDms = computed(() => props.state.dms.filter(isArchived));
const liveDms = computed(() => props.state.dms.filter((dm) => !isArchived(dm)));

/** 本机 Agent（全工作区名单，不按频道过滤——那是「频道成员」的事）。 */
const localAgents = computed(() => props.state.members.filter((member) => member.kind === 'agent'));

function statusOf(member) {
  const agent = props.state.agents.find((item) => item.id === member.id);
  if (agent && typeof agent.status === 'string') return agent.status;
  return props.state.statuses[member.id] || 'offline';
}

/** 当前行：Agent 详情开着时整份 rail 没有当前行（详情是主线视图，不是某个 target）。 */
function isCurrent(targetId) {
  return targetId === props.state.activeTargetId;
}

const me = computed(() => props.state.member || { display_name: 'owner', avatar_slot: 4, kind: 'owner' });
</script>

<template>
  <aside
    id="rail"
    class="rail"
    :class="{ 'is-open': open }"
    data-od-id="workspace-sidebar"
    :aria-label="t('rail.nav')"
  >
    <div class="rail-head">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
            <path d="M6 7h12M6 12h8M6 17h12" /><circle cx="18" cy="12" r="2.4" fill="currentColor" stroke="none" />
          </svg>
        </div>
        <div style="min-width: 0">
          <div class="brand-name">alibot</div>
          <div class="brand-sub">{{ t('app.brandSub') }}</div>
        </div>
      </div>

      <!-- rail 头的两个弹窗入口（旧页面 `studio/index.html:69` / `:83`）。
           圆点用 `v-if` 而不是旧页面的 `hidden`：它没有需要保留的内容（不像人填了一半的表单），
           于是「没有未到点的提醒」在 DOM 层也成立，首帧也不会凭空空出一个点。 -->
      <button
        class="icon-btn"
        type="button"
        data-od-id="btn-reminders"
        :aria-label="t('rail.reminders')"
        :title="t('rail.reminders')"
        @click="emit('open-reminders')"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <path d="M10.268 21a2 2 0 0 0 3.464 0" /><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" />
        </svg>
        <span v-if="reminderPending" class="dot" id="reminderDot"></span>
      </button>
      <!-- 设置入口（旧页面 `:83` 的齿轮几何：齿轮体与中心圆同心，`stroke-width` 与铃铛同为 1.9）。 -->
      <button
        class="icon-btn"
        type="button"
        data-od-id="btn-settings"
        :aria-label="t('settings.open')"
        :title="t('settings.open')"
        @click="emit('open-settings')"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" /><circle cx="12" cy="12" r="3" />
        </svg>
      </button>
    </div>

    <div class="rail-actions">
      <button
        class="btn btn-primary"
        type="button"
        disabled
        data-od-id="btn-new-channel"
        :title="t('app.notMigrated')"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <path d="M5 12h14" /><path d="M12 5v14" />
        </svg>
        <span>{{ t('rail.newChannel') }}</span>
      </button>
      <button
        class="btn"
        type="button"
        disabled
        data-od-id="btn-new-agent"
        :title="t('app.notMigrated')"
        :aria-label="t('rail.newAgentTitle')"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <path d="M5 12h14" /><path d="M12 5v14" />
        </svg>
        <span>{{ t('rail.newAgent') }}</span>
      </button>
    </div>

    <nav class="rail-scroll" data-od-id="nav-lists">
      <div class="group">
        <div class="group-label">{{ t('rail.groupChannels') }}</div>
        <div id="channelList">
          <button
            v-for="channel in state.channels"
            :key="channel.id"
            class="nav-row"
            :class="{ 'is-active': isCurrent(channel.id) }"
            :data-channel="channel.id"
            :data-od-id="'channel-' + channel.id"
            :aria-current="isCurrent(channel.id) ? 'true' : 'false'"
            @click="emit('select-target', channel.id)"
          >
            <span class="hash">#</span>
            <span class="grow">{{ channel.name }}</span>
            <span
              v-if="channel.unread_count > 0"
              class="badge"
              :data-od-id="'unread-' + channel.id"
            >{{ channel.unread_count }}</span>
          </button>
        </div>
        <div v-if="!state.channels.length" class="rail-empty">{{ t('rail.emptyChannels') }}</div>
      </div>

      <div class="group" data-od-id="dm-section">
        <div class="group-label">{{ t('rail.groupDms') }}</div>
        <div id="dmList">
          <button
            v-for="dm in liveDms"
            :key="dm.id"
            class="nav-row"
            type="button"
            :class="{ 'is-active': isCurrent(dm.id) }"
            :data-dm="dm.id"
            :data-od-id="'dm-' + dm.id"
            :aria-current="isCurrent(dm.id) ? 'true' : 'false'"
            @click="emit('select-target', dm.id)"
          >
            <MemberAvatar :name="dm.peer.display_name" :slot="dm.peer.avatar_slot" size="sm" />
            <span class="grow">{{ dm.peer.display_name }}</span>
            <span v-if="dm.unread_count > 0" class="badge" :data-od-id="'unread-' + dm.id">{{ dm.unread_count }}</span>
          </button>
        </div>
        <div v-if="!liveDms.length" class="rail-empty">{{ t('rail.emptyDms') }}</div>
      </div>

      <!-- 已归档私信：Agent 软删除后 DM target / 历史 / 未读都保留，人类面单独成段且只读。
           标题是原生按钮、**默认折叠**，展开态是会话内的呈现状态（不写任何浏览器存储）。 -->
      <div v-if="archivedDms.length" class="group" data-od-id="dm-archive-section">
        <button
          class="group-label is-toggle"
          type="button"
          data-od-id="btn-dm-archive-toggle"
          :aria-expanded="archiveOpen ? 'true' : 'false'"
          aria-controls="dmArchiveList"
          @click="archiveOpen = !archiveOpen"
        >
          <span>{{ t('rail.groupArchivedDms') }}</span>
          <svg class="group-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>
        <div v-show="archiveOpen" id="dmArchiveList">
          <button
            v-for="dm in archivedDms"
            :key="dm.id"
            class="nav-row"
            type="button"
            :class="{ 'is-active': isCurrent(dm.id) }"
            :data-dm="dm.id"
            :data-od-id="'dm-' + dm.id"
            @click="emit('select-target', dm.id)"
          >
            <MemberAvatar :name="dm.peer.display_name" :slot="dm.peer.avatar_slot" size="sm" />
            <span class="grow">{{ dm.peer.display_name }}</span>
            <span v-if="dm.unread_count > 0" class="badge">{{ dm.unread_count }}</span>
          </button>
        </div>
      </div>

      <div class="group">
        <div class="group-label">{{ t('rail.groupAgents') }}</div>
        <div id="memberList">
          <!-- Agent 详情是主线视图，还没迁到本版 ⇒ 这一行只是**读数**（名字 + 状态点），
               不是一个点下去什么都不发生的按钮。它随详情那张票变回可点。 -->
          <div
            v-for="member in localAgents"
            :key="member.id"
            class="nav-row is-static"
            :data-member="member.id"
            :data-od-id="'agent-' + member.id"
          >
            <MemberAvatar :name="member.display_name" :slot="member.avatar_slot" size="sm" />
            <span class="grow">{{ member.display_name }}</span>
            <PresenceDot :status="statusOf(member)" />
          </div>
        </div>
        <div v-if="!localAgents.length" class="rail-empty">{{ t('rail.emptyAgents') }}</div>
      </div>
    </nav>

    <div class="rail-foot">
      <MemberAvatar :name="me.display_name" :slot="me.avatar_slot" />
      <div style="flex: 1; min-width: 0">
        <div
          style="font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
        >{{ me.display_name }}</div>
        <div class="brand-sub" style="letter-spacing: 0">{{ me.kind === 'owner' ? t('identity.human') : me.kind }}</div>
      </div>
      <PresenceDot status="online" />
    </div>
  </aside>
</template>
