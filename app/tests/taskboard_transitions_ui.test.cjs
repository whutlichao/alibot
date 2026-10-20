/**
 * 看板任务状态流转的呈现面（#191）：拖拽 + 卡片「流转」菜单，只暴露**载荷**里的合法迁移。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；与 `task_count_ui.test.cjs` 同形），
 * 不新开第二套前端测试框架（#191「不做什么」）。
 *
 * 这里断言的是**用户可观察的行为**，不是内部函数返回值：
 * 1. 菜单项恰好是该任务载荷的 `allowed_transitions`（用例故意给一份**静态迁移表里不存在**的
 *    集合：todo → done / in_review。它只能来自载荷，因此「前端还留着一份表副本」会立刻红）；
 * 2. 点菜单项只发起**一次**正确的 `PATCH /api/tasks/{id}/status`，并重读看板；
 * 3. 拖拽悬停：合法列 `.drag-over`、非法列 `.invalid-over`、当前列不标态；
 * 4. 非法列松手：**零请求** + 一条说明「不是合法迁移」的 toast；
 * 5. 合法列松手：一次 PATCH；
 * 6. `closed`（终态，载荷是 `[]`）卡片没有流转按钮，拖起后也没有任何合法落点；
 * 7. 离线时按钮禁用、菜单打不开、零请求；
 * 8. 键盘面：打开后焦点进第一项、↑/↓ 移动、Esc 关闭并还焦点、Enter 激活一项。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身（与 `task_count_ui.test.cjs` 同形；多一个「焦点」事实） ──────── */
let activeElement = null;

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
    child.parentNode = null;
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

  focus() {
    activeElement = this;
  }

  blur() {
    if (activeElement === this) activeElement = null;
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
  const attributeValue = /^\[([a-zA-Z0-9_-]+)="([^"]*)"\]$/.exec(selector);
  if (attributeValue) {
    return node.attributes[attributeValue[1]] === attributeValue[2];
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
  await flush();
}

