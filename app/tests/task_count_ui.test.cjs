/**
 * 频道 header 上「任务」页签的计数（#177）。
 *
 * 用户报的 bug：在频道视图里通过「新建任务」弹窗建成任务后，`#taskCount` 仍停在旧值，
 * 要等别的事件（切换视图、消息流重渲）才顺带刷新——因为「任务全量快照 `state.tasks` 变了」
 * 与「页签计数重算」本来是不连通的两条路。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `channel_resources_ui.test.cjs` 同款），
 * 不新开第二套前端测试框架。
 *
 * 断言只落在**用户可见的文本**上（`#taskCount` 的 textContent），不碰内部函数返回值：
 * 1. 从「新建任务」弹窗提交后，计数**立刻**等于新数量；
 * 2. 打开一条原先不在快照里的任务（搜索 / 卡片入口）后，计数同步增加；
 * 3. 看板的频道筛选态下计数仍按**未筛选的全量快照**算（不是筛选后的那几条）。
 *
 * #225 起，同一个装置也钉住「消息」页签（`#msgCount`）：它的计数派生自当前 target 的
 * `unread_count`（后端未读口径），不是消息流条数；已读推进后由同一次 `render()` 刷新。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身（与 `channel_resources_ui.test.cjs` 同形；只实现用到的那些方法） ── */
