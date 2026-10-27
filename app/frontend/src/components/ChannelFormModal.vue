<!-- 新建频道（旧落点 `studio/app.js:9572-9646` 的 `openChannelModal` / `submitChannel`）

     外壳是 `ModalShell`（ADR-0038 §五：弹窗的外壳与关闭语义只有一份），本组件只写内容。
     字段与旧页面逐字一致（名称必有、描述可空、初始成员是可勾选的 Agent 胶囊），类名与
     `data-od-id` 也沿用旧页面（`#channelOverlay` / `.member-pick` / `#channelFormError` /
     `btn-create-channel`）：自动化取证与设计系统都按这些名字定位。

     三条语义照旧页面：
     1. **校验交给后端**：任一字段不合法就整体不创建，错误**留在弹窗内**（不关弹窗、不吞错），
        按钮退出 busy 后可重试——重建一次表单比猜哪条规则拦住了它更诚实。
     2. **在途重复提交不产生两个频道**：提交键进 `busy`，`ModalShell` 的 `busy` 同时挡掉
        ESC / 遮罩 / 关闭键，于是只有第一次会成功落地。
     3. 成功后**自动切过去**：这一步在父组件（`selectTarget` 是 The 唯一入口），本组件只把
        后端回带的 `channel` 交出去。 -->
<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import MemberAvatar from './MemberAvatar.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // Agent 候选（本机全名单，不按频道过滤——新频道里本来就没有成员）。
  agents: { type: Array, default: () => [] },
  // 提交动作由父组件给：它持有 store 与「建完切过去」的接线（`selectTarget`）。
  // 抛错 ⇒ 错误留在弹窗内；返回值是后端载荷（含 `channel`）。
  submit: { type: Function, default: null },
});

const emit = defineEmits(['update:open', 'created']);

const name = ref('');
const description = ref('');
const picked = ref([]);
const error = ref('');
const busy = ref(false);

const canSubmit = computed(() => Boolean(name.value.trim()) && !busy.value);

/** 打开时清空：关掉再打开不该看到上一次填了一半的草稿（旧页面 `openChannelModal` 同一条）。 */
watch(
  () => props.open,
  (open) => {
    if (!open) return;
    name.value = '';
    description.value = '';
    picked.value = [];
    error.value = '';
    busy.value = false;
  },
);

function toggle(memberId) {
  if (picked.value.includes(memberId)) picked.value = picked.value.filter((id) => id !== memberId);
  else picked.value = picked.value.concat([memberId]);
}

function close() {
  emit('update:open', false);
}

async function onSubmit() {
  if (!canSubmit.value) return;
  busy.value = true;
  error.value = '';
  try {
    const payload = await props.submit({
      name: name.value,
      description: description.value,
      memberIds: picked.value,
    });
    close();
    emit('created', payload);
  } catch (err) {
    // 错误**留在弹窗内**：关掉它等于把人填的内容和失败原因一起丢掉。
    error.value = err && err.message ? err.message : String(err);
    await nextTick();
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell
    id="channelOverlay"
    :open="open"
    :busy="busy"
    :title="t('channel.modalTitle')"
    :sub="t('channel.modalSub')"
    @update:open="emit('update:open', $event)"
  >
    <div class="field">
      <label for="channelNameInput">{{ t('channel.nameLabel') }}</label>
      <input
        id="channelNameInput"
        v-model="name"
        class="input"
        :placeholder="t('channel.namePlaceholder')"
        autocomplete="off"
        data-od-id="channel-name"
      />
    </div>
    <div class="field">
      <label for="channelDescInput">{{ t('channel.descLabel') }}</label>
      <textarea
        id="channelDescInput"
        v-model="description"
        class="textarea"
        :placeholder="t('channel.descPlaceholder')"
        data-od-id="channel-desc"
      ></textarea>
    </div>
    <div class="field">
      <label>{{ t('channel.membersLabel') }}</label>
      <div id="memberPicker" class="member-pick" data-od-id="member-picker">
        <div v-if="!agents.length" class="member-pick-empty" data-od-id="member-picker-empty">
          {{ t('channel.pickEmpty') }}
        </div>
        <template v-else>
          <button
            v-for="agent in agents"
            :key="agent.id"
            class="member-opt"
            :class="{ 'is-on': picked.includes(agent.id) }"
            type="button"
            :data-pick="agent.id"
            :aria-pressed="picked.includes(agent.id) ? 'true' : 'false'"
            @click="toggle(agent.id)"
          >
            <MemberAvatar :name="agent.display_name" :slot="agent.avatar_slot" size="sm" />
            {{ agent.display_name }}
          </button>
        </template>
      </div>
      <div class="hint" id="memberPickerHint">{{ t('channel.membersHint') }}</div>
    </div>
    <div v-if="error" class="form-error" id="channelFormError" role="alert">{{ error }}</div>

    <template #foot>
      <span class="sep"></span>
      <button class="btn" id="channelCancel" type="button" @click="close">{{ t('common.cancel') }}</button>
      <button
        class="btn btn-primary"
        id="channelCreateSubmit"
        type="button"
        data-od-id="btn-create-channel"
        :disabled="!canSubmit"
        @click="onSubmit"
      >
        {{ t('channel.create') }}
      </button>
    </template>
  </ModalShell>
</template>
