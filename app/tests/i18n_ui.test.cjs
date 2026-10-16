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
  // 后端事实的两份替身：频道成员表（`GET /api/channels/{id}/members`）与四态状态点
  // （`GET /api/member-statuses`）。名册的内容与状态点必须来自它们——前端不许自己拼一份。
  const channelMembers = config.channelMembers || {};
  const memberStatuses = config.memberStatuses || {};
  const sentMembers = {};

  async function fetchStub(url, init) {
    const method = (init && init.method) || "GET";
    const body = init && init.body ? JSON.parse(init.body) : null;
    calls.push({ url, method, body });
    const membersMatch = /^\/api\/channels\/([^/]+)\/members$/.exec(url);
    if (membersMatch && method === "GET") {
      if (config.membersError) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ error: { code: "internal_error", message: "后端不可达" } }),
        };
      }
      const channelId = decodeURIComponent(membersMatch[1]);
      const members = channelMembers[channelId] || [];
      sentMembers[channelId] = members;
      return { ok: true, status: 200, json: async () => ({ members }) };
    }
    if (url === "/api/member-statuses") {
      return { ok: true, status: 200, json: async () => ({ statuses: memberStatuses }) };
    }
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
        + " openRosterModal, closeRosterModal, renderRoster, renderDock, refreshStatuses,"
        + " refreshChannelMembers };\n",
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
    // 那次 `GET /api/channels/{id}/members` **实际返回**的成员表（名册的内容要逐条对得上它）。
    membersSent: (channelId) => sentMembers[channelId] || [],
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

/* ─── 7. 成员名册（频道成员的**只读**全名单） ──────────────────────────────
   名册的判据只有四条：入口能开、内容 = 后端成员表逐条（含 owner）、唯一动作是关闭、
   文案在两份词条里都取到值。窄屏 / 真实页面的那条入口由浏览器实测验，这里用同一套 DOM 替身。 */

/** 某词条在**指定语言**里的取值（`{name}` 这类参数位按 `t()` 的规则拼进去）。 */
function catalogText(harness, locale, key, params) {
  const value = harness.catalogs[locale][key];
  return String(value).replace(/\{(\w+)\}/g, (whole, name) =>
    params && params[name] !== undefined ? String(params[name]) : whole,
  );
}

/** 一台「1 个频道 + owner + 1 个已加入 Agent + 1 个未加入 Agent」的工作台：
    成员表与状态点都由 fetch 替身给出（名册的两样内容都必须来自它们）。 */
function rosterHarness() {
  const harness = createHarness({
    channelMembers: {
      ch_1: [
        { id: "own_1", kind: "owner", display_name: "林舟", avatar_slot: 4 },
        { id: "ag_1", kind: "agent", display_name: "Susan", avatar_slot: 0 },
      ],
    },
    memberStatuses: { ag_1: "working", ag_2: "online" },
  });
  const state = harness.api.state;
  state.channels = [{ id: "ch_1", name: "设计系统重构", description: "", unread_count: 0 }];
  state.activeChannelId = "ch_1";
  state.member = { id: "own_1", kind: "owner", display_name: "林舟", avatar_slot: 4 };
  state.members = [
    { id: "own_1", kind: "owner", display_name: "林舟", avatar_slot: 4 },
    { id: "ag_1", kind: "agent", display_name: "Susan", avatar_slot: 0 },
    { id: "ag_2", kind: "agent", display_name: "Atlas", avatar_slot: 1 },
  ];
  state.agents = [
    { id: "ag_1", kind: "agent", display_name: "Susan", avatar_slot: 0, role: "秘书", model_id: "ollama:qwen2.5" },
    { id: "ag_2", kind: "agent", display_name: "Atlas", avatar_slot: 1, role: "架构师", model_id: "ollama:qwen2.5" },
  ];
  harness.api.bindEvents();
  return harness;
}

function rosterRowNodes(harness) {
  return harness.elementFor("#rosterBody").querySelectorAll(".roster-row");
}

