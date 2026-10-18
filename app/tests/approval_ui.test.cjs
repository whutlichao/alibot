/**
 * #153：审批卡（规格 §4.6 第 4 条 / ADR-0021 §四）的 DOM 行为。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `run_panel_ui.test.cjs`
 * / `sse_events_ui.test.cjs` 同款），不新开第二套前端测试框架、不新开 seam。
 * 帧走真入口（`handleStreamEvent` / `handleTaskStreamEvent`），恢复走真入口
 * （`loadApprovalsFor` → `GET /api/approvals?target_id=&state=pending`），不是测试
 * 往 state 里塞渲染数据。
 *
 * 断言只落在外部可观察行为上：
 * 1. 恢复 / 实时插入都显示工具名、参数摘要、请求成员与 `requested_at + 600s` 倒计时；
 * 2. 允许 / 拒绝提交到 `POST /api/approvals/{reply_id}`，进入 resolved 后不再提交；
 * 3. 倒计时归零 / 重启后 pending 消失 ⇒ 显示 expired，动作消失；
 * 4. 只渲染 `{id,name,input}`，敏感键值掩码，不把凭证写进 DOM；
 * 5. 频道与任务线程各按自己的 target 显示，互不串台。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
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

function task() {
  return {
    id: "t-1",
    channel_id: "ch-1",
    channel_name: "design",
    target_id: "tg-1",
    title: "修一处样式",
    status: "todo",
    assignee_member_id: null,
    unread_count: 0,
    last_seq: 0,
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
    setInterval: (fn) => {
      intervalFns.push(fn);
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
        + " loadApprovalsFor, handleStreamEvent, handleTaskStreamEvent, submitApproval,"
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
    intervalFns,
    board: elementFor("#approvalBoard"),
    taskSection: elementFor("#ttApprovalSection"),
    taskList: elementFor("#ttApprovals"),
    callsTo(fragment) {
      return calls.filter((call) => call.url.includes(fragment));
    },
    startTask() {
      api.state.activeTaskId = "t-1";
      api.state.activeTask = task();
      api.state.tasks = [task()];
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

/* ─── 1. 快照恢复 · 审批卡内容 · 倒计时 ───────────────────────────────────── */

test("频道视图从 GET /approvals?state=pending 恢复审批卡：工具名、参数摘要、成员、剩余时间", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: [approval()] });
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  await flush();

  const request = harness.callsTo("/api/approvals");
  assert.equal(request.length, 1);
  assert.equal(request[0].url, "/api/approvals?target_id=ch-1&state=pending");
  assert.equal(harness.board.hidden, false);

  const card = cardIn(harness.board, "reply-1");
  assert.ok(card, "频道消息流里应出现审批卡");
  assert.equal(card.dataset.state, "pending");
  assert.match(card.textContent, /Write/);
  assert.match(card.textContent, /notes\.txt/);
  assert.match(card.textContent, /Ada/);
  assert.equal(
    byClass(card, "approval-remaining")[0].textContent,
    harness.api.t("approval.remaining", { time: "09:00" }),
  );
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.pending"));
});

/* ─── 2. 倒计时归零 ⇒ expired，动作消失 ─────────────────────────────────── */

