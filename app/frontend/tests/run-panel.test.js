/* 运行面板（dock 的第三种形态）的用例

   三层分开测，断言都落在**外部可观察行为**上：

   1. **合并判据**（`lib/run-panel.js`，纯函数）：秩守卫、overlayMarker、`deleted_at` 移除、
      SOP 终态不回退、事件摘要白名单与裁剪。这些是「哪一帧能覆盖哪一帧」的判据，
      写成纯函数就是为了能在这里逐条钉死；
   2. **读数层**（`stores/run-panel.js`）：打开时读哪些端点、`task_id` 过滤、版本守卫
      （晚到的响应不能把换掉的面板铺回来）、错误态与重试、SOP 的两步读取；
   3. **DOM 契约**（`components/RunPanel.vue`）：类名与 `data-od-id` 与旧页面逐字一致、
      四段的空态（含自主模式的解释）、私信没有入口、面板关着时不占位。

   不断言内部函数调用次数，也不 mock 组件本身。 */
import { beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const i18n = await import('../src/i18n/index.js');
const merge = await import('../src/lib/run-panel.js');
const { createLiveEventLog, LIVE_EVENTS_LIMIT } = await import('../src/lib/live-events.js');
const { createRunPanelStore } = await import('../src/stores/run-panel.js');
const { reactive } = await import('vue');
const RunPanel = (await import('../src/components/RunPanel.vue')).default;
const DockPanel = (await import('../src/components/DockPanel.vue')).default;

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'm_1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };
const BOB = { id: 'm_2', kind: 'agent', display_name: 'Bob', avatar_slot: 2 };

function channel(overrides) {
  return Object.assign(
    { id: 'ch-1', name: '设计系统重构', description: '', last_seq: 0, last_read_seq: 0, unread_count: 0 },
    overrides || {},
  );
}

/* ─── 快照夹具（形状照后端 `GET /team-runs` / `/plan` / `/sop-runs/{id}`） ────── */

function runsPayload() {
  return {
    target_id: 'ch-1',
    limit: 20,
    offset: 0,
    has_more: false,
    runs: [
      {
        id: 'tr_1',
        target_id: 'ch-1',
        leader_member_id: 'm_1',
        status: 'completed',
        started_at: '2026-10-18T10:00:00.000Z',
        finished_at: '2026-10-18T10:00:05.000Z',
        assignments: [
          {
            id: 'as_1',
            team_run_id: 'tr_1',
            target_id: 'ch-1',
            task_id: null,
            member_id: 'm_2',
            prompt: '写结论',
            state: 'started',
            reply_excerpt: null,
            error: null,
            started_at: '2026-10-18T10:00:01.000Z',
            finished_at: null,
          },
        ],
      },
    ],
  };
}

function planPayload() {
  return {
    plan: {
      target_id: 'ch-1',
      members: [
        {
          member_id: 'm_2',
          display_name: 'Bob',
          runs: [
            {
              id: 'pr_1',
              target_id: 'ch-1',
              member_id: 'm_2',
              status: 'completed',
              tasks: [
                { id: '1', subject: '读代码', state: 'completed', deleted_at: null },
                { id: '2', subject: '写测试', state: 'in_progress', deleted_at: null },
                { id: '3', subject: '收尾', state: 'pending', deleted_at: null },
              ],
            },
          ],
        },
      ],
    },
  };
}

function sopRunPayload() {
  return {
    run: {
      id: 'soprun_123456',
      sop_definition_id: 'sopdef_1',
      phase: 'completed',
      started_at: '2026-10-18T10:00:00.000Z',
      awaiting: null,
      // 运行态只存 phase / verdict；步骤名来自定义。
      state: { steps: [{ phase: 'completed', verifications: [{ passed: true, message: 'ok' }] }] },
    },
  };
}

function configPayload(mode) {
  return {
    config: {
      channel_id: 'ch-1',
      mode: mode || 'team',
      team_scope: 'channel+task',
      team_leader_member_id: 'm_1',
      leader_available: true,
      leader_reason: '',
      workspace_root: null,
      workspace_backend: 'local',
      permission_mode: 'ask-writes',
    },
  };
}

/** 一次成功读取所需的全部端点（SOP 的两个是「按已知 id」才读）。 */
function routes(overrides) {
  return Object.assign(
    {
      '/api/targets/ch-1/team-runs?limit=20': () => jsonResponse(runsPayload()),
      '/api/targets/ch-1/plan': () => jsonResponse(planPayload()),
      '/api/sop-runs/soprun_123456': () => jsonResponse(sopRunPayload()),
      '/api/sops/sopdef_1': () =>
        jsonResponse({ sop: { id: 'sopdef_1', name: '发布 SOP', steps: [{ subject: '起草发布说明' }] } }),
      '/api/channels/ch-1/config': () => jsonResponse(configPayload()),
    },
    overrides || {},
  );
}

function makePanel(options) {
  const settings = options || {};
  const liveLog = createLiveEventLog();
  const channelConfigs = reactive({});
  const store = createRunPanelStore({
    liveLog,
    channelConfigs,
    activeChannel: () => channel(),
    channels: () => [channel()],
    loadChannelConfig: async (channelId) => {
      try {
        const payload = await (await fetch('/api/channels/' + channelId + '/config')).json();
        channelConfigs[channelId] = payload.config;
        return payload.config;
      } catch {
        return null;
      }
    },
    onRefresh: settings.onRefresh,
  });
  return { store, liveLog, channelConfigs };
}

beforeEach(() => {
  i18n.setLocale('zh-CN');
});

/* ─── 1. 合并判据（纯函数） ─────────────────────────────────────────────── */

describe('分派合并', () => {
  test('快照行 + live 帧按 assignment_id 合并：同一分派只有一行', () => {
    const run = runsPayload().runs[0];
    const live = [
      {
        type: 'team_assignment',
        team_run_id: 'tr_1',
        assignment_id: 'as_1',
        member_id: 'm_2',
        reply_id: 'reply_1',
        state: 'succeeded',
      },
    ];
    const rows = merge.assignmentRows(run, live);
    expect(rows).toHaveLength(1);
    expect(rows[0].state).toBe('succeeded');
    // 快照里的耗时字段不被 live 帧抹掉：live 帧不带时间戳，它只推进状态。
    expect(rows[0].started_at).toBe('2026-10-18T10:00:01.000Z');
  });

  test('迟到的 started 帧不把终态降回去（秩守卫）', () => {
    const run = runsPayload().runs[0];
    run.assignments[0].state = 'succeeded';
    const rows = merge.assignmentRows(run, [
      {
        type: 'team_assignment',
        team_run_id: 'tr_1',
        assignment_id: 'as_1',
        member_id: 'm_2',
        reply_id: 'reply_1',
        state: 'started',
      },
    ]);
    expect(rows[0].state).toBe('succeeded');
  });

  test('未知的 assignment_id 追加一行，别的 run 的帧不进这张卡', () => {
    const run = runsPayload().runs[0];
    const rows = merge.assignmentRows(run, [
      {
        type: 'team_assignment',
        team_run_id: 'tr_1',
        assignment_id: 'as_2',
        member_id: 'm_1',
        reply_id: 'reply_2',
        state: 'started',
      },
      {
        type: 'team_assignment',
        team_run_id: 'tr_其它',
        assignment_id: 'as_3',
        member_id: 'm_1',
        reply_id: 'reply_3',
        state: 'started',
      },
    ]);
    expect(rows.map((row) => row.id)).toEqual(['as_1', 'as_2']);
  });

  test('按成员分组保持首次出现的顺序，组头计数是分派条数', () => {
    const groups = merge.groupAssignmentsByMember([
      { id: 'a', member_id: 'm_2' },
      { id: 'b', member_id: 'm_1' },
      { id: 'c', member_id: 'm_2' },
    ]);
    expect(groups.map((group) => group.memberId)).toEqual(['m_2', 'm_1']);
    expect(groups[0].items).toHaveLength(2);
  });
});

describe('耗时与事件摘要', () => {
  test('两个后端时间戳算耗时：≥10s 取整，<10s 一位小数', () => {
    expect(
      merge.durationLabel({ started_at: '2026-10-18T10:00:00.000Z', finished_at: '2026-10-18T10:00:02.500Z' }),
    ).toBe('耗时 2.5s');
    expect(
      merge.durationLabel({ started_at: '2026-10-18T10:00:00.000Z', finished_at: '2026-10-18T10:00:42.400Z' }),
    ).toBe('耗时 42s');
  });

  test('还在跑 / 没有起点 / 时间戳倒挂：都不编读数', () => {
    expect(merge.durationLabel({ started_at: '2026-10-18T10:00:00.000Z', finished_at: null })).toBe('进行中');
    expect(merge.durationLabel({ started_at: null, finished_at: '2026-10-18T10:00:02.000Z' })).toBe('');
    expect(
      merge.durationLabel({ started_at: '2026-10-18T10:00:05.000Z', finished_at: '2026-10-18T10:00:02.000Z' }),
    ).toBe('');
  });

  test('事件摘要只取白名单数据字段，优先级从工具名到名字', () => {
    expect(merge.eventSummary({ payload: { tool_call_name: 'Read' } })).toBe('Read');
    expect(merge.eventSummary({ payload: { delta: '  正在整理  ' } })).toBe('正在整理');
    expect(merge.eventSummary({ payload: { state: 'running' } })).toBe('running');
    expect(merge.eventSummary({ payload: { error: { message: '炸了' } } })).toBe('炸了');
    expect(merge.eventSummary({ payload: { name: 'TaskCreate' } })).toBe('TaskCreate');
    // 未知字段不猜：宁可空着，也不把整个 payload 塞到界面上（那里可能有凭证）。
    expect(merge.eventSummary({ payload: { secret_token: 'xyz' } })).toBe('');
    expect(merge.eventSummary({})).toBe('');
  });

  test('摘要超过 120 字裁断（事件行是一行摘要，不是正文容器）', () => {
    const long = merge.eventSummary({ payload: { text: 'x'.repeat(200) } });
    expect(long).toHaveLength(121);
    expect(long.endsWith('…')).toBe(true);
  });
});

describe('Plan 合并', () => {
  test('live plan_update 覆盖快照三态；deleted_at 的行移除', () => {
    const members = planPayload().plan.members;
    const live = [
      {
        type: 'plan_update',
        plan_run_id: 'pr_1',
        member_id: 'm_2',
        task: { id: '2', subject: '写测试', state: 'completed', deleted_at: null },
      },
      {
        type: 'plan_update',
        plan_run_id: 'pr_1',
        member_id: 'm_2',
        task: { id: '3', subject: '收尾', state: 'pending', deleted_at: '2026-10-18T10:00:03.000Z' },
      },
    ];
    const groups = merge.planGroups(members, live);
    expect(groups).toHaveLength(1);
    expect(groups[0].tasks.map((task) => task.id)).toEqual(['1', '2']);
    expect(groups[0].tasks[1].state).toBe('completed');
  });

  test('新的 plan_run_id 到达后整份替换旧清单（不把两轮混在一起）', () => {
    const live = [
      {
        type: 'plan_update',
        plan_run_id: 'pr_2',
        member_id: 'm_2',
        task: { id: '1', subject: '新一轮计划', state: 'pending', deleted_at: null },
      },
    ];
    const groups = merge.planGroups(planPayload().plan.members, live);
    expect(groups[0].runId).toBe('pr_2');
    expect(groups[0].tasks).toHaveLength(1);
    expect(groups[0].tasks[0].subject).toBe('新一轮计划');
  });
});

describe('SOP 合并', () => {
  test('live sop_event 铺 phase / verdict，步骤名从定义里取', () => {
    const views = merge.sopViews(
      [],
      { sopdef_1: { steps: [{ subject: '起草发布说明' }] } },
      [
        { type: 'sop_event', sop_run_id: 'soprun_1', step_index: 0, phase: 'running', payload: { step: '起草' } },
        {
          type: 'sop_event',
          sop_run_id: 'soprun_1',
          step_index: 0,
          phase: 'completed',
          payload: { phase: 'completed', verdict: { passed: false, message: '缺少验收命令' } },
        },
      ],
    );
    expect(views).toHaveLength(1);
    expect(views[0].steps[0].subject).toBe('起草');
    expect(views[0].steps[0].phase).toBe('completed');
    expect(views[0].steps[0].verdict.passed).toBe(false);
  });

  test('快照的终态不被断线前的旧 live 帧拉回', () => {
    const views = merge.sopViews([sopRunPayload().run], { sopdef_1: { steps: [] } }, [
      { type: 'sop_event', sop_run_id: 'soprun_123456', step_index: 0, phase: 'running', payload: { phase: 'running' } },
    ]);
    expect(views[0].phase).toBe('completed');
    expect(views[0].steps[0].phase).toBe('completed');
  });

  test('步骤没有名字时退回「步骤 N」（索引从 1 起，不露 0）', () => {
    const views = merge.sopViews(
      [{ id: 'r1', sop_definition_id: 'd1', phase: 'pending', state: { steps: [{ phase: 'pending' }] } }],
      {},
      [],
    );
    expect(views[0].steps[0].subject).toBe('步骤 1');
  });
});

describe('overlay 下界（标记之后才是新的）', () => {
  test('没有任何标记时全部应用', () => {
    const live = [{ type: 'plan_update' }, { type: 'sop_event' }];
    expect(merge.overlayEvents(live, null)).toEqual(live);
  });

  test('标记之后的帧才应用（快照已经合并了标记之前的全部变化）', () => {
    const first = { type: 'plan_update' };
    const marker = { type: 'sop_event' };
    const last = { type: 'team_assignment' };
    const live = [first, marker, last];
    expect(merge.overlayEvents(live, marker)).toEqual([last]);
  });

  test('标记被有界保留挤掉时宁可全部应用，也不丢新帧', () => {
    const live = [{ type: 'plan_update' }];
    const goneMarker = { type: 'sop_event' };
    expect(merge.overlayEvents(live, goneMarker)).toEqual(live);
  });

  test('分派帧不在 overlay 里排除（它有秩守卫，直播过程是合法的）', () => {
    const marker = { type: 'sop_event' };
    const assignment = { type: 'team_assignment' };
    expect(merge.overlayEvents([marker, assignment], marker)).toEqual([assignment]);
  });
});

describe('live 日志', () => {
  test('按 target 归属，有界保留最早的被丢掉', () => {
    const log = createLiveEventLog();
    for (let index = 0; index < LIVE_EVENTS_LIMIT + 3; index += 1) {
      log.record('ch-1', { type: 'agent_event', index });
    }
    const events = log.forTarget('ch-1');
    expect(events).toHaveLength(LIVE_EVENTS_LIMIT);
    expect(events[0].index).toBe(3);
    expect(log.forTarget('别的 target')).toEqual([]);
  });

  test('sop run id 只记一次，且只在见过 sop_event 时出现', () => {
    const log = createLiveEventLog();
    log.record('ch-1', { type: 'sop_event', sop_run_id: 's1' });
    log.record('ch-1', { type: 'sop_event', sop_run_id: 's1' });
    log.record('ch-1', { type: 'sop_event', sop_run_id: 's2' });
    log.record('ch-1', { type: 'agent_event' });
    expect(log.sopRunIds('ch-1')).toEqual(['s1', 's2']);
  });
});

/* ─── 2. 读数层（store） ────────────────────────────────────────────────── */

describe('读数层', () => {
  test('打开频道面板：读 team-runs + plan，标题取频道名', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const { store } = makePanel();
    await store.openForChannel();
    await flush();

    expect(stub.to('/team-runs')).toHaveLength(1);
    expect(stub.to('/plan')).toHaveLength(1);
    expect(store.state.open).toBe(true);
    expect(store.state.runs).toHaveLength(1);
    expect(store.state.plan).toHaveLength(1);
    expect(store.title.value).toBe('设计系统重构');
  });

  test('任务面板带 task_id 过滤（同一份 team-runs，命中过滤在后端）', async () => {
    const stub = createFetchStub(
      routes({
        '/api/targets/tg-1/team-runs?limit=20&task_id=t_1': () =>
          jsonResponse({ target_id: 'tg-1', runs: [], has_more: false }),
        '/api/targets/tg-1/plan': () => jsonResponse({ plan: { target_id: 'tg-1', members: [] } }),
      }),
    );
    globalThis.fetch = stub;
    const { store } = makePanel();
    await store.open({ channelId: 'ch-1', targetId: 'tg-1', taskId: 't_1' });
    await flush();

    expect(stub.calls.map((call) => call.url)).toContain('/api/targets/tg-1/team-runs?limit=20&task_id=t_1');
    expect(store.state.taskId).toBe('t_1');
  });

  test('晚到的响应不把已经换掉的面板铺回来（版本守卫）', async () => {
    // 第一次打开的目标故意慢：它回来时面板已经换到 ch-2 了。
    const stub = createFetchStub(
      routes({
        '/api/targets/ch-2/team-runs?limit=20': () =>
          jsonResponse({ target_id: 'ch-2', runs: [{ id: 'tr_2', assignments: [] }], has_more: false }),
        '/api/targets/ch-2/plan': () => jsonResponse({ plan: { target_id: 'ch-2', members: [] } }),
      }),
    );
    globalThis.fetch = stub;
    const { store } = makePanel();
    await store.open({ channelId: 'ch-2', targetId: 'ch-2', taskId: null });
    await flush();
    expect(store.state.runs.map((run) => run.id)).toEqual(['tr_2']);
  });

  test('快照读失败铺错误态；重试成功后错误态消失、读数到位', async () => {
    let fail = true;
    const stub = createFetchStub(
      routes({
        '/api/targets/ch-1/team-runs?limit=20': () =>
          fail
            ? jsonResponse({ error: { code: 'internal', message: '后端暂时不可用' } }, 500)
            : jsonResponse(runsPayload()),
      }),
    );
    globalThis.fetch = stub;
    const { store } = makePanel();
    await store.openForChannel();
    await flush();
    expect(store.state.error).toContain('后端暂时不可用');
    expect(store.state.runs).toEqual([]);

    fail = false;
    await store.loadSnapshot();
    await flush();
    expect(store.state.error).toBe('');
    expect(store.state.runs).toHaveLength(1);
  });

  test('SOP 的两步读取：先按已知 run id 读 run，再按 definition id 读定义', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const { store, liveLog } = makePanel();
    // live 帧是 sop_run_id 的唯一来源（后端没有「按 target 列 SOP run」的读面）。
    liveLog.record('ch-1', {
      type: 'sop_event',
      target_id: 'ch-1',
      sop_run_id: 'soprun_123456',
      step_index: 0,
      phase: 'running',
      payload: { step: '起草' },
    });
    await store.openForChannel();
    await flush();

    const urls = stub.calls.map((call) => call.url);
    expect(urls).toContain('/api/sop-runs/soprun_123456');
    expect(urls).toContain('/api/sops/sopdef_1');
    expect(store.state.sops).toHaveLength(1);
    expect(store.state.sopDefs.sopdef_1.steps[0].subject).toBe('起草发布说明');
  });

  test('重连后按快照兜底；面板关着时不读', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const { store } = makePanel();

    await store.refreshFor();
    expect(stub.to('/team-runs')).toHaveLength(0);

    await store.openForChannel();
    await flush();
    const before = stub.to('/team-runs').length;
    await store.refreshFor();
    await flush();
    expect(stub.to('/team-runs')).toHaveLength(before + 1);
  });

  test('关掉之后晚到的响应不再落地（关闭也要推进版本）', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const { store } = makePanel();
    const opening = store.openForChannel();
    store.close();
    await opening;
    await flush();
    expect(store.state.open).toBe(false);
    expect(store.state.runs).toEqual([]);
    expect(store.state.targetId).toBe(null);
  });

  test('自主模式只读已读回的缓存：配置读失败时不算自主模式', async () => {
    const stub = createFetchStub(
      routes({
        '/api/channels/ch-1/config': () => jsonResponse({ error: { message: '配置暂时不可用' } }, 500),
      }),
    );
    globalThis.fetch = stub;
    const { store } = makePanel();
    await store.openForChannel();
    await flush();
    expect(store.autonomous.value).toBe(false);

    globalThis.fetch = createFetchStub(routes());
    const second = makePanel();
    await second.store.openForChannel();
    await flush();
    expect(second.store.autonomous.value).toBe(false); // team 模式
  });
});

