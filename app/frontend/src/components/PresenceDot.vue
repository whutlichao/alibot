<!-- 状态点（四态：online / working / error / offline）

     四态只表达**后端事实**（空闲 / 正在回复 / 出错 / 未启用），不从会话存活推导。
     `title` / `aria-label` 按词条取值（`statusLabel`），切语言时跟着走——旧实现里那条
     「只看 dataset.status 会把无障碍名留在上一门语言」的坑，在 Vue 里由响应式依赖自动消失。 -->
<script setup>
import { computed } from 'vue';
import { statusLabel } from '../lib/format.js';

const props = defineProps({
  status: { type: String, default: 'offline' },
});

const label = computed(() => statusLabel(props.status));
</script>

<template>
  <span :class="'presence ' + status" :data-status="status" :title="label" :aria-label="label"></span>
</template>
