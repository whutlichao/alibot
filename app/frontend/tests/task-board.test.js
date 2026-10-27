/* 任务看板（票 #306）的 DOM 契约

   断言落在**产品渲染出来的元素 / 文字 / 发出的请求**上：`data-od-id` 与类名逐字沿用旧页面
   （`studio/index.html` 的 `#taskBoard` 段与 `app.js` 的 `renderTaskBoard()` / `taskCardNode()` /
   `boardList()`），文案来自词条表。

   绝大多数用例从 `App.vue` 进（真实 `createAppContext` + `fetch` 替身 + `attachTo: document.body`）：
   `tab-tasks` 的可见性在 `ChannelHeader` 与 `App.vue` 两处判、看板的开关在 App 的 `mode` 里、
   任务计数读的是 App 传下去的 `task-count`，直挂叶子组件两头都测不到（上一阶段 `DockPanel`
   漏传 `pools` 的教训）。

   拖拽用**声明式**状态表达（票面第三节）：`dragstart` / `dragover` / `dragleave` / `drop` / `dragend`
   只写组件里的一个 reactive 状态，列的落点类与卡片的 `.dragging` 都由它推导——因此用例只发
   原生拖拽事件、再断言语义结果，不需要 `dataTransfer` 替身（应用不读它）。
   「拖拽结束后的补发 click」用只伪造 `Date` 的假时钟测（不 `await sleep`）。 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;
const TaskBoard = (await import('../src/components/TaskBoard.vue')).default;
const { createTaskBoardStore } = await import('../src/stores/task-board.js');

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

function dm() {
  return { id: 'dm-1', kind: 'dm', peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1 }, unread_count: 0 };
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
      message_count: 0,
      unread_count: 0,
      allowed_transitions: ['in_progress', 'closed'],
    },
    overrides || {},
  );
}

function threeTasks() {
  return [
    task(),
    task({
      id: 'tsk-bbb002',
      target_id: 'tgt-bbb',
      channel_id: 'ch-2',
      channel_name: 'ops',
      title: '巡检部署',
      status: 'in_progress',
      assignee_member_id: 'mem-1',
      allowed_transitions: ['in_review', 'todo', 'closed'],
    }),
    task({
      id: 'tsk-ccc003',
      target_id: 'tgt-ccc',
      title: '归档旧设计',
      status: 'closed',
      assignee_member_id: 'owner',
      allowed_transitions: [],
    }),
  ];
}

/** 一个会随写路径变化的后端替身（`PATCH .../status` 改它，下一次 `GET /api/tasks` 读回新事实）。 */
function createServer(config) {
  const options = config || {};
  const world = { tasks: options.tasks || threeTasks() };

  const routes = {
    '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
    '/api/workspace': () =>
      jsonResponse({
        member: OWNER,
        members: [OWNER, ADA],
        channels: [channel(), channel({ id: 'ch-2', name: 'ops', description: '运维频道' })],
        dms: [dm()],
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
    '/api/dms/dm-1/messages': () => jsonResponse({ dm: dm(), messages: [] }),
    '/api/dms/dm-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/tasks': () => jsonResponse({ tasks: world.tasks.map((item) => ({ ...item })) }),
  };

  for (const item of world.tasks) {
    routes[`/api/tasks/${item.id}/status`] = (url, request) => {
      // 装置把请求体原样交过来（`createFetchStub` 的 `call.body` 才是解析后的那份）。
      const next = request.body ? JSON.parse(request.body).status : null;
      const index = world.tasks.findIndex((entry) => entry.id === item.id);
      const current = world.tasks[index];
      if (!current.allowed_transitions.includes(next)) {
        return jsonResponse({ error: { message: '不能这样迁移', fields: {} } }, 409);
      }
      world.tasks[index] = Object.assign({}, current, {
        status: next,
        allowed_transitions: next === 'in_progress' ? ['in_review', 'todo', 'closed'] : [],
      });
      return jsonResponse({ task: { ...world.tasks[index] } });
    };
  }

  return { world, routes, overrides: options.overrides || {} };
}

const mounted = [];

async function mountApp(server) {
  const stub = createFetchStub(Object.assign({}, server.routes, server.overrides));
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

function card(wrapper, taskId) {
  return wrapper.find(`[data-od-id="task-card-${taskId}"]`);
}

function column(wrapper, status) {
  return wrapper.find(`[data-od-id="task-column-${status}"]`);
}

function patchCalls(stub) {
  return stub.calls.filter((call) => call.method === 'PATCH');
}

async function dragTo(wrapper, taskId, status) {
  await card(wrapper, taskId).trigger('dragstart');
  const target = column(wrapper, status);
  await target.trigger('dragover');
  await target.trigger('drop');
  await card(wrapper, taskId).trigger('dragend');
  await flush();
  await flush();
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  FakeEventSource.instances = [];
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

/* ─── 一、页签与开关（从 App.vue 进） ──────────────────────────────────── */

describe('任务页签与看板入口', () => {
  test('频道下 `tab-tasks` 出现，计数是**当前频道**的任务数（与看板筛选无关）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    const tab = wrapper.find('[data-od-id="tab-tasks"]');
    expect(tab.exists()).toBe(true);
    // ch-1 有两张任务（tsk-aaa001 / tsk-ccc003），ch-2 一张。
    expect(wrapper.find('#taskCount').text()).toBe('2');
  });

  test('私信下 `tab-tasks` 不出现（任务恰好属于一个频道）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await wrapper.find('[data-od-id="dm-dm-1"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#chanTitle').text()).toBe('@Ada');
    expect(wrapper.find('[data-od-id="tab-tasks"]').exists()).toBe(false);
  });

  test('点 `tab-tasks` ⇒ 看板出现、消息流与 composer 让位（旧 `setViewMode("board")` 同一条）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    expect(wrapper.find('[data-od-id="tasks-view"]').exists()).toBe(false);

    await openBoard(wrapper);
    expect(wrapper.find('[data-od-id="tasks-view"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="messages-view"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="composer"]').exists()).toBe(false);
    // 频道头仍在（页签本身就是它的内容），且「任务」页签是选中态。
    expect(wrapper.find('#chanHead').attributes('hidden')).toBeUndefined();
    expect(wrapper.find('[data-od-id="tab-tasks"]').attributes('aria-selected')).toBe('true');
  });

  test('`T` 键与命令面板的「任务看板」都落到看板（不再是空绑定）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 't', bubbles: true }));
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="tasks-view"]').exists()).toBe(true);

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
    await flush();
    expect(wrapper.find('[data-od-id="tasks-view"]').exists()).toBe(false);
  });
});

