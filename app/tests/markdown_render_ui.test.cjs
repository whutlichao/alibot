/**
 * 消息正文的 Markdown 渲染器（#290 / ADR-0037）。
 *
 * 这一套用例**只装载 `markdown.js`**，不装载 `app.js`：渲染器是纯函数模块（输入正文与提及区间，
 * 输出 DOM 节点），因此用例不需要工作台状态、不需要 `fetch` 替身、不需要事件流。这与其它
 * `*_ui.test.cjs` 的接缝不同，是**刻意的**——能单独测，正是当初把它拆成一个文件边界的理由。
 *
 * 断言分六组：
 *   1. 块级语法：段落 / 标题 / 围栏代码块 / 列表 / 引用 / 分隔线 / 表格
 *   2. 行内语法：粗体 / 斜体 / 删除线 / 行内代码 / 链接 / 转义
 *   3. 单换行按 GFM 口径保留为 `<br>`
 *   4. **危险输入一律降级为纯文本**：HTML 标签、`javascript:` 链接、`data:` 链接
 *   5. 图片**不加载**（ADR-0037 §五）
 *   6. 提及区间映射：区间是原始正文的 UTF-16 下标，要能穿过被消费掉的 `**` 落到文本节点上
 *
 * 最后一组还有一条**机械守卫**：`markdown.js` 与 `app.js` 都不含 `innerHTML`——那条纪律此前
 * 只被技能正文那一段用例覆盖，这里扩到全文件。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");

/* ─── DOM 替身：只实现 markdown.js 用到的那几个方法 ─────────────────────────── */

class FakeNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.className = "";
    this.style = {};
    this._textContent = "";
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  appendChild(child) {
    // 与真实 DOM 一致：appendChild 不会清掉已有的文本节点（`el()` 只在 `textContent` 赋值时清）。
    this.children.push(child);
    child.parentNode = this;
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  descendants() {
    return [this, ...this.children.flatMap((child) => (child.descendants ? child.descendants() : []))];
  }
}

const document = {
  createElement: (tagName) => new FakeNode(tagName),
  createTextNode: (text) => {
    const node = new FakeNode("#text");
    node._textContent = String(text);
    return node;
  },
};

const context = vm.createContext({ document });
vm.runInContext(fs.readFileSync(path.join(STUDIO_DIR, "markdown.js"), "utf8"), context, {
  filename: "markdown.js",
});

/* ─── 断言辅助 ─────────────────────────────────────────────────────────────── */

function render(body, spans) {
  return context.renderMarkdown(body, spans || []);
}

/** 子树里某标签名的全部节点。 */
function byTag(root, tagName) {
  return root.descendants().filter((node) => node.tagName === tagName);
}

/** 子树里某 class 的全部节点。 */
function byClass(root, name) {
  return root.descendants().filter((node) => String(node.className).split(/\s+/).includes(name));
}

/** 正文里的提及节点（`.mention`）的文本，按出现顺序。 */
function mentions(root) {
  return byClass(root, "mention").map((node) => node.textContent);
}

function readStudio(file) {
  return fs.readFileSync(path.join(STUDIO_DIR, file), "utf8");
}

/** 去掉注释：注释里提到 `innerHTML`（比如「不用 innerHTML」这条纪律本身）不算违规。 */
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/* ─── 1. 块级语法 ──────────────────────────────────────────────────────────── */

test("段落：一段文字包在 <p> 里，包在 .markdown-body 下", () => {
  const root = render("hello world");
  assert.equal(root.className, "markdown-body");
  const paragraphs = byTag(root, "p");
  assert.equal(paragraphs.length, 1);
  assert.equal(paragraphs[0].textContent, "hello world");
});

