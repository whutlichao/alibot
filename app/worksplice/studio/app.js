/* ============================================================================
   worksplice 工作台前端
   业务状态的唯一正本是后端 HTTP 面 + 事件流：频道、消息、已读位置、Agent 与状态点都只从后端读。
   本文件不使用 localStorage 保存任何业务状态（唯一允许的本地记忆是「上次打开的频道 id」）。

   本票新增：
   - SSE 订阅：按 seq 追加渲染，Agent 的完整回复不刷新即出现；断线显示连接状态，重连带
     `last_seq` 补拉（补拉由后端读库完成，前端只负责带上游标与去重）。
   - 提及高亮：消费**消息落库时的提及快照**（不自己再实现一遍解析）。
   - 四态状态点：`status_of` 由后端事实推导，前端只渲染 `online/working/error/offline`。
   - 新建 Agent 与远端数据流告知的显式确认。

   DOM 一律用 createElement/textContent 构建，不用 innerHTML（模板与数据分离）。
   ========================================================================== */
'use strict';

const OWNER_FALLBACK = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };

const STATUS_LABEL = {
  online: '空闲',
  working: '正在回复',
  error: '出错',
  offline: '未启用',
};

const PROVIDER_LABEL = {
  dashscope: 'DashScope（远端）',
  openai: 'OpenAI（远端）',
  'openai-response': 'OpenAI Responses（远端）',
  anthropic: 'Anthropic（远端）',
  gemini: 'Gemini（远端）',
  deepseek: 'DeepSeek（远端）',
  moonshot: 'Moonshot（远端）',
  minimax: 'MiniMax（远端）',
  volcengine: '火山引擎（远端）',
  xai: 'xAI（远端）',
  ollama: 'Ollama（本机）',
};

const state = {
  ready: false,
  online: true,
  streamState: 'connecting',
  member: OWNER_FALLBACK,
  channels: [],
  agents: [],
  providers: [],
  localProviders: [],
  consents: [],
  dataFaces: [],
  activeChannelId: null,
  messages: [],
  pending: [],
  members: [],
  statuses: {},
  streams: new Map(),
  seenSeqs: new Map(),
  typing: {},
};

let remoteFlow = { statement: '', data_faces: [], local_providers: [] };

/* ─── 工具 ──────────────────────────────────────────────────────────── */
const $ = (selector) => document.querySelector(selector);

function avatarClass(slot) {
  const index = Number.isInteger(slot) ? slot : 0;
  return 'av-' + (((index % 5) + 5) % 5);
}

function initial(name) {
  const text = String(name || '?').trim();
  return text ? Array.from(text)[0].toUpperCase() : '?';
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function avatarNode(name, slot, size) {
  return el('div', 'avatar ' + (size ? size + ' ' : '') + avatarClass(slot), initial(name));
}

function activeChannel() {
  return state.channels.find((channel) => channel.id === state.activeChannelId) || null;
}

function setActiveChannelId(channelId) {
  state.activeChannelId = channelId;
  // 只记住「上次打开的频道」，这不是业务正本。
  try {
    if (channelId) localStorage.setItem('worksplice.activeChannel', channelId);
    else localStorage.removeItem('worksplice.activeChannel');
  } catch {
    // 隐私模式下 localStorage 不可用：忽略，业务状态本来也不在这里。
  }
}

function readRememberedChannelId() {
  try {
    return localStorage.getItem('worksplice.activeChannel');
  } catch {
    return null;
  }
}

function toast(message, kind) {
  const node = el('div', 'toast' + (kind === 'err' ? ' err' : ''));
  node.appendChild(el('span', 'dot'));
  node.appendChild(document.createTextNode(message));
  $('#toastWrap').appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .2s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 220);
  }, 3200);
}

