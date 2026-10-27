/* 三个新建表单（新建频道 / 新建 Agent / 新建私信）——票 #301

   为什么大部分用例从 `App.vue` 进（而不是直挂表单组件）：`selectTarget`、`busy` 与「建完谁出现
   在 rail 里」这三件事都由 App 裁决；直挂叶子组件只能证明「表单自己长对了」，证明不了接线
   （阶段一实测漏过 `DockPanel` 的 `pools` 传递，就是只看叶子组件的代价）。

   装置照 `app-shell.test.js`：`fetch` 替身换 `globalThis.fetch`、事件源用 FakeEventSource、
   `attachTo: document.body`（`ModalShell` 的焦点进入与回收要真实 DOM 落点）。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;

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

function dm(overrides) {
  return Object.assign(
    { id: 'dm-1', kind: 'dm', peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1 }, unread_count: 0 },
    overrides || {},
  );
}

/** 一个可被测试推进的「在途请求」：用来断言 busy 期间只发一次。 */
function deferred() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

function routes(extra) {
  return Object.assign(
    {
      '/api/settings': () =>
        jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [channel()],
          dms: [],
          agents: [ADA],
          model_providers: ['ollama', 'dashscope'],
          local_providers: ['ollama'],
          remote_consents: [],
        }),
      '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
      '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
      '/api/channels/ch-1/config': () =>
        jsonResponse({ config: { channel_id: 'ch-1', mode: 'autonomous', team_leader_member_id: null } }),
      '/api/member-statuses': () => jsonResponse({ statuses: {} }),
      '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
      '/api/targets/ch-1/team-runs?limit=20': () => jsonResponse({ runs: [] }),
      '/api/targets/ch-1/plan': () => jsonResponse({ plan: { members: [] } }),
    },
    extra || {},
  );
}

const mounted = [];

