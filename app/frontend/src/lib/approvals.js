/* 审批卡的纯呈现派生（票 #308 / T8）

   判据正本是旧 `studio/app.js:3113-3355` 的 `approval*` 族，逐条搬成纯函数——不读 DOM、不读全局
   状态、时钟一律由调用方给（`now` 参数）。于是倒计时与掩码这两条安全相关的判据能被逐条钉用例。

   四条纪律（票面「不发明后端没有的语义」）：

   1. **600 秒是后端的等待上界**，不是前端约定：`approval.py` 的 `DEFAULT_APPROVAL_TIMEOUT_SECONDS
      = 600.0`。前端只由 `requested_at + 600s` 推导剩余与呈现态，不改记录、不改写行为。
   2. **`requested_at` 不可解析 ⇒ 剩余 `null`**（显示 `--:--`），不伪造一个倒计时。
   3. **呈现态**：后端给的终态优先（`state !== 'pending'` 就用它）；仍 pending 但已越过上界 ⇒
      `expired`。它只改卡片可点性与角标读数，**不写回记录**（后端自己会在同一上界标 expired）。
   4. **敏感键一律掩码**：`input` 里命中的键（口令 / 令牌 / 凭证 / cookie …）的值替换成 `••••`；
      对象最多展开三层、长字符串与总摘要都有界——参数摘要是要铺进 DOM 的，铺多少由这里决定。 */
import { hasKey, t } from '../i18n/index.js';

/** 后端 `approval.py` 的等待上界（秒）：既是倒计时的基准，也是前端 `expired` 呈现的唯一判据。 */
export const APPROVAL_TIMEOUT_SECONDS = 600;

/** 敏感键的掩码值（旧 `APPROVAL_REDACTED`）。 */
export const APPROVAL_REDACTED = '••••';

/** 命中的键名一律掩码（旧 `APPROVAL_SENSITIVE_KEY`，逐字沿用同一个正则）。 */
export const APPROVAL_SENSITIVE_KEY =
  /(?:pass(?:word|wd)?|secret|token|api[-_]?key|authorization|cookie|credential|private[-_]?key|access[-_]?key|session[-_]?id)/i;

/** 呈现态 → 词条键（旧 `APPROVAL_STATE_KEYS`）。 */
export const APPROVAL_STATE_KEYS = {
  pending: 'approval.pending',
  approved: 'approval.approved',
  denied: 'approval.denied',
  expired: 'approval.expired',
};

/** 参数摘要里对象最多展开的层数（再深只留 `…`）。 */
const MAX_DEPTH = 3;

/** 单个字符串值的最大长度（超出截断加 `…`）。 */
const MAX_STRING = 120;

/** 一条参数摘要的总长上界。 */
const MAX_SUMMARY = 240;

/** 剩余秒数（向上取整）；`requested_at` 不可解析时返回 `null`——**不伪造**倒计时。 */
export function approvalRemainingSeconds(record, now) {
  const requested = Date.parse(record && record.requested_at);
  if (!Number.isFinite(requested)) return null;
  const at = now === undefined ? Date.now() : now;
  return Math.max(0, Math.ceil((requested + APPROVAL_TIMEOUT_SECONDS * 1000 - at) / 1000));
}

/** 呈现态：后端给的终态优先；仍 pending 但已越过 600s 上界时显示 expired。 */
export function approvalDisplayState(record, now) {
  if (!record) return 'expired';
  if (record.state && record.state !== 'pending') return String(record.state);
  const remaining = approvalRemainingSeconds(record, now);
  if (remaining !== null && remaining <= 0) return 'expired';
  return 'pending';
}

/** 呈现态的说法：词条存在就用词条，缺失退回原词（不伪造译文）。 */
export function approvalStateLabel(value) {
  const key = APPROVAL_STATE_KEYS[value];
  return key && hasKey(key) ? t(key) : String(value || t('approval.stateUnknown'));
}

/** 倒计时的 `mm:ss` 形状（超过 60 分钟也不截断分钟位）。 */
export function approvalClockText(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
}

/** 「剩余 {time}」：终态不再报剩余（倒计时只属于待审批）。 */
export function approvalRemainingText(record, displayState, now) {
  if (displayState !== 'pending') return '';
  const remaining = approvalRemainingSeconds(record, now);
  return t('approval.remaining', { time: remaining === null ? '--:--' : approvalClockText(remaining) });
}

/** `data-urgent`：只剩「快到期」这一个视觉信号（`<= 60s` 记 1）；不可解析时不挂这个属性。 */
export function approvalUrgentFlag(record, displayState, now) {
  if (displayState !== 'pending') return undefined;
  const remaining = approvalRemainingSeconds(record, now);
  if (remaining === null) return undefined;
  return remaining <= 60 ? '1' : '0';
}

