/* 运行面板的读数合并（纯函数，零 DOM、零请求）

   这个文件的每一行都对应旧 `studio/app.js` 的 `runPanel*` 族函数。抽出来的理由不是「整洁」，
   而是那套合并判据**有秩**（哪一帧能覆盖哪一帧），只有把它写成纯函数才能按判据逐条钉在用例里：

   - **分派**：快照行 + live `team_assignment` 按 `assignment_id` 合并；live 只能把状态**往前推**
     （`RUN_PANEL_ASSIGNMENT_RANK`），迟到的 `started` 不把终态降回去；
   - **Plan**：每个成员取快照里最近一个 run 的清单，live `plan_update` 按 `(plan_run_id, task.id)`
     覆盖；`deleted_at` 的行移除；新的 `plan_run_id` **整份替换**旧清单（不把两轮混在一起）；
   - **SOP**：快照 run 视图 + live `sop_event` 按 `step_index` 合并；`completed` / `failed` 是终态，
     迟到的 live 帧不把终态拉回，快照的终态也优先于断线前的旧帧；
   - **overlayMarker**：快照已经合并了它之前的所有变化，因此 Plan / SOP 的 live 覆盖只应用
     **标记之后**到达的帧（否则「重连刷新」会把状态拉回断线前）。分派不走这条排除
     （它有秩守卫，且 `started → 终态` 是合法的直播过程）；`agent_event` 本来就只活在 live 层，全部保留。

   为什么要跟旧实现**逐字**同义：同一个后端事实在两版页面里必须得到同一份读数。这不是「复制代码」
   ——旧页面仍在 `/studio/` 服役，两族用例各自守自己的落点，判据本身只有一份文字描述（ADR-0038 §五）。 */
import { hasKey, t } from '../i18n/index.js';

/** 快照分页：与后端 `GET /team-runs` 的默认 `limit` 同一个数（旧实现同值）。 */
export const RUN_PANEL_RUN_LIMIT = 20;

/** 成员事件段只保留最后这么多条 live 帧（有界呈现，不是有界存储）。 */
export const RUN_PANEL_EVENT_LIMIT = 80;

/** 每个回复下最多铺这么多条事件（同一回复的长流只留尾部）。 */
export const RUN_PANEL_REPLY_EVENT_LIMIT = 20;

/** 分派状态的推进顺序：live 帧只能把行往前推，不能把快照里的终态降回 started。 */
export const RUN_PANEL_ASSIGNMENT_RANK = {
  started: 0,
  succeeded: 1,
  failed: 1,
  interrupted: 1,
  'budget-exhausted': 1,
};

/** 状态词 → 词条键。后端词表以后变大时，未知值落到 `runPanel.state.unknown`（不编造译文）。 */
export const RUN_PANEL_STATE_KEYS = {
  started: 'runPanel.state.started',
  succeeded: 'runPanel.state.succeeded',
  failed: 'runPanel.state.failed',
  interrupted: 'runPanel.state.interrupted',
  'budget-exhausted': 'runPanel.state.budgetExhausted',
  running: 'runPanel.state.running',
  completed: 'runPanel.state.completed',
  'invalid-leader': 'runPanel.state.invalidLeader',
  pending: 'runPanel.state.pending',
  in_progress: 'runPanel.state.inProgress',
  awaiting: 'runPanel.state.awaiting',
};

/** 运行面板消费的 live 事件种类（别的帧留在 live 层给各自的票用，这里不认）。 */
export const RUN_PANEL_LIVE_TYPES = ['agent_event', 'team_assignment', 'plan_update', 'sop_event'];

/** 状态 pill 的说法（`data-state` 仍是后端原词，只有文案按词条走）。 */
export function stateLabel(value) {
  const key = RUN_PANEL_STATE_KEYS[value];
  return key ? t(key) : t('runPanel.state.unknown');
}

/**
 * 分派耗时：两个时间戳都来自后端；`finished_at` 为空是「还在跑」，不拿本地时钟假装读数。
 *
 * `started_at` 缺失或时间戳解析不出来时返回空串（整块不渲染），不显示 `0.0s`。
 */
