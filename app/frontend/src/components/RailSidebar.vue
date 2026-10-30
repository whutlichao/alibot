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
        `.icon-btn`），工作台搜索（搜索视图，票 #304），新建频道 / 新建 Agent 两张表单，
        Agent 行的「打开私信」，正常工作；
     2. 背后是**未迁面的导航入口**（Agent 详情）：整块**不渲染**——
        它们的价值就是把人带到另一处，带不到就不该出现；
     3. **空工作台的出路**（新建频道 / 新建 Agent）：#301 已把两张表单迁过来，因此两个按钮
        **可用**，点击打开 `ChannelFormModal` / `AgentFormModal`（外壳是 #300 的 `ModalShell`）。
        Agent 行同理：`agentDetailAvailable` 开着 ⇒ 打开详情（归 T10）；关着 ⇒ **打开与它的私信**。
        这一行永远不是「按下去什么都不发生」的死行。 -->
<script setup>
import { computed, ref } from 'vue';
import MemberAvatar from './MemberAvatar.vue';
import PresenceDot from './PresenceDot.vue';
import { t } from '../i18n/index.js';
import { agentMembers, memberStatus } from '../lib/member-view.js';
import { isArchivedChannel } from '../lib/target-view.js';

const props = defineProps({
  state: { type: Object, required: true },
  // 抽屉形态（≤900px）：由父组件决定 `.is-open` 与遮罩。
  open: { type: Boolean, default: false },
  // 提醒圆点角标：**还有未到点的提醒**才亮（判据在 `stores/reminders.js` 的 `hasPending`，
  // 那里只消费后端的 `fired_at`）。已提醒 / 已错过不等待任何事，圆点不能退化成历史计数器。
  reminderPending: { type: Boolean, default: false },
  // Agent 详情面是否已迁到本版：T10 的那个开关。
  // **关着（默认）⇒ 点 Agent 行打开与它的私信**；开着 ⇒ 打开详情。T10 只翻这个开关，不改本文件里
  // 那一段的点击语义（票 #301 与 #310 的仲裁规则）。
  agentDetailAvailable: { type: Boolean, default: false },
  // 当前打开的 Agent 详情（`null` ⇒ 主线不在详情档）。它是 rail **当前行**的判据：
  // 详情不是某个 target 的内容，因此它开着时频道 / 私信行一律不是当前行，详情对应的 Agent 行是。
  // 由父组件按 `mode` 与详情 store 的 `memberId` 推导（见 `App.vue` 的 `activeAgentId`），
  // 本文件不另存一份「谁被选中」——那必然出现两份状态各自漂移。
  activeAgentId: { type: String, default: null },
});

const emit = defineEmits([
  'select-target',
  'open-reminders',
  'open-settings',
  'open-search',
  'new-channel',
  'new-agent',
  'open-agent',
  'open-dm',
]);

/** 「已归档」段的展开态：会话内的呈现状态（不是业务正本，不写任何浏览器存储）。
    频道段与私信段各用一枚：两段是两种 target，一个开关同时管两段会让「我只想展开频道」变得不可能。 */
const archiveOpen = ref(false);
// 频道的展开态是**三态**：`null` = 跟随「当前行在不在这段里」（人的手点会把它钉成 true / false）。
// 自动那一档是必需的（#342 review 抓到）：人在 #甲 里点「归档」，#甲 立刻进这一段；若段仍是折叠的，
// 当前打开着的频道会从左栏「消失」——界面上看不出自己归档的是哪一条。
// 私信段没有这个问题：DM 的归档由删 Agent 引发，人不可能「在私信里把当前这条归档」。
const channelArchiveToggle = ref(null);
const channelArchiveVisible = computed(() => {
  if (channelArchiveToggle.value !== null) return channelArchiveToggle.value;
  return archivedChannels.value.some((channel) => isCurrent(channel.id));
});

function toggleChannelArchive() {
  channelArchiveToggle.value = !channelArchiveVisible.value;
}

/** 已归档私信：判据只有后端事实 `peer.deleted_at`（Agent 软删除后 DM 与历史保留、只读）。 */
function isArchived(dm) {
  return Boolean(dm && dm.peer && dm.peer.deleted_at);
}

const archivedDms = computed(() => props.state.dms.filter(isArchived));
const liveDms = computed(() => props.state.dms.filter((dm) => !isArchived(dm)));

/** 频道的两段（#342 / ADR-0040）：判据是 `channel.archived_at`，与 store 的 `archivedChannels`
    共用**同一个函数**（`lib/target-view.js` 的 `isArchivedChannel`），于是「谁算已归档」不会在
    本仓出现第二份。本组件是**叶子呈现**：它按 `state.channels` 里的字段分段，不另存一份
    「谁被归档了」。 */
const archivedChannels = computed(() => props.state.channels.filter(isArchivedChannel));
const liveChannels = computed(() => props.state.channels.filter((channel) => !isArchivedChannel(channel)));

/** 本机 Agent（全工作区名单，不按频道过滤——那是「频道成员」的事）。判据与名册共用一处
    （`lib/member-view.js`）：两份 `kind === 'agent'` 的过滤迟早会漂。 */