test("标题：#~###### 映射到 h1~h6，并带 md-hN 档位 class", () => {
  const root = render("# 一级\n\n## 二级\n\n###### 六级");
  const headings = byTag(root, "h1").concat(byTag(root, "h2"), byTag(root, "h6"));
  assert.equal(headings.length, 3);
  assert.equal(byTag(root, "h1")[0].textContent, "一级");
  assert.equal(byTag(root, "h2")[0].textContent, "二级");
  assert.equal(byTag(root, "h6")[0].textContent, "六级");
  // 档位 class 供 CSS 分别给字号（标题映射到正文档，不是 32px 的大标题）。
  assert.match(byTag(root, "h2")[0].className, /md-h2/);
});

test("围栏代码块：产出 pre.md-code > code，内容逐字保留，不解析行内记号", () => {
  const body = "```js\nconst a = **not bold**;\n  indented\n```";
  const root = render(body);
  const pre = byTag(root, "pre")[0];
  assert.ok(pre, "有一个 <pre>");
  assert.match(pre.className, /md-code/);
  const code = byTag(pre, "code")[0];
  assert.ok(code, "<pre> 里是一个 <code>");
  assert.equal(code.textContent, "const a = **not bold**;\n  indented", "内容逐字保留（含缩进）");
  assert.equal(code.dataset.lang, "js");
  // 代码块里**不**解析行内记号：`**` 原样可见。
  assert.equal(byTag(code, "strong").length, 0, "代码块里不该有 <strong>");
});

test("围栏代码块没闭合时吃到文末，不吞掉内容", () => {
  const root = render("```\nline one\nline two");
  const code = byTag(root, "code")[0];
  assert.equal(code.textContent, "line one\nline two");
});

test("无序列表与有序列表：产出 ul/ol > li", () => {
  const ul = render("- one\n- two");
  assert.equal(byTag(ul, "ul").length, 1);
  assert.deepEqual(byTag(ul, "li").map((node) => node.textContent), ["one", "two"]);

  const ol = render("1. one\n2. two");
  assert.equal(byTag(ol, "ol").length, 1);
  assert.deepEqual(byTag(ol, "li").map((node) => node.textContent), ["one", "two"]);
});

test("引用：产出 blockquote.md-quote，内部照常切块", () => {
  const root = render("> quoted line");
  const quote = byClass(root, "md-quote")[0];
  assert.ok(quote, "有一个 blockquote");
  assert.equal(quote.tagName, "blockquote");
  assert.equal(byTag(quote, "p")[0].textContent, "quoted line");
});

test("分隔线：产出 <hr>", () => {
  assert.equal(byTag(render("---"), "hr").length, 1);
});

test("表格：thead/tbody/th/td 齐全，包在横向滚动容器里", () => {
  const root = render("| 项 | 值 |\n| --- | ---: |\n| a | 1 |");
  const wrapper = byClass(root, "md-table-wrap")[0];
  assert.ok(wrapper, "表格外面有滚动容器");
  const table = byTag(wrapper, "table")[0];
  assert.ok(table, "容器里是 <table>");
  assert.deepEqual(byTag(table, "th").map((node) => node.textContent), ["项", "值"]);
  assert.deepEqual(byTag(table, "td").map((node) => node.textContent), ["a", "1"]);
  // 右对齐来自分隔行的 `---:`。
  assert.equal(byTag(table, "td")[1].style.textAlign, "right");
});

test("表格行的列数与表头不齐时不降级：多的丢掉、少的补空", () => {
  const root = render("| a | b |\n| --- | --- |\n| 只有一列 |");
  const cells = byTag(root, "td");
  assert.equal(cells.length, 2, "按表头两列补齐");
  assert.equal(cells[1].textContent, "", "缺的那格是空的");
});

/* ─── 2. 行内语法 ──────────────────────────────────────────────────────────── */

test("强调：**粗** / *斜* / ~~删~~ 各自产出 strong / em / del", () => {
  const root = render("**粗** 和 *斜* 和 ~~删~~");
  assert.equal(byTag(root, "strong")[0].textContent, "粗");
  assert.equal(byTag(root, "em")[0].textContent, "斜");
  assert.equal(byTag(root, "del")[0].textContent, "删");
});

