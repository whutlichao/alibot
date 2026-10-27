/* 任务线程（票 #307）的 DOM 契约 + 请求契约

   断言落在**产品渲染出来的元素 / 文字 / 发出的请求**上：类名与 `data-od-id` 逐字沿用旧页面
   （`studio/index.html` 的 `#dockTask` 段与 `app.js` 的 `renderTaskThread()`），文案来自词条表。

   绝大多数用例从 `App.vue` 进（真实 `createAppContext` + `fetch` 替身 + `attachTo: document.body`）：
   「点卡片 → dock 切形态」「线程头的运行面板入口」「换 target 整个作废」这三条判据分散在 App 的
   `dockTarget` / `dockMode` / `selectTarget` 里，直挂 `TaskThread.vue` 一条都测不到（阶段一
   `DockPanel` 漏传 `pools` 的教训）。

   请求契约按票面「断言请求 target」：回复必须落在 `/api/tasks/<id>/messages` 且 body 带 `task_ref`。 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const App = (await import('../src/App.vue')).default;
const i18n = await import('../src/i18n/index.js');
const { createTaskThreadStore } = await import('../src/stores/task-thread.js');

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

/* `allowed_transitions` 是**后端载荷的一部分**（`db.allowed_task_transitions()` 的派生结果），
   这里按票面逐条写死而不是在测试里维护一张迁移表——测试扮演的是后端，不是第二个前端。 */
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
      unread_count: 2,
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

function taskRoutes(world, routes) {
  const list = routes || {};
  const current = world.tasks[0];
  list[`/api/tasks/${current.id}/messages`] = (url, request) => {
    if ((request.method || 'GET') === 'POST') {
      const body = request.body ? JSON.parse(request.body) : {};
      const seq = (world.messages[world.messages.length - 1] || { seq: 0 }).seq + 1;
      const created = message({ id: 'msg-' + seq, seq, body: body.body, kind: 'human' });
      world.messages.push(created);
      return jsonResponse({ message: created }, 201);
    }
    return jsonResponse({ task: { ...current }, messages: world.messages.map((item) => ({ ...item })) });
  };
  list[`/api/tasks/${current.id}/read`] = (url, request) => {
    const body = request.body ? JSON.parse(request.body) : {};
    world.tasks[0] = { ...world.tasks[0], unread_count: 0, last_read_seq: body.seq };
    return jsonResponse({ task: { ...world.tasks[0] } });
  };
  list[`/api/tasks/${current.id}/status`] = (url, request) => {
    const body = request.body ? JSON.parse(request.body) : {};
    if (!world.tasks[0].allowed_transitions.includes(body.status)) {
      return jsonResponse({ error: { message: '不能这样迁移', fields: {} } }, 409);
    }
    world.tasks[0] = { ...world.tasks[0], status: body.status, allowed_transitions: [] };
    return jsonResponse({
      task: { ...world.tasks[0] },
      message: message({ id: 'msg-sys-1', seq: 90, kind: 'system', body: 'owner 改状态为进行中' }),
    });
  };
  list[`/api/tasks/${current.id}/title`] = (url, request) => {
    const body = request.body ? JSON.parse(request.body) : {};
    world.tasks[0] = { ...world.tasks[0], title: body.title };
    return jsonResponse({ task: { ...world.tasks[0] } });
  };
  list[`/api/tasks/${current.id}/assignee`] = (url, request) => {
    const body = request.body ? JSON.parse(request.body) : {};
    world.tasks[0] = { ...world.tasks[0], assignee_member_id: body.member_id };
    return jsonResponse({
      task: { ...world.tasks[0] },
      message: message({ id: 'msg-sys-2', seq: 91, kind: 'system', body: 'owner 指派给 Ada' }),
    });
  };
  list['/api/targets/tgt-aaa/team-runs?limit=20&task_id=' + current.id] = () =>
    jsonResponse({ runs: [{ id: 'run-1', status: 'started', leader_member_id: 'mem-1' }] });
  list['/api/targets/tgt-aaa/plan'] = () => jsonResponse({ plan: { members: [] } });
  list['/api/channels/ch-1/config'] = () => jsonResponse({ config: { mode: 'autonomous', budget_limit: 3 } });
  list['/api/channels/ch-2/config'] = () => jsonResponse({ config: { mode: 'autonomous', budget_limit: 3 } });
  return list;
}

