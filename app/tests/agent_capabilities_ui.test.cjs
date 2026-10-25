/**
 * Studio 的**能力配置**（#273）：Agent 详情里的 MCP / 技能两段 + 复制到其他成员 + 名册摘要，
 * 以及「频道配置里不再有这两段」。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `agent_detail_ui.test.cjs` /
 * `channel_resources_ui.test.cjs` 同款），不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 打开 Agent 详情：能力段按 `scope_kind=agent&scope_id=<member>` 读两条清单，MCP 行与技能行
 *    同屏；每行都带编辑 / 安装 / 启停 / 删除入口，没有 scope 选择器；
 * 2. 复制到其他成员：每行一个入口，弹多选（排除当前成员）；目标已存在同名时逐条给出「已跳过」
 *    且不覆盖（没有 PATCH、没有第二个写入体）；
 * 3. 频道配置里不再出现 MCP / 技能段，也不再按频道 scope 读这两条清单（Workspace 段仍在）；
 * 4. 名册的 Agent 行显示 `MCP n / 技能 n`；单成员读取失败只让那一个摘要显示占位，名册照常列出；
 * 5. 新增文案在两份词条表里都取到值。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身（与 `agent_detail_ui.test.cjs` 同形；只实现用到的那些方法） ── */
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
    this.checked = false;
    this.files = null;
    this.type = "";
    this.placeholder = "";
    this.accept = "";
    this.id = "";
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
    if (Object.prototype.hasOwnProperty.call(this.attributes, name)) return this.attributes[name];
    // 真 DOM 里 `dataset.x = …` 与 `setAttribute('data-x', …)` 是同一件事：替身双向读。
    if (name.startsWith("data-")) {
      const key = name
        .slice("data-".length)
        .replace(/-([a-z])/g, (_whole, letter) => letter.toUpperCase());
      return this.dataset[key] === undefined ? null : String(this.dataset[key]);
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

  contains(node) {
    return this.descendants().indexOf(node) >= 0;
  }

  focus() {
    this.focused = true;
  }
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

/** 让挂起的 `await` 链跑完（打开详情是「读配置 → 铺页 → 读两条清单」数拍；逐条导入是
    每个候选一次 POST + 一次重读，因此这里留够拍数）。 */
async function settle() {
  for (let index = 0; index < 40; index += 1) await flush();
}

/** `index.html` 里带 `hidden` 的元素：替身节点的初始可见性从**同一个正本**读。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

function channel(id, name) {
  return { id, name, last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

/** 一个 Agent 配置快照（形状与 `GET /api/agents/{id}` 的 `agent` 一致）。 */
function agentRow(id, name) {
  return {
    id,
    kind: "agent",
    display_name: name,
    avatar_slot: 1,
    role: "工程师",
    model_id: "ollama:qwen2.5",
    system_prompt: "只回答一句话。",
    enabled: true,
    auto_claim: false,
    context_window_messages: null,
    context_trigger_ratio: null,
    react_max_iters: null,
    web_access: null,
    provider: "ollama",
    is_remote: false,
    remote_consent: false,
    status: "online",
    enabled_reason: "",
    stats: { channel_count: 1, round_count: 2, completed_task_count: 0, success_rate: null },
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
      scope_kind: "agent",
      scope_id: "mem-1",
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
      scope_kind: "agent",
      scope_id: "mem-1",
    },
    overrides || {},
  );
}