test("行内代码：`x` 产出 <code>，内容不解析行内记号", () => {
  const root = render("use `a **b** c` here");
  const code = byTag(root, "code")[0];
  assert.equal(code.textContent, "a **b** c");
  assert.equal(byTag(code, "strong").length, 0, "行内代码里不解析强调");
});

test("强调可以嵌套：**粗里有 *斜***", () => {
  const root = render("**粗 *斜* 体**");
  const strong = byTag(root, "strong")[0];
  assert.ok(strong, "有 <strong>");
  assert.equal(byTag(strong, "em").length, 1, "粗体里有 <em>");
  assert.equal(byTag(strong, "em")[0].textContent, "斜");
});

test("snake_case_name 不被误判成斜体（单字符强调要求两侧不是字母数字）", () => {
  const root = render("变量 snake_case_name 保持原样");
  assert.equal(byTag(root, "em").length, 0, "不该有 <em>");
  assert.match(root.textContent, /snake_case_name/);
});

test("2 * 3 * 4 不被误判成斜体（开标记右侧是空白）", () => {
  const root = render("算一下 2 * 3 * 4 等于多少");
  assert.equal(byTag(root, "em").length, 0);
  assert.match(root.textContent, /2 \* 3 \* 4/);
});

test("反斜杠转义：\\* 是字面星号，不开启强调", () => {
  const root = render("\\*不是斜体\\*");
  assert.equal(byTag(root, "em").length, 0);
  assert.equal(root.textContent, "*不是斜体*");
});

test("链接：http/https 产出 <a>，带 target=_blank 与 rel=noopener noreferrer", () => {
  const root = render("[文档](https://example.com/a)");
  const link = byTag(root, "a")[0];
  assert.ok(link, "有 <a>");
  assert.equal(link.getAttribute("href"), "https://example.com/a");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(link.getAttribute("rel"), "noopener noreferrer");
  assert.equal(link.textContent, "文档");
});

test("链接：mailto 也放行", () => {
  const link = byTag(render("[写信](mailto:a@b.c)"), "a")[0];
  assert.equal(link.getAttribute("href"), "mailto:a@b.c");
});

test("链接文字里的行内记号照常解析", () => {
  const link = byTag(render("[**粗**文档](https://example.com)"), "a")[0];
  assert.equal(byTag(link, "strong").length, 1);
  assert.equal(link.textContent, "粗文档");
});

/* ─── 3. 单换行 ────────────────────────────────────────────────────────────── */

test("单换行按 GFM 口径保留为 <br>，不并成空格", () => {
  const root = render("第一行\n第二行");
  const paragraph = byTag(root, "p")[0];
  assert.equal(byTag(paragraph, "br").length, 1, "两行之间有一个 <br>");
  assert.equal(paragraph.textContent, "第一行第二行");
});

test("空行分隔段落：两段各自是 <p>，不是 <br>", () => {
  const root = render("第一段\n\n第二段");
  assert.equal(byTag(root, "p").length, 2);
});

/* ─── 4. 危险输入一律降级为纯文本 ──────────────────────────────────────────── */

test("HTML 内联标签一律当字面文本，不产出对应元素", () => {
  const body = "<script>alert(1)</script><img src=x onerror=alert(1)><span onclick=x>y</span>";
  const root = render(body);
  // 正文逐字保留——降级成纯文本，不是删掉。
  assert.equal(root.textContent, body);
  // 只数**根之外**的节点：根自己就是 `.markdown-body` 的 `<div>`，那是容器的形状，不是解析出来的标签。
  const inner = root.descendants().filter((node) => node !== root);
  for (const tag of ["script", "img", "span", "a", "iframe", "div"]) {
    assert.equal(inner.filter((node) => node.tagName === tag).length, 0, `不该产出 <${tag}>`);
  }
});

