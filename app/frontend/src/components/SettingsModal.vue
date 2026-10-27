<!-- 设置弹窗（旧 `studio/app.js:862-968` 的 `openSettingsModal` / `renderSettingsModal` / `saveSettings`）

     内容照旧页面的 `#settingsOverlay` 段（`studio/index.html:762-818`）：语言下拉 + 消息密度两个
     `.radio-card` + 主题三个 `.radio-card`，类名与 `data-od-id` 逐字沿用。外壳用 `ModalShell`。

     **`applySettings` 是本组件接活的那个死入口**（阶段一自查记录：「设置弹窗能顺手接上
     `applySettings` 写回（现在是死入口）」）：

     - 写路径只有一条：`PATCH /api/settings` → 成功 ⇒ 调 `applySettings(响应.settings)` 铺根元素
       （`<html lang>` / `data-theme` / `data-density`）；
     - **绝不**给 `mutable.settings` 挂 `watch`。`app-context.js` 里 `applySettings` 的文档注释写明
       那段 deep watcher 曾自触发成无限循环、页面白屏卡死；删过的东西不准加回来。
       一条用例（`tests/rail-modals.test.js` 里「保存一次只铺一次根元素」）守着这条；
     - 失败时**不**改本地镜像、不动根元素：界面不能声称一个没落库的档位（与旧 `saveSettings` 同一条），
       后端的话留在弹窗里，弹窗不关。 -->
<script setup>
import { computed, ref, watch } from 'vue';
import ModalShell from './ModalShell.vue';
import { t, locales } from '../i18n/index.js';
import { api } from '../lib/api.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // 当前设置（`state.settings` 的镜像，正本在后端）：三个档位的选中态只读它。
  settings: { type: Object, required: true },
  // 铺根元素的唯一写入口（`createAppContext().applySettings`）。**由父组件传进来**，
  // 本组件不自己写 `document.documentElement`：否则会出现第二个写入口。
  applySettings: { type: Function, required: true },
});

const emit = defineEmits(['update:open']);

/** 语言选项名按**当前语言**给出（词条 `settings.languageZh` / `settings.languageEn`）。 */
const LANGUAGE_KEYS = { 'zh-CN': 'settings.languageZh', en: 'settings.languageEn' };

const error = ref('');
const busy = ref(false);

const density = computed(() => [
  { value: 'comfortable', label: t('settings.densityComfortable'), hint: t('settings.densityComfortableHint'), odId: 'density-comfortable' },
  { value: 'compact', label: t('settings.densityCompact'), hint: t('settings.densityCompactHint'), odId: 'density-compact' },
]);

const themes = computed(() => [
  { value: 'light', label: t('settings.themeLight'), hint: t('settings.themeLightHint'), odId: 'theme-light' },
  { value: 'dark', label: t('settings.themeDark'), hint: t('settings.themeDarkHint'), odId: 'theme-dark' },
  { value: 'system', label: t('settings.themeSystem'), hint: t('settings.themeSystemHint'), odId: 'theme-system' },
]);

const localeOptions = computed(() => locales().map((locale) => ({
  value: locale,
  label: t(LANGUAGE_KEYS[locale] || 'settings.languageLabel'),
})));

// 每次打开把上一次的错误话收回去（旧 `openSettingsModal()` 的第一件事）。
watch(
  () => props.open,
  (open) => {
    if (open) error.value = '';
  },
  { immediate: true },
);

/**
 * 写一个档位：先写后端（正本），成功后按**返回的设置**铺界面。
 *
 * 一次点击只发一次 PATCH、只铺一次根元素——`applySettings` 内部不会反过来触发这里
 * （它不挂 watcher，见文件头）。
 */
async function save(patch) {
  busy.value = true;
  try {
    const payload = await api('/api/settings', { method: 'PATCH', body: patch });
    error.value = '';
    props.applySettings(payload.settings);
  } catch (err) {
    error.value = t('settings.saveFailed', { reason: err.message });
  } finally {
    busy.value = false;
  }
}

function close() {
  emit('update:open', false);
}
</script>

<template>
  <ModalShell
    id="settingsOverlay"
    :open="open"
    :title="t('settings.title')"
    :sub="t('settings.sub')"
    :close-label="t('settings.closeAria')"
    @update:open="emit('update:open', $event)"
  >
    <div class="field">
      <label for="settingsLocaleSelect">{{ t('settings.languageLabel') }}</label>
      <select
        id="settingsLocaleSelect"
        class="input"
        data-od-id="settings-locale"
        :value="settings.locale"
        :disabled="busy"
        @change="save({ locale: $event.target.value })"
      >
        <option v-for="item in localeOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
      </select>
    </div>

    <div class="field">
      <label>{{ t('settings.densityLabel') }}</label>
      <div class="radio-row" id="settingsDensity" role="group" data-od-id="settings-density">
        <button
          v-for="item in density"
          :key="item.value"
          class="radio-card"
          type="button"
          :class="{ 'is-on': settings.message_density === item.value }"
          :data-density="item.value"
          :data-od-id="item.odId"
          :aria-pressed="settings.message_density === item.value ? 'true' : 'false'"
          :disabled="busy"
          @click="save({ message_density: item.value })"
        >
          <b>{{ item.label }}</b>
          <span>{{ item.hint }}</span>
        </button>
      </div>
    </div>

    <div class="field">
      <label>{{ t('settings.themeLabel') }}</label>
      <div class="radio-row" id="settingsTheme" role="group" data-od-id="settings-theme">
        <button
          v-for="item in themes"
          :key="item.value"
          class="radio-card"
          type="button"
          :class="{ 'is-on': settings.theme === item.value }"
          :data-theme="item.value"
          :data-od-id="item.odId"
          :aria-pressed="settings.theme === item.value ? 'true' : 'false'"
          :disabled="busy"
          @click="save({ theme: item.value })"
        >
          <b>{{ item.label }}</b>
          <span>{{ item.hint }}</span>
        </button>
      </div>
    </div>

    <div v-if="error" class="form-error" id="settingsFormError" role="alert">{{ error }}</div>

    <template #foot>
      <span class="sep"></span>
      <button class="btn btn-primary" type="button" data-od-id="settings-done" @click="close">
        {{ t('settings.done') }}
      </button>
    </template>
  </ModalShell>
</template>
