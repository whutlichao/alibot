/* ============================================================================
   alibot 工作台前端
   业务状态的唯一正本是后端 HTTP 面 + 事件流：频道、消息、已读位置、Agent 与状态点都只从后端读。
   本文件不使用 localStorage 保存任何业务状态（唯一允许的本地记忆是「上次打开的频道 id」）。

   本票新增：
   - SSE 订阅：按 seq 追加渲染，Agent 的完整回复不刷新即出现；断线显示连接状态，重连带
     `last_seq` 补拉（补拉由后端读库完成，前端只负责带上游标与去重）。
   - 提及高亮：消费**消息落库时的提及快照**（不自己再实现一遍解析）。
   - 四态状态点：`status_of` 由后端事实推导，前端只渲染 `online/working/error/offline`。
   - 新建 Agent 与远端数据流告知的显式确认。
   - `@` 补全：composer 里输入 `@` 弹出**当前频道的 Agent 成员**清单；前端不解析提及，只按
     `mentions.py` 的引号规则拼出要插入的字符串（「是不是提及」始终由后端在消息落库时裁决）。

   本票（issue #8）新增：
   - 轮次失败的可见呈现：消费只读端点 `GET /channels/{id}/round-outcomes` 的**既有事实**
     （`status` / `reason` / `base_seq`），在消息流里按 `base_seq` 挂到**触发它的那条消息**下面。
     轮次结果不是消息：它不占 `seq`、不改写任何消息，也不伪造「它答应了」。
   - 「未启用」与「这一轮失败」在呈现上区分开：`--offline` 是成员状态（侧栏/dock 状态点），
     `--error` 是这一轮的结论（消息流里的失败行）。

   本票（issue #18）新增：
   - 轮次结果**全态可见**（#58 起六态），但**每一条消息下面至多一行**：同一条消息（同一 `base_seq`）下
     若有多条**非 `failed`** 的轮次结果（`ignored` / `budget-exhausted` / `merged` / `silent`），收成
     **一行默认折叠的摘要行**（成员名按后端返回序逐个列出，超过 2 人写成「甲、乙 等 N 人」）；
     展开后逐成员一行给出该成员的 status 中文说法与后端 `reason` 原文（不重新措辞、不截断）。
   - `failed` 保持**直接展开**（不藏在折叠后面），与摘要行同挂一条消息、互不吞并。
     「它为什么没说话」的四种**正常协议选择**（自判忽略 / 预算已用尽 / 被合并 / 结构化声明沉默）从静默
     变成可读，但不得淹没对话，也不得与「它坏了」混为一谈（`silent` 不是失败，也不是 `ignored`）。
   - 折叠/展开是本浏览器页面的呈现状态（内存里的 `state.expandedOutcomes`），不进 localStorage：
     本文件依旧不用浏览器存储保存任何业务状态。

   DOM 一律用 createElement/textContent 构建，不用 innerHTML（模板与数据分离）。

   本票（issue #14）新增：
   - 右侧「频道成员」面板名实相符：按**当前频道的成员表**（`state.channelMembers`，来源
     `GET /api/channels/{id}/members`）渲染，并分「已加入 / 未加入」两段；未加入的 Agent
     在面板内直接有「加入」控件（人类裁定 2 选 a），点击调用 `POST /api/channels/{id}/members`。
   - 加入成功后**不刷新页面**：用响应里的最新成员表覆盖缓存，`@` 补全立刻能选到它。
     左栏「本机 Agent」（`renderMembers()`）仍是全工作区名单，语义与「频道成员」不再冲突。

   本票（issue #68）新增：
   - **提醒**：`reminder` 是**库内对象**（ADR-0012），后端是唯一正本。左栏 rail 的入口旁的圆点
     只在**有提醒**时出现（原型 `alibot-app.html:511-514` / `:1886`）；弹窗按后端读数渲染列表
     （时间 / 文本 / 归属频道 / 删除）、空态与新增控件（文本 + 时间）。
   - 增删提醒与改设置同一条纪律：**不落消息、不唤醒、不消耗预算**，也不新增 target、不计未读。
     写路径一律以**响应里的最新提醒表**覆盖本地镜像（不本地拼一条假行），因此列表永远等于后端说
     的那一份；本文件依旧不用任何浏览器存储保存业务状态。

   本票（issue #114）新增 —— 上一段 #68 里**两处读数已被本票取代**（原文保留作交付历史）：
   - 圆点的判据从「有提醒」改成「**还有未到点的提醒**」（`!state.reminders.some(r => !r.fired_at)`）：
     已响 / 已错过不等待任何事。
   - 新增控件从「文本 + 时间」改成「文本 + **日期 + 钟点**两个原生输入」，提交时拼成一个
     `YYYY-MM-DD HH:MM` 串交给后端（ADR-0017 的时间语义）；列表行改成
     「文本 · 归属频道 · [到期时刻 | 「已提醒」 | 「已错过」] · 删除」：到期时刻并进状态后缀
     （用户裁定，见 :func:`reminderStateNode`），只消费后端给的 `state`，前端不做任何判定。

   本票（issue #54）新增 —— 频道设置里的**回应预算**（ADR-0007 / 规格 #31 §2）：
   - 频道头新增预算入口，打开一张**频道设置**弹窗（工作区设置弹窗的语义一个字不改）；
   - 弹窗只读四项后端读数（已用 / 剩余 / **有效上界** / **来源**）与**下一世代待生效值**，两者分开
     呈现；预算耗尽写成一条明确事实，不进错误区；
   - 写控件只有固定档位 3 / 5 / 10（默认 3），提交 `{budget_limit}`；保存后按响应里的新读数重铺，
     并提示「从下一个人类世代生效」。用量（`used`）前端既不算也不提交——它是后端的正本。
   ========================================================================== */
'use strict';

const OWNER_FALLBACK = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };

/* ─── 界面文案一律按**词条**取值（`i18n.js` 的 `t(key, params)`） ────────────
   本文件里不再有裸中文文案：结构化状态词（四态状态点 / 任务状态 / 轮次结果状态）后端只给**词**，
   这里只把词翻译成界面说法；措辞全部住在词条表的两份取值里。

   `hasKey` 的分支是「有词条就用词条、没有就回退到后端原词」：回退不是为了兜住漏网文案，
   而是因为状态词是**后端给的集合**（例如未来的新状态），前端不该伪造一个说法给它。 */

/** 四态状态点的界面说法（键名与后端 `status_of` 的四态同名）。 */
function statusLabel(status) {
  const key = 'status.' + status;
  return hasKey(key) ? t(key) : status;
}

/** 任务状态的界面说法（键名与 `TASK_STATUSES` 同名）。 */
function taskStatusLabel(status) {
  const key = 'task.status.' + status;
  return hasKey(key) ? t(key) : status;
}

/** 状态迁移 chip 上的**动词**文案（原型 `THREAD_TRANSITION_LABEL`）。
    按钮一律动宾式（`SKILL.md` §6）；`closed` 是终态，因此它没有目标描述符，
    那张表里也不会有 `closed → *` 的条目（不是「漏了一格」，是这一格不存在）。 */
function taskTransitionLabel(target) {
  const key = 'task.transition.' + target;
  return hasKey(key) ? t(key) : taskStatusLabel(target);
}

const TASK_STATUS_ORDER = ['todo', 'in_progress', 'in_review', 'done', 'closed'];
const TASK_STATUS_TRANSITIONS = {
  todo: ['in_progress', 'closed'],
  in_progress: ['in_review', 'todo', 'closed'],
  in_review: ['done', 'in_progress', 'todo'],
  done: ['in_progress', 'closed'],
  closed: [],
};

/** 模型提供商的界面说法（远端 / 本机是**事实**，不是档位）。 */
function providerLabel(provider) {
  const key = 'provider.' + provider;
  return hasKey(key) ? t(key) : provider;
}

/* 轮次结果在**摘要行**里的呈现：一个 status 一行，三张字段不再各占一张平行表
   （加一态只需改一处，也不会出现「某张表漏了一态」的漂移）。

   键集是 `CONTEXT.md` `## Language` 词表里、**不进摘要行**的那四个：`ignored`（自判忽略）、
   `budget-exhausted`（预算耗尽）、`merged`（被合并）、`silent`（模型经结构化通道声明沉默）。
   它们**不得混为一谈**：`ignored` 是正常协议选择、`budget-exhausted` 不是出错、`merged` 不是失败、
   `silent` 是模型**显式声明**的沉默（不是失败，也不是既有的自判忽略）。

   `replied` 与 `failed` 不在表里：`replied` 的可见事实就是那条回复消息本身（不为它挂行）；
   `failed` 直接展开（状态词要在「成员此刻未启用」与「这一轮失败」之间区分，写在 `roundOutcomeNode()` 里）。
   不给它们留查不到的死词条。

   `note` 是折叠态那一句「为什么」——四态各自的后端事实不同，说法就应当不同。 */
const SUMMARY_STATUSES = ['ignored', 'budget-exhausted', 'merged', 'silent'];

/** 取一个 status 的中文说法；不在摘要词表里就原样回退（不伪造一个说法）。 */
function outcomeLabel(status) {
  const key = 'outcome.' + status + '.label';
  return hasKey(key) ? t(key) : status;
}

/** 折叠行那一句「为什么」（四态各自的后端事实说法；与 `label` 一样只在词条表里）。 */
function outcomeNote(status) {
  const key = 'outcome.' + status + '.note';
  return hasKey(key) ? t(key) : status;
}

// 工作台快照到齐之前的占位视图。它**不是** provider 清单的正本（正本只有后端 `/api/workspace`
// 的 `model_providers`），只为了刷新后马上点「Agent」时不至于给一个空选择器。
const PROVIDER_PLACEHOLDER = ['ollama', 'dashscope', 'openai'];

/* ─── 主题三档（#71）：取值与后端 `THEMES` 同名同序 ──────────────────────────
   `system` 是**缺省**档：它不在这里解析媒体查询——「跟随系统」由 CSS 的
   `@media (prefers-color-scheme: dark)` 裁决，前端只把档位本身写到 `<html data-theme>` 上，
   于是显式两档走属性选择器、跟随系统走媒体查询，两条分支各有一个落点。
   标签文案住在词条表（`settings.themeLight` / `settings.themeDark` / `settings.themeSystem`）。 */
const THEMES = ['light', 'dark', 'system'];
const DEFAULT_THEME = 'system';

const state = {
  ready: false,
  mode: 'channel',
  online: true,
  streamState: 'connecting',
  member: OWNER_FALLBACK,
  channels: [],
  tasks: [],
  boardTasks: [],
  // 看板的呈现状态（不是业务状态）：`.seg` 的两半、频道筛选（`''` = 全工作区）、
  // 「我的任务」。「我的任务」由 owner 与任务指派者（或未指派）的直接比较决定。
  taskView: 'board',
  taskFilterChannelId: '',
  taskFilterMine: false,
  // 任务线程现在是右侧 dock 的**第二种形态**（`dockMode`），不再是主线上的一个视图。
  dockMode: 'agent',
  // 「人把 dock 收起来了」——与「dock 里有没有内容」是两件事。`setDockOpen(false)` 置位，
  // 显式打开（卡片 / Agent 行 / 频道头的开关）清位；`setDockMode()` 只在没有这一位时才
  // 自动显示，否则下一次整体重绘会把「人刚按下的收起」推翻（收起会变成一次性的假动作）。
  dockDismissed: false,
  // Agent 详情（dock **第三形态**）：`activeAgentId` 是被打开的那一个成员，
  // `agentDetailTab` 是它当前显示的页签（只有「概览」「运行时」两个，日志页签不做）。
  activeAgentId: null,
  agentDetailTab: 'overview',
  // 编辑配置弹窗当前编辑的成员（`null` = 弹窗关着）。
  agentEditId: null,
  // 成员名册（频道成员的**只读**全名单）当前锁定的频道 id；`null` = 弹窗关着。
  // 名册回答「打开它的那个频道里有谁」，因此它在打开时锁定频道，不跟着激活频道跑。
  rosterChannelId: null,
  // 频道回应预算弹窗（#54）：`budget` 是 `GET/PATCH /api/channels/{id}/budget` 载荷的镜像，
  // `selected` 是**还没提交**的档位选择（呈现状态）。两者都不是业务正本——正本在后端；
  // 关闭时整个镜像作废，于是「拿旧读数当现状」这个中间态不存在。
  channelBudget: { channelId: null, budget: null, selected: null, loading: false, error: '' },
  activeTaskId: null,
  activeTask: null,
  taskMessages: [],
  taskSeenSeqs: new Set(),
  taskStream: null,
  taskStreamTimer: null,
  dms: [],
  // 主 composer 里还没发送的附件草稿（上传成功后拿到标识与元数据；随下一条消息绑定）。
  // 它**不是**业务正本：正本是库里的附件行（+ 数据目录里的字节）。
  composerAttachments: [],
  agents: [],
  providers: [],
  localProviders: [],
  consents: [],
  dmSelectedAgentId: null,
  dataFaces: [],
  activeChannelId: null,
  messages: [],
  // 轮次结果按「触发它的那条消息的 seq」分组（后端事实的镜像，不是本地推断）。
  outcomes: {},
  // 摘要行的展开/折叠：**会话内的呈现状态**，不是业务状态（不进任何浏览器存储）。
  // 键 = 触发它的那条消息的 seq。
  expandedOutcomes: {},
  // 轮次结果的第二份索引：按**回复消息**的 `reply_seq` 直取那一条轮次结果。
  // 旁白行要展开看「关联轮次结果的 reason」，而 `outcomes` 是按**触发消息**的 `base_seq`
  // 分组的——两份索引都从同一批后端行来、在 `applyOutcomes` 一处建，因此不会各自漂。
  replyOutcomes: {},
  // 旁白行的展开/折叠：同样是**会话内的呈现状态**。键 = 旁白那条消息自己的 `seq`
  // （与 `expandedOutcomes` 不共用：那一个键是**触发**消息的 seq，两者会撞号）。
  expandedNarrations: {},
  pending: [],
  members: [],
  channelMembers: {},
  statuses: {},
  streams: new Map(),
  seenSeqs: new Map(),
  typing: {},
  // 工作区设置（**语言偏好** / **消息密度** / **主题**）：正本是后端 `GET /api/settings`，
  // 前端只镜像它；切换时写回后端，不落浏览器存储。
  settings: {
    locale: I18N_SOURCE_LOCALE,
    message_density: 'comfortable',
    theme: DEFAULT_THEME,
  },
  // 提醒（`CONTEXT.md` **提醒**，ADR-0012）：同样是**后端对象的镜像**，正本是 `GET /api/reminders`
  // （`reminder` 表）。增删都以响应里的最新列表覆盖它，本地不造行、不落浏览器存储。
  reminders: [],
  // 搜索是纯呈现状态：命中、来源和高亮区间都来自后端，不在浏览器保存或重算。
  // `facet` 是**后端的过滤参数**（原型的四档），前端只记「选的是哪一档」。
  search: { query: '', facet: 'all', hits: [], loading: false, error: '' },
};

let searchRequestVersion = 0;
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

/** 头像的 class 串：左栏的就地更新（`syncAvatarCell`）与新建节点（`avatarNode`）共用同一个
    写法，免得改名 / 换色位时两处漂成两套。 */
function avatarClassName(size, slot) {
  return 'avatar ' + (size ? size + ' ' : '') + avatarClass(slot);
}

function avatarNode(name, slot, size) {
  return el('div', avatarClassName(size, slot), initial(name));
}

function activeChannel() {
  return state.channels.find((channel) => channel.id === state.activeChannelId) || null;
}

function activeTarget() {
  return activeChannel() || state.dms.find((dm) => dm.id === state.activeChannelId) || null;
}

function setActiveChannelId(channelId) {
  state.activeChannelId = channelId;
  // 只记住「上次打开的频道」，这不是业务正本。
  try {
    if (channelId) localStorage.setItem('alibot.activeChannel', channelId);
    else localStorage.removeItem('alibot.activeChannel');
  } catch {
    // 隐私模式下 localStorage 不可用：忽略，业务状态本来也不在这里。
  }
}

function readRememberedChannelId() {
  try {
    return localStorage.getItem('alibot.activeChannel');
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
    return payload.detail.map((item) => item.msg || t('error.badRequestBody')).join(t('common.clauseSep'));
  }
  if (payload && payload.error) {
    const fields = payload.error.fields || {};
    const details = Object.keys(fields)
      .map((key) => String(fields[key]))
      // 字段级文案常与总文案重合，别把同一句说两遍。
      .filter((detail) => detail && !String(payload.error.message).includes(detail));
    return details.length
      ? payload.error.message + t('common.colonSep') + details.join(t('common.clauseSep'))
      : payload.error.message;
  }
  return t('error.httpStatus', { status: response.status });
}

function channelPath(channelId, suffix) {
  return '/api/channels/' + encodeURIComponent(channelId) + (suffix || '');
}

function taskPath(taskId, suffix) {
  return '/api/tasks/' + encodeURIComponent(taskId) + (suffix || '');
}

function dmPath(dmId, suffix) {
  return '/api/dms/' + encodeURIComponent(dmId) + (suffix || '');
}

