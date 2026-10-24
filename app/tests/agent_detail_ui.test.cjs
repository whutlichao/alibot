/**
 * Studio 的 **Agent 详情整页视图**（`state.mode === 'agent'`）的 DOM 行为测试。
 *
 * 接缝沿用既有那一套（与 `search_ui.test.cjs` / `i18n_ui.test.cjs` 同款）：`vm` 装载
 * `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点，不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 打开详情：主线切到 `agent` 视图（`state.mode === 'agent'`），`#dock` 让位（`hidden`），
 *    详情页容器可见——左栏 Agent 行 / 成员面板名字 / 失败块的「看成员状态」三个入口同一条路径；
 * 2. 四处区块（统计 / 系统提示 / 配置 / 全局 MCP）在同一页纵向铺开，系统提示紧贴统计之下，
 *    页签节点（`#adTabOverview` / `#adTabRuntime` / `data-adtab`）在正本里不存在；
 * 3. 就地编辑只写草稿：改一个字段 ⇒ 底部保存条出现；「保存」一次性提交**全部**可写字段；
 *    「放弃」丢弃草稿、字段回到后端读数；
 * 4. 后端整体拒绝时错误留在保存条里、输入不丢、保存条仍在；
 * 5. 暂停 / 恢复是 `enabled` 的**唯一**界面入口（只写草稿里的 `enabled`），联网档四档随 PATCH 提交；
 * 6. 关闭详情回到进入前的 `state.mode`，dock 按进入前的开合恢复；
 * 7. 编辑配置弹窗（`#agentEditOverlay`）整个删掉：正本里没有节点，运行时不出现对它的 `$()`。
 * 8. 危险操作是页面最后一个区块：暂停 / 恢复搬进它、删除走**确认弹窗**（第一次点击不发请求），
 *    成功关闭详情并刷新列表、失败留在当前页且弹窗保持打开；离线时两个动作都禁用。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/** 与 `app.js` 的 `$()` 同形的选择器替身：未知选择器返回一个空节点，缺哪个补哪个。 */
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

/** 让挂起的 `await` 链跑完（进入 target 的读路径是「一拍串一拍」）。 */
async function settle() {
  for (let index = 0; index < 10; index += 1) await flush();
}

/** `index.html` 里带 `hidden` 的元素：替身节点的初始可见性从**同一个正本**读，不另手抄一份。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

/** 一个 Agent 详情替身（形状与 `GET /api/agents/{id}` 的 `agent` 一致）。 */
function agentDetail(overrides) {
  return Object.assign(
    {
      id: "mem-1",
      kind: "agent",
      display_name: "小明",
      avatar_slot: 1,
      role: "前端工程师",
      model_id: "ollama:qwen2.5",
      system_prompt: "你只回答一句话。",
      enabled: true,
      auto_claim: false,
      memory_mode: "off",
      context_window_messages: null,
      context_trigger_ratio: null,
      react_max_iters: null,
      // 联网档的 Agent 级覆盖（#202）：`null` = 未配置 = 跟随频道。与 `GET /api/agents/{id}`
      // 的载荷同形（该字段总是出现，未配置时为 `null`）。
      web_access: null,
      provider: "ollama",
      is_remote: false,
      remote_consent: false,
      status: "online",
      enabled_reason: "",
      stats: {
        channel_count: 2,
        round_count: 5,
        completed_task_count: 1,
        success_rate: 0.6666666666666666,
      },
    },
    overrides || {},
  );
}

