/* 频道归档与删除（#342 / ADR-0040）：左栏分段 / 频道头收敛 / composer 只读 / 危险区删除。

   为什么**从 `App.vue` 进**（与 `channel-tools.test.js` 同一条理由）：
   1. 「归档后左栏把它挪进折叠段」「频道头只剩取消归档」「composer 禁用」三件事跨
      store 派生 → 两个组件 → 请求回包，直挂叶子组件只能证明「给定 props 时行长得对」；
   2. 删除的后果是「主线落到空态或下一个可用频道」，那一步的接线只在 `App.vue`；
   3. 「删除先确认、确认才发请求」是**请求次数**的断言，需要真实的 fetch 替身。

   断言落在产品渲染出来的元素 / 文字 / 发出的请求上（`data-od-id` 按既有体例），文案取自词条表。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;
const { createWorkspaceStore } = await import('../src/stores/workspace.js');
const targetView = await import('../src/lib/target-view.js');

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

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
}

function channel(overrides) {
  return Object.assign(
    {
      id: 'ch-1',
      name: 'design',
      description: '设计频道',
      created_at: '2026-10-20T09:00:00Z',
      archived_at: null,
      deleted_at: null,
      last_seq: 0,
      last_read_seq: 0,
      unread_count: 0,
    },
    overrides || {},
  );
}

const LIVE = channel({ id: 'ch-1', name: 'design' });
const ARCHIVED = channel({ id: 'ch-2', name: '旧的', archived_at: '2026-10-26T10:00:00.000Z' });

function config() {
  return {
    channel_id: 'ch-1',
    mode: 'autonomous',
    team_scope: 'channel+task',
    team_leader_member_id: null,
    leader_available: false,
    leader_reason: '',
    workspace_root: null,
    workspace_backend: 'local',
    permission_mode: 'ask-writes',
    web_access: null,
    docker_egress: false,
  };
}

function budget() {
  return {
    channel_id: 'ch-1',
    epoch_seq: 0,
    limit: 3,
    used: 0,
    remaining: 3,
    source: 'built-in-default',
    pending_limit: null,
  };
}

/** 工作台载入 + 三张弹窗可能读到的读数。`overrides` 覆盖单条路由。 */
function routes(overrides) {
  return Object.assign(
    {
      '/api/settings': () =>
        jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [LIVE, ARCHIVED],
          dms: [],
          agents: [ADA],
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        }),
      '/api/channels/ch-1/messages': () => jsonResponse({ channel: LIVE, messages: [] }),
      '/api/channels/ch-2/messages': () => jsonResponse({ channel: ARCHIVED, messages: [] }),
      '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
      '/api/channels/ch-1/config': () => jsonResponse({ config: config() }),
      '/api/channels/ch-1/budget': () => jsonResponse({ budget: budget() }),
      '/api/member-statuses': () => jsonResponse({ statuses: {} }),
      '/api/web-providers': () => jsonResponse({ providers: [], active: null }),
      '/api/remote-data-flow': () => jsonResponse({ data_faces: [], web_consents: [] }),
      '/api/reminders': () => jsonResponse({ reminders: [] }),
    },
    overrides || {},
  );
}

const mounted = [];

