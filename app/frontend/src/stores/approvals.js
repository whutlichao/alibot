/* 审批的读数与写路径（票 #308 / T8；旧 `studio/app.js:3113-3770` 的 `approval*` 族）

   一份镜像，四个读入口、两个写入口：

   - **镜像**：`state.records[target_id][reply_id] = record`。它是审批卡、任务页签的待审批圆点、
     任务卡片 / 列表行的「待审批 N」角标的**唯一**读数——票面明写「徽标与审批卡同一份读数，
     不各读一次、不各算一遍」，所以这些都从 `pendingCountFor()` / `recordsFor()` 派生。
   - **读**：打开任务线程 `loadFor(targetId)`（`GET /api/approvals?target_id=&state=pending`）；
     进入工作区 / 重连 `loadAll()`（`GET /api/approvals?state=pending`，圆点的读数来源）。
   - **写**：`submit()`（`POST /api/approvals/{reply_id}`，单条在途闸门 ⇒ 双击只发一次）与
     `applyFrame()`（live `approval_required` / `approval_resolved`）。
   - **收口**：快照里消失的 pending 标 `expired`（不伪造记录、不删行）；「已知」集合在请求发出
     **之前**抓取，于是在途期间到达的 live 帧不会被这一轮误标过期（旧 `pendingApprovalIndex`）。

   呈现派生（倒计时 / 呈现态 / 掩码）全在 `lib/approvals.js`：那一层不依赖 Vue，逐条钉用例；
   本文件只搬「什么时候读、写哪里、失败怎么办」。

   **不做周期刷新，也不做秒级 tick**（与旧页面的一处刻意差异，理由写在这里）：旧页面用
   12s 全量轮询 + 1s 倒计时 tick 兜底「线程关着时的角标」与「本地 600s 过期」。v2 的替代是
   **事件驱动**：打开线程 / 进入工作区 / 重连 / 任何 `approval_*` live 帧 / 提交之后各读一次。
   真实收口本来就不靠前端猜：后端在同一个 600s 上界把记录标 `expired` 并发出
   `approval_resolved`（`approval.py` 的 `_expire`），那条帧会落进这份镜像。前端仍由
   `requested_at + 600s` 推导呈现态，只是重算时机是上面这些事件，而不是每秒。 */
import { reactive } from 'vue';
import { api } from '../lib/api.js';
import {
  approvalCallIds,
  approvalDecisions,
  approvalDisplayState,
  approvalPendingToolCalls,
  approvalRecordFingerprint,
  sortApprovalRecords,
} from '../lib/approvals.js';

/**
 * @param {object} [options]
 * @param {() => number} [options.now] 时钟（用例注入假时钟，让呈现态可判）
 */
