/**
 * Studio 的 dock 在**私信**下的可见性（用户 2026-10-24 反馈）：
 * 私信里没有「频道成员」这个 dock 形态，因此频道头的 `#btnDockToggle` 不出现、
 * 命令面板里没有「切换详情面板」、`toggleDock()` 什么都不做；切 target 后已打开的 dock 收起。
 * 任务线程 / 运行面板是 dock 的独立形态，私信下仍然算「有可显示的形态」，不得误伤。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `channel_config_ui.test.cjs` 同款），
 * 不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 频道为当前 target 时开关可见，私信下隐藏；
 * 2. 私信下 `toggleDock()` 不改开合、不写 `aria-expanded=true`；频道下能开能关；
 * 3. 私信下命令面板不含 `toggle-dock`，频道下含；
 * 4. 频道里已打开的 dock 在切到私信并整体重绘后收起；
 * 5. 私信下若 dock 里有任务线程（独立形态），整体重绘不收起它、开关仍可见。
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
  return { id, kind: "dm", peer: { display_name: name, avatar_slot: 1 }, unread_count: 0 };
}

function createHarness() {
  const elements = new Map();
  const documentListeners = new Map();
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

  function fetchStub() {
    return { ok: true, status: 200, json: async () => ({}) };
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
      "\nglobalThis.__dockDmTest = { state, render, renderHeader, toggleDock,"
        + " setDockOpen, setDockMode, commandList, closeAgentDetail, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__dockDmTest;
  api.state.member = member;
  api.state.members = [member];
  api.state.agents = [];
  api.state.channels = [];
  api.state.dms = [];
  api.state.tasks = [];
  api.state.online = true;
  api.state.ready = true;

  const harness = {
    api,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    dock: elementFor("#dock"),
    toggle: elementFor("#btnDockToggle"),
    loadChannel() {
      api.state.channels = [channel("ch_1", "设计系统重构")];
      api.state.dms = [dm("dm_1", "小明")];
      api.state.activeChannelId = "ch_1";
      api.render();
    },
    loadDm() {
      api.state.channels = [channel("ch_1", "设计系统重构")];
      api.state.dms = [dm("dm_1", "小明")];
      api.state.activeChannelId = "dm_1";
      api.render();
    },
  };
  return harness;
}

function commandIds(api) {
  return api.commandList().map((command) => command.id);
}

/* ─── 1. 频道头的开关只在频道出现 ─────────────────────────────────────── */

test("频道为当前 target 时开关可见；私信为当前 target 时隐藏", () => {
  const harness = createHarness();

  harness.loadChannel();
  assert.equal(harness.toggle.hidden, false, "频道下 dock 有成员形态，开关要可见");

  harness.loadDm();
  assert.equal(harness.toggle.hidden, true, "私信下没有可显示的形态，开关随之隐藏");
});

/* ─── 2. 私信下开关动作无效；频道下能开能关 ───────────────────────────── */

test("私信下 toggleDock() 不改开合；频道下能开能关且 aria-expanded 同步", () => {
  const harness = createHarness();

  harness.loadDm();
  assert.equal(harness.dock.hidden, true, "起始 dock 是收起的");
  harness.api.toggleDock();
  assert.equal(harness.dock.hidden, true, "私信下按开关不打开 dock");
  assert.equal(harness.dock.classList.contains("is-open"), false, "私信下不写 .is-open");
  assert.notEqual(
    harness.toggle.getAttribute("aria-expanded"),
    "true",
    "私信下不把 aria-expanded 写成 true",
  );

  harness.loadChannel();
  harness.api.setDockOpen(false);
  harness.api.toggleDock();
  assert.equal(harness.dock.hidden, false, "频道下开关能打开 dock");
  assert.equal(harness.dock.classList.contains("is-open"), true);
  assert.equal(harness.toggle.getAttribute("aria-expanded"), "true");

  harness.api.toggleDock();
  assert.equal(harness.dock.hidden, true, "再按一次收起");
  assert.equal(harness.dock.classList.contains("is-open"), false);
  assert.equal(harness.toggle.getAttribute("aria-expanded"), "false");
});

/* ─── 3. 命令面板：私信下没有「切换详情面板」 ─────────────────────────── */

test("私信下 commandList() 不含 toggle-dock；频道下含", () => {
  const harness = createHarness();

  harness.loadDm();
  assert.equal(commandIds(harness.api).includes("toggle-dock"), false);

  harness.loadChannel();
  assert.equal(commandIds(harness.api).includes("toggle-dock"), true);
});

/* ─── 4. 切到私信后，已打开的 dock 收起 ───────────────────────────────── */

test("频道里打开的 dock 在切到私信并 render() 后收起", () => {
  const harness = createHarness();

  harness.loadChannel();
  harness.api.setDockOpen(true);
  assert.equal(harness.dock.hidden, false, "前提：频道下 dock 已打开");

  harness.api.state.activeChannelId = "dm_1";
  harness.api.render();
  assert.equal(harness.dock.hidden, true, "切成私信后 dock 不得留在右侧");
  assert.equal(harness.dock.classList.contains("is-open"), false);
});

/* ─── 5. 反向不误伤：任务线程 / 运行面板是独立形态 ────────────────────── */

test("私信下 dock 有任务线程形态时 render() 不收起它，开关仍可见", () => {
  const harness = createHarness();

  harness.loadDm();
  harness.api.state.activeTaskId = "t_1";
  harness.api.setDockOpen(false);
  harness.api.setDockMode("task", true);
  assert.equal(harness.dock.hidden, false, "前提：任务线程显式打开 dock");

  harness.api.render();
  assert.equal(harness.dock.hidden, false, "任务线程是独立形态，整体重绘不得收起它");
  assert.equal(harness.dock.classList.contains("is-open"), true);
  assert.equal(harness.toggle.hidden, false, "有任务线程形态时开关仍要可见");
});

/* ─── 6. 关闭 Agent 详情不得把 dock 重新打开 ─────────────────────────── */

test("私信下关闭 Agent 详情：render() 收起的 dock 不会被恢复开合重新打开", () => {
  const harness = createHarness();

  harness.loadChannel();
  harness.api.state.activeTaskId = "t_1";
  harness.api.setDockOpen(false);
  harness.api.setDockMode("task", true);
  assert.equal(harness.dock.hidden, false, "前提：进入详情前 dock 是开着的");

  // 与 `openAgentDetail()` 同形：记下进入前的开合，打开详情并清掉任务线程；
  // 详情里把 target 留在同一个私信上，关闭详情后便没有可显示的形态。
  harness.api.state.activeChannelId = "dm_1";
  harness.api.state.activeTaskId = null;
  harness.api.state.activeAgentId = "mem_1";
  harness.api.state.agentReturn = { mode: "channel", dockOpen: true };
  harness.api.closeAgentDetail();

  assert.equal(harness.dock.hidden, true, "私信下关闭详情后 dock 仍收起");
  assert.equal(harness.dock.classList.contains("is-open"), false);
  assert.notEqual(harness.toggle.getAttribute("aria-expanded"), "true");
});
