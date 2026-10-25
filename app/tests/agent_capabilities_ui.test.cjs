/**
 * Studio 的**能力配置**（#273）：Agent 详情里的 MCP / 技能两段 + 名册摘要，
 * 以及「频道配置里不再有这两段」。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `agent_detail_ui.test.cjs` /
 * `channel_resources_ui.test.cjs` 同款），不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 打开 Agent 详情：能力段按 `scope_kind=agent&scope_id=<member>` 读两条清单，MCP 行与技能行
 *    各在自己的页签里；每行都带编辑 / 启停 / 删除入口，没有 scope 选择器；
 * 2. 复制到其他成员这条通道整个撤除（#281 / D6）：两 kind 的入口、弹层、呈现状态与函数都不在
 *    正本里（`data-copy-*` 零命中），`POST /api/skills/{id}/copy` 端点仍由 pytest 契约守着；
 * 3. skills 页签的安装面只剩**一个**「从本机导入」下拉框（#282 / D3 / D4）：候选按 可选 / 已安装 /
 *    不可导入 三组呈现、默认不预选、选中一项发**一次** POST /api/skills；手填路径与上传 zip 两条
 *    入口连同只服务它们的呈现状态一并撤除（`skill-source` / `skill-install-dir` / `skill-zip` 零命中）；
 * 4. 频道配置里不再出现 MCP / 技能段，也不再按频道 scope 读这两条清单（Workspace 段仍在）；
 * 5. 名册的 Agent 行显示 `MCP n / 技能 n`；单成员读取失败只让那一个摘要显示占位，名册照常列出；
 * 6. 新增文案在两份词条表里都取到值，撤除的那些键必须在两份表里都消失。
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
      const created = mcpPayload(Object.assign({ id: "mcp_new" }, request.body || {}));
      return jsonResponse({ mcp_server: created }, 201);
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

  // 页签与面板在 `index.html` 里是静态骨架：按钮在 `#adTabs` 里、面板在 `#adBody` 里。
  // 替身按同一份结构把它们的 `id` 接上（「找得到」与「点得到」都靠这层父子关系）。
  const adTabs = elementFor("#adTabs");
  for (const name of ["base", "skills", "mcp"]) {
    const suffix = name.charAt(0).toUpperCase() + name.slice(1);
    const tab = elementFor("#adTab" + suffix);
    tab.tagName = "button";
    tab.dataset.adtab = name;
    tab.id = "adTab" + suffix;
    adTabs.appendChild(tab);
    const panel = elementFor("#adPanel" + suffix);
    panel.dataset.adpanel = name;
    panel.id = "adPanel" + suffix;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", "adTab" + suffix);
    tab.setAttribute("aria-controls", "adPanel" + suffix);
    elementFor("#adBody").appendChild(panel);
  }
  adTabs.setAttribute("role", "tablist");
  adTabs.className = "ad-tabs";

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
    /** 某个页签的面板（`#adPanelBase` / `#adPanelSkills` / `#adPanelMcp`）：它是 `index.html` 的
        静态骨架，因此与 `#adBody` 一样按选择器取替身节点。 */
    panel(name) {
      return elementFor("#adPanel" + name.charAt(0).toUpperCase() + name.slice(1));
    },
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

function readStudio(file) {
  return fs.readFileSync(path.join(STUDIO_DIR, file), "utf8");
}

/** 注释不算代码：扫「某符号是否还在」以前先剥两种注释（与 `i18n_ui.test.cjs` 同款）。 */
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

function catalogText(harness, locale, key, params) {
  const value = harness.catalogs[locale][key];
  assert.equal(typeof value, "string", `${locale} 缺少词条：${key}`);
  if (!params) return value;
  return value.replace(/\{(\w+)\}/g, (_whole, name) => String(params[name]));
}

/* ─── 1. Agent 详情：两 kind 各归自己的页签，作用域固定为该成员 ──────────── */

