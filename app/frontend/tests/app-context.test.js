/* 应用接线（`src/app-context.js`）的用例

   两件在旧实现里由 `connectStream()` 的 `onopen` 承担、且**静默失败**的事：
   1. 重连成功后要**全量重取**——全局流不补拉（没有单一 target 的游标），不重取就等于断线期间
      的变化永远丢了，而界面上一点异常都看不出来；
   2. 「首次成功载入」的判定要穿透到调用方（它决定要不要把消息流贴到最新一条）。

   用替身 EventSource 推生命周期，用 fetch 替身数请求次数；不联网。 */
import { beforeEach, describe, expect, test } from 'vitest';
import { mount } from '@vue/test-utils';
import { loadStudioGlobals, createFetchStub, jsonResponse, flush } from './helpers/studio-globals.js';

loadStudioGlobals();

const { createAppContext } = await import('../src/app-context.js');
const MessageStream = (await import('../src/components/MessageStream.vue')).default;

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

/* ─── 「正在回复」的两个层级（#333） ────────────────────────────────────────

   症状：私信里「David 正在回复…」在回复消息出现前约 5.4 秒就消失了。真实帧序是——

     typing{active:true, scope:"round"} → agent_event REPLY_START → agent_event REPLY_END
     →（记忆写入，实测 5.4 秒；消息还没落库）→ message → round_outcome
     → typing{active:false, scope:"round"}

   `REPLY_END` 只是 **reply 级**事实（模型正文生成完），**轮次级**事实（`typing`）要到
   `typing{active:false, scope:"round"}` 才结束。旧实现在 `REPLY_END` 里顺手清 `typing`，
   于是 reply 级事件清掉了轮次级事实——指示提前消失。

   第二个层级：team 成员的 `typing{scope:"member"}` 帧只表达成员的在飞（那条由
   `activeRounds` 表达），**不得**写 / 清队长的轮次槽位。 */

/** 把一帧按事件类型推给全局流（与浏览器 `EventSource` 同一条通路）。 */
function feed(source, type, payload) {
  (source.listeners.get(type) || []).forEach((listener) => listener({ data: JSON.stringify(payload) }));
}

function agentReplyMessage() {
  return {
    id: 'msg-1',
    target_id: 'ch-1',
    seq: 1,
    kind: 'agent',
    body: 'David 的回复',
    created_at: '2026-10-28T10:00:00.000Z',
    mention_snapshot: [],
    author: { id: 'mem-1', kind: 'agent', display_name: 'David', avatar_slot: 1 },
  };
}

/** 「正在回复」那一行现在是否渲染出来（判据落在 DOM 上，不是只读内部状态）。 */
function typingLineVisible(ctx) {
  const wrapper = mount(MessageStream, {
    props: { state: ctx.state, messages: [], typingNames: ctx.typingNames.value },
  });
  return wrapper.find('[data-od-id="typing-line"]').exists();
}