/** `GET /api/skills/candidates` 的一条候选（形状与后端一致）；`path` 默认跟随 `name`。 */
function candidatePayload(overrides) {
  const base = { name: "review" };
  const name = (overrides && overrides.name) || base.name;
  return Object.assign(
    {
      name,
      description: "在提交前做一次结构化自审",
      path: "/Users/me/.agents/skills/" + name,
      importable: true,
      reason: null,
      installed: false,
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
  const documentListeners = new Map();
  const member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const agents = config.agents || [agentRow("mem-1", "小明"), agentRow("mem-2", "小红"), agentRow("mem-3", "小刚")];
  const roster = config.roster || {
    "mem-1": { mcp: 1, skills: 1 },
    "mem-2": { mcp: 2, skills: 0 },
  };
  const rosterFail = new Set(config.rosterFail || []);
  const mcpServers = (config.mcp || [mcpPayload()]).map((server) => Object.assign({}, server));
  const skills = (config.skills || [skillPayload()]).map((skill) => Object.assign({}, skill));
  const candidates =
    config.candidates === undefined
      ? [
          candidatePayload({ name: "review" }),
          candidatePayload({
            name: "t18-escape",
            description: "",
            path: "/Users/me/.agents/skills/t18-escape",
            importable: false,
            reason: "技能根目录缺少 SKILL.md",
          }),
          candidatePayload({ name: "installed-one", installed: true }),
        ]
      : config.candidates.map((entry) => Object.assign({}, entry));
  const candidateSource = config.candidateSource || "/Users/me/.agents/skills";
  const candidatesAvailable = config.candidatesAvailable !== false;

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

  function agentScope(id) {
    return "/api/mcp-servers?scope_kind=agent&scope_id=" + id;
  }

  function skillScope(id) {
    return "/api/skills?scope_kind=agent&scope_id=" + id;
  }

  function candidateScope(id) {
    return "/api/skills/candidates?scope_kind=agent&scope_id=" + id;
  }

  function defaultFetch(url, request) {
    const method = request.method || "GET";
    if (url === "/api/agents" && method === "GET") {
      return jsonResponse({
        agents,
        members: [member, ...agents.map((item) => ({ id: item.id, kind: "agent", display_name: item.display_name }))],
      });
    }
    if (/^\/api\/agents\/[^/]+\/global-mcp-servers$/.test(url) && method === "GET") {
      return jsonResponse({ global_mcp_servers: [] });
    }
    if (/^\/api\/agents\/[^/]+$/.test(url) && method === "GET") {
      const id = url.split("/").pop();
      return jsonResponse({ agent: agents.find((item) => item.id === id) || agentRow(id, id) });
    }
    if (url === "/api/channels/ch-1/members") {
      return jsonResponse({
        members: [member, ...agents.map((item) => ({ id: item.id, kind: "agent", display_name: item.display_name }))],
      });
    }
    if (url === "/api/channels/ch-1/config") {
      return jsonResponse({
        config: {
          channel_id: "ch-1",
          mode: "autonomous",
          team_scope: "channel+task",
          team_leader_member_id: null,
          leader_available: false,
          leader_reason: "",
          workspace_root: "/tmp/issue273-project",
          workspace_backend: "local",
          permission_mode: "ask-writes",
        },
      });
    }
    // 当前详情页的成员：两条真实清单（行 id 与用例一致）。
    if (url === agentScope("mem-1") && method === "GET") {
      return jsonResponse({ mcp_servers: mcpServers });
    }
    if (url === skillScope("mem-1") && method === "GET") {
      return jsonResponse({ skills });
    }
    if (url === candidateScope("mem-1") && method === "GET") {
      return jsonResponse({
        source: candidateSource,
        available: candidatesAvailable,
        candidates,
      });
    }
    // 名册摘要：按成员各回一份计数；被标记失败的成员回 500（只影响它那一行的摘要）。
    for (const id of Object.keys(roster)) {
      if (url === agentScope(id) && method === "GET") {
        if (rosterFail.has(id)) return errorResponse(500, "后端读取失败");
        const counts = roster[id];
        return jsonResponse({
          mcp_servers: Array.from({ length: counts.mcp }, (_item, index) => mcpPayload({ id: id + "-mcp" + index })),
        });
      }
      if (url === skillScope(id) && method === "GET") {
        if (rosterFail.has(id)) return errorResponse(500, "后端读取失败");
        const counts = roster[id];
        return jsonResponse({
          skills: Array.from({ length: counts.skills }, (_item, index) => skillPayload({ id: id + "-sk" + index })),
        });
      }
    }
    if (url === "/api/mcp-servers" && method === "POST") {
      if (config.copyConflict) return errorResponse(409, "同名 MCP 声明已存在");
      const created = mcpPayload(Object.assign({ id: "mcp_new" }, request.body || {}));
      return jsonResponse({ mcp_server: created }, 201);
    }
    if (/^\/api\/skills\/[^/]+\/copy$/.test(url) && method === "POST") {
      if (config.copyConflict) return errorResponse(409, "同名技能已存在");
      const skill = skillPayload({ id: "sk_new" });
      return jsonResponse({ skill }, 201);
    }
    if (url === "/api/skills" && method === "POST") {
      const body =
        typeof request.body === "string" ? JSON.parse(request.body || "{}") : request.body || {};
      const name = String(body.source_path || "").split("/").pop();
      if ((config.importConflict || []).includes(name)) {
        return errorResponse(409, "同一作用域内已存在同名 Skill：" + name);
      }
      if ((config.importFail || []).includes(name)) {
        return errorResponse(400, "技能根目录缺少 SKILL.md");
      }
      return jsonResponse({ skill: skillPayload({ id: "sk_import_" + name, name }) }, 201);
    }
    return jsonResponse({});
  }

  function fetchStub(url, init) {
    const request = init || {};
    let parsedBody = null;
    if (typeof request.body === "string" && request.body.length) {
      try {
        parsedBody = JSON.parse(request.body);
      } catch {
        parsedBody = request.body;
      }
    }
    calls.push({
      url: String(url),
      method: request.method || "GET",
      body: parsedBody,
      rawBody: typeof request.body === "string" ? null : request.body,
    });
    if (config.fetch) {
      const custom = config.fetch(String(url), request);
      if (custom) return custom;
    }
    return defaultFetch(String(url), request);
  }

  const BUTTON_IDS = new Set(["#adSave", "#adDiscard", "#channelConfigOverlay"]);
  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = new FakeNode(BUTTON_IDS.has(selector) ? "button" : "div");
      node.hidden = HIDDEN_IN_HTML.has(selector);
      if (selector.startsWith("#")) node.id = selector.slice(1);
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

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
      for (const listener of documentListeners.get(event.type) || []) await listener.call(document, event);
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
      "\nglobalThis.__capabilityTest = { state, bindEvents, render, renderMembers, t,"
        + " openAgentDetail, closeAgentDetail, renderAgentDetail, setViewMode,"
        + " openChannelConfigModal, closeChannelConfigModal, openRosterModal, closeRosterModal,"
        + " renderRoster };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__capabilityTest;
  api.state.member = member;
  api.state.members = [member, ...agents.map((item) => ({ id: item.id, kind: "agent", display_name: item.display_name }))];
  api.state.agents = agents;
  api.state.channels = [channel("ch-1", "设计")];
  api.state.dms = [];
  api.state.tasks = [];
  api.state.activeChannelId = "ch-1";
  api.state.online = true;
  api.state.ready = true;
  api.state.mode = "channel";
  api.setViewMode("channel");
  api.bindEvents();

  const harness = {
    api,
    calls,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    agentBody: elementFor("#adBody"),
    channelBody: elementFor("#channelConfigBody"),
    channelOverlay: elementFor("#channelConfigOverlay"),
    rosterBody: elementFor("#rosterBody"),
    rosterOverlay: elementFor("#rosterOverlay"),
    posts() {
      return calls.filter((call) => call.method === "POST");
    },
    patches() {
      return calls.filter((call) => call.method === "PATCH");
    },
    /** 打开某个成员的详情（走真实入口路径：读配置 → 铺页 → 读两条能力清单）。 */
    async openAgent(id) {
      await harness.api.openAgentDetail(id);
      await settle();
    },
    /** 点详情页里的控件（监听挂在 `#adBody` 上，因此带真实 target 派发）。 */
    async clickInBody(node) {
      assert.ok(node, "要点的控件应当存在");
      await harness.agentBody.dispatchEvent({ type: "click", target: node });
      await settle();
    },
    /** 改详情页里的一个输入控件（文本 / 勾选）。 */
    async changeInBody(node, value) {
      assert.ok(node, "要改的控件应当存在");
      if (node.type === "checkbox") node.checked = Boolean(value);
      else node.value = value;
      const type = node.type === "checkbox" ? "change" : "input";
      await harness.agentBody.dispatchEvent({ type, target: node });
      await settle();
    },
    async openChannelConfig() {
      await harness.api.openChannelConfigModal();
      await settle();
    },
    async openRoster() {
      harness.api.openRosterModal();
      await settle();
    },
  };
  return harness;
}

/* ─── 定位工具（替身选择器不支持带值的属性选择器，因此手写这几条） ────────── */

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

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
    descendants(root).find((node) => node.getAttribute && node.getAttribute("data-od-id") === id) || null
  );
}

