/* 频道头三工具（成员名册 / 频道配置 / 回应预算）的 DOM 契约

   为什么**从 `App.vue` 进**（不是直挂三个弹窗）：
   1. `channelToolsAvailable` 的翻正在 `App.vue` 与 `ChannelHeader.vue` 两处，只挂叶子组件测不到
      「频道下出现、私信下不存在」这条组合判据；
   2. 名册的「加入」要求**不整页刷新**（断言请求数）且 `@` 补全立刻能选到它——那两件事跨
      workspace store / 名册弹窗 / composer 三个组件；
   3. 弹窗的 ESC 由 `ModalShell` 的模块级栈裁决，挂到 `document.body` 才是真实落点。

   断言落在**产品渲染出来的元素 / 文字 / 发出的请求**上：`data-od-id` 与类名逐字沿用旧页面
   （`studio/index.html` / `app.js` 的落点），文案来自词条表。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;
const ChannelHeader = (await import('../src/components/ChannelHeader.vue')).default;

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1, role: '工程师' };
const BOB = { id: 'mem-2', kind: 'agent', display_name: 'Bob', avatar_slot: 2, role: '审阅' };

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

function config(overrides) {
  return Object.assign(
    {
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
    },
    overrides || {},
  );
}

function budget(overrides) {
  return Object.assign(
    {
      channel_id: 'ch-1',
      epoch_seq: 4,
      limit: 3,
      used: 1,
      remaining: 2,
      source: 'built-in-default',
      pending_limit: null,
    },
    overrides || {},
  );
}

/** 一次成功的工作台载入 + 三面读数（三张弹窗打开时读到的就是这几份载荷）。 */
function routes(overrides) {
  return Object.assign(
    {
      '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA, BOB],
          channels: [channel()],
          dms: [dm()],
          agents: [ADA, BOB],
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        }),
      '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
      '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-1/members': (url, request) => {
        if ((request.method || 'GET') === 'POST') {
          // 幂等动作的响应就带该频道**最新**成员表（后端 `add_channel_member` 的载荷形状）。
          return jsonResponse({
            joined: true,
            member: BOB,
            members: [OWNER, ADA, BOB],
            channel: channel(),
          });
        }
        return jsonResponse({ members: [OWNER, ADA] });
      },
      '/api/channels/ch-1/config': () => jsonResponse({ config: config() }),
      '/api/channels/ch-1/budget': () => jsonResponse({ budget: budget() }),
      '/api/dms/dm-1/messages': () => jsonResponse({ dm: dm(), messages: [] }),
      '/api/dms/dm-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
      '/api/member-statuses': () => jsonResponse({ statuses: {} }),
      '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
      '/api/mcp-servers?scope_kind=agent&scope_id=mem-1': () => jsonResponse({ mcp_servers: [{ id: 'm1' }] }),
      '/api/skills?scope_kind=agent&scope_id=mem-1': () => jsonResponse({ skills: [{ id: 's1' }, { id: 's2' }] }),
      '/api/mcp-servers?scope_kind=agent&scope_id=mem-2': () => jsonResponse({ mcp_servers: [] }),
      '/api/skills?scope_kind=agent&scope_id=mem-2': () => jsonResponse({ skills: [] }),
    },
    overrides || {},
  );
}

const mounted = [];

