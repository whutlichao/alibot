/**
 * 左栏（`#rail`，工作区导航；用户口语里的「左侧 dock」，仓库术语就叫 rail）在 **Agent 回复路径**
 * 上不得整表换节点。
 *
 * 症状（用户口径）：Agent 每回复一条消息，左栏就闪一下刷新。成因是回复路径上会连着走
 * `onStreamMessage` → `refreshChannelList` / `refreshDmList`、`onStreamTyping` /
 * `onStreamStatus` → `renderMembers`，以及 `scheduleRead`（约 120ms 后）→ 完整 `render()`，
 * 而三份名单（频道 / 私信 / 本机 Agent）每一趟都把子节点全部摘掉再挂新的一批。行节点被拆掉
 * 再造时，`.nav-row` 的 background/color 过渡与 `.presence.working` 的 pulse 动画都会重头开始，
 * 于是看起来像「左栏闪了一下并刷新」。
 *
 * 断言只落在外部可观察行为上：行按钮的**对象身份**（同一份成员集合时必须还是原来那个节点），
 * 以及真的变了的那一小块（未读徽标文本 / 状态点 class）。为了把「未变的行不 remove 再 insert、
 * 未变的 class 与文本不重写」也变成可检查的事实，替身节点把每一次 DOM 结构 / 文本 / class
 * 写入记进一份变更日志：这比只比对对象身份更狠 —— 同一个节点也可能被摘下来再挂回去
 * （`remove()` + `appendChild()`），那在真实浏览器里同样会重启过渡与动画。
 *
 * 接缝沿用既有 `*.test.cjs` 那一套：`vm` 装载 `i18n.js` + `app.js`，`fetch` 注入替身，
 * DOM 用替身节点，不新开第二套前端框架。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 变更日志（只记写入，读操作不记）──────────────────────────────── */

let MUTATIONS = [];

function resetMutations() {
  MUTATIONS = [];
}

function record(op, node, extra) {
  MUTATIONS.push(Object.assign({ op, node }, extra || {}));
}

/** `node` 是否落在 `root` 的子树里（含自己）。 */
function within(root, node) {
  let cursor = node;
  while (cursor) {
    if (cursor === root) return true;
    cursor = cursor.parentNode;
  }
  return false;
}

/** 某一段子树上发生过的全部写入（结构变更按**被移动的节点**归属）。 */
function mutationsWithin(root) {
  return MUTATIONS.filter((entry) => within(root, entry.node));
}

/** 变更的可读摘要：失败断言里直接贴出来，省得回头猜是哪种写入。 */
function describe(entries) {
  return entries
    .map((entry) => [entry.op, entry.name, describeNode(entry.node)].filter(Boolean).join(" "))
    .join("\n");
}

function describeNode(node) {
  if (!node) return "?";
  const id = (node.dataset && (node.dataset.channel || node.dataset.dm || node.dataset.member))
    || node.getAttribute("data-od-id")
    || "";
  const classes = node._classes ? Array.from(node._classes).join(".") : "";
  return [node.tagName, classes && "." + classes, id && "#" + id].filter(Boolean).join("");
}

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
    this._classes = new Set();
    const classes = this._classes;
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

  // `className` 与真实 DOM 一致：**替换**整串 class（不是叠加），于是「旧状态被清掉」这类
  // 断言验的是应用的真实行为，而不是替身的宽容。写入同时记进变更日志。
  set className(value) {
    record("class", this, { name: String(value) });
    this._classes.clear();
    String(value)
      .split(/\s+/)
      .filter(Boolean)
      .forEach((name) => this._classes.add(name));
  }

  get className() {
    return Array.from(this._classes).join(" ");
  }

  set textContent(value) {
    record("text", this, { name: String(value) });
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
    if (child.parentNode) child.parentNode.removeChild(child);
    record("append", child, { parent: this });
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
    record("remove", this);
    if (this.parentNode) this.parentNode.removeChild(this);
  }

  replaceChildren(...children) {
    this._textContent = "";
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }

  insertBefore(node, reference) {
    record("insert", node, { parent: this });
    if (node.parentNode) node.parentNode.removeChild(node);
    const index = reference ? this.children.indexOf(reference) : -1;
    node.parentNode = this;
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
    return node;
  }

  setAttribute(name, value) {
    record("attr", this, { name: name + "=" + String(value) });
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_whole, letter) => letter.toUpperCase());
      delete this.dataset[key];
    }
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

