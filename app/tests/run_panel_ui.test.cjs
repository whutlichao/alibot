/**
 * Studio 的**运行面板**（#155 / 规格 §4.6；ADR-0020 / ADR-0023）：dock 第四形态。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `channel_config_ui.test.cjs` 同款），
 * 不新开第二套前端测试框架。断言只落在**外部可观察行为**上：
 *
 * 1. 频道入口打开面板时读取 `GET /team-runs` / `GET /plan`，按 run / member 分组呈现分派状态、
 *    耗时与 `reply_excerpt`；
 * 2. 任务线程入口的 target 是 task target，`GET /team-runs` 带 `task_id` 过滤；
 * 3. live `agent_event` 按 member 归属；`team_assignment` live 帧与快照按 `assignment_id`
 *    合并（同一分派只有一行，且不把终态降回 started）；
 * 4. live `plan_update` 覆盖快照里的 Plan 三态；`deleted_at` 的行移除；
 * 5. SOP：live `sop_event` 立即显示 phase / verdict；刷新时按已知 `sop_run_id` 拉快照；
 * 6. 断线重连（EventSource `onopen`）刷新快照；
 * 7. 空态 / 错误态（可重试）；
 * 8. 运行面板不把成员过程混进消息流 / 未读 / 搜索。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

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
    this.type = "";
    this.placeholder = "";
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
    child.parentNode = null;
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

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

function channel(id, name) {
  return { id, name, description: "", last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function configPayload() {
  return {
    config: {
      channel_id: "ch_1",
      mode: "team",
      team_scope: "channel+task",
      team_leader_member_id: "m_1",
      leader_available: true,
      leader_reason: "",
      workspace_root: null,
      workspace_backend: "local",
      permission_mode: "ask-writes",
    },
  };
}

function taskPayload() {
  return {
    id: "t_1",
    channel_id: "ch_1",
    channel_name: "设计系统重构",
    target_id: "tg_1",
    title: "修一处样式",
    status: "todo",
    assignee_member_id: null,
    claimed_at: null,
    unread_count: 0,
    last_seq: 0,
  };
}

function runsPayload(overrides) {
  return {
    target_id: "ch_1",
    limit: 20,
    offset: 0,
    has_more: false,
    runs: [
      {
        id: "tr_1",
        target_id: "ch_1",
        leader_member_id: "m_1",
        status: "completed",
        started_at: "2026-10-18T10:00:00.000Z",
        finished_at: "2026-10-18T10:00:05.000Z",
        assignments: [
          {
            id: "as_1",
            team_run_id: "tr_1",
            target_id: "ch_1",
            task_id: null,
            leader_member_id: "m_1",
            member_id: "m_2",
            prompt: "写结论",
            state: "started",
            jump_consumed: 1,
            reply_excerpt: null,
            error: null,
            started_at: "2026-10-18T10:00:01.000Z",
            finished_at: null,
          },
        ],
      },
    ],
    ...(overrides || {}),
  };
}

function planPayload() {
  return {
    plan: {
      target_id: "ch_1",
      members: [
        {
          member_id: "m_2",
          display_name: "Bob",
          runs: [
            {
              id: "pr_1",
              target_id: "ch_1",
              member_id: "m_2",
              base_seq: 3,
              status: "completed",
              started_at: "2026-10-18T10:00:00.000Z",
              finished_at: "2026-10-18T10:00:04.000Z",
              tasks: [
                { id: "1", seq: 1, subject: "读代码", state: "completed", deleted_at: null },
                { id: "2", seq: 2, subject: "写测试", state: "in_progress", deleted_at: null },
                { id: "3", seq: 3, subject: "收尾", state: "pending", deleted_at: null },
              ],
            },
          ],
        },
      ],
    },
  };
}

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const elements = new Map();
  const sources = [];
  const member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const members = [
    { id: "m_1", kind: "agent", display_name: "Ada", avatar_slot: 1 },
    { id: "m_2", kind: "agent", display_name: "Bob", avatar_slot: 2 },
  ];

  class FakeEventSource {
    constructor(url) {
      this.url = String(url);
      this.listeners = new Map();
      this.onerror = null;
      this.onopen = null;
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
  }

  function defaultFetch(url) {
    if (url.startsWith("/api/targets/ch_1/team-runs")) {
      return { ok: true, status: 200, json: async () => runsPayload() };
    }
    if (url.startsWith("/api/targets/ch_1/plan")) {
      return { ok: true, status: 200, json: async () => planPayload() };
    }
    if (url.startsWith("/api/targets/tg_1/team-runs")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          target_id: "tg_1",
          limit: 20,
          offset: 0,
          has_more: false,
          runs: [],
        }),
      };
    }
    if (url.startsWith("/api/targets/tg_1/plan")) {
      return { ok: true, status: 200, json: async () => ({ plan: { target_id: "tg_1", members: [] } }) };
    }
    if (url.startsWith("/api/sop-runs/")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          run: {
            id: url.split("/").pop(),
            sop_definition_id: "sopdef_1",
            phase: "completed",
            started_at: "2026-10-18T10:00:00.000Z",
            awaiting: null,
            // 运行态只存 phase / verdict；步骤名来自定义。
            state: { steps: [{ phase: "completed", verifications: [{ passed: true, message: "ok" }] }] },
          },
        }),
      };
    }
    if (url === "/api/sops/sopdef_1") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sop: { id: "sopdef_1", name: "发布 SOP", steps: [{ subject: "起草发布说明" }] },
        }),
      };
    }
    if (url === "/api/channels/ch_1/config") {
      return { ok: true, status: 200, json: async () => configPayload() };
    }
    if (url === "/api/channels/ch_1/members") {
      return { ok: true, status: 200, json: async () => ({ members: [member, ...members] }) };
    }
    if (url === "/api/tasks/t_1/messages") {
      return { ok: true, status: 200, json: async () => ({ task: taskPayload(), messages: [] }) };
    }
    if (url === "/api/tasks/t_1/read") {
      return { ok: true, status: 200, json: async () => ({ task: taskPayload() }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  }

  function fetchStub(url, init) {
    const request = init || {};
    calls.push({ url: String(url), method: request.method || "GET" });
    if (config.fetch) return config.fetch(String(url), request);
    return defaultFetch(String(url));
  }

  function buildNode(selector) {
    const node = new FakeNode("div");
    node.hidden = HIDDEN_IN_HTML.has(selector);
    node.id = selector.startsWith("#") ? selector.slice(1) : "";
    return node;
  }

  function elementFor(selector) {
    if (!elements.has(selector)) elements.set(selector, buildNode(selector));
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
      "\nglobalThis.__runPanelTest = { state, bindEvents, render, renderHeader, syncStreams,"
        + " openRunPanelForChannel, openRunPanelForTask, openRunPanel, closeRunPanel,"
        + " renderRunPanel, loadRunPanelSnapshot, recordLiveEvent, openTask, renderTaskThread,"
        + " setDockMode, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__runPanelTest;
  api.state.member = member;
  api.state.members = [member, ...members];
  api.state.agents = members;
  api.state.channels = [];
  api.state.dms = [];
  api.state.tasks = [];
  api.state.online = true;
  api.state.ready = true;
  api.state.streamState = "open";
  api.bindEvents();

  const harness = {
    api,
    calls,
    sources,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    entry: elementFor("#btnRunPanel"),
    taskEntry: elementFor("#btnThreadRunPanel"),
    body: elementFor("#rpBody"),
    title: elementFor("#rpTitle"),
    sub: elementFor("#rpSub"),
    panel: elementFor("#dockRun"),
    dock: elementFor("#dock"),
    loadChannel(id, name) {
      api.state.channels = [channel(id || "ch_1", name || "设计系统重构")];
      api.state.dms = [];
      api.state.activeChannelId = id || "ch_1";
      api.renderHeader();
    },
    loadDm() {
      api.state.channels = [];
      api.state.dms = [{ id: "dm_1", kind: "dm", peer: { id: "m_1", display_name: "Ada" } }];
      api.state.activeChannelId = "dm_1";
      api.renderHeader();
    },
    async openChannelPanel() {
      harness.entry.dispatchEvent({ type: "click", target: harness.entry });
      await flush();
    },
    async openTaskPanel() {
      api.state.tasks = [taskPayload()];
      api.state.activeTaskId = "t_1";
      api.state.activeTask = taskPayload();
      api.setDockMode("task", true);
      harness.taskEntry.dispatchEvent({ type: "click", target: harness.taskEntry });
      await flush();
    },
    live(type, payload) {
      api.recordLiveEvent(payload.target_id || "ch_1", type, payload);
      api.renderRunPanel();
    },
    section(id) {
      return byOdId(harness.body, id);
    },
    callsTo(fragment) {
      return calls.filter((call) => call.url.includes(fragment));
    },
  };
  return harness;
}

function byOdId(root, id) {
  return (
    descendants(root).find(
      (node) =>
        (node.attributes && node.attributes["data-od-id"] === id)
        || (node.dataset && node.dataset.odId === id),
    ) || null
  );
}

function byClass(root, className) {
  return descendants(root).filter((node) => node.classList.contains(className));
}

/* ─── 1. 频道入口 · 快照 · 分组 ───────────────────────────────────────── */
test("频道入口打开运行面板：读 team-runs / plan，分派按 member 分组并显示状态、耗时、excerpt", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url.includes("/team-runs")) {
        const payload = runsPayload();
        payload.runs[0].assignments[0].state = "succeeded";
        payload.runs[0].assignments[0].finished_at = "2026-10-18T10:00:03.500Z";
        payload.runs[0].assignments[0].reply_excerpt = "成员结论";
        return { ok: true, status: 200, json: async () => payload };
      }
      if (url.includes("/plan")) return { ok: true, status: 200, json: async () => planPayload() };
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();

  assert.equal(harness.entry.hidden, false, "频道头的运行面板入口对频道可见");
  assert.equal(harness.panel.hidden, true, "打开前面板是关着的");

  await harness.openChannelPanel();

  assert.equal(harness.panel.hidden, false, "打开后运行面板可见");
  assert.equal(harness.api.state.runPanel.open, true);
  assert.equal(harness.callsTo("/team-runs").length, 1, "打开时读一次 team-runs");
  assert.equal(harness.callsTo("/plan").length, 1, "打开时读一次 plan");
  assert.equal(harness.title.textContent, "设计系统重构");

  const runs = harness.section("rp-runs");
  assert.ok(runs, "有运行段");
  const body = runs.textContent;
  assert.match(body, /Ada/, "run 头显示队长显示名");
  assert.match(body, /Bob/, "分派按成员显示名分组");
  assert.match(body, /成功/, "分派终态可读");
  assert.match(body, /耗时 2\.5s/, "两个后端时间戳算出耗时");
  assert.match(body, /成员结论/, "reply_excerpt 可见");

  const plan = harness.section("rp-plan");
  assert.ok(plan, "有 Plan 段");
  const planText = plan.textContent;
  assert.match(planText, /读代码/);
  assert.match(planText, /已完成/);
  assert.match(planText, /进行中/);
  assert.match(planText, /待开始/, "Plan 三态都可读");
});

