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
import AgentFormModal from './components/AgentFormModal.vue';
import ChannelBudgetModal from './components/ChannelBudgetModal.vue';
import ChannelConfigModal from './components/ChannelConfigModal.vue';
import ChannelFormModal from './components/ChannelFormModal.vue';
import ChannelHeader from './components/ChannelHeader.vue';
import ComposerBox from './components/ComposerBox.vue';
import DockPanel from './components/DockPanel.vue';
import MessageStream from './components/MessageStream.vue';
import RailSidebar from './components/RailSidebar.vue';
import RemindersModal from './components/RemindersModal.vue';
import RosterModal from './components/RosterModal.vue';
import SettingsModal from './components/SettingsModal.vue';
import { createAppContext, useToasts } from './app-context.js';
import { t } from './i18n/index.js';
import { hasOpenModal } from './lib/modal-stack.js';
import { MAX_ATTACHMENT_BYTES, partitionBySize } from './lib/attachments.js';
import { formatBytes } from './lib/format.js';
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
// 频道头的三张弹窗（名册 / 配置 / 预算）：同一时刻只开一张，`toolChannel` 是**打开那一刻的频道**
// （旧页面 `state.rosterChannelId` / `state.channelConfig.channelId` 同一条：打开期间锁定频道，
// 人在弹窗里把别处切走也不影响它列的那份名单）。
const openTool = ref(null);
const toolChannel = ref(null);
// rail 头两个弹窗的显隐（内容仍挂载、只用 `hidden` 隐去，见 `ModalShell.vue` 文件头）。
const remindersOpen = ref(false);
const settingsOpen = ref(false);
// 两张新建表单的显隐（#301）。关掉时值仍留着上一次的"关"，表单自己负责开时清空。
const channelFormOpen = ref(false);
const agentFormOpen = ref(false);
// 折叠态（摘要行 / 旁白行）按 **seq** 认，跨 target 不通用：切 target 时必须整体清掉，
// 否则「在 A 频道展开过的第 3 条」到了 B 频道会莫名展开。这个状态住在模块级（见 ui-state.js），
// 因此清理的落点只有这一处。
const ui = useUiState();

const target = computed(() => store.activeTarget.value);
const isDm = computed(() => store.isDm.value);
const archived = computed(() => store.isArchivedDm.value);
const placeholder = computed(() => composerPlaceholder(target.value));
const messages = computed(() => store.renderedMessages());

/** 提醒圆点：判据在 store 里（存在 `fired_at == null` 的提醒），App 只负责往下传。 */
const reminderPending = computed(() => ctx.reminders.hasPending.value);

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
  // 手上的这批草稿随这条消息一起走：`attachment_ids` 与正文在**同一个请求**里（ADR-0014），
  // 后端在同一事务里绑定，因此不存在「附件落地了但消息没落地」的中间态。
  const drafts = state.composerAttachments.slice();
  const result = await store.sendMessage(
    text,
    drafts.map((item) => item.id),
    drafts,
  );
  if (!result.ok) {
    // 失败时**不清**手上的文件：同一批标识留在 composer，“重试”会带上它。
    if (result.offline) toasts.push(t('toast.sendBlockedOffline'), 'err');
    else if (result.error) toasts.push(t('toast.sendRejected', { reason: result.error.message }), 'err');
    return;
  }
  // 消息已经带着这批附件落地，草稿不再属于“手上”。
  store.clearComposerAttachments();
  if (stream.value) stream.value.scrollToBottom();
}

/**
 * 选中文件后的上传（两步上传的第一步）。
 *
 * 单文件上限在**发请求之前**判（`partitionBySize`，与后端的同一个数字）：超限的文件直接报错、
 * 不发请求——用一个 10 MB 的往返换一条本地就能得出的结论不值得。上传仍可能被后端拒（类型 /
 * 空内容 / 文件名），原因由后端按语言偏好渲染，这里只把它读出来。
 */