async function mountApp(routeOverrides, activeChannelId) {
  const stub = createFetchStub(routes(routeOverrides));
  globalThis.fetch = stub;
  localStorage.setItem('alibot.activeChannel', activeChannelId || 'ch-1');
  const wrapper = mount(App, { props: { sourceFactory: () => FakeEventSource }, attachTo: document.body });
  mounted.push(wrapper);
  await flush();
  await flush();
  return { wrapper, stub };
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

/* ─── 一、左栏分段 ─────────────────────────────────────────────────────────── */

describe('左栏：活跃段与「已归档」折叠段', () => {
  test('归档频道进折叠段、活跃段不含它', async () => {
    const { wrapper } = await mountApp();
    const live = wrapper.find('#channelList');
    expect(live.find('[data-od-id="channel-ch-1"]').exists()).toBe(true);
    expect(live.find('[data-od-id="channel-ch-2"]').exists()).toBe(false);

    const section = wrapper.find('[data-od-id="channel-archive-section"]');
    expect(section.exists()).toBe(true);
    expect(section.find('[data-od-id="channel-ch-2"]').exists()).toBe(true);
    expect(section.find('[data-od-id="channel-ch-1"]').exists()).toBe(false);
  });

  test('折叠段默认收起：归档行在 DOM 里但不可见（当前打开的是活跃频道）', async () => {
    const { wrapper } = await mountApp();
    const section = wrapper.find('[data-od-id="channel-archive-section"]');
    expect(section.find('[data-od-id="channel-ch-2"]').isVisible()).toBe(false);
  });

  test('折叠按钮的 `aria-expanded` 默认 false，点一下翻成 true 且列表可见', async () => {
    const { wrapper } = await mountApp();
    const toggle = wrapper.find('[data-od-id="btn-channel-archive-toggle"]');
    expect(toggle.attributes('aria-expanded')).toBe('false');
    expect(wrapper.find('#channelArchiveList').isVisible()).toBe(false);

    await toggle.trigger('click');

    expect(toggle.attributes('aria-expanded')).toBe('true');
    expect(wrapper.find('#channelArchiveList').isVisible()).toBe(true);
  });

  test('没有归档频道时整段不渲染（不铺一个空段）', async () => {
    const { wrapper } = await mountApp({
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [LIVE],
          dms: [],
          agents: [ADA],
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        }),
    });
    expect(wrapper.find('[data-od-id="channel-archive-section"]').exists()).toBe(false);
  });
});

/* ─── 二、归档态：composer 与频道头 ────────────────────────────────────────── */

