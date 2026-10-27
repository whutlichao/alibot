/* 搜索结果的**纯呈现**判据（无请求、无状态）

   正本是旧 `studio/app.js:520-600` 的 `searchTargetLabel` / `searchSourceLabel` /
   `searchHitTypeLabel` / `searchHitText` / `searchSnipNode` 五段。搬出来成为纯函数，是因为它们
   决定的是「同一份后端命中怎么显示」——与 `lib/format.js` 同一条理由：两版页面必须显示同一个东西。

   三条硬纪律（`CONTEXT.md` 搜索命中 / 高亮区间）：
   - 命中判定由后端做，前端**不在正文里找位置**；
   - 高亮只按后端给的 `highlight_ranges` 切文本，区间外的一切原样呈现；
   - 认不出的 `kind` 不猜语义：类型标签退回后端原词，片段退回空串。

   区间下标口径与后端一致（`db.py` 的 `_search_highlight_ranges`）：**UTF-16** 下标，因此可以直接
   在 JS 字符串上 `slice`（#291 之后的口径，与提及快照同一套）。 */
import { hasKey, t } from '../i18n/index.js';
import { statusLabel } from './format.js';

/** 四档 facet：取值与顺序照原型 `:631-635`（也是后端 `SearchFacet` 的枚举）。 */
export const SEARCH_FACETS = ['all', 'message', 'task', 'agent'];

/** facet → 词条键（`all` 是「全部」那一档）。 */
const FACET_LABEL_KEYS = {
  all: 'search.facetAll',
  message: 'search.facetMessage',
  task: 'search.facetTask',
  agent: 'search.facetAgent',
};

export function facetLabel(facet) {
  const key = FACET_LABEL_KEYS[facet];
  return key ? t(key) : String(facet || '');
}

/** 任务状态词（`task.status.*`；词条表没有就退回后端原词，不伪造译文）。 */
export function taskStatusLabel(status) {
  const key = 'task.status.' + status;
  return hasKey(key) ? t(key) : status;
}

/** 命中所属 target 的说法（频道 / 私信 / 任务线程三类）；缺失时给明确的未知占位。 */
export function targetLabel(target) {
  const name = target && target.display_name ? target.display_name : null;
  if (!name) return t('search.unknownTarget');
  if (target.kind === 'channel') return t('search.targetChannel', { name });
  if (target.kind === 'dm') return t('search.targetDm', { name });
  if (target.kind === 'task') return t('search.targetTask', { name });
  return name;
}

/** 结果行 `.r-top` 的「来源」一格：Agent 不挂 target，因此给它集合名。 */
export function hitSourceLabel(hit) {
  const target = hit && hit.target;
  return target ? targetLabel(target) : t('search.sourceAgent');
}

/** 结果行 `.r-top` 的命中类型标签（与四档 facet 同名：消息 / 任务 / Agent）。 */
export function hitTypeLabel(hit) {
  const map = { message: 'search.facetMessage', task: 'search.facetTask', agent: 'search.facetAgent' };
  const key = map[hit && hit.kind];
  return key ? t(key) : String((hit && hit.kind) || '');
}

/**
 * 把命中的可呈现文本切成「普通段 / 高亮段」。
 *
 * **只按后端区间切**：越界、倒序、重叠的区间一律跳过（不修正、不重排——修一次就等于在猜后端
 * 想高亮哪里），光标只前进不回退，因此「前一段的尾」永远不会被重复输出。
 */
export function hitSegments(hit) {
  const text = String(hit && hit.text == null ? '' : hit.text);
  const ranges = Array.isArray(hit && hit.highlight_ranges) ? hit.highlight_ranges : [];
  const segments = [];
  let cursor = 0;
  ranges.forEach((range) => {
    if (!range || !Number.isInteger(range.start) || !Number.isInteger(range.end)) return;
    if (range.start < cursor || range.end <= range.start || range.end > text.length) return;
    if (range.start > cursor) segments.push({ mark: false, text: text.slice(cursor, range.start) });
    segments.push({ mark: true, text: text.slice(range.start, range.end) });
    cursor = range.end;
  });
  if (cursor < text.length) segments.push({ mark: false, text: text.slice(cursor) });
  return segments;
}

/**
 * 结果行 `.r-snip`：命中本体之外的**一行**辅助事实。
 *
 * 消息给作者（作者已软删除时额外标记，那一格是后端事实 `author.deleted`）、任务给序号与状态、
 * Agent 给状态与模型。认不出的 kind 给空片段 —— 不拿 `text` 冒充片段（那会把正文说两遍）。
 */
export function hitSnip(hit) {
  const kind = hit && hit.kind;
  if (kind === 'message') {
    const author = (hit.message && hit.message.author) || {};
    return {
      text: t('search.snipAuthor', { name: author.display_name || t('search.unknownAuthor') }),
      authorDeleted: Boolean(author.deleted),
    };
  }
  if (kind === 'task') {
    const task = hit.task || {};
    return {
      text: t('search.snipTask', {
        id: String(task.id || '').slice(-6),
        status: taskStatusLabel(task.status),
      }),
      authorDeleted: false,
    };
  }
  if (kind === 'agent') {
    const agent = hit.agent || {};
    return {
      text: t('search.snipAgent', {
        status: agent.status ? statusLabel(agent.status) : '',
        model: agent.model_id || '',
      }),
      authorDeleted: false,
    };
  }
  return { text: '', authorDeleted: false };
}

/**
 * 结果行要不要做成可点。
 *
 * 旧页面**只有任务命中**可点（打开右侧任务线程），判据是载荷里有没有可打开的任务标识；消息命中
 * 的滚动定位与 Agent 详情面都还没有交付，因此那两类是非交互行。本版任务线程也**还没迁**，于是
 * 三类命中一律是 `div`：渲染一个点下去什么都不发生的按钮，比不渲染更难解释（ADR-0038 §五）。
 * 任务线程那张票落地时，把这里改成 `hit.task && hit.task.id` 即可，组件不用动。
 */
export function hitIsInteractive() {
  return false;
}
