/**
 * 频道消息流里的**任务指针消息**（#198 / ADR-0028）的 Studio DOM 行为。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `narration_ui.test.cjs` /
 * `task_count_ui.test.cjs` 同款），不新开第二套前端测试框架。断言只落在用户可见面：
 *
 * 1. 频道 system 行里的 `.task-ref-chip` 文案含**任务未读数**（`task.unread_count`，不是
 *    `task.message_count`）：0 未读时不显示计数段，>0 时是「· N 条未读」；aria-label 同步；
 * 2. 点 chip 走既有 `openTask`（可观察证据：请求 `GET /api/tasks/<id>/messages`）；
 * 3. `#msgCount` 把这条 system 消息计入（0 → 1）；
 * 4. 建任务成功后吸收返回的 `message`：立即出现在流里、页签计数 +1（不需要等下一次刷新）；
 * 5. 任务不在快照里时按未读 0 处理，不显示计数、不编造读数；
 * 6. #223：打开线程完成已读（`openTask` → `markTaskRead`）与线程内新消息被吸收
 *    （`absorbTaskMessage` → `markTaskRead`）之后，频道流里同一任务的 chip 立即变为未读 0，
 *    且是**同一个节点就地改写**——不整板重铺、不丢滚动位置、不重新拉频道消息流；
 * 7. `#217`：该任务 target 有 pending 时，同一行出现与任务卡片同款的「待审批 N」角标，
 *    文本 / aria 复用既有词条、count 严格按 target 归属；无 pending / 别的 target 的 pending
 *    都不画；live `approval_resolved` 与本地 600s 过期都会让它消失，且**不**重新拉消息；
 *    角标本身不是点击目标（可点区域仍只有既有 chip）。
 *
 * 装置分两份、各有唯一来源：前五条（#198 / #223）用文件上半部分的窄装置（只铺消息流 + 任务快照）；
 * #217 的判据需要**审批时钟 / 定时器 / live 帧**，这些能力已经在 `approval_dom_harness.cjs`
 * （#153 / #209 的共用装置）里，因此第 7 组用例复用那一份，不在本地再补第二套。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// #217 的那组用例复用 #153 / #209 的共享装置（见文件头：时钟 / 定时器 / live 帧只有那一份）。
const approvalHarness = require("./approval_dom_harness.cjs");

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
      // #223：chip 的计数读 `unread_count`；`message_count` 故意取不同值，用例据此证明读的是未读。
      unread_count: 2,
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

/** 一条任务线程消息（`GET /api/tasks/{id}/messages` 的项形状）。 */
function threadMessage(overrides) {
  return Object.assign(
    {
      id: "msg_thread_1",
      target_id: "tgt_1",
      seq: 1,
      kind: "human",
      body: "线程里的一条消息",
      created_at: "2026-10-20T09:05:00Z",
      mention_snapshot: [],
      task_ref: null,
      task_pointer: null,
      attachments: [],
      author: { id: "mem_other", kind: "member", display_name: "别的成员", avatar_slot: 1 },
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
  const threadMessages = (config.threadMessages || []).map((message) => Object.assign({}, message));

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
        // 自己建的任务：线程里还没有别人的发言，未读为 0（system 指针与自己的消息都不计）。
        unread_count: 0,
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
      return jsonResponse({
        task: Object.assign({}, found),
        messages: threadMessages.map((message) => Object.assign({}, message)),
      });
    }
    // 已读端点（#223）：后端把任务载荷的未读数归零后原样返回任务。
    if (method === "POST" && /^\/api\/tasks\/[^/]+\/read$/.test(route)) {
      const id = decodeURIComponent(route.split("/")[3]);
      const found = tasks.find((task) => task.id === id);
      if (found) found.unread_count = 0;
      return jsonResponse({ task: Object.assign({}, found || taskRecord({ id, unread_count: 0 })) });
    }
    if (method === "GET" && /^\/api\/tasks\/[^/]+$/.test(route)) {
      const id = decodeURIComponent(route.split("/")[3]);
      const found = tasks.find((task) => task.id === id) || taskRecord({ id });
      return jsonResponse({ task: Object.assign({}, found) });
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
      "\nglobalThis.__pointerTest = { state, bindEvents, setViewMode, renderMessages, openTask, handleTaskStreamEvent };\n",
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

/* ─── 1. 指针消息：chip 文案含未读数，点它打开任务线程 ─────────────────────── */

test("频道指针消息渲染出进入任务线程的 chip，文案带任务未读数，点击打开线程", async () => {
  const harness = createHarness({ tasks: [taskRecord()], messages: [pointerMessage()] });
  harness.show();

  assert.equal(harness.msgCount(), "1", "「消息」页签把这条 system 消息计入");
  const chips = harness.chips();
  assert.equal(chips.length, 1, "指针消息只有一个入口 chip（task_ref 为 null，不额外渲染）");
  const chip = chips[0];
  assert.equal(chip.tagName, "button");
  assert.equal(
    chip.textContent,
    "进入任务线程 · 2 条未读",
    "计数来自任务载荷的 unread_count（2），不是 message_count（3）",
  );
  assert.equal(chip.dataset.openTask, "t_1");
  assert.equal(chip.getAttribute("aria-label"), "打开任务 整理发布清单 的任务线程，有 2 条未读");

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
  assert.equal(chips[0].textContent, "进入任务线程", "新建任务未读为 0：不显示数字与「条」");
  assert.equal(chips[0].getAttribute("aria-label"), "打开任务 写发布说明 的任务线程");
  assert.equal(chips[0].dataset.openTask, "t_new");
});

/* ─── 3. 任务不在快照里：按未读 0 处理，不编造读数 ───────────────────────── */

test("任务不在快照里时 chip 不显示计数段，也不编造条数", () => {
  const harness = createHarness({
    tasks: [],
    messages: [pointerMessage({ task_pointer: "t_missing" })],
  });
  harness.show();

  const chips = harness.chips();
  assert.equal(chips.length, 1);
  assert.equal(chips[0].textContent, "进入任务线程");
  assert.doesNotMatch(chips[0].textContent, /0/);
  assert.equal(chips[0].getAttribute("aria-label"), "打开任务 t_missing 的任务线程");
});

/* ─── 3b. #223 已读后的定点刷新：不重铺、不丢滚动、不重拉频道消息流 ───────── */

/** 频道消息流被整板重拉的次数（用户可观察事实：打开线程不应触发它）。 */
function channelMessageFetches(harness) {
  return harness.calls.filter((call) => call.url.startsWith("/api/channels/ch_1/messages")).length;
}

test("打开任务线程完成已读后，频道流里同一任务的 chip 立即变为未读 0，且不重铺消息流", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ unread_count: 4, message_count: 9 })],
    messages: [pointerMessage()],
    threadMessages: [threadMessage({ seq: 1 })],
  });
  harness.show();

  const chip = harness.chips()[0];
  assert.equal(chip.textContent, "进入任务线程 · 4 条未读");
  const rowBefore = byClass(harness.messageList, "msg")[0];
  assert.ok(rowBefore, "指针消息行应当已铺好");
  const fetchesBefore = channelMessageFetches(harness);
  harness.messageList.scrollTop = 37;

  await harness.clickChip(chip);

  assert.ok(harness.findRequest("POST", "/api/tasks/t_1/read"), "打开线程后走既有已读端点");
  const chips = harness.chips();
  assert.equal(chips.length, 1);
  assert.equal(chips[0], chip, "同一个 chip 节点被就地改写（不整板重铺）");
  assert.equal(byClass(harness.messageList, "msg")[0], rowBefore, "消息行节点没有被重建");
  assert.equal(harness.messageList.scrollTop, 37, "定点更新不改变消息流滚动位置");
  assert.equal(chip.textContent, "进入任务线程", "已读后 chip 不再显示计数段");
  assert.equal(chip.getAttribute("aria-label"), "打开任务 整理发布清单 的任务线程");
  assert.equal(
    channelMessageFetches(harness),
    fetchesBefore,
    "打开线程期间没有重新拉取频道消息流",
  );
});