/* ─── HTTP（唯一读写通道） ───────────────────────────────────────────── */
async function api(path, options) {
  const config = options || {};
  const response = await fetch(path, {
    method: config.method || 'GET',
    headers: config.body ? { 'Content-Type': 'application/json' } : undefined,
    body: config.body ? JSON.stringify(config.body) : undefined,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null; // 非 JSON 响应（例如代理错误页）：正文留给下面的错误映射。
  }
  if (!response.ok) {
    const error = new Error(readError(payload, response));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function readError(payload, response) {
  if (payload && payload.detail && typeof payload.detail === 'string') return payload.detail;
  if (payload && payload.detail && Array.isArray(payload.detail)) {
    return payload.detail.map((item) => item.msg || '请求体不合法').join('；');
  }
  if (payload && payload.error) {
    const fields = payload.error.fields || {};
    const details = Object.keys(fields)
      .map((key) => String(fields[key]))
      // 字段级文案常与总文案重合，别把同一句说两遍。
      .filter((detail) => detail && !String(payload.error.message).includes(detail));
    return details.length ? payload.error.message + '：' + details.join('；') : payload.error.message;
  }
  return '后端返回 ' + response.status;
}

function channelPath(channelId, suffix) {
  return '/api/channels/' + encodeURIComponent(channelId) + (suffix || '');
}

/* ─── 提及：消费后端落库的提及快照（前端不自己解析一遍） ─────────────────── */
function mentionSpans(message) {
  // 只认「有 start/end 的成员快照」：这是后端在同一事务里随消息落库的解析结果。
  const snapshot = Array.isArray(message.mention_snapshot) ? message.mention_snapshot : [];
  return snapshot
    .filter((span) => span && Number.isInteger(span.start) && Number.isInteger(span.end))
    .slice()
    .sort((a, b) => a.start - b.start);
}

function messageText(message) {
  const body = String(message.body == null ? '' : message.body);
  const spans = mentionSpans(message);
  const container = el('div', 'msg-text');
  let cursor = 0;
  spans.forEach((span) => {
    // 快照与正文不一致时（理论上不可能，后端同时写入）宁可退化为纯文本，也不切错字符。
    if (span.start < cursor || span.end > body.length) return;
    if (span.start > cursor) container.appendChild(document.createTextNode(body.slice(cursor, span.start)));
    const mention = el('span', 'mention', body.slice(span.start, span.end));
    mention.dataset.member = span.member_id;
    container.appendChild(mention);
    cursor = span.end;
  });
  if (cursor < body.length) container.appendChild(document.createTextNode(body.slice(cursor)));
  return container;
}

/* ─── 后端状态 → 前端 ───────────────────────────────────────────────── */
async function loadWorkspace(options) {
  const settings = options || {};
  try {
    const snapshot = await api('/api/workspace');
    state.member = snapshot.member;
    state.members = snapshot.members;
    state.channels = snapshot.channels;
    state.agents = snapshot.agents || [];
    state.providers = snapshot.model_providers || [];
    state.localProviders = snapshot.local_providers || [];
    state.consents = snapshot.remote_consents || [];
    state.online = true;
    state.ready = true;
    loadRemoteFlow();
    const known = state.channels.some((channel) => channel.id === state.activeChannelId);
    if (!known) {
      if (settings.keepActive === false) {
        setActiveChannelId(null);
      } else {
        const remembered = readRememberedChannelId();
        const target = state.channels.find((channel) => channel.id === remembered);
        setActiveChannelId(target ? target.id : null);
      }
    }
    await refreshMessages();
    await refreshStatuses();
    syncStreams();
  } catch (err) {
    state.online = false;
    state.ready = true;
    if (!settings.silent) toast('读取后端失败：' + err.message, 'err');
    // 后端不可达不是终点：自动重试，于是「重连后恢复」不需要人点按钮。
    // 这是唯一的本地重试定时器，且它只重读后端状态，不排队任何业务消息。
    scheduleReconnect();
  }
  render();
  // 正在查看的频道：读完快照后立即标读（刷新/重开浏览器走的就是这条路）。
  markCurrentRead();
}

let reconnectTimer = null;

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    loadWorkspace({ silent: true, keepActive: true });
  }, 1500);
}

async function refreshMessages() {
  const channel = activeChannel();
  if (!channel) {
    state.messages = [];
    return;
  }
  const payload = await api(channelPath(channel.id, '/messages'));
  state.messages = payload.messages;
  replaceChannel(payload.channel);
  seenSeqsFor(channel.id).clear();
  payload.messages.forEach((message) => seenSeqsFor(channel.id).add(message.seq));
}

async function refreshStatuses() {
  try {
    const payload = await api('/api/member-statuses');
    state.statuses = payload.statuses || {};
    state.agents = state.agents.map((agent) => ({
      ...agent,
      status: state.statuses[agent.id] || agent.status,
    }));
  } catch {
    // 状态点读不到不影响消息流：保持上一次已知值，不伪造。
  }
}

function replaceChannel(channel) {
  const index = state.channels.findIndex((item) => item.id === channel.id);
  if (index >= 0) state.channels[index] = channel;
  else state.channels.push(channel);
}

function seenSeqsFor(channelId) {
  if (!state.seenSeqs.has(channelId)) state.seenSeqs.set(channelId, new Set());
  return state.seenSeqs.get(channelId);
}

function lastSeqOf(channelId) {
  const channel = state.channels.find((item) => item.id === channelId);
  const known = seenSeqsFor(channelId);
  let max = channel ? Number(channel.last_seq) || 0 : 0;
  known.forEach((seq) => { if (seq > max) max = seq; });
  return max;
}

/* ─── 标读：切入频道、以及正在查看时到达的新消息都立即已读 ─────────────── */
// 每个频道各持一个待发标读定时器：离开 A 去 B 时，A 的标读不能被 B 的覆盖掉
// （共用一个定时器会把前一个 clearTimeout 掉，那个频道的已读推进就被静默丢弃）。
const readTimers = new Map();

