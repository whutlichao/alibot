/* 审批卡 + 待审批外显徽标（票 #308 / T8）

   这一面是**安全相关**的（倒计时 / 幂等 / 掩码），因此四组判据先钉在这里再写组件：

   1. 倒计时语义（旧页面 `app.js:3160-3200` 的 `approvalRemainingSeconds` / `approvalDisplayState`
      / `approvalRemainingText`，与后端 `approval.py` 的 `DEFAULT_APPROVAL_TIMEOUT_SECONDS = 600`）；
   2. 幂等（请求层断言：同一动作重复点 ⇒ `POST /api/approvals/{id}` 的请求数 = 1）；
   3. 只在有权限时出动作（动作只出在 `displayState === 'pending'` 的记录上；`decisions` 只带**未决策**
      的 `tool_call_id`，旧 `approvalPendingToolCalls` / #215——否则相反结论会整批 409）；
   4. 失败不改写状态（卡片保持 pending、错误可见、可重试；读不到真实状态时保留本地状态）。

   另加：敏感键掩码（安全判据）、`task-approval-dot`（有 pending 出现 / 处理完消失 / 与审批卡同一份
   读数）、以及至少一条从 `App.vue` 真实父组件进的用例。

   断言落在**产品渲染出来的元素 / 文字 / 发出的请求**上：类名与 `data-od-id` 逐字沿用旧页面
   (`studio/index.html:446` 的 `#ttApprovalSection` / `:218` 的 `#taskApprovalDot` 与
   `app.js` 的 `approvalCardNode`)，文案来自词条表。时钟一律注入（`now` 参数 / store 的 `now` 选项），
   不用 `sleep`。 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createFetchStub, flush, jsonResponse, loadStudioGlobals } from './helpers/studio-globals.js';

loadStudioGlobals();

const App = (await import('../src/App.vue')).default;
const lib = await import('../src/lib/approvals.js');
const { createApprovalStore } = await import('../src/stores/approvals.js');
const ApprovalCard = (await import('../src/components/ApprovalCard.vue')).default;

/** 夹具的零点：所有倒计时用例都相对它算，读数与真实时钟无关。 */
const T0 = Date.parse('2026-03-01T00:00:00Z');

/** 「刚刚请求」的记录：真实时钟的当下，于是倒计时还在 600s 之内（夹具的零点 T0 是过去时刻）。 */
function fresh(overrides) {
  return record(Object.assign({ requested_at: new Date().toISOString() }, overrides || {}));
}

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

function record(overrides) {
  return Object.assign(
    {
      reply_id: 'rpl-1',
      target_id: 'tgt-aaa',
      member_id: 'mem-1',
      tool_calls: [
        { id: 'tc-1', name: 'write_file', input: { path: '/etc/x' } },
        { id: 'tc-2', name: 'run_shell', input: { cmd: 'ls' } },
      ],
      state: 'pending',
      decisions: [],
      requested_at: new Date(T0).toISOString(),
      resolved_at: null,
    },
    overrides || {},
  );
}

function mountCard(overrides) {
  const options = overrides || {};
  return mount(ApprovalCard, {
    props: {
      record: options.record || record(),
      targetId: options.targetId || 'tgt-aaa',
      member: options.member === undefined ? ADA : options.member,
      now: options.now === undefined ? T0 : options.now,
      online: options.online === undefined ? true : options.online,
      inFlight: Boolean(options.inFlight),
      error: options.error || '',
    },
  });
}

/* ─── 一、倒计时语义 ────────────────────────────────────────────────────── */

