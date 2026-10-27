/* 任务引用 chip / 频道指针 chip（票 #309）

   断言落在**渲染出来的元素、文字与点击后果**上：类名与属性逐字沿用旧页面
   （`.task-ref-chip` / `data-open-task` / `data-task-pointer`，`app.js` 的 `appendTaskRefChip()`），
   文案来自词条表（`msg.taskRef*` / `msg.taskThreadEntry*`）。

   判据只有一处实现（`components/TaskRefChip.vue`）：`MessageItem` 的两个分支与 `TaskThread` 的
   讨论行都挂同一个组件，因此这里的用例按「同一个判据、三个挂点」组织。

   票面明写「至少一条用例从 `MessageStream` 真实父组件进（不直挂 `MessageItem`）」：第 1、2、4、5 条
   都从 `MessageStream` 进；第 3 条从 `App.vue` 进（点击后果在 App 的 `openTask()` 里）。
   最后一条挂 `TaskThread`（dock 的第二形态，同一份判据的第三个挂点）。 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { reactive } from 'vue';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;
const MessageStream = (await import('../src/components/MessageStream.vue')).default;
const TaskThread = (await import('../src/components/TaskThread.vue')).default;

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

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

/* `unread_count` / `title` 是**任务载荷**的字段（`db` 的任务读出），这里扮演后端把它们写死。 */
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
      message_count: 2,
      unread_count: 0,
      allowed_transitions: ['in_progress', 'closed'],
    },
    overrides || {},
  );
}