test("线程内新消息被吸收后，同一任务的 chip 也立即回到未读 0（不重拉频道消息流）", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ unread_count: 1 })],
    messages: [pointerMessage()],
    threadMessages: [threadMessage({ seq: 1 })],
  });
  harness.show();
  await harness.clickChip(harness.chips()[0]);

  // 频道流先看到该任务又出现未读（模拟 12s 轮询带回了新的任务载荷），随后线程吸收一条新消息。
  harness.api.state.tasks[0].unread_count = 1;
  harness.api.renderMessages();
  const chip = harness.chips()[0];
  assert.equal(chip.textContent, "进入任务线程 · 1 条未读");
  const fetchesBefore = channelMessageFetches(harness);

  harness.api.handleTaskStreamEvent(
    "t_1",
    { data: JSON.stringify({ message: threadMessage({ id: "msg_thread_2", seq: 2 }) }) },
    "message",
  );
  await settle();

  assert.ok(harness.findRequest("POST", "/api/tasks/t_1/read"), "吸收线程消息后同样走已读端点");
  assert.equal(harness.chips()[0], chip, "同一个 chip 节点被就地改写");
  assert.equal(chip.textContent, "进入任务线程", "吸收并已读后 chip 回到未读 0");
  assert.equal(
    channelMessageFetches(harness),
    fetchesBefore,
    "吸收线程消息不重新拉取频道消息流",
  );
});

