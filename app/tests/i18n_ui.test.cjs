/**
 * 词条机制（i18n 地基）的 DOM/契约测试。
 *
 * 接缝沿用 `search_ui.test.cjs` 那套（`node --test` + DOM 替身 + `vm` 装载 studio 脚本），
 * 不新开第二套前端测试框架（规格决策 D17）。
 *
 * 这里断言的是**外部可观察行为**，不把任何一句具体译文写死成契约：
 *
 * 1. 两份词条覆盖**同一个键集**、无落单键、无空值、参数位一一对应；
 * 2. `index.html` 里每一个 `data-i18n` / `data-i18n-attr` 的键在两份词条里都存在；
 * 3. **裸中文扫描**：三件套（`index.html` / `app.js` / `app.css`）里不再有不经词条的中文
 *    （HTML 的中文必须挂在 `data-i18n` / `data-i18n-attr` 上，JS/CSS 里一个字都不许有）；
 * 4. `<html lang>` 与当前语言同一个取值；
 * 5. 设置弹窗的两个档位（语言 / 消息密度）读写：写走后端、界面立刻生效、选中态跟着走。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const CJK = /[\u4e00-\u9fff]/;
// 可本地化属性（`data-i18n-attr` 的适用面）：这几类之外的中文属性不属于文案。
const LOCALIZABLE_ATTRIBUTES = ["aria-label", "title", "placeholder", "alt"];

function readStudio(file) {
  return fs.readFileSync(path.join(STUDIO_DIR, file), "utf8");
}

/* ─── JS / CSS 字符串字面量的扫描（注释不算文案） ────────────────────────── */
function jsStringLiterals(source) {
  const out = [];
  let i = 0;
  let line = 1;
  while (i < source.length) {
    const character = source[i];
    if (character === "\n") {
      line += 1;
      i += 1;
      continue;
    }
    if (character === "/" && source[i + 1] === "/") {
      while (i < source.length && source[i] !== "\n") i += 1;
      continue;
    }
    if (character === "/" && source[i + 1] === "*") {
      const end = source.indexOf("*/", i + 2);
      const stop = end < 0 ? source.length : end;
      line += source.slice(i, stop).split("\n").length - 1;
      i = stop + 2;
      continue;
    }
    if (character === '"' || character === "'" || character === "`") {
      let j = i + 1;
      while (j < source.length) {
        if (source[j] === "\\") {
          j += 2;
          continue;
        }
        if (source[j] === character) break;
        if (character !== "`" && source[j] === "\n") break;
        j += 1;
      }
      out.push({ line, value: source.slice(i + 1, j) });
      line += source.slice(i + 1, j).split("\n").length - 1;
      i = j + 1;
      continue;
    }
    i += 1;
  }
  return out;
}

function stripBlockComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

/* ─── HTML 的绑定扫描：每个中文文案都必须挂在词条键上 ───────────────────── */
function htmlAttributePairs(tagBody) {
  const pairs = [];
  const pattern = /([a-zA-Z0-9_-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g;
  let match = pattern.exec(tagBody);
  while (match) {
    pairs.push([match[1], match[2] === undefined ? match[3] : match[2]]);
    match = pattern.exec(tagBody);
  }
  return pairs;
}

function scanHtml(html) {
  const stripped = html.replace(/<!--[\s\S]*?-->/g, "");
  const tagPattern = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;
  const stack = [];
  const unboundText = [];
  const unboundAttributes = [];
  const keys = [];
  let boundTextCount = 0;
  let lastIndex = 0;
  let match = tagPattern.exec(stripped);
  while (match) {
    const text = stripped.slice(lastIndex, match.index);
    if (text.trim() && CJK.test(text)) {
      const open = stack[stack.length - 1];
      if (open && open.textKeys.length) boundTextCount += 1;
      else unboundText.push(text.trim());
    }
    lastIndex = tagPattern.lastIndex;
    const closing = match[1] === "/";
    const tagName = match[2];
    const body = match[3] || "";
    const attributes = htmlAttributePairs(body);
    const textKeys = attributes
      .filter(([name]) => name === "data-i18n")
      .map(([, value]) => value);
    const attributeKeys = attributes
      .filter(([name]) => name === "data-i18n-attr")
      .flatMap(([, value]) => String(value).split(",").map((pair) => pair.split(":")[1]));
    keys.push(...textKeys, ...attributeKeys.map((key) => String(key).trim()));
    for (const [name, value] of attributes) {
      if (!LOCALIZABLE_ATTRIBUTES.includes(name) || !value || !CJK.test(value)) continue;
      const covered = attributes.some(
        ([attrName, attrValue]) =>
          attrName === "data-i18n-attr" &&
          String(attrValue)
            .split(",")
            .some((pair) => pair.split(":")[0].trim() === name),
      );
      if (!covered) unboundAttributes.push(`${name}="${value}"`);
    }
    if (!closing && !body.trimEnd().endsWith("/")) stack.push({ tagName, textKeys });
    else if (closing) stack.pop();
    match = tagPattern.exec(stripped);
  }
  const tail = stripped.slice(lastIndex);
  if (tail.trim() && CJK.test(tail)) unboundText.push(tail.trim());
  return {
    unboundText,
    unboundAttributes,
    keys,
    boundTextCount,
    unclosedTags: stack.map((entry) => entry.tagName),
  };
}

/* ─── DOM 替身 ─────────────────────────────────────────────────────────── */
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
  const attributeValue = /^\[([a-zA-Z0-9_-]+)="([^"]*)"\]$/.exec(selector);
  if (attributeValue) {
    return (
      node.attributes[attributeValue[1]] === attributeValue[2] ||
      node.dataset[attributeValue[1]] === attributeValue[2]
    );
  }
  if (selector.startsWith(".")) return node.classList.contains(selector.slice(1));
  return node.tagName === selector;
}

