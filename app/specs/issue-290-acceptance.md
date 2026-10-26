# 验收取证：#290 消息正文渲染 Markdown

**票型**：实施票（前端渲染器 + 接入 + 样式 + 回归测试）｜**门禁**：G-impl
**Ticket**：[#290](https://github.com/whutlichao/alibot/issues/290)（验收正本 = 票面 AC）
**设计正本**：`docs/adr/0037-message-body-renders-markdown.md`
**前置**：[#291](https://github.com/whutlichao/alibot/issues/291) / PR #292（提及区间 UTF-16 口径，已合入）
**base**：`2b44844`（`origin/main`）｜**branch**：`issue-290-markdown-renderer`
**改动面**：`app/alibot/studio/markdown.js`（新，748 行）、`app/alibot/studio/app.js`（`messageText()` 改 8 行）、
`app/alibot/studio/index.html`（+1 `<script>`）、`app/alibot/studio/app.css`（`.msg-text` 去 `pre-wrap` + 渲染样式）、
`app/alibot/studio/product-tokens.css`（+2 token）、`alibot-design-system/ui_kits/app/product-ext.css`（孪生同步）、
`app/tests/markdown_render_ui.test.cjs`（新，40 条）、`app/tests/*.cjs`（20 个装置各 +1 行加载）。
**没动的面**：后端零改动 / schema / 端点 / `kind='system'` 的生成逻辑 / 消息 append-only / 搜索面 / 技能正文。

**正文口径（一句话判据）**：human / agent 的对话正文渲染成结构；`kind='system'` 的事件记录、旁白、
状态变更记录、搜索命中行、技能正文**都不渲染**。

---

## 1. 票面 AC → 落点 → 读数

### AC1 · 标题 / 粗体 / 斜体 / 删除线 / 行内代码 / 代码块 / 有序无序列表 / 引用 / 分隔线 / 链接 / 表格各自产出正确标签

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 块级 | `markdown.js: collectBlocks()` → `renderBlock()` | 用例「标题：#~###### 映射到 h1~h6」「围栏代码块」「无序列表与有序列表」「引用」「分隔线」「表格」 |
| 行内 | `markdown.js: tokenizeInline()` → `appendTokens()` | 用例「强调：**粗** / *斜* / ~~删~~」「行内代码」「强调可以嵌套」 |
| 真浏览器实测 | `GET /studio/` + 一条覆盖全语法的消息 | 20 种标签逐个数：`h2:1 p:4 strong:1 em:1 del:1 code:2 ul:1 li:4 ol:1 blockquote:1 pre:1 div:1 table:1 thead:1 tr:3 th:2 tbody:1 td:4 a:1 hr:1` |

### AC2 · 危险输入一律降级为纯文本：`<script>`、`<img onerror=>`、`javascript:` 链接、`data:` 链接

| 输入 | 判据 | 读数 |
| --- | --- | --- |
| `<script>alert(1)</script>` | 用例「HTML 内联标签一律当字面文本」 | `script`/`img`/`span`/`a`/`iframe`/`div` 在根之外 **0 命中**；`root.textContent === body`（逐字保留） |
| `[x](javascript:alert(1))` | 用例「javascript: 链接降级为纯文本」 | `<a>` 0 个；`textContent` 是原文 |
| `[x](data:...)` | 用例「data: 链接降级为纯文本」 | 同上 |
| `JavaScript:` / `java\tscript:` / 前导空白 / `vbscript:` / `file://` / `//host` | 用例「协议大小写与空白变形绕不过白名单」 | 六种变形各自 `<a>` 0 个 |
| 相对地址 `./docs` / `#anchor` / `/api/x` | 用例「相对地址不放行」 | 三种各自 `<a>` 0 个 |
| 真浏览器实测 | 上面那条消息 | `scriptTags: 0`、`hasLiteralScript: true`、`hasLiteralBadLink: true` |

白名单实现：`markdown.js: sanitizeLink()`——去掉控制字符与空白后取协议前缀，只放行
`http:` / `https:` / `mailto:`；`//host` 单独拒（协议相对地址会继承当前页面的协议）。

### AC3 · 提及映射正确：`**@某人**` 得加粗提及、`` `@某人` `` 得行内代码样式提及、含 emoji 的正文切点正确

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 先建树、再把区间投到文本节点 | `markdown.js: appendTextWithMentions()` | 用例「粗体里的提及」：`.mention` 在 `<strong>` 里面；「行内代码里的提及」：在 `<code>` 里面 |
| offset 穿过被消费掉的记号 | `tokenizeInline(text, base)` 给每个 token 绝对 `start` | 用例「提及前面的行内记号被消费掉后，区间仍然对得上」：`**重点** 然后 @小明` 里 `**` 占 8 字符、渲染后只剩 2 字符，区间仍正确 |
| emoji | 同 #291 的 UTF-16 口径 | 用例「emoji 在提及之前」：`😀 @小明` 正确 |
| 六种容器 | 段落 / 列表项 / 表格单元格 / 引用 / 粗体 / 行内代码 | 各自一条用例 |
| 越界与重叠 | `normalizeMentionSpans()` | 用例「越界或重叠的区间退化为纯文本，不切错字符」 |
| **前端不判定** | 没有区间就不包标签 | 用例「正文里的 @ 没有区间时不包标签」：`.mention` 0 个，但字符照常呈现 |

### AC4 · 单换行 → `<br>`（GFM 口径）；段落间距由 `<p>` 承担

用例「单换行按 GFM 口径保留为 `<br>`」（段内 1 个 `<br>`）、「空行分隔段落」（两个 `<p>`）。
`.msg-text` 的 `white-space: pre-wrap` **已移除**（`app.css`），真浏览器实测 `whiteSpace: "normal"`。

### AC5 · 系统事件消息 / 旁白 / 状态变更记录 / 搜索结果 / 技能正文**都不含**渲染后的标签

| 面 | 落点 | 判据 |
| --- | --- | --- |
| 系统事件消息 | `app.js:8891` `el('div','msg-text', message.body)` | 走 `el()` 的 `textContent`，**不调** `messageText()` |
| 旁白 | `app.js:8842` `.msg-narration-text` | 同上；`narration_ui.test.cjs:414` 断言 `msg-text` 为空数组 |
| 状态变更记录 | `app.js:1967` `ttHistory` 拼字符串 | 唯一一处「作者名 + 正文」拼接，未改 |
| 搜索命中行 | `app.js:539` `searchHitText()` | 只按 `highlight_ranges` 包 `<mark>`，未改 |
| 技能正文 | `app.js: skillDetailBody()` | `<pre>` 纯文本，未改；`agent_capabilities_ui.test.cjs:1517-1540` 的八标签 0 命中断言照旧全绿 |

`search_ui.test.cjs` / `narration_ui.test.cjs` / `agent_capabilities_ui.test.cjs` 在 §3 的回归里全绿。

### AC6 · 新增 `markdown_render_ui.test.cjs`；`app.js` 与 `markdown.js` 均不含 `innerHTML`

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 新用例文件 | `app/tests/markdown_render_ui.test.cjs` | **40 条**，只装载 `markdown.js`（不装载 `app.js`） |
| `innerHTML` 机械守卫 | 用例「渲染器与 app.js 都不含 innerHTML」 | 两个文件都过 `stripComments` 后 `includes("innerHTML") === false` |
| 纯函数边界 | 用例「渲染器不碰工作台状态、不取词条、不发请求」 | 正则断言 `state.` / `t('` / `fetch(` / `localStorage` 均 0 命中 |
| 加载顺序 | 用例「index.html 在 app.js 之前加载 markdown.js」 | `indexOf` 比对 |

> 这条守卫此前**只**覆盖技能正文那一段（`agent_capabilities_ui.test.cjs:1539` 是唯一一处）。
> 本票新增了一个会大量产出 DOM 的模块，因此把它扩到 `markdown.js` + `app.js` 两个文件。

### AC7 · `scripts/check.sh` 全绿

见 §3。

---

## 2. 真浏览器实测（ego-browser，非假 DOM）

起服务：`PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --port 8931 --data-dir /tmp/alibot-md-demo`。

> **踩到的坑（值得记）**：本 worktree 的 `.venv` 是软链到主仓 `.venv` 的，直接
> `.venv/bin/python -m alibot` 会把 `alibot` 包解析到**主 worktree** 的代码——
> 于是 `markdown.js` 404、`app.js` 里没有 `renderMarkdown`，浏览器渲染出来还是纯文本。
> 必须显式 `PYTHONPATH=<当前 worktree>/app`。

一条覆盖全语法的消息（标题 / 粗斜删 / 行内代码 / 无序有序列表 / 引用 / 围栏代码块 / 表格 /
链接 / 分隔线 / 危险输入），实测读数：

```json
{
  "tags": { "h2": 1, "p": 4, "strong": 1, "em": 1, "del": 1, "code": 2, "ul": 1, "li": 4,
            "ol": 1, "blockquote": 1, "pre": 1, "div": 1, "table": 1, "thead": 1, "tr": 3,
            "th": 2, "tbody": 1, "td": 4, "a": 1, "hr": 1 },
  "strongText": ["加粗"], "emText": ["斜体"], "delText": ["删除线"], "h2Text": ["渲染验证"],
  "liCount": 4, "quoteText": "一段引用文字", "hrCount": 1,
  "preBg": "oklch(0.24 0.015 255)", "preFg": "oklch(0.94 0.01 250)", "preFontIsMono": true,
  "preText": "def hello(name):\n    return f\"hi {name}\"",
  "inlineCodeFontSize": "12px",
  "linkHref": "https://example.com", "linkRel": "noopener noreferrer", "linkTarget": "_blank",
  "scriptTags": 0, "imgTags": 0,
  "hasLiteralScript": true, "hasLiteralImage": true, "hasLiteralBadLink": true,
  "whiteSpace": "normal"
}
```

**视觉复查**：第一版标题字号（h2 = 15px）与正文（14px）几乎看不出区别，截图确认后调到
h1 17px / h2 16px / h3 15px / h4 14px / h5-h6 13px。复查读数 `h2FontSize: "16px"`、
`h2Weight: "650"`、`pFontSize: "14px"`、`authorFontSize: "13px"`——标题明显大于正文，
但仍小于「一条消息里出现大标题会压过作者名」的那条红线。

---

## 3. 门禁读数

| 门禁 | 命令 | 读数 |
| --- | --- | --- |
| ruff check / format | `scripts/check.sh --fast` | 全绿 |
| Studio DOM 用例族 | `node --test app/tests/*.test.cjs` | `tests 389 / pass 389 / fail 0`（349 既有 + 40 新增） |
| 全量 pytest | `pytest app/tests` | 见 PR 描述（CI 同跑） |

---

## 4. 20 个测试装置都要加载 `markdown.js`

`messageText()` 现在调用 `renderMarkdown()`，因此**每一个**用 `vm` 装载 `app.js` 的装置都必须
先装载 `markdown.js`，否则 `ReferenceError`。改了 20 个文件：

- 18 个多行写法（`vm.runInContext(fs.readFileSync(path.join(STUDIO_DIR, "i18n.js")...`）：脚本批量插入
- 2 个单行写法（`i18n_ui.test.cjs` ×2 处、`mobile_shell.test.cjs` ×1 处）：逐处插入
- `initial_scroll_ui.test.cjs` 复用 `approval_dom_harness.cjs`，跟着 harness 一起生效，不用单独改

这条成本是拆文件的代价，值得记下来：**新增 studio 脚本 = 20 个测试装置各加一行**。
它不改变「拆文件」这个决定——纯函数边界带来的可测性（40 条用例只装载一个文件、不碰状态与网络）
远大于这 20 行的机械成本。

---

## 5. 两处「用例写错」而非实现错（记录以免下次误判）

| 断言 | 误判原因 | 修法 |
| --- | --- | --- |
| HTML 标签 0 命中 | `div` 命中的是 `.markdown-body` **根节点自己**（容器的形状，不是解析出来的标签） | 断言改为「根之外」的节点 |
| 不引用 `t('` | `stripComments` 不去行内反引号，注释里的 `` `t()` `` 被当成代码 | 改用带边界的正则 `(^\|[^A-Za-z0-9_])t\(['"]` |

---

## 6. 与 ADR-0037 的两处落地差异

| ADR 写的 | 实际做的 | 为什么 |
| --- | --- | --- |
| 「表格突破 68ch」 | 做了 `width: max-content` + 横向滚动容器 | 一致。实测这条消息的表格宽 64px（内容少），`wrapScrolls: false`——内容少时不产生滚动条，是正确行为 |
| 「代码块照抄 kit 深底」 | 一致，但把 kit 里两个**裸 oklch** 收成了 `--code-bg` / `--code-fg` 两个 token | kit 的 `ui_kits/app/app.css:201` 用的是裸值，与 SKILL.md「Never hard-code a hex, rgb, hsl or named color」有张力。收成名字后，产品侧按名消费，也符合 `product-tokens.css` 的定位 |

孪生文件 `alibot-design-system/ui_kits/app/product-ext.css` 同步加了同样两个 token（该包的契约写明
「两份文件的四个块逐字一致」，见其文件头）。
