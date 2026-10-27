/* Agent 详情（主线视图的第四档）——票 #310 / 阶段 C

   为什么从 `App.vue` 进（而不是直挂 `AgentDetailView`）：本票最容易被单测漏掉的三件事都在父组件里——
   ① `mode === 'agent'` 这一档的**接线**（rail 的 Agent 行 → 进详情 → dock 让位）；
   ② ESC 层序里它该在哪一层（`handleEscape()` 只有一份，`App.vue` 裁决）；
   ③ 保存条的**跨页签可见**（它是页面级的，不在页签面板里）。
   直挂叶子组件只能证明「面板自己长对了」。

   装置照 `app-shell.test.js`：`fetch` 替身换 `globalThis.fetch`、事件源用 FakeEventSource、
   `attachTo: document.body`（`ModalShell` 的焦点进入 / 回收与 ESC 都需要真实 DOM 落点）。 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const App = (await import('../src/App.vue')).default;

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA_ROW = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

/** 后端正本（`GET /api/agents/{id}`）的读数：`stats` 是四个概览数字的唯一来源。 */
function ada() {
  return Object.assign({}, ADA_ROW, {
    role: '前端工程师',
    model_id: 'ollama:qwen2.5',
    provider: 'ollama',
    is_remote: false,
    system_prompt: '你是助手',
    enabled: true,
    auto_claim: false,
    web_access: null,
    status: 'idle',
    enabled_reason: '',
    // 库内既有的上下文三项：界面不再渲染，值也必须原样留在后端（规格 D2）。
    context_window_messages: 40,
    context_trigger_ratio: 0.7,
    react_max_iters: 30,
    stats: { channel_count: 2, round_count: 7, completed_task_count: 3, success_rate: 0.75 },
  });
}

/** 一个技能行（列表载荷：只有元数据与 hash，**不含字节**）。 */
const SKILL = {
  id: 'skl-1',
  scope_kind: 'agent',
  scope_id: 'mem-1',
  name: 'pdf-tools',
  description: '处理 PDF 的技能',
  source_path: '/Users/someone/.agents/skills/pdf-tools',
  content_hash: 'abcdef0123456789abcdef0123456789',
  enabled: true,
};

/** SKILL.md 正文的**独立真值**：用例断言「界面上的正文与后端返回的这份逐字相等」。 */
const SKILL_BODY = '# pdf-tools\n\n1. 先读 front matter 之后的那段\n2. 保留换行与缩进\n';

/** 家目录 `mcp.json` 的原文（保存后必须**逐字**回传，不重排、不重缩进）。 */
const MCP_FILE_TEXT = '{\n  "servers": [\n    { "name": "fs", "transport": "stdio", "command": "npx" }\n  ]\n}\n';

function channel(overrides) {
  return Object.assign(
    { id: 'ch-1', name: 'design', description: '设计频道', last_seq: 0, last_read_seq: 0, unread_count: 0 },
    overrides || {},
  );
}

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

function routes(extra) {
  return Object.assign(
    {
      '/api/settings': () =>
        jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA_ROW],
          channels: [channel()],
          dms: [],
          agents: [ada()],
          model_providers: ['ollama', 'dashscope'],
          local_providers: ['ollama'],
          remote_consents: [],
        }),
      '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
      '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
      '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER, ADA_ROW] }),
      '/api/channels/ch-1/config': () =>
        jsonResponse({ config: { channel_id: 'ch-1', mode: 'autonomous', team_leader_member_id: null } }),
      '/api/member-statuses': () => jsonResponse({ statuses: {} }),
      '/api/remote-data-flow': () => jsonResponse({ data_faces: ['消息正文', '任务描述'] }),
      '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
      '/api/targets/ch-1/team-runs?limit=20': () => jsonResponse({ runs: [] }),
      '/api/targets/ch-1/plan': () => jsonResponse({ plan: { members: [] } }),
      // 详情的五条读路径（旧 `openAgentDetail()` 的 `refreshAgentDetail()` + `loadAgentCapabilities()`）。
      '/api/agents/mem-1': () => jsonResponse({ agent: ada() }),
      '/api/mcp-servers?scope_kind=agent&scope_id=mem-1': () =>
        jsonResponse({
          mcp_servers: [
            {
              id: 'mcp-1',
              name: 'filesystem',
              transport: 'stdio',
              command: 'npx',
              args: ['-y', '@modelcontextprotocol/server-filesystem'],
              url: '',
              env_names: ['GITHUB_TOKEN'],
              enabled: true,
              is_remote: false,
              consent_required: false,
              consented: true,
              runtime: null,
            },
          ],
        }),
      '/api/skills?scope_kind=agent&scope_id=mem-1': () => jsonResponse({ skills: [SKILL] }),
      '/api/skills/candidates?scope_kind=agent&scope_id=mem-1': () =>
        jsonResponse({
          source: '/Users/someone/.agents/skills',
          available: true,
          candidates: [
            { name: 'pdf-tools', path: '/Users/someone/.agents/skills/pdf-tools', importable: true, installed: true, reason: '' },
            { name: 'chart-maker', path: '/Users/someone/.agents/skills/chart-maker', importable: true, installed: false, reason: '' },
            { name: 'broken-one', path: '/Users/someone/.agents/skills/broken-one', importable: false, installed: false, reason: '缺少 SKILL.md' },
          ],
        }),
      '/api/skills/skl-1/markdown': () =>
        jsonResponse({ skill: SKILL, markdown: SKILL_BODY, state: 'ok' }),
      '/api/agents/mem-1/global-mcp-file': () =>
        jsonResponse({ member_id: 'mem-1', exists: true, text: MCP_FILE_TEXT, servers: 1 }),
      '/api/agents/mem-1/global-mcp-servers': () => jsonResponse({ member_id: 'mem-1', global_mcp_servers: [] }),
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