function createServer(overrides) {
  const options = overrides || {};
  const world = {
    tasks: [task()],
    messages: [message(), message({ id: 'msg-2', seq: 2, kind: 'system', body: 'owner 创建了任务' })],
  };
  const routes = {
    '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
    '/api/workspace': () =>
      jsonResponse({
        member: OWNER,
        members: [OWNER, ADA],
        channels: [channel(), channel({ id: 'ch-2', name: 'ops', description: '运维频道' })],
        dms: [{ id: 'dm-1', kind: 'dm', peer: ADA, unread_count: 0 }],
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
    '/api/dms/dm-1/messages': () => jsonResponse({ dm: { id: 'dm-1', kind: 'dm', peer: ADA }, messages: [] }),
    '/api/dms/dm-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/tasks': () => jsonResponse({ tasks: world.tasks.map((item) => ({ ...item })) }),
  };
  taskRoutes(world, routes);
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

async function openBoard(wrapper) {
  await wrapper.find('[data-od-id="tab-tasks"]').trigger('click');
  await flush();
  await flush();
}

async function openTask(wrapper, taskId) {
  await openBoard(wrapper);
  await wrapper.find(`[data-od-id="task-card-${taskId}"]`).trigger('click');
  await flush();
  await flush();
}

function dock(wrapper) {
  return wrapper.find('[data-od-id="detail-dock"]');
}

beforeEach(() => {
  FakeEventSource.instances = [];
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  vi.restoreAllMocks();
});

describe('任务线程（dock 第二形态）', () => {
  test('点卡片 ⇒ dock 切 task 档并显示该任务线程；再点另一个任务 ⇒ 换成新的（不叠）', async () => {
    const server = createServer();
    const second = task({ id: 'tsk-bbb002', target_id: 'tgt-bbb', title: '巡检部署', unread_count: 0 });
    server.routes['/api/tasks'] = () => jsonResponse({ tasks: [task(), second] });
    server.routes['/api/tasks/tsk-bbb002/messages'] = () =>
      jsonResponse({ task: { ...second }, messages: [message({ id: 'msg-b1', target_id: 'tgt-bbb', body: '部署讨论' })] });
    server.routes['/api/tasks/tsk-bbb002/read'] = () => jsonResponse({ task: { ...second } });
    server.routes['/api/channels/ch-1/config'] = () => jsonResponse({ config: { mode: 'autonomous' } });
    const { wrapper } = await mountApp(server);

    await openTask(wrapper, 'tsk-aaa001');
    expect(wrapper.findAll('[data-od-id="task-thread"]')).toHaveLength(1);
    expect(wrapper.find('[data-od-id="thread-title-input"]').element.value).toBe('整理发布清单');
    expect(dock(wrapper).find('.task-thread').exists()).toBe(true);

    await openBoard(wrapper);
    await wrapper.find('[data-od-id="task-card-tsk-bbb002"]').trigger('click');
    await flush();
    await flush();
    // 一次只有一个线程：换的是内容，不是叠上一层。
    expect(wrapper.findAll('[data-od-id="task-thread"]')).toHaveLength(1);
    expect(wrapper.find('[data-od-id="thread-title-input"]').element.value).toBe('巡检部署');
    expect(wrapper.text()).not.toContain('第一条讨论');
  });

  test('线程内 composer 发出的消息落在该任务线程（请求路径 + body 里的 task_ref）', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');

    await wrapper.find('[data-od-id="thread-reply-input"]').setValue('我的新回复');
    await wrapper.find('[data-od-id="thread-reply-send"]').trigger('click');
    await flush();
    await flush();

    const posts = stub.calls.filter((call) => call.method === 'POST' && call.url === '/api/tasks/tsk-aaa001/messages');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ body: '我的新回复', task_ref: 'tsk-aaa001' });
    // 落库后进讨论段（前端按回带的消息吸收，不等下一次全量重读）。
    expect(wrapper.text()).toContain('我的新回复');
    // 发送后输入框清空（与主线 composer 同一条）。
    expect(wrapper.find('[data-od-id="thread-reply-input"]').element.value).toBe('');
  });

  test('线程头出现运行面板入口；点击后 dock 切 run 档且 team-runs 请求带 task_id 过滤', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');

    const entry = wrapper.find('[data-od-id="btn-thread-run-panel"]');
    expect(entry.exists()).toBe(true);
    expect(entry.attributes('title')).toBe(i18n.t('runPanel.taskEntryTitle'));

    await entry.trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('[data-od-id="run-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="task-thread"]').exists()).toBe(false);
    const runsCall = stub.calls.find((call) => call.url.includes('/team-runs'));
    expect(runsCall.url).toContain('task_id=tsk-aaa001');
  });

  test('运行面板在任务上下文的标题 = 任务标题（不是频道名）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');
    await wrapper.find('[data-od-id="btn-thread-run-panel"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('#rpTitle').text()).toBe('整理发布清单');
    expect(wrapper.find('#rpTitle').text()).not.toBe('design');
  });

  test('从任务上下文关掉运行面板 ⇒ 回任务线程（旧 closeRunPanel 的 fromTask 分支）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');
    await wrapper.find('[data-od-id="btn-thread-run-panel"]').trigger('click');
    await flush();
    await flush();

    await wrapper.find('[data-od-id="run-panel-close"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="task-thread"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="run-panel"]').exists()).toBe(false);
  });

  test('换 target：整个 dock 收起、旧线程读数不残留', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');
    expect(wrapper.find('[data-od-id="task-thread"]').exists()).toBe(true);

    await wrapper.find('[data-od-id="channel-ch-2"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('[data-od-id="detail-dock"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="task-thread"]').exists()).toBe(false);
  });

  test('resetForTarget() 把读数整个作废（换 target 的 store 一侧）', async () => {
    const server = createServer();
    const stub = createFetchStub(server.routes);
    globalThis.fetch = stub;
    const thread = createTaskThreadStore({ online: () => true });
    await thread.open('tsk-aaa001');
    expect(thread.state.task.title).toBe('整理发布清单');
    expect(thread.state.messages.length).toBe(2);

    thread.resetForTarget();
    expect(thread.state.task).toBeNull();
    expect(thread.state.messages).toEqual([]);
  });

  test('members 档与 task 档互斥：同一时刻只有一个 dock 子形态', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');

    const dockChildren = wrapper.find('[data-od-id="detail-dock"]').element.children;
    expect(dockChildren.length).toBe(1);
    expect(dockChildren[0].className).toBe('task-thread');
  });

  test('状态 chips 只出载荷 allowed_transitions 里的；closed 任务出 task.noTransitions', async () => {
    const server = createServer();
    server.routes['/api/tasks/tsk-aaa001/messages'] = () =>
      jsonResponse({ task: task(), messages: [message(), message({ id: 'msg-2', seq: 2, kind: 'system', body: '创建' })] });
    const { wrapper } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');

    expect(wrapper.find('[data-od-id="thread-move-in_progress"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="thread-move-closed"]').exists()).toBe(true);
    // 不在载荷里的目标一个都不出（前端不持迁移表副本）。
    expect(wrapper.find('[data-od-id="thread-move-done"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="thread-move-in_review"]').exists()).toBe(false);

    // 终态任务：chips 换成一句事实，不是空容器。
    const closedServer = createServer();
    closedServer.routes['/api/tasks'] = () =>
      jsonResponse({ tasks: [task({ status: 'closed', allowed_transitions: [] })] });
    closedServer.routes['/api/tasks/tsk-aaa001/messages'] = () =>
      jsonResponse({ task: task({ status: 'closed', allowed_transitions: [] }), messages: [] });
    const second = await mountApp(closedServer);
    await openTask(second.wrapper, 'tsk-aaa001');
    expect(second.wrapper.find('[data-od-id="thread-transitions"]').text()).toBe(i18n.t('task.noTransitions'));
    expect(second.wrapper.find('[data-od-id="thread-move-in_progress"]').exists()).toBe(false);
  });

  test('回复失败：错误可见、草稿不丢、可重试', async () => {
    const server = createServer();
    let fail = true;
    server.routes['/api/tasks/tsk-aaa001/messages'] = (url, request) => {
      if ((request.method || 'GET') === 'POST' && fail) {
        return jsonResponse({ error: { message: '后端拒绝了这条回复', fields: {} } }, 400);
      }
      if ((request.method || 'GET') === 'POST') {
        return jsonResponse({ message: message({ id: 'msg-9', seq: 9, kind: 'human', body: '重试成功的回复' }) }, 201);
      }
      return jsonResponse({ task: task(), messages: [message()] });
    };
    const { wrapper, stub } = await mountApp(server);
    await openTask(wrapper, 'tsk-aaa001');

    await wrapper.find('[data-od-id="thread-reply-input"]').setValue('重试成功的回复');
    await wrapper.find('[data-od-id="thread-reply-send"]').trigger('click');
    await flush();
    await flush();

    const errorRow = wrapper.find('[data-od-id="thread-reply-error"]');
    expect(errorRow.exists()).toBe(true);
    expect(errorRow.text()).toContain('后端拒绝了这条回复');
    // 草稿不丢：失败后正文仍在输入框里，重试发的是同一句话。
    expect(wrapper.find('[data-od-id="thread-reply-input"]').element.value).toBe('重试成功的回复');

    fail = false;
    await wrapper.find('[data-od-id="thread-reply-send"]').trigger('click');
    await flush();
    await flush();
    const posts = stub.calls.filter((call) => call.method === 'POST' && call.url === '/api/tasks/tsk-aaa001/messages');
    expect(posts).toHaveLength(2);
    expect(posts[1].body).toEqual({ body: '重试成功的回复', task_ref: 'tsk-aaa001' });
    expect(wrapper.find('[data-od-id="thread-reply-error"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="thread-reply-input"]').element.value).toBe('');
  });
});