async function mountApp(routeOverrides) {
  const stub = createFetchStub(routes(routeOverrides));
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

function countCalls(stub, fragment) {
  return stub.to(fragment).length;
}

async function openRoster(wrapper) {
  await wrapper.find('[data-od-id="btn-channel-members"]').trigger('click');
  await flush();
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

/* ─── 一、开关翻正：频道下三个入口出现，私信下三个都不存在 ──────────────── */

describe('频道头的三个工具入口', () => {
  test('频道下名册 / 配置 / 预算三个入口都出现（与运行面板并列）', async () => {
    const { wrapper } = await mountApp();
    for (const id of ['btn-channel-members', 'btn-channel-settings', 'btn-channel-budget', 'btn-run-panel']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(true);
    }
  });

  test('私信下三个入口一个都不存在（`!isDm` 判据不变）', async () => {
    const { wrapper } = await mountApp();
    await wrapper.find('[data-od-id="dm-dm-1"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('#chanTitle').text()).toBe('@Ada');
    for (const id of ['btn-channel-members', 'btn-channel-settings', 'btn-channel-budget']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(false);
    }
  });

  test('叶子组件：`channelToolsAvailable` 默认是开（迁完那个面只翻开关，组件不用改）', () => {
    const wrapper = mount(ChannelHeader, {
      props: { target: channel(), isDm: false, messageCount: 0, taskCount: 0 },
    });
    for (const id of ['btn-channel-members', 'btn-channel-settings', 'btn-channel-budget']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(true);
    }
  });
});

/* ─── 二、成员名册 ──────────────────────────────────────────────────────── */

describe('成员名册（弹窗）', () => {
  test('打开即分「已加入 / 未加入」两段，行与能力摘要都是后端读数', async () => {
    const { wrapper, stub } = await mountApp();
    expect(wrapper.find('[data-od-id="roster-body"]').isVisible()).toBe(false);

    await openRoster(wrapper);

    expect(wrapper.find('[data-od-id="roster-body"]').isVisible()).toBe(true);
    // 打开时重新读一次该频道的成员表（正本是后端，不用可能过期的缓存当结论）。
    expect(stub.to('/api/channels/ch-1/members').length).toBeGreaterThan(1);

    const joined = wrapper.find('[data-od-id="roster-joined"]');
    // 名册是**全名单**（含 owner）：两段的分工是「已加入 / 未加入」，不是「Agent / 非 Agent」。
    expect(joined.find('.dock-group-label').text()).toBe('已加入（2）');
    expect(joined.find('[data-od-id="roster-member-mem-1"]').exists()).toBe(true);
    expect(joined.find('.roster-name').text()).toBe('owner');
    expect(joined.find('[data-od-id="roster-member-owner"] .roster-me').text()).toBe('我');

    const outsiders = wrapper.find('[data-od-id="roster-not-joined"]');
    expect(outsiders.find('.dock-group-label').text()).toBe('未加入本频道（1）');
    expect(outsiders.find('[data-od-id="roster-join-mem-2"]').exists()).toBe(true);

    // 能力摘要是 agent scope 两条清单的读数（MCP 1 / 技能 2），不是前端推算。
    expect(wrapper.find('[data-od-id="roster-capabilities-mem-1"]').text()).toBe('MCP 1 / 技能 2');
  });

  test('点「加入」：成员挪到已加入段，且没有整页刷新（只多一次 POST）', async () => {
    const { wrapper, stub } = await mountApp();
    await openRoster(wrapper);
    const workspaceCalls = countCalls(stub, '/api/workspace');
    const messageCalls = countCalls(stub, '/api/channels/ch-1/messages');

    await wrapper.find('[data-od-id="roster-join-mem-2"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('[data-od-id="roster-join-mem-2"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="roster-member-mem-2"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="roster-joined"] .dock-group-label').text()).toBe('已加入（3）');
    expect(wrapper.find('[data-od-id="roster-not-joined"] .dock-group-label').text()).toBe('未加入本频道（0）');

    const posts = stub.to('/api/channels/ch-1/members').filter((call) => call.method === 'POST');
    expect(posts.length).toBe(1);
    expect(posts[0].body).toEqual({ member_id: 'mem-2' });
    // 「不刷新页面」的机械判据：不重取工作台快照、不重取消息流。
    expect(countCalls(stub, '/api/workspace')).toBe(workspaceCalls);
    expect(countCalls(stub, '/api/channels/ch-1/messages')).toBe(messageCalls);
  });

  test('加入成功后 `@` 补全立刻能选到它（响应里的成员表覆盖了缓存）', async () => {
    const { wrapper } = await mountApp();
    await openRoster(wrapper);
    await wrapper.find('[data-od-id="roster-join-mem-2"]').trigger('click');
    await flush();
    await flush();

    // 关掉名册（页脚那个唯一按钮），回到 composer。
    await wrapper.find('[data-od-id="roster-done"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="roster-body"]').isVisible()).toBe(false);

    const textarea = wrapper.find('textarea');
    await textarea.setValue('@');
    await textarea.trigger('input');
    expect(wrapper.find('[data-od-id="mention-option-mem-2"]').exists()).toBe(true);
  });

  test('成员表读不到：两段各说「读不到」，不把「读不到」说成「没有成员」', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/members': () => jsonResponse({ error: { code: 'boom', message: '后端读取失败' } }, 500),
    });
    await openRoster(wrapper);

    expect(wrapper.find('[data-od-id="roster-joined"]').text()).toContain('暂时读不到该频道的成员表');
    expect(wrapper.find('[data-od-id="roster-not-joined"]').text()).toContain('成员表读不到时暂不列「未加入」');
    expect(wrapper.find('[data-od-id="roster-join-mem-2"]').exists()).toBe(false);
  });
});

