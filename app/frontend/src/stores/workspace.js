/* 工作台状态（业务状态的唯一正本是后端）

   这一层是旧 `studio/app.js` 里 `state` 与读路径（`loadWorkspace` / `refreshMessages` /
   `refreshOutcomes` / `refreshStatuses` / 标读）的 Vue 版对应物。它**只做三件事**：
   发请求、把后端读数落进响应式镜像、把「已见 seq」这类会话内簿记维护好。

   纪律（沿用旧实现的每一条）：
   - 不用任何浏览器存储保存业务状态（唯一允许的本地记忆是「上次打开的频道 id」）；
   - 后端不可达时不排队、不伪造，只把 `online` 置假并交给调用方提示；
   - `seenSeqs` 是「我已经看到哪一条」的游标，与后端 `last_seq` 一起决定标读上界。

   与旧实现的**一处刻意差异**：这里不再有「整页 render()」。响应式镜像变了，用到它的组件自己
   重渲染——于是「谁顺带重渲了谁」这类耦合不存在，也不需要在每次写后手动重铺。 */
import { computed, reactive, readonly } from 'vue';
import { api, channelPath, dmPath, targetPath } from '../lib/api.js';
import { uploadAttachment } from '../lib/attachments.js';

const OWNER_FALLBACK = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };

/** 上次打开的 target id 的本地记忆（**唯一**允许写浏览器存储的东西，见 CONTEXT.md）。 */
const REMEMBER_KEY = 'alibot.activeChannel';

function readRememberedTargetId() {
  try {
    return localStorage.getItem(REMEMBER_KEY);
  } catch {
    return null;
  }
}

function rememberTargetId(targetId) {
  try {
    if (targetId) localStorage.setItem(REMEMBER_KEY, targetId);
    else localStorage.removeItem(REMEMBER_KEY);
  } catch {
    // 隐私模式下 localStorage 不可用：忽略，业务状态本来也不在这里。
  }
}

/** 工作台镜像。字段名与后端载荷同名（`member` / `channels` / `dms` / `messages` …），
    因此读代码时不需要在两套命名之间做映射。 */