/** 从 rail 的 Agent 行进详情（本票唯一的入口），等五条读数落地。 */
async function openDetail(wrapper) {
  await wrapper.find('[data-od-id="agent-mem-1"]').trigger('click');
  await flush();
  await flush();
  await flush();
}

/** 页签按 `data-adtab` 认（顺序即呈现顺序）。 */
function tabIds(wrapper) {
  return wrapper.findAll('[data-adtab]').map((node) => node.attributes('data-adtab'));
}

/** 一个可被测试推进的「在途请求」：用来断言确认期间重复点击只发一次。 */
function deferred() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

/** 一个面板子树里的 `data-od-id` 顺序（区块归属的唯一可观察判据）。 */
function odIdsIn(wrapper, panel) {
  return wrapper
    .findAll(`[data-adpanel="${panel}"] [data-od-id]`)
    .map((node) => node.attributes('data-od-id'));
}

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

describe('入口与页签骨架', () => {
  test('rail 的 Agent 行可点 ⇒ 进详情；三个页签顺序固定、默认停在第一页', async () => {
    const { wrapper } = await mountApp();
    // 进详情之前它不在（详情是主线视图，不是常驻列）。
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(false);

    await openDetail(wrapper);

    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(true);
    expect(wrapper.find('#adName').text()).toBe('Ada');
    expect(tabIds(wrapper)).toEqual(['base', 'skills', 'mcp']);
    // 默认停在「基础信息和配置」，其余两个面板不占版面（`v-show` 的 hidden）。
    expect(wrapper.find('[data-od-id="agent-detail-tab-base"]').attributes('aria-selected')).toBe('true');
    expect(wrapper.find('[data-od-id="agent-detail-tab-skills"]').attributes('aria-selected')).toBe('false');
    expect(wrapper.find('[data-adpanel="base"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-adpanel="skills"]').isVisible()).toBe(false);
    // 页签容器是自己的 `.ad-tabs`，**不复用** dock 的 `.dock-tabs`。
    expect(wrapper.find('.ad-tabs').exists()).toBe(true);
    expect(wrapper.find('.dock-tabs').exists()).toBe(false);
  });

  test('页签是可切换的 `role=tab`（键盘靠原生 button 的 Tab / Enter，不自造方向键漫游）', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);

    expect(wrapper.find('[role="tablist"]').exists()).toBe(true);
    const skills = wrapper.find('[data-od-id="agent-detail-tab-skills"]');
    expect(skills.element.tagName).toBe('BUTTON');
    expect(skills.attributes('role')).toBe('tab');
    expect(skills.attributes('aria-controls')).toBe('adPanelSkills');
    expect(wrapper.find('[data-adpanel="skills"]').attributes('role')).toBe('tabpanel');

    await skills.trigger('click');
    await flush();
    expect(skills.attributes('aria-selected')).toBe('true');
    expect(wrapper.find('[data-adpanel="skills"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-adpanel="base"]').isVisible()).toBe(false);
  });

  test('ESC 不关详情：它是主线视图，不在旧页面 ESC 层序的任何一层里（app.js:10577-10614）', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);

    await pressEscape();
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(true);
  });

  test('进入前的 dock 形态与开合在 closeAgentDetail() 后恢复（旧 closeAgentDetail() 同一条）', async () => {
    const { wrapper } = await mountApp({
      '/api/agents/mem-1': (_url, request) =>
        request.method === 'DELETE'
          ? jsonResponse({ deleted: 'mem-1', agents: [], members: [OWNER] })
          : jsonResponse({ agent: ada() }),
    });
    // 先进频道并打开运行面板（dock 的 run 档），此时 dock 是开着的。
    await wrapper.find('[data-od-id="btn-run-panel"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="detail-dock"]').exists()).toBe(true);

    await openDetail(wrapper);
    // 详情占主线：dock 让位。
    expect(wrapper.find('[data-od-id="detail-dock"]').exists()).toBe(false);

    // 删除当前成员是真正走 `closeAgentDetail()` 的那条路（旧 `deleteAgent()` 同一条）。
    await wrapper.find('[data-od-id="agent-delete"]').trigger('click');
    await wrapper.find('[data-od-id="agent-delete-confirm-action"]').trigger('click');
    await flush();
    await flush();
    // 进入前开着 ⇒ 连形态一起开回来（不是一张空面板）。
    expect(wrapper.find('[data-od-id="detail-dock"]').exists()).toBe(true);
  });

  test('详情头部「发送消息」在途时禁用，连点只发一条 POST /api/dms', async () => {
    const gate = deferred();
    const { wrapper, stub } = await mountApp({
      '/api/dms': async () => {
        await gate.promise;
        return jsonResponse({
          created: false,
          dm: { id: 'dm-1', kind: 'dm', agent_id: 'mem-1', peer: ADA_ROW, last_seq: 0, last_read_seq: 0, unread_count: 0 },
        });
      },
      '/api/dms/dm-1/messages': () =>
        jsonResponse({ dm: { id: 'dm-1', kind: 'dm', peer: ADA_ROW, unread_count: 0 }, messages: [] }),
    });
    await openDetail(wrapper);

    const send = wrapper.find('[data-od-id="btn-agent-send-message"]');
    await send.trigger('click');
    await send.trigger('click');
    expect(wrapper.find('[data-od-id="btn-agent-send-message"]').attributes('disabled')).toBeDefined();
    expect(stub.calls.filter((call) => call.url === '/api/dms' && call.method === 'POST')).toHaveLength(1);

    gate.release();
    await flush();
    await flush();
  });

  test('主读数读不到：说明原因，并用工作台镜像保住名称（旧页面同一条）', async () => {
    const { wrapper } = await mountApp({
      '/api/agents/mem-1': () => jsonResponse({ error: { message: '详情暂时不可用' } }, 500),
    });
    await wrapper.find('[data-od-id="agent-mem-1"]').trigger('click');
    await flush();
    await flush();
    await flush();

    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).toContain('详情暂时不可用');
    // 主读数没有可展示的正本，但工作台快照里这一行仍能读懂「这是谁」。
    expect(wrapper.find('#adName').text()).toBe('Ada');
  });

  test('任务线程的运行面板打开时进入详情，dock 与任务事件流都关闭', async () => {
    const task = {
      id: 'task-1', target_id: 'target-1', channel_id: 'ch-1', channel_name: 'design',
      title: '任务', status: 'todo', assignee_member_id: null, allowed_transitions: [],
      created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:00Z',
      message_count: 0, unread_count: 0,
    };
    const { wrapper } = await mountApp({
      '/api/tasks': () => jsonResponse({ tasks: [task] }),
      '/api/tasks/task-1/messages': () => jsonResponse({ task, messages: [] }),
      '/api/tasks/task-1/read': () => jsonResponse({ task }),
      '/api/targets/target-1/team-runs?limit=20&task_id=task-1': () => jsonResponse({ runs: [] }),
      '/api/targets/target-1/plan': () => jsonResponse({ plan: { members: [] } }),
    });
    await wrapper.find('[data-od-id="tab-tasks"]').trigger('click');
    await flush();
    await flush();
    await wrapper.find('[data-od-id="task-card-task-1"]').trigger('click');
    await flush();
    await flush();
    const taskStream = FakeEventSource.instances.find((source) => source.url.includes('/api/tasks/task-1/events'));
    expect(taskStream).toBeTruthy();
    await wrapper.find('[data-od-id="btn-thread-run-panel"]').trigger('click');
    await flush();
    await openDetail(wrapper);
    expect(wrapper.find('[data-od-id="detail-dock"]').exists()).toBe(false);
    expect(taskStream.closed).toBe(true);
  });

  test('离开详情再进入时，未提交的 MCP 库行表单不残留', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    await wrapper.find('[data-od-id="mcp-name"]').setValue('尚未保存');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
    await flush();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    expect(wrapper.find('[data-od-id="mcp-name"]').element.value).toBe('');
  });

  test('切到别的 target ⇒ 离开详情并回到消息流（旧 `leaveAgentView()`）', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="channel-ch-1"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="main-column"]').exists()).toBe(true);
  });
});

