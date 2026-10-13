/* ============================================================================
   worksplice 工作台前端
   业务状态的唯一正本是后端 HTTP 面：频道、消息、已读位置都只从后端读写。
   本文件不使用 localStorage 保存任何业务状态（唯一允许的本地记忆是「上次打开的频道 id」）。
   DOM 一律用 createElement/textContent 构建，不用 innerHTML（模板与数据分离）。
   ========================================================================== */
'use strict';

const OWNER_FALLBACK = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };

const state = {
  ready: false,
  online: true,
  member: OWNER_FALLBACK,
  channels: [],
  activeChannelId: null,
  messages: [],
  pending: [],
  members: [],
};

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

/* ─── 后端状态 → 前端 ───────────────────────────────────────────────── */
async function loadWorkspace(options) {
  const settings = options || {};
  try {
    const snapshot = await api('/api/workspace');
    state.member = snapshot.member;
    state.members = snapshot.members;
    state.channels = snapshot.channels;
    state.online = true;
    state.ready = true;
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
  } catch (err) {
    state.online = false;
    state.ready = true;
    if (!settings.silent) toast('读取后端失败：' + err.message, 'err');
  }
  render();
  // 正在查看的频道：读完快照后立即标读（刷新/重开浏览器走的就是这条路）。
  markCurrentRead();
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
}

function replaceChannel(channel) {
  const index = state.channels.findIndex((item) => item.id === channel.id);
  if (index >= 0) state.channels[index] = channel;
  else state.channels.push(channel);
}

/* ─── 标读：切入频道、以及正在查看时到达的新消息都立即已读 ─────────────── */
let readTimer = null;

function markCurrentRead() {
  const channel = activeChannel();
  if (!channel) return;
  const rendered = renderedMessages();
  const maxSeq = Math.max(channel.last_seq, ...rendered.filter((m) => m.seq).map((m) => m.seq), 0);
  if (maxSeq <= channel.last_read_seq) return;
  scheduleRead(channel.id, maxSeq);
}

function scheduleRead(channelId, seq) {
  if (readTimer) clearTimeout(readTimer);
  readTimer = setTimeout(async () => {
    readTimer = null;
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
  }, 120);
}

/* ─── 乐观发送：pending → 确认 seq 或显式失败 ─────────────────────────── */
function renderedMessages() {
  return state.messages.concat(state.pending);
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
    if (state.activeChannelId === pending.target_id) {
      if (!state.messages.some((message) => message.seq === payload.message.seq)) {
        state.messages.push(payload.message);
        state.messages.sort((a, b) => a.seq - b.seq);
      }
      const channel = activeChannel();
      if (channel) channel.last_seq = Math.max(channel.last_seq, payload.message.seq);
    }
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
  const agents = state.members.filter((member) => member.kind === 'agent');
  const rows = agents.map((member) => {
    const row = el('div', 'nav-row');
    row.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    row.appendChild(el('span', 'grow', member.display_name));
    return row;
  });
  replaceChildren($('#memberList'), rows);
  $('#memberListEmpty').hidden = agents.length > 0;
}

function renderHeader() {
  const channel = activeChannel();
  const hasChannel = Boolean(channel);
  $('#chanHead').hidden = !hasChannel;
  $('#composer').hidden = !hasChannel;
  if (!hasChannel) return;
  $('#chanTitle').textContent = channel.name;
  $('#chanDesc').textContent = channel.description || '';
  $('#composerInput').placeholder = '发消息到 #' + channel.name;
}

function renderConnection() {
  $('#connBanner').hidden = state.online;
  $('#composer').classList.toggle('is-disabled', !state.online);
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
  head.appendChild(el('span', 'msg-author', author.display_name));
  head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
  body.appendChild(head);
  body.appendChild(el('div', 'msg-text', message.body));

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
    const empty = el('div', 'member-pick-empty', '还没有可勾选的 Agent：Agent 的创建入口在下一票交付，交付后这里自动可用。');
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
    if (event.key === 'Escape' && !$('#channelOverlay').hidden) closeChannelModal();
  });

  window.addEventListener('online', () => loadWorkspace({ silent: true }));
  window.addEventListener('offline', () => { state.online = false; render(); });
}

/* ─── 启动 ──────────────────────────────────────────────────────────── */
bindEvents();
loadWorkspace();
