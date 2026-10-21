/**
 * #153 / #209：审批卡与「线程关着时的待审批提示」的 DOM 行为。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `run_panel_ui.test.cjs`
 * / `sse_events_ui.test.cjs` 同款），不新开第二套前端测试框架、不新开 seam。
 * 帧走真入口（`handleStreamEvent` / `handleTaskStreamEvent`），恢复走真入口
 * （`loadApprovalsFor` → `GET /api/approvals?target_id=&state=pending`；#209 的全量读数
 * 走 `GET /api/approvals?state=pending`），不是测试往 state 里塞渲染数据。
 *
 * 断言只落在外部可观察行为上：
 * 1. 恢复 / 实时插入都显示工具名、参数摘要、请求成员与 `requested_at + 600s` 倒计时；
 * 2. 允许 / 拒绝提交到 `POST /api/approvals/{reply_id}`，进入 resolved 后不再提交；
 * 3. 倒计时归零 / 重启后 pending 消失 ⇒ 显示 expired，动作消失；
 * 4. 只渲染 `{id,name,input}`，敏感键值掩码，不把凭证写进 DOM；
 * 5. 频道与任务线程各按自己的 target 显示，互不串台；
 * 6. #209：全量 pending 快照驱动任务卡片 / 列表行角标与任务页签圆点；resolved / 过期后消失；
 *    周期刷新在线可见时一轮一次、离线 / 隐藏跳过、在途不叠加；频道 target 不串到任务面。
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

/* ─── 7. #209 全量读数：任务卡片 / 列表行角标 ─────────────────────────── */

test("#209 全量 pending 快照（不带 target_id）驱动任务卡片与列表行「待审批 N」角标", async () => {
  const first = task();
  const second = task({ id: "t-2", target_id: "tg-2", title: "另一个任务" });
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-1", target_id: "tg-1" }),
            approval({ reply_id: "r-2", target_id: "tg-1" }),
            // 频道 target 的 pending 也在同一份全量快照里：它不得变成任务角标。
            approval({ reply_id: "r-ch", target_id: "ch-1" }),
          ],
        });
      }
      return json({});
    },
  });
  harness.api.state.tasks = [first, second];
  harness.api.state.boardTasks = [first, second];
  harness.api.renderTaskBoard();

  periodicTick(harness);
  await flush();
  await flush();

  const requests = harness.callsTo("/api/approvals");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/approvals?state=pending");
  assert.equal(requests[0].url.includes("target_id"), false, "全量读数不得带 target_id");

  const badge = odId(harness.columns, "task-approval-badge-t-1");
  assert.ok(badge, "有待审批的任务卡片应出现角标");
  assert.equal(badge.textContent, harness.api.t("board.approvalBadge", { count: 2 }));
  assert.match(badge.textContent, /2/);
  const aria = badge.getAttribute("aria-label");
  assert.match(aria, /修一处样式/);
  assert.match(aria, /2/);
  // 卡片根是 role=button，可访问名以它为准：条数必须并进根节点的 aria 名，否则子树被覆盖。
  const cardLabel = odId(harness.columns, "task-card-t-1").getAttribute("aria-label");
  assert.match(cardLabel, /修一处样式/);
  assert.match(cardLabel, /2/);
  assert.match(cardLabel, /待审批/);
  assert.equal(odId(harness.columns, "task-approval-badge-t-2"), null, "无 pending 的卡片不渲染角标");
  // 反向不串台：任务 target 的待审批不得出现在频道消息流的 #approvalBoard。
  assert.equal(cardIn(harness.board, "r-1"), null, "任务 target 不得串进频道审批板");

  // 未变化的下一轮周期刷新不得重建看板 / 审批卡 DOM（否则会收起流转菜单、丢焦点、点击落空）。
  const cardBefore = odId(harness.columns, "task-card-t-1");
  const approvalCardBefore = cardIn(harness.board, "r-ch");
  assert.ok(approvalCardBefore);
  periodicTick(harness);
  await flush();
  await flush();
  assert.equal(odId(harness.columns, "task-card-t-1"), cardBefore, "快照未变化时不重建看板");
  assert.equal(cardIn(harness.board, "r-ch"), approvalCardBefore, "快照未变化时不重建审批卡");

  harness.api.state.taskView = "list";
  harness.api.renderTaskBoard();
  const rowBadge = odId(harness.rows, "task-approval-badge-t-1");
  assert.ok(rowBadge, "列表行应出现同一个角标");
  assert.equal(rowBadge.textContent, harness.api.t("board.approvalBadge", { count: 2 }));
  const rowLabel = odId(harness.rows, "task-row-t-1").getAttribute("aria-label");
  assert.match(rowLabel, /2/);
  assert.match(rowLabel, /待审批/);
  assert.equal(odId(harness.rows, "task-approval-badge-t-2"), null);
});