async function mountApp(extra) {
  const stub = createFetchStub(routes(extra));
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

beforeEach(() => {
  i18n.setLocale('zh-CN');
  FakeEventSource.instances = [];
  localStorage.clear();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

describe('空库的出路：两个新建入口可用', () => {
  test('rail 的两个新建按钮不再是禁用态（无 disabled、无 app.notMigrated 的 title）', async () => {
    const { wrapper } = await mountApp();
    for (const id of ['btn-new-channel', 'btn-new-agent']) {
      const button = wrapper.find(`[data-od-id="${id}"]`);
      expect(button.exists(), id).toBe(true);
      expect(button.attributes('disabled'), id).toBeUndefined();
      expect(button.attributes('title'), id).not.toBe('这个入口还没迁到 Vue 版工作台，请用 /studio/ 打开');
    }
  });

  test('点「新建频道」⇒ 弹窗打开，焦点落在名称输入框（正文第一个可聚焦件）', async () => {
    const { wrapper } = await mountApp();
    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await flush();

    const overlay = wrapper.find('#channelOverlay');
    expect(overlay.exists()).toBe(true);
    expect(overlay.element.hidden).toBe(false);
    expect(wrapper.find('#channelNameInput').exists()).toBe(true);
    expect(wrapper.find('#channelDescInput').exists()).toBe(true);
    expect(document.activeElement.id).toBe('channelNameInput');
  });

  test('关掉再打开：上一次填的内容不残留（旧页面 openChannelModal 同一条）', async () => {
    const { wrapper } = await mountApp();
    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await flush();
    await wrapper.find('#channelNameInput').setValue('草稿');
    await wrapper.find('#channelCancel').trigger('click');
    await flush();
    expect(wrapper.find('#channelOverlay').element.hidden).toBe(true);

    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await flush();
    expect(wrapper.find('#channelNameInput').element.value).toBe('');
  });
});

describe('新建频道：建完就在 rail 里、并自动切过去', () => {
  const created = channel({ id: 'ch-2', name: 'product', description: '产品频道' });

  test('提交成功后：rail 名单出现新频道且它成了当前行（`selectTarget` 在 App 里）', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/channels': () => jsonResponse({ channel: created, channels: [channel(), created] }),
      '/api/channels/ch-2/messages': () => jsonResponse({ channel: created, messages: [] }),
      '/api/channels/ch-2/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-2/members': () => jsonResponse({ members: [OWNER] }),
      '/api/channels/ch-2/config': () =>
        jsonResponse({ config: { channel_id: 'ch-2', mode: 'autonomous', team_leader_member_id: null } }),
      '/api/channels/ch-2/read': () => jsonResponse({ channel: created, channels: [channel(), created] }),
      '/api/targets/ch-2/team-runs?limit=20': () => jsonResponse({ runs: [] }),
      '/api/targets/ch-2/plan': () => jsonResponse({ plan: { members: [] } }),
    });

    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await flush();
    await wrapper.find('#channelNameInput').setValue('product');
    await wrapper.find('#channelDescInput').setValue('产品频道');
    await wrapper.find('[data-od-id="btn-create-channel"]').trigger('click');
    await flush();
    await flush();
    await flush();

    const post = stub.calls.find((call) => call.url === '/api/channels' && call.method === 'POST');
    expect(post.body).toEqual({ name: 'product', description: '产品频道', member_ids: [] });

    // 弹窗关掉了，rail 里出现了新频道，且它就是当前行。
    expect(wrapper.find('#channelOverlay').element.hidden).toBe(true);
    const row = wrapper.find('[data-od-id="channel-ch-2"]');
    expect(row.exists()).toBe(true);
    expect(row.classes()).toContain('is-active');
    expect(wrapper.find('#chanTitle').text()).toBe('product');
  });

  test('初始成员勾选会进 member_ids（勾上再取消 ⇒ 不出现在载荷里）', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/channels': () => jsonResponse({ channel: created, channels: [channel(), created] }),
    });
    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await flush();
    await wrapper.find('#channelNameInput').setValue('product');
    const chip = wrapper.find('[data-pick="mem-1"]');
    await chip.trigger('click');
    expect(chip.attributes('aria-pressed')).toBe('true');
    await chip.trigger('click');
    expect(wrapper.find('[data-pick="mem-1"]').attributes('aria-pressed')).toBe('false');

    await wrapper.find('[data-od-id="btn-create-channel"]').trigger('click');
    await flush();
    const post = stub.calls.find((call) => call.url === '/api/channels' && call.method === 'POST');
    expect(post.body.member_ids).toEqual([]);
  });

  test('提交失败：弹窗不关、错误在弹窗内可见、按钮可重试', async () => {
    let attempts = 0;
    const { wrapper, stub } = await mountApp({
      '/api/channels': () => {
        attempts += 1;
        if (attempts === 1) {
          return jsonResponse(
            { error: { code: 'validation_failed', message: '频道名称已存在', fields: {} } },
            400,
          );
        }
        return jsonResponse({ channel: created, channels: [channel(), created] });
      },
    });

    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await flush();
    await wrapper.find('#channelNameInput').setValue('product');
    await wrapper.find('[data-od-id="btn-create-channel"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('#channelOverlay').element.hidden).toBe(false);
    const error = wrapper.find('#channelFormError');
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain('频道名称已存在');
    expect(wrapper.find('[data-od-id="btn-create-channel"]').attributes('disabled')).toBeUndefined();
    // 人填的内容没有被吞掉（弹窗没重建）。
    expect(wrapper.find('#channelNameInput').element.value).toBe('product');

    // 再点一次就成功：同一个弹窗、同一份内容。
    await wrapper.find('[data-od-id="btn-create-channel"]').trigger('click');
    await flush();
    await flush();
    expect(stub.calls.filter((call) => call.url === '/api/channels' && call.method === 'POST')).toHaveLength(2);
  });

  test('在途重复提交只发一次（busy 期间按钮禁用，不会产生两个频道）', async () => {
    const gate = deferred();
    const { wrapper, stub } = await mountApp({
      '/api/channels': async () => {
        await gate.promise;
        return jsonResponse({ channel: created, channels: [channel(), created] });
      },
    });

    await wrapper.find('[data-od-id="btn-new-channel"]').trigger('click');
    await flush();
    await wrapper.find('#channelNameInput').setValue('product');
    const submit = wrapper.find('[data-od-id="btn-create-channel"]');
    await submit.trigger('click');
    await submit.trigger('click');
    await submit.trigger('click');
    expect(stub.calls.filter((call) => call.url === '/api/channels' && call.method === 'POST')).toHaveLength(1);

    gate.release();
    await flush();
    await flush();
    await flush();

    expect(stub.calls.filter((call) => call.url === '/api/channels' && call.method === 'POST')).toHaveLength(1);
    expect(wrapper.find('[data-od-id="channel-ch-2"]').exists()).toBe(true);
  });
});