test("skills 页签：左栏技能列表与技能行入口，请求固定带 agent scope", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const panel = harness.panel("skills");
  assert.ok(panel, "技能列表应在 skills 页签里");
  assert.ok(byOdId(panel, "agent-capabilities-scope"), "skills 页签顶部要有影响范围一句话");

  // 两条清单都按 agent scope 读取（没有频道 scope 的读取路径）。
  const reads = harness.calls.map((call) => call.url);
  assert.ok(reads.includes("/api/mcp-servers?scope_kind=agent&scope_id=mem-1"), "MCP 读数按成员读");
  assert.ok(reads.includes("/api/skills?scope_kind=agent&scope_id=mem-1"), "技能读数按成员读");
  assert.deepEqual(
    reads.filter((url) => url.includes("scope_kind=channel")),
    [],
    "能力配置不再有频道 scope 的读取",
  );

  // 技能行在 skills 页签里，信息面与既有行组件一致。
  const skillRow = byDataset(panel, "skill", "sk_1");
  assert.ok(skillRow, "技能行应当在 skills 页签里渲染");
  assert.equal(skillRow.getAttribute("data-od-id"), "capability-skill-row");
  assert.match(skillRow.textContent, /review/);
  assert.match(skillRow.textContent, /在提交前做一次结构化自审/);
  assert.match(skillRow.textContent, /0123456789ab/, "hash 前缀仍可见");
  // 技能行的入口：启停 + 删除 + 安装（技能段）。复制入口已随弹层撤除（#281 / D6），
  // 反向断言在下面的「复制入口与弹层撤除」用例里。
  assert.ok(byDataset(panel, "skillToggle", "sk_1"), "技能行要有启停入口");
  assert.ok(byDataset(panel, "skillDelete", "sk_1"), "技能行要有删除入口");
  // 安装入口只有一个：从本机导入的下拉框（#282 / D3）。
  assert.ok(byOdId(panel, "skill-import-picker"), "skills 页签的安装入口是「从本机导入」下拉框");

  // 影响范围一句话：只属于本 Agent，字节在家目录。
  const note = byOdId(panel, "agent-capabilities-scope");
  assert.match(note.textContent, /\.alibot\/agents\/mem-1\/skills\//);
});

test("mcp 页签：库行列表、表单与行入口，请求固定带 agent scope", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const panel = harness.panel("mcp");
  assert.ok(panel, "MCP 声明块应在 mcp 页签里");

  // 库行列表与表单在 mcp 页签里，信息面与既有行组件一致。
  const mcpRow = byDataset(panel, "mcpServer", "mcp_1");
  assert.ok(mcpRow, "MCP 行应当在 mcp 页签里渲染");
  assert.equal(mcpRow.getAttribute("data-od-id"), "capability-mcp-row");
  assert.match(mcpRow.textContent, /filesystem/);
  assert.match(mcpRow.textContent, /GITHUB_TOKEN/);
  assert.ok(byOdId(panel, "mcp-form"), "库行表单在 mcp 页签里");

  assert.ok(byDataset(panel, "mcpEdit", "mcp_1"), "MCP 行要有编辑入口");
  assert.ok(byDataset(panel, "mcpToggle", "mcp_1"), "MCP 行要有启停入口");
  assert.ok(byDataset(panel, "mcpDelete", "mcp_1"), "MCP 行要有删除入口");
});

test("两个页签各读自己那一份：MCP 读数只在 mcp 页签、技能读数只在 skills 页签", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const skills = harness.panel("skills");
  const mcp = harness.panel("mcp");
  assert.ok(skills && mcp, "两个页签面板都要在");

  assert.ok(byDataset(skills, "skill", "sk_1"), "技能行在 skills 页签里");
  assert.equal(byDataset(skills, "mcpServer", "mcp_1"), null, "skills 页签里不出现 MCP 行");
  assert.equal(byOdId(skills, "mcp-form"), null, "skills 页签里不出现 MCP 表单");

  assert.ok(byDataset(mcp, "mcpServer", "mcp_1"), "MCP 行在 mcp 页签里");
  assert.equal(byDataset(mcp, "skill", "sk_1"), null, "mcp 页签里不出现技能行");
  assert.equal(byOdId(mcp, "skill-import-picker"), null, "mcp 页签里不出现导入下拉框");

  // 基础信息和配置页签只管配置，两条能力清单都不在那里。
  const base = harness.panel("base");
  assert.ok(base, "基础信息和配置页签也要在");
  assert.equal(byOdId(base, "skill-list"), null);
  assert.equal(byOdId(base, "mcp-list"), null);
});

