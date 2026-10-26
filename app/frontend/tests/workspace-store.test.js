/* 工作台 store 的用例（读路径 / 标读 / 乐观发送）

   断言只落在**外部可观察行为**上：发了哪些请求、镜像里落了什么、pending 项的三种归宿
   （确认 / 失败 / 移除）。不断言内部函数调用次数，也不读组件。 */
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const { createWorkspaceStore } = await import('../src/stores/workspace.js');

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };
const ADA = { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 };

function channel(overrides) {
  return Object.assign(
    { id: 'ch-1', name: 'design', description: '', last_seq: 0, last_read_seq: 0, unread_count: 0 },
    overrides || {},
  );
}

function message(seq, overrides) {
  return Object.assign(
    {
      id: 'msg-' + seq,
      target_id: 'ch-1',
      seq,
      kind: 'human',
      body: 'body ' + seq,
      created_at: '2026-10-26T10:00:00.000Z',
      mention_snapshot: [],
      author: OWNER,
    },
    overrides || {},
  );
}

/** 一次成功的工作台载入所需的全部读数。 */
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
      '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    },
    overrides || {},
  );
}

function makeStore(fetchStub, now) {
  return createWorkspaceStore({ toast: () => {}, now: now || (() => 1_700_000_000_000) });
}

beforeEach(() => {
  localStorage.clear();
  // 真实页面的起点：没有本地记忆时 `activeTargetId` 为空（先让人选一个频道）。
  // 需要「已载入某个频道」的用例显式把它写进本地记忆——这也是唯一允许写存储的那一条。
  localStorage.setItem('alibot.activeChannel', 'ch-1');
});

describe('读路径', () => {
  test('loadWorkspace 把快照落进镜像，并沿用本地记忆的 target', async () => {
    localStorage.setItem('alibot.activeChannel', 'ch-1');
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const store = makeStore(stub);

    const result = await store.loadWorkspace();

    expect(result.ok).toBe(true);
    expect(store.state.ready).toBe(true);
    expect(store.state.channels.map((item) => item.id)).toEqual(['ch-1']);
    expect(store.state.members.map((item) => item.id)).toEqual(['owner', 'mem-1']);
    expect(store.state.activeTargetId).toBe('ch-1');
    expect(stub.to('/api/channels/ch-1/messages').length).toBe(1);
  });

  test('后端不可达时 online 置假、ready 置真，且不抛（重连由调用方排）', async () => {
    const stub = createFetchStub({
      '/api/settings': () => jsonResponse({ settings: {} }),
      '/api/workspace': () => jsonResponse({ detail: 'unavailable' }, 503),
    });
    globalThis.fetch = stub;
    const store = makeStore(stub);

    const result = await store.loadWorkspace({ silent: true });

    expect(result.ok).toBe(false);
    expect(store.state.online).toBe(false);
    expect(store.state.ready).toBe(true);
  });

  test('设置读不到不影响工作台：主流程照常落地', async () => {
    const stub = createFetchStub(
      routes({ '/api/settings': () => jsonResponse({ detail: 'nope' }, 500) }),
    );
    globalThis.fetch = stub;
    const store = makeStore(stub);

    const result = await store.loadWorkspace();

    expect(result.ok).toBe(true);
    expect(store.state.channels.length).toBe(1);
  });

  test('轮次结果读不到时保留上一次已知值（不清空、不判成不可达）', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();
    store.applyOutcomes([{ id: 'r-1', base_seq: 1, member_id: 'mem-1', status: 'ignored', reply_seq: null }]);

    globalThis.fetch = createFetchStub(
      routes({ '/api/channels/ch-1/round-outcomes': () => jsonResponse({ detail: 'boom' }, 500) }),
    );
    await store.refreshOutcomes();

    expect(store.state.outcomes[1]['mem-1'].length).toBe(1);
    expect(store.state.online).toBe(true);
  });

  test('成员表读不到时记进 failed 列表，且不伪造出成员', async () => {
    const stub = createFetchStub(
      routes({ '/api/channels/ch-1/members': () => jsonResponse({ detail: 'boom' }, 500) }),
    );
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();

    expect(store.state.channelMembersFailed).toContain('ch-1');
    expect(store.channelAgentMembers('ch-1')).toEqual([]);
  });
});