test("javascript: 链接降级为纯文本，不产出 <a>", () => {
  const root = render("[点我](javascript:alert(1))");
  assert.equal(byTag(root, "a").length, 0, "不产出 <a>");
  assert.equal(root.textContent, "[点我](javascript:alert(1))", "原文逐字保留");
});

test("data: 链接降级为纯文本", () => {
  const root = render("[x](data:text/html;base64,PHNjcmlwdD4=)");
  assert.equal(byTag(root, "a").length, 0);
});

test("协议大小写与空白变形绕不过白名单", () => {
  for (const body of [
    "[x](JavaScript:alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](  javascript:alert(1))",
    "[x](vbscript:msgbox(1))",
    "[x](file:///etc/passwd)",
    // 协议相对地址会继承当前页面的协议，也不放行。
    "[x](//evil.example.com)",
  ]) {
    assert.equal(byTag(render(body), "a").length, 0, `不该产出 <a>：${body}`);
  }
});

test("相对地址不放行（消息正文里的链接应当是可点走的外部地址）", () => {
  for (const body of ["[x](./docs/a.md)", "[x](#anchor)", "[x](/api/x)"]) {
    assert.equal(byTag(render(body), "a").length, 0, `不该产出 <a>：${body}`);
  }
});

/* ─── 5. 图片不加载 ────────────────────────────────────────────────────────── */

test("图片不加载：![alt](url) 保持原字符，不产出 <img> 也不产出 <a>", () => {
  const body = "![架构图](https://example.com/a.png)";
  const root = render(body);
  assert.equal(byTag(root, "img").length, 0, "不产出 <img>：那会让每条消息都能向第三方发请求");
  assert.equal(byTag(root, "a").length, 0, "也不该退化成可点链接");
  assert.equal(root.textContent, body, "原字符保留");
});

/* ─── 6. 提及区间映射 ──────────────────────────────────────────────────────── */

/** 后端在正文里找出 `@名字` 的 UTF-16 区间（#291 之后的口径）。 */
function mentionSpans(body, name, memberId) {
  const spans = [];
  let index = 0;
  const token = "@" + name;
  while ((index = body.indexOf(token, index)) >= 0) {
    spans.push({ member_id: memberId || "mem_1", start: index, end: index + token.length });
    index += token.length;
  }
  return spans;
}

test("裸提及：区间落在文本上，包出 .mention", () => {
  const body = "@小明 看这个";
  const root = render(body, mentionSpans(body, "小明"));
  assert.deepEqual(mentions(root), ["@小明"]);
});

test("粗体里的提及：**@小明** 得到加粗的提及（先建树、再把区间投到文本节点上）", () => {
  const body = "**@小明** 看这个";
  const root = render(body, mentionSpans(body, "小明"));
  const strong = byTag(root, "strong")[0];
  assert.ok(strong, "有 <strong>");
  assert.deepEqual(mentions(strong), ["@小明"], "提及在 <strong> 里面");
});

test("行内代码里的提及：`@小明` 得到行内代码样式的提及", () => {
  const body = "`@小明` 看这个";
  const root = render(body, mentionSpans(body, "小明"));
  const code = byTag(root, "code")[0];
  assert.ok(code, "有 <code>");
  assert.deepEqual(mentions(code), ["@小明"], "提及在 <code> 里面");
});

test("提及前面的行内记号被消费掉后，区间仍然对得上", () => {
  // `**重点**` 在正文里占 8 个字符，渲染后只剩「重点」两个字符——区间必须按**原串**算。
  const body = "**重点** 然后 @小明 看";
  const root = render(body, mentionSpans(body, "小明"));
  assert.deepEqual(mentions(root), ["@小明"]);
});

test("emoji 在提及之前（UTF-16 口径）：区间不错位", () => {
  // 这是 #291 修的那条：`😀` 在 UTF-16 里是 2 个 code unit，后端给的下标已经按这个算。
  const body = "😀 @小明 看";
  const root = render(body, mentionSpans(body, "小明"));
  assert.deepEqual(mentions(root), ["@小明"]);
});

