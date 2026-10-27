<!-- 提醒弹窗（旧 `studio/app.js:969-1031` 的 `openRemindersModal` / `renderRemindersModal`）

     内容照旧页面的 `#remindersOverlay` 段（`studio/index.html:835-871`）：列表 `.reminder-row`
     （文本 · ` · #归属频道` · 状态后缀 · 删除）+ 空态 + 新增控件（文本 + **日期 + 钟点两个原生输入**）。
     外壳用 `ModalShell`（`wide` 档），本组件只写内容。

     四条判据（旧实现的注释逐条记着，这里只搬结论）：
     1. **圆点与列表都只读后端**：镜像在 `stores/reminders.js`，本组件不拼行、不判定到没到点。
     2. **状态后缀**：`pending` 时它兼作这一行的到期时刻呈现（完整后端串、不切片）；
        `fired` / `missed` 显示词条文案。到期时刻不再单立一列——单立会让 `pending` 行把同一时刻
        写两遍（用户 2026-10-17 对 C16 行形态的裁定）。只消费后端给的 `state`，认不出的取值落回
        到期时刻那一档：不猜测第三种语义。
     3. **到期时刻是两个原生输入拼出来的** `YYYY-MM-DD HH:MM`（ADR-0017 §二把时间语义升成
        「日期 + 钟点」）。合法形状由后端裁决，前端不替它猜：空的那一半不补默认值。
     4. **归属 = 当前打开的频道**；私信或空工作台里没有频道可归属 ⇒ 显式传 `null`
        （后端把它读作「不归属任何频道」，与设置面那条「显式 null 整体拒绝」不同）。

     删除键用 `×` 而不是原型 `:1570` 那个复用「新增」加号图标的形状：图标与可访问名矛盾会让
     图标 / 读屏用户读到相反的动作。 -->
<script setup>
import { computed, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import { t } from '../i18n/index.js';
import { localDateString } from '../lib/format.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // 提醒的本地镜像（`createReminderStore()` 的返回值）：读 / 写都走它，组件不自己发请求。
  reminders: { type: Object, required: true },
  // 归属频道：当前打开的频道，没有就是 `null`（私信 / 空工作台）。
  channelId: { type: String, default: null },
});

const emit = defineEmits(['update:open']);

/** 两个状态词条键（`pending` 没有标签键：它的后缀就是到期时刻本身）。 */
const STATE_LABEL_KEYS = { fired: 'reminder.stateFired', missed: 'reminder.stateMissed' };

const text = ref('');
const date = ref(todayDateValue());
const time = ref('09:00');
const error = ref('');
const busy = ref(false);

/**
 * 本机当天日期（`YYYY-MM-DD`）：日期格的默认值。
 *
 * 只取「年 / 月 / 日」三段拼串，不做任何时区换算——到期时刻按**本机本地时间**解释是后端的口径
 * （C7）；这里只是把「今天」填进原生控件，不建立第二时间正本。
 */
function todayDateValue() {
  return localDateString(new Date());
}

const rows = computed(() => props.reminders.state.reminders);

/** 一条提醒的状态后缀：`pending` 给到期时刻，其余给词条文案（认不出的取值落回到期时刻）。 */
function stateNode(reminder) {
  const value = reminder.state || 'pending';
  const labelKey = Object.prototype.hasOwnProperty.call(STATE_LABEL_KEYS, value)
    ? STATE_LABEL_KEYS[value]
    : null;
  return { state: value, text: labelKey ? t(labelKey) : reminder.time };
}

/**
 * 每次打开都把「新提醒」的取值重置到默认：日期回到今天、错误话收回。
 *
 * 这是「新提醒」的默认语义，不是记住上一次的取值——与旧 `openRemindersModal()` 同一条。
 * 钟点**不重置**（`type="time"` 的默认值就是 `09:00`，连设两条不用重填）。
 */
