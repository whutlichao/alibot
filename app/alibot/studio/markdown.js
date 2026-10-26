/* ─── 消息正文的 Markdown 渲染器（#290 / ADR-0037）──────────────────────────

   一个**纯函数模块**：输入正文与提及区间，输出 DOM 节点。它不碰 `state`、不调 `t()`、
   不发请求、不读 `document` 以外的任何全局——因此可以被单独喂输入看输出。

   为什么自己写而不是引 marked / markdown-it（ADR-0037 §二）：
   1. 成熟库的公开 API 是 `parse(src) -> html string`。本仓的 DOM 纪律是「一律 createElement /
      textContent 构建，不用 innerHTML」（app.js 顶部，且 `agent_capabilities_ui.test.cjs` 把它
      断言成契约）。要塞 HTML 字符串进 DOM 就得 innerHTML，要么破纪律，要么再引一个净化库。
   2. 渲染是**呈现**不是**判定**。后端已经把「哪里是提及」判完了（`mention_snapshot`），
      前端只按区间包标签。库的树结构不为这件事设计，接上去要写一层适配，比自写更绕。

   **offset 是本模块的核心难点**：提及区间是相对**原始正文**的 UTF-16 下标，而行内记号会消费掉
   `**` / `` ` `` 这些字符。因此切分时每个片段都带着自己在原串里的起点（`start`），一路传到
   文本节点，映射时做 `绝对位置 = 片段起点 + 段内偏移`。不做这件事就等于在渲染后的树里重新找一遍
   提及，那是前端判定——被 ADR-0037 §三否掉的路。

   **不支持的**（刻意，不是漏）：图片、HTML 内联标签、脚注、任务列表、数学公式、自动链接、语法高亮。
   图片不加载的理由是它会让正文变成数据外流通道（ADR-0037 §五）；HTML 内联标签一律当字面文本，
   是把「不用 innerHTML」的纪律贯彻到数据面。

   块级元素：段落、标题（#~######）、围栏代码块、无序/有序列表、引用、分隔线、GFM 表格。
   行内元素：粗体、斜体、删除线、行内代码、链接（协议白名单）。
   单换行按 GFM 口径保留为 `<br>`（不并成空格）——否则今天能看的东西明天看不出来。 */

/* 链接只放行这三种协议。`javascript:` / `data:` / `file:` / `vbscript:` 一律降级成纯文本：
   一旦进 `href` 就是注入面，这不是「小心一点」能兜住的，得在渲染器里做成一档显式判定。 */
const MARKDOWN_SAFE_LINK_PROTOCOLS = ['http:', 'https:', 'mailto:'];

