/* rail 头的两个弹窗（提醒 / 设置）——**从 `App.vue` 进**的接线用例

   为什么从真实父组件进（而不是直挂 `RailSidebar` / `RemindersModal`）：rail 的圆点判据需要
   整份「后端读数 → store → 组件 prop」这条链，设置的保存需要 `createAppContext()` 的
   `applySettings`（它是唯一写入口）。阶段一漏过 `DockPanel` 的 `pools` 传递，就是在叶子组件上
   各有各的 prop、各自都过——只有从真实父组件进才看得见。

   装置照 `app-shell.test.js`：`fetch` 替身换 `globalThis.fetch`，事件源用 FakeEventSource，
   挂到 `document.body`（ModalShell 的焦点与 ESC 都需要真实 DOM 落点）。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const { localDateString } = await import('../src/lib/format.js');
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

/** 一条已到点的提醒（`fired_at` 非空 ⇒ 不等待任何事）。 */
function firedReminder() {
  return {
    id: 'rm-fired',
    text: '已经响过的',
    time: '2026-10-01 09:00',
    channel_id: 'ch-1',
    channel_name: 'design',
    fired_at: '2026-10-01T09:00:00Z',
    state: 'fired',
  };
}

/** 一条已错过的提醒（后端派生的只读取值）。 */
function missedReminder() {
  return {
    id: 'rm-missed',
    text: '错过的',
    time: '2026-10-02 09:00',
    channel_id: null,
    channel_name: null,
    fired_at: '2026-10-02T09:00:00Z',
    state: 'missed',
  };
}

/** 一条还没到点的提醒（`fired_at == null` ⇒ 圆点该亮）。 */
function pendingReminder(overrides) {
  return Object.assign(
    {
      id: 'rm-pending',
      text: '还没到点的',
      time: '2030-01-01 09:00',
      channel_id: 'ch-1',
      channel_name: 'design',
      fired_at: null,
      state: 'pending',
    },
    overrides || {},
  );
}

/** 替身收到的请求体是 JSON 串（`createFetchStub` 原样转交 `init`），这里按后端那条路径解析。 */
function bodyOf(request) {
  return request && request.body ? JSON.parse(request.body) : {};
}