/** 让挂起的 `await` 链跑完（读路径是「一拍串一拍」，回复路径上还叠着 `scheduleRead`）。 */
async function settle() {
  for (let index = 0; index < 8; index += 1) await flush();
}

/** `index.html` 里带 `hidden` 的元素：替身节点的初始可见性从**同一个正本**读。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry[1]),
);

/** 一个 Agent（形状与工作台快照 / `GET /api/agents` 的项一致）。 */
function agent(id, name, status) {
  return {
    id,
    kind: "agent",
    display_name: name,
    avatar_slot: 1,
    role: "后端工程师",
    enabled: true,
    status,
  };
}

/** 一个最小频道行（形状与工作台快照的 `channels` 项一致）。 */
function channel(id, name, unread) {
  return { id, name, last_seq: 0, last_read_seq: 0, unread_count: unread || 0 };
}

/** 一个私信 target（`state.dms` 的项；`kind: 'dm'` 是它与频道的唯一区别）。 */
function dm(id, name, unread) {
  return {
    id,
    kind: "dm",
    peer: { id: "mem-2", display_name: name, avatar_slot: 2 },
    last_seq: 0,
    last_read_seq: 0,
    unread_count: unread || 0,
  };
}

function createHarness(options) {
  const config = options || {};
  const calls = [];
  const elements = new Map();
  const timers = [];
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };

  // 后端事实：`fetch` 替身返回**与装载时相同的**频道 / 私信 / Agent 集合，于是任何一次
  // 重新取列表都不会因为「集合变了」而合理地重建行 —— 变的只有测试显式拨动的那些字段。
  const snapshot = {
    channels: [channel("ch-1", "设计", 0), channel("ch-2", "后端", 2)],
    dms: [dm("dm-1", "小明", 0)],
    agents: [agent("mem-1", "小蓝", "online"), agent("mem-2", "小明", "online")],
  };

  function fetchStub(url, init) {
    const request = init || {};
    calls.push({
      url: String(url),
      method: request.method || "GET",
      body: request.body ? JSON.parse(request.body) : null,
    });
    if (config.fetch) return config.fetch(String(url), request);
    const target = String(url);
    if (target === "/api/channels") {
      return { ok: true, status: 200, json: async () => ({ channels: snapshot.channels }) };
    }
    if (target === "/api/dms") {
      return { ok: true, status: 200, json: async () => ({ dms: snapshot.dms }) };
    }
    if (target === "/api/agents") {
      return { ok: true, status: 200, json: async () => ({ agents: snapshot.agents, members: [] }) };
    }
    if (target.endsWith("/read")) {
      // 标读回写：频道返回最新频道表（未读归零），私信同理。
      const channelMatch = /^\/api\/channels\/([^/]+)\/read$/.exec(target);
      if (channelMatch) {
        const id = decodeURIComponent(channelMatch[1]);
        const updated = snapshot.channels.map((item) =>
          item.id === id ? { ...item, unread_count: 0, last_read_seq: 1 } : item);
        snapshot.channels = updated;
        return {
          ok: true,
          status: 200,
          json: async () => ({ channel: updated.find((item) => item.id === id), channels: updated }),
        };
      }
      const dmMatch = /^\/api\/dms\/([^/]+)\/read$/.exec(target);
      if (dmMatch) {
        const id = decodeURIComponent(dmMatch[1]);
        const updated = snapshot.dms.map((item) =>
          item.id === id ? { ...item, unread_count: 0, last_read_seq: 1 } : item);
        snapshot.dms = updated;
        return {
          ok: true,
          status: 200,
          json: async () => ({ dm: updated.find((item) => item.id === id), dms: updated }),
        };
      }
    }
    return { ok: true, status: 200, json: async () => ({}) };
  }

  function elementFor(selector) {
    if (!elements.has(selector)) {
      const node = new FakeNode("div");
      node.hidden = HIDDEN_IN_HTML.has(selector);
      elements.set(selector, node);
    }
    return elements.get(selector);
  }

  const documentListeners = new Map();
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

  // `setTimeout` / `clearTimeout` 用可手动冲洗的替身：`scheduleRead` 的 120ms 延后落在测试
  // 手里，于是「标读回写 → 完整 `render()`」这一段可以被确定性地覆盖（不靠真实等待）。
  const context = vm.createContext({
    document,
    fetch: fetchStub,
    URLSearchParams,
    window: { addEventListener: () => {} },
    console,
    setTimeout: (fn) => {
      timers.push(fn);
      return timers.length;
    },
    clearTimeout: (id) => {
      if (id) timers[id - 1] = null;
    },
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
      "\nglobalThis.__railIdentityTest = { state, bindEvents, render, renderChannels, renderDms,"
        + " renderMembers, onStreamMessage, onStreamStatus, onStreamTyping, setLocale, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__railIdentityTest;
  api.state.member = owner;
  api.state.members = [owner, ...snapshot.agents.map((item) => ({ ...item }))];
  api.state.agents = snapshot.agents.map((item) => ({ ...item }));
  api.state.channels = snapshot.channels.map((item) => ({ ...item }));
  api.state.dms = snapshot.dms.map((item) => ({ ...item }));
  api.state.activeChannelId = "ch-1";
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  const harness = {
    api,
    calls,
    catalogs: context.AlibotI18n.catalogs,
    elementFor,
    snapshot,
    channelList: elementFor("#channelList"),
    dmList: elementFor("#dmList"),
    memberList: elementFor("#memberList"),
    /** 冲刷所有挂起的定时器（`scheduleRead` 的标读回写），连同它派生的 `await` 链。 */
    async flushTimers() {
      const pending = timers.splice(0, timers.length).filter(Boolean);
      for (const fn of pending) await fn();
      await settle();
    },
    /** 只取某一棵子树上的写入（`resetMutations` 之后调用）。 */
    mutations(root) {
      return mutationsWithin(root);
    },
  };
  return harness;
}

/** 一行里的状态点（`.presence`）：行的直接子节点。 */
function presenceOf(row) {
  return row.children.find((child) => child.classList && child.classList.contains("presence")) || null;
}

/** 一行里的未读徽标（`.badge`）：行的直接子节点。 */
function badgeOf(row) {
  return row.children.find((child) => child.classList && child.classList.contains("badge")) || null;
}

/** 整份 rail（频道 / 私信 / Agent 三段）里 `aria-current="true"` 的行。 */
function currentRows(harness) {
  return [harness.channelList, harness.dmList, harness.memberList]
    .flatMap((root) => root.children || [])
    .filter((row) => row.getAttribute("aria-current") === "true");
}

/** 三份名单里每一行按钮的对象身份（键 = 后端 id）。 */
function rowIdentities(harness) {
  const collect = (root, key) => {
    const map = new Map();
    (root.children || []).forEach((row) => {
      if (row.dataset && row.dataset[key] !== undefined) map.set(row.dataset[key], row);
    });
    return map;
  };
  return {
    channels: collect(harness.channelList, "channel"),
    dms: collect(harness.dmList, "dm"),
    members: collect(harness.memberList, "member"),
  };
}

/** 一条 SSE 消息帧（形状与 `absorbMessage` / `onStreamMessage` 读的一致）。 */
function message(seq, body) {
  return {
    seq,
    id: "msg-" + seq,
    target_id: "ch-1",
    sender_id: "mem-1",
    body,
    created_at: "2026-05-01T00:00:00Z",
    mention_snapshot: [],
  };
}

/* ─── 1. 回复路径：三份名单的行节点身份不变 ─────────────────────────────── */

test("Agent 回复一条消息后，左栏三份名单的行节点仍是原来那些对象", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  const before = rowIdentities(harness);
  assert.equal(before.channels.size, 2, "频道两行");
  assert.equal(before.dms.size, 1, "私信一行");
  assert.equal(before.members.size, 2, "本机 Agent 两行");

  // 回复路径：正在查看的频道来一条消息 → 渲染消息流 + 标读（120ms 后 render()）+ 重取频道表。
  harness.api.onStreamMessage("ch-1", { message: message(1, "我来汇报一下") });
  // 同一轮回复通常还有 typing 与 member_status（成员集合不变，状态点会变）。
  harness.api.onStreamTyping("ch-1", { member_id: "mem-1", active: true });
  harness.api.onStreamStatus("ch-1", { member_id: "mem-1", status: "working" });
  await harness.flushTimers();

  const after = rowIdentities(harness);
  assert.equal(after.channels.size, 2);
  assert.equal(after.channels.get("ch-1"), before.channels.get("ch-1"), "频道行未被拆掉重建");
  assert.equal(after.channels.get("ch-2"), before.channels.get("ch-2"), "其它频道行也仍在原节点上");
  assert.equal(after.dms.get("dm-1"), before.dms.get("dm-1"), "私信行未被拆掉重建");
  assert.equal(after.members.get("mem-1"), before.members.get("mem-1"), "Agent 行未被拆掉重建");
  assert.equal(after.members.get("mem-2"), before.members.get("mem-2"), "其它 Agent 行也仍在原节点上");
});

