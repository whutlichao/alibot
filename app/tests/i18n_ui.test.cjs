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
        + " openSettingsModal, renderSettingsModal, setLocale, applyStaticI18n, bindEvents };\n",
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

  // 写失败不改本地镜像，也不改界面语言（界面不能声称一个没落库的取值）。
  assert.equal(harness.api.state.settings.locale, "zh-CN");
  assert.equal(harness.document.documentElement.lang, "zh-CN");
  assert.equal(select.value, "zh-CN", "选择器回到后端说的那一档");
  assert.equal(harness.elementFor("#settingsFormError").hidden, false, "失败要留在弹窗里，不静默");
});