function markCurrentRead() {
  const channel = activeChannel();
  if (!channel) return;
  const rendered = renderedMessages();
  const maxSeq = Math.max(channel.last_seq, ...rendered.filter((m) => m.seq).map((m) => m.seq), 0);
  if (maxSeq <= channel.last_read_seq) return;
  scheduleRead(channel.id, maxSeq);
}

function scheduleRead(channelId, seq) {
  const existing = readTimers.get(channelId);
  if (existing) clearTimeout(existing);
  readTimers.set(
    channelId,
    setTimeout(async () => {
      readTimers.delete(channelId);
      try {
        const payload = await api(channelPath(channelId, '/read'), { method: 'POST', body: { seq } });
        replaceChannel(payload.channel);
        state.channels = payload.channels;
        const active = activeChannel();
        if (active) replaceChannel(active);
        render();
      } catch (err) {
        toast('标读失败：' + err.message, 'err');
      }
    }, 120),
  );
}

/* ─── 乐观发送：pending → 确认 seq 或显式失败 ─────────────────────────── */
function renderedMessages() {
  const channelId = state.activeChannelId;
  if (!channelId) return [];
  // 只渲染当前频道的 pending（含已失败但仍保留在本地的项）：失败态不得串到别的频道，
  // 也不得把别的频道的内容算进本频道的顺序里。
  const pending = state.pending.filter((item) => item.target_id === channelId);
  return state.messages.concat(pending);
}

function sendMessage() {
  const input = $('#composerInput');
  const body = input.value.trim();
  const channel = activeChannel();
  if (!body || !channel) return;
  if (!state.online) {
    toast('后端不可达，发送已禁止；消息没有被送出。', 'err');
    return;
  }
  const pending = {
    clientId: 'pending-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7),
    target_id: channel.id,
    body,
    status: 'pending',
    created_at: new Date().toISOString(),
    author: state.member,
  };
  state.pending.push(pending);
  input.value = '';
  autosizeComposer();
  renderMessages();
  syncSendButton();
  scrollToBottom();
  deliver(pending);
}

async function deliver(pending) {
  try {
    const payload = await api(channelPath(pending.target_id, '/messages'), {
      method: 'POST',
      body: { body: pending.body },
    });
    removePending(pending.clientId);
    // 消息本身由事件流按 seq 追加（同一帧可能已经到达），这里只保证它一定在。
    absorbMessage(pending.target_id, payload.message);
    state.online = true;
    renderMessages();
    scrollToBottom();
    await loadWorkspace({ silent: true, keepActive: true });
    // 正在查看该频道时到达的新消息也立即已读。
    markCurrentRead();
  } catch (err) {
    failPending(pending.clientId, err.message);
    if (!err.status) {
      state.online = false;
      toast('后端不可达：这条消息没有送达，也没有留在本地队列里。', 'err');
    } else {
      toast('发送被拒绝：' + err.message, 'err');
    }
    render();
  }
}

/* ─── 事件流（SSE）：按 seq 追加渲染；断线重连带 last_seq 补拉 ──────────────
   重连策略：断线时**主动关掉连接、稍后用最新游标重开**，而不是依赖浏览器带
   `Last-Event-ID` 自动重连 —— 后者会用最初那个 `after_seq` 重新请求整段历史，
   然后靠前端去重才能不掉包。用最新游标重开让「重连带 last_seq 补拉」这件事只有一条路径，
   也就不会因为「去重写漏了一处」而出现重复或错序。
*/
const STREAM_RETRY_MS = 1500;

// 是否因为后端不可达而断开（决定重连成功后要不要做一次全量重取）。
let wasOffline = false;

function syncStreams() {
  const wanted = new Set(state.channels.map((channel) => channel.id));
  state.streams.forEach((_entry, channelId) => {
    if (!wanted.has(channelId)) {
      closeStream(channelId);
    }
  });
  state.channels.forEach((channel) => {
    if (!state.streams.has(channel.id)) openStream(channel.id);
  });
}

function closeStream(channelId) {
  const entry = state.streams.get(channelId);
  if (!entry) return;
  state.streams.delete(channelId);
  if (entry.timer) clearTimeout(entry.timer);
  if (entry.source) entry.source.close();
}

function openStream(channelId) {
  const entry = { source: null, timer: null };
  state.streams.set(channelId, entry);
  connectStream(channelId, entry);
}