describe('标读', () => {
  test('上界取 last_seq 与已渲染最大 seq 的较大值，且按 target 各自防抖', async () => {
    vi.useFakeTimers();
    const stub = createFetchStub(
      routes({
        '/api/channels/ch-1/messages': () =>
          jsonResponse({ channel: channel({ last_seq: 3 }), messages: [message(1), message(2)] }),
        '/api/channels/ch-1/read': () => jsonResponse({ channel: channel({ last_read_seq: 3 }), channels: [channel()] }),
      }),
    );
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();

    store.markCurrentRead();
    // 未到防抖时间不发请求。
    expect(stub.to('/read').length).toBe(0);
    await vi.advanceTimersByTimeAsync(200);

    expect(stub.to('/read').length).toBe(1);
    expect(stub.to('/read')[0].body).toEqual({ seq: 3 });
    vi.useRealTimers();
  });

  test('已读位置已经追平就不发标读（不产生无意义的写）', async () => {
    vi.useFakeTimers();
    const stub = createFetchStub(
      routes({
        '/api/channels/ch-1/messages': () =>
          jsonResponse({ channel: channel({ last_seq: 2, last_read_seq: 2 }), messages: [message(1), message(2)] }),
      }),
    );
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();

    store.markCurrentRead();
    await vi.advanceTimersByTimeAsync(200);

    expect(stub.to('/read').length).toBe(0);
    vi.useRealTimers();
  });
});

describe('发送（乐观 pending）', () => {
  async function loadedStore(overrides) {
    const stub = createFetchStub(routes(overrides));
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();
    return { stub, store };
  }

  test('成功后 pending 被移除、消息按 seq 落进镜像（同一帧重复到达不重复）', async () => {
    const { stub, store } = await loadedStore({
      '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel({ last_seq: 1 }), messages: [] }),
    });
    globalThis.fetch = createFetchStub(
      routes({
        '/api/channels/ch-1/messages': (url, request) =>
          request.method === 'POST'
            ? jsonResponse({ message: message(1) }, 201)
            : jsonResponse({ channel: channel(), messages: [] }),
      }),
    );

    const result = await store.sendMessage('你好', []);

    expect(result.ok).toBe(true);
    expect(store.state.pending.length).toBe(0);
    expect(store.state.messages.map((item) => item.seq)).toEqual([1]);
    // 事件流再送同一条：按 seq 认，不重复。
    expect(store.absorbMessage('ch-1', message(1))).toBe(false);
    expect(store.state.messages.length).toBe(1);
    expect(stub.to('/api/channels/ch-1/messages').length).toBe(1);
  });

  test('后端拒绝（有 status）：pending 留在流里带失败原因，online 不变', async () => {
    // 先按正常读数载入（否则 `online` 一开始就是假，测不到「拒绝不等于不可达」）。
    const { store } = await loadedStore();
    // 只把**发送**这一个请求换成 400：读路径仍是好的。
    globalThis.fetch = createFetchStub(
      routes({
        '/api/channels/ch-1/messages': (url, request) =>
          request.method === 'POST'
            ? jsonResponse({ detail: 'body 太长' }, 400)
            : jsonResponse({ channel: channel(), messages: [] }),
      }),
    );

    const result = await store.sendMessage('你好', []);

    expect(result.ok).toBe(false);
    expect(store.state.online).toBe(true);
    const failed = store.state.pending.find((item) => item.status === 'failed');
    expect(failed).toBeTruthy();
    expect(failed.reason).toContain('body 太长');
  });

  test('后端不可达（无 status）：pending 同样留在流里，online 置假（不排队、不静默吞）', async () => {
    const { store } = await loadedStore();
    globalThis.fetch = async () => {
      throw new TypeError('Failed to fetch');
    };

    const result = await store.sendMessage('你好', []);

    expect(result.ok).toBe(false);
    expect(store.state.online).toBe(false);
    expect(store.state.pending.length).toBe(1);
  });

  test('离线时直接拒绝发送，不产生 pending（闸门在 store 里也有一道）', async () => {
    const { store } = await loadedStore();
    store.mutable.online = false;

    const result = await store.sendMessage('你好', []);

    expect(result).toEqual({ ok: false, offline: true });
    expect(store.state.pending.length).toBe(0);
  });

  test('已归档私信只读：不产生 pending、不发请求', async () => {
    const dm = { id: 'dm-1', kind: 'dm', peer: { id: 'mem-1', display_name: 'Ada', avatar_slot: 1, deleted_at: '2026-10-01T00:00:00Z' }, last_seq: 0, last_read_seq: 0, unread_count: 0 };
    const stub = createFetchStub(routes({ '/api/workspace': () => jsonResponse({ member: OWNER, members: [OWNER, ADA], channels: [], dms: [dm], agents: [ADA] }) }));
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();
    store.setActiveTargetId('dm-1');
    const before = stub.calls.length;

    const result = await store.sendMessage('你好', []);

    expect(result).toEqual({ ok: false, archived: true });
    expect(stub.calls.length).toBe(before);
  });

  test('空正文不发送（前后空白也算空）', async () => {
    const { store } = await loadedStore();
    const result = await store.sendMessage('   ', []);
    expect(result).toEqual({ ok: false });
    expect(store.state.pending.length).toBe(0);
  });

  test('失败项可以重试：重试把同一条正文再发一次', async () => {
    const { store } = await loadedStore();
    globalThis.fetch = async () => {
      throw new TypeError('Failed to fetch');
    };
    await store.sendMessage('你好', []);
    const failed = store.state.pending[0];

    globalThis.fetch = createFetchStub(
      routes({
        '/api/channels/ch-1/messages': (url, request) =>
          request.method === 'POST'
            ? jsonResponse({ message: message(1) }, 201)
            : jsonResponse({ channel: channel(), messages: [] }),
      }),
    );
    const result = await store.retryPending(failed.clientId);
    await flush();

    expect(result.ok).toBe(true);
    expect(store.state.pending.length).toBe(0);
  });
});

