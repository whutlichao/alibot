/**
 * Studio 的**频道设置面**（#154 / 规格 §4.6 / ADR-0018 / ADR-0021）：
 * 模式（autonomous / team）、`team_scope=channel+task`、队长、项目根、默认后端、默认权限档；
 * team 模式下成员 `auto_claim` 已停用、任务线程只唤醒队长；任务视图读取所属频道的同一份配置。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `channel_budget_ui.test.cjs` 同款），
 * 不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 频道头入口打开设置弹窗；后端读数（mode / team_scope / 队长 / 项目根 / 后端 / 权限档）可见；
 * 2. `team_scope` 固定展示为后端给的 `channel+task`，不是前端自己拼的；
 * 3. `leader_available` / `leader_reason` 可见；team 模式下两条停用提示可见；
 * 4. 保存提交 PATCH，保存后按**响应里的新配置**重铺（读回一致）；
 * 5. 非法路径 / 非法档位 / team 无合法队长时显示后端可读原因，界面不改口；
 * 6. 任务视图读取所属频道配置并显示「任务线程只唤醒队长」；
 * 7. 私信没有这条面（入口隐藏）；
 * 8. 新增词条在两份词条表里都取到值；
 * 9. 频道头的**协作模式 pill**：不必打开弹窗就看出「自主」/「队长制 · 队长名」，
 *    私信下隐藏，读不到配置时隐藏，保存后按响应立刻更新，切频道按新频道读数重铺。
 * 10. 重铺频道设置正文（档位选择 / 保存 / 资源读回）后，`#channelConfigOverlay` 的滚动位置保持不变。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身（与 `channel_budget_ui.test.cjs` 同形；只实现用到的那些方法） ── */
class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.scrollTop = 0;
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