function connectStream(channelId, entry) {
  // 每一轮重连都重新取一次游标：它就是「我已经看到哪一条」的最新事实。
  const cursor = lastSeqOf(channelId);
  const source = new EventSource(channelPath(channelId, '/events') + '?after_seq=' + cursor);
  entry.source = source;
  // 服务端的例行轮换（event: rotate）不算掉线：它只是要求重新连一次。
  entry.rotating = false;
  source.addEventListener('rotate', () => {
    entry.rotating = true;
    source.close();
    if (state.streams.get(channelId) !== entry) return;
    entry.timer = setTimeout(() => connectStream(channelId, entry), 250);
  });
  source.onopen = () => {
    const recovered = state.streamState !== 'open';
    state.streamState = 'open';
    // 重连成功 = 后端回来了：先把「不可达」这个状态清掉，再从前端重取一次快照，
    // 保证断线期间的变化都被补齐（不依赖「只补消息」这一件事）。
    if (state.online === false) state.online = true;
    renderConnection();
    if (recovered && wasOffline) {
      wasOffline = false;
      loadWorkspace({ silent: true, keepActive: true });
    }
  };
  source.onerror = () => {
    if (entry.rotating) return; // 我们自己关的：不是错误
    // 事件流断开是「后端不可达」最直接的证据：它就是后端的连接。因此这里把线上态置为不可达，
    // 发送被禁、横幅显示，并启动自动重试 —— 不需要人去点重试按钮。
    state.streamState = 'reconnecting';
    state.online = false;
    wasOffline = true;
    renderConnection();
    render();
    // 断开期间不可能有 in-flight 事实需要显示：清掉「正在回复」，等重连后的状态快照说实话。
    showTypingFor(channelId, null);
    source.close();
    if (state.streams.get(channelId) !== entry) return; // 已被换掉（频道删除等）
    entry.timer = setTimeout(() => connectStream(channelId, entry), STREAM_RETRY_MS);
  };
  ['message', 'member_status', 'typing', 'round_outcome'].forEach((type) => {
    source.addEventListener(type, (event) => handleStreamEvent(channelId, event, type));
  });
}

function handleStreamEvent(channelId, event, type) {
  let payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return; // 解不开的帧直接丢弃：它不可能是后端发出的
  }
  const seq = Number(payload.seq) || 0;
  if (seq && type === 'message') {
    // 消息的去重正本在 absorbMessage 里（按 seq 认一条消息），这里不另做一套。
    onStreamMessage(channelId, payload);
    return;
  }
  if (seq) seenSeqsFor(channelId).add(seq);
  if (type === 'member_status') onStreamStatus(channelId, payload);
  else if (type === 'typing') onStreamTyping(channelId, payload);
  else if (type === 'round_outcome') onStreamOutcome(channelId, payload);
}

function absorbMessage(channelId, message) {
  const known = seenSeqsFor(channelId);
  if (known.has(message.seq)) return false;
  known.add(message.seq);
  const channel = state.channels.find((item) => item.id === channelId);
  if (channel) channel.last_seq = Math.max(Number(channel.last_seq) || 0, message.seq);
  if (state.activeChannelId === channelId) {
    state.messages.push(message);
    state.messages.sort((a, b) => a.seq - b.seq);
  }
  return true;
}

function onStreamMessage(channelId, payload) {
  const added = absorbMessage(channelId, payload.message);
  if (!added) return;
  if (state.activeChannelId === channelId) {
    renderMessages();
    scrollToBottom();
    // 正在查看这个频道：到达即已读。
    markCurrentRead();
  }
  // 其它频道的未读徽标也要跟着动：从后端重新取一次计数（未读的正本在库）。
  refreshChannelList();
}

async function refreshChannelList() {
  try {
    const payload = await api('/api/channels');
    state.channels = payload.channels;
    const active = activeChannel();
    if (active) replaceChannel(active);
    renderChannels();
  } catch {
    // 保留上一次已知的计数：宁可略陈旧，也不伪造。
  }
}

function onStreamStatus(channelId, payload) {
  state.statuses[payload.member_id] = payload.status;
  state.agents = state.agents.map((agent) =>
    agent.id === payload.member_id ? { ...agent, status: payload.status } : agent,
  );
  if (payload.status !== 'working') showTypingFor(channelId, null);
  renderMembers();
  renderDock();
}

function onStreamTyping(channelId, payload) {
  showTypingFor(channelId, payload.active ? payload.member_id : null);
  if (payload.active) {
    state.statuses[payload.member_id] = 'working';
  } else if (state.statuses[payload.member_id] === 'working') {
    state.statuses[payload.member_id] = 'online';
  }
  renderMembers();
  renderDock();
}

function showTypingFor(channelId, memberId) {
  if (memberId) state.typing[channelId] = memberId;
  else delete state.typing[channelId];
  if (state.activeChannelId === channelId) renderTyping();
}

