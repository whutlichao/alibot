<!-- ⌘K 命令面板（`#cmdOverlay`）

     旧落点：`studio/index.html:501-511` 的 `#cmdOverlay` 段 + `studio/app.js:755-800` 的
     `commandList` / `renderCmd` / `openCmd` / `closeCmd` / `runCmd`。

     **不套 `ModalShell`**（协调者裁定，ADR-0038 §五）：它的根是 `.overlay` + `.cmd`，不是
     `.overlay` + `.modal`，关闭语义（ESC 归 App 的层序表第一层）与 z-index 档位都与模态栈不同。
     因此它不进 `lib/modal-stack.js`，也不需要遮罩点击的 `busy` 判据。

     命令集由 App 组（那里握着频道名单、`selectTarget`、两张表单与 dock 的开合），本组件只做
     **呈现与筛选**：
     - 分组标题走词条（`cmd.groupJump` / `cmd.groupAction` / `cmd.groupChannels`）；
     - 每条命令用**稳定 id**（`data-cmd`）而不是标签做归属——频道名可能与某条命令同名，标签不是键；
     - **只有真绑定的快捷键**才印 `kbd`：本版是 `M`（消息视图）与 `T`（任务看板）；
     - 指向**未迁面**的命令是 `disabled` 条目（`title` 说明理由，词条 `app.notMigrated`），
       不渲染成按下去什么都不发生的假入口（ADR-0038 §五）。

     交互照旧页面：输入框筛选（子串、大小写不敏感）、Enter 跑**第一条可执行的**、点击跑那一条、
     点遮罩空白处关闭。向上 / 向下选择键在旧页面就不存在（原型 `:1839` 只有 Enter），本组件不新造。 -->
<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  open: { type: Boolean, default: false },
  // 命令表：`{ id, group, label, kbd?, disabled?, run }`。`group` 是词条键，`label` 已是译文。
  commands: { type: Array, default: () => [] },
  // 命令为什么不能用的说明（`disabled` 条目的 `title`）。
  disabledReason: { type: String, default: '' },
});

const emit = defineEmits(['update:open', 'run']);

const input = ref(null);
const filter = ref('');

/** 筛选：子串、大小写不敏感（旧 `renderCmd()` 用 `label.toLowerCase().includes(query)`）。 */
const visible = computed(() => {
  const query = filter.value.toLowerCase();
  return props.commands.filter((command) => String(command.label).toLowerCase().includes(query));
});

/** 按分组切段（连续同组归一段，顺序即命令表顺序——与旧 `renderCmd()` 的 `group !== last` 同一条）。 */
const groups = computed(() => {
  const out = [];
  visible.value.forEach((command) => {
    const last = out[out.length - 1];
    if (last && last.group === command.group) last.items.push(command);
    else out.push({ group: command.group, items: [command] });
  });
  return out;
});

/** 高亮落在第一条（旧页面给第一条加 `is-active`；Enter 跑的就是它）。 */
const firstId = computed(() => (visible.value.length ? visible.value[0].id : null));

/** Enter 跑**第一条可执行的**：禁用条目占着高亮位置时不能把 Enter 变成一次空按。 */
function runFirst() {
  const command = visible.value.find((item) => !item.disabled);
  if (command) emit('run', command.id);
}

function close() {
  emit('update:open', false);
}

function onBackdrop(event) {
  // 只有点在遮罩**自身**上才关（点 `.cmd` 内部任一处都不关），与旧 `#cmdOverlay` 的判据同一条。
  if (event.target === event.currentTarget) close();
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    filter.value = '';
    await nextTick();
    if (input.value) input.value.focus();
  },
  { immediate: true },
);
</script>

<template>
  <div v-if="open" id="cmdOverlay" class="overlay" @click="onBackdrop">
    <div class="cmd" role="dialog" aria-modal="true" :aria-label="t('cmd.ariaLabel')">
      <div class="cmd-field">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--faint)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="m21 21-4.34-4.34" /><circle cx="11" cy="11" r="8" />
        </svg>
        <input
          id="cmdInput"
          ref="input"
          v-model="filter"
          :placeholder="t('cmd.placeholder')"
          :aria-label="t('cmd.placeholder')"
          autocomplete="off"
          data-od-id="cmd-input"
          @keydown.enter.prevent="runFirst"
        />
        <kbd class="kbd">ESC</kbd>
      </div>
      <div id="cmdList" class="cmd-list" data-od-id="cmd-list">
        <div v-if="!visible.length" class="cmd-group">{{ t('cmd.empty') }}</div>
        <template v-for="section in groups" :key="section.group">
          <div class="cmd-group">{{ t(section.group) }}</div>
          <button
            v-for="command in section.items"
            :key="command.id"
            class="cmd-item"
            :class="{ 'is-active': command.id === firstId }"
            type="button"
            :data-cmd="command.id"
            :disabled="Boolean(command.disabled)"
            :title="command.disabled ? disabledReason : undefined"
            @click="emit('run', command.id)"
          >
            <span class="grow">{{ command.label }}</span>
            <span v-if="command.kbd" class="kbd">{{ command.kbd }}</span>
          </button>
        </template>
      </div>
    </div>
  </div>
</template>