export function createApprovalStore(options) {
  const config = options || {};
  const now = config.now || (() => Date.now());

  const state = reactive({
    // target_id → { reply_id: record }；两档读数（单 target / 全工作区）共用一个写入口。
    records: {},
    // 在途的 reply_id（同一张卡同一时刻至多一个请求）。
    inFlight: [],
    // reply_id → 失败原因（后端可读文案，铺在卡片上）。
    errors: {},
  });

  function mapFor(targetId) {
    const id = String(targetId || '');
    if (!state.records[id]) state.records[id] = {};
    return state.records[id];
  }

  function recordFor(targetId, replyId) {
    const map = state.records[String(targetId || '')];
    return (map && map[String(replyId || '')]) || null;
  }

  /** 覆盖一条记录（只在指纹变化时写，避免无谓的重渲染）。 */
  function upsert(targetId, record) {
    if (!record || !record.reply_id) return false;
    const id = String(targetId || record.target_id || '');
    if (!id) return false;
    const replyId = String(record.reply_id);
    const next = { ...record, target_id: String(record.target_id || id) };
    const map = mapFor(id);
    if (fingerprintOf(map[replyId]) === fingerprintOf(next)) return false;
    map[replyId] = next;
    return true;
  }

  function fingerprintOf(record) {
    return record ? approvalRecordFingerprint(record) : '';
  }

  /** 局部改一条记录（提交回执之外的场景：例如用例里模拟「已决策的旧调用」）。 */
  function patchRecord(targetId, replyId, patch) {
    const record = recordFor(targetId, replyId);
    if (!record) return null;
    const next = { ...record, ...(patch || {}) };
    mapFor(targetId)[String(replyId)] = next;
    return next;
  }

  /** 某个 target 的记录：pending 在前，其余按请求时间倒序。 */
  function recordsFor(targetId) {
    const map = state.records[String(targetId || '')];
    if (!map) return [];
    return sortApprovalRecords(Object.values(map), now());
  }

  /** 某个 target 当前 pending 的条数（呈现态判据与审批卡一致：本地越过 600s 立刻不再计数）。 */
  function pendingCountFor(targetId) {
    const map = state.records[String(targetId || '')];
    if (!map) return 0;
    const at = now();
    return Object.values(map).filter((record) => approvalDisplayState(record, at) === 'pending').length;
  }

  function errorFor(replyId) {
    return state.errors[String(replyId || '')] || '';
  }

  function isInFlight(replyId) {
    return state.inFlight.includes(String(replyId || ''));
  }

  /** 镜像里所有仍 pending 的记录，按 target 分组（快照对账用；`onlyTargetId` 收窄到单个 target）。 */
  function pendingIndex(onlyTargetId) {
    const known = new Map();
    const at = now();
    Object.keys(state.records).forEach((targetId) => {
      if (onlyTargetId && targetId !== String(onlyTargetId)) return;
      const ids = new Set();
      Object.values(state.records[targetId]).forEach((record) => {
        if (approvalDisplayState(record, at) !== 'pending') return;
        ids.add(String(record.reply_id));
      });
      if (ids.size) known.set(targetId, ids);
    });
    return known;
  }

  /** 一份 pending 快照并入镜像的**唯一**写入口：按 `record.target_id`（单 target 读数可回退到
      `fallbackTargetId`）归属；`known` 里消失的 pending 标 `expired`。返回镜像是否真的变了。 */
  function applySnapshot(records, known, fallbackTargetId) {
    let changed = false;
    const seen = new Map();
    (records || []).forEach((record) => {
      if (!record || !record.reply_id) return;
      const targetId = String(record.target_id || fallbackTargetId || '');
      if (!targetId) return;
      const replyId = String(record.reply_id);
      if (!seen.has(targetId)) seen.set(targetId, new Set());
      seen.get(targetId).add(replyId);
      if (upsert(targetId, { ...record, target_id: targetId })) changed = true;
    });
    (known || new Map()).forEach((replyIds, targetId) => {
      const present = seen.get(targetId) || new Set();
      replyIds.forEach((replyId) => {
        if (present.has(replyId)) return;
        const record = recordFor(targetId, replyId);
        if (record && approvalDisplayState(record, now()) === 'pending') {
          patchRecord(targetId, replyId, { state: 'expired', resolved_at: new Date(now()).toISOString() });
          changed = true;
        }
      });
    });
    return changed;
  }

  /** 打开任务线程时的读数：只读该 target 的 pending 快照（旧 `loadApprovalsFor`）。 */
  async function loadFor(targetId) {
    if (!targetId) return false;
    // 「已知」在请求发出**之前**抓取：在途期间到达的 live 帧不会被这一轮误标过期。
    const known = pendingIndex(targetId);
    let payload;
    try {
      payload = await api('/api/approvals?target_id=' + encodeURIComponent(targetId) + '&state=pending');
    } catch {
      return false;
    }
    const records = Array.isArray(payload && payload.approvals) ? payload.approvals : [];
    return applySnapshot(records, known, targetId);
  }

  /** 全工作区 pending 读数（不带 `target_id`）：圆点 / 角标的读数来源（旧 `loadPendingApprovals`）。 */
  async function loadAll() {
    const known = pendingIndex();
    let payload;
    try {
      payload = await api('/api/approvals?state=pending');
    } catch {
      return false;
    }
    const records = Array.isArray(payload && payload.approvals) ? payload.approvals : [];
    return applySnapshot(records, known);
  }

  /** live `approval_required`：同一个 reply_id 的新帧只更新待办调用。

      #215：`reply_id` 是**一条回复**的标识，一条回复可以有多批确认——记录已收口后同一个
      `reply_id` 还会再来一帧，帧里只含这一批**新**调用。因此终态帧只在「没有新调用」时忽略
      （与后端 register 的幂等边界同形）；有新调用时按新帧重开，`decisions` 作为历史保留，
      `tool_calls` 与后端一样是**累积全量**，倒计时从这一批重新起算。 */
  function applyRequired(payload) {
    const targetId = String((payload && payload.target_id) || '');
    const replyId = String((payload && payload.reply_id) || '');
    if (!targetId || !replyId) return false;
    const existing = recordFor(targetId, replyId);
    const incoming = Array.isArray(payload.tool_calls) ? payload.tool_calls : [];
    const known = approvalCallIds((existing && existing.tool_calls) || []);
    const fresh = incoming.filter((call) => !known.has(String((call && call.id) || '')));
    const displayState = existing ? approvalDisplayState(existing, now()) : 'pending';
    if (existing && displayState !== 'pending' && !fresh.length) return false;
    const reopened = Boolean(existing) && displayState !== 'pending';
    mapFor(targetId)[replyId] = {
      reply_id: replyId,
      target_id: targetId,
      member_id: String((payload && payload.member_id) || (existing && existing.member_id) || ''),
      // 与后端同形：镜像保存**累积**的调用，已决策的那些靠 decisions 过滤掉。
      tool_calls: reopened
        ? [...((existing && existing.tool_calls) || []), ...fresh]
        : incoming.length
          ? incoming
          : (existing && existing.tool_calls) || [],
      state: 'pending',
      decisions: existing ? existing.decisions : [],
      // 重开是**新的一批**：倒计时从这一批重新起算（后端同时刷新了 requested_at）。
      requested_at: reopened
        ? new Date(now()).toISOString()
        : (existing && existing.requested_at) || new Date(now()).toISOString(),
      resolved_at: null,
    };
    return true;
  }

  /** live `approval_resolved`：只更新已经出现在本会话里的卡；没有卡就不伪造一条。 */
  function applyResolved(payload) {
    const targetId = String((payload && payload.target_id) || '');
    const replyId = String((payload && payload.reply_id) || '');
    if (!targetId || !replyId) return false;
    const existing = recordFor(targetId, replyId);
    if (!existing) return false;
    patchRecord(targetId, replyId, {
      state: String((payload && payload.state) || 'resolved'),
      decisions: Array.isArray(payload.decisions) ? payload.decisions : existing.decisions,
      resolved_at: existing.resolved_at || new Date(now()).toISOString(),
    });
    return true;
  }

  /** live 帧的落点（`app-context.js` 的 frame 分流调它）：只写这一份镜像。 */
  function applyFrame(payload) {
    const type = payload && payload.type;
    if (type === 'approval_required') return applyRequired(payload);
    if (type === 'approval_resolved') return applyResolved(payload);
    return false;
  }

  /**
   * 提交一批决策（`POST /api/approvals/{reply_id}`）。
   *
   * 三条判据：
   * 1. **幂等**：同一条记录在途时再点直接返回（请求层闸门，不靠按钮的 `disabled` 兜底）；
   * 2. **只在有权限时出动作**：记录不是 pending（含本地已越过 600s 上界）⇒ 不发请求；
   * 3. **失败不改写状态**：错误原因留在 `errors[reply_id]`，记录原样保留；随后按 target 重读
   *    真实状态（旧 `reconcileApproval`），**读不到就保留本地状态**（不静默清空、不伪造成 expired）。
   */
  async function submit(targetId, replyId, confirmed) {
    const id = String(replyId || '');
    const target = String(targetId || '');
    if (!id || !target || isInFlight(id)) return { ok: false, skipped: true };
    const record = recordFor(target, id);
    if (!record || approvalDisplayState(record, now()) !== 'pending') return { ok: false, skipped: true };
    const decisions = approvalDecisions(record, confirmed);
    if (!decisions.length) return { ok: false, skipped: true };
    state.inFlight.push(id);
    delete state.errors[id];
    try {
      const payload = await api('/api/approvals/' + encodeURIComponent(id), {
        method: 'POST',
        body: { decisions },
      });
      if (payload && payload.approval) upsert(target, payload.approval);
      return { ok: true, approval: payload && payload.approval };
    } catch (err) {
      state.errors[id] = err && err.message ? err.message : String(err);
      await reconcile(target, id);
      return { ok: false, error: err };
    } finally {
      const index = state.inFlight.indexOf(id);
      if (index >= 0) state.inFlight.splice(index, 1);
    }
  }

  /** 失败后按 target 重读真实状态，不猜；读不到保留本地状态（错误行已经铺在卡片上）。 */
  async function reconcile(targetId, replyId) {
    if (!targetId || !replyId) return;
    const id = String(replyId);
    const known = pendingIndex(targetId);
    let payload;
    try {
      payload = await api('/api/approvals?target_id=' + encodeURIComponent(targetId));
    } catch {
      return;
    }
    const records = Array.isArray(payload && payload.approvals) ? payload.approvals : [];
    const found = records.find((record) => String(record.reply_id) === id);
    if (found) {
      // 回读到的记录**不覆盖错误**：失败原因仍是人的第一手信息。
      upsert(targetId, { ...found, target_id: found.target_id || String(targetId) });
      return;
    }
    const seen = new Set(records.map((record) => String(record.reply_id)));
    const knownIds = known.get(String(targetId)) || new Set();
    if (!knownIds.has(id) || seen.has(id)) return;
    const record = recordFor(targetId, id);
    if (record && approvalDisplayState(record, now()) === 'pending') {
      patchRecord(targetId, id, { state: 'expired', resolved_at: new Date(now()).toISOString() });
    }
  }

  /** 换工作区 / 用例之间：整份作废（不在新产品里调用，存在的理由与线程的 `resetForTarget` 同一条）。 */
  function reset() {
    state.records = {};
    state.inFlight.splice(0, state.inFlight.length);
    Object.keys(state.errors).forEach((key) => delete state.errors[key]);
  }

  return {
    state,
    recordsFor,
    recordFor,
    patchRecord,
    pendingCountFor,
    errorFor,
    isInFlight,
    loadFor,
    loadAll,
    applyFrame,
    submit,
    reset,
    // 供呈现层读「现在」的同一个时钟：卡片与派生读数用同一份时间基准。
    now,
    // 已决策过滤这条判据也由 store 给出口（组件不必各自 import 两份实现）。
    pendingToolCalls: approvalPendingToolCalls,
    decisionsFor: approvalDecisions,
  };
}

/** 当前频道下「有任务在等审批」的任务数（页签圆点的 aria 名里的 N = **任务数**，不是记录数）。 */
export function channelPendingTaskCount(tasks, channelId, pendingCountFor) {
  if (!channelId) return 0;
  return (tasks || []).filter(
    (task) => task && task.channel_id === channelId && pendingCountFor(task.target_id) > 0,
  ).length;
}