function byClass(root, name) {
  return descendants(root).filter((node) => node.classList && node.classList.contains(name));
}

function catalogText(harness, locale, key, params) {
  const value = harness.catalogs[locale][key];
  assert.equal(typeof value, "string", `${locale} 缺少词条：${key}`);
  if (!params) return value;
  return value.replace(/\{(\w+)\}/g, (_whole, name) => String(params[name]));
}

/* ─── 1. Agent 详情：两 kind 同屏，作用域固定为该成员 ────────────────────── */

test("Agent 详情的能力段：两种能力同屏，请求固定带 agent scope", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  assert.ok(byOdId(harness.agentBody, "agent-capabilities-section"), "能力段应当在详情页里");
  assert.ok(byOdId(harness.agentBody, "agent-capabilities-mcp"), "MCP 小节标题应当在");
  assert.ok(byOdId(harness.agentBody, "agent-capabilities-skill"), "技能小节标题应当在");

  // 两条清单都按 agent scope 读取（没有频道 scope 的读取路径）。
  const reads = harness.calls.map((call) => call.url);
  assert.ok(reads.includes("/api/mcp-servers?scope_kind=agent&scope_id=mem-1"), "MCP 读数按成员读");
  assert.ok(reads.includes("/api/skills?scope_kind=agent&scope_id=mem-1"), "技能读数按成员读");
  assert.deepEqual(
    reads.filter((url) => url.includes("scope_kind=channel")),
    [],
    "能力配置不再有频道 scope 的读取",
  );

  // 两种行同屏，信息面与既有行组件一致。
  const mcpRow = byDataset(harness.agentBody, "mcpServer", "mcp_1");
  assert.ok(mcpRow, "MCP 行应当渲染");
  assert.equal(mcpRow.getAttribute("data-od-id"), "capability-mcp-row");
  assert.match(mcpRow.textContent, /filesystem/);
  assert.match(mcpRow.textContent, /GITHUB_TOKEN/);
  const skillRow = byDataset(harness.agentBody, "skill", "sk_1");
  assert.ok(skillRow, "技能行应当渲染");
  assert.equal(skillRow.getAttribute("data-od-id"), "capability-skill-row");
  assert.match(skillRow.textContent, /review/);
  assert.match(skillRow.textContent, /在提交前做一次结构化自审/);
  assert.match(skillRow.textContent, /0123456789ab/, "hash 前缀仍可见");

  // 每行的入口：编辑（MCP）/ 启停 + 删除（两 kind）/ 安装（技能段）/ 复制（两 kind）。
  assert.ok(byDataset(harness.agentBody, "mcpEdit", "mcp_1"), "MCP 行要有编辑入口");
  assert.ok(byDataset(harness.agentBody, "mcpToggle", "mcp_1"), "MCP 行要有启停入口");
  assert.ok(byDataset(harness.agentBody, "mcpDelete", "mcp_1"), "MCP 行要有删除入口");
  assert.ok(byDataset(harness.agentBody, "skillToggle", "sk_1"), "技能行要有启停入口");
  assert.ok(byDataset(harness.agentBody, "skillDelete", "sk_1"), "技能行要有删除入口");
  assert.ok(byOdId(harness.agentBody, "skill-install"), "技能段要有安装入口");
  assert.ok(byDataset(harness.agentBody, "copyMcp", "mcp_1"), "MCP 行要有复制入口");
  assert.ok(byDataset(harness.agentBody, "copySkill", "sk_1"), "技能行要有复制入口");

  // 影响范围一句话：只属于本 Agent，字节在家目录。
  const note = byOdId(harness.agentBody, "agent-capabilities-scope");
  assert.ok(note, "能力段顶部要有影响范围一句话");
  assert.match(note.textContent, /\.alibot\/agents\/mem-1\/skills\//);
});