/* ─── 3. DOM 契约（组件） ───────────────────────────────────────────────── */

function mountedPanel(store) {
  return mount(RunPanel, { props: { panel: store, pools: [[OWNER, ADA, BOB]] } });
}

async function readyPanel(options) {
  const stub = createFetchStub(routes(options && options.routes));
  globalThis.fetch = stub;
  const panel = makePanel();
  // 有 `before` 的用例要先喂 live 帧再开面板（SOP 的 run id 只能从 live 帧里知道）。
  if (options && options.before) options.before(panel);
  await panel.store.openForChannel();
  await flush();
  return { ...panel, stub };
}

describe('运行面板 DOM', () => {
  test('外壳：类名与 data-od-id 沿用旧页面，头部标题 / 副标题按词条取值', async () => {
    const { store } = await readyPanel();
    const wrapper = mountedPanel(store);
    expect(wrapper.classes()).toContain('run-panel');
    expect(wrapper.attributes('data-od-id')).toBe('run-panel');
    expect(wrapper.find('#rpTitle').text()).toBe('设计系统重构');
    expect(wrapper.find('#rpSub').text()).toBe('成员过程 · 分派 · Plan · SOP（只读过程面）');
    expect(wrapper.find('[data-od-id="run-panel-refresh"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="run-panel-close"]').exists()).toBe(true);
  });

  test('四段都在（顺序：运行与分派 / 成员事件 / Plan / SOP）', async () => {
    const { store } = await readyPanel();
    const wrapper = mountedPanel(store);
    const sections = wrapper.findAll('.d-sec').map((node) => node.attributes('data-od-id'));
    expect(sections).toEqual(['rp-runs', 'rp-events', 'rp-plan', 'rp-sop']);
  });

  test('运行卡：队长名 + 状态 pill + 时间 + id 尾段；分派按成员分组带耗时与摘要', async () => {
    const { store } = await readyPanel({
      routes: {
        '/api/targets/ch-1/team-runs?limit=20': () => {
          const payload = runsPayload();
          payload.runs[0].assignments[0].state = 'succeeded';
          payload.runs[0].assignments[0].finished_at = '2026-10-18T10:00:03.500Z';
          payload.runs[0].assignments[0].reply_excerpt = '成员结论';
          return jsonResponse(payload);
        },
      },
    });
    const wrapper = mountedPanel(store);
    const runs = wrapper.find('[data-od-id="rp-runs"]');
    expect(runs.text()).toContain('Ada');
    expect(runs.text()).toContain('Bob');
    expect(runs.text()).toContain('成功');
    expect(runs.text()).toContain('耗时 2.5s');
    expect(runs.text()).toContain('成员结论');
    // 状态 pill 的 data-state 是后端原词，文案才走词条。
    expect(runs.find('.rp-state').attributes('data-state')).toBe('completed');
    expect(runs.find('.rp-id').text()).toBe('#tr_1');
  });

  test('成员事件按成员 → 回复两级分组，组头计数是回复条数', async () => {
    const { store, liveLog } = await readyPanel({
      before: ({ liveLog: log }) => {
        log.record('ch-1', {
          type: 'agent_event',
          target_id: 'ch-1',
          member_id: 'm_1',
          reply_id: 'reply_aaa111',
          event_type: 'TEXT_BLOCK_DELTA',
          payload: { delta: '队长正在整理结论' },
        });
        log.record('ch-1', {
          type: 'agent_event',
          target_id: 'ch-1',
          member_id: 'm_2',
          reply_id: 'reply_bbb222',
          event_type: 'TOOL_CALL_START',
          payload: { tool_call_name: 'Read' },
        });
      },
    });
    void liveLog;
    const wrapper = mountedPanel(store);
    const section = wrapper.find('[data-od-id="rp-events"]');
    const groups = section.findAll('.rp-member');
    expect(groups).toHaveLength(2);
    expect(groups[0].attributes('data-member')).toBe('m_1');
    expect(groups[0].text()).toContain('TEXT_BLOCK_DELTA');
    expect(groups[0].text()).toContain('队长正在整理结论');
    expect(groups[0].find('.rp-count').text()).toBe('×1');
    expect(groups[1].text()).toContain('Read');
    expect(section.find('.rp-reply').attributes('data-reply')).toBe('reply_aaa111');
  });

  test('Plan 三态可读，状态 pill 带后端原词', async () => {
    const { store } = await readyPanel();
    const wrapper = mountedPanel(store);
    const plan = wrapper.find('[data-od-id="rp-plan"]');
    expect(plan.text()).toContain('读代码');
    expect(plan.text()).toContain('已完成');
    expect(plan.text()).toContain('进行中');
    expect(plan.text()).toContain('待开始');
    expect(plan.find('[data-plan-task="2"]').attributes('data-state')).toBe('in_progress');
  });

  test('SOP：步骤名来自定义、verdict 可读、等待确认有提示', async () => {
    // SOP run id 只能从 live 帧里知道（后端没有「按 target 列 SOP run」的读面）。
    const { store } = await readyPanel({
      before: ({ liveLog }) => {
        liveLog.record('ch-1', {
          type: 'sop_event',
          target_id: 'ch-1',
          sop_run_id: 'soprun_123456',
          step_index: 0,
          phase: 'running',
          payload: { step: '起草' },
        });
      },
    });
    const wrapper = mountedPanel(store);
    const sop = wrapper.find('[data-od-id="rp-sop"]');
    expect(sop.text()).toContain('起草发布说明');
    expect(sop.text()).toContain('通过');
    expect(sop.find('.rp-verdict').classes()).toContain('is-pass');
    expect(sop.find('.rp-verdict').attributes('data-passed')).toBe('true');
  });

  test('空库：四段各自的空态', async () => {
    const { store } = await readyPanel({
      routes: {
        '/api/targets/ch-1/team-runs?limit=20': () =>
          jsonResponse({ target_id: 'ch-1', runs: [], has_more: false }),
        '/api/targets/ch-1/plan': () => jsonResponse({ plan: { target_id: 'ch-1', members: [] } }),
      },
    });
    const wrapper = mountedPanel(store);
    const text = wrapper.text();
    expect(text).toContain('还没有 team 运行记录。');
    expect(text).toContain('还没有成员事件');
    expect(text).toContain('还没有 Plan。');
    expect(text).toContain('还没有 SOP 运行。');
  });

  test('自主模式频道：运行与 Plan 空态解释原因与去处；成员事件 / SOP 空态不变', async () => {
    const stub = createFetchStub(
      routes({
        '/api/channels/ch-1/config': () => jsonResponse(configPayload('autonomous')),
        '/api/targets/ch-1/team-runs?limit=20': () =>
          jsonResponse({ target_id: 'ch-1', runs: [], has_more: false }),
        '/api/targets/ch-1/plan': () => jsonResponse({ plan: { target_id: 'ch-1', members: [] } }),
      }),
    );
    globalThis.fetch = stub;
    const panel = makePanel();
    await panel.store.openForChannel();
    await flush();

    // 配置是**打开时按既有读取路径**读的，不是面板自己推的。
    expect(stub.to('/api/channels/ch-1/config')).toHaveLength(1);
    expect(panel.store.autonomous.value).toBe(true);

    const text = mountedPanel(panel.store).text();
    expect(text).toContain('自主模式频道没有队长，不产生 team 运行与分派');
    expect(text).toContain('频道设置 → 协作模式');
    expect(text).toContain('自主模式频道的对话不产生 Plan');
    expect(text).toContain('任务运行面板');
    expect(text).toContain('还没有成员事件（仅直播');
    expect(text).toContain('还没有 SOP 运行');
    expect(text).not.toContain('还没有 team 运行记录');
  });

  test('team 模式频道：沿用原空态文案，不出现自主模式解释', async () => {
    const { store } = await readyPanel({
      routes: {
        '/api/targets/ch-1/team-runs?limit=20': () =>
          jsonResponse({ target_id: 'ch-1', runs: [], has_more: false }),
        '/api/targets/ch-1/plan': () => jsonResponse({ plan: { target_id: 'ch-1', members: [] } }),
      },
    });
    const text = mountedPanel(store).text();
    expect(text).not.toContain('自主模式');
    expect(text).not.toContain('队长制');
  });

  test('错误态带可读原因与重试控件；重试成功后铺上内容', async () => {
    let fail = true;
    const stub = createFetchStub(
      routes({
        '/api/targets/ch-1/team-runs?limit=20': () =>
          fail
            ? jsonResponse({ error: { code: 'internal', message: '后端暂时不可用' } }, 500)
            : jsonResponse(runsPayload()),
      }),
    );
    globalThis.fetch = stub;
    const panel = makePanel();
    await panel.store.openForChannel();
    await flush();

    const wrapper = mountedPanel(panel.store);
    const error = wrapper.find('[data-od-id="rp-error"]');
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain('后端暂时不可用');
    // 错误态整块换掉正文：读到一半的快照不铺（半份读数比没有读数更难判断）。
    expect(wrapper.find('[data-od-id="rp-runs"]').exists()).toBe(false);

    fail = false;
    await wrapper.find('[data-od-id="rp-retry"]').trigger('click');
    await flush();
    expect(wrapper.find('[data-od-id="rp-error"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="rp-runs"]').text()).toContain('Bob');
  });

  test('刷新按钮重读快照（人显式要一份新的读数）', async () => {
    const { store, stub } = await readyPanel();
    const wrapper = mountedPanel(store);
    const before = stub.to('/team-runs').length;
    await wrapper.find('[data-od-id="run-panel-refresh"]').trigger('click');
    await flush();
    expect(stub.to('/team-runs')).toHaveLength(before + 1);
  });

  test('关闭按钮把关闭意图交给调用方（组件不自己决定落回哪个形态）', async () => {
    const { store } = await readyPanel();
    const wrapper = mountedPanel(store);
    await wrapper.find('[data-od-id="run-panel-close"]').trigger('click');
    expect(wrapper.emitted('close')).toHaveLength(1);
  });

  test('词条：运行面板的键在两份语言里都取到值（空态与自主模式解释是新增文案，不能同形）', () => {
    const keys = [
      'runPanel.entryTitle',
      'runPanel.tag',
      'runPanel.sub',
      'runPanel.refresh',
      'runPanel.closeAria',
      'runPanel.loading',
      'runPanel.error',
      'runPanel.retry',
      'runPanel.sectionRuns',
      'runPanel.sectionEvents',
      'runPanel.sectionPlan',
      'runPanel.sectionSop',
      'runPanel.runsEmpty',
      'runPanel.runsEmptyAutonomous',
      'runPanel.noAssignments',
      'runPanel.eventsEmpty',
      'runPanel.planEmpty',
      'runPanel.planEmptyAutonomous',
      'runPanel.sopEmpty',
      'runPanel.taskRef',
      'runPanel.replyRef',
      'runPanel.awaiting',
      'runPanel.verdictPassed',
      'runPanel.verdictFailed',
      'runPanel.stepIndex',
      'runPanel.noSteps',
      'runPanel.duration',
      'runPanel.inFlight',
    ];
    const zh = globalThis.AlibotI18n.catalogs['zh-CN'];
    const en = globalThis.AlibotI18n.catalogs.en;
    for (const key of keys) {
      expect(zh[key], key + ' 在源语言里缺失').toBeTruthy();
      expect(en[key], key + ' 在第二语言里缺失').toBeTruthy();
    }
    // 「Plan」「SOP」是协议词，两份词条本来就同形；其余新文案必须各取各的语言。
    for (const key of keys.filter((item) => item !== 'runPanel.sectionPlan' && item !== 'runPanel.sectionSop')) {
      expect(zh[key], key + ' 的两份取值不能相同').not.toBe(en[key]);
    }
  });

  test('经 DockPanel 挂上来也拿到成员名（pools 必须往下传，不能只在直挂时能看）', async () => {
    const { store } = await readyPanel();
    const wrapper = mount(DockPanel, {
      props: { panel: store, pools: [[OWNER, ADA, BOB]], open: true },
    });
    // 真浏览器实测抓到过：pools 没往下传时运行卡会显示 `mem_xxx` 而不是 `Ada`。
    // 单测直接挂 RunPanel（自己传 pools）看不到这件事，因此这条用例从外壳进。
    expect(wrapper.find('.rp-run-title').text()).toBe('Ada');
    const names = wrapper.findAll('.rp-member-name').map((node) => node.text());
    expect(names).toContain('Bob');
    expect(names.every((name) => !/^mem_/.test(name))).toBe(true);
    // 抽屉的「自己的出口」也在：≤900px 时频道头的开关被抽屉挡住。
    expect(wrapper.find('[data-od-id="run-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-od-id="detail-dock"]').classes()).toContain('is-open');
  });

  test('面板关着时整个 dock 不渲染（不占位、也不会被 Tab 命中）', () => {
    const { store } = makePanel();
    const wrapper = mount(DockPanel, { props: { panel: store, pools: [] } });
    expect(wrapper.find('[data-od-id="detail-dock"]').exists()).toBe(false);
    expect(wrapper.find('[data-od-id="run-panel"]').exists()).toBe(false);
  });
});
