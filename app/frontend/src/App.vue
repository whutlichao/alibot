<!-- 工作台外壳（三栏：rail 252 / main 880 / dock 380）

     本票（Vue 重构第一阶段）迁的是**主链路**：壳、左栏、频道头、消息流、composer、SSE、标读。
     其余面（任务看板 / 任务线程 / 运行面板 / 审批卡 / Agent 详情 / 搜索 / 各类弹窗）仍由旧页面
     `/studio/` 承载——`/studio/v2/` 只承诺已迁完的这一条链路可独立使用，不假装它有全部功能
     （见 ADR-0038 §五：迁移期间两条入口并存，逐个面切换，不做一次性大爆炸）。

     布局与类名沿用设计系统的既有契约（`alibot-design-system/ui_kits/app/app.css` 与
     `studio/app.css` 是同一套类名的两个落点）：三栏尺寸、方形头像块、未读徽标色族、
     四态状态点都按名字消费，因此视觉上两版页面是同一个产品，不是一个新皮肤。 -->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import ChannelHeader from './components/ChannelHeader.vue';
import ComposerBox from './components/ComposerBox.vue';
import DockPanel from './components/DockPanel.vue';
import MessageStream from './components/MessageStream.vue';
import RailSidebar from './components/RailSidebar.vue';
import { createAppContext, useToasts } from './app-context.js';
import { t } from './i18n/index.js';
import { hasOpenModal } from './lib/modal-stack.js';
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
// 右侧 dock 在 ≤900px 是抽屉：`.is-open` 与「这一栏在不在」是两件事——
// `dockDrawerOpen` 管抽屉的滑入，`dockTarget` 管**形态**（推导，不另存一份）。
// 桌面它是常驻列，`dockDrawerOpen` 这一位不生效。
const dockDrawerOpen = ref(false);
// 当前打开着哪一个对象：`{ kind: 'run' | 'members' | 'task' }`，`null` ⇒ 整栏不渲染。
// 本票只有运行面板这一档会写它（`members` / `task` 归 T3 与 T7）。
const dockTarget = ref(null);
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
  dockTarget.value = null;
  dockDrawerOpen.value = false;
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

// 整页只有这一个键盘监听：ESC 的层序表在 `onEscape()` 里（T4 复用它，不另写一份）。
document.addEventListener('keydown', onEscape);
onBeforeUnmount(() => document.removeEventListener('keydown', onEscape));

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
  dockTarget.value = { kind: 'run' };
  dockDrawerOpen.value = true;
  await dockPanel.openForChannel();
}

/** 关闭运行面板：把形态整个作废（下次打开是干净的一帧），并收起移动端抽屉。 */
function closeRunPanel() {
  dockPanel.close();
  dockTarget.value = null;
  dockDrawerOpen.value = false;
}

/** 私信下三档一档都不出现：`dockAvailable()` 的判据（私信既没有 team 运行，也没有频道成员）。
    运行面板不是私信的东西，因此私信下 `dockTarget` 恒为 `null`，整栏消失。 */
const visibleDockTarget = computed(() => (isDm.value ? null : dockTarget.value));

/** ≤900px（单列壳）判据：右抽屉只在那一档是「盖在内容上的一层」。 */
const MOBILE_SHELL_QUERY = '(max-width: 900px)';

function mobileShell() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return Boolean(window.matchMedia(MOBILE_SHELL_QUERY).matches);
}

/**
 * ESC 的层序（唯一一份，T4 落地时直接复用本函数，不另写第二份）：
 * **命令面板 > 模态栈最上层（`busy` 时不收）> 移动左抽屉 rail > ≤900px 右抽屉 dock > 搜索视图**。
 *
 * 依据是旧页面 `app/alibot/studio/app.js:10578-10620` 的 document keydown 逐行顺序：
 * ① `#cmdOverlay` 未隐藏 ⇒ closeCmd；② `#agentDeleteOverlay` 未隐藏 ⇒ `busy` 时不收，否则关闭；
 * ③ 其余弹窗（settings / reminders / roster / budget / channel / agent / task）；④ 移动左抽屉；
 * ⑤ ≤900px 的右抽屉（桌面 dock 是常驻列，ESC 不管它）；⑥ `state.mode === 'search'` **最后**一层。
 * 有两处是**有意差异**：③ 在旧页面是无栈、一次全关，本版改成「只关最上面那一层」
 * （栈在 `lib/modal-stack.js`）；① 与 ⑥ 的面（命令面板 / 搜索视图）还没迁到本版，归 T4。
 */
function onEscape(event) {
  if (event.key !== 'Escape') return;
  // ①+②+③：命令面板与模态栈由 `ModalShell` 的模块级栈裁决（它在 document 的捕获阶段吃掉
  // ESC 并判 `busy`），因此这里只需要早退——两边都在，ESC 的唯一性就不依赖监听注册顺序。
  if (hasOpenModal()) return;
  // ④ 移动左抽屉盖在内容上，先收它。
  if (railOpen.value) {
    railOpen.value = false;
    return;
  }
  // ⑤ ≤900px 的右抽屉同样盖在内容上（它没有遮罩，ESC 是它的键盘出口）。
  if (mobileShell() && dockDrawerOpen.value) {
    closeRunPanel();
    return;
  }
  // ⑥ 搜索视图（`state.mode === 'search'`）是最后一层：随 T4 在这里补。
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
      :dock-target="visibleDockTarget"
      :panel="dockPanel"
      :pools="dockPools"
      :open="dockDrawerOpen"
      @close="closeRunPanel"
    />

    <div class="toast-wrap" data-od-id="toast-wrap">
      <div v-for="item in toasts.items" :key="item.id" class="toast" :class="{ err: item.err }">
        <span class="dot"></span>{{ item.message }}
      </div>
    </div>
  </div>
</template>