test("能力段里没有 scope 选择器：作用域恒为当前成员", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  assert.equal(byDataset(harness.agentBody, "mcpScopeKind", "agent"), null, "不存在 scope 选择器");
  assert.equal(
    descendants(harness.agentBody).filter((node) => node.tagName === "select" && node.dataset.mcpField === "scope")
      .length,
    0,
    "表单里没有改 scope 的控件",
  );
});

test("MCP 编辑表单沿用既有形状，提交走 agent scope 的 PATCH / POST", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  // 编辑入口按下后出现表单，名字预填自那一行（沿用 `mcpForm` 的字段面）。
  await harness.clickInBody(byDataset(harness.agentBody, "mcpEdit", "mcp_1"));
  const form = byOdId(harness.agentBody, "mcp-form");
  assert.ok(form, "编辑时出现 MCP 表单");
  const nameInput = byOdId(harness.agentBody, "mcp-name");
  assert.equal(nameInput.value, "filesystem");
  // 变量值没有输入口（只有环境变量名）。
  assert.equal(byOdId(harness.agentBody, "mcp-env-values"), null);

  await harness.clickInBody(byOdId(harness.agentBody, "mcp-save"));
  const patch = harness.patches().find((call) => call.url === "/api/mcp-servers/mcp_1");
  assert.ok(patch, "保存编辑提交 PATCH");
  assert.equal(patch.body.env_names[0], "GITHUB_TOKEN");
  assert.equal("scope_kind" in patch.body, false, "PATCH 不带 scope（scope 是身份）");
});