describe('一、倒计时语义（requested_at + 600s，呈现态 expired 只改呈现）', () => {
  test('剩余 = max(0, ceil((requested_at + 600s − now) / 1000))；不可解析 ⇒ null（不伪造）', () => {
    expect(lib.APPROVAL_TIMEOUT_SECONDS).toBe(600);
    expect(lib.approvalRemainingSeconds(record(), T0)).toBe(600);
    expect(lib.approvalRemainingSeconds(record(), T0 + 510_000)).toBe(90);
    expect(lib.approvalRemainingSeconds(record(), T0 + 600_000)).toBe(0);
    expect(lib.approvalRemainingSeconds(record(), T0 + 999_000)).toBe(0);
    expect(lib.approvalRemainingSeconds(record({ requested_at: 'not-a-date' }), T0)).toBeNull();
    expect(lib.approvalClockText(90)).toBe('01:30');
    expect(lib.approvalClockText(0)).toBe('00:00');
  });

  test('未到期（剩 90s）⇒ 两个按钮都在、data-urgent=0、文案「剩余 01:30」', () => {
    const wrapper = mountCard({ now: T0 + 510_000 });
    expect(wrapper.attributes('data-state')).toBe('pending');
    expect(wrapper.attributes('data-approval-id')).toBe('rpl-1');
    expect(wrapper.attributes('data-approval-target')).toBe('tgt-aaa');
    expect(wrapper.attributes('role')).toBe('group');
    expect(wrapper.attributes('aria-label')).toBe('待审批工具：write_file, run_shell');
    expect(wrapper.find('.approval-remaining').attributes('data-urgent')).toBe('0');
    expect(wrapper.find('.approval-remaining').text()).toBe('剩余 01:30');
    expect(wrapper.find('[data-approval-action="allow"]').exists()).toBe(true);
    expect(wrapper.find('[data-approval-action="deny"]').exists()).toBe(true);
    expect(wrapper.find('[data-approval-action="allow"]').attributes('disabled')).toBeUndefined();
    expect(wrapper.find('.approval-state').text()).toBe('待审批');
  });

  test('剩 30s ⇒ data-urgent=1（唯一的「快到期」视觉信号）', () => {
    const wrapper = mountCard({ now: T0 + 570_000 });
    expect(wrapper.find('.approval-remaining').attributes('data-urgent')).toBe('1');
    expect(wrapper.find('.approval-remaining').text()).toBe('剩余 00:30');
  });

  test('越过 600s ⇒ 呈现态 expired，allow / deny 两个按钮**整块不渲染**（不是禁用）', () => {
    const wrapper = mountCard({ now: T0 + 600_000 });
    expect(wrapper.attributes('data-state')).toBe('expired');
    expect(wrapper.find('.approval-state').text()).toBe('已过期');
    expect(wrapper.find('.approval-actions').exists()).toBe(false);
    expect(wrapper.find('[data-approval-action="allow"]').exists()).toBe(false);
    expect(wrapper.find('[data-approval-action="deny"]').exists()).toBe(false);
    // 终态不再报「剩余」：倒计时只属于待审批。
    expect(wrapper.find('.approval-remaining').text()).toBe('');
    expect(lib.approvalDisplayState(record(), T0 + 600_000)).toBe('expired');
    // 后端给的终态优先（到了上界也只改呈现，不改记录本身）。
    expect(lib.approvalDisplayState(record({ state: 'approved' }), T0)).toBe('approved');
    expect(record().state).toBe('pending');
  });

  test('requested_at 不可解析 ⇒ 「剩余 --:--」，仍是 pending、按钮还在、不挂 data-urgent', () => {
    const wrapper = mountCard({ record: record({ requested_at: 'not-a-date' }) });
    expect(wrapper.attributes('data-state')).toBe('pending');
    expect(wrapper.find('.approval-remaining').text()).toBe('剩余 --:--');
    expect(wrapper.find('.approval-remaining').attributes('data-urgent')).toBeUndefined();
    expect(wrapper.find('.approval-actions').exists()).toBe(true);
  });
});

/* ─── 二、幂等（请求层断言） ─────────────────────────────────────────────── */

