/**
 * #153 / #209 共用的 Studio 审批 DOM 测试装置。
 *
 * 这是一个**测试辅助文件**（不是 `*.test.cjs`，不会被 node glob 当用例执行）：
 * `vm` + `fetch` 替身 + DOM 替身与既有用例族同形，不新开框架、不新开 seam。
 * 两个用例文件各自只放断言：
 *   - `approval_ui.test.cjs`      审批卡本身（#153）
 *   - `approval_badge_ui.test.cjs` 线程关着时的角标 / 页签圆点（#209）
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const NOW = Date.parse("2026-10-18T12:00:00.000Z");

/* ─── DOM 替身（与既有用例族同形） ─────────────────────────────────────────── */

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
    this.id = "";
    this.title = "";
    this.type = "";
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

  get lastElementChild() {
    return this.children.length ? this.children[this.children.length - 1] : null;
  }

  appendChild(child) {
    this._textContent = "";
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parentNode = null;
    return child;
  }

  replaceChildren(...children) {
    this._textContent = "";
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  async dispatchEvent(event) {
    const listeners = this.listeners.get(event.type) || [];
    for (const listener of listeners) await listener.call(this, event);
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

  focus() {}
}

function matchesSelector(node, selector) {
  const attribute = /^\[([a-zA-Z0-9_-]+)\]$/.exec(selector);
  if (attribute) {
    const name = attribute[1];
    if (Object.prototype.hasOwnProperty.call(node.attributes, name)) return true;
    if (name.startsWith("data-")) {
      const key = name
        .slice("data-".length)
        .replace(/-([a-z])/g, (_whole, letter) => letter.toUpperCase());
      return node.dataset[key] !== undefined;
    }
    return false;
  }
  if (selector.startsWith(".")) return node.classList.contains(selector.slice(1));
  return node.tagName === selector;
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function byClass(root, className) {
  return descendants(root).filter((node) => node.classList.contains(className));
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

/* ─── 载荷 ─────────────────────────────────────────────────────────────────── */

function channel(id) {
  return { id: id || "ch-1", name: "design", last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function task(overrides) {
  return Object.assign(
    {
      id: "t-1",
      channel_id: "ch-1",
      channel_name: "design",
      target_id: "tg-1",
      title: "修一处样式",
      status: "todo",
      assignee_member_id: null,
      unread_count: 0,
      last_seq: 0,
    },
    overrides || {},
  );
}

function dm(id) {
  return {
    id: id || "dm-1",
    kind: "dm",
    name: "bob",
    peer: { id: "mem-2", display_name: "Bob", avatar_slot: 2 },
    last_seq: 0,
    last_read_seq: 0,
    unread_count: 0,
  };
}

function approval(overrides) {
  return Object.assign(
    {
      reply_id: "reply-1",
      target_id: "ch-1",
      member_id: "mem-1",
      team_run_id: null,
      tool_calls: [
        { id: "call-1", name: "Write", input: { path: "notes.txt", content: "hello" } },
        { id: "call-2", name: "Bash", input: { command: "npm test" } },
      ],
      state: "pending",
      decisions: [],
      requested_at: new Date(NOW - 60_000).toISOString(),
      resolved_at: null,
    },
    overrides || {},
  );
}

function json(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

function frame(payload) {
  return { data: JSON.stringify(payload) };
}

/* ─── 装置 ─────────────────────────────────────────────────────────────────── */

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const elements = new Map();
  const clock = { now: config.now == null ? NOW : config.now };
  const member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const ada = { id: "mem-1", kind: "agent", display_name: "Ada", avatar_slot: 1 };

  class FakeDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(clock.now);
      else super(...args);
    }

    static now() {
      return clock.now;
    }
  }

  class FakeEventSource {
    constructor(url) {
      this.url = String(url);
      this.listeners = new Map();
      this.closed = false;
    }

    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(listener);
    }

    close() {
      this.closed = true;
    }
  }

  function fetchStub(url, init) {
    const request = init || {};
    const call = {
      url: String(url),
      method: request.method || "GET",
      body: request.body ? JSON.parse(request.body) : null,
    };
    calls.push(call);
    if (config.fetch) return config.fetch(String(url), request);
    return json({});
  }

  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = new FakeNode("div");
      node.id = selector.startsWith("#") ? selector.slice(1) : "";
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

  const documentListeners = new Map();
  const document = {
    documentElement: new FakeNode("html"),
    activeElement: null,
    hidden: false,
    querySelector: (selector) => elementFor(selector),
    querySelectorAll: () => [],
    createElement: (tagName) => new FakeNode(tagName),
    createTextNode: (text) => {
      const node = new FakeNode("#text");
      node.textContent = text;
      return node;
    },
    addEventListener(type, listener) {
      if (!documentListeners.has(type)) documentListeners.set(type, []);
      documentListeners.get(type).push(listener);
    },
    async dispatchEvent(event) {
      for (const listener of documentListeners.get(event.type) || []) {
        await listener.call(document, event);
      }
    },
  };

  const intervalFns = [];
  const context = vm.createContext({
    document,
    fetch: fetchStub,
    EventSource: FakeEventSource,
    URLSearchParams,
    Date: FakeDate,
    window: { addEventListener: () => {} },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    console,
    setTimeout: (fn) => setImmediate(fn),
    clearTimeout: () => {},
    setInterval: (fn, ms) => {
      // `#209`：周期刷新与审批倒计时都走同一个替身；保留间隔，用例据此挑出 10–15s 的那一条。
      intervalFns.push({ fn, ms });
      return intervalFns.length;
    },
    clearInterval: () => {},
  });
  vm.runInContext(fs.readFileSync(path.join(STUDIO_DIR, "i18n.js"), "utf8"), context, {
    filename: "i18n.js",
  });
  const appPath = path.join(STUDIO_DIR, "app.js");
  const source = fs.readFileSync(appPath, "utf8");
  const startup = /\napplyStaticI18n\(document\);\nbindEvents\(\);\nloadWorkspace\(\);\s*$/;
  assert.match(source, startup, "app startup calls should remain at the end of app.js");
  vm.runInContext(
    source.replace(
      startup,
      "\nglobalThis.__approvalTest = { state, bindEvents, render, renderApprovalSurfaces,"
        + " loadWorkspace, loadApprovalsFor, renderTaskBoard, renderTabs,"
        + " handleStreamEvent, handleTaskStreamEvent, submitApproval,"
        + " renderTaskThread, openTask, approvalDisplayState, approvalRemainingSeconds,"
        + " approvalRecordsFor, t, dateNow: () => Date.now() };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__approvalTest;
  api.state.member = member;
  api.state.members = [member, ada];
  api.state.agents = [ada];
  api.state.channels = [channel()];
  api.state.dms = [];
  api.state.tasks = [];
  api.state.activeChannelId = "ch-1";
  api.state.mode = "channel";
  api.state.online = true;
  api.state.ready = true;
  api.state.streamState = "open";
  api.bindEvents();

  return {
    api,
    calls,
    clock,
    document,
    intervalFns,
    board: elementFor("#approvalBoard"),
    taskSection: elementFor("#ttApprovalSection"),
    taskList: elementFor("#ttApprovals"),
    columns: elementFor("#taskColumns"),
    rows: elementFor("#taskListWrap"),
    taskTab: elementFor("#tabTasks"),
    taskDot: elementFor("#taskApprovalDot"),
    callsTo(fragment) {
      return calls.filter((call) => call.url.includes(fragment));
    },
    startTask(overrides) {
      const record = task(overrides);
      api.state.activeTaskId = record.id;
      api.state.activeTask = record;
      api.state.tasks = [record];
      api.state.boardTasks = [record];
      api.state.taskMessages = [];
    },
  };
}

function actionButtons(root) {
  return descendants(root).filter(
    (node) => node.dataset && node.dataset.approvalAction !== undefined,
  );
}

function cardIn(root, replyId) {
  return byClass(root, "approval-card").find((card) => card.dataset.approvalId === replyId) || null;
}

/** 某次渲染里带 `data-od-id` 的节点（#209 的角标 / 圆点按它断言）。 */
function odId(root, id) {
  return descendants(root).find((node) => node.getAttribute("data-od-id") === id) || null;
}

/** #209 的有界周期刷新：从 bindEvents 注册的定时器里挑出 10–15s 的那一条并推进一轮。 */
function periodicTick(harness) {
  const entry = harness.intervalFns.find((item) => item.ms >= 10_000 && item.ms <= 15_000);
  assert.ok(entry, "应用启动后应注册一个 10–15s 的全量待审批刷新定时器");
  entry.fn();
}


module.exports = {
  NOW,
  flush,
  channel,
  task,
  dm,
  approval,
  json,
  frame,
  createHarness,
  actionButtons,
  cardIn,
  odId,
  periodicTick,
  byClass,
  descendants,
};
