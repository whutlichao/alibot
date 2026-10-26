<!-- 右侧详情面板（dock）：**三形态**容器

     旧页面的 `#dock` 里住着三种形态（`#dockAgent` 频道成员 / `#dockTask` 任务线程 / `#dockRun`
     运行面板），共用一个 380px 容器，于是「同时显示两件东西」在构造上不可能发生。

     **本版只有第三种形态**（运行面板）。成员面板与任务线程还没迁过来，所以它们的子树根本不存在
     ——不是渲染出来再 `hidden` 掉：一个占着位置、看不见也可能被 Tab 命中的空容器，比不渲染更难解释。
     那两张票各自迁完时，往这里加一棵子树并把 `mode` 判据扩一档即可（形态仍是「由现在打开着
     哪一个对象推导」，不另存一份状态）。

     ≤900px 下它是右抽屉（`app.css` 的 `.dock.is-open` / `.dock[hidden]` 两条规则）：`hidden` 的语义
     被改写成「离屏 + 不可见」，`.is-open` 决定滑入。`.dock-close` 是这个抽屉**自己的**出口
     ——那时频道头里的开关正好被抽屉挡住。>900px 它是常驻列，`.dock-close` 由 CSS 藏起来。 -->
<script setup>
import { computed } from 'vue';
import RunPanel from './RunPanel.vue';
import { t } from '../i18n/index.js';

const props = defineProps({
  // 运行面板 store（`createRunPanelStore()` 的返回值）。
  panel: { type: Object, required: true },
  // 成员名的三个来源池（工作区成员 / Agent / 频道成员）——**必须往下传**：
  // 不传的话运行面板会退回把 `member_id` 当名字显示。真浏览器实测抓到过这一次，
  // 单测直接挂 RunPanel（自己传 pools）看不到它，因此 `components.test.js` 里另有一条
  // 「通过 DockPanel 挂上来也拿到成员名」的用例。
  pools: { type: Array, default: () => [] },
  // 抽屉形态（≤900px）：由 App.vue 决定开合，与 `hidden` 同进同出。
  open: { type: Boolean, default: false },
});

const emit = defineEmits(['close']);

/** 当前形态：`run` 是唯一已迁的形态，`null` = 收起整列（没有可显示的东西）。 */
const mode = computed(() => (props.panel.state.open ? 'run' : null));

// 会员面板 / 任务线程还没迁：`dockAvailable()` 的判据在本版只剩运行面板这一支。
// 频道成员面板缺席意味着「打开频道就把右栏撑开」不再是默认行为——那是旧页面的形态，
// 在这里它是「人显式打开了运行面板」的结果。
</script>

<template>
  <aside
    v-if="mode"
    id="dock"
    class="dock"
    :class="{ 'is-open': open }"
    data-od-id="detail-dock"
    :aria-label="t('dock.label')"
  >
    <RunPanel
      v-if="mode === 'run'"
      :panel="panel"
      :pools="pools"
      @close="emit('close')"
    />
  </aside>
</template>