describe('二、幂等：重复点 / 慢网重试只发一次 POST', () => {
  test('同一审批动作并发点两次 ⇒ POST /api/approvals/{id} 的请求数 = 1', async () => {
    let release = null;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const stub = createFetchStub({
      '/api/approvals/rpl-1': async () => {
        await gate;
        return jsonResponse({ approval: fresh({ state: 'approved' }) });
      },
    });
    globalThis.fetch = stub;
    const store = createApprovalStore({ now: () => T0 });
    store.applyFrame({ type: 'approval_required', target_id: 'tgt-aaa', reply_id: 'rpl-1', member_id: 'mem-1', tool_calls: record().tool_calls });

    const first = store.submit('tgt-aaa', 'rpl-1', true);
    // 慢网里的第二次点击：在途闸门先吃掉它（按钮此刻是 disabled，但请求层也不靠按钮兜底）。
    const second = store.submit('tgt-aaa', 'rpl-1', true);
    await flush();
    expect(stub.to('/api/approvals/rpl-1')).toHaveLength(1);
    release();
    await first;
    await second;
    expect(stub.to('/api/approvals/rpl-1')).toHaveLength(1);
    expect(store.state.inFlight).toEqual([]);
  });

  test('在途时两个按钮都 disabled，并出现「正在提交…」提示', () => {
    const wrapper = mountCard({ inFlight: true });
    const allow = wrapper.find('[data-approval-action="allow"]');
    const deny = wrapper.find('[data-approval-action="deny"]');
    expect(allow.attributes('disabled')).toBeDefined();
    expect(deny.attributes('disabled')).toBeDefined();
    expect(wrapper.find('.approval-note').text()).toBe('正在提交…');
  });

  test('已收口的记录再点不发第二个请求（动作只在 pending 时存在）', async () => {
    const stub = createFetchStub({
      '/api/approvals/rpl-1': () => jsonResponse({ approval: record({ state: 'denied' }) }),
    });
    globalThis.fetch = stub;
    const store = createApprovalStore({ now: () => T0 });
    store.applyFrame({ type: 'approval_required', target_id: 'tgt-aaa', reply_id: 'rpl-1', member_id: 'mem-1', tool_calls: record().tool_calls });
    store.applyFrame({ type: 'approval_resolved', target_id: 'tgt-aaa', reply_id: 'rpl-1', state: 'denied', decisions: [] });
    await store.submit('tgt-aaa', 'rpl-1', true);
    expect(stub.to('/api/approvals')).toHaveLength(0);
  });
});

/* ─── 三、只在有权限时出动作（判据来自后端载荷） ─────────────────────────── */

describe('三、动作只出在 pending 记录上，且只提交未决策的调用', () => {
  test('终态记录只读（无按钮），工具调用仍如实呈现', () => {
    const wrapper = mountCard({ record: record({ state: 'denied', decisions: [{ tool_call_id: 'tc-1', confirmed: false }] }) });
    expect(wrapper.attributes('data-state')).toBe('denied');
    expect(wrapper.find('.approval-actions').exists()).toBe(false);
    expect(wrapper.findAll('.approval-tool')).toHaveLength(2);
  });

  test('提交的 decisions 只含未决策的 tool_call_id（#215）', async () => {
    const stub = createFetchStub({
      '/api/approvals/rpl-1': () => jsonResponse({ approval: record({ state: 'pending' }) }),
    });
    globalThis.fetch = stub;
    const store = createApprovalStore({ now: () => T0 });
    store.applyFrame({
      type: 'approval_required',
      target_id: 'tgt-aaa',
      reply_id: 'rpl-1',
      member_id: 'mem-1',
      tool_calls: record().tool_calls,
    });
    // 一条记录里同时躺着**已决策的旧调用**与**新调用**（#215 的真实形态）。
    store.patchRecord('tgt-aaa', 'rpl-1', { decisions: [{ tool_call_id: 'tc-1', confirmed: true }] });
    await store.submit('tgt-aaa', 'rpl-1', false);
    const posts = stub.to('/api/approvals/rpl-1');
    expect(posts).toHaveLength(1);
    expect(posts[0].method).toBe('POST');
    expect(posts[0].body).toEqual({ decisions: [{ tool_call_id: 'tc-2', confirmed: false }] });
  });

  test('卡片只铺未决策的调用（已决策的历史不进待办段）', () => {
    const wrapper = mountCard({
      record: record({ decisions: [{ tool_call_id: 'tc-1', confirmed: true }] }),
    });
    const names = wrapper.findAll('.approval-tool-name').map((node) => node.text());
    expect(names).toEqual(['run_shell']);
  });
});