function channel(id, name) {
  return { id, name, description: "", last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

/** 一条任务（`GET /api/tasks` 的行形状；`allowed_transitions` 由**载荷**决定）。 */
function taskRecord(overrides) {
  return Object.assign(
    {
      id: "t_1",
      title: "整理发布清单",
      channel_id: "ch_1",
      channel_name: "设计系统重构",
      status: "todo",
      assignee_member_id: null,
      unread_count: 0,
      allowed_transitions: ["in_progress"],
      target_id: "tgt_1",
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
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  // 后端任务表在替身里是**可变**的：PATCH 之后的重读必须看到新状态（与真实后端一致）。
  const tasks = (config.tasks || []).map((task) => Object.assign({}, task));
  const toasts = [];
  activeElement = null;

  function defaultFetch(url, request) {
    const method = request.method || "GET";
    const route = String(url).split("?")[0];
    if (method === "GET" && route === "/api/tasks") return jsonResponse({ tasks: tasks.slice() });
    if (method === "PATCH" && /^\/api\/tasks\/[^/]+\/status$/.test(route)) {
      const id = decodeURIComponent(route.split("/")[3]);
      const task = tasks.find((item) => item.id === id);
      if (!task) return jsonResponse({ error: { code: "not_found", message: "任务不存在" } }, 404);
      task.status = request.body.status;
      if (config.nextAllowed && config.nextAllowed[task.status]) {
        task.allowed_transitions = config.nextAllowed[task.status];
      } else {
        task.allowed_transitions = [];
      }
      return jsonResponse({ task: Object.assign({}, task), message: null });
    }
    return jsonResponse({});
  }

  function fetchStub(url, init) {
    const request = init || {};
    calls.push({
      url: String(url),
      method: request.method || "GET",
      body: typeof request.body === "string" && request.body.length ? JSON.parse(request.body) : null,
    });
    return defaultFetch(String(url), request);
  }

  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = new FakeNode("div");
      node.id = selector.startsWith("#") ? selector.slice(1) : "";
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

  const documentListeners = new Map();
  const document = {
    documentElement: new FakeNode("html"),
    get activeElement() {
      return activeElement;
    },
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
    EventSource: class {
      constructor(url) {
        this.url = url;
      }
      addEventListener() {}
      close() {}
    },
    URLSearchParams,
    window: { addEventListener: () => {} },
    console,
    // toast 的自动消失在本组用例里不需要：留成 no-op，断言才不会被定时器抢跑。
    setTimeout: () => 0,
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
      "\nglobalThis.__taskboardTransitionsTest = {"
        + " state, bindEvents, renderTaskBoard, taskCardNode, openTaskMoveMenu, closeTaskMoveMenu,"
        + " changeTaskStatus, taskAllowedTransitions };"
        + "\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__taskboardTransitionsTest;
  api.state.member = owner;
  api.state.members = [owner];
  api.state.agents = [];
  api.state.channels = [channel("ch_1", "设计系统重构")];
  api.state.dms = [];
  api.state.activeChannelId = "ch_1";
  api.state.tasks = tasks.map((task) => Object.assign({}, task));
  api.state.boardTasks = api.state.tasks.slice();
  api.state.online = config.online === undefined ? true : config.online;
  api.state.ready = true;
  api.bindEvents();
  api.renderTaskBoard();

  const columnsRoot = elementFor("#taskColumns");

  function cards() {
    return columnsRoot.querySelectorAll("[data-task]");
  }

  const harness = {
    api,
    calls,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    columnsRoot,
    cards,
    card(taskId) {
      return cards().find((node) => node.dataset.task === taskId) || null;
    },
    column(status) {
      return columnsRoot.querySelectorAll(".col").find((node) => node.dataset.status === status) || null;
    },
    menu() {
      return elementFor("#taskMoveMenu");
    },
    menuItems() {
      return elementFor("#taskMoveMenu").querySelectorAll('[role="menuitem"]');
    },
    toastText() {
      return elementFor("#toastWrap").textContent;
    },
    async click(node) {
      assert.ok(node, "要点击的控件应当存在");
      await node.dispatchEvent({ type: "click", target: node });
      await settle();
    },
    async key(node, key) {
      await node.dispatchEvent({ type: "keydown", key, target: node });
      await settle();
    },
    /** 菜单的键盘事件挂在共享浮层上（真实 DOM 里由子项冒泡上来）：这里在浮层上派发、
        把 `target` 指向当前焦点项，与浏览器的事件链同形。 */
    async menuKey(key) {
      const menu = elementFor("#taskMoveMenu");
      await menu.dispatchEvent({ type: "keydown", key, target: activeElement || menu });
      await settle();
    },
    /** 拖拽四个事件都委派在 `#taskColumns` 上：派发到容器（`target` 仍是那张卡片 / 那一列）。 */
    async drag(node, type, extra) {
      await columnsRoot.dispatchEvent(Object.assign({ type, target: node }, extra || {}));
      await settle();
    },
    patches() {
      return calls.filter((call) => call.method === "PATCH");
    },
    renders() {
      return calls.filter((call) => call.method === "GET" && call.url.split("?")[0] === "/api/tasks");
    },
  };
  return harness;
}

/** 一个最小的 `dataTransfer` 替身（`dragstart` 写、`drop` 读）。 */
function transfer() {
  const data = {};
  return {
    effectAllowed: "",
    setData(type, value) {
      data[type] = value;
    },
    getData(type) {
      return data[type] || "";
    },
  };
}

async function startDrag(harness, taskId) {
  const card = harness.card(taskId);
  const dataTransfer = transfer();
  await harness.drag(card, "dragstart", { dataTransfer });
  return { card, dataTransfer };
}

/* ─── 1. 菜单项 = 载荷的 allowed_transitions（不是任何静态表副本） ─────────── */

test("卡片「流转」菜单只列载荷里的 allowed_transitions（顺序也照载荷）", async () => {
  // 这份集合在静态五态表里不存在（todo 只能到 in_progress / closed）：它出现只能是读了载荷。
  const allowed = ["done", "in_review"];
  const harness = createHarness({ tasks: [taskRecord({ allowed_transitions: allowed })] });

  const card = harness.card("t_1");
  assert.ok(card, "看板上应当有这张卡片");
  const trigger = card.querySelector("[data-task-menu]");
  assert.ok(trigger, "有待选目标的卡片右上角应当有流转按钮");

  await harness.click(trigger);

  assert.equal(harness.menu().hidden, false, "点按钮打开菜单");
  assert.equal(trigger.getAttribute("aria-expanded"), "true", "触发器要声明展开态");
  assert.deepEqual(
    harness.menuItems().map((item) => item.dataset.moveTarget),
    allowed,
    "菜单项必须逐项等于载荷的集合，且保持载荷顺序",
  );
});

/* ─── 2. 点菜单项：一次正确的 PATCH + 重读看板 ─────────────────────────────── */

test("点菜单项发起一次 PATCH /api/tasks/{id}/status，并重读看板", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ allowed_transitions: ["done", "in_review"] })],
    nextAllowed: { done: [] },
  });
  const rendersBefore = harness.renders().length;

  await harness.click(harness.card("t_1").querySelector("[data-task-menu]"));
  const second = harness.menuItems()[1];
  assert.equal(second.dataset.moveTarget, "in_review");
  await harness.click(second);

  const patches = harness.patches();
  assert.equal(patches.length, 1, "一次选择只写一次");
  assert.equal(patches[0].url, "/api/tasks/t_1/status");
  assert.deepEqual(patches[0].body, { status: "in_review" });
  assert.equal(harness.menu().hidden, true, "选中后菜单收起");
  assert.ok(harness.renders().length > rendersBefore, "成功后要重读看板（后端事实，不做乐观更新）");
});