/** 等一轮宏任务：让 `saveSettings` 这类 `await` 链（fetch → applySettings → render）跑完。 */
function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

/** 与 `index.html` 同形的两个密度档位卡片（含各自的词条声明）。 */
function densityCard(value, titleKey, hintKey) {
  const card = new FakeNode("button");
  card.dataset.density = value;
  const title = new FakeNode("b");
  title.setAttribute("data-i18n", titleKey);
  const hint = new FakeNode("span");
  hint.setAttribute("data-i18n", hintKey);
  card.appendChild(title);
  card.appendChild(hint);
  return card;
}

function createHarness(options) {
  const config = options || {};
  const mode = { rejectPatch: Boolean(config.rejectPatch) };
  const elements = new Map();
  const calls = [];
  let settings = config.settings || { locale: "zh-CN", message_density: "comfortable" };
  // 提醒的替身存储：与后端同形——写路径返回**最新全表**，`GET` 也返回同一份。
  const reminders = (config.reminders || []).map((item) => ({ ...item }));

  async function fetchStub(url, init) {
    const method = (init && init.method) || "GET";
    const body = init && init.body ? JSON.parse(init.body) : null;
    calls.push({ url, method, body });
    if (mode.rejectPatch && url === "/api/settings" && method === "PATCH") {
      return {
        ok: false,
        status: 400,
        json: async () => ({ error: { code: "validation_failed", message: "工作区设置校验未通过" } }),
      };
    }
    if (url === "/api/settings" && method === "PATCH") {
      settings = { ...settings, ...body };
    }
    if (url === "/api/reminders" && method === "GET") {
      return { ok: true, status: 200, json: async () => ({ reminders: reminders.slice() }) };
    }
    if (url === "/api/reminders" && method === "POST") {
      // 归属频道的可读名由后端随行带出（替身照这个形状给）。
      const reminder = {
        id: "rm_" + (reminders.length + 1),
        text: String(body.text).trim(),
        time: body.time,
        channel_id: body.channel_id === undefined ? null : body.channel_id,
        channel_name: body.channel_id ? "设计系统重构" : "",
        created_at: "2026-10-16T10:00:00.000Z",
      };
      reminders.push(reminder);
      return { ok: true, status: 201, json: async () => ({ reminder, reminders: reminders.slice() }) };
    }
    if (url.startsWith("/api/reminders/") && method === "DELETE") {
      const id = decodeURIComponent(url.slice("/api/reminders/".length));
      const index = reminders.findIndex((item) => item.id === id);
      if (index < 0) {
        return {
          ok: false,
          status: 404,
          json: async () => ({ error: { code: "not_found", message: "提醒不存在" } }),
        };
      }
      const [removed] = reminders.splice(index, 1);
      return { ok: true, status: 200, json: async () => ({ reminder: removed, reminders: reminders.slice() }) };
    }
    return { ok: true, status: 200, json: async () => ({ settings }) };
  }

  function buildNode(selector) {
    if (selector === "#settingsLocaleSelect") return new FakeNode("select");
    if (selector === "#settingsDensity") {
      const group = new FakeNode("div");
      group.appendChild(
        densityCard("comfortable", "settings.densityComfortable", "settings.densityComfortableHint"),
      );
      group.appendChild(
        densityCard("compact", "settings.densityCompact", "settings.densityCompactHint"),
      );
      return group;
    }
    // 提醒：三处替身照 `index.html` 的初始态建（弹窗关着、圆点 `hidden`、时间格默认 09:00）。
    if (selector === "#remindersOverlay" || selector === "#reminderDot") {
      const node = new FakeNode(selector === "#reminderDot" ? "span" : "div");
      node.hidden = true;
      return node;
    }
    if (selector === "#reminderText" || selector === "#reminderTime") {
      const node = new FakeNode("input");
      node.value = selector === "#reminderTime" ? "09:00" : "";
      return node;
    }
    return new FakeNode("div");
  }

  function elementFor(selector) {
    if (!elements.has(selector)) elements.set(selector, buildNode(selector));
    return elements.get(selector);
  }

  const documentElement = new FakeNode("html");
  const document = {
    documentElement,
    activeElement: null,
    querySelector: (selector) => elementFor(selector),
    querySelectorAll: () => [],
    createElement: (tagName) => new FakeNode(tagName),
    createTextNode: (text) => {
      const node = new FakeNode("#text");
      node.textContent = text;
      return node;
    },
    addEventListener: () => {},
  };

  const context = vm.createContext({
    document,
    fetch: fetchStub,
    URLSearchParams,
    window: { addEventListener: () => {} },
    console,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(readStudio("i18n.js"), context, { filename: "i18n.js" });
  const appPath = path.join(STUDIO_DIR, "app.js");
  const source = fs.readFileSync(appPath, "utf8");
  const startup = /\napplyStaticI18n\(document\);\nbindEvents\(\);\nloadWorkspace\(\);\s*$/;
  assert.match(source, startup, "app startup calls should remain at the end of app.js");
  vm.runInContext(
    source.replace(
      startup,
      "\nglobalThis.__i18nTest = { state, applySettings, loadSettings, saveSettings,"
        + " openSettingsModal, renderSettingsModal, setLocale, applyStaticI18n, bindEvents,"
        + " loadReminders, renderReminderDot, renderRemindersModal, openRemindersModal,"
        + " closeRemindersModal, submitReminder, deleteReminder };\n",
    ),
    context,
    { filename: appPath },
  );

  const api = context.__i18nTest;
  // 真实启动路径就是「读回设置 → 铺界面」：这里照做，于是 `lang` 与密度档位从第一帧起
  // 就是后端说的那一档（下面的用例从同一状态出发）。
  api.applySettings(settings);

  return {
    api,
    catalogs: context.AlibotI18n.catalogs,
    locales: context.AlibotI18n.locales,
    calls,
    document,
    elementFor,
    settings: () => settings,
  };
}

/* ─── 1. 两份词条的键集与取值 ───────────────────────────────────────────── */
test("both catalogs cover the same key set with no orphans and no empty values", () => {
  const { catalogs, locales } = createHarness();

  assert.deepEqual([...locales].sort(), ["en", "zh-CN"]);
  const source = Object.keys(catalogs["zh-CN"]).sort();
  const second = Object.keys(catalogs.en).sort();
  assert.ok(source.length > 100, `词条表规模看起来不像全量覆盖：${source.length}`);
  assert.deepEqual(second, source, "两份取值必须覆盖同一个键集（落单键会让某语言露出键名）");
  for (const key of source) {
    assert.equal(typeof catalogs["zh-CN"][key], "string", `${key} 的源语言取值必须是字符串`);
    assert.equal(typeof catalogs.en[key], "string", `${key} 的第二语言取值必须是字符串`);
    assert.ok(catalogs["zh-CN"][key].length > 0, `${key} 的源语言取值不能为空`);
    assert.ok(catalogs.en[key].length > 0, `${key} 的第二语言取值不能为空`);
  }
});

test("parameter placeholders match between the two catalogs of every key", () => {
  const { catalogs } = createHarness();

  const parameters = (value) => (value.match(/\{(\w+)\}/g) || []).sort();
  for (const key of Object.keys(catalogs["zh-CN"])) {
    assert.deepEqual(
      parameters(catalogs.en[key]),
      parameters(catalogs["zh-CN"][key]),
      `${key} 的参数位在两份取值里必须一致`,
    );
  }
});

test("every string-catalog key used by app.js exists in both catalogs", () => {
  const { catalogs } = createHarness();
  const source = readStudio("app.js");
  const used = new Set();
  const pattern = /\b(?:t|hasKey)\(\s*'([a-zA-Z0-9._-]+)'/g;
  let match = pattern.exec(source);
  while (match) {
    used.add(match[1]);
    match = pattern.exec(source);
  }

  assert.ok(used.size > 100, `app.js 用到的词条键看起来太少：${used.size}`);
  for (const key of used) {
    assert.ok(
      Object.prototype.hasOwnProperty.call(catalogs["zh-CN"], key),
      `app.js 用了词条表里没有的键：${key}`,
    );
    assert.ok(Object.prototype.hasOwnProperty.call(catalogs.en, key), `app.js 的键在第二语言里缺失：${key}`);
  }
});

/* ─── 2. index.html 的绑定指向真实存在的键 ───────────────────────────────── */
test("every key declared in index.html exists in both catalogs", () => {
  const { catalogs } = createHarness();
  const scanned = scanHtml(readStudio("index.html"));

  assert.ok(scanned.keys.length > 50, `index.html 的静态文案声明看起来太少：${scanned.keys.length}`);
  assert.ok(scanned.boundTextCount > 50, `被绑定的中文文本节点看起来太少：${scanned.boundTextCount}`);
  for (const key of scanned.keys) {
    assert.ok(key, "data-i18n / data-i18n-attr 的键不能为空");
    assert.ok(
      Object.prototype.hasOwnProperty.call(catalogs["zh-CN"], key),
      `index.html 用了词条表里没有的键：${key}`,
    );
    assert.ok(
      Object.prototype.hasOwnProperty.call(catalogs.en, key),
      `index.html 的键在第二语言里缺失：${key}`,
    );
  }
});

/* ─── 3. 裸中文扫描（三件套） ────────────────────────────────────────────── */
test("index.html keeps no Chinese copy outside a string-catalog binding", () => {
  const scanned = scanHtml(readStudio("index.html"));

  assert.deepEqual(scanned.unclosedTags, [], "index.html 的元素必须成对闭合（文案搬运不能拆掉结构）");
  assert.deepEqual(scanned.unboundText, [], "这些中文文本节点没有挂 data-i18n");
  assert.deepEqual(scanned.unboundAttributes, [], "这些中文属性没有挂 data-i18n-attr");
});

test("app.js and app.css keep no Chinese string literals at all", () => {
  const js = jsStringLiterals(readStudio("app.js")).filter((literal) => CJK.test(literal.value));
  const css = jsStringLiterals(stripBlockComments(readStudio("app.css"))).filter((literal) =>
    CJK.test(literal.value),
  );

  assert.deepEqual(
    js.map((literal) => `app.js:${literal.line} ${literal.value}`),
    [],
    "app.js 里的中文文案必须搬进词条表，按 t(key) 取值",
  );
  assert.deepEqual(
    css.map((literal) => `app.css:${literal.line} ${literal.value}`),
    [],
    "app.css 里不能有语言文案（CSS 生成的标签改用 content: attr(...)）",
  );
});

/* ─── 4. <html lang> 与当前语言同一个取值 ───────────────────────────────── */
test("html lang follows the locale switch", () => {
  const harness = createHarness();

  // 启动时读回后端偏好并铺上（缺省就是源语言）。
  assert.equal(harness.document.documentElement.lang, "zh-CN");

  harness.api.setLocale("en");
  assert.equal(harness.document.documentElement.lang, "en");

  harness.api.setLocale("zh-CN");
  assert.equal(harness.document.documentElement.lang, "zh-CN");
});

test("applySettings drives lang and the message-density tier together", () => {
  const harness = createHarness();

  harness.api.applySettings({ locale: "en", message_density: "compact" });

  assert.equal(harness.document.documentElement.lang, "en");
  assert.equal(harness.document.documentElement.dataset.density, "compact");
  assert.equal(harness.api.state.settings.locale, "en");
  assert.equal(harness.api.state.settings.message_density, "compact");
});

test("unknown locale or density values fall back to the source tier", () => {
  const harness = createHarness();

  harness.api.applySettings({ locale: "fr", message_density: "spacious" });

  assert.equal(harness.document.documentElement.lang, "zh-CN");
  assert.equal(harness.document.documentElement.dataset.density, "comfortable");
});

/* ─── 5. 静态文案的铺法（文本 + 属性） ───────────────────────────────────── */
test("applyStaticI18n fills text nodes and localizable attributes from the catalogs", () => {
  const harness = createHarness();
  const catalogs = harness.catalogs;
  const root = new FakeNode("body");
  const label = new FakeNode("span");
  label.setAttribute("data-i18n", "rail.newChannel");
  const button = new FakeNode("button");
  button.setAttribute("data-i18n-attr", "title:rail.searchTitle,aria-label:rail.searchTitle");
  root.appendChild(label);
  root.appendChild(button);

  harness.api.setLocale("en");
  harness.api.applyStaticI18n(root);
  assert.equal(label.textContent, catalogs.en["rail.newChannel"]);
  assert.equal(button.getAttribute("title"), catalogs.en["rail.searchTitle"]);
  assert.equal(button.getAttribute("aria-label"), catalogs.en["rail.searchTitle"]);

  harness.api.setLocale("zh-CN");
  harness.api.applyStaticI18n(root);
  assert.equal(label.textContent, catalogs["zh-CN"]["rail.newChannel"]);
  assert.equal(button.getAttribute("title"), catalogs["zh-CN"]["rail.searchTitle"]);
});

/* ─── 6. 设置弹窗两个档位的读写 ─────────────────────────────────────────── */
test("the settings modal shows both tiers and no third tier", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  harness.api.renderSettingsModal();

  const select = harness.elementFor("#settingsLocaleSelect");
  const density = harness.elementFor("#settingsDensity");
  const cards = density.querySelectorAll("[data-density]");

  // 语言：两档，取值就是后端档位（`zh-CN` / `en`）。
  assert.deepEqual(
    select.children.map((option) => option.value),
    ["zh-CN", "en"],
  );
  // 消息密度：两档（`comfortable` / `compact`），**没有**主题这一档。
  assert.deepEqual(
    cards.map((card) => card.dataset.density),
    ["comfortable", "compact"],
  );
  assert.deepEqual(
    Object.keys(harness.api.state.settings).sort(),
    ["locale", "message_density"],
    "本票的设置面只有语言与消息密度两档（主题是 #71 的第三档）",
  );
  assert.equal(cards[0].classList.contains("is-on"), true, "缺省密度档要标出来");
});

test("choosing English writes the backend and switches the interface immediately", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  const select = harness.elementFor("#settingsLocaleSelect");
  select.value = "en";

  await select.dispatchEvent({ type: "change", target: select });
  await flush();

  const patch = harness.calls.filter((call) => call.method === "PATCH");
  assert.deepEqual(patch.map((call) => call.body), [{ locale: "en" }]);
  assert.equal(harness.api.state.settings.locale, "en");
  assert.equal(harness.document.documentElement.lang, "en");
  assert.equal(harness.elementFor("#settingsLocaleSelect").value, "en");
});

test("choosing the compact density writes the backend and applies the tier", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  const density = harness.elementFor("#settingsDensity");
  const compact = density.querySelectorAll("[data-density]")[1];

  await density.dispatchEvent({
    type: "click",
    target: { closest: (selector) => (selector === "[data-density]" ? compact : null) },
  });
  await flush();

  const patch = harness.calls.filter((call) => call.method === "PATCH");
  assert.deepEqual(patch.map((call) => call.body), [{ message_density: "compact" }]);
  assert.equal(harness.api.state.settings.message_density, "compact");
  assert.equal(harness.document.documentElement.dataset.density, "compact");
  assert.equal(compact.classList.contains("is-on"), true);
  assert.equal(
    density.querySelectorAll("[data-density]")[0].classList.contains("is-on"),
    false,
    "同一时刻只有一档是选中态",
  );
});