describe('基础信息和配置页签', () => {
  test('远端 provider 的数据流告知可用既有确认动作；确认后按后端读数收起', async () => {
    const gate = deferred();
    const { wrapper, stub } = await mountApp({
      '/api/remote-data-flow/ack': async () => {
        await gate.promise;
        return jsonResponse({
          consents: [{ provider: 'dashscope', acknowledged_at: '2026-10-27T00:00:00Z' }],
          agents: [ada()],
        });
      },
    });
    await openDetail(wrapper);
    await wrapper.find('[data-agent-field="provider"]').setValue('dashscope');
    await flush();

    const notice = wrapper.find('[data-od-id="agent-edit-remote-notice"]');
    expect(notice.exists()).toBe(true);
    expect(notice.text()).toContain('消息正文');
    expect(notice.text()).toContain('任务描述');
    const confirm = wrapper.find('[data-od-id="edit-remote-ack"]');
    await confirm.trigger('click');
    await confirm.trigger('click');
    expect(stub.calls.filter((call) => call.url === '/api/remote-data-flow/ack' && call.method === 'POST'))
      .toHaveLength(1);

    gate.release();
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="agent-edit-remote-notice"]').exists()).toBe(false);
  });

  test('数据面读不到时告知仍可见，确认动作禁用且说明原因', async () => {
    const { wrapper } = await mountApp({
      '/api/remote-data-flow': () => jsonResponse({ error: 'offline' }, 500),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-agent-field="provider"]').setValue('dashscope');
    await flush();

    expect(wrapper.find('[data-od-id="agent-edit-remote-notice"]').text()).toContain('还没读到会离开本机的数据面清单');
    expect(wrapper.find('[data-od-id="edit-remote-ack"]').attributes('disabled')).toBeDefined();
  });
  test('区块顺序 = 统计 → 系统提示 → 配置 → 危险操作（危险操作恒为最后一块）', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);

    const order = odIdsIn(wrapper, 'base').filter((id) => id.startsWith('agent-detail-section-'));
    expect(order).toEqual([
      'agent-detail-section-facts',
      'agent-detail-section-prompt',
      'agent-detail-section-config',
      'agent-detail-section-danger',
    ]);
  });

  test('上下文三项（条数 / 压缩阈值 / 迭代上限）在界面上不可见，也没有任何控件落在它们身上', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);

    for (const field of ['context_window_messages', 'context_trigger_ratio', 'react_max_iters']) {
      expect(wrapper.find(`[data-agent-field="${field}"]`).exists(), field).toBe(false);
      expect(wrapper.find(`#agentField-${field}`).exists(), field).toBe(false);
    }
    // 后端读数里的既有值一个字节都不动（撤的是界面入口，不是数据）。
    expect(wrapper.find('[data-adpanel="base"]').text()).not.toContain('压缩阈值');
    expect(wrapper.find('[data-adpanel="base"]').text()).not.toContain('迭代上限');
  });

  test('保存载荷是 7 个字段，且**不含**上下文三项（`PATCH` 是部分更新，不提交 ⇒ 库里既有值保持）', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/agents/mem-1': (_url, request) =>
        request.method === 'PATCH'
          ? jsonResponse({ agent: Object.assign(ada(), JSON.parse(request.body)) })
          : jsonResponse({ agent: ada() }),
    });
    await openDetail(wrapper);

    // 改一个字段 ⇒ 保存条出现（草稿 ≠ 后端读数）。
    const name = wrapper.find('[data-agent-field="display_name"]');
    await name.setValue('Ada2');
    await flush();
    expect(wrapper.find('#adSaveBar').exists()).toBe(true);

    await wrapper.find('[data-od-id="agent-detail-save"]').trigger('click');
    await flush();
    await flush();

    const patch = stub.calls.find((call) => call.url === '/api/agents/mem-1' && call.method === 'PATCH');
    expect(patch).toBeTruthy();
    expect(patch.body).toEqual({
      display_name: 'Ada2',
      role: '前端工程师',
      model_id: 'ollama:qwen2.5',
      system_prompt: '你是助手',
      enabled: true,
      auto_claim: false,
      web_access: null,
    });
    expect(Object.keys(patch.body)).toHaveLength(7);
    for (const field of ['context_window_messages', 'context_trigger_ratio', 'react_max_iters']) {
      expect(Object.prototype.hasOwnProperty.call(patch.body, field), field).toBe(false);
    }
  });

  test('保存成功后，左栏按响应中的成员名单显示新名字', async () => {
    const updated = { ...ada(), display_name: 'Ada 新名' };
    const { wrapper } = await mountApp({
      '/api/agents/mem-1': (_url, request) =>
        request.method === 'PATCH'
          ? jsonResponse({ agent: updated, agents: [updated], members: [OWNER, { ...ADA_ROW, display_name: 'Ada 新名' }] })
          : jsonResponse({ agent: ada() }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-agent-field="display_name"]').setValue('Ada 新名');
    await wrapper.find('[data-od-id="agent-detail-save"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="agent-mem-1"]').text()).toContain('Ada 新名');
  });

  test('在系统提示框打字时触发一次后台读数刷新（MCP 条目重读）：输入内容与焦点都不丢', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);

    const prompt = wrapper.find('[data-agent-field="system_prompt"]');
    await prompt.setValue('打到一半的提示');
    prompt.element.focus();
    expect(document.activeElement).toBe(prompt.element);

    // 一次后台读数刷新（局部换掉只读的 MCP 条目视图，不动草稿与持焦节点）。
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    await flush();
    await wrapper.find('[data-od-id="global-mcp-consent"]').exists();
    await wrapper.find('[data-od-id="agent-detail-tab-base"]').trigger('click');
    await flush();

    // 草稿没被冲掉，且那个 textarea 还是同一个节点（Vue 不重建，焦点不丢）。
    const after = wrapper.find('[data-agent-field="system_prompt"]');
    expect(after.element.value).toBe('打到一半的提示');
    expect(after.element).toBe(prompt.element);
  });

  test('切页签不丢草稿：保存条跨页签可见，切回来输入还在', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);

    await wrapper.find('[data-agent-field="system_prompt"]').setValue('输了一半的系统提示');
    await flush();
    expect(wrapper.find('#adSaveBar').exists()).toBe(true);

    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();
    // 保存条是页面级的：在 skills 页签里也看得见（人知道配置还有没保存的改动）。
    expect(wrapper.find('#adSaveBar').exists()).toBe(true);

    await wrapper.find('[data-od-id="agent-detail-tab-base"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-agent-field="system_prompt"]').element.value).toBe('输了一半的系统提示');
  });

  test('危险区的「暂停」只写草稿（不发请求），保存时才提交 enabled=false', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/agents/mem-1': (_url, request) =>
        request.method === 'PATCH'
          ? jsonResponse({ agent: Object.assign(ada(), JSON.parse(request.body)) })
          : jsonResponse({ agent: ada() }),
    });
    await openDetail(wrapper);

    await wrapper.find('[data-od-id="agent-toggle-enabled"]').trigger('click');
    await flush();
    expect(stub.calls.filter((call) => call.method === 'PATCH')).toHaveLength(0);
    expect(wrapper.find('#adSaveBar').exists()).toBe(true);

    await wrapper.find('[data-od-id="agent-detail-save"]').trigger('click');
    await flush();
    await flush();
    const patch = stub.calls.find((call) => call.url === '/api/agents/mem-1' && call.method === 'PATCH');
    expect(patch.body.enabled).toBe(false);
  });
});