function message(seq, overrides) {
  return Object.assign(
    {
      id: 'msg-' + seq,
      target_id: 'ch-1',
      seq,
      kind: 'human',
      body: 'body ' + seq,
      created_at: '2026-01-01T00:00:00Z',
      mention_snapshot: [],
      author: OWNER,
      attachments: [],
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

/** 快照查名：`stores/task-board.js` 的 `taskOf(taskId)` 是同一个形状（就地查一份任务载荷）。 */
function lookupOf(tasks) {
  return (taskId) => tasks.find((item) => item.id === taskId) || null;
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  FakeEventSource.instances = [];
  localStorage.clear();
});

/* ─── 一、任务引用 chip（`task_ref`）────────────────────────────────────── */

describe('任务引用 chip（task_ref）', () => {
  test('带 `task_ref` 的消息产出 `.task-ref-chip`：文案含任务标题，`data-open-task` 是任务 id', () => {
    const tasks = [task()];
    const wrapper = mount(MessageStream, {
      props: {
        state: emptyState(),
        messages: [message(1, { task_ref: 'tsk-aaa001' })],
        typingNames: [],
        taskOf: lookupOf(tasks),
      },
    });
    const chip = wrapper.find('.task-ref-chip');
    expect(chip.exists()).toBe(true);
    // 它是按钮：点得动才有意义（假按钮是本仓明令禁止的形态）。
    expect(chip.element.tagName).toBe('BUTTON');
    expect(chip.attributes('type')).toBe('button');
    expect(chip.text()).toBe('任务 · 整理发布清单');
    expect(chip.attributes('data-open-task')).toBe('tsk-aaa001');
    expect(chip.attributes('aria-label')).toBe('打开任务 整理发布清单');
    // `data-task-pointer` 只挂在指针 chip 上（`syncTaskPointerChips()` 的选择子）。
    expect(chip.attributes('data-task-pointer')).toBeUndefined();
  });

  test('任务不在快照里 ⇒ 文案用 id 本身，不编名字、不抛错', () => {
    const wrapper = mount(MessageStream, {
      props: {
        state: emptyState(),
        messages: [message(1, { task_ref: 'tsk-gone' })],
        typingNames: [],
        taskOf: lookupOf([]),
      },
    });
    const chip = wrapper.find('.task-ref-chip');
    expect(chip.exists()).toBe(true);
    expect(chip.text()).toBe('任务 · tsk-gone');
    expect(chip.attributes('data-open-task')).toBe('tsk-gone');
  });
});

/* ─── 二、频道指针 chip（`task_pointer`）───────────────────────────────── */

describe('频道指针 chip（task_pointer）', () => {
  test('未读 0 ⇒ 「进入任务线程」；带 `data-task-pointer`（两个属性同值）', () => {
    const tasks = [task({ unread_count: 0 })];
    const wrapper = mount(MessageStream, {
      props: {
        state: emptyState(),
        // 指针消息是**频道 system 记录**（后端建任务时落的那条），不是对话。
        messages: [message(1, { kind: 'system', body: 'owner 创建了任务「整理发布清单」', task_pointer: 'tsk-aaa001' })],
        typingNames: [],
        taskOf: lookupOf(tasks),
      },
    });
    const chip = wrapper.find('.task-ref-chip');
    expect(chip.exists()).toBe(true);
    expect(chip.text()).toBe('进入任务线程');
    expect(chip.attributes('data-open-task')).toBe('tsk-aaa001');
    expect(chip.attributes('data-task-pointer')).toBe('tsk-aaa001');
    expect(chip.attributes('aria-label')).toBe('打开任务 整理发布清单 的任务线程');
  });

  test('未读 > 0 ⇒ 「进入任务线程 · N 条未读」，aria 名一并带计数', () => {
    const tasks = [task({ unread_count: 3 })];
    const wrapper = mount(MessageStream, {
      props: {
        state: emptyState(),
        messages: [message(1, { kind: 'system', body: 'owner 创建了任务', task_pointer: 'tsk-aaa001' })],
        typingNames: [],
        taskOf: lookupOf(tasks),
      },
    });
    const chip = wrapper.find('.task-ref-chip');
    expect(chip.text()).toBe('进入任务线程 · 3 条未读');
    expect(chip.attributes('aria-label')).toBe('打开任务 整理发布清单 的任务线程，有 3 条未读');
  });

  test('任务不在快照里 ⇒ 按未读 0 处理（不编造读数），文案用 id', () => {
    const wrapper = mount(MessageStream, {
      props: {
        state: emptyState(),
        messages: [message(1, { kind: 'system', body: 'owner 创建了任务', task_pointer: 'tsk-gone' })],
        typingNames: [],
        taskOf: lookupOf([]),
      },
    });
    const chip = wrapper.find('.task-ref-chip');
    expect(chip.text()).toBe('进入任务线程');
    expect(chip.attributes('aria-label')).toBe('打开任务 tsk-gone 的任务线程');
  });

  test('任务载荷更新（3 → 0）⇒ 已渲染的 chip 文案跟着变（Vue 版的 `syncTaskPointerChips`）', async () => {
    const tasks = reactive([task({ unread_count: 3 })]);
    const wrapper = mount(MessageStream, {
      props: {
        state: emptyState(),
        messages: [message(1, { kind: 'system', body: 'owner 创建了任务', task_pointer: 'tsk-aaa001' })],
        typingNames: [],
        taskOf: (taskId) => tasks.find((item) => item.id === taskId) || null,
      },
    });
    expect(wrapper.find('.task-ref-chip').text()).toBe('进入任务线程 · 3 条未读');
    // 标读之后任务载荷的未读变 0（正本在库，前端不自己算）。
    tasks[0].unread_count = 0;
    await wrapper.vm.$nextTick();
    expect(wrapper.find('.task-ref-chip').text()).toBe('进入任务线程');
    expect(wrapper.find('.task-ref-chip').attributes('aria-label')).toBe('打开任务 整理发布清单 的任务线程');
  });
});

/* ─── 三、点击后果（从 `App.vue` 进）───────────────────────────────────── */

function createServer(config) {
  const options = config || {};
  const world = {
    tasks: [task()],
    messages: [
      message(1, { kind: 'system', body: 'owner 创建了任务「整理发布清单」', task_pointer: 'tsk-aaa001' }),
      message(2, { body: '这条在说那张任务', task_ref: 'tsk-aaa001' }),
    ],
    threadMessages: [message(1, { target_id: 'tgt-aaa', body: '线程里的第一条', task_ref: 'tsk-aaa001' })],
  };
  const routes = {
    '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
    '/api/workspace': () =>
      jsonResponse({
        member: OWNER,
        members: [OWNER, ADA],
        channels: [channel()],
        dms: [],
        agents: [ADA],
        model_providers: [],
        local_providers: [],
        remote_consents: [],
      }),
    '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: world.messages.map((item) => ({ ...item })) }),
    '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
    '/api/channels/ch-1/config': () => jsonResponse({ config: { mode: 'autonomous', budget_limit: 3 } }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/tasks': () => jsonResponse({ tasks: world.tasks.map((item) => ({ ...item })) }),
    '/api/tasks/tsk-aaa001/messages': () =>
      jsonResponse({ task: { ...world.tasks[0] }, messages: world.threadMessages.map((item) => ({ ...item })) }),
    '/api/tasks/tsk-aaa001/read': (url, request) => {
      const body = request.body ? JSON.parse(request.body) : {};
      world.tasks[0] = { ...world.tasks[0], unread_count: 0, last_read_seq: body.seq };
      return jsonResponse({ task: { ...world.tasks[0] } });
    },
  };
  return { world, routes: Object.assign(routes, options.routes || {}) };
}

const mounted = [];

async function mountApp(server) {
  const stub = createFetchStub(server.routes);
  globalThis.fetch = stub;
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  const wrapper = mount(App, {
    props: { sourceFactory: () => FakeEventSource },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  await flush();
  await flush();
  return { wrapper, stub };
}

describe('点 chip ⇒ 打开该任务的线程（dock 切 `task` 档）', () => {
  test('频道流里的指针 chip 点下去就开线程（`openTask()` 的唯一入口）', async () => {
    const { wrapper } = await mountApp(createServer());
    const chip = wrapper.find('#messageList [data-task-pointer]');
    expect(chip.exists()).toBe(true);
    await chip.trigger('click');
    await flush();
    await flush();

    const dock = wrapper.find('[data-od-id="detail-dock"]');
    expect(dock.exists()).toBe(true);
    const thread = dock.find('.task-thread');
    expect(thread.exists()).toBe(true);
    // 打开的是**这一条**任务：标题来自它的任务载荷。
    expect(thread.find('[data-od-id="thread-title-input"]').element.value).toBe('整理发布清单');
  });

  test('对话消息里的引用 chip 点下去开同一条线程（两个 chip 一条路径）', async () => {
    const { wrapper } = await mountApp(createServer());
    const chip = wrapper.find('#messageList .msg .task-ref-chip[data-open-task="tsk-aaa001"]');
    expect(chip.exists()).toBe(true);
    await chip.trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="detail-dock"] .task-thread').exists()).toBe(true);
  });
});

/* ─── 四、线程内也挂（同一份判据的第三个挂点）────────────────────────── */

describe('任务线程的讨论行', () => {
  test('讨论行里的消息带 `task_ref` ⇒ 同样产出 chip（一份实现，两处复用）', () => {
    const tasks = [task()];
    const thread = reactive({
      state: {
        taskId: 'tsk-aaa001',
        task: task(),
        messages: [message(1, { target_id: 'tgt-aaa', body: '线程里的讨论', task_ref: 'tsk-aaa001' })],
        loading: false,
        error: '',
        sending: false,
        sendError: '',
      },
      saveTitle: async () => true,
      send: async () => ({ ok: true }),
      changeStatus: async () => true,
      changeAssignee: async () => true,
      statusLabel: (status) => status,
      transitionLabel: (target) => target,
    });
    const wrapper = mount(TaskThread, {
      props: { thread, members: [OWNER, ADA], assignees: [], taskOf: lookupOf(tasks) },
    });
    const row = wrapper.find('#ttReplies .tt-reply-row');
    expect(row.exists()).toBe(true);
    const chip = row.find('.task-ref-chip');
    expect(chip.exists()).toBe(true);
    expect(chip.text()).toBe('任务 · 整理发布清单');
    expect(chip.attributes('data-open-task')).toBe('tsk-aaa001');
  });
});

afterEach(() => {
  vi.useRealTimers();
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});
