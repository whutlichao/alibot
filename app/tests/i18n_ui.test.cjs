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
 * 5. 设置弹窗的三个档位（语言 / 消息密度 / 主题）读写：写走后端、界面立刻生效、选中态跟着走。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
// 视觉契约的正本（只读参照）：token **名**的声明处。C16 的「不加新色位」就是拿实际消费的
// token 名去这三个文件里对——名字对不上就是新色位。
const TOKEN_FILES = [
  path.join(__dirname, "../../alibot-design-system/colors_and_type.css"),
  path.join(__dirname, "../../alibot-design-system/tokens.css"),
  path.join(STUDIO_DIR, "product-tokens.css"),
];
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
    // 真实 DOM 里被摘掉的节点不再有父节点（`closest` 之类因此会在这条链上停下）。
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

/** 与 `index.html` 同形的三个主题档位卡片（浅色 / 深色 / 跟随系统，含各自的词条声明）。 */
function themeCard(value, titleKey, hintKey) {
  const card = new FakeNode("button");
  card.dataset.theme = value;
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
  let settings = config.settings || {
    locale: "zh-CN",
    message_density: "comfortable",
    theme: "system",
  };
  // 提醒的替身存储：与后端同形——写路径返回**最新全表**，`GET` 也返回同一份。
  const reminders = (config.reminders || []).map((item) => ({ ...item }));
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
    if (url === "/api/reminders" && method === "GET") {
      return { ok: true, status: 200, json: async () => ({ reminders: reminders.slice() }) };
    }
    if (url === "/api/reminders" && method === "POST") {
      // 归属频道的可读名由后端随行带出（替身照这个形状给）。
      // `fired_at` / `state` 是读路径**恒有**的两个键（C20）：刚建的提醒一定是未到点。
      const reminder = {
        id: "rm_" + (reminders.length + 1),
        text: String(body.text).trim(),
        time: body.time,
        channel_id: body.channel_id === undefined ? null : body.channel_id,
        channel_name: body.channel_id ? "设计系统重构" : "",
        created_at: "2026-10-16T10:00:00.000Z",
        fired_at: null,
        state: "pending",
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
    if (selector === "#settingsTheme") {
      const group = new FakeNode("div");
      group.appendChild(themeCard("light", "settings.themeLight", "settings.themeLightHint"));
      group.appendChild(themeCard("dark", "settings.themeDark", "settings.themeDarkHint"));
      group.appendChild(themeCard("system", "settings.themeSystem", "settings.themeSystemHint"));
      return group;
    }
    // 提醒：三处替身照 `index.html` 的初始态建（弹窗关着、圆点 `hidden`、钟点格默认 09:00、
    // 日期格留空——「今天」由 `openRemindersModal` 在打开时填，C15 判据 2）。
    if (selector === "#remindersOverlay" || selector === "#reminderDot") {
      const node = new FakeNode(selector === "#reminderDot" ? "span" : "div");
      node.hidden = true;
      return node;
    }
    if (
      selector === "#reminderText" ||
      selector === "#reminderTime" ||
      selector === "#reminderDate"
    ) {
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
        + " closeRemindersModal, submitReminder, deleteReminder,"
        + " openRosterModal, closeRosterModal, renderRoster, renderDock, refreshStatuses,"
        + " renderChannels, renderDms, refreshChannelMembers };\n",
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

test("applySettings drives lang, the message-density tier and the theme tier together", () => {
  const harness = createHarness();

  harness.api.applySettings({ locale: "en", message_density: "compact", theme: "dark" });

  assert.equal(harness.document.documentElement.lang, "en");
  assert.equal(harness.document.documentElement.dataset.density, "compact");
  assert.equal(harness.document.documentElement.dataset.theme, "dark");
  assert.equal(harness.api.state.settings.locale, "en");
  assert.equal(harness.api.state.settings.message_density, "compact");
  assert.equal(harness.api.state.settings.theme, "dark");
});

test("unknown locale, density or theme values fall back to the source tier", () => {
  const harness = createHarness();

  harness.api.applySettings({ locale: "fr", message_density: "spacious", theme: "sepia" });

  assert.equal(harness.document.documentElement.lang, "zh-CN");
  assert.equal(harness.document.documentElement.dataset.density, "comfortable");
  // 主题的缺省档是「跟随系统」，不是「浅色」：坏值一律回落到它。
  assert.equal(harness.document.documentElement.dataset.theme, "system");
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

/* ─── 6. 设置弹窗三个档位的读写 ─────────────────────────────────────────── */
test("the settings modal shows all three tiers", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  harness.api.renderSettingsModal();

  const select = harness.elementFor("#settingsLocaleSelect");
  const density = harness.elementFor("#settingsDensity");
  const theme = harness.elementFor("#settingsTheme");
  const cards = density.querySelectorAll("[data-density]");
  const themeCards = theme.querySelectorAll("[data-theme]");

  // 语言：两档，取值就是后端档位（`zh-CN` / `en`）。
  assert.deepEqual(
    select.children.map((option) => option.value),
    ["zh-CN", "en"],
  );
  // 消息密度：两档（`comfortable` / `compact`）。
  assert.deepEqual(
    cards.map((card) => card.dataset.density),
    ["comfortable", "compact"],
  );
  // 主题（#71 的第三档）：三档（`light` / `dark` / `system`），取值与后端 `THEMES` 同名。
  assert.deepEqual(
    themeCards.map((card) => card.dataset.theme),
    ["light", "dark", "system"],
  );
  assert.deepEqual(
    Object.keys(harness.api.state.settings).sort(),
    ["locale", "message_density", "theme"],
    "设置面就是语言 / 消息密度 / 主题这三档",
  );
  assert.equal(cards[0].classList.contains("is-on"), true, "缺省密度档要标出来");
  assert.equal(
    themeCards[2].classList.contains("is-on"),
    true,
    "缺省主题档是「跟随系统」，要标出来",
  );
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

test("choosing the dark theme writes the backend and applies the tier", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  const theme = harness.elementFor("#settingsTheme");
  const dark = theme.querySelectorAll("[data-theme]")[1];

  await theme.dispatchEvent({
    type: "click",
    target: { closest: (selector) => (selector === "[data-theme]" ? dark : null) },
  });
  await flush();

  const patch = harness.calls.filter((call) => call.method === "PATCH");
  assert.deepEqual(patch.map((call) => call.body), [{ theme: "dark" }]);
  assert.equal(harness.api.state.settings.theme, "dark");
  // 生效标记写在根元素上：显式档位由 `:root[data-theme='dark']` 命中。
  assert.equal(harness.document.documentElement.dataset.theme, "dark");
  assert.equal(dark.classList.contains("is-on"), true);
  assert.equal(
    theme.querySelectorAll("[data-theme]")[2].classList.contains("is-on"),
    false,
    "同一时刻只有一档是选中态（缺省的「跟随系统」要让位）",
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

/** 只装载 composer 工具行所需的那几个元素：其余选择器给一个惰性替身（`bindEvents` 会挂到它们上）。

    `options.fetch` 是 HTTP 替身（默认一律返回空对象）：附件那条路径要断言「发出去的请求长什么样」，
    因此这里把每个请求记进 `requests` 一并带回来。 */
function createComposerHarness(options) {
  const config = options || {};
  const nodes = new Map();
  const requests = [];

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
    fetch: async (url, init) => {
      requests.push({ url, init });
      if (config.fetch) return config.fetch(url, init);
      return { ok: true, json: async () => ({}) };
    },
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
      "\nglobalThis.__composerTest = { state, bindEvents, attachFiles, renderComposerAttachments, removeComposerAttachment, renderMessages, messageNode, uploadAttachment, deliver, syncSendButton };\n",
    ),
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
    requests,
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

test("composer 工具行：按钮位与顺序照原型，附件落在 #btnMention 之前的首槽", () => {
  const html = readStudio("index.html");
  const withoutComments = html.replace(/<!--[\s\S]*?-->/g, "");
  const composerBar = composerBarAt(withoutComments);

  // 顺序照原型：附件 → @提及 → 作为任务发送 → 分隔 → 提示 → 发送。
  const order = [
    'id="btnAttach"',
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
  // 三个图标按钮都挂词条键（文案不写死）且各只有一份。
  for (const marker of ["btn-attach", "btn-mention", "btn-as-task"]) {
    assert.equal(
      withoutComments.split(`data-od-id="${marker}"`).length - 1,
      1,
      `${marker} 在主 composer 里恰好一份`,
    );
  }
  // 附件按钮接的是**隐藏的原生文件选择器**（不是一套自造的挑选器 UI），且允许多选。
  assert.match(
    withoutComments,
    /class="sr-only"[^>]*type="file"[^>]*id="attachInput"[^>]*multiple/,
    "附件按钮触发的是隐藏的原生 <input type=\"file\" multiple>",
  );
  assert.match(
    withoutComments,
    /id="attachInput"[^>]*data-i18n-attr="aria-label:composer\.attachAction"/,
    "文件选择器也要有可本地化的可访问名",
  );
  // 待发送草稿的容器在 composer 里（消息里的附件元素由 app.js 渲染进消息流）。
  assert.equal(withoutComments.includes('id="composerAttachments"'), true);
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

/* ─── 7b. 附件（#67 / ADR-0014）：两步上传 —— 先拿标识，随消息提交时绑定 ─────
   验收面：① composer 里的草稿元素来自**后端返回的元数据**；② 发送时标识随消息一起走
   ③ 消息里的附件是**可点开的下载元素**（`href` 指向内容端点、`download` 是原文件名）
   ④ 上传被拒时给出后端给的可读原因、不留下草稿 ⑤ 新增文案在两份词条里都取到值。

   `fetch` 替身只回后端契约里那几样东西（附件元数据 / 消息 / 工作台快照），前端不许自己拼。 */

/** 等两轮宏任务：上传是 `await fetch(...)` 链，`dispatchEvent` 只等同步那一段。 */
async function settle() {
  await flush();
  await flush();
}

/** 附件路径的 HTTP 替身：上传与「发送带附件的消息」各回一份后端契约形状的载荷。 */
function attachmentFetch(options) {
  const config = options || {};
  return async (url, init) => {
    const method = (init && init.method) || "GET";
    if (url.startsWith("/api/attachments?") && method === "POST") {
      if (config.rejectUpload) {
        return {
          ok: false,
          status: 400,
          json: async () => ({
            error: {
              code: "validation_failed",
              message: "附件校验未通过，未保存任何数据",
              fields: { file: "附件超过单文件上限（10485760 字节）" },
            },
          }),
        };
      }
      return {
        ok: true,
        status: 201,
        json: async () => ({
          attachment: {
            id: "att_1",
            name: "报告.pdf",
            size: 204800,
            mime_type: "application/pdf",
            created_at: "2026-10-16T09:00:00Z",
            message_id: null,
            url: "/api/attachments/att_1",
          },
        }),
      };
    }
    if (/\/messages$/.test(url) && method === "POST") {
      return {
        ok: true,
        status: 201,
        json: async () => ({
          message: {
            id: "msg_1",
            target_id: "ch-1",
            seq: 1,
            kind: "human",
            body: "带附件的一条",
            created_at: "2026-10-16T09:00:01Z",
            mention_snapshot: [],
            task_ref: null,
            attachments: [],
            author: { id: "owner", kind: "owner", display_name: "Owner", avatar_slot: 4 },
          },
        }),
      };
    }
    if (url === "/api/workspace") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          member: { id: "owner", kind: "owner", display_name: "Owner", avatar_slot: 4 },
          channels: [],
          dms: [],
          members: [],
          agents: [],
          unread_total: 0,
          last_seq: 0,
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        }),
      };
    }
    if (url === "/api/channels") return { ok: true, status: 200, json: async () => ({ channels: [] }) };
    return { ok: true, status: 200, json: async () => ({}) };
  };
}

/** 选一个文件：把 `files` 放进那个原生选择器并让它 `change` 一次（真实路径就是这样触发的）。 */
async function chooseFiles(harness, files) {
  const input = harness.elementFor("#attachInput");
  input.files = files;
  await input.dispatchEvent({ type: "change", target: input });
  await settle();
}

test("附件：上传成功后 composer 里出现草稿元素，且请求就是「原始字节 + 文件名」", async () => {
  const harness = createComposerHarness({ fetch: attachmentFetch() });
  const box = harness.elementFor("#composerAttachments");
  harness.api.renderComposerAttachments();
  assert.equal(box.hidden, true, "没有附件时这一块不占版面");

  const file = { name: "报告.pdf", type: "application/pdf", size: 204800 };
  await chooseFiles(harness, [file]);

  assert.equal(box.hidden, false);
  const chips = box.querySelectorAll("[data-attachment]");
  assert.deepEqual(chips.map((chip) => chip.dataset.attachment), ["att_1"]);
  assert.equal(chips[0].textContent.includes("报告.pdf"), true);
  assert.equal(chips[0].textContent.includes("200.0 KB"), true, "体积按元数据呈现");

  const upload = harness.requests.find((request) => request.url.startsWith("/api/attachments"));
  assert.equal(upload.url, "/api/attachments?name=" + encodeURIComponent("报告.pdf"));
  assert.equal(upload.init.method, "POST");
  assert.equal(upload.init.headers["Content-Type"], "application/pdf");
  assert.equal(upload.init.body, file, "请求体是文件本身的字节，不是 JSON 包装");
});

test("附件：发送时把草稿标识随消息带上，然后从 composer 里收走", async () => {
  const harness = createComposerHarness({ fetch: attachmentFetch() });
  const input = harness.elementFor("#composerInput");
  await chooseFiles(harness, [{ name: "报告.pdf", type: "application/pdf", size: 204800 }]);

  input.value = "带附件的一条";
  await harness.elementFor("#btnSend").dispatchEvent({ type: "click" });
  await settle();

  const sent = harness.requests.find(
    (request) => request.init && request.init.method === "POST" && request.url === "/api/channels/ch-1/messages",
  );
  assert.ok(sent, "消息经既有发送路径发出");
  assert.deepEqual(JSON.parse(sent.init.body).attachment_ids, ["att_1"], "标识随消息提交时绑定");
  assert.equal(harness.elementFor("#composerAttachments").hidden, true, "草稿已经交给那条消息");
});

test("附件：消息里的附件渲染出可点开的下载元素（原文件名 + 内容端点）", () => {
  const harness = createComposerHarness();
  harness.api.state.messages = [
    {
      id: "msg_1",
      target_id: "ch-1",
      seq: 1,
      kind: "human",
      body: "带附件的一条",
      created_at: "2026-10-16T09:00:00Z",
      mention_snapshot: [],
      task_ref: null,
      attachments: [
        {
          id: "att_1",
          name: "报告.pdf",
          size: 204800,
          mime_type: "application/pdf",
          created_at: "2026-10-16T09:00:00Z",
          message_id: "msg_1",
          url: "/api/attachments/att_1",
        },
      ],
      author: { id: "owner", kind: "owner", display_name: "Owner", avatar_slot: 4 },
    },
  ];
  harness.api.renderMessages();

  const nodes = harness.elementFor("#messageList").querySelectorAll("[data-attachment]");
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].tagName, "a", "附件元素是可点开的链接");
  assert.equal(nodes[0].getAttribute("href"), "/api/attachments/att_1", "指向内容端点");
  assert.equal(nodes[0].getAttribute("download"), "报告.pdf", "下载名是库里的原文件名");
  assert.equal(nodes[0].classList.contains("is-link"), true);
  assert.ok(nodes[0].getAttribute("aria-label").includes("报告.pdf"));
});

test("附件：没有被绑定的草稿不进消息流（正反两向）", () => {
  const harness = createComposerHarness();
  harness.api.state.messages = [
    {
      id: "msg_1",
      target_id: "ch-1",
      seq: 1,
      kind: "human",
      body: "没有附件的一条",
      created_at: "2026-10-16T09:00:00Z",
      mention_snapshot: [],
      task_ref: null,
      attachments: [],
      author: { id: "owner", kind: "owner", display_name: "Owner", avatar_slot: 4 },
    },
  ];
  harness.api.renderMessages();
  assert.equal(
    harness.elementFor("#messageList").querySelectorAll("[data-attachment]").length,
    0,
    "读取面没给附件时什么都不渲染",
  );
});

test("附件：还没绑定的草稿在消息里是不可点的元素（不渲染必然 404 的链接）", () => {
  const harness = createComposerHarness();
  // 乐观发送中的消息带的是**草稿**（`message_id` 为空）：内容端点对它回 404，
  // 因此它只能是一块不可点的胶囊，而不是一个点下去必然失败的 `<a download>`。
  harness.api.state.messages = [
    {
      id: "msg_1",
      target_id: "ch-1",
      seq: 1,
      kind: "human",
      body: "还在发",
      created_at: "2026-10-16T09:00:00Z",
      mention_snapshot: [],
      task_ref: null,
      attachments: [
        {
          id: "att_draft",
          name: "草稿.txt",
          size: 12,
          mime_type: "text/plain",
          created_at: "2026-10-16T09:00:00Z",
          message_id: null,
          url: "/api/attachments/att_draft",
        },
      ],
      author: { id: "owner", kind: "owner", display_name: "Owner", avatar_slot: 4 },
    },
  ];
  harness.api.renderMessages();

  const nodes = harness.elementFor("#messageList").querySelectorAll("[data-attachment]");
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].tagName, "span", "未绑定 ⇒ 不是链接");
  assert.equal(nodes[0].getAttribute("href"), null);
  assert.equal(nodes[0].classList.contains("is-link"), false);
  assert.equal(nodes[0].textContent.includes("草稿.txt"), true, "名字与体积照旧可读");
});

test("附件：上传还在飞时 Enter 不发消息（不会静默漏掉刚选的文件）", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const route = attachmentFetch();
  const harness = createComposerHarness({
    fetch: (url, init) => (url.startsWith("/api/attachments?") ? gate : route(url, init)),
  });

  const input = harness.elementFor("#attachInput");
  input.files = [{ name: "报告.pdf", type: "application/pdf", size: 2048 }];
  await input.dispatchEvent({ type: "change", target: input });
  // 不 await `settle()`：上传正卡在 fetch 上，此刻 composer 里**还没有**这个附件。
  harness.elementFor("#composerInput").value = "这条不该发出去";
  assert.equal(harness.elementFor("#btnSend").disabled, true, "上传期间发送键禁用");
  await harness.elementFor("#composerInput").dispatchEvent({
    type: "keydown",
    key: "Enter",
    isComposing: false,
    preventDefault: () => {},
  });
  await flush();

  assert.equal(
    harness.requests.some((request) => /\/messages$/.test(request.url)),
    false,
    "上传未落地时 Enter 不发消息",
  );
  assert.ok(
    harness.elementFor("#toastWrap").textContent.includes("还有 1 个附件在上传"),
    "拦住的时候要说明为什么（不是静默什么都不发生）",
  );

  release({
    ok: true,
    status: 201,
    json: async () => ({
      attachment: {
        id: "att_1",
        name: "报告.pdf",
        size: 2048,
        mime_type: "application/pdf",
        created_at: "2026-10-16T09:00:00Z",
        message_id: null,
        url: "/api/attachments/att_1",
      },
    }),
  });
  await settle();
  assert.equal(
    harness.elementFor("#composerAttachments").querySelectorAll("[data-attachment]").length,
    1,
    "上传落地后草稿照常出现在 composer 里",
  );
  assert.equal(harness.elementFor("#btnSend").disabled, false, "落地后发送键恢复");
});

test("附件：上传被拒时给出后端原因，且 composer 里不留下草稿", async () => {
  const harness = createComposerHarness({ fetch: attachmentFetch({ rejectUpload: true }) });
  await chooseFiles(harness, [{ name: "巨大.bin", type: "application/pdf", size: 11534336 }]);

  assert.equal(harness.elementFor("#composerAttachments").hidden, true, "被拒的上传不留草稿");
  const toast = harness.elementFor("#toastWrap").textContent;
  assert.ok(toast.includes("附件上传失败"), "拒绝对话在界面里可读");
  assert.ok(toast.includes("附件超过单文件上限"), "原因是**后端**给的那一句，前端不另编");
});

test("附件：新增文案在两份词条里都取到值", () => {
  const harness = createComposerHarness();
  const catalogs = harness.catalogs;
  const keys = [
    "composer.attachAction",
    "composer.attachRemove",
    "composer.attaching",
    "attach.fallbackName",
    "msg.attachmentAria",
    "toast.attachFailed",
    "toast.attachPending",
  ];
  for (const key of keys) {
    assert.ok(catalogs["zh-CN"][key], `${key} 在源语言里缺失`);
    assert.ok(catalogs.en[key], `${key} 在第二语言里缺失`);
    assert.notEqual(catalogs["zh-CN"][key], catalogs.en[key], `${key} 的两份取值不能是同一句话`);
  }

  // 按键取两套值比对（不写死措辞）：同一个绑定在两套语言下各自取到本语言的值。
  const root = new FakeNode("body");
  const attachButton = new FakeNode("button");
  attachButton.setAttribute("data-i18n-attr", "title:composer.attachAction,aria-label:composer.attachAction");
  root.appendChild(attachButton);
  for (const locale of ["zh-CN", "en"]) {
    harness.i18n.setLocale(locale);
    harness.i18n.applyStaticI18n(root);
    assert.equal(attachButton.getAttribute("aria-label"), catalogs[locale]["composer.attachAction"]);
    assert.equal(attachButton.getAttribute("title"), catalogs[locale]["composer.attachAction"]);
  }

  // 草稿元素与「正在上传」占位也按键取值（参数位按当前语言拼）。
  const harnessEn = createComposerHarness();
  harnessEn.i18n.setLocale("en");
  const node = harnessEn.api.messageNode({
    id: "msg_1",
    target_id: "ch-1",
    seq: 1,
    kind: "human",
    body: "x",
    created_at: "2026-10-16T09:00:00Z",
    mention_snapshot: [],
    task_ref: null,
    attachments: [{ id: "att_1", name: "report.pdf", size: 10, message_id: "msg_1", url: "/api/attachments/att_1" }],
    author: { id: "owner", kind: "owner", display_name: "Owner", avatar_slot: 4 },
  });
  const englishAria = node.querySelector("[data-attachment]").getAttribute("aria-label");
  assert.equal(
    englishAria,
    catalogText(harnessEn, "en", "msg.attachmentAria", { name: "report.pdf", size: "10 B" }),
    "英文界面下附件元素取英文词条",
  );
});

/* ─── 8. 提醒（#68 / #114）：rail 入口 + 指示点 + 弹窗（列表 / 空态 / 新增 / 删除） ─────
   验收面：① 入口与指示点照原型，但圆点的判据是「**还有未到点的提醒**」（C17）
   ② 新增控件是**日期 + 钟点两个原生输入**，提交拼成 `YYYY-MM-DD HH:MM` 一个串（C15）
   ③ 列表行按后端给的 `state` 加状态后缀，只用既有档位 token（C16）
   ④ 三条新词条只在前端两份词条里（C18 的前端半边）⑤ 删除后回到空态。
   数据面归属见 ADR-0012 / ADR-0017：提醒是**库内对象**、**到点是后端事实**，这里的每一条
   断言都只经后端载荷（替身 fetch）；测试里没有任何一行自己算「这个时刻过没过」。 */

/** 与 app.js 同一口径的「本机今天」（`YYYY-MM-DD`）：日期格的默认值就是它（C15 判据 2）。 */
function localToday() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/** 一条有归属频道的提醒（形状与 `GET /api/reminders` 的元素一致，含 C20 的两个恒有键）。 */
const REMINDER_WITH_CHANNEL = {
  id: "rm_1",
  text: "检查令牌替换进度",
  time: "2026-10-18 14:30",
  channel_id: "ch-1",
  channel_name: "设计系统重构",
  created_at: "2026-10-16T10:00:00.000Z",
  fired_at: null,
  state: "pending",
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
  // rail-head 那一段（到 `.search-shell` 为止）：提醒与设置都是这里的 `.icon-btn`。
  const head = rail.slice(0, indexOf(rail, 'class="search-shell"'));

  assert.equal(rail.includes('id="btnReminders"'), true, "rail head 里应有提醒入口");
  const dot = /<span class="dot" id="reminderDot"([^>]*)>/.exec(rail);
  assert.ok(dot, "入口里应有一个 .dot 的指示点");
  // `hidden` 是标记里的静态事实：JS 还没跑完的那一帧也不能凭空空出一个点（原型 :1886 同一纪律）。
  assert.match(dot[1], /\bhidden\b/, "指示点的初始状态必须是「没有提醒」");
  // 入口与设置入口同形：文案不写死，按词条键取。
  assert.match(head, /data-i18n-attr="aria-label:rail\.reminders,title:rail\.reminders"/);
});

test("rail 头部：提醒与设置两枚 icon-btn 同处 rail-head，顺序照原型（品牌 → 提醒 → 设置）", () => {
  const html = readStudio("index.html").replace(/<!--[\s\S]*?-->/g, "");
  const rail = html.slice(html.indexOf('class="rail-head"'), html.indexOf("</aside>"));
  const head = rail.slice(0, indexOf(rail, 'class="search-shell"'));

  // 用户 D1 的裁定：齿轮回头部、不新增太阳图标与主题快捷键。
  assert.equal(head.includes('id="btnSettings"'), true, "设置入口应在 rail-head 里（原型 :515 的位置）");
  assert.match(
    head,
    /data-od-id="btn-settings"[^>]*data-i18n-attr="aria-label:settings\.open,title:settings\.open"/,
    "设置入口保留原型的 data-od-id 与词条绑定",
  );
  const order = ["class=\"brand\"", 'id="btnReminders"', 'id="btnSettings"'].map((marker) => indexOf(head, marker));
  assert.deepEqual(
    order,
    [...order].sort((a, b) => a - b),
    "rail-head 的次序是品牌 → 提醒 → 设置（原型 alibot-app.html:511-518）",
  );
  // 底部的「我」那一行只剩身份信息，不再有控件。
  const foot = rail.slice(indexOf(rail, 'class="rail-foot"'));
  assert.equal(/<button/i.test(foot), false, "rail-foot 里不再有设置入口（已回到头部）");
  assert.equal(rail.includes('id="btnSettings"'), true, "设置入口在整个 rail 里恰好一份");
  assert.equal(rail.split('id="btnSettings"').length - 1, 1, "设置入口不重复");
});

test("提醒：弹窗是 wide，新增控件是两个原生输入（文本 + 日期 + 钟点）", () => {
  const markup = remindersMarkup();

  assert.ok(markup.includes('class="modal wide"'), "提醒弹窗照原型走 wide 那一档");
  assert.ok(markup.includes('id="reminderList"'), "列表容器由 app.js 按后端读数渲染");
  assert.ok(markup.includes('id="reminderText"'), "文本格");
  // C15 判据 1：日期与钟点是**两个原生输入**（不是 `datetime-local` 那种一个控件两个语义）。
  const types = [...markup.matchAll(/<input[^>]*\stype="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual(types, ["text", "date", "time"], "新增控件就是文本 + 日期 + 钟点三个原生输入");
  // 日期格里不写死取值：默认「今天」是 app.js 在打开弹窗时填的（写死在标记里的那格隔天就是谎）。
  const dateTag = /<input[^>]*id="reminderDate"[^>]*>/.exec(markup);
  assert.ok(dateTag, "有日期格");
  assert.match(dateTag[0], /type="date"/);
  assert.equal(/\svalue=/.test(dateTag[0]), false, "日期格的默认值不写死在标记里");
  // C15 判据 5：日期格有可访问名，且可访问名走词条（裸中文扫描也靠这条绑定过）。
  assert.match(dateTag[0], /data-i18n-attr="aria-label:reminder\.dateLabel"/);
  // 重复字段仍然不扩张（ADR-0017 §二：重复由任务承载，提醒不造第二个重复语义）。
  assert.equal(/id="reminder(Day|Repeat)"/.test(markup), false, "没有重复字段");
  // 时间格的默认取值照原型 `#rmTime`（`value="09:00"`）。
  assert.match(markup, /id="reminderTime"[\s\S]{0,160}value="09:00"/);
  // 可访问名与占位符都走词条（两个控件自己都没有可读标签）。
  assert.match(markup, /data-i18n-attr="aria-label:reminder\.timeLabel"/);
});

test("提醒：日期格打开弹窗时就是当天（不用手填），钟点保持 09:00", () => {
  const harness = createHarness();
  const dateInput = harness.elementFor("#reminderDate");
  const timeInput = harness.elementFor("#reminderTime");

  dateInput.value = "1999-01-01"; // 先填一个旧值，证明下面把它改成今天的是这条规则本身
  harness.api.openRemindersModal();

  assert.equal(dateInput.value, localToday(), "日期格的默认值是当天（C15 判据 2）");
  assert.equal(timeInput.value, "09:00", "钟点默认仍是 09:00（与现行为一致）");
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

test("提醒：列表条目字段齐全（文本 / 归属频道 / 状态后缀 / 删除），行里不再有独立的 .time 列", async () => {
  const harness = createHarness({
    reminders: [{ ...REMINDER_WITH_CHANNEL }, { ...REMINDER_WITH_CHANNEL, id: "rm_2", text: "评审 raft 快照方案", time: "2026-10-19 10:00", channel_id: null, channel_name: "" }],
  });
  await harness.api.loadReminders(); // 列表来自后端读数（`GET /api/reminders`），不是本地拼的

  const rows = harness.elementFor("#reminderList").querySelectorAll(".reminder-row");
  assert.equal(rows.length, 2, "列表逐条渲染，不合并");
  assert.deepEqual(rows.map((row) => row.dataset.reminder), ["rm_1", "rm_2"]);

  const first = rows[0];
  assert.equal(first.querySelector(".grow").textContent, "检查令牌替换进度 · #设计系统重构", "文本 + 归属频道");
  // 用户裁定 2026-10-17（对 C16 的显式偏离）：到期时刻并入状态后缀，行里**不再**单立一列。
  assert.equal(first.querySelector(".time"), null, "行里没有独立的 .time 列（时刻只在后缀里出现一次）");
  const entryTexts = first.descendants().map((node) => node.textContent);
  assert.equal(
    entryTexts.filter((text) => text === "2026-10-18 14:30").length,
    1,
    "同一行里到期时刻只出现一次",
  );
  // 待响行的状态后缀就是**到期时刻**本身（后端串原样，不切片），不是「待响」这种标签。
  assert.equal(first.querySelector(".state").textContent, "2026-10-18 14:30", "待响 ⇒ 完整到期时刻");
  assert.equal(first.querySelector(".state").dataset.state, "pending");
  const remove = first.querySelector("[data-delete-reminder]");
  assert.equal(remove.dataset.deleteReminder, "rm_1");
  assert.equal(remove.getAttribute("aria-label"), harness.catalogs["zh-CN"]["reminder.deleteAria"]);
  assert.equal(first.querySelectorAll("[data-delete-reminder]").length, 1, "一行一个删除控件");

  // 不归属任何频道的提醒：没有 `· #频道名` 那一段（可空列的正本事实）。
  assert.equal(rows[1].querySelector(".grow").textContent, "评审 raft 快照方案");
  assert.equal(rows[1].querySelector(".ch"), null);
  assert.equal(rows[1].querySelector(".time"), null);
  assert.equal(rows[1].querySelector(".state").textContent, "2026-10-19 10:00", "第二条的到期时刻也在后缀里");
});

test("提醒：三种 state 在同一列表里后缀可区分（待响 = 到期时刻 / 已提醒 / 已错过）", () => {
  const harness = createHarness();
  harness.api.state.reminders = [
    { ...REMINDER_WITH_CHANNEL, id: "rm_pending", state: "pending", fired_at: null },
    {
      ...REMINDER_WITH_CHANNEL,
      id: "rm_fired",
      time: "2026-10-17 14:30",
      state: "fired",
      fired_at: "2026-10-17T06:30:05.000Z",
    },
    {
      ...REMINDER_WITH_CHANNEL,
      id: "rm_missed",
      time: "2026-10-10 09:00",
      state: "missed",
      fired_at: "2026-10-16T09:00:00.000Z",
    },
  ];
  harness.api.renderRemindersModal();

  const rows = harness.elementFor("#reminderList").querySelectorAll(".reminder-row");
  const suffixes = rows.map((row) => row.querySelector(".state").textContent);
  assert.deepEqual(suffixes, [
    "2026-10-18 14:30",
    harness.catalogs["zh-CN"]["reminder.stateFired"],
    harness.catalogs["zh-CN"]["reminder.stateMissed"],
  ]);
  assert.equal(new Set(suffixes).size, 3, "三种 state 的后缀文本互不相同（C16 判据 1）");
});

test("提醒：状态后缀在两份词条里各取各的语言，切语言后即时重渲染", () => {
  const harness = createHarness();
  harness.api.state.reminders = [
    { ...REMINDER_WITH_CHANNEL, id: "rm_fired", state: "fired", fired_at: "2026-10-17T06:30:05.000Z" },
    { ...REMINDER_WITH_CHANNEL, id: "rm_missed", state: "missed", fired_at: "2026-10-16T09:00:00.000Z" },
  ];
  harness.api.renderRemindersModal();

  harness.api.applySettings({ locale: "en" });
  const suffixes = harness
    .elementFor("#reminderList")
    .querySelectorAll(".reminder-row")
    .map((row) => row.querySelector(".state").textContent);
  assert.deepEqual(suffixes, [
    harness.catalogs.en["reminder.stateFired"],
    harness.catalogs.en["reminder.stateMissed"],
  ]);
});

test("提醒：到点不计未读（状态后缀重渲染不改任何未读读数）", () => {
  const harness = createHarness();
  harness.api.state.channels = [
    { id: "ch-1", name: "设计系统重构", unread_count: 3, last_seq: 42 },
  ];
  const before = JSON.stringify(harness.api.state.channels);
  const callsBefore = harness.calls.length;

  harness.api.state.reminders = [
    { ...REMINDER_WITH_CHANNEL, id: "rm_fired", state: "fired", fired_at: "2026-10-17T06:30:05.000Z" },
    { ...REMINDER_WITH_CHANNEL, id: "rm_missed", state: "missed", fired_at: "2026-10-16T09:00:00.000Z" },
  ];
  harness.api.renderRemindersModal();
  harness.api.renderReminderDot();

  assert.equal(JSON.stringify(harness.api.state.channels), before, "state 变化不改变任何未读读数（C16 判据 4）");
  assert.deepEqual(harness.calls.slice(callsBefore), [], "重绘状态后缀不发任何请求（更不会去改未读）");
});

test("提醒：新增写入后端（文本 + 拼好的到期时刻 + 当前频道），并铺上响应里的最新列表", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  harness.api.state.channels = [{ id: "ch-1", name: "设计系统重构" }];
  harness.api.state.activeChannelId = "ch-1";
  harness.elementFor("#reminderText").value = "评审 raft 快照方案";
  harness.elementFor("#reminderDate").value = "2026-10-18";
  harness.elementFor("#reminderTime").value = "10:00";

  await harness.elementFor("#reminderAddSubmit").dispatchEvent({ type: "click" });
  await flush();

  const posts = harness.calls.filter((call) => call.method === "POST");
  assert.deepEqual(
    posts.map((call) => ({ url: call.url, body: call.body })),
    [
      {
        url: "/api/reminders",
        body: { text: "评审 raft 快照方案", time: "2026-10-18 10:00", channel_id: "ch-1" },
      },
    ],
  );
  assert.equal(harness.api.state.reminders.length, 1);
  assert.equal(harness.elementFor("#reminderDot").hidden, false, "新增一条未到点的提醒后指示点出现");
  assert.equal(
    harness.elementFor("#reminderList").querySelectorAll(".reminder-row").length,
    1,
    "列表按后端返回的那一份渲染",
  );
  assert.equal(harness.elementFor("#reminderText").value, "", "提交后清空正文");
  assert.equal(harness.elementFor("#reminderTime").value, "10:00", "钟点格保留当前取值");
  assert.equal(harness.elementFor("#reminderDate").value, localToday(), "日期格回到今天");
});

test("提醒：只改日期不改钟点也能提交（钟点保留 09:00），payload 的 time 是一个串", async () => {
  const harness = createHarness();
  harness.api.bindEvents();
  harness.api.state.channels = [{ id: "ch-1", name: "设计系统重构" }];
  harness.api.state.activeChannelId = "ch-1";
  harness.api.openRemindersModal(); // 日期格被填成今天，钟点保持 09:00
  harness.elementFor("#reminderText").value = "评审 raft 快照方案";
  harness.elementFor("#reminderDate").value = "2026-11-02"; // 只改日期

  await harness.elementFor("#reminderAddSubmit").dispatchEvent({ type: "click" });
  await flush();

  const posts = harness.calls.filter((call) => call.method === "POST");
  assert.equal(posts.length, 1, "提交成功（没被前端拦下）");
  assert.equal(posts[0].body.time, "2026-11-02 09:00", "钟点保留默认 09:00（C15 判据 3）");
  assert.equal(typeof posts[0].body.time, "string", "time 是**一个串**，不是数组（C15 判据 4）");
  assert.deepEqual(
    Object.keys(posts[0].body).sort(),
    ["channel_id", "text", "time"],
    "没有第二个时间键（日期与钟点在发送前就拼成一个）",
  );
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
    "reminder.dateLabel",
    "reminder.timeLabel",
    "reminder.stateFired",
    "reminder.stateMissed",
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
test("提醒：rail 指示点的判据是「有未到点的提醒」，不读条数（C17）", () => {
  const harness = createHarness();
  const dot = harness.elementFor("#reminderDot");
  const fired = {
    ...REMINDER_WITH_CHANNEL,
    id: "rm_fired",
    time: "2026-10-17 14:30",
    state: "fired",
    fired_at: "2026-10-17T06:30:05.000Z",
  };
  const missed = {
    ...REMINDER_WITH_CHANNEL,
    id: "rm_missed",
    time: "2026-10-10 09:00",
    state: "missed",
    fired_at: "2026-10-16T09:00:00.000Z",
  };
  const pending = { ...REMINDER_WITH_CHANNEL, id: "rm_pending", fired_at: null };

  // C17 判据 4（旧判据下是**红**的那一条）：有两条提醒、但两条都已到点 ⇒ 指示点隐藏。
  dot.hidden = false; // 先点亮，证明下面把它收回去的是这条判据本身
  harness.api.state.reminders = [fired, missed];
  harness.api.renderReminderDot();
  assert.equal(dot.hidden, true, "只有已到点的提醒 ⇒ 指示点隐藏（length 判据在这里会亮）");

  // C17 判据 2：还有一条未到点 ⇒ 出现（混在一起也是出现）。
  harness.api.state.reminders = [fired, pending];
  harness.api.renderReminderDot();
  assert.equal(dot.hidden, false, "还有一条未到点的提醒 ⇒ 指示点出现");

  harness.api.state.reminders = [pending];
  harness.api.renderReminderDot();
  assert.equal(dot.hidden, false, "只有那条未到点的提醒 ⇒ 仍然出现");

  harness.api.state.reminders = [];
  harness.api.renderReminderDot();
  assert.equal(dot.hidden, true, "一条都没有 ⇒ 隐藏（既有行为保持）");
});

test("提醒：状态后缀只消费既有档位 token（不加新色位、不用 --error）", () => {
  // 扫**去注释**后的 CSS：注释里提到 `--error`（就是上面那条纪律的说明文字）不算消费点。
  const css = stripBlockComments(readStudio("app.css"));
  // token **名**的正本 = 三个只读文件里**声明**过的名字（`--x:` 形式，`var(--x)` 不算声明）。
  const declared = new Set();
  for (const file of TOKEN_FILES) {
    const source = fs.readFileSync(file, "utf8");
    for (const match of source.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) declared.add(match[1]);
  }
  assert.ok(declared.size > 40, `token 名集合看起来不对：${declared.size}`);

  const reminderRules = css.match(/^[^{}]*\.reminder[^{}]*\{[^}]*\}/gm) || [];
  assert.ok(reminderRules.length >= 5, `提醒那几条规则没扫到：${reminderRules.length}`);
  const used = new Set();
  for (const rule of reminderRules) {
    for (const match of rule.matchAll(/var\((--[a-zA-Z0-9-]+)\)/g)) used.add(match[1]);
  }
  assert.ok(used.size > 0, "提醒的规则总该消费一些 token");
  for (const name of used) {
    assert.ok(declared.has(name), `提醒规则消费了未声明的 token 名（= 新色位）：${name}`);
  }
  // C16 判据 3：已到点**不是错误**，这一族里不出现 `--error`。
  assert.equal(
    reminderRules.some((rule) => rule.includes("--error")),
    false,
    "提醒的规则里不该出现 --error（已到点不是错误态）",
  );
});

test("提醒：状态后缀的三种取值都在前端词条里，且没越界写进后端词条表", () => {
  const harness = createHarness();
  const keys = ["reminder.dateLabel", "reminder.stateFired", "reminder.stateMissed"];

  for (const key of keys) {
    assert.ok(harness.catalogs["zh-CN"][key], `${key} 在源语言里缺失`);
    assert.ok(harness.catalogs.en[key], `${key} 在第二语言里缺失`);
    assert.notEqual(
      harness.catalogs["zh-CN"][key],
      harness.catalogs.en[key],
      `${key} 的两份取值不能是同一句话`,
    );
  }

  // C18 判据 2：本票的越界校验方向是「验前端没越界写后端键」——界面静态文案只进第一层。
  const backend = fs.readFileSync(path.join(__dirname, "../alibot/i18n.py"), "utf8");
  for (const key of keys) {
    assert.equal(backend.includes(key), false, `后端词条表里不该出现前端键：${key}`);
  }
});

/* ─── 9. 成员名册（频道成员的**只读**全名单） ──────────────────────────────
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

/* ─── 10. 左栏「工作区未读」汇总行下线：整块移除，行内的未读徽标照旧 ─────────────

   那一行是纯展示层的汇总（各 target 的 `unread_count` 相加），不是 `GET /api/workspace`
   的 `unread_total` 契约：后端字段与各端点读数在本票里一字不动，所以守护用例只看两件事——
   ① 左栏不再有那个容器 / 词条（HTML 与两份词条同时消失，工具栏之后不留任何节点）；
   ② 频道名单容器与频道 / 私信行内的未读徽标仍然照旧（不许把隔壁那只徽标一起删掉）。 */

/** 一段标记里某个 `class="..."` 起点所在的元素闭合之后、`<nav data-od-id="nav-lists">` 之前的那截文本。

    按标签深度数闭合，而不是数 `<div` / 数换行：加一个合法包装 div 不该误报，
    残留的空 `<span>` 也不该漏检。注释不算标记（同本文件其它扫描的先例）。 */
function railGap(html) {
  const stripped = html.replace(/<!--[\s\S]*?-->/g, "");
  const classAt = stripped.indexOf('class="rail-actions"');
  assert.notEqual(classAt, -1, "index.html 里应有工具栏（.rail-actions）");
  let index = stripped.indexOf(">", classAt) + 1;
  let depth = 1;
  while (index < stripped.length && depth > 0) {
    const tag = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)/.exec(stripped.slice(index));
    if (!tag) break;
    const tagStart = index + tag.index;
    index = stripped.indexOf(">", tagStart) + 1;
    if (tag[2].toLowerCase() === "div") depth += tag[1] ? -1 : 1;
  }
  const navAt = stripped.indexOf('data-od-id="nav-lists"');
  assert.notEqual(navAt, -1, "index.html 里应有名单容器（nav-lists）");
  return stripped.slice(index, stripped.lastIndexOf("<", navAt));
}

test("左栏不再有「工作区未读」汇总行，频道与私信行内的未读徽标照旧", () => {
  // 注释不算标记：下面每一条扫描都和本文件其它 HTML 扫描一样先剥掉注释。
  const html = readStudio("index.html").replace(/<!--[\s\S]*?-->/g, "");

  // ① 那一整块（容器 + 词条标签 + 徽标 + 定位名）从标记里消失：四项各自是一条独立的回归面。
  assert.equal(html.includes('id="unreadSummary"'), false, "左栏不再有未读汇总容器");
  assert.equal(html.includes('data-od-id="workspace-unread"'), false, "它的定位名一并下线");
  assert.equal(html.includes("rail-unread"), false, "不再有 .rail-unread 这个样式钩子");
  assert.equal(/rail\.unread/.test(html), false, "HTML 里不再挂着 rail.unread* 的文案绑定");
  // 工具栏与名单之间不留任何节点（含空节点）。
  assert.match(railGap(html), /^\s*$/, "工具栏之后只剩白字，没有残留的空节点");

  // ② 名单容器与分组标签原样在：删的是汇总行，不是左栏的名单。
  assert.ok(html.includes('id="channelList"'), "频道名单容器还在");
  assert.ok(html.includes('id="channelListEmpty"'), "频道空态还在");
  assert.match(html, /data-i18n="rail\.groupChannels"/, "「频道」分组标签还在");

  // ③ 两份词条都不再留这两个键（只删一半会让某语言露出键名）。
  const { catalogs } = createHarness();
  for (const locale of ["zh-CN", "en"]) {
    for (const key of ["rail.unread", "rail.unreadCount"]) {
      assert.equal(
        Object.prototype.hasOwnProperty.call(catalogs[locale], key),
        false,
        `${locale} 里不该再有 ${key}`,
      );
    }
  }

  // ④ app.js 里那套汇总状态与两个渲染函数一并下线（不是只摘调用点、留下死代码）。
  //    注释不算代码：本文件对「裸中文」的扫描同样跳过注释，这里守同一条纪律。
  const appSource = stripBlockComments(readStudio("app.js")).replace(/^\s*\/\/.*$/gm, "");
  for (const symbol of ["unreadTotal", "syncUnreadTotal", "renderUnreadSummary"]) {
    assert.equal(
      new RegExp(`\\b${symbol}\\b`).test(appSource),
      false,
      `app.js 里不该再有 ${symbol}`,
    );
  }
  assert.equal(/rail\.unread/.test(appSource), false, "app.js 不再引用 rail.unread* 词条");

  // ⑤ 行内未读徽标照旧：频道与私信两条都在，读数都跟着后端给的 `unread_count` 走。
  const harness = createHarness();
  harness.api.state.agents = [
    {
      id: "ag_1",
      kind: "agent",
      display_name: "演示 Agent",
      avatar_slot: 2,
      enabled: true,
      status: "online",
    },
  ];
  harness.api.state.channels = [
    { id: "ch_9", name: "设计系统重构", last_seq: 7, last_read_seq: 4, unread_count: 3 },
  ];
  harness.api.state.dms = [
    {
      id: "dm_1",
      kind: "dm",
      peer: { id: "ag_1", display_name: "演示 Agent", avatar_slot: 2 },
      last_seq: 5,
      last_read_seq: 3,
      unread_count: 2,
    },
  ];
  harness.api.renderChannels();
  harness.api.renderDms();
  const rowOf = (container, key, id) => {
    const row = container.children.find((node) => node.dataset[key] === id);
    assert.ok(row, `${key}=${id} 的行仍按后端 id 渲染`);
    return row;
  };
  const badgeOf = (row) => row.children.find((node) => node.classList.contains("badge")) || null;

  const channelBadge = badgeOf(rowOf(harness.elementFor("#channelList"), "channel", "ch_9"));
  assert.ok(channelBadge, "频道的未读徽标仍在（本票不许连带删掉它）");
  assert.equal(channelBadge.textContent, "3", "频道徽标读的就是后端给的 unread_count");

  const dmList = harness.elementFor("#dmList");
  const dmBadge = badgeOf(rowOf(dmList, "dm", "dm_1"));
  assert.ok(dmBadge, "私信的未读徽标也在");
  assert.equal(dmBadge.textContent, "2", "私信徽标同样读后端给的 unread_count");
  assert.equal(dmBadge.getAttribute("data-od-id"), "unread-dm_1", "徽标的定位名不变");

  // 归零后徽标照旧被摘掉：就地更新那条行为没有被这次删除带偏（也真走到节点的 remove 路径）。
  harness.api.state.dms = [{ ...harness.api.state.dms[0], unread_count: 0 }];
  harness.api.renderDms();
  assert.equal(badgeOf(rowOf(dmList, "dm", "dm_1")), null, "未读归零时徽标被摘掉");

  // ⑥ 样式侧同样只下线汇总行那两条：`.rail-unread` 没了，行内 `.badge` 的未读色仍在。
  const css = readStudio("app.css");
  assert.equal(css.includes(".rail-unread"), false, "app.css 里不再有 .rail-unread 规则");
  assert.match(css, /\.badge \{[^}]*var\(--unread\)/, "行内未读徽标的样式还在");
});
