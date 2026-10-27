<!-- 新建 Agent（旧落点 `studio/app.js:9647-9696` 的 `openAgentModal` / `submitAgent`，`:9719-9800` 的告知块）

     外壳是 `ModalShell`（ADR-0038 §五：弹窗的外壳与关闭语义只有一份），本组件只写内容。
     字段与旧页面逐字一致：名称 / 角色 / 模型提供商 / 模型名 / 系统提示；提交拼成
     `model_id = provider + ':' + model`（后端只认这一个形态，`provider:model`）。

     **远端数据流告知块**（票 #301 的补充裁定）：默认提供商是远端（`dashscope`），未确认时后端
     会把这个 Agent 判成「未启用」——建完就哑掉，而界面上没有一句解释。因此创建弹窗里保留这一块：
     它不是"顺手多做一块 UI"，而是让验收项"建 Agent 后 rail 成员名单出现新 Agent"真的成立。
     判定只有一条：**当前选中的 provider 是远端（不在 `localProviders` 里）且还没确认过**。
     Agent 详情页的同一确认面归 T10，本组件不碰。

     三条语义照旧页面：
     1. **校验交给后端**：名称或模型标识不合法则整体不创建，错误**留在弹窗内**、按钮可重试。
     2. **在途重复提交不产生两个 Agent**：提交键与告知确认键各自进 busy；`ModalShell` 的 `busy`
        同时挡掉 ESC / 遮罩 / 关闭键。
     3. 确认是**幂等**的（后端如此），因此重复点击不会写出两条记录；成功后弹窗**不关**——人还要
        继续填表单。 -->
<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import { hasKey, t } from '../i18n/index.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // 提供商清单的正本只有后端 `/api/workspace` 的 `model_providers` 一处（前端不留第二份常量表）。
  providers: { type: Array, default: () => [] },
  localProviders: { type: Array, default: () => [] },
  // 已确认过的远端提供商（`remote_consent` 的镜像）与「哪些数据会离开本机」的数据面。
  consents: { type: Array, default: () => [] },
  dataFaces: { type: Array, default: () => [] },
  submit: { type: Function, default: null },
  // 确认远端告知：由父组件给（它持有 store 与后端状态刷新），返回 promise。
  // 与 `submit` 同一条：拿 promise 才挡得住"在途重复点击"（`emit` 没有返回值）。
  acknowledge: { type: Function, default: null },
});

const emit = defineEmits(['update:open', 'created', 'reload-remote-flow']);

const name = ref('');
const role = ref('');
const provider = ref('');
const model = ref('');
const prompt = ref('');
const error = ref('');
const busy = ref(false);
const ackBusy = ref(false);
const ackError = ref('');

/** 提供商的中文/英文说法（词条表里有就翻译，没有就退回原词——与旧页面 `providerLabel` 同一条）。 */
function providerLabel(value) {
  const key = 'provider.' + value;
  return hasKey(key) ? t(key) : value;
}

const options = computed(() => props.providers);

const isLocal = computed(() => props.localProviders.includes(provider.value));
const acknowledged = computed(() =>
  props.consents.some((record) => record && record.provider === provider.value),
);
/** 告知块只在「远端且未确认」时出现：本机模型不离开本机，已确认过的也不再打扰。 */
const noticeVisible = computed(
  () => Boolean(provider.value) && !isLocal.value && !acknowledged.value,
);

const providerHint = computed(() => {
  if (!provider.value) return '';
  return isLocal.value ? t('agent.providerLocalHint') : t('agent.providerRemoteHint');
});

const canSubmit = computed(
  () => Boolean(name.value.trim() && provider.value && model.value.trim()) && !busy.value,
);

/** 打开时清空并回到清单首项：关掉再打开不该看到上一次的草稿，也不该停在一个已不存在的选项上。 */
function reset() {
  name.value = '';
  role.value = '';
  model.value = '';
  prompt.value = '';
  provider.value = options.value[0] || '';
  error.value = '';
  busy.value = false;
  ackBusy.value = false;
  ackError.value = '';
}

watch(
  () => props.open,
  (open) => {
    if (open) reset();
  },
);

onMounted(() => {
  if (props.open) reset();
});

/** 数据面读不到时不编文案：如实说"读不到"，按钮不可点（读不到就没有可告知的内容）。 */
const facesKnown = computed(() => props.dataFaces.length > 0);
const noticeBody = computed(() =>
  facesKnown.value
    ? t('agent.remoteBody', { faces: props.dataFaces.join(t('common.nameListSep')) })
    : t('agent.remoteBodyUnavailable'),
);

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
      role: role.value,
      modelId: provider.value + ':' + model.value.trim(),
      systemPrompt: prompt.value,
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