/* ─── 四、失败不改写状态 ───────────────────────────────────────────────── */

describe('四、提交失败：卡片保持原样、错误可见、可重试', () => {
  test('失败后仍 pending、按钮还在、错误行带后端原因；再点会再发一次请求', async () => {
    let fail = true;
    const stub = createFetchStub({
      '/api/approvals/rpl-1': () => (fail
        ? jsonResponse({ error: { message: '这一批已被别处决策', fields: {} } }, 409)
        : jsonResponse({ approval: record({ state: 'approved' }) })),
      '/api/approvals?target_id=tgt-aaa': () => jsonResponse({ approvals: [record()] }),
    });
    globalThis.fetch = stub;
    const store = createApprovalStore({ now: () => T0 });
    store.applyFrame({ type: 'approval_required', target_id: 'tgt-aaa', reply_id: 'rpl-1', member_id: 'mem-1', tool_calls: record().tool_calls });

    await store.submit('tgt-aaa', 'rpl-1', true);
    expect(store.errorFor('rpl-1')).toContain('这一批已被别处决策');
    const current = store.recordFor('tgt-aaa', 'rpl-1');
    expect(current.state).toBe('pending');
    expect(lib.approvalDisplayState(current, T0)).toBe('pending');
    expect(store.pendingCountFor('tgt-aaa')).toBe(1);

    fail = false;
    await store.submit('tgt-aaa', 'rpl-1', true);
    expect(stub.to('/api/approvals/rpl-1')).toHaveLength(2);
    expect(store.errorFor('rpl-1')).toBe('');
  });

  test('失败后按 target 重读真实状态；读不到就保留本地状态（不静默清空、不伪造成 expired）', async () => {
    const stub = createFetchStub({
      '/api/approvals/rpl-1': () => jsonResponse({ error: { message: 'boom', fields: {} } }, 500),
      // 重读也失败（后端不可达）：本地状态必须原样留着。
      '/api/approvals?target_id=tgt-aaa': () => jsonResponse({ error: { message: 'down', fields: {} } }, 503),
    });
    globalThis.fetch = stub;
    const store = createApprovalStore({ now: () => T0 });
    store.applyFrame({ type: 'approval_required', target_id: 'tgt-aaa', reply_id: 'rpl-1', member_id: 'mem-1', tool_calls: record().tool_calls });

    await store.submit('tgt-aaa', 'rpl-1', true);
    // 重读真的发过（旧 `reconcileApproval`）。
    expect(stub.to('/api/approvals?target_id=tgt-aaa').length).toBeGreaterThanOrEqual(1);
    const current = store.recordFor('tgt-aaa', 'rpl-1');
    expect(current).toBeTruthy();
    expect(current.state).toBe('pending');
    expect(store.errorFor('rpl-1')).toBe('boom');
  });

  test('错误行铺在卡片上，卡片其余部分不变', () => {
    const wrapper = mountCard({ error: '这一批已被别处决策' });
    expect(wrapper.find('.approval-error').text()).toBe('提交失败：这一批已被别处决策');
    expect(wrapper.attributes('data-state')).toBe('pending');
    expect(wrapper.find('.approval-actions').exists()).toBe(true);
  });
});

/* ─── 安全判据：敏感键掩码 ─────────────────────────────────────────────── */

