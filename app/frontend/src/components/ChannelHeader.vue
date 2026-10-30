<!-- 频道头（标题 / 描述 / 页签 / 工具行）

     标题是**当前 target 的名字**：频道带 `#` 前缀，私信带 `@` 前缀（与旧 `renderHeader()`
     同一形态）。desc 与 placeholder 按 target 类型与归档态取值——**不照抄原型的过时文案**
     （原型那句「懒创建会话」与 `CONTEXT.md` 的 `_Avoid_` 冲突）。

     工具行的可见性是「这个 target 有没有这条面」的呈现，不是权限：
     频道工具 / 运行面板都只属于**频道**，私信下整组隐藏——不留一个按下去什么都不发生的按钮。

     **入口合并**（#344）：成员名册 / 频道设置 / 回应预算三张面收进**同一枚**
     `btn-channel-tools` 入口（宽弹窗 + 顶部页签）；归档入口从工具行移进该弹窗的「设置」
     页签危险区（ADR-0040 的语义不变：当场执行、可恢复）。频道头因此只剩两枚图标入口。

     **第二层可见性**：两枚入口背后的面不是同一批票，因此开关仍分成两个：
     - `channelToolsAvailable`：频道工具（三张面已迁 ⇒ 默认**开**）；
     - `runPanelAvailable`：运行面板（**已迁**，默认**开**；关掉它只为了用例能断言反面）。
     渲染一个点下去什么都不发生的入口，比不渲染更难解释；各自的票迁完那个面时把开关打开即可。

     **第三层可见性：归档态**（#342 / ADR-0040）。频道归档后，工具行只留「取消归档」一枚——
     频道工具 / 运行面板**整组不渲染**：那些面背后的写路径已被后端整体拒绝（400），
     留下来只会是「点进去发现什么都做不了」的入口。归档入口在活跃态由频道工具弹窗承载；
     归档态让位给「取消归档」（同一枚按钮的两个语义，`data-od-id` 因此分成两个）。 -->
<script setup>
import { computed } from 'vue';
import { t } from '../i18n/index.js';
import { isArchivedChannel, targetDescription } from '../lib/target-view.js';

const props = defineProps({
  target: { type: Object, default: null },
  isDm: { type: Boolean, default: false },
  // 「当前 target 是只读归档」（私信 peer 已删除 / 频道已归档）。本组件只把它透给页签与工具行的
  // 语义判断；**频道头自己的归档判据不读它**——它从 `target.archived_at` 推导（`isArchivedChannel`），
  // 与 composer 的 `archived` 是同一份后端事实的两处呈现。
  isArchivedDm: { type: Boolean, default: false },
  // 页签计数：「消息」页签读**未读**（后端未读口径），不是流里条数。
  messageCount: { type: Number, default: 0 },
  taskCount: { type: Number, default: 0 },
  activeTab: { type: String, default: 'messages' },
  // 名册 / 配置 / 预算三个入口背后的面是否已迁到本版（**已迁** ⇒ 默认开）。
  channelToolsAvailable: { type: Boolean, default: true },
  // 运行面板是否已迁到本版（**已迁** ⇒ 默认开）。
  runPanelAvailable: { type: Boolean, default: true },
  // 「任务」页签背后的看板是否已迁到本版。
  tasksAvailable: { type: Boolean, default: false },
  // **本频道**里有任务在等审批的任务数（票 #308 的 `task-approval-dot`；`0` ⇒ 圆点不出现）。
  // 它是 `stores/approvals.js` 那一份镜像的派生值，与审批卡同一份读数（不在组件里另算一遍）。
  taskApprovalCount: { type: Number, default: 0 },
});

const emit = defineEmits([
  'select-tab',
  'open-tools',
  'open-run-panel',
  'unarchive',
]);

const name = computed(() => {
  if (!props.target) return '';
  return props.isDm ? props.target.peer.display_name : props.target.name;
});

const description = computed(() => targetDescription(props.target));

/** 当前频道是否已归档（#342 / ADR-0040）：判据只消费后端事实，与 `lib/target-view.js` 同源。 */
const archivedChannel = computed(() => !props.isDm && isArchivedChannel(props.target));
</script>

