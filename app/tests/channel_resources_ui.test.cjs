/**
 * Studio 的**资源管理三段**（#156 / 规格 §4.6 / ADR-0021）：频道设置里的
 * Workspace 状态 / MCP / Skill。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `channel_config_ui.test.cjs` 同款），
 * 不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 打开设置面时按频道 scope 读取 MCP 声明与技能列表；三段各有自己的读数；
 * 2. MCP 列表与表单只出现**变量名**：请求体里没有 `env`（值），界面里没有值的输入口；
 * 3. 新增 / 编辑 / 删除 / 远端确认（幂等）都提交到对应端点，成功按后端重新读回；
 * 4. 连接失败 / 安装失败 / 删除失败都显示后端可读原因；
 * 5. Skill 列表显示 name / description / hash / enabled；本机目录与 zip 两种安装、启停、删除；
 * 6. Docker 状态只陈述后端事实：有失败原因报不可用，否则按后端说未报告 / 未使用；
 * 7. 配置动作不触发消息面（没有任何 `/messages` 请求）。
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
    members: [
      { id: "m_1", kind: "agent", display_name: "Ada", avatar_slot: 1, enabled: true },
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

function errorResponse(status, message) {
  return jsonResponse({ error: { code: "validation_failed", message } }, status);
}

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const elements = new Map();
  const member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  // 清单在替身里是**可变**的：写路径成功后重新读取，读到的就是刚写过的那一份
  // （与真实后端一致；否则「删除后重读」永远还能看见旧行）。
  const servers = (config.mcp || []).map((server) => Object.assign({}, server));
  const skills = (config.skills || []).map((skill) => Object.assign({}, skill));
  const consents = {};

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
    const mcpScope = "/api/mcp-servers?scope_kind=channel&scope_id=ch_1";
    const skillScope = "/api/skills?scope_kind=channel&scope_id=ch_1";
    if (url === "/api/channels/ch_1/config" && method === "GET") {
      return jsonResponse(configPayload(config.config));
    }
    if (url === "/api/channels/ch_1/config" && method === "PATCH") {
      return jsonResponse(configPayload(Object.assign({}, config.config, request.body || {})));
    }
    if (url === "/api/channels/ch_1/members") return jsonResponse(membersPayload());
    if (url === mcpScope && method === "GET") {
      return jsonResponse({ mcp_servers: servers });
    }
    if (url === skillScope && method === "GET") {
      return jsonResponse({ skills });
    }
    if (url === "/api/mcp-servers" && method === "POST") {
      const created = mcpPayload(Object.assign({ id: "mcp_new" }, request.body || {}));
      servers.push(created);
      return jsonResponse({ mcp_server: created }, 201);
    }
    if (url === "/api/skills" && method === "POST") {
      const name = String(request.body || "").length ? "installed-dir" : "installed-zip";
      const created = skillPayload({ id: "sk_new", name });
      skills.push(created);
      return jsonResponse({ skill: created }, 201);
    }
    if (url.startsWith("/api/mcp-servers/") && method === "PATCH") {
      const id = url.split("/").pop();
      const found = servers.find((server) => server.id === id);
      if (found) Object.assign(found, request.body || {});
      return jsonResponse({ mcp_server: found || mcpPayload({ id }) });
    }
    if (url.startsWith("/api/mcp-servers/") && method === "DELETE") {
      const id = url.split("/").pop();
      const index = servers.findIndex((server) => server.id === id);
      const [removed] = index >= 0 ? servers.splice(index, 1) : [mcpPayload({ id })];
      return jsonResponse({ mcp_server: removed });
    }
    if (url.startsWith("/api/mcp-servers/") && method === "POST" && url.endsWith("/consent")) {
      const id = url.split("/").slice(-2)[0];
      const found = servers.find((server) => server.id === id);
      const acknowledged = consents[id] || "2026-10-18T00:00:00Z";
      consents[id] = acknowledged;
      if (found) found.consented = true;
      return jsonResponse({
        mcp_server: found || mcpPayload({ id, consented: true }),
        consent: { acknowledged_at: acknowledged },
      });
    }
    if (url.startsWith("/api/skills/") && method === "PATCH") {
      const id = url.split("/").pop();
      const found = skills.find((skill) => skill.id === id);
      if (found) Object.assign(found, request.body || {});
      return jsonResponse({ skill: found || skillPayload({ id }) });
    }
    if (url.startsWith("/api/skills/") && method === "DELETE") {
      const id = url.split("/").pop();
      const index = skills.findIndex((skill) => skill.id === id);
      const [removed] = index >= 0 ? skills.splice(index, 1) : [skillPayload({ id })];
      return jsonResponse({ skill: removed });
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
      rawBody: typeof request.body === "string" ? null : request.body,
    });
    // 自定义替身只处理它关心的那几条；返回 `null` 表示「没接管」，落回默认替身。
    if (config.fetch) {
      const custom = config.fetch(String(url), request);
      if (custom) return custom;
    }
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
      "\nglobalThis.__resourceTest = { state, bindEvents, renderHeader, t,"
        + " openChannelConfigModal, closeChannelConfigModal, renderChannelConfigModal,"
        + " submitChannelConfig, loadMcpServers, loadSkills, beginMcpEdit, cancelMcpEdit,"
        + " submitMcpDraft, toggleMcpServer, deleteMcpServer, consentMcpServer,"
        + " submitSkillDirectory, uploadSkillZip, toggleSkill, deleteSkill,"
        + " dockerStatusOf, parseEnvNames };\n",
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
    patches() {
      return calls.filter((call) => call.method === "PATCH");
    },
    posts() {
      return calls.filter((call) => call.method === "POST");
    },
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
    async change(node, value) {
      node.value = value;
      await harness.body.dispatchEvent({ type: "change", target: node });
      await settle();
    },
    async input(node, value) {
      node.value = value;
      await harness.body.dispatchEvent({ type: "input", target: node });
      await settle();
    },
    async upload(node, file) {
      node.files = [file];
      await harness.body.dispatchEvent({ type: "change", target: node });
      await settle();
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

/** 最新的那个节点（重铺之后旧节点已经不在正文里）。 */
function fresh(harness, key, value) {
  return byDataset(harness.body, key, value);
}