describe('skills 页签', () => {
  test('双栏：左栏列表 + 右栏选中技能的元数据与 SKILL.md 正文（正文与后端返回逐字相等）', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();

    const panel = wrapper.find('[data-adpanel="skills"]');
    expect(panel.find('.agent-skill-left').exists()).toBe(true);
    expect(panel.find('.agent-skill-right').exists()).toBe(true);
    // 左栏一行 + 右栏详情。
    expect(panel.findAll('[data-od-id="capability-skill-row"]')).toHaveLength(1);
    expect(panel.find('[data-od-id="skill-detail-name"]').text()).toBe('pdf-tools');
    expect(panel.find('[data-od-id="skill-detail-description"]').text()).toBe('处理 PDF 的技能');
    expect(panel.find('[data-od-id="skill-detail-source"]').text()).toBe(SKILL.source_path);
    expect(panel.find('[data-od-id="skill-detail-hash"]').text()).toBe(SKILL.content_hash);
    expect(panel.find('[data-od-id="skill-detail-state"]').text()).toBe('已启用');
    // 正文按**纯文本**呈现：`<pre>` + 逐字相等的正文（不解析 Markdown，因此没有转义面）。
    const body = panel.find('[data-od-id="skill-detail-markdown"]');
    expect(body.element.tagName).toBe('PRE');
    expect(body.element.textContent).toBe(SKILL_BODY);
  });

  test('启停的唯一写入口是左栏行内的 `role=switch`；开关是文字区的**兄弟节点**（不重叠、不嵌套）', async () => {
    const { wrapper, stub } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();

    const row = wrapper.find('[data-od-id="capability-skill-row"]');
    const pick = row.find('[data-skill-pick]');
    const toggle = row.find('[data-skill-toggle]');
    expect(pick.element.tagName).toBe('BUTTON');
    expect(toggle.attributes('role')).toBe('switch');
    expect(toggle.attributes('aria-checked')).toBe('true');
    // 同一行两个控件物理不重叠、也不互相嵌套：兄弟节点，谁也不含谁。
    expect(toggle.element.parentElement).toBe(pick.element.parentElement);
    expect(pick.element.contains(toggle.element)).toBe(false);
    // 列表行不展示描述（右栏有完整值），只留 hash 前缀。
    expect(row.text()).not.toContain('处理 PDF 的技能');
    expect(row.text()).toContain(SKILL.content_hash.slice(0, 12));
    // 右栏只有一格**读数**，没有第二个启停入口。
    expect(wrapper.find('[data-adpanel="skills"] [data-od-id="skill-toggle"]').exists()).toBe(true);
    expect(wrapper.find('[data-adpanel="skills"] [data-od-id="skill-enable"]').exists()).toBe(false);

    await toggle.trigger('click');
    await flush();
    await flush();
    const patch = stub.calls.find((call) => call.url === '/api/skills/skl-1' && call.method === 'PATCH');
    expect(patch.body).toEqual({ enabled: false });
  });

  test('「从本机导入」是下拉框：三组候选、已安装与不可导入禁用、默认不预选、一次只导一个', async () => {
    const { wrapper, stub } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();

    const picker = wrapper.find('[data-od-id="skill-import-picker"]');
    expect(picker.element.tagName).toBe('SELECT');
    expect(picker.element.value).toBe(''); // 默认不预选
    expect(picker.find('optgroup[label="可选"]').exists()).toBe(true);
    expect(picker.find('optgroup[label="已安装"]').exists()).toBe(true);
    expect(picker.find('optgroup[label="不可导入"]').exists()).toBe(true);
    // 已安装 / 不可导入的候选是禁用的，且不可导入的把原因写在选项文本里。
    const options = picker.findAll('option');
    const installed = options.find((node) => node.attributes('value') === 'pdf-tools');
    const blocked = options.find((node) => node.attributes('value') === 'broken-one');
    expect(installed.attributes('disabled')).toBeDefined();
    expect(blocked.attributes('disabled')).toBeDefined();
    expect(blocked.text()).toContain('缺少 SKILL.md');

    await picker.setValue('chart-maker');
    await flush();
    await flush();
    const posts = stub.calls.filter((call) => call.url === '/api/skills' && call.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({
      scope_kind: 'agent',
      scope_id: 'mem-1',
      source_path: '/Users/someone/.agents/skills/chart-maker',
    });
  });

  test('手填绝对路径与上传 zip 两条安装入口不存在（后端分支仍在，只是没有界面入口）', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();

    for (const id of ['skill-source', 'skill-install-dir', 'skill-zip']) {
      expect(wrapper.find(`[data-od-id="${id}"]`).exists(), id).toBe(false);
    }
    const text = wrapper.find('[data-adpanel="skills"]').text();
    expect(text).not.toContain('上传 zip');
    expect(text).not.toContain('手填');
  });

  test('SKILL.md 缺失 ⇒ 与「读不到」不同的空态（不会被读成「这个技能是空的」）', async () => {
    const { wrapper } = await mountApp({
      '/api/skills/skl-1/markdown': () =>
        jsonResponse({ skill: SKILL, markdown: null, state: 'missing' }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();

    const state = wrapper.find('[data-od-id="skill-detail-body-state"]');
    expect(state.exists()).toBe(true);
    expect(state.attributes('data-detail-state')).toBe('missing');
    expect(state.text()).toContain('字节不在家目录');
    expect(wrapper.find('[data-od-id="skill-detail-markdown"]').exists()).toBe(false);
  });
});

describe('mcp 页签', () => {
  test('两块面都在：上块库行（装配正本）+ 下块家目录 mcp.json，各标数据来源', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    await flush();

    const panel = wrapper.find('[data-adpanel="mcp"]');
    // 上块：库行列表 + 表单，数据来源标注逐字取词条。
    expect(panel.find('[data-od-id="mcp-source-db"]').text()).toBe(
      i18n.t('mcp.sourceDb'),
    );
    expect(panel.find('[data-od-id="capability-mcp-row"]').text()).toContain('filesystem');
    expect(panel.find('[data-od-id="mcp-form"]').exists()).toBe(true);
    // 下块：家目录 mcp.json 的原文文本域 + 该文件的条目视图。
    expect(panel.find('[data-od-id="agent-mcp-file-text"]').element.value).toBe(MCP_FILE_TEXT);
    expect(panel.find('[data-od-id="agent-detail-section-global-mcp"]').exists()).toBe(true);
    // 「同名时库行胜出」写在文件块的数据来源标注里（词条值本身就是那句话）。
    expect(panel.find('[data-od-id="agent-detail-mcp-file-source"]').text()).toBe(
      i18n.t('agentDetail.mcpFileSource'),
    );
  });

  test('mcp.json 保存：整份原文一次 PUT（不重排、不重新序列化），成功后按后端读数刷新', async () => {
    const next = '{\n    "servers": []\n}\n';
    const { wrapper, stub } = await mountApp({
      '/api/agents/mem-1/global-mcp-file': (_url, request) =>
        request.method === 'PUT'
          ? jsonResponse({ member_id: 'mem-1', exists: true, text: JSON.parse(request.body).text, servers: 0 })
          : jsonResponse({ member_id: 'mem-1', exists: true, text: MCP_FILE_TEXT, servers: 1 }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    await flush();

    const textarea = wrapper.find('[data-od-id="agent-mcp-file-text"]');
    await textarea.setValue(next);
    await wrapper.find('[data-od-id="agent-mcp-file-save"]').trigger('click');
    await flush();
    await flush();

    const put = stub.calls.find(
      (call) => call.url === '/api/agents/mem-1/global-mcp-file' && call.method === 'PUT',
    );
    // body 就是**整份原文**：前端不拆字段、不归一化、不重排缩进。
    expect(put.body).toEqual({ text: next });
    expect(wrapper.find('[data-od-id="agent-mcp-file-count"]').text()).toContain('0');
  });

  test('保存 mcp.json 后，条目视图重读同一份文件', async () => {
    let saved = false;
    const { wrapper } = await mountApp({
      '/api/agents/mem-1/global-mcp-file': (_url, request) => {
        if (request.method === 'PUT') {
          saved = true;
          return jsonResponse({ member_id: 'mem-1', exists: true, text: JSON.parse(request.body).text, servers: 1 });
        }
        return jsonResponse({ member_id: 'mem-1', exists: true, text: MCP_FILE_TEXT, servers: 1 });
      },
      '/api/agents/mem-1/global-mcp-servers': () => jsonResponse({
        global_mcp_servers: [{ name: saved ? 'new-server' : 'old-server', transport: 'stdio', is_remote: false }],
      }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    expect(wrapper.find('[data-od-id="global-mcp-old-server"]').exists()).toBe(true);
    await wrapper.find('[data-od-id="agent-mcp-file-text"]').setValue('{"servers": []}');
    await wrapper.find('[data-od-id="agent-mcp-file-save"]').trigger('click');
    await flush();
    await flush();
    expect(wrapper.find('[data-od-id="global-mcp-new-server"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="global-mcp-old-server"]').exists()).toBe(false);
  });

  test('mcp.json 保存被后端拒 ⇒ 可读原因落在下块自己的错误行，文本域保留人的输入', async () => {
    const { wrapper } = await mountApp({
      '/api/agents/mem-1/global-mcp-file': (_url, request) =>
        request.method === 'PUT'
          ? jsonResponse({ error: { code: 'validation_failed', message: '第 2 条声明缺少 transport' } }, 400)
          : jsonResponse({ member_id: 'mem-1', exists: true, text: MCP_FILE_TEXT, servers: 1 }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    await flush();

    const broken = '{ "servers": [ { "name": "x" } ] }';
    await wrapper.find('[data-od-id="agent-mcp-file-text"]').setValue(broken);
    await wrapper.find('[data-od-id="agent-mcp-file-save"]').trigger('click');
    await flush();
    await flush();

    const error = wrapper.find('[data-od-id="agent-mcp-file-error"]');
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain('第 2 条声明缺少 transport');
    expect(wrapper.find('[data-od-id="agent-mcp-file-text"]').element.value).toBe(broken);
  });

  test('库行表单新增走既有 MCP 端点，作用域恒为当前成员', async () => {
    const { wrapper, stub } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-mcp"]').trigger('click');
    await flush();

    await wrapper.find('[data-od-id="mcp-name"]').setValue('github');
    await wrapper.find('[data-od-id="mcp-command"]').setValue('npx');
    await wrapper.find('[data-od-id="mcp-save"]').trigger('click');
    await flush();
    await flush();

    const post = stub.calls.find((call) => call.url === '/api/mcp-servers' && call.method === 'POST');
    expect(post.body.scope_kind).toBe('agent');
    expect(post.body.scope_id).toBe('mem-1');
    expect(post.body.name).toBe('github');
  });
});

describe('评审回归（双轴 findings）', () => {
  test('删除技能后的 toast 显示文案，不是词条键名', async () => {
    const { wrapper } = await mountApp({
      '/api/skills/skl-1': (_url, request) =>
        request.method === 'DELETE'
          ? jsonResponse({ deleted: 'skl-1' })
          : jsonResponse({ skill: SKILL }),
      '/api/skills/skl-1/markdown': () => jsonResponse({ skill: SKILL, markdown: SKILL_BODY, state: 'ok' }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();
    await wrapper.find('[data-od-id="skill-delete"]').trigger('click');
    await flush();
    await flush();

    const toast = wrapper.find('[data-od-id="toast-wrap"]').text();
    expect(toast).toBe(i18n.t('skill.deleted'));
    expect(toast).not.toContain('skill.deleted');
  });

  test('启停失败按启停归因（不是「技能列表读取失败」）', async () => {
    const { wrapper } = await mountApp({
      '/api/skills/skl-1': (_url, request) =>
        request.method === 'PATCH'
          ? jsonResponse({ error: { message: '后端拒绝启停' } }, 400)
          : jsonResponse({ skill: SKILL }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-detail-tab-skills"]').trigger('click');
    await flush();
    await wrapper.find('[data-skill-toggle]').trigger('click');
    await flush();
    await flush();

    const text = wrapper.find('[data-od-id="skill-error"]').text();
    expect(text).toContain(i18n.t('skill.toggleFailed', { reason: '后端拒绝启停' }));
    expect(text).not.toContain(i18n.t('skill.loadFailed', { reason: '' }).slice(0, 6));
  });

  test('主读数 500 且快照里没有该行 ⇒ 持久错误行可见，且保存 / 暂停 / 删除都禁用', async () => {
    const { wrapper } = await mountApp({
      '/api/workspace': () =>
        jsonResponse({
          member: OWNER,
          members: [OWNER, ADA_ROW],
          channels: [channel()],
          dms: [],
          agents: [],
          model_providers: ['ollama'],
          local_providers: ['ollama'],
          remote_consents: [],
        }),
      // 快照里没有这行，主读数也读不到：没有任何可展示的正本。
      '/api/agents/mem-1': () => jsonResponse({ error: { message: '详情暂时不可用' } }, 500),
    });
    await openDetail(wrapper);

    const errorRow = wrapper.find('[data-od-id="agent-detail-error"]');
    expect(errorRow.exists()).toBe(true);
    expect(errorRow.text()).toContain('读取 Agent 详情失败');
    expect(errorRow.text()).toContain('详情暂时不可用');
    expect(wrapper.find('[data-od-id="agent-toggle-enabled"]').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-od-id="agent-delete"]').attributes('disabled')).toBeDefined();
  });

  test('主读数在途时切走：不弹「读取 Agent 详情失败」的误报 toast', async () => {
    const gate = deferred();
    const { wrapper } = await mountApp({
      '/api/agents/mem-1': async () => {
        await gate.promise;
        return jsonResponse({ error: { message: '慢请求才失败' } }, 500);
      },
    });
    // 不 await：进详情后立即切回频道行，此时主读数还在飞。
    wrapper.find('[data-od-id="agent-mem-1"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(true);
    await wrapper.find('[data-od-id="channel-ch-1"]').trigger('click');
    await flush();

    gate.release();
    await flush();
    await flush();
    await flush();

    // 目标已切走：读数被作废，不应该给「读失败」或给别的成员弹任何 toast。
    expect(wrapper.find('[data-od-id="toast-wrap"]').text()).not.toContain('读取 Agent 详情失败');
  });
});

describe('危险操作：删除确认弹窗', () => {
  test('点「删除」只开弹窗（零请求）；确认后才发一次 DELETE，并关闭详情回到消息流', async () => {
    const { wrapper, stub } = await mountApp({
      '/api/agents/mem-1': (_url, request) =>
        request.method === 'DELETE'
          ? jsonResponse({ deleted: 'mem-1', agents: [], members: [OWNER] })
          : jsonResponse({ agent: ada() }),
    });
    await openDetail(wrapper);

    await wrapper.find('[data-od-id="agent-delete"]').trigger('click');
    await flush();
    const overlay = wrapper.find('#agentDeleteOverlay');
    expect(overlay.exists()).toBe(true);
    expect(overlay.element.hidden).toBe(false);
    expect(wrapper.find('[data-od-id="agent-delete-confirm"]').text()).toContain('Ada');
    // 弹窗打开着：一个 DELETE 都还没发。
    expect(stub.calls.filter((call) => call.method === 'DELETE')).toHaveLength(0);

    await wrapper.find('[data-od-id="agent-delete-confirm-action"]').trigger('click');
    await flush();
    await flush();

    const deletes = stub.calls.filter((call) => call.url === '/api/agents/mem-1' && call.method === 'DELETE');
    expect(deletes).toHaveLength(1);
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="messages-view"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="composer"]').exists()).toBe(true);
    // 成员已删：rail 的 Agent 行跟着消失（名单的正本在后端响应里）。
    expect(wrapper.find('[data-od-id="agent-mem-1"]').exists()).toBe(false);
  });

  test('删除失败保持弹窗和详情，显示后端原因并允许重试', async () => {
    const { wrapper } = await mountApp({
      '/api/agents/mem-1': (_url, request) =>
        request.method === 'DELETE'
          ? jsonResponse({ error: { message: '后端拒绝删除' } }, 400)
          : jsonResponse({ agent: ada() }),
    });
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-delete"]').trigger('click');
    await wrapper.find('[data-od-id="agent-delete-confirm-action"]').trigger('click');
    await flush();
    await flush();

    expect(wrapper.find('#agentDeleteOverlay').element.hidden).toBe(false);
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(true);
    expect(wrapper.text()).toContain('后端拒绝删除');
    expect(wrapper.find('[data-od-id="agent-delete-confirm-action"]').attributes('disabled')).toBeUndefined();
  });

  test('删除当前私信的 Agent 后，重读私信并立即变为已归档只读', async () => {
    const activeDm = { id: 'dm-1', kind: 'dm', agent_id: 'mem-1', peer: ADA_ROW, last_seq: 0, last_read_seq: 0, unread_count: 0 };
    const archivedDm = { ...activeDm, peer: { ...ADA_ROW, deleted_at: '2026-10-27T00:00:00Z' } };
    const { wrapper, stub } = await mountApp({
      '/api/workspace': () => jsonResponse({
        member: OWNER, members: [OWNER, ADA_ROW], channels: [channel()], dms: [activeDm], agents: [ada()],
        model_providers: ['ollama', 'dashscope'], local_providers: ['ollama'], remote_consents: [],
      }),
      '/api/dms/dm-1/messages': () => jsonResponse({ dm: activeDm, messages: [] }),
      '/api/dms': () => jsonResponse({ dms: [archivedDm] }),
      '/api/agents/mem-1': (_url, request) =>
        request.method === 'DELETE'
          ? jsonResponse({ deleted: 'mem-1', agents: [], members: [OWNER] })
          : jsonResponse({ agent: ada() }),
    });
    await wrapper.find('[data-od-id="dm-dm-1"]').trigger('click');
    await flush();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-delete"]').trigger('click');
    await wrapper.find('[data-od-id="agent-delete-confirm-action"]').trigger('click');
    await flush();
    await flush();

    expect(stub.calls.some((call) => call.url === '/api/dms' && call.method === 'GET')).toBe(true);
    expect(wrapper.find('[data-od-id="dm-archive-section"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="composer-input"]').attributes('disabled')).toBeDefined();
  });

  test('ESC 只关最上面那一层：删除确认弹窗开着时 ESC 收弹窗，详情还在', async () => {
    const { wrapper } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-delete"]').trigger('click');
    await flush();
    expect(wrapper.find('#agentDeleteOverlay').element.hidden).toBe(false);

    await pressEscape();
    expect(wrapper.find('#agentDeleteOverlay').element.hidden).toBe(true);
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(true);
  });

  test('删除确认弹窗的「取消」零请求，详情照旧', async () => {
    const { wrapper, stub } = await mountApp();
    await openDetail(wrapper);
    await wrapper.find('[data-od-id="agent-delete"]').trigger('click');
    await flush();
    await wrapper.find('[data-od-id="agent-delete-cancel"]').trigger('click');
    await flush();
    expect(wrapper.find('#agentDeleteOverlay').element.hidden).toBe(true);
    expect(stub.calls.filter((call) => call.method === 'DELETE')).toHaveLength(0);
    expect(wrapper.find('[data-od-id="agent-detail"]').exists()).toBe(true);
  });
});
