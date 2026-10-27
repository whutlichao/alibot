/* 频道消息流里的审批块（票 #321）

   这块是**补回旧页面既有的一面**，不是新行为：旧页面 `studio/index.html:256-261` 的
   `#approvalBoard` 就住在消息流里——空态之后、`#messageList` 之前，`role="region"
   aria-label="审批"`；内容与倒计时由 `studio/app.js` 的 `renderChannelApprovals()`（3344）
   与 `approvalCardNode()`（3286）铺开。判据正本逐条搬自那里：

   1. **读数与排序**（`approvalRecordsFor()`，3150）：pending 在前、其余在后；组内按
      `requested_at` **倒序**。v2 的实现就是 `stores/approvals.js` 的 `recordsFor()`——
      本票不另算一遍。
   2. **空态互斥**（`renderMessages()`，8542-8547）：流里只有一张审批卡时，不该同时留着
      「该频道还没有消息」的空态。
   3. **隐藏**：没有记录整块不渲染（旧 `board.hidden = !list.length`，v2 用 `v-if`，
      不摆空容器）。
   4. **作用域**：私信下不出现（旧判据是审批归属 target = `state.activeChannelId`，
      私信下为空）；卡片与任务线程的审批读**同一份**镜像（`stores/approvals.js`），
      按 `target_id` 分开，互不串台。

   断言落在渲染出来的元素 / 属性 / 发出的请求上（类名与 `data-od-id` 逐字沿用旧页面），
   时钟一律注入（`createApprovalStore({ now })`），不用 sleep。至少一条用例从 `App.vue`
   真实父组件进——位置与空态互斥之外的接线（App 传哪一份读数、私信下传什么）只能在
   真实父组件下断言。 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createFetchStub, flush, jsonResponse, loadStudioGlobals } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;
const MessageStream = (await import('../src/components/MessageStream.vue')).default;
const { createApprovalStore } = await import('../src/stores/approvals.js');

/** 夹具的零点：倒计时相对它算，读数与真实时钟无关。 */
const T0 = Date.parse('2026-03-01T00:00:00Z');

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

function channel(overrides) {
  return Object.assign(
    { id: 'ch-1', name: 'design', description: '设计频道', last_seq: 0, last_read_seq: 0, unread_count: 0 },
    overrides || {},
  );
}

function dm() {
  return { id: 'dm-1', kind: 'dm', peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1 }, unread_count: 0 };
}

function message(seq, overrides) {
  return Object.assign(
    {
      id: 'msg-' + seq,
      target_id: 'ch-1',
      seq,
      kind: 'human',
      body: 'body ' + seq,
      created_at: '2026-02-28T10:00:00.000Z',
      mention_snapshot: [],
      author: OWNER,
    },
    overrides || {},
  );
}

function emptyState(overrides) {
  return Object.assign(
    {
      member: OWNER,
      members: [OWNER, ADA],
      agents: [ADA],
      channels: [channel()],
      dms: [],
      statuses: {},
      activeTargetId: 'ch-1',
      outcomes: {},
      online: true,
      streamState: 'open',
      channelMembersFailed: [],
    },
    overrides || {},
  );
}

/** 一条审批记录的载荷（`GET /api/approvals` 的形状）。 */
function record(overrides) {
  return Object.assign(
    {
      reply_id: 'rpl-1',
      target_id: 'ch-1',
      member_id: 'mem-1',
      tool_calls: [{ id: 'tc-1', name: 'write_file', input: { path: 'notes.txt' } }],
      state: 'pending',
      decisions: [],
      requested_at: new Date(T0).toISOString(),
      resolved_at: null,
    },
    overrides || {},
  );
}

/** 把一条记录放进**真实的** store（与 live `approval_required` 帧同一条写入口）。 */
function seed(store, item) {
  store.applyFrame({
    type: 'approval_required',
    target_id: item.target_id,
    reply_id: item.reply_id,
    member_id: item.member_id,
    tool_calls: item.tool_calls,
  });
  store.patchRecord(item.target_id, item.reply_id, {
    state: item.state,
    decisions: item.decisions || [],
    requested_at: item.requested_at,
    resolved_at: item.resolved_at || null,
  });
}