watch(
  () => props.open,
  (open) => {
    if (!open) return;
    error.value = '';
    date.value = todayDateValue();
    // 打开时按后端正本再读一次：别的浏览器或上一次会话设的提醒在这里出现。
    // 读不到不关弹窗，把话留在弹窗里（正本读不到时界面不能假装列表是完整的）。
    props.reminders.load().catch((err) => {
      error.value = t('reminder.loadFailed', { reason: err.message });
    });
  },
  { immediate: true },
);

/**
 * 新增一条提醒。
 *
 * 拼成 `YYYY-MM-DD HH:MM` **一个串**交给后端（ADR-0017 §二）。空的那一半不补默认值，形状不对
 * 由后端回 `validation_failed`，界面把后端的话留在弹窗里。
 */
async function submit() {
  const due = [date.value, time.value].join(' ').trim();
  busy.value = true;
  try {
    await props.reminders.create({
      text: text.value,
      time: due,
      channel_id: props.channelId || null,
    });
    error.value = '';
    // 只清正文；日期回到今天、钟点保留当前取值（连设两条不用重填）。
    text.value = '';
    date.value = todayDateValue();
  } catch (err) {
    error.value = t('reminder.saveFailed', { reason: err.message });
  } finally {
    busy.value = false;
  }
}

async function remove(reminderId) {
  try {
    await props.reminders.remove(reminderId);
    error.value = '';
  } catch (err) {
    error.value = t('reminder.deleteFailed', { reason: err.message });
  }
}

function close() {
  emit('update:open', false);
}
</script>

<template>
  <ModalShell
    id="remindersOverlay"
    :open="open"
    wide
    :title="t('reminder.modalTitle')"
    :sub="t('reminder.modalSub')"
    :close-label="t('reminder.closeAria')"
    @update:open="emit('update:open', $event)"
  >
    <div id="reminderList" data-od-id="reminder-list">
      <div v-if="!rows.length" class="empty">
        <b>{{ t('reminder.emptyTitle') }}</b>
        <p>{{ t('reminder.emptyBody') }}</p>
      </div>
      <template v-else>
        <div
          v-for="reminder in rows"
          :key="reminder.id"
          class="reminder-row"
          :data-reminder="reminder.id"
        >
          <span class="grow">
            {{ reminder.text }}<span v-if="reminder.channel_id" class="ch"> · #{{ reminder.channel_name || reminder.channel_id }}</span>
          </span>
          <span class="state" :data-state="stateNode(reminder).state">{{ stateNode(reminder).text }}</span>
          <button
            class="icon-btn"
            type="button"
            :data-delete-reminder="reminder.id"
            data-od-id="reminder-delete"
            :aria-label="t('reminder.deleteAria')"
            @click="remove(reminder.id)"
          >×</button>
        </div>
      </template>
    </div>

    <div class="field reminder-add">
      <label for="reminderText">{{ t('reminder.newLabel') }}</label>
      <div class="reminder-add-row">
        <input
          id="reminderText"
          v-model="text"
          class="input"
          type="text"
          autocomplete="off"
          data-od-id="reminder-text"
          :placeholder="t('reminder.textLabel')"
          :aria-label="t('reminder.textLabel')"
        />
        <!-- 日期 / 钟点两个原生输入（对原型的显式偏离：原型只有 `type="time"`，说不出「明天 9 点」）。
             它们自己没有可读标签，因此各给一个 `aria-label`。 -->
        <input
          v-model="date"
          class="input reminder-date"
          type="date"
          data-od-id="reminder-date"
          :aria-label="t('reminder.dateLabel')"
        />
        <input
          v-model="time"
          class="input reminder-time"
          type="time"
          data-od-id="reminder-time"
          :aria-label="t('reminder.timeLabel')"
        />
      </div>
    </div>

    <div v-if="error" class="form-error" id="reminderFormError" role="alert">{{ error }}</div>

    <template #foot>
      <span class="sep"></span>
      <button class="btn" type="button" @click="close">{{ t('common.cancel') }}</button>
      <button
        class="btn btn-primary"
        type="button"
        data-od-id="reminder-add"
        :disabled="busy"
        @click="submit"
      >{{ t('reminder.add') }}</button>
    </template>
  </ModalShell>
</template>
