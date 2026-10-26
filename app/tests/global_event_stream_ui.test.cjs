/**
 * 整页一条全局事件流（`GET /api/events`）：连接数与 target 数彻底解耦。
 *
 * 背景（实测根因）：`syncStreams()` 原先给**每一个 target**（每个频道 + 每个私信）各开一条常驻
 * `EventSource`。Chromium 对单一 origin 的 HTTP/1.1 并发连接上限是 6，本机 uvicorn 是 HTTP/1.1
 * （无多路复用）；2 频道 + 4 私信正好占满连接池，之后任何 `fetch`（发消息、切频道、刷新列表）
 * 以及 F5 的文档请求都排队，表现为 `/studio/` 渲染出来但完全不响应。
 *
 * 接缝沿用既有那一套（`vm` 装载 `i18n.js` + `app.js`，`fetch` 替身，DOM 替身，`EventSource` 替身）：
 * 不新开第二套前端测试框架、不新开 seam。断言只落在外部可观察行为上：
 *
 * 1. 六个 target 的快照载入后**最多一条**事件流，且 url 是 `/api/events`（不是任何
 *    per-target `/events?after_seq=`）——这是本票的核心断言；
 * 2. 全局流里的 `message` 帧按 `payload.target_id` 归属，与改动前的按 target 消费结果一致；
 * 3. 帧缺 `target_id` ⇒ 丢弃：不猜归属、不写 `seenSeqs`（那份集合同时是重连游标）；
 * 4. `onerror`/`onopen` 的线上态与 `streamState` 语义不变；`rotate` 不算掉线；
 * 5. `EventSource` 不存在时（本仓 `mobile_shell.test.cjs` 的场景）优雅降级，不抛错。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const AGENT = { id: "mem-1", kind: "agent", display_name: "小明", avatar_slot: 1 };
const OWNER = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };

/* ─── DOM 替身（与既有用例族同形，`scratch/sse_events_ui` 同款） ───────────── */