describe('新建 Agent：建完就在 rail 成员名单里', () => {
  const bob = { id: 'mem-2', kind: 'agent', display_name: 'Bob', avatar_slot: 2 };

  function withAgents(snapshot) {
    return {
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [channel()],
          dms: [],
          agents: [ADA],
          model_providers: ['ollama', 'dashscope'],
          local_providers: ['ollama'],
          remote_consents: [],
        }),
      '/api/agents': () => jsonResponse({ agent: bob, agents: [ADA, bob], members: [OWNER, ADA, bob] }),
      ...(snapshot || {}),
    };
  }

  test('点「新建 Agent」⇒ 弹窗打开，provider 选项来自后端清单，默认首项', async () => {
    const { wrapper } = await mountApp(withAgents());
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();

    expect(wrapper.find('#agentOverlay').element.hidden).toBe(false);
    const select = wrapper.find('[data-od-id="agent-provider"]');
    expect(select.element.value).toBe('ollama');
    expect(select.findAll('option').map((node) => node.element.value)).toEqual(['ollama', 'dashscope']);
    expect(document.activeElement.id).toBe('agentNameInput');
  });

  test('提交成功后：rail 的成员名单出现新 Agent，弹窗关闭', async () => {
    const { wrapper, stub } = await mountApp(withAgents());
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();
    await wrapper.find('#agentNameInput').setValue('Bob');
    await wrapper.find('#agentRoleInput').setValue('后端');
    await wrapper.find('[data-od-id="agent-model"]').setValue('qwen-max');
    await wrapper.find('[data-od-id="btn-create-agent"]').trigger('click');
    await flush();
    await flush();

    const post = stub.calls.find((call) => call.url === '/api/agents' && call.method === 'POST');
    expect(post.body).toEqual({
      name: 'Bob',
      role: '后端',
      model_id: 'ollama:qwen-max',
      system_prompt: '',
    });
    expect(wrapper.find('#agentOverlay').element.hidden).toBe(true);
    expect(wrapper.find('[data-od-id="agent-mem-2"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="agent-mem-2"]').text()).toContain('Bob');
  });

  test('名称或模型名留空时提交键禁用（校验边界的最前一道，后端仍是最终判据）', async () => {
    const { wrapper } = await mountApp(withAgents());
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();
    const submit = () => wrapper.find('[data-od-id="btn-create-agent"]');
    expect(submit().attributes('disabled')).toBeDefined();

    await wrapper.find('#agentNameInput').setValue('Bob');
    expect(submit().attributes('disabled')).toBeDefined();
    await wrapper.find('[data-od-id="agent-model"]').setValue('qwen-max');
    expect(submit().attributes('disabled')).toBeUndefined();
  });

  test('提交失败：弹窗不关、错误可见（含后端字段级原因）', async () => {
    const { wrapper } = await mountApp({
      ...withAgents(),
      '/api/agents': () =>
        jsonResponse(
          {
            error: {
              code: 'validation_failed',
              message: '模型标识不合法',
              fields: { model_id: '应为 provider:model' },
            },
          },
          400,
        ),
    });
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();
    await wrapper.find('#agentNameInput').setValue('Bob');
    await wrapper.find('[data-od-id="agent-model"]').setValue('qwen-max');
    await wrapper.find('[data-od-id="btn-create-agent"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('#agentOverlay').element.hidden).toBe(false);
    const error = wrapper.find('#agentFormError');
    expect(error.text()).toContain('模型标识不合法');
    expect(error.text()).toContain('应为 provider:model');
  });
});

