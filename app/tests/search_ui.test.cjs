/**
 * Studio 搜索面（主线视图 + ⌘K 命令面板）的 DOM 行为测试。
 *
 * 接缝沿用既有那一套：`vm` 装载 `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点。
 * 本票把搜索从**弹层**改成**主线视图**，因此这里的断言按新形态写（票面明写这是用户可观察的
 * 有意变更），而不是放宽。断言只落在外部可观察行为上：
 *
 * 1. 搜索视图占消息流那块位置，频道头与 composer 让位；
 * 2. 三类命中共用一条结果行形态（`.result` / `.r-top` / `.r-title` / `.r-snip`），
 *    且 `<mark>` **只**按后端给的区间包（前端不找位置）；
 * 3. facet 是后端的过滤参数：切换档位换一次请求，不在前端过滤；
 * 4. `⌘K` / `Ctrl+K` 打开命令面板、`ESC` 关闭，频道组取自**当前工作台频道**；
 * 5. 新增文案在两份词条里都有取值。
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

/** 等一轮宏任务：`app.js` 的 debounce 与 `await` 链都靠它跑完。 */
function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

/** 四档 facet 的静态节点（与 `index.html` 同形的取值与顺序）。 */
/** 一个频道替身（含已读位置与 unread 三个字段：`markCurrentRead` 这类路径要用它们早退）。 */
function channel(id, name) {
  return { id, name, description: "", last_seq: 0, last_read_seq: 0, unread_count: 0 };
}

function facetChips() {
  return ["all", "message", "task", "agent"].map((facet) => {
    const chip = new FakeNode("button");
    chip.dataset.facet = facet;
    return chip;
  });
}

/** `index.html` 里带 `hidden` 的元素：替身节点的初始可见性从**同一个正本**读，不另手抄一份。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

function createHarness(options) {
  const config = options || {};
  const requests = [];
  const elements = new Map();
  const documentListeners = new Map();
  const state = config.state || {
    channels: [],
    activeChannelId: null,
  };

  async function fetchStub(url) {
    requests.push(url);
    if (config.fetch) return config.fetch(url);
    if (String(url).startsWith("/api/search")) return { ok: true, status: 200, json: async () => ({ hits: [] }) };
    return { ok: true, status: 200, json: async () => ({}) };
  }

  function buildNode(selector) {
    const node = new FakeNode("div");
    node.hidden = HIDDEN_IN_HTML.has(selector);
    if (selector === "#searchFacets") facetChips().forEach((chip) => node.appendChild(chip));
    return node;
  }

  function elementFor(selector) {
    if (!elements.has(selector)) elements.set(selector, buildNode(selector));
    return elements.get(selector);
  }

  const document = {
    documentElement: new FakeNode("html"),
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

  // `setTimeout` 用立即执行的替身：debounce 与重连都是「延后一拍」，测试要的是确定性。
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
      "\nglobalThis.__searchUiTest = { state, bindEvents, showSearchView, showMessagesView,"
        + " showTaskBoard, openCmd, closeCmd, renderSearchResults, setSearchFacet, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__searchUiTest;
  api.state.channels = state.channels;
  api.state.activeChannelId = state.activeChannelId;
  api.bindEvents();

  return {
    api,
    catalogs: context.AlibotI18n.catalogs,
    requests,
    elementFor,
    facets: elementFor("#searchFacets").children,
    input: elementFor("#searchInput"),
    results: elementFor("#searchResults"),
    error: elementFor("#searchError"),
    cmdOverlay: elementFor("#cmdOverlay"),
    cmdInput: elementFor("#cmdInput"),
    cmdList: elementFor("#cmdList"),
    viewSearch: elementFor("#viewSearch"),
    viewMessages: elementFor("#viewMessages"),
    taskBoard: elementFor("#taskBoard"),
    chanHead: elementFor("#chanHead"),
    composer: elementFor("#composer"),
    async key(key, modifiers) {
      return document.dispatchEvent({
        type: "keydown",
        key,
        metaKey: Boolean(modifiers && modifiers.metaKey),
        ctrlKey: Boolean(modifiers && modifiers.ctrlKey),
        preventDefault() {},
        target: { matches: () => false },
      });
    },
  };
}

function response(payload) {
  return { ok: true, json: async () => payload };
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function markTexts(node) {
  return descendants(node)
    .filter((child) => child.tagName === "mark")
    .map((child) => child.textContent);
}

function plainText(node) {
  return descendants(node)
    .filter((child) => child.tagName === "#text")
    .map((child) => child.textContent)
    .join("");
}

function rows(harness) {
  return harness.results.children;
}

function messageHit(body, ranges) {
  return {
    kind: "message",
    text: body,
    highlight_ranges: ranges,
    target: { id: "channel-1", kind: "channel", display_name: "检索频道" },
    message: { id: "msg-1", body, author: { display_name: "Owner" } },
  };
}

function taskHit(title, ranges) {
  return {
    kind: "task",
    text: title,
    highlight_ranges: ranges,
    target: { id: "channel-1", kind: "channel", display_name: "检索频道" },
    task: { id: "task_abcdef123456", target_id: "tgt-1", title, status: "todo" },
  };
}

function agentHit(text, ranges) {
  return {
    kind: "agent",
    text,
    highlight_ranges: ranges,
    target: null,
    agent: { id: "member-1", display_name: "检索助手", role: "检索工程师", model_id: "ollama:qwen2.5", status: "online" },
  };
}

/** 在搜索框里落笔并回车（Enter 立即发请求，不等 debounce）。 */
async function searchFor(harness, query) {
  harness.input.value = query;
  await harness.input.dispatchEvent({ type: "keydown", key: "Enter", preventDefault() {} });
}