export function durationLabel(item) {
  if (!item || !item.started_at) return '';
  if (!item.finished_at) return t('runPanel.inFlight');
  const from = Date.parse(item.started_at);
  const to = Date.parse(item.finished_at);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return '';
  const seconds = (to - from) / 1000;
  const value = seconds >= 10 ? String(Math.round(seconds)) : seconds.toFixed(1);
  return t('runPanel.duration', { seconds: value });
}

/**
 * 成员事件的摘要只取白名单 payload 的**数据字段**，不在这里造句子。
 *
 * 顺序即优先级：工具名 → 增量正文 → 文本 → 状态 → 收尾原因 → 错误消息 → 提示 → 名字。
 * 超过 120 字裁断（事件行是一行摘要，不是正文容器）。
 */
export function eventSummary(event) {
  const payload = (event && event.payload) || {};
  const clip = (text) => {
    const value = String(text || '')
      .replace(/\s+/g, ' ')
      .trim();
    return value.length > 120 ? value.slice(0, 120) + '…' : value;
  };
  if (payload.tool_call_name) return clip(payload.tool_call_name);
  if (typeof payload.delta === 'string' && payload.delta.trim()) return clip(payload.delta);
  if (typeof payload.text === 'string' && payload.text.trim()) return clip(payload.text);
  if (payload.state) return String(payload.state);
  if (payload.finished_reason) return String(payload.finished_reason);
  if (payload.error && payload.error.message) return clip(payload.error.message);
  if (payload.hint) return clip(typeof payload.hint === 'string' ? payload.hint : JSON.stringify(payload.hint));
  if (payload.name) return String(payload.name);
  return '';
}

/**
 * 成员名与头像色位的解析器：先看工作区成员表，再看 Agent 表，最后看频道成员表。
 *
 * 运行面板里的 `member_id` 可能来自三个池子（分派记录里的成员不一定还在工作区名单里），
 * 旧实现按同样的顺序找（`runPanelMemberRecord`）。找不到就退回 id 本身——**不编名字、
 * 不给 0 号色位以外的猜测**：一个陌生 id 显示成陌生人名比显示成 id 更难查。
 */
export function memberResolver(pools) {
  const list = (pools || []).filter((pool) => Array.isArray(pool));
  return (memberId) => {
    const id = String(memberId || '');
    for (const pool of list) {
      const found = pool.find((member) => member && String(member.id) === id);
      if (found) return found;
    }
    return null;
  };
}

/** `member_id` → 显示名（找不到就用 id，与旧实现 `runPanelMemberName` 同一条）。 */
export function memberNameOf(resolve, memberId) {
  const member = resolve(memberId);
  return member ? member.display_name : String(memberId || '');
}

/** `member_id` → 头像色位（找不到就是 0）。 */
export function memberSlotOf(resolve, memberId) {
  const member = resolve(memberId);
  return member ? member.avatar_slot : 0;
}

/** 某个 target 的 live 帧里属于运行面板的那一批（顺序即到达顺序）。 */
export function panelLiveEvents(events) {
  return (events || []).filter((event) => event && RUN_PANEL_LIVE_TYPES.includes(event.type));
}

/**
 * 只保留**快照之后**到达的 Plan / SOP live 帧。
 *
 * 快照是它们更早变化的合并结果；把队列里的旧帧再叠一遍会让「重连刷新」把状态拉回断线前。
 * 分派不在这里排除（它有状态推进的秩守卫，且 `started` → 终态是合法的直播过程）；
 * `agent_event` 本来就只活在 live 层，全部保留。
 */
export function overlayEvents(live, marker) {
  if (!marker) return live;
  const index = live.indexOf(marker);
  // 有界保留把标记挤掉了：宁可全部应用，也别丢新帧。
  if (index < 0) return live;
  return live.slice(index + 1);
}