/* ─── 二、五列与卡片字段 ───────────────────────────────────────────────── */

describe('看板列与卡片', () => {
  test('五列固定顺序 `todo → in_progress → in_review → done → closed`，空列有 `.drop-hint`', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);

    const order = wrapper.findAll('[data-od-id^="task-column-"]').map((node) => node.attributes('data-status'));
    expect(order).toEqual(['todo', 'in_progress', 'in_review', 'done', 'closed']);
    // 每列的头：状态点 + 状态词 + 计数。
    const todo = column(wrapper, 'todo');
    expect(todo.classes()).toContain('col');
    expect(todo.find('.col-head .st-dot').exists()).toBe(true);
    expect(todo.find('.col-head b').text()).toBe('待认领');
    expect(todo.find('.col-count').text()).toBe('1');
    // in_review 是空列：`.col-body` 里只有 `.drop-hint`。
    const empty = column(wrapper, 'in_review').find('.col-body');
    expect(empty.find('.drop-hint').text()).toBe('这一列还没有任务');
    expect(empty.findAll('[data-task]').length).toBe(0);
  });

  test('卡片显示 `#id 尾 6 位` / 标题 / 频道 tag / owner / 未读 / 状态', async () => {
    const server = createServer({
      tasks: [
        task({ unread_count: 3 }),
        task({
          id: 'tsk-bbb002',
          channel_id: 'ch-2',
          channel_name: 'ops',
          title: '巡检部署',
          status: 'in_progress',
          assignee_member_id: 'mem-1',
          allowed_transitions: ['in_review', 'todo', 'closed'],
          unread_count: 120,
        }),
      ],
    });
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);
    // 看板是全工作区视图：先回到「全部频道」，两张卡才在同一块里。
    await wrapper.find('[data-od-id="filter-channel-all"]').trigger('click');
    await flush();

    const mine = card(wrapper, 'tsk-aaa001');
    expect(mine.classes()).toContain('card');
    expect(mine.attributes('role')).toBe('button');
    expect(mine.attributes('tabindex')).toBe('0');
    expect(mine.attributes('data-status')).toBe('todo');
    expect(mine.find('.card-title').text()).toBe('整理发布清单');
    // `#` + 任务 id 尾 6 位（`tsk-aaa001` ⇒ `#aaa001`）。
    expect(mine.find('.card-num').text()).toBe('#aaa001');
    expect(mine.find('.card-tag').text()).toBe('#design');
    // 未指派走 `.card-owner.unassigned`（不编一个 owner 名）。
    expect(mine.find('.card-owner').classes()).toContain('unassigned');
    expect(mine.find('.card-owner').text()).toBe('未认领');
    expect(mine.find('.card-unread').text()).toBe('3');
    expect(mine.find('.card-unread').attributes('aria-label')).toBe('3 条未读任务消息');
    // 卡片根的可访问名把标题与状态并进去（role=button 会盖住子树）。
    expect(mine.attributes('aria-label')).toBe('整理发布清单，待认领，打开任务线程');

    const assigned = card(wrapper, 'tsk-bbb002');
    // 头像块带首字母（与消息流同一个头像色位规则），名字在它后面。
    expect(assigned.find('.card-owner .avatar').exists()).toBe(true);
    expect(assigned.find('.card-owner').text()).toContain('Ada');
    expect(assigned.find('.card-unread').text()).toBe('99+');
  });

  test('列表视图：`.result` 行 + 空态块（`.seg` 的另一半，同一份数据不重取）', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);
    await wrapper.find('[data-od-id="filter-channel-all"]').trigger('click');
    await flush();
    const readsBefore = stub.to('/api/tasks').length;

    await wrapper.find('[data-od-id="taskview-list"]').trigger('click');
    await flush();

    const rows = wrapper.findAll('[data-od-id^="task-row-"]');
    expect(rows.length).toBe(3);
    expect(rows[0].classes()).toContain('result');
    expect(rows[0].attributes('data-status')).toBe('todo');
    expect(rows[0].find('.r-top .hash').text()).toBe('#aaa001');
    expect(rows[0].find('.r-top .card-tag').text()).toBe('#design');
    expect(rows[0].find('.r-top .r-open').text()).toBe('打开线程 →');
    expect(rows[0].find('.r-title').text()).toBe('整理发布清单');
    expect(rows[0].find('.r-snip .card-owner').text()).toBe('未认领');
    // 切视图是呈现：不重读后端。
    expect(stub.to('/api/tasks').length).toBe(readsBefore);
  });

  test('列表视图空态：`.empty` 用 `board.noMatchTitle` / `board.noMatchBody`', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);
    await wrapper.find('[data-od-id="filter-mine"]').trigger('click');
    await wrapper.find('[data-od-id="taskview-list"]').trigger('click');
    await flush();
    // 我的任务 = 未指派 + 指派给 owner 的两张，所以这里先切到只有一张任务的频道。
    await wrapper.find('[data-od-id="filter-channel-ch-2"]').trigger('click');
    await flush();
    expect(wrapper.findAll('[data-od-id^="task-row-"]').length).toBe(0);
    const empty = wrapper.find('[data-od-id="task-list"] .empty');
    expect(empty.find('b').text()).toBe('没有匹配的任务');
    expect(empty.find('p').text()).toBe('试试关掉筛选，或新建一个任务。');
  });
});