function onStreamOutcome(channelId, payload) {
  const outcome = payload.outcome;
  if (!outcome) return;
  if (outcome.status === 'failed') state.statuses[outcome.member_id] = 'error';
  else if (state.statuses[outcome.member_id] === 'working') state.statuses[outcome.member_id] = 'online';
  renderMembers();
  renderDock();
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
  renderMessages();
}

/* ─── 渲染 ──────────────────────────────────────────────────────────── */
function render() {
  renderIdentity();
  renderChannels();
  renderMembers();
  renderHeader();
  renderMessages();
  renderConnection();
  renderDock();
  renderTyping();
  syncSendButton();
}

function renderIdentity() {
  const member = state.member || OWNER_FALLBACK;
  $('#meName').textContent = member.display_name;
  $('#meSub').textContent = member.kind === 'owner' ? '人类 · 本机' : member.kind;
  const avatar = $('#meAvatar');
  avatar.className = 'avatar ' + avatarClass(member.avatar_slot);
  avatar.textContent = initial(member.display_name);
}

function replaceChildren(container, nodes) {
  while (container.firstChild) container.removeChild(container.firstChild);
  nodes.forEach((node) => container.appendChild(node));
}

function renderChannels() {
  const rows = state.channels.map((channel) => {
    const active = channel.id === state.activeChannelId;
    const row = el('button', 'nav-row' + (active ? ' is-active' : ''));
    row.dataset.channel = channel.id;
    row.setAttribute('data-od-id', 'channel-' + channel.id);
    row.setAttribute('aria-current', active ? 'true' : 'false');
    row.appendChild(el('span', 'hash', '#'));
    row.appendChild(el('span', 'grow', channel.name));
    if (channel.unread_count > 0) {
      const badge = el('span', 'badge', channel.unread_count);
      badge.setAttribute('data-od-id', 'unread-' + channel.id);
      row.appendChild(badge);
    }
    return row;
  });
  replaceChildren($('#channelList'), rows);
  $('#channelListEmpty').hidden = state.channels.length > 0;
}

function renderMembers() {
  const byId = new Map(state.agents.map((agent) => [agent.id, agent]));
  const agents = state.members.filter((member) => member.kind === 'agent');
  const rows = agents.map((member) => {
    const row = el('div', 'nav-row');
    row.dataset.member = member.id;
    row.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    row.appendChild(el('span', 'grow', member.display_name));
    // 状态点：四态只表达后端事实（空闲 / 正在回复 / 出错 / 未启用），不从会话存活推导。
    row.appendChild(presenceNode(statusOf(byId.get(member.id))));
    return row;
  });
  replaceChildren($('#memberList'), rows);
  $('#memberListEmpty').hidden = agents.length > 0;
}

function statusOf(agent) {
  if (agent && typeof agent.status === 'string') return agent.status;
  return state.statuses[agent && agent.id] || 'offline';
}

function presenceNode(status) {
  const node = el('span', 'presence ' + status);
  node.setAttribute('title', STATUS_LABEL[status] || status);
  node.setAttribute('aria-label', STATUS_LABEL[status] || status);
  node.dataset.status = status;
  return node;
}

function renderDock() {
  const channel = activeChannel();
  const container = $('#dockMembers');
  if (!container) return;
  if (!channel) {
    replaceChildren(container, [el('div', 'rail-empty', '先打开一个频道。')]);
    return;
  }
  const byId = new Map(state.agents.map((agent) => [agent.id, agent]));
  const agents = state.members.filter((member) => member.kind === 'agent');
  if (!agents.length) {
    replaceChildren(container, [
      el('div', 'rail-empty', '还没有 Agent 成员：用左上角「Agent」按钮创建一个，再在新建频道时勾选它。'),
    ]);
    return;
  }
  const rows = agents.map((member) => {
    const agent = byId.get(member.id) || {};
    const card = el('div', 'dock-member');
    card.dataset.member = member.id;
    const head = el('div', 'dock-member-head');
    head.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    head.appendChild(el('strong', null, member.display_name));
    if (agent.role) head.appendChild(el('span', 'msg-tag', agent.role));
    head.appendChild(presenceNode(statusOf(agent)));
    card.appendChild(head);
    const meta = el('div', 'dock-member-meta mono', agent.model_id || '未配置模型');
    card.appendChild(meta);
    const status = statusOf(agent);
    const line = el('div', 'dock-member-status', STATUS_LABEL[status] || status);
    line.dataset.status = status;
    card.appendChild(line);
    if (agent.enabled_reason) {
      const reason = el('div', 'dock-member-reason', agent.enabled_reason);
      reason.dataset.odId = 'agent-reason-' + member.id;
      card.appendChild(reason);
    }
    return card;
  });
  replaceChildren(container, rows);
}

function renderTyping() {
  const channel = activeChannel();
  const container = $('#typingLine');
  const memberId = channel ? state.typing[channel.id] : null;
  const agent = state.agents.find((item) => item.id === memberId);
  container.hidden = !agent;
  container.textContent = agent ? agent.display_name + ' 正在回复…' : '';
}


