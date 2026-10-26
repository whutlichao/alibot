<!-- 工作台外壳（三栏：rail 252 / main 880 / dock 380）

     本票（Vue 重构第一阶段）迁的是**主链路**：壳、左栏、频道头、消息流、composer、SSE、标读。
     其余面（任务看板 / 任务线程 / 运行面板 / 审批卡 / Agent 详情 / 搜索 / 各类弹窗）仍由旧页面
     `/studio/` 承载——`/studio/v2/` 只承诺已迁完的这一条链路可独立使用，不假装它有全部功能
     （见 ADR-0038 §五：迁移期间两条入口并存，逐个面切换，不做一次性大爆炸）。

     布局与类名沿用设计系统的既有契约（`alibot-design-system/ui_kits/app/app.css` 与
     `studio/app.css` 是同一套类名的两个落点）：三栏尺寸、方形头像块、未读徽标色族、
     四态状态点都按名字消费，因此视觉上两版页面是同一个产品，不是一个新皮肤。 -->
<script setup>
import { computed, onMounted, ref } from 'vue';
import ChannelHeader from './components/ChannelHeader.vue';
import ComposerBox from './components/ComposerBox.vue';
import DockPanel from './components/DockPanel.vue';
import MessageStream from './components/MessageStream.vue';
import RailSidebar from './components/RailSidebar.vue';
import { createAppContext, useToasts } from './app-context.js';
import { t } from './i18n/index.js';
import { composerPlaceholder, isArchivedDm, messageTabCount } from './lib/target-view.js';
import { useUiState } from './stores/ui-state.js';

const props = defineProps({
  // 测试替身入口：事件源的构造器可以注入（生产环境一律用浏览器原生的 `EventSource`）。
  // `fetch` 不在这里注入——用例直接换 `globalThis.fetch`，与 `app-context.test.js` 同一条路。
  sourceFactory: { type: Function, default: null },
});

const toasts = useToasts();
const ctx = createAppContext({ toast: toasts.push, sourceFactory: props.sourceFactory || undefined });
const { state, store } = ctx;

const stream = ref(null);
const railOpen = ref(false);
// 右侧 dock 在 ≤900px 是抽屉：`.is-open` 与 `hidden` 同进同出（两者不同步就会出现
// 「看起来收起了、Tab 还能命中」）。桌面它是常驻列，这一位不生效。
const dockOpen = ref(false);
const activeTab = ref('messages');
// 折叠态（摘要行 / 旁白行）按 **seq** 认，跨 target 不通用：切 target 时必须整体清掉，
// 否则「在 A 频道展开过的第 3 条」到了 B 频道会莫名展开。这个状态住在模块级（见 ui-state.js），
// 因此清理的落点只有这一处。
const ui = useUiState();

const target = computed(() => store.activeTarget.value);
const isDm = computed(() => store.isDm.value);
const archived = computed(() => store.isArchivedDm.value);
const placeholder = computed(() => composerPlaceholder(target.value));
const messages = computed(() => store.renderedMessages());

/** 运行面板的成员名来源：工作区成员 → Agent → 当前频道成员（找不到就用 id，不编名字）。 */
const dockPools = computed(() => [
  state.members,
  state.agents,
  state.channelMembers[state.activeTargetId] || [],
]);

/** 运行面板的标题只拿频道名单：任务面板的入口随任务线程那张票进来。 */
const dockPanel = ctx.runPanel;

/** 候选来源：`null` = 还没读到（去读一次），空数组才是真的没有 Agent 成员。 */
const mentionCandidates = computed(() => store.channelAgentMembers(state.activeTargetId));

async function selectTarget(targetId) {
  if (targetId === state.activeTargetId) return;
  // 先清旧 target 的折叠态，再换 target：顺序反了就会清掉新 target 刚铺上的状态。
  ui.resetForTarget();
  // 运行面板读的是**上一个 target** 的快照与 live 归属：换 target 时整个作废，
  // 不把旧频道的分派留在新频道旁边（旧实现同一条：`resetRunPanelState()`）。
  dockPanel.resetForTarget(true);
  dockOpen.value = false;
  store.setActiveTargetId(targetId);
  railOpen.value = false;
  activeTab.value = 'messages';
  try {
    await store.refreshMessages();
    await store.refreshOutcomes();
    if (!isDm.value) await store.refreshChannelMembers();
    store.mutable.online = true;
  } catch {
    store.mutable.online = false;
  }
  store.markCurrentRead();
  if (stream.value) stream.value.scrollToBottom();
}