/* ─── 2. 任务线程入口 · task_id 过滤 ─────────────────────────────────── */
test("任务线程入口用 task target 读快照，team-runs 带 task_id 过滤；关闭后落回任务线程", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.openTaskPanel();

  const urls = harness.calls.map((call) => call.url);
  assert.ok(
    urls.some((url) => url.startsWith("/api/targets/tg_1/team-runs") && url.includes("task_id=t_1")),
    "任务运行面板读 task target 的快照，并带 task_id 过滤",
  );
  assert.ok(urls.some((url) => url.startsWith("/api/targets/tg_1/plan")));
  assert.equal(harness.title.textContent, "修一处样式");
  assert.match(harness.section("rp-runs").textContent, /还没有 team 运行记录/);

  await harness.elementFor("#rpClose").dispatchEvent({
    type: "click",
    target: harness.elementFor("#rpClose"),
  });
  await flush();
  assert.equal(harness.api.state.runPanel.open, false);
  assert.equal(harness.api.state.dockMode, "task", "关闭后回到任务线程形态");
  assert.equal(harness.elementFor("#dockTask").hidden, false);
});

/* ─── 3. live 事件按成员归属；快照 + live 去重 ────────────────────────── */
test("live agent_event 按 member 归属；同一分派的 live 终态更新快照行而不重复插入", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.openChannelPanel();

  harness.live("agent_event", {
    type: "agent_event",
    target_id: "ch_1",
    member_id: "m_1",
    reply_id: "reply_aaa111",
    event_type: "TEXT_BLOCK_DELTA",
    payload: { delta: "队长正在整理结论" },
  });
  harness.live("agent_event", {
    type: "agent_event",
    target_id: "ch_1",
    member_id: "m_2",
    reply_id: "reply_bbb222",
    event_type: "TOOL_CALL_START",
    payload: { tool_call_id: "call_1", tool_call_name: "Read" },
  });

  const events = harness.section("rp-events");
  const groups = byClass(events, "rp-member");
  assert.equal(groups.length, 2, "两个成员各一组");
  assert.equal(groups[0].dataset.member, "m_1");
  assert.match(groups[0].textContent, /Ada/);
  assert.match(groups[0].textContent, /TEXT_BLOCK_DELTA/);
  assert.match(groups[0].textContent, /队长正在整理结论/);
  assert.match(groups[1].textContent, /Bob/);
  assert.match(groups[1].textContent, /Read/, "工具调用摘要来自 payload 数据字段");

  // 快照里 as_1 是 started；live 终态帧到达后仍只有一行，状态更新为 succeeded。
  harness.live("team_assignment", {
    type: "team_assignment",
    target_id: "ch_1",
    team_run_id: "tr_1",
    assignment_id: "as_1",
    task_id: null,
    member_id: "m_2",
    reply_id: "reply_bbb222",
    state: "succeeded",
  });
  const assignments = byClass(harness.section("rp-runs"), "rp-assign");
  assert.equal(assignments.length, 1, "同一 assignment_id 不重复插入");
  assert.equal(assignments[0].dataset.state, "succeeded");
  assert.match(assignments[0].textContent, /成功/);

  // 终态不会被迟到的 started 帧降级。
  harness.live("team_assignment", {
    type: "team_assignment",
    target_id: "ch_1",
    team_run_id: "tr_1",
    assignment_id: "as_1",
    task_id: null,
    member_id: "m_2",
    reply_id: "reply_bbb222",
    state: "started",
  });
  const afterStale = byClass(harness.section("rp-runs"), "rp-assign");
  assert.equal(afterStale.length, 1);
  assert.equal(afterStale[0].dataset.state, "succeeded", "迟到的 started 帧不倒退终态");
});