/* ─── 三、频道配置 ──────────────────────────────────────────────────────── */

describe('频道设置（弹窗）', () => {
  async function openConfig(wrapper) {
    await wrapper.find('[data-od-id="btn-channel-settings"]').trigger('click');
    await flush();
    await flush();
  }

  test('打开即铺后端读数：mode / team_scope / 队长可用性 / 项目根 / 后端 / 权限档', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/channels/ch-1/config': () =>
        jsonResponse({
          config: config({
            team_leader_member_id: 'mem-1',
            leader_available: false,
            leader_reason: '队长已被停用',
            workspace_root: '/tmp/project',
            workspace_backend: 'docker',
            permission_mode: 'read-only',
          }),
        }),
    });
    await openConfig(wrapper);

    expect(wrapper.find('[data-od-id="channel-config-body"]').isVisible()).toBe(true);
    expect(stub.to('/api/channels/ch-1/config').length).toBeGreaterThan(0);

    // `team_scope` 是**后端只读事实**：固定展示 channel+task，界面不拼取值。
    expect(wrapper.find('[data-od-id="channel-config-team-scope"]').text()).toBe('channel+task');
    // 队长可用性与原因直接读后端（与频道静默时的裁决同一份事实）。
    expect(wrapper.find('[data-od-id="channel-config-leader-availability"]').text()).toBe('队长不可用');
    expect(wrapper.find('[data-od-id="channel-config-leader-reason"]').text()).toContain('队长已被停用');
    expect(wrapper.find('[data-od-id="channel-config-workspace-root"]').element.value).toBe('/tmp/project');
    expect(wrapper.find('[data-od-id="channel-config-save"]').exists()).toBe(true);
  });

  test('team 模式下两条停用提示出现；切回 autonomous 后消失（还没保存）', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/config': () =>
        jsonResponse({ config: config({ mode: 'team', team_leader_member_id: 'mem-1', leader_available: true }) }),
    });
    await openConfig(wrapper);

    const notices = () => wrapper.find('[data-od-id="channel-config-team-notices"]');
    expect(notices().exists()).toBe(true);
    expect(notices().text()).toContain('成员 auto_claim 已停用');
    expect(notices().text()).toContain('任务线程只唤醒队长');

    await wrapper.find('[data-config-mode="autonomous"]').trigger('click');
    await flush();
    expect(notices().exists()).toBe(false);
  });

  test('保存提交整份配置字段；成功后按响应重铺', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/channels/ch-1/config': (url, request) => {
        if ((request.method || 'GET') === 'POST') return jsonResponse({ config: config() });
        if ((request.method || 'GET') === 'PATCH') {
          return jsonResponse({
            config: config({ mode: 'team', team_leader_member_id: 'mem-1', leader_available: true }),
          });
        }
        return jsonResponse({ config: config() });
      },
    });
    await openConfig(wrapper);

    await wrapper.find('[data-config-mode="team"]').trigger('click');
    // 队长候选来自该频道的成员表（`mem-1` 是成员，`mem-2` 还不是——因此只能选 `mem-1`）。
    await wrapper.find('[data-od-id="channel-config-leader"]').setValue('mem-1');
    await wrapper.find('[data-config-save]').trigger('click');
    await flush();
    await flush();

    const patches = stub.to('/api/channels/ch-1/config').filter((call) => call.method === 'PATCH');
    expect(patches.length).toBe(1);
    expect(patches[0].body).toEqual({
      mode: 'team',
      team_leader_member_id: 'mem-1',
      workspace_root: null,
      workspace_backend: 'local',
      permission_mode: 'ask-writes',
      web_access: 'search',
    });
    // 响应里的新配置铺回去：team 的两条提示随之出现。
    expect(wrapper.find('[data-od-id="channel-config-team-notices"]').exists()).toBe(true);
  });

  test('后端拒绝非法字段：只显示后端可读原因，弹窗不关、界面不改口', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/config': (url, request) => {
        if ((request.method || 'GET') === 'PATCH') {
          return jsonResponse(
            {
              error: {
                code: 'validation_failed',
                message: '频道配置不合法，未保存任何改动',
                fields: { workspace_root: '必须是绝对路径' },
              },
            },
            400,
          );
        }
        return jsonResponse({ config: config() });
      },
    });
    await openConfig(wrapper);

    await wrapper.find('[data-od-id="channel-config-workspace-root"]').setValue('relative/path');
    await wrapper.find('[data-config-save]').trigger('click');
    await flush();
    await flush();

    const error = wrapper.find('[data-od-id="channel-config-form-error"]');
    expect(error.isVisible()).toBe(true);
    expect(error.text()).toContain('频道配置不合法');
    expect(error.text()).toContain('必须是绝对路径');
    // 弹窗仍然开着（人能看到失败原因并改），草稿也没被后端读数冲掉。
    expect(wrapper.find('[data-od-id="channel-config-body"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-od-id="channel-config-workspace-root"]').element.value).toBe('relative/path');
  });

  test('配置读不到：只给一行「读不到」，不铺半份表单', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/config': () => jsonResponse({ error: { code: 'boom', message: '后端读取失败' } }, 500),
    });
    await openConfig(wrapper);

    expect(wrapper.find('[data-od-id="channel-config-body"]').text()).toContain('暂时读不到该频道的配置');
    expect(wrapper.find('[data-od-id="channel-config-save"]').exists()).toBe(false);
  });
});