async function onAcknowledge() {
  if (ackBusy.value || !noticeVisible.value || !facesKnown.value || !props.acknowledge) return;
  ackBusy.value = true;
  ackError.value = '';
  try {
    await props.acknowledge(provider.value);
  } catch (err) {
    // 确认失败也留在弹窗内：人正看着这一块，失败原因就该在它旁边。
    ackError.value = err && err.message ? err.message : String(err);
  } finally {
    ackBusy.value = false;
  }
}

/** 打开时数据面可能还没读过（快照那条读是异步的）：没有就补读一次，读不到保持空态。 */
watch(
  () => props.open,
  (open) => {
    if (open && !props.dataFaces.length) emit('reload-remote-flow');
  },
  { immediate: true },
);
</script>

<template>
  <ModalShell
    id="agentOverlay"
    :open="open"
    :busy="busy"
    :title="t('agent.modalTitle')"
    :sub="t('agent.modalSub')"
    @update:open="emit('update:open', $event)"
  >
    <div class="field">
      <label for="agentNameInput">{{ t('agent.nameLabel') }}</label>
      <input
        id="agentNameInput"
        v-model="name"
        class="input"
        :placeholder="t('agent.namePlaceholder')"
        autocomplete="off"
        data-od-id="agent-name"
      />
    </div>
    <div class="field">
      <label for="agentRoleInput">{{ t('agent.roleLabel') }}</label>
      <input
        id="agentRoleInput"
        v-model="role"
        class="input"
        :placeholder="t('agent.rolePlaceholder')"
        autocomplete="off"
        data-od-id="agent-role"
      />
    </div>
    <div class="field">
      <label for="agentProviderSelect">{{ t('agent.providerLabel') }}</label>
      <select id="agentProviderSelect" v-model="provider" class="input" data-od-id="agent-provider">
        <option v-for="item in options" :key="item" :value="item">{{ providerLabel(item) }}</option>
      </select>
      <div class="hint" id="agentProviderHint">{{ providerHint }}</div>
    </div>
    <div class="field">
      <label for="agentModelInput">{{ t('agent.modelLabel') }}</label>
      <input
        id="agentModelInput"
        v-model="model"
        class="input"
        :placeholder="t('agent.modelPlaceholder')"
        autocomplete="off"
        data-od-id="agent-model"
      />
      <!-- 提示拆成「前缀 + provider:model + 后缀」三段：中间那个是标识符本身，不是文案。 -->
      <div class="hint">
        <span>{{ t('agent.modelHintPrefix') }}</span><span class="mono">provider:model</span><span>{{ t('agent.modelHintSuffix') }}</span>
      </div>
    </div>
    <div class="field">
      <label for="agentPromptInput">{{ t('agent.promptLabel') }}</label>
      <textarea
        id="agentPromptInput"
        v-model="prompt"
        class="textarea"
        :placeholder="t('agent.promptPlaceholder')"
        data-od-id="agent-prompt"
      ></textarea>
    </div>

    <div v-if="noticeVisible" class="remote-notice" id="agentRemoteNotice" data-od-id="remote-notice">
      <div class="remote-notice-head">
        <span class="dot"></span>
        <b id="agentRemoteTitle">{{ t('agent.remoteTitle', { provider: providerLabel(provider) }) }}</b>
      </div>
      <p class="remote-notice-body" id="agentRemoteBody">{{ noticeBody }}</p>
      <div class="remote-notice-foot">
        <span class="hint" id="agentRemoteHint">{{ t('agent.remoteHint') }}</span>
        <button
          class="btn btn-sm btn-primary"
          id="agentRemoteConfirm"
          type="button"
          data-od-id="remote-ack"
          :disabled="ackBusy || !facesKnown"
          @click="onAcknowledge"
        >
          {{ t('agent.remoteConfirm') }}
        </button>
      </div>
      <div v-if="ackError" class="form-error" id="agentRemoteError" role="alert">{{ ackError }}</div>
    </div>

    <div v-if="error" class="form-error" id="agentFormError" role="alert">{{ error }}</div>

    <template #foot>
      <span class="sep"></span>
      <button class="btn" id="agentCancel" type="button" @click="close">{{ t('common.cancel') }}</button>
      <button
        class="btn btn-primary"
        id="agentCreateSubmit"
        type="button"
        data-od-id="btn-create-agent"
        :disabled="!canSubmit"
        @click="onSubmit"
      >
        {{ t('agent.create') }}
      </button>
    </template>
  </ModalShell>
</template>
