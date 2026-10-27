/* 搜索主线视图 + ⌘K 命令面板——**从 `App.vue` 进**的接线用例

   为什么从真实父组件进（而不是直挂 `SearchView` / `CommandPalette`）：`mode` 是主线视图的维度
   （`'channel' | 'search'`），它决定消息流 / composer / 频道头让位、决定 ESC 落在哪一层；命令面板
   的 `toggle-dock` 判据与频道头的运行面板入口同一份（私信下不成立），命令集还要读 rail 的频道名单。
   这三件事都由 App 裁决——直挂叶子组件只能证明「自己长对了」，证明不了接线（阶段一实测漏过
   `DockPanel` 的 `pools` 传递，就是只看叶子组件的代价）。

   装置照 `app-shell.test.js`：`fetch` 替身换 `globalThis.fetch`，事件源用 FakeEventSource，
   挂到 `document.body`（ESC / `document.activeElement` / `hidden` 语义都需要真实 DOM 落点）。
   搜索请求按前缀分派，于是「发了几次请求、带的是哪一档 facet」可以被断言（票面验收项直接读它）。 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

class FakeEventSource {
  constructor(url) {
    this.url = String(url);
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  close() {}
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

/** 消息命中：可呈现文本是消息正文，高亮区间由后端给（前端只按区间包标签）。 */
function messageHit(overrides) {
  return Object.assign(
    {
      kind: 'message',
      text: '设计 里的 设计',
      highlight_ranges: [{ start: 0, end: 2 }],
      target: { id: 'ch-1', kind: 'channel', display_name: 'design' },
      message: { id: 'msg-1', seq: 3, body: '设计 里的 设计', author: { display_name: 'Ada' } },
    },
    overrides || {},
  );
}

function taskHit() {
  return {
    kind: 'task',
    text: '设计任务',
    highlight_ranges: [{ start: 0, end: 2 }],
    target: { id: 'ch-1', kind: 'channel', display_name: 'design' },
    task: { id: 'tsk-abcdef', status: 'todo', title: '设计任务', channel_id: 'ch-1' },
  };
}

function agentHit() {
  return {
    kind: 'agent',
    text: 'Ada · 设计师',
    highlight_ranges: [{ start: 0, end: 3 }],
    target: null,
    agent: { id: 'mem-1', display_name: 'Ada', role: '设计师', model_id: 'ollama:qwen-max', status: 'offline' },
  };
}

/** 后端替身：`/api/search?` 走 `server.responder`，于是「请求次数与 facet」都可读。 */
function createServer(seed) {
  const config = seed || {};
  const server = {
    searchCalls: [],
    responder: config.responder || (() => jsonResponse({ hits: [] })),
    channels: config.channels || [channel()],
    dms: config.dms || [],
  };
  server.routes = {
    '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
    '/api/workspace': () =>
      jsonResponse({
        member: OWNER,
        members: [OWNER, ADA],
        channels: server.channels,
        dms: server.dms,
        agents: [ADA],
        model_providers: [],
        local_providers: [],
        remote_consents: [],
      }),
    '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
    '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/channels/ch-1/config': () =>
      jsonResponse({ config: { channel_id: 'ch-1', mode: 'autonomous', team_leader_member_id: null } }),
    '/api/channels/ch-2/messages': () => jsonResponse({ channel: channel({ id: 'ch-2', name: 'ops' }), messages: [] }),
    '/api/channels/ch-2/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-2/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/channels/ch-2/config': () =>
      jsonResponse({ config: { channel_id: 'ch-2', mode: 'autonomous', team_leader_member_id: null } }),
    '/api/dms/dm-1/messages': () => jsonResponse({ dm: dm(), messages: [] }),
    '/api/dms/dm-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
    '/api/targets/ch-1/team-runs?limit=20': () => jsonResponse({ runs: [] }),
    '/api/targets/ch-1/plan': () => jsonResponse({ plan: { members: [] } }),
  };
  return server;
}

const mounted = [];

