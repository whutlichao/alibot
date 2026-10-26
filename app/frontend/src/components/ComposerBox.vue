<!-- 主 composer（频道 / 私信 / 任务线程共用同一套输入契约）

     **工具行只剩「@提及」一个键**：附件上传与「作为任务发送」属于尚未迁到 Vue 版的两个面
     （附件上传 / 任务看板），因此这一版**不渲染**那两个按钮——旧实现自己立的纪律是
     「不留一个按下去什么都不发生的按钮」，那条在这里同样成立。它们随各自的票一起回来。

     契约（沿用旧实现，逐条都有原因）：
     - Enter 发送、Shift+Enter 换行；有选区时不猜提及位置；
     - 发送键的可用性 = 「有正文」∧「后端在线」∧「有当前 target」∧「不是已归档私信」；
       Enter 走的是同一条闸门（键盘用户不能绕过禁用态）；
     - 发送后正文清空、候选清单收起、输入框高度回到单行；
     - 已归档私信只读：输入框与工具行一起禁用，提示换成 `composer.archivedHint`。

     候选清单的出现/收起由**输入框的实时读数**决定（`mentionTriggerAt`），不在这里另写一套规则。 -->
<script setup>
import { computed, nextTick, ref } from 'vue';
import MentionPicker from './MentionPicker.vue';
import { t } from '../i18n/index.js';
import {
  insertMention,
  mentionInsertable,
  mentionTriggerAt,
  mentionTriggerSignature,
} from '../lib/mentions.js';

const props = defineProps({
  // 当前 target 的占位文案（频道 / 私信 / 归档三态由父组件按后端事实给）。
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  online: { type: Boolean, default: true },
  archived: { type: Boolean, default: false },
  // 候选来源：`null` = 还没读到；空数组 = 真的没有成员。
  candidates: { type: Array, default: null },
  candidatesUnavailable: { type: Boolean, default: false },
});

const emit = defineEmits(['send', 'mention-request', 'mention-unsupported']);

const input = ref(null);
const body = ref('');
const picker = ref({ open: false, empty: null, items: [], activeIndex: 0, trigger: null, signature: '' });
// 被 Esc 关掉 / 刚插入完的触发点签名：同一个触发点不再自动弹出。
const dismissed = ref('');

const canSend = computed(() => Boolean(body.value.trim()) && props.online && !props.disabled);

/** 提示行：清单开着时 Enter 不再是「发送」，措辞必须跟着换（否则界面承诺了一个按键做不到的事）。 */
const hint = computed(() => {
  if (props.archived) return t('composer.archivedHint');
  if (!picker.value.open) return t('composer.hint');
  if (picker.value.items.length) return t('mention.hintPick');
  // 空态没有可选项：Enter 仍是发送（键盘契约不变），只说明怎么关掉它。
  return t('mention.hintDismiss');
});

function autosize() {
  const node = input.value;
  if (!node) return;
  node.style.height = 'auto';
  node.style.height = Math.min(node.scrollHeight || 0, 160) + 'px';
}

function closePicker() {
  picker.value = { open: false, empty: null, items: [], activeIndex: 0, trigger: null, signature: '' };
}

/** 按光标处重判触发点：弹清单、换候选、或让它消失。 */
function updatePicker() {
  const node = input.value;
  if (!node || props.disabled) {
    closePicker();
    return;
  }
  const trigger = mentionTriggerAt(node);
  const signature = mentionTriggerSignature(node);
  // 触发点变了就撤掉上一次的「已关闭」记录：关掉的只是那一个触发点，不是整个能力。
  if (dismissed.value && dismissed.value !== signature) dismissed.value = '';
  if (!trigger || signature === dismissed.value) {
    closePicker();
    return;
  }
  if (props.candidates === null) {
    // 候选来源还没读到：先去读一次（父组件负责），回来再判。
    closePicker();
    emit('mention-request');
    return;
  }
  const needle = trigger.query.toLowerCase();
  const items = props.candidates.filter((member) =>
    String(member.display_name || '').toLowerCase().includes(needle),
  );
  if (props.candidates.length && !items.length) {
    // 过滤无匹配：清单消失，且不打断正常输入。
    closePicker();
    return;
  }
  const sameTrigger = picker.value.open && picker.value.signature === signature;
  picker.value = {
    open: true,
    // 同一个触发点上重判（例如光标动了）不重置高亮，否则方向键一按高亮就弹回第一项。
    activeIndex: sameTrigger
      ? Math.min(picker.value.activeIndex, Math.max(items.length - 1, 0))
      : 0,
    items,
    trigger,
    signature,
    empty: props.candidates.length
      ? null
      : props.candidatesUnavailable
        ? 'unavailable'
        : 'no-members',
  };
}