async function send(text) {
  const result = await store.sendMessage(text, []);
  if (!result.ok) {
    if (result.offline) toasts.push(t('toast.sendBlockedOffline'), 'err');
    else if (result.error) toasts.push(t('toast.sendRejected', { reason: result.error.message }), 'err');
    return;
  }
  if (stream.value) stream.value.scrollToBottom();
}

async function ensureMentionCandidates() {
  if (mentionCandidates.value !== null) return;
  await store.refreshChannelMembers();
}

onMounted(async () => {
  const { firstSuccessfulLoad } = await ctx.start();
  // 只有**首次成功载入**才贴底：断线重连与静默刷新不得把正在回看历史的人拉回底部
  // （这条判据的正本在 `store.loadWorkspace()` 的返回值里，不在这里另判一次）。
  if (firstSuccessfulLoad && stream.value) stream.value.scrollToBottom();
});

function retryPending(clientId) {
  store.retryPending(clientId);
}

function discardPending(clientId) {
  store.discardPending(clientId);
}

function unsupportedMention(name) {
  toasts.push(t('mention.quoteUnsupported', { name }), 'err');
}

/** 频道头的运行面板入口：打开 dock 的第三种形态（读完快照前先铺加载态）。 */
async function openRunPanel() {
  dockOpen.value = true;
  await dockPanel.openForChannel();
}

/** 关闭运行面板：把形态整个作废（下次打开是干净的一帧），并收起移动端抽屉。 */
function closeRunPanel() {
  dockPanel.close();
  dockOpen.value = false;
}

// 提醒 / 设置 / Agent 详情 / 名册 / 配置 / 预算都还没迁到本版：
// 对应的入口由各组件自己的「是否可用」开关关着（不渲染），而不是渲染一个只弹 toast 的假按钮。
</script>

<template>
  <div class="app" id="shell" data-od-id="app-shell">
    <!-- 移动端顶栏（≤900px）：只有「打开导航」一个控件；标题是当前 target 的名字。 -->
    <div class="mobile-topbar" data-od-id="mobile-topbar">
      <button
        class="icon-btn"
        type="button"
        data-od-id="rail-toggle"
        aria-controls="rail"
        :aria-expanded="railOpen ? 'true' : 'false'"
        :aria-label="t('mobile.railToggle')"
        :title="t('mobile.railToggle')"
        @click="railOpen = !railOpen"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 5h16" /><path d="M4 12h16" /><path d="M4 19h16" />
        </svg>
      </button>
      <span class="grow" id="mtTitle">{{ target ? (isDm ? '@' : '#') + (isDm ? target.peer.display_name : target.name) : 'alibot' }}</span>
    </div>
    <div v-if="railOpen" class="rail-scrim" data-od-id="rail-scrim" aria-hidden="true" @click="railOpen = false"></div>

    <RailSidebar
      :state="state"
      :open="railOpen"
      @select-target="selectTarget"
    />

    <main class="main" id="mainColumn" data-od-id="main-column">
      <div v-if="!state.online" class="conn-banner" id="connBanner" role="alert">
        <span>{{ t('conn.banner') }}</span>
        <button class="btn btn-sm" data-od-id="btn-reconnect" @click="ctx.start()">{{ t('conn.retry') }}</button>
      </div>
      <div v-if="state.online && state.streamState === 'reconnecting'" class="conn-banner" id="streamState" role="status">
        {{ t('conn.reconnecting') }}
      </div>

      <ChannelHeader
        v-if="target"
        :target="target"
        :is-dm="isDm"
        :is-archived-dm="archived"
        :message-count="messageTabCount(target)"
        :task-count="0"
        :active-tab="activeTab"
        @select-tab="activeTab = $event"
        @open-run-panel="openRunPanel"
      />

      <MessageStream
        v-if="activeTab === 'messages'"
        ref="stream"
        :state="state"
        :messages="messages"
        :typing-names="ctx.typingNames.value"
        @retry="retryPending"
        @discard="discardPending"
      />

      <ComposerBox
        v-if="activeTab === 'messages'"
        :placeholder="placeholder"
        :disabled="archived"
        :online="state.online"
        :archived="archived"
        :candidates="mentionCandidates"
        :candidates-unavailable="state.channelMembersFailed.includes(state.activeTargetId)"
        @send="send"
        @mention-request="ensureMentionCandidates"
        @mention-unsupported="unsupportedMention"
      />
    </main>

    <DockPanel
      :panel="dockPanel"
      :pools="dockPools"
      :open="dockOpen"
      @close="closeRunPanel"
    />

    <div class="toast-wrap" data-od-id="toast-wrap">
      <div v-for="item in toasts.items" :key="item.id" class="toast" :class="{ err: item.err }">
        <span class="dot"></span>{{ item.message }}
      </div>
    </div>
  </div>
</template>