test("a rejected write leaves the local mirror and the language untouched", async () => {
  const harness = createHarness({ rejectPatch: true });
  harness.api.bindEvents();
  const select = harness.elementFor("#settingsLocaleSelect");
  select.value = "en";

  await select.dispatchEvent({ type: "change", target: select });
  await flush();

  // 写失败不改本地镜像，也不改语言偏好（界面不能声称一个没落库的取值）。
  assert.equal(harness.api.state.settings.locale, "zh-CN");
  assert.equal(harness.document.documentElement.lang, "zh-CN");
  assert.equal(select.value, "zh-CN", "选择器回到后端说的那一档");
  assert.equal(harness.elementFor("#settingsFormError").hidden, false, "失败要留在弹窗里，不静默");
});

/* ─── 7. composer 工具行（#66）：@提及 / 作为任务发送 / 任务线程回复框 ─────────
   验收面：① 工具行按钮位与顺序 ② @提及 追加 `@` + 聚焦 + 激活发送键 + 复用既有补全
   ③ 作为任务发送 预填正文 + 当前频道预选 + 取消不动 composer ④ 任务线程回复框没有工具行
   ⑤ 新增文案在两份词条里都取到值。 */

/** 带选区、焦点与行内样式的输入替身：`@提及` 与 `@` 补全都按**光标**判位置，
    因此替身不能只会存 `value`（`autosizeComposer()` 还要写 `style.height`）。 */