test("复制入口与弹层撤除：正本里 data-copy-* 零命中，运行时也没有弹层节点", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  // 运行时：MCP 行与技能行都不再可能点出弹层。
  assert.equal(byDataset(harness.agentBody, "copyMcp", "mcp_1"), null, "MCP 行不该再有复制入口");
  assert.equal(byDataset(harness.agentBody, "copySkill", "sk_1"), null, "技能行不该再有复制入口");
  assert.equal(byOdId(harness.agentBody, "capability-copy-dialog"), null, "弹层节点不再渲染");
  assert.equal(byOdId(harness.agentBody, "capability-copy-targets"), null, "目标多选也不再渲染");
  assert.equal(byOdId(harness.agentBody, "capability-copy-confirm"), null, "确认按钮不再渲染");

  // 正本：两个 kind 的入口标识与弹层标识一处都不剩。
  const studio = ["app.js", "index.html", "app.css"].map(readStudio).join("\n");
  for (const needle of [
    "data-copy-mcp",
    "data-copy-skill",
    "data-copy-target",
    "data-copy-confirm",
    "data-copy-cancel",
    "capability-copy",
  ]) {
    assert.equal(studio.includes(needle), false, `正本里不该再出现 ${needle}`);
  }

  // 只为弹层存在的函数与呈现状态（含只被它调用的两个辅助函数）也不留。
  const appSource = stripComments(readStudio("app.js"));
  for (const symbol of [
    "capabilityCopyNode",
    "openCapabilityCopy",
    "closeCapabilityCopy",
    "toggleCapabilityCopyTarget",
    "submitCapabilityCopy",
    "copyResultText",
    "capabilityMemberName",
    "view.copy",
    "state.copy",
  ]) {
    assert.equal(
      new RegExp(`\\b${symbol.replace(".", "\\.")}\\b`).test(appSource),
      false,
      `app.js 里不该再有 ${symbol}`,
    );
  }
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

test("两条既有安装入口撤除：正本里 skill-source / skill-install-dir / skill-zip 三处零命中", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  // 运行时：安装面只剩一个下拉框，既没有手填路径的文本框，也没有文件选择控件。
  assert.equal(byOdId(harness.agentBody, "skill-source"), null, "不再有手填路径的输入框");
  assert.equal(byOdId(harness.agentBody, "skill-install-dir"), null, "不再有「安装目录」按钮");
  assert.equal(byOdId(harness.agentBody, "skill-zip"), null, "不再有 zip 文件框");
  assert.deepEqual(
    descendants(harness.panel("skills")).filter(
      (node) => node.tagName === "input" && node.type === "file",
    ),
    [],
    "skills 页签里没有文件选择控件",
  );

  // 正本：三处标识与其专属函数 / 呈现状态一处都不剩。
  const studio = ["app.js", "index.html", "app.css"].map(readStudio).join("\n");
  for (const needle of [
    "skill-source",
    "skill-install-dir",
    "skill-zip",
    "data-skill-source",
    "data-skill-zip",
    "skillSourcePath",
    "submitSkillDirectory",
    "uploadSkillZip",
  ]) {
    assert.equal(studio.includes(needle), false, `正本里不该再出现 ${needle}`);
  }
});

/* ─── 2. 从 ~/.agents/skills 导入（#277 / ADR-0036；#282 收成一个下拉框） ──────── */

/** 下拉框里的一个候选选项（`<option value="<name>">`）。 */
function option(picker, name) {
  return (
    descendants(picker).find((node) => node.tagName === "option" && node.value === name) || null
  );
}

/** 下拉框里的分组（`<optgroup>`），按渲染顺序。 */
function groups(picker) {
  return descendants(picker).filter((node) => node.tagName === "optgroup");
}

test("导入下拉框：候选按 可选 / 已安装 / 不可导入 三组呈现，不可选项禁用且带原因，默认不预选", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");
  const panel = harness.panel("skills");

  const block = byOdId(panel, "skill-import");
  assert.ok(block, "skills 页签要有「从本机导入」区块");

  // 命令提示是 ASCII 常量，原样可复制（来源仍是 ~/.agents/skills）。
  const command = byOdId(panel, "skill-import-command");
  assert.ok(command, "要有可复制的命令提示");
  assert.equal(command.textContent, "npx skills add <owner/repo> -g -a codex -y");

  // 候选按 agent scope 读（与技能列表同一作用域）。
  assert.ok(
    harness.calls.some(
      (call) => call.url === "/api/skills/candidates?scope_kind=agent&scope_id=mem-1",
    ),
    "候选读数按成员读",
  );

  // 入口形态：一个下拉框（`<select>`），不是弹窗、也不是多选勾选列表。
  const picker = byOdId(panel, "skill-import-picker");
  assert.ok(picker, "安装入口是一个下拉框");
  assert.equal(picker.tagName, "select");
  assert.equal(
    descendants(panel).filter((node) => node.tagName === "input" && node.type === "checkbox").length,
    0,
    "候选不是一个多选勾选列表",
  );

  // 三组按 可选 / 已安装 / 不可导入 的顺序呈现。
  assert.deepEqual(
    groups(picker).map((group) => group.getAttribute("label")),
    [
      catalogText(harness, "zh-CN", "skill.importGroupReady"),
      catalogText(harness, "zh-CN", "skill.importGroupInstalled"),
      catalogText(harness, "zh-CN", "skill.importGroupBlocked"),
    ],
    "候选按 可选 / 已安装 / 不可导入 分组",
  );

  const ready = option(picker, "review");
  const blocked = option(picker, "t18-escape");
  const installed = option(picker, "installed-one");
  assert.ok(ready && blocked && installed, "三条候选各有一个选项");
  assert.equal(ready.disabled, false, "可选的项可选");
  assert.equal(installed.disabled, true, "已安装的项禁用");
  assert.equal(blocked.disabled, true, "不可导入的项禁用");
  assert.match(blocked.textContent, /缺少 SKILL\.md/, "不可导入的原因写进选项文本");

  // 默认不预选：空选项的文案是 `skill.importPickPlaceholder`。
  assert.equal(picker.value, "", "默认不预选任何候选");
  assert.equal(
    option(picker, "").textContent,
    catalogText(harness, "zh-CN", "skill.importPickPlaceholder"),
    "空选项文案",
  );
});