function renderHeader() {
  const channel = activeChannel();
  const hasChannel = Boolean(channel);
  $('#chanHead').hidden = !hasChannel;
  $('#composer').hidden = !hasChannel;
  if (!hasChannel) return;
  $('#chanTitle').textContent = channel.name;
  $('#chanDesc').textContent = channel.description || '';
  // placeholder 结构按契约：发消息到 #频道名 —— 输入 @ 提及 Agent。
  $('#composerInput').placeholder = '发消息到 #' + channel.name + ' —— 输入 @ 提及 Agent';
}

function renderConnection() {
  $('#connBanner').hidden = state.online;
  $('#composer').classList.toggle('is-disabled', !state.online);
  const stream = $('#streamState');
  if (stream) {
    const reconnecting = state.online && state.streamState !== 'open';
    stream.hidden = !reconnecting;
    stream.textContent = reconnecting ? '事件流正在重连：恢复后会自动补拉错过的消息。' : '';
  }
}

function renderMessages() {
  const channel = activeChannel();
  $('#emptyNoChannel').hidden = Boolean(channel);
  const messages = channel ? renderedMessages() : [];
  $('#emptyNoMessage').hidden = !channel || messages.length > 0;

  const nodes = [];
  let lastDay = null;
  messages.forEach((message) => {
    const day = dayLabel(message.created_at);
    if (day !== lastDay) {
      const separator = el('div', 'day-sep');
      separator.appendChild(el('span', null, day));
      nodes.push(separator);
      lastDay = day;
    }
    nodes.push(messageNode(message));
  });
  replaceChildren($('#messageList'), nodes);
}

function messageNode(message) {
  if (message.kind === 'system') {
    const article = el('article', 'msg sys');
    article.dataset.seq = message.seq;
    article.appendChild(el('div', 'avatar sys-avatar', '·'));
    const body = el('div', 'msg-body');
    const head = el('div', 'msg-head');
    head.appendChild(el('span', 'msg-author', '系统'));
    head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
    body.appendChild(head);
    body.appendChild(el('div', 'msg-text', message.body));
    article.appendChild(body);
    return article;
  }

  const failed = message.status === 'failed';
  // 失败的消息还没有 seq，但它不是「待确认」——两个状态不能同时挂在一个节点上。
  const pending = !message.seq && !failed;
  const author = message.author || state.member;
  const article = el('article', 'msg' + (pending ? ' pending' : '') + (failed ? ' failed' : ''));
  if (pending) article.dataset.pending = message.clientId;
  else article.dataset.seq = message.seq;

  article.appendChild(avatarNode(author.display_name, author.avatar_slot));
  const body = el('div', 'msg-body');
  const head = el('div', 'msg-head');
  head.appendChild(el('span', 'msg-author' + (author.kind === 'agent' ? ' is-agent' : ''), author.display_name));
  if (author.kind === 'agent') head.appendChild(el('span', 'msg-tag', 'Agent'));
  head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
  body.appendChild(head);
  body.appendChild(messageText(message));

  if (failed) {
    const retry = el('div', 'msg-retry');
    retry.appendChild(el('span', null, '未送达：' + (message.reason || '发送失败')));
    const retryBtn = el('button', 'btn btn-sm', '重试发送');
    retryBtn.dataset.retry = message.clientId;
    const discardBtn = el('button', 'btn btn-sm', '移除');
    discardBtn.dataset.discard = message.clientId;
    retry.appendChild(retryBtn);
    retry.appendChild(discardBtn);
    body.appendChild(retry);
  }

  article.appendChild(body);
  return article;
}

function dayLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '时间未知';
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return '今天';
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return date.getFullYear() + '-' + month + '-' + day;
}

function timeLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '--:--';
  return String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
}

function scrollToBottom() {
  const stream = $('#viewMessages');
  stream.scrollTop = stream.scrollHeight;
}

function autosizeComposer() {
  const input = $('#composerInput');
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
}

function syncSendButton() {
  $('#btnSend').disabled = !$('#composerInput').value.trim() || !state.online || !activeChannel();
}

/* ─── 新建频道 ──────────────────────────────────────────────────────── */
function openChannelModal() {
  $('#channelFormError').hidden = true;
  $('#channelNameInput').value = '';
  $('#channelDescInput').value = '';
  renderMemberPicker();
  $('#channelOverlay').hidden = false;
  $('#channelNameInput').focus();
}

function closeChannelModal() {
  $('#channelOverlay').hidden = true;
}