/* 块级记号：遇到这些开头的行，上一段就结束。 */
const MD_FENCE = /^( {0,3})(`{3,}|~{3,})(.*)$/;
const MD_HEADING = /^( {0,3})(#{1,6})([ \t]+)(.*)$/;
const MD_HR = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const MD_UL_ITEM = /^( {0,3})([-*+])([ \t]+)(.*)$/;
const MD_OL_ITEM = /^( {0,3})(\d{1,9})([.)])([ \t]+)(.*)$/;
const MD_BLOCKQUOTE = /^ {0,3}>[ \t]?(.*)$/;
/* GFM 表格的分隔行：`| --- | :---: |` 这类。第二行是这个形状才把上一行当表头。 */
const MD_TABLE_DELIM = /^ {0,3}\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$/;

/** 渲染器入口：正文 + 提及区间 → 节点。

    `spans` 是后端给的 `mention_snapshot`，`start` / `end` 是 **UTF-16 code unit** 下标
    （#291 之后的口径），因此可以直接在 JS 字符串上做偏移换算。

    返回的根节点是 `div.markdown-body`——设计系统的规则挂在 `.msg-text > .markdown-body` 下
    （`ui_kits/app/app.css:198`），多这一层换来的是产品样式与 kit 同形。 */
function renderMarkdown(body, spans) {
  const text = String(body == null ? '' : body);
  const root = document.createElement('div');
  root.className = 'markdown-body';
  const mentions = normalizeMentionSpans(spans, text.length);
  const blocks = collectBlocks(splitLines(text));
  blocks.forEach((block) => {
    const node = renderBlock(block, mentions);
    if (node) root.appendChild(node);
  });
  // 整条正文都是空白时给一个空段落，避免出现「消息行里什么都没有」。
  if (!root.children.length) root.appendChild(document.createElement('p'));
  return root;
}

/** 切成 `[{text, start}]`：`start` 是该行在**原始正文**里的起点。

    行尾的 `\r` 去掉（Windows 换行），但 `start` 仍按原串算——否则 CRLF 正文的提及会整体偏。 */
function splitLines(text) {
  const lines = [];
  let start = 0;
  while (start <= text.length) {
    const next = text.indexOf('\n', start);
    const end = next < 0 ? text.length : next;
    let line = text.slice(start, end);
    if (line.endsWith('\r')) line = line.slice(0, -1);
    lines.push({ text: line, start });
    if (next < 0) break;
    start = next + 1;
  }
  return lines;
}

/** 只认「有 start / end 的整数区间」，并按起点排序、丢掉越界与重叠。

    后端在同一个事务里写正文与快照，理论上不会不一致；不一致时**退化为纯文本**（不包标签），
    而不是切错字符——这与 `messageText()` 此前的守卫同一纪律。 */
function normalizeMentionSpans(spans, length) {
  const list = Array.isArray(spans) ? spans : [];
  const cleaned = [];
  list
    .filter((span) => span && Number.isInteger(span.start) && Number.isInteger(span.end))
    .slice()
    .sort((a, b) => a.start - b.start)
    .forEach((span) => {
      if (span.start < 0 || span.end > length || span.end <= span.start) return;
      const previous = cleaned[cleaned.length - 1];
      if (previous && span.start < previous.end) return;
      cleaned.push({ start: span.start, end: span.end, member_id: span.member_id });
    });
  return cleaned;
}

/* ─── 块级切分 ─────────────────────────────────────────────────────────────── */

/** 把正文切成块。每个块带着自己内容在原始正文里的起点，供行内解析做 offset 换算。

    切分只做「哪几行属于同一块」，块内怎么排由 `renderBlock` 决定——两件事分开，
    改行内语法时不用碰切分。 */
function collectBlocks(lines) {
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];

    // 空行：块分隔符，本身不产出节点。
    if (!line.text.trim()) {
      index += 1;
      continue;
    }

    const fence = MD_FENCE.exec(line.text);
    if (fence) {
      const indent = fence[1].length;
      const marker = fence[2];
      const info = fence[3].trim();
      const body = [];
      index += 1;
      // 围栏块一直吃到**同种**围栏为止；没闭合就到文末（用户还在写的情况不吞掉内容）。
      while (index < lines.length) {
        const candidate = MD_FENCE.exec(lines[index].text);
        if (candidate && candidate[2][0] === marker[0] && candidate[2].length >= marker.length) {
          index += 1;
          break;
        }
        // 去掉与开头围栏同宽的缩进（CommonMark：围栏块的缩进不进入内容）。
        body.push(stripIndent(lines[index].text, indent));
        index += 1;
      }
      // 代码块的内容**一律是字面文本**：不解析行内记号，也不做语法高亮（ADR-0037 §四）。
      blocks.push({ kind: 'code', info, text: body.join('\n') });
      continue;
    }

    const heading = MD_HEADING.exec(line.text);
    if (heading) {
      blocks.push({
        kind: 'heading',
        level: heading[2].length,
        // 标题的正文起点 = 行起点 + 已消费的前缀长度（缩进 + `#` + 空白）。
        content: { text: heading[4], start: line.start + heading[0].length - heading[4].length },
      });
      index += 1;
      continue;
    }

    if (MD_HR.test(line.text)) {
      blocks.push({ kind: 'hr' });
      index += 1;
      continue;
    }

    if (MD_BLOCKQUOTE.test(line.text)) {
      const inner = [];
      while (index < lines.length) {
        const quoted = MD_BLOCKQUOTE.exec(lines[index].text);
        if (!quoted) break;
        const consumed = lines[index].text.length - quoted[1].length;
        inner.push({ text: quoted[1], start: lines[index].start + consumed });
        index += 1;
      }
      // 递归切块：引用里可以再套列表、代码块、表格，因此走同一条切分路径而不是自己拼段落。
      // `> a` / `> b` 两行属于**同一段**（CommonMark 的 lazy continuation），按 GFM 口径
      // 在段内插 `<br>`——这与段落外的单换行是同一个规矩。
      blocks.push({ kind: 'blockquote', blocks: collectBlocks(inner) });
      continue;
    }

    // 表格：**上一行是表头**，这一行是分隔行。因此判定发生在看到分隔行时，回头把上一行拿过来。
    if (
      index + 1 < lines.length &&
      MD_TABLE_DELIM.test(lines[index + 1].text) &&
      line.text.includes('|')
    ) {
      const header = splitTableRow(line);
      const aligns = splitTableRow(lines[index + 1]).map((cell) => tableAlignOf(cell.text));
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].text.includes('|') && lines[index].text.trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push({ kind: 'table', header, aligns, rows });
      continue;
    }

    if (MD_UL_ITEM.test(line.text)) {
      const result = collectList(lines, index, 'ul');
      blocks.push(result.block);
      index = result.next;
      continue;
    }

    if (MD_OL_ITEM.test(line.text)) {
      const result = collectList(lines, index, 'ol');
      blocks.push(result.block);
      index = result.next;
      continue;
    }

    // 段落：一直吃到空行或另一个块级记号的开头。
    const paragraph = [];
    while (index < lines.length) {
      const current = lines[index];
      if (!current.text.trim()) break;
      if (paragraph.length && startsBlock(current.text, lines[index + 1] && lines[index + 1].text)) break;
      paragraph.push(current);
      index += 1;
    }
    blocks.push({ kind: 'paragraph', lines: paragraph });
  }
  return blocks;
}

