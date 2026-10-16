/**
 * Studio 的 **Agent 详情**（dock 第三形态：概览 / 运行时 / 暂停 / 编辑配置）的 DOM 行为测试。
 *
 * 接缝沿用既有那一套（与 `search_ui.test.cjs` / `i18n_ui.test.cjs` 同款）：`vm` 装载
 * `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点，不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 点左栏 Agent 行打开详情面板（第三形态），标题与两个页签的文案来自词条；
 * 2. 两个页签切换（可见性 + `aria-selected`）；
 * 3. 概览四个数字与后端读数一致，且**每个数字都写明来源**；
 * 4. 运行时页签**只**呈现真字段（温度 / 内存 / 延迟 / token / 上下文一律不出现）；
 * 5. 「暂停」发出的请求体与结果状态点（`enabled: false` → 未启用 + 可读原因 + 按钮变「恢复」）；
 * 6. 「编辑配置」提交全部可写字段并读回（左栏名字跟着后端事实走）。
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

function createHarness(options) {
  const config = options || {};
  const calls = [];
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
    if (String(url).startsWith("/api/agents/")) {
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    }
    if (String(url) === "/api/agents") {
      return { ok: true, status: 200, json: async () => ({ agents: [agentDetail()], members: [member] }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  }

  function buildNode(selector) {
    const node = new FakeNode("div");
    node.hidden = HIDDEN_IN_HTML.has(selector);
    // 编辑弹窗的启用档位是静态 HTML 里的两个 `.radio-card`（`index.html`）；替身按同形构建它们，
    // 于是「哪一档被选中」这条断言读的是控件自己的状态，不是应用里的一个布尔。
    if (selector === "#agentEditEnabled") {
      ["on", "off"].forEach((value) => {
        const card = new FakeNode("button");
        card.dataset.enabled = value;
        node.appendChild(card);
      });
    }
    return node;
  }

  function elementFor(selector) {
    if (!elements.has(selector)) elements.set(selector, buildNode(selector));
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
      "\nglobalThis.__agentDetailTest = { state, bindEvents, renderMembers, renderDock,"
        + " openAgentDetail, closeAgentDetail, setAgentDetailTab, renderAgentDetail,"
        + " toggleAgentEnabled, openAgentEditModal, closeAgentEditModal, submitAgentEdit, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__agentDetailTest;
  api.state.member = member;
  api.state.members = [member];
  api.state.agents = [];
  api.state.channels = [];
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  const harness = {
    api,
    calls,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    memberList: elementFor("#memberList"),
    dock: elementFor("#dock"),
    dockAgent: elementFor("#dockAgent"),
    dockTask: elementFor("#dockTask"),
    dockDetail: elementFor("#dockDetail"),
    overview: elementFor("#adOverview"),
    runtime: elementFor("#adRuntime"),
    reportName: elementFor("#adName"),
    reportStatus: elementFor("#adStatusText"),
    reportPresence: elementFor("#adPresence"),
    editOverlay: elementFor("#agentEditOverlay"),
    patches() {
      return calls.filter((call) => call.method === "PATCH");
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
  };
  return harness;
}

/** 按 `data-*` 找一个节点：`dataset.x` 与 `setAttribute('data-x', …)` 两种写法都要认
    （替身的选择器不支持 `[attr="value"]`，而应用里两种写法都在用）。 */
function byDataset(root, key, value) {
  const attribute = "data-" + key.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
  return descendants(root).find((node) => {
    if (node.dataset && node.dataset[key] === value) return true;
    return Boolean(node.attributes && node.attributes[attribute] === value);
  }) || null;
}

/** 按 class 收集子树里的节点（替身选择器不支持 `.a .b` 这类后代选择器）。 */
function byClass(root, name) {
  return descendants(root).filter((node) => node.classList && node.classList.contains(name));
}

/** 概览四个数字（`.stat` 的 `n` 是第一个子节点）。 */
function statValues(root) {
  return byClass(root, "stat").map((cell) => cell.children[0].textContent);
}

