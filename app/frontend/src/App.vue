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
import AgentDetailView from './components/AgentDetailView.vue';
import AgentFormModal from './components/AgentFormModal.vue';
import ChannelFormModal from './components/ChannelFormModal.vue';
import ChannelHeader from './components/ChannelHeader.vue';
import ChannelToolsModal from './components/ChannelToolsModal.vue';
import CommandPalette from './components/CommandPalette.vue';
import ComposerBox from './components/ComposerBox.vue';
import DockPanel from './components/DockPanel.vue';
import MessageStream from './components/MessageStream.vue';
import RailSidebar from './components/RailSidebar.vue';
import RemindersModal from './components/RemindersModal.vue';
import SearchView from './components/SearchView.vue';
import SettingsModal from './components/SettingsModal.vue';
import TaskBoard from './components/TaskBoard.vue';
import TaskFormModal from './components/TaskFormModal.vue';
import { createAppContext, useToasts } from './app-context.js';
import { t } from './i18n/index.js';
import { hasOpenModal } from './lib/modal-stack.js';
import { MAX_ATTACHMENT_BYTES, partitionBySize } from './lib/attachments.js';
import { formatBytes } from './lib/format.js';
import { composerPlaceholder, messageTabCount } from './lib/target-view.js';
import { agentMembers } from './lib/member-view.js';
import { createAgentDetailStore } from './stores/agent-detail.js';
import { createSearchStore } from './stores/search.js';
import { createTaskBoardStore } from './stores/task-board.js';
import { createTaskThreadStore } from './stores/task-thread.js';
import { channelPendingTaskCount } from './stores/approvals.js';
import { useUiState } from './stores/ui-state.js';

const props = defineProps({
  // 测试替身入口：事件源的构造器可以注入（生产环境一律用浏览器原生的 `EventSource`）。
  // `fetch` 不在这里注入——用例直接换 `globalThis.fetch`，与 `app-context.test.js` 同一条路。
  sourceFactory: { type: Function, default: null },
});

/**
 * 当前打开着的任务（运行面板的标题与 `task_id` 过滤读它）。
 *
 * `app-context.js` 的 `createRunPanelStore({ activeTask })` 这个缝就是给任务线程留的：面板的
 * `title` computed 在 `state.taskId` 有值时取**任务标题**、没有时才回退频道名。因此这一位必须
 * 指向「线程现在开着的那条任务」，而不是另存一份 id（那会出现「线程换了、面板标题没换」）。
 * 线程 store 在本函数稍后创建，所以这里用闭包取它——创建那一刻还没有它。
 */
let threadStore = null;

const toasts = useToasts();
const ctx = createAppContext({
  toast: toasts.push,
  sourceFactory: props.sourceFactory || undefined,
  activeTask: () => (threadStore ? threadStore.state.task : null),
});
const { state, store } = ctx;

const stream = ref(null);

/** 删除详情里那个 Agent 后，把焦点交给主线容器这个确定落点。
 *
 * 详情整面卸载后，`ModalShell` 的 opener（删除键）已经 `isConnected === false`，它的
 * `restoreFocus()` 因此不动作；不补这一格，键盘用户的焦点就落回 `body`（旧页面的详情从不整面
 * 卸载到 body——它关的是面板、人还停在左栏）。`#mainColumn` 带 `tabindex="-1"`，可编程聚焦。 */
function focusMainColumn() {
  if (typeof document === 'undefined') return;
  const node = document.getElementById('mainColumn');
  if (node && typeof node.focus === 'function') node.focus();
}
const railOpen = ref(false);
// 右侧 dock 在 ≤900px 是抽屉：`.is-open` 与「这一栏在不在」是两件事——
// `dockDrawerOpen` 管抽屉的滑入，`dockTarget` 管**形态**（推导，不另存一份）。
// 桌面它是常驻列，`dockDrawerOpen` 这一位不生效。
const dockDrawerOpen = ref(false);
// 当前打开着哪一个对象：`{ kind: 'run' | 'members' | 'task' }`，`null` ⇒ 整栏不渲染。
// 它是 dock 形态的**唯一**判据（票 #300 立表）：任务线程 ⇒ `task`（票 #307），从线程开运行面板 ⇒
// `run`，关掉 ⇒ `null`（不占位）。不另存第二份形态状态——那必然出现「关掉了但 dock 还停在旧形态」。
const dockTarget = ref(null);
// 主线视图（票 #304 新增的状态维度，ADR-0038 §五里没有它）：`'channel'`（消息流）、
// `'board'`（任务看板，票 #306）、`'search'`（搜索占住消息流那块位置）或 `'agent'`（Agent 详情，
// 票 #310）。四者是**主线视图**而不是弹层（旧页面 `showSearchView()` / `showTaskBoard()` /
// `setViewMode('agent')` 的注释与人定 2026-10-17 的 D14），因此都不进 `modal-stack`、也不盖住界面。
// 它住在 App 的 `ref` 里而不是 `stores/workspace.js` 的 `state`：后者是**后端镜像**（每个字段
// 都能在后端载荷里找到同名出点），`mode` 是纯呈现状态，混进去会让「哪些字段是后端说的」失守。
const mode = ref('channel');
// ⌘K 命令面板的显隐（`overlay` 不是 `modal`：关闭语义与 z-index 都不同，见 `CommandPalette.vue`）。
const cmdOpen = ref(false);
// 频道工具弹窗（#344：成员 / 设置 / 预算三张面共用一枚入口）：`toolChannel` 是**打开那一刻的频道**
// （旧页面 `state.rosterChannelId` / `state.channelConfig.channelId` 同一条：打开期间锁定频道，
// 人在弹窗里把别处切走也不影响它列的那份名单）。页签选择在 `ChannelToolsModal` 内部，不在这里。
const channelToolsOpen = ref(false);
const toolChannel = ref(null);
// rail 头两个弹窗的显隐（内容仍挂载、只用 `hidden` 隐去，见 `ModalShell.vue` 文件头）。
const remindersOpen = ref(false);
const settingsOpen = ref(false);
// 三张新建表单的显隐（#301 的频道 / Agent，#307 的任务）。关掉时值仍留着上一次的"关"，
// 表单自己负责开时清空。
const channelFormOpen = ref(false);
const agentFormOpen = ref(false);
const taskFormOpen = ref(false);
// 折叠态（摘要行 / 旁白行）按 **seq** 认，跨 target 不通用：切 target 时必须整体清掉，
// 否则「在 A 频道展开过的第 3 条」到了 B 频道会莫名展开。这个状态住在模块级（见 ui-state.js），
// 因此清理的落点只有这一处。
const ui = useUiState();