/** `index.html` 里带 `hidden` 的元素：替身的初始可见性从**同一个正本**读。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

function channel(id, name) {
  return { id, name, last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function dm(id, name) {
  return { id, kind: "dm", peer: { display_name: name } };
}

function membersPayload() {
  return {
    members: [
      { id: "m_1", kind: "agent", display_name: "Ada", avatar_slot: 1, enabled: true },
      { id: "m_2", kind: "agent", display_name: "Bob", avatar_slot: 2, enabled: true },
    ],
  };
}

function configPayload(overrides) {
  return {
    config: Object.assign(
      {
        channel_id: "ch_1",
        mode: "autonomous",
        team_scope: "channel+task",
        team_leader_member_id: null,
        leader_available: false,
        leader_reason: "",
        workspace_root: null,
        workspace_backend: "local",
        permission_mode: "ask-writes",
      },
      overrides || {},
    ),
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

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const elements = new Map();
  const member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };

  class FakeEventSource {
    constructor() {
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
    if (url === "/api/channels/ch_1/config" && method === "GET") {
      return { ok: true, status: 200, json: async () => configPayload() };
    }
    if (url === "/api/channels/ch_1/config" && method === "PATCH") {
      return { ok: true, status: 200, json: async () => configPayload() };
    }
    if (url === "/api/channels/ch_1/members") {
      return { ok: true, status: 200, json: async () => membersPayload() };
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
    calls.push({
      url: String(url),
      method: request.method || "GET",
      body: request.body ? JSON.parse(request.body) : null,
    });
    if (config.fetch) return config.fetch(String(url), request);
    return defaultFetch(String(url), request);
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
      "\nglobalThis.__channelConfigTest = { state, bindEvents, renderHeader,"
        + " openChannelConfigModal, closeChannelConfigModal, loadChannelConfig,"
        + " renderChannelConfigModal, selectChannelConfigMode, selectChannelConfigBackend,"
        + " selectChannelConfigPermission, submitChannelConfig, loadTaskChannelConfig,"
        + " renderTaskThread, openTask, applySettings, t };\n"
        // pill 的读数入口是本票新增的：基线副本（改前的 app.js）里没有它。用 `typeof` 判存
        // （未声明的标识符在 `typeof` 下不抛错），于是同一份用例能在改前 / 改后两个 revision 上跑，
        // 改前那些用例会以「没有这个入口」红。
        + "if (typeof loadActiveChannelMode === 'function')"
        + " globalThis.__channelConfigTest.loadActiveChannelMode = loadActiveChannelMode;\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__channelConfigTest;
  api.state.member = member;
  api.state.members = [member];
  api.state.agents = [];
  api.state.channels = [];
  api.state.dms = [];
  api.state.tasks = [];
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  // Chromium 实测行为（ego-browser 探针）：重铺正文移除最后一个（持焦的）子节点时，
  // 滚动容器会被同步拉回顶部。这不是应用行为，替身必须照实模拟，否则「重铺丢滚动位置」
  // 在用例里不可见。
  const overlayNode = elementFor("#channelConfigOverlay");
  const bodyNode = elementFor("#channelConfigBody");
  const baseRemoveChild = bodyNode.removeChild.bind(bodyNode);
  bodyNode.removeChild = (child) => {
    const removed = baseRemoveChild(child);
    if (bodyNode.children.length === 0) overlayNode.scrollTop = 0;
    return removed;
  };

  const harness = {
    api,
    calls,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    overlay: elementFor("#channelConfigOverlay"),
    body: elementFor("#channelConfigBody"),
    title: elementFor("#channelConfigTitle"),
    sub: elementFor("#channelConfigSub"),
    formError: elementFor("#channelConfigFormError"),
    toastWrap: elementFor("#toastWrap"),
    entry: elementFor("#btnChannelSettings"),
    teamNotice: elementFor("#ttTeamNotice"),
    patches() {
      return calls.filter((call) => call.method === "PATCH");
    },
    loadChannel(id, name) {
      api.state.channels = [channel(id || "ch_1", name || "设计系统重构")];
      api.state.dms = [];
      api.state.activeChannelId = id || "ch_1";
      api.renderHeader();
    },
    loadDm(id, name) {
      api.state.channels = [];
      api.state.dms = [dm(id || "dm_1", name || "小明")];
      api.state.activeChannelId = id || "dm_1";
      api.renderHeader();
    },
    async open() {
      await harness.entry.dispatchEvent({ type: "click", target: harness.entry });
      await flush();
    },
    async click(node) {
      await harness.body.dispatchEvent({ type: "click", target: node });
      await flush();
    },
    async change(node, value) {
      node.value = value;
      await harness.body.dispatchEvent({ type: "change", target: node });
      await flush();
    },
    async input(node, value) {
      node.value = value;
      await harness.body.dispatchEvent({ type: "input", target: node });
      await flush();
    },
    modeCard(mode) {
      const node = byDataset(harness.body, "configMode", mode);
      assert.ok(node, "设置面里应当有这个模式档位：" + mode);
      return node;
    },
    backendCard(backend) {
      const node = byDataset(harness.body, "configBackend", backend);
      assert.ok(node, "设置面里应当有这个后端档位：" + backend);
      return node;
    },
    permissionCard(mode) {
      const node = byDataset(harness.body, "configPermission", mode);
      assert.ok(node, "设置面里应当有这个权限档位：" + mode);
      return node;
    },
    leaderSelect() {
      const node = byDataset(harness.body, "configLeader", "1");
      assert.ok(node, "设置面里应当有队长选择控件");
      return node;
    },
    rootInput() {
      const node = byDataset(harness.body, "configWorkspaceRoot", "1");
      assert.ok(node, "设置面里应当有项目根输入框");
      return node;
    },
    saveButton() {
      const node = byDataset(harness.body, "configSave", "1");
      assert.ok(node, "设置面里应当有保存控件");
      return node;
    },
  };
  return harness;
}

/** 按 `data-*` 找一个节点（替身的选择器不支持 `[attr="value"]` 形式）。 */
function byDataset(root, key, value) {
  const attribute = "data-" + key.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
  return (
    descendants(root).find((node) => {
      if (node.dataset && node.dataset[key] === value) return true;
      return Boolean(node.attributes && node.attributes[attribute] === value);
    }) || null
  );
}

function byOdId(root, id) {
  return (
    descendants(root).find((node) => node.attributes && node.attributes["data-od-id"] === id) || null
  );
}

function textOf(root, id) {
  const node = byOdId(root, id);
  assert.ok(node, "找不到这个读数节点：" + id);
  return node.textContent;
}

function pressed(node) {
  return node.getAttribute("aria-pressed");
}

/* ─── 1. 入口与只读读数 ───────────────────────────────────────────────── */
test("频道头入口打开设置面：mode / team_scope / 队长 / 项目根 / 后端 / 权限档都是后端读数", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      if (url === "/api/channels/ch_1/config" && (request.method || "GET") === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () =>
            configPayload({
              mode: "team",
              team_leader_member_id: "m_1",
              leader_available: false,
              leader_reason: "队长已被停用",
              workspace_root: "/Users/demo/project",
              workspace_backend: "docker",
              permission_mode: "read-only",
            }),
        };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();

  assert.equal(harness.entry.hidden, false, "频道头的设置入口对频道可见");
  assert.equal(harness.overlay.hidden, true, "打开前弹窗是关着的");

  await harness.open();

  assert.equal(harness.overlay.hidden, false, "点入口把弹窗打开");
  assert.ok(
    harness.calls.some((call) => call.url === "/api/channels/ch_1/config" && call.method === "GET"),
    "打开设置面时读取频道配置",
  );
  assert.equal(harness.title.textContent, "频道设置");
  assert.equal(pressed(harness.modeCard("team")), "true", "team 模式是当前档位");
  assert.equal(pressed(harness.modeCard("autonomous")), "false");
  assert.equal(textOf(harness.body, "channel-config-team-scope"), "channel+task");
  assert.equal(textOf(harness.body, "channel-config-leader-availability"), "队长不可用");
  assert.match(textOf(harness.body, "channel-config-leader-reason"), /队长已被停用/);
  assert.equal(harness.rootInput().value, "/Users/demo/project");
  assert.equal(pressed(harness.backendCard("docker")), "true");
  assert.equal(pressed(harness.permissionCard("read-only")), "true");
});

