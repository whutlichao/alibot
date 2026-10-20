/**
 * 频道消息流里的**任务指针消息**（#198 / ADR-0028）的 Studio DOM 行为。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `narration_ui.test.cjs` /
 * `task_count_ui.test.cjs` 同款），不新开第二套前端测试框架。断言只落在用户可见面：
 *
 * 1. 频道 system 行里的 `.task-ref-chip` 文案含**任务线程消息数**（`task.message_count`），
 *    aria-label 含任务标题与条数；
 * 2. 点 chip 走既有 `openTask`（可观察证据：请求 `GET /api/tasks/<id>/messages`）；
 * 3. `#msgCount` 把这条 system 消息计入（0 → 1）；
 * 4. 建任务成功后吸收返回的 `message`：立即出现在流里、页签计数 +1（不需要等下一次刷新）；
 * 5. 任务不在快照里时计数如实显示 0，不编造。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身（与 `narration_ui.test.cjs` 同形；选择器支持 `[attr="value"]`） ── */

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

  focus() {}

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
    if (name.startsWith("data-")) return datasetValue(node, name) === value;
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

async function settle() {
  await flush();
  await flush();
  await flush();
}

function descendants(node) {
  return node.descendants();
}

function byClass(root, name) {
  return descendants(root).filter((node) => node.classList && node.classList.contains(name));
}

const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

/* ─── 后端载荷的形状（照 HTTP 面真字段） ─────────────────────────────────── */

const OWNER = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };

function taskRecord(overrides) {
  return Object.assign(
    {
      id: "t_1",
      target_id: "tgt_1",
      title: "整理发布清单",
      channel_id: "ch_1",
      channel_name: "设计系统重构",
      status: "todo",
      assignee_member_id: null,
      unread_count: 0,
      message_count: 3,
    },
    overrides || {},
  );
}

/** 一条频道 system 指针消息（`GET /api/channels/{id}/messages` 的项形状）。 */
function pointerMessage(overrides) {
  return Object.assign(
    {
      id: "msg_pointer",
      target_id: "ch_1",
      seq: 1,
      kind: "system",
      body: "任务已创建：整理发布清单",
      created_at: "2026-10-20T09:00:00Z",
      mention_snapshot: [],
      task_ref: null,
      task_pointer: "t_1",
      attachments: [],
      author: OWNER,
    },
    overrides || {},
  );
}