/** 快照分派 + live 分派按 `assignment_id` 合并：同一分派只有一行。 */
export function assignmentRows(run, live) {
  const rows = ((run && run.assignments) || []).map((item) => ({ ...item }));
  const byId = new Map(rows.map((item) => [String(item.id), item]));
  (live || []).forEach((frame) => {
    if (frame.type !== 'team_assignment' || frame.team_run_id !== run.id) return;
    const key = String(frame.assignment_id || '');
    if (!key) return;
    const existing = byId.get(key);
    if (!existing) {
      const row = {
        id: key,
        member_id: frame.member_id,
        task_id: frame.task_id,
        reply_id: frame.reply_id,
        state: frame.state,
        started_at: null,
        finished_at: null,
        reply_excerpt: null,
        error: null,
      };
      byId.set(key, row);
      rows.push(row);
      return;
    }
    const currentRank = RUN_PANEL_ASSIGNMENT_RANK[existing.state] ?? 0;
    const frameRank = RUN_PANEL_ASSIGNMENT_RANK[frame.state] ?? 0;
    if (frameRank < currentRank) return; // 不把终态降回 started
    existing.state = frame.state;
    if (frame.member_id) existing.member_id = frame.member_id;
    if (frame.reply_id) existing.reply_id = frame.reply_id;
    if (frame.task_id) existing.task_id = frame.task_id;
  });
  return rows;
}

/** 分派按成员分组（保持后端给的顺序：`Map` 的插入序就是首次出现的顺序）。 */
export function groupAssignmentsByMember(rows) {
  const groups = new Map();
  (rows || []).forEach((item) => {
    const memberId = String(item.member_id || '');
    if (!groups.has(memberId)) groups.set(memberId, []);
    groups.get(memberId).push(item);
  });
  return [...groups.entries()].map(([memberId, items]) => ({ memberId, items }));
}

/** 成员事件按「成员 → 回复」两级分组；同一回复只留尾部若干条。 */
export function groupEventsByMember(frames) {
  const events = (frames || []).filter((frame) => frame.type === 'agent_event').slice(-RUN_PANEL_EVENT_LIMIT);
  const groups = new Map();
  events.forEach((frame) => {
    const memberId = String(frame.member_id || '');
    if (!groups.has(memberId)) groups.set(memberId, new Map());
    const replies = groups.get(memberId);
    const replyId = String(frame.reply_id || '');
    if (!replies.has(replyId)) replies.set(replyId, []);
    replies.get(replyId).push(frame);
  });
  return [...groups.entries()].map(([memberId, replies]) => ({
    memberId,
    // 组头的计数是**回复条数**（不是事件条数）：`×N` 是计数，不写成一句话。
    replyCount: replies.size,
    replies: [...replies.entries()].map(([replyId, framesOfReply]) => ({
      replyId,
      frames: framesOfReply.slice(-RUN_PANEL_REPLY_EVENT_LIMIT),
    })),
  }));
}

/**
 * Plan：快照里每个成员最近一个 run 的清单 + live `plan_update` 覆盖（按 task id 去重）。
 *
 * `autonomous` 频道的空态解释不在这一层：这函数只回答「有哪些清单」，不回答「为什么是空的」。
 */
export function planGroups(planMembers, live) {
  const groups = new Map();
  (planMembers || []).forEach((member) => {
    const latest = (member.runs || [])[0];
    if (!latest) return;
    groups.set(String(member.member_id), {
      memberId: String(member.member_id),
      runId: String(latest.id),
      tasks: (latest.tasks || []).map((task) => ({ ...task })),
    });
  });
  (live || [])
    .filter((frame) => frame.type === 'plan_update')
    .forEach((frame) => {
      const memberId = String(frame.member_id || '');
      let group = groups.get(memberId);
      if (!group) {
        group = { memberId, runId: String(frame.plan_run_id || ''), tasks: [] };
        groups.set(memberId, group);
      }
      if (group.runId !== String(frame.plan_run_id || '')) {
        group.runId = String(frame.plan_run_id || '');
        group.tasks = [];
      }
      const task = frame.task || {};
      const index = group.tasks.findIndex((item) => String(item.id) === String(task.id));
      if (task.deleted_at) {
        if (index >= 0) group.tasks.splice(index, 1);
        return;
      }
      if (index >= 0) group.tasks[index] = { ...group.tasks[index], ...task };
      else group.tasks.push({ ...task });
    });
  return [...groups.values()].map((group) => ({
    ...group,
    tasks: group.tasks.filter((task) => !task.deleted_at),
  }));
}