test("私信没有频道设置面：入口隐藏", () => {
  const harness = createHarness();
  harness.loadDm();
  assert.equal(harness.entry.hidden, true);
});

/* ─── 2. team 的停用提示 ─────────────────────────────────────────────── */
test("team 模式下显示成员 auto_claim 已停用与任务线程只唤醒队长；切回 autonomous 后消失", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      if (url === "/api/channels/ch_1/config" && (request.method || "GET") === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () =>
            configPayload({
              mode: "team",
              team_leader_member_id: "m_1",
              leader_available: true,
            }),
        };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.open();

  const notices = byOdId(harness.body, "channel-config-team-notices");
  assert.ok(notices, "team 模式下应当有停用提示区");
  assert.match(notices.textContent, /auto_claim 已停用/);
  assert.match(notices.textContent, /任务线程只唤醒队长/);
  assert.equal(textOf(harness.body, "channel-config-leader-availability"), "队长可用");

  await harness.click(harness.modeCard("autonomous"));
  assert.equal(byOdId(harness.body, "channel-config-team-notices"), null, "autonomous 下不显示 team 提示");
});

/* ─── 3. 保存：PATCH 提交 + 按响应读回 ───────────────────────────────── */
test("保存提交的是全部配置字段，保存后按响应里的新配置重铺", async () => {
  let saved = false;
  const harness = createHarness({
    fetch: (url, request) => {
      if (url === "/api/channels/ch_1/config" && (request.method || "GET") === "GET") {
        return { ok: true, status: 200, json: async () => configPayload() };
      }
      if (url === "/api/channels/ch_1/config" && request.method === "PATCH") {
        saved = true;
        return {
          ok: true,
          status: 200,
          json: async () =>
            configPayload({
              mode: "team",
              team_leader_member_id: "m_2",
              leader_available: true,
              workspace_root: "/work/demo",
              workspace_backend: "docker",
              permission_mode: "trusted-writes",
            }),
        };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.click(harness.modeCard("team"));
  await harness.change(harness.leaderSelect(), "m_2");
  await harness.input(harness.rootInput(), "/work/demo");
  await harness.click(harness.backendCard("docker"));
  await harness.click(harness.permissionCard("trusted-writes"));
  await harness.click(harness.saveButton());
  await flush();

  const patches = harness.patches();
  assert.equal(patches.length, 1, "一次保存只发一个 PATCH");
  assert.equal(patches[0].url, "/api/channels/ch_1/config");
  assert.deepEqual(harness.patches()[0].body, {
    mode: "team",
    team_leader_member_id: "m_2",
    workspace_root: "/work/demo",
    workspace_backend: "docker",
    permission_mode: "trusted-writes",
    web_access: "search",
  });
  assert.ok(saved, "PATCH 真的发出去了");
  assert.equal(pressed(harness.modeCard("team")), "true", "读回后 team 仍是选中档");
  assert.equal(pressed(harness.backendCard("docker")), "true");
  assert.equal(pressed(harness.permissionCard("trusted-writes")), "true");
  assert.equal(harness.rootInput().value, "/work/demo");
  assert.equal(textOf(harness.body, "channel-config-leader-availability"), "队长可用");
  assert.equal(harness.formError.hidden, true, "成功时没有错误行");
  assert.match(harness.toastWrap.textContent, /频道配置已保存/);
});

test("清空项目根提交 null（未配置项目根是一个合法状态）", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      if (url === "/api/channels/ch_1/config" && (request.method || "GET") === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () => configPayload({ workspace_root: "/old/root" }),
        };
      }
      if (url === "/api/channels/ch_1/config" && request.method === "PATCH") {
        return {
          ok: true,
          status: 200,
          json: async () => configPayload({ workspace_root: null }),
        };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.input(harness.rootInput(), "");
  await harness.click(harness.saveButton());
  await flush();

  assert.equal(harness.patches()[0].body.workspace_root, null);
  assert.equal(harness.rootInput().value, "");
});

/* ─── 4. 整体拒绝：错误可读、界面不改口 ─────────────────────────────── */
test("非法路径被后端整体拒绝时显示可读原因，界面不改口", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      if (url === "/api/channels/ch_1/config" && (request.method || "GET") === "GET") {
        return { ok: true, status: 200, json: async () => configPayload() };
      }
      if (url === "/api/channels/ch_1/config" && request.method === "PATCH") {
        return {
          ok: false,
          status: 400,
          json: async () => ({
            error: {
              code: "validation_failed",
              message: "频道配置未保存：有字段不合法",
              fields: { workspace_root: "项目根必须是绝对路径：../escape" },
            },
          }),
        };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.input(harness.rootInput(), "../escape");
  await harness.click(harness.saveButton());
  await flush();

  assert.equal(harness.formError.hidden, false, "错误行显示出来");
  assert.match(harness.formError.textContent, /有字段不合法/);
  assert.match(harness.formError.textContent, /项目根必须是绝对路径/);
  // 没有待生效 / 已保存的假象：读回仍是后端那份配置。
  assert.equal(textOf(harness.body, "channel-config-team-scope"), "channel+task");
  assert.equal(harness.rootInput().value, "../escape", "未落库的草稿留在输入框里，界面不声称已保存");
});

test("team 没有合法队长时整体拒绝，并显示后端原因", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      if (url === "/api/channels/ch_1/config" && (request.method || "GET") === "GET") {
        return { ok: true, status: 200, json: async () => configPayload() };
      }
      if (url === "/api/channels/ch_1/config" && request.method === "PATCH") {
        assert.equal(
          Object.prototype.hasOwnProperty.call(JSON.parse(request.body), "team_leader_member_id"),
          false,
          "没有选队长时不提交空队长字段",
        );
        return {
          ok: false,
          status: 400,
          json: async () => ({
            error: {
              code: "validation_failed",
              message: "频道配置未保存：team 模式需要队长",
              fields: { team_leader_member_id: "team 模式必须选择一个当前频道成员作为队长" },
            },
          }),
        };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.click(harness.modeCard("team"));
  await harness.click(harness.saveButton());
  await flush();

  assert.equal(harness.formError.hidden, false);
  assert.match(harness.formError.textContent, /team 模式需要队长/);
  assert.match(harness.formError.textContent, /必须选择一个当前频道成员/);
});

/* ─── 5. 任务视图读取所属频道配置 ───────────────────────────────────── */
test("任务视图读取所属频道配置并显示「任务线程只唤醒队长」", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      const method = request.method || "GET";
      if (url === "/api/tasks/t_1/messages" && method === "GET") {
        return { ok: true, status: 200, json: async () => ({ task: taskPayload(), messages: [] }) };
      }
      if (url === "/api/tasks/t_1/read" && method === "POST") {
        return { ok: true, status: 200, json: async () => ({ task: taskPayload() }) };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      if (url === "/api/channels/ch_1/config" && method === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () =>
            configPayload({
              mode: "team",
              team_leader_member_id: "m_1",
              leader_available: true,
            }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.api.state.channels = [channel("ch_1", "设计系统重构")];
  harness.api.state.activeChannelId = "ch_1";
  harness.api.state.tasks = [taskPayload()];

  await harness.api.openTask("t_1");
  await flush();

  assert.ok(
    harness.calls.some((call) => call.url === "/api/channels/ch_1/config" && call.method === "GET"),
    "打开任务线程时读取所属频道的配置",
  );
  assert.equal(harness.teamNotice.hidden, false, "team 频道下任务线程显示提示");
  assert.match(harness.teamNotice.textContent, /任务线程只唤醒队长/);
});

/* ─── 6. 词条 ────────────────────────────────────────────────────────── */
test("新增词条在 zh-CN / en 两份词条表里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "channelConfig.entryTitle",
    "channelConfig.title",
    "channelConfig.sub",
    "channelConfig.closeAria",
    "channelConfig.loading",
    "channelConfig.loadFailed",
    "channelConfig.sectionMode",
    "channelConfig.mode.autonomous",
    "channelConfig.mode.autonomousNote",
    "channelConfig.mode.team",
    "channelConfig.mode.teamNote",
    "channelConfig.sectionScope",
    "channelConfig.teamScope",
    "channelConfig.teamScopeNote",
    "channelConfig.sectionLeader",
    "channelConfig.leaderSelectLabel",
    "channelConfig.leaderAvailability",
    "channelConfig.leaderNone",
    "channelConfig.leaderAvailable",
    "channelConfig.leaderUnavailable",
    "channelConfig.leaderReason",
    "channelConfig.leaderRequiredHint",
    "channelConfig.membersUnavailable",
    "channelConfig.teamNoticeAutoClaim",
    "channelConfig.teamNoticeWake",
    "channelConfig.sectionWorkspace",
    "channelConfig.workspaceRootLabel",
    "channelConfig.workspaceRootPlaceholder",
    "channelConfig.workspaceRootHint",
    "channelConfig.workspaceBackend",
    "channelConfig.backend.local",
    "channelConfig.backend.localNote",
    "channelConfig.backend.docker",
    "channelConfig.backend.dockerNote",
    "channelConfig.sectionPermission",
    "channelConfig.permission.read-only",
    "channelConfig.permission.read-onlyNote",
    "channelConfig.permission.ask-writes",
    "channelConfig.permission.ask-writesNote",
    "channelConfig.permission.trusted-writes",
    "channelConfig.permission.trusted-writesNote",
    "channelConfig.save",
    "channelConfig.saved",
    "channelConfig.saveFailed",
    "channelConfig.modeBadge.title",
    "channelConfig.modeBadge.teamWithLeader",
    "task.teamNotice",
  ];
  for (const key of keys) {
    assert.ok(harness.catalogs["zh-CN"][key], "zh-CN 缺词条：" + key);
    assert.ok(harness.catalogs.en[key], "en 缺词条：" + key);
  }
});

/* ─── 7. 频道头的协作模式 pill（不打开弹窗就看出自主 / 队长制） ──────────
   判据全部落在**外部可观察行为**上：pill 的可见文本 / `hidden` / 可读的 `title` 与 `aria-label`，
   以及它在 `index.html` 里的落点（`#chanTitle` 之后、`.chan-tools` 之前，且不在 `.chan-title` 里）。
   不读实现里的任何内部布尔。 */

/** 连等几轮宏任务：pill 的读数要经过「读配置 → 读成员 → 重绘」两跳异步。 */
async function settle() {
  for (let round = 0; round < 6; round += 1) await flush();
}

/** 一个频道配置 + 成员表都读得到的 `fetch` 替身（`ch_1`；口径与后端一致）。 */
function channelFetch(overrides, members, patches) {
  return (url, request) => {
    const method = request.method || "GET";
    if (url === "/api/channels/ch_1/config" && method === "GET") {
      return { ok: true, status: 200, json: async () => configPayload(overrides) };
    }
    if (url === "/api/channels/ch_1/config" && method === "PATCH") {
      if (patches) return patches(request);
      return { ok: true, status: 200, json: async () => configPayload(overrides) };
    }
    if (url === "/api/channels/ch_1/members") {
      return { ok: true, status: 200, json: async () => members || membersPayload() };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
}

test("team 频道：pill 显示「队长制 · 队长名」；配置还没读回时先隐藏，不伪造结论", async () => {
  const harness = createHarness({
    fetch: channelFetch({ mode: "team", team_leader_member_id: "m_1", leader_available: true }),
  });
  const pill = harness.elementFor("#chanModePill");

  harness.loadChannel();
  assert.equal(pill.hidden, true, "配置还没读回时 pill 隐藏（不伪造 team / autonomous 结论）");

  await harness.api.loadActiveChannelMode();
  await settle();
  assert.equal(pill.hidden, false, "读回 team 配置后 pill 出现");
  assert.equal(pill.textContent, "队长制 · Ada", "文本 = 模式名 + 队长显示名，中间是「·」");
  assert.match(pill.getAttribute("title"), /协作模式/, "title 说明它是协作模式");
  assert.match(pill.getAttribute("title"), /队长制 · Ada/, "title 带当前值");
  assert.equal(pill.getAttribute("aria-label"), pill.getAttribute("title"), "aria-label 与 title 同义");
});

test("autonomous 频道：pill 显示「自主」", async () => {
  const harness = createHarness({ fetch: channelFetch({ mode: "autonomous" }) });

  harness.loadChannel();
  await harness.api.loadActiveChannelMode();
  await settle();

  const pill = harness.elementFor("#chanModePill");
  assert.equal(pill.hidden, false);
  assert.equal(pill.textContent, "自主");
  assert.match(pill.getAttribute("aria-label"), /自主/);
});

test("pill 的落点：在 #chanTitle 之后、.chan-tools 之前，且不在 .chan-title 里（那里带 ellipsis）", () => {
  const html = fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8");
  const pill = html.indexOf('id="chanModePill"');
  assert.ok(pill > 0, "index.html 里应当有这颗 pill");
  assert.ok(html.indexOf('id="chanTitle"') < pill, "pill 在 #chanTitle 之后");
  assert.ok(pill < html.indexOf('class="chan-tools"'), "pill 在 .chan-tools 之前");

  // 「不在 .chan-title 里」：标题的 `<h1 class="chan-title">` 在 pill 之前开、在 pill 之前闭。
  const titleOpen = html.lastIndexOf('<h1 class="chan-title"', pill);
  const titleClose = html.lastIndexOf("</h1>", pill);
  assert.ok(titleOpen >= 0 && titleClose > titleOpen && titleClose < pill, "pill 不在 .chan-title 里");

  // 标题那条 ellipsis 语义一个字不改（pill 若挪进去就会被截断）。
  const css = fs.readFileSync(path.join(STUDIO_DIR, "app.css"), "utf8");
  assert.match(
    css,
    /\.chan-title span \{[^}]*overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/,
    ".chan-title span 的 ellipsis 语义保持原样",
  );
  assert.match(css, /\.chan-mode-pill \{[^}]*border-radius: var\(--r-pill\)/, "pill 有自己的 pill 样式");
});

test("私信下 pill 隐藏：私信没有频道 mode，也不带上一频道的文本与标签", async () => {
  const harness = createHarness({ fetch: channelFetch({ mode: "team", team_leader_member_id: "m_1" }) });
  const pill = harness.elementFor("#chanModePill");

  // 先在频道里把它铺出来（这样后面的「不残留」才不是空断言）
  harness.loadChannel();
  await harness.api.loadActiveChannelMode();
  await settle();
  assert.equal(pill.textContent, "队长制 · Ada");
  assert.ok(pill.getAttribute("aria-label"));

  harness.loadDm();
  await harness.api.loadActiveChannelMode();
  await settle();

  assert.equal(pill.hidden, true, "私信下不显示协作模式");
  assert.equal(pill.textContent, "", "隐藏时不残留上一个频道的文本");
  // 隐藏件不能带着上一频道的可读标签：这里只断言「读不到名字」，不钉死是「摘掉属性」
  // 还是「写成空串」（两种表达在真实 DOM 里都是没有名字）。
  assert.ok(!pill.getAttribute("title"), "隐藏时不携带上一频道的 title");
  assert.ok(!pill.getAttribute("aria-label"), "隐藏时不携带上一频道的 aria-label");
});

test("频道配置读不到：pill 隐藏，不伪造 team / autonomous 结论", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/channels/ch_1/config") {
        return { ok: false, status: 500, json: async () => ({ error: "后端读取失败" }) };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });

  harness.loadChannel();
  await harness.api.loadActiveChannelMode();
  await settle();

  const pill = harness.elementFor("#chanModePill");
  assert.equal(pill.hidden, true, "读不到配置就不下结论，pill 保持隐藏");
  assert.equal(pill.textContent, "");
});

test("队长成员读不到：pill 只显示「队长制」，不加占位名", async () => {
  const harness = createHarness({
    fetch: channelFetch({ mode: "team", team_leader_member_id: "m_9", leader_available: true }),
  });

  harness.loadChannel();
  await harness.api.loadActiveChannelMode();
  await settle();

  const pill = harness.elementFor("#chanModePill");
  assert.equal(pill.hidden, false);
  assert.equal(pill.textContent, "队长制", "成员表里没有这位队长时只显示模式名");
});

test("在设置面把 team 改回 autonomous 并保存：pill 立刻变「自主」（不刷新页面）", async () => {
  let patched = false;
  const harness = createHarness({
    fetch: channelFetch({ mode: "team", team_leader_member_id: "m_1", leader_available: true }, null, () => {
      patched = true;
      return { ok: true, status: 200, json: async () => configPayload({ mode: "autonomous" }) };
    }),
  });
  harness.loadChannel();
  await harness.api.loadActiveChannelMode();
  await settle();

  const pill = harness.elementFor("#chanModePill");
  assert.equal(pill.textContent, "队长制 · Ada");

  await harness.open();
  await harness.click(harness.modeCard("autonomous"));
  await harness.click(harness.saveButton());
  await settle();

  assert.equal(patched, true, "保存确实提交了 PATCH");
  assert.equal(pill.textContent, "自主", "pill 按响应里的新配置更新，不需要刷新页面");
  assert.match(pill.getAttribute("aria-label"), /自主/);
});

test("切频道：pill 按新频道的读数重铺，不残留上一个频道的模式", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      const method = request.method || "GET";
      if (url === "/api/channels/ch_1/config" && method === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () =>
            configPayload({ mode: "team", team_leader_member_id: "m_1", leader_available: true }),
        };
      }
      if (url === "/api/channels/ch_2/config" && method === "GET") {
        return { ok: true, status: 200, json: async () => configPayload({ channel_id: "ch_2" }) };
      }
      if (url === "/api/channels/ch_1/members" || url === "/api/channels/ch_2/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });

  harness.loadChannel("ch_1", "设计系统重构");
  await harness.api.loadActiveChannelMode();
  await settle();
  const pill = harness.elementFor("#chanModePill");
  assert.equal(pill.textContent, "队长制 · Ada");

  harness.api.state.channels = [channel("ch_1", "设计系统重构"), channel("ch_2", "另一个频道")];
  harness.api.state.activeChannelId = "ch_2";
  harness.api.renderHeader();
  await harness.api.loadActiveChannelMode();
  await settle();

  assert.equal(pill.textContent, "自主", "切到 autonomous 频道后 pill 跟着新频道走");
});

/* ─── 10. 联网区块（#199）：档位 / provider 来源 / 凭据掩码 / 保存 / 默认 / 清除 ── */

function providerItem(provider, overrides) {
  return Object.assign(
    { provider, configured: false, masked: "", is_default: false, updated_at: "" },
    overrides || {},
  );
}

function webProvidersPayload(providers, active) {
  return { providers, active: active || { provider: "exa-mcp", source: "fallback" } };
}

function webHarnessFetch(handlers) {
  return (url, request) => {
    const method = request.method || "GET";
    if (handlers[url + " " + method]) return handlers[url + " " + method](request);
    if (url === "/api/channels/ch_1/config" && method === "GET") {
      return { ok: true, status: 200, json: async () => configPayload() };
    }
    if (url === "/api/channels/ch_1/members") {
      return { ok: true, status: 200, json: async () => membersPayload() };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
}

test("联网区块：默认档是 search，当前 provider / 来源与五家掩码状态都可读", async () => {
  const harness = createHarness({
    fetch: webHarnessFetch({
      "/api/web-providers GET": () => ({
        ok: true,
        status: 200,
        json: async () =>
          webProvidersPayload(
            [
              providerItem("brave", { configured: true, masked: "sk-…abcd", is_default: true }),
              providerItem("tavily"),
              providerItem("perplexity"),
              providerItem("jina", { env_configured: true }),
              providerItem("exa"),
            ],
            { provider: "brave", source: "ui" },
          ),
      }),
    }),
  });
  harness.loadChannel();

  await harness.open();

  assert.equal(
    byDataset(harness.body, "configWebAccess", "search").getAttribute("aria-pressed"),
    "true",
    "未配置时界面按默认档 search 选中",
  );
  assert.equal(byDataset(harness.body, "configWebAccess", "off").getAttribute("aria-pressed"), "false");
  assert.match(textOf(harness.body, "channel-config-web-active"), /Brave/);
  assert.match(textOf(harness.body, "channel-config-web-active"), /界面配置/);
  assert.equal(textOf(harness.body, "web-provider-status-brave"), "已配置 · sk-…abcd");
  assert.equal(textOf(harness.body, "web-provider-status-tavily"), "未配置");
  assert.equal(textOf(harness.body, "web-provider-status-jina"), "由环境变量提供");
  assert.equal(textOf(harness.body, "web-provider-default-brave"), "默认");
  const key = byDataset(harness.body, "webKey", "brave");
  assert.equal(key.type, "password", "key 输入框必须是 password");
});

test("联网区块：切到 off 后保存，PATCH 体里带 web_access=off", async () => {
  const harness = createHarness({
    fetch: webHarnessFetch({
      "/api/web-providers GET": () => ({
        ok: true,
        status: 200,
        json: async () => webProvidersPayload([providerItem("brave")]),
      }),
      "/api/channels/ch_1/config PATCH": () => ({
        ok: true,
        status: 200,
        json: async () => configPayload({ web_access: "off" }),
      }),
    }),
  });
  harness.loadChannel();
  await harness.open();

  await harness.click(byDataset(harness.body, "configWebAccess", "off"));
  await harness.click(harness.saveButton());

  const patch = harness.patches()[0];
  assert.equal(patch.body.web_access, "off");
});

test("联网区块：保存一个 key 后只显示掩码，输入框清空且响应里没有完整 key", async () => {
  let saved = providerItem("brave");
  const responses = [];
  const harness = createHarness({
    fetch: (url, request) => {
      const method = request.method || "GET";
      if (url === "/api/web-providers" && method === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () => webProvidersPayload([saved]),
        };
      }
      if (url === "/api/web-providers/brave/credential" && method === "PUT") {
        saved = providerItem("brave", { configured: true, masked: "sk-…abcd" });
        const payload = { credential: saved, providers: [saved] };
        responses.push(JSON.stringify(payload));
        return { ok: true, status: 200, json: async () => payload };
      }
      if (url === "/api/channels/ch_1/config" && method === "GET") {
        return { ok: true, status: 200, json: async () => configPayload() };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.input(byDataset(harness.body, "webKey", "brave"), "sk-1234abcd");
  await harness.click(byDataset(harness.body, "webSave", "brave"));

  const put = harness.calls.find((call) => call.method === "PUT");
  assert.deepEqual(put.body, { api_key: "sk-1234abcd" });
  assert.equal(textOf(harness.body, "web-provider-status-brave"), "已配置 · sk-…abcd");
  assert.equal(byDataset(harness.body, "webKey", "brave").value, "", "保存后输入框不残留明文");
  assert.ok(
    responses.every((text) => !text.includes("sk-1234abcd")),
    "响应里绝不出现完整 key",
  );
});

test("联网区块：切换默认 provider 与清除凭据", async () => {
  let providers = [
    providerItem("brave", { configured: true, masked: "bra…1234", is_default: true }),
    providerItem("tavily", { configured: true, masked: "tav…5678" }),
  ];
  const active = () => ({ provider: "brave", source: "ui" });
  const harness = createHarness({
    fetch: (url, request) => {
      const method = request.method || "GET";
      if (url === "/api/web-providers" && method === "GET") {
        return { ok: true, status: 200, json: async () => webProvidersPayload(providers, active()) };
      }
      if (url === "/api/web-providers/tavily/credential" && method === "PUT") {
        providers = providers.map((item) =>
          Object.assign({}, item, { is_default: item.provider === "tavily" }),
        );
        return { ok: true, status: 200, json: async () => webProvidersPayload(providers, active()) };
      }
      if (url === "/api/web-providers/tavily/credential" && method === "DELETE") {
        providers = providers.map((item) =>
          item.provider === "tavily" ? providerItem("tavily") : item,
        );
        return { ok: true, status: 200, json: async () => webProvidersPayload(providers, active()) };
      }
      if (url === "/api/channels/ch_1/config" && method === "GET") {
        return { ok: true, status: 200, json: async () => configPayload() };
      }
      if (url === "/api/channels/ch_1/members") {
        return { ok: true, status: 200, json: async () => membersPayload() };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.click(byDataset(harness.body, "webDefault", "tavily"));

  const put = harness.calls.find((call) => call.method === "PUT");
  assert.deepEqual(put.body, { is_default: true }, "切换默认不带 key");
  assert.equal(textOf(harness.body, "web-provider-default-tavily"), "默认");
  assert.equal(byOdId(harness.body, "web-provider-default-brave"), null);

  await harness.click(byDataset(harness.body, "webClear", "tavily"));

  const deleted = harness.calls.find((call) => call.method === "DELETE");
  assert.equal(deleted.url, "/api/web-providers/tavily/credential");
  assert.equal(textOf(harness.body, "web-provider-status-tavily"), "未配置");
});

/* ─── 11. 重铺正文保持滚动位置 ──────────────────────────────────────────── */
/* 真实指针点击档位按钮后按钮持有焦点；重铺把持焦节点移除的 Chromium 行为由 harness 的
   `removeChild` 模拟（见上方注释）。该保持对**所有**重铺路径成立，因此四组档位卡各点一次。 */
test("选择任一组档位卡后，弹窗保持重铺前的 scrollTop", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.open();

  const cases = [
    { label: "协作模式", key: "configMode", from: "autonomous", to: "team" },
    { label: "默认后端", key: "configBackend", from: "local", to: "docker" },
    { label: "默认权限档", key: "configPermission", from: "ask-writes", to: "read-only" },
    { label: "联网", key: "configWebAccess", from: "search", to: "off" },
  ];

  for (const item of cases) {
    harness.overlay.scrollTop = 420;
    assert.ok(byDataset(harness.body, item.key, item.from), item.label + " 的旧档位应当存在");
    const target = byDataset(harness.body, item.key, item.to);
    assert.ok(target, item.label + " 的新档位应当存在");

    await harness.click(target);

    assert.equal(harness.overlay.scrollTop, 420, item.label + "：重铺后保持重铺前的 scrollTop");
    const on = byDataset(harness.body, item.key, item.to);
    const off = byDataset(harness.body, item.key, item.from);
    assert.equal(pressed(on), "true", item.label + "：新档位 aria-pressed");
    assert.equal(on.classList.contains("is-on"), true, item.label + "：新档位 is-on");
    assert.equal(pressed(off), "false", item.label + "：同组旧档位 aria-pressed");
    assert.equal(off.classList.contains("is-on"), false, item.label + "：同组旧档位 is-on");
  }
});
