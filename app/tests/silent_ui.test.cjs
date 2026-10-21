/**
 * Studio 的 **结构化沉默**（`silent` 轮次结果）呈现测试（#58 / ADR-0008）。
 *
 * 接缝沿用既有 DOM 用例族（与 `narration_ui.test.cjs` / `agent_detail_ui.test.cjs` 同款）：`vm` 装载
 * `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点——不新开第二套前端测试框架、不新开 seam。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. `silent` 进摘要行（`SUMMARY_STATUSES`），**不是**失败卡：它不是 failed，也不是 `ignored`；
 * 2. 两种语言都有准确说法，且 `silent` 与 `ignored` 的说法**不同**（不能混为一谈）；
 * 3. 同一轮的 `silent` 与 `ignored` 收在同一行里，折叠说明按状态归组、各自点名；
 * 4. 低权重：着色消费既有 `.msg-outcome-line-state`（`--faint`），不落 `--error` 那一档。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const SOURCE_LOCALE = "zh-CN";

/* ─── DOM 替身（与既有用例族同形） ───────────────────────────────────────── */

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

function byClass(root, name) {
  return descendants(root).filter((node) => node.classList && node.classList.contains(name));
}

/* ─── 后端载荷的形状（照 HTTP 面真字段） ─────────────────────────────────── */

const AGENT = { id: "mem-1", kind: "agent", display_name: "小明", avatar_slot: 1 };
const OTHER = { id: "mem-2", kind: "agent", display_name: "云舟", avatar_slot: 2 };

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
      status: "silent",
      reason: "模型经结构化通道（declare_silence 工具）声明本轮沉默，没有产出正文",
      started_at: "2026-10-20T09:00:00Z",
      finished_at: "2026-10-20T09:00:01Z",
      consumed_budget: false,
      merged: false,
      reply_seq: null,
    },
    overrides || {},
  );
}

function createHarness() {
  const elements = new Map();
  let document = null;

  function newNode(tagName) {
    const node = new FakeNode(tagName);
    node.ownerDocument = document;
    return node;
  }

  function elementFor(selector) {
    if (!elements.has(selector)) elements.set(selector, newNode("div"));
    return elements.get(selector);
  }

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
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
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
      "\nglobalThis.__silentTest = { state, bindEvents, renderMessages, applyOutcomes, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__silentTest;
  api.state.member = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  api.state.members = [api.state.member, { ...AGENT }, { ...OTHER }];
  api.state.agents = [
    { ...AGENT, enabled: true, status: "online", model_id: "ollama:qwen2.5" },
    { ...OTHER, enabled: true, status: "online", model_id: "ollama:qwen2.5" },
  ];
  api.state.channels = [{ id: "ch-1", name: "design", last_seq: 0, last_read_seq: 0, unread_count: 0 }];
  api.state.activeChannelId = "ch-1";
  api.state.mode = "channel";
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();

  const messageList = elementFor("#messageList");
  return {
    api,
    catalogs: context.AlibotI18n.catalogs,
    document,
    messageList,
    /** 装一批消息 + 一批轮次结果，然后按真实的渲染入口画一遍。 */
    show(messages, outcomes) {
      api.state.messages = messages.slice();
      api.applyOutcomes((outcomes || []).slice());
      api.renderMessages();
    },
    summary() {
      return byClass(messageList, "outcome-summary")[0] || null;
    },
    lines() {
      return byClass(messageList, "msg-outcome-line");
    },
    lineFor(status) {
      return this.lines().find((line) => line.dataset.status === status) || null;
    },
    async expand() {
      const toggle = byClass(messageList, "msg-outcome-summary-toggle")[0];
      assert.ok(toggle, "摘要行必须有折叠控件");
      await messageList.dispatchEvent({ type: "click", target: toggle });
      await flush();
      return toggle;
    },
  };
}

const HUMAN = message(1, "大家看下这版配色", {
  kind: "human",
  author: { id: "owner", kind: "owner", display_name: "我", avatar_slot: 4 },
});

/* ─── 1. silent 进摘要行（不是失败卡） ──────────────────────────────────── */

test("silent 收进摘要行，而不是渲染成 failed 那块失败卡", () => {
  const harness = createHarness();
  harness.show([HUMAN], [outcome()]);

  const summary = harness.summary();
  assert.ok(summary, "silent 必须能被摘要行收住（否则它在消息流里凭空消失）");
  assert.match(summary.textContent, /小明/);
  // failed 的直接展开块是 `.msg-outcome`（带 --error 权重的那一块）：silent 不得走它。
  assert.deepEqual(byClass(harness.messageList, "msg-outcome"), [], "silent 不是失败");
  // 明细恒常渲染、默认折叠（`hidden`）：折叠态的消息流里只有那一行摘要。
  const details = byClass(harness.messageList, "msg-outcome-details")[0];
  assert.ok(details);
  assert.equal(details.hidden, true, "silent 的明细默认折叠");
});