async function attachFiles(files) {
  const { accepted, rejected } = partitionBySize(files);
  rejected.forEach((file) => {
    toasts.push(
      t('composer.attachTooLarge', {
        name: file.name || t('attach.fallbackName'),
        limit: formatBytes(MAX_ATTACHMENT_BYTES),
      }),
      'err',
    );
  });
  if (!accepted.length) return;
  const { failed } = await store.attachFiles(accepted);
  failed.forEach(({ error }) => {
    toasts.push(t('toast.attachFailed', { reason: error.message }), 'err');
  });
}

/** 上传还没落地时按 Enter / 发送键：不发，并说清为什么（静默吞掉是最难解释的一种）。 */
function attachPending(count) {
  toasts.push(t('toast.attachPending', { count }), 'err');
}

/** Agent 详情面是否已迁到本版：T10 的开关（默认关 ⇒ rail 的 Agent 行是"打开私信"）。 */
const agentDetailAvailable = ref(false);

/** 新建频道弹窗的候选人：本机全名单里的 Agent（新频道里本来就没有成员，不按频道过滤）。 */
const agentRoster = computed(() => state.members.filter((member) => member.kind === 'agent'));

/** 新建表单的提交动作都留在这里：它们需要 `selectTarget` 与 toast，而那是 App 的职责。 */
async function submitChannel(fields) {
  const payload = await store.createChannel(fields);
  // 与旧页面同一条：新频道立刻成为当前 target（`selectTarget` 是进入一个 target 的唯一入口）。
  await selectTarget(payload.channel.id);
  toasts.push(t('toast.channelCreated', { name: payload.channel.name }));
  // 弹窗自己关闭（`created` 之后）；这里只负责"切过去"与告知。
  return payload;
}

async function submitAgent(fields) {
  const payload = await store.createAgent(fields);
  toasts.push(t('toast.agentCreated', { name: payload.agent.display_name }));
  return payload;
}

/**
 * 确认远端数据流告知（弹窗里那一块）。
 *
 * 确认会改变成员的启用态，因此成功后要按后端重读：`consents` 与 `agents` 两件都变了，
 * 而 agent 行的状态点只读后端事实（不在前端推导"确认过了所以现在能跑了"）。
 * 弹窗**不关**：人还要继续填表单。
 */
async function acknowledgeRemote(provider) {
  try {
    await store.acknowledgeRemoteFlow(provider);
    await store.refreshStatuses();
    toasts.push(t('toast.remoteAck', { provider }));
  } catch (err) {
    // 失败不吞也不叠两层提示：弹窗里那一块自己就地显示原因（这正是人在看的地方）。
    throw err;
  }
}

/**
 * rail 的 Agent 行：`agentDetailAvailable` 开 ⇒ 进详情（归 T10），关 ⇒ 打开与它的私信。
 *
 * 关着的那一支走幂等的 `POST /api/dms {agent_id}`（旧页面同一条，`app.js:5184-5215`）：
 * 选中一个成员就同时表达了"和谁私信"，不需要先选目标再点创建的两步弹窗。
 */
