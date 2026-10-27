/* 应用装配根：把「一个 store + 一条事件流 + 一份设置」接到 Vue 上

   这一层只做旧 `app.js` 末尾那几行的对应物（`applyStaticI18n` / `bindEvents` / `loadWorkspace`），
   以及 Vue 版特有的一件新事：**设置铺到根元素**（`<html lang>` / `data-theme` / `data-density`）。
   旧实现靠 `applyStaticI18n(document)` 整页重铺，Vue 版靠词条桥的响应式依赖——两条路径读同一张表。

   纪律不变：业务状态的唯一正本是后端；这里不缓存、不排队、不伪造。 */
import { computed, getCurrentInstance, onBeforeUnmount, reactive } from 'vue';
import { createWorkspaceStore } from './stores/workspace.js';
import { createRunPanelStore } from './stores/run-panel.js';
import { createReminderStore } from './stores/reminders.js';
import { createEventStream, isLiveEventType } from './lib/event-stream.js';
import { createLiveEventLog } from './lib/live-events.js';
import { setLocale, sourceLocale, locales as i18nLocales, t } from './i18n/index.js';

const THEMES = ['light', 'dark', 'system'];
const DEFAULT_THEME = 'system';

/** toast 队列（呈现状态，不是业务状态）：每条 3.2 秒后自动消失。 */
export function useToasts() {
  const items = reactive([]);
  let nextId = 0;

  function push(message, kind) {
    const id = ++nextId;
    items.push({ id, message: String(message), err: kind === 'err' });
    setTimeout(() => {
      const index = items.findIndex((item) => item.id === id);
      if (index >= 0) items.splice(index, 1);
    }, 3200);
    return id;
  }

  return { items, push };
}

/**
 * 应用级接线：store + 事件流 + 设置。
 *
 * `options.toast` 是 toast 落点（由 App.vue 传进来）；`options.sourceFactory` 是测试替身入口。
 */