test("展开后逐成员读到 silent 的说法与后端 reason 原文", async () => {
  const harness = createHarness();
  harness.show([HUMAN], [outcome()]);

  await harness.expand();
  const line = harness.lineFor("silent");
  assert.ok(line, "展开后有一行 silent 明细");
  const state = byClass(line, "msg-outcome-line-state")[0];
  assert.equal(state.textContent, harness.catalogs[SOURCE_LOCALE]["outcome.silent.label"]);
  const reason = byClass(line, "msg-outcome-line-reason")[0];
  assert.match(reason.textContent, /declare_silence|声明本轮沉默/, "reason 是后端原文，不重新措辞");
});

/* ─── 2. silent ≠ ignored（说法不同、同一行里可区分） ────────────────────── */

test("silent 与 ignored 是两种说法，且能收在同一行摘要里逐组区分", async () => {
  const harness = createHarness();
  harness.show(
    [HUMAN],
    [
      outcome(),
      outcome({
        id: "ro-2",
        member_id: OTHER.id,
        status: "ignored",
        reason: "话题与它无关",
      }),
    ],
  );

  const summary = harness.summary();
  assert.ok(summary);
  await harness.expand();
  const silent = harness.lineFor("silent");
  const ignored = harness.lineFor("ignored");
  assert.ok(silent && ignored, "两种结论各有一行明细");
  const silentLabel = byClass(silent, "msg-outcome-line-state")[0].textContent;
  const ignoredLabel = byClass(ignored, "msg-outcome-line-state")[0].textContent;
  assert.notEqual(silentLabel, ignoredLabel, "silent 不能被说成「自判忽略」");
  // 折叠说明按状态归组：两种状态都在里面各自点名（不是只留一条）。
  const why = byClass(harness.messageList, "msg-outcome-summary-why")[0];
  assert.ok(why);
  assert.match(why.textContent, new RegExp(harness.catalogs[SOURCE_LOCALE]["outcome.silent.note"]));
  assert.match(why.textContent, new RegExp(harness.catalogs[SOURCE_LOCALE]["outcome.ignored.note"]));
});

/* ─── 3. 两种语言都有说法 ──────────────────────────────────────────────── */

test("silent 的 label / note 在两种语言里都有取值，且不是回退成状态词原文", () => {
  const harness = createHarness();
  const zh = harness.catalogs[SOURCE_LOCALE];
  const en = harness.catalogs.en;
  for (const key of ["outcome.silent.label", "outcome.silent.note"]) {
    assert.ok(Object.prototype.hasOwnProperty.call(zh, key), `源语言缺词条：${key}`);
    assert.ok(Object.prototype.hasOwnProperty.call(en, key), `第二语言缺词条：${key}`);
  }
  assert.match(zh["outcome.silent.label"], /沉默/);
  assert.match(en["outcome.silent.label"], /ilence/);
  assert.notEqual(zh["outcome.silent.label"], zh["outcome.ignored.label"]);
  assert.notEqual(en["outcome.silent.label"], en["outcome.ignored.label"]);
});

/* ─── 4. 低权重：不落 --error 那一档 ─────────────────────────────────────── */

function cssRule(selector) {
  const css = fs.readFileSync(path.join(STUDIO_DIR, "app.css"), "utf8");
  const start = css.indexOf("\n" + selector + " {");
  if (start < 0) return null;
  return css.slice(start + 1, css.indexOf("}", start));
}

test("silent 的状态词沿用摘要行的低权重色位（--faint），不是失败那一档（--error）", () => {
  const state = cssRule(".msg-outcome-line-state");
  assert.ok(state, "摘要行的明细状态词有既有规则");
  assert.match(state, /var\(--faint\)/);
  assert.equal(/--error/.test(state), false, "silent 不是失败：不得用 --error 权重");
});

for (const [finished_reason, reason] of [
  ['exceed_max_iters', '达到迭代上限，结论可能不完整'],
  ['interrupted', '本轮已由用户中断'],
]) {
  test(`replied 的 ${finished_reason} 在消息流里呈现后端原因`, () => {
    const harness = createHarness();
    harness.show([HUMAN], [outcome({ status: 'replied', finished_reason, reason })]);
    assert.ok(harness.messageList.textContent.includes(reason));
  });
}