function mountStream(options) {
  const config = options || {};
  return mount(MessageStream, {
    props: {
      state: config.state || emptyState(),
      messages: config.messages || [],
      typingNames: [],
      approvals: config.approvals || null,
      approvalTargetId: config.approvalTargetId === undefined ? 'ch-1' : config.approvalTargetId,
      online: true,
    },
  });
}

/** `#streamInner` 的直接子节点里，某个选择器所在的下标（-1 = 没渲染）。 */
function childIndex(wrapper, selector) {
  const inner = wrapper.find('#streamInner').element;
  const node = wrapper.find(selector);
  if (!node.exists()) return -1;
  return Array.prototype.indexOf.call(inner.children, node.element);
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
});

/* ─── 一、位置与结构 ─────────────────────────────────────────────────────── */

describe('一、审批块的位置与结构（旧 `#approvalBoard`：空态之后、消息列表之前）', () => {
  test('有记录 ⇒ 消息流里出现审批块，位置在消息列表之前，标题与 aria 名来自词条表', () => {
    const store = createApprovalStore({ now: () => T0 });
    seed(store, record());

    const wrapper = mountStream({ messages: [message(1)], approvals: store });
    const board = wrapper.find('#approvalBoard');
    expect(board.exists()).toBe(true);
    expect(board.classes()).toContain('approval-board');
    expect(board.attributes('role')).toBe('region');
    expect(board.attributes('aria-label')).toBe('审批');
    expect(board.attributes('data-od-id')).toBe('approval-board');
    expect(board.find('.d-sec-title').text()).toBe('审批');
    expect(board.find('[data-approval-id="rpl-1"]').exists()).toBe(true);
    // 位置判据：审批块在 `#messageList` **之前**（旧 `index.html:256-261` 的顺序）。
    expect(childIndex(wrapper, '#approvalBoard')).toBeGreaterThanOrEqual(0);
    expect(childIndex(wrapper, '#approvalBoard')).toBeLessThan(childIndex(wrapper, '#messageList'));
  });

  test('无记录 ⇒ 整块不渲染（不摆空容器）', () => {
    const store = createApprovalStore({ now: () => T0 });
    const wrapper = mountStream({ messages: [message(1)], approvals: store });
    expect(wrapper.find('#approvalBoard').exists()).toBe(false);
    expect(wrapper.find('.approval-board').exists()).toBe(false);
  });

  test('没接读数（`approvals` 为 null）⇒ 也不渲染，消息面不受影响', () => {
    const wrapper = mountStream({ messages: [message(1)], approvals: null });
    expect(wrapper.find('#approvalBoard').exists()).toBe(false);
    expect(wrapper.find('#messageList').exists()).toBe(true);
  });
});

/* ─── 二、空态互斥 ───────────────────────────────────────────────────────── */

describe('二、空态互斥（旧 `renderMessages()`：流里只有审批卡时不显示「还没有消息」）', () => {
  test('只有审批卡、没有消息 ⇒ 不显示「该频道还没有消息」空态', () => {
    const store = createApprovalStore({ now: () => T0 });
    seed(store, record());

    const wrapper = mountStream({ messages: [], approvals: store });
    expect(wrapper.find('#approvalBoard').exists()).toBe(true);
    expect(wrapper.find('#emptyNoMessage').exists()).toBe(false);
  });

  test('没有记录、没有消息 ⇒ 空态照常（审批块不占位）', () => {
    const store = createApprovalStore({ now: () => T0 });
    const wrapper = mountStream({ messages: [], approvals: store });
    expect(wrapper.find('#emptyNoMessage').exists()).toBe(true);
    expect(wrapper.find('#approvalBoard').exists()).toBe(false);
  });

  test('有消息也有审批卡 ⇒ 两者并存，空态不出现', () => {
    const store = createApprovalStore({ now: () => T0 });
    seed(store, record());

    const wrapper = mountStream({ messages: [message(1), message(2)], approvals: store });
    expect(wrapper.find('#emptyNoMessage').exists()).toBe(false);
    expect(wrapper.find('#approvalBoard').exists()).toBe(true);
    expect(wrapper.findAll('[data-seq]').length).toBe(2);
  });
});