describe('首次成功载入的口径', () => {
  test('第一次成功为真，之后的静默刷新为假（重连不得把回看历史的人拉回底部）', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const store = makeStore(stub);

    const first = await store.loadWorkspace();
    const second = await store.loadWorkspace({ silent: true, keepActive: true });

    expect(first.firstSuccessfulLoad).toBe(true);
    expect(second.firstSuccessfulLoad).toBe(false);
  });

  test('首载失败后重连成功的**那一次**仍算首次成功载入', async () => {
    let workspaces = 0;
    const stub = createFetchStub(
      routes({
        '/api/workspace': () => {
          workspaces += 1;
          return workspaces === 1
            ? jsonResponse({ detail: 'unavailable' }, 503)
            : jsonResponse({
                member: OWNER,
                members: [OWNER, ADA],
                channels: [channel()],
                dms: [],
                agents: [ADA],
                model_providers: [],
                local_providers: [],
                remote_consents: [],
              });
        },
      }),
    );
    globalThis.fetch = stub;
    const store = makeStore(stub);

    const failed = await store.loadWorkspace({ silent: true });
    const recovered = await store.loadWorkspace({ silent: true, keepActive: true });

    expect(failed.ok).toBe(false);
    expect(failed.firstSuccessfulLoad).toBe(false);
    expect(recovered.ok).toBe(true);
    // 「失败过的第一次成功」仍是第一次成功——否则首载失败的用户会停在消息流顶部。
    expect(recovered.firstSuccessfulLoad).toBe(true);
  });

  test('失败不把「已经成功过」这件事抹掉：之后的失败也不会让下次刷新变成「首次」', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();

    globalThis.fetch = createFetchStub({
      '/api/settings': () => jsonResponse({ settings: {} }),
      '/api/workspace': () => jsonResponse({ detail: 'down' }, 503),
    });
    const failed = await store.loadWorkspace({ silent: true });
    expect(failed.ok).toBe(false);

    globalThis.fetch = createFetchStub(routes());
    const again = await store.loadWorkspace({ silent: true, keepActive: true });
    expect(again.firstSuccessfulLoad).toBe(false);
  });
});

describe('已见 seq 游标', () => {
  test('refreshMessages 重建游标；absorbMessage 只推进不重复', async () => {
    const stub = createFetchStub(
      routes({
        '/api/channels/ch-1/messages': () =>
          jsonResponse({ channel: channel({ last_seq: 2 }), messages: [message(1), message(2)] }),
      }),
    );
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();

    expect(store.lastSeqOf('ch-1')).toBe(2);
    store.absorbMessage('ch-1', message(5));
    expect(store.lastSeqOf('ch-1')).toBe(5);
    // 频道行上的 last_seq 也跟着推进（后端事实的镜像）。
    expect(store.state.channels[0].last_seq).toBe(5);
  });

  test('其它 target 的消息不写进当前流，也不推进当前游标', async () => {
    const stub = createFetchStub(routes());
    globalThis.fetch = stub;
    const store = makeStore(stub);
    await store.loadWorkspace();

    store.absorbMessage('ch-other', message(9));

    expect(store.state.messages.length).toBe(0);
    expect(store.lastSeqOf('ch-1')).toBe(0);
  });
});