const target = computed(() => store.activeTarget.value);
const isDm = computed(() => store.isDm.value);
// 只读归档：私信（Agent 已删除）与频道（人自己归档）共用这一档（#342 / ADR-0040）。
const archived = computed(() => store.isArchivedTarget.value);
const placeholder = computed(() => composerPlaceholder(target.value));
const messages = computed(() => store.renderedMessages());

/** 提醒圆点：判据在 store 里（存在 `fired_at == null` 的提醒），App 只负责往下传。 */
const reminderPending = computed(() => ctx.reminders.hasPending.value);

/** 搜索的读数与请求（版本守卫 / 防抖 / facet 换请求都在 store 里，见 `stores/search.js`）。 */
const search = createSearchStore({ isOnline: () => state.online });

/**
 * 任务看板的读数与呈现状态（票 #306，见 `stores/task-board.js`）。
 *
 * 「我的任务」的判据左半边是 `state.member.id`（本机人类成员，`OWNER_FALLBACK`），右半边是任务
 * 载荷自己的 `assignee_member_id`；离线读数与 toast 从这里往下传，组件不各自判一次。
 */
const board = createTaskBoardStore({
  ownerId: () => state.member.id,
  online: () => state.online,
  toast: toasts.push,
});

/**
 * 任务线程的读数与写路径（票 #307，见 `stores/task-thread.js`）。
 *
 * `onTaskChanged` 把线程里回带的最新任务行同步回看板快照：状态 / 标题 / 指派一改，页签计数与卡片
 * 立刻跟着变（旧页面 `renderTaskCount()` 的那条理由——看板还停在主线，不重铺就只能靠这一笔同步）。
 */
threadStore = createTaskThreadStore({
  online: () => state.online,
  toast: toasts.push,
  onTaskChanged: (task) => {
    if (task) board.replaceTask(task);
  },
  refreshChannelMembers: (channelId) => store.refreshChannelMembers(channelId),
  loadChannelConfig: (channelId) => store.loadChannelConfig(channelId),
  // 任务线程自己的事件流（票 #307 补做）：构造器与全局流同一条注入路径。
  sourceFactory: props.sourceFactory || null,
  // live-only 运行事件与全局流**同一份落点**（`ctx` 在下面创建；用闭包取，创建这一刻还没有它）。
  onLiveFrame: (payload, type) => {
    const targetId = payload.target_id;
    ctx.liveLog.record(targetId, payload);
    if (type === 'sop_event') ctx.runPanel.noteSopFrame(payload);
    if (type === 'approval_required' || type === 'approval_resolved') {
      ctx.approvals.applyFrame({ ...payload, type });
    }
  },
  // 其余可补拉帧与全局流同一套语义（`typing` / `member_status` / `round_outcome`）。
  onTaskFrame: (payload, type) => ctx.handleTargetFrame(payload, type),
  // 重连成功后按运行面板快照兜底（旧 `source.onopen` 的 `refreshRunPanelFor()`）。
  onStreamOpen: (targetId) => ctx.runPanel.refreshFor(targetId),
});
const thread = threadStore;

/** 频道页签的选中态由 `mode` 推导（一份状态，两处呈现：页签与主线视图）。 */
const activeTab = computed(() => (mode.value === 'board' ? 'tasks' : 'messages'));

/** 搜索视图是否正占着主线：`mode` 的唯一读法（模板与键盘层都读它）。 */
const searchActive = computed(() => mode.value === 'search');

/** 消息流与 composer 在这一档里**存在**：看板与 Agent 详情是另两个主线视图（旧 `setViewMode()`
    收起两者），搜索只是把它们藏起来（`hidden`，滚动位置与没发出去的草稿都得留着）。 */
const messagesViewAvailable = computed(() => mode.value !== 'board' && mode.value !== 'agent');

/** 频道头（标题 + 消息 / 任务页签）是否让位。
    搜索与 Agent 详情都不是「某个频道 / DM 的内容」：前者自己铺满、后者占整条右侧，
    因此频道头在这两档一起让位（旧 `syncChromeVisibility()` 同一条判据）。 */
const chromeHidden = computed(() => searchActive.value || mode.value === 'agent');

/** 运行面板的成员名来源：工作区成员 → Agent → 当前频道成员（找不到就用 id，不编名字）。 */
const dockPools = computed(() => [
  state.members,
  state.agents,
  state.channelMembers[state.activeTargetId] || [],
]);

/** 运行面板 store（任务面板的标题 / `task_id` 过滤读 `ctx.runPanel`，判据全在 store 里）。 */
const dockPanel = ctx.runPanel;

/** 线程的指派候选 = **所属频道**的 Agent 成员（`null` = 还没读到；空数组 = 真的没有）。 */
const threadAssignees = computed(() => {
  const task = thread.state.task;
  return task ? store.channelAgentMembers(task.channel_id) : null;
});

/** 成员表读失败要说事实（「暂时读不到」而不是摆一个空清单）。 */
const threadAssigneesFailed = computed(() => {
  const task = thread.state.task;
  return Boolean(task) && state.channelMembersFailed.includes(task.channel_id);
});

/** 所属频道的协作模式（只有读回过配置且有值时才铺 team 提示；读不到 ⇒ `null`，什么都不说）。 */
const threadChannelMode = computed(() => {
  const task = thread.state.task;
  const config = task ? state.channelConfigs[task.channel_id] : null;
  return config && config.mode ? config.mode : null;
});

/** 候选来源：`null` = 还没读到（去读一次），空数组才是真的没有 Agent 成员。 */
const mentionCandidates = computed(() => store.channelAgentMembers(state.activeTargetId));