test("导入：选中一项后发一次 POST /api/skills（source_path = 候选 path），结束后重扫候选并回到空选择", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");
  const panel = harness.panel("skills");

  const scansBefore = harness.calls.filter((call) => call.url.startsWith("/api/skills/candidates"))
    .length;
  const readsBefore = harness.calls.filter((call) => call.url === "/api/skills?scope_kind=agent&scope_id=mem-1")
    .length;

  const picker = byOdId(panel, "skill-import-picker");
  picker.value = "review";
  await harness.agentBody.dispatchEvent({ type: "change", target: picker });
  await settle();

  const imports = harness.posts().filter((call) => call.url === "/api/skills");
  assert.equal(imports.length, 1, "一次只导入一个技能：只发一次 POST（候选共三条，可选一条）");
  assert.deepEqual(imports[0].body, {
    scope_kind: "agent",
    scope_id: "mem-1",
    source_path: "/Users/me/.agents/skills/review",
  });

  // 结果按条归因（成功）以及后续刷新：候选重扫 + 技能列表重读 + 下拉框回到空选择。
  const status = byDataset(harness.panel("skills"), "skillImportStatus", "1");
  assert.ok(status, "导入结果要渲染在导入区自己的结果行");
  assert.match(status.textContent, /review/);
  assert.match(status.textContent, /已导入/);
  assert.ok(
    harness.calls.filter((call) => call.url.startsWith("/api/skills/candidates")).length > scansBefore,
    "导入结束后重扫候选",
  );
  assert.ok(
    harness.calls.filter((call) => call.url === "/api/skills?scope_kind=agent&scope_id=mem-1").length >
      readsBefore,
    "导入结束后重读技能列表",
  );
  assert.equal(
    byOdId(harness.panel("skills"), "skill-import-picker").value,
    "",
    "导入结束后下拉框回到空选择",
  );
});

test("导入归因：409 记「已安装，已跳过」，其他失败记后端可读原因并留在导入区自己的错误行", async () => {
  const harness = createHarness({
    candidates: [candidatePayload({ name: "review" }), candidatePayload({ name: "other" })],
    importConflict: ["review"],
    importFail: ["other"],
  });
  await harness.openAgent("mem-1");

  // 同名（409）：不是静默失败，给出「已安装，已跳过」。
  let picker = byOdId(harness.panel("skills"), "skill-import-picker");
  picker.value = "review";
  await harness.agentBody.dispatchEvent({ type: "change", target: picker });
  await settle();

  const skipped = byDataset(harness.panel("skills"), "skillImportStatus", "1");
  assert.ok(skipped, "409 要有一条结果行");
  assert.match(skipped.textContent, /review：已安装，已跳过/);
  assert.equal(byOdId(harness.panel("skills"), "skill-import-error"), null, "同名不是错误");

  // 其他失败：后端那一句可读原因落在导入区自己的错误行，不拖垮右栏。
  picker = byOdId(harness.panel("skills"), "skill-import-picker");
  picker.value = "other";
  await harness.agentBody.dispatchEvent({ type: "change", target: picker });
  await settle();

  const error = byOdId(harness.panel("skills"), "skill-import-error");
  assert.ok(error, "失败落在导入区自己的错误行");
  assert.match(error.textContent, /other/);
  assert.match(error.textContent, /导入失败/);
  assert.match(error.textContent, /缺少 SKILL\.md/, "失败原因来自后端可读正文");
  assert.equal(byOdId(harness.panel("skills"), "skill-error"), null, "不拖垮技能列表错误行");
  assert.ok(byOdId(harness.panel("skills"), "agent-skill-detail"), "右栏详情不受影响");

  // 冲突时不许改用 PATCH 覆盖。
  assert.equal(
    harness.patches().filter((call) => call.url.startsWith("/api/skills/")).length,
    0,
    "冲突时不许改用 PATCH 覆盖",
  );
});

