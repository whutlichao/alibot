/* 应用接线（`src/app-context.js`）的用例

   两件在旧实现里由 `connectStream()` 的 `onopen` 承担、且**静默失败**的事：
   1. 重连成功后要**全量重取**——全局流不补拉（没有单一 target 的游标），不重取就等于断线期间
      的变化永远丢了，而界面上一点异常都看不出来；
   2. 「首次成功载入」的判定要穿透到调用方（它决定要不要把消息流贴到最新一条）。

   用替身 EventSource 推生命周期，用 fetch 替身数请求次数；不联网。 */
import { beforeEach, describe, expect, test } from 'vitest';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const { createAppContext } = await import('../src/app-context.js');

const OWNER = { id: 'owner', kind: 'owner', display_name: 'owner', avatar_slot: 4 };

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
}

function channel() {
  return { id: 'ch-1', name: 'design', description: '', last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function routes() {
  return {
    '/api/settings': () => jsonResponse({ settings: { locale: 'zh-CN', message_density: 'comfortable', theme: 'system' } }),
    '/api/workspace': () =>
      jsonResponse({
        member: OWNER,
        members: [OWNER],
        channels: [channel()],
        dms: [],
        agents: [],
        model_providers: [],
        local_providers: [],
        remote_consents: [],
      }),
    '/api/channels/ch-1/messages': () => jsonResponse({ channel: channel(), messages: [] }),
    '/api/channels/ch-1/round-outcomes': () => jsonResponse({ round_outcomes: [] }),
    '/api/channels/ch-1/members': () => jsonResponse({ members: [OWNER] }),
    '/api/member-statuses': () => jsonResponse({ statuses: {} }),
    '/api/channels/ch-1/read': () => jsonResponse({ channel: channel(), channels: [channel()] }),
  };
}

function makeContext(stub) {
  globalThis.fetch = stub;
  localStorage.setItem('alibot.activeChannel', 'ch-1');
  return createAppContext({
    toast: () => {},
    sourceFactory: () => FakeEventSource,
    retryMs: 10,
  });
}

beforeEach(() => {
  FakeEventSource.instances = [];
  localStorage.clear();
});

/** 等**真实**时间过去一点点。
 *
 * 刻意不用假时钟：这一层要验的是「定时器到点后真的重开连接」，而假时钟下每个 `await` 链
 * 都要手工推进（`flush()` 的 `setTimeout(0)` 也永远不会跑）。重开间隔本来就写成 10ms，
 * 真等 30ms 比维护一串 `advanceTimersByTime` 更稳、也更快。 */
function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('接线', () => {
  test('start() 读设置 + 全量快照 + 开一条流，并把首次成功载入透出去', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);

    const result = await ctx.start();

    expect(result.ok).toBe(true);
    expect(result.firstSuccessfulLoad).toBe(true);
    expect(stub.to('/api/settings').length).toBe(1);
    expect(stub.to('/api/workspace').length).toBe(1);
    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.instances[0].url).toBe('/api/events');
  });

  test('设置铺到根元素：语言 / 主题 / 密度三个档位各一个落点', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();

    expect(document.documentElement.lang).toBe('zh-CN');
    expect(document.documentElement.dataset.theme).toBe('system');
    expect(document.documentElement.dataset.density).toBe('comfortable');
  });

  test('第二次 start() 不再是「首次成功载入」', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);

    await ctx.start();
    const second = await ctx.start();

    expect(second.firstSuccessfulLoad).toBe(false);
  });
});

describe('事件流的两个生命周期动作', () => {
  test('重连成功 ⇒ 全量重取一次（全局流不补拉，不重取就等于丢消息）', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    expect(stub.to('/api/workspace').length).toBe(1);

    // 断线：后端不可达。
    FakeEventSource.instances[0].onerror();
    expect(ctx.state.online).toBe(false);

    // 到点重开，并连上。
    await wait(30);
    expect(FakeEventSource.instances.length).toBe(2);
    FakeEventSource.instances[1].onopen();
    await flush();

    expect(ctx.state.online).toBe(true);
    expect(stub.to('/api/workspace').length).toBe(2);
  });

  test('首次连上（没掉过线）不触发重取：那不是「补齐」', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();

    FakeEventSource.instances[0].onopen();
    await flush();

    expect(stub.to('/api/workspace').length).toBe(1);
  });

  test('断线清掉「正在回复」：断开期间不可能有 in-flight 事实', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    ctx.mutable.typing = { 'ch-1': 'mem-1' };
    ctx.mutable.activeRounds = { 'ch-1': { 'reply-1': 'mem-1' } };

    FakeEventSource.instances[0].onerror();

    expect(ctx.state.typing).toEqual({});
    expect(ctx.state.activeRounds).toEqual({});
  });
});

describe('帧分流', () => {
  test('消息帧：按 seq 落进当前流，并推进 target 的 last_seq', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    const source = FakeEventSource.instances[0];

    const message = {
      id: 'msg-1',
      target_id: 'ch-1',
      seq: 1,
      kind: 'agent',
      body: '来自 Agent 的回复',
      created_at: '2026-10-26T10:00:00.000Z',
      mention_snapshot: [],
      author: { id: 'mem-1', kind: 'agent', display_name: 'Ada', avatar_slot: 1 },
    };
    (source.listeners.get('message') || []).forEach((listener) =>
      listener({ data: JSON.stringify({ target_id: 'ch-1', message }) }),
    );
    await flush();

    expect(ctx.state.messages.map((item) => item.seq)).toEqual([1]);
    expect(ctx.state.channels[0].last_seq).toBe(1);
  });

  test('typing 帧：active 时记下「谁在回」，inactive 时清掉', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    const source = FakeEventSource.instances[0];
    const emit = (payload) =>
      (source.listeners.get('typing') || []).forEach((listener) =>
        listener({ data: JSON.stringify(payload) }),
      );

    emit({ target_id: 'ch-1', member_id: 'mem-1', active: true });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-1']);

    emit({ target_id: 'ch-1', member_id: 'mem-1', active: false });
    expect(ctx.state.typing['ch-1']).toBeUndefined();
  });

  test('live-only 帧不写消息流（它们没有可补拉的 seq）', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    const source = FakeEventSource.instances[0];

    (source.listeners.get('agent_event') || []).forEach((listener) =>
      listener({
        data: JSON.stringify({
          target_id: 'ch-1',
          reply_id: 'reply-1',
          member_id: 'mem-1',
          event_type: 'REPLY_START',
        }),
      }),
    );

    expect(ctx.state.messages.length).toBe(0);
    expect(ctx.state.activeRounds['ch-1']).toEqual({ 'reply-1': 'mem-1' });
  });
});