async function selectTarget(targetId) {
  // 选一个 target = 回到消息流（旧 `selectTarget()` 的 `state.mode = 'channel'`）。这一句必须在
  // 「同 target 早退」**之前**：旧页面没有那道早退，点当前那一条同样是回消息流；v2 的同 target
  // 早退只为了避免重取一次消息，与「视图退回哪一档」是两件事。
  // showMessagesView() 也会关闭 Agent 详情并丢弃草稿。
  showMessagesView();
  if (targetId === state.activeTargetId) return;
  // 先清旧 target 的折叠态，再换 target：顺序反了就会清掉新 target 刚铺上的状态。
  ui.resetForTarget();
  // 运行面板读的是**上一个 target** 的快照与 live 归属：换 target 时整个作废，
  // 不把旧频道的分派留在新频道旁边（旧实现同一条：`resetRunPanelState()`）。
  dockPanel.resetForTarget(true);
  // 任务线程读的也是**上一个 target** 的东西：换 target 时整个作废（旧 `closeTaskDock()` 的
  // `taskMessages = []`）。与上面那条并排，不另存一份「线程开着吗」。
  thread.resetForTarget();
  dockTarget.value = null;
  dockDrawerOpen.value = false;
  store.setActiveTargetId(targetId);
  railOpen.value = false;
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

/** Agent 详情面是否已迁到本版：T10 的开关（票 #310 翻 `true`）。
 *
 * 它只裁决 rail 的 Agent 行点下去干什么（开 ⇒ 进详情，关 ⇒ 打开私信）；那一段的点击语义归票 #301
 * 落地时定下，本票**只翻开关、不改那段逻辑**。 */
const agentDetailAvailable = ref(true);

/** 新建频道弹窗的候选人：本机全名单里的 Agent（新频道里本来就没有成员，不按频道过滤）。 */
const agentRoster = computed(() => agentMembers(state.members));

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
 * 新建任务：后端建行 + 在频道流里落一条 `system` 指针消息（同一事务）。
 *
 * 成功后的两笔善后与旧 `submitTask()` 同一条：把新任务补进看板快照（页签计数立刻跟着变）并重读
 * 一次（筛选 / 其它频道的任务都可能变了）。指针消息由 store 按「到达的消息」吸收，因此当前频道
 * 的消息流立刻出现那一行（system 不计未读，页签读数不变）。
 */
async function submitTask(fields) {
  const payload = await store.createTask(fields.channelId, fields.title);
  if (payload && payload.task) board.replaceTask(payload.task);
  await board.refresh();
  toasts.push(t('toast.taskCreated', { title: payload.task.title }));
  return payload;
}

/**
 * 确认远端数据流告知（新建弹窗与 Agent 详情共用）。
 *
 * 确认会改变成员的启用态，因此成功后要按后端重读：`consents` 与 `agents` 两件都变了，
 * 而 agent 行的状态点只读后端事实（不在前端推导"确认过了所以现在能跑了"）。
 * 新建弹窗**不关**：人还要继续填表单；Agent 详情按响应里的确认读数收起告知块。
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
 * Agent 详情（主线视图的第四档，票 #310）。
 *
 * 旧落点 `app.js` 的 `openAgentDetail()` / `closeAgentDetail()` / `leaveAgentView()`：进详情时
 * **先收掉 dock**（旧 `closeRunPanel()` + `closeTaskStream()`：运行面板与任务线程都是「占右侧」
 * 的呈现，而详情是主线视图、dock 让位），再切 `mode`。关闭时回到进入前的视图（`agentReturn`）。
 *
 * 读数与写路径全在 `stores/agent-detail.js`：这里只负责“切到哪一档”与“关闭时回到哪一档”。
 */
const agentDetail = createAgentDetailStore({
  online: () => state.online,
  toast: toasts.push,
  // 主读数失败时退回工作台已有的这一行镜像（旧页面也从 `state.agents` 取这份显示）。
  fallbackAgent: (memberId) => store.mutable.agents.find((item) => item.id === memberId) || null,
  onAgentUpdated: (payload) => {
    if (payload && Array.isArray(payload.agents)) store.mutable.agents = payload.agents;
    if (payload && Array.isArray(payload.members)) store.mutable.members = payload.members;
    if (payload && payload.agent && payload.agent.id && payload.agent.status) {
      store.mutable.statuses[payload.agent.id] = payload.agent.status;
    }
  },
  // 删除成功后同步名单与频道成员缓存，重读私信的归档状态。
  onAgentsChanged: (payload, memberId, name) => {
    if (payload && Array.isArray(payload.agents)) store.mutable.agents = payload.agents;
    if (payload && Array.isArray(payload.members)) store.mutable.members = payload.members;
    store.mutable.agents = store.mutable.agents.filter((item) => item.id !== memberId);
    store.mutable.members = store.mutable.members.filter((item) => item.id !== memberId);
    Object.keys(store.mutable.channelMembers).forEach((channelId) => {
      const members = store.mutable.channelMembers[channelId];
      if (Array.isArray(members)) {
        store.mutable.channelMembers[channelId] = members.filter((item) => item.id !== memberId);
      }
    });
    delete store.mutable.statuses[memberId];
    if (mode.value === 'agent' && agentDetail.state.memberId === memberId) closeAgentDetail();
    ctx.refreshDmList();
    toasts.push(t('toast.agentDeleted', { name: name || t('toast.memberFallback') }));
    // 删除后整面详情卸载，`ModalShell` 的 opener（删除键）已 isConnected=false，焦点会落回 body。
    // 键盘用户从详情删除后必须能继续操作：把焦点交给主线容器这个确定落点。
    focusMainColumn();
  },
});

/** rail 的当前行判据：详情档是**详情打开的那个成员**，其余档没有 Agent 是当前行。
 *
 * 为什么不复用 `state.activeTargetId`：进详情**不换 target**（详情不是某个 target 的内容，
 * 关掉要回到进入前的消息流），于是它记的仍是进入前那条频道 / 私信。让 rail 继续读它，
 * 打开详情后那条旧 target 就会一直亮着、对应的 Agent 行反倒没有当前态（截图里的缺陷）。
 * 这一份派生值与 `mode` / 详情 store 同源，因此切换 Agent、返回消息流、详情内发送消息
 * 三条路都不需要各自维护一份「谁被选中」。 */
const activeAgentId = computed(() => (mode.value === 'agent' ? agentDetail.state.memberId : null));

/** 进详情前的落点（旧 `state.agentReturn`）：只在**第一次**进入时记录，切成员不覆盖它。
 *
 * 三位都得记：`mode` 决定关闭后回哪一档主线视图；`dockTarget` / `dockDrawerOpen` 决定 dock
 * 能不能、以什么形态开回来。旧页面 `closeAgentDetail()` 恢复的是
 * `setDockOpen(Boolean(back.dockOpen) && dockAvailable())`（`studio/app.js:5156`），而它的
 * 「开合」背后总有一个可显示形态（`dockFallbackMode()` 给 `agent`）；v2 没有成员档，
 * 只凭开合开回来会得到一张空面板——旧注释里正好警告过这个结果。因此这里记住**形态本身**。
 */
let agentReturn = null;

/** 打开 Agent 详情（rail 的 Agent 行是唯一入口）。 */
async function openAgentDetail(memberId) {
  if (!memberId) return;
  if (mode.value !== 'agent') {
    agentReturn = {
      mode: mode.value,
      dockTarget: dockTarget.value,
      dockOpen: dockDrawerOpen.value,
    };
  }
  // 运行面板若来自任务线程，第一次关闭会退回该线程；详情需要连线程一起收掉。
  closeDock();
  if (dockTarget.value) closeDock();
  dockDrawerOpen.value = false;
  mode.value = 'agent';
  railOpen.value = false;
  const loaded = await agentDetail.open(memberId);
  // 切走了（点频道行 / `M`）就不弹任何话：各读数已被 `state.memberId` 守卫作废，`open()` 也
  // 会返回 null，但那是「目标已换」而不是「读失败」——给新成员弹一条空原因的失败是误报。
  if (agentDetail.state.memberId !== memberId) return;
  // 主读数读不到：名称、统计与草稿都没有可展示的正本。与旧 `refreshAgentDetail()` 同一条
  // ——说明原因，不把空白名称与全 0 统计当成这个 Agent 的事实（页面上另有持久错误行）。
  if (!loaded) {
    const reason = agentDetail.view.value ? agentDetail.view.value.agentError : '';
    toasts.push(t('toast.agentDetailFailed', { reason: reason || '' }), 'err');
  }
}

/** 关闭详情：回到进入前的那一档，并把 dock 恢复成进入前的形态与开合。
 *
 * 与旧 `closeAgentDetail()` 同一条意图（strangler 迁移不缩水）：进入前 dock 开着、且那个形态
 * 现在仍可显示，就把它与开合一起恢复；没有可显示形态（私信 / 没有 target / 形态已经作废）就
 * 保持收起，不摆一张空面板。 */
function closeAgentDetail() {
  const back = agentReturn || { mode: 'channel', dockTarget: null, dockOpen: false };
  agentDetail.close();
  mode.value = back.mode && back.mode !== 'agent' ? back.mode : 'channel';
  agentReturn = null;
  const restore = Boolean(back.dockOpen) && Boolean(back.dockTarget) && dockToggleAvailable.value;
  dockTarget.value = restore ? back.dockTarget : null;
  dockDrawerOpen.value = restore;
}

/** 详情头部的「发送消息」在途标记（旧 `agentDmInFlight`，`studio/app.js:5173`）。
 * 它是**重入闸门**：连点只发一条 `POST /api/dms`，按钮同时禁用（见模板的 `:disabled`）。 */
const agentDmInFlight = ref(false);

/** 详情头部的「发送消息」：走幂等的 `POST /api/dms {agent_id}`（旧 `messageActiveAgent()`）。 */
async function messageActiveAgent() {
  const memberId = agentDetail.state.memberId;
  if (!memberId || agentDmInFlight.value) return;
  agentDmInFlight.value = true;
  try {
    const payload = await store.openDm(memberId);
    // 离开详情再切 target：`selectTarget()` 自己会把 `mode` 收回消息流。
    closeAgentDetail();
    await selectTarget(payload.dm.id);
    if (!state.online) return;
    toasts.push(
      payload.created
        ? t('toast.dmCreated', { name: payload.dm.peer.display_name })
        : t('toast.dmOpened', { name: payload.dm.peer.display_name }),
    );
  } catch (err) {
    toasts.push(t('toast.dmOpenFailed', { reason: err.message }), 'err');
  } finally {
    agentDmInFlight.value = false;
  }
}

/**
 * rail 的 Agent 行：`agentDetailAvailable` 开 ⇒ 进详情，关 ⇒ 打开与它的私信。
 *
 * 关着的那一支走幂等的 `POST /api/dms {agent_id}`（旧页面同一条，`app.js:5184-5215`）：
 * 选中一个成员就同时表达了"和谁私信"，不需要先选目标再点创建的两步弹窗。
 *
 * 这一段语义归票 #301 落地时定下（`agentDetailAvailable` 一个开关裁决两档），票 #310 **只翻开关**。
 */
async function openAgentRow(memberId) {
  if (agentDetailAvailable.value) {
    await openAgentDetail(memberId);
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
  // 任务快照也随首载读一次：`tab-tasks` 的计数是**当前频道**的任务数，页签在消息页也要显示它。
  // 读失败保留空快照（任务面读不到不影响频道消息与发送，旧 `loadWorkspace()` 同一条）。
  board.refresh();
  // 全工作区的待审批读数（旧 `loadPendingApprovals`）：页签圆点与卡片角标的读数来源。
  // 本票**不做 12s 周期刷新**（理由在 `stores/approvals.js` 文件头）：替代是这条首读 + 重连后的
  // 一次全量重读 + 每条 `approval_*` live 帧 + 提交回执。因此圆点不会因缺定时器而永远不亮。
  approvals.loadAll().catch(() => {});
});

/**
 * 全局键盘的唯一入口（旧 `bindEvents()` 末尾那一段 document keydown）。
 *
 * 三件事，顺序与旧页面逐行对齐：
 * 1. `⌘K` / `Ctrl+K` 打开命令面板（**它真绑定的是打开面板本身**，不是搜索视图；
 *    因此 rail 的搜索按钮上不印 `⌘K` 徽标——界面不承诺一个按键做不到的事）；
 * 2. `ESC` 走层序表 `handleEscape()`；
 * 3. `M` / `T` 两个单键跳转，**只在人不在输入框里时**生效（旧页面 `target.matches('input,textarea')`）。
 */
function onGlobalKeydown(event) {
  if ((event.metaKey || event.ctrlKey) && String(event.key).toLowerCase() === 'k') {
    event.preventDefault();
    cmdOpen.value = true;
    return;
  }
  if (event.key === 'Escape') {
    handleEscape();
    return;
  }
  // 单键跳转不穿透弹窗与命令面板：那两层有自己的输入框与快捷键语义。
  if (hasOpenModal() || cmdOpen.value) return;
  const target = event.target;
  if (target && typeof target.matches === 'function' && target.matches('input,textarea')) return;
  const key = String(event.key).toLowerCase();
  if (key === 'm') showMessagesView();
  if (key === 't') showTaskBoard();
}

/**
 * ESC 的层序（唯一一份）：
 * **命令面板 > 模态栈最上层（`busy` 时不收）> 移动左抽屉 rail > ≤900px 右抽屉 dock > 搜索视图**。
 *
 * 依据是旧页面 `app/alibot/studio/app.js:10578-10620` 的 document keydown 逐行顺序：
 * ① `#cmdOverlay` 未隐藏 ⇒ closeCmd；② `#agentDeleteOverlay` 未隐藏 ⇒ `busy` 时不收，否则关闭；
 * ③ 其余弹窗（settings / reminders / roster / budget / channel / agent / task）；④ 移动左抽屉；
 * ⑤ ≤900px 的右抽屉（桌面 dock 是常驻列，ESC 不管它）；⑥ `state.mode === 'search'` **最后**一层。
 * 有一处是**有意差异**：③ 在旧页面是无栈、一次全关，本版改成「只关最上面那一层」
 * （栈在 `lib/modal-stack.js`）。
 */
function handleEscape() {
  // ① 命令面板是 `.overlay` + `.cmd`（不是 `.modal`）：不进模态栈，层序第①格自己裁决。
  if (cmdOpen.value) {
    cmdOpen.value = false;
    return;
  }
  // ②+③：模态栈由 `ModalShell` 裁决（它在 document 的**捕获**阶段吃掉 ESC 并判 `busy`），
  // 因此这里只需要早退——两边都在，ESC 的唯一性就不依赖监听注册顺序。
  if (hasOpenModal()) return;
  // ④ 移动左抽屉盖在内容上，先收它。
  if (railOpen.value) {
    railOpen.value = false;
    return;
  }
  // ⑤ ≤900px 的右抽屉同样盖在内容上（它没有遮罩，ESC 是它的键盘出口）。
  if (mobileShell() && dockDrawerOpen.value) {
    closeDock();
    return;
  }
  // ⑥ 搜索视图是最后一层：ESC 退回消息流。看板与 Agent 详情**不**在这一层（旧页面同一条：
  // 它在看板里 ESC 什么都不做，于是卡片「流转」菜单的 ESC 不会被主线视图抢走）。
  if (mode.value === 'search') showMessagesView();
}

// 整页只有这一个键盘监听（ESC 的层序与 ⌘K / M / T 共用一个入口，不各挂一份）。
document.addEventListener('keydown', onGlobalKeydown);
onBeforeUnmount(() => document.removeEventListener('keydown', onGlobalKeydown));

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

/** 频道工具入口的**唯一**落点：锁定当前频道，再打开那张宽弹窗（页签由弹窗自己管）。
    私信下入口不渲染（`!isDm`），因此这里不必再判 target 类型。 */
function openChannelTools() {
  if (!target.value || isDm.value) return;
  toolChannel.value = target.value;
  channelToolsOpen.value = true;
}

function closeChannelTools() {
  channelToolsOpen.value = false;
  toolChannel.value = null;
}

/**
 * 频道归档 / 取消归档（#342 / ADR-0040）：归档入口在频道工具 → 设置页签的危险区。
 *
 * 读数落地全在 store 的 `archiveChannel` / `unarchiveChannel`（用响应里的 `channels` 整份覆盖，
 * 与 `markRead` 同一条纪律）；这里只补两句**人类面**的告知，并且**不在本地改任何读数**——
 * 归档后频道仍留在 `state.channels` 里（带 `archived_at`），左栏因此自动把它挪进「已归档」段。
 *
 * 返回 `true` / `false` 是给 `ChannelToolsModal` 的关闭判据：成功才关弹窗（失败时人还要看着
 * 弹窗里的那一页与 toast 上的失败原因）。归档锁定的频道是 `toolChannel`（打开那一刻的那一个）。
 */
async function archiveChannel() {
  const channel = toolChannel.value || target.value;
  if (!channel || isDm.value || channel.archived_at) return false;
  try {
    await store.archiveChannel(channel.id);
    toasts.push(t('toast.channelArchived', { name: channel.name }));
    return true;
  } catch (err) {
    toasts.push(t('toast.channelArchiveFailed', { reason: err.message }), 'err');
    return false;
  }
}

async function unarchiveChannel() {
  const channel = target.value;
  if (!channel || isDm.value || !channel.archived_at) return;
  try {
    await store.unarchiveChannel(channel.id);
    toasts.push(t('toast.channelUnarchived', { name: channel.name }));
  } catch (err) {
    toasts.push(t('toast.channelUnarchiveFailed', { reason: err.message }), 'err');
  }
}

/**
 * 删除频道（频道工具 → 设置页签危险区的确认条）：抛错交给面板内呈现，成功才收尾。
 *
 * 删掉的是**当前 target** 时，主线必须落到一个可用的地方（票面判据：不残留上一个频道的消息）：
 * 这里按左栏活跃段的顺序取第一个可用频道，没有就用空态——**与 `loadWorkspace()` 的空态处理
 * 走同一条路径**（`selectTarget(null)` + `refreshMessages()` 在空 target 上把 `messages` 清空），
 * 不新造第二套。
 */
async function deleteChannel(channelId) {
  const channel = state.channels.find((item) => item.id === channelId) || toolChannel.value;
  const wasActive = state.activeTargetId === channelId;
  await store.deleteChannel(channelId);
  toasts.push(t('toast.channelDeleted', { name: channel ? channel.name : channelId }));
  closeChannelTools();
  if (!wasActive) return;
  // 活跃段的第一个可用频道（`state.channels` 已由被删响应整份覆盖，这一条因此已经是真事实）。
  const next = state.channels.find((item) => !item.archived_at) || state.channels[0] || null;
  await selectTarget(next ? next.id : null);
}

/** 频道头的运行面板入口：打开 dock 的第三种形态（读完快照前先铺加载态）。 */
async function openRunPanel() {
  dockTarget.value = { kind: 'run' };
  dockDrawerOpen.value = true;
  await dockPanel.openForChannel();
}

/**
 * 任务线程头的运行面板入口（旧 `openRunPanelForTask()`）：同一个 dock 换到 `run` 档，但带
 * `task_id` 过滤——面板的读数因此只属于这一条任务（`stores/run-panel.js` 的 `open()` 已经能接，
 * 本票只补入口与接线，不动它的合并判据）。
 */
async function openTaskRunPanel() {
  const task = thread.state.task;
  if (!task) return;
  dockTarget.value = { kind: 'run' };
  dockDrawerOpen.value = true;
  await dockPanel.open({ targetId: task.target_id, channelId: task.channel_id, taskId: task.id });
}

/**
 * 收掉 dock：把形态整个作废（下次打开是干净的一帧），并收起移动端抽屉。
 *
 * 一处落点判据（旧 `closeRunPanel()` 的 `fromTask` 分支）：**从任务上下文打开的**运行面板关掉后
 * 回任务线程，其余（频道面板 / 线程自己的关闭键）落回「没有形态」。判据读的是面板的 `taskId`
 * 与线程现在开着的那条任务，两者都缺一不可——不然「上一条任务的线程已经不在了」还会被拉回来。
 */
function closeDock() {
  const target = dockTarget.value;
  if (target && target.kind === 'run') {
    const backToTask = Boolean(dockPanel.state.taskId) && Boolean(thread.state.taskId);
    dockPanel.close();
    if (backToTask) {
      // 线程还开着：dock 不停在「没有形态」，抽屉也不收（人是在线程里开的这块面板）。
      dockTarget.value = { kind: 'task', taskId: thread.state.taskId };
      return;
    }
  } else if (target && target.kind === 'task') {
    // 关线程：把它的读数整个作废（不残留旧线程的消息与任务行）。
    thread.resetForTarget();
  }
  dockTarget.value = null;
  dockDrawerOpen.value = false;
}

/* ─── 主线视图：消息流 ⇄ 搜索（`mode`） ──────────────────────────────
   旧落点 `app.js` 的 `showSearchView()` / `showMessagesView()` / `setViewMode()`。

   搜索是**主线视图**：它占住消息流那块位置，于是频道头（含页签）与 composer 一起让位
   （旧 `syncChromeVisibility()`：`mode === 'search'` 时三者同进同出）。dock 不受影响——
   它是右侧常驻列，与主线视图是两个维度（旧 `setViewMode()` 从不碰 dock）。 */

function leaveAgentView() {
  if (mode.value !== 'agent') return;
  agentDetail.close();
  agentReturn = null;
}

/** 进搜索视图（rail 的 `btn-workspace-search`）。不清查询词：回到搜索时上一次的词还在。 */
function showSearchView() {
  leaveAgentView();
  mode.value = 'search';
  railOpen.value = false;
}

/** 回消息流：搜索 / 看板两个主线视图的退出口（`M` 键、页签、命令面板的「消息视图」都落这里）。 */
function showMessagesView() {
  // 离开搜索时把挂起的那一拍与在途响应一起作废：晚到的命中不该在人已经离开后改界面。
  if (mode.value === 'search') search.leave();
  leaveAgentView();
  mode.value = 'channel';
}

/**
 * 进任务看板（频道页签 / `T` 键 / 命令面板的「任务看板」三个入口的唯一落点）。
 *
 * 三件事，顺序与旧 `showTaskBoard()` 对齐：收掉 dock 的旧形态 → 切主线视图 → 读一次任务快照。
 * 进看板时默认筛到**当前频道**（人在 #甲 的任务里，就该先看 #甲 的任务）——看板本身仍是
 * 全工作区视图，「全部频道」chip 回到全部任务，不靠入口区分。私信下没有任务面（`tab-tasks`
 * 不渲染），因此这里也不必再判 target 类型。
 */
async function showTaskBoard() {
  if (!target.value || isDm.value) return;
  leaveAgentView();
  closeDock();
  mode.value = 'board';
  const channelId = state.activeTargetId;
  await board.openForChannel(channelId);
}

/** 页签的唯一落点（旧 `#tabMessages` / `#tabTasks` 两个监听器）：消息 ⇄ 任务。 */
function selectTab(tab) {
  if (tab === 'tasks') {
    showTaskBoard();
    return;
  }
  showMessagesView();
}

/** 任务页签计数：**当前频道**的任务数（与看板的频道筛选无关，读的是全量快照）。 */
const taskCount = computed(() => (isDm.value ? 0 : board.channelTaskCount(state.activeTargetId)));

/**
 * 待审批的读数（票 #308）：审批卡、页签圆点、卡片角标都从**这一份**镜像派生。
 *
 * `ctx.approvals` 是唯一写入口（快照 + live 帧）；这里只给三个读法：
 * - `approvalCountFor(targetId)`：某任务 target 的 pending 条数（卡片 / 列表行角标）；
 * - `channelApprovalTasks`：当前频道下「有任务在等审批」的**任务数**（页签圆点的 aria 名）；
 * - `taskApprovalCount(taskId)`：上面两档的桥接（看板卡片只持有任务行）。
 * 一份读数、三处呈现——不是三处各读一次。
 */
const approvals = ctx.approvals;
const approvalCountFor = (targetId) => approvals.pendingCountFor(targetId);
const channelApprovalTasks = computed(() =>
  isDm.value
    ? 0
    : channelPendingTaskCount(board.state.tasks, state.activeTargetId, approvalCountFor),
);
const taskApprovalCount = (taskId) => {
  const task = board.taskOf(taskId);
  return task && task.target_id ? approvalCountFor(task.target_id) : 0;
};

/** 消息流审批块的归属 target（票 #321）：频道 id；私信下为空——旧页面的判据就是
    `approvalTargetId = state.activeChannelId`，私信下它是 `null`，于是审批块不出现。
    它不是新开关：仍是同一个 `state.activeTargetId`，只是私信下没有频道归属。 */
const approvalStreamTarget = computed(() => (isDm.value ? '' : state.activeTargetId || ''));

/** 任务看板已迁到本版（票 #306）：`tab-tasks` 与看板入口因此翻正（ADR-0038 §五的开关）。 */
const tasksAvailable = ref(true);

/**
 * 点卡片 / 命令面板的入口 = 打开它的任务线程。
 *
 * 形态仍只由 `dockTarget` 决定（`{ kind: 'task', taskId }`），内容在 `stores/task-thread.js`。
 * 打开一条新线程会作废两样旧读数（旧 `openTask()` 同一条）：上一条线程的消息与**另一条任务**的
 * 运行面板——面板的 `task_id` 过滤属于上一条线程，留着它就会在新区里显示旧分派。
 *
 * 读失败**不留空壳**：dock 回落到「没有形态」并说清原因（旧 `openTask()` 的 catch 分支）。
 */
async function openTask(taskId) {
  if (!taskId) return;
  thread.resetForTarget();
  dockPanel.resetForTarget(true);
  dockTarget.value = { kind: 'task', taskId };
  dockDrawerOpen.value = true;
  const opened = await thread.open(taskId);
  if (opened) {
    // 打开线程即读一次**这个 target** 的待审批快照（旧 `loadApprovalsFor`）；失败不阻塞线程。
    approvals.loadFor(opened.task ? opened.task.target_id : null).catch(() => {});
    return;
  }
  // 打开期间人可能已经切走了：那时不动 dock（不要把别人的这一帧收掉）。
  if (!dockTarget.value || dockTarget.value.kind !== 'task' || dockTarget.value.taskId !== taskId) return;
  const reason = thread.state.error;
  closeDock();
  toasts.push(t('toast.openTaskFailed', { reason }), 'err');
}

/** dock 现在有没有可切换的形态：与频道头的运行面板入口同一份判据（私信下没有）。
    旧页面的是 `dockAvailable()`（含任务线程与成员形态）；本版的成员面走弹窗，因此这一位只决定
    「频道头的运行面板开关与命令面板里那条『切换详情面板』在不在」。 */
const dockToggleAvailable = computed(() => !isDm.value && Boolean(target.value));

/** 命令面板里的「切换详情面板」（旧 `toggleDock()`）：开着就关，关着就开运行面板。 */
function toggleDock() {
  if (!dockToggleAvailable.value) return;
  if (dockTarget.value && dockTarget.value.kind === 'run') closeDock();
  else openRunPanel();
}

/**
 * 命令表（旧 `commandList()`）
 *
 * 三组，顺序即呈现顺序：跳转（消息视图 / 任务看板）→ 动作（新建频道 / 新建任务 / 切换详情面板）
 * → **动态**频道组（当前工作台的频道，不是写死的种子名）。
 *
 * 三条纪律：
 * - 命令归属用**稳定 id**（`view-messages` / `new-channel` / `channel:<id>`…），不用标签：
 *   频道名可能与某条命令同名，标签不是键；
 * - **只有真绑定的快捷键**才印 `kbd`：`M` 消息视图、`T` 任务看板（原型里的 `N` / `⇧N` / `D` 没有
 *   绑定任何按键，因此不出现）；
 * - 指向**未迁面**的命令是禁用条目，不渲染成按下去什么都不发生的假入口。任务看板随票 #306 迁完、
 *   新建任务随票 #307 迁完 ⇒ `view-board` / `T` 键与 `new-task` 都是真入口。
 */
const commands = computed(() => {
  const list = [
    { id: 'view-messages', group: 'cmd.groupJump', label: t('cmd.messages'), kbd: 'M', run: showMessagesView },
    { id: 'view-board', group: 'cmd.groupJump', label: t('cmd.board'), kbd: 'T', run: showTaskBoard },
    { id: 'new-channel', group: 'cmd.groupAction', label: t('rail.newChannel'), run: () => { channelFormOpen.value = true; } },
    { id: 'new-task', group: 'cmd.groupAction', label: t('board.newTask'), run: () => { taskFormOpen.value = true; } },
  ];
  // 私信下 dock 没有可显示的形态：这条命令**不列**（与频道头入口同一份判据）。
  if (dockToggleAvailable.value) {
    list.push({ id: 'toggle-dock', group: 'cmd.groupAction', label: t('dock.toggle'), run: toggleDock });
  }
  state.channels.forEach((channel) => {
    list.push({
      id: 'channel:' + channel.id,
      group: 'cmd.groupChannels',
      label: `#${channel.name}`,
      run: () => selectTarget(channel.id),
    });
  });
  return list;
});

/** 跑一条命令：先关面板再动作（旧 `runCmd()` 同一条；面板不该留在下一步的界面上）。 */
function runCommand(commandId) {
  const command = commands.value.find((item) => item.id === commandId);
  if (!command || command.disabled) return;
  cmdOpen.value = false;
  command.run();
}

/** 私信下不出现的那几档：判据是**形态**，不是 `isDm` 一刀切。

    正本是旧 `dockFallbackMode()` / `dockAvailable()` 的注释：「成员」形态只属于频道；任务线程与
    运行面板是**独立形态**，私信下仍算可用。于是：
    - `task` / `run` ⇒ 一律可见（在私信里从搜索命中开的那条线程，同样要能看）；
    - `members` ⇒ 只在非私信里可见（v2 它至今没有调用点，保留以免形态判据不完整）；
    - `null` ⇒ 整栏不渲染（不占位，不是渲染出来再 `hidden`）。

    为什么不再写 `isDm ? null : dockTarget`：搜索入口在私信下是**可用**的（rail 的搜索键不按
    target 类型分身），而任务命中会开线程——私信里整栏隐去就变成一个点下去什么都不发生的按钮，
    那正是本票要除掉的东西。 */
const visibleDockTarget = computed(() => {
  const target = dockTarget.value;
  if (!target || !target.kind) return null;
  if (target.kind === 'members') return isDm.value ? null : target;
  return target;
});

/** ≤900px（单列壳）判据：右抽屉只在那一档是「盖在内容上的一层」。 */
const MOBILE_SHELL_QUERY = '(max-width: 900px)';

function mobileShell() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return Boolean(window.matchMedia(MOBILE_SHELL_QUERY).matches);
}

