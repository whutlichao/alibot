/* 频道设置里的联网 provider 凭据段（票 #320）

   端点早已存在（`GET/PUT/DELETE /api/web-providers/{provider}/credential`、
   `POST /api/web-providers/{provider}/consent`），本票只补 v2 的界面入口。

   为什么**从 `App.vue` 进**（不是直挂 `ChannelConfigModal`）：凭据段的读数要经过
   「HTTP 载荷 → workspace store → 弹窗 prop → 段落」这条链，打开动作也由频道头的设置按钮触发；
   叶子组件上各有各的 prop 会各自都过，只有从真实父组件进才看得见「读数没有接上」这类断链。

   判据来源：
   - 旧页面 `studio/app.js`（`git show 635ed8f:app/alibot/studio/app.js`）的
     `renderWebProviders()` / `webProviderRow()` 族：一行 = 名称 + 掩码状态 + password 输入 +
     保存 / 设为默认 / 清除，落点 `web-provider-*`；`data-od-id` 逐字沿用。
   - ADR-0026 §四：未确认数据面 ⇒ 不装配带凭据工具，因此每行还要报确认态并给确认入口。
   - `app/alibot/api.py` 的 `WebProviderCredentialUpdate`：`api_key` 缺省 = 「这次不换 key，
     只切默认」；显式传值必须非空。

   明文 key 只在写入方向出现：本文件断言读回永远是掩码、保存成功后输入框被清空、
   DOM 文本里找不到原值。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1, role: '工程师' };

/** 一把完整 key 与它的掩码形态（掩码是后端产生的读数，用例只消费它）。 */
const SECRET = 'sk-1234567890abcd';
const MASKED = 'sk-…abcd';

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
      web_access: 'search',
      docker_egress: false,
    },
    overrides || {},
  );
}

/** 一家 provider 的掩码读数（`GET /api/web-providers` 的载荷形状）。 */
function provider(providerId, overrides) {
  return Object.assign(
    {
      provider: providerId,
      configured: false,
      masked: '',
      is_default: false,
      updated_at: '',
      env_configured: false,
    },
    overrides || {},
  );
}

/** 五家的默认读数：全未配置 ⇒ 当前 provider 是免凭据回退。 */
function providersPayload(overrides) {
  return Object.assign(
    {
      providers: ['brave', 'tavily', 'perplexity', 'jina', 'exa'].map((id) => provider(id)),
      active: { provider: 'exa-mcp', source: 'fallback' },
    },
    overrides || {},
  );
}