/** 这一行是否开启一个新块（段落收集时用它判断「该停了」）。 */
function startsBlock(line, next) {
  if (MD_FENCE.test(line) || MD_HEADING.test(line) || MD_HR.test(line)) return true;
  if (MD_BLOCKQUOTE.test(line) || MD_UL_ITEM.test(line) || MD_OL_ITEM.test(line)) return true;
  if (next !== undefined && MD_TABLE_DELIM.test(next) && line.includes('|')) return true;
  return false;
}

/** 收集一段列表。只支持**单层**：缩进的子项按同一个列表的项处理（不建嵌套树）。

    为什么不做嵌套：Agent 回复里的列表几乎都是一层；嵌套要处理「缩进多少算一层」「有序/无序混排」
    这些规则，而收益只在一小部分消息上。宁可把子项拍平，也不做一套半吊子的嵌套。 */
function collectList(lines, start, kind) {
  const items = [];
  let index = start;
  while (index < lines.length) {
    const match = kind === 'ul' ? MD_UL_ITEM.exec(lines[index].text) : MD_OL_ITEM.exec(lines[index].text);
    if (!match) break;
    const content = kind === 'ul' ? match[4] : match[5];
    const item = {
      // 列表项的正文起点 = 行起点 + 已消费的前缀长度。
      parts: [{ text: content, start: lines[index].start + lines[index].text.length - content.length }],
    };
    items.push(item);
    index += 1;
    // 列表项的续行：缩进的非空行仍属于上一项（作为该项的另一行，渲染时插 `<br>`）。
    while (index < lines.length && lines[index].text.trim() && /^ {2,}/.test(lines[index].text)) {
      if (MD_UL_ITEM.test(lines[index].text) || MD_OL_ITEM.test(lines[index].text)) break;
      const trimmed = lines[index].text.trim();
      item.parts.push({
        text: trimmed,
        start: lines[index].start + lines[index].text.indexOf(trimmed),
      });
      index += 1;
    }
  }
  return { block: { kind, items }, next: index };
}

function stripIndent(line, width) {
  let index = 0;
  while (index < width && line[index] === ' ') index += 1;
  return line.slice(index);
}

/** 表格行切成单元格，每格带自己在**原始正文**里的起点。
    首尾的 `|` 不算单元格；`\|` 是转义的竖线。 */