test("技能本机目录安装在 agent scope 下提交，成功按后端重读", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  await harness.changeInBody(byOdId(harness.agentBody, "skill-source"), "/Users/me/skills/audit");
  const before = harness.calls.filter((call) => call.url === "/api/skills?scope_kind=agent&scope_id=mem-1").length;
  await harness.clickInBody(byOdId(harness.agentBody, "skill-install-dir"));

  const install = harness.posts().find((call) => call.url === "/api/skills");
  assert.ok(install, "本机目录安装提交 POST /api/skills");
  assert.deepEqual(install.body, {
    scope_kind: "agent",
    scope_id: "mem-1",
    source_path: "/Users/me/skills/audit",
  });
  const after = harness.calls.filter((call) => call.url === "/api/skills?scope_kind=agent&scope_id=mem-1").length;
  assert.ok(after > before, "成功后按后端重读清单");
});

/* ─── 2. 从 ~/.agents/skills 导入（#277 / ADR-0036） ─────────────────────── */

test("导入区块：读候选、默认勾选「可导入且未安装」、已安装与不可导入项禁用", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const block = byOdId(harness.agentBody, "skill-import");
  assert.ok(block, "技能段要有「从 ~/.agents/skills 导入」区块");
  assert.ok(byOdId(harness.agentBody, "skill-install"), "既有安装表单仍在（区块追加在它之后）");

  // 命令提示是 ASCII 常量，原样可复制。
  const command = byOdId(harness.agentBody, "skill-import-command");
  assert.ok(command, "要有可复制的命令提示");
  assert.equal(command.textContent, "npx skills add <owner/repo> -g -a codex -y");

  // 候选按 agent scope 读（与技能列表同一作用域）。
  assert.ok(
    harness.calls.some(
      (call) => call.url === "/api/skills/candidates?scope_kind=agent&scope_id=mem-1",
    ),
    "候选读数按成员读",
  );

  const ready = byDataset(harness.agentBody, "skillImportPick", "review");
  const blocked = byDataset(harness.agentBody, "skillImportPick", "t18-escape");
  const installed = byDataset(harness.agentBody, "skillImportPick", "installed-one");
  assert.ok(ready && blocked && installed, "三条候选各有一行");
  assert.equal(ready.checked, true, "可导入且未安装的项默认勾选");
  assert.equal(ready.disabled, false, "可导入的项可勾选");
  assert.equal(blocked.checked, false, "不可导入的项不预选");
  assert.equal(blocked.disabled, true, "不可导入的项禁用");
  assert.equal(installed.disabled, true, "已安装的项禁用");

  const row = byDataset(harness.agentBody, "skillImportCandidate", "t18-escape");
  assert.match(row.textContent, /不可导入/);
  assert.match(row.textContent, /缺少 SKILL\.md/, "不可导入的原因要渲染出来");
  assert.match(
    byDataset(harness.agentBody, "skillImportCandidate", "installed-one").textContent,
    /已安装/,
  );
});