function jsonResponse(payload, status) {
  return { ok: (status || 200) < 400, status: status || 200, json: async () => payload };
}

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const elements = new Map();
  const tasks = (config.tasks || []).map((task) => Object.assign({}, task));
  const channelMessages = (config.messages || []).map((message) => Object.assign({}, message));

  class FakeEventSource {
    constructor(url) {
      this.url = url;
    }
    addEventListener() {}
    close() {}
  }

  function defaultFetch(url, request) {
    const method = request.method || "GET";
    const route = String(url).split("?")[0];
    const body = typeof request.body === "string" ? JSON.parse(request.body) : request.body || {};
    if (method === "POST" && route === "/api/channels/ch_1/tasks") {
      const created = taskRecord({
        id: "t_new",
        target_id: "tgt_new",
        title: String(body.title || ""),
        message_count: 1,
      });
      const pointer = pointerMessage({
        id: "msg_new",
        seq: channelMessages.length + 1,
        body: `任务已创建：${created.title}`,
        task_pointer: created.id,
      });
      tasks.push(created);
      channelMessages.push(pointer);
      return jsonResponse({ task: created, target: { id: created.target_id, kind: "task" }, message: pointer }, 201);
    }
    if (method === "GET" && route === "/api/tasks") {
      return jsonResponse({ tasks: tasks.slice() });
    }
    if (method === "GET" && route === "/api/channels/ch_1/messages") {
      return jsonResponse({ messages: channelMessages.slice() });
    }
    if (method === "GET" && /^\/api\/tasks\/[^/]+\/messages$/.test(route)) {
      const id = decodeURIComponent(route.split("/")[3]);
      const found = tasks.find((task) => task.id === id) || taskRecord({ id });
      return jsonResponse({ task: Object.assign({}, found), messages: [] });
    }
    if (method === "GET" && /^\/api\/channels\/[^/]+\/members$/.test(route)) {
      return jsonResponse({ members: [OWNER] });
    }
    if (method === "GET" && /^\/api\/channels\/[^/]+\/config$/.test(route)) {
      return jsonResponse({
        config: {
          channel_id: "ch_1",
          mode: "autonomous",
          team_scope: "channel+task",
          team_leader_member_id: null,
          leader_available: false,
          leader_reason: "",
          workspace_root: "/tmp/issue198-project",
          workspace_backend: "local",
          permission_mode: "ask-writes",
        },
      });
    }
    return jsonResponse({});
  }

  function fetchStub(url, init) {
    const request = init || {};
    calls.push({
      url: String(url),
      method: request.method || "GET",
      body: typeof request.body === "string" && request.body.length ? JSON.parse(request.body) : null,
    });
    return defaultFetch(String(url), request);
  }

  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = new FakeNode("div");
      node.id = selector.startsWith("#") ? selector.slice(1) : "";
      node.hidden = HIDDEN_IN_HTML.has(selector);
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

  let document = null;
  function newNode(tagName) {
    const node = new FakeNode(tagName);
    node.ownerDocument = document;
    return node;
  }
  document = {
    documentElement: newNode("html"),
    activeElement: null,
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

  const context = vm.createContext({
    document,
    fetch: fetchStub,
    EventSource: FakeEventSource,
    URLSearchParams,
    window: { addEventListener: () => {} },
    console,
    setTimeout: (fn) => setImmediate(fn),
    clearTimeout: () => {},
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
      "\nglobalThis.__pointerTest = { state, bindEvents, setViewMode, renderMessages, openTask };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__pointerTest;
  api.state.member = OWNER;
  api.state.members = [OWNER];
  api.state.agents = [];
  api.state.channels = [
    { id: "ch_1", name: "设计系统重构", description: "", last_seq: 0, last_read_seq: 0, unread_count: 0 },
  ];
  api.state.dms = [];
  api.state.activeChannelId = "ch_1";
  api.state.mode = "channel";
  api.state.tasks = tasks.map((task) => Object.assign({}, task));
  api.state.boardTasks = api.state.tasks.slice();
  api.state.messages = channelMessages.slice();
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  const messageList = elementFor("#messageList");
  return {
    api,
    calls,
    elementFor,
    messageList,
    show() {
      api.setViewMode("channel");
      api.renderMessages();
    },
    msgCount() {
      return elementFor("#msgCount").textContent;
    },
    chips() {
      return byClass(messageList, "task-ref-chip");
    },
    findRequest(method, url) {
      return calls.find((call) => call.method === method && call.url === url) || null;
    },
    async clickChip(chip) {
      assert.ok(chip, "要点击的 chip 应当存在");
      await messageList.dispatchEvent({ type: "click", target: chip });
      await settle();
    },
    async click(node) {
      assert.ok(node, "要点击的控件应当存在");
      await node.dispatchEvent({ type: "click", target: node });
      await settle();
    },
  };
}

/* ─── 1. 指针消息：chip 文案含线程条数，点它打开任务线程 ─────────────────── */

test("频道指针消息渲染出进入任务线程的 chip，文案带任务线程消息数，点击打开线程", async () => {
  const harness = createHarness({ tasks: [taskRecord()], messages: [pointerMessage()] });
  harness.show();

  assert.equal(harness.msgCount(), "1", "「消息」页签把这条 system 消息计入");
  const chips = harness.chips();
  assert.equal(chips.length, 1, "指针消息只有一个入口 chip（task_ref 为 null，不额外渲染）");
  const chip = chips[0];
  assert.equal(chip.tagName, "button");
  assert.match(chip.textContent, /进入任务线程 · 3 条/, "计数来自任务载荷的 message_count");
  assert.equal(chip.dataset.openTask, "t_1");
  assert.match(chip.getAttribute("aria-label"), /整理发布清单/);
  assert.match(chip.getAttribute("aria-label"), /3 条/);

  await harness.clickChip(chip);

  assert.ok(
    harness.findRequest("GET", "/api/tasks/t_1/messages"),
    "点 chip 应当走既有 openTask：请求任务线程",
  );
});

/* ─── 2. 建任务成功后立即吸收返回的指针消息 ──────────────────────────────── */

test("建任务成功后吸收返回的 message：流里立刻出现该行，页签计数 +1", async () => {
  const harness = createHarness({ tasks: [] });
  harness.show();
  assert.equal(harness.msgCount(), "0", "起始没有消息");

  await harness.click(harness.elementFor("#btnNewTask"));
  harness.elementFor("#taskCreateTitle").value = "写发布说明";
  harness.elementFor("#taskCreateChannel").value = "ch_1";
  await harness.click(harness.elementFor("#taskCreateSubmit"));

  assert.ok(
    harness.findRequest("POST", "/api/channels/ch_1/tasks"),
    "应当提交到建任务端点",
  );
  assert.equal(harness.msgCount(), "1", "返回的消息被吸收后，「消息」页签立刻 +1");
  assert.match(harness.messageList.textContent, /任务已创建：写发布说明/);
  const chips = harness.chips();
  assert.equal(chips.length, 1);
  assert.match(chips[0].textContent, /进入任务线程 · 1 条/);
  assert.equal(chips[0].dataset.openTask, "t_new");
});

/* ─── 3. 任务不在快照里：计数如实为 0 ───────────────────────────────────── */

test("任务不在快照里时 chip 的计数显示 0，不编造条数", () => {
  const harness = createHarness({
    tasks: [],
    messages: [pointerMessage({ task_pointer: "t_missing" })],
  });
  harness.show();

  const chips = harness.chips();
  assert.equal(chips.length, 1);
  assert.match(chips[0].textContent, /进入任务线程 · 0 条/);
});
