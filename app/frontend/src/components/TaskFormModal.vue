<!-- 新建任务（旧落点 `studio/app.js:3779-3852` 的 `openTaskModal()` / `submitTask()`）

     外壳是 `ModalShell`（ADR-0038 §五：弹窗的外壳与关闭语义只有一份），本组件只写内容。
     字段收窄为「标题 + 所属频道」——所属频道是任务的**归属**，不是看板的筛选；指派在任务线程里
     用「指派」chip 完成（任务恰好属于一个频道，`CONTEXT.md` **任务**词条）。

     三条语义照旧页面：
     1. **空标题不发请求**：弹窗不是 `<form>`（按钮是 `type=button`），浏览器自带的 `required`
        提示不生效，因此要自己说出来（`task.titleRequired` / 无频道时的 `task.noChannel`）。
     2. **错误留在弹窗内**：关掉它等于把人填的内容和失败原因一起丢掉。
     3. 建成后由父组件决定落点（旧页面把频道流里的指针消息吸收掉并刷新看板，这里同一条）。 -->
<script setup>
import { computed, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // 可选频道（空工作台时为空数组：那就禁用提交并说「先创建频道」）。
  channels: { type: Array, default: () => [] },
  // 预选频道（当前 target 是频道时就是它）。
  defaultChannelId: { type: String, default: '' },
  online: { type: Boolean, default: true },
  // 提交动作由父组件给：它持有 store 与「建成后切到哪一档」的接线。
  submit: { type: Function, default: null },
});

const emit = defineEmits(['update:open', 'created']);

const title = ref('');
const channelId = ref('');
const error = ref('');
const busy = ref(false);

const canSubmit = computed(() => props.online && props.channels.length > 0 && Boolean(title.value.trim()) && !busy.value);

/** 打开时清空并预选当前频道：关掉再打开不该看到上一次填了一半的草稿。 */
watch(
  () => props.open,
  (open) => {
    if (!open) return;
    title.value = '';
    error.value = '';
    busy.value = false;
    const fallback = props.defaultChannelId && props.channels.some((item) => item.id === props.defaultChannelId)
      ? props.defaultChannelId
      : (props.channels[0] ? props.channels[0].id : '');
    channelId.value = fallback;
  },
);

function close() {
  emit('update:open', false);
}

async function onSubmit() {
  if (busy.value) return;
  if (!props.channels.length) {
    error.value = t('task.noChannel');
    return;
  }
  if (!title.value.trim()) {
    error.value = t('task.titleRequired');
    return;
  }
  busy.value = true;
  error.value = '';
  try {
    const payload = await props.submit({ channelId: channelId.value, title: title.value.trim() });
    close();
    emit('created', payload);
  } catch (err) {
    error.value = err && err.message ? err.message : String(err);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell
    id="taskOverlay"
    :open="open"
    :busy="busy"
    :title="t('task.modalTitle')"
    :sub="t('task.modalSub')"
    close-id="taskModalClose"
    @update:open="emit('update:open', $event)"
  >
    <div class="field">
      <label for="taskCreateTitle">{{ t('task.titleLabel') }}</label>
      <input
        id="taskCreateTitle"
        v-model="title"
        class="input"
        data-od-id="task-create-title"
        :placeholder="t('task.titlePlaceholder')"
        autocomplete="off"
      />
    </div>
    <div class="field">
      <label for="taskCreateChannel">{{ t('task.channelLabel') }}</label>
      <select
        id="taskCreateChannel"
        v-model="channelId"
        class="input"
        data-od-id="task-create-channel"
        :disabled="!channels.length"
      >
        <option v-for="channel in channels" :key="channel.id" :value="channel.id">#{{ channel.name }}</option>
      </select>
      <div class="hint">{{ channels.length ? t('task.channelHint') : t('task.createChannelFirst') }}</div>
    </div>
    <div v-if="error" class="form-error" role="alert" data-od-id="task-form-error">{{ error }}</div>

    <template #foot>
      <span class="sep"></span>
      <button class="btn" id="taskCancel" type="button" @click="close">{{ t('common.cancel') }}</button>
      <button
        class="btn btn-primary"
        id="taskCreateSubmit"
        type="button"
        data-od-id="btn-create-task"
        :disabled="!canSubmit"
        @click="onSubmit"
      >{{ t('task.create') }}</button>
    </template>
  </ModalShell>
</template>