/* ─── 8. #209 任务页签圆点 ─────────────────────────────────────────────── */

test("#209 当前频道有任务 pending 时任务页签圆点出现；DM 或该频道无 pending 时不出现", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({ approvals: [approval({ reply_id: "r-1", target_id: "tg-1" })] });
      }
      return json({});
    },
  });
  harness.api.state.tasks = [task()];
  harness.api.state.boardTasks = [task()];
  periodicTick(harness);
  await flush();
  await flush();
  harness.api.renderTabs();

  assert.equal(harness.taskDot.hidden, false, "本频道有任务 pending ⇒ 圆点出现");
  const aria = harness.taskDot.getAttribute("aria-label");
  assert.match(aria, /1/);
  assert.match(aria, /等待审批/);

  const dmHarness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({ approvals: [approval({ reply_id: "r-1", target_id: "tg-1" })] });
      }
      return json({});
    },
  });
  dmHarness.api.state.dms = [dm()];
  dmHarness.api.state.activeChannelId = "dm-1";
  dmHarness.api.state.tasks = [task()];
  periodicTick(dmHarness);
  await flush();
  await flush();
  dmHarness.api.renderTabs();
  assert.equal(dmHarness.taskDot.hidden, true, "DM 下不出现任务页签圆点");

  const emptyHarness = createHarness();
  emptyHarness.api.state.tasks = [task()];
  periodicTick(emptyHarness);
  await flush();
  await flush();
  emptyHarness.api.renderTabs();
  assert.equal(emptyHarness.taskDot.hidden, true, "本频道无任务 pending ⇒ 圆点不出现");
});

/* ─── 9. #209 resolved / 越过 600s 上界 ⇒ 角标与圆点消失 ──────────────── */

test("#209 审批 resolved 后角标与圆点即时消失", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-1", target_id: "tg-1" }),
            approval({ reply_id: "r-2", target_id: "tg-1" }),
          ],
        });
      }
      return json({});
    },
  });
  harness.startTask();
  periodicTick(harness);
  await flush();
  await flush();
  assert.ok(odId(harness.columns, "task-approval-badge-t-1"));
  assert.equal(harness.taskDot.hidden, false);

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({ type: "approval_resolved", reply_id: "r-1", state: "approved", decisions: [] }),
    "approval_resolved",
  );
  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({ type: "approval_resolved", reply_id: "r-2", state: "denied", decisions: [] }),
    "approval_resolved",
  );
  await flush();

  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

test("#209 越过 600s 上界后角标与圆点在下一次渲染/刷新后消失", async () => {
  const harness = createHarness({
    now: NOW,
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-1", target_id: "tg-1", requested_at: new Date(NOW).toISOString() }),
          ],
        });
      }
      return json({});
    },
  });
  // 线程**关着**（activeTask = null）：两张审批列表里都没有卡，600s 过期只能由派生集合驱动。
  harness.api.state.tasks = [task()];
  harness.api.state.boardTasks = [task()];
  harness.api.renderTaskBoard();
  periodicTick(harness);
  await flush();
  await flush();
  assert.equal(cardIn(harness.taskList, "r-1"), null, "线程关着时不应挂审批卡");
  assert.ok(odId(harness.columns, "task-approval-badge-t-1"));
  assert.equal(harness.taskDot.hidden, false);

  harness.clock.now = NOW + 600_000;
  // 本地上界由既有 1s 倒计时探测：呈现态一变，角标/圆点在同一个出口消失，不必等下一轮 12s 轮询。
  const countdown = harness.intervalFns.find((item) => item.ms === 1000);
  assert.ok(countdown, "pending 存在时应注册 1s 倒计时");
  countdown.fn();

  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

/* ─── 10. #209 周期刷新有界；离线 / 隐藏跳过；在途不叠加 ───────────────── */