/* ─── 三、跨列拖拽：一次请求、不乐观、非法零请求 ────────────────────────── */

describe('跨列拖拽', () => {
  test('合法落点发出**一次** `PATCH /api/tasks/{id}/status`，成功后按后端事实换列', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);

    await card(wrapper, 'tsk-aaa001').trigger('dragstart');
    const target = column(wrapper, 'in_progress');
    await target.trigger('dragover');
    // 落点态由 reactive 状态推导（合法 ⇒ `.drag-over`，不是 `.invalid-over`）。
    expect(target.classes()).toContain('drag-over');
    expect(target.classes()).not.toContain('invalid-over');

    await target.trigger('drop');
    await flush();
    await flush();

    const patches = patchCalls(stub);
    expect(patches.length).toBe(1);
    expect(patches[0].url).toBe('/api/tasks/tsk-aaa001/status');
    expect(patches[0].body).toEqual({ status: 'in_progress' });
    // 换列由后端事实驱动：重读后卡片出现在 in_progress 列。
    expect(column(wrapper, 'in_progress').find('[data-od-id="task-card-tsk-aaa001"]').exists()).toBe(true);
    expect(column(wrapper, 'todo').find('[data-od-id="task-card-tsk-aaa001"]').exists()).toBe(false);
  });

  test('非法落点：零请求 + 提示可见 + 卡片弹回原位', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);

    await card(wrapper, 'tsk-aaa001').trigger('dragstart');
    const illegal = column(wrapper, 'in_review');
    await illegal.trigger('dragover');
    expect(illegal.classes()).toContain('invalid-over');
    expect(illegal.classes()).not.toContain('drag-over');

    await illegal.trigger('drop');
    await card(wrapper, 'tsk-aaa001').trigger('dragend');
    await flush();
    await flush();

    // `todo` 的合法目标里没有 `in_review` ⇒ **不发请求**（前端守卫只读载荷）。
    expect(patchCalls(stub).length).toBe(0);
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('不能从「待认领」移到「待审」——不是合法迁移');
    // 卡片还在原列，且不再是拖拽态（`.dragging` 由 reactive 状态推导，dragend 后清掉）。
    const back = card(wrapper, 'tsk-aaa001');
    expect(column(wrapper, 'todo').find('[data-od-id="task-card-tsk-aaa001"]').exists()).toBe(true);
    expect(back.classes()).not.toContain('dragging');
    expect(illegal.classes()).not.toContain('invalid-over');
  });

  test('拖回同一列：无动作（既不是迁移，也不给一句「非法」的指责）', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);

    await card(wrapper, 'tsk-aaa001').trigger('dragstart');
    const same = column(wrapper, 'todo');
    await same.trigger('dragover');
    expect(same.classes()).not.toContain('drag-over');
    expect(same.classes()).not.toContain('invalid-over');
    await same.trigger('drop');
    await card(wrapper, 'tsk-aaa001').trigger('dragend');
    await flush();

    expect(patchCalls(stub).length).toBe(0);
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toBe('');
  });

  test('**没有**乐观移动：请求在途时卡片仍在原列（断言中途 DOM）', async () => {
    let release = null;
    const server = createServer();
    server.overrides['/api/tasks/tsk-aaa001/status'] = () =>
      new Promise((resolve) => {
        release = () => {
          server.world.tasks[0] = Object.assign({}, server.world.tasks[0], {
            status: 'in_progress',
            allowed_transitions: ['in_review', 'todo', 'closed'],
          });
          resolve(jsonResponse({ task: { ...server.world.tasks[0] } }));
        };
      });
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);

    await card(wrapper, 'tsk-aaa001').trigger('dragstart');
    await column(wrapper, 'in_progress').trigger('dragover');
    await column(wrapper, 'in_progress').trigger('drop');
    await flush();

    // 请求已经在飞（一次），但 DOM 仍按旧读数铺：卡片在 todo 列。
    expect(patchCalls(stub).length).toBe(1);
    expect(column(wrapper, 'todo').find('[data-od-id="task-card-tsk-aaa001"]').exists()).toBe(true);
    expect(column(wrapper, 'in_progress').find('[data-od-id="task-card-tsk-aaa001"]').exists()).toBe(false);

    release();
    await flush();
    await flush();
    expect(column(wrapper, 'in_progress').find('[data-od-id="task-card-tsk-aaa001"]').exists()).toBe(true);
  });

  test('在途时再拖一次同一张卡：不叠加第二个请求（同一张任务至多一个在飞迁移）', async () => {
    let release = null;
    const server = createServer();
    server.overrides['/api/tasks/tsk-aaa001/status'] = () =>
      new Promise((resolve) => {
        release = () => resolve(jsonResponse({ task: { ...server.world.tasks[0] } }));
      });
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);

    await card(wrapper, 'tsk-aaa001').trigger('dragstart');
    await column(wrapper, 'in_progress').trigger('dragover');
    await column(wrapper, 'in_progress').trigger('drop');
    await flush();
    await card(wrapper, 'tsk-aaa001').trigger('dragstart');
    await column(wrapper, 'closed').trigger('dragover');
    await column(wrapper, 'closed').trigger('drop');
    await flush();
    expect(patchCalls(stub).length).toBe(1);

    release();
    await flush();
    await flush();
  });

  test('卡的 `.dragging` 是拖拽态（CSS 的 opacity .4）：dragstart 挂上、dragend 摘掉', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);

    await card(wrapper, 'tsk-aaa001').trigger('dragstart');
    expect(card(wrapper, 'tsk-aaa001').classes()).toContain('dragging');
    await card(wrapper, 'tsk-aaa001').trigger('dragend');
    expect(card(wrapper, 'tsk-aaa001').classes()).not.toContain('dragging');
  });
});