/* ─── 4. Plan live 覆盖与删除 ────────────────────────────────────────── */
test("live plan_update 覆盖快照 Plan 的三态；deleted_at 的项移除；新 run 替换旧清单", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.openChannelPanel();

  harness.live("plan_update", {
    type: "plan_update",
    target_id: "ch_1",
    plan_run_id: "pr_1",
    member_id: "m_2",
    task: { id: "2", seq: 2, subject: "写测试", state: "completed", deleted_at: null },
  });
  harness.live("plan_update", {
    type: "plan_update",
    target_id: "ch_1",
    plan_run_id: "pr_1",
    member_id: "m_2",
    task: { id: "3", seq: 3, subject: "收尾", deleted_at: "2026-10-18T10:00:03.000Z" },
  });

  let plan = harness.section("rp-plan");
  let rows = byClass(plan, "rp-plan-task");
  assert.equal(rows.length, 2, "deleted_at 的行从清单里移除");
  const row2 = rows.find((row) => row.dataset.planTask === "2");
  assert.equal(row2.dataset.state, "completed", "live 状态覆盖快照里的 in_progress");

  harness.live("plan_update", {
    type: "plan_update",
    target_id: "ch_1",
    plan_run_id: "pr_2",
    member_id: "m_2",
    task: { id: "1", seq: 1, subject: "新一轮计划", state: "pending", deleted_at: null },
  });
  plan = harness.section("rp-plan");
  rows = byClass(plan, "rp-plan-task");
  assert.equal(rows.length, 1, "新 plan_run_id 到达后按新清单铺（不把两轮混在一起）");
  assert.match(rows[0].textContent, /新一轮计划/);
});