/* ─── 1. 搜索是主线视图 ─────────────────────────────────────────────────── */

test("搜索视图占消息流那块位置：频道头与 composer 都让位，退出后消息流回来", async () => {
  const harness = createHarness({ state: { channels: [channel("c1", "检索频道")], activeChannelId: "c1" } });

  harness.api.showSearchView();

  assert.equal(harness.viewSearch.hidden, false, "搜索视图必须是主线视图，不能是弹层");
  assert.equal(harness.viewMessages.hidden, true, "搜索视图要占消息流那块位置");
  assert.equal(harness.taskBoard.hidden, true);
  assert.equal(harness.chanHead.hidden, true, "搜索视图下频道头（含页签）不在");
  assert.equal(harness.composer.hidden, true, "搜索视图下 composer 隐藏");

  harness.api.showMessagesView();

  assert.equal(harness.viewSearch.hidden, true);
  assert.equal(harness.viewMessages.hidden, false);
  assert.equal(harness.composer.hidden, false);
  assert.equal(harness.chanHead.hidden, false);
});

test("ESC 在搜索视图里退出到消息视图", async () => {
  const harness = createHarness({ state: { channels: [channel("c1", "检索频道")], activeChannelId: "c1" } });
  harness.api.showSearchView();

  await harness.key("Escape");

  assert.equal(harness.viewSearch.hidden, true);
  assert.equal(harness.viewMessages.hidden, false);
});

/* ─── 2. 结果行与高亮：一切按后端给的区间 ───────────────────────────────── */

test("三类命中共用一条结果行形态，<mark> 只覆盖后端给的区间", async () => {
  const harness = createHarness({
    fetch: async () => response({
      hits: [
        messageHit("Needle banana needle, needle", [{ start: 0, end: 6 }, { start: 14, end: 20 }]),
        taskHit("令牌 banana 令牌", [{ start: 0, end: 2 }]),
        agentHit("检索助手 · 检索工程师", [{ start: 7, end: 9 }]),
      ],
    }),
  });
  harness.api.showSearchView();
  await searchFor(harness, "needle");

  const [messageRow, taskRow, agentRow] = rows(harness);

  assert.deepEqual(
    [messageRow.tagName, taskRow.tagName, agentRow.tagName],
    // 只有任务命中今天有落地处（点开右侧线程）；消息 / Agent 命中没有可落地的面，
    // 因此不是「点了没反应」的按钮。
    ["div", "button", "div"],
  );
  assert.equal(taskRow.dataset.opentask, "task_abcdef123456");
  rows(harness).forEach((row) => {
    assert.ok(row.classList.contains("result"));
    assert.ok(row.querySelector(".r-top"));
    assert.ok(row.querySelector(".r-title"));
    assert.ok(row.querySelector(".r-snip"));
  });

  // 三处 needle 里只有后端给的两处被包上 <mark>，且文本按区间切开。
  assert.deepEqual(markTexts(messageRow), ["Needle", "needle"]);
  assert.equal(plainText(messageRow.querySelector(".r-title")), " banana , needle");
  assert.deepEqual(markTexts(taskRow), ["令牌"]);
  assert.deepEqual(markTexts(agentRow), ["检索"]);
  assert.equal(plainText(agentRow.querySelector(".r-title")), "检索助手 · 工程师");
});

