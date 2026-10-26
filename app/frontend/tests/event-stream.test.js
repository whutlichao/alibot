/* 全局事件流（`src/lib/event-stream.js`）的用例

   断言的是**外部可观察行为**：开几条连接、哪些帧落到了哪里、断线与轮换分别做了什么。
   连接用替身（`FakeEventSource`），不联网；帧就是后端会发的 JSON。 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { loadStudioGlobals } from './helpers/studio-globals.js';

loadStudioGlobals();

const { createEventStream, isLiveEventType, isMessageEventType, GLOBAL_EVENT_PATH } = await import(
  '../src/lib/event-stream.js'
);

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

  /** 推一帧（`event.data` 是 JSON 串）。 */
  emit(type, payload) {
    (this.listeners.get(type) || []).forEach((listener) => listener({ data: JSON.stringify(payload) }));
  }
}

function makeStream(handlers) {
  const frames = [];
  const states = [];
  const stream = createEventStream({
    onFrame: (payload, type) => {
      frames.push({ payload, type });
      if (handlers && handlers.onFrame) handlers.onFrame(payload, type);
    },
    onOpen: handlers && handlers.onOpen,
    onError: handlers && handlers.onError,
    onState: (value) => states.push(value),
    sourceFactory: () => FakeEventSource,
    retryMs: 10,
    rotateMs: 1,
  });
  return { stream, frames, states };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('连接形状', () => {
  test('整页只有一条流，端点是 /api/events（不带 target、不带游标）', () => {
    const { stream } = makeStream();
    stream.start();
    stream.start();

    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.instances[0].url).toBe(GLOBAL_EVENT_PATH);
    expect(GLOBAL_EVENT_PATH).toBe('/api/events');
  });

  test('事件类型词表：live-only 与可补拉的两组互不重叠', () => {
    expect(isLiveEventType('agent_event')).toBe(true);
    expect(isLiveEventType('message')).toBe(false);
    expect(isMessageEventType('message')).toBe(true);
    expect(isMessageEventType('agent_event')).toBe(false);
    expect(isMessageEventType('some-future-type')).toBe(false);
  });
});

describe('帧分流', () => {
  test('缺 target_id 的帧被丢弃（不猜归属，也不写任何游标）', () => {
    const { stream, frames } = makeStream();
    stream.start();
    const source = FakeEventSource.instances[0];

    source.emit('message', { message: { seq: 1 } });

    expect(frames.length).toBe(0);
  });

  test('解不开的帧被丢弃（它不可能是后端发出的）', () => {
    const { stream, frames } = makeStream();
    stream.start();
    const source = FakeEventSource.instances[0];

    (source.listeners.get('message') || []).forEach((listener) => listener({ data: 'not json' }));

    expect(frames.length).toBe(0);
  });

  test('带归属的帧原样交给落点（类型一起给）', () => {
    const { stream, frames } = makeStream();
    stream.start();
    const source = FakeEventSource.instances[0];

    source.emit('typing', { target_id: 'ch-1', member_id: 'mem-1', active: true });

    expect(frames).toEqual([
      { payload: { target_id: 'ch-1', member_id: 'mem-1', active: true }, type: 'typing' },
    ]);
  });
});

describe('断线与轮换', () => {
  test('断线：状态转 reconnecting、回调一次、按间隔重开', async () => {
    const errors = [];
    const { stream, states } = makeStream({ onError: () => errors.push('error') });
    stream.start();
    const first = FakeEventSource.instances[0];

    first.onerror();
    expect(errors.length).toBe(1);
    expect(states).toContain('reconnecting');
    expect(first.closed).toBe(true);

    await vi.advanceTimersByTimeAsync(20);
    expect(FakeEventSource.instances.length).toBe(2);
  });

  test('服务端轮换（event: rotate）不算掉线：不触发错误、只重连一次', async () => {
    const errors = [];
    const { stream, states } = makeStream({ onError: () => errors.push('error') });
    stream.start();
    const first = FakeEventSource.instances[0];

    (first.listeners.get('rotate') || []).forEach((listener) => listener({}));
    await vi.advanceTimersByTimeAsync(5);

    expect(errors.length).toBe(0);
    expect(states).not.toContain('reconnecting');
    expect(FakeEventSource.instances.length).toBe(2);
  });

  test('重连成功：回调带 recovered，且只在真的掉过线时标 reconnected', async () => {
    const opens = [];
    const { stream } = makeStream({ onOpen: (info) => opens.push(info) });
    stream.start();
    const first = FakeEventSource.instances[0];

    first.onopen();
    expect(opens[0]).toEqual({ recovered: true, reconnected: false });

    first.onerror();
    await vi.advanceTimersByTimeAsync(20);
    FakeEventSource.instances[1].onopen();
    expect(opens[1]).toEqual({ recovered: true, reconnected: true });
  });

  test('stop() 关掉连接并清掉待重开的定时器（页面卸载不留悬空连接）', async () => {
    const { stream } = makeStream();
    stream.start();
    const first = FakeEventSource.instances[0];
    first.onerror();

    stream.stop();
    await vi.advanceTimersByTimeAsync(50);

    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances.length).toBe(1);
  });

  test('没有 EventSource 的宿主不抛（测试替身 / 极旧浏览器）', () => {
    const stream = createEventStream({ sourceFactory: () => undefined });
    expect(() => stream.start()).not.toThrow();
  });
});