describe('「正在回复」的两个层级（#333）', () => {
  test('私信：REPLY_END 之后指示仍在，直到轮次的 typing{active:false} 才消失', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    const source = FakeEventSource.instances[0];

    // 1. 轮次开始：轮次级事实。
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-1', active: true, scope: 'round', seq: 1 });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-1']);

    // 2. reply 开始：reply 级事实，与轮次槽位并存。
    feed(source, 'agent_event', {
      target_id: 'ch-1',
      member_id: 'mem-1',
      reply_id: 'reply-1',
      event_type: 'REPLY_START',
    });
    expect(ctx.state.activeRounds['ch-1']).toEqual({ 'reply-1': 'mem-1' });

    // 3. 模型正文生成完（记忆写入还要 5.4 秒；消息尚未落库）。
    feed(source, 'agent_event', {
      target_id: 'ch-1',
      member_id: 'mem-1',
      reply_id: 'reply-1',
      event_type: 'REPLY_END',
    });
    expect(ctx.state.activeRounds['ch-1']).toBeUndefined();
    // 旧实现在这里红：reply 级事件把轮次级事实一起清了。
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-1']);

    // 「正在回复」那一行仍在界面上。
    expect(typingLineVisible(ctx)).toBe(true);

    // 4. 消息落库：仍不结束轮次。
    feed(source, 'message', { target_id: 'ch-1', message: agentReplyMessage() });
    await flush();
    expect(typingLineVisible(ctx)).toBe(true);
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-1']);

    // 5. 轮次收口（round_outcome → typing{active:false, scope:"round"}）。
    feed(source, 'round_outcome', {
      target_id: 'ch-1',
      outcome: {
        id: 'out-1',
        member_id: 'mem-1',
        target_id: 'ch-1',
        base_seq: 1,
        status: 'replied',
        reason: '说了话',
        reply_seq: 1,
      },
    });
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-1', active: false, scope: 'round' });
    expect(ctx.state.typing['ch-1']).toBeUndefined();
    expect(ctx.typingNames.value).toEqual([]);
    expect(typingLineVisible(ctx)).toBe(false);
  });

  test('team：两个成员同时在飞名字齐全；一个成员 REPLY_END 只收敛它自己，成员帧不碰队长轮次', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    const source = FakeEventSource.instances[0];

    // 队长这一轮开始（轮次级）。
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-leader', active: true, scope: 'round' });
    feed(source, 'agent_event', {
      target_id: 'ch-1',
      member_id: 'mem-leader',
      reply_id: 'reply-leader',
      event_type: 'REPLY_START',
    });
    // 成员分派：先一条 reply 级事件，再一条团队成员级 typing 帧（#210 的多成员名字）。
    feed(source, 'agent_event', {
      target_id: 'ch-1',
      member_id: 'mem-member',
      reply_id: 'reply-member',
      event_type: 'REPLY_START',
    });
    feed(source, 'typing', {
      target_id: 'ch-1',
      member_id: 'mem-member',
      active: true,
      scope: 'member',
      reason: 'team-assignment',
    });

    // 两个 reply 各自一个停止句柄；名字两个都列（#210）。
    expect(Object.keys(ctx.state.activeRounds['ch-1'])).toEqual(['reply-leader', 'reply-member']);
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-leader', 'mem-member']);
    // 成员帧不写轮次槽位：队长这一轮的轮次指示仍是队长。
    expect(ctx.state.typing['ch-1']).toBe('mem-leader');

    // 一个成员收口：只收敛它自己。
    feed(source, 'agent_event', {
      target_id: 'ch-1',
      member_id: 'mem-member',
      reply_id: 'reply-member',
      event_type: 'REPLY_END',
    });
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-leader']);
    expect(ctx.state.typing['ch-1']).toBe('mem-leader');

    // 成员的 typing{active:false, scope:"member"} 不得清队长的轮次指示。
    feed(source, 'typing', {
      target_id: 'ch-1',
      member_id: 'mem-member',
      active: false,
      scope: 'member',
      reason: 'team-assignment',
    });
    expect(ctx.state.typing['ch-1']).toBe('mem-leader');
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-leader']);

    // 队长轮次收口（scope:"round"）才清轮次槽位；队长的 reply 仍在飞，名字仍按 reply 级列。
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-leader', active: false, scope: 'round' });
    expect(ctx.state.typing['ch-1']).toBeUndefined();
    expect(ctx.typingNames.value.map((item) => item.id)).toEqual(['mem-leader']);

    feed(source, 'agent_event', {
      target_id: 'ch-1',
      member_id: 'mem-leader',
      reply_id: 'reply-leader',
      event_type: 'REPLY_END',
    });
    expect(ctx.typingNames.value).toEqual([]);
  });

  test('清理矩阵：只有轮次级事实结束才清 typing；缺 scope 按 round 处理（向后兼容）', async () => {
    const stub = createFetchStub(routes());
    const ctx = makeContext(stub);
    await ctx.start();
    const source = FakeEventSource.instances[0];

    // 旧后端不带 scope：按轮次级处理。
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-1', active: true });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-1', active: false });
    expect(ctx.state.typing['ch-1']).toBeUndefined();

    // 成员级帧不清；轮次级帧的 member_id 与槽位不一致时也不清（不误清别人的轮次）。
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-1', active: true, scope: 'round' });
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-1', active: false, scope: 'member' });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-2', active: false, scope: 'round' });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');

    // 成员状态点离开 working：与该成员一致时清。
    feed(source, 'member_status', { target_id: 'ch-1', member_id: 'mem-2', status: 'online' });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    feed(source, 'member_status', { target_id: 'ch-1', member_id: 'mem-1', status: 'online' });
    expect(ctx.state.typing['ch-1']).toBeUndefined();

    // 停止句柄快照（`round.active`）是 reply 级事实：只铺 `activeRounds`，不写也不清轮次槽位。
    // 空快照尤其不能清——正文已生成、记忆还在写的那 5.4 秒里 reply 句柄正好是空的。
    feed(source, 'typing', { target_id: 'ch-1', member_id: 'mem-1', active: true, scope: 'round' });
    feed(source, 'round.active', { target_id: 'ch-1', rounds: {} });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    feed(source, 'round.active', { target_id: 'ch-1', rounds: { 'reply-9': 'mem-9' } });
    expect(ctx.state.typing['ch-1']).toBe('mem-1');
    expect(ctx.state.activeRounds['ch-1']).toEqual({ 'reply-9': 'mem-9' });

    // 用户中断：reply 级清空之后轮次事实也结束。
    feed(source, 'round.active', { target_id: 'ch-1', rounds: {} });
    feed(source, 'agent_event', {
      target_id: 'ch-1',
      member_id: 'mem-1',
      reply_id: 'reply-1',
      event_type: 'REPLY_START',
    });
    feed(source, 'round.interrupted', { target_id: 'ch-1', reply_ids: ['reply-1'] });
    expect(ctx.state.activeRounds['ch-1']).toBeUndefined();
    expect(ctx.state.typing['ch-1']).toBeUndefined();
  });
});
