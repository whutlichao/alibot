/* HTTP 客户端（业务状态的唯一读写通道）

   与旧实现（`studio/app.js` 的 `api()` / `readError()`）逐字同义：同一个 `fetch` 形状、
   同一套错误映射、同一个 `error.status` 判据（发送路径靠它区分「后端拒绝」与「后端不可达」）。
   把它搬出来是为了让 Vue 侧只有**一个**发请求的落点，不在组件里各写一遍 fetch。

   错误文案仍按词条取值（`t()`）：后端给的 `message` 是后端那一层的语言，前端只补 HTTP 层的兜底。 */
import { t } from '../i18n/index.js';

/** 把后端的错误载荷翻成一句可读的话（顺序即优先级）。 */
export function readError(payload, response) {
  if (payload && payload.detail && typeof payload.detail === 'string') return payload.detail;
  if (payload && payload.detail && Array.isArray(payload.detail)) {
    return payload.detail.map((item) => item.msg || t('error.badRequestBody')).join(t('common.clauseSep'));
  }
  if (payload && payload.error) {
    const fields = payload.error.fields || {};
    const details = Object.keys(fields)
      .map((key) => String(fields[key]))
      // 字段级文案常与总文案重合，别把同一句说两遍。
      .filter((detail) => detail && !String(payload.error.message).includes(detail));
    return details.length
      ? payload.error.message + t('common.colonSep') + details.join(t('common.clauseSep'))
      : payload.error.message;
  }
  return t('error.httpStatus', { status: response.status });
}

/** 一次 JSON 请求。非 2xx 一律抛 `Error`（带 `status` / `payload`），调用方不必自己判状态码。 */
export async function api(path, options) {
  const config = options || {};
  const response = await fetch(path, {
    method: config.method || 'GET',
    headers: config.body ? { 'Content-Type': 'application/json' } : undefined,
    body: config.body ? JSON.stringify(config.body) : undefined,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null; // 非 JSON 响应（例如代理错误页）：正文留给下面的错误映射。
  }
  if (!response.ok) {
    const error = new Error(readError(payload, response));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

/** 三种 target 各一套路径拼法。`isDm` 由调用方给（正本是 store 里的 target 表），
    这里不读全局状态——路径拼法只做字符串，不做归属判定。 */
export function channelPath(channelId, suffix) {
  return '/api/channels/' + encodeURIComponent(channelId) + (suffix || '');
}

export function dmPath(dmId, suffix) {
  return '/api/dms/' + encodeURIComponent(dmId) + (suffix || '');
}

export function taskPath(taskId, suffix) {
  return '/api/tasks/' + encodeURIComponent(taskId) + (suffix || '');
}

export function targetPath(targetId, isDm, suffix) {
  return isDm ? dmPath(targetId, suffix) : channelPath(targetId, suffix);
}