test("多个提及各自包好，不串位", () => {
  const body = "@小明 和 @小明 都看";
  const root = render(body, mentionSpans(body, "小明"));
  assert.deepEqual(mentions(root), ["@小明", "@小明"]);
});

test("列表项里的提及", () => {
  const body = "- @小明 看这个\n- 另一项";
  const root = render(body, mentionSpans(body, "小明"));
  assert.deepEqual(mentions(root), ["@小明"]);
});

test("表格单元格里的提及", () => {
  const body = "| 谁 |\n| --- |\n| @小明 |";
  const root = render(body, mentionSpans(body, "小明"));
  assert.deepEqual(mentions(root), ["@小明"]);
});

test("引用里的提及", () => {
  const body = "> @小明 看这个";
  const root = render(body, mentionSpans(body, "小明"));
  assert.deepEqual(mentions(root), ["@小明"]);
});

test("越界或重叠的区间退化为纯文本，不切错字符", () => {
  const body = "@小明 看";
  // end 超出正文长度 ⇒ 整个区间丢掉。
  assert.deepEqual(mentions(render(body, [{ member_id: "m", start: 0, end: 999 }])), []);
  // 重叠区间 ⇒ 后一个丢掉，不产出两个嵌套的 mention。
  assert.deepEqual(
    mentions(render(body, [
      { member_id: "m", start: 0, end: 3 },
      { member_id: "m", start: 1, end: 3 },
    ])),
    ["@小明"],
  );
});

test("提及是**唯一**由后端区间决定的东西：正文里的 @ 没有区间时不包标签", () => {
  const root = render("@小明 看这个", []);
  assert.deepEqual(mentions(root), [], "没有区间就没有 .mention");
  assert.equal(root.textContent, "@小明 看这个", "但字符照常呈现");
});

/* ─── 机械守卫：不用 innerHTML ─────────────────────────────────────────────── */

test("渲染器与 app.js 都不含 innerHTML（模板与数据分离的机械守卫）", () => {
  // 这条纪律此前只被 `agent_capabilities_ui.test.cjs` 的技能正文用例覆盖；
  // 本票新增了一个会大量产出 DOM 的模块，因此把守卫扩到它身上。
  for (const file of ["markdown.js", "app.js"]) {
    assert.equal(
      stripComments(readStudio(file)).includes("innerHTML"),
      false,
      `${file} 不该有 innerHTML：那是 HTML 注入面`,
    );
  }
});

test("渲染器不碰工作台状态、不取词条、不发请求（纯函数模块的边界）", () => {
  const source = stripComments(readStudio("markdown.js"));
  // 这三样是 app.js 的职责，渲染器一旦引用它们就不再能被单独测。
  // 用带边界的模式匹配：`t('` 会误命中注释与反引号里的 `t()`，因此要求它前面不是标识符字符。
  for (const [pattern, why] of [
    [/\bstate\./, "不该读 state"],
    [/(^|[^A-Za-z0-9_])t\(['"]/, "不该取词条"],
    [/\bfetch\(/, "不该发请求"],
    [/localStorage/, "不该用浏览器存储"],
  ]) {
    assert.equal(pattern.test(source), false, `markdown.js ${why}（命中：${pattern}）`);
  }
});

test("index.html 在 app.js 之前加载 markdown.js", () => {
  const html = readStudio("index.html");
  const markdownAt = html.indexOf('src="./markdown.js"');
  const appAt = html.indexOf('src="./app.js"');
  assert.ok(markdownAt >= 0, "index.html 引用了 markdown.js");
  assert.ok(appAt >= 0, "index.html 引用了 app.js");
  assert.ok(markdownAt < appAt, "markdown.js 必须先于 app.js 加载（后者调用前者）");
});