/** 后端侧的可变状态：POST / DELETE 都用它算「最新提醒表」，于是响应与正本一致。 */
function createServer(seed) {
  const server = {
    reminders: (seed || []).map((item) => Object.assign({}, item)),
    settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' },
    nextId: 1,
  };

  server.routes = {
    '/api/settings': (url, request) => {
      if (request.method === 'PATCH') {
        Object.assign(server.settings, bodyOf(request));
      }
      return jsonResponse({ settings: Object.assign({}, server.settings) });
    },
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
    '/api/reminders': (url, request) => {
      if (request.method === 'POST') {
        const body = bodyOf(request);
        const created = {
          id: 'rm-' + server.nextId++,
          text: body.text,
          time: body.time,
          channel_id: body.channel_id || null,
          channel_name: body.channel_id ? 'design' : null,
          fired_at: null,
          state: 'pending',
        };
        server.reminders.push(created);
        return jsonResponse({ reminder: created, reminders: server.reminders.slice() }, 201);
      }
      return jsonResponse({ reminders: server.reminders.slice() });
    },
    '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
    '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/channels/ch-1/config': () =>
      jsonResponse({ config: { channel_id: 'ch-1', mode: 'autonomous', team_leader_member_id: null } }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
  };

  return server;
}

/** 本机「今天」的 `YYYY-MM-DD`（日期格的缺省值）。 */
function todayValue() {
  return localDateString(new Date());
}

const mounted = [];

async function mountApp(server) {
  const host = server || createServer([]);
  // 删除带 id 的提醒：`/api/reminders/<id>` 这类路径按前缀分派（替身只做字符串匹配）。
  const stub = createFetchStub(host.routes);
  const forward = stub;
  globalThis.fetch = async (url, init) => {
    const path = String(url);
    const request = init || {};
    if (request.method === 'DELETE' && path.startsWith('/api/reminders/')) {
      forward.calls.push({ url: path, method: 'DELETE', body: null });
      const id = decodeURIComponent(path.slice('/api/reminders/'.length));
      const index = host.reminders.findIndex((item) => item.id === id);
      const removed = index >= 0 ? host.reminders.splice(index, 1)[0] : null;
      return jsonResponse({ reminder: removed, reminders: host.reminders.slice() });
    }
    return forward(path, init);
  };
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  const wrapper = mount(App, {
    props: { sourceFactory: () => FakeEventSource },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  await flush();
  await flush();
  return { wrapper, server: host, fetchStub: forward };
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  localStorage.clear();
  document.documentElement.dataset.theme = 'system';
  document.documentElement.dataset.density = 'comfortable';
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

/* ─── 一、rail 头的两个入口 ─────────────────────────────────────────────── */

describe('rail 头的弹窗入口', () => {
  test('rail 头渲染 btn-reminders 与 btn-settings（未迁前这两块整块不存在）', async () => {
    const { wrapper } = await mountApp(createServer([]));
    const head = wrapper.find('#rail .rail-head');
    expect(head.find('[data-od-id="btn-reminders"]').exists()).toBe(true);
    expect(head.find('[data-od-id="btn-settings"]').exists()).toBe(true);
    expect(head.find('[data-od-id="btn-reminders"]').attributes('aria-label')).toBe('提醒');
    expect(head.find('[data-od-id="btn-settings"]').attributes('aria-label')).toBe('设置');
  });

  test('圆点：全部已提醒 / 已错过时**不出现**（圆点不是历史计数器）', async () => {
    const { wrapper } = await mountApp(createServer([firedReminder(), missedReminder()]));
    await flush();
    expect(wrapper.find('[data-od-id="btn-reminders"]').exists()).toBe(true);
    expect(wrapper.find('#reminderDot').exists()).toBe(false);
  });

  test('圆点：有一条未到点（`fired_at == null`）就出现', async () => {
    const { wrapper } = await mountApp(createServer([firedReminder(), pendingReminder()]));
    await flush();
    expect(wrapper.find('#reminderDot').exists()).toBe(true);
    expect(wrapper.find('#reminderDot').classes()).toContain('dot');
  });
});

/* ─── 二、提醒弹窗 ─────────────────────────────────────────────────────── */

describe('提醒弹窗', () => {
  test('点 rail 头的铃铛 ⇒ `.modal.wide` 打开，空态说自己的空', async () => {
    const { wrapper } = await mountApp(createServer([]));
    await wrapper.find('[data-od-id="btn-reminders"]').trigger('click');
    await flush();

    const overlay = wrapper.find('#remindersOverlay');
    expect(overlay.exists()).toBe(true);
    expect(overlay.attributes('hidden')).toBeUndefined();
    // 提醒是 `.modal.wide` 那一档（宽 560，旧页面 `index.html:836`）。
    expect(overlay.find('.modal').classes()).toContain('wide');
    expect(wrapper.find('[data-od-id="reminder-list"]').text()).toContain('还没有提醒');
  });

  test('列表行：文本 · 归属频道 · 状态后缀（只读后端的 state，前端不判定）', async () => {
    const { wrapper } = await mountApp(createServer([pendingReminder(), firedReminder(), missedReminder()]));
    await flush();
    await wrapper.find('[data-od-id="btn-reminders"]').trigger('click');
    await flush();

    const rows = wrapper.findAll('[data-reminder]');
    expect(rows.map((row) => row.attributes('data-reminder'))).toEqual(['rm-pending', 'rm-fired', 'rm-missed']);

    // 一行四件：文本（+ ` · #频道名`）/ 状态后缀 / 删除键。
    const pending = wrapper.find('[data-reminder="rm-pending"]');
    expect(pending.find('.grow').text()).toBe('还没到点的 · #design');
    // `pending` 的后缀就是到期时刻本身（完整后端串，不切片）。
    expect(pending.find('.state').text()).toBe('2030-01-01 09:00');
    expect(pending.find('.state').attributes('data-state')).toBe('pending');

    // 已提醒 / 已错过：后缀是词条文案，`data-state` 是后端原词。
    const fired = wrapper.find('[data-reminder="rm-fired"]');
    expect(fired.find('.state').text()).toBe('已提醒');
    expect(fired.find('.state').attributes('data-state')).toBe('fired');
    const missed = wrapper.find('[data-reminder="rm-missed"]');
    expect(missed.find('.state').text()).toBe('已错过');
    expect(missed.find('.state').attributes('data-state')).toBe('missed');
    // 不归属任何频道时 `· #…` 那一段不出现（可空列的正本事实）。
    expect(missed.find('.grow').text()).toBe('错过的');

    // 删除键：`×` + 可访问名（不用原型那个与可访问名矛盾的加号图标）。
    const remove = pending.find('[data-od-id="reminder-delete"]');
    expect(remove.text()).toBe('×');
    expect(remove.attributes('aria-label')).toBe('删除提醒');
  });

  test('新增：文本 + 日期 + 钟点拼成 `YYYY-MM-DD HH:MM`，列表按响应里的最新表重铺', async () => {
    const { wrapper, fetchStub } = await mountApp(createServer([]));
    await wrapper.find('[data-od-id="btn-reminders"]').trigger('click');
    await flush();

    // 日期格默认今天（`type="date"` 没有值就填不出「今天」）。
    const dateInput = wrapper.find('[data-od-id="reminder-date"]');
    expect(dateInput.attributes('type')).toBe('date');
    expect(dateInput.element.value).toBe(todayValue());

    await wrapper.find('[data-od-id="reminder-text"]').setValue('喝水');
    await dateInput.setValue('2031-03-04');
    await wrapper.find('[data-od-id="reminder-time"]').setValue('07:05');
    await wrapper.find('[data-od-id="reminder-add"]').trigger('click');
    await flush();
    await flush();

    const posted = fetchStub.calls.filter((call) => call.method === 'POST' && call.url === '/api/reminders');
    expect(posted).toHaveLength(1);
    expect(posted[0].body).toEqual({ text: '喝水', time: '2031-03-04 07:05', channel_id: 'ch-1' });

    const rows = wrapper.findAll('[data-reminder]');
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain('喝水');
    // 只清正文：日期回到今天、钟点保留当前取值（连设两条不用重填）。
    expect(wrapper.find('[data-od-id="reminder-text"]').element.value).toBe('');
    expect(wrapper.find('[data-od-id="reminder-date"]').element.value).toBe(todayValue());
    expect(wrapper.find('[data-od-id="reminder-time"]').element.value).toBe('07:05');
  });

  test('新增提醒**不产生消息**（`/api/channels/*/messages` 请求数不变）', async () => {
    const { wrapper, fetchStub } = await mountApp(createServer([]));
    await wrapper.find('[data-od-id="btn-reminders"]').trigger('click');
    await flush();
    const before = fetchStub.to('/messages').length;

    await wrapper.find('[data-od-id="reminder-text"]').setValue('不落消息');
    await wrapper.find('[data-od-id="reminder-date"]').setValue('2031-03-04');
    await wrapper.find('[data-od-id="reminder-time"]').setValue('08:00');
    await wrapper.find('[data-od-id="reminder-add"]').trigger('click');
    await flush();
    await flush();

    expect(fetchStub.to('/messages').length).toBe(before);
    // 也没有标读、没有新 target：整条写路径只碰提醒面。
    expect(fetchStub.to('/api/reminders').filter((call) => call.method === 'POST')).toHaveLength(1);
  });

  test('删除：列表按响应里的最新表重铺，圆点跟着灭', async () => {
    const { wrapper, fetchStub } = await mountApp(createServer([pendingReminder()]));
    await flush();
    expect(wrapper.find('#reminderDot').exists()).toBe(true);

    await wrapper.find('[data-od-id="btn-reminders"]').trigger('click');
    await flush();
    await wrapper.find('[data-reminder="rm-pending"] [data-od-id="reminder-delete"]').trigger('click');
    await flush();
    await flush();

    expect(fetchStub.calls.some((call) => call.method === 'DELETE' && call.url === '/api/reminders/rm-pending')).toBe(true);
    expect(wrapper.findAll('[data-reminder]')).toHaveLength(0);
    expect(wrapper.find('#reminderDot').exists()).toBe(false);
  });

  test('写失败时把后端的话留在弹窗里，镜像个字不改（不声称一个没落库的提醒）', async () => {
    const server = createServer([]);
    server.routes['/api/reminders'] = (url, request) => {
      if (request.method === 'POST') {
        return jsonResponse({ detail: '提醒时间形状不对' }, 422);
      }
      return jsonResponse({ reminders: server.reminders.slice() });
    };
    const { wrapper } = await mountApp(server);
    await wrapper.find('[data-od-id="btn-reminders"]').trigger('click');
    await flush();

    await wrapper.find('[data-od-id="reminder-text"]').setValue('形状不对');
    await wrapper.find('[data-od-id="reminder-add"]').trigger('click');
    await flush();
    await flush();

    const error = wrapper.find('#reminderFormError');
    expect(error.exists()).toBe(true);
    expect(error.attributes('hidden')).toBeUndefined();
    expect(error.text()).toBe('提醒未保存：提醒时间形状不对');
    expect(wrapper.findAll('[data-reminder]')).toHaveLength(0);
  });
});

/* ─── 三、设置弹窗（接活 `applySettings`） ──────────────────────────────── */

/**
 * 数一次动作里 `document.documentElement.dataset` 被写了几次。
 *
 * 判据是**行为级**的：`applySettings` 是设置落到根元素的唯一写入口，一次保存只该铺一次。
 * 那段「给 `mutable.settings` 挂 deep watcher」的自触发无限循环（页面白屏卡死）在外部可观察的
 * 样子正是：一次保存写了两次以上。因此这里数的是根元素被写了几次，不是函数被调了几次。
 */
function countRootWrites() {
  const root = document.documentElement;
  const real = root.dataset;
  const counter = { theme: 0, density: 0 };
  Object.defineProperty(root, 'dataset', {
    configurable: true,
    get() {
      return new Proxy(real, {
        set(target, key, value) {
          if (key === 'theme') counter.theme += 1;
          if (key === 'density') counter.density += 1;
          target[key] = value;
          return true;
        },
      });
    },
  });
  counter.restore = () => {
    delete root.dataset;
  };
  return counter;
}

describe('设置弹窗', () => {
  test('点 rail 头的齿轮 ⇒ 打开；三个档位用旧页面的类名与 data-od-id，当前档位带 is-on', async () => {
    const { wrapper } = await mountApp(createServer([]));
    await wrapper.find('[data-od-id="btn-settings"]').trigger('click');
    await flush();

    const overlay = wrapper.find('#settingsOverlay');
    expect(overlay.exists()).toBe(true);
    expect(overlay.attributes('hidden')).toBeUndefined();
    expect(overlay.find('.modal').classes()).not.toContain('wide');

    const locale = wrapper.find('[data-od-id="settings-locale"]');
    expect(locale.element.value).toBe('zh-CN');

    for (const id of ['settings-density', 'settings-theme']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(true);
    }
    for (const id of ['density-comfortable', 'density-compact', 'theme-light', 'theme-dark', 'theme-system', 'settings-done']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(true);
    }
    // 选中态：`is-on` + `aria-pressed`（旧 `renderSettingsModal()` 的两条落点）。
    const comfortable = wrapper.find('[data-od-id="density-comfortable"]');
    expect(comfortable.classes()).toContain('is-on');
    expect(comfortable.attributes('aria-pressed')).toBe('true');
    expect(wrapper.find('[data-od-id="theme-system"]').classes()).toContain('is-on');
    expect(wrapper.find('[data-od-id="theme-dark"]').attributes('aria-pressed')).toBe('false');
  });

  test('点「深色」⇒ PATCH 一档 + 根元素 data-theme 变 dark + 选中态跟着走（读 DOM，不读内部函数）', async () => {
    const { wrapper, fetchStub } = await mountApp(createServer([]));
    await wrapper.find('[data-od-id="btn-settings"]').trigger('click');
    await flush();

    await wrapper.find('[data-od-id="theme-dark"]').trigger('click');
    await flush();
    await flush();

    const patched = fetchStub.calls.filter((call) => call.method === 'PATCH' && call.url === '/api/settings');
    expect(patched).toHaveLength(1);
    expect(patched[0].body).toEqual({ theme: 'dark' });

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(wrapper.find('[data-od-id="theme-dark"]').classes()).toContain('is-on');
    expect(wrapper.find('[data-od-id="theme-dark"]').attributes('aria-pressed')).toBe('true');
    expect(wrapper.find('[data-od-id="theme-system"]').classes()).not.toContain('is-on');
    // 保存成功不关弹窗（与旧页面同：改一档接着改下一档）。
    expect(wrapper.find('#settingsOverlay').attributes('hidden')).toBeUndefined();
  });

  test('点「紧凑」⇒ 根元素 data-density 变 compact；切语言 ⇒ `<html lang>` 跟着变', async () => {
    const { wrapper, fetchStub } = await mountApp(createServer([]));
    await wrapper.find('[data-od-id="btn-settings"]').trigger('click');
    await flush();

    await wrapper.find('[data-od-id="density-compact"]').trigger('click');
    await flush();
    await flush();
    expect(document.documentElement.dataset.density).toBe('compact');

    await wrapper.find('[data-od-id="settings-locale"]').setValue('en');
    await flush();
    await flush();
    expect(document.documentElement.lang).toBe('en');
    // 语言档之后弹窗文案本身也跟着切（词条桥的响应式依赖）。
    expect(wrapper.find('[data-od-id="settings-done"]').text()).toBe('Done');

    // 回到源语言，后续用例不受影响（三次点击 = 三次 PATCH，没有额外的请求）。
    await wrapper.find('[data-od-id="settings-locale"]').setValue('zh-CN');
    await flush();
    expect(fetchStub.calls.filter((call) => call.method === 'PATCH')).toHaveLength(3);
  });

  test('保存一次只铺一次根元素（防回归：给 mutable.settings 挂 watcher 会自触发无限循环）', async () => {
    // 这条用例守的是阶段一实测踩到的缺陷：`watch(() => mutable.settings, applySettings, {deep:true})`
    // 让 `applySettings` 的回调里那次赋值再次触发自己 ⇒ 主线程转死、页面白屏（只有真浏览器暴露，
    // jsdom 里表现为同一个动作把根元素写了无数次）。判据因此写成「一次保存只有一次铺设」。
    // `applySettings` 是唯一写入口，删过的东西不准加回来。
    const { wrapper } = await mountApp(createServer([]));
    await wrapper.find('[data-od-id="btn-settings"]').trigger('click');
    await flush();

    const counter = countRootWrites();
    try {
      await wrapper.find('[data-od-id="theme-dark"]').trigger('click');
      await flush();
      await flush();
      await flush();

      expect(document.documentElement.dataset.theme).toBe('dark');
      expect(counter.theme).toBe(1);
      expect(counter.density).toBe(1);
    } finally {
      counter.restore();
    }
  }, 3000);

  test('保存失败：把后端的话留在弹窗里、根元素不动（不声称一个没落库的档位）', async () => {
    const server = createServer([]);
    server.routes['/api/settings'] = (url, request) => {
      if (request.method === 'PATCH') return jsonResponse({ detail: '档位不合法' }, 422);
      return jsonResponse({ settings: Object.assign({}, server.settings) });
    };
    const { wrapper } = await mountApp(server);
    await wrapper.find('[data-od-id="btn-settings"]').trigger('click');
    await flush();

    await wrapper.find('[data-od-id="theme-dark"]').trigger('click');
    await flush();
    await flush();

    const error = wrapper.find('#settingsFormError');
    expect(error.exists()).toBe(true);
    expect(error.attributes('hidden')).toBeUndefined();
    expect(error.text()).toBe('设置未保存：档位不合法');
    expect(document.documentElement.dataset.theme).toBe('system');
    expect(wrapper.find('[data-od-id="theme-system"]').classes()).toContain('is-on');
    expect(wrapper.find('#settingsOverlay').attributes('hidden')).toBeUndefined();
  });
});
