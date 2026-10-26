/* 纯呈现辅助（无状态、无请求）：头像 / 时间 / 状态词

   这些函数与旧实现（`studio/app.js` 的 `avatarClass` / `initial` / `timeLabel` / `dayLabel` /
   `statusLabel`）逐字同义——它们决定的是**同一份后端事实怎么显示**，因此不能各写一套：
   同一个成员在两版页面里必须拿到同一个色位、同一个首字母。 */
import { hasKey, t } from '../i18n/index.js';

/** 头像色位：`avatar_slot` → `--av-0…--av-4`（负数与越界都折回 0…4）。 */
export function avatarClass(slot) {
  const index = Number.isInteger(slot) ? slot : 0;
  return 'av-' + (((index % 5) + 5) % 5);
}

/** 头像上的首字母（按**码点**取，`Array.from` 不会把生僻字劈成两个 UTF-16 单元）。 */
export function initial(name) {
  const text = String(name || '?').trim();
  return text ? Array.from(text)[0].toUpperCase() : '?';
}

/** 头像的 class 串（`size` 为 `sm` 时是小号）。 */
export function avatarClassName(size, slot) {
  return 'avatar ' + (size ? size + ' ' : '') + avatarClass(slot);
}

/** 四态状态词的界面说法（键名与后端状态词同名；词条表没有就退回原词，不伪造译文）。 */
export function statusLabel(status) {
  const key = 'status.' + status;
  return hasKey(key) ? t(key) : status;
}

/** 轮次结果状态的说法（摘要行用）。 */
export function outcomeLabel(status) {
  const key = 'outcome.' + status + '.label';
  return hasKey(key) ? t(key) : status;
}

export function outcomeNote(status) {
  const key = 'outcome.' + status + '.note';
  return hasKey(key) ? t(key) : status;
}

/**
 * 附件体积的界面说法。
 *
 * 单位符号（`B` / `KB` / `MB`）与语言无关，因此**不进词条表**——词条表是「按语言取值」的集合，
 * 里面没有裸符号的位置（与旧实现同一条理由）。数值部分取一位小数。
 */
export function formatBytes(size) {
  const bytes = Number(size) || 0;
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/** 时间列（`HH:MM`）；解析不出来时给一条明确占位，不抛错。 */
export function timeLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '--:--';
  return String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
}

/** 本机日期的 `YYYY-MM-DD` 串。 */
export function localDateString(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

/** 日分隔行的文案：今天说「今天」，其余给本机日期。 */
export function dayLabel(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return t('time.unknown');
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return t('time.today');
  return localDateString(date);
}