/** 「键 / 值」行的两半。 */
function kvKeys(root) {
  return byClass(root, "kv").map((row) => row.children[0].textContent);
}

function kvValues(root) {
  return byClass(root, "kv").map((row) => row.children[1].textContent);
}

/** `index.html` 里详情面板的页签声明（标记是**结构**的正本，替身节点不解析静态 HTML）。 */
function dockTabValues() {
  const html = fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8");
  return (html.match(/data-adtab="([a-z]+)"/g) || []).map((match) => /"([a-z]+)"/.exec(match)[1]);
}

/** `index.html` 里详情面板挂的词条键（静态文案的唯一声明就是这里的 `data-i18n`）。 */
function dockMarkupKeys() {
  const html = fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8");
  return (html.match(/data-i18n="(agentDetail\.[A-Za-z]+)"/g) || []).map(
    (match) => /"([^"]+)"/.exec(match)[1],
  );
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function textOf(node) {
  return node.textContent;
}

function catalogText(harness, locale, key) {
  return harness.catalogs[locale][key];
}

/* ─── 1. 打开详情：第三形态 ─────────────────────────────────────────────── */

test("点左栏 Agent 行打开 dock 第三形态，标题与两个页签的文案取自词条", async () => {
  const harness = createHarness();
  harness.load([agentDetail()]);

  assert.equal(harness.dockDetail.hidden, true, "打开前详情面板是关着的");

  await harness.clickRow("mem-1");

  assert.equal(harness.dockDetail.hidden, false, "点左栏 Agent 行要打开详情");
  assert.equal(harness.dockAgent.hidden, true, "dock 一次只显示一种形态（成员面板让位）");
  assert.equal(harness.dockTask.hidden, true);
  assert.equal(harness.dock.hidden, false);
  assert.equal(harness.reportName.textContent, "小明");
  // 两个页签的声明与文案：结构在标记里（`data-adtab` / `data-i18n`），取值在词条里（测试 7 逐个核）。
  assert.deepEqual(dockTabValues(), ["overview", "runtime"]);
  assert.ok(dockMarkupKeys().includes("agentDetail.tabOverview"));
  assert.ok(dockMarkupKeys().includes("agentDetail.tabRuntime"));
  assert.equal(dockMarkupKeys().some((key) => /Log|Activity|Journal/i.test(key)), false, "日志页签不做（D24）");
  assert.equal(harness.elementFor("#adTabOverview").getAttribute("aria-selected"), "true");
  assert.equal(harness.elementFor("#adTabRuntime").getAttribute("aria-selected"), "false");
  // 打开时读一次正本（工作台快照里的概览数字可能已经旧了）。
  assert.ok(
    harness.calls.some((call) => call.url === "/api/agents/mem-1" && call.method === "GET"),
    "打开详情要 GET /api/agents/{id}",
  );
});

test("成员面板里的 Agent 名字也打开详情（「加入」按钮自己的动作不被吞掉）", async () => {
  const harness = createHarness();
  const agent = agentDetail();
  harness.api.state.channels = [{ id: "ch-1", name: "频道", last_seq: 0, last_read_seq: 0, unread_count: 0 }];
  harness.api.state.activeChannelId = "ch-1";
  // 已加入名单的正本是 `state.channelMembers`（与成员面板、@ 补全同一个来源）。
  harness.api.state.channelMembers = {
    "ch-1": [
      { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 },
      { id: "mem-1", kind: "agent", display_name: "小明", avatar_slot: 1 },
    ],
  };
  harness.load([agent]);
  harness.api.renderDock();
  const panel = harness.elementFor("#dockMembers");
  const nameButton = byDataset(panel, "agentOpen", "mem-1");
  assert.ok(nameButton, "成员卡片里的名字是打开详情的控件");

  await panel.dispatchEvent({ type: "click", target: nameButton });
  await flush();

  assert.equal(harness.dockDetail.hidden, false);
  assert.equal(harness.api.state.activeAgentId, "mem-1");
});

/* ─── 2. 两个页签 ───────────────────────────────────────────────────────── */