/* ─── 4. #217 指针消息行上的「待审批 N」角标 ───────────────────────────────
   这一组复用 `approval_dom_harness.cjs`（假时钟 / 定时器 / live 帧的唯一一份装置）。
   断言只落在用户可见面：角标文本与 aria 名、它出现与消失的时机、它不承载点击，
   以及「不重新请求 `/api/messages`」这一条外部可观察事实。 */

// 只取这一组用例需要的部分并**改别名**：`flush` / `byClass` / `createHarness` 在本文件上半部分
// 已有同形的窄装置，同名声明会撞。
const {
  NOW,
  createHarness: createApprovalHarness,
  odId,
  flush: approvalFlush,
  byClass: approvalByClass,
} = approvalHarness;

/** 一条频道指针消息（#198 的 `task_pointer` 行），形状照 HTTP 面。 */
function channelPointer(overrides) {
  return Object.assign(
    {
      id: "msg-pointer",
      target_id: "ch-1",
      seq: 1,
      kind: "system",
      body: "任务已创建：修一处样式",
      created_at: new Date(NOW - 120_000).toISOString(),
      mention_snapshot: [],
      task_ref: null,
      task_pointer: "t-1",
      attachments: [],
      author: { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 },
    },
    overrides || {},
  );
}

/** 指针消息行里的角标：只有 `.card-approval`（与任务卡片同一个类名，不是第二套视觉）。 */
function pointerBadges(harness) {
  return approvalByClass(harness.messageList, "card-approval");
}

/** 让 `#messageList` 里出现这个消息流（`setViewMode('channel')` 之外不额外重铺）。 */
function showMessages(harness) {
  harness.api.renderMessages();
}

test("#217 指针消息行的角标：有 pending 时出现，文本与 aria 名复用任务卡片同一对词条", async () => {
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return approvalHarness.json({
          approvals: [
            approvalHarness.approval({ reply_id: "r-1", target_id: "tg-1" }),
            approvalHarness.approval({ reply_id: "r-2", target_id: "tg-1" }),
          ],
        });
      }
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task()];
  harness.api.state.boardTasks = [approvalHarness.task()];
  showMessages(harness);
  assert.equal(pointerBadges(harness).length, 0, "读数还没到时不画角标（不编造 0 以外的数）");

  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  showMessages(harness);

  const badges = pointerBadges(harness);
  assert.equal(badges.length, 1, "该任务 target 有 pending ⇒ 指针消息行出现角标");
  const badge = badges[0];
  assert.equal(badge.textContent, harness.api.t("board.approvalBadge", { count: 2 }));
  assert.match(badge.textContent, /待审批 2/);
  assert.equal(
    badge.getAttribute("aria-label"),
    harness.api.t("board.approvalBadgeAria", { title: "修一处样式", count: 2 }),
  );
  assert.match(badge.getAttribute("aria-label"), /修一处样式/);
  // 整行的可点区域仍是那一个既有 chip：角标既不挂 `data-open-task`，也不是按钮 / 可聚焦。
  const chips = approvalByClass(harness.messageList, "task-ref-chip");
  assert.equal(chips.length, 1, "入口仍是唯一的那个「进入任务线程」chip");
  assert.equal(badge.getAttribute("data-open-task"), null);
  assert.notEqual(badge.tagName, "button");
  assert.equal(badge.getAttribute("role"), null);
  assert.equal(badge.getAttribute("tabindex"), null);
});