/** 一次成功的工作台载入 + 频道配置读数的路由表。 */
function routes(overrides) {
  return Object.assign(
    {
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
      '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
      '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA] }),
      '/api/channels/ch-1/config': () => jsonResponse({ config: config() }),
      '/api/member-statuses': () => jsonResponse({ statuses: {} }),
      '/api/remote-data-flow': () => jsonResponse({ data_faces: [], web_consents: [] }),
      '/api/web-providers': () => jsonResponse(providersPayload()),
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

async function openConfig(wrapper) {
  await wrapper.find('[data-od-id="btn-channel-settings"]').trigger('click');
  await flush();
  await flush();
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
  localStorage.clear();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = '';
});

describe('联网 provider 凭据段', () => {
  test('出现在联网档位卡之后，五行各报掩码 / 未配置 / 默认 / 数据面确认态', async () => {
    const { wrapper } = await mountApp({
      '/api/web-providers': () =>
        jsonResponse(
          providersPayload({
            providers: [
              provider('brave', { configured: true, masked: MASKED, is_default: true }),
              provider('tavily'),
              provider('perplexity', { configured: true, masked: 'pplx…1234', env_configured: true }),
              provider('jina'),
              provider('exa'),
            ],
            active: { provider: 'brave', source: 'ui' },
          }),
        ),
      '/api/remote-data-flow': () =>
        jsonResponse({
          data_faces: [],
          web_consents: [{ scope_kind: 'web', scope_id: 'brave', acknowledged_at: '2026-10-20T09:00:00Z' }],
        }),
    });
    await openConfig(wrapper);

    const section = wrapper.find('[data-od-id="channel-config-section-web-providers"]');
    expect(section.exists()).toBe(true);

    // 位置：凭据段在联网档位卡（`channel-config-section-web`）之后，而不是之前。
    const levels = wrapper.find('[data-od-id="channel-config-section-web"]').element;
    expect(levels.compareDocumentPosition(section.element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // 当前生效 provider 与来源：后端读数，界面不推算。
    expect(wrapper.find('[data-od-id="channel-config-web-active"]').text()).toContain('Brave');
    expect(wrapper.find('[data-od-id="channel-config-web-active"]').text()).toContain('界面配置');

    // 已配置：掩码 + 默认徽标 + 数据面已确认。
    expect(wrapper.find('[data-od-id="web-provider-status-brave"]').text()).toContain(MASKED);
    expect(wrapper.find('[data-od-id="web-provider-default-brave"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="web-provider-consent-status-brave"]').text()).toContain('已确认');

    // 未配置：说明「未配置」，没有默认徽标，数据面未确认并给出「未确认不会装配」的解释。
    expect(wrapper.find('[data-od-id="web-provider-status-tavily"]').text()).toContain('未配置');
    expect(wrapper.find('[data-od-id="web-provider-default-tavily"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="web-provider-consent-status-tavily"]').text()).toContain('未确认');
    expect(wrapper.find('[data-od-id="web-provider-consent-hint-tavily"]').text()).toContain('不会装配');

    // 环境变量兜底是**读数**，不改「库内是否配置」。
    expect(wrapper.find('[data-od-id="web-provider-status-perplexity"]').text()).toContain('pplx…1234');

    // 明文从不进读回方向。
    expect(wrapper.text()).not.toContain(SECRET);
  });

  test('保存 key ⇒ PUT 带 api_key；响应只回掩码，输入框随之清空（明文不进 state / DOM）', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/web-providers': (url, request) => {
        if ((request.method || 'GET') === 'PUT') {
          // 后端写路径的载荷形状：credential + providers + active，全部只有掩码。
          return jsonResponse(
            providersPayload({
              providers: [
                provider('brave', { configured: true, masked: MASKED, is_default: true }),
                provider('tavily'),
                provider('perplexity'),
                provider('jina'),
                provider('exa'),
              ],
              active: { provider: 'brave', source: 'ui' },
            }),
          );
        }
        return jsonResponse(providersPayload());
      },
      '/api/web-providers/brave/credential': () =>
        jsonResponse(
          providersPayload({
            providers: [
              provider('brave', { configured: true, masked: MASKED, is_default: true }),
              provider('tavily'),
              provider('perplexity'),
              provider('jina'),
              provider('exa'),
            ],
            active: { provider: 'brave', source: 'ui' },
          }),
        ),
    });
    await openConfig(wrapper);

    const input = () => wrapper.find('[data-od-id="web-provider-key-brave"]');
    // 空草稿时保存：先拦一次，不发请求（与后端「显式传值必须非空」同一句提示）。
    await wrapper.find('[data-od-id="web-provider-save-brave"]').trigger('click');
    await flush();
    expect(stub.to('/api/web-providers/brave/credential').length).toBe(0);
    expect(wrapper.find('[data-od-id="web-provider-error-brave"]').text()).toContain('非空');

    await input().setValue(SECRET);
    await wrapper.find('[data-od-id="web-provider-save-brave"]').trigger('click');
    await flush();
    await flush();

    const puts = stub.to('/api/web-providers/brave/credential').filter((call) => call.method === 'PUT');
    expect(puts.length).toBe(1);
    // 保存 key 的请求**带** `api_key`，且不带 `is_default`（换 key 不悄悄改默认）。
    expect(puts[0].body).toEqual({ api_key: SECRET });

    // 读回只有掩码；输入框清空（明文不留在 state 里）。
    expect(wrapper.find('[data-od-id="web-provider-status-brave"]').text()).toContain(MASKED);
    expect(input().element.value).toBe('');
    expect(input().attributes('placeholder')).toBe(MASKED);
    expect(wrapper.html()).not.toContain(SECRET);
  });

  test('「设为默认」的 PUT **不带** api_key（只切默认，不换 key）', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/web-providers': () =>
        jsonResponse(
          providersPayload({
            providers: [
              provider('brave', { configured: true, masked: MASKED, is_default: false }),
              provider('tavily', { configured: true, masked: 'tav…1234', is_default: true }),
              provider('perplexity'),
              provider('jina'),
              provider('exa'),
            ],
            active: { provider: 'tavily', source: 'ui' },
          }),
        ),
      '/api/web-providers/brave/credential': () =>
        jsonResponse(
          providersPayload({
            providers: [
              provider('brave', { configured: true, masked: MASKED, is_default: true }),
              provider('tavily', { configured: true, masked: 'tav…1234', is_default: false }),
              provider('perplexity'),
              provider('jina'),
              provider('exa'),
            ],
            active: { provider: 'brave', source: 'ui' },
          }),
        ),
    });
    await openConfig(wrapper);

    // 已配置且不是默认 ⇒ 可以点；已在默认上的那一家按钮禁用。
    const makeDefault = () => wrapper.find('[data-od-id="web-provider-make-default-brave"]');
    expect(makeDefault().element.disabled).toBe(false);
    expect(wrapper.find('[data-od-id="web-provider-make-default-tavily"]').element.disabled).toBe(true);

    await makeDefault().trigger('click');
    await flush();
    await flush();

    const puts = stub.to('/api/web-providers/brave/credential').filter((call) => call.method === 'PUT');
    expect(puts.length).toBe(1);
    // 「这次不换 key，只切默认」：请求体里**没有** `api_key` 这个键。
    expect(puts[0].body).toEqual({ is_default: true });
    expect('api_key' in puts[0].body).toBe(false);

    // 响应里的新读数铺回去：默认徽标挪到 brave。
    expect(wrapper.find('[data-od-id="web-provider-default-brave"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="web-provider-default-tavily"]').exists()).toBe(false);
    expect(makeDefault().element.disabled).toBe(true);
  });

  test('清空要二次确认：先出现行内确认条，确认后才发 DELETE（请求体为空）', async () => {
    const configured = providersPayload({
      providers: [
        provider('brave', { configured: true, masked: MASKED, is_default: true }),
        provider('tavily'),
        provider('perplexity'),
        provider('jina'),
        provider('exa'),
      ],
      active: { provider: 'brave', source: 'ui' },
    });
    const cleared = providersPayload();
    const { wrapper, stub } = await mountApp({
      '/api/web-providers': () => jsonResponse(configured),
      '/api/web-providers/brave/credential': (url, request) => {
        if ((request.method || 'GET') === 'DELETE') return jsonResponse(cleared);
        return jsonResponse(configured);
      },
    });
    await openConfig(wrapper);

    // 第一下只出确认条，不发请求。
    await wrapper.find('[data-od-id="web-provider-clear-brave"]').trigger('click');
    await flush();
    expect(stub.to('/api/web-providers/brave/credential').filter((call) => call.method === 'DELETE').length).toBe(0);
    const confirmRow = () => wrapper.find('[data-od-id="web-provider-clear-confirm-brave"]');
    expect(confirmRow().exists()).toBe(true);
    expect(confirmRow().text()).toContain('清空 Brave 的凭据');

    // 取消：确认条收回、仍没有请求。
    await wrapper.find('[data-od-id="web-provider-clear-cancel-brave"]').trigger('click');
    await flush();
    expect(confirmRow().exists()).toBe(false);

    // 再来一次并确认：DELETE 发出，请求体为空（清空不带任何字段）。
    await wrapper.find('[data-od-id="web-provider-clear-brave"]').trigger('click');
    await flush();
    await wrapper.find('[data-od-id="web-provider-clear-confirm-action-brave"]').trigger('click');
    await flush();
    await flush();

    const deletes = stub.to('/api/web-providers/brave/credential').filter((call) => call.method === 'DELETE');
    expect(deletes.length).toBe(1);
    expect(deletes[0].body).toBe(null);

    // 响应里的新读数铺回去：未配置、默认徽标消失、清空按钮禁用。
    expect(wrapper.find('[data-od-id="web-provider-status-brave"]').text()).toContain('未配置');
    expect(wrapper.find('[data-od-id="web-provider-default-brave"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="web-provider-clear-brave"]').element.disabled).toBe(true);
    expect(confirmRow().exists()).toBe(false);
  });

  test('未确认数据面：说清「不会装配带凭据工具」并就地给确认入口，确认后转「已确认」', async () => {
    const configured = providersPayload({
      providers: [
        provider('brave', { configured: true, masked: MASKED, is_default: true }),
        provider('tavily'),
        provider('perplexity'),
        provider('jina'),
        provider('exa'),
      ],
      active: { provider: 'brave', source: 'ui' },
    });
    let consents = [];
    const { wrapper, stub } = await mountApp({
      '/api/web-providers': () => jsonResponse(configured),
      '/api/web-providers/brave/consent': () => {
        consents = [{ scope_kind: 'web', scope_id: 'brave', acknowledged_at: '2026-10-20T09:00:00Z' }];
        return jsonResponse({ consent: consents[0] });
      },
      '/api/remote-data-flow': () => jsonResponse({ data_faces: [], web_consents: consents }),
    });
    await openConfig(wrapper);

    // 未确认：状态说「未确认」，解释句说明未确认不会装配，确认入口在行内。
    expect(wrapper.find('[data-od-id="web-provider-consent-status-brave"]').text()).toContain('未确认');
    expect(wrapper.find('[data-od-id="web-provider-consent-hint-brave"]').text()).toContain('不会装配');
    const consentButton = () => wrapper.find('[data-od-id="web-provider-consent-brave"]');
    expect(consentButton().exists()).toBe(true);

    await consentButton().trigger('click');
    await flush();
    await flush();

    // 确认是幂等的一次 POST（不带请求体）。
    const posts = stub.to('/api/web-providers/brave/consent').filter((call) => call.method === 'POST');
    expect(posts.length).toBe(1);
    expect(posts[0].body).toBe(null);

    // 读回的确认态铺回去：转「已确认」，入口与解释句收起。
    expect(wrapper.find('[data-od-id="web-provider-consent-status-brave"]').text()).toContain('已确认');
    expect(wrapper.find('[data-od-id="web-provider-consent-hint-brave"]').exists()).toBe(false);
    expect(consentButton().exists()).toBe(false);
  });

  test('provider 读数读不到：这一段只给一行原因，不铺半份凭据表、也不冒充「未配置」', async () => {
    const { wrapper } = await mountApp({
      '/api/web-providers': () => jsonResponse({ error: { code: 'boom', message: '后端读取失败' } }, 500),
    });
    await openConfig(wrapper);

    const error = wrapper.find('[data-od-id="channel-config-web-providers-error"]');
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain('暂时读不到联网 provider 状态');
    expect(error.text()).toContain('后端读取失败');
    // 档位卡本身仍在（那一段的读数来自频道配置，与 provider 表是两份读数）。
    expect(wrapper.find('[data-od-id="channel-config-web-access-search"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="web-provider-brave"]').exists()).toBe(false);
  });
});