function splitTableRow(line) {
  const source = line.text;
  let text = source.trim();
  // `trim()` 会挪动起点：算回被吃掉的左侧空白数，再加上整行在正文里的起点。
  let offset = line.start + (source.length - source.trimStart().length);
  if (text.startsWith('|')) {
    text = text.slice(1);
    offset += 1;
  }
  if (text.endsWith('|') && !text.endsWith('\\|')) text = text.slice(0, -1);
  const cells = [];
  let current = '';
  let cellStart = 0;
  let cursor = 0;
  for (; cursor < text.length; cursor += 1) {
    const char = text[cursor];
    if (char === '\\' && text[cursor + 1] === '|') {
      if (!current) cellStart = cursor;
      current += '|';
      cursor += 1;
      continue;
    }
    if (char === '|') {
      cells.push(cellNode(current, offset + cellStart));
      current = '';
      cellStart = cursor + 1;
      continue;
    }
    if (!current) cellStart = cursor;
    current += char;
  }
  cells.push(cellNode(current, offset + cellStart));
  return cells;
}

/** 一个单元格：去掉首尾空白，但起点跟着挪，好让提及区间落在正确位置。 */
function cellNode(raw, start) {
  const text = raw.trim();
  const lead = raw.length - raw.trimStart().length;
  return { text, start: start + lead };
}

function tableAlignOf(text) {
  const trimmed = text.trim();
  const left = trimmed.startsWith(':');
  const right = trimmed.endsWith(':');
  if (left && right) return 'center';
  if (right) return 'right';
  if (left) return 'left';
  return '';
}

/* ─── 块级渲染 ─────────────────────────────────────────────────────────────── */

/** 一个块 → 一个节点。`mentions` 一路传下去，供行内解析做 offset 换算。 */
function renderBlock(block, mentions) {
  if (block.kind === 'hr') return document.createElement('hr');

  if (block.kind === 'heading') {
    // 标题**映射到正文档**而不是 32px 的 h1：一条消息里出现大标题会压过作者名，
    // 破坏消息行的层级。六级标题因此只差字重与字号，见 app.css。
    const node = document.createElement('h' + Math.min(6, Math.max(1, block.level)));
    node.className = 'md-heading md-h' + block.level;
    appendInlineSequence(node, [block.content], mentions);
    return node;
  }

  if (block.kind === 'code') {
    const pre = document.createElement('pre');
    pre.className = 'md-code';
    const code = document.createElement('code');
    if (block.info) code.dataset.lang = block.info;
    code.textContent = block.text;
    pre.appendChild(code);
    return pre;
  }

  if (block.kind === 'paragraph') {
    const node = document.createElement('p');
    appendInlineSequence(node, block.lines, mentions);
    return node;
  }

  if (block.kind === 'blockquote') {
    const node = document.createElement('blockquote');
    node.className = 'md-quote';
    block.blocks.forEach((inner) => {
      const child = renderBlock(inner, mentions);
      if (child) node.appendChild(child);
    });
    return node;
  }

  if (block.kind === 'table') return renderTable(block, mentions);

  if (block.kind === 'ul' || block.kind === 'ol') {
    const list = document.createElement(block.kind);
    block.items.forEach((item) => {
      const li = document.createElement('li');
      appendInlineSequence(li, item.parts, mentions);
      list.appendChild(li);
    });
    return list;
  }

  return null;
}