test("「概览 / 运行时」两个页签切换：内容可见性与 aria-selected 一起变", async () => {
  const harness = createHarness();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  assert.equal(harness.overview.hidden, false);
  assert.equal(harness.runtime.hidden, true);

  await harness.elementFor("#adTabRuntime").dispatchEvent({ type: "click" });

  assert.equal(harness.overview.hidden, true);
  assert.equal(harness.runtime.hidden, false);
  assert.equal(harness.elementFor("#adTabRuntime").getAttribute("aria-selected"), "true");
  assert.equal(harness.elementFor("#adTabOverview").getAttribute("aria-selected"), "false");
  // 只有两个页签：日志页签（以及任何第三档）在标记与词条里都不存在。
  assert.deepEqual(dockTabValues(), ["overview", "runtime"]);
  assert.equal(harness.catalogs["zh-CN"]["agentDetail.tabLog"], undefined);
  assert.equal(harness.catalogs.en["agentDetail.tabActivity"], undefined);

  await harness.elementFor("#adTabOverview").dispatchEvent({ type: "click" });

  assert.equal(harness.overview.hidden, false);
  assert.equal(harness.runtime.hidden, true);
});

/* ─── 3. 概览：数字来自后端读数，且写明来源 ─────────────────────────────── */

test("概览四个数字与后端读数一致，每个数字都写明来源", async () => {
  const harness = createHarness();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const numbers = byClass(harness.overview, "stat");
  assert.equal(numbers.length, 4);

  assert.deepEqual(statValues(harness.overview), ["2", "5", "1", "67%"]);

  // 每个数字都带**来源说明**（title / aria-label）——不能让读数变成不可核对的神谕。
  const hints = numbers.map((cell) => cell.title);
  hints.forEach((hint) => assert.ok(hint && hint.length > 0, "每个数字都要写明来源"));
  assert.equal(hints[0], catalogText(harness, "zh-CN", "agentDetail.statChannelsHint"));
  assert.equal(hints[1], catalogText(harness, "zh-CN", "agentDetail.statRoundsHint"));
  assert.equal(hints[2], catalogText(harness, "zh-CN", "agentDetail.statTasksDoneHint"));
  assert.equal(hints[3], catalogText(harness, "zh-CN", "agentDetail.statSuccessHint"));

  // 状态点与未启用原因同样是后端读数。
  assert.equal(harness.reportStatus.textContent, catalogText(harness, "zh-CN", "status.online"));
  assert.equal(harness.reportPresence.dataset.status, "online");
  // 模型标识也在概览的「事实」里（规格 §2 面 4 / D20 点名它）：同一份配置读数的另一处呈现。
  assert.equal(kvKeys(harness.overview)[0], catalogText(harness, "zh-CN", "agentDetail.kvModel"));
  assert.equal(kvValues(harness.overview)[0], "ollama:qwen2.5");
});