// 提醒 / 设置由 rail 头两枚入口打开（`RemindersModal` / `SettingsModal`）；Agent 详情 / 名册 /
// 配置 / 预算都还没迁到本版：对应的入口由各组件自己的「是否可用」开关关着（不渲染），
// 而不是渲染一个只弹 toast 的假按钮。
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
      :active-agent-id="activeAgentId"
      @select-target="selectTarget"
      @open-search="showSearchView"
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

    <main class="main" id="mainColumn" data-od-id="main-column" tabindex="-1">
      <div v-if="!state.online" class="conn-banner" id="connBanner" role="alert">
        <span>{{ t('conn.banner') }}</span>
        <button class="btn btn-sm" data-od-id="btn-reconnect" @click="ctx.start()">{{ t('conn.retry') }}</button>
      </div>
      <div v-if="state.online && state.streamState === 'reconnecting'" class="conn-banner" id="streamState" role="status">
        {{ t('conn.reconnecting') }}
      </div>

      <!-- 频道头在三档主线视图里都在，只有搜索让位（旧 `syncChromeVisibility()`：
           任务视图下 header 与页签本身就可见，收起它就是收起页签）。composer 只属于消息页
           （搜索 / 看板都不带输入框）。
           用 `hidden` 属性而不是 `v-if`：这几块对「在这一帧之前填到一半的东西」有意义
           （滚动位置、未发送的草稿），撕毁重建会让它们凭空丢。 -->
      <ChannelHeader
        v-if="target"
        :hidden="chromeHidden || undefined"
        :target="target"
        :is-dm="isDm"
        :is-archived-dm="archived"
        :message-count="messageTabCount(target)"
        :task-count="taskCount"
        :task-approval-count="channelApprovalTasks"
        :active-tab="activeTab"
        :channel-tools-available="true"
        :tasks-available="tasksAvailable"
        @select-tab="selectTab"
        @open-tools="openChannelTools"
        @open-run-panel="openRunPanel"
        @unarchive="unarchiveChannel"
      />

      <MessageStream
        v-if="messagesViewAvailable"
        ref="stream"
        :hidden="searchActive || undefined"
        :state="state"
        :messages="messages"
        :typing-names="ctx.typingNames.value"
        :task-of="board.taskOf"
        :pending-count-for="approvalCountFor"
        :approvals="approvals"
        :approval-target-id="approvalStreamTarget"
        :online="state.online"
        @retry="retryPending"
        @discard="discardPending"
        @open-task="openTask"
      />

      <!-- 任务看板（主线视图的第二档，票 #306）：与消息流共用同一块内容区。 -->
      <TaskBoard
        v-else-if="mode === 'board'"
        :board="board"
        :state="state"
        :approval-count="taskApprovalCount"
        @open-task="openTask"
        @new-task="taskFormOpen = true"
      />

      <!-- 搜索视图（主线视图的第三档）：同一块内容区，是「三选一」而不是叠层。
           任务命中行会抛 `open-task`：`SearchView` 只发意图，开线程的落点是 App 的 `openTask()`
           （与看板卡片、消息流里的 chip 同一条路径）。搜索留在主线视图，线程开在右侧 dock——
           两者是两个维度，不是二选一（旧 `openTask()` 不写 `state.mode`）。 -->
      <SearchView
        :search="search"
        :active="searchActive"
        :hidden="!searchActive || undefined"
        @open-task="openTask"
      />

      <!-- Agent 详情（主线视图的第四档，票 #310）：与消息流 / 看板 / 搜索共用同一块内容区，
           占住它时 dock 让位（`mode === 'agent'` 时 `closeDock()` 已经收掉形态）。 -->
      <AgentDetailView
        v-if="mode === 'agent'"
        :detail="agentDetail"
        :providers="state.providers"
        :local-providers="state.localProviders"
        :consents="state.consents"
        :data-faces="state.dataFaces"
        :acknowledge="acknowledgeRemote"
        :sending-message="agentDmInFlight"
        :online="state.online"
        @send-message="messageActiveAgent"
      />

      <ComposerBox
        v-if="messagesViewAvailable"
        :hidden="searchActive || undefined"
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

    <TaskFormModal
      v-model:open="taskFormOpen"
      :channels="state.channels"
      :default-channel-id="isDm ? '' : state.activeTargetId"
      :online="state.online"
      :submit="submitTask"
    />

    <DockPanel
      :dock-target="visibleDockTarget"
      :panel="dockPanel"
      :pools="dockPools"
      :open="dockDrawerOpen"
      :thread="thread"
      :thread-members="state.members"
      :thread-assignees="threadAssignees"
      :thread-assignees-failed="threadAssigneesFailed"
      :thread-channel-mode="threadChannelMode"
      :approvals="approvals"
      :online="state.online"
      :thread-task-of="board.taskOf"
      :pending-count-for="approvalCountFor"
      @close="closeDock"
      @open-run-panel="openTaskRunPanel"
      @open-task="openTask"
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

    <!-- ⌘K 命令面板：`.overlay` + `.cmd`（**不是** `ModalShell` 的 `.modal`），因此不进模态栈；
         ESC 归 `handleEscape()` 的第①层。 -->
    <CommandPalette
      v-model:open="cmdOpen"
      :commands="commands"
      :disabled-reason="t('app.notMigrated')"
      @run="runCommand"
    />

    <div class="toast-wrap" data-od-id="toast-wrap">
      <div v-for="item in toasts.items" :key="item.id" class="toast" :class="{ err: item.err }">
        <span class="dot"></span>{{ item.message }}
      </div>
    </div>

    <!-- 频道工具（#344）：一个宽弹窗 + 顶部页签，承载成员 / 设置 / 预算三张面；归档在设置页签的
         危险区里当场执行。dock 的 `members` 档是另一张票的面，与这个弹窗不互换。 -->
    <ChannelToolsModal
      :open="channelToolsOpen"
      :channel="toolChannel"
      :store="store"
      :toast="toasts.push"
      :remove="deleteChannel"
      :archive="archiveChannel"
      @close="closeChannelTools"
    />
  </div>
</template>