describe('安全判据：敏感键的值一律掩码（••••）', () => {
  test('password / api_key / authorization / secret / token / cookie 全部掩码', () => {
    const summary = lib.approvalInputSummary({
      path: '/etc/x',
      password: 'p@ss',
      api_key: 'sk-live-1',
      authorization: 'Bearer abc',
      secret: 's3cr3t',
      token: 'tkn',
      cookie: 'sid=1',
    });
    ['p@ss', 'sk-live-1', 'Bearer abc', 's3cr3t', 'tkn', 'sid=1'].forEach((value) => {
      expect(summary).not.toContain(value);
    });
    expect(summary).toContain('password=••••');
    expect(summary).toContain('api_key=••••');
    expect(summary).toContain('authorization=••••');
    expect(summary).toContain('secret=••••');
    expect(summary).toContain('token=••••');
    expect(summary).toContain('cookie=••••');
    expect(summary).toContain('path=/etc/x');
    // 卡片上也不出现原值。
    const wrapper = mountCard({
      record: record({ tool_calls: [{ id: 'tc-9', name: 'write_file', input: { password: 'p@ss', path: '/etc/x' } }] }),
    });
    expect(wrapper.text()).not.toContain('p@ss');
    expect(wrapper.find('.approval-tool-input').text()).toContain('password=••••');
  });

  test('嵌套最多展开三层、长字符串截断加省略号、参数摘要总长有界', () => {
    expect(lib.approvalSanitizeValue({ a: { b: { c: { d: { e: 1 } } } } }, '', 0)).toEqual({
      a: { b: { c: { d: '…' } } },
    });
    const long = 'x'.repeat(200);
    const truncated = lib.approvalSanitizeValue(long, '', 0);
    expect(truncated).toHaveLength(118);
    expect(truncated.endsWith('…')).toBe(true);
    const summary = lib.approvalInputSummary({ a: long, b: long });
    // 旧页面同一形状：`slice(0, 237) + '…'` ⇒ 恰好 238 个字符。
    expect(summary.length).toBe(238);
    expect(summary.endsWith('…')).toBe(true);
    expect(lib.approvalInputSummary(null)).toBe('（无参数）');
    expect(lib.approvalInputSummary({})).toBe('（无参数）');
  });
});

/* ─── task-approval-dot（与审批卡同一份读数） ─────────────────────────────── */

const mounted = [];

class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = String(url);
    this.listeners = new Map();
    FakeEventSource.instances.push(this);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  close() {}

  emit(type, payload) {
    const frame = { data: JSON.stringify(payload) };
    for (const listener of this.listeners.get(type) || []) listener(frame);
  }
}

function channel(overrides) {
  return Object.assign(
    { id: 'ch-1', name: 'design', description: '设计频道', last_seq: 0, last_read_seq: 0, unread_count: 0 },
    overrides || {},
  );
}

function task(overrides) {
  return Object.assign(
    {
      id: 'tsk-aaa001',
      target_id: 'tgt-aaa',
      channel_id: 'ch-1',
      channel_name: 'design',
      title: '整理发布清单',
      status: 'todo',
      assignee_member_id: null,
      claimed_at: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      message_count: 0,
      unread_count: 0,
      allowed_transitions: ['in_progress', 'closed'],
    },
    overrides || {},
  );
}

function message(overrides) {
  return Object.assign(
    {
      id: 'msg-1',
      target_id: 'tgt-aaa',
      seq: 1,
      kind: 'human',
      body: '第一条讨论',
      created_at: '2026-01-01T00:00:00Z',
      author: OWNER,
      attachments: [],
    },
    overrides || {},
  );
}