async function mountApp(server) {
  const host = server || createServer();
  const stub = createFetchStub(host.routes);
  globalThis.fetch = async (url, init) => {
    const path = String(url);
    if (path.startsWith('/api/search?')) {
      const params = new URLSearchParams(path.slice('/api/search?'.length));
      const call = { url: path, q: params.get('q'), facet: params.get('facet') };
      host.searchCalls.push(call);
      return host.responder(call, host.searchCalls.length);
    }
    return stub(path, init);
  };
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  const wrapper = mount(App, {
    props: { sourceFactory: () => FakeEventSource },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  await flush();
  await flush();
  return { wrapper, server: host, fetchStub: stub };
}

/**
 * 真实键盘事件：从**当前焦点元素**冒泡到 `document`（浏览器里 keydown 就是这个走向，
 * `event.target` 也确实是那个输入框——`M` / `T` 的「人在输入框里就不生效」判据读的正是它）。
 * 焦点不在任何元素上时落在 `document` 自己身上。
 */
function press(key, options) {
  const target = document.activeElement && document.activeElement !== document.body
    ? document.activeElement
    : document;
  target.dispatchEvent(new KeyboardEvent('keydown', Object.assign({ key, bubbles: true }, options || {})));
  return flush();
}

/** 打开搜索视图：点 rail 的搜索入口（本票的唯一产品入口）。 */
async function openSearch(wrapper) {
  await wrapper.find('[data-od-id="btn-workspace-search"]').trigger('click');
  await flush();
}

function searchInput(wrapper) {
  return wrapper.find('[data-od-id="search-input"]');
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

/* ─── 一、搜索是主线视图（rail 入口 + 三档空态） ────────────────────────── */

describe('搜索视图是主线视图', () => {
  test('rail 有 btn-workspace-search；点击后主线进入搜索视图，消息流 / composer / 频道头让位', async () => {
    const { wrapper } = await mountApp();
    const entry = wrapper.find('[data-od-id="btn-workspace-search"]');
    expect(entry.exists()).toBe(true);
    expect(entry.attributes('title')).toBe('搜索工作台');

    await openSearch(wrapper);

    const view = wrapper.find('[data-od-id="search-view"]');
    expect(view.exists()).toBe(true);
    expect(view.isVisible()).toBe(true);
    // 搜索占住消息流那块位置（旧 `syncChromeVisibility()`：`mode === 'search'` 时三者都让位）。
    expect(wrapper.find('[data-od-id="messages-view"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-od-id="composer"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-od-id="channel-header"]').isVisible()).toBe(false);
    // 焦点进搜索框（旧 `showSearchView()` 最后一行）。
    expect(document.activeElement).toBe(searchInput(wrapper).element);
  });

  test('未输入：`搜索整个工作区` 空态，且不发请求', async () => {
    const { wrapper, server } = await mountApp();
    await openSearch(wrapper);
    const results = wrapper.find('[data-od-id="search-results"]');
    expect(results.find('.empty b').text()).toBe('搜索整个工作区');
    expect(results.find('.empty p').text()).toBe('消息不可编辑、不可删除；搜索覆盖消息、任务与 Agent。');
    expect(server.searchCalls).toHaveLength(0);
  });

  test('零命中：`没有找到“{query}”` 空态可见，且没有错误话', async () => {
    const { wrapper, server } = await mountApp(createServer({ responder: () => jsonResponse({ hits: [] }) }));
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('找不到的词');
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(server.searchCalls).toHaveLength(1);
    const results = wrapper.find('[data-od-id="search-results"]');
    expect(results.find('.empty b').text()).toBe('没有找到“找不到的词”');
    expect(results.find('.empty p').text()).toBe('换个关键词，或检查拼写。');
    expect(wrapper.find('#searchError').attributes('hidden')).toBeDefined();
  });

  test('出错 ≠ 零命中：错误话留在结果面里，零命中空态不出现', async () => {
    const servers = createServer({
      responder: () => jsonResponse({ error: { message: '库忙' } }, 503),
    });
    const { wrapper } = await mountApp(servers);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await new Promise((resolve) => setTimeout(resolve, 200));

    const error = wrapper.find('#searchError');
    expect(error.attributes('hidden')).toBeUndefined();
    expect(error.text()).toContain('搜索失败');
    expect(error.text()).toContain('库忙');
    // 结果面不再铺「没有找到」——那会把一次失败说成一次零命中。
    expect(wrapper.find('[data-od-id="search-results"] .empty').exists()).toBe(false);
  });
});

/* ─── 二、facet 与防抖：都是「换一次请求」这件事的两面 ─────────────────── */

describe('facet 是后端的过滤参数', () => {
  test('切档位 ⇒ 发一次**新请求**（带新 facet），前端不对已取回的命中集再过滤', async () => {
    // 后端对每一档都回同一批命中：如果前端自己过滤，切到 `agent` 档后「消息命中」会凭空消失。
    const server = createServer({
      responder: () => jsonResponse({ hits: [messageHit(), taskHit(), agentHit()] }),
    });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();
    expect(server.searchCalls).toEqual([
      { url: '/api/search?q=%E8%AE%BE%E8%AE%A1&facet=all', q: '设计', facet: 'all' },
    ]);

    await wrapper.find('[data-facet="agent"]').trigger('click');
    await flush();
    // 请求次数 1 → 2，且第二发带的是新档位（**不是**在前端把 3 条筛成 1 条）。
    expect(server.searchCalls).toHaveLength(2);
    expect(server.searchCalls[1].facet).toBe('agent');
    expect(server.searchCalls[1].q).toBe('设计');
    // 选中态跟着换（旧 `renderSearchFacets()`：`.is-on` + `aria-pressed` 同进同出）。
    expect(wrapper.find('[data-facet="agent"]').classes()).toContain('is-on');
    expect(wrapper.find('[data-facet="agent"]').attributes('aria-pressed')).toBe('true');
    expect(wrapper.find('[data-facet="all"]').classes()).not.toContain('is-on');
    // 后端仍回三条 ⇒ 界面仍显示三条（前端不按 kind 再过滤一遍）。
    expect(wrapper.findAll('[data-od-id="search-results"] .result')).toHaveLength(3);
  });

  test('点同一个档位不发请求（选中态没变就没有「换一次」可言）', async () => {
    const server = createServer({ responder: () => jsonResponse({ hits: [] }) });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();
    expect(server.searchCalls).toHaveLength(1);

    await wrapper.find('[data-facet="all"]').trigger('click');
    await flush();
    expect(server.searchCalls).toHaveLength(1);
  });

  test('查询词为空时切档位只换选中态、不发请求', async () => {
    const server = createServer({ responder: () => jsonResponse({ hits: [] }) });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await wrapper.find('[data-facet="task"]').trigger('click');
    await flush();
    expect(server.searchCalls).toHaveLength(0);
    expect(wrapper.find('[data-facet="task"]').classes()).toContain('is-on');
  });
});

describe('120ms 防抖', () => {
  test('连打 5 个字符只发 1 次请求（防抖，不是每键一问）', async () => {
    const server = createServer({ responder: () => jsonResponse({ hits: [] }) });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    // 假时钟在装配之后才接管：`flush()` 自己就靠 `setTimeout(0)` 推进，装得太早会一起冻住。
    vi.useFakeTimers();
    try {
      const input = searchInput(wrapper);
      for (const text of ['设', '设计', '设计视', '设计视图', '设计视图好']) {
        await input.setValue(text);
        await vi.advanceTimersByTimeAsync(20);
      }
      // 还没到 120ms 的一拍：一次请求都没发。
      expect(server.searchCalls).toHaveLength(0);
      await vi.advanceTimersByTimeAsync(150);
      expect(server.searchCalls).toHaveLength(1);
      expect(server.searchCalls[0].q).toBe('设计视图好');
    } finally {
      vi.useRealTimers();
    }
  });

  test('Enter 立即执行，不等那一拍（挂起的防抖被取消 ⇒ 仍然只有 1 次请求）', async () => {
    const server = createServer({ responder: () => jsonResponse({ hits: [] }) });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    vi.useFakeTimers();
    try {
      await searchInput(wrapper).setValue('设计');
      await vi.advanceTimersByTimeAsync(20);
      expect(server.searchCalls).toHaveLength(0);

      await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
      await vi.advanceTimersByTimeAsync(0);
      expect(server.searchCalls).toHaveLength(1);

      // 再等过防抖窗口：那一拍已经被取消，不会补发第二次。
      await vi.advanceTimersByTimeAsync(200);
      expect(server.searchCalls).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

/* ─── 三、命中渲染：高亮只按后端区间，正文不重找 ───────────────────────── */

describe('命中行', () => {
  test('三类命中各按后端的 `highlight_ranges` 包 `<mark>`，正文另外的位置不高亮', async () => {
    const server = createServer({
      responder: () => jsonResponse({ hits: [messageHit(), taskHit(), agentHit()] }),
    });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();

    const results = wrapper.findAll('[data-od-id="search-results"] .result');
    expect(results).toHaveLength(3);
    // 后端只给了 [0,2) 一段：第二处「设计」不高亮（前端不去正文里找第二处）。
    expect(results[0].find('.r-title').text()).toBe('设计 里的 设计');
    expect(results[0].findAll('.r-title mark').map((node) => node.text())).toEqual(['设计']);
    expect(results[0].find('.r-top .hash').text()).toBe('频道 · #design');
    expect(results[0].find('.r-top .card-tag').text()).toBe('消息');
    expect(results[0].find('.r-snip').text()).toBe('来自 Ada');
    // 任务命中：`.r-snip` 是「#序号 · 状态」；Agent 命中：来源是集合名、片段是状态与模型。
    expect(results[1].find('.r-snip').text()).toBe('#abcdef · 待认领');
    expect(results[2].find('.r-top .hash').text()).toBe('成员');
    // Agent 的状态词走 `status.*` 词条（`offline` ⇒「未启用」），不认识的状态才退回后端原词。
    expect(results[2].find('.r-snip').text()).toBe('未启用 · ollama:qwen-max');
  });

  test('非法的后端区间被跳过，文本原样呈现（不修正、不重排）', async () => {
    const server = createServer({
      responder: () =>
        jsonResponse({
          hits: [
            messageHit({
              text: 'abcd',
              highlight_ranges: [
                { start: 2, end: 1 },
                { start: 1, end: 99 },
                'bad',
                { start: 3, end: 4 },
              ],
            }),
          ],
        }),
    });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('d');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();
    const result = wrapper.find('[data-od-id="search-results"] .result');
    expect(result.find('.r-title').text()).toBe('abcd');
    expect(result.findAll('.r-title mark').map((node) => node.text())).toEqual(['d']);
  });

  test('命中行不是按钮：任务线程与 Agent 详情都没迁，不做「点了没反应」的行', async () => {
    const server = createServer({ responder: () => jsonResponse({ hits: [taskHit(), agentHit()] }) });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();
    const results = wrapper.findAll('[data-od-id="search-results"] .result');
    expect(results).toHaveLength(2);
    results.forEach((row) => {
      expect(row.element.tagName).toBe('DIV');
      expect(row.attributes('data-opentask')).toBeUndefined();
    });
  });
});

/* ─── 四、断线与版本守卫 ───────────────────────────────────────────────── */

describe('断线与请求版本守卫', () => {
  test('断线时打的是 `search.offline`，不发请求', async () => {
    const server = createServer();
    const { wrapper } = await mountApp(server);
    // 先让页面认为后端不可达（全局流断掉时 store 会置假）。
    const app = wrapper.vm;
    app.$.setupState.store.mutable.online = false;
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();
    expect(server.searchCalls).toHaveLength(0);
    const error = wrapper.find('#searchError');
    expect(error.attributes('hidden')).toBeUndefined();
    expect(error.text()).toBe('后端不可达：无法执行搜索。');
  });

  test('晚到的旧响应不覆盖新结果（版本守卫）', async () => {
    // 第一发挂住不返回，第二发（facet 切换）立刻回一批新命中；第一发之后才 resolve。
    let releaseFirst = null;
    const server = createServer();
    server.responder = (call, count) => {
      if (count === 1) {
        return new Promise((resolve) => {
          releaseFirst = () => resolve(jsonResponse({ hits: [messageHit({ text: '旧的命中' })] }));
        });
      }
      return jsonResponse({ hits: [taskHit()] });
    };
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();
    expect(server.searchCalls).toHaveLength(1);

    // 第二发（切档位）先落地。
    await wrapper.find('[data-facet="task"]').trigger('click');
    await flush();
    expect(server.searchCalls).toHaveLength(2);
    expect(wrapper.findAll('[data-od-id="search-results"] .result')).toHaveLength(1);
    expect(wrapper.find('[data-od-id="search-results"] .r-title').text()).toBe('设计任务');

    // 第一发这时才返回：它的结果是**旧请求**的，不该覆盖已经落地的新结果。
    releaseFirst();
    await flush();
    await flush();
    expect(wrapper.findAll('[data-od-id="search-results"] .result')).toHaveLength(1);
    expect(wrapper.find('[data-od-id="search-results"] .r-title').text()).toBe('设计任务');
  });

  test('晚到的旧响应里的**错误**同样不落地（不能把已经拿到的结果换成一条旧错误）', async () => {
    let rejectFirst = null;
    const server = createServer();
    server.responder = (call, count) => {
      if (count === 1) {
        return new Promise((resolve, reject) => {
          rejectFirst = () => reject(new Error('旧请求超时'));
        });
      }
      return jsonResponse({ hits: [taskHit()] });
    };
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();

    await wrapper.find('[data-facet="task"]').trigger('click');
    await flush();
    rejectFirst();
    await flush();
    await flush();
    expect(wrapper.find('#searchError').attributes('hidden')).toBeDefined();
    expect(wrapper.findAll('[data-od-id="search-results"] .result')).toHaveLength(1);
  });
});

/* ─── 五、⌘K 命令面板 ─────────────────────────────────────────────────── */

describe('⌘K 命令面板', () => {
  test('⌘K 打开面板、ESC 关闭；面板里只有 M / T 两枚 kbd（N / ⇧N / D 不出现）', async () => {
    const { wrapper } = await mountApp();
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(false);

    await press('k', { metaKey: true });
    const list = wrapper.find('[data-od-id="cmd-list"]');
    expect(list.exists()).toBe(true);
    expect(wrapper.find('[data-od-id="cmd-input"]').exists()).toBe(true);
    // 焦点进筛选框（旧 `openCmd()` 最后一行）。
    expect(document.activeElement).toBe(wrapper.find('[data-od-id="cmd-input"]').element);

    const kbds = list.findAll('.cmd-item .kbd').map((node) => node.text());
    expect(kbds).toEqual(['M', 'T']);

    await press('Escape');
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(false);
  });

  test('Ctrl+K 与 ⌘K 同效（非 macOS 的绑定）', async () => {
    const { wrapper } = await mountApp();
    await press('k', { ctrlKey: true });
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(true);
  });

  test('命令集：跳转 + 动作 + 动态频道组（全部指向已迁面）', async () => {
    const server = createServer({ channels: [channel(), channel({ id: 'ch-2', name: 'ops' })] });
    const { wrapper } = await mountApp(server);
    await press('k', { metaKey: true });

    const ids = wrapper.findAll('[data-od-id="cmd-list"] [data-cmd]').map((node) => node.attributes('data-cmd'));
    // 稳定 id 做归属（频道组是动态的，`channel:<id>` 而不是频道名）。
    expect(ids).toEqual([
      'view-messages',
      'view-board',
      'new-channel',
      'new-task',
      'toggle-dock',
      'channel:ch-1',
      'channel:ch-2',
    ]);
    // 分组标题按词条：跳转 / 动作 / 频道。
    const groups = wrapper.findAll('[data-od-id="cmd-list"] .cmd-group').map((node) => node.text());
    expect(groups).toEqual(['跳转', '动作', '频道']);

    // 任务看板随票 #306 迁完、新建任务随票 #307 迁完 ⇒ 两条都是**真入口**，
    // 命令表里现在没有禁用条目（`runFirst()` 「跳过禁用」的兜底仍在，暂无活例）。
    const board = wrapper.find('[data-cmd="view-board"]');
    expect(board.attributes('disabled')).toBeUndefined();
    expect(board.attributes('title')).toBeUndefined();
    const newTask = wrapper.find('[data-cmd="new-task"]');
    expect(newTask.attributes('disabled')).toBeUndefined();
    expect(newTask.attributes('title')).toBeUndefined();
    for (const id of ['view-messages', 'new-channel', 'toggle-dock']) {
      expect(wrapper.find(`[data-cmd="${id}"]`).attributes('disabled'), id).toBeUndefined();
    }
  });

  test('私信下 `toggle-dock` 那条命令不列（与频道头运行面板入口同一份判据）', async () => {
    const { wrapper } = await mountApp(createServer({ dms: [dm()] }));
    // 先进私信：`dockToggleAvailable` 看的是当前 target。
    await wrapper.find('[data-od-id="dm-dm-1"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#chanTitle').text()).toBe('@Ada');

    await press('k', { metaKey: true });
    const ids = wrapper.findAll('[data-od-id="cmd-list"] [data-cmd]').map((node) => node.attributes('data-cmd'));
    expect(ids).not.toContain('toggle-dock');
    // 其余命令照旧（是「这一条不列」，不是整块消失）。
    expect(ids).toContain('view-messages');
    expect(ids).toContain('channel:ch-1');
  });

  test('筛选是子串（大小写不敏感）；无匹配时给 `无匹配命令`', async () => {
    const { wrapper } = await mountApp();
    await press('k', { metaKey: true });
    await wrapper.find('[data-od-id="cmd-input"]').setValue('频道');
    await flush();
    // 「新建频道」与频道组的两条都留着，跳转那两条被筛掉。
    const ids = wrapper.findAll('[data-od-id="cmd-list"] [data-cmd]').map((node) => node.attributes('data-cmd'));
    expect(ids).toContain('new-channel');
    expect(ids).not.toContain('view-messages');

    await wrapper.find('[data-od-id="cmd-input"]').setValue('zzz');
    await flush();
    expect(wrapper.findAll('[data-od-id="cmd-list"] [data-cmd]')).toHaveLength(0);
    expect(wrapper.find('[data-od-id="cmd-list"] .cmd-group').text()).toBe('无匹配命令');
  });

  test('跑一条命令：面板先关，再动作（`切换详情面板` 开 dock；再跑一次收起）', async () => {
    const { wrapper } = await mountApp();
    await press('k', { metaKey: true });
    await wrapper.find('[data-cmd="toggle-dock"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(false);
    expect(wrapper.find('#dock').exists()).toBe(true);

    await press('k', { metaKey: true });
    await wrapper.find('[data-cmd="toggle-dock"]').trigger('click');
    await flush();
    expect(wrapper.find('#dock').exists()).toBe(false);
  });

  test('`消息视图` 命令把搜索视图退回消息流', async () => {
    const { wrapper } = await mountApp();
    await openSearch(wrapper);
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(true);

    await press('k', { metaKey: true });
    await wrapper.find('[data-cmd="view-messages"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-od-id="messages-view"]').isVisible()).toBe(true);
  });

  test('Enter 跑筛选后的第一条命令，跑完面板关掉', async () => {
    const { wrapper } = await mountApp();
    await press('k', { metaKey: true });
    // 筛到只剩「新建任务」（票 #307 已迁完 ⇒ 它是**可执行**的条目）：回车要真跑它，
    // 而不是留在一个「按了没反应」的面板上。
    await wrapper.find('[data-od-id="cmd-input"]').setValue('新建任务');
    await flush();
    await wrapper.find('[data-od-id="cmd-input"]').trigger('keydown', { key: 'Enter' });
    await flush();
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(false);
    expect(wrapper.find('#taskOverlay').element.hidden).toBe(false);

    // 关掉弹窗，再看跳转那一组：回车同样跑第一条匹配项并关面板。
    await wrapper.find('#taskModalClose').trigger('click');
    await flush();
    await press('k', { metaKey: true });
    await wrapper.find('[data-od-id="cmd-input"]').setValue('消息视图');
    await flush();
    await wrapper.find('[data-od-id="cmd-input"]').trigger('keydown', { key: 'Enter' });
    await flush();
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(false);
  });

  test('点遮罩空白处关闭；点面板内部不关', async () => {
    const { wrapper } = await mountApp();
    await press('k', { metaKey: true });
    await wrapper.find('#cmdList').trigger('click');
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(true);

    await wrapper.find('#cmdOverlay').trigger('click');
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(false);
  });
});

/* ─── 六、键位层：ESC 的层序与 M / T ─────────────────────────────────── */

describe('键位层', () => {
  test('ESC 层序：命令面板 > 弹窗 > 搜索视图（只收一层）', async () => {
    const { wrapper } = await mountApp();
    await openSearch(wrapper);
    await press('k', { metaKey: true });
    // 面板盖在搜索视图上：第一下 ESC 只关面板，搜索视图留着。
    await press('Escape');
    expect(wrapper.find('[data-od-id="cmd-list"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(true);

    // 第二下才轮到搜索视图（它是最后一层）。
    await press('Escape');
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-od-id="messages-view"]').isVisible()).toBe(true);
  });

  test('ESC 层序：开着弹窗时 ESC 只收弹窗，搜索视图不动', async () => {
    const { wrapper } = await mountApp();
    await openSearch(wrapper);
    await wrapper.find('[data-od-id="btn-settings"]').trigger('click');
    await flush();
    expect(wrapper.find('#settingsOverlay').attributes('hidden')).toBeUndefined();

    await press('Escape');
    expect(wrapper.find('#settingsOverlay').attributes('hidden')).toBeDefined();
    // 搜索视图是**最后一层**：弹窗那一格吃掉这一下之后就不再往下走。
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(true);
  });

  test('`M` 从搜索视图退回消息流；在输入框里按 `M` 不生效', async () => {
    const { wrapper } = await mountApp();
    await openSearch(wrapper);
    // 焦点在搜索框里（`showSearchView()` 送的）：这时按 `M` 应该只是打字。
    await press('m');
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(true);

    // 焦点离开输入框（人点了别处）之后 `M` 才生效。
    document.activeElement.blur();
    expect(document.activeElement.matches('input,textarea')).toBe(false);
    await press('m');
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-od-id="messages-view"]').isVisible()).toBe(true);
  });
});

/* ─── 七、`mode` 的归属与它的出口 ─────────────────────────────────────── */

describe('`mode` 这条新维度的出口', () => {
  test('换 target 时退出搜索视图（选一个 target = 回到消息流，旧 `selectTarget()` 同一条）', async () => {
    const server = createServer({ channels: [channel(), channel({ id: 'ch-2', name: 'ops' })] });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(true);

    await wrapper.find('[data-od-id="channel-ch-2"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(false);
    expect(wrapper.find('[data-od-id="messages-view"]').isVisible()).toBe(true);
    expect(wrapper.find('#chanTitle').text()).toBe('ops');
  });

  test('点当前那一条 target 也退出搜索视图（旧页面没有「同 target 早退」）', async () => {
    const { wrapper } = await mountApp();
    await openSearch(wrapper);
    await wrapper.find('[data-od-id="channel-ch-1"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(false);
  });

  test('搜索的查询词与命中跨视图保留（回到搜索还在），命令面板的「消息视图」不清它', async () => {
    const server = createServer({ responder: () => jsonResponse({ hits: [taskHit()] }) });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    await searchInput(wrapper).setValue('设计');
    await searchInput(wrapper).trigger('keydown', { key: 'Enter' });
    await flush();
    expect(wrapper.findAll('[data-od-id="search-results"] .result')).toHaveLength(1);

    await press('Escape');
    expect(wrapper.find('[data-od-id="search-view"]').isVisible()).toBe(false);

    await openSearch(wrapper);
    expect(searchInput(wrapper).element.value).toBe('设计');
    expect(wrapper.findAll('[data-od-id="search-results"] .result')).toHaveLength(1);
    // 回到搜索不再重发一次请求（命中还在镜像里）：发请求是「人改了什么」的结果，不是「看回来」。
    expect(server.searchCalls).toHaveLength(1);
  });

  test('离开搜索视图后，挂起的那一拍不再发请求（人已经在看消息流了）', async () => {
    const server = createServer({ responder: () => jsonResponse({ hits: [] }) });
    const { wrapper } = await mountApp(server);
    await openSearch(wrapper);
    vi.useFakeTimers();
    try {
      await searchInput(wrapper).setValue('设计');
      await vi.advanceTimersByTimeAsync(20);
      expect(server.searchCalls).toHaveLength(0);

      // `press()` 内部靠 `flush()`（真 `setTimeout`）推进，假时钟下会一起冻住——这里直接派发。
      document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await vi.advanceTimersByTimeAsync(300);
      // 防抖那一拍被取消了：离开之后不该凭空多一次搜索请求。
      expect(server.searchCalls).toHaveLength(0);
      expect(wrapper.find('[data-od-id="messages-view"]').isVisible()).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
