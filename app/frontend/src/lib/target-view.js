/* 当前 target 的呈现派生（纯函数，读后端镜像，不做判定）

   它回答两个问题，两个答案都只从**后端事实**推导：
   1. composer 的占位文案是哪一条（归档 > 私信 > 频道）；
   2. 消息页签的计数是**未读**（后端未读口径），不是流里条数。

   放进独立文件是因为「同一个事实在两处呈现」时两处必须读同一个函数——旧实现里
   `renderHeader()` 与 `renderTabs()` 各写一份判据，改一处就会漂。 */
import { t } from '../i18n/index.js';

/** 当前 target 是不是已归档私信（判据只有后端事实 `peer.deleted_at`）。 */
export function isArchivedDm(target) {
  return Boolean(target && target.kind === 'dm' && target.peer && target.peer.deleted_at);
}

export function targetName(target) {
  if (!target) return '';
  return target.kind === 'dm' ? target.peer.display_name : target.name;
}

/** composer 的占位文案：归档 > 私信 > 频道。 */
export function composerPlaceholder(target) {
  if (!target) return t('composer.placeholder');
  const name = targetName(target);
  if (isArchivedDm(target)) return t('header.dmArchivedPlaceholder');
  if (target.kind === 'dm') return t('header.dmPlaceholder', { name });
  return t('header.channelPlaceholder', { name });
}

/** 频道头那句描述：归档私信说「只读归档」，私信说「与谁的两方私信」，频道用后端给的描述。
    `ChannelHeader.vue` 曾经在这里手写了一份同判据的第二份实现（review 抓到），现在两边都调它。 */
export function targetDescription(target) {
  if (!target) return '';
  if (isArchivedDm(target)) return t('header.dmArchivedDesc');
  if (target.kind === 'dm') return t('header.dmDesc', { name: targetName(target) });
  return target.description || '';
}

/** 「消息」页签的计数：**未读**条数（后端未读口径，与左栏徽标同一份事实）。 */
export function messageTabCount(target) {
  return target ? Number(target.unread_count) || 0 : 0;
}
