/**
 * Studio 的**频道回应预算**（#54）：频道头入口 + 频道设置弹窗（只读读数 / 待生效 / 3-5-10 写控件）。
 *
 * 接缝沿用既有那一套（与 `agent_detail_ui.test.cjs` / `search_ui.test.cjs` / `i18n_ui.test.cjs` 同款）：
 * `vm` 装载 `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点——不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 频道头的预算入口打开弹窗；已用 / 剩余 / 有效上界 / **来源**四个读数都来自后端载荷；
 * 2. 待生效值（`pending_limit`）与当前有效上界**分开**呈现，并写明「下一个人类世代生效」；
 * 3. 写控件只有 3 / 5 / 10 三档（默认 3）；选择后提交的是 `{budget_limit: <档位>}`，
 *    保存后按**响应里的新读数**重铺并提示「从下一个人类世代生效」；
 * 4. 后端拒绝时显示**可读原因**，且界面不改口（数值仍是后端说的那一份）；
 * 5. 预算耗尽是一个**明确事实**（不是错误区）；
 * 6. 新增文案在两份词条里都取到值（键集由 `i18n_ui.test.cjs` 的比对与这里的点名一起兜住）；
 * 7. 私信没有这条面（入口隐藏）；关闭落点（✕ / 底部关闭 / 点遮罩）。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身（与 `agent_detail_ui.test.cjs` 同形；本文件只实现用到的那些方法） ── */
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

/** `index.html` 里带 `hidden` 的元素：替身的初始可见性从**同一个正本**读，不另手抄一份。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

/** `GET/PATCH /api/channels/{id}/budget` 的 `budget` 载荷（形状与后端逐字一致）。 */
function budgetPayload(overrides) {
  return {
    budget: Object.assign(
      {
        channel_id: "ch_1",
        epoch_seq: 7,
        limit: 3,
        used: 1,
        remaining: 2,
        source: "built-in-default",
        pending_limit: null,
      },
      overrides || {},
    ),
  };
}