test("成员集合不变时，行不 remove 再 insert，未变的 class 与文本也不重写", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  const scroller = harness.elementFor(".rail-scroll");
  const identities = rowIdentities(harness);
  const rows = [
    ...identities.channels.values(),
    ...identities.dms.values(),
    ...identities.members.values(),
  ];

  // 只观察「回复路径那一段」：先清空变更日志，再驱动路径，最后看这三份名单上发生了什么。
  resetMutations();
  harness.api.onStreamMessage("ch-1", { message: message(1, "收到") });
  harness.api.onStreamTyping("ch-1", { member_id: "mem-1", active: true });
  harness.api.onStreamStatus("ch-1", { member_id: "mem-1", status: "working" });
  await harness.flushTimers();

  assert.equal(harness.elementFor(".rail-scroll"), scroller, ".rail-scroll 容器不被替换");

  // 结构：三份名单里一个 remove / append 都不该有 —— 未变的行既不被摘掉也不被重新插入。
  const structural = rows.flatMap((row) => harness.mutations(row))
    .filter((entry) => entry.op === "remove" || entry.op === "append" || entry.op === "insert");
  const rowStructural = structural.filter((entry) => rows.includes(entry.node)
    || rows.includes(entry.parent));
  assert.equal(
    rowStructural.length,
    0,
    "回复路径上一次行级结构写入都不该有，实际：\n" + describe(rowStructural),
  );

  // 未变的行：class 与文本一个字节都不重写（否则 `.nav-row` 的 transition 会空转）。
  const otherRows = [identities.channels.get("ch-2"), identities.dms.get("dm-1"), identities.members.get("mem-2")];
  const untouched = otherRows.flatMap((row) => harness.mutations(row))
    .filter((entry) => entry.op === "class" || entry.op === "text" || entry.op === "attr");
  assert.equal(
    untouched.length,
    0,
    "事实没变的行不该被重写 class / 文本 / 属性，实际：\n" + describe(untouched),
  );

  // 当前频道仍是那一行，`is-active` 写回原节点（不是在新节点上）。
  const activeRow = rowIdentities(harness).channels.get("ch-1");
  assert.equal(activeRow, identities.channels.get("ch-1"), "当前行还是原来的节点");
  assert.equal(activeRow.classList.contains("is-active"), true, "is-active 在原节点上");
  assert.equal(activeRow.getAttribute("aria-current"), "true");
});