test("#217 没有 pending 时指针消息行完全不渲染角标", async () => {
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") return approvalHarness.json({ approvals: [] });
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task()];
  harness.api.state.boardTasks = [approvalHarness.task()];
  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  showMessages(harness);

  assert.equal(pointerBadges(harness).length, 0, "没有 pending 时连空元素都不渲染");
  assert.equal(approvalByClass(harness.messageList, "task-ref-chip").length, 1, "入口 chip 仍在");
});

test("#217 归属严格：频道 target 与别的任务的 pending 都不让这一行出现角标", async () => {
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return approvalHarness.json({
          approvals: [
            // 频道 target（ch-1）自己的 pending：只属于 #approvalBoard。
            approvalHarness.approval({ reply_id: "r-ch", target_id: "ch-1" }),
            // 另一个任务的 pending（tg-2）：不属于这条指针消息指向的任务。
            approvalHarness.approval({ reply_id: "r-other", target_id: "tg-2" }),
          ],
        });
      }
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task(), approvalHarness.task({ id: "t-2", target_id: "tg-2" })];
  harness.api.state.boardTasks = harness.api.state.tasks.slice();
  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  showMessages(harness);

  assert.equal(pointerBadges(harness).length, 0, "指针消息行只认 pointedTask.target_id");
  // 反向对照：别的任务自己的卡片角标照常出现（读数确实到了，只是没串台）。
  harness.api.renderTaskBoard();
  assert.ok(odId(harness.columns, "task-approval-badge-t-2"), "另一个任务的卡片角标照常出现");
});

test("#217 live approval_required / approval_resolved 让角标出现与消失，且没有新的 /api/messages 请求", async () => {
  const harness = createApprovalHarness({ messages: [channelPointer()] });
  // live 帧按「审批记录的 target」归属（`record.target_id`）——任务线程开着时由任务流推导；
  // 这条路径与 #209 的第 12 条用例同一个入口，因此这里同样先把线程打开。
  harness.startTask();
  showMessages(harness);
  assert.equal(pointerBadges(harness).length, 0, "起始没有 pending：这一行不画角标");
  const messageRequestsBefore = harness.callsTo("/messages").length;

  harness.api.handleTaskStreamEvent(
    "t-1",
    approvalHarness.frame({
      type: "approval_required",
      reply_id: "r-live",
      member_id: "mem-1",
      tool_calls: [{ id: "call-live", name: "Bash", input: { command: "npm test" } }],
    }),
    "approval_required",
  );
  await approvalFlush();
  const badges = pointerBadges(harness);
  assert.equal(badges.length, 1, "live approval_required 到达后角标出现（不重新拉消息）");
  assert.equal(badges[0].textContent, harness.api.t("board.approvalBadge", { count: 1 }));

  harness.api.handleTaskStreamEvent(
    "t-1",
    approvalHarness.frame({ type: "approval_resolved", reply_id: "r-live", state: "approved", decisions: [] }),
    "approval_resolved",
  );
  await approvalFlush();

  assert.equal(pointerBadges(harness).length, 0, "resolved 之后角标消失（不需要重新拉消息）");
  assert.equal(
    harness.callsTo("/messages").length,
    messageRequestsBefore,
    "角标出现 / 消失都不得触发任何 /messages 请求",
  );
  // 既有 chip 一字不动：整行的可点区域没有因为角标出现 / 消失而改变。
  assert.equal(approvalByClass(harness.messageList, "task-ref-chip").length, 1);
});

test("#217 越过 requested_at + 600s 后，下一次秒级 tick 让角标消失", async () => {
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return approvalHarness.json({
          approvals: [
            approvalHarness.approval({
              reply_id: "r-1",
              target_id: "tg-1",
              requested_at: new Date(NOW).toISOString(),
            }),
          ],
        });
      }
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task()];
  harness.api.state.boardTasks = [approvalHarness.task()];
  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  showMessages(harness);
  assert.equal(pointerBadges(harness).length, 1);

  harness.clock.now = NOW + 600_000;
  const countdown = harness.intervalFns.find((item) => item.ms === 1000);
  assert.ok(countdown, "pending 存在时应注册 1s 倒计时");
  countdown.fn();
  await approvalFlush();

  assert.equal(pointerBadges(harness).length, 0, "本地上界过期后角标在同一个出口消失");
});

