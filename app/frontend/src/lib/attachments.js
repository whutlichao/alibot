/* 附件的**前端镜像**：单文件上限与两步上传的第一步（ADR-0014）

   单文件上限的正本在**后端**：`app/alibot/db.py:325`
   `MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024`（10 MiB，不是 50 MB）。前端拿同一个数字做预检，
   是为了让「超限」在**不发请求**的情况下就能说清；数值本身仍只有后端一处权威——
   `tests/attachments.test.js` 有一条用例读那份正本比较，两份数字各自漂会当场红。

   上传（`uploadAttachment`）的请求形状就是后端既有端点的形状：请求体是文件的**原始字节**、
   MIME 在 `Content-Type`、文件名在 query 的 `name` 上（`app/alibot/api.py:908-932`，
   **不是** multipart）。绑定额外一步：拿到标识后随消息一次性提交 `attachment_ids`，
   后端在同一事务里完成绑定（ADR-0014「先上传拿标识、再随消息一次提交」）。

   错误映射复用 `lib/api.js` 的 `readError`：拒绝原因由后端按语言偏好渲染，前端只把它读出来。 */
import { readError } from './api.js';

/** 单文件上限（字节）。正本：`app/alibot/db.py` 的 `MAX_ATTACHMENT_BYTES`。 */
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

/**
 * 按上限把选中的文件分成两拨：**能传的**与**超限的**。
 *
 * 超限的那一拨由调用方直接拒绝并给可读原因，**不发请求**——把它传上去再读一遍后端的拒绝，
 * 等于用一个 10 MB 的往返换一条本地就能得出的结论。
 */
export function partitionBySize(files) {
  const list = Array.from(files || []);
  const accepted = [];
  const rejected = [];
  for (const file of list) {
    if (Number(file && file.size) > MAX_ATTACHMENT_BYTES) rejected.push(file);
    else accepted.push(file);
  }
  return { accepted, rejected };
}

/**
 * 上传一个文件成**未绑定**的附件草稿，返回后端给的元数据（标识 / 原名 / 大小 / MIME / url）。
 *
 * 这里不判断大小：预检在调用方（`partitionBySize`）已经做过，后端仍然有自己的闸门，
 * 但两者判的是同一个数字。
 */
export async function uploadAttachment(file) {
  const name = String((file && file.name) || '');
  const response = await fetch('/api/attachments?name=' + encodeURIComponent(name), {
    method: 'POST',
    headers: { 'Content-Type': (file && file.type) || 'application/octet-stream' },
    body: file,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null; // 非 JSON 响应：错误映射会给一条带状态码的兜底文案。
  }
  if (!response.ok) {
    const error = new Error(readError(payload, response));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload.attachment;
}