/* ─── 三、排序与作用域 ───────────────────────────────────────────────────── */

describe('三、排序与作用域（旧 `approvalRecordsFor()`：pending 在前、组内 requested_at 倒序）', () => {
  test('pending 在前、其余在后；组内按 requested_at 倒序', () => {
    const store = createApprovalStore({ now: () => T0 });
    // 落库顺序故意打乱：呈现顺序只由判据决定，不由插入顺序决定。
    seed(store, record({ reply_id: 'old-approved', state: 'approved', requested_at: new Date(T0 - 500_000).toISOString() }));
    seed(store, record({ reply_id: 'pending-old', requested_at: new Date(T0 - 300_000).toISOString() }));
    seed(store, record({ reply_id: 'new-denied', state: 'denied', requested_at: new Date(T0 - 100_000).toISOString() }));
    seed(store, record({ reply_id: 'pending-new', requested_at: new Date(T0 - 60_000).toISOString() }));

    const wrapper = mountStream({ messages: [message(1)], approvals: store });
    const ids = wrapper.findAll('#approvalBoard [data-approval-id]').map((node) => node.attributes('data-approval-id'));
    expect(ids).toEqual(['pending-new', 'pending-old', 'new-denied', 'old-approved']);
  });

  test('只铺**本 target** 的记录：任务线程的审批不串进频道消息流', () => {
    const store = createApprovalStore({ now: () => T0 });
    seed(store, record({ reply_id: 'channel-one' }));
    seed(store, record({ reply_id: 'task-one', target_id: 'tgt-aaa' }));

    const wrapper = mountStream({ messages: [message(1)], approvals: store });
    const ids = wrapper.findAll('#approvalBoard [data-approval-id]').map((node) => node.attributes('data-approval-id'));
    expect(ids).toEqual(['channel-one']);
    expect(wrapper.find('[data-approval-id="task-one"]').exists()).toBe(false);
  });

  test('私信下不出现（App 给空 target：旧判据是 `state.activeChannelId` 在私信下为空）', () => {
    const store = createApprovalStore({ now: () => T0 });
    seed(store, record({ reply_id: 'dm-approval', target_id: 'dm-1' }));

    const wrapper = mountStream({
      state: emptyState({ activeTargetId: 'dm-1', dms: [dm()], channels: [channel()] }),
      messages: [],
      approvals: store,
      approvalTargetId: '',
    });
    expect(wrapper.find('#approvalBoard').exists()).toBe(false);
    // 空态仍按「没有消息」判：审批块不占位，也不掩盖它。
    expect(wrapper.find('#emptyNoMessage').exists()).toBe(true);
  });
});

/* ─── 四、从 App.vue 真实父组件进 ────────────────────────────────────────── */

class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = String(url);
    this.listeners = new Map();
    this.closed = false;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  close() {
    this.closed = true;
  }

  emit(type, payload) {
    (this.listeners.get(type) || []).forEach((listener) => listener({ data: JSON.stringify(payload) }));
  }
}