class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.parentNode = null;
    this.style = {};
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this._textContent = "";
    const classes = new Set();
    this.classList = {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle: (name, force) => {
        const on = force === undefined ? !classes.has(name) : Boolean(force);
        if (on) classes.add(name);
        else classes.delete(name);
        return on;
      },
    };
  }

  set className(value) {
    String(value)
      .split(/\s+/)
      .filter(Boolean)
      .forEach((name) => this.classList.add(name));
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  get firstChild() {
    return this.children[0] || null;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }

  replaceChildren(...children) {
    this._textContent = "";
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }

  set id(value) {
    this.attributes.id = String(value);
  }

  get id() {
    return Object.prototype.hasOwnProperty.call(this.attributes, "id") ? this.attributes.id : "";
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    if (Object.prototype.hasOwnProperty.call(this.attributes, name)) return this.attributes[name];
    if (name.startsWith("data-")) {
      const value = datasetValue(this, name);
      return value === undefined ? null : String(value);
    }
    return null;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  async dispatchEvent(event) {
    const listeners = this.listeners.get(event.type) || [];
    for (const listener of listeners) await listener.call(this, event);
  }

  matches(selector) {
    return matchesSelector(this, selector);
  }

  get tabIndex() {
    const declared = this.getAttribute("tabindex");
    return declared === null ? 0 : Number(declared);
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  descendants() {
    return [this, ...this.children.flatMap((child) => child.descendants())];
  }

  querySelectorAll(selector) {
    return this.descendants().filter((node) => node !== this && matchesSelector(node, selector));
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  closest(selector) {
    let node = this;
    while (node) {
      if (matchesSelector(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
}

function matchesSelector(node, selector) {
  const withValue = /^\[([a-zA-Z0-9_-]+)="([^"]*)"\]$/.exec(selector);
  if (withValue) {
    const [, name, value] = withValue;
    const declared = node.getAttribute(name);
    if (declared !== null) return declared === value;
    return false;
  }
  const attribute = /^\[([a-zA-Z0-9_-]+)\]$/.exec(selector);
  if (attribute) {
    const name = attribute[1];
    if (Object.prototype.hasOwnProperty.call(node.attributes, name)) return true;
    if (name.startsWith("data-")) return datasetValue(node, name) !== undefined;
    return false;
  }
  if (selector.startsWith(".")) return node.classList.contains(selector.slice(1));
  return node.tagName === selector;
}

function datasetValue(node, attributeName) {
  const key = attributeName
    .slice("data-".length)
    .replace(/-([a-z])/g, (_whole, letter) => letter.toUpperCase());
  if (node.dataset && node.dataset[key] !== undefined) return node.dataset[key];
  return Object.prototype.hasOwnProperty.call(node.attributes, attributeName)
    ? node.attributes[attributeName]
    : undefined;
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

/* ─── 工作区快照：2 频道 + 4 私信 = 6 个 target（就是占满连接池的那个数量） ──── */

function channelTarget(id, name) {
  return { id, name, description: "", last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function dmTarget(id, name) {
  return {
    id,
    agent_id: "mem-" + id,
    last_seq: 0,
    last_read_seq: 0,
    unread_count: 0,
    peer: { ...AGENT, id: "mem-" + id, display_name: name },
  };
}

const CHANNELS = [channelTarget("ch-1", "设计"), channelTarget("ch-2", "发布")];
const DMS = [
  dmTarget("dm-1", "小明"),
  dmTarget("dm-2", "小红"),
  dmTarget("dm-3", "小刚"),
  dmTarget("dm-4", "小美"),
];

function workspaceSnapshot() {
  return {
    member: { ...OWNER },
    members: [{ ...OWNER }, { ...AGENT }],
    channels: CHANNELS.map((item) => ({ ...item })),
    dms: DMS.map((item) => ({ ...item })),
    agents: [{ ...AGENT, enabled: true, status: "online" }],
    tasks: [],
    messages: [],
    outcomes: [],
    model_providers: [],
    local_providers: [],
    remote_consents: [],
    unread_total: 0,
    settings: { locale: "zh-CN", message_density: "comfortable", theme: "system" },
  };
}

const SETTINGS = { locale: "zh-CN", message_density: "comfortable", theme: "system" };

/** 读路径的默认响应：够 `loadWorkspace()` 完整跑完一轮（重连兜底会真的走它）。 */
function defaultResponse(url) {
  if (url === "/api/workspace") return workspaceSnapshot();
  if (url === "/api/settings") return { settings: SETTINGS };
  // 未读的正本在后端：到达一条「非当前 target」的消息会触发一次列表重取。
  if (url === "/api/channels") return { channels: CHANNELS.map((item) => ({ ...item })), unread_total: 0 };
  if (url === "/api/dms") return { dms: DMS.map((item) => ({ ...item })), unread_total: 0 };
  if (url === "/api/tasks") return { tasks: [] };
  if (url === "/api/reminders") return { reminders: [] };
  if (url === "/api/member-statuses") return { statuses: {} };
  if (url.startsWith("/api/approvals")) return { approvals: [] };
  if (url.endsWith("/round-outcomes")) return { outcomes: [] };
  if (url.endsWith("/messages")) return { messages: [] };
  if (url.endsWith("/members")) return { members: [] };
  if (url.endsWith("/config")) {
    return {
      config: {
        channel_id: url.split("/")[3],
        mode: "autonomous",
        team_scope: "channel+task",
        team_leader_member_id: null,
        leader_available: false,
        leader_reason: "",
        workspace_root: null,
        workspace_backend: "local",
        permission_mode: "ask-writes",
        web_access: "search",
      },
    };
  }
  return {};
}

/* ─── 装置：vm 装载 i18n.js + app.js，EventSource 替身记录 url ──────────────── */

function createHarness(options) {
  const config = options || {};
  const sources = [];
  const requests = [];
  // 重连纪律的读数：`setTimeout(fn, delay)` 的 delay 原样记下来，用例才能断言“250ms / 1500ms”
  // 这两个既有契约值（否则只证明了“会重连”，证明不了节奇）。
  const timerDelays = [];
  const elements = new Map();

  class FakeEventSource {
    constructor(url) {
      this.url = String(url);
      this.listeners = new Map();
      this.onopen = null;
      this.onerror = null;
      this.closed = false;
      sources.push(this);
    }

    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(listener);
    }

    close() {
      this.closed = true;
    }

    /** 走真入口：按 SSE 语义把一帧交给 app.js 注册的监听器。 */
    emit(type, payload) {
      const listeners = this.listeners.get(type) || [];
      assert.ok(listeners.length > 0, `app.js 没有注册 ${type} 监听器`);
      for (const listener of listeners) listener({ data: JSON.stringify(payload) });
    }
  }

  function newNode(tagName) {
    const node = new FakeNode(tagName);
    node.ownerDocument = document;
    return node;
  }

  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = newNode("div");
      node.hidden = HIDDEN_IN_HTML.has(selector);
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

  let document = null;
  document = {
    documentElement: newNode("html"),
    activeElement: null,
    hidden: false,
    querySelector: (selector) => elementFor(selector),
    querySelectorAll: () => [],
    createElement: (tagName) => newNode(tagName),
    createTextNode: (text) => {
      const node = newNode("#text");
      node.textContent = text;
      return node;
    },
    addEventListener: () => {},
  };

  const fetchStub = async (url) => {
    const href = String(url);
    requests.push(href);
    const body = config.fetch ? config.fetch(href) : defaultResponse(href);
    return { ok: true, status: 200, json: async () => body };
  };

  const globals = {
    document,
    fetch: fetchStub,
    URLSearchParams,
    window: { addEventListener: () => {}, matchMedia: () => ({ matches: false }) },
    console,
    setTimeout: (fn, delay) => {
      timerDelays.push(delay);
      return setImmediate(fn);
    },
    clearTimeout: () => {},
  };
  if (!config.noEventSource) globals.EventSource = FakeEventSource;
  const context = vm.createContext(globals);
  vm.runInContext(fs.readFileSync(path.join(STUDIO_DIR, "i18n.js"), "utf8"), context, {
    filename: "i18n.js",
  });
  // markdown.js 与 i18n.js 同层加载：它是纯函数模块，由 app.js 的 messageText() 调用（ADR-0037）。
  vm.runInContext(fs.readFileSync(path.join(STUDIO_DIR, "markdown.js"), "utf8"), context, {
    filename: "markdown.js",
  });
  const appPath = path.join(STUDIO_DIR, "app.js");
  const source = fs.readFileSync(appPath, "utf8");
  const startup = /\napplyStaticI18n\(document\);\nbindEvents\(\);\nloadWorkspace\(\);\s*$/;
  assert.match(source, startup, "app startup calls should remain at the end of app.js");
  vm.runInContext(
    source.replace(
      startup,
      "\nglobalThis.__globalStreamTest = { state, bindEvents, syncStreams, handleStreamEvent,"
        + " lastSeqOf, renderMessages };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__globalStreamTest;
  // 工作区快照就位（与 `loadWorkspace()` 落地的同一份状态），再由 `syncStreams()` 决定连接数。
  const snapshot = workspaceSnapshot();
  api.state.member = snapshot.member;
  api.state.members = snapshot.members;
  api.state.channels = snapshot.channels;
  api.state.dms = snapshot.dms;
  api.state.agents = snapshot.agents;
  api.state.activeChannelId = "ch-1";
  api.state.mode = "channel";
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  return {
    api,
    sources,
    requests,
    timerDelays,
    document,
    elementFor,
    targets: [...CHANNELS, ...DMS],
  };
}

function message(seq, targetId, body) {
  return {
    seq,
    body: body || "第 " + seq + " 条",
    kind: "agent",
    created_at: "2026-10-20T09:0" + (seq % 10) + ":00Z",
    author: { ...AGENT },
    target_id: targetId,
  };
}

/* ─── 用例 ───────────────────────────────────────────────────────────────── */

test("六个 target 的快照载入后只有一条全局流：/api/events，不带任何 per-target 游标", async () => {
  const harness = createHarness();
  assert.equal(harness.targets.length, 6, "场景就是占满连接池的 2 频道 + 4 私信");

  harness.api.syncStreams();
  await flush();

  assert.equal(harness.sources.length, 1, "整页最多一条事件流（连接数与 target 数解耦）");
  assert.equal(harness.sources[0].url, "/api/events", "全局流不带 target / 不带 after_seq");
  assert.ok(
    !harness.sources.some((source) => /\/events\?after_seq=/.test(source.url)),
    "不得再出现任何 per-target 事件流",
  );

  // target 集合增减不再开关连接：删掉一个私信、再加一个频道，仍然只有那一条。
  harness.api.state.dms = harness.api.state.dms.slice(0, 3);
  harness.api.syncStreams();
  await flush();
  harness.api.state.channels.push(channelTarget("ch-3", "新频道"));
  harness.api.syncStreams();
  await flush();

  assert.equal(harness.sources.length, 1, "target 集合变化不得再开连接");
  assert.equal(harness.sources[0].closed, false, "那条全局流一直活着");
});

test("全局流的 message 帧按 target_id 归属：当前 target 进消息面、其它 target 只推进游标", async () => {
  const harness = createHarness();
  harness.api.syncStreams();
  await flush();
  const source = harness.sources[0];

  source.emit("message", { type: "message", seq: 1, target_id: "ch-1", message: message(1, "ch-1") });
  await flush();
  assert.equal(harness.api.state.messages.length, 1, "当前 target 的消息照旧进消息面");
  assert.equal(harness.api.lastSeqOf("ch-1"), 1, "游标照旧推进（去重与重连兜底都靠它）");

  source.emit("message", { type: "message", seq: 1, target_id: "dm-2", message: message(1, "dm-2") });
  await flush();
  assert.equal(harness.api.state.messages.length, 1, "非当前 target 的消息不进当前消息面");
  assert.equal(harness.api.lastSeqOf("dm-2"), 1, "但它自己的游标推进");
  assert.ok(harness.requests.includes("/api/dms"), "未读徽标照旧按后端重取（未读的正本在库）");

  // 同一帧重复到达（补拉 + 实时并存的时序）：按 seq 去重，不出现两条。
  source.emit("message", { type: "message", seq: 1, target_id: "ch-1", message: message(1, "ch-1") });
  await flush();
  assert.equal(harness.api.state.messages.filter((item) => item.seq === 1).length, 1);
});

test("全局流的 live 运行事件按 target_id 归属记录（与 per-target 消费一致）", async () => {
  const harness = createHarness();
  harness.api.syncStreams();
  await flush();
  const source = harness.sources[0];

  source.emit("agent_event", {
    type: "agent_event",
    target_id: "ch-2",
    event_type: "TEXT_BLOCK_DELTA",
    member_id: "mem-1",
    reply_id: "reply-1",
    payload: { block_id: "b1", delta: "增量" },
  });
  source.emit("team_assignment", {
    type: "team_assignment",
    target_id: "dm-1",
    team_run_id: "run-1",
    assignment_id: "assignment-1",
    task_id: null,
    member_id: "mem-1",
    reply_id: "reply-1",
    state: "started",
  });
  await flush();

  assert.deepEqual(
    Array.from(harness.api.state.liveEvents.get("ch-2"), (event) => event.type),
    ["agent_event"],
  );
  assert.deepEqual(
    Array.from(harness.api.state.liveEvents.get("dm-1"), (event) => event.type),
    ["team_assignment"],
  );
  assert.equal(harness.api.lastSeqOf("ch-2"), 0, "live-only 事件不推进游标");
});

test("帧缺 target_id ⇒ 丢弃：不猜归属、不写 seenSeqs、不推进游标", async () => {
  const harness = createHarness();
  harness.api.syncStreams();
  await flush();
  const source = harness.sources[0];

  source.emit("message", { type: "message", seq: 7, message: message(7, "ch-1") });
  source.emit("member_status", { type: "member_status", seq: 7, member_id: "mem-1", status: "working" });
  source.emit("agent_event", { type: "agent_event", event_type: "REPLY_START", member_id: "mem-1", reply_id: "r1" });
  await flush();

  assert.equal(harness.api.state.messages.length, 0, "无归属的帧不得进任何消息面");
  assert.equal(harness.api.state.liveEvents.size, 0, "无归属的 live 帧不得被记到某个 target");
  const seen = harness.api.state.seenSeqs;
  const written = Array.from(seen.keys()).filter((key) => seen.get(key).size > 0);
  assert.deepEqual(written, [], "无归属的帧不得写任何 target 的 seenSeqs（那是重连游标）");
  harness.targets.forEach((target) => {
    assert.equal(harness.api.lastSeqOf(target.id), 0, `${target.id} 的游标不得被无归属帧推进`);
  });
});

test("onerror ⇒ 不可达 + reconnecting，onopen 恢复并只保留一条流", async () => {
  const harness = createHarness();
  harness.api.syncStreams();
  await flush();
  const first = harness.sources[0];

  first.onerror();
  assert.equal(harness.api.state.online, false, "事件流断开 = 后端不可达最直接的证据");
  assert.equal(harness.api.state.streamState, "reconnecting");
  assert.equal(harness.elementFor("#connBanner").hidden, false, "断线时横幅出现");
  assert.equal(first.closed, true, "断线先自己关掉连接，稍后用最新事实重开");

  await flush();
  assert.equal(harness.sources.length, 2, "重连只重开那一条全局流");
  assert.equal(harness.sources[1].url, "/api/events");
  assert.ok(harness.timerDelays.includes(1500), `断线重连的既有节奇是 1500ms：${harness.timerDelays}`);

  harness.sources[1].onopen();
  assert.equal(harness.api.state.online, true, "onopen 恢复线上态");
  assert.equal(harness.api.state.streamState, "open");
  assert.equal(harness.elementFor("#connBanner").hidden, true, "恢复后横幅消失");
  await flush();
  assert.equal(harness.sources.length, 2, "onopen 不得再开连接");
});

test("rotate 只是例行轮换：不算掉线，250ms 后重连那一条流", async () => {
  const harness = createHarness();
  harness.api.syncStreams();
  await flush();
  const first = harness.sources[0];
  first.onopen(); // 连接真的建立起来（真实浏览器里 onopen 先到）
  first.emit("rotate", {});
  assert.equal(harness.api.state.online, true, "例行轮换不得被说成故障");
  assert.equal(harness.api.state.streamState, "open");

  await flush();
  assert.equal(harness.sources.length, 2, "轮换后仍是一条流");
  assert.equal(first.closed, true);
  assert.ok(harness.timerDelays.includes(250), `例行轮换的重连节奇是 250ms：${harness.timerDelays}`);
});

test("EventSource 不存在（测试替身 / 极旧浏览器）时 syncStreams 不抛错", async () => {
  const harness = createHarness({ noEventSource: true });
  assert.doesNotThrow(() => harness.api.syncStreams());
  await flush();
  assert.equal(harness.sources.length, 0, "没有 EventSource 就不开流，也不炸");
});