class FieldNode extends FakeNode {
  constructor(tagName) {
    super(tagName);
    this.style = {};
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.scrollIntoView = () => {};
    this.remove = () => {};
  }

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }
}

/** 只装载 composer 工具行所需的那几个元素：其余选择器给一个惰性替身（`bindEvents` 会挂到它们上）。 */
function createComposerHarness() {
  const nodes = new Map();

  function newNode(tagName) {
    const node = new FakeNode(tagName);
    node.style = {};
    node.scrollIntoView = () => {};
    node.remove = () => {};
    return node;
  }

  const document = {
    documentElement: new FakeNode("html"),
    activeElement: null,
    querySelector: (selector) => elementFor(selector),
    querySelectorAll: () => [],
    createElement: (tagName) => newNode(tagName),
    createTextNode: (text) => {
      const node = new FakeNode("#text");
      node.textContent = text;
      return node;
    },
    addEventListener: () => {},
  };

  function buildNode(selector) {
    if (selector === "#composerInput") {
      const input = new FieldNode("textarea");
      input.focus = () => { document.activeElement = input; };
      return input;
    }
    if (selector === "#taskCreateTitle") return new FieldNode("input");
    if (selector === "#taskCreateChannel" || selector === "#settingsLocaleSelect") {
      return new FakeNode("select");
    }
    // 这三处替身照 index.html 的初始态建：`hidden` / `disabled` 是标记里的静态事实。
    if (selector === "#btnSend") {
      const button = newNode("button");
      button.disabled = true;
      return button;
    }
    if (selector === "#taskOverlay" || selector === "#composerMention") {
      const node = newNode("div");
      node.hidden = true;
      return node;
    }
    return newNode("div");
  }

  function elementFor(selector) {
    if (!nodes.has(selector)) nodes.set(selector, buildNode(selector));
    return nodes.get(selector);
  }

  const context = vm.createContext({
    document,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    URLSearchParams,
    window: { addEventListener: () => {} },
    console,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(readStudio("i18n.js"), context, { filename: "i18n.js" });
  const appPath = path.join(STUDIO_DIR, "app.js");
  const source = fs.readFileSync(appPath, "utf8");
  const startup = /\napplyStaticI18n\(document\);\nbindEvents\(\);\nloadWorkspace\(\);\s*$/;
  assert.match(source, startup, "app startup calls should remain at the end of app.js");
  vm.runInContext(
    source.replace(startup, "\nglobalThis.__composerTest = { state, bindEvents };\n"),
    context,
    { filename: appPath },
  );

  const api = context.__composerTest;
  api.bindEvents();
  // 频道 + 一名已加入的 Agent：@提及 的候选正是这份成员表。
  api.state.channels = [{ id: "ch-1", name: "design" }];
  api.state.activeChannelId = "ch-1";
  api.state.online = true;
  api.state.mode = "channel";
  api.state.channelMembers = {
    "ch-1": [{ id: "m-1", kind: "agent", display_name: "Ada", avatar_slot: 0 }],
  };

  return {
    api,
    document,
    elementFor,
    catalogs: context.AlibotI18n.catalogs,
    i18n: context.AlibotI18n,
  };
}

/** 主 composer 的 `.composer-bar` 片段：任务线程回复框也有一个 `.composer-bar`，必须按 `#composer` 定位。 */
function composerBarAt(scope) {
  const bar = /<div class="composer-bar">([\s\S]*?)<\/div>/.exec(scope);
  assert.ok(bar, "该 scope 里应有 .composer-bar");
  return bar[1];
}

function indexOf(source, marker) {
  const at = source.indexOf(marker);
  assert.notEqual(at, -1, `片段里缺少 ${marker}`);
  return at;
}

test("composer 工具行：按钮位与顺序照原型，且 #67 的附件位在本票不落", () => {
  const html = readStudio("index.html");
  const withoutComments = html.replace(/<!--[\s\S]*?-->/g, "");
  const composerBar = composerBarAt(withoutComments);

  // 顺序：@提及 → 作为任务发送 → 分隔 → 提示 → 发送（附件位在原型的首槽，本票留空给 #67）。
  const order = [
    'id="btnMention"',
    'id="btnAsTask"',
    'class="sep"',
    'id="composerHint"',
    'id="btnSend"',
  ].map((marker) => indexOf(composerBar, marker));
  assert.deepEqual(
    order,
    [...order].sort((a, b) => a - b),
    "工具行元素的先后顺序必须与原型一致（附件 → @提及 → 作为任务发送 → 分隔 → 提示 → 发送）",
  );

  // 附件位：本票**不**落一个没有行为的图标按钮（#67 接线后插在 `#btnMention` 之前）。
  assert.equal(
    withoutComments.includes('id="btnAttach"'),
    false,
    "附件按钮属于 #67；一个按下去什么都不发生的按钮不进这个工具行",
  );
  // 两个交付的按钮都挂词条键（文案不写死）且各只有一份。
  for (const marker of ["btn-mention", "btn-as-task"]) {
    assert.equal(
      withoutComments.split(`data-od-id="${marker}"`).length - 1,
      1,
      `${marker} 在主 composer 里恰好一份`,
    );
  }
});

test("composer 工具行：@提及 追加 `@`、聚焦输入框、激活发送键并复用既有补全", async () => {
  const harness = createComposerHarness();
  const input = harness.elementFor("#composerInput");
  const send = harness.elementFor("#btnSend");
  const panel = harness.elementFor("#composerMention");

  assert.equal(input.value, "");
  assert.equal(send.disabled, true, "空正文时发送键是 disabled");

  await harness.elementFor("#btnMention").dispatchEvent({ type: "click" });

  assert.equal(input.value, "@", "点击后输入框正文追加一个 `@`");
  assert.equal(harness.document.activeElement, input, "焦点落在输入框上");
  assert.equal(send.disabled, false, "发送键被激活");

  // 候选来自**既有**补全：条目与当前 target 的 Agent 成员逐条一致。
  assert.equal(panel.hidden, false, "候选清单打开");
  const rows = panel.querySelectorAll("[data-mention]");
  assert.deepEqual(rows.map((row) => row.dataset.mention), ["m-1"]);
  assert.equal(rows[0].textContent.includes("Ada"), true);
});

test("composer 工具行：作为任务发送 预填正文与当前频道，取消不动 composer", async () => {
  const harness = createComposerHarness();
  const input = harness.elementFor("#composerInput");
  const overlay = harness.elementFor("#taskOverlay");
  input.value = "把评审结论写成任务";
  assert.equal(overlay.hidden, true);

  await harness.elementFor("#btnAsTask").dispatchEvent({ type: "click" });

  assert.equal(overlay.hidden, false, "既有新建任务弹窗被打开");
  assert.equal(harness.elementFor("#taskCreateTitle").value, "把评审结论写成任务", "正文预填为标题");
  assert.equal(harness.elementFor("#taskCreateChannel").value, "ch-1", "当前频道预选");

  await harness.elementFor("#taskCreateCancel").dispatchEvent({ type: "click" });

  assert.equal(overlay.hidden, true, "取消关闭弹窗");
  assert.equal(input.value, "把评审结论写成任务", "取消路径不碰 composer 内容");
});

test("composer 工具行：任务线程的回复框没有工具行", () => {
  const html = readStudio("index.html");
  const thread = html.slice(html.indexOf('id="dockTask"'));
  const replyBar = composerBarAt(thread.slice(0, thread.indexOf("</aside>")));

  assert.equal(replyBar.includes("btn-mention"), false);
  assert.equal(replyBar.includes("btn-as-task"), false);
  assert.equal(replyBar.includes("icon-btn"), false, "回复框只有提示与发送键（原型 :775-780）");
  assert.equal(replyBar.includes('id="taskSend"'), true);
});

test("composer 工具行：两个新文案在两份词条里都取到值", () => {
  const harness = createComposerHarness();
  const catalogs = harness.catalogs;
  const keys = ["composer.mentionAction", "composer.asTaskAction"];

  for (const key of keys) {
    assert.ok(catalogs["zh-CN"][key], `${key} 在源语言里缺失`);
    assert.ok(catalogs.en[key], `${key} 在第二语言里缺失`);
    assert.notEqual(catalogs["zh-CN"][key], catalogs.en[key], `${key} 的两份取值不能是同一句话`);
  }

  // 按键取两套值比对（不写死措辞）：同一个绑定在两套语言下各自取到本语言的值。
  const root = new FakeNode("body");
  const button = new FakeNode("button");
  button.setAttribute(
    "data-i18n-attr",
    "title:composer.mentionAction,aria-label:composer.mentionAction",
  );
  root.appendChild(button);
  const taskButton = new FakeNode("button");
  taskButton.setAttribute("data-i18n-attr", "aria-label:composer.asTaskAction");
  root.appendChild(taskButton);

  for (const locale of ["zh-CN", "en"]) {
    harness.i18n.setLocale(locale);
    harness.i18n.applyStaticI18n(root);
    assert.equal(button.getAttribute("aria-label"), catalogs[locale]["composer.mentionAction"]);
    assert.equal(button.getAttribute("title"), catalogs[locale]["composer.mentionAction"]);
    assert.equal(taskButton.getAttribute("aria-label"), catalogs[locale]["composer.asTaskAction"]);
  }
});

/* ─── 8. 提醒（#68）：rail 入口 + 指示点 + 弹窗（列表 / 空态 / 新增 / 删除） ─────
   验收面：① 入口与指示点照原型 ② 圆点**只在有提醒时**出现 ③ 列表条目字段齐全
   ④ 删除后回到空态 ⑤ 新增文案在两份词条里都取到值。
   数据面归属见 ADR-0012：提醒是**库内对象**，这里的每一条断言都只经后端载荷（替身 fetch）。 */

/** 一条有归属频道的提醒（形状与 `GET /api/reminders` 的元素一致）。 */
const REMINDER_WITH_CHANNEL = {
  id: "rm_1",
  text: "检查令牌替换进度",
  time: "14:30",
  channel_id: "ch-1",
  channel_name: "设计系统重构",
  created_at: "2026-10-16T10:00:00.000Z",
};

/** 提醒弹窗那一段标记（从 overlay 起到下一个顶层注释为止）。 */
function remindersMarkup() {
  const html = readStudio("index.html").replace(/<!--[\s\S]*?-->/g, "");
  const start = html.indexOf('id="remindersOverlay"');
  assert.notEqual(start, -1, "index.html 里应有提醒弹窗");
  const rest = html.slice(start);
  const end = rest.indexOf('<div class="toast-wrap"');
  return rest.slice(0, end > 0 ? end : rest.length);
}

test("提醒：rail 入口与指示点照原型，圆点的初始标记就是「没有」", () => {
  const html = readStudio("index.html").replace(/<!--[\s\S]*?-->/g, "");
  const rail = html.slice(html.indexOf('class="rail-head"'), html.indexOf("</aside>"));

  assert.equal(rail.includes('id="btnReminders"'), true, "rail head 里应有提醒入口");
  const dot = /<span class="dot" id="reminderDot"([^>]*)>/.exec(rail);
  assert.ok(dot, "入口里应有一个 .dot 的指示点");
  // `hidden` 是标记里的静态事实：JS 还没跑完的那一帧也不能凭空空出一个点（原型 :1886 同一纪律）。
  assert.match(dot[1], /\bhidden\b/, "指示点的初始状态必须是「没有提醒」");
  // 入口与设置入口同形：文案不写死，按词条键取。
  assert.match(rail, /data-i18n-attr="aria-label:rail\.reminders,title:rail\.reminders"/);
});

test("提醒：弹窗是 wide，新增控件只有文本与时间（没有日期与重复字段）", () => {
  const markup = remindersMarkup();

  assert.ok(markup.includes('class="modal wide"'), "提醒弹窗照原型走 wide 那一档");
  assert.ok(markup.includes('id="reminderList"'), "列表容器由 app.js 按后端读数渲染");
  assert.equal((markup.match(/type="time"/g) || []).length, 1, "时间格恰好一个");
  assert.ok(markup.includes('id="reminderText"'), "文本格");
  // 本票不扩张：日期与重复字段是规格未授权的东西，标记里也不许先落一个。
  assert.equal(markup.includes('type="date"'), false, "没有日期字段");
  assert.equal(/id="reminder(Day|Date|Repeat)"/.test(markup), false, "没有日期 / 重复字段");
  // 时间格的默认取值照原型 `#rmTime`（`value="09:00"`）。
  assert.match(markup, /id="reminderTime"[\s\S]{0,160}value="09:00"/);
  // 可访问名与占位符都走词条（`type="time"` 的控件自己没有可读标签）。
  assert.match(markup, /data-i18n-attr="aria-label:reminder\.timeLabel"/);
});

test("提醒：没有提醒时是空态，有提醒时是列表，圆点跟着出现与消失", () => {
  const harness = createHarness();
  const list = harness.elementFor("#reminderList");
  const dot = harness.elementFor("#reminderDot");
  dot.hidden = false; // 先点亮，证明下面把它收回去的是这条规则本身

  harness.api.state.reminders = [];
  harness.api.renderRemindersModal();
  harness.api.renderReminderDot();

  assert.equal(dot.hidden, true, "没有提醒就没有指示点");
  const empty = list.querySelector(".empty");
  assert.ok(empty, "没有提醒时列表位置是空态");
  assert.equal(empty.querySelector("b").textContent, harness.catalogs["zh-CN"]["reminder.emptyTitle"]);
  assert.equal(empty.querySelector("p").textContent, harness.catalogs["zh-CN"]["reminder.emptyBody"]);
  assert.equal(list.querySelectorAll(".reminder-row").length, 0);

  harness.api.state.reminders = [{ ...REMINDER_WITH_CHANNEL }];
  harness.api.renderRemindersModal();
  harness.api.renderReminderDot();

  assert.equal(dot.hidden, false, "有提醒就有指示点");
  assert.equal(list.querySelectorAll(".reminder-row").length, 1);
  assert.equal(list.querySelector(".empty"), null, "有条目时不再是空态");
});

test("提醒：列表条目字段齐全（时间 / 文本 / 归属频道 / 删除）", async () => {
  const harness = createHarness({
    reminders: [{ ...REMINDER_WITH_CHANNEL }, { ...REMINDER_WITH_CHANNEL, id: "rm_2", text: "评审 raft 快照方案", time: "10:00", channel_id: null, channel_name: "" }],
  });
  await harness.api.loadReminders(); // 列表来自后端读数（`GET /api/reminders`），不是本地拼的

  const rows = harness.elementFor("#reminderList").querySelectorAll(".reminder-row");
  assert.equal(rows.length, 2, "列表逐条渲染，不合并");
  assert.deepEqual(rows.map((row) => row.dataset.reminder), ["rm_1", "rm_2"]);

  const first = rows[0];
  assert.equal(first.querySelector(".time").textContent, "14:30", "时间");
  assert.equal(first.querySelector(".grow").textContent, "检查令牌替换进度 · #设计系统重构", "文本 + 归属频道");
  const remove = first.querySelector("[data-delete-reminder]");
  assert.equal(remove.dataset.deleteReminder, "rm_1");
  assert.equal(remove.getAttribute("aria-label"), harness.catalogs["zh-CN"]["reminder.deleteAria"]);
  assert.equal(first.querySelectorAll("[data-delete-reminder]").length, 1, "一行一个删除控件");

  // 不归属任何频道的提醒：没有 `· #频道名` 那一段（可空列的正本事实）。
  assert.equal(rows[1].querySelector(".grow").textContent, "评审 raft 快照方案");
  assert.equal(rows[1].querySelector(".ch"), null);
});

test("提醒：新增写入后端（文本 + 时间 + 当前频道），并铺上响应里的最新列表", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  harness.api.state.channels = [{ id: "ch-1", name: "设计系统重构" }];
  harness.api.state.activeChannelId = "ch-1";
  harness.elementFor("#reminderText").value = "评审 raft 快照方案";
  harness.elementFor("#reminderTime").value = "10:00";

  await harness.elementFor("#reminderAddSubmit").dispatchEvent({ type: "click" });
  await flush();

  const posts = harness.calls.filter((call) => call.method === "POST");
  assert.deepEqual(
    posts.map((call) => ({ url: call.url, body: call.body })),
    [{ url: "/api/reminders", body: { text: "评审 raft 快照方案", time: "10:00", channel_id: "ch-1" } }],
  );
  assert.equal(harness.api.state.reminders.length, 1);
  assert.equal(harness.elementFor("#reminderDot").hidden, false, "新增后指示点出现");
  assert.equal(
    harness.elementFor("#reminderList").querySelectorAll(".reminder-row").length,
    1,
    "列表按后端返回的那一份渲染",
  );
  assert.equal(harness.elementFor("#reminderText").value, "", "提交后清空正文");
  assert.equal(harness.elementFor("#reminderTime").value, "10:00", "时间格保留当前取值");
});

test("提醒：删除走 DELETE 端点，删空后回到空态（圆点也跟着消失）", async () => {
  const harness = createHarness({ reminders: [{ ...REMINDER_WITH_CHANNEL }] });
  harness.api.bindEvents();
  harness.api.state.reminders = [{ ...REMINDER_WITH_CHANNEL }];
  harness.api.renderRemindersModal();
  harness.api.renderReminderDot();
  const list = harness.elementFor("#reminderList");
  const remove = list.querySelectorAll(".reminder-row")[0].querySelector("[data-delete-reminder]");

  await list.dispatchEvent({
    type: "click",
    target: { closest: (selector) => (selector === "[data-delete-reminder]" ? remove : null) },
  });
  await flush();

  assert.deepEqual(
    harness.calls.filter((call) => call.method === "DELETE").map((call) => call.url),
    ["/api/reminders/rm_1"],
  );
  assert.deepEqual(harness.api.state.reminders, []);
  assert.equal(harness.elementFor("#reminderDot").hidden, true, "删空后指示点消失");
  const empty = list.querySelector(".empty");
  assert.ok(empty, "删空后回到空态");
  assert.equal(empty.querySelector("b").textContent, harness.catalogs["zh-CN"]["reminder.emptyTitle"]);
});

test("提醒：写失败留在弹窗里（不静默、也不改本地列表）", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  harness.api.state.reminders = [];
  harness.elementFor("#reminderText").value = "会失败的一条";

  await harness.api.deleteReminder("rm_nope"); // 替身里不存在 ⇒ 404（与后端同形）
  await flush();

  assert.deepEqual(harness.api.state.reminders, [], "失败不改本地镜像");
  const box = harness.elementFor("#reminderFormError");
  assert.equal(box.hidden, false, "失败要留在弹窗里");
  assert.ok(box.textContent.includes("提醒不存在"), `错误正文应带上后端的话：${box.textContent}`);
});