test("导入选中：逐条 POST /api/skills（source_path = 候选 path），409 记已跳过，失败记原因", async () => {
  const harness = createHarness({ importConflict: ["installed-one"], importFail: ["t18-escape"] });
  await harness.openAgent("mem-1");

  // 全部取消勾选后「导入选中」应当是禁用的（真人也点不动），不发任何请求。
  await harness.changeInBody(byDataset(harness.agentBody, "skillImportPick", "review"), false);
  const postsBefore = harness.posts().length;
  assert.equal(
    byOdId(harness.agentBody, "skill-import-run").disabled,
    true,
    "一个都没勾时「导入选中」禁用",
  );
  await harness.clickInBody(byOdId(harness.agentBody, "skill-import-run"));
  assert.equal(
    harness.posts().slice(postsBefore).filter((call) => call.url === "/api/skills").length,
    0,
    "没勾选就不发请求",
  );

  // 重新勾上：按钮恢复可用，导入照常提交。
  await harness.changeInBody(byDataset(harness.agentBody, "skillImportPick", "review"), true);
  assert.equal(
    byOdId(harness.agentBody, "skill-import-run").disabled,
    false,
    "勾上之后「导入选中」可用",
  );
  await harness.clickInBody(byOdId(harness.agentBody, "skill-import-run"));

  const imports = harness
    .posts()
    .slice(postsBefore)
    .filter((call) => call.url === "/api/skills");
  assert.deepEqual(
    imports.map((call) => call.body.source_path),
    ["/Users/me/.agents/skills/review"],
    "只提交勾选的那一项",
  );
  assert.deepEqual(imports[0].body, {
    scope_kind: "agent",
    scope_id: "mem-1",
    source_path: "/Users/me/.agents/skills/review",
  });

  const status = byOdId(harness.agentBody, "skill-import-status");
  assert.ok(status, "逐条结果要渲染");
  assert.match(status.textContent, /review/);
  assert.match(status.textContent, /已导入/);

  // 导入结束后重读技能列表。
  const reads = harness.calls.filter(
    (call) => call.url === "/api/skills?scope_kind=agent&scope_id=mem-1",
  );
  assert.ok(reads.length >= 2, "导入结束后重读技能列表");
});

test("候选为空（来源在但没有技能）给空态指引，不渲染空列表", async () => {
  const harness = createHarness({ candidates: [] });
  await harness.openAgent("mem-1");

  const block = byOdId(harness.agentBody, "skill-import");
  assert.ok(block, "区块照常渲染");
  assert.equal(byOdId(harness.agentBody, "skill-import-candidates"), null, "空候选不渲染列表");
  assert.match(block.textContent, /先运行上面的命令/, "空态要给「先跑命令」的指引");
  assert.equal(byOdId(harness.agentBody, "skill-import-error"), null, "空态不是错误");
});

test("导入结果：409 记「已存在，已跳过」且不覆盖，其他失败记可读原因", async () => {
  const harness = createHarness({
    candidates: [candidatePayload({ name: "review" }), candidatePayload({ name: "other" })],
    importConflict: ["review"],
    importFail: ["other"],
  });
  await harness.openAgent("mem-1");

  // 两条都默认勾选（可导入且未安装）：服务端分别回 409 与 400。
  await harness.clickInBody(byOdId(harness.agentBody, "skill-import-run"));

  const posts = harness.posts().filter((call) => call.url === "/api/skills");
  assert.deepEqual(
    posts.map((call) => call.body.source_path),
    ["/Users/me/.agents/skills/review", "/Users/me/.agents/skills/other"],
    "逐条提交，顺序照候选表",
  );
  const lines = byClass(byOdId(harness.agentBody, "skill-import-status"), "import-result").map(
    (node) => node.textContent,
  );
  assert.equal(lines.length, 2, "两项各一条结果");
  assert.match(lines[0], /review：已存在，已跳过/);
  assert.match(lines[1], /other：导入失败/);
  assert.match(lines[1], /缺少 SKILL\.md/, "失败原因来自后端可读正文");
  assert.equal(
    harness.patches().filter((call) => call.url.startsWith("/api/skills/")).length,
    0,
    "冲突时不许改用 PATCH 覆盖",
  );
});

test("候选来源缺席 / 扫描失败：只落在导入区块自己的行，不拖垮技能段", async () => {
  const missing = createHarness({ candidates: [], candidatesAvailable: false });
  await missing.openAgent("mem-1");
  const block = byOdId(missing.agentBody, "skill-import");
  assert.match(block.textContent, /先运行上面的命令/, "来源缺席时给「先跑命令」的指引");
  assert.ok(byOdId(missing.agentBody, "skill-install"), "既有安装表单照常渲染");
  assert.equal(byOdId(missing.agentBody, "skill-error"), null, "缺席不是错误");

  const broken = createHarness({
    fetch: (url) =>
      url.startsWith("/api/skills/candidates")
        ? errorResponse(500, "后端读取失败")
        : null,
  });
  await broken.openAgent("mem-1");
  assert.ok(byOdId(broken.agentBody, "skill-import-error"), "扫描失败落在导入区块自己的错误行");
  assert.equal(byOdId(broken.agentBody, "skill-error"), null, "不牵连技能列表的错误行");
  assert.ok(byOdId(broken.agentBody, "skill-install"), "技能段其余部分照常");
});

