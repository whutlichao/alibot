/**
 * Studio 的**频道设置 · 工作区与数据面**段（原 #156 资源三段中的第一段），
 * 以及 #273 之后的边界：MCP 与技能**不再**出现在频道配置里。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `channel_config_ui.test.cjs` 同款），
 * 不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 打开设置面显示已落库的项目根 / 后端 / Docker 状态（读数全部来自后端）；
 * 2. Docker 状态只陈述后端事实：失败原因 / 未报告 / 未使用；
 * 3. 频道配置里没有 MCP 段与技能段（#273 把它们搬到 Agent 详情），也不再按频道 scope 读
 *    这两条清单——旧行仍留在库里但不展示、不做「已弃用」提示；
 * 4. 频道配置的写动作不触发任何消息面请求；
 * 5. 这一段用到的词条在 zh-CN / en 两份词条表里都取到值。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身（与 `channel_config_ui.test.cjs` 同形；只实现用到的那些方法） ── */
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
    this.accept = "";
    this.files = null;
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

async function settle() {
  await flush();
  await flush();
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

function membersPayload() {
  return {
    members: [{ id: "m_1", kind: "agent", display_name: "Ada", avatar_slot: 1, enabled: true }],
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
        workspace_root: "/tmp/issue156-project",
        workspace_backend: "local",
        permission_mode: "ask-writes",
      },
      overrides || {},
    ),
  };
}

function mcpPayload(overrides) {
  return Object.assign(
    {
      id: "mcp_1",
      name: "filesystem",
      transport: "stdio",
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      url: "",
      env_names: ["GITHUB_TOKEN"],
      enabled: true,
      scope_kind: "channel",
      scope_id: "ch_1",
      is_remote: false,
      consent_required: false,
      consented: false,
      runtime: null,
    },
    overrides || {},
  );
}

function skillPayload(overrides) {
  return Object.assign(
    {
      id: "sk_1",
      name: "review",
      description: "在提交前做一次结构化自审",
      source_path: "/Users/me/skills/review",
      content_hash: "0123456789abcdef".repeat(4),
      enabled: true,
      scope_kind: "channel",
      scope_id: "ch_1",
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
  const member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  // 旧频道级行仍在库里：它们**可以**被后端读到，但频道配置不该展示、也不该去读。
  const legacyServers = (config.mcp || []).map((server) => Object.assign({}, server));
  const legacySkills = (config.skills || []).map((skill) => Object.assign({}, skill));

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
      return jsonResponse(configPayload(config.config));
    }
    if (url === "/api/channels/ch_1/config" && method === "PATCH") {
      return jsonResponse(configPayload(Object.assign({}, config.config, request.body || {})));
    }
    if (url === "/api/channels/ch_1/members") return jsonResponse(membersPayload());
    // 若前端仍按频道 scope 读这两条清单，这里会把旧行交给它——测试由此可判定「不该读」。
    if (url === "/api/mcp-servers?scope_kind=channel&scope_id=ch_1" && method === "GET") {
      return jsonResponse({ mcp_servers: legacyServers });
    }
    if (url === "/api/skills?scope_kind=channel&scope_id=ch_1" && method === "GET") {
      return jsonResponse({ skills: legacySkills });
    }
    return jsonResponse({});
  }

  function fetchStub(url, init) {
    const request = init || {};
    const parsedBody =
      typeof request.body === "string" && request.body.length ? JSON.parse(request.body) : null;
    calls.push({
      url: String(url),
      method: request.method || "GET",
      headers: request.headers || null,
      body: parsedBody,
    });
    if (config.fetch) {
      const custom = config.fetch(String(url), request);
      if (custom) return custom;
    }
    return defaultFetch(String(url), request);
  }

  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = new FakeNode("div");
      node.hidden = HIDDEN_IN_HTML.has(selector);
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
      "\nglobalThis.__resourceTest = { state, bindEvents, renderHeader, t,"
        + " openChannelConfigModal, closeChannelConfigModal, renderChannelConfigModal,"
        + " submitChannelConfig, dockerStatusOf };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__resourceTest;
  api.state.member = member;
  api.state.members = [member];
  api.state.agents = [];
  api.state.channels = [];
  api.state.dms = [];
  api.state.tasks = [];
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  const harness = {
    api,
    calls,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    overlay: elementFor("#channelConfigOverlay"),
    body: elementFor("#channelConfigBody"),
    formError: elementFor("#channelConfigFormError"),
    entry: elementFor("#btnChannelSettings"),
    loadChannel(id, name) {
      api.state.channels = [channel(id || "ch_1", name || "设计系统重构")];
      api.state.dms = [];
      api.state.activeChannelId = id || "ch_1";
      api.renderHeader();
    },
    async open() {
      await harness.entry.dispatchEvent({ type: "click", target: harness.entry });
      await settle();
    },
    async click(node) {
      assert.ok(node, "要点击的控件应当存在");
      await harness.body.dispatchEvent({ type: "click", target: node });
      await settle();
    },
  };
  return harness;
}