test("提醒：打开弹窗按后端重读一次，切语言后文案跟着换", async () => {
  const harness = createHarness({ reminders: [{ ...REMINDER_WITH_CHANNEL }] });
  harness.api.bindEvents();

  harness.api.openRemindersModal();
  await flush();

  assert.equal(harness.elementFor("#remindersOverlay").hidden, false);
  assert.deepEqual(
    harness.calls.filter((call) => call.url === "/api/reminders" && call.method === "GET").length,
    1,
    "打开时按正本重读一次",
  );

  harness.api.applySettings({ locale: "en" });
  const remove = harness.elementFor("#reminderList").querySelector("[data-delete-reminder]");
  assert.equal(remove.getAttribute("aria-label"), harness.catalogs.en["reminder.deleteAria"]);
});

test("提醒：新增文案在两份词条里都取到值", () => {
  const harness = createHarness();
  const keys = [
    "rail.reminders",
    "reminder.modalTitle",
    "reminder.modalSub",
    "reminder.closeAria",
    "reminder.emptyTitle",
    "reminder.emptyBody",
    "reminder.newLabel",
    "reminder.textLabel",
    "reminder.timeLabel",
    "reminder.add",
    "reminder.deleteAria",
    "reminder.saveFailed",
    "reminder.deleteFailed",
    "reminder.loadFailed",
  ];

  for (const key of keys) {
    assert.ok(harness.catalogs["zh-CN"][key], `${key} 在源语言里缺失`);
    assert.ok(harness.catalogs.en[key], `${key} 在第二语言里缺失`);
    assert.notEqual(
      harness.catalogs["zh-CN"][key],
      harness.catalogs.en[key],
      `${key} 的两份取值不能是同一句话`,
    );
  }
});