test("候选为空（来源在但没有技能）给空态指引，不渲染下拉框", async () => {
  const harness = createHarness({ candidates: [] });
  await harness.openAgent("mem-1");

  const block = byOdId(harness.panel("skills"), "skill-import");
  assert.ok(block, "区块照常渲染");
  assert.equal(byOdId(harness.panel("skills"), "skill-import-picker"), null, "没有候选就不渲染下拉框");
  assert.match(block.textContent, /先运行上面的命令/, "空态要给「先跑命令」的指引");
  assert.equal(byOdId(harness.panel("skills"), "skill-import-error"), null, "空态不是错误");
});

test("候选来源缺席 / 扫描失败：只落在导入区块自己的行，不拖垮技能段", async () => {
  const missing = createHarness({ candidates: [], candidatesAvailable: false });
  await missing.openAgent("mem-1");
  const block = byOdId(missing.panel("skills"), "skill-import");
  assert.equal(
    byOdId(missing.panel("skills"), "skill-import-picker"),
    null,
    "来源目录缺席时不渲染下拉框",
  );
  assert.match(block.textContent, /先运行上面的命令/, "来源缺席时给「先跑命令」的指引");
  assert.equal(byOdId(missing.panel("skills"), "skill-error"), null, "缺席不是错误");

  const broken = createHarness({
    fetch: (url) =>
      url.startsWith("/api/skills/candidates")
        ? errorResponse(500, "后端读取失败")
        : null,
  });
  await broken.openAgent("mem-1");
  assert.ok(
    byOdId(broken.panel("skills"), "skill-import-error"),
    "扫描失败落在导入区块自己的错误行",
  );
  assert.equal(byOdId(broken.panel("skills"), "skill-error"), null, "不牵连技能列表的错误行");
  assert.ok(byOdId(broken.panel("skills"), "skill-list"), "技能段其余部分照常");
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

test("新增文案在两份词条里同键同参数位，随入口撤除的键必须消失", () => {
  const harness = createHarness();
  const keys = [
    "agentCapabilities.scopeNote",
    "agentCapabilities.mcpTitle",
    "agentCapabilities.skillTitle",
    "roster.capabilities",
    "roster.capabilitiesLoading",
    "roster.capabilitiesUnavailable",
    // #282 的下拉框与导入区块的沿用键。
    "skill.importTitle",
    "skill.importCommandHint",
    "skill.importScanButton",
    "skill.importPickLabel",
    "skill.importPickPlaceholder",
    "skill.importGroupReady",
    "skill.importGroupInstalled",
    "skill.importGroupBlocked",
    "skill.importLoading",
    "skill.importScanFailed",
    "skill.importUnavailable",
    "skill.importEmpty",
    "skill.importStatusInstalled",
    "skill.importStatusBlocked",
    "skill.importBusy",
    "skill.importResultDone",
    "skill.importResultSkipped",
    "skill.importResultFailed",
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
  // 能力不再「两 kind 同屏」而是各在自己的页签里，因此那个跨两 kind 的总标题
  // `agentCapabilities.title` 失去全部消费点：两份词条一并移除，不留孤儿键。
  // D6 / D9 第 14 条同理：复制入口撤除后那些键也没有消费点（与 `agent.contextWindow` 那四项同一规则）；
  // #282 撤掉手填路径与上传 zip 两条入口，只服务它们的 `skill.install*` / `sourceRequired` /
  // `importRunButton`（多选「导入选中」）/ `importStatusReady`（可选项不再是行上的状态后缀）/ `importPickRequired` /
  // `importSummary`（一次只导一个，不再有汇总）一并删除。
  for (const key of [
    "agentCapabilities.title",
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
    "agentCapabilities.copySummary",
    "skill.installTitle",
    "skill.installDir",
    "skill.installDirPlaceholder",
    "skill.installDirButton",
    "skill.installDirHint",
    "skill.installZip",
    "skill.installZipHint",
    "skill.installing",
    "skill.sourceRequired",
    "skill.importRunButton",
    "skill.importStatusReady",
    "skill.importPickRequired",
    "skill.importSummary",
  ]) {
    for (const locale of ["zh-CN", "en"]) {
      assert.equal(
        harness.catalogs[locale][key],
        undefined,
        locale + " 仍有已删词条：" + key,
      );
    }
  }
});