test("the channel-header entry opens a roster listing exactly the backend member table", async () => {
  const harness = rosterHarness();
  const overlay = harness.elementFor("#rosterOverlay");
  // 启动后名册没有自己开过（弹窗的 `hidden` 初值来自 `index.html`，那条由浏览器实测覆盖）。
  assert.equal(harness.api.state.rosterChannelId, null);

  await harness.elementFor("#btnChannelMembers").dispatchEvent({ type: "click" });
  await flush();

  assert.equal(overlay.hidden, false, "点频道头部入口要打开名册");
  assert.equal(harness.api.state.rosterChannelId, "ch_1", "名册锁定的就是打开它的那个频道");
  // 状态点也走后端那条面（`GET /api/member-statuses` 的回读），不是前端猜的。
  await harness.api.refreshStatuses();
  harness.api.renderRoster();

  const table = harness.membersSent("ch_1");
  assert.ok(table.length > 0, "替身必须真的被读过一次成员表");
  const rows = rosterRowNodes(harness);
  assert.deepEqual(
    rows.map((row) => [row.dataset.member, row.dataset.kind]),
    table.map((member) => [member.id, member.kind]),
    "名册必须与该频道后端成员表逐条一致（含 owner，顺序也照后端给的顺序）",
  );
  // 姓名与角色也逐条对得上（角色是 Agent 配置的事实；owner 没有角色，走回退说法）。
  assert.deepEqual(
    rows.map((row) => row.querySelector(".roster-name").textContent),
    table.map((member) => member.display_name),
  );
  assert.equal(
    rows[0].querySelector(".roster-me").textContent,
    catalogText(harness, "zh-CN", "identity.me"),
    "owner 那行右侧是「我」（`GET /api/member-statuses` 不覆盖 owner，不替它造状态点）",
  );
  assert.deepEqual(rows[0].querySelectorAll(".presence"), [], "owner 那行没有状态点");
  assert.equal(
    rows[1].querySelector(".presence").dataset.status,
    harness.api.state.statuses["ag_1"],
    "Agent 那行的状态点取值必须就是 `GET /api/member-statuses` 给的四态词",
  );
  // 副标题的条数 = 成员表的条数（按词条取，不写死措辞）。
  assert.equal(
    harness.elementFor("#rosterSub").textContent,
    catalogText(harness, "zh-CN", "roster.sub", { count: table.length }),
  );
});

test("the roster's only action is closing: it holds no join, remove or edit control", async () => {
  const harness = rosterHarness();
  const overlay = harness.elementFor("#rosterOverlay");
  await harness.elementFor("#btnChannelMembers").dispatchEvent({ type: "click" });
  await flush();

  // 反：成员面板那几类动作控件一个都不许出现在名册里（加入 / 移除 / 编辑都不属于它）。
  const body = harness.elementFor("#rosterBody");
  for (const selector of ["[data-join]", ".member-opt", "input", "select", "textarea"]) {
    assert.deepEqual(body.querySelectorAll(selector), [], `名册里出现了不该有的控件：${selector}`);
  }
  assert.deepEqual(
    body.descendants().filter((node) => node.tagName === "button"),
    [],
    "名册正文里不该有任何按钮（唯一动作在弹窗外壳上）",
  );

  // 正：能关的就两条路（头部图标 + 页脚按钮），点哪条都真的把名册关掉。
  await harness.elementFor("#rosterDone").dispatchEvent({ type: "click" });
  assert.equal(overlay.hidden, true, "页脚按钮关闭名册");
  assert.equal(harness.api.state.rosterChannelId, null, "关掉后不再锁定任何频道");

  await harness.elementFor("#btnChannelMembers").dispatchEvent({ type: "click" });
  await flush();
  await harness.elementFor("#rosterClose").dispatchEvent({ type: "click" });
  assert.equal(overlay.hidden, true, "头部图标也关闭名册");
});

test("the roster is not the member panel: the un-joined agent never shows up in it", async () => {
  const harness = rosterHarness();
  await harness.api.refreshStatuses();

  // 先打开一次名册：它按需读回该频道成员表，面板与名册因此读的是同一份后端事实。
  await harness.elementFor("#btnChannelMembers").dispatchEvent({ type: "click" });
  await flush();

  // 面板的「未加入」段里有 Atlas（带「加入」控件）——那是**成员面板**的面。
  harness.api.renderDock();
  assert.deepEqual(
    harness.elementFor("#dockMembers").querySelectorAll("[data-join]").map((button) => button.dataset.join),
    ["ag_2"],
    "面板的「未加入」段必须有那个未加入的 Agent（名册与它不混）",
  );

  const listed = rosterRowNodes(harness).map((row) => row.dataset.member);
  assert.deepEqual(listed, ["own_1", "ag_1"]);
  assert.equal(listed.includes("ag_2"), false, "名册只回答「这个频道里有谁」，不列未加入者");
  assert.deepEqual(harness.elementFor("#rosterBody").querySelectorAll("[data-join]"), []);
});

