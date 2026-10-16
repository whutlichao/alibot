const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

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
    this._textContent = "";
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
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  dispatchEvent(event) {
    const results = (this.listeners.get(event.type) || []).map((listener) => listener.call(this, event));
    return Promise.all(results.filter((result) => result && typeof result.then === "function"));
  }

  focus() {}
}

function createHarness(fetch) {
  const elements = new Map(
    ["#searchQueryInput", "#searchTargetSelect", "#searchSubmit", "#searchError", "#searchResults"].map(
      (selector) => [selector, new FakeNode("input")],
    ),
  );
  const document = {
    querySelector(selector) {
      const element = elements.get(selector);
      if (!element) throw new Error(`Unexpected selector: ${selector}`);
      return element;
    },
    createElement(tagName) {
      return new FakeNode(tagName);
    },
    createTextNode(text) {
      const node = new FakeNode("#text");
      node.textContent = text;
      return node;
    },
  };
  const context = vm.createContext({ document, fetch, URLSearchParams });
  const appPath = path.join(__dirname, "../alibot/studio/app.js");
  const source = fs.readFileSync(appPath, "utf8");
  const startup = /\n\s*bindEvents\(\);\s*loadWorkspace\(\);\s*$/;
  assert.match(source, startup, "app startup calls should remain at the end of app.js");
  const testSource = source.replace(
    startup,
    "\nglobalThis.__searchUiTest = { bindSearchEvents, state };\n",
  );
  vm.runInContext(testSource, context, { filename: appPath });
  context.__searchUiTest.bindSearchEvents();
  return {
    state: context.__searchUiTest.state,
    input: elements.get("#searchQueryInput"),
    target: elements.get("#searchTargetSelect"),
    button: elements.get("#searchSubmit"),
    error: elements.get("#searchError"),
    results: elements.get("#searchResults"),
  };
}

function response(payload) {
  return { ok: true, json: async () => payload };
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function hit(body, ranges) {
  return {
    message: { body, author: { display_name: "Owner" } },
    target: { id: "channel-1", kind: "channel", display_name: "Search" },
    highlight_ranges: ranges,
  };
}

function enterSearch(harness) {
  let prevented = false;
  return harness.input.dispatchEvent({
    type: "keydown",
    key: "Enter",
    preventDefault() {
      prevented = true;
    },
  }).then(() => assert.equal(prevented, true));
}

test("renders only the two backend intervals among three matching occurrences on button click", async () => {
  const harness = createHarness(async () => response({
    hits: [hit("Needle banana needle, needle", [{ start: 0, end: 6 }, { start: 14, end: 20 }])],
  }));
  harness.input.value = "needle";

  await harness.button.dispatchEvent({ type: "click" });

  const renderedNodes = descendants(harness.results);
  const marks = renderedNodes.filter((node) => node.tagName === "mark");
  const plainText = renderedNodes
    .filter((node) => node.tagName === "#text")
    .map((node) => node.textContent)
    .join("");
  assert.equal(marks.length, 2);
  assert.deepEqual(marks.map((node) => node.textContent), ["Needle", "needle"]);
  assert.equal(plainText, " banana , needle");
});

test("preserves leading and trailing whitespace in the literal query on Enter", async () => {
  let requestedQuery = null;
  const harness = createHarness(async (url) => {
    requestedQuery = new URLSearchParams(url.split("?")[1]).get("q");
    return response({ hits: [] });
  });
  harness.input.value = " needle ";

  await enterSearch(harness);

  assert.equal(requestedQuery, " needle ");
  assert.equal(harness.state.search.query, " needle ");
});

test("shows a backend failure instead of an empty-result state", async () => {
  const harness = createHarness(async () => {
    throw new TypeError("Failed to fetch");
  });
  harness.input.value = "needle";

  await harness.button.dispatchEvent({ type: "click" });

  assert.equal(harness.error.hidden, false);
  assert.match(harness.error.textContent, /后端不可达/);
  assert.equal(harness.results.children.length, 0);
});

test("ignores an older response after a newer query completes", async () => {
  let resolveOlder;
  const harness = createHarness((url) => {
    const query = new URLSearchParams(url.split("?")[1]).get("q");
    if (query === "older") {
      return new Promise((resolve) => {
        resolveOlder = () => resolve(response({ hits: [hit("older result", [{ start: 0, end: 5 }])] }));
      });
    }
    return Promise.resolve(response({ hits: [hit("newer result", [{ start: 0, end: 5 }])] }));
  });

  harness.input.value = "older";
  const olderRequest = harness.button.dispatchEvent({ type: "click" });
  harness.input.value = "newer";
  await enterSearch(harness);
  resolveOlder();
  await olderRequest;

  assert.equal(harness.state.search.query, "newer");
  assert.equal(harness.state.search.hits[0].message.body, "newer result");
  assert.equal(harness.button.disabled, false);
});