function createServer(options) {
  const world = { approvals: [fresh()], posts: [] };
  const routes = {
    '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
    '/api/workspace': () =>
      jsonResponse({
        member: OWNER,
        members: [OWNER, ADA],
        channels: [channel(), channel({ id: 'ch-2', name: 'ops', description: '运维频道' })],
        dms: [],
        agents: [ADA],
        model_providers: [],
        local_providers: [],
        remote_consents: [],
      }),
    '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
    '/api/channels/ch-2/messages': () => jsonResponse({ channel: channel({ id: 'ch-2', name: 'ops' }), messages: [] }),
    '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-2/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/channels/ch-2/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
    '/api/channels/ch-1/config': () => jsonResponse({ config: { mode: 'autonomous', budget_limit: 3 } }),
    '/api/channels/ch-2/config': () => jsonResponse({ config: { mode: 'autonomous', budget_limit: 3 } }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/tasks': () => jsonResponse({ tasks: [task()] }),
    '/api/tasks/tsk-aaa001/messages': () => jsonResponse({ task: task(), messages: [message()] }),
    '/api/tasks/tsk-aaa001/read': () => jsonResponse({ task: task() }),
    '/api/approvals?state=pending': () => jsonResponse({ approvals: world.approvals.slice() }),
    '/api/approvals?target_id=tgt-aaa&state=pending': () => jsonResponse({ approvals: world.approvals.slice() }),
    '/api/approvals/rpl-1': (url, request) => {
      const body = request.body ? JSON.parse(request.body) : {};
      world.posts.push(body);
      world.approvals = [];
      return jsonResponse({
        approval: fresh({ state: body.decisions[0].confirmed ? 'approved' : 'denied', decisions: body.decisions }),
      });
    },
  };
  return { world, routes: Object.assign(routes, (options || {}).routes || {}) };
}

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

async function openTask(wrapper, taskId) {
  await wrapper.find('[data-od-id="tab-tasks"]').trigger('click');
  await flush();
  await flush();
  await wrapper.find(`[data-od-id="task-card-${taskId}"]`).trigger('click');
  await flush();
  await flush();
  await flush();
}

beforeEach(() => {
  FakeEventSource.instances = [];
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  vi.restoreAllMocks();
});

describe('task-approval-dot（任务页签上的待审批外显徽标）', () => {
  test('有 pending ⇒ 出现且 aria 名里的 N 是**任务数**；处理完 ⇒ 消失（与审批卡同一份读数）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);

    const dot = () => wrapper.find('[data-od-id="task-approval-dot"]');
    expect(dot().exists()).toBe(true);
    expect(dot().attributes('aria-label')).toBe('该频道有 1 个任务在等待审批');
    expect(dot().classes()).toContain('tab-approval-dot');

    // 从**真实父组件**进：点上页签 → 卡片 → dock 出线程，审批卡与圆点读的是同一份镜像。
    await openTask(wrapper, 'tsk-aaa001');
    const section = wrapper.find('[data-od-id="thread-approvals"]');
    expect(section.exists()).toBe(true);
    expect(section.find('.d-sec-title').text()).toBe('审批');
    expect(wrapper.find('.approval-card').attributes('data-approval-id')).toBe('rpl-1');

    // 审批：一个请求、落库后卡片与圆点**同时**消失（同一份读数，不是各自算一遍）。
    await wrapper.find('[data-approval-action="allow"]').trigger('click');
    await flush();
    await flush();
    await flush();
    const posts = server.world.posts;
    expect(posts).toHaveLength(1);
    expect(posts[0]).toEqual({ decisions: [{ tool_call_id: 'tc-1', confirmed: true }, { tool_call_id: 'tc-2', confirmed: true }] });
    // 卡片留着**只是没了动作**（旧 `renderTaskApprovals` 铺的是该 target 的全部记录，
    // 收口的那条是历史）；页签圆点则必须消失——它数的是「还在等审批的任务」。
    const card = wrapper.find('.approval-card');
    expect(card.attributes('data-state')).toBe('approved');
    expect(card.find('.approval-actions').exists()).toBe(false);
    expect(dot().exists()).toBe(false);
  });

  test('live 帧处理完 ⇒ 圆点与卡片一起消失（帧只写 approvals 镜像）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');
    expect(wrapper.find('[data-od-id="task-approval-dot"]').exists()).toBe(true);

    const source = FakeEventSource.instances[0];
    source.emit('approval_resolved', {
      type: 'approval_resolved',
      target_id: 'tgt-aaa',
      reply_id: 'rpl-1',
      state: 'approved',
      decisions: [{ tool_call_id: 'tc-1', confirmed: true }],
      seq: 999,
    });
    await flush();
    expect(wrapper.find('[data-od-id="task-approval-dot"]').exists()).toBe(false);
    expect(wrapper.find('.approval-card').attributes('data-state')).toBe('approved');
    expect(wrapper.find('.approval-actions').exists()).toBe(false);
    // live 帧只写 approvals 镜像：它不进消息流（帧里那个 seq 不落成任何一条消息）。
    expect(wrapper.vm.$el.querySelector('#msgCount').textContent.trim()).toBe('0');
  });

  test('任务卡片与列表行上的「待审批 N」角标读同一份读数', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await wrapper.find('[data-od-id="tab-tasks"]').trigger('click');
    await flush();
    await flush();
    const badge = wrapper.find('[data-od-id="task-approval-badge-tsk-aaa001"]');
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toBe('待审批 1');
    expect(badge.attributes('aria-label')).toBe('「整理发布清单」有 1 条待审批');
    expect(wrapper.find('[data-od-id="task-card-tsk-aaa001"]').attributes('aria-label')).toBe(
      '整理发布清单，待认领，1 条待审批，打开任务线程',
    );

    await wrapper.find('[data-od-id="taskview-list"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="task-approval-badge-tsk-aaa001"]').exists()).toBe(true);
  });
});