function targetPath(targetId, suffix) {
  return state.dms.some((dm) => dm.id === targetId)
    ? dmPath(targetId, suffix)
    : channelPath(targetId, suffix);
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

/* ─── 工作台搜索：只消费后端命中与区间，不在前端重新判定正文 ───────────────
   三类命中（消息 / 任务 / Agent）共用同一套呈现：可呈现文本 + 后端给的区间 + 来源 + 类型。
   前端既不算命中、也不在文本里找位置（`CONTEXT.md` 搜索命中 / 高亮区间两条硬纪律）。 */
function searchTargetLabel(target) {
  const name = target && target.display_name ? target.display_name : t('search.unknownTarget');
  if (!target) return name;
  if (target.kind === 'channel') return t('search.targetChannel', { name });
  if (target.kind === 'dm') return t('search.targetDm', { name });
  if (target.kind === 'task') return t('search.targetTask', { name });
  return name;
}

/** 结果行 `.r-top` 的「来源」一格：命中所属 target；Agent 不挂 target，因此给它的集合名。 */
function searchSourceLabel(hit) {
  return hit.target ? searchTargetLabel(hit.target) : t('search.sourceAgent');
}

/** 结果行 `.r-top` 的命中类型标签（与四档 facet 同名：`消息` / `任务` / `Agent`）。 */
function searchHitTypeLabel(hit) {
  const key = { message: 'search.facetMessage', task: 'search.facetTask', agent: 'search.facetAgent' }[
    hit.kind
  ];
  return key ? t(key) : String(hit.kind || '');
}

/** `.r-title`：**只**按后端给的区间切分文本，区间缺失时整段呈现（绝不在前端找位置）。 */
function searchHitText(hit) {
  const text = String(hit && hit.text == null ? '' : hit.text);
  const ranges = Array.isArray(hit && hit.highlight_ranges) ? hit.highlight_ranges : [];
  const container = el('div', 'r-title');
  let cursor = 0;
  ranges.forEach((range) => {
    if (!range || !Number.isInteger(range.start) || !Number.isInteger(range.end)) return;
    if (range.start < cursor || range.end <= range.start || range.end > text.length) return;
    if (range.start > cursor) container.appendChild(document.createTextNode(text.slice(cursor, range.start)));
    container.appendChild(el('mark', null, text.slice(range.start, range.end)));
    cursor = range.end;
  });
  if (cursor < text.length) container.appendChild(document.createTextNode(text.slice(cursor)));
  return container;
}

/** `.r-snip`：命中本体之外的**一行**辅助事实（消息给作者、任务给序号与状态、Agent 给状态与模型）。 */
function searchSnipNode(hit) {
  const snip = el('div', 'r-snip');
  if (hit.kind === 'message') {
    const author = (hit.message && hit.message.author) || {};
    snip.appendChild(
      document.createTextNode(
        t('search.snipAuthor', { name: author.display_name || t('search.unknownAuthor') }),
      ),
    );
    if (author.deleted) snip.appendChild(el('span', 'search-author-deleted', t('search.authorDeleted')));
    return snip;
  }
  if (hit.kind === 'task') {
    const task = hit.task || {};
    snip.textContent = t('search.snipTask', {
      id: String(task.id || '').slice(-6),
      status: taskStatusLabel(task.status),
    });
    return snip;
  }
  const agent = hit.agent || {};
  snip.textContent = t('search.snipAgent', {
    status: statusLabel(agent.status),
    model: agent.model_id || '',
  });
  return snip;
}

/** 一条结果行（`.result` / `.r-top` / `.r-title` / `.r-snip`）。

    今天唯一能落地的命中是**任务**（点开右侧任务线程，原型 `data-opentask` 同构），判据是
    **载荷里有没有可打开的任务标识**——不按 `kind` 再分派一次；消息命中的滚动定位与 Agent 详情面
    都还没有交付（Agent 详情是 #64），因此那两类是**非交互**行，不做「点了没反应」的按钮。 */
function searchResultNode(hit) {
  const taskId = hit.task && hit.task.id ? hit.task.id : null;
  const node = el(taskId ? 'button' : 'div', 'result');
  if (taskId) {
    node.type = 'button';
    node.dataset.opentask = taskId;
    node.setAttribute('data-od-id', 'search-task');
  }
  const top = el('div', 'r-top');
  top.appendChild(el('span', 'hash', searchSourceLabel(hit)));
  top.appendChild(el('span', 'card-tag', searchHitTypeLabel(hit)));
  node.appendChild(top);
  node.appendChild(searchHitText(hit));
  node.appendChild(searchSnipNode(hit));
  return node;
}

function searchEmptyState(titleKey, bodyKey, params) {
  const empty = el('div', 'empty');
  empty.appendChild(el('b', null, t(titleKey, params)));
  empty.appendChild(el('p', null, t(bodyKey)));
  return empty;
}

function renderSearchResults() {
  const results = $('#searchResults');
  const error = $('#searchError');
  const search = state.search;
  error.hidden = !search.error;
  error.textContent = search.error;
  if (search.error) {
    replaceChildren(results, []);
    return;
  }
  if (search.loading) {
    replaceChildren(results, [el('div', 'empty', t('search.searching'))]);
    return;
  }
  if (!search.query) {
    replaceChildren(results, [searchEmptyState('search.idleTitle', 'search.idleBody')]);
    return;
  }
  if (!search.hits.length) {
    replaceChildren(results, [
      searchEmptyState('search.emptyTitle', 'search.emptyBody', { query: search.query }),
    ]);
    return;
  }
  replaceChildren(results, search.hits.map(searchResultNode));
}

/** 四档 facet 的选中态（`.filter-chip.is-on`，原型 `:631-635` 的取值与顺序）。 */
function renderSearchFacets() {
  const row = $('#searchFacets');
  if (!row) return;
  Array.prototype.forEach.call(row.querySelectorAll('[data-facet]'), (chip) => {
    const on = chip.dataset.facet === state.search.facet;
    chip.classList.toggle('is-on', on);
    chip.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

// 输入时每次按键都打后端是浪费：把「落笔之后的一次请求」延后一拍，facet 切换与 Enter 立即执行。
const SEARCH_DEBOUNCE_MS = 120;
let searchTimer = null;

function cancelPendingSearch() {
  if (searchTimer === null) return;
  clearTimeout(searchTimer);
  searchTimer = null;
}

function scheduleSearch() {
  cancelPendingSearch();
  searchTimer = setTimeout(() => {
    searchTimer = null;
    runSearch();
  }, SEARCH_DEBOUNCE_MS);
}

/** 「搜索」入口：搜索是**主线视图**（占消息流那块位置），不是盖住界面的弹层。 */
function showSearchView() {
  closeTaskStream();
  closeTaskDock();
  state.mode = 'search';
  render();
  const input = $('#searchInput');
  input.value = state.search.query;
  input.focus();
}

function setSearchFacet(facet) {
  if (state.search.facet === facet) return;
  state.search.facet = facet;
  cancelPendingSearch();
  renderSearchFacets();
  if (state.search.query) runSearch();
  else renderSearchResults();
}

async function runSearch() {
  const input = $('#searchInput');
  const search = state.search;
  const requestVersion = ++searchRequestVersion;
  // 空格也是字面子串的一部分，不能像一般表单字段那样静默去掉首尾空白。
  search.query = input.value;
  search.error = '';
  search.hits = [];
  search.loading = false;
  if (!search.query) {
    renderSearchResults();
    return;
  }
  if (!state.online) {
    search.error = t('search.offline');
    renderSearchResults();
    return;
  }
  search.loading = true;
  renderSearchResults();
  // facet 是**后端的过滤参数**：切换档位就是换一次请求，不在前端过滤结果集。
  const params = new URLSearchParams({ q: search.query, facet: search.facet });
  try {
    const payload = await api('/api/search?' + params.toString());
    if (requestVersion !== searchRequestVersion) return;
    search.hits = Array.isArray(payload.hits) ? payload.hits : [];
  } catch (err) {
    if (requestVersion !== searchRequestVersion) return;
    // 搜索失败与零命中是两种事实：错误必须留在结果面里，不能只清空列表。
    search.error = t('search.failed', { reason: err.message });
  } finally {
    if (requestVersion === searchRequestVersion) {
      search.loading = false;
      renderSearchResults();
    }
  }
}

/* ─── ⌘K 命令面板（规格 D10） ──────────────────────────────────────────────
   命令集 = 跳转（消息视图 / 任务看板）+ 动作（新建频道 / 新建任务 / 切换详情面板）
   + **动态**频道组（当前工作台的频道，不是原型里写死的种子名）。D10 枚举的命令集里**没有**
   原型 `CMDS` 的「搜索」一条，`⌘K` 真绑定的是**打开命令面板**本身（不是搜索视图），
   因此这里也不给它挂 `⌘K` 标签——界面不承诺一个按键做不到的事。
   面板里只呈现**真绑定**的快捷键：`M` 消息视图、`T` 任务看板（`ESC` 的提示在面板自己的字段上）。
   原型的 `N` / `⇧N` / `D` 三个 kbd 标签并没有绑定任何按键，因此不出现。 */
function commandList() {
  return [
    { id: 'view-messages', group: 'cmd.groupJump', label: t('cmd.messages'), kbd: 'M', run: showMessagesView },
    { id: 'view-board', group: 'cmd.groupJump', label: t('cmd.board'), kbd: 'T', run: showTaskBoard },
    { id: 'new-channel', group: 'cmd.groupAction', label: t('rail.newChannel'), run: openChannelModal },
    { id: 'new-task', group: 'cmd.groupAction', label: t('board.newTask'), run: () => openTaskModal() },
    { id: 'toggle-dock', group: 'cmd.groupAction', label: t('dock.toggle'), run: toggleDock },
    ...state.channels.map((channel) => ({
      id: 'channel:' + channel.id,
      group: 'cmd.groupChannels',
      label: '#' + channel.name,
      run: () => selectTarget(channel.id),
    })),
  ];
}

function renderCmd(filter) {
  const query = String(filter || '').toLowerCase();
  const commands = commandList().filter((command) => command.label.toLowerCase().includes(query));
  const list = $('#cmdList');
  if (!commands.length) {
    replaceChildren(list, [el('div', 'cmd-group', t('cmd.empty'))]);
    return;
  }
  const nodes = [];
  let group = null;
  let first = true;
  commands.forEach((command) => {
    if (command.group !== group) {
      group = command.group;
      nodes.push(el('div', 'cmd-group', t(group)));
    }
    const item = el('button', 'cmd-item' + (first ? ' is-active' : ''));
    item.type = 'button';
    // 用**稳定 id** 而不是标签做归属：频道名可能与某条命令同名，标签不是键。
    item.dataset.cmd = command.id;
    item.appendChild(el('span', 'grow', command.label));
    if (command.kbd) item.appendChild(el('span', 'kbd', command.kbd));
    nodes.push(item);
    first = false;
  });
  replaceChildren(list, nodes);
}

function openCmd() {
  $('#cmdOverlay').hidden = false;
  const input = $('#cmdInput');
  input.value = '';
  renderCmd('');
  input.focus();
}

function closeCmd() {
  $('#cmdOverlay').hidden = true;
}

function runCmd(commandId) {
  const command = commandList().find((item) => item.id === commandId);
  if (!command) return;
  closeCmd();
  command.run();
}

/* ─── 工作区设置（语言偏好 / 消息密度 / 主题）：后端是唯一正本 ───────────── */

/** 把一份设置铺到界面上：`<html lang>` 与语言同一个取值、密度与主题两个档位写到根元素、
    静态文案重铺。

    它是「读回」与「写回」两条路径的**唯一**落点：启动读回、切档位写回后都走它，
    因此不存在「内存里变了但界面没变」的中间态。 */
function applySettings(settings) {
  const next = settings || {};
  state.settings = {
    locale: I18N_LOCALES.includes(next.locale) ? next.locale : I18N_SOURCE_LOCALE,
    message_density: next.message_density === 'compact' ? 'compact' : 'comfortable',
    theme: THEMES.includes(next.theme) ? next.theme : DEFAULT_THEME,
  };
  setLocale(state.settings.locale);
  // 密度档位挂在根元素上（CSS 只认 `html[data-density]` 两个取值）。
  document.documentElement.dataset.density = state.settings.message_density;
  // 主题档位同样挂在根元素上，且**三档都写**：显式 `light` / `dark` 由
  // `:root[data-theme='dark']` 命中，`system` 交给设计系统包里的
  // `@media (prefers-color-scheme: dark)`——这里不解析媒体查询，两条分支各一个落点。
  document.documentElement.dataset.theme = state.settings.theme;
  applyStaticI18n(document);
  renderSettingsModal();
  // 提醒弹窗的文案（空态、删除键的可访问名）也是词条：切语言时要与其它静态文案一起重铺，
  // 否则它会留在上一个语言。渲染只读 `state.reminders`，不发请求。
  renderRemindersModal();
  // 名册的动态文案（标题 / 副标题 / 行内角色与状态说法）由 `t()` 渲染，因此切语言时
  // 要跟设置弹窗一样重铺一遍（静态的关闭按钮由上面那句 `applyStaticI18n` 覆盖）。
  if (state.rosterChannelId) renderRoster();
  // 预算弹窗的读数标签、来源说法与写控件文案同样由 `t()` 渲染：开着就按同一份读数重铺，
  // 于是它不会留在上一门语言（读数本身是后端事实，重铺不发请求）。
  if (state.channelBudget.channelId) renderBudgetModal();
  return state.settings;
}

async function loadSettings() {
  const payload = await api('/api/settings');
  return applySettings(payload.settings);
}

/** 写一个档位：先写后端（正本），成功后按返回的设置重铺界面。

    失败时**不**改本地镜像——界面不能声称一个没落库的取值（改设置不落消息、不唤醒、
    不消耗预算，但「已保存」仍必须是后端说的）。 */
async function saveSettings(patch) {
  const box = $('#settingsFormError');
  try {
    const payload = await api('/api/settings', { method: 'PATCH', body: patch });
    if (box) box.hidden = true;
    applySettings(payload.settings);
    render();
  } catch (err) {
    if (box) {
      box.textContent = t('settings.saveFailed', { reason: err.message });
      box.hidden = false;
    }
    renderSettingsModal();
  }
}

function openSettingsModal() {
  const box = $('#settingsFormError');
  if (box) box.hidden = true;
  renderSettingsModal();
  $('#settingsOverlay').hidden = false;
  const select = $('#settingsLocaleSelect');
  if (select) select.focus();
}

function closeSettingsModal() {
  $('#settingsOverlay').hidden = true;
}

/** 三个档位的选中态与 `<select>` 的当前值都从这里同步（打开与写回后都走它，不各写一份）。 */
function renderSettingsModal() {
  const select = $('#settingsLocaleSelect');
  if (select) {
    const options = I18N_LOCALES.map((locale) => {
      const option = el('option', null, t(locale === 'en' ? 'settings.languageEn' : 'settings.languageZh'));
      option.value = locale;
      return option;
    });
    replaceChildren(select, options);
    select.value = state.settings.locale;
  }
  const group = $('#settingsDensity');
  if (group) {
    Array.prototype.forEach.call(group.querySelectorAll('[data-density]'), (card) => {
      const on = card.dataset.density === state.settings.message_density;
      card.classList.toggle('is-on', on);
      card.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  // 主题三档（浅色 / 深色 / 跟随系统）：与密度同一套 `.radio-card` 选中态。
  const themes = $('#settingsTheme');
  if (themes) {
    Array.prototype.forEach.call(themes.querySelectorAll('[data-theme]'), (card) => {
      const on = card.dataset.theme === state.settings.theme;
      card.classList.toggle('is-on', on);
      card.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
}

/* ─── 提醒（ADR-0012：库内对象，后端是唯一正本） ────────────────────────────
   列表 / 空态 / 圆点全部按**后端读数**渲染，本地不拼行、不落浏览器存储。

   四处刻意的**偏离原型**（每一条都记账）：
   1. 圆点的判据从原型 `:1886` 的「有提醒就亮」改成「**还有未到点的提醒**才亮」（C17）：
      已响 / 已错过不等待任何事，圆点不能退化成历史计数器。
   2. 新增控件是**两个原生输入**（日期 + 钟点，C15）——原型 `:1579` 只有 `type="time"`。
   3. 行形态把原型的 `.time`（到期时刻列）并进**状态后缀**（理由见 :func:`reminderStateNode`）。
   4. 删除控件用 `×` 而不是原型那个 `plus` 图标 —— 原型 `:1570` 在一个 `aria-label="删除提醒"`
      的控件里复用了「新增」的加号图标（图标与它的可访问名互相矛盾）。可视文案与可访问名不一致
      会让图标/读屏用户读到相反的动作，因此这里按本仓的关闭控件形状用 `×`；
      本仓已有先例：指派 pill 用 `<span>` 而不是原型的 `<button>`（那也是一个按下去不发生的站）。 */

/** 读提醒（正本在后端）：刷新本地镜像、圆点与弹窗列表。

    落点与写路径共用 `applyReminderPayload`——「拿到一份提醒表之后怎么铺界面」只有一处实现，
    读与写不会各自演化（读成功同样要把上一次的错误话收回去：那份状态已经不成立了）。 */
async function loadReminders() {
  applyReminderPayload(await api('/api/reminders'));
  return state.reminders;
}

/** 写路径（新增 / 删除）与读路径的**唯一落点**：用响应里的**最新**提醒表覆盖镜像，再重绘圆点与列表。

    不在本地拼一条「看起来成功了」的行：界面只能显示后端说过的那一份。 */
function applyReminderPayload(payload) {
  state.reminders = (payload && payload.reminders) || [];
  hideReminderError();
  renderReminderDot();
  renderRemindersModal();
}

function showReminderError(message) {
  const box = $('#reminderFormError');
  if (!box) return;
  box.textContent = message;
  box.hidden = false;
}

function hideReminderError() {
  const box = $('#reminderFormError');
  if (box) box.hidden = true;
}

/** 圆点**只在「还有未到点的提醒」时**出现（C17）：已响 / 已错过不等待任何事。

    判据是后端给的「已投递」标记 `fired_at`（C20），**不是条数**——只剩已到点的提醒时圆点
    必须灭掉，否则它就从「还有事等着」退化成历史计数器。这里只**消费**这个标记：`fired_at`
    的含义、以及「到没到点」的判定都在后端，前端不比对时钟（C20 把判定放在后端）。 */
function renderReminderDot() {
  const dot = $('#reminderDot');
  if (!dot) return;
  dot.hidden = !state.reminders.some((reminder) => !reminder.fired_at);
}

/** 本机当天日期（`YYYY-MM-DD`）：日期格的默认值（C15 判据 2）。

    只取「年 / 月 / 日」三段拼串，不做任何时区换算——到期时刻按**本机本地时间**解释是后端的
    口径（C7）；前端这里只是把「今天」填进原生控件，不需要建立一个第二时间正本。 */
function todayDateValue() {
  return localDateString(new Date());
}

function openRemindersModal() {
  hideReminderError();
  renderRemindersModal();
  $('#remindersOverlay').hidden = false;
  // 日期格默认**今天**：打开弹窗就能直接填文本提交，不用先手填日期（C15 判据 2）。
  // 每次打开都回到今天——这是「新提醒」的默认语义，不是记住上一次的取值。
  const dateInput = $('#reminderDate');
  if (dateInput) dateInput.value = todayDateValue();
  const input = $('#reminderText');
  if (input) input.focus();
  // 打开时按**后端正本**再读一次：别的浏览器或上一次会话设的提醒在这里出现。
  // 读不到不关弹窗，把话留在弹窗里（正本读不到时界面不能假装列表是完整的）。
  loadReminders().catch((err) => showReminderError(t('reminder.loadFailed', { reason: err.message })));
}

function closeRemindersModal() {
  $('#remindersOverlay').hidden = true;
}

/** 新增一条提醒：文本 + **日期与钟点两个原生输入**拼成的到期时刻（C15）。

    拼成 `YYYY-MM-DD HH:MM` **一个串**交给后端（ADR-0017 §二把时间语义升成「日期 + 钟点」）。
    合法形状由后端裁决（`db.py` 的写路径正则），前端不替后端猜：空的那一半不补默认值，
    形状不对就由后端回 `validation_failed`，界面把后端的话留在弹窗里。

    归属 = **当前打开的频道**（原型 `:1775` 用当前频道）；DM 视图或空工作台里没有频道可归属，
    于是显式传 `null`——后端把它读作「不归属任何频道」，与设置面那条「显式 null 整体拒绝」不同
    （档位没有「不归属」这个合法状态，提醒有）。 */
async function submitReminder() {
  const textInput = $('#reminderText');
  const dateInput = $('#reminderDate');
  const timeInput = $('#reminderTime');
  const channel = activeChannel();
  const due = [dateInput ? dateInput.value : '', timeInput ? timeInput.value : ''].join(' ').trim();
  try {
    const payload = await api('/api/reminders', {
      method: 'POST',
      body: {
        text: textInput ? textInput.value : '',
        time: due,
        channel_id: channel ? channel.id : null,
      },
    });
    applyReminderPayload(payload);
    // 只清正文：日期格回到今天、钟点格保留当前取值（原型 `#rmTime` 的默认值就是 09:00，
    // 连设两条不用重填）。
    if (textInput) textInput.value = '';
    if (dateInput) dateInput.value = todayDateValue();
  } catch (err) {
    showReminderError(t('reminder.saveFailed', { reason: err.message }));
  }
}

async function deleteReminder(reminderId) {
  try {
    const payload = await api('/api/reminders/' + encodeURIComponent(reminderId), { method: 'DELETE' });
    applyReminderPayload(payload);
  } catch (err) {
    showReminderError(t('reminder.deleteFailed', { reason: err.message }));
  }
}

/** 弹窗内容的两态：有条目就是列表，没有就是空态（照原型 `:1567` 的那条 `|| 空态`）。 */
function renderRemindersModal() {
  const list = $('#reminderList');
  if (!list) return;
  if (!state.reminders.length) {
    const empty = el('div', 'empty');
    empty.appendChild(el('b', null, t('reminder.emptyTitle')));
    empty.appendChild(el('p', null, t('reminder.emptyBody')));
    replaceChildren(list, [empty]);
    return;
  }
  replaceChildren(list, state.reminders.map(reminderRowNode));
}

/** 两个状态词条键（`pending` 没有标签键：它的后缀就是到期时刻本身，C16）。模块级常量：
    键名由 C18 钉死，不随渲染重建，也不拼成 `prefix.value` 那种动态键。 */
const REMINDER_STATE_LABEL_KEYS = { fired: 'reminder.stateFired', missed: 'reminder.stateMissed' };

/** 一条提醒的**状态后缀**节点（C16）：`pending` 时它兼作这一行的到期时刻呈现（完整后端串、
    不切片），`fired` / `missed` 时它显示前端词条文案。

    行形态的正本在这里：**到期时刻与状态后缀是同一件事的两种说法**（等哪会儿 / 已经过去了），
    所以时刻不再单立一个 `.time` 列（原型 `alibot-app.html:1567` 是分开的）——单立会让
    `pending` 行把同一时刻写两遍。这是用户 2026-10-17 对规格 C16 行形态的裁定（B：时刻只出现
    一次），属**显式偏离**，不是实现随差；偏差登记见 PR #122 正文。

    只**消费**后端给的 `state`（C20 的只读派生值），前端自己不比对时钟、不算 `fired_at − time`；
    认不出的取值（更老的后端、测试替身）落回到期时刻那一档：**不猜测**第三种语义。
    `data-state` 让 CSS 按既有档位取色（只用既有 token，不新增色位名）。 */
function reminderStateNode(reminder) {
  const stateValue = reminder.state || 'pending';
  const labelKey = Object.prototype.hasOwnProperty.call(REMINDER_STATE_LABEL_KEYS, stateValue)
    ? REMINDER_STATE_LABEL_KEYS[stateValue]
    : null;
  const node = el('span', 'state', labelKey ? t(labelKey) : reminder.time);
  node.dataset.state = stateValue;
  return node;
}

/** 一行提醒：文本（+ 归属频道）/ **状态后缀**（到期时刻 | 已提醒 | 已错过）/ 删除。
    行形态为何比原型少了行首那个 `.time`：见 :func:`reminderStateNode`。 */
function reminderRowNode(reminder) {
  const row = el('div', 'reminder-row');
  row.dataset.reminder = reminder.id;
  const grow = el('span', 'grow');
  // 正文用文本节点挂在 `.grow` 上，因为后面还要往里追加归属频道那一段
  // （`el(tag, class, text)` 是「只有一段文字」时用的；`toast()` 对同一种形态也是这么拼的）。
  grow.appendChild(document.createTextNode(reminder.text));
  if (reminder.channel_id) {
    // `· #频道名` 与原型同一形状；**不归属**时这一段不出现（可空列的正本事实）。
    grow.appendChild(el('span', 'ch', ' · #' + (reminder.channel_name || reminder.channel_id)));
  }
  row.appendChild(grow);
  row.appendChild(reminderStateNode(reminder));
  const remove = el('button', 'icon-btn', '×');
  remove.type = 'button';
  remove.dataset.deleteReminder = reminder.id;
  remove.setAttribute('aria-label', t('reminder.deleteAria'));
  remove.setAttribute('data-od-id', 'reminder-delete');
  row.appendChild(remove);
  return row;
}

/* ─── 后端状态 → 前端 ───────────────────────────────────────────────── */
async function loadWorkspace(options) {
  const settings = options || {};
  try {
    // 设置面先读：语言偏好与密度档位要在第一帧就是用户选的那一档。
    try {
      await loadSettings();
    } catch {
      // 设置读不到不影响工作台：保留当前镜像（源语言 + 舒适）并照常渲染主流程——
      // 「后端是否可达」由下面那条快照请求判定，不在这里抢先下结论。
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
    // 清单已就位就立刻刷已打开的弹窗：下面每一步都是 `await`，任何一步抛错都会跳到 catch，
    // 而已经拿到的 provider 快照不该因为「其它请求挂了」而白白丢掉。
    syncOpenAgentModal();
    await refreshTasks();
    loadRemoteFlow();
    // 提醒的读取不阻塞主线：读不到不影响工作台（与 `loadRemoteFlow` 同一条，打开弹窗时会再试一次
    // 并在弹窗里说明），读到了圆点与列表按后端说话。
    loadReminders().catch(() => {});
    const known = state.channels.some((channel) => channel.id === state.activeChannelId)
      || state.dms.some((dm) => dm.id === state.activeChannelId);
    if (!known) {
      if (settings.keepActive === false) {
        setActiveChannelId(null);
      } else {
        const remembered = readRememberedChannelId();
        const target = state.channels.find((channel) => channel.id === remembered)
          || state.dms.find((dm) => dm.id === remembered);
        setActiveChannelId(target ? target.id : null);
      }
    }
    await refreshMessages();
    await refreshOutcomes();
    await refreshChannelMembers();
    await refreshStatuses();
    syncStreams();
  } catch (err) {
    state.online = false;
    state.ready = true;
    applyOutcomes([]);
    state.expandedOutcomes = {};
    if (!settings.silent) toast(t('load.workspaceFailed', { reason: err.message }), 'err');
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
  const target = activeTarget();
  if (!target) {
    state.messages = [];
    return;
  }
  const payload = await api(targetPath(target.id, '/messages'));
  state.messages = payload.messages;
  replaceTarget(payload.dm || payload.channel);
  seenSeqsFor(target.id).clear();
  payload.messages.forEach((message) => seenSeqsFor(target.id).add(message.seq));
}

async function refreshOutcomes() {
  const channel = activeChannel();
  if (!channel) {
    applyOutcomes([]);
    return;
  }
  try {
    const payload = await api(channelPath(channel.id, '/round-outcomes'));
    applyOutcomes(payload.round_outcomes || []);
  } catch {
    // 轮次结果读不到不影响消息流（与 refreshStatuses 同一条纪律）：保留上一次已知值，
    // 也不把整个工作台判成「后端不可达」。
  }
}

// 轮次结果的两份索引，都从**同一批**后端行建、在**同一处**换，免得两份各自漂：
//   - `byBase`：按**触发它的**那条消息的 `base_seq` + 成员分组（摘要行与失败行按它挂）；
//   - `byReply`：按**它产出的**那条回复消息的 `reply_seq` 直取（旁白行展开看 reason 按它查）。
// `reply_seq` 是后端写进 `round_outcome` 的取证字段（`replied` 之外为 `null`），不是前端推断。
function applyOutcomes(records) {
  const byBase = {};
  const byReply = {};
  records.forEach((record) => {
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

/* ─── 任务看板与任务线程 ───────────────────────────────────────────────
   看板是**全工作区视图**（`CONTEXT.md` **任务看板**：可按频道筛选）——「任务恰好属于一个频道」
   是归属事实，筛选是呈现，两者不是一回事，所以「全部频道」是一个显式选项。
   频道页签是其中一个入口：从它进来时默认筛到**当前频道**（左栏视角仍是全部）。
   任务线程挂在**右侧 dock**（`dockMode`），与频道成员面板共用那个 380px 容器。 */
async function refreshTasks() {
  try {
    const query = state.taskFilterChannelId
      ? '?channel_id=' + encodeURIComponent(state.taskFilterChannelId)
      : '';
    const payload = await api('/api/tasks' + query);
    state.boardTasks = payload.tasks || [];
    // `state.tasks` 是「未筛选的全量」：频道页签计数、卡片查名、以及刷新分派的默认落点都用它。
    // 筛选态下它就是另一个查询，所以再读一次（图省一次请求而拿筛选后的清单冒充全量，
    // 会让未筛选的频道计数偏低）。
    if (state.taskFilterChannelId) {
      const all = await api('/api/tasks');
      state.tasks = all.tasks || [];
    } else {
      state.tasks = state.boardTasks.slice();
    }
    if (state.activeTaskId) {
      const current = state.tasks.find((task) => task.id === state.activeTaskId);
      if (current) state.activeTask = current;
    }
  } catch {
    // 任务面读失败不影响频道消息与发送；保留最后一次成功快照。
  }
  renderTaskBoard();
}

/** 「我的任务」：指派给我（`assignee_member_id === owner`）或还没人认领的任务。
    它**不是**「我创建的任务」——任务不记录创建者归属。 */
function isMyTask(task) {
  return !task.assignee_member_id || task.assignee_member_id === state.member.id;
}

function visibleBoardTasks() {
  return state.taskFilterMine ? state.boardTasks.filter(isMyTask) : state.boardTasks;
}

function taskMemberName(memberId) {
  const member = state.members.find((item) => item.id === memberId);
  return member ? member.display_name : memberId;
}

/** 任务在成员表里的下标（决定 `--av-*` 色位；与消息流同一个头像色位规则）。 */
function memberSlotOf(memberId) {
  const member = state.members.find((item) => item.id === memberId);
  return member ? member.avatar_slot : 0;
}

function taskById(taskId) {
  return (state.activeTask && state.activeTask.id === taskId ? state.activeTask : null) ||
    state.tasks.find((task) => task.id === taskId) ||
    state.boardTasks.find((task) => task.id === taskId) || null;
}

function renderTabs() {
  const target = activeTarget();
  const isDm = Boolean(target) && target.kind === 'dm';
  const messagesTab = $('#tabMessages');
  const tasksTab = $('#tabTasks');
  if (!messagesTab || !tasksTab) return;
  // DM 里没有任务板这个概念（任务恰好属于一个频道）：整个页签不出现，而不是点开看空态。
  tasksTab.hidden = isDm;
  const selected = state.mode === 'board' ? 'tasks' : 'messages';
  [messagesTab, tasksTab].forEach((tab) => {
    if (tab.hidden) return;
    const active = tab.dataset.tab === selected;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  $('#msgCount').textContent = target ? renderedMessages().length : 0;
  $('#taskCount').textContent = isDm ? 0 : channelTaskCount();
}

/** 当前频道的任务数（该频道 = 当前 target；与看板的频道筛选无关）。 */
function channelTaskCount() {
  const channelId = state.activeChannelId;
  if (!channelId) return 0;
  return state.tasks.filter((task) => task.channel_id === channelId).length;
}

function fillTaskChannelSelect(select, includeAll, selected) {
  const options = [];
  if (includeAll) {
    const all = el('option', null, t('board.allChannels'));
    all.value = '';
    options.push(all);
  } else {
    const prompt = el('option', null, state.channels.length ? t('task.selectChannel') : t('task.createChannelFirst'));
    prompt.value = '';
    options.push(prompt);
  }
  state.channels.forEach((channel) => {
    const option = el('option', null, '#' + channel.name);
    option.value = channel.id;
    options.push(option);
  });
  replaceChildren(select, options);
  select.value = selected || '';
  if (!includeAll) select.disabled = state.channels.length === 0;
}

/** 一个筛选 chip（原型 `#filterMine` 的形态）：`.is-on` 只用 `--selected*` 族。 */
function filterChip(label, on, dataset) {
  const chip = el('button', 'filter-chip' + (on ? ' is-on' : ''));
  chip.type = 'button';
  chip.setAttribute('aria-pressed', on ? 'true' : 'false');
  Object.keys(dataset || {}).forEach((key) => { chip.dataset[key] = dataset[key]; });
  chip.textContent = label;
  return chip;
}

function renderBoardToolbar() {
  const filters = $('#boardFilters');
  if (!filters) return;
  const nodes = [
    // 「我的任务」= 指派给我或未认领（任务不记录创建者归属，所以这不是「我建的」）。
    filterChip(t('board.mine'), state.taskFilterMine, { boardMine: '1', odId: 'filter-mine' }),
    el('span', 'filter-sep'),
    filterChip(t('board.allChannels'), state.taskFilterChannelId === '', { boardChannel: '', odId: 'filter-channel-all' }),
  ];
  state.channels.forEach((channel) => {
    nodes.push(
      filterChip(
        '#' + channel.name,
        state.taskFilterChannelId === channel.id,
        { boardChannel: channel.id, odId: 'filter-channel-' + channel.id },
      ),
    );
  });
  replaceChildren(filters, nodes);
}

/** 一张看板卡片（原型 `cardHtml()`）：标题 + mono 序号 + 频道 tag + 指派者 / 未认领。
    它是 `<button>`（可点击打开 dock 线程），**不**带 `draggable`：拖拽属 #26。 */
function taskCardNode(task) {
  const card = el('button', 'card');
  card.type = 'button';
  card.dataset.task = task.id;
  card.dataset.status = task.status;
  card.setAttribute('data-od-id', 'task-card-' + task.id);
  card.setAttribute('aria-label', t('board.cardAria', { title: task.title, status: taskStatusLabel(task.status) }));
  card.appendChild(el('div', 'card-title', task.title));
  const meta = el('div', 'card-meta');
  meta.appendChild(el('span', 'card-num', '#' + String(task.id).slice(-6)));
  meta.appendChild(el('span', 'card-tag', '#' + task.channel_name));
  if (task.assignee_member_id) {
    const owner = el('span', 'card-owner');
    owner.appendChild(avatarNode(taskMemberName(task.assignee_member_id), memberSlotOf(task.assignee_member_id), 'sm'));
    owner.appendChild(document.createTextNode(taskMemberName(task.assignee_member_id)));
    meta.appendChild(owner);
  } else {
    meta.appendChild(el('span', 'card-owner unassigned', t('task.unassigned')));
  }
  card.appendChild(meta);
  if (task.unread_count > 0) {
    const unread = el('span', 'card-unread', task.unread_count > 99 ? '99+' : task.unread_count);
    unread.setAttribute('aria-label', t('board.unreadAria', { count: task.unread_count }));
    meta.appendChild(unread);
  }
  return card;
}

/** 看板列（`TASK_STATUS_ORDER` 五态）：固定 `--board-col-w`(236px) → 超出时容器横向滚动。 */
function boardColumns() {
  const tasks = visibleBoardTasks();
  return TASK_STATUS_ORDER.map((status) => {
    const items = tasks.filter((task) => task.status === status);
    const column = el('section', 'col');
    column.dataset.status = status;
    column.setAttribute('data-od-id', 'task-column-' + status);
    const head = el('div', 'col-head');
    head.appendChild(el('span', 'st-dot'));
    head.appendChild(el('b', null, taskStatusLabel(status)));
    head.appendChild(el('span', 'col-count', String(items.length)));
    column.appendChild(head);
    const body = el('div', 'col-body');
    if (items.length) items.forEach((task) => body.appendChild(taskCardNode(task)));
    // 空态只陈述事实。「拖拽任务到此」是原型文案，但拖拽属 #26、现在拖不动，
    // 写上去就是一句假话（`SKILL.md` §7 的「不实现就不摆样子」同一条纪律）。
    else body.appendChild(el('div', 'drop-hint', t('board.emptyColumn')));
    column.appendChild(body);
    return column;
  });
}

/** 列表视图：`.seg` 的另一半，`max-width:880px` 居中（原型 `#taskListWrap`）。 */
function boardList() {
  const tasks = visibleBoardTasks();
  if (!tasks.length) {
    const empty = el('div', 'empty');
    empty.appendChild(el('b', null, t('board.noMatchTitle')));
    empty.appendChild(el('p', null, t('board.noMatchBody')));
    return empty;
  }
  const wrap = el('div');
  tasks.forEach((task) => {
    const row = el('button', 'result');
    row.type = 'button';
    row.dataset.task = task.id;
    row.setAttribute('data-od-id', 'task-row-' + task.id);
    row.setAttribute('aria-label', t('board.cardAria', { title: task.title, status: taskStatusLabel(task.status) }));
    const top = el('div', 'r-top');
    top.appendChild(el('span', 'st-dot'));
    top.appendChild(el('span', null, taskStatusLabel(task.status)));
    top.appendChild(el('span', 'hash', '#' + String(task.id).slice(-6)));
    top.appendChild(el('span', 'card-tag', '#' + task.channel_name));
    top.appendChild(el('span', 'r-open', t('board.openThread')));
    row.appendChild(top);
    row.appendChild(el('div', 'r-title', task.title));
    const snip = el('div', 'r-snip');
    if (task.assignee_member_id) {
      snip.appendChild(avatarNode(taskMemberName(task.assignee_member_id), memberSlotOf(task.assignee_member_id), 'sm'));
      snip.appendChild(document.createTextNode(taskMemberName(task.assignee_member_id)));
    } else {
      snip.appendChild(el('span', 'card-owner unassigned', t('task.unassigned')));
    }
    if (task.unread_count > 0) {
      const unread = el('span', 'card-unread', task.unread_count);
      unread.setAttribute('aria-label', t('board.unreadAria', { count: task.unread_count }));
      snip.appendChild(unread);
    }
    row.appendChild(snip);
    row.dataset.status = task.status;
    wrap.appendChild(row);
  });
  return wrap;
}

function renderTaskBoard() {
  const columns = $('#taskColumns');
  const list = $('#taskListWrap');
  if (!columns || !list) return;
  renderBoardToolbar();
  const creating = $('#taskCreateSubmit');
  if (creating) creating.disabled = !state.online || state.channels.length === 0;
  const createChannel = $('#taskCreateChannel');
  if (createChannel) {
    // 新建任务的默认频道：当前频道优先（人在这个频道里按「新建任务」，就该建在这里）。
    const selected = createChannel.value || state.activeChannelId || '';
    fillTaskChannelSelect(createChannel, false, selected);
  }
  const isList = state.taskView === 'list';
  columns.hidden = isList;
  list.hidden = !isList;
  if (isList) {
    replaceChildren(list, [boardList()]);
    return;
  }
  replaceChildren(columns, boardColumns());
}

/** `.seg` 的选中态与 `aria-selected` 只在**一处**同步（切换与重渲染都走它）。 */
function syncTaskViewSeg() {
  document.querySelectorAll('.seg [data-taskview]').forEach((button) => {
    const on = button.dataset.taskview === state.taskView;
    button.classList.toggle('is-active', on);
    button.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

/** 任务线程（dock 形态 B，原型的 `.tt-*`）。

    「讨论」与「状态变更记录」是**同一个数据源的两个视图**：后端把每次任务变更
    （创建 / 标题 / 状态 / 指派）追加成一条 `kind='system'` 消息进该 task 的 target 消息流
    （`CONTEXT.md` **任务**、ADR-0006）。`kind='system'` 的那些行进「状态变更记录」
    与「关联消息」的语义分得开：后者是**人类 / Agent 主动发的、带 `task_ref` 指针的消息**
    （`CONTEXT.md` **指针消息**），一个 kind 不会同时落在两边。

    原型那个伪造的 `TASK_HISTORY` 常量不移植：历史就是这一条流。 */
function renderTaskThread() {
  const task = state.activeTask;
  if (!task) return;
  const channelName = task.channel_name;
  const linked = linkedTaskMessages(task);
  const history = state.taskMessages.filter((message) => message.kind === 'system');
  const systemIds = new Set(history.map((message) => message.id));
  const replies = state.taskMessages.filter((message) => !systemIds.has(message.id));

  // ── dock 头部：「任务线程」tag + mono #id + 「#频道名 · 关联消息 N 条」──
  $('#ttHeadId').textContent = '#' + String(task.id).slice(-6);
  const unread = Number(task.unread_count) || 0;
  $('#ttHeadSub').textContent = t('task.threadHeadSub', { channel: channelName, count: linked.length })
    + (unread ? t('task.threadUnread', { count: unread }) : '');

  // ── 摘要：标题（就地可编辑）+ 状态 pill + 指派 pill ──
  const titleInput = $('#ttTitleInput');
  if (document.activeElement !== titleInput) titleInput.value = task.title;
  const status = $('#ttStatus');
  // 状态色位由 CSS 的 `.tt-status[data-status=…]` 表驱动（与 `.col` / `.card` 共用同一张表），
  // 这里只声明状态，不内联色位：同一个映射存在两份就会漂移。
  status.dataset.status = task.status;
  $('#ttStatusText').textContent = taskStatusLabel(task.status);
  renderAssigneePill(task);

  // ── 推进 chips：**只**出现迁移集合里的目标（`closed` 是终态 ⇒ 一个都不出）──
  const transitions = TASK_STATUS_TRANSITIONS[task.status] || [];
  if (transitions.length) {
    replaceChildren($('#ttTransitions'), transitions.map((target) => {
      const chip = filterChip(
        taskTransitionLabel(target),
        false,
        { ttMove: target, odId: 'thread-move-' + target },
      );
      chip.disabled = !state.online;
      return chip;
    }));
  } else {
    // 原型那句「已关闭，无可用推进」在这里是**真话**（`closed` 确实是终态）。
    replaceChildren($('#ttTransitions'), [el('span', 'tt-note', t('task.noTransitions'))]);
  }

  // ── 指派 chips：该频道成员里的 Agent（见 `renderAssignChips` 的候选来源）──
  renderAssignChips(task);

  // ── 关联消息 ──
  const linkedBox = $('#ttLinked');
  if (linked.length) {
    const wrap = el('div', 'tt-linked');
    linked.forEach((message) => {
      const row = el('div', 'tt-linked-row');
      const top = el('div', 'tt-linked-top');
      top.appendChild(el('span', 'hash', '#' + channelName));
      top.appendChild(el('span', null, message.author ? message.author.display_name : t('search.unknownAuthor')));
      top.appendChild(el('span', 'card-tag', timeLabel(message.created_at)));
      row.appendChild(top);
      row.appendChild(el('div', 'tt-linked-body', message.body));
      appendAttachmentList(row, message);
      wrap.appendChild(row);
    });
    replaceChildren(linkedBox, [wrap]);
  } else {
    replaceChildren(linkedBox, [el('div', 'tt-empty', t('task.linkedEmpty'))]);
  }

  // ── 讨论 · N 条 ──
  $('#ttReplyTitle').textContent = t('task.discussionCount', { count: replies.length });
  replaceChildren(
    $('#ttReplies'),
    replies.length
      ? replies.map((message) => {
        const row = el('div', 'tt-reply-row');
        const author = message.author || state.member;
        row.appendChild(avatarNode(author.display_name, author.avatar_slot, 'sm'));
        const body = el('div', 'msg-body');
        const head = el('div', 'msg-head');
        head.appendChild(el('span', 'msg-author' + (author.kind === 'agent' ? ' is-agent' : ''), author.display_name));
        if (author.kind === 'agent') head.appendChild(el('span', 'msg-tag', 'Agent'));
        head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
        body.appendChild(head);
        // 正文直接用消息流同一套渲染（`.msg-text`，ADR-0016 的 14px / 68ch）：
        // 提及高亮、换行与量例都与主线一致，不另造一套（也不多套一层壳）。
        body.appendChild(messageText(message));
        appendAttachmentList(body, message);
        appendTaskRefChip(body, message);
        row.appendChild(body);
        return row;
      })
      : [el('div', 'tt-empty', t('task.repliesEmpty'))],
  );

  // ── 状态变更记录：该流的 `system` 行（不另行伪造历史）──
  const log = $('#ttHistory');
  replaceChildren(
    log,
    history.length
      ? history.map((message) => {
        const li = el('li');
        li.appendChild(document.createTextNode((message.author ? message.author.display_name + ' ' : '') + message.body));
        li.appendChild(el('span', 'ts', timeLabel(message.created_at)));
        return li;
      })
      : [el('div', 'tt-empty', t('task.historyEmpty'))],
  );
  // 最近一次变更用 graphic 档的点标出来（原型 `.is-now`；不是新色位）。
  const last = log.lastElementChild;
  if (last && last.tagName === 'LI') last.classList.add('is-now');
}

/** 关联消息 = 任务线程里**带 `task_ref` 指针的非 system 消息**。

    数据面的事实：`task_ref` 必须与消息同 target（`db.py:append_message` 的校验，
    `test_tasks.py` 逐条盯住），所以在任务线程里能出现的指针**就是指向这个任务自己的**。
    于是这一段与「讨论」必然有重叠（一条回复可以同时挂了指针）——**这是数据本身的样子，
    不是漏了去重**：票面明确说这两段是同一数据源的两个视图，`task_ref` 与 `kind='system'`
    是两个正交字段（刚创建的 `system` 行 task_ref 为 null，人类回复可以带 task_ref），
    所以「关联消息 ⊆ 讨论」是这个域的语义，而不是冗余展示。
    该指针在「讨论」里另外以 `.task-ref-chip` 就地显示（已有行为，不另造一套）。 */
function linkedTaskMessages(task) {
  return state.taskMessages.filter((message) => message.kind !== 'system' && message.task_ref === task.id);
}

/** 指派 pill：点击展开 / 收起下方的指派 chips（原型：pill 只提示，chips 才是入口）。 */
function renderAssigneePill(task) {
  const pill = $('#ttAssignee');
  const assigned = task.assignee_member_id;
  if (!assigned) {
    pill.className = 'assignee unassigned';
    pill.textContent = t('task.unassignedPill');
    return;
  }
  pill.className = 'assignee';
  replaceChildren(pill, []);
  pill.appendChild(avatarNode(taskMemberName(assigned), memberSlotOf(assigned), 'sm'));
  pill.appendChild(document.createTextNode(taskMemberName(assigned)));
  pill.appendChild(el('span', 'assignee-hint', t('task.reassignHint')));
}

/** 指派 chips：**该任务所属频道的 Agent 成员**。

    为什么不拿 `GET /api/tasks/{id}/members`（target 成员）当候选：那张表 = {owner} ∪ {当前指派者}
    （`create_task` 只写入 owner，`update_task_assignee` 随后把被指派者加进去）——
    拿它当候选就永远只能「取消指派」，换不了人。票面写的就是「指派 chip 用该频道成员里的 Agent」。
    后端写路径不限制候选必须已在频道里（只校「未删除的 Agent」），所以频道成员是
    这里的正确候选面；线程成员表仍用于判断「该 target 现有哪些 Agent」。 */
function renderAssignChips(task) {
  const channelAgents = channelAgentMembers(task.channel_id);
  const box = $('#ttAssign');
  if (channelAgents === null) {
    replaceChildren(box, [
      el('span', 'tt-note', channelMembersFailed.has(task.channel_id)
        ? t('task.assignUnavailable')
        : t('task.assignLoading')),
    ]);
    return;
  }
  if (!channelAgents.length) {
    replaceChildren(box, [
      el('span', 'tt-note', t('task.assignNoAgents')),
    ]);
    return;
  }
  const chips = channelAgents.map((member) => {
    const chip = filterChip(member.display_name, task.assignee_member_id === member.id, {
      ttAssign: member.id,
      odId: 'thread-assign-' + member.id,
    });
    chip.insertBefore(avatarNode(member.display_name, member.avatar_slot, 'sm'), chip.firstChild);
    chip.disabled = !state.online;
    return chip;
  });
  if (task.assignee_member_id) {
    const clear = filterChip(t('task.clearAssign'), false, { ttAssign: '', odId: 'thread-unassign' });
    clear.disabled = !state.online;
    chips.push(clear);
  }
  replaceChildren(box, chips);
}

/** dock 形态切换：频道成员（`agent`）↔ 任务线程（`task`）↔ Agent 详情（`detail`）。

    与消息流不在同一个维度：看板仍在主线（`state.mode === 'board'`），
    而任务线程 / Agent 详情只是 dock 里显示什么。三种形态共用同一个 380px 容器，因此
    「同时显示两件东西」在构造上不可能发生（一次只有一种为可见）。

    形态本身**不单独存一份**：它由「现在打开着哪一个对象」推导（`activeTaskId` 优先于
    `activeAgentId`），于是「关闭了但 dock 还停在旧形态」这类漂移没有落脚点。

    `reveal` = 这一次是**人显式的打开**（点看板卡片 / 点左栏 Agent 行）：它清掉
    `dockDismissed`。整体重绘（`render()`）走默认的 `false`，于是人按过的收起不会被下一次
    重绘推翻 —— 「看起来收起了、下一帧又回来」等于没有出口。 */
function setDockMode(mode, reveal) {
  state.dockMode = mode;
  if (reveal) state.dockDismissed = false;
  const isTask = mode === 'task' && state.activeTaskId;
  const isDetail = mode === 'detail' && state.activeAgentId;
  $('#dockAgent').hidden = Boolean(isTask || isDetail);
  $('#dockTask').hidden = !isTask;
  $('#dockDetail').hidden = !isDetail;
  // 滑入放在**形态定下来之后**：≤900px 打开抽屉要把焦点送进第一个可聚焦控件，
  // 而那一步要跳过 `hidden` 子树 —— 先开抽屉会把焦点送给一个还在 `hidden` 里的控件。
  if ((isTask || isDetail) && !state.dockDismissed) setDockOpen(true);
}

function setViewMode(mode) {
  state.mode = mode;
  syncChromeVisibility(mode);
  $('#viewMessages').hidden = mode !== 'channel';
  // 搜索是**主线视图**，与消息流 / 看板共用同一块内容区（原型 `renderViews()` 的三选一）。
  $('#viewSearch').hidden = mode !== 'search';
  // 看板是**唯一**的任务主线视图：任务线程不再占用主线（它搬进了 dock）。
  // dock 的形态与主线视图是两个维度（原型 `renderDock()` 同样独立于 `renderViews()`）：
  // 从频道消息里的任务 chip 打开线程时，主线仍是消息流，dock 里就是那条线程。
  $('#taskBoard').hidden = mode !== 'board';
  renderTabs();
}

/** 频道 header（含「消息 / 任务」页签）与 composer 的可见性。

    两个页签在**同一内容区**切换，因此 header 在消息页与任务页**都**可见——原型
    `renderViews()` 写的是 `$('#chanHead').hidden = search;`，只在搜索视图隐藏它，
    任务视图下 header 与页签本身就是可见的。收起 header 等于把页签一起收走。
    composer 只属于消息页（原型同理：`$('#composer').hidden = search || tasks`）。
    两处调用（`setViewMode` 与 `renderHeader`）共用这一条规则，不各写一遍。 */
function syncChromeVisibility(mode) {
  const hasTarget = Boolean(activeTarget());
  $('#chanHead').hidden = !hasTarget || mode === 'search';
  $('#composer').hidden = !hasTarget || mode !== 'channel';
}

/** 「任务」页签：看板（全工作区视图 + 频道筛选，见 #44 重做其结构）。

    从频道页签进来默认筛到**当前频道**（人在 #甲 的任务里，就该先看 #甲 的任务）。
    看板本身仍是全工作区视图：工具栏上的「全部频道」chip 回到全部任务，不靠入口区分。 */
async function showTaskBoard() {
  closeTaskStream();
  closeTaskDock();
  state.mode = 'board';
  state.taskFilterChannelId = state.activeChannelId || '';
  setViewMode('board');
  render();
  await refreshTasks();
}

/** 「消息」页签：消息流 + composer（同一条流的既有渲染，不重取—— SSE 一直在线跟踪）。
    它也是搜索视图的退出口（`ESC` 与命令面板的「消息视图」都落到这里）。 */
function showMessagesView() {
  closeTaskStream();
  closeTaskDock();
  state.mode = 'channel';
  setViewMode('channel');
  render();
  markCurrentRead();
}

/* ─── 移动端抽屉壳（≤900px）：左抽屉（rail）/ 遮罩 / 右抽屉（dock） ──────────
   类名与断点照原型 `alibot-app.html:459-495` 与切片 `screens/mobile-shell.html:14-18`，
   规格决策 D11 把 `.mobile-topbar` / `.rail-scrim` / `.is-open` 三个名字定为契约。

   呈现态只有一个正本：`.is-open`（CSS 在 ≤900px 认它）。`hidden` 保留它原本的语义
   ——`rail` 从来没有这个属性，`dock` 的 `hidden` 是「这里没有内容 / 人把它收起来了」；
   ≤900px 下它由 CSS 改写成「离屏 + 不可见」，于是开着没有只看那一个类。

   键盘可达：抽屉在 ≤900px 关着时是 `visibility: hidden` 的离屏件，Tab 命不中它；
   打开时把焦点送进抽屉的第一个控件、收起时还给触发它的控件，于是焦点不会停在
   一个屏幕外、看不见的控件上。这一条只在移动壳里做（`mobileShell()`），
   桌面的焦点行为一个字不动。 */

/** 移动壳是否生效。这里是 JS 侧唯一知道那个断点的地方，取值必须与 `app.css` 的
    `@media (max-width: 900px)` 一致 —— 它只用来决定「要不要替抽屉搬焦点」。
    （节点本身在不在屏幕上、可不可见，一律由 CSS 裁决，JS 不镜像一套。） */
const MOBILE_SHELL_QUERY = '(max-width: 900px)';

function mobileShell() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return Boolean(window.matchMedia(MOBILE_SHELL_QUERY).matches);
}

function focusNode(node) {
  if (node && typeof node.focus === 'function') node.focus();
}

/** 抽屉里第一个能接焦点的控件（跳过 `hidden` 子树与禁用件）。 */
function firstFocusable(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return null;
  const nodes = root.querySelectorAll('button, a[href], input, select, textarea, [tabindex]');
  for (const node of nodes) {
    if (node.disabled) continue;
    if (typeof node.closest === 'function' && node.closest('[hidden]')) continue;
    return node;
  }
  return null;
}

/** 焦点现在在不在这个容器里（收起抽屉时据此决定要不要把焦点还回去）。 */
function focusInside(container) {
  const active = typeof document !== 'undefined' ? document.activeElement : null;
  if (!active || !container || typeof container.contains !== 'function') return false;
  return container.contains(active);
}

function railOpen() {
  const rail = $('#rail');
  return Boolean(rail && rail.classList.contains('is-open'));
}

/** 开左抽屉：`.is-open` + 遮罩出 + `aria-expanded=true`（三者同步，不靠人记）。 */
function openRail() {
  const rail = $('#rail');
  if (!rail) return;
  rail.classList.add('is-open');
  const scrim = $('#railScrim');
  if (scrim) scrim.hidden = false;
  const toggle = $('#railToggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'true');
  if (mobileShell()) focusNode(firstFocusable(rail));
}

/** 收左抽屉（scrim 点击 / ESC / 选中一个导航行共用它）。 */
function closeRail() {
  const rail = $('#rail');
  if (!rail) return;
  rail.classList.remove('is-open');
  const scrim = $('#railScrim');
  if (scrim) scrim.hidden = true;
  const toggle = $('#railToggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
  if (mobileShell()) focusNode(toggle);
}

/** dock 的开合：`hidden` 与 `.is-open` 必须同进同出（票面：状态与类名同步），
    因此所有写 `dock.hidden` 的地方都走这里，不各写一遍。
    「人收起来了」这一位也在这里落 —— 它是 `setDockMode()` 自动显示时要看的那一位。 */
function setDockOpen(open) {
  const dock = $('#dock');
  if (!dock) return;
  const wasOpen = !dock.hidden;
  dock.hidden = !open;
  dock.classList.toggle('is-open', open);
  state.dockDismissed = !open;
  const toggle = $('#btnDockToggle');
  if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (!mobileShell()) return;
  if (open && !wasOpen) focusNode(firstFocusable(dock));
  else if (!open && wasOpen && focusInside(dock)) focusNode(toggle);
}

/** 详情面板的开合（rail 图标与命令面板里的「切换详情面板」共用同一个动作，不各写一遍）。 */
function toggleDock() {
  const dock = $('#dock');
  if (dock) setDockOpen(dock.hidden);
}

/** 关闭 dock 里的某一个形态之后**落到哪里**——两条关闭路径（`closeAgentDetail()` /
    `closeTaskDock()`）共用这一份判据，不各写一份。

    顺序：仍打开着任务线程 → 任务线程形态；仍打开着 Agent 详情 → 详情形态；
    当前有打开的频道 → 频道成员形态；否则 → `null`（收起 dock）。

    「当前有打开的频道」的唯一判据是既有的 `activeChannel()`：`state.channels` 里 id 等于
    `state.activeChannelId` 的那一项。于是「没有任何 target」「只打开着 DM」「`activeChannelId`
    指向一个已经不存在的频道」这三种情况**同样**落到 `null` —— 没有频道就没有「频道成员」形态，
    不把成员面板留在右侧（用户 2026-10-16 的裁定）。

    调用方先把「正在关闭的那一个对象」清掉再问这里，因此「关闭详情」永远看不到 `activeAgentId`、
    「关闭线程」永远看不到 `activeTaskId`：每个入口只会落到**另一个**仍打开着的形态或收起。 */
function dockFallbackMode() {
  if (state.activeTaskId) return 'task';
  if (state.activeAgentId) return 'detail';
  if (activeChannel()) return 'agent';
  return null;
}

/** 两条关闭路径共用的收尾：有落点就切到那个形态，没有落点就收起 dock。
    收起时**形态仍落回「频道成员」**，于是人下一次显式点开开关（`toggleDock()`）看到的是
    `renderDock()` 的空态提示，而不是一帧过期的 Agent 详情。 */
function settleDockFallback() {
  const mode = dockFallbackMode();
  setDockMode(mode || 'agent');
  if (!mode) setDockOpen(false);
}

/** 关闭 dock 的任务线程，回到**另一个仍打开着**的形态（卡片与线程都留在看板上）。

    落点判据与关闭 Agent 详情共用（`settleDockFallback()`），但**只在这次确实在关一条线程时**才走它：
    切搜索 / 切看板 / 切消息流（ESC / M / T / 命令面板）也调这个函数，那些调用点只是「顺手确保
    线程关着」，并没有人在关 dock 里的东西 —— 它们的既有行为是**不碰 dock 的开合**（桌面 dock 是
    常驻列，ESC 不管它），因此这里原样保留。

    于是「有频道 → 频道成员；否则收起」这条规则只落在两个**真的**在关闭的入口上：
    `#btnThreadClose`（以及 `openTask()` 的失败回退）与 `#btnAgentDetailClose`。 */
function closeTaskDock() {
  closeTaskStream();
  const hadThread = Boolean(state.activeTaskId);
  state.activeTaskId = null;
  state.activeTask = null;
  state.taskMessages = [];
  state.taskSeenSeqs = new Set();
  if (hadThread) settleDockFallback();
  else setDockMode(state.activeAgentId ? 'detail' : 'agent');
}

function taskMaxSeq() {
  return state.taskMessages.reduce((max, message) => Math.max(max, Number(message.seq) || 0), 0);
}

/** 点看板卡片：在**右侧 dock** 开任务线程（主线仍留在看板上）。

    打开线程会**关掉** Agent 详情：dock 一次只显示一种形态，两者同时「打开着」会让关闭时的
    落点变成猜谜。 */
async function openTask(taskId) {
  closeTaskStream();
  state.activeAgentId = null;
  state.activeTaskId = taskId;
  state.activeTask = taskById(taskId);
  state.taskMessages = [];
  state.taskSeenSeqs = new Set();
  // `reveal`：点卡片是**人显式的打开**，于是它压过「上一次人把 dock 收起来了」这一位。
  setDockMode('task', true);
  renderTaskThread();
  try {
    // 任务线程成员表（`GET /api/tasks/{id}/members`）**不取**：指派 chip 的候选是**频道**的
    // Agent 成员（见 `renderAssignChips`），target 成员表没有消费者，取它只是多一次请求。
    const thread = await api(taskPath(taskId, '/messages'));
    if (state.activeTaskId !== taskId) return;
    state.activeTask = thread.task;
    state.taskMessages = thread.messages || [];
    state.taskSeenSeqs = new Set(state.taskMessages.map((message) => message.seq));
    // 看板是全工作区视图，任务所属频道可能并非当前频道：按需单独拉该频道的成员表。
    if (channelAgentMembers(thread.task.channel_id) === null) {
      await refreshChannelMembers(thread.task.channel_id);
    }
    const index = state.tasks.findIndex((task) => task.id === taskId);
    if (index >= 0) state.tasks[index] = thread.task;
    else state.tasks.push(thread.task);
    setDockMode('task');
    renderTaskThread();
    connectTaskStream(taskId, taskMaxSeq());
    await markTaskRead(taskId, taskMaxSeq());
    $('#ttScroll').scrollTop = $('#ttScroll').scrollHeight;
  } catch (err) {
    // 落点交给 `closeTaskDock()` 的共用判据（有频道 → 成员面板；否则收起 dock），
    // 这里不再另写一份 `setDockMode('agent')`——那会把「没有频道就没有成员面板」又推翻一次。
    closeTaskDock();
    renderTaskBoard();
    toast(t('toast.openTaskFailed', { reason: err.message }), 'err');
  }
}

async function markTaskRead(taskId, seq) {
  if (!seq) return;
  try {
    const payload = await api(taskPath(taskId, '/read'), { method: 'POST', body: { seq } });
    if (state.activeTaskId === taskId) state.activeTask = payload.task;
    const index = state.tasks.findIndex((task) => task.id === taskId);
    if (index >= 0) state.tasks[index] = payload.task;
    renderTaskThread();
    renderTaskBoard();
  } catch (err) {
    toast(t('toast.taskReadFailed', { reason: err.message }), 'err');
  }
}

function absorbTaskMessage(message) {
  if (!message || state.taskSeenSeqs.has(message.seq)) return false;
  state.taskSeenSeqs.add(message.seq);
  state.taskMessages.push(message);
  state.taskMessages.sort((a, b) => a.seq - b.seq);
  renderTaskThread();
  const scroll = $('#ttScroll');
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
  if (state.activeTaskId) markTaskRead(state.activeTaskId, message.seq);
  return true;
}

function connectTaskStream(taskId, cursor) {
  if (state.activeTaskId !== taskId) return;
  const source = new EventSource(taskPath(taskId, '/events') + '?after_seq=' + cursor);
  const entry = { source, timer: null, taskId };
  state.taskStream = entry;
  source.addEventListener('rotate', () => {
    source.close();
    if (state.taskStream !== entry) return;
    entry.timer = setTimeout(() => connectTaskStream(taskId, taskMaxSeq()), 250);
  });
  source.addEventListener('message', (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (payload.message && state.activeTaskId === taskId) {
      absorbTaskMessage(payload.message);
      refreshTaskDetails(taskId);
    }
  });
  source.onerror = () => {
    source.close();
    if (state.taskStream !== entry) return;
    entry.timer = setTimeout(() => connectTaskStream(taskId, taskMaxSeq()), 1500);
  };
}

function closeTaskStream() {
  const entry = state.taskStream;
  state.taskStream = null;
  if (!entry) return;
  if (entry.timer) clearTimeout(entry.timer);
  if (entry.source) entry.source.close();
  if (state.taskStreamTimer) clearTimeout(state.taskStreamTimer);
  state.taskStreamTimer = null;
}

async function refreshTaskDetails(taskId) {
  try {
    const payload = await api(taskPath(taskId));
    if (state.activeTaskId !== taskId) return;
    state.activeTask = payload.task;
    const index = state.tasks.findIndex((task) => task.id === taskId);
    if (index >= 0) state.tasks[index] = payload.task;
    renderTaskThread();
    renderTaskBoard();
  } catch {
    // 线程已可见时保留上一份快照；下一条事件或手动重开会再同步。
  }
}

/** 新建任务：弹窗形态（原型 `#btnNewTask` → `openNewTask()`），字段收窄为
    「标题 + 所属频道」——与搬过来的内联表单采集的字段等价，不借机新增输入路径。
    这里的频道下拉是**所属频道**（任务的归属），不是看板的筛选。

    `prefillTitle` 只有「作为任务发送」那条路径会传（规格决策 D6）：它同时充当「任务建成后是否
    清空 composer」的标记——看板工具栏的 `#btnNewTask` 不带正文，因此不会清空输入框。 */
function openTaskModal(prefillTitle) {
  forTaskFromComposer = typeof prefillTitle === 'string';
  $('#taskFormError').hidden = true;
  $('#taskCreateTitle').value = forTaskFromComposer ? prefillTitle : '';
  const select = $('#taskCreateChannel');
  fillTaskChannelSelect(select, false, state.activeChannelId || '');
  $('#taskCreateSubmit').disabled = !state.online || state.channels.length === 0;
  $('#taskOverlay').hidden = false;
  $('#taskCreateTitle').focus();
}

/** 「作为任务发送」（工具行）是否正带着 composer 正文：任务建成后要清空 composer（D6）。 */
let forTaskFromComposer = false;

/** 工具行「作为任务发送」：打开**既有**新建任务弹窗，把 composer 正文预填为标题、当前频道预选。
    确认走既有的 `POST /api/channels/{id}/tasks`（`submitTask`），取消则 composer 一字不动。 */
function openTaskFromComposer() {
  openTaskModal($('#composerInput').value);
}

function closeTaskModal() {
  $('#taskOverlay').hidden = true;
}

async function submitTask() {
  const channelId = $('#taskCreateChannel').value;
  const title = $('#taskCreateTitle').value.trim();
  const box = $('#taskFormError');
  if (!title) {
    // 弹窗不再是 `<form>`（按钮是 type=button），因此没有浏览器自带的 required 提示：
    // 空标题要自己说出来，不能默默什么都不发生。
    box.textContent = state.channels.length ? t('task.titleRequired') : t('task.noChannel');
    box.hidden = false;
    $('#taskCreateTitle').focus();
    return;
  }
  const button = $('#taskCreateSubmit');
  button.disabled = true;
  box.hidden = true;
  try {
    const payload = await api(channelPath(channelId, '/tasks'), { method: 'POST', body: { title } });
    closeTaskModal();
    if (forTaskFromComposer) {
      forTaskFromComposer = false;
      resetComposerInput(); // D6：正文已变成任务标题，成功后不再留在输入框里。
    }
    // 新任务要能看见：把筛选放到「能让它可见」的那一档，而不是让它落在一个被筛掉的频道之外。
    if (!state.taskFilterChannelId || state.taskFilterChannelId === channelId) {
      await refreshTasks();
    } else {
      state.taskFilterChannelId = channelId;
      await refreshTasks();
    }
    renderTaskBoard();
    toast(t('toast.taskCreated', { title: payload.task.title }));
  } catch (err) {
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    button.disabled = !state.online || state.channels.length === 0;
  }
}

/** 标题就地保存：`PATCH /api/tasks/{id}/title`（`CONTEXT.md` **任务**：任务行是可变的）。
    保存成功后后端会追加一条 `system` 消息，因此「状态变更记录」里会出现这一行。 */
async function saveTaskTitle() {
  const task = state.activeTask;
  if (!task) return;
  const input = $('#ttTitleInput');
  const title = input.value.trim();
  if (!title || title === task.title) {
    input.value = task.title;
    return;
  }
  try {
    const payload = await api(taskPath(task.id, '/title'), { method: 'PATCH', body: { title } });
    state.activeTask = payload.task;
    // 变更记录是本票要展示的事实：把后端返回的那条 system 消息立即收进线程。
    if (payload.message) absorbTaskMessage(payload.message);
    await refreshTasks();
    renderTaskThread();
  } catch (err) {
    toast(t('toast.titleUpdateFailed', { reason: err.message }), 'err');
    renderTaskThread();
  }
}

async function changeTaskStatus(status) {
  const task = state.activeTask;
  if (!task || status === task.status) return;
  // 前端只渲染合法迁移的 chip（`closed` 是终态 ⇒ 它一个都不出），但把这条不变量
  // 再当守卫写一遍：chip 是呈现，不变量是语义，两者不互相代替。
  if (!(TASK_STATUS_TRANSITIONS[task.status] || []).includes(status)) return;
  try {
    const payload = await api(taskPath(task.id, '/status'), { method: 'PATCH', body: { status } });
    state.activeTask = payload.task;
    if (payload.message) absorbTaskMessage(payload.message);
    await refreshTasks();
    renderTaskThread();
  } catch (err) {
    toast(t('toast.statusUpdateFailed', { reason: err.message }), 'err');
    renderTaskThread();
  }
}

async function changeTaskAssignee(memberId) {
  const task = state.activeTask;
  if (!task) return;
  try {
    const payload = await api(taskPath(task.id, '/assignee'), {
      method: 'PATCH', body: { member_id: memberId || null },
    });
    state.activeTask = payload.task;
    if (payload.message) absorbTaskMessage(payload.message);
    // 指派写路径会改该 target 的成员行；chip 候选来自**频道**成员表（未变），
    // 因此这里只需重读任务面与线程，不必再拉成员表。
    await refreshTasks();
    renderTaskThread();
  } catch (err) {
    toast(t('toast.assigneeUpdateFailed', { reason: err.message }), 'err');
    renderTaskThread();
  }
}

async function sendTaskMessage() {
  const task = state.activeTask;
  const input = $('#taskComposerInput');
  const body = input.value.trim();
  if (!task || !body || !state.online) return;
  $('#taskSend').disabled = true;
  try {
    const payload = await api(taskPath(task.id, '/messages'), {
      method: 'POST', body: { body, task_ref: task.id },
    });
    input.value = '';
    input.style.height = 'auto';
    absorbTaskMessage(payload.message);
    await markTaskRead(task.id, payload.message.seq);
    await refreshTasks();
  } catch (err) {
    toast(t('toast.taskMessageFailed', { reason: err.message }), 'err');
  } finally {
    $('#taskSend').disabled = !state.online || !input.value.trim();
  }
}

function replaceDm(dm) {
  const index = state.dms.findIndex((item) => item.id === dm.id);
  if (index >= 0) state.dms[index] = dm;
  else state.dms.push(dm);
}

function replaceTarget(target) {
  if (!target) return;
  if (target.kind === 'dm') replaceDm(target);
  else replaceChannel(target);
}

function seenSeqsFor(targetId) {
  if (!state.seenSeqs.has(targetId)) state.seenSeqs.set(targetId, new Set());
  return state.seenSeqs.get(targetId);
}

function lastSeqOf(targetId) {
  const target = state.channels.find((item) => item.id === targetId)
    || state.dms.find((item) => item.id === targetId);
  const known = seenSeqsFor(targetId);
  let max = target ? Number(target.last_seq) || 0 : 0;
  known.forEach((seq) => { if (seq > max) max = seq; });
  return max;
}

/* ─── 标读：切入频道、以及正在查看时到达的新消息都立即已读 ─────────────── */
// 每个频道各持一个待发标读定时器：离开 A 去 B 时，A 的标读不能被 B 的覆盖掉
// （共用一个定时器会把前一个 clearTimeout 掉，那个频道的已读推进就被静默丢弃）。
const readTimers = new Map();

function markCurrentRead() {
  const target = activeTarget();
  if (!target) return;
  const rendered = renderedMessages();
  const maxSeq = Math.max(target.last_seq, ...rendered.filter((m) => m.seq).map((m) => m.seq), 0);
  if (maxSeq <= target.last_read_seq) return;
  scheduleRead(target.id, maxSeq);
}

function scheduleRead(targetId, seq) {
  const existing = readTimers.get(targetId);
  if (existing) clearTimeout(existing);
  readTimers.set(
    targetId,
    setTimeout(async () => {
      readTimers.delete(targetId);
      try {
        const payload = await api(targetPath(targetId, '/read'), { method: 'POST', body: { seq } });
        if (payload.dm) {
          state.dms = payload.dms;
          replaceDm(payload.dm);
        } else {
          replaceChannel(payload.channel);
          state.channels = payload.channels;
          const active = activeChannel();
          if (active) replaceChannel(active);
        }
        render();
      } catch (err) {
        toast(t('toast.readFailed', { reason: err.message }), 'err');
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
  const target = activeTarget();
  if (!body || !target) return;
  if (!state.online) {
    toast(t('toast.sendBlockedOffline'), 'err');
    return;
  }
  // 上传还在飞时**不发**：这条消息会漏掉那个人刚选好、还没拿到标识的文件。
  // 发送键在 `syncSendButton()` 里也是这个理由被禁用；但 Enter 走的是这条路径，
  // 所以闸门必须在这里也有一道（否则键盘用户能绕过禁用态，静默漏掉附件）。
  if (attachingCount > 0) {
    toast(t('toast.attachPending', { count: attachingCount }), 'err');
    return;
  }
  // 草稿随这条消息一起走：它们既不留在 composer（已经属于这条 pending），也在失败时随 pending
  // 一起保留（重试会带上同一批标识）。
  const attachments = state.composerAttachments.slice();
  const pending = {
    clientId: 'pending-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7),
    target_id: target.id,
    body,
    status: 'pending',
    created_at: new Date().toISOString(),
    author: state.member,
    attachments,
  };
  state.pending.push(pending);
  resetComposerInput();
  clearComposerAttachments();
  renderMessages();
  scrollToBottom();
  deliver(pending);
}

async function deliver(pending) {
  try {
    const payload = await api(targetPath(pending.target_id, '/messages'), {
      method: 'POST',
      body: {
        body: pending.body,
        attachment_ids: (pending.attachments || []).map((item) => item.id),
      },
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
      toast(t('toast.sendMessageLost'), 'err');
    } else {
      toast(t('toast.sendRejected', { reason: err.message }), 'err');
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
  const wanted = new Set([
    ...state.channels.map((channel) => channel.id),
    ...state.dms.map((dm) => dm.id),
  ]);
  state.streams.forEach((_entry, targetId) => {
    if (!wanted.has(targetId)) {
      closeStream(targetId);
    }
  });
  wanted.forEach((targetId) => {
    if (!state.streams.has(targetId)) openStream(targetId);
  });
}

function closeStream(targetId) {
  const entry = state.streams.get(targetId);
  if (!entry) return;
  state.streams.delete(targetId);
  if (entry.timer) clearTimeout(entry.timer);
  if (entry.source) entry.source.close();
}

function openStream(targetId) {
  const entry = { source: null, timer: null };
  state.streams.set(targetId, entry);
  connectStream(targetId, entry);
}

function connectStream(targetId, entry) {
  // 每一轮重连都重新取一次游标：它就是「我已经看到哪一条」的最新事实。
  const cursor = lastSeqOf(targetId);
  const source = new EventSource(targetPath(targetId, '/events') + '?after_seq=' + cursor);
  entry.source = source;
  // 服务端的例行轮换（event: rotate）不算掉线：它只是要求重新连一次。
  entry.rotating = false;
  source.addEventListener('rotate', () => {
    entry.rotating = true;
    source.close();
    if (state.streams.get(targetId) !== entry) return;
    entry.timer = setTimeout(() => connectStream(targetId, entry), 250);
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
    showTypingFor(targetId, null);
    source.close();
    if (state.streams.get(targetId) !== entry) return; // 已被换掉（频道删除等）
    entry.timer = setTimeout(() => connectStream(targetId, entry), STREAM_RETRY_MS);
  };
  ['message', 'member_status', 'typing', 'round_outcome'].forEach((type) => {
    source.addEventListener(type, (event) => handleStreamEvent(targetId, event, type));
  });
}

function handleStreamEvent(targetId, event, type) {
  let payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return; // 解不开的帧直接丢弃：它不可能是后端发出的
  }
  const seq = Number(payload.seq) || 0;
  if (seq && type === 'message') {
    // 消息的去重正本在 absorbMessage 里（按 seq 认一条消息），这里不另做一套。
    onStreamMessage(targetId, payload);
    return;
  }
  if (seq) seenSeqsFor(targetId).add(seq);
  if (type === 'member_status') onStreamStatus(targetId, payload);
  else if (type === 'typing') onStreamTyping(targetId, payload);
  else if (type === 'round_outcome') onStreamOutcome(targetId, payload);
}

function absorbMessage(targetId, message) {
  const known = seenSeqsFor(targetId);
  if (known.has(message.seq)) return false;
  known.add(message.seq);
  const target = state.channels.find((item) => item.id === targetId)
    || state.dms.find((item) => item.id === targetId);
  if (target) target.last_seq = Math.max(Number(target.last_seq) || 0, message.seq);
  if (state.activeChannelId === targetId) {
    state.messages.push(message);
    state.messages.sort((a, b) => a.seq - b.seq);
  }
  return true;
}

function onStreamMessage(targetId, payload) {
  const added = absorbMessage(targetId, payload.message);
  if (!added) return;
  if (state.activeChannelId === targetId) {
    renderMessages();
    scrollToBottom();
    // 正在查看该 target：到达即已读。
    markCurrentRead();
  }
  // 其它 target 的未读徽标也要跟着动：从后端重新取计数（未读的正本在库）。
  if (state.dms.some((dm) => dm.id === targetId)) refreshDmList();
  else refreshChannelList();
}

async function refreshDmList() {
  try {
    const payload = await api('/api/dms');
    state.dms = payload.dms;
    renderDms();
  } catch {
    // 保留上一次已知的计数：宁可略陈旧，也不伪造。
  }
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

function onStreamStatus(targetId, payload) {
  const previous = state.statuses[payload.member_id];
  state.statuses[payload.member_id] = payload.status;
  state.agents = state.agents.map((agent) =>
    agent.id === payload.member_id ? { ...agent, status: payload.status } : agent,
  );
  if (payload.status !== 'working') showTypingFor(targetId, null);
  renderMembers();
  renderDms();
  renderDock();
  renderRoster(); // 名册若开着，它的状态点也必须跟着后端事实走（关着时是空操作）
  // 详情面板开着时它的状态点同样只能来自后端事实（关着时是空操作）。
  if (state.activeAgentId) renderAgentDetail();
  // 已呈现的轮次结果行里的状态描述（「未启用」/「这一轮失败」）必须跟着后端事实走，
  // 不能停在一句已经不成立的话上。没呈现过该成员就不重建消息流。
  if (state.activeChannelId === targetId && previous !== payload.status && outcomeMembers().has(payload.member_id)) {
    renderMessages();
  }
}

function onStreamTyping(targetId, payload) {
  showTypingFor(targetId, payload.active ? payload.member_id : null);
  if (payload.active) {
    state.statuses[payload.member_id] = 'working';
  } else if (state.statuses[payload.member_id] === 'working') {
    state.statuses[payload.member_id] = 'online';
  }
  renderMembers();
  renderDock();
  renderRoster(); // 名册若开着，它的状态点也必须跟着后端事实走（关着时是空操作）
  if (state.activeAgentId) renderAgentDetail();
}

function showTypingFor(targetId, memberId) {
  if (memberId) state.typing[targetId] = memberId;
  else delete state.typing[targetId];
  if (state.activeChannelId === targetId) renderTyping();
}

function onStreamOutcome(targetId, payload) {
  const outcome = payload.outcome;
  if (!outcome) return;
  if (outcome.status === 'failed') state.statuses[outcome.member_id] = 'error';
  else if (state.statuses[outcome.member_id] === 'working') state.statuses[outcome.member_id] = 'online';
  let added = false;
  if (state.activeChannelId === targetId && Number.isInteger(outcome.base_seq)) {
    const byMember = state.outcomes[outcome.base_seq] || (state.outcomes[outcome.base_seq] = {});
    const records = byMember[outcome.member_id] || (byMember[outcome.member_id] = []);
    // 同一 id 只留一条：实时推送与断线补拉说的是同一件事，不重复呈现。
    if (!records.some((record) => record.id === outcome.id)) {
      records.push(outcome);
      added = true;
    }
    // 回复序号的索引也要跟着更新：刚到达的这条结果可能是某条旁白消息**自己那一轮**的结论，
    // 而那条消息可能先于它到达（`replied` 的消息先落库、结果后收口）。
    if (Number.isInteger(outcome.reply_seq)) state.replyOutcomes[outcome.reply_seq] = outcome;
  }
  renderMembers();
  renderDock();
  renderRoster(); // 名册若开着，它的状态点也必须跟着后端事实走（关着时是空操作）
  // 详情面板开着时：这个成员自己刚落了一条轮次结果 ⇒ 概览的四个数字（库里聚合而来）要重读一次；
  // 别人的轮次结果只可能改变状态点，重绘即可。
  if (state.activeAgentId === outcome.member_id) refreshAgentDetail(outcome.member_id);
  else if (state.activeAgentId) renderAgentDetail();
  // 五种结果都要重建消息流：只有 `failed` 会被状态点推导拦住（`ignored` 等三态**不是**失败，
  // 状态点仍空闲），但它们的摘要行同样要在不刷新页面的情况下出现。
  if (state.activeChannelId === targetId && added) renderMessages();
}

// 哪些成员在消息流里有轮次结果呈现（用于状态点变化时只重渲染该重的那部分）。
function outcomeMembers() {
  const ids = new Set();
  Object.keys(state.outcomes).forEach((seq) => {
    Object.keys(state.outcomes[seq]).forEach((memberId) => ids.add(memberId));
  });
  return ids;
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
  renderReminderDot();
  renderChannels();
  renderDms();
  renderMembers();
  renderTaskBoard();
  // dock 的三种形态共用同一个 380px 容器：每次整体重绘都把形态重新同步一遍（形态由
  // 「现在打开着哪一个对象」推导，见 `setDockMode()`），否则某次局部重建会把形态留在上一帧。
  setDockMode(state.activeTaskId ? 'task' : (state.activeAgentId ? 'detail' : 'agent'));
  if (state.activeTask) renderTaskThread();
  syncTaskViewSeg();
  renderHeader();
  setViewMode(state.mode);
  renderMessages();
  // 顶部栏（≤900px）显示**当前 target 的标题**：它跟着 target 走，因此和 header 一起刷。
  renderMobileTopbar();
  // 搜索面的文案（facet / 结果行 / 空态）都按词条取值，因此它和别的面一样随整体重绘刷新——
  // 切语言后搜索结果不会留在上一门语言里。
  renderSearchFacets();
  renderSearchResults();
  renderConnection();
  renderDock();
  // Agent 详情开着时它的读数（状态点 / 跑动的数字）也要跟着整体重绘刷新：它的每个数字都是
  // 后端事实的镜像，因此重建它和重建消息流是同一件事。
  if (state.activeAgentId) renderAgentDetail();
  renderTyping();
  syncSendButton();
}

function renderIdentity() {
  const member = state.member || OWNER_FALLBACK;
  $('#meName').textContent = member.display_name;
  $('#meSub').textContent = member.kind === 'owner' ? t('identity.human') : member.kind;
  const avatar = $('#meAvatar');
  avatar.className = 'avatar ' + avatarClass(member.avatar_slot);
  avatar.textContent = initial(member.display_name);
}

function replaceChildren(container, nodes) {
  while (container.firstChild) container.removeChild(container.firstChild);
  nodes.forEach((node) => container.appendChild(node));
}

/* ─── 左栏三份名单：就地更新，不整表换节点 ──────────────────────────────

   左栏行（`.nav-row`）带 background/color 过渡，状态点带 `pulse` 动画。整表
   `replaceChildren` 会把行节点连同过渡与动画一起换掉：Agent 每回复一条消息，回复路径上
   都会先后走到「重取列表 → 三份名单重绘」与 `scheduleRead` → `render()`，于是左栏看起来
   闪一下并刷新。因此这三份名单改成**按后端 id 认领已有行**：成员集合不变就沿用原节点，
   只有真的新增 / 删除 / 换序才动 DOM 结构；行内部的可见变化（当前行、名字、未读徽标、
   状态点、无障碍名）就地改那一个子节点，且值没变时不写。 */

/** 行内某个 class 的直接子节点（每一行里同一 class 至多一个）。 */
function railCell(row, className) {
  return Array.from(row.children).find(
    (node) => node.classList && node.classList.contains(className),
  ) || null;
}

/** 就地写一格文本：文本没变就不写——对真实 DOM 来说，写一次 `textContent` 会换掉文本节点。 */
function setRailCellText(row, className, text) {
  const cell = railCell(row, className);
  if (!cell) return;
  const value = String(text);
  if (cell.textContent !== value) cell.textContent = value;
}

/** 就地写「当前行」：`is-active` 与 `aria-current` 是同一条事实的两处呈现，一起对齐。 */
function syncActiveRow(row, active) {
  if (row.classList.contains('is-active') !== active) row.classList.toggle('is-active', active);
  const current = active ? 'true' : 'false';
  if (row.getAttribute('aria-current') !== current) row.setAttribute('aria-current', current);
}

/** 就地写未读徽标：计数 `>0` 时有一个徽标节点，`0` 时没有；数字变了只改文本，不换节点。 */
function syncUnreadBadge(row, count, odId) {
  const total = Number(count) || 0;
  const badge = railCell(row, 'badge');
  if (total <= 0) {
    if (badge) badge.remove();
    return;
  }
  if (!badge) {
    const node = el('span', 'badge', total);
    node.setAttribute('data-od-id', odId);
    row.appendChild(node);
    return;
  }
  if (badge.textContent !== String(total)) badge.textContent = String(total);
}

/** 就地写头像（首字母 + 色位）：改名或换头像后，左栏那一格要跟着后端事实走。 */
function syncAvatarCell(row, name, slot, size) {
  const avatar = railCell(row, 'avatar');
  if (!avatar) return;
  const text = initial(name);
  if (avatar.textContent !== text) avatar.textContent = text;
  const className = avatarClassName(size, slot);
  if (avatar.className !== className) avatar.className = className;
}

/** 就地写状态点：四态只表达后端事实（空闲 / 正在回复 / 出错 / 未启用），不从会话存活推导。

    只改那一个子节点的 class / dataset / 无障碍名，行节点本身不动（`.presence.working`
    的 pulse 动画因此在「已经是在回复」时不会重头开始）。每一行在 `build` 时就已经带上
    状态点，所以这里不需要再造一个。

    切语言的守卫：`title` / `aria-label` 是 `statusLabel()` 在当前 locale 下的取值，
    只看 `dataset.status` 会让「状态没变、语言变了」的切语言路径整格跳过，把无障碍名
    留在上一门语言里——因此把词条文本也当成同一条事实的一部分一起比对。 */
function syncPresenceCell(row, status) {
  const dot = railCell(row, 'presence');
  if (!dot) return;
  const label = statusLabel(status);
  if (dot.dataset.status !== status
    || dot.getAttribute('title') !== label
    || dot.getAttribute('aria-label') !== label) {
    applyPresence(dot, status);
  }
}

/** 三份名单共用的就地更新：同一份成员集合下保留已有行节点。

    `items` 是后端事实的镜像（频道 / 私信 / Agent 列表），行的身份取自后端 id
    （`data-channel` / `data-dm` / `data-member`），因此每一趟都取回同一个节点。
    `build(item)` 只在后端出现了新的 id 时调用（挂上固定的结构：图标位 / 名字格 / 状态点）；
    `paint(row, item)` 每趟都调，负责把行内的可见事实对齐到当前值——它必须只改真的变了的
    子节点（见上面几个 `sync*` 助手）。 */
function syncRailRows(container, items, key, build, paint) {
  if (!container) return;
  const existing = new Map();
  Array.from(container.children).forEach((node) => {
    const id = node.dataset ? node.dataset[key] : undefined;
    if (id !== undefined && !existing.has(id)) existing.set(id, node);
  });
  const rows = items.map((item) => {
    // 复用的行从索引里取走：后端若意外给了重复 id，第二行必须新造，不能把同一个节点挂两次。
    const id = String(item.id);
    const row = existing.get(id) || build(item);
    existing.delete(id);
    paint(row, item);
    return row;
  });
  // 只摘掉后端已经不再返回的行（以及没有 id 的杂物）。
  Array.from(container.children).forEach((node) => {
    if (!rows.includes(node)) node.remove();
  });
  // 位置已经对了就**一个 DOM 操作都不做**——未变的行不 remove 再 insert，它们的过渡与动画
  // 因此不会重头开始。真的换序时才重排，且**只动错位的那一段**：前缀位置正确的行一律不碰，
  // 从第一个错位的行开始按目标顺序重挂（`appendChild` 挂已在容器里的节点就是把它移到末尾）。
  const orderChanged = container.children.length !== rows.length
    || rows.some((row, index) => container.children[index] !== row);
  if (orderChanged) {
    let start = 0;
    while (start < rows.length && container.children[start] === rows[start]) start += 1;
    rows.slice(start).forEach((row) => container.appendChild(row));
  }
}

/** 新建一行频道：只在后端出现了新的频道 id 时调用，之后每一趟都是就地改写这一行。 */
function channelRowNode(channel) {
  const row = el('button', 'nav-row');
  row.dataset.channel = channel.id;
  row.setAttribute('data-od-id', 'channel-' + channel.id);
  row.appendChild(el('span', 'hash', '#'));
  row.appendChild(el('span', 'grow', channel.name));
  return row;
}

/** 就地改写一行频道：当前行、名字、未读徽标。 */
function paintChannelRow(row, channel) {
  syncActiveRow(row, channel.id === state.activeChannelId);
  setRailCellText(row, 'grow', channel.name);
  syncUnreadBadge(row, channel.unread_count, 'unread-' + channel.id);
}

function renderChannels() {
  syncRailRows($('#channelList'), state.channels, 'channel', channelRowNode, paintChannelRow);
  $('#channelListEmpty').hidden = state.channels.length > 0;
}

/** 新建一行私信：结构与改前一致（头像 / 名字 / 状态点）。 */
function dmRowNode(dm, status) {
  const peer = dm.peer;
  const row = el('button', 'nav-row');
  row.type = 'button';
  row.dataset.dm = dm.id;
  row.setAttribute('data-od-id', 'dm-' + dm.id);
  row.appendChild(avatarNode(peer.display_name, peer.avatar_slot, 'sm'));
  row.appendChild(el('span', 'grow', peer.display_name));
  row.appendChild(presenceNode(status));
  return row;
}

/** 就地改写一行私信：当前行、头像与名字、状态点、未读徽标。 */
function paintDmRow(row, dm, status) {
  const peer = dm.peer;
  syncActiveRow(row, dm.id === state.activeChannelId);
  syncAvatarCell(row, peer.display_name, peer.avatar_slot, 'sm');
  setRailCellText(row, 'grow', peer.display_name);
  syncPresenceCell(row, status);
  syncUnreadBadge(row, dm.unread_count, 'unread-' + dm.id);
}

function renderDms() {
  const agentsById = new Map(state.agents.map((agent) => [agent.id, agent]));
  const statusOfPeer = (dm) => statusOf(agentsById.get(dm.peer.id));
  syncRailRows(
    $('#dmList'),
    state.dms,
    'dm',
    (dm) => dmRowNode(dm, statusOfPeer(dm)),
    (row, dm) => paintDmRow(row, dm, statusOfPeer(dm)),
  );
  $('#dmListEmpty').hidden = state.dms.length > 0;
}

/** 新建一行本机 Agent：左栏每一行都是**打开 Agent 详情**的入口，与频道 / 私信两段同形
    （`<button class="nav-row">`），因此键盘可达、可聚焦，不是「看着能点、按下去什么都不发生」的装饰行。 */
function memberRowNode(member, status) {
  const row = el('button', 'nav-row');
  row.type = 'button';
  row.dataset.member = member.id;
  row.setAttribute('data-od-id', 'agent-row-' + member.id);
  row.setAttribute('aria-label', t('agentDetail.openAria', { name: member.display_name }));
  row.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
  row.appendChild(el('span', 'grow', member.display_name));
  row.appendChild(presenceNode(status));
  return row;
}

/** 就地改写一行 Agent：当前行、头像与名字、无障碍名（切语言后跟着词条走）、状态点。 */
function paintMemberRow(row, member, status) {
  syncActiveRow(row, member.id === state.activeAgentId);
  syncAvatarCell(row, member.display_name, member.avatar_slot, 'sm');
  setRailCellText(row, 'grow', member.display_name);
  const label = t('agentDetail.openAria', { name: member.display_name });
  if (row.getAttribute('aria-label') !== label) row.setAttribute('aria-label', label);
  syncPresenceCell(row, status);
}

function renderMembers() {
  const byId = new Map(state.agents.map((agent) => [agent.id, agent]));
  const agents = state.members.filter((member) => member.kind === 'agent');
  const statusOfMember = (member) => statusOf(byId.get(member.id));
  syncRailRows(
    $('#memberList'),
    agents,
    'member',
    (member) => memberRowNode(member, statusOfMember(member)),
    (row, member) => paintMemberRow(row, member, statusOfMember(member)),
  );
  $('#memberListEmpty').hidden = agents.length > 0;
}

function statusOf(agent) {
  if (agent && typeof agent.status === 'string') return agent.status;
  return state.statuses[agent && agent.id] || 'offline';
}

/** 把四态写到一个状态点节点上（class / title / aria-label / dataset 一起），返回同一个节点。

    它是状态点的**唯一**写法：静态列表（`presenceNode`）与详情页头部（会重建的 `#adPresence`）
    共用它，于是「同一个事实在两处呈现」不会漂移成两套 class 与两套无障碍文案。 */
function applyPresence(node, status) {
  node.className = 'presence ' + status;
  node.dataset.status = status;
  node.setAttribute('title', statusLabel(status));
  node.setAttribute('aria-label', statusLabel(status));
  return node;
}

function presenceNode(status) {
  return applyPresence(el('span', 'presence'), status);
}

/** 该频道的 Agent 成员（已加入）+ 本工作区里未加入的 Agent。

    「已加入」的**唯一来源**是该频道的成员表（`GET /api/channels/{id}/members`），与面板标题
    「频道成员」一致；「未加入」= 全工作区 Agent 名单（`state.members`）减去已加入者。
    两者都是后端事实的镜像，前端不自己推断频道关系。

    未加入的 Agent 呈现在**面板内**的「未加入」一段（人类裁定 2 选 a）：入口就在成员面板里，
    人看到「它不在这个频道」的那一眼就同时看到了「加入」按钮。 */
function dockGroups(channelId) {
  // 已加入的 Agent 与 @ 补全列表同一个来源（同一个函数），不在这里重写一遍过滤规则。
  const joined = channelAgentMembers(channelId);
  const joinedIds = new Set((joined || []).map((member) => member.id));
  const outsiders = state.members.filter(
    (member) => member.kind === 'agent' && !joinedIds.has(member.id),
  );
  return { loaded: joined !== null, joined: joined || [], outsiders };
}

function renderDock() {
  const channel = activeChannel();
  const container = $('#dockMembers');
  if (!container) return;
  if (!channel) {
    const target = activeTarget();
    replaceChildren(
      container,
      [el('div', 'rail-empty', target && target.kind === 'dm'
        ? t('dock.dmNoJoin')
        : t('dock.openChannelFirst'))],
    );
    return;
  }
  const groups = dockGroups(channel.id);
  const agentsById = new Map(state.agents.map((agent) => [agent.id, agent]));
  const nodes = [];

  // ── 已加入：本频道的 Agent 成员 ──────────────────────────────────────
  const joinedSection = el('section', 'dock-group');
  joinedSection.dataset.odId = 'dock-joined';
  const joinedLabel = el('div', 'dock-group-label', t('dock.joined', { count: groups.joined.length }));
  joinedSection.appendChild(joinedLabel);
  if (!groups.loaded) {
    // 「读不到 / 还没读到」与「真的没有成员」是两件事：不把前者说成后者（与 `@` 清单的
    // `unavailable` 分档同一纪律）。读失败时也不把已加入的 Agent 列进「未加入」——
    // 那会伪造一个「它不在这个频道」的事实。
    joinedSection.appendChild(
      el(
        'div',
        'rail-empty',
        channelMembersFailed.has(channel.id)
          ? t('dock.membersUnavailable')
          : t('dock.membersLoading'),
      ),
    );
  } else if (!groups.joined.length) {
    joinedSection.appendChild(
      el('div', 'rail-empty', t('dock.noJoinedAgents')),
    );
  } else {
    groups.joined.forEach((member) => joinedSection.appendChild(memberCard(member, agentsById)));
  }
  nodes.push(joinedSection);

  // ── 未加入：本工作区的 Agent，点「加入」进入该频道 ────────────────────
  const outsideSection = el('section', 'dock-group');
  outsideSection.dataset.odId = 'dock-not-joined';
  outsideSection.appendChild(
    el('div', 'dock-group-label', t('dock.notJoined', { count: groups.outsiders.length })),
  );
  if (!groups.loaded) {
    outsideSection.appendChild(
      el(
        'div',
        'rail-empty',
        channelMembersFailed.has(channel.id)
          ? t('dock.notJoinedUnavailable')
          : t('dock.membersLoading'),
      ),
    );
  } else if (!groups.outsiders.length) {
    outsideSection.appendChild(
      el(
        'div',
        'rail-empty',
        state.members.some((member) => member.kind === 'agent')
          ? t('dock.allJoined')
          : t('dock.noAgents'),
      ),
    );
  } else {
    groups.outsiders.forEach((member) => outsideSection.appendChild(memberCard(member, agentsById, true)));
  }
  nodes.push(outsideSection);
  replaceChildren(container, nodes);
}

/** 一张成员卡片。``canJoin`` 为真时附上「加入」控件（与既有 `.btn-sm` 同一语汇，不新造色位）。 */
function memberCard(member, agentsById, canJoin) {
  const agent = agentsById.get(member.id) || {};
  const card = el('div', 'dock-member' + (canJoin ? ' is-outside' : ''));
  card.dataset.member = member.id;
  card.setAttribute('data-od-id', (canJoin ? 'dock-outside-' : 'dock-member-') + member.id);
  const head = el('div', 'dock-member-head');
  head.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
  // 名字本身是**打开 Agent 详情**的控件：卡片里已经有「加入」按钮（未加入那一段），
  // 嵌套按钮在 HTML 里是无效结构，因此入口落在名字上，而不是把整张卡片做成按钮。
  const nameButton = el('button', 'dock-member-name', member.display_name);
  nameButton.type = 'button';
  nameButton.dataset.agentOpen = member.id;
  nameButton.setAttribute('data-od-id', 'dock-open-' + member.id);
  nameButton.setAttribute('aria-label', t('agentDetail.openAria', { name: member.display_name }));
  head.appendChild(nameButton);
  if (agent.role) head.appendChild(el('span', 'msg-tag', agent.role));
  head.appendChild(presenceNode(statusOf(agent)));
  card.appendChild(head);
  const meta = el('div', 'dock-member-meta mono', agent.model_id || t('dock.noModel'));
  card.appendChild(meta);
  const status = statusOf(agent);
  const line = el('div', 'dock-member-status', statusLabel(status));
  line.dataset.status = status;
  card.appendChild(line);
  if (agent.enabled_reason) {
    const reason = el('div', 'dock-member-reason', agent.enabled_reason);
    reason.dataset.odId = 'agent-reason-' + member.id;
    card.appendChild(reason);
  }
  if (canJoin) {
    const foot = el('div', 'dock-member-foot');
    const button = el('button', 'btn btn-sm', joining.has(member.id) ? t('dock.joining') : t('dock.join'));
    button.type = 'button';
    button.dataset.join = member.id;
    button.setAttribute('data-od-id', 'dock-join-' + member.id);
    button.setAttribute('aria-label', t('dock.joinAria', { name: member.display_name }));
    button.disabled = joining.has(member.id);
    foot.appendChild(button);
    card.appendChild(foot);
  }
  return card;
}

/* ─── Agent 详情（dock 第三形态）：概览 + 运行时 ─────────────────────────
   `CONTEXT.md` **Agent** 与 ADR-0011：详情页是**人类面**的只读呈现 + 两个真动作（暂停 / 编辑配置），
   两个动作都落在同一个写路径 `PATCH /api/agents/{id}` 上。

   两条纪律（票面 Constraints）：

   - **概览只呈现有真实来源的事实**：四个数字全部来自后端 `GET /api/agents/{id}` 的 `stats`
     （`round_outcome` 聚合 + 任务行 + 频道成员表）。前端**不**自己算，也不为填满界面编数值。
   - **运行时只呈现 `agent_config` 里真实存在的字段**：原型的内存 / 延迟 / 工具权限 / 温度 /
     Token 消耗 / 上下文占用在本仓没有数据面，因此这里既不渲染也不留占位。
   日志页签**不做**（用户人定 D24，入口另立 backlog 票 #72）。 */

/** 取一个成员在 `state` 里的**最新**读数：`state.agents`（含配置与 stats）优先，
    回退到 `state.members`（只有身份行）。两者都是后端读数的镜像，不在这里合成新事实。 */
function agentById(memberId) {
  return state.agents.find((agent) => agent.id === memberId)
    || state.members.find((member) => member.id === memberId)
    || null;
}

/** 用后端刚返回的那一份覆盖本地镜像（新增与覆盖一条路径，不写两份）。 */
function upsertAgent(agent) {
  if (!agent || !agent.id) return;
  const index = state.agents.findIndex((item) => item.id === agent.id);
  if (index >= 0) state.agents[index] = agent;
  else state.agents.push(agent);
}

/** 打开 Agent 详情（入口：左栏 Agent 行 / 成员面板里的名字）。 */
async function openAgentDetail(memberId) {
  if (!memberId) return;
  // dock 一次只显示一种形态：打开详情就关掉任务线程（反之亦然，见 `openTask`）。
  closeTaskStream();
  state.activeTaskId = null;
  state.activeTask = null;
  state.taskMessages = [];
  state.taskSeenSeqs = new Set();
  state.activeAgentId = memberId;
  state.agentDetailTab = 'overview';
  // 同上：点 Agent 行是显式打开，`reveal` 压过「人把 dock 收起来过」这一位。
  setDockMode('detail', true);
  renderMembers();
  renderAgentDetail();
  await refreshAgentDetail(memberId);
}

/** 关闭详情：回到另一个仍打开着的形态；没有可回退的 target 时**收起 dock**
    （落点判据与关闭任务线程共用，见 `dockFallbackMode()`）。 */
function closeAgentDetail() {
  state.activeAgentId = null;
  settleDockFallback();
  renderMembers();
}

/** 从后端读**这一个**成员的最新读数（配置 + 状态点 + 概览数字）。
    工作台快照里的那份可能已经旧了（消息处理数与完成任务数会变），因此打开时读一次正本。 */
async function refreshAgentDetail(memberId) {
  try {
    const payload = await api('/api/agents/' + encodeURIComponent(memberId));
    if (state.activeAgentId !== memberId) return; // 已经切到别人：不把旧读数铺到新面板上
    upsertAgent(payload.agent);
    renderMembers();
    renderDock();
    renderAgentDetail();
  } catch (err) {
    toast(t('toast.agentDetailFailed', { reason: err.message }), 'err');
  }
}

/** 两个页签的选中态与两块内容的可见性（唯一的落点：打开、切页签、整帧重绘都走它）。 */
function setAgentDetailTab(tab) {
  state.agentDetailTab = tab === 'runtime' ? 'runtime' : 'overview';
  const overview = state.agentDetailTab === 'overview';
  $('#adOverview').hidden = !overview;
  $('#adRuntime').hidden = overview;
  [['overview', $('#adTabOverview')], ['runtime', $('#adTabRuntime')]].forEach(([name, button]) => {
    const on = name === state.agentDetailTab;
    button.classList.toggle('is-active', on);
    button.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

/** 详情面板的**唯一**渲染落点（打开、状态流推送、配置保存后都走它）。 */
function renderAgentDetail() {
  const agent = state.activeAgentId ? agentById(state.activeAgentId) : null;
  if (!agent) return;
  const status = statusOf(agent);
  applyPresence($('#adPresence'), status);
  $('#adStatusText').textContent = statusLabel(status);
  $('#adName').textContent = agent.display_name;
  replaceChildren($('#adOverview'), agentOverviewNodes(agent));
  replaceChildren($('#adRuntime'), agentRuntimeNodes(agent));
  setAgentDetailTab(state.agentDetailTab);
}

/** 详情面板的分区标题：文案走词条，`data-od-id` 是 `alibot-design-system/SKILL.md` 对
    「regions / headings」的要求（这类标题是运行时生成的，因此标识也在这里给）。 */
function sectionTitle(key, odId) {
  const node = el('div', 'd-sec-title', t(key));
  if (odId) node.setAttribute('data-od-id', odId);
  return node;
}

/** 概览：四个数字（同一个 `stats` 来源）+ 当前状态与未启用原因 + 两个真动作。 */
function agentOverviewNodes(agent) {
  const stats = agent.stats || {};
  const nodes = [];

  const facts = el('section', 'd-sec');
  facts.appendChild(sectionTitle('agentDetail.sectionFacts', 'agent-detail-section-facts'));
  const grid = el('div', 'stat-grid');
  [
    ['agentDetail.statChannels', stats.channel_count, 'agentDetail.statChannelsHint', 'agent-stat-channels'],
    ['agentDetail.statRounds', stats.round_count, 'agentDetail.statRoundsHint', 'agent-stat-rounds'],
    ['agentDetail.statTasksDone', stats.completed_task_count, 'agentDetail.statTasksDoneHint',
      'agent-stat-completed-tasks'],
  ].forEach(([labelKey, value, hintKey, odId]) => {
    grid.appendChild(statCell(t(labelKey), t(hintKey), String(Number(value) || 0), odId));
  });
  // 成功率：没有样本时**不编** 0%——「一条都没有」与「全部失败」是两件事。
  const rate = stats.success_rate;
  const rateText = rate === null || rate === undefined
    ? t('agentDetail.noSamples')
    : Math.round(Number(rate) * 100) + '%';
  grid.appendChild(statCell(t('agentDetail.statSuccess'), t('agentDetail.statSuccessHint'), rateText,
    'agent-stat-success-rate'));
  facts.appendChild(grid);
  // 概览也呈现**模型标识**（规格 §2 面 4 与 D20 把它列进「概览呈现有真实来源的事实」；
  // US17 同样点名它）。它与运行时页签那一行同源同措辞，只是概览把它放在「事实」里一眼可见。
  facts.appendChild(kvRow(t('agentDetail.kvModel'), agent.model_id || t('dock.noModel'),
    'agent-overview-model'));
  nodes.push(facts);

  const status = statusOf(agent);
  const statusSection = el('section', 'd-sec');
  statusSection.appendChild(sectionTitle('agentDetail.sectionStatus', 'agent-detail-section-status'));
  statusSection.appendChild(kvRow(t('agentDetail.kvStatus'), statusLabel(status),
    'agent-detail-status-value'));
  if (agent.enabled_reason) {
    statusSection.appendChild(kvRow(t('agentDetail.kvReason'), agent.enabled_reason,
      'agent-detail-enabled-reason'));
  }
  nodes.push(statusSection);

  const actions = el('section', 'd-sec');
  actions.appendChild(sectionTitle('agentDetail.sectionActions', 'agent-detail-section-actions'));
  const row = el('div', 'agent-actions');
  // 「暂停 / 恢复」就是写 `enabled`（ADR-0011）：按钮的说法随**当前落库取值**变，
  // 不另存一个「暂停中」的前端状态。
  const toggle = el('button', 'btn btn-sm',
    agent.enabled ? t('agentDetail.pause') : t('agentDetail.resume'));
  toggle.type = 'button';
  toggle.dataset.agentToggle = agent.id;
  toggle.setAttribute('data-od-id', 'agent-toggle-enabled');
  toggle.disabled = !state.online;
  row.appendChild(toggle);
  const edit = el('button', 'btn btn-sm', t('agentDetail.edit'));
  edit.type = 'button';
  edit.dataset.agentEdit = agent.id;
  edit.setAttribute('data-od-id', 'agent-open-edit');
  edit.disabled = !state.online;
  row.appendChild(edit);
  actions.appendChild(row);
  // 暂停的**已知代价**写在界面上（不是只写在 ADR 里）：人点之前就该知道它会留下一条失败轮次结果。
  actions.appendChild(el('div', 'hint', t('agentDetail.pauseNote')));
  nodes.push(actions);
  return nodes;
}

/** 运行时：**只**有 `agent_config` 里真实存在的字段（外加可用性推导出的告知状态）。 */
function agentRuntimeNodes(agent) {
  const section = el('section', 'd-sec');
  section.appendChild(sectionTitle('agentDetail.sectionRuntime', 'agent-detail-section-runtime'));
  section.appendChild(el('div', 'hint', t('agentDetail.runtimeNote')));
  const rows = [
    ['agentDetail.kvModel', agent.model_id || t('dock.noModel'), 'agent-detail-model'],
    ['agentDetail.kvProvider', agent.provider ? providerLabel(agent.provider) : t('agentDetail.emptyField'),
      'agent-detail-provider'],
    ['agentDetail.kvRemote', agent.is_remote ? t('agentDetail.remoteYes') : t('agentDetail.remoteNo'),
      'agent-detail-remote'],
    ['agentDetail.kvConsent', agentConsentLabel(agent), 'agent-detail-consent'],
    ['agentDetail.kvRole', agent.role || t('agentDetail.emptyField'), 'agent-detail-role'],
    ['agentDetail.kvEnabled', agent.enabled ? t('agentDetail.enabledOn') : t('agentDetail.enabledOff'),
      'agent-detail-enabled'],
    // 「自动认领任务」（#65）也是 `agent_config` 里真实存在的一列，因此与其余字段同一条口径：
    // 运行时页签**读**它，写它的地方仍是**同一个**编辑配置弹窗（不另造第二个编辑面）。
    ['agentDetail.kvAutoClaim',
      agent.auto_claim ? t('agentDetail.autoClaimOn') : t('agentDetail.autoClaimOff'),
      'agent-detail-auto-claim'],
  ];
  rows.forEach(([key, value, odId]) => section.appendChild(kvRow(t(key), value, odId)));

  const promptSection = el('section', 'd-sec');
  promptSection.appendChild(sectionTitle('agentDetail.kvPrompt', 'agent-detail-section-prompt'));
  const body = el('div', 'prompt-body', agent.system_prompt || t('agentDetail.emptyField'));
  body.setAttribute('data-od-id', 'agent-detail-prompt');
  promptSection.appendChild(body);
  return [section, promptSection];
}

/** 远端数据流告知的**状态**（不是文案本身）：本机 provider 不需要确认，远端 provider 看库里的记录。 */
function agentConsentLabel(agent) {
  if (!agent.provider) return t('agentDetail.emptyField');
  if (!agent.is_remote) return t('agentDetail.consentNotNeeded');
  return agent.remote_consent ? t('agentDetail.consentAck') : t('agentDetail.consentPending');
}

/** 一个概览数字：`title` 与 `aria-label` 都写明**来源**（数字本身不带来源就是不可核对的读数）。 */
function statCell(label, hint, value, odId) {
  const cell = el('div', 'stat');
  cell.title = hint;
  cell.setAttribute('aria-label', label + t('common.colonSep') + value + t('common.clauseSep') + hint);
  const number = el('div', 'n', value);
  number.setAttribute('data-od-id', odId);
  cell.appendChild(number);
  cell.appendChild(el('div', 'l', label));
  return cell;
}

/** 一行「键 : 值」。值用 `<span>` 装：`.kv` 的排版靠它。 */
function kvRow(key, value, odId) {
  const row = el('div', 'kv');
  row.appendChild(el('span', 'k', key));
  const cell = el('span', 'v', value);
  if (odId) cell.setAttribute('data-od-id', odId);
  row.appendChild(cell);
  return row;
}

/** 「暂停 / 恢复」：写 `enabled`（ADR-0011）。它**不落消息、不唤醒、不消耗预算**；
    响应里的成员清单是本地的**新**镜像（配置变更不会经事件流广播，因此必须吃这一份响应）。 */
async function toggleAgentEnabled(memberId) {
  const agent = agentById(memberId);
  if (!agent) return;
  const next = !agent.enabled;
  try {
    const payload = await api('/api/agents/' + encodeURIComponent(memberId), {
      method: 'PATCH',
      body: { enabled: next },
    });
    applyAgentUpdate(payload);
    toast(t(next ? 'toast.agentResumed' : 'toast.agentPaused', { name: payload.agent.display_name }));
  } catch (err) {
    toast(t(next ? 'agentDetail.resumeFailed' : 'agentDetail.pauseFailed', { reason: err.message }), 'err');
  }
}

/** 把一次写路径的响应铺回界面：成员行、左栏、成员面板与详情页读的是同一份后端读数。 */
function applyAgentUpdate(payload) {
  if (payload.agents) state.agents = payload.agents;
  if (payload.members) state.members = payload.members;
  upsertAgent(payload.agent);
  renderMembers();
  renderDock();
  renderAgentDetail();
}

/* ─── 编辑配置（`PATCH /api/agents/{id}` 的人类面表单） ────────────────── */

// 编辑弹窗里的 provider 选择是否出自人的手（与新建弹窗的 `agentProviderTouched` 同一个理由：
// hydration 之前的占位默认值不是用户选择）。
let agentEditProviderTouched = false;

function openAgentEditModal(memberId) {
  const agent = agentById(memberId);
  if (!agent) return;
  state.agentEditId = memberId;
  agentEditProviderTouched = false;
  const box = $('#agentEditFormError');
  if (box) box.hidden = true;
  $('#agentEditName').value = agent.display_name || '';
  $('#agentEditRole').value = agent.role || '';
  $('#agentEditPromptInput').value = agent.system_prompt || '';
  // `model_id` 是 `provider:model`：界面把它拆成两个控件（与新建同形），保存时再拼回去。
  const modelId = String(agent.model_id || '');
  const cut = modelId.indexOf(':');
  $('#agentEditModelInput').value = cut >= 0 ? modelId.slice(cut + 1) : modelId;
  const preferred = cut >= 0 ? modelId.slice(0, cut) : (agent.provider || '');
  renderAgentEditProviderSelect(preferred);
  renderEditTier('#agentEditEnabled', 'enabled', agent.enabled !== false);
  renderEditTier('#agentEditAutoClaim', 'autoClaim', agent.auto_claim === true);
  renderAgentEditNotice();
  $('#agentEditOverlay').hidden = false;
  $('#agentEditName').focus();
}

function closeAgentEditModal() {
  state.agentEditId = null;
  $('#agentEditOverlay').hidden = true;
}

function renderAgentEditProviderSelect(preferred) {
  const select = $('#agentEditProviderSelect');
  fillProviderSelect(select, agentEditProviderTouched);
  if (!agentEditProviderTouched && preferred && state.providers.includes(preferred)) {
    select.value = preferred;
  }
  $('#agentEditProviderHint').textContent = providerHintText(select.value);
  return select.value;
}

/** 一个布尔档位组里的卡片集合：`selector` 是组元素，`key` 是组内卡片的 `dataset` 键。
    「启用状态」与「自动认领任务」两组共用**同一套** `.radio-card` 控件族与同一份判定，
    各自只换 selector 与 key（渲染与取值因此不可能两组各说一份）。
    遍历「档位卡片」而不是用 `[data-enabled="on"]` 这类**带值**的属性选择器：判定只认卡片自己的状态。 */
function editTierCards(selector, key) {
  const attribute = 'data-' + key.replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase());
  return Array.prototype.slice.call($(selector).querySelectorAll('[' + attribute + ']'));
}

/** 写一个布尔档位组的选中态（档位卡片自己带状态，应用里不另存一个布尔）。 */
function renderEditTier(selector, key, on) {
  editTierCards(selector, key).forEach((card) => {
    const selected = (card.dataset[key] === 'on') === Boolean(on);
    card.classList.toggle('is-on', selected);
    card.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
}

/** 读一个布尔档位组的当前取值：判定只认卡片自己的状态（与 `renderEditTier` 同一份卡片集合）。 */
function editTierValue(selector, key) {
  let on = false;
  editTierCards(selector, key).forEach((card) => {
    if (card.dataset[key] === 'on') on = card.classList.contains('is-on');
  });
  return on;
}

function renderAgentEditNotice() {
  renderRemoteNoticeBlock(AGENT_EDIT_NOTICE_IDS, $('#agentEditProviderSelect').value);
}

/** 保存：**整份字段一起提交**（部分更新的语义在后端，前端不做「哪些改过」的差量）——
    任一字段不合法时后端整体不写，错误原文照后端给的那句话显示。 */
async function submitAgentEdit() {
  const memberId = state.agentEditId;
  if (!memberId) return;
  const provider = $('#agentEditProviderSelect').value;
  const payload = {
    display_name: $('#agentEditName').value.trim(),
    role: $('#agentEditRole').value.trim(),
    model_id: provider + ':' + $('#agentEditModelInput').value.trim(),
    system_prompt: $('#agentEditPromptInput').value.trim(),
    enabled: editTierValue('#agentEditEnabled', 'enabled'),
    auto_claim: editTierValue('#agentEditAutoClaim', 'autoClaim'),
  };
  const box = $('#agentEditFormError');
  const submit = $('#agentEditSubmit');
  submit.disabled = true;
  try {
    const result = await api('/api/agents/' + encodeURIComponent(memberId), {
      method: 'PATCH',
      body: payload,
    });
    applyAgentUpdate(result);
    closeAgentEditModal();
    toast(t('toast.agentUpdated', { name: result.agent.display_name }));
  } catch (err) {
    if (box) {
      box.textContent = err.message;
      box.hidden = false;
    }
  } finally {
    submit.disabled = false;
  }
}

/** 编辑弹窗里的告知确认：与新建走**同一个**端点与同一套镜像更新（不写第二套）。 */
async function confirmAgentEditRemoteFlow() {
  await confirmRemoteFlow(
    $('#agentEditProviderSelect').value,
    $('#agentEditRemoteConfirm'),
    renderAgentEditNotice,
  );
}

/* ─── 成员名册（频道成员的**只读**全名单） ────────────────────────────────
   `CONTEXT.md` **成员名册**：内容 = 该频道后端成员表**逐条**（`GET /api/channels/{id}/members`，
   含 owner），每条带**状态点**（`GET /api/member-statuses` 的四态，取值走与**成员面板**同一个
   `statusOf()` / `presenceNode()`）。它与**成员面板**的分工是「动作」与「范围」两件事：
   名册**唯一动作是关闭**——加入 / 移除 / 编辑都不是它的面，因此这里没有那些控件，
   也不列「未加入」一段。 */

/** 该频道的**完整**成员表（含 owner）；`null` = 还没读到 / 读不到——与「真的没有成员」
    是两件事（与 `channelAgentMembers()` 同一条纪律，读的是同一份缓存，只是不按 kind 过滤）。 */
function channelMemberRows(channelId) {
  const members = state.channelMembers[channelId];
  return members || null;
}

/** 打开名册（入口 = 频道头部的成员按钮，原型 `#btnChannelMembers`）：锁定当前频道、
    按需读一次它的成员表，然后渲染。名册只对**频道**存在，DM 下入口隐藏（见 `renderHeader()`）。 */
function openRosterModal() {
  const channel = activeChannel();
  if (!channel) return;
  state.rosterChannelId = channel.id;
  $('#rosterOverlay').hidden = false;
  renderRoster();
  const close = $('#rosterClose');
  if (close) close.focus();
  // 名册的内容是后端成员表的读数：打开时读一次，读回后按**同一个频道**再渲染一遍
  // （成员在别处长出来后重新打开名册就能看到；弹窗关掉或换过频道就不动它）。
  refreshChannelMembers(channel.id).then(() => {
    if (state.rosterChannelId === channel.id) renderRoster();
  });
}

function closeRosterModal() {
  state.rosterChannelId = null;
  $('#rosterOverlay').hidden = true;
}

/** 名册的**唯一**渲染落点：打开、成员表读回、切语言三处都走它，于是「后端读到的成员表」
    与「名册上列出的成员」不可能各说一份（不在这里另写一遍过滤或排序规则）。 */
function renderRoster() {
  const channelId = state.rosterChannelId;
  if (!channelId) return;
  const channel = state.channels.find((item) => item.id === channelId) || null;
  const members = channelMemberRows(channelId);
  const agentsById = new Map(state.agents.map((agent) => [agent.id, agent]));
  const title = $('#rosterTitle');
  if (title) title.textContent = t('roster.title', { name: channel ? channel.name : '' });
  const sub = $('#rosterSub');
  // 副标题的数字就是成员表的**条数**；读不到时不写 0（那会把「读不到」说成「一个都没有」）。
  const loaded = !channelMembersFailed.has(channelId) && Boolean(members);
  if (sub) sub.textContent = loaded ? t('roster.sub', { count: members.length }) : '';
  const body = $('#rosterBody');
  if (!body) return;
  // 「读不到」先判：读失败时缓存会被置成空表，若不先看这个标记，下面那一步就会把
  // 「暂时读不到」说成「这个频道还没有成员」（与面板同一条纪律，只是名册连旧表也不列）。
  if (channelMembersFailed.has(channelId)) {
    replaceChildren(body, [el('div', 'rail-empty', t('roster.unavailable'))]);
    return;
  }
  if (!members) {
    replaceChildren(body, [el('div', 'rail-empty', t('roster.loading'))]);
    return;
  }
  if (!members.length) {
    replaceChildren(body, [el('div', 'rail-empty', t('roster.empty'))]);
    return;
  }
  replaceChildren(body, members.map((member) => rosterRow(member, agentsById)));
}

/** 名册的一行：头像 + 姓名 / 角色，右侧是状态点。owner 那行右侧是「我」——原型 `:1601-1629`
    与切片对 owner 的呈现就是「我」；`GET /api/member-statuses` 只覆盖 Agent，
    给 owner 造一个状态点等于伪造后端没有的事实。 */
function rosterRow(member, agentsById) {
  const agent = agentsById.get(member.id) || null;
  const row = el('div', 'roster-row');
  row.dataset.member = member.id;
  row.dataset.kind = member.kind;
  row.setAttribute('data-od-id', 'roster-member-' + member.id);
  const who = el('div', 'roster-who');
  who.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
  const text = el('div', 'roster-who-text');
  text.appendChild(el('b', 'roster-name', member.display_name));
  text.appendChild(el('span', 'roster-role', (agent && agent.role) || t('roster.roleFallback')));
  who.appendChild(text);
  row.appendChild(who);
  if (member.kind === 'owner') {
    row.appendChild(el('span', 'roster-me', t('identity.me')));
  } else {
    // 状态点按成员 id 查（Agent 配置快照里的 `status` 是后端 `member-statuses` 的回写；
    // 快照里没有这个 Agent 时直接查状态表，而不是凭缺失下一句「未启用」的结论）。
    row.appendChild(presenceNode(statusOf(agent || { id: member.id })));
  }
  return row;
}

function renderTyping() {
  const target = activeTarget();
  const container = $('#typingLine');
  const memberId = target ? state.typing[target.id] : null;
  const agent = state.agents.find((item) => item.id === memberId);
  container.hidden = !agent;
  container.textContent = agent ? t('typing.working', { name: agent.display_name }) : '';
}

/* ─── 频道回应预算（#54 / ADR-0007 / 规格 #31 §2）：读数是后端事实的镜像，写控件只表达**上界** ──

   `CONTEXT.md` **回应预算**：每个 target 每个**人类世代**最多可发生的 Agent→Agent 跳数。上界是
   **库内逐频道配置**（ADR-0007），人类面只能改它；用量（`used`）是后端的正本——后端在人类消息
   落库开世代时重置、并只在一次**实际投递**的跳上 +1。因此这张频道设置弹窗里：

   - 只读的四项读数（已用 / 剩余 / 有效上界 / **来源**）与待生效值全部来自
     `GET /api/channels/{id}/budget`，前端不计算、不推断、也不保存跨会话的旧读数；
   - 写控件只有固定档位 3 / 5 / 10（默认 3），提交 `{budget_limit: <档位>}`，**绝不提交**任何用量
     字段（后端也会整体拒绝：用量不可写，`error.budget_used_read_only`）；
   - 保存后按**响应里的新读数**重铺（一次调用即刷新），并明确提示「从下一个人类世代生效」——
     当前世代的 `limit` / `used` / `remaining` 不变由后端保证，前端不乐观改写本地读数。 */

/** 固定档位（与后端 `BUDGET_LIMIT_TIERS` 同序同名）；默认档位是后端内置默认 3（ADR-0002 未修订）。 */
const BUDGET_LIMIT_TIERS = [3, 5, 10];
const BUDGET_DEFAULT_LIMIT = 3;

/** 来源取值的界面说法（键名与后端 `source` 的取值同名；未知取值原样回退，不伪造一个说法）。 */
function budgetSourceLabel(source) {
  const key = 'budget.source.' + source;
  return hasKey(key) ? t(key) : source;
}

/** 弹窗里「已保存的档位」：待生效值优先；否则「当前有效上界就是频道覆盖」那份；再否则内置默认。 */
function savedBudgetTier(budget) {
  if (!budget) return BUDGET_DEFAULT_LIMIT;
  const pending = budget.pending_limit;
  if (pending !== null && pending !== undefined) return Number(pending);
  return budget.source === 'channel-override' ? Number(budget.limit) : BUDGET_DEFAULT_LIMIT;
}

/** 弹窗的空镜像（打开 / 关闭都用它，于是「缓存了过期读数」这个中间态不存在）。 */
function emptyBudgetView() {
  return { channelId: null, budget: null, selected: null, loading: false, error: '' };
}

/** 打开弹窗（入口 = 频道头的预算按钮）：锁定当前频道、先显示「读取中」，随后按后端读数渲染。 */
async function openBudgetModal() {
  const channel = activeChannel();
  if (!channel) return;
  state.channelBudget = {
    channelId: channel.id,
    budget: null,
    selected: null,
    loading: true,
    error: '',
  };
  $('#budgetOverlay').hidden = false;
  const box = $('#budgetFormError');
  if (box) box.hidden = true;
  renderBudgetModal();
  await loadChannelBudget(channel.id);
}

/** 读一次该频道的预算读数（正本是后端）。读回后按**同一个频道**重铺——关掉 / 换过频道就不动它。 */
async function loadChannelBudget(channelId) {
  try {
    const payload = await api(channelPath(channelId, '/budget'));
    if (state.channelBudget.channelId !== channelId) return;
    state.channelBudget.budget = payload.budget;
    state.channelBudget.selected = savedBudgetTier(payload.budget);
    state.channelBudget.loading = false;
    state.channelBudget.error = '';
  } catch (err) {
    if (state.channelBudget.channelId !== channelId) return;
    state.channelBudget.loading = false;
    state.channelBudget.error = err.message;
  }
  renderBudgetModal();
}

function closeBudgetModal() {
  state.channelBudget = emptyBudgetView();
  $('#budgetOverlay').hidden = true;
  const box = $('#budgetFormError');
  if (box) box.hidden = true;
}

/** 选中一个档位（**还没提交**的呈现状态）。不在固定档位里的取值连选中都不成立。 */
function selectBudgetTier(tier) {
  const value = Number(tier);
  if (!BUDGET_LIMIT_TIERS.includes(value)) return;
  if (!state.channelBudget.channelId) return;
  state.channelBudget.selected = value;
  renderBudgetModal();
}

/** 保存选中的档位：写后端（正本），成功后按响应里的新读数重铺并提示下个世代生效。

    失败时**不**改本地读数——界面不能声称一个没落库的档位（与 `saveSettings` 同一条纪律）。 */
async function submitBudgetTier() {
  const view = state.channelBudget;
  const channelId = view.channelId;
  const tier = view.selected;
  if (!channelId || tier === null || !BUDGET_LIMIT_TIERS.includes(Number(tier))) return;
  const box = $('#budgetFormError');
  if (box) box.hidden = true;
  try {
    const payload = await api(channelPath(channelId, '/budget'), {
      method: 'PATCH',
      body: { budget_limit: Number(tier) },
    });
    if (state.channelBudget.channelId !== channelId) return;
    state.channelBudget.budget = payload.budget;
    state.channelBudget.selected = savedBudgetTier(payload.budget);
    state.channelBudget.error = '';
    state.channelBudget.loading = false;
    renderBudgetModal();
    toast(t('budget.saved', { limit: Number(tier) }));
  } catch (err) {
    if (state.channelBudget.channelId !== channelId) return;
    if (box) {
      box.textContent = t('budget.saveFailed', { reason: err.message });
      box.hidden = false;
    }
    renderBudgetModal();
  }
}

/** 一行说明（`.field .hint` 那一档的排版，不新造样式）。 */
function budgetHint(key, odId) {
  const field = el('div', 'field');
  const hint = el('div', 'hint', t(key));
  if (odId) hint.setAttribute('data-od-id', odId);
  field.appendChild(hint);
  return field;
}

/** 弹窗的唯一渲染落点：读数 / 待生效 / 写控件都从这里出，于是「读数」与「可写档位」不会各说一份。 */
function renderBudgetModal() {
  const view = state.channelBudget;
  const body = $('#budgetBody');
  if (!body) return;
  const channel = view.channelId ? state.channels.find((item) => item.id === view.channelId) : null;
  const title = $('#budgetTitle');
  if (title) title.textContent = t('budget.title');
  const sub = $('#budgetSub');
  if (sub) sub.textContent = t('budget.sub', { name: channel ? channel.name : '' });
  if (!view.channelId) {
    replaceChildren(body, []);
    return;
  }
  if (view.loading) {
    replaceChildren(body, [el('div', 'rail-empty', t('budget.loading'))]);
    return;
  }
  if (view.error) {
    replaceChildren(body, [el('div', 'rail-empty', t('budget.loadFailed', { reason: view.error }))]);
    return;
  }
  const budget = view.budget;
  if (!budget) {
    replaceChildren(body, []);
    return;
  }

  const nodes = [];

  // ① 当前人类世代：四项读数，`remaining` 是后端按**当前有效上界**算好的那一份。
  const current = el('section', 'd-sec');
  current.appendChild(sectionTitle('budget.sectionCurrent', 'budget-section-current'));
  current.appendChild(kvRow(t('budget.used'), String(budget.used), 'budget-used'));
  current.appendChild(kvRow(t('budget.remaining'), String(budget.remaining), 'budget-remaining'));
  current.appendChild(kvRow(t('budget.limit'), String(budget.limit), 'budget-limit'));
  current.appendChild(kvRow(t('budget.source'), budgetSourceLabel(budget.source), 'budget-source'));
  if (Number(budget.remaining) === 0) {
    // 预算是**耗尽**，不是出错：它和轮次结果里的 `budget-exhausted` 是同一条事实
    // （消息照常落库、零投递），因此**不进错误区**，只在读数里说清楚。
    current.appendChild(kvRow(t('budget.state'), t('budget.exhausted'), 'budget-exhausted'));
    current.appendChild(budgetHint('budget.exhaustedNote', 'budget-exhausted-note'));
  }
  nodes.push(current);

  // ② 下一个人类世代：待生效值与当前有效上界**分开**一行，避免把待生效配置误读成当前额度。
  const next = el('section', 'd-sec');
  next.appendChild(sectionTitle('budget.sectionNext', 'budget-section-next'));
  const pending = budget.pending_limit;
  const hasPending = pending !== null && pending !== undefined;
  next.appendChild(
    kvRow(t('budget.pending'), String(hasPending ? pending : budget.limit), 'budget-pending'),
  );
  next.appendChild(
    budgetHint(
      hasPending ? 'budget.pendingEffective' : 'budget.pendingSame',
      'budget-pending-note',
    ),
  );
  nodes.push(next);

  // ③ 写控件：只有固定三档（没有可输入任意数字的控件 ⇒ 非法值不会被提交）。
  const tiers = el('section', 'd-sec');
  tiers.appendChild(sectionTitle('budget.tierLabel', 'budget-section-tiers'));
  const row = el('div', 'radio-row');
  BUDGET_LIMIT_TIERS.forEach((tier) => {
    const on = Number(view.selected) === tier;
    const card = el('button', 'radio-card');
    card.type = 'button';
    card.dataset.budgetTier = String(tier);
    card.setAttribute('data-od-id', 'budget-tier-' + tier);
    card.setAttribute('aria-pressed', on ? 'true' : 'false');
    card.classList.toggle('is-on', on);
    card.appendChild(el('b', null, String(tier)));
    card.appendChild(
      el(
        'span',
        null,
        tier === BUDGET_DEFAULT_LIMIT ? t('budget.tierDefaultUnit') : t('budget.tierUnit'),
      ),
    );
    row.appendChild(card);
  });
  tiers.appendChild(row);
  tiers.appendChild(budgetHint('budget.tierHint', 'budget-tier-hint'));
  const actions = el('div', 'agent-actions');
  const save = el('button', 'btn btn-primary', t('budget.save'));
  save.type = 'button';
  save.dataset.budgetSave = '1';
  save.setAttribute('data-od-id', 'budget-save');
  // 「与已保存的档位相同」时没有可提交的改动：按钮禁用，而不是发一个空转的 PATCH。
  save.disabled =
    !state.online || view.selected === null || Number(view.selected) === savedBudgetTier(budget);
  actions.appendChild(save);
  tiers.appendChild(actions);
  nodes.push(tiers);

  replaceChildren(body, nodes);
}


function renderHeader() {
  const target = activeTarget();
  const hasTarget = Boolean(target);
  syncChromeVisibility(state.mode);
  if (!hasTarget) {
    closeMentionPicker();
    return;
  }
  const isDm = target.kind === 'dm';
  const name = isDm ? target.peer.display_name : target.name;
  // 名册是**频道**成员的只读全名单（`CONTEXT.md` **成员名册**）：DM 下没有这条面，
  // 入口随之隐藏——不留一个按下去什么都不发生的按钮。
  $('#btnChannelMembers').hidden = isDm;
  // 回应预算同样是**频道**配置（库内逐频道项，ADR-0007）：DM / 任务型 target 没有这条设置面，
  // 入口随之隐藏（预算读数的路径 `/api/channels/{id}/budget` 只认频道）。
  $('#btnChannelBudget').hidden = isDm;
  // DM 的标题带 `@` 前缀（原型 `renderHeader()` 的 DM 分支写的就是 `'@' + m.name`）。
  $('#chanHash').hidden = isDm;
  $('#chanTitle').textContent = isDm ? '@' + name : name;
  // desc 保持现状的写法：原型那句「懒创建会话」与 `CONTEXT.md` **私信**词条的
  // `_Avoid_`（明确否定「懒创建」）冲突，是原型的过时文案，不照抄。
  $('#chanDesc').textContent = isDm ? t('header.dmDesc', { name }) : target.description || '';
  $('#composerInput').placeholder = isDm
    ? t('header.dmPlaceholder', { name })
    : t('header.channelPlaceholder', { name });
}

/** 移动端顶部栏的标题（≤900px）：**当前 target 名**，形态跟频道头一致 ——
    频道是 `#名`、私信是 `@名`（原型 `alibot-app.html:1647,1655` 对 `#mtTitle` 的两处赋值）。
    `>900px` 时顶栏 `display:none`，这次赋值没有可见效果 —— 但它与断点无关地保持正确，
    窗口宽度在两边来回时不会留下一条旧标题。没有当前 target 时退回品牌名：
    顶栏是常显件，不留一条空标题。（`alibot` 是本页的品牌名，`index.html` 的品牌块同样
    直接写它、不进词条表。） */
function renderMobileTopbar() {
  const title = $('#mtTitle');
  if (!title) return;
  const target = activeTarget();
  if (!target) {
    title.textContent = 'alibot';
    return;
  }
  title.textContent = target.kind === 'dm' ? '@' + target.peer.display_name : '#' + target.name;
}

function renderConnection() {
  $('#connBanner').hidden = state.online;
  $('#composer').classList.toggle('is-disabled', !state.online);
  const stream = $('#streamState');
  if (stream) {
    const reconnecting = state.online && state.streamState !== 'open';
    stream.hidden = !reconnecting;
    stream.textContent = reconnecting ? t('conn.reconnecting') : '';
  }
  const taskInput = $('#taskComposerInput');
  const taskSend = $('#taskSend');
  if (taskInput && taskSend) taskSend.disabled = !state.online || !taskInput.value.trim();
}

function renderMessages() {
  const target = activeTarget();
  const targetMode = state.mode === 'channel';
  $('#emptyNoChannel').hidden = !targetMode || Boolean(target);
  const messages = targetMode && target ? renderedMessages() : [];
  $('#emptyNoMessage').hidden = !target || messages.length > 0;

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
    // 轮次结果挂在**触发它的那条消息**下面（按 base_seq 绑定），不是与消息无关的横幅。
    // 同一条消息下**至多**两块：一块默认折叠的摘要行 + 每个 `failed` 各一块（直接展开）。
    if (message.seq) {
      const byMember = state.outcomes[message.seq];
      if (byMember) {
        const records = Object.keys(byMember).flatMap((memberId) => byMember[memberId]);
        const summary = roundOutcomeSummaryNode(records);
        if (summary) nodes.push(summary);
        records
          .filter((record) => record.status === 'failed')
          .forEach((record) => nodes.push(roundOutcomeNode(record)));
      }
    }
  });
  replaceChildren($('#messageList'), nodes);
  // 页签里的消息计数就是「消息流条数」，与这一处渲染同一个来源，因此跟着它一起刷新。
  renderTabs();
}

/** 摘要行要覆盖的四种轮次结果（`failed` 直接展开，`replied` 由它自己的回复消息承载）。 */
function summarizableOutcomes(records) {
  return records.filter((record) => SUMMARY_STATUSES.includes(record.status));
}

/**
 * 把同一条消息（同一 `base_seq`）下的**非 `failed`** 轮次结果收成**一行**可折叠摘要（或零行）。
 *
 * 为什么收成一行：`ignored` 在当前唤醒面（频道内全部 agent 成员）下会按成员各有一条，
 * 那是 append-only 的取证面、**不得**在后端过滤掉；但逐成员铺在消息流里会淹没对话。
 * 因此只在**呈现**层合并：默认折叠（一行说清「谁没有回应、为什么是正常选择」），
 * 展开后才逐成员给出 `status` 中文说法 + 后端 `reason` 原文。
 *
 * 它**不是消息**：不占 `seq`（刻意用 `data-base-seq`）、没有头像、不参与「数几条消息」的统计。
 */
function roundOutcomeSummaryNode(records) {
  const items = summarizableOutcomes(records);
  if (!items.length) return null;
  const baseSeq = items[0].base_seq;
  const expanded = Boolean(state.expandedOutcomes[baseSeq]);

  const row = el('article', 'msg outcome-summary');
  row.dataset.baseSeq = baseSeq;
  // 与失败行同一个理由：这一行不是一条消息，不能被数进「几条消息」。
  row.setAttribute('role', 'status');
  row.setAttribute('aria-label', t('outcome.summaryAria', { names: summarizeNames(items), count: items.length }));

  const body = el('div', 'msg-body');
  const head = el('div', 'msg-head');

  // 折叠控件是**原生 `<button>`**：它天然可 Tab 聚焦、可 Enter/Space 激活，且 `aria-expanded`
  // 让展开态对屏幕阅读器可感知。不复用 `msg-outcome-act`（那是失败行里的「看成员状态」，
  // 动词开头的一条动作链接），它是另一件事。
  const toggle = el('button', 'msg-outcome-summary-toggle');
  toggle.dataset.summaryToggle = String(baseSeq);
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  toggle.setAttribute('aria-controls', 'outcome-summary-' + baseSeq);
  toggle.appendChild(el('span', 'msg-outcome-summary-mark', expanded ? '\u25be' : '\u25b8'));
  toggle.appendChild(el('span', 'msg-outcome-summary-names', summarizeNames(items)));
  toggle.appendChild(el('span', 'msg-outcome-summary-text', expanded ? t('outcome.collapse') : t('outcome.expand')));
  head.appendChild(toggle);
  head.appendChild(el('span', 'msg-time', timeLabel(items[0].finished_at)));
  body.appendChild(head);

  // 折叠态：一行说清「为什么」——四态的**后端事实不同，说法就不同**，不得混为一谈。
  // 只有一种状态时不重复上一行的名单（名单已经在折叠控件里）；混合状态时才逐组点名，
  // 否则读不出「是谁因为哪条原因」。
  body.appendChild(el('div', 'msg-outcome-summary-why', summarizeWhy(items)));

  // 逐成员明细**恒常渲染**、用 `hidden` 切可见性（而不是折叠时不存在）：
  // `aria-controls` 指向的 id 必须在任何时刻都能解析到，否则折叠态的 `aria-controls`
  // 就是一个悬空引用（无障碍与被自动化取证都会踩到）。
  const details = el('div', 'msg-outcome-details');
  details.id = 'outcome-summary-' + baseSeq;
  details.hidden = !expanded;
  items.forEach((record) => details.appendChild(roundOutcomeSummaryLine(record)));
  body.appendChild(details);

  row.appendChild(body);
  return row;
}

/** 摘要行的成员名单：按后端返回序（`finished_at, id` 升序）逐个列出，超过 2 人写成「等 N 人」。 */
function summarizeNames(items) {
  return joinNames(items.map((record) => outcomeMemberName(record.member_id)));
}

/** 名单的**唯一**拼法：≤2 个逐个列出，>2 个写「甲、乙 等 N 人」（摘要行与折叠说明共用）。 */
function joinNames(names) {
  if (names.length <= 2) return names.join(t('common.nameListSep'));
  const head = names[0] + t('common.nameListSep') + names[1];
  return t('common.nameListMore', { names: head, count: names.length });
}

/**
 * 折叠行那一句「为什么」：把成员按 status 归组，每组用自己的后端事实说法（不把三态混为一谈）。
 *
 * 例：「阿墨、云舟：自判忽略（正常的协议选择，不是失败）；风间：预算已用尽，本轮零投递……」。
 */
function summarizeWhy(items) {
  const order = [];
  const groups = new Map();
  items.forEach((record) => {
    // 按 status 归组（不是按 label）：同一状态的成员聚在一起，各自用自己的后端事实说法。
    if (!groups.has(record.status)) {
      groups.set(record.status, []);
      order.push(record.status);
    }
    groups.get(record.status).push(outcomeMemberName(record.member_id));
  });
  const mixed = order.length > 1;
  return order
    .map((status) => {
      const text = outcomeNote(status);
      // 单一状态：名单已在折叠控件上，这里只陈述「为什么」（不重复一遍名字）。
      if (!mixed) return text;
      return joinNames(groups.get(status)) + t('common.colonSep') + text;
    })
    .join(t('common.clauseSep'));
}

/**
 * 展开后的一行：一个成员的 `status` 中文说法 + 后端给出的 `reason` **原文**。
 *
 * `reason` 已是可读中文，因此直接呈现——不重新措辞、不截断成省略号（票面硬要求）。
 * 成员名与状态词用 `--faint`/`--muted` 一档：这四态都**不是失败**，不得使用 `--error`。
 */
function roundOutcomeSummaryLine(record) {
  const line = el('div', 'msg-outcome-line');
  line.dataset.outcome = record.id;
  line.dataset.member = record.member_id;
  line.dataset.status = record.status;
  const head = el('div', 'msg-outcome-line-head');
  head.appendChild(el('span', 'msg-outcome-line-author', outcomeMemberName(record.member_id)));
  head.appendChild(el('span', 'msg-outcome-line-state', outcomeLabel(record.status)));
  head.appendChild(el('span', 'msg-time', timeLabel(record.finished_at)));
  line.appendChild(head);
  const reason = el('div', 'msg-outcome-line-reason', record.reason || t('outcome.lineReasonEmpty'));
  // 与失败行的取证 id 同一前缀（`round-reason-<id>`）：同一字段两个前缀只会让取证混淆。
  reason.dataset.odId = 'round-reason-' + record.id;
  line.appendChild(reason);
  return line;
}

/** 成员显示名：拿不到成员表时退回 id（与失败行同一个纪律，不伪造名字）。 */
function outcomeMemberName(memberId) {
  const member = state.members.find((item) => item.id === memberId);
  return member ? member.display_name : memberId;
}

/**
 * 把**一条** `failed` 轮次结果渲染成消息流里直接展开的一块（非 `failed` 返回 `null`）。
 *
 * 失败是用户主诉的缺口（含「被点名却不回应」），因此它**不藏在折叠后面**：既有的呈现
 * （「未回应」tag、`这一轮失败`/`没启用` 状态词、`看成员状态`、reason 正文）逐字保留，
 * 只把它并入与摘要行同一个消息锚点（同一 `base_seq`）下。三者由 `renderMessages()` 调度：
 * 摘要行在前、failed 块在后（失败文本更重，放最后离触发消息更近）。
 */
function roundOutcomeNode(record) {
  if (!record || record.status !== 'failed') return null;
  const member = state.members.find((item) => item.id === record.member_id);
  const agent = state.agents.find((item) => item.id === record.member_id);
  // 状态未知时**不声明**任何成员状态：宁可不提，也不能把「我不知道」说成「未启用」。
  const status = agent ? statusOf(agent) : null;
  const disabled = status === 'offline';
  const name = member ? member.display_name : record.member_id;

  const row = el('article', 'msg outcome');
  row.dataset.outcome = record.id;
  row.dataset.member = record.member_id;
  // 刻意**不**用 `data-seq`：那个属性在本文件里一直是「这一行是 seq=N 的消息」的意思。
  // 轮次结果不是消息，它只是被那条消息触发，所以用不同的名字。
  row.dataset.baseSeq = record.base_seq;
  // 这一行不是消息（不占 seq、不是作者说的话），因此标记成 status 而不是 message：
  // 屏幕阅读器与自动化取证都不该把它当成「又一条消息」。
  row.setAttribute('role', 'status');
  row.setAttribute('aria-label', t('outcome.failedAria', { name, reason: record.reason || t('outcome.lineReasonEmpty') }));

  const body = el('div', 'msg-body');
  const head = el('div', 'msg-head');
  // 作者名用**自己的**类名而不是 `is-agent`：这一行不是一条 Agent 消息，
  // 任何「数几条 Agent 消息」的统计都不该把它数进去。
  head.appendChild(el('span', 'msg-author msg-outcome-author', name));
  head.appendChild(el('span', 'msg-tag', t('outcome.tag')));
  if (status) head.appendChild(el('span', 'msg-outcome-state', disabled ? t('outcome.stateDisabled') : t('outcome.stateFailed')));
  if (!disabled && agent) {
    // 从失败现场直接打开成员详情，不用人先找到右上角那个图标。
    const inspect = el('button', 'msg-outcome-act', t('outcome.inspect'));
    inspect.dataset.dockToggle = '1';
    head.appendChild(inspect);
  }
  head.appendChild(el('span', 'msg-time', timeLabel(record.finished_at)));
  body.appendChild(head);

  // 「未启用」是成员**此刻**的状态，「这一轮失败」是**库里那一次**轮次的结论。
  // 两者会同时为真，因此分开陈述，而不是把后者说成前者、也不把前者冒充后者。
  if (disabled) {
    body.appendChild(el('div', 'msg-outcome-note', t('outcome.disabledNote', { name })));
  }
  const reason = el('div', 'msg-outcome-reason', record.reason || t('outcome.lineReasonEmpty'));
  reason.dataset.odId = 'round-reason-' + record.id;
  body.appendChild(reason);

  row.appendChild(body);
  return row;
}

/* ─── 旁白式交代的**呈现降档**（#57；#35 的 D1 判据-4 + D4 形态-a）───────────

   旁白式交代 = Agent 把「我不需要说话」当成正文说出来（「（本轮消息与我无关，无需回应）」）。
   它**仍是**一条真实 Agent 消息：占自己的 `seq`、计未读、照旧唤醒与花预算——这些数据面后果按
   #35 登记为**已知代价**，本票不改，也不在这里补。本票只把它的**呈现**降一档。

   判据是**形态**，不是措辞表：整条正文被**同一对圆括号**从头包到尾、只有一行、而且很短。
   因此这里既没有正则词表、也没有「无需回应」这类字样清单——换措辞、换语言都不会让判据失效。
   **失效方向可放松**：判据拿不准（多行、超长、中途就闭合、方括号/书名号、不是 Agent 发的）时
   一律按普通消息呈现，宁可少降档，不可误吞真实回复。

   长度上限取 24 个字符：整条括注式交代是一句自我说明（「（本轮消息与我无关，无需回应）」是 15 个
   字符），超过这个长度的括注更可能是真的在说一件事（引用、长句说明），按真实回复对待。 */
const NARRATION_MAX_LENGTH = 24;
/** 成对的圆括号：只认圆括号，不认方括号 / 书名号（那些是标题式强调，可能就是真实回复）。 */
const NARRATION_PAREN_PAIRS = { '\uFF08': '\uFF09', '(': ')' };

/** 这一条消息是不是旁白式交代；是就返回它降档后要显示的正文（已去首尾空白），否则 `null`。 */
function narrationOf(message) {
  // 只判**已落库的 Agent 消息**：还没落库的乐观发送项没有 seq，也不该在这里被判定。
  if (!message || message.kind !== 'agent') return null;
  if (!Number.isInteger(message.seq) || message.seq <= 0) return null;
  const body = typeof message.body === 'string' ? message.body.trim() : '';
  if (!body) return null;
  // 按**码点**数长度（`body.length` 数的是 UTF-16 单元，会把汉字算成 1、生僻字算成 2）。
  const chars = Array.from(body);
  if (chars.length > NARRATION_MAX_LENGTH) return null;
  // 多行的括注更可能是真内容（清单 / 引用），不降档。
  if (/[\r\n]/.test(body)) return null;
  const close = NARRATION_PAREN_PAIRS[chars[0]];
  if (!close || chars[chars.length - 1] !== close) return null;
  let depth = 0;
  for (let index = 0; index < chars.length; index += 1) {
    if (chars[index] === chars[0]) depth += 1;
    else if (chars[index] === close) {
      depth -= 1;
      // 括号在**中途**就回到 0，说明它不是「整条被一对括住」（例如「（一）（二）」）。
      if (depth === 0 && index !== chars.length - 1) return null;
    }
  }
  return depth === 0 ? body : null;
}

/**
 * 把一条旁白式交代渲染成**一行**、低权重、可展开的消息行（`D4` 形态-a）。
 *
 * 形态-a 的三条硬约束都在这里：
 * 1. **它仍是消息**：占自己的 `seq`（`data-seq`）、有作者与时间、`role` 保持默认（不是摘要行的
 *    `role="status"`）。因此「数几条消息」的口径一个字都不用改。
 * 2. **一行**：正文与作者名同处 `.msg-head` 这一行（不再有独立的 `.msg-text` 段落），折叠态就是一行。
 * 3. **可展开看关联轮次结果的 `reason`**：这一行按钮展开的是**它自己那一轮**的结论——按这条消息
 *    的 `seq` 去 `state.replyOutcomes`（`round_outcome.reply_seq`）直取，不跨数据面拼装、也不并入
 *    摘要行。查不到时如实说「读不到」，不编造理由。
 */
function narrationNode(message, body) {
  const author = message.author || state.member;
  const seq = String(message.seq);
  const expanded = Boolean(state.expandedNarrations[seq]);
  const record = state.replyOutcomes[message.seq] || null;

  const article = el('article', 'msg narration');
  article.dataset.seq = message.seq;
  // 头像降一档（`sm`）：它与正文一起构成「低权重」，但作者身份照旧是它自己的色位。
  article.appendChild(avatarNode(author.display_name, author.avatar_slot, 'sm'));
  const bodyNode = el('div', 'msg-body');
  const line = el('div', 'msg-head');
  line.appendChild(el('span', 'msg-author is-agent', author.display_name));
  line.appendChild(el('span', 'msg-tag', t('narration.tag')));
  line.appendChild(el('span', 'msg-narration-text', body));

  const toggle = el('button', 'msg-narration-toggle');
  toggle.dataset.narrationToggle = seq;
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  toggle.setAttribute('aria-controls', 'narration-reason-' + seq);
  const action = expanded ? t('narration.collapse') : t('narration.expand');
  toggle.setAttribute('aria-label', t('narration.toggleAria', { name: author.display_name, action }));
  toggle.appendChild(el('span', 'msg-narration-mark', expanded ? '\u25be' : '\u25b8'));
  toggle.appendChild(el('span', 'msg-narration-toggle-text', action));
  line.appendChild(toggle);
  line.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
  bodyNode.appendChild(line);

  // 明细**恒常渲染**、用 `hidden` 切可见性（与摘要行同一条理由）：`aria-controls` 指向的 id 在
  // 任何时刻都要能解析到，否则折叠态就是一个悬空引用。`reason` 是后端原文，不重新措辞、不截断。
  const details = el('div', 'msg-narration-reason');
  details.id = 'narration-reason-' + seq;
  details.hidden = !expanded;
  if (record) {
    details.dataset.odId = 'round-reason-' + record.id;
    details.textContent = record.reason || t('outcome.lineReasonEmpty');
  } else {
    details.textContent = t('narration.reasonMissing');
  }
  bodyNode.appendChild(details);

  article.appendChild(bodyNode);
  return article;
}

function messageNode(message) {
  // 旁白式交代先判：它是最省的一种呈现，且必须**永远**保留消息身份（seq / 作者 / 时间）。
  const narration = narrationOf(message);
  if (narration !== null) return narrationNode(message, narration);
  if (message.kind === 'system') {
    // 系统事件消息（穿透加入）：沿用既有消息行形态——方形头像色位 + 作者名 + 时间。
    // 不新造色位：作者名 + 头像用**该成员自己的** --av-* 色位（与他说话时同一个），
    // 正文降一档到 --muted 以便与人类/Agent 正文区分。
    const author = message.author || {};
    const article = el('article', 'msg sys');
    article.dataset.seq = message.seq;
    article.appendChild(avatarNode(author.display_name, author.avatar_slot, 'sm'));
    const body = el('div', 'msg-body');
    const head = el('div', 'msg-head');
    head.appendChild(el('span', 'msg-author', author.display_name || t('msg.systemAuthor')));
    head.appendChild(el('span', 'msg-tag', t('msg.tagEvent')));
    head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
    body.appendChild(head);
    body.appendChild(el('div', 'msg-text', message.body));
    appendAttachmentList(body, message);
    appendTaskRefChip(body, message);
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
  if (author.kind === 'agent') head.appendChild(el('span', 'msg-tag', t('msg.tagAgent')));
  head.appendChild(el('span', 'msg-time', timeLabel(message.created_at)));
  // 两个 capsule 的字面量走 CSS 的 `content: attr(data-state-label)`：语言取值只在这一处，
  // CSS 里不放任何语言文案（否则切语言后它们会留在上一个语言）。
  if (pending) head.dataset.stateLabel = t('msg.pendingTag');
  if (failed) head.dataset.stateLabel = t('msg.failedTag');
  body.appendChild(head);
  body.appendChild(messageText(message));
  appendAttachmentList(body, message);
  appendTaskRefChip(body, message);

  if (failed) {
    const retry = el('div', 'msg-retry');
    retry.appendChild(el('span', null, t('msg.notDelivered', { reason: message.reason || t('msg.sendFailed') })));
    const retryBtn = el('button', 'btn btn-sm', t('msg.retry'));
    retryBtn.dataset.retry = message.clientId;
    const discardBtn = el('button', 'btn btn-sm', t('msg.discard'));
    discardBtn.dataset.discard = message.clientId;
    retry.appendChild(retryBtn);
    retry.appendChild(discardBtn);
    body.appendChild(retry);
  }

  article.appendChild(body);
  return article;
}

function appendTaskRefChip(body, message) {
  if (!message.task_ref) return;
  const referencedTask = taskById(message.task_ref);
  const title = referencedTask ? referencedTask.title : message.task_ref;
  const chip = el('button', 'task-ref-chip', t('msg.taskRef', { title }));
  chip.type = 'button';
  chip.dataset.openTask = message.task_ref;
  chip.setAttribute('aria-label', t('msg.taskRefAria', { title }));
  body.appendChild(chip);
}

function dayLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return t('time.unknown');
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return t('time.today');
  return localDateString(date);
}

/** 本机日期的 `YYYY-MM-DD` 串（`dayLabel` 与提醒日期格的默认值共用同一个拼法）。 */
function localDateString(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
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
  $('#btnSend').disabled =
    !$('#composerInput').value.trim() ||
    !state.online ||
    !activeTarget() ||
    state.mode !== 'channel' ||
    // 上传还没落地时不能发：那会把还没拿到标识的文件丢在这条消息之外。
    attachingCount > 0;
}

/** 清空主 composer 的输入区：正文、自适应高度、发送键、候选清单一起收。
    两条路径共用同一件事「正文已经不在输入框里了」：消息送出（`sendMessage`）与
    「作为任务发送」成功把正文变成任务标题（D6）。

    **不**清附件草稿：这条路径只说「正文不在输入框里了」。消息送出会另调
    `clearComposerAttachments()` 把草稿交给那条消息；「作为任务发送」则**留着**草稿——
    任务不需要附件（D15），静默丢掉人选好的文件才是更难解释的行为。 */
function resetComposerInput() {
  $('#composerInput').value = '';
  autosizeComposer();
  syncSendButton();
  // 清单描述的是刚消失的那段前缀，不能留在屏上。
  closeMentionPicker();
}

/* ─── 附件（#67 / ADR-0014）：两步上传 —— 先拿标识，随消息提交时绑定 ──────────
   前端只做两件机械事：把选中的文件按**字节**POST 成一个草稿、把后端返回的标识攒在手上。
   「绑定」发生在发送那条消息的**同一个请求**里（后端在同一事务内完成），前端不另开一条路径，
   也不提前把草稿画成「已发送」——未绑定的草稿在读取面里不存在，前端也就不该假装它存在。 */

/** 正在上传的个数：上传期间发送键禁用（否则会把还没拿到标识的文件丢在消息之外）。 */
let attachingCount = 0;

/** 附件体积的界面说法。单位符号与语言无关，因此不进词条表（词条表里没有裸符号的位置）。 */
function formatBytes(size) {
  const bytes = Number(size) || 0;
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/** 上传一个文件成**草稿**，返回附件元数据（标识 / 原名 / 大小 / MIME / 内容 url）。

    请求体是文件的原始字节、`Content-Type` 是声明的 MIME、文件名在 query 上：与后端
    `POST /api/attachments` 的契约一致（那个端点因此不需要 multipart 解析）。
    拒绝原因由后端按**语言偏好**渲染，这里只把它读出来给调用方。 */
async function uploadAttachment(file) {
  const response = await fetch('/api/attachments?name=' + encodeURIComponent(file.name || ''), {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const error = new Error(readError(payload, response));
    error.status = response.status;
    throw error;
  }
  return payload.attachment;
}

/** 选中的文件逐个上传成草稿；单个失败只影响它自己（可读原因进 toast），已成功的留在手上。 */
async function attachFiles(files) {
  const list = Array.from(files || []);
  if (!list.length) return;
  attachingCount += list.length;
  renderComposerState();
  for (const item of list) {
    try {
      state.composerAttachments.push(await uploadAttachment(item));
    } catch (err) {
      toast(t('toast.attachFailed', { reason: err.message }), 'err');
    } finally {
      attachingCount -= 1;
      renderComposerState();
    }
  }
}

/** 移除一个还没发送的草稿（只从手上移走；库里的草稿行与字节仍在——没有删除端点）。 */
function removeComposerAttachment(attachmentId) {
  state.composerAttachments = state.composerAttachments.filter((item) => item.id !== attachmentId);
  renderComposerState();
}

/** 草稿交给某条消息之后清空手上的清单（发送成功与乐观发送都走它）。 */
function clearComposerAttachments() {
  state.composerAttachments = [];
  renderComposerState();
}

/** 附件区与发送键是同一份状态（手上的草稿 + 在飞的上传）的两个出口：一起刷。
    分成两次调用迟早会漏掉一处，于是发送键会对着「还有草稿没落地」的状态说「可以发」。 */
function renderComposerState() {
  renderComposerAttachments();
  syncSendButton();
}

/**
 * 一块附件元素：草稿态（composer 里，可移除）与已发送态（消息里，可下载）共用同一块形状。
 *
 * 已发送态用**原生 `<a download>`**：内容端点给出 `Content-Disposition` 与 MIME，浏览器据此
 * 落到本地文件；「预览」是同一个端点的 `disposition=inline`，本票只做消息里的下载入口
 * （富媒体预览是 Out of Scope）。
 *
 * **只有已绑定**（`message_id` 非空）的附件才是可点的链接：乐观发送中的消息与失败留下的消息
 * 带的仍是**草稿**，而内容端点对草稿回 404 —— 渲染成一个点下去必然 404 的链接是骗人的。
 */
function attachmentNode(attachment, options) {
  const opts = options || {};
  const name = attachment.name || t('attach.fallbackName');
  const size = formatBytes(attachment.size);
  const link = Boolean(opts.link) && Boolean(attachment.message_id);
  const node = el(link ? 'a' : 'span', 'attach' + (link ? ' is-link' : ''));
  node.dataset.attachment = attachment.id;
  if (link) {
    node.setAttribute('href', attachment.url);
    node.setAttribute('download', name);
    node.setAttribute('aria-label', t('msg.attachmentAria', { name, size }));
  }
  node.appendChild(el('span', 'attach-name', name));
  node.appendChild(el('span', 'attach-size', size));
  if (opts.removable) {
    const remove = el('button', 'attach-remove', '×');
    remove.type = 'button';
    remove.dataset.removeAttachment = attachment.id;
    remove.setAttribute('aria-label', t('composer.attachRemove', { name }));
    node.appendChild(remove);
  }
  return node;
}

/** 把待发送的草稿与「正在上传」的占位画进 composer 的附件区（空的时候整块隐藏）。 */
function renderComposerAttachments() {
  const box = $('#composerAttachments');
  if (!box) return;
  const nodes = state.composerAttachments.map((attachment) =>
    attachmentNode(attachment, { removable: true }),
  );
  if (attachingCount > 0) {
    nodes.push(el('span', 'attach is-pending', t('composer.attaching', { count: attachingCount })));
  }
  box.hidden = nodes.length === 0;
  replaceChildren(box, nodes);
}

/** 消息里的附件：一块可点开（下载）的元素。没有附件时什么都不渲染。 */
function appendAttachmentList(body, message) {
  const attachments = message.attachments || [];
  if (!attachments.length) return;
  const list = el('div', 'msg-attachments');
  attachments.forEach((attachment) => list.appendChild(attachmentNode(attachment, { link: true })));
  body.appendChild(list);
}

/* ─── @ 补全：清单只找候选 + 按解析器的引号规则拼字符串（前端不解析提及） ──────
   分工：候选来自后端的频道成员表（`GET /api/channels/{id}/members`），插入的字符串按
   `alibot/mentions.py` 的规则拼；正文到底是不是提及**只由后端**裁决（`mention_snapshot`
   随消息落库），前端既不另写一套解析规则、也不预测解析结果。

   从后端借来的两条判据（只决定「要不要弹」与「怎么拼」，不决定「是不是提及」）：
   1. `@` 的左边界：前面是 ASCII 字母或数字时不算提及（邮箱式）——对应 `_left_boundary_ok`。
      这类位置**不弹清单**，否则插进去的文本必然不被解析（违背票面硬约束）。
   2. 裸形式的候选串遇到分隔符即止（正本 `_TOKEN_STOPS`：空白、各类标点、`@`、`\` 等）——
      名字里含其中任一字符时只能写引号形式（空格是最常见的一种）。引号字符本身也在分隔符里，
      于是一名叫 `a"b` 的成员必须写引号形式；而正本认**三种**引号对（`"…"` / `“…”` / `‘…’`），
      所以选“**闭引号不在名字里**”的那一对（正本用 `text.find(closer)`，闭引号若出现在名字中间，
      名字就会被截断）。只有当名字同时含 `"`、`”`、`’` 三个闭引号时，**没有**任何合法插入形态：
      选中它时给可读提示、不插入无效文本。

   过滤只用于把候选缩小（包含匹配、ASCII 大小写不敏感），插入的始终是**库里的原名**：
   于是「最左最长」这类解析语义不会因为前端放宽过滤而改变。 */
const MENTION_TOKEN_STOPS =
  " \t\r\n@,，。、;；:：!！?？'\"“”‘’()（）[]【】{}「」<>《》/\\|~#$%^&*=";
// 与正本 `mentions.py._QUOTE_PAIRS` 同源：开引号 → 闭引号。顺序 = 优先用哪种引号。
const MENTION_QUOTE_PAIRS = [
  ['"', '"'],
  ['“', '”'],
  ['‘', '’'],
];

// 补全清单的运行时状态。它不是业务正本：候选只来自后端返回的频道成员表。
const mentionPicker = {
  open: false,
  empty: null, // null = 有候选；'no-members' = 频道没有 Agent 成员；'unavailable' = 成员清单读不到
  items: [],
  activeIndex: 0,
  trigger: null, // {at, query, quote}：@ 的位置、已输入的前缀、引号形式时的开引号
  signature: '',
  dismissed: '', // 被 Esc 关掉 / 刚插入完的触发点签名：同一个触发点不再自动弹出
};
// 频道成员清单的读取状态：每个频道各自一份；失败只记事实，不伪造成员。
const channelMembersFailed = new Set();
// 在飞的读取：**同一个频道复用同一个 promise**，于是调用方 `await` 之后拿到的就是
// 「这一次读取已经落地」的状态。早退会给调用方一个「读完了」的假信号——名册打开的那一瞬
// 撞上在飞读取（切频道、composer 获得焦点都会发起）时，它就会用旧缓存渲染，而且再没有
// 第二次重绘的机会；`openTask` / `selectTarget` 的 `await` 也依赖同一条语义。
const channelMembersInFlight = new Map();
// 正在「加入频道」的成员 id（按下到后端答复之间禁用那一个控件，防重复提交）。
const joining = new Set();

function containsAny(text, chars) {
  for (const char of String(text == null ? '' : text)) {
    if (chars.includes(char)) return true;
  }
  return false;
}

function isAsciiAlnum(char) {
  return char !== undefined && ((char >= '0' && char <= '9') || (char >= 'A' && char <= 'Z') || (char >= 'a' && char <= 'z'));
}

/** 该名字要插入的字符串；`null` = 这个名字没有任何能被解析器识别的插入形态。

    名字不含分隔符 ⇒ 裸形式 `@名字`；否则走引号形式，引号对取「闭引号不在名字里」的第一个
    （正本的 `find(closer)` 会在名字中间的闭引号处截断）。*/
function mentionText(name) {
  const text = String(name == null ? '' : name);
  if (!containsAny(text, MENTION_TOKEN_STOPS)) return '@' + text;
  const pair = MENTION_QUOTE_PAIRS.find(([, closer]) => !text.includes(closer));
  return pair ? '@' + pair[0] + text + pair[1] : null;
}

/** 该名字有没有能被解析器识别的插入形态（三个闭引号都在名字里时没有）。 */
function mentionInsertable(name) {
  return mentionText(name) !== null;
}

/** 光标处是否正在写一个 `@提及`；是则给出被替换的起点与已输入的前缀。 */
function mentionTriggerAt(input) {
  const caret = input.selectionStart;
  if (caret === null || caret !== input.selectionEnd) return null; // 有选区时不猜位置
  const before = input.value.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  // 左边界（与 _left_boundary_ok 对齐）：邮箱式文本不弹清单。
  if (at > 0 && isAsciiAlnum(before[at - 1])) return null;
  const after = before.slice(at + 1);
  const opener = after.slice(0, 1);
  const closer = MENTION_QUOTE_PAIRS.reduce((found, [open, close]) => (open === opener ? close : found), null);
  if (closer) {
    const body = after.slice(1);
    // 已经写上了闭合引号 = 这段提及写完了，不再提示。
    if (body.includes(closer)) return null;
    return { at, query: body, quote: opener };
  }
  // 裸形式：候选串遇到分隔符即止（已经离开这个候选串，或它已不可能再被补全）。
  if (containsAny(after, MENTION_TOKEN_STOPS)) return null;
  return { at, query: after, quote: null };
}

function mentionTriggerSignature(input) {
  const trigger = mentionTriggerAt(input);
  return trigger ? trigger.at + ':' + trigger.query : '';
}

function channelAgentMembers(channelId) {
  const members = state.channelMembers[channelId];
  // null 与「读到空列表」是两件事：前者是还没读到（去读一次），后者才是真的没有成员。
  return members ? members.filter((member) => member.kind === 'agent') : null;
}

/** 读一次该频道的成员表并落进缓存；**同一频道的在飞读取复用同一个 promise**
    （`await` 它拿到的就是「这一次读取已落地」的状态，见 `channelMembersInFlight`）。 */
async function refreshChannelMembers(channelId) {
  const id = channelId || state.activeChannelId;
  const channel = state.channels.find((item) => item.id === id);
  if (!channel) return null;
  const inFlight = channelMembersInFlight.get(id);
  if (inFlight) return inFlight;
  const read = (async () => {
    try {
      const payload = await api(channelPath(id, '/members'));
      state.channelMembers[id] = payload.members || [];
      channelMembersFailed.delete(id);
    } catch {
      // 读不到就如实记下：不静默清空已读到的候选，也不伪造成员。
      if (!state.channelMembers[id]) state.channelMembers[id] = [];
      channelMembersFailed.add(id);
    }
    return state.channelMembers[id];
  })();
  channelMembersInFlight.set(id, read);
  try {
    return await read;
  } finally {
    channelMembersInFlight.delete(id);
  }
}

/** 把一个 Agent 加入当前频道，然后**不刷新页面**就地刷新成员表与 `@` 补全候选。

    校验全在后端：失败时只呈现后端的可读原因，不在前端建半成品状态。响应自带该频道最新成员表，
    直接覆盖本地缓存（覆盖而不是「先加再读」，中间没有可读到旧成员表的窗口）。
    刷新后重判一次补全：清单正开着时，新加入的成员**立刻**可选。 */
async function joinChannelMember(memberId) {
  const channel = activeChannel();
  if (!channel || joining.has(memberId)) return;
  joining.add(memberId);
  renderDock();
  try {
    const payload = await api(channelPath(channel.id, '/members'), {
      method: 'POST',
      body: { member_id: memberId },
    });
    state.channelMembers[channel.id] = payload.members || state.channelMembers[channel.id];
    channelMembersFailed.delete(channel.id);
    if (payload.channel) replaceChannel(payload.channel);
    const name = (payload.member && payload.member.display_name) || t('toast.memberFallback');
    toast(
      payload.joined
        ? t('toast.joinedChannel', { name, channel: channel.name })
        : t('toast.alreadyMember', { name, channel: channel.name }),
    );
    updateMentionPicker();
  } catch (err) {
    toast(t('toast.joinFailed', { reason: err.message }), 'err');
  } finally {
    joining.delete(memberId);
    renderDock();
  }
}

function syncMentionHint() {
  const hint = $('#composerHint');
  if (!hint) return;
  if (!mentionPicker.open) {
    hint.textContent = t('composer.hint');
  } else if (mentionPicker.items.length) {
    hint.textContent = t('mention.hintPick');
  } else {
    // 空态没有可选项：Enter 仍是发送（键盘契约不变），只说明怎么关掉它。
    hint.textContent = t('mention.hintDismiss');
  }
}

function renderMentionPicker() {
  const panel = $('#composerMention');
  if (!panel) return;
  if (!mentionPicker.open) {
    panel.hidden = true;
    replaceChildren(panel, []);
    return;
  }
  panel.hidden = false;
  if (mentionPicker.empty) {
    const text = mentionPicker.empty === 'unavailable' ? t('mention.unavailable') : t('mention.empty');
    const empty = el('div', 'member-pick-empty mention-empty', text);
    empty.setAttribute('data-od-id', 'mention-picker-empty');
    replaceChildren(panel, [empty]);
    return;
  }
  const rows = mentionPicker.items.map((member, index) => {
    const active = index === mentionPicker.activeIndex;
    const row = el('button', 'member-opt mention-opt' + (active ? ' is-active' : ''));
    row.type = 'button';
    row.dataset.mention = member.id;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', active ? 'true' : 'false');
    row.setAttribute('data-od-id', 'mention-option-' + member.id);
    row.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    row.appendChild(el('span', 'mention-name', member.display_name));
    if (!mentionInsertable(member.display_name)) {
      // 不静默：它在清单里，但明说这个名字无法被 @ 到。
      row.appendChild(el('span', 'mention-note', t('mention.cannotMention')));
    }
    return row;
  });
  replaceChildren(panel, rows);
  const active = panel.querySelector('.mention-opt.is-active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

function closeMentionPicker() {
  mentionPicker.open = false;
  mentionPicker.empty = null;
  mentionPicker.items = [];
  mentionPicker.activeIndex = 0;
  mentionPicker.trigger = null;
  mentionPicker.signature = '';
  renderMentionPicker();
  syncMentionHint();
}

/** 按光标处重判触发点：弹清单、换候选、或让它消失。 */
function updateMentionPicker() {
  const input = $('#composerInput');
  const channel = activeChannel();
  if (!input || !channel) {
    closeMentionPicker();
    return;
  }
  const trigger = mentionTriggerAt(input);
  const signature = mentionTriggerSignature(input);
  // 触发点变了就撤掉上一次的「已关闭」记录：关掉的只是那一个触发点，不是整个能力。
  if (mentionPicker.dismissed && mentionPicker.dismissed !== signature) mentionPicker.dismissed = '';
  if (!trigger || signature === mentionPicker.dismissed) {
    closeMentionPicker();
    return;
  }
  const agents = channelAgentMembers(channel.id);
  if (agents === null) {
    // 候选来源还没读到：先去读一次，回来再判（读失败则落到明确空态，不再重试）。
    closeMentionPicker();
    refreshChannelMembers().then(updateMentionPicker);
    return;
  }
  const needle = trigger.query.toLowerCase();
  const items = agents.filter((member) => String(member.display_name || '').toLowerCase().includes(needle));
  if (agents.length && !items.length) {
    // 过滤无匹配：清单消失，且不打断正常输入。
    closeMentionPicker();
    return;
  }
  const sameTrigger = mentionPicker.open && mentionPicker.signature === signature;
  mentionPicker.trigger = trigger;
  mentionPicker.signature = signature;
  mentionPicker.items = items;
  if (agents.length) mentionPicker.empty = null;
  else if (channelMembersFailed.has(channel.id)) mentionPicker.empty = 'unavailable';
  else mentionPicker.empty = 'no-members';
  mentionPicker.open = true;
  // 同一个触发点上重判（例如光标动了）不重置高亮，否则方向键一按高亮就弹回第一项。
  mentionPicker.activeIndex = sameTrigger ? Math.min(mentionPicker.activeIndex, Math.max(items.length - 1, 0)) : 0;
  renderMentionPicker();
  syncMentionHint();
}

function moveMentionHighlight(delta) {
  const total = mentionPicker.items.length;
  if (!total) return;
  mentionPicker.activeIndex = Math.max(0, Math.min(total - 1, mentionPicker.activeIndex + delta));
  renderMentionPicker();
}

function dismissMentionPicker() {
  // Esc：只关清单、不改正文，并把这个触发点记为已关闭（不然下一次 input 立刻又弹出来）。
  mentionPicker.dismissed = mentionTriggerSignature($('#composerInput'));
  closeMentionPicker();
}

function insertMention(name) {
  const input = $('#composerInput');
  const trigger = mentionPicker.trigger;
  if (!trigger) return;
  const insert = mentionText(name);
  if (insert === null) return; // 调用方已拦（chooseMention），这里只保证不插入 null
  const caret = input.selectionStart;
  const value = input.value;
  const after = value.slice(caret);
  // 光标处的 `@前缀`（引号形式时含开引号）正好是 [trigger.at, caret)：整段替换掉。
  const tail = /^\s/.test(after) ? insert : insert + ' ';
  input.value = value.slice(0, trigger.at) + tail + after;
  const nextCaret = trigger.at + tail.length;
  input.setSelectionRange(nextCaret, nextCaret);
  autosizeComposer();
  syncSendButton();
  closeMentionPicker();
  mentionPicker.dismissed = mentionTriggerSignature(input);
  updateMentionPicker();
  input.focus();
}

function chooseMention(member) {
  if (!member) return;
  const name = String(member.display_name || '');
  if (!mentionInsertable(name)) {
    // 不静默、也不插入无效文本：这个名字没有能被解析器识别的插入形态。
    toast(t('mention.quoteUnsupported', { name }), 'err');
    return;
  }
  insertMention(name);
}

/** 工具行「@提及」：与原型 `alibot-app.html:1798` 同构——向输入框**追加** `@`、聚焦、激活发送键。
    随后弹出的候选仍是**既有**的 `@` 补全（`updateMentionPicker()` → `renderMentionPicker()`），
    这里不新写第二套候选逻辑，也不改候选集（仍只列当前 target 的 Agent 成员）。 */
function mentionFromToolbar() {
  const input = $('#composerInput');
  if (!input) return;
  input.value += '@';
  const caret = input.value.length;
  input.focus();
  input.setSelectionRange(caret, caret);
  autosizeComposer();
  syncSendButton();
  updateMentionPicker();
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

/* ─── 新建私信 ──────────────────────────────────────────────────────── */
function openDmModal() {
  state.dmSelectedAgentId = null;
  $('#dmFormError').hidden = true;
  $('#dmCreateSubmit').disabled = true;
  renderDmAgentPicker();
  $('#dmOverlay').hidden = false;
}

function closeDmModal() {
  $('#dmOverlay').hidden = true;
}

function renderDmAgentPicker() {
  const agents = state.members.filter((member) => member.kind === 'agent');
  const picker = $('#dmAgentPicker');
  if (!agents.length) {
    const empty = el('div', 'member-pick-empty', t('dm.noAgents'));
    empty.setAttribute('data-od-id', 'dm-agent-picker-empty');
    replaceChildren(picker, [empty]);
    return;
  }
  const options = agents.map((member) => {
    const selected = member.id === state.dmSelectedAgentId;
    const option = el('button', 'member-opt' + (selected ? ' is-on' : ''));
    option.type = 'button';
    option.dataset.dmAgent = member.id;
    option.setAttribute('aria-pressed', selected ? 'true' : 'false');
    option.appendChild(avatarNode(member.display_name, member.avatar_slot, 'sm'));
    option.appendChild(document.createTextNode(member.display_name));
    return option;
  });
  replaceChildren(picker, options);
}

async function submitDm() {
  if (!state.dmSelectedAgentId) return;
  const submit = $('#dmCreateSubmit');
  submit.disabled = true;
  $('#dmFormError').hidden = true;
  try {
    const payload = await api('/api/dms', {
      method: 'POST',
      body: { agent_id: state.dmSelectedAgentId },
    });
    replaceDm(payload.dm);
    setActiveChannelId(payload.dm.id);
    closeDmModal();
    await loadWorkspace({ silent: true, keepActive: true });
    scrollToBottom();
    $('#composerInput').focus();
    toast(payload.created
      ? t('toast.dmCreated', { name: payload.dm.peer.display_name })
      : t('toast.dmOpened', { name: payload.dm.peer.display_name }));
  } catch (err) {
    const box = $('#dmFormError');
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    submit.disabled = !state.dmSelectedAgentId;
  }
}

function renderMemberPicker() {
  const agents = state.members.filter((member) => member.kind === 'agent');
  const picker = $('#memberPicker');
  if (!agents.length) {
    // 无 Agent 时成员清单是空态，但控件（勾选容器）按规格存在。
    const empty = el('div', 'member-pick-empty', t('channel.pickEmpty'));
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
    toast(t('toast.channelCreated', { name: payload.channel.name }));
  } catch (err) {
    const box = $('#channelFormError');
    box.textContent = err.message;
    box.hidden = false;
  } finally {
    submit.disabled = false;
  }
}

/* ─── 新建 Agent 与远端数据流告知 ───────────────────────────────────── */
// 「新建 Agent」表单里，选择器当前的提供商是否出自人的手（而不是 hydration 之前的占位默认值）。
let agentProviderTouched = false;

function markProviderTouched() {
  agentProviderTouched = true;
}

function openAgentModal() {
  $('#agentFormError').hidden = true;
  $('#agentNameInput').value = '';
  $('#agentRoleInput').value = '';
  $('#agentModelInput').value = '';
  $('#agentPromptInput').value = '';
  agentProviderTouched = false;
  renderProviderSelect();
  renderRemoteNotice();
  $('#agentOverlay').hidden = false;
  $('#agentNameInput').focus();
}

function closeAgentModal() {
  $('#agentOverlay').hidden = true;
}

/* 两个弹窗（新建 / 编辑）各自的远端告知块节点。**判定与文案只有一份实现**
   （`renderRemoteNoticeBlock()`），这里只是「哪几个节点属于哪个弹窗」的接线表。 */
const AGENT_CREATE_NOTICE_IDS = {
  box: '#agentRemoteNotice',
  title: '#agentRemoteTitle',
  body: '#agentRemoteBody',
  hint: '#agentRemoteHint',
};
const AGENT_EDIT_NOTICE_IDS = {
  box: '#agentEditRemoteNotice',
  title: '#agentEditRemoteTitle',
  body: '#agentEditRemoteBody',
  hint: '#agentEditRemoteHint',
};

// 重建选项会清掉当前选择。`keep` 为真时保留它：只有确实出自人的手、且仍然在这份清单里的那一项
// 才保留；其余一律回到清单首项（provider 清单的正本只有后端 `/api/workspace` 一处）。
function fillProviderSelect(select, keep) {
  const previous = keep ? select.value : '';
  const providers = state.providers.length ? state.providers : PROVIDER_PLACEHOLDER;
  replaceChildren(select, providers.map((provider) => {
    const option = el('option', null, providerLabel(provider));
    option.value = provider;
    return option;
  }));
  select.value = providers.includes(previous) ? previous : providers[0];
  return select.value;
}

function renderProviderSelect(preserveUserPick) {
  fillProviderSelect($('#agentProviderSelect'), Boolean(preserveUserPick));
  updateProviderHint();
}

// 工作台快照可能晚于「新建 Agent」弹窗到达（刷新后马上点 Agent）：清单到齐时刷新已打开的弹窗，
// 于是人不必先关再开一次。provider 清单的正本只有后端 `/api/workspace` 一处。
// 只在人真的动过选择器时保留选择：hydration 之前那一项只是占位默认值，不是用户选择，
// 换完整清单后应回到清单首项（与常规加载先打开弹窗看到的一致）。
function syncOpenAgentModal() {
  if ($('#agentOverlay').hidden) return;
  renderProviderSelect(agentProviderTouched);
  renderRemoteNotice();
}

function currentProvider() {
  return $('#agentProviderSelect').value;
}

function isLocalProvider(provider) {
  return state.localProviders.indexOf(provider) >= 0;
}

function providerHintText(provider) {
  return isLocalProvider(provider) ? t('agent.providerLocalHint') : t('agent.providerRemoteHint');
}

function updateProviderHint() {
  $('#agentProviderHint').textContent = providerHintText(currentProvider());
}

function acknowledged(provider) {
  return state.consents.some((record) => record.provider === provider);
}

/** 远端数据流告知块的**唯一**渲染实现（新建与编辑两个弹窗共用）。

    返回真 = 这一块**可见**（当前 provider 是远端且还没确认过）。判定读的是库内 `remote_consent`
    的镜像（`state.consents`），因此「未确认」在界面上与后端的可用性判定说的是同一件事。 */
function renderRemoteNoticeBlock(ids, provider) {
  const box = $(ids.box);
  if (isLocalProvider(provider) || acknowledged(provider)) {
    box.hidden = true;
    return false;
  }
  box.hidden = false;
  const faces = (state.dataFaces.length ? state.dataFaces : remoteFlow.data_faces).join(t('common.nameListSep'));
  $(ids.title).textContent = t('agent.remoteTitle', { provider: providerLabel(provider) });
  $(ids.body).textContent = t('agent.remoteBody', { faces });
  $(ids.hint).textContent = t('agent.remoteHint');
  return true;
}

function renderRemoteNotice() {
  renderRemoteNoticeBlock(AGENT_CREATE_NOTICE_IDS, currentProvider());
}

/** 确认一次告知（幂等）。`provider` / `button` / `refreshNotice` 由调用点给：新建与编辑
    两个弹窗共用这一条路径与同一份本地镜像更新，不各写一套。 */
async function confirmRemoteFlow(provider, button, refreshNotice) {
  button.disabled = true;
  try {
    const payload = await api('/api/remote-data-flow/ack', { method: 'POST', body: { provider } });
    state.consents = payload.consents || [];
    state.agents = payload.agents || state.agents;
    refreshNotice();
    // 确认会改变成员的启用态：状态点必须从后端重取，不靠本地推断。
    await loadWorkspace({ silent: true, keepActive: true });
    toast(t('toast.remoteAck', { provider: providerLabel(provider) }));
  } catch (err) {
    toast(t('toast.remoteAckFailed', { reason: err.message }), 'err');
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
    toast(t('toast.agentCreated', { name: result.agent.display_name }));
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
    // 告知文案要用这份响应里的数据面来拼。响应晚到、而弹窗已经开着时，正文会一时停在空数据面
    // （这是异步加载本就存在的窗口）；响应一到就重绘，避免界面一直停在空正文。
    // 只重绘告知，不重建选择器、不动人的选择与同意语义。
    if (!$('#agentOverlay').hidden) renderRemoteNotice();
  } catch {
    // 告知文案读不到不影响主流程；下一次打开面板会重试。
  }
}

/* ─── 事件绑定 ──────────────────────────────────────────────────────── */
async function selectTarget(targetId) {
  const target = state.channels.find((item) => item.id === targetId)
    || state.dms.find((item) => item.id === targetId);
  if (!target) return;
  const previous = state.activeChannelId;
  if (previous && previous !== targetId) {
    // 离开旧 target 前把它推到当前标读；其它 target 的已读位置不受影响。
    const stale = state.channels.find((item) => item.id === previous)
      || state.dms.find((item) => item.id === previous);
    if (stale && stale.last_seq > stale.last_read_seq) scheduleRead(previous, stale.last_seq);
  }
  closeTaskStream();
  state.mode = 'channel';
  state.activeTaskId = null;
  state.activeTask = null;
  setActiveChannelId(targetId);
  state.pending = state.pending.filter((item) => item.target_id === targetId || item.status === 'failed');
  // 候选清单属于刚被切换掉的频道：进新 target 时先关掉，再按新频道的成员表重建。
  closeMentionPicker();
  state.outcomes = {};
  // 折叠态的键是「触发它的那条消息的 seq」，跨频道不通用：换频道时一并清掉。
  // 旁白行的折叠态键是旁白自己的 seq，同理只在当前 target 内有效。
  state.expandedOutcomes = {};
  state.replyOutcomes = {};
  state.expandedNarrations = {};
  render();
  try {
    await refreshMessages();
    await refreshOutcomes();
    if (target.kind !== 'dm') await refreshChannelMembers();
    state.online = true;
  } catch (err) {
    state.online = false;
    toast(t('toast.sessionLoadFailed', { reason: err.message }), 'err');
  }
  render();
  markCurrentRead();
  scrollToBottom();
}

function bindSearchEvents() {
  const input = $('#searchInput');
  // 实时搜索（原型 `$('#searchInput').addEventListener('input', renderSearch)`）：
  // 落笔延时一拍只发一次请求，Enter 立即发。
  input.addEventListener('input', scheduleSearch);
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    cancelPendingSearch();
    return runSearch();
  });
  $('#searchFacets').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-facet]');
    if (chip) setSearchFacet(chip.dataset.facet);
  });
}

function bindCmdEvents() {
  const input = $('#cmdInput');
  input.addEventListener('input', (event) => renderCmd(event.target.value));
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    // Enter 跑**第一条**（高亮的那一条，原型 `:1839` 同一行为）。
    const first = $('#cmdList').querySelector('[data-cmd]');
    if (first) runCmd(first.dataset.cmd);
  });
  $('#cmdList').addEventListener('click', (event) => {
    const item = event.target.closest('[data-cmd]');
    if (item) runCmd(item.dataset.cmd);
  });
  $('#cmdOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'cmdOverlay') closeCmd();
  });
}

function bindEvents() {
  // ── 工作区设置：rail **头部**入口（原型 `:515-518` 的第二枚 icon-btn）+ 弹窗（语言 / 消息密度 / 主题三档）──
  $('#btnSettings').addEventListener('click', openSettingsModal);
  $('#settingsModalClose').addEventListener('click', closeSettingsModal);
  $('#settingsDone').addEventListener('click', closeSettingsModal);
  $('#settingsOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'settingsOverlay') closeSettingsModal();
  });
  // 语言：`change` 一次写一档（后端是正本），渲染由 `applySettings` 统一铺。
  $('#settingsLocaleSelect').addEventListener('change', (event) => {
    saveSettings({ locale: event.target.value });
  });
  // 消息密度：两个 `.radio-card`（原型形态），点哪个写哪个。
  $('#settingsDensity').addEventListener('click', (event) => {
    const card = event.target.closest('[data-density]');
    if (!card) return;
    saveSettings({ message_density: card.dataset.density });
  });
  // 主题：三个 `.radio-card`（浅色 / 深色 / 跟随系统），点哪个写哪个。
  $('#settingsTheme').addEventListener('click', (event) => {
    const card = event.target.closest('[data-theme]');
    if (!card) return;
    saveSettings({ theme: card.dataset.theme });
  });
  // ── 提醒：rail 入口 + 弹窗（列表 / 空态 / 新增 / 删除）──
  $('#btnReminders').addEventListener('click', openRemindersModal);
  $('#remindersModalClose').addEventListener('click', closeRemindersModal);
  $('#remindersCancel').addEventListener('click', closeRemindersModal);
  $('#remindersOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'remindersOverlay') closeRemindersModal();
  });
  $('#reminderAddSubmit').addEventListener('click', submitReminder);
  // 与其它「一格正文 + 一个提交键」的弹窗同形：Enter 直接提交。
  $('#reminderText').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submitReminder(); }
  });
  // 删除：一个控件一条提醒，点击委派到它所在的那一行。
  $('#reminderList').addEventListener('click', (event) => {
    const remove = event.target.closest('[data-delete-reminder]');
    if (remove) deleteReminder(remove.dataset.deleteReminder);
  });
  // 搜索现在是**主线视图**：rail 头下方的 `.search-shell` 入口与 `⌘K` 面板里的「搜索」都进同一个视图。
  $('#btnSearch').addEventListener('click', showSearchView);
  bindSearchEvents();
  bindCmdEvents();
  // ── 成员名册：频道头部成员入口 + 弹窗（**唯一动作是关闭**）──
  $('#btnChannelMembers').addEventListener('click', openRosterModal);
  $('#rosterClose').addEventListener('click', closeRosterModal);
  $('#rosterDone').addEventListener('click', closeRosterModal);
  $('#rosterOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'rosterOverlay') closeRosterModal();
  });
  // ── 频道回应预算（#54）：频道头入口 + 频道设置弹窗（只读读数 + 3 / 5 / 10 上界档位）──
  // 正文（读数 / 待生效 / 写控件）是**整段重铺**的，因此点击委派到 `#budgetBody` 上（一个监听器），
  // 与成员列表 / 提醒列表同一做法：重铺不会把监听器一起换掉。
  $('#btnChannelBudget').addEventListener('click', openBudgetModal);
  $('#budgetClose').addEventListener('click', closeBudgetModal);
  $('#budgetDone').addEventListener('click', closeBudgetModal);
  $('#budgetOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'budgetOverlay') closeBudgetModal();
  });
  $('#budgetBody').addEventListener('click', (event) => {
    const tier = event.target.closest('[data-budget-tier]');
    if (tier) {
      selectBudgetTier(tier.dataset.budgetTier);
      return;
    }
    if (event.target.closest('[data-budget-save]')) submitBudgetTier();
  });
  $('#btnNewChannel').addEventListener('click', openChannelModal);
  // 新建私信入口在「私信」分组标题的 `+` 上（#103 起从 rail-actions 挪来），点击行为不变。
  $('#btnNewDm').addEventListener('click', openDmModal);
  $('#dmCancel').addEventListener('click', closeDmModal);
  $('#dmModalClose').addEventListener('click', closeDmModal);
  $('#dmCreateSubmit').addEventListener('click', submitDm);
  $('#dmOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'dmOverlay') closeDmModal();
  });
  $('#dmAgentPicker').addEventListener('click', (event) => {
    const option = event.target.closest('[data-dm-agent]');
    if (!option) return;
    state.dmSelectedAgentId = option.dataset.dmAgent;
    renderDmAgentPicker();
    $('#dmCreateSubmit').disabled = !state.dmSelectedAgentId;
  });
  $('#btnNewAgent').addEventListener('click', openAgentModal);
  $('#agentCancel').addEventListener('click', closeAgentModal);
  $('#agentModalClose').addEventListener('click', closeAgentModal);
  $('#agentCreateSubmit').addEventListener('click', submitAgent);
  $('#agentRemoteConfirm').addEventListener('click', () => {
    confirmRemoteFlow(currentProvider(), $('#agentRemoteConfirm'), renderRemoteNotice);
  });
  // 只监听 change 不够：重新点选「已经选中的那一项」不派发 change，而 hydration 期间那一项还只是
  // 占位默认值，清单到齐后的重建正好会把它换成完整清单首项 —— 静默把本机模型换成远端模型。
  // 因此指针按下与键盘操作也记为「选择出自人的手」。
  $('#agentProviderSelect').addEventListener('pointerdown', markProviderTouched);
  $('#agentProviderSelect').addEventListener('keydown', markProviderTouched);
  $('#agentProviderSelect').addEventListener('change', () => {
    markProviderTouched();
    updateProviderHint();
    renderRemoteNotice();
  });
  $('#agentOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'agentOverlay') closeAgentModal();
  });

  // ── 编辑 Agent 配置（PATCH /api/agents/{id}）：与新建同形的选择器 / 告知语义 ──
  $('#agentEditCancel').addEventListener('click', closeAgentEditModal);
  $('#agentEditClose').addEventListener('click', closeAgentEditModal);
  $('#agentEditSubmit').addEventListener('click', submitAgentEdit);
  $('#agentEditRemoteConfirm').addEventListener('click', () => { confirmAgentEditRemoteFlow(); });
  $('#agentEditProviderSelect').addEventListener('pointerdown', () => { agentEditProviderTouched = true; });
  $('#agentEditProviderSelect').addEventListener('keydown', () => { agentEditProviderTouched = true; });
  $('#agentEditProviderSelect').addEventListener('change', () => {
    agentEditProviderTouched = true;
    $('#agentEditProviderHint').textContent = providerHintText($('#agentEditProviderSelect').value);
    renderAgentEditNotice();
  });
  $('#agentEditEnabled').addEventListener('click', (event) => {
    const card = event.target.closest('[data-enabled]');
    if (card) renderEditTier('#agentEditEnabled', 'enabled', card.dataset.enabled === 'on');
  });
  $('#agentEditAutoClaim').addEventListener('click', (event) => {
    const card = event.target.closest('[data-auto-claim]');
    if (card) renderEditTier('#agentEditAutoClaim', 'autoClaim', card.dataset.autoClaim === 'on');
  });
  $('#agentEditOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'agentEditOverlay') closeAgentEditModal();
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
    if (!row) return;
    selectTarget(row.dataset.channel);
    // 挑完目标就把左抽屉收起来：抽屉盖着刚选中的那块内容，留着它等于把结果挡住
    // （桌面上这个动作是空转 —— 抽屉在那里根本不存在，`closeRail()` 是幂等的）。
    closeRail();
  });
  $('#dmList').addEventListener('click', (event) => {
    const row = event.target.closest('[data-dm]');
    if (!row) return;
    selectTarget(row.dataset.dm);
    closeRail();
  });
  // 左栏 Agent 行：打开 dock 的**第三形态**（Agent 详情）。行是 `<button>`，因此键盘也走同一条路径。
  $('#memberList').addEventListener('click', (event) => {
    const row = event.target.closest('[data-member]');
    if (row) openAgentDetail(row.dataset.member);
  });

  // Agent 详情：两个页签、关闭、以及「暂停 / 恢复」与「编辑配置」两个真动作。
  // 「暂停」的控件由 `renderAgentDetail()` 每次重建，因此这里用**委派**而不是逐个绑事件。
  $('#adTabOverview').addEventListener('click', () => setAgentDetailTab('overview'));
  $('#adTabRuntime').addEventListener('click', () => setAgentDetailTab('runtime'));
  $('#btnAgentDetailClose').addEventListener('click', closeAgentDetail);
  $('#adOverview').addEventListener('click', (event) => {
    const toggle = event.target.closest('[data-agent-toggle]');
    if (toggle) {
      toggleAgentEnabled(toggle.dataset.agentToggle);
      return;
    }
    const edit = event.target.closest('[data-agent-edit]');
    if (edit) openAgentEditModal(edit.dataset.agentEdit);
  });

  // 页签：两个页签在同一内容区切换（任务的唯一入口）。
  // 任务页签带 `fromChannelTab`：进入看板时默认筛到**当前频道**（左栏/全工作区视角仍看全部）。
  $('#tabMessages').addEventListener('click', showMessagesView);
  $('#tabTasks').addEventListener('click', showTaskBoard);

  // `.seg`：看板 / 列表（同一批任务的两种呈现，不重取）。
  document.querySelectorAll('.seg [data-taskview]').forEach((button) => {
    button.addEventListener('click', () => {
      state.taskView = button.dataset.taskview;
      syncTaskViewSeg();
      renderTaskBoard();
    });
  });
  // 看板筛选 chips（一次委派服务看板与列表两个视图）。
  $('#boardFilters').addEventListener('click', (event) => {
    const mine = event.target.closest('[data-board-mine]');
    if (mine) {
      state.taskFilterMine = !state.taskFilterMine;
      renderTaskBoard();
      return;
    }
    const channel = event.target.closest('[data-board-channel]');
    if (!channel) return;
    state.taskFilterChannelId = channel.dataset.boardChannel;
    refreshTasks();
  });
  // 卡片与列表行都打开同一个 dock 线程（点击是唯一入口；不实现拖拽）。
  ['#taskColumns', '#taskListWrap'].forEach((selector) => {
    $(selector).addEventListener('click', (event) => {
      const card = event.target.closest('[data-task]');
      if (card) openTask(card.dataset.task);
    });
  });

  // 新建任务：弹窗形态（标题 + 所属频道）。
  $('#btnNewTask').addEventListener('click', () => openTaskModal());
  $('#taskCreateCancel').addEventListener('click', closeTaskModal);
  $('#taskModalClose').addEventListener('click', closeTaskModal);
  $('#taskCreateSubmit').addEventListener('click', submitTask);
  $('#taskOverlay').addEventListener('click', (event) => {
    if (event.target.id === 'taskOverlay') closeTaskModal();
  });
  $('#taskCreateTitle').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submitTask(); }
  });

  // dock 任务线程：标题就地编辑（Enter 保存 / Esc 取消 / 失焦保存）、迁移与指派 chips、回复。
  const titleInput = $('#ttTitleInput');
  titleInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); titleInput.blur(); }
    if (event.key === 'Escape') {
      event.preventDefault();
      if (state.activeTask) titleInput.value = state.activeTask.title;
      titleInput.blur();
    }
  });
  titleInput.addEventListener('blur', () => { saveTaskTitle(); });
  $('#ttTransitions').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-tt-move]');
    if (chip) changeTaskStatus(chip.dataset.ttMove);
  });
  $('#ttAssign').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-tt-assign]');
    if (chip) changeTaskAssignee(chip.dataset.ttAssign);
  });
  $('#ttReplies').addEventListener('click', (event) => {
    const link = event.target.closest('[data-open-task]');
    if (link) openTask(link.dataset.openTask);
  });
  $('#btnThreadClose').addEventListener('click', () => {
    closeTaskDock();
    renderDock();
    renderTaskBoard();
    renderTabs();
  });

  $('#taskSend').addEventListener('click', sendTaskMessage);
  $('#taskComposerInput').addEventListener('input', () => {
    const input = $('#taskComposerInput');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    $('#taskSend').disabled = !input.value.trim() || !state.online;
  });
  $('#taskComposerInput').addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendTaskMessage();
    }
  });

  $('#composerInput').addEventListener('input', () => {
    autosizeComposer();
    syncSendButton();
    updateMentionPicker();
  });
  // 光标移动不产生 input 事件：点一下、按左右/Home/End 都要重判触发点。
  $('#composerInput').addEventListener('click', () => updateMentionPicker());
  $('#composerInput').addEventListener('keyup', (event) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'Home' || event.key === 'End') {
      updateMentionPicker();
    }
  });
  $('#composerInput').addEventListener('blur', () => closeMentionPicker());
  // 候选清单是 composer 自己的关注点，因此它的刷新也挂在 composer 上：每次获得焦点重新读一遍
  // 该频道的成员表（穿透加入这类成员变化就会在下一次落笔前被看到），读完再按当前光标重判。
  $('#composerInput').addEventListener('focus', () => {
    refreshChannelMembers().then(updateMentionPicker);
  });
  $('#composerInput').addEventListener('keydown', (event) => {
    // 输入法组合中的按键一律不接管（含 Enter 不算发送）。
    if (event.isComposing) return;
    if (mentionPicker.open && mentionPicker.items.length) {
      // 清单打开且有候选：↑/↓ 移动高亮、Enter/Tab 选中。
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        moveMentionHighlight(event.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
        event.preventDefault();
        chooseMention(mentionPicker.items[mentionPicker.activeIndex]);
        return;
      }
    }
    if (event.key === 'Escape' && mentionPicker.open) {
      event.preventDefault();
      dismissMentionPicker();
      return;
    }
    // Enter 发送、Shift+Enter 换行（清单关闭时与补全之前完全一致）。
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  // 鼠标选择：mousedown 时拦一下，textarea 不失焦（插入要用它当前的选区）。
  $('#composerMention').addEventListener('mousedown', (event) => {
    const row = event.target.closest('[data-mention]');
    if (!row) return;
    event.preventDefault();
    const member = mentionPicker.items.find((item) => item.id === row.dataset.mention);
    chooseMention(member);
  });
  $('#btnSend').addEventListener('click', sendMessage);
  // composer 工具行：附件（两步上传）→ @提及（复用既有补全）→ 作为任务发送（D6）。
  $('#btnAttach').addEventListener('click', () => {
    const input = $('#attachInput');
    if (input && typeof input.click === 'function') input.click();
  });
  $('#attachInput').addEventListener('change', (event) => {
    const input = event.target;
    attachFiles(input && input.files);
    // 选完之后把控件清空：同一个文件再选一次也应当触发一次新的 `change`。
    if (input) input.value = '';
  });
  $('#composerAttachments').addEventListener('click', (event) => {
    const remove = event.target.closest('[data-remove-attachment]');
    if (remove) removeComposerAttachment(remove.dataset.removeAttachment);
  });
  $('#btnMention').addEventListener('click', mentionFromToolbar);
  $('#btnAsTask').addEventListener('click', openTaskFromComposer);

  // 成员面板里的「加入」：一个控件一个成员，点击后把它加入当前频道。
  $('#dockMembers').addEventListener('click', (event) => {
    const button = event.target.closest('[data-join]');
    if (button) {
      joinChannelMember(button.dataset.join);
      return;
    }
    // 成员面板里的 Agent 也是打开详情的入口（名字是控件；点卡片其余部分同样打开，
    // 但「加入」按钮先判——它自己的动作不能被详情吞掉）。
    const open = event.target.closest('[data-agent-open]') || event.target.closest('[data-member]');
    if (open) openAgentDetail(open.dataset.agentOpen || open.dataset.member);
  });
  // ≤900px 抽屉自己的出口（`>900px` 时那个控件 `display:none`，所以这里在桌面是空转）。
  $('#dock').addEventListener('click', (event) => {
    if (event.target.closest('[data-dock-close]')) setDockOpen(false);
  });

  $('#messageList').addEventListener('click', (event) => {
    const taskRef = event.target.closest('[data-open-task]');
    if (taskRef) {
      openTask(taskRef.dataset.openTask);
      return;
    }
    // 旁白行的折叠控件：与摘要行同一个纪律（只切**呈现状态**、重建后把焦点还回去），
    // 但键是旁白那条消息**自己的** seq（摘要行的键是触发消息的 base_seq，两者会撞号）。
    const narration = event.target.closest('[data-narration-toggle]');
    if (narration) {
      const seq = narration.dataset.narrationToggle;
      if (state.expandedNarrations[seq]) delete state.expandedNarrations[seq];
      else state.expandedNarrations[seq] = true;
      renderMessages();
      const next = $('#messageList').querySelector('[data-narration-toggle="' + seq + '"]');
      if (next) next.focus();
      return;
    }
    // 摘要行的折叠控件：切换的只是**呈现状态**（内存），后端事实一个字不动。
    const summary = event.target.closest('[data-summary-toggle]');
    if (summary) {
      const baseSeq = summary.dataset.summaryToggle;
      const wasExpanded = Boolean(state.expandedOutcomes[baseSeq]);
      if (wasExpanded) delete state.expandedOutcomes[baseSeq];
      else state.expandedOutcomes[baseSeq] = true;
      // 重建整条消息流（消息节点无状态，重建是既有做法）。重建会把刚被点击的按钮换成新节点，
      // 键盘焦点会落到 body；因此重建后把焦点还给**同一个控件**的新节点，否则「Tab 到它、
      // Enter 展开」之后就再也无法用键盘收起（焦点已丢）。
      renderMessages();
      const next = $('#messageList').querySelector('[data-summary-toggle="' + baseSeq + '"]');
      if (next) next.focus();
      return;
    }
    const inspect = event.target.closest('[data-dock-toggle]');
    if (inspect) {
      // 只打开面板，不滚动页面：失败现场就在眼前，人不需要先去找那个图标。
      setDockOpen(true);
      renderDock();
      return;
    }
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

  $('#btnDockToggle').addEventListener('click', toggleDock);
  // 移动端壳（≤900px）：顶部栏按钮开合左抽屉，点遮罩收起。
  // 同一个按钮承担开与合（`aria-expanded` 是它的状态），键盘用户因此有来有回。
  $('#railToggle').addEventListener('click', () => {
    if (railOpen()) closeRail();
    else openRail();
  });
  $('#railScrim').addEventListener('click', closeRail);
  $('#btnReconnect').addEventListener('click', () => loadWorkspace());

  // ── 全局键盘：⌘K / Ctrl+K 打开命令面板、ESC 关闭最上面那一层、M/T 两个单键跳转 ──
  // 顺序与原型 `:1847-1852` 一致：命令面板 → 弹窗 → 搜索视图；`M` / `T` 只在人不在输入框里时生效。
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && String(event.key).toLowerCase() === 'k') {
      event.preventDefault();
      openCmd();
      return;
    }
    if (event.key === 'Escape') {
      if (!$('#cmdOverlay').hidden) {
        closeCmd();
        return;
      }
      if (!$('#settingsOverlay').hidden) closeSettingsModal();
      if (!$('#remindersOverlay').hidden) closeRemindersModal();
      if (!$('#rosterOverlay').hidden) closeRosterModal();
      if (!$('#budgetOverlay').hidden) closeBudgetModal();
      if (!$('#agentEditOverlay').hidden) closeAgentEditModal();
      if (!$('#channelOverlay').hidden) closeChannelModal();
      if (!$('#dmOverlay').hidden) closeDmModal();
      if (!$('#agentOverlay').hidden) closeAgentModal();
      if (!$('#taskOverlay').hidden) closeTaskModal();
      // 移动端壳的左抽屉盖在内容上，因此 ESC 先收它（它比搜索视图更靠前），收到就到此为止。
      if (railOpen()) {
        closeRail();
        return;
      }
      // ≤900px 的右抽屉同样盖在内容上（它没有遮罩，因此 ESC 是它的键盘出口）。
      // 桌面 dock 是常驻列，ESC 不管它 —— 那会改掉既有行为。
      if (mobileShell() && !$('#dock').hidden) {
        setDockOpen(false);
        return;
      }
      if (state.mode === 'search') showMessagesView();
      return;
    }
    const target = event.target;
    if (target && typeof target.matches === 'function' && target.matches('input,textarea')) return;
    const key = String(event.key).toLowerCase();
    if (key === 'm') showMessagesView();
    if (key === 't') showTaskBoard();
  });

  window.addEventListener('online', () => loadWorkspace({ silent: true }));
  window.addEventListener('offline', () => { state.online = false; render(); });
}

/* ─── 启动 ──────────────────────────────────────────────────────────── */
// 静态文案先按**当前语言**铺一遍（此刻是源语言；`loadWorkspace` 读到后端偏好后会再铺一次），
// 于是「JS 还没跑完」的那一帧也不会有半份译文。
applyStaticI18n(document);
bindEvents();
loadWorkspace();