describe('归档频道：只读的呈现面', () => {
  test('打开归档频道 ⇒ composer 禁用、占位是「只读归档」那句', async () => {
    const { wrapper } = await mountApp(undefined, 'ch-2');
    await wrapper.find('[data-od-id="btn-channel-archive-toggle"]').trigger('click');
    await wrapper.find('[data-od-id="channel-ch-2"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('#chanTitle').text()).toBe('旧的');
    expect(wrapper.find('#chanDesc').text()).toBe(i18n.t('header.channelArchivedDesc'));
    const input = wrapper.find('#composer textarea');
    expect(input.attributes('placeholder')).toBe(i18n.t('header.channelArchivedPlaceholder'));
    expect(input.attributes('disabled')).toBeDefined();
  });

  test('归档态下频道工具行只剩「取消归档」：名册 / 配置 / 预算 / 运行面板整组不渲染', async () => {
    const { wrapper } = await mountApp(undefined, 'ch-2');
    await wrapper.find('[data-od-id="btn-channel-archive-toggle"]').trigger('click');
    await wrapper.find('[data-od-id="channel-ch-2"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('[data-od-id="btn-channel-unarchive"]').exists()).toBe(true);
    for (const id of [
      'btn-channel-tools',
      'btn-channel-members',
      'btn-channel-settings',
      'btn-channel-budget',
      'btn-run-panel',
      'btn-channel-archive',
    ]) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(false);
    }
  });

  test('活跃频道头没有归档按钮：归档入口在频道工具 → 设置页签的危险区里', async () => {
    const { wrapper } = await mountApp();
    expect(wrapper.find('[data-od-id="btn-channel-tools"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="btn-channel-unarchive"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="btn-channel-archive"]').exists()).toBe(false);

    await openConfig(wrapper);

    const danger = wrapper.find('[data-od-id="channel-config-section-danger"]');
    expect(danger.find('[data-od-id="btn-channel-archive"]').exists()).toBe(true);
    expect(danger.find('[data-od-id="btn-channel-delete"]').exists()).toBe(true);
  });

  test('点归档 ⇒ POST .../archive，并用响应覆盖左栏（频道挪进折叠段）', async () => {
    const archivedRow = channel({ ...LIVE, archived_at: '2026-10-27T08:00:00.000Z' });
    const { wrapper, stub } = await mountApp({
      '/api/channels/ch-1/archive': () =>
        jsonResponse({ channel: archivedRow, channels: [archivedRow, ARCHIVED] }),
    });

    await openConfig(wrapper);
    await wrapper.find('[data-od-id="btn-channel-archive"]').trigger('click');
    await flush();
    await flush();
    await flush();

    const calls = stub.to('/api/channels/ch-1/archive');
    expect(calls.length).toBe(1);
    expect(calls[0].method).toBe('POST');
    // 左栏：活跃段里已经没有它，折叠段里有，而且**这一段自动展开**（否则刚归档的当前频道会
    // 从左栏消失——人看不出自己归档的是哪一条）。
    expect(wrapper.find('#channelList [data-od-id="channel-ch-1"]').exists()).toBe(false);
    const movedRow = wrapper.find('[data-od-id="channel-archive-section"] [data-od-id="channel-ch-1"]');
    expect(movedRow.exists()).toBe(true);
    expect(movedRow.isVisible()).toBe(true);
    expect(
      wrapper.find('[data-od-id="btn-channel-archive-toggle"]').attributes('aria-expanded'),
    ).toBe('true');
    // 归档成功：频道工具弹窗关闭，频道头只剩「取消归档」（频道工具入口整组让位）。
    expect(wrapper.find('#channelToolsOverlay').attributes('hidden')).toBeDefined();
    expect(wrapper.find('[data-od-id="btn-channel-tools"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="btn-channel-unarchive"]').exists()).toBe(true);
    expect(wrapper.find('#composer textarea').attributes('disabled')).toBeDefined();
  });

  test('归档失败：工具弹窗不关，toast 给后端可读原因', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/archive': () =>
        jsonResponse({ error: { code: 'boom', message: '后端拒绝归档' } }, 500),
    });
    await openConfig(wrapper);
    await wrapper.find('[data-od-id="btn-channel-archive"]').trigger('click');
    await flush();
    await flush();

    // 失败只呈现既有 toast 文案；弹窗与人所在的那一页都还在。
    expect(wrapper.find('#channelToolsOverlay').attributes('hidden')).toBeUndefined();
    expect(wrapper.find('[data-od-id="channel-config-body"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('归档频道失败');
    expect(wrapper.find('[data-od-id="btn-channel-unarchive"]').exists()).toBe(false);
  });

  test('人手把归档段收起后，它保持收起（手点把跟随态钉住）', async () => {
    const archivedRow = channel({ ...LIVE, archived_at: '2026-10-27T08:00:00.000Z' });
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/archive': () =>
        jsonResponse({ channel: archivedRow, channels: [archivedRow, ARCHIVED] }),
    });
    await openConfig(wrapper);
    await wrapper.find('[data-od-id="btn-channel-archive"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#channelArchiveList').isVisible()).toBe(true);

    await wrapper.find('[data-od-id="btn-channel-archive-toggle"]').trigger('click');

    expect(wrapper.find('[data-od-id="btn-channel-archive-toggle"]').attributes('aria-expanded')).toBe('false');
    expect(wrapper.find('#channelArchiveList').isVisible()).toBe(false);
  });

  test('点取消归档 ⇒ POST .../unarchive，频道回到活跃段', async () => {
    const restored = channel({ id: 'ch-2', name: '旧的', archived_at: null });
    const { wrapper, stub } = await mountApp(
      {
        '/api/channels/ch-2/unarchive': () =>
          jsonResponse({ channel: restored, channels: [LIVE, restored] }),
      },
      'ch-2',
    );
    await wrapper.find('[data-od-id="btn-channel-archive-toggle"]').trigger('click');
    await wrapper.find('[data-od-id="channel-ch-2"]').trigger('click');
    await flush();
    await flush();

    await wrapper.find('[data-od-id="btn-channel-unarchive"]').trigger('click');
    await flush();
    await flush();

    expect(stub.to('/api/channels/ch-2/unarchive').length).toBe(1);
    expect(wrapper.find('#channelList [data-od-id="channel-ch-2"]').exists()).toBe(true);
    // 回到活跃态：频道工具入口重新出现（归档入口随「设置」页签一起回来）。
    expect(wrapper.find('[data-od-id="btn-channel-tools"]').exists()).toBe(true);
    expect(wrapper.find('#composer textarea').attributes('disabled')).toBeUndefined();
  });
});

/* ─── 三、危险区：删除要先确认 ─────────────────────────────────────────────── */

/** 打开频道工具弹窗 → 切到「设置」页签（归档与删除都在它的危险区里）。 */
async function openConfig(wrapper) {
  await wrapper.find('[data-od-id="btn-channel-tools"]').trigger('click');
  await flush();
  await flush();
  await wrapper.find('[data-od-id="channel-tools-tab-settings"]').trigger('click');
  await flush();
}

