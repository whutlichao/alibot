/**
 * Studio 失败轮次块「看成员状态」的 DOM 行为测试。
 *
 * 接缝沿用既有 Studio DOM 用例族（与 `silent_ui.test.cjs` / `narration_ui.test.cjs` 同款）：
 * `vm` 装载 `i18n.js` + `app.js`，`fetch` 注入替身，DOM 用替身节点——不新开第二套前端测试框架。
 *
 * 断言只落在**外部可观察行为**上：
 * 1. 失败块里的按钮由**自己**携带失败成员的 id（不写死成员、不按名字匹配）；
 * 2. 点它打开的是**该成员**的整页详情：`GET /api/agents/<member_id>` 被请求，
 *    `state.mode === 'agent'` 且 `#dock` 让位（详情不再是 dock 的第三形态）；
 * 3. 已经停在**另一个成员**的详情时点它，仍切到失败成员（这是本票修复的行为）；
 * 4. 运行面板开着时点它，run 面板被收掉（否则下一次整体重绘会盖回 run）。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const SOURCE_LOCALE = "zh-CN";

/* ─── DOM 替身（与既有用例族同形；选择器支持 `[attr]` / `[attr="value"]`） ──── */

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
    // 真 DOM 里 `dataset.x = …` 与 `setAttribute('data-x', …)` 是同一件事：替身双向读。
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

/** 等到条件成立（`openAgentDetail` 是异步的，读详情还要走一次 fetch）。 */
async function until(predicate, label) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (predicate()) return;
    await flush();
  }
  assert.fail(label);
}

function byClass(root, name) {
  return root.descendants().filter((node) => node.classList && node.classList.contains(name));
}

