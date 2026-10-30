<!-- 频道工具（#344）：一枚入口 + 宽弹窗内的三个页签

     频道头右上角原先并排五个图标（成员名册 / 频道设置 / 回应预算 / 归档 / 运行面板），
     入口过多且彼此没有分组。本组件把前三张面收进**一个**宽弹窗，用顶部页签承载：

     - 成员：`ChannelRosterPanel`（原 `RosterModal`；按 `CONTEXT.md` 的**成员名册**命名）
     - 设置：`ChannelSettingsPanel`（原 `ChannelConfigModal`；归档 / 删除都在它的危险区）
     - 预算：`ChannelBudgetPanel`（原 `ChannelBudgetModal`）

     运行面板**不并进来**：它是 dock 的过程视图，与任务线程共用同一形态，仍走频道头自己的入口。

     三条纪律：

     1. **唯一外壳**：`ModalShell` 的 `wide` 档是这一层唯一的弹窗骨架；三个面板不再各自包壳、
        不再各自给「完成」页脚——关掉这一层的动作只有顶部关闭键与页脚那枚按钮。
     2. **一次挂载，页签只切显示**：三个面板在打开时一起挂载并各自读一次后端；切页签只换
        `v-show`，不重新读、不销毁子组件，因此设置页签的未保存草稿与预算页签的档位选择都不会丢。
        关闭时三个面板按各自原有的 `open` 语义复位（关掉再打开就重新读）。
     3. **每次打开都落在「成员」页签**：页签的初始选择是这一层的呈现状态，不跨打开会话记忆。

     归档在「设置」页签的危险区里**当场执行**（可恢复，ADR-0040）：它只发意图给父组件
     （`App.vue` 持有 store 与 toast）；成功时父组件返回 `true`，这一层据此关闭自己。失败时
     弹窗不关，父组件的 toast 说的是后端给的可读原因。 -->
<script setup>
import { computed, ref, watch } from 'vue';
import ChannelBudgetPanel from './ChannelBudgetPanel.vue';
import ChannelRosterPanel from './ChannelRosterPanel.vue';
import ChannelSettingsPanel from './ChannelSettingsPanel.vue';
import ModalShell from './ModalShell.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  // 显隐由 `App.vue` 的 `channelToolsOpen` 驱动。
  open: { type: Boolean, default: false },
  // 打开那一刻的频道（`null` = 还没打开过）：三个面板都只读它。
  channel: { type: Object, default: null },
  store: { type: Object, required: true },
  // toast 落点由 `App.vue` 传（加入 / 保存 / 归档各一条）。
  toast: { type: Function, default: null },
  // 删除动作由父组件给：抛错 ⇒ 错误留在设置页签内，确认条不收起。
  remove: { type: Function, default: null },
  // 归档动作由父组件给：返回 `true` 表示成功（这一层据此关闭弹窗）。
  archive: { type: Function, default: null },
});

const emit = defineEmits(['close']);

const activeTab = ref('members');

watch(
  () => props.open,
  (open) => {
    // 每次打开都从「成员」开始；关闭时不必改，下一次打开的同一帧会把它拨回来。
    if (open) activeTab.value = 'members';
  },
  { immediate: true },
);

const channelName = computed(() => (props.channel ? props.channel.name : ''));
const title = computed(() => t('channelTools.title'));
const sub = computed(() => t('channelTools.sub', { name: channelName.value }));

function selectTab(tab) {
  activeTab.value = tab;
}

/** 归档：调父组件的动作；成功才关掉这一层（失败时弹窗不关，toast 说原因）。 */
async function archiveChannel() {
  if (!props.archive) return;
  const archived = await props.archive();
  if (archived) emit('close');
}
</script>

<template>
  <ModalShell
    id="channelToolsOverlay"
    class="channel-tools-overlay"
    :open="open"
    wide
    :title="title"
    :sub="sub"
    :close-label="t('channelTools.closeAria')"
    close-id="channelToolsClose"
    close-od-id="channel-tools-close"
    @close="emit('close')"
  >
    <div
      class="tabs"
      role="tablist"
      :aria-label="t('channelTools.tabsLabel')"
      data-od-id="channel-tools-tabs"
    >
      <button
        class="tab"
        :class="{ 'is-active': activeTab === 'members' }"
        type="button"
        role="tab"
        :aria-selected="activeTab === 'members' ? 'true' : 'false'"
        data-od-id="channel-tools-tab-members"
        @click="selectTab('members')"
      >{{ t('channelTools.tabMembers') }}</button>
      <button
        class="tab"
        :class="{ 'is-active': activeTab === 'settings' }"
        type="button"
        role="tab"
        :aria-selected="activeTab === 'settings' ? 'true' : 'false'"
        data-od-id="channel-tools-tab-settings"
        @click="selectTab('settings')"
      >{{ t('channelTools.tabSettings') }}</button>
      <button
        class="tab"
        :class="{ 'is-active': activeTab === 'budget' }"
        type="button"
        role="tab"
        :aria-selected="activeTab === 'budget' ? 'true' : 'false'"
        data-od-id="channel-tools-tab-budget"
        @click="selectTab('budget')"
      >{{ t('channelTools.tabBudget') }}</button>
    </div>

    <!-- 三张面一次挂载：`v-show` 只切显示，切页签不重读后端、不丢未保存草稿。 -->
    <div v-show="activeTab === 'members'">
      <ChannelRosterPanel :open="open" :channel="channel" :store="store" :toast="toast" />
    </div>
    <div v-show="activeTab === 'settings'">
      <ChannelSettingsPanel
        :open="open"
        :channel="channel"
        :store="store"
        :archive="archiveChannel"
        :remove="remove"
      />
    </div>
    <div v-show="activeTab === 'budget'">
      <ChannelBudgetPanel :open="open" :channel="channel" :store="store" :toast="toast" />
    </div>

    <template #foot>
      <span class="sep"></span>
      <button class="btn btn-primary" type="button" data-od-id="channel-tools-done" @click="emit('close')">
        {{ t('channelTools.done') }}
      </button>
    </template>
  </ModalShell>
</template>