/* ─── 3. 复制到其他成员 ─────────────────────────────────────────────────── */

test("复制入口列出其他成员（排除当前成员），选目标后逐条提交", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  await harness.clickInBody(byDataset(harness.agentBody, "copyMcp", "mcp_1"));
  const dialog = byOdId(harness.agentBody, "capability-copy-dialog");
  assert.ok(dialog, "按下复制后出现多选弹层");
  assert.ok(byDataset(harness.agentBody, "copyTarget", "mem-2"), "其他成员可选");
  assert.ok(byDataset(harness.agentBody, "copyTarget", "mem-3"), "其他成员可选");
  assert.equal(byDataset(harness.agentBody, "copyTarget", "mem-1"), null, "当前成员不在目标清单里");

  // 没选目标就确认：不发请求，给可读提示。
  await harness.clickInBody(byOdId(harness.agentBody, "capability-copy-confirm"));
  assert.equal(harness.posts().length, 0, "没选目标不发请求");
  assert.ok(byOdId(harness.agentBody, "capability-copy-error"), "要提示先选目标");

  const target = byDataset(harness.agentBody, "copyTarget", "mem-2");
  await harness.changeInBody(target, true);
  await harness.clickInBody(byOdId(harness.agentBody, "capability-copy-confirm"));

  const copy = harness.posts().find((call) => call.url === "/api/mcp-servers");
  assert.ok(copy, "MCP 复制用既有 POST /api/mcp-servers");
  assert.equal(copy.body.scope_kind, "agent");
  assert.equal(copy.body.scope_id, "mem-2", "复制目标就是选中的成员");
  assert.equal(copy.body.name, "filesystem");
  assert.deepEqual(copy.body.env_names, ["GITHUB_TOKEN"], "只复制变量名");
  const results = byOdId(harness.agentBody, "capability-copy-results");
  assert.ok(results, "结果逐条回显");
  assert.match(results.textContent, /小红/);
  assert.match(results.textContent, /已复制/);
});

test("目标已存在同名时逐条提示已跳过，且不覆盖（没有 PATCH、没有改名写入）", async () => {
  const harness = createHarness({ copyConflict: true });
  await harness.openAgent("mem-1");

  await harness.clickInBody(byDataset(harness.agentBody, "copySkill", "sk_1"));
  await harness.changeInBody(byDataset(harness.agentBody, "copyTarget", "mem-2"), true);
  await harness.changeInBody(byDataset(harness.agentBody, "copyTarget", "mem-3"), true);
  await harness.clickInBody(byOdId(harness.agentBody, "capability-copy-confirm"));

  const copies = harness.posts().filter((call) => call.url === "/api/skills/sk_1/copy");
  assert.deepEqual(copies.map((call) => call.body.scope_id), ["mem-2", "mem-3"], "逐目标复制");
  const results = byOdId(harness.agentBody, "capability-copy-results");
  const lines = byClass(results, "copy-result").map((node) => node.textContent);
  assert.equal(lines.length, 2, "两个目标各一条结果");
  assert.match(lines[0], /小红/);
  assert.match(lines[0], /已跳过/);
  assert.match(lines[1], /小刚/);
  assert.match(lines[1], /已跳过/);
  assert.equal(
    harness.patches().filter((call) => call.url.startsWith("/api/skills/")).length,
    0,
    "冲突时不许改用 PATCH 覆盖",
  );
  assert.equal(harness.posts().filter((call) => call.url === "/api/skills").length, 0, "冲突时不再装上第二份");
});

/* ─── 3. 频道配置：两段已删 ─────────────────────────────────────────────── */

