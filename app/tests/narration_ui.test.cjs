/**
 * Studio 的 **旁白式交代**（旁白消息的呈现降档，#57 / #35 D1 判据-4 + D4 形态-a）的 DOM 行为测试。
 *
 * 接缝沿用既有那一套（与 `agent_detail_ui.test.cjs` / `search_ui.test.cjs` 同款）：`vm` 装载
 * `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点，**不新开第二套前端测试框架、不新开 seam**。
 * 「未来」的旁白走**真的那条路径**（`handleStreamEvent('message')` → `onStreamMessage` →
 * `absorbMessage` → `renderMessages`），不是测试自己往 `state.messages` 里塞一条再重绘。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 历史与未来的旁白都按消息自己的 `seq` 保留消息行，并以**一行**（正文不与作者名分两行）呈现；
 * 2. 它是**低权重**形态：`.msg.narration` + `.msg-narration-text`，不是普通 `.msg-text` 段落；
 * 3. 展开后可读到**关联轮次结果**的 `reason`；控件是原生 `<button>`，`aria-expanded` /
 *    `aria-controls` 全程可解析，重建后焦点回到同一个控件；
 * 4. 普通实质回复（以及任何判据拿不准的形态）**不**被降档——失效方向是「宁可少降档」；
 * 5. 形态-a：不并入 `round_outcome` 摘要行，不是只改 `msg-tag`。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const SOURCE_LOCALE = "zh-CN";

/* ─── DOM 替身（与既有用例族同形；选择器额外支持 `[attr="value"]`） ───────── */

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
    this.scrollHeight = 0;
    this.scrollTop = 0;
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

  /** `replaceChildren(container, nodes)` 靠它清空上一个渲染帧——缺了它旧节点会留在列表里。 */
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

  /** 真 DOM 里 `node.id = …` 就是写 `id` 属性（`aria-controls` 的解析靠这条等价）。 */
  set id(value) {
    this.attributes.id = String(value);
  }

  get id() {
    return Object.prototype.hasOwnProperty.call(this.attributes, "id") ? this.attributes.id : "";
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    if (Object.prototype.hasOwnProperty.call(this.attributes, name)) return this.attributes[name];
    // 真 DOM 里 `dataset.x = …` 与 `setAttribute('data-x', …)` 是同一件事（前者写后者），替身按
    // 同一条纪律做双向读：应用里两种写法都在用，读不到会让断言的失败原因变成替身的缺陷。
    if (name.startsWith("data-")) {
      const value = datasetValue(this, name);
      return value === undefined ? null : String(value);
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

  /** 键盘可达性的可观察面：原生按钮默认可 Tab 聚焦，`tabindex="-1"` / `disabled` 才把它摘出去。 */
  get tabIndex() {
    if (this.disabled) return -1;
    const declared = this.getAttribute("tabindex");
    return declared === null ? 0 : Number(declared);
  }

  focus() {
    this.ownerDocument.activeElement = this;
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
}

function matchesSelector(node, selector) {
  const withValue = /^\[([a-zA-Z0-9_-]+)="([^"]*)"\]$/.exec(selector);
  if (withValue) {
    const [, name, value] = withValue;
    const declared = node.getAttribute(name);
    if (declared !== null) return declared === value;
    if (name.startsWith("data-")) return datasetValue(node, name) === value;
    return false;
  }
  const attribute = /^\[([a-zA-Z0-9_-]+)\]$/.exec(selector);
  if (attribute) {
    const name = attribute[1];
    if (Object.prototype.hasOwnProperty.call(node.attributes, name)) return true;
    if (name.startsWith("data-")) return datasetValue(node, name) !== undefined;
    return false;
  }
  if (selector.startsWith(".")) return node.classList.contains(selector.slice(1));
  return node.tagName === selector;
}

function datasetValue(node, attributeName) {
  const key = attributeName
    .slice("data-".length)
    .replace(/-([a-z])/g, (_whole, letter) => letter.toUpperCase());
  if (node.dataset && node.dataset[key] !== undefined) return node.dataset[key];
  // 反向兜底：`setAttribute('data-x', …)` 之后 `dataset.x` 也该读得到（真 DOM 就是这么表现的）。
  return Object.prototype.hasOwnProperty.call(node.attributes, attributeName)
    ? node.attributes[attributeName]
    : undefined;
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

function descendants(node) {
  return node.descendants();
}

/** 按 class 收集子树里的节点（替身选择器不支持 `.a .b` 这类后代选择器）。 */
function byClass(root, name) {
  return descendants(root).filter((node) => node.classList && node.classList.contains(name));
}

/** `index.html` 里带 `hidden` 的元素：替身节点的初始可见性从**同一个正本**读。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

/* ─── 后端载荷的形状（照 HTTP 面真字段，不另造一套） ─────────────────────── */

const AGENT = { id: "mem-1", kind: "agent", display_name: "小明", avatar_slot: 1 };

/** 一条已落库的消息（`GET /api/channels/{id}/messages` 的项形状）。 */
function message(seq, body, overrides) {
  return Object.assign(
    {
      seq,
      body,
      kind: "agent",
      created_at: "2026-10-20T09:0" + (seq % 10) + ":00Z",
      author: AGENT,
    },
    overrides || {},
  );
}

/** 一条轮次结果（`GET /api/channels/{id}/round-outcomes` 的项形状）。 */
function outcome(overrides) {
  return Object.assign(
    {
      id: "ro-1",
      member_id: AGENT.id,
      target_id: "ch-1",
      base_seq: 1,
      status: "replied",
      reason: "模型自主判断需要回应",
      started_at: "2026-10-20T09:00:00Z",
      finished_at: "2026-10-20T09:00:01Z",
      consumed_budget: false,
      merged: false,
      reply_seq: 2,
    },
    overrides || {},
  );
}

function createHarness() {
  const elements = new Map();
  const requests = [];

  function newNode(tagName) {
    const node = new FakeNode(tagName);
    node.ownerDocument = document;
    return node;
  }

  function buildNode(selector) {
    const node = newNode("div");
    node.hidden = HIDDEN_IN_HTML.has(selector);
    return node;
  }

  function elementFor(selector) {
    if (!elements.has(selector)) elements.set(selector, buildNode(selector));
    return elements.get(selector);
  }

  // 先声明、后填充：`documentElement` 也要经 `newNode` 建（它会把 `ownerDocument` 指回来），
  // 而那时 `document` 这个绑定还没初始化。
  let document = null;
  document = {
    documentElement: newNode("html"),
    activeElement: null,
    querySelector: (selector) => elementFor(selector),
    querySelectorAll: () => [],
    createElement: (tagName) => newNode(tagName),
    createTextNode: (text) => {
      const node = newNode("#text");
      node.textContent = text;
      return node;
    },
    addEventListener: () => {},
  };

  const context = vm.createContext({
    document,
    fetch: async (url, init) => {
      requests.push({ url: String(url), init });
      // 事件流到达后的既有副作用会顺手刷新左栏（未读的正本在后端）：只给那条路径喂它要的形状，
      // 其余端点返回空载荷即可——本组用例断言的是消息流的呈现，不是左栏。
      if (String(url) === "/api/channels") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            channels: [{ id: "ch-1", name: "design", last_seq: 2, last_read_seq: 0, unread_count: 0 }],
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
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
      "\nglobalThis.__narrationTest = { state, bindEvents, renderMessages, messageNode,"
        + " handleStreamEvent, applyOutcomes, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__narrationTest;
  api.state.member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  api.state.members = [api.state.member, { ...AGENT }];
  api.state.agents = [{ ...AGENT, enabled: true, status: "online", model_id: "ollama:qwen2.5" }];
  api.state.channels = [{ id: "ch-1", name: "design", last_seq: 0, last_read_seq: 0, unread_count: 0 }];
  api.state.activeChannelId = "ch-1";
  api.state.mode = "channel";
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  const messageList = elementFor("#messageList");
  const harness = {
    api,
    requests,
    catalogs: context.AlibotI18n.catalogs,
    document,
    messageList,
    /** 装一批历史消息 + 一批轮次结果，然后按真实的渲染入口画一遍消息流。 */
    show(messages, outcomes) {
      api.state.messages = messages.slice();
      api.applyOutcomes((outcomes || []).slice());
      api.renderMessages();
    },
    /** 让一条消息**经事件流**到达（未来消息走的是这条路径，不是测试自己塞数组）。 */
    async arrive(payload) {
      api.handleStreamEvent("ch-1", { data: JSON.stringify(payload) }, "message");
      await flush();
    },
    /** `#messageList` 这一行的文章节点（按 `data-seq` 找）。 */
    rowAt(seq) {
      return descendants(messageList).find((node) => node.tagName === "article" && node.dataset.seq === seq) || null;
    },
    /** 点消息流里的某个控件（监听挂在 `#messageList` 上，因此带真实 target 派发）。
        第一个参数是 `data-*` 键的驼峰名（与 `dataset` 的写法一致）。 */
    async click(datasetKey, value) {
      const attribute = "data-" + datasetKey.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
      const selector = "[" + attribute + '="' + value + '"]';
      const target = descendants(messageList).find(
        (node) => node.classList && matchesSelector(node, selector),
      );
      assert.ok(target, `消息流里应当有这个控件：${selector}`);
      await messageList.dispatchEvent({ type: "click", target });
      await flush();
      return target;
    },
    toggleAt(seq) {
      return descendants(messageList).find(
        (node) => node.getAttribute("data-narration-toggle") === String(seq),
      ) || null;
    },
    expandAt(seq) {
      return descendants(messageList).find(
        (node) => node.getAttribute("data-narration-toggle") === String(seq),
      ) || null;
    },
  };
  return harness;
}

/** 一条旁白式交代的典型正文：整条被一对圆括号包住，短，只有一行。 */
const NARRATION = "（本轮消息与我无关，无需回应）";

/* ─── 1. 历史旁白：一行、低权重、保留自己的 seq ─────────────────────────── */

test("历史旁白按自己的 seq 渲染成一行低权重消息，不铺成普通回复段落", () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色", { kind: "human" }), message(2, NARRATION)], [outcome()]);

  const row = harness.rowAt(2);
  assert.ok(row, "旁白仍是一条消息：它占自己的 seq=2");
  assert.ok(row.classList.contains("narration"), "旁白走 .msg.narration 这族轻量形态");
  assert.ok(!row.classList.contains("outcome") && !row.classList.contains("outcome-summary"));
  // 「一行」：正文不作为独立段落块（`.msg-text`）另起一行，而是与作者名同一行。
  assert.deepEqual(byClass(row, "msg-text"), [], "旁白不再渲染成普通正文段落");
  const line = byClass(row, "msg-head")[0];
  assert.ok(line, "旁白只有一行：作者名 / 标签 / 正文 / 展开控件 / 时间都在 .msg-head 里");
  assert.ok(byClass(line, "msg-narration-text").length > 0, "正文就在这一行里");
  assert.match(byClass(line, "msg-narration-text")[0].textContent, /本轮消息与我无关/);
  assert.match(line.textContent, /小明/, "作者名照旧（它仍是这条消息的作者）");
  // 低权重不是只换个 tag：正文另有一个可展开的关联结论区。
  assert.ok(harness.toggleAt(2), "旁白带一个展开控件（可查看关联轮次结果的 reason）");
});

/* ─── 2. 展开：关联轮次结果的 reason 可读、aria 全程可解析 ───────────────── */

test("展开旁白读到关联轮次结果的 reason，收起后回到一行", async () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色", { kind: "human" }), message(2, NARRATION)], [outcome()]);

  const toggle = harness.toggleAt(2);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  const controls = toggle.getAttribute("aria-controls");
  assert.ok(controls, "折叠控件必须声明它展开的是哪一块");
  const detail = descendants(harness.messageList).find((node) => node.getAttribute("id") === controls);
  assert.ok(detail, "aria-controls 指向的节点必须存在（折叠态也是）");
  assert.equal(detail.hidden, true, "默认折叠：折叠态的旁白就是一行");
  assert.match(detail.textContent, /模型自主判断需要回应/, "展开后读到的是那一轮的 reason 原文");

  await harness.click("narrationToggle", 2);
  const expandedToggle = harness.toggleAt(2);
  assert.equal(expandedToggle.getAttribute("aria-expanded"), "true");
  const expandedDetail = descendants(harness.messageList).find(
    (node) => node.getAttribute("id") === expandedToggle.getAttribute("aria-controls"),
  );
  assert.equal(expandedDetail.hidden, false);

  await harness.click("narrationToggle", 2);
  assert.equal(harness.toggleAt(2).getAttribute("aria-expanded"), "false");
});

test("展开控件是原生按钮，键盘可 Tab 到，重建后焦点回到同一个控件", async () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色", { kind: "human" }), message(2, NARRATION)], [outcome()]);

  const toggle = harness.toggleAt(2);
  assert.equal(toggle.tagName, "button", "原生 <button>：Tab / Enter / Space 天然可用");
  assert.equal(toggle.disabled, false);
  assert.equal(toggle.tabIndex, 0, "不能被 tabindex=-1 摘出 Tab 序");
  assert.ok(toggle.getAttribute("aria-label"), "展开控件要有可访问名（不只靠一个箭头符号）");
  assert.match(toggle.getAttribute("aria-label"), /小明/);

  await harness.click("narrationToggle", 2);
  // 重建整条消息流后焦点必须回到**同一个控件**，否则「Tab 到它、Enter 展开」之后无法用键盘收起。
  assert.equal(harness.document.activeElement, harness.toggleAt(2));
});

/* ─── 3. 未来旁白：走真的那条到达路径 ───────────────────────────────────── */

test("经事件流到达的旁白同样降档，并保留它自己的 seq", async () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色", { kind: "human" })], []);

  await harness.arrive({ seq: 2, message: message(2, NARRATION) });
  harness.api.applyOutcomes([outcome()]);
  harness.api.renderMessages();

  const row = harness.rowAt(2);
  assert.ok(row, "到达的旁白是一条 seq=2 的消息");
  assert.ok(row.classList.contains("narration"), "未来消息与历史消息走同一套呈现判据");
  assert.deepEqual(byClass(row, "msg-text"), []);
  assert.match(harness.messageList.textContent, /本轮消息与我无关/);
});

/* ─── 4. 普通实质回复不误降档 ─────────────────────────────────────────── */

test("普通实质回复仍按普通 Agent 消息呈现，不带旁白的轻量形态", () => {
  const harness = createHarness();
  const reply = "看过了，我把对比度从 3:1 调到 4.6:1，明早再跑一遍无障碍检查。";
  harness.show([message(1, "大家看下这版配色", { kind: "human" }), message(2, reply)], [outcome()]);

  const row = harness.rowAt(2);
  assert.ok(row);
  assert.ok(!row.classList.contains("narration"), "实质回复不得被降档");
  assert.equal(byClass(row, "msg-text").length, 1, "普通回复照旧有自己的正文段落");
  assert.equal(harness.toggleAt(2), null, "普通回复不带旁白的展开控件");
});

test("判据只认「整条被一对圆括号包住且很短」的形态：拿不准的一律按普通消息呈现", () => {
  const cases = [
    // 整条括注但太长：更可能是真的在说一件事（引用、长句说明），宁可少降档。
    "（" + "这次改动涉及配色、间距和两处图标，我先按设计稿把前三页改完再回来同步进度" + "）",
    // 多行括注：真内容常见形态（清单 / 引用）。
    "（第一步先改左栏）\n（第二步再改消息流）",
    // 中途就闭合的第二对括号：不是「整条被一对括住」。
    "（一）（二）",
    // 方括号 / 书名号不是括注：它们是标题式强调，可能就是真实回复。
    "【重要】明天发版",
    "《配色规范》我下午补",
    // 括号只在一边：不构成成对括注。
    "（这段话没有右括号",
    "这段话没有左括号）",
  ];
  cases.forEach((body, index) => {
    const harness = createHarness();
    harness.show([message(1, "大家看下这版配色", { kind: "human" }), message(2, body)], []);
    const row = harness.rowAt(2);
    assert.ok(row, `第 ${index + 1} 种形态应当仍然是一条消息`);
    assert.ok(
      !row.classList.contains("narration"),
      `判据拿不准时必须按普通消息呈现（宁可少降档）：${body.slice(0, 12)}…`,
    );
    assert.equal(byClass(row, "msg-text").length, 1, "普通呈现有自己的正文段落");
  });
});

test("人类消息即使整条被括注包住也不降档：旁白只属于 Agent 的消息", () => {
  const harness = createHarness();
  harness.show([message(1, "（先记一笔）", { kind: "human", author: { id: "owner", kind: "owner", display_name: "我", avatar_slot: 4 } })], []);

  assert.ok(!harness.rowAt(1).classList.contains("narration"), "人写的话不做「旁白」这个判断");
});

/* ─── 5. 形态-a：不并入 round_outcome 摘要行、不是只改 msg-tag ───────────── */

test("旁白不并入 round_outcome 摘要行：它仍是自己的消息行，摘要行原样留在它自己的锚点上", () => {
  const harness = createHarness();
  harness.show(
    [message(1, "大家看下这版配色", { kind: "human" }), message(2, NARRATION)],
    // 两个成员各自的一轮：一条 replied（→ 旁白消息 seq=2）、一条 ignored（→ 摘要行 @ base_seq 1）。
    [
      outcome(),
      outcome({ id: "ro-2", member_id: "mem-2", status: "ignored", reason: "话题与它无关", reply_seq: null }),
    ],
  );

  const row = harness.rowAt(2);
  assert.ok(row, "旁白保留自己的 message 行");
  assert.equal(row.getAttribute("role"), null, "它不是一条「非消息」的摘要行（摘要行是 role=status）");
  // 摘要行仍是 base_seq=1 下面**另外**一块，旁白没有被搬进去。
  const summary = byClass(harness.messageList, "outcome-summary")[0];
  assert.ok(summary, "ignored 的摘要行照旧存在");
  assert.ok(!summary.textContent.includes("本轮消息与我无关"), "旁白的正文不并入摘要行");
  assert.equal(summary.getAttribute("role"), "status");
  // 「不是只改 msg-tag」：降档后的行多了正文与可展开结论，而不是同一个普通消息只换个标签。
  assert.ok(byClass(row, "msg-narration-text").length > 0);
  assert.ok(harness.toggleAt(2));
});

test("旁白一行的标签与展开文案取自词条表（两种语言都有取值）", () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色", { kind: "human" }), message(2, NARRATION)], [outcome()]);

  const zh = harness.catalogs[SOURCE_LOCALE];
  const en = harness.catalogs.en;
  const row = harness.rowAt(2);
  const tag = byClass(row, "msg-tag")[0];
  assert.equal(tag.textContent, zh["narration.tag"], "旁白标签来自源语言词条");
  const toggle = harness.toggleAt(2);
  assert.match(toggle.textContent, new RegExp(zh["narration.expand"]));
  for (const key of ["narration.tag", "narration.expand", "narration.collapse", "narration.toggleAria", "narration.reasonMissing"]) {
    assert.ok(Object.prototype.hasOwnProperty.call(zh, key), `源语言缺词条：${key}`);
    assert.ok(Object.prototype.hasOwnProperty.call(en, key), `第二语言缺词条：${key}`);
  }
});