export function createWorkspaceStore(options) {
  const config = options || {};
  // 测试替身可以注入一个假时钟（标读的防抖与重连都读它）。
  const now = config.now || (() => Date.now());
  const toast = config.toast || (() => {});

  const state = reactive({
    ready: false,
    online: true,
    streamState: 'connecting',
    member: OWNER_FALLBACK,
    members: [],
    agents: [],
    channels: [],
    dms: [],
    providers: [],
    localProviders: [],
    consents: [],
    // 远端数据面（`GET /api/remote-data-flow` 的 `data_faces`）：只有「会离开本机的数据面」
    // 这一个读数，用来拼新建 Agent 弹窗的告知正文。空数组 = 没读到（读不到就保留上一次已知值）。
    dataFaces: [],
    statuses: {},
    settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' },
    activeTargetId: null,
    mode: 'channel',
    messages: [],
    pending: [],
    outcomes: {},
    replyOutcomes: {},
    typing: {},
    activeRounds: {},
    channelMembers: {},
    channelMembersFailed: [],
    // composer 已上传成草稿、等着随下一条消息提交的附件（`lib/attachments.js`）。
    // 正本在后端：这里只是“手上这批”的镜像，发送成功就清空，失败留着可重试。
    composerAttachments: [],
    // 还在飞的上传个数：> 0 时不能发送（否则这条消息会漏掉还没拿到标识的文件）。
    attachingCount: 0,
    // 频道配置的**只读缓存**（`GET /api/channels/{id}/config`）。正本在后端；运行面板的
    // 「自主模式」空态解释只读它，读不到就是未缓存 ⇒ 铺通用空态，**不**推算模式。
    channelConfigs: {},
  });

  // ─── 派生读数（都是后端事实的投影，不做本地推断） ────────────────────────
  const activeChannel = computed(
    () => state.channels.find((channel) => channel.id === state.activeTargetId) || null,
  );
  const activeDm = computed(() => state.dms.find((dm) => dm.id === state.activeTargetId) || null);
  const activeTarget = computed(() => activeChannel.value || activeDm.value || null);
  const isDm = computed(() => Boolean(activeDm.value));
  const isArchivedDm = computed(
    () => Boolean(activeDm.value && activeDm.value.peer && activeDm.value.peer.deleted_at),
  );

  /** 已见 seq 的游标（不进响应式：它只在标读时读，变了也不该触发任何重渲染）。 */
  const seenSeqs = new Map();

  function seenSeqsFor(targetId) {
    if (!seenSeqs.has(targetId)) seenSeqs.set(targetId, new Set());
    return seenSeqs.get(targetId);
  }

  /** 「看到哪一条」的唯一表达式：后端 `last_seq` 与本地已见集合的较大值。 */
  function lastSeqOf(targetId) {
    const target = state.channels.find((item) => item.id === targetId)
      || state.dms.find((item) => item.id === targetId);
    const known = seenSeqsFor(targetId);
    let max = target ? Number(target.last_seq) || 0 : 0;
    known.forEach((seq) => {
      if (seq > max) max = seq;
    });
    return max;
  }

  function replaceChannel(channel) {
    if (!channel) return;
    const index = state.channels.findIndex((item) => item.id === channel.id);
    if (index >= 0) state.channels[index] = channel;
    else state.channels.push(channel);
  }

  function replaceDm(dm) {
    if (!dm) return;
    const index = state.dms.findIndex((item) => item.id === dm.id);
    if (index >= 0) state.dms[index] = dm;
    else state.dms.push(dm);
  }

  function replaceTarget(target) {
    if (!target) return;
    if (target.kind === 'dm') replaceDm(target);
    else replaceChannel(target);
  }

  function setActiveTargetId(targetId) {
    state.activeTargetId = targetId;
    rememberTargetId(targetId);
  }

  function targetPathOf(targetId, suffix) {
    const dm = state.dms.some((item) => item.id === targetId);
    return targetPath(targetId, dm, suffix);
  }

  // ─── 读路径 ─────────────────────────────────────────────────────────────

  /** 设置（语言偏好 / 消息密度 / 主题）：正本是后端，前端只镜像。 */
  async function loadSettings() {
    const payload = await api('/api/settings');
    return payload.settings;
  }

  /** 当前 target 的消息流。`replaceTarget` 用的是响应里回带的频道 / 私信行（含最新未读读数）。 */
  async function refreshMessages() {
    const target = activeTarget.value;
    if (!target) {
      state.messages = [];
      return [];
    }
    const payload = await api(targetPathOf(target.id, '/messages'));
    state.messages = payload.messages || [];
    replaceTarget(payload.dm || payload.channel);
    const known = seenSeqsFor(target.id);
    known.clear();
    state.messages.forEach((message) => known.add(message.seq));
    return state.messages;
  }

  /** 轮次结果的两份索引（同一批后端行建、同一处换，免得两份各自漂）。 */
  function applyOutcomes(records) {
    const byBase = {};
    const byReply = {};
    (records || []).forEach((record) => {
      if (!record) return;
      if (Number.isInteger(record.base_seq)) {
        if (!byBase[record.base_seq]) byBase[record.base_seq] = {};
        const byMember = byBase[record.base_seq];
        if (!byMember[record.member_id]) byMember[record.member_id] = [];
        byMember[record.member_id].push(record);
      }
      if (Number.isInteger(record.reply_seq)) byReply[record.reply_seq] = record;
    });
    state.outcomes = byBase;
    state.replyOutcomes = byReply;
  }

  /** 轮次结果只读快照。读不到不影响消息流：保留上一次已知值，也不把整个工作台判成不可达。 */
  async function refreshOutcomes() {
    const channel = activeChannel.value;
    if (!channel) {
      applyOutcomes([]);
      return;
    }
    try {
      const payload = await api(channelPath(channel.id, '/round-outcomes'));
      applyOutcomes(payload.round_outcomes || []);
    } catch {
      // 保留上一次已知值（与 `refreshStatuses` 同一条纪律）。
    }
  }

  /** 四态状态点：读不到就保持上一次已知值，不伪造。 */
  async function refreshStatuses() {
    try {
      const payload = await api('/api/member-statuses');
      state.statuses = payload.statuses || {};
      state.agents = state.agents.map((agent) => ({
        ...agent,
        status: state.statuses[agent.id] || agent.status,
      }));
    } catch {
      // 同上：宁可略陈旧，也不编造。
    }
  }

  /**
   * 远端数据面（新建 Agent 弹窗的告知正文要用它拼一句话）。
   *
   * **读不到就保留上一次已知值**：告知正文里列举的是「哪些数据会离开本机」，编不出来也不该编，
   * 于是调用方拿到空数组时必须如实说「读不到」，而不是渲染一句少了数据面的半真文案
   * （与 `refreshStatuses` / `refreshOutcomes` 同一条纪律）。
   */
  async function loadRemoteFlow() {
    try {
      const payload = await api('/api/remote-data-flow');
      if (Array.isArray(payload.data_faces)) state.dataFaces = payload.data_faces;
      return payload;
    } catch {
      return null;
    }
  }

  /** 当前频道的成员表（`@` 补全与成员面板共用同一个来源）。 */
  async function refreshChannelMembers(channelId) {
    const id = channelId || state.activeTargetId;
    const channel = state.channels.find((item) => item.id === id);
    if (!channel) return null;
    try {
      const payload = await api(channelPath(id, '/members'));
      state.channelMembers[id] = payload.members || [];
      state.channelMembersFailed = state.channelMembersFailed.filter((item) => item !== id);
    } catch {
      // 读不到就如实记下：不静默清空已读到的候选，也不伪造成员。
      if (!state.channelMembers[id]) state.channelMembers[id] = [];
      if (!state.channelMembersFailed.includes(id)) state.channelMembersFailed.push(id);
    }
    return state.channelMembers[id];
  }

  /** 当前频道的 Agent 成员（`null` = 还没读到；空数组才是真的没有成员）。 */
  function channelAgentMembers(channelId) {
    const members = state.channelMembers[channelId || state.activeTargetId];
    return members ? members.filter((member) => member.kind === 'agent') : null;
  }

  /**
   * 把一个 Agent 加入频道（名册弹窗里那个**唯一**的写动作）。
   *
   * **用响应里的最新成员表覆盖缓存**，不是「先加再读」：后者中间存在一个「读回旧表」的窗口，
   * 而那正是名册刚把人挪过去、`@` 补全却还选不到它的那一帧。加入成功也清掉该频道的
   * `channelMembersFailed` 标记（一次成功的写就证明成员表现已可读）。
   *
   * 幂等由后端保证（`joined` 区分新增与本来就在）；这里只负责把响应里的两份读数落地。
   */
  async function addChannelMember(channelId, memberId) {
    const payload = await api(channelPath(channelId, '/members'), {
      method: 'POST',
      body: { member_id: memberId },
    });
    if (Array.isArray(payload.members)) state.channelMembers[channelId] = payload.members;
    state.channelMembersFailed = state.channelMembersFailed.filter((item) => item !== channelId);
    if (payload.channel) replaceChannel(payload.channel);
    return payload;
  }

  /**
   * 一个成员的能力计数（名册行右侧的摘要）：两条 agent scope 清单并行读。
   *
   * 任一读不到只让**这一行**标成不可用（`failed`），不外溢：名册的价值是「有谁」，
   * 一行读不到不该把整份名单变成读不到。
   */
  async function readMemberCapabilityCounts(memberId) {
    try {
      const [mcp, skills] = await Promise.all([
        api('/api/mcp-servers?scope_kind=agent&scope_id=' + encodeURIComponent(memberId)),
        api('/api/skills?scope_kind=agent&scope_id=' + encodeURIComponent(memberId)),
      ]);
      return {
        mcp: (mcp && Array.isArray(mcp.mcp_servers) ? mcp.mcp_servers : []).length,
        skills: (skills && Array.isArray(skills.skills) ? skills.skills : []).length,
        failed: false,
      };
    } catch {
      return { mcp: 0, skills: 0, failed: true };
    }
  }

  /**
   * 读一次频道配置并缓存（正本在后端）。
   *
   * **读不到就保持未缓存**，返回 `null`：运行面板的「自主模式」空态解释据此决定铺哪一句，
   * 因此「配置读不到」绝不能被当成 `autonomous`（那会把一次网络抖动说成频道是自主模式）。
   * 同一频道的在飞读取复用同一个 promise（旧实现 `channelConfigsInFlight` 同一条）。
   */
  const channelConfigInFlight = new Map();

  /**
   * 读一次频道配置并**覆盖缓存**（频道设置弹窗每次打开都要读最新的那一份）。
   *
   * 与 `loadChannelConfig` 的分工：那个是「有缓存就复用」的只读快照（运行面板的解释用它），
   * 这个是**显式动作**的读数——打开设置面必须看到后端现在那一份，不能拿缓存当结论。
   * 读回后同样写进 `state.channelConfigs`，运行面板的协作模式解释因此跟着更新。
   */
  async function refreshChannelConfig(channelId) {
    const payload = await api(channelPath(channelId, '/config'));
    state.channelConfigs[channelId] = payload.config;
    return payload.config;
  }

  /** 保存频道配置（部分更新；后端任一字段不合法就整体拒绝），按响应里的新配置覆盖缓存。 */
  async function saveChannelConfig(channelId, changes) {
    const payload = await api(channelPath(channelId, '/config'), { method: 'PATCH', body: changes });
    state.channelConfigs[channelId] = payload.config;
    return payload.config;
  }

  /** 读一次该频道的预算读数（**不缓存**：用量随人类世代变化，缓存下来的只能是过期事实）。 */
  async function readChannelBudget(channelId) {
    const payload = await api(channelPath(channelId, '/budget'));
    return payload.budget;
  }

  /** 保存预算上界（只提交 `budget_limit`；用量是后端的正本，前端既不算也不提交）。 */
  async function saveChannelBudgetLimit(channelId, budgetLimit) {
    const payload = await api(channelPath(channelId, '/budget'), {
      method: 'PATCH',
      body: { budget_limit: budgetLimit },
    });
    return payload.budget;
  }

  async function loadChannelConfig(channelId) {
    if (state.channelConfigs[channelId]) return state.channelConfigs[channelId];
    const inFlight = channelConfigInFlight.get(channelId);
    if (inFlight) return inFlight;
    const read = (async () => {
      try {
        const payload = await api(channelPath(channelId, '/config'));
        state.channelConfigs[channelId] = payload.config;
        return payload.config;
      } catch {
        // 保持未缓存：任务线程与运行面板都不伪造 team / autonomous 结论，下次再试。
        return null;
      } finally {
        channelConfigInFlight.delete(channelId);
      }
    })();
    channelConfigInFlight.set(channelId, read);
    return read;
  }

  /**
   * 工作台是否已经**成功**载入过一轮。
   *
   * 不能用 `state.ready`：它在失败分支也会置真，而「首载失败 → 重连成功」的那一次仍属于
   * 「首次成功载入」，必须贴底（否则首载失败的用户会停在消息流顶部）。
   */
  let workspaceLoaded = false;

  /**
   * 全量快照。
   *
   * 返回的 `firstSuccessfulLoad` 是**这一次**是否为「第一次真正拿到消息」：只有整轮读数全部
   * 落地才算（中途任何一步抛错都会走 catch，重连那一次才补得上贴底）。调用方据此决定要不要
   * 把消息流滚到最新一条——断线重连与静默刷新**不是**首次载入，不该把回看历史的人拉回底部。
   */
  async function loadWorkspace(loadOptions) {
    const settings = loadOptions || {};
    const firstSuccessfulLoad = !workspaceLoaded;
    // 「这一次尝试成功了吗」与「有没有成功过」是两件事：前者决定返回值，后者决定贴底口径。
    let attemptOk = false;
    try {
      try {
        state.settings = await loadSettings();
      } catch {
        // 设置读不到不影响工作台：保留当前镜像，照常渲染主流程。
      }
      const snapshot = await api('/api/workspace');
      state.member = snapshot.member;
      state.members = snapshot.members;
      state.channels = snapshot.channels;
      state.dms = snapshot.dms || [];
      state.agents = snapshot.agents || [];
      state.providers = snapshot.model_providers || [];
      state.localProviders = snapshot.local_providers || [];
      state.consents = snapshot.remote_consents || [];
      state.online = true;
      state.ready = true;

      const known = state.channels.some((channel) => channel.id === state.activeTargetId)
        || state.dms.some((dm) => dm.id === state.activeTargetId);
      if (!known) {
        if (settings.keepActive === false) {
          setActiveTargetId(null);
        } else {
          const remembered = readRememberedTargetId();
          const target = state.channels.find((channel) => channel.id === remembered)
            || state.dms.find((dm) => dm.id === remembered);
          setActiveTargetId(target ? target.id : null);
        }
      }
      // 远端数据面不阻塞主线（与旧实现 `loadRemoteFlow()` 同一条）：读不到只影响新建 Agent
      // 弹窗里那一句告知，不影响工作台本身；弹窗打开时还会再试一次。
      loadRemoteFlow();
      await refreshMessages();
      await refreshOutcomes();
      await refreshChannelMembers();
      await refreshStatuses();
      state.ready = true;
      // 整轮读数都落地了才算「成功载入」：上面任何一步抛错都不会走到这里。
      workspaceLoaded = true;
      attemptOk = true;
    } catch (err) {
      state.online = false;
      state.ready = true;
      applyOutcomes([]);
      if (!settings.silent) toast(err.message, 'err');
    }
    return { ok: attemptOk, firstSuccessfulLoad: firstSuccessfulLoad && attemptOk };
  }

  // ─── 标读（切入 target、以及正在查看时到达的新消息都立即已读） ─────────────

  /** 每个 target 各持一个待发标读定时器：离开 A 去 B 时，A 的标读不能被 B 的覆盖掉。 */
  const readTimers = new Map();

  function markRead(targetId, seq) {
    const existing = readTimers.get(targetId);
    if (existing) clearTimeout(existing);
    readTimers.set(
      targetId,
      setTimeout(async () => {
        readTimers.delete(targetId);
        try {
          const payload = await api(targetPathOf(targetId, '/read'), { method: 'POST', body: { seq } });
          if (payload.dm) {
            state.dms = payload.dms || state.dms;
            replaceDm(payload.dm);
          } else {
            replaceChannel(payload.channel);
            if (payload.channels) state.channels = payload.channels;
          }
        } catch (err) {
          toast(err.message, 'err');
        }
      }, 120),
    );
  }

  /** 标读当前 target：上界 = `last_seq` 与「流里已渲染的最大 seq」的较大值。 */
  function markCurrentRead() {
    const target = activeTarget.value;
    if (!target) return;
    const maxSeq = Math.max(
      lastSeqOf(target.id),
      ...state.messages.filter((message) => message.seq).map((message) => message.seq),
      0,
    );
    if (maxSeq <= target.last_read_seq) return;
    markRead(target.id, maxSeq);
  }

  // ─── 写路径：发送（乐观 pending → 确认 seq 或显式失败） ───────────────────

  function renderedMessages() {
    const targetId = state.activeTargetId;
    if (!targetId) return [];
    // 只渲染当前 target 的 pending：失败态不得串到别的 target。
    const pending = state.pending.filter((item) => item.target_id === targetId);
    return state.messages.concat(pending);
  }

  /** 一条消息是否已经收过（按 seq 认一条；事件流与响应可能同时带来同一条）。 */
  function absorbMessage(targetId, message) {
    if (!message || !Number.isInteger(message.seq)) return false;
    const known = seenSeqsFor(targetId);
    if (known.has(message.seq)) return false;
    known.add(message.seq);
    const target = state.channels.find((item) => item.id === targetId)
      || state.dms.find((item) => item.id === targetId);
    if (target) target.last_seq = Math.max(Number(target.last_seq) || 0, message.seq);
    if (state.activeTargetId === targetId) {
      state.messages.push(message);
      state.messages.sort((a, b) => a.seq - b.seq);
    }
    return true;
  }

  function removePending(clientId) {
    state.pending = state.pending.filter((item) => item.clientId !== clientId);
  }

  function failPending(clientId, reason) {
    const pending = state.pending.find((item) => item.clientId === clientId);
    if (!pending) return;
    pending.status = 'failed';
    pending.reason = reason;
  }

  function discardPending(clientId) {
    removePending(clientId);
  }

  /** 真正投递一条 pending 项：成功则移除并吸收回带的消息，失败则就地标记（不重排队）。 */
  async function deliver(pending) {
    try {
      const payload = await api(targetPathOf(pending.target_id, '/messages'), {
        method: 'POST',
        body: { body: pending.body, attachment_ids: pending.attachment_ids || [] },
      });
      removePending(pending.clientId);
      // 消息本身由事件流按 seq 追加（同一帧可能已经到达），这里只保证它一定在。
      absorbMessage(pending.target_id, payload.message);
      state.online = true;
      markCurrentRead();
      return { ok: true, message: payload.message };
    } catch (err) {
      failPending(pending.clientId, err.message);
      // `status` 只在后端答复里才有：没有它就是「连不上后端」，与「后端拒绝」不是同一件事。
      if (!err.status) state.online = false;
      return { ok: false, error: err };
    }
  }

  /** 发送一条消息。返回 `{ok, message}`；失败时 pending 项留在流里（带失败原因与重试入口）。 */
  async function sendMessage(body, attachmentIds, composerAttachments) {
    const target = activeTarget.value;
    const text = String(body == null ? '' : body).trim();
    if (!text || !target) return { ok: false };
    if (isArchivedDm.value) return { ok: false, archived: true };
    if (!state.online) return { ok: false, offline: true };

    // 附件随这条 pending 一起走：
    // - 请求里的 `attachment_ids` 是**既有 JSON 字段**（`MessageCreate.attachment_ids`），
    //   后端在同一事务里完成绑定（ADR-0014）；
    // - pending 带上完整元数据，于是乐观帧里显示的仍是**草稿**（不是可点的链接）——
    //   未绑定的上传取内容端点回 404，渲染成链接是骗人的；
    // - 失败时同一批标识留在 composer（重试会带上它）。
    const drafts = Array.isArray(composerAttachments)
      ? composerAttachments
      : state.composerAttachments;
    const pending = {
      clientId: 'pending-' + now() + '-' + Math.random().toString(36).slice(2, 7),
      target_id: target.id,
      body: text,
      status: 'pending',
      created_at: new Date(now()).toISOString(),
      author: state.member,
      attachments: drafts,
      attachment_ids: attachmentIds || [],
    };
    state.pending.push(pending);
    return deliver(pending);
  }

  // ─── 写路径：新建对象（频道 / Agent / 私信） ──────────────────────────────

  /**
   * 新建频道。`POST /api/channels` 回带**整份**频道名单，因此这里不手工拼接列表——
   * 列表的正本在后端，前端只把它落到镜像里（旧实现 `state.channels = payload.channels` 同一条）。
   *
   * 校验交给后端：任一字段不合法就整体不创建，这里把错误抛给调用方（弹窗内呈现），
   * 不建半成品、也不在本层猜哪一条规则拦住了它。
   */
  async function createChannel(fields) {
    const input = fields || {};
    const payload = await api('/api/channels', {
      method: 'POST',
      body: {
        name: String(input.name || '').trim(),
        description: String(input.description || '').trim(),
        member_ids: input.memberIds || [],
      },
    });
    if (Array.isArray(payload.channels)) state.channels = payload.channels;
    state.online = true;
    return payload;
  }

  /**
   * 新建 Agent。响应回带整份 `agents` 与 `members`，因此 rail 的「本机 Agent」段不需要额外读取。
   * 同样把校验错误抛给调用方（弹窗内呈现），不建半成品成员行。
   */
  async function createAgent(fields) {
    const input = fields || {};
    const payload = await api('/api/agents', {
      method: 'POST',
      body: {
        name: String(input.name || '').trim(),
        role: String(input.role || '').trim(),
        model_id: String(input.modelId || '').trim(),
        system_prompt: String(input.systemPrompt || '').trim(),
      },
    });
    if (Array.isArray(payload.agents)) state.agents = payload.agents;
    if (Array.isArray(payload.members)) state.members = payload.members;
    state.online = true;
    return payload;
  }

  /**
   * 打开与某个 Agent 的私信（`POST /api/dms {agent_id}`）。
   *
   * 后端幂等：已有私信就复用（`created: false`），没有就新建。因此「选中一个成员」与
   * 「创建私信」是同一次动作，前端不需要先查一遍再决定发什么。
   */
  async function openDm(agentId) {
    const payload = await api('/api/dms', { method: 'POST', body: { agent_id: agentId } });
    replaceDm(payload.dm);
    state.online = true;
    return payload;
  }

  /**
   * 确认一次远端数据流告知（`POST /api/remote-data-flow/ack`，按 provider，幂等）。
   *
   * 成功后 `consents` 与 `agents` 都按响应换新：确认会改变成员的启用态，"未启用"这个读数
   * 必须跟着后端走（本层不推断「确认过了所以现在能跑了」）。
   */
  async function acknowledgeRemoteFlow(provider) {
    const payload = await api('/api/remote-data-flow/ack', {
      method: 'POST',
      body: { provider },
    });
    if (Array.isArray(payload.consents)) state.consents = payload.consents;
    if (Array.isArray(payload.agents)) state.agents = payload.agents;
    return payload;
  }

  /** 重试一条失败项：**同一个 pending 项**回到 `pending` 再投递一次。

      它刻意**不**过 `state.online` 那道闸门（与旧实现同一条）：重试是人明确按下的一次动作，
      后端若仍不可达就再失败一次、如实标记——而不是让按钮在离线时按下去毫无反应。 */
  function retryPending(clientId) {
    const pending = state.pending.find((item) => item.clientId === clientId);
    if (!pending) return null;
    pending.status = 'pending';
    pending.reason = null;
    return deliver(pending);
  }

  // ─── 写路径：附件草稿（两步上传的第一步） ──────────────────────────────

  /** 把一批已选中的文件逐个上传成草稿：单个失败只影响它自己，已成功的留在手上。 */
  async function attachFiles(files) {
    const list = Array.from(files || []);
    if (!list.length) return { uploaded: [], failed: [] };
    state.attachingCount += list.length;
    const uploaded = [];
    const failed = [];
    for (const file of list) {
      try {
        const attachment = await uploadAttachment(file);
        state.composerAttachments = state.composerAttachments.concat([attachment]);
        uploaded.push(attachment);
      } catch (err) {
        failed.push({ file, error: err });
      } finally {
        state.attachingCount -= 1;
      }
    }
    return { uploaded, failed };
  }

  /** 移除一个还没发出去的草稿：只从手上移走（库里那一行与字节仍在——没有删除端点）。 */
  function removeComposerAttachment(attachmentId) {
    state.composerAttachments = state.composerAttachments.filter((item) => item.id !== attachmentId);
  }

  /** 草稿交给某条消息之后清空手上的清单（**只在发送成功时**调；失败要留着可重试）。 */
  function clearComposerAttachments() {
    state.composerAttachments = [];
  }

  return {
    state: readonly(state),
    // 写路径要能改镜像：`mutable` 只给本模块与事件流用，组件一律读 `state`。
    mutable: state,
    activeChannel,
    activeDm,
    activeTarget,
    isDm,
    isArchivedDm,
    renderedMessages,
    seenSeqsFor,
    lastSeqOf,
    replaceChannel,
    replaceDm,
    replaceTarget,
    setActiveTargetId,
    targetPathOf,
    loadSettings,
    loadWorkspace,
    refreshMessages,
    refreshOutcomes,
    refreshStatuses,
    refreshChannelMembers,
    addChannelMember,
    readMemberCapabilityCounts,
    loadRemoteFlow,
    loadChannelConfig,
    refreshChannelConfig,
    saveChannelConfig,
    readChannelBudget,
    saveChannelBudgetLimit,
    channelAgentMembers,
    applyOutcomes,
    absorbMessage,
    markCurrentRead,
    markRead,
    sendMessage,
    createChannel,
    createAgent,
    openDm,
    acknowledgeRemoteFlow,
    retryPending,
    discardPending,
    removePending,
    attachFiles,
    removeComposerAttachment,
    clearComposerAttachments,
  };
}
