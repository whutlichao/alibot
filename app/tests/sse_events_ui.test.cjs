/**
 * #141：前端消费 live-only 运行事件（`agent_event` / `team_assignment` / `plan_update` / `sop_event` /
 * `approval_*`）的 DOM 行为。
 *
 * 接缝沿用既有那一套（与 `narration_ui.test.cjs` / `search_ui.test.cjs` 同款）：`vm` 装载
 * `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点，**不新开第二套前端测试框架、不新开 seam**。
 * 帧走真入口（`handleStreamEvent` / `handleTaskStreamEvent`），不是测试往 state 里塞数据。
 *
 * 断言只落在外部可观察行为上：
 * 1. 新事件按 `target_id` 记录，不混进消息流 / 未读 / seq 游标；
 * 2. 未知 `type` 忽略且不报错，**不**推进重连游标（否则旧前端会吞掉未来消息）；
 * 3. 重连后同一消息 / 同一轮次结果不重复（补拉与实时是同一份事实）。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const AGENT = { id: "mem-1", kind: "agent", display_name: "小明", avatar_slot: 1 };

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
    // 与真实 DOM 一致：appendChild 不清掉此前的 `textContent`（`textContent = x`
    // 在真实 DOM 里只是先放了一个文本节点）。
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

function message(seq, body) {
  return {
    seq,
    body,
    kind: "agent",
    created_at: "2026-10-20T09:0" + (seq % 10) + ":00Z",
    author: AGENT,
    target_id: "ch-1",
  };
}

function createHarness() {
  const requests = [];
  const elements = new Map();

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
    fetch: async (url, options) => {
      requests.push({ url: String(url), options });
      if (String(url) === "/api/channels") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            channels: [{ id: "ch-1", name: "design", last_seq: 0, last_read_seq: 0, unread_count: 0 }],
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
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
      "\nglobalThis.__sseEventsTest = { state, bindEvents, handleStreamEvent, handleTaskStreamEvent,"
        + " absorbMessage, absorbTaskMessage, lastSeqOf, renderMessages, applyOutcomes };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__sseEventsTest;
  api.state.member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  api.state.members = [api.state.member, { ...AGENT }];
  api.state.agents = [{ ...AGENT, enabled: true, status: "online", model_id: "ollama:qwen2.5" }];
  api.state.channels = [{ id: "ch-1", name: "design", last_seq: 0, last_read_seq: 0, unread_count: 0 }];
  api.state.activeChannelId = "ch-1";
  api.state.mode = "channel";
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();
  return { api, document, requests };
}

function frame(payload) {
  return { data: JSON.stringify(payload) };
}

function liveEvent(type, targetId, extra) {
  return Object.assign({ type, target_id: targetId }, extra || {});
}

/* ─── 用例 ───────────────────────────────────────────────────────────────── */

test("新运行事件按 target 记录，不进消息流、不推进游标", async () => {
  const { api } = createHarness();
  const recorded = [
    liveEvent("agent_event", "ch-1", {
      event_type: "TEXT_BLOCK_DELTA",
      member_id: "mem-1",
      reply_id: "reply-1",
      payload: { block_id: "b1", delta: "增量" },
    }),
    liveEvent("team_assignment", "ch-1", {
      team_run_id: "run-1",
      assignment_id: "assignment-1",
      task_id: null,
      member_id: "mem-1",
      reply_id: "reply-1",
      state: "started",
    }),
    liveEvent("plan_update", "ch-1", { plan_run_id: "plan-1", member_id: "mem-1", task: { id: "pt-1" } }),
    liveEvent("sop_event", "ch-1", { sop_run_id: "sop-1", step_index: 0, phase: "started", payload: {} }),
    liveEvent("approval_required", "ch-1", { reply_id: "reply-1", member_id: "mem-1", tool_calls: [] }),
    liveEvent("approval_resolved", "ch-1", { reply_id: "reply-1", state: "resolved", decisions: [] }),
  ];
  recorded.forEach((payload) => api.handleStreamEvent("ch-1", frame(payload), payload.type));
  await flush();

  assert.equal(api.state.messages.length, 0, "运行事件不是消息，不得进入消息流");
  assert.equal(api.state.liveEvents.get("ch-1").length, recorded.length);
  assert.deepEqual(
    Array.from(api.state.liveEvents.get("ch-1"), (event) => event.type),
    recorded.map((event) => event.type),
  );
  // 没有 seq ⇒ 重连游标不动；也没有被记进消息去重集合。
  assert.equal(api.lastSeqOf("ch-1"), 0);
  assert.equal((api.state.seenSeqs.get("ch-1") || new Set()).size, 0);
});

