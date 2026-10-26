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
 * 3b. skills 页签的右栏是**选中技能**的详情（#283 / D5）：五格元数据 + 启停 / 删除（作用于当前选中项）
 *    + SKILL.md 正文（`GET /api/skills/{id}/markdown`，按需读、纯文本呈现、四态各有说法）；默认选中
 *    左栏第一行，删除后回落第一行，左栏为空给空态；
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

/** 家目录 `mcp.json` 的原文替身：**非标准缩进 + 倒键序 + 末尾换行**，与后端逐字写盘口径同形。 */
const MCP_FILE_TEXT =
  '{\n  "servers": [\n    {"transport": "stdio", "command": "python3", "name": "notes"}\n  ]\n}\n';

/** 技能正文的替身（形状与 `GET /api/skills/{id}/markdown` 一致）。

    `SKILL_BODY` 是「磁盘上 `SKILL.md` 里 front matter 之后的那段字节」的替身：保留空行与缩进、
    含 `#` / `*` / `|` 这些 Markdown 记号——右栏必须把它当**纯文本**原样呈现。 */
const SKILL_BODY =
  "# 提交前自审\n\n按步骤来：\n\n    先读改动，再读测试\n\n- 一\n- 二\n\n**重点**：边界条件。";

function markdownPayload(overrides) {
  return Object.assign(
    { state: "ok", markdown: SKILL_BODY },
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
  // 家目录 mcp.json 的原文镜像（#284 / D7）：读 / 写共用同一份，所以「保存后按后端读数刷新」
  // 可以被观测到（PUT 之后下一次 GET / 同一次响应的 readers 都是同一个值）。
  const mcpFileDoc = {
    exists: config.mcpFileExists === undefined ? true : config.mcpFileExists,
    text: config.mcpFileText === undefined ? MCP_FILE_TEXT : config.mcpFileText,
    servers: config.mcpFileServers === undefined ? 1 : config.mcpFileServers,
  };
  // 正文端点逐 id 的替身：没登记的技能按「字节缺失」回（与后端 `state="missing"` 同形）。
  const skillMarkdown = config.skillMarkdown || { sk_1: markdownPayload() };

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
    // 家目录 mcp.json 的原文（#284 / D7）：读回后端读数，写（PUT）回提交的原文 + 同一条数。
    const mcpFile = /^\/api\/agents\/([^/]+)\/global-mcp-file$/.exec(url);
    if (mcpFile && method === "GET") {
      return jsonResponse({
        member_id: mcpFile[1],
        exists: mcpFileDoc.exists,
        text: mcpFileDoc.text,
        servers: mcpFileDoc.servers,
      });
    }
    if (mcpFile && method === "PUT") {
      const body =
        typeof request.body === "string" ? JSON.parse(request.body || "{}") : request.body || {};
      if (config.mcpFilePutError) {
        return errorResponse(400, config.mcpFilePutError);
      }
      mcpFileDoc.text = String(body.text || "");
      mcpFileDoc.exists = true;
      mcpFileDoc.servers = mcpFileDoc.servers + 1;
      return jsonResponse({
        member_id: mcpFile[1],
        exists: true,
        text: mcpFileDoc.text,
        servers: mcpFileDoc.servers,
      });
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
    const markdownRead = /^\/api\/skills\/([^/]+)\/markdown$/.exec(url);
    if (markdownRead && method === "GET") {
      const id = markdownRead[1];
      const spec = skillMarkdown[id] || markdownPayload({ state: "missing", markdown: null });
      const row = skills.find((item) => item.id === id) || skillPayload({ id });
      return jsonResponse({ skill: Object.assign({}, row), markdown: spec.markdown, state: spec.state });
    }
    const skillWrite = /^\/api\/skills\/([^/]+)$/.exec(url);
    if (skillWrite && method === "PATCH") {
      const body =
        typeof request.body === "string" ? JSON.parse(request.body || "{}") : request.body || {};
      const row = skills.find((item) => item.id === skillWrite[1]);
      if (row && "enabled" in body) row.enabled = Boolean(body.enabled);
      return jsonResponse({ skill: Object.assign({}, row || skillPayload({ id: skillWrite[1] })) });
    }
    if (skillWrite && method === "DELETE") {
      const index = skills.findIndex((item) => item.id === skillWrite[1]);
      const removed = index >= 0 ? skills.splice(index, 1)[0] : skillPayload({ id: skillWrite[1] });
      return jsonResponse({ skill: Object.assign({}, removed) });
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
    /** 切到 skills 页签（点真实页签按钮：监听挂在 `#adTabs` 上）。 */
    async openSkillsTab() {
      await elementFor("#adTabs").dispatchEvent({ type: "click", target: elementFor("#adTabSkills") });
      await settle();
    },
    /** 点任意一个页签按钮（`base` / `skills` / `mcp`）。 */
    async openTab(name) {
      const suffix = name.charAt(0).toUpperCase() + name.slice(1);
      await elementFor("#adTabs").dispatchEvent({ type: "click", target: elementFor("#adTab" + suffix) });
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
  // 动作入口在**右栏**，作用于当前选中项（#283 / D5）；左栏行只负责选人（复制入口已随弹层
  // 撤除，反向断言在下面的「复制入口与弹层撤除」用例里）。
  assert.ok(byDataset(panel, "skillToggle", "sk_1"), "右栏的启停入口指向当前选中项");
  assert.ok(byDataset(panel, "skillDelete", "sk_1"), "右栏的删除入口指向当前选中项");
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

test("mcp 页签：上块库行、下块 mcp.json 文本域，两块各有一行数据来源说明", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const panel = harness.panel("mcp");
  assert.ok(panel, "MCP 页签要在");

  // 两块按顺序：库行块在上，文件块在下。
  const dbBlock = byOdId(panel, "agent-capabilities-mcp");
  const fileBlock = byOdId(panel, "agent-detail-section-mcp-file");
  assert.ok(dbBlock && fileBlock, "两块都要在");
  assert.ok(
    descendants(panel).indexOf(dbBlock.parentNode) < descendants(panel).indexOf(fileBlock.parentNode),
    "库行块在上、文件块在下",
  );

  // 上块：一行数据来源说明 + 库行表单本身。
  const dbSource = byOdId(panel, "mcp-source-db");
  assert.ok(dbSource, "上块要给一行数据来源说明");
  assert.equal(dbSource.textContent, catalogText(harness, "zh-CN", "mcp.sourceDb"));
  assert.ok(byOdId(panel, "mcp-form"), "上块是库行表单");
  assert.ok(byDataset(panel, "mcpToggle", "mcp_1"), "上块的库行启停还在");
  assert.ok(byDataset(panel, "mcpDelete", "mcp_1"), "上块的库行删除还在");

  // 下块：一行数据来源说明（**必须写明「同名时库行胜出」**）+ 文本域。
  const fileSource = byOdId(panel, "agent-detail-mcp-file-source");
  assert.ok(fileSource, "下块要给一行数据来源说明");
  assert.equal(fileSource.textContent, catalogText(harness, "zh-CN", "agentDetail.mcpFileSource"));
  assert.match(fileSource.textContent, /同名时库行胜出/, "数据来源要写明同名时谁胜出");
  const textarea = byOdId(panel, "agent-mcp-file-text");
  assert.ok(textarea, "下块是 mcp.json 文本域");
  assert.equal(textarea.tagName, "textarea", "下块是纯文本域，不做 JSON 格式化");
});

test("mcp 页签下块：打开页签时文本域显示文件当前原文，并给出条目计数", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const panel = harness.panel("mcp");
  // 读的是这个成员的原文端点（不是别的成员，也不是列表响应里的摘要）。
  assert.ok(
    harness.calls.some((call) => call.url === "/api/agents/mem-1/global-mcp-file" && call.method === "GET"),
    "打开页签读该成员的 mcp.json 原文",
  );
  assert.equal(byOdId(panel, "agent-mcp-file-text").value, MCP_FILE_TEXT, "文本域显示原文（逐字）");
  assert.equal(
    byOdId(panel, "agent-mcp-file-count").textContent,
    catalogText(harness, "zh-CN", "agentDetail.mcpFileServers", { count: "1" }),
    "条目计数按后端读数呈现",
  );
});

test("mcp 页签下块：文件不存在时文本域为空，并给出「还没有这个文件」的可读说明", async () => {
  const harness = createHarness({ mcpFileExists: false, mcpFileText: "", mcpFileServers: 0 });
  await harness.openAgent("mem-1");

  const panel = harness.panel("mcp");
  assert.equal(byOdId(panel, "agent-mcp-file-text").value, "", "文件不存在时文本域为空");
  const missing = byOdId(panel, "agent-mcp-file-missing");
  assert.ok(missing, "文件不存在时要有一句可读说明");
  assert.equal(missing.textContent, catalogText(harness, "zh-CN", "agentDetail.mcpFileMissing"));
  // 那句话说的必须是「还没有这个文件」，不能被读成「读到空文件」。
  assert.match(missing.textContent, /还没有/);
});

test("mcp 页签下块：保存发一次 PUT（body 是整份原文），成功后按后端读数刷新", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const panel = harness.panel("mcp");
  const textarea = byOdId(panel, "agent-mcp-file-text");
  // 人改了文本域（包括换行与缩进）：保存发的是**整份原文**，不是拆开的字段。
  const edited = '{\n    "servers": [\n        {"name": "later", "transport": "stdio", "command": "node"}\n    ]\n}';
  await harness.changeInBody(textarea, edited);
  await harness.clickInBody(byOdId(panel, "agent-mcp-file-save"));

  const puts = harness.calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1, "保存只发一次 PUT");
  assert.equal(puts[0].url, "/api/agents/mem-1/global-mcp-file");
  assert.deepEqual(puts[0].body, { text: edited }, "body 是整份原文（一个 text 字段）");

  // 保存成功后文本域与条目计数按**后端读数**刷新（不是把本地输入当结果）。
  assert.equal(byOdId(harness.panel("mcp"), "agent-mcp-file-text").value, edited);
  assert.equal(
    byOdId(harness.panel("mcp"), "agent-mcp-file-count").textContent,
    catalogText(harness, "zh-CN", "agentDetail.mcpFileServers", { count: "2" }),
    "计数跟着后端读数走",
  );
});

test("mcp 页签下块：保存失败时给出后端可读原因，且不清空人的输入", async () => {
  const harness = createHarness({ mcpFilePutError: "mcp.json 不是可解析的 JSON" });
  await harness.openAgent("mem-1");

  const panel = harness.panel("mcp");
  await harness.changeInBody(byOdId(panel, "agent-mcp-file-text"), "{ 坏掉的 json");
  await harness.clickInBody(byOdId(panel, "agent-mcp-file-save"));

  const error = byOdId(harness.panel("mcp"), "agent-mcp-file-error");
  assert.ok(error, "失败落在下块自己的错误行");
  assert.match(error.textContent, /不是可解析的 JSON/, "原因来自后端可读正文");
  assert.equal(
    byOdId(harness.panel("mcp"), "agent-mcp-file-text").value,
    "{ 坏掉的 json",
    "文本域保留人的输入（不清空）",
  );
});

/** 下块的两行数据来源说明与「同名时库行胜出」的语义不需要屏上文本外的事实：
    `agentDetail.mcpFileSource` 的词条值本身就写明了它（上一条用例咬的是渲染出的文本）。 */

test("两个页签各读自己那一份：MCP 读数只在 mcp 页签、技能读数只在 skills 页签", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");

  const skills = harness.panel("skills");
  const mcp = harness.panel("mcp");
  assert.ok(skills && mcp, "两个页签面板都要在");

  assert.ok(byDataset(skills, "skill", "sk_1"), "技能行在 skills 页签里");
  assert.equal(byDataset(skills, "mcpServer", "mcp_1"), null, "skills 页签里不出现 MCP 行");

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

/* ─── 2b. skills 右栏：选中技能的详情与 SKILL.md 正文（#283 / D5） ─────────

  观察面：`data-od-id="agent-skill-detail"` 子树的五格元数据、两个动作入口、正文节点
  （`skill-detail-markdown`）的 `textContent`，以及点左栏一行时发出的正文读取。 */

/** 右栏的一格元数据（`data-od-id="skill-detail-<key>"`）。 */
function detailCell(panel, key) {
  return byOdId(panel, "skill-detail-" + key);
}

/** 某一行的选中态读法：`is-active` 与 `aria-current` 是同一条事实的两处呈现。 */
function rowIsCurrent(panel, id) {
  const row = byDataset(panel, "skill", id);
  return Boolean(row) && row.getAttribute("aria-current") === "true";
}

test("skills 右栏：默认选中第一行，五项元数据 + 正文与磁盘 SKILL.md 逐字相等", async () => {
  const harness = createHarness({
    skills: [skillPayload(), skillPayload({ id: "sk_2", name: "triage", description: "分流" })],
    skillMarkdown: {
      sk_1: markdownPayload(),
      sk_2: markdownPayload({ markdown: "# Triage\n\n先分级。" }),
    },
  });
  await harness.openAgent("mem-1");
  await harness.openSkillsTab();

  const panel = harness.panel("skills");
  const detail = byOdId(panel, "agent-skill-detail");
  assert.ok(detail, "右栏是选中技能的详情");

  // 进入页签默认选中左栏第一行（不需要先点一下）。
  assert.equal(harness.api.state.agentDetailSkillId, "sk_1", "默认选中左栏第一行");
  assert.equal(rowIsCurrent(panel, "sk_1"), true, "第一行有可读的选中态");
  assert.equal(rowIsCurrent(panel, "sk_2"), false);

  // 五项元数据：名称 / 描述 / 来源路径 / 内容 hash / 启停态。
  assert.equal(detailCell(panel, "name").textContent, "review");
  assert.equal(detailCell(panel, "description").textContent, "在提交前做一次结构化自审");
  assert.equal(detailCell(panel, "source").textContent, "/Users/me/skills/review");
  assert.equal(
    detailCell(panel, "hash").textContent,
    "0123456789abcdef".repeat(4),
    "内容 hash 是完整值（不是列表里那个 12 位前缀）",
  );
  assert.equal(
    detailCell(panel, "state").textContent,
    catalogText(harness, "zh-CN", "skill.enabled"),
  );

  // 正文按需读：选中时调新端点，而不是塞进列表响应。
  assert.ok(
    harness.calls.some((call) => call.url === "/api/skills/sk_1/markdown"),
    "正文走 GET /api/skills/{id}/markdown",
  );
  assert.equal(
    descendants(panel).filter((node) => node.tagName === "pre").length,
    1,
    "正文节点是一个 <pre>（保留换行与缩进）",
  );
  const body = byOdId(panel, "skill-detail-markdown");
  assert.ok(body, "右栏要有正文节点");
  assert.equal(body.textContent, SKILL_BODY, "正文与磁盘上的 SKILL.md 正文逐字相等");
});

test("skills 右栏：点左栏另一行切换选中项，正文按需重读", async () => {
  const harness = createHarness({
    skills: [skillPayload(), skillPayload({ id: "sk_2", name: "triage", description: "分流" })],
    skillMarkdown: {
      sk_1: markdownPayload(),
      sk_2: markdownPayload({ markdown: "# Triage\n\n先分级。" }),
    },
  });
  await harness.openAgent("mem-1");
  await harness.openSkillsTab();
  const panel = harness.panel("skills");

  await harness.clickInBody(byDataset(panel, "skill", "sk_2"));

  assert.equal(harness.api.state.agentDetailSkillId, "sk_2", "选中态跟着点中那一行");
  assert.equal(rowIsCurrent(harness.panel("skills"), "sk_2"), true);
  const reads = harness.calls.filter((call) => /\/markdown$/.test(call.url)).map((call) => call.url);
  assert.deepEqual(
    reads,
    ["/api/skills/sk_1/markdown", "/api/skills/sk_2/markdown"],
    "每选中一行读一次正文（不预读整列）",
  );
  assert.equal(
    byOdId(harness.panel("skills"), "skill-detail-markdown").textContent,
    "# Triage\n\n先分级。",
  );
  assert.equal(detailCell(harness.panel("skills"), "name").textContent, "triage");
});

test("skills 右栏：启停与删除作用于当前选中项，启停成功后右栏启停态立刻跟着变", async () => {
  const harness = createHarness({
    skills: [skillPayload(), skillPayload({ id: "sk_2", name: "triage", description: "分流" })],
    skillMarkdown: { sk_1: markdownPayload(), sk_2: markdownPayload({ markdown: "# Triage" }) },
  });
  await harness.openAgent("mem-1");
  await harness.openSkillsTab();

  // 选中第二行，然后按**右栏**的启停入口。
  await harness.clickInBody(byDataset(harness.panel("skills"), "skill", "sk_2"));
  const toggle = byDataset(harness.panel("skills"), "skillToggle", "sk_2");
  assert.ok(toggle, "右栏的启停入口指向当前选中项");
  assert.equal(toggle.textContent, catalogText(harness, "zh-CN", "skill.disable"));

  await harness.clickInBody(toggle);
  const patched = harness.patches().filter((call) => call.url.startsWith("/api/skills/"));
  assert.deepEqual(patched.map((call) => call.url), ["/api/skills/sk_2"], "只改当前选中项");
  assert.deepEqual(patched[0].body, { enabled: false });
  // 启停成功后右栏的启停态立刻跟着变（不用重开页签）。
  assert.equal(
    detailCell(harness.panel("skills"), "state").textContent,
    catalogText(harness, "zh-CN", "skill.disabled"),
  );
  assert.equal(
    byDataset(harness.panel("skills"), "skillToggle", "sk_2").textContent,
    catalogText(harness, "zh-CN", "skill.enable"),
  );
  // 未选中那一行不动。
  assert.match(byDataset(harness.panel("skills"), "skill", "sk_1").textContent, /已启用/);

  // 删除同样只作用于当前选中项。
  await harness.clickInBody(byDataset(harness.panel("skills"), "skillDelete", "sk_2"));
  assert.deepEqual(
    harness.calls.filter((call) => call.method === "DELETE").map((call) => call.url),
    ["/api/skills/sk_2"],
    "删除只发当前选中项",
  );
  // 删掉当前选中项 ⇒ 右栏回落到第一行，不停在一个已删的技能上。
  assert.equal(harness.api.state.agentDetailSkillId, "sk_1", "选中项回落第一行");
  assert.equal(detailCell(harness.panel("skills"), "name").textContent, "review");
});

test("skills 右栏：左栏行可键盘选中（Enter / Space），选中态带 aria-current", async () => {
  const harness = createHarness({
    skills: [skillPayload(), skillPayload({ id: "sk_2", name: "triage", description: "分流" })],
    skillMarkdown: { sk_1: markdownPayload(), sk_2: markdownPayload({ markdown: "# Triage" }) },
  });
  await harness.openAgent("mem-1");
  await harness.openSkillsTab();

  const row = byDataset(harness.panel("skills"), "skill", "sk_2");
  assert.ok(row, "技能行要在左栏");
  assert.equal(row.getAttribute("tabindex"), "0", "行可被 Tab 抵达");
  await harness.agentBody.dispatchEvent({ type: "keydown", target: row, key: "Enter" });
  await settle();
  assert.equal(harness.api.state.agentDetailSkillId, "sk_2", "Enter 选中该行");
  assert.ok(
    descendants(harness.panel("skills")).some((node) => node.getAttribute("aria-current") === "true"),
    "选中态有一条 aria 事实",
  );
  // 焦点停在动作按钮上时 Enter 不该顺带换选中项（否则键盘用户按启停会磒到别的技能）。
  const toggle = byDataset(harness.panel("skills"), "skillToggle", "sk_2");
  await harness.agentBody.dispatchEvent({ type: "keydown", target: toggle, key: "Enter" });
  await settle();
  assert.equal(harness.api.state.agentDetailSkillId, "sk_2", "动作按钮上的 Enter 不换选中项");
});

test("skills 右栏：左栏为空时给空态指引，不读正文", async () => {
  const harness = createHarness({ skills: [] });
  await harness.openAgent("mem-1");
  await harness.openSkillsTab();

  const panel = harness.panel("skills");
  const detail = byOdId(panel, "agent-skill-detail");
  assert.ok(detail, "左栏为空时右栏仍有落点");
  assert.equal(
    detail.textContent,
    catalogText(harness, "zh-CN", "skill.detailEmpty"),
    "空列表给空态指引",
  );
  assert.equal(
    harness.calls.filter((call) => /\/markdown$/.test(call.url)).length,
    0,
    "没有选中项就不读正文",
  );
});

test("skills 右栏：正文按纯文本呈现，不把 Markdown 解析成 HTML", async () => {
  const harness = createHarness();
  await harness.openAgent("mem-1");
  await harness.openSkillsTab();

  const panel = harness.panel("skills");
  const body = byOdId(panel, "skill-detail-markdown");
  assert.ok(body, "正文节点在右栏");
  assert.equal(body.tagName, "pre", "正文是一个 <pre>（纯文本容器）");
  // 正文里的 Markdown 记号原样呈现：`#` 与 `**` 不被消费成标签。
  assert.equal(body.textContent, SKILL_BODY);
  assert.match(body.textContent, /^# 提交前自审/);
  assert.match(body.textContent, /\*\*重点\*\*/);
  assert.match(body.textContent, /\n {4}先读改动，再读测试/, "缩进保留");
  for (const tag of ["h1", "h2", "strong", "em", "ul", "li", "code", "p"]) {
    assert.equal(
      descendants(body).filter((node) => node.tagName === tag).length,
      0,
      `正文里不该有 <${tag}>：Markdown 不被解析`,
    );
  }
  // 正文只经 textContent 写：正本里没有 innerHTML。
  assert.equal(stripComments(readStudio("app.js")).includes("innerHTML"), false, "不引入 HTML 注入面");
});

test("skills 右栏：ok / missing / invalid / unreadable 各有自己的说明文案", async () => {
  const harness = createHarness({
    skills: [
      skillPayload(),
      skillPayload({ id: "sk_2", name: "missing-one" }),
      skillPayload({ id: "sk_3", name: "invalid-one" }),
      skillPayload({ id: "sk_4", name: "unreadable-one" }),
    ],
    skillMarkdown: {
      sk_1: markdownPayload(),
      sk_2: markdownPayload({ state: "missing", markdown: null }),
      sk_3: markdownPayload({ state: "invalid", markdown: null }),
      sk_4: markdownPayload({ state: "unreadable", markdown: null }),
    },
  });
  await harness.openAgent("mem-1");
  await harness.openSkillsTab();

  const readings = {};
  for (const id of ["sk_1", "sk_2", "sk_3", "sk_4"]) {
    await harness.clickInBody(byDataset(harness.panel("skills"), "skill", id));
    const panel = harness.panel("skills");
    const stateNode = byOdId(panel, "skill-detail-body-state");
    readings[id] = {
      bodyState: stateNode ? stateNode.getAttribute("data-detail-state") : null,
      text: stateNode ? stateNode.textContent : byOdId(panel, "skill-detail-markdown").textContent,
    };
  }

  assert.equal(readings.sk_1.bodyState, null, "ok 没有空态行，正文就在那里");
  assert.equal(readings.sk_2.bodyState, "missing");
  assert.equal(readings.sk_3.bodyState, "invalid");
  assert.equal(readings.sk_4.bodyState, "unreadable");

  // 四种状态的说明文案各不相同（「读不到」不会被读成「这个技能是空的」）。
  for (const [id, expected] of [
    ["sk_2", "skill.detailMissing"],
    ["sk_3", "skill.detailInvalid"],
    ["sk_4", "skill.detailUnreadable"],
  ]) {
    assert.equal(readings[id].text, catalogText(harness, "zh-CN", expected), `${id} 的说明文案`);
  }
  const texts = [readings.sk_1.text, readings.sk_2.text, readings.sk_3.text, readings.sk_4.text];
  assert.equal(new Set(texts).size, 4, "四种状态的说明互不相同");
});

/* ─── 2c. skills 页签的**固定高度工作区**（用户 2026-10-26："skills 页窗口固定，下拉列表高度
   固定，否则窗口拉下去右侧 skill 详情都看不到了"）───────────────────────
   两层判据，各管一件事：
   1. 状态层（Node 能判）：切页签时 `.agent-page` 上的状态类**跟着走**——skills 置上，base / mcp
      不带；同时不重铺面板、不发请求（切页签不丢草稿是 D1 的硬约束，不能被布局改动拌坏）。
   2. 样式层（同理可判）：`app.css` 摊平后按视口求值，断言桌面档（1280 / 1440）里固定高度链条
      的每一环都在位，而 ≤900px 单栏断点下这一整块**不生效**（那里仍是页面滚动）。
      真机计算值（clientHeight / scrollHeight / getBoundingClientRect）由 ego-browser 实测给出，
      不在 Node 里假装。 */

/** 一条声明的 `prop: value` 表（`!important` 从取值里剥掉，本判据不看优先级）。 */
function declarationsOf(body) {
  const out = {};
  for (const chunk of body.split(";")) {
    const at = chunk.indexOf(":");
    if (at < 0) continue;
    const prop = chunk.slice(0, at).trim();
    const value = chunk.slice(at + 1).replace(/!important/g, "").trim();
    if (prop) out[prop] = value;
  }
  return out;
}

/** 摊平 `app.css` 成有序的 `{ conditions, selector, declarations }`。

    与 `mobile_shell.test.cjs` 的那一份同源，但**刻意不是同一份**：那边判的是移动壳（需要
    `prefers-reduced-motion` 条件），本文件只需要宽度两个方向（`mediaMatches` 只认这两个），
    于是那边把不认识的写成「不生效」。跨文件抽公用模块会让两份判据绑在一起，而
    `approval_dom_harness.cjs` 那种共享装置的正当理由是「需要假时钟 / 定时器」这类非它不可的能力，
    这里没有。两份的差异就只有这一条，已在 `mediaMatches` 的注释里写明。 */
function flattenCss(css, conditions, out) {
  const list = out || [];
  let index = 0;
  while (index < css.length) {
    const open = css.indexOf("{", index);
    if (open < 0) break;
    const head = css.slice(index, open).trim();
    let depth = 1;
    let cursor = open + 1;
    while (cursor < css.length && depth > 0) {
      if (css[cursor] === "{") depth += 1;
      else if (css[cursor] === "}") depth -= 1;
      cursor += 1;
    }
    const body = css.slice(open + 1, cursor - 1);
    if (head.startsWith("@media")) {
      flattenCss(body, (conditions || []).concat([head.slice("@media".length).trim()]), list);
    } else if (!head.startsWith("@")) {
      for (const selector of head.split(",")) {
        list.push({
          conditions: conditions || [],
          selector: selector.trim(),
          declarations: declarationsOf(body),
        });
      }
    }
    index = cursor;
  }
  return list;
}

/** 本票只需要宽度两个方向的条件；其它条件（`prefers-color-scheme`、`prefers-reduced-motion` 等）
    一律算不生效——这就是与 `mobile_shell.test.cjs` 同名函数唯一的差异（那边多认一个动效档）。 */
function mediaMatches(condition, width) {
  const max = /^\(max-width:\s*(\d+)px\)$/.exec(condition);
  if (max) return width <= Number(max[1]);
  const min = /^\(min-width:\s*(\d+)px\)$/.exec(condition);
  if (min) return width >= Number(min[1]);
  return false;
}

/** 某个视口宽度下的「基础选择器 + 状态选择器」合并后的声明表（靠后的规则赢）。 */
function declarationsAt(width, base, state) {
  const map = new Map();
  const css = readStudio("app.css").replace(/\/\*[\s\S]*?\*\//g, "");
  for (const rule of flattenCss(css, [], [])) {
    if (!rule.conditions.every((condition) => mediaMatches(condition, width))) continue;
    map.set(rule.selector, Object.assign(map.get(rule.selector) || {}, rule.declarations));
  }
  const merged = Object.assign({}, map.get(base) || {});
  if (state && map.has(state)) Object.assign(merged, map.get(state));
  return merged;
}

test("切页签：skills 置上固定高度状态类，base / mcp 不带，且不重铺面板也不发请求", async () => {
  const harness = createHarness({
    skills: [skillPayload(), skillPayload({ id: "sk_2", name: "triage" })],
    skillMarkdown: { sk_1: markdownPayload(), sk_2: markdownPayload({ markdown: "# Triage" }) },
  });
  await harness.openAgent("mem-1");
  const viewAgent = harness.elementFor("#viewAgent");

  // 默认页签（基础信息和配置）不带：那一页仍是整页滚动。
  assert.equal(viewAgent.classList.contains("is-skills-fixed"), false, "基础信息和配置页签不带固定高度状态");

  await harness.openSkillsTab();
  assert.equal(viewAgent.classList.contains("is-skills-fixed"), true, "skills 页签置上固定高度状态");

  // 切页签只改呈现状态：面板节点**还是原来那批**（不重铺 ⇒ 焦点与草稿不会被换掉）。
  const skillsPanelBefore = harness.panel("skills");
  const rowBefore = byDataset(skillsPanelBefore, "skill", "sk_1");
  const callsBefore = harness.calls.length;

  await harness.openTab("base");
  assert.equal(viewAgent.classList.contains("is-skills-fixed"), false, "回到基础信息和配置就撤掉固定高度状态");
  assert.equal(harness.panel("base").hidden, false);
  assert.equal(harness.panel("skills").hidden, true);

  await harness.openTab("mcp");
  assert.equal(viewAgent.classList.contains("is-skills-fixed"), false, "mcp 页签也不带固定高度状态");

  await harness.openSkillsTab();
  assert.equal(viewAgent.classList.contains("is-skills-fixed"), true, "再切回 skills 又置上");
  assert.equal(byDataset(harness.panel("skills"), "skill", "sk_1"), rowBefore, "面板内容一个节点都没被换掉");
  assert.equal(
    harness.calls.length,
    callsBefore,
    "来回切页签不发任何请求（选中项与正文缓存都还是一次读取的结果）",
  );
});

test("桌面档样式契约：skills 固定高度链条逐环在位，右栏只有一处滚动、正文不再自带 60vh", () => {
  for (const width of [1280, 1440]) {
    const prefix = width + "px：";
    // 外层滚动器：基础规则里的 `overflow-y: auto` 原样保留（固定布局**不加**覆盖），于是矮视口下
    // 导入区装不下时整页还能滚到它——skills 档正常高度下它不产生溢出，因为子层各自滚。
    assert.equal(
      declarationsAt(width, ".agent-page-scroll")["overflow-y"],
      "auto",
      prefix + "外层滚动器保持 auto（矮视口下还能滚到导入区，不裁掉）",
    );
    assert.equal(
      declarationsAt(width, ".agent-page", ".agent-page.is-skills-fixed > .agent-page-scroll")["overflow-y"],
      undefined,
      prefix + "固定高度不靠覆盖外层 overflow 实现（不写零效果的重复声明）",
    );
    // 高度链条：每一环都要断开默认的 `min-height: auto`，缺一层就会重新被内容撑高。
    const inner = declarationsAt(width, ".agent-page-inner", ".agent-page.is-skills-fixed .agent-page-inner");
    assert.equal(inner["flex-direction"], "column", prefix + "内层是列 flex");
    assert.equal(inner["min-height"], "0", prefix + "内层允许收缩");
    assert.equal(inner.height, "100%", prefix + "内层占满外层");

    const panel = declarationsAt(
      width,
      "[data-adpanel='skills']",
      ".agent-page.is-skills-fixed [data-adpanel='skills']",
    );
    assert.equal(panel["flex-direction"], "column");
    assert.equal(panel.flex, "1");
    assert.equal(panel["min-height"], "0");
    // 面板按 `data-adpanel` 选：全表不再有 id 选择器（否则改 `index.html` 的 id 会静默断样式）。
    assert.equal(
      declarationsAt(width, "#adPanelSkills").display,
      undefined,
      prefix + "skills 面板不靠 id 选择器定位",
    );

    const section = declarationsAt(
      width,
      ".agent-skill-section",
      ".agent-page.is-skills-fixed .agent-skill-section",
    );
    assert.equal(section["flex-direction"], "column");
    assert.equal(section["min-height"], "0");

    // 导入区不被压缩：「顶部导入区固定」的承重行。删了它双栏就会把导入区挤成几像素，
    // 而下面那三条用例（两栏滚动、正文、页签状态）仍全绿——所以它必须单独钉住。
    assert.equal(
      declarationsAt(
        width,
        ".agent-skill-section > :not(.agent-skill-columns)",
        ".agent-page.is-skills-fixed .agent-skill-section > :not(.agent-skill-columns)",
      ).flex,
      "none",
      prefix + "导入区不参与收缩",
    );
    // 导入结果行会累积：它不被压缩（`flex: none`），装不下时由外层整页滚，
    // 不在导入区块里再套一根滚动条（那会把它的下拉框自己滚出去）。
    assert.equal(
      declarationsAt(
        width,
        "[data-od-id='skill-import']",
        ".agent-page.is-skills-fixed [data-od-id='skill-import']",
      )["overflow-y"],
      undefined,
      prefix + "导入区块没有自己的滚动条",
    );

    const columns = declarationsAt(
      width,
      ".agent-skill-columns",
      ".agent-page.is-skills-fixed .agent-skill-columns",
    );
    assert.match(String(columns.flex), /^1 1 auto$/, prefix + "双栏拿余下高度（可伸缩）");
    assert.ok(columns["min-height"], prefix + "双栏有极矮视口的兜底高度");

    // 左右两栏**各自**滚动（不是只滚左栏、也不是都不滚）。
    for (const column of [".agent-skill-left", ".agent-skill-right"]) {
      const side = declarationsAt(
        width,
        column,
        ".agent-page.is-skills-fixed " + column,
      );
      assert.ok(side["overflow-y"], prefix + column + " 有内部滚动");
      assert.equal(side["min-height"], "0", prefix + column + " 能收缩到容器高度");
    }

    // 右栏只有**一处**滚动：正文不再自带 60vh 滚动盒。
    const markdown = declarationsAt(
      width,
      ".skill-detail-markdown",
      ".agent-page.is-skills-fixed .skill-detail-markdown",
    );
    assert.equal(markdown["max-height"], "none", prefix + "正文不再有固定最大高度");
    assert.equal(markdown.overflow, "visible", prefix + "正文不再自己滚（否则长正文要滚两根条）");

    // 右栏表格行不被压扁（列 flex 的直接子节点默认会缩）。
    assert.equal(
      declarationsAt(
        width,
        ".skill-detail > .kv",
        ".agent-page.is-skills-fixed .skill-detail > .kv",
      ).flex,
      "none",
      prefix + "元数据行不被压缩",
    );
  }
});

test("≤900px 单栏档：固定高度整块不生效，页面滚动与旧行为不变", () => {
  const css = readStudio("app.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const mobile = flattenCss(css, [], []).filter(
    (rule) =>
      rule.conditions.some((condition) => condition === "(max-width: 900px)")
      && rule.selector.includes("is-skills-fixed"),
  );
  assert.deepEqual(mobile, [], "≤900px 里没有任何固定高度规则（单栏靠页面滚动，不用固定盒子）");

  // 单栏断点本身仍在：两栏上下堆叠。
  const columns = declarationsAt(900, ".agent-skill-columns");
  assert.match(String(columns["grid-template-columns"]), /minmax\(0, 1fr\)/);
  // 两栏在 900 宽下没有内部滚动（那一条只写在 >=901px）。
  assert.equal(declarationsAt(900, ".agent-skill-left")["overflow-y"], undefined, "≤900px 左栏没有内部滚动");
  assert.equal(declarationsAt(900, ".agent-skill-right")["overflow-y"], undefined, "≤900px 右栏没有内部滚动");
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
    // #283 的右栏详情（D5）：五格元数据的标签、两个动作入口、正文标题与四态空态文案。
    "skill.detailTitle",
    "skill.detailName",
    "skill.detailDescription",
    "skill.detailSource",
    "skill.detailHash",
    "skill.detailState",
    "skill.detailEmpty",
    "skill.detailBodyTitle",
    "skill.detailBodyLoading",
    "skill.detailBodyFailed",
    "skill.detailMissing",
    "skill.detailInvalid",
    "skill.detailUnreadable",
    // #284 的 mcp 页签两块面（D7）：上块的数据来源标注 + 下块的文本域、四态说明与保存入口。
    "mcp.sourceDb",
    "agentDetail.mcpFileTitle",
    "agentDetail.mcpFileSource",
    "agentDetail.mcpFileLoading",
    "agentDetail.mcpFileMissing",
    "agentDetail.mcpFileServers",
    "agentDetail.mcpFileSave",
    "agentDetail.mcpFileLoadFailed",
    "agentDetail.mcpFileSaveFailed",
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
  // #284：下块的数据来源标注**必须**写明「同名时库行胜出」——那是 D7 的优先级口径，
  // 少了这句人就看不出同名时谁赢（装配行为本身一字不改）。
  for (const locale of ["zh-CN", "en"]) {
    const source = harness.catalogs[locale]["agentDetail.mcpFileSource"];
    assert.equal(typeof source, "string", `${locale} 缺少词条：agentDetail.mcpFileSource`);
    assert.ok(source.length > 0, `${locale} 的 agentDetail.mcpFileSource 不能为空`);
  }
  assert.match(
    catalogText(harness, "zh-CN", "agentDetail.mcpFileSource"),
    /同名时库行胜出/,
    "中文的数据来源写明同名时库行胜出",
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
