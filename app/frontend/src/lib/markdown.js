/* Markdown 渲染器的桥（ADR-0037 / #290）

   **唯一实现仍是 `app/alibot/studio/markdown.js`**（classic script，纯函数模块：输入正文与提及
   区间、输出 DOM 节点，不碰状态、不调 `t()`、不发请求）。Vue 版不重写渲染器，只把它包一层：

   - 保住 ADR-0037 的呈现契约（块级/行内子集、链接协议白名单、图片不加载、HTML 当字面文本）；
   - 保住「不用 innerHTML」的纪律：产物是节点树，Vue 侧用 `appendChild` 挂上去，不做字符串拼接。

   为什么不在 Vue 侧 import 那份文件：它顶层是 `function renderMarkdown(...)` 的 classic script，
   加 `export` 会让旧页面的 `<script src>` 变成语法错误。两条页面共用同一个文件，是
   「渲染器只有一份」的前提（与 `i18n/index.js` 同一个理由）。 */

/** 渲染入口。缺失时抛一条能定位的错，不静默退化成纯文本（那会让 Markdown 悄悄失效）。 */
export function renderMarkdown(body, spans) {
  const fn = globalThis.renderMarkdown;
  if (typeof fn !== 'function') {
    throw new Error('renderMarkdown 未加载：v2 页面必须在模块脚本之前引入 /studio/markdown.js');
  }
  return fn(body, spans);
}

/** 消息的提及区间：只认「有 start/end 的成员快照」，按起点排序。

    这是后端在**同一事务**里随消息落库的解析结果（`mention_snapshot`），区间口径是 UTF-16
    下标（#291 之后）。前端不重算、不预测——「是不是提及」只有后端一个判定落点。 */
export function mentionSpans(message) {
  const snapshot = Array.isArray(message.mention_snapshot) ? message.mention_snapshot : [];
  return snapshot
    .filter((span) => span && Number.isInteger(span.start) && Number.isInteger(span.end))
    .slice()
    .sort((a, b) => a.start - b.start);
}