<template>
  <header v-if="target" id="chanHead" class="chan-head" data-od-id="channel-header">
    <div class="chan-top">
      <h1 class="chan-title">
        <span v-if="!isDm" class="hash">#</span>
        <span id="chanTitle">{{ (isDm ? '@' : '') + name }}</span>
      </h1>
      <div class="chan-tools">
        <!-- 归档态（#342 / ADR-0040）：整组工具让位给唯一一枚「取消归档」——名册 / 配置 / 预算 /
             运行面板在归档频道的写路径上已被后端整体拒绝（400），留下来只会是「点进去什么都做不了」
             的入口（ADR-0038 §五）。 -->
        <button
          v-if="!isDm && archivedChannel"
          class="icon-btn"
          type="button"
          data-od-id="btn-channel-unarchive"
          :title="t('header.unarchiveChannel')"
          :aria-label="t('header.unarchiveChannel')"
          @click="emit('unarchive')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5" />
          </svg>
        </button>
        <template v-else-if="!isDm && channelToolsAvailable">
          <!-- 频道工具（#344）：成员 / 设置 / 预算三张面共用这一枚入口，弹窗内按页签分组；
               归档入口也随设置页签进了弹窗（ADR-0040 的语义不变）。 -->
          <button
            class="icon-btn"
            type="button"
            data-od-id="btn-channel-tools"
            :title="t('channelTools.entryTitle')"
            :aria-label="t('channelTools.entryTitle')"
            @click="emit('open-tools')"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10 5H3" /><path d="M12 19H3" /><path d="M14 3v4" /><path d="M16 17v4" />
              <path d="M21 12h-9" /><path d="M21 19h-5" /><path d="M21 5h-7" /><path d="M8 10v4" /><path d="M8 12H3" />
            </svg>
          </button>
        </template>
        <!-- 运行面板是**已迁面**：它有自己的开关（默认开），不跟上面三个同涨同落。
             私信下它仍然不存在：私信没有 team 运行，留一个点开只会看到空态。
             归档频道下它同样不渲染：频道已只读，运行面板的入口在那条路径上没有意义。 -->
        <button
          v-if="!isDm && !archivedChannel && runPanelAvailable"
          class="icon-btn"
          type="button"
          data-od-id="btn-run-panel"
          :title="t('runPanel.entryTitle')"
          :aria-label="t('runPanel.entryTitle')"
          @click="emit('open-run-panel')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />
          </svg>
        </button>
      </div>
    </div>
    <div class="chan-desc" id="chanDesc">{{ description }}</div>
    <div class="tabs" id="channelTabs" role="tablist" :aria-label="t('tabs.viewLabel')" data-od-id="channel-tabs">
      <button
        id="tabMessages"
        class="tab"
        :class="{ 'is-active': activeTab === 'messages' }"
        type="button"
        role="tab"
        :aria-selected="activeTab === 'messages' ? 'true' : 'false'"
        data-tab="messages"
        data-od-id="tab-messages"
        @click="emit('select-tab', 'messages')"
      >
        <span>{{ t('tabs.messages') }}</span> <span class="count" id="msgCount">{{ messageCount }}</span>
      </button>
      <!-- 私信里没有任务板这个概念（任务恰好属于一个频道）：整个页签不出现，而不是点开看空态。 -->
      <button
        v-if="!isDm && tasksAvailable"
        id="tabTasks"
        class="tab"
        :class="{ 'is-active': activeTab === 'tasks' }"
        type="button"
        role="tab"
        :aria-selected="activeTab === 'tasks' ? 'true' : 'false'"
        data-tab="tasks"
        data-od-id="tab-tasks"
        @click="emit('select-tab', 'tasks')"
      >
        <span>{{ t('tabs.tasks') }}</span> <span class="count" id="taskCount">{{ taskCount }}</span>
        <!-- 待审批外显徽标（票 #308，旧 `#taskApprovalDot`）：有未处理审批时出现、处理完消失。
             `aria-label` 的 N 是**任务数**（不是记录数）——一个任务等三条审批也只算一个任务。 -->
        <span
          v-if="!isDm && tasksAvailable && taskApprovalCount > 0"
          id="taskApprovalDot"
          class="tab-approval-dot"
          data-od-id="task-approval-dot"
          :aria-label="t('tabs.tasksApprovalDot', { count: taskApprovalCount })"
        ></span>
      </button>
    </div>
  </header>
</template>