test("状态点真的变了：只改那一个节点上的状态点，行节点身份不变", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  const row = rowIdentities(harness).members.get("mem-2");
  const dot = presenceOf(row);
  assert.ok(dot, "Agent 行上有状态点");
  assert.equal(dot.classList.contains("online"), true, "起始空闲");

  resetMutations();
  // `member_status` 是状态点的正本（`onStreamTyping` 只改本地 typing 镜像，状态点仍按后端事实写）。
  harness.api.onStreamStatus("ch-1", { member_id: "mem-2", status: "working" });
  await settle();

  assert.equal(rowIdentities(harness).members.get("mem-2"), row, "改状态点不动行节点");
  assert.equal(presenceOf(row), dot, "状态点还是原来那个子节点（就地改 class，不换节点）");
  assert.equal(dot.classList.contains("working"), true, "状态点跟着后端事实变成「正在回复」");
  assert.equal(dot.classList.contains("online"), false, "旧态被清掉（不是两态叠着）");

  // 这一趟的写入只该落在那个状态点上（行本身与兄弟节点都不受影响）。
  const writes = harness.mutations(row);
  assert.deepEqual(
    Array.from(new Set(writes.map((entry) => entry.node))),
    [dot],
    "只写状态点这一个节点，实际：\n" + describe(writes),
  );
});