function channel(id, name) {
  return { id, name, last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function dm(id, name) {
  return { id, kind: "dm", peer: { display_name: name } };
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
    if (String(url) === "/api/channels/ch_1/budget") {
      if ((request.method || "GET") === "PATCH") {
        return { ok: true, status: 200, json: async () => budgetPayload() };
      }
      return { ok: true, status: 200, json: async () => budgetPayload() };
    }
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
      "\nglobalThis.__channelBudgetTest = { state, bindEvents, renderHeader, openBudgetModal,"
        + " closeBudgetModal, loadChannelBudget, renderBudgetModal, selectBudgetTier,"
        + " submitBudgetTier, applySettings, savedBudgetTier, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__channelBudgetTest;
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
    overlay: elementFor("#budgetOverlay"),
    body: elementFor("#budgetBody"),
    title: elementFor("#budgetTitle"),
    sub: elementFor("#budgetSub"),
    formError: elementFor("#budgetFormError"),
    toastWrap: elementFor("#toastWrap"),
    entry: elementFor("#btnChannelBudget"),
    patches() {
      return calls.filter((call) => call.method === "PATCH");
    },
    /** 让某频道成为当前频道（弹窗入口是频道头的按钮，频道头按 `activeTarget()` 渲染）。 */
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
    /** 点频道头的预算入口并等读取跑完。 */
    async open() {
      await harness.entry.dispatchEvent({ type: "click", target: harness.entry });
      await flush();
    },
    async click(node) {
      await harness.body.dispatchEvent({ type: "click", target: node });
      await flush();
    },
    tier(tier) {
      const node = byDataset(harness.body, "budgetTier", String(tier));
      assert.ok(node, "弹窗里应当有这一档的控件：" + tier);
      return node;
    },
    saveButton() {
      const node = byDataset(harness.body, "budgetSave", "1");
      assert.ok(node, "弹窗里应当有保存控件");
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

/** 按 `data-od-id` 找一个节点（`kvRow` / `sectionTitle` 用它给读数与分区标标识）。 */
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

/* ─── 1. 打开弹窗：四个读数 + 来源 ─────────────────────────────────────── */
test("频道头的预算入口打开弹窗：已用 / 剩余 / 有效上界 / 来源四个读数都来自后端载荷", async () => {
  const harness = createHarness({
    fetch: () =>
      ({
        ok: true,
        status: 200,
        json: async () =>
          budgetPayload({ limit: 5, used: 1, remaining: 4, source: "channel-override" }),
      }),
  });
  harness.loadChannel();

  assert.equal(harness.entry.hidden, false, "频道头的预算入口对频道可见");
  assert.equal(harness.overlay.hidden, true, "打开前弹窗是关着的");

  await harness.open();

  assert.equal(harness.overlay.hidden, false, "点入口把弹窗打开");
  assert.ok(harness.calls.some((call) => call.url === "/api/channels/ch_1/budget" && call.method === "GET"));
  assert.equal(textOf(harness.body, "budget-used"), "1");
  assert.equal(textOf(harness.body, "budget-remaining"), "4");
  assert.equal(textOf(harness.body, "budget-limit"), "5");
  // 来源是**机器可读的取值**（`channel-override` / `built-in-default`）的界面说法，不是裸词。
  assert.equal(textOf(harness.body, "budget-source"), "频道覆盖");
  assert.equal(harness.title.textContent, "回应预算");
});

/* ─── 2. 待生效值与当前有效上界分开 ────────────────────────────────────── */
test("待生效的下一世代值与当前有效上界分开呈现，并写明下一个人类世代生效", async () => {
  const harness = createHarness({
    fetch: () =>
      ({
        ok: true,
        status: 200,
        json: async () => budgetPayload({ limit: 3, used: 1, remaining: 2, pending_limit: 5 }),
      }),
  });
  harness.loadChannel();
  await harness.open();

  assert.equal(textOf(harness.body, "budget-limit"), "3", "当前世代的**有效上界**仍是 3");
  assert.equal(textOf(harness.body, "budget-remaining"), "2", "剩余量按当前有效上界算，不按待生效值");
  assert.equal(textOf(harness.body, "budget-pending"), "5", "下一世代的已保存值是 5（与当前分开一行）");
  assert.match(harness.body.textContent, /下一个人类世代生效/);
});

test("没有待生效差异时，下一世代那一行明确写成「与当前相同」", async () => {
  const harness = createHarness({
    fetch: () =>
      ({ ok: true, status: 200, json: async () => budgetPayload({ limit: 3, pending_limit: null }) }),
  });
  harness.loadChannel();
  await harness.open();

  assert.equal(textOf(harness.body, "budget-pending"), "3");
  assert.match(harness.body.textContent, /没有待生效改动/);
});

/* ─── 3. 写控件：只有 3 / 5 / 10 三档；保存后读回并提示下个世代生效 ─────── */
test("写控件只有 3 / 5 / 10 三档（默认 3），提交的是 {budget_limit: 档位}", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.open();

  const tiers = descendants(harness.body)
    .filter((node) => node.dataset && node.dataset.budgetTier !== undefined)
    .map((node) => node.dataset.budgetTier);
  assert.deepEqual(tiers, ["3", "5", "10"], "只有固定三档——没有可输入任意数字的控件");
  // 默认档位（内置默认 3）就是选中的那一档。
  assert.equal(harness.tier(3).getAttribute("aria-pressed"), "true");
  assert.equal(harness.tier(5).getAttribute("aria-pressed"), "false");

  await harness.click(harness.tier(5));
  assert.equal(harness.tier(5).getAttribute("aria-pressed"), "true", "选中 5");
  assert.equal(harness.tier(3).getAttribute("aria-pressed"), "false");

  await harness.click(harness.saveButton());

  const patches = harness.patches();
  assert.equal(patches.length, 1, "一次保存只发一个 PATCH");
  assert.equal(patches[0].url, "/api/channels/ch_1/budget");
  assert.deepEqual(patches[0].body, { budget_limit: 5 }, "请求体只表达上界");
});

test("保存成功后按响应里的新读数重铺，并提示从下一个人类世代生效", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      if ((request.method || "GET") === "PATCH") {
        return {
          ok: true,
          status: 200,
          json: async () =>
            budgetPayload({ limit: 3, used: 1, remaining: 2, pending_limit: 10 }),
        };
      }
      return { ok: true, status: 200, json: async () => budgetPayload({ limit: 3 }) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.click(harness.tier(10));
  await harness.click(harness.saveButton());

  assert.equal(textOf(harness.body, "budget-pending"), "10", "读回的是后端说待生效的 10");
  assert.equal(textOf(harness.body, "budget-limit"), "3", "当前世代没被回溯改写");
  assert.match(harness.toastWrap.textContent, /下一个人类世代生效/);
  assert.equal(harness.formError.hidden, true, "成功时没有错误行");
});

/* ─── 4. 后端拒绝：显示可读原因，界面不改口 ────────────────────────────── */
test("后端拒绝非法档位时显示可读原因，读数保持后端说的那一份", async () => {
  const harness = createHarness({
    fetch: (url, request) => {
      if ((request.method || "GET") === "PATCH") {
        return {
          ok: false,
          status: 400,
          json: async () => ({
            error: {
              code: "validation_failed",
              message: "回应预算上界只接受固定档位：3, 5, 10，未保存任何改动",
              fields: { budget_limit: "必须是这些之一：3, 5, 10" },
            },
          }),
        };
      }
      return { ok: true, status: 200, json: async () => budgetPayload({ pending_limit: null }) };
    },
  });
  harness.loadChannel();
  await harness.open();

  await harness.click(harness.tier(10));
  await harness.click(harness.saveButton());

  assert.equal(harness.formError.hidden, false, "错误行显示出来");
  assert.match(harness.formError.textContent, /只接受固定档位/);
  assert.match(harness.formError.textContent, /3, 5, 10/);
  // 失败不改本地镜像：没有待生效值这件事仍是后端说的那一份。
  assert.equal(textOf(harness.body, "budget-pending"), "3");
});

/* ─── 5. 预算耗尽是一个明确事实，不是错误 ─────────────────────────────── */
test("预算耗尽时给出明确的「预算耗尽」事实，不显示成出错", async () => {
  const harness = createHarness({
    fetch: () =>
      ({
        ok: true,
        status: 200,
        json: async () => budgetPayload({ limit: 3, used: 3, remaining: 0 }),
      }),
  });
  harness.loadChannel();
  await harness.open();

  assert.equal(textOf(harness.body, "budget-exhausted"), "预算耗尽");
  assert.match(harness.body.textContent, /本轮零投递/, "并说明它不是出错");
  assert.equal(harness.formError.hidden, true, "耗尽不占用错误区");
});

/* ─── 6. 新增文案在两份词条里都取到值 ──────────────────────────────────── */
test("新增的预算文案在两份词条里都存在，且 app.js / index.html 里引用的预算键一个不缺", () => {
  const harness = createHarness();
  const zh = harness.catalogs["zh-CN"];
  const en = harness.catalogs.en;
  const required = [
    "budget.entryTitle",
    "budget.title",
    "budget.sub",
    "budget.closeAria",
    "budget.loading",
    "budget.loadFailed",
    "budget.sectionCurrent",
    "budget.used",
    "budget.remaining",
    "budget.limit",
    "budget.source",
    "budget.source.channel-override",
    "budget.source.built-in-default",
    "budget.state",
    "budget.exhausted",
    "budget.exhaustedNote",
    "budget.sectionNext",
    "budget.pending",
    "budget.pendingEffective",
    "budget.pendingSame",
    "budget.tierLabel",
    "budget.tierUnit",
    "budget.tierDefaultUnit",
    "budget.tierHint",
    "budget.save",
    "budget.saved",
    "budget.saveFailed",
  ];
  required.forEach((key) => {
    assert.ok(zh[key], "zh-CN 缺词条：" + key);
    assert.ok(en[key], "en 缺词条：" + key);
  });

  // `app.js` / `index.html` 里点名的预算键：一个都不许在两份表里落空（不靠人眼）。
  // 扫描的是**键的字面量**（`t('budget.x')` 与 `sectionTitle('budget.x')` 两种传法都算）。
  const appSource = fs.readFileSync(path.join(STUDIO_DIR, "app.js"), "utf8");
  const html = fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8");
  const referenced = new Set(
    [...appSource.matchAll(/'(budget\.[A-Za-z.-]+)'/g)]
      .map((match) => match[1])
      // `'budget.source.' + source` 这种是**前缀**，具体取值由下面两行显式点名。
      .filter((key) => !key.endsWith(".")),
  );
  [...html.matchAll(/data-i18n(?:-attr)?="([^"]*)"/g)].forEach((match) => {
    match[1]
      .split(",")
      .map((part) => part.split(":").pop().trim())
      .filter((key) => key.startsWith("budget."))
      .forEach((key) => referenced.add(key));
  });
  // 两个来源取值是**拼出来的**键（`'budget.source.' + source`），静态扫描抓不到，这里显式点名。
  ["channel-override", "built-in-default"].forEach((source) => {
    referenced.add("budget.source." + source);
  });
  assert.ok(referenced.size >= required.length, "应当至少覆盖上面那一批键");
  referenced.forEach((key) => {
    assert.ok(zh[key] !== undefined, "zh-CN 缺被引用的键：" + key);
    assert.ok(en[key] !== undefined, "en 缺被引用的键：" + key);
  });
});

/* ─── 7. 切语言时弹窗跟着重铺 ─────────────────────────────────────────── */
test("切到 English 后弹窗读数与来源说法跟着重铺（不留在上一门语言）", async () => {
  const harness = createHarness({
    fetch: () =>
      ({
        ok: true,
        status: 200,
        json: async () => budgetPayload({ limit: 3, used: 3, remaining: 0 }),
      }),
  });
  harness.loadChannel();
  await harness.open();
  assert.equal(textOf(harness.body, "budget-source"), "内置默认");

  harness.api.applySettings({ locale: "en", message_density: "comfortable", theme: "system" });

  assert.equal(harness.title.textContent, "Response budget");
  assert.equal(textOf(harness.body, "budget-source"), "Built-in default");
  assert.equal(textOf(harness.body, "budget-exhausted"), "Budget exhausted");
});

/* ─── 8. 私信没有这条面 ───────────────────────────────────────────────── */
test("私信视图隐藏预算入口（频道设置面只属于频道）", () => {
  const harness = createHarness();
  harness.loadDm();
  assert.equal(harness.entry.hidden, true);

  harness.loadChannel();
  assert.equal(harness.entry.hidden, false);
});

/* ─── 9. 关闭落点 ─────────────────────────────────────────────────────── */
test("✕ / 底部关闭 / 点遮罩都能关掉弹窗，且关掉后不再保留读数", async () => {
  const harness = createHarness();
  harness.loadChannel();
  await harness.open();
  assert.equal(harness.overlay.hidden, false);

  await harness.elementFor("#budgetClose").dispatchEvent({ type: "click" });
  assert.equal(harness.overlay.hidden, true);
  assert.equal(harness.api.state.channelBudget.budget, null, "关闭即丢弃镜像（不缓存过期读数）");

  await harness.open();
  await harness.elementFor("#budgetDone").dispatchEvent({ type: "click" });
  assert.equal(harness.overlay.hidden, true);

  await harness.open();
  await harness.overlay.dispatchEvent({ type: "click", target: harness.overlay });
  assert.equal(harness.overlay.hidden, true);
});