test("倒计时按 requested_at + 600s 递减，归零后显示已过期且不再有允许/拒绝", async () => {
  const harness = createHarness({
    now: NOW,
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) {
        return json({ approvals: [approval({ requested_at: new Date(NOW).toISOString() })] });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  let card = cardIn(harness.board, "reply-1");
  assert.equal(
    byClass(card, "approval-remaining")[0].textContent,
    harness.api.t("approval.remaining", { time: "10:00" }),
  );

  harness.clock.now = NOW + 60_000;
  harness.api.renderApprovalSurfaces();
  card = cardIn(harness.board, "reply-1");
  assert.equal(
    byClass(card, "approval-remaining")[0].textContent,
    harness.api.t("approval.remaining", { time: "09:00" }),
  );
  assert.equal(actionButtons(card).length, 2);

  harness.clock.now = NOW + 600_000;
  harness.api.renderApprovalSurfaces();
  card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "expired");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.expired"));
  assert.equal(actionButtons(card).length, 0, "过期后不得还能提交");
});

/* ─── 3. 允许 · 幂等 · resolved 后禁止重复提交 ──────────────────────────── */

test("允许提交全部 tool_call 到 POST /api/approvals/{reply_id}，进入 approved 后不再发请求", async () => {
  let resolvePost = null;
  const postCalls = [];
  const harness = createHarness({
    fetch: (url, request) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: [approval()] });
      if (url === "/api/approvals/reply-1" && request.method === "POST") {
        postCalls.push(JSON.parse(request.body));
        return new Promise((resolve) => {
          resolvePost = () =>
            resolve(json({
              approval: approval({
                state: "approved",
                decisions: [
                  { tool_call_id: "call-1", confirmed: true },
                  { tool_call_id: "call-2", confirmed: true },
                ],
                resolved_at: new Date(NOW).toISOString(),
              }),
            }));
        });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  const allow = actionButtons(harness.board).find((button) => button.dataset.approvalAction === "allow");
  assert.ok(allow);

  // 双击：提交中的第二次点击不得再发一次 POST（按钮同时进入 disabled）。
  await harness.board.dispatchEvent({ type: "click", target: allow });
  await harness.board.dispatchEvent({ type: "click", target: allow });
  await flush();

  assert.equal(postCalls.length, 1, "提交中重复点击只允许一次 POST");
  assert.deepEqual(postCalls[0], {
    decisions: [
      { tool_call_id: "call-1", confirmed: true },
      { tool_call_id: "call-2", confirmed: true },
    ],
  });
  assert.ok(actionButtons(harness.board).every((button) => button.disabled), "提交中动作禁用");

  resolvePost();
  await flush();
  await flush();

  let card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "approved");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.approved"));
  assert.equal(actionButtons(card).length, 0);

  // resolved 之后即使拿旧按钮再点一次也不得重复提交。
  await harness.board.dispatchEvent({ type: "click", target: allow });
  await flush();
  assert.equal(postCalls.length, 1);
  card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "approved");
});

test("拒绝提交 confirmed=false，UI 进入 denied", async () => {
  const postCalls = [];
  const harness = createHarness({
    fetch: (url, request) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: [approval()] });
      if (url === "/api/approvals/reply-1" && request.method === "POST") {
        postCalls.push(JSON.parse(request.body));
        return json({
          approval: approval({
            state: "denied",
            decisions: [
              { tool_call_id: "call-1", confirmed: false },
              { tool_call_id: "call-2", confirmed: false },
            ],
            resolved_at: new Date(NOW).toISOString(),
          }),
        });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  const deny = actionButtons(harness.board).find((button) => button.dataset.approvalAction === "deny");
  await harness.board.dispatchEvent({ type: "click", target: deny });
  await flush();

  assert.equal(postCalls.length, 1);
  assert.deepEqual(postCalls[0], {
    decisions: [
      { tool_call_id: "call-1", confirmed: false },
      { tool_call_id: "call-2", confirmed: false },
    ],
  });
  const card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "denied");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.denied"));
  assert.equal(actionButtons(card).length, 0);
});

/* ─── 4. 实时帧 · 频道与任务线程各按 target ─────────────────────────────── */