test("区间缺失时整段呈现：后端没给区间，前端不自己找位置", async () => {
  const harness = createHarness({
    fetch: async () => response({ hits: [messageHit("needle needle", [])] }),
  });
  harness.api.showSearchView();
  await searchFor(harness, "needle");

  const row = rows(harness)[0];

  assert.deepEqual(markTexts(row), []);
  assert.equal(row.querySelector(".r-title").textContent, "needle needle");
});

test("查询保留首尾空白（字面子串的一部分）", async () => {
  const harness = createHarness();
  harness.api.showSearchView();

  await searchFor(harness, " needle ");

  assert.equal(harness.api.state.search.query, " needle ");
  assert.equal(harness.requests.at(-1), "/api/search?q=+needle+&facet=all");
});

/* ─── 3. facet 是后端的过滤参数 ──────────────────────────────────────────── */

test("切换 facet 换一次后端请求：结果集只换成这一类", async () => {
  const seen = [];
  const harness = createHarness({
    fetch: async (url) => {
      seen.push(url);
      const facet = new URLSearchParams(String(url).split("?")[1]).get("facet");
      if (facet === "task") return response({ hits: [taskHit("令牌任务", [{ start: 0, end: 2 }])] });
      return response({
        hits: [
          messageHit("令牌消息", [{ start: 0, end: 2 }]),
          taskHit("令牌任务", [{ start: 0, end: 2 }]),
        ],
      });
    },
  });
  harness.api.showSearchView();
  await searchFor(harness, "令牌");
  assert.equal(rows(harness).length, 2);

  const taskChip = harness.facets.find((chip) => chip.dataset.facet === "task");
  await harness.elementFor("#searchFacets").dispatchEvent({ type: "click", target: taskChip });
  await flush();

  assert.equal(seen.at(-1), "/api/search?q=%E4%BB%A4%E7%89%8C&facet=task");
  assert.equal(rows(harness).length, 1);
  assert.equal(rows(harness)[0].querySelector(".card-tag").textContent, "任务");
  assert.equal(taskChip.getAttribute("aria-pressed"), "true");
  assert.equal(harness.facets[0].getAttribute("aria-pressed"), "false");
});

test("后端失败留在结果面里，不伪装成零命中", async () => {
  const harness = createHarness({
    fetch: async () => {
      throw new TypeError("Failed to fetch");
    },
  });
  harness.api.showSearchView();
  await searchFor(harness, "needle");

  assert.equal(harness.error.hidden, false);
  assert.match(harness.error.textContent, /后端不可达/);
  assert.equal(rows(harness).length, 0);
});

test("更早的响应被丢弃（新查询先完成）", async () => {
  let resolveOlder;
  const harness = createHarness({
    fetch: async (url) => {
      const query = new URLSearchParams(String(url).split("?")[1]).get("q");
      if (query === "older") {
        return new Promise((resolve) => {
          resolveOlder = () => resolve(response({ hits: [messageHit("older result", [{ start: 0, end: 5 }])] }));
        });
      }
      return response({ hits: [messageHit("newer result", [{ start: 0, end: 5 }])] });
    },
  });
  harness.api.showSearchView();

  const olderRequest = searchFor(harness, "older");
  await searchFor(harness, "newer");
  resolveOlder();
  await olderRequest;

  assert.equal(harness.api.state.search.query, "newer");
  assert.equal(rows(harness).length, 1);
  assert.equal(rows(harness)[0].querySelector(".r-title").textContent, "newer result");
});