/* ─── 5. SOP live + 快照刷新 ─────────────────────────────────────────── */
test("SOP：live sop_event 显示 phase / verdict；刷新按已知 sop_run_id 拉快照", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.openChannelPanel();

  harness.live("sop_event", {
    type: "sop_event",
    target_id: "ch_1",
    sop_run_id: "soprun_123456",
    step_index: 0,
    phase: "running",
    payload: { step: "起草" },
  });
  harness.live("sop_event", {
    type: "sop_event",
    target_id: "ch_1",
    sop_run_id: "soprun_123456",
    step_index: 0,
    phase: "completed",
    payload: { phase: "completed", verdict: { passed: false, message: "缺少验收命令" } },
  });

  let sop = harness.section("rp-sop");
  assert.match(sop.textContent, /起草/);
  assert.match(sop.textContent, /未通过/, "verdict 未通过可读");
  assert.match(sop.textContent, /缺少验收命令/);

  // recordLiveEvent 记下了 run id；刷新时按 id 读快照。
  assert.deepEqual([...harness.api.state.runPanel.sopIds], ["soprun_123456"]);
  await harness.elementFor("#rpRefresh").dispatchEvent({
    type: "click",
    target: harness.elementFor("#rpRefresh"),
  });
  await flush();
  assert.equal(
    harness.calls.filter((call) => call.url === "/api/sop-runs/soprun_123456").length,
    1,
    "刷新时按已知 id 读 SOP run 快照",
  );
  assert.equal(
    harness.calls.filter((call) => call.url === "/api/sops/sopdef_1").length,
    1,
    "步骤名按 definition id 读定义（run state 不存 subject）",
  );
  sop = harness.section("rp-sop");
  assert.match(sop.textContent, /已完成/, "快照 phase 覆盖 live phase");
  assert.match(sop.textContent, /通过/, "快照 verdict 也可见");
  assert.match(sop.textContent, /起草发布说明/, "步骤名来自 SOP 定义");
});