test("the roster copy is taken from the string catalogs in both languages", async () => {
  const harness = rosterHarness();
  await harness.api.refreshStatuses();
  await harness.elementFor("#btnChannelMembers").dispatchEvent({ type: "click" });
  await flush();

  // 本票新增的文案（票面 ④）：按键取**两套**取值比对——两份都在、都不是空串、且第二语言
  // 不是源语言逐字复制。键名从词条表按前缀取（不在这里另抄一份清单，「键集都有值」已由上面
  // 的键集比对盯着，这一条盯的是「名册的每条文案真的翻过」）。
  const keys = Object.keys(harness.catalogs["zh-CN"]).filter((key) => key.startsWith("roster."));
  assert.ok(keys.length >= 8, `名册的词条看起来太少：${keys.length}`);
  for (const key of keys) {
    const zh = harness.catalogs["zh-CN"][key];
    const en = harness.catalogs.en[key];
    assert.equal(typeof en, "string", `${key} 在第二语言里必须有取值`);
    assert.ok(zh.length > 0 && en.length > 0, `${key} 在两份取值里都不能是空串`);
    assert.notEqual(en, zh, `${key} 的第二语言取值不能与源语言逐字相同`);
  }

  const name = harness.api.state.channels[0].name;
  assert.equal(
    harness.elementFor("#rosterTitle").textContent,
    catalogText(harness, "zh-CN", "roster.title", { name }),
  );

  // 切到 English：标题 / 副标题 / 行内角色与状态说法都必须按第二语言重铺（同一批键的另一套取值）。
  harness.api.applySettings({ locale: "en", message_density: "comfortable" });
  assert.equal(
    harness.elementFor("#rosterTitle").textContent,
    catalogText(harness, "en", "roster.title", { name }),
  );
  assert.equal(
    harness.elementFor("#rosterSub").textContent,
    catalogText(harness, "en", "roster.sub", { count: 2 }),
  );
  assert.equal(
    harness.elementFor("#rosterBody").querySelectorAll(".presence")[0].attributes["title"],
    catalogText(harness, "en", "status.working"),
    "状态点的可读说法也跟着语言走",
  );

  harness.api.applySettings({ locale: "zh-CN", message_density: "comfortable" });
  assert.equal(
    harness.elementFor("#rosterTitle").textContent,
    catalogText(harness, "zh-CN", "roster.title", { name }),
  );
});

test("the roster waits for an in-flight member read instead of staying on the loading line", async () => {
  const harness = rosterHarness();
  // 名册打开的那一刻正好有一次该频道的成员表读取在飞（切频道 / composer 获得焦点都会发起）：
  // 名册必须等这一趟落地后再渲染，而不是渲染成旧缓存（或永远停在「正在读取…」）。
  const pending = harness.api.refreshChannelMembers("ch_1");
  await harness.elementFor("#btnChannelMembers").dispatchEvent({ type: "click" });
  assert.equal(
    harness.elementFor("#rosterBody").textContent,
    catalogText(harness, "zh-CN", "roster.loading"),
    "读取还没落地时是「正在读取」，不是空名单",
  );

  await pending;
  await flush();

  assert.deepEqual(
    rosterRowNodes(harness).map((row) => row.dataset.member),
    ["own_1", "ag_1"],
    "在飞的那次读取落地后，名册要按它渲染出成员",
  );
});

test("a failed member read is reported as unavailable, never as an empty roster", async () => {
  const harness = createHarness({ membersError: true });
  const state = harness.api.state;
  state.channels = [{ id: "ch_1", name: "设计系统重构" }];
  state.activeChannelId = "ch_1";
  state.members = [{ id: "own_1", kind: "owner", display_name: "林舟", avatar_slot: 4 }];
  state.agents = [];
  harness.api.bindEvents();

  await harness.elementFor("#btnChannelMembers").dispatchEvent({ type: "click" });
  await flush();

  // 「读不到」不是「一个都没有」：后端那次 GET 失败时，名册说的是不可用，不是空名单，
  // 副标题也不写「0 位成员」（那会把失败说成一个成立的读数）。
  assert.equal(
    harness.elementFor("#rosterBody").textContent,
    catalogText(harness, "zh-CN", "roster.unavailable"),
  );
  assert.notEqual(
    harness.elementFor("#rosterBody").textContent,
    catalogText(harness, "zh-CN", "roster.empty"),
  );
  assert.equal(harness.elementFor("#rosterSub").textContent, "");
});