/** `completed` / `failed` 是 SOP 的终态：快照已经收口的运行不会被迟到的 live 帧拉回。 */
export function sopTerminalPhase(phase) {
  return phase === 'completed' || phase === 'failed';
}

/**
 * SOP：快照 run 视图 + live `sop_event` 覆盖（按 run id + step_index 合并）。
 *
 * 步骤名在**定义**里（run state 只存 phase / verdict），因此调用方要传 `sopDefs`
 * （`GET /api/sops/{definition_id}` 的读数，按 definition id 索引）。
 */
export function sopViews(sopRuns, sopDefs, live) {
  const defs = sopDefs || {};
  const views = new Map();
  (sopRuns || []).forEach((run) => {
    views.set(String(run.id), {
      id: String(run.id),
      definitionId: String(run.sop_definition_id || ''),
      phase: run.phase,
      startedAt: run.started_at,
      awaiting: run.awaiting || null,
      steps: ((run.state && run.state.steps) || []).map((step, index) => ({
        index,
        subject: step.subject || '',
        phase: step.phase,
        verdicts: (step.verifications || []).slice(),
      })),
    });
  });
  (live || [])
    .filter((frame) => frame.type === 'sop_event')
    .forEach((frame) => {
      const runId = String(frame.sop_run_id || '');
      if (!runId) return;
      let view = views.get(runId);
      if (!view) {
        view = { id: runId, definitionId: '', phase: 'pending', startedAt: null, awaiting: null, steps: [] };
        views.set(runId, view);
      }
      const payload = frame.payload || {};
      if (payload.run_phase && !sopTerminalPhase(view.phase)) view.phase = frame.phase;
      if (payload.reply_id) view.awaiting = { reply_id: payload.reply_id };
      const index = Number(frame.step_index) || 0;
      let step = view.steps[index];
      if (!step) {
        step = { index, subject: payload.step || '', phase: 'pending', verdicts: [] };
        view.steps[index] = step;
      }
      const nextPhase = payload.phase || frame.phase;
      if (nextPhase && (!sopTerminalPhase(step.phase) || sopTerminalPhase(nextPhase))) {
        step.phase = nextPhase;
      }
      if (payload.verdict) step.verdicts = step.verdicts.concat([payload.verdict]);
    });
  return [...views.values()].map((view) => {
    const definitionSteps = (defs[view.definitionId] || {}).steps || [];
    return {
      ...view,
      steps: view.steps
        .slice()
        .sort((a, b) => a.index - b.index)
        .map((step) => {
          const definitionStep = definitionSteps[step.index];
          const subject = step.subject
            || (definitionStep && definitionStep.subject)
            || t('runPanel.stepIndex', { index: step.index + 1 });
          // 只呈现**最新**一个 verdict：逐步的判定历史不是这个面板的职责（旧实现同一条）。
          const verdict = step.verdicts.length ? step.verdicts.at(-1) : null;
          return { ...step, subject, verdict };
        }),
    };
  });
}

/**
 * 频道运行面板的协作模式：只读**已读回的缓存**（正本在后端）。
 *
 * `null` = 任务面板 / 没有频道 / 配置还没读回——前端不推算模式，读不到就按通用空态铺，
 * 绝不把「读不到」误报成 `autonomous`。
 */
export function channelMode(panel, channelConfigs) {
  if (!panel || panel.taskId || !panel.channelId) return null;
  const config = (channelConfigs || {})[panel.channelId];
  return config ? config.mode : null;
}

/** 词条键是否存在（用例守两份语言对齐时用；组件不直接用）。 */
export function hasStateLabel(value) {
  return hasKey('runPanel.state.' + value);
}