/** 一个最小频道行（形状与工作台快照的 `channels` 项一致）。 */
function channel(id, name) {
  return { id, name, last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const queries = [];
  const elements = new Map();
  const documentListeners = new Map();
  const member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };

  function fetchStub(url, init) {
    const request = init || {};
    calls.push({
      url: String(url),
      method: request.method || "GET",
      body: request.body ? JSON.parse(request.body) : null,
    });
    if (config.fetch) return config.fetch(String(url), request);
    if (String(url).endsWith("/global-mcp-servers")) {
      return { ok: true, status: 200, json: async () => ({ global_mcp_servers: [] }) };
    }
    if (String(url).startsWith("/api/agents/")) {
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    }
    if (String(url) === "/api/agents") {
      return { ok: true, status: 200, json: async () => ({ agents: [agentDetail()], members: [member] }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  }

  // 替身节点的初始可见性从**同一个正本**读（`index.html` 里带 `hidden` 的元素），
  // 已知的几个控件按真实标签名建（`<button>` 的键盘可达性断言读得到）。
  const BUTTON_IDS = new Set([
    "#btnAgentDetailClose",
    "#btnAgentSendMessage",
    "#adSave",
    "#adDiscard",
    "#btnDockToggle",
    "#agentDeleteConfirm",
    "#agentDeleteCancel",
    "#agentDeleteClose",
  ]);
  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = new FakeNode(BUTTON_IDS.has(selector) ? "button" : "div");
      node.hidden = HIDDEN_IN_HTML.has(selector);
      // 真实 DOM 里 `id` 是属性与属性的双向映射（`event.target.id` 这类判据要用它）。
      if (selector.startsWith("#")) node.id = selector.slice(1);
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

  const document = {
    documentElement: new FakeNode("html"),
    activeElement: null,
    querySelector: (selector) => {
      queries.push(selector);
      return elementFor(selector);
    },
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

  // `setTimeout` 用立即执行的替身：toast 的淡出与重连都是「延后一拍」，测试要的是确定性。
  const context = vm.createContext({
    document,
    fetch: fetchStub,
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
      "\nglobalThis.__agentDetailTest = { state, bindEvents, render, renderMembers, renderDock,"
        + " openAgentDetail, closeAgentDetail, closeTaskDock, toggleDock, setDockMode, setViewMode,"
        // `loadWorkspace` 是真实载入路径：两态用例靠它（与产品自己的重连）驱动，不 poke 模块内标志。
        + " showMessagesView, showTaskBoard, renderAgentDetail, loadWorkspace, sendMessage, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__agentDetailTest;
  api.state.member = member;
  api.state.members = [member];
  api.state.agents = [];
  api.state.channels = [];
  api.state.dms = [];
  api.state.tasks = [];
  api.state.online = true;
  api.state.ready = true;
  api.state.providers = ["ollama"];
  api.state.localProviders = ["ollama"];
  api.state.consents = [];
  api.bindEvents();

  // 每个选择器对应的节点在 `#viewAgent` 的子树之外，因此「详情页里有哪些节点」只能从
  // `#adBody` / 保存条这些容器往下找（与真实 DOM 同一个判据）。
  const harness = {
    api,
    calls,
    queries,
    catalogs: context.AlibotI18n.catalogs,
    i18n: context.AlibotI18n,
    toastWrap: elementFor("#toastWrap"),
    elementFor,
    memberList: elementFor("#memberList"),
    dock: elementFor("#dock"),
    dockAgent: elementFor("#dockAgent"),
    dockTask: elementFor("#dockTask"),
    viewAgent: elementFor("#viewAgent"),
    agentBody: elementFor("#adBody"),
    saveBar: elementFor("#adSaveBar"),
    saveButton: elementFor("#adSave"),
    discardButton: elementFor("#adDiscard"),
    saveError: elementFor("#adSaveError"),
    deleteOverlay: elementFor("#agentDeleteOverlay"),
    deleteText: elementFor("#agentDeleteText"),
    deleteConfirm: elementFor("#agentDeleteConfirm"),
    deleteCancel: elementFor("#agentDeleteCancel"),
    deleteClose: elementFor("#agentDeleteClose"),
    deleteTitle: elementFor("#agentDeleteModalTitle"),
    reportName: elementFor("#adName"),
    reportStatus: elementFor("#adStatusText"),
    reportPresence: elementFor("#adPresence"),
    patches() {
      return calls.filter((call) => call.method === "PATCH");
    },
    deletes() {
      return calls.filter((call) => call.method === "DELETE");
    },
    /** 把一批 Agent 装进工作台镜像（左栏与详情都从它取数）。 */
    load(agents) {
      api.state.agents = agents;
      api.state.members = [member, ...agents.map((item) => ({ ...item }))];
      api.renderMembers();
    },
    /** 点左栏某一行（行是 `<button>`；监听在 `#memberList` 上，因此带着真实 target 派发）。
        行按 `dataset.member` 找——替身的选择器只支持 `[name]` 形式，不支持带值的属性选择器。 */
    async clickRow(agentId) {
      const row = byDataset(harness.memberList, "member", agentId);
      assert.ok(row, "左栏应当有这一行：" + agentId);
      await harness.memberList.dispatchEvent({ type: "click", target: row });
      await flush();
    },
    /** 在详情页里改一个字段：写控件值 + 派发 `input`（文本 / 数字）或 `change`（勾选 / 下拉）。 */
    async edit(field, value) {
      const control = agentField(harness.agentBody, field);
      assert.ok(control, "配置区块缺少可编辑字段：" + field);
      if (control.type === "checkbox") control.checked = Boolean(value);
      else control.value = value;
      const type = control.type === "checkbox" ? "change" : "input";
      await harness.agentBody.dispatchEvent({ type, target: control });
      await flush();
      return control;
    },
    /** 点详情页里的某个控件（监听挂在 `#adBody` 上，因此带真实 target 派发）。 */
    async clickInBody(node) {
      assert.ok(node, "要点的控件不存在");
      await harness.agentBody.dispatchEvent({ type: "click", target: node });
      await flush();
    },
    /** 点底部保存条上的某个按钮（监听直接绑在按钮上）。 */
    async clickSaveBar(node) {
      await node.dispatchEvent({ type: "click" });
      await flush();
    },
    /** 点详情页头部的关闭控件（真实用户出口）。 */
    async closeDetail() {
      await harness.elementFor("#btnAgentDetailClose").dispatchEvent({ type: "click" });
      await flush();
    },
    /** 点危险区「删除」：只打开确认弹窗，不发请求。 */
    async openDeleteModal() {
      await harness.clickInBody(byOdId(harness.agentBody, "agent-delete"));
    },
    /** 确认删除（弹窗里的第二个动作）：监听直接绑在按钮上。 */
    async confirmDelete() {
      await harness.deleteConfirm.dispatchEvent({ type: "click" });
      await flush();
    },
    /** 全局键盘：Escape 是弹窗的键盘出口（监听挂在 `document` 上）。 */
    async pressEscape() {
      await document.dispatchEvent({ type: "keydown", key: "Escape" });
      await flush();
    },
    /** 点弹窗遮罩（只有 target 就是 overlay 本身才算）。 */
    async clickDeleteOverlay() {
      await harness.deleteOverlay.dispatchEvent({ type: "click", target: harness.deleteOverlay });
      await flush();
    },
  };
  return harness;
}

/** 按 `data-*` 找一个节点：`dataset.x` 与 `setAttribute('data-x', …)` 两种写法都要认。 */
function byDataset(root, key, value) {
  const attribute = "data-" + key.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
  return descendants(root).find((node) => {
    if (node.dataset && node.dataset[key] === value) return true;
    return Boolean(node.attributes && node.attributes[attribute] === value);
  }) || null;
}

/** 按 `data-od-id` 找一个节点（区块标题 / 档位组 / 暂停按钮都靠它定位）。 */
function byOdId(root, odId) {
  return descendants(root).find((node) => {
    const value = node.getAttribute ? node.getAttribute("data-od-id") : null;
    return value === odId;
  }) || null;
}

/** 详情页里某个 `data-od-id` 标题所属的区块（标题是区块的第一个子节点）。 */
function sectionOf(root, odId) {
  const title = byOdId(root, odId);
  return title && title.parentNode ? title.parentNode : null;
}

/** 详情页里某个可编辑字段的控件（`data-agent-field` 的取值与 PATCH 字段同名）。 */
function agentField(root, field) {
  return descendants(root).find((node) => {
    if (node.dataset && node.dataset.agentField === field) return true;
    return Boolean(node.attributes && node.attributes["data-agent-field"] === field);
  }) || null;
}

/** 指向某个 `id` 的 `<label for>`（替身选择器不支持带值的属性选择器，因此手写这一条）。 */
function byLabelFor(root, id) {
  return descendants(root).find((node) => node.tagName === "label" && node.getAttribute("for") === id) || null;
}

/** 按 class 收集子树里的节点（替身选择器不支持 `.a .b` 这类后代选择器）。 */
function byClass(root, name) {
  return descendants(root).filter((node) => node.classList && node.classList.contains(name));
}

/** 概览四个数字（`.stat` 的 `n` 是第一个子节点）。 */
function statValues(root) {
  return byClass(root, "stat").map((cell) => cell.children[0].textContent);
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function catalogText(harness, locale, key) {
  return harness.catalogs[locale][key];
}

function readStudio(file) {
  return fs.readFileSync(path.join(STUDIO_DIR, file), "utf8");
}

/** 把 toast 的正文在挂进 `#toastWrap` 的那一刻记下来：替身把 `setTimeout` 折成立即执行，
    淡出定时器会在同一条断言路上把节点摘掉，因此不能事后读容器。 */
function recordToasts(harness) {
  const seen = [];
  const append = harness.toastWrap.appendChild.bind(harness.toastWrap);
  harness.toastWrap.appendChild = (node) => {
    seen.push(node.textContent);
    return append(node);
  };
  return seen;
}

/** 一个把工作台摆成「有频道、当前在频道视图」的 harness。 */
function withChannel(options) {
  const harness = createHarness(options);
  harness.api.state.channels = [channel("ch-1", "设计")];
  harness.api.state.dms = [];
  harness.api.state.activeChannelId = "ch-1";
  harness.api.state.mode = "channel";
  harness.api.setViewMode("channel");
  return harness;
}

/* ─── 1. 打开详情：主线视图 + dock 让位 ─────────────────────────────────── */

test("点左栏 Agent 行打开整页详情：主线切到 agent 视图，dock 让位", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  harness.api.render();
  harness.api.toggleDock(); // dock 默认是收起的（`index.html` 的 `hidden`），人显式打开

  assert.equal(harness.viewAgent.hidden, true, "打开前整页详情不可见");
  assert.equal(harness.dock.hidden, false, "前提：频道视图下 dock 是开着的");

  await harness.clickRow("mem-1");

  assert.equal(harness.api.state.mode, "agent", "详情是一条主线视图，不是 dock 的形态");
  assert.equal(harness.api.state.activeAgentId, "mem-1");
  assert.equal(harness.viewAgent.hidden, false, "详情页可见");
  assert.equal(harness.dock.hidden, true, "整条右侧归详情，dock 让位");
  // 频道头（标题 + 消息 / 任务页签）属于频道内容，详情页里不该压在它上面。
  assert.equal(harness.elementFor("#chanHead").hidden, true, "频道头随详情让位");
  assert.equal(harness.elementFor("#composer").hidden, true, "输入框随详情让位");
  assert.equal(harness.reportName.textContent, "小明");
  // 打开时读一次正本（工作台快照里的数字可能已经旧了）。
  assert.ok(
    harness.calls.some((call) => call.url === "/api/agents/mem-1" && call.method === "GET"),
    "打开详情要 GET /api/agents/{id}",
  );
  // 进入前的视图与 dock 开合被记住（关闭时按它恢复）。
  assert.equal(harness.api.state.agentReturn.mode, "channel");
  assert.equal(harness.api.state.agentReturn.dockOpen, true);
});

test("成员面板里的 Agent 名字也打开整页详情（「加入」按钮自己的动作不被吞掉）", async () => {
  const harness = withChannel();
  harness.api.state.channelMembers = {
    "ch-1": [
      { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 },
      { id: "mem-1", kind: "agent", display_name: "小明", avatar_slot: 1 },
    ],
  };
  harness.load([agentDetail()]);
  harness.api.renderDock();
  const panel = harness.elementFor("#dockMembers");
  const nameButton = byDataset(panel, "agentOpen", "mem-1");
  assert.ok(nameButton, "成员卡片里的名字是打开详情的控件");

  await panel.dispatchEvent({ type: "click", target: nameButton });
  await flush();

  assert.equal(harness.api.state.mode, "agent");
  assert.equal(harness.viewAgent.hidden, false);
  assert.equal(harness.dock.hidden, true);
});

test("运行面板开着时打开详情：先收掉 run 面板（否则整体重绘会把它盖回来）", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  harness.api.state.runPanel.open = true;

  await harness.clickRow("mem-1");

  assert.equal(harness.api.state.runPanel.open, false, "run 面板必须被收掉");
  assert.equal(harness.api.state.mode, "agent");
  assert.equal(harness.viewAgent.hidden, false);
});

/* ─── 2. 区块：一页铺开，没有页签 ───────────────────────────────────────── */

test("所有区块在同一页纵向铺开：统计 / 系统提示 / 配置 / 全局 MCP / 危险操作", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const ids = [
    "agent-detail-section-facts",
    "agent-detail-section-prompt",
    "agent-detail-section-config",
    "agent-detail-section-global-mcp",
    "agent-detail-section-danger",
  ];
  const order = descendants(harness.agentBody)
    .map((node) => (node.getAttribute ? node.getAttribute("data-od-id") : null))
    .filter((id) => ids.indexOf(id) >= 0);
  assert.deepEqual(order, ids, "五处区块按顺序同时出现在同一页里，不需要切换");
  assert.equal(
    harness.agentBody.children[harness.agentBody.children.length - 1].children[0].getAttribute("data-od-id"),
    "agent-detail-section-danger",
    "危险操作是页面最后一个区块（在全局 MCP 之后）",
  );

  // 系统提示的**控件本身**就夹在统计与配置之间（不是「标题在中间、控件在别处」）。
  const flat = descendants(harness.agentBody);
  const at = (odId) => flat.indexOf(byOdId(harness.agentBody, odId));
  assert.ok(
    at("agent-detail-section-facts") < at("agent-detail-prompt")
      && at("agent-detail-prompt") < at("agent-detail-section-config"),
    "系统提示的文本框位于统计区之后、配置区之前",
  );

  // 危险区的三段内容：标题（危险语义）+ 危险说明 + 暂停 / 恢复与删除两个动作。
  const danger = sectionOf(harness.agentBody, "agent-detail-section-danger");
  assert.equal(byOdId(danger, "agent-detail-section-danger").textContent,
    catalogText(harness, "zh-CN", "agentDetail.sectionDanger"));
  assert.match(danger.textContent, /危险/, "危险说明本身要出现「危险」语义");
  assert.ok(byDataset(danger, "agentToggle", "mem-1"), "暂停 / 恢复按钮在危险区里");
  assert.ok(byDataset(danger, "agentDelete", "mem-1"), "删除按钮在危险区里");
  // 删除确认搬进弹窗后，危险区里不再有内联确认区，也不再重复一行「暂停只写配置」。
  assert.equal(
    descendants(danger).some((node) => node.dataset && node.dataset.agentDeleteConfirm !== undefined),
    false,
    "危险区没有内联确认区",
  );
  assert.equal(descendants(danger).some((node) => node.classList.contains("danger-confirm")), false,
    "危险区不再有 .danger-confirm 盒子");
  assert.equal(danger.textContent.includes("暂停只写配置"), false, "界面不再重复这句提示");

  // 配置区块不再渲染暂停 / 恢复按钮与那句代价说明。
  const config = sectionOf(harness.agentBody, "agent-detail-section-config");
  assert.equal(byDataset(config, "agentToggle", "mem-1"), null, "配置区块里不再有暂停 / 恢复按钮");
  assert.equal(config.textContent.includes("暂停只写配置"), false);
});

test("配置区块不再有「改动先留在草稿里」那句提示", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const config = byOdId(harness.agentBody, "agent-detail-section-config").parentNode;
  assert.equal(config.children[0].getAttribute("data-od-id"), "agent-detail-section-config");
  assert.equal(
    config.children[1].classList.contains("hint"),
    false,
    "标题下的第一块不再是一句提示，而是第一个字段",
  );
});

test("删除确认弹窗是 index.html 里的既有模态骨架，危险区不再有内联确认节点", () => {
  const html = readStudio("index.html");
  assert.match(html, /<div class="overlay" id="agentDeleteOverlay" hidden>/);
  assert.match(html, /<div class="modal" role="dialog" aria-modal="true" aria-labelledby="agentDeleteModalTitle">/);
  assert.match(html, /id="agentDeleteModalTitle"[^>]*data-i18n="agentDetail\.deleteTitle"/);
  assert.match(html, /id="agentDeleteText"[^>]*data-od-id="agent-delete-confirm"/);
  assert.match(html, /id="agentDeleteCancel"[^>]*data-i18n="agentDetail\.deleteCancel"/);
  assert.match(html, /id="agentDeleteConfirm"[^>]*data-i18n="agentDetail\.deleteConfirmAction"/);
  assert.match(html, /id="agentDeleteClose"[^>]*data-i18n-attr="aria-label:common\.close"/);
  assert.equal(/class="danger-confirm"/.test(html), false, "HTML 里没有内联确认盒子");
  assert.equal(/data-agent-delete-confirm/.test(html), false, "删除确认不再是内联节点");
});

test("危险区不再消费 .danger-confirm 盒子样式，确认文案样式仍被弹窗复用", () => {
  const css = readStudio("app.css");
  assert.equal(/^\.danger-confirm\s*\{/m.test(css), false, ".danger-confirm 盒子样式已删除");
  assert.match(css, /^\.danger-confirm-text\s*\{/m, "确认文案样式保留给弹窗正文");
});

test("正本里不再有页签与编辑弹窗（index.html）", () => {
  const html = readStudio("index.html");
  assert.equal(/data-adtab=/.test(html), false, "页签数据属性已删除");
  assert.equal(/id="adTabOverview"/.test(html), false);
  assert.equal(/id="adTabRuntime"/.test(html), false);
  assert.equal(/class="dock-tabs"/.test(html), false, "页签容器已删除");
  assert.equal(/id="dockDetail"/.test(html), false, "详情不再是 dock 的形态");
  assert.equal(/id="agentEditOverlay"/.test(html), false, "编辑配置弹窗已删除");
  // 新形态的正本：整页视图 + 底部保存条。
  assert.match(html, /id="viewAgent"[^>]*hidden/, "整页详情是主线视图（默认隐藏）");
  assert.match(html, /id="adSaveBar"[^>]*hidden/, "保存条默认不出现");
  assert.match(html, /id="adSave"/);
  assert.match(html, /id="adDiscard"/);
});

test("运行时不出现对已删编辑弹窗节点的 $() 调用", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await harness.edit("display_name", "小明明");
  await harness.clickSaveBar(harness.saveButton);
  await harness.clickSaveBar(harness.discardButton);
  await harness.closeDetail();

  const offenders = harness.queries.filter((selector) => selector.indexOf("#agentEdit") === 0);
  assert.deepEqual(offenders, [], "已删节点的选择器不许再被查询（运行时报 null 就是回归）");
});

/* ─── 3. 就地编辑：草稿 → 保存条 → 整份提交 / 放弃 ───────────────────────── */

test("就地编辑只写草稿：改一个字段后出现保存条，点保存一次性提交全部可写字段", async () => {
  const renamed = agentDetail({ display_name: "小明明", role: "后端工程师" });
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const harness = withChannel({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "PATCH") {
        // 响应形状与 `POST /api/agents` 同形：`agent` + `agents` + `members`（左栏与成员面板一次刷新）。
        return {
          ok: true,
          status: 200,
          json: async () => ({ agent: renamed, agents: [renamed], members: [owner, renamed] }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  assert.equal(harness.saveBar.hidden, true, "没有改动时不出现保存条");

  await harness.edit("display_name", "小明明");
  await harness.edit("role", "后端工程师");

  assert.equal(harness.saveBar.hidden, false, "有未保存的改动 ⇒ 保存条出现");
  assert.equal(harness.patches().length, 0, "输入只写草稿，不直接发 PATCH");
  assert.equal(harness.agentBody.textContent.includes("有未保存的改动"), false);

  await harness.clickSaveBar(harness.saveButton);

  const patch = harness.patches();
  assert.equal(patch.length, 1);
  assert.equal(patch[0].url, "/api/agents/mem-1");
  assert.deepEqual(patch[0].body, {
    display_name: "小明明",
    role: "后端工程师",
    model_id: "ollama:qwen2.5",
    system_prompt: "你只回答一句话。",
    enabled: true,
    auto_claim: false,
    memory_mode: "off",
    context_window_messages: null,
    context_trigger_ratio: null,
    react_max_iters: null,
    web_access: null,
  });
  assert.equal(harness.saveBar.hidden, true, "保存成功后保存条消失");
  assert.equal(harness.reportName.textContent, "小明明");
  assert.equal(harness.memberList.textContent.includes("小明明"), true, "左栏名字跟着后端读数走");
});

test("「放弃」丢弃草稿：字段回到后端读数，保存条消失，不发 PATCH", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  await harness.edit("display_name", "改坏了");
  assert.equal(harness.saveBar.hidden, false);

  await harness.clickSaveBar(harness.discardButton);

  assert.equal(agentField(harness.agentBody, "display_name").value, "小明", "草稿丢弃，按后端读数重铺");
  assert.equal(harness.saveBar.hidden, true, "保存条消失");
  assert.equal(harness.patches().length, 0, "放弃不发 PATCH");
});

test("后端整体拒绝时错误留在保存条里、输入不丢、保存条仍在", async () => {
  const harness = withChannel({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "PATCH") {
        return {
          ok: false,
          status: 400,
          json: async () => ({
            error: { code: "validation_failed", message: "Agent 配置校验未通过，未改动任何字段", fields: {} },
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  await harness.edit("display_name", "不该生效");
  await harness.clickSaveBar(harness.saveButton);

  assert.equal(harness.saveBar.hidden, false, "失败不收起保存条");
  assert.equal(harness.saveError.hidden, false);
  assert.equal(harness.saveError.textContent, "Agent 配置校验未通过，未改动任何字段");
  assert.equal(agentField(harness.agentBody, "display_name").value, "不该生效", "输入不丢");
  assert.equal(harness.reportName.textContent, "小明", "界面不声称一个没落库的取值");
});

test("暂停 / 恢复是 enabled 的唯一界面入口：配置区没有「启用状态」档位，按钮只改草稿", async () => {
  const paused = agentDetail({ enabled: false, status: "offline", enabled_reason: "该 Agent 的模型配置已停用" });
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const harness = withChannel({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "PATCH") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ agent: paused, agents: [paused], members: [owner, paused] }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  // 配置区不再有「启用状态」档位（用户 2026-10-24 定夺）：`enabled` 的界面入口只剩危险区按钮。
  const config = sectionOf(harness.agentBody, "agent-detail-section-config");
  assert.equal(byOdId(config, "agent-config-enabled"), null, "配置区不再渲染启用状态档位组");
  assert.equal(byDataset(config, "agentTier", "enabled"), null, "配置区没有 enabled 的档位组");
  const configLabels = byClass(config, "field").map((field) => field.children[0].textContent);
  assert.equal(configLabels.includes("启用状态"), false, "配置区不再有「启用状态」字段行");

  const toggle = byOdId(harness.agentBody, "agent-toggle-enabled");
  assert.ok(toggle, "危险区要有暂停 / 恢复按钮");
  assert.ok(
    sectionOf(harness.agentBody, "agent-detail-section-danger").contains(toggle),
    "暂停 / 恢复按钮长在危险区里（配置区块不再渲染它）",
  );
  assert.equal(toggle.textContent, catalogText(harness, "zh-CN", "agentDetail.pause"));

  await harness.clickInBody(toggle);

  assert.equal(harness.patches().length, 0, "按钮只写草稿，不直接发 PATCH");
  assert.equal(harness.saveBar.hidden, false, "点按钮产生未保存改动 ⇒ 保存条出现");
  assert.equal(byOdId(harness.agentBody, "agent-toggle-enabled").textContent,
    catalogText(harness, "zh-CN", "agentDetail.resume"), "按钮说法跟着草稿走");

  await harness.clickSaveBar(harness.saveButton);

  assert.equal(harness.patches()[0].body.enabled, false, "保存条才把 enabled 提交给后端");
  assert.equal(harness.reportPresence.dataset.status, "offline");
  assert.equal(harness.reportStatus.textContent, catalogText(harness, "zh-CN", "status.offline"));
});

test("点「删除」只打开确认弹窗：零请求、文案带名称与不可撤销；取消 / Escape / 点遮罩都收起且零请求", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const deleteButton = byOdId(harness.agentBody, "agent-delete");
  assert.ok(deleteButton, "危险区要有删除按钮");
  assert.equal(deleteButton.disabled, false);
  assert.equal(harness.deleteOverlay.hidden, true, "打开详情时弹窗关着");

  await harness.openDeleteModal();

  assert.equal(harness.deletes().length, 0, "打开弹窗**不**发 DELETE");
  assert.equal(harness.deleteOverlay.hidden, false, "确认弹窗可见");
  assert.ok(harness.deleteText.textContent.includes("小明"), "确认文案带 Agent 名称");
  assert.match(harness.deleteText.textContent, /不可撤销/, "确认文案点明不可逆后果");
  assert.equal(harness.api.state.activeAgentId, "mem-1", "打开弹窗不关闭详情");
  assert.equal(harness.deleteCancel.focused, true, "焦点落在安全的「取消」上");
  assert.equal(harness.agentBody.querySelector("[data-agent-delete-confirm]"), null,
    "详情页里不再有内联确认区");

  await harness.deleteCancel.dispatchEvent({ type: "click" });
  await flush();
  assert.equal(harness.deleteOverlay.hidden, true, "取消收起弹窗");
  assert.equal(harness.deletes().length, 0, "取消不发请求");
  assert.equal(harness.api.state.activeAgentId, "mem-1", "取消不关闭详情");

  await harness.openDeleteModal();
  await harness.pressEscape();
  assert.equal(harness.deleteOverlay.hidden, true, "Escape 收起弹窗");
  assert.equal(harness.deletes().length, 0, "Escape 不发请求");
  assert.equal(harness.api.state.activeAgentId, "mem-1", "Escape 不关闭详情");

  await harness.openDeleteModal();
  await harness.clickDeleteOverlay();
  assert.equal(harness.deleteOverlay.hidden, true, "点遮罩收起弹窗");
  assert.equal(harness.deletes().length, 0, "点遮罩不发请求");

  await harness.openDeleteModal();
  await harness.deleteClose.dispatchEvent({ type: "click" });
  await flush();
  assert.equal(harness.deleteOverlay.hidden, true, "关闭图标收起弹窗");
  assert.equal(harness.deletes().length, 0, "关闭图标不发请求");
});

test("确认删除只发一次 DELETE：成功后弹窗收起、详情关闭、列表刷新、toast 可读", async () => {
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const harness = withChannel({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "DELETE") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ deleted: "mem-1", agents: [], members: [owner] }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  // 频道成员面板 / @ 补全读的是这份后端镜像；删除后它也必须立刻摘掉该成员。
  harness.api.state.channelMembers = { "ch-1": [owner, agentDetail()] };
  await harness.clickRow("mem-1");

  await harness.openDeleteModal();
  await harness.confirmDelete();

  const deletes = harness.deletes();
  assert.equal(deletes.length, 1, "确认只发一次请求");
  assert.equal(deletes[0].url, "/api/agents/mem-1");
  assert.equal(harness.deleteOverlay.hidden, true, "删除成功后弹窗收起");
  assert.equal(harness.api.state.activeAgentId, null, "删除成功后关闭详情");
  assert.equal(harness.viewAgent.hidden, true, "回到进入前的视图");
  assert.equal(harness.api.state.agents.length, 0);
  assert.equal(harness.api.state.members.some((member) => member.id === "mem-1"), false,
    "本地成员镜像里移除该成员");
  assert.equal(harness.memberList.textContent.includes("小明"), false, "左栏刷新后不再有它");
  assert.equal(harness.elementFor("#dockMembers").textContent.includes("小明"), false,
    "成员面板（频道成员缓存）同步刷新");
  const expectedToast = catalogText(harness, "zh-CN", "toast.agentDeleted").replace("{name}", "小明");
  assert.ok(harness.toastWrap.textContent.includes(expectedToast), "删除成功后给出可读 toast");
});

test("删除请求在途时按钮禁用：请求没回来之前不能重复提交", async () => {
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  let release = () => {};
  const gate = new Promise((resolve) => { release = resolve; });
  const harness = withChannel({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "DELETE") {
        await gate;
        return {
          ok: true,
          status: 200,
          json: async () => ({ deleted: "mem-1", agents: [], members: [owner] }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await harness.openDeleteModal();
  await harness.confirmDelete();

  assert.equal(harness.deletes().length, 1);
  assert.equal(byOdId(harness.agentBody, "agent-delete").disabled, true, "请求中删除按钮禁用");
  assert.equal(harness.deleteConfirm.disabled, true, "请求中确认按钮禁用");
  assert.equal(harness.deleteCancel.disabled, true, "请求中取消按钮禁用");
  assert.equal(harness.deleteClose.disabled, true, "请求中关闭图标禁用");
  assert.equal(harness.deleteOverlay.hidden, false, "请求在途时弹窗保持打开");
  assert.equal(harness.api.state.activeAgentId, "mem-1", "请求没回来之前详情仍在");

  release();
  for (let tick = 0; tick < 4; tick += 1) await flush();
  assert.equal(harness.deleteOverlay.hidden, true, "请求返回后弹窗收起");
  assert.equal(harness.api.state.activeAgentId, null, "请求返回后按成功路径关闭详情");
});

test("删除失败留在当前页、弹窗不收起、原因不静默吞掉", async () => {
  const harness = withChannel({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "DELETE") {
        return {
          ok: false,
          status: 500,
          json: async () => ({ error: { code: "internal", message: "删除时出错，未改动任何数据" } }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  await harness.openDeleteModal();
  await harness.confirmDelete();

  assert.equal(harness.deletes().length, 1);
  assert.equal(harness.deleteOverlay.hidden, false, "失败时弹窗保持打开");
  assert.equal(harness.deleteConfirm.disabled, false, "失败后确认按钮恢复可用");
  assert.equal(harness.deleteCancel.disabled, false, "失败后取消按钮恢复可用");
  assert.equal(harness.api.state.activeAgentId, "mem-1", "失败不关闭详情");
  assert.equal(harness.api.state.agents.some((agent) => agent.id === "mem-1"), true,
    "失败不改本地成员镜像");
  assert.ok(
    harness.toastWrap.textContent.includes("删除时出错，未改动任何数据"),
    "失败原因按后端那一句显示出来",
  );
});

test("离线时暂停与删除都禁用", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  harness.api.state.online = false;
  harness.api.renderAgentDetail();

  const toggle = byOdId(harness.agentBody, "agent-toggle-enabled");
  const deleteButton = byOdId(harness.agentBody, "agent-delete");
  assert.ok(toggle, "离线时暂停按钮仍在（只是不可用）");
  assert.ok(deleteButton, "离线时删除按钮仍在（只是不可用）");
  assert.equal(toggle.disabled, true, "离线时暂停禁用");
  assert.equal(deleteButton.disabled, true, "离线时删除禁用");
});

test("危险区与删除弹窗都有英文取值：切到 English 后同样可见", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  harness.i18n.setLocale("en");
  try {
    harness.api.renderAgentDetail();
    const danger = sectionOf(harness.agentBody, "agent-detail-section-danger");
    assert.equal(byOdId(danger, "agent-detail-section-danger").textContent,
      catalogText(harness, "en", "agentDetail.sectionDanger"));
    assert.ok(danger.textContent.includes(catalogText(harness, "en", "agentDetail.dangerHint")));
    assert.equal(byOdId(danger, "agent-delete").textContent,
      catalogText(harness, "en", "agentDetail.delete"));

    await harness.openDeleteModal();
    assert.equal(harness.deleteOverlay.hidden, false, "英文下弹窗同样可见");
    const expectedText = catalogText(harness, "en", "agentDetail.deleteConfirm")
      .replace("{name}", "小明");
    assert.equal(harness.deleteText.textContent, expectedText, "确认文案按英文词条渲染");
    // 标题 / 两个按钮是 `index.html` 的静态节点（由 `applyStaticI18n` 铺），它们的中英键集
    // 由 `i18n_ui.test.cjs` 的静态绑定守卫覆盖，这里只确认替身里弹窗确实开着。
  } finally {
    harness.i18n.setLocale("zh-CN");
  }
});

test("危险说明与确认文案说「移出列表与全部频道」，不再声称「从工作台隐藏」", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  // 只读危险说明那一个节点：区块 textContent 里还含着（隐藏的）确认区，混在一起会让断言失真。
  // 每次重查（切语言后整页重绘，节点会换）。
  const hintText = () => byClass(sectionOf(harness.agentBody, "agent-detail-section-danger"),
    "danger-hint")[0].textContent;

  // 源语言：危险说明与确认文案都要点明删掉的是「列表 + 全部频道」，保留私信 / 历史 / 任务。
  const zhHint = hintText();
  for (const phrase of ["Agent 列表", "全部频道", "私信", "历史消息", "任务保留"]) {
    assert.ok(zhHint.includes(phrase), "危险说明要写明：" + phrase);
  }
  assert.equal(zhHint.includes("从工作台隐藏"), false, "不再声称删除会「从工作台隐藏」");

  await harness.openDeleteModal();
  const zhConfirm = harness.deleteText.textContent;
  for (const phrase of ["Agent 列表", "全部频道", "私信", "历史消息"]) {
    assert.ok(zhConfirm.includes(phrase), "确认文案要写明：" + phrase);
  }
  assert.equal(zhConfirm.includes("从工作台隐藏"), false, "确认文案同样不声称「从工作台隐藏」");

  // 第二语言同一口径（DM target 与私信历史保留是本票既有语义，文案不得与实现冲突）。
  harness.i18n.setLocale("en");
  try {
    harness.api.renderAgentDetail();
    await harness.openDeleteModal();
    const enHint = hintText();
    const enConfirm = harness.deleteText.textContent;
    for (const phrase of ["agent list", "every channel", "DMs", "message history"]) {
      assert.ok(enHint.includes(phrase), "English danger hint must say: " + phrase);
      assert.ok(enConfirm.includes(phrase), "English confirmation must say: " + phrase);
    }
    assert.equal((enHint + enConfirm).includes("hides it from the workspace"), false,
      "English copy must not claim the agent is hidden from the workspace");
  } finally {
    harness.i18n.setLocale("zh-CN");
  }
});

test("任务指派者已不在成员表里：成员表成功载入过才说「已删除」", async () => {
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const gone = "mem_gone_0a1b";
  const task = {
    id: "task_1",
    title: "遗留任务",
    status: "todo",
    channel_name: "设计",
    assignee_member_id: gone,
    unread_count: 0,
    pending_approval_count: 0,
    allowed_transitions: ["in_progress"],
  };
  let workspaceAttempts = 0;
  const harness = withChannel({
    fetch: async (url) => {
      if (String(url) === "/api/workspace") {
        workspaceAttempts += 1;
        // 首载失败（与真实断线同一形状：`state.ready` 也会置 true）；重连那一次成功。
        if (workspaceAttempts === 1) {
          return {
            ok: false,
            status: 503,
            json: async () => ({ error: { code: "unavailable", message: "工作台快照暂时读不到" } }),
          };
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            member: owner,
            members: [owner],
            channels: [],
            dms: [],
            agents: [],
            model_providers: ["ollama"],
            local_providers: ["ollama"],
            remote_consents: [],
          }),
        };
      }
      if (String(url).endsWith("/messages")) {
        return { ok: true, status: 200, json: async () => ({ messages: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });

  // 阶段一：首载失败。失败分支会置 `state.ready = true`（所以它不能当「成员表已就绪」的判据），
  // 但 `workspaceLoaded` 只在整轮读数都落地后才置位 —— 断言只看外部可观察行为。
  await harness.api.loadWorkspace({ silent: true });
  assert.equal(harness.api.state.online, false, "首载失败的旁证：工作台被标成离线");

  harness.api.state.members = [owner];
  harness.api.state.agents = [];
  harness.api.state.boardTasks = [task];
  harness.api.state.tasks = [task];
  harness.api.render();

  const loading = harness.elementFor("#taskColumns");
  assert.ok(loading.textContent.includes(catalogText(harness, "zh-CN", "toast.memberFallback")),
    "未成功载入时用中性「成员」占位");
  assert.equal(loading.textContent.includes(catalogText(harness, "zh-CN", "task.deletedAssignee")), false,
    "未成功载入时不误报「已删除」");
  assert.equal(loading.textContent.includes(gone), false, "裸成员 id 不外露");

  // 阶段二：重连成功（走产品自己的重连路径）⇒「查不到」才解释成「已删除」。
  for (let tick = 0; tick < 20 && !harness.api.state.online; tick += 1) await flush();
  assert.ok(workspaceAttempts >= 2, "确实经历了一次失败的首载与一次成功的重连");
  assert.equal(harness.api.state.online, true, "重连后工作台恢复在线");

  harness.api.state.members = [owner];
  harness.api.state.boardTasks = [task];
  harness.api.state.tasks = [task];
  harness.api.render();

  const board = harness.elementFor("#taskColumns");
  assert.ok(board.textContent.includes(catalogText(harness, "zh-CN", "task.deletedAssignee")),
    "已成功载入且成员查不到 ⇒ 「已删除的成员」");
  assert.equal(board.textContent.includes(gone), false, "裸成员 id 不外露");
});

test("联网档四档：未配置时选中「跟随频道」，点具体档位后保存提交该档", async () => {
  const saved = agentDetail({ web_access: "search+fetch" });
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const harness = withChannel({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "PATCH") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ agent: saved, agents: [saved], members: [owner, saved] }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const group = byOdId(harness.agentBody, "agent-config-web-access");
  const follow = byDataset(group, "agentAccess", "follow");
  const searchFetch = byDataset(group, "agentAccess", "search+fetch");
  assert.ok(follow && searchFetch, "配置区块要有 follow / off / search / search+fetch 四档");
  assert.equal(follow.classList.contains("is-on"), true, "`agent.web_access=null` = 没有覆盖");

  await harness.clickInBody(searchFetch);
  assert.equal(searchFetch.classList.contains("is-on"), true);
  assert.equal(follow.classList.contains("is-on"), false, "四档互斥");

  await harness.clickSaveBar(harness.saveButton);
  assert.equal(harness.patches()[0].body.web_access, "search+fetch");
});

test("联网档：从具体档位切回「跟随频道」提交显式 null（清除覆盖）", async () => {
  const configured = agentDetail({ web_access: "off" });
  const harness = withChannel({
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ agent: configured }) }),
  });
  harness.load([configured]);
  await harness.clickRow("mem-1");

  const group = byOdId(harness.agentBody, "agent-config-web-access");
  assert.equal(byDataset(group, "agentAccess", "off").classList.contains("is-on"), true,
    "选中态读后端读数 `web_access='off'`");

  await harness.clickInBody(byDataset(group, "agentAccess", "follow"));
  await harness.clickSaveBar(harness.saveButton);

  const body = harness.patches()[0].body;
  assert.equal("web_access" in body && body.web_access, null, "跟随频道 = 提交显式 null");
});

test("长期记忆与上下文配置：读写草稿，清空数值提交显式 null", async () => {
  const configured = agentDetail({
    memory_mode: "agentic",
    context_window_messages: 60,
    context_trigger_ratio: 0.6,
    react_max_iters: 45,
  });
  const harness = withChannel({
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ agent: configured }) }),
  });
  harness.load([configured]);
  await harness.clickRow("mem-1");

  assert.equal(agentField(harness.agentBody, "memory_mode").checked, true);
  assert.equal(agentField(harness.agentBody, "context_window_messages").value, "60");
  assert.equal(agentField(harness.agentBody, "context_trigger_ratio").value, "0.6");
  assert.equal(agentField(harness.agentBody, "react_max_iters").value, "45");

  await harness.edit("memory_mode", false);
  await harness.edit("context_window_messages", "");
  await harness.edit("context_trigger_ratio", "");
  await harness.edit("react_max_iters", "");
  await harness.clickSaveBar(harness.saveButton);

  const payload = harness.patches()[0].body;
  assert.equal(payload.memory_mode, "off");
  assert.equal(payload.context_window_messages, null);
  assert.equal(payload.context_trigger_ratio, null);
  assert.equal(payload.react_max_iters, null);
});

test("模型提供商 + 模型名：保存时拼回 provider:model", async () => {
  const harness = withChannel();
  harness.api.state.providers = ["ollama", "openai"];
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const provider = agentField(harness.agentBody, "provider");
  assert.ok(provider, "配置区块要有模型提供商下拉");
  assert.equal(provider.value, "ollama");
  assert.equal(agentField(harness.agentBody, "model").value, "qwen2.5");

  provider.value = "openai";
  await harness.agentBody.dispatchEvent({ type: "change", target: provider });
  await harness.edit("model", "gpt-4o");
  await harness.clickSaveBar(harness.saveButton);

  assert.equal(harness.patches()[0].body.model_id, "openai:gpt-4o");
});

test("远端 provider 的数据流告知块搬进配置区块，切回本机 provider 后收起", async () => {
  const remote = agentDetail({ provider: "openai", model_id: "openai:gpt-4o", is_remote: true });
  const harness = withChannel({
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ agent: remote }) }),
  });
  harness.api.state.providers = ["ollama", "openai"];
  harness.api.state.localProviders = ["ollama"];
  harness.load([remote]);
  await harness.clickRow("mem-1");

  const notice = byOdId(harness.agentBody, "agent-edit-remote-notice");
  assert.ok(notice, "配置区块要有远端数据流告知块");
  assert.equal(notice.hidden, false, "远端 provider 且未确认 ⇒ 告知块可见");
  assert.ok(
    notice.textContent.includes(
      harness.catalogs["zh-CN"]["agent.remoteTitle"]
        .replace("{provider}", catalogText(harness, "zh-CN", "provider.openai")),
    ),
    "「启用远端模型前需要你确认一次」这条既有行为不得丢掉",
  );

  const provider = agentField(harness.agentBody, "provider");
  provider.value = "ollama";
  await harness.agentBody.dispatchEvent({ type: "change", target: provider });
  await flush();

  assert.equal(byOdId(harness.agentBody, "agent-edit-remote-notice").hidden, true,
    "本机 provider 不需要确认");
});

/* ─── 4. 只读区块：统计 / 全局 MCP ─────────────────────────────────────── */

test("统计区块的四个数字与后端读数一致，每个数字都写明来源", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  assert.deepEqual(statValues(harness.agentBody), ["2", "5", "1", "67%"]);
  const hints = byClass(harness.agentBody, "stat").map((cell) => cell.title);
  hints.forEach((hint) => assert.ok(hint && hint.length > 0, "每个数字都要写明来源"));
  assert.equal(hints[0], catalogText(harness, "zh-CN", "agentDetail.statChannelsHint"));
  // 标题是「统计」（取词条值，不写死字面量）。
  const title = byOdId(harness.agentBody, "agent-detail-section-facts");
  assert.equal(title.textContent, catalogText(harness, "zh-CN", "agentDetail.sectionFacts"));
  // 模型标识不再在统计区重复一份（可编辑的那一份在「配置」区块里）。
  assert.equal(byOdId(harness.agentBody, "agent-overview-model"), null, "统计区里没有模型标识行");
  assert.equal(
    title.parentNode.textContent.includes("ollama:qwen2.5"),
    false,
    "统计区不重复呈现模型标识",
  );
});

test("成功率没有样本时显示「—」，不编一个 0%", async () => {
  const harness = withChannel({
    fetch: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        agent: agentDetail({
          status: "offline",
          enabled_reason: "该 Agent 的模型配置已停用",
          stats: { channel_count: 0, round_count: 0, completed_task_count: 0, success_rate: null },
        }),
      }),
    }),
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  assert.equal(statValues(harness.agentBody)[3], catalogText(harness, "zh-CN", "agentDetail.noSamples"));
  // 未启用原因随「当前状态」区块一起从详情页退出（成员名册里那处小字保持原样）。
  assert.equal(
    harness.agentBody.textContent.includes("该 Agent 的模型配置已停用"),
    false,
    "详情页不再显示未启用原因",
  );
});

test("系统提示是配置数据的原文，且就地可编辑", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const prompt = agentField(harness.agentBody, "system_prompt");
  assert.ok(prompt, "系统提示区块要有可编辑控件");
  assert.equal(prompt.value, "你只回答一句话。", "读的是配置原文，不是被改写的一句话");
  assert.ok(byOdId(harness.agentBody, "agent-detail-section-prompt"), "提示区块有自己的稳定标识");
});

test("Agent 详情的全局 MCP 小节是只读行：stdio 不可确认，确认动作按成员 + 名字发出", async () => {
  const servers = [
    { name: "notes", transport: "stdio", is_remote: false, consented: false, runtime: null },
    {
      name: "remote",
      transport: "http",
      is_remote: true,
      consented: false,
      runtime: { connected: false, error: "需要确认远端数据面" },
    },
  ];
  const harness = withChannel({
    fetch: async (url, init) => {
      const method = (init && init.method) || "GET";
      if (String(url) === "/api/agents/mem-1/global-mcp-servers" && method === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ global_mcp_servers: servers.map((server) => ({ ...server })) }),
        };
      }
      if (
        String(url) === "/api/agents/mem-1/global-mcp-servers/remote/consent"
        && method === "POST"
      ) {
        servers[1] = { ...servers[1], consented: true };
        return {
          ok: true,
          status: 200,
          json: async () => ({ global_mcp_server: { ...servers[1] }, consent: {} }),
        };
      }
      if (String(url) === "/api/agents/mem-1") {
        return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const localRow = byDataset(harness.agentBody, "globalMcpServer", "notes");
  const remoteRow = byDataset(harness.agentBody, "globalMcpServer", "remote");
  assert.ok(localRow, "stdio 条目仍要只读展示");
  assert.ok(remoteRow, "远端条目要出现在只读列表里");
  assert.equal(localRow.querySelector("[data-global-mcp-consent]"), null, "stdio 不可确认");
  const confirmButton = remoteRow.querySelector("[data-global-mcp-consent]");
  assert.ok(confirmButton, "远端未确认条目要能确认");
  assert.ok(remoteRow.textContent.includes("需要确认远端数据面"), "运行期读数原样呈现");

  await harness.clickInBody(confirmButton);
  await flush();

  const posts = harness.calls.filter((call) => call.method === "POST");
  assert.deepEqual(
    posts.map((call) => call.url),
    ["/api/agents/mem-1/global-mcp-servers/remote/consent"],
  );
  const refreshed = byDataset(harness.agentBody, "globalMcpServer", "remote");
  assert.equal(refreshed.querySelector("[data-global-mcp-consent]"), null, "确认后按钮消失");
  assert.ok(
    refreshed.textContent.includes(catalogText(harness, "zh-CN", "agentDetail.globalMcpConsented")),
    "确认后要读回已确认",
  );
});

test("全局 MCP 小节不再有「来自家目录 mcp.json」那句提示；空态用新文案", async () => {
  const harness = withChannel({
    fetch: async (url) => {
      if (String(url) === "/api/agents/mem-1/global-mcp-servers") {
        return { ok: true, status: 200, json: async () => ({ global_mcp_servers: [] }) };
      }
      if (String(url) === "/api/agents/mem-1") {
        return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const section = sectionOf(harness.agentBody, "agent-detail-section-global-mcp");
  assert.equal(section.textContent.includes("来自该成员家目录的 mcp.json"), false,
    "那句提示已删除");
  const hints = byClass(section, "hint").map((node) => node.textContent);
  assert.equal(hints.includes("“全局mcp.json”里没有声明MCP服务"), true,
    "空态逐字等于新文案（全角引号、mcp.json 前后无空格）");

  harness.i18n.setLocale("en");
  try {
    harness.api.renderAgentDetail();
    const enHints = byClass(sectionOf(harness.agentBody, "agent-detail-section-global-mcp"), "hint")
      .map((node) => node.textContent);
    assert.equal(enHints.includes("No MCP servers are declared in the global mcp.json."), true,
      "英文空态同步改写");
  } finally {
    harness.i18n.setLocale("zh-CN");
  }
});

test("草稿脏时确认全局 MCP：只读小节照常刷新，人的输入不丢", async () => {
  const servers = [
    { name: "remote", transport: "http", is_remote: true, consented: false, runtime: null },
  ];
  const harness = withChannel({
    fetch: async (url, init) => {
      const method = (init && init.method) || "GET";
      if (String(url) === "/api/agents/mem-1/global-mcp-servers" && method === "GET") {
        return { ok: true, status: 200, json: async () => ({ global_mcp_servers: servers.map((s) => ({ ...s })) }) };
      }
      if (String(url) === "/api/agents/mem-1/global-mcp-servers/remote/consent" && method === "POST") {
        servers[0] = { ...servers[0], consented: true };
        return { ok: true, status: 200, json: async () => ({ global_mcp_server: { ...servers[0] }, consent: {} }) };
      }
      if (String(url) === "/api/agents/mem-1") {
        return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  // 先把草稿弄脏：整页重铺从此停下（保住焦点与输入）。
  await harness.edit("display_name", "打到一半");
  const before = agentField(harness.agentBody, "display_name");
  const row = byDataset(harness.agentBody, "globalMcpServer", "remote");
  await harness.clickInBody(row.querySelector("[data-global-mcp-consent]"));
  await flush();

  assert.equal(
    byDataset(harness.agentBody, "globalMcpServer", "remote").querySelector("[data-global-mcp-consent]"),
    null,
    "确认后按钮消失：只读小节跟着后端读数走",
  );
  assert.equal(agentField(harness.agentBody, "display_name"), before, "输入控件没被换成新节点");
  assert.equal(before.value, "打到一半", "人的输入还在");
  assert.equal(harness.saveBar.hidden, false, "草稿仍脏，保存条还在");
});

/* ─── 5. 关闭 / 切换：恢复进入前的视图与 dock，草稿丢弃 ─────────────────── */

test("关闭详情回到进入前的视图；dock 按进入前的开合恢复", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  harness.api.state.mode = "board";
  harness.api.setViewMode("board");
  harness.api.toggleDock(); // 人显式打开 dock（进入详情前它是开着的）
  assert.equal(harness.dock.hidden, false, "前提：dock 开着");

  await harness.clickRow("mem-1");
  assert.equal(harness.dock.hidden, true);

  await harness.closeDetail();

  assert.equal(harness.api.state.mode, "board", "原来在看板就回看板，不一律跳回频道");
  assert.equal(harness.viewAgent.hidden, true, "详情页让位");
  assert.equal(harness.dock.hidden, false, "dock 恢复到进入前的开合");
  assert.equal(harness.dockAgent.hidden, false, "dock 恢复成频道成员形态");
  assert.equal(harness.api.state.activeAgentId, null);
});

test("从频道进入再关闭：频道头与输入框跟着回来", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);

  await harness.clickRow("mem-1");
  assert.equal(harness.elementFor("#chanHead").hidden, true);

  await harness.closeDetail();

  assert.equal(harness.api.state.mode, "channel");
  assert.equal(harness.elementFor("#chanHead").hidden, false, "回到频道，标题与页签恢复");
  assert.equal(harness.elementFor("#composer").hidden, false, "输入框恢复");
});

test("进入前 dock 是收起的：关闭详情后仍收起，不把成员面板顶出来", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  harness.api.setDockMode("agent");
  // 起点：dock 收起（`index.html` 的 `hidden`）。
  assert.equal(harness.dock.hidden, true);

  await harness.clickRow("mem-1");
  await harness.closeDetail();

  assert.equal(harness.dock.hidden, true, "没有「关闭后 dock 变成空白成员面板」的漂移");
  assert.equal(harness.api.state.mode, "channel");
});

test("无频道时关闭详情：dock 保持收起", async () => {
  const harness = createHarness();
  harness.load([agentDetail()]);
  harness.api.state.channels = [];
  harness.api.state.dms = [];
  harness.api.state.activeChannelId = null;
  harness.api.state.mode = "channel";
  harness.api.setViewMode("channel");

  await harness.clickRow("mem-1");
  await harness.closeDetail();

  assert.equal(harness.dock.hidden, true, "没有可回退的频道 ⇒ 收起");
  assert.equal(harness.api.state.activeAgentId, null);
});

test("未保存改动时切到别的 Agent：草稿丢弃，按新成员的后端读数重铺", async () => {
  const other = agentDetail({ id: "mem-2", display_name: "小红", role: "测试" });
  const harness = withChannel();
  harness.load([agentDetail(), other]);
  await harness.clickRow("mem-1");

  await harness.edit("display_name", "不该跟过去");
  await harness.clickRow("mem-2");

  assert.equal(harness.api.state.activeAgentId, "mem-2");
  assert.equal(agentField(harness.agentBody, "display_name").value, "小红");
  assert.equal(harness.saveBar.hidden, true, "草稿丢弃 ⇒ 没有未保存改动");
});

test("未保存改动时切主线视图：详情关闭、草稿丢弃（不弹二次确认）", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await harness.edit("display_name", "不该留下");

  await harness.api.showMessagesView();

  assert.equal(harness.api.state.mode, "channel");
  assert.equal(harness.api.state.activeAgentId, null, "详情关闭");
  assert.equal(harness.viewAgent.hidden, true);
  assert.equal(harness.saveBar.hidden, true, "保存条跟着消失");
});

test("切看板同样离开整页详情", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  await harness.api.showTaskBoard();

  assert.equal(harness.api.state.mode, "board");
  assert.equal(harness.api.state.activeAgentId, null);
  assert.equal(harness.viewAgent.hidden, true);
});

/* ─── 6. 文案与无障碍 ───────────────────────────────────────────────────── */

test("本票新增 / 沿用的文案在两份词条里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "agentDetail.tag",
    "agentDetail.closeAria",
    "agentDetail.openAria",
    "agentDetail.sectionFacts",
    "agentDetail.statChannels",
    "agentDetail.statChannelsHint",
    "agentDetail.statRounds",
    "agentDetail.statRoundsHint",
    "agentDetail.statTasksDone",
    "agentDetail.statTasksDoneHint",
    "agentDetail.statSuccess",
    "agentDetail.statSuccessHint",
    "agentDetail.noSamples",
    "agentDetail.sectionConfig",
    "agentDetail.kvPrompt",
    "agentDetail.pause",
    "agentDetail.resume",
    "agentDetail.deleteTitle",
    "agentDetail.save",
    "agentDetail.discard",
    "agentDetail.unsaved",
    "agent.nameLabel",
    "agent.roleLabel",
    "agent.providerLabel",
    "agent.modelLabel",
    "agent.modelHintPrefix",
    "agent.modelHintSuffix",
    "agent.promptLabel",
    "agent.promptPlaceholder",
    "agent.autoClaimLabel",
    "agent.autoClaimOn",
    "agent.autoClaimOnHint",
    "agent.autoClaimOff",
    "agent.autoClaimOffHint",
    "agent.autoClaimNote",
    "agent.memoryLabel",
    "agent.memoryHint",
    "agent.contextWindow",
    "agent.contextTrigger",
    "agent.maxIters",
    "agent.runtimeDefaultHint",
    "agent.webAccessLabel",
    "agent.webAccessFollow",
    "agent.webAccessFollowHint",
    "agent.webAccessOff",
    "agent.webAccessOffHint",
    "agent.webAccessSearch",
    "agent.webAccessSearchHint",
    "agent.webAccessSearchFetch",
    "agent.webAccessSearchFetchHint",
    "agent.webAccessNote",
    "agent.remoteTitleStatic",
    "agent.remoteConfirm",
    "agentDetail.globalMcpTitle",
    "agentDetail.globalMcpEmpty",
    "agentDetail.globalMcpRemote",
    "agentDetail.globalMcpLocal",
    "agentDetail.globalMcpConsented",
    "agentDetail.globalMcpConsentPending",
    "agentDetail.globalMcpNoConsent",
    "agentDetail.globalMcpConfirm",
    "agentDetail.globalMcpConfirmed",
    "agentDetail.globalMcpRuntimeIdle",
    "agentDetail.globalMcpRuntimeConnected",
    "agentDetail.sectionDanger",
    "agentDetail.dangerHint",
    "agentDetail.delete",
    "agentDetail.deleteConfirm",
    "agentDetail.deleteConfirmAction",
    "agentDetail.deleteCancel",
    "toast.agentUpdated",
    "toast.agentDetailFailed",
    "toast.agentDeleted",
    "toast.agentDeleteFailed",
    "task.deletedAssignee",
  ];
  for (const key of keys) {
    for (const locale of ["zh-CN", "en"]) {
      const value = harness.catalogs[locale][key];
      assert.ok(typeof value === "string" && value.length > 0, locale + " 缺词条：" + key);
    }
  }
  // 页签词条随页签一起删掉：留着它们等于留一个不存在的界面说法。
  for (const key of ["agentDetail.tabsLabel", "agentDetail.tabOverview", "agentDetail.tabRuntime"]) {
    for (const locale of ["zh-CN", "en"]) {
      assert.equal(harness.catalogs[locale][key], undefined, locale + " 仍有已删页签词条：" + key);
    }
  }
  // 随「当前状态」「运行时」两区块、配置提示与统计区模型标识一起删掉的词条同此。
  for (const key of [
    "agentDetail.sectionStatus",
    "agentDetail.kvStatus",
    "agentDetail.kvReason",
    "agentDetail.sectionRuntime",
    "agentDetail.runtimeNote",
    "agentDetail.configNote",
    "agentDetail.kvModel",
    "agentDetail.kvProvider",
    "agentDetail.kvRemote",
    "agentDetail.remoteYes",
    "agentDetail.remoteNo",
    "agentDetail.kvConsent",
    "agentDetail.consentAck",
    "agentDetail.consentPending",
    "agentDetail.consentNotNeeded",
    "agentDetail.emptyField",
    // 危险区那句「暂停只写配置…」与全局 MCP 那句「来自成员家目录…」随节点一起删除：
    // 词条也一并移除，不留没有消费点的孤儿键。
    "agentDetail.pauseNote",
    "agentDetail.globalMcpNote",
    // 「启用状态」档位随配置区的删除而失去全部消费点：两份词条一并移除，不留孤儿键。
    "agent.enabledLabel",
    "agent.enabledOn",
    "agent.enabledOnHint",
    "agent.enabledOff",
    "agent.enabledOffHint",
  ]) {
    for (const locale of ["zh-CN", "en"]) {
      assert.equal(harness.catalogs[locale][key], undefined, locale + " 仍有已删词条：" + key);
    }
  }
});

test("详情视图的关闭控件与保存条按钮都可键盘触达", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await harness.edit("display_name", "小明明");

  for (const node of [harness.saveButton, harness.discardButton]) {
    assert.equal(node.getAttribute("tabindex"), null, "保存条按钮不需要额外的 tabindex 才可 Tab 到");
    assert.equal(node.disabled, false);
  }
  // 三个控件在正本里都是 `<button>`（替身节点不解析静态 HTML，因此这一条读 `index.html`）。
  const html = readStudio("index.html");
  assert.match(html, /<button[^>]*id="btnAgentDetailClose"[^>]*data-i18n-attr="aria-label:agentDetail\.closeAria"/,
    "关闭控件是带 aria-label 的 button");
  assert.match(html, /<button[^>]*id="adDiscard"[^>]*>/);
  assert.match(html, /<button[^>]*id="adSave"[^>]*>/);
});

test("配置区块的标签真的指向控件：单控件 label[for]、档位组 aria-labelledby", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  // 单控件：`<label for>` 必须指向真实存在的 `id`，否则点标签不落焦点、读屏念不出字段名。
  for (const field of ["display_name", "role", "provider", "model", "context_window_messages",
    "context_trigger_ratio", "react_max_iters"]) {
    const control = agentField(harness.agentBody, field);
    assert.ok(control, "配置区块缺少可编辑字段：" + field);
    assert.ok(control.id, field + " 的控件要有 id，作为 label[for] 的落点");
    const label = byLabelFor(harness.agentBody, control.id);
    assert.ok(label, field + " 的控件缺少指向它的 label[for]");
  }
  // 档位组没有「一个」可聚焦控件，用 aria-labelledby 指向同一份标签。
  // 配置区只剩「自动认领」一组档位（`enabled` 的入口是危险区那颗按钮，不再是档位组）。
  const groups = ["auto_claim"].map((tier) =>
    byDataset(harness.agentBody, "agentTier", tier));
  for (const group of groups) {
    assert.ok(group, "档位组缺失");
    assert.ok(group.getAttribute("aria-labelledby"), "档位组要有 aria-labelledby");
  }
  assert.equal(byDataset(harness.agentBody, "agentTier", "enabled"), null,
    "启用状态档位组已从配置区删除");
  // 系统提示是 textarea，用 aria-label 承担字段名（它不是 `.field` 里的单控件行）。
  assert.ok(agentField(harness.agentBody, "system_prompt").getAttribute("aria-label"),
    "系统提示要有 aria-label");
});

/* ─── 「发送消息」：DM 创建能力的唯一人类入口（分组 `+` 与弹窗已删除）──────── */

/** 一条私信 target（`state.dms` 的项；`deleted_at` 非空 = 已归档）。 */
function dmTarget(id, name, deletedAt) {
  return {
    id,
    kind: "dm",
    peer: { id: "mem-1", display_name: name, avatar_slot: 1, deleted_at: deletedAt || null },
    last_seq: 0,
    last_read_seq: 0,
    unread_count: 0,
  };
}

/** 详情页 / 工作台其余读路径的默认应答（只看这一张票需要的端点）。 */
function detailFetchBase(url) {
  const target = String(url);
  if (target.endsWith("/global-mcp-servers")) {
    return { ok: true, status: 200, json: async () => ({ global_mcp_servers: [] }) };
  }
  if (target.startsWith("/api/agents/")) {
    return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
  }
  if (target === "/api/agents") {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        agents: [agentDetail()],
        members: [{ id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 }],
      }),
    };
  }
  if (target.endsWith("/messages")) {
    return { ok: true, status: 200, json: async () => ({ messages: [] }) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
}

test("详情头部有「发送消息」按钮：文案挂词条、落在关闭控件旁", () => {
  const html = readStudio("index.html").replace(/<!--[\s\S]*?-->/g, "");
  const headStart = html.indexOf('class="agent-page-head"');
  const headEnd = html.indexOf('class="agent-page-scroll"');
  assert.ok(headStart >= 0 && headEnd > headStart, "index.html 里应有详情头部");
  const head = html.slice(headStart, headEnd);
  const send = /<button\b[^>]*id="btnAgentSendMessage"[^>]*>/.exec(head);
  const close = /<button\b[^>]*id="btnAgentDetailClose"[^>]*>/.exec(head);
  assert.ok(send, "详情头部应有 #btnAgentSendMessage");
  assert.ok(close, "详情头部仍有关闭控件");
  assert.ok(head.indexOf(send[0]) < head.indexOf(close[0]), "发送消息在关闭控件之前（同一行右侧）");
  assert.match(send[0], /data-i18n="agentDetail\.sendMessage"/, "文案挂词条，不写死");

  const harness = createHarness();
  for (const locale of ["zh-CN", "en"]) {
    assert.ok(catalogText(harness, locale, "agentDetail.sendMessage"), "两份词条都有发送消息：" + locale);
  }
});

test("点「发送消息」发出 POST /api/dms，复用已有私信并离开 Agent 详情", async () => {
  const dm = dmTarget("dm-1", "小明");
  const harness = withChannel({
    fetch: (url, init) => {
      const request = init || {};
      if (String(url) === "/api/dms" && (request.method || "GET") === "POST") {
        return { ok: true, status: 200, json: async () => ({ dm, created: false }) };
      }
      return detailFetchBase(url);
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await settle();
  assert.equal(harness.api.state.mode, "agent", "起始在 Agent 详情");
  const toasts = recordToasts(harness);

  await harness.elementFor("#btnAgentSendMessage").dispatchEvent({ type: "click" });
  await settle();

  const posts = harness.calls.filter((call) => call.url === "/api/dms" && call.method === "POST");
  assert.equal(posts.length, 1, "点一次只发一次创建 / 复用请求");
  assert.deepEqual(posts[0].body, { agent_id: "mem-1" }, "body 只带当前详情成员的 id");
  assert.equal(harness.api.state.activeChannelId, "dm-1", "进入了这条私信");
  assert.equal(harness.api.state.mode, "channel", "离开 Agent 详情，回到消息视图");
  assert.equal(harness.api.state.activeAgentId, null, "详情已关闭");
  assert.equal(harness.viewAgent.hidden, true, "详情视图让位");
  assert.equal(harness.elementFor("#composer").hidden, false, "composer 可见");
  assert.equal(harness.elementFor("#composerInput").focused, true, "输入框拿到焦点");
  const expected = catalogText(harness, "zh-CN", "toast.dmOpened").replace("{name}", "小明");
  assert.ok(toasts.some((text) => text.includes(expected)), "成功 toast 用「已打开」词条");
});

test("新建路径（created: true）同样进入该私信，toast 用「已创建」词条", async () => {
  const dm = dmTarget("dm-7", "小明");
  const harness = withChannel({
    fetch: (url, init) => {
      const request = init || {};
      if (String(url) === "/api/dms" && (request.method || "GET") === "POST") {
        return { ok: true, status: 200, json: async () => ({ dm, created: true }) };
      }
      return detailFetchBase(url);
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await settle();
  const toasts = recordToasts(harness);

  await harness.elementFor("#btnAgentSendMessage").dispatchEvent({ type: "click" });
  await settle();

  assert.equal(harness.api.state.activeChannelId, "dm-7", "新建后进入这条私信");
  assert.equal(harness.api.state.mode, "channel", "两条路径都落到消息视图");
  const expected = catalogText(harness, "zh-CN", "toast.dmCreated").replace("{name}", "小明");
  assert.ok(toasts.some((text) => text.includes(expected)), "成功 toast 用「已创建」词条");
});

test("请求在途时「发送消息」禁用：不能重复提交", async () => {
  let release = () => {};
  const gate = new Promise((resolve) => { release = resolve; });
  const dm = dmTarget("dm-1", "小明");
  const harness = withChannel({
    fetch: async (url, init) => {
      const request = init || {};
      if (String(url) === "/api/dms" && (request.method || "GET") === "POST") {
        await gate;
        return { ok: true, status: 200, json: async () => ({ dm, created: false }) };
      }
      return detailFetchBase(url);
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await settle();

  const click = harness.elementFor("#btnAgentSendMessage").dispatchEvent({ type: "click" });
  await flush();
  assert.equal(harness.elementFor("#btnAgentSendMessage").disabled, true, "请求没回来之前禁用");
  // 在途期间再点一次：闸门在 `messageActiveAgent()` 里，第二个请求不该发出去。
  await harness.elementFor("#btnAgentSendMessage").dispatchEvent({ type: "click" });
  assert.equal(
    harness.calls.filter((call) => call.url === "/api/dms" && call.method === "POST").length,
    1,
    "在途期间重复点击不发第二个请求",
  );

  release();
  await click;
  await settle();
  assert.equal(harness.elementFor("#btnAgentSendMessage").disabled, false, "请求结束后恢复可用");
  assert.equal(
    harness.calls.filter((call) => call.url === "/api/dms" && call.method === "POST").length,
    1,
    "整条路径只发一次请求",
  );
});

/* ─── 已归档私信只读：composer 可见但不可用 ─────────────────────────────── */

test("已归档私信只读：输入区与工具键禁用，hint / desc / placeholder 说明归档", async () => {
  const harness = withChannel();
  harness.api.state.dms = [dmTarget("dm-2", "小蓝", "2026-05-01T00:00:00Z")];
  harness.api.state.activeChannelId = "dm-2";
  harness.api.state.messages = [];
  harness.api.setViewMode("channel");
  // 先放一段草稿：否则 `#btnSend` 本来就会因为空正文禁用，归档闸门的断言会空转。
  harness.elementFor("#composerInput").value = "还想说点什么";
  harness.api.render();
  await settle();

  const input = harness.elementFor("#composerInput");
  assert.equal(input.disabled, true, "#composerInput 真正 disabled");
  ["#btnAttach", "#btnMention", "#btnAsTask", "#btnSend"].forEach((selector) => {
    assert.equal(harness.elementFor(selector).disabled, true, selector + " 在归档私信里禁用");
  });
  assert.equal(
    harness.elementFor("#composerHint").textContent,
    catalogText(harness, "zh-CN", "composer.archivedHint"),
    "composer hint 说明只读归档",
  );
  assert.equal(
    harness.elementFor("#chanDesc").textContent,
    catalogText(harness, "zh-CN", "header.dmArchivedDesc"),
    "频道头 desc 说明该 Agent 已删除、这段私信只读",
  );
  assert.equal(
    input.placeholder,
    catalogText(harness, "zh-CN", "header.dmArchivedPlaceholder"),
    "输入框 placeholder 同样说明归档",
  );

  // Enter 走的 `sendMessage()` 也有一道显式闸门：绕过禁用态直接调用也发不出去。
  input.value = "还想说点什么";
  const before = harness.calls.length;
  harness.api.sendMessage();
  await settle();
  assert.equal(harness.calls.length, before, "归档私信上不产生任何发送请求");
  assert.equal(input.value, "还想说点什么", "正文留在输入框里（没有假装发出去）");
});

test("切到普通频道或正常私信后，输入区控件与文案全部恢复", async () => {
  const harness = withChannel();
  harness.api.state.dms = [dmTarget("dm-2", "小蓝", "2026-05-01T00:00:00Z")];
  harness.api.state.activeChannelId = "dm-2";
  harness.api.state.messages = [];
  harness.api.setViewMode("channel");
  harness.api.render();
  await settle();
  assert.equal(harness.elementFor("#composerInput").disabled, true, "起始是归档只读");

  // 切回普通频道。
  harness.api.state.channels = [channel("ch-1", "设计")];
  harness.api.state.activeChannelId = "ch-1";
  harness.api.setViewMode("channel");
  harness.elementFor("#composerInput").value = "正常消息";
  harness.api.render();
  await settle();
  ["#composerInput", "#btnAttach", "#btnMention", "#btnAsTask", "#btnSend"].forEach((selector) => {
    assert.equal(harness.elementFor(selector).disabled, false, selector + " 在普通频道恢复可用");
  });
  assert.equal(
    harness.elementFor("#composerHint").textContent,
    catalogText(harness, "zh-CN", "composer.hint"),
    "hint 恢复普通词条",
  );
  assert.equal(
    harness.elementFor("#composerInput").placeholder,
    catalogText(harness, "zh-CN", "header.channelPlaceholder").replace("{name}", "设计"),
    "placeholder 恢复频道词条",
  );

  // 切到未删除 Agent 的正常私信。
  harness.api.state.dms = [dmTarget("dm-3", "小蓝")];
  harness.api.state.activeChannelId = "dm-3";
  harness.api.setViewMode("channel");
  harness.api.render();
  await settle();
  ["#composerInput", "#btnAttach", "#btnMention", "#btnAsTask", "#btnSend"].forEach((selector) => {
    assert.equal(harness.elementFor(selector).disabled, false, selector + " 在正常私信恢复可用");
  });
  assert.equal(
    harness.elementFor("#composerHint").textContent,
    catalogText(harness, "zh-CN", "composer.hint"),
    "正常私信的 hint 不是归档文案",
  );
  assert.equal(
    harness.elementFor("#composerInput").placeholder,
    catalogText(harness, "zh-CN", "header.dmPlaceholder").replace("{name}", "小蓝"),
    "placeholder 恢复私信词条",
  );
});

test("离线时「发送消息」禁用；恢复在线后按同一判据重新可用", async () => {
  const harness = withChannel();
  harness.load([agentDetail()]);
  harness.api.state.online = false;
  await harness.clickRow("mem-1");
  await settle();
  assert.equal(harness.api.state.mode, "agent", "仍可打开详情（读路径不受离线影响）");
  assert.equal(harness.elementFor("#btnAgentSendMessage").disabled, true, "离线时写入口禁用");

  harness.api.state.online = true;
  harness.api.render();
  await flush();
  assert.equal(harness.elementFor("#btnAgentSendMessage").disabled, false, "恢复在线后同一判据放行");
});
