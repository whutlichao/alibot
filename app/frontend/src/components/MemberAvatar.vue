<!-- 头像（方形色位块，7px 圆角，不是圆形）

     `avatar_slot` → `--av-0…--av-4` 五个确定性色位；它只用于识别同一成员，**不承载**在线状态
     或主题语义（`CONTEXT.md` **头像色位**）。色位算法与旧实现共用 `format.js`，两版页面里
     同一个成员必须拿到同一个色位。 -->
<script setup>
import { computed } from 'vue';
import { avatarClassName, initial } from '../lib/format.js';

const props = defineProps({
  name: { type: String, default: '' },
  slot: { type: Number, default: 0 },
  // `sm` = 小号（旁白行、左栏私信行、成员面板）。
  size: { type: String, default: '' },
});

const className = computed(() => avatarClassName(props.size, props.slot));
const letter = computed(() => initial(props.name));
</script>

<template>
  <div :class="className" aria-hidden="true">{{ letter }}</div>
</template>