function renderTable(block, mentions) {
  // 表格外面套一个横向滚动容器，并**突破 68ch**：表格的可读性依赖列对齐，硬塞进 68ch 会挤成一团。
  // 这是 `.msg-text` 那条 68ch 规矩的唯一例外（ADR-0037 §四）。
  const wrapper = document.createElement('div');
  wrapper.className = 'md-table-wrap';
  const table = document.createElement('table');
  table.className = 'md-table';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  block.header.forEach((cell, column) => {
    const th = document.createElement('th');
    applyAlign(th, block.aligns[column]);
    appendInlineSequence(th, [cell], mentions);
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  if (block.rows.length) {
    const tbody = document.createElement('tbody');
    block.rows.forEach((row) => {
      const tr = document.createElement('tr');
      // 列数以表头为准：多出来的单元格丢掉，少的补空——不因为一行不齐就把整张表降级成文本。
      block.header.forEach((_head, column) => {
        const td = document.createElement('td');
        applyAlign(td, block.aligns[column]);
        appendInlineSequence(td, [row[column] || { text: '', start: 0 }], mentions);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
  }

  wrapper.appendChild(table);
  return wrapper;
}

function applyAlign(node, align) {
  if (align) node.style.textAlign = align;
}

/* ─── 行内解析 ─────────────────────────────────────────────────────────────── */

/** 多段行内内容之间按 GFM 口径插 `<br>`（单换行不并成空格）。

    每段分别做行内解析，是因为行内记号与提及区间都不跨行；这样每段的 offset 基准就是
    `part.start`，映射时不必处理跨行区间。 */
function appendInlineSequence(node, parts, mentions) {
  parts.forEach((part, index) => {
    if (index > 0) node.appendChild(document.createElement('br'));
    appendInline(node, part.text, part.start, mentions);
  });
}

/** 单段文本 → 行内节点序列。

    `base` 是这段文本在**原始正文**里的起点：行内记号会消费掉 `**` 这类字符，但文本节点必须
    记住自己在原串里的位置，提及区间才能投上去。 */
function appendInline(node, text, base, mentions) {
  appendTokens(node, tokenizeInline(text, base), mentions);
}

/** token 列表 → 节点序列。**递归**处理任意深度的嵌套（`**粗 *斜* 体**`、链接里再套强调）。

    每个 token 的 `start` 都是**绝对**原串下标（`tokenizeInline` 按 base 换算过），因此这里
    不再做任何偏移运算——偏移只在一个地方算，出错时也只有一处要查。 */
function appendTokens(node, tokens, mentions) {
  tokens.forEach((token) => {
    if (token.kind === 'text') {
      appendTextWithMentions(node, token.text, token.start, mentions);
      return;
    }
    if (token.kind === 'code') {
      // 行内代码：内容一律字面文本，但里面的 `@某人` **仍是提及**（ADR-0037 §三：
      // `**@某人**` 是加粗的提及、`` `@某人` `` 是行内代码样式的提及）。
      const code = document.createElement('code');
      appendTextWithMentions(code, token.text, token.start, mentions);
      node.appendChild(code);
      return;
    }
    const wrapper = document.createElement(token.tag);
    if (token.tag === 'a') {
      wrapper.setAttribute('href', token.href);
      wrapper.setAttribute('target', '_blank');
      wrapper.setAttribute('rel', 'noopener noreferrer');
    }
    // 强调与链接都可以嵌套：内层走同一条路径，深度不限。
    appendTokens(wrapper, token.children, mentions);
    node.appendChild(wrapper);
  });
}

/** 把一段字面文本按提及区间切开：落在区间里的那截包 `span.mention`，其余是纯文本节点。

    **只按后端给的区间包标签**，不在这里判定「这是不是提及」——ADR-0037 §三。
    `start` 是本段文本在原串里的**绝对**起点（`tokenizeInline` 已按 base 换算），
    因此这里直接用绝对下标比对区间，不再做任何偏移运算。 */
function appendTextWithMentions(node, text, start, mentions) {
  const from0 = start;
  const to0 = start + text.length;
  let cursor = from0;
  mentions.forEach((span) => {
    if (span.end <= cursor || span.start >= to0) return;
    const from = Math.max(span.start, cursor);
    const to = Math.min(span.end, to0);
    if (from > cursor) node.appendChild(document.createTextNode(text.slice(cursor - from0, from - from0)));
    const mention = document.createElement('span');
    mention.className = 'mention';
    mention.dataset.member = span.member_id;
    mention.textContent = text.slice(from - from0, to - from0);
    node.appendChild(mention);
    cursor = to;
  });
  if (cursor < to0) node.appendChild(document.createTextNode(text.slice(cursor - from0)));
}

/** 行内记号切分。返回 `[{kind, start, text, ...}]`，`start` 是**绝对**原串下标。

    递归下降：先找 code / 链接 / 强调，找到就把外层剥掉、内层带着新基准再切一遍。刻意不做完整的
    CommonMark 优先级处理（那需要一整套 delimiter stack），只覆盖消息里真会出现的写法。 */
function tokenizeInline(text, base) {
  const tokens = [];
  let index = 0;
  let buffer = '';
  let bufferStart = 0;

  const flush = () => {
    if (!buffer) return;
    tokens.push({ kind: 'text', text: buffer, start: base + bufferStart });
    buffer = '';
  };

  while (index < text.length) {
    const char = text[index];

    // 反斜杠转义：`\*` 是字面星号。
    if (char === '\\' && index + 1 < text.length && isMarkdownPunctuation(text[index + 1])) {
      if (!buffer) bufferStart = index;
      buffer += text[index + 1];
      index += 2;
      continue;
    }

    // 行内代码：`` `x` `` 或 ```` ``x`` ````（反引号个数要配对）。
    if (char === '`') {
      const run = backtickRun(text, index);
      const close = findBacktickRun(text, index + run, run);
      if (close >= 0) {
        flush();
        tokens.push({
          kind: 'code',
          // 去掉内容首尾空白后，起点要跟着挪（`` ` @小明 ` `` 的提及仍在原位）。
          ...trimmedWithStart(text.slice(index + run, close), base + index + run),
        });
        index = close + run;
        continue;
      }
      // 没闭合：反引号按字面留下（用户还在写的情况不吞字符）。
      if (!buffer) bufferStart = index;
      buffer += char;
      index += 1;
      continue;
    }

    // 图片 `![alt](url)`：**不加载**（ADR-0037 §五：会让正文变成数据外流通道）。
    // 判定必须**早于**链接——否则 `!` 会留在文本里、`[alt](url)` 被当成可点链接，
    // 变成「一半是图片语法、一半是可点链接」的四不像。整段原样保留。
    if (char === '!' && text[index + 1] === '[') {
      const image = matchLink(text, index + 1);
      if (image) {
        flush();
        tokens.push({ kind: 'text', text: '!' + image.raw, start: base + index });
        index = image.end;
        continue;
      }
    }

    // 链接 `[文字](url)`：文字部分继续切行内记号，url 走协议白名单。
    if (char === '[') {
      const link = matchLink(text, index);
      if (link) {
        flush();
        const href = sanitizeLink(link.url);
        if (href === null) {
          // 协议不安全 ⇒ **整段降级成纯文本**，不产出 `<a>`，也不丢掉原文。
          tokens.push({ kind: 'text', text: link.raw, start: base + index });
        } else {
          tokens.push({
            kind: 'link',
            tag: 'a',
            href,
            children: tokenizeInline(link.label, base + link.labelStart),
            start: base + index,
          });
        }
        index = link.end;
        continue;
      }
    }

    // 强调：`**粗**` / `__粗__` / `*斜*` / `_斜_` / `~~删~~`。
    const emphasis = matchEmphasis(text, index);
    if (emphasis) {
      flush();
      tokens.push({
        kind: emphasis.kind,
        tag: emphasis.tag,
        children: tokenizeInline(emphasis.text, base + emphasis.contentStart),
        start: base + index,
      });
      index = emphasis.end;
      continue;
    }

    if (!buffer) bufferStart = index;
    buffer += char;
    index += 1;
  }
  flush();
  return tokens;
}

/** 去掉首尾空白，同时给出「去完之后」在原串里的起点。 */
function trimmedWithStart(raw, start) {
  const text = raw.trim();
  const lead = raw.length - raw.trimStart().length;
  return { text, start: start + lead };
}

function isMarkdownPunctuation(char) {
  return '\\`*_{}[]()#+-.!|<>~'.includes(char);
}

function backtickRun(text, index) {
  let count = 0;
  while (text[index + count] === '`') count += 1;
  return count;
}

function findBacktickRun(text, from, length) {
  let index = from;
  while (index < text.length) {
    if (text[index] === '`') {
      const run = backtickRun(text, index);
      if (run === length) return index;
      index += run;
      continue;
    }
    index += 1;
  }
  return -1;
}

/** `[label](url)`；`url` 可带尖括号 `<...>`。返回 `{label, labelStart, url, raw, end}` 或 null。 */
function matchLink(text, start) {
  if (text[start] !== '[') return null;
  let depth = 0;
  let index = start;
  for (; index < text.length; index += 1) {
    const char = text[index];
    if (char === '\\') {
      index += 1;
      continue;
    }
    if (char === '[') depth += 1;
    if (char === ']') {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  if (index >= text.length || text[index] !== ']') return null;
  const labelEnd = index;
  if (text[labelEnd + 1] !== '(') return null;
  let cursor = labelEnd + 2;
  while (cursor < text.length && text[cursor] === ' ') cursor += 1;
  let url = '';
  if (text[cursor] === '<') {
    const close = text.indexOf('>', cursor + 1);
    if (close < 0) return null;
    url = text.slice(cursor + 1, close);
    cursor = close + 1;
  } else {
    let paren = 0;
    while (cursor < text.length) {
      const char = text[cursor];
      if (char === '\\') {
        url += text[cursor + 1] || '';
        cursor += 2;
        continue;
      }
      if (char === '(') paren += 1;
      if (char === ')') {
        if (paren === 0) break;
        paren -= 1;
      }
      if (char === ' ' || char === '\t') break;
      url += char;
      cursor += 1;
    }
  }
  while (cursor < text.length && text[cursor] === ' ') cursor += 1;
  if (text[cursor] !== ')') return null;
  return {
    label: text.slice(start + 1, labelEnd),
    labelStart: start + 1,
    url,
    raw: text.slice(start, cursor + 1),
    end: cursor + 1,
  };
}

/** 链接协议白名单。返回可用的 href，不安全时返回 null（调用方降级成纯文本）。 */
function sanitizeLink(url) {
  const text = String(url || '').trim();
  if (!text) return null;
  // 去掉控制字符与空白：`java\nscript:` 这类变形不该绕过判定。
  const cleaned = text.replace(/[\u0000-\u0020]/g, '');
  const lower = cleaned.toLowerCase();
  // `//host` 是协议相对地址，会继承当前页面的协议——不放行。
  if (lower.startsWith('//')) return null;
  const colon = lower.indexOf(':');
  // 无协议的相对地址（`./docs`、`#anchor`）也不放行：消息正文里的链接应当是可点走的外部地址，
  // 相对地址在本页面语境下没有意义。
  if (colon < 0) return null;
  const protocol = lower.slice(0, colon + 1);
  if (!MARKDOWN_SAFE_LINK_PROTOCOLS.includes(protocol)) return null;
  return cleaned;
}

/** `**粗**` / `__粗__` / `*斜*` / `_斜_` / `~~删~~`。
    返回 `{kind, tag, text, contentStart, end}`（下标相对**本段文本**）或 null。

    单字符强调要求**两侧不是字母数字**（`snake_case_name` 的 `_` 前面是字母、后面是字母，
    因此不当强调）。`2 * 3 * 4` 同理不会变成斜体。这是 Markdown 的固有歧义，这里取
    「宁可少渲染」的一侧——误渲染比漏渲染更难解释。 */
function matchEmphasis(text, start) {
  const char = text[start];
  if (char !== '*' && char !== '_' && char !== '~') return null;

  const double = text[start + 1] === char;
  if (char === '~' && !double) return null;
  const marker = double ? char + char : char;
  const contentStart = start + marker.length;
  if (contentStart >= text.length) return null;
  // 开标记右侧不能是空白（`** 粗**` 不是粗体）。
  if (/\s/.test(text[contentStart])) return null;
  // 单字符强调还要求左侧不是字母数字（`snake_case` 的 `_` 前面是字母）。
  if (!double && start > 0 && /[A-Za-z0-9]/.test(text[start - 1])) return null;

  let cursor = contentStart;
  while (cursor < text.length) {
    const found = text.indexOf(marker, cursor);
    if (found < 0) return null;
    const before = text[found - 1];
    const after = text[found + marker.length];
    // 闭标记左侧不能是空白；单字符强调还要求右侧不是字母数字（`case_name` 的 `_` 后面是字母）。
    const okBefore = before !== undefined && !/\s/.test(before);
    const okAfter = double ? true : after === undefined || !/[A-Za-z0-9]/.test(after);
    if (okBefore && okAfter && found > contentStart) {
      return {
        kind: char === '~' ? 'del' : double ? 'strong' : 'em',
        tag: char === '~' ? 'del' : double ? 'strong' : 'em',
        text: text.slice(contentStart, found),
        contentStart,
        end: found + marker.length,
      };
    }
    cursor = found + marker.length;
  }
  return null;
}