/* ─── 1. 打开设置面：按频道 scope 读三段 ─────────────────────────────── */

test("打开设置面按频道 scope 读取 MCP 与技能，并显示已落库的项目根与后端", async () => {
  const harness = createHarness({
    mcp: [mcpPayload()],
    skills: [skillPayload()],
  });
  harness.loadChannel();
  await harness.open();

  const reads = harness.calls.map((call) => call.url);
  assert.ok(
    reads.includes("/api/mcp-servers?scope_kind=channel&scope_id=ch_1"),
    "MCP 读数应当按频道 scope 读取",
  );
  assert.ok(
    reads.includes("/api/skills?scope_kind=channel&scope_id=ch_1"),
    "技能读数应当按频道 scope 读取",
  );
  assert.equal(byOdId(harness.body, "workspace-mgmt-root").textContent, "/tmp/issue156-project");
  assert.equal(byOdId(harness.body, "workspace-mgmt-backend").textContent, "本地");
  assert.ok(byOdId(harness.body, "mcp-list"), "MCP 段应当有清单");
  assert.ok(byOdId(harness.body, "skill-list"), "技能段应当有清单");
});

/* ─── 2. MCP 只出现变量名 ────────────────────────────────────────────── */

test("MCP 列表只显示 env 变量名；表单没有值的输入口", async () => {
  const harness = createHarness({ mcp: [mcpPayload({ env_names: ["GITHUB_TOKEN", "LOG_LEVEL"] })] });
  harness.loadChannel();
  await harness.open();

  const row = byDataset(harness.body, "mcpServer", "mcp_1");
  assert.ok(row, "应当有这一条 MCP 声明");
  assert.match(row.textContent, /GITHUB_TOKEN/);
  assert.match(row.textContent, /LOG_LEVEL/);
  assert.doesNotMatch(row.textContent, /环境变量名：.*=/);
  assert.equal(
    descendants(harness.body).filter((node) => node.attributes["data-mcp-env-values"] !== undefined)
      .length,
    0,
    "界面不得存在变量值的输入口",
  );
  assert.equal(byOdId(harness.body, "mcp-env-note").textContent.includes("不保存"), true);
});

/* ─── 3. 新增 / 编辑 / 删除 ─────────────────────────────────────────── */