describe('频道设置的危险区：删除的二次确认', () => {
  test('危险区有归档说明与删除按钮；「删除」默认不发请求', async () => {
    const { wrapper, stub } = await mountApp();
    await openConfig(wrapper);

    const danger = wrapper.find('[data-od-id="channel-config-section-danger"]');
    expect(danger.exists()).toBe(true);
    expect(danger.text()).toContain(i18n.t('channelConfig.dangerArchiveNote'));
    expect(danger.find('[data-od-id="btn-channel-delete"]').exists()).toBe(true);
    // 点第一次只铺确认条：**没有任何 DELETE**。
    await danger.find('[data-od-id="btn-channel-delete"]').trigger('click');
    await flush();
    expect(stub.calls.filter((call) => call.method === 'DELETE').length).toBe(0);
    expect(wrapper.find('[data-od-id="channel-delete-confirm"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="channel-delete-confirm"]').text()).toContain('#design');
  });

  test('点「取消」收起确认条，仍然没有发请求', async () => {
    const { wrapper, stub } = await mountApp();
    await openConfig(wrapper);
    await wrapper.find('[data-od-id="btn-channel-delete"]').trigger('click');
    await flush();

    await wrapper.find('[data-od-id="channel-delete-cancel"]').trigger('click');
    await flush();

    expect(wrapper.find('[data-od-id="channel-delete-confirm"]').exists()).toBe(false);
    expect(stub.calls.filter((call) => call.method === 'DELETE').length).toBe(0);
  });

  test('点「确认删除」才发 DELETE；请求在飞时两枚按钮都禁用', async () => {
    let release = null;
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    const { wrapper, stub } = await mountApp({
      '/api/channels/ch-1': () =>
        pending.then(() => jsonResponse({ deleted: 'ch-1', channels: [ARCHIVED] })),
    });
    await openConfig(wrapper);
    await wrapper.find('[data-od-id="btn-channel-delete"]').trigger('click');
    await flush();

    await wrapper.find('[data-od-id="channel-delete-confirm-action"]').trigger('click');
    await flush();

    const deletes = stub.calls.filter((call) => call.method === 'DELETE');
    expect(deletes.length).toBe(1);
    expect(deletes[0].url).toContain('/api/channels/ch-1');
    // 在飞：确认与取消都禁用（连点两次不该发出两次 DELETE）。
    expect(wrapper.find('[data-od-id="channel-delete-confirm-action"]').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-od-id="channel-delete-cancel"]').attributes('disabled')).toBeDefined();

    release();
    await flush();
    await flush();
  });

  test('删除当前打开的频道 ⇒ 主线落到下一个可用频道，不残留上一个频道的消息', async () => {
    const third = channel({ id: 'ch-3', name: '下一个', created_at: '2026-10-21T09:00:00Z' });
    const { wrapper, stub } = await mountApp({
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [LIVE, ARCHIVED, third],
          dms: [],
          agents: [ADA],
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        }),
      '/api/channels/ch-1/messages': () =>
        jsonResponse({
          channel: LIVE,
          messages: [
            {
              id: 'm1',
              target_id: 'ch-1',
              seq: 1,
              kind: 'human',
              body: '上一个频道的消息',
              created_at: '2026-10-26T10:00:00.000Z',
              mention_snapshot: [],
              author: OWNER,
            },
          ],
        }),
      '/api/channels/ch-3/messages': () => jsonResponse({ channel: third, messages: [] }),
      '/api/channels/ch-3/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-3/members': () => jsonResponse({ members: [OWNER] }),
      '/api/channels/ch-1': () => jsonResponse({ deleted: 'ch-1', channels: [ARCHIVED, third] }),
    });
    expect(wrapper.find('#chanTitle').text()).toBe('design');
    // 消息流里已经有上一个频道的那条消息。
    await flush();
    const beforeMessages = wrapper.text();
    expect(beforeMessages).toContain('上一个频道的消息');

    await openConfig(wrapper);
    await wrapper.find('[data-od-id="btn-channel-delete"]').trigger('click');
    await flush();
    await wrapper.find('[data-od-id="channel-delete-confirm-action"]').trigger('click');
    await flush();
    await flush();
    await flush();

    expect(stub.calls.filter((call) => call.method === 'DELETE').length).toBe(1);
    // 主线落到了**下一个活跃频道**（跳过归档的那个），旧频道的消息不再渲染。
    expect(wrapper.find('#chanTitle').text()).toBe('下一个');
    expect(wrapper.text()).not.toContain('上一个频道的消息');
    expect(wrapper.find('#channelList [data-od-id="channel-ch-1"]').exists()).toBe(false);
  });
});