/* ─── 6. 断线重连刷新快照 ────────────────────────────────────────────── */
test("事件流重连（onopen）后重新读 team-runs / plan；面板关着时不读", async () => {
  const harness = createHarness();
  harness.loadChannel();
  harness.api.syncStreams();
  const source = harness.sources[harness.sources.length - 1];

  harness.api.state.streamState = "connecting";
  source.onopen();
  await flush();
  assert.equal(harness.callsTo("/team-runs").length, 0, "面板没开 ⇒ 重连不读快照");

  await harness.openChannelPanel();
  const before = harness.callsTo("/team-runs").length;
  harness.api.state.streamState = "reconnecting";
  source.onopen();
  await flush();
  assert.equal(harness.callsTo("/team-runs").length, before + 1, "重连后刷新 team-runs");
  assert.equal(harness.callsTo("/plan").length, 2, "重连后刷新 plan");
});

test("重连快照兜底：快照已收口的 Plan / SOP 不被断线前的旧 live 帧拉回", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url.includes("/team-runs")) return { ok: true, status: 200, json: async () => runsPayload() };
      if (url.includes("/plan")) {
        const payload = planPayload();
        payload.plan.members[0].runs[0].tasks[1].state = "completed";
        return { ok: true, status: 200, json: async () => payload };
      }
      if (url.startsWith("/api/sop-runs/")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            run: {
              id: url.split("/").pop(),
              phase: "completed",
              started_at: "2026-10-18T10:00:00.000Z",
              awaiting: null,
              state: {
                steps: [
                  { subject: "起草", phase: "completed", verifications: [{ passed: true, message: "ok" }] },
                ],
              },
            },
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  // 断线前到达的旧帧：Plan 还在进行中、SOP 还在 running。它们会在快照之前进入 live 队列。
  harness.api.recordLiveEvent("ch_1", "plan_update", {
    type: "plan_update",
    target_id: "ch_1",
    plan_run_id: "pr_1",
    member_id: "m_2",
    task: { id: "2", seq: 2, subject: "写测试", state: "in_progress", deleted_at: null },
  });
  harness.api.recordLiveEvent("ch_1", "sop_event", {
    type: "sop_event",
    target_id: "ch_1",
    sop_run_id: "soprun_123456",
    step_index: 0,
    phase: "running",
    payload: { step: "起草", phase: "running" },
  });

  await harness.openChannelPanel();

  const planRows = byClass(harness.section("rp-plan"), "rp-plan-task");
  const planRow = planRows.find((row) => row.dataset.planTask === "2");
  assert.equal(planRow.dataset.state, "completed", "本次快照的 Plan 状态优先于旧 live 帧");

  // 快照返回 completed / passed；旧 live 的 running 帧不能再把它拉回。
  const sopText = harness.section("rp-sop").textContent;
  assert.match(sopText, /已完成/, "快照 phase 优先");
  assert.match(sopText, /通过/, "快照 verdict 优先");
  assert.doesNotMatch(sopText, /运行中/, "旧 live 的 running 不覆盖快照终态");
});