function onInput() {
  autosize();
  updatePicker();
}

function onKeydown(event) {
  if (picker.value.open && picker.value.items.length) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const total = picker.value.items.length;
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      picker.value.activeIndex = Math.max(0, Math.min(total - 1, picker.value.activeIndex + delta));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      choose(picker.value.items[picker.value.activeIndex]);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      // Esc：只关清单、不改正文，并把这个触发点记为已关闭（不然下一次 input 立刻又弹出来）。
      dismissed.value = mentionTriggerSignature(input.value);
      closePicker();
      return;
    }
  }
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
}

function choose(member) {
  if (!member) return;
  const name = String(member.display_name || '');
  if (!mentionInsertable(name)) {
    // 不静默、也不插入无效文本：这个名字没有能被解析器识别的插入形态。
    emit('mention-unsupported', name);
    return;
  }
  const node = input.value;
  const result = insertMention(node.value, node.selectionStart, picker.value.trigger, name);
  if (!result) return;
  body.value = result.value;
  nextTick(() => {
    node.setSelectionRange(result.caret, result.caret);
    node.focus();
    autosize();
    closePicker();
    dismissed.value = mentionTriggerSignature(node);
  });
}

/** 工具行「@提及」：向输入框**追加** `@`、聚焦，随后弹出的仍是既有候选（不新写第二套）。 */
function mentionFromToolbar() {
  const node = input.value;
  if (!node) return;
  body.value += '@';
  nextTick(() => {
    const caret = node.value.length;
    node.focus();
    node.setSelectionRange(caret, caret);
    autosize();
    updatePicker();
  });
}

function submit() {
  const text = body.value.trim();
  if (!text || !canSend.value) return;
  emit('send', text);
  body.value = '';
  closePicker();
  nextTick(() => {
    autosize();
    if (input.value) input.value.focus();
  });
}

defineExpose({ focus: () => input.value && input.value.focus(), clear: () => { body.value = ''; } });
</script>

<template>
  <footer id="composer" class="composer" :class="{ 'is-disabled': !online || disabled }" data-od-id="composer">
    <div class="composer-inner">
      <div class="composer-box">
        <MentionPicker
          v-if="picker.open"
          :items="picker.items"
          :active-index="picker.activeIndex"
          :empty="picker.empty"
          @choose="choose"
          @hover="picker.activeIndex = $event"
        />
        <textarea
          ref="input"
          v-model="body"
          class="composer-input"
          rows="1"
          :placeholder="placeholder"
          :aria-label="t('composer.inputLabel')"
          :disabled="disabled"
          data-od-id="composer-input"
          @input="onInput"
          @click="updatePicker"
          @keyup="updatePicker"
          @keydown="onKeydown"
          @blur="closePicker"
        ></textarea>
        <div class="composer-bar">
          <button
            class="icon-btn"
            type="button"
            :title="t('composer.mentionAction')"
            :aria-label="t('composer.mentionAction')"
            :disabled="disabled"
            data-od-id="btn-mention"
            @click="mentionFromToolbar"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="4" /><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8" />
            </svg>
          </button>
          <span class="sep"></span>
          <span class="composer-hint" data-od-id="composer-hint">{{ hint }}</span>
          <button
            class="composer-send"
            type="button"
            :disabled="!canSend"
            :aria-label="t('composer.send')"
            data-od-id="btn-send"
            @click="submit"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" />
              <path d="m21.854 2.147-10.94 10.939" />
            </svg>
          </button>
        </div>
      </div>
      <div class="composer-note">{{ t('composer.note') }}</div>
    </div>
  </footer>
</template>
