/* 任务看板的呈现派生（纯函数，读后端载荷，不做判定）

   两张表都不在这里：

   1. **状态迁移表**：`allowed_transitions` 由后端从 `TASK_STATUS_TRANSITIONS` 派生并随任务载荷出行
      （`db.allowed_task_transitions()`，`CONTEXT.md` **任务看板**「前端不持迁移表副本」）。
      本文件只**读**那个字段；字段缺失就是空集合（一个目标都不给），不回退到一份本地表。
   2. **任务状态词表**：那是后端 `TASK_STATUSES`。前端需要的只是**列的呈现顺序**
      （下面这个 `TASK_STATUS_ORDER`，旧页面 `app.js:114` 同一个序列）——它决定五列的左右次序，
      不是一张「什么状态能到什么状态」的表。

   状态词的说法走词条 `task.status.<status>`、迁移的说法走词条 `task.transition.<target>`
   （与线程 chips 同一套说法）；词条缺失就退回原词，不伪造译文。 */
import { hasKey, t } from '../i18n/index.js';

/** 看板五列的固定顺序（呈现顺序，不是迁移表）。终态 `closed` 在最右。 */
export const TASK_STATUS_ORDER = ['todo', 'in_progress', 'in_review', 'done', 'closed'];

/** 拖拽结束后抑制「内核补发的那个 click」的时间窗（旧页面 `suppressClickUntil` 同值）。 */
export const SUPPRESS_CLICK_MS = 250;

export function taskStatusLabel(status) {
  const key = 'task.status.' + status;
  return hasKey(key) ? t(key) : status;
}

export function taskTransitionLabel(target) {
  const key = 'task.transition.' + target;
  return hasKey(key) ? t(key) : taskStatusLabel(target);
}

/** 一份任务载荷的全部合法迁移目标（**唯一**来源是载荷；没有字段 ⇒ 空集合）。 */
export function allowedTransitions(task) {
  return Array.isArray(task && task.allowed_transitions) ? task.allowed_transitions : [];
}

/** 目标是不是这份载荷的合法迁移之一（**唯一**判据来源）。 */
export function canTransition(task, target) {
  return allowedTransitions(task).includes(target);
}

/** 「我的任务」= 指派给我（`assignee_member_id === owner`）或还没人认领。
    它**不是**「我创建的任务」——任务行不记录创建者归属。 */
export function isMyTask(task, ownerId) {
  return !task.assignee_member_id || task.assignee_member_id === ownerId;
}

/** 卡片的 mono 序号：`#` + 任务 id 尾 6 位（与旧页面 `card-num` 同一形态）。 */
export function taskNumber(taskId) {
  return '#' + String(taskId == null ? '' : taskId).slice(-6);
}

/** 未读角标：>99 显示 `99+`，其余是数字本身（`aria-label` 仍报真实条数）。 */
export function unreadBadge(count) {
  const value = Number(count) || 0;
  return value > 99 ? '99+' : String(value);
}