/* ─── 3. 悬停落点三态：合法 `.drag-over` / 非法 `.invalid-over` / 当前列不标态 ── */

test("拖拽悬停：合法列 `.drag-over`、非法列 `.invalid-over`、当前列不标态", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ allowed_transitions: ["in_progress"] })],
  });
  const { card, dataTransfer } = await startDrag(harness, "t_1");
  assert.equal(card.classList.contains("dragging"), true, "被拖起的卡片呈 `.dragging`");

  const legal = harness.column("in_progress");
  await harness.drag(legal, "dragover", { dataTransfer });
  assert.equal(legal.classList.contains("drag-over"), true, "合法落点标 `.drag-over`");
  assert.equal(legal.classList.contains("invalid-over"), false);

  const illegal = harness.column("done");
  await harness.drag(illegal, "dragover", { dataTransfer });
  assert.equal(illegal.classList.contains("invalid-over"), true, "非法落点标 `.invalid-over`");
  assert.equal(illegal.classList.contains("drag-over"), false);
  assert.equal(legal.classList.contains("drag-over"), false, "落点态同一时刻只属于一列");

  const current = harness.column("todo");
  await harness.drag(current, "dragover", { dataTransfer });
  assert.equal(current.classList.contains("drag-over"), false, "拖回原列不标合法态");
  assert.equal(current.classList.contains("invalid-over"), false, "拖回原列也不标非法态（它是无动作，不是错误）");

  // 指针离开看板：落点态必须跟着清掉，不能留一个没有行为支撑的高亮。
  await harness.drag(legal, "dragover", { dataTransfer });
  assert.equal(legal.classList.contains("drag-over"), true);
  await harness.drag(harness.columnsRoot, "dragleave", { dataTransfer, relatedTarget: null });
  assert.equal(legal.classList.contains("drag-over"), false, "离开看板后合法落点态清掉");
});

/* ─── 4. 非法松手：零请求 + toast 说明 ───────────────────────────────────── */

test("非法列松手不发任何请求，并给出一条说明「不是合法迁移」的 toast", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ allowed_transitions: ["in_progress"] })],
  });
  const { dataTransfer } = await startDrag(harness, "t_1");
  const illegal = harness.column("done");
  await harness.drag(illegal, "dragover", { dataTransfer });
  await harness.drag(illegal, "drop", { dataTransfer });

  assert.equal(harness.patches().length, 0, "非法落点必须零写入");
  const zh = harness.catalogs["zh-CN"];
  const expected = zh["toast.transitionInvalid"]
    .replace("{from}", zh["task.status.todo"])
    .replace("{to}", zh["task.status.done"]);
  assert.equal(harness.toastText(), expected, "toast 要说清「从哪个状态到哪个状态不是合法迁移」");
  assert.equal(illegal.classList.contains("invalid-over"), false, "松手后落点态清掉");
});

/* ─── 5. 合法松手：一次 PATCH ──────────────────────────────────────────── */

test("合法列松手发起一次 PATCH /api/tasks/{id}/status", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ allowed_transitions: ["in_progress"] })],
    nextAllowed: { in_progress: ["in_review"] },
  });
  const { dataTransfer } = await startDrag(harness, "t_1");
  const legal = harness.column("in_progress");
  await harness.drag(legal, "dragover", { dataTransfer });
  await harness.drag(legal, "drop", { dataTransfer });

  const patches = harness.patches();
  assert.equal(patches.length, 1, "合法落点只写一次");
  assert.equal(patches[0].url, "/api/tasks/t_1/status");
  assert.deepEqual(patches[0].body, { status: "in_progress" });
  assert.equal(harness.calls.some((call) => call.url === "/api/tasks/t_1/status" && call.method !== "PATCH"), false);
});

/* ─── 6. 终态：没有流转按钮，也没有合法落点 ─────────────────────────────── */

test("closed 卡片没有流转按钮、不是拖拽源，也没有任何合法落点", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ status: "closed", allowed_transitions: [] })],
  });

  const card = harness.card("t_1");
  assert.equal(card.querySelector("[data-task-menu]"), null, "终态不摆一个点不出东西的入口");
  assert.equal(card.getAttribute("draggable"), null, "终态也不是拖拽源（没有出边就没有可拖的动作）");

  const dataTransfer = transfer();
  await harness.drag(card, "dragstart", { dataTransfer });
  assert.equal(card.classList.contains("dragging"), false, "拖不起来");
  for (const status of ["todo", "in_progress", "in_review", "done", "closed"]) {
    const column = harness.column(status);
    await harness.drag(column, "dragover", { dataTransfer });
    assert.equal(column.classList.contains("drag-over"), false, `closed → ${status} 不标合法落点`);
    assert.equal(column.classList.contains("invalid-over"), false, `closed → ${status} 也不标非法落点（没有拖拽进行中）`);
  }

  await harness.drag(harness.column("done"), "drop", { dataTransfer });
  assert.equal(harness.patches().length, 0, "终态卡片松手同样零请求");
});