test("新增 MCP 提交 env_names 而不是 env 值，成功后按后端重新读回", async () => {
  const harness = createHarness({ mcp: [] });
  harness.loadChannel();
  await harness.open();

  await harness.input(byOdId(harness.body, "mcp-name"), "filesystem");
  await harness.input(byOdId(harness.body, "mcp-command"), "npx");
  await harness.input(byOdId(harness.body, "mcp-args"), "-y pkg /tmp");
  await harness.input(byOdId(harness.body, "mcp-env-names"), "GITHUB_TOKEN, LOG_LEVEL");
  await harness.click(byOdId(harness.body, "mcp-save"));

  const post = harness.posts().find((call) => call.url === "/api/mcp-servers");
  assert.ok(post, "应当提交一条新声明");
  assert.equal(post.body.scope_kind, "channel");
  assert.equal(post.body.scope_id, "ch_1");
  assert.deepEqual(post.body.env_names, ["GITHUB_TOKEN", "LOG_LEVEL"]);
  assert.equal(post.body.args.length, 3);
  assert.equal(Object.prototype.hasOwnProperty.call(post.body, "env"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(post.body, "env_values"), false);
  const reads = harness.calls.filter(
    (call) => call.url === "/api/mcp-servers?scope_kind=channel&scope_id=ch_1",
  );
  assert.equal(reads.length, 2, "成功后应当重新读一次清单");
});

test("编辑 MCP 提交 PATCH，且不带 scope（scope 是身份，不可改）", async () => {
  const harness = createHarness({ mcp: [mcpPayload()] });
  harness.loadChannel();
  await harness.open();

  await harness.click(byOdId(harness.body, "mcp-edit"));
  assert.equal(byOdId(harness.body, "mcp-name").value, "filesystem");
  await harness.input(byOdId(harness.body, "mcp-name"), "fs-renamed");
  await harness.click(byOdId(harness.body, "mcp-save"));

  const patch = harness.patches().find((call) => call.url === "/api/mcp-servers/mcp_1");
  assert.ok(patch, "应当 PATCH 这一条声明");
  assert.equal(patch.body.name, "fs-renamed");
  assert.equal(Object.prototype.hasOwnProperty.call(patch.body, "scope_kind"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(patch.body, "scope_id"), false);
});

test("删除 MCP 提交 DELETE，成功清空清单", async () => {
  const harness = createHarness({ mcp: [mcpPayload()] });
  harness.loadChannel();
  await harness.open();

  await harness.click(byOdId(harness.body, "mcp-delete"));
  const removed = harness.calls.find(
    (call) => call.method === "DELETE" && call.url === "/api/mcp-servers/mcp_1",
  );
  assert.ok(removed, "应当删除这一条声明");
  assert.equal(byDataset(harness.body, "mcpServer", "mcp_1"), null);
});

/* ─── 4. 远端确认（幂等） ────────────────────────────────────────────── */

test("远端 MCP 未确认时给出告知与确认入口；确认动作幂等，显示后端返回的确认时刻", async () => {
  const remote = mcpPayload({ id: "mcp_remote", name: "remote", transport: "http", url: "https://example.com/mcp", is_remote: true, consent_required: true, consented: false, command: "", args: [], env_names: [] });
  const harness = createHarness({ mcp: [remote] });
  harness.loadChannel();
  await harness.open();

  const row = byDataset(harness.body, "mcpServer", "mcp_remote");
  assert.match(row.textContent, /未确认/);
  await harness.click(byOdId(harness.body, "mcp-consent"));
  const consents = harness.posts().filter((call) => call.url === "/api/mcp-servers/mcp_remote/consent");
  assert.equal(consents.length, 1, "点击一次提交一次确认");
  assert.match(
    byDataset(harness.body, "mcpServer", "mcp_remote").textContent,
    /2026-10-18T00:00:00Z/,
  );

  // 幂等：再确认一次仍然后端同一条记录（同一个时刻），界面不出现第二份事实。
  await harness.api.consentMcpServer("mcp_remote");
  await settle();
  const repeated = harness.posts().filter((call) => call.url === "/api/mcp-servers/mcp_remote/consent");
  assert.equal(repeated.length, 2);
  const times = descendants(harness.body).filter((node) => node.textContent.includes("2026-10-18"));
  assert.ok(times.length >= 1);
});

/* ─── 5. 运行读数 / 连接失败可读 ─────────────────────────────────────── */

test("MCP 运行读数可读：未装配 / 已连接工具数 / 连接失败原因", async () => {
  const harness = createHarness({
    mcp: [
      mcpPayload({ id: "mcp_1", name: "fresh", runtime: null }),
      mcpPayload({
        id: "mcp_2",
        name: "ok",
        runtime: { connected: true, error: null, tools: ["echo", "read"], checked_at: "2026-10-18T00:00:00Z" },
      }),
      mcpPayload({
        id: "mcp_3",
        name: "broken",
        runtime: { connected: false, error: "Failed to start MCP 'broken': alibot-mcp-binary not found", tools: [], checked_at: "2026-10-18T00:00:00Z" },
      }),
    ],
  });
  harness.loadChannel();
  await harness.open();

  assert.match(byDataset(harness.body, "mcpRuntime", "mcp_1").textContent, /尚未装配/);
  assert.match(byDataset(harness.body, "mcpRuntime", "mcp_2").textContent, /已连接 · 2 个工具/);
  const broken = byDataset(harness.body, "mcpRuntime", "mcp_3");
  assert.match(broken.textContent, /连接失败/);
  assert.match(broken.textContent, /alibot-mcp-binary not found/);
  assert.equal(broken.classList.contains("is-error"), true);
});

/* ─── 6. Skill 列表 / 启停 / 删除 / 安装 ────────────────────────────── */

test("技能列表显示 name / description / hash / enabled；启停提交 PATCH {enabled}", async () => {
  const harness = createHarness({ skills: [skillPayload()] });
  harness.loadChannel();
  await harness.open();

  const row = byDataset(harness.body, "skill", "sk_1");
  assert.ok(row, "应当有这一条技能");
  assert.match(row.textContent, /review/);
  assert.match(row.textContent, /在提交前做一次结构化自审/);
  assert.match(row.textContent, /0123456789ab/);
  assert.match(row.textContent, /已启用/);

  await harness.click(byOdId(harness.body, "skill-toggle"));
  const patch = harness.patches().find((call) => call.url === "/api/skills/sk_1");
  assert.ok(patch, "启停应当 PATCH 该技能");
  assert.deepEqual(patch.body, { enabled: false });
  assert.equal(
    harness.calls.filter((call) => call.method === "DELETE" && call.url.startsWith("/api/skills/"))
      .length,
    0,
  );
});

test("技能删除提交 DELETE，并从清单里消失", async () => {
  const harness = createHarness({ skills: [skillPayload()] });
  harness.loadChannel();
  await harness.open();

  await harness.click(byOdId(harness.body, "skill-delete"));
  assert.ok(
    harness.calls.some((call) => call.method === "DELETE" && call.url === "/api/skills/sk_1"),
    "应当删除该技能",
  );
  assert.equal(byDataset(harness.body, "skill", "sk_1"), null);
});

test("本机目录安装提交 JSON；空路径不发请求且错误可读", async () => {
  const harness = createHarness({ skills: [] });
  harness.loadChannel();
  await harness.open();

  await harness.click(byOdId(harness.body, "skill-install-dir"));
  assert.equal(
    harness.posts().filter((call) => call.url === "/api/skills").length,
    0,
    "空路径不应该发请求",
  );
  assert.match(byOdId(harness.body, "skill-error").textContent, /绝对路径/);

  await harness.input(byOdId(harness.body, "skill-source"), "/Users/me/skills/review");
  await harness.click(byOdId(harness.body, "skill-install-dir"));
  const post = harness.posts().find((call) => call.url === "/api/skills");
  assert.ok(post, "应当提交安装请求");
  assert.deepEqual(post.body, {
    scope_kind: "channel",
    scope_id: "ch_1",
    source_path: "/Users/me/skills/review",
  });
});

test("zip 安装把原始字节送进正文，元数据走 query", async () => {
  const harness = createHarness({ skills: [] });
  harness.loadChannel();
  await harness.open();

  const file = { name: "review.zip", type: "application/zip", size: 128 };
  await harness.upload(byOdId(harness.body, "skill-zip"), file);
  const post = harness.posts().find((call) => call.url.startsWith("/api/skills?"));
  assert.ok(post, "应当提交 zip 安装请求");
  assert.equal(
    post.url,
    "/api/skills?scope_kind=channel&scope_id=ch_1&filename=review.zip",
  );
  assert.equal(post.headers["Content-Type"], "application/zip");
  assert.equal(post.rawBody, file, "正文应当是原始字节（同一个文件对象）");
});

test("安装失败 / 启停失败都显示后端可读原因", async () => {
  const harness = createHarness({
    skills: [skillPayload()],
    fetch: (url, request) => {
      if (url === "/api/skills" && (request.method || "GET") === "POST") {
        return errorResponse(400, "技能目录里没有 SKILL.md");
      }
      if (url === "/api/skills/sk_1" && request.method === "PATCH") {
        return errorResponse(400, "技能不存在或已被删除");
      }
      return null;
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.input(byOdId(harness.body, "skill-source"), "/Users/me/skills/broken");
  await harness.click(byOdId(harness.body, "skill-install-dir"));
  assert.match(byOdId(harness.body, "skill-error").textContent, /技能目录里没有 SKILL.md/);
  assert.match(byOdId(harness.body, "skill-error").textContent, /安装失败/);

  await harness.click(byOdId(harness.body, "skill-toggle"));
  assert.match(byOdId(harness.body, "skill-error").textContent, /技能不存在或已被删除/);
});

test("MCP 保存失败显示后端可读原因，且不清空草稿", async () => {
  const harness = createHarness({
    mcp: [],
    fetch: (url, request) => {
      if (url === "/api/mcp-servers" && request.method === "POST") {
        return errorResponse(
          400,
          "MCP 声明校验未通过：stdio 需要一个可执行命令",
        );
      }
      return null;
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.input(byOdId(harness.body, "mcp-name"), "broken");
  await harness.click(byOdId(harness.body, "mcp-save"));
  assert.match(
    byOdId(harness.body, "mcp-error").textContent,
    /MCP 声明校验未通过：stdio 需要一个可执行命令/,
  );
  assert.equal(byOdId(harness.body, "mcp-name").value, "broken", "失败的草稿不应被清掉");
});

/* ─── 7. Docker 状态 ────────────────────────────────────────────────── */

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

/* ─── 8. 配置动作不碰消息面 ─────────────────────────────────────────── */

test("MCP / Skill 的配置动作不触发任何消息面请求", async () => {
  const harness = createHarness({ mcp: [mcpPayload()], skills: [skillPayload()] });
  harness.loadChannel();
  await harness.open();
  // 先打开一个频道消息会话面需要的数据：本测试只关心资源动作，因此直接清掉已有调用记录。
  harness.calls.length = 0;

  await harness.click(byOdId(harness.body, "mcp-toggle"));
  await harness.click(byOdId(harness.body, "mcp-delete"));
  await harness.click(byOdId(harness.body, "skill-toggle"));
  await harness.click(byOdId(harness.body, "skill-delete"));

  const urls = harness.calls.map((call) => call.url);
  assert.deepEqual(
    urls.filter((url) => url.includes("/messages") || url.includes("/events")),
    [],
    "配置动作不得触碰消息面",
  );
  assert.deepEqual(
    urls.filter((url) => url.includes("/channels/ch_1/config")),
    [],
    "资源动作也不重读频道配置（配置事实没有变化）",
  );
});

/* ─── 9. 词条 ───────────────────────────────────────────────────────── */

test("资源三段的词条在 zh-CN / en 两份词条表里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "workspaceMgmt.section",
    "workspaceMgmt.dockerUnavailableAgent",
    "mcp.section",
    "mcp.scopeNote",
    "mcp.envNames",
    "mcp.consentRequired",
    "mcp.runtimeFailed",
    "skill.section",
    "skill.hashLabel",
    "skill.installZipHint",
  ];
  for (const key of keys) {
    assert.equal(typeof harness.catalogs["zh-CN"][key], "string", `${key} 缺少中文词条`);
    assert.equal(typeof harness.catalogs.en[key], "string", `${key} 缺少英文词条`);
  }
  assert.equal(harness.api.parseEnvNames("A, B  A\nC").length, 3);
});