describe('命令面板与看板的新建任务入口', () => {
  test('命令面板 new-task 打开表单并提交到 POST /api/channels/{id}/tasks', async () => {
    const server = createServer();
    server.routes['/api/channels/ch-1/tasks'] = (url, request) => {
      const body = request.body ? JSON.parse(request.body) : {};
      return jsonResponse({ task: task({ id: 'tsk-new001', title: body.title }), message: message({ seq: 80, kind: 'system' }) }, 201);
    };
    const { wrapper, stub } = await mountApp(server);

    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
    await flush();
    await wrapper.find('[data-cmd="new-task"]').trigger('click');
    await flush();

    const modal = wrapper.find('#taskOverlay');
    expect(modal.element.hidden).toBe(false);
    await wrapper.find('[data-od-id="task-create-title"]').setValue('新任务标题');
    await wrapper.find('[data-od-id="task-create-channel"]').setValue('ch-1');
    await wrapper.find('[data-od-id="btn-create-task"]').trigger('click');
    await flush();
    await flush();

    const posts = stub.calls.filter((call) => call.method === 'POST' && call.url === '/api/channels/ch-1/tasks');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ title: '新任务标题' });
    expect(modal.element.hidden).toBe(true);
  });

  test('看板的「新建任务」不再是禁用入口，打开的是同一张表单', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);
    const button = wrapper.find('[data-od-id="btn-new-task"]');
    expect(button.attributes('disabled')).toBeUndefined();
    await button.trigger('click');
    await flush();
    expect(wrapper.find('#taskOverlay').element.hidden).toBe(false);
  });
});