class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this.id = "";
    this.type = "";
    this.placeholder = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.parentNode = null;
    this.style = {};
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
    this._textContent = "";
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

  matches(selector) {
    return matchesSelector(this, selector);
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

  blur() {}
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

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function settle() {
  await flush();
  await flush();
  await flush();
}

function channel(id, name) {
  return { id, name, description: "", last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

/** 一条频道消息（`GET /api/channels/{id}/messages` 的项形状；只留断言用到的字段）。 */
function messageRecord(overrides) {
  return Object.assign(
    {
      id: "msg_1",
      target_id: "ch_1",
      seq: 1,
      kind: "human",
      body: "一条消息",
      created_at: "2026-10-20T09:00:00Z",
      mention_snapshot: [],
      task_ref: null,
      task_pointer: null,
      attachments: [],
      author: { id: "m_1", kind: "agent", display_name: "Ada", avatar_slot: 1 },
    },
    overrides || {},
  );
}

/** 一条私信（`GET /api/workspace` 快照里 `dms` 的项形状）。 */
function dmRecord(overrides) {
  return Object.assign(
    {
      id: "dm_1",
      kind: "dm",
      peer: { id: "m_1", kind: "agent", display_name: "Ada", avatar_slot: 1 },
      last_seq: 0,
      last_read_seq: 0,
      unread_count: 0,
    },
    overrides || {},
  );
}

/** 一条任务（`GET /api/tasks` 的行形状；只留这一族用例读到的字段）。 */
function taskRecord(overrides) {
  return Object.assign(
    {
      id: "t_1",
      title: "整理发布清单",
      channel_id: "ch_1",
      channel_name: "设计系统重构",
      status: "todo",
      assignee_member_id: null,
      unread_count: 0,
      last_seq: 0,
      target_id: "tgt_1",
    },
    overrides || {},
  );
}

function jsonResponse(payload, status) {
  return {
    ok: (status || 200) < 400,
    status: status || 200,
    json: async () => payload,
  };
}

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const elements = new Map();
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const agent = { id: "m_1", kind: "agent", display_name: "Ada", avatar_slot: 1, enabled: true };
  // 后端的任务表在替身里是**可变**的：`POST` 建出来的那一条，随后的 `GET /api/tasks` 就读得到
  // （与真实后端一致；否则「建成后计数变新数量」无从谈起）。
  const tasks = (config.tasks || []).map((task) => Object.assign({}, task));
  // #225：「消息」页签读当前 target 的 `unread_count`。替身同样保持可变——`POST /read`
  // 会把该 target 的未读归零并原样返回，前端随后由同一次 `render()` 把页签改成新读数。
  const channels = [channel("ch_1", "设计系统重构"), channel("ch_2", "发布工程")];
  if (Number.isInteger(config.channelUnread)) channels[0].unread_count = config.channelUnread;
  const dms = (config.dms || []).map((dm) => Object.assign({}, dm));
  const messages = (config.messages || []).map((message) => Object.assign({}, message));

  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = new Map();
      this.onerror = null;
    }
    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(listener);
    }
    close() {}
  }

  function defaultFetch(url, request) {
    const method = request.method || "GET";
    const [route, query] = String(url).split("?");
    if (method === "POST" && route === "/api/channels/ch_1/tasks") {
      const created = taskRecord({
        id: "t_new",
        title: String((request.body || {}).title || ""),
        channel_id: "ch_1",
      });
      tasks.push(created);
      return jsonResponse({ task: created }, 201);
    }
    if (method === "GET" && route === "/api/tasks") {
      if (query === undefined) return jsonResponse({ tasks: tasks.slice() });
      // 筛选态下的读数：只有该频道的那几条。
      const channelId = new URLSearchParams(query).get("channel_id");
      return jsonResponse({ tasks: tasks.filter((task) => task.channel_id === channelId) });
    }
    if (method === "GET" && /^\/api\/tasks\/[^/]+\/messages$/.test(route)) {
      const id = decodeURIComponent(route.split("/")[3]);
      // 打开的任务**不在**快照里：它的正文只由这条读数给出。
      const found = tasks.find((task) => task.id === id) || config.openTask;
      return jsonResponse({ task: Object.assign({}, found), messages: [] });
    }
    if (method === "GET" && route === "/api/channels/ch_1/members") {
      return jsonResponse({ members: [agent] });
    }
    if (method === "GET" && /^\/api\/(channels|dms)\/[^/]+\/messages$/.test(route)) {
      return jsonResponse({ messages: messages.slice() });
    }
    if (method === "POST" && /^\/api\/channels\/[^/]+\/read$/.test(route)) {
      const id = decodeURIComponent(route.split("/")[3]);
      const found = channels.find((item) => item.id === id);
      if (found) found.unread_count = 0;
      return jsonResponse({
        channel: Object.assign({}, found),
        channels: channels.map((item) => Object.assign({}, item)),
      });
    }
    if (method === "POST" && /^\/api\/dms\/[^/]+\/read$/.test(route)) {
      const id = decodeURIComponent(route.split("/")[3]);
      const found = dms.find((item) => item.id === id);
      if (found) found.unread_count = 0;
      return jsonResponse({
        dm: Object.assign({}, found),
        dms: dms.map((item) => Object.assign({}, item)),
      });
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
          workspace_root: "/tmp/issue177-project",
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
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

  const documentListeners = new Map();
  const document = {
    documentElement: new FakeNode("html"),
    activeElement: null,
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
      "\nglobalThis.__taskCountTest = { state, bindEvents, setViewMode, refreshTasks, openTask, renderTabs, renderMessages, markCurrentRead };"
        + "\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__taskCountTest;
  api.state.member = owner;
  api.state.members = [owner, agent];
  api.state.agents = [agent];
  api.state.channels = channels;
  api.state.dms = dms;
  api.state.activeChannelId = config.activeChannelId === undefined ? "ch_1" : config.activeChannelId;
  api.state.messages = messages.map((message) => Object.assign({}, message));
  api.state.tasks = tasks.map((task) => Object.assign({}, task));
  api.state.boardTasks = api.state.tasks.slice();
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  const harness = {
    api,
    calls,
    elementFor,
    taskCount() {
      return elementFor("#taskCount");
    },
    msgCount() {
      return elementFor("#msgCount").textContent;
    },
    /** 消息流 GET 的次数（用户可观察事实：已读推进不该重拉消息流）。 */
    messageGets() {
      return calls.filter(
        (call) => call.method === "GET" && /^\/api\/(channels|dms)\/[^/]+\/messages$/.test(call.url),
      ).length;
    },
    /** 进入频道视图——页签（含计数）在这里第一次渲染，与真实启动同一个入口。 */
    showChannel() {
      api.setViewMode("channel");
    },
    async click(node) {
      assert.ok(node, "要点击的控件应当存在");
      await node.dispatchEvent({ type: "click", target: node });
      await settle();
    },
    posts() {
      return calls.filter((call) => call.method === "POST");
    },
  };
  return harness;
}

/* ─── 1. 新建任务弹窗：提交后计数立刻变 ────────────────────────────── */

test("从「新建任务」弹窗建成任务后，「任务」页签计数立刻变成新数量", async () => {
  const harness = createHarness({ tasks: [taskRecord()] });
  harness.showChannel();
  assert.equal(harness.taskCount().textContent, "1", "打开频道时计数应当按当前快照渲染");

  await harness.click(harness.elementFor("#btnNewTask"));
  assert.equal(harness.elementFor("#taskOverlay").hidden, false, "「新建任务」应当打开弹窗");

  harness.elementFor("#taskCreateTitle").value = "写发布说明";
  harness.elementFor("#taskCreateChannel").value = "ch_1";
  await harness.click(harness.elementFor("#taskCreateSubmit"));

  const post = harness.posts().find((call) => call.url === "/api/channels/ch_1/tasks");
  assert.ok(post, "应当提交到 POST /api/channels/ch_1/tasks");
  assert.equal(post.body.title, "写发布说明");
  assert.equal(harness.elementFor("#taskOverlay").hidden, true, "成功后弹窗应当关闭");
  assert.equal(harness.taskCount().textContent, "2", "任务快照对齐后，页签计数要立刻跟着变");
});

/* ─── 2. 打开不在快照里的任务：计数同步增加 ─────────────────────────── */

test("打开一条原先不在快照里的任务后，「任务」页签计数同步增加", async () => {
  const harness = createHarness({
    tasks: [taskRecord()],
    openTask: taskRecord({ id: "t_9", title: "从搜索打开的任务" }),
  });
  harness.showChannel();
  assert.equal(harness.taskCount().textContent, "1", "起始计数只有快照里那一条");

  await harness.api.openTask("t_9");
  await settle();

  assert.equal(harness.taskCount().textContent, "2", "补进快照后页签计数要跟着变，不能只重渲线程");
});

/* ─── 3. 筛选态下的计数仍按未筛选的全量快照算 ───────────────────────── */

test("看板筛到别的频道时，页签计数仍按未筛选的全量快照算", async () => {
  const harness = createHarness({
    tasks: [taskRecord(), taskRecord({ id: "t_2", channel_id: "ch_2", channel_name: "发布工程" })],
  });
  harness.showChannel();
  assert.equal(harness.taskCount().textContent, "1", "当前频道 ch_1 只有一条任务");

  harness.api.state.taskFilterChannelId = "ch_2";
  await harness.api.refreshTasks();
  await settle();

  // 筛选是**看板的呈现状态**，计数问的是「这个频道里有几条任务」，两者不是同一个数字。
  assert.equal(harness.taskCount().textContent, "1", "筛选不得让页签计数变成筛选后的条数");
});

/* ─── 4. #225「消息」页签：计数是当前 target 的未读数，不是消息流条数 ────── */

test("频道未读 3、消息流只有 1 条时，「消息」页签显示 3", () => {
  const harness = createHarness({ channelUnread: 3, messages: [messageRecord()] });
  harness.showChannel();

  // 两个数字故意不同：读数来自后端未读口径（`unread_count`），不是消息流总条数（1）。
  assert.equal(harness.msgCount(), "3", "页签读的是频道未读数（3），不是消息流条数（1）");
});

test("已读推进后「消息」页签立即变为 0，且不新增消息流 GET 请求", async () => {
  const harness = createHarness({
    channelUnread: 3,
    messages: [messageRecord({ id: "msg_2", seq: 2 })],
  });
  harness.showChannel();
  assert.equal(harness.msgCount(), "3", "起始按后端未读数渲染");
  const messageGetsBefore = harness.messageGets();

  // 与真实入口一致：进入频道后走 `markCurrentRead()` → `/read` → `render()`。
  harness.api.markCurrentRead();
  await settle();

  assert.ok(
    harness.posts().some((call) => call.url === "/api/channels/ch_1/read"),
    "已读推进应当走既有 /read 端点",
  );
  assert.equal(harness.msgCount(), "0", "/read 把未读归零后，页签由同一次 render 改成 0");
  assert.equal(
    harness.messageGets(),
    messageGetsBefore,
    "已读推进不得重新拉取消息流",
  );
});

test("私信 target 的「消息」页签同样按 unread_count 显示", () => {
  const harness = createHarness({
    dms: [dmRecord({ unread_count: 4 })],
    activeChannelId: "dm_1",
  });
  harness.showChannel();

  assert.equal(harness.msgCount(), "4", "私信页签同样读 target 的 unread_count");
});

test("没有当前 target 时「消息」页签显示 0", () => {
  const harness = createHarness({ channelUnread: 3 });
  harness.api.state.activeChannelId = null;
  harness.showChannel();

  assert.equal(harness.msgCount(), "0", "没有 target 时按 0 写，与「任务」页签同一形态");
});
