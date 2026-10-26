/* 全局事件流（SSE）：整页**一条**连接，断线后重开（ADR-0020）

   为什么是一条全局流（沿用旧实现的判据）：浏览器对单一 origin 的 HTTP/1.1 并发连接上限是 6 条，
   而 uvicorn 没有多路复用。按 target 各开一条常驻 `EventSource` 会把连接池占满，之后任何
   `fetch`（发消息、切频道）与 F5 的文档请求都在排队——页面渲染出来了但完全不响应。
   全局流把「连接数」与「target 数」解耦成两件事。

   重连策略不变：断线时**主动关掉连接、稍后重开**，不依赖浏览器带 `Last-Event-ID` 自动重连——
   全局流不补拉（它没有单一 target 的游标），断线期间的变化由重连成功后的全量重取补齐。

   本文件只做「连接 + 分流」：帧怎么落到状态里由 `handleFrame` 给（调用方注入），
   于是「哪一帧算数」的判据只有一份，测试可以只喂帧看结果。 */

/** 整页唯一的全局事件流端点：任意 target 的事件都从这里来，`target_id` 是帧内的归属正本。 */
export const GLOBAL_EVENT_PATH = '/api/events';

/** live-only 运行事件（ADR-0020）：没有可补拉的 seq，因此不写 `seenSeqs`、不进消息流 / 未读 / 搜索。 */
export const LIVE_EVENT_TYPES = [
  'agent_event',
  'team_assignment',
  'plan_update',
  'sop_event',
  'approval_required',
  'approval_resolved',
  'round.interrupted',
  'round.active',
];

/** 可补拉的消息类事件（其余一律忽略：词表以后可能变大，旧前端不能因此报错）。 */
export const MESSAGE_EVENT_TYPES = ['message', 'member_status', 'typing', 'round_outcome'];

export function isLiveEventType(type) {
  return LIVE_EVENT_TYPES.indexOf(type) !== -1;
}

export function isMessageEventType(type) {
  return MESSAGE_EVENT_TYPES.indexOf(type) !== -1;
}

/**
 * 建一条可重开的事件流。
 *
 * @param {object} options
 * @param {(payload: object, type: string) => void} options.onFrame 每一帧的落点（已 JSON.parse）
 * @param {(info: {recovered: boolean}) => void} [options.onOpen] 连上（含重连成功）
 * @param {() => void} [options.onError] 断线（含我们自己关掉之后的意外）
 * @param {(state: string) => void} [options.onState] 连接状态（`open` / `reconnecting`）
 * @param {number} [options.retryMs] 断线重试间隔
 * @param {() => typeof EventSource} [options.sourceFactory] 测试替身入口
 */
export function createEventStream(options) {
  const config = options || {};
  const retryMs = config.retryMs == null ? 1500 : config.retryMs;
  const rotateMs = config.rotateMs == null ? 250 : config.rotateMs;
  const factory = config.sourceFactory || (() => globalThis.EventSource);
  const entry = { source: null, timer: null, rotating: false, state: 'connecting', wasOffline: false };

  function notifyState() {
    if (config.onState) config.onState(entry.state);
  }

  function connect() {
    const Source = factory();
    if (typeof Source !== 'function') return; // 测试替身 / 极旧浏览器：没有流也不抛
    // 全局流不带 target、不带游标：它不补拉（重连补齐由全量重取承担）。
    const source = new Source(GLOBAL_EVENT_PATH);
    entry.source = source;
    entry.rotating = false;
    source.addEventListener('rotate', () => {
      // 服务端的例行轮换（`event: rotate`）不算掉线：它只是要求重新连一次。
      entry.rotating = true;
      source.close();
      entry.timer = setTimeout(connect, rotateMs);
    });
    source.onopen = () => {
      const recovered = entry.state !== 'open';
      entry.state = 'open';
      notifyState();
      if (recovered && entry.wasOffline) {
        entry.wasOffline = false;
        if (config.onOpen) config.onOpen({ recovered: true, reconnected: true });
      } else if (config.onOpen) {
        config.onOpen({ recovered, reconnected: false });
      }
    };
    source.onerror = () => {
      if (entry.rotating) return; // 我们自己关的：不是错误
      // 事件流断开是「后端不可达」最直接的证据：它就是后端的连接。
      entry.state = 'reconnecting';
      entry.wasOffline = true;
      notifyState();
      if (config.onError) config.onError();
      source.close();
      entry.timer = setTimeout(connect, retryMs);
    };
    [...MESSAGE_EVENT_TYPES, ...LIVE_EVENT_TYPES].forEach((type) => {
      source.addEventListener(type, (event) => {
        let payload = null;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return; // 解不开的帧直接丢弃：它不可能是后端发出的
        }
        // 缺 `target_id` 的帧**丢弃**：不猜归属，也不写任何 target 的游标。
        if (!payload || !payload.target_id) return;
        if (config.onFrame) config.onFrame(payload, type);
      });
    });
  }

  function start() {
    if (entry.source) return entry;
    connect();
    return entry;
  }

  function stop() {
    if (entry.timer) clearTimeout(entry.timer);
    entry.timer = null;
    if (entry.source) entry.source.close();
    entry.source = null;
    entry.state = 'closed';
    notifyState();
  }

  return { start, stop, entry };
}