test("未知 type 忽略且不报错，也不推进重连游标", async () => {
  const { api } = createHarness();
  assert.doesNotThrow(() => {
    api.handleStreamEvent("ch-1", frame({ type: "future_event", seq: 99, payload: {} }), "future_event");
    api.handleTaskStreamEvent("task-1", frame({ type: "future_task_event", seq: 42 }), "future_task_event");
    api.handleStreamEvent("ch-1", frame({ type: "message" }), "message"); // 缺 message 载荷
  });
  await flush();

  assert.equal(api.lastSeqOf("ch-1"), 0, "未知事件的 seq 不能成为重连游标");
  assert.equal(api.state.messages.length, 0);
  assert.equal(api.state.liveEvents.size, 0);
});

test("重连后同一消息与同一轮次结果不重复", async () => {
  const { api } = createHarness();
  const one = { type: "message", seq: 1, target_id: "ch-1", message: message(1, "同一句话") };
  // 补拉一次、实时一次：浏览器重连的真实时序。
  api.handleStreamEvent("ch-1", frame(one), "message");
  api.handleStreamEvent("ch-1", frame(one), "message");
  await flush();
  assert.equal(api.state.messages.filter((item) => item.seq === 1).length, 1);

  const outcome = {
    type: "round_outcome",
    seq: 1,
    target_id: "ch-1",
    outcome: {
      id: "ro-1",
      member_id: "mem-1",
      target_id: "ch-1",
      base_seq: 1,
      status: "replied",
      reason: "需要回应",
      started_at: "2026-10-20T09:00:00Z",
      finished_at: "2026-10-20T09:00:01Z",
      consumed_budget: false,
      merged: false,
      reply_seq: 2,
    },
  };
  api.handleStreamEvent("ch-1", frame(outcome), "round_outcome");
  api.handleStreamEvent("ch-1", frame(outcome), "round_outcome");
  await flush();
  assert.equal(api.state.outcomes[1]["mem-1"].length, 1, "同一轮次结果只留一条");
  assert.equal(api.lastSeqOf("ch-1"), 1);
});

test("任务线程消费新事件：按 target 归属，消息仍按 seq 去重", async () => {
  const { api } = createHarness();
  api.state.activeTaskId = "task-1";
  api.state.activeTask = { id: "task-1", target_id: "tgt-task-1" };

  api.handleTaskStreamEvent(
    "task-1",
    frame(liveEvent("team_assignment", "tgt-task-1", {
      team_run_id: "run-1",
      assignment_id: "assignment-1",
      task_id: "task-1",
      member_id: "mem-1",
      reply_id: "reply-1",
      state: "succeeded",
    })),
    "team_assignment",
  );
  const msg = { type: "message", seq: 1, target_id: "tgt-task-1", message: { ...message(1, "任务线程"), target_id: "tgt-task-1" } };
  api.handleTaskStreamEvent("task-1", frame(msg), "message");
  api.handleTaskStreamEvent("task-1", frame(msg), "message");
  await flush();

  assert.equal(api.state.taskMessages.filter((item) => item.seq === 1).length, 1);
  assert.equal(api.state.messages.length, 0, "任务线程消息不得混进频道消息流");
  const events = api.state.liveEvents.get("tgt-task-1");
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "team_assignment");
  assert.equal(events[0].task_id, "task-1");
});


function typingButtons(line) {
  return line.children.filter((node) => node.tagName === 'button');
}

function addSecondMember(api) {
  api.state.members.push({ id: 'mem-2', kind: 'agent', display_name: '第二位', avatar_slot: 2 });
  api.state.agents.push({ id: 'mem-2', display_name: '第二位', enabled: true, status: 'online' });
}

function replyStart(memberId, replyId) {
  return liveEvent('agent_event', 'ch-1', {
    event_type: 'REPLY_START', member_id: memberId, reply_id: replyId, payload: {},
  });
}