/** 一个「频道 + 私信 + 一条频道级审批」的服务端替身。 */
function createServer(options) {
  // 真实时钟下的「刚刚请求」：倒计时还在 600s 之内（夹具零点 `T0` 是过去时刻，不拿来铺页面）。
  const fresh = (overrides) =>
    record(Object.assign({ requested_at: new Date().toISOString() }, overrides || {}));
  const world = { approvals: [fresh()], posts: [], release: null };
  const routes = {
    '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
    '/api/workspace': () =>
      jsonResponse({
        member: OWNER,
        members: [OWNER, ADA],
        channels: [channel()],
        dms: [dm()],
        agents: [ADA],
        model_providers: [],
        local_providers: [],
        remote_consents: [],
      }),
    '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
    '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
    '/api/channels/ch-1/config': () => jsonResponse({ config: { mode: 'autonomous', budget_limit: 3 } }),
    '/api/dms/dm-1/messages': () => jsonResponse({ dm: dm(), messages: [] }),
    '/api/dms/dm-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/tasks': () => jsonResponse({ tasks: [] }),
    '/api/approvals?state=pending': () => jsonResponse({ approvals: world.approvals.slice() }),
    '/api/approvals/rpl-1': async (_url, request) => {
      const body = request.body ? JSON.parse(request.body) : { decisions: [] };
      // 请求先落地（幂等用例数的是**发出的请求**），再卡在闸门上模拟慢网。
      world.posts.push(body);
      if (world.release) await world.release();
      world.approvals = [];
      return jsonResponse({ approval: fresh({ state: 'approved', decisions: body.decisions }) });
    },
  };
  return { world, routes: Object.assign(routes, (options || {}).routes || {}) };
}

const mounted = [];

async function mountApp(server) {
  const stub = createFetchStub(server.routes);
  globalThis.fetch = stub;
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  const wrapper = mount(App, { props: { sourceFactory: () => FakeEventSource }, attachTo: document.body });
  mounted.push(wrapper);
  await flush();
  await flush();
  return { wrapper, stub };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  localStorage.clear();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('四、从 App.vue 真实父组件进', () => {
  test('频道页出现审批卡；点「允许」走 POST /api/approvals/{reply_id}，收口后动作消失且只剩一次请求', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);

    const board = wrapper.find('#approvalBoard');
    expect(board.exists()).toBe(true);
    // 空态互斥也在真实父组件下成立：频道没有消息，但审批卡在，于是不留「还没有消息」。
    expect(wrapper.find('#emptyNoMessage').exists()).toBe(false);

    const card = board.find('[data-approval-id="rpl-1"]');
    expect(card.attributes('data-approval-target')).toBe('ch-1');
    expect(card.attributes('data-state')).toBe('pending');
    expect(card.text()).toContain('Ada');
    expect(card.text()).toContain('write_file');

    // 慢网里的第二次点击：在途闸门吃掉它（请求层判据，不靠按钮的 disabled 兜底）。
    let release = null;
    server.world.release = () => new Promise((resolve) => { release = resolve; });
    await card.find('[data-approval-action="allow"]').trigger('click');
    await wrapper.find('[data-approval-action="allow"]').trigger('click');
    await flush();
    expect(server.world.posts).toHaveLength(1);
    expect(server.world.posts[0].decisions).toEqual([{ tool_call_id: 'tc-1', confirmed: true }]);
    release();
    server.world.release = null;
    await flush();
    await flush();

    expect(server.world.posts).toHaveLength(1);
    // 收口后卡片留着**只是没了动作**（旧 `renderChannelApprovals` 铺的是该 target 的全部记录，
    // 收口的那条是历史）；按钮与倒计时一起退场。
    const settled = wrapper.find('[data-approval-id="rpl-1"]');
    expect(settled.attributes('data-state')).toBe('approved');
    expect(settled.find('.approval-actions').exists()).toBe(false);
    expect(settled.find('[data-approval-action="allow"]').exists()).toBe(false);
  });

  test('切到私信 ⇒ 审批块不出现（旧判据：审批归属 target 在私信下为空）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    expect(wrapper.find('#approvalBoard').exists()).toBe(true);

    await wrapper.find('[data-od-id="dm-dm-1"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#chanTitle').text()).toBe('@Ada');
    expect(wrapper.find('#approvalBoard').exists()).toBe(false);
    expect(wrapper.find('.approval-card').exists()).toBe(false);
  });
});
