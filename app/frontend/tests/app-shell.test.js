/* 外壳接线（`App.vue`）：dock 的形态判据与 ESC 层序表

   为什么从 `App.vue` 进（而不是直挂 `DockPanel` / `ModalShell`）：上一阶段实测抓到过
   「`DockPanel` 漏把 `pools` 传给 `RunPanel`」——单测各自挂叶子组件时都过了，只有从真实父组件
   进才看得见。这一票同理：`dockTarget` 由 App 推导（私信下恒 `null`）、ESC 的层序也由 App 裁决，
   两条都只能在接上真实父组件之后断言。

   装置照 `app-context.test.js`：`fetch` 替身换 `globalThis.fetch`，事件源用 FakeEventSource，
   挂到 `document.body`（ESC 与 `document.activeElement` 都需要真实 DOM 落点）。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;
const ModalShell = (await import('../src/components/ModalShell.vue')).default;

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
    (this.listeners.get(type) || []).forEach((listener) => listener({ data: JSON.stringify(payload) }));
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

function routes() {
  return {
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
    '/api/channels/ch-1/config': () =>
      jsonResponse({ config: { channel_id: 'ch-1', mode: 'autonomous', team_leader_member_id: null } }),
    '/api/dms/dm-1/messages': () => jsonResponse({ dm: dm(), messages: [] }),
    '/api/dms/dm-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
    '/api/targets/ch-1/team-runs?limit=20': () => jsonResponse({ runs: [] }),
    '/api/targets/ch-1/plan': () => jsonResponse({ plan: { members: [] } }),
  };
}

const mounted = [];

async function mountApp() {
  const stub = createFetchStub(routes());
  globalThis.fetch = stub;
  // 「上次打开的 target」是唯一允许写浏览器存储的本地记忆（`stores/workspace.js`）。
  // 容器与旧用例同一条：不铺它就没有当前 target，也就没有频道头与运行面板入口。
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  const wrapper = mount(App, {
    props: { sourceFactory: () => FakeEventSource },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  // 等 `onMounted` 里的全量快照落地（工作台镜像铺满才有频道头与运行面板入口）。
  await flush();
  await flush();
  return { wrapper, stub };
}

/** 真实键盘事件打到 `document`（与浏览器里 `Escape` 的同一条路径）。 */
async function pressEscape() {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await flush();
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  FakeEventSource.instances = [];
  localStorage.clear();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

describe('dock 的三档判据', () => {
  test('默认没有形态：整栏不渲染', async () => {
    const { wrapper } = await mountApp();
    expect(wrapper.find('[data-od-id="app-shell"]').exists()).toBe(true);
    expect(wrapper.find('#dock').exists()).toBe(false);
  });

  test('点频道头的运行面板入口 ⇒ `#dock` 出现且 `.is-open`（桌面常驻列也是从「有形态」开始）', async () => {
    const { wrapper } = await mountApp();
    await wrapper.find('[data-od-id="btn-run-panel"]').trigger('click');
    await flush();

    const dock = wrapper.find('#dock');
    expect(dock.exists()).toBe(true);
    expect(dock.classes()).toContain('dock');
    expect(dock.classes()).toContain('is-open');
    expect(wrapper.find('[data-od-id="run-panel"]').exists()).toBe(true);
    // 标题来自频道名单（运行面板的 store 拿到的是同一个频道）。
    expect(wrapper.find('#rpTitle').text()).toBe('design');
  });

  test('切到私信 ⇒ `#dock` 整栏消失（私信下三档一档都不出现）', async () => {
    const { wrapper } = await mountApp();
    await wrapper.find('[data-od-id="btn-run-panel"]').trigger('click');
    await flush();
    expect(wrapper.find('#dock').exists()).toBe(true);

    await wrapper.find('[data-od-id="dm-dm-1"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#chanTitle').text()).toBe('@Ada');
    expect(wrapper.find('[data-od-id="btn-run-panel"]').exists()).toBe(false);
    expect(wrapper.find('#dock').exists()).toBe(false);
  });
});

describe('ESC 的层序表', () => {
  test('移动左抽屉开着时 ESC 收抽屉，dock 不受影响', async () => {
    // jsdom 没有 `matchMedia`：给一个「≤900px」的读数，让 ESC 的第五层也有判据可走。
    const original = window.matchMedia;
    window.matchMedia = (query) => ({ matches: query === '(max-width: 900px)', media: query });
    try {
      const { wrapper } = await mountApp();
      await wrapper.find('[data-od-id="btn-run-panel"]').trigger('click');
      await flush();
      await wrapper.find('[data-od-id="rail-toggle"]').trigger('click');
      await flush();
      expect(wrapper.find('[data-od-id="rail-scrim"]').exists()).toBe(true);

      await pressEscape();
      // 左抽屉先收（它是第四层），右抽屉仍在（第五层没轮到）。
      expect(wrapper.find('[data-od-id="rail-scrim"]').exists()).toBe(false);
      expect(wrapper.find('#rail').classes()).not.toContain('is-open');
      expect(wrapper.find('#dock').exists()).toBe(true);

      // 再一下才轮到右抽屉：它收起整栏（`closeRunPanel` 作废形态）。
      await pressEscape();
      expect(wrapper.find('#dock').exists()).toBe(false);
    } finally {
      window.matchMedia = original;
    }
  });

  test('桌面宽度（>900px）下 ESC 不关 dock：它是常驻列', async () => {
    const original = window.matchMedia;
    window.matchMedia = (query) => ({ matches: false, media: query });
    try {
      const { wrapper } = await mountApp();
      await wrapper.find('[data-od-id="btn-run-panel"]').trigger('click');
      await flush();
      expect(wrapper.find('#dock').exists()).toBe(true);

      await pressEscape();
      expect(wrapper.find('#dock').exists()).toBe(true);
      expect(wrapper.find('[data-od-id="run-panel"]').exists()).toBe(true);
    } finally {
      window.matchMedia = original;
    }
  });
});

describe('ESC 层序：模态栈在抽屉之上', () => {
  test('弹窗开着时 ESC 只收弹窗，左抽屉原样（层序表里模态栈排在 rail 之前）', async () => {
    const original = window.matchMedia;
    window.matchMedia = (query) => ({ matches: query === '(max-width: 900px)', media: query });
    let modal = null;
    try {
      const { wrapper } = await mountApp();
      await wrapper.find('[data-od-id="rail-toggle"]').trigger('click');
      await flush();
      expect(wrapper.find('[data-od-id="rail-scrim"]').exists()).toBe(true);

      modal = mount(ModalShell, { props: { open: true, title: '工作区设置' }, attachTo: document.body });
      await flush();

      await pressEscape();
      // 弹窗先收（它是第二层），左抽屉仍在（第四层没轮到）。
      expect(modal.emitted('update:open')).toEqual([[false]]);
      expect(wrapper.find('[data-od-id="rail-scrim"]').exists()).toBe(true);
    } finally {
      if (modal) modal.unmount();
      window.matchMedia = original;
    }
  });
});