test('当前 reply_id 的停止按钮可见文案带成员名，aria-label 说明停的是谁的本轮', async () => {
  const { api, document, requests } = createHarness();
  const start = replyStart('mem-1', 'reply-stop');
  api.handleStreamEvent('ch-1', frame(start), 'agent_event');
  const line = document.querySelector('#typingLine');
  assert.equal(line.hidden, false);
  assert.match(line.textContent, /小明 正在回复…/);
  const button = typingButtons(line)[0];
  assert.ok(button);
  assert.equal(button.textContent, '停止 小明');
  assert.equal(button.getAttribute('aria-label'), '停止 小明 的本轮');
  await button.dispatchEvent({ type: 'click', target: button });
  assert.ok(requests.some((request) => request.url === '/api/targets/ch-1/rounds/reply-stop/interrupt' && request.options.method === 'POST'));
  assert.equal(line.hidden, true);
  assert.equal(line.children.length, 0);

  api.handleStreamEvent('ch-1', frame(start), 'agent_event');
  api.handleStreamEvent('ch-1', frame(liveEvent('round.interrupted', 'ch-1', { reply_ids: ['reply-stop'] })), 'round.interrupted');
  assert.equal(line.hidden, true);
  assert.equal(api.lastSeqOf('ch-1'), 0);
});

test('两个成员同时回复时 typing 行列出两个名字，停止按钮各自带名字且只中断自己', async () => {
  const { api, document, requests } = createHarness();
  addSecondMember(api);
  for (const [memberId, replyId] of [['mem-1', 'r1'], ['mem-2', 'r2']]) {
    api.handleStreamEvent('ch-1', frame(replyStart(memberId, replyId)), 'agent_event');
  }
  const line = document.querySelector('#typingLine');
  assert.equal(line.hidden, false);

  let buttons = typingButtons(line);
  assert.deepEqual(buttons.map((button) => button.dataset.replyId), ['r1', 'r2']);
  assert.deepEqual(
    buttons.map((button) => button.textContent),
    ['停止 小明', '停止 第二位'],
    '两个停止按钮的可见文案必须各自带成员名，不能一模一样',
  );
  assert.deepEqual(
    buttons.map((button) => button.getAttribute('aria-label')),
    ['停止 小明 的本轮', '停止 第二位 的本轮'],
  );
  assert.match(line.textContent, /小明、第二位 正在回复…/, 'typing 行必须同时列出正在回复的两位成员');

  await buttons[0].dispatchEvent({ type: 'click', target: buttons[0] });
  assert.ok(requests.some((request) => request.url === '/api/targets/ch-1/rounds/r1/interrupt' && request.options.method === 'POST'));
  assert.ok(!requests.some((request) => request.url.includes('/rounds/r2/interrupt')), '点击一个按钮不得中断另一位成员的轮次');

  buttons = typingButtons(line);
  assert.deepEqual(buttons.map((button) => button.dataset.replyId), ['r2'], '被中断的按钮移除后，另一位的按钮仍在');
  assert.equal(line.textContent, '第二位 正在回复…停止 第二位', 'typing 行的名字必须收敛到仍在回复的人');
});

test('任务线程的 typing 行与频道共用同一份渲染：两个名字、两个带名按钮', () => {
  const { api, document } = createHarness();
  addSecondMember(api);
  api.state.activeTaskId = 'task-1';
  api.state.activeTask = { id: 'task-1', target_id: 'tgt-task-1' };
  for (const [memberId, replyId] of [['mem-1', 'r1'], ['mem-2', 'r2']]) {
    api.handleStreamEvent('tgt-task-1', frame(liveEvent('agent_event', 'tgt-task-1', {
      event_type: 'REPLY_START', member_id: memberId, reply_id: replyId, payload: {},
    })), 'agent_event');
  }
  const line = document.querySelector('#taskTypingLine');
  assert.equal(line.hidden, false);
  assert.equal(line.textContent, '小明、第二位 正在回复…停止 小明停止 第二位');
  assert.deepEqual(typingButtons(line).map((button) => button.dataset.replyId), ['r1', 'r2']);
});

test('一个成员 REPLY_END 后 typing 行与停止按钮同步收敛到剩下的人', () => {
  const { api, document } = createHarness();
  addSecondMember(api);
  for (const [memberId, replyId] of [['mem-1', 'r1'], ['mem-2', 'r2']]) {
    api.handleStreamEvent('ch-1', frame(replyStart(memberId, replyId)), 'agent_event');
  }
  api.handleStreamEvent('ch-1', frame(liveEvent('agent_event', 'ch-1', {
    event_type: 'REPLY_END', member_id: 'mem-1', reply_id: 'r1', payload: {},
  })), 'agent_event');
  const line = document.querySelector('#typingLine');
  assert.equal(line.hidden, false);
  assert.equal(line.textContent, '第二位 正在回复…停止 第二位');
  assert.deepEqual(typingButtons(line).map((button) => button.dataset.replyId), ['r2']);
});