function renderMemberPicker() {
  const agents = state.members.filter((member) => member.kind === 'agent');
  const picker = $('#memberPicker');
  if (!agents.length) {
    // 无 Agent 时成员清单是空态，但控件（勾选容器）按规格存在。
    const empty = el('div', 'member-pick-empty', '还没有可勾选的 Agent：用左上角「Agent」按钮创建一个，回到这里勾选即可加入频道。');
    empty.setAttribute('data-od-id', 'member-picker-empty');
    replaceChildren(picker, [empty]);
    return;
  }
  const options = agents.map((member) => {
    const option = el('button', 'member-opt');
    option.type = 'button';
    option.dataset.pick = member.id;
    option.setAttribute('aria-pressed', 'false');
    option.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    option.appendChild(document.createTextNode(member.display_name));
    return option;
  });
  replaceChildren(picker, options);
}

async function submitChannel() {
  const name = $('#channelNameInput').value.trim();
  const description = $('#channelDescInput').value.trim();
  const memberIds = Array.prototype.map.call(
    $('#memberPicker').querySelectorAll('.member-opt.is-on'),
    (node) => node.dataset.pick,
  );
  const submit = $('#channelCreateSubmit');
  submit.disabled = true;
  try {
    // 校验交给后端：失败时整体不创建，前端只呈现错误、不建半成品。
    const payload = await api('/api/channels', { method: 'POST', body: { name, description, member_ids: memberIds } });
    state.channels = payload.channels;
    setActiveChannelId(payload.channel.id);
    closeChannelModal();
    await loadWorkspace({ silent: true, keepActive: true });
    scrollToBottom();
    $('#composerInput').focus();
    toast('已创建频道 #' + payload.channel.name + '，可以直接发送第一条消息。');
  } catch (err) {
    const box = $('#channelFormError');
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    submit.disabled = false;
  }
}

/* ─── 新建 Agent 与远端数据流告知 ───────────────────────────────────── */
function openAgentModal() {
  $('#agentFormError').hidden = true;
  $('#agentNameInput').value = '';
  $('#agentRoleInput').value = '';
  $('#agentModelInput').value = '';
  $('#agentPromptInput').value = '';
  renderProviderSelect();
  renderRemoteNotice();
  $('#agentOverlay').hidden = false;
  $('#agentNameInput').focus();
}

function closeAgentModal() {
  $('#agentOverlay').hidden = true;
}

function renderProviderSelect() {
  const select = $('#agentProviderSelect');
  const providers = state.providers.length ? state.providers : ['ollama', 'dashscope', 'openai'];
  const options = providers.map((provider) => {
    const option = el('option', null, PROVIDER_LABEL[provider] || provider);
    option.value = provider;
    return option;
  });
  replaceChildren(select, options);
  select.value = providers[0];
  updateProviderHint();
}

function currentProvider() {
  return $('#agentProviderSelect').value;
}

function isLocalProvider(provider) {
  return state.localProviders.indexOf(provider) >= 0;
}

function updateProviderHint() {
  const provider = currentProvider();
  const hint = $('#agentProviderHint');
  hint.textContent = isLocalProvider(provider)
    ? '本机模型：不离开本机，不需要数据流确认。'
    : '远端模型：启用前需要先确认数据会离开本机（见下方告知）。';
}

function acknowledged(provider) {
  return state.consents.some((record) => record.provider === provider);
}

function renderRemoteNotice() {
  const provider = currentProvider();
  const box = $('#agentRemoteNotice');
  if (isLocalProvider(provider) || acknowledged(provider)) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const faces = (state.dataFaces.length ? state.dataFaces : remoteFlow.data_faces).join('、');
  $('#agentRemoteTitle').textContent = '启用 ' + (PROVIDER_LABEL[provider] || provider) + ' 前需要你确认一次';
  $('#agentRemoteBody').textContent =
    '启用后，以下数据会离开本机并发送给该模型提供商：' +
    faces +
    '。接收方是远端服务，不在你的机器上。';
  $('#agentRemoteHint').textContent =
    '本机存储不做应用层端到端加密，也不会声称端到端加密；后端默认只监听 loopback。';
}

async function confirmRemoteFlow() {
  const provider = currentProvider();
  const button = $('#agentRemoteConfirm');
  button.disabled = true;
  try {
    const payload = await api('/api/remote-data-flow/ack', { method: 'POST', body: { provider } });
    state.consents = payload.consents || [];
    state.agents = payload.agents || state.agents;
    renderRemoteNotice();
    // 确认会改变成员的启用态：状态点必须从后端重取，不靠本地推断。
    await loadWorkspace({ silent: true, keepActive: true });
    toast('已记录数据流告知确认：' + (PROVIDER_LABEL[provider] || provider) + '。');
  } catch (err) {
    toast('确认失败：' + err.message, 'err');
  } finally {
    button.disabled = false;
  }
}

