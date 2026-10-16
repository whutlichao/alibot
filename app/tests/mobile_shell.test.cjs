/**
 * 移动端抽屉壳（≤900px）的 DOM / 契约测试 —— issue #70。
 *
 * 接缝沿用 `i18n_ui.test.cjs` / `search_ui.test.cjs` 那一套：`vm` 装载 `i18n.js` + `app.js`，
 * `fetch` 注入替身，DOM 用替身节点，不新开第二套前端测试框架。
 *
 * 这一层管得住的是**两件在 Node 里可判定的东西**：
 *
 * 1. **样式表契约**：`app.css` 是「按媒体条件 + 选择器」摊平后求值的，于是可以问
 *    「900px 宽时 `.rail` 的 `transform` 是什么」「901px 宽时 `.mobile-topbar` 还是不是
 *    `display:none`」——正反两向都问一遍。这一层刻意不写死整条声明文本，只断言
 *    **票面点名的那几个属性**（`display` / `position` / `transform` / `visibility` /
 *    `flex-direction` / 宽度），所以样式表里改注释、改无关声明不会把它打红。
 * 2. **状态同步与焦点**：`.is-open` / `hidden` / `aria-expanded` 三者同进同出，
 *    焦点在 ≤900px 时被送进抽屉、收起时被还给触发者（**不留在屏幕外、看不见的控件上**）。
 *
 * Node 里没有排版引擎，真正的**计算值**（`getComputedStyle` 的 matrix、像素宽度、
 * `document.activeElement`）由本机浏览器实测给出（`ego-browser`），不在这里假装。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

function readStudio(file) {
  return fs.readFileSync(path.join(STUDIO_DIR, file), "utf8");
}

/* ─── 1. 样式表：摊平 + 按视口宽度求值 ──────────────────────────────────── */

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

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

/** 把整张样式表摊成有序的 `{ conditions, selector, declarations }`（`conditions` 为媒体条件）。 */
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

/** 只认两个本判据要用的条件：宽度两个方向 + 「减少动效」。
    其它条件（`prefers-color-scheme` 等）一律算「不生效」——本票的判据不落在它们上。 */
function mediaMatches(condition, options) {
  const max = /^\(max-width:\s*(\d+)px\)$/.exec(condition);
  if (max) return options.width <= Number(max[1]);
  const min = /^\(min-width:\s*(\d+)px\)$/.exec(condition);
  if (min) return options.width >= Number(min[1]);
  if (/^\(prefers-reduced-motion:\s*reduce\)$/.test(condition)) return Boolean(options.reducedMotion);
  return false;
}

/** 某个视口宽度下的选择器 → 声明表（按书写顺序合并，靠后的赢）。 */
function sheetAt(width, reducedMotion) {
  const options = { width, reducedMotion: Boolean(reducedMotion) };
  const map = new Map();
  for (const rule of flattenCss(stripComments(readStudio("app.css")), [], [])) {
    if (!rule.conditions.every((condition) => mediaMatches(condition, options))) continue;
    map.set(rule.selector, Object.assign(map.get(rule.selector) || {}, rule.declarations));
  }
  return map;
}

/** 生效取值：先取基础选择器，再让状态选择器覆盖（样式表里状态规则都写在基础规则之后）。 */
function effective(map, base, state) {
  const merged = Object.assign({}, map.get(base) || {});
  if (state && map.has(state)) Object.assign(merged, map.get(state));
  return merged;
}

test("≤900px：顶栏出现、rail 离屏、dock 是右侧抽屉、看板单列（正）", () => {
  const css = sheetAt(900);

  const topbar = effective(css, ".mobile-topbar");
  assert.equal(topbar.display, "flex", "≤900px 顶部栏必须出现");
  assert.equal(topbar.position, "sticky", "顶部栏是 sticky");

  const rail = effective(css, ".rail");
  assert.equal(rail.position, "fixed");
  assert.equal(rail.transform, "translateX(-100%)", "rail 的静止位是向左离屏");
  assert.equal(rail.visibility, "hidden", "离屏 rail 不能还能接焦点");
  const railOpen = effective(css, ".rail", ".rail.is-open");
  assert.equal(railOpen.transform, "none", ".is-open 把 rail 滑到位");
  assert.equal(railOpen.visibility, "visible");

  const scrim = effective(css, ".rail-scrim");
  assert.equal(scrim.position, "fixed", "遮罩铺满视口");
  assert.ok(scrim.background, "遮罩要有底色（token，不是裸色值）");
  assert.equal(effective(css, ".rail-scrim", ".rail-scrim[hidden]").display, "none");

  const dock = effective(css, ".dock");
  assert.equal(dock.position, "fixed");
  assert.equal(dock.transform, "translateX(100%)", "dock 的静止位是向右离屏");
  assert.equal(dock.visibility, "hidden");
  assert.equal(effective(css, ".dock", ".dock.is-open").transform, "none");
  assert.equal(effective(css, ".dock", ".dock.is-open").visibility, "visible");

  const columns = effective(css, ".board-cols");
  assert.equal(columns["flex-direction"], "column", "看板 ≤900px 单列纵向堆叠");
  const column = effective(css, ".col");
  assert.equal(column.width, "100%", "列宽从 236px 变成 100%");
  // 右抽屉自己的出口（原型 `[data-dock-close]`）：只在 ≤900px 出现。
  assert.equal(effective(css, ".dock-close").display, "grid");
});