/* ─── 四、回应预算 ──────────────────────────────────────────────────────── */

describe('回应预算（弹窗）', () => {
  async function openBudget(wrapper) {
    await wrapper.find('[data-od-id="btn-channel-budget"]').trigger('click');
    await flush();
    await flush();
  }

  test('四项读数分开呈现，全部来自后端载荷（已用 / 剩余 / 有效上界 / 来源）', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/budget': () =>
        jsonResponse({ budget: budget({ limit: 5, used: 1, remaining: 4, source: 'channel-override' }) }),
    });
    await openBudget(wrapper);

    expect(wrapper.find('[data-od-id="budget-body"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-od-id="budget-used"]').text()).toBe('1');
    expect(wrapper.find('[data-od-id="budget-remaining"]').text()).toBe('4');
    expect(wrapper.find('[data-od-id="budget-limit"]').text()).toBe('5');
    // 来源是机器可读取值（`channel-override` / `built-in-default`）的界面说法，不是裸词。
    expect(wrapper.find('[data-od-id="budget-source"]').text()).toBe('频道覆盖');
  });

  test('下一世代待生效值与当前有效上界**分开**呈现，并写明从下一个人类世代生效', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/budget': () =>
        jsonResponse({ budget: budget({ limit: 3, used: 1, remaining: 2, pending_limit: 10 }) }),
    });
    await openBudget(wrapper);

    expect(wrapper.find('[data-od-id="budget-limit"]').text()).toBe('3');
    expect(wrapper.find('[data-od-id="budget-remaining"]').text()).toBe('2');
    expect(wrapper.find('[data-od-id="budget-pending"]').text()).toBe('10');
    expect(wrapper.find('[data-od-id="budget-pending-note"]').text()).toContain('从下一个人类世代生效');
    // 两段各在自己的 section 里（不是同一行）。
    expect(wrapper.find('[data-od-id="budget-section-current"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="budget-section-next"]').exists()).toBe(true);
  });

  test('写控件只有 3 / 5 / 10 三档，提交的只有 {budget_limit}', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/channels/ch-1/budget': (url, request) => {
        if ((request.method || 'GET') === 'PATCH') {
          return jsonResponse({ budget: budget({ pending_limit: 5 }) });
        }
        return jsonResponse({ budget: budget() });
      },
    });
    await openBudget(wrapper);

    const tiers = wrapper.findAll('[data-budget-tier]').map((node) => node.attributes('data-budget-tier'));
    expect(tiers).toEqual(['3', '5', '10']);
    expect(wrapper.find('[data-budget-tier="3"]').attributes('aria-pressed')).toBe('true');

    await wrapper.find('[data-budget-tier="5"]').trigger('click');
    expect(wrapper.find('[data-budget-tier="5"]').attributes('aria-pressed')).toBe('true');
    expect(wrapper.find('[data-budget-tier="3"]').attributes('aria-pressed')).toBe('false');

    await wrapper.find('[data-budget-save]').trigger('click');
    await flush();
    await flush();

    const patches = stub.to('/api/channels/ch-1/budget').filter((call) => call.method === 'PATCH');
    expect(patches.length).toBe(1);
    expect(patches[0].body).toEqual({ budget_limit: 5 });
  });

  test('保存成功后按响应里的新读数重铺，并提示从下一个人类世代生效', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/budget': (url, request) => {
        if ((request.method || 'GET') === 'PATCH') {
          return jsonResponse({ budget: budget({ limit: 3, used: 1, remaining: 2, pending_limit: 10 }) });
        }
        return jsonResponse({ budget: budget({ limit: 3, used: 1, remaining: 2 }) });
      },
    });
    await openBudget(wrapper);

    await wrapper.find('[data-budget-tier="10"]').trigger('click');
    await wrapper.find('[data-budget-save]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('[data-od-id="budget-pending"]').text()).toBe('10');
    expect(wrapper.find('[data-od-id="budget-limit"]').text()).toBe('3');
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('下一个人类世代生效');
    expect(wrapper.find('[data-od-id="budget-form-error"]').exists()).toBe(false);
  });

  test('后端拒绝非法档位：错误行给后端可读原因，读数保持后端说的那一份', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/budget': (url, request) => {
        if ((request.method || 'GET') === 'PATCH') {
          return jsonResponse(
            {
              error: {
                code: 'validation_failed',
                message: '回应预算上界只接受固定档位：3, 5, 10，未保存任何改动',
                fields: { budget_limit: '必须是这些之一：3, 5, 10' },
              },
            },
            400,
          );
        }
        return jsonResponse({ budget: budget() });
      },
    });
    await openBudget(wrapper);

    await wrapper.find('[data-budget-tier="10"]').trigger('click');
    await wrapper.find('[data-budget-save]').trigger('click');
    await flush();
    await flush();

    const error = wrapper.find('[data-od-id="budget-form-error"]');
    expect(error.isVisible()).toBe(true);
    expect(error.text()).toContain('只接受固定档位');
    expect(wrapper.find('[data-od-id="budget-pending"]').text()).toBe('3');
  });

  test('预算耗尽是可读事实，不在错误区（断言 DOM 位置，不断言文案实现）', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/budget': () =>
        jsonResponse({ budget: budget({ limit: 3, used: 3, remaining: 0 }) }),
    });
    await openBudget(wrapper);

    const exhausted = wrapper.find('[data-od-id="budget-exhausted"]');
    expect(exhausted.exists()).toBe(true);
    expect(exhausted.text()).toBe('预算耗尽');
    // 它在**当前世代**那一段里，不在错误行里；错误行这一帧根本不存在。
    expect(wrapper.find('[data-od-id="budget-section-current"]').element.contains(exhausted.element)).toBe(true);
    expect(wrapper.find('[data-od-id="budget-form-error"]').exists()).toBe(false);
  });

  test('预算读数读不到：只给一行「读不到」，不铺半份读数', async () => {
    const { wrapper } = await mountApp({
      '/api/channels/ch-1/budget': () => jsonResponse({ error: { code: 'boom', message: '后端读取失败' } }, 500),
    });
    await openBudget(wrapper);

    expect(wrapper.find('[data-od-id="budget-body"]').text()).toContain('暂时读不到该频道的预算读数');
    expect(wrapper.find('[data-budget-tier="3"]').exists()).toBe(false);
  });
});