/* ─── 7. 离线：按钮禁用、菜单打不开、零请求 ─────────────────────────────── */

test("离线时流转按钮禁用，菜单打不开也不发请求", async () => {
  const harness = createHarness({
    online: false,
    tasks: [taskRecord({ allowed_transitions: ["in_progress"] })],
  });
  const trigger = harness.card("t_1").querySelector("[data-task-menu]");
  assert.ok(trigger, "离线时入口仍在（它说明的是「现在写不出去」，不是「没有这条路」）");
  assert.equal(trigger.disabled, true, "离线禁用");

  await harness.click(trigger);
  assert.equal(harness.menu().hidden, true, "禁用态点不开菜单");
  assert.equal(harness.patches().length, 0);
});

/* ─── 8. 键盘：打开进第一项、↑/↓ 循环、Esc 还焦点、Enter 激活 ──────────────── */

test("键盘可用：打开进第一项、↓ 移动、Esc 关闭并还焦点、Enter 激活一项", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ allowed_transitions: ["in_progress", "closed"] })],
    nextAllowed: { in_progress: ["in_review"] },
  });
  const trigger = harness.card("t_1").querySelector("[data-task-menu]");
  const menu = harness.menu();

  await harness.key(trigger, "Enter");
  assert.equal(menu.hidden, false, "Enter 打开菜单");
  const items = harness.menuItems();
  assert.equal(items.length, 2);
  assert.equal(harness.api.state.online, true);
  assert.equal(menuItemsFocus(harness), 0, "打开后焦点落在第一项");

  await harness.menuKey("ArrowDown");
  assert.equal(menuItemsFocus(harness), 1, "↓ 移到下一项");
  await harness.menuKey("ArrowDown");
  assert.equal(menuItemsFocus(harness), 0, "末项再 ↓ 回到第一项（循环）");

  await harness.menuKey("Escape");
  assert.equal(menu.hidden, true, "Esc 关闭菜单");
  assert.equal(trigger.getAttribute("aria-expanded"), "false");
  assert.equal(activeElement, trigger, "Esc 把焦点还给触发器");

  // 打开 → ↓ → Enter：激活第二项（closed），写一次 PATCH。
  await harness.key(trigger, "Enter");
  await harness.menuKey("ArrowDown");
  await harness.menuKey("Enter");
  const patches = harness.patches();
  assert.equal(patches.length, 1);
  assert.deepEqual(patches[0].body, { status: "closed" });
});

function menuItemsFocus(harness) {
  return harness.menuItems().indexOf(activeElement);
}

/* ─── 9. 真实 `querySelectorAll` 的形状：NodeList 没有 `indexOf` ───────────────
   浏览器证据里抓到过一次真 bug：菜单键盘面在 NodeList 上直接调 `indexOf` ⇒ TypeError，
   整个键盘面（↑↓ / Esc）静默失效，而 DOM 替身返回数组时用例是绿的。这条用例把
   「替身必须像 NodeList」钉住：把 `querySelectorAll` 换成没有 `indexOf` 的 array-like。 */
test("菜单键盘面在 NodeList 形状下仍然成立（不能依赖数组的 indexOf）", async () => {
  const harness = createHarness({
    tasks: [taskRecord({ allowed_transitions: ["in_progress", "closed"] })],
  });
  const trigger = harness.card("t_1").querySelector("[data-task-menu]");
  await harness.key(trigger, "Enter");

  const menu = harness.menu();
  const original = menu.querySelectorAll.bind(menu);
  const items = Array.from(original('[role="menuitem"]'));
  const nodeList = (selector) => {
    const nodes = Array.from(original(selector));
    const list = {
      length: nodes.length,
      forEach: (fn) => nodes.forEach(fn),
      item: (index) => nodes[index] || null,
      [Symbol.iterator]: () => nodes[Symbol.iterator](),
    };
    nodes.forEach((node, index) => {
      list[index] = node;
    });
    return list;
  };
  menu.querySelectorAll = nodeList;

  // 焦点已经站在第一项上：↓ 必须移到第二项（在 NodeList 形状下也一样）。
  assert.equal(items[1].dataset.moveTarget, "closed");
  await harness.menuKey("ArrowDown");
  assert.equal(items.indexOf(activeElement), 1, "NodeList 形状下 ↓ 仍然移动焦点");
  await harness.menuKey("Escape");
  assert.equal(menu.hidden, true, "NodeList 形状下 Esc 仍然关闭菜单");
});