test("「减少动效」档下抽屉不做过渡（打开态也必须立刻到位）", () => {
  const css = sheetAt(900, true);
  assert.equal(effective(css, ".rail").transition, "none");
  assert.equal(effective(css, ".rail", ".rail.is-open").transition, "none", "打开态漏掉就会留下 200ms 的滑入");
  assert.equal(effective(css, ".dock").transition, "none");
  assert.equal(effective(css, ".dock", ".dock.is-open").transition, "none");
});

test(">900px：三个状态类都不生效，桌面断点不回归（反）", () => {
  const css = sheetAt(901);

  assert.equal(effective(css, ".mobile-topbar").display, "none", "桌面没有顶部栏");
  assert.equal(effective(css, ".rail").transform, undefined, "桌面 rail 没有离屏位移");
  assert.equal(effective(css, ".rail").position, undefined, "桌面 rail 仍在文档流里");
  assert.equal(css.has(".rail.is-open"), false, "桌面没有 .rail.is-open 这条规则");
  assert.equal(css.has(".dock.is-open"), false, "桌面没有 .dock.is-open 这条规则");
  assert.equal(effective(css, ".dock").transform, undefined, "桌面 dock 不位移");
  assert.equal(effective(css, ".rail-scrim").display, "none", "桌面遮罩不存在");
  assert.equal(effective(css, ".dock-close").display, "none", "桌面没有右抽屉的关闭控件");
  // 既有的两档桌面宽度照旧：1080px 档把 dock 收窄，看板仍是横向滚动的固定 236px 列。
  assert.equal(effective(css, ".dock").width, "var(--dock-w-md)");
  assert.equal(effective(css, ".col").width, "var(--board-col-w)");
  assert.equal(effective(css, ".board-cols")["flex-direction"], undefined);
});

/* ─── 2. DOM 替身 ───────────────────────────────────────────────────────── */