const localAgents = computed(() => agentMembers(props.state.members));

function statusOf(member) {
  return memberStatus(props.state, member);
}

/** 消息流档的当前行：详情开着时整份 rail 没有 target 是当前行
    （详情是主线视图，不是某个 target 的内容；那一刻的当前行是下面那条 Agent 行）。 */
function isCurrent(targetId) {
  return !props.activeAgentId && targetId === props.state.activeTargetId;
}

/** 详情档的当前行：详情打开的那个成员。与 `isCurrent()` 互斥，因此同一时刻只有一行亮。 */
function isCurrentAgent(memberId) {
  return Boolean(props.activeAgentId) && memberId === props.activeAgentId;
}

/** Agent 行的无障碍名随点击语义换：开详情说「查看详情」，开私信说「打开私信」。 */
function agentRowAria(member) {
  return props.agentDetailAvailable
    ? t('agentDetail.openAria', { name: member.display_name })
    : t('rail.openDmAria', { name: member.display_name });
}

function openAgentRow(member) {
  if (props.agentDetailAvailable) emit('open-agent', member.id);
  else emit('open-dm', member.id);
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

    <!-- 工作台搜索入口（旧 `studio/index.html:93-99` 的 `.search-shell` / `.search-btn`）。
         它是**按钮**不是输入框：真正落笔的地方是主线搜索视图里的 `#searchInput`（票 #304）。
         旧页面明确**不印** `⌘K` 徽标（人定 2026-10-17）：`⌘K` 真绑定的是打开命令面板，
         印在搜索按钮上会让人以为它能开搜索——界面不承诺一个按键做不到的事。 -->
    <div class="search-shell">
      <button
        class="search-btn"
        type="button"
        data-od-id="btn-workspace-search"
        :title="t('rail.searchTitle')"
        @click="emit('open-search')"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="m21 21-4.34-4.34" /><circle cx="11" cy="11" r="8" />
        </svg>
        <span>{{ t('rail.searchPlaceholder') }}</span>
      </button>
    </div>

    <div class="rail-actions">
      <button
        class="btn btn-primary"
        type="button"
        data-od-id="btn-new-channel"
        @click="emit('new-channel')"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <path d="M5 12h14" /><path d="M12 5v14" />
        </svg>
        <span>{{ t('rail.newChannel') }}</span>
      </button>
      <button
        class="btn"
        type="button"
        data-od-id="btn-new-agent"
        :title="t('rail.newAgentTitle')"
        :aria-label="t('rail.newAgentTitle')"
        @click="emit('new-agent')"
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
            v-for="channel in liveChannels"
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
        <!-- 空态判据是「一条频道都没有」，不是「活跃段为空」：全部归档时该出现的是下面那一段，
             而不是一句「还没有频道」——那会把「有、但都归档了」说成「没有」。 -->
        <div v-if="!state.channels.length" class="rail-empty">{{ t('rail.emptyChannels') }}</div>
      </div>

      <!-- 已归档频道（#342 / ADR-0040）：归档只收走写入面，历史仍可读、仍可被搜索，人类面因此
           单独成段（形态与下面的 `dm-archive-section` 一致：原生按钮标题、默认折叠）。 -->
      <div v-if="archivedChannels.length" class="group" data-od-id="channel-archive-section">
        <button
          class="group-label is-toggle"
          type="button"
          data-od-id="btn-channel-archive-toggle"
          :aria-expanded="channelArchiveVisible ? 'true' : 'false'"
          aria-controls="channelArchiveList"
          @click="toggleChannelArchive"
        >
          <span>{{ t('rail.groupArchivedChannels') }}</span>
          <svg class="group-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>
        <div v-show="channelArchiveVisible" id="channelArchiveList" data-od-id="channelArchiveList">
          <button
            v-for="channel in archivedChannels"
            :key="channel.id"
            class="nav-row"
            type="button"
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
          <!-- Agent 行的点击语义**由一个开关裁决**（票 #301 与 T10 的仲裁规则）：
               `agentDetailAvailable` 开 ⇒ 进详情（那面归 T10）；关（默认）⇒ 打开与它的私信。
               两种都不是死行——旧页面同一件事的入口就是「选中一个成员 = 表达和谁私信」
               （`app.js:5184-5215`，幂等的 `POST /api/dms`）。
               类名沿用旧页面 rail 的 Agent 行（`button.nav-row`）；`.nav-row.is-static`
               那条只读样式由 #296 加在 `app.css` 里，本票不删它（旧页面与历史都不受影响）。 -->
          <button
            v-for="member in localAgents"
            :key="member.id"
            class="nav-row"
            type="button"
            :class="{ 'is-active': isCurrentAgent(member.id) }"
            :data-member="member.id"
            :data-od-id="'agent-' + member.id"
            :aria-label="agentRowAria(member)"
            :aria-current="isCurrentAgent(member.id) ? 'true' : 'false'"
            @click="openAgentRow(member)"
          >
            <MemberAvatar :name="member.display_name" :slot="member.avatar_slot" size="sm" />
            <span class="grow">{{ member.display_name }}</span>
            <PresenceDot :status="statusOf(member)" />
          </button>
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