describe('远端数据流告知块（建完不可用的挡板）', () => {
  test('默认 provider 是远端 ⇒ 告知块出现；切到本机 provider ⇒ 它消失', async () => {
    const { wrapper } = await mountApp({
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [channel()],
          dms: [],
          agents: [ADA],
          model_providers: ['dashscope', 'ollama'],
          local_providers: ['ollama'],
          remote_consents: [],
        }),
      '/api/remote-data-flow': () =>
        jsonResponse({ data_faces: ['消息正文', '任务描述'], local_providers: ['ollama'], consents: [] }),
    });
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();
    await flush();

    const notice = wrapper.find('[data-od-id="remote-notice"]');
    expect(notice.exists()).toBe(true);
    expect(wrapper.find('#agentRemoteBody').text()).toContain('消息正文');
    expect(wrapper.find('#agentRemoteBody').text()).toContain('任务描述');

    await wrapper.find('[data-od-id="agent-provider"]').setValue('ollama');
    await flush();
    expect(wrapper.find('[data-od-id="remote-notice"]').exists()).toBe(false);
  });

  test('已确认过的远端 provider 不再显示告知块', async () => {
    const { wrapper } = await mountApp({
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [channel()],
          dms: [],
          agents: [ADA],
          model_providers: ['dashscope'],
          local_providers: ['ollama'],
          remote_consents: [{ provider: 'dashscope', acknowledged_at: '2026-10-01T00:00:00Z' }],
        }),
      '/api/remote-data-flow': () => jsonResponse({ data_faces: ['消息正文'] }),
    });
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="remote-notice"]').exists()).toBe(false);
  });

  test('数据面读不到：如实说明且确认键不可点（不编一句缺了数据面的文案）', async () => {
    const { wrapper } = await mountApp({
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [channel()],
          dms: [],
          agents: [ADA],
          model_providers: ['dashscope'],
          local_providers: ['ollama'],
          remote_consents: [],
        }),
      '/api/remote-data-flow': () => jsonResponse({ error: 'boom' }, 500),
    });
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('[data-od-id="remote-notice"]').exists()).toBe(true);
    expect(wrapper.find('#agentRemoteBody').text()).toBe(
      '还没读到会离开本机的数据面清单，确认键暂时不可用（重新打开弹窗会再读一次）。',
    );
    expect(wrapper.find('[data-od-id="remote-ack"]').attributes('disabled')).toBeDefined();
  });

  test('点确认 ⇒ 只发一次 ack、弹窗不关、告知块消失', async () => {
    const gate = deferred();
    const { wrapper, stub } = await mountApp({
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA],
          channels: [channel()],
          dms: [],
          agents: [ADA],
          model_providers: ['dashscope'],
          local_providers: ['ollama'],
          remote_consents: [],
        }),
      '/api/remote-data-flow': () => jsonResponse({ data_faces: ['消息正文'] }),
      '/api/remote-data-flow/ack': async () => {
        await gate.promise;
        return jsonResponse({
          consent: { provider: 'dashscope', acknowledged_at: '2026-10-26T00:00:00Z' },
          consents: [{ provider: 'dashscope', acknowledged_at: '2026-10-26T00:00:00Z' }],
          agents: [ADA],
        });
      },
    });
    await wrapper.find('[data-od-id="btn-new-agent"]').trigger('click');
    await flush();
    await flush();

    const ack = wrapper.find('[data-od-id="remote-ack"]');
    await ack.trigger('click');
    await ack.trigger('click');
    expect(stub.calls.filter((call) => call.url === '/api/remote-data-flow/ack' && call.method === 'POST'))
      .toHaveLength(1);

    gate.release();
    await flush();
    await flush();

    expect(wrapper.find('#agentOverlay').element.hidden).toBe(false);
    expect(wrapper.find('[data-od-id="remote-notice"]').exists()).toBe(false);
    expect(stub.calls.find((call) => call.url === '/api/remote-data-flow/ack').body).toEqual({
      provider: 'dashscope',
    });
  });
});