class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.listeners = new Map();
    this.style = {};
    this.disabled = false;
    this.value = "";
    this.parentNode = null;
    this.ownerDocument = null;
    this._hidden = false;
    this._dataset = null;
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

  /** `dataset` 与 `data-*` 属性是同一份事实（真 DOM 就是这么反射的），
      于是 `[data-channel]` 这类选择器在替身上也照样命中。 */
  get dataset() {
    if (!this._dataset) {
      const node = this;
      const store = {};
      this._dataset = new Proxy(store, {
        set(target, key, value) {
          target[key] = value;
          node.attributes[
            "data-" + String(key).replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase())
          ] = String(value);
          return true;
        },
        deleteProperty(target, key) {
          delete target[key];
          delete node.attributes[
            "data-" + String(key).replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase())
          ];
          return true;
        },
      });
    }
    return this._dataset;
  }

  get id() {
    return this.attributes.id || "";
  }

  set className(value) {
    String(value)
      .split(/\s+/)
      .filter(Boolean)
      .forEach((name) => this.classList.add(name));
  }

  get hidden() {
    return this._hidden;
  }

  set hidden(value) {
    this._hidden = Boolean(value);
    if (this._hidden) this.attributes.hidden = "";
    else delete this.attributes.hidden;
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "hidden") this._hidden = true;
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name);
  }

  appendChild(child) {
    this._textContent = "";
    child.parentNode = this;
    child.ownerDocument = this.ownerDocument;
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

  replaceChildren(...nodes) {
    this._textContent = "";
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  async dispatchEvent(event) {
    const payload = Object.assign({ target: this, preventDefault() {} }, event);
    for (const listener of this.listeners.get(event.type) || []) await listener.call(this, payload);
  }

  contains(node) {
    for (let cursor = node; cursor; cursor = cursor.parentNode) {
      if (cursor === this) return true;
    }
    return false;
  }

  descendants() {
    return [this, ...this.children.flatMap((child) => child.descendants())];
  }

  querySelectorAll(selector) {
    const selectors = String(selector)
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    return this.descendants().filter(
      (node) => node !== this && selectors.some((part) => matchesSelector(node, part)),
    );
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  closest(selector) {
    const selectors = String(selector)
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    for (let node = this; node; node = node.parentNode) {
      if (selectors.some((part) => matchesSelector(node, part))) return node;
    }
    return null;
  }

  /** 真浏览器的 `focus()` 只会落在可见、可聚焦的控件上；替身记下「谁拿到了焦点」就够。 */
  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
}

function matchesSelector(node, selector) {
  let rest = selector.trim();
  let not = false;
  if (rest.startsWith(":not(")) {
    not = true;
    rest = rest.slice(5, -1).trim();
  }
  const tag = /^[a-zA-Z][a-zA-Z0-9-]*/.exec(rest);
  if (tag) {
    const hit = node.tagName === tag[0];
    if (not) return !hit;
    if (!hit) return false;
    rest = rest.slice(tag[0].length);
  }
  const tokens = rest.match(/(\.[\w-]+|#[\w-]+|\[[^\]]*\])/g) || [];
  const hit = tokens.every((token) => {
    if (token[0] === ".") return node.classList.contains(token.slice(1));
    if (token[0] === "#") return node.getAttribute("id") === token.slice(1);
    const body = token.slice(1, -1).trim();
    const equals = /^([\w-]+)="([^"]*)"$/.exec(body);
    if (equals) return node.getAttribute(equals[1]) === equals[2];
    return node.hasAttribute(body);
  });
  return not ? !hit : hit;
}

/** `index.html` 里带 `hidden` 的元素：替身的初始可见性从**同一个正本**读，不另手抄一份。 */
const HIDDEN_IN_HTML = new Set(
  (readStudio("index.html").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

/** 同理，`index.html` 里每个带 id 的节点的**初始属性**（`aria-expanded="false"` 这类
    首帧取值）也从那个正本读 —— 替身不手抄一份，于是「HTML 忘了写」会被测出来。 */
const ATTRIBUTES_IN_HTML = new Map(
  (readStudio("index.html").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => {
      const id = /\bid="([^"]+)"/.exec(tag);
      if (!id) return null;
      const attributes = {};
      const pattern = /([a-zA-Z0-9_:.-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g;
      let match = pattern.exec(tag);
      while (match) {
        attributes[match[1]] = match[2] === undefined ? (match[3] === undefined ? "" : match[3]) : match[2];
        match = pattern.exec(tag);
      }
      return ["#" + id[1], attributes];
    })
    .filter(Boolean),
);

/** 与 `index.html` 的 rail 同形的动作行（`firstFocusable()` 的 DOM 序就取这一串）。 */
const RAIL_ACTIONS = ["btnNewChannel", "btnSearch", "btnNewDm", "btnNewAgent"];

function createHarness(options) {
  const config = options || {};
  const width = config.width === undefined ? 900 : config.width;
  const elements = new Map();
  const documentListeners = new Map();
  const state = Object.assign({ channels: [], dms: [], activeChannelId: null }, config.state || {});

  function buildNode(selector) {
    const node = new FakeNode(selector === "#rail" ? "aside" : "div");
    if (selector) node.setAttribute("id", selector.slice(1));
    for (const [name, value] of Object.entries(ATTRIBUTES_IN_HTML.get(selector) || {})) {
      if (name === "class") node.className = value;
      else node.setAttribute(name, value);
    }
    node.hidden = HIDDEN_IN_HTML.has(selector);
    if (selector === "#rail") {
      const actions = new FakeNode("div");
      for (const id of RAIL_ACTIONS) {
        const button = new FakeNode("button");
        button.setAttribute("id", id);
        actions.appendChild(button);
      }
      node.appendChild(actions);
    }
    if (selector === "#dock") {
      // 与 `index.html` 同形的两个形态容器：成员那一栏的头部里有「关闭详情面板」
      // （只在 ≤900px 长出），任务线程里有「关闭任务线程」这个常驻控件。
      const agent = new FakeNode("div");
      agent.setAttribute("id", "dockAgent");
      agent.hidden = HIDDEN_IN_HTML.has("#dockAgent");
      const head = new FakeNode("div");
      const closePanel = new FakeNode("button");
      closePanel.setAttribute("id", "dockClose");
      closePanel.dataset.dockClose = "";
      head.appendChild(closePanel);
      agent.appendChild(head);
      const task = new FakeNode("div");
      task.setAttribute("id", "dockTask");
      task.hidden = HIDDEN_IN_HTML.has("#dockTask");
      const close = new FakeNode("button");
      close.setAttribute("id", "btnThreadClose");
      task.appendChild(close);
      node.appendChild(agent);
      node.appendChild(task);
      elements.set("#dockAgent", agent);
      elements.set("#dockTask", task);
      elements.set("#dockClose", closePanel);
      elements.set("#btnThreadClose", close);
    }
    return node;
  }

  function elementFor(selector) {
    if (!elements.has(selector)) elements.set(selector, buildNode(selector));
    const found = elements.get(selector);
    // 替身节点的「归属文档」要能在整棵子树里被读到（`focus()` 据此记下 activeElement）。
    for (const descendant of found.descendants()) descendant.ownerDocument = document;
    return found;
  }

  const document = {
    documentElement: new FakeNode("html"),
    activeElement: null,
    querySelector: (selector) => elementFor(selector),
    querySelectorAll: () => [],
    createElement: (tagName) => {
      const node = new FakeNode(tagName);
      node.ownerDocument = document;
      return node;
    },
    createTextNode: (text) => {
      const node = new FakeNode("#text");
      node.textContent = text;
      node.ownerDocument = document;
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

  // 默认替身返回一份「空工作区」快照：任何一次读都拿到合法形状，于是异步续跑不会
  // 在测试结束后抛出来（本文件的用例都不依赖具体数据）。
  const EMPTY_WORKSPACE = {
    member: { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 },
    members: [],
    channels: [],
    dms: [],
    agents: [],
    tasks: [],
    messages: [],
    outcomes: [],
    model_providers: [],
    local_providers: [],
    remote_consents: [],
    unread_total: 0,
    settings: { locale: "zh-CN", message_density: "comfortable", theme: "system" },
  };
  const fetchStub =
    config.fetch || (async () => ({ ok: true, status: 200, json: async () => EMPTY_WORKSPACE }));
  const context = vm.createContext({
    document,
    fetch: fetchStub,
    URLSearchParams,
    window: {
      addEventListener: () => {},
      matchMedia: (query) => ({ media: query, matches: width <= 900 }),
    },
    console,
    setTimeout: (fn) => setImmediate(fn),
    clearTimeout: () => {},
    EventSource: undefined,
  });
  vm.runInContext(readStudio("i18n.js"), context, { filename: "i18n.js" });
  const source = readStudio("app.js");
  const startup = /\napplyStaticI18n\(document\);\nbindEvents\(\);\nloadWorkspace\(\);\s*$/;
  assert.match(source, startup, "app startup calls should remain at the end of app.js");
  vm.runInContext(
    source.replace(
      startup,
      "\nglobalThis.__mobileShellTest = { state, bindEvents, openRail, closeRail, railOpen,"
        + " setDockOpen, setDockMode, renderMobileTopbar, mobileShell, firstFocusable, setLocale, t };\n",
    ),
    context,
    { filename: path.join(STUDIO_DIR, "app.js") },
  );

  const api = context.__mobileShellTest;
  api.state.channels = state.channels;
  api.state.dms = state.dms;
  api.state.activeChannelId = state.activeChannelId;
  api.bindEvents();

  return {
    api,
    width,
    catalogs: context.AlibotI18n.catalogs,
    document,
    elementFor,
    active: () => document.activeElement,
    /** 替身没有事件冒泡，这里按祖先链补上：委托处理器（挂在祖先上）才接得到。 */
    async clickNode(node) {
      for (let cursor = node; cursor; cursor = cursor.parentNode) {
        await cursor.dispatchEvent({ type: "click", target: node });
      }
      // 点击处理器里可能挂着不 await 的异步续跑（例如 `selectTarget`）：让它跑完再断言，
      // 免得上一条用例的尾巴在测试结束后才炸。
      await settle();
    },
    async click(selector) {
      await this.clickNode(elementFor(selector));
    },
    async key(key) {
      await document.dispatchEvent({ type: "keydown", key, target: new FakeNode("body") });
    },
  };
}

function channel(id, name) {
  return { id, name, description: "", last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

/** 让挂起的 `await` 链跑完（`app.js` 里的读路径都是「一拍串一拍」）。 */
async function settle() {
  for (let index = 0; index < 8; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/* ─── 3. 左抽屉：状态同步与键盘可达 ─────────────────────────────────────── */

test("≤900px：顶部栏按钮开左抽屉，状态三者同步，焦点进抽屉", async () => {
  const harness = createHarness({ width: 900, state: { channels: [channel("c1", "设计")] } });
  const rail = harness.elementFor("#rail");
  const scrim = harness.elementFor("#railScrim");
  const toggle = harness.elementFor("#railToggle");

  assert.equal(harness.api.railOpen(), false, "起始是收起态");
  assert.equal(scrim.hidden, true, "起始遮罩不出现");
  assert.equal(toggle.getAttribute("aria-expanded"), "false");

  await harness.click("#railToggle");

  assert.equal(harness.api.railOpen(), true, "点按钮后 .is-open 生效");
  assert.equal(rail.classList.contains("is-open"), true);
  assert.equal(scrim.hidden, false, ".is-open 与遮罩同步");
  assert.equal(toggle.getAttribute("aria-expanded"), "true", "aria-expanded 与类名同步");
  assert.ok(
    rail.contains(harness.active()),
    "打开后焦点必须落在抽屉里（不是在屏幕外的节点上、也不是停在触发按钮上）",
  );
  // DOM 序的第二个控件 = 真浏览器里 Tab 一次的落点（替身没有 Tab 键，这里按序递推）。
  const focusables = rail.querySelectorAll("button, a[href], input, select, textarea, [tabindex]");
  assert.ok(focusables.length >= 2, "抽屉里要有可继续 Tab 的控件");
  assert.equal(focusables[0], harness.active(), "打开时落在第一个控件上");
  focusables[1].focus();
  assert.equal(harness.active(), focusables[1], "Tab 一次的落点是抽屉里的下一个控件");
  assert.equal(focusables[1].id, "btnSearch");
});

test("≤900px：点遮罩 / 按 ESC / 选中导航行都收起抽屉，焦点还给触发者", async () => {
  const harness = createHarness({ width: 900, state: { channels: [channel("c1", "设计")] } });
  const rail = harness.elementFor("#rail");
  const scrim = harness.elementFor("#railScrim");
  const toggle = harness.elementFor("#railToggle");

  await harness.click("#railToggle");
  await harness.click("#railScrim");
  assert.equal(rail.classList.contains("is-open"), false, "点遮罩收起抽屉");
  assert.equal(scrim.hidden, true, "遮罩跟着收起");
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(harness.active(), toggle, "收起后焦点回到顶部栏按钮（可见控件）");

  await harness.click("#railToggle");
  assert.equal(rail.classList.contains("is-open"), true);
  await harness.key("Escape");
  assert.equal(rail.classList.contains("is-open"), false, "ESC 收起抽屉");
  assert.equal(harness.active(), toggle);

  // 选中一个导航行：抽屉盖着刚选中的内容，必须自己让开。
  await harness.click("#railToggle");
  const row = new FakeNode("button");
  row.dataset.channel = "c1";
  const list = harness.elementFor("#channelList");
  list.appendChild(row);
  await harness.clickNode(row);
  assert.equal(rail.classList.contains("is-open"), false, "选中频道后抽屉收起");
  assert.ok(harness.active() !== row, "焦点不能留在被收起的抽屉里的那个行上");
});

test("≤900px：dock 是右抽屉，开合同步 hidden 与 .is-open", async () => {
  const harness = createHarness({ width: 900 });
  const dock = harness.elementFor("#dock");
  const agent = harness.elementFor("#dockAgent");
  const task = harness.elementFor("#dockTask");
  const toggle = harness.elementFor("#btnDockToggle");
  const close = harness.elementFor("#btnThreadClose");

  assert.equal(dock.hidden, true, "起始没有内容");
  assert.equal(dock.classList.contains("is-open"), false);

  // 点看板卡片 = 开一条任务线程（dock 的第二种形态）：抽屉在同一个动作里打开。
  harness.api.state.activeTaskId = "t1";
  harness.api.setDockMode("task");
  assert.equal(dock.hidden, false, "有内容 = hidden 拿掉");
  assert.equal(dock.classList.contains("is-open"), true, "同一个动作里抽屉滑入");
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(task.hidden, false);
  assert.equal(agent.hidden, true, "另一种形态收起来");
  assert.equal(harness.active(), close, "打开后焦点进抽屉（第一个非 hidden 的控件）");

  close.focus();
  harness.api.setDockOpen(false);
  assert.equal(dock.hidden, true);
  assert.equal(dock.classList.contains("is-open"), false);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(harness.active(), toggle, "焦点从收起的抽屉里被挪回触发按钮");

  // 右抽屉没有遮罩，所以它有**自己的**出口（原型 `[data-dock-close]`）与 ESC 两条路。
  harness.api.setDockMode("agent");
  harness.api.setDockOpen(true);
  await harness.click("#dockClose");
  assert.equal(dock.classList.contains("is-open"), false, "点抽屉里的关闭控件收起它");
  assert.equal(dock.hidden, true);
  assert.equal(harness.active(), toggle, "关闭后焦点回到可见的触发按钮");

  harness.api.setDockOpen(true);
  await harness.key("Escape");
  assert.equal(dock.classList.contains("is-open"), false, "ESC 也收起右抽屉");
  assert.equal(dock.hidden, true);

  // 「收起来了」必须压得住下一次整体重绘：`render()` 每次都按内容形态调 `setDockMode()`，
  // 若它无条件显示，收起就只是一帧的假动作（打开着一条任务线程时按 ESC 会立刻弹回来）。
  harness.api.state.activeTaskId = "t1";
  harness.api.setDockMode("task", true);
  assert.equal(dock.classList.contains("is-open"), true, "有内容 + 显式打开 → 显示");
  harness.api.setDockOpen(false);
  harness.api.setDockMode("task");
  assert.equal(dock.classList.contains("is-open"), false, "整体重绘不得把人按过的收起推翻");
  assert.equal(dock.hidden, true);
  // 但显式打开（点卡片 / 点左栏 Agent 行，`reveal`）要能重新打开它。
  harness.api.setDockMode("task", true);
  assert.equal(dock.classList.contains("is-open"), true, "显式打开清掉「人收起来了」这一位");
});

test(">900px：抽屉的焦点行为一个字不动（桌面不回归）", async () => {
  const harness = createHarness({ width: 1200, state: { channels: [channel("c1", "设计")] } });
  const focusMarker = harness.elementFor("#btnSend");
  focusMarker.focus();

  harness.api.openRail();
  assert.equal(harness.api.mobileShell(), false);
  assert.equal(harness.active(), focusMarker, "桌面开抽屉不抢焦点");

  harness.api.setDockOpen(true);
  assert.equal(harness.active(), focusMarker, "桌面开 dock 不抢焦点");
  harness.api.setDockOpen(false);
  assert.equal(harness.active(), focusMarker, "桌面收 dock 也不抢焦点");
});

/* ─── 4. 顶部栏标题 = 当前 target ───────────────────────────────────────── */

test("顶部栏显示当前 target 标题（频道 #名 / 私信 @名 / 无 target 退回品牌名）", () => {
  const dm = { id: "d1", kind: "dm", peer: { display_name: "小明" } };
  const harness = createHarness({
    width: 900,
    state: { channels: [channel("c1", "设计")], dms: [dm], activeChannelId: null },
  });
  const title = harness.elementFor("#mtTitle");

  harness.api.renderMobileTopbar();
  assert.equal(title.textContent, "alibot", "没有 target 时不留空标题");

  harness.api.state.activeChannelId = "c1";
  harness.api.renderMobileTopbar();
  assert.equal(title.textContent, "#设计");

  harness.api.state.activeChannelId = "d1";
  harness.api.renderMobileTopbar();
  assert.equal(title.textContent, "@小明");
});

/* ─── 5. 新增文案进两份词条 ─────────────────────────────────────────────── */

test("顶部栏按钮的文案在两份词条里都取到值，且绑定指向同一个键", () => {
  const harness = createHarness({ width: 900 });
  assert.equal(harness.api.t("mobile.railToggle"), "打开导航");

  harness.api.setLocale("en");
  assert.equal(harness.api.t("mobile.railToggle"), "Open navigation");
  harness.api.setLocale("zh-CN");

  for (const locale of ["zh-CN", "en"]) {
    const value = harness.catalogs[locale]["mobile.railToggle"];
    assert.equal(typeof value, "string");
    assert.ok(value.length > 0);
  }
  const html = readStudio("index.html");
  assert.match(
    html,
    /<button[^>]*id="railToggle"[^>]*data-i18n-attr="[^"]*mobile\.railToggle[^"]*"/,
    "顶部栏按钮的 aria-label/title 必须挂在词条键上（不是裸文案）",
  );
});
