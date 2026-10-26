<!-- 一块附件（ADR-0014）

     两种形态共用同一块 `.attach` 形状，差别只有一个 `is-link` 档：
     - **已绑定**（`message_id` 非空）⇒ 原生 `<a download>`，内容端点给出 `Content-Disposition`
       与 MIME，浏览器据此落到本地文件；
     - **未绑定**（草稿）⇒ `<span>`。内容端点对草稿回 404，渲染成一个点下去必然 404 的链接
       是骗人的（与旧实现同一条判据）。

     体积符号（`B` / `KB` / `MB`）在 `format.js` 里拼，不进词条表：它是与语言无关的单位。 -->
<script setup>
import { computed } from 'vue';
import { t } from '../i18n/index.js';
import { formatBytes } from '../lib/format.js';

const props = defineProps({
  attachment: { type: Object, required: true },
});

const name = computed(() => props.attachment.name || t('attach.fallbackName'));
const size = computed(() => formatBytes(props.attachment.size));
/** 只有**已绑定**的附件才是可点的链接：草稿取内容端点必 404。 */
const bound = computed(() => Boolean(props.attachment.message_id));
</script>

<template>
  <a
    v-if="bound"
    class="attach is-link"
    :data-attachment="attachment.id"
    :href="attachment.url"
    :download="name"
    :aria-label="t('msg.attachmentAria', { name, size })"
  >
    <span class="attach-name">{{ name }}</span>
    <span class="attach-size">{{ size }}</span>
  </a>
  <span v-else class="attach" :data-attachment="attachment.id">
    <span class="attach-name">{{ name }}</span>
    <span class="attach-size">{{ size }}</span>
  </span>
</template>