test("#217 重铺消息流保留 composer 草稿与 pending 消息（沿用既有 renderMessages，无新增保存/恢复）", async () => {
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return approvalHarness.json({
          approvals: [approvalHarness.approval({ reply_id: "r-1", target_id: "tg-1" })],
        });
      }
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task()];
  harness.api.state.boardTasks = [approvalHarness.task()];
  showMessages(harness);
  assert.equal(pointerBadges(harness).length, 0, "读数还没到");

  // 重铺**之前**先摆好这两样：composer 里的草稿与 `state.pending` 里那条还没发出去的消息。
  // 它们都不由消息流节点持有，因此重铺本来就该原样留着——这条用例钉的是「不新增保存/恢复」
  // 这个承诺本身（如果有人开始在 renderMessages 里重建 composer，它会红）。
  const composer = harness.elementFor("#composerInput");
  composer.value = "写了一半的回复";
  harness.api.state.pending = [
    {
      clientId: "pending-217",
      target_id: "ch-1",
      body: "还没发出去的草稿消息",
      status: "pending",
      created_at: new Date(NOW).toISOString(),
      author: { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 },
      attachments: [],
    },
  ];
  showMessages(harness);
  assert.ok(
    approvalByClass(harness.messageList, "msg").some((node) => node.dataset.pending === "pending-217"),
    "重铺前 pending 消息已在流里",
  );

  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();

  assert.equal(pointerBadges(harness).length, 1, "派生集合变化 ⇒ 角标出现（消息流被重铺）");
  assert.equal(composer.value, "写了一半的回复", "重铺不动 composer 里的草稿");
  assert.ok(
    approvalByClass(harness.messageList, "msg").some((node) => node.dataset.pending === "pending-217"),
    "重铺后 pending 消息仍在流里",
  );
  assert.equal(
    approvalByClass(harness.messageList, "task-ref-chip").length,
    1,
    "指针消息行仍是唯一那一条",
  );
});

test("#217 指针消息行仍走既有 chip：点 chip 打开任务线程，点角标本身不触发任何打开", async () => {
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return approvalHarness.json({
          approvals: [approvalHarness.approval({ reply_id: "r-1", target_id: "tg-1" })],
        });
      }
      if (/^\/api\/tasks\/t-1\/messages$/.test(url)) {
        return approvalHarness.json({ task: approvalHarness.task(), messages: [] });
      }
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task()];
  harness.api.state.boardTasks = [approvalHarness.task()];
  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  showMessages(harness);
  assert.equal(pointerBadges(harness).length, 1);

  // 角标本身**不是**第二个点击目标：从它出发的事件在 `#messageList` 的委托里
  // `closest('[data-open-task]')` 命中不到任何东西，因此不请求任务线程。
  const badge = pointerBadges(harness)[0];
  assert.ok(badge, "角标应在");
  await harness.messageList.dispatchEvent({ type: "click", target: badge });
  await approvalFlush();
  assert.equal(
    harness.callsTo("/api/tasks/t-1/messages").length,
    0,
    "点角标本身不打开任何东西（可点区域仍只有既有 chip）",
  );

  const chip = approvalByClass(harness.messageList, "task-ref-chip")[0];
  assert.ok(chip, "既有 chip 仍在");
  await harness.messageList.dispatchEvent({ type: "click", target: chip });
  await approvalFlush();
  assert.ok(
    harness.callsTo("/api/tasks/t-1/messages").length >= 1,
    "点既有 chip 仍走 openTask（整行可点区域逐字不变）",
  );
});