function byOdId(root, id) {
  return (
    descendants(root).find((node) => node.attributes && node.attributes["data-od-id"] === id) || null
  );
}

/* ─── 1. 打开设置面：工作区与数据面的读数 ───────────────────────────── */

test("打开设置面显示已落库的项目根与后端", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.open();

  assert.equal(harness.overlay.hidden, false, "设置面打开");
  assert.equal(byOdId(harness.body, "workspace-mgmt-root").textContent, "/tmp/issue156-project");
  assert.equal(byOdId(harness.body, "workspace-mgmt-backend").textContent, "本地");
  assert.ok(byOdId(harness.body, "workspace-mgmt-notes"), "说明段落仍在");
});

/* ─── 2. 频道配置里没有能力段（#273） ───────────────────────────────── */

test("频道配置不再有 MCP / 技能段，也不按频道 scope 读这两条清单", async () => {
  const harness = createHarness({ mcp: [mcpPayload()], skills: [skillPayload()] });
  harness.loadChannel();
  await harness.open();

  for (const odId of [
    "channel-config-section-mcp",
    "channel-config-section-skill",
    "mcp-list",
    "mcp-form",
    "mcp-error",
    "skill-list",
    "skill-install",
    "skill-error",
  ]) {
    assert.equal(byOdId(harness.body, odId), null, `频道配置里不该再有：${odId}`);
  }
  assert.deepEqual(
    descendants(harness.body).filter((node) => node.tagName === "input" && node.type === "file"),
    [],
    "频道配置里没有 zip 上传控件",
  );
  assert.deepEqual(
    harness.calls.filter((call) => call.url.includes("scope_kind=channel")).map((call) => call.url),
    [],
    "打开频道配置不再按频道 scope 读 MCP / 技能",
  );
  // 旧行仍在库里（替身可提供），只是不展示——不做「已弃用」提示。
  assert.equal(harness.body.textContent.includes("filesystem"), false);
  assert.equal(harness.body.textContent.includes("已弃用"), false);
});

/* ─── 3. Docker 状态只陈述后端事实 ──────────────────────────────────── */

test("Docker 状态只陈述后端事实：失败原因 / 未报告 / 未使用", async () => {
  const unavailable = createHarness({ config: { workspace_backend: "docker" } });
  unavailable.api.state.agents = [
    { id: "m_1", display_name: "Ada", enabled_reason: "Docker 不可用：Cannot connect to the Docker daemon" },
  ];
  unavailable.loadChannel();
  await unavailable.open();
  const cell = byOdId(unavailable.body, "workspace-mgmt-docker");
  assert.equal(cell.dataset.dockerState, "unavailable");
  assert.match(cell.textContent, /Ada/);
  assert.match(cell.textContent, /Cannot connect to the Docker daemon/);

  const reported = createHarness({ config: { workspace_backend: "docker" } });
  reported.api.state.agents = [{ id: "m_1", display_name: "Ada", enabled_reason: "" }];
  reported.loadChannel();
  await reported.open();
  const reportedCell = byOdId(reported.body, "workspace-mgmt-docker");
  assert.equal(reportedCell.dataset.dockerState, "reported");
  assert.match(reportedCell.textContent, /未报告故障/);

  const unused = createHarness();
  unused.api.state.agents = [{ id: "m_1", display_name: "Ada", enabled_reason: "" }];
  unused.loadChannel();
  await unused.open();
  const unusedCell = byOdId(unused.body, "workspace-mgmt-docker");
  assert.equal(unusedCell.dataset.dockerState, "unused");
  assert.match(unusedCell.textContent, /本地/);
});

/* ─── 4. 频道配置的写动作不碰消息面 ─────────────────────────────────── */

test("频道配置的动作不触发任何消息面请求", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.open();
  harness.calls.length = 0;

  await harness.click(byOdId(harness.body, "channel-config-save"));

  const urls = harness.calls.map((call) => call.url);
  assert.ok(urls.includes("/api/channels/ch_1/config"), "保存仍走频道配置端点");
  assert.deepEqual(
    urls.filter((url) => url.includes("/messages") || url.includes("/events")),
    [],
    "配置动作不得触碰消息面",
  );
});

/* ─── 5. 词条 ───────────────────────────────────────────────────────── */

test("工作区段的词条在 zh-CN / en 两份词条表里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "workspaceMgmt.section",
    "workspaceMgmt.root",
    "workspaceMgmt.backend",
    "workspaceMgmt.dockerStatus",
    "workspaceMgmt.dockerUnavailableAgent",
    "workspaceMgmt.dockerReported",
    "workspaceMgmt.dockerUnused",
    "workspaceMgmt.noteTools",
  ];
  for (const key of keys) {
    assert.equal(typeof harness.catalogs["zh-CN"][key], "string", `${key} 缺少中文词条`);
    assert.equal(typeof harness.catalogs.en[key], "string", `${key} 缺少英文词条`);
  }
});