test('同一成员有多条在飞轮次：名字只显示一次，停止按钮仍按轮次各一个', async () => {
  const { api, document } = createHarness();
  for (const replyId of ['r1', 'r2']) {
    api.handleStreamEvent('ch-1', frame(replyStart('mem-1', replyId)), 'agent_event');
  }
  const line = document.querySelector('#typingLine');
  assert.equal(line.textContent, '小明 正在回复…停止 小明停止 小明');
  assert.deepEqual(typingButtons(line).map((button) => button.dataset.replyId), ['r1', 'r2']);
  await typingButtons(line)[0].dispatchEvent({ type: 'click', target: typingButtons(line)[0] });
  assert.deepEqual(typingButtons(line).map((button) => button.dataset.replyId), ['r2']);
  assert.equal(line.textContent, '小明 正在回复…停止 小明');
});

test('REPLY_START 尚未到达时，typing 事件仍按那一个成员显示且没有停止按钮', () => {
  const { api, document } = createHarness();
  api.handleStreamEvent('ch-1', frame({ type: 'typing', member_id: 'mem-1', active: true }), 'typing');
  const line = document.querySelector('#typingLine');
  assert.equal(line.hidden, false);
  assert.equal(line.textContent, '小明 正在回复…');
  assert.equal(typingButtons(line).length, 0);
});

test('中断一轮只移除它的停止按钮，同 target 的其他成员继续显示', () => {
  const { api, document } = createHarness();
  addSecondMember(api);
  for (const [member_id, reply_id] of [['mem-1', 'r1'], ['mem-2', 'r2']]) {
    api.handleStreamEvent('ch-1', frame(replyStart(member_id, reply_id)), 'agent_event');
  }
  const line = document.querySelector('#typingLine');
  assert.match(line.textContent, /小明、第二位 正在回复…/);
  api.handleStreamEvent('ch-1', frame(liveEvent('round.interrupted', 'ch-1', { reply_ids: ['r1'] })), 'round.interrupted');
  api.handleStreamEvent('ch-1', frame({ type: 'typing', member_id: 'mem-1', active: false }), 'typing');
  assert.equal(line.hidden, false);
  assert.equal(line.textContent, '第二位 正在回复…停止 第二位');
  assert.ok(line.children.some((node) => node.dataset.replyId === 'r2'));
  assert.ok(!line.children.some((node) => node.dataset.replyId === 'r1'));
});

for (const [finished_reason, reason] of [
  ['exceed_max_iters', '达到迭代上限，结论可能不完整'],
  ['interrupted', '本轮已由用户中断'],
]) {
  test(`任务线程呈现 ${finished_reason} 的轮次文案且重连不重复`, () => {
    const { api, document } = createHarness();
    api.state.activeTaskId = 'task-1';
    api.state.activeTask = {
      id: 'task-1', target_id: 'tgt-task-1', channel_id: 'ch-1', channel_name: 'design',
      title: '任务', status: 'open', allowed_transitions: [],
    };
    api.state.taskMessages = [{ ...message(1, '任务已创建'), kind: 'system' }];
    const payload = { outcome: {
      id: 'round-task-1', member_id: 'mem-1', base_seq: 1, status: 'replied',
      finished_reason, reason, finished_at: '2026-10-21T09:00:00Z',
    } };
    api.handleTaskStreamEvent('task-1', frame(payload), 'round_outcome');
    api.handleTaskStreamEvent('task-1', frame(payload), 'round_outcome');
    const history = document.querySelector('#ttHistory');
    assert.match(history.textContent, new RegExp(reason));
    assert.equal(history.querySelectorAll('[data-od-id="round-reason-round-task-1"]').length, 1);
    assert.match(document.querySelector('#ttReplyTitle').textContent, /0/);
  });
}

test('重连快照恢复当前轮的停止按钮，空快照清掉旧指示', async () => {
  const { api, document, requests } = createHarness();
  api.handleStreamEvent('ch-1', frame(liveEvent('round.active', 'ch-1', {
    rounds: { 'reply-reconnected': 'mem-1' },
  })), 'round.active');
  const line = document.querySelector('#typingLine');
  assert.equal(line.hidden, false);
  const button = line.querySelector('button');
  assert.ok(button);
  assert.equal(button.textContent, '停止 小明');
  await button.dispatchEvent({ type: 'click', target: button });
  assert.ok(requests.some((request) => request.url.includes('/reply-reconnected/interrupt')));
  api.handleStreamEvent('ch-1', frame(liveEvent('round.active', 'ch-1', {
    rounds: {},
  })), 'round.active');
  assert.equal(line.hidden, true);
  assert.equal(api.lastSeqOf('ch-1'), 0);
});