async function submitAgent() {
  const provider = currentProvider();
  const model = $('#agentModelInput').value.trim();
  const payload = {
    name: $('#agentNameInput').value.trim(),
    role: $('#agentRoleInput').value.trim(),
    model_id: provider + ':' + model,
    system_prompt: $('#agentPromptInput').value.trim(),
  };
  const box = $('#agentFormError');
  const submit = $('#agentCreateSubmit');
  submit.disabled = true;
  try {
    // 校验交给后端：名称或模型留空时整体不创建，前端只呈现错误、不建半成品。
    const result = await api('/api/agents', { method: 'POST', body: payload });
    state.agents = result.agents || state.agents;
    state.members = result.members || state.members;
    closeAgentModal();
    await loadWorkspace({ silent: true, keepActive: true });
    toast('已创建 Agent ' + result.agent.display_name + '，可在新建频道时勾选它。');
  } catch (err) {
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    submit.disabled = false;
  }
}

async function loadRemoteFlow() {
  try {
    remoteFlow = await api('/api/remote-data-flow');
    state.dataFaces = remoteFlow.data_faces || [];
  } catch {
    // 告知文案读不到不影响主流程；下一次打开面板会重试。
  }
}

/* ─── 事件绑定 ──────────────────────────────────────────────────────── */
async function selectChannel(channelId) {
  const channel = state.channels.find((item) => item.id === channelId);
  if (!channel) return;
  const previous = state.activeChannelId;
  if (previous && previous !== channelId) {
    // 离开旧频道前把它推到当前标读；其它频道的已读位置不受影响。
    const stale = state.channels.find((item) => item.id === previous);
    if (stale && stale.last_seq > stale.last_read_seq) scheduleRead(previous, stale.last_seq);
  }
  setActiveChannelId(channelId);
  state.pending = state.pending.filter((item) => item.target_id === channelId || item.status === 'failed');
  render();
  try {
    await refreshMessages();
    state.online = true;
  } catch (err) {
    state.online = false;
    toast('读取频道失败：' + err.message, 'err');
  }
  render();
  markCurrentRead();
  scrollToBottom();
}

function bindEvents() {
  $('#btnNewChannel').addEventListener('click', openChannelModal);
  $('#btnNewAgent').addEventListener('click', openAgentModal);
  $('#agentCancel').addEventListener('click', closeAgentModal);
  $('#agentModalClose').addEventListener('click', closeAgentModal);
  $('#agentCreateSubmit').addEventListener('click', submitAgent);
  $('#agentRemoteConfirm').addEventListener('click', confirmRemoteFlow);
  $('#agentProviderSelect').addEventListener('change', () => {
    updateProviderHint();
    renderRemoteNotice();
  });
  $('#agentOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'agentOverlay') closeAgentModal();
  });
  $('#channelCancel').addEventListener('click', closeChannelModal);
  $('#channelModalClose').addEventListener('click', closeChannelModal);
  $('#channelCreateSubmit').addEventListener('click', submitChannel);
  $('#channelOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'channelOverlay') closeChannelModal();
  });
  $('#channelNameInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submitChannel(); }
  });
  $('#memberPicker').addEventListener('click', (event) => {
    const option = event.target.closest('.member-opt');
    if (!option) return;
    const on = option.classList.toggle('is-on');
    option.setAttribute('aria-pressed', on ? 'true' : 'false');
  });

  $('#channelList').addEventListener('click', (event) => {
    const row = event.target.closest('[data-channel]');
    if (row) selectChannel(row.dataset.channel);
  });

  $('#composerInput').addEventListener('input', () => {
    autosizeComposer();
    syncSendButton();
  });
  $('#composerInput').addEventListener('keydown', (event) => {
    // Enter 发送、Shift+Enter 换行；输入法组合中的 Enter 不算发送。
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendMessage();
    }
  });
  $('#btnSend').addEventListener('click', sendMessage);

  $('#messageList').addEventListener('click', (event) => {
    const retry = event.target.closest('[data-retry]');
    if (retry) {
      const pending = state.pending.find((item) => item.clientId === retry.dataset.retry);
      if (pending) {
        pending.status = 'pending';
        pending.reason = null;
        renderMessages();
        deliver(pending);
      }
      return;
    }
    const discard = event.target.closest('[data-discard]');
    if (discard) discardPending(discard.dataset.discard);
  });

  $('#btnDockToggle').addEventListener('click', () => {
    $('#dock').hidden = !$('#dock').hidden;
  });
  $('#btnReconnect').addEventListener('click', () => loadWorkspace());

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!$('#channelOverlay').hidden) closeChannelModal();
    if (!$('#agentOverlay').hidden) closeAgentModal();
  });

  window.addEventListener('online', () => loadWorkspace({ silent: true }));
  window.addEventListener('offline', () => { state.online = false; render(); });
}

/* ─── 启动 ──────────────────────────────────────────────────────────── */
bindEvents();
loadWorkspace();