/** 递归掩码：敏感键的值一律 `••••`；对象最多展开三层，避免把大载荷铺进 DOM。 */
export function approvalSanitizeValue(value, key, depth) {
  if (key && APPROVAL_SENSITIVE_KEY.test(String(key))) return APPROVAL_REDACTED;
  if (depth > MAX_DEPTH) return '…';
  if (Array.isArray(value)) {
    return value.map((item) => approvalSanitizeValue(item, '', depth + 1));
  }
  if (value && typeof value === 'object') {
    const out = {};
    Object.keys(value).forEach((name) => {
      out[name] = approvalSanitizeValue(value[name], name, depth + 1);
    });
    return out;
  }
  if (typeof value === 'string' && value.length > MAX_STRING) return value.slice(0, MAX_STRING - 3) + '…';
  return value;
}

/** 值的文字形态（字符串原样，其余按 JSON；序列化失败退回 `String`）。 */
export function approvalValueText(value) {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return String(value);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** 参数摘要：只读 `input`，敏感键掩码；总长有界，不把整份文件内容铺开。 */
export function approvalInputSummary(input) {
  if (input === null || input === undefined) return t('approval.noInput');
  if (typeof input !== 'object') return approvalValueText(approvalSanitizeValue(input, '', 0));
  const keys = Object.keys(input);
  if (!keys.length) return t('approval.noInput');
  const text = keys
    .map((key) => key + '=' + approvalValueText(approvalSanitizeValue(input[key], key, 0)))
    .join(' · ');
  return text.length > MAX_SUMMARY ? text.slice(0, MAX_SUMMARY - 3) + '…' : text;
}

/** 还没被决策的调用（#215）：``reply_id`` 是回复级的，一条记录里会同时躺着**已决策的旧调用**
    与**新调用**（快照读的是整条记录）。卡片与提交都只看这一份——否则对第二批点「拒绝」会把
    旧调用的相反结论一起带上，被服务端按 409 整批拒掉。只读，不改 record。 */
export function approvalPendingToolCalls(record) {
  const decided = new Set(
    ((record && record.decisions) || []).map((item) => String((item && item.tool_call_id) || '')),
  );
  return ((record && record.tool_calls) || []).filter(
    (call) => !decided.has(String((call && call.id) || '')),
  );
}

/** 一组工具调用的 id 集合（空 id 也收，与后端 `_call_key` 同口径）。 */
export function approvalCallIds(calls) {
  return new Set((calls || []).map((call) => String((call && call.id) || '')));
}

/** 卡片要铺的调用：待审批时只问**未决策**的（#215）；已收口时回看整条记录（决策是历史）。 */
export function approvalCardCalls(record, displayState) {
  return displayState === 'pending'
    ? approvalPendingToolCalls(record)
    : ((record && record.tool_calls) || []).slice();
}

/** 提交的 `decisions` 载荷：只带未决策的调用、只带有 id 的（`POST /api/approvals/{reply_id}`）。 */
export function approvalDecisions(record, confirmed) {
  return approvalPendingToolCalls(record)
    .map((call) => ({ tool_call_id: String((call && call.id) || ''), confirmed: Boolean(confirmed) }))
    .filter((decision) => decision.tool_call_id);
}

/** 卡片的名字：「待审批工具：<工具名, …>」。 */
export function approvalCardLabel(record, displayState) {
  const tools = approvalCardCalls(record, displayState)
    .map((call) => String((call && call.name) || ''))
    .filter(Boolean)
    .join(', ');
  return t('approval.cardLabel', { tools });
}

/** 审批记录的呈现指纹：只覆盖会进入 DOM / 角标的字段，用来判断一次快照是否真的带来变化。 */
export function approvalRecordFingerprint(record) {
  return JSON.stringify([
    record && record.target_id,
    record && record.state,
    record && record.member_id,
    record && record.requested_at,
    record && record.resolved_at,
    record && record.tool_calls,
    record && record.decisions,
  ]);
}

/** 一个 target 的记录顺序：pending 在前，其余按请求时间倒序（本次会话内的显示顺序）。 */
export function sortApprovalRecords(records, now) {
  const rank = (record) => (approvalDisplayState(record, now) === 'pending' ? 0 : 1);
  return (records || [])
    .slice()
    .sort(
      (left, right) =>
        rank(left) - rank(right) ||
        String(right.requested_at || '').localeCompare(String(left.requested_at || '')),
    );
}