/* ─── 四、store：用响应覆盖镜像，不本地拼行 ────────────────────────────────── */

describe('store 的三个动作', () => {
  const LOCAL = { id: 'ch-x', name: 'x', description: '', archived_at: null, deleted_at: null };

  function makeStore(fetchImpl) {
    globalThis.fetch = fetchImpl;
    return createWorkspaceStore({ toast: () => {} });
  }

  test('unarchiveChannel 用响应里的 channels 整份覆盖（不本地拼一条「看起来成功了」的行）', async () => {
    const restored = { ...LOCAL, archived_at: null, name: '服务端给的名字' };
    const stub = createFetchStub({
      '/api/channels/ch-x/unarchive': () =>
        jsonResponse({ channel: restored, channels: [restored, { ...LOCAL, id: 'ch-y' }] }),
    });
    const store = makeStore(stub);
    store.mutable.channels = [LOCAL];

    const payload = await store.unarchiveChannel('ch-x');

    expect(stub.calls[0].method).toBe('POST');
    expect(stub.calls[0].url).toContain('/api/channels/ch-x/unarchive');
    expect(payload.channel.name).toBe('服务端给的名字');
    expect(store.state.channels.map((item) => item.id)).toEqual(['ch-x', 'ch-y']);
    expect(store.state.channels[0].name).toBe('服务端给的名字');
    expect(store.state.channels[0].archived_at).toBe(null);
  });

  test('archiveChannel / deleteChannel 同样以响应覆盖；派生读数跟着变', async () => {
    const archivedRow = { ...LOCAL, archived_at: '2026-10-27T00:00:00.000Z' };
    const stub = createFetchStub({
      '/api/channels/ch-x/archive': () => jsonResponse({ channel: archivedRow, channels: [archivedRow] }),
      '/api/channels/ch-x': () => jsonResponse({ deleted: 'ch-x', channels: [] }),
    });
    const store = makeStore(stub);
    store.mutable.channels = [LOCAL];
    store.setActiveTargetId('ch-x');
    expect(store.liveChannels.value.map((item) => item.id)).toEqual(['ch-x']);

    await store.archiveChannel('ch-x');
    expect(store.archivedChannels.value.map((item) => item.id)).toEqual(['ch-x']);
    expect(store.liveChannels.value).toEqual([]);
    expect(store.isArchivedChannel.value).toBe(true);
    expect(store.isArchivedTarget.value).toBe(true);

    await store.deleteChannel('ch-x');
    expect(store.state.channels).toEqual([]);
  });
});

/* ─── 五、target-view 的优先级：归档 > 私信 > 频道 ─────────────────────────── */

describe('target-view 的呈现派生', () => {
  test('归档频道的占位与描述走归档那一句，私信仍走它自己那一句', () => {
    const archivedChannel = channel({ archived_at: '2026-10-27T00:00:00Z' });
    const liveChannel = channel();
    const dm = { id: 'dm-1', kind: 'dm', peer: { display_name: 'Ada', deleted_at: null } };
    const archivedDm = { id: 'dm-2', kind: 'dm', peer: { display_name: 'Bob', deleted_at: '2026-10-01T00:00:00Z' } };

    expect(targetView.composerPlaceholder(archivedChannel)).toBe(i18n.t('header.channelArchivedPlaceholder'));
    expect(targetView.targetDescription(archivedChannel)).toBe(i18n.t('header.channelArchivedDesc'));
    expect(targetView.isArchivedChannel(archivedChannel)).toBe(true);
    expect(targetView.isArchivedChannel(liveChannel)).toBe(false);

    expect(targetView.composerPlaceholder(liveChannel)).toBe(i18n.t('header.channelPlaceholder', { name: 'design' }));
    expect(targetView.composerPlaceholder(dm)).toBe(i18n.t('header.dmPlaceholder', { name: 'Ada' }));
    expect(targetView.composerPlaceholder(archivedDm)).toBe(i18n.t('header.dmArchivedPlaceholder'));
    expect(targetView.targetDescription(archivedDm)).toBe(i18n.t('header.dmArchivedDesc'));
  });
});