test("频道配置里不再出现 MCP / 技能段，也不再按频道 scope 读两份清单", async () => {
  const harness = createHarness();
  await harness.openChannelConfig();

  assert.equal(byOdId(harness.channelBody, "channel-config-section-mcp"), null, "MCP 段已从频道配置移除");
  assert.equal(byOdId(harness.channelBody, "channel-config-section-skill"), null, "技能段已从频道配置移除");
  assert.equal(byOdId(harness.channelBody, "mcp-list"), null);
  assert.equal(byOdId(harness.channelBody, "mcp-form"), null);
  assert.equal(byOdId(harness.channelBody, "skill-list"), null);
  assert.equal(byOdId(harness.channelBody, "skill-install"), null);
  assert.deepEqual(
    descendants(harness.channelBody).filter((node) => node.dataset && node.dataset.mcpServer !== undefined),
    [],
    "频道配置里没有任何 MCP 行",
  );
  assert.deepEqual(
    harness.calls.filter((call) => call.url.includes("scope_kind=channel")).map((call) => call.url),
    [],
    "打开频道配置不再按频道 scope 读 MCP / 技能",
  );

  // 其余频道配置仍在（Workspace 那一段没有被顺手删掉）。
  assert.ok(byOdId(harness.channelBody, "workspace-mgmt-root"), "Workspace 段仍在");
  assert.equal(byOdId(harness.channelBody, "channel-config-section-mode").textContent !== "", true);
});

/* ─── 4. 名册：能力摘要（只读） ─────────────────────────────────────────── */

test("名册的 Agent 行显示 MCP n / 技能 n，读不到时只让那一行显示占位", async () => {
  const harness = createHarness({ rosterFail: ["mem-2"] });
  await harness.openRoster();

  const row1 = byOdId(harness.rosterBody, "roster-capabilities-mem-1");
  assert.ok(row1, "Agent 行要有能力摘要");
  assert.equal(row1.textContent, catalogText(harness, "zh-CN", "roster.capabilities", { mcp: 1, skills: 1 }));

  const row2 = byOdId(harness.rosterBody, "roster-capabilities-mem-2");
  assert.ok(row2, "失败的成员也照样列出");
  assert.equal(row2.textContent, catalogText(harness, "zh-CN", "roster.capabilitiesUnavailable"));

  // 名册本身没被拖垮：三行都在，顺序照后端成员表。
  const rows = byClass(harness.rosterBody, "roster-row").map((row) => row.dataset.member);
  assert.deepEqual(rows, ["owner", "mem-1", "mem-2", "mem-3"]);
});

/* ─── 5. 词条 ───────────────────────────────────────────────────────────── */

test("新增文案在两份词条里同键同参数位", () => {
  const harness = createHarness();
  const keys = [
    "agentCapabilities.title",
    "agentCapabilities.scopeNote",
    "agentCapabilities.mcpTitle",
    "agentCapabilities.skillTitle",
    "agentCapabilities.copyTo",
    "agentCapabilities.copyTitle",
    "agentCapabilities.copyHint",
    "agentCapabilities.copyConfirm",
    "agentCapabilities.copyCancel",
    "agentCapabilities.copyNoTargets",
    "agentCapabilities.copyPickRequired",
    "agentCapabilities.copyBusy",
    "agentCapabilities.copyResultCopied",
    "agentCapabilities.copyResultSkipped",
    "agentCapabilities.copyResultFailed",
    "roster.capabilities",
    "roster.capabilitiesLoading",
    "roster.capabilitiesUnavailable",
    "skill.importTitle",
    "skill.importCommandHint",
    "skill.importScanButton",
    "skill.importRunButton",
    "skill.importLoading",
    "skill.importScanFailed",
    "skill.importUnavailable",
    "skill.importEmpty",
    "skill.importStatusReady",
    "skill.importStatusInstalled",
    "skill.importStatusBlocked",
    "skill.importBusy",
    "skill.importResultDone",
    "skill.importResultSkipped",
    "skill.importResultFailed",
    "skill.importPickRequired",
    "skill.importSummary",
  ];
  for (const key of keys) {
    for (const locale of ["zh-CN", "en"]) {
      assert.equal(typeof harness.catalogs[locale][key], "string", `${locale} 缺少词条：${key}`);
      assert.ok(harness.catalogs[locale][key].length > 0, `${locale} 的 ${key} 不能为空`);
    }
  }
  assert.equal(
    catalogText(harness, "zh-CN", "agentCapabilities.scopeNote", { member: "m1" }).includes("m1"),
    true,
    "scopeNote 的 {member} 参数位要能用",
  );
});