test("#217 任务只在看板快照里（不在 state.tasks）时，live 帧仍让指针消息行的角标出现", async () => {
  const harness = createApprovalHarness({ messages: [channelPointer()] });
  // 筛选态下 `state.tasks` 与 `state.boardTasks` 不同源（`refreshTasks()` 两次查询）：
  // 任务是 `taskById()` 读得到、`state.tasks` 里却没有的形态。签名必须覆盖这两种读域，
  // 否则消息流不重铺、角标出不来（#217 review 抓到的漏洞）。
  harness.api.state.tasks = [];
  harness.api.state.boardTasks = [approvalHarness.task()];
  showMessages(harness);
  assert.equal(pointerBadges(harness).length, 0, "读数还没到");

  // 帧自带 `target_id`（任务线程关着时 live 帧的真实来源），因此不需要 `activeTask` 兜底。
  harness.api.handleTaskStreamEvent(
    "t-1",
    approvalHarness.frame({
      type: "approval_required",
      target_id: "tg-1",
      reply_id: "r-board-only",
      member_id: "mem-1",
      tool_calls: [{ id: "call-board", name: "Write", input: { path: "board.txt" } }],
    }),
    "approval_required",
  );
  await approvalFlush();

  const badges = pointerBadges(harness);
  assert.equal(badges.length, 1, "任务只在看板快照里时，指针消息行也要跟着重铺");
  assert.equal(
    badges[0].getAttribute("data-od-id"),
    "message-approval-badge-msg-pointer",
    "消息行的角标有自己可定位的 data-od-id（与卡片的 task- 前缀分开）",
  );
});

test("#217 一次签名变化只把频道审批板铺一次（消息流的重铺不重复铺它）", async () => {
  // 两次读数：第一轮只有 tg-1 的 pending，第二轮多出 tg-9 的 —— 签名因此真的变化两次。
  let snapshot = 0;
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        snapshot += 1;
        const approvals = [approvalHarness.approval({ reply_id: "r-ch", target_id: "ch-1" })];
        approvals.push(approvalHarness.approval({ reply_id: "r-t", target_id: "tg-1" }));
        if (snapshot > 1) approvals.push(approvalHarness.approval({ reply_id: "r-t9", target_id: "tg-9" }));
        return approvalHarness.json({ approvals });
      }
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task(), approvalHarness.task({ id: "t-9", target_id: "tg-9" })];
  harness.api.state.boardTasks = harness.api.state.tasks.slice();

  // 计数 `#approvalBoard` 的整板重建轮数：每次铺板都会先建一个 `.d-sec-title` 再整板重铺，
  // 因此数它被追加的次数就是数整板重建的轮数。审批板里的按钮是点击目标，同一拍重建两次
  // 会把正在按的那个换成新节点（#209 的纪律），所以一次签名变化必须恰好一轮。
  let boardRenders = 0;
  const rawAppend = harness.board.appendChild.bind(harness.board);
  harness.board.appendChild = (child) => {
    if (child.classList && child.classList.contains("d-sec-title")) boardRenders += 1;
    return rawAppend(child);
  };

  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  assert.equal(boardRenders, 1, "第一次签名变化：频道审批板只铺一轮");
  assert.equal(pointerBadges(harness).length, 1, "消息行的角标同时出现");

  boardRenders = 0;
  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  assert.equal(boardRenders, 1, "第二次签名变化：频道审批板仍只铺一轮（不被消息流的重铺带成两轮）");
  assert.equal(pointerBadges(harness).length, 1);
  assert.equal(approvalByClass(harness.board, "approval-card").length, 1, "频道审批板只放频道 target 的卡");
});

test("#217 签名未变化的周期刷新不重铺消息流（同一批节点还在）", async () => {
  const harness = createApprovalHarness({
    messages: [channelPointer()],
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return approvalHarness.json({
          approvals: [approvalHarness.approval({ reply_id: "r-1", target_id: "tg-1" })],
        });
      }
      return approvalHarness.json({});
    },
  });
  harness.api.state.tasks = [approvalHarness.task()];
  harness.api.state.boardTasks = [approvalHarness.task()];
  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  showMessages(harness);
  assert.equal(pointerBadges(harness).length, 1);

  const rowBefore = approvalByClass(harness.messageList, "msg")[0];
  assert.ok(rowBefore);
  // 快照没有变化 ⇒ `loadPendingApprovals()` 不会进 `renderApprovalSurfaces()`，
  // 更不会重铺消息流：节点对象必须还是同一个（重建会让正在点的控件 / 滚动位置丢）。
  approvalHarness.periodicTick(harness);
  await approvalFlush();
  await approvalFlush();
  assert.equal(approvalByClass(harness.messageList, "msg")[0], rowBefore, "未变化时不重建消息行");
});