test("关联轮次结果读不到时不编造理由，但一行呈现与可展开面都还在", () => {
  const harness = createHarness();
  // 轮次结果没读到（接口失败时的既有降级：保留上一次已知值 ⇒ 这里就是空）。
  harness.show([message(1, "大家看下这版配色", { kind: "human" }), message(2, NARRATION)], []);

  const row = harness.rowAt(2);
  assert.ok(row.classList.contains("narration"));
  const toggle = harness.toggleAt(2);
  assert.ok(toggle, "可展开面与数据无关地存在（无障碍面不随数据抖动）");
  const detail = descendants(harness.messageList).find(
    (node) => node.getAttribute("id") === toggle.getAttribute("aria-controls"),
  );
  assert.equal(detail.hidden, true);
  assert.equal(detail.textContent, harness.catalogs[SOURCE_LOCALE]["narration.reasonMissing"]);
});

/* ─── 6. 「低权重」是色位降档，不是新造一套颜色 ──────────────────────────── */

/** 取 `app.css` 里某条规则（按**精确选择器**找，`{` 到 `}` 之间的声明体）。 */
function cssRule(selector) {
  const css = fs.readFileSync(path.join(STUDIO_DIR, "app.css"), "utf8");
  const start = css.indexOf("\n" + selector + " {");
  if (start < 0) return null;
  return css.slice(start + 1, css.indexOf("}", start));
}

test("旁白的低权重消费既有色位（正文降到 --muted），不硬编码任何颜色", () => {
  const row = cssRule(".msg.narration");
  assert.ok(row, "旁白行有自己的呈现规则（不是复用失败行 / 摘要行的）");
  const text = cssRule(".msg-narration-text");
  assert.ok(text, "旁白正文有自己的规则");
  assert.match(text, /var\(--muted\)/, "正文降一档到 --muted（普通正文是 --fg）");
  assert.match(cssRule(".msg.narration .msg-tag"), /var\(--faint\)/, "「旁白」capsule 比正文还轻");
  assert.match(cssRule(".msg-narration-toggle"), /var\(--faint\)/, "展开控件是低权重的，不抢阅读");
  const declarations = [row, text, cssRule(".msg-narration-reason"), cssRule(".msg-narration-toggle")].join("\n");
  assert.equal(
    /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/.test(declarations),
    false,
    "只按名字消费既有角色族色位，不硬编码 hex/rgb/hsl",
  );
});