test("成功率没有样本时显示「—」，不编一个 0%", async () => {
  const harness = createHarness({
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

  assert.equal(statValues(harness.overview)[3], catalogText(harness, "zh-CN", "agentDetail.noSamples"));
  // 未启用原因也呈现出来（「为什么不能跑」必须可读）。
  assert.ok(kvValues(harness.overview).includes("该 Agent 的模型配置已停用"));
});

/* ─── 4. 运行时：只放真字段 ─────────────────────────────────────────────── */

test("运行时页签只呈现 agent_config 里的真字段，不出现原型的内存 / 延迟 / token / 上下文", async () => {
  const harness = createHarness();
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");
  await harness.elementFor("#adTabRuntime").dispatchEvent({ type: "click" });

  const keys = kvKeys(harness.runtime);
  assert.deepEqual(keys, [
    catalogText(harness, "zh-CN", "agentDetail.kvModel"),
    catalogText(harness, "zh-CN", "agentDetail.kvProvider"),
    catalogText(harness, "zh-CN", "agentDetail.kvRemote"),
    catalogText(harness, "zh-CN", "agentDetail.kvConsent"),
    catalogText(harness, "zh-CN", "agentDetail.kvRole"),
    catalogText(harness, "zh-CN", "agentDetail.kvEnabled"),
  ]);
  const values = kvValues(harness.runtime);
  assert.equal(values[0], "ollama:qwen2.5");
  assert.equal(values[1], catalogText(harness, "zh-CN", "provider.ollama"));
  assert.equal(values[2], catalogText(harness, "zh-CN", "agentDetail.remoteNo"));
  assert.equal(values[3], catalogText(harness, "zh-CN", "agentDetail.consentNotNeeded"));
  assert.equal(values[4], "前端工程师");
  assert.equal(values[5], catalogText(harness, "zh-CN", "agentDetail.enabledOn"));
  // 系统提示是配置数据的**原文**（不是被改写的一句话）。
  assert.equal(byDataset(harness.runtime, "odId", "agent-detail-prompt").textContent, "你只回答一句话。");

  // 反：本仓没有这些数据面，界面就一个字都不许出现。
  const runtimeText = harness.dockDetail.textContent;
  for (const forbidden of ["温度", "内存", "延迟", "token", "Token", "上下文", "工具权限", "运行时长"]) {
    assert.equal(runtimeText.includes(forbidden), false, "运行时不得出现：" + forbidden);
  }
});

/* ─── 5. 暂停：写 enabled，状态点跟着后端事实走 ────────────────────────── */

test("「暂停」发出 `enabled: false` 的 PATCH，结果状态点是未启用、原因是后端那句话、按钮变「恢复」", async () => {
  const paused = agentDetail({
    enabled: false,
    status: "offline",
    enabled_reason: "该 Agent 的模型配置已停用",
  });
  const harness = createHarness({
    fetch: async (url, init) => {
      if (String(url) === "/api/agents/mem-1" && (init.method || "GET") === "PATCH") {
        return { ok: true, status: 200, json: async () => ({ agent: paused, agents: [paused], members: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({ agent: agentDetail() }) };
    },
  });
  harness.load([agentDetail()]);
  await harness.clickRow("mem-1");

  const pauseButton = harness.overview.querySelector('[data-agent-toggle]');
  assert.equal(textOf(pauseButton), catalogText(harness, "zh-CN", "agentDetail.pause"));

  await harness.overview.dispatchEvent({ type: "click", target: pauseButton });
  await flush();

  const patch = harness.patches();
  assert.equal(patch.length, 1);
  assert.equal(patch[0].url, "/api/agents/mem-1");
  assert.deepEqual(patch[0].body, { enabled: false });
  // 状态点是后端事实：未启用 + 可读原因。
  assert.equal(harness.reportPresence.dataset.status, "offline");
  assert.equal(harness.reportStatus.textContent, catalogText(harness, "zh-CN", "status.offline"));
  const values = kvValues(harness.overview);
  assert.ok(values.includes("该 Agent 的模型配置已停用"), "未启用原因要可读");
  // 按钮的说法随落库取值变（同一个动作的反向）。
  assert.equal(
    textOf(harness.overview.querySelector('[data-agent-toggle]')),
    catalogText(harness, "zh-CN", "agentDetail.resume"),
  );
  // 暂停的已知代价写在界面上，不是只写在 ADR 里。
  assert.ok(
    harness.overview.textContent.includes(catalogText(harness, "zh-CN", "agentDetail.pauseNote")),
    "暂停的已知代价要在界面上可读",
  );
});

/* ─── 6. 编辑配置：提交全部可写字段并读回 ───────────────────────────────── */

test("「编辑配置」预填当前配置，保存提交全部可写字段并读回（左栏名字跟着变）", async () => {
  const renamed = agentDetail({ display_name: "小明明", role: "后端工程师" });
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const harness = createHarness({
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

  await harness.overview.dispatchEvent({
    type: "click",
    target: harness.overview.querySelector('[data-agent-edit]'),
  });
  await flush();
  assert.equal(harness.editOverlay.hidden, false);
  // 预填：`model_id` 被拆回「提供商 + 模型名」两个控件。
  assert.equal(harness.elementFor("#agentEditName").value, "小明");
  assert.equal(harness.elementFor("#agentEditRole").value, "前端工程师");
  assert.equal(harness.elementFor("#agentEditProviderSelect").value, "ollama");
  assert.equal(harness.elementFor("#agentEditModelInput").value, "qwen2.5");
  assert.equal(harness.elementFor("#agentEditPromptInput").value, "你只回答一句话。");

  harness.elementFor("#agentEditName").value = "小明明";
  harness.elementFor("#agentEditRole").value = "后端工程师";
  await harness.elementFor("#agentEditSubmit").dispatchEvent({ type: "click" });
  await flush();

  const patch = harness.patches();
  assert.equal(patch.length, 1);
  assert.deepEqual(patch[0].body, {
    display_name: "小明明",
    role: "后端工程师",
    model_id: "ollama:qwen2.5",
    system_prompt: "你只回答一句话。",
    enabled: true,
  });
  assert.equal(harness.editOverlay.hidden, true, "保存成功要关掉弹窗");
  assert.equal(harness.memberList.textContent.includes("小明明"), true, "左栏名字跟着后端读数走");
  assert.equal(harness.reportName.textContent, "小明明");
});

test("后端整体拒绝时错误留在弹窗里，弹窗不关、名字不变", async () => {
  const harness = createHarness({
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
  await harness.overview.dispatchEvent({
    type: "click",
    target: harness.overview.querySelector('[data-agent-edit]'),
  });
  await flush();

  harness.elementFor("#agentEditName").value = "不该生效";
  await harness.elementFor("#agentEditSubmit").dispatchEvent({ type: "click" });
  await flush();

  assert.equal(harness.editOverlay.hidden, false, "失败不关弹窗");
  assert.equal(harness.elementFor("#agentEditFormError").hidden, false);
  assert.equal(harness.elementFor("#agentEditFormError").textContent, "Agent 配置校验未通过，未改动任何字段");
  assert.equal(harness.reportName.textContent, "小明", "界面不声称一个没落库的取值");
});

/* ─── 7. 新增文案：两份词条都取到值 ─────────────────────────────────────── */

test("本票新增的文案在两份词条里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "agentDetail.tag",
    "agentDetail.tabsLabel",
    "agentDetail.tabOverview",
    "agentDetail.tabRuntime",
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
    "agentDetail.sectionStatus",
    "agentDetail.kvStatus",
    "agentDetail.kvReason",
    "agentDetail.sectionActions",
    "agentDetail.pause",
    "agentDetail.resume",
    "agentDetail.edit",
    "agentDetail.pauseNote",
    "agentDetail.sectionRuntime",
    "agentDetail.runtimeNote",
    "agentDetail.kvModel",
    "agentDetail.kvProvider",
    "agentDetail.kvRemote",
    "agentDetail.remoteYes",
    "agentDetail.remoteNo",
    "agentDetail.kvConsent",
    "agentDetail.consentAck",
    "agentDetail.consentPending",
    "agentDetail.consentNotNeeded",
    "agentDetail.kvRole",
    "agentDetail.kvPrompt",
    "agentDetail.kvEnabled",
    "agentDetail.enabledOn",
    "agentDetail.enabledOff",
    "agentDetail.emptyField",
    "agentDetail.editTitle",
    "agentDetail.editSub",
    "agentDetail.save",
    "agentDetail.saveFailed",
    "agentDetail.pauseFailed",
    "agentDetail.resumeFailed",
    "agent.enabledLabel",
    "agent.enabledOn",
    "agent.enabledOnHint",
    "agent.enabledOff",
    "agent.enabledOffHint",
    "toast.agentUpdated",
    "toast.agentDetailFailed",
    "toast.agentPaused",
    "toast.agentResumed",
  ];
  for (const key of keys) {
    for (const locale of ["zh-CN", "en"]) {
      const value = harness.catalogs[locale][key];
      assert.ok(typeof value === "string" && value.length > 0, locale + " 缺词条：" + key);
    }
  }
});
