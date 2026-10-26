<!-- 共享弹窗壳（`.overlay` / `.modal` / `.modal-head` / `.modal-body` / `.modal-foot`）

     骨架与关闭语义**只有这一份**，内容走插槽进来。类名逐字沿用旧页面
     （`studio/index.html` 的十个 `#*Overlay` 段）：`app.css` 的 `.overlay` / `.modal` /
     `.modal-head` / `.modal-body` / `.modal-foot` / `.modal.wide` 一个字都不用改——
     这就是「视觉契约只有一份」在这张票上的落点。

     三条语义照旧页面（`studio/app.js`）：
     1. **不用 `v-if` 撕毁内容**：`open` 驱动根节点的 `hidden` 属性（旧页面的
        `$('#xOverlay').hidden = false/true` 同一条）。人填了一半的表单在关掉再打开之间不该被重建。
     2. **ESC 只关最上面那一层**：层序由模块级栈 `lib/modal-stack.js` 裁决（旧页面
        `app.js:10578-10620` 是无栈、一次全关；这里是有意改进）。本组件只做接线——
        不要在组件里另挂 keydown，多实例各自监听就表达不了「只关最上一层」。
     3. **`busy` 时整层不收**：ESC、遮罩点击、关闭键都不动作，与旧页面对删除确认弹窗的
        `state.agentDelete.busy` 判据等价（`app.js:10075-10086`）——在途请求里被关掉，
        人就看不到它失败的原因了。

     焦点：打开时移到弹窗内**第一个可聚焦元素**（旧页面各弹窗自己挑落点，这里统一成第一条），
     关闭时还给**打开它的那个元素**（不是 `document.body`，否则键盘用户每次关弹窗都被扔回页首）。 -->
<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import { popModal, pushModal } from '../lib/modal-stack.js';
import { t } from '../i18n/index.js';

const props = defineProps({
  // 显隐：驱动根 `.overlay` 的 `hidden` 属性，**不卸载**内容。
  open: { type: Boolean, default: false },
  // 在途请求（删除确认这类）：为真时 ESC / 遮罩点击 / 关闭键都不关。
  busy: { type: Boolean, default: false },
  // 宽弹窗（`.modal.wide`：成员名册 / 提醒那一档）。
  wide: { type: Boolean, default: false },
  // 标题与副标题：调用方传**已译文案**（有些标题含后端读数，例如频道名与成员条数）。
  title: { type: String, default: '' },
  sub: { type: String, default: '' },
  // 关闭键的无障碍名：缺省走词条 `common.close`，特殊弹窗（名册）可覆盖。
  closeLabel: { type: String, default: '' },
  // `role="dialog"` 的 `aria-labelledby` 指向的 id。默认标题走内部生成的 id；
  // 用 `head` 插槽自绘标题时由调用方传它自己的 id（不传则整个属性缺席，不留悬空引用）。
  labelledBy: { type: String, default: '' },
});

const emit = defineEmits(['update:open', 'close']);

const root = ref(null);
const titleId = useId();
const labelledById = computed(() => props.labelledBy || (props.title ? titleId : ''));
const closeLabelText = computed(() => props.closeLabel || t('common.close'));

/** 打开它的那个元素：关闭时把焦点还回去（不是 `document.body`）。 */
let opener = null;

/** 这一层在栈里的身份：关闭动作与「在途请求」判据。 */
const entry = { close: requestClose, isBusy: () => props.busy };

function requestClose() {
  if (props.busy) return;
  emit('update:open', false);
  emit('close');
}

const FOCUSABLE = 'input, textarea, select, button, [href], [tabindex]:not([tabindex="-1"])';

function firstFocusableIn(container) {
  if (!container) return null;
  const nodes = Array.from(container.querySelectorAll(FOCUSABLE));
  return (
    nodes.find((node) => {
      if (node.disabled) return false;
      const buried = node.closest ? node.closest('[hidden]') : null;
      return !buried;
    }) || null
  );
}

/**
 * 弹窗内第一个可接焦点的控件（跳过禁用件与 `hidden` 子树）。
 *
 * **先找 `.modal-body`，找不到才退回整个弹窗**：DOM 顺序上头一个可聚焦元素永远是标题行那个
 * 「关闭」图标，把焦点落在它上面等于每次打开弹窗先选中「关掉它」——旧页面恰恰不是这样
 * （新建任务 / 新建频道 / 新建 Agent 落在第一个输入框上，只读的名册落在关闭键上）。
 * 于是判据写成「正文里第一个，正文没有可聚焦件时就是弹窗里第一个」。
 */
function firstFocusable() {
  if (!root.value) return null;
  const body = root.value.querySelector('.modal-body');
  return firstFocusableIn(body) || firstFocusableIn(root.value);
}

function focusFirst() {
  const node = firstFocusable();
  if (node) node.focus();
}

function restoreFocus() {
  const node = opener;
  opener = null;
  if (node && node.isConnected && typeof node.focus === 'function') node.focus();
}

watch(
  () => props.open,
  (open, wasOpen) => {
    if (open === wasOpen) return;
    if (open) {
      // 记下打开者：此刻 `document.activeElement` 就是按下去的那个控件。
      const active = document.activeElement;
      opener = active && active !== document.body ? active : null;
      pushModal(entry);
      nextTick(focusFirst);
    } else {
      popModal(entry);
      nextTick(restoreFocus);
    }
  },
);

onMounted(() => {
  // 首帧就开着（调用方直接 `open` 挂上来）：补一次与 watcher 相同的事。
  if (!props.open) return;
  const active = document.activeElement;
  opener = active && active !== document.body ? active : null;
  pushModal(entry);
  nextTick(focusFirst);
});

// 开着就卸载：栈里那一层必须跟着走，否则后面的 ESC 会被一个不存在的弹窗吃掉。
onBeforeUnmount(() => popModal(entry));
</script>

<template>
  <div ref="root" class="overlay" :hidden="!open" @click.self="requestClose">
    <div class="modal" :class="{ wide }" role="dialog" aria-modal="true" :aria-labelledby="labelledById || undefined">
      <div class="modal-head">
        <slot name="head">
          <div style="flex: 1">
            <h2 :id="titleId">{{ title }}</h2>
            <div v-if="sub" class="sub">{{ sub }}</div>
          </div>
        </slot>
        <button class="icon-btn" type="button" :aria-label="closeLabelText" @click="requestClose">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 6 6 18" /><path d="m6 6 12 12" />
          </svg>
        </button>
      </div>
      <div class="modal-body">
        <slot />
      </div>
      <div v-if="$slots.foot" class="modal-foot">
        <slot name="foot" />
      </div>
    </div>
  </div>
</template>