async function openAgentRow(memberId) {
  if (agentDetailAvailable.value) {
    // Agent 详情整面归 T10：本票不摆一个"开了也没处去"的入口。
    return;
  }
  try {
    const payload = await store.openDm(memberId);
    // `selectTarget` 会按新 target 重读消息 / 轮次结果 / 成员；私信下 dock 一档都不出现。
    await selectTarget(payload.dm.id);
    if (!state.online) return;
    toasts.push(
      payload.created
        ? t('toast.dmCreated', { name: payload.dm.peer.display_name })
        : t('toast.dmOpened', { name: payload.dm.peer.display_name }),
    );
  } catch (err) {
    toasts.push(t('toast.dmOpenFailed', { reason: err.message }), 'err');
  }
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

async function retryPending(clientId) {
  const pending = state.pending.find((item) => item.clientId === clientId);
  const attachmentIds = (pending && pending.attachment_ids) || [];
  const result = await store.retryPending(clientId);
  // 重试成功 ⇒ 那批草稿已经绑到这条消息上了，不能再留在手上：下一条消息会带上一批**已绑定**的
  // 标识，而后端对重复绑定回 `error.attachment_already_bound`，整条消息会被拒掉。
  if (result && result.ok) attachmentIds.forEach((id) => store.removeComposerAttachment(id));
}

function discardPending(clientId) {
  store.discardPending(clientId);
}

function unsupportedMention(name) {
  toasts.push(t('mention.quoteUnsupported', { name }), 'err');
}

/** 频道头三个工具入口的**唯一**落点：锁定当前频道，再决定开哪一张弹窗。
    私信下三个入口都不渲染（`!isDm`），因此这里不必再判 target 类型。 */
function openChannelTool(kind) {
  if (!target.value || isDm.value) return;
  toolChannel.value = target.value;
  openTool.value = kind;
}

function closeChannelTool() {
  openTool.value = null;
  toolChannel.value = null;
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
      :reminder-pending="reminderPending"
      :agent-detail-available="agentDetailAvailable"
      @select-target="selectTarget"
      @open-reminders="remindersOpen = true"
      @open-settings="settingsOpen = true"
      @new-channel="channelFormOpen = true"
      @new-agent="agentFormOpen = true"
      @open-agent="openAgentRow"
      @open-dm="openAgentRow"
    />

    <ChannelFormModal
      v-model:open="channelFormOpen"
      :agents="agentRoster"
      :submit="submitChannel"
    />
    <AgentFormModal
      v-model:open="agentFormOpen"
      :providers="state.providers"
      :local-providers="state.localProviders"
      :consents="state.consents"
      :data-faces="state.dataFaces"
      :submit="submitAgent"
      :acknowledge="acknowledgeRemote"
      @reload-remote-flow="store.loadRemoteFlow()"
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
        :channel-tools-available="true"
        @select-tab="activeTab = $event"
        @open-roster="openChannelTool('roster')"
        @open-config="openChannelTool('config')"
        @open-budget="openChannelTool('budget')"
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
        :attachments="state.composerAttachments"
        :attaching="state.attachingCount"
        @send="send"
        @attach-files="attachFiles"
        @remove-attachment="store.removeComposerAttachment"
        @attach-pending="attachPending"
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

    <!-- rail 头的两个弹窗（T2）：外壳是共享的 `ModalShell`，这里只给显隐与数据源。
         归属频道 = 当前 target 是频道时它的 id；私信 / 空工作台 ⇒ `null`（后端读作「不归属」）。 -->
    <RemindersModal
      v-model:open="remindersOpen"
      :reminders="ctx.reminders"
      :channel-id="isDm ? null : state.activeTargetId"
    />

    <!-- 设置：三个档位的选中态读 `state.settings`（后端正本的前端镜像），写回一律经
         `ctx.applySettings`（它是铺根元素的**唯一**写入口，不挂 watcher）。 -->
    <SettingsModal
      v-model:open="settingsOpen"
      :settings="state.settings"
      :apply-settings="ctx.applySettings"
    />

    <div class="toast-wrap" data-od-id="toast-wrap">
      <div v-for="item in toasts.items" :key="item.id" class="toast" :class="{ err: item.err }">
        <span class="dot"></span>{{ item.message }}
      </div>
    </div>

    <!-- 名册是**弹窗形态**（旧 `openRosterModal()` 就是弹窗）：dock 的 `members` 档是另一张票的面，
         两者不互换。 -->
    <RosterModal
      :open="openTool === 'roster'"
      :channel="toolChannel"
      :store="store"
      :toast="toasts.push"
      @close="closeChannelTool"
    />
    <ChannelConfigModal
      :open="openTool === 'config'"
      :channel="toolChannel"
      :store="store"
      @close="closeChannelTool"
    />
    <ChannelBudgetModal
      :open="openTool === 'budget'"
      :channel="toolChannel"
      :store="store"
      :toast="toasts.push"
      @close="closeChannelTool"
    />
  </div>
</template>