/* ─── 7. 空态与错误态 ────────────────────────────────────────────────── */
test("没有运行 / Plan / 事件 / SOP 时显示各段空态", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url.includes("/team-runs")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ target_id: "ch_1", runs: [], has_more: false }),
        };
      }
      if (url.includes("/plan")) {
        return { ok: true, status: 200, json: async () => ({ plan: { target_id: "ch_1", members: [] } }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.openChannelPanel();

  const text = harness.body.textContent;
  assert.match(text, /还没有 team 运行记录/);
  assert.match(text, /还没有成员事件/);
  assert.match(text, /还没有 Plan/);
  assert.match(text, /还没有 SOP 运行/);
});

test("快照读取失败显示可读原因与重试；重试成功后铺上内容", async () => {
  let fail = true;
  const harness = createHarness({
    fetch: (url) => {
      if (url.includes("/team-runs")) {
        if (fail) {
          return {
            ok: false,
            status: 500,
            json: async () => ({ error: { code: "internal", message: "后端暂时不可用" } }),
          };
        }
        return { ok: true, status: 200, json: async () => runsPayload() };
      }
      if (url.includes("/plan")) return { ok: true, status: 200, json: async () => planPayload() };
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.openChannelPanel();

  const error = harness.section("rp-error");
  assert.ok(error, "错误态可见");
  assert.match(error.textContent, /后端暂时不可用/);
  const retry = byOdId(harness.body, "rp-retry");
  assert.ok(retry, "错误态带重试控件");

  fail = false;
  await harness.body.dispatchEvent({ type: "click", target: retry });
  await flush();
  assert.equal(harness.section("rp-error"), null, "重试成功后错误态消失");
  assert.match(harness.section("rp-runs").textContent, /Bob/);
});

/* ─── 8. 独立面：不进消息流 / 未读 / 搜索 ────────────────────────────── */
test("运行面板的 live 事件不进消息流、不计未读、不触发搜索", async () => {
  const harness = createHarness();
  harness.loadChannel();
  const before = {
    messages: harness.api.state.messages.length,
    unread: harness.api.state.channels[0].unread_count,
    seen: harness.api.state.seenSeqs.size,
  };
  await harness.openChannelPanel();

  harness.live("agent_event", {
    type: "agent_event",
    target_id: "ch_1",
    member_id: "m_2",
    reply_id: "reply_live",
    event_type: "TEXT_BLOCK_DELTA",
    payload: { delta: "过程正文" },
  });
  harness.live("team_assignment", {
    type: "team_assignment",
    target_id: "ch_1",
    team_run_id: "tr_9",
    assignment_id: "as_9",
    task_id: null,
    member_id: "m_2",
    reply_id: "reply_live",
    state: "started",
  });

  assert.equal(harness.api.state.messages.length, before.messages, "消息流不新增行");
  assert.equal(harness.api.state.channels[0].unread_count, before.unread, "未读不变");
  assert.equal(harness.api.state.seenSeqs.size, before.seen, "live 帧不推进重连游标");
  assert.equal(harness.callsTo("/api/search").length, 0, "没有搜索请求");
  assert.equal(harness.api.state.liveEvents.get("ch_1").length, 2, "事件只留在 live 层");
});

/* ─── 9. 私信没有这条面；词条两侧一致 ────────────────────────────────── */
test("私信下运行面板入口隐藏", () => {
  const harness = createHarness();
  harness.loadDm();
  assert.equal(harness.entry.hidden, true);
});

test("运行面板的新词条在两份词条表里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "runPanel.entryTitle",
    "runPanel.taskEntryTitle",
    "runPanel.sectionRuns",
    "runPanel.sectionEvents",
    "runPanel.sectionPlan",
    "runPanel.sectionSop",
    "runPanel.eventsEmpty",
    "runPanel.state.budgetExhausted",
    "runPanel.verdictPassed",
  ];
  for (const key of keys) {
    assert.ok(harness.catalogs["zh-CN"][key], key + " 在源语言里缺失");
    assert.ok(harness.catalogs.en[key], key + " 在第二语言里缺失");
  }
  // 「Plan」「SOP」是协议词，两份词条本来就同形；其余新文案必须各取各的语言。
  for (const key of keys.filter((key) => key !== "runPanel.sectionPlan" && key !== "runPanel.sectionSop")) {
    assert.notEqual(harness.catalogs["zh-CN"][key], harness.catalogs.en[key], key + " 的两份取值不能相同");
  }
});