/* ─── 四、卡片动作菜单与拖拽同一条判定 ─────────────────────────────────── */

describe('卡片「流转」菜单（触屏 / 键盘的等价路径）', () => {
  test('菜单项集合 = 载荷 `allowed_transitions`，文案走 `task.transition.<target>`', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);

    await wrapper.find('[data-od-id="task-card-tsk-aaa001"] .card-menu-btn').trigger('click');
    await flush();

    const menu = wrapper.find('[data-od-id="task-move-menu"]');
    expect(menu.exists()).toBe(true);
    expect(menu.attributes('role')).toBe('menu');
    const items = menu.findAll('[role="menuitem"]');
    expect(items.map((node) => node.attributes('data-move-target'))).toEqual(['in_progress', 'closed']);
    expect(items.map((node) => node.text())).toEqual(['推进至进行中', '关闭任务']);
    expect(items.map((node) => node.attributes('tabindex'))).toEqual(['-1', '-1']);
    // 触发器声明自己展开着。
    expect(wrapper.find('[data-od-id="task-card-tsk-aaa001"] .card-menu-btn').attributes('aria-expanded')).toBe('true');
  });

  test('点菜单项 = 一次迁移请求（与拖拽同一条写路径），菜单关掉', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);

    await wrapper.find('[data-od-id="task-card-tsk-aaa001"] .card-menu-btn').trigger('click');
    await flush();
    await wrapper.find('[data-od-id="task-move-menu"] [data-move-target="in_progress"]').trigger('click');
    await flush();
    await flush();

    const patches = patchCalls(stub);
    expect(patches.length).toBe(1);
    expect(patches[0].body).toEqual({ status: 'in_progress' });
    expect(wrapper.find('[data-od-id="task-move-menu"]').exists()).toBe(false);
  });

  test('键盘面：↓ 循环、Enter 激活、Esc 关闭并还焦点', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);

    const trigger = wrapper.find('[data-od-id="task-card-tsk-aaa001"] .card-menu-btn');
    await trigger.trigger('click');
    await flush();
    const menu = wrapper.find('[data-od-id="task-move-menu"]');
    const items = menu.findAll('[role="menuitem"]');
    // 打开即把焦点放到第一项。
    expect(document.activeElement).toBe(items[0].element);

    await menu.trigger('keydown', { key: 'ArrowDown' });
    expect(document.activeElement).toBe(items[1].element);
    // 从最后一项再 ↓ 回到第一项（环绕）。
    await menu.trigger('keydown', { key: 'ArrowDown' });
    expect(document.activeElement).toBe(items[0].element);
    await menu.trigger('keydown', { key: 'ArrowUp' });
    expect(document.activeElement).toBe(items[1].element);

    await menu.trigger('keydown', { key: 'Escape' });
    await flush();
    expect(wrapper.find('[data-od-id="task-move-menu"]').exists()).toBe(false);
    // 焦点还给触发器（不是 body）。
    expect(document.activeElement).toBe(trigger.element);
    expect(patchCalls(stub).length).toBe(0);

    await trigger.trigger('click');
    await flush();
    const reopened = wrapper.find('[data-od-id="task-move-menu"]');
    await reopened.trigger('keydown', { key: 'Enter' });
    await flush();
    await flush();
    expect(patchCalls(stub).length).toBe(1);
  });

  test('终态卡片（`closed`）：没有 `draggable`、没有 `.card-menu-btn`（不摆拖不动的拖拽源）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);

    const closed = card(wrapper, 'tsk-ccc003');
    expect(closed.attributes('draggable')).toBeUndefined();
    expect(closed.find('.card-menu-btn').exists()).toBe(false);
    expect(closed.classes()).not.toContain('has-menu');
    // 有出边的卡片反过来两条都挂。
    const open = card(wrapper, 'tsk-aaa001');
    expect(open.attributes('draggable')).toBe('true');
    expect(open.find('.card-menu-btn').exists()).toBe(true);
    expect(open.find('.card-menu-btn').attributes('aria-haspopup')).toBe('menu');
    expect(open.find('.card-menu-btn').attributes('aria-label')).toBe('「整理发布清单」的流转菜单');
  });

  test('离线时菜单触发器禁用（不发得出去的写就不摆可按的入口）', async () => {
    const stub = createFetchStub({ '/api/tasks': () => jsonResponse({ tasks: threeTasks() }) });
    globalThis.fetch = stub;
    const board = createTaskBoardStore({ ownerId: () => 'owner' });
    const wrapper = mount(TaskBoard, {
      props: {
        board,
        state: { online: false, members: [OWNER, ADA], channels: [channel(), channel({ id: 'ch-2', name: 'ops' })] },
      },
      attachTo: document.body,
    });
    await flush();
    expect(wrapper.find('[data-od-id="task-card-tsk-aaa001"] .card-menu-btn').attributes('disabled')).toBeDefined();
    wrapper.unmount();
  });
});