/* ─── 2. 未读数真的变了：徽标文本更新，行按钮身份仍不变 ─────────────────── */

test("未读数真的变了：徽标文本更新，行按钮身份仍不变", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  const row = rowIdentities(harness).channels.get("ch-2");
  assert.ok(badgeOf(row), "起始就有未读徽标（后端给了 2）");
  assert.equal(badgeOf(row).textContent, "2");
  const badge = badgeOf(row);

  // 后端事实变化：未读涨到 5。
  harness.api.state.channels = harness.api.state.channels.map((item) =>
    item.id === "ch-2" ? { ...item, unread_count: 5 } : item);
  resetMutations();
  harness.api.renderChannels();
  await settle();

  const later = rowIdentities(harness).channels.get("ch-2");
  assert.equal(later, row, "未读变化不换行节点");
  assert.equal(badgeOf(row), badge, "徽标节点本身也沿用（只改文本）");
  assert.equal(badgeOf(row).textContent, "5", "徽标文本跟着后端事实走");
  assert.equal(badgeOf(row).getAttribute("data-od-id"), "unread-ch-2", "徽标的定位名不变");

  // 未读归零：徽标应当消失，行节点仍然不变。
  harness.api.state.channels = harness.api.state.channels.map((item) =>
    item.id === "ch-2" ? { ...item, unread_count: 0 } : item);
  resetMutations();
  harness.api.renderChannels();
  await settle();
  assert.equal(rowIdentities(harness).channels.get("ch-2"), row, "徽标消失也不换行节点");
  assert.equal(badgeOf(row), null, "未读为零时徽标被摘掉（可见结果与改前一致）");
  assert.equal(
    harness.mutations(row).filter((entry) => entry.op === "remove" || entry.op === "append").length,
    0,
    "摘徽标不牵连行节点本身（行不从容器里被 remove 再 insert）",
  );
});

/* ─── 3. 切语言：左栏文案仍跟着词条走 ───────────────────────────────────── */

test("切语言后左栏行文案跟着词条走，行节点身份不变", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  const row = rowIdentities(harness).members.get("mem-1");
  const label = harness.catalogs["zh-CN"]["agentDetail.openAria"].replace("{name}", "小蓝");
  assert.equal(row.getAttribute("aria-label"), label, "起始是无障碍名（中文词条）");

  harness.api.setLocale("en");
  harness.api.render();
  await settle();

  const later = rowIdentities(harness).members.get("mem-1");
  assert.equal(later, row, "切语言也不换行节点");
  const englishLabel = harness.catalogs.en["agentDetail.openAria"].replace("{name}", "小蓝");
  assert.equal(row.getAttribute("aria-label"), englishLabel, "无障碍名跟着词条切换");
  assert.ok(englishLabel !== label, "两份词条确实不同（否则这条断言空转）");
});

test("切语言后状态点的无障碍名也跟着词条走（只剩状态没变的行也一样）", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  // 状态点在切语言前后**没有变**（都是 online）：只看 dataset.status 的守卫会让这格整格跳过，
  // 把 title / aria-label 留在上一门语言里。
  const row = rowIdentities(harness).members.get("mem-2");
  const dot = presenceOf(row);
  assert.ok(dot, "Agent 行上有状态点");
  const zh = harness.catalogs["zh-CN"]["status.online"];
  assert.equal(dot.getAttribute("aria-label"), zh, "起始是中文词条");

  harness.api.setLocale("en");
  harness.api.render();
  await settle();

  const en = harness.catalogs.en["status.online"];
  assert.ok(en !== zh, "两份词条确实不同（否则这条断言空转）");
  assert.equal(presenceOf(row), dot, "状态点还是原来那个节点");
  assert.equal(dot.getAttribute("aria-label"), en, "无障碍名跟着词条切换");
  assert.equal(dot.getAttribute("title"), en, "悬停文案也跟着词条切换");
  assert.equal(dot.classList.contains("online"), true, "状态本身没变（只是文案重铺）");
});

/* ─── 4. 行真的新增 / 删除时，结构跟着后端事实走 ────────────────────────── */