test("#209 周期刷新：在线可见时一轮恰好一次请求；离线 / 隐藏跳过；前一轮未返回不叠加", async () => {
  let snapshotCalls = 0;
  let hold = false;
  const releases = [];
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        snapshotCalls += 1;
        if (!hold) return json({ approvals: [] });
        return new Promise((resolve) => {
          releases.push(() => resolve(json({ approvals: [] })));
        });
      }
      return json({});
    },
  });

  hold = true;
  periodicTick(harness);
  periodicTick(harness);
  await flush();
  assert.equal(snapshotCalls, 1, "前一轮未返回时不得叠加第二个请求");
  releases[0]();
  await flush();
  await flush();
  hold = false;

  harness.api.state.online = false;
  periodicTick(harness);
  await flush();
  assert.equal(snapshotCalls, 1, "离线时跳过本轮");

  harness.api.state.online = true;
  harness.document.hidden = true;
  periodicTick(harness);
  await flush();
  assert.equal(snapshotCalls, 1, "页面隐藏时跳过本轮");

  harness.document.hidden = false;
  periodicTick(harness);
  await flush();
  await flush();
  assert.equal(snapshotCalls, 2, "在线可见时一轮恰好一次请求");
});

/* ─── 11. #209 频道 target 不串到任务面 ───────────────────────────────── */

test("#209 频道 target 的 pending 只属于 #approvalBoard，不产生任务角标或页签圆点", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({ approvals: [approval({ reply_id: "r-ch", target_id: "ch-1" })] });
      }
      return json({});
    },
  });
  harness.startTask();
  harness.api.render();
  periodicTick(harness);
  await flush();
  await flush();

  assert.ok(cardIn(harness.board, "r-ch"), "频道 target 的审批卡仍显示在消息流");
  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

/* ─── 12. #209 live 帧即时更新角标与圆点 ──────────────────────────────── */

test("#209 任务线程 live approval_required / approval_resolved 即时更新角标与圆点", async () => {
  const harness = createHarness();
  harness.startTask();
  harness.api.renderTaskBoard();
  harness.api.renderTabs();
  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({
      type: "approval_required",
      reply_id: "r-live",
      member_id: "mem-1",
      tool_calls: [{ id: "call-live", name: "Bash", input: { command: "npm test" } }],
    }),
    "approval_required",
  );
  await flush();

  const badge = odId(harness.columns, "task-approval-badge-t-1");
  assert.ok(badge, "live 帧应即时生成任务角标");
  assert.equal(badge.textContent, harness.api.t("board.approvalBadge", { count: 1 }));
  assert.equal(harness.taskDot.hidden, false);

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({ type: "approval_resolved", reply_id: "r-live", state: "denied", decisions: [] }),
    "approval_resolved",
  );
  await flush();

  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

/* ─── 13. #209 进入工作区的真实入口 ───────────────────────────────────── */

test("#209 进入工作区（loadWorkspace）时读一次全量 pending 并渲染任务角标", async () => {
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/settings") {
        return json({ settings: { locale: "zh-CN", message_density: "comfortable", theme: "system" } });
      }
      if (url === "/api/workspace") {
        return json({
          member: owner,
          members: [owner],
          channels: [channel()],
          dms: [],
          agents: [],
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        });
      }
      if (url === "/api/tasks") return json({ tasks: [task()] });
      if (url === "/api/reminders") return json({ reminders: [] });
      if (url === "/api/remote-data-flow") return json({ data_faces: [] });
      if (url === "/api/channels/ch-1/messages") return json({ channel: channel(), messages: [] });
      if (url === "/api/channels/ch-1/round-outcomes") return json({ round_outcomes: [] });
      if (url === "/api/channels/ch-1/members") return json({ members: [] });
      if (url === "/api/channels/ch-1/config") return json({ config: { mode: "autonomous" } });
      if (url === "/api/member-statuses") return json({ statuses: {} });
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-boot", target_id: "tg-1" }),
            approval({ reply_id: "r-boot-ch", target_id: "ch-1" }),
          ],
        });
      }
      return json({});
    },
  });

  await harness.api.loadWorkspace({ silent: true });

  const requests = harness.callsTo("/api/approvals");
  assert.equal(requests.length, 1, "进入工作区只读一次全量 pending（不再按 target 另读一次）");
  assert.equal(requests[0].url, "/api/approvals?state=pending");
  assert.equal(requests[0].url.includes("target_id"), false);
  assert.ok(odId(harness.columns, "task-approval-badge-t-1"), "全量读数后任务卡片应有角标");
  // 同一份全量读数也恢复既有频道审批卡：删掉按 target 的入口不会让频道消息流回退。
  assert.ok(cardIn(harness.board, "r-boot-ch"), "频道审批卡应由全量读数恢复");
});