export function createAppContext(options) {
  const config = options || {};
  const toast = config.toast || (() => {});

  const store = createWorkspaceStore({ toast, now: config.now });
  const { state, mutable } = store;

  /** live-only 事件的会话内日志（按 target 有界保留）：过程面（运行面板 / 后续的审批卡）的数据源。 */
  const liveLog = createLiveEventLog();

  /** 运行面板（dock 的第三种形态）：读数 + 呈现状态，合并判据全在 `lib/run-panel.js`。 */
  const runPanel = createRunPanelStore({
    liveLog,
    channelConfigs: mutable.channelConfigs,
    loadChannelConfig: store.loadChannelConfig,
    // 任务线程与频道名单都还没迁（/ 或只在 store 里）：标题取不到时面板退到 id 尾段，不编名字。
    activeTask: config.activeTask || (() => null),
    channels: () => mutable.channels,
    activeChannel: () => store.activeChannel.value,
  });

  /** 提醒的本地镜像（正本在后端 `/api/reminders`）：圆点角标与提醒弹窗都读它。

      读取**不阻塞主链路**：读不到不影响工作台（旧 `loadWorkspace()` 里的
      `loadReminders().catch(() => {})` 同一条），打开弹窗时会再试一次并在弹窗里说明理由。 */
  const reminders = createReminderStore();

  /** 设置落到根元素：三个档位各一个落点，与旧 `applySettings()` 同一份判据。

      **它是唯一的写入口**：读回（启动）与写回（设置弹窗）都调它，它自己负责把 `mutable.settings`
      写成规范化后的取值。刻意**不**挂 `watch(() => mutable.settings, applySettings)`——那会让
      回调里的赋值再触发自己，主线程直接转死（这一版踩过：页面白屏 + `page.evaluate` 超时）。 */
  function applySettings(settings) {
    const next = settings || {};
    const locale = i18nLocales().includes(next.locale) ? next.locale : sourceLocale();
    setLocale(locale);
    mutable.settings = {
      locale,
      message_density: next.message_density === 'compact' ? 'compact' : 'comfortable',
      theme: THEMES.includes(next.theme) ? next.theme : DEFAULT_THEME,
    };
    if (typeof document !== 'undefined' && document.documentElement) {
      document.documentElement.dataset.density = mutable.settings.message_density;
      // 主题三档都写：显式 light / dark 由 `:root[data-theme='dark']` 命中，
      // `system` 交给设计系统包里的 `@media (prefers-color-scheme: dark)`——这里不解析媒体查询。
      document.documentElement.dataset.theme = mutable.settings.theme;
    }
    return mutable.settings;
  }

  // ─── 事件流的分流（与旧 `handleStreamEvent` 同一套判据） ───────────────────
  function handleFrame(payload, type) {
    const targetId = payload.target_id;
    if (isLiveEventType(type)) {
      // live-only 运行事件按 target 有界保留：运行面板从这里取，与快照按后端 id 合并。
      // 它们**绝不**进消息流 / 未读 / 搜索，也不写游标（`seenSeqs` 是标读与重连补拉的判据，
      // live 帧没有 seq，错记一次就会让全量重取的兜底少一条）。
      liveLog.record(targetId, payload);
      if (type === 'sop_event') runPanel.noteSopFrame(payload);
      // 本票只记录 typing 与轮次起止；审批卡那一层随它自己的票迁过来。
      if (type === 'agent_event' && payload.reply_id) {
        const rounds = mutable.activeRounds[targetId] || (mutable.activeRounds[targetId] = {});
        if (payload.event_type === 'REPLY_START') {
          rounds[payload.reply_id] = payload.member_id;
          mutable.typing[targetId] = payload.member_id;
        } else if (payload.event_type === 'REPLY_END') {
          delete rounds[payload.reply_id];
          if (!Object.keys(rounds).length) {
            delete mutable.activeRounds[targetId];
            delete mutable.typing[targetId];
          }
        }
      } else if (type === 'round.interrupted') {
        const rounds = mutable.activeRounds[targetId] || {};
        const ids = payload.reply_ids || (payload.reply_id ? [payload.reply_id] : []);
        ids.forEach((replyId) => {
          delete rounds[replyId];
        });
        if (!Object.keys(rounds).length) {
          delete mutable.activeRounds[targetId];
          delete mutable.typing[targetId];
        }
      } else if (type === 'round.active') {
        mutable.activeRounds[targetId] = payload.rounds || {};
        const first = Object.values(mutable.activeRounds[targetId])[0];
        if (first) mutable.typing[targetId] = first;
        else delete mutable.typing[targetId];
      }
      return;
    }
    if (type === 'message') {
      if (!payload.message) return;
      const added = store.absorbMessage(targetId, payload.message);
      if (!added) return;
      // 其它 target 的未读徽标也要跟着动：从后端重新取计数（未读的正本在库）。
      if (mutable.dms.some((dm) => dm.id === targetId)) refreshDmList();
      else refreshChannelList();
      if (state.activeTargetId === targetId) {
        store.markCurrentRead();
        if (config.onNewMessage) config.onNewMessage();
      }
      return;
    }
    const seq = Number(payload.seq) || 0;
    if (seq) store.seenSeqsFor(targetId).add(seq);
    if (type === 'member_status') {
      mutable.statuses[payload.member_id] = payload.status;
      mutable.agents = mutable.agents.map((agent) =>
        agent.id === payload.member_id ? { ...agent, status: payload.status } : agent,
      );
      if (payload.status !== 'working' && mutable.typing[targetId] === payload.member_id) {
        delete mutable.typing[targetId];
      }
    } else if (type === 'typing') {
      if (payload.active) mutable.typing[targetId] = payload.member_id;
      else if (mutable.typing[targetId] === payload.member_id) delete mutable.typing[targetId];
    } else if (type === 'round_outcome') {
      const outcome = payload.outcome;
      if (!outcome) return;
      if (outcome.status === 'failed') mutable.statuses[outcome.member_id] = 'error';
      if (state.activeTargetId === targetId && Number.isInteger(outcome.base_seq)) {
        const byMember = mutable.outcomes[outcome.base_seq] || (mutable.outcomes[outcome.base_seq] = {});
        const records = byMember[outcome.member_id] || (byMember[outcome.member_id] = []);
        if (!records.some((record) => record.id === outcome.id)) records.push(outcome);
        if (Number.isInteger(outcome.reply_seq)) mutable.replyOutcomes[outcome.reply_seq] = outcome;
      }
    }
  }

  async function refreshChannelList() {
    try {
      const payload = await apiGet('/api/channels');
      if (!payload || !Array.isArray(payload.channels)) return;
      mutable.channels = payload.channels;
    } catch {
      // 保留上一次已知的计数：宁可略陈旧，也不伪造。
    }
  }

  async function refreshDmList() {
    try {
      const payload = await apiGet('/api/dms');
      if (!payload || !Array.isArray(payload.dms)) return;
      mutable.dms = payload.dms;
    } catch {
      // 同上。
    }
  }

  const stream = createEventStream({
    onFrame: handleFrame,
    onState(streamState) {
      mutable.streamState = streamState;
    },
    onOpen(info) {
      mutable.online = true;
      // 全局流**不补拉**（它没有单一 target 的游标）：断线期间的变化只能靠重连后的一次全量重取
      // 补齐。这条在旧实现里是 `connectStream()` 的 `onopen`，漏了它 = 重连后界面上一直是旧读数
      // （而且它不会报错，只会「看起来还在工作」）。
      if (info && info.reconnected) {
        store.loadWorkspace({ silent: true, keepActive: true }).then(() => {
          store.markCurrentRead();
        });
        // 提醒也随重连后的这次全量重取刷新：断线期间别的浏览器或投递器改了提醒表，
        // 圆点是它的投影，不刷新就会一直亮着一个已经不成立的点（旧页面同一处也在
        // `loadWorkspace()` 里重读一次）。读不到保留旧镜像，不伪造。
        reminders.load().catch(() => {});
      }
      // 运行面板的 live 帧同样不补（ADR-0020）：重连成功后按快照兜底，把断线期间的分派 / Plan /
      // SOP 变化读回来。面板没开时是空操作（`refreshFor()` 自己判）。
      if (info && info.reconnected) runPanel.refreshFor();
    },
    onError() {
      mutable.online = false;
      // 断开期间不可能有 in-flight 事实需要显示：清掉「正在回复」，等重连后的快照说实话。
      mutable.activeRounds = {};
      mutable.typing = {};
    },
    sourceFactory: config.sourceFactory,
    retryMs: config.retryMs,
  });

  /** 启动：读设置 → 全量快照 → 开流 → 标读。

      返回 `{ ok, firstSuccessfulLoad }`：后者是「这是第一次真正拿到消息」——调用方据此决定
      要不要把消息流贴到最新一条。断线重连与静默刷新都**不是**首次成功载入，因此不会把正在
      回看历史的人拉回底部。 */
  async function start() {
    // 设置由 `loadWorkspace()` 自己在最前面读（它要保证首帧就是用户选的那一档），因此这里
    // **不再**单独读一次——两次读同一个端点只是多一个请求，读到的还是同一份正本。
    const result = await store.loadWorkspace();
    // 读回后按同一份判据铺一次：于是「设置读到了、但界面没铺」这个中间态不存在。
    applySettings(mutable.settings);
    // 提醒的读取不阻塞主线（旧页面同一条）：读不到不影响工作台，圆点保持未亮，
    // 打开弹窗时会再试一次并把失败话留在弹窗里。
    reminders.load().catch(() => {});
    stream.start();
    store.markCurrentRead();
    return { ok: result.ok, firstSuccessfulLoad: result.firstSuccessfulLoad };
  }

  // 只有在组件作用域里创建时才挂卸载钩子：用例与 `main.js` 之外的地方也会建这个上下文
  // （那不属于任何组件实例，挂 `onBeforeUnmount` 会触发一次无害但吵的 Vue 警告）。
  if (getCurrentInstance()) onBeforeUnmount(() => stream.stop());

  const typingNames = computed(() => {
    const targetId = state.activeTargetId;
    const rounds = targetId ? state.activeRounds[targetId] || {} : {};
    const ids = [];
    Object.values(rounds).forEach((memberId) => {
      if (memberId && !ids.includes(memberId)) ids.push(memberId);
    });
    const typingId = targetId ? state.typing[targetId] : null;
    if (!ids.length && typingId) ids.push(typingId);
    return ids.map((memberId) => {
      const member = state.members.find((item) => item.id === memberId);
      return { id: memberId, name: member ? member.display_name : memberId };
    });
  });

  return {
    store,
    state,
    mutable,
    stream,
    start,
    applySettings,
    typingNames,
    liveLog,
    runPanel,
    reminders,
    t,
  };
}

/** 小工具：两个列表刷新用的 GET（不走 store 的读路径，因为它们只改列表镜像）。 */
async function apiGet(path) {
  const response = await fetch(path, { method: 'GET' });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}