/* ─── 4. ⌘K 命令面板 ────────────────────────────────────────────────────── */

test("⌘K / Ctrl+K 打开命令面板，ESC 关闭", async () => {
  const harness = createHarness();
  assert.equal(harness.cmdOverlay.hidden, true);

  await harness.key("k", { metaKey: true });
  assert.equal(harness.cmdOverlay.hidden, false);

  await harness.key("Escape");
  assert.equal(harness.cmdOverlay.hidden, true);

  await harness.key("K", { ctrlKey: true });
  assert.equal(harness.cmdOverlay.hidden, false);
});

test("命令面板的频道组取自当前工作台频道，不是原型里的种子名", async () => {
  const harness = createHarness({
    state: {
      channels: [channel("c1", "检索频道"), channel("c2", "发布频道")],
      activeChannelId: "c1",
    },
  });

  await harness.key("k", { metaKey: true });

  const groups = harness.cmdList.children.filter((node) => node.tagName === "div");
  const items = harness.cmdList.children.filter((node) => node.tagName === "button");
  const labels = items.map((item) => item.querySelector(".grow").textContent);

  assert.deepEqual(groups.map((group) => group.textContent), ["跳转", "动作", "频道"]);
  assert.deepEqual(labels.slice(-2), ["#检索频道", "#发布频道"]);
  assert.deepEqual(
    labels.filter((label) => label.startsWith("#")).length,
    2,
    "频道组只列当前工作台的频道",
  );
});

test("命令面板只呈现真绑定的快捷键（M / T），不出现原型未绑定的 N / ⇧N / D / ⌘K", async () => {
  const harness = createHarness({ state: { channels: [channel("c1", "检索频道")], activeChannelId: "c1" } });

  await harness.key("k", { metaKey: true });

  const kbds = harness.cmdList.children
    .filter((node) => node.tagName === "button")
    .map((item) => item.querySelector(".kbd"))
    .filter(Boolean)
    .map((node) => node.textContent);

  // `⌘K` 真绑定的是**打开命令面板本身**，不是面板里某条命令，因此它不出现在这里。
  assert.deepEqual(kbds, ["M", "T"]);
});

test("命令面板里的「消息视图」真的跳回消息流", async () => {
  const harness = createHarness({ state: { channels: [channel("c1", "检索频道")], activeChannelId: "c1" } });
  harness.api.showSearchView();

  await harness.key("k", { metaKey: true });
  const first = harness.cmdList.children.find((node) => node.tagName === "button");
  await harness.elementFor("#cmdList").dispatchEvent({ type: "click", target: first });
  await flush();

  assert.equal(harness.cmdOverlay.hidden, true);
  assert.equal(harness.viewMessages.hidden, false);
});

/* ─── 5. 词条覆盖 ───────────────────────────────────────────────────────── */

test("本票新增的文案在两份词条里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "search.fieldPlaceholder",
    "search.fieldLabel",
    "search.facetsLabel",
    "search.facetAll",
    "search.facetMessage",
    "search.facetTask",
    "search.facetAgent",
    "search.sourceAgent",
    "search.snipAuthor",
    "search.snipTask",
    "search.snipAgent",
    "search.idleTitle",
    "search.idleBody",
    "search.emptyTitle",
    "search.emptyBody",
    "cmd.ariaLabel",
    "cmd.placeholder",
    "cmd.empty",
    "cmd.groupJump",
    "cmd.groupAction",
    "cmd.groupChannels",
    "cmd.messages",
    "cmd.board",
  ];

  ["zh-CN", "en"].forEach((locale) => {
    const catalog = harness.catalogs[locale];
    keys.forEach((key) => {
      assert.ok(catalog[key], `${locale} 缺少词条 ${key}`);
      assert.notEqual(catalog[key], key);
    });
  });
});