/* rail 的 Agent 行由**一个开关**裁决两档（票 #301 立语义，票 #310 翻开关）：
   开 ⇒ 进 Agent 详情，关 ⇒ 打开与它的私信。下面这组守的是**关着**那一档（私信）——
   它仍然存在（旧页面同一件事的入口就是「选中一个成员 = 表达和谁私信」，`app.js:5184-5215`），
   因此用一个桩替身把 `agentDetailAvailable` 拨回去，逐条守住私信分支的语义没有随开关翻正而丢掉。
   开关翻正之后 rail 上的无障碍名是「查看 {name} 的详情」——那一条在 `agent-detail.test.js` 里断言。 */
async function mountAppWithDetailOff(extra) {
  const { default: RailSidebar } = await import('../src/components/RailSidebar.vue');
  const { reactive } = await import('vue');
  const state = reactive({
    member: OWNER,
    members: [OWNER, ADA],
    channels: [channel()],
    dms: [],
    agents: [ADA],
    statuses: {},
    activeTargetId: 'ch-1',
  });
  const opened = [];
  const wrapper = mount(RailSidebar, {
    props: {
      state,
      agentDetailAvailable: false,
      onOpenDm: (id) => opened.push(id),
    },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  return { wrapper, opened };
}

describe('rail 的 Agent 行：一个开关裁决两档（票 #301 立语义，票 #310 翻开关）', () => {
  function dmRoutes(created) {
    const opened = dm({ id: 'dm-9' });
    return {
      '/api/dms': () => jsonResponse({ dm: opened, created }),
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
      '/api/dms/dm-9/messages': () => jsonResponse({ dm: opened, messages: [] }),
      '/api/dms/dm-9/members': () => jsonResponse({ members: [OWNER, ADA] }),
      '/api/dms/dm-9/read': () => jsonResponse({ dm: opened, dms: [opened] }),
    };
  }

  test('点 Agent 行 ⇒ 只抛 `open-dm`（私信分支）；开关翻正后 App 侧走的是详情分支', async () => {
    const { wrapper, opened } = await mountAppWithDetailOff();
    const row = wrapper.find('[data-od-id="agent-mem-1"]');
    expect(row.element.tagName).toBe('BUTTON');
    expect(row.classes()).toContain('nav-row');
    // 关着时无障碍名说的是「打开私信」；点下去抛的是 `open-dm`（不是 `open-agent`）。
    expect(row.attributes('aria-label')).toBe('打开与 Ada 的私信');
    expect(row.find('.presence').exists()).toBe(true);
    await row.trigger('click');
    expect(opened).toEqual(['mem-1']);
  });

  test('开关开着时同一个行抛的是 `open-agent`，无障碍名改成「查看详情」', async () => {
    const { default: RailSidebar } = await import('../src/components/RailSidebar.vue');
    const { reactive } = await import('vue');
    const state = reactive({
      member: OWNER,
      members: [OWNER, ADA],
      channels: [channel()],
      dms: [],
      agents: [ADA],
      statuses: {},
      activeTargetId: 'ch-1',
    });
    const picked = [];
    const wrapper = mount(RailSidebar, {
      props: {
        state,
        agentDetailAvailable: true,
        onOpenAgent: (id) => picked.push(id),
      },
      attachTo: document.body,
    });
    mounted.push(wrapper);

    const row = wrapper.find('[data-od-id="agent-mem-1"]');
    expect(row.attributes('aria-label')).toBe('查看 Ada 的详情');
    await row.trigger('click');
    expect(picked).toEqual(['mem-1']);
  });
});