/* ─── 五、视图与筛选都是呈现状态 ───────────────────────────────────────── */

describe('呈现状态：视图档与筛选', () => {
  test('视图切换 `board ⇄ list` 不写浏览器存储（旧页面纪律），也没有后端可读的镜像', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);
    await wrapper.find('[data-od-id="filter-channel-all"]').trigger('click');
    await flush();
    await wrapper.find('[data-od-id="taskview-list"]').trigger('click');
    await flush();
    expect(wrapper.findAll('[data-od-id^="task-row-"]').length).toBe(3);
    expect(localStorage.getItem('alibot.taskView')).toBe(null);
    expect(localStorage.length).toBe(1); // 只有唯一允许的那条「上次打开的 target」
  });

  test('重挂一个全新的看板仍是**默认档**（视图是内存状态，不是持久化的）', async () => {
    const server = createServer();
    const first = await mountApp(server);
    await openBoard(first.wrapper);
    await first.wrapper.find('[data-od-id="filter-channel-all"]').trigger('click');
    await flush();
    await first.wrapper.find('[data-od-id="taskview-list"]').trigger('click');
    await flush();
    expect(first.wrapper.findAll('[data-od-id^="task-row-"]').length).toBe(3);
    first.wrapper.unmount();
    mounted.pop();

    // 第二个 App：看板是全新挂上来的，视图回到 board（没有从任何持久化里读档）。
    const second = await mountApp(server);
    await openBoard(second.wrapper);
    expect(second.wrapper.find('[data-od-id="taskview-board"]').classes()).toContain('is-active');
    expect(second.wrapper.find('[data-od-id="taskview-board"]').attributes('aria-selected')).toBe('true');
    expect(second.wrapper.findAll('[data-od-id^="task-row-"]').length).toBe(0);
    expect(second.wrapper.findAll('[data-od-id^="task-column-"]').length).toBe(5);
  });

  test('「我的任务」筛的是「指派给我或未认领」（不是「我建的」）', async () => {
    const server = createServer();
    const { wrapper, stub } = await mountApp(server);
    await openBoard(wrapper);
    const reads = stub.to('/api/tasks').length;

    await wrapper.find('[data-od-id="filter-mine"]').trigger('click');
    await flush();

    expect(wrapper.find('[data-od-id="filter-mine"]').classes()).toContain('is-on');
    expect(wrapper.find('[data-od-id="filter-mine"]').attributes('aria-pressed')).toBe('true');
    const ids = wrapper.findAll('[data-task]').map((node) => node.attributes('data-task'));
    expect(ids).toEqual(['tsk-aaa001', 'tsk-ccc003']);
    // 筛选是**本地**筛（判据已在载荷里）：不重新发请求。
    expect(stub.to('/api/tasks').length).toBe(reads);
  });

  test('频道 chip 改变可见集合，「全部频道」回到全量', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);

    // 从页签进来默认筛到**当前频道**（旧 `showTaskBoard()` 同一条）。
    expect(wrapper.find('[data-od-id="filter-channel-ch-1"]').classes()).toContain('is-on');
    expect(wrapper.findAll('[data-task]').map((node) => node.attributes('data-task'))).toEqual([
      'tsk-aaa001',
      'tsk-ccc003',
    ]);

    await wrapper.find('[data-od-id="filter-channel-ch-2"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="filter-channel-ch-2"]').classes()).toContain('is-on');
    expect(wrapper.findAll('[data-task]').map((node) => node.attributes('data-task'))).toEqual(['tsk-bbb002']);

    await wrapper.find('[data-od-id="filter-channel-all"]').trigger('click');
    await flush();
    expect(wrapper.findAll('[data-task]').length).toBe(3);
    expect(wrapper.find('[data-od-id="filter-channel-all"]').classes()).toContain('is-on');
  });

  test('新建任务按钮可见且可用（表单随票 #307 迁完，`new-task` 两个入口共用一张表）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);
    const button = wrapper.find('[data-od-id="btn-new-task"]');
    expect(button.exists()).toBe(true);
    expect(button.classes()).toEqual(expect.arrayContaining(['btn', 'btn-sm']));
    // 在线且已迁完 ⇒ 不再是禁用条目；`title` 也不再是「没迁」的说明。
    expect(button.attributes('disabled')).toBeUndefined();
    expect(button.attributes('title')).toBeUndefined();
  });
});