describe('从 App.vue 进的失败路径', () => {
  test('提交失败：卡片仍在、错误可见；重试再发一次请求', async () => {
    let fail = true;
    const server = createServer();
    server.routes['/api/approvals/rpl-1'] = (url, request) => {
      const body = request.body ? JSON.parse(request.body) : {};
      server.world.posts.push(body);
      if (fail) return jsonResponse({ error: { message: '这一批已被别处决策', fields: {} } }, 409);
      server.world.approvals = [];
      return jsonResponse({ approval: fresh({ state: 'approved' }) });
    };
    // 失败后的重读（旧 `reconcileApproval`，读全部状态）：后端说这条记录仍在待审批。
    server.routes['/api/approvals?target_id=tgt-aaa'] = () => jsonResponse({ approvals: [fresh()] });
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');

    await wrapper.find('[data-approval-action="deny"]').trigger('click');
    await flush();
    await flush();
    await flush();
    const error = wrapper.find('.approval-error');
    expect(error.exists()).toBe(true);
    expect(error.text()).toBe('提交失败：这一批已被别处决策');
    expect(wrapper.find('.approval-card').attributes('data-state')).toBe('pending');
    expect(wrapper.find('.approval-actions').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="task-approval-dot"]').exists()).toBe(true);

    fail = false;
    await wrapper.find('[data-approval-action="allow"]').trigger('click');
    await flush();
    await flush();
    await flush();
    expect(server.world.posts).toHaveLength(2);
    expect(server.world.posts[1]).toEqual({ decisions: [{ tool_call_id: 'tc-1', confirmed: true }, { tool_call_id: 'tc-2', confirmed: true }] });
    expect(wrapper.find('.approval-card').attributes('data-state')).toBe('approved');
    expect(wrapper.find('[data-od-id="task-approval-dot"]').exists()).toBe(false);
  });

  test('双击「允许」⇒ 请求数 = 1（按钮在途时 disabled）', async () => {
    let release = null;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const server = createServer();
    server.routes['/api/approvals/rpl-1'] = async (url, request) => {
      const body = request.body ? JSON.parse(request.body) : {};
      server.world.posts.push(body);
      await gate;
      server.world.approvals = [];
      return jsonResponse({ approval: fresh({ state: 'approved' }) });
    };
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');

    const allow = wrapper.find('[data-approval-action="allow"]');
    await allow.trigger('click');
    await allow.trigger('click');
    await flush();
    expect(server.world.posts).toHaveLength(1);
    expect(wrapper.find('[data-approval-action="allow"]').attributes('disabled')).toBeDefined();
    expect(wrapper.find('.approval-note').text()).toBe('正在提交…');
    release();
    await flush();
    await flush();
    await flush();
    expect(server.world.posts).toHaveLength(1);
    // 收口后卡片变终态（只读）、页签圆点消失——两者读的是同一份镜像。
    expect(wrapper.find('.approval-card').attributes('data-state')).toBe('approved');
    expect(wrapper.find('[data-od-id="task-approval-dot"]').exists()).toBe(false);
  });
});
