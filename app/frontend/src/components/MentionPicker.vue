<!-- @ 补全弹层

     候选 = **当前频道的 Agent 成员**（与成员面板「已加入」段同一来源），不列 owner、
     不列频道外成员、不标注「未加入」。过滤只用于把候选缩小（包含匹配、ASCII 大小写不敏感），
     插入的始终是**库里的原名**——「最左最长」这类解析语义不因前端放宽过滤而改变。

     键盘契约：↑↓ 移动高亮、Enter 选中、Esc 关清单。清单一出现，Enter 就不再是「发送」，
     因此提示行（`composerHint`）也要跟着换词条（由 Composer 统一裁决）。 -->
<script setup>
import MemberAvatar from './MemberAvatar.vue';
import { t } from '../i18n/index.js';
import { mentionInsertable } from '../lib/mentions.js';

const props = defineProps({
  items: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
  // `null` = 有候选；`'no-members'` = 频道没有 Agent 成员；`'unavailable'` = 成员清单读不到
  empty: { type: String, default: null },
});

const emit = defineEmits(['choose', 'hover']);
</script>

<template>
  <div
    id="composerMention"
    class="composer-mention"
    role="listbox"
    :aria-label="t('composer.mentionLabel')"
    data-od-id="mention-picker"
  >
    <div v-if="empty" class="member-pick-empty mention-empty" data-od-id="mention-picker-empty">
      {{ empty === 'unavailable' ? t('mention.unavailable') : t('mention.empty') }}
    </div>
    <button
      v-for="(member, index) in items"
      v-else
      :key="member.id"
      class="member-opt mention-opt"
      :class="{ 'is-active': index === activeIndex }"
      type="button"
      role="option"
      :aria-selected="index === activeIndex ? 'true' : 'false'"
      :data-mention="member.id"
      :data-od-id="'mention-option-' + member.id"
      @mousedown.prevent="emit('choose', member)"
      @mouseenter="emit('hover', index)"
    >
      <MemberAvatar :name="member.display_name" :slot="member.avatar_slot" size="sm" />
      <span class="mention-name">{{ member.display_name }}</span>
      <!-- 不静默：它在清单里，但明说这个名字无法被 @ 到（三个闭引号都在名字里时没有合法插入形态）。 -->
      <span v-if="!mentionInsertable(member.display_name)" class="mention-note">{{ t('mention.cannotMention') }}</span>
    </button>
  </div>
</template>