/* ─── 六、点卡片 = 打开线程；拖拽不污染点击 ──────────────────────────────── */

describe('点卡片与拖拽不互相污染', () => {
  test('点卡片触发 `openTask`（dock 进 task 形态；内容归 T7）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    expect(wrapper.find('#dock').exists()).toBe(false);
    await openBoard(wrapper);

    await card(wrapper, 'tsk-aaa001').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#dock').exists()).toBe(true);
    // task 形态，不是 run 形态（运行面板的入口没被顺带打开）。
    expect(wrapper.find('[data-od-id="run-panel"]').exists()).toBe(false);
  });

  test('Enter / Space 也打开线程（卡片是 div，键盘语义要显式补）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);
    await card(wrapper, 'tsk-aaa001').trigger('keydown', { key: 'Enter' });
    await flush();
    await flush();
    expect(wrapper.find('#dock').exists()).toBe(true);
  });

  test('点 `.card-menu-btn` 不打开线程（它是卡内控件，先于卡片判定）', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);
    await wrapper.find('[data-od-id="task-card-tsk-aaa001"] .card-menu-btn').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="task-move-menu"]').exists()).toBe(true);
    expect(wrapper.find('#dock').exists()).toBe(false);
  });

  test('拖拽刚结束的那个 `click` 不打开线程（短窗口抑制，旧 `suppressClickUntil` 同一条）', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    const server = createServer();
    const { wrapper } = await mountApp(server);
    await openBoard(wrapper);

    await dragTo(wrapper, 'tsk-aaa001', 'in_progress');
    await card(wrapper, 'tsk-aaa001').trigger('click');
    await flush();
    expect(wrapper.find('#dock').exists()).toBe(false);

    // 窗口过去之后，点击照常打开线程。
    vi.setSystemTime(Date.now() + 400);
    await card(wrapper, 'tsk-aaa001').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#dock').exists()).toBe(true);
  });
});

/* ─── 七、叶子挂载：`TaskBoard` 的默认档与开关 ──────────────────────────── */

describe('TaskBoard 叶子契约', () => {
  test('默认档是看板（旧页面 `state.taskView` 初值 `board`）', async () => {
    const stub = createFetchStub({ '/api/tasks': () => jsonResponse({ tasks: threeTasks() }) });
    globalThis.fetch = stub;
    const board = createTaskBoardStore({ ownerId: () => 'owner' });
    const wrapper = mount(TaskBoard, {
      props: {
        board,
        state: { online: true, members: [OWNER, ADA], channels: [channel(), channel({ id: 'ch-2', name: 'ops' })] },
      },
      attachTo: document.body,
    });
    await flush();
    expect(wrapper.find('[data-od-id="taskview-board"]').classes()).toContain('is-active');
    expect(wrapper.findAll('[data-od-id^="task-column-"]').length).toBe(5);
    wrapper.unmount();
  });
});