test("成员集合真的变了：新增行插入、消失行摘掉，其余行节点保持原样", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  const before = rowIdentities(harness);
  const channelOne = before.channels.get("ch-1");

  harness.api.state.channels = [
    { ...channel("ch-3", "前端", 1) },
    { ...channel("ch-1", "设计", 0) },
  ];
  resetMutations();
  harness.api.renderChannels();
  await settle();

  const after = rowIdentities(harness);
  assert.equal(after.channels.size, 2, "后端说两行就两行");
  assert.equal(after.channels.get("ch-1"), channelOne, "留下的那一行还是原节点");
  assert.equal(after.channels.has("ch-2"), false, "消失的频道行被摘掉");
  assert.ok(after.channels.get("ch-3"), "新增的频道行出现");
  assert.deepEqual(
    harness.channelList.children.map((row) => row.dataset.channel),
    ["ch-3", "ch-1"],
    "顺序按后端事实排",
  );
});

/* ─── 5. 当前行只有一行：Agent 详情与频道 / 私信不同时呈现 ─────────────── */

test("打开 Agent 详情时 rail 只有 Agent 行是当前行，关闭后频道行恢复", async () => {
  const harness = createHarness();
  harness.api.render();
  await settle();

  // 起始：当前频道行是唯一的当前行。
  const before = rowIdentities(harness);
  assert.equal(before.channels.get("ch-1").getAttribute("aria-current"), "true");
  assert.equal(before.channels.get("ch-1").classList.contains("is-active"), true);
  assert.equal(before.members.get("mem-1").getAttribute("aria-current"), "false");
  assert.equal(currentRows(harness).length, 1, "起始只有一行当前行");

  // 打开 Agent 详情：`activeAgentId` 是「打开着哪一个成员」的唯一正本，
  // 频道 / 私信行就此让位（不再呈现当前行）。
  harness.api.state.activeAgentId = "mem-1";
  harness.api.render();
  await settle();

  const opened = rowIdentities(harness);
  const agentRow = opened.members.get("mem-1");
  // 先看整份 rail 的计数：重叠时这里会读出 2 行 `aria-current="true"`。
  assert.equal(
    currentRows(harness).length,
    1,
    "整份 rail 在同一时刻至多一行是当前行，实际："
      + describe(currentRows(harness).map((row) => ({ op: "current", node: row }))),
  );
  assert.equal(agentRow.classList.contains("is-active"), true, "Agent 行是当前行");
  assert.equal(agentRow.getAttribute("aria-current"), "true");
  ["ch-1", "ch-2"].forEach((id) => {
    const row = opened.channels.get(id);
    assert.equal(row.classList.contains("is-active"), false, "频道行不得同时是当前行：" + id);
    assert.equal(row.getAttribute("aria-current"), "false", "频道行 aria-current 让位：" + id);
  });
  assert.equal(opened.dms.get("dm-1").classList.contains("is-active"), false, "私信行不得是当前行");
  assert.equal(opened.dms.get("dm-1").getAttribute("aria-current"), "false");
  assert.deepEqual(currentRows(harness), [agentRow], "整份 rail 恰好一行 aria-current=true");

  // 关闭详情：频道行按原判据恢复当前行，Agent 行让位。
  harness.api.state.activeAgentId = null;
  harness.api.render();
  await settle();

  const closed = rowIdentities(harness);
  assert.equal(closed.channels.get("ch-1").classList.contains("is-active"), true, "频道行恢复当前行");
  assert.equal(closed.channels.get("ch-1").getAttribute("aria-current"), "true");
  assert.equal(closed.members.get("mem-1").classList.contains("is-active"), false, "Agent 行让位");
  assert.equal(closed.members.get("mem-1").getAttribute("aria-current"), "false");
  assert.deepEqual(currentRows(harness), [closed.channels.get("ch-1")], "整份 rail 仍恰好一行当前行");
});

test("空态显隐：没有 Agent 时空态出现，有 Agent 时收起来", async () => {
  const harness = createHarness();
  const empty = harness.elementFor("#memberListEmpty");
  harness.api.render();
  await settle();
  assert.equal(empty.hidden, true, "有 Agent 时空态收起");

  harness.api.state.members = [{ id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 }];
  harness.api.renderMembers();
  await settle();
  assert.equal(empty.hidden, false, "没有 Agent 时空态出现");
  assert.equal(harness.memberList.children.length, 0, "名单是空的");
});
