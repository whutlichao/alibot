/* 共用装置：把**正本**的两个 classic script 装进测试环境

   `i18n.js` 与 `markdown.js` 住在 `app/alibot/studio/`（Vue 源码之外），它们顶层是 `var` /
   `function` 声明，靠 classic script 的全局语义挂到 `window` 上。测试里用**间接 eval** 复现同一件事：
   间接 eval 在全局作用域执行，`var` 因此落在 `globalThis` 上——与浏览器里 `<script src>` 的
   结果一致。不用 `import`：给那两份文件加 `export` 会让旧页面的 `<script src>` 变成语法错误。

   于是测试读的是**同一份**词条表与**同一份**渲染器，不是副本——「两份实现各自漂」这件事
   在构造上不存在。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// `tests/helpers/` → 上溯三级到 `app/`，再进 `alibot/studio`（正本所在）。
export const STUDIO_DIR = path.resolve(HERE, '../../../alibot/studio');

/** 读正本文件（测试断言源码纪律时也用它）。 */
export function readStudio(file) {
  return fs.readFileSync(path.join(STUDIO_DIR, file), 'utf8');
}

/** 在全局作用域执行一段 classic script（间接 eval：`var` 落在 globalThis 上）。 */
export function loadClassicScript(file) {
  const code = readStudio(file);
  // eslint-disable-next-line no-eval -- 见文件头：这是刻意复现 classic script 的全局语义
  (0, eval)(code);
  return code;
}

/** 一次性装好词条表与渲染器（多个用例文件共用；重复调用是幂等的）。 */
export function loadStudioGlobals() {
  if (!globalThis.AlibotI18n) loadClassicScript('i18n.js');
  if (typeof globalThis.renderMarkdown !== 'function') loadClassicScript('markdown.js');
  return { i18n: globalThis.AlibotI18n, renderMarkdown: globalThis.renderMarkdown };
}

/** 造一个 `fetch` 替身：按 URL 分派，未列出的 URL 返回 `{}`（读坏也不影响主流程）。 */
export function jsonResponse(payload, status) {
  return {
    ok: (status || 200) < 400,
    status: status || 200,
    json: async () => payload,
  };
}

export function createFetchStub(routes) {
  const calls = [];
  const stub = async (url, init) => {
    const request = init || {};
    calls.push({
      url: String(url),
      method: request.method || 'GET',
      body: request.body ? JSON.parse(request.body) : null,
    });
    const handler = routes[String(url)];
    if (handler) return handler(String(url), request);
    return jsonResponse({});
  };
  stub.calls = calls;
  stub.to = (fragment) => calls.filter((call) => call.url.includes(fragment));
  return stub;
}

/** 等一轮微任务 + 宏任务：让 fetch 链与 Vue 的下一拍 flush 都跑完。 */
export function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