test("SSE approval_required 实时插入、approval_resolved 更新；频道与任务线程按 target 分开", async () => {
  const harness = createHarness();

  harness.api.handleStreamEvent(
    "ch-1",
    frame({
      type: "approval_required",
      reply_id: "reply-ch",
      member_id: "mem-1",
      tool_calls: [{ id: "call-ch", name: "Write", input: { path: "channel.md" } }],
    }),
    "approval_required",
  );
  await flush();

  let channelCard = cardIn(harness.board, "reply-ch");
  assert.ok(channelCard, "频道 SSE 应实时插入审批卡");
  assert.match(channelCard.textContent, /channel\.md/);

  harness.api.handleStreamEvent(
    "ch-1",
    frame({
      type: "approval_resolved",
      reply_id: "reply-ch",
      state: "approved",
      decisions: [{ tool_call_id: "call-ch", confirmed: true }],
    }),
    "approval_resolved",
  );
  await flush();
  channelCard = cardIn(harness.board, "reply-ch");
  assert.equal(channelCard.dataset.state, "approved");

  harness.startTask();
  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({
      type: "approval_required",
      reply_id: "reply-tg",
      member_id: "mem-1",
      tool_calls: [{ id: "call-tg", name: "Bash", input: { command: "npm test" } }],
    }),
    "approval_required",
  );
  await flush();

  assert.equal(harness.taskSection.hidden, false);
  const taskCard = cardIn(harness.taskList, "reply-tg");
  assert.ok(taskCard, "任务线程应显示本 task target 的审批卡");
  assert.match(taskCard.textContent, /npm test/);
  assert.equal(cardIn(harness.taskList, "reply-ch"), null, "频道卡不得串进任务线程");
  assert.equal(cardIn(harness.board, "reply-tg"), null, "任务卡不得串进频道消息流");

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({
      type: "approval_resolved",
      reply_id: "reply-tg",
      state: "expired",
      decisions: [],
    }),
    "approval_resolved",
  );
  await flush();
  assert.equal(cardIn(harness.taskList, "reply-tg").dataset.state, "expired");
});

/* ─── 5. 敏感键掩码 · 只渲染 {id,name,input} ───────────────────────────── */

test("只渲染 {id,name,input}；api_key / password / TOKEN / Authorization 的值在 DOM 中被掩码", async () => {
  const secrets = {
    api: "sk-live-DO-NOT-LEAK",
    password: "hunter2-DO-NOT-LEAK",
    authorization: "Bearer DO-NOT-LEAK",
    env: "env-DO-NOT-LEAK",
    callLevel: "call-level-DO-NOT-LEAK",
  };
  const harness = createHarness({
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) {
        return json({
          approvals: [
            approval({
              tool_calls: [
                {
                  id: "call-1",
                  name: "Bash",
                  input: {
                    command: "npm test",
                    api_key: secrets.api,
                    nested: { password: secrets.password, Authorization: secrets.authorization },
                    env: { TOKEN: secrets.env },
                  },
                  // 后端已收窄；即便多出来的字段也绝不能进 DOM。
                  credentials: secrets.callLevel,
                },
              ],
            }),
          ],
        });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  const card = cardIn(harness.board, "reply-1");
  const text = card.textContent;
  assert.match(text, /npm test/);
  assert.match(text, /api_key=••••/);
  assert.match(text, /"password":"••••"/);
  assert.match(text, /"TOKEN":"••••"/);
  assert.match(text, /"Authorization":"••••"/);
  Object.values(secrets).forEach((secret) => {
    assert.equal(text.includes(secret), false, `凭证值不得出现在 DOM：${secret}`);
  });
});

/* ─── 6. 重启 / 失联后 pending 消失 ⇒ expired ──────────────────────────── */

test("快照里 pending 消失（进程重启把 pending 标 expired）后卡片转为 expired 且不可提交", async () => {
  let records = [approval()];
  const harness = createHarness({
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: records.slice() });
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  assert.equal(cardIn(harness.board, "reply-1").dataset.state, "pending");
  assert.equal(actionButtons(harness.board).length, 2);

  records = [];
  await harness.api.loadApprovalsFor("ch-1");
  await flush();

  const card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "expired");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.expired"));
  assert.equal(actionButtons(card).length, 0);
});