/** `index.html` 里带 `hidden` 的元素：替身节点的初始可见性从**同一个正本**读。 */
const HIDDEN_IN_HTML = new Set(
  (fs.readFileSync(path.join(STUDIO_DIR, "index.html"), "utf8").match(/<[a-zA-Z][^>]*>/g) || [])
    .map((tag) => ({ id: /\bid="([^"]+)"/.exec(tag), hidden: /\shidden(\s|>|\/)/.test(tag) }))
    .filter((entry) => entry.id && entry.hidden)
    .map((entry) => "#" + entry.id[1]),
);

/* ─── 后端载荷的形状（照 HTTP 面真字段） ─────────────────────────────────── */

const AGENT = { id: "mem-1", kind: "agent", display_name: "小贾", avatar_slot: 1 };
const OTHER = { id: "mem-2", kind: "agent", display_name: "小红", avatar_slot: 2 };
const HUMAN = { id: "owner", kind: "owner", display_name: "我", avatar_slot: 4 };

/** `GET /api/agents/{id}` 的 `agent` 形状（详情面板要读的字段）。 */
function agentDetail(member, overrides) {
  return Object.assign(
    {
      ...member,
      role: "前端工程师",
      model_id: "ollama:qwen2.5",
      system_prompt: "你只回答一句话。",
      enabled: true,
      auto_claim: false,
      web_access: null,
      provider: "ollama",
      is_remote: false,
      remote_consent: false,
      status: "online",
      enabled_reason: "",
      stats: {
        channel_count: 1,
        round_count: 1,
        completed_task_count: 0,
        success_rate: null,
      },
    },
    overrides || {},
  );
}

function message(seq, body, overrides) {
  return Object.assign(
    {
      seq,
      body,
      kind: "human",
      created_at: "2026-10-20T09:0" + (seq % 10) + ":00Z",
      author: HUMAN,
    },
    overrides || {},
  );
}

/** 一条**失败**轮次结果（`GET /api/channels/{id}/round-outcomes` 的项形状）。 */
function failedOutcome(member, overrides) {
  return Object.assign(
    {
      id: "ro-" + member.id,
      member_id: member.id,
      target_id: "ch-1",
      base_seq: 1,
      status: "failed",
      reason: "模型调用超时",
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

  // 读详情的两条端点按真形状返回；其余端点给空载荷（本组用例只关心详情怎么被打开）。
  const fetchStub = async (url) => {
    requests.push(String(url));
    const target = String(url);
    const detailMatch = /^\/api\/agents\/([^/]+)$/.exec(target);
    if (detailMatch) {
      const member = [AGENT, OTHER].find((item) => item.id === decodeURIComponent(detailMatch[1]));
      return {
        ok: true,
        status: 200,
        json: async () => ({ agent: agentDetail(member || AGENT) }),
      };
    }
    if (target.endsWith("/global-mcp-servers")) {
      return { ok: true, status: 200, json: async () => ({ global_mcp_servers: [] }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
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
  // markdown.js 与 i18n.js 同层加载：它是纯函数模块，由 app.js 的 messageText() 调用（ADR-0037）。
  vm.runInContext(fs.readFileSync(path.join(STUDIO_DIR, "markdown.js"), "utf8"), context, {
    filename: "markdown.js",
  });
  const appPath = path.join(STUDIO_DIR, "app.js");
  const source = fs.readFileSync(appPath, "utf8");
  const startup = /\napplyStaticI18n\(document\);\nbindEvents\(\);\nloadWorkspace\(\);\s*$/;
  assert.match(source, startup, "app startup calls should remain at the end of app.js");
  vm.runInContext(
    source.replace(
      startup,
      "\nglobalThis.__outcomeInspectTest = { state, bindEvents, renderMessages, renderMembers,"
        + " applyOutcomes, t };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__outcomeInspectTest;
  api.state.member = { ...HUMAN };
  api.state.members = [{ ...HUMAN }, { ...AGENT }, { ...OTHER }];
  api.state.agents = [
    agentDetail(AGENT),
    agentDetail(OTHER),
  ];
  api.state.channels = [{ id: "ch-1", name: "design", last_seq: 0, last_read_seq: 0, unread_count: 0 }];
  api.state.activeChannelId = "ch-1";
  api.state.mode = "channel";
  api.state.online = true;
  api.state.ready = true;
  api.bindEvents();
  // 左栏成员行是「打开另一个成员详情」的真实入口（测试用它铺前提，不直接调内部函数）。
  api.renderMembers();

  const messageList = elementFor("#messageList");
  const memberList = elementFor("#memberList");
  const harness = {
    api,
    requests,
    catalogs: context.AlibotI18n.catalogs,
    document,
    messageList,
    memberList,
    dock: elementFor("#dock"),
    dockAgent: elementFor("#dockAgent"),
    viewAgent: elementFor("#viewAgent"),
    detailName: elementFor("#adName"),
    /** 装一批消息 + 一批轮次结果，然后按真实的渲染入口画一遍消息流。 */
    show(messages, outcomes) {
      api.state.messages = messages.slice();
      api.applyOutcomes((outcomes || []).slice());
      api.renderMessages();
    },
    /** 失败块里的「看成员状态」按钮（`.msg-outcome-act`）。 */
    inspectButton() {
      return byClass(messageList, "msg-outcome-act")[0] || null;
    },
    /** 点消息流里的某个控件（监听挂在 `#messageList` 上，因此带真实 target 派发）。 */
    async click(target) {
      await messageList.dispatchEvent({ type: "click", target });
      await flush();
    },
    /** 点左栏的某个 Agent 行（监听挂在 `#memberList` 上；这是打开详情的既有用户入口）。 */
    async clickMember(memberId) {
      const row = memberList.descendants().find((node) => node.dataset.member === memberId);
      assert.ok(row, "左栏应当有这一行：" + memberId);
      await memberList.dispatchEvent({ type: "click", target: row });
      await flush();
    },
    /** 该端点有没有被请求过（外部可观察面：打开详情必然读一次正本）。 */
    requested(url) {
      return requests.includes(url);
    },
  };
  return harness;
}

/* ─── 1. 按钮自己携带失败成员的 id ─────────────────────────────────────── */

test("失败块的「看成员状态」按钮携带该失败成员的 id", () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色")], [failedOutcome(AGENT)]);

  const button = harness.inspectButton();
  assert.ok(button, "failed 轮次块必须有「看成员状态」按钮");
  assert.equal(button.tagName, "button");
  assert.equal(button.textContent, harness.catalogs[SOURCE_LOCALE]["outcome.inspect"]);
  // 成员 id 由按钮自己携带：点击委托据此打开**这一个**成员，不写死、不按名字猜。
  assert.equal(button.getAttribute("data-inspect-member"), AGENT.id);
});

/* ─── 2. 点击打开该成员的详情（dock 原本收起时也打开） ──────────────────── */

test("点「看成员状态」打开失败成员的详情，而不是只把成员列表滑出来", async () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色")], [failedOutcome(AGENT)]);
  assert.equal(harness.dock.hidden, true, "前提：dock 原本是收起的");

  await harness.click(harness.inspectButton());
  await until(() => harness.requested("/api/agents/" + AGENT.id), "应当读取失败成员的详情正本");

  assert.equal(harness.api.state.activeAgentId, AGENT.id);
  assert.equal(harness.api.state.mode, "agent", "详情是主线视图");
  assert.equal(harness.viewAgent.hidden, false, "整页详情可见");
  assert.equal(harness.dock.hidden, true, "详情占据整条右侧，dock 让位");
  assert.equal(harness.detailName.textContent, AGENT.display_name);
});

/* ─── 3. dock 停在另一个成员的详情时，仍切到失败成员 ────────────────────── */

test("dock 停在另一个成员详情时，点失败块的按钮仍切到失败成员", async () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色")], [failedOutcome(AGENT)]);

  // 先把 dock 停在**另一个成员**的详情上（点左栏成员行，走的是既有用户入口）。
  await harness.clickMember(OTHER.id);
  await until(() => harness.requested("/api/agents/" + OTHER.id), "前提：另一位成员的详情已打开");
  assert.equal(harness.api.state.activeAgentId, OTHER.id);
  assert.equal(harness.detailName.textContent, OTHER.display_name);

  await harness.click(harness.inspectButton());
  await until(() => harness.requested("/api/agents/" + AGENT.id), "应当切到失败成员的详情");

  assert.equal(harness.api.state.activeAgentId, AGENT.id);
  assert.equal(harness.detailName.textContent, AGENT.display_name, "面板标题跟着换成失败成员");
  assert.equal(harness.api.state.mode, "agent");
  assert.equal(harness.viewAgent.hidden, false);
  assert.equal(harness.dock.hidden, true);
});

/* ─── 4. dock 停在运行面板时也能切过去（run 不再压回来） ────────────────── */

test("dock 停在运行面板时，点失败块也切到该成员详情并收掉 run 面板", async () => {
  const harness = createHarness();
  harness.show([message(1, "大家看下这版配色")], [failedOutcome(AGENT)]);
  harness.api.state.runPanel.open = true;

  await harness.click(harness.inspectButton());
  await until(() => harness.requested("/api/agents/" + AGENT.id), "应当读取失败成员的详情正本");

  // `render()` 的形态推导以 run 为先：不先收掉它，下一次整体重绘会把详情盖回 run 面板。
  assert.equal(harness.api.state.runPanel.open, false, "run 面板必须被收掉");
  assert.equal(harness.api.state.activeAgentId, AGENT.id);
  assert.equal(harness.api.state.mode, "agent");
  assert.equal(harness.viewAgent.hidden, false);
  assert.equal(harness.dock.hidden, true);
});
